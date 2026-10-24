#!/usr/bin/env python3
"""
Populate the Qdrant "words" collection with an embedding for every word of
a language's lexicon: `data/wordlist_<lang>_freq.tsv`, then every word of
`data/wordlist_<lang>_scrabble.tsv` it lacks (the Scrabble dictionary is
merged whole into every grid's lexicon, see backend/crossword_gen.py's
`merge_scrabble_lexicon`).

`WordEmbeddingIndexer` reads the TSVs (columns
`MOT<TAB>ACCENTUE<TAB>FREQUENCE<TAB>CANONIQUE`, and
`MOT<TAB>ACCENTUE<TAB>CANONIQUE` for the Scrabble one), embeds the natural
accented spelling of each word via `backend/embedder.py`'s `Embedder`
(batched), and upserts one point per word into Qdrant via
`backend/qdrant_store.py`'s `QdrantStore` — one tenant per language
(`lang` payload field), deterministic point id per `(lang, word)`.

Because point ids are deterministic (`qdrant_store.word_point_id`), a
re-run updates each word in place rather than duplicating it — safe to
interrupt and resume (`--offset`), and safe to re-run after the wordlist
has been rebuilt.

Prerequisites: `./run_qdrant.sh` (Qdrant up) and `./run_embed.sh` (embed
server up).

CLI (run from the repo root):
  python -m data_builder.qdrant_populate fr
  python -m data_builder.qdrant_populate --all
  python -m data_builder.qdrant_populate fr --limit 5000 --recreate
  python -m data_builder.qdrant_populate de --batch 128 --offset 800000
  python -m data_builder.qdrant_populate fr --source scrabble
  python -m data_builder.qdrant_populate --init-only
  python -m data_builder.qdrant_populate fr --multi --recreate

`--source` picks the words sent: `all` (default: the freq wordlist, then
the Scrabble words it lacks), `freq` or `scrabble` (only the Scrabble words
the freq wordlist lacks — adds them without re-embedding the rest).
`--offset` counts words of that stream.

`--multi` fills the "words_multi" collection instead
(`qdrant_store.multi_collection_name()`, same shape, tenants and point
ids) with every expression of `data/wordlist_<lang>_multi.tsv`
(`MOT<TAB>ACCENTUE<TAB>CANONIQUE`, English and French only — another
language is skipped), each with its FREQUENCE from
`data/wordlist_<lang>_freq_multi.tsv` when it has one; `--source` does not
apply there.
"""
import argparse
import pathlib
import sys
import time

_REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from backend.embedder import Embedder, EmbedderError          # noqa: E402
from backend.qdrant_store import (                              # noqa: E402
    QdrantStore, QdrantStoreError, multi_collection_name,
)

LANGUAGES = ("fr", "en", "de", "es", "it", "pt")
DATA_DIR = _REPO_ROOT / "data"
WORDLIST_TEMPLATE = "wordlist_{lang}_freq.tsv"
SCRABBLE_TEMPLATE = "wordlist_{lang}_scrabble.tsv"
MULTI_TEMPLATE = "wordlist_{lang}_multi.tsv"
FREQ_MULTI_TEMPLATE = "wordlist_{lang}_freq_multi.tsv"
SOURCES = ("all", "freq", "scrabble")


