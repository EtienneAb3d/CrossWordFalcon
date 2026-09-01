#!/usr/bin/env python3
"""
Serveur back : expose le générateur de grilles de mots croisés (crossword_gen.py,
toute la logique métier de génération vit dans backend/) via une API JSON. Ne sert
aucun fichier statique — uniquement les routes listées ci-dessous. Toute autre
requête reçoit la réponse 404 par défaut de FastAPI (la documentation interactive
/docs, /redoc et /openapi.json est désactivée : ce ne sont pas des routes
nécessaires au fonctionnement).

Génération asynchrone avec suivi d'avancement : POST /api/generate ne bloque pas
jusqu'à la fin (génération de grille + définitions peut prendre de la dizaine de
secondes à plusieurs minutes) — il démarre un job en tâche de fond et répond
immédiatement avec un job_id ; le client interroge ensuite
GET /api/generate/status/{job_id} (polling) pour suivre l'avancement étape par
étape puis récupérer le résultat final. Chaque étape est aussi tracée dans
backend.log via le module `logging` standard (capturé par uvicorn -> voir
run_Falcon.sh).

Usage :
    uvicorn backend.app:app --port 3001
"""
import asyncio
import logging
import multiprocessing
import time
import uuid
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

from .clues import ClueGenerationError, LLMClueGenerator
from .crossword_gen import (
    DEFAULT_HEIGHT, DEFAULT_WIDTH, DIFFICULTY_PRESETS, GenerationCancelled, generate_grid,
)
from .svg_export import save_grid_png, save_grid_svg
from .system_info import get_system_info

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("crosswordfalcon")

clue_generator = LLMClueGenerator()

DATA_DIR = Path(__file__).resolve().parent.parent / "data"

# Language code -> wordlist file. Add an entry here (plus the matching
# data/wordlist_<code>_full.tsv and an option in the frontend's language
# selector) to support another grid/clue language.
WORDLISTS = {
    "fr": DATA_DIR / "wordlist_fr_full.tsv",
    "en": DATA_DIR / "wordlist_en_full.tsv",
    "de": DATA_DIR / "wordlist_de_full.tsv",
    "es": DATA_DIR / "wordlist_es_full.tsv",
    "it": DATA_DIR / "wordlist_it_full.tsv",
}

# Sélecteur "Mode" de l'interface web (voir frontend/static/index.html), à
# la demande explicite de l'utilisateur : "Flash/1000 Turbo/10000
# Rapide/100000 Moyen/500000 Ultra/5000000" — fixe directement le budget
# de recherche par tentative (`crossword_gen.try_fill`'s `deadline_checks`,
# voir sa propre docstring pour d'où vient ce paramètre), sans rapport avec
# la taille de la grille, contrairement à la formule par défaut (largeur ×
# hauteur × 2000) qu'un mode choisi ici remplace entièrement pour cette
# requête. Clé interne en anglais, comme toute autre valeur envoyée par
# l'interface (voir "difficulty") — seul le libellé affiché est traduit par
# langue (voir frontend/static/i18n.js's modeLabel*).
BUDGET_MODES = {
    "flash": 1_000,
    "turbo": 10_000,
    "fast": 100_000,
    "medium": 500_000,
    "ultra": 5_000_000,
}

app = FastAPI(title="CrossWordFalcon API", docs_url=None, redoc_url=None, openapi_url=None)

# In-memory job store: job_id -> {status, step, result, error}. A single
# uvicorn process (no --workers, see run_Falcon.sh) is all this app ever
# runs as, so a plain dict needs no locking or external store. Bounded to
# the most recent MAX_JOBS entries so a long-running process doesn't grow
# this dict forever — a finished job only needs to survive long enough for
# the frontend's polling loop to pick up its result.
JOBS = {}
MAX_JOBS = 50

