# How to use CrossWordFalcon's web page

This document describes, in plain terms, what every element of the web
page (`frontend/static/index.html`) does and how a player uses it. It says
nothing about how the interface is implemented — see `CLAUDE.md` for that.

The page has one language selector (`#language`) that drives two things at
once: the language of the crossword puzzle itself, and the language the
interface's own labels/messages are shown in
(`frontend/static/script.js`, `setUiLanguage`, `applyTranslations`) —
there is no separate "interface language" setting.

## First visit — the welcome panel

The very first time the page is opened (no preferences cookie yet), a
panel (`#welcome-overlay`) appears over the whole page and can only be
dismissed by clicking **Accepter / Accept** — no close button, and
neither the Escape key nor a click on the background closes it. It
contains:

- A **language** selector, preset to the browser's own language when it
  is one of the six supported languages, otherwise English. Changing it
  immediately re-renders both the panel itself and the whole page behind
  it in that language (this is the "second" language selector — it stays
  in sync with `#language` in the form).
- A **pseudo / nickname** field (up to 15 characters). It is required —
  the panel will not close until it is filled in.
- A short notice that the site needs a functional preferences cookie to
  work, and that it uses no tracking cookies and no advertising cookies.

Clicking **Accept** saves the language and pseudo in that one cookie and
closes the panel. The choice is remembered on later visits (the panel
does not reappear).

## Header

- The logo and page title sit at the top left (`#logo`, `h1`).
- The **pseudo** chosen in the welcome panel is shown centered between
  the title and the right-hand badges (`#user-pseudo`). While no pseudo
  is set it shows a "set a nickname" label instead. Clicking it reopens
  the welcome panel to change the pseudo or the language.
- A small green pill on the right (`#online-count`) shows how many
  distinct people are using the app right now — "x en ligne / x online".
  It refreshes every 2 seconds (`POST /api/presence`); tabs sharing the
  same pseudo count as one person, and someone is dropped from the count
  60 seconds after their last refresh.
- A small pill next to it (`#version-badge`) shows the app's current
  version (`GET /api/version`).
- An "i" icon next to it (`#info-badge`) reveals a tooltip on hover or
  keyboard focus (`frontend/static/script.js`, `renderSystemInfoTooltip`),
  showing which LLM model writes the clues, whether it runs on CPU or
  GPU, and — when it's a GPU — its name and available memory
  (`GET /api/system_info`). Below that report, a small meter bar per
  resource (`#resource-meters`, `renderResourceMeters`) shows how busy
  each one currently is: one meter for every CPU combined, and one more
  per detected GPU. Right below the meters, two more lines
  (`#queue-lengths`, `renderQueueLengths`) show how many jobs are
  currently in the grid-generation queue ("Grille (CPU)") and in the
  definitions-generation queue ("Définition (GPU)") — see the "Status
  line" section further below for what these two queues are and how a
  generation can end up waiting in one of them. All of this updates
  live, roughly every 2 seconds, riding along with the same background
  heartbeat that keeps `#online-count` current (`POST /api/presence`).

## Generation form

Every field below (`#generate-form`) is used to start a new grid
generation (`frontend/static/script.js`, the form's own `submit` handler,
`runGeneration`).

- **Langue / Language** (`#language`) — which of the six supported
  languages (French, English, German, Spanish, Italian, Portuguese) the grid's own
  words and clues are written in. Also switches every label/message on
  the page to that same language.
- **Largeur / Width** (`#width`) — the grid's own width in cells, from 5
  to 30 when the page is opened on the local machine, from 5 to 20 when
  it is opened from another machine on the network. A value outside the
  allowed range is snapped back to the nearest bound as soon as the
  field loses focus (`frontend/static/script.js`, `clampDimensionInputs`).
- **Bilingue / Bilingual** (`#bilingual-language`) — the language of the
  grid's *vertical* (down) words; the horizontal (across) words always
  stay in the main **Langue** field above. Changing **Langue** always
  resets this field to match it, so a genuinely bilingual grid — two
  different languages, one for across words and one for down words —
  needs this field set to a *different* value each time, right after
  picking **Langue**. Left equal to **Langue** (the default), the grid is
  an ordinary, single-language one. On a bilingual grid, every clue is
  written in that specific word's own language (`frontend/static/
  script.js`, `buildChatUiContext`; `backend/clues.py`, `LLMClueGenerator.
  generate`), the **Dictionnaire / Dictionary** panel's own language
  selector (see below) follows whichever word is currently hovered or
  clicked, and David FALCON replies entirely in the language of whichever
  word/direction is currently selected in the grid (see "David FALCON"
  below).
- **Hauteur / Height** (`#height`) — the grid's own height in cells, from
  5 to 30 on the local machine, from 5 to 20 from another machine on the
  network; same out-of-range snap-back on blur as **Largeur / Width**
  above (`frontend/static/script.js`, `clampDimensionInputs`).
- **Difficulté / Difficulty** (`#difficulty`) — Easy, Medium, or Hard.
  Easy and Medium use a smaller, more common vocabulary and never place a
  word that looks like it could be a proper noun (a person's or place's
  name); Hard can use the entire dictionary, including proper nouns.
- **Taux noir / Black rate** (`#black-enrichment`) — roughly how many
  black cells the finished grid aims for, as a percentage of the grid's
  cells (0-100%, 17% by default). A higher value gives shorter, easier
  words at the cost of a denser-looking grid.
