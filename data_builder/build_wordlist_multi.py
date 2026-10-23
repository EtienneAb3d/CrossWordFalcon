#!/usr/bin/env python3
"""
Builds data/wordlist_<lang>_multi.tsv (MOT<TAB>ACCENTUE<TAB>CANONIQUE, the
Scrabble wordlist's columns, sorted by MOT): every multi-word expression of
the language's Wiktionary — a headword made of several words joined by a
space or a hyphen ("arc-en-ciel", "quart de finale", "screw cap",
"wild-goose chase") — written as one grid word. Its words join no grid
lexicon.

Sources (Kaikki.org extracts, cached in data/wiktionary/, downloaded when
missing): the language's own Wiktionary edition (build_gloss_dictionary.py's
`<lang>_wiktionary.jsonl`) and the English-Wiktionary edition
(build_inflections.py's `<Name>-en.jsonl.gz`); for English both are the
same dump, read once. Only entries of the language itself (`lang_code`)
are read.

- A headword is kept when it holds at least one space or hyphen, every
  piece between the separators (space, hyphen, apostrophe) is made of
  letters only, and its part of speech is in KEPT_POS (proper nouns,
  proverbs, abbreviations and onomatopoeias included; symbols, affixes,
  articles, contractions and typographic variants left out).
- MOT is the headword's grid form with its separators removed
  (build_wordlist_scrabble.py's `grid_form`: accents stripped, ligatures
  folded, A-Z only) — "arc-en-ciel" -> ARCENCIEL — at most MAX_LENGTH
  (20) letters.
- An inflected form (every sense a `form_of`/`alt_of` sense: "arcs-en-ciel",
  "screw caps") is kept, its lemma(s) in CANONIQUE, like the other
  wordlists.
- ACCENTUE lists every headword with that MOT, `;`-separated, the main one
  first (a lemma entry before an inflected form, then lowercase first,
  then alphabetical order); CANONIQUE is the union of their lemmas, the
  main spelling's first (a lemma entry is its own lemma). Text columns are
  ligature-folded.

- A grammatical phrase is left out (`_is_grammatical_phrase`): one made of
  stopwords only ("ce que", "à la", "to the", "is a"), or of stopwords and
  a single verb form with the English infinitive marker ("to")
  right before it, or a subject pronoun right before it (CLITICS aside) or,
  in French, joined after it by a hyphen ("sais-tu"), for a verb, phrase or
  interjection entry (VERB_PHRASE_POS) — a conjugated or infinitive verb
  rather than an expression ("je suis", "il y a", "tu m'étonnes", "you
  know", "to be"; "un point c'est tout", "go for it", "want to" and the
  conjunction "c'est-à-dire" stay).
- An incomplete verbal phrase is left out too (`_is_incomplete_verbal_
  phrase`), for a verb, phrase or interjection entry: a single verb form
  opening the expression (after an elided negation: "ne", "don't") and
  followed by stopwords only, ending with a word awaiting its complement
  (DANGLING_WORDS: "want to", "look for", "essayer de", "faire avec"), or
  by nothing but a negation ("sais pas", "don't know"); a phrasal-verb
  particle is not dangling ("come on", "give up" stay), nor a pronoun
  hyphenated to an imperative ("ferme-la"). Words are the lowercase letter runs of the headword
  (build_wordlist_freq.py's `_WORD_RE`); STOPWORDS and SUBJECT_WORDS list
  them per language; a verb form is a form or lemma the single-word
  inflection table or gloss dictionary (data/inflection/<lang>_inflect.
  jsonl, data/gloss_dictionary/<lang>_glosses.jsonl) gives as a verb
  (`_verb_forms`).

- A determiner phrase is left out (`_is_determiner_phrase`, every entry
  but a proper noun): an article, possessive, demonstrative or numeral
  (DETERMINERS) opening the expression, followed by a single content word
  ("un autre", "mon père", "les deux", "the way", "a lot").
- An expression ending with a preposition or a conjunction
  (TRAILING_FUNCTION_WORDS) is left out, whatever its part of speech
  (`_ends_with_function_word`: "près de", "afin de", "dire que", "because
  of", "such as"); English phrasal-verb particles (up, on, off, in...) are
  not in the list.
- A conjugated verbal phrase is left out, for a verb, phrase or
  interjection entry (`_is_verbal_phrase`): a subject pronoun followed
  (CLITICS aside) by a verb form or an auxiliary ("j'ai dit", "I told
  you"), or a single verb form whose other words are all pronouns or
  adverbs (VERB_COMPANIONS: "y aller", "à voir", "screw it"); a reflexive
  verb ("se taire", "s'en aller") stays.
- An entry with no definition of its own (an inflected form, or a lemma
  entry without a gloss) is left out when the reference corpus never holds
  it (`_corpus_counts`: build_wordlist_freq_multi.py's counting).
- `--dropped FILE` writes every headword left out, with its filter.

A MOT already present in the freq or Scrabble wordlist is kept: the file
lists the expressions themselves.

Usage:
    python3 data_builder/build_wordlist_multi.py fr
"""
import argparse
import gzip
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import build_gloss_dictionary  # noqa: E402
import build_inflections  # noqa: E402
from build_wordlist_freq import _WORD_RE, fold_ligatures  # noqa: E402
from build_wordlist_scrabble import grid_form  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"
LANGUAGES = ("en", "fr")

