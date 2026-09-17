#!/usr/bin/env python3
"""
Backend server: exposes the crossword grid generator (crossword_gen.py,
all the generation business logic lives in backend/) via a JSON API. Serves
no static files — only the routes listed below. Any other request gets
FastAPI's default 404 response (the interactive documentation /docs, /redoc
and /openapi.json is disabled: these aren't routes needed for the app to
function).

Asynchronous generation with progress tracking: POST /api/generate does not
block until completion (generating a grid + definitions can take anywhere
from a few seconds to several minutes) — it starts a background job and
responds immediately with a job_id; the client then polls
GET /api/generate/status/{job_id} to follow progress step by step and
retrieve the final result. Every step is also logged to backend.log via the
standard `logging` module (captured by uvicorn -> see run_Falcon.sh).

Usage:
    uvicorn backend.app:app --port 3001
"""
import asyncio
import concurrent.futures
import datetime
import json
import logging
import multiprocessing
import os
import random
import re
import sys
import threading
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, HTTPException
from fastapi.responses import Response, StreamingResponse
from pydantic import BaseModel, Field

from .chatbot import ChatBot, ChatError
from .clues import (
    ClueGenerationError, LLMClueGenerator, TITLE_PROPOSALS_COUNT,
    _LANGUAGE_STOPWORDS_RAW,
)
from .dictionary_lookup import search as dictionary_search_impl
from .embedder import Embedder, EmbedderError
from .qdrant_store import QdrantStore, QdrantStoreError
from .secret_store import verify_or_claim as verify_or_claim_pseudo_secret
from .crossword_gen import (
    DEFAULT_HEIGHT, DEFAULT_WIDTH, DIFFICULTY_PRESETS, GenerationCancelled, GenerationPaused,
    PREFILL_MIN_WORD_COUNT, DualIndex, DualSet, build_index, build_letters_grid,
    build_word_entries, challenge_word_grid_form, extract_slots, generate_grid, slot_direction,
    _interactive_fill_diagnostics, _interactive_letter_stats,
    _serialize_resume_state, interactive_boundary_candidates, interactive_clean_impossible_zones,
    interactive_crossing_words, interactive_minimize_black_cells,
    interactive_place_word, interactive_slot_candidates, load_wordlist, make_pattern,
)
from .grid_store import (
    _slugify_title, get_grid, list_grids, save_grid_json,
    save_grid_work, list_grid_work, get_grid_work, delete_grid_work,
    save_grid_game, get_grid_game,
)
from .svg_export import (
    render_puzzle_svg,
    save_grid_png,
    save_grid_svg,
    svg_to_pdf_bytes,
)
from .system_info import get_system_info, sample_resource_usage

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("crosswordfalcon")

clue_generator = LLMClueGenerator()
chatbot = ChatBot()

# Optional SECOND local LLM instance, dedicated to interactive/on-demand
# requests, on a second GPU — at the user's explicit request: "All automatic
# generation requests, coming from Populate or from the UI, are assigned to
# the first card. All interactive requests (the interactive edit UI, ChatBot,
# Dictionary, Paraphraser, etc.) are assigned to the second card." `clue_generator`/
# `chatbot` above (reading the plain LLM_BASE_URL/LLM_MODEL/LLM_API_KEY —
# see env.sh) always drive the AUTOMATIC path: _run_generate_job (full-
# grid CSP fill + clue/title writing, whether started from the web UI's
# own "Générer la grille" form, Automation/Populate.py, or Interactive
# mode's own "Finir la grille" button, which hands off to this exact same
# job) and _run_recompute_job ("Recalculer"). `interactive_clue_generator`/
# `interactive_chatbot` below drive every INTERACTIVE call instead: the
# Interactive/Edition mode's own theme-glossary build (_run_interactive_job,
# never _run_interactive_resume_job — see its own docstring for why a
# themed grid's own resume path never recomputes one) and "Proposer un
# titre" (interactive_title), the Dictionary panel's "Définir"/
# "Thématique" (dictionary_define/_similar_words_impl — "Synonymes" is a
# pure Qdrant lookup, no LLM call at all, see _synonyms_impl), the
# Paraphraseur panel (paraphrase), and the ChatBot (chat).
#
# See env.sh/env_default.sh's own "Dual-GPU LLM" section and run_llm.sh/
# run_sglang.sh (LLM_INTERACTIVE_GPU_INDEX) for how the second server
# process itself gets launched, bound to the second card. Deliberately a
# plain runtime env-var check here, not a second hardcoded singleton pair:
# LLM_BASE_URL_INTERACTIVE is only ever set (by Install.sh, or by hand in
# env.sh) once a genuine second GPU/second server is actually configured
# — left unset (the default, every single-GPU machine), this whole block
# degrades to `interactive_clue_generator is clue_generator` and
# `interactive_chatbot is chatbot`, i.e. every interactive call keeps
# sharing the one automatic instance exactly as it always has, with zero
# behavior change for a machine that never opts into this feature.
_interactive_llm_base_url = os.environ.get("LLM_BASE_URL_INTERACTIVE", "").strip()
if _interactive_llm_base_url and _interactive_llm_base_url != clue_generator.base_url:
    _interactive_llm_model = os.environ.get("LLM_MODEL_INTERACTIVE", "").strip() or clue_generator.model
    _interactive_llm_api_key = os.environ.get("LLM_API_KEY_INTERACTIVE", "").strip() or clue_generator.api_key
    interactive_clue_generator = LLMClueGenerator(
        base_url=_interactive_llm_base_url, model=_interactive_llm_model, api_key=_interactive_llm_api_key,
    )
    interactive_chatbot = ChatBot(
        base_url=_interactive_llm_base_url, model=_interactive_llm_model, api_key=_interactive_llm_api_key,
    )
    logger.info(
        "Second LLM instance for interactive requests: base_url=%s model=%s",
        _interactive_llm_base_url, _interactive_llm_model,
    )
else:
    interactive_clue_generator = clue_generator
    interactive_chatbot = chatbot

# "Thématique" button in the Dictionary panel (see frontend/static/
# script.js and GET /api/similar_words): mirrors themed-grid glossary
# construction — the typed expression is first expanded by the LLM
# (`describe_theme`) into a keyword list, split into individual keywords,
# and EACH keyword runs its own Qdrant nearest-words search; the results
# are merged (best score per word). Every word kept has a similarity
# >= the `min_score` query param — the current value of the form's
# "Précision thématique" field, so the panel reacts to it live
# (THEME_MIN_SCORE is only the fallback when the field is blank) —
# restricted to the panel's language tenant, most-similar-first. Both
# Qdrant/embed clients are lazy (no HTTP connection until first use), so
# importing this with Qdrant / the embed server down is harmless — the
# endpoint just returns a clean 503. The Qdrant/embed timeouts stay short
# (10s each — a per-keyword search pages the ranking only until the score
# drops below the threshold, fast around THEME_MIN_SCORE = 0.76), but the
# added LLM expansion makes the whole call slower than before, so the
# frontend/proxy timeouts for this route are widened (see
# SIMILAR_*_TIMEOUT in script.js / frontend/server.py, and
# _SIMILAR_DESCRIBE_TIMEOUT_S below).
_SIMILAR_TIMEOUT_S = 10.0
# Timeout for the describe_theme LLM expansion inside _similar_words_impl
# — generous (the local model can be slow) but far below describe_theme's
# own 300s default, which would let a stuck call hang the panel.
_SIMILAR_DESCRIBE_TIMEOUT_S = 45.0
_similar_qdrant = QdrantStore(timeout=_SIMILAR_TIMEOUT_S)
_similar_embedder = Embedder(timeout=_SIMILAR_TIMEOUT_S)

# The scraper scripts live in the `scrapper/` package at the project root
# (moved there at the user's explicit request, together with data_builder/
# for the dictionary-building scripts), not in backend/ itself. The project
# root is added to sys.path so that `from scrapper import ...` resolves
# regardless of the launch directory, rather than duplicating their
# scraping logic here: at the user's explicit request, "Configure a daemon
# that reads all these RSS feeds once a day... and saves each RSS feed in
# an RSS folder."
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))
from scrapper import fetch_rss_feeds  # noqa: E402  (import after the sys.path manipulation, deliberate)
from scrapper import fetch_grid_links  # noqa: E402  (same reason)

RSS_DIR = _PROJECT_ROOT / "RSS"
SCRAPP_DIR = _PROJECT_ROOT / "SCRAPP"
# Local time (24h) at which the feed is refreshed every day, at the user's
# explicit request: "once a day (e.g. in the morning at 8am)."
RSS_FETCH_HOUR = 8

# Log of the "David FALCON" chatbot's conversations, at the user's explicit
# request: "For each conversation in the ChatBot, create a LOG of the
# questions/answers in a LOG_CHAT folder. Each log is prefixed by a
# timestamp so the files can be seen in chronological order. One file per
# user session." Folder at the project root, gitignored — a generated log,
# not source content, the same convention as LOG_LLM/ (backend/clues.py)
# for the definitions' own LLM call logs.
CHAT_LOG_DIR = _PROJECT_ROOT / "LOG_CHAT"

# Log of the theme pre-search (see THEME_LENGTH_MIN/MAX/
# THEME_MIN_SCORE and _run_generate_job): one
# `LOG_THEME/<timestamp>_<short_id>.log` file per themed generation, the
# name prefixed with a full timestamp (like LOG_LLM/), at the user's
# explicit request ("Show the LLM response on the output file's first
# line"; "Prefix the LOG_THEME saves with a timestamp, like LOG_LLM").
# First line = the ~50-word sentence produced by the LLM (or the raw theme
# if the LLM call failed), then the typed theme and the full glossary of
# words preselected by Qdrant, one word per line. Best-effort: a write
# failure is logged but never interrupts the generation.
# Project root, gitignored — a generated log, not source content, the same
# convention as LOG_CHAT/ / LOG_USERS/ / LOG_LLM/.
THEME_LOG_DIR = _PROJECT_ROOT / "LOG_THEME"

# "chat debug" option (CHATBOT_DEBUG in env.sh/env_default.sh) — when on,
# _append_chat_log also writes the COMPLETE prompt actually sent to the
# LLM (the full messages array: system prompt + whole conversation
# history + the current question) into this session's LOG_CHAT/*.md
# file, inside a collapsible <details> block above each reply, for
# analysis. Off unless the value is one of 1/true/yes/on (case-
# insensitive) — so CHATBOT_DEBUG=0 stays off.
CHATBOT_DEBUG = os.environ.get("CHATBOT_DEBUG", "").strip().lower() in ("1", "true", "yes", "on")

# Red "experimental site" notice on the welcome panel (Pseudo/Mot secret),
# at the user's explicit request, after this instance's own public-facing
# deployment turned out to be experimental rather than a stable release:
# warns a first-time visitor that features may be temporarily broken.
# Defaults ON (the safe failure mode for a warning: an unset/misconfigured
# env var must never silently hide it) — a stable deployment opts out
# explicitly via CROSSWORDFALCON_EXPERIMENTAL_NOTICE=0 in env.sh. See
# GET /api/system_info's own `experimental_notice` field.
EXPERIMENTAL_NOTICE = os.environ.get(
    "CROSSWORDFALCON_EXPERIMENTAL_NOTICE", "1"
).strip().lower() not in ("0", "false", "no", "off")
# session_id (supplied by the frontend, see ChatRequest) -> path of this
# session's log file, already created. An in-memory dict, like
# JOBS/CANCEL_EVENTS above — a single uvicorn process, no --workers (see
# run_Falcon.sh), so no lock or external store is needed. The TIMESTAMP in
# the filename is that of this session's very FIRST message (computed once,
# here, never recomputed) — this is what lets the files be seen in
# chronological order of each session's start, every later turn of the
# same session simply being appended to the same file.
_CHAT_LOG_PATHS = {}


def _chat_log_path_for_session(session_id):
    """Returns the log file path for this session, creating it
    (and registering it in `_CHAT_LOG_PATHS`) on the very first call for
    this `session_id`. A missing/empty `session_id` (a call predating this
    feature, or a client that doesn't supply one) gets a fallback
    identifier generated here (`uuid.uuid4()`) — never silently
    dropped, this conversation is still logged, just with no link
    to a specific frontend session."""
    if not session_id:
        session_id = f"sans-session-{uuid.uuid4().hex[:8]}"
    if session_id not in _CHAT_LOG_PATHS:
        CHAT_LOG_DIR.mkdir(parents=True, exist_ok=True)
        timestamp = time.strftime("%Y%m%d-%H%M%S")
        # session_id can contain characters unsafe for a filename (the
        # frontend can send any string) — only alphanumeric/dash/underscore
        # characters are kept.
        safe_id = "".join(c if c.isalnum() or c in "-_" else "_" for c in session_id)[:64]
        _CHAT_LOG_PATHS[session_id] = CHAT_LOG_DIR / f"{timestamp}_{safe_id}.md"
    return _CHAT_LOG_PATHS[session_id]


def _format_prompt_messages(messages):
    """Renders the exact `messages` array sent to the LLM as one plain
    text block, each message delimited by a header line naming its
    1-based index and role, for the "chat debug" full-prompt trace (see
    `CHATBOT_DEBUG`). Wrapped in a 6-tilde fence by the caller so the
    system prompt's own Markdown/backticks/`~~~` runs never break out."""
    parts = []
    for i, m in enumerate(messages, 1):
        role = m.get("role", "?")
        parts.append(f"========== [{i}/{len(messages)}] role={role} ==========\n{m.get('content', '')}")
    return "\n\n".join(parts)


def _append_chat_log(session_id, language, message, reply, first_token_s=None, total_s=None,
                     prompt_messages=None):
    """Appends one conversation turn (question + full reply) to
    this session's log file — best-effort, like every other log write in
    this project (SVG/PNG, LOG_LLM/): a write failure is logged but must
    never make the response to the player fail.

    `first_token_s`/`total_s` (both `None` by default, for a hypothetical
    future caller that doesn't measure timing) — at the user's explicit
    request: "Dans les LOG_CHAT, en dessous de chaque réponse, noter le
    temps de récupération du premier mot, et le temps total de génération
    de la réponse." Written as one small italic line right under the
    reply, before the closing `---` separator. `first_token_s` can be
    `None` even when `total_s` isn't (a call failed before ever streaming
    a single chunk — see `chat()`'s own `ChatError` branch) — in that
    case only the total-time bit is written, never a fabricated "0s".

    `prompt_messages` (the full messages array actually sent to the LLM)
    is only ever passed when the "chat debug" option is on (`CHATBOT_
    DEBUG`, see its own comment) — written inside a collapsible
    `<details>` block right under the question, so the whole prompt
    (system prompt + history + question) is available for analysis
    without burying the readable Q/A."""
    path = _chat_log_path_for_session(session_id)
    try:
        with path.open("a", encoding="utf-8") as f:
            f.write(f"## {time.strftime('%Y-%m-%d %H:%M:%S')} ({language})\n\n")
            f.write(f"**Utilisateur** : {message}\n\n")
            if prompt_messages:
                total_chars = sum(len(m.get("content", "")) for m in prompt_messages)
                f.write(
                    f"<details>\n<summary>Prompt complet envoyé au LLM — "
                    f"{len(prompt_messages)} messages, {total_chars} caractères</summary>\n\n"
                    f"~~~~~~\n{_format_prompt_messages(prompt_messages)}\n~~~~~~\n\n</details>\n\n"
                )
            f.write(f"**David FALCON** : {reply}\n\n")
            if first_token_s is not None or total_s is not None:
                bits = []
                if first_token_s is not None:
                    bits.append(f"premier mot reçu après {first_token_s:.2f}s")
                if total_s is not None:
                    bits.append(f"temps total : {total_s:.2f}s")
                f.write(f"*{' — '.join(bits)}*\n\n")
            f.write("---\n\n")
    except OSError:
        logger.exception("chat: echec d'ecriture du log pour la session %s", session_id)

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
    "pt": DATA_DIR / "wordlist_pt_full.tsv",
}

# "Mode" selector of the web UI (see frontend/static/index.html), at the
# user's explicit request: "Flash/1000 Turbo/10000
# Fast/100000 Medium/500000 Ultra/5000000" — directly sets the
# search budget per attempt (`crossword_gen.try_fill`'s `deadline_checks`,
# see its own docstring for where this parameter comes from), unrelated to
# the grid size, unlike the default formula (width ×
# height × 2000) which a mode chosen here entirely replaces for this
# request. Internal key in English, like every other value sent by
# the frontend (see "difficulty") — only the displayed label is translated per
# language (see frontend/static/i18n.js's modeLabel*).
BUDGET_MODES = {
    "flash": 1_000,
    "turbo": 10_000,
    "fast": 100_000,
    "medium": 500_000,
    "ultra": 5_000_000,
}

# Optional "Theme" field on the generation form, at the user's explicit
# request: if the theme's word list is non-empty, a Qdrant vector
# pre-search builds a glossary of words close to the theme, which the CSP
# solver then tries in priority for every slot (see crossword_gen.py's
# `generate_grid`'s `priority_words` / `Filler._backtrack`). Best-effort:
# if Qdrant or the embedding server is unavailable, or the
# collection hasn't been populated yet for that language, the
# generation simply proceeds without a theme.
#
# The LLM's own theme description (describe_theme) is a telegraphic list
# of about 30 comma-separated keywords. It is NOT embedded as-is: it is
# split into individual keywords
# (_split_keywords), and EACH keyword runs its own Qdrant nearest-
# neighbor search, embedded bare (no surrounding context) — a much
# sharper query vector than a single embedding averaged over ~30 words,
# and undiluted by whatever other keywords the same theme produced.
# _compiled_theme_words_by_length then merges every result (best score
# per word). Each search is done PER LENGTH
# (THEME_LENGTH_MIN..THEME_LENGTH_MAX letters) — rather than a plain
# global top-N (the old THEME_PRESEARCH_LIMIT), which could leave an
# entire slot length with no theme word at all if the nearest
# neighbors happened to fall mostly at other lengths. There is no
# per-length cap at all (see THEME_MIN_SCORE below): ALL
# words whose score exceeds this threshold are kept. See
# _theme_words_by_length.
THEME_LENGTH_MIN = 3
THEME_LENGTH_MAX = 15
# Size of each Qdrant page read while iterating (see _theme_words_by_length):
# large enough to amortize the network round trip, small enough to
# stop early once the score threshold is crossed.
THEME_LENGTH_SEARCH_PAGE = 1000
# There is NO depth/word-count cap at all, at the user's explicit
# request: "Do not limit the number of words returned by the Thematic
# dictionary. Trust the threshold." _theme_words_by_length paginates
# until the score drops below THEME_MIN_SCORE (a guaranteed, fast stop
# since Qdrant results are ranked by decreasing score) or the tenant is
# exhausted.
# COMMON Qdrant score threshold (cosine similarity — vectors are
# normalized, see backend/embedder.py): the single variable that governs
# BOTH the per-length glossary construction (_theme_words_by_
# length) AND every per-keyword theme search that calls it
# (_compiled_theme_words_by_length). At the user's explicit request:
# "list every word with an identical threshold to the glossary
# construction (name the shared variable)". Since Qdrant results are
# already ranked by decreasing score, as soon as a score below this
# threshold is encountered, every following one (both in the current page AND
# any later page) is too — _theme_words_by_length therefore
# stops iterating entirely, not just accepting, from that point on.
#
# This is the DEFAULT VALUE: the "Précision thématique" field on the
# generation form (right after "Mode", at the user's explicit
# request) lets it be tuned by hand for a given generation
# (GenerateRequest.theme_precision -> `min_score` parameter of
# _compiled_theme_words_by_length / _theme_words_by_length). The
# Dictionary panel's "Thématique" button, meanwhile, always uses this
# constant.
THEME_MIN_SCORE = 0.76

# Grid glossary construction ONLY (not the Dictionary panel's
# "Thématique" button), at the user's explicit request: "raise the LLM's
# temperature to 0.9, iterate at most
# 3 times to try to get 300 words to search in Qdrant." After
# the first pass (whole theme + one call per word for a
# multi-word theme), if the deduplicated set of keywords to search has
# fewer than THEME_MIN_KEYWORDS entries, _run_generate_job re-runs
# describe_theme (with THEME_KEYWORD_LLM_TEMPERATURE = 0.9 to maximize
# variety) up to THEME_KEYWORD_LLM_MAX_LOOPS more times,
# stopping as soon as a call brings in no new keyword. The
# 0.9 temperature is also used for the first pass. An
# indicative target, not a guarantee.
THEME_MIN_KEYWORDS = 300
THEME_KEYWORD_LLM_MAX_LOOPS = 3
THEME_KEYWORD_LLM_TEMPERATURE = 0.9

app = FastAPI(title="CrossWordFalcon API", docs_url=None, redoc_url=None, openapi_url=None)


async def _rss_daily_scheduler():
    """Runs as a background task for the whole lifetime of the process: refreshes
    the RSS feeds (fetch_rss_feeds.fetch_all) once a day, at
    RSS_FETCH_HOUR (8am by default, local time) — at the user's explicit
    request. A plain `asyncio.sleep` until the next 8am rather than
    a real system scheduler (cron/launchd): this project has never
    had any system-service infrastructure, everything already runs as a
    manually-launched Python process (see run_Falcon.sh) — this mechanism
    therefore only refreshes as long as the backend is running, which already
    matches this project's operational reality (no feature
    expects to keep running with the server off).

    `fetch_all()` is blocking (synchronous httpx) — run via
    `asyncio.to_thread`, like every other blocking call in this file
    (grid generation, LLM calls), so it never freezes the FastAPI
    event loop while the feeds download.

    Never raises an exception to the caller: a download/write error
    is already handled inside `fetch_all()`
    itself (best-effort per feed); any unexpected error here is
    only logged, never left to interrupt the loop — a
    refresh failure on a given day must never prevent the
    following ones.

    Also refreshes `fetch_grid_links.fetch_all()` (the grid
    aggregation/SCRAPP, see that module), in the same daily tick, at the
    user's explicit request: "Reproduce the aggregation the
    site above does, to fetch links and descriptions once
    a day (like the RSS feeds)." A `try/except` specific to each of the
    two calls (factored into _refresh_rss/_refresh_scrapp below,
    shared with _rss_startup_catchup) — a failure of one must never
    prevent the other from running that same day, exactly the same principle
    already applied internally to each RSS feed taken individually.

    This mechanism itself has NO catch-up of its own: if it doesn't run
    exactly at RSS_FETCH_HOUR on a given day (e.g. a process restart
    happening between the two calls, a real incident observed on
    2026-09-11 where SCRAPP had stayed dated to the previous day despite RSS
    being freshly up to date), nothing retries before the next day's
    tick — see _rss_startup_catchup, which fills exactly this gap at
    process startup."""
    while True:
        now = datetime.datetime.now()
        next_run = now.replace(hour=RSS_FETCH_HOUR, minute=0, second=0, microsecond=0)
        if next_run <= now:
            next_run += datetime.timedelta(days=1)
        await asyncio.sleep((next_run - now).total_seconds())
        await _refresh_rss()
        await _refresh_scrapp()


async def _refresh_rss():
    """A single call to fetch_rss_feeds.fetch_all(), logged in every
    case — factored out so it can be shared between the daily tick above
    and the startup catch-up below (_rss_startup_catchup), so
    the two can never diverge in how they report
    success/failure."""
    try:
        items = await asyncio.to_thread(fetch_rss_feeds.fetch_all)
        logger.info("rss: %d articles rafraichis", len(items))
    except Exception:
        logger.exception("rss: echec du rafraichissement")


async def _refresh_scrapp():
    """Same role as _refresh_rss above, for fetch_grid_links.
    fetch_all()."""
    try:
        grids = await asyncio.to_thread(fetch_grid_links.fetch_all)
        logger.info("scrapp: %d grilles rafraichies", len(grids) if grids is not None else 0)
    except Exception:
        logger.exception("scrapp: echec du rafraichissement")


