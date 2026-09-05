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
import datetime
import json
import logging
import multiprocessing
import sys
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from .chatbot import ChatBot, ChatError
from .clues import ClueGenerationError, LLMClueGenerator
from .crossword_gen import (
    DEFAULT_HEIGHT, DEFAULT_WIDTH, DIFFICULTY_PRESETS, GenerationCancelled, GenerationPaused,
    generate_grid,
)
from .grid_store import get_grid, list_grids, save_grid_json
from .svg_export import save_grid_png, save_grid_svg
from .system_info import get_system_info

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("crosswordfalcon")

clue_generator = LLMClueGenerator()
chatbot = ChatBot()

# fetch_rss_feeds.py vit à la racine du projet (à côté de build_sentence_
# corpus.py et des autres scripts one-off), pas dans backend/ lui-même — un
# import relatif ordinaire ne peut pas l'atteindre. Le chemin racine est
# ajouté une seule fois à sys.path, au démarrage du module, plutôt que de
# dupliquer sa logique de récupération ici : à la demande explicite de
# l'utilisateur, "Configure un demon qui lit tous ces flux RSS une fois par
# jour... et sauvegarde chaque flux RSS dans un dossier RSS."
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))
import fetch_rss_feeds  # noqa: E402  (import après la manipulation de sys.path, volontaire)
import fetch_grid_links  # noqa: E402  (meme raison, meme dossier racine)

RSS_DIR = _PROJECT_ROOT / "RSS"
SCRAPP_DIR = _PROJECT_ROOT / "SCRAPP"
# Heure locale (24h) à laquelle le flux est rafraîchi chaque jour, à la
# demande explicite de l'utilisateur : "une fois par jour (par exemple, le
# matin à 8H)."
RSS_FETCH_HOUR = 8

# Journal des conversations du chatbot "David FALCON", à la demande
# explicite de l'utilisateur : "Pour chaque discussion dans le ChatBot,
# crée un LOG des questions/réponses dans un dossier LOG_CHAT. Chaque log
# est préfixé par un timestamp permettant de voir les fichiers dans
# l'ordre temporel. Un fichier par session utilisateur." Dossier à la
# racine du projet, gitignored — un journal généré, pas du contenu
# source, la même convention que LOG_LLM/ (backend/clues.py) pour les
# journaux d'appels LLM des définitions.
CHAT_LOG_DIR = _PROJECT_ROOT / "LOG_CHAT"
# session_id (fourni par le frontend, voir ChatRequest) -> chemin du
# fichier de log de cette session, déjà créé. Un dict en mémoire, comme
# JOBS/CANCEL_EVENTS ci-dessus — un seul processus uvicorn, pas de
# --workers (voir run_Falcon.sh), donc pas de verrou ni de store externe
# nécessaire. Le TIMESTAMP du nom de fichier est celui du tout PREMIER
# message de cette session (calculé une seule fois, ici, jamais
# recalculé) — c'est ce qui permet de voir les fichiers dans l'ordre
# temporel de démarrage de chaque session, chaque tour suivant de la même
# session étant simplement ajouté (append) au même fichier.
_CHAT_LOG_PATHS = {}


def _chat_log_path_for_session(session_id):
    """Renvoie le chemin du fichier de log pour cette session, le créant
    (et l'enregistrant dans `_CHAT_LOG_PATHS`) au tout premier appel pour
    ce `session_id`. `session_id` manquant/vide (un appel antérieur à
    cette fonctionnalité, ou un client qui n'en fournirait pas) reçoit un
    identifiant de repli généré ici (`uuid.uuid4()`) — jamais silencieusement
    ignoré, cette conversation est quand même journalisée, juste sans lien
    avec une session frontend précise."""
    if not session_id:
        session_id = f"sans-session-{uuid.uuid4().hex[:8]}"
    if session_id not in _CHAT_LOG_PATHS:
        CHAT_LOG_DIR.mkdir(parents=True, exist_ok=True)
        timestamp = time.strftime("%Y%m%d-%H%M%S")
        # session_id peut contenir des caractères non sûrs pour un nom de
        # fichier (le frontend peut envoyer n'importe quelle chaîne) — on
        # ne garde que les caractères alphanumériques/tiret/underscore.
        safe_id = "".join(c if c.isalnum() or c in "-_" else "_" for c in session_id)[:64]
        _CHAT_LOG_PATHS[session_id] = CHAT_LOG_DIR / f"{timestamp}_{safe_id}.md"
    return _CHAT_LOG_PATHS[session_id]


