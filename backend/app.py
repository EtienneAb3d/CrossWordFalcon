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
    uvicorn backend.app:app --port 8001
"""
import asyncio
import logging
import uuid
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

from .clues import ClueGenerationError, LLMClueGenerator
from .crossword_gen import DEFAULT_HEIGHT, DEFAULT_WIDTH, DIFFICULTY_PRESETS, generate_grid
from .svg_export import save_grid_png, save_grid_svg

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

app = FastAPI(title="CrossWordFalcon API", docs_url=None, redoc_url=None, openapi_url=None)

# In-memory job store: job_id -> {status, step, result, error}. A single
# uvicorn process (no --workers, see run_Falcon.sh) is all this app ever
# runs as, so a plain dict needs no locking or external store. Bounded to
# the most recent MAX_JOBS entries so a long-running process doesn't grow
# this dict forever — a finished job only needs to survive long enough for
# the frontend's polling loop to pick up its result.
JOBS = {}
MAX_JOBS = 50

# asyncio.create_task() only holds a weak reference to the task it
# schedules — without another strong reference kept somewhere, the task can
# be garbage-collected mid-run (a documented asyncio footgun). This set is
# that reference; each task removes itself once done.
_BACKGROUND_TASKS = set()


class GenerateRequest(BaseModel):
    language: str = Field(default="fr", description="fr, en, de, es ou it")
    width: int = Field(default=DEFAULT_WIDTH, ge=5, le=25, description="Largeur de la grille (horizontal)")
    height: int = Field(default=DEFAULT_HEIGHT, ge=5, le=25, description="Hauteur de la grille (vertical)")
    difficulty: str = Field(default="easy", description="easy, medium ou hard")
    seed: Optional[int] = None


@app.get("/api/health")
def health():
    return {"status": "ok"}


def _new_job():
    if len(JOBS) >= MAX_JOBS:
        oldest = next(iter(JOBS))
        del JOBS[oldest]
    job_id = uuid.uuid4().hex
    JOBS[job_id] = {
        "status": "running", "step": {"code": "starting"}, "result": None,
        "error": None, "error_code": None,
    }
    return job_id


async def _run_generate_job(job_id, req):
    job = JOBS[job_id]
    short_id = job_id[:8]

    def progress(step, **data):
        job["step"] = {"code": step, **data}
        logger.info("[%s] %s %s", short_id, step, data)

    try:
        logger.info(
            "[%s] starting generation: language=%s width=%s height=%s difficulty=%s",
            short_id, req.language, req.width, req.height, req.difficulty,
        )
        result = await asyncio.to_thread(
            generate_grid,
            width=req.width,
            height=req.height,
            difficulty=req.difficulty,
            seed=req.seed,
            wordlist_path=str(WORDLISTS[req.language]),
            on_progress=progress,
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

        progress("clues", current=0, total=len(result["words"]))
        clues = await asyncio.to_thread(
            clue_generator.generate,
            [(w["answer"], w["accented"], w["canonical"]) for w in result["words"]],
            req.difficulty,
            req.language,
            on_progress=lambda current, total: progress("clues", current=current, total=total),
        )
        for w in result["words"]:
            w["clue"] = clues.get(w["answer"], "")

        progress("saving")
        try:
            svg_path = await asyncio.to_thread(save_grid_svg, result, req.language)
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


@app.post("/api/generate", status_code=202)
async def generate(req: GenerateRequest):
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
