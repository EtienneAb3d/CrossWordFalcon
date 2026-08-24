# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

**CrossWordFalcon** — a crossword grid generator (French, English, German, Spanish, or
Italian), usable from the CLI or from a web UI backed by two FastAPI servers:

- `build_wordlist_freq.py` — one-off preprocessing script that converts a HermitDave
  FrequencyWords source (`data/raw/<lang>_50k.txt`, `word count` per line) into
  `data/wordlist_<lang>_full.tsv`, a `MOT<TAB>ACCENTUE<TAB>FREQUENCE` word list ready
  for the generator — the accented/inflected column preserves the word's natural
  spelling (gender, number, conjugation) alongside the grid's bare accent-stripped
  uppercase form, for `backend/clues.py` to use when writing clues. Used for all five
  languages (fr/en/de/es/it), so they're all built the same way from the same kind of
  source — no language gets special treatment.
- `backend/crossword_gen.py` — the core grid generator library/CLI (all the grid-generation
  business logic lives in `backend/`). `generate_grid()` is the reusable entry point (used
  by both the CLI `main()` and `backend/app.py`); it takes a word list and produces a
  filled, symmetric crossword grid of `width`×`height` cells (15×10 by default —
  independent dimensions, not necessarily square).
- `backend/app.py` — **back** FastAPI server: exposes `generate_grid()` as a JSON API
  (`POST /api/generate`, `GET /api/health`) via a relative import
  (`from .crossword_gen import ...`). No static files, no `/docs`/`/openapi.json`
  (disabled) — any other path 404s by default. The request's `language` field
  (`fr`/`en`/`de`/`es`/`it`) selects the wordlist via the `WORDLISTS` dict — add a
  language by adding an entry there plus a `data/wordlist_<code>_full.tsv`. After
  generating a grid, it calls `backend/clues.py` to fill in a definition for every word
  via an LLM, in the same language as the grid, styled to match the requested
  `difficulty`; if the LLM call fails, the endpoint returns a 502.
- `backend/clues.py` — `LLMClueGenerator`, the one class that owns all LLM handling
  (endpoint config, prompt text, the HTTP call, response parsing); `backend/app.py`
  builds a single instance at module scope and calls `.generate()` per grid. Talks to
  an OpenAI-compatible chat-completions endpoint, split into small batches (`_BATCH_SIZE`
  = 6 words/request) rather than one big request — small local models reliably answer a
  handful of words but degenerate (dropped entries, off-language text) well before the
  token budget on a long list. Output is plain text, not JSON — one line per word, `word:
  clue 1; clue 2; clue 3` (`_parse_response`) — small local models without constrained
  decoding were unreliable at valid JSON syntax; the line format fails one word at a
  time instead of the whole response, and `_parse_response` tolerates a model that
  answers with a numbered/bulleted multi-line list under the header instead. Each word
  is shown to the model by its accented/inflected spelling (`words[i]["accented"]`, not
  the grid's bare form) and the model is asked for 3 candidate clues per word; one is
  picked at random on our side (`_pick_clues`) — never the model's choice — filtering
  out anything that isn't a recognizable clue (empty, or containing non-Latin-script
  characters — a drift failure mode seen in testing). The prompt (`_build_prompt`)
  explicitly forbids restating the
  word/spelling or a bare grammatical label ("verbe avoir 2e personne...") and allows
  synonyms; phrasing style is calibrated by `difficulty` (easy/medium/hard) via
  `DIFFICULTY_STYLE`, language by `LANGUAGE_NAMES[language]` (must match the grid's
  wordlist). The endpoint is configurable via `LLM_BASE_URL`/`LLM_MODEL`/`LLM_API_KEY`
  (see `env.sh`) so it can target either the local llama.cpp server (default, see
  `run_llm.sh`) or a cloud API (e.g. Mistral) with no code change. Used only by
  `backend/app.py` — the offline CLI (`crossword_gen.py`) never calls it.
