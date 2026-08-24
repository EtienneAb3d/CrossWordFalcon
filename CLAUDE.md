# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

**CrossWordFalcon** — a crossword grid generator (French, English, German, Spanish, or
Italian), usable from the CLI or from a web UI backed by two FastAPI servers:

- `build_sentence_corpus.py` — one-off preprocessing script: downloads a partial
  chunk (`--max-bytes`, default 50MB per source) of two OPUS (opus.nlpl.eu) corpora —
  OpenSubtitles (colloquial/dialogue vocabulary) and Wikipedia (formal/technical
  vocabulary, and rare-but-real words dialogue rarely uses) — per language, merges
  them, and filters out sentences likely to contain a wrong-language part: dropped if
  either a contiguous run of `MAX_INVALID_RUN` (3+) words the language's own Hunspell
  dictionary doesn't recognize, or too high an overall fraction of them
  (`MAX_INVALID_WORD_FRACTION`), calibrated by hand against real contamination (English
  dialogue/quotes leaking into every language's file) vs. genuine sentences with a
  proper noun or two. Output: `data/reference_corpus/<lang>_sentences.txt` (gitignored
  — generated, not source content). Used two ways: `build_wordlist_freq.py` counts
  word occurrences in it (`_count_word_frequencies`); `backend/example_sentences.py`
  looks up real usage examples of a specific inflected form in it (see
  backend/clues.py).
- `build_wordlist_freq.py` — one-off preprocessing script that reads a language's
  reference corpus (`data/reference_corpus/<lang>_sentences.txt`, counting word
  occurrences itself — `_count_word_frequencies`) and writes
  `data/wordlist_<lang>_full.tsv`, a `MOT<TAB>ACCENTUE<TAB>FREQUENCE<TAB>CANONIQUE`
  word list ready for the generator — the accented/inflected column preserves the
  word's natural spelling (gender, number, conjugation) alongside the grid's bare
  accent-stripped uppercase form, for `backend/clues.py` to use when writing clues.
  Used for all five languages (fr/en/de/es/it), so they're all built the same way
  from the same kind of source — no language gets special treatment. There's no
  separate persisted frequency-table file anymore (`data/raw/` and the script that
  used to write it, `compute_word_frequencies.py`, were both retired) — that hand-off
  file only existed for a since-removed HermitDave-based version of this pipeline,
  and once the corpus became this project's own, keeping a distinct on-disk stage
  between two scripts served no purpose. The source corpus isn't purely monolingual
  even after `build_sentence_corpus.py`'s own filtering, so every candidate is
  additionally validated against a real dictionary of its own language: a Hunspell
  dictionary
  (downloaded from LibreOffice/dictionaries, cached in `data/hunspell_cache/`,
  gitignored) is queried with the `hunspell` command-line spellchecker itself (`brew
  install hunspell` / `apt-get install hunspell`) — checking both the word's corpus
  spelling and a title-cased variant, since the corpus lowercases everything but
  languages like German require every noun capitalized. A candidate not recognized
  either way is dropped as noise/contamination; one that's only valid title-cased
  (e.g. German `haus`) is kept with its correct capitalization as the `ACCENTED` value
  (`Haus`), not the corpus's lowercase spelling. Deliberately not `unmunch` (which
  pre-expands a dictionary into every surface form): verified by hand that it silently
  drops many irregular verb conjugations (French être/avoir/vouloir — `SUIS`, `ÉTAIT`,
  `VEUX`...) that `hunspell`'s own spellcheck engine correctly recognizes. `CANONIQUE`
  is every candidate canonical form/lemma Hunspell's morphological analysis
  (`hunspell -m`) finds for the word (semicolon-separated when more than one — a word
  can be genuinely ambiguous between stems, e.g. French `SUIS` -> `suivre;être`) — used
  both to blend 90% the most-frequent candidate's own frequency into `FREQUENCE`
  (correcting subtitle/dialogue-frequency distortion, e.g. French `déterminées` ranked
  far below its infinitive `déterminer` despite being an equally easy word) and, kept
  in full, so `backend/gloss_lookup.py` can look up a real definition by lemma without
  the ambiguity being silently resolved at dictionary-build time.
