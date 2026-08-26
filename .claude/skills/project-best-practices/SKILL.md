---
name: project-best-practices
description: Project management rules for CrossWordFalcon, applied and kept up to date automatically — decision log in this SKILL, requirements.txt synced with installed packages, Install.sh synced with the install procedure, README.md synced with the project's features. Invoke before/after a structuring project decision, a package install, any change to the install procedure, or adding/changing a feature.
---

# Best practices — CrossWordFalcon project

This SKILL is the living memory of this project's management rules. It must
stay current on its own — don't wait to be asked.

The project's official language is English: code identifiers, comments,
this SKILL, and README.md are written in English. User-facing product
content (the French crossword words/clues, the web UI text) stays in French
— that's the app's domain, not the project's engineering language.

## Permanent rules

1. **Update this SKILL** as soon as an important project-management decision
   is made (architecture choice, convention change, scope decision, tooling
   choice, etc.). Add a timeless line under "Decisions" below (the rule/fact
   plus why, no dates — this is standing guidance, not a changelog) before
   considering the task done.

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
   technical content belongs in a separate developer doc (to be created
   later). Never mention this auto-update rule inside `README.md` itself —
   it only lives in this SKILL.

6. **Recompute the *entire* downstream data pipeline whenever the initial
   corpus source list changes** — adding/removing/changing an OPUS source
   in `build_sentence_corpus.py` means re-running, for every affected
   language, all three stages in order: `build_sentence_corpus.py` (the
   sentence corpus itself), `build_wordlist_freq.py` (the wordlist —
   frequencies/canonical forms depend on what's in the corpus), *and*
   `build_gloss_dictionary.py` (glosses are looked up by each wordlist's
   own CANONICAL column, so a wordlist rebuild can introduce or drop
   lemmas the gloss dictionary hasn't caught up with yet). Doing only the
   first stage and treating the rest as optional/deferred leaves the
   pipeline in a silently inconsistent state — this is a standing rule
   now, not something to ask about or defer each time it comes up.

6. **Keep `env_default.sh` (project root) in sync with `env.sh`'s structure**
   whenever a notable change touches `env.sh` (a new variable, a changed
   default, a new provider block) — same variables and comments, but never
   a real secret (placeholders like `EMPTY` or `your-...-api-key-here`
   only). `env_default.sh` is checked into the repo specifically so a fresh
   clone has a safe example to copy (`cp env_default.sh env.sh`); `env.sh`
   itself stays gitignored.

7. **Restart any already-running server automatically after editing code it
   loads** — `backend/*.py` or `frontend/*.py` changes and a backend
   (`:8001`) or middleware (`:8000`) process currently up (check with
   `lsof -ti tcp:8000` / `tcp:8001`) means restart it via `./run_Falcon.sh`
   before doing anything else with it, without waiting to be asked.
   `run_Falcon.sh` starts both with plain `uvicorn` — no `--reload` — so a
   running process keeps executing whatever code was loaded at its last
   start; a live check or a user report against a stale process is
   indistinguishable from a real bug (this happened for real — see the
   `_contains_target_word` incident below). Never conclude a backend-side
   fix "doesn't work" from live testing without confirming the server was
   actually restarted after the fix landed.

8. **Bump `VERSION.txt` (project root) at the end of every completed series
   of changes** — increment the rightmost number (e.g. `0.2.0` -> `0.2.1`)
   once a task/turn's work is done and verified, not per individual file
   edit within it. The version badge in the web UI reads this file
   directly (see the version-badge decision below), so it's the one
   user-visible signal that something changed.

9. **Keep `frontend/static/i18n.js` current with every UI-visible string
   change** — any label, button, heading, status/progress message, or
   error message added, changed, or removed anywhere in `frontend/static/`
   or in a backend/proxy error path needs its `I18N` entry (or
   `describeStep()`/`describeErrorCode()` case in `script.js`) updated for
   **all five** supported languages (fr/en/de/es/it) in the same change —
   never just the language you happen to be testing in. This is the
   config the user asked to be "generated and kept up to date" — treat it
   with the same standing-sync discipline as `env_default.sh` (rule 6).

## Decisions

- two-FastAPI-server web architecture — `backend/app.py` (the
  "back end", port 8001) exposes grid generation as JSON via
  `crossword_gen.generate_grid()`; `frontend/server.py` (the "middleware",
  port 8000) serves the static page (`frontend/static/`) and proxies
  `/api/*` to the back end, so the browser only ever talks to one origin (no
  CORS). Both servers disable `/docs`, `/redoc`, `/openapi.json` and only
  respond on the strictly necessary routes/files — any other request gets
  FastAPI's default 404 (unknown routes on the back end, `StaticFiles` for
  missing files on the middleware). Dependencies added: `fastapi`,
  `uvicorn[standard]`, `httpx` (see `requirements.txt`).

- all grid-generation business logic (`crossword_gen.py`) moved
  to the root of the `backend/` directory — it must live only there (not at
  the project root, not in `frontend/`). `backend/app.py` now imports it
  with a relative import (`from .crossword_gen import ...`). The CLI stays
  directly usable: `python3 backend/crossword_gen.py --size 9 ...` (from the
  project root, so the default `data/wordlist_fr_full.tsv` path stays
  valid).

- the project and software are now called **CrossWordFalcon**
  (former name: CroosWords). Root folder renamed to `CrossWordFalcon`, the
  FastAPI API title and web page title updated, the middleware's proxy env
  var renamed to `CROSSWORDFALCON_BACKEND_URL`. Renaming the folder broke
  the `.venv` scripts (absolute paths hardcoded in shebangs): the venv had
  to be rebuilt from scratch (`rm -rf .venv && ./Install.sh`) — do this
  systematically after any move/rename of the root folder.

- this SKILL must never mention an absolute path (hardcoded,
  specific to one machine or user) — only paths relative to the project
  root (e.g. `backend/app.py`, `data/wordlist_fr_full.tsv`). Reason: an
  absolute path freezes a location specific to one machine/user, becomes
  wrong as soon as the project is cloned elsewhere or the folder is
  renamed/moved, and makes the SKILL misleading for anyone else using it.

- crossword clue generation via an LLM (`backend/clues.py`,
  called by `backend/app.py`, not by the `crossword_gen.py` CLI which stays
  deliberately offline). One HTTP request per grid (every word in the
  solution sent at once) via `httpx`, with a clue style matched to the
  `difficulty` parameter already used for the grid's vocabulary. The LLM
  endpoint is configurable via three env vars — `LLM_BASE_URL`, `LLM_MODEL`,
  `LLM_API_KEY` — read from `env.sh` (project root, **secret file, never
  commit**) and sourced by `run_Falcon.sh` before starting the servers. This
  is provider-agnostic (any OpenAI-compatible chat-completions endpoint
  works): the default points at a local server (see below); pointing it at
  a cloud API (e.g. Mistral) instead is just an env.sh edit, no code
  change. If the LLM call fails, `POST /api/generate` returns an explicit
  502 rather than a grid without clues.

- the project went live as a GitHub repo. Created `README.md`
  (root), meant to be kept up to date with every functional change just
  like `requirements.txt` and `Install.sh` (see permanent rule 5).

- rewrote `README.md` for a non-developer audience (removed the
  architecture, JSON API reference, and implementation details it initially
  had) — a separate developer doc is planned later for that technical
  content. Reason: the README is also read by people who install and use
  the app without reading the code; mixing it with technical docs made it
  hard for them to follow. The rule to keep `README.md` up to date must only
  be mentioned in this SKILL, never in the README itself.

- `backend/clues.py` was generalized from a Mistral-only client to a
  generic OpenAI-compatible one (see the env-var decision above)
  specifically so the local LLM server backing it could be swapped with no
  code change — only an `env.sh` edit.

- the project's official language switched to English. This
  SKILL was renamed from `bonnes-pratiques-projet` to
  `project-best-practices` and fully translated, and `README.md` was
  translated to English. Code identifiers were already English and needed
  no change. Product-facing content (crossword words/clues, web UI text)
  intentionally stays in French — the app's domain is French crosswords,
  independent of the project's engineering language.

- grid dimensions are now configurable independently
  (`width`/`height` in `crossword_gen.generate_grid()`, the `POST
  /api/generate` request body, and the web form), replacing the old single
  square `size` parameter. Default changed from 9×9 to 15×10
  (width×height) — a more traditional crossword shape. 180° symmetry and
  all structural-validity checks generalize unchanged to rectangles
  (`rows-1-r, cols-1-c`), so this was a pure parametrization, no algorithm
  change.

- the web UI became a playable crossword instead of a read-only
  display of the generated solution: the grid starts empty (only clue
  numbers shown), the player clicks a cell to select it (blue) and types
  letters — lowercase advances the selection right, uppercase (Shift/Caps
  Lock) advances it down, so the same keys work naturally for both across
  and down entries. A "Solution" button reveals the answer grid without
  discarding what the player typed (toggling it back restores their
  entries); a "Vérification" button color-codes typed letters against the
  solution (green/red) without revealing untyped cells. The two are
  mutually exclusive (see the `style-guide` SKILL for the visual side of
  this decision). Created the `style-guide` SKILL alongside this change to
  track UI/styling decisions separately from project-management ones (this
  SKILL) — see its own decision log for colors and interaction states.

- added grid generation and clue generation in English, German,
  Spanish, and Italian, alongside French. Word/frequency dictionaries for
  the four new languages come from the HermitDave FrequencyWords repo
  (github.com/hermitdave/FrequencyWords, CC-BY-SA, `<lang>_50k.txt`,
  `word count` per line, already sorted by frequency) — raw sources in
  `data/raw/`, converted to the project's `WORD<TAB>ACCENTED<TAB>FREQUENCY`
  format by `build_wordlist_freq.py` (accent-stripping/uppercasing/
  length-3+ rules, same for all five languages including French — see
  below). `backend/app.py` picks the wordlist by
  a new `language` request field (`WORDLISTS` dict); `backend/clues.py`
  writes the clue prompt in the matching language (`LANGUAGE_NAMES`) so
  words and clues stay in the same language. The web UI gained a language
  selector that both switches the interface's own text (`I18N` table in
  `script.js`, `data-i18n` attributes in `index.html`) and drives which
  language is requested from the API — one selector for both, since picking
  a language obviously means both directions. Chose HermitDave over
  scraping/curating a bespoke source per language: freely available, one
  consistent format across languages, good-enough frequency ranking for a
  crossword's purpose (this isn't a linguistics-grade resource like
  Lexique383, but the grids read fine in practice).

- fixed a correctness bug in `Filler._backtrack`
  (`backend/crossword_gen.py`): it called the global `random.shuffle`
  instead of the seeded `rng` passed into `generate_grid`, so `--seed` never
  actually made the CSP fill's word-choice order reproducible (only the
  black-square pattern was seeded), and concurrent web requests handled by
  FastAPI's threadpool could mutate that shared global RNG state at the
  same time — surfaced as a request that spun at 100% CPU indefinitely
  during testing. `Filler` now takes and uses a local `rng` end to end.
  While profiling this, also found and fixed `_domain()` recomputing a
  cell's position within a crossing slot via `list.index()` on every call
  (millions of times per grid) — precomputed once in
  `Filler.__init__`'s `cell_to_slots` instead, as `(slot_index, position)`
  pairs. Net effect: correct, reproducible fills, and roughly 25-30% faster
  — but generation at the new 15×10 default (see the grid-dimensions
  decision above) still commonly takes 15-35 seconds depending on
  language/luck, noticeably slower than the old 9×9 default. The CSP
  solver's Python-level per-node cost still scales with slot count; a
  deeper fix (incremental/memoized domain tracking instead of recomputing
  every unassigned slot's domain at every backtracking node) would help
  further but is a bigger change than this bug fix — flagged to the user
  rather than done speculatively.

- French now also goes through `build_wordlist_freq.py` from a
  HermitDave source (`data/raw/fr_50k.txt`), instead of Lexique383. Deleted
  `build_wordlist.py` and `data/Lexique383.tsv` (the old French-only
  pipeline) at the user's request, for consistency: all five languages are
  now built the same way from the same kind of source, none gets special
  treatment. Trade-off accepted knowingly: Lexique383 was a richer,
  linguist-curated resource (surface forms with accurate conjugation
  coverage, frequency from a books corpus) — HermitDave is a raw web/subtitle
  frequency count, lower linguistic quality, but the user prioritized
  consistency over that extra quality for French specifically.

- `_call()` always sends an explicit `max_tokens` (never relies on the
  local model server's bare default) — found by testing the local backend
  for real: without it, a grid with many words got its response truncated
  mid-answer. A missing clue for one word no longer falls back to showing
  the answer itself in the UI — see the later, superseding entry below
  ("no definition available" placeholder) for why that fallback was
  removed. Note: small local models can still drift off-language partway
  through a
  long clue list — a model-quality limit, not something a parsing fix can
  address (see the non-Latin-script filter below).

- added `frontend/static/logo.svg` (+ a rendered
  `frontend/static/logo.png` for `README.md`, produced with macOS's
  built-in `qlmanage -t`, no extra tool installed) — a simplified falcon/
  raptor head holding the idea of a crossword grid, hand-authored as flat
  SVG shapes rather than traced from a photo. Iterated directly against
  rendered output (no visual design tool available in this environment):
  getting a convincingly "hooked" beak by hand-coding bezier paths took
  many attempts — key lessons kept here in case this file is touched again:
  `stroke-linejoin="round"` rounds off every corner including ones meant to
  be sharp (use `miter` when a path needs a crisp point); smooth `Q`/`C`
  curves alone tend to render as a soft rounded blob no matter the control
  points chosen, a reliable sharp corner needs two straight (`L`) segments
  meeting at the point; layering flat shapes (dark full silhouette, then
  lighter overlays for the head/beak/brow) is far easier to get right than
  one complex multi-colored path. Logo inserted top-left of the web page
  next to the `<h1>` (`#page-header` in `index.html`/`style.css`) — see the
  `style-guide` SKILL for that placement decision.

- wordlists (`data/wordlist_<lang>_full.tsv`, produced by
  `build_wordlist_freq.py`) gained a second column: the word's natural
  accented/inflected spelling, alongside the grid's bare accent-stripped
  uppercase form. Reason: the grid form alone doesn't carry gender, number,
  or conjugation (e.g. `ETE` could be "été" or a stripped form of a verb
  form), so the LLM writing clues couldn't reliably get grammar right.
  `crossword_gen.load_wordlist()` now returns `(by_length, accents)` instead
  of just `by_length`; `generate_grid()` attaches `accented` to each entry
  in its `words` list.

- `backend/clues.py` restructured around a single class,
  `LLMClueGenerator`, at the user's request to keep the prompt-writing logic
  (`_build_prompt`) clearly separated from the HTTP/parsing plumbing
  (`_call`/`_parse_response`/`_pick_clues`) — previously a flat module
  function mixing both concerns. `backend/app.py` constructs one instance
  at module scope (`clue_generator = LLMClueGenerator()`) and reuses it
  per request. Prompt now also: asks for 3 candidate clues per word instead
  of 1 (picked at random on our side, `_pick_clues` — the model never
  chooses), forbids restating the word/spelling or a bare grammatical label
  (e.g. "verbe avoir à la deuxième personne...") as a clue, and explicitly
  allows synonyms.

- the harder 3-candidates-per-word prompt broke reliability on
  the small local model (no constrained decoding) — tested
  with a 20-word grid, it degenerated (dropped entries, off-language
  fragments, then stopped) well before hitting the token budget, going from
  ~1 empty clue in 20 (old 1-candidate prompt) to 19 empty. Root-caused with
  a direct `LLMClueGenerator._call()` test: same prompt style but only 5
  words came back perfectly every time. Fixed by (1) batching — `_BATCH_SIZE
  = 6`, `generate()` now loops over chunks and merges results instead of
  one big request, only raising `ClueGenerationError` if *every* batch
  fails; (2) lowering `TEMPERATURE` to 0.4 (was 0.8) — variety comes from
  the "3 different clues" instruction, not sampling noise, and high
  temperature was making the small model's output less stable, not more
  creative; (3) `_pick_clues` drops any candidate containing non-Latin-
  script characters (CJK/Cyrillic/Hebrew/etc. — a drift failure mode seen
  twice in testing; all 5 supported languages use the Latin alphabet).
  Verified end to end on this Mac: 20/20 usable clues, matching the
  requested "real clue, not a label" style.

- switched `backend/clues.py`'s output format from JSON to
  plain text — one line per word, `word: clue 1; clue 2; clue 3` — at the
  user's request after seeing unparsable JSON from the local model in
  practice. Dropped `response_format: json_object` from the request
  entirely (asking for JSON mode while wanting plain text would conflict).
  `_parse_response` is deliberately forgiving about what counts as a
  "word:" header: it only trusts one if the word matches a word actually
  sent in that batch, checked both case- and accent-insensitively (a model
  given "élevées" sometimes echoes "elevees" back), because a clue can
  itself contain a colon. It also tolerates a model that switches to a
  numbered/bulleted multi-line list under the header instead of the
  requested single semicolon-joined line — a real failure mode hit while
  testing this change, not a hypothetical one. Small local models still
  don't hit this format 100% of the time (occasionally drop the colon
  format entirely) — batching (see above) means one bad batch just costs
  those ~6 words their clue, not the whole grid.

- `env_default.sh` (root, checked in — see permanent rule 6) is the
  template a fresh clone copies to `env.sh`; same shape as `env.sh` but
  with placeholder credentials only, never a real key.

- version badge in the top-right of `#page-header` reads `VERSION.txt`
  (root) via `frontend/server.py`'s `/api/version` (reads the file
  directly, no backend round-trip needed for something this static) and
  `script.js` fetching it on load. Bump `VERSION.txt` for a release; the
  badge picks it up automatically.

- `frontend/server.py`'s proxy to `POST /api/generate` uses a 240s
  `httpx` timeout, not a short default — grid generation plus several LLM
  clue-generation batches can together take a few minutes on a small local
  model, and a short middleware timeout would report the backend as down
  ("Serveur back indisponible") while it was actually just still working.

