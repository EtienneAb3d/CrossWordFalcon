#!/usr/bin/env python3
"""
Completes the CANONIQUE column of data/wordlist_<lang>_freq.tsv for every
row whose ACCENTUE carries no accent (only ASCII letters): such a corpus
spelling may be one whose accents are unknown, so its MOT is looked up
"impoverished", i.e. accents aside, in the English-Wiktionary Kaikki dump
of the language (data/wiktionary/<Name>-en.jsonl.gz, shared with
build_inflections.py and build_wordlist_scrabble.py): every accented
spelling with the same MOT adds its lemma(s) from the dump and from
`hunspell -m`, each source being possibly incomplete, after the row's own
("macerons" -> "maceron;macérer", from "macérons", a form of "macérer").
data/wordlist_<lang>_scrabble.tsv needs no such pass: its builder already
combines every source accents aside.

An accented spelling counts when its accents are all that tells it apart
from ACCENTUE (`accented_variant`): same letters and case once its accents
are stripped (German: same MOT whatever the case, since every German noun
is capitalized and "ß" is spelled "SS" in a MOT). A row whose ACCENTUE
carries an accent is left as is. Lemmas are ligature-folded like every
text column of the dictionaries. Idempotent.

Runs right after build_wordlist_freq.py, before build_wordlist_scrabble.py
(which takes over the freq row's lemmas), build_gloss_dictionary.py (which
reads the completed CANONIQUE column) and build_inflections.py (which
makes the same impoverished match on forms).

Usage:
    python3 data_builder/build_unaccented_lemmas.py fr
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import build_wordlist_freq  # noqa: E402
import build_wordlist_scrabble  # noqa: E402
from build_wordlist_freq import fold_ligatures, strip_accents  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
WORDLIST_DIR = ROOT / "data"
LANGUAGES = ("fr", "en", "de", "es", "it", "pt")

CANONIQUE_COLUMN = 3


def is_unaccented(spelling):
    """True when `spelling` (ligature-folded) holds no accented letter."""
    return spelling.isascii()


def accented_variant(lang, spelling, candidate):
    """True when `candidate` is `spelling` (an unaccented ACCENTUE) with
    accents (see the module docstring)."""
    candidate = fold_ligatures(candidate)
    if candidate.isascii():
        return False
    if lang == "de":
        return build_wordlist_scrabble.grid_form(candidate) == build_wordlist_scrabble.grid_form(spelling)
    return strip_accents(candidate) == spelling


def _read(path):
    with open(path, encoding="utf-8") as f:
        return [line.rstrip("\n").split("\t") for line in f]


def build(lang):
    path = WORDLIST_DIR / f"wordlist_{lang}_freq.tsv"
    rows = _read(path)
    wanted = {parts[0] for parts in rows
              if len(parts) > CANONIQUE_COLUMN and is_unaccented(parts[1])}
    print(f"{lang}: {len(wanted)} MOTs with an unaccented spelling", file=sys.stderr)

    kaikki = build_wordlist_scrabble._kaikki_forms(lang, wanted)
    variants = {}
    for parts in rows:
        if parts[0] in wanted:
            found = {fold_ligatures(c): l for c, l in kaikki.get(parts[0], {}).items()
                     if accented_variant(lang, parts[1], c)}
            if found:
                variants[parts[0]] = found
    stems = build_wordlist_freq._stem_map(
        lang, sorted({c for found in variants.values() for c in found}))

    changed = 0
    with open(path, "w", encoding="utf-8") as out:
        for parts in rows:
            found = variants.get(parts[0]) if is_unaccented(parts[1]) else None
            if found:
                lemmas = [c for c in parts[CANONIQUE_COLUMN].split(";") if c]
                for candidate in sorted(found):
                    lemmas.extend(sorted(fold_ligatures(l) for l in found[candidate] | set(stems.get(candidate, ())))
                                  or [candidate])
                canonical = ";".join(dict.fromkeys(lemmas))
                if canonical != parts[CANONIQUE_COLUMN]:
                    parts[CANONIQUE_COLUMN] = canonical
                    changed += 1
            out.write("\t".join(parts) + "\n")
    print(f"{lang}: {changed} rows completed in {path.name}")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("language", choices=LANGUAGES)
    build(ap.parse_args().language)


if __name__ == "__main__":
    main()
