---
name: style-guide
description: Visual/UI styling decisions for the CrossWordFalcon web page (frontend/static/) — colors, layout, interaction states. Kept up to date automatically whenever a styling decision affects the interface. Invoke before/after any visual change to frontend/static/ (colors, layout, new interactive states).
---

# Style guide — CrossWordFalcon web UI

This SKILL is the living record of every visual/styling decision made for
the web page (`frontend/static/index.html`, `script.js`, `style.css`). It
must stay current on its own — update it whenever a change touches the
interface's look, not just when asked.

Note: product content shown by the UI (crossword words, clues, button
labels like "Générer la grille") stays in French — that's the app's
domain. This SKILL itself, like the rest of the project, is written in
English (see `project-best-practices`).

## Permanent rules

1. **Log every styling decision here** as soon as it's made — colors,
   layout choices, new interactive states (hover, selected, active,
   correct/incorrect, etc.) — before considering a UI-affecting task done.
   Include the *why* when the choice was a deliberate constraint (e.g. "grid
   stays monochrome so colored cell states stand out unambiguously").

2. **All CSS custom properties live in `:root` in `style.css`** — never
   hardcode a color value directly in a rule if a token already exists for
   it. Add a new token here (and in this log) rather than a one-off literal.

3. **The grid itself (`#grid` cells) stays black & white** — only black
   cells (`--black-cell`) and white cells (`--white-cell`); no color is
   applied to the grid's base look. Colored states (selection, correctness
   feedback) are overlaid on top of the white cells only, so they read as
   temporary/interactive rather than part of the puzzle's base appearance.

## Decisions

- page background is a very light mauve (`--bg: #f6f0fb`);
  buttons use blue (`--accent: #2563eb`, `--accent-fg: #ffffff`). The grid
  itself stays strictly black and white (`--black-cell` / `--white-cell`),
  unaffected by the page's accent color — see permanent rule 3.