- `backend/crossword_gen.py` — the core grid generator library/CLI (all the grid-generation
  business logic lives in `backend/`). `generate_grid()` is the reusable entry point (used
  by both the CLI `main()` and `backend/app.py`); it takes a word list and produces a
  filled, symmetric crossword grid of `width`×`height` cells (15×10 by default —
  independent dimensions, not necessarily square). Takes an optional `on_progress(step,
  **data)` callback, invoked at each of the pipeline's real phase transitions (a pattern
  attempt, a successful fill, minimizing, the finished grid) — `None` by default (a no-op)
  so the CLI and any other caller that doesn't care about progress needs no change;
  `backend/app.py` is the only caller that passes one.
- `backend/app.py` — **back** FastAPI server: exposes `generate_grid()` as a JSON API
  via a relative import (`from .crossword_gen import ...`). No static files, no
  `/docs`/`/openapi.json` (disabled) — any other path 404s by default. The request's
  `language` field (`fr`/`en`/`de`/`es`/`it`) selects the wordlist via the `WORDLISTS`
  dict — add a language by adding an entry there plus a
  `data/wordlist_<code>_full.tsv`. Generation is asynchronous, not a single blocking
  call: `POST /api/generate` validates the request, starts the work as a background
  `asyncio` task (`_run_generate_job`, itself running the CPU-bound
  `generate_grid()`/clue-generation calls via `asyncio.to_thread` so the event loop
  stays free), and immediately returns `{"job_id": ...}` (HTTP 202) — the client then
  polls `GET /api/generate/status/{job_id}` for progress and the eventual result.
  `JOBS` is a plain in-memory dict (one uvicorn process, no `--workers`, see
  `run_Falcon.sh` — no locking or external store needed), bounded to `MAX_JOBS` (50)
  entries so a long-running process doesn't grow it forever; each background task is
  kept in `_BACKGROUND_TASKS` purely so `asyncio.create_task`'s result isn't
  garbage-collected mid-run (a documented asyncio footgun — it only holds a weak
  reference otherwise). Every generation step calls a shared `progress(step, **data)`
  closure that both logs to `backend.log` via the standard `logging` module (captured
  by uvicorn's stdout redirect, see `run_Falcon.sh`) and updates the job's `step` field
  for the next status poll to pick up — see `generate_grid()`'s and
  `LLMClueGenerator.generate()`'s `on_progress` parameter. After a grid and its clues
  are both ready, it calls `backend/svg_export.py` to save a durable copy to `GRIDS/`,
  then a PNG rendering of that same SVG to `GRID_SAMPLES/` (`save_grid_png`, via
  `rsvg-convert`), before marking the job done; a failure to save either is logged but
  never fails the request — it's a nice-to-have record, not the point of asking for a
  grid. If the LLM
  call fails, the job ends in `status: "error"` (the old direct 502 doesn't apply
  anymore now that the request itself always returns 202 immediately).
- `backend/clues.py` — `LLMClueGenerator`, the one class that owns all LLM handling
  (endpoint config, prompt text, the HTTP call, response parsing); `backend/app.py`
  builds a single instance at module scope and calls `.generate()` per grid. Talks to
  an OpenAI-compatible chat-completions endpoint, one word per request (`_BATCH_SIZE
  = 1`) rather than a bigger batch — even a handful of words per request degenerated
  (dropped entries, off-language text) on the small local model before finishing; one
  word per call is the size that's actually reliable, at the cost of one HTTP
  round-trip per word (measured live: ~2s/word end to end — this is by far the
  slowest phase of generating a grid, which is why `generate()` takes an optional
  `on_progress(current, total)` callback, called after every word attempt, for
  `backend/app.py` to surface live progress instead of one static "generating…"
  message for what can be up to a couple of minutes). Output is plain text, not
  JSON — one line per word, `word:
  clue 1; clue 2; clue 3` (`_parse_response`) — small local models without constrained
  decoding were unreliable at valid JSON syntax; the line format fails one word at a
  time instead of the whole response, and `_parse_response` tolerates a model that
  answers with a numbered/bulleted multi-line list under the header instead. Each word
  is shown to the model by its accented/inflected spelling (`words[i]["accented"]`, not
  the grid's bare form) and the model is asked for 3 candidate clues per word; one is
  picked at random on our side (`_pick_clues`) — never the model's choice — filtering
  out anything that isn't a recognizable clue: empty, non-Latin-script characters (a
  drift failure mode seen in testing), or the word itself appearing anywhere in the
  clue — as the whole clue, or embedded inside a longer sentence (e.g. "je serais s'il
  pleuvait demain" for `SERAIS`) — in any case/accent (`_contains_target_word`,
  tokenized so it doesn't misfire on an unrelated word sharing the same letters, e.g.
  "château" vs. `CHAT`; the prompt forbids this too, but small models do it anyway —
  the filter is the actual guarantee). A word left with zero candidates after
  filtering, or that the model never answered at all, gets re-queried in a follow-up
  round — `generate()` loops up to 3 rounds, each re-sending only words still missing a
  clue. The prompt (`_build_prompt`) explicitly forbids restating the
  word/spelling, a bare grammatical label ("verbe avoir 2e personne..."), or
  describing the word's spelling/letters instead of its meaning (e.g. "word starting
  with T and ending in EE" for TEE — a real observed failure mode) and allows
  synonyms. Also requires a conjugated verb form's clue to match its exact person,
  number, AND mood/tense together (and, for a noun or adjective, its number —
  singular/plural — and gender), not just the rough meaning (e.g. a third-person
  conditional like SERRERAIT needs a clue that's also third-person conditional, not a
  first-person-future one that only gets the general idea right; a plural noun like
  ANS, "years", needs a clue that itself reads as plural, not one describing a single
  12-month span) — a partial fix, not a complete one, and the failure rate varies a lot
  by word: sampled essentially 0/6 wrong for `SERRERAIT` after the rule's second
  iteration, but `ÉTAIS` (a form of "être", the single most overloaded/high-frequency
  French verb) still failed most of a 6-sample re-test — plausibly harder to correct
  because the model has much stronger competing priors for that one verb specifically;
  `ANS` after the third (noun-number) iteration only reached ~3/8 clearly-plural
  samples. No post-filter for this (unlike the copy-of-word/non-Latin checks):
  grammatical agreement needs real parsing of the *clue text*, not just the target
  word, which isn't a lightweight, reliable, five-languages-at-once check the way
  those are.
  Phrasing style is calibrated by `difficulty` (easy/medium/hard) via
  `DIFFICULTY_STYLE`, language by `LANGUAGE_NAMES[language]` (must match the grid's
  wordlist). The endpoint is configurable via `LLM_BASE_URL`/`LLM_MODEL`/`LLM_API_KEY`
  (see `env.sh`) so it can target either the local llama.cpp server (default, see
  `run_llm.sh`) or a cloud API (e.g. Mistral) with no code change. Used only by
  `backend/app.py` — the offline CLI (`crossword_gen.py`) never calls it. `_build_prompt`
  also grounds the model with real definitions/examples when available
  (`_build_gloss_block`/`_build_examples_block`, backed by `backend/gloss_lookup.py` and
  `backend/example_sentences.py`) — added after the small local model defined French
  `ARE` (the 100 m² land-area unit) as the English verb "to be", confirmed by direct
  testing to be a knowledge gap rather than a prompt-following problem (giving it the
  correct gloss directly fixed the answer every time). Gloss lookup is by the word's
  candidate canonical form(s) (`words[i]["canonical"]`, from `build_wordlist_freq.py`'s
  4th column) — a genuinely ambiguous word (French `suis` -> `être` or `suivre`) shows
  every candidate's definition rather than the dictionary silently picking one; example-
  sentence lookup is by the exact inflected form instead, since Wiktionary is indexed by
  lemma but real usage sentences are not. Both sections are omitted entirely when
  nothing is found for a word — not every word has dictionary or corpus coverage.
- `backend/gloss_lookup.py` — `find_glosses_for_canonicals()`, looks up real
  definitions in the per-language gloss dictionary built by `build_gloss_dictionary.py`
  (`data/gloss_dictionary/<lang>_glosses.jsonl`, gitignored). Loaded and cached in
  memory per language on first use (lazy, once per process lifetime — same pattern as
  `backend/example_sentences.py` below).
- `backend/example_sentences.py` — `find_examples_for_words()`, looks up real usage
  sentences in the per-language reference corpus (`build_sentence_corpus.py`'s output).
  Indexes the *entire* language wordlist once per process lifetime, not just the current
  grid's ~30-50 words, the first time that language is needed (lazy-built,
  `_index_cache`) — tokenizing every line of a multi-million-line corpus and checking
  each token against a large target set costs about the same as checking a small one
  (both are O(1) hash lookups per token), so indexing the whole lexicon up front is
  barely slower than a narrower index would have been, and every later lookup (any
  word, any future grid, same language) becomes a cached-dict read instead of a
  multi-second rescan. Reservoir sampling (per word, capped at `RESERVOIR_SIZE`) keeps
  memory bounded across tens of thousands of words while still returning a uniform
  random sample across every match — not just the first few encountered — each time
  `find_examples_for_words()` is called, so a common word doesn't always show the same
  handful of examples.
- `build_gloss_dictionary.py` — one-off preprocessing script: downloads a language's
  Wiktionary extract in full from Kaikki.org (kaikki.org, CC-BY-SA/GFDL like
  Wiktionary itself) — for English, the primary (English-Wiktionary-sourced)
  extraction, already in English; for French/German/Spanish/Italian, that same
  extraction's glosses are in *English* (English Wiktionary's take on a foreign word),
  so this uses Kaikki's own-language Wiktionary edition instead (`frwiktionary`,
  `dewiktionary`, `eswiktionary`, `itwiktionary`), which gives native-language
  definitions. These files can't usefully be partially downloaded like
  `build_sentence_corpus.py`'s sources — they're not sorted by frequency, so a partial
  download would only ever cover words starting with the first few letters of the
  alphabet — so each is downloaded in full (multi-gigabyte: ~3.2GB for English, ~3.2GB
  French, ~3.3GB German, ~1.4GB Spanish, ~460MB Italian) and immediately filtered down
  to just the lemmas `data/wordlist_<lang>_full.tsv`'s `CANONIQUE` column actually
  needs (a few hundred thousand words at most) — the raw download is deleted right
  after, only the small filtered result (`data/gloss_dictionary/<lang>_glosses.jsonl`,
  gitignored) is kept.
