---
name: project-best-practices
description: Project management rules for CrossWordFalcon, applied and kept up to date automatically — decision log in this SKILL, requirements.txt synced with installed packages, Install.sh synced with the install procedure, README.md synced with the project's features. Invoke before/after a structuring project decision, a package install, any change to the install procedure, or adding/changing a feature.
---

# Best practices — CrossWordFalcon project

This SKILL is the living memory of this project's management rules. It must
stay current on its own — don't wait to be asked.

The project's official language is English: code identifiers, comments,
this SKILL, and README.md are written in English. User-facing product
content (the crossword words/clues in all 6 supported languages, the web UI
text) stays in the relevant language — that's the app's domain, not the
project's engineering language.

## Permanent rules

1. **Keep this SKILL current, not historical — and this applies just as
   absolutely to `CLAUDE.md`, `DOC_ALGO/FR/ReadMe.md`, `DOC_DIC/FR/
   ReadMe.md`, and `DOC_USER/EN/ReadMe.md`** (see rules 11, 18, 19, 20 for
   the three `DOC_*` files individually). All five of these documents are
   timeless references meant to let a reader understand the *current*
   state of the code and the project without needing to read the code
   itself — never notes about the history of the decisions that led
   there. Whenever an important project-management decision is made
   (architecture choice, convention change, scope decision, tooling
   choice, etc.), update this SKILL's "Decisions" section (and,
   symmetrically, whichever of the other four documents covers the
   affected area) to reflect the new state — as a present-tense fact ("X
   does Y", "the default is Z"), never as a narrated change ("X was
   changed from A to B because...", "at the user's explicit request",
   "found live", "previously..."). None of these five documents is a
   changelog: when a new decision supersedes an old one, replace the old
   fact in place instead of appending a new entry next to it. Drop a fact
   entirely once it no longer describes the current codebase, rather than
   keeping it as history.

   `CLAUDE.md` was, for a long stretch of this project's history,
   deliberately exempt from this rule — the one place the narrative (the
   *why* and the *how it came to be*, including reverted experiments and
   bug-fix war stories) was allowed to accumulate. That narrative grew the
   file to ~22,000 lines, at which point it was saturating every
   conversation's own context on its own (the file is loaded in full,
   automatically, at the start of every session) — the user then had it
   rewritten from scratch as a compact, current-state-only technical
   reference (architecture, API surface, algorithm/prompt behavior),
   explicitly discarding the narrative rather than archiving it elsewhere
   (git history still has the full old version, at the commit right
   before this rewrite, if a past decision's exact reasoning is ever
   needed). **The exemption is gone — `CLAUDE.md` is now held to the
   exact same current-state-only discipline as the other four documents,
   permanently.** Do not let it regrow into a changelog: when documenting
   a change to `CLAUDE.md`, replace the affected fact in place, and
   resist the pull (especially strong for a fix motivated by a subtle,
   hard-won bug) to also narrate *why* or *how it was found* — that
   reasoning either becomes a one-clause justification woven into the
   present-tense fact itself (when it's load-bearing enough that a future
   reader needs it to avoid repeating the mistake) or it doesn't belong
   in this file at all.

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
   before doing anything else with it, without waiting to be asked. The
   same holds for `backend_java/` changes and a Java back end
   (`./run_FalconJ.sh`, which also rebuilds the jar) — restart whichever
   back end version is currently running, never silently switch versions.
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
   **Always re-read `VERSION.txt` immediately before writing the new
   value** — never bump from a number remembered earlier in the session.
   The user edits this file by hand between turns; overwriting it with
   `<remembered> + 1` can silently undo a manual bump or move the version
   backward. Read the current value, then increment that.

10. **Keep `frontend/static/i18n.js` current with every UI-visible string
    change** — any label, button, heading, status/progress message, or
    error message added, changed, or removed anywhere in `frontend/static/`
    or in a backend/proxy error path needs its `I18N` entry (or
    `describeStep()`/`describeErrorCode()` case in `script.js`) updated for
    **all six** supported languages (fr/en/de/es/it/pt) in the same change —
    never just the language you happen to be testing in.

11. **`DOC_ALGO/FR/ReadMe.md` is a timeless reference, not a decision
    log.** Its whole purpose is to let a reader understand the *current*
    version of `backend/crossword_gen.py`'s algorithm without having to
    read the code — never an inventory of past decisions. Whenever the
    grid-generation algorithm changes, update it to describe the new
    current behavior directly (present tense, as if it had always worked
    this way) — never append "à la demande explicite de l'utilisateur",
    "précédemment", "a été essayé puis abandonné/reverti", a changed-N-times
    account, "Vérifié en direct...", a bug report/incident narrative (a
    user quote, a screenshot reference, a root-cause trace, before/after
    measurements), or any other trace of *how* the current state was
    reached. That narrative doesn't belong in any maintained document —
    git history is where it lives if ever needed. Both this file and
    `CLAUDE.md` must stay in sync on *current* algorithm facts (dual-write
    the fact, narrate in neither). If a change makes an old explanation
    wrong, replace it in place rather
    than layering a correction on top. Found live drifting from this rule
    despite it already existing: small, single-fact edits (bump a
    constant, change a formula) kept slipping in a one-clause "à la
    demande explicite de l'utilisateur" aside each time — harmless in
    isolation, but compounding turns the file back into a changelog one
    small violation at a time. A large corrective pass then had to strip
    accumulated narrative across the whole file, including a ~500-line
    block that was pure bug-fix history for the live-preview/diagnostic
    display mechanism (screenshots, quoted reports, root-cause traces,
    "vérifié en direct" measurements) — compressed down to a few
    paragraphs of what that mechanism currently does. Treat every edit to
    this file as an opportunity to re-check the *surrounding* paragraph
    for drift too, not just the sentence being changed.

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

14. **Code and code comments must be written in English, with no
    exception for pre-existing content — every source file of every kind
    must actually be converted, not just new comments going forward.** At
    the user's explicit, repeated request. Applies to EVERY source file in
    the repository regardless of extension — not just `.py`/`.js`/`.css`/
    `.html` (the first pass of this cleanup only searched those four and
    missed the project's own `.sh` shell scripts, `Install.sh` in
    particular, until the user pointed it out directly) — and their inline
    comments/docstrings; it does not apply to product content the app
    itself displays (crossword words, clues, UI labels — see this SKILL's
    own convention of English code identifiers with translated UI text),
    to a CLI's own user-facing `help=`/`print()` text, or to reports/
    summaries sent to the user, which stay in French (see the user's own
    persistent instruction on report language). This rule originally
    carried a "going forward, not retrofitted" exemption for this
    codebase's existing French comments (`backend/crossword_gen.py` most
    of all, with an extensive history of them). The user found that
    exemption kept letting French narrative slip back into fresh edits of
    an already-French comment block (new text added in the same
    paragraph's existing language, to stay stylistically consistent with
    it) and revoked it outright, asking directly for every remaining
    French comment/docstring across the whole codebase to be found (grep
    for French marker phrases such as "la demande") and converted — then,
    once the `.py`/`.js`/`.css`/`.html` pass was believed complete, found
    a further gap in `Install.sh` that the extension-scoped search had
    never even looked at. Whenever checking whether this rule is
    satisfied, search the entire repository by content, not by a fixed
    extension list — a new source-file type (a `.sh` script, a `Dockerfile`,
    a config format that supports comments, etc.) is in scope the moment
    it exists, with no separate decision needed to include it. Check for
    this whenever editing a file that still has French comments nearby —
    don't let a new edit's own language choice be dictated by an
    old paragraph's, and treat a French comment noticed anywhere in a
    touched file as a defect to fix, not a pre-existing condition to
    leave alone.

15. **Every new `backend/app.py` endpoint needs a matching proxy route in
    `frontend/server.py`, added in the same change** — `frontend/server.py`
    has no generic passthrough, only a small hand-written list of proxy
    routes (`proxy_generate`/`proxy_generate_status`/`proxy_generate_
    cancel`/etc.); a backend route with no matching proxy silently falls
    through to the catch-all `StaticFiles` mount at the end of that file,
    which only serves `GET`/`HEAD` — so a browser request to it fails with
    a confusing 405 "Method Not Allowed" instead of an obviously
    backend-shaped error. Found live exactly this way when `POST /api/
    generate/continue/{job_id}` shipped without its own proxy route (see
    CLAUDE.md's `frontend/server.py` entry for the full incident) — check
    for this whenever a new backend endpoint is added.

16. **`DOC_ALGO/FR/ReadMe.md` must always be tracked in git — never
    gitignored.** Found live: `/DOC_ALGO/` had been listed in `.gitignore`
    (alongside genuinely generated/cache directories like `CORPUS/`,
    `DICS/`, `GRID_SVG/`) even though this specific file is a hand-
    maintained reference doc, updated on the same footing as `CLAUDE.md`/
    `README.md` (see permanent rule 11) — every edit to it was silently
    invisible to `git status`/`git diff`, so a real update could easily
    look, from the outside, like it had never happened. Fixed by removing
    the `/DOC_ALGO/` line from `.gitignore` and committing the file for
    the first time. Check this whenever a new top-level directory is added
    to `.gitignore`: only genuinely generated/downloaded/cached content
    belongs there, never a maintained source document.

17. **Run verification/benchmark `generate_grid()` calls in "Flash" mode
    (`deadline_checks=1000`) to go faster, unless the task at hand
    specifically needs to exercise another mode.** At the user's explicit
    request. A verification pass just needs to confirm correctness (0
    mismatches, 0 empty white cells, no outright failure) — it doesn't
    need the same search budget a real player-facing generation gets, and
    the cross-palier retry mechanism (see `backend/crossword_gen.py`,
    `generate_grid`) already makes even a small per-attempt budget viable.
    Reach for `deadline_checks=None` (the grid-size-proportional default)
    or a larger `BUDGET_MODES` value only when the change under test is
    itself about search-budget behavior, timing, or something that Flash's
    small budget could hide or distort.

18. **Read `DOC_ALGO/FR/Lexicon.md` AND `DOC_ALGO/FR/ReadMe.md` at the
    start of every request — before any search through the code — and keep
    both current.** These two documents are the entry point to the
    grid-generation algorithm, and they come first, ahead of `grep`/`Read`
    on `backend/crossword_gen.py`: the Lexicon fixes the meaning of the
    important terms used to talk about the algorithm and its diagnostics
    (emplacement bloqué, case croisée bloquée, emplacement écarté,
    emplacement pauvre, etc.), so reading it first ensures a shared,
    unambiguous vocabulary with the user; `ReadMe.md` describes the whole
    current algorithm in execution order, so it says which mechanism (and
    therefore which function) a request is actually about, instead of
    having to rediscover it from a 10,000-line file one grep at a time.
    Reading them is not optional and not conditional on the request looking
    algorithm-related — a UI, diagnostic, or preview question usually turns
    out to be one. Use the code itself to confirm the detail and the exact
    current values once these two have said where to look.
    Whenever a new important term appears in a conversation to designate a
    concept in the code (a new diagnostic, a new grid state, a new UI
    mechanism, etc.), add its definition to `DOC_ALGO/FR/Lexicon.md` in the
    same style as the existing entries (French, present-tense, timeless — no
    narrative — with a `backend/`/`frontend/` source citation per rule 13's
    convention). Treat it with the exact same current-state-only discipline
    as `DOC_ALGO/FR/ReadMe.md` (rule 11): replace a definition in place if
    the concept it names changes, drop it if the concept disappears.

## Decisions

### Architecture

- **Licensed under MIT.** A `LICENSE` file (standard MIT text, verbatim so
  GitHub's license detection recognises it, `Copyright (c) 2026 Étienne
  Monneret`) sits at the repo root, and `README.md` carries a "## License"
  section pointing at it. If the copyright year/holder or the license
  itself ever changes, update the `LICENSE` file, that README section, and
  any SPDX identifier in project metadata together.
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
- **The back end exists in two interchangeable implementations**: Python
  (`backend/`, started by `run_Falcon.sh`) and Java (`backend_java/`,
  Java 21 + Maven, only third-party dependency Jackson, JDK `HttpServer`
  with virtual threads, started by `run_FalconJ.sh`). Same port, same API,
  same stores; the middleware and the UI cannot tell them apart. Each
  launcher stops the other version started from this checkout (matched by
  executable + command line + working directory, so another checkout's
  back end is never touched) before starting its own. Permanent rule 23
  makes them evolve together. In the Java back end a palier's parallel
  attempts are threads of the single JVM (not processes), each with its own
  RNG, sharing the read-only word index; the "Stop" signal, the sibling
  "attempt done" signal and the per-attempt budget counters are atomics,
  and the search threads get the same lowered priority as the Python
  worker processes (`renice` per thread, Linux).
- All grid-generation business logic lives only in `backend/crossword_gen.py`
  (never at the project root, never in `frontend/`) — and its Java mirror,
  the `backend_java/.../falcon/gen/` package. The Python module is both the
  CLI (`python3 backend/crossword_gen.py ...`, run from the project root so
  its default wordlist path resolves) and a library imported by
  `backend/app.py` via a relative import; the Java one has an equivalent CLI
  (`java -cp backend_java/target/crosswordfalcon-backend.jar
  falcon.gen.Generator ...`).
- English is this project's engineering language (code, comments, this
  SKILL, `CLAUDE.md`, `README.md`); product content (crossword words/clues,
  web UI text) is written in whichever of the 6 supported languages
  (fr/en/de/es/it/pt) the request is in.
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
- **"Interactif" authoring mode** (`mode="interactive"` on the web UI's
  `#mode` selector) is a hand-driven alternative to automatic generation:
  the player builds one grid word by word ("Suivant" places one word,
  "Précédent" undoes one step, the grid is fully editable including black
  cells), hand-writes every clue (with an LLM "Proposer" reusing
  `GET /api/dictionary/define`, a "Définitions" button that auto-fills one
  for every filled + valid word still lacking one — reusing
  `POST /api/interactive/verify` then `GET /api/dictionary/define` per
  word — and a "Vérifier" check), proposes a title, and either saves a
  draft to `GRID_WORK` ("Sauvegarder", no publish) or publishes to the
  Library tagged `(Création)` ("Publier", shown only once the grid is
  complete). The Library list also has a per-row "Ouvrir en mode
  Interactif" icon button (`POST /api/interactive/from-library`, matching
  proxy route per rule 15) that reshapes a finished library grid into an
  editable interactive session — reusing `_run_interactive_resume_job`
  wholesale — so it becomes its own brand-new `GRID_WORK` "Créations"
  entry on first autosave; the stored library record is never touched
  (`priority_words` is left empty even for a themed grid — the resolved
  Qdrant glossary was never stored on a library record, same limitation
  as a recompute job). The automatic-generation attempt-preview panel
  has the same kind of icon button next to each attempt's own stats line
  (`.attempt-preview-interactive-btn`, `renderAttemptPreview`) — `POST
  /api/interactive/from-attempt` (matching proxy route per rule 15) opens
  that specific attempt's own snapshot grid (whatever letters/black cells
  it has at that exact moment, an attempt-preview snapshot is never
  persisted or indexed by any id so the client sends the grid it is
  already showing directly) as a new editable interactive session, via a
  synthetic record reusing `_run_interactive_resume_job` exactly like
  `from-library`; the client cancels the still-running generation job
  first if there is one. Backend:
  (`POST /api/interactive/start` — a background job polled via
  `GET /api/generate/status/{job_id}` and cancelled via `POST /api/
  generate/cancel/{job_id}`; `POST /api/interactive/step` /`/title`
  /`/save`, all synchronous), each with its own `proxy_interactive_*`
  route in `frontend/server.py` (rule 15). A non-serializable module dict
  `INTERACTIVE_SESSIONS` (job_id -> built word index + theme glossary +
  rng) is evicted in lockstep with `JOBS`/`CANCEL_EVENTS` in `_new_job()`.
  The single-word placement is `crossword_gen.interactive_place_word()`, a
  new self-contained module function that never touches `Filler._backtrack`
  /`make_pattern`/`try_fill`/`generate_grid`. `save_grid_json` gained an
  `interactive` param -> `record["interactive"]`, surfaced by
  `_iter_stored_grids` and rendered as the `(Création)` library tag.
  Publishing (`POST /api/interactive/save`) also best-effort re-writes the
  session's own `GRID_WORK` draft to the final published state (via
  `save_grid_work`) so the "Créations" list stays current; the draft is
  kept, not deleted, on publish. Deleting a "Créations" entry
  (`POST /api/interactive/work/delete` -> `grid_store.delete_grid_work`)
  only removes the `GRID_WORK/<id>.json` file and never touches the
  published `GRID_STORE` library record. Every `save_grid_work` call also
  stashes the record's own PRE-overwrite content under a `previous` field
  (one step of history only, never a full chain — see CLAUDE.md's
  `GRID_WORK/` entry), at the user's own explicit request, so a live bug
  report against "Suivant" can be replayed straight from the saved file
  without first asking the player to hit "Précédent" purely to hand over
  a "before" snapshot. Every reader of the record (`get_grid_work`/
  `_iter_stored_grid_work`/`_run_interactive_resume_job`) reads specific
  top-level fields for the CURRENT state and must keep doing so — `previous`
  is diagnostic-only, never itself the active state a resume rebuilds
  from. A `diagnostics` field pairs with it, carrying everything the grid
  was SHOWING at that save beyond its letters (every cell overlay, the
  "Stats" letters, and the last "Suivant"'s own `placed` object), sent by
  the client — the only authority on it — on every autosave and on
  "Sauvegarder"/"Publier". It exists because none of it is recomputable
  from the saved grid: `interactive_place_word` rebuilds its
  `excluded_cells` from scratch on every click and keeps nothing between
  two of them, so a yellow emplacement is unrecoverable once the page is
  gone. `previous["diagnostics"]` is then the state displayed before the
  last click. Any new save path must resend it (and `challenge_words`)
  rather than let the record's own field be blanked out.
  **The same one-step `previous` convention applies to `STOP_DUMP`'s own
  `live_preview` tiles**: a process's live tile is only ever overwritten,
  so each entry carries the snapshot it replaced (`crossword_gen._one_
  step_previous`/`_store_live_state`, applied at the three places a tile
  is written — the best-state/heartbeat drain thread, the palier-start
  reset, the just-finished-attempt tile). Without it a dump held each
  attempt's last state and nothing to say what changed to reach it, which
  is what most diagnostic questions about a stopped search actually ask.
  `GET /api/generate/status/{job_id}` strips the field from its own
  response (the web UI never renders it, and it would double a payload
  polled every 2s) — `JOBS[job_id]["live_preview"]` keeps it, which is
  what `save_stop_dump` writes. Both states name their publication
  channel (`reason`) and attempt (`attempt_id`), which a comparison needs:
  the new-record and heartbeat channels deliberately publish different
  overlay sets, so a difference between two tiles is not automatically a
  search step.
  **No new Python package and no new install step** — `requirements.txt`/
  `Install.sh`/`README.md`'s install section are unaffected (README's
  "Using the app" section does get a short user-facing paragraph, per rule
  5). `run_llm.sh` must be running for "Proposer" and the title
  suggestion, exactly as for normal clue generation.
