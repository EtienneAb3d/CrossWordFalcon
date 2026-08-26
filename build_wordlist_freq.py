#!/usr/bin/env python3
"""
Builds a crossword wordlist (WORD<TAB>ACCENTED<TAB>FREQUENCY<TAB>CANONICAL)
for one language, from its reference sentence corpus
(data/reference_corpus/<lang>_sentences.txt, built by
build_sentence_corpus.py from OpenSubtitles + Wikipedia + Books) — used
for all five languages (data/wordlist_{fr,en,de,es,it}_full.tsv). Word frequency is
computed directly here (counting occurrences in the corpus) rather than
via a separate persisted intermediate file: an earlier version of this
pipeline wrote that count to data/raw/<lang>_50k.txt as a hand-off between
two scripts, but now that the corpus is itself this project's own (not a
third-party source needing separate archival — see the project-best-
practices SKILL for the earlier HermitDave-to-corpus switch), persisting
that intermediate step served no purpose `data/raw/` was originally kept
for, so it was folded into one script and the file removed.

- WORD is accent-stripped and uppercased (crossword grid convention).
- ACCENTED is the original spelling as written in the source (natural case,
  accents/diacritics kept) — carried through so clue generation
  (backend/clues.py) can see the word's real gender/number/conjugation,
  which the grid's bare WORD form doesn't preserve.
- Words under 3 letters after normalization are excluded.
- On duplicates after accent-stripping, the higher frequency count wins
  (and its accented spelling is the one kept).
- The corpus-building step already filters out wrong-language sentences
  (see build_sentence_corpus.py), but every candidate word is *also*
  validated individually against a real dictionary of its own language
  here, as a second, independent check: a Hunspell dictionary (.dic/.aff,
  from LibreOffice/dictionaries — see HUNSPELL_SOURCE) is downloaded
  (cached in data/hunspell_cache/, gitignored) and each candidate's
  natural spelling is spellchecked against it with the `hunspell`
  command-line tool itself (part of the `hunspell` package — `brew
  install hunspell` or `apt-get install hunspell`), rather than
  pre-expanding the dictionary into a wordlist with `unmunch`: verified by
  hand that `unmunch` silently drops many irregular verb conjugations
  (French être/avoir/vouloir and friends — SUIS, ÉTAIT, VEUX, SONT, AVEZ...)
  that `hunspell`'s own spellchecker correctly recognizes, almost certainly
  an affix-flag-chaining limitation in `unmunch`'s naive full enumeration
  rather than in the dictionary itself.
- FREQUENCY (the ranking signal `backend/crossword_gen.py` uses to decide
  which words are "easy") is not the raw corpus occurrence count as-is:
  dialogue-heavy corpora underrepresent some inflected forms that are
  nevertheless perfectly easy/common words (e.g. French "déterminées" is
  rare in dialogue even though its infinitive "déterminer" is common).
  Hunspell's morphological analysis (`hunspell -m`) finds each word's most
  probable canonical form(s) (its stem — a word can be genuinely ambiguous
  between several, e.g. French "suis" -> "être" or "suivre"), and the
  written frequency blends 90% the single most-frequent candidate
  canonical form's own frequency with 10% the word's own raw frequency
  (CANONICAL_WEIGHT) — enough to correct the corpus-frequency distortion
  while still ranking different inflected forms of the same stem against
  each other, rather than collapsing them all to one identical score.
- CANONICAL is every distinct candidate canonical form found for this word
  (semicolon-separated when there's more than one, e.g. "être;suivre"),
  not just the single one used for the frequency blend above — a gloss
  dictionary is indexed by lemma, not by inflected form, so looking up a
  word's definition means mapping inflected -> canonical(s) first; keeping
  every candidate (not just the most-frequent) lets that lookup show every
  plausible meaning and leaves resolving genuine ambiguity to the LLM,
  which sees the word in its actual clue-writing context, rather than
  silently committing to one meaning at dictionary-build time.
- Likely proper nouns (person/place/brand names) have their FREQUENCY
  multiplied by PROPER_NOUN_SCORE_FACTOR (0.5), after a report that they
  showed up too often at "easy" difficulty (see backend/crossword_gen.py's
  DIFFICULTY_PRESETS, which caps "easy" to the globally-highest-scored
  words). Detected via the same as-is-vs-title-cased check `_spellcheck_
  valid` already does for every candidate: in French/English/Spanish/
  Italian, an ordinary word is normally written lowercase in running text,
  so a candidate that Hunspell only recognizes once capitalized (and
  wasn't already capitalized in the corpus itself) is overwhelmingly
  likely to be a name that happened to appear in a lowercased/otherwise-
  uncapitalized corpus line, not a common word. This signal is deliberately
  *not* applied to German: every German noun, common or proper, requires
  capitalization, so "needed title-case to validate" carries no proper-
  noun information there at all — see PROPER_NOUN_SCORE_FACTOR's own
  comment. Not a hard exclusion (a real proper noun can still be a
  legitimate, well-known crossword answer) — a same-magnitude demotion
  rather than a removal, so a very frequent name can still rank above a
  rare common word, just not among the very easiest words by default.

Usage:
    python3 build_wordlist_freq.py fr
"""
import argparse
import os
import re
import subprocess
import sys
import unicodedata
import urllib.request
from collections import Counter
from pathlib import Path