# job_id -> multiprocessing.Event, kept *separate* from JOBS itself, at the
# user's explicit request (bouton "Stop") — neither a threading.Event nor a
# multiprocessing.Event is JSON-serializable, and GET /api/generate/status/
# {job_id} returns the JOBS entry directly (`return job`), so putting it
# there would break that endpoint's response the moment a client polled a
# running job. Deliberately `multiprocessing.Event` rather than the plain
# `threading.Event` used here originally: `generate_grid()`'s own CSP search
# runs each parallel attempt in a separate OS process
# (`concurrent.futures.ProcessPoolExecutor`, see crossword_gen.py), and a
# `threading.Event` has no meaning across a process boundary — pickling one
# into a worker process silently reconstructs an independent, disconnected
# copy, never seeing `.set()` calls made in this process. A
# `multiprocessing.Event` is real cross-process shared state (backed by the
# OS), so `Filler._backtrack` (running inside a worker process) can check
# the *same* event this endpoint sets — needed so a "Stop" click actually
# interrupts a pattern search already in progress, not just the gap between
# paliers (see crossword_gen.CANCEL_CHECK_INTERVAL). `generate_grid()`
# itself (between paliers) and `LLMClueGenerator.generate()` (between
# words) check the very same event too — it's a signal everywhere, never a
# forced kill of an already-running thread/process. Evicted in lockstep
# with JOBS's own MAX_JOBS bound in _new_job() below, so this dict never
# outlives its own JOBS entry.
CANCEL_EVENTS = {}

# asyncio.create_task() only holds a weak reference to the task it
# schedules — without another strong reference kept somewhere, the task can
# be garbage-collected mid-run (a documented asyncio footgun). This set is
# that reference; each task removes itself once done.
_BACKGROUND_TASKS = set()


class GenerateRequest(BaseModel):
    language: str = Field(default="fr", description="fr, en, de, es ou it")
    # Pas de borne haute, à la demande explicite de l'utilisateur (le
    # plafond précédent, 25, a été retiré) — seule une borne basse reste,
    # une grille plus petite que ça n'a plus vraiment de sens comme mots
    # croisés. Le CLI (`crossword_gen.py`'s `main()`) n'a jamais eu de
    # plafond du tout ; cette Field est donc désormais alignée sur lui.
    width: int = Field(default=DEFAULT_WIDTH, ge=5, description="Largeur de la grille (horizontal)")
    height: int = Field(default=DEFAULT_HEIGHT, ge=5, description="Hauteur de la grille (vertical)")
    difficulty: str = Field(default="easy", description="easy, medium ou hard")
    seed: Optional[int] = None
    # 1 par défaut (relevé de 0, à la demande explicite de l'utilisateur) —
    # le sondage statistique de "graines" (voir crossword_gen.py's
    # sample_letter_biases/generate_grid — anciennement appelées "lettres
    # forcées", renommées à la demande explicite de l'utilisateur : "des
    # emplacements qui initient les premiers placements, ou les influencent
    # quand il y a déjà d'autres lettres") était auparavant appliqué
    # systématiquement à une fraction fixe (5 %) ; c'est désormais un champ
    # de saisie libre de l'interface (un entier entre 0 et 100, plutôt
    # qu'une liste de pourcentages prédéfinis — à la demande explicite de
    # l'utilisateur, voir frontend/static/index.html), convertie en
    # fraction (`percent / 100`) juste avant d'appeler generate_grid.
    force_letters_percent: int = Field(
        default=1, ge=0, le=100,
        description="Pourcentage de graines en début de remplissage (entier, 0 à 100)",
    )
    # 14 % par défaut côté API (l'interface utilise cette même valeur fixe
    # comme valeur initiale, voir frontend/static/index.html — remplace
    # une formule dépendante de la taille de la grille utilisée
    # auparavant, à la demande explicite de l'utilisateur) — remplace
    # POST_PREFILL_BLACK_FRACTION (crossword_gen.py), auparavant une
    # constante fixe à 10 % non réglable depuis l'interface. Appliqué à
    # chaque palier qui part d'une grille vierge ou d'un nettoyage
    # (`_build_retry_seed`) — jamais à un palier de reprise "telle-quelle"
    # (`_pattern_continue`), qui ne repose sur aucun nouvel appel à
    # make_pattern et ne peut donc ajouter aucune case noire de toute
    # façon. Champ de saisie libre depuis l'interface (un entier entre 0
    # et 100), à la demande explicite de l'utilisateur, plutôt qu'une
    # liste de pourcentages prédéfinis. Le pourcentage est calculé sur le
    # nombre de cases blanches *avant* le pré-remplissage de ce palier
    # (`make_pattern`'s `initial_white_count`), pas sur ce qu'il en reste
    # une fois le pré-remplissage terminé — à la demande explicite de
    # l'utilisateur ("les cases noires ajoutées en pré-remplissage
    # comptent pour l'objectif de remplissage en noir") : si le
    # pré-remplissage a déjà posé plus de cases que ce pourcentage n'en
    # réclame, aucune case supplémentaire n'est ajoutée pour cette raison.
    # Removed once (mistakenly, alongside the unrelated per-cycle
    # single-cell lock), then restored — only that separate lock was ever
    # meant to go, not this percentage mechanism (see CLAUDE.md).
    black_enrichment_percent: int = Field(
        default=17, ge=0, le=100,
        description=(
            "Pourcentage de cases blanches (avant pré-remplissage) transformées "
            "en cases noires à chaque palier, pré-remplissage inclus (entier, 0 à 100)"
        ),
    )
    # Sélecteur "Mode" (voir BUDGET_MODES ci-dessus), à la demande explicite
    # de l'utilisateur — fixe directement le budget de recherche par
    # tentative, remplaçant pour cette requête la formule par défaut de
    # `crossword_gen.try_fill` (largeur × hauteur × 2000). "medium" par
    # défaut, le mode le plus proche en ordre de grandeur de cette même
    # formule sur la grille de référence 15×10 (300 000).
    mode: str = Field(
        default="medium",
        description=f"Mode de budget de recherche : {sorted(BUDGET_MODES)}",
    )


