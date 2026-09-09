#!/usr/bin/env python3
"""
Multilingual text embeddings via an OpenAI-compatible /v1/embeddings API.

All embedding handling lives in the `Embedder` class below: give it a
string, get back its vector. It talks HTTP to a local embedding server
(llama.cpp's OpenAI-compatible server in --embedding mode, see
run_embed.sh) or to any other OpenAI-compatible embeddings endpoint — same
"swap env vars, no code change" design as backend/clues.py's LLM client.

Default local model (served by run_embed.sh): BAAI/bge-m3, Q4_K_M GGUF —
a small (~418 MB on disk, 567M params) but state-of-the-art multilingual
embedder covering 100+ languages including all 6 CrossWordFalcon
languages, 1024-dimensional, CLS pooling, and fast enough to run on CPU
alone with no GPU.

Configuration (env.sh at the project root):
  - EMBED_BASE_URL : base URL, ".../v1" (default: local server, port 3003)
  - EMBED_MODEL    : model id / alias to request
  - EMBED_API_KEY  : bearer token (default "EMPTY" — the local server
                     ignores it unless configured to require one)

CLI:
  python -m backend.embedder --text "bonjour le monde"
  python -m backend.embedder --benchmark 1000      # time 1000 embeddings
"""
import argparse
import math
import os
import time

import httpx

DEFAULT_EMBED_BASE_URL = "http://127.0.0.1:3003/v1"
DEFAULT_EMBED_MODEL = "bge-m3"
DEFAULT_EMBED_API_KEY = "EMPTY"
DEFAULT_TIMEOUT = 60.0


class EmbedderError(RuntimeError):
    """Raised when the embedding endpoint is unreachable or returns an
    unusable response."""


def _l2_normalize(vec):
    norm = math.sqrt(sum(x * x for x in vec))
    if norm == 0.0:
        return list(vec)
    return [x / norm for x in vec]


class Embedder:
    """Sends a string to the embedding endpoint and returns its vector.

    One instance holds one keep-alive `httpx.Client`; reuse it across many
    calls rather than creating a new `Embedder` per string. Safe to use as
    a context manager (`with Embedder() as e: ...`) to close the client
    deterministically; otherwise it is closed on garbage collection.
    """

    def __init__(self, base_url=None, model=None, api_key=None,
                 timeout=DEFAULT_TIMEOUT):
        self.base_url = (
            base_url or os.environ.get("EMBED_BASE_URL", DEFAULT_EMBED_BASE_URL)
        ).rstrip("/")
        self.model = model or os.environ.get("EMBED_MODEL", DEFAULT_EMBED_MODEL)
        self.api_key = api_key or os.environ.get(
            "EMBED_API_KEY", DEFAULT_EMBED_API_KEY
        )
        self.timeout = timeout
        self._client = None
        self._dimension = None

    # -- lifecycle -------------------------------------------------------
    def _http(self):
        if self._client is None:
            self._client = httpx.Client(
                timeout=self.timeout,
                headers={"Authorization": f"Bearer {self.api_key}"},
            )
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

    # -- embedding ------------------------------------------------------
    def embed(self, text, normalize=True):
        """One string -> one vector (list[float]). L2-normalized by
        default, so a plain dot product between two results is their
        cosine similarity."""
        return self.embed_batch([text], normalize=normalize)[0]

    def embed_batch(self, texts, normalize=True):
        """A list of strings -> a list of vectors, in the same order.
        Sent as a single request (the endpoint's `input` array), so this
        is markedly faster than calling `embed()` in a loop when several
        strings are ready at once."""
        if not texts:
            return []
        try:
            resp = self._http().post(
                f"{self.base_url}/embeddings",
                json={"model": self.model, "input": list(texts)},
            )
            resp.raise_for_status()
            payload = resp.json()
        except httpx.HTTPError as exc:
            raise EmbedderError(
                f"embedding request to {self.base_url} failed: {exc}. "
                "Is the embed server running? (./run_embed.sh)"
            ) from exc
        rows = payload.get("data")
        if not rows or len(rows) != len(texts):
            raise EmbedderError(
                f"unexpected embeddings response: {str(payload)[:200]}"
            )
        rows = sorted(rows, key=lambda r: r.get("index", 0))
        vecs = [r["embedding"] for r in rows]
        if self._dimension is None and vecs:
            self._dimension = len(vecs[0])
        if normalize:
            vecs = [_l2_normalize(v) for v in vecs]
        return vecs

    @property
    def dimension(self):
        """Vector length for this model (probed once, then cached)."""
        if self._dimension is None:
            self.embed("dimension probe")
        return self._dimension


