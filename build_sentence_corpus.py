#!/usr/bin/env python3
"""
Builds a reference sentence corpus per language from two OPUS
(opus.nlpl.eu) sources — OpenSubtitles and Wikipedia — merged together.
Used two ways downstream: backend/example_sentences.py looks up real
usage examples of a word's exact inflected form in it (to ground
backend/clues.py's clue-writing prompt for rare/ambiguous words — see the
French "ARE" case in the project-best-practices SKILL); compute_word_
frequencies.py counts word occurrences in it to build this project's own
word-frequency source, replacing the previously-used HermitDave
FrequencyWords lists (see the project-best-practices SKILL for why).

Wikipedia is a deliberate second source, not a replacement for
OpenSubtitles: subtitle dialogue is colloquial and covers everyday
vocabulary (conjugated verbs, casual nouns) that encyclopedic text rarely
uses, while Wikipedia covers formal/technical vocabulary (and rare-but-
real words like French "are", the land-area unit) that dialogue almost
never does — each fills a real gap the other has.

The full per-language source is multi-gigabyte (compressed) for either
corpus; downloading all of it for every language would be impractical
here, so this fetches only the first `--max-bytes` of each compressed
file via an HTTP Range request and decompresses whatever complete data
that partial download yields (the truncated tail, if any, is simply
dropped) — still hundreds of thousands of lines per source.

Kept sentences must be:
- under 50 words (--max-words) — drops very long/merged blocks;
- valid words of their own language, mostly: neither source is perfectly
  monolingual per language file (dialogue/article text in another
  language leaks into every language's split — the same contamination
  problem build_wordlist_freq.py solves for word *lists*) — each sentence
  is tokenized and checked against the language's Hunspell dictionary
  (reusing build_wordlist_freq.py's HUNSPELL_SOURCE/_fetch_hunspell, same
  cached dictionaries in data/hunspell_cache/); a sentence with too high a
  fraction of unrecognized words is dropped as likely wrong-language
  content (a "sentence containing parts in the wrong language") rather
  than kept as a false grounding example or counted into our own
  frequency table.

Usage:
    python3 build_sentence_corpus.py fr
    python3 build_sentence_corpus.py en --max-bytes 100000000
"""
import argparse
import re
import subprocess
import sys
import zlib
from pathlib import Path

from build_wordlist_freq import HUNSPELL_ENCODING, HUNSPELL_SOURCE, _fetch_hunspell

CORPUS_DIR = Path(__file__).resolve().parent / "data" / "reference_corpus"

# path template (language filled in) -> full download URL, per OPUS source.
SOURCES = {
    "opensubtitles": "https://object.pouta.csc.fi/OPUS-OpenSubtitles/v2018/mono/{lang}.txt.gz",
    "wikipedia": "https://object.pouta.csc.fi/OPUS-Wikipedia/v1.0/mono/{lang}.txt.gz",
}

DEFAULT_MAX_BYTES = 50_000_000
MAX_WORDS_PER_SENTENCE = 50
# A sentence is dropped as likely containing a wrong-language *part* if
# EITHER condition holds — two complementary signals, calibrated by hand
# against real contaminated examples (English dialogue/quotes leaking into
# other languages' files) vs. genuine sentences with a proper noun or two:
# - MAX_INVALID_RUN: 3+ *consecutive* unrecognized words — a contiguous
#   run this long is almost always an embedded foreign phrase/quote
#   ("You are now crossing the equator"), not a couple of foreign proper
#   nouns scattered in otherwise-correct text (which rarely run 3 long).
# - MAX_INVALID_WORD_FRACTION: catches short sentences that are foreign
#   throughout even without one single long run ("Clowns are what the
#   village boys are!" — alternating recognized/unrecognized tokens).
MAX_INVALID_RUN = 3
MAX_INVALID_WORD_FRACTION = 0.25

_WORD_RE = re.compile(r"[^\W\d_]+", re.UNICODE)


def _download_partial(url, max_bytes, dst_path):
    subprocess.run(
        ["curl", "-sL", "--fail", "--range", f"0-{max_bytes - 1}", "-o", str(dst_path), url],
        check=True,
    )


