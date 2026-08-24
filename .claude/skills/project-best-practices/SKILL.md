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
  `rsvg-convert`. Unlike every other generated-output directory in this
  project (`GRIDS/`, `data/hunspell_cache/`, `data/reference_corpus/`,
  `data/gloss_dictionary/`, `models/` — all gitignored), `GRID_SAMPLES/`
  is deliberately checked into git: the user wants a growing, version-
  controlled visual record of what the app actually produces, evolving
  release to release, not a disposable local cache — confirmed explicit
  intent, not an oversight, so don't add it to `.gitignore` later out of
  habit-matching the other generated directories. Best-effort like
  `save_grid_svg`: a missing `rsvg-convert` or any conversion failure is
  logged as a warning by `backend/app.py`, never fails the request.

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
