#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")"

# librsvg (rsvg-convert) — a runtime dependency of the app itself, not just a
# build/dev tool: backend/svg_export.py's save_grid_png() calls it after
# every successfully generated grid to render the GRID_SAMPLES/ PNG (best-
# effort — a missing binary is logged as a warning, never fails the
# request, which is exactly the gap that let this go unnoticed until now).
# Also used to render frontend/static/logo.png from its source SVG (see the
# style-guide SKILL). Installed here so a fresh machine has it from the
# start instead of only discovering the gap from a runtime warning later.
if ! command -v rsvg-convert >/dev/null 2>&1; then
    echo "Installing librsvg (rsvg-convert)..."
    if [ "$(uname -s)" = "Darwin" ]; then
        if command -v brew >/dev/null 2>&1; then
            brew install librsvg || echo "Warning: 'brew install librsvg' failed — install it manually."
        else
            echo "Warning: Homebrew not found — install librsvg manually (brew install librsvg)."
        fi
    elif command -v apt-get >/dev/null 2>&1; then
        sudo apt-get update && sudo apt-get install -y librsvg2-bin \
            || echo "Warning: 'apt-get install librsvg2-bin' failed — install it manually."
    elif command -v dnf >/dev/null 2>&1; then
        sudo dnf install -y librsvg2-tools \
            || echo "Warning: 'dnf install librsvg2-tools' failed — install it manually."
    elif command -v pacman >/dev/null 2>&1; then
        sudo pacman -S --noconfirm librsvg \
            || echo "Warning: 'pacman -S librsvg' failed — install it manually."
    else
        echo "Warning: no supported package manager found — install librsvg manually"
        echo "(provides rsvg-convert): https://gitlab.gnome.org/GNOME/librsvg"
    fi
fi

python3 -m venv .venv
source .venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt

# data/reference_corpus_<lang>.tar.xz (optional, one archive per language) —
# a pre-built snapshot of the CAPPED reference corpus (data/reference_
# corpus/<lang>_sentences.txt, at most build_sentence_corpus.MAX_SENTENCES_
# PER_LANGUAGE sentences — see that script), so a fresh clone gets backend/
# example_sentences.py's own LLM-grounding lookups working without any OPUS
# download at all. Split per language (rather than one combined archive) so
# each file stays under GitHub's 100MB hard file-size limit. Each language
# is unpacked independently if its archive is present; a language with no
# archive just has no example-sentence grounding for that language until
# build_sentence_corpus.py is run for it. NOT sufficient to regenerate a
# language's data/wordlist_<lang>_full.tsv from scratch, though — that
# needs the FULL, uncapped corpus (<lang>_sentences_full.txt), never
# published here (see build_sentence_corpus.py/build_wordlist_freq.py's own
# docstrings for why) — but data/wordlist_<lang>_full.tsv is itself already
# checked into the repo, so a fresh clone never needs to rebuild it just to
# use the app; only actually regenerating it (e.g. after a pipeline change)
# needs the full corpus, via build_sentence_corpus.py from scratch.
found_corpus_archive=0
for lang in fr en de es it; do
    archive="data/reference_corpus_${lang}.tar.xz"
    if [ -f "$archive" ]; then
        echo "Extracting $archive..."
        tar -xJf "$archive" -C data
        found_corpus_archive=1
    fi
done
if [ "$found_corpus_archive" -eq 0 ]; then
    echo "No data/reference_corpus_<lang>.tar.xz archives found — skipping"
    echo "(optional; run build_sentence_corpus.py per language to build the"
    echo "reference corpus from scratch)."
fi