- `run_llm.sh` — default local LLM launcher: downloads a quantized GGUF (default
  `bartowski/Qwen_Qwen3.5-9B-GGUF`, `Q4_K_M`, ~5.75GB, into `models/`, gitignored) the
  first time, then serves it via `llama_cpp.server` (llama.cpp's built-in
  OpenAI-compatible server — no hand-written wrapper needed). One package
  (`requirements-llama.txt`) covers Linux and macOS alike (Metal on Apple Silicon, CUDA
  on Linux with a GPU, CPU everywhere). Passes `--chat_template_kwargs
  '{"enable_thinking": false}'` — Qwen3.5 is a reasoning model that otherwise burns the
  whole token budget on a `<think>...</think>` block before ever answering, which
  starved `backend/clues.py`'s batches of any usable output (verified: 28s and no
  parsable line without the flag, 4s and a clean answer with it). `backend/hf_server.py`
  (`transformers`-based) and `run_vllm.sh` (vLLM) remain as alternative backends — e.g.
  vLLM for higher-throughput serving of a non-GGUF model on a Linux GPU box — but
  `run_llm.sh`/llama.cpp is what `env.sh` points at by default now.
- `frontend/server.py` — **middleware** FastAPI server: serves the static UI
  (`frontend/static/index.html`, `script.js`, `style.css`) and proxies `/api/*` to the
  backend (via `httpx`, base URL from `CROSSWORDFALCON_BACKEND_URL`, default
  `http://127.0.0.1:8001`) so the browser only ever talks to one origin. Static files are
  served via Starlette's `StaticFiles`, which 404s on anything not present in
  `frontend/static/` (including path traversal attempts) — keep that directory limited to
  the files the page actually needs. The page itself is a playable crossword, not just a
  solution viewer: the grid starts empty, the player types letters into selected cells
  (`script.js` tracks `userLetters` separately from the API's `solution`), with "Solution"
  and "Vérification" toggle buttons to reveal/check answers. UI styling decisions live in
  the `style-guide` SKILL, not here.

`data/wordlist_fr_full.tsv` is the CLI's default dictionary (`--wordlist`); the backend
picks among `data/wordlist_{fr,en,de,es,it}_full.tsv` per the request's `language` (see
`backend/app.py`'s `WORDLISTS`). There is no plain-text fallback list checked into the
repo; `load_wordlist` still accepts a free-text format as a fallback parser, but no file
of that kind ships here.

## Commands

Run everything with the venv's Python (`.venv`, Python 3.14). `pip install -r
requirements.txt` (or `./Install.sh`) installs `fastapi`, `uvicorn[standard]`, `httpx`.

```bash
# Regenerate a wordlist from its raw HermitDave source (only needed if a
# data/raw/<lang>_50k.txt changes; the outputs are already checked into data/)
python3 build_wordlist_freq.py data/raw/fr_50k.txt data/wordlist_fr_full.tsv

# Generate a crossword grid from the CLI (defaults: 15x10, medium difficulty)
python3 backend/crossword_gen.py

# Common CLI options
python3 backend/crossword_gen.py --width 15 --height 15 --difficulty hard --seed 42

# Generate in another language (CLI has no --language flag, just point --wordlist
# at the language's dictionary; en/de/es/it are all pre-built in data/)
python3 backend/crossword_gen.py --wordlist data/wordlist_en_full.tsv

# Web UI: run both servers (separate terminals), then open http://127.0.0.1:8000
uvicorn backend.app:app --port 8001
uvicorn frontend.server:app --port 8000

# Or simply:
./run_Falcon.sh   # stops any server already running on 8000/8001, sources
                  # env.sh (LLM_BASE_URL/LLM_MODEL/LLM_API_KEY) if present,
                  # then relaunches both

# Local LLM for clue generation (default — see env.sh to use a cloud API instead).
# Downloads the GGUF into models/ on first run, then serves it on :8002. Works
# the same way on Linux and macOS (Metal/CUDA/CPU autodetected).
pip install -r requirements-llama.txt
./run_llm.sh
```

`backend/app.py` needs a reachable LLM at `LLM_BASE_URL` to generate word
definitions. Copy `env_default.sh` to `env.sh` (gitignored, not checked in) and
edit it — defaults to the local llama.cpp server above; comment/uncomment the
block in `env.sh` to point at a cloud API (e.g. Mistral) instead.
`run_Falcon.sh` sources `env.sh` automatically before starting the backend.

There is no test suite, linter, or build step in this repo.

## Architecture

`backend/crossword_gen.py` runs a two-phase pipeline, driven by `main()`:

1. **Black-square pattern generation** (`make_symmetric_pattern`): places black
   cells in 180°-symmetric pairs, rejecting any placement that violates
   `is_structurally_valid` (no white run shorter than 3 cells, and all white cells must
   stay 4-connected). Picks each cell from a 32-cell look-ahead window (lowest
   row+column black-cell count wins) rather than strict shuffle order — a soft bias
   against black cells piling up in a few rows/columns ("walls" that split the grid
   into disconnected-looking blocks and force many neighboring words to share the
   same length). Real but partial fix: `minimize_black_squares` below washes out
   much of this bias since it removes cells by fillability alone, not aesthetics —
   see the `project-best-practices` SKILL for what was tried and rejected (a hard
   per-row/column cap cut alignment much further but cost ~55% more generation
   time). Retries with an increasing black-cell ratio (`--black-ratio`, up to
   `--attempts` times) until a fillable pattern is found.

2. **CSP fill** (`Filler` / `_backtrack`): `extract_slots` turns the black/white pattern
   into across/down word slots; `build_index` pre-indexes the word list by
   `(length, position, letter)` so slot domains can be computed by set intersection
   instead of a linear scan (needed because the full lexicon is 100k+ words). Backtracking
   search picks the most-constrained slot first (MRV heuristic) and respects a
   `deadline_checks` budget so a bad grid pattern fails fast instead of hanging. `Filler`
   takes the same seeded `rng` as pattern generation (not the global `random` module —
   that was a real bug: unreproducible fills and, in the web server, a shared-state race
   across concurrent requests) and shuffles candidate words with it. At the 15×10
   default, `_domain()` is still called for every unassigned slot at every backtracking
   node (MRV's cost), so a single `/api/generate` request commonly takes 15-35 seconds —
   this is expected, not a hang; don't "fix" it by adding a timeout without addressing
   the actual solver cost.

3. **Minimization** (`minimize_black_squares`): after a successful fill, greedily tries
   removing each symmetric pair of black cells and re-running the CSP fill; a removal is
   kept only if the grid is still structurally valid and fillable, otherwise it's
   reverted. This densifies the grid (fewer black squares) without ever downgrading from
   a known-good solution.

Word lists are loaded via `load_wordlist`, which expects the
`MOT<TAB>ACCENTUE<TAB>FREQUENCE` format produced by `build_wordlist_freq.py`
(falling back to a 2-column `MOT<TAB>FREQUENCE` or a plain free-text parser if a file
doesn't match that format — accents then just default to the bare word). It returns
`(by_length, accents)`: `by_length` feeds the CSP solver as before, `accents` maps each
grid-usable word to its natural accented/inflected spelling, threaded through
`generate_grid()`'s `words[i]["accented"]` for `backend/clues.py`. `--difficulty`
(`easy`/`medium`/`hard`) caps how many of the most frequent words per length are kept —
fewer words means more recognizable vocabulary but a harder-to-fill grid; `hard` keeps
the entire lexicon.

`build_wordlist_freq.py` reads a HermitDave FrequencyWords source (`data/raw/<lang>_50k.txt`,
`word count` per line, CC-BY-SA, github.com/hermitdave/FrequencyWords), strips
accents/diacritics and uppercases (crossword convention), excludes multi-word/hyphenated/
apostrophe entries and words under 3 letters after normalization, and keeps the raw
occurrence count as the frequency. All five languages (fr/en/de/es/it) go through this
same script — French used to be built from Lexique383 (a richer, linguist-curated lexical
database) via a since-removed `build_wordlist.py`, but that was switched to HermitDave for
consistency with the other four languages, which don't have an equivalent curated source.
