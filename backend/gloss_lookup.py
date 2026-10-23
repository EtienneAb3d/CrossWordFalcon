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

The multi-word expressions' dictionary (`<lang>_glosses_multi.jsonl`,
`build_gloss_dictionary.py --multi`) is read with it, an entry of the
single-word one winning a shared key.
"""
import json
from collections.abc import Mapping
from pathlib import Path

from .text_lines import iter_lines_with_offsets, read_line_at

GLOSS_DIR = Path(__file__).resolve().parent.parent / "data" / "gloss_dictionary"

_cache = {}  # language -> _GlossIndex


# Lookup key: lowercase, ligatures folded ("Cœur" -> "coeur") — the
# dictionaries are written with folded ligatures (data_builder/
# build_wordlist_freq.py's `fold_ligatures`), so a query spelled with one
# still finds its entry.
_LIGATURE_KEY = str.maketrans({"œ": "oe", "æ": "ae"})


def _key(word):
    return word.lower().translate(_LIGATURE_KEY)


class _GlossIndex(Mapping):
    """{lemma key: entry} of one language's gloss dictionaries, holding
    only each entry's byte offset in its JSONL file (`parts`: one
    `(path, {key: offset})` per file, the first holding a key winning):
    the entry itself (`{"word", "entries": [{"pos", "glosses"}]}`) is read
    and parsed from disk when looked up."""

    def __init__(self, parts, name_only=frozenset()):
        self._parts = parts
        # Keys whose entry has only proper-noun senses (every `pos` "name").
        self.name_only = name_only

    def __getitem__(self, key):
        for path, offsets in self._parts:
            offset = offsets.get(key)
            if offset is not None:
                return json.loads(read_line_at(path, offset).strip())
        raise KeyError(key)

    def __contains__(self, key):
        return any(key in offsets for _path, offsets in self._parts)

    def __iter__(self):
        seen = set()
        for _path, offsets in self._parts:
            for key in offsets:
                if key not in seen:
                    seen.add(key)
                    yield key

    def __len__(self):
        return sum(1 for _ in self)


def _read_offsets(path, name_only):
    """{key: offset} of one JSONL file; adds to `name_only` the keys whose
    entry has only "name" senses and drops from it those whose entry does
    not (a later line of a key replaces an earlier one)."""
    offsets = {}
    if path.exists():
        for offset, line in iter_lines_with_offsets(path):
            line = line.strip()
            if not line:
                continue
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                continue
            key = _key(entry["word"])
            offsets[key] = offset
            senses = entry.get("entries") or []
            if senses and all(s.get("pos") == "name" for s in senses):
                name_only.add(key)
            else:
                name_only.discard(key)
    return offsets


def _load(language):
    if language not in _cache:
        parts = []
        name_only = set()
        for name in (f"{language}_glosses.jsonl", f"{language}_glosses_multi.jsonl"):
            path = GLOSS_DIR / name
            # A key of an earlier file wins, its name-only status too.
            earlier = set().union(*(offsets for _path, offsets in parts))
            file_name_only = set()
            offsets = _read_offsets(path, file_name_only)
            if offsets:
                parts.append((path, offsets))
                name_only.update(k for k in file_name_only if k not in earlier)
        _cache[language] = _GlossIndex(parts, frozenset(name_only))
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
        key = _key(lemma)
        if key in index:
            entry = index[key]
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
    return any(offsets for _path, offsets in _load(language)._parts)


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
    return any(_key(c) in index for c in candidates)


def only_proper_noun_glosses(candidates, language):
    """True if `candidates` (a word's spelling with its canonical forms)
    have at least one gloss-dictionary entry and every entry found has
    only proper-noun ("name") senses — no other part of speech possible.
    False when `language` has no gloss dictionary."""
    index = _load(language)
    found = [k for k in {_key(c) for c in candidates} if k in index]
    return bool(found) and all(k in index.name_only for k in found)
