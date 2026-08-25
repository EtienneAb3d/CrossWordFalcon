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
