#!/usr/bin/env python3
"""Usage score of a word (`data/usage_score_<lang>_scrabble.tsv` and, for the
freq words absent from the Scrabble wordlist, `data/usage_score_<lang>_
scrabble_out.tsv`, written by `data_builder/build_usage_scores.py`: 5
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


def usage_score(mot, language):
    """The usage score of the grid form `mot` (bare A-Z, as in the MOT
    column), `None` when unknown."""
    scores = _load(language)
    return None if scores is None else scores.get(mot.upper())


def usage_score_counts(languages):
    """Number of rated words per usage score, for the localhost-only
    "Statistiques" panel (`GET /api/usage_stats`), over both files of each
    language: `{"scores": [every score found, ascending], "languages":
    [{"language", "counts": [one count per score of "scores"], "total",
    "length_counts"}], "lengths": [...]}`; `lengths` lists, longest first,
    every word length found among the words rated at least
    `LENGTH_STATS_MIN_SCORE`, and each `length_counts` holds that language's
    number of such words per length of `lengths`. `counts`/`total`/
    `length_counts` `None` for a language with neither file."""
    per_language = {language: _load(language) for language in languages}
    scores = sorted({s for m in per_language.values() if m for s in m.values()})
    lengths = sorted({len(w) for m in per_language.values() if m
                      for w, s in m.items() if s >= LENGTH_STATS_MIN_SCORE}, reverse=True)
    rows = []
    for language, m in per_language.items():
        if m is None:
            rows.append({"language": language, "counts": None, "total": None,
                         "length_counts": None})
            continue
        tally = {}
        by_length = {}
        for w, s in m.items():
            tally[s] = tally.get(s, 0) + 1
            if s >= LENGTH_STATS_MIN_SCORE:
                by_length[len(w)] = by_length.get(len(w), 0) + 1
        rows.append({"language": language, "counts": [tally.get(s, 0) for s in scores],
                     "total": len(m),
                     "length_counts": [by_length.get(n, 0) for n in lengths]})
    return {"scores": scores, "languages": rows, "lengths": lengths}
