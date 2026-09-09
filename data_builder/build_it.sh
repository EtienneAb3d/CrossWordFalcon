#!/usr/bin/env bash
# One-shot: build every data artefact for the Italian
# language, in dependency order. Long-running (OPUS partial downloads +
# Hunspell filtering can take a couple of hours). Writes progress to
# logs/build_it.log. Safe to re-run: every build_*.py step reuses its
# own on-disk caches (CORPUS/, DICS/, data/hunspell_cache/).
#
# hunspell is not a system package on this host — a rootless build was
# installed under ~/.local, so PATH/LD_LIBRARY_PATH must point at it.
set -u
cd "$(dirname "${BASH_SOURCE[0]}")/.."
export PATH="$HOME/.local/bin:$PATH"
export LD_LIBRARY_PATH="$HOME/.local/lib:${LD_LIBRARY_PATH:-}"
PY=.venv/bin/python
LANG_CODE=it

step() { echo; echo "=== $(date '+%F %T')  $*"; }

step "1/6  build_sentence_corpus $LANG_CODE"
$PY data_builder/build_sentence_corpus.py "$LANG_CODE" || { echo "FAILED at build_sentence_corpus"; exit 1; }

step "2/6  build_wordlist_freq $LANG_CODE"
$PY data_builder/build_wordlist_freq.py "$LANG_CODE" || { echo "FAILED at build_wordlist_freq"; exit 1; }

step "3/6  build_gloss_dictionary $LANG_CODE"
$PY data_builder/build_gloss_dictionary.py "$LANG_CODE" || { echo "FAILED at build_gloss_dictionary"; exit 1; }

step "4/6  compress_reference_corpus $LANG_CODE"
$PY data_builder/compress_reference_corpus.py "$LANG_CODE" || { echo "FAILED at compress_reference_corpus"; exit 1; }

step "5/6  build_inflections $LANG_CODE"
$PY data_builder/build_inflections.py "$LANG_CODE" || { echo "FAILED at build_inflections"; exit 1; }

step "6/6  qdrant_populate $LANG_CODE  (best effort — needs ./run_qdrant.sh + ./run_embed.sh running)"
# Non-fatal: the wordlist/gloss artefacts above are the real deliverables; feeding
# the Qdrant "words" collection is a downstream nicety. --recreate wipes this
# language's tenant first, since the wordlist it mirrors was just rebuilt.
$PY -m data_builder.qdrant_populate "$LANG_CODE" --recreate \
    || echo "WARNING: qdrant_populate skipped/failed — start Qdrant (./run_qdrant.sh) and the embed server (./run_embed.sh), then: $PY -m data_builder.qdrant_populate $LANG_CODE --recreate"

step "DONE — Italian data pipeline complete"
ls -la data/wordlist_it_full.tsv data/gloss_dictionary/it_glosses.jsonl \
       data/reference_corpus_it.tar.xz data/reference_corpus/it_sentences.txt data/inflection/it.jsonl 2>&1
