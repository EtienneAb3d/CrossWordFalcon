#!/usr/bin/env python3
"""
Middleware server: serves the crossword generator's HTML+JS page and
relays /api/* calls to the backend server (backend/app.py). The browser
only ever talks to this server — no CORS, no direct exposure of the
backend.

Only the files under the `static/` folder (HTML, JS, CSS...) and the
/api/* routes are served: any other request gets a 404 (StaticFiles'
own default behavior for a missing file, and FastAPI's for an unknown
route).

Usage:
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

# Maximum delay granted to a proxy call to the backend before giving up and
# returning a 502 to the browser — at the user's explicit request, after a
# report of sporadic 502s on /api/generate/status with no matching trace at
# all in the backend's own log (see CLAUDE.md / the project-best-practices
# SKILL): nothing in the application log means the connection never reached
# the FastAPI layer at all, which points either to a backend process restart
# or to its event loop being occasionally too busy to accept a new
# connection in time (a generation can launch up to PARALLEL_ATTEMPTS CSP
# processes in parallel — see crossword_gen.py) — raised to 30s (from a
# previous 10s/5s split depending on the endpoint) to leave margin either
# way, one shared value for every proxy call rather than different delays
# with no clear reason. See also FETCH_TIMEOUT_MS in
# frontend/static/script.js, which must stay strictly greater so it never
# times out on the browser side before this delay does on the proxy side.
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
    """Relays the "Actu Croisée" panel on the home page (see
    backend/app.py/script.js) to the back end — same pattern as this
    proxy's other simple GET routes."""
    try:
        async with httpx.AsyncClient(timeout=PROXY_TIMEOUT_S) as client:
            resp = await client.get(f"{BACKEND_URL}/api/rss")
    except httpx.RequestError:
        raise HTTPException(status_code=502, detail={"code": "backend_unavailable"})
    return JSONResponse(status_code=resp.status_code, content=resp.json())


@app.post("/api/presence")
async def proxy_presence(request: Request):
    """Relays the "x online" heartbeat (every 2s) to the back end — see
    backend/app.py's POST /api/presence."""
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


@app.post("/api/pseudo/claim")
async def proxy_pseudo_claim(request: Request):
    """Relays the pseudo/secret-word verification/claim submitted when
    the welcome overlay closes to the back end — see backend/app.py's
    POST /api/pseudo/claim and backend/secret_store.py."""
    body = await request.body()
    try:
        async with httpx.AsyncClient(timeout=PROXY_TIMEOUT_S) as client:
            resp = await client.post(
                f"{BACKEND_URL}/api/pseudo/claim", content=body,
                headers={"content-type": "application/json"},
            )
    except httpx.RequestError:
        raise HTTPException(status_code=502, detail={"code": "backend_unavailable"})
    return JSONResponse(status_code=resp.status_code, content=resp.json())


@app.get("/api/scrapp")
async def proxy_scrapp():
    """Exact mirror of proxy_rss above, for the grid-link aggregation
    (SCRAPP/, see scrapper/fetch_grid_links.py) — the same "Actu Croisée"
    panel merges entries from both sources into a single journal."""
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
    """Relays the UI's "Stop" button (see script.js) to the back end —
    same pattern as this proxy's other routes."""
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
    reply is the whole point of this endpoint, and buffering it here
    would silently defeat that the moment it crosses this proxy hop.
    Still uses CHAT_PROXY_TIMEOUT_S (see its own comment) as the
    connection's own overall timeout.

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
    """Relays the UI's "Continuer" button (see script.js) to the back
    end — same pattern as this proxy's other routes. Without this
    explicit route, a POST request to this path used to fall through to
    the `app.mount` `StaticFiles` mounted at the very end (no declared
    route matched it), which only ever answers GET/HEAD — hence a
    reported "Method Not Allowed" (405) instead of a real relay to the
    back end."""
    try:
        async with httpx.AsyncClient(timeout=PROXY_TIMEOUT_S) as client:
            resp = await client.post(f"{BACKEND_URL}/api/generate/continue/{job_id}")
    except httpx.RequestError:
        raise HTTPException(status_code=502, detail={"code": "backend_unavailable"})
    return JSONResponse(status_code=resp.status_code, content=resp.json())