- **Graines / Seeds** (`#force-letters`) — a small percentage (0-100%,
  1% by default) of cells the generator seeds with a statistically
  likely letter before it starts searching for real words, nudging the
  search rather than fixing an actual answer in place.
- **Mode** (`#mode`) — how much computing effort one attempt is allowed
  before giving up and trying again: Flash (fastest, least thorough),
  Turbo, Rapide/Fast, Moyen/Medium (the default), Ultra (slowest, most
  thorough). A harder grid (a larger size, a stricter black rate) may
  need a slower mode to succeed at all. **Ultra** is only selectable when
  the page is opened on the local machine; from another machine on the
  network its option is greyed out and unavailable
  (`frontend/static/script.js`, `restrictUltraModeToLocalhost`).
  **Interactif / Interactive** is a different kind of mode, listed above
  Flash: instead of the computer filling the whole grid on its own, it
  hands you a black-cell pattern with one word already placed and lets
  you build the rest yourself (see "Interactive authoring mode" below).
- **Précision thématique / Theme precision** (`#theme-precision`) — the
  minimum closeness a word must have to the theme to enter the theme
  glossary, a number from 0 to 1 (0.68 by default; use a **point**, not a
  comma, for the decimal — a typed comma is converted automatically).
  Higher means a tighter, more on-topic glossary with fewer words; lower
  means a broader one. It only affects grid generation when the
  **Thématique** field is filled in, and it also sets the closeness cutoff
  for the Dictionary panel's **Thématique** button (below).
- **Thématique / Theme** (`#theme`) — an optional list of words, next to
  the Generate button. Left empty, the grid is filled from the whole
  dictionary as usual. Filled in, the language model first writes a
  short (~30-word) comma-separated list of keywords describing the theme of
  your words, deliberately mixing word types (nouns, verbs, adjectives,
  adverbs). The generator then runs a separate vector search for *each
  keyword* on its own, and merges every result: it looks up every word,
  from 2 to 15 letters long, that is close enough to that keyword (via the
  search over the word embeddings — Qdrant must be running and populated
  for that language, and a word too semantically distant is left out
  entirely — no limit at all on how many words come back, however many or
  few a given length ends up with, only the closeness cutoff, the same for
  every keyword). If
  you typed more than one word, the model also writes a keyword list for
  each word on its own, and all the keywords from every list are searched
  and merged, so a multi-word theme draws on a much wider vocabulary. If
  that still leaves fewer than about 300 distinct keywords, the model is
  asked a few more times (up to 3) to widen the list before the searches
  run. The
  generator then
  fills every slot from those words first, only falling back to another
  dictionary word for a slot when no combination of the pre-selected
  words can complete it. On a **bilingual** grid a separate theme
  glossary is built for each language — one for the across words, one for
  the down words — so both directions are steered toward the theme in
  their own language. The clues written for the finished grid are also
  strongly steered toward the theme — the model is told to prefer a
  theme-flavoured wording wherever it can do so without making a clue
  inaccurate. If the model or the vector search is unavailable,
  generation simply proceeds with no theme. The words you
  typed are saved with the grid and shown in the library's Theme column;
  every keyword list the model produced, the flat set of keywords actually
  searched, and the resulting word list are written to a `LOG_THEME/` log
  file.
- **Générer la grille / Generate** (`#generate-btn`) — starts generation
  with the settings above. While a generation is running, this and every
  field above stay usable for the *next* generation, but see "While a
  grid is generating" below for what appears meanwhile.

## Action buttons

These sit next to the generate button (`#generate-form`) and only appear
when relevant.

- **Voir / View** (`#attempt-preview-reveal-btn`, `frontend/static/
  script.js`, `togglePreviewLetters`) — while a grid is generating, shows
  or hides the actual letters inside the search-progress preview grids
  (see "While a grid is generating" below) and the "grid word
  verification" table underneath them (a dictionary/root-form lookup for
  each placed word — see that table's own entry further below). Off by
  default, so a generation in progress never spoils the puzzle before
  it's ready to play. IMPORTANT — do not confuse this with the
  "Vérification / Check" button described a few bullets below: that is a
  COMPLETELY DIFFERENT feature (it colors the PLAYER's own typed letters
  correct/incorrect once a grid is finished and being played) that merely
  happens to share the word "verification" in its own name. "Voir" itself
  never tells anyone whether an answer is right or wrong — it only
  reveals or hides letters that are not yet the player's own.
- **Stop** (`#stop-btn`, `frontend/static/script.js`, `stopBtn` click
  handler, `POST /api/generate/cancel/{job_id}`) — appears once a
  generation has started; asks the server to abandon it. Takes effect at
  the next safe checkpoint (search, optimization, or clue writing), not
  necessarily instantly.
- **Continuer / Continue** (`#continue-btn`, `frontend/static/script.js`,
  `continueBtn` click handler, `POST /api/generate/continue/{job_id}`) —
  appears only after a generation fails specifically because no fillable
  grid could be found with the chosen settings. Restarts the search from
  exactly where it left off, with a fresh full budget, instead of
  starting over from a blank grid. Clicking it again after a further
  failure keeps resuming from the most recent attempt.
- **Vérification / Check** (`#check-btn`, `frontend/static/script.js`,
  `toggleChecking`) — while playing (a finished grid, never during
  generation), colors every filled cell green (correct) or red
  (incorrect) against the real solution. Turning it on turns "Solution"
  off. IMPORTANT — do not confuse this with the "Voir / View" button
  described a few bullets above (which only reveals/hides letters in the
  search-progress previews shown WHILE a grid is still generating, and
  has nothing to do with whether an answer is correct): the two features
  merely happen to share the word "verification"/"vérification" in their
  names, they are otherwise unrelated.
