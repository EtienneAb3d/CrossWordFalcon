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

The API server exists in two interchangeable implementations: Python
(`backend/`, started by `run_Falcon.sh`) and Java (`backend_java/`,
started by `run_FalconJ.sh`) — same port, routes, request validation, JSON
responses, on-disk stores and LLM prompts, so the middleware and the web UI
work identically with either. The two evolve together (`project-best-
practices` SKILL, permanent rule 23); everything below that names a
`backend/*.py` function holds for its Java mirror too (see "Java back end").

Engineering language is English (code, comments, this file, the SKILLs,
`README.md`); product content — crossword words/clues and every UI string
— is written in whichever of the six supported languages applies.

## Repository map

| Path | Contents |
|---|---|
| `backend/` | All Python business logic: the API server, the generation engine, LLM/chat/embedding clients, persistence, export. No subpackages — every `.py` file sits directly under `backend/`. |
| `backend_java/` | The Java back end (Java 21, Maven, `pom.xml`): `src/main/java/falcon/` mirrors `backend/` (see "Java back end"), `build.sh` builds `dist/crosswordfalcon-backend.jar`, committed with `dist/sources.sha256` (the fingerprint of the sources it was built from) so a checkout runs it without rebuilding; `target/` (Maven's own output) is gitignored. |
| `frontend/` | `server.py` (proxy + static host) and `static/` (the whole single-page app: `index.html`, `script.js`, `style.css`, `i18n.js`, logo assets). |
| `data_builder/` | One-off/periodic scripts that build each language's dictionary artifacts (corpus → wordlist → gloss dictionary → inflection table → Qdrant embeddings), plus one orchestration shell script per language. |
| `scrapper/` | Daily-refreshed content scrapers feeding the web UI's "Actu Croisée" panel (RSS feeds, aggregated crossword-publisher links). |
| `Automation/` | `Populate.py`, a CLI daemon that drives the real `/api/generate` endpoint to bulk-fill the grid library. |
| `data/` | Per-language dictionaries (`wordlist_<lang>_full.tsv`, `gloss_dictionary/`, `inflection/`), the reference sentence corpus, and Qdrant's on-disk storage. |
| `DOC_ALGO/FR/`, `DOC_DIC/FR/`, `DOC_USER/EN/` | Hand-maintained, present-tense-only reference docs (see intro). |
| `GRID_STORE/`, `GRID_WORK/`, `GRID_GAME/` | Persisted grids: published library grids, in-progress "Interactif" authoring drafts, per-player play state. All gitignored. |
| `STOP_DUMP/` | One diagnostic snapshot per automatic-generation job interrupted via the "Stop" button: last known cell/slot state of every attempt still running at that moment. Gitignored. |
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
   columns: `MOT` (bare, accent-stripped, ligature-folded, uppercase — the
   grid form: a French ligature letter with no accent-style decomposition
   of its own, `œ`/`Œ`/`æ`/`Æ`, is folded into its two separate ASCII
   letters — "sœur" -> `SOEUR`, not `SŒUR` — so MOT always stays a plain
   run of A-Z letters, individually typable on a simple keyboard/grid
   cell; `ACCENTUE` keeps the ligature),
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
  only log of attempt-preview snapshots the web UI replays), `live_preview`
  (a single, continuously OVERWRITTEN attempt-preview snapshot — every new
  best state the CSP search reaches on any still-running attempt, never
  appended, `None` for a job type that never produces one; each entry
  additionally carries the snapshot it replaced under its own `previous`
  field, a STOP_DUMP-only diagnostic `GET /api/generate/status/{job_id}`
  drops from its own response), `clues_
  progress`, `success_count` (genuine successful attempts so far, every
  palier included, updated live by `generate_grid`'s `"success_count"`
  progress event — one per attempt finishing with a grid, emitted from the
  palier's harvest loop — without replacing `step`, like
  `"budget_progress"`; shown by the web UI's gold-medal badge
  `#success-medal`), `resume_state` (set only on total failure, feeds the
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
  paraphrase` (LLM paraphrases), `GET /api/correct` (LLM proofreading of
  one typed definition or title, Interactive mode's "Corriger"), `GET /api/similar_words` (LLM-expanded +
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
  `/boundary` (dictionary words that can start or end a slot, even
  shorter than its full length), `/stats` (read-only statistical letter
  preview for every still-empty cell), `/impossible` (read-only diagnostic
  recompute), `/verify` (dictionary-
  membership check), `/title` (LLM proposals), `/save` (publish),
  `/save_work` (autosave draft), `GET /work` + `/work/delete` (drafts
  list/delete), `/resume` (relaunch a draft), `/from-library` (reopen a
  published grid as an editable draft), `/from-attempt` (reopen one
  automatic-generation attempt-preview snapshot as an editable draft),
  `/finish` (hand the current grid to automatic generation, locking
  already-placed letters — see below).
- *Chat*: `POST /api/chat` (streamed "David FALCON" reply).
- *Qdrant admin* (localhost-only, gated by `frontend/server.py`): `GET
  /api/qdrant/admin`, `POST /api/qdrant/admin/recreate`, `POST /api/
  qdrant/admin/delete-tenant`.

**Key request models** (Pydantic, all in `app.py`): `GenerateRequest`
(`language`, `bilingual_language`, `width`/`height` [5-30], `difficulty`
[easy/medium/hard], `seed`, `force_letters_percent` [0-100, default 1],
`black_enrichment_percent` [0-100, default 17], `mode` [flash/turbo/fast/
medium/ultra, default medium], `pseudo`, `theme`, `theme_precision`
[0.0-1.0, default `THEME_MIN_SCORE`], `source`, `challenge_words`
[list of free-form "Mots Défi" strings, default empty]); `RecomputeRequest`;
the `Interactive*Request` family (`Step`, `Clean`, `Candidates`,
`Crossing`, `Impossible`, `Verify`, `Title`, `Save`, `SaveWork`, `Finish`
— `Finish` additionally carries an optional `zone_cells` to scope
automatic completion to a selected region instead of the whole grid,
`WorkId`, `FromLibrary`, `FromAttempt`); `PresenceRequest`, `PseudoClaimRequest`,
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
"hard" up to 5 (never restricted otherwise). `MAX_NON_GLOSS_WORDS` mirrors
this same per-difficulty cap for words absent from the gloss dictionary.
Both are enforced by `try_fill` as a final safety net, after an otherwise
fully successful fill (every slot holds a real word, no empty domain, no
crossing deadlock): exceeding either cap flips `truly_complete` back to
`False` (`reason` becomes `"too_many_proper_nouns"`/`"too_many_non_gloss_
words"`) exactly like any other failure, and every offending word actually
present in the grid (not just the excess beyond the quota) is folded into
`impossible_slots`/`impossible_cells` (`_quota_overflow_slot_indices`) —
so this kind of rejection gets the same red highlighting in the preview,
and the same cross-palier repair (`_clean_blocked_slots`/`_build_retry_
seed` strips the flagged word(s) and retries with a different candidate)
as any other impossible slot, rather than showing a seemingly "clean,"
fully-lettered grid marked failed for no visible reason.

**Grid generation is a three-phase pipeline**, run inside `generate_grid`
across up to `attempts` (default 200) *paliers*, each running
`PARALLEL_ATTEMPTS` (default: `CROSSWORDFALCON_PARALLEL_ATTEMPTS` env
override, else `os.cpu_count()`) independent worker processes in parallel
via `ProcessPoolExecutor`:

1. **Black-cell placement** (`make_pattern`) — places black cells one at
   a time (never symmetric pairs), via `_place_black_cells`: before
   every draw the pool is restricted to the candidates lying both in a
   least-loaded column and in a least-loaded row (`_least_loaded_pool`:
   minimum over the rows/columns still owning a candidate, both bounds
   raised together one black cell at a time while that intersection is
   empty), then a 32-cell look-ahead window of that pool prefers the
   cell farthest from every black cell already placed (greatest
   Euclidean distance to the closest one, `_nearest_black_distance_sq`,
   ties keeping the shuffled order), restricted to
   non-adjacent candidates satisfying
   `STRUCTURAL_MIN_INTERIOR_FREE=4` (an interior white zone must be at
   least this long), relaxed down to 1 — but adjacency itself is never
   accepted at all, on any palier (`forbid_adjacency=True`, always, not
   only on a call's very first, blank palier): a palier whose black-fill
   percentage target can't be reached without an adjacent cell simply
   ends up short of that target, left as-is, rather than forcing one. A
   pre-fill phase (`_prefill_unfillable_slots`) runs first (and again
   after ratio-based placement, whenever letters are already locked) to
   blacken any slot whose length has too few dictionary candidates
   (`PREFILL_MIN_WORD_COUNT=3`) or whose already-locked letters leave too
   few exact matches (`PREFILL_LOCKED_MIN_WORD_COUNT=3`) — same
   never-adjacent rule, falling through to removing a crossing locked word
   or marking the slot `unfixable` instead; pre-fill cells always count
   toward the density target. This adjacency prohibition is scoped to
   pattern generation itself: the cross-palier retry machinery below and
   the impossible-zone-resolution passes remain free to place or relocate
   a black cell adjacent to an existing one when repairing an
   already-impossible zone requires it. The
   overall black-cell rate (web UI "Taux noir", `black_enrichment_
   fraction`, default `POST_PREFILL_BLACK_FRACTION=0.10`) is a share of
   the WHOLE grid: `target = round(fraction * rows * cols)`, compared
   against the black cells already there (the carried-forward seed's
   own plus pre-fill's), so only the shortfall is added; the same
   unscaled rate is pre-fill's curative-cleanup zone budget.
2. **CSP fill** (`Filler`/`_backtrack`, via `try_fill`) — a standard
   recursive backtracking solver. Slot selection (`_select_target_slot`,
   also reused verbatim by `interactive_place_word`) is a fixed 9-level
   cascade: (1) optional, currently disabled
   (`ALTERNATE_DIRECTION_ENABLED=False`) — when enabled, draw across-vs-
   down weighted by remaining open-slot count; while disabled, this level
   is a no-op and the cascade starts from the whole unassigned pool, both
   directions together; (2) prefer ones where an unused "Mots Défi" word still fits, if
   any (`Filler.challenge_words`) — checked purely geometrically (length +
   already-known letters), never requiring dictionary membership, and
   placed here, ahead of every level below, so it can never be starved by
   an unrelated fragile slot winning level 3 first; (3) among slots ≥4
   letters, prefer ones with fewer than
   `PREFILL_MIN_WORD_COUNT` real candidates, if any; (4) optional,
   currently disabled (`KNOWN_LETTER_LEVEL_ENABLED=False`, a no-op while
   disabled) — when enabled, among those, prefer ones with at least one
   already-known letter, if any; (5) among
   those, prefer ones where an unused theme word still fits, if any —
   since level 2 already ran first, this group may already be challenge-
   narrowed, which is what still gives "Mots Défi" priority over the
   theme glossary specifically; (6) score the remaining group
   geometrically (squared distance between the slot's own CLOSEST cell to
   `SLOT_SELECTION_ORIGIN=(0, 0)`, the grid's top-left cell, and that
   origin itself — not the slot's own midpoint), shuffle, and keep the
   `SLOT_SELECTION_WINDOW_SIZE=10` lowest-scored as a window; (7) within
   that window, among slots of at least
   `MOST_CONSTRAINED_START_LENGTH=12` letters — a threshold lowered one
   letter at a time, down to `MOST_CONSTRAINED_MIN_LENGTH=2`, until some
   slot of the window has a measurable free cell (no-op when none has one
   even at 2) — find
   the smallest number of letters
   still possible on any one still-free cell (`_slot_min_letter_options`,
   reading the same per-direction tally Interactive mode's "Stats"
   button displays, `Filler.letter_scores_by_dir`: the across and the
   down slot through a cell each keep their own letter tally there, and
   the count is that of the letters BOTH directions observed,
   `_crossed_letter_option_count`/`_crossed_letter_counts`, each kept at
   the lower of its two counts — 0 when the two share none, the tightest
   possible, which only makes that slot selected first: being a sample,
   a 0 never marks the cell a "case croisée bloquée" — only the real-
   domain check, `Filler.slot_is_blocked`, decides that; a cell in a single slot keeps that slot's tally alone; a cell
   already determined by a real letter is skipped, so a partially-filled
   slot never reports 1) and keep only the
   slots owning a cell with that count, resolving the grid's tightest
   cell while the search still has room. That tally is kept current
   rather than frozen on the pre-search sampling: every time a word is
   written, `_refresh_letter_scores_around` re-samples each still-open
   slot it CROSSES (skipping one whose domain is empty — nothing left to
   measure) against that slot's own current `_domain`, replacing its
   cells' summed tally (`letter_scores`, what `_candidate_score`/`_slot_
   letter_frequency_score` read) rather than adding to it, since the other
   contributor to those cells is the word just placed, whose letters are
   now fixed, and replacing only that slot's own direction entry in
   `letter_scores_by_dir`, the other direction's left as it stands;
   `_restore_letter_scores` undoes it as the placement is reverted, so a
   tally never outlives its own assignment. Bounded by construction: at
   most one crossing slot per cell of the placed word, each costing one
   `_domain` call `_backtrack` already pays for every unassigned slot on
   every node; (8)
   re-sort that window by letters-already-placed (most first), keep the
   top `SLOT_SELECTION_REFINE_FRACTION=1/2`; (9) re-sort by statistical
   fill-option richness (`_slot_letter_frequency_score`: square root of
   the sum of the squared top-letter frequencies of the slot's still-free
   cells), highest wins.
   Candidate word order
   within the chosen slot is `Filler.ordered_candidates` — this engine's
   single candidate-ordering rule, shared verbatim with Interactive
   mode's own "Suivant" (see "Priority-tier search in Interactive mode"
   below): shuffled, then ranked by `_candidate_score`
   (square root of the sum of squared per-cell statistical letter scores
   from `sample_letter_biases`, which draws `LETTER_BIAS_SAMPLE_SIZE=10`
   words per slot) with a random draw inside a `CANDIDATE_SCORE_
   WINDOW=50`-word sliding window — deliberately far narrower than a
   slot's own domain, so the statistical ranking stays in charge while two
   attempts on the same state still diverge; a themed grid's matching words are
   pulled to the front as a stable block first, then any still-unused
   "Mots Défi" word geometrically fitting the chosen slot is injected
   ahead of that block (even when absent from the loaded lexicon
   entirely) — the same precedence `interactive_place_word`'s own
   candidate draw already gives it. Each family's black-cell reshape
   options for the chosen slot follow that family's own words (see
   "Floating black cells and reshapes" below). A slot whose real dictionary domain
   is empty is not treated as a dead end (`Filler.
   mark_immediately_impossible_slots`/`_backtrack`'s own per-node
   domain check) as long as an unused, not-yet-abandoned challenge word
   still fits it (`Filler._active_challenge_words()` — see "Crossing-
   safety retry, all three candidate tiers" below for what "abandoned"
   means here). Placing a "Mots Défi" candidate is immediately reverted,
   with no further recursion, the moment it leaves a CROSSING slot with
   no viable candidate left — dictionary-dry AND no active challenge word
   able to fill it in turn — so the loop simply moves on to the next
   candidate (another challenge word fitting the same slot, or the theme
   glossary/ordinary dictionary domain); see "Crossing-safety retry, all
   three candidate tiers" below for the per-word/per-slot give-up budget
   layered on top of this, for every candidate tier. The search checks a
   `deadline_checks` budget, a cooperative `cancel_event` (every
   `CANCEL_CHECK_INTERVAL=500` calls), and, when `UNFILLABLE_ABANDON_
   ENABLED` is true (optional, currently disabled), self-abandons
   (`UNFILLABLE_ABANDON_SLOT_COUNT=3`) once more than that many
   still-unassigned slots belong to an already-impossible zone. Before
   handing `unassigned` to the 9-level cascade, `_backtrack` also
   deprioritizes every slot already in `Filler._impossible_this_attempt` —
   slots observed as impossible earlier in this same attempt, excluded
   from `unassigned` unless doing so would leave nothing selectable (in
   which case the cascade runs over the full, unfiltered list instead, as
   a last resort). Fed for free, whenever the ordinary per-node domain
   check (right above the cascade) finds an unassigned slot dry — never by
   a dedicated, proactive `_crossing_deadlock_slots` scan run on every
   node (the other way a slot can be impossible: non-empty domain in
   isolation, yet jointly unfillable with a slot it crosses): that scan's
   own cost scales with every open slot's full domain size, which can
   reach tens of thousands of candidate words on an early, still-mostly-
   empty grid, stalling visible search progress for a long time on
   exactly the grids with the most empty space left. A deadlock IS still
   fed in, but only at the coarser cadence `Filler.excluded_zone_cells`'s
   own `include_deadlock=True` callers already pay this same cost at
   (`_publish_new_best`/the final diagnostics snapshot, see below) — one
   arising and resolving entirely between two such snapshots is simply
   never caught this way. The set keeps only the `MAX_EXCLUDED_SLOTS` (3)
   most recently flagged slots (`_RecentSlots`: re-flagging a slot makes it
   the most recent again, a fourth one evicts the oldest), and a flagged
   slot leaves it as soon as a word placed across it leaves it not blocked
   — `_backtrack`'s per-candidate crossing check already calls
   `slot_is_blocked` on every open crossing slot of an accepted candidate,
   so the re-evaluation costs nothing (`unblocked_crossers`). A removal is
   not undone when that word is later backtracked; the slot is flagged
   again if it goes dry again. Scoped to `_backtrack`
   alone — `interactive_place_word`'s own call into `_select_target_slot`
   builds its own `viable` list directly and never consults this set,
   since it runs no recursive search of its own to observe a slot going
   dry over.
   Once the search ends (whatever the outcome), `_close_implied_slots`
   runs one cheap final pass over `filler.best_assignment`, confirming
   every still-unassigned slot down to a single real, unused candidate
   given the letters real crossing words already determine — a slot the
   search simply never got to select, which would otherwise leave a
   visually complete grid formally incomplete. It needs only ENOUGH cells
   known to leave one candidate, not all of them, so it can write real
   letters, and is therefore held to the same crossing rule as
   `_backtrack`: a confirmation that would leave a crossing open slot
   with no available word at all is skipped (that slot's single candidate
   was its only option either way, so the grid was not completable
   whichever of the two gave way — leaving both open keeps
   `_clean_blocked_slots` able to repair real empty cells instead of a
   sealed run of garbage).
3. **Minimization** (`minimize_black_squares`) — once a fill succeeds,
   iteratively removes black cells one at a time, keeping a removal only
   if the grid stays fillable (at the loosest structural bound,
   `min_interior_free=1`, since this phase only ever lengthens slots) and
   every resulting word is a genuine dictionary entry.

**Minimum successful attempts, with worker reassignment** — a single
successful grid is never enough to conclude the search: `generate_grid`
only stops once at least `MIN_SUCCESSFUL_ATTEMPTS=2` attempts have
genuinely succeeded, counted cumulatively across the *whole* search (every
palier, not just the one that just ran). While harvesting a palier's
`PARALLEL_ATTEMPTS` parallel futures, the moment any of them finishes
(success or failure) while some ORIGINAL attempt of the palier is still
racing, the worker process it just freed is immediately reassigned to a
brand-new, from-scratch attempt (the same shape as an ordinary "reset"
attempt — never a continuation of the grid that just finished) instead of
sitting idle. A replacement runs with `racing=False` (`_pattern_attempt`):
it never flags `attempt_active`, so it never counts as a sibling still
racing and never extends an original's elastic budget. The harvest loop
polls every 0.5s and sets `attempt_done_event` — interrupting every
replacement still running — as soon as every original has finished
(`interrupt_threshold`) or every original still pending has used up its
budget (`checks_progress` of its slot ≥ `resolved_deadline_checks`); no
replacement is dispatched after that. A palier that ends with fewer than `MIN_SUCCESSFUL_ATTEMPTS`
successes in hand (0 or 1) does not stop the search: it falls through to
the ordinary cross-palier retry machinery below for its own failed
attempts, and whatever single success it did find stays remembered for
comparison against a later palier's own. Once the threshold is reached,
the best candidate is picked among every genuinely successful attempt
found anywhere in the search so far (see "Content scoring" below) and the
search stops. That choice minimizes every success in parallel on the
palier's own pool (`_minimize_trial`, one task per success, the
"minimizing" step published as they start with every success as its
examples) and keeps the winner's minimized grid as the final grid
(`best_minimized`) instead of minimizing it again; only a lone success
accepted at the end of the budget goes through the separate final
`minimize_black_squares` call. A mid-palier replacement attempt takes the
next free lineage number (`next_lineage_number`, used then advanced), so
tiles are numbered 1…`PARALLEL_ATTEMPTS`, then `PARALLEL_ATTEMPTS`+1 for
the replacement. Replacements make a palier return more candidates than it
has workers; only the best `PARALLEL_ATTEMPTS - reset_count` cleaned grids
(N-1 of N, fewer by one per discarded grid after a full cleanup) are
carried into the next palier, alongside its blank-grid worker(s) —
`_seed_pool` caps the pool at the next palier's non-reset slots, both on
the "reprise telle quelle" and the full-cleanup path. If the whole `attempts` budget (200 paliers by default) is
exhausted with only one success ever found, that one success is accepted
rather than the search reporting total failure.

**Cross-palier retry** (the mechanism that lets a failed palier's real
progress survive into the next one, rather than starting every palier
from scratch): after a failed palier, `still_has_hope` is true iff some
unassigned slot exists that is neither itself impossible nor touching an
impossible one — in that case the next palier reuses the exact same
pattern verbatim (`_pattern_continue`, never calling `make_pattern`
again), first stripping any word that directly crosses an impossible
slot (`_clean_blocked_slots`) to free those cells for a fresh attempt.
Before that stripping runs, `_shorten_impossible_zones`/`_lengthen_
impossible_zones` each try placing a shorter/longer word into a still-
impossible slot by adding or relocating one black cell — deliberately
allowed to cross an ALREADY impossible slot elsewhere (only a NEW
degradation rejects a candidate, `_new_crossing_impossibility`), which
means `_clean_blocked_slots`'s own crossing-word removal can go on to
remove that exact word a few lines later, in the very same cleanup pass.
Both functions hand back a `black_cell_links` map pairing each word they
place with its own black-cell change; `_clean_blocked_slots` reverts that
change in the same step it removes the word, so a word and the black
cell added or moved for it are always kept or discarded together.
This can repeat for up to `MAX_CONSECUTIVE_CONTINUE_PALIERS=4`
consecutive paliers before a full cleanup is forced regardless (and
immediately, if every one of a palier's parallel attempts independently
gave up as `abandoned_too_unfillable` — never the case while
`UNFILLABLE_ABANDON_ENABLED` is False). Once no slot can usefully be
continued, a full cleanup (`_build_retry_seed`) runs: remove every word
crossing an impossible slot, keep every surviving letter as the next
palier's locked constraint, and reopen any black cell that neither
bounds a surviving word nor sits sandwiched between two confirmed
letters on the same axis — then generate a brand new pattern
(`_pattern_attempt`) from that state. Each cleaned candidate is tracked
on its own across consecutive full cleanups ("reprise telle quelle"
paliers in between neither count nor reset it): its ordinary cleanup's
state (pattern + confirmed content, `_cycle_start_preview`) is compared
with every state the previous full cleanup produced
(`carry_cleanup_streaks`). At `GRID_REPEAT_DEEP_CLEANUP_STREAK=2`
consecutive identical states it is cleaned deeper instead
(`_build_retry_seed(deep=True, exclude_impossible_locked=True)`:
`_clean_blocked_slots` also removes every word crossing a word the
ordinary removal took out, freeing the letters that forced that word
straight back in, plus any fully-locked slot spelling no real word); at
`GRID_REPEAT_DISCARD_STREAK=3` it is dropped from the pool, and the next
palier gives its place to one more blank-grid worker (`carry_discarded_
count`, added to `FULL_RESET_ATTEMPT_COUNT` in `reset_count`) while every
other candidate is kept. Only when every candidate is dropped does the
whole search restart from a blank grid.

**An "emplacement écarté" (yellow) is a pure deprioritization, and is
reset to nothing at the start of every new palier.** `Filler._impossible_
this_attempt` is the single set that holds them, and is the single
definition of the term throughout the engine (see `DOC_ALGO/FR/Lexicon.
md`): a slot recently found blocked during THIS attempt (at most the
`MAX_EXCLUDED_SLOTS` latest, and only until a crossing word unblocks it) is set aside
so the search only comes back to it once no other slot can take a word,
but it is never walled off and is
never inherited from a previous palier (`generate_grid` always dispatches
`_pattern_continue` with `excluded_slots=None`; a fresh `Filler` starts
with the set empty). Three things feed it: `Filler.mark_immediately_
impossible_slots()`, run once before the search starts for a slot already
dry under the definitive constraints alone (purely a head start — the
per-node check below would find the same slots on its first call anyway);
`_backtrack`'s own per-node domain check, the instant a slot's domain goes
dry; and `_backtrack`'s own candidate loop running out, when every one of
the chosen slot's candidates was rejected for leaving some crossing slot
impossible — free to detect, since that loop has just paid for every one
of them. That third source leaves a slot with a genuinely non-empty
domain, so it keeps being crossed freely by neighbouring words; only its
selection priority changes, unlike a blocked (red) slot, which is never
crossed at all. A crossing-letter deadlock is NOT one of them: `excluded_zone_cells`'s
own `include_deadlock=True` calls report it for that one snapshot only.
A deadlock is a property of the assignment being examined, not a lasting
fact about the slot — the next placement can dissolve it — so memorising
it would let a display call permanently rewrite the search's own
scheduling set, flooding it until every slot is écarté and the
deprioritization means nothing. Finding a slot unfillable in the current
state makes the node backtrack (`return False`), since a placement of this
search caused it: a dry slot at the node's domain check, and a chosen slot
whose candidates were all rejected with at least one of them for blocking
a still-healthy crossing slot (`blameable_rejection`). What does NOT
backtrack is what no backtracking can repair — `Filler._tolerated_dry`:
the slots already dry before the search placed anything (`solve()`,
`_dry_open_slots`) and, in the last-resort pass only, the slots an
accepted `allow_breaking` word dried (added on placement, removed on
revert). A tolerated dry slot is just left out of that node's `domains`;
a slot whose candidates were all rejected only for crossing a tolerated
one lets the node move on to its next slot; and in the last-resort pass
the all-rejected case never backtracks, so the node can still reach its
`allow_breaking` stage. The écarté FLAG survives the backtrack: a slot
flagged in an earlier configuration may be viable again, and stays
selectable in the node's released stage. `_backtrack` runs each node in three
ordered stages: (1) try to place a word on a NON-écarté slot, taking them
in cascade order until one accepts a candidate; (2) if none does, release
the écarté slots and carry on, still without backtracking; (3) only once
nothing can be placed even on those does the node fail and ordinary
backtracking resume. `released` is a plain `_backtrack` parameter, so it
is inherited by everything placed below a release and restores itself as
the backtrack unwinds back above the node that released it.
Every stage of a node (the `allow_breaking` pass included) shares one cap,
`MAX_DESCENTS_PER_NODE` (3; `<= 0` disables it) — raised to
`EARLY_MAX_DESCENTS_PER_NODE` (7) for a node entered while fewer than
`EARLY_DESCENTS_WORD_COUNT` (10) words are in place on top of the
attempt's initial state (`Filler._initial_assigned_count`, the words
already assigned when `solve()` starts), and removed entirely for an
attempt inherited from a previous palier — one starting from locked
cells (`locked_letters` non-empty or words already assigned when
`solve()` starts, `Filler._inherited`), which must be finished as well as
possible and so explores every option of every node: once the node has made
that many recursive descents without success — a descent being a
candidate that passed the crossing check and was recursed into, never one
rejected on the spot, and never a "Mots Défi" or theme-glossary candidate
(`challenged_set`/`pri_set`), whose hypotheses are all explored whatever
the count — it returns `False` at once, whatever stage it has
reached. Without it a node only fails once its whole subtree is
exhausted, which never happens within the budget on a real dictionary, so
backtracking climbs only a few levels and a hard word placed early stays
for the whole attempt; with it, backtracking climbs back to those early
words. On top of it, `_backtrack` does conflict-directed backjumping
(`BACKJUMPING_ENABLED`): every `return False` goes through `Filler._fail`,
which leaves in `_last_conflict` the slots holding the placed words the
failure depends on — a dry slot's assigned crossers plus the slots holding
a word of its (fully used) domain (`_dry_slot_conflict`); for a slot whose
candidates were all rejected blameably, the assigned crossers of that slot
and of the slots each rejection would have blocked (`_assigned_crossers`);
for an exhausted node, the union over everything it tried, minus its own
slot. A parent whose slot is not in the child's conflict set undoes its
word and returns that same set without trying other candidates, so
backtracking jumps straight to the most recent word actually involved
instead of replaying the same failure under every unrelated intermediate
level. `None` (budget, abandon, periodic stop, disabled) falls back to
chronological backtracking; an empty set (only root-dry slots left) jumps
to the root. Before backjumping, a failure is first backghosted
(`Filler._fail_or_backghost`, `MAX_BACKGHOSTS_PER_DESCENT`, currently 0 — disabled): at each
place a failure arises with a conflict set (a dry slot, a slot whose
candidates were all rejected blameably, a node exhausted or at its descent
cap — never a child's failure merely passed up), if the most recent word of
that set placed by this search (`Filler._placement_seq`, slot → placement
sequence number; words already there when `solve()` starts are never
ghosted) is not the word placed right above — so a backjump would unwind
other placed words to reach it — that word alone is taken off the grid in
place (its crossers' letter tallies re-sampled, restored afterwards), and a
fresh `_backtrack` node carries on from there, with every word in between
still placed. The node that placed a ghosted word finds its entry gone once
the search really unwinds to it and has nothing left to remove (`owned`).
At most `MAX_BACKGHOSTS_PER_DESCENT` backghosts can be pending on the
current descent (`_ghosts_in_descent`, nested retries); past that the
failure backjumps as above. A failed retry reports the union of both
conflict sets minus the ghosted slot. A flagged
slot therefore stays fully reusable for the rest of the attempt and is
picked back up automatically, with no special bookkeeping, the moment
some other slot's own assignment changes and its domain becomes non-empty
again (`_domain` is recomputed fresh on every node); it stops showing
yellow as soon as a word is actually placed on it (`excluded_zone_cells`
filters to still-unassigned slots).

`Filler.excluded_slots` is a separate, unrelated mechanism and is NOT an
"emplacement écarté": it drops a slot out of the grid the search has to
solve at all — never selected, never required by `truly_complete`, never
counted as a broken crossing, and never surfaced by any diagnostic or
overlay. `_optimize_before_cleanup` is its only caller, on both of its
own fills: the ordinary one (completing what it can while deliberately
leaving an entirely-empty or already-impossible zone untouched) and the
last-chance one right below, where naming the impossible slots here is
exactly what lets a word cross them — `_backtrack` skips an excluded slot
in its per-candidate crossing check, so neither `crossing_broken` nor
`crossing_still_impossible` can reject a candidate on its account. Every
generation palier leaves it `None`.

**Last-chance enrichment before cleanup**: the final thing
`_optimize_before_cleanup` does, once its ordinary fill and its
black-cell removal loop are finished and the failed palier's grid is
about to be handed to the cleanup, is one more `try_fill` whose whole
purpose is to add words across an emplacement already known impossible —
the one moment that is allowed (`DOC_ALGO/FR/Lexicon.md`, "emplacement
bloqué"), since the more words the grid carries when `_clean_blocked_
slots`/`_build_retry_seed` run, the more of them survive into the next
palier. It differs from the ordinary fill on exactly three points, each
load-bearing: the entirely-empty slots are NOT excluded (so the search
genuinely tries to fill them instead of leaving them locked), the
impossible ones still ARE (which is what makes them crossable, per
`excluded_slots` above), and its result is absorbed from
`diagnostics["assignment"]` even when the fill never completes —
`try_fill` only returns a grid once every required slot is solved, which
is precisely what this grid cannot do, while its best partial state is
the whole point. It only ever adds letters: `preseed_assignment` locks
every fully-confirmed slot, no black cell is touched, and nothing already
placed can be lost or contradicted. A word it places may seal an
impossible emplacement into a fully-lettered run spelling nothing real;
the `_invalid_fully_known_indices`/`_impossible_indices` recomputation
that follows in the same function catches that like any other case, so
such a slot reaches the cleanup flagged impossible rather than passing
for valid.

**"Impossible" also covers a crossing-letter deadlock**, on top of the
plain empty-domain case above: two still-open slots crossing at a cell
whose remaining achievable letters (real dictionary candidates already
placed nowhere else in the grid, plus any still-active "Mots Défi" word)
share no letter at all are both flagged impossible too, even though each
one's own domain is non-empty in isolation — `Filler._crossing_deadlock_
slots` (live-search context, folded into `Filler.impossible_zone_slots`,
already excluding `used_words`) and its module-level counterpart
`_crossing_deadlock_indices` (folded into `_impossible_indices`, so every
one of that function's own callers — `_optimize_before_cleanup`,
`_shorten_impossible_zones`/`_lengthen_impossible_zones`'s own internal
re-checks, `interactive_clean_impossible_zones`/`interactive_minimize_
black_cells` — inherits it automatically; also excludes a word already
fully spelled out elsewhere in the grid, matching the Filler-based
version — a word already placed once can't paper over a real deadlock by
still counting as "achievable" for a different slot). `Filler.impossible_
zone_cells()` (the preview-facing set of cells) deliberately never treats
an "emplacement écarté" as impossible on that basis alone: that set only
records that a slot went dry at least once during this attempt, and
backtracking routinely makes such a slot viable again, so painting it red
would conflate "deprioritized" with "unfixable".
`Filler.excluded_zone_cells(assignment=None)` gives such
a cell its own signal instead — rendered as a yellow background
(`.attempt-preview-grid .cell.white.excluded`, `frontend/static/style.
css`) — distinct from `.impossible`/`.deadlock`'s red, and never
subtracted from those two: a cell can be both excluded and (freshly,
genuinely) impossible at once, and the CSS cascade (`.low-candidates`,
then `.excluded`, then `.noise`/`.impossible`/`.deadlock`) lets the
stronger, more severe signal win the background whenever several apply —
yellow outranks the orange "emplacement pauvre" (having no placeable word
at all says more than having few candidates), and is in turn outranked by
violet and red. The same order applies to Interactive mode's own
`.cell.white.interactive-low`/`.interactive-excluded`/`.interactive-
impossible`/`.interactive-deadlock`.
It renders exactly
`_impossible_this_attempt`, filtered to still-unassigned slots in
`assignment` (`self.assignment` by default) so a slot later filled via a
different path never shows this overlay on top of its own real letters —
so the overlay and the search's own deprioritization can never disagree.
On top of the two sources that feed that set directly (see above), a
crossing-letter deadlock (`_crossing_deadlock_slots`) currently found in
`assignment` is merged in when `excluded_zone_cells`'s own
`include_deadlock` argument is `True`: the plain empty-domain case
already gets this "temporarily set aside" treatment live, the instant
`_backtrack` notices it, but a deadlock can only ever be noticed OUTSIDE
the search's own hot per-node loop (see `_crossing_deadlock_slots`'s own
cost, scaling with every open slot's domain size), so without this third
source such a slot showed red (`impossible_zone_cells`/`deadlock_zone_cells`, both already
fold it in) but never also yellow. `include_deadlock` defaults to `False`
so every pre-existing caller (in particular `_publish_live_state`'s own
tight heartbeat cadence, see right below) stays exactly as cheap as
before — only `_publish_new_best` and the final diagnostics snapshot pass
`True`, since both already pay for `deadlock_zone_cells()`'s own
equivalent cost on the very same call. A slot found this way is reported
for THAT snapshot only and is never merged into `Filler._impossible_this_
attempt`: a crossing deadlock is a property of the assignment being
examined, not a lasting fact about the slot, and nothing on a display
path may write to that set (`DOC_ALGO/FR/Lexicon.md`). Memorising it let
a display call permanently rewrite the search's own scheduling set —
replaying a STOP_DUMP with and without the preview queue, all else
identical, gave 44 slots set aside vs 0 and 7991 of 8967 slot selections
with nothing but écarté slots to choose from vs 0, i.e. the grid froze
into a block of yellow and the deprioritization became vacuous. The
consequence to keep in mind is that a deadlock's yellow only shows on the
snapshots that pass `include_deadlock=True`, never on the heartbeat's own
cheaper ones.

Scoped to one attempt's own lifecycle, not merely to a display channel:
valid only "pendant une étape," reset "en fin d'étape, avant la phase
d'optimisation" — at the user's own explicit framing, with one exception
named directly: "ils peuvent éventuellement être visibles sur l'étape
clef avant optimisation, mais plus ensuite." Concretely, real (non-empty
when applicable) on exactly three things: `try_fill`'s two live
callbacks (`_publish_new_best(best_assignment)`/`_publish_live_state
(current_assignment)`, the latter cheaply, since this method costs only
plain set lookups, no domain iteration); the harvesting loop's own "just
finished" live tile; and `last_examples` — the `pattern_attempt_failed`/
`pattern_found` navigable-history entry, which is precisely that named
exception (this same attempt's own concluding snapshot,
`excluded_zone_cells(filler.best_assignment)`, published before
`_optimize_before_cleanup`/cleanup ever run — the same `d`/diagnostics
dict backs both this entry and the harvesting loop's live tile). Always
`[]` everywhere published *after* that point: the next attempt's own
cycle-start preview (`pattern`/`pattern_generated`, whether following a
"reprise telle quelle" or a fresh pattern — `_impossible_this_attempt`
belongs to a `Filler` that doesn't exist yet at cycle-start), and a fully
successful search's own `minimizing` step (nothing left excluded to
show). This scoping also happens to sidestep a real display-priority
detail: `pollJob`/`advanceLiveDisplay` (`frontend/static/script.js`)
prioritizes draining any `previewHistory` backlog over rendering the bare
`live_preview` channel, and in a fast mode (many paliers/second) that
backlog rarely empties — so a signal confined to `live_preview` alone
would rarely reach the screen; `last_examples` carrying it too means the
signal still reaches the screen during exactly such a backlog. `Filler.
on_checks_progress`/`on_live_state`
(below) are what prove, live, that such an attempt is still genuinely
working rather than stuck. Blackening is a distinct
mechanism from plain "nettoyage" cleanup, at the user's explicit request:
`_clean_blocked_slots` never blackens a cell for a slot in its own
`deadlocked_slots` subset (recomputed fresh by each caller that passes
it — `interactive_clean_impossible_zones`, `_clean_continue_candidate`),
skipping the ordinary 1-in-10 black-cell alternative entirely for it —
such a slot only ever has whatever crossing word(s) happen to touch its
OTHER cells removed (the same unconditional "remove everything crossing
an impossible slot" plain cleanup already applies to any impossible
slot), which does nothing at all when both sides of the deadlock are
still open (nothing assigned to remove) — left flagged impossible until
resolved some other way: a manual edit, or automatic generation's own
pattern-reshaping (`_shorten_impossible_zones`/`_lengthen_impossible_
zones`, or a fresh pattern on a later palier), which already treats black
cells as fully mutable on its own terms regardless of this exclusion.
`Filler._crossing_deadlock_slots`/`_crossing_deadlock_indices` also
return the exact conflicting cell(s) alongside the slot indices, so both
Interactive mode and the automatic-generation attempt previews can
highlight that specific cell in a more vivid red (`--error`) than the
rest of the same impossible slot(s) (`--incorrect-bg`) —
`_interactive_fill_diagnostics` returns this as a third value,
`deadlock_cells`, always a subset of `impossible_cells`; `Filler.
deadlock_zone_cells()` is its live-search counterpart, folded into every
preview `examples` entry `try_fill`/`generate_grid` build (alongside
`impossible_cells`) the same way `theme_cells`/`challenge_cells` already
are — `frontend/static/script.js`'s `renderAttemptPreview()` reads it as
`deadlock_cells` and adds `.deadlock` (`.attempt-preview-grid .cell.
white.deadlock`).

**Content scoring** — every place in `generate_grid` that has to pick the
"best" grid among several candidates shares one formula, `_content_score`
(a `(word, cells)` pairs iterable in, a number out): sum of squares of
each placed word's own "scored length" — its own length capped at
`CONTENT_SCORE_LENGTH_CAP=7` letters (so one very long word can't
dominate the sum on its own) before any bonus is added. Every placed word
counts, whatever `priority_words`/`challenge_words` say — a theme/
challenge-enriched attempt is favored by a bonus on top of the plain
count, never by excluding the rest of the grid's content from the sum. A
word belonging to its own slot's theme glossary (`priority_words`) gets
`THEME_WORD_SCORE_BONUS=2` added to its capped length before squaring; a
`challenge_words` ("Mots Défi") word gets the larger `CHALLENGE_WORD_
SCORE_BONUS=4` instead (the two never stack — a challenge word that's
also a theme word is scored with the challenge bonus only, once) — so an
attempt that manages to place one is favored over an otherwise-equal one
that doesn't. The *same* formula backs every one of
these selections, successful or failed alike: `opt_score` (tie-break
among every genuinely successful attempt found so far across the whole
search — see "Minimum successful attempts, with worker reassignment"
above — on top of a primary sort by fewest black cells),
`_playable_score`/`_cleaned_playable_score`
(picking a *failed* palier's own "best" attempt to report/carry forward,
raw state vs. post-`_clean_blocked_slots` state respectively), and
`_words_in_place_score` (picking the best cleaned candidate among several
— full nettoyage or "reprise telle quelle" alike, via `_sorted_by_score`,
tie-broken by fewest black cells).

`generate_grid`'s signature accepts `width`/`height`/`difficulty`/
`max_words`/`black_ratio`/`attempts`/`seed`/`wordlist_path`/`on_
progress`/`force_letters_fraction`/`cancel_event`/`black_enrichment_
fraction`/`deadline_checks`/`resume_state`/`should_pause`/`bilingual_
wordlist_path`/`priority_words`/`bilingual_priority_words`/`permanent_
locked_letters`/`permanent_black_cells`/`required_cells`/`challenge_
words`. It returns
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

**"Mots Défi" in automatic generation**: `challenge_words` (`GenerateRequest.
challenge_words` on the web UI's main generation form, alongside
"Thématique") applies the same placement mechanic to automatic generation
as Interactive mode's "Suivant" already applies to a single word: given
priority over both the theme glossary and the ordinary dictionary domain
wherever it geometrically fits a slot (see the CSP-fill cascade above),
and never required to be a real dictionary entry itself. Threaded through
the same `ProcessPoolExecutor` pool-initializer mechanism as `priority_
words` (a `_worker_challenge_words` global, set once per worker,
never a `DualSet` — a challenge word carries no language). Once placed,
it survives the cross-palier cleanup passes that would otherwise treat a
non-dictionary word as an "invented word" bug: `_optimize_before_cleanup`/
`_shorten_impossible_zones`/`_lengthen_impossible_zones`/`_clean_continue_
candidate`'s own calls to `_invalid_fully_known_indices` all additionally
exempt a challenge word's own cells (`_challenge_word_cells`), the same
way they already exempt `permanent_locked_letters`. `minimize_black_
squares` (the final black-cell-removal optimization pass) gives it the
same protection: a placed challenge word's cells are locked/preseeded
into every trial's own `try_fill` and exempted from that function's own
final "every word must be a real dictionary entry" check, so optimizing
a grid can never silently swap out or reject-and-revert a challenge word
already in place. The same three
functions also never treat a still-OPEN slot as "impossible" — and so
never shorten/lengthen/strip/blacken it — as long as an unused challenge
word could still legally fill it (`_challenge_fillable_slot_indices`,
subtracted from every `_impossible_indices` result they compute
internally, including mid-loop recomputations, not just their own
return value), the same exemption `Filler.impossible_zone_slots`/
`interactive_clean_impossible_zones` already apply elsewhere. This
remains a best-effort mechanic, not a hard geometry guarantee: black-cell placement
itself (`make_pattern`/`_prefill_unfillable_slots`) knows nothing about
challenge words, so nothing reserves a slot of the right shape ahead of
time — see "Floating black cells and reshapes" below for the mechanism
that *does* carve one out, inside the search in automatic generation and
per "Suivant" click in Interactive mode.
`backend/grid_store.py`'s `save_grid_json` persists the typed list
(`challenge_words`, never included in `_iter_stored_grids`'s Library-
listing whitelist, so it stays invisible when browsing the Library) so
it round-trips back into the Interactive panel when the grid is later
reopened for editing (`_library_record_to_interactive`). It also feeds
"Content scoring" above: a placed challenge word always counts toward
every one of `generate_grid`'s content scores, regardless of
`priority_words`, at a bonus-boosted length.

**Crossing-safety retry, all three candidate tiers**: `Filler._backtrack`
tries every slot's candidates in one fixed precedence — "Mots Défi"
(challenge words), then the theme glossary (`priority_words`), then the
general dictionary — and applies the same per-candidate `crossing_broken`
forward-check to every one of them: placing a candidate that leaves some
slot it CROSSES with no viable word left (dictionary-dry, and no other
unused/not-yet-abandoned challenge word able to fill it either) reverts
the placement on the spot, with no further recursion, and the candidate
loop simply tries the next entry of the same or a lower tier. The rule
holds on every tier, with exactly one last-resort exception, and its
trigger is global to the attempt, never local to a node. `Filler.solve`
runs the search in two passes: a strict one from the root, where no node
may create an impossible slot and backtracking climbs back to the first
words placed (within `MAX_DESCENTS_PER_NODE` per node); then, only if that
strict search was exhausted from the root — the root itself failed, not
the budget running out (`_budget_exhausted`) nor an abandon — a second
pass from the root with `Filler.breaking_permitted` on. Only in that
second pass does a node that has explored the whole strict subtree of
every one of its slots (non-écarté, then released écarté) without success
come back round with `allow_breaking` and DOES accept a word that creates an
impossible slot, rather than failing and leaving the grid sparse — a
well-filled grid carrying one impossible zone, which the cross-palier
cleanup repairs next palier, beats a grid declared failed early and left
nearly empty. That pass relaxes `crossing_broken` and nothing else:
CREATING an impossible slot is what it licenses, never writing into one
that is impossible already — `crossing_still_impossible` (below) keeps
rejecting a candidate that leaves an already-impossible crossing slot
impossible, at every stage including this one — the one place in the
engine where crossing an already-impossible slot IS allowed is the
last-chance enrichment `_optimize_before_cleanup` runs once a palier has
already failed (see "Last-chance enrichment before cleanup" below), and
it gets there through `excluded_slots` rather than by relaxing this
check. `allow_breaking` is never
inherited by a child node: each one must exhaust its own strict options
first. Candidates keep their normal priority order in the relaxed pass,
so a crossing-safe one is always preferred and is only overtaken once its
own subtree has failed too.

The check is measured against a baseline, and that baseline is free:
`domains`, built at the top of the node before any candidate was placed,
already omits every slot found dry there (those are flagged "écarté"), so
`j in domains` means exactly "j still had a real candidate before this
word was placed". A slot that goes dry only now is the candidate's own
doing and rejects it; one that was already dry is never blamed on
whichever candidate happens to be tried next. This is what makes the
absolute rule workable — without a baseline, one already-dry neighbour
made every candidate at every adjacent slot look unsafe. An "écarté"
slot that is healthy again right now is in `domains` and is therefore
fully protected, exactly like any other open slot.

The same per-candidate check splits into two verdicts, and the baseline is
what separates them. A crossing slot that WAS in `domains` was fine before
the candidate, so this candidate is what made it impossible
(`crossing_broken` — rejected unless `allow_breaking`). A crossing slot
that was NOT in `domains` was impossible already, and finding it still
impossible after the placement means this word crosses an impossible slot
(`crossing_still_impossible` — rejected at every stage, `allow_breaking`
included). A slot the candidate's own letter puts back in play (superseding
a statistical seed, say) is not impossible afterwards and so lands in
neither: what both verdicts judge is the state the candidate LEAVES, never
which neighbour happened to be blocked beforehand. An "emplacement écarté"
that is healthy again is in `domains`, so it may be crossed freely as long
as the placement does not block it again; its only remaining restriction is
the selection deprioritization above.

Placing a word is never the whole picture: `_close_implied_slots`'s own
crossing check (phase 2 above) applies the same rule at the one other place
a word is written.

Every one of these per-candidate checks goes through `Filler.slot_is_
blocked`, whose cost is held down by a per-node `options_cache`
(`Filler._letter_options_cached`, always equal to a from-scratch
`_slot_letter_options`): entries hold per-position letter counts over the
domain minus the node's own used words, so the candidate only removes a
letter it was the last supporter of; a slot crossing the target is keyed
by its known-letter signature (`_known_letters_signature`), one entry per
distinct crossing letter; a blank slot's counts are precomputed once per
length (`_blank_letter_counts`); an entry whose recorded used-word set no
longer matches is recomputed, never reused.

What differs between tiers is only the per-word give-up budget:

- A challenge word or a theme word that breaks a crossing this often
  keeps its own per-word budget (`Filler._challenge_word_budget`/`_theme_
  word_budget`, each `FALLBACK_PHASE_BUDGET_FRACTION = 0.10` of the
  attempt's own `deadline_checks`, resolved once in `solve()`): once
  trying it has broken a crossing slot that many times in this one
  attempt, it is abandoned for the rest of it (`Filler._challenge_
  abandoned`/`_theme_abandoned`, checked via `_active_challenge_words()`/
  `_active_priority_words_for()` everywhere that word's eligibility
  matters) — it stops being injected as a candidate (and, for a challenge
  word, stops exempting a crossing slot's dry domain too), freeing the
  rest of the search's own budget instead of chasing one particularly
  hard-to-place word; normal backtracking across the rest of the search
  tree is what eventually offers a still-active word a different slot
  elsewhere, since `_select_target_slot`'s own level 2 (challenge)/level 5
  (theme) keeps preferring any slot it still fits.
- The general dictionary has no word identity worth tracking that way,
  and needs none: it simply keeps trying its remaining candidates. A word
  accepted under `allow_breaking` never counts against any budget — it
  was not given up on.

Interactive mode's "Suivant" (`interactive_place_word`) applies a
broadened version of this exemption (`_word_breaks_open_slot`), but runs
no search of its own to draw a `deadline_checks`-shaped budget from: it
instead builds every geometrically-fitting (word, open slot) combination
for the whole "Mots Défi" pool up front (`target`'s own slot tried first,
then every other open slot, all ranked by the same statistical score/
frequency the final word draw already used), sets `Filler._challenge_
word_budget` accordingly, and walks the ranked list skipping any
combination that would break a slot, reusing `Filler._register_
challenge_word_break`/`_challenge_abandoned` exactly like the automatic
search does; only once every such combination (ordinary or, failing that,
individually reshaped — see "Priority-tier search in Interactive mode"
below) has been tried does the search fall through to the theme glossary,
then the general dictionary — full detail, including
why each candidate's own black-cell reshape (if any) must be evaluated in
total isolation from every other candidate's, is in that section.

`_word_breaks_open_slot`'s check is deliberately wider, here, than
`_backtrack`'s own inline one: it rejects a candidate that empties the
domain of ANY still-open slot in the grid, not only one literally
CROSSING `target` — a themed glossary is typically narrow, so the very
word chosen for one slot is often also the last unused dictionary
candidate for some entirely disjoint slot elsewhere (no shared cell at
all), and placing it there is just as much an "impossible zone" as a
broken crossing would be. `_backtrack`'s own `crossing_broken` check
stays crossing-only, since automatic generation's cross-palier retry
machinery (`_clean_blocked_slots`/`_build_retry_seed`) already repairs
that broader class of zone on the next palier regardless of how it
arose — `interactive_place_word` has no such follow-up palier, so each
"Suivant" click needs to get this right on its own.

The cases are told apart by what the candidate CROSSES, against the same
`_open_slot_baseline` snapshot (computed once per candidate slot and
reused across every candidate tried there) `_backtrack` gets for free
from its own per-node `domains`. A crossed slot that was alive in the
baseline and is blocked after the placement was blocked BY this candidate
(`"crossing"`, the exact counterpart of `crossing_broken`); one already
dead in the baseline and still blocked afterwards means the candidate
crosses an "emplacement bloqué" (`"crossing_blocked"`, the counterpart of
`crossing_still_impossible`). A DISJOINT slot left with no candidate is
`"other"`; one already blocked before the call is never blamed on
whatever candidate ends up tried — otherwise one pre-existing impossible
zone anywhere in the grid would make every single candidate at every
other slot look unsafe too. `_word_breaks_open_slot` returns
`"crossing_blocked"`/`"crossing"`/`"other"`/`None`, and `_placement_
accepted` maps each verdict onto the `PLACEMENT_LEVEL_*` at which it
becomes acceptable (see "Staged acceptance levels in Interactive mode"
below); `"crossing_blocked"` is accepted at none of them. It
reasons on each crossing slot's own domain, so it catches a slot left with
no word at all but NOT a crossing deadlock the placement creates — the
same domain-iteration cost that keeps `_crossing_deadlock_slots` out of
`_backtrack`'s per-node loop.

**Floating black cells and reshapes**: a "Mots Défi" word — and a
theme-glossary word while fewer than `THEME_RESHAPE_MAX_PLACED_WORDS` (5)
distinct theme words are placed (`_placed_theme_words`/`_theme_reshape_
allowed`) — that no empty slot of its own length can take (known letters
incompatible everywhere) may get one carved out by changing "floating"
black cells, i.e. any black cell not in `permanent_black_cells`. A
reshape is always tied to exactly one word and never outlives it; the
pattern is never reshaped ahead of the search.

*In automatic generation* the reshape is a node option of `Filler.
_backtrack`, on the slot the node just chose (`try_fill(reshape_black_
cells=True)`, passed only by `_pattern_attempt`/`_pattern_continue`, off
whenever `excluded_slots` is given or `MAX_BACKGHOSTS_PER_DESCENT > 0`).
`_with_reshape_candidates` streams the node's candidates lazily as
challenge words, challenge reshapes, theme words, theme reshapes, then the
rest; `_reshape_candidates` picks at most `RESHAPE_WORDS_PER_NODE` (5)
eligible words per family (other length than the slot, unused, still
active, fitting no empty slot), and `_reshape_geometries`/`_reshape_
options` build each `_Reshape`: a shorter word gets a new black cell right
past it, flush against either end of the slot; a longer word frees the
black cell bounding one end, runs on into the white cells beyond it and
gets a new black cell right past it unless the run already ends there. An
option is kept only if the word agrees with every known letter of its
span, no known letter is blackened, `is_structurally_valid(min_interior_
free=1)` holds, and no slot holding a word is altered — every other slot
maps to its new index (`forward`). `_try_reshape` then memorises the
state the change replaces (`_apply_reshape`: slot list, pattern,
assignment, `_tolerated_dry`, `_placement_seq`, the écarté list, all
carried over to the new indices through `_index_slots`), places the word,
checks the slots it crosses plus every slot the change created (a created
slot counts as healthy before the placement — `crossing_broken`/`crossing_
still_impossible` otherwise as usual), and recurses. Whenever the word is
refused — on the spot or once its subtree failed — `_undo_reshape` puts
the modified black cells back in their original state with the slot list
and per-slot state that went with them (the écarté entries made since are
translated back), the child's conflict set is translated to the original
indices (unmappable -> `None`), and the node moves on to its next option.
A reshape option always counts as a descent, whatever its family; a
refusal for a crossing counts against the word's give-up budget. Records
store the slot list/pattern they were taken on (`best_slots`/`best_
pattern`); after `solve()`, `Filler.adopt_best_structure` moves back onto
the record's grid, `try_fill` uses `filler.slots` from there on and
writes `filler.pattern` into the caller's `grid` in place, and the live
callbacks publish `filler.pattern`/`filler.slots`. `stat_letters` skips a
cell no slot covers, and `build_partial_letters_grid` never overlays a
seed or locked letter onto a black cell.

*In Interactive mode* (`interactive_place_word`), `_find_priority_word_
placement` gives each word with no free, letter-compatible slot of its
length (`_free_matching_slot`) its own isolated widen-then-shorten attempt
(`_try_reshape_for_word`) on a copy of the grid, kept only if that word is
the one placed. Widening (`_widen_one_floating_black_cell`) scans up to
`WIDEN_BLACK_CELL_WINDOW` shuffled floating black cells and, for each,
the merged run relocating it would produce (`_white_run`); if the word
fits flush against either end while keeping `is_structurally_valid(min_
interior_free=1)`, the black cell moves to bound the word on its far side
(`_try_widen_black_cell`). Shortening (`_shorten_one_slot_for_word`) scans
up to `SHORTEN_SLOT_WINDOW` (`FALLBACK_PHASE_BUDGET_FRACTION` of
`WIDEN_BLACK_CELL_WINDOW`) empty slots longer than the word and casts a
new black cell right past it, flush against either end (`_try_shorten_
slot`). Both refuse to blacken a known letter and refuse any change that
would leave a PERPENDICULAR slot with no real dictionary candidate
(`_perpendicular_slot_stays_valid`/`_slot_has_domain`: freeing a cell
merges it into the perpendicular slot(s) next to it, checked with its
about-to-be-written letter applied; blackening one splits its
perpendicular slot, each piece checked with the letters known there). The
theme tier passes `allow_reshape` from the theme words already on the
grid and a `fits_reshaped` check keeping a reshaped theme word on a slot
of its own glossary's direction. `WIDEN_PRIORITY_WORDS_LIMIT` caps the
words tried per tier.

**Priority-tier search in Interactive mode**: `interactive_place_word`
tries, in order, "Mots Défi" (`Filler.challenge_words`), then the theme
glossary (`Filler.priority_words`), then the general dictionary at
`target` alone — the same three-tier precedence automatic generation's
own `_backtrack` applies (see "Crossing-safety retry, all three candidate
tiers" above), but built around one hard constraint the batch widening
mechanism above never has to satisfy: only ONE word is ever placed per
call, so whichever black-cell reshape (if any) ends up backing it must be
decided *before* anything is committed, and every OTHER word's own
would-be reshape must never touch the grid at all — every attempt is
independent of every other, each one isolated on its own copy of the
grid whenever more than one is under consideration at once.
`_find_priority_word_placement` (shared by both the "Mots Défi" and the
theme tier) is what enforces this: it first tries every ordinary,
already-dictionary-viable slot the word pool fits, purely against the
grid's own untouched base pattern/`Filler` (`target`'s own combos first,
each slot's own words drawn by `Filler.ordered_candidates` and the slots
themselves following their own best-scored word,
`_word_breaks_open_slot` rejecting any that breaks a crossing) — nothing
here can ever be contaminated, since no reshape is involved at all. For
the "Mots Défi" tier, and for the theme tier while fewer than
`THEME_RESHAPE_MAX_PLACED_WORDS` theme words are placed (otherwise it
passes `allow_reshape=False` and stops here), and only
once every ordinary combo has failed, does it give each remaining word
with no natural or ordinary slot anywhere its own, fully ISOLATED
widen-then-shorten attempt (`_try_reshape_for_word`, one independent copy
of the base pattern per word, discarded immediately if unused — never a
shared one), builds a brand-new, throwaway `Filler` from that ONE copy
alone (`_build_interactive_filler`), and runs the exact same `_word_
breaks_open_slot` check against it — the real, final pattern this
specific candidate would leave behind if chosen, nothing else mixed in,
so the check can never be fooled by another word's own reshape. `Filler.
_register_challenge_word_break`/`_register_theme_word_break` (and their
own `_challenge_word_budget`/`_theme_word_budget`, sized from the total
number of ordinary combos plus reshape attempts considered) are always
applied to the grid's own outer `Filler`, never to a per-candidate
isolated one, so a word's abandonment bookkeeping persists correctly
across both phases regardless of which specific `Filler` ends up
confirming any one candidate. The exemption `_word_breaks_open_slot`
checks (some OTHER still-active "Mots Défi" word able to bail out a slot
this candidate would otherwise break) is always drawn fresh from the
grid's own current challenge pool, regardless of which tier is running —
that check is hard-coded to challenge words specifically. Once a tier's
search returns a winner, `interactive_place_word` reconciles that
winner's own pattern (the base one, untouched, for an ordinary pick; the
one isolated reshape copy, for a "Mots Défi"/theme pick that needed one)
straight into the real letter grid and writes the word's own letters in
— no revert-unused-reshapes pass is needed any more, since nothing but
the eventual winner's own single reshape (if any) was ever applied to
begin with. Each tier has its own `target`, selected by the cascade
against that tier's own glossary only (`_tier_target`, through
`_select_target_slot`'s `challenge_level`/`theme_level` switches): the
"Mots Défi" tier with level 2 alone, the theme tier with level 5 alone,
the general dictionary with neither — so once a glossary tier fails, the
next one re-evaluates the candidate slots instead of inheriting slots
chosen for a glossary it does not apply. Each is resolved once per call,
on first use, and reused at every acceptance level. The general-dictionary
tier sweeps every still-open slot in the cascade's own order, levels 2
and 5 off (`_cascade_slot_order`, its own target yielded first), not its
target alone; each swept slot
orders its own candidates through `Filler.ordered_candidates` — the same
shuffle/statistical-sort/sliding-window draw the automatic search uses,
never a strict argmax, which would make "Suivant" return the same word on
every click for a given grid state — and keeps the first acceptable one.
A slot whose every
candidate is refused becomes an "emplacement écarté" (`_general_dictionary_
pick` returning `None`) and the sweep moves on, so the whole grid is
declared impossible only once no still-open slot can take a word at all,
at any acceptance level (see right below). Within each sweep, a slot
already deemed blocked (`blocked_targets`) is tried only after every other
one. Each slot excludes every "Mots Défi"/theme word from its own candidate
pool outright (any such word still present in its domain was necessarily
already tried, across every slot in the grid, by one of the two tiers
above) — falling back to the raw, unfiltered domain only if excluding both
pools would leave nothing at all, so "Suivant" never gets stuck. The slots
set aside this way — at most the `MAX_EXCLUDED_SLOTS` (3) most recently set
aside, the same `_RecentSlots` cap the automatic search applies — come
back as `excluded_cells` (`_slot_cells_of`, every
cell of the slot, letters included — an "emplacement écarté" is a property
of a whole emplacement, exactly what `Filler.excluded_zone_cells` reports
for the automatic previews; reporting only the still-empty cells scatters
the overlay into disconnected squares instead), threaded through `POST /api/
interactive/step` into `frontend/static/script.js`'s `interactiveExcludedCells` and
rendered as a yellow background (`.cell.white.interactive-excluded`,
declared before `.interactive-impossible`/`.interactive-low` so a stronger
signal wins the cell) — the same "emplacement écarté" notion, and the same
colour, automatic generation's own previews already use.

Each "Suivant" also reports the slots the target of the tier that placed
the word (the last tier tried when nothing is placed) was drawn from:
`Filler._select_target_slot` keeps its level-6 geometric window in
`Filler.last_selection_window` (a plain attribute assignment, overwritten
by every call), which `_tier_target` reads right after resolving that
tier's target — before the tier-3 sweep re-runs the cascade — and turns into
`window_cells` (`_origin_closest_cells`: for each window slot, its cell(s)
closest to `SLOT_SELECTION_ORIGIN`, i.e. the cell that gives it its level-6
score, ties included). Threaded through `POST /api/interactive/step` (and
the start job's result; empty on a resume) into `script.js`'s
`interactiveWindowCells`, rendered as a blue outline
(`.cell.white.interactive-window`, `outline` so it composes with every
background and box-shadow state), with the same staleness lifecycle as
`interactiveExcludedCells`.

**Staged acceptance levels in Interactive mode**: `interactive_place_
word` cannot express `_backtrack`'s own three-stage node (non-écarté
slots, then released écarté ones, then `allow_breaking`) by recursing,
since one "Suivant" click makes exactly one decision — so it re-runs its
whole three-tier search, over the whole grid, once per acceptance level
(`_placement_accepted`, `PLACEMENT_LEVEL_STRICT`/`_DISJOINT`/
`_BREAKING`), threading `level` through `_find_priority_word_placement`
and `_general_dictionary_pick`:

- `PLACEMENT_LEVEL_STRICT` — only a candidate whose verdict is `None`;
- `PLACEMENT_LEVEL_DISJOINT` — additionally `"other"` (a disjoint slot
  left with no word), tolerated because one pre-existing impossible zone
  elsewhere would otherwise stall "Suivant" for good;
- `PLACEMENT_LEVEL_BREAKING` — additionally `"crossing"`, the one and
  only exception to "never create an impossible emplacement"
  (`DOC_ALGO/FR/Lexicon.md`, "case croisée bloquée"): once nothing else
  can be placed anywhere, a well-filled grid carrying an impossible zone
  — which "Nettoyer", a manual edit or "Finir la grille" then repairs,
  the zone having to exist before it can be cleaned — beats a grid
  declared impossible and left half empty.

A whole level is exhausted, across all three tiers and every still-open
slot, before the next is tried, so a stricter placement anywhere in the
grid always outranks a more damaging one and "Mots Défi" keeps its
precedence over the theme glossary and the general dictionary at every
level; every slot set aside at one level is released at the next, exactly
as `_backtrack`'s own stage 2 releases its écarté slots. A slot the sweep
finally places on at a later level is dropped from `excluded_cells`
rather than reported yellow. A candidate accepted at a tolerant level
never counts against a tier's own give-up budget (`_register_challenge_
word_break`/`_register_theme_word_break`), the same rule `_backtrack`
applies to a word accepted under `allow_breaking`. `"crossing_blocked"`
is refused at every level, so `interactive_place_word` still reports the
grid impossible when every candidate of every still-open slot would cross
an already-blocked emplacement.

On the frontend, `interactiveNextBtn`'s click handler (`script.js`)
replaces the whole `interactiveGrid` from `POST /api/interactive/step`'s
own response (`data.grid`) rather than patching only `data.placed.cells`
— a relocated black cell can land outside the placed word's own cells,
so patching only those would silently leave the client's own grid
showing the stale, unwidened black-cell layout even though the backend's
grid was correct, and every following "Suivant"/"Précédent" call would
then diverge from the server's own state. The undo stack ("Précédent")
needs no separate handling for this: it already snapshots/restores the
whole grid, so a widened black cell is reverted along with everything
else a "Suivant" click changed.

**`GenerationCancelled`**/**`GenerationPaused`** are cooperative
exceptions checked at palier boundaries, inside `_backtrack` (every
`CANCEL_CHECK_INTERVAL` checks, see `Filler._periodic_checkpoints`
below), inside `minimize_black_squares`'s
removal loop, and between words during clue generation — no worker
process is ever force-killed; a check simply returns/raises at its next
natural checkpoint.

**Live progress reporting during a still-running attempt** — two
cooperative callbacks on `Filler`, both fired from inside `_backtrack`
independently of whether a new record is ever reached, since `Filler.
on_new_best` alone (fired only when `best_assigned_count` improves) can
stay silent for tens of thousands of checks during a genuine plateau (see
"Limites de la recherche" in `DOC_ALGO/FR/ReadMe.md`), which otherwise
made a slow-but-alive search visually indistinguishable from a stuck one:
`Filler.on_checks_progress` (every `CHECKS_PROGRESS_REPORT_INTERVAL=500`
checks) reports the raw `checks` count into a `multiprocessing.Array`
(`_worker_checks_progress`, one cell per concurrent slot of the current
palier, `0..PARALLEL_ATTEMPTS-1`, every cell reset to 0 at the start of
every palier) — each attempt writes only into the one slot (`checks_slot`,
passed as a plain per-task argument alongside the array reference) it was
dispatched into, a slot a freed worker's replacement attempt inherits
(reset to 0 first) when reassigned mid-palier. The parent's own periodic
"% budget consumed" status-line report (`BUDGET_PROGRESS_REPORT_INTERVAL_S`
= 2s) reads the AVERAGE of every slot's own value, never the single
highest one — a max-based reading let one struggling attempt (typically
the one accumulating checks fastest, since a rejected candidate counts as
a check the same as a productive one) pin the report at 100% on its own
while every other attempt of the same palier was still comfortably below
its own budget and genuinely improving the grids shown in the live
preview — instead of the `best_state_queue` messages `on_new_best` alone
produces. `Filler.on_live_state` (every
`LIVE_STATE_HEARTBEAT_INTERVAL=5000` checks, coarser since it costs a
real `build_partial_letters_grid` call) publishes the CURRENT `self.
assignment` — which can be less complete than the last record, since
backtracking freely retreats — onto the same `best_state_queue` as
`on_new_best`, tagged `"kind": "heartbeat"`; `generate_grid`'s drain
thread forwards a heartbeat straight to the live preview exactly like a
real record, but never appends it to `best_state_buffer` (which feeds the
end-of-palier candidate selection — a heartbeat must never compete there,
since it can be a genuine regression relative to an already-published
best). A heartbeat skips `impossible_zone_cells()`/`deadlock_zone_cells()`
entirely (each iterates every open slot's own domain — too costly at this
tighter cadence, the same cost profile responsible for a real, separately
fixed slowdown) — its own tile simply shows no red/orange highlighting
for the instant it's displayed.

Every interval-throttled signal of the search (`cancel_event`,
`on_checks_progress`, `on_live_state`, `batch_abandoned_event`,
`attempt_done_event`, the optional unfillable-abandon check) lives in
`Filler._periodic_checkpoints`, called both at `_backtrack`'s entry and
right after every `self.checks += 1` of its candidate loop. Each fires via
`Filler._checkpoint_due` — once at least its interval has ELAPSED since it
last fired (tracked per signal in `_last_checkpoint_checks`), never on an
exact `checks % N == 0` match: `checks` advances once per candidate, most
candidates are rejected without recursing, so an entry-only exact-match
test was skipped over for arbitrarily long stretches — a worker then
published no heartbeat, no progress, and noticed neither "Stop" nor a
sibling's early-stop signal, while its live tile stayed blue and frozen.

Every live-preview example dict (`on_live_preview`'s own `examples`
argument) also carries `budget_percent` — that one process's own share of
`checks_progress` (via `seed_to_checks_slot`, mirrored by `seed_to_
lineage`), expressed as a percentage of `resolved_deadline_checks` and never capped
at 100 (the elastic budget below lets an attempt run past its own
deadline; the palier-wide average is uncapped too), shown
on the live tile's own stats line next to the pencil icon. Unlike the
parent's palier-wide average above, this is read straight off the
process's own array slot, unaveraged — a "computing" entry gets it fresh
on every publish (via `current_seed_to_checks_slot_ref`, mirroring
`current_seed_to_lineage_ref`'s own reference-publishing convention) AND
every `BUDGET_PROGRESS_REPORT_INTERVAL_S` from the drain thread
(`_refresh_computing_budget_percents`, resolving each tile by its process
number through both refs, since a cycle-start tile carries no current
seed), republishing the preview when a value changed; a
"succeeded"/"failed"/"interrupted" entry's own value is read once, right when that
attempt's final live tile is built, before the mid-palier worker-
reassignment logic resets that same array slot to 0 for whichever new
attempt inherits it — so the frozen tile keeps the real percentage that
specific attempt actually consumed. Every cycle-start ("pattern")
preview starts at 0%, since `checks_progress` is reset to all zeros
for the new palier before that preview is even built.

A finished attempt's frozen tile ("succeeded"/"failed") carries its
`attempt_id`, and the drain thread drops any record or heartbeat of that
same attempt that reaches it afterwards: a worker queues its last messages
on `best_state_queue` just before returning, and the harvest loop can get
the result through the executor first, so without this check the late
message turned the finished tile back to "computing" for good.

**Elastic per-attempt budget** — `Filler._deadline_reached_without_
extension` is the single source of truth both of `_backtrack`'s own
deadline checkpoints consult (the cheap one at the top of the function,
and the per-candidate one inside the `for w in cands:` loop, which is
the one that actually bounds a slot whose candidates mostly get rejected
without ever recursing back into the top-of-function check). For a
caller with no sibling visibility (`_checks_slot`/`_sibling_checks_
progress`/`_sibling_attempt_active` all `None` — interactive mode,
`minimize_black_squares`, a solitary CLI run), this is the plain,
unconditional "budget's up" rule, unchanged. For a palier's own parallel
attempts, the budget is elastic instead: an attempt whose own `checks`
has exceeded its `deadline_checks` keeps searching past it as long as
some sibling attempt of the same palier is still genuinely racing
towards its own deadline — stopping this one right now would just leave
its CPU core idle until that slower sibling finishes anyway, since the
palier's own harvesting loop already waits for every one of its futures
regardless. `Filler._siblings_still_racing` answers this by reading two
`multiprocessing.Array`s shared for the whole palier: `checks_progress`
(one cell per concurrent slot, each attempt's own live `checks` count —
already existed for the palier-wide average above) and the new
`attempt_active` (one byte per slot, 1 while that slot's own attempt is
genuinely still running, 0 once it returns or before it's ever
dispatched — `checks_progress` alone can't tell "still racing" apart
from "already stopped early with a low, now-frozen count"). Both are
reset to all zeros at the start of every palier; `_pattern_attempt`/
`_pattern_continue` set their own `checks_slot` cell of `attempt_active`
to 1 right before calling `try_fill` and back to 0 in a `finally` once it
returns, whatever the outcome.

The "keep going" verdict is checked immediately the first time the
deadline is crossed (`self.checks += 1` in the per-candidate loop makes
this exact-match reliable there), then only every `CHECKS_PROGRESS_
REPORT_INTERVAL` checks past that point (the same cadence every
sibling's own `checks_progress` cell is itself refreshed at, so checking
more often couldn't see fresher data regardless) — not on every check
past the deadline, keeping the extension's own cost negligible. Once a
periodic re-check finds no sibling still racing, the verdict flips to
"stop" and STAYS there for the rest of this attempt
(`Filler._deadline_extension_denied`) — this stickiness is essential,
not an optimization: ordinary backtracking only ever reacts to a `False`
return by trying the NEXT candidate at that same slot, so a transient,
non-sticky "stop" would reject only one candidate and let the loop place
a different one right after, on a check count that no longer falls on a
checkpoint — never actually unwinding the search the way the plain,
irreversible `self.checks > deadline_checks` condition this replaces
always did (confirmed live: an early version without the sticky flag let
an attempt with no racing sibling run to full, unconstrained completion
instead of stopping within a handful of checks past its own deadline).

**Interactive-authoring module functions** (backing the web UI's
"Interactif" word-by-word mode, all operating on a plain `grid`/`rows`/
`cols`/`index` without any of the parallel-attempt machinery above):
`interactive_place_word` (places exactly one more word — no backtracking
for an ordinary candidate, reusing the same 9-level slot-selection
cascade, except for the bounded "Mots Défi" crossing-safety retry
described above, the one case where several (word, slot) combinations
genuinely are tried in sequence before this call commits to one; it can
carve out a right-sized slot for a "Mots Défi" word (or theme word, under
the 5-placed-theme-words threshold) with no matching-length slot, on an
isolated copy of the grid (see "Floating black cells and reshapes"
above), passing this call's own already-placed letters as
`locked_letters` so an existing crossing letter can never be destroyed;
its own word draw prefers an unused,
crossing-safe "Mots Défi" word over an unused theme word, which in turn
comes before every ordinary candidate — and, once no challenge word can
be safely placed this round, the theme-glossary/general-dictionary
fallback is itself crossing-safety-aware too (see "Crossing-safety retry,
all three candidate tiers" above): the whole three-tier search is re-run
over the whole grid once per acceptance level (see "Staged acceptance
levels in Interactive mode" above), so a candidate that creates an
impossible emplacement is only ever placed once no tier, at any
still-open slot, has anything safer to offer. `challenge_words`, a plain
frozenset of bare uppercase words, built by `POST /api/interactive/step`'s
own handler from `InteractiveStepRequest.challenge_words` (the author's
own typed spelling, accents/case kept — see that field's own docstring)
via `challenge_word_grid_form` (fold a ligature letter — `œ`/`Œ`/`æ`/`Æ` —
into its two separate letters, NFKD-normalize, drop combining marks,
uppercase, strip anything left outside A-Z — the wordlist's own MOT-
column convention). A "Mots Défi" word is matched purely geometrically —
`Filler._challenge_word_fits`, length plus any letter already fixed by a
crossing word — never required to be a genuine dictionary entry the way
every other candidate is, so a real proper noun typed there (a surname,
say) can still be placed automatically once the crossing-safety retry
finds it a slot that doesn't break anything; if none does, it shows up
flagged invalid by this same function's own diagnostics below instead,
same as one placed by hand); `interactive_clean_
impossible_zones`/`interactive_minimize_black_cells` (manual equivalents
of the automatic cleanup, the latter additionally trying to remove every
black cell outright); `interactive_slot_candidates` (dictionary words
fitting a slot's known letters, theme matches unbounded, others capped
at `INTERACTIVE_SLOT_CANDIDATES_LIMIT=300`); `interactive_crossing_
words` (letter/word options at one cell, both directions at once);
`interactive_boundary_candidates` (dictionary words that can start or end
a slot, from length 2 up to its own full length, honoring letters already
placed — a shorter-than-full-length candidate is only offered when the
single boundary cell right beyond it is free to turn black: not already
carrying a letter, and structurally valid at `min_interior_free=1`;
`INTERACTIVE_SLOT_CANDIDATES_LIMIT` applies per length rather than once
over the combined pool, so short lengths never crowd out longer ones).
All three of these candidate-listing functions return every word wrapped
as `{"word", "unsafe"}` (`_words_with_unsafe_positions`) instead of a bare
string — `unsafe` is the sorted list of 0-indexed positions within that
word where placing it would NEWLY make a crossing slot impossible to fill
(`_unsafe_letter_positions`: per position, compares that crossing slot's
own real-dictionary domain, minus words already used elsewhere, before vs.
after hypothetically writing this candidate's letter there — a crossing
slot already impossible beforehand, for an unrelated reason, is never
reported; `_challenge_word_fits_cells` exempts a still-available "Mots
Défi" word the same way `_challenge_fillable_slot_indices` does elsewhere).
All three now also accept `challenge_words` (threaded from each endpoint's
own `InteractiveCandidatesRequest`/`InteractiveCrossingRequest`/
`InteractiveBoundaryRequest.challenge_words`, converted via `challenge_
word_grid_form` exactly like `/step`/`/impossible`) purely for this
exemption. `frontend/static/script.js`'s `appendWordLetters()` (shared by
the "Mots"/"Croisés"/"Début"/"Fin" panels) renders each candidate one
`<span>` per letter, adding `.interactive-word-unsafe-letter` (red
underline, `--error`) to a flagged position — composing with the existing
`.interactive-word-highlight-letter` (blue, the selected-cell letter) when
both land on the same letter. Each of these four buttons' own results
block carries an "eye" toggle button in its header
(`createInteractiveWordsEyeToggle()`), top-right of the emplacement
label: clicking it hides every candidate flagged with at least one
unsafe letter (`.interactive-word-item-unsafe`, set at render time
whenever a candidate's own `unsafe` list is non-empty), leaving only the
safe ones to compare, and swaps the button's icon to a crossed-out eye;
a second click restores them and reverts the icon. Purely a display
filter, scoped to that one stacked block — it never re-fetches or
re-renders, so it can't disturb any other stacked block's own state.
The comma between two candidates in these blocks is CSS-generated
(`.interactive-word-item`'s own `::before` rule, matching a visible item
preceded by another visible sibling) rather than a literal `", "` text
node, so a hidden candidate never leaves a stray comma behind.
`_interactive_fill_diagnostics` (returns `(impossible_cells, low_
candidate_cells)` for the live red/orange grid highlighting, also
catching a word invented purely by crossing letters that isn't real —
takes an optional `challenge_words`: a "Mots Défi" word is considered
part of the dictionary for this check, never flagged red, whether it's a
still-open slot only a challenge word could fill (folded into "low
candidates" instead — `_challenge_fillable_slot_indices`) or an already-
typed slot spelling one verbatim (`_challenge_word_cells`, exempted from
`_invalid_fully_known_indices`); `interactive_clean_impossible_zones`/
`interactive_minimize_black_cells` (`POST /api/interactive/clean`, both
`Nettoyer` and `Nettoyer (+noires)`) and `POST /api/interactive/verify`
(`Vérifier`) apply the same exemption, so none of them ever strips out
or reports a validly placed challenge word as invalid).

`_interactive_letter_stats` (`POST /api/interactive/stats`, the "Stats"
button, placed just before "Impossibles") is a read-only diagnostic
mirroring `_interactive_fill_diagnostics`'s own structure: for every
still-empty white cell it runs `sample_letter_biases` (`force_
fraction=0.0`, so nothing is ever forced into the grid — see "Les
graines" above) with `known_letters` built from the grid's own
already-placed letters, and returns the single most common letter of
each cell's crossed tally (`_most_probable_letter` over
`_crossed_letter_counts`: letters both the across and the down slot
observed, each at the lower of its two counts — `[[row, col, letter],
...]`, omitting a cell whose every crossing slot is already impossible
and so contributes nothing to the tally, or whose two directions share
no letter). `frontend/static/script.js` renders each returned letter as
a light-gray overlay `<span>` (`.interactive-stat-letter`) inside the
otherwise-still-empty cell, alongside the existing `.cell-number`
overlay. "Stats" is a two-state toggle (`.toggle-btn`, `interactiveStatsOn`,
on by default): while on, `renderInteractive()` calls
`scheduleInteractiveStatsRefresh()`, which refetches the letters
(debounced, `INTERACTIVE_STATS_DEBOUNCE_MS`) whenever the grid on screen
differs from the one they were fetched for (`interactiveStatsGridKey`,
reset by `clearInteractiveDiagnostics()`); a response arriving for a grid
no longer on screen is dropped.

The automatic-generation previews carry the same letters: `Filler.
stat_letters(assignment)` returns the most likely letter of every white
cell `assignment` leaves undetermined (no assigned word, locked letter or
seed on it), read from the live crossed tally;
`Filler.best_stat_letters` snapshots it whenever `best_assignment` is
recorded (the tally follows the CURRENT assignment and is unwound with
it), read back through `best_stat_letters_for()`, which drops any cell
`_close_implied_slots` has filled since. `try_fill` puts it in
every preview dict as `stat_letters` — `_publish_new_best` (the record's
snapshot), `_publish_live_state` (the current assignment's), and the
final failure diagnostics (the record's) — and `generate_grid` forwards it
on the live tiles (computing, and the "failed" whitelist) and on the
`pattern_attempt_failed`/`pattern_found` key step (`last_examples`),
never on cycle-start, post-cleanup or `minimizing` previews.
`renderAttemptPreview()` shows each as a `.preview-stat-letter` span in
a still-empty (".") cell, only while "Voir" (`showPreviewLetters`) is on,
like the real letters.

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
paraphrases` (the "Paraphraseur" panel), `correct_text` (Interactive
mode's two "Corriger" buttons: one call returning the typed definition or title with its
agreement errors, typos, missing accents, missing spaces, lowercase first
letter and unnatural word order (an adjective on the wrong side of its noun)
fixed and its wording kept;
the first line of the answer, the input unchanged if it is empty). Every clue-generation call
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

Four independent filesystem stores, one JSON file shape shared with the
`generate_grid()` result dict plus metadata:

- **`GRID_STORE/<language|bilingual>/`** — one file per published grid,
  named `<timestamp>_<title-slug>_<4-digit-code>.json`, never rewritten.
  `save_grid_json`/`get_grid`/`list_grids` (paginated, filterable by
  language/difficulty/seen-state/pseudo).
- **`GRID_WORK/`** — one continuously-overwritten file per in-progress
  "Interactif" authoring session, named `<timestamp>_<pseudo-slug>_<job_
  id>.json`. `save_grid_work`/`get_grid_work`/`list_grid_work`/`delete_
  grid_work`. Every save carries a `previous` field: every top-level field
  the record held right before this save overwrote it (minus its own,
  now-stale `previous`, so this only ever holds one step of history, never
  a full chain), or `None` on a session's very first save — a diagnostic-
  only extra a bug report can be replayed from directly, without asking
  the player to hit "Précédent" first purely to hand over a "before"
  snapshot. Every reader of this record keeps reading the top-level fields
  for the CURRENT state; `previous` is never itself the active session
  state a resume rebuilds from. A `diagnostics` field pairs with it,
  holding everything the editable grid was SHOWING at that save beyond its
  own letters — `impossible_cells`/`deadlock_cells`/`low_candidate_cells`/
  `excluded_cells`/`window_cells`/`invalid_cells`/`theme_cells`/
  `challenge_cells`/`zone_cells`, the "Stats" letters (`stat_letters`, `[row, col, letter]`
  triples), and `last_placed` (the `placed` object of the last "Suivant"
  step: word, cells, direction, `from_theme`/`from_challenge`) — sent by
  the client on every autosave and on "Sauvegarder"/"Publier"
  (`InteractiveSaveWorkRequest`/`InteractiveSaveRequest.diagnostics`,
  `None` from any caller with no display of its own). None of it is
  recomputable from the saved grid: an "emplacement écarté" in particular
  exists only in the one `POST /api/interactive/step` response that
  reported it, since `interactive_place_word` rebuilds that list from
  scratch on every click (see "Priority-tier search in Interactive mode"
  above). With `previous["diagnostics"]`, a record therefore holds both
  displayed states — the one on screen and the one right before the last
  click. Diagnostic-only, and absent from `_iter_stored_grid_work`'s own
  "Créations" listing whitelist.
- **`GRID_GAME/<grid_id>/<pseudo-slug>.json`** — one file per (grid,
  player) pair holding that player's own typed letters + elapsed timer.
  `save_grid_game`/`get_grid_game`.
- **`STOP_DUMP/`** — one file per automatic-generation job interrupted via
  the "Stop" button, named `<timestamp>_<pseudo-slug>_<job_id>.json`,
  written once by `save_stop_dump`. `backend/app.py`'s `_run_generate_job`
  calls it from its own `except GenerationCancelled:` handler, since
  nothing in `crossword_gen.py` catches that exception to build a final
  snapshot itself — the dump instead reuses `JOBS[job_id]`'s own
  already-published state: `live_preview` (the continuously-overwritten,
  one-entry-per-still-running-attempt snapshot list the attempt-preview UI
  itself already renders live — each entry's `example_grid` plus its
  `impossible_cells`/`deadlock_cells`/`excluded_cells`/`forced_cells`/
  `locked_cells`/`theme_cells`/`challenge_cells`, i.e. every cell-state/
  slot-state overlay the live preview draws — each entry also carrying,
  under its own `previous` field, the snapshot that exact tile replaced,
  one step only and `None` for a process's own first tile, the same
  convention `GRID_WORK/` already uses), `examples_history` (every
  completed palier so far, same shape, for context), plus `step`/
  `request`/`resume_state` and the job id/pseudo/timestamp. Best-effort: a
  write failure is logged and never turns a clean Stop into an error.
  Both states of a tile name the channel that published them (`reason`:
  a new record, a heartbeat, a cycle start, a just-finished attempt) and
  the attempt they belong to (`attempt_id`), so a difference between them
  can be attributed to a genuine search step rather than to a change of
  publication channel or of attempt — the two channels publish different
  overlay sets (a heartbeat skips the crossing-deadlock computation
  entirely, see "Live progress reporting during a still-running attempt"
  above).

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

## Java back end (`backend_java/`)

A port of `backend/` to Java 21 (package `falcon`, one Maven module, the
only third-party dependency being Jackson for JSON). Module mapping:
`app.py` → `App.java` (routes, jobs, queues, schedulers) on top of
`Web.java` (JDK `HttpServer`, virtual threads, FastAPI-style `{"detail":
...}` errors and 404/405/422), `Body.java`/`GenReq.java` (Pydantic-style
validation) and `Job.java` (one `JOBS` entry, mutated and serialized under
its own lock); the theme-glossary/Qdrant part of `app.py` → `Themes.java`;
`clues.py` → `Clues.java`; `chatbot.py` → `ChatBot.java` (SSE streaming);
`grid_store.py` → `GridStore.java`; `svg_export.py` → `SvgExport.java`;
`embedder.py`/`qdrant_store.py`/`system_info.py`/`gloss_lookup.py`/
`inflection_lookup.py`/`example_sentences.py`/`dictionary_lookup.py`/
`secret_store.py` → one class each; `crossword_gen.py` → package
`falcon.gen` (`Words`: lexicon loading, `DualIndex`/`PW`, slot candidates;
`Grids`: structure, slots, black-cell patterns; `Filler`: the CSP solver;
`Fill`: seeding, `tryFill`, minimization, overlays, content score;
`Cleanup`: impossible-zone analysis and every cross-palier repair, widen/
shorten; `Generator`: the palier loop and its CLI; `Interactive`: every
`interactive_*` function).

Representation choices that differ from Python without changing behavior:
a cell is one `int` (`row << 16 | col`, `Cells`), a slot an `int[]`; each
length's words are indexed by (position, letter) as `BitSet`s over word ids
(`LenIndex`), so a slot's domain is a `Dom` (the whole length, or a
`BitSet`) and constraint intersection is a bitwise AND; letter tallies are
`int[]` indexed by a process-wide letter registry (`Alpha`). A palier's
`PARALLEL_ATTEMPTS` attempts run as threads of one fixed pool (256 MB
stacks for the recursive search), sharing the read-only index; the
cancel/"attempt done" events are `AtomicBoolean`s, `checks_progress`/
`attempt_active` are `AtomicLongArray`/`AtomicIntegerArray`, the
best-state queue a `BlockingQueue`, and each search thread lowers its own
priority by `CROSSWORDFALCON_GENERATION_NICE` (`renice` of its Linux thread
id). Loaded lexicons + indices are cached per (wordlist, difficulty) for
the process lifetime. RNGs are seeded per attempt like Python's, but with
Java's generator, so a given seed does not reproduce Python's exact grid.
The daily RSS/SCRAPP refresh runs the Python scrapers through `.venv`.
Python text semantics are reproduced where Java differs (`Py.strip`/
`Py.split`: Unicode whitespace; `Py.fmt`/`Py.round`: half-even rounding of
the exact binary value; Unicode-aware regexes), which keeps every
deterministic output — endpoint JSON, SVG/PDF sheets, LLM prompts, response
filters — byte-identical to the Python back end.

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
  While a playable grid is on screen (`displayFinalGrid`), the creation
  form is folded (`#generate-form.play-collapsed`, `setGenerateFormCollapsed`
  in `script.js`) behind a `#create-grid-btn` "Créer une grille" button
  that unfolds it; the tool buttons stay visible. `runGeneration`/
  `runInteractive`/`enterInteractiveMode` unfold it.
- **`script.js`** — all client logic in one file. Major areas: grid
  rendering/keyboard input/solution-checking; the interactive-authoring
  mode (by far the largest block — zone selection, undo stack, per-cell
  editing, calls to every `/api/interactive/*` endpoint, candidate/
  crossing-word panels, a "Mots Défi" challenge-word list — stored and
  shown exactly as the author typed it (accents/case kept), with a
  derived bare-uppercase grid form (`challengeWordGridForm()`) computed
  on demand wherever a comparison against actual grid cells is needed:
  tested client-side against every candidate/crossing/boundary result and
  highlighted first, ahead of the theme glossary, with its own
  present-in-grid coloring and authoritative click-to-insert (a challenge
  word need not itself be a real dictionary entry, so this matching never
  goes through the backend's own theme_words/other_words arrays), but
  also sent to the backend verbatim on every "Suivant" click (`Interactive
  StepRequest.challenge_words`, itself deriving the bare grid form server-
  side via `challenge_word_grid_form`) so the automatically placed word is
  drawn from it first, and persisted to/restored from `GRID_WORK` — also
  verbatim, as typed — across a pause/resume of the editing session
  (`grid_store.save_grid_work`'s own `challenge_words` field) — the
  "Créations" drafts panel); a second, simpler "Mots Défi
  (personnalisation)" mini-form on the main generation form itself
  (`#generate-challenge-panel`, right next to "Thématique") — a plain
  add/remove list with no grid yet to color/click-insert against, sent as
  `GenerateRequest.challenge_words` on "Générer la grille" — for either
  mode the selector offers: an ordinary automatic generation, or (`mode
  === "interactive"`) `POST /api/interactive/start`, which reuses the
  same `GenerateRequest` field and round-trips it into the Interactive
  panel's own list on entry (`job["result"].challenge_words`), same as a
  resumed/from-library session already does. `POST /api/interactive/save`
  ("Publier") likewise takes its own `InteractiveSaveRequest.challenge_
  words` from the client's current live list on every call, never from
  the session's own (session-start-only) snapshot. The list `<ul>` is
  only shown once non-empty and grows in height with the number of words
  (plain block flow, no fixed height/scroll, unlike the Interactive-mode
  panel's own grid-height-stretched list). "Thématique"
  (`#theme-field`) uses this exact same "+/-" chip-list mechanic (an
  input, a "+" button, a word list below with per-word "-" removal) —
  `themeKeywords` in `script.js`, joined into one space-separated string
  wherever a `theme` request field is built, so the wire format/backend
  tokenizer (`_theme_tokens`) are unaffected. All three of these text
  inputs (`#generate-challenge-input`, `#interactive-challenge-input`,
  `#theme`) also auto-validate the typed word — the same effect as
  clicking "+" — the instant the user types a trailing punctuation
  character (space, comma, semicolon, colon, period, `!`, `?`), via one
  shared `attachPunctuationAutoAdd()` helper; the library panel
  (paginated/filterable listing, load-into-player, reopen-as-editable);
  the dictionary panel (root-family search, "Définir", "Thématique"/
  "Synonymes", bilingual-aware); the chatbot UI (Markdown rendering,
  incremental streaming, live UI-context snapshot sent as grounding,
  open/collapsed state persisted in its own `cwf-chatbot-state` cookie —
  `max-age` recomputed on every toggle down to local midnight, so a
  reload later the same day restores the last state but a new day always
  shows it open again); the RSS/SCRAPP panel; the presence counter and
  CPU/GPU/queue-length meters; the virtual keyboard; the welcome overlay
  and cookie-based preference storage (`cwf-prefs`).
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
  theme for that one grid. It never gives up on a job while that job still
  runs on the server: a forced stop (second Ctrl-C, SIGTERM), a
  `--per-grid-timeout` abandon or a network error first sends `POST /api/
  generate/cancel/{job_id}` (`_cancel_current_job`, best-effort), so no
  orphaned job keeps occupying the queues after the script exits. Each
  submission also logs a `job_submitted <job_id> <base_url>` line, which
  `run_Populate.sh stop` reads back to cancel that job itself when it has
  to SIGKILL the process.
  `run_Populate.sh` is its start/stop/restart
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
WORKERS` (default 10) is the frontend's own uvicorn `--workers` count,
and `CROSSWORDFALCON_FRONTEND_HOST` (default `0.0.0.0`; `127.0.0.1` keeps
the UI local to this machine) the interface it binds to —
the backend must never be given `--workers`, since its `JOBS`/queues/
schedulers live in one process's memory. `CROSSWORDFALCON_EXPERIMENTAL_
NOTICE` (`backend/app.py`'s `EXPERIMENTAL_NOTICE`, on by default) toggles
a red warning on the web UI's welcome overlay ("site expérimental, en
cours de développement"), surfaced via `GET /api/system_info`'s
`experimental_notice` field — set it to `0` on a stable deployment. See
the `project-best-practices` SKILL's "Ports and environment variables"
section for the full variable list and the dual-GPU LLM routing scheme.

- **`Install.sh`** — installs `rsvg-convert` (runtime dependency), sets
  up the Python venv, installs a JDK 21 + Maven (system package manager,
  or a user-local Temurin JDK/Apache Maven under `~/.local` without root)
  and builds the Java back end, and interactively configures which local
  LLM engine/model to run based on detected hardware.
- **`run_Falcon.sh`** — launches the backend (single process) and
  frontend (multiple workers), stopping any prior listener on those
  ports first, including orphaned CSP-worker child processes, and any
  Java back end started from this checkout (a `java` process running
  `crosswordfalcon-backend.jar` whose working directory is this checkout).
- **`run_FalconJ.sh`** — the same launcher for the Java back end: rebuilds
  the jar only when the sources' fingerprint differs from the committed
  `backend_java/dist/sources.sha256` (`backend_java/build.sh`), stops the
  listeners on both ports and any Python back end started from this
  checkout (`uvicorn backend.app:app`, same working-directory scoping),
  then starts `java -jar backend_java/dist/crosswordfalcon-backend.jar
  --port $CROSSWORDFALCON_BACKEND_PORT` (extra JVM options from
  `CROSSWORDFALCON_JAVA_OPTS`) plus the same Python middleware. Both
  launchers write to `logs/backend.log`.
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
./run_Falcon.sh            # Python back end
./run_FalconJ.sh           # or the Java back end (stops the Python one)

# Java back end: build (only when the sources changed) / check / CLI generator
backend_java/build.sh [--force|--check]
java -cp backend_java/dist/crosswordfalcon-backend.jar falcon.gen.Generator --width 15 --height 10 --deadline-checks 1000 --seed 2

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
