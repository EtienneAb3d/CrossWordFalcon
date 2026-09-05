# How to use CrossWordFalcon's web page

This document describes, in plain terms, what every element of the web
page (`frontend/static/index.html`) does and how a player uses it. It says
nothing about how the interface is implemented — see `CLAUDE.md` for that.

The page has one language selector (`#language`) that drives two things at
once: the language of the crossword puzzle itself, and the language the
interface's own labels/messages are shown in
(`frontend/static/script.js`, the `languageSelect` "change" handler,
`applyTranslations`) — there is no separate "interface language" setting.

## Header

- The logo and page title sit at the top left (`#logo`, `h1`).
- A small pill next to the title (`#version-badge`) shows the app's
  current version (`GET /api/version`).
- An "i" icon next to it (`#info-badge`) reveals a tooltip on hover or
  keyboard focus (`frontend/static/script.js`, `renderSystemInfoTooltip`),
  showing which LLM model writes the clues, whether it runs on CPU or
  GPU, and — when it's a GPU — its name and available memory
  (`GET /api/system_info`).

## Generation form

Every field below (`#generate-form`) is used to start a new grid
generation (`frontend/static/script.js`, the form's own `submit` handler,
`runGeneration`).

- **Langue / Language** (`#language`) — which of the five supported
  languages (French, English, German, Spanish, Italian) the grid's own
  words and clues are written in. Also switches every label/message on
  the page to that same language.
- **Largeur / Width** and **Hauteur / Height** (`#width`/`#height`) — the
  grid's own dimensions in cells, from 5 to 30 in each direction.
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
  need a slower mode to succeed at all.
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
  (see "While a grid is generating" below) and the word verification
  table underneath them. Off by default, so a generation in progress
  never spoils the puzzle before it's ready to play.
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
  `toggleChecking`) — while playing, colors every filled cell green
  (correct) or red (incorrect) against the real solution. Turning it on
  turns "Solution" off.
- **Solution** (`#solution-btn`, `frontend/static/script.js`,
  `toggleSolution`) — reveals every letter of the finished grid and
  stops accepting typed input; toggling it off restores exactly what the
  player had typed, nothing is lost. Turning it on turns "Vérification"
  off.
- **Définitions / Definitions** (`#definitions-btn`, `frontend/static/
  script.js`, `toggleDefinitions`) — shows or hides the across/down clue
  lists below the grid. Hidden by default on a freshly generated or
  freshly loaded grid.
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

## Library

Opened with the **Bibliothèque** button (`#library`, `frontend/static/
script.js`, `renderLibraryList`). Lists every grid ever saved on this
server (`backend/grid_store.py`, `GET /api/library`), one row per grid:
its language, creation date, title, difficulty, and size — sorted with
the interface's current language first, then English, then everything
else, most recent first within each group. Clicking a row (or pressing
Enter/Space on it) loads that grid straight into the player
(`loadLibraryGrid`), exactly as if it had just finished generating — the
same "Vérification"/"Solution" buttons become available.

The list shows at most 20 rows per page (`backend/app.py`,
`LIBRARY_PAGE_SIZE`); **◀**/**▶** buttons (`#library-pagination`,
`#library-prev-btn`/`#library-next-btn`) move between pages, and a
"Page X/Y" indicator (`#library-position`) shows the current position.
Reopening the button always starts back at page 1.

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
  puzzle, based on its own words (`backend/clues.py`, `generate_title`).
  Shown only when one was successfully generated.
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
  black cell can't be selected); type a letter to fill it and move to
  the next cell of the current word (`handleKeydown`, `moveSelection`).
  Typing a **lowercase** letter continues across (rightward); typing an
  **uppercase** letter (or holding Shift, or with Caps Lock on) continues
  down. Backspace/Delete clears the selected cell without moving.
  Hovering a cell (or a clue line, see below) outlines every cell of that
  same word (`wordCellsAt`) and, once "Définitions" is on, shows that
  word's own clue underneath the grid (`#hover-definition`).
- **Horizontalement/Verticalement (Across/Down)** clue lists
  (`#clues-across`/`#clues-down`, `renderClueLines`) — every word's own
  clue, grouped by its starting cell number; hidden until "Définitions"
  is turned on. Hovering a clue line highlights its word in the grid, the
  same as hovering the grid highlights its clue.

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
direction, clue, and answer. David FALCON always replies in the
interface's current language, and only ever answers questions about
using this app or about the grid currently on screen — for anything
else, it politely suggests looking elsewhere instead.

## How a grid is actually built (a summary of the generation algorithm)

This section summarizes, in plain English, how `backend/crossword_gen.py`
builds a filled crossword grid — the full, authoritative technical
reference is `DOC_ALGO/FR/ReadMe.md` (in French); this is a condensed,
player-facing version of the same material, kept in sync with it. It
exists so a curious player can understand *why* a generation sometimes
takes a while, why the preview panel shows several grids at once, or why
the "Continuer" button exists, without needing to read that longer
technical document.

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
