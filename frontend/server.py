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
import json
import os
from pathlib import Path
from urllib.parse import urlsplit

import httpx
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse, Response, StreamingResponse
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

# Loopback names accepted by _require_localhost() below. This middleware
# server binds 0.0.0.0 (run_Falcon.sh, LAN-reachable), so `request.client.
# host` genuinely is the real peer IP for a direct connection — a LAN
# client shows its 192.168.x.x / etc. address, not a loopback one. The
# extra Host-header check catches the reverse-proxy case (e.g. Apache
# forwarding a public domain to 127.0.0.1:3443, where the peer IP alone
# would look local): a request whose Host is a real domain is rejected
# even if it reaches us over loopback.
_LOOPBACK_CLIENT_IPS = {"127.0.0.1", "::1"}
_LOOPBACK_HOSTNAMES = {"127.0.0.1", "::1", "localhost"}


def _require_localhost(request: Request) -> None:
    """Raise 403 unless the request genuinely comes from this machine:
    a loopback peer IP AND a loopback Host header. Used to gate the
    Qdrant-admin routes, which are a localhost-only maintenance tool."""
    client_ip = request.client.host if request.client else ""
    hostname = (urlsplit("//" + request.headers.get("host", "")).hostname
                or "").lower()
    ok = client_ip in _LOOPBACK_CLIENT_IPS and (
        hostname in _LOOPBACK_HOSTNAMES or hostname.endswith(".localhost")
    )
    if not ok:
        raise HTTPException(status_code=403, detail={"code": "localhost_only"})


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


@app.get("/api/rss")
async def proxy_rss():
    """Relaie le panneau "Actu Croisée" de la page d'accueil (voir
    backend/app.py/script.js) vers le back — même schéma que les autres
    routes GET simples de ce proxy."""
    try:
        async with httpx.AsyncClient(timeout=PROXY_TIMEOUT_S) as client:
            resp = await client.get(f"{BACKEND_URL}/api/rss")
    except httpx.RequestError:
        raise HTTPException(status_code=502, detail={"code": "backend_unavailable"})
    return JSONResponse(status_code=resp.status_code, content=resp.json())


@app.post("/api/presence")
async def proxy_presence(request: Request):
    """Relaie le battement de cœur "x en ligne" (toutes les 2s) vers le
    back — voir backend/app.py's POST /api/presence."""
    body = await request.body()
    try:
        async with httpx.AsyncClient(timeout=PROXY_TIMEOUT_S) as client:
            resp = await client.post(
                f"{BACKEND_URL}/api/presence", content=body,
                headers={"content-type": "application/json"},
            )
    except httpx.RequestError:
        raise HTTPException(status_code=502, detail={"code": "backend_unavailable"})
    return JSONResponse(status_code=resp.status_code, content=resp.json())


@app.get("/api/scrapp")
async def proxy_scrapp():
    """Miroir exact de proxy_rss ci-dessus, pour l'agrégation de grilles
    (SCRAPP/, voir scrapper/fetch_grid_links.py) — même panneau "Actu Croisée",
    à la demande explicite de l'utilisateur : "Ajoute les entrées de
    SCRAPP aux journal de la première page." """
    try:
        async with httpx.AsyncClient(timeout=PROXY_TIMEOUT_S) as client:
            resp = await client.get(f"{BACKEND_URL}/api/scrapp")
    except httpx.RequestError:
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


@app.get("/api/generate/phase/{job_id}")
async def proxy_generate_phase(job_id: str):
    try:
        async with httpx.AsyncClient(timeout=PROXY_TIMEOUT_S) as client:
            resp = await client.get(f"{BACKEND_URL}/api/generate/phase/{job_id}")
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


# A real chat reply (POST /api/chat below) is a single, synchronous LLM
# call (backend/chatbot.py's own DEFAULT_TIMEOUT, 120s) rather than a
# quick status check — PROXY_TIMEOUT_S (30s) alone would abort the proxy
# request before a genuinely slow model ever got the chance to finish,
# exactly the same "set above the callee's own timeout" reasoning already
# documented for PROXY_TIMEOUT_S itself vs. the backend it forwards to.
CHAT_PROXY_TIMEOUT_S = 150.0