- added interactive cell states for the playable grid (default
  view: empty grid + clues, filled in by the player):
  - **Selected cell**: light blue background (`--selected: #bfdbfe`), set by
    clicking a white cell. Chosen as blue specifically so it reads as
    distinct from the green/red correctness feedback below.
  - **Correct letter** (Vérification mode, matches solution): light green
    background with dark green text (`--correct-bg: #bbf7d0` /
    `--correct-fg: #14532d`).
  - **Incorrect letter** (Vérification mode, doesn't match solution): light
    red background with dark red text (`--incorrect-bg: #fecaca` /
    `--incorrect-fg: #7f1d1d`).
  Reason for light-background/dark-text pairs rather than solid saturated
  fills: keeps typed letters legible while still giving an unambiguous
  color signal at a glance.

- "Solution" and "Vérification" are toggle buttons
  (`.toggle-btn`), mutually exclusive (turning one on turns the other off —
  showing the full solution and a right/wrong overlay at the same time
  wouldn't mean anything). Active state is visually inverted (`.active`:
  white background, blue text, blue inset border) rather than a third color,
  so it reads as "this mode is engaged" without introducing another hue.

- clicking "Solution" reveals every letter and disables cell
  selection/typing (read-only view); clicking it again restores the
  player's own entries exactly as typed (solution display never overwrites
  `userLetters` — it's a separate rendering path in `script.js`). Reason:
  letting players peek at the solution without losing their progress.

- added a logo (`frontend/static/logo.svg`, a simplified
  falcon/raptor head) in a `#page-header` flex row, top-left of the page,
  directly left of the `<h1>` title (44px/`2.75rem` square, `gap: 0.75rem`).
  The logo's own palette is independent from the page's mauve/blue palette
  — it's a static brand mark, not a UI control, so it doesn't need to
  follow the "grid stays monochrome, buttons are blue" rules above.

- user replaced `frontend/static/logo.svg` with a more detailed
  illustrated version (falcon head gripping a numbered crossword grid in
  its beak, purple/blue feather swirl background, circular composition) —
  supersedes the hand-coded flat-shape version from the entry above. Palette
  is now purple/blue/white/navy rather than the original navy/cream/yellow;
  `#logo`'s 44px display size (unchanged) works fine with it since the new
  artwork reads clearly even small. Regenerated `frontend/static/logo.png`
  from it for `README.md`. Whenever `logo.svg` is replaced again,
  regenerate `logo.png` the same way — it is not derived automatically.

- `frontend/static/logo.png` must be rendered with a transparent
  background, not white — use `rsvg-convert -w 1024 -h 1024
  --keep-aspect-ratio -o logo.png logo.svg` (`brew install librsvg` if
  missing), not macOS's built-in `qlmanage -t`: Quick Look composites SVG
  thumbnails onto an opaque white backdrop regardless of the source having
  no background fill, silently baking in a white square that only shows up
  once the logo sits on anything other than a white page. Verified with
  Pillow (`im.getpixel((0,0))`) that the corner pixel is `(0,0,0,0)` after
  `rsvg-convert`, `(255,255,255,255)` after `qlmanage` — same source SVG.

- logo stays top-left (`#page-header`, unchanged position),
  enlarged from `2.75rem` to `4rem`. Confirmed `position: static` (normal
  document flow) — it's a static brand mark, not a fixed/sticky overlay.

- "Vérification" and "Solution" moved out of the separate
  `#toolbar` (removed) and into `#generate-form` itself, right after the
  "Générer la grille" submit button, in that order — so all three buttons
  read left-to-right as one action group instead of two disconnected
  clusters. They're plain `hidden` HTML buttons (not a wrapper div) toggled
  by `script.js` alongside `#result`'s own hidden state, since they only
  make sense once a grid exists.

- version badge (`#version-badge`, reads `VERSION.txt` via `/api/version`)
  sits top-right of `#page-header`, pushed there by `margin-right: auto` on
  `h1` rather than its own positioning — keeps the header a single flex row
  with logo/title/badge in source order, no absolute positioning. Styled as
  a neutral pill (`.badge`: white background, thin border, grey text) since
  it's a small piece of metadata, not an action or a status the user needs
  to notice.

- "Verticalement" (down clues) lives in its own full-width
  `#down-clues-section` below `#board`, not in the `#clues` sidebar next to
  the grid where "Horizontalement" stays — a down-clue list is one line per
  grid column, so on a wide grid it's long and reads awkwardly squeezed
  into a narrow sidebar. It's laid out with CSS multi-column
  (`.clue-lines-columns { column-count: 2 }`) rather than flex, since flex
  can't split a list of children across columns — `.clue-line` needs
  `break-inside: avoid` so an individual definition never splits across
  the column break.

- within `#board`, "Horizontalement" (`#clues`) comes before `#grid` in
  source order, at the user's request — a plain flex row with no CSS
  `order` override, so source order alone puts the across clues on the
  left and the grid on the right. `script.js` accesses both by `id`, not
  by DOM position, so this reorder needed no script change.

- `#status` now shows live, granular progress during generation instead of
  one static "generating…" message the whole time — generation moved to a
  polled background job (see `project-best-practices`) specifically so
  this was possible, since a single blocking request has nothing to report
  mid-flight. No new visual treatment: still the same `#status` element,
  same plain text, just updated repeatedly (`describeStep()` in
  `script.js`) as the backend's job status changes — treated as
  informational text, not a progress bar/spinner, consistent with the
  existing plain-text status element rather than introducing a new visual
  component for this.

- the browser tab title (`document.title`) now switches with the UI
  language too, not just the on-page `<h1>` — found missing during an i18n
  completeness audit (`applyTranslations()` updated every `[data-i18n]`
  element already, but nothing touched `document.title`). Set from the
  same `t.pageTitle` value the `<h1>` uses, so the two never drift apart.

- `#grid` gained 1-based row/column index headers (a header row of column
  numbers, a header column of row numbers), at the user's request — added
  as one extra row and one extra column inside the *same* CSS grid
  (`grid-template-columns: repeat(width + 1, 2rem)`) rather than a
  separate layout, so the headers stay pixel-aligned with the puzzle
  cells automatically as the grid resizes. Header cells (`.header-cell`)
  use the page background (`var(--bg)`), not white/black, and
  `cursor: default` — visually distinct from both puzzle-cell states and
  non-interactive, since clicking a header does nothing. Each clue line
  (`renderClueLines()`) is now prefixed with the same row (across) /
  column (down) index in bold (`.clue-line-position`, `var(--accent)`,
  fixed `min-width` so the clue text after it stays aligned regardless of
  the number's digit count) — lets a reader match a definition line back
  to the grid's own header numbers. `backend/svg_export.py` mirrors both
  additions (row/column labels on the exported grid, bold index prefix on
  each clue line) since it's meant to match what the browser shows.

- each word's own cell number within a clue line is now shown in
  parentheses, e.g. "**3** (1) Voile de bateau — (5) Prénom hébreu"
  instead of "3 1. Voile de bateau — 5. Prénom hébreu" — at the user's
  request, to reduce confusion between it and the new bold row/column
  index just before it on the same line (both were bare numbers
  otherwise, easy to conflate at a glance). `backend/svg_export.py`
  mirrors this too.

- the difficulty `<select>`'s default option is now "Facile" (easy), not
  "Moyenne" (medium) — matches the backend default (see
  project-best-practices SKILL). Just moved `selected` between the two
  `<option>` elements; no other markup change.

- `main`'s width is now `80%` of the viewport (was a fixed `max-width:
  1100px`), at the user's explicit request — the page now scales with
  the browser window instead of capping out at a fixed pixel width, so it
  fills more of the screen on wide displays. Still centered via
  `margin: 0 auto`; no other layout change.

- added an info badge (`#info-badge`, a circled-"i" inline SVG icon, no
  external icon font/library) next to `#version-badge` in `#page-header`,
  at the user's explicit request — `h1`'s existing `margin-right: auto`
  already pushes everything after it to the right, so no new layout code
  was needed to place it top-right, just adding the element after the
  version badge in source order. On hover/keyboard-focus, a dark tooltip
  (`.info-tooltip`, `position: absolute; top: 100%; right: 0`) drops down
  from the badge showing a small technical report — LLM model, CPU/GPU,
  and (if GPU) its name and available VRAM, from a new `GET /api/
  system_info` endpoint (see `backend/system_info.py` in CLAUDE.md) —
  populated once on page load by `script.js` and redrawn (not re-fetched)
  on every UI language change via `renderSystemInfoTooltip()`, the same
  "fetch once, redraw per language" pattern already used for clue-related
  strings. Pure-CSS hover/focus toggle (`:hover`/`:focus-visible` reveal
  `.info-tooltip`, no JS needed to open/close it) — `tabindex="0"
  role="button"` on the badge itself so it's keyboard-focusable and
  reachable by assistive tech, with `data-i18n-aria` (a new, second
  translation-attribute convention alongside the existing `data-i18n`
  for text content) driving its `aria-label` per UI language. Right-
  aligned tooltip (`right: 0`) specifically because the badge sits at the
  page's own right edge — a left- or center-aligned tooltip would risk
  overflowing the viewport on a narrow window. **Not visually verified in
  an actual browser**: this session's environment has no `chromium-cli`,
  `node`, or Python `playwright` available, so positioning/overlap could
  only be checked by reading the served HTML/CSS directly and manually
  tracing `renderSystemInfoTooltip()`'s logic against the real `/api/
  system_info` response — both checked out, but an actual visual/hover
  check is still owed before fully trusting this on a real display.

- added a faint logo watermark behind the grid/definitions area (`#result`),
  at the user's explicit request (50% width, 90% transparent). First
  attempt put it on `main::before` instead, covering the whole page —
  since `#result` is `hidden` until a grid exists, `main`'s height (and
  so the vertical center a `main`-wide background centers itself on) was
  just the header+form's height at that point, which visually put the
  watermark over the menus instead of over the grid/clues, exactly the
  opposite of what was asked; corrected by moving the pseudo-element from
  `main` to `#result` itself (`position: relative` added there instead).
  Still `::before` rather than a plain `background-image` — a background
  on the element itself would sit behind the element's own background/
  border only, whereas a low-opacity image needs its own layer so the
  opacity doesn't also fade the grid/clue text sitting above it;
  `z-index: -1` keeps the pseudo-element below `#board`/`#down-clues-
  section` (normal-flow children) while still painting above `#result`'s
  own background, since `#result`'s own `position: relative` establishes
  the stacking context the negative z-index resolves against.
  `background-size: 50% auto` sizes the image to exactly 50% of the
  container's width with the height auto-scaled to the SVG's own aspect
  ratio (not a fixed 50%/50% box, which would distort a non-square logo).
  `opacity: 0.1` on the pseudo-element itself (not a filter on the image)
  is the 90%-transparent request. `pointer-events: none` so the (purely
  decorative, behind everything anyway) watermark can never intercept a
  click.