@app.get("/api/health")
def health():
    return {"status": "ok"}


@app.get("/api/system_info")
def system_info():
    """Best-effort local hardware/model report for the web UI's info
    badge (see frontend/static/script.js) — not on the hot path of any
    grid generation, just a nice-to-have status display, so this is
    computed fresh per request rather than cached: it's cheap (a couple
    of subprocess probes, see backend/system_info.py) and a user could
    plausibly ask about a machine's hardware changing (e.g. hot-swapped
    external GPU) across the lifetime of one long-running server
    process."""
    return get_system_info(clue_generator.model)


def _new_job():
    if len(JOBS) >= MAX_JOBS:
        oldest = next(iter(JOBS))
        del JOBS[oldest]
        CANCEL_EVENTS.pop(oldest, None)
    job_id = uuid.uuid4().hex
    JOBS[job_id] = {
        "status": "running", "step": {"code": "starting"}, "result": None,
        "error": None, "error_code": None,
        "examples_history": [],
        # "Continuer" button (see POST /api/generate/continue/{job_id}
        # below), at the user's explicit request: set once generate_grid()
        # exhausts every one of its `attempts` without finding a fillable
        # grid (see the "pattern_failed" branch of progress() in
        # _run_generate_job) — a JSON-safe snapshot (crossword_gen.py's
        # `_serialize_resume_state`) of exactly where that failed run left
        # off, so a follow-up generation can pick up from there instead of
        # starting over from a blank grid. Stays `None` for a job that
        # never fails this way (succeeds, is cancelled, or fails for an
        # unrelated reason with nothing meaningful to resume from).
        "resume_state": None,
        # The original request's own parameters (language/width/height/
        # etc.), needed so POST /api/generate/continue/{job_id} can start a
        # brand new job with the exact same settings, just seeded from
        # `resume_state` above instead of a blank grid — set once in
        # _run_generate_job, right before generate_grid() is even called.
        "request": None,
    }
    CANCEL_EVENTS[job_id] = multiprocessing.Event()
    return job_id