- `backend/svg_export.py` — `save_grid_svg()`, called by `backend/app.py` once a grid
  and its clues are both ready: renders a single self-contained SVG (no external
  assets/fonts) — the empty puzzle (grid + clue lists, grouped/chained the same way
  `frontend/static/script.js`'s `renderClueLines()` does, reimplemented in Python since
  this is backend-only) followed by the fully-solved grid underneath — and writes it to
  `GRIDS/` (project root, gitignored — generated output, not source content), named
  `<timestamp>_<language>.svg` (microsecond precision so two grids finishing in the
  same second, e.g. from two browser tabs, don't collide). A durable record of every
  grid the app produces, since the web UI itself has no export feature and forgets the
  grid the moment the tab closes. Clue-heading text (`_HEADINGS`) duplicates
  `frontend/static/i18n.js`'s `acrossHeading`/`downHeading` strings by hand — keep both
  in sync if a heading ever changes. `save_grid_png()` additionally renders that SVG to
  a PNG of the same basename under `GRID_SAMPLES/` (project root) via the
  `rsvg-convert` CLI (part of `librsvg` — `brew install librsvg` / `apt-get install
  librsvg2-bin`; the same tool already used for `frontend/static/logo.png`, see the
  style-guide SKILL) — unlike `GRIDS/`, `GRID_SAMPLES/` is deliberately **not**
  gitignored: a growing, checked-in visual record of what the app actually produces,
  meant to be committed and evolve across versions, at the user's explicit request.
  Both saves are best-effort (a missing `rsvg-convert`, or any other failure, is logged
  as a warning by `backend/app.py`, never fails the request). `_group_clue_lines()`
  takes the grid's `language` and shows a translated "no definition available"
  placeholder (`_NO_DEFINITION`) for any word whose clue is missing — never the bare
  answer. That used to be the fallback (a word with no clue displayed as its own
  definition), a deliberate early design choice later found to defeat the whole
  point of `backend/clues.py`'s copy/embedded-word filtering: a word that exhausts
  all 3 retry rounds without a valid clue is exactly the case those filters are
  meant to catch, so silently falling back to the answer let "word == definition"
  back in through a side door. `frontend/static/script.js`'s `renderClueLines()`
  mirrors the same fix client-side (`I18N[uiLanguage].noDefinition`, `script.js`
  below).
- `run_llm.sh` — default local LLM launcher: downloads a quantized GGUF (default
  `bartowski/Qwen_Qwen3.5-9B-GGUF`, `Q4_K_M`, ~5.75GB, into `models/`, gitignored) the
  first time, then serves it via `llama_cpp.server` (llama.cpp's built-in
  OpenAI-compatible server — no hand-written wrapper needed). One package
  (`requirements-llama.txt`) covers Linux and macOS alike (Metal on Apple Silicon, CUDA
  on Linux with a GPU, CPU everywhere) — but a plain `pip install` only builds
  llama-cpp-python for CPU; before starting the server, the script checks
  `llama_cpp.llama_supports_gpu_offload()` against whether a GPU should be present
  (macOS, or `nvidia-smi -L` succeeding) and force-reinstalls with the right
  `CMAKE_ARGS` (`-DGGML_METAL=on` / `-DGGML_CUDA=on`) if they disagree, so
  `--n_gpu_layers -1` below isn't silently a no-op. For CUDA specifically, also
  checks `nvcc`/`$CUDACXX` is actually available first (`nvidia-smi` only proves the
  driver is installed, not the CUDA Toolkit needed to compile) and never lets a
  failed rebuild abort the script — falls back to whatever's already installed and
  runs on CPU rather than not starting at all. Passes `--chat_template_kwargs
  '{"enable_thinking": false}'` — Qwen3.5 is a reasoning model that otherwise burns the
  whole token budget on a `<think>...</think>` block before ever answering, which
  starved `backend/clues.py`'s calls of any usable output (verified: 28s and no
  parsable line without the flag, 4s and a clean answer with it). This is the only
  local LLM backend in the repo — see `LLM_BASE_URL` in `env.sh` to point at a cloud
  API (e.g. Mistral) instead.
