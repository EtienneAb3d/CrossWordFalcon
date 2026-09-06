#!/usr/bin/env python3
"""Automation/Populate.py — génère des grilles complètes une par une.

À la demande explicite de l'utilisateur : "générer 1000 grilles une par
une pour ne pas surcharger les files d'attente". Le script ne lance JAMAIS
deux générations en parallèle : il soumet une requête à
`POST /api/generate`, sonde `GET /api/generate/phase/{job_id}` (la route
condensée ajoutée pour ça) jusqu'à ce que le job soit `finished`, puis
passe au suivant. Les files d'attente `GRID_QUEUE`/`CLUES_QUEUE` du back
(voir backend/app.py) ne voient donc jamais plus d'un job à la fois venant
d'ici — d'autres clients (l'interface web) peuvent continuer à s'en servir
normalement en même temps.

Chaque grille terminée est automatiquement enregistrée dans la
bibliothèque par le back (`save_grid_json`, voir `_run_generate_job`) —
c'est tout l'intérêt de "populate".

Par défaut les paramètres (langue, difficulté, taille) sont tirés au
hasard à chaque grille pour peupler la bibliothèque avec de la variété ;
on peut en figer n'importe lequel en ligne de commande (voir --help).

Usage :
    .venv/bin/python Automation/Populate.py            # 1000 grilles, params aléatoires
    .venv/bin/python Automation/Populate.py --count 50 --language fr --difficulty easy
    .venv/bin/python Automation/Populate.py --mode turbo --width 15 --height 10

Ctrl-C : arrête proprement après la grille en cours (affiche le bilan).
"""
import argparse
import json
import os
import random
import signal
import sys
import time
import urllib.error
import urllib.request

LANGUAGES = ["fr", "en", "de", "es", "it", "pt"]
DIFFICULTIES = ["easy", "medium", "hard"]
MODES = ["flash", "turbo", "fast", "medium", "ultra"]

# Bornes de la taille aléatoire d'une grille (largeur ET hauteur), à la
# demande explicite de l'utilisateur : "des tailles entre 8 et 20
# (horizontal et vertical)". `--width`/`--height` peuvent toujours figer
# une valeur hors de cet intervalle si besoin (bornée seulement par le
# back, 5 à 30).
MIN_SIZE = 8
MAX_SIZE = 20

DEFAULT_BASE_URL = os.environ.get(
    "CROSSWORDFALCON_BACKEND_URL", "http://127.0.0.1:3001"
)

_stop_requested = False


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
    """GET si payload est None, sinon POST JSON. Renvoie le corps décodé
    en JSON. Lève urllib.error.HTTPError / URLError comme d'habitude."""
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


def _pick(fixed, pool):
    """`fixed` si non None, sinon un tirage aléatoire dans `pool`."""
    return fixed if fixed is not None else random.choice(pool)


def _build_request(args):
    width = args.width if args.width is not None else random.randint(MIN_SIZE, MAX_SIZE)
    height = args.height if args.height is not None else random.randint(MIN_SIZE, MAX_SIZE)
    return {
        "language": _pick(args.language, LANGUAGES),
        "difficulty": _pick(args.difficulty, DIFFICULTIES),
        # Toujours le mode "medium", à la demande explicite de
        # l'utilisateur ("Populate ne doit utiliser que le mode MOYEN") —
        # le mode "ultra" peut passer des heures sur la seule recherche
        # d'une grille, ce qui rend un peuplement de 1000 grilles
        # ingérable. `--mode` peut toujours forcer une autre valeur.
        "mode": args.mode or "medium",
        "width": width,
        "height": height,
        # seed omis volontairement -> le back en tire un ; on veut des
        # grilles différentes à chaque fois.
    }


def _generate_one(base_url, req, poll_interval, per_grid_timeout):
    """Soumet une génération et sonde sa phase jusqu'à la fin. Renvoie
    (job_id, phase_finale, error_code|None, secondes_écoulées)."""
    started = time.monotonic()
    try:
        resp = _http_json(f"{base_url}/api/generate", payload=req, timeout=60)
    except urllib.error.HTTPError as e:
        if e.code == 400:
            # Requête refusée d'emblée (ex. une langue tout juste ajoutée
            # dont le dictionnaire n'est pas encore construit) — pas une
            # panne : on retire cette combinaison et on retire une autre,
            # sans la compter comme échec ni consommer un retry.
            return None, "rejected", None, time.monotonic() - started
        raise
    job_id = resp["job_id"]
    last_phase = None
    while True:
        time.sleep(poll_interval)
        if per_grid_timeout and (time.monotonic() - started) > per_grid_timeout:
            return job_id, "timeout", None, time.monotonic() - started
        try:
            phase_info = _http_json(
                f"{base_url}/api/generate/phase/{job_id}", timeout=30
            )
        except urllib.error.HTTPError as e:
            if e.code == 404:
                # Job évincé de JOBS (MAX_JOBS) avant qu'on ait vu la fin —
                # rare ici (un seul job à la fois), traité comme inconnu.
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

    print(
        f"Peuplement : {args.count} grilles, une par une, via {base_url}\n"
        f"Paramètres : "
        f"langue={args.language or 'aléatoire'}, "
        f"difficulté={args.difficulty or 'aléatoire'}, "
        f"mode={args.mode or 'medium'}, "
        f"taille={args.width or f'aléatoire {MIN_SIZE}-{MAX_SIZE}'}"
        f"x{args.height or f'aléatoire {MIN_SIZE}-{MAX_SIZE}'}\n",
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
            label = (
                f"[{i}/{args.count}] {req['language']}/{req['difficulty']}/"
                f"{req['mode']} {req['width']}x{req['height']}"
                + (f" (tentative {attempt})" if attempt > 1 else "")
            )
            print(label, flush=True)
            try:
                job_id, phase, error_code, elapsed = _generate_one(
                    base_url, req, args.poll, args.per_grid_timeout
                )
            except (urllib.error.URLError, TimeoutError, ConnectionError) as e:
                print(f"    !! erreur réseau : {e}", flush=True)
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
                # Combinaison refusée par le back (langue pas encore prête).
                # Si la langue n'est pas figée, on retire une autre requête
                # au hasard sans rien compter ; sinon c'est un vrai échec.
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