- A **"Finir la grille"** button, at the end of the `#interactive-arrows`
  row, hands the current editable grid off to the ordinary automatic
  generation engine: every already-placed letter becomes a hard,
  permanent lock (`crossword_gen.generate_grid`'s own `resume_state`
  mechanism — the same one "Continuer" already uses, seeded with a
  `locked_letters` map built from the whole editable grid rather than
  from a previously failed automatic run), and the search fills in
  whatever's left, adding new black cells/words wherever still needed.
  `POST /api/interactive/finish` (matching `frontend/server.py` proxy
  route per rule 15) reuses the interactive session's own already-
  resolved theme glossary verbatim (`_run_generate_job`'s
  `override_priority_words`/`override_theme_description` parameters —
  no re-derivation of the theme via a second LLM/Qdrant round trip) and
  only asks the LLM for a clue on a word that doesn't already have one
  (`_run_generate_job`'s `preserved_clues` parameter, a `{(row, col,
  direction): clue}` map built from the definitions already typed) — a
  word's exact position/spelling can't have changed once its letters are
  locked, so this map still matches after the grid is completed. Returns
  a brand-new job_id, polled exactly like an ordinary generation
  (`GET /api/generate/status/{job_id}`); the interactive session itself
  is left untouched. The frontend highlights every already-placed cell
  in a dedicated light-green border (`.finish-locked`, `--finish-locked`
  token) throughout every attempt-preview grid of that run, distinct
  from `.locked` (a cell merely confirmed by the search itself, which a
  later cleanup can still revert) — see the `style-guide` SKILL.

### Ports and environment variables

- **Ports live in the 300x range**: frontend/middleware 3000, backend 3001,
  local LLM server 3002, local embedding server 3003 (`EMBED_PORT`, see
  `run_embed.sh`) — moved off the original 800x range after
  diagnosing a real collision: a VS Code helper process was also listening
  on `127.0.0.1:8000`, silently shadowing the real frontend server for any
  client connecting via `127.0.0.1` specifically. Do not move these back
  into the 800x range.
- The core ports are declared once, at the top of `env.sh`/`env_default.sh`,
  as `export VAR="${VAR:-default}"` (`CROSSWORDFALCON_FRONTEND_PORT`,
  `CROSSWORDFALCON_BACKEND_PORT`, `LLM_PORT`, `EMBED_PORT`) so a value already set in the
  calling shell's environment survives sourcing. `CROSSWORDFALCON_BACKEND_URL`
  and every `LLM_BASE_URL` line are *derived* from these two port variables
  via shell interpolation, never a separately hardcoded literal — changing a
  port once updates every URL built from it. `run_Falcon.sh`/`run_llm.sh`/
  `frontend/server.py`/`backend/clues.py` each fall back to the same literal
  defaults only if neither env file was ever sourced.
- **Production and development run side by side on one machine as two
  separate checkouts.** The public, stable site is a clean clone of the
  GitHub repository (`CrossWordFalcon_PROD/`, gitignored inside the
  development checkout) on the default ports 3000/3001/3443, bound on
  `0.0.0.0`, with `CROSSWORDFALCON_EXPERIMENTAL_NOTICE=0`; it owns the
  public data (`GRID_STORE/`, `GRID_GAME/`, `GRID_WORK/`, `SECRET/`) and
  the Let's Encrypt state (`letsencrypt/`, `renew-https.sh`, webroot = its
  own `frontend/static/`). The development checkout serves on 5000/5001,
  bound on `127.0.0.1` only (`CROSSWORDFALCON_FRONTEND_HOST`, read by
  `run_Falcon.sh`), with no HTTPS instance and an explicit
  `CROSSWORDFALCON_BACKEND_URL` (without it `frontend/server.py` falls
  back to 3001, i.e. the production back end). Both share one set of model
  servers — LLM 3002/3004, embeddings 3003, Qdrant 6333 — launched from
  the development checkout, since the GPUs cannot hold a second copy.
  Restarting `run_llm.sh`/`run_embed.sh`/`run_qdrant.sh` therefore
  interrupts the public site too.
- `CROSSWORDFALCON_PARALLEL_ATTEMPTS` (default: this machine's own CPU
  count, `os.cpu_count()` — changed from a fixed 10 at the user's explicit
  request, so a deployment automatically uses as many parallel attempts as
  it has cores instead of a number picked for one particular machine)
  controls how many parallel pattern/CSP-fill attempts `backend/
  crossword_gen.py` runs per palier; mentioned (commented out, as an
  override example) at the top of `env.sh`/`env_default.sh`.
- `CROSSWORDFALCON_FRONTEND_WORKERS` (default 10, set in `env.sh`/
  `env_default.sh`, read by `run_Falcon.sh`) is the uvicorn `--workers`
  count for the **middleware/front server only** (both the HTTP and, if
  enabled, the HTTPS instance). Safe there: `frontend/server.py` is a
  stateless proxy + static-file server (a fresh `httpx.AsyncClient` per
  request, no cross-request state, no `@app.on_event` scheduler). The
  **back server always runs single-process — never pass `--workers` to
  `backend.app`**: its `JOBS`/`CANCEL_EVENTS`/`_BACKGROUND_TASKS`/
  `_PRESENCE` dict, the `GRID_QUEUE`/`CLUES_QUEUE` single-concurrency
  queues, and the two `@app.on_event("startup")` background tasks
  (`_rss_daily_scheduler`, `_presence_sweep_scheduler`) all
  live in one process's memory and cannot be shared across workers — a job created by
  one worker 404s when polled via another, the queues stop bounding
  CPU/GPU load, the RSS fetch runs N times a day, and each worker sees
  (and logs to `LOG_USERS/`) only its own share of the presence
  heartbeats. Making the back
  multi-worker would require a shared job store (Redis/SQLite) + a
  cross-process queue/semaphore + restricting the scheduler to one
  worker; not done, and the back (fully async, CPU delegated to
  `ProcessPoolExecutor`) gains essentially nothing from `--workers`
  anyway.
- `env.sh` (project root) holds `LLM_BASE_URL`/`LLM_MODEL`/`LLM_API_KEY` and
  is gitignored (real secrets); `env_default.sh` is the checked-in template
  with placeholder credentials only, copied to `env.sh` on a fresh clone.
  `run_Falcon.sh` sources `env.sh` (or `env_default.sh` if absent) before
  starting the backend.
- **Qdrant vector database (optional)** — a local vector store for word
  embeddings, standalone infra mirroring how SGLang has its own separate
  install path (not part of the base `Install.sh`/`run_Falcon.sh` flow).
  The running app uses it in two places:
    1. the Dictionary panel's "Thématique" button (`GET /api/similar_
       words` → `_similar_words_impl`, restricted to the language tenant)
       — mirrors themed-grid glossary construction: `describe_theme`
       expands the typed term into a keyword list, then `_compiled_
       similar_words` runs one Qdrant nearest-words search per keyword
       (`_iter_scored_words`) and merges (best score per word). Returns
       **every** merged word whose score reaches `min_score` — the current
       value of the generation form's "Précision thématique" field,
       forwarded as a query param so the panel reacts to it live (default
       `THEME_MIN_SCORE` = 0.78 when blank, clamped `[0,1]`) —
       most-similar-first, no count limit and no length filter; a clean
       503 `similar_unavailable` when Qdrant / the embed server is down or
       the collection is unpopulated, so the rest of the UI is
       unaffected). The LLM expansion makes it slower than a plain vector
       search, so its frontend/proxy timeouts are widened (`SIMILAR_FETCH_
       TIMEOUT_MS` 70s / `SIMILAR_PROXY_TIMEOUT_S` 60s);
    2. a **localhost-only "Qdrant (admin)" panel** — `GET /api/qdrant/
       admin` (read-only state: reachability, vector config, point/index
       counts, tenant index, per-language counts), `POST /api/qdrant/
       admin/recreate` (`ensure_collection(recreate=True)`), `POST
       /api/qdrant/admin/delete-tenant` (`delete_lang`). The gate is
       entirely at the proxy: `frontend/server.py`'s `_require_localhost()`
       403s any request whose client IP is not loopback **or** whose
       `Host` header is not a loopback name (blocks the LAN and the
       reverse-proxied-public-domain cases alike); `script.js` also keeps
       the button `hidden` off `isLocalhostOrigin()`. All three routes
       have matching `frontend/server.py` proxy routes (rule 15).
  - `Install_qdrant.sh` installs Docker (if missing) and pulls the
    `qdrant/qdrant` image; `run_qdrant.sh` (`start` [default] / `stop` /
    `status`) runs the container with `data/qdrant/` bind-mounted as
    persistent storage, waits for the HTTP API, then creates/asserts the
    `words` collection — by delegating to `python -m backend.qdrant_store
    --init` when the venv is present (so the collection's shape has one
    source of truth), falling back to a bare `curl PUT` create otherwise.
    Both scripts invoke `docker` directly, or via `sudo docker` if the
    user isn't in the `docker` group.
  - `backend/qdrant_store.py` (`QdrantStore`) is the client class — plain
    `httpx`, no `qdrant-client` SDK, no new `requirements.txt` package,
    same env-swap design as `backend/embedder.py`. Every request retries
    a transient timeout/connection error up to `QDRANT_REQUEST_RETRIES`
    (2) times with a short linear backoff before raising
    `QdrantStoreError` — a real 4xx/5xx response is never retried. The
    text actually embedded per word (`upsert_words`/`_compose_embed_
    text`) is just the word's own forms, space-joined: the accented/
    inflected spelling, the bare accent-stripped uppercase MOT form, and
    each candidate canonical form/lemma (no dictionary definitions — an
    earlier gloss-enriched version was dropped as not precise enough).
    The Qdrant payload itself never carries this compiled text, only the
    word's own plain fields. **One collection
    (`words`), one tenant per language**: every point has a `lang`
    payload field, registered as a Qdrant *tenant* keyword index
    (`is_tenant: true`); a per-language search is a filtered search, never
    a separate collection. The vector **dimension is probed from the live
    embedder** (`backend/embedder.py`, BAAI/bge-m3 → 1024), unless
    `QDRANT_VECTOR_SIZE` is set. Vectors are **RAM-resident by default**
    (`QDRANT_ON_DISK=0`; the six-language set is only a few GB — on-disk
    HNSW seeks per graph hop and is unusably slow on a spinning HDD, so
    only set `QDRANT_ON_DISK=1` on SSD storage). Deterministic point ids
    (`uuid5("<lang>:<word>")`) make the populator idempotent/resumable.
  - `data_builder/qdrant_populate.py` (`WordEmbeddingIndexer`) fills the
    collection from `data/wordlist_<lang>_full.tsv` — embeds, via
    `Embedder` (batched), the compiled text `backend/qdrant_store.py`'s
    `_compose_embed_text` builds for each word (its accented/inflected
    spelling, bare uppercase MOT form, and canonical form(s), space-
    joined), upserts one tenanted point per word. CLI:
    `python -m data_builder.qdrant_populate {fr|…|
    --all} [--limit N] [--offset N] [--batch N] [--recreate]`. Needs
    `./run_qdrant.sh` + `./run_embed.sh` up. A full `--all` recompute is a
    multi-hour run (measured ~150-200 words/s with the short word-forms
    text while sharing the GPU with SGLang) — run detached (`nohup ... &
    disown`, `logs/qdrant_populate.log`/`.pid`, the same convention as
    `run_Populate.sh`), never blocking on it; deterministic point ids
    make it safe to interrupt and resume (`--offset`) or simply re-run in
    full.
  - Config: `QDRANT_URL` (or `QDRANT_HOST` `127.0.0.1` + `QDRANT_PORT`
    `6333`; `QDRANT_GRPC_PORT` `6334`), `QDRANT_COLLECTION` (`words`),
    `QDRANT_DISTANCE` (`Cosine`), `QDRANT_VECTOR_SIZE` (unset → probe;
    bge-m3 is 1024), `QDRANT_ON_DISK` (`0` = RAM; `1` only on SSD), `QDRANT_API_KEY` (empty;
    sent as the `api-key` header when set, for Qdrant Cloud),
    `QDRANT_IMAGE`/`QDRANT_CONTAINER` (for the scripts). Each has the
    same fallback baked into the scripts and the class; a commented block
    in `env.sh`/`env_default.sh` documents overrides. `data/qdrant/` is
    gitignored.
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
- **Dual-GPU LLM routing**: on a machine with two or more NVIDIA GPUs,
  `run_llm.sh` (llama.cpp) and `run_sglang.sh` (CUDA path only — Apple
  Silicon has a single integrated GPU) can each launch TWO independent
  instances of the same model, one per card (`CUDA_VISIBLE_DEVICES`),
  instead of one. `LLM_GPU_INDEX` (default `"0"`) is the primary card;
  `LLM_INTERACTIVE_GPU_INDEX` (default unset — single-instance mode)
  is the second card, which also needs `LLM_PORT_INTERACTIVE` (default
  `3004`) and, for `backend/app.py` to actually route traffic to it,
  `LLM_BASE_URL_INTERACTIVE` (`LLM_MODEL_INTERACTIVE`/`LLM_API_KEY_
  INTERACTIVE` are optional, falling back to the primary instance's own
  `LLM_MODEL`/`LLM_API_KEY` — both instances are always the same model,
  only the endpoint differs). `backend/clues.py`'s `LLMClueGenerator`
  and `backend/chatbot.py`'s `ChatBot` both take optional `base_url`/
  `model`/`api_key` constructor overrides for this (falling back to the
  environment when omitted, unchanged for every other caller);
  `backend/app.py` builds a second `interactive_clue_generator`/
  `interactive_chatbot` pair whenever `LLM_BASE_URL_INTERACTIVE` is set
  and differs from the primary, otherwise both aliases point at the same
  primary instance. Routing: the **primary** instance serves every
  automatic full-grid generation request — `Automation/Populate.py`,
  the web UI's "Générer la grille" form, Interactive mode's "Finir la
  grille" (all funnel into `_run_generate_job`), and "Recalculer"
  (`_run_recompute_job`); the **interactive** instance serves everything
  else that calls the LLM — Interactive/Edition mode's own theme-glossary
  build and "Proposer un titre", the ChatBot (`POST /api/chat`), and the
  Dictionary panel's "Définir"/"Thématique" plus the Paraphraseur ("
  Synonymes" makes no LLM call at all, pure Qdrant, so it's unaffected
  either way). `_build_theme_glossary` (`backend/app.py`) takes a
  `clue_gen` parameter (defaulting to the primary `clue_generator`) so
  its two call sites can each pass the right instance.
  `Install.sh` detects the GPU count (`nvidia-smi -L`) and, only when
  ≥2 NVIDIA GPUs are found and the chosen engine is `llamacpp` or
  `sglang_cuda`, asks interactively whether to dedicate a second card to
  interactive requests, writing the right env vars into the managed
  `env.sh` block. `stop_running_llm_server` (`Install.sh`) and both
  launcher scripts' own stop-existing-server logic always stop *both*
  ports unconditionally, so switching back to a single instance never
  leaves the second process orphaned.
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
- `run_Falcon.sh`'s `stop_port()` finds the server with
  `lsof -ti tcp:$port -sTCP:LISTEN` — the `-sTCP:LISTEN` scope is
  load-bearing: a bare `lsof -ti tcp:$port` also returns any *client*
  with an open connection to that port (a browser, a `curl`,
  `Automation/Populate.py`'s polling loop), and `stop_port()` would then
  SIGTERM the client too. That silently killed a long Populate run once,
  mid-poll, on a routine `./run_Falcon.sh` restart (no traceback, no
  graceful shutdown — Populate has no SIGTERM handler). It then kills
  each matched PID's whole process tree
  (`kill_tree()`, recursing via `pgrep -P` before killing the PID itself),
  not just the PID alone — fixed after 436 orphaned `ProcessPoolExecutor`
  worker processes were found live, accumulated over days: restarting
  mid-generation used to only kill the uvicorn PID, silently orphaning
  any active worker processes (`backend/crossword_gen.py`'s
  `generate_grid()` creates its own `ProcessPoolExecutor` per palier,
  never held anywhere a shutdown handler could reach) — orphaned this
  way, a worker runs forever, since nothing in `Filler._backtrack` checks
  "is my parent still alive." A deliberate exception to this project's
  usual "never forcibly kill a worker" rule (see `GenerationCancelled`
  in `backend/crossword_gen.py`): that rule is about the cooperative
  "Stop" button *inside* a still-running server; stopping the whole
  server abandons any in-flight work regardless, so there's nothing left
  to preserve by leaving its workers alive. Verified live: a real
  restart triggered mid-search (10 active workers confirmed via `ps`)
  left zero afterward.
- `run_Populate.sh` (project root) is the start/stop/restart wrapper for
  `Automation/Populate.py` (the bulk library populator). It is *not*
  managed by `run_Falcon.sh` — Populate is a plain background process,
  not a port listener. Subcommands: `status` (default, never starts
  anything), `start [args…]` (pass-through to `Populate.py`), `stop`
  (graceful SIGINT, escalating to a second SIGINT then SIGKILL after
  `POPULATE_STOP_GRACE`, default 20s), `restart [args…]`. Tracks the
  process via `logs/populate.pid` (validated against `/proc/<pid>/cmdline`)
  with a `pgrep` fallback. Standing constraint: do **not** actually start
  Populate until the user says clue grammatical agreement is good enough
  — the script exists so it can be managed cleanly, not so it can be run
  now.
- `frontend/server.py` proxies `/api/*` through one shared
  `httpx.AsyncClient(timeout=PROXY_TIMEOUT_S)` (30s, one value for every
  proxied route, not a per-endpoint split) and sets
  `Cache-Control: no-store` (plus `Pragma`/`Expires`) on every response via
  a blanket middleware, since this app is edited and reloaded by hand
  during development. `frontend/static/script.js` polls
  `GET /api/generate/status/{job_id}` every `POLL_INTERVAL_MS` (2000ms) and
  its own browser→middleware fetch timeout is kept comfortably above
  `PROXY_TIMEOUT_S` so it never races the proxy's own timeout.
- Client-side persistence split: the user's own preferences (chosen UI
  language + pseudo + the "cookies accepted" flag, set through the
  first-visit `#welcome-overlay`) live in a single functional cookie,
  `cwf-prefs` (`document.cookie`, one-year `max-age`, `SameSite=Lax`) —
  small, and framed to the user as a cookie so the consent notice
  ("no tracking, no advertising cookies") is literally accurate. The
  potentially-large per-browser "seen grids" id list stays in
  `localStorage` (`cwf-seen-grids`), since it can hold thousands of ids,
  well past a cookie's size budget. New client-only state should follow
  the same rule: a cookie for a tiny consent-relevant preference,
  `localStorage` for anything that can grow.
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
  `LOG_CHAT/`, `LOG_USERS/` (one daily `LOG_USERS/<YYYY-MM-DD>.log` per
  day: one line — `date time | count | active pseudo list` — appended by
  `backend/app.py` each time the distinct online-visitor count changes,
  created lazily on first write), `LOG_THEME/` (one
  `LOG_THEME/<timestamp>_<short_id>.log` per themed generation, timestamp
  precision matching `LOG_LLM/`: first line is the LLM's ~15-word
  comma-separated keyword list, then the typed theme, every keyword list
  produced, the flat set of keywords actually searched in Qdrant, and the
  entire preselected-word glossary, one
  `word<TAB>length<TAB>score<TAB>keyword<TAB>whole_theme_score` per line
  (Qdrant's own cosine similarity, the reference keyword whose search
  produced that score, then that same word's own proximity to the theme
  exactly as typed by the user — blank only on a Qdrant/embedder outage
  for that one extra computation)),
  `models/` (LLM GGUF weights,
  auto-downloaded by `run_llm.sh`), `data/
  hunspell_cache/`, `data/reference_corpus/` (both the full and the
  capped sentence corpus — see the next bullet — the full one alone can be
  multiple GB of raw text), and `data/qdrant/` (Qdrant's own on-disk
  storage, created by `run_qdrant.sh`). If one of these ever shows as staged/committed
  by mistake and hasn't been pushed yet, undo with a plain `git reset
  HEAD~1` (uncommits without touching the working tree) rather than a
  history rewrite.
- `data/gloss_dictionary/<lang>_glosses.jsonl` **is** committed (a few tens
  of MB total) — small enough to ship directly and load-bearing at runtime
  (the "easy"-difficulty gloss filter, LLM clue grounding), unlike the much
  larger raw corpus/dump caches above.
- `data/inflection/<lang>.jsonl` **is** committed (~4–23 MB per language,
  ~100 MB total) — the exact-form grammatical-analysis table read by
  `backend/inflection_lookup.py` for the clue prompt's `A=` line. Stored
  **plain uncompressed** (at the user's request, so the tables can be
  inspected / `grep`ed by hand) — each file is comfortably under GitHub's
  100MB hard limit and its 50MB soft warning. Same "small + load-bearing,
  ship it, don't make every deploy re-run a multi-MB-download build" call
  as the gloss dictionary; no unpack step, so `Install.sh` needs nothing
  for it. Its own raw source dumps live in `DICS/` (gitignored) like the
  gloss dumps.
- `build_sentence_corpus.py` writes TWO files per language, not one, at the
  user's explicit request once it was noticed that capping the corpus for
  GitHub distribution was also silently starving `build_wordlist_freq.py`
  of data it needs uncapped (see the data pipeline section below): `data/
  reference_corpus/<lang>_sentences_full.txt` (every validated sentence,
  gitignored, never distributed — `build_wordlist_freq.py`'s own input) and
  `data/reference_corpus/<lang>_sentences.txt` (a `MAX_SENTENCES_PER_
  LANGUAGE`-capped random sample of it, also gitignored locally, but this
  is the one `compress_reference_corpus.py` publishes and `backend/
  example_sentences.py` reads at runtime — the two have genuinely
  different size needs and must never be confused for each other).
- `data/reference_corpus_<lang>.tar.xz` (one archive per language,
  committed) is `compress_reference_corpus.py`'s own output — a fast-path
  `Install.sh` can unpack instead of re-running `build_sentence_corpus.py`
  from scratch, sufficient for `backend/example_sentences.py`'s own
  runtime lookups but NOT for regenerating a wordlist (see the full/capped
  split above). GitHub enforces a **hard** 100MB per-file limit (not just
  a soft warning, which instead starts at 50MB) — checked directly by the
  script itself (`GITHUB_HARD_LIMIT_BYTES`/`GITHUB_WARN_LIMIT_BYTES`), and
  the reason this archive is split per language rather than one combined
  one in the first place (a single combined archive once got rejected
  outright by a real `git push`, "GH001: Large files detected," when this
  mechanism was first built). `compress_reference_corpus.py` already
  compresses correctly internally — do not "fix" it by switching to `tar
  -cJf ... ` with `XZ_OPT`: that exact mistake was already made and found
  live, twice, in this project's own history (first when this per-language
  split was originally introduced, then again when the full/capped split
  above was added and the script rewritten from scratch without checking
  this entry first) — on a machine whose `tar` is `bsdtar`/libarchive
  (common on macOS, confirmed via `tar --version`), `tar -cJf`'s own
  built-in xz filter silently ignores `XZ_OPT`, producing a materially
  worse compression ratio with no error or warning (measured live on the
  same French corpus: 52.7 MB via the broken method vs. 48.6 MB via the
  correct one — the exact difference between failing and passing GitHub's
  50MB soft-warning threshold). The script instead pipes a real `tar -cf -`
  stream through a directly-invoked `xz -9e -T0` process — check
  `compress_reference_corpus.py`'s own source before changing this again.
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
  SENTENCE` (50) words are kept. Writes TWO outputs (see "Data & git
  hygiene" above for why): `data/reference_corpus/<lang>_sentences_full.txt`
  (everything kept) and `data/reference_corpus/<lang>_sentences.txt` (a
  reproducible random sample capped at `MAX_SENTENCES_PER_LANGUAGE`, 3M).
  `--recap` re-derives just the capped file from an already-built full one
  (memory-safe streaming reservoir sample — never loads the full,
  multi-gigabyte file into memory at once) without re-running the download/
  filter pipeline. Each source's own raw sentences are cached under
  `CORPUS/` so a reprocessing pass doesn't re-download from opus.nlpl.eu.
- `build_wordlist_freq.py` writes `data/wordlist_<lang>_full.tsv` as
  `MOT<TAB>ACCENTUE<TAB>FREQUENCE<TAB>CANONIQUE` — the bare accent-stripped
  uppercase grid form, its natural accented/inflected spelling, a blended
  frequency score, and every candidate canonical form/lemma
  (semicolon-separated when ambiguous). `strip_accents` also folds a
  ligature letter with no accent-style decomposition of its own (French
  `œ`/`Œ`/`æ`/`Æ`) into its two separate ASCII letters before MOT is
  derived (`œ`→`oe`, `æ`→`ae` — "sœur" -> `SOEUR`, never `SŒUR`), so MOT
  always stays a plain run of A-Z letters typable on a simple keyboard;
  `ACCENTUE`/`CANONIQUE` keep the natural ligature spelling.
  `backend/crossword_gen.py`'s `challenge_word_grid_form` and
  `backend/dictionary_lookup.py`'s `_norm` apply the same fold before
  deriving their own grid/search-key forms, for the same reason. Minimum word length is 2 (a
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
- `build_inflections.py` downloads the **English-Wiktionary** Kaikki dump
  per language (`kaikki.org-dictionary-<Name>.jsonl.gz`, ~54-96 MB gzip,
  cached in `DICS/` as `<Name>-en.jsonl.gz`) — the English edition, not
  the own-language one `build_gloss_dictionary.py` uses, because only it
  tags inflection with a consistent structured vocabulary — extracts every
  `form-of` sense's grammatical tags, filters to the surface forms in
  `data/wordlist_<lang>_full.tsv`, and writes
  `data/inflection/<lang>.jsonl` (committed, plain uncompressed). Read at runtime by
  `backend/inflection_lookup.py` for the clue prompt's `A=` line. It reads
  the wordlist, so re-run it after a wordlist rebuild.
- `data_builder/build_<lang>.sh` (one per fr/en/de/es/it/pt) is a one-shot
  orchestration wrapper that runs the pipeline stages for that language in
  dependency order — `build_sentence_corpus.py` -> `build_wordlist_freq.py`
  -> `build_gloss_dictionary.py` -> `compress_reference_corpus.py` ->
  `build_inflections.py` (steps 1-5, each bailing out on the first
  failure) -> `python -m data_builder.qdrant_populate <lang> --recreate`
  (step 6/6, **non-fatal**: feeding the Qdrant `words` collection is a
  downstream nicety, needs `./run_qdrant.sh` + `./run_embed.sh` running,
  and only warns if skipped/failed — the wordlist/gloss artefacts from
  steps 1-5 are the real deliverables). It
  `cd`s to the repo root, points `PATH`/`LD_LIBRARY_PATH` at this host's
  rootless `~/.local` hunspell build, and is safe to re-run (every stage
  reuses its own on-disk cache: `CORPUS/`, `DICS/`, `data/hunspell_cache/`;
  step 6's `--recreate` deliberately re-embeds from the just-rebuilt
  wordlist rather than reusing anything).
  These scripts live alongside the `build_*.py`/`compress_*.py` stages in
  `data_builder/`, not in `Automation/`.
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
  default 14) — applied only at a fresh-pattern palier: the very first
  one, or any palier immediately following a full cleanup (never a
  "reprise telle quelle" palier, which never calls `make_pattern` at all).
  The fraction is a share of the WHOLE grid, with no scaling by the
  remaining white area: `target = max(placed, black_ratio_floor,
  round(fraction * rows * cols))`, where `placed` already counts the
  carried-forward seed's own black cells and pre-fill's, so only the
  shortfall is added. A grid that restarts from a heavily reopened
  cleanup is therefore brought back up to the chosen rate rather than
  left sparse.
- `PARALLEL_ATTEMPTS` (default: this machine's CPU count, see env vars
  above) independent attempts run concurrently per palier via
  `ProcessPoolExecutor`; `attempts` (paliers)
  defaults to 200 (raised from an original 40 — some grids need many quick,
  unproductive cycles before a workable state emerges).
- **Every finished attempt frees its worker for a fresh replacement
  attempt** (success or failure), as long as an original attempt of the
  palier is still racing; replacements never extend the palier
  (`racing=False`, no `attempt_active` flag) and are all interrupted
  (`attempt_done_event`) once every original has finished or used up its
  budget. They are an extra chance within the palier, not extra lineages:
  the next palier resumes only the best `PARALLEL_ATTEMPTS - reset_count`
  cleaned grids (N-1 of N) plus its blank-grid worker(s) (`_seed_pool`).
- A single successful grid never concludes `generate_grid`'s search on its
  own: at least `MIN_SUCCESSFUL_ATTEMPTS` (2) genuine successes, counted
  cumulatively across the whole search rather than one palier alone, are
  required before the best one is picked. While harvesting a palier's
  parallel attempts, a worker freed by a success is immediately reassigned
  to a brand-new, from-scratch attempt (never a continuation of the grid
  that just succeeded) as long as the threshold isn't reached yet, instead
  of sitting idle for the rest of that palier. Relies entirely on the
  existing `attempts` budget (200 paliers by default, see above) as its
  only cap — if that budget runs out with just one success ever found,
  that one is accepted rather than the search reporting total failure.
- **Cross-palier retry**: when a palier's search fails, if the best failed
  attempt still has an unassigned slot that is neither impossible nor
  crossing an impossible one (`_slots_touching` — a slot crossing a
  known-impossible one counts as "no hope" too: `_clean_blocked_slots`
  strips its words at every "continue" palier anyway, so treating it as
  real progress lets `still_has_hope` stay `True` indefinitely and
  prevents cleanup from ever triggering), **and**
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
  that state. Neither path ever adds a black cell any more: the single-
  cell lock that used to run at the end of both (`_impossible_cell_groups`/
  `_lock_one_impossible_cell`, chosen at random among the cells of the
  winning grid's own impossible slot(s), preferring a still-blank cell
  over a lettered one) was removed entirely, at the user's explicit
  request — quoting their own prior description of the mechanism back and
  asking to delete it, right after separately asking to stop the
  per-step density draw above (see CLAUDE.md for the removed mechanism's
  full history). `carry_seed_grid` on the continue-verbatim path is now a
  direct, unmutated reference to the winning grid — no more index-shift
  workaround needed (`carry_preseed_assignment`/`carry_excluded_slots` map
  1:1 onto the winning grid's own unchanged slot indices). Both paths
  still dedupe the palier's parallel outcomes by
  (pattern, assignment) before counting/selecting from them. "Best failed
  attempt" (`failed_pairs[0]`) means the highest `_cleaned_playable_score`
  (the post-cleanup content score — see `crossword_gen.py`'s own
  `_content_score` docstring), not fewest impossible cells nor fewest
  black cells. "Best cleaned candidate" (chosen after nettoyage, among
  the 6 candidates) means the highest `_words_in_place_score` — the same
  `_content_score` formula, over every slot whose *every* cell is
  confirmed after cleanup (a few long confirmed words outweigh many short
  ones for the same letter total; a partially-confirmed slot scores 0),
  not the raw confirmed-letter count. This one shared formula (`_content_
  score`) also backs the successful-attempt tie-break (`opt_score`) —
  with a theme (`priority_words`) it only counts a word belonging to its
  own slot's glossary, and a "Mots Défi" word always counts, at a
  bonus-boosted length, regardless of theme — so a failed palier's
  attempt/candidate selection favors the theme/challenge words exactly as
  much as a successful palier's own tie-break does. `still_has_hope` is also forced to
  `False` (nettoyage instead of continue) whenever every one of the
  palier's `PARALLEL_ATTEMPTS` raw outcomes has `reason ==
  "abandoned_too_unfillable"` (see below) — if every worker independently
  gave up on its own pattern as too far gone, continuing "telle quelle" on
  it isn't worth trying.
- `Filler._backtrack` abandons a search attempt early — `self.abandoned =
  True`, every later call returns `False` immediately — once more than
  `UNFILLABLE_ABANDON_SLOT_COUNT` (3) still-unassigned slots are deemed
  impossible (`impossible_zone_slots()` against `best_
  assignment`), checked every `UNFILLABLE_ABANDON_CHECK_INTERVAL` (500)
  calls (not every call — recomputing domains for every unassigned slot
  has a real cost). Surfaces as `try_fill`'s `reason ==
  "abandoned_too_unfillable"`, checked ahead of `deadline_exceeded`/
  `blocked_on_excluded_slot`/`search_exhausted`. A one-attempt-making-
  zero-progress-then-recovering-next-palier pattern is normal, observed
  behavior of this whole mechanism, not itself a bug — verified live by
  reproducing the identical transient stall with the pre-window-of-10
  tier rule on the same seed before either of these two rules existed.
- **One shared definition of "emplacement bloqué", used by both modes.**
  `Filler.slot_is_blocked` is it: no candidate left at all, OR a crossing
  deadlock. `Filler._backtrack`'s own per-candidate check and Interactive
  mode's "Suivant" (`_word_breaks_open_slot`) make the identical call with
  identical arguments — at the user's explicit request: "Il ne doit pas y
  avoir 2 codes différents : le mode interactif est une version pas à pas
  du mode automatique... Si un emplacement est rouge à l'écran, le code
  doit aussi voir ce blocage." Both previously tested only for an empty
  domain, so a word could be placed across a slot the interface was
  already painting red (reproduced twice from real saved grids). The
  domain-iteration cost this check used to be kept out of the hot loop for
  is held down by a per-node `options_cache` (`Filler._letter_options_
  cached`), which always returns exactly what a from-scratch
  `_slot_letter_options` would: entries hold per-position letter COUNTS
  over the domain minus the node's own used words, so the candidate being
  tried only removes a letter it was the last supporter of (no domain
  rescan); a slot crossing the target is keyed by its known-letter
  signature (at most one entry per distinct crossing letter per node); a
  blank slot's counts come precomputed per length (`_blank_letter_counts`).
  Verified identical to a from-scratch recomputation on ~165 000 cached
  queries (Flash, three hash seeds, with and without "Mots Défi"); same
  fills as before on the same patterns, 2-9× less CPU per attempt, worst
  attempt 0.4-0.5s instead of 1.5-4.2s. A stale entry would read as
  "still has options" and hide a real blockage, so an entry whose
  recorded used-word set no longer matches is recomputed. Measured on
  the full French wordlist, Flash mode: 7×5 4/4 successes both before and
  after (20.2s vs 22.9s cumulative); 9×7 **2/4 before vs 4/4 after** — per
  successful grid it is about twice as slow (41.6/49.1s vs 82.9/101.6s),
  but it converts two 200s timeouts into real grids, so total wall time
  for the same four seeds drops from 490.7s (2 grids) to 329.2s (4 grids).
  11×8 times out 4/4 either way (pre-existing). Do not reintroduce a
  domain-only crossing test anywhere: `slot_is_blocked` is the one place
  this question is answered.
- "Impossible" also covers a crossing-letter deadlock, not just a plain
  empty domain: two still-open slots crossing at a cell whose remaining
  achievable letters share none in common are both flagged impossible,
  even though each one's own domain is non-empty alone
  (`Filler._crossing_deadlock_slots`, folded into `impossible_zone_slots`;
  module-level `_crossing_deadlock_indices`, folded into
  `_impossible_indices` so every one of its own callers inherits it too).
  Blackening a cell is a distinct mechanism from plain "nettoyage"
  cleanup, at the user's explicit request: `_clean_blocked_slots` never
  blackens a cell for a slot in its own `deadlocked_slots` subset — such
  a slot only ever has whatever crossing word(s) happen to touch its
  OTHER cells removed, the same unconditional "remove everything crossing
  an impossible slot" plain cleanup already applies to any impossible
  slot — which does nothing at all when both sides of the deadlock are
  still open (nothing assigned to remove either way), left flagged
  impossible until resolved some other way (a manual edit, or automatic
  generation's own pattern-reshaping, which already treats black cells as
  fully mutable on its own terms). The two functions also return the
  exact conflicting cell(s), surfaced by `_interactive_fill_diagnostics`
  as a third value (`deadlock_cells`, always ⊆ `impossible_cells`) shown
  in a more vivid red (`.interactive-deadlock`/`.attempt-preview-grid
  .cell.white.deadlock`, see `style-guide` SKILL) than the rest of the
  same impossible slot, in both Interactive mode and the automatic-
  generation attempt previews — `Filler.deadlock_zone_cells()` is
  `impossible_zone_cells()`'s own live-search counterpart, folded into
  every preview `examples` entry alongside `theme_cells`/`challenge_
  cells` — see `DOC_ALGO/FR/ReadMe.md` for the full reasoning.
  `_crossing_deadlock_indices` (the module-level, non-`Filler` version)
  also excludes a word already fully spelled out elsewhere in the grid
  from its own achievable-letter computation, matching the `Filler`-based
  version — a real bug found live: without this exclusion, an already-
  placed word could still count as "achievable" for an unrelated slot
  (it can't really be placed there again), silently papering over a real
  deadlock that the live red highlight (backed by the `Filler`-based
  check) still correctly showed — "Nettoyer" would then report nothing
  to clean up for a slot visibly shown impossible.
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
  too-many-impossible-slots rule fires) — so one worker's abandon becomes every sibling's
  `reason == "abandoned_too_unfillable"` within one check interval,
  without waiting for each to independently reach `UNFILLABLE_ABANDON_SLOT_COUNT` or its own
  `deadline_checks` budget. **Deliberately never wired into
  `_pattern_attempt`** ("motif neuf" paliers) — `_pattern_attempt` always
  passes `batch_abandoned_event=None` to `try_fill`, regardless of the
  worker-global being set, so this mechanism is a structural no-op there.
  This is load-bearing, not a stylistic choice: `_pattern_continue`'s own
  `PARALLEL_ATTEMPTS` workers all search the exact same shared pattern
  (only their exploration order differs), so one worker's "too many
  impossible slots" finding really does generalize to its siblings — but `_pattern_attempt`'s
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
- **A palier's own per-attempt budget is elastic, not a hard cutoff**: an
  attempt whose `checks` exceeds its own `deadline_checks` keeps
  searching past it as long as some sibling attempt of the same palier
  is still genuinely racing towards its own deadline (`Filler._siblings_
  still_racing`, backed by a palier-wide `attempt_active` `multiprocessing.
  Array` alongside the pre-existing `checks_progress` one) — stopping it
  right away would just leave its CPU core idle until that slower sibling
  finishes anyway, since the harvesting loop already waits for every
  future regardless. The "stop" verdict, once reached, is sticky for the
  rest of that attempt (see CLAUDE.md, "Elastic per-attempt budget," for
  why a non-sticky version silently defeats the whole mechanism). Every
  caller with no sibling visibility (interactive mode, `minimize_black_
  squares`, a solitary CLI run) is unaffected — same plain, unconditional
  stop as always.
- **Each `Filler._backtrack` node makes at most `MAX_DESCENTS_PER_NODE`
  (3) recursive descents** — `EARLY_MAX_DESCENTS_PER_NODE` (10) while the
  search has placed fewer than `EARLY_DESCENTS_WORD_COUNT` (5) words on
  top of the attempt's initial state — before returning `False` to its parent — one
  cap shared by all four stages of the node, `allow_breaking` included;
  a candidate rejected by the crossing check is not a descent, nor is a
  "Mots Défi" or theme-glossary candidate (every hypothesis from those
  two glossaries is explored); `<= 0`
  disables the cap. It exists so backtracking climbs back to words placed
  early in an attempt: an uncapped node only fails after exhausting its
  whole subtree, which never happens within the budget.
- **`Filler._backtrack` backjumps on conflict sets** (`BACKJUMPING_ENABLED`):
  chronological backtracking with a per-node cap still costs `cap^k` nodes
  to climb k levels, so the first words of an attempt are never revisited
  and a local failure is replayed under every unrelated intermediate word.
  Every failure path goes through `Filler._fail` so `_last_conflict` is
  never stale; `None` means "backtrack chronologically".
- **The last-resort `allow_breaking` stage is gated globally, not per
  node.** `Filler.solve` runs a strict pass from the root first; only if
  the root itself fails (not the budget, not an abandon) is the search
  replayed with `Filler.breaking_permitted` on. The rule, in the user's
  words: create an impossible slot only once "on a exploré toutes les
  possibilités avec backtrack" — across the whole grid, the first words
  included. A per-node trigger let the deepest node relax first, before
  any early word was ever questioned. Expected consequence: this pass only
  runs when total possibilities are few (tiny grids, or large grids mostly
  locked by earlier paliers).
- `Filler.mark_immediately_impossible_slots()` (called once in
  `try_fill`, right before `solve()`) flags as "écarté"
  (`_impossible_this_attempt`) any still-unassigned slot whose domain is
  already empty given *only* fixed constraints (locked letters, no search
  decision made yet). Purely a head start for the deprioritization —
  `_backtrack`'s own per-node domain check would flag the same slots on
  its very first call anyway — so the very first slot selection already
  prefers a healthier slot. One pass suffices, since flagging a slot never
  changes any other slot's own domain.
- `Filler.excluded_slots` is a **structural** exclusion, deliberately kept
  distinct from an "emplacement écarté" (see that entry below): a slot
  named there is dropped out of the grid the search has to solve at all —
  never selected, never required by `truly_complete`, never counted as a
  broken crossing, never surfaced by any diagnostic or overlay.
  `_optimize_before_cleanup` is its only caller (completing what it can
  while deliberately leaving an entirely-empty or already-impossible zone
  untouched); every generation palier leaves it `None`. Do not reuse it to
  express "this slot looks blocked right now" — that is what
  `_impossible_this_attempt` is for, and conflating the two is exactly the
  drift from `DOC_ALGO/FR/Lexicon.md` that the écarté entry below records.
- `Filler._backtrack`'s slot-selection is a **9-level cascade**
  (`_select_target_slot`, reused verbatim by `interactive_place_word` —
  the authoritative level-by-level description lives in `CLAUDE.md` and
  `DOC_ALGO/FR/ReadMe.md`, chapter "Choisir un emplacement"), with **no
  MRV/domain-size override**: restricting the choice to the smallest
  *domain* is the rejected rule, and it stays rejected. Level 7 is its
  per-CELL counterpart and is a separate, user-validated rule: find the
  smallest number of letters still possible on any one still-free cell
  (`_slot_min_letter_options`, reading the same tally Interactive mode's
  "Stats" button displays via `_interactive_letter_stats`) and keep only
  the slots owning a cell with that count. The tally is kept separately
  per direction (`Filler.letter_scores_by_dir`, refreshed direction by
  direction as words are placed) and crossed: only letters both the
  across and the down slot observed at the cell count, each at the lower
  of its two counts (`_crossed_letter_counts`) — the user-validated
  definition of "letters still possible on a cell". A cell
  already determined by a real letter is skipped, otherwise every
  partially-filled slot would report 1. Level 7 runs inside level 6's
  geometric window (the `SLOT_SELECTION_WINDOW_SIZE` (10) slots closest to
  the grid's center), not over the whole group, and measures slots through a
  decreasing length threshold: 7 letters and more first
  (`MOST_CONSTRAINED_START_LENGTH`), then 6, 5… down to 2
  (`MOST_CONSTRAINED_MIN_LENGTH`), stopping at the first threshold where
  some slot of the window has a measurable free cell.
  The older 2-tier rule this cascade grew out of is kept below for the
  MRV reasoning it carries, which still holds: (1) alternate across/down,
  weighted by how many free slots
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
  `letter_scores` tally. That tally is then kept current during the
  search instead of staying frozen on the pre-search state: each time
  `_backtrack` writes a word, `Filler._refresh_letter_scores_around`
  re-samples every still-open slot it CROSSES against that slot's own
  current `_domain`, and `_restore_letter_scores` undoes it as the
  placement is reverted. A slot with an empty domain is skipped (nothing
  left to measure), and a refreshed slot REPLACES its cells' tally rather
  than adding to it — the other contributor to those cells is the word
  just placed, whose letters are now fixed. Measured on `try_fill`
  directly, same patterns and seeds, 20 000-check budget: 7×5 went from
  9/25 to **13/25** filled (9.5s -> 6.3s), 9×7 from 0/15 to **2/15**
  (8.5s -> 7.5s) — fresher statistics pay for their own cost and then
  some. Cost is bounded by construction: at most one crossing slot per
  cell of the placed word, each costing one `_domain` call the node
  already pays for every unassigned slot. Candidates are shuffled, ranked by a
  sum-of-squares score against `letter_scores`, then drawn via a
  `CANDIDATE_SCORE_WINDOW`-wide sliding window (random among the best
  remaining, not a strict rank order) — this ranking is always active,
  independent of whether letter-forcing itself is on. The window is **50**
  — deliberately far narrower than a slot's own domain, which routinely
  holds thousands of words: a window wider than the domain puts every
  candidate in it at every draw, which makes the order a plain uniform
  shuffle and cancels the scoring out entirely, so a rare word becomes as
  likely as a well-scored one. The value arbitrates exactly that, against
  leaving enough room for two attempts (or two "Suivant" clicks) on the
  same state to diverge.
  `Filler.ordered_candidates` is the one place this rule lives, and it is
  shared verbatim by the automatic search (`_backtrack`) and by
  Interactive mode's "Suivant" (`_general_dictionary_pick` for the
  general-dictionary tier, `_find_priority_word_placement` for the "Mots
  Défi"/theme tiers, each slot's own words ordered by that same call).
  Interactive mode is the step-by-step version of the automatic search,
  so it must draw from the window too: picking a slot's single
  best-scored candidate instead makes "Suivant" deterministic, returning
  the same word on every click for a given grid state with no way to
  reach the other candidates that slot genuinely has. Do not reintroduce
  a strict-argmax candidate choice anywhere.
- **Themed generation** (`generate_grid(priority_words=...)`,
  `Filler(priority_words=...)`): an optional set of preferred words. When
  non-empty, `Filler._backtrack` stably partitions each slot's own
  candidate list — priority members first, the rest after — *after* all
  the statistical ordering above, so backtracking exhausts every fitting
  theme word for a slot before descending to a non-theme dictionary word
  there. A genuine per-slot preference with fallback, never a hard
  restriction: the full lexicon is still loaded and used wherever no
  combination of theme words completes a slot. Threaded to the CSP
  workers through the pool `initializer` (`_worker_priority_words`, like
  `_worker_index`) and into `minimize_black_squares`. `backend/app.py`'s
  optional `GenerateRequest.theme` field (free-text word list, new
  "Thématique" form input) drives it: `_run_generate_job` first asks the
  LLM (`LLMClueGenerator.describe_theme`) for a ~30-word telegraphic,
  comma-separated keyword list describing the theme — deliberately mixing
  parts of speech (nouns, verbs, adjectives, adverbs), at the user's
  explicit request, so the glossary isn't nouns-only — one list for the
  whole theme, plus one per word (`_theme_tokens`) when the typed theme
  has more than one word (each word likewise expanded via `describe_theme`,
  falling back to the bare word / typed tokens on LLM failure). Every list
  is split into individual keywords (`_split_keywords`), all keywords are
  flattened into one case-insensitively de-duplicated search set; if that
  set has fewer than `THEME_MIN_KEYWORDS` (300) entries,
  `describe_theme(theme)` is re-called up to `THEME_KEYWORD_LLM_MAX_LOOPS`
  (3) more times (early-stop when a call adds nothing new) — **grid
  glossary only**, at the user's explicit request; the Dictionary panel's
  "Thématique" button never loops. Every grid-glossary `describe_theme`
  call uses `THEME_KEYWORD_LLM_TEMPERATURE` (0.9, vs. the method's own 0.7
  default the Dictionary panel keeps) to widen the keyword variety. Then
  `_compiled_theme_words_by_length` runs a **separate Qdrant nearest-words
  search per keyword** (`_theme_words_by_length` each), merging every
  result and keeping each word's highest score across searches, along
  with the specific keyword whose own search produced that kept score —
  `(word, score, keyword)` triples, logged next to each word in
  `LOG_THEME/`, at the user's explicit request: "indiquer le mot de
  référence qui a servi à calculer le taux de proximité." A single sharp
  keyword is a far more precise query vector than one averaged embedding
  of a 15-word sentence — the full-sentence embedding is no longer
  searched at all. A 4th LOG_THEME column, `whole_theme_score`, is
  computed SEPARATELY by `_whole_theme_proximity_scores` for every one of
  these words regardless of which keyword surfaced it — at the user's
  own further, explicit request: "ajouter sur chaque ligne le taux de
  proximité du mot avec le 'whole theme'", then "calculer le score pour
  tous les mots, y compris ceux qui ne proviennent pas de la 'whole
  theme'" once it was pointed out that reusing the incidental per-keyword
  search hits (as the first version of this column did) silently left it
  blank for any word that only ever surfaced via a per-token/top-up
  keyword, then corrected twice more: first to compare against the theme
  exactly as typed by the user (`theme`, `GenerateRequest.theme`) rather
  than the LLM-expanded "(whole theme)" keyword list, then to take the
  BEST score across `theme`'s own individual words (`_theme_tokens(theme)`
  — the same tokenizer used elsewhere in this function to build one
  keyword list per theme word) rather than one merged embedding of the
  whole typed phrase. It embeds each of `theme`'s own words once via
  the embedder directly, retrieves every
  preselected word's own ALREADY-INDEXED vector by id in one batched call
  (`QdrantStore.retrieve_word_vectors`, built on a new `retrieve_vectors`
  — Qdrant's "Retrieve points" API, no re-embedding, so the vector is
  exactly the one already stored for that word), and computes each
  (word, theme word) pair's cosine similarity by hand as a plain dot
  product — valid because `backend/embedder.py`'s vectors are
  L2-normalized and this collection's own distance metric is Cosine, so a
  dot product IS the cosine similarity, identical to what a real Qdrant
  search would report. At the user's own further explicit request, this
  score also GATES membership in the compiled glossary: a preselected
  word is dropped outright — never handed to `generate_grid`, never
  listed in `LOG_THEME/` — the moment its own `whole_theme_score` falls
  below `WHOLE_THEME_REJECT_LENIENCY` (1.5)'s own reject threshold,
  `max(0.0, 1 - (1 - theme_precision) * WHOLE_THEME_REJECT_LENIENCY)` —
  deliberately looser than `theme_precision` itself, since a word can
  legitimately sit very close to one sharp keyword while only moderately
  close to the theme's own literal words; this filter exists only to
  catch a word that drifted essentially off-topic. The resolved threshold
  is logged in `LOG_THEME/`'s own header as `whole-theme reject
  threshold`. Best-effort, isolated from the
  rest of `_build_theme_glossary`: a Qdrant/embedder outage during just
  this extra computation leaves the column blank AND skips this filter
  entirely (every preselected word kept) rather than failing the
  themed generation itself. `theme_description` (the **whole-theme** keyword
  list only — never a per-token or top-up list) is still produced and fed
  to `generate_title` (soft inspiration) and to `generate()` clue writing
  as a **strong** directive: `_build_system_prompt`'s `THEME` block tells
  the model to actively steer every clue toward the theme wherever the
  word's meaning and grammar allow, accuracy still inviolable (a
  theme-flavoured phrasing is dropped for a clue it would make wrong).
  The list is echoed in each `LOG_LLM/*.md` header (`- **Theme
  keywords** …`) and once to `backend.log`. The `LOG_THEME/` header
  records every keyword list and the flat searched set. `_theme_words_by_length` itself
  pages through the Qdrant tenant's own
  ranked nearest-neighbor list (`QdrantStore.search`'s `offset`)
  collecting **every** word whose own length falls between
  `THEME_LENGTH_MIN` and `THEME_LENGTH_MAX` (3-15) and whose Qdrant
  cosine-similarity score is at least the threshold — `THEME_MIN_SCORE`
  (0.78) is the *default*, overridable by the "Précision thématique" form
  field (`GenerateRequest.theme_precision`, a 0-1 float threaded as
  `min_score` through `_compiled_theme_words_by_length`/`_theme_words_by_
  length`/`_iter_scored_words`); the same field's value is also forwarded
  to the Dictionary panel's "Thématique" button (`GET /api/similar_words`'s
  `min_score` query param). No cap
  on how many words come back or on how deep the pagination goes, only the
  score threshold and the 2-15 length range bound the result — the scan
  stops the moment a hit drops below the score threshold (Qdrant's own
  results are already ranked by score descending, so nothing later could
  ever qualify either), or once the tenant is exhausted. The returned list is sorted by increasing word
  length (stable, so score-descending order survives within each
  length) for `LOG_THEME/`'s own readability — `crossword_gen.py`'s
  `priority_words` only ever treats it as an unordered set of words.
  Best-effort — Qdrant/embed-server down or the
  collection unpopulated for that language → logged, generation
  proceeds with no theme. The LLM keyword list
  is written as the first line of a per-job `LOG_THEME/<timestamp>_
  <short_id>.log` (`THEME_LOG_DIR`, gitignored). The raw theme string is stored on
  the grid record (`grid_store.save_grid_json`'s `theme` param) and shown
  in the library's "Thématique" column; the LLM description and the
  pre-selected words are not stored on the record. On a bilingual grid
  the theme applies to the main (across) language only.
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
  preference. It also takes a `challenge_words` parameter, protecting any
  "Mots Défi" word already placed on the grid the exact same way it
  already protects a `permanent_locked_letters` cell: the word's own
  cells are locked/preseeded into every trial's own `try_fill`, and
  exempted from this function's own final "every word must be a real
  dictionary entry" acceptance check. Found live as a real gap: unlike
  every other cross-palier repair stage (`_optimize_before_cleanup`/
  `_shorten_impossible_zones`/`_lengthen_impossible_zones`/`_clean_
  continue_candidate`, which already exempt a challenge word via
  `_challenge_word_cells`), this final optimization pass used to rerun a
  from-scratch `try_fill` with zero memory of which slot held a challenge
  word — silently overwriting it with a different real word the fresh
  search happened to prefer (reproduced deterministically in isolation:
  a locked-nowhere 7-cell slot holding the non-dictionary "ZXQVJ" came
  back as the real word "ETRENNE" once its neighboring black cell was
  removed) or, more rarely, rejecting an otherwise-legitimate removal
  outright because the challenge word itself had no dictionary-entry
  exemption to fall back on. Both symptoms are the same missing-parameter
  root cause, fixed by giving `minimize_black_squares` this parameter and
  wiring it through both of its own callers in `generate_grid` (the
  trial-optimization tie-break among several successful paliers, and the
  real, final optimization of the winning grid) — the same isolated test
  confirmed the word now survives unchanged (the removal is correctly
  skipped) once `challenge_words` is passed.
- A cooperative `GenerationCancelled` mechanism (checked at palier
  boundaries, inside `Filler._backtrack` every `CANCEL_CHECK_INTERVAL`
  (500) elapsed checks — `Filler._periodic_checkpoints`, evaluated at its
  entry and after every counted candidate — inside `minimize_black_squares`'s removal loop, and between
  words during clue generation) backs the web UI's "Stop" button — it never
  force-kills a worker process, only stops at the next natural checkpoint.
- **Floating-black-cell widening for "Mots Défi" words only**
  (`_widen_floating_black_cells_for_priority_words`): a challenge
  word with no matching-length empty slot anywhere yet gets one carved
  out by relocating a "floating" black cell (not in `permanent_black_
  cells`, relocatable while keeping `is_structurally_valid(min_interior_
  free=1)` — the same relaxed threshold `minimize_black_squares` already
  uses) to the far side of the word instead of its current position
  (`_try_widen_black_cell`, built on a module-level `_white_run` helper).
  It never writes a letter itself, only reshapes the black-cell pattern,
  leaving the existing slot-selection/candidate-priority cascade to place
  the word on its own; `_try_widen_black_cell` also refuses to let the
  relocated black cell land on any cell already in `locked_letters` (never
  destroying an already-known letter) and refuses any relocation that
  would turn a PERPENDICULAR slot into one with no real dictionary
  candidate at all (`_perpendicular_slot_stays_valid`/`_slot_has_domain`
  — a lightweight, `Filler`-independent domain check applied to the slot
  crossing `(r, c)` once it takes its own new letter, and to the slot(s)
  `new_black` splits/shortens) — at the user's explicit request: "Le
  placement des Mots Défi doit se faire en respectant les règles
  fondamentales du placement d'un mot (ne pas créer d'emplacement
  impossible). Si aucun placement ne permet de respecter cette règle, le
  Mot Défi doit être considéré comme implaçable." Without this, a
  relocation could silently attach an uncloseable stray cell to an
  existing crossing word, truncate one, or leave a newly-formed short
  crossing slot unfillable — none of it visible to `is_structurally_
  valid`, which only ever reasons about black/white shape, never about
  known letters or the dictionary. Bounded by `WIDEN_BLACK_CELL_WINDOW`
  (black cells scanned per word), `WIDEN_PRIORITY_WORDS_LIMIT` (words
  tried per group), and `WIDEN_MAX_SUCCESSFUL` (total relocations per
  pattern) to keep its cost bounded. A theme-glossary word never gets a
  slot widened or shortened for it — widening/shortening is reserved for
  "Mots Défi", in both modes (`_pattern_attempt` passes the challenge
  list alone; Interactive mode's theme tier calls
  `_find_priority_word_placement` with `allow_reshape=False`), so a theme
  word only ever takes a slot the pattern already offers. A
  best-effort mechanic, same as the ordinary "Mots Défi"/theme placement
  it strengthens — a word too long for any available merged run, or one
  no relocation can accommodate without corrupting something else, falls
  through to the shortening fallback below before ultimately falling back
  to the ordinary geometric-fit placement (or being left unplaced this
  round). Once every word of one glossary group has had its own widening
  attempt, a shortening fallback (`_shorten_one_slot_for_word`/`_try_
  shorten_slot`) runs over whichever of that group's words are still
  unplaced, before the next glossary group gets a turn, at the user's
  explicit request: the mirror operation — instead of relocating an
  existing black cell to grow a run up to the word's own length, it scans
  existing EMPTY slots already longer than the word and casts a brand new
  black cell into the interior, right past the word's own span, casing it
  flush against either end (no existing black cell moves, since the space
  was already open). Same two safety checks (`locked_letters`/
  `_perpendicular_slot_stays_valid`) apply to the new black cell. Bounded
  by `SHORTEN_SLOT_WINDOW` (existing slots scanned per word) — set to
  `FALLBACK_PHASE_BUDGET_FRACTION` (10%) of `WIDEN_BLACK_CELL_WINDOW`, at
  the user's own explicit framing of the budget as "identique aux 10%
  déjà calculés" elsewhere in this same mechanism family, reused here
  since no `deadline_checks`-based budget exists yet at this pre-search
  stage — and shares `WIDEN_MAX_SUCCESSFUL` (total relocations/insertions
  per pattern) with the widening pass above. `_pattern_attempt` is the one
  caller of the batch orchestrator built on top of these two functions
  (`_widen_floating_black_cells_for_priority_words`), applying it to a
  freshly generated pattern before the CSP search starts (never
  `_pattern_continue`, whose pattern already carries real placed words on
  some slots — every cell is still blank at `_pattern_attempt`'s own
  call, so every check above is a trivial no-op there) — safe to reshape
  for its WHOLE word list in one cumulative pass, since every reshape gets
  kept regardless of outcome (the CSP search that follows fills the whole
  grid over many placements, never just one). `interactive_place_word`
  (Interactive mode's "Suivant") calls the exact same two low-level
  functions directly, but never the batch orchestrator and never on a
  shared, cumulative pattern — see "Priority-tier search in Interactive
  mode, fully isolated per candidate" below for why (one click only ever
  places one word, so only one reshape may ever survive) and how.
  `frontend/static/script.js`'s `interactiveNextBtn` handler
  replaces the whole `interactiveGrid` from the step response
  (`data.grid`) rather than patching only `data.placed.cells` — a
  relocated or newly-inserted black cell can land outside the placed
  word's own cells, and patching only those silently desynced the
  client's grid from the backend's; the existing full-snapshot undo stack
  ("Précédent") needed no separate change to correctly revert either kind
  of black-cell change along with everything else.
- **Crossing-safety retry, generalized to all three candidate tiers**: a
  candidate is never left in place once it leaves a crossing slot with no
  viable word at all (dictionary-dry AND no other unused, not-yet-
  abandoned challenge word able to fill it in turn) — at the user's
  original explicit request for "Mots Défi": "si le placement d'un Mot
  Défi crée des emplacements croisés impossibles, ne pas le poser,
  chercher un autre Mot Défi ou un autre emplacement (équivalent à un
  backtrack immédiat)", later generalized, again at the user's explicit
  request, to the theme glossary and the general dictionary — the same
  three tiers `_backtrack` already tries in precedence order for a slot.
  `Filler._backtrack`'s own per-candidate `crossing_broken` check (already
  reverting any word that breaks a crossing) is unconditional for every
  candidate regardless of tier — the immediate revert-and-try-the-next-
  candidate behavior this check already had is what satisfies "backtrack
  immédiat" for all three. What differs per tier is what happens once a
  tier keeps failing this way: a challenge word or a theme word gets its
  own per-word give-up budget (`FALLBACK_PHASE_BUDGET_FRACTION = 0.10` of
  the attempt's own `deadline_checks` — renamed from `CHALLENGE_WORD_
  PHASE_BUDGET_FRACTION` once it stopped being challenge-word-specific —
  `Filler._challenge_word_budget`/`_theme_word_budget`); once trying it
  has broken a crossing that many times in one attempt, it's abandoned
  for the rest of that attempt (a fresh attempt/palier gets a fresh
  budget), matching the user's own "renoncer à placer un Mot [Défi/du
  glossaire thématique] que quand tout a été essayé ou 10% du budget
  épuisé dans cette phase de recherche." The general dictionary needs no
  such budget: it simply keeps trying its remaining candidates, and the
  node fails normally once none is safe. The rule holds on every tier
  with exactly ONE last-resort exception, and the distinction is the
  whole point: **it may never yield on a budget, only on genuine
  exhaustion.** An earlier per-slot hatch (`_domain_break_abandoned`)
  relaxed it once a slot had spent 10% of the attempt's
  `deadline_checks` — i.e. BEFORE exploring — and the result was grids
  carrying runs of letters spelling nothing real (LEETSP, CAEPSL,
  SSERIIV…), visible in a `STOP_DUMP` snapshot. The correct trigger, in
  the user's words: "on ne doit pas poser un mot qui crée un emplacement
  impossible, sauf si on a exploré toutes les possibilités avec backtrack
  et que la seule manière de continuer c'est de poser un mot qui crée un
  emplacement impossible… plutôt que la laisser dans un état précoce avec
  beaucoup de vide." So `_backtrack` relaxes (`allow_breaking`) only once
  a node has explored the whole strict subtree of every one of its slots;
  the dry-slot freeze yields at that same moment and for the same reason;
  `allow_breaking` is never inherited by a child node; and a word
  accepted that way never counts against any per-word give-up budget.
  What keeps the strict phase workable is the baseline: `crossing_broken`
  only fires for a slot that was in `domains` at the top of the node —
  i.e. still had a real candidate BEFORE this word was placed. Without
  it, one already-dry neighbour made every candidate at every adjacent
  slot look unsafe, which is what the budget hatch was really
  compensating for. The baseline is free (`domains` is already built per
  node, already omitting dry slots); never swap it for a budget.
  Interactive
  mode's "Suivant" (`interactive_place_word`) applies a BROADER version of
  this check (`_word_breaks_open_slot`) to all three tiers: a candidate is
  rejected not only when it empties the domain of a slot it directly
  crosses, but also of any OTHER still-open slot anywhere in the grid that
  happened to have that exact same word as its own last unused candidate
  — a narrow theme glossary routinely puts two entirely disjoint slots in
  that situation, and `interactive_place_word` has no cross-palier retry
  of its own (unlike `_backtrack`) to repair the damage afterward, so each
  click has to get this right up front. `_open_slot_baseline`, computed
  once per candidate slot and reused across every word tried there,
  snapshots each other open slot's own domain beforehand so a slot already
  impossible for an unrelated reason is never blamed on whichever
  candidate happens to be tested — without it, one pre-existing impossible
  zone anywhere would make every candidate everywhere look unsafe. All
  three tiers reuse the very same `Filler` field/method shape
  (`_active_challenge_words`/`_active_priority_words_for`/`_register_
  challenge_word_break`/`_register_theme_word_break`/`_register_domain_
  break`) rather than three separate implementations.
- **Priority-tier search in Interactive mode, fully isolated per
  candidate**: `_word_breaks_open_slot` alone is not a sufficient safety
  check for a candidate that needs its own black-cell reshape, because
  reshaping changes which cells even belong to a slot (freeing/blackening
  a cell merges/splits whatever run passes through it) — so whichever
  `Filler` the check runs against must reflect the exact, final pattern
  that specific candidate would leave behind, nothing else mixed in. An
  earlier version got this wrong: it ran the batch orchestrator
  (`_widen_floating_black_cells_for_priority_words`) once for the WHOLE
  "Mots Défi"/theme word list, mirroring `_pattern_attempt`'s own call —
  stacking up to `WIDEN_MAX_SUCCESSFUL` reshapes onto ONE shared pattern
  before any word was even chosen, then running every candidate's own
  safety check against that SAME shared, over-reshaped `Filler`. A
  candidate could look perfectly safe there only because some UNRELATED
  word's own, not-yet-decided reshape happened to still be propping up a
  THIRD slot's shape for the duration of that one check; once that
  unrelated reshape was reverted (a different word ending up being the
  one actually placed), the third slot's real shape could turn out
  genuinely impossible after all — found live twice, first as a "Mots
  Défi" word (`MONTAGNE`) breaking a distant row purely because of an
  unrelated word's own reshape sitting on the shared pattern during its
  check, then again as a theme word (`MEMOS`) landing in a slot whose own
  delimiting black cell had been silently stolen back by a later,
  unrelated word's own widening scan within that same shared batch pass.
  Fixed, at the user's own explicit framing ("chaque tentative doit être
  considérée comme indépendante des autres... isolées sur des copies de
  la situation"), by giving up the shared batch pattern for Interactive
  mode entirely: `_find_priority_word_placement` (shared by the "Mots
  Défi" and the theme tier) first tries every ordinary, already-
  dictionary-viable slot the word pool fits, purely against the grid's own
  untouched base `Filler` — nothing here can ever be contaminated, since
  no reshape is involved. Only once every ordinary combo has failed does
  it give each remaining word with no natural or ordinary slot its own,
  fully ISOLATED widen-then-shorten attempt (`_try_reshape_for_word`, one
  independent copy of the base pattern per word, discarded immediately if
  unused), builds a brand-new, throwaway `Filler` from that ONE copy alone
  (`_build_interactive_filler`), and runs `_word_breaks_open_slot` against
  it — the real, final pattern this specific candidate would leave behind
  if chosen, so the check can never be fooled by another word's own
  reshape. Abandonment bookkeeping (`_register_challenge_word_break`/
  `_register_theme_word_break`, their own budgets sized from the total
  number of ordinary combos plus reshape attempts) is always applied to
  the grid's own outer `Filler`, never to a per-candidate isolated one, so
  it persists correctly regardless of which specific `Filler` ends up
  confirming any one candidate; the exemption `_word_breaks_open_slot`
  itself checks is likewise always drawn fresh from the outer `Filler`'s
  own current challenge pool (that check is hard-coded to challenge words
  specifically, regardless of which tier is running). The general-
  dictionary tier sweeps every still-open slot in the cascade's own order
  (`target` first), not `target` alone: a slot whose every candidate is
  refused becomes an "emplacement écarté" — reported to the panel as
  `excluded_cells` and shown yellow — and the sweep moves on, so the grid
  is declared impossible only once no still-open slot can take a word.
  A slot already deemed blocked is tried last within each sweep. Each slot excludes both other pools from its
  own candidate list outright — any such word still present in its
  domain was necessarily already tried, across every slot in the grid, by
  one of the two tiers above — falling back to the raw, unfiltered domain
  only if excluding both would leave nothing at all. Once a tier's search
  returns a winner, `interactive_place_word` reconciles that winner's own
  pattern (the base one, untouched, for an ordinary pick; the one isolated
  reshape copy, for a pick that needed one) straight into the real letter
  grid — no revert-unused-reshapes pass is needed any more, since nothing
  but the eventual winner's own single reshape (if any) was ever applied
  to begin with.
- **Crossing an already-impossible emplacement is allowed in exactly one
  place**: the last-chance enrichment at the very end of
  `_optimize_before_cleanup`, once a palier has failed and its grid is
  about to be handed to the cleanup. The rule, in the user's words: "des
  mots peuvent être posés, croisant les emplacements impossibles, quand
  tout a été tenté et que la grille est sur le point d'être déclarée
  'échouée', de manière à permettre de poser plus de mots pouvant
  éventuellement survivre à un nettoyage à suivre, et ainsi garder une
  grille mieux remplie à l'étape suivante." Scoped per failed palier (on
  the candidate grids just before cleanup), not per node and not per
  "Suivant" click — `Filler._backtrack`'s own `crossing_still_impossible`
  stays absolute, and so does Interactive mode's `"crossing_blocked"`
  verdict. It works through `excluded_slots` (an excluded slot is skipped
  by `_backtrack`'s per-candidate crossing check), not by relaxing that
  check. Sealing is explicitly allowed: a word may complete an impossible
  emplacement into a run spelling nothing real — the
  `_invalid_fully_known_indices` recomputation in that same function then
  flags it impossible, so the cleanup still knows about it. Do not
  generalise this exception to any other call site without asking.
- **The last-resort exception applies to Interactive mode too**, not only
  to the automatic search: `interactive_place_word` re-runs its whole
  three-tier search over the whole grid once per acceptance level
  (`_placement_accepted`, `PLACEMENT_LEVEL_STRICT` → `_DISJOINT` →
  `_BREAKING`), which is how a single-decision click expresses
  `_backtrack`'s own three-stage node (strict slots, released écarté
  slots, `allow_breaking`). The last level accepts a candidate that NEWLY
  blocks an emplacement it crosses (`_word_breaks_open_slot`'s
  `"crossing"` verdict); `"crossing_blocked"` — crossing an emplacement
  blocked already — stays refused at every level, exactly as
  `crossing_still_impossible` does in `_backtrack`. The rule, in the
  user's words: both modes must accept releasing the "emplacements
  écartés" and placing words that create impossible emplacements as a
  last resort, since such a zone has to exist before a later cleanup can
  repair it — better a well-filled grid carrying one than a grid declared
  impossible while still half empty. A whole level is exhausted across
  all three tiers and every still-open slot before the next is tried, so
  a stricter placement anywhere outranks a more damaging one and "Mots
  Défi" keeps its precedence at every level; a candidate accepted at a
  tolerant level never counts against a tier's own give-up budget. The
  automatic search already satisfied this rule through `_backtrack`'s own
  `allow_breaking` stage — only Interactive mode had to be brought in
  line.
- **A word's own black-cell change is atomic with the word itself, across
  cross-palier cleanup**: `_shorten_impossible_zones`/`_lengthen_
  impossible_zones` deliberately allow a shorter/longer word to cross an
  ALREADY impossible slot elsewhere (`_new_crossing_impossibility` only
  rejects a NEW degradation, never a pre-existing one) — a word placed
  this way can therefore go on to be removed by `_clean_blocked_slots`'s
  own crossing-word removal a few lines later, in that very same cleanup
  pass, once it processes that other slot. Found live as a real gap: the
  black cell added or relocated specifically to fit that word used to
  stay behind regardless, permanently and pointlessly narrowing (or
  failing to widen) a zone whose own justification had just been removed.
  Fixed by having both functions hand back `black_cell_links` (a map from
  each placed word's own cells to its exact black-cell change), threaded
  through `_clean_continue_candidate` into `_clean_blocked_slots`, which
  reverts the linked change (`_revert_black_cell_link`) in the same step
  it removes a linked word — reopening a shortened word's own added
  black cell, or restoring a lengthened word's own old boundary and
  undoing its new one. Reverting never re-checks structural validity: it
  only ever restores a cell to the exact color it had before this
  round's shortening/lengthening touched it, a state already known
  valid. `_clean_blocked_slots` now returns a 4th value, `reopened_cells`
  (cells to whiten), alongside its existing `new_black_cells` (cells to
  blacken) — `_clean_continue_candidate` applies both to the pattern it
  hands to the next palier.
- **An "emplacement écarté" (yellow) is a pure deprioritization, held in
  exactly one set, and reset to nothing at the start of every palier.**
  `Filler._impossible_this_attempt` is that set and is the single
  definition of the term across the whole engine, matching
  `DOC_ALGO/FR/Lexicon.md`, which is the specification here, in the
  user's own words: "les emplacements écartés (après avoir été une fois
  impossibles) fonctionnent exactement comme tous les autres
  emplacements, ils ne sont juste pas prioritaires pour tester une
  nouvelle pose, tant qu'on peut poser ailleurs." The list holds only the
  `MAX_EXCLUDED_SLOTS` (3) most recently flagged slots — with most of the
  grid yellow, deprioritizing it steers nothing — and every word placed
  removes from it the crossing slots it leaves no longer blocked. Its
  ONLY effect is `selectable`'s deprioritization
  (with a fallback to the full pool), and in every other respect — domain
  recomputation, crossing protection, being picked and filled — such a
  slot is an ordinary slot. It is never walled off, never triggers a
  backtrack on its own, and is never inherited from a previous palier
  (`generate_grid` always dispatches `_pattern_continue` with
  `excluded_slots=None`; a fresh `Filler` starts with the set empty).
  Three feeds, and ONLY three, all internal to the search:
  `mark_immediately_impossible_slots()` before it starts, `_backtrack`'s
  per-node domain check during it, and `_backtrack`'s candidate loop
  running out — every candidate of the chosen slot rejected for leaving
  some crossing slot impossible. That third feed costs nothing (the loop
  has just tried them all) and leaves the slot with a genuinely non-empty
  domain, so it keeps being crossed freely; only its selection priority
  changes, unlike a blocked (red) slot, which is never crossed.
  **Nothing on a display path may ever write to this set.** A crossing
  deadlock in particular is reported by
  `excluded_zone_cells(include_deadlock=True)` for that one snapshot and
  never memorised: it is a property of the assignment being examined, not
  a lasting fact about the slot. Memorising it (an earlier design) meant
  the mere act of publishing previews rewrote the search's own scheduling
  set — replaying a STOP_DUMP with and without the preview queue, all else
  identical, gave 44 slots set aside vs 0, 36 yellow cells vs 4, and 7991
  of 8967 slot selections with nothing but écarté slots to choose from vs
  0, i.e. the grid froze into a block of yellow and the deprioritization
  became vacuous.
  `_backtrack` runs each node in three ordered stages — non-écarté slots
  first, then the écarté ones released for the rest of that descent, then
  ordinary backtracking — with `released` as a recursion parameter so the
  release undoes itself on unwinding. It falls
  back to the full pool the moment stage 1 would leave nothing to choose
  from; `excluded_zone_cells` renders exactly the same set, so the overlay
  and the search behavior can never disagree. **Three invariants to
  preserve**: (1) nothing may ever remove an écarté slot from `unassigned`
  — the previous design did, via `excluded_slots`/a derived
  `_crossing_excluded_slots`, and a `STOP_DUMP` snapshot showed a search
  burning hundreds of thousands of checks cycling among a shrinking pocket
  of slots while a large walled-off yellow zone sat untouched; (2) a slot
  that merely CROSSES an écarté one has no special status at all — it is
  neither deprioritized nor painted yellow; (3) finding a slot
  unfillable in the current state makes the node backtrack (the user's
  rule: "La détection d'emplacements écartés, donc impossible à remplir en
  l'état, doit provoquer un backtrack"), except for what no backtracking
  can repair (`Filler._tolerated_dry`: dry before the search started, or
  dried on purpose by a last-resort word), while the écarté flag itself
  stays a possibility of the node, retried in its released stage ("ces
  emplacements ont peut-être été créés dans une configuration antérieure,
  et ils sont peut-être redevenus viables").

### LLM clue generation (`backend/clues.py`)

- `LLMClueGenerator` owns all LLM handling (endpoint config, prompt text,
  the HTTP call, response parsing); `backend/app.py` builds one instance at
  module scope. Talks to any OpenAI-compatible chat-completions endpoint —
  the local `llama_cpp.server` (`run_llm.sh`) by default, or a cloud API
  (e.g. Mistral) via `env.sh`, no code change needed either way.
- One word per LLM *request* (`_BATCH_SIZE = 1` — even a small batch
  degraded reliability on small local models); up to 3 immediate,
  consecutive retries per word before giving up. `generate()` fires up to
  `CLUE_BATCH_PARALLELISM` (10 by default, env-overridable) of those
  single-word requests concurrently via a `ThreadPoolExecutor`, so a
  server with continuous batching (e.g. SGLang) decodes several at once;
  a server without request concurrency (a plain llama.cpp build with no
  `--parallel`) simply serializes them — no speed-up but no harm.
  `generate()` also accepts a per-call `batch_parallelism` override
  (`None` = the module default): `backend/app.py` passes `1` whenever
  `GenerateRequest.source == "populate"` (set by `Automation/
  Populate.py`'s own `_build_request()`), forcing that one job's own
  words fully sequential — at the user's explicit request, so a
  Populate-originated grid's clue generation never crowds out a real
  user's own concurrent, `CLUES_QUEUE`-exempt LLM call (`GET /api/
  dictionary/define`, `POST /api/interactive/title`) for the same GPU.
  `CLUES_QUEUE` itself already serializes clue-generation *jobs*
  project-wide (one job's `generate()` call at a time) regardless of
  origin — this override only narrows the *intra-job* concurrency of
  whichever job currently holds that queue slot.
- The model is asked for exactly 4 lines: an `A=` line first (the target
  word's part of speech + full inflection — person/number/mood/tense for
  a verb, number/gender for a noun/adjective), then 3 clue lines
  `C1=`/`C2=`/`C3=`. `_parse_response()` drops the `A=` line
  (`_ANALYSIS_LINE_RE`) — it exists only to force the model to analyse
  the grammar before writing the clues (the one instruction-side change
  that measurably improved tense/number/gender agreement on the small
  local model, unlike four earlier prose/example reinforcements). Clue
  lines are otherwise trusted as-is — the `C1=`/`C2=`/`C3=` labels help
  the model but are stripped and never required (`_LEADING_MARKER_RE`).
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
  parsing of the clue text, which isn't attempted. Prompt-only
  mitigation is layered, and the two levers that actually moved agreement
  on the small local model are: (1) the mandatory `A=` analysis line (the
  model must state the target's part of speech + full inflection before
  writing any clue — see the output-format bullet above), and (2)
  feeding that analysis to it directly, from a pure-local lookup of the
  exact form (`_build_pos_block` / `backend/inflection_lookup.py` —
  structured Wiktionary tags, incl. the *person* Hunspell can't give).
  Together: `HUMERA` 0/8→4/4 future, `IRAS` now second-person, `SERIONS`
  4/4 first-person-plural conditional (the "être" worst case). On top of
  those: rule 4 (with two named traps), a per-word grammar reminder
  appended to the user message right after `Word:`, a compact "GRAMMAR
  CHECK" block just before "OUTPUT FORMAT", and per-language
  `rule_bad`/`rule_good` worked examples in `data/<lang>_prompt_config.
  json` covering wrong-tense, wrong-person, wrong-number and wrong-gender
  clues. This still remains a known, accepted small-model reliability
  ceiling — the local table has no row for some forms (a lemma with no
  Wiktionary form-of entry, or a form it doesn't document), so those
  fall back to POS-only, and the model still slips on hard cases — so
  the mitigations
  raise the ceiling rather than remove it; a larger
  model (Qwen3.8-27B or a cloud API) both analyses and follows more
  reliably. Disclosed rather than chased with endless prompt iteration.
- Grounding: `_build_gloss_block`/`_build_examples_block` append real
  dictionary definitions and real usage sentences to the prompt when
  available, keyed as described in the data-pipeline section above; both
  sections are omitted when nothing is found, and the model is told to
  treat multiple genuine senses as an opportunity for variety across its 3
  candidates rather than collapsing to one. A **noun** gloss sense
  (`pos` in `{"noun", "name"}`) is dropped from the prompt unless its
  lemma is the grid word or its plural (`_noun_sense_matches_word` /
  `_singularize` — French: `aux`→`al`, else drop trailing `s`/`x`;
  en/es/it/pt: drop trailing `s`; German exempt) — so a verb form like
  French "iras" is not grounded with the noun "aller" even though "aller"
  is one of its canonical forms, while "allers" (that noun's plural)
  still is. Verb/adjective/etc. senses are never affected. Right after
  the definitions, `_build_pos_block` adds a block giving the exact grid
  form's part of speech AND full inflection, to feed the `A=` analysis
  line directly. Primary source: `backend/inflection_lookup.py`'s
  `describe_form()` — a **pure-local** read of
  `data/inflection/<lang>.jsonl` (built by `data_builder/build_
  inflections.py` from the English-Wiktionary Kaikki dump, checked in
  plain/uncompressed for hand inspection, ~100 MB total for all 6
  languages, filtered to each wordlist), whose
  form-of rows carry structured tags → e.g. `verb, third-person singular
  future (of "humer")`, `verb, second-person singular future (of
  "aller")` for `iras` (Hunspell can't give the person). No network,
  lazily loaded + cached per language. Fallback for a form not in the
  table (a lemma with no Wiktionary form-of entry, e.g. French `JE`): the
  Hunspell-stem (`hunspell -m` cached in CANONIQUE, no binary needed at
  request time) + gloss-dict POS line (POS only, no inflection). Both
  apply the same noun filter and easy-mode `name` drop as the gloss
  block; the block is omitted when neither source has anything. (An
  earlier version did this lookup online via `backend/kaikki_lookup.py`,
  since removed — the user wanted fully-offline capability.)
- Every LLM call (success or failure alike) writes its own diagnostic file
  under `LOG_LLM/` (gitignored), named `<timestamp>_<answer>_SUCCES.md` or
  `_ERROR.md`: the full system+user prompt, the raw LLM output, and every
  candidate's verdict (selected / accepted-not-selected / rejected +
  reason).
- When a word exhausts all 3 retries because every candidate was rejected
  *solely* for containing the target word (its every clue leaks the word
  — common for very short function words like French JE), `_generate_one`
  picks one of those rejected candidates at random and blanks the word
  out with "_" (`_mask_target_word`: "Je dis la vérité quand je parle" ->
  "_ dis la vérité quand _ parle", a legitimate fill-in-the-blank clue),
  at the user's explicit request. It never falls back to the bare answer
  as its own definition. A word with genuinely zero usable candidates
  (LLM unreachable, only too-long / wrong-language output) still ends up
  unclued — `frontend/static/script.js`'s `renderClueLines()` and
  `backend/svg_export.py`'s `_group_clue_lines()` then show a translated
  "no definition available" placeholder.
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

### Local multilingual embeddings (`backend/embedder.py` / `run_embed.sh`)

- A multilingual text-embedding stack, server/client split like the LLM
  one: `run_embed.sh` launches `llama_cpp.server --embedding` (no new
  dependency — `llama-cpp-python` is already installed via
  `requirements-llama.txt`) on `EMBED_PORT` (3003); `backend/embedder.py`'s
  `Embedder` class is the HTTP client. `Embedder.embed(str) -> list[float]`
  (L2-normalized), `embed_batch(list[str])` for many at once,
  `EmbedderError` on connection failure. `python -m backend.embedder
  --benchmark 1000` is the timing harness.
- **Default model: BAAI/bge-m3, `bge-m3-Q4_K_M.gguf` from `gpustack/bge-m3-GGUF`**
  (~418 MB, 1024-dim, CLS pooling, 100+ languages). Chosen as the smallest
  *current, working* multilingual GGUF: `multilingual-e5-small` (118M) is
  genuinely smaller but every community GGUF conversion of it fails to
  load in `llama-cpp-python` 0.3.35 (`bert model needs to define token
  type count` — stale conversions missing required metadata). `gpustack`'s
  bge-m3 build is current and loads cleanly. All knobs overridable in
  `env.sh` (`EMBED_MODEL`/`EMBED_GGUF_REPO`/`EMBED_GGUF_FILE`/
  `EMBED_BASE_URL`/`EMBED_API_KEY`); repoint `EMBED_BASE_URL` at any
  OpenAI-compatible `/v1` endpoint to swap providers with no code change.
- **Supported alternative: Qwen/Qwen3-Embedding-0.6B**, `Qwen3-Embedding-
  0.6B-Q8_0.gguf` from `Qwen/Qwen3-Embedding-0.6B-GGUF` (~609 MB, 1024-dim,
  last-token pooling, decoder-only Qwen3 base — no smaller official quant
  exists). Documented as a commented-out block in `env_default.sh`, right
  after the active `EMBED_MODEL`/`EMBED_GGUF_REPO`/`EMBED_GGUF_FILE`
  exports (uncomment those three to switch — `llama_cpp.server` never
  passes an explicit `pooling_type` override, so it reads the correct
  pooling mode straight from the GGUF's own metadata, no other file or
  code change needed). Measured cross-lingual FR/EN cosine 0.873
  (same-meaning) vs. 0.543 (unrelated) — a cleaner separation than
  bge-m3's own 0.90/0.48 — traded against noticeably slower GPU throughput
  (~66/s single-request, ~162/s batched, vs. bge-m3's ~124/s / ~465/s).
  **Switching the embed model always requires a full Qdrant rebuild**
  (`python -m data_builder.qdrant_populate --all --recreate`) regardless of
  whether the vector dimension happens to match (it does here, 1024 both
  ways) — the vector space itself is model-specific, so old and new
  vectors can never coexist in the same tenant.
- `run_embed.sh` is **CPU by default** (`--n_gpu_layers 0` +
  `CUDA_VISIBLE_DEVICES=""`): the request was for a CPU embedder, and on a
  machine where `llama-cpp-python` is a CUDA build (for `run_llm.sh`), not
  hiding the GPU made model load hang for minutes while the GPU was busy
  with the LLM. `EMBED_N_GPU_LAYERS` (`env_default.sh` default `0`; `99` =
  all layers) opts into GPU. Measured on bge-m3: **CPU ~40 ms/embedding
  p50, no batched speed-up; GPU ~8 ms single (~120/s), ~2 ms batched
  (~465/s)** — GPU is ~5-15× faster and, unlike CPU, scales with
  batching. Either supported model needs well under 1 GB VRAM, so it
  **can cohabit with the LLM on one 12 GB card**, but SGLang grabs the
  whole card at its default `SGLANG_MEM_FRACTION_STATIC=0.78` — this
  project's dev-box `env.sh` overrides it to `0.60` (outside the
  Install.sh SGLANG AUTOCONFIG block) alongside `EMBED_N_GPU_LAYERS=99`,
  currently serving Qwen3-Embedding-0.6B in that slot. The llama.cpp LLM
  engine (`run_llm.sh`) shares VRAM gracefully and needs no such tuning.
  It is *not* browser-facing — no `frontend/server.py` proxy route
  (permanent rule 15 only applies to endpoints the web UI calls).

### Documentation

- `CLAUDE.md` is the technical reference for how the codebase currently
  works (architecture, API, algorithm/prompt behavior) — this SKILL stays
  focused on project-management conventions and current-state facts
  organized by topic, not a duplicate of `CLAUDE.md`'s own detail.
- `DOC_ALGO/FR/ReadMe.md` is the French, user-facing explanation of the
  grid-generation algorithm — keep it in sync with `backend/crossword_gen.py`
  per permanent rule 11. Tracked in git like `CLAUDE.md`/`README.md`, never
  gitignored (permanent rule 16).
- `DOC_DIC/FR/ReadMe.md` is the French, user-facing explanation of the
  dictionary-generation pipeline (`build_sentence_corpus.py` ->
  `build_wordlist_freq.py` -> `build_gloss_dictionary.py`) — kept in sync
  the same way, per permanent rule 18.
- `DOC_USER/EN/ReadMe.md` is the English, user-facing explanation of every
  element of the web UI (`frontend/static/index.html`/`script.js`/
  `style.css`/`i18n.js`) — how it works and what it's for, for a reader
  who wants to understand the interface without reading its code — kept
  in sync the same way, per permanent rule 19. It also carries its own
  "How a grid is actually built" section — a condensed, plain-English
  summary of `DOC_ALGO/FR/ReadMe.md`'s own algorithm, one substantial
  paragraph per major stage (black-cell placement, slot-selection
  priority, candidate-word selection, backtracking, the parallel/
  incremental cross-cycle construction, final optimization) — kept in
  sync with `DOC_ALGO/FR/ReadMe.md` per permanent rule 20.
- `README.md` stays non-technical (permanent rule 5); anything about
  implementation, JSON formats, or internal module behavior belongs in
  `CLAUDE.md` instead.
- Code and code comments are written in English going forward (permanent
  rule 14) — a change from this project's own long-standing practice of
  French inline comments throughout `backend/crossword_gen.py` in
  particular, kept as-is rather than retrofitted.

18. **Keep `DOC_DIC/FR/ReadMe.md` current, on the same footing as
    `DOC_ALGO/FR/ReadMe.md`** — a French, present-tense-only, timeless
    reference explaining how `build_sentence_corpus.py`,
    `build_wordlist_freq.py`, `build_gloss_dictionary.py`, and
    `compress_reference_corpus.py` build and package this project's
    per-language dictionaries (`data/reference_corpus/`, `data/wordlist_
    <lang>_full.tsv`, `data/gloss_dictionary/`, `data/reference_corpus_
    <lang>.tar.xz`) — this list of scripts is itself illustrative, not
    exhaustive: any future script added to this same corpus/wordlist/
    gloss/packaging pipeline family falls under this same rule
    automatically, whether or not it's been named here yet (a real gap
    found live: `compress_reference_corpus.py` shipped an entire session
    without DOC_DIC ever mentioning it, specifically because this rule's
    own scope sentence only ever named the original three scripts by
    name). Whenever any pipeline script's behavior changes, update it to
    describe the new current behavior directly — the same rules already governing
    `DOC_ALGO/FR/ReadMe.md` apply here identically: no narrative ("à la
    demande explicite de l'utilisateur", "précédemment", a changed-N-times
    account, a bug-fix/incident trace — see permanent rule 11, which that
    narrative belongs in no maintained document at all), every point
    cites its source file and function (see permanent rule 13, no line
    numbers), and the
    file must always stay tracked in git, never gitignored (see permanent
    rule 16). Both `DOC_DIC/FR/ReadMe.md` and `CLAUDE.md` must stay in
    sync on *current* facts about these three scripts, exactly as already
    required between `DOC_ALGO/FR/ReadMe.md` and `CLAUDE.md` for
    `backend/crossword_gen.py`.

19. **Keep `DOC_USER/EN/ReadMe.md` current, automatically, on every change
    to the web UI** — at the user's explicit request: "Crée une
    documentation expliquant (en anglais) le fonctionnement de chaque
    élément de l'interface dans DOC_USER. Consigne dans le SKILL de
    bonnes pratiques que cette documentation doit être tenue à jour
    automatiquement à chaque modification de l'interface." An English,
    present-tense-only, timeless reference explaining what every element
    of `frontend/static/index.html` (form fields, buttons, panels, the
    playable grid) does and how a player uses it — never how the code
    implements it (that's `CLAUDE.md`'s job) and never a decision log
    (that's `CLAUDE.md`'s job too). Whenever `frontend/static/index.html`/
    `script.js`/`style.css`/`i18n.js` change in any way a user would
    notice — a new control, a changed label/behavior, a removed feature,
    a new status/error message, a new interactive state — update this
    file in the same change, not as a separate follow-up. The same rules
    already governing `DOC_ALGO/FR/ReadMe.md` apply here identically: no
    narrative ("à la demande explicite de l'utilisateur", "previously", a
    changed-N-times account, a bug-fix/incident trace — see permanent
    rule 11, which that narrative belongs in no maintained document at
    all), every point cites its source location (see permanent rule 13, no line
    numbers — e.g. "(`frontend/static/script.js`, `renderLibraryList`)"),
    and the file must always stay tracked in git, never gitignored (see
    permanent rule 16). Both `DOC_USER/EN/ReadMe.md` and `CLAUDE.md` must
    stay in sync on *current* facts about the UI, exactly as already
    required between `DOC_ALGO/FR/ReadMe.md` and `CLAUDE.md` for
    `backend/crossword_gen.py` — the two documents serve different
    readers (a player vs. a future engineer) but must never describe two
    different versions of the same interface.

20. **Keep `DOC_USER/EN/ReadMe.md`'s "How a grid is actually built" section
    current, automatically, every time a significant change is made to the
    generation algorithm** — at the user's explicit request: "Enrichis le
    DOC_USER avec un résumé du DOC_ALGO (en anglais). Ecris un gros
    paragraphe pour chaque étape (placement des cases noires, choix des
    emplacements, choix des mots, backtracking, construction en parallèle
    incrémentale, etc). Ajoute dans le SKILL de bonnes pratiques que ce
    résumé doit être mis à jour chaque fois qu'un changement important est
    effectué dans les algos." This section is a condensed, plain-English,
    player-facing summary of `DOC_ALGO/FR/ReadMe.md`'s own current algorithm
    — not a line-by-line translation, and not exhaustive the way `DOC_ALGO/
    FR/ReadMe.md` itself is, but it must never describe a stage of the
    algorithm that no longer matches current behavior. Whenever `DOC_ALGO/
    FR/ReadMe.md` itself is updated for a real algorithm change (per
    permanent rule 11), check whether this summary's own matching paragraph
    still holds — update it in the same change if not. A change small
    enough that `DOC_ALGO/FR/ReadMe.md` only needed a one-line constant/
    formula edit (e.g. a threshold or fraction changing value, with no
    change to the underlying mechanism it governs) does not by itself
    require touching this summary, which is meant to stay stable at the
    level of "what each stage does," not track every tuned constant's own
    exact current value — but a change to which mechanism exists at all, or
    to the overall shape of a stage (a new stage, a removed one, a
    fundamentally different selection/backtracking/parallelism strategy),
    always does. Same anti-narrative convention as every other timeless
    reference in this project (permanent rule 11): present tense, no "à la
    demande explicite de l'utilisateur," no changed-N-times account — this
    summary describes the *current* algorithm as if it had always worked
    this way, the story stays `CLAUDE.md`-only.

21. **Always reply to the user in French**, regardless of the language of
    their own message — at the user's explicit request. This is about the
    conversational language only, distinct from the project's own
    engineering-language rule (permanent rule 14: code/comments/this
    SKILL/README.md stay in English) and from product-content language
    (crossword words/clues and UI strings, each in whichever of the six
    supported languages applies) — neither of those changes.

22. **Never code a new algorithmic rule that the user has not validated.**
    The `DOC_ALGO/FR/` documents (`ReadMe.md` and `Lexicon.md`) are this
    project's **specification**, not just a description written after the
    fact: they state what the generation algorithm is required to do, they
    are read before any request (permanent rule 18), and they are what a
    request is measured against. The one and only real task is therefore
    to **verify that the code actually does what those documents
    require** — and to fix the code where it does not.

    Concretely:

    - A discrepancy between the code and `DOC_ALGO/FR/` is a defect in the
      CODE by default. Fix the code to match the specification; only
      change the specification when the user says the specification itself
      is what is wrong.
    - Inventing an additional rule, constraint, guard or heuristic that
      `DOC_ALGO/FR/` does not call for is out of scope, even when it looks
      like an obvious improvement, even when it fixes the symptom at hand,
      and even when it is measured to be better. Propose it — state the
      mechanism, what it changes, and the measurements — and wait for the
      user's decision before it lands.
    - The same applies to *strengthening* an existing rule (making a
      soft preference absolute, widening a check's scope, removing an
      existing exception): that is a new rule too.
    - When a fix genuinely cannot be written without some new rule, say so
      explicitly, name the rule being added, and flag it for validation in
      the same message that reports the fix — never let it pass silently
      as an implementation detail.
    - Reporting "the code does X, the specification requires Y" with
      evidence is a complete, successful piece of work on its own. It does
      not need a unilateral design decision attached to it.

23. **The Python back end (`backend/`) and the Java back end
    (`backend_java/`) evolve together, in the same change — always.** At
    the user's explicit request. They are two implementations of ONE back
    end: same port, same routes, same request validation, same JSON
    responses, same on-disk stores (`GRID_STORE/`, `GRID_WORK/`,
    `GRID_GAME/`, `STOP_DUMP/`, `SECRET/`, `LOG_*/`), same LLM prompts
    byte for byte, same generation algorithm. The middleware
    (`frontend/server.py`), the web UI, the data pipeline and the scrapers
    exist once and are shared; `run_Falcon.sh` starts the Python back end,
    `run_FalconJ.sh` the Java one, and either launcher stops the other
    version (started from this checkout) before starting its own.

    Concretely:

    - Any change to `backend/*.py` — an endpoint, a request field, a
      response key, a prompt, a filter, a constant, an algorithm rule, a
      store format — is ported to `backend_java/` in that same change, and
      vice versa. A change that only exists in one of the two is
      unfinished work, never a state to leave for later.
    - Module map: `app.py` → `App.java` (+ `Web`/`Body`/`Job`/`GenReq`
      for the HTTP layer), `clues.py` → `Clues.java`, `chatbot.py` →
      `ChatBot.java`, `grid_store.py` → `GridStore.java`, `svg_export.py`
      → `SvgExport.java`, the theme-glossary part of `app.py` →
      `Themes.java`, `crossword_gen.py` → the `falcon.gen` package
      (`Words`, `Grids`, `Filler`, `Fill`, `Cleanup`, `Generator`,
      `Interactive`), and one class per small helper module (same name,
      CamelCase).
    - Verify parity, not just "it compiles": for anything deterministic,
      send the same input to both back ends (two instances on two ports)
      and compare the outputs byte for byte — endpoint JSON, rendered
      SVGs, the full LLM prompts, the LLM-response filters. Only what is
      genuinely random (seeded searches, samplings, LLM replies) can
      differ, and there the check is behavioral (the benchmark grids still
      succeed, the flows still complete). Python string semantics must be
      reproduced on purpose where Java differs: `str.strip()`/`split()`
      strip Unicode whitespace (NBSP included — use `Py.strip`/`Py.split`,
      never `String.strip()`), `f"{x:.1f}"`/`round()` round the exact
      binary value half-even (use `Py.fmt`/`Py.round`, never
      `String.format`/`Math.round`), and regular expressions need
      `Pattern.UNICODE_CHARACTER_CLASS` to match Python's Unicode `\w`/`\s`.
    - Build: `backend_java/build.sh` (JDK 21+ and Maven, installed by
      `Install.sh`); `run_FalconJ.sh` rebuilds the jar automatically when
      a source file is newer than it. Permanent rule 8 applies to the Java
      back end too: after editing `backend_java/`, restart a running Java
      back end with `./run_FalconJ.sh`.
    - Scope: the back end only. The middleware stays Python
      (`frontend/server.py`) and serves both back ends unchanged; the
      scrapers (`scrapper/`) stay Python and the Java back end runs them
      through the project's venv for the daily RSS/SCRAPP refresh.