- default local LLM: **llama.cpp serving a quantized GGUF (Qwen3.5-9B,
  Q4_K_M, ~5.75GB)** via `run_llm.sh` — the only local backend in the repo.
  Reasoning for the model: the user asked for "Qwen3.5 14B" specifically,
  but no official Qwen3.5 release exists at that size (checked the HF API —
  real sizes are 0.8B/2B/4B/9B/27B/MoE); 9B is the closest that still
  comfortably fits a 12GB GPU at Q4 with headroom for the KV cache, 27B
  would not (~16GB for weights alone). Reasoning for llama.cpp: it's the
  natural runtime for a GGUF file and, via `llama_cpp.server`, already
  speaks the OpenAI-compatible shape `backend/clues.py` expects — no
  hand-written wrapper needed — and has no prebuilt PyPI wheels
  (`requirements-llama.txt`) so it builds from source via CMake on both
  Linux and macOS from one package. An earlier vLLM-based default (plus a
  transformers-based macOS fallback, since vLLM ships no macOS wheels) was
  fully removed once llama.cpp covered both platforms natively — no
  reason to keep two working local-LLM code paths around, and the leftover
  vLLM-specific files/mentions were themselves a source of confusion.

- Qwen3.5 is a reasoning ("thinking") model: its chat template defaults
  to emitting a `<think>...</think>` block before the actual answer unless
  told otherwise. This silently broke clue generation at first (the model
  spent the entire token budget thinking and never produced a parsable
  `word: clue 1; clue 2; clue 3` line — verified directly: 28s and no
  usable output). Fixed by launching `llama_cpp.server` with
  `--chat_template_kwargs '{"enable_thinking": false}'` (see `run_llm.sh`)
  — verified fix: ~4s and a clean, fully parsable response. Keep this flag
  if the default model ever changes to another Qwen3-family (or other
  reasoning) model — check for the same failure mode before assuming a
  new model "just works" with the existing prompt.

