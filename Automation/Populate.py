#!/usr/bin/env python3
"""Automation/Populate.py — generates full grids one at a time.

At the user's explicit request: "generate 1000 grids one at a time so as
not to overload the queues." The script NEVER launches two generations in
parallel: it submits a request to `POST /api/generate`, polls
`GET /api/generate/phase/{job_id}` (the condensed route added for this)
until the job is `finished`, then moves on to the next one. The
backend's `GRID_QUEUE`/`CLUES_QUEUE` queues (see backend/app.py)
therefore never see more than one job at a time coming from here — other
clients (the web UI) can keep using them normally at the same time.

Every finished grid is automatically saved to the library by the backend
(`save_grid_json`, see `_run_generate_job`) — that's the whole point of
"populate."

By default the parameters (language, difficulty, size) are drawn at
random for every grid to populate the library with variety; any of them
can be pinned via the command line (see --help).

Each grid also gets a random theme by default: before submitting the
generation request, this script calls `GET /api/theme/random?lang=<language>`
(backend/app.py) — a random dictionary word (5 to 10 letters) is picked
from that language's own wordlist and handed to the LLM as an "indicative
word" note, which asks it to invent a short, original crossword theme
around it (see backend/clues.py's `LLMClueGenerator.generate_random_theme`
for why the word is there — it's a randomness seed, not a requirement
that the theme literally use it). The resulting theme phrase is sent as
`GenerateRequest.theme`, so the grid gets a real thematic glossary
(Qdrant pre-search) exactly as if a user had typed it into the web UI's
"Thématique" field. `--no-theme` disables this and generates ordinary,
un-themed grids instead; a failure of the theme call itself (LLM/network
unreachable) never blocks a grid — it just falls back to no theme for
that one.

Usage:
    .venv/bin/python Automation/Populate.py            # 1000 grids, random params
    .venv/bin/python Automation/Populate.py --count 50 --language fr --difficulty easy
    .venv/bin/python Automation/Populate.py --mode turbo --width 15 --height 10

Ctrl-C: stops cleanly after the current grid (prints a summary). A second
Ctrl-C (or SIGTERM) exits immediately — but never before cancelling the
job this script still has on the server (`POST /api/generate/cancel/
{job_id}`), so no orphaned job is left in the backend's queues to compute
on its own once this script has gone. The same cancellation applies to a
grid abandoned on `--per-grid-timeout`.
"""
import argparse
import json
import os
import random
import signal
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

LANGUAGES = ["fr", "en", "de", "es", "it", "pt"]
DIFFICULTIES = ["easy", "medium", "hard"]
MODES = ["flash", "turbo", "fast", "medium", "ultra"]

# GET /api/theme/random makes an LLM round-trip (see backend/app.py's
# `random_theme` / backend/clues.py's `generate_random_theme`) — generous
# like every other LLM call in this project, but bounded so a stuck LLM
# server can't hang the whole populate run indefinitely: a timeout here
# just means this one grid falls back to no theme.
THEME_FETCH_TIMEOUT_S = 90.0

# Bounds for a grid's random size (width AND height), at the user's
# explicit request: "sizes between 8 and 20 (horizontal and vertical)".
# `--width`/`--height` can always still pin a value outside this range if
# needed (only bounded by the backend, 5 to 30).
MIN_SIZE = 8
MAX_SIZE = 20

DEFAULT_BASE_URL = os.environ.get(
    "CROSSWORDFALCON_BACKEND_URL", "http://127.0.0.1:3001"
)

_stop_requested = False

# The job this script has submitted and not yet seen finish, with the
# backend it lives on — cancelled before this script ever gives up on it
# (`_cancel_current_job`).
_current_job_id = None
_current_base_url = None

# Bounded, so an unreachable backend cannot hold up an exit.
CANCEL_TIMEOUT_S = 10.0