async def _run_generate_job(job_id, req, resume_state=None):
    job = JOBS[job_id]
    short_id = job_id[:8]
    cancel_event = CANCEL_EVENTS[job_id]
    # Stored as a plain JSON-safe dict (not the pydantic model instance
    # itself), so POST /api/generate/continue/{job_id} can rebuild an
    # equivalent GenerateRequest later without depending on the original
    # object's lifetime — see _new_job's own "request" field.
    job["request"] = req.model_dump()

    # Horodatages des deux frontières internes de generate_grid() dont
    # progress() ci-dessous a besoin pour séparer la durée de génération
    # de celle d'optimisation (voir grid_start plus bas) — à la demande
    # explicite de l'utilisateur : "Optimisation en XhXmnXs" entre
    # "Grille générée en..." et "Définitions générées en...". Un dict
    # plutôt que des variables locales séparées : muté depuis l'intérieur
    # de `progress()` (une closure), pas besoin d'un `nonlocal` par clé.
    # `generate_grid()` émet "minimizing" juste avant `minimize_black_
    # squares()` (fin de la recherche/remplissage) et "grid_ready" juste
    # après (fin de l'optimisation) — voir crossword_gen.py.
    phase_times = {}

    def progress(step, **data):
        job["step"] = {"code": step, **data}
        if step in ("minimizing", "grid_ready"):
            phase_times[step] = time.monotonic()
        # `examples` (a list of dicts, each
        # {example_grid, impossible_cells, forced_cells} — no fixed cap, see
        # crossword_gen.py's generate_grid, CLAUDE.md) is otherwise only
        # visible in `job["step"]` for the single progress event that
        # carries it — the very next event (e.g. the next palier's plain
        # "pattern" step, which carries none) overwrites `job["step"]`
        # entirely, so a client polling every POLL_INTERVAL_MS (frontend/
        # static/script.js) can easily miss that narrow window outright,
        # especially when paliers resolve faster than the poll interval.
        #
        # `job["examples_history"]` used to be a single `last_examples`
        # slot, just overwritten every time a new `examples` arrived — at
        # the user's explicit request, after a real, reported confusion
        # this caused: a palier that resolves fast enough (a real, observed
        # case — several paliers completing within a single 2-second poll
        # interval) could have its own `examples` overwritten before the
        # client ever polls it even once, so the very *first* preview a
        # client ever sees might already belong to a *later* palier than
        # palier 1 — misleadingly looking like palier 1 itself started with
        # forced/locked cells it never actually had. Now every `examples`
        # update is *appended* to this list instead (never overwritten,
        # never dropped) — a real history of every palier's own end state,
        # in order — so the frontend (`script.js`'s `pollJob()`) can walk
        # through it one entry per poll, guaranteeing every single palier's
        # own preview gets shown at least once, in order, however fast
        # paliers actually resolve relative to the polling interval.
        #
        # Each entry is `{"step": ..., "examples": ...}`, not a bare
        # `examples` list, at the user's explicit request: "L'historique
        # des visualisation doit inclure le status (indiquant notamment le
        # nombre de cycles)." The web UI's manual back/forward navigation
        # through this same history (script.js's previewHistory, see
        # CLAUDE.md) showed only the preview grids on their own, with no
        # indication of which cycle/attempt a given grid actually came
        # from — `job["step"]` (just built above) already carries exactly
        # that (code, attempt, attempts, total_attempts, or current/total
        # for the "clues" step). Stored as a shallow copy with its own
        # `examples` key stripped out, not the dict itself — `job["step"]`
        # is otherwise a fresh, never-mutated-afterward dict every call, so
        # storing it directly would have been safe too, but it also
        # contains this very same `examples` list under `data`'s own key,
        # which would otherwise be carried twice per entry (once as the
        # step's own field, once as this entry's dedicated `examples` key)
        # for no benefit — nothing ever reads `entry["step"]["examples"]`.
        examples = data.get("examples")
        if examples:
            step_without_examples = {k: v for k, v in job["step"].items() if k != "examples"}
            job["examples_history"].append({"step": step_without_examples, "examples": examples})
        # "Continuer" button, at the user's explicit request — see
        # _new_job's own "resume_state" field and crossword_gen.py's
        # _serialize_resume_state. Only ever present on the single
        # "pattern_failed" event a total failure emits (once per job), so a
        # plain unconditional assignment is enough here — unlike
        # `examples_history` above, which appends across many events, there
        # is only ever one `resume_state` to keep per job.
        if "resume_state" in data:
            job["resume_state"] = data["resume_state"]
        logger.info("[%s] %s %s", short_id, step, data)

    try:
        logger.info(
            "[%s] starting generation: language=%s width=%s height=%s difficulty=%s "
            "force_letters_percent=%s black_enrichment_percent=%s mode=%s",
            short_id, req.language, req.width, req.height, req.difficulty,
            req.force_letters_percent, req.black_enrichment_percent, req.mode,
        )
        # Durées affichées au-dessus de la grille finale (`#generation-times`
        # côté frontend), à la demande explicite de l'utilisateur — mesurées
        # ici plutôt que côté client, qui n'a aucun moyen fiable de savoir
        # quand chaque phase a réellement commencé/fini (seul ce process
        # voit directement les deux appels bloquants ci-dessous). `time.
        # monotonic()`, pas `time.time()` : une horloge murale peut reculer
        # (ajustement NTP, changement d'heure), ce qui fausserait une durée
        # calculée par simple soustraction — `monotonic()` ne recule jamais.
        grid_start = time.monotonic()
        result = await asyncio.to_thread(
            generate_grid,
            width=req.width,
            height=req.height,
            difficulty=req.difficulty,
            seed=req.seed,
            wordlist_path=str(WORDLISTS[req.language]),
            on_progress=progress,
            force_letters_fraction=req.force_letters_percent / 100,
            black_enrichment_fraction=req.black_enrichment_percent / 100,
            cancel_event=cancel_event,
            deadline_checks=BUDGET_MODES[req.mode],
            resume_state=resume_state,
        )
        if result is None:
            job["status"] = "error"
            job["error_code"] = "no_fillable_grid"
            job["error"] = (
                "Aucune grille remplissable trouvée avec ces paramètres, "
                "réessayez ou changez la taille/difficulté."
            )
            logger.warning("[%s] no fillable grid found", short_id)
            return
        # "minimizing"/"grid_ready" toujours présents ici (result n'est
        # jamais None sans être passé par toute la pipeline) — `.get(...,
        # grid_start)` reste une protection défensive, pas un cas normal :
        # sans elle, l'absence improbable de l'un des deux ferait échouer
        # tout le job juste pour ce calcul de durée, alors que la grille
        # elle-même est déjà prête.
        search_done = phase_times.get("minimizing", grid_start)
        optimization_done = phase_times.get("grid_ready", search_done)
        result["generation_duration_seconds"] = search_done - grid_start
        result["optimization_duration_seconds"] = optimization_done - search_done

        # Aperçu de la grille finale (déjà minimisée), au tout début de la
        # génération des définitions — à la demande explicite de
        # l'utilisateur, réutilisant exactement le même mécanisme que
        # l'aperçu de crossword_gen.py's "minimizing" (voir progress()
        # ci-dessus). `result["solution"]` (pas `result["pattern"]`, qui ne
        # contient que le motif noir/blanc nu) contient déjà les vraies
        # lettres — construite par build_letters_grid, exactement le même
        # format que `example_grid` attend (case noire ou lettre, jamais de
        # "."). Montrer les lettres ici ne les affiche pas pour autant :
        # `renderAttemptPreview()` (frontend/static/script.js) les masque
        # déjà par défaut et ne les révèle que si l'utilisateur active
        # #attempt-preview-reveal-btn, exactement comme pour l'aperçu
        # "minimizing" de crossword_gen.py (voir CLAUDE.md) — un revirement
        # analogue au sien, à la demande explicite de l'utilisateur, par
        # rapport à la toute première version de cet aperçu qui montrait
        # volontairement `result["pattern"]` sans aucune lettre.
        # `impossible_cells`/`forced_cells`/`locked_cells` vides : cette
        # grille est déjà remplie et minimisée avec succès, il n'y a ni case
        # impossible, ni lettre forcée, ni case verrouillée à signaler.
        progress(
            "clues", current=0, total=len(result["words"]),
            examples=[{
                "example_grid": result["solution"],
                "impossible_cells": [],
                "forced_cells": [],
                "locked_cells": [],
            }],
        )
        clues_start = time.monotonic()
        clues = await asyncio.to_thread(
            clue_generator.generate,
            [(w["answer"], w["accented"], w["canonical"]) for w in result["words"]],
            req.difficulty,
            req.language,
            on_progress=lambda current, total: progress("clues", current=current, total=total),
            cancel_event=cancel_event,
        )
        result["clues_duration_seconds"] = time.monotonic() - clues_start
        for w in result["words"]:
            w["clue"] = clues.get(w["answer"], "")

        progress("saving")
        try:
            svg_path = await asyncio.to_thread(
                save_grid_svg, result, req.language, req.difficulty, req.mode
            )
            logger.info("[%s] saved %s", short_id, svg_path)
            try:
                png_path = await asyncio.to_thread(save_grid_png, svg_path)
                logger.info("[%s] saved %s", short_id, png_path)
            except OSError as e:
                logger.warning("[%s] failed to save grid PNG sample: %s", short_id, e)
        except OSError as e:
            # A durable copy of the grid is a nice-to-have, not the point
            # of the request — never fail the user's grid over it.
            logger.warning("[%s] failed to save grid SVG: %s", short_id, e)

        progress("done")
        job["status"] = "done"
        job["result"] = result
        logger.info("[%s] done", short_id)
    except GenerationCancelled:
        # Interruption demandée par l'utilisateur (bouton "Stop", voir
        # POST /api/generate/cancel/{job_id} plus bas) — un statut à part,
        # jamais "error" : ce n'est pas un échec, juste un arrêt volontaire,
        # et le frontend l'affiche donc sans le style d'erreur (voir
        # frontend/static/script.js's pollJob()).
        job["status"] = "cancelled"
        logger.info("[%s] cancelled by user", short_id)
    except ClueGenerationError as e:
        job["status"] = "error"
        job["error_code"] = "clue_generation_failed"
        job["error"] = str(e)
        logger.warning("[%s] clue generation failed: %s", short_id, e)
    except Exception:
        job["status"] = "error"
        job["error_code"] = "internal_error"
        job["error"] = "Erreur interne."
        logger.exception("[%s] unhandled error during generation", short_id)