@app.post("/api/chat")
async def proxy_chat(request: Request):
    """"David FALCON" chat widget (see frontend/static/script.js) — relays
    the backend's own streamed `text/event-stream` response chunk by
    chunk as it arrives, rather than buffering the whole reply and
    returning it in one piece like every other route here: streaming the
    reply is the whole point of this endpoint, at the user's explicit
    request ("Le Bot doit afficher la réponse en streaming"), and
    buffering it here would silently defeat that the moment it crosses
    this proxy hop. Still uses CHAT_PROXY_TIMEOUT_S (see its own comment)
    as the connection's own overall timeout.

    Unlike every other route here, a connection failure to the back end
    can't be turned into a clean 502 the usual way: by the time
    StreamingResponse starts iterating this generator, the response's own
    status code (200) has already been committed — there's no way to
    retroactively change it once even one chunk may already be on the
    wire. Instead, a connection failure yields a single `data: {"error":
    "backend_unavailable"}` event, in the exact same shape the backend's
    own `POST /api/chat` already uses for a *stream-side* failure — the
    frontend's own chat-handling code already has to parse this event
    shape for that case regardless, so it degrades to the same handling
    here too, just reached a different way."""
    body = await request.body()

    async def relay():
        try:
            async with httpx.AsyncClient(timeout=CHAT_PROXY_TIMEOUT_S) as client:
                async with client.stream(
                    "POST", f"{BACKEND_URL}/api/chat",
                    content=body,
                    headers={"content-type": "application/json"},
                ) as resp:
                    async for chunk in resp.aiter_bytes():
                        yield chunk
        except httpx.RequestError:
            yield f"data: {json.dumps({'error': 'backend_unavailable'})}\n\n".encode()

    return StreamingResponse(relay(), media_type="text/event-stream")


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


@app.post("/api/recompute")
async def proxy_recompute(request: Request):
    """Relaie le bouton "Recalculer" de l'interface (voir script.js) vers le
    back — même schéma que proxy_generate : le back répond immédiatement
    avec un job_id, que le navigateur sonde ensuite via
    /api/generate/status/{job_id}."""
    body = await request.body()
    try:
        async with httpx.AsyncClient(timeout=PROXY_TIMEOUT_S) as client:
            resp = await client.post(
                f"{BACKEND_URL}/api/recompute",
                content=body,
                headers={"content-type": "application/json"},
            )
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


@app.post("/api/library")
async def proxy_library_list_filtered(request: Request):
    """Variante POST : le corps JSON porte `seen_filter` + `seen_ids` (les
    grilles déjà vues par ce client — voir script.js, qui garde
    l'ensemble en localStorage) pour que le back filtre/annote la liste.
    Corps relayé tel quel."""
    try:
        body = await request.body()
        async with httpx.AsyncClient(timeout=PROXY_TIMEOUT_S) as client:
            resp = await client.post(
                f"{BACKEND_URL}/api/library",
                content=body,
                headers={"Content-Type": "application/json"},
            )
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


@app.get("/api/library/{grid_id}/pdf")
async def proxy_library_get_pdf(grid_id: str):
    """Relaie le téléchargement PDF d'une grille de la bibliothèque (grille
    vide + définitions + titre, sans réponses — voir backend/app.py's
    library_get_pdf). Passe-plat binaire : renvoie les octets PDF tels
    quels avec le Content-Disposition du back ; un échec du back (JSON)
    est relayé en JSON."""
    try:
        async with httpx.AsyncClient(timeout=PROXY_TIMEOUT_S) as client:
            resp = await client.get(f"{BACKEND_URL}/api/library/{grid_id}/pdf")
    except httpx.RequestError:
        raise HTTPException(status_code=502, detail={"code": "backend_unavailable"})
    if resp.status_code != 200:
        try:
            return JSONResponse(status_code=resp.status_code, content=resp.json())
        except ValueError:
            return Response(status_code=resp.status_code, content=resp.content)
    headers = {}
    disposition = resp.headers.get("content-disposition")
    if disposition:
        headers["Content-Disposition"] = disposition
    return Response(
        content=resp.content, media_type="application/pdf", headers=headers
    )


