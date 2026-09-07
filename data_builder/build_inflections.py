#!/usr/bin/env python3
"""data_builder/build_inflections.py — extracts a pure-local table of the
grammatical description (part of speech + person / number / gender /
tense / mood) of every inflected form the app might need to clue.

Source: the Kaikki.org "Machine-readable <Language> dictionary" bulk dump
— specifically the **English-Wiktionary edition** for every language
(fr/en/de/es/it/pt), because its `senses[].tags` tag inflection with a
consistent, language-neutral vocabulary (`["form-of", "future",
"singular", "third-person"]`) where the own-language editions
`build_gloss_dictionary.py` uses for definitions mostly leave the detail
in prose. These dumps are small (~55-95 MB gzipped) compared to the
own-language ones, and are cached under DICS/ like every other raw
download in this pipeline.

Output: `data/inflection/<lang>.jsonl` — a plain-text (uncompressed, so
it can be inspected by hand and still fits well within GitHub's file-size
limits) JSON-lines file, one object per line, sorted by form:

    {"form": "humera", "analyses": [
        {"pos": "verb", "tags": "third-person singular future", "lemma": "humer"}]}

filtered to just the surface forms present in
`data/wordlist_<lang>_full.tsv` (the app only ever looks these up: every
grid word comes from that file), which keeps each language's file to a
few MB up to ~20 MB. Read at runtime by `backend/inflection_lookup.py`.

Usage:
    .venv/bin/python data_builder/build_inflections.py fr
    .venv/bin/python data_builder/build_inflections.py de --force
"""
import argparse
import gzip
import json
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DICS_DIR = ROOT / "DICS"
WORDLIST_DIR = ROOT / "data"
OUT_DIR = ROOT / "data" / "inflection"

# English-Wiktionary-edition Kaikki page name per language code.
DUMP_NAME = {
    "fr": "French", "en": "English", "de": "German",
    "es": "Spanish", "it": "Italian", "pt": "Portuguese",
}
BASE_URL = "https://kaikki.org/dictionary"

# Grammatical tags kept, grouped and emitted in
# person -> number -> gender -> tense -> mood -> non-finite order. Every
# other tag Kaikki attaches to a form-of sense ("form-of" itself,
# "historic"/"rare"/"archaic"/"morpheme", "error-*", ...) is dropped.
# MUST stay in sync with backend/inflection_lookup.py's copy.
_TAG_GROUPS = (
    ("first-person", "second-person", "third-person", "impersonal"),
    ("singular", "plural", "dual"),
    ("masculine", "feminine", "neuter", "common-gender"),
    ("present", "past", "future", "imperfect", "preterite", "perfect",
     "pluperfect", "aorist", "future-perfect"),
    ("indicative", "subjunctive", "conditional", "imperative",
     "optative", "jussive"),
    ("infinitive", "participle", "gerund", "supine", "converb"),
)
_TAG_ORDER = [t for group in _TAG_GROUPS for t in group]


def _download_dump(lang):
    """Downloads (once, cached under DICS/) the English-edition dump for
    `lang` and returns its local path."""
    name = DUMP_NAME[lang]
    dst = DICS_DIR / f"{name}-en.jsonl.gz"
    DICS_DIR.mkdir(parents=True, exist_ok=True)
    if not dst.exists():
        url = f"{BASE_URL}/{name}/kaikki.org-dictionary-{name}.jsonl.gz"
        print(f"Downloading {url} ...", file=sys.stderr)
        urllib.request.urlretrieve(url, dst)
    return dst


def _wordlist_forms(lang):
    """Every ACCENTED spelling (2nd column) in the language's wordlist,
    lowercased — the inflection table is filtered to just these."""
    path = WORDLIST_DIR / f"wordlist_{lang}_full.tsv"
    forms = set()
    with open(path, encoding="utf-8") as f:
        for line in f:
            parts = line.rstrip("\n").split("\t")
            if len(parts) >= 2 and parts[1]:
                forms.add(parts[1].lower())
    return forms


def build(lang):
    dump = _download_dump(lang)
    keep = _wordlist_forms(lang)
    print(f"{lang}: {len(keep)} wordlist forms to match against {dump.name}",
          file=sys.stderr)

    table = {}          # form_lower -> [{"pos":.., "tags":.., "lemma":..}, ...]
    seen = set()        # (form, pos, tags_str, lemma) — dedup identical rows
    scanned = matched = 0
    with gzip.open(dump, "rt", encoding="utf-8") as fh:
        for line in fh:
            scanned += 1
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                continue
            form = (entry.get("word") or "").lower()
            if not form or form not in keep:
                continue
            pos = entry.get("pos") or None
            for sense in entry.get("senses", []):
                tags = sense.get("tags") or []
                if "form-of" not in tags:
                    continue
                gram = " ".join(t for t in _TAG_ORDER if t in tags)
                fo = sense.get("form_of") or []
                lemma = (
                    fo[0].get("word")
                    if fo and isinstance(fo[0], dict) else None
                )
                if not pos and not gram:
                    continue
                key = (form, pos, gram, lemma)
                if key in seen:
                    continue
                seen.add(key)
                table.setdefault(form, []).append(
                    {"pos": pos, "tags": gram, "lemma": lemma}
                )
                matched += 1

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    out = OUT_DIR / f"{lang}.jsonl"
    with open(out, "w", encoding="utf-8") as w:
        for form in sorted(table):
            w.write(json.dumps({"form": form, "analyses": table[form]},
                               ensure_ascii=False) + "\n")
    print(
        f"{lang}: scanned {scanned} entries, {matched} form-of rows for "
        f"{len(table)} distinct forms -> {out} "
        f"({out.stat().st_size / 1_000_000:.1f} MB)",
        file=sys.stderr,
    )


def main():
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    ap.add_argument("language", choices=sorted(DUMP_NAME))
    ap.add_argument(
        "--force", action="store_true",
        help="re-download the dump even if it is already cached under DICS/",
    )
    args = ap.parse_args()
    if args.force:
        cached = DICS_DIR / f"{DUMP_NAME[args.language]}-en.jsonl.gz"
        cached.unlink(missing_ok=True)
    build(args.language)
    return 0


if __name__ == "__main__":
    sys.exit(main())