def _append_chat_log(session_id, language, message, reply, first_token_s=None, total_s=None):
    """Ajoute un tour de conversation (question + réponse complète) au
    fichier de log de cette session — best-effort, comme toute autre
    écriture de journal de ce projet (SVG/PNG, LOG_LLM/) : une erreur
    d'écriture est journalisée mais ne doit jamais faire échouer la
    réponse au joueur.

    `first_token_s`/`total_s` (both `None` by default, for a hypothetical
    future caller that doesn't measure timing) — at the user's explicit
    request: "Dans les LOG_CHAT, en dessous de chaque réponse, noter le
    temps de récupération du premier mot, et le temps total de génération
    de la réponse." Written as one small italic line right under the
    reply, before the closing `---` separator. `first_token_s` can be
    `None` even when `total_s` isn't (a call failed before ever streaming
    a single chunk — see `chat()`'s own `ChatError` branch) — in that
    case only the total-time bit is written, never a fabricated "0s"."""
    path = _chat_log_path_for_session(session_id)
    try:
        with path.open("a", encoding="utf-8") as f:
            f.write(f"## {time.strftime('%Y-%m-%d %H:%M:%S')} ({language})\n\n")
            f.write(f"**Utilisateur** : {message}\n\n")
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


async def _rss_daily_scheduler():
    """Tourne en tâche de fond pour toute la durée du processus : rafraîchit
    les flux RSS (fetch_rss_feeds.fetch_all) une fois par jour, à
    RSS_FETCH_HOUR (8h par défaut, heure locale) — à la demande explicite
    de l'utilisateur. Un simple `asyncio.sleep` jusqu'au prochain 8h plutôt
    qu'un vrai ordonnanceur système (cron/launchd) : ce projet n'a jamais
    eu d'infrastructure de service système, tout tourne déjà comme un
    processus Python lancé à la main (voir run_Falcon.sh) — ce mécanisme
    ne rafraîchit donc que tant que le back tourne, ce qui correspond déjà
    à la réalité opérationnelle de ce projet (aucune fonctionnalité
    n'attend de continuer à tourner serveur éteint).

    `fetch_all()` est bloquant (httpx synchrone) — exécuté via
    `asyncio.to_thread`, comme tout autre appel bloquant de ce fichier
    (génération de grille, appels LLM), pour ne jamais geler la boucle
    d'événements FastAPI pendant le téléchargement des flux.

    Ne lève jamais d'exception vers l'appelant : une erreur de
    téléchargement/écriture est déjà gérée à l'intérieur de `fetch_all()`
    elle-même (best-effort par flux) ; toute erreur inattendue ici est
    seulement journalisée, jamais laissée à interrompre la boucle — un
    échec de rafraîchissement un jour donné ne doit jamais empêcher les
    suivants.

    Rafraîchit aussi `fetch_grid_links.fetch_all()` (l'agrégation de
    grilles/SCRAPP, voir ce module), dans le même tick quotidien, à la
    demande explicite de l'utilisateur : "Reproduit l'agrégation que fait
    le site ci-dessus, pour récupérer les liens et descriptions une fois
    par jour (comme les flux RSS)." Un `try/except` propre à chacun des
    deux appels — l'échec de l'un ne doit jamais empêcher l'autre de
    tourner ce même jour, exactement le même principe déjà appliqué en
    interne à chaque flux RSS pris individuellement."""
    while True:
        now = datetime.datetime.now()
        next_run = now.replace(hour=RSS_FETCH_HOUR, minute=0, second=0, microsecond=0)
        if next_run <= now:
            next_run += datetime.timedelta(days=1)
        await asyncio.sleep((next_run - now).total_seconds())
        try:
            items = await asyncio.to_thread(fetch_rss_feeds.fetch_all)
            logger.info("rss: %d articles rafraichis", len(items))
        except Exception:
            logger.exception("rss: echec du rafraichissement quotidien")
        try:
            grids = await asyncio.to_thread(fetch_grid_links.fetch_all)
            logger.info("scrapp: %d grilles rafraichies", len(grids) if grids is not None else 0)
        except Exception:
            logger.exception("scrapp: echec du rafraichissement quotidien")