# ----------------------------------------------------------------------
# CLI: quick check + throughput benchmark
# ----------------------------------------------------------------------
_BENCH_SAMPLES = [
    # short, crossword-word-like, across all 6 supported languages
    "faucon", "grille", "mystère", "château", "hirondelle", "clavier",
    "falcon", "puzzle", "riddle", "castle", "swallow", "keyboard",
    "Falke", "Rätsel", "Schloss", "Schwalbe", "Tastatur", "Kreuzung",
    "halcón", "acertijo", "castillo", "golondrina", "teclado", "crucigrama",
    "falco", "enigma", "castello", "rondine", "tastiera", "cruciverba",
    "falcão", "enigma", "castelo", "andorinha", "teclado", "palavra",
    # a few short phrases so the mix isn't only single tokens
    "un oiseau de proie rapide",
    "a fast bird of prey",
    "ein schneller Greifvogel",
    "un ave rapaz veloz",
    "un rapace veloce",
    "uma ave de rapina veloz",
]


def _percentile(sorted_vals, pct):
    if not sorted_vals:
        return 0.0
    k = (len(sorted_vals) - 1) * pct / 100.0
    lo = int(math.floor(k))
    hi = int(math.ceil(k))
    if lo == hi:
        return sorted_vals[lo]
    return sorted_vals[lo] + (sorted_vals[hi] - sorted_vals[lo]) * (k - lo)


def _run_benchmark(embedder, count, batch):
    texts = [_BENCH_SAMPLES[i % len(_BENCH_SAMPLES)] for i in range(count)]

    # warm-up (model load / first-call cost is not part of the measure)
    embedder.embed(texts[0])
    dim = embedder.dimension
    print(f"model={embedder.model}  endpoint={embedder.base_url}  dim={dim}")

    # 1) one string per request — the "send a string, get its embedding" path
    per_call = []
    t0 = time.perf_counter()
    for t in texts:
        c0 = time.perf_counter()
        embedder.embed(t)
        per_call.append((time.perf_counter() - c0) * 1000.0)
    total = time.perf_counter() - t0
    per_call.sort()
    print(f"\n[1 string / request]  {count} embeddings in {total:.2f}s")
    print(f"  mean   {total / count * 1000:.1f} ms/embedding"
          f"   ->  {count / total:.1f} embeddings/s")
    print(f"  p50    {_percentile(per_call, 50):.1f} ms"
          f"   p95 {_percentile(per_call, 95):.1f} ms"
          f"   max {per_call[-1]:.1f} ms")

    # 2) batched requests — realistic when many strings are ready at once
    if batch > 1:
        t0 = time.perf_counter()
        for i in range(0, count, batch):
            embedder.embed_batch(texts[i:i + batch])
        total_b = time.perf_counter() - t0
        print(f"\n[{batch} strings / request]  {count} embeddings in {total_b:.2f}s")
        print(f"  mean   {total_b / count * 1000:.1f} ms/embedding"
              f"   ->  {count / total_b:.1f} embeddings/s")

    # cross-lingual sanity: same meaning across languages must score higher
    # than unrelated text.
    a = embedder.embed("un oiseau de proie rapide")
    b = embedder.embed("a fast bird of prey")
    c = embedder.embed("la théorie des nombres premiers")
    dot = lambda x, y: sum(p * q for p, q in zip(x, y))
    print(f"\ncross-lingual check: cos(FR,EN same meaning)={dot(a, b):+.3f}  "
          f"cos(FR,unrelated)={dot(a, c):+.3f}  "
          f"{'OK' if dot(a, b) > dot(a, c) else 'SUSPECT'}")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--text", help="embed this one string and print it")
    parser.add_argument("--benchmark", nargs="?", type=int, const=1000,
                        metavar="N", help="time N embeddings (default 1000)")
    parser.add_argument("--batch", type=int, default=16,
                        help="batch size for the batched benchmark pass "
                             "(default 16; 1 disables it)")
    parser.add_argument("--base-url", help="override EMBED_BASE_URL")
    parser.add_argument("--model", help="override EMBED_MODEL")
    args = parser.parse_args()

    with Embedder(base_url=args.base_url, model=args.model) as embedder:
        if args.text is not None:
            vec = embedder.embed(args.text)
            print(f"dim={len(vec)}  norm={math.sqrt(sum(x * x for x in vec)):.4f}")
            print("first 8:", [round(x, 5) for x in vec[:8]])
        elif args.benchmark is not None:
            _run_benchmark(embedder, args.benchmark, args.batch)
        else:
            parser.print_help()


if __name__ == "__main__":
    main()
