---
name: project-best-practices
description: Project management rules for CrossWordFalcon, applied and kept up to date automatically — decision log in this SKILL, requirements.txt synced with installed packages, Install.sh synced with the install procedure, README.md synced with the project's features. Invoke before/after a structuring project decision, a package install, any change to the install procedure, or adding/changing a feature.
---

# Best practices — CrossWordFalcon project

This SKILL is the living memory of this project's management rules. It must
stay current on its own — don't wait to be asked.

The project's official language is English: code identifiers, comments,
this SKILL, and README.md are written in English. User-facing product
content (the crossword words/clues in all 5 supported languages, the web UI
text) stays in the relevant language — that's the app's domain, not the
project's engineering language.

## Permanent rules

1. **Keep this SKILL current, not historical.** Whenever an important
   project-management decision is made (architecture choice, convention
   change, scope decision, tooling choice, etc.), update the "Decisions"
   section below to reflect the new state — as a present-tense fact
   ("X does Y", "the default is Z"), never as a narrated change ("X was
   changed from A to B because..."). This SKILL is a timeless reference of
   *current* conventions, not a changelog: when a new decision supersedes
   an old one, replace the old fact in place instead of appending a new
   entry next to it. Drop a fact entirely once it no longer describes the
   current codebase, rather than keeping it as history.

2. **Update `requirements.txt`** (project root) whenever a Python package is
   installed (`pip install ...`), added, upgraded, or removed. The file must
   always reflect the real dependency state, ideally with pinned versions.
   Heavy, optional, or platform-specific dependencies (e.g. `llama-cpp-python`)
   belong in their own `requirements-*.txt` instead, so the base install stays
   light.

3. **Update `Install.sh`** (project root) whenever a change implies a
   different install procedure (new system dependency, new setup step,
   Python version change, new data file to generate, etc.). `Install.sh`
   must remain the single source of truth for installing the project from
   scratch.

4. **Never write an absolute path in this SKILL** — only paths relative to
   the project root (e.g. `backend/app.py`, not
   `/Users/.../CrossWordFalcon/backend/app.py`). An absolute path is tied to
   one machine/user and becomes wrong or misleading elsewhere.

5. **Update `README.md`** (project root) for every feature added, changed,
   or removed — but keep it non-technical, for a reader who doesn't develop
   (installation, LLM/API key configuration, launching the app, using the
   web page). `README.md` must not contain architecture, API reference, or
   implementation details (`backend/*.py`, JSON formats, etc.) — that
   technical content belongs in `CLAUDE.md` instead. Never mention this
   auto-update rule inside `README.md` itself — it only lives in this SKILL.

6. **Recompute the *entire* downstream data pipeline whenever the initial
   corpus source list changes.** Adding/removing/changing an OPUS source in
   `build_sentence_corpus.py` means re-running, for every affected language,
   all three stages in order: `build_sentence_corpus.py` (the sentence
   corpus itself), `build_wordlist_freq.py` (frequencies/canonical forms
   depend on what's in the corpus), *and* `build_gloss_dictionary.py`
   (glosses are looked up by each wordlist's own CANONICAL column, so a
   wordlist rebuild can introduce or drop lemmas the gloss dictionary hasn't
   caught up with yet). Doing only the first stage and treating the rest as
   optional/deferred leaves the pipeline in a silently inconsistent state.

7. **Keep `env_default.sh` (project root) in sync with `env.sh`'s structure**
   whenever a notable change touches `env.sh` (a new variable, a changed
   default, a new provider block) — same variables and comments, but never
   a real secret (placeholders like `EMPTY` or `your-...-api-key-here`
   only). `env_default.sh` is checked into the repo specifically so a fresh
   clone has a safe example to copy (`cp env_default.sh env.sh`); `env.sh`
   itself stays gitignored.

8. **Restart any already-running server automatically after editing code it
   loads** — `backend/*.py` or `frontend/*.py` changes and a backend
   (`:3001`) or middleware (`:3000`) process currently up (check with
   `lsof -ti tcp:3000` / `tcp:3001`) means restart it via `./run_Falcon.sh`
   before doing anything else with it, without waiting to be asked.
   `run_Falcon.sh` starts both with plain `uvicorn` — no `--reload` — so a
   running process keeps executing whatever code was loaded at its last
   start; a live check or a user report against a stale process is
   indistinguishable from a real bug. Never conclude a backend-side fix
   "doesn't work" from live testing without confirming the server was
   actually restarted after the fix landed.

9. **Bump `VERSION.txt` (project root) at the end of every completed series
   of changes** — increment the rightmost number (e.g. `0.2.0` -> `0.2.1`)
   once a task/turn's work is done and verified, not per individual file
   edit within it. The version badge in the web UI reads this file
   directly, so it's the one user-visible signal that something changed.

10. **Keep `frontend/static/i18n.js` current with every UI-visible string
    change** — any label, button, heading, status/progress message, or
    error message added, changed, or removed anywhere in `frontend/static/`
    or in a backend/proxy error path needs its `I18N` entry (or
    `describeStep()`/`describeErrorCode()` case in `script.js`) updated for
    **all five** supported languages (fr/en/de/es/it) in the same change —
    never just the language you happen to be testing in.

11. **`DOC_ALGO/FR/ReadMe.md` is a timeless reference, not a decision
    log.** Its whole purpose is to let a reader understand the *current*
    version of `backend/crossword_gen.py`'s algorithm without having to
    read the code — never an inventory of past decisions. Whenever the
    grid-generation algorithm changes, update it to describe the new
    current behavior directly (present tense, as if it had always worked
    this way) — never append "à la demande explicite de l'utilisateur",
    "précédemment", "a été essayé puis abandonné/reverti", a changed-N-times
    account, or any other trace of *how* the current state was reached.
    That narrative belongs in `CLAUDE.md` alone, which both this file and
    `CLAUDE.md` must stay in sync with in terms of *current* algorithm
    facts (dual-write the fact; the story stays CLAUDE.md-only). If a
    change makes an old explanation wrong, replace it in place rather than
    layering a correction on top.

12. **Never test or stress-test `crossword_gen.py` with an artificially
    small dictionary** (e.g. a `max_words=400`-style restriction) — at the
    user's explicit, repeated request: "on n'utilisera jamais un
    dictionnaire aussi petit." Real deployments always use the full
    per-language wordlist (tens to hundreds of thousands of words); a
    400-word (or similarly tiny) vocabulary is not a scenario this project
    will ever actually run, so a failure or a behavior specific to it isn't
    a real regression and isn't worth chasing. To exercise a *hard* CSP-fill
    scenario for verification purposes, use the full wordlist at a smaller
    grid size instead, or `difficulty="hard"` — never a truncated word list.

13. **Every point in `DOC_ALGO/FR/ReadMe.md` must cite its source location**
    — the file name and the class or method name (when relevant) of the code
    that mainly implements it, e.g. "(`backend/crossword_gen.py`,
    `Filler._backtrack`)" or "(`backend/crossword_gen.py`, `generate_grid`)"
    right after the point it documents. **No line numbers** — at the user's
    explicit follow-up request, after finding they drift too often and
    require too many upkeep passes on every unrelated edit; a file/method
    citation stays valid across normal code changes the way a line number
    never does. This applies going forward to new/edited content; the
    file's pre-existing sections have not all been retrofitted with
    citations yet.

14. **Code and code comments must be written in English** — at the user's
    explicit request. Applies to source files (`.py`/`.js`/`.css`/`.html`)
    and their inline comments/docstrings going forward; it does not apply
    to product content the app itself displays (crossword words, clues, UI
    labels — see this SKILL's own convention of English code identifiers
    with translated UI text) or to reports/summaries sent to the user,
    which stay in French (see the user's own persistent instruction on
    report language). Not retroactive: this codebase's existing files
    (`backend/crossword_gen.py` most of all) carry an extensive history of
    French comments predating this rule — they are not being retrofitted
    to English as part of this decision, the same "applies going forward,
    not retrofitted" stance already taken for rule 13's citations.

## Decisions

### Architecture

- The project is called **CrossWordFalcon** (a former rename target;
  renaming the root folder again requires rebuilding `.venv` from scratch,
  since venv scripts hardcode absolute paths in their shebangs).
- Two-FastAPI-server web architecture: `backend/app.py` (the "back end")
  exposes grid generation and clue generation as a JSON API; `frontend/
  server.py` (the "middleware") serves the static UI (`frontend/static/`)
  and proxies `/api/*` to the back end, so the browser only ever talks to
  one origin (no CORS needed). Both servers disable `/docs`/`/redoc`/
  `/openapi.json` and respond only on the routes/files they need — any
  other request gets a plain 404.
- All grid-generation business logic lives only in `backend/crossword_gen.py`
  (never at the project root, never in `frontend/`). It is both the CLI
  (`python3 backend/crossword_gen.py ...`, run from the project root so its
  default wordlist path resolves) and a library imported by `backend/app.py`
  via a relative import.
- English is this project's engineering language (code, comments, this
  SKILL, `CLAUDE.md`, `README.md`); product content (crossword words/clues,
  web UI text) is written in whichever of the 5 supported languages
  (fr/en/de/es/it) the request is in.
