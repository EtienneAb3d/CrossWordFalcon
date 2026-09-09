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
import os
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
from .clues import ClueGenerationError, LLMClueGenerator
from .dictionary_lookup import search as dictionary_search_impl
from .embedder import Embedder, EmbedderError
from .qdrant_store import QdrantStore, QdrantStoreError
from .crossword_gen import (
    DEFAULT_HEIGHT, DEFAULT_WIDTH, DIFFICULTY_PRESETS, GenerationCancelled, GenerationPaused,
    generate_grid,
)
from .grid_store import _slugify_title, get_grid, list_grids, save_grid_json
from .svg_export import (
    render_puzzle_svg,
    save_grid_png,
    save_grid_svg,
    svg_to_pdf_bytes,
)
from .system_info import get_system_info

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("crosswordfalcon")

clue_generator = LLMClueGenerator()
chatbot = ChatBot()

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
# drops below the threshold, fast around THEME_MIN_SCORE = 0.68), but the
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

# Les scripts de récupération vivent dans le paquet `scrapper/` à la racine
# du projet (déplacés là à la demande explicite de l'utilisateur, avec
# data_builder/ pour les scripts de construction de dictionnaires), pas
# dans backend/ lui-même. Le chemin racine est ajouté à sys.path pour que
# `from scrapper import ...` résolve quel que soit le répertoire de
# lancement, plutôt que de dupliquer ici leur logique de récupération : à
# la demande explicite de l'utilisateur, "Configure un demon qui lit tous
# ces flux RSS une fois par jour... et sauvegarde chaque flux RSS dans un
# dossier RSS."
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))
from scrapper import fetch_rss_feeds  # noqa: E402  (import après la manipulation de sys.path, volontaire)
from scrapper import fetch_grid_links  # noqa: E402  (meme raison)

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

# Journal de la pré-recherche thématique (voir THEME_LENGTH_MIN/MAX/
# THEME_MIN_SCORE et _run_generate_job) : un fichier
# `LOG_THEME/<timestamp>_<short_id>.log` par génération thématique, le
# nom préfixé par un timestamp complet (comme pour LOG_LLM/), à la
# demande explicite de l'utilisateur ("Montre la réponse LLM en première
# ligne du fichier de sortie" ; "Préfixer les sauvegarde dans LOG_THEME
# avec un timestamp, comme pour LOG_LLM"). Première ligne = la phrase
# ~50 mots produite par le LLM (ou le thème brut si l'appel LLM a
# échoué), puis le thème saisi et le glossaire complet des mots
# présélectionnés par Qdrant, un mot par ligne. Best-effort : un échec
# d'écriture est journalisé mais n'interrompt jamais la génération.
# Racine du projet, gitignored — un journal généré, pas du contenu source, la même
# convention que LOG_CHAT/ / LOG_USERS/ / LOG_LLM/.
THEME_LOG_DIR = _PROJECT_ROOT / "LOG_THEME"

# "chat debug" option (CHATBOT_DEBUG in env.sh/env_default.sh) — when on,
# _append_chat_log also writes the COMPLETE prompt actually sent to the
# LLM (the full messages array: system prompt + whole conversation
# history + the current question) into this session's LOG_CHAT/*.md
# file, inside a collapsible <details> block above each reply, for
# analysis. Off unless the value is one of 1/true/yes/on (case-
# insensitive) — so CHATBOT_DEBUG=0 stays off.
CHATBOT_DEBUG = os.environ.get("CHATBOT_DEBUG", "").strip().lower() in ("1", "true", "yes", "on")
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

# Champ "Thématique" optionnel du formulaire de génération, à la demande
# explicite de l'utilisateur : si la liste de mots de la thématique est
# non vide, une pré-recherche vectorielle Qdrant construit un glossaire de
# mots proches de la thématique, que le solveur CSP tente alors en
# priorité pour chaque emplacement (voir crossword_gen.py's
# `generate_grid`'s `priority_words` / `Filler._backtrack`). Best-effort :
# si Qdrant ou le serveur d'embeddings est indisponible, ou si la
# collection n'a pas encore été alimentée pour cette langue, la
# génération se poursuit simplement sans thématique.
#
# La description LLM du thème (describe_theme) est une liste télégraphique
# d'environ 30 mots-clefs séparés par des virgules. On ne l'embed PAS
# telle quelle : elle est découpée en mots-clefs individuels
# (_split_keywords), et CHAQUE mot-clef fait sa propre recherche Qdrant du
# plus-proche-voisin — _compiled_theme_words_by_length fusionne ensuite
# tous les résultats (meilleur score par mot). Une recherche par mot-clef
# unique donne un vecteur de requête bien plus net qu'un seul embedding
# moyenné sur ~30 mots. Chaque recherche est faite PAR LONGUEUR
# (THEME_LENGTH_MIN..THEME_LENGTH_MAX lettres) — plutôt qu'un simple top-N
# global (l'ancien THEME_PRESEARCH_LIMIT), qui pouvait laisser une
# longueur d'emplacement entière sans aucun mot thématique si les voisins
# les plus proches se trouvaient surtout à d'autres longueurs. Il n'y a
# aucun plafond par longueur (voir THEME_MIN_SCORE plus bas) : TOUS les
# mots dont le score dépasse ce seuil sont pris. Voir
# _theme_words_by_length.
THEME_LENGTH_MIN = 2
THEME_LENGTH_MAX = 15
# Taille de chaque page Qdrant lue en itérant (voir _theme_words_by_length) :
# assez grande pour amortir l'aller-retour réseau, assez petite pour
# s'arrêter tôt une fois le seuil de score franchi.
THEME_LENGTH_SEARCH_PAGE = 1000
# Il n'y a AUCUN plafond de profondeur / de nombre de mots renvoyés, à la
# demande explicite de l'utilisateur : "Ne pas limiter le nombre de mots
# renvoyés par le dictionnaire Thématique. Faire confiance au seuil."
# _theme_words_by_length pagine jusqu'à ce que le score passe sous
# THEME_MIN_SCORE (arrêt garanti et rapide vu que les résultats Qdrant
# sont classés par score décroissant) ou que le tenant soit épuisé.
# Seuil de score Qdrant COMMUN (similarité cosinus — les vecteurs sont
# normalisés, voir backend/embedder.py) : la variable unique qui gouverne
# À LA FOIS la construction du glossaire par longueur (_theme_words_by_
# length) ET chacune des recherches thématiques par mot-clef qui
# l'appellent (_compiled_theme_words_by_length). À la demande explicite de
# l'utilisateur : "lister tous les mots avec un seuil identique à la
# construction du glossaire (nommer la variable commune)". Les résultats
# Qdrant étant déjà classés par score décroissant, dès qu'un score sous ce
# seuil est rencontré, tous les suivants (dans la page courante ET dans
# toute page ultérieure) le sont aussi — _theme_words_by_length arrête
# donc complètement d'itérer, pas seulement d'accepter, dès ce point.
#
# C'est la VALEUR PAR DÉFAUT : le champ "Précision thématique" du
# formulaire de génération (à la suite de "Mode", à la demande explicite
# de l'utilisateur) permet de la régler à la main pour une génération
# donnée (GenerateRequest.theme_precision -> paramètre `min_score` de
# _compiled_theme_words_by_length / _theme_words_by_length). Le bouton
# "Thématique" du panneau Dictionnaire, lui, utilise toujours cette
# constante.
THEME_MIN_SCORE = 0.68

