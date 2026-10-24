#!/usr/bin/env bash
# One-shot: build every data artefact for the English
# language, in dependency order. Long-running (OPUS partial downloads +
# Hunspell filtering can take a couple of hours). Writes progress to
# logs/build_en.log. Safe to re-run: every build_*.py step reuses its
# own on-disk caches (CORPUS/, data/wiktionary/, data/hunspell_cache/).
#
# hunspell comes from the system package manager (Install.sh) or from a
# rootless build under ~/.local, which PATH/LD_LIBRARY_PATH also cover.
set -u
cd "$(dirname "${BASH_SOURCE[0]}")/.."
export PATH="$HOME/.local/bin:$PATH"
export LD_LIBRARY_PATH="$HOME/.local/lib:${LD_LIBRARY_PATH:-}"
PY=.venv/bin/python
LANG_CODE=en

step() { echo; echo "=== $(date '+%F %T')  $*"; }

step "1/11  build_sentence_corpus $LANG_CODE"
$PY data_builder/build_sentence_corpus.py "$LANG_CODE" || { echo "FAILED at build_sentence_corpus"; exit 1; }

step "2/11  build_wordlist_freq $LANG_CODE"
$PY data_builder/build_wordlist_freq.py "$LANG_CODE" || { echo "FAILED at build_wordlist_freq"; exit 1; }

step "3/11  build_unaccented_lemmas $LANG_CODE"
$PY data_builder/build_unaccented_lemmas.py "$LANG_CODE" || { echo "FAILED at build_unaccented_lemmas"; exit 1; }

step "4/11  build_wordlist_scrabble $LANG_CODE"
$PY data_builder/download_scrabble_dictionaries.py "$LANG_CODE" || { echo "FAILED at download_scrabble_dictionaries"; exit 1; }
$PY data_builder/build_wordlist_scrabble.py "$LANG_CODE" || { echo "FAILED at build_wordlist_scrabble"; exit 1; }

step "5/11  build_wordlist_hunspell $LANG_CODE"
$PY data_builder/build_wordlist_hunspell.py "$LANG_CODE" || { echo "FAILED at build_wordlist_hunspell"; exit 1; }

step "6/11  build_wordlist_multi $LANG_CODE"
$PY data_builder/build_wordlist_multi.py "$LANG_CODE" || { echo "FAILED at build_wordlist_multi"; exit 1; }
$PY data_builder/build_wordlist_freq_multi.py "$LANG_CODE" || { echo "FAILED at build_wordlist_freq_multi"; exit 1; }

step "7/11  build_gloss_dictionary $LANG_CODE"
$PY data_builder/build_gloss_dictionary.py "$LANG_CODE" || { echo "FAILED at build_gloss_dictionary"; exit 1; }
$PY data_builder/build_gloss_dictionary.py "$LANG_CODE" --multi || { echo "FAILED at build_gloss_dictionary --multi"; exit 1; }

step "8/11  compress_reference_corpus $LANG_CODE"
$PY data_builder/compress_reference_corpus.py "$LANG_CODE" || { echo "FAILED at compress_reference_corpus"; exit 1; }

step "9/11  build_inflections $LANG_CODE"
$PY data_builder/build_inflections.py "$LANG_CODE" || { echo "FAILED at build_inflections"; exit 1; }
$PY data_builder/build_inflections.py "$LANG_CODE" --multi || { echo "FAILED at build_inflections --multi"; exit 1; }

step "10/11  build_usage_scores $LANG_CODE  (best effort — needs the local LLM, ./run_llm.sh; resumable, many hours)"
# Non-fatal: the usage scores refine word choice and grid scoring, but every
# other artefact works without them. Interrupted, it resumes where it stopped.
$PY data_builder/build_usage_scores.py "$LANG_CODE" \
    || echo "WARNING: build_usage_scores skipped/failed — start the LLM (./run_llm.sh), then: $PY data_builder/build_usage_scores.py $LANG_CODE"
$PY data_builder/build_usage_scores.py "$LANG_CODE" --source out \
    || echo "WARNING: build_usage_scores --source out skipped/failed — start the LLM (./run_llm.sh), then: $PY data_builder/build_usage_scores.py $LANG_CODE --source out"

step "11/11  qdrant_populate $LANG_CODE  (words + words_multi; best effort — needs ./run_qdrant.sh + ./run_embed.sh running)"
# Non-fatal: the wordlist/gloss artefacts above are the real deliverables; feeding
# the Qdrant "words" and "words_multi" collections is a downstream nicety.
# --recreate wipes this language's tenant first, since the wordlist it mirrors
# was just rebuilt.
$PY -m data_builder.qdrant_populate "$LANG_CODE" --recreate \
    || echo "WARNING: qdrant_populate skipped/failed — start Qdrant (./run_qdrant.sh) and the embed server (./run_embed.sh), then: $PY -m data_builder.qdrant_populate $LANG_CODE --recreate"
$PY -m data_builder.qdrant_populate "$LANG_CODE" --multi --recreate \
    || echo "WARNING: qdrant_populate --multi skipped/failed — start Qdrant (./run_qdrant.sh) and the embed server (./run_embed.sh), then: $PY -m data_builder.qdrant_populate $LANG_CODE --multi --recreate"

step "DONE — English data pipeline complete"
ls -la data/wordlist_en_freq.tsv data/wordlist_en_scrabble.tsv data/wordlist_en_hunspell.tsv data/wordlist_en_multi.tsv data/wordlist_en_freq_multi.tsv data/gloss_dictionary/en_glosses_multi.jsonl data/inflection/en_inflect_multi.jsonl data/gloss_dictionary/en_glosses.jsonl \
       data/reference_corpus_en.tar.xz data/reference_corpus/en_sentences.txt data/inflection/en_inflect.jsonl data/usage_score_en_scrabble.tsv data/usage_score_en_scrabble_out.tsv 2>&1