def _cancel_current_job():
    """Cancels the job still owned by this script, if any (best-effort)."""
    global _current_job_id
    job_id, _current_job_id = _current_job_id, None
    if not job_id or not _current_base_url:
        return
    try:
        _http_json(
            f"{_current_base_url}/api/generate/cancel/{job_id}",
            payload={}, timeout=CANCEL_TIMEOUT_S,
        )
        print(f"    job {job_id[:8]} annulé sur le serveur.", flush=True)
    except (urllib.error.URLError, TimeoutError, ConnectionError, OSError) as e:
        print(f"    !! annulation du job {job_id[:8]} impossible : {e}", flush=True)


def _handle_sigterm(signum, frame):
    # SystemExit unwinds through main()'s own `finally`, which cancels the
    # current job before the process exits.
    print("\nArrêt immédiat (SIGTERM).", flush=True)
    sys.exit(143)


def _handle_sigint(signum, frame):
    global _stop_requested
    if _stop_requested:
        print("\nArrêt immédiat.", flush=True)
        sys.exit(130)
    _stop_requested = True
    print(
        "\nArrêt demandé — on termine la grille en cours puis on s'arrête "
        "(Ctrl-C à nouveau pour forcer).",
        flush=True,
    )


def _http_json(url, payload=None, timeout=60):
    """GET if payload is None, otherwise a JSON POST. Returns the
    JSON-decoded body. Raises urllib.error.HTTPError / URLError as usual."""
    if payload is None:
        req = urllib.request.Request(url, method="GET")
    else:
        req = urllib.request.Request(
            url,
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        body = resp.read().decode("utf-8")
    return json.loads(body) if body else {}


def _fetch_random_theme(base_url, language, timeout=THEME_FETCH_TIMEOUT_S):
    """Asks the back end (GET /api/theme/random) for a random theme in
    `language` — see this module's own docstring for the full mechanism.
    Returns "" (never raises) on any network/HTTP failure, an unreachable
    LLM, or an empty result: the caller then just submits an ordinary,
    un-themed generation instead of losing the whole grid over it."""
    url = f"{base_url}/api/theme/random?lang={urllib.parse.quote(language)}"
    try:
        resp = _http_json(url, timeout=timeout)
    except (urllib.error.URLError, TimeoutError, ConnectionError) as e:
        print(f"    thème aléatoire indisponible ({e}), grille sans thème.", flush=True)
        return ""
    return (resp.get("theme") or "").strip()


def _pick(fixed, pool):
    """`fixed` if not None, otherwise a random draw from `pool`."""
    return fixed if fixed is not None else random.choice(pool)


def _build_request(args):
    width = args.width if args.width is not None else random.randint(MIN_SIZE, MAX_SIZE)
    height = args.height if args.height is not None else random.randint(MIN_SIZE, MAX_SIZE)
    return {
        "language": _pick(args.language, LANGUAGES),
        # `bilingual_language` (GenerateRequest, backend/app.py) is
        # deliberately omitted here, at the user's explicit request
        # ("Populate.py should not generate bilingual grids for now") —
        # its absence already degrades cleanly into an ordinary
        # monolingual generation (see crossword_gen.generate_grid's own
        # `bilingual_wordlist_path`), so there's nothing more to do here
        # to get that behavior.
        "difficulty": _pick(args.difficulty, DIFFICULTIES),
        # Always "medium" mode, at the user's explicit request ("Populate
        # should only use MEDIUM mode") — "ultra" mode can spend hours on
        # a single grid search alone, which makes populating 1000 grids
        # unmanageable. `--mode` can always force a different value.
        "mode": args.mode or "medium",
        "width": width,
        "height": height,
        # seed deliberately omitted -> the backend draws one; we want
        # different grids every time.
        # Marks this request as coming from Populate, at the user's
        # explicit request: "when a request comes from Populate, generate
        # definitions without parallelizing several requests at once, so
        # as not to overload the GPU for real users." Read by
        # backend/app.py's GenerateRequest.source: forces
        # LLMClueGenerator.generate(batch_parallelism=1) for this job
        # (only one word being generated at a time), without affecting
        # any other request.
        "source": "populate",
    }


def _generate_one(base_url, req, poll_interval, per_grid_timeout):
    """Submits a generation and polls its phase until it finishes. Returns
    (job_id, final_phase, error_code|None, elapsed_seconds)."""
    started = time.monotonic()
    try:
        resp = _http_json(f"{base_url}/api/generate", payload=req, timeout=60)
    except urllib.error.HTTPError as e:
        if e.code == 400:
            # Request rejected outright (e.g. a language just added whose
            # dictionary isn't built yet) — not a real failure: this
            # combination is dropped and another one is drawn instead,
            # without counting it as a failure or spending a retry.
            return None, "rejected", None, time.monotonic() - started
        raise
    global _current_job_id, _current_base_url
    job_id = resp["job_id"]
    _current_job_id, _current_base_url = job_id, base_url
    # Machine-readable line: run_Populate.sh reads the last one back to
    # cancel this job itself if the process ever has to be SIGKILLed.
    print(f"    job_submitted {job_id} {base_url}", flush=True)
    last_phase = None
    while True:
        time.sleep(poll_interval)
        if per_grid_timeout and (time.monotonic() - started) > per_grid_timeout:
            _cancel_current_job()
            return job_id, "timeout", None, time.monotonic() - started
        try:
            phase_info = _http_json(
                f"{base_url}/api/generate/phase/{job_id}", timeout=30
            )
        except urllib.error.HTTPError as e:
            if e.code == 404:
                # Job evicted from JOBS (MAX_JOBS) before we saw it
                # finish — rare here (only one job at a time), treated as
                # unknown.
                _current_job_id = None
                return job_id, "gone", None, time.monotonic() - started
            raise
        phase = phase_info.get("phase")
        if phase != last_phase:
            extra = ""
            if phase in ("grid_queue", "clues_queue"):
                extra = (
                    f" (file : {phase_info.get('queue_position')}"
                    f"/{phase_info.get('queue_length')})"
                )
            elif phase == "clues_generation" and phase_info.get("clues_total"):
                extra = (
                    f" ({phase_info.get('clues_done')}"
                    f"/{phase_info.get('clues_total')} définitions)"
                )
            print(f"    -> {phase}{extra}", flush=True)
            last_phase = phase
        if phase_info.get("finished"):
            _current_job_id = None
            return (
                job_id,
                phase,
                phase_info.get("error_code"),
                time.monotonic() - started,
            )


def main():
    parser = argparse.ArgumentParser(
        description="Génère des grilles complètes une par une (peuplement de la bibliothèque).",
    )
    parser.add_argument("--count", type=int, default=1000, help="Nombre de grilles à générer (défaut 1000).")
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL, help=f"URL du back (défaut {DEFAULT_BASE_URL}).")
    parser.add_argument("--language", choices=LANGUAGES, default=None, help="Fige la langue (sinon aléatoire).")
    parser.add_argument("--difficulty", choices=DIFFICULTIES, default=None, help="Fige la difficulté (sinon aléatoire).")
    parser.add_argument("--mode", choices=MODES, default=None, help="Fige le mode/budget (défaut : medium).")
    parser.add_argument("--width", type=int, default=None, help=f"Fige la largeur (sinon aléatoire {MIN_SIZE}-{MAX_SIZE}).")
    parser.add_argument("--height", type=int, default=None, help=f"Fige la hauteur (sinon aléatoire {MIN_SIZE}-{MAX_SIZE}).")
    parser.add_argument("--poll", type=float, default=5.0, help="Intervalle de sondage de la phase, en secondes (défaut 5).")
    parser.add_argument("--between", type=float, default=2.0, help="Pause entre deux grilles, en secondes (défaut 2).")
    parser.add_argument("--per-grid-timeout", type=float, default=0.0, help="Abandonne une grille après N secondes (0 = jamais).")
    parser.add_argument("--retries", type=int, default=1, help="Nouvelles tentatives si une grille finit en error (défaut 1).")
    parser.add_argument("--random-seed", type=int, default=None, help="Graine du tirage des paramètres (reproductibilité).")
    parser.add_argument(
        "--no-theme", dest="theme", action="store_false",
        help="Désactive le thème aléatoire (par défaut, chaque grille reçoit un thème inventé par le LLM).",
    )
    args = parser.parse_args()

    if args.random_seed is not None:
        random.seed(args.random_seed)

    base_url = args.base_url.rstrip("/")

    try:
        health = _http_json(f"{base_url}/api/health", timeout=10)
    except (urllib.error.URLError, TimeoutError, ConnectionError) as e:
        print(f"Back injoignable sur {base_url} ({e}). Lancez ./run_Falcon.sh d'abord.", file=sys.stderr)
        return 1
    if health.get("status") != "ok":
        print(f"Réponse /api/health inattendue : {health!r}", file=sys.stderr)
        return 1

    signal.signal(signal.SIGINT, _handle_sigint)
    signal.signal(signal.SIGTERM, _handle_sigterm)
    try:
        return _populate(args, base_url)
    finally:
        _cancel_current_job()


