#!/usr/bin/env python3
"""
Rates how common every word of data/wordlist_<lang>_scrabble.tsv is, with
the local LLM, and writes data/usage_score_<lang>_scrabble.tsv
(`MOT<TAB>ACCENTUE<TAB>SCORE`, sorted by decreasing SCORE, then MOT).
With `--source out`, rates instead every word absent from the Scrabble
wordlist — each word of data/wordlist_<lang>_freq.tsv missing from it (its
highest-FREQUENCE row per MOT: ACCENTUE as the form, CANONIQUE as the
lemmas), then each word of data/wordlist_<lang>_hunspell.tsv
(build_wordlist_hunspell.py, when it exists: every spelling of ACCENTUE,
CANONIQUE as the lemmas) — and writes
data/usage_score_<lang>_scrabble_out.tsv, same format. With `--source
multi`, rates the multi-word expressions of data/wordlist_<lang>_multi.tsv
(build_wordlist_multi.py; the ones of data/wordlist_<lang>_freq_multi.tsv
first, by decreasing FREQUENCE) into data/usage_score_<lang>_multi.tsv,
same format: the entry is presented as "Expression"; its analyses and
definitions come from data/inflection/
<lang>_inflect_multi.jsonl and data/gloss_dictionary/<lang>_glosses_multi.
jsonl (the single-word files for a single-word lemma), their corpus counts
from the expression's own occurrences (build_wordlist_freq_multi.py's
`_count_expressions`), and the system prompt gains MULTI_WORD_NOTES (the
expression is rated as a whole). Before its rating, each expression goes
through a short self-contained check (`SELF_CONTAINED_PROMPT`, its main
spelling alone, a one-letter Y/N answer, examples in the prompt config's
"multi_incomplete_examples"/"multi_established_examples"): an expression
that does not clearly make sense out of a sentence giving it its meaning
("à cause", "c'est pourquoi") is rated 0 without the rating call.

Scale (SCORE):
  5  everyday vocabulary
  4  formal or literary vocabulary
  3  rare form of an everyday word (French: imperfect/pluperfect
     subjunctive, first/second-person simple past...)
  2  technical term of a trade or a scientific discipline
  1  dated or archaic word
  0  practically unknown word

The LLM is given, per word: the inflected form (every spelling of the
ACCENTUE column), its grammatical analysis (data/inflection/<lang>_inflect.jsonl,
`backend/inflection_lookup.describe_form`), its lemmas (CANONIQUE), their
dictionary definitions (data/gloss_dictionary/, `backend/gloss_lookup`)
and the number of occurrences of the form and of each lemma in the
reference corpus (data/reference_corpus/<lang>_sentences.txt, counted
once at start-up the way build_wordlist_freq.py counts tokens). The
wordlist's own FREQUENCE column is not used: it is mostly the lemma's
frequency (CANONICAL_WEIGHT), so it cannot tell a rare form from a common
one.

Each word is rated by its own request (words sharing a prompt influence
each other's ratings), `--concurrency` (5) requests at a time. The system
prompt, identical for every word, comes first so the LLM server's prefix
(KV) cache reuses it; the word's entry follows as the user message. The
answer is the score alone, one token (`max_tokens` 1). The prompt's rules
are in English here; the per-language examples and rare-form definitions
live in data/<lang>_prompt_config.json's "usage_score" object.

Resumable: every answered word is appended to the output file's
`.partial` (data/usage_score_<lang>_scrabble[_out].tsv.partial,
gitignored) and skipped on
the next run; the sorted final file replaces it once every word has a
score. A word already rated in the final file is not rated again either,
so a run after the word set grew only rates the new words (delete the
final file to rate everything again); the final file keeps only the
words of the current word set. A word with no digit from 0 to 5 in its answer after
`MAX_ATTEMPTS` is left out (reported) — a re-run picks it up.

Needs the local LLM (./run_llm.sh): LLM_BASE_URL/LLM_MODEL/LLM_API_KEY,
like backend/clues.py; without LLM_MODEL, the first model the server
lists is used.

Usage:
    python3 data_builder/build_usage_scores.py fr
    python3 data_builder/build_usage_scores.py fr --words MAISON FUSSIEZ   # print only
    python3 data_builder/build_usage_scores.py fr --source out
    python3 data_builder/build_usage_scores.py fr --source multi
"""
import argparse
import json
import os
import re
import sys
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(ROOT))
from build_wordlist_freq import _WORD_RE, fold_ligatures, is_grid_form  # noqa: E402
from build_wordlist_freq_multi import _count_expressions  # noqa: E402
from backend import gloss_lookup, inflection_lookup  # noqa: E402