- `DIFFICULTY_STYLE` (`backend/clues.py`) pairs each level with a worked
  example clue for the same word (CHAT), not just an adjective list, and
  `_build_prompt` states the difficulty up front as "the single most
  important constraint" rather than as one clause among many at the end.
  Needed because the original phrasing technically told the model the
  difficulty but didn't actually change its output: easy vs. hard clues for
  the same word came out barely distinguishable in testing. Verified after
  the fix: easy clues are short and literal ("Animal qui miaule"), hard
  ones are longer and figurative/elliptical ("Il retombe toujours sur ses
  pattes") — a real, checkable difference, not just a prompt that mentions
  the word "difficulty".

- `make_symmetric_pattern` (`backend/crossword_gen.py`) picks each black
  cell from a look-ahead window (32 still-untried cells) instead of strict
  shuffle order, choosing whichever candidate's row+column currently have
  the fewest black cells — a soft preference (never rejects a placement
  outright), tuned empirically against variance-of-black-cells-per-row/
  column: window=32 beat both smaller windows (barely better than no bias)
  and a full-list argmin at every step (paradoxically *worse* on a
  rectangular grid — ties cascade into clustering instead of spreading
  out). Motivation: a purely random placement order lets black cells pile
  up by chance in a handful of rows/columns, which reads as "walls" that
  visually split the grid into disconnected-looking blocks and forces many
  neighboring words to share the same length (length = the gap between
  black cells in that row/column). Measured effect is real but partial:
  the initial pattern's row/column black-count variance drops ~20-30%, but
  `minimize_black_squares` running afterward (driven purely by
  fillability, not aesthetics) washes out a lot of that gain — the *final*
  grid's worst-case row/column black-cell count only improves a few
  percent. Tried and rejected: (1) biasing `minimize_black_squares`'s
  removal order the same way — inconsistent effect in testing, sometimes
  slightly worse; (2) a hard per-row/column cap on black-cell count during
  construction — cut the worst-case alignment much more (e.g. columns
  with 4+ black cells dropped from ~4 to ~0.4 per grid) but cost ~55%
  more generation time and undershot the requested black ratio when the
  cap was strict, or lost most of the benefit when made lenient enough to
  hit the ratio. A real fix would need the row/column balance to survive
  minimization (e.g. score-guided removal that actively targets alignment,
  not just fillability) or a fundamentally different construction method
  (aesthetic-scored search rather than greedy random placement) — bigger
  changes than this session made; flagged rather than done speculatively.

- `run_Falcon.sh` starts the middleware (`frontend.server:app`) with
  `--host 0.0.0.0`, not the uvicorn default (`127.0.0.1`), so the UI is
  reachable from other machines on the same network, not just the one
  running it. The back end (`backend.app:app`) stays on `127.0.0.1` only —
  it has no reason to be reachable directly, the middleware is the only
  thing that talks to it. The script also tries to print the machine's LAN
  IP (`ipconfig getifaddr en0`/`en1` on macOS, `hostname -I` on Linux) so
  the user has the address to hand to another device; falls back to
  silence (not an error) if neither works, since `set -e`/`pipefail` are on
  and a missing/down network interface is a normal, not exceptional, case.

- `run_llm.sh` detects and self-heals a real `llama-cpp-python` footgun:
  `pip install` builds a CPU-only binary by default — GPU support (CUDA,
  Metal) only gets compiled in if `CMAKE_ARGS` says so at *install* time,
  and `--n_gpu_layers -1` at *run* time silently does nothing if that
  backend was never built in, regardless of what hardware is physically
  present (reported by the user: model loading on CPU despite having a
  GPU). Fix checks `llama_cpp.llama_supports_gpu_offload()` — the actual
  compiled capability, not a hardware guess — against whether a GPU should
  be there (macOS → Metal; `nvidia-smi -L` succeeding → CUDA), and if they
  disagree, force-reinstalls `llama-cpp-python[server]` with the right
  `CMAKE_ARGS` (parsed from `requirements-llama.txt` so the version stays
  in sync automatically) before starting the server. Verified the check
  itself is silent/harmless when GPU support is already present (this
  Mac): reads `True` cleanly from stdout even though the same import emits
  a page of Metal device-init logs — those go to stderr, not stdout, so
  they don't corrupt the captured value.

- The CUDA rebuild above checks for `nvcc` (or `$CUDACXX`) before
  attempting it — `nvidia-smi -L` only proves the NVIDIA *driver* is
  installed, not the CUDA *Toolkit* (a separate install that provides the
  compiler CMake needs). Hit this for real: a user's machine had the
  driver but not the toolkit, so the rebuild's `cmake` step failed with
  "No CMAKE_CUDA_COMPILER could be found". The rebuild attempt is also
  never allowed to abort the script (`if ! pip install ...; then ...` —
  not a bare command under `set -e`) — a failed GPU build falls back to
  whatever's already installed and runs on CPU, which is strictly better
  than the script refusing to start the server at all. Don't remove either
  guard when touching this: the failure mode (toolkit missing, or the
  build failing for some other reason) is a normal, expected case on a
  real user's machine, not a hypothetical.

- Every CPU-fallback message in `run_llm.sh` names the actual missing
  piece and how to install it, not just "running on CPU" — the user
  otherwise has no way to tell "there's no GPU here" apart from "the GPU
  build silently failed for a fixable reason". Missing CUDA Toolkit:
  prints the right package-manager command for the detected distro
  (`apt-get`/`dnf`/`pacman`, else a link to NVIDIA's downloads page).
  Missing Xcode Command Line Tools (macOS, needed to build with Metal):
  prints `xcode-select --install`. Generic rebuild failure (anything else
  — usually a missing `cmake` or C/C++ compiler): points at
  `build-essential`/`cmake` and says to check the error printed just
  above. Keep this pattern — name the missing thing and the fix — for any
  future fallback path added here.

- `frontend/server.py` sets `Cache-Control: no-store, no-cache,
  must-revalidate, max-age=0` (plus `Pragma`/`Expires` for older clients)
  on every response via an `@app.middleware("http")`, at the user's
  request — applies to the static files (`index.html`/`script.js`/
  `style.css`/`logo.*`) and the `/api/*` proxy routes alike, not just one
  or the other. Reason: this app is edited directly and reloaded by hand
  during development, and a browser caching a stale `script.js` is a far
  more likely and confusing failure than the extra bytes of always
  refetching are a real cost. If the app ever needs real caching (e.g. for
  the model download or some future heavy static asset), scope an
  exception to that specific route rather than removing the blanket rule.

- `backend/clues.py` filters out any candidate clue that's just the word
  itself (`_is_copy_of_word`) — case-insensitive, accent-insensitive,
  tolerant of surrounding punctuation/quotes — checked against both the
  grid's bare `answer` and the `accented` form sent to the model. Reported
  by the user as a real, recurring failure despite the prompt already
  saying not to; the prompt (`_build_prompt`) was also made more explicit
  (spells out bad examples: "CHAT", "chat", "Chat") but the filter is the
  actual guarantee — small local models don't reliably follow instructions
  just because they're stated. `generate()` now runs up to 3 rounds total,
  each one only re-querying (in fresh, smaller batches) whichever words
  still have zero surviving candidates after `_pick_clues` — whether
  because every candidate was a copy, non-Latin drift, or the model never
  answered for that word at all. Capped at 3 rounds so a word the model
  just can't do doesn't retry forever; ends up with no clue (falls back to
  showing the answer in the UI) rather than looping. Verified the retry
  path specifically with a mocked `_call` (not just the live model, which
  might not reproduce the failure on demand): first round returns only
  copies for one word, second round is confirmed to fire for *only* that
  word and produces a real clue.

- `backend/clues.py`'s `_BATCH_SIZE` dropped from 6 to 1 (one word per LLM
  call), at the user's request: even a 6-word batch was still unreliable on
  the small local model — good clues for the first word or two, then
  degrading into empty/off-topic/malformed lines for the rest of the same
  batch. All the batching/retry/parsing machinery already generalized to
  any batch size, so this was a one-line constant change, not a rewrite.
  Verified live end to end afterward on two grid sizes: 40/40 and 53/53
  words got a non-empty, non-copy clue (0 failures either way), with
  noticeably more idiomatic clues than the earlier 6-word-batch baseline.
  Trade-off, measured rather than assumed: total clue-generation time went
  up to ~2s/word (~80s for 40 words, ~110s for 53 words) since it's now one
  HTTP round-trip per word instead of one per ~6 — still comfortably inside
  `frontend/server.py`'s 240s proxy timeout for these sizes, so that
  timeout did not need raising, but a much larger grid could get closer to
  it; revisit the timeout if grid sizes grow substantially beyond the
  15×10 default.

- `build_wordlist_freq.py` filters contamination out of every language's
  wordlist (HermitDave's corpora are scraped web/subtitle text, not purely
  monolingual). **Superseded twice — read to the end before touching this
  code.** First approach: drop a target-language word if it's also among
  English's 10 000 most frequent words. Tested live and rejected: it
  deleted genuinely common target-language words that happen to coincide
  with English (French `CHAT`, `PAIN`, `MAIN`, `SON`, `PORT`, `RUE` — ~13%
  of the French list). Refined to only drop a word if it's *also* outside
  the target language's own top 10 000 — kept the French words above, but
  the user judged the whole cross-language-frequency idea "too strong" and
  asked for a completely different approach: validate each language's
  candidates against a real dictionary of that language instead of against
  another language's frequency.

- `build_wordlist_freq.py`'s contamination filter is now dictionary-based:
  each candidate is checked against a Hunspell dictionary for its own
  language (`HUNSPELL_SOURCE`: fr/en/de/es/it pairs from
  github.com/LibreOffice/dictionaries, `.dic`/`.aff` downloaded on demand
  into `data/hunspell_cache/` — gitignored, like `models/`). This carries
  no frequency information at all, so — unlike the superseded approach —
  it can never reject a word for merely being common in another language.
  Two real bugs surfaced and were fixed before this was trustworthy:
  1. First implementation used `unmunch` to pre-expand each dictionary into
     a plain wordlist, then checked membership. Live testing on French
     caught it silently dropping extremely common irregular verb forms —
     `SUIS`, `ÉTAIT`, `SAIS`, `VEUX`, `SONT`, `AVEZ`... (être/avoir/vouloir
     conjugations) — even though direct testing showed the `hunspell`
     spellchecker itself correctly recognizes all of them. Root cause:
     `unmunch`'s naive full-enumeration doesn't correctly walk this
     dictionary's affix-flag chaining for irregular verbs (`FLAG long`)
     — a tool limitation, not a dictionary gap. Fixed by dropping `unmunch`
     entirely and calling the real `hunspell` CLI spellchecker
     (`hunspell -d <dict> -G`) directly on each candidate instead — same
     dictionary, correct results, and also sidesteps `unmunch`'s
     multi-million-form combinatorial output for morphologically
     productive languages (Italian's came to 34M+ raw lines).
  2. First `hunspell`-based version still rejected ordinary German nouns
     (`haus`, `auto`, `tisch`, `stuhl`...) wholesale. Root cause:
     HermitDave lowercases every word, but German requires all nouns
     capitalized, so `hunspell` correctly flags lowercase `haus` as
     misspelled — the bug was checking only the corpus's own casing. Fixed
     by also checking a title-cased variant and accepting either; the
     `ACCENTED` column then uses whichever form actually validated (`Haus`,
     not the corpus's lowercase `haus`), so this doubles as a de-facto
     capitalization fix for German nouns rather than just a validity gate.
  Verified end to end after both fixes: French keeps `CHAT`/`PAIN`/`MAIN`/
  `SUIS`/`ÉTAIT`/`VEUX` while still dropping real English leakage (`ABOVE`,
  `AFRICA`, `AGAINST`...); German keeps `Haus`/`Auto`/`Krankenhaus`/`Tisch`
  with correct capitalization. Each language is independent (no cross-
  language dependency, unlike the superseded approach) — no required build
  order. Requires the `hunspell` CLI (`brew install hunspell` / `apt-get
  install hunspell`) only when (re)building wordlists, not at app runtime.
  Known limitation, accepted rather than solved: Hunspell's compound
  handling means some legitimate German compound nouns not literally in
  its dictionary and not covered by its compound rules may still be
  rejected — a real gap, but the alternative (no dictionary validity check
  at all) reintroduces the contamination problem this whole exercise was
  about; flagged here rather than silently accepted as perfect.

- added `data/raw/README.md`, at the user's request: documents where
  `<lang>_50k.txt` comes from (HermitDave FrequencyWords, CC-BY-SA) and
  points at `build_wordlist_freq.py` for how to regenerate a wordlist from
  it. `data/hunspell_cache/`'s origin is documented in
  `build_wordlist_freq.py` itself instead, since that cache isn't part of
  `data/raw/` (it's gitignored, derived, and downloaded on demand).

- `build_wordlist_freq.py`'s written `FREQUENCY` — the ranking signal
  `--difficulty` caps on — is now a canonical-form-weighted score, not the
  raw HermitDave count, at the user's request: subtitle corpora
  underrepresent some inflected forms that are nevertheless easy/common
  words (their example: French `déterminées`, raw frequency 108, vs. its
  infinitive `déterminer` at 3345 — 31× higher). `_stem_map` runs
  `hunspell -m` on every kept word to find its canonical form(s); when
  genuinely ambiguous (`suis` → `être` or `suivre`), the candidate that's
  itself most frequent in this same wordlist is used, exactly matching the
  "most probable canonical form" the user asked for rather than picking
  arbitrarily or always the first result. Final score: `CANONICAL_WEIGHT`
  (0.9) × canonical form's own frequency + 0.1 × the word's own raw
  frequency — the user's specified 90/10 split. Falls back to the word's
  own frequency unchanged wherever no stem can be found (self-canonical
  words like `chat`/`maison`, or `hunspell -m` giving nothing usable), so
  words unaffected by the corpus-bias problem keep their original ranking.
  Verified live: `DETERMINEES` jumped from 108 to 3021.3, its whole
  inflectional family (`déterminé`/`déterminée`/`déterminés`) similarly
  boosted while staying distinguishable from each other by their own
  frequency; all five languages still generate valid grids at every
  difficulty afterward. Noted, not fixed, as an accepted consequence of
  the exact 90/10 formula asked for: this is a two-edged blend, not a
  one-way boost — it also pulled French `SUIS`/`ÉTAIT` (both already very
  common) *down* somewhat, since `être`'s own raw frequency is lower than
  either conjugated form's.

- `run_Falcon.sh` starts both servers with plain `uvicorn` — no `--reload`.
  A user-reported "the [feature] fix doesn't work" while testing right
  after a `backend/*.py` code change is often just a stale process still
  running the pre-change bytecode, not a real regression (hit this for
  real: `_contains_target_word` already correctly caught a reported
  copy-in-clue case in an isolated test, immediately after the backend had
  been fixed — it just hadn't been restarted since the fix landed). Always
  restart via `./run_Falcon.sh` before concluding a backend-side fix
  didn't work, and check the running process's age (`ps -o etime`) against
  when the file was last edited if a "still broken" report is surprising.

- `backend/app.py`'s `POST /api/generate` became asynchronous, at the
  user's request for live progress feedback: it now starts generation as a
  background `asyncio` task and returns `{"job_id": ...}` (HTTP 202)
  immediately, rather than blocking until the whole grid+clues pipeline
  finishes. `GET /api/generate/status/{job_id}` (new) is polled by the
  frontend for progress and the eventual result. The CPU-bound
  `generate_grid()` call and the blocking LLM HTTP calls both run via
  `asyncio.to_thread` inside the background task so the event loop stays
  free to answer status polls concurrently — without this, one client
  polling for its own job's progress would itself have blocked on the very
  generation it's trying to check on. `JOBS` is a plain in-memory dict
  (single uvicorn process, no `--workers` — see `run_Falcon.sh` — so no
  locking or external store needed), bounded to 50 entries (`MAX_JOBS`) so
  a long-running process doesn't grow it forever. Each background task is
  also kept in a module-level `_BACKGROUND_TASKS` set purely so it isn't
  garbage-collected mid-run — `asyncio.create_task()` only holds a *weak*
  reference to what it schedules, a documented, easy-to-miss asyncio
  footgun.

- every notable step of grid generation (`backend/crossword_gen.py`'s
  `generate_grid()`, `backend/clues.py`'s `LLMClueGenerator.generate()`)
  now takes an optional `on_progress` callback, at the user's request that
  `backend.log` trace each step and that the same steps reach the UI's
  status message. `backend/app.py` passes one shared `progress(step,
  **data)` closure that does both: logs via the standard `logging` module
  (configured once, `logging.basicConfig` — this is what actually lands in
  `backend.log`, since `run_Falcon.sh` just redirects uvicorn's stdout/
  stderr there, no separate log-file setup needed) and updates the job's
  `step` field for the next status poll. Kept as a single generic
  `on_progress(step, **data)` shape (a short machine-readable step code
  plus loosely-typed extra data) rather than pre-formatted prose, so
  `frontend/static/script.js` can translate each step into the UI's
  current language (`describeStep()`/the `I18N` table) instead of the
  backend dictating English/French text regardless of the request's
  `language`. `generate_grid()` reports at each real phase transition
  (pattern-generation-and-fill attempt, pattern found, minimizing, grid
  ready) — not finer-grained than that, since the CSP backtracking itself
  has no natural, stably-countable checkpoints to report progress from
  mid-search. `LLMClueGenerator.generate()` reports after every single word
  attempt (`current`/`total` counts), since one-word-per-call batching
  (see above) makes that the single longest phase by far (up to a couple
  of minutes for a large grid) and the one granular, evenly-paced progress
  signal actually worth showing live.

- added `backend/svg_export.py` (`save_grid_svg`), at the user's request
  that every produced grid be saved to disk: renders one self-contained
  SVG per grid — empty puzzle (grid + clue lists) then the solved grid
  underneath — written to `GRIDS/` (project root, gitignored like
  `models/`/`data/hunspell_cache/` — generated output, not source
  content), named `<timestamp>_<language>.svg` with microsecond precision
  (plain per-second timestamps could collide once concurrent generation
  jobs became possible). Clue-line grouping (`_group_clue_lines`)
  reimplements `script.js`'s `renderClueLines()` grouping rule in Python
  by hand (one line per grid row/column, same-row/column clues chained
  with " — ") since there's no shared code path between the Python backend
  and the JS frontend — keep both in sync if that grouping rule ever
  changes. Saving is best-effort: a failure (e.g. disk full) is logged as
  a warning but never fails the actual `/api/generate` request — a durable
  copy is a nice-to-have, not the point of asking for a grid.

- extracted the inline `I18N` object out of `script.js` into its own
  `frontend/static/i18n.js`, at the user's request for a proper, explicit
  i18n config rather than translations embedded in application logic.
  Plain global script (no bundler/build step in this project), loaded
  before `script.js` in `index.html` — order in the HTML is what makes
  `I18N` available, not an import/module system. Audited for completeness
  while doing this and found one real gap: `document.title` was never
  updated on a language change (only the `<h1>`, via `data-i18n`, was) —
  fixed by setting it from `t.pageTitle` inside `applyTranslations()`.
  Also extended coverage to error messages, previously shown as raw,
  always-French backend text regardless of the UI's language: backend
  errors that can actually happen through normal use (no fillable grid,
  clue generation failing, an internal error, the middleware losing
  contact with the back end) now carry a machine-readable `error_code` /
  `{"code": ...}` alongside the existing free-text message (kept for
  `backend.log` and as a fallback), which `describeErrorCode()` in
  `script.js` maps to a localized message. Deliberately left the
  synchronous 400 validation errors (unknown `language`/`difficulty`) as
  plain French strings — they can't be triggered through the actual UI
  (the form's dropdowns only ever send valid values), so translating them
  would be effort spent on a path no real user hits.

- `backend/clues.py`'s prompt (`_build_prompt`) gained a rule against
  describing a word's spelling/letters instead of its meaning, at the
  user's request after seeing the small local model answer "Mot qui
  commence par T et se termine par EE." for `TEE` — technically true but
  not a real clue. No post-filter added for this one (unlike the copy-of-
  word / embedded-word checks above): those have a clean, deterministic
  signature to match against (the exact word appearing), while "does this
  clue describe spelling instead of meaning" doesn't have an equivalent
  reliable, language-independent pattern to check across all five
  supported languages without real risk of false positives — prompt-only,
  same as the bare-grammatical-label rule. Verified live: 6/6 regenerated
  `TEE` clues were genuine meaning-based clues afterward.

- diagnosed French `ARE` (the 100 m² land-area unit, as in "hectare")
  being defined by the small local model as the English verb "are"
  ("Seconde personne du pluriel de l'indicatif présent", "Équivalent de
  'être' au pluriel", etc.) in roughly 2/3 of attempts — a real,
  reproducible failure, but a *meaning* confusion, not a *language*
  confusion: every response stayed in fluent, grammatically correct
  French throughout, it just described the wrong concept (the English
  "to be", translated into French prose) rather than switching languages.
  Confirmed the root cause directly: appending an explicit gloss ("'are'
  is the French unit of area = 100 m², not the English verb") to the
  prompt fixed it 4/4 times, proving this is a knowledge gap about one
  rare, obscure homograph — not a prompt-following or instruction-clarity
  problem. Tried and rejected the user's proposed fix — prefixing the
  prompt with 5 short warm-up sentences in the target language, on the
  theory that it would anchor the model more firmly in that language
  before the real task: tested live, still wrong 4/6 times, because the
  model was never actually switching languages in the first place, so
  there was nothing for a language-anchoring warm-up to fix. Not shipped:
  it would add real cost (extra tokens/generation time on *every* word,
  since `_BATCH_SIZE=1` means one LLM call per word already) for no
  measured benefit. No general fix currently available: the wordlists
  carry only `word / accented spelling / frequency`, no meaning/gloss
  data to inject per-word, and adding a full bilingual dictionary just to
  disambiguate rare homographs would be a much bigger scope change than
  this one failure case justifies — accepted as a known small-model
  limitation on rare, cross-language-homograph words rather than solved.
  (Superseded by the three entries below — the "no meaning/gloss data" gap
  this entry describes was later closed.)

- added real usage-sentence grounding for `backend/clues.py`, at the
  user's request: `build_sentence_corpus.py` (original version — see the
  next entry for how its scope grew) downloaded a partial OpenSubtitles
  chunk per language and filtered it for language purity (Hunspell-based
  invalid-word-fraction check, same approach later reused for
  `build_wordlist_freq.py`'s own candidates too);
  `backend/example_sentences.py`'s `find_examples_for_words()` looks up
  real sentences containing a word's exact inflected form. Design choices
  made and verified live: (1) matched as a whole token (tokenize +
  membership check), not a raw substring, so a word like "chat" isn't
  falsely matched inside "château"; (2) at the user's explicit request,
  results are drawn at *random* across every match in the corpus, not
  just the first few encountered — implemented as reservoir sampling
  (bounded memory regardless of how common a word is, still a uniform
  sample); (3) originally scanned the corpus fresh on every call (~10-12s
  for a single grid's ~20-50 words) — recognized this was wasteful since
  `backend/app.py` runs as one long-lived process, and switched to
  indexing the *entire* language wordlist once, lazily, on first use per
  language (`_index_cache`): tokenizing every corpus line and checking
  against a large target set costs about the same per line as checking a
  small one (both are O(1) hash lookups), so indexing everything up
  front is barely slower than indexing just one grid's words would have
  been, and every later lookup in that language becomes a cached-dict
  read instead of a multi-second rescan.

- replaced the wordlist pipeline's frequency source: HermitDave
  FrequencyWords (a third-party project) is no longer read directly.
  `build_sentence_corpus.py` (new) downloads a partial chunk (50MB per
  source by default — full downloads are multi-gigabyte) of two OPUS
  corpora, OpenSubtitles and Wikipedia, per language, merges them, and
  filters out sentences likely to contain a wrong-language part;
  `compute_word_frequencies.py` (new) counts word occurrences in the
  result to produce `data/raw/<lang>_50k.txt` — same `word count` format
  HermitDave used, so `build_wordlist_freq.py` needed no changes to keep
  reading it. Motivation: HermitDave's own frequency count for French
  `are` (confused by the small LLM with the English verb "to be" — see
  above) turned out to be inflated by English-contamination occurrences,
  and there was no way to fix that without owning the counting step
  ourselves. Wikipedia was added as a *second* source deliberately, not a
  replacement for OpenSubtitles: subtitle dialogue covers colloquial/
  conjugated-verb vocabulary Wikipedia's encyclopedic register rarely
  uses, while Wikipedia covers formal/rare-but-real vocabulary (word
  families like `are`/`ares`) dialogue almost never does. Sentence-level
  language-purity filtering went through two iterations: first, an
  overall-invalid-word-fraction threshold (`MAX_INVALID_WORD_FRACTION`,
  reusing the same Hunspell-based approach as the earlier per-word
  filter); tested live and found to still let short *embedded* foreign
  quotes through (French Wikipedia text quoting English song/album/book
  titles, e.g. "The Kids are Alright est la bande originale...") since a
  2-3-word English quote inside an otherwise-French sentence doesn't push
  the whole-sentence fraction very high. Fixed by adding a second,
  complementary check: reject if any *contiguous run* of `MAX_INVALID_RUN`
  (3+) consecutive unrecognized words exists anywhere in the sentence —
  calibrated by hand (run>=2 had false positives on legitimate sentences
  with two consecutive foreign proper nouns, e.g. character names) to
  catch a genuine embedded quote/phrase without penalizing that. Verified
  end to end afterward: French dictionary word count went from ~35k
  (HermitDave-based) to ~105k (much richer coverage, e.g. previously-
  capped vocabulary now included), German went from ~39k to ~295k
  (German's extreme compounding productivity is far better captured by
  counting our own corpus than by a capped external list) — grid
  generation re-verified at multiple difficulties for all five languages
  afterward. Known, accepted residual limitation: even with Wikipedia
  added, French `are` (singular) specifically still has zero genuine
  correct-sense occurrences in a 50MB-per-source sample — its plural
  "ares" appears correctly, but nobody writes the bare land-area-unit
  singular in running text; a narrow gap, not a pipeline bug.

- `build_wordlist_freq.py`'s output gained a 4th column, `CANONICAL`
  (every distinct candidate canonical form/lemma for the word,
  semicolon-separated when there's more than one — e.g. French `SUIS` ->
  `suivre;être`), at the user's request to support looking up a real
  dictionary definition (which is indexed by lemma, not by inflected
  form) for grounding `backend/clues.py`'s prompt. Previously `_stem_map`'s
  candidate stems were used only internally, to pick the single most-
  frequent one for the frequency blend, then discarded — deliberately
  kept ALL candidates in the output instead of just that one: collapsing
  a genuinely ambiguous word to one "most likely" lemma at dictionary-
  build time would be the wrong layer to resolve the ambiguity, since the
  LLM sees the word in its actual clue-writing context and can judge
  correctly, while a frequency-based guess can't. `backend/crossword_gen.py`'s
  `load_wordlist()`/`generate_grid()` thread this through as
  `words[i]["canonical"]` (a list), alongside the existing `accented`.

- added a real dictionary-definition grounding source:
  `build_gloss_dictionary.py` (new) downloads a language's Wiktionary
  extract in full from Kaikki.org (kaikki.org — machine-readable
  Wiktionary extracts, CC-BY-SA/GFDL) and filters it down to just the
  lemmas this project's own wordlists need. Unlike
  `build_sentence_corpus.py`'s sources, these files can't be usefully
  partially downloaded — verified they're not sorted by frequency, so a
  partial download would only ever cover words starting with the first
  few letters of the alphabet — so each is downloaded in full (~3.2GB
  English, ~3.2GB French, ~3.3GB German, ~1.4GB Spanish, ~460MB Italian)
  and the raw download deleted immediately after filtering; user
  explicitly confirmed this download-size tradeoff before it was
  started. Real gotcha caught before committing to it: Kaikki's primary,
  English-Wiktionary-sourced extraction (`kaikki.org/dictionary/<Word
  Language>`) gives *English* glosses for every language's words (English
  Wiktionary's take on a French/German/Spanish/Italian word) — only
  actually useful for English itself. Fixed by using Kaikki's own-
  language Wiktionary editions instead for the other four
  (`frwiktionary`/`Français`, `dewiktionary`/`Deutsch`,
  `eswiktionary`/`Español`, `itwiktionary`/`Italiano`), verified by hand
  to give native-language definitions (e.g. French "lire" ->
  "Interpréter des informations écrites..."). `backend/gloss_lookup.py`
  (new) loads and caches the filtered result per language (same lazy,
  process-lifetime-cached pattern as `backend/example_sentences.py`);
  `backend/clues.py`'s `_build_gloss_block` looks up every candidate
  canonical form (not just one) and shows every definition found, leaving
  the LLM to resolve which one fits this specific word's context — the
  `_build_examples_block` (real usage sentences, see the entry above)
  and `_build_gloss_block` (real dictionary definitions) sections are
  both optional, appended to the prompt only when something was actually
  found for a word in that batch.

- retired `data/raw/` and `compute_word_frequencies.py`: word-frequency
  counting is now done directly inside `build_wordlist_freq.py`
  (`_count_word_frequencies`, reading `data/reference_corpus/<lang>
  _sentences.txt`), rather than as a separate script writing an
  intermediate `data/raw/<lang>_50k.txt` for `build_wordlist_freq.py` to
  read back in. That hand-off file only ever existed because the
  original HermitDave-based pipeline needed a place to put a third-party
  source; once the corpus became this project's own
  (`build_sentence_corpus.py`), persisting the frequency count as a
  separate on-disk stage between two scripts served no remaining
  purpose. `build_wordlist_freq.py`'s CLI changed accordingly: takes a
  bare `<language>` code now (`python3 build_wordlist_freq.py fr`), not
  `<source.txt> <destination.tsv>` — both paths are derived from the
  language directly. Verified the merge changed nothing else: re-ran all
  five languages, identical word counts and scores to before (e.g.
  French still 104788 words, `ARE`/`SUIS`/`DETERMINEES` unchanged).

- tested and rejected client-side parallelization of
  `backend/clues.py`'s clue-generation calls ("5 at a time"), at the
  user's request. Measured directly against the running LLM server: 5
  sequential calls took 33.3s, 5 concurrent calls (via a
  `ThreadPoolExecutor`) took 31.9s — a 1.04x "speedup", i.e. none.
  Root-caused rather than assumed: `llama_cpp.server`'s settings model
  (`ModelSettings`/`ServerSettings`) has no parallel-slot / continuous-
  batching option at all (unlike the native llama.cpp `server` binary,
  which does via `-np`/`--cont-batching`) — every request is processed
  through one sequential inference slot no matter how many concurrent
  HTTP connections the client opens. Real alternatives exist (run
  multiple `llama_cpp.server` processes, each with the full model loaded
  — ~5x the RAM per N-way concurrency; or switch to a backend that
  supports continuous batching natively) but both are a real
  infrastructure cost/change, not a code tweak — user chose not to
  pursue either for now. Don't re-attempt plain client-side concurrency
  against this server without addressing the server-side limitation
  first; it won't help.

- fixed a real bug the user reported directly: French `ABD` (score 103)
  showed up in "easy" difficulty despite being obscure. Root cause:
  `DIFFICULTY_PRESETS`/`load_wordlist()`'s vocabulary cap was applied
  *per word length* (`max_per_length`), not globally — a per-length cap
  doesn't restrict a length that has fewer total words than the cap
  itself (French has only ~700 3-letter words, so the old 600-per-length
  "easy" cap let every single one through, `ABD` included, which
  actually ranks ~33 000th by frequency across the whole lexicon).
  `load_wordlist()`/`generate_grid()` now rank *all* words together
  (regardless of length) and cap globally — `max_per_length`/
  `--max-per-length` renamed to `max_words`/`--max-words` throughout to
  match. New preset values: easy=25 000, medium=50 000 (was 600/3000 —
  not comparable numbers, since they measured completely different
  things: words-per-length vs. words-total). Verified: `ABD` (global
  rank 33123) is now excluded from easy (cutoff 25000, 33123 > 25000)
  but included at medium (cutoff 50000, 33123 < 50000) and hard
  (uncapped); grid generation re-verified across easy/medium/hard for
  French and, at easy specifically, all five languages.

- added a second, independent filter to "easy" specifically, at the
  user's follow-up request: a word must also have a findable definition
  (`backend/gloss_lookup.py`'s `has_any_gloss`, checked against both the
  word's inflected spelling and every candidate canonical form), not just
  pass the frequency-rank cutoff above. Motivation: frequency alone
  doesn't catch a word that's merely *common* yet has no real, usable
  definition — some proper nouns/loanword-like tokens pass Hunspell
  validity checking (they're spelled like real words) without being
  genuinely definable. `load_wordlist()` gained `require_gloss=False`
  (default off; `generate_grid()` passes `require_gloss=(difficulty ==
  "easy")`); language is inferred from the wordlist's filename
  (`_lang_from_path`), and the whole filter no-ops silently if that
  fails or the language has no gloss dictionary built — `require_gloss`
  must never be able to break a caller that doesn't have gloss data
  available. Real wrinkle handled deliberately: `backend/gloss_lookup.py`
  uses a relative import (`.gloss_lookup`), but `crossword_gen.py` is
  also run standalone as a CLI script
  (`python3 backend/crossword_gen.py`) with no package context to
  resolve that against — confirmed by hand that this raises a plain
  `ImportError` (not something more exotic) in that mode, so the import
  is done lazily inside a function (`_try_import_has_any_gloss`) and
  caught there, never at module scope, keeping the standalone CLI
  working. Verified live: removed 106 additional French words from
  "easy" beyond the rank cutoff alone (mostly foreign proper nouns —
  "Abby", "Beverly", "Cordelia", etc. — that Hunspell had validated as
  spelled-correctly but which Wiktionary has no entry for).

- researched (not implemented) whether `llama_cpp.server` — the default
  local LLM backend — can batch multiple clue-generation requests
  together, at the user's follow-up questions after the rejected
  client-side-parallelism attempt above. Read the actual source rather
  than assuming: `llama_cpp.server` wraps every request in a double
  `anyio.Lock()` specifically so a new request can *cancel* a
  currently-streaming one — not a missing batching flag, a deliberate
  single-sequence design. Went one level deeper: the low-level ctypes
  bindings (`llama_batch_init`, `llama_n_seq_max`, etc.) do implement
  real multi-sequence batching, but the high-level `Llama` class only
  ever sets `n_seq_max > 1` when constructed in **embedding** mode
  (`llama/llama.py`'s `__init__`) — for text generation (`embedding=False`,
  our case) the context is always single-sequence, so batching isn't a
  missing flag for chat/completion, it's a code path the library never
  exercises for that use case at all, continuous or static. Real options
  if this is ever revisited: llama.cpp's native C++ `server` binary
  (implements continuous batching properly via `-np`/`--cont-batching`),
  or hand-writing against the low-level ctypes API directly (manual
  `llama_batch`/sequence management) — both bigger undertakings than a
  config change, not attempted.

- strengthened `backend/clues.py`'s prompt with a rule that a conjugated
  verb form's clue must match its exact grammatical person/number, not
  just tense, at the user's request after seeing "On a célébré la fin
  des examens" given as a clue for `ÉTAIS` (first person singular) — a
  clue that fits *some* past-tense state but not specifically "je".
  Verified honestly rather than declared fixed: sampled 18 clues for
  `ÉTAIS` across three batches before/after the change — the rule
  clearly shifted output toward correct first-person phrasing ("Comment
  je me sentais hier", "je me situais hier") most of the time, but ~39%
  of post-fix samples still drifted into second/third person ("Comment
  te sentais-tu", "Qui existait alors"). Shipped anyway as a real,
  directionally-correct improvement, not withheld for being incomplete —
  but recorded honestly as partial. No post-filter attempted: unlike
  copy-of-word/non-Latin-script checks, verifying grammatical person
  agreement requires actually parsing the *clue text* per language, not
  a simple, reliable, five-languages-at-once pattern match.

- second iteration on the person/number-agreement rule above, at the
  user's follow-up: "Je rapprocherai les chaises" (first person future)
  given as a clue for `SERRERAIT` (third person singular *conditional*)
  showed the first fix didn't cover mood/tense, only person — a clue can
  get the person right while still using the wrong mood, or vice versa.
  Broadened the rule to require person, number, AND mood/tense together,
  added `SERRERAIT` as a second worked example alongside `ÉTAIS`, and
  added an explicit "check before answering" instruction (name the
  subject pronoun the word actually is, verify the clue fits that same
  pronoun in that same mood/tense). Verified honestly, not just re-run
  until it looked good: `SERRERAIT` went to ~0/6 wrong (from the reported
  failure) and `FERONT` (3rd plural future) was 3/4 correct, but a
  re-test of `ÉTAIS` itself came back mostly wrong again (1/6) in this
  sample — worse than the ~61% correct rate measured for it right after
  the *first* iteration. Reported this mixed result to the user rather
  than presenting only the good numbers: the rule change clearly helps
  for some verb forms (especially ones with a distinctive mood marker
  like the conditional "-rait" ending) but `être` specifically — the
  single most overloaded, highest-frequency French verb, used as both a
  full verb and an auxiliary — seems to carry stronger competing priors
  in the small model that this kind of prompt instruction doesn't
  reliably overturn. Left as a known, word-dependent limitation rather
  than iterating further on `ÉTAIS` specifically without a clear next
  idea likely to help — flagged for whoever picks this up next rather
  than silently re-tried.

- added `GRID_SAMPLES/` (project root), at the user's explicit request:
  `backend/svg_export.py`'s `save_grid_png()` renders each grid's already-
  saved SVG (`GRIDS/`) to a PNG of the same basename here via
  `rsvg-convert`. Unlike most other generated-output directories in this
  project (`GRIDS/`, `data/hunspell_cache/`, `data/reference_corpus/`,
  `models/` — all gitignored), `GRID_SAMPLES/` is deliberately checked
  into git: the user wants a growing, version-controlled visual record of
  what the app actually produces, evolving release to release, not a
  disposable local cache — confirmed explicit intent, not an oversight,
  so don't add it to `.gitignore` later out of habit-matching the other
  generated directories. Best-effort like `save_grid_svg`: a missing
  `rsvg-convert` or any conversion failure is logged as a warning by
  `backend/app.py`, never fails the request.

- un-ignored `data/gloss_dictionary/` and committed its contents (a few
  tens of MB total across all five languages), at the user's explicit
  request after the `wordlist_loaded {'word_count': 0, ...}` deploy bug
  above — confirmed first via `AskUserQuestion` which of the four
  gitignored data directories were actually worth committing.
  `data/reference_corpus/` (1.1GB) and `data/hunspell_cache/` (8MB)
  stay gitignored: the reference corpus only feeds optional LLM-clue
  example sentences and already degrades gracefully to "no examples
  found" when absent (`backend/example_sentences.py` checks
  `corpus_path.exists()`), and the hunspell cache is only ever read by
  the one-off wordlist-building scripts, never by the running app.
  `models/` also stays gitignored: it's the local LLM's GGUF weights,
  already auto-downloaded by `run_llm.sh` from HuggingFace on first run —
  committing a duplicate 5.7GB binary into git history would be pure
  waste. `data/gloss_dictionary/` was the one directory that's both
  small enough to commit reasonably and load-bearing enough at runtime
  (the "easy"-difficulty definition filter, LLM clue grounding) to be
  worth shipping directly rather than depending on every deploy
  remembering to run the separate, heavy `build_gloss_dictionary.py`.

- `README.md` leads with two headline facts, at the user's explicit
  request: runs 100% locally (no cloud account/AI subscription needed)
  and works on CPU or GPU. Placed as a short bullet list right after the
  opening paragraph, before "What you need" — these are "why use this"
  selling points, not installation detail, so they belong at the top,
  not buried in the `env.sh`/`run_llm.sh` walkthrough further down where
  the same facts were already implied but not stated plainly up front.
  While there, also fixed a stale example that still showed the old
  "3. clue" numbering format instead of the current "**3** (1) clue"
  (bold row/column number + parenthesized word number) — README.md must
  stay accurate for every UI-visible change per rule 5, and this had
  drifted since the parentheses/row-column-header changes.

- third iteration on `backend/clues.py`'s inflection-agreement prompt rule
  (see the `ÉTAIS`/`SERRERAIT` entries above), at the user's request after
  "Durée de douze mois" (a singular framing — one 12-month span) was given
  as a clue for `ANS` (plural, "years"). Extended the rule to also cover
  noun/adjective number and gender, not just verb person/mood/tense, with
  `ANS` as a third worked example. Verified honestly: of 8 sampled clues
  post-fix, only ~3 clearly read as plural ("L'une des années du
  calendrier républicain" being the strongest); several others were still
  singular-framed despite the new rule. Recorded as the same kind of
  partial, word-dependent improvement as the earlier two iterations, not
  a fix — still no post-filter, for the same reason (grammatical number
  agreement needs real per-language parsing of the clue text, not a
  pattern match).

- fourth iteration on the same inflection-agreement rule, at the user's
  request after "Cacher le vrai" (a bare infinitive — no tense of its
  own) was given as a clue for `MENTIRA` (French, third person singular
  FUTURE of "mentir" — "il/elle mentira"): the correct phrasing would be
  future-tense "Cachera le vrai". Added `MENTIRA` as a fourth worked
  example, specifically covering the future tense (the three earlier
  examples covered imperfect, conditional, and noun plurality — no
  future-tense example existed yet). Verified live: 6/6 sampled clues
  came back correctly future-tense ("Il cachera la vérité", "Elle dira le
  faux", "Elle donnera un démenti", ...) — notably cleaner than the
  ÉTAIS/ANS results, plausibly because "mentir" is a much less
  overloaded verb than "être" and future tense marks itself clearly with
  a distinctive "-ra" ending. Regression-checked `SERRERAIT` (6/6 still
  correctly conditional) and `ANS` (still roughly half clearly plural,
  matching the third iteration's own result) — no degradation from
  adding the new example.

- removed the "fall back to the bare answer" behavior for a word with no
  surviving clue, in both `frontend/static/script.js`'s `renderClueLines()`
  and `backend/svg_export.py`'s `_group_clue_lines()`, after the user
  reported `DUE` shown as its own definition. Root-caused by direct
  reproduction (8/8 clean samples in isolation) that this was *not* a
  failure of `_contains_target_word`'s copy filter — it was a separate,
  older fallback: when all 3 retry rounds in `LLMClueGenerator.generate()`
  exhaust without producing a valid clue, `backend/app.py` sets an empty
  clue string, and both renderers used to substitute the bare grid answer
  for an empty clue. That was a deliberate early design choice (see the
  now-superseded entry this replaces: "a missing clue falls back to
  showing the answer, rather than failing the whole grid") made before
  the copy/embedded-word filtering existed — once that filtering existed,
  the fallback became the one remaining path back to exactly the
  "word == definition" bug all that filtering exists to prevent. Replaced
  with a translated "no definition available" placeholder
  (`noDefinition` in `frontend/static/i18n.js`, `_NO_DEFINITION` in
  `backend/svg_export.py`, which also needed a new `language` parameter
  threaded into `_group_clue_lines()` to pick the right translation) —
  an honest placeholder instead of a silent, misleading substitution.
  Applies to any word that exhausts retries, not just `DUE` specifically.

- added real diagnostics to `backend/crossword_gen.py`'s pattern-fill
  failure logging, at the user's request after seeing a "no fillable
  grid found" (`pattern_failed`) with all 40 attempts finishing in
  ~150ms total in `backend.log` — far too fast to be an ordinary CSP
  dead end (confirmed by direct testing: a genuine failed attempt with
  the same word list/grid size takes several seconds, since the
  200,000-check deadline has to actually run out or the search has to
  actually exhaust every candidate). The old log line only said
  `attempts=40`, giving no way to tell "the search genuinely explored
  everything and failed" apart from "something degenerate made every
  attempt fail near-instantly" after the fact. `try_fill()` now accepts
  an optional `diagnostics` dict it fills in with the pattern's shape
  (`slot_count`/`length_counts`) and how the search actually ended
  (`checks` reached, `reason`: `deadline_exceeded` / `search_exhausted` /
  `no_slots`) — `generate_grid()` logs this on every failed attempt
  (`pattern_attempt_failed`) and again in the final `pattern_failed`
  summary (`last_attempt`), plus the loaded word list's own
  `length_counts` once per request (`wordlist_loaded`), so a
  `require_gloss`/`max_words` combination that starves a specific slot
  length down to very few (or zero) words is visible directly in the log
  instead of needing to be reproduced by hand next time this happens.

- fixed a real bug this new logging immediately caught on a deployed
  instance: `wordlist_loaded {'word_count': 0, ...}` — `require_gloss`
  (the "easy" difficulty default) was rejecting *every* word instead of
  no-opping, on an instance whose wordlist TSV was present but whose
  gloss dictionary (`data/gloss_dictionary/<lang>_glosses.jsonl` —
  optional, gitignored, built by the separate, heavy `build_gloss_
  dictionary.py`) hadn't been built. `backend/gloss_lookup.py`'s
  `has_any_gloss` returns `False` both for "this word has no dictionary
  entry" and for "this language has no dictionary built at all" — the two
  look identical from inside that function, and `crossword_gen.py`'s
  `require_gloss` filter only ever checked whether the *import* of
  `has_any_gloss` succeeded, not whether the language actually had a
  dictionary — so it always applied the filter, and once every word
  "has no gloss" the same way, the entire word list emptied out. CLAUDE.md
  already documented the intended fallback ("no-op if the language...
  has no gloss dictionary built") — it had just never been implemented.
  Fixed by adding `has_gloss_dictionary(language)` to
  `backend/gloss_lookup.py` (true iff that language's loaded index is
  non-empty) and gating the filter on it in `crossword_gen.py`, verified
  by removing a gloss dictionary file locally and confirming the word
  list now stays at its full size instead of emptying out (the
  `data/gloss_dictionary/` un-ignoring decision is logged separately
  above).

- `backend/svg_export.py`'s exported grids now open with a header (logo,
  "CrossWordFalcon", version, generation date, grid language, difficulty
  level), at the user's explicit request — a saved `GRIDS/*.svg` file can
  now be identified at a glance instead of relying on its filename/
  timestamp alone. The logo is embedded as a base64 `data:` URI read from
  `frontend/static/logo.png` (cached per process,
  `_logo_data_uri_cache`) rather than referenced externally, keeping the
  file genuinely self-contained like the rest of this module. Language
  and difficulty are shown using the exact same text the web UI itself
  uses (native language name from `index.html`'s `<select>`, difficulty
  label/name from `i18n.js`) — duplicated by hand in Python the same way
  `_HEADINGS` already duplicates the clue-list headings, not derived
  automatically, so keep all of these in sync if the UI strings change.
  `render_grid_svg()`/`save_grid_svg()` gained a `difficulty` parameter
  for this (threaded from `backend/app.py`'s `req.difficulty`) — verified
  by rendering a real grid to SVG, converting it to PNG with
  `rsvg-convert`, and visually inspecting the header.

- fixed `run_Falcon.sh`/`run_llm.sh` so their background server processes
  survive the launching shell/terminal closing, at the user's explicit
  request. `nohup` alone only makes the process ignore `SIGHUP` — it
  doesn't remove the job from the launching shell's own job table, so
  some shells/terminals still act on it when they exit. Added `disown`
  right after backgrounding each process, plus `< /dev/null` on stdin
  (a fully detached process has no legitimate terminal to read from) —
  verified live: after `disown`, `jobs -l` in the launching shell shows
  nothing, and `ps` confirms the servers already report `PPID=1`
  (reparented to init), independent of the shell that launched them.
  `setsid` (a more complete detachment — new session, immune to
  process-group-wide signals) was considered but isn't available on
  macOS by default (it's a Linux/util-linux tool), so this stays the
  portable, cross-platform fix rather than reaching for it.

- `backend/svg_export.py`'s `save_grid_png()` now renders `GRID_SAMPLES/`
  PNGs at 300 DPI (`PNG_DPI`) instead of `rsvg-convert`'s 96 DPI default,
  at the user's request — this is a checked-in, print-quality visual
  record (see the `GRID_SAMPLES/` decision above), not a screen-only
  preview. Implemented as `rsvg-convert -z (PNG_DPI / 96)` (zoom), not
  `--dpi-x`/`--dpi-y`: verified directly that those flags are a no-op
  here, since the exported SVG's root `<svg>` has no physical unit
  (in/mm/pt) on its width/height for librsvg to rescale against — only
  `-z` actually scales a unitless (bare CSS-px) SVG's output pixel count.
  Verified live: a 720×1046 px SVG now renders to a 2250×3269 px PNG
  (720×300/96 = 2250), the exact factor a true 300 DPI would apply to a
  document authored at the standard 96 CSS-px-per-inch baseline.

- fifth/sixth round of prompt fixes in `backend/clues.py`, at the user's
  request, addressing two distinct reported cases in one pass:
  (1) `FEES` answered "Pluriel du mot 'une fée'" — a bare grammatical
  description (naming the pluralization operation) rather than an actual
  definition, added as a second worked example under the existing
  "no bare grammatical/technical description" rule (previously only had
  the "verbe avoir..." example). (2) `SERIONS` (conditional "nous
  serions", "we would be") answered "Il existe des solutions" — a wholly
  unrelated, hallucinated sentence with no connection to "être" at all;
  this is a *meaning* error, not a grammar-agreement one, so it needed a
  new rule (none of the four inflection-agreement iterations above cover
  "the clue must actually mean the right thing"), instructing the model
  to check the gloss/example grounding block before answering rather than
  free-associating. Verified honestly, not declared fixed: the specific
  reported hallucination didn't reproduce in a 6-sample `SERIONS` re-test,
  and `FEES` went from (implicitly) reliably wrong to 5/6 correct — but a
  bare-label clue still slipped through once each for `FEES` and
  `SERIONS`, and twice `SERIONS` was answered with a completely different
  verb's conjugation ("dirions"/"dirons", from "dire", sharing the same
  "-rions"/"-rons" ending) instead of "être" — a same-family-verb
  confusion distinct from the originally reported bug, noted but not
  separately chased this round. Regression-checked `MENTIRA` (4/4) and
  `SERRERAIT` (4/4) after adding both new rules/examples — no
  degradation from the added prompt length.

- replaced the default local LLM (`run_llm.sh`) with DeepSeek-R1-Distill-
  Qwen-14B, Q4_K_M (bartowski, ~9GB), at the user's explicit request —
  Qwen3.5-9B kept as a fully supported alternative, not removed: both
  `GGUF_REPO`/`GGUF_FILE` and now `--chat_template_kwargs` are overridable
  via env vars (`LLAMA_GGUF_REPO`/`LLAMA_GGUF_FILE`/`LLAMA_CHAT_TEMPLATE_
  KWARGS`), with the exact Qwen override lines given as a ready-to-
  uncomment block in both `env_default.sh` and the user's own `env.sh`.
  Verified directly, not assumed, two things before calling this done:
  (1) inspected the GGUF's own embedded chat template (logged at server
  startup) and confirmed it never references `enable_thinking` at all —
  unlike Qwen3.5's hybrid template, DeepSeek-R1-Distill has no flag to
  disable its reasoning step, it always emits a `<think>...</think>`
  block before answering; (2) actually downloaded and ran the model
  end-to-end through `backend/clues.py` before considering the swap
  finished, not just wiring up the config. That live test surfaced two
  real, load-bearing findings that a blind swap would have missed
  entirely: **(a)** the existing `max_tokens` budget (390 for one word)
  was nowhere near enough for a `<think>` block plus answer — measured
  live, a single word's full response ran anywhere from ~300 to ~1300
  tokens — so a new `REASONING_TOKEN_BUDGET` (2048, comfortably above the
  observed high end) was added on top, and `run_llm.sh`'s `--n_ctx` was
  bumped 4096 → 8192 to fit prompt + reasoning + answer; **(b)** the raw
  `<think>` block, left in, risks false-triggering `_parse_response`'s
  header-line detection (the model reasoning about the word by name
  followed by a colon reads exactly like a "word:" header) — fixed with
  a new `_strip_reasoning()` step in `_call()` that removes everything up
  to and including `</think>` before parsing (a no-op for Qwen, which
  never emits the tag). **Reported honestly to the user, not glossed
  over**: this is dramatically slower than Qwen (measured 19-67s/word vs.
  Qwen's ~2s/word — a 7×7 grid's 16 words took roughly 8 minutes of clue
  generation alone) and, on the exact hard grammatical-agreement cases
  this project has iterated on repeatedly (`ANS` plural-noun framing,
  `SERRERAIT` conditional mood, `ÉTAIS` person), did not clearly
  outperform Qwen in a same-day side-by-side sample — the reasoning
  didn't reliably fix what it, in principle, has more "room" to get
  right. Shipped as explicitly requested (default changed, Qwen kept as
  the alternative), with this trade-off on record for whoever revisits
  the choice of default next.

- default local LLM (`run_llm.sh`) changed again, at the user's explicit
  request: Qwen3-14B, Q4_K_M (bartowski, ~9GB), replacing
  DeepSeek-R1-Distill-Qwen-14B as the default — both DeepSeek-R1-Distill
  and the original Qwen3.5-9B stay fully supported alternatives (same
  `LLAMA_GGUF_REPO`/`LLAMA_GGUF_FILE`/`LLAMA_CHAT_TEMPLATE_KWARGS`
  override mechanism, now with all three options' exact override lines
  given in both `env_default.sh` and the user's own `env.sh`). Verified
  directly rather than assumed: (1) downloaded and inspected Qwen3-14B's
  own embedded chat template at server startup — it references
  `enable_thinking` exactly like Qwen3.5's does (`{%- if enable_thinking
  is defined and enable_thinking is false %}`), so the existing
  thinking-disable mechanism applies unchanged, unlike the DeepSeek swap
  where it didn't apply at all; (2) ran real clue-generation calls
  end-to-end (both in isolation and through a full 7×7 grid via the
  actual API) before calling this done. Measured live: ~8-9s/word — much
  faster than DeepSeek-R1-Distill's 20-70s/word, but slower than
  Qwen3.5-9B's ~2s/word (larger model, same non-reasoning behavior, so
  the extra latency is pure per-token cost, not a reasoning step).
  Reported honestly, not just the speed number: quality on this
  project's known hard grammatical-agreement cases was mixed relative to
  Qwen3.5-9B's own (separately) prompt-tuned behavior — `MENTIRA`
  reverted to a bare-infinitive clue ("Cacher la réalité par des fausses
  paroles", no future tense marker) and `SERRERAIT` came back with an
  explicit "en conditionnel" tacked onto the clue text (the exact
  bare-grammatical-label pattern the prompt forbids, just phrased
  differently than the FEES case it was written against); `ANS` was
  still singular-framed, consistent with the already-documented,
  unresolved limitation. Not re-tuned specifically for Qwen3-14B this
  round — the prompt's worked examples were calibrated against whichever
  model was live at the time (originally Qwen3.5-9B), and different
  models in the same family don't necessarily generalize identically;
  left as a known gap for whoever next iterates on the clue prompt with
  this specific model active, rather than re-running the full
  ÉTAIS/SERRERAIT/MENTIRA/ANS/FEES/SERIONS calibration cycle unprompted.
  `backend/clues.py`'s `DEFAULT_LLM_MODEL` updated to match
  (`"Qwen/Qwen3-14B"`); `DEFAULT_TIMEOUT` (300s) and `REASONING_TOKEN_
  BUDGET`/`_strip_reasoning` deliberately left as-is rather than reverted
  — both are harmless, shared-safe defaults that work correctly
  regardless of which of the three supported models is actually
  configured (a fast non-reasoning model just never needs the extra
  budget/timeout headroom, it doesn't slow it down).

- added Qwen3.5-4B unquantized (`bf16`, bartowski, ~8.7GB) as a fourth
  configurable/testable local LLM option, at the user's explicit
  request — downloaded, verified its chat template references
  `enable_thinking` the same way Qwen3-14B/Qwen3.5-9B do (so the existing
  thinking-disable mechanism applies unchanged), then actually ran the
  same 5-word test set used for the two previous model swaps
  (`MENTIRA`/`SERRERAIT`/`ANS`/`FEES`/`SERIONS`) before writing anything
  down. The request was to configure and test it, not to change the
  active default — Qwen3-14B stays the default; this is documented as a
  fourth override option in `env_default.sh`/`env.sh`/`run_llm.sh`'s
  comments, same pattern as the other two alternatives. Measured live:
  ~3s/word — the fastest of all four options, close to Qwen3.5-9B's own
  ~2s/word despite being unquantized rather than 4-bit (the smaller
  parameter count dominates over the quantization difference here).
  Quality, reported honestly: `MENTIRA` came back correct (future tense,
  via a valid synonym — "cachera la vérité" for "to lie"); `ANS` was
  singular-framed as usual (the same unresolved, documented limitation
  regardless of model); but `SERRERAIT` came back in the wrong mood
  (future "fermera" instead of conditional "fermerait") and, most
  notably, `SERIONS` ("nous serions", conditional of "être") was answered
  with an entirely different, unrelated verb — "nous réussirions" (from
  "réussir", to succeed) inside an otherwise well-formed "si... alors"
  conditional sentence — the same "être gets confused with a different
  verb in the same mood/person" failure pattern already seen with
  DeepSeek-R1-Distill's `SERIONS` test, now also reproduced on a Qwen
  model. `FEES` was grammatically plural but semantically vague ("gentle,
  likeable people" — never actually conveying "fairy"/magical), a milder
  version of the same meaning-drift concern the SERIONS/meaning-hallucination
  prompt rule was written for. Overall: fastest option, but the smallest
  model of the four, with correspondingly the weakest semantic grounding
  observed so far — a real trade-off to weigh against its speed if this
  is ever considered as a default, not chased further this round since
  only configuration + testing was requested.

- default local LLM (`run_llm.sh`) changed again, at the user's explicit
  follow-up request: Qwen3.5-4B unquantized (`bf16`, bartowski, ~8.7GB)
  is now the default, replacing Qwen3-14B — which, along with Qwen3.5-9B
  and DeepSeek-R1-Distill-Qwen-14B, stays a fully supported alternative
  (same override mechanism, `env_default.sh`/`env.sh` updated so the
  now-4-way choice always shows the currently-inactive options as the
  uncomment-to-switch blocks). This model had already been downloaded and
  tested in the immediately preceding entry (configured, not yet made
  default) — the trade-offs recorded there (fastest of the four, ~3s/
  word, but weakest observed semantic grounding: `SERRERAIT`'s mood
  mismatch, `SERIONS`'s wrong-verb confusion, `FEES`'s vague meaning)
  apply unchanged here; nothing new to re-measure, this entry is the
  "made it the default" follow-through, not a new evaluation.
  `backend/clues.py`'s `DEFAULT_LLM_MODEL` updated to
  `"Qwen/Qwen3.5-4B"` to match. Verified end-to-end after switching, not
  just per-word in isolation: a real 9×9 grid (30 words) generated
  successfully through the actual running API with the new default
  live.

- default local LLM reverted back to Qwen3.5-9B, Q4_K_M (this project's
  very first default), at the user's explicit request — Qwen3.5-4B
  unquantized, Qwen3-14B, and DeepSeek-R1-Distill-Qwen-14B all remain
  fully supported alternatives (same 4-way override mechanism,
  `env_default.sh`/`env.sh`/`run_llm.sh` updated to show whichever three
  are currently inactive as the uncomment blocks). `backend/clues.py`'s
  `DEFAULT_LLM_MODEL` updated to match. No new evaluation needed — this
  model's behavior was already measured across every earlier entry in
  this log (it's the baseline every other model was compared against).

- enriched `backend/clues.py`'s inflection-agreement prompt rule with a
  new `_AGREEMENT_EXAMPLES` bank of ~20 correct worked examples, at the
  user's explicit request ("une vingtaine d'exemples précis impliquant
  des conjugaisons et des accords en nombre et en genre") — a deliberate
  shift from the established pattern of adding one negative ("bad
  example") illustration at a time after a specific reported failure, to
  adding many *positive* examples at once, on the theory that more varied
  few-shot anchors of what success looks like helps a small model beyond
  what rules-plus-counterexamples alone can teach. Covers, in French
  only (like every other worked example in this rule — the model is
  expected to generalize the underlying agreement *concept* to whichever
  language it's actually writing in): `tu`/`nous`/`il`/`vous`/`ils`
  across présent/imparfait/futur/conditionnel for regular `-er`/`-ir`/
  `-re` verbs, `être`/`avoir` specifically (the two verbs every earlier
  iteration flagged as hardest), and masculine/feminine singular/plural
  noun/adjective agreement including classic irregular plurals
  (`nouveau`→`nouveaux`, `vieux`→`vieilles`, `cheval`→`chevaux`,
  `travail`→`travaux`). Each of the 20 clues was manually checked before
  shipping — not just written and trusted — to (a) never contain the
  target word or a same-family form of it (the existing copy-filter rule
  the whole prompt already enforces), and (b) itself be phrased in the
  exact matching mood/tense/number/gender, not merely gesture at the
  right general idea (the same standard the "bad example" illustrations
  hold themselves to). Verified live on both previously-known-hard words
  (`MENTIRA`, `SERRERAIT`, `SERIONS`, `ANS`, `ÉTAIS`) and, importantly,
  brand-new words never mentioned in the added examples themselves
  (`BELLES`, `MANGERIEZ`, `CHANTAIENT`, `JOYEUSES`) — specifically to
  check the model was generalizing the *concept*, not just echoing
  memorized examples back on request. Results, reported honestly:
  `MENTIRA` and `SERRERAIT` now come back cleanly correct consistently;
  `BELLES` and `CHANTAIENT` (novel words) also came back correctly
  agreed; but `ANS` and `ÉTAIS` are unchanged (still the same known,
  unresolved limitations from earlier iterations), `SERIONS` ("être"
  again) produced a bare grammatical label in 2/6 samples despite the
  rule immediately above explicitly forbidding exactly that, and two
  other novel words, `MANGERIEZ` and `JOYEUSES`, came back without
  clearly matching mood/number either. Net assessment: a real,
  measurable improvement on several cases, generalizing beyond the
  literal added examples, but still not a fix — "être" in particular
  remains the project's single hardest recurring case across every
  model and every prompt iteration tried this project has attempted so
  far. Verified the change doesn't break anything end-to-end: restarted
  both the LLM server (back on Qwen3.5-9B, this entry's default swap)
  and the app servers, then generated a real 9×9 grid (30 words)
  successfully through the actual running API with the enriched prompt
  live.

- restructured `backend/clues.py`'s prompt in two ways at the user's
  explicit request, both purely about clarity/structure, not new rules
  or behavior. (1) Rephrased throughout for a single word, not a batch:
  the prompt used to say "each word below", list "Words:\n- word" even
  though it only ever contains one, and ask for "one line per word" —
  leftover phrasing from before `_BATCH_SIZE` was fixed at 1 (see the
  earlier entry on why: even a modest batch degraded on the small local
  model). `_build_prompt(entries, ...)` (took a list) was split into
  `_build_system_prompt(difficulty, language)` (no word-dependence at
  all) and `_build_user_message(entry, language)` (takes a single
  `(answer, accented, canonical)` tuple, not a list) — `_build_gloss_
  block`/`_build_examples_block` simplified the same way, dropping the
  now-pointless per-batch iteration. (2) Pulled every "bad"/"good"
  illustration out of the rule sentences they used to be embedded in
  (a rule and its example used to be one long run-on sentence) into a
  single, clearly delimited `=== EXAMPLES ===` block after the now-short,
  numbered rules, each illustration labeled by which rule it
  demonstrates — so a rule reads as a directive and an example reads as
  an example, rather than the two blended together the way they'd
  accumulated over many one-at-a-time additions. Verified live, not just
  read for correctness: printed the actual generated system+user
  messages to confirm the new structure renders as intended, ran several
  previously-tested words (`SERRERAIT`, `MENTIRA`, `ANS`, `FEES`) through
  the real pipeline to confirm no regression from the restructuring
  itself, then generated a full grid through the actual running API
  end-to-end.

- split `backend/clues.py`'s LLM call into a `system` message (the fixed
  instructions from `_build_system_prompt()`) and a `user` message (the
  per-word content from `_build_user_message()`), at the user's explicit
  request — previously everything was sent as a single `user` message,
  with no `system` message at all. `_call()`'s signature changed from
  `_call(prompt, max_tokens, timeout)` to `_call(system_prompt,
  user_message, max_tokens, timeout)`, sending `messages=[{"role":
  "system", ...}, {"role": "user", ...}]` to the OpenAI-compatible
  endpoint — every provider this project targets (the local llama.cpp
  server and Mistral's cloud API, see `env.sh`) supports a `system` role
  the same way. `_parse_response`'s "word: clue 1; clue 2; clue 3"
  output format was deliberately left unchanged (only the *request*
  side changed) — that format's self-labeling header line is a genuine
  correctness check (it lets `_parse_response` verify the model actually
  answered about the right word), not batch-era cruft, so there was no
  reason to touch it. Verified live end-to-end the same way as the
  restructuring entry above (real generated messages inspected, several
  words tested through the real pipeline, a full grid generated through
  the actual running API).

- fixed a real, previously undetected bug in `backend/example_sentences.py`,
  caught when the user noticed real example sentences never actually
  appeared in a shown prompt: `CORPUS_DIR` pointed at
  `data/opensubtitles_corpus/` — this corpus's name from before Wikipedia
  was added as a second source — while `build_sentence_corpus.py` has
  written to `data/reference_corpus/` this whole time. `_build_index()`
  treats a missing corpus directory as "no corpus for this language" and
  degrades gracefully (a legitimate case for a language nobody's built a
  corpus for yet) — so this failed completely silently: no error, no log
  line, just an empty examples section on every single LLM call, for
  every word, in every language, for as long as this mismatch existed.
  Confirmed directly (not assumed) that no `data/opensubtitles_corpus/`
  ever existed on disk in this session — this was a pure stale code
  reference (both the constant and the module docstring), not a leftover
  directory needing migration. Fixed by pointing `CORPUS_DIR` at
  `data/reference_corpus/`; verified live: `find_examples_for_words(['fils'],
  'fr')` now returns real sentences (previously `{}`), the regenerated
  `FILS` user-message example now shows a populated "Real example
  sentences" section, and a full 9×9 grid was generated successfully
  end-to-end through the actual running API after restarting the app
  servers. A useful reminder that "returns no examples" and "silently
  broken" look identical from the outside for a function whose whole
  contract is "absence is a normal, expected outcome" — worth
  double-checking a real, populated example the next time a
  gracefully-degrading data source is touched, rather than trusting that
  an empty result means "nothing available" by default.

- added Qwen3.8-27B (`unsloth/Qwen3.8-27B-GGUF`, `UD-Q2_K_XL` — Unsloth
  Dynamic 2-bit quant, ~9.8GB) as a fifth local LLM option, at the user's
  explicit request — configured as an alternative (`env_default.sh`/
  `env.sh`/`run_llm.sh` updated), the active default left unchanged
  (Qwen3.5-9B). Verified its chat template references `enable_thinking`
  the same way every other Qwen3/Qwen3.5 GGUF tried so far does, so the
  existing thinking-disable mechanism applies unchanged. Download was
  interrupted mid-transfer by a Wi-Fi change — the stalled `curl` process
  didn't error out on its own (a dead TCP connection that never timed
  out), so it had to be killed manually and resumed with `curl -C -`
  from its last written byte rather than restarted from scratch; verified
  directly that the resume picked up from the interrupted byte offset,
  not from zero, before letting it run to completion.
  Measured live: ~20-40s/word — slower than every other non-reasoning
  model tried (Qwen3.5-9B ~2s, Qwen3.5-4B ~3s, Qwen3-14B ~8-9s), as
  expected for a 27B model even at aggressive 2-bit quantization, but
  **the strongest clue quality observed all session** on the two hardest
  recurring cases: `ANS` (plural framing) came back correctly plural in
  4/4 samples ("Durées de douze mois", "Durées écoulées en années", ...)
  — no other model tested this session exceeded ~50% on this word;
  `SERIONS` (conditional "être") came back with correctly matching mood
  and person in 4/4 samples too, with no bare-grammatical-label slip and
  no wrong-verb-entirely substitution (both real failures seen on other
  models). `ÉTAIS` (imperfect "j'étais") remained the one exception —
  2/4 correct, wrong person or number on the other two — consistent with
  every prior finding that this specific verb form is uniquely hard
  regardless of model or prompt iteration. Not proposed as a new default
  this round (only configuration was requested), but flagged here as the
  most promising quality/speed trade-off candidate if a future request
  asks to optimize for clue-agreement quality specifically over raw
  speed.

- default local LLM changed again, at the user's explicit follow-up
  request: Qwen3.8-27B (Unsloth Dynamic `UD-Q2_K_XL`) is now the default,
  replacing Qwen3.5-9B — which, along with Qwen3.5-4B unquantized,
  Qwen3-14B, and DeepSeek-R1-Distill-Qwen-14B, stays a fully supported
  alternative (same 4-way override mechanism, `env_default.sh`/`env.sh`/
  `run_llm.sh` updated to show whichever four are currently inactive as
  the uncomment blocks). `backend/clues.py`'s `DEFAULT_LLM_MODEL` updated
  to `"Qwen/Qwen3.8-27B"` to match. No new quality evaluation needed —
  this model's behavior was already measured thoroughly in the
  immediately preceding entry (configured, not yet made default), and
  those results (4/4 correct on `ANS` and `SERIONS`, the two hardest
  recurring cases, at the cost of ~20-40s/word) stand as the basis for
  this decision. Verified end-to-end after switching, not just per-word
  in isolation: a real 9×9 grid (32 words) generated successfully
  through the actual running API with the new default live — took ~13
  minutes of clue generation, matching the measured per-word speed; the
  user checked in mid-generation ("Bloqué ?") since this is a
  noticeably longer wait than every prior default, worth keeping in mind
  when reporting progress on this model — the job genuinely was
  progressing throughout (confirmed via the live status endpoint and
  backend.log advancing word by word), not stalled.

- fixed a real gap in `Install.sh`, caught by the user hitting the exact
  runtime warning it produces ("`rsvg-convert` not found"): `librsvg`
  (which provides `rsvg-convert`) has been documented as a required
  system package ever since `backend/svg_export.py`'s `save_grid_png()`
  and the logo-rendering step were added, but `Install.sh` never actually
  installed it — a real violation of permanent rule 3 (update `Install.sh`
  whenever a system dependency is added) that had gone unnoticed because
  the failure is best-effort (a missing binary only logs a warning,
  never fails the request — see `save_grid_png()`'s own docstring).
  Fixed by adding an OS-aware install step (Homebrew on macOS; apt-get/
  dnf/pacman on Linux, same detection pattern as `run_llm.sh`'s CUDA
  toolkit check), non-fatal on failure the same way. Investigated
  before concluding this was purely an `Install.sh` gap and not a live
  bug on this machine: confirmed `rsvg-convert` actually is installed
  here (`/opt/homebrew/bin/rsvg-convert` via Homebrew), and confirmed a
  freshly generated grid through the actual running backend saved its
  PNG sample successfully with no warning — so the pasted warning was
  from an earlier point (before `librsvg` was installed on this machine,
  or a different launch context/`PATH`), not an ongoing failure; verified
  directly with `env -i bash -c 'command -v rsvg-convert'` that a
  minimal, non-interactive shell's default `PATH` does *not* include
  Homebrew's `/opt/homebrew/bin` — a plausible root cause for how a
  backend process could fail to find a binary that's genuinely installed
  system-wide, depending on how it was launched. `Install.sh` installing
  it directly removes any dependency on the launching shell's `PATH`
  having Homebrew set up correctly at the time.

- default local LLM reverted back to Qwen3.5-9B (this project's very
  first default), at the user's explicit request — Qwen3.5-4B
  unquantized, Qwen3-14B, DeepSeek-R1-Distill-Qwen-14B, and Qwen3.8-27B
  all remain fully supported alternatives (same 4-way override mechanism,
  `env_default.sh`/`env.sh`/`run_llm.sh` updated). `backend/clues.py`'s
  `DEFAULT_LLM_MODEL` updated to match. No new evaluation needed — this
  is the model every other option in this log was benchmarked against.
  Verified end-to-end after switching: a real 9×9 grid (30 words)
  generated successfully through the actual running API, back down to
  ~2 minutes total (vs. ~13 minutes with the prior Qwen3.8-27B default),
  confirming the speed reverted along with the config.

- documented Qwen3.8-27B as a recommended alternative in `README.md`, at
  the user's explicit request, gated on having a GPU with at least 12GB
  VRAM — placed right after the paragraph explaining the default model's
  GPU/CPU auto-detection in the "Generating clues" section, since that's
  where a reader deciding whether they have the hardware for a bigger
  model would already be looking. Points at the same `env.sh` override
  block documented everywhere else in this log rather than duplicating
  the model's rationale — the "why" (strongest observed clue-agreement
  quality, especially on conjugation/agreement, at the cost of speed)
  is stated briefly in the README itself and in full in this SKILL's own
  entry on the model above, not re-derived from scratch in either place.

- removed `run_llm.sh`'s own hardcoded default GGUF (`${LLAMA_GGUF_REPO:-
  bartowski/Qwen_Qwen3.5-9B-GGUF}` and its two sibling `:-` defaults), at
  the user's explicit request ("run_llm.sh must use the env.sh config,
  not the default"). This was a real, previously-unnoticed design flaw:
  every model swap this session required editing `run_llm.sh`'s hardcoded
  fallback *and* `env_default.sh`'s active block *and* `env.sh`'s active
  block in lockstep — three copies of the same fact, none of them
  authoritative, that a missed edit could silently desynchronize (e.g.
  `LLM_MODEL` naming one model while the GGUF actually served was a
  different one, with nothing to catch the mismatch). Verified the
  sourcing precedence was never actually broken before changing anything
  — built a scratch copy of `env.sh` with a different model's block
  active, sourced it, and confirmed `run_llm.sh`'s variable resolution
  already picked it up correctly — so this was a maintainability/
  duplication problem, not a functional bug in the override mechanism
  itself. Fixed by: (1) `GGUF_REPO`/`GGUF_FILE`/`CHAT_TEMPLATE_KWARGS` are
  now required (`${LLAMA_GGUF_REPO:?...}`-style, erroring clearly if
  unset) rather than defaulted; (2) `run_llm.sh` sources `env_default.sh`
  when `env.sh` doesn't exist yet, instead of relying on its own
  duplicate copy of the default — `env_default.sh` is checked in and
  always has a complete, valid four-line block active, so this loses
  nothing; (3) `env_default.sh` and `env.sh` both now set all four of
  `LLM_MODEL`/`LLAMA_GGUF_REPO`/`LLAMA_GGUF_FILE`/`LLAMA_CHAT_TEMPLATE_
  KWARGS` directly in their active block (previously the three `LLAMA_*`
  lines were commented out there too, relying on `run_llm.sh`'s now-
  removed hardcoded default — the exact redundancy being eliminated).
  Verified live: (a) with neither `env.sh` nor `env_default.sh` present,
  the script now fails immediately with a clear "not set" error instead
  of silently reaching for a stale value; (b) `./run_llm.sh` with the
  real, restored `env.sh` still launches the correct model (Qwen3.5-9B);
  (c) a full grid generated successfully end-to-end through the actual
  running API afterward. Going forward, changing the default model only
  ever requires editing `env_default.sh` (and matching `env.sh` if the
  user wants to follow suit) — never `run_llm.sh` itself.

- moved every French-specific worked example in `backend/clues.py`'s LLM
  prompt (`_AGREEMENT_EXAMPLES`, plus every "bad"/"good" illustration for
  rules 1-5 and the difficulty-style example, all previously hardcoded in
  French only regardless of the request's actual language) into
  `data/fr_prompt_config.json`, and authored four equivalent files —
  `data/{en,de,es,it}_prompt_config.json` — at the user's explicit
  request. `backend/clues.py` gained `_load_prompt_config()` (cached per
  language, falls back to `fr` if a language's file is missing) and a
  `_bullets()` helper; `_build_system_prompt()` now assembles the
  EXAMPLES section from whichever language's config was loaded instead
  of French text baked into the method itself. `DIFFICULTY_STYLE` lost
  its embedded "Example: for CHAT, ..." clause — the style descriptions
  are now pure English prose (this project's engineering language) with
  the concrete word/clue example appended dynamically from each
  language's `difficulty_examples`. Rule 4's subject-pronoun list ("je",
  "tu", "il/elle", ...) — previously hardcoded French pronouns baked
  directly into the rule's own prose, not just its examples — also moved
  to config (`subject_pronouns`), one native pronoun set per language.
  Verified the refactor itself introduced no regression before writing
  any new content: rebuilt French's config as a byte-for-byte extraction
  of the existing, already-validated text (confirmed by diffing the
  freshly generated French system prompt against the pre-refactor
  version — identical after fixing one cosmetic capitalization slip in
  rule 2's "Bad:"/"Good:" prefix, present in all 5 languages' files).
  The four new languages' content was authored to fit each language's
  *own* grammar, not a French-shaped template forced onto it: English
  and German have no single-word synthetic future or conditional for
  most verbs (unlike French/Spanish/Italian, which all have genuine
  `mentira`/`serrerait`-style one-word forms), so their `rule4_bad`/
  `rule4_good` lean on what those two languages actually possess instead
  — English modal auxiliaries (`WILL`, `WOULD`, `COULD`) and the simple-
  past-vs-past-participle distinction (`SANG` vs `SUNG`, a genuinely
  tricky, common English error); German's Konjunktiv II, which *does*
  give a handful of common irregular verbs (`sein`, `haben`, `kommen`) a
  real single-word conditional (`WÄRE`, `HÄTTE`, `KÄME`) even though
  regular weak verbs don't have one; irregular plurals in both (`MICE`,
  `GEESE`, `CHILDREN` / `PFERDE`, `BÜCHER`). Spanish and Italian, being
  full-conjugation Romance languages like French, could mirror the
  French structure closely (`mentirá`/`mentirà`, `apretaría`/
  `stringerebbe` as direct `serrerait`-equivalents). Every one of the
  ~20 agreement examples per language (100 total across the 4 new files)
  was manually checked the same way the original French set was: never
  containing the target word or an obvious same-family form, and
  actually phrased in the mood/tense/number/gender it claims to
  demonstrate — caught and fixed a few same-family leaks while drafting
  (e.g. an early Spanish `HABLARÍAIS` draft used "la palabra" — "the
  floor/word" — which shares a root with `hablar`, replaced; an early
  Spanish `SERÍAN` draft used "a ser" — literally the infinitive of the
  target's own root — replaced with `convertirse en`). Verified live,
  not just read for correctness: generated the full system prompt for
  all 5 languages and read every one in full; ran an isolated
  `generate()` call per language (fr/en/de/es/it, using real words from
  each language's own wordlist) through the actual running local LLM
  server and confirmed every one returned a valid, parseable clue; then
  generated a full 9×9 grid end-to-end in German specifically (the
  language that most exercises the newly-added non-French path) through
  the actual running API, and spot-checked several of its real clues for
  basic grammatical coherence (e.g. a past-tense clue correctly paired
  with a past-tense-marked answer).

- flattened `data/<lang>_prompt_config.json`'s schema from one list per
  rule number (`rule1_bad`/`rule2_bad`/`rule2_good`/`rule3_bad`/
  `rule4_bad`/`rule4_good`/`rule5_bad`) to just two lists, `rule_bad` and
  `rule_good`, at the user's explicit request — the per-rule-number
  contract from the entry above forced every language to supply exactly
  the same shape of illustration for exactly the same rules, which
  doesn't hold up: different languages legitimately need different
  *numbers* of examples for a given point (or an example that doesn't
  map cleanly onto a single rule), so a rigid 7-key-per-language
  structure was the wrong fit almost immediately. `backend/clues.py`'s
  `_build_system_prompt()` updated to match: the five "Rule N (...) —
  bad/good:" subsections collapsed into two, "Examples of what NOT to
  do:" (`rule_bad`) and "Examples of what TO do (...):" (`rule_good`) —
  each bad/good example's own text already names or implies which rule
  it illustrates (e.g. "naming the grammatical operation... is still a
  label, not a clue" is unambiguously about the no-bare-label rule even
  without an explicit "Rule 2" header), so the explicit per-rule grouping
  wasn't pulling its weight once the schema no longer required it.
  Content itself is unchanged, just regrouped — rule2's bullets lost the
  "Bad: "/"Good: " sentence-prefix they used to need (now redundant with
  the two-section headers) and were recapitalized ("for FEES" → "For
  FEES") to read correctly as standalone bullets, matching every other
  bullet's existing capitalization; every other example's text is
  byte-identical to before. Verified live: rebuilt and read the full
  system prompt for all 5 languages after the schema change, ran a real
  `generate()` call through the actual LLM server, and restarted the app
  servers.

- added a `GET /api/system_info` endpoint (`backend/system_info.py`) and
  a hover-triggered info badge in the web UI, at the user's explicit
  request — reports the LLM model in use, whether it's likely running on
  CPU or GPU, and (if GPU) the GPU's name and VRAM/unified memory.
  Detection is local-machine hardware probing (`nvidia-smi`, or on macOS
  `system_profiler`/`sysctl`), not a live query of the separate LLM
  server process (`llama_cpp.server` exposes no such endpoint) — a
  documented, accepted approximation (see the CLAUDE.md entry for the
  exact edge case: `run_llm.sh` falling back to CPU despite a GPU being
  present would make this endpoint overstate GPU usage). Verified a real,
  non-obvious platform detail before writing the Apple Silicon branch,
  rather than assuming symmetry with the NVIDIA branch: `system_profiler
  SPDisplaysDataType` reports a `VRAM (Total)` line for discrete GPUs but
  *no VRAM line at all* on this session's own Apple Silicon Mac (checked
  directly, not assumed) — Apple Silicon has no dedicated video memory to
  report, so `sysctl -n hw.memsize` (total system RAM) is used instead,
  tagged `unified_memory: true` so the frontend labels it correctly
  rather than presenting shared system RAM as if it were dedicated VRAM.
  `frontend/server.py` proxies the new route the same way as the
  existing `/api/*` routes (each one explicitly declared, no wildcard
  proxy in this codebase). Frontend: `script.js` fetches `/api/system_
  info` once on page load and redraws (not re-fetches) the tooltip text
  on every UI language change, via a new `data-i18n-aria` convention
  (parallel to the existing `data-i18n` for text content) for the
  badge's accessible label — see the style-guide SKILL for the visual
  details. **This session's environment has no browser-automation
  tooling** (`chromium-cli`, `node`, and Python `playwright` are all
  absent) — verified as thoroughly as possible without one (served HTML/
  CSS inspected directly, `renderSystemInfoTooltip()`'s logic hand-traced
  against the real `/api/system_info` response, both correct), but an
  actual visual/hover check in a real browser is still owed and was
  explicitly flagged to the user as not done, rather than silently
  skipped or falsely claimed.

- fixed three real bad clues the user reported by hand from live use
  (French `MAMANS`/`SEMAI`/`TENU` — see the CLAUDE.md entry for the full
  before/after text). Distinguished two different bug classes rather than
  treating all three the same way: `MAMANS`'s clue leaked the word's own
  singular root ("maman") — a same-family-word case rule 1 already
  forbade in the prompt but the code-side filter never actually checked
  for, since `_contains_target_word` only compared against the exact
  target spelling. Fixed in code: it now also checks the word's
  `canonical` form(s) (already computed per word for gloss lookup), so a
  root-form leak like this is mechanically blocked, not just prompt-
  requested. `SEMAI` (a generic infinitive-style "Action de..." clue for
  a specific passé simple form) and `TENU` (a feminine subject noun
  "maison" for a masculine participle) are a different class — neither
  clue contains the target word or its root literally, so no containment
  filter could ever catch them; both are rule-4 grammar-agreement misses,
  the same category already documented as a known, accepted small-model
  reliability ceiling. Fix there is prompt-only: rule 4's text now names
  both specific traps explicitly, and one new `rule_bad` illustration per
  trap was added per language, only where that language's own grammar
  can actually produce the trap (the infinitive-vs-conjugated trap in all
  5 languages; the gender-disagreement trap only in French/Spanish/
  Italian, since German predicative adjectives don't inflect for gender
  and English has none). Verified live, not just read back: resampled the
  exact reported words several times each through the real local LLM
  server after the change — no more root leak, correct gender agreement,
  and correct passé simple tense on repeat (one still-imperfect sample
  appeared once, consistent with this being a reliability ceiling, not a
  fully solved problem — reported honestly rather than claiming a
  complete fix).

- default LLM switched to Qwen3-14B (Q4_K_M), at the user's explicit
  request — was Qwen3.5-9B. `env_default.sh`/`env.sh`'s active block and
  `backend/clues.py`'s `DEFAULT_LLM_MODEL` fallback both updated together
  (the same "keep all four LLAMA_*/LLM_MODEL lines in sync as a group"
  rule as every previous model swap this project has done — see
  `run_llm.sh`'s no-hardcoded-default architecture). The GGUF was already
  cached locally from earlier evaluation of this same model (`models/
  Qwen_Qwen3-14B-Q4_K_M.gguf`, ~9GB), so no re-download was needed.
  Verified live: killed the old Qwen3.5-9B `llama_cpp.server` process,
  restarted via `./run_llm.sh` (picked up the new active block), waited
  for `/v1/models` to report `Qwen_Qwen3-14B-Q4_K_M.gguf`, then restarted
  the app servers via `./run_Falcon.sh` and confirmed `/api/system_info`
  reports `"llm_model":"Qwen/Qwen3-14B"`.

- fixed another real bad clue reported by hand: French `LÉGALE` (FEMININE
  singular, "lawful") got "Qualifie un contrat établi selon les règles du
  droit" — "contrat" is masculine, the mirror-image case of the earlier
  `TENU` bug (masculine target, feminine noun), just in the opposite
  direction. Same conclusion as `TENU`/`SEMAI`: the clue never contains
  the target word or its root, so no containment filter could ever catch
  this — it's rule 4's grammar-agreement gap again, not a new bug class.
  Also noticed while fixing this: every language's `rule_good` bank had a
  masculine-singular example (`GRAND`/`GRANDE`/`GRANDE`) but no feminine-
  singular one — every feminine illustration jumped straight to plural,
  so the model never saw a worked feminine-singular example specifically.
  Added one `rule_bad` (the LÉGALE-style reverse-direction mismatch) and
  one `rule_good` (a correctly-agreed feminine-singular clue) to French,
  and mirrored both to Spanish (`NUEVA`) and Italian (`NUOVA`) — the two
  other languages with the same adjective/participle gender-agreement
  grammar, same pattern as the earlier `TENU`/`CANSADO`/`STANCO` fix.
  Verified live: resampled `LÉGALE` three times through the real local
  LLM server after the change — no more masculine-noun mismatch (two
  samples used a correctly feminine noun, one avoided a noun entirely) —
  reported as an improvement consistent with the known small-model
  grammar-agreement ceiling, not a guaranteed fix for every future word.

- added diagnostic logging (`backend/clues.py`'s new `crosswordfalcon.
  clues` logger) for the "no definition available" placeholder, at the
  user's request, after they reported it recurring on specific words
  (`MESURONS`/`SES`/`TEL`) and asked whether it was a temporary glitch.
  The logging itself immediately answered that question on the first
  live test: not temporary/random at all — those exact words failed
  100% of the time (3 words × 3 retry rounds = 9/9 failures) for one
  concrete, reproducible reason: the model was echoing the system
  prompt's literal format-template placeholder ("word:") instead of
  substituting the real word before the colon, which the parser's
  known-word check correctly refused to trust (by design — otherwise a
  colon inside a clue's own sentence could get mistaken for a new
  header). Fixed at the root: `_build_system_prompt()` now spells out
  the format-line example using the call's *actual* word instead of the
  ambiguous literal placeholder text, removing the ambiguity entirely.
  Also added narrow defense-in-depth in `_parse_response()` for any
  future variant of the same slip (see CLAUDE.md for the exact
  mechanism). Verified live: resampled all 3 reported words — 3/3
  resolved cleanly on the first round afterward, vs. 9/9 failures before
  the fix. This is the kind of finding this project values highly: the
  user asked only for *visibility* into a mystery, and the visibility
  itself immediately located and let us fix the actual bug — logging
  requests like this should always be treated as a potential root-cause
  hunt, not just box-ticking instrumentation.

  While stress-testing that fix against a broader word list (not just
  the 3 reported ones), a related leak surfaced on its own: French
  `MAISON` came back with the literal candidate `"clue 2"` — same root
  cause (the model echoing the format template's own placeholder text),
  different spot (one of the 3 clue slots, not the word header), and a
  second variant where the placeholder survives as a prefix in front of
  an otherwise real clue (`"clue 3: Édifice destiné à..."`). Neither
  passed any existing filter (not a copy of the answer, not non-Latin),
  so it would have been shown to players verbatim. Fixed both forms —
  see CLAUDE.md for the two regexes and the new `_clean_candidate()`
  helper. Verified live: resampled `MAISON` 11 times across both fixes
  (no further leaks), then re-ran a 15-word stress list end-to-end —
  15/15 resolved, zero warnings logged. Worth noting for next time: two
  distinct, real bugs were found in one debugging session purely because
  the fix for the first one was stress-tested past the exact reported
  words instead of stopping once those 3 passed — worth doing again
  whenever a similar parsing/prompt fix is made.

- default LLM reverted to Qwen3.5-9B, at the user's explicit request —
  was Qwen3-14B (see the earlier switch-to-Qwen3-14B entry above). The
  user found Qwen3.5-9B's clue quality better in practice despite
  Qwen3-14B's larger size, the opposite of this project's earlier
  general pattern (bigger/slower model tried so far = better clues) —
  recorded as-is, not second-guessed: quality here is inherently a
  subjective, per-user judgment call this project has always deferred
  to (see every previous model swap in this log). `env_default.sh`/
  `env.sh`'s active block reverted to the Qwen3.5-9B four-line group
  (Qwen3-14B kept as a commented alternative, same as every other model
  this project has tried), `backend/clues.py`'s `DEFAULT_LLM_MODEL`
  fallback reverted to match. Verified live: killed the Qwen3-14B
  `llama_cpp.server` process, restarted via `./run_llm.sh`, waited for
  `/v1/models` to report `Qwen_Qwen3.5-9B-Q4_K_M.gguf`, restarted the
  app servers, confirmed `/api/system_info` reports
  `"llm_model":"Qwen/Qwen3.5-9B"`.

- redesigned `backend/clues.py`'s LLM response format entirely, at the
  user's explicit suggestion, rather than continuing to patch the old
  one. Their reasoning: since `_BATCH_SIZE = 1` means every call is
  already about exactly one word, there was never a real need for the
  model to echo that word back as a header to match a response against
  — asking for a structured `"word: clue 1; clue 2; clue 3"` line was
  pure unnecessary risk once batching was already down to one word per
  call. This was validated by the session's own two immediately-prior
  incidents (the "word:" placeholder echo and the "clue N" leak, both
  logged above): both were the *same* underlying design flaw (a
  structured format the model could get wrong) surfacing twice in a
  row. Redesign: the model now writes exactly 3 plain lines, one clue
  per line, nothing else; `_parse_response()` just splits on newlines
  and trusts every non-empty line directly — no header, no delimiter,
  no template text left to echo or leak. This retired 5 things outright
  (`_WORD_LINE_RE`, `_LIST_ITEM_RE`, `_LEAKED_TEMPLATE_RE`,
  `_LEAKED_TEMPLATE_PREFIX_RE`, `_clean_candidate()` — all deleted, not
  left as unused code) and simplified `_pick_clues()` into `_pick_clue()`
  (one word's candidates at a time, matching what `_BATCH_SIZE = 1`
  already meant in practice). See the CLAUDE.md entry for the full
  before/after. Worth remembering for next time: when the same class of
  bug is fixed twice in one session, that's a signal to ask whether the
  *design* is wrong, not just the latest symptom — the user caught this
  pattern here before a third incident would have forced the same
  question. Verified live: full 15-word stress list re-run (15/15, zero
  warnings), `_parse_response()` unit-tested directly against numbered/
  bulleted lines, an empty response, and a single bare line, and a real
  20-word grid generated end-to-end through the actual running API
  (`POST /api/generate`, polled to completion) — 20/20 words got a
  clue, zero warnings, SVG/PNG saved successfully.

- added a hard clue-length cap (`MAX_CLUE_WORDS = 20`) and strengthened
  the language-consistency instruction, at the user's explicit two-part
  request, after they reported a real clue that came back as a multi-
  sentence English reasoning trace instead of a short definition (a
  hard/ambiguous word — the model thinking out loud about whether it
  might be an abbreviation, even quoting the system prompt's own
  instructions back). Fixed both structurally and via the prompt, not
  one or the other: `_pick_clue()` now rejects any candidate over
  `MAX_CLUE_WORDS` words outright (language-agnostic — doesn't try to
  detect "sounds like reasoning" text, just caps length, since no
  legitimate clue is ever that long), and `_build_system_prompt()`'s
  rule 7 explicitly states that same limit and forbids writing out
  reasoning/discussing the word's letters/quoting the instructions; a
  new rule 8 requires every clue to stay entirely in the target
  language from start to finish. Verified live: unit-tested
  `_pick_clue()` against the exact reported 30-word example (rejected)
  and a normal short clue (kept), then ran a 10-word batch through the
  real LLM server including several abbreviation-like words
  (`ABC`/`ETC`/`ONU`/`PDG` — the exact kind of word likely to trigger
  this "is it an abbreviation?" spiral) — 10/10 resolved, all well
  under the cap, no reasoning leaks or language drift.

- added always-on raw-response logging, at the user's explicit request,
  after a report from a separately deployed instance that couldn't be
  fully diagnosed from the existing warning-only logging alone: a word
  ended up with no clue, and the log seemed to show a nearby success
  right after its round-1 failure, but this session's own read of
  `generate()`'s retry design suggests that's very likely a *different*
  word's unrelated success (rounds process the whole pending list once
  before circling back to any failure), not proof the reported word was
  actually retried successfully. Rather than keep reasoning about log
  timing without more data, `_call()` now logs the LLM's exact,
  unmodified response for every single call, unconditionally, before
  `_strip_reasoning()` or any of `generate()`'s own parsing/filtering
  ever runs — so a deployed instance's log always has the ground truth
  for what the model actually said. Verified live: called `generate()`
  directly for the exact reported word (`CHIER`) with logging enabled —
  confirmed the raw response line fires before any success/failure
  verdict, for every call. Still waiting on the user to confirm from
  their own deployed log whether the original report was this logging
  gap or a genuine bug — this addition should make that determinable
  next time either way.

- fixed another real bad clue reported by hand: French `RASÉE` (FEMININE
  past participle of "raser", "to shave") got "Il est rasé de près pour
  la fête" — leaking "rasé", a different inflection (masculine) of this
  exact same word, not a different lexeme. The user explicitly asked for
  this to be fixed via *forbidden examples* (prompt-level), not a
  code-level structural filter, and explained why: a code-level ban on
  "any inflected variant of the target's own lemma" would also catch
  legitimate, hard-to-avoid auxiliary usage for words whose canonical
  lemma is a fundamental verb like "être" (a different form of "être"
  shows up almost everywhere in French, including in valid clues for
  other être-conjugations) — this project's existing `_contains_target_
  word` only matches the exact answer/accented spelling and the known
  canonical form(s), deliberately not a full morphological generator,
  for exactly this reason. Fix is prompt-only, matching the established
  pattern for this whole class of rule-1/rule-4 grammar traps
  (`TENU`/`SEMAI`/`LÉGALE` before it): rule 1's text in
  `_build_system_prompt()` now explicitly names "a different inflection
  of this exact same word" as forbidden, not just an unrelated same-
  family word, and one new `rule_bad` illustration was added per
  language with adjective/participle gender inflection — `RASÉE`/
  `AFEITADA`/`RASATA` in French/Spanish/Italian (German/English don't
  have this trap, same reasoning as the earlier `TENU`/`CANSADO`/
  `STANCO` case: no predicative gender agreement in either language).
  Verified live: resampled `RASÉE` 5 times through the real local LLM
  server — no leak of "rasé"/"raser" in any sample — then re-tested
  `TENU`/`LÉGALE`/`MAMANS` (all previously-fixed cases) to confirm no
  regression from the rule 1 wording change.

- added a third OPUS source (Books — literary prose) to `build_sentence_
  corpus.py` and reprocessed all 5 languages' corpora/wordlists end to
  end, at the user's explicit request. Also added, per the same request:
  a raw per-source sentence cache under `CORPUS/` (project root,
  gitignored) so a future reprocessing pass doesn't need to re-download
  from opus.nlpl.eu — a source already cached there is read from disk
  instead; and a 50% score penalty for likely proper nouns
  (`PROPER_NOUN_SCORE_FACTOR` in `build_wordlist_freq.py`), after a
  report that "easy" difficulty grids had too many of them — detected
  via the existing as-is-vs-title-cased Hunspell signal (a word only
  valid capitalized, in a language where common words aren't normally
  capitalized), deliberately *not* applied to German, where every noun
  requires capitalization regardless of common/proper status, so the
  same signal carries no information there. See the CLAUDE.md entries
  for both scripts for full detail (URL verification, the exact
  detection/degradation logic, worked examples).

  Sequencing mattered here: verified the OPUS Books URL pattern resolves
  for all 5 languages, then ran a tiny (`--max-bytes 2000000`) smoke test
  for Italian through both scripts end-to-end (confirmed the new source
  downloads/merges/caches correctly, and that a second run reuses the
  cache instead of re-downloading), deleted that smoke-test's tiny cache
  and output (so the real run wouldn't wrongly reuse 2MB of cached data
  instead of downloading the full default 50MB), *then* launched the
  real, full 5-language reprocessing as a background job (~7 minutes
  total for all 5 languages — much faster than expected going in).
  Verified live afterward: `git status` confirms `CORPUS/` never shows as
  untracked (properly gitignored); generated real grids end-to-end from
  the freshly-regenerated `wordlist_fr_full.tsv`/`wordlist_de_full.tsv`
  through the actual `generate_grid()` function; confirmed German's
  summary line correctly omits the "(N likely proper nouns...)" suffix
  the other 4 languages show, matching `PROPER_NOUN_LANGS`'s design.
  Noticed and flagged to the user, but did *not* act on unprompted since
  it's a separate, much larger undertaking (multi-GB per language — see
  `build_gloss_dictionary.py`'s own entry in CLAUDE.md): the gloss
  dictionaries (`data/gloss_dictionary/<lang>_glosses.jsonl`, used to
  gate "easy" difficulty via `require_gloss`) are now slightly stale
  relative to the regenerated wordlists — measured directly for French,
  "easy" mode still finds gloss coverage for 109,088 of 112,385 words
  (~97%, a ~3% drop from full coverage, mostly words newly introduced or
  newly re-ranked by the Books source), a small, non-blocking gap, not a
  functional regression, but worth a rebuild next time someone wants full
  coverage of what the Books corpus specifically added.

- the "why does `data/reference_corpus/` look unmodified" question above
  led to the user asking to rebuild `data/gloss_dictionary/` too ("Idem
  pour data/gloss_dictionary"), then stating directly: recomputing the
  *entire* downstream pipeline is standing best practice whenever the
  initial corpus source list changes, not just the first stage — now
  codified as permanent rule 6 in this SKILL, and as a `feedback`-type
  memory (`feedback_pipeline_recompute.md`) so it carries into future
  sessions on this project even outside this SKILL. The gap this closed:
  the Books-corpus addition earlier in this session rebuilt the sentence
  corpus and wordlist for all 5 languages but left the gloss dictionaries
  (dated the day before, confirmed via their file timestamps) untouched —
  correctly flagged as a known gap at the time, but treated as optional/
  deferred rather than part of the same change, which is exactly the
  framing the user corrected.

  Separately, mid-rebuild, the user asked for the same raw-download
  caching `build_sentence_corpus.py`'s `CORPUS/` already does: keep each
  language's full Kaikki/Wiktionary dump under `DICS/` (project root,
  gitignored) instead of deleting it right after filtering, so a later
  gloss-dictionary rebuild re-filters an already-downloaded dump instead
  of re-fetching several gigabytes per language. Caught mid-flight: the
  first rebuild attempt (without this change yet) was already downloading
  French's ~3.2GB dump when the request came in — killed that process
  tree (the outer bash script, the Python process, and its `curl` child)
  and deleted the resulting `.part` file rather than let it finish under
  the old delete-after-use code, so every one of the 5 languages —
  including French — would get a genuine `DICS/` cache from a clean
  restart, instead of French alone ending up inconsistent with the other
  four. Verified live: confirmed via `ps`/`pgrep` that all 3 processes
  were actually gone before deleting the partial file and relaunching;
  confirmed the relaunched run correctly re-downloads (no stale cache to
  find yet) rather than silently no-op'ing.

  All 5 languages' gloss dictionaries finished rebuilding against the
  Books-updated wordlists — French/English/German/Spanish/Italian's
  `data/gloss_dictionary/<lang>_glosses.jsonl` all show as modified in
  git (as expected — unlike `CORPUS/`/`DICS/`/`data/reference_corpus/`,
  these are checked in). `DICS/` ended up ~11GB total (`de`/`en`/`fr`
  ~3.2-3.3GB each, `es` ~1.4GB, `it` ~460MB — matching the sizes already
  on record in CLAUDE.md). Verified live: re-ran `build_gloss_dictionary.
  py it` a second time standalone — correctly reused the cached dump
  ("Using cached Wiktionary dump: ...") and finished in ~3.8s instead of
  re-downloading 460MB; re-measured French's "easy"-mode gloss coverage
  (from `require_gloss=True`) — 110,487 of 112,385 words now, up from
  109,088 before this rebuild — and generated a real grid end-to-end from
  the fully-rebuilt French wordlist through `generate_grid()`. The whole
  pipeline (sentence corpus → wordlist → gloss dictionary, for all 5
  languages) is now fully consistent with the Books-corpus addition, per
  rule 6 above.

- changed `backend/clues.py`'s retry scheduling from round-based to
  immediate per-word retry, at the user's explicit choice between the
  two (asked directly rather than assumed, since both are legitimate
  designs — see AskUserQuestion's "Stratégie retry" in this
  conversation). Prompted by the user observing a real deployed
  instance's log directly: `'CET'`'s round 1/3 failure was immediately
  followed by a *different* word's ('RAP') own round 1/3 line — correct
  under the old design (every word gets one attempt per pass over the
  whole pending list before any word gets a second attempt) but read
  exactly like a silently-abandoned retry. Now every word gets up to 3
  consecutive attempts, immediately, before `generate()` moves to the
  next word — same total attempt cap, same log format (`"clue round N/3:
  ..."`), just sequenced differently, so a word's fate (success or final
  failure) is known right away rather than only after every other word's
  first attempt has already run. Also retired the now-unused `_chunks()`
  helper (only ever called from the old round-based loop) rather than
  leaving it as dead code. Verified live: reran the exact reported word
  (`CET`) plus `RAP`/`CHAT`/`MAISON`/`QUI` — all 5 resolved; confirmed via
  INFO-level logging that consecutive log lines for words that succeed on
  their first attempt naturally show one line per word (no artificial
  multi-line noise for the easy case), and confirmed by reading the new
  loop directly (`for attempt in range(3): ... if answer in clues: break`)
  that a word needing retries would show all of its own attempts
  back-to-back rather than mixed with other words' — the scenario the
  user originally asked about, now unambiguous from the log alone.

- added a per-word failure diagnostic file (`LOG/<timestamp>_<answer>_
  ERROR.md`, gitignored), at the user's explicit request, for any word
  that exhausts all 3 retry attempts: the last attempt's complete prompt,
  raw output, and any error — everything needed to reproduce that one
  specific failure by hand, without needing to reconstruct it from
  `backend.log`. Verified live: unit-tested the file directly, then
  forced a real total failure (unreachable `base_url`) and confirmed the
  file was written with the actual connection error.

- while reviewing the new raw-response logging's output, the user asked
  to filter obvious artifacts (`<think>`/`</think>` remnants, assorted
  dash styles, stray spaces) before any content analysis. Doing this
  surfaced a real, previously-unnoticed bug: `_strip_reasoning()` gated
  on `"<think>" in content`, but its own stripping regex only needs
  `</think>` — some chat-template/server setups inject the opening
  `<think>` into the *prompt*, never echoing it back in the completion,
  so a response can start directly with raw reasoning and only a stray
  `</think>`; the old gate skipped stripping entirely in that exact case
  and would have leaked reasoning text into the parsed candidates. Fixed
  by gating on `</think>` instead. Also broadened `_LEADING_MARKER_RE` to
  em/en-dash bullets and added non-breaking-space normalization in
  `_parse_response()`. Worth remembering: a "please also clean up X"
  request is sometimes worth treating as an invitation to actually reread
  the surrounding logic rather than a checklist item — this is the
  second time in this project a request framed as tidying/reinforcement
  turned up a real, independently-worth-fixing bug nearby (the "word:"
  placeholder-echo bug was the first). Verified live: unit-tested all 4
  `_strip_reasoning()` branches plus dash/NBSP handling in
  `_parse_response()`, then re-ran a 5-word live batch with no
  regression.

- added two more `rule_bad` worked examples per language (all 5, not
  just Romance ones), at the user's request, after two more real bad
  clues: (1) visible inline self-correction leaking into the answer
  itself ("Elle raserait... (wait, no) -> Elle l'abattra..." — not a
  `<think>` tag, just stream-of-consciousness drafting left in the final
  line) — rule 7 now explicitly names and forbids this pattern, not just
  "thinking out loud" generically; (2) a wrong-*person* conjugation ("Ce
  que tu fais..." for `RIT`, which needs "il/elle") — the same rule-4
  class as `ÉTAIS`/`SERRERAIT` but specifically "right periphrastic
  template, wrong pronoun slot", plausible since `rule_good`'s own
  examples repeat that template across many different pronouns. See the
  CLAUDE.md entry for the exact words used per language. Verified live:
  resampled both exact reported words (`ABATTRA`, `RIT`) — no
  self-correction, no person mismatch, no regression on prior fixed
  cases.

- added `LLAMA_FORCE_CPU` to `run_llm.sh`/`env.sh`/`env_default.sh`, at
  the user's explicit request: GPU stays the default when detected, but
  setting this (any non-empty value) skips GPU detection/rebuild
  entirely and forces `--n_gpu_layers 0`. Verified live: ran the script
  with it set, confirmed via `ps` the server actually launched with
  `--n_gpu_layers 0` and served a real request, then ran it again unset
  to confirm it went back to `-1`.

- reinforced rule 4's gender/number-agreement trap (b) further, at the
  user's explicit request, after another real gender-mismatch clue: "Se
  dit d'une herbe privée d'humidité" (feminine) for `SEC` (masculine) —
  the same class `TENU`/`LÉGALE` already illustrate, but this time the
  ask was to strengthen the *instruction* itself, not just add another
  example. Added an explicit final self-check step to the rule text, and
  called out that the mismatch is just as easy to make with an ordinary
  noun ("grass") as an obviously-gendered one. One new `rule_bad`
  example (`SEC`/`SECO`/`SECCO`) added to French/Spanish/Italian, same
  language split as `TENU`/`CANSADO`/`STANCO`. Verified live: resampled
  `SEC` 5 times — all correctly masculine — then re-tested
  `TENU`/`LÉGALE`/`RIT` for regression.

- fixed a real, significant bug in `backend/example_sentences.py`, caught
  by the user reading a `LOG/*_ERROR.md` failure file directly: French
  `élu` was missing its "Real example sentences" section despite the
  corpus genuinely containing sentences using it. Root cause:
  `_load_wordlist_words()` read the wordlist's accent-stripped `MOT`
  column instead of its natural `ACCENTED` column, so the target set
  used to build the corpus index never contained any accented word form
  — meaning every genuinely accented word, in every one of the 4
  accented languages, has silently gotten zero grounding examples since
  this file was written (the same silent-failure shape as the earlier
  `CORPUS_DIR` mix-up in this same file, logged above). This is exactly
  the kind of gap the new `LOG/*_ERROR.md` failure-log feature was built
  to surface — it worked as intended the first time it mattered.
  Verified live: `élu` now finds real examples; spot-checked accented
  words across all 4 affected languages (fr/es/it/de) — all now find
  examples where they previously found none; confirmed via a real
  generated user prompt that the section actually appears end to end.

- fixed the info badge showing "GPU" even with `LLAMA_FORCE_CPU` set,
  reported by the user right after actually using the option for the
  first time. `get_system_info()` never checked the flag at all — now it
  does, and reports "cpu" unconditionally when set, since that's a real,
  deliberate choice rather than a hardware-capability guess (unlike the
  rest of this function's probing). Works because `run_Falcon.sh` sources
  the same `env.sh` `run_llm.sh` does. Verified live: restarted the real
  servers with `LLAMA_FORCE_CPU=1` actually set — `/api/system_info`
  correctly reports `"compute":"cpu"`, matching the real running
  `llama_cpp.server` process's own `--n_gpu_layers 0` (checked via `ps`).

- extended the per-candidate diagnostic logging further, at the user's
  explicit request: every rejected candidate is now logged individually
  with the specific reason(s) it failed (too long / non-Latin / contains
  the target word — all applicable ones, not just the first), and the
  candidate ultimately selected is logged too. `_pick_clue()` used to
  filter with one silent list comprehension; now it classifies each
  candidate one at a time so nothing about *why* is lost. Verified live:
  unit-tested a synthetic 4-candidate mix (one clean, one leaking the
  target word, one too long, one non-Latin) — each logged with its own
  correct reason; tested a candidate failing two checks at once — both
  reasons appeared together; then confirmed the format against a real
  LLM call.

- expanded the `LOG/*.md` diagnostic file from failure-only to *every*
  LLM call, at the user's explicit request ("liste tous les appels LLM,
  pas seulement les erreurs"). `_write_failure_log()` (once per word,
  after exhausting retries) became `_write_call_log()` (once per
  attempt, unconditionally), with a new `outcome` field. Filename
  simplified from `<timestamp>_<answer>_ERROR.md` to
  `<timestamp>_<answer>.md` per the same request ("le mot recherché ...
  à la fin du nom de fichier") — `answer` was already the accent-
  stripped uppercase form needed, no extra work required. Verified live:
  a successful `CHAT` call now writes its own file too, with the
  selected clue recorded in `**Outcome**`.

- fixed French `SLIPS` coming back with every candidate labeled
  `"slips - "` before the actual definition — correctly rejected by the
  existing containment filter, but wasting a whole retry round instead
  of salvaging what were otherwise good definitions. Fixed both ways
  the user asked: rule 1 in the prompt now explicitly forbids this
  labeling pattern, and a new `_strip_leading_word_label()` salvages a
  candidate that only fails because of it. One `rule_bad` example added
  per language, each a plural noun so it reinforces number agreement
  too. Verified live: the exact reported case cleaned correctly, no
  false positives on several adversarial inputs, a real `SLIPS` call
  resolved cleanly end to end.

- added `_detect_wrong_language()` to `_pick_clue()`, at the user's
  explicit request for "un typage de langue", after a candidate leaked
  English meta-commentary for a French word that neither the non-Latin
  check nor the length cap could catch. Deliberately a small hardcoded
  per-language stopword list, not a real language-ID library or a
  Hunspell-based check — the backend has never had a runtime dependency
  on Hunspell (only the offline `build_*.py` scripts do), and this
  doesn't need one either. Caught a real false positive in the first
  draft before shipping: legitimate Spanish was flagged as French
  because "de"/"que" are identical cognates in both — fixed by computing
  cross-language word overlap *programmatically* (any word in more than
  one language's list gets dropped from all of them) rather than trusting
  manual review, so a future list edit can't silently reintroduce the
  same class of bug. Verified live: the reported example now correctly
  flags as `en`, the Spanish false positive is gone, and a dozen more
  real clues across all 5 languages (including several generated earlier
  in this session) triggered zero false positives.

- refined `LOG/`'s per-call files further, both at the user's explicit
  request: every filename now ends with `_SUCCES` or `_ERROR` (that
  specific attempt did/didn't produce a usable clue), and each file's
  last section lists every candidate proposed with its own verdict
  (selected / accepted-not-selected / rejected + reason), not just the
  header's one-line outcome summary. Needed `_pick_clue()` to return
  `(chosen, details)` instead of just `chosen` — tracked by index into
  the details list, not by candidate text, so two textually-identical
  candidates can't both get marked "selected". Verified live: unit-
  tested the new return shape against a 3-way mix (rejected/accepted-
  not-chosen/selected) — correct verdicts, original order preserved;
  forced a real success and a real total failure — confirmed
  `_SUCCES.md`/`_ERROR.md` filenames and a correct Candidates section
  in each.

- renamed the `LOG/` folder to `LOG_LLM/` (`.gitignore` updated to
  match), at the user's explicit request — this project could plausibly
  grow other, unrelated kinds of logs later, and `LOG_LLM/` says
  specifically what this one is for. Simple mechanical rename: updated
  `CALL_LOG_DIR`'s path, deleted the old (test-artifacts-only) `LOG/`
  directory rather than migrate it, updated every *current-state*
  mention of the folder name in CLAUDE.md/this SKILL — left the
  *historical* narrative entries describing the folder from before this
  rename as `LOG/` unchanged, since that's what it was actually called
  at the time each of those entries was written.

- a real incident: a locally-made commit ("reference_corpus was lacking")
  removed `.gitignore`'s `/data/reference_corpus/` line and committed all
  5 languages' reference corpus text files directly (~1.2GB, ~27M lines
  total) — almost certainly the cause of a reported `git push` 408
  timeout. Diagnosed via `git show --stat HEAD` and confirmed safe to
  undo (`git status` showed the commit was still local-only, 1 ahead of
  `origin/main`, never pushed) before touching anything. Fixed with a
  plain mixed `git reset HEAD~1` (uncommits without touching working-tree
  files, so the corpus files stayed on disk as untracked) followed by
  `git checkout HEAD -- .gitignore` to restore the correct ignore line —
  no history rewrite/force-push needed since nothing had left the local
  repo. Reinforces the standing convention that `data/reference_corpus/`
  (like `CORPUS/`, `DICS/`, `GRIDS/`, `LOG_LLM/`, `models/`,
  `data/hunspell_cache/`) is generated/regenerable data, never committed
  directly.

- added an optional `data/reference_corpus.tar.xz` fast-path to
  `Install.sh`, at the user's explicit request, right after the venv/
  `pip install` step: if that archive is present, it's unpacked in place
  (`tar -xJf data/reference_corpus.tar.xz -C data`) so a fresh clone can
  skip `build_sentence_corpus.py`'s multi-source OPUS download/filter
  pass entirely; if absent, `Install.sh` just logs that it's skipping it
  and moves on — `build_sentence_corpus.py` (then `build_wordlist_freq.py`)
  remains the correct from-scratch path either way, this is purely an
  optional shortcut. The archive's own path (project root vs. `data/`)
  and whether it should be git-tracked were both still undecided when
  this was written — the previous incident above is exactly why that
  needs a deliberate choice, not a default: even `xz`-compressed, this
  corpus could easily still exceed GitHub's ~100MB soft file-size limit.

- the single combined `data/reference_corpus.tar.xz` above turned out to
  actually hit that risk: committing it (227MB) got rejected outright by
  GitHub's pre-receive hook (`GH001: Large files detected` — a **hard**
  100MB-per-file limit, not just a soft warning), confirmed by attempting
  a real push and reading the server's own rejection message rather than
  guessing from the generic "fetch first" error the git client showed
  first. Fixed by uncommitting (`git reset HEAD~1`, still safe/local-only
  each time this happened) and splitting the archive into one
  `data/reference_corpus_<lang>.tar.xz` per language instead of one
  combined file — each language's own corpus compresses to ~44-50MB
  alone, comfortably under the 100MB limit, so all 5 can now be
  git-tracked directly (this resolves the "still undecided" question
  above: yes, tracked in git, one file per language, under `data/`).
  `Install.sh` extracts each present archive independently (a missing
  archive for one language just falls back to that language's own
  from-scratch `build_sentence_corpus.py` path, not an all-or-nothing
  gate). Verified live: recompressed all 5 languages
  (`XZ_OPT=-T0 tar -cJf data/reference_corpus_<lang>.tar.xz -C data
  reference_corpus/<lang>_sentences.txt`), confirmed every resulting file
  is 44-50MB, and pushed successfully.

- `backend/clues.py`'s OUTPUT FORMAT instructions now spell out a
  concrete per-line template — `C1=`/`C2=`/`C3=` — instead of just
  prose ("exactly 3 plain lines"), at the user's explicit request, to
  give a small model a clearer shape to follow. Critically, this is
  advisory only, the same defensive posture as every earlier format
  change in this file's history (see the two incidents in the module
  docstring that motivated dropping the old header/delimiter format
  entirely): `_parse_response()` never requires or parses for the
  `C1=`/`C2=`/`C3=` label, it just strips one if present
  (`_LEADING_MARKER_RE`, extended with a `[Cc][123]\s*=` alternative
  alongside the existing dash/bullet/numbered-marker cases) — a line
  missing its label, mislabeled, or out of order is still trusted just
  the same. Verified live end-to-end against the real local LLM server
  (French, CHAT): the model echoed the `C1=`/`C2=`/`C3=` labels exactly
  as asked, `LOG_LLM`'s raw-output section confirmed it, and all 3
  candidates parsed cleanly with the labels stripped, one selected.

- `_build_gloss_block()`'s instruction on multiple dictionary senses was
  flagged by the user as counter-productive: it told the model "only one
  may be the meaning that fits... ignore the others" — forcing
  disambiguation down to a single sense even for a word that genuinely
  has several (e.g. French "chat": domestic animal, an online chat, a
  zodiac sign). Reversed at the user's explicit request: the model is
  now told to treat multiple real senses as an opportunity for variety,
  drawing on different ones across its 3 candidates instead of writing 3
  variations on one meaning — while still requiring each candidate stay
  true to one of the word's real, genuine senses (never invent one).
  Verified live against the real local LLM server with CHAT (a word with
  clearly distinct senses: animal / online chat / zodiac sign) — the raw
  output drew on two different senses across its 3 candidates (two
  animal-themed, one correctly pulling the zodiac-sign meaning straight
  from the dictionary block), confirming the model actually diversifies
  now rather than collapsing to one sense.

- `backend/svg_export.py`'s `GRIDS/` renamed to `GRID_SVG/`, and the PNG
  export moved from `GRID_SAMPLES/` to a new `GRID_PNG/`, both at the
  user's explicit request. `GRID_SAMPLES/` still exists and stays
  git-tracked (deliberately not gitignored), but the app no longer
  writes to it automatically — before this, every single generated
  grid's PNG accumulated there without bound; now it's purely a
  hand-curated selection of examples, populated only when someone
  deliberately picks a grid and adds it manually. `GRID_SVG/`/`GRID_PNG/`
  are both gitignored, same treatment `GRIDS/` always had. Verified
  live: confirmed `GRIDS/` was empty and untracked before removing it,
  ran a real offline grid generation end-to-end through
  `save_grid_svg()`/`save_grid_png()` — output landed in `GRID_SVG/`/
  `GRID_PNG/` correctly, `GRID_SAMPLES/`'s pre-existing content was
  untouched.

- fixed a real bug in the same file, reported by the user: a long clue
  line (several same-row/column clues chained with " — ", or just a
  wordy generated clue) had no wrapping at all — it just ran past the
  canvas's right edge, invisibly overflowing the raw SVG and visibly
  clipped once rasterized to PNG (a fixed-size render, unlike an SVG
  viewed directly, which some viewers may not clip). Added `_text_width()`
  (a rough per-character pixel-width estimate, deliberately biased
  slightly high — wrapping a touch early is a harmless waste of margin,
  underestimating reproduces the exact bug) and `_wrap_line()` (a greedy
  word-wrapper using that estimate) — `add_lines()` now wraps each clue
  line to fit the canvas, indenting continuation lines to align under the
  first line's own text rather than under the bold row/column-number
  prefix. Verified live: unit-tested `_wrap_line()` against a realistic
  long chained clue (confirmed every wrapped sub-line's estimated width
  stayed under the canvas's available width), then generated a real grid
  with a deliberately very long injected clue end-to-end through
  `save_grid_svg()`/`save_grid_png()` and read the resulting PNG directly
  — the long clue wrapped into 3 lines, correctly indented, nothing
  clipped by the canvas edge.

- `DIFFICULTY_PRESETS` (`backend/crossword_gen.py`) raised at the user's
  explicit request, following a change to how the underlying frequency
  tables are prepared: easy 25 000 -> 40 000, medium 50 000 -> 80 000
  (hard stays uncapped) — first set to a straight doubling (50 000/
  100 000), then adjusted down to 40 000/80 000 in the same exchange,
  before ever being committed. Same global-ranking-not-per-length
  application as before, unchanged. Verified live (against the
  doubled values, still valid after the follow-up adjustment since
  nothing about the mechanism changed): reloaded the module and
  confirmed the new preset values, then generated a real offline
  `easy` grid end-to-end with no errors.

- added a 4th OPUS source, TED2013 (TED talk transcripts — a spoken but
  prepared/explanatory register, distinct from OpenSubtitles' casual
  dialogue, Wikipedia's encyclopedic prose, and Books' literary
  narrative), to `build_sentence_corpus.py`'s `SOURCES`, at the user's
  explicit request — same pattern as adding Books earlier: verified the
  URL (`OPUS-TED2013/v1.1/mono/{lang}.txt.gz` — `v1.1`, not `v1` like
  Books) resolves for all 5 languages, ran a small Italian smoke test to
  confirm it downloads/merges and the raw-cache mechanism (`CORPUS/`)
  picks it up on a second run, then deleted that smoke-test cache. Per
  rule 6 (recompute the *entire* pipeline when the source list changes),
  reran `build_sentence_corpus.py` -> `build_wordlist_freq.py` ->
  `build_gloss_dictionary.py` for all 5 languages (`build_gloss_
  dictionary.py` reused its `DICS/` cached raw Wiktionary dumps rather
  than re-downloading multi-GB files, since the lemma set changing
  doesn't invalidate the raw dump itself), then rebuilt all 5
  `data/reference_corpus_<lang>.tar.xz` archives (see the earlier
  per-language-split decision above) from the new, larger corpus files.
  One rebuild attempt (`it`) got killed by a 10-minute command timeout
  mid-compression, caught by verifying every archive with `xz -t` after
  the fact rather than assuming completion from exit status alone —
  found the truncated file this way, rebuilt just that one language with
  a longer timeout, then re-verified all 5. Final archive sizes:
  fr=50MB, en=79MB, de=54MB, es=48MB, it=52MB — all comfortably under
  GitHub's 100MB hard limit. Not committed/pushed as part of this
  change — no explicit instruction to do so this time (see the earlier
  entries above for when that *was* asked for explicitly).
