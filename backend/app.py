#!/usr/bin/env python3
"""
Serveur back : expose le générateur de grilles de mots croisés (crossword_gen.py,
toute la logique métier de génération vit dans backend/) via une API JSON. Ne sert
aucun fichier statique — uniquement les deux routes listées ci-dessous. Toute autre
requête reçoit la réponse 404 par défaut de FastAPI (la documentation interactive
/docs, /redoc et /openapi.json est désactivée : ce ne sont pas des routes
nécessaires au fonctionnement).

Usage :
    uvicorn backend.app:app --port 8001
"""
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

from .clues import ClueGenerationError, LLMClueGenerator
from .crossword_gen import DEFAULT_HEIGHT, DEFAULT_WIDTH, DIFFICULTY_PRESETS, generate_grid

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


class GenerateRequest(BaseModel):
    language: str = Field(default="fr", description="fr, en, de, es ou it")
    width: int = Field(default=DEFAULT_WIDTH, ge=5, le=25, description="Largeur de la grille (horizontal)")
    height: int = Field(default=DEFAULT_HEIGHT, ge=5, le=25, description="Hauteur de la grille (vertical)")
    difficulty: str = Field(default="medium", description="easy, medium ou hard")
    seed: Optional[int] = None


@app.get("/api/health")
def health():
    return {"status": "ok"}


@app.post("/api/generate")
def generate(req: GenerateRequest):
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

    result = generate_grid(
        width=req.width,
        height=req.height,
        difficulty=req.difficulty,
        seed=req.seed,
        wordlist_path=str(WORDLISTS[req.language]),
    )
    if result is None:
        raise HTTPException(
            status_code=422,
            detail="Aucune grille remplissable trouvée avec ces paramètres, "
                   "réessayez ou changez la taille/difficulté.",
        )

    try:
        clues = clue_generator.generate(
            [(w["answer"], w["accented"]) for w in result["words"]],
            req.difficulty,
            req.language,
        )
    except ClueGenerationError as e:
        raise HTTPException(status_code=502, detail=str(e))

    for w in result["words"]:
        w["clue"] = clues.get(w["answer"], "")

    return result