- **Solution** (`#solution-btn`, `frontend/static/script.js`,
  `toggleSolution`) — reveals every letter of the finished grid and
  stops accepting typed input; toggling it off restores exactly what the
  player had typed, nothing is lost. Turning it on turns "Vérification"
  off.
- **Définitions / Definitions** (`#definitions-btn`, `frontend/static/
  script.js`, `toggleDefinitions`) — shows or hides the across/down clue
  lists below the grid. Hidden by default on a freshly generated or
  freshly loaded grid.
- **Recalculer / Recompute** (`#recompute-btn`, `frontend/static/
  script.js`, `recomputeBtn` click handler, `POST /api/recompute`) —
  appears next to "Définitions" whenever a grid is on screen in play
  mode. Rewrites every clue for the current grid from scratch, keeping
  the grid layout and answers exactly as they are, and shows the result
  in place of the current grid. The grid it started from is never
  changed: the recomputed grid is saved as a separate library entry
  whose title gets a version marker — the first recompute of "Graines"
  is titled **"Graines (V2)"**, recomputing that gives **"(V3)"**, and
  so on.
- **Bibliothèque / Library** (`#library-btn`) — always visible; opens or
  closes the library panel (see "Library" below).

## Status line

A single line (`#status`) right below the form reports what's currently
happening: the generation's live progress (which phase it's in, how many
attempts/words tried so far — `frontend/static/script.js`,
`describeStep`), a final success/failure message, or an error
(`describeErrorCode`). While the connection to the server is briefly
interrupted during a generation, this line shows a "reconnecting" message
and retries a few times on its own before giving up (`pollJob`,
`POLL_RECONNECT_ATTEMPTS`) — the generation itself keeps running on the
server the whole time regardless of what this browser tab can currently
reach.

The server only ever builds one grid's pattern and writes one grid's
definitions at a time (`backend/app.py`, `GRID_QUEUE`/`CLUES_QUEUE`), to
avoid overloading the machine when several people generate grids at
once. If another generation is already using the relevant stage, this
line instead shows a "queued" message with your place in line (`step.
code` `"queued_grid"`/`"queued_clues"`) — it also suggests playing a
grid from the Library while you wait, and notes that your own grid will
be added there automatically once it's done. A generation that's been
running for a very long time on one of these two stages, with someone
else waiting behind it, briefly steps aside for that next person before
picking back up right where it left off — you may see your own queue
position appear again partway through an otherwise-long generation.

## Dictionary

The **Dictionnaire / Dictionary** button (`#dictionary-btn`) opens a panel
for looking words up. It shares the central area with the Library, the
generation preview and the finished grid — opening it hides whichever of
those was showing.

- A language selector (`#dictionary-language`) picks which language's
  dictionary to search. It starts on the interface language, and while a
  grid is on screen it follows whichever word you hover or click in the
  grid (so on a bilingual grid it switches between the two languages as
  you move around).
- Type an expression in the field and use one of three buttons:
  - **Chercher / Search** (`#dictionary-search-btn`) — lists every word
    sharing the same root as what you typed (accents and case ignored),
    each with its real definitions, in a table
    (`frontend/static/script.js`, `renderDictionaryResult`; `GET
    /api/dictionary`).
  - **Thématique / Theme** (`#dictionary-similar-btn`,
    `frontend/static/script.js`, `renderSimilarWordsResult`; `GET
    /api/similar_words`) — works exactly like the grid's own theme
    glossary: the language model first expands what you typed into a short
    keyword list (nouns, verbs, adjectives…), then a separate vector
    search is run for each keyword and the results are merged. You get
    *every* word whose *meaning* is close enough to any of those keywords,
    most similar first, on a single line separated by commas, each with
    its similarity score in parentheses (e.g. `CHAT (0.89)`, two
    decimals). "Close enough" is set by the **Précision thématique** field
    above — there is no limit on how many words come back, only that
    cutoff — so a broad expression can return many words and a very
    specific one only a handful. Because the model expands the term first,
    this can take a few seconds and interprets an ambiguous single word
    its own way (use **Chercher** for a literal same-root lookup). It uses
    a separate vector database (Qdrant), the embedding server and the LLM;
    if any isn't running, or the word index hasn't been built yet, it
    shows a short "unavailable" message instead.
  - **Définir / Define** (`#dictionary-define-btn`, `frontend/static/
    script.js`, `renderDefineResult`; `GET /api/dictionary/define`) —
    asks the same LLM that writes grid clues to write up to 10
    independent definitions of what you typed, one per line. This can
    take up to a minute or so, especially on a small local model. If the
    LLM is unreachable, it shows a short "unavailable" message instead.
- **Effacer / Clear** (`#dictionary-clear-btn`) empties the field and all
  results. Each search (of any of the three kinds) stacks its own result
  block at the top, newest first, until cleared.

## Qdrant (admin)

A **"Qdrant (admin)"** button appears in the action row **only when the
page is opened on the machine that runs the app itself** (localhost) —
never from another computer on the network. It opens a small maintenance
panel for the vector database behind "Similar words"
(`frontend/static/script.js`, `renderQdrantAdmin`; `GET /api/qdrant/admin`,
gated to loopback requests by `frontend/server.py`, `_require_localhost`):