- the `#result`-attached watermark above still had two bugs, both
  reported by the user from real use: invisible at initial page load
  (`#result` is `hidden` until a grid exists, so there was nothing to
  attach a background to yet), and clipped to the grid's own height once
  a small grid did generate (`#result`'s box is exactly as tall as its
  content — `#board`/`#down-clues-section` — and a background painted
  with `inset: 0` can never render past its own box's edges, so a short
  grid clipped the image before it reached its full auto-scaled height).
  Both bugs share one root cause: attaching the watermark to any element
  whose height depends on dynamic content. Fixed by moving it once more,
  this time to `body::before` with `position: fixed; inset: 0` — sized to
  the *viewport*, not to any content box, so it's always exactly as tall
  as the browser window regardless of what's rendered inside `main`,
  visible from the very first load, and never clippable by short
  content. `background-size` changed from `50% auto` (50% of `#result`'s
  width) to `50vw auto` (50% of the *viewport's* width) to match, since
  the sizing basis moved from a content box to the viewport itself. Still
  `z-index: -1`, still `opacity: 0.1`, still `pointer-events: none` — the
  layering/transparency/click-through requirements are unchanged, only
  the positioning strategy. One accepted side effect of `position: fixed`
  vs. the earlier `absolute` attempts: the watermark now stays pinned to
  the browser window and does not scroll away with the page content —
  standard behavior for a persistent watermark, not treated as a bug.

- mirrored the same watermark onto the SVG/PNG exports
  (`backend/svg_export.py`'s `render_grid_svg()`), at the user's explicit
  request — a different sizing/position than the web UI's (90% of the
  canvas's width, centered vertically, vs. the web UI's 50vw) since this
  is a request for the export specifically, not a re-application of the
  web UI's own numbers. Unlike the web UI, this target's canvas has a
  *fixed*, fully-known final size (`canvas_width`/`y` are only computed
  once the whole document — header, both grids, both clue lists — is laid
  out), so there was never a version of the truncation/invisibility bug
  the web UI hit — the watermark is placed and centered against that
  final size directly, correct on the first attempt. Reuses
  `_logo_data_uri()` (the same base64-embedded `frontend/static/logo.png`
  already used for the header logo) rather than embedding `logo.svg`
  separately — one asset, one caching mechanism, and this PNG is already
  confirmed to have a transparent background (see the entry above on
  `rsvg-convert` vs. `qlmanage`). Verified visually, not just by reading
  the markup: generated a real grid, rendered it through `save_grid_png()`
  (the actual `rsvg-convert` path used in production), and read the
  resulting PNG — the watermark is faintly visible behind the entire page
  (empty puzzle and solution both), correctly sized and centered.

- added bidirectional hover highlighting between the grid and the clue
  lists, at the user's explicit request: hovering a grid cell frames every
  cell of the word under the cursor (`.cell.word-highlight`, an inset
  `box-shadow` border in `--accent`, not a background fill — kept visually
  distinct from `.selected`'s solid blue fill and the green/red
  correctness states, and composes cleanly when a cell is several of
  these at once) and highlights that word's own clue text
  (`.clue-segment.hover-highlight`, a `--selected`-colored background);
  hovering a clue highlights the matching cells in the grid, the same
  direction. Direction on grid hover is Shift-or-CapsLock = vertical word,
  otherwise horizontal — matches the existing typing convention already
  established for cell input (`handleKeydown`'s `isUpper` check), so the
  same modifier gesture means "vertical" everywhere in the UI, not just
  for typing. Re-evaluated live on every Shift/CapsLock keydown/keyup too,
  not just on mouseenter, so the highlighted word switches immediately if
  the modifier is toggled while the mouse sits still over the same cell.
  `renderClueLines()`'s previously plain-text-joined clue line
  (`"(1) clue — (2) clue"`) is now built from individual
  `<span class="clue-segment" data-row data-col data-direction>` elements
  per word instead, so a single word's own hover target can be
  distinguished from its neighbors on the same line — the row/col/
  direction triple is enough to identify a word uniquely (verified
  against real generated grid data: two different words can share the
  same clue *number* at the same starting cell — one across, one down —
  so `data-number` alone would have been ambiguous, but adding direction
  as the third key resolves it). Word extent (which cells belong to the
  hovered word) is computed by scanning the grid's own `pattern` outward
  from the hovered/clicked cell until a black cell or the edge
  (`wordCellsAt()`), not by looking anything up in `puzzle.words` first —
  works from any cell in the word, not just its numbered start, and
  every white run is guaranteed ≥3 cells by the backend's own structural-
  validity check, so there's no length-1/2 fragment edge case to handle.
  This session's environment still has no browser-automation tooling
  (see the info-badge entry above) — verified as thoroughly as possible
  without one: real JS syntax check via a temporarily `pip install`ed
  `esprima` (removed again afterward, never added to `requirements.txt`
  — it's a one-off verification tool, not a runtime dependency), and the
  `wordCellsAt()` word-extent algorithm mirrored in a standalone Python
  script and run against a real generated grid's actual `pattern`/`words`
  data — confirmed it reconstructs the exact same cells (and the same
  `length`) as the backend's own word metadata for all 24 words, hovering
  from both a word's start cell and a middle cell. The actual pixel-level
  hover appearance in a real browser is still unverified and owed, same
  caveat as the info badge.

- added a fixed-height, always-3-lines-tall panel (`#hover-definition`)
  directly below the grid, at the user's explicit request: shows the
  definition of whichever word is currently hovered (grid cell or clue
  line — both hover paths already went through the same `highlightWordAt()`
  /`clearHighlights()` pair, see the entry above, so this needed hooking
  into exactly those two functions and nothing else), so a player can read
  the current word's clue without keeping the full across/down clue lists
  in view. Placed "under the grid" literally, not under the whole
  `#board` row (which also has the `#clues` sidebar): wrapped `#grid` and
  the new panel in a new `#grid-column` flex column, so the panel's
  `width: 100%` stretches to match `#grid`'s own intrinsic width via the
  column's default `align-items: stretch`, rather than the sidebar's
  width or the row's full remaining space. Height is a fixed `4.5rem`
  (3 × a `1.5rem` line-height given in rem, not a unitless multiplier, so
  "3 lines" is an exact, predictable height) rather than auto-growing
  with the text, so the layout never shifts as different definitions are
  shown; `overflow-y: auto` lets an unusually long definition still be
  read by scrolling within the fixed window instead of being cut off.
  Idle state (nothing hovered) shows a translated placeholder
  (`hoverDefinitionPlaceholder`, all 5 languages) in lighter italic text
  (`.placeholder`) so it reads as a hint, never mistaken for a real
  (oddly generic) clue — reuses the `renderSystemInfoTooltip()`-style
  "compute/set once, redraw on language change" pattern via a new
  `renderHoverDefinitionPlaceholder()`, called from `applyTranslations()`
  (only when nothing is currently hovered — a live definition is puzzle
  content, in the grid's own language, never touched by a UI-chrome
  language change), from `clearHighlights()` (the idle state after a
  hover ends), and from `renderGrid()`'s own reset block (so a freshly
  generated/regenerated grid never shows a stale definition left over
  from the previous one). Reuses the matching `.clue-segment`'s own
  `textContent` (`"(N) clue text"`) directly for the shown definition
  rather than a fresh `puzzle.words` lookup — one less place needing the
  `noDefinition` fallback logic already applied once in
  `renderClueLines()`. Verified: JS syntax-checked via a temporarily
  `pip install`ed `esprima` (removed again after, same as the earlier
  hover-highlighting work — not a runtime dependency), and confirmed via
  a real HTTP request to the running frontend server that the served page
  actually contains the new `#grid-column`/`#hover-definition` markup
  (not just that the source files parse). Same caveat as the two entries
  above: no browser-automation tooling in this environment, so the actual
  rendered layout/hover interaction in a real browser is still unverified
  and owed.

- fixed a real bug in `#hover-definition` above, reported by the user
  from actual use: a long definition stretched the whole box (and
  `#grid-column` with it) wide instead of wrapping within the fixed
  3-line height. Root cause is the classic flexbox/intrinsic-sizing
  gotcha: `#grid-column` sizes itself from its children's natural width
  (see that entry's own comment), and a block element's natural/max-
  content contribution to that computation is how wide its text would be
  laid out on a *single unwrapped line* — flex/grid items default to
  `min-width: auto`, which (for this purpose) doesn't let the element
  shrink below that unwrapped width, so a long clue could force the box
  wider rather than wrap. Fixed with `min-width: 0` on `#hover-definition`
  itself (the standard, well-documented fix for this exact class of bug),
  which lets it shrink below its content's preferred width so normal text
  wrapping actually takes effect at the width `#grid` already dictates;
  added `overflow-wrap: break-word` too, defensively, for the rare case
  of one single unbreakable "word" wider than the box on its own.
  Verified structurally (confirmed via a real request to the running
  frontend server that the served CSS contains the fix) — actual visual
  wrapping in a real browser is still unverified, same standing caveat as
  the rest of this feature.

- the `min-width: 0` fix above did *not* actually resolve the bug — the
  user reported, from real use, that long definitions still stretched
  the box instead of wrapping. Root cause was a compounding version of
  the same flexbox gotcha: `#grid-column` is *itself* a flex item (of
  `#board`, `flex-shrink: 0`) with the browser's default
  `min-width: auto`, so its own auto-computed width could still be pulled
  wide by `#hover-definition`'s unwrapped content before the inner
  element's own `min-width: 0` got a chance to matter — patching only the
  innermost element wasn't enough once there were two nested
  intrinsically-sized flex containers involved. Replaced the CSS-only
  approach with an explicit, JS-set pixel width instead of relying on any
  further flexbox auto-sizing fix: `renderGrid()` now sets
  `hoverDefinition.style.width = gridEl.offsetWidth + "px"` right after
  every cell has been appended (so `offsetWidth` reflects the grid's
  final rendered width, not a partial layout) — an inline style, so it
  overrides the CSS `width: 100%` rule outright. This sidesteps the
  auto-sizing ambiguity entirely rather than fighting it: once
  `#hover-definition` has a fixed pixel width, its contribution to
  `#grid-column`'s own sizing is that same fixed number, not its text's
  unwrapped max-content width, so neither element can be pulled wide by
  long text anymore, and normal text wrapping applies inside the now-
  fixed-width box. Left the earlier `min-width: 0`/`overflow-wrap:
  break-word` CSS in place too (harmless, and a reasonable fallback for
  the brief window before `renderGrid()` first runs). Also checked for
  and ruled out an unrelated possible cause first: no `white-space:
  nowrap` rule anywhere in the stylesheet applies to `#hover-definition`
  or any of its ancestors (the only two `nowrap` rules in the file are
  scoped to `.badge`/`.info-tooltip`). Verified: JS syntax-checked via
  the same temporary-`esprima` method as before, and confirmed via a real
  request to the running frontend server that it serves the updated
  `renderGrid()`. Still no browser-automation tooling in this
  environment, so — unlike the previous attempt at this same bug — this
  fix is *not yet confirmed against an actual rendered browser*; flagged
  clearly rather than declared fixed a second time on theory alone.

- Reported: killing the frontend server (port 8000) while a generation
  request is in progress crashed Firefox entirely (a full OS-level process
  crash, per the user's own clarification — not just a hung/unresponsive
  tab). Investigated the JS: `pollJob()`'s polling loop already wraps its
  `fetch` in the surrounding `try`/`catch` and doesn't retry in a tight
  loop on failure, so nothing in this codebase's own JS explained an actual
  browser-process crash — that pointed toward a Firefox/OS-level issue
  outside this app's control. Tried to get a concrete diagnostic from the
  user (a Mozilla `about:crashes` report ID, then the macOS
  `~/Library/Logs/DiagnosticReports/*.ips` crash log matching the UUID-
  format ID the user did find, which confirmed this really was an OS-level
  crash rather than a Firefox-internal one) — searched
  `~/Library/Logs/DiagnosticReports/` directly by the given identifier and
  found no matching Firefox report (only unrelated `cmTC_*` files from a
  different date), and the user couldn't dig further themselves. With both
  diagnostic avenues exhausted, added a defensive hardening regardless of
  confirmed root cause, since it can only help: `fetchWithTimeout()`
  (`AbortController` + `setTimeout`) now wraps every fetch to this origin
  (`/api/generate`, `/api/generate/status/{job}` in `pollJob`'s loop), at
  `FETCH_TIMEOUT_MS` (15s — deliberately above `frontend/server.py`'s own
  10s outbound proxy timeout, so a legitimately slow-but-healthy round trip
  through the proxy can't race against this client-side abort). A timeout
  or an outright connection failure is now caught explicitly and mapped to
  a new, translated `errorConnectionLost` string (added to all 5 languages
  in `i18n.js`) instead of leaving `pollJob` to propagate whatever raw,
  untranslated error message the browser's own `fetch` implementation
  happened to throw. Verified: JS syntax-checked via the same temporary-
  `esprima` method used elsewhere in this file (`10_000` had to become
  `10000` — numeric separators aren't valid ES syntax to this aging parser,
  though they are to a real browser; kept the plain form anyway purely so
  this project's own verification method keeps working). Explicitly **not**
  confirmed to fix the reported crash itself — no browser-automation
  tooling in this environment, and, per the investigation above, the actual
  root cause looks like it lives in Firefox/the OS rather than in this
  app's code; this closes off "a fetch could hang forever" as one
  contributing mechanism this app's own code could control, nothing more.

- Added a live "attempt preview" (`#attempt-preview`/`#attempt-preview-grid`,
  `renderAttemptPreview()`/`hideAttemptPreview()` in `script.js`), at the
  user's explicit request — "pour affichage dans l'interface, afin de se
  faire une idée de ce qui a été tenté" (so the interface can give a sense
  of what was tried): whenever a generation attempt fails (see
  `backend/crossword_gen.py`'s `try_fill`, `diagnostics["example_grid"]`,
  CLAUDE.md), the poll response's `step.example_grid` (mid-generation, one
  palier out of several failing) or `step.last_attempt.example_grid`
  (terminal failure, right before the job errors out) is rendered as a
  small read-only grid — the most-filled-in state that specific attempt
  reached before giving up. Placed between `#status` and `#result`, shown
  as soon as the first such event arrives and left visible through a
  terminal error (so the last attempt stays on screen next to the error
  message), only cleared at the very start of a new generation
  (`hideAttemptPreview()` in the form submit handler) or once a generation
  actually succeeds (the real interactive `#grid` takes over at that
  point). Deliberately reuses `#grid`'s own `.cell`/`.white`/`.black`
  classes/colors for visual consistency (this reads as "a crossword grid",
  not an unrelated new widget) but scopes them smaller
  (`#attempt-preview-grid .cell`, 1.1rem vs. `#grid`'s 2rem) and strips
  every interactive affordance (`cursor: default`, no hover/selection
  states, no row/column headers) — this is a quick, secondary glance at
  what the solver tried, not a second playable grid. A single new
  `attemptPreviewLabel` string added to all 5 languages in `i18n.js`,
  wired through the existing `data-i18n` convention. Verified: JS syntax-
  checked via the same temporary-`esprima` method used elsewhere in this
  file, confirmed the running frontend server serves every updated file
  (`index.html`/`style.css`/`script.js`/`i18n.js`), and confirmed against
  the *real* backend end-to-end — started an actual generation job over
  the API, and both `backend.log` and a direct poll of `GET /api/generate/
  status/{job_id}` showed a genuine `pattern_attempt_failed` event
  carrying a well-formed, letter-populated `example_grid` (and, forcing a
  terminal failure with `attempts=1` in a direct Python call, confirmed the
  nested `last_attempt.example_grid` shape too) — the frontend-side
  rendering itself is still unverified in an actual browser, same
  documented limitation as every other visual feature added in this
  session (no browser-automation tooling in this environment).

- Reported next: the preview never actually appeared, `#attempt-preview`
  observed stuck at `display: none`. Root cause was a polling race, not a
  CSS bug: `backend/app.py`'s `job["step"]` gets fully overwritten by
  *every* progress event, so a failed attempt's `example_grid` only lived
  in the API response for the single poll window before the next event
  (often the next palier's plain "pattern" step) overwrote it — a client
  polling every `POLL_INTERVAL_MS` could easily poll right past that
  narrow window every single time, never once catching it, which reads
  from the browser exactly like "this never renders." Fixed on the backend
  (CLAUDE.md has the detail) by persisting the most recent one separately
  as `job["last_example_grid"]`, which — unlike `job["step"]` — only ever
  changes when a *new* one actually arrives. `script.js` simplified to
  match: reads `data.last_example_grid` directly instead of the previous
  `data.step.example_grid || data.step.last_attempt.example_grid` check.
  Verified live against the real backend (restarted to pick up the fix):
  polled an actual failing job 20 times in a row after the field first
  turned non-null — stayed non-null on all 20, confirming the preview data
  is now reliably available regardless of polling timing. The rendering
  itself is still unverified in an actual browser, per the entry above.

- Added highlighting for "impossible zones" within the attempt preview
  above, at the user's explicit request: cells belonging to a slot that had
  no candidate word left at all, at the snapshot shown, get a light red
  background — reusing `--incorrect-bg` (`.cell.white.impossible`), the
  same token already used for a wrong letter on the real, playable grid,
  rather than a new color, so "something is wrong here" reads consistently
  across both contexts (permanent rule 2: no new literal color if a token
  already fits). Backend side: `Filler.impossible_zone_cells()` (see
  CLAUDE.md) computes this from `best_assignment`, persisted the same way
  as `example_grid` (`job["last_impossible_cells"]` in `backend/app.py`,
  sharing the just-added `_latest()` helper rather than duplicating the
  same "check `data`, then `data["last_attempt"]`" logic a second time).
  `renderAttemptPreview()` takes a second `impossibleCells` parameter (an
  array of `[row, col]` pairs, possibly empty/absent) and adds `.impossible`
  to the matching white cells. Verified live against the real backend
  (restarted to pick up the change): polled a real failing job repeatedly
  and confirmed `last_impossible_cells` appears alongside
  `last_example_grid`, with a genuinely non-zero, growing cell count across
  the run (15 → 16) as later attempts contributed their own impossible
  zones. Rendering itself still unverified in an actual browser, per the
  entries above.

- Fixed a real bug reported live from an actual browser (the first visual
  confirmation any of this session's UI-only additions had actually
  received): `#hover-definition` showed an inline `width: 0px` instead of
  the grid's real width. Root cause: `renderGrid()`'s own width-measurement
  fix (`hoverDefinition.style.width = gridEl.offsetWidth + "px"`, added
  earlier in this project's history) ran *before* `result.hidden = false`
  in the form submit handler — while `#result` is still `hidden`,
  everything inside it (including `#grid`) lays out at zero size
  regardless of how many cells were just appended, so the measurement
  silently read 0. Every *other* `renderGrid()` call site (cell selection,
  typing, the solution/checking toggles) was already safe, since those are
  only reachable once a grid — and so a visible `#result` — already exists;
  only the very first render, right after a new grid finishes generating,
  had the ordering wrong. Fixed by moving `result.hidden = false` a few
  statements earlier, right before `renderGrid()` instead of after —
  causes no visible flash, since nothing `await`s between the two
  statements and `renderGrid()`/`renderClues()` repopulate the now-visible
  container synchronously in the same tick. JS syntax-checked via the same
  temporary-`esprima` method used throughout this file; the fix itself
  still awaits a second live-browser confirmation from the user, same as
  every other visual change made without direct browser access this
  session.

- Added highlighting for statistical-hint letters in the attempt preview,
  at the user's explicit request: cells whose shown letter came from
  `sample_letter_biases` (CLAUDE.md) rather than a real placement by the
  search get a light blue background — reusing `--selected` (the same
  token already used for cell selection on the real, playable grid) rather
  than a new color, distinct from `.impossible`'s red. `renderAttemptPreview()`
  takes a third `forcedCells` parameter (mirrors `impossibleCells`'s own
  shape — an array of `[row, col]` pairs, possibly empty/absent) and adds
  `.forced` to the matching cells; declared *before* `.cell.white.impossible`
  in the stylesheet so a cell matching both (rare, but possible — a
  statistically-forced cell can in principle also belong to an impossible
  zone) shows the red, the more important signal of the two. Verified live
  against the real backend (restarted to pick up the change): polled an
  actual failing job and confirmed `last_forced_cells` appears alongside
  `last_example_grid` and correctly shrinks as later attempts' own
  best-assignment snapshots cover more of the previously-forced cells with
  real letters (9 → 8 across consecutive polls). Rendering itself still
  unverified in an actual browser, per every other visual change made
  without direct browser access this session.

- Reworked `.cell.white.forced` from a background fill to a thick inset
  border, at the user's explicit follow-up request, specifically so it
  survives being combined with `.impossible`'s red background on the same
  cell — two competing `background` declarations mean whichever rule is
  declared last simply wins outright, silently hiding the other state,
  whereas a border (`box-shadow: inset 0 0 0 2px ...`, the same technique
  `#grid`'s own `.word-highlight` already uses on the real, playable grid)
  composes independently of whatever fill sits underneath it. Switched the
  color token in step, from `--selected` (a pastel meant for background
  fills) to `--accent` (the same blue `.word-highlight` already borders
  with) — the right token changed along with the technique, not just the
  CSS property. `LETTER_BIAS_FORCE_FRACTION` also lowered from 10% to 5% in
  the same request (`backend/crossword_gen.py`) — fewer forced cells shown
  in the preview as a result. Verified live against the real backend
  (restarted to pick up both changes): a real failing job's polled
  `last_forced_cells` count (4) was noticeably lower than prior polls seen
  at the old 10% setting (8-9), consistent with the halved target: and the
  served `style.css`/`script.js` both reflect the new border-based styling
  and comment. Rendering itself still unverified in an actual browser, per
  every other visual change made without direct browser access this
  session.

- Reported next: the preview showed few forced-cell borders, decreasing to
  none as generation progressed through higher black-cell ratios, opposite
  the expected direction (shorter slots at a higher ratio should, if
  anything, make the statistical sample *more* consensual). Diagnosed with
  a direct trace rather than guessing which layer was at fault: `sample_
  letter_biases` itself stayed stable (6-7 forced cells per attempt across
  every ratio from 5% to 40% tested) — not a generation bug — but the
  *reported* `forced_cells` (then filtered down to "still showing the
  guessed letter, not yet covered by a real one") dropped to 0-2 at several
  ratios, since a higher ratio's shorter slots are also easier for the
  search to make real progress on, covering more of the originally-forced
  cells with confirmed letters before giving up. Correct computation, but
  a confusing signal — indistinguishable from a broken pre-fill by looking
  at the preview alone. Fixed exactly as the user proposed:
  `build_partial_letters_grid` (CLAUDE.md) now returns *every* cell
  `forced_letters` ever set, not just the still-unconfirmed subset, and
  `renderAttemptPreview()` was restructured so the `.forced` border is
  added in a dedicated **final pass** over `forcedCells`, after every cell
  element already exists in the grid (tracked via a `cellElementsByCoord`
  map built during the main per-cell loop) — a genuine overlay applied on
  top of everything already drawn, at the user's own explicit follow-up
  request, removing any doubt about draw order. Verified live against the
  real backend (restarted to pick up the change): a direct trace across
  the same ratio range confirmed `diagnostics["forced_cells"]` now exactly
  matches `sample_letter_biases`'s own raw count at every ratio (previously
  it could fall well short); polled a real job and confirmed
  `last_forced_cells` (7) stayed stable and populated across consecutive
  polls, and the served `script.js` reflects the new overlay-pass
  structure. Rendering itself still unverified in an actual browser, per
  every other visual change made without direct browser access this
  session.