def _combined_json_is_fresh(path):
    """True if `path` (RSS/combined.json or SCRAPP/combined.json) exists
    and its own `fetched_at` is dated today (local date) —
    used by _rss_startup_catchup below. False in every other
    case (file missing, unreadable, or dated an earlier day), without
    ever raising — a corrupt/missing file should simply trigger
    a refresh, not crash the server's startup."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        fetched_at = datetime.datetime.fromisoformat(data["fetched_at"])
        if fetched_at.tzinfo is not None:
            fetched_at = fetched_at.astimezone().replace(tzinfo=None)
        return fetched_at.date() == datetime.datetime.now().date()
    except (OSError, json.JSONDecodeError, KeyError, ValueError):
        return False


async def _rss_startup_catchup():
    """A one-off catch-up run at process startup (not
    on the daily RSS_FETCH_HOUR cycle) — following a real incident
    observed on 2026-09-11: RSS/combined.json had indeed
    refreshed at 8am that morning, but SCRAPP/combined.json had stayed
    dated to the previous day. _rss_daily_scheduler() itself is
    correct though (each of the two calls has its own independent try/except,
    one failing can never prevent the other from running in the
    same tick) — the most likely scenario is that a process restart
    (for an unrelated reason) happened exactly between
    the two calls that morning, before fetch_grid_links.fetch_all()
    had time to write its own file. In that case,
    without this catch-up, no further refresh would have happened before
    the next day's 8am tick — up to 24h of delay, exactly what was
    reported. Reruns each of the two fetches, independently, only if
    its own file isn't already dated today — so it does
    nothing at all at startup on a day when both already refreshed
    normally."""
    if not _combined_json_is_fresh(RSS_DIR / "combined.json"):
        await _refresh_rss()
    if not _combined_json_is_fresh(SCRAPP_DIR / "combined.json"):
        await _refresh_scrapp()


@app.on_event("startup")
async def _start_rss_scheduler():
    asyncio.create_task(_rss_daily_scheduler())
    asyncio.create_task(_rss_startup_catchup())
    # Periodic presence sweep: catches a drop in headcount
    # (LOG_USERS/) even once no more heartbeat arrives — see
    # _presence_sweep_scheduler.
    asyncio.create_task(_presence_sweep_scheduler())
    # Periodic CPU/GPU occupancy sampling for the info-panel meters — see
    # _resource_usage_sampler.
    asyncio.create_task(_resource_usage_sampler())

# In-memory job store: job_id -> {status, step, result, error}. A single
# uvicorn process (no --workers, see run_Falcon.sh) is all this app ever
# runs as, so a plain dict needs no locking or external store. Bounded to
# the most recent MAX_JOBS entries so a long-running process doesn't grow
# this dict forever — a finished job only needs to survive long enough for
# the frontend's polling loop to pick up its result.
JOBS = {}
MAX_JOBS = 50

# Rows per page of GET /api/library's own listing, at the user's explicit
# request: "Ajoute une pagination à la liste des grilles de la
# bibliothèque : 20 lignes affichées max à chaque page."
LIBRARY_PAGE_SIZE = 20

# Max length of a user's own nickname ("pseudo"), at the user's explicit
# request ("un pseudo (moins de 15 lettres)"). Enforced defensively on
# every path that accepts one — a longer value is silently trimmed, never
# rejected, so a stray extra character can't block a generation.
MAX_PSEUDO_LENGTH = 15

# Max length of the secret word associated with a pseudo (backend/secret_store.py),
# at the user's explicit request — lets a user
# prove a chosen pseudo genuinely belongs to them. Same defensive
# convention as MAX_PSEUDO_LENGTH: bounded server-side, never rejected for
# an excessive length before truncation.
MAX_SECRET_LENGTH = 60

# "x en ligne" counter, at the user's explicit request. Each open web-UI
# tab POSTs /api/presence every 2s with its own session id + current
# pseudo; a session is "active" while its last ping is under
# PRESENCE_TTL_S old. The count is de-duplicated by pseudo (two tabs of
# the same person = one user, at the user's explicit follow-up request);
# a still-pseudo-less session (welcome overlay not accepted yet) counts
# as its own anonymous unit, keyed by session id. `_PRESENCE` is a plain
# module dict — fine, since the back end always runs single-process (no
# --workers, see above) — pruned of stale entries on every request and
# hard-capped so an abusive client can't grow it without bound.
PRESENCE_TTL_S = 60
MAX_PRESENCE_ENTRIES = 5000
_PRESENCE = {}  # session_id -> {"last_seen": monotonic float, "pseudo": str}

# Log of the number of online visitors, at the user's explicit
# request: "the backend must generate one file per day in the
# LOG_USERS folder, recording in this file a new line with the date
# and time of a change in the number of visitors, the number of
# visitors, then the list of active pseudos after this number
# changes." One `LOG_USERS/<YYYY-MM-DD>.log` file per day (project
# root, gitignored — a generated artifact, the same convention as LOG_LLM/,
# LOG_CHAT/), a line added only when the distinct-user *count*
# changes (never when only the composition of pseudos changes at a
# constant headcount — it really is "a change in the count" that is
# asked for). Best-effort write: a failure is only logged,
# never left to break a presence heartbeat.
USERS_LOG_DIR = _PROJECT_ROOT / "LOG_USERS"

# A `threading.Lock` now protects `_PRESENCE` and
# `_last_logged_pseudos`: `POST /api/presence` (a synchronous `def`
# function, run in Starlette's own thread pool) and the periodic
# sweep `_presence_sweep_scheduler` (a coroutine on the event
# loop) can both recompute the headcount — without a lock, one side purging
# expired sessions could race a write on the
# other side, and two callers could simultaneously see "the headcount
# changed" and both write a duplicate line. The lock only covers the
# in-memory recomputation; the disk write always happens outside it.
_PRESENCE_LOCK = threading.Lock()

# Last distinct user LIST logged to LOG_USERS (None at
# startup, so the very first heartbeat after the server launches/
# restarts logs a line — which also marks a
# restart in the timeline). At the user's explicit
# request ("LOG_USERS must update every time the
# user list changes"): tracked by the full LIST (the `pseudos`
# that _write_users_log records), not just by total headcount — a
# plain counter never catches a user going from anonymous to
# named, or a pseudo changing, as long as the total count stays
# the same, which is exactly what left the log stuck on a
# stale "(anonymous)" once the welcome panel was accepted.
_last_logged_pseudos = None

# The periodic sweep exists to catch a *drop* in headcount when
# heartbeats stop arriving (everyone has left): without it, the purge
# only ever runs inside `POST /api/presence`, so the drop to 0 would
# never be logged until a new visitor connects. 10s is
# fine-grained enough compared to PRESENCE_TTL_S (60s) and costs almost nothing
# (iterating a dict of at most MAX_PRESENCE_ENTRIES entries).
PRESENCE_SWEEP_INTERVAL_S = 10

# Small CPU/GPU occupancy meters in the top-right info panel, at the
# user's explicit request: "ajouter un petit vu-mètre indiquant le taux
# d'occupation de chaque ressource (GPUs / CPU). Un seul vu-mètre pour
# l'ensemble des CPUs." Sent to the frontend in the same calls as the
# online-presence heartbeat (POST /api/presence), also at the user's
# explicit request, rather than through a separate call — an open tab
# therefore gets an occupancy update at the same cadence as its own
# presence heartbeat (every 2s), with no extra HTTP request.
#
# Sampled on its own timer (_resource_usage_sampler below), never per
# request: a GPU read (nvidia-smi) is a real subprocess spawn, far too
# costly to redo on every heartbeat of every connected tab.
# `_LATEST_RESOURCE_USAGE` is a plain module dict — updating it is a
# whole-reference reassignment (never an in-place mutation), atomic in
# CPython, so `POST /api/presence` can read it with no lock despite the
# concurrent write from the background task.
RESOURCE_USAGE_SAMPLE_INTERVAL_S = 2.0
_LATEST_RESOURCE_USAGE = {"cpu_percent": None, "gpu_percent": []}

# A dedicated single-thread executor for _resource_usage_sampler below,
# rather than the event loop's own shared default executor (what a plain
# `asyncio.to_thread` call uses) — deliberately, since that shared pool is
# also where every generation job's own blocking `asyncio.to_thread(
# generate_grid, ...)` call runs for however long that generation takes
# (up to several minutes), and this app can have several such jobs queued
# at once. Found live: right after a restart with a generation already in
# flight, the sampler's own `asyncio.to_thread` call sat queued behind
# that other work for over 15s before ever running a second time — the
# one moment a real load meter most needs to stay responsive is exactly
# when the machine is under real load, so this queue contention would
# have silently defeated the whole feature. A dedicated single worker
# thread means this sampler's own quick /proc/stat read + nvidia-smi call
# never waits on anything else this app is doing.
_RESOURCE_USAGE_EXECUTOR = concurrent.futures.ThreadPoolExecutor(
    max_workers=1, thread_name_prefix="resource-usage"
)

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

# Per-job state for the web UI's "Interactif" authoring mode — the built
# word index (a DualIndex), the theme glossary set, and the per-session
# RNG. Kept in its OWN dict, out of JOBS itself, for the same reason as
# CANCEL_EVENTS: none of it is JSON-serializable and GET /api/generate/
# status/{job_id} returns the JOBS entry directly. Evicted in lockstep
# with JOBS's MAX_JOBS bound in _new_job(). POST /api/interactive/step
# reads its `index`/`priority_words`/`rng` back to place one more word.
INTERACTIVE_SESSIONS = {}

# asyncio.create_task() only holds a weak reference to the task it
# schedules — without another strong reference kept somewhere, the task can
# be garbage-collected mid-run (a documented asyncio footgun). This set is
# that reference; each task removes itself once done.
_BACKGROUND_TASKS = set()


class GenerateRequest(BaseModel):
    language: str = Field(default="fr", description="fr, en, de, es or it")
    # `None` by default (an ordinary, monolingual grid), at the user's
    # explicit request: "Add the ability to generate a
    # bilingual grid... every step uses the first language
    # for the across words, and the second language for the
    # down words." The web UI forces this field to the same value as
    # `language` the moment that one changes (see frontend/static/script.js),
    # but the player can then set it to a different language to
    # get a genuinely bilingual grid — `None`, or a value identical
    # to `language`, both degrade cleanly to ordinary
    # monolingual generation (see crossword_gen.generate_grid's own
    # `bilingual_wordlist_path`), so Automation/Populate.py (which
    # never sends this field) keeps generating purely
    # monolingual grids with no change on its own side.
    bilingual_language: Optional[str] = Field(
        default=None,
        description=(
            "Language of the down words for a bilingual grid (fr, en, de, es, "
            "it or pt); None or identical to `language` = ordinary monolingual grid"
        ),
    )
    # No upper bound, at the user's explicit request (the
    # previous cap, 25, was removed) — only a lower bound remains,
    # a grid smaller than that no longer really makes sense as a crossword.
    # The CLI (`crossword_gen.py`'s `main()`) never had a
    # cap at all; this Field is therefore now aligned with it.
    width: int = Field(default=DEFAULT_WIDTH, ge=5, le=30, description="Grid width (horizontal)")
    height: int = Field(default=DEFAULT_HEIGHT, ge=5, le=30, description="Grid height (vertical)")
    difficulty: str = Field(default="easy", description="easy, medium or hard")
    seed: Optional[int] = None
    # 1 by default (raised from 0, at the user's explicit request) —
    # the statistical "seed" sampling (see crossword_gen.py's
    # sample_letter_biases/generate_grid — formerly called "forced
    # letters", renamed at the user's explicit request: "slots
    # that initiate the first placements, or influence them
    # once other letters already exist") used to be applied
    # systematically at a fixed fraction (5%); it is now a free-form
    # input field on the UI (an integer between 0 and 100, rather
    # than a list of predefined percentages — at the user's explicit
    # request, see frontend/static/index.html), converted to a
    # fraction (`percent / 100`) right before calling generate_grid.
    force_letters_percent: int = Field(
        default=1, ge=0, le=100,
        description="Percentage of seeds at the start of filling (integer, 0 to 100)",
    )
    # 14% by default on the API side (the UI uses this same fixed
    # value as its initial value, see frontend/static/index.html — replaces
    # a formula that depended on the grid size, used
    # before, at the user's explicit request) — replaces
    # POST_PREFILL_BLACK_FRACTION (crossword_gen.py), previously a
    # constant fixed at 10%, not adjustable from the UI. Applied to
    # every palier that starts from a blank grid or a cleanup
    # (`_build_retry_seed`) — never to a "reprise telle-quelle" (as-is
    # resume) palier (`_pattern_continue`), which never calls
    # make_pattern again and therefore can never add any black cell
    # either way. Free-form input field from the UI (an integer between 0
    # and 100), at the user's explicit request, rather than a
    # list of predefined percentages. The percentage is computed on the
    # number of white cells *before* this palier's pre-fill
    # (`make_pattern`'s `initial_white_count`), not on what's left of it
    # once pre-fill is finished — at the user's explicit
    # request ("the black cells added during pre-fill
    # count toward the black-fill target"): if
    # pre-fill has already placed more cells than this percentage
    # asks for, no further cell is added for this reason.
    # Removed once (mistakenly, alongside the unrelated per-cycle
    # single-cell lock), then restored — only that separate lock was ever
    # meant to go, not this percentage mechanism (see CLAUDE.md).
    black_enrichment_percent: int = Field(
        default=17, ge=0, le=100,
        description=(
            "Percentage of white cells (before pre-fill) turned "
            "into black cells at every palier, pre-fill included (integer, 0 to 100)"
        ),
    )
    # "Mode" selector (see BUDGET_MODES above), at the user's explicit
    # request — directly sets the search budget per
    # attempt, replacing for this request the default formula of
    # `crossword_gen.try_fill` (width × height × 2000). "medium" by
    # default, the mode closest in order of magnitude to that same
    # formula on the 15×10 reference grid (300,000).
    mode: str = Field(
        default="medium",
        description=f"Search-budget mode: {sorted(BUDGET_MODES)}",
    )
    # Nickname (pseudo) of the user generating the grid, at the
    # user's explicit request: "When a grid is
    # saved, if a pseudo is set, save the pseudo in the
    # grid's JSON." `None`/empty = no author recorded (the
    # behavior before this feature). Not bounded here by a
    # pydantic constraint that would return a 422: `_run_generate_job`
    # cleans it up and truncates it to MAX_PSEUDO_LENGTH, so a stray
    # extra character never blocks a generation.
    pseudo: Optional[str] = None
    # Optional theme: a list of words (free text) giving a
    # semantic orientation to the grid, at the user's explicit
    # request. Non-empty -> `_run_generate_job` runs a Qdrant
    # pre-search per keyword, per length (see THEME_LENGTH_MIN/MAX/
    # THEME_MIN_SCORE), and passes the resulting glossary to
    # generate_grid(priority_words=...). `None`/empty = ordinary grid,
    # no pre-search. Not bounded by a pydantic constraint (like
    # `pseudo`): cleaned up in _run_generate_job.
    theme: Optional[str] = None
    # "Précision thématique" (theme precision) form field (right after "Mode"), at
    # the user's explicit request: "to be able to configure
    # THEME_MIN_SCORE by hand". Minimum Qdrant similarity threshold for
    # a word to enter THIS generation's theme glossary —
    # passed as `min_score` to _compiled_theme_words_by_length /
    # _theme_words_by_length. Defaults to the module
    # constant THEME_MIN_SCORE (0.68). Has no effect if `theme` is empty.
    theme_precision: float = Field(
        default=THEME_MIN_SCORE, ge=0.0, le=1.0,
        description="Minimum Qdrant similarity threshold for the theme glossary (0.0 to 1.0)",
    )
    # Origin of the request, at the user's explicit request:
    # "Populate: when a request comes from Populate, generate the
    # definitions without parallelizing several requests at once, so as
    # not to overload the GPU for real users." `None` (an ordinary
    # web UI request) by default — `Automation/Populate.py` is the
    # only caller that ever sends `"populate"` here (see its own
    # `_build_request()`). `_run_generate_job` reads this field to force
    # `clue_generator.generate(batch_parallelism=1)` (see backend/
    # clues.py's own docstring) only for this case — CLUES_QUEUE
    # already serializes jobs against each other, but a single job can
    # still fire up to CLUE_BATCH_PARALLELISM concurrent LLM requests, which
    # could slow down a real user calling in parallel an
    # out-of-queue endpoint (e.g. "Proposer une définition"/"Proposer un
    # titre"). Not bounded by a pydantic constraint: an
    # unknown value is simply ignored (treated as an
    # ordinary request), never a 422.
    source: Optional[str] = None
    # "Mots Défi (personnalisation)": the same free-form, author-typed
    # word list as Interactive mode's own panel (see
    # `InteractiveStepRequest.challenge_words` below) — kept exactly as
    # typed (accents/case), normalized only inside `_run_generate_job`
    # via `challenge_word_grid_form` before being handed to
    # `generate_grid(challenge_words=...)`. Empty by default = no effect,
    # ordinary generation. Never shown in the Library (see `grid_store.
    # save_grid_json`'s own `challenge_words` field, absent from
    # `_iter_stored_grids`'s whitelist) — these words are assumed to be
    # answers the author is deliberately hiding at specific locations.
    challenge_words: list[str] = Field(default_factory=list)


class RecomputeRequest(BaseModel):
    """Body of POST /api/recompute — the "Recalculer" button on a grid in
    play mode, at the user's explicit request: "recalculer les définitions.
    Ne pas remplacer la grille sauvegardée, mais créer une copie identique
    ... Remplacer la grille affichée par la nouvelle." Only the library id
    of the currently displayed grid is sent — the backend reloads the full
    stored record (grid_store.get_grid), re-runs only clue generation on
    its words (never the grid search), bumps a "(Vn)" version marker on
    the title (_next_version_title — "Graines" -> "Graines (V2)" ->
    "Graines (V3)"; never regenerated), and saves a brand new library
    record, leaving the original untouched."""
    grid_id: str


class InteractiveStepRequest(BaseModel):
    """Body of POST /api/interactive/step — the "Suivant" button of the
    "Interactif" authoring mode. Carries the whole current editable grid
    (a 2D list, one string per cell: "#" black, "." empty white, or an
    uppercase letter). The backend places exactly one more word on top of
    it (no backtracking) and returns the updated grid. `challenge_words`
    is the client's current "Mots Défi" list, exactly as the author typed
    each entry — accents/case kept, "comme dans les dictionnaires," at the
    user's explicit request, see #interactive-challenge-panel in the web
    UI — sent on every "Suivant" click so the placed word is drawn from
    this list first whenever one still fits, ahead of the theme glossary
    (see `backend/crossword_gen.py`'s `interactive_place_word`/`Filler.
    challenge_words`). This endpoint's own handler derives each entry's
    bare-uppercase grid form on the fly (`challenge_word_grid_form`) —
    the request body itself is never stored, so it doesn't need to be in
    that form already. Defaults to empty for a session with no challenge
    words."""
    job_id: str
    grid: list[list[str]]
    challenge_words: list[str] = []


class InteractiveCleanRequest(BaseModel):
    """Body of POST /api/interactive/clean — the "Nettoyer"/"Nettoyer
    (+noires)" buttons of the "Interactif" authoring mode. "Nettoyer", at
    the user's explicit request ("ajouter un bouton 'Nettoyer' permettant
    de déclencher l'opération de nettoyage complet des zones
    impossibles"): removes every word crossing an impossible zone (or
    blackens a cell, same rules as the automatic generator's own
    "nettoyage complet" — see `interactive_clean_impossible_zones`).
    `deep=True` ("Nettoyer (+noires)", at the user's own explicit
    follow-up request — "nettoyage approfondi... nettoyage des cases
    noires, et pas seulement les emplacements impossibles") additionally
    runs `interactive_minimize_black_cells` on the result: tries removing
    every black cell of the grid outright, not just the ones incidentally
    touched while resolving an impossible zone.

    `challenge_words` is the client's current "Mots Défi" list, exactly
    as the author typed it — same convention/derivation as `Interactive
    StepRequest.challenge_words` (`challenge_word_grid_form`, applied by
    this endpoint's own handler) — so "Nettoyer"/"Nettoyer (+noires)"
    never strip out a validly placed challenge word, at the user's
    explicit request: "Les Mots Défi doivent être considérés comme
    faisant partie du dictionnaire.\" """
    job_id: str
    grid: list[list[str]]
    deep: bool = False
    challenge_words: list[str] = []


class InteractiveCandidatesRequest(BaseModel):
    """Body of POST /api/interactive/candidates — the "Mots" button of the
    "Interactif" authoring mode, at the user's explicit request: "ajouter
    un bouton Mots qui liste les mots possible pour l'emplacement
    sélectionné." `cells` is the selected word's own ordered list of
    `[row, col]` pairs (already resolved client-side by
    `selectedInteractiveWord()`, script.js) — never recomputed server-side
    from `grid` alone, so this works the same way whether that word is
    already fully typed or still partially empty. `challenge_words` (the
    author's own typed spelling, like `InteractiveStepRequest`) is treated
    as part of the dictionary when deciding whether a candidate would
    create a new impossible crossing slot (see `interactive_slot_
    candidates`'s own `unsafe` field)."""
    job_id: str
    grid: list[list[str]]
    cells: list[list[int]]
    challenge_words: list[str] = []


class InteractiveCrossingRequest(BaseModel):
    """Body of POST /api/interactive/crossing — the "Croisés" button, at
    the user's explicit request: "à droite du bouton Mots, ajouter un
    bouton Croisés : identifie les lettres compatibles avec un mot dans
    chaque sens (peut être restreint par les lettres en place)... pour
    chaque lettre compatible avec un mot dans chaque sens, lister les
    mots horizontaux..., et les mots verticaux..." Unlike
    `InteractiveCandidatesRequest` (a whole slot's own `cells`), this
    button reasons about the crossing of TWO emplacements (horizontal AND
    vertical) through a single cell — so it only ever needs that one
    `cell`, never a pre-resolved cell list; the backend derives both
    emplacements itself (see `interactive_crossing_words`). `challenge_
    words`, like `InteractiveCandidatesRequest`, feeds the same crossing-
    safety check backing each candidate's own `unsafe` field."""
    job_id: str
    grid: list[list[str]]
    cell: list[int]
    challenge_words: list[str] = []


class InteractiveBoundaryRequest(BaseModel):
    """Body of POST /api/interactive/boundary — the "Début"/"Fin" buttons,
    at the user's explicit request: "à côté du bouton Croisés, ajouter un
    bouton 'Début' qui liste le mots pouvant commencer l'emplacement
    sélectionné en tenant compte des lettres posées, même si il ne fait
    pas la longueur totale de l'emplacement... ajouter un bouton 'Fin' qui
    fait la même chose pour lister les mots qui peuvent terminer
    l'emplacement." Like `InteractiveCandidatesRequest`, `cells` is the
    selected slot's own ordered `[row, col]` list (`selectedInteractiveWord
    ()`, script.js); `side` ("start" for "Début", "end" for "Fin") picks
    which end of the slot the shorter candidate words anchor to — see
    `interactive_boundary_candidates`. `challenge_words`, like
    `InteractiveCandidatesRequest`, feeds the same crossing-safety check
    backing each candidate's own `unsafe` field."""
    job_id: str
    grid: list[list[str]]
    cells: list[list[int]]
    side: str
    challenge_words: list[str] = []


class InteractiveImpossibleRequest(BaseModel):
    """Body of POST /api/interactive/impossible — the "Impossibles" button
    of the "Interactif" authoring mode, at the user's explicit request:
    "à gauche de 'Vérifier', ajouter un bouton 'Impossibles' qui ne
    vérifie que les emplacements impossibles (y compris les mots posés
    inconnus) et les emplacements avec trop peu de possibilités." A
    read-only diagnostic — never mutates the grid, unlike "Nettoyer" —
    reusing `_interactive_fill_diagnostics` exactly as `/step`/`/clean`
    already do internally: `impossible_cells` already covers both a
    still-open slot with zero real candidates AND an already-fully-typed
    word that isn't a real dictionary word (`_invalid_fully_known_
    indices`), matching "y compris les mots posés inconnus" with no
    separate mechanism needed. "Vérifier" reuses this exact same check
    and additionally verifies every complete word has a definition.

    `challenge_words` (the client's current "Mots Défi" list, same
    convention as `InteractiveStepRequest.challenge_words`) is exempted
    from this check — a "Mots Défi" word is considered part of the
    dictionary for it, at the user's explicit request, so it never shows
    up red as an "impossible"/invented word here (or, by extension, via
    "Vérifier", which is built directly on top of this endpoint)."""
    job_id: str
    grid: list[list[str]]
    challenge_words: list[str] = []


class InteractiveStatsRequest(BaseModel):
    """Body of POST /api/interactive/stats — the "Stats" button of the
    "Interactif" authoring mode, placed just before "Impossibles". A
    read-only diagnostic — never mutates the grid — that reuses the same
    statistical mechanism `generate_grid` uses to pick its own "graines"
    preview letters (`sample_letter_biases`): for every still-empty white
    cell, the single letter that turns up most often across a large
    random sample of dictionary words compatible with that cell's own
    slot(s) and whatever letters are already placed elsewhere in the
    grid."""
    job_id: str
    grid: list[list[str]]


class InteractiveVerifyWord(BaseModel):
    """One word to check via POST /api/interactive/verify — carries its
    own `direction` so a genuinely bilingual interactive session (see
    _load_interactive_index's own `bilingual_language` parameter) checks
    each word against the right language's dictionary. Reported directly
    by the user: "Le bouton 'Vérifier' ne semble pas tenir compte du fait
    que la grille est bilingue, et signale les mots de la seconde langue
    comme ne faisant pas partie du dictionnaire" — the previous shape
    (`words: list[str]`) had no way to say which direction a given answer
    belonged to, so the endpoint always checked every word against
    `.across` regardless."""
    answer: str
    direction: str = "across"


class InteractiveVerifyRequest(BaseModel):
    """Body of POST /api/interactive/verify — the "Vérifier" button of the
    "Interactif" authoring mode, at the user's explicit request: check the
    WHOLE grid at once (not just the currently selected word, as this
    button used to) and report every complete word that is not a real
    dictionary word. `words` is every currently complete (fully filled)
    slot's own {answer, direction} — whether a complete word also has a
    definition is resolved entirely client-side (interactiveDefs lives
    only in the browser), so the backend is only ever asked to validate
    dictionary membership, nothing else.

    `challenge_words` (the client's current "Mots Défi" list, same
    convention as `InteractiveStepRequest.challenge_words`) is considered
    part of the dictionary for this check, at the user's explicit
    request — a word in this list is never reported back as invalid,
    whatever its real dictionary status."""
    job_id: str
    words: list[InteractiveVerifyWord]
    challenge_words: list[str] = []


class InteractiveTitleRequest(BaseModel):
    """Body of POST /api/interactive/title — the "Proposer un titre" button.
    `words` is a list of `{answer, accented?, canonical?}` for every grid
    word; the backend asks the LLM for up to TITLE_PROPOSALS_COUNT (10)
    candidate titles (best-effort, [] on failure — see backend/clues.py's
    `LLMClueGenerator.generate_titles`), at the user's explicit request:
    "Le bouton 'Proposer un titre' doit générer 10 propositions affichées
    en dessous (comme 'Proposer une définition')."

    `theme` (optional) is the CURRENT value of the "Thématique" field —
    at the user's explicit request ("également utiliser le champ
    Thématique si renseigné"). Sent by the frontend rather than always
    read back from the session's own stored `theme`/`theme_description`
    so a player's own live edit to the field is honoured immediately,
    and so a re-edited grid whose theme was never computed into a rich
    LLM sentence (see `_run_interactive_resume_job`'s own `theme_
    description: None`) still gets *some* theme steering — falls back to
    the session's own stored theme when omitted/blank."""
    job_id: str
    words: list[dict]
    language: str = "fr"
    theme: Optional[str] = None


class InteractiveSaveRequest(BaseModel):
    """Body of POST /api/interactive/save — the "Sauvegarder" button.
    `grid` is the final editable grid (letters + "#" + "."); `definitions`
    is `[{row, col, direction, clue}]` keyed by each word's start cell.
    The backend rebuilds a generate_grid()-shaped result and stores it in
    the library, tagged `interactive=True` ("(Création)")."""
    job_id: str
    grid: list[list[str]]
    definitions: list[dict]
    title: str = ""
    language: str = "fr"
    # The session's own second (vertical-words) language on a genuinely
    # bilingual interactive grid — `None` (the default) for an ordinary
    # monolingual one. Passed straight through to grid_store.save_grid_
    # json's own `bilingual` parameter.
    bilingual_language: Optional[str] = None
    difficulty: str = "easy"
    theme: Optional[str] = None
    pseudo: Optional[str] = None
    # The client's current "Mots Défi" list — same convention as
    # InteractiveSaveWorkRequest.challenge_words just below (it genuinely
    # changes within a session, no session-start value to fall back to,
    # so it's resent here too rather than read back from
    # `JOBS[job_id]["interactive"]`, which is never kept current).
    challenge_words: list[str] = []


class InteractiveSaveWorkRequest(BaseModel):
    """Body of POST /api/interactive/save_work — an internal autosave
    fired by the frontend after every "Suivant"/"Précédent" click in the
    "Interactif" mode, at the user's explicit request: "chaque appui sur
    Suivant/Précédent sauvegarde l'état en cours du process de création
    dans le dossier GRID_WORK." Unlike InteractiveSaveRequest (the final
    "Sauvegarder" button, which files a brand-new, permanent GRID_STORE
    record), this repeatedly OVERWRITES one single GRID_WORK file across
    a session's whole lifetime — see grid_store.save_grid_work's own
    docstring. `language`/`difficulty`/`theme` are deliberately absent
    here: the endpoint reads them back from `JOBS[job_id]["interactive"]`
    (set once, at session start) instead of trusting the frontend to
    resend them correctly on every single autosave. `challenge_words` is
    the client's current "Mots Défi" list — unlike the three fields just
    above, it genuinely changes within a session (the author edits it
    directly, no session-start value to fall back to), so it IS resent on
    every autosave, exactly like `grid`/`definitions`/`title`."""
    job_id: str
    grid: list[list[str]]
    definitions: list[dict] = []
    title: str = ""
    pseudo: Optional[str] = None
    challenge_words: list[str] = []


class InteractiveFinishRequest(BaseModel):
    """Body of POST /api/interactive/finish — the "Finir la grille" button
    of the "Interactif" authoring mode, at the user's explicit request:
    "lance une génération automatique en verrouillant définitivement les
    lettres déjà positionnées..., y compris la génération des définitions
    manquantes (mais pas celles déjà définies)." `grid`/`definitions` are
    the current editable state (same shape as InteractiveSaveRequest);
    `language`/`difficulty`/`bilingual_language`/`theme`/the interactive
    session's own already-resolved theme glossary are all read back from
    `JOBS[job_id]["interactive"]`/`INTERACTIVE_SESSIONS[job_id]` (set once
    at session start), never trusted from the request body — the same
    convention `InteractiveSaveWorkRequest` already establishes.
    `mode`/`black_enrichment_percent`/`force_letters_percent` mirror the
    generation form's own current values, since this reuses the ordinary
    automatic-generation engine (see `interactive_finish`/
    `_run_generate_job`)."""
    job_id: str
    grid: list[list[str]]
    definitions: list[dict] = []
    mode: str = "medium"
    black_enrichment_percent: int = Field(default=17, ge=0, le=100)
    force_letters_percent: int = Field(default=1, ge=0, le=100)
    # Same convention as InteractiveSaveRequest/InteractiveSaveWorkRequest
    # (trusted directly from the frontend's own current userPseudo, never
    # derived from the session) — this is who's using the browser right
    # now, not necessarily tracked anywhere on the session itself.
    pseudo: Optional[str] = None
    # "Finir la zone" — `None`/empty (the default) is plain "Finir la
    # grille", completely unaffected. When given, this is the ALREADY
    # whole-emplacement-expanded selection the player drag-selected
    # client-side (script.js's interactiveZoneSelection — every [row, col]
    # of it, not just the raw dragged rectangle), at the user's explicit
    # request: "lance un processus de remplissage automatique similaire à
    # 'Finir la grille', mais en verrouillant tous les emplacements qui ne
    # font pas partie de la sélection (en plus des lettres et cases noires
    # déjà en place)." Every currently-blank cell NOT in this list gets
    # permanently frozen black (see `interactive_finish`) — a cell already
    # carrying a letter is locked exactly like "Finir la grille" already
    # does, regardless of whether it's inside or outside the zone.
    zone_cells: Optional[list[list[int]]] = None


class InteractiveWorkIdRequest(BaseModel):
    """Body shared by POST /api/interactive/work/delete and POST
    /api/interactive/resume — both only ever need the target GRID_WORK
    record's own id."""
    work_id: str


class InteractiveFromLibraryRequest(BaseModel):
    """Body of POST /api/interactive/from-library — the "Ouvrir en mode
    Interactif" icon button next to a library grid's "Jouer" link, at the
    user's explicit request: "ouvrir la grille en mode Interactif, donc
    créer une nouvelle tâche dans GRID_WORK." Only the library id is sent;
    the backend reloads the full stored record (grid_store.get_grid),
    reshapes its finished pattern/solution/words into an editable
    interactive session (every letter already placed, every clue
    pre-filled) and, from the very first autosave on, that session lives
    as its own brand-new GRID_WORK "Créations" entry — the original
    library record is never touched."""
    grid_id: str


@dataclass
class GenerationTask:
    """Everything the two-stage background pipeline below (GRID_QUEUE,
    then CLUES_QUEUE) needs to process one generation request end to end,
    at the user's explicit request: "mets les informations de la
    génération d'une grille dans une classe de tâche." Created once per
    job, in _run_generate_job, and threaded through both queues in turn —
    `req`/`resume_state` are exactly what generate_grid() itself already
    needed before this refactor, just carried in one object instead of as
    two loose parameters. Equality/identity: job_id is always a fresh
    uuid4 hex (see _new_job), so two distinct tasks can never compare
    equal by accident — safe for the plain `is`/`in`/`list.remove()`
    checks GRID_QUEUE/CLUES_QUEUE below rely on.

    `req`/`resume_state` are never actually read back off the task (it is
    purely a queue-identity token) — `_run_recompute_job` below reuses the
    same queue plumbing with `req=None`, since a recompute has no
    GenerateRequest of its own."""
    job_id: str
    req: Optional[GenerateRequest] = None
    resume_state: Optional[dict] = None


# Two single-concurrency FIFO queues, at the user's explicit request:
# "La génération étant coûteuse en ressources, mets les informations de
# la génération d'une grille dans une classe de tâche, et gère deux
# files d'attente : une file pour la génération de la grille (CPU), et
# une autre pour la génération des définitions (GPU)." Each is a plain
# list of GenerationTask, front = index 0 = whichever task is either
# about to start or already running — deliberately *not* a
# concurrent.futures/asyncio.Queue (put/get only, no way to inspect what
# else is waiting without consuming it): _wait_in_queue below needs to
# report every other waiting job's own 1-based position, which a plain
# list's own .index() gives for free. A task is only ever appended once
# (right before it needs that queue) and removed once (right after the
# real work it was queued for finishes, win or lose — see
# _run_generate_job's own try/finally around each stage) — never
# reordered, so at most one task is ever actually being processed per
# queue at a time: the CPU queue never runs more than one grid search at
# once (each one already spawns up to PARALLEL_ATTEMPTS worker processes
# on its own, see crossword_gen.py), and the GPU/LLM queue never runs
# more than one clue-writing pass at once (there has only ever been one
# local model server to share, see backend/clues.py).
GRID_QUEUE = []
CLUES_QUEUE = []

# How often a still-queued job's own status is refreshed with its current
# queue position (frontend/static/i18n.js's statusQueuedGrid/
# statusQueuedClues) — deliberately not instant: a plain polling loop is
# far simpler than an event/condition-variable wakeup, and a couple of
# seconds' latency before a freshly-freed queue slot is actually noticed
# is a complete non-issue for a background job the player is already
# waiting on regardless.
QUEUE_STATUS_POLL_INTERVAL_S = 2.0


async def _wait_in_queue(queue, task, job, cancel_event, step_code):
    """Blocks until `task` reaches the front of `queue` (index 0 — see
    GenerationTask's own docstring: this means either "about to start" or
    "already running", the two are indistinguishable from outside since
    nothing ever awaits between a task reaching the front and its own
    real work actually starting) — never removes `task` from `queue`
    itself either way, that's the caller's own responsibility once the
    real stage work this wait is gating is actually finished (see
    _run_generate_job's own try/finally blocks), so the task keeps
    "holding" the queue's one slot for the whole duration of its real
    work too, not just while waiting.

    While still waiting (not yet at the front), updates job["step"] with
    a live queue-position status once every QUEUE_STATUS_POLL_INTERVAL_S
    — and, at the user's explicit request, also cooperatively honors the
    "Stop" button's cancel_event even before this job's own real
    processing has ever started: without this check here, clicking Stop
    on a still-queued job would silently do nothing until its turn
    finally came, since no other code checks this event while a job is
    merely waiting in line."""
    while queue[0] is not task:
        if cancel_event.is_set():
            queue.remove(task)
            raise GenerationCancelled()
        job["step"] = {
            "code": step_code,
            "position": queue.index(task) + 1,
            "queue_length": len(queue),
        }
        await asyncio.sleep(QUEUE_STATUS_POLL_INTERVAL_S)


# Fair-scheduling / round-robin preemption, at the user's explicit
# request: "Lorsqu'une génération de grille ou de définitions dure depuis
# plus de 15mn, et qu'il y a des tâches en attente dans la phase en
# cours, au moment de passer au cycle suivant ou à la génération de
# définition suivante, replacer la tâche en cours dans la file d'attente
# de cette phase, et traiter la demande suivante dans la file de cette
# tâche. Les tâches doivent pouvoir être reprises là où elles ont été
# interrompues. Informer l'utilisateur de sa position dans la file
# d'attente." Without this, a single very long-running job (a large or
# especially hard grid, or a slow LLM) could monopolize its whole queue
# indefinitely, starving every other job waiting behind it even though
# each one only ever gets one turn at a time either way.
MAX_TURN_DURATION_S = 15 * 60


def _is_populate_task(task):
    """Whether `task` was started by Automation/Populate.py — identified
    the same way LLMClueGenerator's own single-clue-at-a-time branch
    already does (`req.source == "populate"`, see GenerateRequest.source
    and Automation/Populate.py's own `_build_request()`). Always `False`
    for a recompute task (`req=None`, see GenerationTask's own docstring)
    and for the "Continuer" resume path — neither is ever started by
    Populate.py."""
    return task.req is not None and task.req.source == "populate"


# Populate priority preemption, at the user's explicit request: "quand une
# demande de génération arrive dans une des files d'attente (remplissage /
# définition), mettre en pause la tâche Populate à la fin de l'étape en
# cours (cycle de remplissage / génération d'une définition) pour donner
# la priorité à la tâche dans la file d'attente. Ne reprendre la tâche
# Populate que quand la file d'attente qui le concerne est vide." Distinct
# from — and checked *before* — the generic MAX_TURN_DURATION_S fairness
# rule below: a Populate task yields the very next time its own
# `should_pause()` is checked (the next palier/word boundary, i.e. "the
# end of the current step"), with no 15-minute grace period at all,
# whenever at least one *other*, non-Populate task is anywhere in the same
# queue — never for another Populate task (Automation/Populate.py only
# ever runs one grid at a time, but this stays correct even if that ever
# changed): Populate's own background bulk generation is never meant to
# compete with a real user's own request, only with other Populate work.
def _make_should_pause(queue, task):
    """Builds a fresh `should_pause` callable for one single "turn" of
    `task` at the front of `queue` — call this again (a new closure, a
    new `turn_start`) every time `task` resumes after being sent back to
    the end of the queue, so "more than 15 minutes" is always measured
    from *this* turn's own start, never cumulatively across several
    pause/resume cycles (see MAX_TURN_DURATION_S's own docstring). Only
    ever yields once someone else is actually waiting (`len(queue) > 1`)
    — pausing a job nobody is waiting behind would serve no purpose, and
    would just needlessly delay it via the queue's own polling cadence
    (see QUEUE_STATUS_POLL_INTERVAL_S) for nothing in return. Passed
    straight through to generate_grid()/LLMClueGenerator.generate() as
    their own `should_pause` parameter — see GenerationPaused's own
    docstring for what happens once it returns true.

    `task` re-entering the front of `queue` after being paused this way
    re-checks the exact same condition immediately (no minimum turn
    duration for this branch) — as long as a foreign task is still
    present anywhere in `queue`, `task` keeps yielding to the back on its
    very next checkpoint, effectively never accumulating more than one
    checkpoint's worth of work at a time until the queue is genuinely
    free of competing (non-Populate) work again."""
    turn_start = time.monotonic()
    is_populate = _is_populate_task(task)

    def should_pause():
        if is_populate and any(
            other is not task and not _is_populate_task(other) for other in queue
        ):
            return True
        return time.monotonic() - turn_start >= MAX_TURN_DURATION_S and len(queue) > 1

    return should_pause


@app.get("/api/health")
def health():
    return {"status": "ok"}


class PresenceRequest(BaseModel):
    """Body of POST /api/presence — a heartbeat from one web UI tab
    (every 2s). `session_id`: a stable opaque id for this page load;
    `pseudo`: the user's current pseudo (empty until the welcome panel is
    validated). Defensively bounded — these are client-supplied strings,
    used only as in-memory keys, never written to disk."""
    session_id: str = Field(..., min_length=1, max_length=200)
    pseudo: Optional[str] = None


def _presence_snapshot(record=None):
    """Under `_PRESENCE_LOCK`: optionally records a heartbeat (`record` =
    `(session_id, entry)`), purges expired sessions, applies the anti-abuse
    cap, then returns `(count, pseudos, changed)`:

    - `count`  : the number of distinct active users — de-duplicated by
      pseudo (two tabs of the same person = one user), a session still
      without a pseudo counting on its own;
    - `pseudos`: the list to log — the distinct pseudos, sorted
      case-insensitively, followed by one `(anonyme)` per session still
      without a pseudo, so `len(pseudos) == count`;
    - `changed`: `True` if and only if `pseudos` (the WHOLE list, not
      just its length) differs from the last list logged to LOG_USERS —
      and, in that case, updates that marker right here (under the lock),
      so exactly one caller ever sees a given transition and writes a
      single line. A user going from anonymous to named, or changing
      pseudo, therefore triggers a new line even when the total headcount
      itself doesn't move.

    The lock only covers this in-memory recomputation; the caller does
    the disk write (`_write_users_log`) outside of it."""
    global _last_logged_pseudos
    with _PRESENCE_LOCK:
        now = time.monotonic()
        if record is not None:
            sid, entry = record
            _PRESENCE[sid] = entry
        # Purges expired sessions — keeps the dict bounded to "whatever
        # sent a heartbeat within the last PRESENCE_TTL_S (60s) seconds".
        for sid in [s for s, e in _PRESENCE.items()
                    if now - e["last_seen"] > PRESENCE_TTL_S]:
            del _PRESENCE[sid]
        # A safety net against an abusive client: if the dict is still
        # over the cap despite the purge, drop the oldest entries.
        if len(_PRESENCE) > MAX_PRESENCE_ENTRIES:
            for sid, _ in sorted(_PRESENCE.items(),
                                 key=lambda kv: kv[1]["last_seen"])[
                : len(_PRESENCE) - MAX_PRESENCE_ENTRIES
            ]:
                del _PRESENCE[sid]
        named = set()
        anonymous = 0
        for entry in _PRESENCE.values():
            if entry["pseudo"]:
                named.add(entry["pseudo"])
            else:
                anonymous += 1
        count = len(named) + anonymous
        pseudos = sorted(named, key=str.lower) + ["(anonyme)"] * anonymous
        changed = pseudos != _last_logged_pseudos
        if changed:
            _last_logged_pseudos = pseudos
        return count, pseudos, changed


def _write_users_log(count, pseudos):
    """Appends a line to `LOG_USERS/<YYYY-MM-DD>.log`:
    `YYYY-MM-DD HH:MM:SS | <count> | <pseudo1, pseudo2, ...>`. Best-effort
    — a failure is only logged, never let to propagate (the same
    convention as every other log write in this project)."""
    try:
        USERS_LOG_DIR.mkdir(parents=True, exist_ok=True)
        now = datetime.datetime.now()
        path = USERS_LOG_DIR / f"{now:%Y-%m-%d}.log"
        line = (f"{now:%Y-%m-%d %H:%M:%S} | {count} | "
                f"{', '.join(pseudos) if pseudos else '—'}\n")
        with path.open("a", encoding="utf-8") as fh:
            fh.write(line)
    except OSError as exc:
        logger.warning("failed to write LOG_USERS journal: %s", exc)


def _write_theme_log(short_id, theme, description, words, language=None,
                     keyword_lists=None, searched_keywords=None, min_score=None):
    """Writes `LOG_THEME/<timestamp>_<short_id>.log`, the filename prefixed
    with a full timestamp (`%Y%m%d-%H%M%S-%f`) like `LOG_LLM/`
    (`backend/clues.py`, `_write_call_log`) — at the user's explicit
    request ("Préfixer les sauvegarde dans LOG_THEME avec un timestamp,
    comme pour LOG_LLM") — rather than the plain date used until then, so
    files sort chronologically down to the second/microsecond, consistent
    with this project's other logs. At the user's explicit request, the
    FIRST line of the file is the sentence the LLM produced to describe
    the theme (`description`) — or the raw theme if the LLM call failed;
    then come the typed theme, the number of preselected words, and a
    sample. Best-effort — a failure is only logged. At the user's explicit
    request ("Lister le glossaire produit dans le LOG_THEME (un mot par
    ligne)"), the entire glossary of words preselected by Qdrant is listed
    in full, one word per line, right after the header.

    `words`: a list of `(word, score)` pairs, already sorted by increasing
    word length by the caller (`_theme_words_by_length`) — at the user's
    explicit request ("continuer à les lister par taille de mots
    croissante"). The Qdrant similarity score (cosine, higher = closer to
    the theme) is shown next to each word, at the user's explicit request
    ("afficher les scores de chaque mot produit par Qdrant"), and — at the
    user's explicit request ("en indiquant le nombre de lettres en plus du
    score") — the word's own letter count is shown between the two, each
    field separated by a tab so the file stays easy to parse/align.

    `language`: the target language this glossary was built for (the
    `language` parameter of `_build_theme_glossary`, e.g. `"fr"`) — logged
    in the header so a reader of the trace alone (without cross-
    referencing the job/request that produced it) can tell which
    language's Qdrant tenant and `describe_theme` calls this file
    reflects, particularly useful for a bilingual grid's two per-direction
    calls. `keyword_lists`: the keyword lists produced by the LLM, shaped as
    `[(label_or_None, [keyword, ...]), ...]` — `None` for the whole-theme
    list, the theme's own word for each per-word list (see
    `_run_generate_job`'s own theme block). `searched_keywords`: the flat,
    deduplicated set of keywords that actually triggered a Qdrant search
    (see `_split_keywords`/`_compiled_theme_words_by_length`). `min_score`:
    the Qdrant score threshold used for this generation (the "Précision
    thématique" field, `GenerateRequest.theme_precision`). All logged in
    the header to keep track of what was compiled."""
    try:
        THEME_LOG_DIR.mkdir(parents=True, exist_ok=True)
        now = datetime.datetime.now()
        timestamp = now.strftime("%Y%m%d-%H%M%S-%f")
        path = THEME_LOG_DIR / f"{timestamp}_{short_id}.log"
        with path.open("w", encoding="utf-8") as fh:
            fh.write((description or theme).strip() + "\n")
            fh.write(f"\n# generated {now:%Y-%m-%d %H:%M:%S}\n")
            if language is not None:
                fh.write(f"# language: {language}\n")
            fh.write(f"# theme (as typed): {theme}\n")
            if min_score is not None:
                fh.write(f"# score threshold (theme_precision): {min_score}\n")
            if keyword_lists:
                fh.write(f"# {len(keyword_lists)} keyword list(s) from the LLM:\n")
                for label, kws in keyword_lists:
                    tag = "(whole theme)" if label is None else label
                    fh.write(f"#   [{tag}] {', '.join(kws)}\n")
            if searched_keywords:
                fh.write(
                    f"# {len(searched_keywords)} distinct keywords searched in Qdrant: "
                    f"{', '.join(searched_keywords)}\n"
                )
            fh.write(f"# preselected words: {len(words)}\n")
            if words:
                fh.write("\n")
                fh.write("\n".join(
                    f"{w}\t{len(w)}\t{score:.4f}" if score is not None
                    else f"{w}\t{len(w)}"
                    for w, score in words
                ) + "\n")
    except OSError as exc:
        logger.warning("failed to write LOG_THEME journal: %s", exc)


@app.post("/api/presence")
def presence(req: PresenceRequest):
    """Records/refreshes this heartbeat and returns `{"count": N,
    "resource_usage": {...}, "queue_lengths": {"grid": ..., "clues":
    ...}}` — N is the number of distinct active users (see
    `_presence_snapshot`), `resource_usage` is the last CPU/GPU occupancy
    sampled by `_resource_usage_sampler` (`_LATEST_RESOURCE_USAGE`, never
    recomputed here — see that module-level dict for why), and
    `queue_lengths` is the *current* length of GRID_QUEUE/CLUES_QUEUE, at
    the user's explicit request: "ajouter une indication sur la longueur
    des 2 files d'attente : Grille (CPU) et Définition (GPU)." Unlike
    `resource_usage`, this one is recomputed on every call rather than
    cached by a background task: `len()` on a plain in-memory Python list
    (never a subprocess/network call) costs nothing, and GRID_QUEUE/
    CLUES_QUEUE are already the real lists `_wait_in_queue` mutates — no
    staleness risk to avoid here the way there is for GPU occupancy.
    Logs a line to LOG_USERS/ if the user LIST (not just the total count)
    changed since the last line written — at the user's explicit request
    ("LOG_USERS doit se mettre à jour à chaque fois que la liste des
    utilisateurs change"): a user going from anonymous to named, or
    changing pseudo, therefore triggers a new line even when the total
    count itself doesn't move."""
    record = (req.session_id, {
        "last_seen": time.monotonic(),
        "pseudo": (req.pseudo or "").strip()[:MAX_PSEUDO_LENGTH],
    })
    count, pseudos, changed = _presence_snapshot(record)
    if changed:
        _write_users_log(count, pseudos)
    return {
        "count": count,
        "resource_usage": _LATEST_RESOURCE_USAGE,
        "queue_lengths": {"grid": len(GRID_QUEUE), "clues": len(CLUES_QUEUE)},
    }


class PseudoClaimRequest(BaseModel):
    """Body of POST /api/pseudo/claim — submitted when the welcome panel
    closes (see frontend/static/script.js, `welcomeForm`), at the user's
    explicit request: "ajouter une entrée 'Mot secret' permettant à
    l'utilisateur de prouver que le pseudo lui appartient." Unlike every
    other `pseudo` field in this file (always `Optional[str] = None`,
    silently truncated to MAX_PSEUDO_LENGTH), this one is required here
    (`min_length=1`) — but deliberately with no `max_length`: an
    over-long value is silently truncated by the route body below, never
    rejected, the same convention used everywhere else in this file for
    MAX_PSEUDO_LENGTH."""
    pseudo: str = Field(..., min_length=1)
    secret: str = Field(..., min_length=1)


@app.post("/api/pseudo/claim")
def pseudo_claim(req: PseudoClaimRequest):
    """Checks that `secret` matches the secret word already associated
    with `pseudo` (backend/secret_store.py), or registers it if this
    pseudo has never been claimed before (first use = claim).

    Returns `{"ok": true}` on success (correct secret word, or a pseudo
    just claimed); `{"ok": false, "code": "pseudo_taken"}` if this pseudo
    already exists under a different secret word — at the user's explicit
    request: "si le Pseudo saisi existe déjà et que le Mot secret ne
    correspond pas, signaler à l'utilisateur que ce Pseudo est déjà pris,
    ne pas fermer la boite." Always a 200 either way: this isn't a request
    error, only a normal business-logic outcome the client itself must
    distinguish."""
    pseudo = req.pseudo.strip()[:MAX_PSEUDO_LENGTH]
    secret = req.secret.strip()[:MAX_SECRET_LENGTH]
    if not pseudo or not secret:
        raise HTTPException(status_code=400, detail="pseudo ou mot secret vide")
    if verify_or_claim_pseudo_secret(pseudo, secret):
        return {"ok": True}
    return {"ok": False, "code": "pseudo_taken"}


async def _presence_sweep_scheduler():
    """Runs in the background for the whole life of the process: every
    PRESENCE_SWEEP_INTERVAL_S (10s), recomputes the presence headcount and
    logs a line to LOG_USERS/ if it dropped because heartbeats stopped
    (everyone left). Without this sweep, the purge only ever runs inside
    `POST /api/presence`, so the drop to 0 would never be logged until a
    new visitor connects. Never raises to the caller — any error is only
    logged, never allowed to interrupt the loop."""
    while True:
        await asyncio.sleep(PRESENCE_SWEEP_INTERVAL_S)
        try:
            count, pseudos, changed = _presence_snapshot()
            if changed:
                await asyncio.to_thread(_write_users_log, count, pseudos)
        except Exception:
            logger.exception("presence: echec du balayage periodique")


async def _resource_usage_sampler():
    """Runs in the background for the whole life of the process: every
    RESOURCE_USAGE_SAMPLE_INTERVAL_S (2s), samples CPU/GPU occupancy
    (backend/system_info.py, sample_resource_usage()) and replaces
    `_LATEST_RESOURCE_USAGE` wholesale. Samples right at startup (no wait
    before the first round), so the very first presence heartbeat already
    has a real GPU value available (CPU only gets one starting from the
    second sample — see sample_resource_usage()). Goes through
    `_RESOURCE_USAGE_EXECUTOR` (its own dedicated thread, never the event
    loop's default pool — see its own docstring) rather than a plain
    `asyncio.to_thread`, precisely so it never blocks the event loop nor
    gets queued behind some other blocking call this app makes. Never
    raises to its caller — any error is only logged, never left to break
    the loop."""
    global _LATEST_RESOURCE_USAGE
    loop = asyncio.get_running_loop()
    while True:
        try:
            _LATEST_RESOURCE_USAGE = await loop.run_in_executor(
                _RESOURCE_USAGE_EXECUTOR, sample_resource_usage
            )
        except Exception:
            logger.exception("resource usage: echec de l'echantillonnage")
        await asyncio.sleep(RESOURCE_USAGE_SAMPLE_INTERVAL_S)


@app.get("/api/rss")
def rss_feed():
    """Returns the already-aggregated/sorted content of RSS/combined.json
    (see fetch_rss_feeds.py), at the user's explicit request — the "Actu
    Croisée" panel on the home page (frontend/static/script.js) calls it
    once when the page loads. No XML parsing here: fetch_rss_feeds.py
    already did all the work at the time of the daily refresh (see
    _rss_daily_scheduler above) — this route just re-reads an already-
    ready JSON file. If that file doesn't exist yet (no refresh has ever
    run on this installation), returns an empty list rather than an
    error — an empty panel is a perfectly normal, expected state before
    the very first run."""
    combined_path = RSS_DIR / "combined.json"
    if not combined_path.exists():
        return {"fetched_at": None, "items": []}
    try:
        return json.loads(combined_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        logger.exception("rss: echec de lecture de %s", combined_path)
        return {"fetched_at": None, "items": []}


@app.get("/api/scrapp")
def scrapp_links():
    """Exact mirror of `rss_feed()` above, for SCRAPP/combined.json (see
    fetch_grid_links.py) instead of RSS/combined.json — at the user's
    explicit request: "Ajoute les entrées de SCRAPP aux journal de la
    première page." Same "read-only from an already-ready JSON file"
    approach (no re-request to grillesdujour.fr on every call), same
    graceful degradation (empty list) if the file doesn't exist yet or
    can't be read."""
    combined_path = SCRAPP_DIR / "combined.json"
    if not combined_path.exists():
        return {"fetched_at": None, "items": []}
    try:
        return json.loads(combined_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        logger.exception("scrapp: echec de lecture de %s", combined_path)
        return {"fetched_at": None, "items": []}


@app.get("/api/system_info")
def system_info():
    """Best-effort local hardware/model report for the web UI's info
    badge (see frontend/static/script.js) — not on the hot path of any
    grid generation, just a nice-to-have status display, so this is
    computed fresh per request rather than cached: it's cheap (a couple
    of subprocess probes, see backend/system_info.py) and a user could
    plausibly ask about a machine's hardware changing (e.g. hot-swapped
    external GPU) across the lifetime of one long-running server
    process. `interactive_llm_model` is only passed when a genuinely
    separate second instance is actually configured (LLM_BASE_URL_
    INTERACTIVE, see this module's own dual-GPU setup above) — the same
    `interactive_clue_generator is not clue_generator` check already used
    there, so a single-instance machine never double-reports one model as
    if it were two. `embed_on_gpu` mirrors EMBED_N_GPU_LAYERS (see
    run_embed.sh) directly rather than probing the embed server itself,
    same "known-in-advance config, not a hardware guess" reasoning as
    LLAMA_FORCE_CPU in backend/system_info.py."""
    try:
        _embed_gpu_layers = int(os.environ.get("EMBED_N_GPU_LAYERS", "0").strip() or "0")
    except ValueError:
        _embed_gpu_layers = 0
    info = get_system_info(
        clue_generator.model,
        interactive_llm_model=(
            interactive_clue_generator.model if interactive_clue_generator is not clue_generator else None
        ),
        embed_model=_similar_embedder.model,
        embed_on_gpu=_embed_gpu_layers > 0,
    )
    # See EXPERIMENTAL_NOTICE's own comment above.
    info["experimental_notice"] = EXPERIMENTAL_NOTICE
    return info


_LIBRARY_SEEN_FILTERS = ("all", "unseen", "seen", "mine")
_LIBRARY_DIFFICULTY_FILTERS = ("easy", "medium", "hard")


class GridGameSaveRequest(BaseModel):
    """Body of POST /api/game/save — autosave of a player's in-progress
    game on a library grid (see grid_store.save_grid_game), at the user's
    explicit request: "A chaque modification de la grille, sauvegarder
    l'état de la grille dans GRID_GAME avec le nom de l'utilisateur...
    Inclure l'état du compteur temps." `grid_id` must match a genuinely
    stored grid (a GRID_STORE id, see grid_store._GRID_ID_RE); `pseudo`
    is required — the frontend only ever fires this call once a pseudo
    is already set (see script.js's scheduleGridGameSave), but the
    endpoint still revalidates it server-side."""
    grid_id: str
    pseudo: str
    user_letters: list[list[str]]
    elapsed_seconds: int = Field(default=0, ge=0)


class LibraryListRequest(BaseModel):
    """Body of POST /api/library — the same role as the GET route's query
    parameters, but as a POST so it can carry `seen_ids`, which can hold
    thousands of ids (well past what a query string, or a cookie, can
    reasonably take — see frontend/static/script.js, which keeps the
    whole set in localStorage)."""
    preferred_language: str = "fr"
    page: int = 1
    # Language filter, at the user's explicit request ("Par défaut,
    # n'afficher que les grilles dans la langue de l'interface"): "all" ->
    # every language; a code (fr/en/de/es/it) -> only that language.
    # `preferred_language` still drives the sort order (that language
    # first) independently of this filter. The frontend initializes it
    # to the interface language (see #library-language-filter).
    language_filter: str = "all"
    # Difficulty filter, at the user's explicit request: "all" (default,
    # "Tous les niveaux") -> every grid; "easy"/"medium"/"hard" -> only
    # grids of that level. The frontend initializes it to "all" and does
    # not tie it to the interface language.
    difficulty_filter: str = "all"
    # "all" (default): the full list, every grid just annotated seen=…
    # "unseen": only grids absent from seen_ids
    # "seen"  : only ones present in seen_ids
    # "mine"  : only grids whose `pseudo` field matches `pseudo` below
    #           (nothing if `pseudo` is empty), at the user's explicit
    #           request ("ajouter une entrée 'Mes grilles'").
    seen_filter: str = "all"
    # Ids (a GRID_STORE file's own `id` field) of the grids this client
    # has already seen. Defensively bounded — a normal client has at most
    # a few thousand; beyond that it's noise we ignore.
    seen_ids: list[str] = Field(default_factory=list, max_length=100_000)
    # The current user's pseudo — only used when `seen_filter == "mine"`.
    # `None`/empty: the "Mes grilles" filter returns nothing.
    pseudo: Optional[str] = None


def _library_page(preferred_language, page, seen_filter, seen_ids,
                  language_filter="all", difficulty_filter="all", pseudo=None):
    """Shared core of GET and POST /api/library — the list (metadata only,
    never the whole grid: see backend/grid_store.py's list_grids) of
    GRID_STORE/'s grids, sorted with the configured language first, then
    English, then the rest, most recent first within each group, filtered
    by `language_filter`, `difficulty_filter`, then `seen_filter`/
    `seen_ids` (or, for `seen_filter=="mine"`: only grids whose `pseudo`
    matches the given `pseudo`), then paginated by `LIBRARY_PAGE_SIZE`
    (20).

    `list_grids()` itself stays unchanged (always the full sorted list,
    every language); the language filter ("all" or a code), "already
    seen / not yet seen", and pagination are all this route's own
    concern. `preferred_language` only drives the sort order, never the
    filtering. Exception: with `language_filter=="all"`, list_grids()'s
    own language grouping is undone and the list falls back to pure
    reverse-chronological order. Filtering happens BEFORE pagination so
    `total`/the page count reflect the list actually shown. Every
    returned grid also carries `seen` (bool) so the frontend can grey it
    out without re-consulting its own storage. `page` is floored to 1; a
    page past the last one returns an empty list, not an error.

    The "bilingual" filter gets the same grouping cancellation as "all",
    at the user's explicit request: "La liste des grilles de la
    Bibliothèque Bilingue doit être classée dans l'ordre chronologique
    inverse (et non regroupé par langues)." list_grids()'s own grouping
    works on each grid's `language` field (its primary language) — for a
    bilingual grid, that field varies from one grid to the next (fr/en,
    es/it, ...), so this grouping is NEVER a no-op here, unlike the case
    of one specific language (where every row already shares the same
    language)."""
    if seen_filter not in _LIBRARY_SEEN_FILTERS:
        seen_filter = "all"
    # "bilingual" (see GRID_STORE/bilingual/, backend/grid_store.py's
    # save_grid_json) is never a real WORDLISTS key — filtered
    # separately, on each grid's own `bilingual` field rather than its
    # `language` (which always stays its primary language), at the
    # user's explicit request: "ajouter Bilingue dans le sélecteur de
    # langue de la Bibliothèque."
    only_bilingual = language_filter == "bilingual"
    only_language = language_filter if language_filter in WORDLISTS else None
    # Difficulty filter (easy/medium/hard) — "all"/any unknown value lets
    # everything through, at the user's explicit request.
    only_difficulty = (
        difficulty_filter if difficulty_filter in _LIBRARY_DIFFICULTY_FILTERS else None
    )
    seen = set(seen_ids or ())
    # "Mes grilles": filters on each grid's own `pseudo` field, at the
    # user's explicit request. An empty `pseudo` matches nothing (the
    # "Mes grilles" filter is then moot).
    my_pseudo = (pseudo or "").strip()
    rows = []
    for g in list_grids(preferred_language):
        if only_difficulty is not None and g.get("difficulty") != only_difficulty:
            continue
        if only_bilingual:
            if not g.get("bilingual"):
                continue
        elif only_language is not None:
            # A specific language (never "all"/"bilingual" — only_language
            # is only ever set for a real WORDLISTS key) also excludes a
            # bilingual grid whose `language` matches, at the user's
            # explicit request: "quand une seule langue est sélectionnée,
            # ne pas afficher les grilles bilingues" — a bilingual grid
            # then only ever shows through the "Bilingue" filter itself,
            # never mixed into a single-language list even if that
            # language is its own primary one.
            if g.get("language") != only_language or g.get("bilingual"):
                continue
        is_seen = g.get("id") in seen
        if seen_filter == "unseen" and is_seen:
            continue
        if seen_filter == "seen" and not is_seen:
            continue
        if seen_filter == "mine" and (
            not my_pseudo or (g.get("pseudo") or "").strip() != my_pseudo
        ):
            continue
        rows.append({**g, "seen": is_seen})
    # "Toutes les langues" AND "Bilingue" (neither is a real WORDLISTS
    # key): pure reverse-chronological order, without the language
    # grouping list_grids() applies for the default view — at the user's
    # explicit request, "Bilingue" specifically included: "La liste des
    # grilles de la Bibliothèque Bilingue doit être classé dans l'ordre
    # chronologique inverse (et non regroupé par langues)." A filter on a
    # specific language makes this grouping moot anyway (every row already
    # shares the same language), so we only re-sort in this one case.
    if only_language is None:
        rows.sort(key=lambda e: e.get("created_at") or "", reverse=True)
    page = max(1, page)
    start = (page - 1) * LIBRARY_PAGE_SIZE
    return {
        "grids": rows[start:start + LIBRARY_PAGE_SIZE],
        "total": len(rows),
        "page": page,
        "page_size": LIBRARY_PAGE_SIZE,
    }


@app.get("/api/library")
def library_list(preferred_language: str = "fr", page: int = 1):
    """"Bibliothèque" button of the interface — see _library_page. This
    GET variant (no language filter, no notion of already-seen grids) is
    kept for simple access; the frontend uses POST /api/library to
    transmit the language filter and the list of already-seen grids
    (see LibraryListRequest)."""
    return _library_page(preferred_language, page, "all", (), "all")


@app.post("/api/library")
def library_list_filtered(req: LibraryListRequest):
    """Like GET /api/library, but the body carries `language_filter`,
    `seen_filter` + `seen_ids` (see LibraryListRequest): the backend
    filters the list by language and by "already seen", annotates it,
    then paginates it, at the user's explicit request ("Passer les
    grilles déjà vues au Back pour qu'il sache comment gérer la liste à
    transmettre au Front")."""
    return _library_page(
        req.preferred_language, req.page, req.seen_filter, req.seen_ids,
        req.language_filter, req.difficulty_filter, req.pseudo,
    )


@app.get("/api/library/{grid_id}")
def library_get(grid_id: str, pseudo: str = ""):
    """Loads a previously saved grid to play again — returns exactly the
    same shape as a finished job's own `result` (see _run_generate_job),
    plus the library's own metadata (id/title/language/difficulty/mode/
    date), so the frontend can display it through the same code path as a
    generation that just finished (see frontend/static/script.js's
    displayFinalGrid).

    `pseudo` (optional): when given, also looks in GRID_GAME (see
    grid_store.get_grid_game) for a game this player already saved for
    this exact grid, and adds it to the result under `saved_game`
    (`{user_letters, elapsed_seconds}`, or absent/None if nothing was
    found) — at the user's explicit request: "Dans la Librairie, quand un
    utilisateur clique pour jouer sur une grille, chercher si cette grille
    existe dans GRID_GAME pour la recharger et relancer le compteur de
    temps là où il était à la sauvegarde." The frontend (loadLibraryGrid)
    sends the current pseudo on every call; omitted, this field is simply
    absent, with no error — a freshly finished generation never has a
    saved game to look for (its grid_id was just created)."""
    record = get_grid(grid_id)
    if record is None:
        raise HTTPException(status_code=404, detail="grille introuvable dans la bibliothèque")
    pseudo = (pseudo or "").strip()
    if pseudo:
        saved_game = get_grid_game(grid_id, pseudo)
        if saved_game is not None:
            record = {
                **record,
                "saved_game": {
                    "user_letters": saved_game.get("user_letters"),
                    "elapsed_seconds": saved_game.get("elapsed_seconds", 0),
                },
            }
    return record


@app.get("/api/library/{grid_id}/pdf")
async def library_get_pdf(grid_id: str):
    """Downloads a library grid as a printable PDF — an EMPTY grid,
    definitions and title only, never the answers — at the user's
    explicit request. Rendered as SVG (render_puzzle_svg) then converted
    to PDF via `rsvg-convert -f pdf` (svg_to_pdf_bytes)."""
    record = get_grid(grid_id)
    if record is None:
        raise HTTPException(status_code=404, detail="grille introuvable dans la bibliothèque")
    title = (record.get("title") or "").strip()
    try:
        svg = render_puzzle_svg(
            record, record.get("language", "fr"), title, record.get("difficulty")
        )
        pdf_bytes = await asyncio.to_thread(svg_to_pdf_bytes, svg)
    except OSError as exc:
        # `rsvg-convert` missing or failing — the same dependency family
        # as the PNG generation (see svg_export.save_grid_png).
        raise HTTPException(status_code=503, detail=str(exc))
    slug = _slugify_title(title) if title else "grille"
    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{slug}.pdf"'},
    )


@app.post("/api/game/save")
def game_save(req: GridGameSaveRequest):
    """Autosave of the in-progress game (see GridGameSaveRequest /
    grid_store.save_grid_game) — called by the frontend on every grid
    change in play mode (a letter typed or erased), as long as a pseudo
    is set. Always a plain acknowledgement (`{"ok": True}`); never an
    error if no GRID_STORE grid genuinely matches `grid_id` — a game
    state stays valid even for a grid that would, hypothetically, no
    longer be referenced anywhere else (there is no mechanism today to
    delete a grid from the library)."""
    if not save_grid_game(req.grid_id, req.pseudo, req.user_letters, req.elapsed_seconds):
        raise HTTPException(status_code=400, detail="identifiant de grille ou pseudo invalide")
    return {"ok": True}


@app.get("/api/dictionary")
async def dictionary_search(q: str, lang: str = "fr"):
    """Dictionary lookup for the interface's "Dictionnaire" panel, at the
    user's explicit request: given a word (accents and case ignored),
    lists every word sharing the same root drawn from
    data/wordlist_<lang>_full.tsv, each with its real definitions from
    data/gloss_dictionary/<lang>_glosses.jsonl. See
    backend/dictionary_lookup.search — the per-language index is built
    once and then cached (the French wordlist runs to ~200k lines), hence
    running it via asyncio.to_thread."""
    if lang not in WORDLISTS:
        raise HTTPException(status_code=400, detail=f"langue inconnue : {lang!r}")
    return await asyncio.to_thread(dictionary_search_impl, q, lang)


# Difficulty used for the "Définir" button below — the Dictionary panel
# has no difficulty selector of its own (unlike the grid-generation form),
# so this is fixed rather than exposed as a new UI control; "medium"
# matches the grid's own default difficulty style (reworded, a little
# indirect, still fair — see backend/clues.py's DIFFICULTY_STYLE).
DEFINE_DIFFICULTY = "medium"
DEFINE_COUNT = 10


@app.get("/api/dictionary/define")
async def dictionary_define(q: str, lang: str = "fr", theme: str = ""):
    """"Définir" button of the Dictionnaire panel (see frontend/static/
    script.js): asks the LLM for up to DEFINE_COUNT (10) independent
    definitions of the typed expression, the same way as a grid word
    (backend/clues.py, LLMClueGenerator.generate_definitions — the same
    real dictionary/example grounding, the same content filter — but a
    single best-effort call, with none of a grid generation's own
    per-word retry loop). A ClueGenerationError (LLM unreachable) becomes
    a clean 503; the rest of the UI is unaffected.

    `theme` (optional, "" by default) is the current content of the
    "Thématique" field of Interactive mode — at the user's explicit
    request ("Vérifier que le bouton 'Propose une définition' utilise
    bien le champ thématique pour les propositions quand il est
    renseigné") — that mode's "Proposer" (and "Définitions") button
    always sends it whenever this field isn't empty (see
    frontend/static/script.js's `dictionaryDefineUrl`). The generic
    Dictionnaire panel itself never sends this parameter: with no grid in
    progress, there's no theme to pass along."""
    if lang not in WORDLISTS:
        raise HTTPException(status_code=400, detail=f"langue inconnue : {lang!r}")
    text = q.strip()
    if not text:
        raise HTTPException(status_code=400, detail="expression vide")
    try:
        definitions = await asyncio.to_thread(
            interactive_clue_generator.generate_definitions, text, lang, DEFINE_DIFFICULTY, DEFINE_COUNT,
            timeout=90.0, theme_description=theme.strip() or None,
        )
    except ClueGenerationError as exc:
        logger.warning("dictionary_define unavailable: %s", exc)
        raise HTTPException(
            status_code=503,
            detail={"code": "define_unavailable", "message": str(exc)},
        )
    return {"query": text, "lang": lang, "definitions": definitions}


# "Paraphraseur" panel, at the user's explicit request — mirrors "Définir"
# above (fixed count, a single best-effort LLM call, no difficulty
# selector of its own since this panel has none either).
PARAPHRASE_COUNT = 5
PARAPHRASE_TIMEOUT_S = 90.0


@app.get("/api/paraphrase")
async def paraphrase(q: str, lang: str = "fr"):
    """"Paraphraser" button of the "Paraphraseur" panel (see frontend/
    static/script.js), at the user's explicit request: asks the LLM for
    PARAPHRASE_COUNT (5) independent rewordings of the typed text
    (backend/clues.py, LLMClueGenerator.generate_paraphrases — a single
    best-effort call, with no dictionary/example grounding and none of a
    grid generation's own per-word retry loop). A ClueGenerationError
    (LLM unreachable) becomes a clean 503."""
    if lang not in WORDLISTS:
        raise HTTPException(status_code=400, detail=f"langue inconnue : {lang!r}")
    text = q.strip()
    if not text:
        raise HTTPException(status_code=400, detail="texte vide")
    try:
        paraphrases = await asyncio.to_thread(
            interactive_clue_generator.generate_paraphrases, text, lang, PARAPHRASE_COUNT,
            timeout=PARAPHRASE_TIMEOUT_S,
        )
    except ClueGenerationError as exc:
        logger.warning("paraphrase unavailable: %s", exc)
        raise HTTPException(
            status_code=503,
            detail={"code": "paraphrase_unavailable", "message": str(exc)},
        )
    return {"query": text, "lang": lang, "paraphrases": paraphrases}


# Length window (in letters, counted on the wordlist's own ACCENTUE
# column) for the random "indicative word" GET /api/theme/random embeds
# into the LLM prompt (see LLMClueGenerator.generate_random_theme's own
# docstring for what it's for) — short enough to still read as an
# ordinary word, long enough to skew away from bare function words.
RANDOM_THEME_WORD_MIN_LEN = 5
RANDOM_THEME_WORD_MAX_LEN = 10


def _random_dictionary_word(language, min_len=RANDOM_THEME_WORD_MIN_LEN,
                             max_len=RANDOM_THEME_WORD_MAX_LEN):
    """One word drawn uniformly at random from data/wordlist_<language>_
    full.tsv's own ACCENTUE column (its natural accented/inflected
    spelling — see CLAUDE.md's data pipeline section), restricted to
    entries whose length in letters falls in [min_len, max_len].
    Reservoir sampling over the file — never loads the whole (up to
    ~200k-line) file into memory — so this stays cheap even for the
    largest wordlist. Returns None if the language is unknown, the file
    can't be read, or no entry in range was found."""
    wordlist_path = WORDLISTS.get(language)
    if not wordlist_path:
        return None
    chosen = None
    seen = 0
    try:
        with open(wordlist_path, encoding="utf-8") as f:
            for line in f:
                stripped = line.rstrip("\n")
                if not stripped or stripped.startswith("#"):
                    continue
                columns = stripped.split("\t")
                if len(columns) < 2:
                    continue
                word = columns[1]
                if not (min_len <= len(word) <= max_len):
                    continue
                seen += 1
                if random.randint(1, seen) == 1:
                    chosen = word
    except OSError:
        return None
    return chosen


@app.get("/api/theme/random")
async def random_theme(lang: str = "fr"):
    """Random-theme feature backing Automation/Populate.py: picks a
    random dictionary word (RANDOM_THEME_WORD_MIN_LEN to RANDOM_THEME_
    WORD_MAX_LEN letters) from `lang`'s own wordlist and asks the LLM
    (LLMClueGenerator.generate_random_theme) to invent an original
    crossword theme, folding that word into the prompt as a randomness
    seed. Not used by the web UI itself — Populate.py is the only
    caller — but proxied through frontend/server.py like every other
    backend endpoint.

    Returns {language, hint_word, theme} — `theme` is "" if the LLM call
    failed or never returned anything usable; the caller then falls back
    to an ordinary, un-themed generation, exactly like a request with no
    typed theme."""
    if lang not in WORDLISTS:
        raise HTTPException(status_code=400, detail=f"langue inconnue : {lang!r}")
    hint_word = await asyncio.to_thread(_random_dictionary_word, lang)
    theme = await asyncio.to_thread(
        interactive_clue_generator.generate_random_theme, lang, hint_word,
    )
    return {"language": lang, "hint_word": hint_word, "theme": theme}


def _iter_scored_words(vec, lang: str, min_score: float = THEME_MIN_SCORE):
    """Yield `(word, score)` from the `lang` tenant of the Qdrant "words"
    collection, most-similar-first, stopping the moment a hit's score
    drops below `min_score` (Qdrant hits are score-descending, so nothing
    later could ever qualify) or the tenant is exhausted. Pages
    THEME_LENGTH_SEARCH_PAGE points at a time via `QdrantStore.search`'s
    `offset` — no depth cap and no count cap, the score threshold is the
    SOLE gate. De-duplicates words across pages. This is the one place the
    threshold + pagination logic lives: both the themed grid glossary
    (`_theme_words_by_length`, whose caller may pass a per-generation
    `min_score` from GenerateRequest.theme_precision) and the Dictionary
    panel's "Thématique" button (`_similar_words_impl`, always the default
    THEME_MIN_SCORE) consume it, so they can never disagree on how the
    cutoff is applied."""
    seen: set[str] = set()
    offset = 0
    while True:
        hits = _similar_qdrant.search(
            vec, lang=lang, limit=THEME_LENGTH_SEARCH_PAGE, offset=offset,
        )
        if not hits:
            return
        for hit in hits:
            score = hit.get("score")
            if score is not None and score < min_score:
                return
            word = (hit.get("payload") or {}).get("word")
            if word and word not in seen:
                seen.add(word)
                yield word, score
        offset += len(hits)
        if len(hits) < THEME_LENGTH_SEARCH_PAGE:
            return  # tenant exhausted


def _compiled_similar_words(keywords: list[str], lang: str,
                            min_score: float = THEME_MIN_SCORE) -> list[tuple[str, float]]:
    """Like `_compiled_theme_words_by_length` but for the Dictionnaire
    panel: runs a Qdrant nearest-neighbor search for EVERY keyword in
    `keywords` (embedding the bare keyword + `_iter_scored_words`) and
    merges them — each word keeps its BEST score. Two differences from the
    "grid glossary" version: (1) no 3-15 length filter (a dictionary
    lookup shouldn't drop long words); (2) sorted by DESCENDING score (the
    panel's own "most similar first" order), not by length. A keyword
    whose Qdrant search fails is skipped; the error only propagates as
    long as no search has succeeded yet (Qdrant/embedder genuinely
    unavailable -> 503)."""
    merged: dict[str, float] = {}
    any_ok = False
    for kw in keywords:
        try:
            vec = _similar_embedder.embed(kw)
            pairs = list(_iter_scored_words(vec, lang, min_score))
        except (QdrantStoreError, EmbedderError):
            if not any_ok:
                raise
            continue
        any_ok = True
        for word, score in pairs:
            if word not in merged:
                merged[word] = score
            elif score is not None and (merged[word] is None or score > merged[word]):
                merged[word] = score
    return sorted(merged.items(), key=lambda pair: -(pair[1] or 0.0))


def _synonyms_impl(query: str, lang: str,
                    min_score: float = THEME_MIN_SCORE) -> list[tuple[str, float]]:
    """"Synonymes" button of the Dictionnaire panel, at the user's
    explicit request: "un bouton 'Synonymes' qui lance une recherche
    Qdrant avec le mot ou l'expression saisie (sans faire appel au LLM
    pour étendre la recherche, comme le fait Thématique)." Unlike
    `_similar_words_impl` right below (which first asks `describe_theme`
    for a list of about thirty keywords before running a Qdrant search
    per keyword), this function directly reuses `_compiled_similar_words`
    with the raw query as the SOLE keyword — for a single entry, this
    function reduces exactly to "embed the query as typed, search Qdrant,
    sort by descending score": no expansion, no LLM call, so no risk of
    thematic drift at all (a "cat" search stays a search for the word
    "cat" itself, never widened to its own lexical field). Same
    `min_score` threshold/same sort convention as `_similar_words_impl`,
    so the panel can reuse the exact same rendering (`renderSimilarWords
    Result`) with no distinction."""
    return _compiled_similar_words([query], lang, min_score)


def _similar_words_impl(query: str, lang: str,
                        min_score: float = THEME_MIN_SCORE) -> tuple[list[str], list[tuple[str, float]]]:
    """Blocking. Applies the themed-grid-glossary principle to the
    Dictionary panel, at the user's explicit request ("appliquer le même
    principe que pour la génération du glossaire thématique : demander au
    LLM de générer des listes de mots dans le thème du mot cherché, avant
    de compiler les recherches Qdrant"): the typed `query` is first
    expanded by the LLM (`describe_theme`) into a ~30-word telegraphic
    keyword list spanning every part of speech, split into individual
    keywords (`_split_keywords`, case-insensitively de-duplicated), and
    `_compiled_similar_words` runs one Qdrant nearest-words search per
    keyword, each embedded bare (no surrounding context — an earlier
    version prefixed every keyword with the raw `query`, diluting a sharp
    keyword's own embedding with the query's other words and shrinking
    the compiled glossary; removed at the user's explicit request), and
    merges the results (best score per word). Falls back to the raw
    `query` as the sole keyword if the LLM call fails or returns nothing
    (same fallback shape as `_run_generate_job`'s own theme block).

    Every kept `(word, score)` has a similarity >= `min_score` (the
    "Précision thématique" form field's current value, forwarded as the
    `min_score` query param; default THEME_MIN_SCORE). No count limit and
    no length filter (unlike the grid glossary's 3-15 bound). Returned
    most-similar-first; the score is shown to 2 decimals next to each word
    in the panel.

    Returns `(keywords, scored_words)`: `keywords` is the LLM's own raw
    expansion list, shown in the panel under "Champ lexical" ahead of the
    Qdrant-compiled `scored_words`, shown under "Glossaire thématique" —
    at the user's explicit request to surface both stages of the search
    rather than only the final compiled list."""
    desc = ""
    try:
        desc = interactive_clue_generator.describe_theme(
            query, lang, timeout=_SIMILAR_DESCRIBE_TIMEOUT_S,
        )
    except Exception as exc:  # noqa: BLE001 — best-effort, on retombe sur le mot brut
        logger.warning(
            "similar_words: theme expansion failed (%s) — searching the raw query", exc,
        )
        desc = ""
    keywords: list[str] = []
    seen: set[str] = set()
    for kw in _split_keywords(desc, lang):
        k = kw.lower()
        if k not in seen:
            seen.add(k)
            keywords.append(kw)
    if not keywords:
        keywords = [query]
    logger.info(
        "similar_words: %r -> %d keywords (min_score=%s)", query, len(keywords), min_score,
    )
    return keywords, _compiled_similar_words(keywords, lang, min_score)


def _theme_words_by_length(query: str, lang: str,
                           min_score: float = THEME_MIN_SCORE) -> list[tuple[str, float]]:
    """Blocking: builds the themed-generation glossary (see
    THEME_LENGTH_MIN/MAX/THEME_MIN_SCORE above) by embedding the bare
    `query` keyword itself (no surrounding context — see
    `_compiled_theme_words_by_length`'s own docstring for why), then
    walking the `lang` tenant's own ranked nearest-neighbor
    list via `_iter_scored_words` — the shared threshold/pagination
    helper, which stops the moment a hit's score drops below `min_score`
    (the per-generation GenerateRequest.theme_precision, defaulting to the
    THEME_MIN_SCORE constant) or the tenant is exhausted, with no depth
    cap and no count cap. This
    function then keeps only the words whose length falls within
    THEME_LENGTH_MIN..THEME_LENGTH_MAX (`payload.word`'s own length, the
    bare accent-stripped MOT form crossword slots use) — that length
    bound is the ONLY thing it adds on top of `_iter_scored_words`; the
    Dictionary panel's "Thématique" button (`_similar_words_impl`) uses
    the exact same helper without it.

    Returns a flat list of `(word, score)` pairs, sorted by increasing
    word length — at the user's explicit request ("continuer à les
    lister par taille de mots croissante") — with score-descending order
    preserved within each length (the order each page already returns
    them in; the sort is stable, so this ordering survives the final
    re-sort by length untouched). `score` is Qdrant's own cosine-
    similarity result for that point (higher = closer to the theme),
    kept alongside the word at the user's explicit request ("afficher
    les scores de chaque mot produit par Qdrant" in LOG_THEME/, see
    `_write_theme_log`). The caller (`crossword_gen.py`'s
    `priority_words`) only ever treats this as an unordered preference
    set of *words*, so neither the score nor the length-sorted order is
    a ranking guarantee there — both exist purely for LOG_THEME/'s own
    readability."""
    vec = _similar_embedder.embed(query)
    words = [
        (word, score)
        for word, score in _iter_scored_words(vec, lang, min_score)
        if THEME_LENGTH_MIN <= len(word) <= THEME_LENGTH_MAX
    ]
    return sorted(words, key=lambda pair: len(pair[0]))


def _split_keywords(text: str, lang: Optional[str] = None) -> list[str]:
    """Splits a `describe_theme` reply (a telegraphic list of about 30
    comma-separated keywords) into individual keywords — each will then
    run its own Qdrant nearest-neighbor search (see `_run_generate_job`'s
    own theme block and `_compiled_theme_words_by_length`). Splits on
    commas, semicolons and newlines; trims each piece; drops anything
    under 2 characters. Order is kept, no de-duplication here (the caller
    flattens and de-duplicates across every list).

    At the user's explicit request ("si le LLM ne met pas de virgule,
    découpe tous les mots"): the small local models occasionally ignore
    the comma-separated-list instruction entirely and reply with one
    space-separated blob instead — left as-is, that blob would become a
    single ~30-word "keyword" (the exact dilution problem splitting
    exists to avoid, see the module comment above THEME_LENGTH_MIN). When
    no comma/semicolon/newline was found at all, this falls back to
    splitting the whole reply into individual words (`_THEME_TOKEN_RE`,
    the same tokenizer used on the user's own typed theme).

    `lang` (also at the user's explicit request: "supprime les mots creux
    : le, la, les, de, du, ce, cela, etc.") drops any resulting piece that
    IS one of that language's stopwords (`clues._LANGUAGE_STOPWORDS_RAW`
    — the same function-word lists `LLMClueGenerator` uses to detect a
    reply written in the wrong language), a pure noise word that would
    otherwise run its own, useless Qdrant search. `None` (the default)
    applies no filtering — every call site passes its own `lang`/
    `language`."""
    pieces = [
        kw for piece in re.split(r"[,;\n]+", text or "")
        if len(kw := piece.strip().strip(".").strip()) >= 2
    ]
    if len(pieces) <= 1:
        pieces = _THEME_TOKEN_RE.findall(text or "")
    stopwords = _LANGUAGE_STOPWORDS_RAW.get(lang) if lang else None
    if stopwords:
        pieces = [kw for kw in pieces if kw.lower() not in stopwords]
    return pieces


# A significant "word" of the "Thématique" field: a run of letters (an
# internal apostrophe/hyphen tolerated — "l'agriculture", "mots-croisés"
# each count as a single word), at least 2 letters long.
_THEME_TOKEN_RE = re.compile(r"[^\W\d_]+(?:['’\-][^\W\d_]+)*", re.UNICODE)


def _theme_tokens(theme: str) -> list[str]:
    """The distinct words of the theme description the user typed —
    case-insensitively de-duplicated (first spelling kept), at least 2
    letters each. At the user's explicit request: "Lorsqu'il y a
    plusieurs mots dans la définition thématique donnée par l'utilisateur,
    compiler les glossaires thématiques pour chacun des mots" — see
    `_compiled_theme_words_by_length` and `_run_generate_job`'s own theme
    block."""
    seen: set[str] = set()
    out: list[str] = []
    for tok in _THEME_TOKEN_RE.findall(theme or ""):
        if len(tok) < 2:
            continue
        key = tok.lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(tok)
    return out


def _compiled_theme_words_by_length(keywords: list[str], lang: str,
                                    min_score: float = THEME_MIN_SCORE) -> list[tuple[str, float]]:
    """Runs `_theme_words_by_length` for EVERY keyword in `keywords` and
    merges the resulting glossaries — each word keeps the BEST (highest)
    score seen across every search. At the user's explicit request:
    "Compiler toutes les recherches dans Qdrant pour tous les mots de ces
    listes (dédoublonner les mots)." `keywords` is the flat, already
    de-duplicated list of keywords extracted from the LLM-produced lists
    (see `_split_keywords` and `_run_generate_job`'s own theme block): a
    single keyword is a much sharper query than one embedding averaged
    over a ~30-word sentence — each is searched bare, with no surrounding
    context, so it stays that sharp (an earlier version prefixed every
    keyword with the complete typed theme text, at the user's own
    then-request, to keep a drifting keyword grounded in the original
    input; removed at the user's later, explicit follow-up request once it
    was found to be diluting even an on-topic keyword's own embedding and
    shrinking the compiled glossary — see `_build_theme_glossary`). Each
    search applies the `min_score` threshold (the form's "Précision
    thématique" field — GenerateRequest.theme_precision —, defaulting to
    the THEME_MIN_SCORE constant) via `_theme_words_by_length`.

    The result is re-sorted by increasing word length then descending
    score — exactly the order a single call to `_theme_words_by_length`
    already returns (see `_write_theme_log`). A keyword whose Qdrant
    search fails is skipped; the error only propagates as long as no
    search has succeeded yet (Qdrant/embedder genuinely unavailable →
    generation then proceeds with no theme)."""
    merged: dict[str, float] = {}
    any_ok = False
    for kw in keywords:
        try:
            pairs = _theme_words_by_length(kw, lang, min_score)
        except (QdrantStoreError, EmbedderError):
            if not any_ok:
                raise
            continue
        any_ok = True
        for word, score in pairs:
            if word not in merged:
                merged[word] = score
            elif score is not None and (merged[word] is None or score > merged[word]):
                merged[word] = score
    return sorted(merged.items(), key=lambda pair: (len(pair[0]), -(pair[1] or 0.0)))


@app.get("/api/similar_words")
async def similar_words(q: str, lang: str = "fr", min_score: float = THEME_MIN_SCORE):
    """"Thématique" button of the Dictionnaire panel (see frontend/static/
    script.js): EVERY word of the Qdrant "words" collection whose
    similarity score with the typed expression reaches `min_score` — the
    current value of the form's "Précision thématique" field, sent by the
    frontend, at the user's explicit request ("Dictionnaire / Thématique
    ... doit être sensible à la modification du paramètre Précision
    thématique"); defaults to the THEME_MIN_SCORE constant when the field
    is empty. Restricted to the panel's own language tenant, most similar
    first. No count cap at all: the score threshold is the only limit.
    Embedding and vector search are blocking, hence asyncio.to_thread.
    Returns a clean 503 (code "similar_unavailable") if Qdrant or the
    embedding server isn't running, or if the collection hasn't been
    populated yet (python -m data_builder.qdrant_populate --all)."""
    if lang not in WORDLISTS:
        raise HTTPException(status_code=400, detail=f"langue inconnue : {lang!r}")
    query = q.strip()
    if not query:
        raise HTTPException(status_code=400, detail="expression vide")
    min_score = max(0.0, min(1.0, min_score))
    try:
        keywords, scored = await asyncio.to_thread(_similar_words_impl, query, lang, min_score)
    except (QdrantStoreError, EmbedderError) as exc:
        logger.warning("similar_words unavailable: %s", exc)
        raise HTTPException(
            status_code=503,
            detail={"code": "similar_unavailable", "message": str(exc)},
        )
    # `words`: one {word, score} object per entry (the Qdrant score is
    # shown in parentheses next to each word in the Dictionnaire panel,
    # at the user's explicit request). `keywords`: the LLM's own raw
    # expansion list ("Champ lexical" in the panel), ahead of `words`
    # ("Glossaire thématique" — the Qdrant-compiled list).
    words = [{"word": w, "score": s} for w, s in scored]
    return {"query": query, "lang": lang, "keywords": keywords, "words": words}


@app.get("/api/synonyms")
async def synonyms(q: str, lang: str = "fr", min_score: float = THEME_MIN_SCORE):
    """"Synonymes" button of the Dictionnaire panel: a direct Qdrant
    search on `q` (a raw embedding, no LLM call), unlike the "Thématique"
    button (`/api/similar_words`) which first expands the search via
    `describe_theme` — see `_synonyms_impl`. Same request/response shape
    as `/api/similar_words`, reusable as-is by the same frontend
    rendering."""
    if lang not in WORDLISTS:
        raise HTTPException(status_code=400, detail=f"langue inconnue : {lang!r}")
    query = q.strip()
    if not query:
        raise HTTPException(status_code=400, detail="expression vide")
    min_score = max(0.0, min(1.0, min_score))
    try:
        scored = await asyncio.to_thread(_synonyms_impl, query, lang, min_score)
    except (QdrantStoreError, EmbedderError) as exc:
        logger.warning("synonyms unavailable: %s", exc)
        raise HTTPException(
            status_code=503,
            detail={"code": "similar_unavailable", "message": str(exc)},
        )
    words = [{"word": w, "score": s} for w, s in scored]
    return {"query": query, "lang": lang, "words": words}


# ----------------------------------------------------------------------
# Qdrant admin — the "Qdrant (admin)" panel in the web UI (localhost only:
# the frontend hides its button off localhost, and frontend/server.py
# rejects the proxy routes for a non-loopback client / Host). The backend
# itself only ever sees the proxy as its client, so it does no localhost
# check of its own — the gate is entirely at the proxy.
# ----------------------------------------------------------------------
def _qdrant_admin_impl() -> dict:
    """Blocking: full read-only state of the Qdrant "words" collection —
    reachability, vector config, point/index counts, tenant index, and a
    per-language point count."""
    store = _similar_qdrant
    out = {
        "base_url": store.base_url,
        "collection": store.collection,
        "dashboard_url": f"{store.base_url}/dashboard",
    }
    if not store.ping():
        out["reachable"] = False
        return out
    out["reachable"] = True
    if not store.collection_exists():
        out["exists"] = False
        return out
    out["exists"] = True
    info = store.collection_info()
    vec = info.get("config", {}).get("params", {}).get("vectors", {}) or {}
    out["vector"] = {
        "size": vec.get("size"),
        "distance": vec.get("distance"),
        "on_disk": vec.get("on_disk"),
    }
    out["status"] = info.get("status")
    out["optimizer_status"] = info.get("optimizer_status")
    out["points_count"] = info.get("points_count")
    out["indexed_vectors_count"] = info.get("indexed_vectors_count")
    out["segments_count"] = info.get("segments_count")
    lang_schema = (info.get("payload_schema") or {}).get("lang", {}) or {}
    out["tenant_index"] = bool(lang_schema) and (
        (lang_schema.get("params") or {}).get("is_tenant") is True
        or lang_schema.get("data_type") == "keyword"
    )
    counts = {}
    for code in WORDLISTS:
        try:
            counts[code] = store.count(code)
        except QdrantStoreError:
            counts[code] = None
    out["languages"] = counts
    return out


@app.get("/api/qdrant/admin")
async def qdrant_admin():
    """Read-only state of the Qdrant vector store, for the interface's
    "Qdrant (admin)" panel (localhost-only — see frontend/server.py).
    Always returns 200: an unreachable Qdrant or a missing collection are
    both reported via `reachable`/`exists`."""
    return await asyncio.to_thread(_qdrant_admin_impl)


class QdrantTenantRequest(BaseModel):
    lang: str


@app.post("/api/qdrant/admin/recreate")
async def qdrant_admin_recreate():
    """Drops then recreates the "words" collection (+ re-indexes the
    `lang` tenant). Fast, but destructive: empties every language.
    Populating it again is then done via
    `python -m data_builder.qdrant_populate --all`."""
    try:
        info = await asyncio.to_thread(
            lambda: _similar_qdrant.ensure_collection(recreate=True)
        )
    except (QdrantStoreError, EmbedderError) as exc:
        logger.warning("qdrant_admin recreate failed: %s", exc)
        raise HTTPException(
            status_code=503,
            detail={"code": "qdrant_unavailable", "message": str(exc)},
        )
    return {"ok": True, "points_count": info.get("points_count", 0)}


@app.post("/api/qdrant/admin/delete-tenant")
async def qdrant_admin_delete_tenant(req: QdrantTenantRequest):
    """Deletes every vector of one language (a tenant) from the "words"
    collection. Fast."""
    if req.lang not in WORDLISTS:
        raise HTTPException(status_code=400, detail=f"langue inconnue : {req.lang!r}")
    try:
        await asyncio.to_thread(_similar_qdrant.delete_lang, req.lang)
        remaining = await asyncio.to_thread(_similar_qdrant.count, req.lang)
    except (QdrantStoreError, EmbedderError) as exc:
        logger.warning("qdrant_admin delete-tenant failed: %s", exc)
        raise HTTPException(
            status_code=503,
            detail={"code": "qdrant_unavailable", "message": str(exc)},
        )
    return {"ok": True, "lang": req.lang, "remaining": remaining}


class ChatMessage(BaseModel):
    role: str
    content: str


class ChatRequest(BaseModel):
    """"David FALCON" chat widget (see frontend/static/script.js), at the
    user's explicit request. `history` never includes the system prompt
    itself (rebuilt fresh server-side every call, see backend/chatbot.py)
    — just the conversation's own prior user/assistant turns, oldest
    first. `ui_context` is the frontend's own live state snapshot; every
    field is optional so a request sent before any grid has ever been
    generated still works (just without grid-specific grounding).

    `session_id` (optional — a request with none still works, just logged
    under a freshly-generated fallback id instead of a real per-tab one),
    at the user's explicit request: "Pour chaque discussion dans le
    ChatBot, crée un LOG des questions/réponses dans un dossier LOG_CHAT...
    Un fichier par session utilisateur." A stable, opaque id the frontend
    generates once per page load/chat-widget lifetime (see script.js's own
    `chatSessionId`) and resends on every message of that same
    conversation — this is what lets `_chat_log_path_for_session` route
    every turn of one conversation to the same log file rather than a new
    one per message."""
    message: str
    history: list[ChatMessage] = Field(default_factory=list)
    language: str = Field(default="fr", description="fr, en, de, es ou it")
    ui_context: dict = Field(default_factory=dict)
    session_id: Optional[str] = None


@app.post("/api/chat")
async def chat(req: ChatRequest):
    """A reply from "David FALCON", streamed to the player as it's
    written, at the user's explicit request: "Le Bot doit afficher la
    réponse en streaming." Deliberately not the job/polling pattern
    POST /api/generate uses — one chat message is a single, comparatively
    quick LLM call, and streaming it directly is both simpler and gives
    faster-feeling feedback than polling a job status would (see backend/
    chatbot.py's own module docstring for why this never goes through
    GRID_QUEUE/CLUES_QUEUE either).

    A plain `text/event-stream` of `data: {"delta": "..."}\\n\\n` chunks
    (one per piece of text ChatBot.reply_stream() yields), terminated by
    `data: [DONE]\\n\\n` — or, if the LLM call itself fails before any
    chunk was ever produced, a single `data: {"error": "..."}\\n\\n`
    instead. Deliberately a bespoke, minimal event shape rather than
    mirroring the OpenAI streaming format verbatim — the frontend
    (frontend/static/script.js) is the only consumer, so there's no
    compatibility reason to match that format, only a reason to keep the
    frontend's own parsing as simple as possible.

    Every full question/answer turn is also appended to this session's own
    LOG_CHAT/ file (see `_append_chat_log`), at the user's explicit
    request: "Pour chaque discussion dans le ChatBot, crée un LOG des
    questions/réponses dans un dossier LOG_CHAT... Un fichier par session
    utilisateur." The reply text is accumulated chunk by chunk as it
    streams (`full_reply`), and the log write happens once the stream is
    fully done (success or `ChatError`) — never per-chunk, since only the
    complete reply is meaningful to log. A failure logs whatever partial
    reply had already streamed (possibly empty), tagged as such, rather
    than silently dropping the exchange from the log. When the "chat
    debug" option is on (`CHATBOT_DEBUG`), the log also carries the exact
    full prompt sent to the LLM (system prompt + history + question),
    captured via `reply_stream`'s `on_prompt` callback so it's recorded
    even if the LLM call then fails.

    Also times each reply, at the user's own later explicit request:
    "noter le temps de récupération du premier mot, et le temps total de
    génération de la réponse." `time.monotonic()` (never `time.time()` —
    a wall clock can jump on an NTP adjustment, corrupting a duration
    computed by plain subtraction), same convention already established
    for `crossword_gen.py`'s own generation/optimization/clue durations.
    `first_token_s` is only ever set once, on the very first chunk
    actually yielded — stays `None` if the call fails before streaming
    anything at all."""
    async def event_stream():
        full_reply = []
        start = time.monotonic()
        first_token_s = None
        # Only populated (and only logged) when the "chat debug" option is
        # on — the full messages array reply_stream() actually sends.
        captured_prompt = []

        def _capture_prompt(messages):
            captured_prompt[:] = messages

        try:
            async for chunk in interactive_chatbot.reply_stream(
                [m.model_dump() for m in req.history], req.message, req.language, req.ui_context,
                on_prompt=_capture_prompt if CHATBOT_DEBUG else None,
            ):
                if first_token_s is None:
                    first_token_s = time.monotonic() - start
                full_reply.append(chunk)
                yield f"data: {json.dumps({'delta': chunk})}\n\n"
            total_s = time.monotonic() - start
            yield "data: [DONE]\n\n"
            _append_chat_log(
                req.session_id, req.language, req.message, "".join(full_reply),
                first_token_s, total_s, captured_prompt or None,
            )
        except ChatError as e:
            total_s = time.monotonic() - start
            yield f"data: {json.dumps({'error': str(e)})}\n\n"
            reply_so_far = "".join(full_reply)
            _append_chat_log(
                req.session_id, req.language, req.message,
                f"{reply_so_far}\n\n*(échec en cours de réponse : {e})*" if reply_so_far
                else f"*(échec : {e})*",
                first_token_s, total_s, captured_prompt or None,
            )

    return StreamingResponse(event_stream(), media_type="text/event-stream")


def _load_wordlist_raw_lines(language):
    """{MOT: raw TSV line, exactly as written in data/wordlist_<language>_
    full.tsv} — used by _build_word_verification_table (column 2) to show
    the file's real content verbatim, not a reconstruction from a few
    parsed fields. A MOT can legitimately repeat on disk (build_wordlist_
    freq.py only dedupes by keeping the highest-frequency occurrence in
    memory, never on disk) — the first line seen is kept, matching that
    same highest-frequency-first convention closely enough for a
    diagnostic table (this file is not re-sorted here)."""
    wordlist_path = WORDLISTS.get(language)
    lines = {}
    if not wordlist_path:
        return lines
    try:
        with open(wordlist_path, encoding="utf-8") as f:
            for line in f:
                stripped = line.rstrip("\n")
                if not stripped or stripped.startswith("#"):
                    continue
                mot = stripped.split("\t", 1)[0].upper()
                lines.setdefault(mot, stripped)
    except OSError:
        pass
    return lines


def _load_gloss_raw_lines(language):
    """{lemma_lower: raw JSON Lines entry, exactly as written in data/
    gloss_dictionary/<language>_glosses.jsonl} — mirrors backend/gloss_
    lookup.py's own _load() index (same lemma-lowercased key, same one-
    bucket-per-lemma file shape from build_gloss_dictionary.py) but keeps
    each entry's exact original line text rather than the parsed dict,
    since _build_word_verification_table (column 3) shows the file's real
    content verbatim, not a re-serialization of it."""
    path = DATA_DIR / "gloss_dictionary" / f"{language}_glosses.jsonl"
    lines = {}
    if not path.exists():
        return lines
    with open(path, encoding="utf-8") as f:
        for line in f:
            stripped = line.strip()
            if not stripped:
                continue
            try:
                entry = json.loads(stripped)
            except json.JSONDecodeError:
                continue
            word = entry.get("word")
            if word:
                lines.setdefault(word.lower(), stripped)
    return lines


def _build_word_verification_table(words, language, bilingual_language=None):
    """Diagnostic table built right before clue generation starts (see
    _run_generate_job's own `progress("clues", ...)` call), at the user's
    explicit request: one row per grid word, sorted top-to-bottom then
    left-to-right (reading order: by starting row, then starting column;
    "across" before "down" for the rare case where both start at the same
    cell), checking two things a legitimately placed word should always
    satisfy.

    Column 2 ("wordlist entry"): whether the word's bare, accent-stripped
    grid spelling (`w["answer"]`) really exists as a MOT entry in
    data/wordlist_<language>_full.tsv — the exact same dictionary
    crossword_gen.py's solver draws every candidate from — and, when it
    does, the *entire, verbatim TSV line* for that entry (MOT/ACCENTUE/
    FREQUENCE/CANONIQUE together), not just the word's own accented
    spelling. A missing entry is the one directly visible symptom of the
    rare "invented word" edge case documented in CLAUDE.md (a slot
    completed purely by its crossing assignments, never itself validated
    against the real dictionary) — this table exists specifically so that
    residual bug, if it ever recurs, is immediately visible on screen
    rather than silently shipped in a finished grid. Read directly from
    the TSV file (`_load_wordlist_raw_lines`) rather than reusing
    crossword_gen.py's own in-memory `accents`/`canonicals` dicts (not
    returned by generate_grid() at all) — this also makes the check
    genuinely independent of whatever difficulty-based subset the solver
    happened to restrict itself to for this one request.

    Column 3 ("root form"): among the word's candidate canonical form(s)
    (`w["canonical"]`, already computed by crossword_gen.py via Hunspell's
    morphological analysis — see build_wordlist_freq.py), the *entire,
    verbatim JSON Lines entry* (`_load_gloss_raw_lines`) for each one that
    has a real entry in data/gloss_dictionary/<language>_glosses.jsonl —
    never looked up at all for a word that already failed the column-2
    check, since an invented word's own "canonical form" carries no
    meaningful information either.

    Returns a plain list of dicts (JSON-safe, ready for a progress event):
    {row, col, direction, answer, in_wordlist, wordlist_line, gloss_lines}
    — `direction` ("across"/"down") is carried through unchanged from
    `w["direction"]` (see crossword_gen.py's `build_word_entries`) so the
    frontend can prefix each coordinate with H/V (frontend/static/
    script.js's `renderWordTable()`)."""
    # On a bilingual grid, `bilingual_language` is the language of the
    # down words (see crossword_gen.generate_grid's own `bilingual_
    # language`) — so each word is checked against ITS OWN LANGUAGE'S
    # dictionary (`w.get("language", language)`, already set by
    # generate_grid on every entry), never always the same one. Both
    # {wordlist,gloss}_lines pairs are preloaded exactly once each (never
    # rebuilt per word); `wordlist_lines`/`gloss_lines` remain the names
    # used below for the primary language, with a second set loaded only
    # when a bilingual grid is actually in play.
    wordlist_lines_by_lang = {language: _load_wordlist_raw_lines(language)}
    gloss_lines_by_lang = {language: _load_gloss_raw_lines(language)}
    if bilingual_language and bilingual_language != language:
        wordlist_lines_by_lang[bilingual_language] = _load_wordlist_raw_lines(bilingual_language)
        gloss_lines_by_lang[bilingual_language] = _load_gloss_raw_lines(bilingual_language)

    rows = []
    for w in sorted(words, key=lambda w: (w["row"], w["col"], w["direction"])):
        answer = w["answer"]
        word_lang = w.get("language", language)
        wordlist_lines = wordlist_lines_by_lang.get(word_lang, wordlist_lines_by_lang[language])
        gloss_lines = gloss_lines_by_lang.get(word_lang, gloss_lines_by_lang[language])
        wordlist_line = wordlist_lines.get(answer)
        in_wordlist = wordlist_line is not None
        matched_gloss_lines = []
        if in_wordlist:
            canonical_list = w.get("canonical") or [w.get("accented", answer)]
            for lemma in canonical_list:
                gloss_line = gloss_lines.get(lemma.lower())
                if gloss_line:
                    matched_gloss_lines.append(gloss_line)
        rows.append({
            "row": w["row"],
            "col": w["col"],
            "direction": w["direction"],
            "answer": answer,
            "in_wordlist": in_wordlist,
            "wordlist_line": wordlist_line,
            "gloss_lines": matched_gloss_lines,
        })
    return rows


def _new_job():
    if len(JOBS) >= MAX_JOBS:
        oldest = next(iter(JOBS))
        del JOBS[oldest]
        CANCEL_EVENTS.pop(oldest, None)
        INTERACTIVE_SESSIONS.pop(oldest, None)
    job_id = uuid.uuid4().hex
    JOBS[job_id] = {
        "status": "running", "step": {"code": "starting"}, "result": None,
        "error": None, "error_code": None,
        "examples_history": [],
        # Live feed of every definition the LLM has produced so far during
        # the "clues" step, one {"answer", "accented", "clue"} dict per
        # successfully-clued word, appended in arrival order — never
        # overwritten, mirroring `examples_history`'s own append-only
        # design (see progress()'s own "clues" handling) — at the user's
        # explicit request: "afficher les définitions créées sous la
        # grille aperçu." A word whose clue generation ultimately failed
        # never gets an entry here at all (nothing to show).
        "clues_progress": [],
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
        # JSON-safe metadata for the "Interactif" authoring mode (language/
        # difficulty/width/height/theme/theme_description), set once by
        # _run_interactive_job; None for every ordinary generation job.
        "interactive": None,
    }
    CANCEL_EVENTS[job_id] = multiprocessing.Event()
    return job_id


async def _build_theme_glossary(theme, language, theme_precision, short_id,
                                cancel_event, log_tag, theme_language=None,
                                clue_gen=clue_generator):
    """Complete themed pre-search for ONE language: LLM expansion into
    keywords (describe_theme, + a list per theme word + top-ups), then a
    Qdrant nearest-neighbor search per keyword in that language's tenant,
    then compilation (_compiled_theme_words_by_length). Returns
    `(priority_words | None, theme_description)`. Called once for the
    primary language and, on a bilingual grid, a second time for the
    down-words language, at the user's explicit request: "Quand une
    grille est bilingue, il faut générer un glossaire thématique par
    langue [...] demander au LLM de générer des mots dans la langue de la
    grille, en tenant compte du fait que les grilles peuvent être
    bilingues (une langue différente par sens, mais avec les mêmes mots
    Thématiques en entrée)." `log_tag` distinguishes the log lines and the
    LOG_THEME/ filename of the two calls.

    `theme_language` (`None` by default — no effect) is the language
    `theme` was probably typed in, when it is KNOWN and DIFFERENT from
    `language` — i.e. only for the second (bilingual) call, whose target
    language differs by construction from the primary one. Passed as-is
    to every `describe_theme(..., verify_translation=...)` call this
    function makes (the whole-theme call, each per-word call, and the
    top-ups) — see that parameter for why it's only ever enabled when a
    language mismatch is genuinely likely (a false positive on the common,
    same-language case would cost useless retries for nothing).

    `clue_gen` (the module-level `clue_generator` — the AUTOMATIC instance
    — by default) is the `LLMClueGenerator` instance whose `describe_theme`
    is called throughout this function: `_run_generate_job` (automatic
    generation, Populate/the web UI) leaves the default value, while
    `_run_interactive_job` (Interactive mode) explicitly passes
    `interactive_clue_generator` — see the card-1/card-2 split at the
    user's explicit request, documented right above where these two
    instances are built at the top of the file."""
    verify_translation = theme_language is not None and theme_language != language
    theme_priority_words = None
    theme_description = ""
    try:
        theme_description = await asyncio.to_thread(
            clue_gen.describe_theme,
            theme, language, cancel_event=cancel_event,
            temperature=THEME_KEYWORD_LLM_TEMPERATURE,
            verify_translation=verify_translation,
        )
    except GenerationCancelled:
        raise
    except Exception as exc:  # noqa: BLE001 — best-effort, falls back to the raw words
        logger.warning(
            "[%s] theme description failed (%s) — searching the raw theme words instead",
            log_tag, exc,
        )
        theme_description = ""
    logger.info(
        "[%s] theme %r -> description %r", log_tag, theme, theme_description,
    )
    # At the user's explicit request: "demander au LLM de générer des
    # listes de 30 mots clefs séparés par des virgules ... Compiler toutes
    # les recherches dans Qdrant pour tous les mots de ces listes
    # (dédoublonner les mots)." The first list is always the WHOLE theme's
    # own (its LLM description); when the theme has more than one word,
    # one list per word is added — each also passed through
    # describe_theme, falling back to the bare word if the LLM call
    # fails. Every list is then split into keywords (_split_keywords).
    keyword_lists: list[tuple[Optional[str], list[str]]] = [
        (None, _split_keywords(theme_description, language) or _theme_tokens(theme) or [theme])
    ]
    tokens = _theme_tokens(theme)
    if len(tokens) > 1:
        for tok in tokens:
            tok_desc = ""
            try:
                tok_desc = await asyncio.to_thread(
                    clue_gen.describe_theme,
                    tok, language, cancel_event=cancel_event,
                    temperature=THEME_KEYWORD_LLM_TEMPERATURE,
                    verify_translation=verify_translation,
                )
            except GenerationCancelled:
                raise
            except Exception as exc:  # noqa: BLE001 — best-effort, repli sur le mot brut
                logger.warning(
                    "[%s] theme word %r description failed (%s) — searching the bare word",
                    log_tag, tok, exc,
                )
                tok_desc = ""
            keyword_lists.append((tok, _split_keywords(tok_desc, language) or [tok]))
        logger.info(
            "[%s] theme has %d words -> %d keyword lists",
            log_tag, len(tokens), len(keyword_lists),
        )
    # Flattens every list into one search set, case-insensitively
    # de-duplicated (first spelling kept).
    searched_keywords: list[str] = []
    _seen_kw: set[str] = set()

    def _add_keywords(kws):
        added = 0
        for kw in kws:
            k = kw.lower()
            if k not in _seen_kw:
                _seen_kw.add(k)
                searched_keywords.append(kw)
                added += 1
        return added

    for _label, _kws in keyword_lists:
        _add_keywords(_kws)
    # At the user's explicit request (grid glossary ONLY, never the
    # Dictionnaire panel): re-run describe_theme up to THEME_KEYWORD_
    # LLM_MAX_LOOPS more times as long as we have fewer than THEME_MIN_
    # KEYWORDS distinct keywords to search. Stops early the moment a call
    # adds nothing new.
    for _loop in range(1, THEME_KEYWORD_LLM_MAX_LOOPS + 1):
        if len(searched_keywords) >= THEME_MIN_KEYWORDS:
            break
        _more = ""
        try:
            _more = await asyncio.to_thread(
                clue_gen.describe_theme,
                theme, language, cancel_event=cancel_event,
                temperature=THEME_KEYWORD_LLM_TEMPERATURE,
                verify_translation=verify_translation,
            )
        except GenerationCancelled:
            raise
        except Exception as exc:  # noqa: BLE001 — best-effort
            logger.warning(
                "[%s] theme keyword top-up %d failed (%s)", log_tag, _loop, exc,
            )
            break
        _kw_more = _split_keywords(_more, language)
        _added = _add_keywords(_kw_more)
        keyword_lists.append((f"(top-up {_loop})", _kw_more))
        logger.info(
            "[%s] theme keyword top-up %d -> +%d (%d total)",
            log_tag, _loop, _added, len(searched_keywords),
        )
        if _added == 0:
            break
    logger.info(
        "[%s] theme -> %d distinct keywords to search in Qdrant (min_score=%s)",
        log_tag, len(searched_keywords), theme_precision,
    )
    theme_scored_words: list[tuple[str, float]] = []
    try:
        theme_scored_words = await asyncio.to_thread(
            _compiled_theme_words_by_length, searched_keywords, language,
            theme_precision,
        )
        theme_priority_words = [w for w, _score in theme_scored_words]
        logger.info(
            "[%s] theme -> %d preselected words",
            log_tag, len(theme_priority_words),
        )
    except (QdrantStoreError, EmbedderError) as exc:
        logger.warning(
            "[%s] theme pre-search unavailable (%s) — generating without a theme",
            log_tag, exc,
        )
        theme_priority_words = None
    await asyncio.to_thread(
        _write_theme_log, log_tag, theme, theme_description,
        theme_scored_words, language=language, keyword_lists=keyword_lists,
        searched_keywords=searched_keywords, min_score=theme_precision,
    )
    return theme_priority_words, theme_description


async def _run_generate_job(job_id, req, resume_state=None, override_priority_words=None,
                             override_theme_description="", preserved_clues=None,
                             permanent_locked_letters=None, permanent_black_cells=None,
                             publish=True, origin=None, zone_revert=None,
                             required_cells=None):
    """`permanent_black_cells` (`None` by default — no effect for any
    other caller) is "Finir la zone"'s own set of cells frozen black
    because they lie outside the selected zone — passed straight through
    to `generate_grid(permanent_black_cells=...)`, see that function's own
    docstring and `interactive_finish`'s `zone_cells` for the full
    reasoning.

    `publish` (`True` by default — every pre-existing caller unaffected)
    controls what happens to the finished grid once it's ready: `True`
    saves it to the Bibliothèque (GRID_STORE, plus a durable SVG/PNG copy)
    exactly as before this parameter existed. `False` — used only by
    POST /api/interactive/finish ("Finir la grille") — skips the
    Bibliothèque/SVG/PNG entirely and instead saves the finished grid as a
    brand-new "Créations" (GRID_WORK) draft under THIS job's own id, at
    the user's explicit request: "ne pas publier la grille. Ajouter la
    nouvelle version aux Créations de l'auteur. Réouvrir la grille
    automatiquement en mode édition." Never `resume_state`'s ORIGINAL
    interactive job_id — this is deliberately a genuinely NEW entry,
    never a rename/overwrite of whatever the originating interactive
    session had already autosaved on its own. `result["grid_work_id"]` is
    set to the new draft's id so `frontend/static/script.js`'s
    `runGeneration()` can detect this case and reopen it in Édition mode
    automatically instead of showing it as an ordinary finished/playable
    grid, via the exact same `POST /api/interactive/resume` mechanism the
    "Créations" panel itself already uses.

    `origin` (`None` by default) is `interactive_finish`'s own originating
    interactive session's `job["interactive"]["origin"]` snapshot (see
    `_run_interactive_resume_job`) — carried straight through to
    `grid_store.save_grid_work` so a session that started from an existing
    library/GRID_WORK grid keeps that same provenance across a "Finir la
    grille" completion, exactly like an ordinary autosave already does.

    `override_priority_words`/`override_theme_description` (`None`/`""`
    by default — no effect for any pre-existing caller) let a caller hand
    in an ALREADY-RESOLVED theme glossary instead of having this function
    recompute one itself via `_build_theme_glossary` (an LLM call + a
    Qdrant search per keyword — costly and non-deterministic) — used by
    POST /api/interactive/finish (see its own docstring) to reuse the
    interactive session's own `priority_words` verbatim, exactly the same
    "never re-derive, just reuse" principle already established for
    `_run_interactive_resume_job`'s own `priority_words`. Only ever
    `bilingual_theme_priority_words = None` in that case (an interactive
    session never builds a per-language glossary, see
    `_run_interactive_job`'s own docstring).

    `preserved_clues` (`None` by default — a `{word_answer: clue}` map,
    no effect for any pre-existing caller), at the user's explicit request
    for "Finir la grille" ("génération des définitions manquantes... mais
    pas celles déjà définies"): a finished word whose exact answer text is
    a key of this map already has a clue and is excluded from the LLM
    clue-generation batch entirely, keeping that clue text verbatim
    instead of asking the LLM for a new one. Deliberately keyed by the
    word's own TEXT, not its (row, col, direction) — see
    `interactive_finish`'s own docstring for why a position-keyed map
    breaks the moment the search extends a word into a boundary cell that
    wasn't yet decided black, at the user's own explicit correction: "il
    ne faut pas se contenter de vérifier les positions : des mots ont pu
    changer."

    `permanent_locked_letters` (`None` by default — no effect for any
    other caller) is `interactive_finish`'s own `locked_letters` — passed
    straight through to `generate_grid(permanent_locked_letters=...)`, at
    the user's explicit request: "la génération ne doit pas toucher aux
    lettres verrouillées, y compris ne pas poser de case noire sur ces
    lettres... [même si un emplacement contient] un mot impossible
    (probablement un nom propre voulu par l'utilisateur)." Unlike
    `resume_state`'s own `locked_letters` (only ever the STARTING point of
    the very first palier, then recomputed/replaced palier after palier
    by the ordinary cross-palier retry machinery — see `generate_grid`'s
    own docstring), this one stays identical and hard for the WHOLE
    generation, however many paliers it takes.

    `zone_revert` (`None` by default — no effect for any pre-existing
    caller, including plain "Finir la grille") is "Finir la zone"'s own
    `{(row, col): original_char}` map — every cell OUTSIDE the selected
    zone, at its exact pre-click value ("#", ".", or a real letter) —
    applied right after `generate_grid()` succeeds, before anything else
    (clue generation included) ever reads `result`. REDESIGNED at the
    user's explicit request, after two earlier designs both turned out
    wrong once seen live (see `interactive_finish`'s own docstring for the
    full trail — first forcing every outside-zone blank cell black, then
    letting the search complete outside the zone freely): "'Finir la
    zone' ne doit toucher qu'aux cases strictement laissées non
    grisées..., ET ne pas toucher aux lettres et cases noires déjà en
    place dans la zone non grisée." The search itself still runs over the
    WHOLE grid as before (the CSP model has no native notion of "this
    region doesn't exist for this call" — every cell must resolve to a
    real letter or black to be searchable at all) — but its own answer for
    every cell in `zone_revert` is simply discarded afterward, overwritten
    back to exactly what it was before "Finir la zone" was ever clicked,
    cell by cell, on both `result["pattern"]` and `result["solution"]`.
    Only a word that ends up with at least one cell reverted back to
    BLANK — genuinely losing content, not just lying outside the zone — is
    dropped from `result["words"]` entirely: a word entirely outside the
    zone that was ALREADY fully lettered before the click reverts every
    one of its cells to that same letter (a no-op) and stays in `result
    ["words"]`, definition and all, exactly as the player left it; only a
    word touching a cell that was genuinely blank outside the zone (the
    search's own new letter there now discarded) is actually incomplete
    and gets dropped — sending an incomplete word to clue generation or
    the word-verification table would be actively wrong.
    The end result is never a "finished, playable" grid in the usual
    sense — only the selected zone is ever truly complete — which is
    exactly why this endpoint already returns a "Créations" (GRID_WORK)
    draft (`publish=False`) reopened in the editor, never a Bibliothèque
    entry: the player is expected to keep working on whatever's still
    blank outside the zone, by hand or with another "Finir la zone"
    call.

    `required_cells` (`None` by default — no effect for any pre-existing
    caller) is passed straight through to `generate_grid(required_cells=
    ...)` — see its own docstring — at the user's explicit request: "quand
    toutes les cases non verrouillées sont remplies, et que les
    emplacements complets sont des mots valides qui ne créent pas de zones
    impossibles, la grille doit être considérée comme réussie, même si il
    reste des emplacements non complets couvrant les cases verrouillées."
    `interactive_finish` builds it as every currently-blank cell of the
    selected zone (or of the whole grid for plain "Finir la grille", a
    provable no-op there — see `generate_grid`'s own docstring). A word
    generate_grid() itself never resolved this way is already dropped from
    `result["words"]` before this function ever sees it, so the rest of
    this function (`preserved_clues`/`words_needing_clue`, the word-
    verification table, theme-cells) needs no further change at all to
    only ever process complete words."""
    job = JOBS[job_id]
    short_id = job_id[:8]
    cancel_event = CANCEL_EVENTS[job_id]
    # Stored as a plain JSON-safe dict (not the pydantic model instance
    # itself), so POST /api/generate/continue/{job_id} can rebuild an
    # equivalent GenerateRequest later without depending on the original
    # object's lifetime — see _new_job's own "request" field.
    job["request"] = req.model_dump()
    task = GenerationTask(job_id=job_id, req=req, resume_state=resume_state)

    # Timestamps of generate_grid()'s two internal boundaries that
    # progress() below needs to split generation duration from
    # optimization duration (see grid_start further below) — at the
    # user's explicit request: "Optimisation en XhXmnXs" between "Grille
    # générée en..." and "Définitions générées en...". A dict rather than
    # separate local variables: mutated from inside `progress()` (a
    # closure), no need for a `nonlocal` per key. `generate_grid()` emits
    # "minimizing" right before `minimize_black_squares()` (end of
    # search/fill) and "grid_ready" right after (end of optimization) —
    # see crossword_gen.py.
    phase_times = {}

    def progress(step, **data):
        if step == "budget_progress":
            # Enriches the status already shown (e.g. "Tentative
            # N/200...") with a percentage of the checks budget already
            # consumed, at the user's explicit request: "sur la ligne de
            # statut de l'interface, ajouter le pourcentage du budget
            # déjà consommé par la phase de remplissage en cours." Never
            # replaces `job["step"]` wholesale the way the other steps do
            # right below — this signal is republished every
            # `BUDGET_PROGRESS_REPORT_INTERVAL_S` seconds while a search
            # is running (see crossword_gen.py), and overwriting it would
            # replace the real status (attempt number, etc.) with a bare
            # percentage. The next "normal" event (`pattern`/
            # `pattern_attempt_failed`/...) replaces `job["step"]`
            # wholesale as usual, naturally making this now-stale
            # `budget_percent` disappear until a new report arrives for
            # the next attempt.
            job["step"] = {**job["step"], "budget_percent": data.get("percent")}
            return
        # "Finir la zone" (`zone_revert`) — applied here too, not just to
        # the FINAL result (see below), at the user's explicit correction:
        # "il met des lettres dans la zone qui était grisée, qui n'a pas
        # été verrouillée" — the live attempt-preview shown DURING the
        # search is exactly as visible to the player as the final saved
        # grid, so every `example_grid` the search publishes gets the same
        # cell-by-cell revert applied to it, right here, before it's ever
        # stored into `job["step"]`/`job["examples_history"]` — the search
        # still explores the whole grid underneath (unavoidable — see
        # `_run_generate_job`'s own docstring), but the player never sees
        # any of its temporary answers outside the zone, at any point in
        # the process, not only in the end. Every one of these cells is
        # also added to `locked_cells` (merged, not replaced) so the
        # existing green `.locked` highlight covers the whole zone
        # boundary from the very first preview onward, not just the
        # already-typed letters.
        if zone_revert and data.get("examples"):
            for ex in data["examples"]:
                eg = ex.get("example_grid")
                if not eg:
                    continue
                for (r, c), original in zone_revert.items():
                    eg[r][c] = original if original not in ("#", ".") else (
                        "#" if original == "#" else "."
                    )
                ex["locked_cells"] = sorted(
                    {tuple(cell) for cell in ex.get("locked_cells", [])} | set(zone_revert)
                )
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
            step_without_examples = {
                k: v for k, v in job["step"].items() if k not in ("examples", "word_table")
            }
            entry = {"step": step_without_examples, "examples": examples}
            # word_table (see _build_word_verification_table below) rides
            # along on this same entry rather than a separate job-level
            # field, at the user's explicit request that this diagnostic
            # table appear right below the final-grid preview it belongs
            # to — the only progress event that ever carries a word_table
            # is this same "clues" event that also carries `examples`, so
            # the two naturally travel together through the frontend's own
            # previewHistory navigation (script.js's showPreviewEntry()).
            if "word_table" in data:
                entry["word_table"] = data["word_table"]
            job["examples_history"].append(entry)
        # "Continuer" button, at the user's explicit request — see
        # _new_job's own "resume_state" field and crossword_gen.py's
        # _serialize_resume_state. Only ever present on the single
        # "pattern_failed" event a total failure emits (once per job), so a
        # plain unconditional assignment is enough here — unlike
        # `examples_history` above, which appends across many events, there
        # is only ever one `resume_state` to keep per job.
        if "resume_state" in data:
            job["resume_state"] = data["resume_state"]
        # Live "definitions created so far" feed, at the user's explicit
        # request: "afficher les définitions créées sous la grille aperçu."
        # `new_clue` (an {"answer", "accented", "clue"} dict) only ever
        # rides on a "clues" progress event, one per word that just got a
        # real clue (never for a word whose clue generation failed — see
        # the lambda passed to clue_generator.generate below) — appended
        # here rather than overwriting, the same append-only convention as
        # `examples_history` just above, so the frontend can grow a running
        # list across polls instead of only ever seeing the latest one.
        new_clue = data.get("new_clue")
        if new_clue:
            job["clues_progress"].append(new_clue)
        logger.info("[%s] %s %s", short_id, step, data)

    try:
        logger.info(
            "[%s] starting generation: language=%s bilingual_language=%s width=%s "
            "height=%s difficulty=%s force_letters_percent=%s black_enrichment_percent=%s "
            "mode=%s theme_precision=%s source=%s",
            short_id, req.language, req.bilingual_language, req.width, req.height,
            req.difficulty, req.force_letters_percent, req.black_enrichment_percent,
            req.mode, req.theme_precision, req.source,
        )
        # Grid (CPU) queue, at the user's explicit request — see GRID_
        # QUEUE's own module-level docstring: at most one grid search runs
        # at a time across every concurrent job, everyone else waits their
        # turn with a live position status. `grid_start` (below) is
        # measured *after* this wait, deliberately — queue wait time is
        # not generation time, and reporting it as such would misleadingly
        # inflate the "Grille générée en..." duration shown on the
        # finished grid with time this job spent merely queued, not
        # actually being computed.
        #
        # Wrapped in a pause/resume loop, at the user's explicit request
        # (see MAX_TURN_DURATION_S's own docstring): if this job's own
        # turn runs past 15 minutes *and* another job is genuinely waiting
        # behind it, generate_grid() raises GenerationPaused with a resume
        # state (the exact same mechanism already built for the
        # "Continuer" button) instead of ever being force-killed — this
        # job goes to the *back* of GRID_QUEUE and waits its turn again,
        # picking up next time exactly where it left off. `grid_paused_
        # compute_s` accumulates every earlier, paused turn's own real
        # compute time (excluding time spent merely queued) so the final
        # `generation_duration_seconds` still reflects the true total
        # work done, not just the last turn's own duration.
        # Themed pre-search (see THEME_LENGTH_MIN/MAX/THEME_MIN_SCORE): if
        # the "Thématique" field is non-empty, we FIRST ask the LLM for a
        # telegraphic list of about 30 keywords describing the theme
        # (describe_theme) — one list per theme word when it has more
        # than one. Each list is split into individual keywords
        # (_split_keywords), all flattened and de-duplicated; EVERY
        # keyword then runs its own Qdrant nearest-neighbor search, and
        # _compiled_theme_words_by_length merges every result (best score
        # per word) into a glossary generate_grid() will try in priority
        # for every slot. The LLM's own keyword list(s) are written at
        # the top of a LOG_THEME/ file. Best-effort and done only once
        # (not on every resume after a queue pause): a failed LLM call
        # simply falls back to the theme's own raw words; Qdrant/embedder
        # unavailability or a collection not populated for this language
        # never blocks generation — it just proceeds with no theme.
        theme = (req.theme or "").strip()
        theme_priority_words = None
        bilingual_theme_priority_words = None
        # Stays "" if `theme` is empty, or if describe_theme fails — read
        # further below by the only other consumer of this variable, the
        # grid's own title (see clue_generator.generate_title(...,
        # theme_description=...) and its own comment), which must stay
        # defined even with no theme.
        theme_description = ""
        if override_priority_words is not None:
            # "Finir la grille" (see POST /api/interactive/finish): reuses
            # the glossary already resolved by the interactive session
            # itself verbatim, rather than re-running the whole LLM/Qdrant
            # call — see this function's own docstring.
            theme_priority_words = override_priority_words
            theme_description = override_theme_description
        elif theme:
            # A dedicated status step, at the user's explicit request
            # ("indiquer la phase de génération du glossaire
            # thématique"): this phase (the LLM call + Qdrant pagination
            # by length, see _theme_words_by_length) can take several
            # seconds and used to signal nothing at all — the displayed
            # status stayed "starting" (or the previous step, on a
            # resume) throughout. A single event, with no numeric
            # progress (unlike "pattern"/"clues"/etc.): this whole block
            # runs as one unit, with no sub-steps to report.
            progress("theme", theme=theme)
            # Bilingual grid: a second glossary for the down-words
            # language (see _build_theme_glossary), at the user's
            # explicit request. `theme_description` stays the primary
            # language's own (the title + the clues' own steering). Both
            # calls are launched in parallel via asyncio.gather (at the
            # user's explicit request: "paralléliser la génération des
            # deux langues") rather than one after the other — each is
            # only ever a chain of calls already wrapped in
            # asyncio.to_thread (the describe_theme LLM call, the Qdrant
            # search), so both genuinely run at the same time on the
            # thread pool with neither ever blocking the other nor the
            # asyncio loop itself. `return_exceptions=True`: both results
            # are collected first, before any exception (typically
            # GenerationCancelled, if the user clicks Stop while both are
            # running) is explicitly re-raised — without this, the
            # faster task's own exception would be raised immediately by
            # gather() without ever awaiting/consuming the other's own
            # result (or exception), which asyncio logs as "exception
            # never retrieved".
            if req.bilingual_language and req.bilingual_language != req.language:
                primary_result, bilingual_result = await asyncio.gather(
                    _build_theme_glossary(
                        theme, req.language, req.theme_precision, short_id,
                        cancel_event, short_id,
                    ),
                    _build_theme_glossary(
                        theme, req.bilingual_language, req.theme_precision, short_id,
                        cancel_event, f"{short_id}_{req.bilingual_language}",
                        # `theme` was typed with `req.language` in mind (the
                        # grid's own primary/across language) — passing it
                        # here, differing from this call's own target
                        # `req.bilingual_language`, is what tells describe_
                        # theme() a genuine translation is expected, not just
                        # a coincidental echo, at the user's explicit request.
                        theme_language=req.language,
                    ),
                    return_exceptions=True,
                )
                for _r in (primary_result, bilingual_result):
                    if isinstance(_r, BaseException):
                        raise _r
                theme_priority_words, theme_description = primary_result
                bilingual_theme_priority_words, _ = bilingual_result
            else:
                theme_priority_words, theme_description = await _build_theme_glossary(
                    theme, req.language, req.theme_precision, short_id, cancel_event,
                    short_id,
                )

        # "Mots Défi (personnalisation)" — same normalization as POST
        # /api/interactive/step's own `challenge_words` handling (see
        # `interactive_step` below): the author's own typed spelling,
        # reduced here to the grid's bare-uppercase form
        # (`challenge_word_grid_form`), the only form `generate_grid`'s
        # CSP search ever compares against actual cells.
        challenge_words = frozenset(
            grid_form
            for w in req.challenge_words
            if w and (grid_form := challenge_word_grid_form(w))
        )

        GRID_QUEUE.append(task)
        try:
            grid_resume_state = resume_state
            grid_paused_compute_s = 0.0
            while True:
                await _wait_in_queue(GRID_QUEUE, task, job, cancel_event, "queued_grid")
                # Durations shown above the final grid (`#generation-times`
                # on the frontend), at the user's explicit request —
                # measured here rather than client-side, which has no
                # reliable way to know when each phase genuinely started/
                # finished (only this process directly sees the two
                # blocking calls below). `time.monotonic()`, not
                # `time.time()`: a wall clock can jump backward (an NTP
                # adjustment, a DST change), which would corrupt a
                # duration computed by plain subtraction — `monotonic()`
                # never goes backward.
                grid_start = time.monotonic()
                try:
                    result = await asyncio.to_thread(
                        generate_grid,
                        width=req.width,
                        height=req.height,
                        difficulty=req.difficulty,
                        seed=req.seed,
                        wordlist_path=str(WORDLISTS[req.language]),
                        bilingual_wordlist_path=(
                            str(WORDLISTS[req.bilingual_language])
                            if req.bilingual_language and req.bilingual_language != req.language
                            else None
                        ),
                        on_progress=progress,
                        force_letters_fraction=req.force_letters_percent / 100,
                        black_enrichment_fraction=req.black_enrichment_percent / 100,
                        cancel_event=cancel_event,
                        deadline_checks=BUDGET_MODES[req.mode],
                        resume_state=grid_resume_state,
                        should_pause=_make_should_pause(GRID_QUEUE, task),
                        priority_words=theme_priority_words,
                        bilingual_priority_words=bilingual_theme_priority_words,
                        permanent_locked_letters=permanent_locked_letters,
                        permanent_black_cells=permanent_black_cells,
                        required_cells=required_cells,
                        challenge_words=challenge_words,
                    )
                    break
                except GenerationPaused as p:
                    grid_paused_compute_s += time.monotonic() - grid_start
                    grid_resume_state = p.resume_state
                    logger.info("[%s] grid turn paused, back of the queue", short_id)
                    GRID_QUEUE.remove(task)
                    GRID_QUEUE.append(task)
        finally:
            # Freed as soon as this job's own CPU work is done, before its
            # (possibly much slower) clue-writing stage even starts — see
            # GRID_QUEUE's own docstring: the whole point of two
            # independent queues is that the *next* queued job's own grid
            # search can start right away instead of waiting for this
            # job's clues too.
            if task in GRID_QUEUE:
                GRID_QUEUE.remove(task)
        if result is None:
            job["status"] = "error"
            job["error_code"] = "no_fillable_grid"
            job["error"] = (
                "Aucune grille remplissable trouvée avec ces paramètres, "
                "réessayez ou changez la taille/difficulté."
            )
            logger.warning("[%s] no fillable grid found", short_id)
            return
        # "Finir la zone" (`zone_revert`, see this function's own docstring
        # above) — undo, cell by cell, whatever the search decided outside
        # the selected zone, before anything else ever reads `result`: no
        # clue generation, no word-verification table, no theme-cells
        # computation should ever see the search's own outside-zone answer
        # at all. `pattern`/`solution` are both mutated in place — both are
        # freshly built by this exact `generate_grid()` call, never shared
        # with anything else that might still need the pre-revert version.
        if zone_revert:
            pattern = result["pattern"]
            solution = result["solution"]
            # `reverted_pattern` mirrors what each reverted cell now looks
            # like on the black/white pattern ("#" for a cell that was
            # already black, "." for a cell that was blank) — kept
            # separately from `zone_revert` itself (which still holds the
            # RAW original character, letter included) so the word filter
            # just below can tell "reverted to blank" (a real content
            # loss) apart from "reverted to its own already-black/already-
            # lettered value" (a no-op, nothing actually lost).
            reverted_pattern = {}
            for (r, c), original in zone_revert.items():
                reverted = "#" if original == "#" else "."
                pattern[r][c] = reverted
                solution[r][c] = original if original not in ("#", ".") else reverted
                reverted_pattern[(r, c)] = reverted
            # Drop a word only if the revert actually left one of its cells
            # BLANK (its content genuinely lost) — never merely for having
            # a cell outside the zone: a word entirely outside the zone
            # that was ALREADY fully lettered before "Finir la zone" was
            # even clicked (so every one of its cells reverts right back to
            # the exact letter it already had — a no-op) must stay in
            # `result["words"]`, definition and all, exactly as the player
            # left it. Only a word touching a cell that was blank outside
            # the zone (the search's own new letter there now discarded)
            # is genuinely incomplete and gets dropped.
            result["words"] = [
                w for w in result["words"]
                if not any(
                    reverted_pattern.get((
                        w["row"] + (dk if w["direction"] != "across" else 0),
                        w["col"] + (dk if w["direction"] == "across" else 0),
                    )) == "."
                    for dk in range(len(w["answer"]))
                )
            ]
            logger.info(
                "[%s] zone_revert: %d cell(s) reverted outside the selected zone, "
                "%d word(s) kept",
                short_id, len(zone_revert), len(result["words"]),
            )
        # "minimizing"/"grid_ready" are always present here (result is
        # never None without going through the whole pipeline) —
        # `.get(..., grid_start)` stays a defensive safeguard, not a
        # normal case: without it, the unlikely absence of either one
        # would fail the whole job just for this duration calculation,
        # even though the grid itself is already ready.
        search_done = phase_times.get("minimizing", grid_start)
        optimization_done = phase_times.get("grid_ready", search_done)
        # `grid_paused_compute_s` (0.0 for the overwhelmingly common case
        # of a job that never had to yield its turn) folds in every
        # earlier paused turn's own real compute time, so a job that was
        # sent to the back of GRID_QUEUE one or more times still reports
        # its true total generation time, not just its final turn's own
        # duration — see the pause/resume loop above.
        result["generation_duration_seconds"] = grid_paused_compute_s + (search_done - grid_start)
        result["optimization_duration_seconds"] = optimization_done - search_done
        # Difficulty level returned on the job's result, at the user's
        # explicit request (shown to the right of the playable grid's
        # title, see frontend/static/script.js). A GRID_STORE record (a
        # grid reloaded from the library) already carries this field;
        # here it's added for a freshly generated grid.
        result["difficulty"] = req.difficulty
        # The typed theme (a word string) carried on the job's result and
        # saved into the stored grid — `None` if the field was empty. See
        # GenerateRequest.theme / grid_store.save_grid_json / the
        # library's "Thématique" column.
        result["theme"] = theme or None

        # Preview of the final (already minimized) grid, right at the
        # very start of clue generation — at the user's explicit request,
        # reusing the exact same mechanism as crossword_gen.py's
        # "minimizing" preview (see progress() above). `result["solution"]`
        # (not `result["pattern"]`, which only holds the bare black/white
        # pattern) already carries the real letters — built by
        # build_letters_grid, in exactly the shape `example_grid` expects
        # (a black cell or a letter, never a "."). Showing the letters
        # here doesn't actually reveal them, though:
        # `renderAttemptPreview()` (frontend/static/script.js) already
        # hides them by default and only reveals them if the player turns
        # on #attempt-preview-reveal-btn, exactly like crossword_gen.py's
        # own "minimizing" preview (see CLAUDE.md) — a similar reversal to
        # its own, at the user's explicit request, from this preview's
        # very first version, which deliberately showed `result["pattern"]`
        # with no letters at all.
        # `impossible_cells`/`forced_cells`/`locked_cells` empty: this
        # grid is already filled and successfully minimized, so there's
        # no impossible cell, no forced letter, and no locked cell to
        # report.
        # word_table (see _build_word_verification_table above), at the
        # user's explicit request: a verification table, one grid word per
        # row, shown right below this final-grid preview on the frontend
        # (see script.js's showPreviewEntry()) — never recomputed or
        # republished afterward (every later "clues" progress call, one per
        # word during clue generation, never carries `examples` at all, so
        # none of them create a further entry in job["examples_history"] —
        # see the progress() closure above), so this one entry stays the
        # only one that ever carries this table. asyncio.to_thread: reads a
        # potentially large file (up to a few hundred thousand lines for
        # German) synchronously — must never block the asyncio event loop,
        # even though the result is needed before clue_generator.generate
        # can start right after.
        word_table = await asyncio.to_thread(
            _build_word_verification_table, result["words"], req.language,
            result.get("bilingual_language"),
        )
        # Cells of the words drawn from the theme glossary, shown in green
        # letters in the preview (see crossword_gen.py's
        # `_theme_word_cells`/renderAttemptPreview) — recomputed here from
        # `result["words"]` (each word carries `answer`/`row`/`col`/
        # `direction`), since `generate_grid` doesn't return a top-level
        # `theme_cells` list. Empty when there's no theme.
        _theme_set = set(theme_priority_words or ())
        theme_cells = sorted({
            (w["row"] + (dk if w["direction"] != "across" else 0),
             w["col"] + (dk if w["direction"] == "across" else 0))
            for w in result["words"] if w["answer"] in _theme_set
            for dk in range(len(w["answer"]))
        })
        # Same computation for "Mots Défi" (`challenge_words`, already a
        # bare-grid-form frozenset at this point — see its own definition
        # above), at the user's explicit request: this "final grid" preview
        # (the very last one shown, right after optimization/minimization)
        # had never computed this at all, unlike every earlier preview in
        # crossword_gen.py (`_challenge_word_cells_from_assignment`) — so a
        # challenge word placed in the grid stopped showing in green the
        # moment generation reached this step, even though it was still
        # correctly highlighted in every preview before it.
        challenge_cells = sorted({
            (w["row"] + (dk if w["direction"] != "across" else 0),
             w["col"] + (dk if w["direction"] == "across" else 0))
            for w in result["words"] if w["answer"] in challenge_words
            for dk in range(len(w["answer"]))
        })
        # "Finir la grille" (see POST /api/interactive/finish): a word
        # whose exact ANSWER TEXT already carries a preserved clue
        # (`preserved_clues`, a `{word: clue}` map — deliberately never
        # keyed by (row, col, direction), see that endpoint's own
        # docstring for why a position-keyed match silently breaks the
        # moment the search extends a word into an undecided boundary
        # cell) is never sent to the LLM. `None`/empty for any
        # pre-existing caller: `words_needing_clue` then becomes
        # `result["words"]` in full, `total_words_for_clues` ==
        # `len(result["words"])`, unchanged behavior. Computed here (not
        # only further below, where `remaining_entries` also needs it) so
        # this very first "clues" event already shows the true total word
        # count to define, consistent with the progress shown afterward.
        preserved_by_word = preserved_clues or {}
        words_needing_clue = [
            w for w in result["words"]
            if not preserved_by_word.get(w["answer"])
        ]
        total_words_for_clues = len(words_needing_clue)
        progress(
            "clues", current=0, total=total_words_for_clues,
            examples=[{
                "example_grid": result["solution"],
                "impossible_cells": [],
                "forced_cells": [],
                "locked_cells": [],
                "theme_cells": theme_cells,
                "challenge_cells": challenge_cells,
                # The number of the process that genuinely produced this
                # winning grid (backend/crossword_gen.py's own `winning_
                # process_number`, threaded through the result dict — see
                # its own docstring for the full "process number"
                # feature), at the user's explicit request.
                "process_number": result.get("winning_process_number"),
                "is_best": True,
            }],
            word_table=word_table,
        )
        # Clues (GPU/LLM) queue, at the user's explicit request — see
        # CLUES_QUEUE's own module-level docstring: at most one job's own
        # clue-writing (and, right after it, title-writing — both hit the
        # same local LLM server, so they share this same queue slot)
        # happens at a time; everyone else waits their turn with a live
        # position status. This job's own grid is already fully generated
        # and saved into `result` by now, and the "final grid" preview
        # above (progress("clues", current=0, ...)) has already been
        # published regardless of whether this job has to wait here — a
        # queued job's grid is already known/finished, only its
        # definitions are still pending.
        # Wrapped in the same kind of pause/resume loop as GRID_QUEUE
        # above, at the user's explicit request (see MAX_TURN_DURATION_S):
        # each word's own clue is independent, so "pausing" clue writing
        # is simple — LLMClueGenerator.generate() just stops before the
        # next word and hands back every clue already found plus the
        # still-pending word list (see GenerationPaused's own docstring),
        # accumulated here across as many pause/resume cycles as it takes.
        # `on_progress` reports the running total across every turn
        # (`len(accumulated_clues) + current`, `len(result["words"])`),
        # never just the current turn's own remaining subset — otherwise
        # the "N/total mots" status shown to the player would misleadingly
        # jump backward every time this job resumes after a pause.
        CLUES_QUEUE.append(task)
        try:
            # A 4th element (language) instead of 3, at the user's
            # explicit request for a bilingual grid: each word already
            # carries its own language (crossword_gen.generate_grid's own
            # per-word `language`, based on its direction) —
            # LLMClueGenerator.generate() resolves this language per word
            # (falling back to `req.language`, the positional argument
            # below, for a word that carries none) instead of a single
            # language for the whole call as before this feature.
            # `words_needing_clue`/`preserved_by_word` already computed above
            # (see the very first "clues" progress event, right before this
            # queue wait) — reused here so the two never disagree.
            remaining_entries = [
                (w["answer"], w["accented"], w["canonical"], w.get("language"))
                for w in words_needing_clue
            ]
            # Lookup used only to enrich the live "clues_progress" feed
            # (see progress()'s own "new_clue" handling) with a word's
            # natural accented spelling — clue_generator.generate() itself
            # only ever hands `on_progress` the bare grid answer.
            words_by_answer = {w["answer"]: w for w in result["words"]}
            if theme_description:
                logger.info(
                    "[%s] clue generation steered by theme keyword list: %r",
                    short_id, theme_description,
                )
            accumulated_clues = {}
            clues_compute_s = 0.0
            while True:
                await _wait_in_queue(CLUES_QUEUE, task, job, cancel_event, "queued_clues")
                clues_start = time.monotonic()
                try:
                    new_clues = await asyncio.to_thread(
                        clue_generator.generate,
                        remaining_entries,
                        req.difficulty,
                        req.language,
                        on_progress=lambda current, total, answer=None, clue=None: progress(
                            "clues", current=len(accumulated_clues) + current, total=total_words_for_clues,
                            new_clue=({
                                "answer": answer,
                                "accented": words_by_answer.get(answer, {}).get("accented", answer),
                                "clue": clue,
                            } if clue else None),
                        ),
                        cancel_event=cancel_event,
                        should_pause=_make_should_pause(CLUES_QUEUE, task),
                        # For a themed grid, tells the LLM the WHOLE
                        # THEME'S OWN KEYWORD LIST (describe_theme(theme)'s
                        # own output — the `[(whole theme)]` list, NEVER a
                        # per-word or top-up list) as the theme it must
                        # strongly draw on for every definition, at the
                        # user's explicit request: "il est important,
                        # quand il y a une thématique, que les définitions
                        # respectent au mieux cette thématique." The THEME
                        # section is appended to each word's own user
                        # message (see _build_user_message), not the
                        # system prompt. Stays "" for a non-themed grid
                        # (see above): no effect.
                        theme_description=theme_description,
                        # Only 1 LLM request at a time for a grid coming
                        # from Populate, at the user's explicit request
                        # ("ne pas surcharger le GPU pour les
                        # utilisateurs") — see GenerateRequest.source and
                        # LLMClueGenerator.generate's own docstring. `None`
                        # (the default, parallel behavior) for every other
                        # request.
                        batch_parallelism=(1 if req.source == "populate" else None),
                    )
                    accumulated_clues.update(new_clues)
                    clues_compute_s += time.monotonic() - clues_start
                    break
                except GenerationPaused as p:
                    clues_compute_s += time.monotonic() - clues_start
                    partial_clues, remaining_entries = p.resume_state
                    accumulated_clues.update(partial_clues)
                    logger.info("[%s] clues turn paused, back of the queue", short_id)
                    CLUES_QUEUE.remove(task)
                    CLUES_QUEUE.append(task)
            result["clues_duration_seconds"] = clues_compute_s
            for w in result["words"]:
                # A preserved clue ("Finir la grille", see
                # `preserved_clues` above) always wins — never overwritten
                # by any entry of the same word in `accumulated_clues`
                # (which can't hold one for this exact word anyway, since
                # it was never part of `remaining_entries`). Matched by
                # `w["answer"]` itself, same as `words_needing_clue` above.
                preserved = preserved_by_word.get(w["answer"])
                w["clue"] = preserved if preserved else accumulated_clues.get(w["answer"], "")

            # A short, catchy title for the whole grid, at the user's explicit
            # request: "demande au LLM de générer un titre sympa pour la
            # grille en fonction des mots qu'elle contient" — generated once
            # every clue already exists (LLMClueGenerator.generate_title's own
            # docstring explains why this is a single best-effort call, unlike
            # generate()'s own per-word retry loop), shown above the finished
            # grid (frontend/static/script.js's displayFinalGrid) and saved
            # alongside it below. A failure here never raises (see generate_
            # title) — "" simply means no title line is shown/stored, exactly
            # like a grid generated before this feature existed. For a themed
            # generation, `theme_description` (set earlier, still "" when
            # there was no theme / describe_theme failed) is passed through
            # as an inspiration reference, at the user's explicit request:
            # "passer au LLM la phrase ayant servi à construire le glossaire
            # thématique comme référence d'inspiration pour le titre."
            title = await asyncio.to_thread(
                clue_generator.generate_title,
                [(w["answer"], w["accented"], w["canonical"]) for w in result["words"]],
                req.language,
                cancel_event=cancel_event,
                theme_description=theme_description,
            )
            result["title"] = title
            logger.info("[%s] title: %r", short_id, title)
        finally:
            if task in CLUES_QUEUE:
                CLUES_QUEUE.remove(task)

        progress("saving")
        # The automatic search engine's own parameters (see grid_store.
        # save_grid_json's own `generation_params` docstring), at the
        # user's explicit request: "sauvegarder tous les paramètres...
        # pour pouvoir les reconfigurer à l'identique quand la grille est
        # rechargée en mode édition." `mode` already has its own top-level
        # field (see right above) — duplicated here too so the frontend
        # only needs to read one object to reconfigure all 4 form fields
        # at once. Computed unconditionally (before the `if publish:`
        # below) since both branches need it.
        pseudo = (req.pseudo or "").strip()[:MAX_PSEUDO_LENGTH] or None
        generation_params = {
            "black_enrichment_percent": req.black_enrichment_percent,
            "force_letters_percent": req.force_letters_percent,
            "mode": req.mode,
            "theme_precision": req.theme_precision,
        }
        if publish:
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

            # Bibliothèque (see GET /api/library, GET /api/library/{grid_id}
            # below, and frontend/static/script.js's "Bibliothèque" button),
            # at the user's explicit request — same best-effort treatment as
            # the SVG/PNG saves just above: a failure to persist this grid
            # for later browsing is logged, never allowed to fail the request
            # the player is actually waiting on.
            try:
                grid_id = await asyncio.to_thread(
                    save_grid_json, result, req.language, req.difficulty, req.mode, title,
                    result.get("bilingual_language"), pseudo, theme or None,
                    generation_params=generation_params,
                    challenge_words=req.challenge_words or None,
                )
                logger.info("[%s] saved to library: %s (pseudo=%r)", short_id, grid_id, pseudo)
                # This grid's own GRID_STORE file id, so the frontend can
                # mark it "already seen" (localStorage) the moment it's
                # shown — at the user's explicit request ("y compris la
                # grille qu'il vient de générer"). The same key (`id`) that
                # GET /api/library/{grid_id} returns for a reloaded grid,
                # so the frontend handles both cases the same way.
                result["id"] = grid_id
            except OSError as e:
                logger.warning("[%s] failed to save grid to library: %s", short_id, e)
        else:
            # "Finir la grille" (see this function's own docstring for
            # `publish`) — no SVG/PNG, no Bibliothèque record. Saved as a
            # brand-new "Créations" (GRID_WORK) draft instead, under THIS
            # job's own id — never `resume_state`'s ORIGINAL interactive
            # job_id, so this is genuinely a new entry, never a rename of
            # whatever the originating session had already autosaved on
            # its own.
            try:
                definitions = [
                    {
                        "row": w["row"], "col": w["col"], "direction": w["direction"],
                        "clue": w.get("clue", ""),
                    }
                    for w in result["words"]
                ]
                work_id = await asyncio.to_thread(
                    save_grid_work, job_id, result["solution"], definitions, title,
                    req.language, req.difficulty, theme or None,
                    theme_priority_words or (), req.seed or 0, pseudo,
                    None, origin, result.get("bilingual_language"),
                    generation_params=generation_params,
                )
                logger.info("[%s] saved to Créations: %s (pseudo=%r)", short_id, work_id, pseudo)
                # Read by frontend/static/script.js's runGeneration(), which
                # reopens this draft in Édition mode automatically instead
                # of showing it as an ordinary finished/playable grid — see
                # this function's own docstring for `publish`.
                result["grid_work_id"] = work_id
            except Exception as e:
                logger.warning("[%s] failed to save grid to Créations: %s", short_id, e)

        progress("done")
        job["status"] = "done"
        job["result"] = result
        logger.info("[%s] done", short_id)
    except GenerationCancelled:
        # An interruption requested by the user (the "Stop" button, see
        # POST /api/generate/cancel/{job_id} further below) — its own
        # separate status, never "error": this isn't a failure, just a
        # voluntary stop, and the frontend shows it without the error
        # styling (see frontend/static/script.js's pollJob()).
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


async def _load_interactive_index(language, difficulty, bilingual_language=None):
    """Loads the wordlist/frequency index for one language/difficulty and
    wraps it as the DualIndex every interactive-mode helper
    (interactive_place_word, interactive_slot_candidates, _interactive_
    fill_diagnostics, interactive_clean_impossible_zones, interactive_
    minimize_black_cells...) expects. For an ordinary monolingual session
    (`bilingual_language` `None` or identical to `language`), `.across`/
    `.down` end up the identical object, exactly as before this parameter
    existed. When a genuinely different `bilingual_language` is given, a
    second wordlist/index is loaded for it and used as `.down` — the same
    DualIndex convention crossword_gen.generate_grid() already uses for a
    bilingual grid (see its own `bilingual_wordlist_path`) — at the user's
    explicit request: "quand une grille bilingue est chargée, configurer
    les langues dans celles de la grille (idem en monolingue)." Every one
    of the interactive-mode helpers above already resolves the right
    dictionary per direction via `.for_cells()`/`.for_direction()`, never
    `index[length]` directly, so this is the only change needed to make
    the whole solving/checking pipeline bilingual-aware.

    Shared by `_run_interactive_job` (fresh start) and `_run_interactive_
    resume_job` (resume). Returns `(index, known_words)` — `known_words`
    is the union of every word actually in either lexicon loaded (just
    the one lexicon's own words for a monolingual session), used to
    filter a theme/saved `priority_words` list down to real entries."""
    by_length, accents, _canon, frequencies = await asyncio.to_thread(
        load_wordlist,
        str(WORDLISTS[language]),
        DIFFICULTY_PRESETS.get(difficulty),
        require_gloss=(difficulty == "easy"),
        exclude_proper_nouns=(difficulty == "easy"),
    )
    idx = build_index(by_length, frequencies)
    is_bilingual = bool(bilingual_language) and bilingual_language != language
    if not is_bilingual:
        return DualIndex(idx, idx), set(accents)
    by_length_down, accents_down, _canon_down, frequencies_down = await asyncio.to_thread(
        load_wordlist,
        str(WORDLISTS[bilingual_language]),
        DIFFICULTY_PRESETS.get(difficulty),
        require_gloss=(difficulty == "easy"),
        exclude_proper_nouns=(difficulty == "easy"),
    )
    idx_down = build_index(by_length_down, frequencies_down)
    return DualIndex(idx, idx_down), set(accents) | set(accents_down)


async def _run_interactive_job(job_id, req):
    """Background job behind POST /api/interactive/start — the "Interactif"
    authoring mode. Unlike _run_generate_job it never runs the parallel
    palier search: it builds the theme glossary (if any), makes ONE black-
    cell pattern, places ONE first word, and stores the session so
    POST /api/interactive/step can place further words one at a time.
    No clue generation, no queue, no library save here — that all happens
    later, interactively, driven by the web UI."""
    job = JOBS[job_id]
    short_id = job_id[:8]
    cancel_event = CANCEL_EVENTS[job_id]
    job["request"] = req.model_dump()

    def progress(step, **data):
        job["step"] = {"code": step, **data}

    try:
        theme = (req.theme or "").strip()
        theme_priority_words = None
        theme_description = ""
        if theme:
            progress("theme", theme=theme)
            # clue_gen=interactive_clue_generator: Interactive/Edition
            # mode's own theme-glossary build is an interactive request
            # (card 2, see the module-level singletons' own comment),
            # unlike _run_generate_job's two calls to this same helper
            # (automatic generation, card 1) further above in this file.
            theme_priority_words, theme_description = await _build_theme_glossary(
                theme, req.language, req.theme_precision, short_id, cancel_event,
                short_id, clue_gen=interactive_clue_generator,
            )

        progress("interactive_building")

        # `None`/identical-to-`language` degrades to an ordinary
        # monolingual session, exactly like GenerateRequest.bilingual_
        # language already does for the automatic generator (see
        # _validate_generate_request) — this field is validated the same
        # way in interactive_start before this job is ever started.
        bilingual_language = req.bilingual_language
        is_bilingual = bool(bilingual_language) and bilingual_language != req.language
        index, known = await _load_interactive_index(
            req.language, req.difficulty, bilingual_language,
        )

        # Same MOT-form normalization generate_grid applies to its own
        # priority_words: uppercase, keep only words actually in the loaded
        # lexicon (either language, on a bilingual session). Always a
        # plain frozenset here — Filler/_priority_words_for already treat
        # a plain frozenset as applying uniformly to both directions (see
        # crossword_gen.py's own `_priority_words_for`), which is an
        # accepted simplification for interactive mode: unlike the
        # automatic generator, a themed interactive session never builds a
        # separate per-language glossary.
        priority_words = frozenset(
            u for w in (theme_priority_words or ())
            if (u := str(w).upper()) in known
        )

        rng = random.Random(req.seed)
        rows, cols = req.height, req.width
        available_lengths = DualSet(
            across={L for L, d in index.across.items() if len(d["words"]) >= PREFILL_MIN_WORD_COUNT},
            down={L for L, d in index.down.items() if len(d["words"]) >= PREFILL_MIN_WORD_COUNT},
        )
        grid = await asyncio.to_thread(
            make_pattern, rows, cols, 0.0, rng,
            available_lengths=available_lengths, index=index,
            black_enrichment_fraction=req.black_enrichment_percent / 100,
        )
        # "Mots Défi (personnalisation)" typed on the main generation form
        # before switching to "mode == interactive" (GenerateRequest.
        # challenge_words, reused as-is by POST /api/interactive/start) —
        # same bare-uppercase-grid-form normalization POST /api/
        # interactive/step already applies to every later "Suivant" call,
        # so this very first placed word is drawn from the list too, not
        # just later ones.
        challenge_words = frozenset(
            grid_form
            for w in req.challenge_words
            if w and (grid_form := challenge_word_grid_form(w))
        )
        placed = await asyncio.to_thread(
            interactive_place_word, grid, rows, cols, index, rng, priority_words,
            challenge_words,
        )

        INTERACTIVE_SESSIONS[job_id] = {
            "index": index,
            "priority_words": priority_words,
            "rng": rng,
            # The original seed this session started from — kept around
            # purely so an autosave (POST /api/interactive/save_work) can
            # persist it for a later POST /api/interactive/resume, at the
            # user's explicit request: exact rng *continuation* across a
            # pause is neither preserved nor needed, only a real,
            # reproducible starting point for the resumed session's own
            # further Filler/CSP randomness.
            "seed": req.seed,
        }
        job["interactive"] = {
            "language": req.language,
            # None for an ordinary monolingual session — see is_bilingual
            # above. Mirrored onto job["result"] below too, since pollJob()
            # only ever returns that one, for the same reason "theme"/
            # "language" already are (see enterInteractiveMode()).
            "bilingual_language": bilingual_language if is_bilingual else None,
            "difficulty": req.difficulty,
            "width": req.width,
            "height": req.height,
            "theme": theme or None,
            "has_theme": bool(priority_words),
            "theme_description": theme_description,
            # "Mots Défi (personnalisation)" this session started from —
            # the client's own author-typed spelling verbatim (see
            # InteractiveSaveWorkRequest/InteractiveSaveRequest's own
            # `challenge_words` docstring), read back by POST /api/
            # interactive/save[_work] as a *fallback* only (the frontend
            # always resends its own, possibly-since-edited, live list on
            # every actual save call) and mirrored onto job["result"]
            # below so enterInteractiveMode() can restore it into the
            # "Mots Défi" panel(s) on this entry path too, exactly like a
            # resumed/from-library session already does.
            "challenge_words": req.challenge_words or [],
        }
        result_grid = grid if placed["impossible"] else placed["grid"]
        job["result"] = {
            "width": req.width,
            "height": req.height,
            "grid": result_grid,
            "placed": None if placed["impossible"] else placed["placed"],
            "impossible": placed["impossible"],
            "has_theme": bool(priority_words),
            "impossible_cells": placed.get("impossible_cells", []),
            "low_candidate_cells": placed.get("low_candidate_cells", []),
            # The raw theme string this session started from (already set
            # on job["interactive"]["theme"] above — mirrored here too so
            # the frontend can read it straight off pollJob()'s own return
            # value, which is job["result"] alone, never job["interactive"]
            # — see enterInteractiveMode()'s own use of it to re-fill the
            # "Thématique" field on every entry path uniformly).
            "theme": theme or None,
            # Same reasoning as "theme" above: job["interactive"] already
            # carries language/difficulty/bilingual_language, but pollJob()
            # only ever returns job["result"] to the frontend — mirrored
            # here so enterInteractiveMode() can keep interactiveLanguage/
            # interactiveDifficulty/interactiveBilingualLanguage correct on
            # every entry path (a real bug otherwise: "Publier"/
            # "Sauvegarder" send whatever those module-level `let`s last
            # held, which used to only ever be set by the generation
            # form's own submit handler).
            "language": req.language,
            "bilingual_language": job["interactive"]["bilingual_language"],
            "difficulty": req.difficulty,
            # Same reasoning as "theme"/"language" above — see job
            # ["interactive"]["challenge_words"]'s own comment just above.
            "challenge_words": req.challenge_words or [],
        }
        progress("done")
        job["status"] = "done"
    except GenerationCancelled:
        job["status"] = "cancelled"
    except Exception:
        job["status"] = "error"
        job["error_code"] = "internal_error"
        job["error"] = "Erreur interne."
        logger.exception("[%s] unhandled error during interactive start", short_id)


# Trailing "(Vn)" version marker on a recomputed grid's title, at the
# user's explicit request ("Au lieu de '(new clues)', indiquer '(V2)',
# puis '(V3)', etc"). The un-suffixed original grid is treated as V1, so
# its first recompute is "(V2)"; each further recompute bumps the number.
_VERSION_SUFFIX_RE = re.compile(r"\s*\(V(\d+)\)\s*$")


def _next_version_title(title):
    """"Graines" -> "Graines (V2)"; "Graines (V2)" -> "Graines (V3)"; an
    empty title -> "(V2)". See _run_recompute_job below."""
    title = (title or "").strip()
    m = _VERSION_SUFFIX_RE.search(title)
    if m:
        base = title[: m.start()].rstrip()
        n = int(m.group(1)) + 1
    else:
        base = title
        n = 2
    return f"{base} (V{n})".strip()


async def _run_recompute_job(job_id, grid_id):
    """Background job behind POST /api/recompute — the "Recalculer" button
    (see RecomputeRequest). Reloads the stored grid `grid_id`
    (grid_store.get_grid), re-runs ONLY clue generation on its words
    (through the same CLUES_QUEUE the normal pipeline uses, so a recompute
    waits its turn behind any grid currently having its clues written),
    then saves a brand new library record whose title carries a bumped
    "(Vn)" version marker (_next_version_title) — the original record is
    never touched. The finished
    `result` is the exact same generate_grid()-shaped dict displayFinalGrid()
    already knows how to render, so the frontend polls this job via the
    same GET /api/generate/status/{job_id} and hands the result straight to
    displayFinalGrid()."""
    job = JOBS[job_id]
    short_id = job_id[:8]
    cancel_event = CANCEL_EVENTS[job_id]

    def progress(step, **data):
        job["step"] = {"code": step, **data}
        examples = data.get("examples")
        if examples:
            step_without_examples = {
                k: v for k, v in job["step"].items() if k not in ("examples", "word_table")
            }
            entry = {"step": step_without_examples, "examples": examples}
            if "word_table" in data:
                entry["word_table"] = data["word_table"]
            job["examples_history"].append(entry)
        # Live "definitions created so far" feed — see _run_generate_job's
        # own progress() for the full rationale; mirrored here since a
        # recompute also re-runs clue generation and shows the same
        # attempt-preview panel.
        new_clue = data.get("new_clue")
        if new_clue:
            job["clues_progress"].append(new_clue)
        logger.info("[%s] %s %s", short_id, step, data)

    try:
        record = await asyncio.to_thread(get_grid, grid_id)
        if record is None:
            job["status"] = "error"
            job["error_code"] = "grid_not_found"
            job["error"] = "grille introuvable dans la bibliothèque"
            logger.warning("[%s] recompute: grid %r not found", short_id, grid_id)
            return

        language = record.get("language") or "fr"
        bilingual_language = record.get("bilingual_language")
        difficulty = record.get("difficulty") or "easy"
        mode = record.get("mode") or "medium"

        # Rebuild the generate_grid()-shaped payload underneath the
        # library-only metadata save_grid_json added (id/created_at/
        # bilingual). `title` is popped separately: save_grid_json sets its
        # own, and the new one is the old one with a bumped "(Vn)" marker.
        result = {
            k: v for k, v in record.items()
            if k not in ("id", "created_at", "bilingual")
        }
        original_title = (result.pop("title", "") or "").strip()
        new_title = _next_version_title(original_title)

        logger.info(
            "[%s] recompute: grid=%s language=%s bilingual_language=%s difficulty=%s mode=%s words=%d",
            short_id, grid_id, language, bilingual_language, difficulty, mode,
            len(result.get("words", [])),
        )

        task = GenerationTask(job_id=job_id)

        word_table = await asyncio.to_thread(
            _build_word_verification_table, result["words"], language, bilingual_language,
        )
        progress(
            "clues", current=0, total=len(result["words"]),
            examples=[{
                "example_grid": result["solution"],
                "impossible_cells": [],
                "forced_cells": [],
                "locked_cells": [],
                # A definitions recompute never replays the themed
                # pre-search (the theme sentence isn't persisted on the
                # grid — see grid_store), so there's no theme word to
                # report here.
                "theme_cells": [],
                "process_number": result.get("winning_process_number"),
                "is_best": True,
            }],
            word_table=word_table,
        )

        CLUES_QUEUE.append(task)
        try:
            remaining_entries = [
                (w["answer"], w["accented"], w["canonical"], w.get("language"))
                for w in result["words"]
            ]
            words_by_answer = {w["answer"]: w for w in result["words"]}
            accumulated_clues = {}
            clues_compute_s = 0.0
            while True:
                await _wait_in_queue(CLUES_QUEUE, task, job, cancel_event, "queued_clues")
                clues_start = time.monotonic()
                try:
                    new_clues = await asyncio.to_thread(
                        clue_generator.generate,
                        remaining_entries,
                        difficulty,
                        language,
                        on_progress=lambda current, total, answer=None, clue=None: progress(
                            "clues", current=len(accumulated_clues) + current,
                            total=len(result["words"]),
                            new_clue=({
                                "answer": answer,
                                "accented": words_by_answer.get(answer, {}).get("accented", answer),
                                "clue": clue,
                            } if clue else None),
                        ),
                        cancel_event=cancel_event,
                        should_pause=_make_should_pause(CLUES_QUEUE, task),
                    )
                    accumulated_clues.update(new_clues)
                    clues_compute_s += time.monotonic() - clues_start
                    break
                except GenerationPaused as p:
                    clues_compute_s += time.monotonic() - clues_start
                    partial_clues, remaining_entries = p.resume_state
                    accumulated_clues.update(partial_clues)
                    logger.info("[%s] recompute clues turn paused, back of the queue", short_id)
                    CLUES_QUEUE.remove(task)
                    CLUES_QUEUE.append(task)
            result["clues_duration_seconds"] = clues_compute_s
            for w in result["words"]:
                w["clue"] = accumulated_clues.get(w["answer"], "")
        finally:
            if task in CLUES_QUEUE:
                CLUES_QUEUE.remove(task)

        result["title"] = new_title

        progress("saving")
        try:
            svg_path = await asyncio.to_thread(save_grid_svg, result, language, difficulty, mode)
            logger.info("[%s] recompute saved %s", short_id, svg_path)
            try:
                png_path = await asyncio.to_thread(save_grid_png, svg_path)
                logger.info("[%s] recompute saved %s", short_id, png_path)
            except OSError as e:
                logger.warning("[%s] recompute failed to save grid PNG sample: %s", short_id, e)
        except OSError as e:
            logger.warning("[%s] recompute failed to save grid SVG: %s", short_id, e)

        try:
            # A recompute creates a new "same grid, different definitions"
            # entry — the original author's own pseudo is kept (already
            # present in `result`, inherited from the reloaded record)
            # rather than replaced with whoever triggered the recompute.
            new_grid_id = await asyncio.to_thread(
                save_grid_json, result, language, difficulty, mode, new_title,
                bilingual_language, result.get("pseudo"), result.get("theme"),
                # A recompute never touches the grid's own layout, only
                # its definitions — so the search-engine parameters that
                # produced it are still valid and are simply carried
                # through (`result` is already the original record minus
                # id/created_at/bilingual, see above).
                generation_params=result.get("generation_params"),
                challenge_words=result.get("challenge_words"),
            )
            logger.info("[%s] recompute saved to library: %s", short_id, new_grid_id)
            result["id"] = new_grid_id
        except OSError as e:
            logger.warning("[%s] recompute failed to save grid to library: %s", short_id, e)

        progress("done")
        job["status"] = "done"
        job["result"] = result
        logger.info("[%s] recompute done", short_id)
    except GenerationCancelled:
        job["status"] = "cancelled"
        logger.info("[%s] recompute cancelled by user", short_id)
    except ClueGenerationError as e:
        job["status"] = "error"
        job["error_code"] = "clue_generation_failed"
        job["error"] = str(e)
        logger.warning("[%s] recompute clue generation failed: %s", short_id, e)
    except Exception:
        job["status"] = "error"
        job["error_code"] = "internal_error"
        job["error"] = "Erreur interne."
        logger.exception("[%s] unhandled error during recompute", short_id)


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
    # A language can be wired up (WORDLISTS entry, UI option, i18n block)
    # before its data pipeline has finished producing data/wordlist_<lang>_
    # full.tsv — reject cleanly here rather than let the job fail deep
    # inside load_wordlist() with a bare FileNotFoundError.
    if not WORDLISTS[req.language].exists():
        raise HTTPException(
            status_code=400,
            detail=f"le dictionnaire pour {req.language!r} n'est pas encore "
                   "construit sur ce serveur — réessayez plus tard.",
        )
    # `bilingual_language` (see GenerateRequest) undergoes exactly the
    # same checks as `language` above — but only when a genuinely
    # bilingual grid is requested (a value given AND different from
    # `language`); `None` or an identical value already degrades cleanly
    # to a monolingual generation and so needs no further validation.
    if req.bilingual_language is not None and req.bilingual_language != req.language:
        if req.bilingual_language not in WORDLISTS:
            raise HTTPException(
                status_code=400,
                detail=(
                    f"langue bilingue inconnue : {req.bilingual_language!r} "
                    f"(attendu : {sorted(WORDLISTS)})"
                ),
            )
        if not WORDLISTS[req.bilingual_language].exists():
            raise HTTPException(
                status_code=400,
                detail=f"le dictionnaire pour {req.bilingual_language!r} n'est pas "
                       "encore construit sur ce serveur — réessayez plus tard.",
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


# ---------------------------------------------------------------------------
# "Interactif" authoring mode — build ONE grid word by word under the
# user's control, hand-write every clue, propose a title, save as a
# "(Création)". POST /api/interactive/start reuses the job machinery
# (polled via GET /api/generate/status, cancelled via POST /api/generate/
# cancel); the three other routes are plain synchronous calls.
# ---------------------------------------------------------------------------

@app.post("/api/interactive/start", status_code=202)
async def interactive_start(req: GenerateRequest):
    """Kick off an interactive authoring session — theme glossary (if any)
    + one black-cell pattern + one first placed word — as a background
    job. `mode` is accepted but ignored (this path never runs the palier
    search, so BUDGET_MODES doesn't apply); validation is done here rather
    than via _validate_generate_request."""
    if req.language not in WORDLISTS or not WORDLISTS[req.language].exists():
        raise HTTPException(status_code=400, detail="langue inconnue ou dictionnaire absent")
    # Same bilingual validation as _validate_generate_request — only when a
    # real bilingual session is requested (given AND different from
    # `language`); `None`/identical degrades to monolingual, no further
    # check needed.
    if req.bilingual_language is not None and req.bilingual_language != req.language:
        if req.bilingual_language not in WORDLISTS or not WORDLISTS[req.bilingual_language].exists():
            raise HTTPException(status_code=400, detail="langue bilingue inconnue ou dictionnaire absent")
    if req.difficulty not in DIFFICULTY_PRESETS:
        raise HTTPException(status_code=400, detail="difficulté inconnue")
    job_id = _new_job()
    task = asyncio.create_task(_run_interactive_job(job_id, req))
    _BACKGROUND_TASKS.add(task)
    task.add_done_callback(_BACKGROUND_TASKS.discard)
    return {"job_id": job_id}


@app.post("/api/interactive/step")
async def interactive_step(req: InteractiveStepRequest):
    """"Suivant" button: place exactly one more word onto the supplied
    grid (no backtracking), or report the grid impossible if no slot can
    take a word."""
    sess = INTERACTIVE_SESSIONS.get(req.job_id)
    if sess is None:
        raise HTTPException(status_code=404, detail="session interactive inconnue (expirée ?)")
    rows = len(req.grid)
    cols = len(req.grid[0]) if req.grid else 0
    if rows < 1 or cols < 1:
        raise HTTPException(status_code=400, detail="grille vide")
    # `req.challenge_words` carries the author's own typed spelling
    # verbatim (accents/case kept, "comme dans les dictionnaires" — see
    # InteractiveStepRequest's own docstring); `interactive_place_word`
    # itself only ever needs the grid's own bare-uppercase form to compare
    # against actual cells, derived here on demand
    # (`challenge_word_grid_form`) rather than stored anywhere.
    challenge_words = frozenset(
        grid_form
        for w in req.challenge_words
        if w and (grid_form := challenge_word_grid_form(w))
    )
    placed = await asyncio.to_thread(
        interactive_place_word,
        [list(row) for row in req.grid], rows, cols,
        sess["index"], sess["rng"], sess["priority_words"],
        challenge_words,
    )
    if placed["impossible"]:
        return {"width": cols, "height": rows, "grid": req.grid,
                "placed": None, "impossible": True,
                "impossible_cells": placed.get("impossible_cells", []),
                "low_candidate_cells": placed.get("low_candidate_cells", [])}
    return {"width": cols, "height": rows, "grid": placed["grid"],
            "placed": placed["placed"], "impossible": False,
            "impossible_cells": placed.get("impossible_cells", []),
            "low_candidate_cells": placed.get("low_candidate_cells", [])}


@app.post("/api/interactive/clean")
async def interactive_clean(req: InteractiveCleanRequest):
    """"Nettoyer" button: run the automatic generator's own "nettoyage
    complet" (remove crossing words / blacken a cell) on every zone of
    the supplied grid currently deemed impossible — see
    `interactive_clean_impossible_zones`. "Nettoyer (+noires)"
    (`req.deep`) additionally runs `interactive_minimize_black_cells` on
    the result — a deeper pass that also tries removing black cells
    outright, anywhere in the grid, not just the ones incidentally
    touched while resolving a specific impossible zone."""
    sess = INTERACTIVE_SESSIONS.get(req.job_id)
    if sess is None:
        raise HTTPException(status_code=404, detail="session interactive inconnue (expirée ?)")
    rows = len(req.grid)
    cols = len(req.grid[0]) if req.grid else 0
    if rows < 1 or cols < 1:
        raise HTTPException(status_code=400, detail="grille vide")
    # Same conversion as POST /api/interactive/step — see InteractiveClean
    # Request's own `challenge_words` docstring.
    challenge_words = frozenset(
        grid_form
        for w in req.challenge_words
        if w and (grid_form := challenge_word_grid_form(w))
    )
    result = await asyncio.to_thread(
        interactive_clean_impossible_zones,
        [list(row) for row in req.grid], rows, cols,
        sess["index"], sess["rng"], challenge_words,
    )
    grid = result["grid"]
    removed_black_count = 0
    if req.deep:
        black_result = await asyncio.to_thread(
            interactive_minimize_black_cells, grid, rows, cols, sess["index"], sess["rng"],
            challenge_words,
        )
        if black_result["changed"]:
            grid = black_result["grid"]
            removed_black_count = black_result["removed_count"]
    imp, low = await asyncio.to_thread(
        _interactive_fill_diagnostics, grid, rows, cols, sess["index"], challenge_words,
    )
    return {
        "width": cols, "height": rows,
        "grid": grid,
        "changed": result["changed"] or removed_black_count > 0,
        "cleared_count": result["cleared_count"],
        "removed_black_count": removed_black_count,
        "impossible_cells": imp,
        "low_candidate_cells": low,
    }


@app.post("/api/interactive/candidates")
async def interactive_candidates(req: InteractiveCandidatesRequest):
    """"Mots" button: list every real dictionary word compatible with the
    letters already posed on the selected slot (`req.cells`), split into
    the applicable theme-glossary matches (always shown first) and every
    other match (capped, see `INTERACTIVE_SLOT_CANDIDATES_LIMIT`) — each
    entry an `{"word", "unsafe"}` dict, `unsafe` flagging which of that
    candidate's own letters would create a new impossible crossing slot if
    placed — see `interactive_slot_candidates`."""
    sess = INTERACTIVE_SESSIONS.get(req.job_id)
    if sess is None:
        raise HTTPException(status_code=404, detail="session interactive inconnue (expirée ?)")
    rows = len(req.grid)
    cols = len(req.grid[0]) if req.grid else 0
    if rows < 1 or cols < 1:
        raise HTTPException(status_code=400, detail="grille vide")
    cells = [tuple(c) for c in req.cells]
    if len(cells) < 2:
        raise HTTPException(status_code=400, detail="emplacement invalide")
    # Same conversion as POST /api/interactive/step — see InteractiveImpossibleRequest's
    # own `challenge_words` docstring.
    challenge_words = frozenset(
        grid_form
        for w in req.challenge_words
        if w and (grid_form := challenge_word_grid_form(w))
    )
    theme_words, other_words = await asyncio.to_thread(
        interactive_slot_candidates,
        [list(row) for row in req.grid], rows, cols,
        sess["index"], cells, sess["priority_words"], challenge_words,
    )
    return {"theme_words": theme_words, "other_words": other_words}


@app.post("/api/interactive/crossing")
async def interactive_crossing(req: InteractiveCrossingRequest):
    """"Croisés" button: for the selected cell, list every letter
    compatible with a real dictionary word in BOTH the horizontal AND the
    vertical emplacement crossing there (restricted by whatever letters
    are already in place elsewhere on the grid), each paired with its own
    matching horizontal/vertical candidate words (`{"word", "unsafe"}`
    dicts, `unsafe` flagging which of that candidate's own letters would
    create a new impossible crossing slot if placed) — see
    `interactive_crossing_words`."""
    sess = INTERACTIVE_SESSIONS.get(req.job_id)
    if sess is None:
        raise HTTPException(status_code=404, detail="session interactive inconnue (expirée ?)")
    rows = len(req.grid)
    cols = len(req.grid[0]) if req.grid else 0
    if rows < 1 or cols < 1:
        raise HTTPException(status_code=400, detail="grille vide")
    if len(req.cell) != 2:
        raise HTTPException(status_code=400, detail="case invalide")
    cell = (req.cell[0], req.cell[1])
    if not (0 <= cell[0] < rows and 0 <= cell[1] < cols):
        raise HTTPException(status_code=400, detail="case hors grille")
    # Same conversion as POST /api/interactive/step — see InteractiveImpossibleRequest's
    # own `challenge_words` docstring.
    challenge_words = frozenset(
        grid_form
        for w in req.challenge_words
        if w and (grid_form := challenge_word_grid_form(w))
    )
    across_start, down_start, letters = await asyncio.to_thread(
        interactive_crossing_words,
        [list(row) for row in req.grid], rows, cols, sess["index"], cell, challenge_words,
    )
    return {"across_start": across_start, "down_start": down_start, "letters": letters}


@app.post("/api/interactive/boundary")
async def interactive_boundary(req: InteractiveBoundaryRequest):
    """"Début"/"Fin" buttons: list every real dictionary word that could
    start (`side="start"`) or end (`side="end"`) the selected slot
    (`req.cells`), from length 2 up to the slot's own full length,
    compatible with the letters already posed — a shorter word is only
    offered when the cell right beyond it could actually turn black (never
    already carrying a letter, and structurally valid) — split into theme-
    glossary matches and every other match, both sorted by length then
    alphabetically, each entry an `{"word", "unsafe"}` dict (`unsafe`
    flagging which of that candidate's own letters would create a new
    impossible crossing slot if placed) — see `interactive_boundary_
    candidates`."""
    sess = INTERACTIVE_SESSIONS.get(req.job_id)
    if sess is None:
        raise HTTPException(status_code=404, detail="session interactive inconnue (expirée ?)")
    rows = len(req.grid)
    cols = len(req.grid[0]) if req.grid else 0
    if rows < 1 or cols < 1:
        raise HTTPException(status_code=400, detail="grille vide")
    cells = [tuple(c) for c in req.cells]
    if len(cells) < 2:
        raise HTTPException(status_code=400, detail="emplacement invalide")
    if req.side not in ("start", "end"):
        raise HTTPException(status_code=400, detail="côté invalide")
    # Same conversion as POST /api/interactive/step — see InteractiveImpossibleRequest's
    # own `challenge_words` docstring.
    challenge_words = frozenset(
        grid_form
        for w in req.challenge_words
        if w and (grid_form := challenge_word_grid_form(w))
    )
    theme_words, other_words = await asyncio.to_thread(
        interactive_boundary_candidates,
        [list(row) for row in req.grid], rows, cols,
        sess["index"], cells, req.side, sess["priority_words"], challenge_words,
    )
    return {"theme_words": theme_words, "other_words": other_words}


@app.post("/api/interactive/impossible")
async def interactive_impossible(req: InteractiveImpossibleRequest):
    """"Impossibles" button: recompute `_interactive_fill_diagnostics` for
    the grid exactly as given (no cleanup, no mutation) — see
    InteractiveImpossibleRequest's own docstring."""
    sess = INTERACTIVE_SESSIONS.get(req.job_id)
    if sess is None:
        raise HTTPException(status_code=404, detail="session interactive inconnue (expirée ?)")
    rows = len(req.grid)
    cols = len(req.grid[0]) if req.grid else 0
    if rows < 1 or cols < 1:
        raise HTTPException(status_code=400, detail="grille vide")
    # Same conversion as POST /api/interactive/step — see InteractiveImpossibleRequest's
    # own `challenge_words` docstring.
    challenge_words = frozenset(
        grid_form
        for w in req.challenge_words
        if w and (grid_form := challenge_word_grid_form(w))
    )
    imp, low = await asyncio.to_thread(
        _interactive_fill_diagnostics, [list(row) for row in req.grid], rows, cols, sess["index"],
        challenge_words,
    )
    return {"impossible_cells": imp, "low_candidate_cells": low}


@app.post("/api/interactive/stats")
async def interactive_stats(req: InteractiveStatsRequest):
    """"Stats" button: for every empty cell, the statistically most likely
    letter — see InteractiveStatsRequest's own docstring."""
    sess = INTERACTIVE_SESSIONS.get(req.job_id)
    if sess is None:
        raise HTTPException(status_code=404, detail="session interactive inconnue (expirée ?)")
    rows = len(req.grid)
    cols = len(req.grid[0]) if req.grid else 0
    if rows < 1 or cols < 1:
        raise HTTPException(status_code=400, detail="grille vide")
    letters = await asyncio.to_thread(
        _interactive_letter_stats, [list(row) for row in req.grid], rows, cols, sess["index"],
    )
    return {"letters": letters}


@app.post("/api/interactive/verify")
async def interactive_verify(req: InteractiveVerifyRequest):
    """"Vérifier" button: check every currently complete word of the whole
    grid against the real dictionary in one pass — a plain, per-length set
    lookup, resolved per word against its own direction's dictionary
    (`sess["index"].for_direction(word.direction)`) so a genuinely
    bilingual session (see `_load_interactive_index`'s own `bilingual_
    language`) checks a down word against the second language's own
    lexicon instead of always the primary one — for an ordinary
    monolingual session `.across`/`.down` are still the identical index,
    so this is a no-op there. Returns the subset of `req.words`' own
    answer strings that aren't real dictionary words; the frontend already
    knows each complete word's own missing-definition status locally, so
    that half of the check never needs a round trip at all. Note: a word
    whose exact spelling happens to be valid in one direction but not the
    other (a rare cross-language coincidence) is still only ever reported
    back as a bare answer string, matching by content on the frontend —
    an accepted, disclosed simplification, not a full per-slot round
    trip."""
    sess = INTERACTIVE_SESSIONS.get(req.job_id)
    if sess is None:
        raise HTTPException(status_code=404, detail="session interactive inconnue (expirée ?)")

    # Same conversion as POST /api/interactive/step — see InteractiveVerifyRequest's
    # own `challenge_words` docstring.
    challenge_words = frozenset(
        grid_form
        for w in req.challenge_words
        if w and (grid_form := challenge_word_grid_form(w))
    )

    def _check():
        invalid = []
        seen = set()
        for w in req.words:
            key = (w.direction, w.answer)
            if key in seen:
                continue
            seen.add(key)
            if w.answer in challenge_words:
                continue
            idx = sess["index"].for_direction(w.direction)
            entry = idx.get(len(w.answer))
            if not entry or w.answer not in entry["words"]:
                invalid.append(w.answer)
        return invalid

    invalid_words = await asyncio.to_thread(_check)
    return {"invalid_words": invalid_words}


@app.post("/api/interactive/title")
async def interactive_title(req: InteractiveTitleRequest):
    """"Proposer un titre" button: one best-effort LLM call for up to
    TITLE_PROPOSALS_COUNT (10) candidate titles for the whole grid,
    displayed as a pick list the same way "Proposer une définition"
    already shows its own proposals — see backend/clues.py's
    `LLMClueGenerator.generate_titles`, at the user's explicit request.
    Returns `{"titles": [...]}`, possibly `[]` (never an error) on any
    failure.

    The theme steering the request carries — `req.theme` if given
    (the CURRENT "Thématique" field, per InteractiveTitleRequest's own
    docstring), else this session's own stored `theme_description` (a
    themed FRESH generation's rich LLM sentence — see `_run_generate_
    job`/`_build_theme_glossary`), else its own raw `theme` string (a
    re-edited grid, whose `theme_description` is never recomputed — see
    `_run_interactive_resume_job`) — mirrors the same fallback chain
    `req.theme` itself documents, so a caller that omits it entirely
    still benefits from whatever theme context the session already has."""
    entries = [
        (w["answer"], w.get("accented") or w["answer"], w.get("canonical") or w["answer"])
        for w in req.words
        if w.get("answer")
    ]
    meta = (JOBS.get(req.job_id) or {}).get("interactive") or {}
    theme_description = (
        (req.theme or "").strip()
        or meta.get("theme_description")
        or meta.get("theme")
        or None
    )
    try:
        titles = await asyncio.to_thread(
            interactive_clue_generator.generate_titles, entries, req.language,
            count=TITLE_PROPOSALS_COUNT, theme_description=theme_description,
        )
    except Exception:
        logger.exception("interactive title generation failed")
        titles = []
    return {"titles": titles}


@app.post("/api/interactive/save")
async def interactive_save(req: InteractiveSaveRequest):
    """"Publier" button: rebuild a generate_grid()-shaped result from the
    final editable grid + hand-written definitions, save it to the
    library tagged interactive=True ("(Création)"), best-effort SVG/PNG.
    If this session started from an existing library grid ("Ouvrir en
    mode Interactif"), carries that grid's own origin snapshot
    (job["interactive"]["origin"], set by _run_interactive_resume_job)
    onto the new record — see grid_store.save_grid_json's own `origin`
    parameter. Returns the new library id."""
    rows = len(req.grid)
    cols = len(req.grid[0]) if req.grid else 0
    if rows < 1 or cols < 1:
        raise HTTPException(status_code=400, detail="grille vide")
    bw = [["#" if ch == "#" else "." for ch in row] for row in req.grid]
    slots = extract_slots(bw, rows, cols)
    assignment = ["".join(req.grid[r][c] for (r, c) in cells) for cells in slots]
    words = build_word_entries(bw, rows, cols, slots, assignment)
    clue_by_key = {
        (d["row"], d["col"], d["direction"]): (d.get("clue") or "")
        for d in req.definitions
    }
    # A down word on a genuinely bilingual grid is in the second language,
    # mirroring crossword_gen.generate_grid()'s own per-word `language`
    # (see its own docstring) — an ordinary monolingual grid (bilingual_
    # language None/identical to language) leaves every word on the
    # primary language, unchanged from before this field existed.
    is_bilingual = bool(req.bilingual_language) and req.bilingual_language != req.language
    for w in words:
        w["clue"] = clue_by_key.get((w["row"], w["col"], w["direction"]), "")
        w.setdefault("accented", w["answer"])
        w.setdefault("canonical", w["answer"])
        w["language"] = req.bilingual_language if (is_bilingual and w["direction"] == "down") else req.language
    solution = build_letters_grid(rows, cols, slots, assignment)
    n_black = sum(c == "#" for row in bw for c in row)
    result = {
        "width": cols,
        "height": rows,
        "pattern": bw,
        "solution": solution,
        "words": words,
        "word_count": len(words),
        "black_count": n_black,
        "black_ratio": n_black / (rows * cols) if rows and cols else 0,
        "language": req.language,
        "bilingual_language": req.bilingual_language if is_bilingual else None,
        "generation_duration_seconds": 0,
        "optimization_duration_seconds": 0,
        "clues_duration_seconds": 0,
        "difficulty": req.difficulty,
        "theme": (req.theme or "").strip() or None,
        "title": req.title,
    }
    try:
        svg_path = await asyncio.to_thread(
            save_grid_svg, result, req.language, req.difficulty, "interactive",
        )
        try:
            await asyncio.to_thread(save_grid_png, svg_path)
        except OSError:
            pass
    except OSError:
        logger.warning("interactive save: SVG/PNG export skipped")
    pseudo = (req.pseudo or "").strip()[:MAX_PSEUDO_LENGTH] or None
    # Read once, up front, so both saves below (the new library record and
    # the GRID_WORK snapshot further down) agree on the same origin
    # snapshot — see grid_store.save_grid_json/save_grid_work's own
    # `origin` parameter and _run_interactive_resume_job's own comment on
    # job["interactive"]["origin"].
    meta = (JOBS.get(req.job_id) or {}).get("interactive") or {}
    grid_id = await asyncio.to_thread(
        save_grid_json, result, req.language, req.difficulty, "interactive",
        req.title, req.bilingual_language if is_bilingual else None, pseudo,
        (req.theme or "").strip() or None, True, meta.get("origin"),
        # See grid_store.save_grid_json's own `generation_params`
        # docstring — carried through as-is (None for a session started
        # by hand, the real values for a grid re-edited from an
        # automatic generation via "Ouvrir en mode Interactif"), so the
        # settings' own provenance survives publication just like
        # "origin"/"theme" above.
        generation_params=meta.get("generation_params"),
        # "Mots Défi (personnalisation)" — the client's own current list,
        # resent on every "Publier" click just like on every autosave
        # (`req.challenge_words`, not `job["interactive"]`, which is only
        # ever populated at session start/resume and never kept current
        # as the author edits the list — see InteractiveSaveRequest's own
        # `challenge_words` docstring), carried onto the published record
        # so "Ouvrir en mode Interactif" restores it later — see grid_
        # store.save_grid_json's own `challenge_words` docstring.
        challenge_words=req.challenge_words or None,
    )
    # Also refresh this session's own GRID_WORK snapshot to the final,
    # published state (at the user's explicit request: "Au moment de
    # Publier, sauvegarder la dernière version de la grille pour
    # l'utilisateur.") — best-effort, never fails the publish. The last
    # autosave was fired by "Suivant"/"Précédent", so it can be missing a
    # definition typed afterwards or a hand-edited title; this brings the
    # "Créations" entry up to date with what was actually published. The
    # published library grid (save_grid_json above) is a separate record
    # and is never affected by a later "Créations" delete (which only ever
    # calls delete_grid_work).
    sess = INTERACTIVE_SESSIONS.get(req.job_id)
    if sess is not None:
        try:
            await asyncio.to_thread(
                save_grid_work,
                req.job_id, req.grid, req.definitions, req.title,
                meta.get("language", req.language),
                meta.get("difficulty", req.difficulty),
                meta.get("theme") or ((req.theme or "").strip() or None),
                sess["priority_words"], sess.get("seed", 0), pseudo,
                sess.get("resumed_from"), meta.get("origin"),
                meta.get("bilingual_language"),
                generation_params=meta.get("generation_params"),
                # Same reasoning as the save_grid_json call above: the
                # client's own current list (`req.challenge_words`), not
                # the stale `job["interactive"]` snapshot — without this,
                # publishing silently wiped out this same GRID_WORK file's
                # own "Mots Défi" list (the parameter defaults to None,
                # promoted by save_grid_work to an empty list) even though
                # the session's last real autosave had already correctly
                # saved it.
                challenge_words=req.challenge_words,
            )
        except Exception:
            logger.warning("interactive save: GRID_WORK snapshot skipped", exc_info=True)
    return {"grid_id": grid_id}


# ---------------------------------------------------------------------------
# GRID_WORK — autosaved, resumable "Interactif" work-in-progress sessions
# (the "Créations" panel), at the user's explicit request: "chaque appui sur
# Suivant/Précédent sauvegarde l'état en cours du process de création dans
# le dossier GRID_WORK... afficher un panneau avec la liste. Il peut cliquer
# pour relancer sa session interactive où elle s'était arrêtée, cliquer sur
# un bouton icône pour supprimer la tâche." See backend/grid_store.py's own
# entry for the storage-side design (one continuously-overwritten file per
# session, found again by its own job_id suffix).
# ---------------------------------------------------------------------------

@app.post("/api/interactive/save_work")
async def interactive_save_work(req: InteractiveSaveWorkRequest):
    """Autosave fired by the frontend after every "Suivant"/"Précédent"
    click — see InteractiveSaveWorkRequest. `language`/`bilingual_
    language`/`difficulty`/`theme`/`origin`/`generation_params` are read
    back from `JOBS[req.job_id]["interactive"]` (set once at session
    start/resume) rather than trusted from the request body."""
    sess = INTERACTIVE_SESSIONS.get(req.job_id)
    if sess is None:
        raise HTTPException(status_code=404, detail="session interactive inconnue (expirée ?)")
    meta = (JOBS.get(req.job_id) or {}).get("interactive") or {}
    pseudo = (req.pseudo or "").strip()[:MAX_PSEUDO_LENGTH] or None
    work_id = await asyncio.to_thread(
        save_grid_work,
        req.job_id, req.grid, req.definitions, req.title,
        meta.get("language", "fr"), meta.get("difficulty", "easy"), meta.get("theme"),
        sess["priority_words"], sess.get("seed", 0), pseudo,
        sess.get("resumed_from"), meta.get("origin"),
        meta.get("bilingual_language"),
        generation_params=meta.get("generation_params"),
        challenge_words=req.challenge_words,
    )
    return {"work_id": work_id}


@app.get("/api/interactive/work")
async def interactive_work_list(pseudo: str = ""):
    """"Créations" panel: every saved work-in-progress belonging to
    `pseudo` (blank = no filter), most recently updated first — see
    grid_store.list_grid_work."""
    items = await asyncio.to_thread(list_grid_work, pseudo)
    return {"items": items}


@app.post("/api/interactive/work/delete")
async def interactive_work_delete(req: InteractiveWorkIdRequest):
    """"Créations" panel's own delete-icon button: permanently removes one
    saved work-in-progress file. Never 404s on an already-gone/unknown id
    — deleting something that isn't there already achieves what the
    caller wanted, so this just reports whether a file was actually
    removed."""
    deleted = await asyncio.to_thread(delete_grid_work, req.work_id)
    return {"deleted": deleted}


@app.post("/api/interactive/resume", status_code=202)
async def interactive_resume(req: InteractiveWorkIdRequest):
    """"Créations" panel: relaunch a saved work-in-progress session
    exactly where it stopped — a brand-new job_id/session (never the
    original one, long gone), seeded from the saved grid/definitions/
    title/theme-glossary/seed instead of a fresh, blank pattern — see
    _run_interactive_resume_job."""
    record = await asyncio.to_thread(get_grid_work, req.work_id)
    if record is None:
        raise HTTPException(status_code=404, detail="création introuvable (supprimée ?)")
    job_id = _new_job()
    task = asyncio.create_task(_run_interactive_resume_job(job_id, record))
    _BACKGROUND_TASKS.add(task)
    task.add_done_callback(_BACKGROUND_TASKS.discard)
    return {"job_id": job_id}


async def _run_interactive_resume_job(job_id, record):
    """Background job behind POST /api/interactive/resume. Unlike
    `_run_interactive_job` (fresh start: generates a brand-new pattern,
    places one first word), this rebuilds the session's own index/
    priority_words/rng from the saved record and hands the ALREADY-SAVED
    grid straight back — `priority_words` is reused verbatim (never
    re-derived by re-running the theme LLM/Qdrant lookup, which is
    neither deterministic nor cheap), only re-filtered against the
    freshly reloaded lexicon exactly like a fresh start already does.
    Fresh diagnostics (`_interactive_fill_diagnostics`) are recomputed on
    the resumed grid so the impossible/low-candidate highlights are
    accurate immediately, not stale from whenever it was last saved."""
    job = JOBS[job_id]
    short_id = job_id[:8]

    def progress(step, **data):
        job["step"] = {"code": step, **data}

    try:
        language = record.get("language", "fr")
        difficulty = record.get("difficulty", "easy")
        # See _load_interactive_index's own `bilingual_language` parameter.
        # Never fails the whole resume over a stale/unknown value (a
        # dictionary retired since this record was saved, say) — it just
        # degrades to a monolingual session in that case, the same
        # tolerance _iter_stored_grids/_library_page already extend to a
        # bilingual grid's own second language elsewhere in this file.
        bilingual_language = record.get("bilingual_language")
        if bilingual_language and (
            bilingual_language not in WORDLISTS or not WORDLISTS[bilingual_language].exists()
        ):
            bilingual_language = None
        if language not in WORDLISTS or not WORDLISTS[language].exists():
            job["status"] = "error"
            job["error_code"] = "internal_error"
            job["error"] = "Langue inconnue ou dictionnaire absent."
            return

        progress("interactive_building")
        index, known = await _load_interactive_index(language, difficulty, bilingual_language)
        priority_words = frozenset(
            u for w in (record.get("priority_words") or ())
            if (u := str(w).upper()) in known
        )
        seed = record.get("seed", 0)
        rng = random.Random(seed)
        # See grid_store.save_grid_json's own `generation_params`
        # docstring — the automatic search engine's own parameters (Taux
        # noir/Graines/Mode/Précision thématique) that produced this
        # grid, None for a grid never derived from an automatic
        # generation. Mirrored onto both job["interactive"] and
        # job["result"] (pollJob() only ever returns the latter), the
        # same convention as "theme"/"origin" below, so
        # enterInteractiveMode() can reconfigure the form's own Mode/Taux
        # noir/Graines/Précision thématique fields identically.
        generation_params = record.get("generation_params")
        grid = record.get("grid") or []
        rows = len(grid)
        cols = len(grid[0]) if grid else 0
        # Same conversion as POST /api/interactive/step — see
        # InteractiveStepRequest's own `challenge_words` docstring — so a
        # resumed session's own initial diagnostics already exempt its
        # "Mots Défi" words, same as every other diagnostics call site.
        challenge_words = frozenset(
            grid_form
            for w in (record.get("challenge_words") or ())
            if w and (grid_form := challenge_word_grid_form(w))
        )
        imp, low = await asyncio.to_thread(
            _interactive_fill_diagnostics, grid, rows, cols, index, challenge_words,
        )

        INTERACTIVE_SESSIONS[job_id] = {
            "index": index,
            "priority_words": priority_words,
            "rng": rng,
            "seed": seed,
            # The GRID_WORK file this session started from — read once by
            # the very next autosave (POST /api/interactive/save_work) so
            # it renames/adopts that same file instead of silently
            # orphaning it while creating a brand-new one under this
            # session's own fresh job_id (see grid_store.save_grid_work's
            # own docstring for the full reasoning).
            "resumed_from": record.get("id"),
        }
        job["interactive"] = {
            "language": language,
            "bilingual_language": bilingual_language,
            "difficulty": difficulty,
            "width": cols,
            "height": rows,
            "theme": record.get("theme"),
            "has_theme": bool(priority_words),
            "theme_description": None,
            "generation_params": generation_params,
            # Snapshot of the grid this session was derived from — set
            # only when `record` itself carries one (a library grid
            # opened via "Ouvrir en mode Interactif", or a GRID_WORK
            # entry that already carried its own — see
            # _library_record_to_interactive/save_grid_work's own
            # `origin`) — None for an ordinary fresh session/resume.
            # Read back by POST /api/interactive/save[_work] and passed
            # straight through to grid_store.save_grid_json/
            # save_grid_work so the provenance survives both a publish
            # and a pause/resume of the editing session.
            "origin": record.get("origin"),
            # "Mots Défi" list this session was resumed with — see
            # grid_store.save_grid_work's own `challenge_words` docstring.
            # Not otherwise consulted from `job["interactive"]` (the
            # frontend resends its own live list on every later autosave/
            # "Suivant" instead — see InteractiveStepRequest/
            # InteractiveSaveWorkRequest); only mirrored onto job["result"]
            # below, like "theme"/generation_params, so enterInteractive
            # Mode() can restore it on this entry path too.
            "challenge_words": record.get("challenge_words") or [],
        }
        job["result"] = {
            "width": cols,
            "height": rows,
            "grid": grid,
            "placed": None,
            "impossible": False,
            "has_theme": bool(priority_words),
            "impossible_cells": imp,
            "low_candidate_cells": low,
            # Only ever set on a resume result (a fresh start's own result
            # has neither yet) — enterInteractiveMode() uses these two to
            # restore interactiveDefs/the title input, which a fresh
            # session never needs to do.
            "definitions": record.get("definitions") or [],
            "title": record.get("title") or "",
            # The raw theme string this session started from — mirrors
            # job["interactive"]["theme"] above, at the user's explicit
            # request: "Quand un utilisateur réédite une grille
            # thématique, renseigner le champ Thématique avec les mots de
            # la grille d'origine." pollJob() only ever returns job
            # ["result"], never job["interactive"], so it needs to be
            # here too for enterInteractiveMode() to re-fill the
            # "Thématique" field — works identically whether `record`
            # came from a real GRID_WORK resume or from a library grid
            # reshaped by _library_record_to_interactive.
            "theme": record.get("theme"),
            # Same reasoning as "theme" above, and as the fresh-start
            # path's own job["result"] in _run_interactive_job: pollJob()
            # only ever returns job["result"], so language/difficulty/
            # bilingual_language (already on job["interactive"]) need to be
            # mirrored here too for enterInteractiveMode() to keep
            # interactiveLanguage/interactiveDifficulty/
            # interactiveBilingualLanguage correct on this entry path too.
            "language": language,
            "bilingual_language": bilingual_language,
            "difficulty": difficulty,
            # Same reasoning again, for enterInteractiveMode()'s own
            # Mode/Taux noir/Graines/Précision thématique restore — see
            # grid_store.save_grid_json's own `generation_params`
            # docstring.
            "generation_params": generation_params,
            # Same reasoning again, for enterInteractiveMode()'s own "Mots
            # Défi" list restore — see job["interactive"]["challenge_words"]
            # just above.
            "challenge_words": record.get("challenge_words") or [],
        }
        progress("done")
        job["status"] = "done"
    except GenerationCancelled:
        job["status"] = "cancelled"
    except Exception:
        job["status"] = "error"
        job["error_code"] = "internal_error"
        job["error"] = "Erreur interne."
        logger.exception("[%s] unhandled error during interactive resume", short_id)


def _library_record_to_interactive(record):
    """Reshapes a finished library grid record (grid_store.get_grid — a
    generate_grid()-shaped dict with pattern/solution/words) into the
    minimal GRID_WORK-shaped dict `_run_interactive_resume_job` already
    consumes, so opening a library grid in "Interactif" mode reuses the
    entire resume machinery (diagnostics recompute, definitions/title
    restore) with no duplication.

    The `solution` grid is already exactly the interactive-grid
    convention (build_letters_grid: "#" for a black cell, an uppercase
    letter for every white one — a finished grid has no empty white
    cells, so no "." placeholder ever appears); `pattern` is the
    fallback if `solution` is somehow absent (an all-blank editable
    grid). Every word carries its own row/col/direction/clue, so the
    GRID_WORK `definitions` list ({row, col, direction, clue}) is a
    direct projection.

    Deliberately carries NO top-level `id` key of its own (`_run_
    interactive_resume_job` reads `record.get("id")` into the session's
    `resumed_from`, which grid_store.save_grid_work only honours for a
    real GRID_WORK id — `_WORK_ID_RE` — so with it absent, the first
    autosave simply creates a fresh GRID_WORK file, exactly "créer une
    nouvelle tâche dans GRID_WORK"). `priority_words` stays empty even
    for a themed grid: the raw `theme` string is kept (shown in
    "Créations"/on save) but the resolved Qdrant glossary was never
    stored on a library record — the same limitation a recompute job
    already has (see _run_recompute_job). `seed` is a fixed 0: interactive
    placement only needs *a* reproducible starting point, not a
    continuation of any prior RNG stream.

    `origin` is a snapshot of THIS library record's own id/title/pseudo/
    created_at — at the user's explicit request: "Quand un utilisateur
    modifie une grille sélectionnée dans la Bibliothèque, conserver dans
    la sauvegarde de la nouvelle grille, les information sur la grille
    d'origine : nom de la grille, date de création de la grille, auteur
    de la grille, ID de la grille." `_run_interactive_resume_job` reads
    it into `job["interactive"]["origin"]`, from where every later save
    (POST /api/interactive/save[_work]) picks it up — see grid_store.
    save_grid_json/save_grid_work's own `origin` parameter."""
    grid = record.get("solution") or record.get("pattern") or []
    definitions = [
        {
            "row": w.get("row"),
            "col": w.get("col"),
            "direction": w.get("direction"),
            "clue": w.get("clue", ""),
        }
        for w in (record.get("words") or [])
    ]
    return {
        "language": record.get("language", "fr"),
        # The library record's own second (vertical-words) language field
        # is "bilingual" (see grid_store.save_grid_json), never "bilingual_
        # language" — translated to the latter key here so _run_
        # interactive_resume_job can read every source (a real GRID_WORK
        # record, or this library-shaped dict) the same generic way, at
        # the user's explicit request: "quand une grille bilingue est
        # chargée, configurer les langues dans celles de la grille."
        "bilingual_language": record.get("bilingual"),
        "difficulty": record.get("difficulty", "easy"),
        "grid": grid,
        "definitions": definitions,
        "title": record.get("title") or "",
        "theme": record.get("theme"),
        # See grid_store.save_grid_json's own `generation_params`
        # docstring — None for a grid never derived from an automatic
        # generation (e.g. a grid itself built by hand in Interactive
        # mode, then published).
        "generation_params": record.get("generation_params"),
        # "Mots Défi (personnalisation)" — see grid_store.save_grid_json's
        # own `challenge_words` docstring; `_run_interactive_resume_job`
        # reads it back via `record.get("challenge_words") or []`, same
        # as it already does for a real GRID_WORK record.
        "challenge_words": record.get("challenge_words") or [],
        "priority_words": [],
        "seed": 0,
        "origin": {
            "id": record.get("id"),
            "title": record.get("title") or "",
            "pseudo": record.get("pseudo"),
            "created_at": record.get("created_at"),
        },
    }


@app.post("/api/interactive/from-library", status_code=202)
async def interactive_from_library(req: InteractiveFromLibraryRequest):
    """Open a finished library grid in the "Interactif" authoring mode —
    a brand-new job_id/session (the stored library record is never
    touched), seeded from that grid's own filled pattern/definitions/
    title instead of a fresh blank pattern, so the player can edit an
    existing grid and it becomes its own new GRID_WORK "Créations" entry
    from the first autosave on. Reuses _run_interactive_resume_job
    wholesale via _library_record_to_interactive."""
    record = await asyncio.to_thread(get_grid, req.grid_id)
    if record is None:
        raise HTTPException(status_code=404, detail="grille introuvable dans la bibliothèque")
    job_id = _new_job()
    synthetic = _library_record_to_interactive(record)
    task = asyncio.create_task(_run_interactive_resume_job(job_id, synthetic))
    _BACKGROUND_TASKS.add(task)
    task.add_done_callback(_BACKGROUND_TASKS.discard)
    return {"job_id": job_id}


@app.post("/api/interactive/finish", status_code=202)
async def interactive_finish(req: InteractiveFinishRequest):
    """"Finir la grille" button of the "Interactif" authoring mode, at the
    user's explicit request: "lance une génération automatique en
    verrouillant définitivement les lettres déjà positionnées..., y
    compris la génération des définitions manquantes (mais pas celles
    déjà définies)." Every already-placed letter of the current editable
    grid becomes a hard, permanent constraint (`locked_letters` — see
    crossword_gen.generate_grid's own docstring: a locked cell can never
    be blackened, and its letter is never dropped by any later cleanup
    pass, unlike the merely-carried-forward "confirmed" content an
    ordinary retry cycle produces) via the exact same `resume_state`
    mechanism already built for the "Continuer" button — the ordinary
    automatic engine (_run_generate_job) then takes over from there,
    adding new black cells / words wherever still needed, exactly like a
    fresh generation would.

    Reuses the interactive session's own already-resolved theme glossary
    verbatim (`override_priority_words`/`override_theme_description` —
    see _run_generate_job's own docstring) rather than re-running the
    LLM/Qdrant theme pre-search a second time. Every word whose exact
    TEXT (never its position — see `preserved_clues`'s own construction
    below, and `_run_generate_job`'s docstring, for why matching by
    (row, col, direction) alone silently breaks the moment the search
    extends a word into a boundary cell that wasn't yet decided black)
    already carried a real definition at the time of the click keeps that
    clue untouched — only a genuinely NEW word (one the search itself
    completed, or whose own final text no longer matches what was typed)
    ever gets sent to the LLM.

    Returns a brand-new job_id, polled exactly like an ordinary
    generation (GET /api/generate/status/{job_id}) — the interactive
    session itself (`req.job_id`) is left completely untouched, the same
    "never mutate the job it's derived from" convention already
    established for "Continuer".

    Never publishes to the Bibliothèque, at the user's explicit request:
    "à la fin du processus, ne pas publier la grille. Ajouter la nouvelle
    version aux Créations de l'auteur. Réouvrir la grille automatiquement
    en mode édition." (`_run_generate_job(publish=False, origin=...)` —
    see its own docstring). The originating session's own `origin`
    snapshot (if any — e.g. this session was itself opened from an
    existing library/GRID_WORK grid) is carried through so the new draft
    keeps the same provenance.

    "Finir la zone" (`req.zone_cells`, `None`/empty by default — a plain
    "Finir la grille" run otherwise) is the same underlying mechanism,
    restricted to a player-selected sub-region — went through two earlier
    designs, both rejected once seen live, before landing on this one, at
    the user's own explicit, precise correction each time:

    1. First: every blank cell OUTSIDE the selection forced permanently
       black, synchronously, before generation even started. Rejected:
       "'Finir la zone' ne doit ABSOLUMENT PAS toucher aux cases
       verrouillées ! Il me met des noires partout !", confirmed to be
       "des cases blanches (et quelques noires) avant de lancer le
       process" turning solid black — on anything but a tiny selection,
       most of the grid, not something the search itself ever decided.
    2. Then: outside the selection left completely free, exactly like
       plain "Finir la grille" (the engine could add real letters OR new
       black cells there). Also rejected, directly and precisely: "'Finir
       la grille' ne doit toucher qu'aux cases strictement laissées non
       grisées (qui peuvent sortir de la zone carrée de sélection) ET ne
       pas toucher aux lettres et cases noires déjà en place dans la zone
       non grisée" — the search was still writing new LETTERS into cells
       the player considers off-limits, which is exactly as unwanted as
       turning them black.

    Final design: the search still runs over the WHOLE grid as before (the
    CSP model has no way to "skip" a region — every cell must resolve to a
    real letter or black to be searchable at all), completing freely
    everywhere exactly like design 2 — but its own answer for every cell
    OUTSIDE the zone is discarded afterward and reverted back to EXACTLY
    its pre-click value (`zone_revert`, built below, passed to
    `_run_generate_job` — see its own docstring for where/how the revert
    itself happens). Only the selected zone ever ends up genuinely
    complete; anything the player hasn't finished outside it stays exactly
    as they left it, to keep working on by hand or with another "Finir la
    zone" call — which is also why "Finir la grille"/"Finir la zone" have
    never published to the Bibliothèque (`publish=False` above): the
    result was always meant to be an intermediate editable draft, not a
    guaranteed-finished playable grid."""
    sess = INTERACTIVE_SESSIONS.get(req.job_id)
    job = JOBS.get(req.job_id)
    if sess is None or job is None or job.get("interactive") is None:
        raise HTTPException(status_code=404, detail="session interactive inconnue (expirée ?)")
    rows = len(req.grid)
    cols = len(req.grid[0]) if req.grid else 0
    if rows < 1 or cols < 1:
        raise HTTPException(status_code=400, detail="grille vide")

    meta = job["interactive"]
    # Black/white pattern + locked letters, the exact same conversion
    # `interactive_save` already established (see its own `bw`) — a wire
    # grid cell is either "#" (black), "." (empty white), or a real
    # uppercase letter.
    seed_grid = [["#" if ch == "#" else "." for ch in row] for row in req.grid]
    locked_letters = {
        (r, c): ch
        for r, row in enumerate(req.grid)
        for c, ch in enumerate(row)
        if ch not in ("#", ".")
    }
    # "Finir la zone" — see this endpoint's own docstring above for the
    # full design history. Final design: the zone's own already-black
    # cells stay fixed during the search (`permanent_black_cells`, same
    # protection mechanism as before, just still scoped to the zone
    # itself); every cell OUTSIDE the zone is recorded here, at its exact
    # current value, in `zone_revert` — `_run_generate_job` applies it
    # (overwriting the search's own answer back to this exact value) right
    # after `generate_grid()` returns, before anything else reads
    # `result` — see its own docstring.
    permanent_black_cells = None
    zone_revert = None
    if req.zone_cells:
        zone = {(c[0], c[1]) for c in req.zone_cells}
        permanent_black_cells = {
            (r, c)
            for r, row in enumerate(req.grid)
            for c, ch in enumerate(row)
            if ch == "#" and (r, c) in zone
        }
        zone_revert = {
            (r, c): ch
            for r, row in enumerate(req.grid)
            for c, ch in enumerate(row)
            if (r, c) not in zone
        }
    # `required_cells` (see generate_grid's own docstring), at the user's
    # explicit request: "quand toutes les cases non verrouillées... sont
    # remplies... la grille doit être considérée comme réussie, même si il
    # reste des emplacements non complets couvrant les cases verrouillées."
    # Every cell the search genuinely NEEDS to resolve to declare success —
    # a still-blank cell (never "#", never already lettered) that's also
    # inside the selected zone, or, for plain "Finir la grille" (no zone at
    # all), any still-blank cell of the whole grid. This makes the relaxed
    # completeness rule a provable no-op for plain "Finir la grille" (see
    # generate_grid's own docstring for why) — computed unconditionally
    # either way rather than only for "Finir la zone", so both code paths
    # share the exact same mechanism instead of one being special-cased.
    required_cells = {
        (r, c)
        for r, row in enumerate(req.grid)
        for c, ch in enumerate(row)
        if ch == "." and (not req.zone_cells or (r, c) in zone)
    }
    resume_state = _serialize_resume_state(seed_grid, locked_letters, None, None)
    # {word_answer: clue} for every definition already typed — keyed by the
    # WORD ITSELF, never by (row, col, direction), at the user's explicit
    # correction: "il ne faut pas se contenter de vérifier les positions :
    # des mots ont pu changer. Il faut vérifier si un mot présent n'a pas
    # déjà une définition, à partir du mot lui-même (peu importe où et dans
    # quel sens)." A position-keyed map (the original design) silently
    # broke the moment a word's own boundary cell wasn't already black at
    # the time of the click: `locked_letters` only pins down the cells the
    # player had already typed, never the cell(s) immediately beyond
    # them — a very common case in practice, since manually placing every
    # single terminating black cell defeats the whole point of "Finir la
    # grille". Left free, the search can extend such a word straight
    # through that still-open cell (e.g. a player-typed "CHAT" with an
    # undecided cell right after it can resolve into a real, longer word
    # like "CHATIE") — the (row, col, direction) key of that slot is
    # completely unchanged (same starting cell, same direction, the
    # ONLY thing extract_slots ever keys on), so the old code kept
    # matching it and wrongly attached the clue written for "CHAT" to a
    # word it was never written for — reproduced live (a hand-built grid
    # with exactly this shape) and confirmed fixed by this rewrite.
    # Matching by the word's own text instead is immune to this: only a
    # definition whose slot was ALREADY fully lettered (no
    # "." anywhere in its own maximal white run) at the moment of the
    # click is trusted at all — an incomplete slot's clue can't reliably
    # be attributed to any specific final word yet, so it's simply
    # dropped rather than guessed at (matching `frontend/static/
    # script.js`'s own `filled` convention, recomputed here server-side
    # rather than trusted from the client) — together this is exactly
    # "recalcule toutes les définitions, alors que certaines existaient
    # déjà" for a grid where boundaries were left for the automatic
    # engine to decide, as "Finir la grille" is meant to allow.
    # `Filler.used_words` already guarantees no two slots of one finished
    # grid ever share the same exact word, so a plain `{word: clue}` map
    # can never misattribute one preserved clue to two different final
    # placements.
    slots = extract_slots(seed_grid, rows, cols)
    slot_by_key = {
        (cells[0][0], cells[0][1], slot_direction(cells)): cells
        for cells in slots
    }
    preserved_clues = {}
    # A word that already carries a preserved clue must also keep its own
    # exact shape, not merely its letters — `locked_letters` above already
    # stops the search from ever blackening one of ITS OWN cells, but
    # nothing stopped `minimize_black_squares`'s own final optimization
    # pass (or, on a "reprise telle quelle" palier, `_optimize_before_
    # cleanup`/`_lengthen_impossible_zones`) from removing an ALREADY-
    # BLACK boundary cell right next to it, silently merging it with
    # whatever sits just beyond. Reused from before this fix, still keyed
    # off each preserved word's own CURRENT position (not its text) —
    # unrelated to how the clue itself gets matched back afterward: every
    # one of the 3 removal-capable functions `permanent_black_cells` is
    # threaded through already refuses to touch a cell listed here, so
    # widening this one set (rather than any new mechanism in the solver
    # itself) is enough — for each preserved-clue word, its own immediate
    # boundary cell(s) — right before its first cell, right after its
    # last, in its own direction — join the set whenever they're already
    # black, so they can never be reopened out from under it. This never
    # protects against extending into a boundary that's still open ("."),
    # which is exactly the case the word-based clue matching above exists
    # to tolerate correctly rather than prevent.
    protected_black_cells = set()
    for d in req.definitions:
        clue = (d.get("clue") or "").strip()
        if not clue:
            continue
        key = (d.get("row"), d.get("col"), d.get("direction"))
        cells = slot_by_key.get(key)
        if not cells:
            continue
        word = "".join(req.grid[r][c] for (r, c) in cells)
        if "." in word:
            continue  # slot not actually complete yet — nothing reliable to attribute this clue to
        preserved_clues[word] = clue
        (r0, c0), (r1, c1) = cells[0], cells[-1]
        if key[2] == "across":
            boundary_cells = ((r0, c0 - 1), (r1, c1 + 1))
        else:
            boundary_cells = ((r0 - 1, c0), (r1 + 1, c1))
        for (br, bc) in boundary_cells:
            if 0 <= br < rows and 0 <= bc < cols and seed_grid[br][bc] == "#":
                protected_black_cells.add((br, bc))
    if protected_black_cells:
        permanent_black_cells = (permanent_black_cells or set()) | protected_black_cells
    genreq = GenerateRequest(
        language=meta["language"],
        bilingual_language=meta.get("bilingual_language"),
        width=cols,
        height=rows,
        difficulty=meta.get("difficulty", "easy"),
        seed=random.randrange(2**31),
        force_letters_percent=req.force_letters_percent,
        black_enrichment_percent=req.black_enrichment_percent,
        mode=req.mode,
        theme=meta.get("theme") or None,
        pseudo=req.pseudo,
    )
    _validate_generate_request(genreq)
    new_job_id = _new_job()
    task = asyncio.create_task(
        _run_generate_job(
            new_job_id, genreq, resume_state=resume_state,
            override_priority_words=sess["priority_words"],
            override_theme_description=meta.get("theme_description") or "",
            preserved_clues=preserved_clues,
            permanent_locked_letters=locked_letters,
            permanent_black_cells=permanent_black_cells,
            publish=False, origin=meta.get("origin"),
            zone_revert=zone_revert,
            required_cells=required_cells,
        )
    )
    _BACKGROUND_TASKS.add(task)
    task.add_done_callback(_BACKGROUND_TASKS.discard)
    return {"job_id": new_job_id}


# The five phases exposed by GET /api/generate/phase/{job_id}, at the
# user's explicit request ("file d'attente grille, génération de la
# grille, file d'attente définition, génération des définitions, grille
# terminée") — plus "error"/"cancelled" for a job that will never finish
# normally. A stable summary of `job["step"]["code"]` (see
# _run_generate_job's own progress() closure), meant for an automation
# client (Automation/Populate.py) that just wants to know "where is this
# job at" without needing to know the dozen or so internal codes
# (pattern, pattern_attempt_failed, minimizing, pre_cleanup_optimized...).
_GRID_GENERATION_STEPS = frozenset({
    "starting", "theme", "interactive_building", "pattern", "pattern_generated",
    "pattern_attempt_failed", "pattern_found", "pattern_failed",
    "pre_cleanup_optimizing", "pre_cleanup_optimized", "minimizing", "grid_ready",
})
_CLUES_GENERATION_STEPS = frozenset({"clues", "saving"})


def _job_phase(job):
    """Reduces a job's internal state down to one of the five public
    phases (+ error/cancelled). `queued_grid`/`queued_clues` are only ever
    set by _wait_in_queue while there's genuine contention; with no
    queue, a job goes straight from `starting` to actually generating, so
    `grid_queue`/`clues_queue` can simply never appear — that's normal."""
    status = job.get("status")
    if status in ("done", "error", "cancelled"):
        return status
    code = (job.get("step") or {}).get("code")
    if code == "queued_grid":
        return "grid_queue"
    if code == "queued_clues":
        return "clues_queue"
    if code in _CLUES_GENERATION_STEPS:
        return "clues_generation"
    # `starting`, every grid-search code, and any unexpected code as long
    # as the job is still running.
    return "grid_generation"


@app.get("/api/generate/phase/{job_id}")
def generate_phase(job_id: str):
    """A generation job's phase code, at the user's explicit request.
    `phase` is one of: "grid_queue", "grid_generation", "clues_queue",
    "clues_generation", "done", or "error"/"cancelled". The other fields
    (the raw step_code, queue position/length, clue progress) are there
    for an automation client's own convenience and can be ignored."""
    job = JOBS.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="job inconnu (expiré ou jamais existé)")
    step = job.get("step") or {}
    phase = _job_phase(job)
    out = {
        "job_id": job_id,
        "phase": phase,
        "finished": phase in ("done", "error", "cancelled"),
        "status": job.get("status"),
        "step_code": step.get("code"),
    }
    if step.get("code") in ("queued_grid", "queued_clues"):
        out["queue_position"] = step.get("position")
        out["queue_length"] = step.get("queue_length")
    if step.get("code") == "clues":
        out["clues_done"] = step.get("current")
        out["clues_total"] = step.get("total")
    if job.get("error_code"):
        out["error_code"] = job["error_code"]
    if phase == "error" and job.get("error"):
        out["error"] = job["error"]
    return out


@app.post("/api/generate/cancel/{job_id}")
def generate_cancel(job_id: str):
    """Sets the job's `cancel_event` (the interface's own "Stop" button,
    see CANCEL_EVENTS) — a plain signal, never a forced stop: the job
    keeps running until its next cooperative checkpoint (see
    crossword_gen.GenerationCancelled), after which its status turns to
    "cancelled" (visible on the next poll of GET /api/generate/status/
    {job_id}, not immediately here). No effect if the job is already
    finished — `.set()` on an already-set event, or on a job that already
    finished some other way, does no harm."""
    job = JOBS.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="job inconnu (expiré ou jamais existé)")
    CANCEL_EVENTS[job_id].set()
    return {"status": "cancelling"}