- The current state: whether Qdrant is reachable, the vector size /
  distance / storage mode, its status, total and indexed point counts,
  segment count, whether the per-language index is in place, and a count
  of stored words per language.
- **"Ouvrir le tableau de bord Qdrant" / "Open the Qdrant dashboard"** —
  a link to Qdrant's own built-in web console (in a new tab).
- **"Recréer la collection" / "Recreate the collection"** — drops and
  rebuilds the collection (asks for confirmation first; this deletes
  every stored word of every language).
- **"Vider" / "Clear"** on each language row — deletes just that
  language's stored words (with confirmation).
- A reminder of the command that refills the database:
  `python -m data_builder.qdrant_populate --all`.

## Library

Opened with the **Bibliothèque** button (`#library`, `frontend/static/
script.js`, `renderLibraryList`). Lists every grid ever saved on this
server (`backend/grid_store.py`, `GET /api/library`), one row per grid:
its language, creation date, title, the theme words it was generated with
(if any), difficulty, size, and the pseudo of
whoever generated it — a grid generated with no pseudo set is credited to
"Falcon Auto Bot" — sorted with
the interface's current language first, then English, then everything
else, most recent first within each group. The one exception is the
"Toutes les langues / All languages" filter, which drops that
language grouping entirely and orders the whole list purely by
creation date, most recent first (`backend/app.py`, `_library_page`).
Clicking a row (or pressing
Enter/Space on it) loads that grid straight into the player
(`loadLibraryGrid`), exactly as if it had just finished generating — the
same "Vérification"/"Solution" buttons become available.

The last columns of each row are links and one action button. **Lien /
Link** ("Jouer" / "Play") is a shareable address —
`https://falcon.cubaix.com/?grid=<id>` — that opens the site with that
grid already loaded into the player; you can also paste `?grid=<id>` onto
your own local address. Next to it, a small pencil icon
(`.library-interactive-btn`, `renderLibraryList`) opens that grid in
Interactive authoring mode (see "Interactive mode" below) with every
letter and clue already in place, so you can edit an existing grid; doing
so creates a brand-new entry in your **Créations** list on the first
autosave and never changes the stored library grid. **PDF** (a small
red PDF icon) downloads a printable sheet of the grid: the empty grid,
its clues and its title only — never the answers — with a footer line
linking back to play it online, with its solution, at the same shareable
address.

A grid created this way (`backend/grid_store.py`, `save_grid_json`'s own
`origin` field — a snapshot of the original grid's title, author and
creation date taken the moment editing starts, never re-read from it
later) keeps a note of the original grid it was edited from — published
under **Publish**, its row in the Library shows that provenance right
under the new title, e.g. "(created from *Original title* / *Author*
*date*)" (`renderLibraryList`, `.library-origin-tag`); a grid that isn't
derived from an existing one (a fresh Interactive session, or an
automatically generated grid) never shows this line. Drafts saved to
your **Créations** list along the way keep the same note, so it survives
even if you pause and resume the editing session before publishing.

The list shows at most 20 rows per page (`backend/app.py`,
`LIBRARY_PAGE_SIZE`); **◀**/**▶** buttons (`#library-pagination`,
`#library-prev-btn`/`#library-next-btn`) move between pages, and a
"Page X/Y" indicator (`#library-position`) shows the current position.
Reopening the button always starts back at page 1.

A language filter (`#library-language-filter`, right after "Toutes les
langues / All languages") narrows the list to one language, or to
**Bilingue / Bilingual** — every grid whose down words are in a different
language from its across words (`backend/grid_store.py`, `save_grid_
json`'s own `bilingual` field). A bilingual grid's own row shows both
language codes side by side in its language column (e.g. "Français
(fr/en)"), and it only ever shows up under "Bilingue" or "Toutes les
langues" — picking one specific language (e.g. "Français" alone) hides
every bilingual grid, even one whose across words happen to be in that
exact language (`backend/app.py`, `_library_page`).

A level filter (`#library-difficulty-filter`, in the panel's top-right
row alongside the other filters) narrows the list to one difficulty —
**Tous les niveaux / All levels** (the default), **Facile / Easy**,
**Moyenne / Medium**, or **Difficile / Hard**. Unlike the language
filter, it does not follow the interface language; it stays on "Tous les
niveaux" until changed. Filtering happens on the server before
pagination, so the page count reflects only the matching grids
(`backend/app.py`, `_library_page`).

A "seen" filter (`#library-seen-filter`) offers **Toutes les grilles /
All grids** (the default), **Non vues / Not seen yet**, **Déjà vues /
Already seen**, and **Mes grilles / My grids** — the last one keeps only
grids whose author pseudo matches the one currently set (nothing if no
pseudo is set).

A ↻ button (`#library-refresh-btn`, next to the ✕ close button) re-loads
the current page with the current filters, without jumping back to page
1 — useful to pick up a grid someone else just generated, or a "seen"
status changed elsewhere, without losing your place in the list.

## While a grid is generating

A dedicated panel (`#attempt-preview`, `frontend/static/script.js`,
`renderAttemptPreview`) appears below the status line and shows a live,
moving snapshot of the search: up to several small preview grids per
step (one per attempt running in parallel), each annotated with how
full/black it currently is and, when something went wrong on that
attempt, which cells are involved. A green outline marks whichever
preview is currently considered the best candidate. **⏮ ◀ ▶ ⏭** buttons
(`showFirstPreview`/`showPreviousPreview`/`showNextPreview`/
`catchUpPreviewToEnd`) and a "Étape X/Y" position indicator
(`#attempt-preview-position`, `renderPreviewPosition`) let a player step
back through earlier moments of the search rather than only ever seeing
the latest one; it keeps auto-advancing on its own (`autoFollowPreview`)
unless a player has manually stepped back.

Once clue writing starts, a live list of definitions (`#live-clues-wrap`,
`frontend/static/script.js`, `renderLiveClues`) grows underneath these
preview grids as each one is produced — this list is always visible, so
the definitions themselves are never treated as a spoiler; only the
matching grid word/answer for each one is shown once the **Voir** button
is turned on.

Letters inside these preview grids, and a word-verification table
underneath them (`#word-verification-wrap`, listing every word placed so
far, whether it's a real dictionary entry, and its dictionary/glossary
source line), are hidden until the **Voir** button is turned on — this
panel is diagnostic, not part of the puzzle, but its letters can still
spoil the answer if shown by default.

## The finished grid

Once generation completes, the search-progress panel disappears and
`#result` appears in its place (`frontend/static/script.js`,
`displayFinalGrid`):

- **Grid title** (`#grid-title`) — a short, LLM-generated title for the
  puzzle, based on its own words (`backend/clues.py`, `generate_title`),
  followed by the grid's difficulty as a full phrase ("Difficulté :
  Moyenne", translated to the interface language,
  `frontend/static/script.js`, `renderGridDifficulty`). The whole line
  is shown whenever there is a title or a difficulty to display.
- **Stats line** (`#stats`) — the finished grid's own black-cell
  percentage, fill percentage, and unplayable-cell percentage.
- **Generation times** (`#generation-times`) — how long grid generation,
  optimization, and clue writing each took. Its own **◀**/**▶** buttons
  (`#generation-times-prev-btn`/`#generation-times-next-btn`) let a
  player revisit the same step-by-step search history the in-progress
  preview panel showed, now that the grid is finished.
