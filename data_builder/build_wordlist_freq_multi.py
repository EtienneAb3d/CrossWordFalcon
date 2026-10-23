#!/usr/bin/env python3
"""
Builds data/wordlist_<lang>_freq_multi.tsv (MOT<TAB>ACCENTUE<TAB>FREQUENCE
<TAB>CANONIQUE, the freq wordlist's columns, sorted by decreasing
FREQUENCE): every multi-word expression of data/wordlist_<lang>_multi.tsv
(build_wordlist_multi.py) found at least once in the language's reference
sentence corpus, with its occurrence count blended like
build_wordlist_freq.py's.

- Counting: the corpus (`<lang>_sentences_full.txt`, else the capped
  `<lang>_sentences.txt` when the full one is not on disk —
  build_sentence_corpus.py) and every spelling of the multi wordlist are
  cut into lowercase letter runs (build_wordlist_freq.py's `_WORD_RE`), so
  a space, a hyphen or an apostrophe all separate words ("arc-en-ciel" and
  "arc en ciel" are the same run of words); every occurrence of a
  spelling's words in a sentence counts, an expression nested in a longer
  one included (`_count_expressions`).
- ACCENTUE is the spelling of the MOT found most often (the multi
  wordlist's order on a tie); CANONIQUE is the multi wordlist's.
- FREQUENCE blends CANONICAL_WEIGHT (0.9) x the count of the most frequent
  lemma (the count of the lemma's own MOT, the MOT's own count when no
  lemma was found) with 0.1 x the MOT's own count — the sum over its
  spellings — then multiplies a likely proper noun (main spelling starting
  with a capital letter, in PROPER_NOUN_LANGS) by PROPER_NOUN_SCORE_FACTOR,
  like build_wordlist_freq.py.

Only English and French are built (build_wordlist_multi.py's LANGUAGES).

Usage:
    python3 data_builder/build_wordlist_freq_multi.py fr
"""
import argparse
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from build_wordlist_freq import (  # noqa: E402
    CANONICAL_WEIGHT, CORPUS_DIR, PROPER_NOUN_LANGS, PROPER_NOUN_SCORE_FACTOR, _WORD_RE,
)
from build_wordlist_multi import LANGUAGES, _mot  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"


def _corpus_path(lang):
    full = CORPUS_DIR / f"{lang}_sentences_full.txt"
    if full.exists():
        return full
    capped = CORPUS_DIR / f"{lang}_sentences.txt"
    print(f"{lang}: {full.name} not found, counting over {capped.name}", file=sys.stderr)
    return capped


def _read_multi(lang):
    """[(MOT, [spellings], [lemmas])] of data/wordlist_<lang>_multi.tsv."""
    rows = []
    with open(DATA_DIR / f"wordlist_{lang}_multi.tsv", encoding="utf-8") as f:
        for line in f:
            parts = line.rstrip("\n").split("\t")
            if len(parts) >= 3:
                rows.append((parts[0], [s for s in parts[1].split(";") if s],
                             [c for c in parts[2].split(";") if c]))
    return rows


def _words(text):
    return tuple(_WORD_RE.findall(text.lower()))


def _count_expressions(corpus_path, expressions):
    """{words tuple: occurrences in the corpus} for every tuple of
    `expressions` (at least 2 words each)."""
    prefixes = {e[:n] for e in expressions for n in range(1, len(e))}
    counts = Counter()
    with open(corpus_path, encoding="utf-8") as f:
        for line in f:
            words = _words(line)
            for i in range(len(words) - 1):
                if (words[i],) not in prefixes:
                    continue
                for j in range(i + 2, len(words) + 1):
                    key = words[i:j]
                    if key in expressions:
                        counts[key] += 1
                    if key not in prefixes:
                        break
    return counts


def build(lang):
    rows = _read_multi(lang)
    expressions = {_words(s) for _, spellings, _ in rows for s in spellings}
    expressions = {e for e in expressions if len(e) >= 2}
    corpus = _corpus_path(lang)
    print(f"{lang}: counting {len(expressions)} expressions in {corpus}", file=sys.stderr)
    counts = _count_expressions(corpus, expressions)

    found = {}  # MOT -> (own count, main spelling, lemmas)
    for mot, spellings, lemmas in rows:
        by_spelling = {}
        for s in spellings:
            by_spelling.setdefault(_words(s), s)
        total = sum(counts.get(words, 0) for words in by_spelling)
        if total:
            main = max(spellings, key=lambda s: counts.get(_words(s), 0))
            found[mot] = (total, main, lemmas)

    scored = []
    for mot, (own, main, lemmas) in found.items():
        lemma_counts = [found[m][0] for m in (_mot(c) for c in lemmas) if m in found]
        canonical = max(lemma_counts) if lemma_counts else own
        score = CANONICAL_WEIGHT * canonical + (1 - CANONICAL_WEIGHT) * own
        if lang in PROPER_NOUN_LANGS and main[:1].isupper():
            score *= PROPER_NOUN_SCORE_FACTOR
        scored.append((score, mot, main, ";".join(lemmas) or main))

    dst = DATA_DIR / f"wordlist_{lang}_freq_multi.tsv"
    with open(dst, "w", encoding="utf-8") as out:
        for score, mot, main, canonical in sorted(scored, key=lambda r: (-r[0], r[1])):
            out.write(f"{mot}\t{main}\t{score}\t{canonical}\n")
    print(f"{lang}: {len(scored)}/{len(rows)} expressions found in the corpus, written to {dst}")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("language", choices=LANGUAGES)
    build(ap.parse_args().language)


if __name__ == "__main__":
    main()