def _decompress_partial(compressed_path):
    """Decompresses as much of the (truncated, since this is a partial
    download) gzip file as possible — a truncated stream raises an error
    right at the end; everything decoded before that point is still valid
    and is what we keep."""
    decompressor = zlib.decompressobj(zlib.MAX_WBITS | 16)  # 16 = expect a gzip header
    out = bytearray()
    chunk_size = 1 << 20
    with open(compressed_path, "rb") as f:
        while True:
            chunk = f.read(chunk_size)
            if not chunk:
                break
            try:
                out += decompressor.decompress(chunk)
            except zlib.error:
                break
    return bytes(out)


def _split_sentences(raw_bytes, max_words):
    lines = raw_bytes.decode("utf-8", errors="ignore").splitlines()
    if lines:
        lines = lines[:-1]  # last line is likely truncated mid-sentence
    kept = []
    for line in lines:
        line = line.strip()
        if not line:
            continue
        if len(line.split()) < max_words:
            kept.append(line)
    return kept


def _max_invalid_run(tokens, invalid_words):
    best = current = 0
    for t in tokens:
        if t in invalid_words:
            current += 1
            best = max(best, current)
        else:
            current = 0
    return best


def _filter_by_language(sentences, lang):
    """Drops sentences likely to contain a wrong-language part — either a
    long contiguous run of unrecognized words (an embedded foreign phrase
    or quote) or too high an overall fraction of them (foreign
    throughout) — see MAX_INVALID_RUN / MAX_INVALID_WORD_FRACTION."""
    if lang not in HUNSPELL_SOURCE or not sentences:
        return sentences
    tokenized = [_WORD_RE.findall(s) for s in sentences]
    all_words = {w for toks in tokenized for w in toks}
    dict_basename = _fetch_hunspell(lang)
    encoding = HUNSPELL_ENCODING.get(lang, "utf-8")
    result = subprocess.run(
        ["hunspell", "-d", dict_basename, "-i", encoding, "-l"],
        input="\n".join(all_words).encode(encoding, errors="replace"),
        stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
    )
    invalid_words = set(result.stdout.decode(encoding, errors="replace").splitlines())

    kept = []
    for line, toks in zip(sentences, tokenized):
        if not toks:
            continue
        invalid_fraction = sum(1 for w in toks if w in invalid_words) / len(toks)
        if invalid_fraction > MAX_INVALID_WORD_FRACTION:
            continue
        if _max_invalid_run(toks, invalid_words) >= MAX_INVALID_RUN:
            continue
        kept.append(line)
    return kept


def _fetch_source_sentences(source_name, url_template, lang, max_bytes, tmp_dir):
    url = url_template.format(lang=lang)
    compressed_path = tmp_dir / f"{lang}_{source_name}.txt.gz.part"
    print(f"Downloading up to {max_bytes:,} bytes of {source_name} ({lang}) ...", file=sys.stderr)
    _download_partial(url, max_bytes, compressed_path)
    raw = _decompress_partial(compressed_path)
    compressed_path.unlink()
    sentences = _split_sentences(raw, MAX_WORDS_PER_SENTENCE)
    print(f"  {len(sentences)} candidate sentences from {source_name}", file=sys.stderr)
    return sentences


def build_sentence_corpus(lang, max_bytes=DEFAULT_MAX_BYTES, sources=SOURCES):
    CORPUS_DIR.mkdir(parents=True, exist_ok=True)
    all_sentences = []
    for source_name, url_template in sources.items():
        all_sentences.extend(_fetch_source_sentences(source_name, url_template, lang, max_bytes, CORPUS_DIR))

    print(f"Checking {len(all_sentences)} total candidate sentences against "
          f"the {lang!r} dictionary...", file=sys.stderr)
    kept = _filter_by_language(all_sentences, lang)

    dst = CORPUS_DIR / f"{lang}_sentences.txt"
    with open(dst, "w", encoding="utf-8") as out:
        out.write("\n".join(kept))
        out.write("\n")
    print(f"{len(kept)} sentences written to {dst}")
    return dst


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("language", choices=sorted(HUNSPELL_SOURCE))
    ap.add_argument("--max-bytes", type=int, default=DEFAULT_MAX_BYTES,
                     help=f"how much of each compressed source to download (default: {DEFAULT_MAX_BYTES:,})")
    ap.add_argument("--sources", nargs="+", choices=sorted(SOURCES), default=sorted(SOURCES),
                     help="which OPUS sources to include (default: both)")
    args = ap.parse_args()
    build_sentence_corpus(args.language, args.max_bytes, {k: SOURCES[k] for k in args.sources})


if __name__ == "__main__":
    main()
