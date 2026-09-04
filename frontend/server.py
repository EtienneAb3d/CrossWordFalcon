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
    uvicorn frontend.server:app --port 3000
"""
import os
from pathlib import Path

import httpx
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

STATIC_DIR = Path(__file__).resolve().parent / "static"
VERSION_PATH = Path(__file__).resolve().parent.parent / "VERSION.txt"
BACKEND_URL = os.environ.get("CROSSWORDFALCON_BACKEND_URL", "http://127.0.0.1:3001")

# Délai maximal accordé à un appel proxy vers le back avant d'abandonner et de
# renvoyer un 502 au navigateur — à la demande explicite de l'utilisateur,
# suite à un rapport de 502 sporadiques sur /api/generate/status sans aucune
# trace correspondante dans le log du back (voir CLAUDE.md / le
# project-best-practices SKILL) : rien dans le log applicatif signifie que la
# connexion n'a jamais atteint la couche FastAPI, ce qui pointe soit vers un
# redémarrage du process back, soit vers son event-loop ponctuellement trop
# chargé pour accepter une nouvelle connexion à temps (une génération peut
# lancer jusqu'à PARALLEL_ATTEMPTS processus CSP en parallèle — voir
# crossword_gen.py) — relevé à 30s (contre 10s/5s selon l'endpoint
# auparavant) pour laisser de la marge dans les deux cas, la même valeur pour
# tous les appels proxy plutôt que des délais différents sans raison claire.
# Voir aussi FETCH_TIMEOUT_MS dans frontend/static/script.js, qui doit rester
# strictement supérieur pour ne jamais expirer côté navigateur avant ce
# délai-ci côté proxy.
PROXY_TIMEOUT_S = 30.0

app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)


@app.middleware("http")
async def no_cache(request: Request, call_next):
    """Tells the browser never to cache anything from this origin — static
    files (index.html/script.js/style.css/logo.*) included, not just the
    /api/* responses. The app is small and iterated on directly by editing
    these files; a stale cached copy (especially of script.js) is a much
    more likely and confusing failure mode here than the extra requests are
    a real cost."""
    response = await call_next(request)
    response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
    response.headers["Pragma"] = "no-cache"
    response.headers["Expires"] = "0"
    return response


@app.post("/api/generate")
async def proxy_generate(request: Request):
    # The backend runs generation as a background job and responds almost
    # immediately with a job_id (see backend/app.py) — the browser then
    # polls /api/generate/status/{job_id} for progress and the final
    # result. PROXY_TIMEOUT_S is generous anyway (see its own comment),
    # covering the rare case this specific call itself gets delayed too.
    body = await request.body()
    try:
        async with httpx.AsyncClient(timeout=PROXY_TIMEOUT_S) as client:
            resp = await client.post(
                f"{BACKEND_URL}/api/generate",
                content=body,
                headers={"content-type": "application/json"},
            )
    except httpx.RequestError:
        # Structured, not a plain string, so the frontend's i18n config
        # (describeErrorCode() in script.js) can show this in the UI's
        # current language instead of always-French text.
        raise HTTPException(status_code=502, detail={"code": "backend_unavailable"})
    return JSONResponse(status_code=resp.status_code, content=resp.json())


@app.get("/api/generate/status/{job_id}")
async def proxy_generate_status(job_id: str):
    try:
        async with httpx.AsyncClient(timeout=PROXY_TIMEOUT_S) as client:
            resp = await client.get(f"{BACKEND_URL}/api/generate/status/{job_id}")
    except httpx.RequestError:
        raise HTTPException(status_code=502, detail={"code": "backend_unavailable"})
    return JSONResponse(status_code=resp.status_code, content=resp.json())


@app.post("/api/generate/cancel/{job_id}")
async def proxy_generate_cancel(job_id: str):
    """Relaie le bouton "Stop" de l'interface (voir script.js) vers le
    back — même schéma que les autres routes de ce proxy."""
    try:
        async with httpx.AsyncClient(timeout=PROXY_TIMEOUT_S) as client:
            resp = await client.post(f"{BACKEND_URL}/api/generate/cancel/{job_id}")
    except httpx.RequestError:
        raise HTTPException(status_code=502, detail={"code": "backend_unavailable"})
    return JSONResponse(status_code=resp.status_code, content=resp.json())


@app.post("/api/generate/continue/{job_id}")
async def proxy_generate_continue(job_id: str):
    """Relaie le bouton "Continuer" de l'interface (voir script.js) vers le
    back — même schéma que les autres routes de ce proxy. Sans cette route
    explicite, une requête POST vers ce chemin tombait dans le `app.mount`
    `StaticFiles` monté en dernier (aucune route déclarée ne correspondait),
    qui ne répond qu'en GET/HEAD — d'où le "Method Not Allowed" (405)
    signalé en direct plutôt qu'un vrai relais vers le back."""
    try:
        async with httpx.AsyncClient(timeout=PROXY_TIMEOUT_S) as client:
            resp = await client.post(f"{BACKEND_URL}/api/generate/continue/{job_id}")
    except httpx.RequestError:
        raise HTTPException(status_code=502, detail={"code": "backend_unavailable"})
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
        async with httpx.AsyncClient(timeout=PROXY_TIMEOUT_S) as client:
            resp = await client.get(f"{BACKEND_URL}/api/health")
    except httpx.RequestError:
        raise HTTPException(status_code=502, detail="Serveur back indisponible.")
    return JSONResponse(status_code=resp.status_code, content=resp.json())


@app.get("/api/system_info")
async def proxy_system_info():
    try:
        async with httpx.AsyncClient(timeout=PROXY_TIMEOUT_S) as client:
            resp = await client.get(f"{BACKEND_URL}/api/system_info")
    except httpx.RequestError:
        raise HTTPException(status_code=502, detail={"code": "backend_unavailable"})
    return JSONResponse(status_code=resp.status_code, content=resp.json())


@app.get("/api/library")
async def proxy_library_list(request: Request):
    """Relaie le bouton "Bibliothèque" de l'interface (voir script.js) vers
    le back — même schéma que les autres routes de ce proxy. La query
    string (`preferred_language`) est transmise telle quelle : sans route
    explicite ici, une requête vers ce chemin tomberait dans le
    `app.mount` `StaticFiles` monté en dernier, exactement le bug déjà
    rencontré une fois pour /api/generate/continue/{job_id} (voir son
    propre commentaire ci-dessus)."""
    try:
        async with httpx.AsyncClient(timeout=PROXY_TIMEOUT_S) as client:
            resp = await client.get(f"{BACKEND_URL}/api/library", params=request.query_params)
    except httpx.RequestError:
        raise HTTPException(status_code=502, detail={"code": "backend_unavailable"})
    return JSONResponse(status_code=resp.status_code, content=resp.json())


@app.get("/api/library/{grid_id}")
async def proxy_library_get(grid_id: str):
    """Relaie le chargement d'une grille de la bibliothèque (voir
    script.js) vers le back — même schéma que les autres routes de ce
    proxy."""
    try:
        async with httpx.AsyncClient(timeout=PROXY_TIMEOUT_S) as client:
            resp = await client.get(f"{BACKEND_URL}/api/library/{grid_id}")
    except httpx.RequestError:
        raise HTTPException(status_code=502, detail={"code": "backend_unavailable"})
    return JSONResponse(status_code=resp.status_code, content=resp.json())


# Montée en dernier : les routes /api/* déclarées ci-dessus restent prioritaires,
# tout le reste est résolu dans static/ (404 si le fichier n'y existe pas).
app.mount("/", StaticFiles(directory=STATIC_DIR, html=True), name="static")