CACHE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "hunspell_cache")
CORPUS_DIR = Path(__file__).resolve().parent / "data" / "reference_corpus"
WORDLIST_DIR = Path(__file__).resolve().parent / "data"

_WORD_RE = re.compile(r"[^\W\d_]+", re.UNICODE)

# How much of the final ranking score comes from the word's canonical
# (stem) form's frequency vs. the word's own raw frequency — see the
# FREQUENCY bullet in the module docstring above.
CANONICAL_WEIGHT = 0.9

# Multiplier applied to a likely proper noun's final score — see the
# "Likely proper nouns" bullet in the module docstring. A flat halving
# rather than a more elaborate formula: this is a coarse signal (one
# Hunspell capitalization check, not real named-entity recognition), so a
# coarse correction matches it — enough to push most proper nouns out of
# "easy" difficulty's globally-highest-scored cutoff without pretending to
# rank them precisely against each other or against common words.
# Languages where every noun requires capitalization regardless of common/
# proper status (German) get no adjustment at all: see PROPER_NOUN_LANGS.
PROPER_NOUN_SCORE_FACTOR = 0.5

# Parses one "-m" morphological-analysis output line, e.g.
# "déterminées  st:déterminer fl:p+" -> ("déterminées", "déterminer").
_STEM_LINE_RE = re.compile(r"^(\S+)\s+st:(\S+)")

# Path (relative to LibreOffice/dictionaries' raw GitHub root) of each
# language's .dic/.aff pair. French lives in a nested "dictionaries"
# subfolder because fr_FR/ is otherwise just LibreOffice extension
# packaging; German uses the "frami" dictionary (the one LibreOffice ships
# for de_DE) rather than a plain de_DE.dic, which doesn't exist upstream.
HUNSPELL_SOURCE = {
    "fr": "fr_FR/dictionaries/fr",
    "en": "en/en_US",
    "de": "de/de_DE_frami",
    "es": "es/es_ES",
    "it": "it_IT/it_IT",
}

# Languages where an ordinary common word is normally written lowercase in
# running text, so "Hunspell only validated the title-cased form" (see
# _spellcheck_valid) is a meaningful proper-noun signal — every HUNSPELL_
# SOURCE language except German, whose nouns are ALL capitalized
# regardless of common/proper status, so the same signal there would flag
# ordinary nouns ("Haus") just as often as real names and carries no
# useful information — see PROPER_NOUN_SCORE_FACTOR above.
PROPER_NOUN_LANGS = set(HUNSPELL_SOURCE) - {"de"}

# Actual byte encoding of each pair's .dic/.aff, verified with `file` since
# a missing "SET" line in the .aff doesn't reliably mean the Hunspell
# default of ISO-8859-1 — most of these are UTF-8 in practice regardless.
HUNSPELL_ENCODING = {
    "fr": "utf-8",
    "en": "utf-8",
    "de": "iso-8859-1",
    "es": "utf-8",
    "it": "utf-8",
}


def strip_accents(s):
    return "".join(
        c for c in unicodedata.normalize("NFKD", s)
        if not unicodedata.combining(c)
    )


def _count_word_frequencies(lang):
    """Counts word occurrences (case-insensitive, accents kept) in
    `lang`'s reference sentence corpus — see build_sentence_corpus.py.
    Returns [(raw_word, count), ...], most frequent first."""
    corpus_path = CORPUS_DIR / f"{lang}_sentences.txt"
    counts = Counter()
    with open(corpus_path, encoding="utf-8") as f:
        for line in f:
            for token in _WORD_RE.findall(line):
                counts[token.lower()] += 1
    return counts.most_common()


