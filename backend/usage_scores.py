#!/usr/bin/env python3
"""Usage score of a word (`data/usage_score_<lang>_scrabble.tsv` and, for the
freq words absent from the Scrabble wordlist, `data/usage_score_<lang>_
scrabble_out.tsv`, and for the multi-word expressions `data/usage_score_
<lang>_multi.tsv`, written by `data_builder/build_usage_scores.py`: 5
everyday, 4 formal/literary, 3 rare form of an everyday word, 2 technical, 1 dated, 0 practically unknown), for
the "Dictionnaire" panel (`backend/dictionary_lookup.py`). Each language's
files are read once, lazily, and cached for the process lifetime; a
language without either has no scores.
"""
import threading
from pathlib import Path

_DIR = Path(__file__).resolve().parent.parent / "data"

# Lowest usage score counted by the per-length table of `usage_score_counts`.
LENGTH_STATS_MIN_SCORE = 3

_cache = {}  # language -> {MOT: score} | None
_lock = threading.Lock()


def _load(language):
    with _lock:
        if language not in _cache:
            scores = None
            for name in (f"usage_score_{language}_scrabble.tsv",
                         f"usage_score_{language}_scrabble_out.tsv"):
                path = _DIR / name
                if not path.exists():
                    continue
                if scores is None:
                    scores = {}
                with open(path, encoding="utf-8") as f:
                    for line in f:
                        parts = line.rstrip("\n").split("\t")
                        if len(parts) >= 3 and parts[2].isdigit():
                            scores[parts[0].upper()] = int(parts[2])
            _cache[language] = scores
        return _cache[language]


_multi_cache = {}  # language -> {MOT: score} | None


def _load_multi(language):
    """{MOT: score} of `data/usage_score_<lang>_multi.tsv` (the multi-word
    expressions' usage scores), `None` when the file does not exist."""
    with _lock:
        if language not in _multi_cache:
            scores = None
            path = _DIR / f"usage_score_{language}_multi.tsv"
            if path.exists():
                scores = {}
                with open(path, encoding="utf-8") as f:
                    for line in f:
                        parts = line.rstrip("\n").split("\t")
                        if len(parts) >= 3 and parts[2].isdigit():
                            scores[parts[0].upper()] = int(parts[2])
            _multi_cache[language] = scores
        return _multi_cache[language]


def usage_score(mot, language, multi=False):
    """The usage score of the grid form `mot` (bare A-Z, as in the MOT
    column), `None` when unknown: from the single-word files, else the
    multi-word one (`_load_multi`) — the multi-word one first when `multi`
    (a spelling holding a space, hyphen or apostrophe)."""
    key = mot.upper()
    sources = (_load_multi, _load) if multi else (_load, _load_multi)
    for load in sources:
        scores = load(language)
        if scores is not None and key in scores:
            return scores[key]
    return None


def usage_score_counts(languages, lexicon_counts=None, multi_counts=None):
    """Number of rated words per usage score, for the localhost-only
    "Statistiques" panel (`GET /api/usage_stats`), over both files of each
    language: `{"scores": [every score found, ascending], "levels": [the
    difficulties of `lexicon_counts`], "languages": [{"language", "counts":
    [one count per score of "scores"], "total", "length_counts",
    "level_totals", "easy_length_counts"}], "lengths": [...]}`; `lengths`
    lists, longest first, every word length found among the words rated at
    least `LENGTH_STATS_MIN_SCORE` or in an "easy" lexicon, and each
    `length_counts` holds that language's number of such rated words per
    length of `lengths`. `counts`/`total`/`length_counts` `None` for a
    language with neither file. `lexicon_counts` ({language: {difficulty:
    {word length: number of words}}}, the lexicon a grid of that difficulty
    draws from) gives `level_totals` (one total per level of "levels") and
    `easy_length_counts` (the "easy" lexicon's words per length of
    `lengths`).

    Multi-word expressions, per language: `multi_total` (distinct MOTs of
    its multi-word wordlist), `multi_rated`/`multi_rated_min` (expressions
    of `data/usage_score_<lang>_multi.tsv`, all of them / those rated at
    least `LENGTH_STATS_MIN_SCORE`), `multi_level_totals` (one count per
    level of "levels": the expressions of that difficulty's lexicon),
    `multi_length_counts`/`multi_easy_length_counts` (per length of
    `lengths`: the expressions rated at least `LENGTH_STATS_MIN_SCORE` /
    those of the "easy" lexicon), from `multi_counts` ({language: {"total":
    n, "levels": {difficulty: {word length: n}}}}, `None` for a language
    without a multi-word wordlist); each `None` when unknown. `lengths`
    covers those expressions' lengths too."""
    lexicon_counts = lexicon_counts or {}
    multi_counts = multi_counts or {}
    levels = list(next(iter(lexicon_counts.values()), {}))
    per_language = {language: _load(language) for language in languages}
    multi_per_language = {language: _load_multi(language) for language in languages}
    scores = sorted({s for m in per_language.values() if m for s in m.values()})
    lengths = sorted({len(w) for m in (*per_language.values(), *multi_per_language.values()) if m
                      for w, s in m.items() if s >= LENGTH_STATS_MIN_SCORE}
                     | {n for c in lexicon_counts.values() for n in c.get("easy", {})}
                     | {n for c in multi_counts.values()
                        for n in (c["levels"].get("easy") or {})},
                     reverse=True)
    rows = []
    for language, m in per_language.items():
        row = {"language": language, "counts": None, "total": None, "length_counts": None}
        if m is not None:
            tally = {}
            by_length = {}
            for w, s in m.items():
                tally[s] = tally.get(s, 0) + 1
                if s >= LENGTH_STATS_MIN_SCORE:
                    by_length[len(w)] = by_length.get(len(w), 0) + 1
            row.update(counts=[tally.get(s, 0) for s in scores], total=len(m),
                       length_counts=[by_length.get(n, 0) for n in lengths])
        levels_of = lexicon_counts.get(language)
        row["level_totals"] = [sum(levels_of[d].values()) for d in levels] if levels_of else None
        row["easy_length_counts"] = ([levels_of["easy"].get(n, 0) for n in lengths]
                                     if levels_of and "easy" in levels_of else None)
        multi_of = multi_counts.get(language)
        multi_scores = multi_per_language[language]
        row["multi_total"] = multi_of["total"] if multi_of else None
        row["multi_rated"] = len(multi_scores) if multi_scores is not None else None
        row["multi_rated_min"] = (sum(1 for s in multi_scores.values() if s >= LENGTH_STATS_MIN_SCORE)
                                  if multi_scores is not None else None)
        multi_levels = multi_of["levels"] if multi_of else {}
        row["multi_level_totals"] = ([None if multi_levels.get(d) is None
                                      else sum(multi_levels[d].values()) for d in levels]
                                     if multi_of else None)
        multi_by_length = {}
        for w, s in (multi_scores or {}).items():
            if s >= LENGTH_STATS_MIN_SCORE:
                multi_by_length[len(w)] = multi_by_length.get(len(w), 0) + 1
        row["multi_length_counts"] = ([multi_by_length.get(n, 0) for n in lengths]
                                      if multi_scores is not None else None)
        multi_easy = multi_levels.get("easy")
        row["multi_easy_length_counts"] = ([multi_easy.get(n, 0) for n in lengths]
                                           if multi_easy is not None else None)
        rows.append(row)
    return {"scores": scores, "levels": levels, "languages": rows, "lengths": lengths}
