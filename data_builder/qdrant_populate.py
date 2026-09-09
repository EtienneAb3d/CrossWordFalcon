#!/usr/bin/env python3
"""
Populate the Qdrant "words" collection with an embedding for every word in
`data/wordlist_<lang>_full.tsv`.

`WordEmbeddingIndexer` reads the TSV (columns
`MOT<TAB>ACCENTUE<TAB>FREQUENCE<TAB>CANONIQUE`), embeds the natural
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
  python -m data_builder.qdrant_populate --init-only
"""
import argparse
import pathlib
import sys
import time

_REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from backend.embedder import Embedder, EmbedderError          # noqa: E402
from backend.qdrant_store import QdrantStore, QdrantStoreError  # noqa: E402

LANGUAGES = ("fr", "en", "de", "es", "it", "pt")
DATA_DIR = _REPO_ROOT / "data"
WORDLIST_TEMPLATE = "wordlist_{lang}_full.tsv"


class WordEmbeddingIndexer:
    """Reads a `wordlist_<lang>_full.tsv` and feeds every word into the
    Qdrant "words" collection as an embedded, language-tenanted point."""

    def __init__(self, store=None, embedder=None, batch_size=64,
                 data_dir=DATA_DIR):
        self.store = store or QdrantStore()
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

    def iter_rows(self, lang, limit=None, offset=0):
        """Yield `(word, accented, frequency, canonical)` per line.
        `frequency` is a float or None; `canonical` is a string (possibly
        empty, possibly ";"-separated)."""
        path = self.wordlist_path(lang)
        if not path.exists():
            raise FileNotFoundError(path)
        yielded = 0
        with path.open(encoding="utf-8") as fh:
            for lineno, line in enumerate(fh):
                if lineno < offset:
                    continue
                line = line.rstrip("\n")
                if not line:
                    continue
                parts = line.split("\t")
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
                yielded += 1
                if limit is not None and yielded >= limit:
                    return

    # -- indexing ------------------------------------------------
    def index_language(self, lang, limit=None, offset=0, recreate=False,
                       log_every=5000):
        path = self.wordlist_path(lang)
        if not path.exists():
            print(f"[{lang}] SKIP — {path.name} not found")
            return 0

        self.store.ensure_collection()  # idempotent; probes the dimension once
        if recreate:
            print(f"[{lang}] clearing existing tenant...")
            self.store.delete_lang(lang)

        rows = self.iter_rows(lang, limit=limit, offset=offset)
        start = time.perf_counter()
        last_logged = [0]

        def _progress(done):
            if done - last_logged[0] >= log_every:
                last_logged[0] = done
                rate = done / max(time.perf_counter() - start, 1e-9)
                print(f"  [{lang}] {done:>8d} words   {rate:6.0f}/s")

        total = self.store.upsert_words(
            lang, rows, embedder=self.embedder,
            batch_size=self.batch_size, on_progress=_progress)

        elapsed = time.perf_counter() - start
        held = self.store.count(lang)
        print(f"[{lang}] done: {total} words sent in {elapsed:.1f}s "
              f"({total / max(elapsed, 1e-9):.0f}/s); "
              f"tenant now holds {held}")
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
                        help="skip the first N lines (resume an interrupted run)")
    parser.add_argument("--batch", type=int, default=64,
                        help="embedding / upsert batch size (default 64)")
    parser.add_argument("--recreate", action="store_true",
                        help="delete a language's existing points before indexing")
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
        with WordEmbeddingIndexer(batch_size=args.batch) as indexer:
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
                  f"points={info.get('points_count')})")

            if args.init_only:
                return 0

            grand_total = 0
            for lang in langs:
                grand_total += indexer.index_language(
                    lang, limit=args.limit, offset=args.offset,
                    recreate=args.recreate)
            print(f"\nTotal: {grand_total} words indexed across "
                  f"{len(langs)} language(s).")
    except (EmbedderError, QdrantStoreError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