- **The grid itself** (`#grid`, `frontend/static/script.js`,
  `renderGrid`) — a black-and-white crossword grid with 1-based
  row/column headers. Click a white cell to select it (`selectCell`, a
  black cell can't be selected) — the clicked cell turns light blue, and
  the rest of the word running through it in the current Across/Down
  direction is tinted light green (`applySelectedWordHighlight`), so it
  is clear which word is being filled. Type a letter to fill the cell
  and move to the next cell in the current Across/Down direction
  (`handleKeydown`, `moveSelection`) — so setting the direction with
  **Ctrl** or the Across/Down buttons also controls which way typing
  advances, not just Shift/Caps Lock. Typing an **uppercase** letter
  (or holding Shift, or with Caps Lock on) still forces a downward
  advance — an uppercase key implies a vertical word, and Shift/Caps Lock
  set the direction to down anyway. Pressing **Ctrl** flips the current
  Across/Down direction on each tap (`toggleDirectionOnCtrl`) —
  press-to-toggle, unlike Shift/Caps Lock which only change direction
  while held. Switching direction any of these ways (or with the
  Across/Down buttons) re-tints the green band along the other word
  through the same cell. Backspace/Delete clears the selected cell
  without moving. The **arrow keys** move the selection to the next white
  cell in that direction, skipping black cells and stopping at the grid
  edge (`moveSelectionArrow`) — the same navigation Interactive mode has.
  Hovering a cell (or a clue line, see below) outlines every cell of that
  same word (`wordCellsAt`) and shows that word's own clue in a fixed
  5-line panel underneath the grid (`#hover-definition`). When nothing is
  hovered, that panel shows the clue of the currently selected (clicked)
  word instead — the word running through the selected cell in the
  current Across/Down direction (`renderHoverDefinitionForSelection`) —
  and only falls back to a generic help line when no cell is selected.
  A small `→`/`↓` pair beside the panel switches the Across/Down
  direction (the same shared state as the virtual keyboard's own arrows,
  Shift/Caps Lock, and grid hover); switching it with a cell selected
  immediately updates the panel (and the green word band) to the other
  word through that cell.
- **Horizontalement/Verticalement (Across/Down)** clue lists
  (`#clues-across`/`#clues-down`, `renderClueLines`) — every word's own
  clue, grouped by its starting cell number; hidden until "Définitions"
  is turned on. Hovering a clue line highlights its word in the grid, the
  same as hovering the grid highlights its clue.

## Building a grid

To hand-build a grid yourself (rather than let an automatic generation fill
one in), start from the home page:

1. Set up the grid using the options on the home page (Language, size,
   Difficulty, Taux noir, etc. — see "Generation form" above).
2. Choose **Interactif / Interactive** in the **Mode** selector.
3. Optionally, list some words in the **Thématique / Theme** field to steer
   the grid toward a topic (see "Generation form" above).
4. Click **Générer la grille / Generate the grid**.

The same guidance below is also available in-app, once the interactive
session has started: click the **?** button (`#interactive-help-btn`) just
to the left of **Mots / Words** to open it as an overlay panel.

- Place your letters in the grid. The Space key adds or removes a black
  cell.
- The **Suivant / Next** button automatically generates a new word (taking
  any theme glossary into account). The **Précédent / Back** button undoes
  a step (for example so Next generates a different word).
- Use the tools to help you: **Dictionnaire / Dictionary**, **Paraphraseur
  / Paraphraser**, the **Mots / Words** button lists the words compatible
  with the selected slot.
- Two buttons clean up the grid's impossible zones, with or without
  removing black cells.
- The **Impossibles / Impossible** button identifies zones where no word
  fits any more. **Vérifier / Check** makes sure every word is really in
  the dictionary and has a definition.
- The **Définitions / Definitions** button automatically generates the
  missing definitions.
- The **Proposer une définition / Suggest a definition** and **Proposer un
  titre / Suggest a title** buttons help you with several proposals.
- Make sure every placed word has a definition.
- Click **Finir la grille / Finish the grid** so Falcon fills in the cells
  that are still empty.
- At the end of the automatic process, delete any words you don't like,
  then go back to placing letters, black cells and definitions. You can run
  **Finir la grille / Finish the grid** again as many times as needed.
- Remember to save.
- Once the grid is complete and you're happy with it, click **Publier /
  Publish**. You'll find it in the **Library**, where you can copy a link to
  play it or export it as a PDF.

See "Interactive authoring mode" right below for the full, detailed
reference of every one of these buttons.

## Interactive authoring mode

Choosing **Interactif / Interactive** in the **Mode** selector and
submitting the form starts a hands-on authoring session
(`frontend/static/script.js`, `runInteractive`, `POST /api/interactive/
start`) instead of an automatic generation. The form's Language, size,
Difficulty, Taux noir and Thématique fields still apply — they shape the
black-cell pattern and, if Thématique is filled in, a preferred-word
glossary. The status line briefly shows "Building the interactive grid…"
while the server prepares a pattern with one word already placed, then
the grid appears in the usual result area with a control strip below it
(`#interactive-controls`). The Dictionary panel (see "Dictionary" above)
opens automatically the moment the session starts, for looking words up
while filling in the grid by hand; it never steals keyboard focus away
from the grid to do so.

Opening an existing grid for editing (the Library's own pencil icon, or
resuming a "Créations" draft — see "Library" below) re-fills the
Thématique field from that grid's own theme (`enterInteractiveMode`,
reading `theme` off the started/resumed session's own result) rather
than leaving it at whatever it previously held or blank — so a themed
grid keeps steering both "Suggest a definition"/"Suggest a title" (see
below) toward its own theme once you start editing it, with no need to
retype it by hand.

**Building the grid**

- **Suivant / Next** (`#interactive-next-btn`, `POST /api/interactive/
  step`) — places exactly one more word, choosing the spot where the
  fewest words still fit and preferring a theme word where one does. If
  no word fits anywhere, it leaves the grid unchanged and shows either
  "Grid complete and valid" (when every white cell is filled and every
  word is a real dictionary entry) or "Grid has become impossible"
  (otherwise — edit it or step back, then try again).
- **Précédent / Back** (`#interactive-prev-btn`) — undoes the last change
  one step at a time: a typed letter, a black-cell toggle, or a whole
  "Suivant" placement. Disabled once there is nothing left to undo.
- The grid is fully editable, black cells included. Click any cell, then:
  type a letter to fill it; press **Space** (or the **■** key on the
  virtual keyboard) to turn it black or white; press **Backspace** or
  **Delete** to clear it. The cursor advances in the current Across/Down
  direction after a letter.
- **→ / ↓** (`#interactive-dir-across-btn`/`#interactive-dir-down-btn`) —
  set which direction the cursor advances and which word counts as
  "selected"; the same shared state as the virtual keyboard's arrows,
  Shift/Caps Lock, and the **Ctrl** key (each Ctrl tap flips this
  direction, `toggleDirectionOnCtrl`). They do **not** influence which
  word "Suivant" places.

**Writing the definitions**

- The text field below the grid holds the definition of the currently
  selected word; typing there stores it for that word, and it comes back
  when you reselect the word.
- **Proposer une définition / Suggest a definition**
  (`#interactive-propose-btn`) — asks the language
  model for several possible definitions of the selected word (it must be
  fully filled in); click one of the suggestions to drop it into the
  field. When the Thématique field (see above) has something in it, the
  suggestions lean toward that theme whenever the word's own real
  meaning leaves room for it — a definition that would become wrong is
  never sacrificed for the theme.
- **Vérifier / Check** (`#interactive-verify-btn`) — checks that every
  word has a definition; if one is missing, it selects that word (and
  switches the Across/Down direction to match) so you can fill it in.
- **Définitions / Definitions** (`#interactive-definitions-btn`,
  `frontend/static/script.js`) — generates a definition automatically for
  every fully filled-in, valid word that does not have one yet: it first
  checks each candidate word against the dictionary (words absent from it
  are skipped), then asks the language model for a definition of each
  remaining word and keeps the first suggestion. It works through the
  words one at a time and shows its progress; a word whose definition
  could not be produced is simply left blank.
- **Finir la grille / Finish the grid** (`#interactive-finish-btn`,
  `POST /api/interactive/finish`) — permanently locks every letter
  already placed and hands the grid off to the automatic generation
  engine, which fills in whatever is left (adding new black cells/words
  wherever still needed) and writes a definition for any word that does
  not already have one, leaving every existing definition untouched.
  Clicking it leaves Interactive mode and shows the exact same live
  attempt-preview grids and progress reporting as an ordinary automatic
  generation, ending with the same playable, finished grid. In those
  preview grids, every cell already carrying a letter at the moment you
  clicked is framed in light green — distinct from the orange frame used
  elsewhere for a cell the search itself has confirmed so far, since a
  light-green letter can never be changed or removed for the rest of
  this generation. Your interactive session itself is left untouched, so
  it stays available in your "Créations" list.

**Finishing**

- Once every white cell is filled and every word has a definition, a
  **title** field appears, automatically pre-filled with one language-
  model suggestion the first time — you can edit it freely from there,
  or pick a different one from the list right below it (see the next
  bullet).
- **Proposer un titre / Suggest a title** (`#interactive-title-propose-btn`,
  `POST /api/interactive/title`) — asks the language model for up to 10
  distinct title ideas for the whole grid and lists them right below the
  field, the same clickable-list behavior as "Suggest a definition"
  above; click one to drop it into the title field. Clicking this button
  never touches whatever is already typed there on its own — only
  picking a line from the list does. Like "Suggest a definition", it
  leans toward the Thématique field's own theme when one is set.
- **Sauvegarder / Save** (`#interactive-draft-save-btn`, `POST /api/
  interactive/save_work`) — saves the whole current grid, its definitions
  and its title to your "Créations" list as a draft, **without**
  publishing it. Available the whole time you are building the grid, so
  you can keep the draft up to date between (or without) placing more
  words.
- **Publier / Publish** (`#interactive-save-btn`, `POST /api/interactive/
  save`) — appears to the right of "Save" once the grid is complete
  (every white cell filled, every word defined). It stores the grid in
  the Library with your nickname as the author, tagged **(Création)**
  next to the name in the Library list, and also refreshes this
  session's own "Créations" entry with the final published version.
  Deleting a "Créations" entry later only removes the draft, never the
  published Library grid.

## David FALCON (chat assistant)

A small chat panel (`#chatbot`, `frontend/static/script.js`), fixed to
the bottom-right corner of the page regardless of scroll position, open
by default. Its title bar shows the app's own icon and the assistant's
name; a button next to it (`#chatbot-toggle-btn`) collapses the panel
down to just this title bar, or reopens it.

A welcome message greets the player as soon as the page loads
(`renderChatWelcome`), inviting them to ask for help using the interface
or for hints solving the current grid; switching the interface's own
language rewrites this greeting into the new language, as long as no
real message has been sent yet.

Typing a message and pressing "Envoyer" (or Enter) sends it to David
FALCON (`POST /api/chat`, `backend/chatbot.py`), along with the current
conversation so far and a snapshot of the interface's own state: whether
a grid is loaded, which cell is currently selected in it, and — if a
grid is loaded — every one of its words with their starting position,
direction, clue, and answer. David FALCON replies in the
interface's current language, and only ever answers questions about
using this app or about the grid currently on screen — for anything
else, it politely suggests looking elsewhere instead. On a bilingual
grid (see the **Bilingue / Bilingual** field above), the reply language
follows the grid instead: whenever a word is selected — hovered, or the
word being filled at the clicked cell in the current Across/Down
direction — David FALCON writes its *entire* reply in that word's own
language, which can differ between an across word and a down word. With
nothing selected, it uses the interface language.

## How a grid is actually built (a summary of the generation algorithm)

This section summarizes, in plain English, how `backend/crossword_gen.py`
builds a filled crossword grid — the full, authoritative technical
reference is `DOC_ALGO/FR/ReadMe.md` (in French); this is a condensed,
player-facing version of the same material, kept in sync with it. It
exists so a curious player can understand *why* a generation sometimes
takes a while, why the preview panel shows several grids at once, or why
the "Continuer" button exists, without needing to read that longer
technical document.

On a bilingual grid (see the **Bilingue / Bilingual** field above), every
stage described below works exactly the same way, with one addition:
whenever a word is needed for a horizontal slot, only the primary
language's own dictionary is ever consulted; for a vertical slot, only
the bilingual field's own dictionary is — the two are never mixed within
one grid.

**Placing the black cells.** The generator starts from a completely
white grid and adds black cells one at a time, entirely independently —
there is no requirement that the pattern be symmetric, which lets it
reach far sparser layouts (and so grids with far more visible letters)
than a traditional symmetric crossword would. Every candidate cell has to
respect a few hard rules: a white cell can never end up boxed in on all
four sides (it would belong to no word at all and could never receive a
letter); the white area of the grid must stay fully connected, never
split into isolated pockets by a wall of black cells; and an ordinary
interior word slot should normally be at least 8 cells long, unless one
of its ends touches the grid's own border, in which case any length is
allowed. Before this placement even starts, a separate "pre-fill" pass
runs first: as long as some slot's own length is covered by too few
dictionary words to be safely fillable, more black cells are added
specifically to shorten it, and this pre-fill is itself intertwined with
whatever content already survived from an earlier cycle — a slot whose
locked letters leave it with too few *actually matching* candidate
words gets shortened the same way, by removing a black cell from right
within that slot's own cells (never from some unrelated part of the
grid), or, if that isn't enough, by removing one of the crossing words
that pinned those letters in place to begin with. Each new black cell is
chosen, among a small batch of candidate positions, to fall in whichever
row and column already has the fewest black cells of its own — spreading
them out rather than letting them clump into ugly "walls" — and the
generator actively prefers a candidate that touches no other black cell
at all, only accepting one right next to another as a genuine last
resort, and never at all for the very first grid of a whole generation.