DATA_DIR = ROOT / "data"
CORPUS_DIR = DATA_DIR / "reference_corpus"

DEFAULT_LLM_BASE_URL = "http://127.0.0.1:3002/v1/chat/completions"
CONCURRENCY = 5
MAX_ATTEMPTS = 3
REQUEST_TIMEOUT_S = 300
# Prompt size bounds per word.
MAX_LEMMAS = 3
MAX_GLOSSES_PER_LEMMA = 3
MAX_GLOSS_CHARS = 180
MAX_ANALYSES = 4

SYSTEM_PROMPT = """You are an expert lexicographer of contemporary {language}.
You are given a {language} word entry: an inflected word form, its grammatical analysis, its base form(s) (lemmas), how often the form and its lemmas occur in a reference corpus and, when available, dictionary definitions.
Your task: rate how well known and used THIS EXACT FORM is by an adult native speaker of {language} today.

Scale (one integer):
5: everyday vocabulary — known to everyone, used in conversation, the press or at school (e.g. {examples_5}).
4: formal or literary vocabulary — understood by a well-read person, met in literature or careful writing, seldom used in speech (e.g. {examples_4}).
3: rare form of an everyday word — the lemma rates 5 but this inflected form is rarely used today: {rare_forms} (e.g. {examples_3}).
2: technical term — vocabulary specific to a trade, a craft or a scientific discipline, known mostly to specialists (e.g. {examples_2}).
1: dated or archaic word — once in use but hardly used any more, except jokingly or in old texts (e.g. {examples_1}).
0: practically unknown word — found only in highly specialised works or very old texts; even a well-read person does not know it (e.g. {examples_0}).

Procedure:
1. Rate the LEMMA alone (5, 4, 2, 1 or 0) by its real use today, taking its most common sense. A common verb stays common whatever its form.
2. Classify the FORM: "rare" if it is {rare_forms}; "common" otherwise ({common_forms}).
3. The rating is 3 if the lemma rates 5 and the form is rare; otherwise it is the lemma's rating (a rare form of a formal, technical, dated or unknown word keeps its lemma's rating).

Notes:
- The form may be written without its accents or diacritics (the source dictionary lost them): this is not a misspelling, judge the word with its accents ({unaccented_example}).
- The corpus counts are only a hint: the corpus (film subtitles, Wikipedia, books, talks) is small, so many well-known words and most inflected forms occur in it rarely or not at all. A high count confirms a word is in current use; a low or zero count proves nothing: never lower a rating only because a count is low, rely on your knowledge of real usage.
- The definitions may be incomplete or list only rare senses; rely first on your knowledge of real usage.
- If several lemmas are possible, rate the most common one.
- A proper noun or an abbreviation is rated by how well known it is.{extra_notes}

Answer with the rating alone: one digit from 0 to 5, nothing else."""