- `frontend/server.py` — **middleware** FastAPI server: serves the static UI
  (`frontend/static/index.html`, `script.js`, `style.css`) and proxies `/api/*` to the
  backend (via `httpx`, base URL from `CROSSWORDFALCON_BACKEND_URL`, default
  `http://127.0.0.1:8001`) so the browser only ever talks to one origin. `run_Falcon.sh`
  binds it to `0.0.0.0` (LAN-reachable, e.g. from a phone on the same network) — the
  back end stays on `127.0.0.1` only, it's never meant to be reached directly. Proxies
  both `POST /api/generate` (now fast — the backend responds immediately with a
  `job_id`, see above — so this needs only a short timeout, not the long one a single
  blocking call used to require) and the new `GET /api/generate/status/{job_id}` the
  frontend polls for progress. A blanket `@app.middleware("http")` sets
  `Cache-Control: no-store` (plus `Pragma`/`Expires`) on every response — static files
  and `/api/*` alike — so the browser never caches a stale `script.js`/`style.css`/etc.
  while the app is being edited directly. Static files are
  served via Starlette's `StaticFiles`, which 404s on anything not present in
  `frontend/static/` (including path traversal attempts) — keep that directory limited to
  the files the page actually needs. The page itself is a playable crossword, not just a
  solution viewer: the grid starts empty, the player types letters into selected cells
  (`script.js` tracks `userLetters` separately from the API's `solution`), with "Solution"
  and "Vérification" toggle buttons to reveal/check answers. After submitting the form,
  `script.js`'s `pollJob()` repeatedly polls the status endpoint (every
  `POLL_INTERVAL_MS`, 700ms) and turns each `{code, ...data}` step into a localized
  message via `describeStep()` — falls back to the generic "generating…" message for a
  step code it doesn't recognize, so an older frontend build never breaks against a
  newer backend. Errors are localized the same way: a backend/proxy error carries a
  machine-readable `code` (`error_code` on a job, or `{"code": ...}` as
  `frontend/server.py`'s own `HTTPException` detail when it can't reach the back end at
  all) that `describeErrorCode()` maps to the UI's current language, falling back to
  whatever raw text the backend sent for a code it doesn't recognize. UI
  styling decisions live in the `style-guide` SKILL, not here.