- Grid dimensions (`width`/`height`) are independent, default 15×10, with
  only a lower bound (`ge=5` — a grid smaller than that stops making sense
  as a crossword). There is deliberately no upper bound in either the web
  API or the CLI; a very large grid taking a long time is the caller's own
  choice to make, not something the API second-guesses.
- The web UI is a playable crossword, not a read-only viewer: the grid
  starts empty, typing a letter (lowercase advances right, uppercase/
  Shift/Caps Lock advances down) fills the selection, and "Solution"/
  "Vérification" toggle buttons reveal/check answers without discarding
  what the player typed. The language selector drives both the UI's own
  text and which language is requested from the API — one selector for
  both. Visual/interaction/CSS decisions for the web page live in the
  `style-guide` SKILL, not here.
- The version badge in the web UI reads `VERSION.txt` (project root)
  through a dedicated endpoint; bump the file per permanent rule 9 and the
  badge picks it up automatically.

### Ports and environment variables

- **Ports live in the 300x range**: frontend/middleware 3000, backend 3001,
  local LLM server 3002 — moved off the original 800x range after
  diagnosing a real collision: a VS Code helper process was also listening
  on `127.0.0.1:8000`, silently shadowing the real frontend server for any
  client connecting via `127.0.0.1` specifically. Do not move these back
  into the 800x range.
- All three ports are declared once, at the top of `env.sh`/`env_default.sh`,
  as `export VAR="${VAR:-default}"` (`CROSSWORDFALCON_FRONTEND_PORT`,
  `CROSSWORDFALCON_BACKEND_PORT`, `LLM_PORT`) so a value already set in the
  calling shell's environment survives sourcing. `CROSSWORDFALCON_BACKEND_URL`
  and every `LLM_BASE_URL` line are *derived* from these two port variables
  via shell interpolation, never a separately hardcoded literal — changing a
  port once updates every URL built from it. `run_Falcon.sh`/`run_llm.sh`/
  `frontend/server.py`/`backend/clues.py` each fall back to the same literal
  defaults only if neither env file was ever sourced.
- `CROSSWORDFALCON_PARALLEL_ATTEMPTS` (default 10) controls how many
  parallel pattern/CSP-fill attempts `backend/crossword_gen.py` runs per
  palier; also declared at the top of `env.sh`/`env_default.sh`.
