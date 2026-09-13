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
from .clues import ClueGenerationError, LLMClueGenerator, TITLE_PROPOSALS_COUNT
from .dictionary_lookup import search as dictionary_search_impl
from .embedder import Embedder, EmbedderError
from .qdrant_store import QdrantStore, QdrantStoreError
from .secret_store import verify_or_claim as verify_or_claim_pseudo_secret
from .crossword_gen import (
    DEFAULT_HEIGHT, DEFAULT_WIDTH, DIFFICULTY_PRESETS, GenerationCancelled, GenerationPaused,
    PREFILL_MIN_WORD_COUNT, DualIndex, DualSet, build_index, build_letters_grid,
    build_word_entries, extract_slots, generate_grid, _interactive_fill_diagnostics,
    _serialize_resume_state, interactive_clean_impossible_zones, interactive_minimize_black_cells,
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
# requests, on a second GPU — at the user's explicit request: "Toutes les
# requêtes de génération automatique, en provenance de Populate ou de
# l'interface, sont affectée à la première carte. Toutes les requêtes
# interactives (interface d'édition interactive, ChatBot, Dictionnaire,
# Paraphraser, etc) sont affectés à la seconde carte." `clue_generator`/
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
THEME_LENGTH_MIN = 3
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
    deux appels (factorisés dans _refresh_rss/_refresh_scrapp ci-dessous,
    partagés avec _rss_startup_catchup) — l'échec de l'un ne doit jamais
    empêcher l'autre de tourner ce même jour, exactement le même principe
    déjà appliqué en interne à chaque flux RSS pris individuellement.

    Ce mécanisme n'a lui-même AUCUN rattrapage : s'il ne tourne pas
    exactement à RSS_FETCH_HOUR un jour donné (ex. un redémarrage du
    processus survenu entre les deux appels, un incident réel constaté le
    2026-09-11 où SCRAPP était resté daté de la veille malgré un RSS
    fraîchement à jour), rien ne retente avant le tick du lendemain —
    voir _rss_startup_catchup, qui comble exactement ce trou au
    démarrage du processus."""
    while True:
        now = datetime.datetime.now()
        next_run = now.replace(hour=RSS_FETCH_HOUR, minute=0, second=0, microsecond=0)
        if next_run <= now:
            next_run += datetime.timedelta(days=1)
        await asyncio.sleep((next_run - now).total_seconds())
        await _refresh_rss()
        await _refresh_scrapp()


async def _refresh_rss():
    """Un appel à fetch_rss_feeds.fetch_all(), journalisé dans tous les
    cas — factorisé pour être partagé entre le tick quotidien ci-dessus
    et le rattrapage au démarrage ci-dessous (_rss_startup_catchup), pour
    qu'ils ne puissent jamais diverger dans la façon de rapporter un
    succès/échec."""
    try:
        items = await asyncio.to_thread(fetch_rss_feeds.fetch_all)
        logger.info("rss: %d articles rafraichis", len(items))
    except Exception:
        logger.exception("rss: echec du rafraichissement")


async def _refresh_scrapp():
    """Même rôle que _refresh_rss ci-dessus, pour fetch_grid_links.
    fetch_all()."""
    try:
        grids = await asyncio.to_thread(fetch_grid_links.fetch_all)
        logger.info("scrapp: %d grilles rafraichies", len(grids) if grids is not None else 0)
    except Exception:
        logger.exception("scrapp: echec du rafraichissement")


def _combined_json_is_fresh(path):
    """True si `path` (RSS/combined.json ou SCRAPP/combined.json) existe
    et que son propre `fetched_at` date d'aujourd'hui (date locale) —
    utilisé par _rss_startup_catchup ci-dessous. False dans tout autre
    cas (fichier absent, illisible, ou daté d'un jour antérieur), sans
    jamais lever — un fichier corrompu/absent doit simplement déclencher
    un rafraîchissement, pas faire planter le démarrage du serveur."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        fetched_at = datetime.datetime.fromisoformat(data["fetched_at"])
        if fetched_at.tzinfo is not None:
            fetched_at = fetched_at.astimezone().replace(tzinfo=None)
        return fetched_at.date() == datetime.datetime.now().date()
    except (OSError, json.JSONDecodeError, KeyError, ValueError):
        return False


async def _rss_startup_catchup():
    """Rattrapage exécuté une seule fois, au démarrage du processus (pas
    sur le cycle quotidien de RSS_FETCH_HOUR) — à la suite d'un incident
    réel constaté le 2026-09-11 : RSS/combined.json s'était bien
    rafraîchi à 8h ce matin-là, mais SCRAPP/combined.json était resté
    daté de la veille. _rss_daily_scheduler() lui-même est pourtant
    correct (chacun des deux appels a son propre try/except indépendant,
    l'échec de l'un ne peut jamais empêcher l'autre de tourner dans le
    même tick) — le scénario le plus probable est qu'un redémarrage du
    processus (pour une raison sans rapport) est survenu exactement entre
    les deux appels ce matin-là, avant que fetch_grid_links.fetch_all()
    n'ait eu le temps d'écrire son fichier. Dans ce cas, sans ce
    rattrapage, plus aucun rafraîchissement n'aurait eu lieu avant le
    tick du lendemain 8h — jusqu'à 24h de retard, exactement ce qui a été
    signalé. Relance chacun des deux fetch, indépendamment, seulement si
    son propre fichier n'est pas déjà daté d'aujourd'hui — ne fait donc
    rien du tout au démarrage un jour où les deux se sont déjà bien
    rafraîchis normalement."""
    if not _combined_json_is_fresh(RSS_DIR / "combined.json"):
        await _refresh_rss()
    if not _combined_json_is_fresh(SCRAPP_DIR / "combined.json"):
        await _refresh_scrapp()


@app.on_event("startup")
async def _start_rss_scheduler():
    asyncio.create_task(_rss_daily_scheduler())
    asyncio.create_task(_rss_startup_catchup())
    # Balayage périodique de la présence : capte une baisse d'effectif
    # (LOG_USERS/) même quand plus aucun battement n'arrive — voir
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

# Longueur max du mot secret associé à un pseudo (backend/secret_store.py),
# à la demande explicite de l'utilisateur — permet à un utilisateur de
# prouver qu'un pseudo choisi lui appartient bien. Même convention
# défensive que MAX_PSEUDO_LENGTH : borné côté serveur, jamais rejeté pour
# une longueur excessive avant troncature.
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
# `_last_logged_pseudos` : `POST /api/presence` (fonction `def`
# synchrone, exécutée dans le pool de threads de Starlette) et le balayage
# périodique `_presence_sweep_scheduler` (coroutine sur la boucle
# d'événements) peuvent tous deux recalculer l'effectif — sans verrou, la
# purge des sessions expirées d'un côté pourrait lever pendant une écriture
# de l'autre, et deux appelants pourraient voir simultanément « l'effectif
# a changé » et écrire une ligne en double. Le verrou ne couvre que le
# recalcul en mémoire ; l'écriture disque se fait toujours en dehors.
_PRESENCE_LOCK = threading.Lock()

# Dernière LISTE d'utilisateurs distincte consignée dans LOG_USERS (None au
# démarrage, si bien que le tout premier battement après lancement/
# redémarrage du serveur consigne une ligne — ce qui marque aussi un
# redémarrage dans la chronologie). À la demande explicite de
# l'utilisateur ("LOG_USERS doit se mettre à jour à chaque fois que la
# liste des utilisateurs change") : suivi par LISTE complète (le `pseudos`
# que _write_users_log consigne), pas seulement par effectif total — un
# simple compteur ne capte jamais qu'un utilisateur est passé d'anonyme à
# nommé, ou qu'un pseudo a changé, tant que le nombre total reste
# identique, ce qui laissait justement le journal figé sur un
# "(anonyme)" périmé une fois le panneau d'accueil validé.
_last_logged_pseudos = None

# Le balayage périodique existe pour capter une *baisse* d'effectif quand
# les battements s'arrêtent (tout le monde a quitté) : sans lui, la purge
# ne tourne que dans `POST /api/presence`, donc le passage à 0 ne serait
# jamais consigné avant qu'un nouveau visiteur ne se connecte. 10s est
# assez fin devant PRESENCE_TTL_S (60s) et coûte quasiment rien (itération
# d'un dict d'au plus MAX_PRESENCE_ENTRIES entrées).
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
    # Origine de la requête, à la demande explicite de l'utilisateur :
    # "Populate : quand une demande vient de Populate, générer les
    # définitions sans paralléliser plusieurs requêtes en parallèle, pour
    # ne pas surcharger le GPU pour les utilisateurs." `None` (une requête
    # ordinaire du web UI) par défaut — `Automation/Populate.py` est le
    # seul appelant à envoyer `"populate"` ici (voir sa propre
    # `_build_request()`). `_run_generate_job` lit ce champ pour forcer
    # `clue_generator.generate(batch_parallelism=1)` (voir backend/
    # clues.py's own docstring) uniquement pour ce cas — CLUES_QUEUE
    # sérialise déjà les jobs entre eux, mais un seul job peut encore
    # tirer jusqu'à CLUE_BATCH_PARALLELISM requêtes LLM concurrentes, ce
    # qui pouvait ralentir un vrai utilisateur appelant en parallèle un
    # endpoint hors file (ex. "Proposer une définition"/"Proposer un
    # titre"). Non borné par une contrainte pydantic : une valeur
    # inconnue est simplement ignorée (traitée comme une requête
    # ordinaire), jamais un 422.
    source: Optional[str] = None


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
    it (no backtracking) and returns the updated grid."""
    job_id: str
    grid: list[list[str]]


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
    touched while resolving an impossible zone."""
    job_id: str
    grid: list[list[str]]
    deep: bool = False


class InteractiveCandidatesRequest(BaseModel):
    """Body of POST /api/interactive/candidates — the "Mots" button of the
    "Interactif" authoring mode, at the user's explicit request: "ajouter
    un bouton Mots qui liste les mots possible pour l'emplacement
    sélectionné." `cells` is the selected word's own ordered list of
    `[row, col]` pairs (already resolved client-side by
    `selectedInteractiveWord()`, script.js) — never recomputed server-side
    from `grid` alone, so this works the same way whether that word is
    already fully typed or still partially empty."""
    job_id: str
    grid: list[list[str]]
    cells: list[list[int]]


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
    and additionally verifies every complete word has a definition."""
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
    dictionary membership, nothing else."""
    job_id: str
    words: list[InteractiveVerifyWord]


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
    resend them correctly on every single autosave."""
    job_id: str
    grid: list[list[str]]
    definitions: list[dict] = []
    title: str = ""
    pseudo: Optional[str] = None


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
    - `changed`: `True` si et seulement si `pseudos` (la LISTE complète,
      pas seulement sa longueur) diffère de la dernière liste consignée
      dans LOG_USERS — et, dans ce cas, met à jour ce marqueur ici même
      (sous le verrou), de sorte qu'un seul appelant voit jamais une
      transition donnée et écrit une seule ligne. Un utilisateur passant
      d'anonyme à nommé, ou changeant de pseudo, déclenche donc une
      nouvelle ligne même quand l'effectif total, lui, ne bouge pas.

    Le verrou ne couvre que ce recalcul en mémoire ; l'appelant fait
    l'écriture disque (`_write_users_log`) en dehors."""
    global _last_logged_pseudos
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
        changed = pseudos != _last_logged_pseudos
        if changed:
            _last_logged_pseudos = pseudos
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
    """Corps de POST /api/pseudo/claim — soumis à la fermeture du panneau
    d'accueil (voir frontend/static/script.js, `welcomeForm`), à la
    demande explicite de l'utilisateur : "ajouter une entrée 'Mot secret'
    permettant à l'utilisateur de prouver que le pseudo lui appartient."
    Contrairement à tout autre champ `pseudo` de ce fichier (toujours
    `Optional[str] = None`, tronqué en silence à MAX_PSEUDO_LENGTH),
    celui-ci est ici obligatoire (`min_length=1`) — mais volontairement
    sans `max_length` : une valeur trop longue est tronquée en silence
    par le corps de la route ci-dessous, jamais rejetée, même convention
    que partout ailleurs dans ce fichier pour MAX_PSEUDO_LENGTH."""
    pseudo: str = Field(..., min_length=1)
    secret: str = Field(..., min_length=1)


@app.post("/api/pseudo/claim")
def pseudo_claim(req: PseudoClaimRequest):
    """Vérifie que `secret` correspond au mot secret déjà associé à
    `pseudo` (backend/secret_store.py), ou l'enregistre si ce pseudo n'a
    jamais été revendiqué (première utilisation = revendication).

    Renvoie `{"ok": true}` en cas de succès (mot secret correct, ou
    pseudo tout juste revendiqué) ; `{"ok": false, "code": "pseudo_taken"}`
    si ce pseudo existe déjà sous un autre mot secret — à la demande
    explicite de l'utilisateur : "si le Pseudo saisi existe déjà et que
    le Mot secret ne correspond pas, signaler à l'utilisateur que ce
    Pseudo est déjà pris, ne pas fermer la boite." Toujours un 200 dans
    les deux cas : ce n'est pas une erreur de requête, seulement un
    résultat métier normal que le client doit distinguer lui-même."""
    pseudo = req.pseudo.strip()[:MAX_PSEUDO_LENGTH]
    secret = req.secret.strip()[:MAX_SECRET_LENGTH]
    if not pseudo or not secret:
        raise HTTPException(status_code=400, detail="pseudo ou mot secret vide")
    if verify_or_claim_pseudo_secret(pseudo, secret):
        return {"ok": True}
    return {"ok": False, "code": "pseudo_taken"}


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
    return get_system_info(
        clue_generator.model,
        interactive_llm_model=(
            interactive_clue_generator.model if interactive_clue_generator is not clue_generator else None
        ),
        embed_model=_similar_embedder.model,
        embed_on_gpu=_embed_gpu_layers > 0,
    )


_LIBRARY_SEEN_FILTERS = ("all", "unseen", "seen", "mine")
_LIBRARY_DIFFICULTY_FILTERS = ("easy", "medium", "hard")


class GridGameSaveRequest(BaseModel):
    """Corps de POST /api/game/save — autosauvegarde de la partie en cours
    d'un joueur sur une grille de la bibliothèque (voir grid_store.
    save_grid_game), à la demande explicite de l'utilisateur : "A chaque
    modification de la grille, sauvegarder l'état de la grille dans
    GRID_GAME avec le nom de l'utilisateur... Inclure l'état du compteur
    temps." `grid_id` doit correspondre à une grille réellement stockée
    (id de GRID_STORE, voir grid_store._GRID_ID_RE) ; `pseudo` est
    obligatoire — le frontend ne déclenche cet appel que si un pseudo est
    déjà défini (voir script.js's scheduleGridGameSave), mais l'endpoint
    le revalide quand même côté serveur."""
    grid_id: str
    pseudo: str
    user_letters: list[list[str]]
    elapsed_seconds: int = Field(default=0, ge=0)


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
    une liste vide, pas une erreur.

    Le filtre "bilingual" subit la même annulation du regroupement que
    "all", à la demande explicite de l'utilisateur : "La liste des
    grilles de la Bibliothèque Bilingue doit être classée dans l'ordre
    chronologique inverse (et non regroupé par langues)." Le regroupement
    de list_grids() se fait sur le champ `language` (la langue primaire)
    de chaque grille — pour une grille bilingue, ce champ varie d'une
    grille à l'autre (fr/en, es/it, ...), donc ce regroupement n'est
    JAMAIS un no-op ici, contrairement au cas d'une langue précise (où
    toutes les lignes partagent déjà la même langue)."""
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
    # "Toutes les langues" ET "Bilingue" (ni l'un ni l'autre n'est une
    # vraie clé de WORDLISTS) : ordre purement chronologique inverse, sans
    # le regroupement par langue que list_grids() applique pour la vue par
    # défaut — à la demande explicite de l'utilisateur, y compris pour
    # "Bilingue" spécifiquement : "La liste des grilles de la Bibliothèque
    # Bilingue doit être classé dans l'ordre chronologique inverse (et non
    # regroupé par langues)." Un filtre sur une langue précise rend ce
    # regroupement inopérant de toute façon (toutes les lignes partagent la
    # même langue), donc on ne re-trie que dans ce seul cas-là.
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
def library_get(grid_id: str, pseudo: str = ""):
    """Charge une grille précédemment sauvegardée pour la rejouer —
    renvoie exactement la même forme qu'un job terminé (`result`, voir
    _run_generate_job), avec en plus les métadonnées de la bibliothèque
    (id/titre/langue/difficulté/mode/date), pour que le frontend puisse
    l'afficher via le même chemin de code qu'une génération qui vient de
    se terminer (voir frontend/static/script.js's displayFinalGrid).

    `pseudo` (optionnel) : si renseigné, cherche aussi dans GRID_GAME
    (voir grid_store.get_grid_game) une partie déjà sauvegardée par ce
    joueur pour cette grille précise, et l'ajoute au résultat sous
    `saved_game` (`{user_letters, elapsed_seconds}`, ou absent/None si
    rien n'a été trouvé) — à la demande explicite de l'utilisateur :
    "Dans la Librairie, quand un utilisateur clique pour jouer sur une
    grille, chercher si cette grille existe dans GRID_GAME pour la
    recharger et relancer le compteur de temps là où il était à la
    sauvegarde." Le frontend (loadLibraryGrid) transmet le pseudo courant
    à chaque appel ; omis, ce champ est simplement absent, sans erreur —
    une génération fraîchement terminée n'a jamais de partie sauvegardée
    à chercher (son grid_id vient d'être créé)."""
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


@app.post("/api/game/save")
def game_save(req: GridGameSaveRequest):
    """Autosauvegarde de la partie en cours (voir GridGameSaveRequest /
    grid_store.save_grid_game) — appelée par le frontend à chaque
    modification de la grille en mode jeu (lettre tapée ou effacée), tant
    qu'un pseudo est défini. Toujours un simple accusé de réception
    (`{"ok": True}`) ; jamais d'erreur si aucune grille GRID_STORE ne
    correspond réellement à `grid_id` — un game state reste valable même
    pour une grille qui ne serait, hypothétiquement, plus référencée
    ailleurs (il n'existe aujourd'hui aucun mécanisme de suppression de
    grille de la bibliothèque)."""
    if not save_grid_game(req.grid_id, req.pseudo, req.user_letters, req.elapsed_seconds):
        raise HTTPException(status_code=400, detail="identifiant de grille ou pseudo invalide")
    return {"ok": True}


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
async def dictionary_define(q: str, lang: str = "fr", theme: str = ""):
    """"Définir" bouton du panneau Dictionnaire (voir frontend/static/
    script.js) : demande au LLM jusqu'à DEFINE_COUNT (10) définitions
    indépendantes de l'expression saisie, comme pour un mot de grille
    (backend/clues.py, LLMClueGenerator.generate_definitions — même
    ancrage réel dictionnaire/exemples, même filtre de contenu — mais un
    seul appel best-effort, sans la boucle de relance par mot d'une
    génération de grille). Un ClueGenerationError (LLM injoignable)
    devient un 503 propre ; le reste de l'UI n'est pas affecté.

    `theme` (optionnel, "" par défaut) est le contenu actuel du champ
    "Thématique" du mode Interactif — à la demande explicite de
    l'utilisateur ("Vérifier que le bouton 'Propose une définition'
    utilise bien le champ thématique pour les propositions quand il est
    renseigné") — le bouton "Proposer" (et "Définitions") de ce mode
    l'envoie systématiquement quand ce champ n'est pas vide (voir
    frontend/static/script.js's `dictionaryDefineUrl`). Le panneau
    Dictionnaire générique, lui, n'envoie jamais ce paramètre : sans
    grille en cours, il n'y a pas de thématique à transmettre."""
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
    """"Paraphraser" bouton du panneau "Paraphraseur" (voir frontend/
    static/script.js), à la demande explicite de l'utilisateur : demande
    au LLM PARAPHRASE_COUNT (5) reformulations indépendantes du texte
    saisi (backend/clues.py, LLMClueGenerator.generate_paraphrases — un
    seul appel best-effort, sans ancrage dictionnaire/exemples, sans la
    boucle de relance par mot d'une génération de grille). Un
    ClueGenerationError (LLM injoignable) devient un 503 propre."""
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
    la version "glossaire de grille" : (1) aucun filtre de longueur 3-15
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


def _synonyms_impl(query: str, lang: str,
                    min_score: float = THEME_MIN_SCORE) -> list[tuple[str, float]]:
    """Bouton "Synonymes" du panneau Dictionnaire, à la demande explicite
    de l'utilisateur : "un bouton 'Synonymes' qui lance une recherche
    Qdrant avec le mot ou l'expression saisie (sans faire appel au LLM
    pour étendre la recherche, comme le fait Thématique)." Contrairement
    à `_similar_words_impl` juste en dessous (qui demande d'abord à
    `describe_theme` une liste d'une trentaine de mots-clefs avant de
    lancer une recherche Qdrant par mot-clef), cette fonction réutilise
    directement `_compiled_similar_words` avec la requête brute comme
    UNIQUE mot-clef — pour une seule entrée, cette fonction se réduit
    exactement à "embedder la requête telle quelle, chercher dans Qdrant,
    trier par score décroissant" : aucune expansion, aucun appel LLM,
    donc aucun risque de dérive thématique (une recherche "chat" reste
    une recherche du mot "chat" lui-même, jamais élargie à son champ
    lexical). Même seuil `min_score`/mêmes conventions de tri que
    `_similar_words_impl`, pour que le panneau puisse réutiliser le même
    rendu (`renderSimilarWordsResult`) sans distinction."""
    return _compiled_similar_words([query], lang, min_score)


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
    no length filter (unlike the grid glossary's 3-15 bound). Returned
    most-similar-first; the score is shown to 2 decimals next to each word
    in the panel."""
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


@app.get("/api/synonyms")
async def synonyms(q: str, lang: str = "fr", min_score: float = THEME_MIN_SCORE):
    """Bouton "Synonymes" du panneau Dictionnaire : recherche Qdrant
    directe sur `q` (embedding brut, aucun appel LLM), contrairement au
    bouton "Thématique" (`/api/similar_words`) qui étend d'abord la
    recherche via `describe_theme` — voir `_synonyms_impl`. Même forme de
    requête/réponse que `/api/similar_words`, réutilisable telle quelle
    par le même rendu côté frontend."""
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
    """Pré-recherche thématique complète pour UNE langue : expansion LLM en
    mots-clefs (describe_theme, + une liste par mot du thème + top-ups),
    puis une recherche Qdrant du plus-proche-voisin par mot-clef dans le
    tenant de cette langue, puis compilation (_compiled_theme_words_by_
    length). Renvoie `(priority_words | None, theme_description)`. Appelée
    une fois pour la langue principale et, sur une grille bilingue, une
    seconde fois pour la langue des mots verticaux, à la demande explicite
    de l'utilisateur : "Quand une grille est bilingue, il faut générer un
    glossaire thématique par langue [...] demander au LLM de générer des
    mots dans la langue de la grille, en tenant compte du fait que les
    grilles peuvent être bilingues (une langue différente par sens, mais
    avec les mêmes mots Thématiques en entrée)." `log_tag` distingue les
    lignes de journal et le nom du fichier LOG_THEME/ des deux appels.

    `theme_language` (`None` par défaut — aucun effet) est la langue dans
    laquelle `theme` a probablement été tapé, quand elle est CONNUE et
    DIFFÉRENTE de `language` — c'est-à-dire uniquement pour le second
    appel (bilingue), dont la langue cible diffère par construction de la
    langue principale. Passé tel quel à chaque appel `describe_theme(...,
    verify_translation=...)` de cette fonction (l'appel du thème entier,
    chaque appel par mot, et les top-ups) — voir ce paramètre pour la
    raison de ne l'activer QUE lorsqu'un décalage de langue est
    réellement probable (un faux positif sur le cas courant, même
    langue, coûterait des tentatives inutiles pour rien).

    `clue_gen` (le `clue_generator` module-niveau — l'instance AUTOMATIQUE
    — par défaut) est l'instance `LLMClueGenerator` dont `describe_theme`
    est appelé pour toute la fonction : `_run_generate_job` (génération
    automatique, Populate/interface) laisse la valeur par défaut, tandis
    que `_run_interactive_job` (mode Interactif) passe explicitement
    `interactive_clue_generator` — voir la répartition carte 1/carte 2 à
    la demande explicite de l'utilisateur, documentée juste au-dessus de
    la construction de ces deux instances en tête de fichier."""
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


async def _run_generate_job(job_id, req, resume_state=None, override_priority_words=None,
                             override_theme_description="", preserved_clues=None,
                             permanent_locked_letters=None, publish=True, origin=None):
    """`publish` (`True` by default — every pre-existing caller unaffected)
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

    `preserved_clues` (`None` by default — a `{(row, col, direction):
    clue}` map, no effect for any pre-existing caller), at the user's
    explicit request for "Finir la grille" ("génération des définitions
    manquantes... mais pas celles déjà définies"): a word whose exact
    (row, col, direction) is in this map already has a clue — its letters
    were locked into the search as hard constraints (see
    `permanent_locked_letters` below), so its position/spelling can't
    have changed — and is excluded from the LLM clue-generation batch
    entirely, keeping that clue text verbatim instead of asking the LLM
    for a new one.

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
    generation, however many paliers it takes."""
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
        if override_priority_words is not None:
            # "Finir la grille" (see POST /api/interactive/finish): reuses
            # the glossary already resolved by the interactive session
            # itself verbatim, rather than re-running the whole LLM/Qdrant
            # call — see this function's own docstring.
            theme_priority_words = override_priority_words
            theme_description = override_theme_description
        elif theme:
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
            # Grille bilingue : un second glossaire pour la langue des mots
            # verticaux (voir _build_theme_glossary), à la demande explicite
            # de l'utilisateur. `theme_description` reste celle de la langue
            # principale (titre + orientation des définitions). Les deux
            # appels sont lancés en parallèle via asyncio.gather (à la
            # demande explicite de l'utilisateur : "paralléliser la
            # génération des deux langues") plutôt que l'un après l'autre —
            # chacun n'est qu'un enchaînement d'appels déjà enveloppés dans
            # asyncio.to_thread (l'appel LLM describe_theme, la recherche
            # Qdrant), donc les deux tournent réellement en même temps sur
            # le thread pool sans jamais se bloquer mutuellement ni bloquer
            # la boucle asyncio elle-même. `return_exceptions=True` : les
            # deux résultats sont d'abord récupérés avant qu'une éventuelle
            # exception (typiquement GenerationCancelled, si l'utilisateur
            # clique Stop pendant que les deux tournent) ne soit relevée
            # explicitement — sans ça, l'exception de la tâche la plus
            # rapide serait levée immédiatement par gather() sans jamais
            # attendre/consommer le résultat (ou l'exception) de l'autre,
            # ce qu'asyncio journalise comme "exception never retrieved".
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
                        permanent_locked_letters=permanent_locked_letters,
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
        # "Finir la grille" (see POST /api/interactive/finish): a word
        # whose exact (row, col, direction) already carries a preserved
        # clue (`preserved_clues`) is never sent to the LLM — its letters
        # were just locked as a hard constraint in the search (see
        # `resume_state`), so its position/spelling can't have changed.
        # `None`/empty for any pre-existing caller: `words_needing_clue`
        # then becomes `result["words"]` in full, `total_words_for_clues`
        # == `len(result["words"])`, unchanged behavior. Computed here
        # (not only further below, where `remaining_entries` also needs
        # it) so this very first "clues" event already shows the true
        # total word count to define, consistent with the progress shown
        # afterward.
        preserved_by_key = preserved_clues or {}
        words_needing_clue = [
            w for w in result["words"]
            if not preserved_by_key.get((w["row"], w["col"], w["direction"]))
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
            # `words_needing_clue`/`preserved_by_key` already computed above
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
                        # 1 seule requête LLM à la fois pour une grille
                        # venant de Populate, à la demande explicite de
                        # l'utilisateur ("ne pas surcharger le GPU pour
                        # les utilisateurs") — voir GenerateRequest.source
                        # et LLMClueGenerator.generate's own docstring.
                        # `None` (le comportement par défaut, parallèle)
                        # pour toute autre requête.
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
                # it was never part of `remaining_entries`).
                preserved = preserved_by_key.get((w["row"], w["col"], w["direction"]))
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
        # Les paramètres du moteur de recherche automatique (voir
        # grid_store.save_grid_json's own `generation_params` docstring),
        # à la demande explicite de l'utilisateur : "sauvegarder tous les
        # paramètres... pour pouvoir les reconfigurer à l'identique quand
        # la grille est rechargée en mode édition." `mode` est déjà son
        # propre champ de premier niveau (voir juste au-dessus) — dupliqué
        # ici aussi pour que le Front n'ait qu'un seul objet à lire pour
        # reconfigurer les 4 champs du formulaire à la fois. Calculé
        # inconditionnellement (avant le `if publish:` ci-dessous) car les
        # deux branches en ont besoin.
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
        placed = await asyncio.to_thread(
            interactive_place_word, grid, rows, cols, index, rng, priority_words,
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
            # Un recalcul crée une nouvelle entrée "même grille, autres
            # définitions" — on conserve le pseudo de l'auteur d'origine
            # (déjà présent dans `result`, hérité du record rechargé)
            # plutôt que d'y mettre celui de qui lance le recalcul.
            new_grid_id = await asyncio.to_thread(
                save_grid_json, result, language, difficulty, mode, new_title,
                bilingual_language, result.get("pseudo"), result.get("theme"),
                # Un recalcul ne touche jamais la mise en page de la grille,
                # seulement ses définitions — les paramètres du moteur de
                # recherche qui l'ont produite restent donc valables et sont
                # simplement reconduits (`result` est déjà le record d'origine
                # minus id/created_at/bilingual, voir plus haut).
                generation_params=result.get("generation_params"),
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
    placed = await asyncio.to_thread(
        interactive_place_word,
        [list(row) for row in req.grid], rows, cols,
        sess["index"], sess["rng"], sess["priority_words"],
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
    result = await asyncio.to_thread(
        interactive_clean_impossible_zones,
        [list(row) for row in req.grid], rows, cols,
        sess["index"], sess["rng"],
    )
    grid = result["grid"]
    removed_black_count = 0
    if req.deep:
        black_result = await asyncio.to_thread(
            interactive_minimize_black_cells, grid, rows, cols, sess["index"], sess["rng"],
        )
        if black_result["changed"]:
            grid = black_result["grid"]
            removed_black_count = black_result["removed_count"]
    imp, low = await asyncio.to_thread(
        _interactive_fill_diagnostics, grid, rows, cols, sess["index"],
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
    other match (capped, see `INTERACTIVE_SLOT_CANDIDATES_LIMIT`) — see
    `interactive_slot_candidates`."""
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
    theme_words, other_words = await asyncio.to_thread(
        interactive_slot_candidates,
        [list(row) for row in req.grid], rows, cols,
        sess["index"], cells, sess["priority_words"],
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
    imp, low = await asyncio.to_thread(
        _interactive_fill_diagnostics, [list(row) for row in req.grid], rows, cols, sess["index"],
    )
    return {"impossible_cells": imp, "low_candidate_cells": low}


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

    def _check():
        invalid = []
        seen = set()
        for w in req.words:
            key = (w.direction, w.answer)
            if key in seen:
                continue
            seen.add(key)
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
        # Voir grid_store.save_grid_json's own `generation_params`
        # docstring — reconduit tel quel (None pour une session
        # démarrée à la main, les vraies valeurs pour une grille
        # rééditée depuis une création automatique via "Ouvrir en mode
        # Interactif"), pour que la provenance des réglages survive la
        # publication comme "origin"/"theme" ci-dessus.
        generation_params=meta.get("generation_params"),
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
        # Voir grid_store.save_grid_json's own `generation_params`
        # docstring — les paramètres du moteur de recherche automatique
        # (Taux noir/Graines/Mode/Précision thématique) qui ont produit
        # cette grille, None pour une grille jamais issue d'une création
        # automatique. Mirroré à la fois sur job["interactive"] et
        # job["result"] (pollJob() ne renvoie jamais que ce dernier),
        # même convention que "theme"/"origin" ci-dessous, pour que
        # enterInteractiveMode() puisse reconfigurer les champs Mode/Taux
        # noir/Graines/Précision thématique du formulaire à l'identique.
        generation_params = record.get("generation_params")
        grid = record.get("grid") or []
        rows = len(grid)
        cols = len(grid[0]) if grid else 0
        imp, low = await asyncio.to_thread(_interactive_fill_diagnostics, grid, rows, cols, index)

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
        # Voir grid_store.save_grid_json's own `generation_params`
        # docstring — None pour une grille jamais issue d'une création
        # automatique (ex. une grille elle-même construite à la main en
        # mode Interactif, puis publiée).
        "generation_params": record.get("generation_params"),
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
    (row, col, direction) already carries a real definition
    (`preserved_clues`) keeps that clue untouched — only a genuinely NEW
    word (one the search itself completed) ever gets sent to the LLM.

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
    keeps the same provenance."""
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
    resume_state = _serialize_resume_state(seed_grid, locked_letters, None, None)
    # {(row, col, direction): clue} for every definition already typed —
    # this is `interactive_finish`'s own preserved-clues map; an empty
    # entry (word not defined yet) is simply omitted, letting that word
    # go through automatic clue generation like any other missing one.
    preserved_clues = {
        (d.get("row"), d.get("col"), d.get("direction")): (d.get("clue") or "").strip()
        for d in req.definitions
        if (d.get("clue") or "").strip()
    }
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
            publish=False, origin=meta.get("origin"),
        )
    )
    _BACKGROUND_TASKS.add(task)
    task.add_done_callback(_BACKGROUND_TASKS.discard)
    return {"job_id": new_job_id}


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
    "starting", "theme", "interactive_building", "pattern", "pattern_generated",
    "pattern_attempt_failed", "pattern_found", "pattern_failed",
    "pre_cleanup_optimizing", "pre_cleanup_optimized", "minimizing", "grid_ready",
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
