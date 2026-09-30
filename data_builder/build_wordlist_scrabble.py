#!/usr/bin/env python3
"""
Builds data/wordlist_<lang>_scrabble.tsv (MOT<TAB>ACCENTUE<TAB>CANONIQUE)
from the language's Scrabble word list(s) (data/scrabble/<lang>/*.txt,
downloaded by download_scrabble_dictionaries.py) — the same columns as
data/wordlist_<lang>_freq.tsv minus FREQUENCE.

- MOT is the entry's grid form, the wordlist's own convention
  (build_wordlist_freq.py's `strip_accents`, then uppercased — so German
  "ß" becomes "SS"); an entry left with anything else than A-Z, or
  shorter than 2 letters, is skipped. Every file of a language folder is
  merged, one row per distinct MOT.
- Most Scrabble lists are written without accents (ODS8, FILE/FISE,
  Zingarelli, SOWPODS/TWL) and all are lowercase, so ACCENTUE (the natural
  spelling: accents and capitalization) and CANONIQUE (the lemma(s),
  `;`-separated) are recovered, in this order of preference:
  1. the row of data/wordlist_<lang>_freq.tsv with the same MOT;
  2. the English-Wiktionary Kaikki dump of the language (data/wiktionary/<Name>-en.
     jsonl.gz, shared with build_inflections.py, downloaded if missing):
     every headword and every inflected form listed under it (`forms`,
     or a `form-of` sense) with its lemma. Among several spellings with
     the same MOT, the one equal to the entry itself (case aside) wins,
     then a lowercase one, then the alphabetically first;
  3. Hunspell (build_wordlist_freq.py's dictionaries): the entry as-is or
     title-cased (German nouns), its lemma(s) from `hunspell -m`;
  4. Hunspell's spelling suggestions (`hunspell -a`, one process per CPU
     over chunks of the remaining entries): the first suggestion with the
     same MOT restores the accents ("cheriez" -> "chériez"), its lemma(s)
     again from `hunspell -m`;
  5. the entry itself, as its own lemma.

Usage:
    python3 data_builder/build_wordlist_scrabble.py fr
"""
import argparse
import concurrent.futures
import gzip
import json
import os
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import build_inflections  # noqa: E402
import build_wordlist_freq  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
SCRABBLE_DIR = ROOT / "data" / "scrabble"
WORDLIST_DIR = ROOT / "data"
LANGUAGES = ("fr", "en", "de", "es", "it", "pt")


def grid_form(word):
    """MOT of one entry, or None (see the module docstring)."""
    form = build_wordlist_freq.strip_accents(word.strip()).upper()
    if len(form) < 2 or not all("A" <= c <= "Z" for c in form):
        return None
    return form


def _scrabble_entries(lang):
    """{MOT: raw entry} over every .txt file of the language, first entry
    kept."""
    entries = {}
    for path in sorted((SCRABBLE_DIR / lang).glob("*.txt")):
        with open(path, encoding="utf-8") as f:
            for line in f:
                raw = line.strip()
                form = grid_form(raw) if raw else None
                if form and form not in entries:
                    entries[form] = raw
    return entries


def _freq_rows(lang):
    """{MOT: (ACCENTUE, CANONIQUE)} from wordlist_<lang>_freq.tsv."""
    rows = {}
    with open(WORDLIST_DIR / f"wordlist_{lang}_freq.tsv", encoding="utf-8") as f:
        for line in f:
            parts = line.rstrip("\n").split("\t")
            if len(parts) >= 4:
                rows[parts[0]] = (parts[1], parts[3] or parts[1])
    return rows


def _kaikki_forms(lang, wanted):
    """{MOT: {spelling: set(lemmas)}} for the MOTs in `wanted`, read from
    the Kaikki dump: headwords (their own lemma) and their inflected
    forms."""
    found = {}

    def add(spelling, lemma):
        if not spelling or " " in spelling:
            return
        form = grid_form(spelling)
        if form is None or form not in wanted:
            return
        lemmas = found.setdefault(form, {}).setdefault(spelling, set())
        if lemma:
            lemmas.add(lemma)

    dump = build_inflections._download_dump(lang)
    with gzip.open(dump, "rt", encoding="utf-8") as fh:
        for line in fh:
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                continue
            word = entry.get("word") or ""
            lemma_of_word = word
            for sense in entry.get("senses", []):
                fo = sense.get("form_of") or []
                if "form-of" in (sense.get("tags") or []) and fo and isinstance(fo[0], dict):
                    lemma_of_word = fo[0].get("word") or word
                    break
            add(word, lemma_of_word)
            for f in entry.get("forms") or []:
                tags = f.get("tags") or []
                if "table-tags" in tags or "inflection-template" in tags or "romanization" in tags:
                    continue
                add(f.get("form") or "", word)
    return found