def _fetch_hunspell(lang):
    """Downloads (and caches under CACHE_DIR) the .dic/.aff pair for
    `lang`, returns the shared basename `hunspell -d` expects (it appends
    .aff/.dic itself)."""
    base = "https://raw.githubusercontent.com/LibreOffice/dictionaries/master"
    source = HUNSPELL_SOURCE[lang]
    os.makedirs(CACHE_DIR, exist_ok=True)
    basename = os.path.join(CACHE_DIR, lang)
    for ext in ("aff", "dic"):
        local_path = f"{basename}.{ext}"
        if not os.path.exists(local_path):
            print(f"Downloading {source}.{ext} ...", file=sys.stderr)
            urllib.request.urlretrieve(f"{base}/{source}.{ext}", local_path)
    return basename


def _titlecase(word):
    return word[:1].upper() + word[1:]


def _spellcheck_valid(lang, raw_words):
    """Maps each of `raw_words` (natural corpus spelling) that `hunspell`
    recognizes as a real word of `lang` to the spelling that's actually
    valid — the word as-is, or a title-cased variant. HermitDave's corpora
    are lowercased, but German (among others) requires every noun to be
    capitalized, so a purely case-as-given check would reject ordinary
    nouns like "haus" wholesale even though "Haus" is exactly right; using
    whichever form actually validated (rather than always the raw,
    possibly wrongly-cased, spelling) keeps the ACCENTED column a real,
    correctly-cased spelling instead of silently keeping "haus". Returns
    None (skip validity filtering) if `lang` has no known Hunspell source,
    the download fails, or `hunspell` isn't installed."""
    if lang not in HUNSPELL_SOURCE:
        return None
    if not raw_words:
        return {}
    try:
        dict_basename = _fetch_hunspell(lang)
    except OSError as e:
        print(
            f"Warning: could not fetch the Hunspell dictionary for {lang!r}: {e}. "
            "Continuing without dictionary validity filtering.",
            file=sys.stderr,
        )
        return None

    forms_to_check = set(raw_words) | {_titlecase(w) for w in raw_words}
    encoding = HUNSPELL_ENCODING.get(lang, "utf-8")
    try:
        result = subprocess.run(
            ["hunspell", "-d", dict_basename, "-i", encoding, "-G"],
            input="\n".join(forms_to_check).encode(encoding, errors="replace"),
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
        )
    except FileNotFoundError:
        print(
            "Warning: `hunspell` not found (install it with `brew install "
            "hunspell` or `apt-get install hunspell`). Continuing without "
            "dictionary validity filtering.",
            file=sys.stderr,
        )
        return None
    valid_forms = set(result.stdout.decode(encoding, errors="replace").splitlines())

    accented_by_raw = {}
    for w in raw_words:
        if w in valid_forms:
            accented_by_raw[w] = w
        elif _titlecase(w) in valid_forms:
            accented_by_raw[w] = _titlecase(w)
    return accented_by_raw


