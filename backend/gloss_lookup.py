#!/usr/bin/env python3
"""
Looks up real dictionary definitions for a word's canonical form(s) in the
per-language gloss dictionary built by build_gloss_dictionary.py
(data/gloss_dictionary/<lang>_glosses.jsonl, derived from Wiktionary via
Kaikki.org) — used by backend/clues.py to ground the clue-writing prompt
with an actual definition instead of relying solely on the LLM's own
(sometimes wrong) sense of what a word means. See the French "ARE" case in
the project-best-practices SKILL for why this exists.

Looks up by CANONICAL form (lemma), not by the grid's inflected spelling —
Wiktionary is itself indexed by lemma, so backend/crossword_gen.py's
`words[i]["canonical"]` (one or more candidate lemmas — build_wordlist_
freq.py keeps every one found for a genuinely ambiguous word, e.g. French
"suis" -> "être" or "suivre") is what this looks up, not the word as it
appears in the grid.
"""
import json
from pathlib import Path

GLOSS_DIR = Path(__file__).resolve().parent.parent / "data" / "gloss_dictionary"

_cache = {}  # language -> {lemma_lower: {"word": ..., "entries": [{"pos":..., "glosses":[...]}]}}


def _load(language):
    if language not in _cache:
        index = {}
        path = GLOSS_DIR / f"{language}_glosses.jsonl"
        if path.exists():
            with open(path, encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        entry = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    index[entry["word"].lower()] = entry
        _cache[language] = index
    return _cache[language]


def find_glosses_for_canonicals(canonical_forms, language):
    """`canonical_forms` is an iterable of lemma strings (e.g. a grid
    word's `canonical` list). Returns {lemma: [{"pos":..., "glosses":
    [...]}, ...]} for every lemma with an entry, matched case-
    insensitively. A lemma absent from the result simply has no
    Wiktionary coverage — not necessarily an error, callers should treat
    it as "no gloss available"."""
    index = _load(language)
    result = {}
    for lemma in canonical_forms:
        entry = index.get(lemma.lower())
        if entry:
            result[lemma] = entry["entries"]
    return result


def has_gloss_dictionary(language):
    """True if `language` actually has a built gloss dictionary (at least
    one entry) — as opposed to `has_any_gloss` returning False for a
    *specific* word because that word itself isn't covered. Distinguishing
    these matters: `backend/crossword_gen.py`'s `require_gloss` filter is
    meant to no-op entirely when a language has no gloss dictionary built
    at all (an optional, gitignored artifact from `build_gloss_
    dictionary.py` that a deploy can easily skip) rather than reject every
    single word, which is what unconditionally trusting `has_any_gloss`
    would do once every word "has no gloss" the same way."""
    return bool(_load(language))


def has_any_gloss(candidates, language):
    """True if any of `candidates` (e.g. a word's inflected spelling
    together with its candidate canonical form(s)) has an entry in
    `language`'s gloss dictionary. Used by
    `backend/crossword_gen.py`'s "easy" difficulty to require a word have
    a real, findable definition — frequency alone doesn't catch a word
    that's common yet has no usable definition (abbreviations, some
    proper nouns, e.g. French "ABD"). Returns False (not an error) if
    `language` has no gloss dictionary built yet."""
    index = _load(language)
    return any(c.lower() in index for c in candidates)
