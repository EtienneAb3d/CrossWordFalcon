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

echo "Install complete. Activate the venv with: source .venv/bin/activate"
echo "Start the app: ./run_Falcon.sh"
echo
echo "Optional: to run the default local LLM that generates crossword clues,"
echo "run: pip install -r requirements-llama.txt && ./run_llm.sh"