# Construction du glossaire de grille UNIQUEMENT (pas le bouton
# "Thématique" du panneau Dictionnaire), à la demande explicite de
# l'utilisateur : "augmenter la température du LLM à 0.9, itérer au plus
# 3 fois pour essayer d'obtenir 300 mots à chercher dans Qdrant." Après
# le premier passage (thème entier + un appel par mot pour un thème
# multi-mots), si l'ensemble dédoublonné des mots-clefs à chercher compte
# moins de THEME_MIN_KEYWORDS entrées, _run_generate_job relance
# describe_theme (avec THEME_KEYWORD_LLM_TEMPERATURE = 0.9 pour maximiser
# la variété) jusqu'à THEME_KEYWORD_LLM_MAX_LOOPS fois de plus, en
# s'arrêtant dès qu'un appel n'apporte aucun mot-clef nouveau. La
# température 0.9 est aussi utilisée pour le premier passage. Objectif
# indicatif, pas une garantie.
THEME_MIN_KEYWORDS = 300
THEME_KEYWORD_LLM_MAX_LOOPS = 3
THEME_KEYWORD_LLM_TEMPERATURE = 0.9

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
    # Balayage périodique de la présence : capte une baisse d'effectif
    # (LOG_USERS/) même quand plus aucun battement n'arrive — voir
    # _presence_sweep_scheduler.
    asyncio.create_task(_presence_sweep_scheduler())

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

# Journal du nombre de visiteurs en ligne, à la demande explicite de
# l'utilisateur : "le Back doit générer un fichier par jour dans le dossier
# LOG_USERS, avec consigné dans ce fichier une nouvelle ligne avec la date
# et l'heure d'un changement dans le nombre des visiteurs, le nombre de
# visiteurs, puis la liste des pseudos actifs après changement de ce
# nombre." Un fichier `LOG_USERS/<AAAA-MM-JJ>.log` par jour (racine du
# projet, gitignoré — artefact généré, même convention que LOG_LLM/,
# LOG_CHAT/), une ligne ajoutée uniquement quand le *nombre* d'utilisateurs
# distincts change (jamais quand seule la composition des pseudos change à
# effectif constant — c'est bien "un changement dans le nombre" qui est
# demandé). Écriture best-effort : un échec est seulement journalisé,
# jamais laissé casser un battement de présence.
USERS_LOG_DIR = _PROJECT_ROOT / "LOG_USERS"

# Un `threading.Lock` protège maintenant `_PRESENCE` et
# `_last_logged_presence_count` : `POST /api/presence` (fonction `def`
# synchrone, exécutée dans le pool de threads de Starlette) et le balayage
# périodique `_presence_sweep_scheduler` (coroutine sur la boucle
# d'événements) peuvent tous deux recalculer l'effectif — sans verrou, la
# purge des sessions expirées d'un côté pourrait lever pendant une écriture
# de l'autre, et deux appelants pourraient voir simultanément « l'effectif
# a changé » et écrire une ligne en double. Le verrou ne couvre que le
# recalcul en mémoire ; l'écriture disque se fait toujours en dehors.
_PRESENCE_LOCK = threading.Lock()

# Dernier effectif distinct consigné dans LOG_USERS (None au démarrage, si
# bien que le tout premier battement après lancement/redémarrage du serveur
# consigne une ligne — ce qui marque aussi un redémarrage dans la
# chronologie).
_last_logged_presence_count = None

# Le balayage périodique existe pour capter une *baisse* d'effectif quand
# les battements s'arrêtent (tout le monde a quitté) : sans lui, la purge
# ne tourne que dans `POST /api/presence`, donc le passage à 0 ne serait
# jamais consigné avant qu'un nouveau visiteur ne se connecte. 10s est
# assez fin devant PRESENCE_TTL_S (60s) et coûte quasiment rien (itération
# d'un dict d'au plus MAX_PRESENCE_ENTRIES entrées).
PRESENCE_SWEEP_INTERVAL_S = 10

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
    # `None` par défaut (une grille ordinaire, monolingue), à la demande
    # explicite de l'utilisateur : "Ajouter la possibilité de générer des
    # grille bilingue... toutes les étapes utilisent la première langue
    # pour les mots horizontaux, et la seconde langue pour les mots
    # verticaux." L'interface web force ce champ à la même valeur que
    # `language` dès que celui-ci change (voir frontend/static/script.js),
    # mais le joueur peut ensuite le régler sur une langue différente pour
    # obtenir une vraie grille bilingue — `None`, ou une valeur identique
    # à `language`, dégradent tous deux proprement en génération
    # monolingue ordinaire (voir crossword_gen.generate_grid's propre
    # `bilingual_wordlist_path`), donc Automation/Populate.py (qui
    # n'envoie jamais ce champ) continue de générer des grilles purement
    # monolingues sans aucun changement de son côté.
    bilingual_language: Optional[str] = Field(
        default=None,
        description=(
            "Langue des mots verticaux pour une grille bilingue (fr, en, de, es, "
            "it ou pt) ; None ou identique à `language` = grille monolingue ordinaire"
        ),
    )
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
    # Pseudo (nickname) de l'utilisateur qui génère la grille, à la
    # demande explicite de l'utilisateur : "Quand une grille est
    # sauvegardée, si un pseudo est défini, sauvegarder le pseudo dans le
    # JSON de la grille." `None`/vide = pas d'auteur enregistré (le
    # comportement d'avant cette fonctionnalité). Non borné ici par une
    # contrainte pydantic qui renverrait un 422 : `_run_generate_job` le
    # nettoie et le tronque à MAX_PSEUDO_LENGTH, pour qu'un caractère en
    # trop ne bloque jamais une génération.
    pseudo: Optional[str] = None
    # Thématique optionnelle : une liste de mots (texte libre) donnant une
    # orientation sémantique à la grille, à la demande explicite de
    # l'utilisateur. Non vide -> `_run_generate_job` fait une pré-recherche
    # Qdrant par mot-clef, par longueur (voir THEME_LENGTH_MIN/MAX/
    # THEME_MIN_SCORE), et passe le glossaire obtenu à
    # generate_grid(priority_words=...). `None`/vide = grille ordinaire,
    # aucune pré-recherche. Non borné par une contrainte pydantic (comme
    # `pseudo`) : nettoyé dans _run_generate_job.
    theme: Optional[str] = None
    # Champ "Précision thématique" du formulaire (à la suite de "Mode"), à
    # la demande explicite de l'utilisateur : "permettant de configurer à
    # la main THEME_MIN_SCORE". Seuil de similarité Qdrant minimal pour
    # qu'un mot entre dans le glossaire thématique de CETTE génération —
    # passé comme `min_score` à _compiled_theme_words_by_length /
    # _theme_words_by_length. Par défaut la constante module
    # THEME_MIN_SCORE (0.68). Sans effet si `theme` est vide.
    theme_precision: float = Field(
        default=THEME_MIN_SCORE, ge=0.0, le=1.0,
        description="Seuil de similarité Qdrant minimal du glossaire thématique (0.0 à 1.0)",
    )


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


