#!/usr/bin/env python3
"""
Builds data/wordlist_<lang>_hunspell.tsv (MOT<TAB>ACCENTUE<TAB>CANONIQUE,
the Scrabble wordlist's columns, sorted by MOT): every word of the
language's Hunspell dictionary (data/hunspell_cache/<lang>.dic/.aff,
build_wordlist_freq.py's `_fetch_hunspell`) that fits a crossword grid
and is absent from both data/wordlist_<lang>_freq.tsv and
data/wordlist_<lang>_scrabble.tsv. Its words get dictionary definitions
(build_gloss_dictionary.py), grammatical analyses (build_inflections.py)
and usage scores (build_usage_scores.py --source out); they do not join
any grid lexicon.

- Every form of the dictionary is listed by Hunspell's `unmunch` (stem x
  affix expansion). `unmunch` misreads some affix files (multi-character
  flags, elision prefixes), so a flag left after "/" is cut off and every
  spelling kept must be accepted by `hunspell -G` itself, as-is or
  title-cased (build_wordlist_freq.py's `_spellcheck_valid`).
- MOT is the spelling's grid form (build_wordlist_scrabble.py's
  `grid_form`: accents stripped, ligatures folded, A-Z only, at least 2
  letters); spellings with an apostrophe, a hyphen or a digit drop out
  there.
- ACCENTUE lists every spelling with that MOT, `;`-separated, the main
  one first (a lowercase one, then alphabetical order); CANONIQUE is the
  union of their `hunspell -m` stems, the main spelling's first, the main
  spelling itself when none is known. Text columns are ligature-folded.

Only English, French and Spanish are built (LANGUAGES). For German,
Italian and Portuguese the Hunspell dictionary expands to hundreds of
thousands or millions of extra forms (German compounds with every
declension, Italian verb forms with their enclitic pronouns, Portuguese
prefixed/suffixed derivations of every root), far beyond what the usage
scoring can rate.

Needs `hunspell` and `unmunch` (Install.sh). Runs after
build_wordlist_scrabble.py, before build_gloss_dictionary.py.

Usage:
    python3 data_builder/build_wordlist_hunspell.py fr
"""
import argparse
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import build_wordlist_freq  # noqa: E402
from build_wordlist_freq import HUNSPELL_ENCODING, fold_ligatures  # noqa: E402
from build_wordlist_scrabble import grid_form  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
WORDLIST_DIR = ROOT / "data"
LANGUAGES = ("en", "fr", "es")


def _known_mots(lang):
    """MOTs of the freq and Scrabble wordlists."""
    known = set()
    for name in (f"wordlist_{lang}_freq.tsv", f"wordlist_{lang}_scrabble.tsv"):
        path = WORDLIST_DIR / name
        if path.exists():
            with open(path, encoding="utf-8") as f:
                known.update(line.split("\t", 1)[0] for line in f)
    return known


def _unmunched_forms(lang):
    """Every spelling `unmunch` lists for the language's dictionary,
    ligature-folded, any leftover "/flags" cut off."""
    basename = build_wordlist_freq._fetch_hunspell(lang)
    result = subprocess.run(["unmunch", f"{basename}.dic", f"{basename}.aff"],
                            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, check=True)
    forms = set()
    for line in result.stdout.decode(HUNSPELL_ENCODING.get(lang, "utf-8"), errors="replace").splitlines():
        form = line.strip().split("/", 1)[0]
        if form:
            forms.add(fold_ligatures(form))
    return forms


def _main_first(spellings):
    return sorted(spellings, key=lambda s: (not s.islower(), s))


def build(lang):
    known = _known_mots(lang)
    by_mot = {}
    for form in _unmunched_forms(lang):
        mot = grid_form(form)
        if mot and mot not in known:
            by_mot.setdefault(mot, set()).add(form)
    print(f"{lang}: {len(by_mot)} MOTs from unmunch absent from the freq/Scrabble wordlists",
          file=sys.stderr)

    valid = build_wordlist_freq._spellcheck_valid(lang, sorted({f for s in by_mot.values() for f in s}))
    if valid is None:
        sys.exit("hunspell is needed to validate the unmunched forms")
    rows = {}
    for mot, forms in by_mot.items():
        spellings = {fold_ligatures(valid[f]) for f in forms if f in valid}
        spellings = {s for s in spellings if grid_form(s) == mot}
        if spellings:
            rows[mot] = _main_first(spellings)
    stems = build_wordlist_freq._stem_map(lang, sorted({s for sp in rows.values() for s in sp}))

    dst = WORDLIST_DIR / f"wordlist_{lang}_hunspell.tsv"
    with open(dst, "w", encoding="utf-8") as out:
        for mot in sorted(rows):
            spellings = rows[mot]
            lemmas = [fold_ligatures(stem) for s in spellings for stem in stems.get(s, ())]
            canonical = ";".join(dict.fromkeys(lemmas)) or spellings[0]
            out.write(f"{mot}\t{';'.join(spellings)}\t{canonical}\n")
    print(f"{lang}: {len(rows)} words written to {dst}")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("language", choices=LANGUAGES)
    build(ap.parse_args().language)


if __name__ == "__main__":
    main()