# Première initialisation du panneau "Actu Croisée" (flux RSS + grilles
# scrappées), à la demande explicite de l'utilisateur : "Lors de
# l'installation sur une nouvelle machine, il faudra automatiquement
# initialiser une première fois les RSS et SCRAPP si ils n'existent pas
# encore." Sans ça, une machine fraîchement installée montrerait un
# panneau vide (voir backend/app.py's GET /api/rss et /api/scrapp,
# tous deux dégradant gracieusement vers une liste vide si le fichier
# combined.json n'existe pas) jusqu'au premier passage du planificateur
# quotidien (8h, voir _rss_daily_scheduler dans backend/app.py) — ce qui
# peut représenter jusqu'à 24h d'attente selon l'heure de l'installation.
# Chaque fichier est vérifié indépendamment (jamais réécrit s'il existe
# déjà, y compris sur une réinstallation) et l'échec de l'un ne bloque
# jamais l'autre ni le reste de l'installation — un problème réseau
# ponctuel ici ne doit pas empêcher l'installation d'aboutir ; le
# planificateur quotidien réessaiera de toute façon le lendemain.
if [ ! -f RSS/combined.json ]; then
    echo "Initialisation du flux RSS (première fois)..."
    python3 -c "import fetch_rss_feeds; fetch_rss_feeds.fetch_all()" \
        || echo "Warning: echec de l'initialisation du flux RSS — le planificateur quotidien reessaiera demain."
fi
if [ ! -f SCRAPP/combined.json ]; then
    echo "Initialisation des grilles scrappees (SCRAPP, premiere fois)..."
    python3 -c "import fetch_grid_links; fetch_grid_links.fetch_all()" \
        || echo "Warning: echec de l'initialisation de SCRAPP — le planificateur quotidien reessaiera demain."
fi

echo "Install complete. Activate the venv with: source .venv/bin/activate"
echo "Start the app: ./run_Falcon.sh"
echo
echo "Optional: to run the default local LLM that generates crossword clues,"
echo "run: pip install -r requirements-llama.txt && ./run_llm.sh"

# SGLang : Install.sh installe et configure désormais réellement le
# meilleur moteur/modèle disponible sur cette machine, à la demande
# explicite de l'utilisateur — corrigeant un comportement report-only
# jugé insuffisant : "Sur une machine déjà installée, Install.sh
# n'installe pas SGLang avec le modèle prévu." + "Install.sh doit
# considérer qu'il reconfigure la machine, donc installer la meilleure
# option." Deux étapes désormais : (1) installer SGLang lui-même si
# absent et que le matériel le permet (jamais réinstallé si déjà
# fonctionnel — voir sglang_already_working ci-dessous) ; (2) reconfigurer
# env.sh pour pointer vers SGLang + le modèle le plus adapté au matériel
# détecté, à CHAQUE exécution (pas seulement à la première) — via un
# bloc balisé (SGLANG_MARKER_BEGIN/END) que ce script retire puis
# réécrit intégralement à chaque fois, sans jamais toucher au reste du
# fichier (le port, une éventuelle clé API personnalisée, etc.).
#
# Modèles choisis pour chaque matériel : Apple Silicon → mlx-community/
# Qwen3-4B-4bit (le meilleur compromis vitesse/qualité réellement mesuré
# en direct sur ce projet, voir CLAUDE.md — Qwen3.5/Qwen3.8 crashent sur
# ce backend quel que soit le format, Qwen3-14B fonctionne mais est
# nettement plus lent que 4B sans gain de qualité mesuré suffisant).
# NVIDIA/CUDA → unsloth/Qwen3.8-27B-GGUF (la meilleure qualité de
# définition observée dans l'historique de ce projet via llama.cpp,
# GGUF directement supporté par le vrai chemin CUDA de SGLang) — jamais
# testé en direct sur cette machine (pas de GPU NVIDIA disponible ici),
# disclosed honnêtement plutôt que présenté comme vérifié.
#
# Aucun GPU détecté : llama.cpp (déjà la configuration par défaut
# d'env_default.sh) reste la meilleure — et seule — option viable ; ce
# script ne touche alors à rien.
SGLANG_VENV=".venv-sglang"
SGLANG_SRC="sglang-src"
SGLANG_REPO_URL="https://github.com/sgl-project/sglang.git"
SGLANG_MARKER_BEGIN="# BEGIN SGLANG AUTOCONFIG (gere par Install.sh — modifiez en dehors de ce bloc, jamais a l'interieur)"
SGLANG_MARKER_END="# END SGLANG AUTOCONFIG"

