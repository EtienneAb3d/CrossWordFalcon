#!/usr/bin/env python3
"""
Builds data/wordlist_<lang>_scrabble.tsv (MOT<TAB>ACCENTUE<TAB>CANONIQUE)
from the language's Scrabble word list(s) (data/scrabble/<lang>/*.txt,
downloaded by download_scrabble_dictionaries.py) — the same columns as
data/wordlist_<lang>_freq.tsv minus FREQUENCE, ACCENTUE listing every
possible spelling.

- MOT is the entry's grid form, the wordlist's own convention
  (build_wordlist_freq.py's `strip_accents`, then uppercased — so German
  "ß" becomes "SS"); an entry left with anything else than A-Z, or
  shorter than 2 letters, is skipped. Every file of a language folder is
  merged, one row per distinct MOT.
- Most Scrabble lists are written without accents (ODS8, FILE/FISE,
  Zingarelli, SOWPODS/TWL) and all are lowercase: an entry's accents are
  unknown, so every source is searched "impoverished", accents aside, and
  their answers are combined, each source being possibly incomplete. The
  spellings of an entry are every spelling its accents alone tell apart
  from it (`accent_variant`: same letters and case once accents are
  stripped; German: same MOT, any case, nouns being capitalized), found
  in:
  1. the row of data/wordlist_<lang>_freq.tsv with the same MOT (its
     ACCENTUE and CANONIQUE);
  2. the English-Wiktionary Kaikki dump of the language (data/wiktionary/
     <Name>-en.jsonl.gz, shared with build_inflections.py, downloaded if
     missing): every headword and every inflected form listed under it
     (`forms`, or a `form-of` sense) with its lemma (`_kaikki_forms`);
  3. Hunspell (build_wordlist_freq.py's dictionaries): the entry as-is or
     title-cased (German nouns);
  4. Hunspell's spelling suggestions (`hunspell -a`, one process per CPU
     over chunks of the entries): every suggestion with the same MOT
     ("macheriez" -> "mâcheriez", `_hunspell_suggestions`).
  Every spelling found also gets its lemma(s) from `hunspell -m`.
  ACCENTUE lists every spelling found, `;`-separated, the main one first:
  the first found in that order of sources (in the dump, `_pick_spelling`:
  the one equal to the entry, case aside, then a lowercase one, then the
  alphabetically first), the entry itself when none is — the runtime
  takes that one as the word's natural spelling. CANONIQUE is the union
  of the lemmas of every spelling, those of the main one first
  ("MACERONS  macerons;macérons  maceron;macérer", from the noun
  "maceron" and the verb form "macérons"), the main spelling itself when
  none is known.

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
from build_wordlist_freq import strip_accents  # noqa: E402

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
    the Kaikki dump: headwords (their own lemma, or the one their form-of
    sense names) and the forms they list (with that same lemma)."""
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
                # A form listed under an inflected entry (an alternative
                # spelling: "macèrerions" under "macérerions") shares
                # that entry's lemma, never the entry itself.
                add(f.get("form") or "", lemma_of_word)
    return found


def _pick_spelling(raw, spellings):
    """The spelling kept among several with the same MOT (see the module
    docstring, step 2)."""
    return sorted(
        spellings,
        key=lambda s: (s.lower() != raw.lower(), s != s.lower(), s),
    )[0]


def accent_variant(lang, raw, spelling):
    """True when `spelling` is the entry `raw` up to its accents (see the
    module docstring)."""
    spelling = build_wordlist_freq.fold_ligatures(spelling)
    if lang == "de":
        return grid_form(spelling) == grid_form(raw)
    return strip_accents(spelling) == strip_accents(raw)


def _hunspell_suggestions(lang, raws):
    """{raw: [spelling, ...]}: every Hunspell suggestion of each of `raws`
    with the same MOT (step 4 of the module docstring)."""
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
            matching = [s for s in suggestions.split(", ") if grid_form(s) == target]
            if matching:
                found[raw] = matching
        return found

    merged = {}
    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as pool:
        for found in pool.map(run, chunks):
            merged.update(found)
    return merged


def build(lang):
    entries = _scrabble_entries(lang)
    freq = _freq_rows(lang)
    # MOT -> {spelling: set(lemmas)}, spellings in order of discovery.
    spellings = {form: {} for form in entries}
    accented = {}

    def add(form, spelling, lemmas=()):
        spelling = build_wordlist_freq.fold_ligatures(spelling)
        spellings[form].setdefault(spelling, set()).update(
            build_wordlist_freq.fold_ligatures(l) for l in lemmas if l)

    for form, raw in entries.items():
        if form in freq:
            spelling, canonical = freq[form]
            add(form, spelling, canonical.split(";"))
            accented[form] = build_wordlist_freq.fold_ligatures(spelling)
    print(f"{lang}: {len(entries)} Scrabble words, {len(accented)} found in the freq wordlist",
          file=sys.stderr)

    kaikki = _kaikki_forms(lang, set(entries))
    from_kaikki = 0
    for form, found in kaikki.items():
        raw = entries[form]
        variants = {s: l for s, l in found.items() if accent_variant(lang, raw, s)}
        if not variants:
            continue
        from_kaikki += 1
        if form not in accented:
            accented[form] = build_wordlist_freq.fold_ligatures(_pick_spelling(raw, variants))
            add(form, accented[form])
        for spelling in sorted(variants):
            add(form, spelling, variants[spelling])
    print(f"{lang}: {from_kaikki} found in the Kaikki dump", file=sys.stderr)

    raws = [entries[form] for form in sorted(entries)]
    valid = build_wordlist_freq._spellcheck_valid(lang, raws) or {}
    suggested = _hunspell_suggestions(lang, [r for r in raws if r not in valid])
    for form in sorted(entries):
        raw = entries[form]
        found = ([valid[raw]] if raw in valid else []) + suggested.get(raw, [])
        for spelling in found:
            if form not in accented:
                accented[form] = build_wordlist_freq.fold_ligatures(spelling)
                add(form, accented[form])
            add(form, spelling)
    print(f"{lang}: {len(valid)} validated by Hunspell, {len(suggested)} with suggestions",
          file=sys.stderr)

    stems = build_wordlist_freq._stem_map(
        lang, sorted({s for found in spellings.values() for s in found}))
    rows = {}
    for form, raw in entries.items():
        found = spellings[form]
        for spelling in found:
            add(form, spelling, stems.get(spelling, []))
        spelling = accented.get(form, raw)
        lemmas = sorted(found.get(spelling, ()))
        for other in found:
            if other != spelling:
                lemmas.extend(sorted(found[other]))
        listed = ";".join(dict.fromkeys([spelling, *found]))
        rows[form] = (listed, ";".join(dict.fromkeys(lemmas)) or spelling)
    print(f"{lang}: {len(entries) - len(accented)} kept as written", file=sys.stderr)

    dst = WORDLIST_DIR / f"wordlist_{lang}_scrabble.tsv"
    with open(dst, "w", encoding="utf-8") as out:
        for form in sorted(rows):
            spelling, canonical = rows[form]
            out.write(f"{form}\t{spelling}\t{canonical}\n")
    print(f"{len(rows)} words written to {dst}")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("language", choices=LANGUAGES)
    build(ap.parse_args().language)


if __name__ == "__main__":
    main()