**Choosing which word slot to fill next.** Once a black-cell pattern is
accepted, every run of at least 2 white cells (across or down) becomes a
slot that needs a real dictionary word. Rather than filling slots in a
fixed reading order, the generator picks the next slot through several
layers of priority: first it leans, with some randomness, toward
whichever of "across" or "down" still has more open slots, so the two
categories fill in roughly together instead of one being finished before
the other even starts; within that category, a slot with fewer than 3
real dictionary candidates left is tackled first, on the theory that
finishing it with a genuine word now is better than letting a later
cleanup pass shorten it with a black cell instead; among what's left, a
slot that's already partly determined by a real crossing letter is
preferred over one that's still completely blank, so the generator tends
to finish what it's already started rather than opening new fronts
everywhere at once; ties are then broken by physical position (roughly
sweeping from the top-left corner of the grid), and, as a final
tie-break among a handful of similarly-placed slots, by which one's
still-open cells look statistically the most promising to fill (see the
next paragraph). A slot that crosses another slot already known to be
unfillable is skipped entirely — a word placed there would likely just
be removed again the moment the grid gets cleaned up.

**Choosing which candidate word to try for that slot.** Before any real
search even begins, the generator takes a quick statistical peek at what
the rest of the grid might plausibly look like: for every still-open
slot, it randomly samples 100 dictionary words of the right length
(filtered down to whichever ones are still compatible with any letters
already known there) and looks, cell by cell, at which letter shows up
most often. A handful of these cells, where one letter dominates clearly
enough, are picked at random to become "seeds" — soft hints that guide
the search without being real, confirmed answers; a real crossing word
placed later always overrides a seed the moment it reaches that cell.
Separately, and regardless of whether any seed was actually planted, this
same sampling is used to rank the real dictionary candidates for a slot
before trying them: a candidate whose letters line up well with the
statistical consensus on the slot's own still-undetermined cells is tried
before one that doesn't, though the very first word actually attempted is
still drawn at random from among a wide window of the best-ranked
candidates, not strictly the single best one — this keeps different
attempts from converging on the exact same choice every time. Separately
again, before any of this even runs, any slot whose already-known letters
leave exactly one real dictionary word possible has that word locked in
directly, as a plain fact rather than a mere statistical guess.