def _pick_spelling(raw, spellings):
    """The spelling kept among several with the same MOT (see the module
    docstring, step 2)."""
    return sorted(
        spellings,
        key=lambda s: (s.lower() != raw.lower(), s != s.lower(), s),
    )[0]


def _hunspell_suggestions(lang, raws):
    """{raw: spelling} for each of `raws` whose Hunspell suggestions hold
    one with the same MOT (step 4 of the module docstring)."""
    if not raws or lang not in build_wordlist_freq.HUNSPELL_SOURCE:
        return {}
    try:
        dict_basename = build_wordlist_freq._fetch_hunspell(lang)
    except OSError:
        return {}
    encoding = build_wordlist_freq.HUNSPELL_ENCODING.get(lang, "utf-8")
    workers = os.cpu_count() or 1
    chunks = [raws[k::workers] for k in range(workers)]

    def run(chunk):
        found = {}
        if not chunk:
            return found
        result = subprocess.run(
            ["hunspell", "-d", dict_basename, "-i", encoding, "-a"],
            input="\n".join(chunk).encode(encoding, errors="replace"),
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
        )
        for line in result.stdout.decode(encoding, errors="replace").splitlines():
            if not line.startswith("& ") or ": " not in line:
                continue
            head, suggestions = line.split(": ", 1)
            raw = head.split(" ")[1]
            target = grid_form(raw)
            for suggestion in suggestions.split(", "):
                if grid_form(suggestion) == target:
                    found[raw] = suggestion
                    break
        return found

    merged = {}
    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as pool:
        for found in pool.map(run, chunks):
            merged.update(found)
    return merged


def build(lang):
    entries = _scrabble_entries(lang)
    freq = _freq_rows(lang)
    rows = {}
    for form, raw in entries.items():
        if form in freq:
            rows[form] = freq[form]
    missing = {form for form in entries if form not in rows}
    print(f"{lang}: {len(entries)} Scrabble words, {len(rows)} found in the freq wordlist",
          file=sys.stderr)

    kaikki = _kaikki_forms(lang, missing)
    for form in list(missing):
        spellings = kaikki.get(form)
        if not spellings:
            continue
        spelling = _pick_spelling(entries[form], spellings)
        lemmas = sorted(spellings[spelling]) or [spelling]
        rows[form] = (spelling, ";".join(lemmas))
        missing.discard(form)
    print(f"{lang}: {len(entries) - len(missing) - len(freq.keys() & entries.keys())} "
          f"more from the Kaikki dump, {len(missing)} left", file=sys.stderr)

    raws = [entries[form] for form in sorted(missing)]
    valid = build_wordlist_freq._spellcheck_valid(lang, raws) or {}
    restored = _hunspell_suggestions(lang, [r for r in raws if r not in valid])
    spelled = {**restored, **valid}
    stems = build_wordlist_freq._stem_map(lang, sorted(set(spelled.values())))
    for form in sorted(missing):
        raw = entries[form]
        accented = spelled.get(raw)
        if accented is not None:
            lemmas = list(dict.fromkeys(stems.get(accented, []))) or [accented]
        else:
            accented, lemmas = raw, [raw]
        rows[form] = (accented, ";".join(lemmas))
    print(f"{lang}: {len(valid)} validated by Hunspell, {len(restored)} restored from its "
          f"suggestions, {len(missing) - len(spelled)} kept as written", file=sys.stderr)

    dst = WORDLIST_DIR / f"wordlist_{lang}_scrabble.tsv"
    with open(dst, "w", encoding="utf-8") as out:
        for form in sorted(rows):
            accented, canonical = rows[form]
            canonical = ";".join(dict.fromkeys(
                build_wordlist_freq.fold_ligatures(c) for c in canonical.split(";") if c))
            out.write(f"{form}\t{build_wordlist_freq.fold_ligatures(accented)}\t{canonical}\n")
    print(f"{len(rows)} words written to {dst}")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("language", choices=LANGUAGES)
    build(ap.parse_args().language)


if __name__ == "__main__":
    main()
