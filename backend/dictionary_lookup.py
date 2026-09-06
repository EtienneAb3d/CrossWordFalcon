#!/usr/bin/env python3
"""Accent- and case-insensitive dictionary search for the web UI's
"Dictionnaire" panel, at the user's explicit request: given a word, list
every word of the same root (same canonical form) from
`data/wordlist_<lang>_full.tsv`, each with its real definitions pulled
from `data/gloss_dictionary/<lang>_glosses.jsonl`.

Distinct from `backend/gloss_lookup.py` (which only ever looks a single
known lemma up, for clue grounding): this walks the whole wordlist to
resolve a free-typed query to its root family, then joins that family
against the gloss dictionary. The per-language index is built once, lazily,
and cached — the fr wordlist is ~200k lines.
"""
import unicodedata
from pathlib import Path

from .gloss_lookup import _load as _load_gloss_index

WORDLIST_DIR = Path(__file__).resolve().parent.parent / "data"

# Never return an unbounded family (a query like a single common letter, or
# a very productive root, could otherwise match thousands of inflected
# forms and flood the panel).
MAX_ROWS = 300

_index_cache = {}  # language -> _LangIndex | None


def _norm(text):
    """Accent-stripped, ASCII-folded, case-folded, trimmed — the single
    normalization used for every key and every query, so "Écrit", "ecrit"
    and "  ÉCRIT " all collapse to the same lookup key."""
    decomposed = unicodedata.normalize("NFKD", text)
    ascii_only = decomposed.encode("ascii", "ignore").decode("ascii")
    return ascii_only.casefold().strip()


class _LangIndex:
    __slots__ = ("rows", "by_key", "by_canon")

    def __init__(self):
        # rows[i] = (accented_form, (canonical, ...))
        self.rows = []
        # normalized(MOT) / normalized(accented) -> [row index, ...]
        self.by_key = {}
        # normalized(canonical) -> {row index, ...}  (the whole root family)
        self.by_canon = {}


def _build_index(language):
    path = WORDLIST_DIR / f"wordlist_{language}_full.tsv"
    if not path.exists():
        return None
    idx = _LangIndex()
    try:
        with open(path, encoding="utf-8") as f:
            for line in f:
                stripped = line.rstrip("\n")
                if not stripped or stripped.startswith("#"):
                    continue
                parts = stripped.split("\t")
                if len(parts) < 2:
                    continue
                mot = parts[0]
                accented = parts[1] or mot
                canon_field = parts[3] if len(parts) >= 4 and parts[3] else accented
                canonicals = tuple(c for c in (p.strip() for p in canon_field.split(";")) if c)
                if not canonicals:
                    canonicals = (accented,)
                row_i = len(idx.rows)
                idx.rows.append((accented, canonicals))
                for key in {_norm(mot), _norm(accented)}:
                    if key:
                        idx.by_key.setdefault(key, []).append(row_i)
                for c in canonicals:
                    ck = _norm(c)
                    if ck:
                        idx.by_canon.setdefault(ck, set()).add(row_i)
    except OSError:
        return None
    return idx


def _get_index(language):
    if language not in _index_cache:
        _index_cache[language] = _build_index(language)
    return _index_cache[language]


def _definitions_for(canonicals, gloss_index, show_lemma):
    """[{"pos": ..., "gloss": ..., "lemma": ...}] for every gloss of every
    canonical form, de-duplicated. `lemma` is only filled when the row has
    more than one canonical form (so the reader can tell which root a given
    definition belongs to); otherwise it's "" and the frontend omits it."""
    out = []
    seen = set()
    for lemma in canonicals:
        entry = gloss_index.get(lemma.lower())
        if not entry:
            continue
        for sense in entry.get("entries", []):
            pos = sense.get("pos") or ""
            for gloss in sense.get("glosses", []):
                key = (lemma.lower(), pos, gloss)
                if key in seen:
                    continue
                seen.add(key)
                out.append({
                    "pos": pos,
                    "gloss": gloss,
                    "lemma": lemma if show_lemma else "",
                })
    return out


def search(query, language):
    """Returns {"query", "language", "rows": [...], "truncated": bool}.

    A row is {"form", "canonical", "definitions": [{"pos","gloss","lemma"}]}
    — `form` is the natural (accented) spelling, `canonical` is its
    canonical form(s) joined by "; ", `definitions` may be empty for an
    inflected form whose lemma simply has no Wiktionary coverage.

    Match is accent- and case-insensitive on the bare form, the accented
    form, and the canonical form; the result is the whole root family
    (every wordlist entry sharing any canonical with the query's own
    match), sorted with the canonical/searched forms first."""
    q = (query or "").strip()
    result = {"query": q, "language": language, "rows": [], "truncated": False}
    if not q:
        return result
    idx = _get_index(language)
    if idx is None:
        return result
    gloss_index = _load_gloss_index(language)
    qn = _norm(q)

    # Resolve the query to its root(s). Two ways in:
    #  - the query IS a canonical form (searched a lemma directly);
    #  - the query is a bare/accented inflected form of some wordlist
    #    row(s) — take that row's own canonical(s) (all of them if it has
    #    several, e.g. French "suis" -> "suivre"/"être").
    # Deliberately NOT expanded through rows that merely *share* a root
    # with the query: French "chatte" carries both "chat" and "chatter"
    # as lemmas, and searching "chat" should not drag in the entire
    # "chatter" conjugation just because one shared member happens to
    # link the two.
    roots = set()
    if qn in idx.by_canon:
        roots.add(qn)
    for i in idx.by_key.get(qn, []):
        for c in idx.rows[i][1]:
            roots.add(_norm(c))
    # A query that only exists in the gloss dictionary (a lemma with no
    # wordlist row of its own) still resolves to a root so its own
    # definitions show up.
    if not roots and qn in {_norm(w) for w in gloss_index}:
        roots.add(qn)

    if not roots:
        return result

    family = set()
    for r in roots:
        family |= idx.by_canon.get(r, set())

    rows = []
    for i in sorted(family):
        form, canonicals = idx.rows[i]
        show_lemma = len(canonicals) > 1
        rows.append({
            "form": form,
            "canonical": "; ".join(canonicals),
            "_canon_norm": {_norm(c) for c in canonicals},
            "definitions": _definitions_for(canonicals, gloss_index, show_lemma),
        })
    # If the query itself is a lemma with no wordlist row at all, add a
    # synthetic entry for it so the reader still sees its definitions.
    if qn in roots and not any(qn in row["_canon_norm"] and _norm(row["form"]) == qn for row in rows):
        for w in gloss_index:
            if _norm(w) == qn:
                rows.append({
                    "form": w,
                    "canonical": w,
                    "_canon_norm": {qn},
                    "definitions": _definitions_for([w], gloss_index, False),
                })
                break

    # Canonical/searched forms first, then alphabetical by folded form.
    def sort_key(row):
        fn = _norm(row["form"])
        is_root = 0 if fn in roots else 1
        is_query = 0 if fn == qn else 1
        return (is_query, is_root, fn)

    rows.sort(key=sort_key)
    if len(rows) > MAX_ROWS:
        rows = rows[:MAX_ROWS]
        result["truncated"] = True
    for row in rows:
        row.pop("_canon_norm", None)
    result["rows"] = rows
    return result