def _populate(args, base_url):

    print(
        f"Peuplement : {args.count} grilles, une par une, via {base_url}\n"
        f"Paramètres : "
        f"langue={args.language or 'aléatoire'}, "
        f"difficulté={args.difficulty or 'aléatoire'}, "
        f"mode={args.mode or 'medium'}, "
        f"taille={args.width or f'aléatoire {MIN_SIZE}-{MAX_SIZE}'}"
        f"x{args.height or f'aléatoire {MIN_SIZE}-{MAX_SIZE}'}, "
        f"thème={'aléatoire (LLM)' if args.theme else 'aucun'}\n",
        flush=True,
    )

    done = errors = timeouts = 0
    t0 = time.monotonic()

    for i in range(1, args.count + 1):
        if _stop_requested:
            break
        attempt = 0
        while True:
            attempt += 1
            req = _build_request(args)
            if args.theme:
                theme = _fetch_random_theme(base_url, req["language"])
                if theme:
                    req["theme"] = theme
            label = (
                f"[{i}/{args.count}] {req['language']}/{req['difficulty']}/"
                f"{req['mode']} {req['width']}x{req['height']}"
                + (f" thème=\"{req['theme']}\"" if req.get("theme") else "")
                + (f" (tentative {attempt})" if attempt > 1 else "")
            )
            print(label, flush=True)
            try:
                job_id, phase, error_code, elapsed = _generate_one(
                    base_url, req, args.poll, args.per_grid_timeout
                )
            except (urllib.error.URLError, TimeoutError, ConnectionError) as e:
                print(f"    !! erreur réseau : {e}", flush=True)
                _cancel_current_job()
                phase, error_code, elapsed, job_id = "network_error", None, 0.0, None

            if phase == "done":
                done += 1
                print(f"    OK en {elapsed:.0f}s (job {job_id[:8] if job_id else '?'})", flush=True)
                break
            if phase == "timeout":
                timeouts += 1
                print(f"    délai dépassé ({args.per_grid_timeout:.0f}s), on passe.", flush=True)
                break
            if phase == "rejected":
                # Combination rejected by the backend (language not ready
                # yet). If the language isn't pinned, another request is
                # drawn at random without counting anything; otherwise
                # it's a genuine failure.
                print("    combinaison refusée par le serveur (langue pas prête ?)", flush=True)
                if args.language is None and not _stop_requested:
                    attempt -= 1  # ne consomme pas de tentative
                    time.sleep(0.2)
                    continue
                errors += 1
                break
            # error / cancelled / gone / network_error
            print(f"    échec : phase={phase} error_code={error_code}", flush=True)
            if attempt <= args.retries and not _stop_requested:
                time.sleep(args.between)
                continue
            errors += 1
            break

        if _stop_requested:
            break
        time.sleep(args.between)

    elapsed_total = time.monotonic() - t0
    print(
        f"\nBilan : {done} générées, {errors} en échec, {timeouts} abandonnées "
        f"sur {args.count} demandées — {elapsed_total / 60:.1f} min.",
        flush=True,
    )
    return 0 if errors == 0 else 2


if __name__ == "__main__":
    sys.exit(main())
