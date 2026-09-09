#!/usr/bin/env python3
"""
Qdrant vector store for word embeddings — the "words" collection.

All Qdrant handling lives in the `QdrantStore` class below. It talks plain
HTTP (httpx) to a Qdrant REST endpoint — no `qdrant-client` SDK, the same
"swap env vars, no code change" design as `backend/embedder.py` and
`backend/clues.py`, and no new package in `requirements.txt` (httpx is
already a dependency).

Multitenancy: ONE collection ("words"), ONE tenant per language. Every
point carries a `lang` payload field ("fr", "en", "de", "es", "it",
"pt"), and `lang` is registered as a *tenant* keyword payload index
(`is_tenant: true`) so Qdrant co-locates each language's vectors on disk
and a language-filtered search stays fast as the collection grows across
all six languages. A per-language search is a normal search with a
`filter` on `lang`; there is no separate collection per language.

Vector dimension is whatever the embedding model produces. By default
`ensure_collection()` probes the live embedder (`backend/embedder.py`,
BAAI/bge-m3 -> 1024). Set `QDRANT_VECTOR_SIZE` in `env.sh` to skip the
probe (e.g. to create the collection while the embed server is down).

Configuration (env.sh at the project root — every value has a fallback):
  - QDRANT_URL         full base URL; else built from QDRANT_HOST/QDRANT_PORT
  - QDRANT_HOST        default 127.0.0.1
  - QDRANT_PORT        default 6333 (REST)
  - QDRANT_COLLECTION  default "words"
  - QDRANT_API_KEY     sent as the `api-key` header when non-empty (Qdrant Cloud)
  - QDRANT_DISTANCE    default "Cosine"
  - QDRANT_VECTOR_SIZE optional; overrides the embedder probe
  - QDRANT_ON_DISK     "1"/"0", default 0 — keep vectors in RAM. Set to 1
                       ONLY when the Qdrant storage lives on an SSD: HNSW
                       search reads vector data at every graph hop, so
                       on-disk vectors on a spinning HDD make each search
                       tens of seconds (it hits Qdrant's 60s operation
                       timeout, especially while a populate run is also
                       writing). The full six-language word set is a few
                       GB of vectors — fine in RAM on any real host.

CLI:
  python -m backend.qdrant_store --init                 # collection + tenant index
  python -m backend.qdrant_store --info
  python -m backend.qdrant_store --search "oiseau de proie" --lang fr
"""
import argparse
import os
import sys
import time
import uuid

import httpx

DEFAULT_QDRANT_HOST = "127.0.0.1"
DEFAULT_QDRANT_PORT = "6333"
DEFAULT_COLLECTION = "words"
DEFAULT_DISTANCE = "Cosine"
DEFAULT_TIMEOUT = 30.0
TENANT_FIELD = "lang"

# Retry policy for a transient timeout/connection error on any single
# HTTP request (see _request) — a short, fixed number of extra attempts
# with a small linear backoff, never applied to a real 4xx/5xx response.
QDRANT_REQUEST_RETRIES = 2
QDRANT_REQUEST_RETRY_DELAY_S = 1.0

# Fixed namespace so a word's point id is deterministic across runs: the
# same (lang, word) always maps to the same id, so re-running the
# populator updates points in place instead of duplicating them.
_POINT_ID_NAMESPACE = uuid.UUID("9f1c0b2a-3d4e-5f60-8a90-b0c1d2e3f405")


class QdrantStoreError(RuntimeError):
    """Raised when Qdrant is unreachable or returns an unusable response."""


def word_point_id(lang, word):
    """Deterministic Qdrant point id (UUID string) for a (language, word)
    pair — stable across runs, so an upsert of the same word updates it
    rather than creating a duplicate."""
    return str(uuid.uuid5(_POINT_ID_NAMESPACE, f"{lang}:{word}"))


def _lang_filter(lang):
    return {"must": [{"key": TENANT_FIELD, "match": {"value": lang}}]}