**Filling the grid by trial and backtracking.** With a slot and a
ranked/seeded list of candidate words in hand, the generator tries each
candidate in turn: it places the word, immediately checks every other
slot that shares a cell with it (never the whole grid — only a slot's own
direct neighbors can possibly be affected by one word being placed), and
if that leaves any of them with no real dictionary word left at all, the
candidate is discarded right away and the next one is tried instead — no
time is wasted descending any further into a branch that's already
doomed. If none of a slot's own candidates work out, the whole attempt to
fill that particular slot fails, and whichever slot was chosen just
before it gets its own placed word undone so a different candidate can
be tried there instead — this "undo and try something else" behavior can
ripple back through several slots at once if needed. The search finishes
successfully the moment every slot in the grid holds a real word, and
fails outright only if the very first slot ever chosen runs out of
candidates with nothing placed yet at all — meaning the current pattern,
combined with whatever letters were already fixed coming in, has no valid
solution whatsoever. To keep a single hard grid from searching forever,
every single candidate word actually tried (whether or not it leads
anywhere) counts against a budget of "checks" (300,000 by default on a
15×10 grid, or a fixed value chosen directly through the "Mode" selector
in the interface) — once that budget runs out, the current attempt is
abandoned and the generator moves on rather than grinding away
indefinitely on a hopeless case.