# Longest MOT kept (letters only, separators not counted).
MAX_LENGTH = 20

# Function words: articles, determiners, pronouns, prepositions,
# conjunctions, common adverbs, the auxiliaries and the elided pieces the
# letter runs leave ("c'est" -> "c", "est"; "don't" -> "don", "t").
STOPWORDS = {
    "fr": frozenset("""
        le la les l un une des du de d au aux à a ce cet cette ces c ça cela ceci
        mon ma mes ton ta tes son sa ses notre nos votre vos leur leurs
        je j tu t il elle on nous vous ils elles me m te se s moi toi lui eux soi
        y en ne n pas plus que qu qui quoi dont où quel quelle quels quelles
        lequel laquelle lesquels lesquelles
        et ou ni mais or donc car si comme quand lorsque puisque
        dans par pour sur sous avec sans chez vers entre contre depuis pendant
        avant après jusque jusqu dès selon
        très bien tout toute tous toutes même aussi ainsi alors encore déjà
        ici là voici voilà
        être suis es est sommes êtes sont étais était étions étiez étaient été
        sera seras serons serez seront serait seraient soit soient fut
        avoir ai as avons avez ont avais avait avions aviez avaient eu aura
        aurai auras aurons aurez auront aurait auraient ait aient
        """.split()),
    "en": frozenset("""
        a an the this that these those my your his her its our their
        i me you he him she it we us they them myself yourself itself
        what which who whom whose where when why how there here
        and or nor but so if as than then because while
        of to in on at by for with from up down out off over under into onto
        about after before through between against during without within
        not no yes all any some each every both either neither such very too
        just only also even still more most much many
        am is are was were be been being have has had having
        do does did doing done will would shall should can could may might must
        don doesn didn isn aren wasn weren hasn haven hadn won wouldn
        shouldn couldn can cannot t s ll re ve d m
        """.split()),
}

# Parts of speech of an entry the subject + verb rule applies to.
VERB_PHRASE_POS = frozenset({"verb", "phrase", "intj"})

# Subject pronouns: one right before the verb form (CLITICS aside), or in
# French right after it ("sais-tu", "va-t-il"), makes it a conjugation.
SUBJECT_WORDS = {
    "fr": frozenset("je j tu il elle on nous vous ils elles ce c ça cela".split()),
    "en": frozenset("i you he she it we they there".split()),
}

# Pronouns and contracted auxiliaries that may stand between the subject and
# the verb ("je vous aime", "tu m'étonnes", "I'll show").
CLITICS = {
    "fr": frozenset("ne n me m te t se s le la les l lui leur y en nous vous".split()),
    "en": frozenset("ll d m re ve s".split()),
}

# English infinitive marker, right before the verb form ("to be").
INFINITIVE_MARKER = {"en": "to"}

# Incomplete verbal phrase (`_is_incomplete_verbal_phrase`, verb, phrase
# and interjection entries only — VERB_PHRASE_POS): a verb form
# opening the expression (after NEGATION_LEAD words only) and followed by
# stopwords ending with a DANGLING_WORDS word that awaits its complement
# ("want to", "dire que", "look for"), or by nothing but a negation
# (NEGATION_TAIL: "sais pas"; a negation lead with no tail: "don't know").
# Phrasal-verb particles that end a complete phrase ("come on", "give up")
# are not dangling.
NEGATION_LEAD = {
    "fr": frozenset("ne n".split()),
    "en": frozenset("do does did don doesn didn t not".split()),
}
NEGATION_TAIL = {
    "fr": frozenset("pas plus jamais rien".split()),
    "en": frozenset("not".split()),
}
DANGLING_WORDS = {
    "fr": frozenset("""que qu de d du des à au aux pour par sur avec dans sans
                       contre vers chez comme si dont le la les l un une""".split()),
    "en": frozenset("""to of for with at by from into onto than as that if whether
                       the a an""".split()),
}

