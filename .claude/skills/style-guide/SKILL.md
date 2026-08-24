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
  from it (`qlmanage -t`, same method as before) for `README.md`. Whenever
  `logo.svg` is replaced again, regenerate `logo.png` the same way — it is
  not derived automatically.

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