def _validate_generate_request(req):
    """Shared by POST /api/generate and POST /api/generate/continue/{job_id}
    (see below) — the latter rebuilds a `GenerateRequest` from a previous
    job's own stored parameters rather than from a fresh HTTP body, but it
    still deserves the exact same validation a first-time request gets
    (e.g. in case a server upgrade narrowed one of these value sets since
    the original job was submitted)."""
    if req.language not in WORDLISTS:
        raise HTTPException(
            status_code=400,
            detail=f"langue inconnue : {req.language!r} (attendu : {sorted(WORDLISTS)})",
        )
    if req.difficulty not in DIFFICULTY_PRESETS:
        raise HTTPException(
            status_code=400,
            detail=f"difficulté inconnue : {req.difficulty!r} "
                   f"(attendu : {sorted(DIFFICULTY_PRESETS)})",
        )
    if req.mode not in BUDGET_MODES:
        raise HTTPException(
            status_code=400,
            detail=f"mode inconnu : {req.mode!r} (attendu : {sorted(BUDGET_MODES)})",
        )


@app.post("/api/generate", status_code=202)
async def generate(req: GenerateRequest):
    _validate_generate_request(req)
    job_id = _new_job()
    task = asyncio.create_task(_run_generate_job(job_id, req))
    _BACKGROUND_TASKS.add(task)
    task.add_done_callback(_BACKGROUND_TASKS.discard)
    return {"job_id": job_id}