# For `--source multi`: SYSTEM_PROMPT's first procedure step, and an extra
# note.
# `--source multi`: the system prompt of the self-contained check run
# before the rating (`Rater._self_contained`); "N" rates the expression 0.
SELF_CONTAINED_PROMPT = """You are an expert lexicographer of contemporary {language}.
You are given a {language} multi-word sequence taken from a dictionary. Decide whether it is a SELF-CONTAINED expression: one that means something clear and complete on its own, out of any sentence, the way a dictionary headword does — a compound noun, a set phrase or idiom, an adverbial locution, an interjection, a proper name (e.g. {established_examples}).
It is NOT self-contained when it only makes sense inside a longer sentence that gives it its meaning: it lacks the word it is built to introduce or to depend on, or it is a mere grammatical combination with no meaning of its own (e.g. {incomplete_examples}).
Answer Y if it is self-contained, N otherwise. One letter, nothing else."""
MULTI_WORD_NOTES = """
- The expression (words joined by spaces, hyphens or apostrophes; a crossword writes it as one word without them) is rated as a whole, as one unit of the language (idiom, compound noun, set phrase, proper name), not its separate words; its lemma is the expression's base form, and its corpus counts are those of the whole expression."""


def _self_contained_prompt(language):
    with open(DATA_DIR / f"{language}_prompt_config.json", encoding="utf-8") as f:
        cfg = json.load(f)["usage_score"]
    return SELF_CONTAINED_PROMPT.format(
        language=cfg["language_name"],
        incomplete_examples=", ".join(f'"{e}"' for e in cfg["multi_incomplete_examples"]),
        established_examples=", ".join(f'"{e}"' for e in cfg["multi_established_examples"]),
    )


def _llm_settings():
    base_url = os.environ.get("LLM_BASE_URL", DEFAULT_LLM_BASE_URL)
    api_key = os.environ.get("LLM_API_KEY", "EMPTY")
    model = os.environ.get("LLM_MODEL")
    if not model:
        models_url = base_url.rsplit("/chat/completions", 1)[0] + "/models"
        r = httpx.get(models_url, headers={"Authorization": f"Bearer {api_key}"}, timeout=30)
        r.raise_for_status()
        model = r.json()["data"][0]["id"]
    return base_url, model, api_key


def _system_prompt(language, multi=False):
    with open(DATA_DIR / f"{language}_prompt_config.json", encoding="utf-8") as f:
        cfg = json.load(f)["usage_score"]
    examples = {k: ", ".join(v) for k, v in cfg["examples"].items()}
    return SYSTEM_PROMPT.format(
        extra_notes=MULTI_WORD_NOTES if multi else "",
        language=cfg["language_name"],
        rare_forms=cfg["rare_forms"],
        common_forms=cfg["common_forms"],
        unaccented_example=cfg["unaccented_example"],
        **{f"examples_{k}": v for k, v in examples.items()},
    )


def _corpus_counts(language):
    """Lowercase, ligature-folded token -> occurrences in the reference
    corpus, plus the corpus's total token count."""
    counts = Counter()
    with open(CORPUS_DIR / f"{language}_sentences.txt", encoding="utf-8") as f:
        for line in f:
            counts.update(_WORD_RE.findall(fold_ligatures(line.lower())))
    return counts, sum(counts.values())


def _load_entries(language):
    entries = []
    with open(DATA_DIR / f"wordlist_{language}_scrabble.tsv", encoding="utf-8") as f:
        for line in f:
            parts = line.rstrip("\n").split("\t")
            if len(parts) < 3 or not parts[0]:
                continue
            spellings = [s for s in parts[1].split(";") if s] or [parts[0].lower()]
            lemmas = [s for s in parts[2].split(";") if s] or [spellings[0]]
            entries.append((parts[0], spellings, lemmas))
    return entries