class PresenceRequest(BaseModel):
    """Corps de POST /api/presence — battement de cœur d'un onglet de
    l'interface web (toutes les 2s). `session_id` : identifiant opaque
    stable pour ce chargement de page ; `pseudo` : pseudo courant de
    l'utilisateur (vide tant que le panneau d'accueil n'est pas validé).
    Bornés défensivement — ce sont des chaînes fournies par le client,
    utilisées uniquement comme clés en mémoire, jamais sur disque."""
    session_id: str = Field(..., min_length=1, max_length=200)
    pseudo: Optional[str] = None


def _presence_snapshot(record=None):
    """Sous `_PRESENCE_LOCK` : enregistre éventuellement un battement
    (`record` = `(session_id, entry)`), purge les sessions expirées,
    applique le plafond anti-abus, puis renvoie
    `(count, pseudos, changed)` :

    - `count`  : nombre d'utilisateurs actifs distincts — dédoublonné par
      pseudo (deux onglets d'une même personne = un utilisateur), une
      session encore sans pseudo comptant pour elle-même ;
    - `pseudos`: la liste à consigner — les pseudos distincts triés
      (insensible à la casse), suivis d'un `(anonyme)` par session encore
      sans pseudo, de sorte que `len(pseudos) == count` ;
    - `changed`: `True` si et seulement si `count` diffère du dernier
      effectif consigné dans LOG_USERS — et, dans ce cas, met à jour ce
      marqueur ici même (sous le verrou), de sorte qu'un seul appelant
      voit jamais une transition donnée et écrit une seule ligne.

    Le verrou ne couvre que ce recalcul en mémoire ; l'appelant fait
    l'écriture disque (`_write_users_log`) en dehors."""
    global _last_logged_presence_count
    with _PRESENCE_LOCK:
        now = time.monotonic()
        if record is not None:
            sid, entry = record
            _PRESENCE[sid] = entry
        # Purge des sessions expirées — garde le dict borné à « ce qui a
        # émis un battement dans les PRESENCE_TTL_S (60s) dernières
        # secondes ».
        for sid in [s for s, e in _PRESENCE.items()
                    if now - e["last_seen"] > PRESENCE_TTL_S]:
            del _PRESENCE[sid]
        # Filet de sécurité contre un client abusif : si malgré la purge
        # le dict dépasse le plafond, on retire les plus anciens.
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
        changed = count != _last_logged_presence_count
        if changed:
            _last_logged_presence_count = count
        return count, pseudos, changed


def _write_users_log(count, pseudos):
    """Ajoute une ligne à `LOG_USERS/<AAAA-MM-JJ>.log` :
    `AAAA-MM-JJ HH:MM:SS | <count> | <pseudo1, pseudo2, ...>`. Best-effort
    — un échec est seulement journalisé, jamais laissé remonter (même
    convention que toute autre écriture de journal de ce projet)."""
    try:
        USERS_LOG_DIR.mkdir(parents=True, exist_ok=True)
        now = datetime.datetime.now()
        path = USERS_LOG_DIR / f"{now:%Y-%m-%d}.log"
        line = (f"{now:%Y-%m-%d %H:%M:%S} | {count} | "
                f"{', '.join(pseudos) if pseudos else '—'}\n")
        with path.open("a", encoding="utf-8") as fh:
            fh.write(line)
    except OSError as exc:
        logger.warning("echec d'ecriture du journal LOG_USERS: %s", exc)


def _write_theme_log(short_id, theme, description, words,
                     keyword_lists=None, searched_keywords=None, min_score=None):
    """Écrit `LOG_THEME/<timestamp>_<short_id>.log`, le nom préfixé par un
    timestamp complet (`%Y%m%d-%H%M%S-%f`) comme pour `LOG_LLM/`
    (`backend/clues.py`, `_write_call_log`) — à la demande explicite de
    l'utilisateur ("Préfixer les sauvegarde dans LOG_THEME avec un
    timestamp, comme pour LOG_LLM") — plutôt que la simple date utilisée
    jusque-là, pour que les fichiers se trient chronologiquement à la
    seconde/microseconde près, cohérent avec les autres journaux de ce
    projet. À la demande explicite de l'utilisateur, la PREMIÈRE ligne du
    fichier est la phrase produite par le LLM pour décrire la thématique
    (`description`) — ou le thème brut si l'appel LLM a échoué ; suivent
    le thème saisi, le nombre de mots présélectionnés et un échantillon.
    Best-effort — un échec est seulement journalisé. À la demande
    explicite de l'utilisateur ("Lister le glossaire produit dans le
    LOG_THEME (un mot par ligne)"), le glossaire complet des mots
    présélectionnés par Qdrant est listé intégralement, un mot par ligne,
    à la suite de l'en-tête.

    `words` : liste de couples `(mot, score)`, déjà triée par longueur de
    mot croissante par l'appelant (`_theme_words_by_length`) — à la demande
    explicite de l'utilisateur ("continuer à les lister par taille de mots
    croissante"). Le score de similarité Qdrant (cosinus, plus haut = plus
    proche de la thématique) est affiché à côté de chaque mot, à la demande
    explicite de l'utilisateur ("afficher les scores de chaque mot produit
    par Qdrant"), et — à la demande explicite de l'utilisateur ("en
    indiquant le nombre de lettres en plus du score") — le nombre de
    lettres du mot est affiché entre les deux, chaque champ séparé par une
    tabulation pour rester facile à parser/aligner.

    `keyword_lists` : les listes de mots-clefs produites par le LLM, sous
    la forme `[(label_ou_None, [mot_clef, ...]), ...]` — `None` pour la
    liste du thème entier, le mot du thème pour chacune des listes par mot
    (voir le bloc thématique de `_run_generate_job`). `searched_keywords` :
    l'ensemble à plat, dédoublonné, des mots-clefs qui ont réellement fait
    une recherche Qdrant (voir `_split_keywords` /
    `_compiled_theme_words_by_length`). `min_score` : le seuil de score
    Qdrant utilisé pour cette génération (champ "Précision thématique",
    GenerateRequest.theme_precision). Tous journalisés dans l'en-tête pour
    garder la trace de ce qui a été compilé."""
    try:
        THEME_LOG_DIR.mkdir(parents=True, exist_ok=True)
        now = datetime.datetime.now()
        timestamp = now.strftime("%Y%m%d-%H%M%S-%f")
        path = THEME_LOG_DIR / f"{timestamp}_{short_id}.log"
        with path.open("w", encoding="utf-8") as fh:
            fh.write((description or theme).strip() + "\n")
            fh.write(f"\n# generated {now:%Y-%m-%d %H:%M:%S}\n")
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
        logger.warning("echec d'ecriture du journal LOG_THEME: %s", exc)


@app.post("/api/presence")
def presence(req: PresenceRequest):
    """Enregistre/rafraîchit ce battement de cœur et renvoie
    `{"count": N}` où N est le nombre d'utilisateurs actifs distincts (voir
    `_presence_snapshot`). Consigne une ligne dans LOG_USERS/ si ce nombre
    a changé. À la demande explicite de l'utilisateur."""
    record = (req.session_id, {
        "last_seen": time.monotonic(),
        "pseudo": (req.pseudo or "").strip()[:MAX_PSEUDO_LENGTH],
    })
    count, pseudos, changed = _presence_snapshot(record)
    if changed:
        _write_users_log(count, pseudos)
    return {"count": count}