def _compose_embed_text(word, accented, canonical):
    """Builds the text actually sent to the embedder for one word, at the
    user's explicit request: "Ne plus générer des embeddings à partir des
    définitions. Utiliser uniquement des chaînes composées du mot fléchi,
    de sa version majuscule sans accent, et sa forme canonique." — the
    dictionary-definition enrichment used previously (`backend/gloss_
    lookup.py`) is gone; the embedded text is now just, space-joined,
    the word's own natural accented/inflected spelling, its bare
    accent-stripped uppercase form (the MOT column), and each of its
    candidate canonical form(s)/lemma(s).

    `canonical` is the wordlist's own `;`-separated CANONIQUE column
    (possibly empty — not every word has a candidate lemma). Exact
    duplicates among the parts are dropped (a lemma identical to the
    accented spelling, say) but the accented and the uppercase forms are
    both kept even though they only differ in case, since the request
    names all three explicitly. Falls back to the bare word if somehow
    every part is empty."""
    lemmas = [c.strip() for c in (canonical or "").split(";") if c.strip()]
    parts = []
    for p in [accented, word, *lemmas]:
        if p and p not in parts:
            parts.append(p)
    return " ".join(parts) if parts else (accented or word or "")


class QdrantStore:
    """Thin HTTP client for one Qdrant collection.

    One instance holds one keep-alive `httpx.Client`; reuse it across many
    calls. Usable as a context manager (`with QdrantStore() as s: ...`) to
    close the client deterministically; otherwise it closes on GC.
    """

    def __init__(self, url=None, collection=None, api_key=None,
                 timeout=DEFAULT_TIMEOUT):
        self.base_url = (
            url
            or os.environ.get("QDRANT_URL")
            or "http://{host}:{port}".format(
                host=os.environ.get("QDRANT_HOST", DEFAULT_QDRANT_HOST),
                port=os.environ.get("QDRANT_PORT", DEFAULT_QDRANT_PORT),
            )
        ).rstrip("/")
        self.collection = collection or os.environ.get(
            "QDRANT_COLLECTION", DEFAULT_COLLECTION
        )
        self.api_key = (
            api_key if api_key is not None
            else os.environ.get("QDRANT_API_KEY", "")
        )
        self.timeout = timeout
        self._client = None

    # -- lifecycle -----------------------------------------------------
    def _http(self):
        if self._client is None:
            headers = {"Content-Type": "application/json"}
            if self.api_key:
                headers["api-key"] = self.api_key
            self._client = httpx.Client(timeout=self.timeout, headers=headers)
        return self._client

    def close(self):
        if self._client is not None:
            self._client.close()
            self._client = None

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    def __del__(self):
        try:
            self.close()
        except Exception:
            pass

    # -- low-level ---------------------------------------------------
    def _request(self, method, path, **kwargs):
        # A transient timeout/connection error is retried a few times
        # (short backoff) before giving up — added after a real,
        # multi-hour `qdrant_populate --all` run was killed outright by a
        # single `PUT .../points` timeout (contention from a concurrent
        # bulk delete running at the same time), losing nothing in terms
        # of already-written data (upserts are idempotent) but forcing a
        # manual restart of a long background job over one blip. Only
        # httpx's own timeout/connect exceptions are retried — a 4xx/5xx
        # response (handled by `_ok`, not here) or any other error is
        # never worth retrying, since it would just fail identically
        # again.
        last_exc = None
        for attempt in range(QDRANT_REQUEST_RETRIES + 1):
            try:
                return self._http().request(
                    method, f"{self.base_url}{path}", **kwargs
                )
            except (httpx.TimeoutException, httpx.ConnectError) as exc:
                last_exc = exc
                if attempt < QDRANT_REQUEST_RETRIES:
                    time.sleep(QDRANT_REQUEST_RETRY_DELAY_S * (attempt + 1))
                    continue
            except httpx.HTTPError as exc:
                raise QdrantStoreError(
                    f"Qdrant request {method} {path} failed: {exc}. "
                    "Is Qdrant running? (./run_qdrant.sh)"
                ) from exc
        raise QdrantStoreError(
            f"Qdrant request {method} {path} failed after "
            f"{QDRANT_REQUEST_RETRIES + 1} attempts: {last_exc}. "
            "Is Qdrant running? (./run_qdrant.sh)"
        ) from last_exc

    def _ok(self, resp, *extra_ok):
        if resp.status_code in (200, 201, 202, *extra_ok):
            return resp
        raise QdrantStoreError(
            f"Qdrant {resp.request.method} {resp.request.url.path} -> "
            f"{resp.status_code}: {resp.text[:300]}"
        )

    # -- collection ------------------------------------------------
    def ping(self):
        """True if the Qdrant HTTP API answers at all."""
        try:
            return self._request("GET", "/").status_code == 200
        except QdrantStoreError:
            return False

    def collection_exists(self):
        resp = self._request("GET", f"/collections/{self.collection}")
        if resp.status_code == 200:
            return True
        if resp.status_code == 404:
            return False
        return bool(self._ok(resp))

    def _resolve_dimension(self, dimension):
        if dimension:
            return int(dimension)
        env = os.environ.get("QDRANT_VECTOR_SIZE")
        if env:
            return int(env)
        # Probe the live embedding model — no hardcoded dimension.
        try:
            from backend.embedder import Embedder
        except ImportError:  # run as a plain script from backend/
            from embedder import Embedder
        with Embedder() as emb:
            return int(emb.dimension)

    def ensure_collection(self, dimension=None, distance=None, on_disk=None,
                          recreate=False):
        """Create the collection (if missing) plus the per-language tenant
        index. Idempotent — safe to call before every populate/search.
        Returns the collection info dict."""
        distance = distance or os.environ.get("QDRANT_DISTANCE",
                                              DEFAULT_DISTANCE)
        if on_disk is None:
            # Default RAM-resident: on-disk vectors on a spinning HDD make
            # HNSW search unusably slow (each graph hop is a disk seek).
            # Opt in with QDRANT_ON_DISK=1 only on SSD-backed storage.
            on_disk = os.environ.get("QDRANT_ON_DISK", "0").lower() in (
                "1", "true", "yes", "on"
            )

        exists = self.collection_exists()
        if exists and recreate:
            self._ok(self._request(
                "DELETE", f"/collections/{self.collection}"))
            exists = False

        if not exists:
            dim = self._resolve_dimension(dimension)
            body = {
                "vectors": {
                    "size": dim,
                    "distance": distance,
                    "on_disk": bool(on_disk),
                },
                # Payload is tiny (word + lang + a few fields). Qdrant
                # defaults it to on-disk; keep it in RAM alongside the
                # vectors so a search never does a disk read for the
                # payload of each candidate either — same HDD reasoning as
                # `on_disk` above. Follows the vector setting: on-disk
                # payload only makes sense when vectors are on-disk too.
                "on_disk_payload": bool(on_disk),
            }
            self._ok(self._request(
                "PUT", f"/collections/{self.collection}", json=body))

        self.ensure_tenant_index()
        return self.collection_info()

    def ensure_tenant_index(self, field=TENANT_FIELD):
        """Register `field` as a tenant keyword payload index. Idempotent:
        a repeat call on an already-indexed field is treated as success."""
        body = {"field_name": field,
                "field_schema": {"type": "keyword", "is_tenant": True}}
        resp = self._request(
            "PUT", f"/collections/{self.collection}/index?wait=true",
            json=body)
        if resp.status_code in (200, 201, 202):
            return
        if "already exists" in resp.text.lower():
            return
        raise QdrantStoreError(
            f"could not create tenant index on '{field}': "
            f"{resp.status_code} {resp.text[:300]}")

    def collection_info(self):
        resp = self._ok(self._request(
            "GET", f"/collections/{self.collection}"))
        return resp.json().get("result", {})

    def drop_collection(self):
        return self._ok(self._request(
            "DELETE", f"/collections/{self.collection}"), 404)

    # -- points ---------------------------------------------------
    def count(self, lang=None, exact=True):
        body = {"exact": bool(exact)}
        if lang:
            body["filter"] = _lang_filter(lang)
        resp = self._ok(self._request(
            "POST", f"/collections/{self.collection}/points/count",
            json=body))
        return int(resp.json()["result"]["count"])

    def upsert(self, points, wait=True):
        """`points`: list of {id, vector, payload}. Returns the count sent."""
        if not points:
            return 0
        q = "?wait=true" if wait else ""
        self._ok(self._request(
            "PUT", f"/collections/{self.collection}/points{q}",
            json={"points": list(points)}))
        return len(points)

    def upsert_words(self, lang, rows, embedder=None, batch_size=64,
                     on_progress=None):
        """Embed and upsert one point per word for a language.

        `rows`: iterable of `(word, accented, frequency, canonical)` — the
        four `wordlist_<lang>_full.tsv` columns (`frequency` may be None,
        `canonical` may be ""). The text actually embedded is built by
        `_compose_embed_text`: just the accented/inflected spelling, the
        bare accent-stripped uppercase form, and each candidate canonical
        form, space-joined (no dictionary definitions — see that
        function's own docstring). Payload:
        `{lang, word, accented, canonical?, frequency?}` (payload is
        always just the word's own fields — never the compiled embed
        text itself). Point id is deterministic (`word_point_id`), so a
        re-run updates in place.

        Mid-run batches are sent with `wait=false` for throughput; the
        final batch waits, so a `count()` right after is accurate.
        Returns the total number of points sent.
        """
        own = embedder is None
        if own:
            from backend.embedder import Embedder  # local import: optional dep path
            embedder = Embedder()
        total = 0
        try:
            # One-batch lookahead so the very last batch (full or partial)
            # is the only one sent with wait=true — a count() right after
            # is then accurate, without paying the wait on every batch.
            prev, buf = None, []
            for row in rows:
                buf.append(row)
                if len(buf) >= batch_size:
                    if prev is not None:
                        total += self._flush(lang, prev, embedder, wait=False)
                        if on_progress:
                            on_progress(total)
                    prev, buf = buf, []
            if prev is not None and buf:
                total += self._flush(lang, prev, embedder, wait=False)
                if on_progress:
                    on_progress(total)
                total += self._flush(lang, buf, embedder, wait=True)
                if on_progress:
                    on_progress(total)
            elif prev is not None:          # exact multiple of batch_size
                total += self._flush(lang, prev, embedder, wait=True)
                if on_progress:
                    on_progress(total)
            elif buf:                       # fewer than batch_size rows total
                total += self._flush(lang, buf, embedder, wait=True)
                if on_progress:
                    on_progress(total)
        finally:
            if own:
                embedder.close()
        return total

    def _flush(self, lang, rows, embedder, wait):
        texts = [
            _compose_embed_text(word, accented, canonical)
            for (word, accented, _f, canonical) in rows
        ]
        vectors = embedder.embed_batch(texts)
        points = []
        for (word, accented, freq, canonical), vec in zip(rows, vectors):
            payload = {TENANT_FIELD: lang, "word": word,
                       "accented": accented or word}
            if canonical:
                payload["canonical"] = canonical
            if freq is not None:
                payload["frequency"] = freq
            points.append({"id": word_point_id(lang, word),
                           "vector": list(vec), "payload": payload})
        return self.upsert(points, wait=wait)

    def delete_lang(self, lang, wait=True):
        """Remove every point of one language's tenant."""
        q = "?wait=true" if wait else ""
        return self._ok(self._request(
            "POST", f"/collections/{self.collection}/points/delete{q}",
            json={"filter": _lang_filter(lang)})).json()

    def delete_points(self, ids, wait=True):
        """Remove specific points by id (Qdrant's own `points` — not
        `filter` — delete-request field). Added at the user's explicit
        request, for cleaning up a wordlist trim ("Penser à filtrer les
        entrées Qdrant retirées"): unlike `delete_lang`, which wipes an
        entire tenant, this removes exactly the given ids and nothing
        else — e.g. `word_point_id(lang, word)` for every word a
        wordlist rebuild dropped. `ids` is a plain list of point-id
        strings; a caller with many ids should chunk them (see
        `delete_words`, which does this for the common "given a list of
        words" case) rather than sending one huge request."""
        q = "?wait=true" if wait else ""
        return self._ok(self._request(
            "POST", f"/collections/{self.collection}/points/delete{q}",
            json={"points": list(ids)})).json()

    def delete_words(self, lang, words, batch_size=2000, wait=True):
        """Remove specific `(lang, word)` points by word — the common
        case for `delete_points`, computing each `word_point_id` and
        chunking into `batch_size`-sized `delete_points` calls so a large
        drop list (e.g. hundreds of thousands of words trimmed from a
        wordlist) never sends one oversized request. Returns the total
        number of ids sent for removal."""
        words = list(words)
        for i in range(0, len(words), batch_size):
            chunk = words[i:i + batch_size]
            self.delete_points(
                [word_point_id(lang, w) for w in chunk], wait=wait,
            )
        return len(words)

    # -- search --------------------------------------------------
    def search(self, vector, lang=None, limit=10, with_payload=True, offset=None):
        """`offset` (Qdrant's own search-request field) skips the first
        `offset` results of the ranked list — lets a caller page deeper
        into the same nearest-neighbor ordering across several calls
        (e.g. backend/app.py's per-length theme glossary, which pages
        through the ranking looking for enough words of each target
        length) instead of re-fetching from the top with an ever-larger
        `limit` each time. `None`/0 (the default) is a plain, unpaged
        search — identical to this method's own pre-existing behavior."""
        body = {"vector": list(vector), "limit": int(limit),
                "with_payload": with_payload}
        if lang:
            body["filter"] = _lang_filter(lang)
        if offset:
            body["offset"] = int(offset)
        resp = self._ok(self._request(
            "POST", f"/collections/{self.collection}/points/search",
            json=body))
        return resp.json()["result"]

    def search_text(self, text, lang=None, limit=10, embedder=None):
        own = embedder is None
        if own:
            from backend.embedder import Embedder
            embedder = Embedder()
        try:
            vec = embedder.embed(text)
        finally:
            if own:
                embedder.close()
        return self.search(vec, lang=lang, limit=limit)


