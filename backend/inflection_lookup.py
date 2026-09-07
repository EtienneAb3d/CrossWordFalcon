#!/usr/bin/env python3
"""Pure-local grammatical analysis of an exact inflected word form —
part of speech plus full inflection (person / number / gender / tense /
mood). Reads `data/inflection/<lang>.jsonl` (built once by
`data_builder/build_inflections.py` from the English-Wiktionary Kaikki
dump, filtered to the language's wordlist), lazily per language and
cached for the process's lifetime — no network, no per-request I/O after
the first lookup for a language. The file is plain uncompressed
JSON-lines so it can be inspected by hand:

    {"form": "humera", "analyses": [
        {"pos": "verb", "tags": "third-person singular future", "lemma": "humer"}]}

`describe_form(word, language)` returns `[(pos_code, description), ...]`,
e.g. `[("verb", 'verb, third-person singular future (of "humer")')]`,
the exact shape `backend/clues.py`'s `_build_pos_block` consumes. `[]`
means the form isn't in the table (a lemma with no `form-of` entry, or a
form Wiktionary doesn't document) — the caller then falls back to the
Hunspell-stem + gloss-dictionary part-of-speech line.
"""
import json
from pathlib import Path

_DIR = Path(__file__).resolve().parent.parent / "data" / "inflection"

# Readable label for the common Kaikki part-of-speech codes; anything not
# listed is passed through verbatim.
_POS_LABEL = {
    "adj": "adjective",
    "adv": "adverb",
    "name": "proper noun",
    "num": "numeral",
    "pron": "pronoun",
    "det": "determiner",
    "prep": "preposition",
    "conj": "conjunction",
    "intj": "interjection",
}

# language -> {form_lower: [(pos_code, description), ...]}
_cache = {}


def _load(language):
    if language in _cache:
        return _cache[language]
    index = {}
    path = _DIR / f"{language}.jsonl"
    if path.exists():
        with open(path, encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    continue
                form = rec.get("form")
                if not form:
                    continue
                out = []
                for a in rec.get("analyses", []):
                    pos = a.get("pos")
                    tags = a.get("tags")
                    lemma = a.get("lemma")
                    bits = []
                    if pos:
                        bits.append(_POS_LABEL.get(pos, pos))
                    if tags:
                        bits.append(tags)
                    text = ", ".join(bits)
                    if lemma and lemma.lower() != form:
                        text += f' (of "{lemma}")'
                    pair = (pos, text)
                    if pair not in out:
                        out.append(pair)
                index[form] = out
    _cache[language] = index
    return index


def describe_form(word, language):
    """`[(pos_code, description), ...]` for the exact `word` in
    `language`, or `[]` if it isn't in the table. Never raises, never
    touches the network."""
    return _load(language).get(word.lower(), [])