@app.get("/api/generate/status/{job_id}")
def generate_status(job_id: str):
    job = JOBS.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="job inconnu (expiré ou jamais existé)")
    return job


@app.post("/api/generate/cancel/{job_id}")
def generate_cancel(job_id: str):
    """Déclenche le `cancel_event` du job (bouton "Stop" de l'interface,
    voir CANCEL_EVENTS) — un simple signal, jamais un arrêt forcé : le job
    continue de tourner jusqu'à son prochain point de contrôle coopératif
    (voir crossword_gen.GenerationCancelled), après quoi son statut passe
    à "cancelled" (visible au prochain sondage de GET /api/generate/status/
    {job_id}, pas immédiatement ici). Sans effet si le job est déjà
    terminé — `.set()` sur un événement déjà positionné, ou sur un job qui
    a déjà fini par une autre voie, ne fait rien de mal."""
    job = JOBS.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="job inconnu (expiré ou jamais existé)")
    CANCEL_EVENTS[job_id].set()
    return {"status": "cancelling"}


@app.post("/api/generate/continue/{job_id}", status_code=202)
async def generate_continue(job_id: str):
    """Bouton "Continuer" de l'interface web, à la demande explicite de
    l'utilisateur : affiché quand un job se termine en `status: "error"`
    avec `error_code: "no_fillable_grid"` (voir _run_generate_job) —
    relance un nouveau job, avec les mêmes paramètres que l'original
    (`job["request"]`), mais en reprenant depuis l'état exact où la
    génération précédente s'est arrêtée (`job["resume_state"]`, voir
    crossword_gen.py's `_serialize_resume_state`) au lieu de repartir d'une
    grille vierge — un nouveau budget complet de `attempts` (200 par
    défaut) paliers, pas une poursuite du même job. Renvoie un job_id
    distinct du job d'origine (le job d'origine reste consultable tel
    quel), exactement comme POST /api/generate : le client se contente
    d'interroger ce nouveau job_id de la même façon."""
    job = JOBS.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="job inconnu (expiré ou jamais existé)")
    if job.get("resume_state") is None or job.get("request") is None:
        raise HTTPException(
            status_code=400,
            detail="aucun état de reprise disponible pour ce job",
        )
    req = GenerateRequest(**job["request"])
    _validate_generate_request(req)
    new_job_id = _new_job()
    task = asyncio.create_task(
        _run_generate_job(new_job_id, req, resume_state=job["resume_state"])
    )
    _BACKGROUND_TASKS.add(task)
    task.add_done_callback(_BACKGROUND_TASKS.discard)
    return {"job_id": new_job_id}