def _load_out_entries(language):
    """(MOT, [ACCENTUE], [CANONIQUE...]) of every freq-wordlist word absent
    from the Scrabble wordlist — its highest-FREQUENCE row, like
    `backend/crossword_gen.py`'s `load_wordlist` — in decreasing FREQUENCE,
    then (MOT, [spellings], [lemmas]) of every word of the Hunspell
    wordlist (`_load_hunspell_entries`) neither of them holds."""
    with open(DATA_DIR / f"wordlist_{language}_scrabble.tsv", encoding="utf-8") as f:
        scrabble = {line.split("\t", 1)[0] for line in f}
    best = {}
    with open(DATA_DIR / f"wordlist_{language}_freq.tsv", encoding="utf-8") as f:
        for line in f:
            parts = line.rstrip("\n").split("\t")
            if len(parts) < 4 or not is_grid_form(parts[0].upper()):
                continue
            mot = parts[0].upper()
            if mot in scrabble:
                continue
            try:
                freq = float(parts[2])
            except ValueError:
                freq = 0.0
            if mot not in best or freq > best[mot][0]:
                lemmas = [s for s in parts[3].split(";") if s] or [parts[1]]
                best[mot] = (freq, parts[1], lemmas)
    ranked = sorted(best.items(), key=lambda kv: -kv[1][0])
    entries = [(mot, [accented], lemmas) for mot, (_freq, accented, lemmas) in ranked]
    return entries + [e for e in _load_hunspell_entries(language)
                      if e[0] not in scrabble and e[0] not in best]


def _load_hunspell_entries(language, name=None):
    """(MOT, [spellings], [lemmas]) of data/wordlist_<language>_hunspell.tsv
    (build_wordlist_hunspell.py), or of the `name` file of the same format,
    [] when it does not exist."""
    path = DATA_DIR / (name or f"wordlist_{language}_hunspell.tsv")
    if not path.exists():
        return []
    entries = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            parts = line.rstrip("\n").split("\t")
            if len(parts) < 3 or not parts[0]:
                continue
            spellings = [s for s in parts[1].split(";") if s] or [parts[0].lower()]
            lemmas = [s for s in parts[2].split(";") if s] or [spellings[0]]
            entries.append((parts[0], spellings, lemmas))
    return entries


def _load_multi_entries(language):
    """(MOT, [spellings], [lemmas]) of data/wordlist_<language>_multi.tsv,
    the expressions of data/wordlist_<language>_freq_multi.tsv first (by
    decreasing FREQUENCE), then the others in the file's order."""
    entries = _load_hunspell_entries(language, f"wordlist_{language}_multi.tsv")
    rank = {}
    path = DATA_DIR / f"wordlist_{language}_freq_multi.tsv"
    if path.exists():
        with open(path, encoding="utf-8") as f:
            for i, line in enumerate(f):
                rank.setdefault(line.split("\t", 1)[0], i)
    return sorted(entries, key=lambda e: rank.get(e[0], len(rank)))


def _multi_corpus_counts(language, entries, counts):
    """`counts` plus, for every multi-word spelling or lemma of `entries`,
    its occurrences in the reference corpus, keyed by its lowercase
    letter runs joined by spaces."""
    expressions = {tuple(_WORD_RE.findall(fold_ligatures(t.lower())))
                   for _m, spellings, lemmas in entries for t in spellings + lemmas}
    expressions = {e for e in expressions if len(e) >= 2}
    found = _count_expressions(CORPUS_DIR / f"{language}_sentences.txt", expressions)
    counts = Counter(counts)
    counts.update({" ".join(e): n for e, n in found.items()})
    return counts