@app.on_event("startup")
async def _start_rss_scheduler():
    asyncio.create_task(_rss_daily_scheduler())

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
    width: int = Field(default=DEFAULT_WIDTH, ge=5, le=30, description="Largeur de la grille (horizontal)")
    height: int = Field(default=DEFAULT_HEIGHT, ge=5, le=30, description="Hauteur de la grille (vertical)")
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
    checks GRID_QUEUE/CLUES_QUEUE below rely on."""
    job_id: str
    req: GenerateRequest
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
    docstring for what happens once it returns true."""
    turn_start = time.monotonic()

    def should_pause():
        return time.monotonic() - turn_start >= MAX_TURN_DURATION_S and len(queue) > 1

    return should_pause


@app.get("/api/health")
def health():
    return {"status": "ok"}


@app.get("/api/rss")
def rss_feed():
    """Renvoie le contenu déjà agrégé/trié de RSS/combined.json (voir
    fetch_rss_feeds.py), à la demande explicite de l'utilisateur — le
    panneau "Actu Croisée" de la page d'accueil (frontend/static/script.js)
    l'appelle une fois au chargement de la page. Aucun parsing XML ici :
    fetch_rss_feeds.py a déjà fait tout le travail au moment du
    rafraîchissement quotidien (voir _rss_daily_scheduler ci-dessus) —
    cette route se contente de relire un fichier JSON déjà prêt. Si ce
    fichier n'existe pas encore (aucun rafraîchissement n'a encore eu lieu
    sur cette installation), renvoie une liste vide plutôt qu'une erreur —
    un panneau vide est un état parfaitement normal et attendu avant la
    toute première exécution."""
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
    """Miroir exact de `rss_feed()` ci-dessus, pour SCRAPP/combined.json
    (voir fetch_grid_links.py) au lieu de RSS/combined.json — à la
    demande explicite de l'utilisateur : "Ajoute les entrées de SCRAPP
    aux journal de la première page." Même route "lecture seule d'un JSON
    déjà prêt" (aucune re-requête vers grillesdujour.fr à chaque appel),
    même dégradation gracieuse (liste vide) si le fichier n'existe pas
    encore ou est illisible."""
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
    process."""
    return get_system_info(clue_generator.model)


@app.get("/api/library")
def library_list(preferred_language: str = "fr", page: int = 1):
    """Bouton "Bibliothèque" de l'interface, à la demande explicite de
    l'utilisateur — la liste (métadonnées seulement, jamais la grille
    entière : voir backend/grid_store.py's list_grids) de toutes les
    grilles déjà sauvegardées dans GRID_STORE/, triées langue configurée
    d'abord, puis anglais, puis le reste, plus récente en premier dans
    chaque groupe. `preferred_language` est la langue actuellement
    sélectionnée côté interface (le même sélecteur pilote à la fois la
    langue de la grille et celle de l'interface — voir CLAUDE.md),
    transmise explicitement par le frontend à chaque ouverture du
    panneau plutôt que déduite ici d'un cookie ou d'un en-tête.

    Paginée par `LIBRARY_PAGE_SIZE` (20) lignes, à la demande explicite de
    l'utilisateur — `list_grids()` elle-même reste inchangée (elle rend
    toujours la liste complète, déjà triée ; la pagination est une
    préoccupation de présentation de cette seule route, pas du tri
    lui-même, qui doit rester cohérent sur l'ensemble de la liste d'une
    page à l'autre). `page` est 1-indexée et bornée à 1 au minimum (une
    valeur absurde comme 0 ou négative ne renvoie jamais une tranche vide
    par accident) ; une page au-delà de la dernière renvoie simplement une
    liste vide plutôt qu'une erreur — `total`/`page_size` dans la réponse
    donnent au frontend tout ce qu'il faut pour calculer le nombre de
    pages et désactiver ses boutons "précédent"/"suivant" en conséquence."""
    grids = list_grids(preferred_language)
    page = max(1, page)
    start = (page - 1) * LIBRARY_PAGE_SIZE
    return {
        "grids": grids[start:start + LIBRARY_PAGE_SIZE],
        "total": len(grids),
        "page": page,
        "page_size": LIBRARY_PAGE_SIZE,
    }


@app.get("/api/library/{grid_id}")
def library_get(grid_id: str):
    """Charge une grille précédemment sauvegardée pour la rejouer —
    renvoie exactement la même forme qu'un job terminé (`result`, voir
    _run_generate_job), avec en plus les métadonnées de la bibliothèque
    (id/titre/langue/difficulté/mode/date), pour que le frontend puisse
    l'afficher via le même chemin de code qu'une génération qui vient de
    se terminer (voir frontend/static/script.js's displayFinalGrid)."""
    record = get_grid(grid_id)
    if record is None:
        raise HTTPException(status_code=404, detail="grille introuvable dans la bibliothèque")
    return record


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
    than silently dropping the exchange from the log.

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
        try:
            async for chunk in chatbot.reply_stream(
                [m.model_dump() for m in req.history], req.message, req.language, req.ui_context,
            ):
                if first_token_s is None:
                    first_token_s = time.monotonic() - start
                full_reply.append(chunk)
                yield f"data: {json.dumps({'delta': chunk})}\n\n"
            total_s = time.monotonic() - start
            yield "data: [DONE]\n\n"
            _append_chat_log(
                req.session_id, req.language, req.message, "".join(full_reply),
                first_token_s, total_s,
            )
        except ChatError as e:
            total_s = time.monotonic() - start
            yield f"data: {json.dumps({'error': str(e)})}\n\n"
            reply_so_far = "".join(full_reply)
            _append_chat_log(
                req.session_id, req.language, req.message,
                f"{reply_so_far}\n\n*(échec en cours de réponse : {e})*" if reply_so_far
                else f"*(échec : {e})*",
                first_token_s, total_s,
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


def _build_word_verification_table(words, language):
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
    wordlist_lines = _load_wordlist_raw_lines(language)
    gloss_lines = _load_gloss_raw_lines(language)

    rows = []
    for w in sorted(words, key=lambda w: (w["row"], w["col"], w["direction"])):
        answer = w["answer"]
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
    task = GenerationTask(job_id=job_id, req=req, resume_state=resume_state)

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
        if step == "budget_progress":
            # Enrichit le statut déjà affiché (ex. "Tentative N/200...")
            # d'un pourcentage de budget de vérifications consommé, à la
            # demande explicite de l'utilisateur : "sur la ligne de statut
            # de l'interface, ajouter le pourcentage du budget déjà
            # consommé par la phase de remplissage en cours." Ne remplace
            # jamais `job["step"]` en entier comme les autres étapes le
            # font juste en dessous — ce signal est republié toutes les
            # `BUDGET_PROGRESS_REPORT_INTERVAL_S` secondes pendant qu'une
            # recherche est en cours (voir crossword_gen.py), et
            # l'écraser remplacerait le statut réel (numéro de tentative,
            # etc.) par un pourcentage nu. Le prochain événement "normal"
            # (`pattern`/`pattern_attempt_failed`/...) remplace `job
            # ["step"]` en entier comme d'habitude, faisant naturellement
            # disparaître ce `budget_percent` devenu obsolète jusqu'à ce
            # qu'un nouveau rapport arrive pour la tentative suivante.
            job["step"] = {**job["step"], "budget_percent": data.get("percent")}
            return
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
        logger.info("[%s] %s %s", short_id, step, data)

    try:
        logger.info(
            "[%s] starting generation: language=%s width=%s height=%s difficulty=%s "
            "force_letters_percent=%s black_enrichment_percent=%s mode=%s",
            short_id, req.language, req.width, req.height, req.difficulty,
            req.force_letters_percent, req.black_enrichment_percent, req.mode,
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
        GRID_QUEUE.append(task)
        try:
            grid_resume_state = resume_state
            grid_paused_compute_s = 0.0
            while True:
                await _wait_in_queue(GRID_QUEUE, task, job, cancel_event, "queued_grid")
                # Durées affichées au-dessus de la grille finale (`#generation-times`
                # côté frontend), à la demande explicite de l'utilisateur — mesurées
                # ici plutôt que côté client, qui n'a aucun moyen fiable de savoir
                # quand chaque phase a réellement commencé/fini (seul ce process
                # voit directement les deux appels bloquants ci-dessous). `time.
                # monotonic()`, pas `time.time()` : une horloge murale peut reculer
                # (ajustement NTP, changement d'heure), ce qui fausserait une durée
                # calculée par simple soustraction — `monotonic()` ne recule jamais.
                grid_start = time.monotonic()
                try:
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
                        resume_state=grid_resume_state,
                        should_pause=_make_should_pause(GRID_QUEUE, task),
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
        # "minimizing"/"grid_ready" toujours présents ici (result n'est
        # jamais None sans être passé par toute la pipeline) — `.get(...,
        # grid_start)` reste une protection défensive, pas un cas normal :
        # sans elle, l'absence improbable de l'un des deux ferait échouer
        # tout le job juste pour ce calcul de durée, alors que la grille
        # elle-même est déjà prête.
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
            _build_word_verification_table, result["words"], req.language
        )
        progress(
            "clues", current=0, total=len(result["words"]),
            examples=[{
                "example_grid": result["solution"],
                "impossible_cells": [],
                "forced_cells": [],
                "locked_cells": [],
                # Numéro du process qui a réellement produit cette grille
                # gagnante (backend/crossword_gen.py's own `winning_
                # process_number`, threaded through the result dict — see
                # its own docstring for the full "numéro du process"
                # feature), à la demande explicite de l'utilisateur.
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
            remaining_entries = [(w["answer"], w["accented"], w["canonical"]) for w in result["words"]]
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
                        on_progress=lambda current, total: progress(
                            "clues", current=len(accumulated_clues) + current, total=len(result["words"]),
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
                    logger.info("[%s] clues turn paused, back of the queue", short_id)
                    CLUES_QUEUE.remove(task)
                    CLUES_QUEUE.append(task)
            result["clues_duration_seconds"] = clues_compute_s
            for w in result["words"]:
                w["clue"] = accumulated_clues.get(w["answer"], "")

            # A short, catchy title for the whole grid, at the user's explicit
            # request: "demande au LLM de générer un titre sympa pour la
            # grille en fonction des mots qu'elle contient" — generated once
            # every clue already exists (LLMClueGenerator.generate_title's own
            # docstring explains why this is a single best-effort call, unlike
            # generate()'s own per-word retry loop), shown above the finished
            # grid (frontend/static/script.js's displayFinalGrid) and saved
            # alongside it below. A failure here never raises (see generate_
            # title) — "" simply means no title line is shown/stored, exactly
            # like a grid generated before this feature existed.
            title = await asyncio.to_thread(
                clue_generator.generate_title,
                [(w["answer"], w["accented"], w["canonical"]) for w in result["words"]],
                req.language,
                cancel_event=cancel_event,
            )
            result["title"] = title
            logger.info("[%s] title: %r", short_id, title)
        finally:
            if task in CLUES_QUEUE:
                CLUES_QUEUE.remove(task)

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

        # Bibliothèque (see GET /api/library, GET /api/library/{grid_id}
        # below, and frontend/static/script.js's "Bibliothèque" button),
        # at the user's explicit request — same best-effort treatment as
        # the SVG/PNG saves just above: a failure to persist this grid
        # for later browsing is logged, never allowed to fail the request
        # the player is actually waiting on.
        try:
            grid_id = await asyncio.to_thread(
                save_grid_json, result, req.language, req.difficulty, req.mode, title
            )
            logger.info("[%s] saved to library: %s", short_id, grid_id)
        except OSError as e:
            logger.warning("[%s] failed to save grid to library: %s", short_id, e)

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