- `frontend/static/i18n.js` — the internationalization config: every user-visible
  interface string (labels, buttons, headings, progress/status messages, error
  messages), for every supported language (fr/en/de/es/it), as one `I18N` object —
  pure data, no logic, loaded as a plain `<script>` before `script.js` in
  `index.html` (both are classic non-module scripts sharing the global scope, so
  order in `index.html` is what makes `I18N` available to `script.js`, not an
  import). `applyTranslations()` (in `script.js`) walks every `[data-i18n]` element in
  the DOM plus `document.title` on each language change; `describeStep()`/
  `describeErrorCode()` (also `script.js`) look up the rest (parameterized progress/
  error messages, which can't be plain `data-i18n` text since they need values
  interpolated in). Generated crossword content itself (the words/clues) is never in
  this file — that's written by the backend LLM in the request's `language`, a
  separate concern from the UI's own chrome. Keep this file current whenever any
  UI-visible string changes anywhere in `frontend/static/` — see the
  `project-best-practices` SKILL.

`data/wordlist_fr_full.tsv` is the CLI's default dictionary (`--wordlist`); the backend
picks among `data/wordlist_{fr,en,de,es,it}_full.tsv` per the request's `language` (see
`backend/app.py`'s `WORDLISTS`). There is no plain-text fallback list checked into the
repo; `load_wordlist` still accepts a free-text format as a fallback parser, but no file
of that kind ships here.

