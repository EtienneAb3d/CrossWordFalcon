#!/usr/bin/env python3
"""
Builds a crossword wordlist (WORD<TAB>ACCENTED<TAB>FREQUENCY) from a
HermitDave FrequencyWords source file (github.com/hermitdave/FrequencyWords,
CC-BY-SA), one "word count" pair per line — used for all five languages
(data/wordlist_{fr,en,de,es,it}_full.tsv, raw sources in data/raw/).

- WORD is accent-stripped and uppercased (crossword grid convention).
- ACCENTED is the original spelling as written in the source (natural case,
  accents/diacritics kept) — carried through so clue generation
  (backend/clues.py) can see the word's real gender/number/conjugation,
  which the grid's bare WORD form doesn't preserve.
- Multi-word entries, contractions (apostrophes), and words under 3 letters
  after normalization are excluded.
- On duplicates after accent-stripping, the higher frequency count wins
  (and its accented spelling is the one kept).

Usage:
    python3 build_wordlist_freq.py data/raw/en_50k.txt data/wordlist_en_full.tsv
"""
import sys
import unicodedata


def strip_accents(s):
    return "".join(
        c for c in unicodedata.normalize("NFKD", s)
        if not unicodedata.combining(c)
    )


def main():
    if len(sys.argv) != 3:
        print(f"Usage: {sys.argv[0]} <source.txt> <destination.tsv>", file=sys.stderr)
        sys.exit(1)
    src, dst = sys.argv[1], sys.argv[2]

    best = {}  # word -> (count, accented)
    with open(src, encoding="utf-8") as f:
        for line in f:
            parts = line.split()
            if len(parts) != 2:
                continue
            raw_word, raw_count = parts
            if any(ch in raw_word for ch in "-_'"):
                continue
            word = strip_accents(raw_word).upper()
            if not word.isalpha() or len(word) < 3:
                continue
            try:
                count = float(raw_count)
            except ValueError:
                continue
            if word not in best or count > best[word][0]:
                best[word] = (count, raw_word)

    with open(dst, "w", encoding="utf-8") as out:
        for word, (count, accented) in sorted(best.items(), key=lambda kv: -kv[1][0]):
            out.write(f"{word}\t{accented}\t{count}\n")

    print(f"{len(best)} words written to {dst}")


if __name__ == "__main__":
    main()