# Words opening a determiner phrase (`_is_determiner_phrase`).
DETERMINERS = {
    "fr": frozenset("""le la les l un une des du au aux ce cet cette ces mon ma mes
                       ton ta tes son sa ses notre nos votre vos leur leurs deux trois
                       quatre cinq six sept huit neuf dix cent mille""".split()),
    "en": frozenset("""a an the this that these those my your his her its our their
                       some any every each no two three four five six seven eight
                       nine ten hundred thousand""".split()),
}

# Prepositions and conjunctions that cannot end an expression
# (`_ends_with_function_word`); English phrasal-verb particles excluded.
TRAILING_FUNCTION_WORDS = {
    "fr": frozenset("""de d du des à au aux que qu pour par sur avec dans sans contre
                       vers chez comme si et ou mais dont entre selon dès jusque
                       jusqu envers parmi malgré""".split()),
    "en": frozenset("""of to for with at from into onto upon than as that if whether
                       and or but because nor unless until till since towards toward
                       among amongst""".split()),
}

# Pronouns and adverbs that may accompany a single verb in a phrase left
# out by `_is_verbal_phrase` ("y aller", "à voir", "screw it"); the
# reflexive pronouns are not among them.
VERB_COMPANIONS = {
    "fr": frozenset("""y en le la les l lui leur me m te t nous vous ne n pas plus
                       jamais rien bien là ici à de""".split()),
    "en": frozenset("""it him her them me us you not never so too again here there
                       now then well""".split()),
}

# Parts of speech kept (Kaikki's normalized `pos`).
KEPT_POS = frozenset({
    "noun", "verb", "adj", "adv", "phrase", "prep_phrase", "adv_phrase",
    "intj", "prep", "conj", "pron", "det", "num", "particle",
    "name", "proverb", "abbrev", "onomatopoeia",
})

# Every piece made of letters, joined by spaces, hyphens or apostrophes.
_EXPRESSION_RE = re.compile(r"[^\W\d_]+(?:(?: |-|'|’)[^\W\d_]+)+")
_SEPARATOR_RE = re.compile(r"[ \-'’]")
# Leading "word" key of a dump line: lets a single-word entry be skipped
# without parsing the whole line.
_HEAD_WORD_RE = re.compile(r'^\{"word": "((?:[^"\\]|\\.)*)"')


def _dump_paths(lang):
    """Local paths of the language's dumps, downloaded when missing."""
    own = build_gloss_dictionary.DICS_DIR / f"{lang}_wiktionary.jsonl"
    if not own.exists():
        build_gloss_dictionary.DICS_DIR.mkdir(parents=True, exist_ok=True)
        build_gloss_dictionary._download(build_gloss_dictionary._kaikki_url(lang), own)
    if lang == "en":
        return [own]
    return [own, build_inflections._download_dump(lang)]


def _is_expression(word):
    return (" " in word or "-" in word) and _EXPRESSION_RE.fullmatch(word) is not None


def _entry_lemmas(entry):
    """Lemmas of an inflected/alternative form (every sense a form-of or
    alt-of sense), else None."""
    senses = entry.get("senses") or []
    lemmas = []
    for sense in senses:
        targets = sense.get("form_of") or sense.get("alt_of")
        if not targets:
            return None
        lemmas.extend(t.get("word", "") for t in targets)
    lemmas = [w.strip() for w in lemmas if w and w.strip()]
    return lemmas or None


def _read_entries(lang, path):
    """(headword, pos, lemmas or None, defined) for every multi-word entry of
    `lang` in one dump; `defined`: a lemma entry with a gloss of its own."""
    opener = gzip.open if path.suffix == ".gz" else open
    with opener(path, "rt", encoding="utf-8", errors="ignore") as f:
        for line in f:
            head = _HEAD_WORD_RE.match(line)
            if head:
                word = json.loads(f'"{head.group(1)}"')
                if " " not in word and "-" not in word:
                    continue
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                continue
            word = (entry.get("word") or "").strip()
            if entry.get("lang_code") != lang or entry.get("pos") not in KEPT_POS:
                continue
            if _is_expression(word):
                lemmas = _entry_lemmas(entry)
                defined = lemmas is None and any(
                    g for sense in entry.get("senses") or () for g in sense.get("glosses") or ())
                yield word, entry.get("pos"), lemmas, defined