def _stem_map(lang, forms):
    """For each of `forms` (already-validated, correctly-cased spellings),
    every candidate canonical stem Hunspell's morphological analysis
    proposes — a word can have more than one when genuinely ambiguous
    (French "suis" parses as either "être" or "suivre"). Returns
    {form: [stem, ...]}; a form absent from the result got no stem lines
    back from `hunspell -m` (rare — only for the small share of inputs a
    dictionary can spellcheck but not analyze morphologically). Returns {}
    if `lang` has no known Hunspell source, the download fails, or
    `hunspell` isn't installed — callers should then skip the canonical-
    form boost entirely rather than fail the whole build."""
    if lang not in HUNSPELL_SOURCE or not forms:
        return {}
    try:
        dict_basename = _fetch_hunspell(lang)
    except OSError as e:
        print(
            f"Warning: could not fetch the Hunspell dictionary for {lang!r}: {e}. "
            "Continuing without the canonical-form ranking boost.",
            file=sys.stderr,
        )
        return {}

    encoding = HUNSPELL_ENCODING.get(lang, "utf-8")
    try:
        result = subprocess.run(
            ["hunspell", "-d", dict_basename, "-i", encoding, "-m"],
            input="\n".join(forms).encode(encoding, errors="replace"),
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
        )
    except FileNotFoundError:
        print(
            "Warning: `hunspell` not found (install it with `brew install "
            "hunspell` or `apt-get install hunspell`). Continuing without "
            "the canonical-form ranking boost.",
            file=sys.stderr,
        )
        return {}

    stems = {}
    for line in result.stdout.decode(encoding, errors="replace").splitlines():
        match = _STEM_LINE_RE.match(line)
        if match:
            stems.setdefault(match.group(1), []).append(match.group(2))
    return stems


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("language", choices=sorted(HUNSPELL_SOURCE))
    args = ap.parse_args()
    lang = args.language
    dst = WORDLIST_DIR / f"wordlist_{lang}_full.tsv"

    candidates = []  # (raw_word, raw_count)
    for raw_word, raw_count in _count_word_frequencies(lang):
        word = strip_accents(raw_word).upper()
        # 2-letter words are kept (grid slots as short as 2 cells are now
        # allowed — MAX_SHORT_ZONE_COUNT in backend/crossword_gen.py — and,
        # unlike 1-letter zones, are real cluable words: "et", "ou", "no",
        # etc.); a bare 1-letter zone never becomes a slot at all, so a
        # 1-letter word would never be looked up — excluded here too.
        if not word.isalpha() or len(word) < 2:
            continue
        candidates.append((raw_word, raw_count))

    accented_by_raw = _spellcheck_valid(lang, [w for w, _ in candidates])

    best = {}  # word -> (count, accented, likely_proper_noun)
    excluded = 0
    for raw_word, raw_count in candidates:
        if accented_by_raw is not None:
            accented = accented_by_raw.get(raw_word)
            if accented is None:
                excluded += 1
                continue
        else:
            accented = raw_word
        count = float(raw_count)
        word = strip_accents(raw_word).upper()
        # See PROPER_NOUN_SCORE_FACTOR/PROPER_NOUN_LANGS above: only
        # meaningful when Hunspell had to capitalize the word itself to
        # validate it (accented != raw_word) in a language where ordinary
        # words aren't normally capitalized in running text.
        likely_proper_noun = lang in PROPER_NOUN_LANGS and accented != raw_word
        if word not in best or count > best[word][0]:
            best[word] = (count, accented, likely_proper_noun)

    # Blend in the frequency of each word's most probable canonical form —
    # see the FREQUENCY bullet in the module docstring. Also keep EVERY
    # distinct candidate canonical form (not just the one used for the
    # frequency blend) as its own column: a word can be genuinely
    # ambiguous between stems (French "suis" -> "être" or "suivre"), and a
    # Wiktionary-style gloss dictionary is indexed by lemma, not by every
    # inflected form — looking up a definition for a grid word later means
    # mapping inflected -> canonical(s) first. Keeping only the single
    # highest-frequency candidate here would silently commit to one
    # meaning before the LLM ever sees the word in its actual clue-writing
    # context, which is the wrong layer to resolve a genuine ambiguity.
    stems_by_form = _stem_map(lang, [accented for _, accented, _ in best.values()])
    scored = {}
    for word, (own_freq, accented, likely_proper_noun) in best.items():
        stem_candidates = stems_by_form.get(accented, [])
        canonical_freq = None
        for stem in stem_candidates:
            stem_entry = best.get(strip_accents(stem).upper())
            if stem_entry is not None and (canonical_freq is None or stem_entry[0] > canonical_freq):
                canonical_freq = stem_entry[0]
        if canonical_freq is None:
            canonical_freq = own_freq
        score = CANONICAL_WEIGHT * canonical_freq + (1 - CANONICAL_WEIGHT) * own_freq
        if likely_proper_noun:
            score *= PROPER_NOUN_SCORE_FACTOR
        # Dedupe while preserving order; no stem found means the word is its own lemma.
        canonical_forms = list(dict.fromkeys(stem_candidates)) or [accented]
        scored[word] = (score, accented, canonical_forms)

    with open(dst, "w", encoding="utf-8") as out:
        for word, (score, accented, canonical_forms) in sorted(scored.items(), key=lambda kv: -kv[1][0]):
            out.write(f"{word}\t{accented}\t{score}\t{';'.join(canonical_forms)}\n")

    proper_noun_count = sum(1 for _, _, likely_proper_noun in best.values() if likely_proper_noun)
    message = f"{len(best)} words written to {dst}"
    if accented_by_raw is not None:
        message += f" ({excluded} words not found in the {lang!r} dictionary filtered out)"
    if lang in PROPER_NOUN_LANGS:
        message += f" ({proper_noun_count} likely proper nouns scored at {PROPER_NOUN_SCORE_FACTOR:.0%})"
    print(message)


if __name__ == "__main__":
    main()