sglang_already_working() {
    [ -x "$SGLANG_VENV/bin/python3" ] && "$SGLANG_VENV/bin/python3" -c "import sglang" >/dev/null 2>&1
}

IS_APPLE_SILICON=false
HAS_NVIDIA_GPU=false
if [ "$(uname -s)" = "Darwin" ] && [ "$(uname -m)" = "arm64" ]; then
    IS_APPLE_SILICON=true
elif command -v nvidia-smi >/dev/null 2>&1 && nvidia-smi -L >/dev/null 2>&1; then
    HAS_NVIDIA_GPU=true
fi

echo
echo "Configuration du moteur LLM local (llama.cpp est le repli sur tout matériel ;"
echo "SGLang, plus rapide, est installé/activé automatiquement si le matériel le permet) :"

sglang_ready=false
if [ "$IS_APPLE_SILICON" = true ] || [ "$HAS_NVIDIA_GPU" = true ]; then
    if sglang_already_working; then
        echo "  SGLang deja installe et fonctionnel ($SGLANG_VENV) — pas de reinstallation."
        sglang_ready=true
    else
        echo "  Installation de SGLang (peut prendre plusieurs minutes)..."
        if [ "$IS_APPLE_SILICON" = true ]; then
            # Apple Silicon / MLX : nécessite Python 3.12 spécifiquement
            # (voir run_sglang.sh) — un venv dédié, distinct de .venv
            # (Python 3.14). Installation depuis un clone source (jamais
            # PyPI) : verifié en direct lors de la mise en place initiale
            # de cette fonctionnalité que le support MLX/MPS vit
            # directement dans le pyproject.toml principal du dépôt
            # (l'extra "all_mps"), plus besoin d'échanger un fichier
            # pyproject variante comme au tout début de ce projet — cette
            # étape a été retirée en conséquence.
            PY312=""
            if command -v python3.12 >/dev/null 2>&1; then
                PY312="python3.12"
            elif command -v brew >/dev/null 2>&1; then
                brew install python@3.12 || true
                if command -v python3.12 >/dev/null 2>&1; then
                    PY312="python3.12"
                fi
            fi
            if [ -z "$PY312" ]; then
                echo "  Warning: Python 3.12 introuvable (et Homebrew absent ou l'installation a"
                echo "  echoue) — SGLang non installe, llama.cpp reste actif."
            else
                "$PY312" -m venv "$SGLANG_VENV"
                if [ ! -d "$SGLANG_SRC" ]; then
                    git clone --depth 1 "$SGLANG_REPO_URL" "$SGLANG_SRC"
                fi
                if (cd "$SGLANG_SRC/python" && SGLANG_BUILD_RUST_EXTS=none "$OLDPWD/$SGLANG_VENV/bin/pip" install -e ".[all_mps]"); then
                    sglang_ready=true
                else
                    echo "  Warning: installation de SGLang (MLX) echouee — llama.cpp reste actif."
                fi
            fi
        elif [ "$HAS_NVIDIA_GPU" = true ]; then
            # CUDA : installation directe depuis PyPI (jamais besoin de
            # cloner le depot pour ce chemin), per la documentation
            # officielle de SGLang verifiee en direct au moment d'ecrire
            # cette fonctionnalite (docs.sglang.io) — --prerelease=allow
            # est necessaire car certaines dependances ne publient que
            # des pre-releases sur PyPI.
            python3 -m venv "$SGLANG_VENV"
            "$SGLANG_VENV/bin/pip" install --upgrade pip uv
            if "$SGLANG_VENV/bin/uv" pip install --python "$SGLANG_VENV/bin/python3" --prerelease=allow sglang; then
                sglang_ready=true
            else
                echo "  Warning: installation de SGLang (CUDA) echouee — llama.cpp reste actif."
            fi
        fi
    fi
else
    echo "  Aucun GPU (Apple Silicon ou NVIDIA) detecte — SGLang n'a pas de chemin"
    echo "  CPU-only pertinent ici ; llama.cpp reste la seule option viable."