@app.get("/api/dictionary")
async def proxy_dictionary(request: Request):
    """Relaie le bouton "Dictionnaire" de l'interface (voir script.js) vers
    le back — la query string (`q`, `lang`) transmise telle quelle, même
    schéma que proxy_library_list."""
    try:
        async with httpx.AsyncClient(timeout=PROXY_TIMEOUT_S) as client:
            resp = await client.get(f"{BACKEND_URL}/api/dictionary", params=request.query_params)
    except httpx.RequestError:
        raise HTTPException(status_code=502, detail={"code": "backend_unavailable"})
    return JSONResponse(status_code=resp.status_code, content=resp.json())


# "Définir" (backend/clues.py's LLMClueGenerator.generate_definitions) makes
# one real LLM round-trip asking for up to 10 definitions at once — a
# meaningfully heavier single call than a quick status check, measured live
# at ~40s on this project's own small local model. PROXY_TIMEOUT_S (30s)
# alone would abort before it can finish; same "set above the callee's own
# timeout" reasoning already used for CHAT_PROXY_TIMEOUT_S vs. PROXY_
# TIMEOUT_S, here set above generate_definitions()'s own 90s default.
DEFINE_PROXY_TIMEOUT_S = 100.0


@app.get("/api/dictionary/define")
async def proxy_dictionary_define(request: Request):
    """Relaie le bouton "Définir" du panneau Dictionnaire (voir script.js)
    vers le back — query string (`q`, `lang`) transmise telle quelle,
    même schéma que proxy_dictionary, mais avec DEFINE_PROXY_TIMEOUT_S
    (voir sa propre note) au lieu de PROXY_TIMEOUT_S."""
    try:
        async with httpx.AsyncClient(timeout=DEFINE_PROXY_TIMEOUT_S) as client:
            resp = await client.get(f"{BACKEND_URL}/api/dictionary/define", params=request.query_params)
    except httpx.RequestError:
        raise HTTPException(status_code=502, detail={"code": "backend_unavailable"})
    return JSONResponse(status_code=resp.status_code, content=resp.json())


SIMILAR_PROXY_TIMEOUT_S = 60.0


@app.get("/api/similar_words")
async def proxy_similar_words(request: Request):
    """Relaie le bouton "Thématique" du panneau Dictionnaire (voir
    script.js) vers le back — query string (`q`, `lang`, `min_score`)
    transmise telle quelle. Timeout élargi (SIMILAR_PROXY_TIMEOUT_S) :
    le back fait maintenant une expansion LLM du terme avant les
    recherches Qdrant (voir _similar_words_impl), donc l'appel n'est plus
    "rapide ou 503" comme avant — même schéma que proxy_define."""
    try:
        async with httpx.AsyncClient(timeout=SIMILAR_PROXY_TIMEOUT_S) as client:
            resp = await client.get(f"{BACKEND_URL}/api/similar_words", params=request.query_params)
    except httpx.RequestError:
        raise HTTPException(status_code=502, detail={"code": "backend_unavailable"})
    return JSONResponse(status_code=resp.status_code, content=resp.json())


@app.get("/api/synonyms")
async def proxy_synonyms(request: Request):
    """Relaie le bouton "Synonymes" du panneau Dictionnaire — recherche
    Qdrant directe, sans appel LLM (voir backend/app.py's `_synonyms_
    impl`), donc le timeout générique (PROXY_TIMEOUT_S) suffit, pas besoin
    du timeout élargi de "Thématique"."""
    try:
        async with httpx.AsyncClient(timeout=PROXY_TIMEOUT_S) as client:
            resp = await client.get(f"{BACKEND_URL}/api/synonyms", params=request.query_params)
    except httpx.RequestError:
        raise HTTPException(status_code=502, detail={"code": "backend_unavailable"})
    return JSONResponse(status_code=resp.status_code, content=resp.json())


# --- Qdrant admin panel (localhost only) --------------------------------
# _require_localhost() rejects any non-loopback client / Host BEFORE the
# request reaches the back, so the admin panel is unreachable from the LAN
# or via a reverse-proxied public domain even though this server binds
# 0.0.0.0. The frontend also hides the panel's button off localhost.
@app.get("/api/qdrant/admin")
async def proxy_qdrant_admin(request: Request):
    _require_localhost(request)
    try:
        async with httpx.AsyncClient(timeout=PROXY_TIMEOUT_S) as client:
            resp = await client.get(f"{BACKEND_URL}/api/qdrant/admin")
    except httpx.RequestError:
        raise HTTPException(status_code=502, detail={"code": "backend_unavailable"})
    return JSONResponse(status_code=resp.status_code, content=resp.json())