## Commands

Run everything with the venv's Python (`.venv`, Python 3.14). `pip install -r
requirements.txt` (or `./Install.sh`) installs `fastapi`, `uvicorn[standard]`, `httpx`.

```bash
# Full pipeline to rebuild one language's wordlist from scratch (only needed to
# refresh the source corpus/frequencies; data/wordlist_*.tsv are already checked
# into data/). Each language is independent — no particular build order required.
python3 build_sentence_corpus.py fr    # downloads OpenSubtitles+Wikipedia, filters
python3 build_wordlist_freq.py fr      # counts words, validates, writes wordlist_fr_full.tsv

# Optional: build the gloss dictionary backend/clues.py grounds clues with
# (large one-time download per language — see build_gloss_dictionary.py)
python3 build_gloss_dictionary.py fr

# Generate a crossword grid from the CLI (defaults: 15x10, easy difficulty)
python3 backend/crossword_gen.py

# Common CLI options
python3 backend/crossword_gen.py --width 15 --height 15 --difficulty hard --seed 42

# Generate in another language (CLI has no --language flag, just point --wordlist
# at the language's dictionary; en/de/es/it are all pre-built in data/)
python3 backend/crossword_gen.py --wordlist data/wordlist_en_full.tsv

# Web UI: run both servers (separate terminals), then open http://127.0.0.1:8000
uvicorn backend.app:app --port 8001                       # 127.0.0.1 only, internal
uvicorn frontend.server:app --host 0.0.0.0 --port 8000    # LAN-reachable

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
`MOT<TAB>ACCENTUE<TAB>FREQUENCE<TAB>CANONIQUE` format produced by `build_wordlist_freq.py`
(falling back to a 3- or 2-column format, or a plain free-text parser, if a file doesn't
match that format — accents/canonical then just default to the bare word). It returns
`(by_length, accents, canonicals)`: `by_length` feeds the CSP solver as before, `accents`
maps each grid-usable word to its natural accented/inflected spelling (threaded through
`generate_grid()`'s `words[i]["accented"]`), `canonicals` maps it to its candidate
canonical form(s)/lemma(s) (`words[i]["canonical"]`) — both used by `backend/clues.py`.
`--difficulty` (`easy`/`medium`/`hard`) caps how many of the most frequent words are
kept — fewer words means more recognizable vocabulary but a harder-to-fill grid; `hard`
keeps the entire lexicon. The cap (`DIFFICULTY_PRESETS`: easy=25 000, medium=50 000,
hard=uncapped) is applied *globally* (ranked across all lengths together), not per
length — a per-length cap doesn't actually restrict short lengths that have fewer total
words than the cap itself (a real bug: French has only ~700 3-letter words, so an
earlier 600-per-length "easy" cap let through every one of them, including obscure
entries like `ABD`, score 103, ~33 000th globally). "Easy" specifically also requires a
findable definition: `load_wordlist(..., require_gloss=True)` drops any word with no
gloss-dictionary entry (`backend/gloss_lookup.py`'s `has_any_gloss`) under either its
inflected spelling or any candidate canonical form — frequency rank alone doesn't catch
a word that's merely common yet undefinable (verified: 106 additional words dropped
from French `easy` this way, mostly foreign proper nouns that had passed Hunspell
validation as loanword-like tokens). Falls back to no-op (silently skips the
gloss-filter) if the wordlist's language can't be inferred from its filename or it has
no gloss dictionary built — `require_gloss` never breaks a caller that doesn't have one.

`build_wordlist_freq.py` counts word occurrences directly from a language's reference
corpus (`data/reference_corpus/<lang>_sentences.txt`, `_count_word_frequencies`) —
strips accents/diacritics and uppercases (crossword convention), excludes words under 3
letters after normalization, and keeps the occurrence count as the frequency. All five
languages (fr/en/de/es/it) go through this same script. This pipeline used to read the
third-party HermitDave FrequencyWords lists as a persisted `data/raw/<lang>_50k.txt`
(and before that, French alone used Lexique383 via a since-removed `build_wordlist.py`)
— switched to a self-built corpus (`build_sentence_corpus.py`) so every step from raw
corpus to frequency number is something this project controls, after a concrete
quality problem (French `ARE`'s frequency count included English-contamination
occurrences under HermitDave) couldn't be fixed without leaving HermitDave's own
processing behind; `data/raw/` and the intermediate script that wrote it
(`compute_word_frequencies.py`) were later retired too, once persisting that count
between two scripts no longer served a purpose. Every candidate (all five languages) is additionally checked against
a Hunspell dictionary for that language (`HUNSPELL_SOURCE`, fetched from
LibreOffice/dictionaries and cached in `data/hunspell_cache/`, gitignored) using the
`hunspell` CLI's own spellchecker (`_spellcheck_valid`) — both the word's corpus
spelling and a title-cased variant, since some languages (German) require every noun
capitalized while the corpus lowercases everything; whichever form actually validated
becomes the `ACCENTED` value, not necessarily the corpus's original casing. A first
attempt used `unmunch` to pre-expand each Hunspell dictionary into a static wordlist and
tested membership against that — rejected after finding it silently drops many irregular
verb conjugations (French être/avoir/vouloir) that the real `hunspell` spellchecker
correctly recognizes, almost certainly an affix-flag-chaining limitation in `unmunch`'s
naive enumeration rather than in the dictionaries themselves. Requires the `hunspell`
package (`brew install hunspell` / `apt-get install hunspell`) only when (re)building
wordlists — not a runtime dependency of the app itself.

The `FREQUENCY` column written to `data/wordlist_<lang>_full.tsv` (the value
`crossword_gen.load_wordlist()` sorts on and caps per `--difficulty`) is not the raw
source count as-is: dialogue-heavy corpora underrepresent some inflected forms that are
nevertheless perfectly easy, common words (e.g. French "déterminées" is rarer in
dialogue than its infinitive "déterminer", despite being an equally easy word).
`_stem_map` runs `hunspell -m` (morphological analysis) on every kept word to find its
candidate canonical form(s) — when a word is genuinely ambiguous between stems (French
"suis" parses as either "être" or "suivre"), whichever candidate is itself most
frequent in this same wordlist is used for the frequency blend below, but **every**
candidate is kept in the `CANONICAL` column (semicolon-separated) for gloss lookup —
see `backend/gloss_lookup.py`; committing to a single "most likely" candidate would be
the wrong layer to resolve a genuine ambiguity the LLM should resolve using the word's
actual clue-writing context. The written frequency score is `CANONICAL_WEIGHT` (0.9)
times the (most-frequent candidate) canonical form's own frequency plus 0.1 times the
word's own raw frequency — enough to correct the corpus-frequency distortion while
still ranking different inflected forms of the same stem against each other (rather
than collapsing them all to one identical score). This is a two-edged blend, not a
one-way boost: it can also pull an already very-common conjugated form's score *down*
when its own raw frequency happens to exceed its infinitive's (verified: French
"suis"/"était" both dropped somewhat, since "être"'s own raw count is lower than
either) — an accepted, symmetric consequence of weighting toward the canonical form,
not a bug.