fi

# Reconfigure env.sh pour utiliser SGLang si l'installation ci-dessus a
# reussi (ou etait deja en place) — cree env.sh depuis env_default.sh
# s'il n'existe pas encore, puis retire un eventuel bloc balise existant
# (idempotent — une reexecution de ce script ne duplique jamais le bloc)
# avant d'en ecrire un nouveau reflet du matériel actuellement détecté.
if [ ! -f env.sh ]; then
    cp env_default.sh env.sh
fi
if grep -qF "$SGLANG_MARKER_BEGIN" env.sh 2>/dev/null; then
    awk -v begin="$SGLANG_MARKER_BEGIN" -v end="$SGLANG_MARKER_END" '
        $0 == begin { skip = 1; next }
        $0 == end { skip = 0; next }
        !skip { print }
    ' env.sh > env.sh.new && mv env.sh.new env.sh
fi

if [ "$sglang_ready" = true ]; then
    if [ "$IS_APPLE_SILICON" = true ]; then
        SGLANG_MODEL="mlx-community/Qwen3-4B-4bit"
        {
            echo ""
            echo "$SGLANG_MARKER_BEGIN"
            echo "export LLM_ENGINE=\"sglang\""
            echo "export SGLANG_MODEL_PATH=\"$SGLANG_MODEL\""
            echo "export LLM_MODEL=\"$SGLANG_MODEL\""
            echo "export LLM_BASE_URL=\"http://127.0.0.1:\${LLM_PORT}/v1/chat/completions\""
            echo "export LLM_API_KEY=\"EMPTY\""
            echo "export SGLANG_CHAT_TEMPLATE_KWARGS='{\"enable_thinking\":false}'"
            echo "$SGLANG_MARKER_END"
        } >> env.sh
    else
        SGLANG_MODEL="unsloth/Qwen3.8-27B-GGUF"
        {
            echo ""
            echo "$SGLANG_MARKER_BEGIN"
            echo "export LLM_ENGINE=\"sglang\""
            echo "export SGLANG_MODEL_PATH=\"$SGLANG_MODEL\""
            echo "export SGLANG_QUANTIZATION=\"gguf\""
            echo "export LLM_MODEL=\"$SGLANG_MODEL\""
            echo "export LLM_BASE_URL=\"http://127.0.0.1:\${LLM_PORT}/v1/chat/completions\""
            echo "export LLM_API_KEY=\"EMPTY\""
            echo "$SGLANG_MARKER_END"
        } >> env.sh
    fi
    echo "  env.sh reconfigure : moteur=sglang, modele=$SGLANG_MODEL"

    # "Install.sh doit tuer le llm en cours de fonctionnement si il
    # reconfigure un autre" — arrête tout serveur LLM déjà démarré sur le
    # port configuré, pour qu'un ./run_llm.sh ultérieur reparte bien sur
    # la config qu'on vient d'écrire plutôt que de laisser tourner
    # l'ancien serveur (potentiellement un autre modèle/moteur) à côté.
    LLM_PORT_FOR_KILL="3002"
    if [ -f env.sh ]; then
        # shellcheck disable=SC1091
        LLM_PORT_FOR_KILL="$(source env.sh >/dev/null 2>&1; echo "${LLM_PORT:-3002}")"
    fi
    llm_pids="$(lsof -ti tcp:"$LLM_PORT_FOR_KILL" 2>/dev/null || true)"
    if [ -n "$llm_pids" ]; then
        echo "  Arret du serveur LLM en cours (port $LLM_PORT_FOR_KILL) — la reconfiguration necessite un redemarrage."
        kill $llm_pids 2>/dev/null || true
        sleep 1
        llm_pids="$(lsof -ti tcp:"$LLM_PORT_FOR_KILL" 2>/dev/null || true)"
        [ -n "$llm_pids" ] && kill -9 $llm_pids 2>/dev/null || true
    fi
    echo "  Lancez (ou relancez) le serveur LLM avec : ./run_llm.sh"
fi
