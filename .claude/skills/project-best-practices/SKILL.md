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
  mid-answer. A missing clue for one word falls back to showing the answer
  itself in the UI (see `script.js`) rather than failing the whole grid.
  Note: small local models can still drift off-language partway through a
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
