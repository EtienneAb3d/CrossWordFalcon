#!/usr/bin/env bash
# One-shot: build every data artefact for the newly-added Portuguese
# language, in dependency order. Long-running (OPUS partial downloads +
# Hunspell filtering can take a couple of hours). Writes progress to
# logs/build_pt.log. Safe to re-run: every build_*.py step reuses its
# own on-disk caches (CORPUS/, DICS/, data/hunspell_cache/).
#
# hunspell is not a system package on this host — a rootless build was
# installed under ~/.local, so PATH/LD_LIBRARY_PATH must point at it.
set -u
cd "$(dirname "${BASH_SOURCE[0]}")/.."
export PATH="$HOME/.local/bin:$PATH"
export LD_LIBRARY_PATH="$HOME/.local/lib:${LD_LIBRARY_PATH:-}"
PY=.venv/bin/python
LANG_CODE=pt

step() { echo; echo "=== $(date '+%F %T')  $*"; }

step "1/4  build_sentence_corpus $LANG_CODE"
$PY data_builder/build_sentence_corpus.py "$LANG_CODE" || { echo "FAILED at build_sentence_corpus"; exit 1; }

step "2/4  build_wordlist_freq $LANG_CODE"
$PY data_builder/build_wordlist_freq.py "$LANG_CODE" || { echo "FAILED at build_wordlist_freq"; exit 1; }

step "3/4  build_gloss_dictionary $LANG_CODE"
$PY data_builder/build_gloss_dictionary.py "$LANG_CODE" || { echo "FAILED at build_gloss_dictionary"; exit 1; }

step "4/4  compress_reference_corpus $LANG_CODE"
$PY data_builder/compress_reference_corpus.py "$LANG_CODE" || { echo "FAILED at compress_reference_corpus"; exit 1; }

step "DONE — Portuguese data pipeline complete"
ls -la data/wordlist_pt_full.tsv data/gloss_dictionary/pt_glosses.jsonl \
       data/reference_corpus_pt.tar.xz data/reference_corpus/pt_sentences.txt 2>&1