async def _presence_sweep_scheduler():
    """Tourne en tâche de fond pour toute la durée du processus : toutes
    les PRESENCE_SWEEP_INTERVAL_S (10s), recalcule l'effectif de présence
    et consigne une ligne dans LOG_USERS/ s'il a baissé parce que des
    battements se sont arrêtés (tout le monde a quitté). Sans ce balayage,
    la purge ne tourne que dans `POST /api/presence`, donc le passage à 0
    ne serait jamais consigné avant qu'un nouveau visiteur ne se connecte.
    Ne lève jamais vers l'appelant — toute erreur est seulement
    journalisée, jamais laissée interrompre la boucle."""
    while True:
        await asyncio.sleep(PRESENCE_SWEEP_INTERVAL_S)
        try:
            count, pseudos, changed = _presence_snapshot()
            if changed:
                await asyncio.to_thread(_write_users_log, count, pseudos)
        except Exception:
            logger.exception("presence: echec du balayage periodique")


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


_LIBRARY_SEEN_FILTERS = ("all", "unseen", "seen", "mine")
_LIBRARY_DIFFICULTY_FILTERS = ("easy", "medium", "hard")


class LibraryListRequest(BaseModel):
    """Corps de POST /api/library — même rôle que les paramètres de query
    de la route GET, mais en POST pour pouvoir transporter `seen_ids`, qui
    peut compter des milliers d'identifiants (bien au-delà de ce qu'une
    query string, ou un cookie, encaisse raisonnablement — voir
    frontend/static/script.js, qui garde l'ensemble en localStorage)."""
    preferred_language: str = "fr"
    page: int = 1
    # Filtre de langue, à la demande explicite de l'utilisateur ("Par
    # défaut, n'afficher que les grilles dans la langue de l'interface") :
    # "all" -> toutes langues ; un code (fr/en/de/es/it) -> seulement
    # cette langue. `preferred_language` continue de piloter l'ordre de
    # tri (cette langue d'abord), indépendamment de ce filtre. Le
    # frontend l'initialise à la langue de l'interface (voir
    # #library-language-filter).
    language_filter: str = "all"
    # Filtre de niveau, à la demande explicite de l'utilisateur : "all"
    # (défaut, "Tous les niveaux") -> tous ; "easy"/"medium"/"hard" ->
    # seulement les grilles de ce niveau. Le frontend l'initialise à
    # "all" et ne le fait pas suivre la langue de l'interface.
    difficulty_filter: str = "all"
    # "all" (défaut) : liste complète, chaque grille juste annotée seen=…
    # "unseen" : seulement les grilles absentes de seen_ids
    # "seen"   : seulement celles présentes dans seen_ids
    # "mine"   : seulement les grilles dont le champ `pseudo` correspond à
    #            `pseudo` ci-dessous (rien si `pseudo` est vide), à la
    #            demande explicite de l'utilisateur ("ajouter une entrée
    #            'Mes grilles'").
    seen_filter: str = "all"
    # Identifiants (champ `id` d'un fichier GRID_STORE) des grilles que ce
    # client a déjà vues. Borné défensivement — un client normal en a au
    # plus quelques milliers ; au-delà c'est du bruit qu'on ignore.
    seen_ids: list[str] = Field(default_factory=list, max_length=100_000)
    # Pseudo de l'utilisateur courant — utilisé seulement quand
    # `seen_filter == "mine"`. `None`/vide : le filtre "Mes grilles" ne
    # renvoie rien.
    pseudo: Optional[str] = None


def _library_page(preferred_language, page, seen_filter, seen_ids,
                  language_filter="all", difficulty_filter="all", pseudo=None):
    """Coeur partagé de GET et POST /api/library — la liste (métadonnées
    seulement, jamais la grille entière : voir backend/grid_store.py's
    list_grids) des grilles de GRID_STORE/, triées langue configurée
    d'abord puis anglais puis le reste, plus récente en premier dans
    chaque groupe, filtrée par `language_filter`, `difficulty_filter` puis
    par `seen_filter`/`seen_ids` (ou `seen_filter=="mine"` : seulement les
    grilles dont `pseudo` correspond au `pseudo` passé), puis paginée par
    `LIBRARY_PAGE_SIZE` (20).

    `list_grids()` elle-même reste inchangée (toujours la liste complète
    triée, toutes langues) ; le filtrage par langue ("all" ou un code) et
    "déjà vue / pas encore vue" et la pagination sont des préoccupations
    de cette route. `preferred_language` pilote seulement l'ordre de tri,
    pas le filtrage. Exception : avec `language_filter=="all"`, le
    regroupement par langue de list_grids() est annulé et la liste
    repasse en ordre purement chronologique inverse. Les filtrages se font AVANT la pagination pour que
    `total`/le nombre de pages reflètent la liste réellement montrée.
    Chaque grille renvoyée porte en plus `seen` (bool) pour que le
    frontend puisse la griser sans re-consulter son propre stockage.
    `page` bornée à 1 au minimum ; une page au-delà de la dernière renvoie
    une liste vide, pas une erreur."""
    if seen_filter not in _LIBRARY_SEEN_FILTERS:
        seen_filter = "all"
    # "bilingual" (voir GRID_STORE/bilingual/, backend/grid_store.py's
    # save_grid_json) n'est jamais une vraie clé de WORDLISTS — filtré
    # séparément, sur le champ `bilingual` de chaque grille plutôt que sur
    # son `language` (qui reste toujours sa langue primaire), à la
    # demande explicite de l'utilisateur : "ajouter Bilingue dans le
    # sélecteur de langue de la Bibliothèque."
    only_bilingual = language_filter == "bilingual"
    only_language = language_filter if language_filter in WORDLISTS else None
    # Filtre de niveau (easy/medium/hard) — "all"/toute valeur inconnue
    # laisse tout passer, à la demande explicite de l'utilisateur.
    only_difficulty = (
        difficulty_filter if difficulty_filter in _LIBRARY_DIFFICULTY_FILTERS else None
    )
    seen = set(seen_ids or ())
    # "Mes grilles" : filtre sur le champ `pseudo` de chaque grille, à la
    # demande explicite de l'utilisateur. Un `pseudo` vide ne matche
    # rien (le sélecteur "Mes grilles" est alors sans objet).
    my_pseudo = (pseudo or "").strip()
    rows = []
    for g in list_grids(preferred_language):
        if only_difficulty is not None and g.get("difficulty") != only_difficulty:
            continue
        if only_bilingual:
            if not g.get("bilingual"):
                continue
        elif only_language is not None:
            # Une langue précise (jamais "all"/"bilingual" — only_language
            # n'est posé que pour une vraie clé de WORDLISTS) exclut aussi
            # les grilles bilingues dont `language` correspond, à la
            # demande explicite de l'utilisateur : "quand une seule langue
            # est sélectionnée, ne pas afficher les grilles bilingues" —
            # une grille bilingue ne se montre alors que via le filtre
            # "Bilingue" lui-même, jamais mélangée dans la liste d'une
            # seule langue même si celle-ci est sa langue primaire.
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
    # "Toutes les langues" (ni "bilingual", ni une vraie clé de WORDLISTS) :
    # ordre purement chronologique inverse, sans le regroupement par langue
    # que list_grids() applique pour la vue par défaut — à la demande
    # explicite de l'utilisateur. Un filtre sur une langue précise rend ce
    # regroupement inopérant de toute façon (toutes les lignes partagent la
    # même langue), donc on ne re-trie que dans le cas "all".
    if not only_bilingual and only_language is None:
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
    """Bouton "Bibliothèque" de l'interface — voir _library_page. Cette
    variante GET (sans filtre langue ni notion de grilles vues) est
    conservée pour un accès simple ; le frontend utilise POST /api/library
    pour transmettre le filtre de langue et la liste des grilles déjà vues
    (voir LibraryListRequest)."""
    return _library_page(preferred_language, page, "all", (), "all")


