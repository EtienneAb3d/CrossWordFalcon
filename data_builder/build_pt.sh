#!/usr/bin/env bash
# One-shot: build every data artefact for the newly-added Portuguese
# language, in dependency order. Long-running (OPUS partial downloads +
# Hunspell filtering can take a couple of hours). Writes progress to
# logs/build_pt.log. Safe to re-run: every build_*.py step reuses its
# own on-disk caches (CORPUS/, data/wiktionary/, data/hunspell_cache/).
#
# hunspell comes from the system package manager (Install.sh) or from a
# rootless build under ~/.local, which PATH/LD_LIBRARY_PATH also cover.
set -u
cd "$(dirname "${BASH_SOURCE[0]}")/.."
export PATH="$HOME/.local/bin:$PATH"
export LD_LIBRARY_PATH="$HOME/.local/lib:${LD_LIBRARY_PATH:-}"
PY=.venv/bin/python
LANG_CODE=pt

step() { echo; echo "=== $(date '+%F %T')  $*"; }

step "1/7  build_sentence_corpus $LANG_CODE"
$PY data_builder/build_sentence_corpus.py "$LANG_CODE" || { echo "FAILED at build_sentence_corpus"; exit 1; }

step "2/7  build_wordlist_freq $LANG_CODE"
$PY data_builder/build_wordlist_freq.py "$LANG_CODE" || { echo "FAILED at build_wordlist_freq"; exit 1; }

step "3/7  build_wordlist_scrabble $LANG_CODE"
$PY data_builder/download_scrabble_dictionaries.py "$LANG_CODE" || { echo "FAILED at download_scrabble_dictionaries"; exit 1; }
$PY data_builder/build_wordlist_scrabble.py "$LANG_CODE" || { echo "FAILED at build_wordlist_scrabble"; exit 1; }

step "4/7  build_gloss_dictionary $LANG_CODE"
$PY data_builder/build_gloss_dictionary.py "$LANG_CODE" || { echo "FAILED at build_gloss_dictionary"; exit 1; }

step "5/7  compress_reference_corpus $LANG_CODE"
$PY data_builder/compress_reference_corpus.py "$LANG_CODE" || { echo "FAILED at compress_reference_corpus"; exit 1; }

step "6/7  build_inflections $LANG_CODE"
$PY data_builder/build_inflections.py "$LANG_CODE" || { echo "FAILED at build_inflections"; exit 1; }

step "7/7  qdrant_populate $LANG_CODE  (best effort — needs ./run_qdrant.sh + ./run_embed.sh running)"
# Non-fatal: the wordlist/gloss artefacts above are the real deliverables; feeding
# the Qdrant "words" collection is a downstream nicety. --recreate wipes this
# language's tenant first, since the wordlist it mirrors was just rebuilt.
$PY -m data_builder.qdrant_populate "$LANG_CODE" --recreate \
    || echo "WARNING: qdrant_populate skipped/failed — start Qdrant (./run_qdrant.sh) and the embed server (./run_embed.sh), then: $PY -m data_builder.qdrant_populate $LANG_CODE --recreate"

step "DONE — Portuguese data pipeline complete"
ls -la data/wordlist_pt_freq.tsv data/wordlist_pt_scrabble.tsv data/gloss_dictionary/pt_glosses.jsonl \
       data/reference_corpus_pt.tar.xz data/reference_corpus/pt_sentences.txt data/inflection/pt.jsonl 2>&1