class _MultiLookup:
    """Analyses and definitions of multi-word forms and lemmas: the _multi
    inflection table and gloss dictionary, then the single-word ones."""

    def __init__(self, language):
        self.language = language
        self.analyses = {}
        path = DATA_DIR / "inflection" / f"{language}_inflect_multi.jsonl"
        if path.exists():
            with open(path, encoding="utf-8") as f:
                for line in f:
                    rec = json.loads(line)
                    self.analyses[rec["form"]] = [_analysis_text(a, rec["form"]) for a in rec["analyses"]]
        self.glosses = {}
        path = DATA_DIR / "gloss_dictionary" / f"{language}_glosses_multi.jsonl"
        if path.exists():
            with open(path, encoding="utf-8") as f:
                for line in f:
                    rec = json.loads(line)
                    self.glosses[fold_ligatures(rec["word"].lower())] = rec["entries"]

    def describe_form(self, form, language):
        found = self.analyses.get(fold_ligatures(form.lower()))
        if found is not None:
            return [(None, text) for text in found]
        return inflection_lookup.describe_form(form, language)

    def find_glosses(self, lemmas, language):
        result = {}
        for lemma in lemmas:
            entries = self.glosses.get(fold_ligatures(lemma.lower()))
            if entries:
                result[lemma] = entries
        rest = [lemma for lemma in lemmas if lemma not in result]
        if rest:
            result.update(gloss_lookup.find_glosses_for_canonicals(rest, language))
        return result


def _analysis_text(analysis, form):
    """`inflection_lookup`'s description of one analysis row."""
    bits = []
    pos = analysis.get("pos")
    if pos:
        bits.append(inflection_lookup._POS_LABEL.get(pos, pos))
    if analysis.get("tags"):
        bits.append(analysis["tags"])
    text = ", ".join(bits)
    lemma = analysis.get("lemma")
    if lemma and fold_ligatures(lemma.lower()) != form:
        text += f' (of "{lemma}")'
    return text


def _frequency_text(word, counts, total):
    n = counts.get(" ".join(_WORD_RE.findall(fold_ligatures(word.lower()))), 0)
    if not n:
        return "0 occurrences"
    return f"{n} occurrences ({n * 1_000_000 / total:.2f} per million words)"


def _entry_text(_mot, spellings, lemmas, language, counts, total, lookup=None):
    lemmas = lemmas[:MAX_LEMMAS]
    lines = [f"{'Expression' if lookup else 'Inflected form'}: {spellings[0]}"]
    if len(spellings) > 1:
        lines.append(f"Other possible spellings: {', '.join(spellings[1:])}")
    lines.append(f"Lemma(s): {' ; '.join(lemmas)}")

    analyses = []
    for sp in spellings:
        for _pos, desc in (lookup or inflection_lookup).describe_form(sp, language):
            if desc not in analyses:
                analyses.append(desc)
    if analyses:
        lines.append("Grammatical analysis (number, gender, conjugation):")
        lines += [f"- {a}" for a in analyses[:MAX_ANALYSES]]
    else:
        lines.append("Grammatical analysis: unknown (form missing from the inflection table)")

    lines.append(f"Corpus frequency ({total // 1_000_000} million words):")
    for sp in spellings:
        lines.append(f"- form \"{sp}\": {_frequency_text(sp, counts, total)}")
    for lemma in lemmas:
        if lemma.lower() not in (s.lower() for s in spellings):
            lines.append(f"- lemma \"{lemma}\": {_frequency_text(lemma, counts, total)}")

    glosses = (lookup.find_glosses(lemmas, language) if lookup
               else gloss_lookup.find_glosses_for_canonicals(lemmas, language))
    if glosses:
        lines.append("Definitions:")
        for lemma, items in glosses.items():
            n = 0
            for item in items:
                for g in item.get("glosses", []):
                    if n >= MAX_GLOSSES_PER_LEMMA:
                        break
                    if len(g) > MAX_GLOSS_CHARS:
                        g = g[:MAX_GLOSS_CHARS].rstrip() + "…"
                    lines.append(f"- {lemma} ({item.get('pos', '?')}): {g}")
                    n += 1
    else:
        lines.append("Definitions: none found")
    return "\n".join(lines)


_DIGIT_RE = re.compile(r"[0-5]")