@app.post("/api/library")
def library_list_filtered(req: LibraryListRequest):
    """Comme GET /api/library, mais le corps porte `language_filter`,
    `seen_filter` + `seen_ids` (voir LibraryListRequest) : le back filtre
    la liste par langue et par "déjà vue", l'annote, puis la pagine, à la
    demande explicite de l'utilisateur ("Passer les grilles déjà vues au
    Back pour qu'il sache comment gérer la liste à transmettre au
    Front")."""
    return _library_page(
        req.preferred_language, req.page, req.seen_filter, req.seen_ids,
        req.language_filter, req.difficulty_filter, req.pseudo,
    )


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


@app.get("/api/library/{grid_id}/pdf")
async def library_get_pdf(grid_id: str):
    """Télécharge une grille de la bibliothèque en PDF imprimable — grille
    VIDE, définitions et titre uniquement, jamais les réponses — à la
    demande explicite de l'utilisateur. Rendu SVG (render_puzzle_svg) puis
    converti en PDF via `rsvg-convert -f pdf` (svg_to_pdf_bytes)."""
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
        # `rsvg-convert` manquant ou en échec — même famille de dépendance
        # que la génération PNG (voir svg_export.save_grid_png).
        raise HTTPException(status_code=503, detail=str(exc))
    slug = _slugify_title(title) if title else "grille"
    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{slug}.pdf"'},
    )


@app.get("/api/dictionary")
async def dictionary_search(q: str, lang: str = "fr"):
    """Recherche de dictionnaire pour le panneau "Dictionnaire" de
    l'interface, à la demande explicite de l'utilisateur : à partir d'un
    mot (accents et casse ignorés), liste tous les mots de la même racine
    tirés de data/wordlist_<lang>_full.tsv, chacun avec ses définitions
    réelles de data/gloss_dictionary/<lang>_glosses.jsonl. Voir
    backend/dictionary_lookup.search — l'index par langue est construit une
    seule fois puis mis en cache (le wordlist fr fait ~200k lignes), d'où
    l'exécution via asyncio.to_thread."""
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
async def dictionary_define(q: str, lang: str = "fr"):
    """"Définir" bouton du panneau Dictionnaire (voir frontend/static/
    script.js) : demande au LLM jusqu'à DEFINE_COUNT (10) définitions
    indépendantes de l'expression saisie, comme pour un mot de grille
    (backend/clues.py, LLMClueGenerator.generate_definitions — même
    ancrage réel dictionnaire/exemples, même filtre de contenu — mais un
    seul appel best-effort, sans la boucle de relance par mot d'une
    génération de grille). Un ClueGenerationError (LLM injoignable)
    devient un 503 propre ; le reste de l'UI n'est pas affecté."""
    if lang not in WORDLISTS:
        raise HTTPException(status_code=400, detail=f"langue inconnue : {lang!r}")
    text = q.strip()
    if not text:
        raise HTTPException(status_code=400, detail="expression vide")
    try:
        definitions = await asyncio.to_thread(
            clue_generator.generate_definitions, text, lang, DEFINE_DIFFICULTY, DEFINE_COUNT,
        )
    except ClueGenerationError as exc:
        logger.warning("dictionary_define unavailable: %s", exc)
        raise HTTPException(
            status_code=503,
            detail={"code": "define_unavailable", "message": str(exc)},
        )
    return {"query": text, "lang": lang, "definitions": definitions}


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
    """Comme `_compiled_theme_words_by_length` mais pour le panneau
    Dictionnaire : lance une recherche Qdrant du plus-proche-voisin pour
    CHAQUE mot-clef de `keywords` (embedding + `_iter_scored_words`) et
    fusionne — chaque mot garde son MEILLEUR score. Deux différences avec
    la version "glossaire de grille" : (1) aucun filtre de longueur 2-15
    (une recherche de dictionnaire ne doit pas écarter les mots longs) ;
    (2) tri par score DÉCROISSANT (l'ordre "plus similaires d'abord" du
    panneau), pas par longueur. Un mot-clef dont la recherche Qdrant
    échoue est ignoré ; l'erreur ne se propage que tant qu'aucune
    recherche n'a abouti (Qdrant/embedder réellement indisponible ->
    503)."""
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


def _similar_words_impl(query: str, lang: str,
                        min_score: float = THEME_MIN_SCORE) -> list[tuple[str, float]]:
    """Blocking. Applies the themed-grid-glossary principle to the
    Dictionary panel, at the user's explicit request ("appliquer le même
    principe que pour la génération du glossaire thématique : demander au
    LLM de générer des listes de mots dans le thème du mot cherché, avant
    de compiler les recherches Qdrant"): the typed `query` is first
    expanded by the LLM (`describe_theme`) into a ~30-word telegraphic
    keyword list spanning every part of speech, split into individual
    keywords (`_split_keywords`, case-insensitively de-duplicated), and
    `_compiled_similar_words` runs one Qdrant nearest-words search per
    keyword and merges (best score per word). Falls back to the raw
    `query` as the sole keyword if the LLM call fails or returns nothing
    (same fallback shape as `_run_generate_job`'s own theme block).

    Every kept `(word, score)` has a similarity >= `min_score` (the
    "Précision thématique" form field's current value, forwarded as the
    `min_score` query param; default THEME_MIN_SCORE). No count limit and
    no length filter (unlike the grid glossary's 2-15 bound). Returned
    most-similar-first; the score is shown to 2 decimals next to each word
    in the panel."""
    desc = ""
    try:
        desc = clue_generator.describe_theme(
            query, lang, timeout=_SIMILAR_DESCRIBE_TIMEOUT_S,
        )
    except Exception as exc:  # noqa: BLE001 — best-effort, on retombe sur le mot brut
        logger.warning(
            "similar_words: theme expansion failed (%s) — searching the raw query", exc,
        )
        desc = ""
    keywords: list[str] = []
    seen: set[str] = set()
    for kw in _split_keywords(desc):
        k = kw.lower()
        if k not in seen:
            seen.add(k)
            keywords.append(kw)
    if not keywords:
        keywords = [query]
    logger.info(
        "similar_words: %r -> %d keywords (min_score=%s)", query, len(keywords), min_score,
    )
    return _compiled_similar_words(keywords, lang, min_score)


