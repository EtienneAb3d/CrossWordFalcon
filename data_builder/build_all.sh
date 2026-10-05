#!/usr/bin/env bash
# Rebuilds every data artefact of the six languages from scratch, one
# language after the other (data_builder/build_<lang>.sh — corpus, freq
# wordlist, lemmas of its unaccented spellings, Scrabble dictionary, gloss
# dictionary, corpus archive, inflection table, Qdrant tenant). Very long (several hours per language);
# each language's own log goes to logs/build_<lang>.log. Stops at the
# first language that fails.
set -u
cd "$(dirname "${BASH_SOURCE[0]}")/.."
mkdir -p logs
for lang in fr en de es it pt; do
    echo "=== $(date '+%F %T')  build_${lang}.sh (log: logs/build_${lang}.log)"
    bash "data_builder/build_${lang}.sh" > "logs/build_${lang}.log" 2>&1 \
        || { echo "FAILED: build_${lang}.sh — see logs/build_${lang}.log"; exit 1; }
done
echo "=== $(date '+%F %T')  all languages rebuilt"