@app.post("/api/qdrant/admin/recreate")
async def proxy_qdrant_admin_recreate(request: Request):
    _require_localhost(request)
    try:
        async with httpx.AsyncClient(timeout=PROXY_TIMEOUT_S) as client:
            resp = await client.post(f"{BACKEND_URL}/api/qdrant/admin/recreate")
    except httpx.RequestError:
        raise HTTPException(status_code=502, detail={"code": "backend_unavailable"})
    return JSONResponse(status_code=resp.status_code, content=resp.json())


@app.post("/api/qdrant/admin/delete-tenant")
async def proxy_qdrant_admin_delete_tenant(request: Request):
    _require_localhost(request)
    body = await request.body()
    try:
        async with httpx.AsyncClient(timeout=PROXY_TIMEOUT_S) as client:
            resp = await client.post(
                f"{BACKEND_URL}/api/qdrant/admin/delete-tenant",
                content=body,
                headers={"content-type": "application/json"},
            )
    except httpx.RequestError:
        raise HTTPException(status_code=502, detail={"code": "backend_unavailable"})
    return JSONResponse(status_code=resp.status_code, content=resp.json())


@app.post("/api/interactive/start")
async def proxy_interactive_start(request: Request):
    """"Interactif" mode: kicks off a one-grid authoring job. Same shape as
    proxy_generate — the back replies with a job_id polled via
    /api/generate/status/{job_id}."""
    body = await request.body()
    try:
        async with httpx.AsyncClient(timeout=PROXY_TIMEOUT_S) as client:
            resp = await client.post(
                f"{BACKEND_URL}/api/interactive/start",
                content=body,
                headers={"content-type": "application/json"},
            )
    except httpx.RequestError:
        raise HTTPException(status_code=502, detail={"code": "backend_unavailable"})
    return JSONResponse(status_code=resp.status_code, content=resp.json())


@app.post("/api/interactive/step")
async def proxy_interactive_step(request: Request):
    """"Suivant" button of the "Interactif" mode — places one more word."""
    body = await request.body()
    try:
        async with httpx.AsyncClient(timeout=PROXY_TIMEOUT_S) as client:
            resp = await client.post(
                f"{BACKEND_URL}/api/interactive/step",
                content=body,
                headers={"content-type": "application/json"},
            )
    except httpx.RequestError:
        raise HTTPException(status_code=502, detail={"code": "backend_unavailable"})
    return JSONResponse(status_code=resp.status_code, content=resp.json())


@app.post("/api/interactive/clean")
async def proxy_interactive_clean(request: Request):
    """"Nettoyer" button of the "Interactif" mode — full cleanup of every
    impossible zone (remove crossing words, or blacken a cell)."""
    body = await request.body()
    try:
        async with httpx.AsyncClient(timeout=PROXY_TIMEOUT_S) as client:
            resp = await client.post(
                f"{BACKEND_URL}/api/interactive/clean",
                content=body,
                headers={"content-type": "application/json"},
            )
    except httpx.RequestError:
        raise HTTPException(status_code=502, detail={"code": "backend_unavailable"})
    return JSONResponse(status_code=resp.status_code, content=resp.json())


@app.post("/api/interactive/candidates")
async def proxy_interactive_candidates(request: Request):
    """"Mots" button of the "Interactif" mode — lists every real
    dictionary word compatible with the selected slot's own letters."""
    body = await request.body()
    try:
        async with httpx.AsyncClient(timeout=PROXY_TIMEOUT_S) as client:
            resp = await client.post(
                f"{BACKEND_URL}/api/interactive/candidates",
                content=body,
                headers={"content-type": "application/json"},
            )
    except httpx.RequestError:
        raise HTTPException(status_code=502, detail={"code": "backend_unavailable"})
    return JSONResponse(status_code=resp.status_code, content=resp.json())


@app.post("/api/interactive/verify")
async def proxy_interactive_verify(request: Request):
    """"Vérifier" button of the "Interactif" mode — checks every complete
    word of the whole grid against the real dictionary in one call."""
    body = await request.body()
    try:
        async with httpx.AsyncClient(timeout=PROXY_TIMEOUT_S) as client:
            resp = await client.post(
                f"{BACKEND_URL}/api/interactive/verify",
                content=body,
                headers={"content-type": "application/json"},
            )
    except httpx.RequestError:
        raise HTTPException(status_code=502, detail={"code": "backend_unavailable"})
    return JSONResponse(status_code=resp.status_code, content=resp.json())