@app.post("/api/recompute")
async def proxy_recompute(request: Request):
    """Relays the UI's "Recalculer" button (see script.js) to the back
    end — same pattern as proxy_generate: the back end answers
    immediately with a job_id, which the browser then polls via
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
    """Relays the UI's "Bibliothèque" button (see script.js) to the back
    end — same pattern as this proxy's other routes. The query string
    (`preferred_language`) is passed through verbatim: without an
    explicit route here, a request to this path would fall through to
    the `app.mount` `StaticFiles` mounted at the very end — the exact
    same bug already hit once for /api/generate/continue/{job_id} (see
    its own comment above)."""
    try:
        async with httpx.AsyncClient(timeout=PROXY_TIMEOUT_S) as client:
            resp = await client.get(f"{BACKEND_URL}/api/library", params=request.query_params)
    except httpx.RequestError:
        raise HTTPException(status_code=502, detail={"code": "backend_unavailable"})
    return JSONResponse(status_code=resp.status_code, content=resp.json())


@app.post("/api/library")
async def proxy_library_list_filtered(request: Request):
    """POST variant: the JSON body carries `seen_filter` + `seen_ids`
    (the grids this client has already seen — see script.js, which keeps
    the set in localStorage) so the back end can filter/annotate the
    list. Body relayed verbatim."""
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
async def proxy_library_get(grid_id: str, request: Request):
    """Relays loading a library grid (see script.js) to the back end —
    same pattern as this proxy's other routes. The query string
    (`pseudo`, see backend/app.py's library_get — looks up an already-
    saved game state in GRID_GAME) is passed through verbatim."""
    try:
        async with httpx.AsyncClient(timeout=PROXY_TIMEOUT_S) as client:
            resp = await client.get(
                f"{BACKEND_URL}/api/library/{grid_id}", params=request.query_params
            )
    except httpx.RequestError:
        raise HTTPException(status_code=502, detail={"code": "backend_unavailable"})
    return JSONResponse(status_code=resp.status_code, content=resp.json())


@app.get("/api/library/{grid_id}/pdf")
async def proxy_library_get_pdf(grid_id: str):
    """Relays downloading a library grid's PDF (empty grid + clues +
    title, no answers — see backend/app.py's library_get_pdf). A binary
    passthrough: returns the PDF bytes as-is with the back end's own
    Content-Disposition; a back-end failure (JSON) is relayed as JSON."""
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


@app.post("/api/game/save")
async def proxy_game_save(request: Request):
    """Relays the current game's autosave (see script.js's
    scheduleGridGameSave, backend/app.py's game_save) to the back end —
    JSON body relayed verbatim, same pattern as proxy_recompute."""
    body = await request.body()
    try:
        async with httpx.AsyncClient(timeout=PROXY_TIMEOUT_S) as client:
            resp = await client.post(
                f"{BACKEND_URL}/api/game/save",
                content=body,
                headers={"Content-Type": "application/json"},
            )
    except httpx.RequestError:
        raise HTTPException(status_code=502, detail={"code": "backend_unavailable"})
    return JSONResponse(status_code=resp.status_code, content=resp.json())


@app.get("/api/dictionary")
async def proxy_dictionary(request: Request):
    """Relays the UI's "Dictionnaire" button (see script.js) to the back
    end — the query string (`q`, `lang`) passed through verbatim, same
    pattern as proxy_library_list."""
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
    """Relays the Dictionary panel's "Définir" button (see script.js) to
    the back end — query string (`q`, `lang`) passed through verbatim,
    same pattern as proxy_dictionary, but with DEFINE_PROXY_TIMEOUT_S
    (see its own note) instead of PROXY_TIMEOUT_S."""
    try:
        async with httpx.AsyncClient(timeout=DEFINE_PROXY_TIMEOUT_S) as client:
            resp = await client.get(f"{BACKEND_URL}/api/dictionary/define", params=request.query_params)
    except httpx.RequestError:
        raise HTTPException(status_code=502, detail={"code": "backend_unavailable"})
    return JSONResponse(status_code=resp.status_code, content=resp.json())


SIMILAR_PROXY_TIMEOUT_S = 60.0


@app.get("/api/similar_words")
async def proxy_similar_words(request: Request):
    """Relays the Dictionary panel's "Thématique" button (see script.js)
    to the back end — query string (`q`, `lang`, `min_score`) passed
    through verbatim. Widened timeout (SIMILAR_PROXY_TIMEOUT_S): the back
    end now runs an LLM expansion of the term before the Qdrant searches
    (see _similar_words_impl), so the call is no longer "quick or 503"
    the way it used to be — same pattern as proxy_define."""
    try:
        async with httpx.AsyncClient(timeout=SIMILAR_PROXY_TIMEOUT_S) as client:
            resp = await client.get(f"{BACKEND_URL}/api/similar_words", params=request.query_params)
    except httpx.RequestError:
        raise HTTPException(status_code=502, detail={"code": "backend_unavailable"})
    return JSONResponse(status_code=resp.status_code, content=resp.json())


@app.get("/api/synonyms")
async def proxy_synonyms(request: Request):
    """Relays the Dictionary panel's "Synonymes" button — a direct
    Qdrant search, no LLM call (see backend/app.py's `_synonyms_impl`),
    so the generic timeout (PROXY_TIMEOUT_S) is enough — no need for
    "Thématique"'s own widened one."""
    try:
        async with httpx.AsyncClient(timeout=PROXY_TIMEOUT_S) as client:
            resp = await client.get(f"{BACKEND_URL}/api/synonyms", params=request.query_params)
    except httpx.RequestError:
        raise HTTPException(status_code=502, detail={"code": "backend_unavailable"})
    return JSONResponse(status_code=resp.status_code, content=resp.json())


# "Paraphraser" (backend/clues.py's LLMClueGenerator.generate_paraphrases)
# makes one real LLM round-trip asking for several paraphrases at once —
# same "one meaningfully heavier single call" reasoning as DEFINE_PROXY_
# TIMEOUT_S above, set above generate_paraphrases()'s own 90s default.
PARAPHRASE_PROXY_TIMEOUT_S = 100.0


@app.get("/api/paraphrase")
async def proxy_paraphrase(request: Request):
    """Relays the "Paraphraseur" panel's "Paraphraser" button (see
    script.js) to the back end — query string (`q`, `lang`) passed
    through verbatim, same pattern as proxy_dictionary_define."""
    try:
        async with httpx.AsyncClient(timeout=PARAPHRASE_PROXY_TIMEOUT_S) as client:
            resp = await client.get(f"{BACKEND_URL}/api/paraphrase", params=request.query_params)
    except httpx.RequestError:
        raise HTTPException(status_code=502, detail={"code": "backend_unavailable"})
    return JSONResponse(status_code=resp.status_code, content=resp.json())


# GET /api/theme/random makes one LLM round-trip (LLMClueGenerator.
# generate_random_theme) — same "one meaningfully heavier single call"
# reasoning as PARAPHRASE_PROXY_TIMEOUT_S above.
RANDOM_THEME_PROXY_TIMEOUT_S = 100.0


@app.get("/api/theme/random")
async def proxy_random_theme(request: Request):
    """Relays Automation/Populate.py's random-theme feature (see
    backend/app.py's `random_theme`) to the back end — query string
    (`lang`) passed through verbatim, same pattern as proxy_paraphrase.
    Not called by the browser itself (Populate.py talks to the backend
    directly on its own port), but every backend endpoint gets its own
    proxy route regardless of caller."""
    try:
        async with httpx.AsyncClient(timeout=RANDOM_THEME_PROXY_TIMEOUT_S) as client:
            resp = await client.get(f"{BACKEND_URL}/api/theme/random", params=request.query_params)
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


@app.post("/api/interactive/crossing")
async def proxy_interactive_crossing(request: Request):
    """"Croisés" button of the "Interactif" mode — for the selected cell,
    lists every letter compatible with a real dictionary word in both the
    horizontal and the vertical emplacement crossing there."""
    body = await request.body()
    try:
        async with httpx.AsyncClient(timeout=PROXY_TIMEOUT_S) as client:
            resp = await client.post(
                f"{BACKEND_URL}/api/interactive/crossing",
                content=body,
                headers={"content-type": "application/json"},
            )
    except httpx.RequestError:
        raise HTTPException(status_code=502, detail={"code": "backend_unavailable"})
    return JSONResponse(status_code=resp.status_code, content=resp.json())


@app.post("/api/interactive/impossible")
async def proxy_interactive_impossible(request: Request):
    """"Impossibles" button of the "Interactif" mode — read-only check of
    which slots are impossible (including a complete word unknown to the
    dictionary) or have too few candidates, with no mutation of the grid."""
    body = await request.body()
    try:
        async with httpx.AsyncClient(timeout=PROXY_TIMEOUT_S) as client:
            resp = await client.post(
                f"{BACKEND_URL}/api/interactive/impossible",
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


@app.post("/api/interactive/from-library")
async def proxy_interactive_from_library(request: Request):
    """Library list: opens a finished library grid in "Interactif" mode
    as a brand-new GRID_WORK creation."""
    body = await request.body()
    try:
        async with httpx.AsyncClient(timeout=PROXY_TIMEOUT_S) as client:
            resp = await client.post(
                f"{BACKEND_URL}/api/interactive/from-library",
                content=body,
                headers={"content-type": "application/json"},
            )
    except httpx.RequestError:
        raise HTTPException(status_code=502, detail={"code": "backend_unavailable"})
    return JSONResponse(status_code=resp.status_code, content=resp.json())


@app.post("/api/interactive/finish")
async def proxy_interactive_finish(request: Request):
    """"Finir la grille" button of the "Interactif" authoring mode: locks
    every already-placed letter and starts a brand-new automatic
    generation job (grid completion + missing definitions only)."""
    body = await request.body()
    try:
        async with httpx.AsyncClient(timeout=PROXY_TIMEOUT_S) as client:
            resp = await client.post(
                f"{BACKEND_URL}/api/interactive/finish",
                content=body,
                headers={"content-type": "application/json"},
            )
    except httpx.RequestError:
        raise HTTPException(status_code=502, detail={"code": "backend_unavailable"})
    return JSONResponse(status_code=resp.status_code, content=resp.json())


# Montée en dernier : les routes /api/* déclarées ci-dessus restent prioritaires,
# tout le reste est résolu dans static/ (404 si le fichier n'y existe pas).
app.mount("/", StaticFiles(directory=STATIC_DIR, html=True), name="static")