**Many attempts running in parallel, and carrying progress forward
between cycles.** Rather than only ever trying one pattern-and-fill
combination at a time, each cycle ("palier") launches as many independent
attempts in parallel as the machine has processor cores, each running in
its own process. If more than one of them actually succeeds in the same
cycle — which happens more often than one might expect — the generator
doesn't just keep the first one that finished: every successful attempt
is genuinely optimized on its own (see the next paragraph) and whichever
one ends up with the fewest black cells afterward is the one that's
actually kept. If every attempt in a cycle fails instead, the generator
doesn't necessarily throw everything away and start over from a blank
grid: as long as the best of that cycle's own failed attempts still has
at least one slot worth trying and a cycle-count limit hasn't been
reached yet, the very next cycle simply picks the search back up on the
exact same pattern, still holding onto everything already confirmed —
this can repeat for several cycles in a row before a deeper cleanup ever
becomes necessary. Only once a pattern genuinely has no realistic path
left forward does the generator actually simplify it: it removes every
word that crosses a now-hopeless slot, decides afterward which black
cells are still needed to bound whatever survived, and hands that leaner,
smaller pattern to the next cycle instead of a blank one — real words and
black cells that were never part of the problem are preserved throughout.
Only when even that cleanup keeps producing the exact same stuck state
several times in a row does the generator finally give up on the pattern
entirely and restart one fresh, independent attempt from a completely
blank grid (never all of them at once, so most of the next cycle's
attempts still build on whatever already-cleaned progress exists). If an
entire generation exhausts its full budget of cycles (200 by default)
without ever succeeding, the "Continuer" button lets the player relaunch
another full budget of cycles picking up from exactly that same
carried-forward state, rather than starting over from nothing.

**Optimizing the finished grid.** Once some cycle finally produces a
grid where every single slot holds a real word, one last pass tries to
remove even more black cells from it, one at a time: each black cell is
temporarily turned back into a white cell and a fresh, smaller fill is
attempted right there; if the grid is still structurally sound and every
resulting word is a genuine dictionary entry, the black cell stays
removed — otherwise it's put right back and the next one is tried. This
whole grid is swept over and over, in a freshly shuffled order each
time, for as long as at least one black cell keeps getting successfully
removed on a full pass, since removing one can sometimes free up another
that couldn't be removed before. Because a rejected removal is always
undone immediately, this step can never make an already-valid grid
worse — only denser, and only when that's genuinely still possible.