- `env.sh` (project root) holds `LLM_BASE_URL`/`LLM_MODEL`/`LLM_API_KEY` and
  is gitignored (real secrets); `env_default.sh` is the checked-in template
  with placeholder credentials only, copied to `env.sh` on a fresh clone.
  `run_Falcon.sh` sources `env.sh` (or `env_default.sh` if absent) before
  starting the backend.
- `run_llm.sh` carries **no hardcoded default GGUF** — `LLAMA_GGUF_REPO`/
  `LLAMA_GGUF_FILE`/`LLAMA_CHAT_TEMPLATE_KWARGS` are required
  (`${VAR:?...}`-style, erroring clearly if unset), sourced only from
  `env.sh`/`env_default.sh`. Changing the default local model only ever
  means editing `env_default.sh` (and `env.sh` to match) — never
  `run_llm.sh` itself; duplicating the default there was a real, now-fixed
  footgun (three copies of the same fact that could silently disagree).
- `LLAMA_FORCE_CPU` (unset by default, any non-empty value counts as set)
  forces `--n_gpu_layers 0` and skips GPU detection/rebuild entirely.
  `backend/system_info.py`'s `get_system_info()` checks this flag first and
  reports `compute: "cpu"` unconditionally when set, before any hardware
  probing.
- `run_llm.sh` auto-detects whether `llama-cpp-python` was actually built
  with GPU support (`llama_cpp.llama_supports_gpu_offload()`) versus what
  hardware is present (macOS → Metal; `nvidia-smi -L` → CUDA), and
  force-reinstalls with the right `CMAKE_ARGS` if they disagree — a plain
  `pip install` only builds a CPU-only binary by default. For CUDA
  specifically, it also checks `nvcc`/`$CUDACXX` (a driver alone, via
  `nvidia-smi`, doesn't imply the CUDA Toolkit is installed) and never lets
  a failed GPU rebuild abort the script — it falls back to CPU. Every
  CPU-fallback message names the actual missing piece (CUDA Toolkit, Xcode
  Command Line Tools, a missing compiler) and how to install it.
- `run_Falcon.sh`/`run_llm.sh` background their server processes with
  `disown` and `< /dev/null` (in addition to `nohup`) so they survive the
  launching shell/terminal closing — `nohup` alone doesn't remove the
  process from the shell's job table on every shell. `setsid` isn't used:
  it's not available on macOS by default.
- `frontend/server.py` proxies `/api/*` through one shared
  `httpx.AsyncClient(timeout=PROXY_TIMEOUT_S)` (30s, one value for every
  proxied route, not a per-endpoint split) and sets
  `Cache-Control: no-store` (plus `Pragma`/`Expires`) on every response via
  a blanket middleware, since this app is edited and reloaded by hand
  during development. `frontend/static/script.js` polls
  `GET /api/generate/status/{job_id}` every `POLL_INTERVAL_MS` (2000ms) and
  its own browser→middleware fetch timeout is kept comfortably above
  `PROXY_TIMEOUT_S` so it never races the proxy's own timeout.
- `backend/app.py`'s `JOBS` is a plain in-memory dict (one uvicorn process,
  no `--workers`), bounded to `MAX_JOBS` (50) entries. `CANCEL_EVENTS`
  (job_id -> event) is a *separate* module-level dict, evicted in lockstep
  with `JOBS` — a job's cancellation event isn't stored inside `JOBS` itself
  because `GET /api/generate/status/{job_id}` returns that entry directly
  and an event isn't JSON-serializable. The event is a
  `multiprocessing.Event` (not `threading.Event`): a `ProcessPoolExecutor`
  worker on macOS's "spawn" start method shares no memory with the parent,
  so cancellation is only passed to workers via the pool's `initializer`,
  never as a normal argument to `submit()`.

### Data & git hygiene

