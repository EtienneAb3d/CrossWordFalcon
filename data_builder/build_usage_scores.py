#!/usr/bin/env python3
"""
Rates how common every word of data/wordlist_<lang>_scrabble.tsv is, with
the local LLM, and writes data/usage_score_<lang>_scrabble.tsv
(`MOT<TAB>ACCENTUE<TAB>SCORE`, sorted by decreasing SCORE, then MOT).
With `--source out`, rates instead every word of
data/wordlist_<lang>_freq.tsv absent from the Scrabble wordlist (its
highest-FREQUENCE row per MOT: ACCENTUE as the form, CANONIQUE as the
lemmas) and writes data/usage_score_<lang>_scrabble_out.tsv, same format.

Scale (SCORE):
  5  everyday vocabulary
  4  formal or literary vocabulary
  3  rare form of an everyday word (French: imperfect/pluperfect
     subjunctive, first/second-person simple past...)
  2  technical term of a trade or a scientific discipline
  1  dated or archaic word
  0  practically unknown word

The LLM is given, per word: the inflected form (every spelling of the
ACCENTUE column), its grammatical analysis (data/inflection/<lang>.jsonl,
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
score. A word with no digit from 0 to 5 in its answer after
`MAX_ATTEMPTS` is left out (reported) — a re-run picks it up.

Needs the local LLM (./run_llm.sh): LLM_BASE_URL/LLM_MODEL/LLM_API_KEY,
like backend/clues.py; without LLM_MODEL, the first model the server
lists is used.

Usage:
    python3 data_builder/build_usage_scores.py fr
    python3 data_builder/build_usage_scores.py fr --words MAISON FUSSIEZ   # print only
    python3 data_builder/build_usage_scores.py fr --source out
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
from build_wordlist_freq import _WORD_RE, fold_ligatures  # noqa: E402
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
- A proper noun or an abbreviation is rated by how well known it is.

Answer with the rating alone: one digit from 0 to 5, nothing else."""


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


def _system_prompt(language):
    with open(DATA_DIR / f"{language}_prompt_config.json", encoding="utf-8") as f:
        cfg = json.load(f)["usage_score"]
    examples = {k: ", ".join(v) for k, v in cfg["examples"].items()}
    return SYSTEM_PROMPT.format(
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
    `backend/crossword_gen.py`'s `load_wordlist` — in decreasing FREQUENCE."""
    with open(DATA_DIR / f"wordlist_{language}_scrabble.tsv", encoding="utf-8") as f:
        scrabble = {line.split("\t", 1)[0] for line in f}
    best = {}
    with open(DATA_DIR / f"wordlist_{language}_freq.tsv", encoding="utf-8") as f:
        for line in f:
            parts = line.rstrip("\n").split("\t")
            if len(parts) < 4 or not parts[0].isalpha():
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
    return [(mot, [accented], lemmas) for mot, (_freq, accented, lemmas) in ranked]


def _frequency_text(word, counts, total):
    n = counts.get(word.lower(), 0)
    if not n:
        return "0 occurrences"
    return f"{n} occurrences ({n * 1_000_000 / total:.2f} per million words)"


def _entry_text(_mot, spellings, lemmas, language, counts, total):
    lemmas = lemmas[:MAX_LEMMAS]
    lines = [f"Inflected form: {spellings[0]}"]
    if len(spellings) > 1:
        lines.append(f"Other possible spellings: {', '.join(spellings[1:])}")
    lines.append(f"Lemma(s): {' ; '.join(lemmas)}")

    analyses = []
    for sp in spellings:
        for _pos, desc in inflection_lookup.describe_form(sp, language):
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

    glosses = gloss_lookup.find_glosses_for_canonicals(lemmas, language)
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
    def __init__(self, language, counts, total):
        # Built once here: the lookup modules' lazy, unlocked caches would
        # otherwise be built by every worker thread at once.
        inflection_lookup.describe_form("", language)
        gloss_lookup.find_glosses_for_canonicals([], language)
        self.language = language
        self.counts = counts
        self.total = total
        self.system_prompt = _system_prompt(language)
        self.base_url, self.model, api_key = _llm_settings()
        self.client = httpx.Client(headers={"Authorization": f"Bearer {api_key}"},
                                   timeout=REQUEST_TIMEOUT_S)

    def _ask(self, entry):
        payload = {
            "model": self.model,
            # The system prompt, identical for every word, comes first so
            # the LLM server's prefix (KV) cache reuses it.
            "messages": [{"role": "system", "content": self.system_prompt},
                         {"role": "user", "content": _entry_text(*entry, self.language,
                                                                 self.counts, self.total)}],
            "temperature": 0.0,
            "max_tokens": 1,
            "chat_template_kwargs": {"enable_thinking": False},
        }
        r = self.client.post(self.base_url, json=payload)
        r.raise_for_status()
        return r.json()["choices"][0]["message"]["content"] or ""

    def rate(self, entry):
        """The usage score of `entry` (MOT, spellings, lemmas), `None` when
        no answer holds a digit from 0 to 5 after `MAX_ATTEMPTS`."""
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
    ap.add_argument("--source", choices=["scrabble", "out"], default="scrabble",
                    help="scrabble: the Scrabble wordlist; out: the freq-wordlist words absent from it")
    ap.add_argument("--concurrency", type=int, default=CONCURRENCY, help="LLM requests in flight")
    ap.add_argument("--limit", type=int, help="rate at most N more words, then stop (no final file)")
    ap.add_argument("--words", nargs="+", help="rate only these MOTs and print the results, writing nothing")
    ap.add_argument("--show-prompt", action="store_true", help="print the prompt of the first word")
    args = ap.parse_args()
    lang = args.language

    print(f"Counting corpus tokens ({lang})...", flush=True)
    counts, total = _corpus_counts(lang)
    entries = _load_entries(lang) if args.source == "scrabble" else _load_out_entries(lang)
    rater = Rater(lang, counts, total)
    print(f"{len(entries)} words, {total} corpus tokens, model {rater.model}", flush=True)

    if args.show_prompt:
        print(rater.system_prompt)
        print("-----")
        print(_entry_text(*entries[0], lang, counts, total))
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

    suffix = "" if args.source == "scrabble" else "_out"
    dst = DATA_DIR / f"usage_score_{lang}_scrabble{suffix}.tsv"
    partial_path = dst.with_suffix(".tsv.partial")
    scores = _read_partial(partial_path)
    accented_of = {m: s[0] for m, s, _ in entries}
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