class Rater:
    def __init__(self, language, counts, total, lookup=None):
        # Built once here: the lookup modules' lazy, unlocked caches would
        # otherwise be built by every worker thread at once.
        inflection_lookup.describe_form("", language)
        gloss_lookup.find_glosses_for_canonicals([], language)
        self.language = language
        self.counts = counts
        self.total = total
        self.lookup = lookup
        self.system_prompt = _system_prompt(language, multi=lookup is not None)
        self.self_contained_prompt = _self_contained_prompt(language) if lookup is not None else None
        self.base_url, self.model, api_key = _llm_settings()
        self.client = httpx.Client(headers={"Authorization": f"Bearer {api_key}"},
                                   timeout=REQUEST_TIMEOUT_S)

    def _ask(self, entry, self_contained=False):
        if self_contained:
            messages = [{"role": "system", "content": self.self_contained_prompt},
                        {"role": "user", "content": f"Sequence: {entry[1][0]}"}]
        else:
            messages = [{"role": "system", "content": self.system_prompt},
                        {"role": "user", "content": _entry_text(*entry, self.language,
                                                                self.counts, self.total,
                                                                self.lookup)}]
        payload = {
            "model": self.model,
            # The system prompt, identical for every word, comes first so
            # the LLM server's prefix (KV) cache reuses it.
            "messages": messages,
            "temperature": 0.0,
            "max_tokens": 1,
            "chat_template_kwargs": {"enable_thinking": False},
        }
        r = self.client.post(self.base_url, json=payload)
        r.raise_for_status()
        return r.json()["choices"][0]["message"]["content"] or ""

    def _self_contained(self, entry):
        """False when the LLM answers that the expression does not make
        sense out of a sentence (`SELF_CONTAINED_PROMPT`), True otherwise
        (an unclear or failed answer keeps it)."""
        for attempt in range(MAX_ATTEMPTS):
            try:
                answer = self._ask(entry, self_contained=True).strip().upper()
            except (httpx.HTTPError, KeyError, ValueError) as exc:
                print(f"  {entry[0]}: request failed ({exc}), retrying", file=sys.stderr)
                time.sleep(2 * (attempt + 1))
                continue
            if answer[:1] in ("Y", "N"):
                return answer[:1] == "Y"
        return True

    def rate(self, entry):
        """The usage score of `entry` (MOT, spellings, lemmas), `None` when
        no answer holds a digit from 0 to 5 after `MAX_ATTEMPTS`; 0 for an
        expression the self-contained check rejects (`--source multi`)."""
        if self.self_contained_prompt and not self._self_contained(entry):
            return 0
        for attempt in range(MAX_ATTEMPTS):
            try:
                m = _DIGIT_RE.search(self._ask(entry))
            except (httpx.HTTPError, KeyError, ValueError) as exc:
                print(f"  {entry[0]}: request failed ({exc}), retrying", file=sys.stderr)
                time.sleep(2 * (attempt + 1))
                continue
            if m:
                return int(m.group(0))
        return None


def _read_partial(path):
    """{MOT: (ACCENTUE, SCORE)} of a partial or final score file."""
    done = {}
    if path.exists():
        with open(path, encoding="utf-8") as f:
            for line in f:
                parts = line.rstrip("\n").split("\t")
                if len(parts) >= 3 and parts[2].isdigit():
                    done[parts[0]] = (parts[1], int(parts[2]))
    return done