- Gitignored, generated/regenerable directories that must never be
  committed directly: `CORPUS/` (raw per-source sentence cache), `DICS/`
  (raw Wiktionary/Kaikki dumps), `GRID_SVG/`, `GRID_PNG/`, `LOG_LLM/`,
  `models/` (LLM GGUF weights, auto-downloaded by `run_llm.sh`), `data/
  hunspell_cache/`, and `data/reference_corpus/` (the uncompressed sentence
  corpus, ~1GB+ of raw text). If one of these ever shows as staged/committed
  by mistake and hasn't been pushed yet, undo with a plain `git reset
  HEAD~1` (uncommits without touching the working tree) rather than a
  history rewrite.
- `data/gloss_dictionary/<lang>_glosses.jsonl` **is** committed (a few tens
  of MB total) — small enough to ship directly and load-bearing at runtime
  (the "easy"-difficulty gloss filter, LLM clue grounding), unlike the much
  larger raw corpus/dump caches above.
- `data/reference_corpus_<lang>.tar.xz` (one archive per language,
  committed) is a fast-path `Install.sh` can unpack instead of re-running
  `build_sentence_corpus.py` from scratch. GitHub enforces a **hard** 100MB
  per-file limit (not just a soft warning) — that's why this is split per
  language rather than one combined archive. When rebuilding one of these
  archives, compress by piping through the real `xz` CLI directly
  (`tar -cf - -C data reference_corpus/<lang>_sentences.txt | xz -9e -T0 >
  data/reference_corpus_<lang>.tar.xz`), **not** `tar -cJf ... ` with
  `XZ_OPT` — on a machine whose `tar` is `bsdtar`/libarchive (common on
  macOS), `XZ_OPT` is silently ignored by libarchive's built-in `-J` filter,
  producing a materially worse (and on at least one language, over-the-limit)
  compression ratio with no error or warning.
- `GRID_SAMPLES/` (project root) **is** committed and the app never writes
  to it automatically — it's a small, hand-curated selection of example
  grids, populated only when someone deliberately picks a grid and adds it.
  `GRID_PNG/`/`GRID_SVG/` (gitignored) hold every generated grid instead.

### Data pipeline (corpus → wordlist → gloss dictionary)

- `build_sentence_corpus.py` builds each language's corpus from 5 OPUS
  (opus.nlpl.eu) sources — OpenSubtitles, Wikipedia, Books, TED2013,
  CCMatrix — via a partial, resumable per-source download
  (`--max-bytes`), merged and filtered for language purity (a Hunspell-based
  check: reject a sentence with a contiguous run of `MAX_INVALID_RUN` (3+)
  unrecognized words, or too high an overall invalid-word fraction). Only
  sentences between `MIN_WORDS_PER_SENTENCE` (5) and `MAX_WORDS_PER_
  SENTENCE` (50) words are kept. Output:
  `data/reference_corpus/<lang>_sentences.txt`; each source's own raw
  sentences are cached under `CORPUS/` so a reprocessing pass doesn't
  re-download from opus.nlpl.eu.
- `build_wordlist_freq.py` writes `data/wordlist_<lang>_full.tsv` as
  `MOT<TAB>ACCENTUE<TAB>FREQUENCE<TAB>CANONIQUE` — the bare accent-stripped
  uppercase grid form, its natural accented/inflected spelling, a blended
  frequency score, and every candidate canonical form/lemma
  (semicolon-separated when ambiguous). Minimum word length is 2 (a
  2-letter word is a real, cluable grid slot; a bare 1-letter word can never
  become a slot at all). Every candidate is validated against a Hunspell
  dictionary for its own language (both as-is and title-cased, since German
  requires capitalized nouns) via the real `hunspell` CLI spellchecker
  (never `unmunch`, which silently drops many irregular verb conjugations).
  `FREQUENCE` blends `CANONICAL_WEIGHT` (0.9) × the most-frequent canonical
  form's own frequency + 0.1 × the word's raw frequency, correcting
  subtitle/dialogue-frequency distortion. A likely proper noun (detected via
  the same as-is-vs-title-cased Hunspell signal, in fr/en/es/it only — not
  German, where every noun requires capitalization regardless) has its
  final frequency multiplied by `PROPER_NOUN_SCORE_FACTOR` (0.5).
- `build_gloss_dictionary.py` downloads each language's full Wiktionary
  extract from Kaikki.org — the *own-language* Wiktionary edition for
  fr/de/es/it (not the primary English-Wiktionary-sourced extraction, which
  gives English glosses for every language) — filters it down to the lemmas
  `CANONIQUE` actually needs, and writes
  `data/gloss_dictionary/<lang>_glosses.jsonl`. Raw dumps are cached under
  `DICS/` so a later rebuild re-filters instead of re-downloading several
  gigabytes per language.
- `backend/gloss_lookup.py`/`backend/example_sentences.py` each lazily
  build and cache their index once per process lifetime (not per request):
  gloss lookup is keyed by canonical form(s); example-sentence lookup is
  keyed by the exact accented/inflected spelling (the corpus isn't
  accent-stripped) — using the wordlist's bare `MOT` column instead would
  silently return zero examples for every accented word, a real bug fixed
  once already.
- `DIFFICULTY_PRESETS` (`backend/crossword_gen.py`) are *fractions* of each
  language's own gloss-filtered lexicon, not fixed word counts: easy=0.66,
  medium=0.80, hard=1.0 (uncapped). A fixed count doesn't have a comparable
  effect across languages with very different vocabulary sizes (e.g.
  French ~127k words vs. German ~436k due to heavy compounding). "Easy"
  additionally requires a findable gloss (`require_gloss=True`) — the
  fraction is resolved *after* that filter already dropped undefinable
  words, so a language's own gloss coverage affects what "easy" actually
  means for it.

### Crossword generation algorithm (`backend/crossword_gen.py`)

Full mechanism-level detail and iteration history live in `CLAUDE.md`
(current state) and this project's own file history — the facts below are
the current defaults/behavior to know before touching this code.

- Black-cell placement is **not** 180°-symmetric (dropped in favor of
  independent, non-paired placement, which reaches sparser valid patterns).
- `is_structurally_valid`: an *interior* white zone (black cells on both
  sides) must be at least 3 cells long; a zone touching the grid's own
  border on at least one side is unrestricted in length or count. One
  invariant is absolute, never relaxed: a white cell can never be short
  (1 letter) in *both* directions at once (fully isolated on all 4 sides).
  A 1-letter zone is a pure passthrough, never its own slot; a 2-letter zone
  *is* a real, cluable slot.
- A pre-fill phase runs before ratio-based placement, adding black cells
  (never counted against the ratio target) until every slot has at least
  `PREFILL_MIN_WORD_COUNT` (10) real dictionary candidates — locked-letter
  aware, so a slot partially fixed by letters carried over from a previous
  palier is checked by real per-position candidate count, not just raw
  length. A preventive filter (`_new_black_cell_breaks_locked_slot`) refuses
  any new black cell that would drop a locked-adjacent slot below this
  threshold; a repair pass re-runs pre-fill afterward as a safety net.
- The black-cell ratio starts at 0.0 and does **not** escalate across
  paliers — pre-fill plus the cross-palier retry mechanism below are
  sufficient to make progress without artificially densifying the grid.
  A flat, non-escalating `POST_PREFILL_BLACK_FRACTION` is layered on top of
  pre-fill. The web UI exposes this as `black_enrichment_percent` — a
  free-text integer field (0-100, `GenerateRequest.Field(ge=0, le=100)`,
  default 14), statically initialized (no longer auto-computed from grid
  size — an earlier `round(0.3 * sqrt(width * height))` formula was
  dropped at the user's explicit request in favor of this fixed default) —
  this setting only ever applies to a palier that starts from a
  blank/cleaned-up grid, never to a "continue verbatim" palier (see
  below), which never calls `make_pattern` at all. The fraction is
  computed on the white-cell count **before** pre-fill runs (`make_pattern`'s
  `initial_white_count`), and pre-fill's own placed cells now genuinely
  count toward that target: `target = max(placed, black_ratio_floor,
  round(fraction * initial_white_count))`, `placed` already including
  whatever pre-fill placed — reversed at the user's explicit request from
  an earlier version where the fraction was computed on the cells still
  white right *after* pre-fill and added *on top* of `placed`
  unconditionally (so pre-fill's own cells never counted toward it). If
  pre-fill alone already placed more cells than the target percentage of
  the *original* white-cell count calls for, no further cells are added
  for this reason at all.
- `PARALLEL_ATTEMPTS` (default 10, see env vars above) independent attempts
  run concurrently per palier via `ProcessPoolExecutor`; `attempts` (paliers)
  defaults to 200 (raised from an original 40 — some grids need many quick,
  unproductive cycles before a workable state emerges).
- **Cross-palier retry**: when a palier's search fails, if the best failed
  attempt still has an unassigned slot that is neither impossible nor
  crossing an impossible one (`_slots_touching`, shared by both this check
  and `Filler._crossing_excluded_slots` below — a slot crossing a known-
  impossible one must count as "no hope" too, since it will never actually
  be attempted; folding it into `still_has_hope` only came after a first
  version without it caused a total generation failure, see below), **and**
  fewer than 50 "continue" paliers have already run consecutively since the
  last nettoyage (`consecutive_continue_paliers`, cap raised from 5 to 10
  to 50 at the user's explicit request; reset to 0 on every real
  nettoyage — a hard cap independent of `still_has_hope`'s own correctness,
  since that check can legitimately stay `True` for a long streak even when
  no real progress is happening palier to palier), its
  exact grid is carried forward *verbatim* to the next palier
  (`_pattern_continue`, never calling `make_pattern` again, never reopening
  a black cell) with the known-impossible slot(s) excluded from the search
  entirely — but not every already-filled cell stays locked: any word
  crossing one of those impossible slots is stripped first
  (`_clean_blocked_slots`, the same helper `_build_retry_seed` itself now
  calls internally — see below), freeing its cells back up for the next
  palier's own search rather than keeping them locked on a word that has
  no chance of surviving anyway. Only once no slot can be usefully added
  does the alternative path run (`_build_retry_seed`): the same
  `_clean_blocked_slots` step, followed by reopening black cells that
  neither bound a surviving word nor sit between two confirmed letters on
  the same axis (an isolated-hole check also prevents reopening a cell
  fully surrounded by black cells) — and generate a fresh pattern from
  that state. Both paths (continue verbatim *and* full nettoyage) then
  additionally lock in **one** new black cell — exactly one per palier,
  never one per cleaned candidate — chosen at random among the cells that
  belonged to any impossible slot of the winning grid, via the shared
  helpers `_impossible_cell_groups`/`_lock_one_impossible_cell` — at the
  user's explicit request ("tenter de ne pas reproduire les mêmes erreurs
  en verrouillant progressivement les configurations problématiques").
  Originally full-nettoyage-only; extended to the continue-verbatim path
  too at the user's own explicit follow-up ("il faut ajouter une case
  noire à tous les tours où on nettoie les emplacements injouables, pas
  seulement quand on nettoie aussi les cases noires") — this is the one
  and only exception to that path's "never touches a black cell" rule.
  Candidate cells are split into two groups — still blank (no letter) vs.
  already carrying a letter from a crossing assigned word — computed from
  the *raw*, pre-cleanup assignment (before `_clean_blocked_slots` strips
  every crossing word), since post-cleanup *every* impossible-slot cell is
  blank without exception, which would make the distinction meaningless
  if computed any later — at the user's own explicit follow-up precision
  ("ajouter la case noire avant de nettoyer... privilégier de noircir une
  case blanche, sinon une case avec une lettre"). The blank group is tried
  first if non-empty (`list(blank) or list(lettered)`), shuffled with the
  palier's own seeded `rng` and tried in order, skipping any that would
  break `is_structurally_valid(min_interior_free=1)` (the absolute
  connectivity/no-orphaned-cell invariant), falling back to the next
  candidate within the same group; if none in the chosen group validate,
  no cell is added (accepted edge case — the other group is not tried as
  a further fallback). On the continue-verbatim path specifically, since
  this lock can split/shorten the targeted slot, every subsequent slot
  index in `extract_slots`'s own row-then-column scan order can shift —
  `carry_preseed_assignment`/`carry_excluded_slots` are therefore rebuilt
  from scratch against a *fresh* `extract_slots` call on the just-locked
  grid (matched by cell tuples via the cell-based `confirmed` dict
  `_clean_blocked_slots` already returns, never carried over from the
  pre-lock slot list) rather than reused at their old, potentially
  now-wrong indices — a real bug caught by code review before it was ever
  run live, not from a reported failure. The full-nettoyage path has no
  such concern: it only ever threads cell-keyed data (`locked_letters`) to
  the next palier, never a position-indexed list, so nothing needed
  rebuilding there. Both paths dedupe the palier's parallel outcomes by
  (pattern, assignment) before counting/selecting from them. "Best failed
  attempt" (`failed_
  pairs[0]`) means fewest cells belonging to an impossible slot
  (`impossible_cells`), not fewest black cells. "Best cleaned candidate"
  (chosen after nettoyage, among the 6 candidates) means the highest
  `_words_in_place_score` — sum of `length ** 2` over every slot whose
  *every* cell is confirmed after cleanup (a few long confirmed words
  outweigh many short ones for the same letter total; a partially-
  confirmed slot scores 0) — not the raw confirmed-letter count. `still_has_hope` is also forced to
  `False` (nettoyage instead of continue) whenever every one of the
  palier's `PARALLEL_ATTEMPTS` raw outcomes has `reason ==
  "abandoned_too_unfillable"` (see below) — if every worker independently
  gave up on its own pattern as too far gone, continuing "telle quelle" on
  it isn't worth trying.
- `Filler._backtrack` abandons a search attempt early — `self.abandoned =
  True`, every later call returns `False` immediately — once more than
  `UNFILLABLE_ABANDON_FRACTION` (30%) of the grid's white cells belong to
  a slot deemed impossible (`impossible_zone_cells()` against `best_
  assignment`), checked every `UNFILLABLE_ABANDON_CHECK_INTERVAL` (500)
  calls (not every call — recomputing domains for every unassigned slot
  has a real cost). Surfaces as `try_fill`'s `reason ==
  "abandoned_too_unfillable"`, checked ahead of `deadline_exceeded`/
  `blocked_on_excluded_slot`/`search_exhausted`. A one-attempt-making-
  zero-progress-then-recovering-next-palier pattern is normal, observed
  behavior of this whole mechanism, not itself a bug — verified live by
  reproducing the identical transient stall with the pre-window-of-10
  tier rule on the same seed before either of these two rules existed.
- On a `_pattern_continue` ("reprise telle quelle") palier only, the
  moment any one of its `PARALLEL_ATTEMPTS` parallel workers abandons
  itself this way, every *other* worker in that same palier stops too,
  rather than each running to its own independent abandon/deadline —
  `_worker_batch_abandoned_event` (a `multiprocessing.Event`, created once
  per `generate_grid()` call, passed to every worker via the pool's
  `_init_worker` initializer like `index`/`cancel_event`, never as a
  per-task argument — the same macOS "spawn" pickling restriction that
  already forced `cancel_event` through the initializer applies here too).
  Unlike `cancel_event` (set once for the whole generation), this one is
  `.clear()`ed by the parent at the start of every palier, since a
  blockage at palier N must never carry over and affect palier N+1's own
  attempts. `Filler._backtrack` both checks it (same
  `UNFILLABLE_ABANDON_CHECK_INTERVAL` cadence, sets its own `self.abandoned
  = True` and returns `False` if set) and sets it (right when its *own*
  30% rule fires) — so one worker's abandon becomes every sibling's
  `reason == "abandoned_too_unfillable"` within one check interval,
  without waiting for each to independently reach 30% or its own
  `deadline_checks` budget. **Deliberately never wired into
  `_pattern_attempt`** ("motif neuf" paliers) — `_pattern_attempt` always
  passes `batch_abandoned_event=None` to `try_fill`, regardless of the
  worker-global being set, so this mechanism is a structural no-op there.
  This is load-bearing, not a stylistic choice: `_pattern_continue`'s own
  `PARALLEL_ATTEMPTS` workers all search the exact same shared pattern
  (only their exploration order differs), so one worker's "30% impossible"
  finding really does generalize to its siblings — but `_pattern_attempt`'s
  own workers each build their *own* independent random pattern via
  `make_pattern` (same starting `seed_grid`/`locked_letters`, different
  new black cells laid down by each worker's own `rng`), so one worker's
  bad luck on its own pattern says nothing reliable about a sibling's
  differently-shaped one. Sharing the signal between them regardless was
  tried and caused a real, reproduced regression on the standard 15×10
  benchmark's seed 7 (previously reliable, failed outright — `None`
  returned after exhausting all paliers) before being scoped back to
  `_pattern_continue` only, which restored the seed's success.
- `try_fill`'s `deadline_checks` default is `rows * cols * 2000` (see
  above) only when the caller passes `None` — the web UI's "Mode"
  selector (`backend/app.py`'s `BUDGET_MODES`: flash=1,000, turbo=10,000,
  fast=100,000, medium=500,000 [default], ultra=5,000,000) instead sends
  an explicit value, threaded through `generate_grid(deadline_checks=...)`
  → `_pattern_attempt`/`_pattern_continue` → `try_fill`, overriding the
  grid-size formula entirely for that request. The CLI and any other
  caller that never sets it keeps using the formula, unaffected.
- `Filler.exclude_immediately_impossible_slots()` (called once in
  `try_fill`, right before `solve()`) excludes any still-unassigned slot
  whose domain is already empty given *only* fixed constraints (locked
  letters, no search decision made yet) — such a slot's emptiness can
  never change during this search, so leaving it in `unassigned` would
  make `_backtrack`'s own domain-check fail on literally every call
  (`checks=1`), long before the search gets any chance to fill the rest of
  an otherwise fillable grid. Folds into the same `excluded_slots`/
  `_crossing_excluded_slots` machinery used for a slot already known
  impossible from a previous palier — one pass suffices, since excluding a
  slot never changes any other slot's own domain.
- `Filler` never attempts to fill a slot that crosses one already in
  `excluded_slots` (`_crossing_excluded_slots`, computed once in
  `__init__` via `_slots_touching`) — a word placed there would just get
  stripped back out by the next cleanup anyway (see above), so it's not
  worth the search budget. Only has any effect once `excluded_slots` is
  non-empty (a "continue" palier); harmless otherwise. This must stay in
  sync with `still_has_hope`'s own use of `_slots_touching` above — a
  first version that added this exclusion without also updating
  `still_has_hope` caused every generation to fail outright (200 paliers
  exhausted, stuck reusing the same pattern forever, since the newly
  unfillable crossing slot(s) never counted as "no hope" either).
- `Filler._backtrack`'s slot-selection is a 2-tier rule, with **no MRV/
  domain-size override** — this is a deliberate, user-confirmed choice, not
  an oversight: (1) alternate across/down, weighted by how many free slots
  remain in each direction; (2) compute `int(100 * placed_letter_count /
  length ** 0.5)` for every slot in that direction, shuffle the pool (the
  attempt's own seeded `rng`) then sort by score descending, and draw
  uniformly at random among the **top `max(5, int(len(direction_pool) /
  10))`** — recomputed fresh at every `_backtrack` call (not cached on
  `Filler`, unlike an earlier grid-size-based version of this same window:
  `direction_pool` shrinks as the search fills in slots, so a size derived
  from it can't be computed once up front) — so the window scales with how
  many slots are still open in the drawn direction right now, down to a
  floor of 5 once few remain, rather than with the grid's fixed overall
  size. The shuffle-before-sort avoids a positional bias among slots tied
  at the window's own cutoff (`sorted` is stable, so without a prior
  shuffle the original list order would decide which tied slot falls just
  inside vs. just outside the window). The ×100 scaling in the score is
  load-bearing: without it, `int(placed / length**2)`, an earlier version
  of this formula's denominator, was provably 0 for every slot, since a
  slot's placed-letter count can never exceed its own length. This exact
  criterion has changed eight times at the user's explicit request:
  "fewest remaining free cells" (window of 10) → "most already-placed
  letters" raw count (window widened to 30) → placed/length ratio (window
  narrowed to 15) → exact-tie score with `length ** 2` as the denominator
  (no window at all) → the same exact-tie score with `length ** 0.5`
  (sqrt) as the denominator instead (no window at all) → a fixed window of
  10 on top of that same sqrt-based score → a window of
  `int(sqrt(rows * cols))` (grid-area-based) → the current
  `max(5, int(len(direction_pool) / 10))` (free-slot-count-based).
  Squaring the earlier `length ** 2` denominator penalized a long slot's
  own score heavily regardless of how advanced it was; `sqrt(length)`
  fixed that (a 12-letter slot 8-placed used to score 5, now scores 230,
  ahead of a
  3-letter slot 2-placed's 115 either way — see CLAUDE.md for the worked
  comparison) — the window size itself is a separate, later change on top
  of that fix, unrelated to the denominator. MRV was removed because the
  cross-palier retry mechanism makes "fail fast on the most constrained
  slot within one monolithic attempt" the wrong model. Reinstating it was
  tried once, to fix a real, still-open symptom — the very first palier (a
  blank grid, nothing crossed yet) can fill sparsely, because nothing
  distinguishes candidates by real dictionary-candidate count when nothing
  is placed yet — but the user explicitly rejected bringing MRV's absolute
  priority back as the fix. Do not reinstate it; the sparse-first-palier
  symptom needs a different fix. The 2-tier rule (current: free-slot-count-
  proportional window score, see above) makes the standard 15×10 benchmark
  noticeably *faster* than the previous 4-tier cascade. A restricted, artificially small (400-word)
  dictionary stress
  scenario, used earlier in this project's history to exercise the
  cross-palier retry mechanism, is **not a valid test case for this
  project anymore** — the user explicitly retired it ("on n'utilisera
  jamais un dictionnaire aussi petit"), since real deployments always use
  the full per-language wordlist (tens to hundreds of thousands of words).
  Do not use a dictionary anywhere near that small to stress-test this
  area again; use the full wordlist at a reduced grid size instead if a
  harder scenario is needed.
- Candidate *word* order within a chosen slot is also statistically
  informed: `sample_letter_biases` runs unconditionally on every pattern
  attempt (100 random same-length words per slot, no cross-validation),
  producing both a small set of forced-letter hints and a full per-cell
  `letter_scores` tally. Candidates are ranked by a sum-of-squares score
  against `letter_scores`, then drawn via a 20-word look-ahead window
  (random among the top 20 remaining, not a strict rank order) — this
  ranking is always active, independent of whether letter-forcing itself is
  on.
- "Graines" (French UI label; internally still `forced_letters`/
  `force_letters_percent` in code — renamed in the UI only, at the user's
  explicit request, from "Lettres forcées"/"Forced letters") are a
  separate, UI-configurable option (`force_letters_percent`, a free-text
  integer field 0-100, `GenerateRequest.Field(ge=0, le=100)`, static "1"
  default) — at most one seed per slot, drawn at random among eligible
  candidates (not the statistically strongest one, to avoid always forcing
  the same dominant letter), each needing `LETTER_BIAS_MIN_COUNT` (10)
  occurrences out of the 100-word sample to be eligible at all. A seeded
  cell counts as an already-known letter for `Filler._placed_letter_count`
  (and hence for the tier-2 selection score above) — without this, a seed
  on an otherwise-blank grid had no selection priority at all (every slot
  tied at a score of 0), so the search had no structural reason to ever
  place a word on it first, defeating the seed's whole purpose. A seed is
  also never placed on a slot already known impossible (`sample_letter_
  biases`'s `excluded_slots` parameter) — `_pattern_continue` passes its
  own `excluded_slots` straight through, and `_pattern_attempt` computes
  which fully-locked slots have no real matching word
  (`locked_impossible_slots`, a by-product of the existing preseed-
  assignment validation, reordered to run before the seed sampling instead
  of after) and passes that. `letter_scores` (candidate-word ranking)
  stays fully populated for an excluded slot's own cells regardless — only
  `forced` (the seed itself) is restricted.
- `minimize_black_squares` (after a successful fill) only ever removes
  black cells, one at a time, keeping a removal only if the grid stays
  fillable and structurally valid at `min_interior_free=1` (not the
  generation-time default of 3) — it only needs to preserve connectivity
  and the no-orphaned-cell invariant, not `make_pattern`'s own aesthetic
  preference.
- A cooperative `GenerationCancelled` mechanism (checked at palier
  boundaries, inside `Filler._backtrack` every `CANCEL_CHECK_INTERVAL`
  (500) calls, inside `minimize_black_squares`'s removal loop, and between
  words during clue generation) backs the web UI's "Stop" button — it never
  force-kills a worker process, only stops at the next natural checkpoint.

### LLM clue generation (`backend/clues.py`)

- `LLMClueGenerator` owns all LLM handling (endpoint config, prompt text,
  the HTTP call, response parsing); `backend/app.py` builds one instance at
  module scope. Talks to any OpenAI-compatible chat-completions endpoint —
  the local `llama_cpp.server` (`run_llm.sh`) by default, or a cloud API
  (e.g. Mistral) via `env.sh`, no code change needed either way.
- One word per LLM call (`_BATCH_SIZE = 1` — even a small batch degraded
  reliability on small local models); up to 3 immediate, consecutive
  retries per word before giving up. `llama_cpp.server` has no
  parallel-request/continuous-batching support at all — client-side
  concurrency does not speed this up, don't re-attempt it without first
  addressing that server-side limitation.
- The model is asked for exactly 3 plain lines, one candidate clue per
  line, nothing else — no header, no delimiter syntax for it to get wrong.
  `_pick_clue()` filters candidates for: length (`MAX_CLUE_WORDS = 20`),
  non-Latin script, containing the target word/accented spelling/any known
  canonical form (`_contains_target_word` — deliberately not a full
  morphological-family filter, since a fundamental verb like "être" would
  become nearly unclueable under one), a leaked word-label prefix, and a
  wrong-language stopword check (`_detect_wrong_language`, using
  per-language stopword lists with cross-language-ambiguous words
  programmatically removed from all lists).
- Prompt content is split: structure/rules/explanatory prose lives in
  `backend/clues.py` itself (English, this project's engineering language);
  the concrete per-language worked examples (agreement examples, rule
  bad/good illustrations, difficulty-style examples, subject pronouns) live
  in `data/<lang>_prompt_config.json` (`rule_bad`/`rule_good` flat lists,
  not one list per rule number — different languages need different
  numbers/shapes of illustration). Each of the 5 languages' examples is
  authored to fit that language's own grammar, not a French template forced
  onto it.
- Grammatical agreement (person/number/mood/tense/gender matching the
  target word) has no code-level filter — it needs real per-language
  parsing of the clue text, which isn't attempted. This remains a known,
  accepted small-model reliability ceiling, most visible on high-frequency
  irregular verbs (French "être" especially) — prompt-only mitigations
  (explicit rules, worked examples) help but don't fully solve it.
- Grounding: `_build_gloss_block`/`_build_examples_block` append real
  dictionary definitions and real usage sentences to the prompt when
  available, keyed as described in the data-pipeline section above; both
  sections are omitted when nothing is found, and the model is told to
  treat multiple genuine senses as an opportunity for variety across its 3
  candidates rather than collapsing to one.
- Every LLM call (success or failure alike) writes its own diagnostic file
  under `LOG_LLM/` (gitignored), named `<timestamp>_<answer>_SUCCES.md` or
  `_ERROR.md`: the full system+user prompt, the raw LLM output, and every
  candidate's verdict (selected / accepted-not-selected / rejected +
  reason).
- A word that exhausts all 3 retries never falls back to showing the bare
  answer as its own definition — both `frontend/static/script.js`'s
  `renderClueLines()` and `backend/svg_export.py`'s `_group_clue_lines()`
  show a translated "no definition available" placeholder instead.
- `generate()` accepts a `cancel_event`, checked once per word before that
  word's own retry rounds start (see the cancellation mechanism above).
- **Default local model: Qwen3.5-0.8B** (bf16, unquantized) — chosen
  deliberately so a fresh checkout can generate a clue end-to-end on CPU
  alone, with no GPU required. It is not the fastest option in practice on
  every machine (this project's own system prompt is unusually long, making
  prompt processing rather than generation the dominant per-call cost, so
  "smaller" doesn't reliably mean "faster" here) — verify with your own
  hardware before assuming otherwise. Supported alternatives, all
  configured via the same `env.sh`/`env_default.sh` override mechanism
  (`LLM_MODEL`/`LLAMA_GGUF_REPO`/`LLAMA_GGUF_FILE`/
  `LLAMA_CHAT_TEMPLATE_KWARGS`, all four kept in sync as one group): Qwen3.5
  at 2B/4B (bf16)/9B (Q4_K_M), Qwen3.8-27B (Unsloth `UD-Q2_K_XL` — the
  strongest observed clue-agreement quality of any model tried, at
  ~20-40s/word; recommended in `README.md` for a GPU with at least 12GB
  VRAM), and a cloud API (e.g. Mistral). Qwen3-14B and DeepSeek-R1-Distill-
  Qwen-14B were evaluated and are no longer offered.
- Every Qwen3/Qwen3.5 GGUF used here is a hybrid thinking model whose chat
  template reads an `enable_thinking` flag — `run_llm.sh` always launches
  with `--chat_template_kwargs '{"enable_thinking": false}'`; without it,
  the model burns its token budget on a `<think>` block instead of
  answering. Check for the same flag/failure mode before adding any new
  reasoning-capable model.

### Documentation

- `CLAUDE.md` is the technical reference for how the codebase currently
  works (architecture, API, algorithm/prompt behavior) — this SKILL stays
  focused on project-management conventions and current-state facts
  organized by topic, not a duplicate of `CLAUDE.md`'s own detail.
- `DOC_ALGO/FR/ReadMe.md` is the French, user-facing explanation of the
  grid-generation algorithm — keep it in sync with `backend/crossword_gen.py`
  per permanent rule 11.
- `README.md` stays non-technical (permanent rule 5); anything about
  implementation, JSON formats, or internal module behavior belongs in
  `CLAUDE.md` instead.
- Code and code comments are written in English going forward (permanent
  rule 14) — a change from this project's own long-standing practice of
  French inline comments throughout `backend/crossword_gen.py` in
  particular, kept as-is rather than retrofitted.