def _verb_forms(lang):
    """Every lowercase form or lemma the single-word inflection table and
    gloss dictionary give as a verb."""
    verbs = set()
    path = DATA_DIR / "inflection" / f"{lang}_inflect.jsonl"
    if path.exists():
        with open(path, encoding="utf-8") as f:
            for line in f:
                rec = json.loads(line)
                if any(a.get("pos") == "verb" for a in rec.get("analyses", [])):
                    verbs.add(rec["form"].lower())
    path = DATA_DIR / "gloss_dictionary" / f"{lang}_glosses.jsonl"
    if path.exists():
        with open(path, encoding="utf-8") as f:
            for line in f:
                rec = json.loads(line)
                if any(e.get("pos") == "verb" for e in rec.get("entries", [])):
                    verbs.add(rec["word"].lower())
    return verbs


def _is_incomplete_verbal_phrase(word, pos, lang, verbs):
    """True, for a verb, phrase or interjection entry, for a single verb
    form opening the expression and left without its complement
    (DANGLING_WORDS) or with a bare negation (module docstring)."""
    if pos not in VERB_PHRASE_POS:
        return False
    stop = STOPWORDS[lang]
    lower = word.lower()
    words = _WORD_RE.findall(lower)
    k = 0
    while k < len(words) and words[k] in NEGATION_LEAD[lang]:
        # A one-letter piece is an elision ("n'", "don't") only next to an
        # apostrophe, not a letter ("N-méthyler").
        letter = words[k]
        if len(letter) == 1 and not re.search(
                rf"(?<![^\W\d_]){letter}['’]|['’]{letter}(?![^\W\d_])", lower):
            break
        k += 1
    if k >= len(words) or words[k] in stop or words[k] not in verbs:
        return False
    tail = words[k + 1:]
    if any(w not in stop for w in tail):
        return False
    # A pronoun hyphenated to an imperative completes it ("ferme-la").
    if tail and tail[-1] in DANGLING_WORDS[lang] and not lower.endswith(f"-{tail[-1]}"):
        return True
    negation = NEGATION_TAIL[lang]
    if tail and all(w in negation for w in tail):
        return True
    return not tail and k > 0


def _is_grammatical_phrase(word, pos, lang, verbs):
    """True for stopwords only, or stopwords and one verb form conjugated
    with a subject pronoun or preceded by the infinitive marker (module
    docstring)."""
    stop = STOPWORDS[lang]
    words = _WORD_RE.findall(word.lower())
    others = [i for i, w in enumerate(words) if w not in stop]
    if not others:
        return True
    if pos not in VERB_PHRASE_POS or len(others) != 1 or words[others[0]] not in verbs:
        return False
    k = others[0]
    subjects = SUBJECT_WORDS[lang]
    if k > 0 and words[k - 1] == INFINITIVE_MARKER.get(lang):
        return True
    i = k - 1
    while i >= 0 and words[i] in CLITICS[lang] and words[i] not in subjects:
        i -= 1
    if i >= 0 and words[i] in subjects:
        return True
    if lang == "fr" and f"{words[k]}-" in word.lower():
        after = words[k + 1:k + 3]
        if after[:1] and after[0] == "t":
            after = after[1:]
        return bool(after) and after[0] in subjects
    return False


def _is_determiner_phrase(word, pos, lang):
    """A determiner followed by one single content word (module docstring)."""
    if pos == "name":
        return False
    words = _WORD_RE.findall(word.lower())
    if not words or words[0] not in DETERMINERS[lang]:
        return False
    return sum(1 for w in words[1:] if w not in STOPWORDS[lang]) == 1


def _ends_with_function_word(word, lang):
    words = _WORD_RE.findall(word.lower())
    return bool(words) and words[-1] in TRAILING_FUNCTION_WORDS[lang]


