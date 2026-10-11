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