# ----------------------------------------------------------------------
# CLI
# ----------------------------------------------------------------------
def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--init", action="store_true",
                        help="create the collection + tenant index (idempotent)")
    parser.add_argument("--recreate", action="store_true",
                        help="with --init: drop and recreate the collection first")
    parser.add_argument("--info", action="store_true",
                        help="print collection info and per-language counts")
    parser.add_argument("--search", metavar="TEXT",
                        help="embed TEXT and print the nearest words")
    parser.add_argument("--lang", help="restrict --search to this language tenant")
    parser.add_argument("--limit", type=int, default=10)
    args = parser.parse_args(argv)

    if not any((args.init, args.info, args.search)):
        parser.print_help()
        return 0

    with QdrantStore() as store:
        if not store.ping():
            print(f"Qdrant not reachable at {store.base_url} — start it with "
                  "./run_qdrant.sh", file=sys.stderr)
            return 1

        if args.init:
            info = store.ensure_collection(recreate=args.recreate)
            print(f"collection '{store.collection}' ready "
                  f"(points={info.get('points_count')}, "
                  f"status={info.get('status')})")

        if args.info:
            info = store.collection_info()
            vectors = (info.get("config", {}).get("params", {})
                       .get("vectors", {}))
            print(f"collection : {store.collection}")
            print(f"status     : {info.get('status')}")
            print(f"points     : {info.get('points_count')}")
            print(f"vector     : size={vectors.get('size')} "
                  f"distance={vectors.get('distance')} "
                  f"on_disk={vectors.get('on_disk')}")
            for lang in ("fr", "en", "de", "es", "it", "pt"):
                try:
                    print(f"  {lang}: {store.count(lang)}")
                except QdrantStoreError:
                    pass

        if args.search:
            hits = store.search_text(args.search, lang=args.lang,
                                     limit=args.limit)
            for h in hits:
                pl = h.get("payload", {})
                print(f"  {h['score']:+.4f}  {pl.get('accented', ''):20s} "
                      f"[{pl.get('lang', '')}]  {pl.get('word', '')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
