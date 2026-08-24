#!/usr/bin/env python3
"""
Serveur middleware : sert la page HTML+JS du générateur de grilles et relaie
les appels /api/* vers le serveur back (backend/app.py). Le navigateur ne
parle qu'à ce serveur — pas de CORS, pas d'exposition directe du back.

Seuls les fichiers du dossier `static/` (HTML, JS, CSS...) et les routes
/api/* sont servis : toute autre requête reçoit un 404 (comportement par
défaut de StaticFiles pour les fichiers absents, et de FastAPI pour les
routes inconnues).

Usage :
    uvicorn frontend.server:app --port 8000
"""
import os
from pathlib import Path

import httpx
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

STATIC_DIR = Path(__file__).resolve().parent / "static"
VERSION_PATH = Path(__file__).resolve().parent.parent / "VERSION.txt"
BACKEND_URL = os.environ.get("CROSSWORDFALCON_BACKEND_URL", "http://127.0.0.1:8001")

app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)


@app.post("/api/generate")
async def proxy_generate(request: Request):
    body = await request.body()
    try:
        # Grid generation (CSP fill) plus several LLM clue-generation batches
        # can together take a few minutes on a small local model — well past
        # a typical short HTTP timeout. Generous on purpose: a real backend
        # outage still fails via httpx.RequestError below, just slower.
        async with httpx.AsyncClient(timeout=240.0) as client:
            resp = await client.post(
                f"{BACKEND_URL}/api/generate",
                content=body,
                headers={"content-type": "application/json"},
            )
    except httpx.RequestError:
        raise HTTPException(status_code=502, detail="Serveur back indisponible.")
    return JSONResponse(status_code=resp.status_code, content=resp.json())


@app.get("/api/version")
async def version():
    try:
        return {"version": VERSION_PATH.read_text(encoding="utf-8").strip()}
    except FileNotFoundError:
        return {"version": None}


@app.get("/api/health")
async def proxy_health():
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.get(f"{BACKEND_URL}/api/health")
    except httpx.RequestError:
        raise HTTPException(status_code=502, detail="Serveur back indisponible.")
    return JSONResponse(status_code=resp.status_code, content=resp.json())


# Montée en dernier : les routes /api/* déclarées ci-dessus restent prioritaires,
# tout le reste est résolu dans static/ (404 si le fichier n'y existe pas).
app.mount("/", StaticFiles(directory=STATIC_DIR, html=True), name="static")