@app.post("/api/generate/continue/{job_id}", status_code=202)
async def generate_continue(job_id: str):
    """"Continuer" button of the web UI, at the user's explicit request:
    shown when a job ends in `status: "error"` with `error_code:
    "no_fillable_grid"` (see _run_generate_job) — starts a new job, with
    the same parameters as the original (`job["request"]`), but resuming
    from the exact state the previous generation stopped at
    (`job["resume_state"]`, see crossword_gen.py's
    `_serialize_resume_state`) instead of starting over from a blank
    grid — a brand new full budget of `attempts` (200 by default)
    paliers, not a continuation of the same job. Returns a job_id
    distinct from the original job's own (the original job stays
    inspectable as-is), exactly like POST /api/generate: the client
    simply polls this new job_id the same way."""
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


@app.post("/api/recompute", status_code=202)
async def recompute(req: RecomputeRequest):
    """"Recalculer" button on a grid in play mode, at the user's explicit
    request (see RecomputeRequest / _run_recompute_job). Starts a new
    background job that re-runs only clue generation for the stored grid
    `req.grid_id` and saves it as a brand new library record whose title
    carries a bumped "(Vn)" marker — the original stays untouched.
    Returns a fresh job_id the client polls exactly like a generation job
    (GET /api/generate/status/{job_id}), then hands the result to
    displayFinalGrid()."""
    new_job_id = _new_job()
    task = asyncio.create_task(_run_recompute_job(new_job_id, req.grid_id))
    _BACKGROUND_TASKS.add(task)
    task.add_done_callback(_BACKGROUND_TASKS.discard)
    return {"job_id": new_job_id}
