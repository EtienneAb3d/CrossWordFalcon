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

# Hardware detection for the alternative SGLang engine (see run_sglang.sh/
# env_default.sh's own commented-out block), at the user's explicit
# request: "Détecter les possibilités de la machine dans le Install.sh
# pour configurer le env.sh au mieux." Deliberately report-only — this
# never installs SGLang itself (a separate, one-time, heavier install:
# its own Python 3.12 venv, an editable install from a cloned source
# checkout — see run_sglang.sh's own header for why), and never
# overwrites an existing env.sh (a personal, gitignored file this script
# has otherwise never touched) — it only tells the user which SGLang
# path (if any) is realistically usable on this machine, matching this
# project's own default llama.cpp path staying the one that always
# works regardless of what's detected here.
echo
echo "Local LLM engine detection (llama.cpp above is always the safe default;"
echo "SGLang is a faster, opt-in alternative — see env_default.sh):"
if [ "$(uname -s)" = "Darwin" ] && [ "$(uname -m)" = "arm64" ]; then
    echo "  Apple Silicon detected — SGLang's native MLX backend is usable here"
    echo "  (verified live on this project's own dev machine). See run_sglang.sh's"
    echo "  own header and env_default.sh's commented Apple Silicon block to set"
    echo "  it up (a one-time install: Python 3.12 + a dedicated .venv-sglang/ +"
    echo "  an editable install from a cloned sglang-src/ checkout)."
elif command -v nvidia-smi >/dev/null 2>&1 && nvidia-smi -L >/dev/null 2>&1; then
    echo "  NVIDIA GPU detected — SGLang's CUDA path (with direct GGUF support)"
    echo "  is likely usable here (not directly tested by this project itself, no"
    echo "  CUDA hardware available on its own dev machine). See run_sglang.sh's"
    echo "  own header and env_default.sh's commented CUDA block to set it up."
else
    echo "  No Apple Silicon or NVIDIA GPU detected — SGLang isn't a good fit here"
    echo "  (it has no meaningful CPU-only path); stick with llama.cpp above."
fi