def _theme_words_by_length(query: str, lang: str,
                           min_score: float = THEME_MIN_SCORE) -> list[tuple[str, float]]:
    """Blocking: builds the themed-generation glossary (see
    THEME_LENGTH_MIN/MAX/THEME_MIN_SCORE above) by embedding `query` once,
    then walking the `lang` tenant's own ranked nearest-neighbor list via
    `_iter_scored_words` — the shared threshold/pagination helper, which
    stops the moment a hit's score drops below `min_score` (the
    per-generation GenerateRequest.theme_precision, defaulting to the
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


def _split_keywords(text: str) -> list[str]:
    """Découpe une réponse de `describe_theme` (une liste télégraphique
    d'environ 30 mots-clefs séparés par des virgules) en mots-clefs
    individuels — chacun fera ensuite sa propre recherche Qdrant du
    plus-proche-voisin (voir le bloc thématique de `_run_generate_job` et
    `_compiled_theme_words_by_length`). Découpe sur les virgules, points-
    virgules et retours à la ligne ; nettoie chaque morceau ; écarte tout
    ce qui fait moins de 2 caractères. L'ordre est conservé, aucun
    dédoublonnage ici (l'appelant met à plat et dédoublonne sur toutes les
    listes)."""
    out: list[str] = []
    for piece in re.split(r"[,;\n]+", text or ""):
        kw = piece.strip().strip(".").strip()
        if len(kw) >= 2:
            out.append(kw)
    return out


# Un "mot" significatif du champ "Thématique" : une suite de lettres (avec
# apostrophe/tiret internes tolérés — "l'agriculture", "mots-croisés"
# comptent chacun pour un seul mot), d'au moins 2 lettres.
_THEME_TOKEN_RE = re.compile(r"[^\W\d_]+(?:['’\-][^\W\d_]+)*", re.UNICODE)


def _theme_tokens(theme: str) -> list[str]:
    """Les mots distincts de la définition thématique saisie par
    l'utilisateur — dédoublonnés sans tenir compte de la casse (première
    graphie conservée), au moins 2 lettres chacun. À la demande explicite
    de l'utilisateur : "Lorsqu'il y a plusieurs mots dans la définition
    thématique donnée par l'utilisateur, compiler les glossaires
    thématiques pour chacun des mots" — voir `_compiled_theme_words_by_
    length` et le bloc thématique de `_run_generate_job`."""
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
    """Exécute `_theme_words_by_length` pour CHAQUE mot-clef de `keywords`
    et fusionne les glossaires obtenus — chaque mot garde le MEILLEUR
    score (le plus élevé) vu sur l'ensemble des recherches. À la demande
    explicite de l'utilisateur : "Compiler toutes les recherches dans
    Qdrant pour tous les mots de ces listes (dédoublonner les mots)."
    `keywords` est la liste à plat, déjà dédoublonnée, des mots-clefs
    extraits des listes produites par le LLM (voir `_split_keywords` et le
    bloc thématique de `_run_generate_job`) : un mot-clef unique est une
    requête bien plus nette qu'un seul embedding moyenné sur une phrase de
    ~30 mots. Chaque recherche applique le seuil `min_score` (le champ
    "Précision thématique" du formulaire — GenerateRequest.theme_precision
    —, par défaut la constante THEME_MIN_SCORE) via `_theme_words_by_
    length`.

    Le résultat est re-trié par longueur de mot croissante puis score
    décroissant — exactement l'ordre que renvoie déjà un appel unique à
    `_theme_words_by_length` (voir `_write_theme_log`). Un mot-clef dont la
    recherche Qdrant échoue est ignoré ; l'erreur ne se propage que tant
    qu'aucune recherche n'a encore abouti (Qdrant/embedder réellement
    indisponible → la génération se fait alors sans thématique)."""
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
    """Bouton "Thématique" du panneau Dictionnaire (voir frontend/static/
    script.js) : TOUS les mots de la collection Qdrant "words" dont le
    score de similarité avec l'expression saisie atteint `min_score` —
    la valeur courante du champ "Précision thématique" du formulaire,
    transmise par le front, à la demande explicite de l'utilisateur
    ("Dictionnaire / Thématique ... doit être sensible à la modification
    du paramètre Précision thématique") ; par défaut la constante
    THEME_MIN_SCORE quand le champ est vide. Restreints au tenant de la
    langue du panneau, les plus similaires d'abord. Aucun plafond de
    nombre : le seuil de score est la seule limite. L'embedding et la
    recherche vectorielle sont bloquants, d'où asyncio.to_thread. Renvoie
    un 503 propre (code "similar_unavailable") si Qdrant ou le serveur
    d'embeddings n'est pas lancé, ou si la collection n'a pas encore été
    alimentée (python -m data_builder.qdrant_populate --all)."""
    if lang not in WORDLISTS:
        raise HTTPException(status_code=400, detail=f"langue inconnue : {lang!r}")
    query = q.strip()
    if not query:
        raise HTTPException(status_code=400, detail="expression vide")
    min_score = max(0.0, min(1.0, min_score))
    try:
        scored = await asyncio.to_thread(_similar_words_impl, query, lang, min_score)
    except (QdrantStoreError, EmbedderError) as exc:
        logger.warning("similar_words unavailable: %s", exc)
        raise HTTPException(
            status_code=503,
            detail={"code": "similar_unavailable", "message": str(exc)},
        )
    # `words` : un objet {word, score} par entrée (le score Qdrant est
    # affiché entre parenthèses à côté de chaque mot dans le panneau
    # Dictionnaire, à la demande explicite de l'utilisateur).
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
    """État lecture seule de la base vectorielle Qdrant, pour le panneau
    "Qdrant (admin)" de l'interface (visible uniquement en localhost — cf.
    frontend/server.py). Renvoie toujours 200 : un Qdrant injoignable ou
    une collection absente sont indiqués par `reachable`/`exists`."""
    return await asyncio.to_thread(_qdrant_admin_impl)


class QdrantTenantRequest(BaseModel):
    lang: str


@app.post("/api/qdrant/admin/recreate")
async def qdrant_admin_recreate():
    """Supprime puis recrée la collection "words" (+ réindexe le tenant
    `lang`). Rapide, mais destructif : vide toutes les langues.
    L'alimentation se relance ensuite via
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
    """Supprime tous les vecteurs d'une langue (un tenant) de la
    collection "words". Rapide."""
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
            async for chunk in chatbot.reply_stream(
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
    # Sur une grille bilingue, `bilingual_language` désigne la langue des
    # mots verticaux (voir crossword_gen.generate_grid's own `bilingual_
    # language`) — chaque mot est donc vérifié contre LE DICTIONNAIRE DE
    # SA PROPRE LANGUE (`w.get("language", language)`, déjà posé par
    # generate_grid sur chaque entrée), jamais toujours le même. Les deux
    # paires {wordlist,gloss}_lines sont préchargées une seule fois
    # chacune (jamais reconstruites par mot) ; `wordlist_lines`/
    # `gloss_lines` restent les noms utilisés plus bas pour la langue
    # primaire, avec un second jeu chargé seulement si une grille bilingue
    # est effectivement en jeu.
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


