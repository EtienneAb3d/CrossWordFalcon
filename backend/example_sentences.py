#!/usr/bin/env python3
"""
Looks up real usage examples of a word's exact inflected form in the
per-language reference sentence corpus built by build_sentence_corpus.py
(data/reference_corpus/<lang>_sentences.txt) — used by
backend/clues.py to ground the clue-writing prompt with genuine context
for rare/ambiguous words, instead of relying solely on the LLM's own
(sometimes wrong) sense of what a word means. See the French "ARE" case
in the project-best-practices SKILL for why this exists.

Indexed once per language, not once per request: a single streaming pass
over the corpus (a few million lines) tokenizes every line and checks
each token against the language's *entire* wordlist (data/wordlist_<lang>
_full.tsv, ~35-45k words) — checking membership in a large target set
costs the same per line as checking a small one (both are O(1) hash
lookups per token), so indexing the whole lexicon up front is barely
slower than indexing just one grid's ~30-50 words would have been, and
every later lookup (any word, any future grid, same language) is then a
plain cached-dict read instead of a multi-second rescan. Paid once per
language, lazily, the first time that language is needed in this
process's lifetime (backend/app.py runs as one long-lived process — see
run_Falcon.sh — so this cost is amortized across every grid generated
after the first one in a given language).
"""
import random
import re
from pathlib import Path

CORPUS_DIR = Path(__file__).resolve().parent.parent / "data" / "reference_corpus"
DATA_DIR = Path(__file__).resolve().parent.parent / "data"
_WORD_RE = re.compile(r"[^\W\d_]+", re.UNICODE)

DEFAULT_LIMIT = 5
# Per-word cap while building the index — bounds memory across an entire
# lexicon's worth of words (tens of thousands) without biasing which
# matches are kept (reservoir sampling / "Algorithm R": a uniform sample
# over every match seen so far, not just the first ones encountered).
RESERVOIR_SIZE = 20

_index_cache = {}  # language -> {word_lower: [sentence, ...]}


def _load_wordlist_words(language):
    """The wordlist's ACCENTED column (2nd of 4 — build_wordlist_freq.py's
    natural, accented/inflected spelling), not its bare MOT column (1st —
    accent-stripped, uppercase, the grid's own form). A real, previously
    unnoticed bug: reading MOT here meant `targets` only ever held accent-
    stripped forms ("elu"), while _build_index's corpus scan tokenizes and
    lowercases actual corpus text without stripping accents ("élu") — the
    two could only ever agree for words with no diacritics to begin with,
    so every genuinely accented word (a large fraction of French/Spanish/
    Italian/German vocabulary) silently never matched, no error, just an
    empty examples section on every LLM call for that word. Caught only
    because the user noticed a specific word's ("élu") missing example-
    sentence section in a real failure log despite the corpus visibly
    containing it — the same class of silent gap as the CORPUS_DIR
    mix-up documented in CLAUDE.md's history for this file."""
    path = DATA_DIR / f"wordlist_{language}_full.tsv"
    words = set()
    with open(path, encoding="utf-8") as f:
        for line in f:
            parts = line.split("\t")
            if len(parts) >= 2:
                accented = parts[1].strip()
                if accented:
                    words.add(accented.lower())
    return words


def _build_index(language):
    corpus_path = CORPUS_DIR / f"{language}_sentences.txt"
    if not corpus_path.exists():
        return {}
    targets = _load_wordlist_words(language)
    reservoirs = {}
    seen_counts = {}

    with open(corpus_path, encoding="utf-8") as f:
        for line in f:
            line = line.rstrip("\n")
            if not line:
                continue
            for token in _WORD_RE.findall(line):
                w = token.lower()
                if w not in targets:
                    continue
                seen_counts[w] = seen_counts.get(w, 0) + 1
                reservoir = reservoirs.setdefault(w, [])
                if len(reservoir) < RESERVOIR_SIZE:
                    reservoir.append(line)
                else:
                    j = random.randint(0, seen_counts[w] - 1)
                    if j < RESERVOIR_SIZE:
                        reservoir[j] = line
    return reservoirs


def _get_index(language):
    if language not in _index_cache:
        _index_cache[language] = _build_index(language)
    return _index_cache[language]


def find_examples_for_words(words, language, limit=DEFAULT_LIMIT):
    """`words` is an iterable of exact inflected forms (natural spelling)
    to find real usage examples for. Returns {word: [sentence, ...]} — up
    to `limit` sentences per word, drawn at random from every match found
    in `language`'s reference corpus, matched case-insensitively as a
    whole token. A word with no matches is simply absent from the result
    — callers should treat that as "no examples available", not an
    error. Returns {} outright if this language has no corpus built (see
    build_sentence_corpus.py)."""
    index = _get_index(language)
    result = {}
    for w in words:
        reservoir = index.get(w.lower())
        if reservoir:
            result[w] = random.sample(reservoir, min(limit, len(reservoir)))
    return result
