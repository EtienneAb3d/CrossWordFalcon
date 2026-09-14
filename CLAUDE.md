# CLAUDE.md

This file is the technical reference for how CrossWordFalcon currently
works: architecture, module responsibilities, API surface, data formats,
and the grid-generation algorithm. It documents the **current state only**
— no history, no "at the user's request", no bug-fix narratives. For
project-management conventions (permanent rules, install/data-pipeline
maintenance, things that would cause a real regression if violated), see
the `project-best-practices` SKILL — **its rules are mandatory and must be
followed on every request**, not just consulted when convenient; invoke it
and apply its checklist (VERSION.txt bump, i18n sync, requirements.txt
sync, etc.) even when not explicitly reminded. For visual/UI decisions,
see the `style-guide` SKILL. For a deep, French, user-facing explanation
of the algorithm/data-pipeline/UI respectively, see `DOC_ALGO/FR/ReadMe.md`,
`DOC_DIC/FR/ReadMe.md`, `DOC_USER/EN/ReadMe.md` (English) — those and this
file must agree on current facts, but this file goes into more
implementation depth.

## What this is

**CrossWordFalcon** is a crossword grid generator for six languages
(French, English, German, Spanish, Italian, Portuguese), usable from the
CLI (`backend/crossword_gen.py`) or from a browser-playable web app backed
by two FastAPI servers: `backend/app.py` (the API, port 3001) and
`frontend/server.py` (static SPA + reverse proxy, port 3000 — the only
origin the browser ever talks to). Clue writing and every other natural-
language feature (chat, definitions, paraphrases, theme expansion) go
through a local or cloud LLM behind an OpenAI-compatible chat-completions
endpoint (`backend/clues.py`/`backend/chatbot.py`); "Thématique"/
"Synonymes" search goes through a local multilingual embedding server
(`backend/embedder.py`) plus a Qdrant vector database (`backend/
qdrant_store.py`), both optional.

Engineering language is English (code, comments, this file, the SKILLs,
`README.md`); product content — crossword words/clues and every UI string
— is written in whichever of the six supported languages applies.

## Repository map

| Path | Contents |
|---|---|
| `backend/` | All Python business logic: the API server, the generation engine, LLM/chat/embedding clients, persistence, export. No subpackages — every `.py` file sits directly under `backend/`. |
| `frontend/` | `server.py` (proxy + static host) and `static/` (the whole single-page app: `index.html`, `script.js`, `style.css`, `i18n.js`, logo assets). |
| `data_builder/` | One-off/periodic scripts that build each language's dictionary artifacts (corpus → wordlist → gloss dictionary → inflection table → Qdrant embeddings), plus one orchestration shell script per language. |
| `scrapper/` | Daily-refreshed content scrapers feeding the web UI's "Actu Croisée" panel (RSS feeds, aggregated crossword-publisher links). |
| `Automation/` | `Populate.py`, a CLI daemon that drives the real `/api/generate` endpoint to bulk-fill the grid library. |
| `data/` | Per-language dictionaries (`wordlist_<lang>_full.tsv`, `gloss_dictionary/`, `inflection/`), the reference sentence corpus, and Qdrant's on-disk storage. |
| `DOC_ALGO/FR/`, `DOC_DIC/FR/`, `DOC_USER/EN/` | Hand-maintained, present-tense-only reference docs (see intro). |
| `GRID_STORE/`, `GRID_WORK/`, `GRID_GAME/` | Persisted grids: published library grids, in-progress "Interactif" authoring drafts, per-player play state. All gitignored. |
| `GRID_SVG/`, `GRID_PNG/` | Generated SVG/PNG exports of every finished grid. Gitignored. `GRID_SAMPLES/` (committed) is a small, hand-curated set of examples — never written automatically. |
| `LOG_LLM/`, `LOG_CHAT/`, `LOG_THEME/`, `LOG_USERS/` | Diagnostic/audit logs (LLM call traces, chat transcripts, theme pre-search traces, daily presence headcount). Gitignored. |
| `RSS/`, `SCRAPP/` | Daily-refreshed scraper output caches. Gitignored. |
| `CORPUS/`, `DICS/` | Raw downloaded source caches for the dictionary pipeline (OPUS corpora, Wiktionary/Kaikki dumps). Gitignored. |
| `env.sh` / `env_default.sh` | Runtime configuration (ports, LLM/embed/Qdrant endpoints, model choice). `env.sh` is gitignored (real machine config); `env_default.sh` is the checked-in template. |
| `run_*.sh`, `Install*.sh` | Launch/setup scripts — see "Environment, ports, launch scripts" below. |
| `requirements.txt`, `requirements-llama.txt` | Base web-server dependencies (`fastapi`, `uvicorn[standard]`, `httpx`) and the optional local-LLM dependency (`llama-cpp-python[server]`), respectively. |
| `VERSION.txt` | Single version string, read by both the CLI and the web UI's version badge. |