def _write_sorted(dst, scores):
    rows = sorted(scores.items(), key=lambda kv: (-kv[1][1], kv[0]))
    tmp = dst.with_suffix(".tsv.tmp")
    with open(tmp, "w", encoding="utf-8") as out:
        for mot, (accented, score) in rows:
            out.write(f"{mot}\t{accented}\t{score}\n")
    tmp.replace(dst)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("language", choices=["de", "en", "es", "fr", "it", "pt"])
    ap.add_argument("--source", choices=["scrabble", "out", "multi"], default="scrabble",
                    help="scrabble: the Scrabble wordlist; out: the freq-wordlist words absent from it; "
                         "multi: the multi-word expressions")
    ap.add_argument("--concurrency", type=int, default=CONCURRENCY, help="LLM requests in flight")
    ap.add_argument("--limit", type=int, help="rate at most N more words, then stop (no final file)")
    ap.add_argument("--words", nargs="+", help="rate only these MOTs and print the results, writing nothing")
    ap.add_argument("--show-prompt", action="store_true", help="print the prompt of the first word")
    args = ap.parse_args()
    lang = args.language

    print(f"Counting corpus tokens ({lang})...", flush=True)
    counts, total = _corpus_counts(lang)
    lookup = None
    if args.source == "multi":
        entries = _load_multi_entries(lang)
        print(f"Counting {len(entries)} expressions in the corpus...", flush=True)
        counts = _multi_corpus_counts(lang, entries, counts)
        lookup = _MultiLookup(lang)
    else:
        entries = _load_entries(lang) if args.source == "scrabble" else _load_out_entries(lang)
    rater = Rater(lang, counts, total, lookup)
    print(f"{len(entries)} words, {total} corpus tokens, model {rater.model}", flush=True)

    if args.show_prompt:
        print(rater.system_prompt)
        print("-----")
        print(_entry_text(*entries[0], lang, counts, total, lookup))
        print("-----")

    if args.words:
        wanted = {w.upper() for w in args.words}
        todo = [e for e in entries if e[0] in wanted]
        with ThreadPoolExecutor(args.concurrency) as pool:
            for entry, score in zip(todo, pool.map(rater.rate, todo)):
                print(f"{entry[0]}\t{score}")
        missing = wanted - {e[0] for e in todo}
        if missing:
            print(f"Not in the {args.source} word set: {' '.join(sorted(missing))}")
        return

    dst = DATA_DIR / {"scrabble": f"usage_score_{lang}_scrabble.tsv",
                      "out": f"usage_score_{lang}_scrabble_out.tsv",
                      "multi": f"usage_score_{lang}_multi.tsv"}[args.source]
    partial_path = dst.with_suffix(".tsv.partial")
    accented_of = {m: s[0] for m, s, _ in entries}
    # Words already in the final file keep their score; the partial file
    # holds the ones rated since.
    scores = {m: v for m, v in {**_read_partial(dst), **_read_partial(partial_path)}.items()
              if m in accented_of}
    todo = [e for e in entries if e[0] not in scores]
    if args.limit is not None:
        todo = todo[:args.limit]
    print(f"{len(scores)} already rated, {len(todo)} to rate", flush=True)

    started = time.time()
    rated = failed = 0
    with open(partial_path, "a", encoding="utf-8") as partial, \
            ThreadPoolExecutor(args.concurrency) as pool:
        futures = {pool.submit(rater.rate, e): e[0] for e in todo}
        for i, fut in enumerate(as_completed(futures), 1):
            mot = futures[fut]
            score = fut.result()
            if score is None:
                failed += 1
            else:
                scores[mot] = (accented_of[mot], score)
                partial.write(f"{mot}\t{accented_of[mot]}\t{score}\n")
                partial.flush()
                rated += 1
            if i % 500 == 0 or i == len(todo):
                elapsed = time.time() - started
                rate = rated / elapsed if elapsed else 0
                left = (len(todo) - i) / rate if rate else 0
                print(f"  {i}/{len(todo)} words ({failed} unanswered), "
                      f"{rate:.1f} words/s, ~{left / 3600:.1f} h left", flush=True)

    missing = [m for m, _s, _l in entries if m not in scores]
    if missing:
        print(f"{len(missing)} words still unrated — re-run to complete {dst.name}"
              + (f" (e.g. {' '.join(missing[:10])})" if failed else ""))
        return
    _write_sorted(dst, scores)
    partial_path.unlink()
    print(f"{len(scores)} words written to {dst}")


if __name__ == "__main__":
    main()