def _is_verbal_phrase(word, pos, lang, verbs):
    """A conjugated verbal phrase or a verb with pronouns/adverbs only
    (module docstring)."""
    if pos not in VERB_PHRASE_POS:
        return False
    words = _WORD_RE.findall(word.lower())
    subjects, clitics = SUBJECT_WORDS[lang], CLITICS[lang]
    for i, w in enumerate(words[:-1]):
        if w not in subjects:
            continue
        j = i + 1
        while j < len(words) and words[j] in clitics and words[j] not in subjects:
            j += 1
        if j < len(words) and (words[j] in verbs or words[j] in STOPWORDS[lang]):
            return True
    content = [w for w in words if w not in VERB_COMPANIONS[lang]]
    if lang == "fr" and any(w in ("se", "s") for w in words):
        return False
    return len(content) == 1 and content[0] in verbs


def _corpus_counts(lang, spellings):
    """{spelling: occurrences in the reference corpus}
    (build_wordlist_freq_multi.py's counting)."""
    import build_wordlist_freq_multi as freq_multi
    tuples = {s: freq_multi._words(s) for s in spellings}
    counts = freq_multi._count_expressions(
        freq_multi._corpus_path(lang), {t for t in tuples.values() if len(t) >= 2})
    return {s: counts.get(t, 0) for s, t in tuples.items()}


def _mot(word):
    mot = grid_form(_SEPARATOR_RE.sub("", word))
    return mot if mot and len(mot) <= MAX_LENGTH else None


def _left_out(word, pos, lang, verbs):
    """The filter leaving `word` out, else None."""
    if _is_grammatical_phrase(word, pos, lang, verbs):
        return "grammatical"
    if _is_incomplete_verbal_phrase(word, pos, lang, verbs):
        return "incomplete_verbal"
    if _is_determiner_phrase(word, pos, lang):
        return "determiner"
    if _ends_with_function_word(word, lang):
        return "trailing_function_word"
    if _is_verbal_phrase(word, pos, lang, verbs):
        return "verbal"
    return None


def build(lang, dropped_path=None):
    # MOT -> spelling -> [is_inflected, lemmas, defined]
    by_mot = {}
    verbs = _verb_forms(lang)
    dropped = []
    for path in _dump_paths(lang):
        count = 0
        for word, pos, lemmas, defined in _read_entries(lang, path):
            mot = _mot(word)
            if not mot:
                continue
            reason = _left_out(word, pos, lang, verbs)
            if reason:
                dropped.append((reason, word))
                continue
            spelling = fold_ligatures(word)
            info = by_mot.setdefault(mot, {}).setdefault(spelling, [True, [], False])
            if lemmas is None:
                info[0] = False
                lemmas = [word]
            info[1].extend(fold_ligatures(w) for w in lemmas)
            info[2] = info[2] or defined
            count += 1
        print(f"{lang}: {count} multi-word entries kept from {path.name}", file=sys.stderr)

    # An entry with no definition of its own needs one corpus occurrence.
    undefined = [s for spellings in by_mot.values() for s, info in spellings.items() if not info[2]]
    counts = _corpus_counts(lang, undefined)
    for mot in list(by_mot):
        spellings = by_mot[mot]
        for s in [s for s in spellings if not spellings[s][2] and not counts.get(s)]:
            dropped.append(("undefined_unseen", s))
            del spellings[s]
        if not spellings:
            del by_mot[mot]
    reasons = {}
    for reason, _word in dropped:
        reasons[reason] = reasons.get(reason, 0) + 1
    print(f"{lang}: left out {reasons}", file=sys.stderr)
    if dropped_path:
        with open(dropped_path, "w", encoding="utf-8") as out:
            for reason, word in dropped:
                out.write(f"{reason}\t{word}\n")

    dst = DATA_DIR / f"wordlist_{lang}_multi.tsv"
    with open(dst, "w", encoding="utf-8") as out:
        for mot in sorted(by_mot):
            spellings = by_mot[mot]
            order = sorted(spellings, key=lambda s: (spellings[s][0], not s[:1].islower(), s))
            lemmas = [w for s in order for w in spellings[s][1]]
            out.write(f"{mot}\t{';'.join(order)}\t{';'.join(dict.fromkeys(lemmas))}\n")
    print(f"{lang}: {len(by_mot)} expressions written to {dst}")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("language", choices=LANGUAGES)
    ap.add_argument("--dropped", help="write every headword left out, with its filter, to this file")
    args = ap.parse_args()
    build(args.language, args.dropped)


if __name__ == "__main__":
    main()