## Data pipeline & data files

Each of the six languages has its own dictionary, built independently by
`data_builder/build_<lang>.sh` through six ordered stages:

1. **`build_sentence_corpus.py`** — downloads and merges five OPUS
   (opus.nlpl.eu) corpora (OpenSubtitles, Wikipedia, Books, TED2013,
   CCMatrix), filters for sentence length and language purity (Hunspell-
   based), and writes two files: `data/reference_corpus/<lang>_
   sentences_full.txt` (every kept sentence — this stage's real output,
   used by stage 2) and `<lang>_sentences.txt` (a reproducible random
   sample capped for distribution — used at runtime by `backend/
   example_sentences.py`). Both gitignored.
2. **`build_wordlist_freq.py`** — counts word occurrences over the full
   corpus and writes `data/wordlist_<lang>_full.tsv`, four tab-separated
   columns: `MOT` (bare, accent-stripped, uppercase — the grid form),
   `ACCENTUE` (natural accented/inflected spelling), `FREQUENCE` (a
   blended frequency score favoring the word's own canonical/lemma form),
   `CANONIQUE` (one or more `;`-separated candidate lemmas). Every
   candidate is Hunspell-validated; a likely proper noun (detected via
   the as-is-vs-title-cased Hunspell signal, skipped for German where
   every noun is capitalized) has its score halved. Committed.
3. **`build_gloss_dictionary.py`** — downloads each language's own-
   language Wiktionary extract from Kaikki.org (English edition for
   English, since it's already native there), filters to the lemmas
   `CANONIQUE` needs, and writes `data/gloss_dictionary/<lang>_glosses.
   jsonl` (one JSON object per line: `{"word", "entries": [{"pos",
   "glosses"}]}`). Committed.
4. **`compress_reference_corpus.py`** — packages the capped corpus file
   into `data/reference_corpus_<lang>.tar.xz` for distribution (a
   sibling of the gitignored `data/reference_corpus/` directory, so it
   escapes that ignore rule). Committed, one archive per language.
5. **`build_inflections.py`** — downloads the English-Wiktionary Kaikki
   dump (its tags are structured and consistent across languages, unlike
   the own-language editions), extracts every `form-of` sense's
   grammatical tags, filters to `data/wordlist_<lang>_full.tsv`'s own
   surface forms, and writes `data/inflection/<lang>.jsonl` (`{"form",
   "analyses": [{"pos", "tags", "lemma"}]}`) — used by `backend/
   inflection_lookup.py` for clue-writing's grammar grounding. Committed,
   plain uncompressed.
6. **`qdrant_populate.py`** (`WordEmbeddingIndexer`) — embeds and upserts
   every wordlist word into a local Qdrant vector collection (optional;
   only needed for the "Thématique"/"Synonymes" features). Non-fatal if
   Qdrant/the embed server isn't running.

Each stage is idempotent and reuses its own on-disk cache
(`CORPUS/`/`DICS/`/`data/hunspell_cache/`) — a full re-run only re-fetches
what's missing. Re-running any *earlier* stage should always be followed
by every later one, since each depends on the previous stage's exact
output (a wordlist rebuild can add/drop lemmas the gloss dictionary or
Qdrant index haven't caught up with).

`backend/gloss_lookup.py`/`backend/example_sentences.py`/`backend/
inflection_lookup.py` each lazily build and cache their own index once
per process lifetime, never per request.

## Backend (`backend/`)

### `app.py` — the API server

A single-process FastAPI app (never run with `--workers` — see "ports and
launch scripts" below) exposing every JSON endpoint the web UI (and
`Automation/Populate.py`) uses. No static files, no `/docs`/`/openapi.
json`. Holds all server-side state in plain module dicts/lists:

- **`JOBS`** (`dict[job_id, dict]`, bounded to `MAX_JOBS=50`, oldest
  evicted first) — every generation/interactive-authoring job. An entry
  carries `status` (`running`/`done`/`cancelled`/`error`), a live `step`
  progress dict, `result` once finished, `examples_history` (an append-
  only log of attempt-preview snapshots the web UI replays), `clues_
  progress`, `resume_state` (set only on total failure, feeds the
  "Continuer" button), the original `request`, and `interactive` (JSON-
  safe session metadata, `None` for an ordinary generation). A companion
  `CANCEL_EVENTS[job_id]` (a real `multiprocessing.Event`, since worker
  processes share no memory with the parent) and, for authoring sessions,
  `INTERACTIVE_SESSIONS[job_id]` (non-serializable: the built word index,
  theme word set, per-session RNG) are evicted in lockstep.
- **`GRID_QUEUE`/`CLUES_QUEUE`** — two plain lists enforcing single
  concurrency for, respectively, the CPU-bound grid search and the GPU/
  LLM-bound clue-writing pass. A job appends itself, waits for its own
  turn (`_wait_in_queue`, reporting live queue position, honoring "Stop"
  even while merely waiting), then runs. Fair-scheduling preemption
  (`_make_should_pause`) sends a job that's held a queue slot longer than
  `MAX_TURN_DURATION_S` (15 min) back to the tail if anyone else is
  waiting, resuming later from its own saved state; a Populate-sourced
  job (`req.source == "populate"`) yields immediately, with no grace
  period, to any real user's job in the same queue.
- **Background tasks**, registered on `@app.on_event("startup")`: a daily
  RSS/SCRAPP refresh (`_rss_daily_scheduler`, `RSS_FETCH_HOUR=8` local
  time) plus a startup catch-up if today's refresh is missing; a presence
  sweep every `PRESENCE_SWEEP_INTERVAL_S` (10s) pruning stale heartbeats
  and logging headcount changes to `LOG_USERS/`; a resource-usage sampler
  every `RESOURCE_USAGE_SAMPLE_INTERVAL_S` (2s), run on its own dedicated
  thread so it's never starved by a generation job's blocking work.

**Endpoints**, grouped:

- *Health/presence*: `GET /api/health`, `POST /api/presence` (heartbeat →
  online count + CPU/GPU occupancy + queue lengths), `POST /api/pseudo/
  claim` (claims/verifies a nickname's secret word via `backend/secret_
  store.py`), `GET /api/system_info`, `GET /api/rss`, `GET /api/scrapp`.
- *Library*: `GET|POST /api/library` (paginated, filterable listing),
  `GET /api/library/{grid_id}` (optionally merges a player's saved play
  state), `GET /api/library/{grid_id}/pdf` (answer-free printable sheet),
  `POST /api/game/save` (autosave play state to `GRID_GAME`).
- *Dictionary/paraphrase*: `GET /api/dictionary` (root-family search),
  `GET /api/dictionary/define` (LLM candidate definitions), `GET /api/
  paraphrase` (LLM paraphrases), `GET /api/similar_words` (LLM-expanded +
  Qdrant "Thématique"), `GET /api/synonyms` (plain Qdrant nearest-
  neighbor, no LLM), `GET /api/theme/random` (a random dictionary word
  handed to the LLM as an inspiration seed, returning an invented theme
  phrase — used by `Automation/Populate.py`, not the web UI itself).
- *Generation lifecycle*: `POST /api/generate` (202, starts a background
  job), `GET /api/generate/status/{job_id}` (full polled state), `GET
  /api/generate/phase/{job_id}` (condensed phase code, used by Populate),
  `POST /api/generate/cancel/{job_id}`, `POST /api/generate/continue/
  {job_id}` (resume from `resume_state`), `POST /api/recompute`
  (regenerate only a stored grid's clues, saved as a new "(Vn)" copy).
- *Interactive mode* (word-by-word manual authoring): `POST /api/
  interactive/start`, `/step` (place one word), `/clean` (remove/blacken
  impossible zones, optional deep mode), `/candidates` (dictionary words
  for a slot), `/crossing` (letter/word options at one cell's crossing),
  `/impossible` (read-only diagnostic recompute), `/verify` (dictionary-
  membership check), `/title` (LLM proposals), `/save` (publish),
  `/save_work` (autosave draft), `GET /work` + `/work/delete` (drafts
  list/delete), `/resume` (relaunch a draft), `/from-library` (reopen a
  published grid as an editable draft), `/finish` (hand the current grid
  to automatic generation, locking already-placed letters — see below).
- *Chat*: `POST /api/chat` (streamed "David FALCON" reply).
- *Qdrant admin* (localhost-only, gated by `frontend/server.py`): `GET
  /api/qdrant/admin`, `POST /api/qdrant/admin/recreate`, `POST /api/
  qdrant/admin/delete-tenant`.

**Key request models** (Pydantic, all in `app.py`): `GenerateRequest`
(`language`, `bilingual_language`, `width`/`height` [5-30], `difficulty`
[easy/medium/hard], `seed`, `force_letters_percent` [0-100, default 1],
`black_enrichment_percent` [0-100, default 17], `mode` [flash/turbo/fast/
medium/ultra, default medium], `pseudo`, `theme`, `theme_precision`
[0.0-1.0, default `THEME_MIN_SCORE`], `source`); `RecomputeRequest`;
the `Interactive*Request` family (`Step`, `Clean`, `Candidates`,
`Crossing`, `Impossible`, `Verify`, `Title`, `Save`, `SaveWork`, `Finish`
— `Finish` additionally carries an optional `zone_cells` to scope
automatic completion to a selected region instead of the whole grid,
`WorkId`, `FromLibrary`); `PresenceRequest`, `PseudoClaimRequest`,
`GridGameSaveRequest`, `LibraryListRequest`, `QdrantTenantRequest`,
`ChatRequest`/`ChatMessage`.

`BUDGET_MODES = {flash: 1000, turbo: 10000, fast: 100000, medium: 500000,
ultra: 5000000}` sets the CSP search-check budget per attempt for the
"Mode" selector. `WORDLISTS` maps each of the six language codes to its
`data/wordlist_<lang>_full.tsv` path.

### `crossword_gen.py` — the grid-generation engine

Both a CLI (`python3 backend/crossword_gen.py ...`, run from the repo
root) and the library `backend/app.py` imports. `load_wordlist()` reads a
`wordlist_<lang>_full.tsv`, returning `(by_length, accents, canonicals,
frequencies)`; `build_index()` turns that into a `(length, position,
letter) → word set` index for fast domain computation. `DIFFICULTY_
PRESETS = {easy: 0.66, medium: 0.80, hard: 1.0}` are *fractions* of the
gloss-filtered lexicon kept (not fixed counts, so the effect is
comparable across languages with very different vocabulary sizes); "easy"
additionally requires a findable Wiktionary gloss and excludes likely
proper nouns; "medium" allows up to `MAX_PROPER_NOUNS["medium"]=2` and
"hard" up to 5 (never restricted otherwise).

**Grid generation is a three-phase pipeline**, run inside `generate_grid`
across up to `attempts` (default 200) *paliers*, each running
`PARALLEL_ATTEMPTS` (default: `CROSSWORDFALCON_PARALLEL_ATTEMPTS` env
override, else `os.cpu_count()`) independent worker processes in parallel
via `ProcessPoolExecutor`:

1. **Black-cell placement** (`make_pattern`) — places black cells one at
   a time (never symmetric pairs), via `_place_black_cells`: a 32-cell
   look-ahead window preferring the row/column with the fewest black
   cells so far, restricted to non-adjacent candidates satisfying
   `STRUCTURAL_MIN_INTERIOR_FREE=8` (an interior white zone must be at
   least this long), relaxed down to 1 before ever accepting adjacency —
   and adjacency is never accepted at all on a call's very first, blank
   palier. A pre-fill phase (`_prefill_unfillable_slots`) runs first (and
   again after ratio-based placement, whenever letters are already
   locked) to blacken any slot whose length has too few dictionary
   candidates (`PREFILL_MIN_WORD_COUNT=3`) or whose already-locked
   letters leave too few exact matches (`PREFILL_LOCKED_MIN_WORD_
   COUNT=3`) — pre-fill cells always count toward the density target. The
   overall black-cell rate (web UI "Taux noir", `black_enrichment_
   fraction`, default `POST_PREFILL_BLACK_FRACTION=0.10`) is scaled by
   how much of the grid is still white at the start of *this* palier, so
   later paliers add proportionally less.
2. **CSP fill** (`Filler`/`_backtrack`, via `try_fill`) — a standard
   recursive backtracking solver. Slot selection (`_select_target_slot`,
   also reused verbatim by `interactive_place_word`) is a fixed 7-level
   cascade: (1) draw across-vs-down weighted by remaining open-slot
   count; (2) among slots ≥4 letters, prefer ones with fewer than
   `PREFILL_MIN_WORD_COUNT` real candidates, if any; (3) among those,
   prefer ones with at least one already-known letter, if any; (4) for a
   themed grid, prefer ones where an unused theme word still fits, if
   any; (5) score the remaining group geometrically (distance² from the
   top-left corner) and take a random draw among the `SLOT_SELECTION_
   WINDOW_SIZE=10` lowest-scored; (6) re-sort that window by letters-
   already-placed (most first), keep the top `SLOT_SELECTION_REFINE_
   FRACTION=1/2`; (7) re-sort by statistical fill-option richness
   (`_slot_letter_frequency_score`), highest wins. Candidate word order
   within the chosen slot: shuffled, then ranked by `_candidate_score`
   (sum of squared per-cell statistical letter scores from `sample_
   letter_biases`) with a random draw inside a `CANDIDATE_SCORE_
   WINDOW=20000`-word sliding window; a themed grid's matching words are
   pulled to the front as a stable block first. The search checks a
   `deadline_checks` budget, a cooperative `cancel_event` (every
   `CANCEL_CHECK_INTERVAL=500` calls), and self-abandons
   (`UNFILLABLE_ABANDON_FRACTION=0.30`) once too much of the grid belongs
   to an already-impossible zone.
3. **Minimization** (`minimize_black_squares`) — once a fill succeeds,
   iteratively removes black cells one at a time, keeping a removal only
   if the grid stays fillable (at the loosest structural bound,
   `min_interior_free=1`, since this phase only ever lengthens slots) and
   every resulting word is a genuine dictionary entry.

**Cross-palier retry** (the mechanism that lets a failed palier's real
progress survive into the next one, rather than starting every palier
from scratch): after a failed palier, `still_has_hope` is true iff some
unassigned slot exists that is neither itself impossible nor touching an
impossible one — in that case the next palier reuses the exact same
pattern verbatim (`_pattern_continue`, never calling `make_pattern`
again), first stripping any word that directly crosses an impossible
slot (`_clean_blocked_slots`) to free those cells for a fresh attempt.
This can repeat for up to `MAX_CONSECUTIVE_CONTINUE_PALIERS=4`
consecutive paliers before a full cleanup is forced regardless (and
immediately, if every one of a palier's parallel attempts independently
gave up as `abandoned_too_unfillable`). Once no slot can usefully be
continued, a full cleanup (`_build_retry_seed`) runs: remove every word
crossing an impossible slot, keep every surviving letter as the next
palier's locked constraint, and reopen any black cell that neither
bounds a surviving word nor sits sandwiched between two confirmed
letters on the same axis — then generate a brand new pattern
(`_pattern_attempt`) from that state. `GRID_REPEAT_INFEASIBLE_
THRESHOLD=3` caps how many consecutive full-cleanup paliers may produce
an identical grid state before a hard reset to a blank grid.

`generate_grid`'s signature accepts `width`/`height`/`difficulty`/
`max_words`/`black_ratio`/`attempts`/`seed`/`wordlist_path`/`on_
progress`/`force_letters_fraction`/`cancel_event`/`black_enrichment_
fraction`/`deadline_checks`/`resume_state`/`should_pause`/`bilingual_
wordlist_path`/`priority_words`/`bilingual_priority_words`/`permanent_
locked_letters`/`permanent_black_cells`/`required_cells`. It returns
`{width, height, pattern, solution, words, word_count, black_count,
black_ratio, winning_process_number, language, bilingual_language}` —
`words` is a list of per-slot dicts (`answer`, `accented`, `canonical`,
`language`, `row`/`col`/`direction`/`number`), each later filled in with
a `clue` by `backend/app.py`.

**Bilingual grids** (across words in one language, down words in
another) are supported throughout via `DualIndex`/`DualSet` wrapper
classes: for a monolingual grid both directions resolve to the identical
underlying dictionary/index (a true no-op); for a bilingual one, every
lookup point in the solver (`Filler._domain`, `sample_letter_biases`, the
pre-fill length/candidate checks) resolves the right dictionary via
`index.for_cells(cells)`/`.for_direction(direction)` based on `slot_
direction(cells)`.

**Themed generation**: `priority_words`/`bilingual_priority_words` (sets
of preferred words, built by `backend/app.py` from an LLM-expanded,
Qdrant-searched glossary — see below) are a soft preference throughout
the CSP fill (cascade level 4 above, plus front-loading matching
candidates within a chosen slot) — never a hard restriction; the full
lexicon is always still available.

**`GenerationCancelled`**/**`GenerationPaused`** are cooperative
exceptions checked at palier boundaries, inside `_backtrack` (every
`CANCEL_CHECK_INTERVAL` calls), inside `minimize_black_squares`'s
removal loop, and between words during clue generation — no worker
process is ever force-killed; a check simply returns/raises at its next
natural checkpoint.

**Interactive-authoring module functions** (backing the web UI's
"Interactif" word-by-word mode, all operating on a plain `grid`/`rows`/
`cols`/`index` without any of the parallel-attempt machinery above):
`interactive_place_word` (places exactly one more word, no backtracking,
reusing the same 7-level slot-selection cascade); `interactive_clean_
impossible_zones`/`interactive_minimize_black_cells` (manual equivalents
of the automatic cleanup, the latter additionally trying to remove every
black cell outright); `interactive_slot_candidates` (dictionary words
fitting a slot's known letters, theme matches unbounded, others capped
at `INTERACTIVE_SLOT_CANDIDATES_LIMIT=300`); `interactive_crossing_
words` (letter/word options at one cell, both directions at once);
`_interactive_fill_diagnostics` (returns `(impossible_cells, low_
candidate_cells)` for the live red/orange grid highlighting, also
catching a word invented purely by crossing letters that isn't real).

"Finir la grille"/"Finir la zone" reuses the ordinary automatic pipeline
via `permanent_locked_letters`/`permanent_black_cells` (every already-
placed cell becomes a hard, permanent constraint) and `required_cells`
(when a zone is selected rather than the whole grid, only that zone's
cells must end up resolved for the search to declare success — cells
outside it may remain unresolved).

### `clues.py` — `LLMClueGenerator`

Owns all LLM interaction for clue/title/definition/paraphrase writing.
Talks to any OpenAI-compatible chat-completions endpoint (`LLM_BASE_URL`/
`LLM_MODEL`/`LLM_API_KEY`, local llama.cpp/SGLang by default, a cloud API
via `env.sh` with no code change). One HTTP call per word
(`_BATCH_SIZE=1`, more reliable on a small local model than batching),
asking for 4 lines: an `A=` grammatical-analysis line (part of speech +
full inflection — forces the model to reason about agreement before
answering, then discarded) followed by 3 candidate clues (`C1=`/`C2=`/
`C3=`); one is picked at random after heavy filtering (`_filter_
candidates`/`_pick_clue`): length, non-Latin script, containing the
target word/its canonical form(s), a leaked label prefix, wrong-language
stopwords. Up to 3 immediate retries per word; `generate()` fires up to
`CLUE_BATCH_PARALLELISM` (10, env-overridable) of these single-word
requests concurrently via a thread pool — a server with continuous
batching decodes several at once, one without simply serializes them
with no harm. A word whose every candidate only failed by containing the
target itself is masked (`_mask_target_word`, "Je dis la vérité quand je
parle" → "\_ dis la vérité quand \_ parle") rather than left undefined.

Grounding: `_build_gloss_block`/`_build_pos_block`/`_build_examples_
block` append real dictionary definitions (`backend/gloss_lookup.py`),
the exact form's grammatical analysis (`backend/inflection_lookup.py`,
falling back to a Hunspell-stem + gloss-dictionary POS guess when the
form isn't in the inflection table), and real corpus usage sentences
(`backend/example_sentences.py`) — all keyed by the word's own canonical/
lemma form(s) or exact inflected spelling as appropriate, omitted when
nothing is found. Prompt structure/rules live in English in this file;
concrete per-language worked examples live in `data/<lang>_prompt_
config.json`.

Also on this class: `generate_title`/`generate_titles` (LLM grid-title
proposals, filtered against reusing a grid word), `describe_theme`
(expands a typed theme into a short keyword list for the Qdrant
pre-search — see "Themed generation" above), `generate_random_theme`
(invents an original theme phrase from scratch, given only a language and
an optional random "indicative word" folded into the prompt purely to
perturb the model into a different answer each call — backs `GET /api/
theme/random`, used by `Automation/Populate.py`), `generate_definitions`
(free-text dictionary lookups for the "Définir" button), `generate_
paraphrases` (the "Paraphraseur" panel). Every clue-generation call
writes a Markdown trace to `LOG_LLM/<timestamp>_<ANSWER>_<SUCCES|
ERROR>.md` (full prompt, raw output, every candidate's verdict).

### `chatbot.py` — `ChatBot` ("David FALCON")

A separate class from `LLMClueGenerator` talking to the same LLM
endpoint family. Builds its system prompt from the full text of
`DOC_USER/EN/ReadMe.md` plus the live UI context sent by the browser
(loaded grid's words/clues, hovered/selected cell, active fill
direction) so it can answer questions about how to use the interface or
give hints without revealing answers outright. `reply_stream()` yields
the reply incrementally (server-sent-events style) and strips `<think>...
</think>` reasoning blocks according to `CHATBOT_THINK_FILTER`.

### `grid_store.py` — persistence

Three independent filesystem stores, one JSON file shape shared with the
`generate_grid()` result dict plus metadata:

- **`GRID_STORE/<language|bilingual>/`** — one file per published grid,
  named `<timestamp>_<title-slug>_<4-digit-code>.json`, never rewritten.
  `save_grid_json`/`get_grid`/`list_grids` (paginated, filterable by
  language/difficulty/seen-state/pseudo).
- **`GRID_WORK/`** — one continuously-overwritten file per in-progress
  "Interactif" authoring session, named `<timestamp>_<pseudo-slug>_<job_
  id>.json`. `save_grid_work`/`get_grid_work`/`list_grid_work`/`delete_
  grid_work`.
- **`GRID_GAME/<grid_id>/<pseudo-slug>.json`** — one file per (grid,
  player) pair holding that player's own typed letters + elapsed timer.
  `save_grid_game`/`get_grid_game`.

### `svg_export.py`

Renders a `generate_grid()`-shaped result to a self-contained SVG:
`render_grid_svg` (empty grid + clue lists + solved grid, for the
per-generation archive under `GRID_SVG/`) or `render_puzzle_svg` (a
printable, answer-free sheet for the library's PDF download). `save_grid_
png` and `svg_to_pdf_bytes` shell out to the external `rsvg-convert`
binary (a real runtime dependency, installed by `Install.sh`).

### `system_info.py`

Best-effort hardware/model reporting for the info badge: GPU enumeration
(`nvidia-smi` or macOS `system_profiler`/`sysctl`), RAM/CPU count, and
`sample_resource_usage()` (periodic CPU/GPU occupancy, respecting
`LLAMA_FORCE_CPU`).

### `embedder.py` / `qdrant_store.py`

`Embedder` (embedder.py) is a thin `httpx` client to an OpenAI-compatible
`/v1/embeddings` endpoint (default: a local llama.cpp server serving
BAAI/bge-m3, CPU by default — Qwen/Qwen3-Embedding-0.6B is a supported,
GPU-friendly alternative, see `env_default.sh`), with batch embedding and
a CLI benchmark. Switching the embed model always requires a full Qdrant
rebuild (`python -m data_builder.qdrant_populate --all --recreate`).

`QdrantStore` (qdrant_store.py) is a plain-HTTP client (no SDK) around
one Qdrant collection (`words`), multitenant by language. `upsert_words`
embeds each word as its own accented spelling + bare uppercase form +
canonical form(s), space-joined (no dictionary text). `search`/`search_
text` do a filtered nearest-neighbor lookup. `data_builder/qdrant_
populate.py` fills the collection from the wordlists; deterministic
point ids make it resumable/idempotent.

### Small lookup helpers

`gloss_lookup.py` (Wiktionary gloss by canonical form, plus `has_gloss_
dictionary`/`has_any_gloss` used by the difficulty filters), `inflection_
lookup.py` (exact-form grammatical analysis), `example_sentences.py`
(reservoir-sampled real usage sentences per exact inflected form),
`dictionary_lookup.py` (accent/case-insensitive same-root-family search
for the "Dictionnaire" panel — distinct from `gloss_lookup.py`'s single-
lemma lookup), `secret_store.py` (`verify_or_claim`: PBKDF2-hashed
nickname secret words under `SECRET/`, anti-pseudo-theft only, not a
real auth system).

## Frontend (`frontend/`)

### `server.py`

A stateless proxy + static-file host: serves `frontend/static/` as the
SPA and relays every `/api/*` path to `backend/app.py` (`BACKEND_URL`,
one shared `httpx.AsyncClient(timeout=PROXY_TIMEOUT_S=30.0)`). Every new
backend endpoint needs its own matching route here — there is no generic
passthrough, an unmatched path falls through to the static-file mount
and 405s. Runs with multiple uvicorn workers (safe: no cross-request
state, unlike the backend).

### `static/`

- **`index.html`** — the whole page's markup: header/info badge,
  generation form, the playable board (grid + across/down clue
  sidebars), a collapsible virtual keyboard, and a set of togglable
  panels (library, dictionary, paraphraser, Qdrant admin, chatbot,
  interactive-mode controls), plus the first-visit welcome overlay.
- **`script.js`** — all client logic in one file. Major areas: grid
  rendering/keyboard input/solution-checking; the interactive-authoring
  mode (by far the largest block — zone selection, undo stack, per-cell
  editing, calls to every `/api/interactive/*` endpoint, candidate/
  crossing-word panels, the "Créations" drafts panel); the library panel
  (paginated/filterable listing, load-into-player, reopen-as-editable);
  the dictionary panel (root-family search, "Définir", "Thématique"/
  "Synonymes", bilingual-aware); the chatbot UI (Markdown rendering,
  incremental streaming, live UI-context snapshot sent as grounding);
  the RSS/SCRAPP panel; the presence counter and CPU/GPU/queue-length
  meters; the virtual keyboard; the welcome overlay and cookie-based
  preference storage (`cwf-prefs`).
- **`style.css`** — one flat stylesheet organized by component in source
  order, IDs/classes mirroring `script.js`'s DOM references 1:1.
- **`i18n.js`** — pure-data translation table, `I18N = {fr, en, de, es,
  it, pt}`, every UI string keyed identically across all six languages,
  consumed by `script.js`'s `applyTranslations`/`describeStep`/`describe
  ErrorCode`. `SUPPORTED_UI_LANGS` in `script.js` lists the same six
  codes.
- **`logo.png`/`logo.svg`** — app logo, also embedded as a base64 image
  into every exported SVG/PNG/PDF as a watermark.

## Automation & scraping

- **`Automation/Populate.py`** — a CLI daemon that bulk-fills the grid
  library by driving the real `POST /api/generate` + `GET /api/generate/
  phase/{job_id}` endpoints one job at a time (randomized language/
  difficulty/size/mode by default, flags to override). Before each grid,
  it also calls `GET /api/theme/random` (unless `--no-theme`) to get an
  LLM-invented theme phrase for that grid's language and sends it as
  `GenerateRequest.theme`, so the populated grid gets a real thematic
  glossary instead of none; a failed theme call just falls back to no
  theme for that one grid. `run_Populate.sh` is its start/stop/restart
  process manager (tracked via `logs/populate.pid`, since it's a
  background script, not a port listener).
- **`scrapper/fetch_rss_feeds.py`** — downloads a small, hand-verified
  set of crossword-specific RSS feeds daily into `RSS/combined.json`.
- **`scrapper/fetch_grid_links.py`** — reproduces a fixed, hand-verified
  aggregation of crossword-publisher "today's grid" pages into `SCRAPP/
  combined.json` daily.

## Environment, ports, launch scripts

Ports: frontend/middleware 3000, backend 3001, local LLM server 3002,
local embedding server 3003, a second interactive-dedicated LLM instance
(dual-GPU setups only) 3004 — all declared once at the top of `env.sh`/
`env_default.sh` and derived into every URL built from them.
`CROSSWORDFALCON_PARALLEL_ATTEMPTS` overrides `crossword_gen.py`'s
per-palier worker count (default: CPU count). `CROSSWORDFALCON_FRONTEND_
WORKERS` (default 10) is the frontend's own uvicorn `--workers` count —
the backend must never be given `--workers`, since its `JOBS`/queues/
schedulers live in one process's memory. See the `project-best-practices`
SKILL's "Ports and environment variables" section for the full variable
list and the dual-GPU LLM routing scheme.

- **`Install.sh`** — installs `rsvg-convert` (runtime dependency), sets
  up the Python venv, and interactively configures which local LLM
  engine/model to run based on detected hardware.
- **`run_Falcon.sh`** — launches the backend (single process) and
  frontend (multiple workers), stopping any prior listener on those
  ports first, including orphaned CSP-worker child processes.
- **`run_llm.sh`** — launches the default local clue-generation LLM
  server (llama.cpp's `llama_cpp.server`), or dispatches to `run_sglang.
  sh` when `LLM_ENGINE=sglang`. Supports dual-GPU (two independent
  instances pinned to separate cards).
- **`run_sglang.sh`** — alternative LLM launcher (SGLang, its own
  `.venv-sglang`), supporting both Apple-Silicon MLX and CUDA GGUF
  backends. Only ever invoked via `run_llm.sh`'s dispatch.
- **`run_embed.sh`** — launches the local embedding server (CPU by
  default).
- **`run_qdrant.sh`** — starts/stops/reports the local Qdrant Docker
  container, bind-mounted to `data/qdrant/`.
- **`Install_qdrant.sh`** — one-time Docker/Qdrant-image setup.
- **`run_Populate.sh`** — see "Automation & scraping" above.
- **`install_cuda13.sh`**, **`renew-https.sh`** — machine-specific
  one-off helpers (CUDA Toolkit install, Let's Encrypt renewal),
  gitignored.
- **`clean.sh`** — clears generated/debug artifacts (`GRID_SVG/`,
  `GRID_PNG/`, `LOG_LLM/`, `LOG_THEME/`).

## Commands

```bash
# Full pipeline to rebuild one language's dictionary from scratch (data/
# already ships every artifact — only needed to refresh/extend it)
data_builder/build_fr.sh   # or build_en.sh / build_de.sh / build_es.sh / build_it.sh / build_pt.sh

# Generate a crossword grid from the CLI (defaults: 15x10, easy)
python3 backend/crossword_gen.py
python3 backend/crossword_gen.py --width 15 --height 15 --difficulty hard --seed 42
python3 backend/crossword_gen.py --wordlist data/wordlist_en_full.tsv

# Web UI: run both servers, then open http://127.0.0.1:3000
./run_Falcon.sh

# Local LLM for clue generation (needed for the web UI, not the bare CLI)
pip install -r requirements-llama.txt
./run_llm.sh

# Optional: local embedding server + Qdrant, for "Thématique"/"Synonymes"
./run_embed.sh
./run_qdrant.sh
python -m data_builder.qdrant_populate --all
```

`backend/app.py` needs a reachable LLM at `LLM_BASE_URL` for clue/title/
definition/paraphrase/chat features; copy `env_default.sh` to `env.sh`
and edit it (`run_Falcon.sh` sources it automatically). There is no test
suite, linter, or build step in this repo.