@app.post("/api/interactive/title")
async def proxy_interactive_title(request: Request):
    """"Proposer un titre" button — one blocking LLM call, so it uses the
    longer chat timeout (the 30s default would abort it)."""
    body = await request.body()
    try:
        async with httpx.AsyncClient(timeout=CHAT_PROXY_TIMEOUT_S) as client:
            resp = await client.post(
                f"{BACKEND_URL}/api/interactive/title",
                content=body,
                headers={"content-type": "application/json"},
            )
    except httpx.RequestError:
        raise HTTPException(status_code=502, detail={"code": "backend_unavailable"})
    return JSONResponse(status_code=resp.status_code, content=resp.json())


@app.post("/api/interactive/save")
async def proxy_interactive_save(request: Request):
    """"Sauvegarder" button — stores the authored grid in the library."""
    body = await request.body()
    try:
        async with httpx.AsyncClient(timeout=PROXY_TIMEOUT_S) as client:
            resp = await client.post(
                f"{BACKEND_URL}/api/interactive/save",
                content=body,
                headers={"content-type": "application/json"},
            )
    except httpx.RequestError:
        raise HTTPException(status_code=502, detail={"code": "backend_unavailable"})
    return JSONResponse(status_code=resp.status_code, content=resp.json())


@app.post("/api/interactive/save_work")
async def proxy_interactive_save_work(request: Request):
    """Autosave fired after every "Suivant"/"Précédent" click in the
    "Interactif" mode — see backend/app.py's own entry."""
    body = await request.body()
    try:
        async with httpx.AsyncClient(timeout=PROXY_TIMEOUT_S) as client:
            resp = await client.post(
                f"{BACKEND_URL}/api/interactive/save_work",
                content=body,
                headers={"content-type": "application/json"},
            )
    except httpx.RequestError:
        raise HTTPException(status_code=502, detail={"code": "backend_unavailable"})
    return JSONResponse(status_code=resp.status_code, content=resp.json())


@app.get("/api/interactive/work")
async def proxy_interactive_work_list(request: Request):
    """"Créations" panel's own list — same query-string passthrough
    convention as GET /api/library above."""
    try:
        async with httpx.AsyncClient(timeout=PROXY_TIMEOUT_S) as client:
            resp = await client.get(f"{BACKEND_URL}/api/interactive/work", params=request.query_params)
    except httpx.RequestError:
        raise HTTPException(status_code=502, detail={"code": "backend_unavailable"})
    return JSONResponse(status_code=resp.status_code, content=resp.json())


@app.post("/api/interactive/work/delete")
async def proxy_interactive_work_delete(request: Request):
    """"Créations" panel's own delete-icon button."""
    body = await request.body()
    try:
        async with httpx.AsyncClient(timeout=PROXY_TIMEOUT_S) as client:
            resp = await client.post(
                f"{BACKEND_URL}/api/interactive/work/delete",
                content=body,
                headers={"content-type": "application/json"},
            )
    except httpx.RequestError:
        raise HTTPException(status_code=502, detail={"code": "backend_unavailable"})
    return JSONResponse(status_code=resp.status_code, content=resp.json())


@app.post("/api/interactive/resume")
async def proxy_interactive_resume(request: Request):
    """"Créations" panel: relaunches a saved work-in-progress session."""
    body = await request.body()
    try:
        async with httpx.AsyncClient(timeout=PROXY_TIMEOUT_S) as client:
            resp = await client.post(
                f"{BACKEND_URL}/api/interactive/resume",
                content=body,
                headers={"content-type": "application/json"},
            )
    except httpx.RequestError:
        raise HTTPException(status_code=502, detail={"code": "backend_unavailable"})
    return JSONResponse(status_code=resp.status_code, content=resp.json())


# Montée en dernier : les routes /api/* déclarées ci-dessus restent prioritaires,
# tout le reste est résolu dans static/ (404 si le fichier n'y existe pas).
app.mount("/", StaticFiles(directory=STATIC_DIR, html=True), name="static")
