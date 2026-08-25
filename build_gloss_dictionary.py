#!/usr/bin/env python3
"""
Builds a per-language gloss dictionary — real dictionary definitions for
this project's own crossword vocabulary — from Kaikki.org's machine-
readable Wiktionary extracts (kaikki.org, itself built from Wiktionary
dumps, CC-BY-SA/GFDL like Wiktionary). Used by backend/clues.py to ground
the clue-writing prompt with an actual definition of a word's canonical
form(s), rather than relying solely on the LLM's own (sometimes wrong)
sense of what a word means — see the French "ARE" case in the
project-best-practices SKILL for why this exists, and why it targets the
word's CANONICAL form(s) (data/wordlist_<lang>_full.tsv's 4th column, from
build_wordlist_freq.py) rather than every inflected form: Wiktionary is
itself indexed by lemma, and a genuinely ambiguous word (French "suis" ->
"être" or "suivre") can have more than one candidate lemma, each gathered
here so the LLM sees every plausible meaning rather than one silently
picked in advance.

For English, Kaikki's primary extraction (grouped by the *defined* word's
language) already has English glosses, since English Wiktionary defines
English words in English. For French/German/Spanish/Italian, the primary
extraction's glosses are in *English* (English Wiktionary's take on a
French/German/Spanish/Italian word) — not what we want — so this instead
uses Kaikki's same-language Wiktionary edition (frwiktionary, dewiktionary,
eswiktionary, itwiktionary), which gives native-language definitions.

Unlike build_sentence_corpus.py's sources, these files can't usefully be
partially downloaded: they aren't sorted by frequency, so a partial
download would only ever cover words starting with the first few letters
of the alphabet. Each is downloaded in full (multi-gigabyte), filtered
down to just the lemmas this project's dictionaries actually use (a few
hundred thousand words at most, vs. every word/sense Wiktionary has), and
kept under DICS/ (project root, gitignored — same caching principle as
build_sentence_corpus.py's CORPUS/) rather than deleted: a lemma already
cached there is read from disk instead of re-downloaded, so a later
rebuild (the wordlist's own CANONICAL column changed, MAX_GLOSSES_PER_
WORD changed, etc.) doesn't need to re-fetch several gigabytes per
language from kaikki.org every time.

Usage:
    python3 build_gloss_dictionary.py fr
    python3 build_gloss_dictionary.py en
"""
import argparse
import json
import subprocess
import sys
from pathlib import Path

DATA_DIR = Path(__file__).resolve().parent / "data"
GLOSS_DIR = DATA_DIR / "gloss_dictionary"
# Raw, full Kaikki/Wiktionary dump cache — see the module docstring's
# "kept under DICS/" paragraph. A project-root sibling of CORPUS/ (build_
# sentence_corpus.py's own raw cache), not nested under data/: both are
# purely local, gitignored working caches of upstream downloads, distinct
# from GLOSS_DIR above, which stays the final, filtered, checked-into-git
# output the rest of the pipeline actually reads.
DICS_DIR = Path(__file__).resolve().parent / "DICS"

# Kaikki source per language: (edition, word-language-name-in-that-edition).
# English uses the primary (English-Wiktionary-sourced) extraction, already
# in English; the other four use their own-language Wiktionary edition so
# glosses come out in that language, not English.
KAIKKI_SOURCE = {
    "en": ("dictionary", "English"),
    "fr": ("frwiktionary", "Français"),
    "de": ("dewiktionary", "Deutsch"),
    "es": ("eswiktionary", "Español"),
    "it": ("itwiktionary", "Italiano"),
}

MAX_GLOSSES_PER_WORD = 3


def _kaikki_url(lang):
    edition, word_lang = KAIKKI_SOURCE[lang]
    from urllib.parse import quote
    encoded = quote(word_lang)
    return f"https://kaikki.org/{edition}/{encoded}/kaikki.org-dictionary-{encoded}.jsonl"


def _target_lemmas(lang):
    """Every canonical form (lemma) this project's own dictionary for
    `lang` actually needs a gloss for — data/wordlist_<lang>_full.tsv's
    4th column, build_wordlist_freq.py's CANONICAL (one or more per word,
    semicolon-separated)."""
    path = DATA_DIR / f"wordlist_{lang}_full.tsv"
    lemmas = set()
    with open(path, encoding="utf-8") as f:
        for line in f:
            parts = line.rstrip("\n").split("\t")
            if len(parts) >= 4:
                lemmas.update(c for c in parts[3].split(";") if c)
    return lemmas


def _download(url, dst_path):
    print(f"Downloading {url} (this is several GB, one-time) ...", file=sys.stderr)
    subprocess.run(["curl", "-sL", "--fail", "-o", str(dst_path), url], check=True)


def build_gloss_dictionary(lang):
    if lang not in KAIKKI_SOURCE:
        raise ValueError(f"no Kaikki source configured for {lang!r}")
    lemmas = _target_lemmas(lang)
    lemmas_lower = {w.lower() for w in lemmas}
    print(f"Looking for glosses for {len(lemmas)} lemmas", file=sys.stderr)

    GLOSS_DIR.mkdir(parents=True, exist_ok=True)
    DICS_DIR.mkdir(parents=True, exist_ok=True)
    raw_path = DICS_DIR / f"{lang}_wiktionary.jsonl"
    if raw_path.exists():
        print(f"Using cached Wiktionary dump: {raw_path}", file=sys.stderr)
    else:
        _download(_kaikki_url(lang), raw_path)

    found = {}  # lemma_lower -> {"word": natural spelling, "entries": [{"pos":..., "glosses":[...]}]}
    with open(raw_path, encoding="utf-8", errors="ignore") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                continue  # truncated/malformed line — skip, not fatal
            word = entry.get("word")
            if not word or word.lower() not in lemmas_lower:
                continue
            glosses = []
            for sense in entry.get("senses", []):
                glosses.extend(sense.get("glosses", []))
                if len(glosses) >= MAX_GLOSSES_PER_WORD:
                    break
            if not glosses:
                continue
            bucket = found.setdefault(word.lower(), {"word": word, "entries": []})
            bucket["entries"].append({
                "pos": entry.get("pos", ""),
                "glosses": glosses[:MAX_GLOSSES_PER_WORD],
            })

    dst = GLOSS_DIR / f"{lang}_glosses.jsonl"
    with open(dst, "w", encoding="utf-8") as out:
        for bucket in found.values():
            out.write(json.dumps(bucket, ensure_ascii=False) + "\n")
    print(f"{len(found)}/{len(lemmas)} lemmas matched, written to {dst}")
    return dst


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("language", choices=sorted(KAIKKI_SOURCE))
    args = ap.parse_args()
    build_gloss_dictionary(args.language)


if __name__ == "__main__":
    main()