async def _build_theme_glossary(theme, language, theme_precision, short_id,
                                cancel_event, log_tag):
    """Pré-recherche thématique complète pour UNE langue : expansion LLM en
    mots-clefs (describe_theme, + une liste par mot du thème + top-ups),
    puis une recherche Qdrant du plus-proche-voisin par mot-clef dans le
    tenant de cette langue, puis compilation (_compiled_theme_words_by_
    length). Renvoie `(priority_words | None, theme_description)`. Appelée
    une fois pour la langue principale et, sur une grille bilingue, une
    seconde fois pour la langue des mots verticaux, à la demande explicite
    de l'utilisateur : "Quand une grille est bilingue, il faut générer un
    glossaire thématique par langue." `log_tag` distingue les lignes de
    journal et le nom du fichier LOG_THEME/ des deux appels."""
    theme_priority_words = None
    theme_description = ""
    try:
        theme_description = await asyncio.to_thread(
            clue_generator.describe_theme,
            theme, language, cancel_event=cancel_event,
            temperature=THEME_KEYWORD_LLM_TEMPERATURE,
        )
    except GenerationCancelled:
        raise
    except Exception as exc:  # noqa: BLE001 — best-effort, on retombe sur les mots bruts
        logger.warning(
            "[%s] theme description failed (%s) — searching the raw theme words instead",
            log_tag, exc,
        )
        theme_description = ""
    logger.info(
        "[%s] theme %r -> description %r", log_tag, theme, theme_description,
    )
    # À la demande explicite de l'utilisateur : "demander au LLM de
    # générer des listes de 30 mots clefs séparés par des virgules
    # ... Compiler toutes les recherches dans Qdrant pour tous les
    # mots de ces listes (dédoublonner les mots)." La première
    # liste est toujours celle du thème ENTIER (sa description
    # LLM) ; s'y ajoute, quand le thème compte plus d'un mot, une
    # liste par mot — chacune passée elle aussi par describe_theme,
    # avec repli sur le mot brut si l'appel LLM échoue. Chaque
    # liste est ensuite découpée en mots-clefs (_split_keywords).
    keyword_lists: list[tuple[Optional[str], list[str]]] = [
        (None, _split_keywords(theme_description) or _theme_tokens(theme) or [theme])
    ]
    tokens = _theme_tokens(theme)
    if len(tokens) > 1:
        for tok in tokens:
            tok_desc = ""
            try:
                tok_desc = await asyncio.to_thread(
                    clue_generator.describe_theme,
                    tok, language, cancel_event=cancel_event,
                    temperature=THEME_KEYWORD_LLM_TEMPERATURE,
                )
            except GenerationCancelled:
                raise
            except Exception as exc:  # noqa: BLE001 — best-effort, repli sur le mot brut
                logger.warning(
                    "[%s] theme word %r description failed (%s) — searching the bare word",
                    log_tag, tok, exc,
                )
                tok_desc = ""
            keyword_lists.append((tok, _split_keywords(tok_desc) or [tok]))
        logger.info(
            "[%s] theme has %d words -> %d keyword lists",
            log_tag, len(tokens), len(keyword_lists),
        )
    # Met à plat toutes les listes en un seul ensemble de recherche
    # dédoublonné sans tenir compte de la casse (première graphie
    # conservée).
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
    # À la demande explicite de l'utilisateur (glossaire de grille
    # UNIQUEMENT, pas le Dictionnaire) : relancer describe_theme
    # jusqu'à THEME_KEYWORD_LLM_MAX_LOOPS fois de plus tant qu'on a
    # moins de THEME_MIN_KEYWORDS mots-clefs distincts à chercher.
    # Arrêt anticipé dès qu'un appel n'ajoute rien de neuf.
    for _loop in range(1, THEME_KEYWORD_LLM_MAX_LOOPS + 1):
        if len(searched_keywords) >= THEME_MIN_KEYWORDS:
            break
        _more = ""
        try:
            _more = await asyncio.to_thread(
                clue_generator.describe_theme,
                theme, language, cancel_event=cancel_event,
                temperature=THEME_KEYWORD_LLM_TEMPERATURE,
            )
        except GenerationCancelled:
            raise
        except Exception as exc:  # noqa: BLE001 — best-effort
            logger.warning(
                "[%s] theme keyword top-up %d failed (%s)", log_tag, _loop, exc,
            )
            break
        _kw_more = _split_keywords(_more)
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
        theme_scored_words, keyword_lists, searched_keywords,
        theme_precision,
    )
    return theme_priority_words, theme_description


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
            "[%s] starting generation: language=%s bilingual_language=%s width=%s "
            "height=%s difficulty=%s force_letters_percent=%s black_enrichment_percent=%s "
            "mode=%s theme_precision=%s",
            short_id, req.language, req.bilingual_language, req.width, req.height,
            req.difficulty, req.force_letters_percent, req.black_enrichment_percent,
            req.mode, req.theme_precision,
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
        # Pré-recherche thématique (voir THEME_LENGTH_MIN/MAX/THEME_MIN_
        # SCORE) : si le champ "Thématique" est non vide, on demande
        # D'ABORD au LLM une liste télégraphique d'environ 30 mots-clefs
        # décrivant la thématique (describe_theme) — une liste par mot du
        # thème quand il en compte plusieurs. Chaque liste est découpée en
        # mots-clefs individuels (_split_keywords), tous mis à plat et
        # dédoublonnés ; CHAQUE mot-clef fait alors sa propre recherche
        # Qdrant du plus-proche-voisin, et _compiled_theme_words_by_length
        # fusionne tous les résultats (meilleur score par mot) en un
        # glossaire que generate_grid() tentera en priorité pour chaque
        # emplacement. La ou les listes de mots-clefs du LLM sont écrites
        # en tête d'un fichier LOG_THEME/. Best-effort et fait une seule
        # fois (pas à chaque reprise après une pause de file) : un échec de
        # l'appel LLM fait simplement retomber sur les mots bruts du
        # thème ; une indisponibilité de Qdrant/embedder ou une collection
        # non alimentée pour cette langue n'empêche jamais la génération,
        # elle se fait alors sans thématique.
        theme = (req.theme or "").strip()
        theme_priority_words = None
        bilingual_theme_priority_words = None
        # Reste "" si `theme` est vide, ou si describe_theme échoue — lu
        # plus bas par le seul autre consommateur de cette variable, le
        # titre de la grille (voir clue_generator.generate_title(...,
        # theme_description=...) et son propre commentaire), qui doit
        # rester défini même sans thématique.
        theme_description = ""
        if theme:
            # Étape de statut dédiée, à la demande explicite de
            # l'utilisateur ("indiquer la phase de génération du
            # glossaire thématique") : cette phase (appel LLM +
            # pagination Qdrant par longueur, voir _theme_words_by_
            # length) peut prendre plusieurs secondes et ne se
            # signalait par rien de particulier jusque-là — le statut
            # affiché restait "starting" (ou l'étape précédente, sur une
            # reprise) tout du long. Un seul événement, sans progression
            # chiffrée (contrairement à "pattern"/"clues"/etc.) : tout ce
            # bloc s'exécute d'un bloc, sans sous-étapes à rapporter.
            progress("theme", theme=theme)
            theme_priority_words, theme_description = await _build_theme_glossary(
                theme, req.language, req.theme_precision, short_id, cancel_event,
                short_id,
            )
            # Grille bilingue : un second glossaire pour la langue des mots
            # verticaux (voir _build_theme_glossary), à la demande explicite
            # de l'utilisateur. `theme_description` reste celle de la langue
            # principale (titre + orientation des définitions).
            if req.bilingual_language and req.bilingual_language != req.language:
                bilingual_theme_priority_words, _ = await _build_theme_glossary(
                    theme, req.bilingual_language, req.theme_precision, short_id,
                    cancel_event, f"{short_id}_{req.bilingual_language}",
                )

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
        # Niveau de difficulté renvoyé sur le result du job, à la demande
        # explicite de l'utilisateur (affiché à droite du titre de la
        # grille à jouer, voir frontend/static/script.js). Un
        # enregistrement GRID_STORE (grille rechargée depuis la
        # bibliothèque) porte déjà ce champ ; ici on l'ajoute pour une
        # grille fraîchement générée.
        result["difficulty"] = req.difficulty
        # Thématique saisie (chaîne de mots) portée sur le result du job et
        # enregistrée dans la grille sauvegardée — `None` si le champ était
        # vide. Voir GenerateRequest.theme / grid_store.save_grid_json /
        # la colonne "Thématique" de la Bibliothèque.
        result["theme"] = theme or None

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
            _build_word_verification_table, result["words"], req.language,
            result.get("bilingual_language"),
        )
        # Cases des mots issus du glossaire thématique, à afficher en lettres
        # vertes dans l'aperçu (voir crossword_gen.py's `_theme_word_cells` /
        # renderAttemptPreview) — recalculées ici depuis `result["words"]`
        # (chaque mot porte `answer`/`row`/`col`/`direction`), la génération
        # `generate_grid` ne renvoyant pas de liste `theme_cells` de haut
        # niveau. Vide s'il n'y a pas de thématique.
        _theme_set = set(theme_priority_words or ())
        theme_cells = sorted({
            (w["row"] + (dk if w["direction"] != "across" else 0),
             w["col"] + (dk if w["direction"] == "across" else 0))
            for w in result["words"] if w["answer"] in _theme_set
            for dk in range(len(w["answer"]))
        })
        progress(
            "clues", current=0, total=len(result["words"]),
            examples=[{
                "example_grid": result["solution"],
                "impossible_cells": [],
                "forced_cells": [],
                "locked_cells": [],
                "theme_cells": theme_cells,
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
            # 4e élément (langue) au lieu de 3, à la demande explicite de
            # l'utilisateur pour une grille bilingue : chaque mot porte
            # déjà sa propre langue (crossword_gen.generate_grid's own
            # per-word `language`, selon sa direction) — LLMClueGenerator.
            # generate() résout cette langue par mot (repli sur `req.
            # language`, l'argument positionnel ci-dessous, pour un mot
            # qui n'en porterait pas) plutôt qu'une seule langue pour tout
            # l'appel comme avant cette fonctionnalité.
            remaining_entries = [
                (w["answer"], w["accented"], w["canonical"], w.get("language"))
                for w in result["words"]
            ]
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
                        on_progress=lambda current, total: progress(
                            "clues", current=len(accumulated_clues) + current, total=len(result["words"]),
                        ),
                        cancel_event=cancel_event,
                        should_pause=_make_should_pause(CLUES_QUEUE, task),
                        # Pour une grille thématique, indique au LLM la
                        # LISTE DE MOTS-CLEFS DU THÈME ENTIER (la sortie de
                        # describe_theme(theme) — la liste `[(whole
                        # theme)]`, JAMAIS une liste par mot ni de
                        # complétion) comme thématique dont il doit très
                        # fortement s'inspirer pour toutes les définitions,
                        # à la demande explicite de l'utilisateur : "il est
                        # important, quand il y a une thématique, que les
                        # définitions respectent au mieux cette
                        # thématique." La section THEME est ajoutée au
                        # message utilisateur de chaque mot (voir
                        # _build_user_message), pas au prompt système.
                        # Reste "" pour une grille non thématique (voir
                        # plus haut) : aucun effet.
                        theme_description=theme_description,
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
            pseudo = (req.pseudo or "").strip()[:MAX_PSEUDO_LENGTH] or None
            grid_id = await asyncio.to_thread(
                save_grid_json, result, req.language, req.difficulty, req.mode, title,
                result.get("bilingual_language"), pseudo, theme or None,
            )
            logger.info("[%s] saved to library: %s (pseudo=%r)", short_id, grid_id, pseudo)
            # L'identifiant du fichier GRID_STORE de cette grille, pour que
            # le frontend puisse la marquer "déjà vue" (localStorage) dès
            # qu'il l'affiche — à la demande explicite de l'utilisateur
            # ("y compris la grille qu'il vient de générer"). Même clé
            # (`id`) que GET /api/library/{grid_id} renvoie pour une grille
            # rechargée, donc le frontend traite les deux cas pareil.
            result["id"] = grid_id
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
                # Un recalcul de définitions ne rejoue pas la pré-recherche
                # thématique (la phrase de thème n'est pas persistée sur la
                # grille — voir grid_store), donc pas de mot thématique à
                # signaler ici.
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
                        on_progress=lambda current, total: progress(
                            "clues", current=len(accumulated_clues) + current,
                            total=len(result["words"]),
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
            # Un recalcul crée une nouvelle entrée "même grille, autres
            # définitions" — on conserve le pseudo de l'auteur d'origine
            # (déjà présent dans `result`, hérité du record rechargé)
            # plutôt que d'y mettre celui de qui lance le recalcul.
            new_grid_id = await asyncio.to_thread(
                save_grid_json, result, language, difficulty, mode, new_title,
                bilingual_language, result.get("pseudo"), result.get("theme"),
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
    # `bilingual_language` (voir GenerateRequest) subit exactement les
    # mêmes vérifications que `language` ci-dessus — mais seulement quand
    # une vraie grille bilingue est demandée (une valeur fournie ET
    # différente de `language`) ; `None` ou une valeur identique dégrade
    # déjà proprement en génération monolingue et n'a donc besoin d'aucune
    # validation supplémentaire.
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


# Les cinq phases exposées par GET /api/generate/phase/{job_id}, à la
# demande explicite de l'utilisateur ("file d'attente grille, génération
# de la grille, file d'attente définition, génération des définitions,
# grille terminée") — plus "error"/"cancelled" pour un job qui ne finira
# jamais normalement. Un condensé stable de `job["step"]["code"]` (voir
# _run_generate_job's own progress() closure), pensé pour un client
# d'automatisation (Automation/Populate.py) qui veut juste savoir "où en
# est ce job" sans avoir à connaître la dizaine de codes internes
# (pattern, pattern_attempt_failed, minimizing, pre_cleanup_optimized...).
_GRID_GENERATION_STEPS = frozenset({
    "starting", "pattern", "pattern_generated", "pattern_attempt_failed",
    "pattern_found", "pattern_failed", "pre_cleanup_optimizing",
    "pre_cleanup_optimized", "minimizing", "grid_ready",
})
_CLUES_GENERATION_STEPS = frozenset({"clues", "saving"})


def _job_phase(job):
    """Réduit l'état interne d'un job à l'une des cinq phases publiques
    (+ error/cancelled). `queued_grid`/`queued_clues` ne sont posés par
    _wait_in_queue que tant qu'il y a réellement de la contention ; sans
    file d'attente, un job passe directement de `starting` à la
    génération, donc `grid_queue`/`clues_queue` peuvent tout simplement
    ne jamais apparaître — c'est normal."""
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
    # `starting`, tous les codes de recherche de grille, et tout code
    # inattendu tant que le job tourne encore.
    return "grid_generation"


@app.get("/api/generate/phase/{job_id}")
def generate_phase(job_id: str):
    """Code de phase d'un job de génération, à la demande explicite de
    l'utilisateur. `phase` vaut l'une de : "grid_queue" (file d'attente
    grille), "grid_generation" (génération de la grille), "clues_queue"
    (file d'attente définitions), "clues_generation" (génération des
    définitions), "done" (grille terminée), ou "error"/"cancelled". Les
    autres champs (step_code brut, position/longueur de file, avancement
    des définitions) sont là pour le confort d'un client d'automatisation
    et peuvent être ignorés."""
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
