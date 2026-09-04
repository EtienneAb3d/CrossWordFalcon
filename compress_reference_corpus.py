#!/usr/bin/env python3
"""One-off packaging script: compresses a language's CAPPED reference
corpus (data/reference_corpus/<lang>_sentences.txt — at most
build_sentence_corpus.MAX_SENTENCES_PER_LANGUAGE sentences; itself
gitignored — see build_sentence_corpus.py) into a single data/
reference_corpus_<lang>.tar.xz archive, meant to be checked into the
repository so a fresh clone can reuse it for backend/example_sentences.py's
own LLM-grounding lookups instead of re-running build_sentence_corpus.py's
own multi-gigabyte OPUS downloads from scratch.

Deliberately the capped variant, never <lang>_sentences_full.txt: a fresh
clone that only needs example sentences never needs the full 80-100M-line
corpus, and the full corpus compresses to roughly a gigabyte per language
(see GITHUB_HARD_LIMIT_BYTES below) — utterly incompatible with GitHub
regardless. A fresh clone that DOES need the full corpus (to rebuild
data/wordlist_<lang>_full.tsv via build_wordlist_freq.py) still has to run
build_sentence_corpus.py <lang> from scratch — this script's own archive
was never meant to substitute for that.

Deliberately named reference_corpus_<lang>.tar.xz (a sibling of the
data/reference_corpus/ directory, not a file inside it) so the archive
falls outside that directory's own gitignore rule (.gitignore:
/data/reference_corpus/) and can be committed on its own.

Run once per language, as the last step of the corpus-generation pipeline
(after build_sentence_corpus.py has produced that language's own
_sentences.txt, the capped one — see its own docstring):

    python3 compress_reference_corpus.py fr

Validates the resulting archive against GitHub's own size limits — a
plain git push is hard-rejected past GITHUB_HARD_LIMIT_BYTES (100 MB) and
GitHub's own UI warns starting at GITHUB_WARN_LIMIT_BYTES (50 MB) — and
exits with a non-zero status (never partially/silently) if the hard limit
is exceeded, so this can gate the rest of a build pipeline rather than
leave an unpublishable archive sitting on disk unnoticed."""
import argparse
import subprocess
import sys
from pathlib import Path

LANGUAGES = ("fr", "en", "de", "es", "it")

DATA_DIR = Path(__file__).resolve().parent / "data"
CORPUS_DIR = DATA_DIR / "reference_corpus"

# GitHub hard-blocks any pushed file over 100 MB (decimal megabytes, the
# unit GitHub's own docs use) without Git LFS; it starts warning in the
# web UI from 50 MB onward. Both checked here, not just the hard one, so
# a build that "passes" still gets a heads-up before it's too close to
# the wall for comfort.
GITHUB_HARD_LIMIT_BYTES = 100_000_000
GITHUB_WARN_LIMIT_BYTES = 50_000_000


def compress_reference_corpus(lang):
    src = CORPUS_DIR / f"{lang}_sentences.txt"
    if not src.exists():
        print(f"error: {src} does not exist — run build_sentence_corpus.py {lang} first",
              file=sys.stderr)
        return None

    dst = DATA_DIR / f"reference_corpus_{lang}.tar.xz"
    src_size = src.stat().st_size
    print(f"Compressing {src} ({src_size / 1e9:.2f} GB) -> {dst} ...")

    # Real `xz` CLI piped from `tar -cf -`, NOT `tar -cJf ... ` (xz via
    # tar's own built-in filter) — this project already shells out to real
    # CLI tools for heavy one-off jobs elsewhere (hunspell, rsvg-convert)
    # rather than reimplementing them, but the *specific* two-step pipe
    # form here is load-bearing, not a style preference: this exact mistake
    # was already made and fixed once before, documented in the project-
    # best-practices SKILL (this project's own `data/reference_corpus_
    # <lang>.tar.xz` archives already existed from before this script did,
    # going back to when the corpus was small enough to need no cap at
    # all) — on a machine whose `tar` is bsdtar/libarchive (the default on
    # macOS, confirmed via `tar --version` on this very machine), `tar
    # -cJf`'s own built-in xz filter silently ignores `XZ_OPT`, producing a
    # materially worse compression ratio with no error or warning
    # whatsoever (measured live on this exact corpus: 52.7 MB via `tar
    # -cJf`+`XZ_OPT="-T0"` vs. 48.6 MB via this real pipe — the difference
    # between failing and passing GitHub's 50 MB soft-warning threshold).
    # `-9e` (extreme preset) trades some compression time for a better
    # ratio — affordable here since `-T0` still parallelizes across every
    # core. `-C CORPUS_DIR <name>` (passed to `tar`, not `xz`) stores only
    # the bare filename inside the archive (not the full path), so `xz -d
    # -c reference_corpus_<lang>.tar.xz | tar -xf -` extracts directly to
    # `<lang>_sentences.txt` in the current directory, ready to be moved
    # back into data/reference_corpus/.
    tar_proc = subprocess.Popen(
        ["tar", "-cf", "-", "-C", str(CORPUS_DIR), src.name],
        stdout=subprocess.PIPE,
    )
    with open(dst, "wb") as out:
        subprocess.run(["xz", "-9e", "-T0"], stdin=tar_proc.stdout, stdout=out, check=True)
    tar_proc.stdout.close()
    tar_proc.wait()
    if tar_proc.returncode != 0:
        raise subprocess.CalledProcessError(tar_proc.returncode, "tar")

    dst_size = dst.stat().st_size
    ratio = src_size / dst_size if dst_size else float("inf")
    print(f"{dst}: {dst_size / 1e6:.1f} MB (compression ratio {ratio:.2f}x)")

    if dst_size > GITHUB_HARD_LIMIT_BYTES:
        print(
            f"FAIL: {dst.name} is {dst_size / 1e6:.1f} MB, over GitHub's "
            f"{GITHUB_HARD_LIMIT_BYTES / 1e6:.0f} MB hard limit — a plain "
            "git push of this file will be rejected outright.",
            file=sys.stderr,
        )
        return dst, False
    if dst_size > GITHUB_WARN_LIMIT_BYTES:
        print(
            f"WARN: {dst.name} is {dst_size / 1e6:.1f} MB, over GitHub's "
            f"{GITHUB_WARN_LIMIT_BYTES / 1e6:.0f} MB recommended threshold "
            "(still under the 100 MB hard limit, but GitHub's own web UI "
            "will flag it).",
        )
    else:
        print(f"OK: {dst.name} is comfortably under GitHub's size limits.")
    return dst, True


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("language", choices=sorted(LANGUAGES))
    args = ap.parse_args()
    result = compress_reference_corpus(args.language)
    if result is None or not result[1]:
        sys.exit(1)


if __name__ == "__main__":
    main()