class WordEmbeddingIndexer:
    """Reads a `wordlist_<lang>_freq.tsv` and feeds every word into the
    Qdrant "words" collection as an embedded, language-tenanted point —
    or, with `multi`, every expression of `wordlist_<lang>_multi.tsv` into
    the "words_multi" collection."""

    def __init__(self, store=None, embedder=None, batch_size=64,
                 data_dir=DATA_DIR, multi=False):
        self.multi = multi
        self.store = store or QdrantStore(
            collection=multi_collection_name() if multi else None)
        self.embedder = embedder or Embedder()
        self.batch_size = batch_size
        self.data_dir = pathlib.Path(data_dir)

    # -- lifecycle ---------------------------------------------------
    def close(self):
        self.embedder.close()
        self.store.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    # -- data ------------------------------------------------------
    def wordlist_path(self, lang):
        return self.data_dir / WORDLIST_TEMPLATE.format(lang=lang)

    def scrabble_path(self, lang):
        return self.data_dir / SCRABBLE_TEMPLATE.format(lang=lang)

    def source_path(self, lang):
        """The wordlist this indexer mirrors for `lang`."""
        if self.multi:
            return self.data_dir / MULTI_TEMPLATE.format(lang=lang)
        return self.wordlist_path(lang)

    @staticmethod
    def _freq_rows(path):
        with path.open(encoding="utf-8") as fh:
            for line in fh:
                parts = line.rstrip("\n").split("\t")
                word = parts[0].strip()
                if not word:
                    continue
                accented = (parts[1].strip()
                            if len(parts) > 1 and parts[1].strip() else word)
                freq = None
                if len(parts) > 2 and parts[2].strip():
                    try:
                        freq = float(parts[2])
                    except ValueError:
                        freq = None
                canonical = parts[3].strip() if len(parts) > 3 else ""
                yield (word, accented, freq, canonical)

    @staticmethod
    def _scrabble_rows(path):
        with path.open(encoding="utf-8") as fh:
            for line in fh:
                parts = line.rstrip("\n").split("\t")
                word = parts[0].strip()
                if not word:
                    continue
                accented = (parts[1].strip()
                            if len(parts) > 1 and parts[1].strip() else word)
                canonical = parts[2].strip() if len(parts) > 2 else ""
                yield (word, accented, None, canonical)

    def iter_rows(self, lang, limit=None, offset=0, source="all"):
        """Yield `(word, accented, frequency, canonical)` per word of
        `source` (see the module docstring), skipping the first `offset`.
        `frequency` is a float or None (always None for a Scrabble-only
        word); `canonical` is a string (possibly empty, possibly
        ";"-separated). With `multi`, the multi-word wordlist's rows, each
        with its freq-multi FREQUENCE (None when it has none)."""
        path = self.source_path(lang)
        if not path.exists():
            raise FileNotFoundError(path)
        scrabble = self.scrabble_path(lang)

        def multi_rows():
            freq_path = self.data_dir / FREQ_MULTI_TEMPLATE.format(lang=lang)
            freqs = ({row[0]: row[2] for row in self._freq_rows(freq_path)}
                     if freq_path.exists() else {})
            for word, accented, _f, canonical in self._scrabble_rows(path):
                yield (word, accented, freqs.get(word), canonical)

        def rows():
            if self.multi:
                yield from multi_rows()
                return
            if source in ("all", "freq"):
                yield from self._freq_rows(path)
            if source in ("all", "scrabble") and scrabble.exists():
                known = {row[0] for row in self._freq_rows(path)}
                for row in self._scrabble_rows(scrabble):
                    if row[0] not in known:
                        yield row

        yielded = 0
        for n, row in enumerate(rows()):
            if n < offset:
                continue
            yield row
            yielded += 1
            if limit is not None and yielded >= limit:
                return

    # -- indexing ------------------------------------------------
    def index_language(self, lang, limit=None, offset=0, recreate=False,
                       log_every=5000, source="all"):
        path = self.source_path(lang)
        if not path.exists():
            print(f"[{lang}] SKIP — {path.name} not found", flush=True)
            return 0

        self.store.ensure_collection()  # idempotent; probes the dimension once
        if recreate:
            print(f"[{lang}] clearing existing tenant...", flush=True)
            self.store.delete_lang(lang)

        rows = self.iter_rows(lang, limit=limit, offset=offset, source=source)
        start = time.perf_counter()
        last_logged = [0]

        def _progress(done):
            if done - last_logged[0] >= log_every:
                last_logged[0] = done
                rate = done / max(time.perf_counter() - start, 1e-9)
                print(f"  [{lang}] {done:>8d} words   {rate:6.0f}/s", flush=True)

        total = self.store.upsert_words(
            lang, rows, embedder=self.embedder,
            batch_size=self.batch_size, on_progress=_progress)

        elapsed = time.perf_counter() - start
        held = self.store.count(lang)
        print(f"[{lang}] done: {total} words sent in {elapsed:.1f}s "
              f"({total / max(elapsed, 1e-9):.0f}/s); "
              f"tenant now holds {held}", flush=True)
        return total

    def index_all(self, **kwargs):
        results = {}
        for lang in LANGUAGES:
            results[lang] = self.index_language(lang, **kwargs)
        return results


# ----------------------------------------------------------------------
# CLI
# ----------------------------------------------------------------------
def main(argv=None):
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("langs", nargs="*", metavar="LANG",
                        help=f"languages to index ({'/'.join(LANGUAGES)})")
    parser.add_argument("--all", action="store_true",
                        help="index every language that has a wordlist file")
    parser.add_argument("--init-only", action="store_true",
                        help="just create the collection + tenant index, index nothing")
    parser.add_argument("--limit", type=int,
                        help="stop after this many words per language (testing)")
    parser.add_argument("--offset", type=int, default=0,
                        help="skip the first N words of the stream (resume an interrupted run)")
    parser.add_argument("--source", choices=SOURCES, default="all",
                        help="words to send: all (default), freq, or scrabble "
                             "(only the Scrabble words the freq wordlist lacks)")
    parser.add_argument("--batch", type=int, default=64,
                        help="embedding / upsert batch size (default 64)")
    parser.add_argument("--recreate", action="store_true",
                        help="delete a language's existing points before indexing")
    parser.add_argument("--multi", action="store_true",
                        help="fill the multi-word expressions' collection (words_multi) "
                             "from wordlist_<lang>_multi.tsv instead")
    args = parser.parse_args(argv)

    langs = list(args.langs)
    if args.all:
        langs = list(LANGUAGES)
    unknown = [x for x in langs if x not in LANGUAGES]
    if unknown:
        parser.error(f"unknown language(s): {', '.join(unknown)} "
                     f"(known: {', '.join(LANGUAGES)})")
    if not langs and not args.init_only:
        parser.error("give at least one LANG, or --all, or --init-only")

    try:
        with WordEmbeddingIndexer(batch_size=args.batch, multi=args.multi) as indexer:
            if not indexer.store.ping():
                print(f"Qdrant not reachable at {indexer.store.base_url} — "
                      "start it with ./run_qdrant.sh", file=sys.stderr)
                return 1

            indexer.store.ensure_collection()
            info = indexer.store.collection_info()
            vectors = (info.get("config", {}).get("params", {})
                       .get("vectors", {}))
            print(f"collection '{indexer.store.collection}' ready "
                  f"(dim={vectors.get('size')}, "
                  f"distance={vectors.get('distance')}, "
                  f"points={info.get('points_count')})", flush=True)

            if args.init_only:
                return 0

            grand_total = 0
            for lang in langs:
                grand_total += indexer.index_language(
                    lang, limit=args.limit, offset=args.offset,
                    recreate=args.recreate, source=args.source)
            print(f"\nTotal: {grand_total} words indexed across "
                  f"{len(langs)} language(s).", flush=True)
    except (EmbedderError, QdrantStoreError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
