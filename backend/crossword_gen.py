#!/usr/bin/env python3
"""
Dense crossword grid generator.

Two-phase approach:
  1. Black-cell pattern generation (no symmetry constraint — each black
     cell is placed independently) respecting structural rules (no
     interior slot shorter than 3 cells except at the grid border, no
     white cell orphaned in both directions at once, connected white
     grid — see is_structurally_valid). The target black-cell ratio stays
     fixed at 0% (`black_ratio`) across every palier — no more escalation
     across paliers, at the user's explicit request: "the pre-fill
     principle (with at least 10 solutions per slot), on top of keeping
     the previous grid, should be enough to make the grid progress." The
     grid is already pre-filled with black cells by the pre-fill phase
     below whenever needed to guarantee enough candidate words per slot,
     and the cross-palier retry mechanism (`_build_retry_seed`, see
     generate_grid) builds each following palier on top of what was
     already resolved at the previous palier rather than on a blank grid
     — these two mechanisms are enough to make the search progress from
     one palier to the next, with no need to artificially densify the
     grid by escalating a target ratio. See generate_grid for the former
     escalation mechanism (+2 points per failure, up to 45%) and its own
     tuning history, replaced by this simpler principle. Up to 200
     paliers (raised from 40, at the user's explicit request, after a
     report of cycles getting stuck very quickly — some grids need
     considerably more than 40 paliers to find a way through via the
     cross-palier retry mechanism). At each palier, `PARALLEL_ATTEMPTS`
     (this machine's own CPU count by default, configurable via
     CROSSWORDFALCON_PARALLEL_ATTEMPTS in env.sh) independent attempts
     (full pattern + CSP fill) are launched in parallel across separate
     processes — since the machine is typically far from saturating its
     CPU with a single attempt at a time, this parallelism gives several
     chances per palier for a real-time cost close to that of a single
     attempt; if several succeed at the same palier, whichever maximizes
     the sum of squares of all its own word lengths is kept, not simply
     the first one found.
  2. CSP fill (backtracking) against a real dictionary, then local
     minimization: try removing each black cell one at a time and only
     keep the removal if the grid stays fillable.

The grid can be rectangular: `width` (number of columns, horizontal) and
`height` (number of rows, vertical) are set independently (15x10 by default).

Usage (from the project root):
    python3 backend/crossword_gen.py --width 15 --height 10 --wordlist data/wordlist_fr_full.tsv
"""
import argparse
import concurrent.futures
import math
import multiprocessing
import os
import queue
import random
import re
import sys
import threading
import time
from collections import Counter, defaultdict

BLACK = "#"
WHITE = "."


class GenerationCancelled(Exception):
    """Raised by generate_grid()/minimize_black_squares() when the
    optional `cancel_event` (threading.Event) they were given fires along
    the way — at the user's explicit request (the web UI's "Stop"
    button, see backend/app.py), to interrupt a generation in progress
    whatever the current phase (pattern search, minimization, clue
    generation — the last one inside backend/clues.py's LLMClueGenerator.
    generate(), which raises the same exception). A purely *cooperative*
    signal: every long-running loop involved checks the event at its own
    natural checkpoints (between two paliers, between two removed black
    cells, between two words) and raises this exception rather than
    continuing — never a forced interruption of a thread or subprocess
    already mid-execution (see generate_grid's own docstring for the
    limit this implies: stopping can take until the end of whichever
    checkpoint is currently in progress, not instantaneous)."""


class GenerationPaused(Exception):
    """Raised by generate_grid() when the optional `should_pause`
    (callable) it was given returns true at the boundary between two
    paliers — at the user's explicit request: "When a grid or clue
    generation has been running for more than 15 minutes, and there are
    tasks waiting in the current phase's queue, when moving to the next
    cycle..., put the current task back at the end of that phase's
    queue... Tasks must be resumable exactly where they were
    interrupted." Unlike `GenerationCancelled` (a final, user-requested
    stop that discards all accumulated state), a pause is meant to resume
    later exactly where it left off — `resume_state` therefore carries
    the serializable state (`_serialize_resume_state`, the same mechanism
    already used for the "Continuer" button) needed for a future call to
    `generate_grid(resume_state=...)` to resume the next palier exactly
    as if no pause had ever happened — never `None` except in the
    degenerate case where the palier loop has never run at all yet
    (`attempt == 0`, `carry_seed_grid` still `None`)."""

    def __init__(self, resume_state):
        super().__init__("generation paused (queue turn yielded)")
        self.resume_state = resume_state


DEFAULT_WIDTH = 15
DEFAULT_HEIGHT = 10

# Number of attempts (pattern + CSP fill) launched in parallel at each
# black-cell-ratio palier — see generate_grid(). The machine is typically far
# from saturating its CPU with a single sequential attempt at a time, so
# running one attempt per available CPU makes full use of it. Defaults to
# `os.cpu_count()` (the number of CPUs this machine reports, at the user's
# explicit request: "Nombre de process lancés en parallèle = nombre de
# processeurs de la machine" — replacing a previous fixed default of 10,
# which had no relationship to the actual hardware a given deployment runs
# on) — `or 1` guards the documented edge case where `os.cpu_count()` itself
# can't determine a count and returns `None`, so this never becomes `0` (which
# would make `ProcessPoolExecutor(max_workers=0)` fail outright). Still
# overridable via the CROSSWORDFALCON_PARALLEL_ATTEMPTS environment variable
# (see env.sh/env_default.sh — sourced by run_Falcon.sh before starting the
# back end, so effective for the web API; the CLI, launched directly, reads
# it too if already exported in the current shell), at the user's own earlier
# explicit request, for a deployment that wants a different number regardless
# of the machine's own core count.
PARALLEL_ATTEMPTS = (
    int(os.environ["CROSSWORDFALCON_PARALLEL_ATTEMPTS"])
    if os.environ.get("CROSSWORDFALCON_PARALLEL_ATTEMPTS")
    else (os.cpu_count() or 1)
)

# Niceness increment applied to every CSP-search worker process (see
# _init_worker below), at the user's explicit request: "Configurer mes
# process de génération en priorité système basse pour que les demandes en
# provenance de l'interface (ChatBot, Dictionnaire, Paraphraseur, etc)
# soient traitées prioritairement." These worker processes are the one
# genuinely CPU-heavy part of a generation (up to PARALLEL_ATTEMPTS of them
# running flat-out in parallel, one per palier) — on a machine that also
# runs the local LLM/embedding servers on CPU (see run_llm.sh's
# LLAMA_FORCE_CPU, run_embed.sh's default), or that simply has every core
# busy with a generation, a request that only needs a quick round-trip to
# the LLM server (a chat reply, a dictionary definition, a paraphrase) can
# end up waiting behind these workers for CPU time even though it does far
# less actual computation itself. Raising their own niceness (a HIGHER
# nice value means LOWER scheduling priority — never touches the LLM/
# embedding server processes, nor the backend/frontend processes
# themselves, which stay at the OS default) asks the kernel scheduler to
# favor every other process on the machine whenever CPU is contended,
# without slowing generation down at all when the machine is otherwise
# idle (niceness only ever matters under real contention). `os.nice()` is
# POSIX-only (Linux/macOS, this project's only two supported platforms —
# see run_llm.sh's own Metal/CUDA/CPU branching) and requires no special
# privilege to *raise* one's own niceness (only lowering it, i.e. asking
# for higher priority, needs CAP_SYS_NICE/root) — safe to call
# unconditionally from a plain worker process. Overridable via
# CROSSWORDFALCON_GENERATION_NICE (same env.sh/env_default.sh convention
# as CROSSWORDFALCON_PARALLEL_ATTEMPTS above) — set to `0` to disable this
# entirely and restore the OS default priority for generation workers.
GENERATION_PROCESS_NICE_INCREMENT = (
    int(os.environ["CROSSWORDFALCON_GENERATION_NICE"])
    if os.environ.get("CROSSWORDFALCON_GENERATION_NICE")
    else 10
)

# The number of failed-attempt examples shown in the preview (see
# generate_grid's "pattern_attempt_failed"/"pattern_failed", and
# frontend/static/script.js's renderAttemptPreview) is no longer capped at
# a fixed value (`FAILED_ATTEMPT_EXAMPLES`, formerly 6) — at the user's
# explicit request: "Show every best grid in the preview, not just the 6
# best." Every distinct attempt of the palier (up to PARALLEL_ATTEMPTS,
# this machine's own CPU count by default) is now shown, with no
# truncation at all — displayed across as many rows of 3 grids as needed
# (`grid-template-columns: repeat(3, auto)`, see style.css, which never
# imposed a fixed row count and so needed no change at all for this
# removal). Full history (the first version, a single example; then a
# fixed cap of 6 over 2 rows of 3; then this complete removal of the cap)
# in CLAUDE.md.


# ---------- Dictionary ----------

# Difficulty presets: fraction of the lexicon kept (global ranking by
# frequency, across every length combined), not per length — a per-length
# cap filters nothing for a length that has fewer total words than the
# cap itself (e.g. French only has ~700 3-letter words, so a former
# per-length cap of 600 let through EVERY 3-letter word, including
# obscure ones like "ABD" — a real bug reported by the user, score 103,
# ~33,000th global position). Fewer words -> more recognizable vocabulary
# but a grid that's sometimes harder to fill; "hard" keeps the whole
# lexicon (100%).
#
# Deliberately a FRACTION of each language's own lexicon, not an absolute
# word count — at the user's explicit request, after noticing a fixed
# threshold (e.g. 80,000 words) has a very different effect depending on
# the language: French has ~113,000 words in its frequency table, German
# ~436,000 (German compounds words heavily, which inflates its
# vocabulary) — the same absolute threshold of 80,000 would keep ~70% of
# the French lexicon but only ~18% of German's, making "easy" noticeably
# harder in German than in French without that being intended.
# `load_wordlist()` computes the real word count from this fraction once
# the language's lexicon has actually been loaded (i.e. after "easy"'s
# own `require_gloss` filtering, if any) — see `max_words` in
# `load_wordlist()`, which tells a fraction (float, 0 < x <= 1) apart from
# an absolute count (int, always the behavior of `--max-words` on the
# command line) by its type.
DIFFICULTY_PRESETS = {
    "easy": 0.66,
    "medium": 0.80,
    "hard": 1.0,
}

# Maximum number of proper-noun-looking words (see PROPER_NOUN_
# EXCLUDED_LANGS/exclude_proper_nouns below) tolerated in the final grid,
# per difficulty — at the user's explicit request: "at EASY difficulty
# never allow placing proper nouns, at MEDIUM allow at most 2 proper
# nouns, at HARD allow up to 5 proper nouns." Replaces the previous
# all-or-nothing rule (`exclude_proper_nouns=(difficulty in ("easy",
# "medium"))`, which excluded proper nouns from the lexicon entirely at
# both easy and medium) with a genuine per-grid budget for
# "medium"/"hard" — "easy" is still covered by `load_wordlist`'s own
# `exclude_proper_nouns` (no proper noun even enters the lexicon at that
# difficulty, so this budget of 0 is redundant but harmless there).
# Applied as a final safety net inside `try_fill` (see its own docstring)
# rather than as an active constraint inside `Filler._backtrack` itself —
# exceeding the quota is treated exactly like any other fill failure (the
# palier fails, the already-existing cross-palier retry mechanism retries
# normally), rather than risking a deep change to this area of the file,
# documented as especially fragile.
MAX_PROPER_NOUNS = {"easy": 0, "medium": 2, "hard": 5}

# Same principle as MAX_PROPER_NOUNS above, applied the same way (a final
# safety net inside `try_fill`, never an active constraint inside
# `Filler._backtrack`), but for words absent from the definitions
# dictionary `data/gloss_dictionary/<lang>_glosses.jsonl` — no entry for
# any of their canonical forms, the same signal `load_wordlist(require_
# gloss=...)` already uses. At the user's explicit request: "EASY: no
# unknown word at all; MEDIUM: at most 2 unknown words; HARD: at most 5
# unknown words." For "easy", `load_wordlist(require_gloss=True)` already
# strips these words from the lexicon outright upstream, so the `non_
# gloss_words` set computed in `generate_grid` comes out empty and this
# quota (0) only duplicates an already-guaranteed property, never actually
# getting a chance to apply.
MAX_NON_GLOSS_WORDS = {"easy": 0, "medium": 2, "hard": 5}

# Languages where "Hunspell only validated the title-cased form" (see
# load_wordlist's own exclude_proper_nouns) carries no proper-noun signal
# at all: German capitalizes every noun, common or proper, so this exact
# same detection would just flag ordinary nouns ("Haus") instead — see
# build_wordlist_freq.py's own PROPER_NOUN_LANGS/PROPER_NOUN_SCORE_FACTOR,
# which this mirrors at runtime (the wordlist TSV itself never stores this
# flag directly — only the ACCENTED column's own capitalization does, since
# every corpus word is counted lowercase in build_wordlist_freq.py's own
# _count_word_frequencies, so ACCENTED only ever ends up capitalized when
# Hunspell needed the title-cased form to validate it in the first place).
PROPER_NOUN_EXCLUDED_LANGS = {"de"}


def _lang_from_path(path):
    match = re.search(r"wordlist_([a-z]{2})_full\.tsv$", os.path.basename(str(path)))
    return match.group(1) if match else None


def _try_import_gloss_lookup():
    """`backend/gloss_lookup.py`'s `has_any_gloss`/`has_gloss_dictionary`,
    imported lazily and tolerantly: crossword_gen.py is also run standalone
    as a CLI script (`python3 backend/crossword_gen.py`, see the module
    docstring) — a relative import at module scope would break that (no
    package context to resolve `.gloss_lookup` against), so this is only
    ever attempted from inside a function, and a failure just means
    `require_gloss` silently has no effect rather than crashing the CLI."""
    try:
        from .gloss_lookup import has_any_gloss, has_gloss_dictionary
        return has_any_gloss, has_gloss_dictionary
    except ImportError:
        return None, None


def load_wordlist(path, max_words=None, require_gloss=False, exclude_proper_nouns=False):
    """Loads a lexicon in the `MOT<TAB>ACCENTUE<TAB>FREQUENCE<TAB>CANONIQUE`
    format (build_wordlist_freq.py), or, as a fallback, a 3- or 2-column
    format (no CANONIQUE), or plain free text (one or more words per
    line, unknown frequency -> 0, no accented/canonical form available).
    If `require_gloss` is true, a word is also excluded if it has no
    findable definition under either its inflected form or any of its
    canonical forms (see backend/gloss_lookup.py — frequency alone isn't
    enough to catch a common but undefinable word, e.g. the abbreviation
    "ABD"), falling back silently if the language can't be inferred from
    the filename or if no gloss dictionary has been built for it.

    If `exclude_proper_nouns` is true, a word that's potentially a proper
    noun is excluded outright, rather than merely demoted in the
    frequency ranking (see PROPER_NOUN_SCORE_FACTOR in
    build_wordlist_freq.py, which stays active independently of this
    parameter — the two stack) — at the user's explicit request: "In the
    grid word fill, at EASY and MEDIUM difficulty, forbid words that are
    potentially proper nouns." The reused signal is the one already
    computed when the lexicon was built (build_wordlist_freq.py's
    `likely_proper_noun`), but reconstructed here from the ACCENTUE
    column alone — every corpus word is counted fully lowercase there
    (`_count_word_frequencies`), so ACCENTUE only ever ends up capitalized
    when Hunspell only validated the word under its title-cased form,
    exactly the "likely proper noun" signal; a word correctly spelled in
    lowercase (an ordinary noun/adjective/verb) keeps a lowercase
    ACCENTUE value and is never touched by this filter. Falls back
    silently (like `require_gloss`) if the language can't be inferred
    from the filename; has no effect for German
    (`PROPER_NOUN_EXCLUDED_LANGS`), where this same signal means nothing
    (every noun is capitalized there, proper or not — see that
    module-level constant). `max_words` accepts two types, with
    different meanings: an `int` is an absolute word count to keep
    (historical behavior, used by `--max-words` on the command line); a
    `float` (0 < x <= 1, see DIFFICULTY_PRESETS) is a *fraction* of the
    lexicon actually loaded for this language — the corresponding
    absolute count is only computed here, once the lexicon's real size
    is known (i.e. after deduplication and after `require_gloss`
    filtering, if any), so the same `difficulty` value retains a
    comparable share of the vocabulary regardless of language, rather
    than a fixed word count that has a different effect depending on
    each language's own lexicon size. Returns (by_length, accents,
    canonicals, frequencies):
    - by_length = {length: [words]} — only the `max_words` most frequent
      words *globally* (across every length combined), if given, are
      kept, then grouped by length for the CSP solver;
    - accents = {MOT: accented/natural form}, for the words kept in
      by_length (used to give the LLM the real spelling — gender,
      number, conjugation — when it writes definitions; see
      backend/clues.py);
    - canonicals = {MOT: [canonical form(s)/lemma(s)]}, for the words
      kept in by_length (used to look up a dictionary definition by
      lemma rather than by inflected form; see backend/clues.py);
    - frequencies = {MOT: raw frequency (float)}, for the words kept in
      by_length — passed to `build_index` (see `NOISE_FREQUENCY_
      THRESHOLD`/`_noise_slot_cells`), to tell a statistically credible
      candidate apart from a near-zero dictionary entry (corpus noise,
      an acronym, a foreign fragment)."""
    entries = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.rstrip("\n")
            if not line or line.startswith("#"):
                continue
            parts = line.split("\t")
            if len(parts) >= 4:
                word = parts[0].upper()
                accented = parts[1]
                try:
                    freq = float(parts[2])
                except ValueError:
                    freq = 0.0
                canonical = [c for c in parts[3].split(";") if c]
                if word.isalpha():
                    entries.append((word, accented, freq, canonical or [accented]))
            elif len(parts) == 3:
                word = parts[0].upper()
                accented = parts[1]
                try:
                    freq = float(parts[2])
                except ValueError:
                    freq = 0.0
                if word.isalpha():
                    entries.append((word, accented, freq, [accented]))
            elif len(parts) == 2:
                word = parts[0].upper()
                try:
                    freq = float(parts[1])
                except ValueError:
                    freq = 0.0
                if word.isalpha():
                    entries.append((word, word, freq, [word]))
            else:
                for tok in line.upper().split():
                    if tok.isalpha():
                        entries.append((tok, tok, 0.0, [tok]))

    best = {}  # word -> (accented, best_freq, canonical)
    for word, accented, freq, canonical in entries:
        if word not in best or freq > best[word][1]:
            best[word] = (accented, freq, canonical)

    if require_gloss:
        has_any_gloss, has_gloss_dictionary = _try_import_gloss_lookup()
        lang = _lang_from_path(path)
        # Guard on the language actually *having* a gloss dictionary built,
        # not just on the import succeeding — `has_any_gloss` returning
        # False for a word with no dictionary at all is indistinguishable
        # from it returning False for a word genuinely undefinable in a
        # real dictionary; without this check, a language with no gloss
        # dictionary built (an optional, gitignored artifact a deploy can
        # easily skip — see build_gloss_dictionary.py) would have every
        # single word rejected instead of the filter no-op'ing as intended.
        if has_any_gloss and lang and has_gloss_dictionary(lang):
            best = {
                word: v for word, v in best.items()
                if has_any_gloss([v[0], *v[2]], lang)
            }

    if exclude_proper_nouns:
        lang = _lang_from_path(path)
        if lang and lang not in PROPER_NOUN_EXCLUDED_LANGS:
            best = {
                word: v for word, v in best.items()
                if not v[0][:1].isupper()
            }

    # Global frequency ranking (see DIFFICULTY_PRESETS above for why this
    # replaced a per-length cap) — then group into by_length for the CSP
    # solver. Order within a length no longer matters here: Filler already
    # shuffles its candidate list with the seeded rng before trying them.
    ranked = sorted(best.items(), key=lambda kv: -kv[1][1])
    if max_words:
        # A float is a fraction of the real lexicon (DIFFICULTY_PRESETS),
        # resolved here into an absolute count now that the lexicon's real
        # size (post dedup/require_gloss) is known; an int stays an
        # absolute word count (--max-words).
        if isinstance(max_words, float):
            max_words = round(len(ranked) * max_words)
        ranked = ranked[:max_words]

    result = defaultdict(list)
    accents = {}
    canonicals = {}
    frequencies = {}
    for word, (accented, freq, canonical) in ranked:
        result[len(word)].append(word)
        accents[word] = accented
        canonicals[word] = canonical
        frequencies[word] = freq
    return dict(result), accents, canonicals, frequencies


# ---------- Black-cell pattern generation ----------

# "Normal" minimum length (aesthetic, not absolute — the real, never-
# crossed limit, connectivity/no orphaned cell, remains the literal
# `min_interior_free=1` explicitly passed by every other caller, see
# below) of an *interior* white zone (bounded by a black cell on both
# sides), used as `is_structurally_valid`'s own default value and as the
# starting point of `_place_black_cells`'s own relaxation cascade (see
# its own docstring) — named and set to 8 (raised from 3) at the user's
# explicit request: "Give this rule **at least 3 cells** a variable name.
# Set this number to 8. If no cell can be placed while respecting this
# number to reach the black-fill target, lower the number and start
# retrying to place black cells." This last point — lowering the number
# and retrying — is exactly what `_place_black_cells` was already doing,
# until now with a fixed 3-level cascade (3, 2, 1): generalized to step
# down one level at a time from this constant down to 1
# (`range(STRUCTURAL_MIN_INTERIOR_FREE, 0, -1)`), so the progressive
# relaxation stays coherent whatever value is chosen here, rather than 3
# fixed levels independent of this number.
STRUCTURAL_MIN_INTERIOR_FREE = 8


def is_structurally_valid(grid, rows, cols, min_interior_free=STRUCTURAL_MIN_INTERIOR_FREE):
    """A grid is valid if:
    - every *interior* white zone (bounded by a black cell on both sides)
      is at least `min_interior_free` cells long (`STRUCTURAL_MIN_
      INTERIOR_FREE`, 8 by default), **except** if one of its two ends
      directly touches the grid's own border (row/column 0, or the last
      one): such a border zone is always allowed, whatever its length
      (including 1 or 2 cells) and however many of them exist on the
      whole grid — no budget or counter at all, unlike a former system
      for this (see the project-best-practices SKILL). `min_interior_
      free` exists for `_place_black_cells`, at the user's explicit
      request: if the default requirement (`STRUCTURAL_MIN_INTERIOR_
      FREE`) leaves only cells adjacent to another black cell, it's
      lowered one level at a time down to 1 for this specific placement
      attempt (see its own docstring) — every other caller
      (`minimize_black_squares` included) uses `min_interior_free=1`
      explicitly (the real absolute limit — connectivity and absence of
      an orphaned cell, never the aesthetic requirement above), never
      this function's own default value. A single-letter zone never
      becomes a real slot to fill (extract_slots always excludes it, see
      below) — it only serves as a passthrough for a longer word in the
      other direction — but a TWO-letter zone becomes a genuine slot in
      its own right (extract_slots, threshold >= 2), filled by a real
      2-letter dictionary word with its own definition ("et", "ou", "no",
      etc.);
    - no white cell ends up both in a 1-letter zone horizontally AND a
      1-letter zone vertically (a fully isolated white cell, surrounded
      by black cells on all 4 sides): such a cell would belong to no slot
      of at least 2 letters at all and would therefore never receive a
      letter — a correctness constraint (a white cell with no slot is a
      bug), never relaxed regardless of `min_interior_free`;
    - the white grid stays fully connected."""
    row_run_len = [[0] * cols for _ in range(rows)]
    col_run_len = [[0] * cols for _ in range(rows)]

    def _short_zone_ok(run, run_start, run_end, line_length):
        """A zone shorter than `min_interior_free` cells is only accepted
        if it touches the grid's own border (run_start == 0 or
        run_end == line_length) — with no other limit at all (neither on
        its exact length, nor on their total count). A zone of at least
        `min_interior_free` cells is always accepted, border or not."""
        if run >= min_interior_free:
            return True
        return run_start == 0 or run_end == line_length

    for r in range(rows):
        run = 0
        run_start = 0
        for c in range(cols):
            if grid[r][c] == WHITE:
                if run == 0:
                    run_start = c
                run += 1
            else:
                if run > 0:
                    if not _short_zone_ok(run, run_start, run_start + run, cols):
                        return False
                for cc in range(run_start, run_start + run):
                    row_run_len[r][cc] = run
                run = 0
        if run > 0:
            if not _short_zone_ok(run, run_start, run_start + run, cols):
                return False
        for cc in range(run_start, run_start + run):
            row_run_len[r][cc] = run

    for c in range(cols):
        run = 0
        run_start = 0
        for r in range(rows):
            if grid[r][c] == WHITE:
                if run == 0:
                    run_start = r
                run += 1
            else:
                if run > 0:
                    if not _short_zone_ok(run, run_start, run_start + run, rows):
                        return False
                for rr in range(run_start, run_start + run):
                    col_run_len[rr][c] = run
                run = 0
        if run > 0:
            if not _short_zone_ok(run, run_start, run_start + run, rows):
                return False
        for rr in range(run_start, run_start + run):
            col_run_len[rr][c] = run

    for r in range(rows):
        for c in range(cols):
            if grid[r][c] == WHITE and row_run_len[r][c] < 2 and col_run_len[r][c] < 2:
                return False

    white = [(r, c) for r in range(rows) for c in range(cols) if grid[r][c] == WHITE]
    if not white:
        return False
    whiteset = set(white)
    seen = {white[0]}
    stack = [white[0]]
    while stack:
        r, c = stack.pop()
        for dr, dc in ((1, 0), (-1, 0), (0, 1), (0, -1)):
            nb = (r + dr, c + dc)
            if nb in whiteset and nb not in seen:
                seen.add(nb)
                stack.append(nb)
    return len(seen) == len(white)


def _has_black_neighbor(grid, rows, cols, r, c):
    """True if at least one of (r, c)'s up-to-4 orthogonal neighbors is
    already black (diagonal contact doesn't count) — used by
    `_place_black_cells` to prefer an isolated cell over one that would
    touch another black cell, when a choice is available."""
    for dr, dc in ((1, 0), (-1, 0), (0, 1), (0, -1)):
        nr, nc = r + dr, c + dc
        if 0 <= nr < rows and 0 <= nc < cols and grid[nr][nc] == BLACK:
            return True
    return False


def _new_black_cell_breaks_locked_slot(grid, rows, cols, r, c, index, locked_letters,
                                        available_lengths=None):
    """Checks, for a candidate cell (r, c) currently white, whether turning
    it into a black cell would cut one of its 4 sides (meaning the 4
    directions from this cell, not its 4 direct neighbor cells: the
    remaining slot fragment on each side, up to the next black cell or
    the grid's own border) into a fragment of at least 2 cells ("not a
    border and not a single cell" — a single-cell fragment is never a
    real slot, see extract_slots) touching at least one already-locked
    letter, whose fixed letter combination no longer has enough real
    dictionary candidates — a preventive filter at the user's explicit
    request, applied to *every* candidate cell before it's accepted in
    `_place_black_cells`, rather than placing it unchecked and relying
    solely on the after-the-fact repair pass (see `_prefill_unfillable_
    slots`, called a second time after ratio-based placement) to catch
    the damage. The two mechanisms deliberately coexist rather than one
    replacing the other: this filter avoids creating the problem in the
    first place (so, generally, with no extra black cell needed to
    repair it afterward), the repair pass remains the safety net for the
    residual case where no candidate cell would pass this filter across
    the whole available window.

    Extended at the user's explicit request to cover a second, distinct
    case, unrelated to locking: a real bug found live as soon as the
    fixed density draw after pre-fill was reintroduced
    (`POST_PREFILL_BLACK_FRACTION`, see make_pattern) — this filter, until
    then, only ever checked fragments touching an already-locked letter,
    never the fragment's own plain *length* against `available_lengths`;
    on a palier with no locked word at all (or whose cut fragment touches
    none), nothing stopped this draw from creating a slot with a length
    far too rare in the dictionary — confirmed live: the very first
    palier still succeeded (a normal search), but every following palier
    failed almost instantly (`checks` of 5 to 7 on every parallel
    attempt, across 36 consecutive paliers) as soon as this extra 5%
    draw came into play. Every fragment is now also rejected
    (regardless of any locking) if its length isn't in `available_
    lengths` — the same criterion `_prefill_unfillable_slots` already
    uses to decide a length is "available" — before even looking at
    whether any locked letters are present there.

    `False` immediately if `index` is absent (no dictionary to check
    against — a caller that never needs this filter); with neither
    `locked_letters` nor `available_lengths`, this filter also does
    nothing at all, costing nothing for a caller that needs neither.

    A fragment **entirely** locked (every one of its cells already in
    `locked_letters` — so an already-real, confirmed word) never counts
    as "broken" here, regardless of its own candidate count — the same
    fix as `_slot_with_insufficient_candidates` above, and for the exact
    same reason: the vast majority of real words only ever match
    themselves in the dictionary (a single candidate), a count almost
    always below `PREFILL_LOCKED_MIN_WORD_COUNT` — without this fix,
    placing a black cell that would cleanly isolate an already-confirmed
    word could be wrongly rejected, as if this black cell were "breaking"
    a slot, when it only isolates an already-resolved word that needs no
    further candidates at all."""
    if index is None or (not locked_letters and available_lengths is None):
        return False

    def _run_cells(dr, dc):
        cells = []
        rr, cc = r + dr, c + dc
        while 0 <= rr < rows and 0 <= cc < cols and grid[rr][cc] == WHITE:
            cells.append((rr, cc))
            rr += dr
            cc += dc
        return cells

    for cells in (
        list(reversed(_run_cells(0, -1))),
        _run_cells(0, 1),
        list(reversed(_run_cells(-1, 0))),
        _run_cells(1, 0),
    ):
        length = len(cells)
        if length < 2:
            continue
        if available_lengths is not None and length not in available_lengths.for_cells(cells):
            return True
        if locked_letters:
            locked_count = sum(1 for cell in cells if cell in locked_letters)
            if 0 < locked_count < length:
                if _slot_candidate_count(index, length, cells, locked_letters) < PREFILL_LOCKED_MIN_WORD_COUNT:
                    return True
    return False


def _place_black_cells(grid, rows, cols, row_black, col_black, candidates, target, placed,
                        index=None, locked_letters=None, available_lengths=None,
                        forbid_adjacency=False):
    """Core of black-cell placement, shared by make_pattern and its own
    pre-fill phase (see below) — shuffled once, drawn from a small
    already-shuffled window of `candidates`. Places at most `target -
    placed` new black cells; also stops as soon as `candidates` is
    exhausted. On every draw, a window of 32 candidates is considered,
    ranked by a single criterion — the row and column with, together,
    the fewest black cells already placed.

    Within this window, at the user's explicit request, a cell that
    touches no other black cell (`_has_black_neighbor`) is always
    preferred: the best candidate (by the criterion above) that's both
    isolated and structurally valid under the normal requirement
    (`is_structurally_valid`, `min_interior_free=STRUCTURAL_MIN_INTERIOR_
    FREE`, 8 — at least 8 free cells per interior slot) is sought first.
    If this requirement leaves no candidate that's both isolated and
    valid, it's lowered one level at a time (7, then 6, ... down to 1),
    at the user's explicit request ("if no cell can be placed while
    respecting this number... lower the number and start retrying to
    place black cells"), before accepting adjacency: this relaxation
    only applies to this specific placement attempt, not to the whole
    grid nor to later attempts. Only if no isolated candidate works at
    any of these levels is adjacency accepted, retrying the same cascade
    (`STRUCTURAL_MIN_INTERIOR_FREE` down to 1, one level at a time) with
    no more isolation requirement — **unless `forbid_adjacency` is true**
    (`False` by default, every pre-existing caller of this parameter
    unchanged), in which case this very last attempt (accepting
    adjacency) is skipped entirely: at the user's explicit request, "When
    first initializing black cells, forbid any draw that would place 2
    black cells with an adjacent side" — `make_pattern` passes
    `forbid_adjacency=True` only when `seed_grid` is `None` (the very
    first grid of a `generate_grid()` call, entirely white), never for a
    palier resuming an already partially-blackened pattern from a
    previous palier, where adjacency is still accepted as a last resort
    exactly as before. No isolated candidate found across the whole
    window at this point then behaves exactly like the residual case
    below — the best candidate is refused and removed from the pool, the
    loop continues with the rest of the pool, never a crash or a
    deadlock. In the residual case where even this finds nothing across
    the whole window (all 32 candidates break connectivity or create an
    orphaned cell, or — with `forbid_adjacency` — are all adjacent to an
    already-black cell), the best candidate by the main criterion is
    simply refused and removed from the pool, to guarantee the loop
    always makes progress.

    Returns (placed, rejected) — `rejected` covers EVERY cell not placed,
    whether the loop stops for lack of candidates or because `target` is
    reached: the refused cells, followed by whichever were still in
    `candidates` without even having been tried (only possible when
    `target` is reached before `candidates` is exhausted) — a caller that
    needs to continue (like `_prefill_unfillable_slots`, which calls this
    function with a deliberately small `target`, one cell at a time)
    starts from this complete list rather than silently losing never-
    tried candidates (a real bug found by direct testing before
    `_prefill_unfillable_slots` was considered done: without this fix, a
    success on the very first try returned an empty `rejected`, even
    though almost the entire original candidate pool remained perfectly
    usable for the next step).

    `index`/`locked_letters` (both `None` by default — every caller
    pre-dating this feature, as well as any call with no locked letters,
    is unchanged), at the user's explicit request, on top of
    `is_structurally_valid`: a candidate cell that would break, on one of
    its 4 sides, a slot of at least 2 cells touching an already-locked
    letter with not enough real dictionary candidates
    (`_new_black_cell_breaks_locked_slot`, see its own docstring) is
    refused just like a structurally invalid cell — a preventive filter,
    not merely an after-the-fact repair. If no cell in the whole window
    passes this filter, the existing behavior takes over unchanged: the
    best candidate is refused and removed from the pool like any other
    residual case, letting the loop progress normally with, at worst,
    fewer cells placed than `target` — at the user's explicit request,
    this is deliberately not treated as a deadlock to work around here
    but left to surface as-is: the CSP fill will simply be attempted on
    the resulting pattern, and if it fails, the already-in-place
    cross-palier cleanup mechanism (`_build_retry_seed`) gives it another
    chance at the next palier by freeing up room again, exactly as it
    already does for any other failure cause."""
    window = 32
    rejected = []
    remaining = candidates

    def _first_valid(indices, min_free):
        for idx in indices:
            r, c = remaining[idx]
            if grid[r][c] == BLACK:
                continue
            if _new_black_cell_breaks_locked_slot(grid, rows, cols, r, c, index, locked_letters,
                                                   available_lengths):
                continue
            grid[r][c] = BLACK
            ok = is_structurally_valid(grid, rows, cols, min_interior_free=min_free)
            grid[r][c] = WHITE
            if ok:
                return idx
        return None

    while remaining and placed < target:
        sample_size = min(window, len(remaining))
        order = sorted(
            range(sample_size),
            key=lambda i: row_black[remaining[i][0]] + col_black[remaining[i][1]],
        )
        non_adjacent = [i for i in order if not _has_black_neighbor(grid, rows, cols, *remaining[i])]

        chosen = None
        for min_free in range(STRUCTURAL_MIN_INTERIOR_FREE, 0, -1):
            chosen = _first_valid(non_adjacent, min_free)
            if chosen is not None:
                break
        if chosen is None and not forbid_adjacency:
            for min_free in range(STRUCTURAL_MIN_INTERIOR_FREE, 0, -1):
                chosen = _first_valid(order, min_free)
                if chosen is not None:
                    break

        if chosen is None:
            r, c = remaining.pop(order[0])
            rejected.append((r, c))
            continue

        r, c = remaining.pop(chosen)
        grid[r][c] = BLACK
        row_black[r] += 1
        col_black[c] += 1
        placed += 1
    return placed, rejected + remaining


# Minimum number of words of a given length in the dictionary for that
# length to be considered "available" by the pre-fill phase below.
# Historically set to 10 (against an initial threshold of a single word,
# raised after a real regression on the 15×10 benchmark — see PREFILL_
# LOCKED_MIN_WORD_COUNT's own history right below for the same kind of
# measurement), then aligned to 3 at the user's explicit request, to stay
# consistent with the fill-impossibility criterion used by
# `_slot_with_insufficient_candidates`/`_new_black_cell_breaks_locked_slot`
# (see PREFILL_LOCKED_MIN_WORD_COUNT below) — both constants now equal 3,
# even though they remain two separate constants (they apply to two
# different checks: a bare length here, an exact combination of locked
# letters at specific positions for the other one). Verified live after
# the alignment: the standard 15×10 benchmark (seeds 2 and 7) still
# succeeds with no regression at this new value.
PREFILL_MIN_WORD_COUNT = 3

# Same idea as PREFILL_MIN_WORD_COUNT above, but for the position-aware
# re-check the pre-fill phase also runs against a slot touching at least
# one already-locked letter (see _slot_with_insufficient_candidates/
# _new_black_cell_breaks_locked_slot) — a deliberately separate constant
# from PREFILL_MIN_WORD_COUNT above, even though both now share the same
# value (3): this one guarantees genuine *existence* of at least one real
# word matching a slot's *exact* locked letters at their *exact*
# positions (a far stronger, much rarer condition than merely having
# enough words of the right length), while the other only ever checks
# length alone.
#
# First set to the user's own literal value (1), then verified live —
# not just assumed safe: two real `generate_grid()` runs on the standard
# 15×10 benchmark (seeds 2 and 7, previously reliable throughout this
# project's entire history) both failed outright at threshold 1 (73.3s
# and 88.5s respectively, exhausting all 200 paliers) — a real
# regression, confirmed reproducible, not a fluke: a slot locked down to
# exactly one real candidate word is extremely fragile, since that one
# word conflicting with even a single crossing letter anywhere makes the
# slot permanently impossible, with pre-fill no longer stepping in to
# shorten/avoid it. Reported to the user with this measurement; the user
# chose an intermediate threshold (3) over keeping 1 (accepting the
# regression) or reverting to 10 outright.
PREFILL_LOCKED_MIN_WORD_COUNT = 3

# Raw frequency (the FREQUENCE column of data/wordlist_<lang>_full.tsv,
# see load_wordlist) below which a candidate no longer counts as
# "playable" for `_noise_slot_cells` (see its own docstring) — at the
# user's explicit request, to tell a genuinely unfillable cell (no real
# candidate at all, already covered by the .impossible red highlight)
# apart from a cell that's technically non-empty but whose only
# remaining candidates are either already used elsewhere in the grid, or
# corpus noise (an acronym, a foreign fragment, an OCR artifact) rather
# than genuinely credible real words.
#
# Calibrated live against the real French lexicon: the 10 lowest
# frequencies of length 2 and 3 are, without exception, recognizable
# noise ("ΔT", "Nʼ", "ZL", "ΜG"...; "GLX", "ITO", "TEO", "ZIO"...), while
# the concrete case that motivated this feature (a grid stuck on 3 cells
# for 11 consecutive paliers, see CLAUDE.md) showed `ESR`=3.0, `GSR`=1.0,
# `KSS`=1.0, `TSS`=4.0 — none of them a real French word — against
# `VOS`=27330.0 and `SE`, both common. A threshold of 5 rules out these
# four noise entries without touching real words, but stays deliberately
# conservative: at this threshold, only 4.5% of length-2 words and 10.3%
# of length-3 words in the real French lexicon are excluded (measured
# live, `data/wordlist_fr_full.tsv`).
NOISE_FREQUENCY_THRESHOLD = 5

# Minimum number of new black cells always guaranteed to a single zone
# during the "nettoyage curatif" budget check (see
# `_prefill_unfillable_slots`) before that zone's own percentage-scaled
# budget (`fill_objective_fraction * zone_white_count`) is allowed to
# restrict it further — at the user's explicit request, after a real
# regression measured live: the grid's own overall black-cell fill
# objective (10-14% by default) applied *directly* to a single zone's own
# size (typically 8-15 cells) left a budget of 0 or 1 cell for almost
# every zone (e.g. exactly 0 for any zone of 9 cells or fewer at 10%) —
# confirmed by re-running the standard 15×10 benchmark (seeds 2 and 7,
# previously reliable throughout this project's entire history), which
# both failed outright (148.2s/180.4s, exhausting all 200 paliers) once
# this was wired in without a floor. Reported to the user with this
# measurement; the user chose a guaranteed per-zone floor over either a
# single grid-wide cumulative budget or reverting the whole mechanism.
#
# Tried as a *shared* budget instead (one pool of 2 across every zone
# `_prefill_unfillable_slots` is tracking at once, not 2 per zone) later
# in this project's history, at the user's own explicit request, after
# they pointed out the arithmetic the per-zone scoping implied ("si 3
# emplacements à problème, on monterait à 6 cases en plus autorisées") —
# then reverted again immediately, by the user's own explicit follow-up
# instruction, once a real regression was measured live on the exact
# same standard benchmark this floor was originally introduced to fix:
# seed 2 failed outright, seed 7 succeeded but 3.5× slower (355.4s vs.
# ~102s). Back to the original per-zone scoping — see the
# `project_nettoyage_curatif_paused` memory note for the full trail of
# both attempts, kept for any future session that revisits this area.
#
# Lowered from 2 to 1, at the user's own later explicit request, quoting
# this exact floor's own docstring back: "2 cases minimum crée trop de
# cases noires" — a real, opposite-direction complaint from the one that
# originally raised this floor from 0/1 to 2 (that earlier regression was
# about too FEW guaranteed cells causing outright failures; this one is
# about too MANY black cells being added in practice once every zone gets
# at least 2). Verified live rather than assumed safe, given this exact
# constant's own fraught history: two real `generate_grid()` runs on the
# standard 15×10 benchmark (seeds 2 and 7, Flash mode) both succeeded — 0
# mismatches, 0 empty white cells each — confirming this specific value
# (unlike the *shared*-budget alternative tried and reverted above, and
# unlike the unrelated `PREFILL_LOCKED_MIN_WORD_COUNT=1` regression noted
# further above) doesn't reproduce either of those earlier failures.
PREFILL_ZONE_BLACK_BUDGET_FLOOR = 1

# Fraction of extra black cells drawn after the pre-fill phase (see
# make_pattern), at the user's explicit request: "rétablir un tirage de 5%
# de nouvelles cases ajoutées (5% par rapport au nombre de cases blanches
# restantes)", raised to 10% at the user's own explicit (and immediate)
# follow-up request right after. Reinstated after the old cross-palier
# ratio-escalation mechanism was removed entirely (see generate_grid) — but
# deliberately different from that old mechanism: this is a fixed draw
# (never escalated palier to palier) expressed as a percentage of the cells
# *still white after pre-fill*, not of the grid's total cell count.
# Pre-fill already places whatever is structurally necessary (at least
# PREFILL_MIN_WORD_COUNT candidates per slot); this further draw, purely
# aesthetic/density-driven, only ever applies on top and never removes
# anything pre-fill has already placed.
# Became a *default* rather than a fixed constant, at the user's explicit
# request: `generate_grid`/`make_pattern` now accept `black_enrichment_
# fraction` as a parameter — tunable from the web UI (a "Taux noir"
# selector, a free-text 0-100 integer field, 14% by default — see
# GenerateRequest.black_enrichment_percent in backend/app.py) rather than
# fixed at 10% for everyone. This constant remains the default value for
# any caller that doesn't specify one (the CLI, notably).
#
# Applies only at a fresh-pattern palier — the very first one, or any
# palier immediately following a full cleanup (`_build_retry_seed`) —
# never to a "reprise telle-quelle" palier (`_pattern_continue`), which
# never calls `make_pattern` at all. A SEPARATE mechanism that used to add
# one extra black cell on top of this, on every single palier including
# "reprise telle-quelle" ones (`_impossible_cell_groups`/`_lock_one_
# impossible_cell`, a single-cell lock targeting whichever cells belonged
# to an impossible/blockage slot), was removed entirely, at the user's
# explicit request — this density-percentage mechanism itself was never
# meant to be removed, only that separate per-cycle single-cell lock. See
# CLAUDE.md for the full history of both mechanisms, including the
# removal of the single-cell lock and this mechanism's own brief,
# mistaken removal and restoration in the same session.
POST_PREFILL_BLACK_FRACTION = 0.10


def _slot_candidates(index, length, cells, known_letters):
    """Real candidate words for a slot of length `length` covering
    `cells`, taking into account letters already known at some of its
    cells (`known_letters`, a cell->letter dict) — not just its length.
    Same per-position intersection logic as `Filler._domain`
    (`idx["pos"][pos][letter]`, filtered/intersected position by
    position), but usable here outside of any ongoing CSP search (before
    it even starts, during pattern generation, or for the seeds'
    statistical sampling) — see `_slot_candidate_count` (count only) and
    `_force_single_candidate_slots`/`sample_letter_biases` (real words,
    not just their count), all three of which use this instead of
    duplicating the same intersection. Returns `idx["words"]` (the entire
    lexicon of that length, a list) if no cell of this slot is known yet;
    an empty set if the index has no word of this length, or if the
    known letters match no real word. `index` is a DualIndex (see its
    docstring) — resolved right here, by `cells`'s own direction, for a
    bilingual grid (across/down words in two distinct dictionaries);
    always the same dictionary on both sides for a monolingual grid, so
    no effect in that case."""
    idx = index.for_cells(cells).get(length)
    if idx is None:
        return ()
    constraints = {}
    for pos, cell in enumerate(cells):
        letter = known_letters.get(cell)
        if letter is not None:
            constraints[pos] = letter
    if not constraints:
        return idx["words"]
    sets = [idx["pos"][pos].get(ch) for pos, ch in constraints.items()]
    if any(not s for s in sets):
        return ()
    sets = sorted(sets, key=len)
    result = sets[0]
    for s in sets[1:]:
        result = result & s
        if not result:
            return ()
    return result


def _slot_candidate_count(index, length, cells, locked_letters):
    """Number of candidate words for a slot — see `_slot_candidates` for
    the actual logic; only computes what's needed to know whether the
    count reaches `PREFILL_MIN_WORD_COUNT` or not (see its own caller),
    not a need to know the words themselves."""
    return len(_slot_candidates(index, length, cells, locked_letters))


def _has_slot_without_candidate(grid, rows, cols, available_lengths, index=None, locked_letters=None):
    """True if the grid, as it currently stands, has at least one slot
    (`extract_slots`) whose length has fewer than `PREFILL_MIN_WORD_COUNT`
    candidate words in `available_lengths` (the set of slot lengths the
    word list has *enough* words for — see `PREFILL_MIN_WORD_COUNT`) — a
    slot that would be either impossible or merely very hard to fill,
    regardless of which letters end up assigned to its crossings.

    `index`/`locked_letters` (both `None` by default — every caller before
    the cross-palier retry mechanism unaffected), at the user's explicit
    request, after a real bug found and confirmed live with the user's own
    reported data: `available_lengths` alone only checks that a length is
    *generally* well-covered by the dictionary — it says nothing about
    whether a *specific* slot of that length, once some of its cells are
    already pinned to specific letters by `locked_letters` (carried forward
    from a previous palier by `_build_retry_seed`), still has *any* matching
    candidate at all. Reproduced live: a palier that reopened almost every
    black cell around just two surviving locked down-words left several
    18-25-cell-long across slots each needing two fixed letters at specific
    positions — a combination no French word of that length actually has —
    and `try_fill` failed at `checks=1`, before assigning a single new
    letter, confirming the pre-fill phase's own "this length is fine" check
    was silently wrong for slots touching a locked cell. When both are
    given, a slot whose cells include at least one locked cell is checked
    with the more precise `_slot_candidate_count` (a real per-position
    intersection against the word index) instead of the cheap
    length-only lookup; a slot with no locked cell at all still uses the
    fast path unchanged, since nothing there needs the more expensive check."""
    for slot in extract_slots(grid, rows, cols):
        length = len(slot)
        if length not in available_lengths:
            return True
        if locked_letters and any(cell in locked_letters for cell in slot):
            if _slot_candidate_count(index, length, slot, locked_letters) < PREFILL_LOCKED_MIN_WORD_COUNT:
                return True
    return False


def _slot_with_insufficient_candidates(grid, rows, cols, available_lengths, index=None,
                                        locked_letters=None, skip=None):
    """Like `_has_slot_without_candidate` (True/False), but returns the
    slot itself (its own list of cells) as soon as it finds one whose
    length is not in `available_lengths` (fewer than `PREFILL_MIN_WORD_
    COUNT` dictionary words for that length in general) — or, when
    `locked_letters` covers at least one of its cells, whose intersection
    with those exact letters (see `_slot_candidate_count`) leaves fewer
    than `PREFILL_LOCKED_MIN_WORD_COUNT` candidates — a threshold much
    lower than the length-only one, at the user's explicit request (a
    threshold of a single word was tried and then dropped after live
    verification: the standard 15×10 benchmark then failed on seeds that
    had previously succeeded reliably, a slot reduced to a single
    candidate being too fragile against even a single crossing conflict —
    see `PREFILL_LOCKED_MIN_WORD_COUNT`'s own definition for the full
    measurement); `None` if every slot is fine (or already in `skip`, see
    below). At the user's explicit request: `_prefill_unfillable_slots`
    needs this to directly target the black cell to place *inside that
    slot* rather than anywhere in the grid (see its own docstring for the
    bug this targeting fixes).

    `skip` (a set of cell tuples, `None` by default), at the user's
    explicit request: a slot whose every cell is already locked
    (typically two adjacent locked words, see `_prefill_unfillable_
    slots`) can, by construction, never be fixed by placing a black cell
    in it — without `skip`, this function would keep returning that same
    unfixable slot on every new call, preventing `_prefill_unfillable_
    slots` from ever making progress on the *other*, genuinely fixable
    slots.

    A slot **entirely** covered by `locked_letters` (every one of its
    cells already locked — so already a real, confirmed word, not a slot
    still to be solved) is **never** considered insufficient, regardless
    of its own candidate count — a real bug found and fixed after a
    direct report from the user, backed by two screenshots: an
    already-locked and confirmed word (e.g. "AVALAS") was disappearing
    between the "before" and "after" preview of the same attempt, even
    though the black cells stayed rigorously identical (so not an
    independent new grid — see above for that case, already fixed
    separately). Root-caused live rather than assumed: `_slot_candidate_
    count` counts, for the vast majority of real words of a given length,
    **exactly 1** result (the word itself — most words of 5 letters or
    more are the only dictionary entry matching their own exact spelling
    precisely), a count almost always strictly below `PREFILL_LOCKED_
    MIN_WORD_COUNT` (3) — before this fix, this check therefore wrongly
    considered almost every already-confirmed word an "insufficient" slot
    to fix, triggering `_remove_a_crossing_word` (see below) for a word
    that was actually already resolved and needed no fixing at all —
    reproduced and confirmed with a tiny, fully controlled dictionary: a
    fictional, entirely locked word ("AVOIR", the dictionary's only
    5-letter word matching that exact spelling, so 1 candidate) really
    was the very first slot returned by this function, ahead of the
    genuinely problematic slot it crosses. A slot entirely locked but
    whose combination matches *no* real word at all (genuinely
    impossible, not merely rare) is also no longer returned here — this
    case is still correctly detected later, once `make_pattern` has
    returned, by `_pattern_attempt`/`_pattern_continue`'s own dedicated
    mechanism (`preseed_assignment`/`locked_impossible_slots`, which
    validates every entirely locked slot against the dictionary and
    leaves it `None` if it matches no real word — see their own
    docstrings): pre-fill has no useful way to act on such a slot anyway
    (no cell is available there for a black cell — all already locked —
    and removing a word that *crosses* it changes nothing about its own
    letters, already fixed by construction)."""
    for slot in extract_slots(grid, rows, cols):
        if skip and tuple(slot) in skip:
            continue
        length = len(slot)
        if length not in available_lengths.for_cells(slot):
            return slot
        if locked_letters:
            locked_count = sum(1 for cell in slot if cell in locked_letters)
            if 0 < locked_count < length:
                if _slot_candidate_count(index, length, slot, locked_letters) < PREFILL_LOCKED_MIN_WORD_COUNT:
                    return slot
    return None


def _has_slot_without_candidate(grid, rows, cols, available_lengths, index=None, locked_letters=None):
    """True if the grid, as it currently stands, has at least one slot
    (`extract_slots`) whose length has fewer than `PREFILL_MIN_WORD_COUNT`
    candidate words in `available_lengths` — see `_slot_with_insufficient_
    candidates`, which this is now a thin wrapper around (kept as a
    separate, plain boolean helper for the few callers — e.g. unit tests —
    that only need the yes/no answer, not the offending slot itself)."""
    return _slot_with_insufficient_candidates(grid, rows, cols, available_lengths, index, locked_letters) is not None


def _remove_a_crossing_word(slot, grid, rows, cols, locked_letters, rng=None):
    """"Curative cleanup", at the user's explicit request: instead of
    continuing to blacken `slot` (the least fillable slot — whose
    intersection with already-locked letters no longer leaves enough
    candidates, see `_slot_with_insufficient_candidates` — which already has
    letters positioned on some of its cells), remove an already-confirmed
    word that **contributes** to those already-positioned letters: a slot
    crossing `slot` — necessarily in the other direction, since it shares at
    least one cell with it — every one of whose cells is in `locked_letters`
    (so a genuine, already-locked word, not just an isolated cell), itself
    responsible for at least one of the letters that make `slot` hard to
    fill. Rather than choosing, among all these crossing words, the one with
    the fewest fill possibilities of its own — a criterion tried and then
    explicitly dropped by the user after a regression measured live (see
    below) — a choice is drawn at random (shuffled with `rng`, this
    attempt's own already-seeded RNG, to stay reproducible and avoid any
    positional bias, the same principle used everywhere else in this file)
    among every crossing word found, with no fragility criterion at all.
    Mutates `locked_letters` in place (removes every cell of the chosen
    word) and returns `True` if a word was indeed removed; `False` if `slot`
    touches no locked word at all (nothing to remove — the only remaining
    recourse is then a black cell, or declaring the slot unfixable).

    Formerly `_remove_least_fillable_crossing_word`: a first version chose
    the crossing word with the fewest candidates of its own — the idea being
    to sacrifice the already-most-fragile word, already the closest to
    becoming impossible itself at the next conflict anyway. This idea
    proved harmful in practice, confirmed live on the standard benchmark:
    even with the black-cell budget entirely disabled (see `PREFILL_ZONE_
    BLACK_BUDGET_FLOOR`), the mere act of removing a word in this edge case
    (no black cell available in `slot`) was enough to make a previously
    reliable seed fail — removing *specifically* the most fragile word
    turned out to be more harmful than beneficial, likely because an
    already-fragile word isn't necessarily redundant: its disappearance can
    deprive the rest of the search of a useful confirmation elsewhere in the
    grid. At the user's explicit request, this selection criterion is
    dropped: any crossing word contributing to the problem can be removed,
    with no attempt to guess which one would be the "cheapest" to lose."""
    if not locked_letters:
        return False
    slot_tuple = tuple(slot)
    slot_cells = set(slot)
    candidates = []
    for other in extract_slots(grid, rows, cols):
        if tuple(other) == slot_tuple:
            continue
        if not (slot_cells & set(other)):
            continue
        if not all(cell in locked_letters for cell in other):
            continue
        candidates.append(other)
    if not candidates:
        return False
    if rng is not None:
        rng.shuffle(candidates)
    chosen = candidates[0]
    for cell in chosen:
        locked_letters.pop(cell, None)
    return True


def _prefill_unfillable_slots(grid, rows, cols, row_black, col_black, candidates,
                               available_lengths, index=None, locked_letters=None, rng=None,
                               fill_objective_fraction=1.0, forbid_adjacency=False):
    """Pre-fill phase, at the user's explicit request: as long as the grid
    has a slot (`extract_slots`) whose length has fewer than `PREFILL_MIN_
    WORD_COUNT` candidate words in the dictionary (`available_lengths` —
    typically a slot too long for the dictionary, or of a length so rare
    that almost no word remains to fill it, raised from a threshold of a
    single word at the user's explicit request) — or, when `index`/
    `locked_letters` are given (see `_has_slot_without_candidate`), a slot
    whose length is generally fine but whose intersection with already-
    locked letters no longer leaves enough candidates — black cells keep
    being placed, one at a time so a recheck can happen after every cell to
    see whether a slot still lacks enough candidates. Stops as soon as
    that's no longer the case, or if no more cell can be added (`candidates`
    exhausted) while such a slot still remains — an accepted edge case, not
    an error: the grid is left as-is, and a pattern that can't be fixed this
    way will simply fail at the CSP fill stage afterward, the normal way.

    The cell placed at each iteration is chosen **directly within the
    problematic slot itself** (`_slot_with_insufficient_candidates`, among
    its own cells still available in `candidates`) rather than elsewhere in
    the grid via `_place_black_cells`'s generic row/column criterion — fixed
    at the user's explicit request, after a real bug confirmed live: the
    generic criterion has no reason to ever land precisely on the slot in
    question, so pre-fill could blacken many cells elsewhere in the grid,
    with no relation whatsoever to the problem being fixed, before a cell
    finally landed there by pure chance — observed live on a real grid: a
    "successful" attempt that blackened almost the entire grid (barely two
    white columns left out of 25), an unusable crossword grid, when a
    handful of well-targeted cells would have sufficed. The targeting
    chooses, among the cells of the slot in question still in `candidates`,
    the one with the fewest black cells already placed on its own
    row+column (`row_black[r] + col_black[c]`, the same "most available
    zone" criterion `_place_black_cells` already uses elsewhere in this
    file) — proximity to a balanced split of the slot (`abs(2*position -
    (length-1))`) now only breaks ties between cells equal on this first
    criterion. Fixed at the user's explicit request after a second real bug
    confirmed live: an earlier version sorted only by this balanced split,
    never looking at `row_black`/`col_black` at all — on a wide grid where
    many slots of the same length need cutting (for instance, right at the
    very start of generation, a whole 25-cell row with no black cell yet at
    all), the "most balanced" cut is systematically the same geometric
    position (the exact middle) for every one of them, making every row
    land on exactly the same column — a column entirely black from top to
    bottom, the exact opposite of "seek the most available zones". The new
    criterion means that once a cell is placed in a given column, that
    column (and its row) becomes less attractive for the next slot to fix,
    which then prefers a still-untouched column — the spread emerges
    naturally, with no dedicated "never twice the same column" rule. If no
    cell of this slot is available in `candidates` (already all excluded,
    for instance because they're all locked — two adjacent locked words,
    typically) or none preserves connectivity, this specific slot is marked
    unfixable (`unfixable`, see `_slot_with_insufficient_candidates`) and
    ignored for the rest of this call — at the user's explicit request,
    this is not a reason to abandon pre-fill for the whole grid, only for
    this specific slot; every other problematic slot is still fixed
    normally. Every candidate cell is also checked with `is_structurally_
    valid(min_interior_free=1)` before being accepted — the absolute
    invariant (connectivity, no orphaned cell) that any black cell placed
    anywhere in this file must respect; the slot's candidates are tried in
    this preference order until one satisfies it, or until exhaustion (same
    accepted edge case as above).

    **"Curative cleanup"**, at the user's explicit request, added on top of
    this same mechanism for the case of a slot made insufficient by
    already-locked letters (never for the case of a simply too-rare length —
    see below): instead of indefinitely continuing to blacken this slot, a
    budget is now respected. For every problematic slot encountered, its
    original size (the number of white cells it covered the very first time
    it was detected, before any black cell was added to fix it — tracked by
    `zone_footprints`, a list of `[original_cells, black_cells_already_
    added]`, since the same zone can be rediscovered several times in a
    row, cut into shorter and shorter pieces as cells are added; a fragment
    is attached to the original zone whose cells it's a subset of, never
    recreated as an independent zone) serves as the reference: as long as
    the number of new black cells already added to this zone stays under
    its own budget (`zone_budget` — `fill_objective_fraction`, the same
    black-fill objective already applied to the whole grid, see
    `make_pattern`, applied to this zone's own original size, but **never
    fewer than `PREFILL_ZONE_BLACK_BUDGET_FLOOR` (1) guaranteed cell** — see
    its own definition for the real regression measured live, without this
    floor, that motivated adding it: this percentage, 10-14% by default,
    once brought down directly to a single zone's typical size (often 8-15
    cells), left a budget of 0 or 1 cell for the vast majority of zones,
    instead of a genuinely proportional budget), a black cell keeps being
    tried normally. Once this budget is exceeded (or if no available black
    cell fits), rather than immediately declaring the slot unfixable,
    `_remove_a_crossing_word` is tried: removing an already-locked word
    that crosses this slot (drawn at random among those contributing to it,
    with no attempt to guess which one would be the cheapest to lose — see
    its own docstring for the regression this choice fixes) relaxes a
    letter constraint without adding any further black cell — a way to fix
    the slot that avoids over-blackening a single zone well beyond what the
    grid's own overall black-fill objective calls for. Only marked
    unfixable if neither a black cell nor a word removal unblocks the
    situation — the one edge case kept from the previous version. For a
    slot made insufficient purely by its own length (`length not in
    available_lengths`, never caused by locked letters), removing a word
    wouldn't change its length at all — the budget/removal is therefore
    ignored in this case, which keeps exactly the original behavior (black
    cell, or unfixable)."""
    count = 0
    unfixable = set()
    zone_footprints = []  # [cases_d_origine (set), cases_noires_ajoutées (int)]
    while candidates:
        slot = _slot_with_insufficient_candidates(
            grid, rows, cols, available_lengths, index, locked_letters, skip=unfixable
        )
        if slot is None:
            break
        length = len(slot)
        is_length_problem = length not in available_lengths.for_cells(slot)

        slot_set = set(slot)
        footprint = None
        for fp in zone_footprints:
            if slot_set <= fp[0]:
                footprint = fp
                break
        if footprint is None:
            footprint = [slot_set, 0]
            zone_footprints.append(footprint)
        zone_white_count = len(footprint[0])

        candidate_set = set(candidates)
        cells_in_slot = [cell for cell in slot if cell in candidate_set]
        if rng is not None:
            rng.shuffle(cells_in_slot)
        options = sorted(
            cells_in_slot,
            key=lambda cell: row_black[cell[0]] + col_black[cell[1]],
        )

        # The per-zone budget is the overall black-fill objective's
        # percentage applied to *this* zone's own size, but never fewer
        # than `PREFILL_ZONE_BLACK_BUDGET_FLOOR` (1) guaranteed black cell —
        # at the user's explicit request, after a real regression confirmed
        # live: this percentage (10-14% by default), once brought down to a
        # single zone's typical size (often 8-15 cells), left a budget of
        # 0 or 1 cell for the vast majority of zones — see the docstring
        # further down for the full measurement.
        #
        # A budget SHARED across every zone (rather than per zone) was
        # tried for a while, at the user's explicit request — then
        # explicitly cancelled by the user themselves once the real
        # regression it caused was measured live on the standard benchmark
        # (seed 2 total failure, seed 7 3.5x slower): "The budget must not
        # be changed..." Reverted back to the original per-zone budget —
        # see `PREFILL_ZONE_BLACK_BUDGET_FLOOR`'s own definition for the
        # full history of both attempts.
        zone_budget = max(PREFILL_ZONE_BLACK_BUDGET_FLOOR,
                           int(fill_objective_fraction * zone_white_count))
        within_budget = (
            is_length_problem
            or zone_white_count == 0
            or (footprint[1] + 1) <= zone_budget
        )
        placed_one = False
        if within_budget:
            non_adjacent = [
                cell for cell in options if not _has_black_neighbor(grid, rows, cols, *cell)
            ]
            ordered_options = non_adjacent if forbid_adjacency else (
                non_adjacent + [cell for cell in options if cell not in set(non_adjacent)]
            )
            for (r, c) in ordered_options:
                grid[r][c] = BLACK
                if is_structurally_valid(grid, rows, cols, min_interior_free=1):
                    row_black[r] += 1
                    col_black[c] += 1
                    candidates.remove((r, c))
                    count += 1
                    footprint[1] += 1
                    placed_one = True
                    break
                grid[r][c] = WHITE
        if placed_one:
            continue

        if not is_length_problem and _remove_a_crossing_word(
            slot, grid, rows, cols, locked_letters, rng
        ):
            continue

        # Cet emplacement précis ne peut être corrigé ni par une case noire
        # disponible, ni par le retrait d'un mot verrouillé qui le croise
        # (typiquement : toutes ses cases sont déjà verrouillées par des
        # lettres d'un palier précédent sans qu'aucun mot croisant ne soit
        # lui-même verrouillé, ou aucune case ne préserve la connexité) — à
        # la demande explicite de l'utilisateur, ce n'est pas une raison
        # d'abandonner tout le pré-remplissage : on le marque pour ne plus
        # jamais le reproposer (`unfixable`) et on continue sur les autres
        # emplacements, qui restent corrigibles indépendamment. Ce résidu,
        # s'il subsiste jusqu'au remplissage CSP, échouera simplement là
        # normalement — et, dans le cas d'un mot verrouillé, sera retiré au
        # palier suivant par le même mécanisme de nettoyage qui retire déjà
        # tout mot croisant un emplacement impossible (voir _build_retry_seed).
        unfixable.add(tuple(slot))
    return candidates


def make_pattern(rows, cols, black_ratio, rng, available_lengths=None,
                  seed_grid=None, locked_letters=None, index=None,
                  black_enrichment_fraction=POST_PREFILL_BLACK_FRACTION):
    """Places black cells one at a time, independently (no symmetry
    constraint — dropped at the user's explicit request, since the CSP
    fill is fast enough that trying more patterns is cheap, and a
    non-symmetric search can reach a much lower black-cell ratio while
    staying structurally valid, in a way pairing every cell with its
    180° mirror could not always do), biased to keep black cells apart
    from each other.

    A purely random placement order (just shuffling every cell) tends to
    let black cells end up touching each other by chance, forming small
    clumps/"walls" — which both look worse and force many neighboring
    words to share the same length (the length is just the gap between
    black cells in that row/column). Keeping black cells apart avoids
    that directly.

    Implemented as a small look-ahead (`_place_black_cells`): at each step,
    sample a window of 32 still-untried cells and prefer the one whose
    row+column currently have, together, the fewest black cells already
    placed — a single main criterion, at the user's explicit request,
    reverting a much more elaborate design this area had grown into (a
    cascade of strict-then-tolerant phases with per-length zone budgets, a
    row/column discount that varied by phase, and an adjacency secondary
    tie-break) — see the project-best-practices SKILL for that whole
    history. Falls back to shuffle order once the window is exhausted, so
    even this one criterion is a soft preference, not a hard constraint —
    it never makes a fillable ratio/size combination infeasible.

    Structural validity itself (`is_structurally_valid`) is equally simple
    now: an *interior* white zone (bounded by a black cell on both sides)
    must be at least `min_interior_free` cells long
    (`STRUCTURAL_MIN_INTERIOR_FREE`, 8 by default — named and raised from
    an original 3 at the user's own later explicit request, see that
    constant's own docstring for the full reasoning); a zone
    touching the grid's own border on at least one side is always allowed,
    whatever its length and however many of them the grid ends up with.
    `_place_black_cells` reintroduces a preference for keeping black cells
    apart, at the user's explicit request, but expressed by relaxing this
    structural minimum rather than by a secondary tie-break criterion: among
    the window, it first looks for the best candidate (by the row/column
    criterion) that is *not* adjacent to any existing black cell and valid
    at `min_interior_free=STRUCTURAL_MIN_INTERIOR_FREE`; if that requirement
    leaves no such isolated candidate, it's relaxed one step at a time (7,
    then 6, ... down to 1), still only considering isolated candidates —
    only once even the most relaxed level finds none is adjacency accepted
    at all, again cascading `STRUCTURAL_MIN_INTERIOR_FREE` down to 1 before
    giving up on that specific placement attempt.

    `available_lengths` (`None` by default — every existing caller
    unaffected), at the user's explicit request: the set of slot lengths
    the word list has *at least* `PREFILL_MIN_WORD_COUNT` candidate words
    for (10, not just 1 — raised at the user's own explicit follow-up
    request, since a length with only a handful of words in the entire
    list stays very hard to fill even when it's not literally impossible,
    especially once more than one slot of that length competes for the
    same tiny pool). When given, a
    **pre-fill phase** (`_prefill_unfillable_slots`, see its own docstring)
    runs first, before the ratio-based placement below even starts: as long
    as the grid has a slot whose length isn't in `available_lengths` (too
    long for any word the list has, or simply too poorly covered), it keeps
    placing black cells with this exact same look-ahead algorithm until
    that's no longer the case. Cells placed this way are never counted
    against `black_ratio`'s own target — placement below only starts
    counting *after* the pre-fill phase returns, so a slot that would
    otherwise have too few candidate words (and make the whole pattern hard
    or impossible to fill at the CSP-fill stage no matter how the rest of
    it turns out) gets fixed for free, without eating into the ratio the
    rest of the grid still needs.

    `seed_grid` (`None` by default — every existing caller unaffected), at
    the user's explicit request: instead of starting from an all-white
    grid, continue placing black cells on top of an already-partially-black
    grid (see `generate_grid`'s cross-palier retry-seed mechanism,
    `_build_retry_seed`) — `row_black`/`col_black` and `placed` (the
    running count `_place_black_cells` compares against `target`) are
    initialized from `seed_grid`'s own existing black cells instead of
    zero, and only `seed_grid`'s still-white cells become placement
    candidates, so every already-black cell is preserved exactly as given
    rather than being re-decided. `locked_letters` (a `{(r, c): letter}`
    dict, meaningful only together with `seed_grid`) excludes its cells
    from the candidate pool entirely — at the user's explicit request,
    verified necessary rather than assumed: without it, a white cell that
    already holds a real, confirmed letter from the previous palier (see
    `_build_retry_seed`) would be just as eligible for a *new* black cell
    as any other still-white cell, silently destroying that confirmed
    letter the moment a black cell landed on it. Also threaded through to
    `_prefill_unfillable_slots` (together with `index`, the word index —
    both required together for that check to go beyond the plain
    length-only one; see `_has_slot_without_candidate`'s own docstring for
    the real bug this fixes).

    A further, distinct bug was found and fixed here, reported by the user
    from a real generation: "le tirage de nouvelles cases noires peut
    enfermer des groupes de lettres qui ne correspondent pas à un mot
    possible, et donc rendre la grille immédiatement injouable... la
    probabilité de produire une telle situation augmente avec le
    remplissage de plus en plus complet de la grille." The pre-fill phase
    above only runs *once*, before the ratio-based placement below —
    `_place_black_cells` (the generic row/column-balance heuristic used for
    that ratio-based placement) has no `locked_letters`/`index` parameter at
    all, so nothing stops it from truncating a slot that crosses an
    already-locked letter (carried forward from a previous palier) into a
    shape that no longer has enough real candidates given that fixed
    letter — exactly the "enfermer des lettres" the user described. This
    risk is close to zero on a fresh, unlocked palier (nothing is fixed yet
    for a new cell to conflict with) but grows every palier a search
    fails and more letters get locked in — matching the user's own
    observation that the probability increases "avec le remplissage de
    plus en plus complet de la grille." Confirmed directly before fixing:
    seeding 30 real grids with a handful of locked words each (mimicking a
    genuine carried-forward core) and running the ratio-based phase at a
    non-trivial ratio (0.10) on top produced at least one locked-touching
    slot with fewer than `PREFILL_MIN_WORD_COUNT` real candidates — several
    with *zero* — on **30/30** seeds. Fixed by running the exact same
    `_prefill_unfillable_slots` repair pass a second time, after the
    ratio-based placement, whenever `locked_letters`/`index` are present —
    reusing the existing mechanism rather than adding a new one: its own
    internal loop already re-scans every slot in the grid after each cell it
    places, so a single extra call is enough to reach a fresh fixed point
    against whatever the ratio-based phase just did, including marking a
    genuinely irreducible case `unfixable` for the next palier's own cleanup
    to resolve, exactly as it already does for the first pre-fill pass.

    A fixed, non-escalating extra density draw was reinstated after
    pre-fill, at the user's explicit request, once the previous ratio-
    escalation-across-paliers mechanism had been fully removed elsewhere
    (see `generate_grid`): "rétablir un tirage de 5% de nouvelles cases
    ajoutées (5% par rapport au nombre de cases blanches restantes)."
    Deliberately different from the old ladder it replaces: this fraction
    (`POST_PREFILL_BLACK_FRACTION`) never escalates from one palier to the
    next — the same fixed fraction applies every time. `black_ratio`
    itself is still honored as a floor (`round(rows*cols*black_ratio)`)
    for a caller that still passes a non-zero value (e.g. the CLI's
    `--black-ratio`), but `generate_grid`'s own default (`0.0`, never
    escalated) means this floor contributes nothing in the common case.

    Whether pre-fill's own cells count toward this fraction's target has
    itself changed, at the user's explicit request. Originally, the
    fraction was computed on the cells still white right *after* pre-fill
    had already placed whatever was structurally necessary (not the
    grid's total size), and added *on top* of `placed` (`placed` itself
    recomputed right after pre-fill so `_place_black_cells` never
    double-counted pre-fill's own cells as still-to-place) — meaning
    pre-fill's own cells never counted toward this specific percentage
    target: however many pre-fill needed, the same fixed fraction of
    whatever remained was *always* added on top. Changed later, again at
    the user's explicit request ("les cases noires ajoutées en
    pré-remplissage comptent pour l'objectif de remplissage en noir"): the
    fraction is now computed once, on the count of white cells *before*
    pre-fill ever runs (`initial_white_count`, captured right after the
    initial shuffle), and folded into the same `max(...)` as `placed` and
    the `black_ratio` floor, rather than added on top of `placed`
    unconditionally. Since `placed` already includes whatever pre-fill
    itself placed, this means pre-fill's own cells now genuinely count
    toward reaching this percentage: if pre-fill alone already placed more
    cells than the target percentage of the *original* white-cell count
    calls for, no further cells are added for this reason at all (`placed`
    wins the `max`); if it placed fewer, only the shortfall is added on
    top by `_place_black_cells` below.

    This mechanism was briefly (mistakenly) removed entirely in the same
    session, along with a separate, unrelated per-cycle single-cell lock
    (`_impossible_cell_groups`/`_lock_one_impossible_cell`, see
    `generate_grid`'s own history) — the user's own follow-up correction
    clarified that only that separate lock was meant to go, not this
    density-percentage mechanism, which is still meant to apply at the
    very first palier and at every palier immediately following a full
    cleanup, exactly as it always has.

    `black_enrichment_fraction` is no longer applied as the fixed
    percentage it's given as, at the user's explicit later request: it is
    scaled by `initial_white_count / (rows * cols)` — the proportion of
    the grid still white, measured on *this* call's own starting state
    (including whatever `seed_grid` already carries forward), before this
    call's own pre-fill runs — right after `initial_white_count` is
    captured. This scaled value is what feeds both `fill_objective_
    fraction` (and therefore the curative-cleanup zone budget inside
    `_prefill_unfillable_slots`) and the `target` computation below — the
    raw, caller-supplied `black_enrichment_fraction` is never used
    directly again past this point. For the very first palier of a call
    (`seed_grid is None`, an entirely white grid), this proportion is
    always exactly 1 (`initial_white_count == rows * cols`), so the
    scaled rate equals the raw one — the very first grid's own behavior
    is unchanged. From then on, as successive paliers accumulate more
    black cells (whatever `seed_grid` is carried forward already has), the
    proportion — and so the effective rate applied by pre-fill — shrinks
    accordingly, without any caller needing to compute or pass this
    shrinking rate itself.

    Adjacency between two black cells is never accepted at all for the
    very first palier of a call (`seed_grid is None`), at the user's
    explicit request — see `_place_black_cells`'s own `forbid_adjacency`
    parameter (passed here as `seed_grid is None`) for the mechanics.
    Every later palier, which always starts from an already-partially-
    black `seed_grid` carried forward from a previous one, keeps the
    pre-existing behavior unchanged (adjacency still accepted as a last
    resort when no isolated candidate can be found)."""
    # Copie défensive de `locked_letters`, au même titre que celle déjà
    # faite pour `seed_grid` juste en dessous — bug réel constaté en
    # direct, capture d'écran à l'appui : des lettres verrouillées bien
    # présentes dans l'aperçu "pattern" (début de cycle) disparaissaient
    # de l'aperçu "pattern_generated" du même cycle, et pas seulement à
    # l'écran. Cause : cet aperçu reconstruit spéculativement, dans le
    # processus PARENT, le motif que le dernier worker non réinitialisé
    # va lui-même recalculer — en appelant `make_pattern` directement sur
    # `carry_locked_letters`, l'objet PARTAGÉ réellement transmis juste
    # après aux vrais workers dispatchés (`_pattern_attempt`). Or
    # `_prefill_unfillable_slots`/`_remove_a_crossing_word` (« nettoyage
    # curatif ») mutent leur propre paramètre `locked_letters` sur place
    # (retrait de cases par `.pop`) — un comportement sans risque pour un
    # vrai worker, qui ne reçoit jamais qu'une copie indépendante une fois
    # ses arguments transmis à son propre processus séparé, mais qui
    # endommageait ici l'état partagé du processus parent lui-même : une
    # simple reconstruction d'aperçu, censée être jetable, retirait
    # réellement des lettres confirmées de `carry_locked_letters` avant
    # même que les vrais workers de ce palier ne soient soumis — ceux-ci
    # recevaient donc, eux aussi, une version déjà amputée. Cette copie
    # protège tout appelant, pas seulement celui-là, exactement comme la
    # copie de `seed_grid` protège déjà tout appelant contre une mutation
    # similaire de la grille elle-même.
    locked_letters = dict(locked_letters) if locked_letters else locked_letters
    if seed_grid is not None:
        grid = [row[:] for row in seed_grid]
        row_black = [row.count(BLACK) for row in grid]
        col_black = [sum(1 for r in range(rows) if grid[r][c] == BLACK) for c in range(cols)]
        locked = set(locked_letters) if locked_letters else set()
        candidates = [
            (r, c) for r in range(rows) for c in range(cols)
            if grid[r][c] == WHITE and (r, c) not in locked
        ]
        placed = sum(row_black)
    else:
        grid = [[WHITE] * cols for _ in range(rows)]
        row_black = [0] * rows
        col_black = [0] * cols
        # Exclut `locked_letters` du pool de candidates même sans
        # `seed_grid` — sans effet pour tout appelant existant avant
        # "Finir la grille" (aucun ne passait `locked_letters` sans
        # `seed_grid` du tout), mais nécessaire pour un worker "réinitialisé"
        # (`FULL_RESET_ATTEMPT_COUNT`, motif entièrement neuf) sur une
        # génération où des lettres restent verrouillées de façon
        # permanente (`permanent_locked_letters`, voir generate_grid) :
        # celles-ci ne doivent jamais recevoir de case noire, même sur un
        # motif reparti de zéro.
        locked = set(locked_letters) if locked_letters else set()
        candidates = [
            (r, c) for r in range(rows) for c in range(cols)
            if (r, c) not in locked
        ]
        placed = 0
    rng.shuffle(candidates)
    # Count of white cells *before* pre-fill — the base for the
    # `black_enrichment_fraction` computation below, at the user's
    # explicit request ("les cases noires ajoutées en pré-remplissage
    # comptent pour l'objectif de remplissage en noir"): the target
    # percentage is computed on this fixed total, not on however much
    # white remains once pre-fill is done.
    initial_white_count = len(candidates)

    # À la demande explicite de l'utilisateur : le taux fixe ("Taux noir",
    # `black_enrichment_fraction`) n'est plus appliqué tel quel dans les
    # phases de pré-remplissage — il est multiplié par la proportion de
    # cases blanches restantes (cases blanches restantes / cases totales
    # de la grille), mesurée sur CE palier précis (via `seed_grid` s'il y
    # en a un) avant que son propre pré-remplissage ne démarre. Pour la
    # toute première grille (aucun `seed_grid`, entièrement blanche),
    # `initial_white_count == rows * cols` donc cette proportion vaut 1 —
    # "le taux reste donc 1 pour la toute première grille" — et elle
    # diminue mécaniquement, palier après palier, à mesure que la grille
    # se noircit, sans qu'aucun code appelant n'ait besoin de le calculer
    # lui-même : `initial_white_count` reflétait déjà cette réalité, il
    # ne servait simplement pas encore à moduler le taux lui-même.
    white_proportion = initial_white_count / (rows * cols)
    black_enrichment_fraction = black_enrichment_fraction * white_proportion

    # « Nettoyage curatif » (voir _prefill_unfillable_slots) : réutilise ce
    # même objectif de remplissage en noir de la grille entière comme seuil
    # au-delà duquel une zone impossible bascule du simple ajout de cases
    # noires vers le retrait d'un mot déjà verrouillé qui la croise — à la
    # demande explicite de l'utilisateur, plutôt que d'inventer un nouveau
    # seuil séparé pour cette règle. `black_ratio` est presque toujours 0.0
    # aujourd'hui (voir plus haut), donc `black_enrichment_fraction` domine
    # en pratique ; les deux sont pris en compte ici par simple robustesse
    # pour un appelant (le CLI) qui fixerait encore `--black-ratio`.
    fill_objective_fraction = max(black_ratio, black_enrichment_fraction)

    if available_lengths is not None:
        candidates = _prefill_unfillable_slots(
            grid, rows, cols, row_black, col_black, candidates, available_lengths,
            index, locked_letters, rng, fill_objective_fraction,
            forbid_adjacency=(seed_grid is None),
        )

    placed = sum(row.count(BLACK) for row in grid)
    # `placed` already includes whatever pre-fill placed above — counting
    # it toward the target means this last term
    # (`black_enrichment_fraction * initial_white_count`, computed on the
    # white total *before* pre-fill, never on what's left after it) is a
    # third argument to `max`, on the same footing as `placed` and the
    # `black_ratio` floor — no longer added on top unconditionally, at the
    # user's explicit request: if pre-fill already placed more cells than
    # this percentage calls for, no further cell is added for this reason
    # (`placed` already wins the max); if it placed fewer, only the
    # difference is completed.
    target = max(
        placed,
        round(rows * cols * black_ratio),
        round(black_enrichment_fraction * initial_white_count),
    )
    _place_black_cells(grid, rows, cols, row_black, col_black, candidates, target, placed,
                        index=index, locked_letters=locked_letters, available_lengths=available_lengths,
                        forbid_adjacency=(seed_grid is None))

    if available_lengths is not None and locked_letters:
        _prefill_unfillable_slots(
            grid, rows, cols, row_black, col_black, candidates, available_lengths,
            index, locked_letters, rng, fill_objective_fraction,
            forbid_adjacency=(seed_grid is None),
        )

    return grid



# ---------- Extraction des cases (slots across / down) ----------

def extract_slots(grid, rows, cols):
    """A white run of exactly 2 cells is now a real, cluable slot (a 2-letter
    word — "et", "ou", "no", etc.), not just a passthrough for a crossing
    word; a run of exactly 1 cell never becomes a slot at all (see
    is_structurally_valid's border-zone/orphan-check discussion for why both
    are tolerated in the grid at all)."""
    slots = []
    for r in range(rows):
        c = 0
        while c < cols:
            if grid[r][c] == WHITE:
                start = c
                while c < cols and grid[r][c] == WHITE:
                    c += 1
                if c - start >= 2:
                    slots.append([(r, cc) for cc in range(start, c)])
            else:
                c += 1
    for c in range(cols):
        r = 0
        while r < rows:
            if grid[r][c] == WHITE:
                start = r
                while r < rows and grid[r][c] == WHITE:
                    r += 1
                if r - start >= 2:
                    slots.append([(rr, c) for rr in range(start, r)])
            else:
                r += 1
    return slots


def slot_direction(cells):
    """"across" if a slot's own cells run along a single row (its first two
    cells share a row), "down" if they run along a single column — the
    same convention build_word_entries already computes locally elsewhere
    in this file. Only the first two cells need comparing: every slot is a
    straight horizontal or vertical run, never diagonal, so this never
    needs the whole list."""
    return "across" if cells[0][0] == cells[1][0] else "down"


class DualIndex:
    """Wraps two independently-built word indices (see build_index below)
    — one for the grid's "across" words, one for its "down" words — so
    every consumer of a plain `index[length]`-shaped lookup (Filler.
    _domain, _slot_candidates, sample_letter_biases, minimize_black_
    squares, ...) can resolve the right one for a given slot without
    itself needing to know whether this is a monolingual or a bilingual
    generation. `for_cells`/`for_direction` are the only two ways this
    object is ever read — every existing function that used to do
    `index[length]` directly now does `index.for_cells(cells)[length]`
    (or `.for_direction(direction)[length]`) instead, resolving `cells`'s
    own direction with `slot_direction` above.

    For an ordinary, monolingual generation, `across` and `down` are the
    exact same index object (never built twice) — every lookup stays
    byte-for-byte identical to the single-index behavior this file always
    had, at the cost of one extra attribute check per lookup (negligible
    next to the dict/set work `_domain`/`_slot_candidates` already do).
    Only a genuinely bilingual generation (`generate_grid`'s own
    `bilingual_wordlist_path`, see its docstring) ever builds two
    distinct index dicts and wraps them here — added at the user's
    explicit request: "toutes les étapes utilisent la première langue
    pour les mots horizontaux, et la seconde langue pour les mots
    verticaux." Picklable like the plain dict it replaces (both attributes
    are themselves picklable), so it crosses the `ProcessPoolExecutor`
    worker-process boundary via `_init_worker`'s own `initargs` exactly
    like the single index dict always did — for the monolingual case,
    pickling preserves the shared `across is down` identity, so this
    never doubles the data actually sent to a worker."""
    __slots__ = ("across", "down")

    def __init__(self, across, down):
        self.across = across
        self.down = down

    def for_direction(self, direction):
        return self.across if direction == "across" else self.down

    def for_cells(self, cells):
        return self.for_direction(slot_direction(cells))


class DualSet:
    """Same idea as DualIndex, but for a plain set of "available" slot
    lengths (see PREFILL_MIN_WORD_COUNT/available_lengths below) rather
    than a full word index — a given length can be well-covered by one
    language's dictionary and not the other's, so "is this length
    available" must also be resolved per direction for a bilingual grid.
    `across`/`down` are the same set object for a monolingual grid,
    exactly like DualIndex.

    Also used to carry a bilingual grid's per-language theme glossary
    (`generate_grid`'s `priority_words`/`bilingual_priority_words`): the
    `__bool__` below lets `if priority_words:` / `if not priority_words:`
    guards keep working whether `priority_words` is a plain frozenset
    (monolingual) or a DualSet (bilingual)."""
    __slots__ = ("across", "down")

    def __init__(self, across, down):
        self.across = across
        self.down = down

    def __bool__(self):
        return bool(self.across or self.down)

    def for_direction(self, direction):
        return self.across if direction == "across" else self.down

    def for_cells(self, cells):
        return self.for_direction(slot_direction(cells))


def _priority_words_for(priority_words, cells):
    """The frozenset of priority theme words applicable to slot `cells`'s
    own direction — see `generate_grid`'s `priority_words`. On a bilingual
    grid, `priority_words` is a DualSet (one glossary per language, at the
    user's explicit request: "When a grid is bilingual, a per-language
    theme glossary must be generated"); otherwise a single frozenset (or
    empty/`None`). Always returns a frozenset."""
    if not priority_words:
        return frozenset()
    if isinstance(priority_words, DualSet):
        return priority_words.for_cells(cells)
    return priority_words


# ---------- Lexicon index: words by (length, position, letter) ----------
#
# With 100,000+ words, filtering by linear scan at every cell is too slow.
# Indexed once per length: pos[p][letter] -> set of words of this length
# having `letter` at position p. Intersecting a handful of sets (one per
# already-known letter) replaces a full scan of the lexicon.

def build_index(by_length, frequencies=None):
    """`frequencies` (a {MOT: frequency} dict, `None` by default) feeds
    `index[length]["freq"]` — used only by `_noise_slot_cells` (see
    `NOISE_FREQUENCY_THRESHOLD`) to tell a statistically credible candidate
    apart from a near-zero dictionary entry. Omitted (`None`), every word of
    `index[length]["freq"]` falls back to `0.0` — a no-op for any caller
    that doesn't use this feature (no real caller other than `generate_
    grid` today, but an isolated test that builds its own small lexicon
    doesn't need to supply this parameter to keep working as before)."""
    frequencies = frequencies or {}
    index = {}
    for length, words in by_length.items():
        pos_sets = [defaultdict(set) for _ in range(length)]
        for w in words:
            for p, ch in enumerate(w):
                pos_sets[p][ch].add(w)
        index[length] = {
            "words": words,
            "pos": pos_sets,
            "freq": {w: frequencies.get(w, 0.0) for w in words},
        }
    return index


# ---------- CSP: backtracking fill ----------

# MRV (Minimum Remaining Values — pre-selecting the least constrained slot,
# see the full history in the project-best-practices SKILL) was removed
# from `Filler._backtrack`'s own selection rule, at the user's explicit
# request: it no longer has a place in how the grid is built now. Its
# original justification — spot as early as possible the slot most likely
# to block, within a single, complete fill attempt — no longer holds once
# progress happens through small steps that follow one another and
# accumulate across paliers ("reprise telle-quelle", `_pattern_continue`,
# and `used_words`-aware impossibility detection, see below): a slot with
# very few candidates once some letters are locked is now, in practice,
# almost always a sign that it's genuinely blocked (or about to be) rather
# than a slot deserving priority attention — yet MRV would systematically
# push it ahead of every other one, including easy slots abundantly
# supplied with candidates, which could make the search spin its wheels on
# an almost hopeless case instead of making progress elsewhere. See the
# project-best-practices SKILL for the full diagnostic that led to this
# removal.
#
# **This removal still stands**: reinstating MRV as an absolute priority
# was briefly tried to fix a sparse fill of the very first palier (blank
# grid, nothing locked yet), then explicitly rejected by the user — "my
# last request on this topic was precisely to stop giving MRV priority."
# The real fix for this specific case doesn't involve MRV; see CLAUDE.md
# for the solution eventually adopted.

# Frequency (in number of calls to _backtrack) at which a CSP search checks
# `cancel_event` (the "Stop" button, see Filler.__init__), at the user's
# explicit request ("the Stop button doesn't apply quickly enough...
# provide for stopping in every phase") — a search can call _backtrack
# hundreds of thousands of times (up to `deadline_checks`) without ever
# otherwise yielding control, so an *external* checkpoint between two
# paliers (already in place) isn't enough to make "Stop" responsive while a
# palier is in progress. Checked every CANCEL_CHECK_INTERVAL times rather
# than on every call: `multiprocessing.Event.is_set()` stays cheap, but no
# reason to pay that cost at every node of a search that can visit hundreds
# of thousands of them.
CANCEL_CHECK_INTERVAL = 500

# Threshold (fraction of the grid's white cells) and check frequency (in
# number of calls to _backtrack) for early abandonment of an attempt, at
# the user's explicit request: "When a generation situation reaches more
# than 30% of the grid deemed unfillable, consider this attempt's own
# palier as failed, and stop trying to add words." See Filler._backtrack —
# checked periodically (like CANCEL_CHECK_INTERVAL above, not on every
# call) since computing impossible cells (impossible_zone_cells) has a
# real, non-negligible cost if repeated at every node of a search that can
# visit hundreds of thousands of them.
UNFILLABLE_ABANDON_FRACTION = 0.30
UNFILLABLE_ABANDON_CHECK_INTERVAL = 500

# Check frequency (in number of calls to _backtrack) for the "another
# attempt of the same palier has already finished" signal (see
# attempt_done_event/generate_grid), at the user's explicit request:
# "interrupt every search as soon as one search finishes (success or
# failure) to move on to the next palier." Same value and same reasoning as
# CANCEL_CHECK_INTERVAL/UNFILLABLE_ABANDON_CHECK_INTERVAL above (a
# `multiprocessing.Event.is_set()` stays cheap but not free to repeat at
# every node of a search that can visit hundreds of thousands of them) —
# kept as its own constant, for consistency with the style already used
# here, rather than reusing one of the two existing constants whose name
# carries an entirely different meaning.
PALIER_ATTEMPT_DONE_CHECK_INTERVAL = 500

# Fraction of a palier's PARALLEL_ATTEMPTS attempts that must have finished
# (success or failure) before every other still-running attempt of the same
# palier is interrupted, at the user's explicit request — refining the
# initial design (interrupt as soon as the very first attempt finishes):
# "à partir de 30% des tentatives qui se terminent... interrompre toutes
# les tentatives." See attempt_done_event/generate_grid.
#
# Fixed, for the time being, at 1.0 (100%) — i.e. every single attempt of
# the batch must finish before any interruption ever happens, which in
# practice means it never fires at all (the batch's own last attempt to
# finish has, by definition, nothing left to interrupt) — at the user's
# explicit later request: "Donner un nom de variable à la quantité de
# process qui échouent avant de décider d'interrompre tous les process
# (actuellement 30%). Fixer cette proportion pour le moment à 100% (on
# attend que tous les process terminent)." The variable already had a name
# from the original request above; only the value changed here. With
# `math.ceil(1.0 * len(futures)) == len(futures)`, `interrupt_threshold`
# below always equals the full batch size, so `attempt_done_event` is only
# ever set once every attempt has already completed on its own — a
# temporary, deliberately conservative setting the user may revisit later.
PALIER_ATTEMPT_INTERRUPT_FRACTION = 1.0

# Grace period (seconds) left for `best_state_queue`'s drain (see
# generate_grid) to catch a message published right before a worker yields
# control, at the user's explicit request ("Each process tracks its own
# best state, and tells the parent process when that best state has
# changed"). Needed because of a well-known `multiprocessing.Queue`
# quirk, confirmed live with an isolated test: `put()` doesn't block — it
# hands the object off to an internal thread dedicated to feeding the
# underlying pipe, which may not have finished its work at the exact
# moment the calling process yields control (`f.result()` in the parent);
# a plain `get_nowait()` right after can therefore legitimately raise
# `Empty` even though a message was just published (reproduced: an
# isolated `try_fill` publishing a state, immediately followed by a
# no-delay drain, retrieved nothing — the same drain after a `time.sleep
# (0.1)` did retrieve the published message). A short value (20ms) is more
# than enough in practice: this delay is only ever paid once per palier,
# only once the "fast" drain (a `get_nowait()` loop) has already consumed
# everything immediately available — and only if a message genuinely
# arrives during this short extra delay; otherwise the next palier starts
# without waiting.
BEST_STATE_QUEUE_DRAIN_GRACE_S = 0.02

# Cadence (seconds) at which `generate_grid` republishes, as long as a
# search is in progress, the percentage of the check budget (`deadline_
# checks`) already consumed by the current palier's most advanced attempt —
# at the user's explicit request: "on the interface's status line, add the
# percentage of the budget already consumed by the current fill phase."
# Reuses `best_state_buffer` (already continuously drained by `_drain_
# best_state_queue_continuously`, see its own definition) rather than a new
# dedicated channel: every message arriving there already carries `checks`
# (see `_publish_new_best`), so the maximum of that value among the
# current palier's messages is already a reasonable estimate of "how far
# the search has gotten" — an estimate, not an exact reading at time T,
# since it only advances at the moments one of the parallel attempts beats
# its own record for words placed (see `Filler.on_new_best`), not at every
# individual check; a worker deeply stuck in backtracking without ever
# improving its record therefore shows a percentage frozen at its last
# known record, rather than continuous progress — the displayed value is
# therefore a lower bound ("at least X% already consumed"), never an
# overstatement. 2 seconds, the same cadence as the interface's own polling
# (`POLL_INTERVAL_MS`, frontend/static/script.js): frequent enough to look
# "live" to the eye, without republishing on every individual publication
# (up to ~50-60 per attempt, see `_worker_best_state_queue`), which would
# spam `backend.log` for an imperceptible gain in freshness.
BUDGET_PROGRESS_REPORT_INTERVAL_S = 2.0

# Maximum number of consecutive "reprise telle quelle" paliers (see
# generate_grid's own `if still_has_hope:` branch) allowed before a full
# cleanup ("nettoyage complet") is triggered unconditionally, even if the
# current pattern still has real hope of progress. Named out of a previously
# unnamed inline literal (`consecutive_continue_paliers >= 5`), at the user's
# explicit request.
#
# Set to 0 — every single palier triggers a full cleanup, no "reprise telle
# quelle" streak at all. This was first tried, then briefly reverted to 5
# after appearing to cause a severe regression (a real generation stuck for
# 150+ consecutive paliers on a pattern already 100% filled with real,
# crossing-confirmed letters and 0% impossible) — but the user pushed back
# directly on that diagnosis: "La reprise telle quelle ou le nettoyage est
# un mécanisme qui intervient après la tentative de remplissage, qui doit
# aller jusqu'au bout... tous les emplacements restants doivent être testés
# avant de terminer un cycle... Si on a bien testé tous les emplacements
# restant, et que tous les mots en place sont valides, la grille est alors
# réputée réussie." The real bug was elsewhere: `Filler`/`try_fill` could
# end a search attempt (deadline exceeded, 30% abandon, interrupted by a
# sibling attempt, or genuinely exhausted) while a handful of slots were
# already fully and validly determined by real crossing letters — matching
# exactly one still-available dictionary word each — yet never explicitly
# confirmed by `_backtrack`, simply because its own selection order never
# reached them in time. Fixed at the actual root (`_close_implied_slots`,
# called from `try_fill` right after `filler.solve()` returns): any such
# trivially-implied slot is now confirmed as a final, cheap closing pass
# before a search attempt's outcome is decided — so 0 no longer needs
# "reprise telle quelle" to paper over this gap, restoring the 0 value the
# user actually asked for. See `_close_implied_slots`'s own docstring for
# the full mechanism and the live evidence that motivated it.
#
# Raised from 0 to 4, at the user's own later explicit request ("Régler
# MAX_CONSECUTIVE_CONTINUE_PALIERS à 4") — a plain value change, no
# reasoning given beyond the number itself; every mechanic this constant
# gates (the counter's own increment/reset points, the all-abandoned
# force-nettoyage rule, `_close_implied_slots`'s own fix above, which
# remains what makes even a small non-zero value here safe) is untouched.
#
# A real regression on the standard 15×10 benchmark's seed 7 was found and
# confirmed causal by a direct A/B (this exact constant, nothing else,
# flipped back to 0 and re-tested) before shipping this value: in **Flash**
# mode (`deadline_checks=1000`, the tightest of the 5 real `BUDGET_MODES`)
# seed 7 fails reproducibly at 4 (two runs, 17.2s/17.3s, all 200 paliers
# exhausted) but succeeds reliably at 0 (41.6s, 60 words) — seed 2
# succeeds either way. Reported to the user with this measurement; they
# chose to keep 4 regardless, the same trade-off already accepted for the
# `Filler._backtrack` checks-per-candidate change earlier this session:
# Flash's own tiny budget is the mode most exposed to this kind of
# reliability cost, not necessarily representative of the real default
# budget (300 000+) or a larger `BUDGET_MODES` choice.
MAX_CONSECUTIVE_CONTINUE_PALIERS = 4

# Number of CONSECUTIVE full cleanups during which `generate_grid` may
# produce exactly the same state (black/white pattern AND confirmed
# content, see `_cycle_start_preview`) before being declared infeasible
# and reset to an entirely blank grid on the next cycle — at the user's
# explicit request: "Remember the grids at the end of each cycle. When the
# same grid is produced for more than 3 cycles, declare that grid
# infeasible, and remove it on the next cycle (it becomes the entirely
# blank grid of the following palier)." See generate_grid, inside the
# `else:` (full cleanup) branch of `if still_has_hope: ... else: ...`, for
# the mechanism itself — deliberately restricted to this one branch, never
# to "reprise telle quelle", after two regressions measured live on the
# standard 15×10 (Flash) benchmark and two back-and-forths with the user
# (see the mechanism's own comment for the full detail): comparing the
# pattern alone across both branches confused a stable pattern (normal
# under "reprise telle quelle", where a black cell is only ever added one
# time in ten, see BLACK_CELL_INSTEAD_OF_REMOVAL_PROBABILITY) with a
# genuine deadlock; comparing pattern+content across both branches always
# duplicated MAX_CONSECUTIVE_CONTINUE_PALIERS, which already bounds
# "reprise telle quelle" with a gentler response (an ordinary cleanup, not
# a blank grid). Restricted to cleanup, this safety net covers a deeper
# fixed point than the one already handled right next to it (comparing
# only the confirmed letters, a single retry with `exclude_impossible_
# locked=True`, see `previous_locked_letters` below) — which doesn't
# always resolve the deadlock on the first try. Named separately from
# MAX_CONSECUTIVE_CONTINUE_PALIERS above: the two ceilings answer two
# different questions (how many "reprise telle quelle" cycles to chain
# without any cleanup at all, vs. how many consecutive cleanups to
# tolerate a state that still isn't changing despite them) and have no
# relation to each other.
GRID_REPEAT_INFEASIBLE_THRESHOLD = 3

# Number of PARALLEL_ATTEMPTS workers that, right after a full cleanup
# ("nettoyage complet" — see generate_grid's own `else:` branch, as opposed
# to "reprise telle quelle"), start the very next palier from a completely
# blank grid instead of the just-cleaned seed_grid/locked_letters every
# other worker of that palier gets — at the user's explicit request: "A
# chaque nettoyage complet (tous les 5 cycles) redémarrer 20% des process
# avec une grille réinitialisée totalement." All PARALLEL_ATTEMPTS workers
# normally start from the exact same carried-forward state after a cleanup
# (only their own random seed differs), which can make every one of them
# converge on the same kind of dead end again and again — deliberately
# sacrificing a small share of the batch to a genuinely fresh start gives
# the search a chance to escape that instead. See generate_grid's own
# `just_cleaned` flag.
#
# Originally a fraction (`FULL_RESET_ATTEMPT_FRACTION = 0.20`, resolving to
# `round(0.20 * PARALLEL_ATTEMPTS)` workers — 2 on a 10-core machine).
# Reduced to a fixed count of 1, at the user's explicit later request:
# "Réduire le nombre de process qui calculent une grille totalement
# nouvelle à 1 seul (au lieu de 20%)." A single from-scratch worker is
# already enough to give the search a genuinely fresh escape route from a
# repeated dead end, at a smaller cost to the batch's own carried-forward
# progress than sacrificing 2+ workers to it every single cleanup.
FULL_RESET_ATTEMPT_COUNT = 1


def _slots_touching(slots, target_indices):
    """Returns the set of slot indices (excluding `target_indices`
    themselves) that share at least one cell with one of the slots in
    `target_indices` — used both by `Filler.__init__` (to never try to
    fill a slot that crosses a slot already known impossible, see
    `_crossing_excluded_slots`) and by `generate_grid` (so the
    "still_has_hope" computation treats these same slots as hopeless too,
    rather than as a still-promising slot — see below for why this second
    use is necessary)."""
    target_indices = set(target_indices)
    if not target_indices:
        return set()
    cell_to_slots = defaultdict(list)
    for i, cells in enumerate(slots):
        for cell in cells:
            cell_to_slots[cell].append(i)
    touching = set()
    for i in target_indices:
        for cell in slots[i]:
            for j in cell_to_slots[cell]:
                if j != i:
                    touching.add(j)
    return touching


# Window for drawing at random among a slot's best candidate words, once
# sorted by `_candidate_score` (see `Filler._backtrack`) — a bit like
# `_place_black_cells`'s own 32-cell window for black cells: keeps overall
# priority on the statistically best-scored words while avoiding trying
# them in exactly the sort order, which would amount to an entirely
# deterministic choice (for a given seed) rather than genuine exploration.
# Raised from 20 to 200, at the user's explicit request: "avoids overly
# rare words, while leaving more room for exploring varied solutions" — a
# wider window still, in practice, mostly reaches statistically well-ranked
# words (never the very last ones in the dictionary), but among a notably
# wider pool than before, for more diversity from one attempt to the next.
#
# Raised from 200 to 5000, at the user's explicit request: "The top 200
# force starting from blank slots with a very restricted vocabulary. Relax
# the constraint... (this will probably have the effect of cancelling out
# the point of the scoring, but I'd like to see what it gives)" — the user
# themselves anticipates that such a wide window, on a slot still entirely
# blank (no cell fixed by a crossing, so `letter_scores` has no
# discriminating effect at all on the sort — see `_candidate_score`),
# amounts in practice to an almost uniform draw across the whole
# dictionary of that length, rather than a genuine priority for the
# best-scored words.
#
# Raised again from 5000 to 20000, at the user's explicit request, set
# "for the time being" to "everything available for 8-letter words" — the
# French dictionary has 19,066 8-letter words (counted live from data/
# wordlist_fr_full.tsv), so 20000 already covers the entire dictionary for
# any slot length up to and including 8 letters (and nearly the entire
# dictionary beyond that — only a few much rarer/longer lengths have more)
# : at this value, the window no longer excludes any word for the vast
# majority of real slots, pushing the draw even further toward an almost
# uniform choice over the whole dictionary of the length in question (the
# same consequence already anticipated above when moving to 5000, only
# more pronounced still).
CANDIDATE_SCORE_WINDOW = 20000

# Size (fixed, not a proportion of the group) of the final draw window
# among the retained group of slots (`selection_pool`, see `Filler.
# _backtrack`, "Choose which slot to fill first"): only the `SLOT_
# SELECTION_WINDOW_SIZE` slots with the smallest geometric score (see the
# computation right above) are kept, regardless of `selection_pool`'s own
# size — never fewer if the group has fewer slots than this size (`[:N]`
# on a shorter list simply returns the whole list).
SLOT_SELECTION_WINDOW_SIZE = 10

# Once the window above is obtained (`window`, sorted by ascending
# geometric score), `Filler._backtrack` re-sorts it a second time by the
# number of letters already placed in each slot (the most letters first —
# `_placed_letter_count`, the same fait-acquis/mere-guess distinction as
# `_has_known_letter`), then reduces it again to its own first `SLOT_
# SELECTION_REFINE_FRACTION` slots (the ones best supplied with already-
# known letters) before the final draw — at the user's explicit request,
# who also raised this proportion from 1/4 to 1/2 in the same move (a
# milder reduction, keeping half rather than a quarter of `window`).
# Floor **always at 1 slot, never 0** (never 5 either, unlike the previous
# window): `window` itself can be as small as its own floor of 5, and a
# higher floor here would cancel out the requested reduction in this very
# common case (half of 5 is 2, but a third or a quarter of 5 would already
# be 1, under a floor of 5 that would then force the whole window to be
# kept as-is) — this reduced window (`refined_window`) can therefore never
# end up empty, whatever `window`'s size or this fraction's value.
# Named separately from `SLOT_SELECTION_WINDOW_SIZE` above: one fixes a
# window size, the other a proportion — they apply to two different
# windows, one after the other (this one operates on `window`, not on
# `selection_pool`), with no numeric relationship between them.
SLOT_SELECTION_REFINE_FRACTION = 1 / 2


class Filler:
    def __init__(self, slots, index, rng, forced_letters=None, letter_scores=None,
                 excluded_slots=None, cancel_event=None, batch_abandoned_event=None,
                 attempt_done_event=None, on_new_best=None, locked_letters=None,
                 priority_words=None):
        self.slots = slots
        self.index = index
        self.rng = rng
        # Theme preselection (see generate_grid's `priority_words`): in
        # _backtrack, a slot's candidates that belong to the glossary of
        # ITS OWN direction (`_priority_words_for`) are tried BEFORE any
        # other dictionary word — a non-theme word is therefore only tried
        # on a slot once backtracking has exhausted, with no solution,
        # every theme word that fit there. A single frozenset on a
        # monolingual grid, a DualSet (one glossary per language) on a
        # bilingual grid; empty = no theme, no change to the order.
        self.priority_words = priority_words or frozenset()
        # Signal shared between the parallel attempts of the same batch,
        # see its own definition (`_worker_batch_abandoned_event`) — set by
        # any one of them the moment it abandons itself (see below),
        # checked by every other one.
        self.batch_abandoned_event = batch_abandoned_event
        # "This palier already has its answer" signal (see attempt_done_event
        # in generate_grid), at the user's explicit request: "interrupt every
        # search as soon as one search finishes (success or failure) to move
        # on to the next palier." Unlike `batch_abandoned_event` above, this
        # one is passed by both `_pattern_attempt` and `_pattern_continue`:
        # it never judges the quality or prospects of THIS attempt's own
        # pattern (which, for `_pattern_attempt`, would say nothing reliable
        # about a sibling attempt's independent pattern — see
        # `_worker_batch_abandoned_event`) — it only announces that ANOTHER
        # attempt of the same palier already finished (success or failure)
        # and that continuing to search here is now pointless, regardless of
        # what this attempt would eventually have found.
        self.attempt_done_event = attempt_done_event
        # Web UI "Stop" button (see GenerationCancelled), at the user's
        # explicit request: unlike the checkpoints already in place between
        # two paliers (generate_grid) or between two black cells removed
        # (minimize_black_squares), a CSP search on its own can run for a
        # very long time (up to `deadline_checks`, width × height × 2000
        # checks — see try_fill) without ever yielding control — without a
        # checkpoint *inside* the search itself, "Stop" could stay
        # invisibly ineffective for the whole duration of the current
        # palier. Checked every CANCEL_CHECK_INTERVAL calls to _backtrack
        # (see below) rather than on every call — a `multiprocessing.
        # Event.is_set()` stays cheap, but hundreds of thousands of calls
        # per search still justify not checking it at literally every
        # node.
        self.cancel_event = cancel_event
        # cell -> letter "recommended" by the prior statistical sampling
        # (see sample_letter_biases) — a plain hint used by _domain to
        # steer the search from the start, never a real assignment: the
        # moment a crossing slot is genuinely assigned, its own letter
        # takes precedence over this hint (see _domain below).
        self.forced_letters = forced_letters or {}
        # cell -> letter genuinely locked by a previous palier (the
        # `locked_letters` of `_pattern_attempt`/`_pattern_continue`), at
        # the user's explicit request — kept here *separately* from
        # `self.forced_letters`, unlike before, when the caller merged it
        # directly into `forced_letters` even before this `Filler` was
        # built (`{**forced_letters, **locked_letters}`). That merge lost a
        # real distinction: `_domain(i, ignore_forced=True)` (used only by
        # `impossible_zone_slots`, see below) deliberately ignores every
        # `self.forced_letters` entry — correct for a mere statistical seed
        # never verified, but `locked_letters` is not one of those: it's
        # genuinely confirmed content, carried from one palier to the next.
        # Real bug confirmed live: a slot entirely locked by `locked_
        # letters`, whose combination matches no real word (so excluded
        # from the search, never assigned), was almost never flagged
        # "impossible" once this merge was ignored by `ignore_forced=True`
        # — 330 instances out of 349 measured live on the same test seed.
        # Result: `_clean_blocked_slots`/`_build_retry_seed` (the cleanup
        # between paliers) never saw this slot as a problem to fix, so
        # never removed the crossing word responsible for it — the same
        # invalid combination then reconstructed itself identically, cycle
        # after cycle, sometimes for more than 70 consecutive cycles on a
        # single cell, never making progress and never being flagged.
        # Separating the two dicts and checking `self.locked_letters`
        # unconditionally (see _domain below, never ignored even with
        # `ignore_forced=True`) fixes this at the root.
        self.locked_letters = locked_letters or {}
        # cell -> Counter(letter -> occurrences), the same statistical
        # sampling as forced_letters above but kept in full (see sample_
        # letter_biases) — used by _backtrack to sort a slot's candidate
        # words instead of drawing them at random, at the user's explicit
        # request (see _candidate_score below). Always filled by
        # `_pattern_attempt` (at the user's explicit request: this sorting
        # no longer depends on `force_letters_fraction`, only `forced_
        # letters` still does) — `or {}` here remains a safeguard for a
        # direct caller of Filler that doesn't supply one (e.g. a test), in
        # which case _backtrack simply falls back to a purely random draw.
        self.letter_scores = letter_scores or {}
        # cell -> [(slot_index, position_within_that_slot), ...]. Precomputed
        # once here rather than looked up with list.index() inside _domain
        # (the hot path, called millions of times per grid) since a cell's
        # position within a slot never changes once slots are extracted.
        self.cell_to_slots = defaultdict(list)
        for i, cells in enumerate(slots):
            for pos, cell in enumerate(cells):
                self.cell_to_slots[cell].append((i, pos))
        # For every slot, the set of OTHER slots sharing at least one cell
        # with it (precomputed once here, from cell_to_slots right above —
        # a slot's own geometry never changes once `slots` is extracted).
        # Used by `_backtrack`, at the user's explicit request, to
        # immediately evaluate, right after placing a word, only the slots
        # that word genuinely crosses — rather than waiting for the next
        # recursive call, which recomputes the domain of EVERY still-open
        # slot of the grid, including the ones this word could never have
        # affected anyway.
        self._crossing_slots = [
            {j for cell in cells for j, _ in self.cell_to_slots[cell] if j != i}
            for i, cells in enumerate(slots)
        ]
        # Total number of white cells in the grid (one cell per
        # cell_to_slots key, regardless of how many slots run through it)
        # — the denominator of UNFILLABLE_ABANDON_FRACTION, see
        # _backtrack. Computed once here, never recomputed.
        self._total_white_cells = len(self.cell_to_slots)
        # Turns True the moment an attempt is abandoned along the way for
        # lack of reasonable hope (see _backtrack and UNFILLABLE_ABANDON_
        # FRACTION) — once set, every following call to _backtrack fails
        # immediately, with no further exploration.
        self.abandoned = False
        # Distinct from `self.abandoned` above (which it still reuses as a
        # fast short-circuit, see _backtrack): set specifically when this
        # attempt was cut short because ANOTHER attempt of the same palier
        # already answered (`attempt_done_event`), never because this one
        # judged its own pattern hopeless — try_fill uses it to distinguish
        # the two in `diagnostics["reason"]`.
        self.interrupted_by_sibling = False
        # "across" or "down" per slot, precomputed once for _backtrack's
        # own across/down alternation (see below) — same convention as
        # build_word_entries: a slot of more than one cell is across if its
        # 2nd cell is on the same row as the 1st, down otherwise (a
        # single-cell slot — a case that doesn't exist here since
        # extract_slots requires at least 2 cells — doesn't matter for this
        # purpose).
        self.directions = [
            "across" if len(cells) > 1 and cells[1][0] == cells[0][0] else "down"
            for cells in slots
        ]
        self.assignment = [None] * len(slots)
        # Slots outside every consideration of _backtrack — never selected
        # for an assignment attempt, and never a source of immediate
        # failure via the domain check below — at the user's explicit
        # request: "before cleaning up the slot identified as blocked,
        # keep adding words as long as it's possible." A slot already
        # identified as impossible during a previous attempt on this same
        # pattern, without this exclusion, would start by making the very
        # first call to `_backtrack` fail (the domain check runs over
        # *every* unassigned slot before even choosing which one to
        # handle, so an empty domain for this one specific slot was enough
        # to prevent any new assignment anywhere else in the grid, even
        # with no relation to it at all) — seen live, `checks=1` on every
        # attempt. An empty set by default (`set()` rather than `None`,
        # never re-evaluated on every call) leaves every existing caller
        # unchanged.
        self.excluded_slots = excluded_slots if excluded_slots is not None else set()
        # Slots that cross (share at least one cell with) a slot in
        # `excluded_slots` — new selection rule, at the user's explicit
        # request, taking priority over `_backtrack`'s own 7 levels: never
        # try to fill such a slot. A word placed there would be removed
        # again by the next cleanup anyway (`_build_retry_seed`, which
        # removes any word directly crossing an impossible slot) if it was
        # never reconsidered before then — better to never place it at all
        # than to spend search budget on a word doomed to disappear.
        # Computed once here, not on every call to _backtrack:
        # `excluded_slots` never changes after __init__.
        self._crossing_excluded_slots = _slots_touching(slots, self.excluded_slots)
        self.used_words = set()
        self.checks = 0
        # Copy of the assignment at the moment the largest number of slots
        # were filled simultaneously across the whole search, regardless of
        # exactly where it eventually failed — unlike self.assignment
        # (which reverts to [None, ...] once the search is entirely undone
        # by backtracking), best_assignment keeps track of the most
        # advanced state reached. Purely diagnostic, at the user's explicit
        # request: it triggers no recovery attempt of any kind (neither a
        # patch nor a retry) — only a preview to surface on failure, see
        # try_fill/diagnostics["example_grid"].
        self.best_assignment = list(self.assignment)
        self.best_assigned_count = 0
        # Called back (see _backtrack) every time best_assignment has just
        # been improved, with this new state as an argument — lets
        # try_fill publish this new state to the parent process in real
        # time instead of only once, right at the very end of the search,
        # at the user's explicit request (see `_worker_best_state_queue`,
        # further down in this file, for the full history). `None` by
        # default — no effect for any pre-existing caller.
        self.on_new_best = on_new_best

    def _domain(self, i, ignore_forced=False):
        """Set/list of words compatible with cell i's already-known letters
        (without yet excluding words used elsewhere — see _pick). A letter
        "recommended" by self.forced_letters (see __init__) counts as a
        constraint just like a letter genuinely imposed by an already-
        assigned crossing slot — but only as long as no crossing slot is
        actually assigned at this cell: a real assignment always wins over
        a mere statistical hint.

        `ignore_forced` (`False` by default — unchanged behavior for any
        pre-existing caller), at the user's explicit request: entirely
        ignores `self.forced_letters` (the mere statistical seed), keeping
        only letters genuinely imposed by an already-assigned crossing
        slot — see `impossible_zone_slots` (the only caller that passes
        `True`), which needs an "impossible" notion grounded solely in
        confirmed facts, never in a mere, never-verified statistical seed.
        `self.locked_letters` (genuinely confirmed content, carried from
        one palier to the next — see __init__) is, by contrast, NEVER
        ignored, even with `ignore_forced=True`: it isn't a guess, so
        `impossible_zone_slots` must be able to rely on it just as much as
        on a real crossing assignment."""
        cells = self.slots[i]
        length = len(cells)
        # self.index is a DualIndex (see its own docstring) — resolved
        # here by this slot's own direction, so an across slot only ever
        # draws from language A's dictionary and a down slot from
        # language B's, on a bilingual grid; the same single dictionary
        # both ways on an ordinary monolingual one.
        idx = self.index.for_cells(cells).get(length)
        if idx is None:
            return ()
        constraints = {}
        for pos, cell in enumerate(cells):
            letter = None
            for j, other_pos in self.cell_to_slots[cell]:
                if j != i and self.assignment[j] is not None:
                    letter = self.assignment[j][other_pos]
                    break
            if letter is None:
                letter = self.locked_letters.get(cell)
            if letter is None and not ignore_forced:
                letter = self.forced_letters.get(cell)
            if letter is not None:
                constraints[pos] = letter
        if not constraints:
            return idx["words"]
        sets = []
        for pos, ch in constraints.items():
            s = idx["pos"][pos].get(ch)
            if not s:
                return ()
            sets.append(s)
        sets.sort(key=len)
        result = sets[0]
        for s in sets[1:]:
            result = result & s
            if not result:
                return ()
        return result

    def _placed_letter_count(self, i):
        """Number of cells of slot i already determined by a real letter —
        a crossing word already assigned during this same attempt, or a
        letter locked from a previous palier (self.locked_letters). Same
        fait-acquis/mere-guess distinction as _has_known_letter
        (self.forced_letters, a mere statistical seed, never counts here)
        — see its own docstring. Used by _backtrack to re-sort level 5's
        own selection window (the most letters already placed first), at
        the user's explicit request."""
        count = 0
        for cell in self.slots[i]:
            if cell in self.locked_letters:
                count += 1
                continue
            for j, _ in self.cell_to_slots[cell]:
                if j != i and self.assignment[j] is not None:
                    count += 1
                    break
        return count

    def _has_known_letter(self, i):
        """True if slot i already has at least one cell determined by a
        real letter — a crossing word already assigned during this same
        attempt, or a letter locked from a previous palier
        (self.locked_letters). A mere statistical seed (self.forced_
        letters) never counts here, as everywhere else in this file (see
        _domain/impossible_zone_slots) — it's only an unverified guess, not
        a fait-acquis. Used by _backtrack to prioritize already-partially-
        known slots over an entirely blank one."""
        for cell in self.slots[i]:
            if cell in self.locked_letters:
                return True
            for j, _ in self.cell_to_slots[cell]:
                if j != i and self.assignment[j] is not None:
                    return True
        return False

    def _slot_letter_frequency_score(self, i):
        """Sum of the squares of the measured frequencies (self.letter_
        scores — the same statistic sample_letter_biases computes to
        choose seeds/forced_letters, see _candidate_score below) of the
        most frequent letter at every STILL-FREE cell of slot i — a cell
        already determined by a real letter (a crossing word already
        assigned during this same attempt, or self.locked_letters) offers
        no fill option anymore, so it's not counted here, same exclusion
        as _placed_letter_count/_has_known_letter.

        Used by _backtrack as level 5's final tie-break criterion (see its
        own docstring), at the user's explicit request: favors the slot
        whose own zone statistically offers the most fill options — that
        is, for every still-free cell, several different real words fitting
        there rather than a single letter dominating the rest — and
        therefore, for the neighboring slots crossing those same cells, the
        most credible letters to work with in turn. Squaring the
        frequencies favors a slot where several still-free cells all show a
        strong statistical consensus over a slot that only owes a high
        score to a single exceptional cell — the same reasoning already
        applied elsewhere in this file (_candidate_score, the sum of
        squares of word lengths in generate_grid)."""
        total = 0
        for cell in self.slots[i]:
            if cell in self.locked_letters:
                continue
            fixed = False
            for j, _ in self.cell_to_slots[cell]:
                if j != i and self.assignment[j] is not None:
                    fixed = True
                    break
            if fixed:
                continue
            counts = self.letter_scores.get(cell)
            if counts:
                total += max(counts.values()) ** 2
        return total

    def _candidate_score(self, i, word):
        """Sum of the squares of the statistical scores (self.letter_
        scores, see sample_letter_biases) of `word` over slot i's cells
        that are *not* already fixed by an assigned crossing slot — an
        already-fixed cell needs no further ranking, since `word` must
        already match it exactly to be in the domain (see _domain). Used
        by _backtrack to sort a slot's candidate words, at the user's
        explicit request, rather than drawing them at random — squaring
        the scores favors a word whose several still-free cells all match
        the statistical consensus well, over a word that only owes a high
        score to a single exceptional cell, consistent with the same
        choice already made elsewhere in this project (the sum of squares
        of word lengths to break ties among parallel attempts, see
        generate_grid)."""
        cells = self.slots[i]
        total = 0
        for pos, cell in enumerate(cells):
            fixed = any(
                j != i and self.assignment[j] is not None
                for j, _ in self.cell_to_slots[cell]
            )
            if fixed:
                continue
            total += self.letter_scores.get(cell, {}).get(word[pos], 0) ** 2
        return total

    def exclude_immediately_impossible_slots(self):
        """À la demande explicite de l'utilisateur : "Le tour après une
        régénération semble s'arrêter dès qu'un emplacement est impossible,
        ce qui peut se produire immédiatement à cause du tirage des cases
        noires. Tous les tours doivent se dérouler aussi longtemps qu'on
        peut ajouter des mots en respectant les règles d'ajout."

        À appeler une seule fois, juste avant `solve()` (donc après
        l'application éventuelle de `preseed_assignment` par l'appelant,
        voir try_fill) et avant tout appel à `_backtrack` : à ce moment
        précis, `self.assignment` ne contient encore que les cases
        réellement verrouillées (aucune décision de recherche n'a encore
        été prise), donc le domaine de chaque emplacement encore non
        assigné ne reflète que des contraintes définitives — s'il est déjà
        vide (ou entièrement déjà utilisé) à cet instant, il le restera
        pour le reste de cette recherche, quoi que la recherche essaie par
        ailleurs (`_domain` ne dépend que des croisements réellement
        assignés/verrouillés, jamais d'un choix encore à faire).

        Sans ce correctif, le contrôle de domaine de `_backtrack` (qui
        s'exécute pour *tous* les emplacements non assignés avant même de
        choisir lequel traiter) trouvait ce même emplacement vide à
        absolument chaque appel, quel que soit le chemin de recherche
        emprunté — la recherche échouait alors immédiatement (`checks=1`
        ou presque), sans jamais avoir la moindre chance d'essayer de
        remplir le reste de la grille, pourtant souvent parfaitement
        remplissable par ailleurs. Chaque emplacement ainsi identifié est
        ajouté à `excluded_slots` (même mécanisme que pour un emplacement
        déjà connu impossible d'un palier précédent, voir `_pattern_
        continue`) — jamais assigné, mais laissant la recherche continuer
        librement sur tout le reste ; `_crossing_excluded_slots` est
        recalculé en conséquence, pour que la nouvelle règle "ne pas
        essayer de remplir les emplacements qui croisent un emplacement
        réputé impossible" s'applique aussi à ces exclusions découvertes
        ici, pas seulement à celles reçues en argument.

        Un seul passage suffit (pas besoin de reboucler jusqu'à un point
        fixe) : exclure un emplacement ne change le domaine calculé
        d'aucun autre — `_domain` ne consulte jamais `excluded_slots`, il
        ne fait que déterminer lesquels `_backtrack` a le droit de
        sélectionner."""
        newly_excluded = {
            i for i in range(len(self.slots))
            if self.assignment[i] is None
            and i not in self.excluded_slots
            and i not in self._crossing_excluded_slots
            and all(w in self.used_words for w in self._domain(i))
        }
        if newly_excluded:
            self.excluded_slots = self.excluded_slots | newly_excluded
            self._crossing_excluded_slots = _slots_touching(self.slots, self.excluded_slots)
        return newly_excluded

    def solve(self, deadline_checks):
        return self._backtrack(deadline_checks)

    def impossible_zone_slots(self):
        """Like impossible_zone_cells (see below), but returns *slot
        indices* rather than the cells themselves — at the user's explicit
        request, for the cross-palier resume algorithm (see generate_grid/
        _build_retry_seed) which needs to know *which slots* are blocked
        in order to remove the words directly connected to them, not just
        which cells to highlight in the preview. `impossible_zone_cells`
        is rewritten in terms of this method rather than duplicating the
        same computation twice.

        A slot counts as impossible not only when its raw domain
        (`_domain`, which ignores words already used elsewhere in the
        grid) is empty, but also when *every* one of its candidates is
        already used by another word already placed in `best_assignment`
        — otherwise such a slot (a domain that looks non-empty, but with
        no candidate genuinely available anymore) stays invisible to this
        diagnostic, wrongly preventing `generate_grid`'s "still_has_hope"/
        `excluded_slots` from ever treating it as blocked (see _backtrack
        below for the same fix on the search side). `used_at_best` is
        recomputed directly from `best_assignment` rather than reading
        `self.used_words` — the latter reflects `self.assignment`'s
        *current* state (which may have fully backtracked to its starting
        state once the search has ended), not necessarily that of the most
        advanced point (`best_assignment`) this diagnostic examines.

        `_domain(i, ignore_forced=True)` — never the default version, which
        would let a mere statistical seed (`forced_letters`, an unverified
        hint, see `sample_letter_biases`) count as a hard constraint. Real
        bug confirmed live: a slot could be declared "impossible"
        (highlighted red in the preview, excluded from filling, targeted
        by the cross-palier cleanup) even though none of its letters were
        actually imposed by a confirmed crossing word — just a statistical
        guess never confirmed nor refuted, which never becomes relevant
        again once the search has stopped on that state. Direct
        consequence: the cleanup (`_clean_blocked_slots`) would then
        recompute a real candidate for this same slot itself (since it
        never looks at `forced_letters`) and would therefore literally do
        nothing — no word removal, no black cell via the 1/10 rule —
        leaving the slot marked "impossible" indefinitely, cycle after
        cycle, with no cleanup mechanism ever able to act on it. Confirmed
        live on a real grid: 41% of slots declared impossible actually
        had, once the statistical seeds were ignored, at least one real
        candidate — a disagreement this frequent between this diagnostic
        and the cleanup that must rely on it couldn't be a mere rare edge
        case."""
        saved = self.assignment
        self.assignment = self.best_assignment
        used_at_best = {w for w in self.best_assignment if w is not None}
        result = [
            i for i, word in enumerate(self.best_assignment)
            if word is None
            and all(w in used_at_best for w in self._domain(i, ignore_forced=True))
        ]
        self.assignment = saved
        return result

    def impossible_zone_cells(self):
        """Cells belonging to an unassigned slot, in the self.best_
        assignment state (the most advanced point reached before
        abandonment — see __init__), whose domain is empty (no word fits
        given the letters already fixed by crossing slots) — the
        "impossible zones" to highlight in a failed attempt's own preview
        (see try_fill, diagnostics["impossible_cells"]), at the user's
        explicit request. Can be empty: the most advanced point reached
        isn't necessarily the one where the search eventually failed —
        for instance a failure from exhausting the check budget
        (`deadline_exceeded`) can occur while every domain at that moment
        was still non-empty, just not yet resolved in time."""
        cells = set()
        for i in self.impossible_zone_slots():
            cells.update(self.slots[i])
        return sorted(cells)

    def _select_target_slot(self, unassigned, domains):
        """Chooses which slot to fill next among `unassigned` (already
        guaranteed non-empty, each with at least one genuinely available
        candidate — see the domain check right before this call, in
        `_backtrack`), via the 7-level cascade documented below.

        Factored out of `_backtrack` to be reused as-is by `interactive_
        place_word` (the web UI's "Interactif" mode), at the user's
        explicit request — a hand-written duplicate of this logic used to
        live there (a plain MRV: the smallest domain, then an already
        partially-known slot, then random), with neither level 2's length
        threshold (which excludes 2-3-letter slots) nor level 5's
        geometric score (which favors the top-left corner) — which made
        interactive fill start with 2-letter slots scattered across the
        grid instead of following the same rules as automatic generation.
        Takes `unassigned`/`domains` as parameters (rather than
        recomputing them) since `interactive_place_word` has already built
        them in a slightly different shape (`viable`, filtered by `used_
        words`) for its own use."""
        # 7-level selection rule, at the user's explicit request (MRV was
        # removed — see the comment further up, before the Filler class,
        # for why):
        # 1. first alternate across/down: draw the category (across or
        #    down) at random, with a probability proportional to the
        #    number of still-open slots in each of the 2 categories
        #    (self.directions, precomputed in __init__) — a category that
        #    still has many unfilled slots has a better chance of being
        #    chosen than the other, which naturally tends to alternate/
        #    balance the two as the fill progresses without fixing a
        #    strict order;
        # 2. **New, at the user's explicit request, taking priority over
        #    the domain criterion below**: among the slots of the drawn
        #    category that are **4 letters and longer** (at the user's
        #    explicit request — a 2-3-letter slot has a naturally
        #    restricted vocabulary, this priority brings nothing there),
        #    if at least one has a domain (`domains[i]`, already computed
        #    right above) with strictly fewer than `PREFILL_MIN_WORD_COUNT`
        #    candidate words — the same threshold pre-fill's own step 1
        #    uses to decide a slot needs a black cell — the choice is
        #    restricted to those slots only. Goal: try to resolve these
        #    fragile slots with a real word while the search is still
        #    making progress, before a future cleanup palier judges them
        #    insufficient and adds a black cell to fix them (see
        #    `_prefill_unfillable_slots`, step 1) — a word genuinely placed
        #    here avoids that black cell. First tried with a different
        #    criterion ("a single still-empty cell"), replaced by this one
        #    at the user's explicit request, which directly targets the
        #    same threshold pre-fill uses rather than a geometric proxy.
        #    If no slot of the category is in this case, this level
        #    changes nothing: level 3 then applies to the whole category,
        #    exactly as before this level was added;
        # 3. **New, at the user's explicit request**: among the slots of
        #    the group obtained at the previous level, if at least one
        #    already has at least one cell determined by a real letter
        #    (`_has_known_letter` — an already-assigned crossing word, or a
        #    letter locked from a previous palier — never a mere
        #    statistical seed), the choice is restricted to those slots
        #    only, excluding entirely blank slots as long as at least one
        #    already-partially-known one remains — finish an already-
        #    started slot rather than opening a new one. If every slot of
        #    the group is entirely blank, this level changes nothing:
        #    level 4 then applies to the whole group, exactly as before
        #    this level was added;
        # 4. **Themed grid only, at the user's explicit request**: moved
        #    here, after the "few candidates" and "at least one known
        #    cell" levels above — originally applied right after level 1,
        #    moved to this spot at the user's explicit request. Like any
        #    other link in the cascade, this level always applies after
        #    the previous one, with no particular priority over the
        #    following levels. If, among the slots of the group obtained
        #    at the previous level, at least one slot exists where a word
        #    from the theme glossary (`self.priority_words`, not yet used)
        #    still fits given the known letters, the choice is restricted
        #    to those slots. Filling therefore starts with thematically
        #    achievable zones (and places a theme word there in priority,
        #    see candidate sorting further below). With no theme, or if no
        #    slot of the group accepts a theme word, this level changes
        #    nothing: the next level then applies to the whole group;
        # 5. among the slots of the group obtained at the previous level, a
        #    purely **geometric** score is computed for each: `x² + y²`,
        #    where `(y, x)` is the slot's first cell (`self.slots[i][0]`,
        #    always the topmost/leftmost one among its own cells — see
        #    `extract_slots`), measured from the grid's own top-left
        #    corner (the same origin as `(row, col)` everywhere else in
        #    this file) — see the computation itself further below for the
        #    detail. This score doesn't depend at all on the slot's own
        #    fill state (neither its known letters nor its domain) —
        #    only on its fixed position in the grid — which tends to make
        #    the fill progress along a geometric front rather than by each
        #    slot's own difficulty. A uniform random draw is then made
        #    **among the `SLOT_SELECTION_WINDOW_SIZE` (10) slots with the
        #    smallest score** (the closest to the top-left corner) — a
        #    fixed window size, not a proportion of the group (see its own
        #    docstring). The slots are shuffled (with this attempt's own
        #    seeded RNG, hence reproducible) before being sorted by score:
        #    without this prior shuffle, the sort order (`sorted` is
        #    stable) would decide which tied slots pass the window's own
        #    cutoff, reintroducing the same positional bias already
        #    encountered elsewhere in this file (see further up, pre-
        #    fill's own "black column"/"triangle" bugs) — all the more
        #    relevant here since the score is geometric, so many slots can
        #    share exactly the same score (the whole arc at a given
        #    Euclidean distance from the corner). This geometric window
        #    (`window`) is then re-sorted twice more, each time reducing
        #    it further, before the final choice is made:
        # 6. by the number of letters already placed in each slot
        #    (`_placed_letter_count`, the most letters first), reduced to
        #    its own first `SLOT_SELECTION_REFINE_FRACTION` slots (see this
        #    constant's own docstring);
        # 7. by `_slot_letter_frequency_score` (see its own docstring), the
        #    highest score first — the slot whose own zone statistically
        #    offers the most fill options — whose very first entry directly
        #    becomes the chosen slot. Each of these two reductions
        #    re-shuffles its own input window beforehand (same reason as
        #    level 5's own shuffle: since `sorted` is stable, this shuffle
        #    is what breaks ties between slots with equal scores, not the
        #    order inherited from the previous sort).
        free_across = [i for i in unassigned if self.directions[i] == "across"]
        free_down = [i for i in unassigned if self.directions[i] == "down"]
        if free_across and free_down:
            direction_pool = self.rng.choices(
                [free_across, free_down],
                weights=[len(free_across), len(free_down)],
                k=1,
            )[0]
        else:
            direction_pool = free_across or free_down
        # Only for slots of 4 letters and longer, at the user's explicit
        # request: a 2-3-letter slot has a naturally restricted
        # vocabulary, triggering this "few candidates" priority there
        # brings nothing useful.
        few_candidates = [
            i for i in direction_pool
            if len(self.slots[i]) >= 4 and len(domains[i]) < PREFILL_MIN_WORD_COUNT
        ]
        selection_pool = few_candidates if few_candidates else direction_pool
        # New level, at the user's explicit request: among the group
        # obtained at the previous level, if at least one already has at
        # least one cell determined by a real letter (`_has_known_letter`
        # — an already-assigned crossing word, or a letter locked from a
        # previous palier), the choice is restricted to those slots only,
        # excluding entirely blank slots as long as at least one already-
        # partially-known one remains. If every slot of the group is
        # entirely blank, this level changes nothing.
        non_blank = [i for i in selection_pool if self._has_known_letter(i)]
        if non_blank:
            selection_pool = non_blank
        # Theme level: moved here, after the two levels above ("few
        # candidates" then "at least one known cell"), at the user's
        # explicit request — originally applied right after the category
        # draw. Like any other link in the cascade, this level always
        # applies after the previous one, with no particular priority over
        # the following levels. For a themed grid, the choice is
        # restricted to the slots of the group obtained at the previous
        # level where at least one word from the theme glossary (self.
        # priority_words) still fits, given the letters already known and
        # the words already placed elsewhere — filling therefore favors
        # thematically achievable zones (and places a theme word there in
        # priority, see candidate sorting further below). Skipped if
        # there's no theme at all, or if no slot of the group accepts a
        # theme word (nothing to restrict).
        if self.priority_words:
            # `selection_pool` always stays within a single direction
            # (across or down) — it only ever narrows `direction_pool`,
            # never mixes the two — so the applicable glossary (the same
            # for all its slots) is resolved once — a single frozenset on
            # a monolingual grid, that direction's language glossary on a
            # bilingual grid (see `_priority_words_for`).
            _pw = _priority_words_for(self.priority_words, self.slots[selection_pool[0]])
            theme_placeable = [
                i for i in selection_pool
                if any(
                    w not in self.used_words
                    for w in _pw.intersection(domains[i])
                )
            ]
            if theme_placeable:
                selection_pool = theme_placeable
        # Geometric score: x²+y², where (y, x) is the slot's first cell
        # (self.slots[i][0], always the topmost/leftmost one among its own
        # cells — see extract_slots), x/y measured from the grid's own
        # top-left corner — the same origin as `(row, col)` everywhere
        # else in this file, so x = column, y = row directly. A slot whose
        # first cell already sits at the top-left corner gets the lowest
        # possible score (0); the score grows as a slot starts further
        # down and/or further right. Squaring each coordinate before
        # summing them (a squared Euclidean distance, replacing an earlier
        # plain x + y linear score — a Manhattan distance, constant along
        # a whole diagonal) penalizes a slot that's markedly off-center on
        # a single axis more heavily than one at an equal Manhattan
        # distance but spread across both axes — a fill front held more
        # tightly around the top-left corner, rather than a flat diagonal.
        # An even earlier version measured this same score from the
        # top-RIGHT corner instead (x = distance from the right edge
        # rather than the left) — reverted once live testing confirmed the
        # fill was starting from the wrong corner, in favor of the
        # top-left origin used here; this version needs no knowledge of
        # the grid's own width at all (`cols` was removed from
        # `Filler.__init__`, which only ever used it for that earlier
        # calculation).
        scores = {
            i: self.slots[i][0][1] ** 2 + self.slots[i][0][0] ** 2
            for i in selection_pool
        }
        shuffled_pool = list(selection_pool)
        self.rng.shuffle(shuffled_pool)
        window = sorted(shuffled_pool, key=lambda i: scores[i])[:SLOT_SELECTION_WINDOW_SIZE]
        # New, at the user's explicit request: re-sort this window by the
        # number of letters already placed in each slot (the most letters
        # first), then reduce it again to its own first SLOT_SELECTION_
        # REFINE_FRACTION slots — see this constant's own docstring.
        # Re-shuffled first (with this attempt's own seeded RNG) for the
        # same reason as the previous shuffle: `sorted` is stable, so
        # without this second shuffle the order from the first sort (by
        # geometric score) would decide which slots tied on already-placed
        # letters pass this second window's own cutoff.
        shuffled_window = list(window)
        self.rng.shuffle(shuffled_window)
        placed_counts = {i: self._placed_letter_count(i) for i in window}
        refined_window_size = max(1, int(len(window) * SLOT_SELECTION_REFINE_FRACTION))
        refined_window = sorted(shuffled_window, key=lambda i: -placed_counts[i])[:refined_window_size]
        # New, at the user's explicit request: rank the slots of this
        # reduced window by _slot_letter_frequency_score (see its own
        # docstring), the highest score first — so the slot whose own zone
        # statistically offers the most fill options, including for the
        # neighboring slots crossing its still-free cells. Re-shuffled
        # first (with this attempt's own seeded RNG), for the same reason
        # as the two previous shuffles: `sorted` is stable, so without this
        # third shuffle the order from the two previous sorts would decide,
        # at equal score, which slot wins.
        shuffled_refined = list(refined_window)
        self.rng.shuffle(shuffled_refined)
        freq_scores = {i: self._slot_letter_frequency_score(i) for i in refined_window}
        return sorted(shuffled_refined, key=lambda i: -freq_scores[i])[0]

    def _backtrack(self, deadline_checks):
        # `self.checks` is no longer incremented here (once per call/node)
        # but once per candidate word genuinely attempted, in the `for w in
        # cands:` loop further below — see its own comment for the reason
        # (at the user's explicit request, "to avoid spending a long time
        # iterating over hopeless cases"). This first call (from `Filler.
        # solve()`) therefore starts with `self.checks` still at its entry
        # value (0 for a fresh search); the checks below remain correct
        # with this value as-is.
        if self.abandoned:
            return False
        if self.checks > deadline_checks:
            return False
        if (
            self.cancel_event is not None
            and self.checks % CANCEL_CHECK_INTERVAL == 0
            and self.cancel_event.is_set()
        ):
            raise GenerationCancelled()
        # Early stop of the WHOLE batch the moment a sibling attempt has
        # abandoned itself (see _worker_batch_abandoned_event and
        # UNFILLABLE_ABANDON_FRACTION below) — at the user's explicit
        # request: don't wait for this attempt to also reach its own
        # abandon threshold or its own budget once another one has already
        # judged the shared pattern hopeless. Same check frequency as the
        # other signals above/below — a real cost not worth paying at
        # every node.
        if (
            self.batch_abandoned_event is not None
            and self.checks % UNFILLABLE_ABANDON_CHECK_INTERVAL == 0
            and self.batch_abandoned_event.is_set()
        ):
            self.abandoned = True
            return False
        # Early stop as soon as ANOTHER attempt of the same palier already
        # answered (success or failure), at the user's explicit request
        # ("interrupt every search as soon as one search finishes (success
        # or failure) to move on to the next palier") — see
        # attempt_done_event in generate_grid. Unlike the batch_abandoned_
        # event checkpoint above, this one applies to both _pattern_attempt
        # and _pattern_continue (see Filler.__init__'s own docstring for why
        # the distinction made for batch_abandoned_event doesn't apply
        # here). Reuses `self.abandoned` as a fast short-circuit (same
        # mechanism as just above), but also sets
        # `self.interrupted_by_sibling` so try_fill can distinguish this
        # specific cause in `diagnostics["reason"]`.
        if (
            self.attempt_done_event is not None
            and self.checks % PALIER_ATTEMPT_DONE_CHECK_INTERVAL == 0
            and self.attempt_done_event.is_set()
        ):
            self.abandoned = True
            self.interrupted_by_sibling = True
            return False
        # Early abandonment of an attempt, at the user's explicit request
        # (see UNFILLABLE_ABANDON_FRACTION above): the moment more than 30%
        # of the grid's white cells belong to a slot deemed impossible (in
        # the sense of impossible_zone_cells, computed on best_assignment),
        # this attempt is judged to have no reasonable hope left and is
        # abandoned on the spot — no point continuing to try adding words
        # elsewhere on a pattern already this badly compromised. Checked
        # only every UNFILLABLE_ABANDON_CHECK_INTERVAL times (like
        # cancel_event above), not on every call: impossible_zone_cells
        # recomputes every unassigned slot's domain, a real cost not worth
        # paying at every node.
        if (
            self._total_white_cells > 0
            and self.checks % UNFILLABLE_ABANDON_CHECK_INTERVAL == 0
            and len(self.impossible_zone_cells())
            > UNFILLABLE_ABANDON_FRACTION * self._total_white_cells
        ):
            self.abandoned = True
            # Signals to every other attempt of the same batch that they
            # too can stop — see _worker_batch_abandoned_event's own
            # comment and the matching checkpoint further up in this same
            # method.
            if self.batch_abandoned_event is not None:
                self.batch_abandoned_event.set()
            return False
        unassigned = [
            i for i in range(len(self.slots))
            if self.assignment[i] is None
            and i not in self.excluded_slots
            and i not in self._crossing_excluded_slots
        ]
        # Counted directly from self.assignment (not derived from
        # `len(self.slots) - len(unassigned)`): with `excluded_slots`
        # non-empty, that latter formula would wrongly count every
        # excluded slot as "assigned" even though it genuinely stays at
        # None.
        assigned_count = sum(1 for a in self.assignment if a is not None)
        if assigned_count > self.best_assigned_count:
            self.best_assigned_count = assigned_count
            self.best_assignment = list(self.assignment)
            if self.on_new_best is not None:
                self.on_new_best(self.best_assignment)
        if not unassigned:
            return True

        # Every unassigned slot's domain is computed here (and immediate
        # failure follows if any one of them is already dry), to detect a
        # dead branch as early as possible — this domain is also used to
        # sort the finally chosen slot's own candidate words (see below),
        # whichever criterion designated it.
        #
        # `_domain` only accounts for letter constraints (already-assigned
        # crossings / statistical hints) — never `self.used_words`. A slot
        # with a non-empty raw domain can therefore, on an already very
        # full grid, genuinely have NO candidate left at all (every one of
        # its words already used elsewhere) — a real deadlock, identical
        # in practice to an empty domain, but invisible to this check
        # without also verifying `used_words` here. Real bug confirmed
        # live: a 143-slot grid stayed stuck at exactly 115 assigned/3
        # impossible for more than 180 consecutive paliers in a row,
        # `checks=1` every time — the slot genuinely blocking the search
        # had a technically non-empty domain (some fifteen candidates),
        # but every one of them was already used by another word of the
        # grid, so `impossible_zone_slots` (see above, same fix) never
        # surfaced it either.
        domains = {}
        for i in unassigned:
            domain = self._domain(i)
            if all(w in self.used_words for w in domain):
                return False
            domains[i] = domain

        # Selects which slot to fill next via the 7-level cascade,
        # factored out into _select_target_slot (reused as-is by
        # interactive_place_word — see its own docstring for the full
        # history of every level).
        best_i = self._select_target_slot(unassigned, domains)

        cands = [w for w in domains[best_i] if w not in self.used_words]
        # Always shuffled first (with this attempt's own seeded RNG, hence
        # reproducible) — whether this shuffle serves as the final draw
        # (letter_scores empty, unchanged behavior) or only to break ties
        # in the sort right below, `sort` being stable: without letter_
        # scores, a word never shares the same score twice (always 0), so
        # the shuffle's own order is what decides.
        self.rng.shuffle(cands)
        if self.letter_scores:
            # At the user's explicit request: try the chosen slot's
            # candidate words in priority order of the sum of squares of
            # their statistical scores over still-free cells (see
            # _candidate_score), instead of a purely random draw — applied
            # systematically as soon as `letter_scores` is supplied, which
            # is now the case on every attempt, whether `force_letters_
            # fraction` is 0 or not (see __init__ and _pattern_attempt):
            # only `forced_letters` (genuinely fixed cells) still depends
            # on that setting. This block only ever stays inactive — with
            # the shuffle right above then acting as the final draw,
            # exactly as before this feature existed — for a direct
            # caller of Filler that supplies no `letter_scores` at all.
            cands.sort(key=lambda w: self._candidate_score(best_i, w), reverse=True)
            # Not a strictly descending test order even so, at the user's
            # explicit request: at every draw, a random pick is made among
            # the `CANDIDATE_SCORE_WINDOW` best words *still remaining* in
            # the sort (not the first `CANDIDATE_SCORE_WINDOW` of the
            # original sort, fixed once and for all — the window slides as
            # words get removed from it) — see the constant's own
            # docstring for the detail of what it balances.
            window = CANDIDATE_SCORE_WINDOW
            reordered = []
            remaining = cands
            while remaining:
                take = min(window, len(remaining))
                idx = self.rng.randrange(take)
                reordered.append(remaining.pop(idx))
            cands = reordered
        if self.priority_words:
            # Theme preselection: the order already obtained above is
            # stabilized into two blocks — theme candidates first, then
            # the rest — so `for w in cands:` tries every theme word that
            # fits this slot before moving on to an ordinary dictionary
            # word. Backtracking does the rest: a non-theme word is only
            # reached if no theme word led to a solution here (nor
            # further down). Skipped if every candidate — or none of
            # them — is thematic (nothing to reorder).
            _pw = _priority_words_for(self.priority_words, self.slots[best_i])
            pri = [w for w in cands if w in _pw]
            if pri and len(pri) != len(cands):
                cands = pri + [w for w in cands if w not in _pw]
        for w in cands:
            # Count this placement attempt immediately, whether or not it
            # leads to a further recursive descent — at the user's
            # explicit request ("change it so the budget count is
            # incremented every time a word is attempted, whether it
            # triggers a recursive descent or not"), to avoid spending a
            # long time iterating over hopeless cases. Before this change,
            # `self.checks` was only incremented right at the top of
            # `_backtrack`, so only when the recursion genuinely went
            # further (see the comment right above the crossing check,
            # further below): a slot whose candidates almost all break a
            # crossing (see that same check) never recurses back into
            # `_backtrack`, so this counter didn't move at all while this
            # loop potentially went through hundreds of rejected candidates
            # one by one — neither the budget (`deadline_checks`) nor
            # `self.abandoned` was ever re-consulted while the loop kept
            # going, since these two checks are otherwise only evaluated
            # at `_backtrack`'s own entry. Counting — and checking — right
            # at this attempt, even before placing the word, finally
            # bounds this case: the loop now stops at most `deadline_
            # checks` attempts after its last pass through the top of the
            # function, rather than being able to continue indefinitely on
            # a doomed slot. `self.abandoned` is re-checked here for the
            # same reason: it may have been set to `True` by a sibling
            # attempt already explored earlier in this same loop (a
            # candidate that recursed, declared the search hopeless deeper
            # down, then yielded control back) — without this check, the
            # following candidates would keep being tried (and their own
            # crossing check computed, a real cost) before the next
            # recursive call finally notices via its own `if self.
            # abandoned: return False`.
            self.checks += 1
            if self.abandoned or self.checks > deadline_checks:
                return False
            self.assignment[best_i] = w
            self.used_words.add(w)
            # Evaluate right away, before descending further into the
            # recursion, whether the word just placed makes one of the
            # slots CROSSING it (self._crossing_slots, precomputed in
            # __init__) impossible to fill — the same impossibility
            # criterion as the domain check above (empty domain, or
            # entirely already used elsewhere), but restricted to only the
            # slots this word could genuinely have affected, at the user's
            # explicit request. Placing a word can never change the domain
            # of a slot sharing no cell with it (`_domain` only reads the
            # slot's own cells) — checking only the neighbors therefore
            # gives exactly the same result as the next recursive call's
            # own "every still-open slot" domain check, without having to
            # trigger it (neither its own `checks` counter nor its own
            # scan of the whole grid) for a word already doomed: if even
            # one neighbor has become impossible, this word is removed
            # immediately and the next one is tried, never descending any
            # further. A slot already set aside (`excluded_slots`/
            # `_crossing_excluded_slots`) is never affected by this — it
            # already never blocks anything for this same pattern.
            crossing_broken = False
            for j in self._crossing_slots[best_i]:
                if (
                    self.assignment[j] is None
                    and j not in self.excluded_slots
                    and j not in self._crossing_excluded_slots
                ):
                    domain = self._domain(j)
                    if all(w2 in self.used_words for w2 in domain):
                        crossing_broken = True
                        break
            if not crossing_broken and self._backtrack(deadline_checks):
                return True
            self.assignment[best_i] = None
            self.used_words.discard(w)
        return False


# ---------- Statistical pre-fill before the CSP ----------

# Number of words drawn at random per slot (filtered only by length, with
# no validation against other slots) to estimate, by plain sampling, which
# letter is most likely to occupy each cell even before the real fill ever
# starts — at the user's explicit request.
LETTER_BIAS_SAMPLE_SIZE = 100

# Fraction of the grid's total white cells that get fixed in advance with
# the letter most frequently observed there in the sampling above — only
# the cells where this letter came up most often (broadly speaking: the
# most "consensual" cells first) are kept, up to this fraction. Lowered
# from 10% to 5% at the user's explicit request.
LETTER_BIAS_FORCE_FRACTION = 0.05

# Minimum number of words in the LETTER_BIAS_SAMPLE_SIZE sample that must
# share the retained letter for a cell to be eligible to be fixed — at the
# user's explicit request, on top of the limit of a single forced cell per
# slot: too weak a consensus (a letter that only wins because the others
# were even more scattered, without genuinely dominating) doesn't
# guarantee enough compatible words remain to fill the slot once this
# letter is fixed.
LETTER_BIAS_MIN_COUNT = 10


def _force_single_candidate_slots(slots, index, known_letters, excluded_slots=None):
    """At the user's explicit request: "Before computing the statistics
    for placing seeds, add a step: when a valid slot no longer has more
    than one possible word, force the remaining letters to place that
    word." Unlike `sample_letter_biases`'s own statistical sampling (a
    plain consensus over 100 randomly drawn words, never a certainty), a
    slot whose already-known letters (`known_letters`) leave only one
    dictionary word possible is no longer a matter of probability: it's
    that word, or none. It then directly forces this slot's not-yet-known
    letters into the returned dict — on the same footing as a letter
    already locked by a previous palier, not as a mere statistical hint.

    Repeated until a full pass over every slot changes nothing anymore:
    forcing a slot's letters can, via a crossing cell, also bring a
    still-unresolved neighboring slot down to a single possibility — a
    single pass could miss this kind of chain reaction depending on the
    scan order.

    A slot from `excluded_slots` (already known impossible — see `Filler.
    excluded_slots`) is never tested: it will never be tried by the search
    anyway, no point looking for a deduction there. A slot already
    entirely known (every cell already in `known_letters`) also has
    nothing left to deduce — all that remains is to check, elsewhere (see
    `_pattern_attempt`'s own `preseed_assignment`), that the word it
    spells is genuinely real.

    Never modifies `known_letters` in place: returns a new dict, copied
    once at the very start, leaving the caller to decide what to do with
    the original (for instance comparing it to the augmented version to
    know whether anything changed)."""
    excluded = excluded_slots or set()
    known = dict(known_letters or {})
    changed = True
    while changed:
        changed = False
        for slot_idx, cells in enumerate(slots):
            if slot_idx in excluded:
                continue
            if all(cell in known for cell in cells):
                continue
            candidates = _slot_candidates(index, len(cells), cells, known)
            if len(candidates) != 1:
                continue
            word = next(iter(candidates))
            for pos, cell in enumerate(cells):
                if cell not in known:
                    known[cell] = word[pos]
                    changed = True
    return known


def _close_implied_slots(slots, index, assignment, used_words, excluded_slots=None):
    """Closes, in one cheap final pass, every slot the search left with
    every letter already determined by genuinely assigned crossing words —
    but which `_backtrack` itself never explicitly confirmed (it simply
    never had the chance to select it before the attempt ended, whatever
    the reason: budget exhausted, 30% abandonment, interrupted by a
    sibling palier, or genuinely exhausted search). Such a slot is
    visually "complete" (every cell already carries a real letter) but
    stays formally `None` in `assignment` — so neither counted as
    successful, nor ever flagged unfillable (`Filler.impossible_zone_
    slots()` never flags it: its one possible word isn't yet used
    elsewhere).

    Fixes a real bug reported live, backed by a screenshot: "77% filled"
    (= 100% of white cells already carrying a letter, 23% black cells)
    with 0% unfillable, and yet a generation that kept restarting
    indefinitely without ever succeeding — the user stated it explicitly
    as a principle: "every remaining slot must be tried before ending a
    cycle... if every word in place is valid, the grid is then deemed
    successful." This function is exactly that final test, applied once
    the search has ended rather than relying on `_backtrack` to have done
    it on its own.

    Unlike `_force_single_candidate_slots` (used even before the search
    starts, on only the letters already locked from a previous palier —
    never a word already placed to exclude at that stage), this one must
    account for `used_words`: a word already used elsewhere in the grid
    can't be confirmed a second time, even if it exactly matches the
    letters already in place.

    Repeated to a fixed point (confirming a slot can, via a crossing cell,
    determine another one in turn); mutates `assignment`/`used_words` in
    place, no return value.

    Never places a guessed or statistical word, and never makes the
    search itself progress: if no still-unassigned (and non-excluded) slot
    is already reduced to exactly one real, available word, this function
    changes nothing at all — it only confirms what is already, implicitly,
    the only remaining possibility.

    The `len(candidates) - len(used_words) > 1` guard avoids the cost of a
    `not in used_words` filter on a slot still largely open (thousands of
    raw candidates for a given length, against a few dozen/hundred already-
    used words): removing at most `len(used_words)` words can never bring
    a set larger than `len(used_words) + 1` down to exactly 1, so such a
    slot could never close here anyway — no point paying for the filter to
    verify it."""
    excluded = excluded_slots or set()
    known = {}
    for i, cells in enumerate(slots):
        word = assignment[i]
        if word is not None:
            for pos, cell in enumerate(cells):
                known[cell] = word[pos]
    changed = True
    while changed:
        changed = False
        for i, cells in enumerate(slots):
            if i in excluded or assignment[i] is not None:
                continue
            candidates = _slot_candidates(index, len(cells), cells, known)
            if len(candidates) - len(used_words) > 1:
                continue
            real_candidates = [w for w in candidates if w not in used_words]
            if len(real_candidates) != 1:
                continue
            word = real_candidates[0]
            assignment[i] = word
            used_words.add(word)
            for pos, cell in enumerate(cells):
                known[cell] = word[pos]
            changed = True


def sample_letter_biases(grid, rows, cols, index, rng,
                          sample_size=LETTER_BIAS_SAMPLE_SIZE,
                          force_fraction=LETTER_BIAS_FORCE_FRACTION,
                          excluded_slots=None, known_letters=None):
    """Before starting the real CSP fill on a freshly chosen black/white
    grid, at the user's explicit request: for every slot, draws
    `sample_size` random words of the right length, counts for each of
    that slot's cells which letter appears most often in the sample, keeps
    only the cells where this letter exceeds `LETTER_BIAS_MIN_COUNT` (10)
    occurrences (too weak a consensus — a letter that only wins because
    the others were even more scattered — doesn't guarantee enough
    compatible words remain once this letter is fixed), then draws at
    random among these eligible cells until covering `force_fraction` of
    the grid's white cells *still without a known letter* — not the total
    number of white cells, at the user's explicit request (see `target`'s
    own computation further below for the full reasoning) — at most ONE
    forced cell per slot (never two forced cells on the same word). The
    random draw (rather than the strongest-consensus cells first, a
    previous version of this rule) is at the user's explicit request,
    after a report: for a given length, systematically taking the most
    consensual cells first too often ended up fixing the same dominant
    letter (the language's most frequent one at that position) on most
    slots of that length, instead of varying. The limit of a single
    forced cell per slot remains necessary for the same reason as before:
    several cells forced independently on the same long slot can match no
    real word at all (each cell is chosen independently of the others,
    with no guarantee any real word has all of these letters at once),
    which was measured live: up to 9 out of 10 attempts failed right at
    the very first check. A cell belonging to two slots (a crossing)
    consumes both of their quotas at once — if either of the two already
    has its forced cell, the other can no longer offer a new one, even at
    a different cell. The number of genuinely forced cells can therefore
    stay below `force_fraction` — either because the grid doesn't have
    enough distinct slots to reach it (rare in practice), or because few
    cells reach the consensus threshold (more common, and deliberate:
    better to force fewer cells than to force one on a weak consensus).

    `excluded_slots` (a set of slot indices, `None` by default — no
    effect for a caller that doesn't supply one), at the user's explicit
    request: "seeds must only be placed on slots deemed playable (if
    possible), i.e. not locked as unfillable." A slot from this set
    (already known impossible — see `Filler.excluded_slots`) never offers
    any of its own cells as a candidate to become a seed anymore —
    placing a seed there would be a wasted hint, since this slot will
    never be tried by the search anyway. Only affects `forced`: `letter_
    scores` keeps being fed for *every* slot without exception, excluded
    ones included — a crossing cell shared with a non-excluded slot
    always needs its full statistical contribution to correctly sort that
    second slot's own candidate words (see below). "If possible": if
    every slot of the grid is excluded (an edge case never encountered in
    practice), `eligible` simply stays empty and no seed is placed at
    all, rather than forcing a cell onto an unfillable slot for lack of
    an alternative.

    `known_letters` (a {cell: letter} dict, `None` by default — no effect
    for a caller that doesn't supply one), at the user's explicit
    request: "Only draw words that are valid with respect to the letters
    already in place on the slots." Previously, a slot's sample was drawn
    at random among *every* word of the right length, with no regard for
    the letters already known at some of its cells (`_pattern_attempt`'s
    `locked_letters`, carried from one palier to the next by the resume
    mechanism — see `generate_grid` — or the letters already fixed by
    `_pattern_continue`'s `preseed_assignment`) — a less informative
    sampling than it should be, since a good share of the 100 drawn words
    could already be incompatible with what was already known for
    certain. For a slot with at least one cell in `known_letters`, the
    sample is now drawn only among words genuinely compatible with those
    letters (the same per-position intersection as `Filler._domain`/
    `_slot_candidate_count`) rather than among the entire lexicon of that
    length. If no word matches — a slot that's genuinely impossible in
    the proper sense, since its already-placed letters match no real word
    — the sample is simply empty and this slot contributes to neither
    `forced` nor `letter_scores` for this palier: at the user's explicit
    request ("don't test slots deemed impossible... the change must make
    a valid draw impossible"), this filtering alone is enough to
    guarantee no valid draw is possible on such a slot, with no need for
    a separate explicit check — unlike `excluded_slots` above (whose role
    remains necessary for `_pattern_continue`: a slot listed there can be
    impossible for a broader structural reason than just its own
    already-known letters taken in isolation, in which case this
    filtering alone isn't enough to exclude it from sampling). A cell
    already present in `known_letters` is also never offered as an
    `eligible` candidate (see below): the word already known at that
    position needs no further statistical hint, and keeping it would have
    wasted the single-seed-per-slot quota on a cell that genuinely
    would have needed it.

    Returns `(forced, letter_scores)`:
    - `forced`: a {cell: letter} dict — the "hints" Filler treats as
      constraints as long as no crossing slot is genuinely assigned at
      that cell (see Filler._domain), not as definitively placed letters;
    - `letter_scores`: a {cell: Counter(letter -> occurrences)} dict —
      the *complete* tally of the sampling above at every white cell of
      the grid (not just the winning letter kept for `forced`), combining
      both slots of a crossing cell (each contributes its own sample to
      that same cell). At the user's explicit request: used by `Filler.
      _backtrack` to sort a slot's candidate words by the sum of squares
      of these scores over its still-free cells, highest to lowest,
      instead of a purely random draw — see `Filler.__init__`/
      `_candidate_score`."""
    slots = extract_slots(grid, rows, cols)
    cell_to_slots = defaultdict(list)
    for slot_idx, cells in enumerate(slots):
        for cell in cells:
            cell_to_slots[cell].append(slot_idx)

    excluded = excluded_slots or set()
    known = known_letters or {}
    eligible = []  # (count, cell, letter) — cells exceeding LETTER_BIAS_MIN_COUNT
    letter_scores = defaultdict(Counter)
    for slot_idx, cells in enumerate(slots):
        length = len(cells)
        idx = index.for_cells(cells).get(length)
        if not idx or not idx["words"]:
            continue
        # Restricts the drawn lexicon to words genuinely compatible with
        # this slot's already-known letters (`_slot_candidates`, the same
        # per-position intersection as `Filler._domain`), at the user's
        # explicit request — see `known_letters`'s own docstring above. No
        # known constraint: falls back to the entire lexicon of this
        # length, exactly as before this feature existed. An empty set (no
        # real word matches the letters already in place — this slot is
        # impossible in the proper sense of the term): no valid draw
        # exists, so none is made (neither `forced` nor `letter_scores`
        # for it at this palier).
        pool = _slot_candidates(index, length, cells, known)
        if not pool:
            continue
        sample = rng.choices(list(pool), k=sample_size)
        for pos, cell in enumerate(cells):
            counts = Counter(word[pos] for word in sample)
            letter_scores[cell].update(counts)
            letter, count = counts.most_common(1)[0]
            if cell not in known and count > LETTER_BIAS_MIN_COUNT and slot_idx not in excluded:
                eligible.append((count, cell, letter))
    rng.shuffle(eligible)

    # At the user's explicit request: the targeted seed count must be
    # computed relative to the number of white cells *still without a
    # known letter*, not relative to the grid's total white-cell count —
    # otherwise, once a "reprise telle quelle" palier has already
    # confirmed a good part of the grid (see `known_letters` above), the
    # raw white-cell count stays almost unchanged (only new black cells
    # bring it down), giving the misleading impression of a "constant"
    # seed count from one cycle to the next even though fewer and fewer
    # cells genuinely still need a statistical hint. A cell already in
    # `known` is never itself eligible to become a seed anyway (see
    # above) — also excluding it from the target count's own base aligns
    # the two. For the very first grid of a palier (`known` empty), this
    # count is exactly identical to the total white-cell count —
    # unchanged behavior.
    remaining_white = sum(
        1 for r in range(rows) for c in range(cols)
        if grid[r][c] == WHITE and (r, c) not in known
    )
    target = round(remaining_white * force_fraction)
    forced = {}
    used_slots = set()
    for count, cell, letter in eligible:
        if len(forced) >= target:
            break
        if cell in forced:
            continue
        touching = cell_to_slots[cell]
        if any(slot_idx in used_slots for slot_idx in touching):
            continue
        forced[cell] = letter
        used_slots.update(touching)
    return forced, dict(letter_scores)


def try_fill(grid, rows, cols, index, rng, deadline_checks=None, diagnostics=None,
             forced_letters=None, letter_scores=None, preseed_assignment=None,
             excluded_slots=None, cancel_event=None, batch_abandoned_event=None,
             attempt_done_event=None, locked_letters=None, best_state_queue=None,
             attempt_id=None, proper_noun_words=None, max_proper_nouns=None,
             non_gloss_words=None, max_non_gloss=None, priority_words=None):
    """`non_gloss_words`/`max_non_gloss` (both `None` by default — every
    pre-existing caller unaffected) work exactly like `proper_noun_words`/
    `max_proper_nouns` below, but count words absent from the definition
    dictionary `data/gloss_dictionary/<lang>_glosses.jsonl` instead of
    likely proper nouns — same final-guardrail mechanism, same
    difficulty caps by request (voir MAX_NON_GLOSS_WORDS) : "FACILE :
    aucun mot inconnu ; MOYEN : au plus 2 ; DIFFICILE : au plus 5". If
    `filler.assignment` ends up with more than `max_non_gloss` of its
    words in `non_gloss_words`, the completion is rejected the same way
    an over-`max_proper_nouns` one is (`truly_complete` -> False,
    `diagnostics["reason"] = "too_many_non_gloss_words"`).

    `proper_noun_words`/`max_proper_nouns` (both `None` by default — every
    pre-existing caller is unaffected), at the user's explicit request:
    "in EASY mode don't allow placing proper nouns, in MEDIUM mode allow
    at most 2 proper nouns, in HARD mode allow up to 5 proper nouns" (see
    MAX_PROPER_NOUNS). `proper_noun_words` is the set of words (grid form,
    no accent) considered proper nouns for this language (see
    generate_grid, which builds it once from `accents`/`PROPER_NOUN_
    EXCLUDED_LANGS`). Checked here, once the search has ended, as a final
    safety net rather than as an active constraint inside `Filler.
    _backtrack` itself (an area of this file documented as particularly
    fragile — see MAX_PROPER_NOUNS): if the number of `filler.assignment`
    words present in `proper_noun_words` exceeds `max_proper_nouns`, this
    attempt is NOT considered successful even if `truly_complete` would
    otherwise be true — treated exactly like any other fill failure
    (`reason` becomes `"too_many_proper_nouns"`, a new diagnostic code),
    so the palier fails and the cross-palier resume mechanism already in
    place (see generate_grid) retries normally, with other words.

    `preseed_assignment`/`excluded_slots` (both `None` by default — every
    pre-existing caller is unaffected), at the user's explicit request:
    "reprise telle-quelle" mechanism carrying a palier's state over to the
    next one (see generate_grid/_pattern_continue), distinct from the
    cleanup-based resume (`_build_retry_seed`) already in place.
    `preseed_assignment`, when given, initializes `Filler.assignment`
    (and `used_words`/`best_assignment`/`best_assigned_count` accordingly)
    with the previous palier's already-known state instead of starting
    from a blank grid — every already-assigned slot stays locked there,
    `_backtrack` never reconsiders it. `excluded_slots` (see `Filler.
    excluded_slots`) ignores, for the duration of this search, any slot
    already identified as impossible in the previous palier — without
    this exclusion, `_backtrack`'s own plain domain check (which runs for
    *every* unassigned slot even before choosing which one to handle)
    would make the entire search fail on the very first call, even for
    slots with no relation to that one at all.

    With `excluded_slots` non-empty, a deliberately excluded slot can never
    be assigned by this search anymore: `Filler.solve()` can therefore
    return `True` (in `_backtrack`'s own internal sense: no more
    *non-excluded* slot to handle) while the grid stays incomplete — this
    isn't a genuine success for the caller. `truly_complete` (below) makes
    the distinction: only a grid entirely filled, exclusions included,
    counts as a real success; otherwise, the diagnostics are filled in
    just like for any other failure (see generate_grid, which needs an
    up-to-date `assignment`/`impossible_slots` to decide whether a slot
    still remains to add a word to, or whether cleanup is needed). Without
    `excluded_slots` (every pre-existing caller's case), `truly_complete`
    coincides exactly with the internal `solved` — no behavior change for
    them.

    `diagnostics`, if given a dict, is filled in with data useful to
    understand *why* a fill attempt failed (see generate_grid's
    "pattern_failed" logging): `slot_count`/`length_counts` (the CSP's
    shape, independent of the word list), `checks`/`reason` (how far
    the search got — "search_exhausted" means every candidate was tried
    within budget and none worked, a genuine dead end for this pattern;
    "deadline_exceeded" means the `deadline_checks` budget ran out first,
    inconclusive; "abandoned_too_unfillable" means the search itself gave
    up early, well before either of the above, because more than
    `UNFILLABLE_ABANDON_FRACTION` (30%) of the grid's white cells already
    belonged to a slot deemed impossible (see `Filler.abandoned`) — at
    that point continuing to search elsewhere on the same pattern isn't
    worth the remaining budget; "interrupted_other_attempt_done" means this
    attempt was cut short because another attempt of the same palier
    already produced the palier's outcome (success or failure) — see
    `attempt_done_event`/`generate_grid`, at the user's explicit request to
    stop waiting for every parallel attempt once one has already answered;
    "no_slots" means the pattern had no white run >= 3 cells at all), and,
    on failure only, `example_grid` — a snapshot
    (`build_partial_letters_grid`) of the most-filled-in state the search
    ever reached before giving up, at the user's explicit request, so a
    failed attempt can be shown to the user (not just logged) instead of
    disappearing with no visible trace of what was tried — and
    `impossible_cells` (`Filler.impossible_zone_cells()`), the cells of
    whichever unassigned slot(s), at that same snapshot, had no candidate
    word left at all, for the UI to highlight (may be empty — see that
    method's own docstring for why).

    `forced_letters`, if given (see `sample_letter_biases`), seeds the
    search with a statistically-guessed letter for a subset of cells,
    treated by `Filler` as a soft hint rather than a real assignment (see
    `Filler._domain`) — also overlaid onto `example_grid` on failure (cells
    no real assignment already covers), with their own coordinates listed
    separately in `forced_cells`, at the user's explicit request, so the UI
    can show *which* letters in the preview are statistical hints rather
    than real progress from the search.

    On failure, `diagnostics` also carries the raw `assignment` (`Filler.
    best_assignment`, one word-or-None per slot) and `impossible_slots`
    (`Filler.impossible_zone_slots()`, slot *indices* rather than cells) —
    at the user's explicit request, for `generate_grid`'s cross-palier
    retry-seed mechanism (`_build_retry_seed`) to work from the real
    slot/word structure directly rather than re-deriving it from the
    letter-grid shown in the UI (which also overlays purely statistical
    `forced_letters` hints, indistinguishable there from a real placed
    letter).

    `deadline_checks` (`None` by default) is computed from the grid's own
    size, at the user's explicit request: `width × height × 2000` (raised
    from × 100 then × 300, each time at the user's explicit request),
    rather than a fixed budget (200,000, unrelated to the actual size of
    the grid being searched — far too generous for a very small grid,
    potentially insufficient for a very large one). `None` rather than a
    value computed directly in the function's signature: `rows`/`cols`
    are only known once the function is called, a default value can't
    depend on another parameter in Python. `minimize_black_squares`
    (step 3, once the grid is already filled) keeps its own, much smaller
    budget (`deadline_checks=6_000`), explicitly passed to every one of
    its own calls to `try_fill` — this formula therefore only applies to a
    caller that never supplied its own budget, never to that case.

    `best_state_queue` (`None` by default — no effect for any pre-existing
    caller), at the user's explicit request: when given, a callback is set
    on the `Filler` built here (`Filler.on_new_best`) to publish, in real
    time, every new `best_assignment` record reached during the search —
    not just the final state returned by this function once `filler.
    solve()` has returned. The callback rebuilds the full preview
    (`example_grid`/`impossible_cells`/`forced_cells`/`locked_cells`)
    exactly the way this same `try_fill` does on failure further below,
    from this new `best_assignment` — same functions, same result shape —
    then publishes it on `best_state_queue` (see `_worker_best_state_
    queue`/`generate_grid` for what happens to it next). A defensively
    copied `grid` (`[row[:] for row in grid]`) comes with every
    publication: `grid` itself never changes after `make_pattern` (see its
    own docstring), but each publication must stay an independent snapshot
    rather than a shared reference, to stay consistent once deserialized
    on the parent side, where it will live longer than this call to
    `try_fill`.

    `attempt_id` (`None` by default — no effect for any pre-existing
    caller), at the user's explicit request: "only one best grid is kept
    per process." An opaque identifier (this specific attempt's own seed,
    see `_pattern_attempt`/`_pattern_continue`) copied verbatim, with no
    processing at all, both into every state published on `best_state_
    queue` above and into `diagnostics["attempt_id"]` on failure — so
    `generate_grid` can recognize, among all the states a single palier
    reports back to it (the final result AND every state published along
    the way), which ones come from the SAME parallel attempt, in order to
    keep only one (the best) per attempt in the on-screen preview (see
    `generate_grid`)."""
    if deadline_checks is None:
        deadline_checks = rows * cols * 2000
    slots = extract_slots(grid, rows, cols)
    if diagnostics is not None:
        diagnostics["slot_count"] = len(slots)
        diagnostics["length_counts"] = dict(sorted(Counter(len(s) for s in slots).items()))
    if not slots:
        if diagnostics is not None:
            diagnostics["checks"] = 0
            diagnostics["reason"] = "no_slots"
            diagnostics["example_grid"] = grid
            diagnostics["impossible_cells"] = []
            diagnostics["forced_cells"] = []
            diagnostics["assigned_letter_count"] = 0
            diagnostics["assignment"] = []
            diagnostics["impossible_slots"] = []
            diagnostics["locked_cells"] = []
        return None
    # Cells already locked *even before* this search starts (see
    # `preseed_assignment` above) — at the user's explicit request, so the
    # web preview can visually tell them apart from `forced_cells`'s own
    # statistical letters (sample_letter_biases): a locked cell carries a
    # real letter, confirmed by a previous palier's own search, not a mere
    # guess. Computed once here, before `solve()` runs, since neither
    # `preseed_assignment` nor `locked_letters` change during this search
    # (a slot already assigned in `preseed_assignment` is never
    # reconsidered — see `Filler._backtrack`, which only ever retains
    # slots still at `None` —, and `locked_letters` itself is never
    # modified after this point).
    #
    # Fixed after a direct user report, backed by two screenshots: "there
    # are cases where the word-generation process doesn't preserve locked
    # cells" — the preview shown right after black cells were placed
    # (before the search) showed many cells outlined as locked, but the
    # preview shown after the search failed only showed a handful left.
    # The cause wasn't a genuine loss of constraint: `locked_letters`
    # (once merged into `forced_letters` by the caller — see `_pattern_
    # attempt`/`_pattern_continue`) is indeed still applied as a hard
    # constraint by `Filler._domain` on every slot touching one of its
    # cells, in both directions, so the letter itself never changed. The
    # bug was purely in this `locked_cells` diagnostic: before this fix,
    # it only ever listed the cells of a slot *entirely* covered by
    # `locked_letters` (so already promoted to a real word in `preseed_
    # assignment`) — a locked cell belonging to a slot only *partially*
    # covered (the rest of its letters still to be discovered by the
    # search) never appeared in `locked_cells`, even though it's just as
    # locked and constrained as the others. `locked_letters`, when given,
    # is therefore now this diagnostic's primary source — the same
    # complete cell list already shown by `_cycle_start_preview` before
    # the search (see generate_grid) — rather than `preseed_assignment`
    # alone, which remains a plain fallback for a caller that would supply
    # only that (no real case today: `_pattern_attempt`/`_pattern_
    # continue` always supply both together).
    all_slot_cells = {cell for s in slots for cell in s}
    if locked_letters:
        locked_cells = sorted(cell for cell in locked_letters if cell in all_slot_cells)
    elif preseed_assignment is not None:
        locked_cells = sorted({cell for i, word in enumerate(preseed_assignment) if word is not None
                                for cell in slots[i]})
    else:
        locked_cells = []
    filler = Filler(slots, index, rng, forced_letters=forced_letters, letter_scores=letter_scores,
                     excluded_slots=excluded_slots, cancel_event=cancel_event,
                     batch_abandoned_event=batch_abandoned_event,
                     attempt_done_event=attempt_done_event, locked_letters=locked_letters,
                     priority_words=priority_words)
    if best_state_queue is not None:
        # Assigned after construction, not passed to Filler(...) directly
        # above: the closure below needs `filler` itself (to read filler.
        # impossible_zone_cells(), which depends on the Filler's full
        # state, not just the best_assignment received as an argument) —
        # `filler` doesn't exist yet at the point the call to Filler(...)
        # is built, but already exists by the time this callback is
        # actually invoked (from _backtrack, well after).
        def _publish_new_best(best_assignment):
            example_grid, forced_cells, _ = build_partial_letters_grid(
                grid, slots, best_assignment, forced_letters, locked_letters
            )
            # `impossible_slots` (not just `impossible_cells`) is essential
            # here: on the parent side, a state published through this
            # queue can end up selected as `failed_pairs[0]`/among the
            # cleanup candidates (`_build_retry_seed`/`_clean_all_
            # candidates`), both of which read `cand_diag["impossible_
            # slots"]` directly — omitting it would crash this path the
            # moment a state published here wins the selection. `checks`/
            # `reason` are included purely for shape consistency with the
            # final diagnostic produced further below (useful for
            # backend.log if this state wins `last_diag`) but are read
            # nowhere on the parent side for this intermediate state —
            # `reason` carries a dedicated value (`"best_state_snapshot"`),
            # distinct from every value produced at the end of a search,
            # so a state published along the way can be unambiguously told
            # apart, in the logs, from an attempt's own final result.
            best_state_queue.put({
                "grid": [row[:] for row in grid],
                "assignment": list(best_assignment),
                "example_grid": example_grid,
                "impossible_cells": filler.impossible_zone_cells(),
                "impossible_slots": filler.impossible_zone_slots(),
                "forced_cells": forced_cells,
                "locked_cells": locked_cells,
                "theme_cells": _theme_word_cells(slots, best_assignment, priority_words),
                "checks": filler.checks,
                "reason": "best_state_snapshot",
                # `attempt_id` is what lets `generate_grid` later recover
                # the "lineage" number (see `_build_dispatch_lineage`/
                # `seed_to_lineage`) of the task that published this state
                # — no PID needed here anymore, the translation happens
                # purely through this seed.
                "attempt_id": attempt_id,
            })
        filler.on_new_best = _publish_new_best
    if preseed_assignment is not None:
        filler.assignment = list(preseed_assignment)
        filler.used_words = {w for w in preseed_assignment if w is not None}
        filler.best_assignment = list(preseed_assignment)
        filler.best_assigned_count = sum(1 for w in preseed_assignment if w is not None)
    filler.exclude_immediately_impossible_slots()
    solved_internally = filler.solve(deadline_checks)
    # Closes every slot already entirely determined by real crossing words
    # but never explicitly confirmed by `_backtrack` itself — see `_close_
    # implied_slots`'s own docstring for the real bug this fixes. Operates
    # on `filler.best_assignment` (the highest level of progress ever
    # reached, not `filler.assignment`'s current state, potentially
    # already partially "unwound" by backtracking if the search ended in
    # failure) — this is also the state, never `filler.assignment`
    # directly, that the rest of this file (diagnostics, `_build_retry_
    # seed`, the displayed preview) already reads further below.
    _close_implied_slots(slots, index, filler.best_assignment, filler.used_words, filler.excluded_slots)
    # `filler.assignment` resynchronized from `best_assignment` once this
    # closure has been applied: on a native success (`solved_internally`
    # with no excluded slot), the two already coincided, so this line
    # changes nothing; for any other outcome, it's `best_assignment` —
    # never reduced by backtracking, only ever increased here — that
    # reflects the real state to consider when deciding whether this
    # attempt is genuinely complete.
    filler.assignment = list(filler.best_assignment)
    # See the docstring above: with `excluded_slots` non-empty, `solved_
    # internally` (_backtrack's own internal sense — no more *non-
    # excluded* slot to handle) isn't enough to guarantee a complete grid.
    # Without `excluded_slots` (every pre-existing caller), the two always
    # coincide exactly.
    truly_complete = all(w is not None for w in filler.assignment)
    # Final "proper noun quota" safety net (see MAX_PROPER_NOUNS/this
    # function's own docstring): a grid otherwise complete but containing
    # too many words present in `proper_noun_words` is NOT accepted as a
    # genuine success — `truly_complete` flips to `False`, which makes
    # this function return `None` further below exactly like any other
    # fill failure, with no separate recovery code to write: the cross-
    # palier retry mechanism already in place (generate_grid) already
    # retries normally on any `None`.
    over_proper_noun_budget = False
    if truly_complete and max_proper_nouns is not None and proper_noun_words:
        proper_noun_count = sum(1 for w in filler.assignment if w in proper_noun_words)
        over_proper_noun_budget = proper_noun_count > max_proper_nouns
        if over_proper_noun_budget:
            truly_complete = False
    # Same safety net for words absent from the definition dictionary
    # (see MAX_NON_GLOSS_WORDS / this function's own docstring).
    over_non_gloss_budget = False
    if truly_complete and max_non_gloss is not None and non_gloss_words:
        non_gloss_count = sum(1 for w in filler.assignment if w in non_gloss_words)
        over_non_gloss_budget = non_gloss_count > max_non_gloss
        if over_non_gloss_budget:
            truly_complete = False
    if diagnostics is not None:
        diagnostics["checks"] = filler.checks
        diagnostics["reason"] = (
            "too_many_proper_nouns" if over_proper_noun_budget
            else "too_many_non_gloss_words" if over_non_gloss_budget
            else "solved" if truly_complete
            else "interrupted_other_attempt_done" if filler.interrupted_by_sibling
            else "abandoned_too_unfillable" if filler.abandoned
            else "deadline_exceeded" if filler.checks >= deadline_checks
            else "blocked_on_excluded_slot" if solved_internally
            else "search_exhausted"
        )
        if not truly_complete:
            example_grid, forced_cells, assigned_letter_count = build_partial_letters_grid(
                grid, slots, filler.best_assignment, forced_letters, locked_letters
            )
            diagnostics["example_grid"] = example_grid
            diagnostics["forced_cells"] = forced_cells
            diagnostics["impossible_cells"] = filler.impossible_zone_cells()
            diagnostics["assigned_letter_count"] = assigned_letter_count
            diagnostics["assignment"] = list(filler.best_assignment)
            diagnostics["impossible_slots"] = filler.impossible_zone_slots()
            diagnostics["locked_cells"] = locked_cells
            diagnostics["theme_cells"] = _theme_word_cells(
                slots, filler.best_assignment, priority_words
            )
            diagnostics["attempt_id"] = attempt_id
    if truly_complete:
        return slots, filler.assignment
    return None


# ---------- Minimisation locale des cases noires ----------

def minimize_black_squares(grid, result, rows, cols, index, rng, deadline_checks=6_000,
                            cancel_event=None, proper_noun_words=None, max_proper_nouns=None,
                            non_gloss_words=None, max_non_gloss=None, priority_words=None,
                            permanent_locked_letters=None, permanent_black_cells=None):
    """`permanent_black_cells` (`None`/empty by default — no effect for
    any pre-existing caller): a set of `(row, col)` cells never considered
    as candidates for removal, at the user's explicit request for the
    "Finir la zone" button (see backend/app.py's `interactive_finish` —
    `zone_cells`): "Finish the zone... locking every slot that isn't part
    of the selection." Every white cell outside the chosen zone is
    converted into a permanent black cell by the caller (in the
    `seed_grid` passed via `resume_state`) even before the multi-palier
    search starts; this black cell then stays protected throughout THIS
    ENTIRE search by `_build_retry_seed`'s own already-established
    invariant ("a cell already black in the original `seed_grid` stays so
    forever, across every palier") — the one place in the whole pipeline
    where this same invariant does NOT already apply is precisely this
    function, the only one to remove black cells once the grid is
    entirely solved. Without this parameter, this final optimization
    could legitimately remove one of these "frozen" cells if the removal
    stays valid and re-fillable — reopening a cell that "Finir la zone"
    specifically meant to never touch.

    Iteratively removes black cells one at a time (independently, without
    pairing them with a mirror cell — consistent with make_pattern, which
    no longer places black cells in symmetric pairs either) as long as the
    grid stays fillable, keeping the last known solution (this avoids a
    final new try_fill that could fail on a difficult search even though a
    solution was just found).

    Calls `is_structurally_valid` with `min_interior_free=1` rather than
    the default (3): this function only ever REMOVES black cells (never
    adds any), which can only lengthen existing slots, never create a
    new, shorter one — the invariant it genuinely needs to preserve is
    connectivity and the absence of an orphaned cell, not `make_pattern`'s
    own aesthetic preference for
    slots of at least 3 cells in `make_pattern`. Necessary since `_place_
    black_cells` can legitimately leave an interior slot of 1 or 2 cells
    when that's the only way to avoid adjacency to another black cell (see
    make_pattern): without this change, a grid produced this way would
    violate `is_structurally_valid`'s own default right at the very first
    call here, regardless of which cell is actually being removed,
    blocking any minimization at all.

    `cancel_event` (see GenerationCancelled) is checked between two
    candidate black cells — this phase is normally fast (each trial is
    bounded by `deadline_checks`, much smaller than the main search), but
    a large grid can have many black cells to try, so this checkpoint
    remains useful rather than waiting for the whole loop to finish.

    Also validates, at the user's explicit request ("this optimization
    must only accept a change if every word is valid"), that a successful
    `try_fill`'s own result contains ONLY words genuinely present in
    `index` (at their exact length) before accepting this black cell's
    removal: `try_fill`/`Filler` should never, in principle, produce a
    word absent from the dictionary, but a residual, rare case already
    documented elsewhere in this file (`_shorten_impossible_zones`: a slot
    can end up entirely completed by its own crossings alone without ever
    being validated itself) shows this isn't an absolute guarantee.
    Reported live by the user: an optimized grid containing "UNT", absent
    from the French dictionary. This safety net is cheap (a handful of
    set lookups per accepted removal — `word_sets` is built once, never
    reconverted on every trial — and never inside `Filler`'s own search
    loop) and eliminates this risk precisely where an "optimized" grid is
    about to be accepted as the new reference state; a candidate rejected
    here is treated exactly like a `try_fill` failure — the removed black
    cell is restored, no other cell is retried in its place during this
    same pass.

    `proper_noun_words`/`max_proper_nouns` (both `None` by default — no
    effect for any pre-existing caller), at the user's explicit request
    (see MAX_PROPER_NOUNS): passed through as-is to every internal call
    to `try_fill`, which already refuses on its own (see its own safety
    net) a solution exceeding the quota — no extra check to write here, a
    `try_fill` returning `None` for this reason is already treated
    exactly like any other fill failure by the loop below (black cell
    restored, no other cell retried in its place during this same pass).

    `index` is a DualIndex (see its own docstring): `word_sets` is
    therefore also built per direction (`word_sets.for_direction("across"/
    "down")[length]`) rather than a single `{length: set}` dict — on a
    bilingual grid, a word valid on the across side (language A) has no
    reason to exist in the down dictionary (language B), and vice versa,
    so the validation below must check every word against ITS OWN
    direction's dictionary, never the other. Same dictionary on both sides
    (so the same behavior as before this feature existed) on a
    monolingual grid.

    `permanent_locked_letters` (`None` by default — no effect for any
    pre-existing caller before "Finir la grille", see `generate_grid`'s
    own docstring): a word entirely covered by these cells is always
    accepted by the validation below, whatever its actual content — without
    this exception, a single word placed by the player themselves in
    Interactive mode and absent from the dictionary (likely a proper
    noun) would prevent this optimization from removing even a single
    black cell anywhere in the grid, since this validation covers EVERY
    word of the grid, not only the ones affected by the removed black
    cell."""
    word_sets = DualSet(
        across={length: set(data["words"]) for length, data in index.across.items()},
        down={length: set(data["words"]) for length, data in index.down.items()},
    )
    slots, assignment = result
    improved = True
    while improved:
        improved = False
        black_cells = [(r, c) for r in range(rows) for c in range(cols) if grid[r][c] == BLACK]
        rng.shuffle(black_cells)
        for (r, c) in black_cells:
            if cancel_event is not None and cancel_event.is_set():
                raise GenerationCancelled()
            if grid[r][c] != BLACK:
                continue
            if permanent_black_cells and (r, c) in permanent_black_cells:
                continue
            saved = grid[r][c]
            grid[r][c] = WHITE
            if is_structurally_valid(grid, rows, cols, min_interior_free=1):
                # `permanent_locked_letters` (see the docstring above) must
                # also constrain the search itself, not just the after-the-
                # fact validation — otherwise this step, which relaunches an
                # entirely fresh `try_fill` (with no pre-fill at all) for
                # every candidate black cell, would be free to replace the
                # word placed by the player themselves in Interactive mode
                # with a completely different, if genuinely real, word.
                # `locked_letters` provides the hard constraint (`Filler.
                # locked_letters`, consulted for every cell it covers,
                # including a slot only partially covered by it);
                # `preseed_assignment` additionally promotes, verbatim and
                # without ever revalidating it against the dictionary,
                # every slot ENTIRELY covered by these cells — recomputed
                # here on `grid`, the pattern this same `try_fill` will
                # itself re-query via `extract_slots`, to stay aligned with
                # the structure it will actually use.
                permanent_preseed = None
                if permanent_locked_letters:
                    trial_slots = extract_slots(grid, rows, cols)
                    permanent_preseed = [
                        "".join(permanent_locked_letters[cell] for cell in cells)
                        if all(cell in permanent_locked_letters for cell in cells) else None
                        for cells in trial_slots
                    ]
                new_result = try_fill(grid, rows, cols, index, rng, deadline_checks,
                                       cancel_event=cancel_event,
                                       proper_noun_words=proper_noun_words,
                                       max_proper_nouns=max_proper_nouns,
                                       non_gloss_words=non_gloss_words,
                                       max_non_gloss=max_non_gloss,
                                       priority_words=priority_words,
                                       locked_letters=permanent_locked_letters or None,
                                       preseed_assignment=permanent_preseed)
                if new_result is not None:
                    new_slots, new_assignment = new_result
                    if all(
                        w is not None and (
                            w in word_sets.for_cells(new_slots[i]).get(len(w), ())
                            or (permanent_locked_letters and all(
                                cell in permanent_locked_letters for cell in new_slots[i]
                            ))
                        )
                        for i, w in enumerate(new_assignment)
                    ):
                        slots, assignment = new_slots, new_assignment
                        improved = True
                        continue
            grid[r][c] = saved
    return grid, slots, assignment


# ---------- Word numbering (for definitions) ----------

def build_word_entries(grid, rows, cols, slots, assignment):
    """Numbers the starting cells per the standard crossword convention
    (reading left->right then top->bottom, one number per starting cell,
    shared between the across and/or down word starting there). Returns a
    list of {number, direction, row, col, length, answer}."""
    starts = defaultdict(list)
    for i, cells in enumerate(slots):
        cell = cells[0]
        direction = "across" if len(cells) > 1 and cells[1][0] == cell[0] else "down"
        starts[cell].append((direction, i))

    numbers = {}
    counter = 1
    for r in range(rows):
        for c in range(cols):
            if (r, c) in starts:
                numbers[(r, c)] = counter
                counter += 1

    entries = []
    for cell, items in starts.items():
        number = numbers[cell]
        for direction, i in items:
            entries.append({
                "number": number,
                "direction": direction,
                "row": cell[0],
                "col": cell[1],
                "length": len(slots[i]),
                "answer": assignment[i],
            })
    entries.sort(key=lambda e: (e["number"], e["direction"]))
    return entries


def _interactive_fill_diagnostics(grid, rows, cols, index):
    """For grid `grid` (same cell conventions as `interactive_place_
    word`), returns `(impossible_cells, low_candidate_cells)` — two sorted
    `[r, c]` lists, to display on the interface in "Interactif" mode just
    like on the previews: red for a still-open slot no dictionary word
    fits anymore (given the letters already placed and the words already
    used elsewhere), orange for a slot with strictly fewer than
    `PREFILL_MIN_WORD_COUNT` options left. A slot already entirely filled
    IS now also checked — not for its option count (it no longer needs
    one, its cells are already fixed), but for the actual validity of the
    word it holds (`_invalid_fully_known_indices`): an invented word can
    form there without ever having been explicitly chosen by anyone,
    simply reconstructed as-is from individually correct crossings never
    verified together (the same bug class as AVALAS/UNT/AMN, see
    CLAUDE.md) — in particular once a slot initially flagged impossible
    (still partial) ends up entirely completed by a later placement:
    without this check, it purely and simply vanished from every report,
    even though nothing ever guaranteed the word thus completed was real.
    Fixed at the user's explicit request, after a live report: one zone
    shown impossible for a few iterations then "disappeared" without ever
    being genuinely fixed, and another never shown impossible at all —
    both cases correspond to an entirely known slot the old version never
    checked. No backtracking: builds a `Filler` purely as a domain-
    computation helper, like `interactive_place_word`."""
    pattern = [["#" if ch == BLACK else "." for ch in row] for row in grid]
    slots = extract_slots(pattern, rows, cols)
    if not slots:
        return [], []
    known = {
        (r, c): grid[r][c]
        for r in range(rows)
        for c in range(cols)
        if grid[r][c] not in (BLACK, WHITE)
    }
    # `letter_scores` only ever influences the candidates' order (never
    # their count), and this diagnostic only counts domain sizes — a
    # throwaway RNG is enough, no need to touch the session's own `rng`.
    scratch_rng = random.Random(0)
    _, letter_scores = sample_letter_biases(
        pattern, rows, cols, index, scratch_rng, force_fraction=0.0, known_letters=known,
    )
    filler = Filler(
        slots, index, scratch_rng,
        letter_scores=letter_scores, locked_letters=known,
    )
    for i, cells in enumerate(slots):
        if all(cell in known for cell in cells):
            filler.assignment[i] = "".join(known[cell] for cell in cells)
            filler.used_words.add(filler.assignment[i])

    impossible, low = set(), set()
    for i, cells in enumerate(slots):
        if filler.assignment[i] is not None:
            continue
        n = len(set(filler._domain(i)) - filler.used_words)
        if n == 0:
            impossible.update(cells)
        elif n < PREFILL_MIN_WORD_COUNT:
            low.update(cells)
    for i in _invalid_fully_known_indices(slots, index, known):
        impossible.update(slots[i])
    return (
        sorted([r, c] for (r, c) in impossible),
        sorted([r, c] for (r, c) in low),
    )


def interactive_place_word(grid, rows, cols, index, rng, priority_words=None):
    """Place EXACTLY ONE additional word into `grid`, honouring every letter
    already present, with NO backtracking — the single-step primitive behind
    the web UI's "Interactif" authoring mode (see backend/app.py's
    `_run_interactive_job` / `POST /api/interactive/step`).

    `grid` is a 2D list where each cell is `"#"` (black), `"."` (empty
    white) or an uppercase letter (filled white). `extract_slots` already
    treats any non-`"#"` cell as white, so a grid carrying letters feeds
    straight into it. `index` is a DualIndex; `priority_words` a
    frozenset/DualSet (theme glossary) or `None`.

    Returns `{"impossible": True}` when no still-open slot has any viable
    candidate word, otherwise
    `{"impossible": False, "grid": <new 2D list, one word written in>,
      "placed": {"cells": [[r, c], ...], "word": "MOT",
                 "direction": "across"|"down"}}`.

    Builds a `Filler` purely as a domain/scoring helper — it never calls
    `Filler.solve` / `_backtrack` / `try_fill`, so none of the fragile CSP
    search machinery is exercised.
    """
    # `extract_slots`/`sample_letter_biases` only treat a cell literally
    # equal to WHITE (".") as white — a cell carrying a letter would break
    # a run — so work from a plain black/white pattern derived from `grid`,
    # with the letters tracked separately as `known`.
    pattern = [["#" if ch == BLACK else "." for ch in row] for row in grid]
    slots = extract_slots(pattern, rows, cols)
    if not slots:
        return {"impossible": True}
    known = {
        (r, c): grid[r][c]
        for r in range(rows)
        for c in range(cols)
        if grid[r][c] not in (BLACK, WHITE)
    }
    _, letter_scores = sample_letter_biases(
        pattern, rows, cols, index, rng, force_fraction=0.0, known_letters=known,
    )
    filler = Filler(
        slots, index, rng,
        letter_scores=letter_scores, locked_letters=known,
        priority_words=priority_words,
    )
    # Pre-assign every slot already entirely filled: it can neither be
    # re-selected nor counted as a new placement, and its word blocks a
    # duplicate elsewhere (`used_words`).
    for i, cells in enumerate(slots):
        if all(cell in known for cell in cells):
            word = "".join(known[cell] for cell in cells)
            filler.assignment[i] = word
            filler.used_words.add(word)

    # `domains` keeps the RAW domain (like in _backtrack, never filtered by
    # used_words) — that's what _select_target_slot expects, in
    # particular for its own "fewer than PREFILL_MIN_WORD_COUNT
    # candidates" threshold (level 2), which genuinely compares the raw
    # domain's size. `viable` remains the filtered version (genuinely
    # available candidates) the word draw, right after, uses.
    domains, viable = {}, {}
    for i, cells in enumerate(slots):
        if filler.assignment[i] is not None:
            continue
        domain = set(filler._domain(i))
        cands = domain - filler.used_words
        if cands:
            domains[i] = domain
            viable[i] = cands
    if not viable:
        imp, low = _interactive_fill_diagnostics(grid, rows, cols, index)
        return {"impossible": True, "impossible_cells": imp, "low_candidate_cells": low}

    # Target slot: the same 7-level cascade as automatic generation (see
    # Filler._select_target_slot), reused as-is rather than a hand-rolled,
    # simple MRV — at the user's explicit request, after confirming live
    # that this MRV (smallest domain first) made Interactive mode start
    # with 2-letter slots scattered across the grid, honoring neither the
    # length threshold (level 2, >=4 letters) nor the top-left front
    # sought by automatic generation's own geometric score (level 5).
    target = filler._select_target_slot(list(viable.keys()), domains)
    cells = slots[target]
    cands = viable[target]

    # Word: theme-glossary members first, if applicable, then the rest;
    # ranked by statistical score, then by lexicon frequency.
    themed = _priority_words_for(filler.priority_words, cells) & cands
    pool = themed or cands
    freq = index.for_cells(cells).get(len(cells), {}).get("freq", {})
    word = max(pool, key=lambda w: (filler._candidate_score(target, w), freq.get(w, 0.0)))

    new_grid = [row[:] for row in grid]
    for (r, c), ch in zip(cells, word):
        new_grid[r][c] = ch
    # Diagnostics computed on the grid AFTER placement — that's the state
    # the player sees after "Suivant".
    imp, low = _interactive_fill_diagnostics(new_grid, rows, cols, index)
    return {
        "impossible": False,
        "grid": new_grid,
        "impossible_cells": imp,
        "low_candidate_cells": low,
        "placed": {
            "cells": [[r, c] for (r, c) in cells],
            "word": word,
            "direction": slot_direction(cells),
            # `from_theme`: does the automatically placed word come from
            # the applicable theme glossary? Shown in magenta on the
            # interface ("Interactif"), at the user's explicit request.
            "from_theme": word in themed,
        },
    }


# Cap on how many candidate words `interactive_slot_candidates` ever
# returns for a single slot, at the user's explicit request scope (the
# "Mots" button in "Interactif" mode) — a short/common slot length can
# easily have thousands of real dictionary matches, which would be
# unusable as a single comma-separated line under the definition field.
# Mirrors `backend/dictionary_lookup.py`'s own `MAX_ROWS` (300) for the
# same "never flood a UI list" reason, applied to the OTHER (non-theme)
# words only — every theme-glossary match is always kept in full, since
# that list is normally small and is exactly what the user asked to see
# first/highlighted.
INTERACTIVE_SLOT_CANDIDATES_LIMIT = 300


def interactive_slot_candidates(grid, rows, cols, index, cells, priority_words=None):
    """"Mots" button (mode "Interactif"), at the user's explicit request:
    "add a Mots button listing the possible words for the selected slot...
    First, the theme-glossary words if there are any... then the other
    words... When the player clicks a word, it gets placed on the
    selected slot." `cells` is the ordered `(row, col)` list of the slot
    selected on the interface (computed by `selectedInteractiveWord()`,
    script.js) — never recomputed here from `extract_slots`, to stay
    correct even while the slot is still partially empty (a not-yet-
    complete slot has no stable slot-list index anyway).

    Returns `(theme_words, other_words)`, two sorted lists of real
    dictionary words compatible with the letters already placed on
    `cells` (the same per-position intersection as `_slot_candidates`,
    reused as-is) — `theme_words`: the candidates belonging to the theme
    glossary applicable to `cells`'s own direction (`_priority_words_for`,
    like `interactive_place_word`), always complete, never truncated;
    `other_words`: the rest, capped at `INTERACTIVE_SLOT_CANDIDATES_
    LIMIT`. A word already used elsewhere in the grid (another slot,
    entirely filled, already carrying this word) is excluded from both
    lists — like `interactive_place_word`, to never offer a duplicate."""
    pattern = [["#" if ch == BLACK else "." for ch in row] for row in grid]
    slots = extract_slots(pattern, rows, cols)
    known = {
        (r, c): grid[r][c]
        for r in range(rows)
        for c in range(cols)
        if grid[r][c] not in (BLACK, WHITE)
    }
    own_cells = set(cells)
    used_words = {
        "".join(known[cell] for cell in other_cells)
        for other_cells in slots
        if set(other_cells) != own_cells and all(cell in known for cell in other_cells)
    }
    candidates = set(_slot_candidates(index, len(cells), cells, known)) - used_words
    themed = _priority_words_for(priority_words, cells) & candidates
    other = candidates - themed
    theme_words = sorted(themed)
    other_words = sorted(other)[:INTERACTIVE_SLOT_CANDIDATES_LIMIT]
    return theme_words, other_words


def _interactive_white_run_at(grid, rows, cols, cell, axis):
    """The maximal white run (list of (row, col)) running through `cell`
    along `axis` ("across" or "down") — the same convention as
    extract_slots, but computed for a single cell instead of the whole
    grid (like `selectedInteractiveWord()` on the client side). `()` if
    `cell` is black, or if this run is shorter than 2 cells (no genuine
    slot in this direction at this cell)."""
    r, c = cell
    if grid[r][c] == BLACK:
        return ()
    if axis == "across":
        start = c
        while start > 0 and grid[r][start - 1] != BLACK:
            start -= 1
        end = c
        while end < cols - 1 and grid[r][end + 1] != BLACK:
            end += 1
        cells = [(r, cc) for cc in range(start, end + 1)]
    else:
        start = r
        while start > 0 and grid[start - 1][c] != BLACK:
            start -= 1
        end = r
        while end < rows - 1 and grid[end + 1][c] != BLACK:
            end += 1
        cells = [(rr, c) for rr in range(start, end + 1)]
    return tuple(cells) if len(cells) >= 2 else ()


def interactive_crossing_words(grid, rows, cols, index, cell):
    """"Croisés" button (mode "Interactif"), to the right of "Mots", at
    the user's explicit request: "identify the letters compatible with a
    word in each direction (can be restricted by the letters in place)...
    for every letter compatible with a word in each direction, list the
    across words..., and the down words... highlight the shared letter in
    blue."

    Unlike `interactive_slot_candidates` (a single, already-known slot,
    `cells` supplied by the caller), this function starts from a SINGLE
    cell (`cell`, a `(row, col)` tuple) and computes both slots itself —
    across and down — that run through it (`_interactive_white_run_at`).

    Returns `(across_start, down_start, letters)`:
    - `across_start`/`down_start`: the first cell (`(row, col)`) of each
      slot, or `None` if that direction has no genuine slot at `cell`
      (white run < 2 cells) — in that case `letters` is always empty, a
      cell with no slot in one direction by definition has no letter
      "compatible with a word in each direction".
    - `letters`: a list sorted by letter of `{"letter", "across_words",
      "down_words"}` — a letter only appears if it's `cell`'s own letter
      in AT LEAST one across candidate word AND AT LEAST one down
      candidate word (the same per-position intersection as `_slot_
      candidates`, already reused by `_slot_candidates` itself);
      `across_words`/`down_words` are the real candidate words (sorted,
      each capped at `INTERACTIVE_SLOT_CANDIDATES_LIMIT`) carrying this
      letter at this position. A word already used elsewhere in the grid
      (another slot, entirely filled, already carrying this word) is
      excluded, like `interactive_slot_candidates`."""
    across_cells = _interactive_white_run_at(grid, rows, cols, cell, "across")
    down_cells = _interactive_white_run_at(grid, rows, cols, cell, "down")
    across_start = list(across_cells[0]) if across_cells else None
    down_start = list(down_cells[0]) if down_cells else None
    if not across_cells or not down_cells:
        return across_start, down_start, []
    pattern = [["#" if ch == BLACK else "." for ch in row] for row in grid]
    slots = extract_slots(pattern, rows, cols)
    known = {
        (r, c): grid[r][c]
        for r in range(rows)
        for c in range(cols)
        if grid[r][c] not in (BLACK, WHITE)
    }
    across_set = set(across_cells)
    down_set = set(down_cells)
    used_words = {
        "".join(known[c2] for c2 in other_cells)
        for other_cells in slots
        if set(other_cells) != across_set and set(other_cells) != down_set
        and all(c2 in known for c2 in other_cells)
    }
    across_words = set(_slot_candidates(index, len(across_cells), across_cells, known)) - used_words
    down_words = set(_slot_candidates(index, len(down_cells), down_cells, known)) - used_words
    pos_across = across_cells.index(cell)
    pos_down = down_cells.index(cell)
    by_letter_across = defaultdict(list)
    for w in across_words:
        by_letter_across[w[pos_across]].append(w)
    by_letter_down = defaultdict(list)
    for w in down_words:
        by_letter_down[w[pos_down]].append(w)
    common_letters = sorted(set(by_letter_across) & set(by_letter_down))
    letters = [
        {
            "letter": letter,
            "across_words": sorted(by_letter_across[letter])[:INTERACTIVE_SLOT_CANDIDATES_LIMIT],
            "down_words": sorted(by_letter_down[letter])[:INTERACTIVE_SLOT_CANDIDATES_LIMIT],
        }
        for letter in common_letters
    ]
    return across_start, down_start, letters


def interactive_clean_impossible_zones(grid, rows, cols, index, rng):
    """"Nettoyer" button (mode "Interactif") — the equivalent, for the
    manual grid, of the "full cleanup" `_build_retry_seed` automatically
    applies at every palier of automatic generation (see CLAUDE.md for its
    full history). At the user's explicit request: "add a 'Nettoyer'
    button that triggers the full cleanup of impossible zones."

    Directly reuses `_clean_blocked_slots` (already proven by automatic
    generation) rather than reimplementing this logic a second time: for
    every slot deemed impossible — in the combined sense of `_impossible_
    indices` (still open, no real candidate left) and `_invalid_fully_
    known_indices` (already entirely filled, but the combination matches
    no real word — see `_interactive_fill_diagnostics`, which now checks
    both same cases for the red highlight) — removes any word crossing
    it, or blackens one of its own cells if word removal alone isn't
    enough (the same 1/10 alternative, and the same "zone strictement
    sans issue" for a length the dictionary doesn't cover at all, as
    automatic generation). The impossible slot itself is also cleared —
    `_clean_blocked_slots` never touches the entry of the slot it's asked
    to clean, only those of the slots crossing it (a no-op for automatic
    generation, where this entry is already `None` — see `_clean_blocked_
    slots`'s own docstring — but not here, where `assignment` is built
    directly from the letters already present in the grid, including for
    the impossible slot itself): without this explicit clearing, its own
    invalid combination stayed visible after "Nettoyer" even though every
    word that justified it had been removed.

    `grid` stays unchanged if no impossible zone is found — OR if none
    could genuinely be resolved (see below). Returns `{"changed": bool,
    "grid": <updated or identical grid>, "cleared_count": <number of
    words removed + cells blackened>}` — `cleared_count` measures what
    GENUINELY changed, not the number of zones merely "considered": an
    impossible slot for which `_clean_blocked_slots` removes no word (no
    still-assigned crossing to remove — see the limitation below) and
    blackens no cell (the 1/10 alternative wasn't drawn, or none of its
    cells stays structurally valid once blackened) counts for nothing — a
    `changed=True` report with a `cleared_count` reflecting no visible
    change would mislead the player.

    Known, unaddressed limitation: a slot fixed only by a letter coming
    from a *partially* filled crossing slot (a letter typed by hand
    without completing the whole word) is never removed here — `_clean_
    blocked_slots` only ever removes already-fully-placed words
    (`assignment[j] is not None`); such a case can only be resolved via
    the black-cell alternative, if it applies to the impossible slot
    itself."""
    pattern = [["#" if ch == BLACK else "." for ch in row] for row in grid]
    slots = extract_slots(pattern, rows, cols)
    if not slots:
        return {"changed": False, "grid": grid, "cleared_count": 0}
    known = {
        (r, c): grid[r][c]
        for r in range(rows)
        for c in range(cols)
        if grid[r][c] not in (BLACK, WHITE)
    }
    # A slot counts as "a placed word" (non-None assignment) the moment
    # every one of its cells is known — whether via a full placement
    # (interactive_place_word) or via crossings — never partially, the
    # same convention `_clean_blocked_slots` expects.
    assignment = [
        "".join(known[cell] for cell in cells) if all(cell in known for cell in cells) else None
        for cells in slots
    ]
    impossible = sorted(
        set(_impossible_indices(slots, index, known))
        | set(_invalid_fully_known_indices(slots, index, known))
    )
    if not impossible:
        return {"changed": False, "grid": grid, "cleared_count": 0}

    cleaned_assignment, _confirmed, new_black_cells = _clean_blocked_slots(
        slots, assignment, impossible, index=index, rng=rng,
        grid=grid, rows=rows, cols=cols,
    )
    # `_clean_blocked_slots` only ever clears a CROSSING slot's own word —
    # never the impossible slot i's own entry (see its own docstring: built
    # for automatic generation, where a slot only ever becomes "fully known
    # but invalid" through crossing letters `Filler` itself never explicitly
    # validated, so assignment[i] is already `None` there — nothing to
    # clear). Here `assignment` was built directly from whatever letters
    # already sit in the grid (`known`), so an impossible slot flagged by
    # `_invalid_fully_known_indices` DOES carry its own non-`None` (but
    # invalid) string in `cleaned_assignment[i]`, left untouched by that
    # loop — it would otherwise survive "Nettoyer" verbatim, at the user's
    # explicit report: "ils retirent les mots qui croisent les emplacements
    # impossibles. Ils doivent aussi retirer les emplacements impossibles
    # eux-mêmes." Cleared explicitly here, and `confirmed` rebuilt from the
    # corrected list rather than reused from `_clean_blocked_slots`'s own
    # (now-stale) return value.
    cleaned_assignment = list(cleaned_assignment)
    for i in impossible:
        cleaned_assignment[i] = None
    confirmed = {}
    for i, word in enumerate(cleaned_assignment):
        if word is None:
            continue
        for cell, ch in zip(slots[i], word):
            confirmed[cell] = ch
    # How many words were genuinely removed (a slot that had a word
    # before and no longer has one after) — the only other possible
    # action of `_clean_blocked_slots` (`new_black_cells`) is already
    # counted separately.
    removed_words = sum(
        1 for before, after in zip(assignment, cleaned_assignment)
        if before is not None and after is None
    )
    cleared_count = removed_words + len(new_black_cells)
    if cleared_count == 0:
        return {"changed": False, "grid": grid, "cleared_count": 0}

    new_grid = [row[:] for row in grid]
    for (r, c) in new_black_cells:
        new_grid[r][c] = BLACK
    # Any cell that used to be known but is no longer covered by any
    # still-assigned slot (its own word, or a crossing word, removed by
    # the cleanup) turns white again — unless it was just blackened right
    # above, in which case it stays black.
    for (r, c) in known:
        if (r, c) not in confirmed and new_grid[r][c] != BLACK:
            new_grid[r][c] = WHITE
    return {"changed": True, "grid": new_grid, "cleared_count": cleared_count}


def interactive_minimize_black_cells(grid, rows, cols, index, rng):
    """"Nettoyer (+noires)" button (mode "Interactif") — the extra half of
    the deep cleanup, at the user's explicit request: "deep cleanup
    (cleaning black cells too, not just impossible slots)."
    `interactive_clean_impossible_zones` already only touches black cells
    in passing, as a side effect of handling one specific impossible slot
    (its own 1/10 alternative, or its "zone strictement sans issue") —
    this function instead directly targets black cells themselves,
    anywhere in the grid, not just the ones tied to an impossible slot.

    Tries to remove, one at a time (shuffled order — the same absence of
    positional bias as everywhere else in this file), every black cell of
    the grid — inspired by `minimize_black_squares` (used by automatic
    generation), but adapted to a manual grid potentially still very
    incomplete: unlike `minimize_black_squares`, which requires a removal
    to leave the grid fully re-fillable (a genuine full second CSP pass),
    this function only requires a weaker but sufficient guarantee here —
    removing a cell must never MAKE THINGS WORSE: the grid must stay
    structurally valid (`is_structurally_valid`, the absolute
    connectivity/orphaned-cell invariant already used everywhere else in
    this file), and the total number of slots deemed impossible (the same
    combined criterion as `_interactive_fill_diagnostics`/`interactive_
    clean_impossible_zones`: `_impossible_indices` ∪ `_invalid_fully_
    known_indices`) must not INCREASE by
    rapport à l'état courant — un retrait qui fusionnerait deux
    emplacements déjà remplis en un nouvel emplacement dont la
    combinaison ne correspond à aucun mot réel est refusé, tout comme un
    retrait qui casserait la connectivité de la grille.

    Retourne `{"changed": bool, "grid": <grille mise à jour ou identique>,
    "removed_count": <nombre de cases noires effectivement retirées>}`."""
    black_cells = [(r, c) for r in range(rows) for c in range(cols) if grid[r][c] == BLACK]
    if not black_cells:
        return {"changed": False, "grid": grid, "removed_count": 0}

    def _impossible_count(g):
        pattern = [["#" if ch == BLACK else "." for ch in row] for row in g]
        slots = extract_slots(pattern, rows, cols)
        if not slots:
            return 0
        known = {
            (r, c): g[r][c]
            for r in range(rows)
            for c in range(cols)
            if g[r][c] not in (BLACK, WHITE)
        }
        return len(
            set(_impossible_indices(slots, index, known))
            | set(_invalid_fully_known_indices(slots, index, known))
        )

    rng.shuffle(black_cells)
    working = [row[:] for row in grid]
    baseline = _impossible_count(working)
    removed_count = 0
    for (r, c) in black_cells:
        working[r][c] = WHITE
        # `is_structurally_valid` only understands a plain black/white
        # PATTERN (BLACK/WHITE cells only) — calling it directly on
        # `working` (which still carries real letters for every
        # already-filled cell) makes its row/col run-length scan misread
        # every letter as an obstacle, exactly like a black cell, so it
        # rejected almost any removal next to already-typed content — a
        # real bug, reported directly by the user: "Nettoyer (+noires) ne
        # supprime pas les cases noires isolées." Fixed by converting to
        # the same pattern-only view `_impossible_count` above already
        # builds for its own purpose, reused here for the structural check
        # too.
        pattern_check = [["#" if ch == BLACK else "." for ch in row] for row in working]
        if not is_structurally_valid(pattern_check, rows, cols, min_interior_free=1):
            working[r][c] = BLACK
            continue
        new_count = _impossible_count(working)
        if new_count > baseline:
            working[r][c] = BLACK
            continue
        baseline = new_count
        removed_count += 1
    if removed_count == 0:
        return {"changed": False, "grid": grid, "removed_count": 0}
    return {"changed": True, "grid": working, "removed_count": removed_count}


# ---------- Display ----------

def print_grid(grid):
    for row in grid:
        print(" ".join(row))


# ---------- Full generation (usable as a library, e.g. an API server) ----------

def build_letters_grid(rows, cols, slots, assignment):
    letters = [[BLACK] * cols for _ in range(rows)]
    for cells, word in zip(slots, assignment):
        for (r, c), ch in zip(cells, word):
            letters[r][c] = ch
    return letters


def build_partial_letters_grid(grid, slots, assignment, forced_letters=None, locked_letters=None):
    """Like build_letters_grid, but for a fill abandoned along the way
    (see try_fill, diagnostics["example_grid"]) — at the user's explicit
    request, to give a preview of what was tried before an attempt failed,
    shown on the interface. Unlike build_letters_grid, `assignment` can
    contain `None` entries (a slot the search never reached): starts from
    the real black/white pattern (`grid`, where every not-yet-determined
    white cell stays WHITE) rather than initializing everything to BLACK
    on the assumption that every slot will be filled.

    `forced_letters` (see sample_letter_biases), if given, is overlaid
    onto the cells no real assignment already covers — at the user's
    explicit request, so a failed attempt's own preview also shows the
    statistical hints, not just the letters genuinely placed by the
    search. A real assignment always wins over the displayed letter (a
    cell can never contradict the hint that constrained it anyway — see
    Filler._domain — but the priority order stays explicit here).

    `locked_letters` (genuinely confirmed content, carried from one
    palier to the next — see Filler.locked_letters), if given, is
    overlaid the same way, before `forced_letters` (so `forced_letters`
    can, in theory, overwrite an already-locked cell without ever
    actually contradicting it in practice — `sample_letter_biases`
    already excludes `known_letters`'s own cells from its own sampling,
    see its docstring, so the two dicts never genuinely overlap). Added
    separately from `forced_letters`, at the user's explicit request,
    after a real bug reported live: `_pattern_attempt`/`_pattern_continue`
    used to merge `locked_letters` INTO `forced_letters` before calling
    `try_fill`, precisely so this function would also show locked letters
    not yet covered by a real assignment — but that same merge then
    contaminated `forced_cells` (see below) with cells that weren't mere
    statistical guesses. This parameter cleanly separates the two needs:
    the cell stays visible in `example_grid` (via this parameter), but
    `forced_cells` now only ever reports a genuine statistical sample.

    Returns (letters_grid, forced_cells, placed_letter_count) — the 2nd
    element is the sorted list of EVERY cell of `forced_letters` (never
    `locked_letters`), whether still visible in the returned grid or
    already covered by a real assignment — at the user's explicit
    request, after confirming that a previous version (returning only
    "still unconfirmed" cells) made the forced-letters display on the
    interface almost vanish as the search progressed, even though the
    statistical sampling itself stayed stable: measured live, up to 7
    forced cells by sample_letter_biases on every attempt, against
    sometimes 0 still "visible" once filtered. See try_fill, diagnostics
    ["forced_cells"], and script.js for the border shown on all these
    cells, including the ones now showing a real letter rather than the
    original hint. The 3rd element (`len(covered)`) counts cells covered
    by a *real* assignment only (never `forced_letters`'s own statistical
    hints nor `locked_letters`'s content) — at the user's explicit
    request, to rank several failed attempts against each other by their
    real progress (see try_fill, diagnostics["assigned_letter_count"], and
    generate_grid)."""
    letters = [row[:] for row in grid]
    covered = set()
    for cells, word in zip(slots, assignment):
        if word is None:
            continue
        for (r, c), ch in zip(cells, word):
            letters[r][c] = ch
            covered.add((r, c))
    if locked_letters:
        for cell, letter in locked_letters.items():
            if cell not in covered:
                r, c = cell
                letters[r][c] = letter
    if forced_letters:
        for cell, letter in forced_letters.items():
            if cell not in covered:
                r, c = cell
                letters[r][c] = letter
    return letters, (sorted(forced_letters) if forced_letters else []), len(covered)


def _theme_word_cells(slots, assignment, priority_words):
    """Cells of every slot whose assigned word belongs to `priority_words`
    (the theme-generation glossary, see `generate_grid`'s `priority_
    words`) — at the user's explicit request: "In the preview grids, show
    words coming from the theme glossary in green letters." Empty list if
    there's no theme at all (`priority_words` empty/`None`) or if no
    assigned word is in it. `assignment` can contain `None` entries
    (slot not reached) — simply ignored. On a bilingual grid `priority_
    words` is a DualSet (one glossary per language): each word is tested
    against the glossary of ITS OWN direction (see `_priority_words_
    for`)."""
    if not priority_words:
        return []
    out = set()
    for cells, word in zip(slots, assignment):
        if word is not None and word in _priority_words_for(priority_words, cells):
            out.update((r, c) for (r, c) in cells)
    return sorted(out)


def _theme_cells_from_preview_state(seed_grid, rows, cols, locked_letters,
                                     preseed_assignment, priority_words):
    """`_theme_word_cells` for a cycle-START preview (`_cycle_start_
    preview`), where the resume state is either a per-slot word list
    (`preseed_assignment`) or a plain `{cell: letter}` map (`locked_
    letters`) rather than a genuine `(slots, assignment)` pair. For the
    map, a slot is only considered to carry a word if ALL of its cells are
    in it."""
    if not priority_words or seed_grid is None:
        return []
    slots = extract_slots(seed_grid, rows, cols)
    if preseed_assignment is not None:
        return _theme_word_cells(slots, preseed_assignment, priority_words)
    if locked_letters:
        assignment = [
            "".join(locked_letters[c] for c in cells)
            if all(c in locked_letters for c in cells) else None
            for cells in slots
        ]
        return _theme_word_cells(slots, assignment, priority_words)
    return []


def _low_candidate_slot_cells(grid, rows, cols, index, locked_letters):
    """All cells of a *partially* locked slot (at least one locked cell,
    but not all — see `_slot_with_insufficient_candidates` for why an
    *entirely* locked slot is never affected: it's already a real,
    confirmed word, not a still-fragile slot) whose intersection with the
    already-locked letters leaves strictly fewer than `PREFILL_LOCKED_
    MIN_WORD_COUNT` (3) real candidates in the dictionary (`_slot_
    candidate_count`, the same per-position intersection as `Filler.
    _domain`).

    Purely diagnostic, for the web preview — at the user's explicit
    request, on the "Génération du motif de cases noires" grid (the
    `pattern` event, a cycle's own starting state): "show, with an orange
    background, the cells below the fill-possibility threshold (< 3
    possibilities)", to make visible, even before pre-fill/curative
    cleanup ever acts on it, which slots are already fragile. Unlike
    `_slot_with_insufficient_candidates` (used by pre-fill itself to
    *decide* on an action, and which stops at the very first problematic
    slot found, with `skip`/length-only on top), this function returns
    the complete set of affected cells, across every slot at once —
    nothing needs to be targeted one at a time for a plain display.

    Returns a sorted list of `(r, c)` cells, empty if `locked_letters` is
    empty/`None` (nothing to report) or if no slot falls below the
    threshold."""
    if not locked_letters:
        return []
    cells = set()
    for slot in extract_slots(grid, rows, cols):
        length = len(slot)
        locked_count = sum(1 for cell in slot if cell in locked_letters)
        if 0 < locked_count < length:
            if _slot_candidate_count(index, length, slot, locked_letters) < PREFILL_LOCKED_MIN_WORD_COUNT:
                cells.update(slot)
    return sorted(cells)


def _noise_slot_cells(grid, rows, cols, index, locked_letters):
    """Cases où **aucune lettre** ne satisfait à la fois l'emplacement
    horizontal et l'emplacement vertical qui s'y croisent — le domaine brut
    de chacun des deux emplacements, pris séparément, peut très bien être
    non vide (donc ni l'un ni l'autre n'est signalé par le surlignage rouge
    `.impossible`, un domaine totalement vide), mais si aucun de leurs mots
    réellement *jouables* respectifs ne partage la même lettre à cette case
    précise, elle ne peut en pratique jamais être remplie.

    « Réellement jouable », pour un emplacement *partiellement* verrouillé
    (même restriction que `_low_candidate_slot_cells` ci-dessus — un
    emplacement entièrement verrouillé est déjà un mot confirmé, un
    emplacement entièrement vierge est hors du champ de cette fonction),
    signifie un candidat du dictionnaire (`_slot_candidates`) qui n'est ni :
    - déjà utilisé ailleurs dans cette même grille (tout autre emplacement
      entièrement verrouillé, `used_words`, calculé ici directement depuis
      `locked_letters` — un mot déjà posé ne peut plus être reposé) ;
    - en dessous de `NOISE_FREQUENCY_THRESHOLD` en fréquence brute
      (`index[length]["freq"]`, voir `build_index`) — voir la docstring de
      cette constante pour sa calibration.

    Une case ne croisée QUE par un seul emplacement encore ouvert (l'autre
    direction y est déjà entièrement verrouillée, ou n'y forme même pas un
    véritable emplacement — une zone d'une seule case) est un cas
    dégénéré de la même règle : elle est signalée si et seulement si ce
    seul emplacement, à lui seul, n'a plus aucun mot jouable du tout.

    Cas concret ayant motivé cette fonctionnalité, voir CLAUDE.md : une
    grille bloquée 11 paliers d'affilée sur 3 cases, dont une seule
    (l'intersection entre un emplacement horizontal `_S_` et un
    emplacement vertical `ER_`) était en réalité totalement bloquée par ce
    critère — chaque emplacement pris isolément avait pourtant bel et bien
    des mots jouables (`OST`/`PST`/...  d'un côté, `ERG`/`ERS`/`ERE` de
    l'autre), mais aucune lettre commune aux deux ensembles à leur case
    partagée ; un premier jet de cette fonction, qui ne vérifiait chaque
    emplacement qu'isolément (sans le croisement), ne signalait donc rien
    du tout sur cette grille — vérifié en la rejouant explicitement contre
    l'historique réel de cette même génération.

    Purement diagnostique, sur l'aperçu "Génération du motif de cases
    noires" (l'événement `pattern`), à la demande explicite de
    l'utilisateur — même portée que `_low_candidate_slot_cells`, jamais
    calculée pour un palier de reprise "telle quelle" (voir son propre
    appelant dans `generate_grid`)."""
    if not locked_letters:
        return []
    all_slots = extract_slots(grid, rows, cols)
    used_words = {
        "".join(locked_letters[cell] for cell in slot)
        for slot in all_slots
        if all(cell in locked_letters for cell in slot)
    }
    # Mots jouables par emplacement partiellement verrouillé (None pour
    # tout autre emplacement — entièrement verrouillé ou entièrement
    # vierge — hors du champ de cette fonction, voir la docstring).
    playable_by_slot = []
    for slot in all_slots:
        length = len(slot)
        locked_count = sum(1 for cell in slot if cell in locked_letters)
        if not (0 < locked_count < length):
            playable_by_slot.append(None)
            continue
        candidates = _slot_candidates(index, length, slot, locked_letters)
        freq_map = index.for_cells(slot).get(length, {}).get("freq", {})
        playable_by_slot.append([
            w for w in candidates
            if w not in used_words and freq_map.get(w, 0.0) >= NOISE_FREQUENCY_THRESHOLD
        ])
    # Pour chaque case encore libre, les emplacements ouverts (au sens
    # ci-dessus) qui la traversent, avec sa position exacte dans chacun —
    # 1 seul pour une case bordée d'un côté par du verrouillé/du noir, 2
    # pour un vrai croisement horizontal/vertical.
    cell_slots = defaultdict(list)
    for i, slot in enumerate(all_slots):
        if playable_by_slot[i] is None:
            continue
        for pos, cell in enumerate(slot):
            if cell not in locked_letters:
                cell_slots[cell].append((i, pos))
    cells = set()
    for cell, entries in cell_slots.items():
        letter_sets = []
        for slot_i, pos in entries:
            playable = playable_by_slot[slot_i]
            if not playable:
                letter_sets = []
                break
            letter_sets.append({w[pos] for w in playable})
        if not letter_sets:
            cells.add(cell)
            continue
        common = letter_sets[0]
        for s in letter_sets[1:]:
            common &= s
        if not common:
            cells.add(cell)
    return sorted(cells)


def _cycle_start_preview(rows, cols, seed_grid, locked_letters, preseed_assignment):
    """Builds the single-grid preview shown right at the *start* of a
    palier (generate_grid's own `progress("pattern", ...)` call), at the
    user's explicit request: "Ajouter à l'historique (donc stacké) l'état
    initial d'un cycle." Until this, examples_history (backend/app.py)
    only ever stacked a palier's own *end* state (a failed attempt, or the
    winning grid at "minimizing"/"clues") — the state a palier actually
    *starts from* (whatever `_build_retry_seed`/`_clean_blocked_slots`
    carried forward from the previous one, or a blank grid for the very
    first palier) had no entry of its own in that history at all.

    Returns `(example_grid, locked_cells)` — the same two fields every
    other preview entry already carries (`impossible_cells`/`forced_cells`
    are always empty for this one: nothing has been searched yet at this
    point, so nothing is "impossible" yet, and no statistical hint has
    been sampled yet either — that only happens once this palier's own
    `_pattern_attempt`/`_pattern_continue` workers actually run). Wired
    into `progress("pattern", ...)` below, so a fresh grid is stacked into
    `examples_history` right alongside the "Tentative X/Y..." status
    already carried by that same event's own kwargs — the two arrive
    together in one call, so the web UI's history navigation
    (frontend/static/script.js's previewHistory, see CLAUDE.md) always
    shows a cycle's real starting point paired with its own cycle count,
    not just its outcome.

    `locked_cells` mirrors the meaning it already has everywhere else in
    this file (see `build_partial_letters_grid`'s own docstring, `try_
    fill`'s diagnostics): a cell whose shown letter is a real, previously-
    confirmed one carried over from the *previous* palier, not a guess —
    rendered with the same distinct `.locked` highlight already used
    elsewhere in the web UI. Handles both of `generate_grid`'s mutually
    exclusive resume shapes: `preseed_assignment` (the "reprise
    telle-quelle" case, a real word per already-assigned slot) and
    `locked_letters` (the "nettoyage" case, a plain `{cell: letter}` map).
    `seed_grid` being `None` (the very first palier of a call) means a
    blank grid with nothing locked at all, regardless of the other two."""
    if seed_grid is None:
        return [[WHITE] * cols for _ in range(rows)], []
    grid = [row[:] for row in seed_grid]
    # `if grid[r][c] == BLACK: continue` below (both branches) guards
    # against a case found and confirmed live: `generate_grid`'s "reset"
    # mechanism (FULL_RESET_ATTEMPT_COUNT) starts a handful of
    # a palier's own `_pattern_attempt` workers from a totally blank,
    # *independent* grid rather than building on top of `carry_seed_grid`
    # — this function is reused (see progress("pattern_generated", ...)
    # in generate_grid) to preview *each* of a palier's own outcomes, one
    # of which can genuinely be such a reset worker's own unrelated
    # pattern, passed here as `seed_grid`. Without this guard, a locked
    # cell (from `carry_locked_letters`/`carry_preseed_assignment`, both
    # computed against the *previous* palier's own pattern) could
    # coincide with a black cell this specific reset worker's own
    # `make_pattern` call happened to place there, and unconditionally
    # writing a letter over it would silently erase that black cell from
    # the shown preview — reproduced live: a real generate_grid() run's
    # `pattern_generated` event showed *fewer* black cells than the same
    # palier's own "pattern" (cycle-start) event, an impossible outcome
    # without this bug, since `make_pattern` can only ever add black
    # cells on top of a real `carry_seed_grid`, never remove any — this
    # guard is what actually prevents that from ever showing here again.
    # For the ordinary case (this palier's own real carried-forward
    # pattern, or "reprise telle-quelle"'s byte-identical one), a locked
    # cell is already guaranteed to stay white by construction elsewhere
    # in this file, so this guard is a pure no-op there — it only ever
    # changes anything for a reset worker's own independent pattern.
    if preseed_assignment is not None:
        slots = extract_slots(seed_grid, rows, cols)
        locked_cells = []
        for cells, word in zip(slots, preseed_assignment):
            if word is None:
                continue
            for (r, c), ch in zip(cells, word):
                if grid[r][c] == BLACK:
                    continue
                grid[r][c] = ch
                locked_cells.append((r, c))
        return grid, sorted(locked_cells)
    if locked_letters:
        locked_cells = []
        for (r, c), ch in locked_letters.items():
            if grid[r][c] == BLACK:
                continue
            grid[r][c] = ch
            locked_cells.append((r, c))
        return grid, sorted(locked_cells)
    return grid, []


def _playable_score(diag):
    """Mesure la quantité de contenu réellement posé et confirmé dans
    `diag["assignment"]` — racine carrée de la somme des carrés des
    longueurs de chaque mot déjà assigné (`None` ignoré) — à la demande
    explicite de l'utilisateur : "Au lieu d'un score sur les injouables,
    mesurer les jouables (racine carré des sommes des carrés des longueurs
    jouables)." Utilisé par `generate_grid` pour trier `failed_unique` en
    sélectionnant la "meilleure" tentative échouée d'un palier — voir son
    propre commentaire pour le biais que ce critère corrige (un ancien tri
    par "le moins de cases injouables" favorisait à tort un état publié
    tôt dans une recherche encore peu avancée, où peu de mots posés
    signifie mécaniquement peu de cases pouvant déjà être jugées
    injouables).

    Même principe que le score déjà utilisé par `generate_grid` pour
    départager plusieurs tentatives *réussies* du même palier — favoriser
    quelques mots longs plutôt que beaucoup de mots courts pour le même
    total de lettres — avec en plus la racine carrée pour ramener ce score
    à une échelle comparable à une simple longueur plutôt qu'à une somme de
    carrés. La longueur d'un mot assigné est prise directement via
    `len(word)` (jamais recalculée depuis le motif) : un mot ne peut être
    assigné qu'à un emplacement de sa propre longueur, donc les deux
    valeurs sont toujours rigoureusement égales."""
    return sum(len(w) ** 2 for w in diag["assignment"] if w is not None) ** 0.5


def _cleaned_playable_score(grid, diag, rows, cols, index, rng):
    """Comme `_playable_score`, mais sur l'état APRÈS nettoyage — le
    contenu qui survivrait réellement une fois retiré, un par un, ce qui
    est nécessaire pour lever chaque situation impossible de `diag[
    "impossible_slots"]` (`_clean_blocked_slots`, voir sa propre docstring
    pour l'algorithme "un mot à la fois" désormais utilisé) — plutôt que
    sur `diag["assignment"]` brut, à la demande explicite de
    l'utilisateur : "Il faut montrer les emplacements avant nettoyage,
    évaluer la grille après nettoyage (qui sera transmise au cycle
    suivant si sélectionnée)." `index`/`rng` transmis tels quels à
    `_clean_blocked_slots` — le même générateur aléatoire déjà partagé
    par tout `generate_grid()`, pour que ce score reste reproductible
    depuis la même graine plutôt que d'introduire une seconde source
    d'aléatoire indépendante.

    Utilisé pour trier `failed_unique`/choisir `failed_pairs[0]` — la
    tentative qui l'emporte est donc désormais celle qui garde le plus de
    contenu réellement posé une fois nettoyée, pas celle qui, avant tout
    nettoyage, a le moins de cases injouables ou le plus de contenu brut :
    deux tentatives avec le même nombre de cases injouables brutes peuvent
    perdre des quantités de contenu très différentes une fois nettoyées
    (une tentative dont le mot croisant l'emplacement impossible est
    court perd moins qu'une tentative dont il est long), et c'est bien
    cette quantité *après* nettoyage qui détermine ce qui sera réellement
    transmis au palier suivant si cette tentative est retenue — c'est
    donc elle qu'il faut évaluer, pas l'état brut.

    Recalcule `slots` directement depuis le vrai motif noir/blanc `grid`
    (jamais depuis un `example_grid` aux lettres superposées, qui
    fausserait `extract_slots`) — chaque tentative a son propre motif et
    sa propre affectation, rien à partager entre elles. Repli sur
    `_playable_score(diag)` (l'état brut) si `slots` ne correspond pas en
    longueur à `diag["assignment"]` — ne devrait jamais arriver en usage
    réel, un filet de sécurité plutôt qu'un cas attendu."""
    slots = extract_slots(grid, rows, cols)
    if len(slots) != len(diag["assignment"]):
        return _playable_score(diag)
    cleaned_assignment, _, _ = _clean_blocked_slots(
        slots, diag["assignment"], diag["impossible_slots"], index=index, rng=rng,
    )
    return sum(len(w) ** 2 for w in cleaned_assignment if w is not None) ** 0.5


def _public_diag(diag):
    """Copie de `diag` sûre à étaler dans un événement `progress(...)` —
    filet de sécurité générique contre un futur champ de diagnostic qui
    ne serait pas JSON-safe tel quel (par exemple un dict indexé par
    cellule `(row, col)`, un tuple comme *clé* de dict), plutôt qu'un
    filtre pour un champ précis aujourd'hui.

    Root-causé en direct, la première (et jusqu'ici seule) fois que ce
    problème s'est posé : un vrai `GET /api/generate/status/{job_id}`
    tombé en 500 (l'interface web affichait alors une erreur
    "JSON.parse: unexpected character..." puisque le corps de réponse
    n'était plus du JSON valide) — `backend.log` montrait
    `TypeError: cannot use 'list' as a dict key` au beau milieu de
    `fastapi.encoders.jsonable_encoder`. Le champ fautif à l'époque,
    `own_locked_letters` (voir `_pattern_attempt` dans son historique),
    encodait chaque case comme clé de dict — `jsonable_encoder` encode
    récursivement chaque clé pour la rendre JSON-safe, ce qui transforme
    un tuple en liste, puis tente de s'en servir comme clé d'un dict
    Python tout court pour construire le résultat encodé — une liste
    n'étant pas hashable, ça lève cette même `TypeError`. Chaque autre
    champ de `diag` contenant des cellules (`locked_cells`,
    `impossible_cells`, `forced_cells`...) les porte en tant qu'éléments
    d'une simple liste, jamais en tant que clés de dict — aucun d'eux ne
    pose ce problème. `own_locked_letters` lui-même a depuis été retiré
    entièrement (son seul lecteur, `_preview_locked_source`, a disparu en
    même temps que l'aperçu tardif qu'il alimentait) — cette fonction
    reste néanmoins en place, volontairement, comme garde-fou pour la
    même classe de bug si un futur champ de diagnostic prenait une forme
    similaire."""
    return {k: v for k, v in diag.items() if k != "own_locked_letters"}


# Probabilité de tenter une case noire plutôt que de retirer un mot
# croisant, dans la boucle "un par un" de `_clean_blocked_slots` ci-dessous
# — à la demande explicite de l'utilisateur, restreinte à la reprise
# "telle quelle" uniquement (voir `generate_grid`, branche `if
# still_has_hope:`), jamais au nettoyage complet (`_build_retry_seed`, qui
# régénère déjà un motif neuf via `make_pattern` et peut donc déjà ajouter
# des cases noires par ce biais) : "En l'état, nettoyer les zones
# impossibles et les connectés, supprime beaucoup de mots, ce qui oblige
# plus tard à rajouter des cases noires par d'autres mécanismes. Autant
# tenter la case noire tout de suite, et supprimer moins de mots. Par
# ailleurs, sur des toutes petites zones, la suppression de mots ne
# supprime pas grand chose, et la recherche tourne en rond sur très peu de
# lettres modifiables. Ajouter des noires peut permettre de réellement
# finir ces petites zones où la vraie solution n'existe peut-être pas."
# Abaissée de 1/3 à 1/10 juste après, à la demande explicite de
# l'utilisateur ("trop de cases noires à 1/3") — même mécanisme, valeur
# revue à la baisse suite à un premier usage réel jugé trop agressif.
BLACK_CELL_INSTEAD_OF_REMOVAL_PROBABILITY = 1 / 10


def _impossible_indices(slots_list, index, known):
    """Indices des emplacements de `slots_list` réputés impossibles au sens
    local de `_shorten_impossible_zones` ci-dessous : pas entièrement
    couverts par `known`, et sans aucun candidat réel une fois cette
    contrainte (partielle, ou totalement absente — une longueur que le
    dictionnaire ne couvre pas du tout est tout aussi bloquée qu'une
    combinaison de lettres invalide, même critère que la "zone strictement
    sans issue" de `_clean_blocked_slots`) appliquée (`_slot_candidates`
    vide). Un emplacement entièrement couvert par `known` est un mot déjà
    confirmé — sa validité éventuelle est une tout autre question, déjà
    traitée ailleurs dans ce fichier (voir le bug AVALAS dans CLAUDE.md).
    Même critère utilisé aux deux endroits où `_shorten_impossible_zones`
    en a besoin : pour redétecter les emplacements encore bloqués après
    chaque case noire posée, et pour calculer la liste finale renvoyée à
    l'appelant."""
    result = []
    for j, cells in enumerate(slots_list):
        length = len(cells)
        if length == sum(1 for c in cells if c in known):
            continue
        sub_known = {c: known[c] for c in cells if c in known}
        if not _slot_candidates(index, length, cells, sub_known):
            result.append(j)
    return result


def _invalid_fully_known_indices(slots_list, index, known, exempt=None):
    """Indices des emplacements de `slots_list` entièrement couverts par
    `known` — chacune de leurs cases fixée, directement ou indirectement,
    par un mot croisant — mais dont la combinaison de lettres ne
    correspond à AUCUN mot réel du dictionnaire. `_impossible_indices`
    ignore volontairement ce cas (voir sa propre docstring : "sa validité
    éventuelle est une tout autre question, déjà traitée ailleurs dans ce
    fichier") — un choix de conception valable pour ses propres appelants
    d'origine (`_pattern_attempt`/`_pattern_continue`, qui valident déjà
    séparément tout emplacement entièrement verrouillé, voir `locked_
    impossible_slots`), mais `_shorten_impossible_zones` ci-dessous, en
    posant de nouvelles lettres qui peuvent achever de couvrir un
    emplacement croisant entièrement sans jamais interroger le
    dictionnaire pour cette combinaison précise, n'avait aucun autre
    endroit pour effectuer ce même contrôle — bug réel constaté en
    direct : un mot inventé ("ATEIRS", "TENLES"...), jamais réellement
    choisi par personne, simplement recomposé tel quel à partir de lettres
    individuellement correctes mais jamais vérifiées ensemble.

    `exempt` (`None` par défaut — aucun effet pour tout appelant existant
    avant cette fonctionnalité) : un dict/ensemble de cases — typiquement
    `permanent_locked_letters`, voir la docstring de `generate_grid` — dont
    un emplacement entièrement couvert n'est JAMAIS signalé ici, quelle que
    soit sa validité réelle au sens du dictionnaire, à la demande explicite
    de l'utilisateur : "Les lettres posées en mode interactif sont à
    considérer comme bonnes, même si un emplacement contient un mot
    impossible (probablement un nom propre voulu par l'utilisateur)... ne
    doivent pas être remis en cause par la génération de la grille 'Finir
    la grille'." Seul le mode "Interactif" (Impossibles/Vérifier) doit
    encore signaler un tel mot au joueur — cette fonction n'y est jamais
    appelée avec `exempt`, seuls ses appelants internes à `generate_grid`
    (`_shorten_impossible_zones`/`_lengthen_impossible_zones`/
    `_optimize_before_cleanup`/`_clean_continue_candidate`) le font."""
    result = []
    for j, cells in enumerate(slots_list):
        length = len(cells)
        if length != sum(1 for c in cells if c in known):
            continue
        if exempt and all(c in exempt for c in cells):
            continue
        known_full = {c: known[c] for c in cells}
        if not _slot_candidates(index, length, cells, known_full):
            result.append(j)
    return result


def _new_crossing_impossibility(cur_slots, cell_to_slots, own_idx, sub, word, known, index):
    """True si poser `word` sur `sub` (une fois `known` mis à jour avec ses
    lettres) rendrait impossible un emplacement CROISANT — un autre
    emplacement de `cur_slots`, dans l'autre direction, partageant l'une
    des cases de `sub` — qui ne l'était pas déjà avant ce placement précis.
    Un emplacement croisant déjà sans candidat AVANT ce placement n'est
    jamais compté ici (il n'est pas "créé" par ce mot, il l'était déjà,
    pour une tout autre raison) — seule une dégradation NOUVELLE,
    directement causée par cette lettre précise, doit faire rejeter le
    candidat. Même mécanique d'intersection par position que `_impossible_
    indices`/`Filler._domain` (`_slot_candidates`), appliquée ici avant/
    après une seule lettre ajoutée plutôt qu'à l'état final."""
    for pos, cell in enumerate(sub):
        letter = word[pos]
        for j in cell_to_slots.get(cell, ()):
            if j == own_idx:
                continue
            cross_cells = cur_slots[j]
            known_before = {c: known[c] for c in cross_cells if c in known}
            if not _slot_candidates(index, len(cross_cells), cross_cells, known_before):
                continue
            known_after = dict(known_before)
            known_after[cell] = letter
            if not _slot_candidates(index, len(cross_cells), cross_cells, known_after):
                return True
    return False


def _find_shorter_word_for_zone(grid, rows, cols, cells, cur_slots, cell_to_slots, own_idx,
                                 index, known, used_words, rng):
    """Pour UN emplacement réputé impossible (`cells`, d'indice `own_idx`
    dans `cur_slots`), cherche un mot plus court à poser en tête ou en fin
    de la zone, laissant au moins une case vide de l'autre côté — donc de
    longueur maximale `len(cells) - 1`, jamais la longueur complète de
    l'emplacement, `3` au minimum (jamais `2` ni moins — à la demande
    explicite de l'utilisateur : "ne pas tester un remplissage partiel de
    moins de 2 lettres... comprendre : au moins 3 lettres"). Rassemble
    D'ABORD tous les candidats valables, toutes longueurs et tous côtés
    confondus — un candidat n'est retenu que si sa case frontière (celle
    qui sépare le mot de la case vide restante) reste structurellement
    valide une fois noircie (`is_structurally_valid(min_interior_free=1)`,
    jamais de passe-droit sur cet invariant absolu), que le morceau
    restant (l'autre côté de la zone, une fois la case frontière noircie)
    n'est pas déjà couvert par une combinaison de lettres invalide (voir
    plus bas), que le mot lui-même ajoute au moins une lettre réellement
    nouvelle (une case qui n'était pas encore connue — poser un "mot" dont
    toutes les lettres étaient déjà acquises n'apporte aucun progrès,
    seulement une case noire), et que le mot n'est pas déjà utilisé
    ailleurs dans la grille (`used_words`) — puis tire au hasard dans cet
    ensemble complet (longueur elle-même variable, pas seulement le côté à
    longueur égale, ni un ordre "le plus long d'abord").

    Pour chaque candidat ainsi tiré, vérifie qu'il ne crée pas un nouvel
    emplacement croisant impossible (`_new_crossing_impossibility`) avant
    de le retenir — si c'est le cas, ce candidat est écarté et un autre
    est tiré, jusqu'à épuisement de l'ensemble. Renvoie `(mot,
    cases_du_mot, case_frontière)` du premier candidat qui passe tous ces
    contrôles, ou `None` si aucun ne convient — auquel cas cet emplacement
    n'est pas touché du tout, ni case noire ni mot posé.

    Une case frontière déjà couverte par une lettre confirmée (`known`)
    n'est jamais candidate — la noircir détruirait le mot croisant qui la
    fixe déjà, laissant ses autres cases assignées à un fragment qui n'est
    plus forcément un vrai mot du dictionnaire (bug réel constaté en
    direct : "génère des mots qui n'existent pas... ne pas poser de case
    noire sur une case qui contient déjà une lettre").

    Les deux morceaux résultants de la zone initiale — le mot posé
    lui-même, et le reste éventuel de l'autre côté de la case frontière —
    sont l'un et l'autre contrôlés s'ils se trouvent entièrement déterminés
    : le mot posé l'est toujours, par construction (validé contre le
    dictionnaire avant d'être retenu comme candidat) ; le reste, s'il
    compte au moins deux cases (sinon ce n'est pas un vrai emplacement, il
    ne sera jamais lui-même testé comme un mot) et se trouve déjà
    entièrement couvert par `known` (par d'autres croisements,
    indépendamment de ce placement précis), doit lui aussi correspondre à
    un mot réel — à la demande explicite de l'utilisateur : "bien tester
    les 2 parties de l'emplacement initialement vide, si les 2 parties
    sont complètes. Si un des deux morceaux complets n'est pas un mot
    valide, ne pas faire le remplacement partiel." Si ce reste est
    entièrement déterminé mais ne correspond à aucun mot réel, toute la
    combinaison (longueur, côté) est écartée d'un coup — sans même
    chercher de mot pour `sub`, puisque la validité du reste ne dépend pas
    du mot choisi."""
    length = len(cells)
    options = []
    for m in range(length - 1, 2, -1):
        for side in (0, 1):
            if side == 0:
                sub = cells[:m]
                boundary = cells[m]
                leftover = cells[m + 1:]
            else:
                sub = cells[length - m:]
                boundary = cells[length - m - 1]
                leftover = cells[:length - m - 1]
            br, bc = boundary
            if grid[br][bc] == BLACK or boundary in known:
                continue
            grid[br][bc] = BLACK
            valid = is_structurally_valid(grid, rows, cols, min_interior_free=1)
            grid[br][bc] = WHITE
            if not valid:
                continue
            if len(leftover) >= 2 and all(c in known for c in leftover):
                leftover_known = {c: known[c] for c in leftover}
                if not _slot_candidates(index, len(leftover), leftover, leftover_known):
                    continue
            if all(c in known for c in sub):
                continue
            sub_known = {c: known[c] for c in sub if c in known}
            candidates = [
                w for w in _slot_candidates(index, m, sub, sub_known)
                if w not in used_words
            ]
            for w in candidates:
                options.append((w, sub, boundary))
    rng.shuffle(options)
    for word, sub, boundary in options:
        if _new_crossing_impossibility(cur_slots, cell_to_slots, own_idx, sub, word, known, index):
            continue
        return word, sub, boundary
    return None


def _known_slot_boundary_cells(new_grid, rows, cols, cur_slots, known):
    """Cases noires qui bornent, ou sont prises en sandwich par, un
    emplacement déjà entièrement déterminé par `known` — jamais à
    supprimer/déplacer, sous peine de casser un mot déjà acquis. Réutilise
    le même double critère déjà établi et longuement affiné pour
    `_build_retry_seed`'s propre étape 3 (voir sa docstring pour
    l'historique complet des versions essayées) : (1) elle borne
    directement un emplacement entièrement connu — immédiatement avant sa
    première case ou après sa dernière, dans le sens propre de cet
    emplacement ; (2) elle a une lettre connue des DEUX côtés à la fois
    d'un même axe (haut ET bas, ou gauche ET droite, jamais besoin des deux
    axes ensemble) — la supprimer fusionnerait deux emplacements distincts
    en un seul qui peut ne correspondre à aucun mot réel, perturbant les
    deux côtés à la fois. Utilisée par `_find_longer_word_for_zone`/
    `_lengthen_impossible_zones` pour décider quelle case noire bordant une
    zone impossible peut être supprimée/déplacée sans risque — la même
    notion de "borne un mot déjà posé" que `_build_retry_seed`, juste
    recalculée ici à partir de `cur_slots`/`known` (l'état d'un tour de la
    boucle de nettoyage) plutôt que d'un `assignment` global."""
    protected = set()
    for cells in cur_slots:
        if not all(c in known for c in cells):
            continue
        direction = "across" if len(cells) > 1 and cells[1][0] == cells[0][0] else "down"
        dr, dc = (0, 1) if direction == "across" else (1, 0)
        (r0, c0), (r1, c1) = cells[0], cells[-1]
        for br, bc in ((r0 - dr, c0 - dc), (r1 + dr, c1 + dc)):
            if 0 <= br < rows and 0 <= bc < cols:
                protected.add((br, bc))

    def _direction_has_known_letter(r, c, dr, dc):
        rr, cc = r + dr, c + dc
        while 0 <= rr < rows and 0 <= cc < cols and new_grid[rr][cc] == WHITE:
            if (rr, cc) in known:
                return True
            rr += dr
            cc += dc
        return False

    for r in range(rows):
        for c in range(cols):
            if new_grid[r][c] != BLACK:
                continue
            vertical_both = _direction_has_known_letter(r, c, -1, 0) and \
                _direction_has_known_letter(r, c, 1, 0)
            horizontal_both = _direction_has_known_letter(r, c, 0, -1) and \
                _direction_has_known_letter(r, c, 0, 1)
            if vertical_both or horizontal_both:
                protected.add((r, c))
    return protected


def _new_boundary_crossing_impossible(grid, rows, cols, boundary, letter, own_dr, own_dc,
                                       known, index):
    """Comme `_new_crossing_impossibility`, mais pour la case `boundary`
    elle-même — jusqu'ici noire, donc absente de `cell_to_slots` (calculé
    sur l'ancien motif, où cette case n'appartenait à aucun emplacement).
    La rendre blanche peut faire naître, dans le sens PERPENDICULAIRE à
    `own_dr`/`own_dc` (le sens propre de la zone qu'on allonge), un tout
    nouvel emplacement croisant que `_new_crossing_impossibility` ne peut
    pas voir — ce contrôle recalcule ce parcours perpendiculaire directement
    (la suite de cases blanches consécutives de part et d'autre de
    `boundary`, jusqu'à une case noire ou le bord), exactement le même
    principe que `_new_crossing_impossibility`, appliqué à cette unique
    case qui lui échappe. Renvoie `False` si l'emplacement perpendiculaire
    ainsi trouvé compte moins de 2 cases (pas un vrai emplacement) ou était
    déjà sans candidat AVANT même l'ajout de `letter` (donc pas une
    dégradation NOUVELLE causée par ce placement précis)."""
    perp_dr, perp_dc = own_dc, own_dr
    br, bc = boundary
    cells = [boundary]
    rr, cc = br - perp_dr, bc - perp_dc
    while 0 <= rr < rows and 0 <= cc < cols and grid[rr][cc] == WHITE:
        cells.insert(0, (rr, cc))
        rr -= perp_dr
        cc -= perp_dc
    rr, cc = br + perp_dr, bc + perp_dc
    while 0 <= rr < rows and 0 <= cc < cols and grid[rr][cc] == WHITE:
        cells.append((rr, cc))
        rr += perp_dr
        cc += perp_dc
    if len(cells) < 2:
        return False
    known_before = {c: known[c] for c in cells if c in known and c != boundary}
    if not _slot_candidates(index, len(cells), cells, known_before):
        return False
    known_after = dict(known_before)
    known_after[boundary] = letter
    return not _slot_candidates(index, len(cells), cells, known_after)


def _find_longer_word_for_zone(grid, rows, cols, cells, cur_slots, cell_to_slots, own_idx,
                                protected, index, known, used_words, rng):
    """Pour UN emplacement réputé impossible (`cells`, d'indice `own_idx`
    dans `cur_slots`), à la demande explicite de l'utilisateur : "si un
    emplacement ne trouve pas de mot... mais qu'au moins une des cases
    noires limitant la zone peut être supprimée ou déplacée (parce qu'il y
    a de la place avant ou après, et que cette case noire n'est pas une
    limite d'un mot déjà posé), tester des longueurs différentes en
    supprimant ou déplaçant la case noire." Le complément exact de
    `_find_shorter_word_for_zone` (qui RACCOURCIT la zone en ajoutant une
    case noire à l'intérieur) : celle-ci l'ALLONGE en repoussant l'une de
    ses DEUX cases noires bordantes existantes (tête ou fin, jamais les
    deux à la fois dans un même appel) — soit en la déplaçant de quelques
    cases plus loin (une nouvelle case noire plus loin dans la même
    direction), soit en la supprimant purement et simplement quand
    l'obstacle naturel suivant (une autre case noire, ou le bord de la
    grille) suffit déjà à borner la zone allongée.

    Une case bordante n'est même candidate que si (1) elle est
    effectivement noire et dans la grille — sinon la zone touche déjà le
    bord de ce côté, rien à repousser — et (2) elle ne figure pas dans
    `protected` (voir `_known_slot_boundary_cells`) — une case qui borne
    déjà un mot différent, réellement posé, ne doit jamais être touchée.
    Au-delà, la place disponible de ce côté (la suite de cases blanches
    consécutives immédiatement après cette case bordante, jusqu'à la
    prochaine case noire ou le bord) détermine combien de longueurs
    différentes sont essayées : allonger de 1 case (la case bordante
    elle-même rejoint la zone, une nouvelle case noire est posée juste
    après), de 2, ..., jusqu'à absorber la totalité de la place disponible
    (aucune nouvelle case noire posée du tout, l'obstacle suivant borne
    déjà la zone allongée).

    Une case noire candidate à ce déplacement n'est jamais posée sur une
    case déjà connue (`known`) — la noircir détruirait le mot croisant qui
    la fixe déjà — et doit elle-même rester structurellement valide
    (`is_structurally_valid(min_interior_free=1)`, le même invariant
    absolu utilisé partout ailleurs dans ce fichier pour l'ajout d'une
    case noire) ; aucun contrôle de ce genre n'est nécessaire quand
    l'obstacle suivant est déjà en place (rien de nouveau n'est ajouté —
    et RETIRER une case noire ne peut jamais violer cet invariant, qui ne
    concerne que les ajouts).

    Rassemble d'abord tous les candidats valables — les deux côtés, toutes
    les longueurs d'allongement possibles, tous les mots réels qui y
    correspondent compte tenu des lettres déjà connues sur la zone
    allongée — puis tire au hasard dans cet ensemble complet. Pour chaque
    candidat ainsi tiré, vérifie qu'il ne crée pas de nouvelle situation
    impossible : `_new_crossing_impossibility` pour les cases qui
    appartenaient déjà à un emplacement avant cet allongement, et
    `_new_boundary_crossing_impossible` pour la case bordante elle-même
    (jusque-là noire, donc absente de `cell_to_slots`) — un nouvel
    emplacement perpendiculaire peut naître exactement à cet endroit une
    fois cette case rendue blanche. Renvoie `(mot, cases_du_mot,
    ancienne_case_bordante, nouvelle_case_bordante_ou_None)` du premier
    candidat qui passe tous ces contrôles, ou `None` si aucun côté/
    longueur ne convient."""
    length = len(cells)
    direction = "across" if length > 1 and cells[1][0] == cells[0][0] else "down"
    dr, dc = (0, 1) if direction == "across" else (1, 0)
    (r0, c0), (r1, c1) = cells[0], cells[-1]

    options = []
    for side, boundary, extend_dr, extend_dc in (
        ("head", (r0 - dr, c0 - dc), -dr, -dc),
        ("tail", (r1 + dr, c1 + dc), dr, dc),
    ):
        br, bc = boundary
        if not (0 <= br < rows and 0 <= bc < cols):
            continue
        if grid[br][bc] != BLACK or boundary in protected:
            continue
        avail = []
        rr, cc = br + extend_dr, bc + extend_dc
        while 0 <= rr < rows and 0 <= cc < cols and grid[rr][cc] == WHITE:
            avail.append((rr, cc))
            rr += extend_dr
            cc += extend_dc
        if not avail:
            continue
        for k in range(len(avail) + 1):
            new_boundary = avail[k] if k < len(avail) else None
            if new_boundary is not None:
                if new_boundary in known:
                    continue
                # Valider l'état RÉEL une fois ce candidat appliqué — l'ancienne
                # case bordante redevient blanche EN MÊME TEMPS que la nouvelle
                # devient noire, jamais l'une sans l'autre : les tester l'une
                # sans l'autre (par ex. la nouvelle case noire posée alors que
                # l'ancienne est encore noire elle aussi) évaluerait un état
                # hypothétique qui ne sera jamais réellement validé — bug
                # trouvé en direct : les deux cases noires adjacentes coupaient
                # alors la grille en deux composantes déconnectées, un faux
                # échec de connexité qui n'existe pas dans l'état vraiment visé.
                nb_r, nb_c = new_boundary
                grid[br][bc] = WHITE
                grid[nb_r][nb_c] = BLACK
                valid = is_structurally_valid(grid, rows, cols, min_interior_free=1)
                grid[br][bc] = BLACK
                grid[nb_r][nb_c] = WHITE
                if not valid:
                    continue
            absorbed = avail[:k]
            if side == "head":
                new_cells = list(reversed(absorbed)) + [boundary] + list(cells)
            else:
                new_cells = list(cells) + [boundary] + list(absorbed)
            new_length = len(new_cells)
            sub_known = {c: known[c] for c in new_cells if c in known}
            candidates = [
                w for w in _slot_candidates(index, new_length, new_cells, sub_known)
                if w not in used_words
            ]
            for w in candidates:
                options.append((w, tuple(new_cells), boundary, new_boundary))
    rng.shuffle(options)
    for word, new_cells, old_boundary, new_boundary in options:
        if _new_crossing_impossibility(cur_slots, cell_to_slots, own_idx, new_cells, word, known, index):
            continue
        boundary_idx = new_cells.index(old_boundary)
        if _new_boundary_crossing_impossible(
            grid, rows, cols, old_boundary, word[boundary_idx], dr, dc, known, index
        ):
            continue
        return word, list(new_cells), old_boundary, new_boundary
    return None


def _sort_examples_by_process(examples):
    """Trie une liste d'exemples d'aperçu (voir generate_grid's propres
    listes `examples=[...]`) par numéro de process croissant (1..N), à la
    demande explicite de l'utilisateur : "Afficher les prévisualisations
    toujours dans l'ordre des process." Un ordre d'affichage stable,
    indépendant du score qui décide par ailleurs laquelle est la
    "meilleure" (voir le champ `is_best`, ajouté sur chaque exemple avant
    ce tri, jamais recalculé après — c'est ce tri qui doit s'adapter à la
    sélection déjà faite, pas l'inverse). Un exemple sans numéro de
    process (le tout premier palier d'une génération, avant toute
    soumission réelle à un worker) est placé en fin de liste plutôt que
    de faire échouer le tri — `None` n'est jamais comparable à un entier
    en Python."""
    return sorted(examples, key=lambda ex: (ex.get("process_number") is None, ex.get("process_number") or 0))


# Sur une grosse grille très noire, `_optimize_before_cleanup` (ci-dessous)
# pouvait tenter le retrait de chacune de ses cases noires non verrouillées
# à CHAQUE tentative distincte d'un palier — potentiellement plusieurs
# centaines de cases, chacune coûtant un `try_fill` complet — rendant cette
# étape, exécutée à *chaque* cycle, très lente sur ce genre de grille. À la
# demande explicite de l'utilisateur : "Ne faire l'optimisation complète
# que sur la grille finale [minimize_black_squares, qui garde son propre
# retrait exhaustif, inchangé]. Sur les optimisations à chaque cycle, au
# dessus de 50 cases noires, échantillonner 50 cases au hasard à
# optimiser." Une seule constante sert les deux rôles demandés — le seuil
# de déclenchement de l'échantillonnage ET la taille de l'échantillon
# lui-même partagent la même valeur (50).
PER_CYCLE_OPTIMIZATION_SAMPLE_SIZE = 50


def _optimize_before_cleanup(cand_grid, cand_diag, rows, cols, index, rng,
                              deadline_checks=6_000, cancel_event=None,
                              permanent_locked_letters=None, permanent_black_cells=None):
    """Nouvelle étape insérée AVANT même `_shorten_impossible_zones`/
    `_clean_blocked_slots` (donc avant tout nettoyage), à la demande
    explicite de l'utilisateur : "verrouiller tous les emplacements
    entièrement vides et les éventuelles cases noires avant/après ces
    emplacements vides [puis] lancer un cycle d'optimisation comme celui
    fait à toute fin [minimize_black_squares], qui ne doit pas toucher aux
    cases blanches ou noires verrouillées." Appliquée à CHAQUE tentative
    distincte d'un palier échoué (`failed_pairs`), pas seulement la
    meilleure — à la demande explicite de l'utilisateur, le même principe
    déjà établi pour `_clean_continue_candidate`/`_clean_all_candidates`.

    Un emplacement "entièrement vide" est un emplacement dont AUCUNE case
    ne porte de lettre, ni par sa propre affectation ni par un croisement
    (lu directement sur `cand_diag["example_grid"]`, jamais sur
    `cand_diag["assignment"]` seul, qui ne dit rien des lettres apportées
    par un emplacement perpendiculaire) — une catégorie différente d'un
    emplacement "impossible" (qui, lui, peut déjà porter certaines lettres
    sans qu'aucun mot réel ne les satisfasse toutes). Ses cases, et la ou
    les cases noires qui le bordent immédiatement (avant sa première case,
    après sa dernière, dans son propre sens), sont verrouillées : jamais
    proposées au retrait de case noire ci-dessous, jamais exigées par le
    remplissage (`excluded_slots`, comme un emplacement déjà connu
    impossible — voir `Filler.excluded_slots`) — elles ressortent de cette
    étape rigoureusement telles quelles.

    Les emplacements déjà connus impossibles (`cand_diag["impossible_
    slots"]`) sont eux aussi exclus du remplissage exigé ici (sans quoi le
    moindre emplacement impossible ailleurs dans la grille ferait échouer
    tout `try_fill` d'entrée de jeu, avant même d'avoir tenté le moindre
    retrait de case noire) — mais leurs cases noires bordantes ne sont
    PAS verrouillées : le nettoyage habituel qui suit cette étape reste
    entièrement libre d'agir dessus, exactement comme avant cette
    fonctionnalité. Si un retrait de case noire fusionne malgré tout un
    emplacement impossible avec un voisin (un emplacement dont les cases
    ne correspondent plus exactement à l'un des deux emplacements
    d'origine), ce nouvel emplacement fusionné n'est plus exclu et le
    remplissage tente réellement de le résoudre — un vrai retrait de case
    noire peut donc parfois débloquer un emplacement autrefois impossible.

    Procède en deux temps, tous deux via un même remplissage partiel
    (`preseed_assignment` verrouillant tout ce qui est déjà confirmé,
    `excluded_slots` couvrant à la fois les emplacements vides et les
    emplacements impossibles d'origine, recalculés à chaque appel par
    correspondance de cases pour rester valides malgré un éventuel
    décalage d'indices d'emplacement) :

    1. tente d'abord de compléter tout ce qui peut l'être ailleurs dans la
       grille, sans retirer la moindre case noire — un budget de recherche
       (`try_fill`) inexploité par la recherche d'origine (interrompue par
       `attempt_done_event`/`batch_abandoned_event`, ou par son propre
       budget) peut acheter là un progrès réel gratuit ;
    2. puis, comme `minimize_black_squares`, retire une à une (ordre
       mélangé, jusqu'à ce qu'un tour complet n'améliore plus rien) les
       cases noires NON verrouillées, en ne gardant le retrait que si la
       grille reste structurellement valide (`min_interior_free=1`, la
       même invariant absolu que `minimize_black_squares`) et à nouveau
       remplissable dans ces mêmes conditions.

       Contrairement à `minimize_black_squares` (jamais exécutée qu'une
       seule fois, sur la grille finale déjà réussie), cette étape tourne
       à *chaque* tentative de *chaque* palier — un vrai coût sur une
       grille dense en cases noires. Au-delà de `PER_CYCLE_OPTIMIZATION_
       SAMPLE_SIZE` (50) cases noires candidates au retrait, un seul
       échantillon aléatoire de 50 d'entre elles est essayé par tour,
       plutôt que la totalité — dès que l'une d'elles est effectivement
       retirée, l'échantillon en cours est abandonné et un nouveau tirage
       de 50, recalculé sur l'état à jour de la grille, prend
       immédiatement sa place, à la demande explicite de l'utilisateur.
       Sous ce seuil, le comportement reste exhaustif, inchangé : toutes
       les cases candidates d'un même tour sont essayées avant de vérifier
       si un nouveau tour est nécessaire.

    Retourne `(new_grid, new_diag)` — `new_diag` une copie de `cand_diag`
    dont seuls `assignment`/`impossible_slots`/`example_grid` sont mis à
    jour (tout le reste, `process_number` compris, est transmis tel quel)
    — prête à remplacer `(cand_grid, cand_diag)` partout où le nettoyage
    habituel les attendait, sans qu'aucun appelant n'ait besoin de
    connaître le détail de cette étape. `assignment`/`impossible_slots`
    sont recalculés depuis zéro sur l'état FINAL (jamais reprojetés depuis
    `cand_diag` par simple correspondance de cases) — voir le commentaire
    juste avant leur calcul, plus bas, pour le bug réel ("UNT") que cette
    recomputation corrige.

    `permanent_black_cells` (`None`/vide par défaut — aucun effet pour tout
    appelant existant avant "Finir la zone") : bug réel rapporté en direct
    par l'utilisateur — "le bouton 'Finir la zone' ne verrouille pas
    correctement les cases grisées, le remplissage automatique continue à
    essayer de les alimenter." Cette étape retire une à une (voir la boucle
    `while improved` ci-dessous) toute case noire qui n'est pas dans
    `locked_black_cells`, sans jamais avoir eu connaissance des cases
    gelées en permanence par "Finir la zone" (des cases hors de la zone
    sélectionnée, sans lettre, donc jamais couvertes par
    `permanent_locked_letters` ni par `locked_black_cells`, qui ne protège
    que les bornes d'un emplacement vide) — une case ainsi gelée, si elle
    ne borde par ailleurs aucun emplacement vide, était donc un candidat au
    retrait comme n'importe quelle autre case noire ordinaire, la
    rouvrant et permettant au remplissage de lui attribuer une lettre.
    Désormais exclue de `removable` au même titre que `locked_black_cells`."""
    cand_slots = extract_slots(cand_grid, rows, cols)
    example_grid = cand_diag["example_grid"]
    empty_cell_tuples = {
        tuple(cells) for cells in cand_slots
        if all(example_grid[r][c] == "." for (r, c) in cells)
    }
    impossible_cell_tuples = {
        tuple(cand_slots[i]) for i in cand_diag["impossible_slots"]
    }

    locked_black_cells = set()
    for cells in cand_slots:
        if tuple(cells) not in empty_cell_tuples:
            continue
        (r0, c0), (r1, c1) = cells[0], cells[-1]
        dr = 1 if len(cells) > 1 and cells[1][0] != r0 else 0
        dc = 1 if len(cells) > 1 and cells[1][1] != c0 else 0
        for (br, bc) in ((r0 - dr, c0 - dc), (r1 + dr, c1 + dc)):
            if 0 <= br < rows and 0 <= bc < cols and cand_grid[br][bc] == BLACK:
                locked_black_cells.add((br, bc))

    confirmed = {}
    for cells, word in zip(cand_slots, cand_diag["assignment"]):
        if word is not None:
            for cell, ch in zip(cells, word):
                confirmed[cell] = ch

    def _try_complete(g):
        trial_slots = extract_slots(g, rows, cols)
        preseed = [
            "".join(confirmed[cell] for cell in cells)
            if all(cell in confirmed for cell in cells) else None
            for cells in trial_slots
        ]
        excluded = {
            j for j, cells in enumerate(trial_slots)
            if tuple(cells) in empty_cell_tuples or tuple(cells) in impossible_cell_tuples
        }
        return try_fill(g, rows, cols, index, rng, deadline_checks,
                         preseed_assignment=preseed, excluded_slots=excluded,
                         cancel_event=cancel_event,
                         locked_letters=permanent_locked_letters or None)

    def _absorb(result):
        result_slots, result_assignment = result
        for cells, word in zip(result_slots, result_assignment):
            if word is not None:
                for cell, ch in zip(cells, word):
                    confirmed[cell] = ch

    grid = [row[:] for row in cand_grid]
    initial = _try_complete(grid)
    if initial is not None:
        _absorb(initial)

    improved = True
    while improved:
        improved = False
        removable = [
            (r, c) for r in range(rows) for c in range(cols)
            if grid[r][c] == BLACK and (r, c) not in locked_black_cells
            and not (permanent_black_cells and (r, c) in permanent_black_cells)
        ]
        rng.shuffle(removable)
        # Échantillonnage au-delà de PER_CYCLE_OPTIMIZATION_SAMPLE_SIZE
        # (voir la docstring ci-dessus et le commentaire de la constante) :
        # `sampling` distingue les deux régimes — sous le seuil, `break`
        # n'est jamais atteint plus bas, comportement exhaustif inchangé ;
        # au-dessus, le premier retrait réussi de l'échantillon interrompt
        # immédiatement ce tour (`break`) pour retirer un TOUT nouvel
        # échantillon de 50, recalculé sur l'état à jour de `grid` dès le
        # prochain passage dans `while improved`.
        sampling = len(removable) > PER_CYCLE_OPTIMIZATION_SAMPLE_SIZE
        if sampling:
            removable = removable[:PER_CYCLE_OPTIMIZATION_SAMPLE_SIZE]
        for (r, c) in removable:
            if cancel_event is not None and cancel_event.is_set():
                raise GenerationCancelled()
            if grid[r][c] != BLACK:
                continue
            saved = grid[r][c]
            grid[r][c] = WHITE
            if is_structurally_valid(grid, rows, cols, min_interior_free=1):
                result = _try_complete(grid)
                if result is not None:
                    _absorb(result)
                    improved = True
                    if sampling:
                        break
                    continue
            grid[r][c] = saved

    final_slots = extract_slots(grid, rows, cols)
    final_assignment = [
        "".join(confirmed[cell] for cell in cells)
        if all(cell in confirmed for cell in cells) else None
        for cells in final_slots
    ]
    # Recalcule les emplacements impossibles à partir de l'état RÉEL après
    # optimisation, à la demande explicite de l'utilisateur : "les mots
    # pouvant changer pendant l'optimisation, il est important que cette
    # optimisation recalcule les mots impossibles avant de passer la main
    # au nettoyage." L'ancienne version se contentait de reprojeter la
    # liste `impossible_slots` D'ORIGINE (par correspondance de cases,
    # `tuple(cells) in impossible_cell_tuples`) sans jamais revalider si un
    # emplacement encore listé "impossible" avait, entre-temps, été
    # entièrement recomposé — via `confirmed`, ci-dessus — par ses seuls
    # croisements (chacun individuellement valide) en un mot qui, lui,
    # n'existe dans le dictionnaire pour AUCUNE combinaison de ces lettres
    # précises. Bug réel rapporté en direct par l'utilisateur : une grille
    # optimisée contenant "UNT" (absent du dictionnaire français), déjà
    # signalé "réputé impossible" avant optimisation — mais dont
    # l'invalidité n'était plus reflétée nulle part une fois cette
    # fonction terminée, puisque `_clean_blocked_slots` (le nettoyage qui
    # suit) ne retire jamais un mot déjà présent sur l'emplacement
    # impossible lui-même, seulement ceux qui le CROISENT — "UNT" restait
    # donc tel quel, verrouillé au palier suivant. `_invalid_fully_known_
    # indices` (un emplacement entièrement couvert par `confirmed` mais
    # dont la combinaison de lettres ne correspond à aucun mot réel — même
    # classe de bug déjà corrigée une fois pour `_shorten_impossible_
    # zones`, voir sa propre docstring) efface ce genre de mot inventé
    # avant qu'il ne soit jamais transmis plus loin ; `_impossible_indices`
    # (un emplacement pas entièrement couvert, sans aucun candidat réel
    # une fois ses lettres connues appliquées) capture le cas complémentaire
    # — un emplacement toujours bloqué, touché ou non par l'optimisation.
    # L'union des deux, et non plus la seule reprojection de l'ancienne
    # liste, est la définition correcte d'"impossible" une fois cette
    # étape terminée — y compris le cas, déjà anticipé plus haut dans cette
    # docstring, où un retrait de case noire débloque légitimement un
    # emplacement autrefois impossible : ni l'une ni l'autre fonction ne le
    # signale alors, il disparaît naturellement de `final_impossible`.
    invalid_fully_known = set(
        _invalid_fully_known_indices(final_slots, index, confirmed, exempt=permanent_locked_letters)
    )
    for j in invalid_fully_known:
        final_assignment[j] = None
    final_impossible = sorted(
        invalid_fully_known | set(_impossible_indices(final_slots, index, confirmed))
    )
    # build_partial_letters_grid returns (letters_grid, forced_cells,
    # covered_count) — only the grid itself is needed here; `forced_letters`
    # is never passed (no statistical seeding happens in this optimization
    # pass), so its own `forced_cells` return is always empty and already
    # covered separately by `cand_diag.get("forced_cells", [])` at the call
    # site that builds this step's own preview.
    new_example_grid, _forced_cells, _covered = build_partial_letters_grid(
        grid, final_slots, final_assignment
    )
    # Corrigé à la demande explicite de l'utilisateur : "Pour fonctionner
    # correctement, l'optimisation doit déverrouiller toute la grille avant
    # de verrouiller les emplacements vides et les cases noires
    # avant/après." Avant ce correctif, `new_diag` reprenait — via
    # `**cand_diag` juste en dessous — l'ancien `cand_diag["locked_cells"]`
    # tel quel, calculé par la recherche d'ORIGINE à partir de son propre
    # `locked_letters`/`preseed_assignment` (donc reflétant le palier
    # PRÉCÉDENT), sans jamais être recalculé pour refléter ce que CETTE
    # étape d'optimisation verrouille réellement — un vrai reliquat périmé,
    # jamais remis à zéro. `locked_cells` est maintenant reconstruit
    # entièrement à partir de zéro ("déverrouiller toute la grille" — on ne
    # part d'aucun état hérité) puis rerempli avec exactement ce que cette
    # fonction protège tout du long : les cases de chaque emplacement
    # entièrement vide (`empty_cell_tuples`, aplati case par case) et leurs
    # cases noires bordantes (`locked_black_cells`) — la seule définition du
    # "verrouillé" qui ait un sens pour cette étape précise, indépendante de
    # tout ce qui a pu être verrouillé avant elle. Ces deux ensembles
    # restent valables pour la grille FINALE, pas seulement la grille de
    # départ : une case de `locked_black_cells` ne peut jamais être retirée
    # par la boucle `while improved` ci-dessus (explicitement exclue de
    # `removable`), et une case de `empty_cell_tuples` reste ce qu'elle
    # était au moment où cette étape a commencé à protéger sa zone, qu'elle
    # ait fini par recevoir une lettre réelle entre-temps (via `_try_
    # complete`) ou non.
    locked_cells = sorted(
        locked_black_cells | {cell for cells in empty_cell_tuples for cell in cells}
    )
    new_diag = {
        **cand_diag,
        "assignment": final_assignment,
        "impossible_slots": final_impossible,
        "example_grid": new_example_grid,
        "locked_cells": locked_cells,
    }
    return grid, new_diag


def _shorten_impossible_zones(grid, rows, cols, slots, assignment, impossible_slots,
                               index, rng, permanent_locked_letters=None):
    """Nouvelle étape insérée AVANT le nettoyage habituel des emplacements
    bloqués (`_clean_blocked_slots` ci-dessous), à la demande explicite de
    l'utilisateur, réservée à la reprise "telle quelle" (voir
    `_clean_continue_candidate`) — jamais au nettoyage complet, qui
    régénère de toute façon un motif neuf via `make_pattern` et peut donc
    déjà ajouter ses propres cases noires par ce biais, exactement le
    même principe déjà établi pour `BLACK_CELL_INSTEAD_OF_REMOVAL_
    PROBABILITY` juste au-dessus.

    Pour chaque emplacement de `impossible_slots`, tente de poser un mot
    plus court en tête ou en fin de la zone (voir `_find_shorter_word_for_
    zone`, qui tire au hasard parmi tous les candidats valables — toutes
    longueurs confondues — et rejette tout candidat qui créerait un
    nouvel emplacement croisant impossible) plutôt que de retirer
    directement les mots qui le croisent. Un mot trouvé est posé, une
    case noire est ajoutée à l'extrémité qui laisse une case vide ; un
    emplacement pour lequel AUCUN mot plus court ne convient (soit qu'il
    n'en existe aucun, soit que chacun créerait un nouveau blocage
    ailleurs) n'est pas touché du tout — ni case noire, ni mot — et
    attend simplement le prochain tour, ou le nettoyage habituel si plus
    aucun progrès n'est possible nulle part. Une fois tous les emplacements
    de ce tour ainsi traités, la détection des emplacements bloqués est
    relancée sur la grille mise à jour (le nouveau motif peut avoir résolu
    certains emplacements, ou raccourci d'autres qui restent encore trop
    contraints, ou même en avoir révélé de nouveaux via les vérifications
    de croisement) — jusqu'à ce qu'aucun mot plus court ne puisse plus
    être placé nulle part. La fonction rend alors la main, avec la liste
    des emplacements encore réellement impossibles à ce stade, pour que
    `_clean_blocked_slots` prenne le relais avec son propre mécanisme
    (retrait de mots, ou son alternative case noire).

    Le critère "impossible" utilisé ici (`_impossible_indices`, une simple
    intersection de candidats par position) est volontairement plus
    simple que celui du vrai solveur CSP (`Filler.impossible_zone_slots`,
    qui tient aussi compte de `used_words`/`forced_letters` au moment
    précis où la recherche a le plus progressé) — cette fonction opère
    APRÈS la recherche, sur un motif qui va de toute façon être remodelé,
    donc redériver ce même critère localement à chaque tour de boucle
    (plutôt que de relancer un vrai `Filler`, bien plus coûteux) est
    suffisant et cohérent avec `_low_candidate_slot_cells`/`_noise_slot_
    cells`, qui font déjà ce même choix ailleurs dans ce fichier.

    Renvoie `(grid, slots, assignment, impossible_slots)` — inchangés,
    par référence, si aucun mot plus court n'a jamais pu être placé (le
    cas courant), sinon un nouveau triplet motif/emplacements/mots
    reflétant l'état après ce nettoyage préalable, avec la liste des
    emplacements encore impossibles réindexée sur le nouveau motif.

    Le motif, les emplacements croisants (`cell_to_slots`) et les mots déjà
    utilisés sont recalculés à neuf avant CHAQUE emplacement traité — pas
    seulement une fois par tour — pour que l'examen d'un emplacement tienne
    toujours compte du mot que l'emplacement précédent vient tout juste de
    poser (bug réel constaté en direct : un emplacement traité juste après
    un autre, avec un instantané de motif encore périmé, pouvait accepter
    un mot en réalité déjà incompatible avec ce qui venait d'être posé).
    Chaque emplacement encore à traiter est identifié par ses propres cases
    (un tuple de coordonnées), jamais par un indice numérique dans la liste
    des emplacements — un indice se périmerait dès qu'une case noire
    ajoutée ailleurs modifie l'ordre/le nombre d'emplacements, le même
    piège d'indices déjà rencontré ailleurs dans ce fichier.

    `permanent_locked_letters` (`None` par défaut — aucun effet pour tout
    appelant existant avant "Finir la grille"/"Finir la zone") est fusionné
    dans `known` dès le départ — bug réel rapporté en direct par
    l'utilisateur : "'Finir la zone' semble correctement marquer les cases
    en vert, mais continue à placer des cases noires là où il y a du vert
    (donc sur des cases verrouillées, supposées intouchables)." Sans cette
    fusion, `known` ne reflétait que les emplacements déjà entièrement
    ASSIGNÉS (`assignment`) — une case verrouillée par l'utilisateur mais
    dont AUCUN des deux emplacements qui la croisent n'était encore
    entièrement résolu à ce palier précis (par ex. un emplacement encore
    partiellement rempli) restait absente de `known`, donc jamais protégée
    par le contrôle `boundary in known` de `_find_shorter_word_for_zone` :
    cette case pouvait alors être choisie comme nouvelle case-frontière et
    noircie, écrasant une lettre pourtant censée rester définitive."""
    known = {}
    for i, word in enumerate(assignment):
        if word is None:
            continue
        for cell, ch in zip(slots[i], word):
            known[cell] = ch
    if permanent_locked_letters:
        known.update(permanent_locked_letters)

    new_grid = [row[:] for row in grid]
    changed = False
    remaining_cells = [tuple(slots[i]) for i in impossible_slots]

    while remaining_cells:
        progressed = False
        for cells_tuple in remaining_cells:
            cur_slots = extract_slots(new_grid, rows, cols)
            try:
                own_idx = cur_slots.index(list(cells_tuple))
            except ValueError:
                # Cet emplacement n'existe plus tel quel (l'une de ses
                # propres cases a été noircie/couverte entre-temps) —
                # aucun de ses cas normaux ne devrait jamais l'atteindre
                # (voir la garde `boundary in known`/`grid[br][bc] ==
                # BLACK` ci-dessus, qui protège explicitement les propres
                # cases de CHAQUE emplacement traité), mais reste un
                # garde-fou défensif plutôt qu'un plantage.
                continue
            cells = cur_slots[own_idx]
            cell_to_slots = defaultdict(list)
            for i, s in enumerate(cur_slots):
                for c in s:
                    cell_to_slots[c].append(i)
            used_words = {
                "".join(known[c] for c in s) for s in cur_slots
                if all(c in known for c in s)
            }
            result = _find_shorter_word_for_zone(
                new_grid, rows, cols, cells, cur_slots, cell_to_slots, own_idx,
                index, known, used_words, rng,
            )
            if result is None:
                continue
            word, sub, boundary = result
            for c, ch in zip(sub, word):
                known[c] = ch
            new_grid[boundary[0]][boundary[1]] = BLACK
            progressed = True
            changed = True
        if not progressed:
            break
        cur_slots = extract_slots(new_grid, rows, cols)
        remaining_idx = _impossible_indices(cur_slots, index, known)
        remaining_cells = [tuple(cur_slots[j]) for j in remaining_idx]

    if not changed:
        return grid, slots, assignment, impossible_slots

    final_slots = extract_slots(new_grid, rows, cols)
    invalid_full = set(
        _invalid_fully_known_indices(final_slots, index, known, exempt=permanent_locked_letters)
    )
    final_assignment = [
        "".join(known[c] for c in cells)
        if all(c in known for c in cells) and j not in invalid_full else None
        for j, cells in enumerate(final_slots)
    ]
    final_impossible = sorted(set(_impossible_indices(final_slots, index, known)) | invalid_full)
    return new_grid, final_slots, final_assignment, final_impossible


def _lengthen_impossible_zones(grid, rows, cols, slots, assignment, impossible_slots,
                                index, rng, permanent_locked_letters=None,
                                permanent_black_cells=None):
    """Nouvelle étape, complément exact de `_shorten_impossible_zones`
    ci-dessus, à la demande explicite de l'utilisateur : "si un
    emplacement ne trouve pas de mot dans le glossaire thématique (ou le
    glossaire normal si ce n'est pas une grille thématique, donc
    emplacement devenu impossible), mais qu'au moins une des cases noires
    limitant la zone peut être supprimée ou déplacée..., tester des
    longueurs différentes en supprimant ou déplaçant la case noire."
    L'"impossible" utilisé ici est le même critère générique déjà établi
    pour `_shorten_impossible_zones` (`_impossible_indices` — aucun mot
    réel, quelle qu'en soit la source, ne correspond aux lettres déjà
    connues) : la génération thématique ne restreint jamais la notion
    d'"impossible" elle-même au seul glossaire thématique — `priority_
    words` n'est qu'une préférence d'ordre d'essai pendant le remplissage
    CSP (voir `Filler._backtrack`), jamais une restriction du dictionnaire
    réellement interrogé ici, exactement comme pour `_find_shorter_word_
    for_zone` qui n'a elle non plus jamais eu connaissance du thème.

    Appelée, dans `_clean_continue_candidate`, juste APRÈS `_shorten_
    impossible_zones` — sur ce qui reste encore impossible une fois le
    raccourcissement déjà tenté — plutôt qu'avant ou à sa place : un choix
    d'ordre délibéré mais non explicitement demandé, le moins risqué des
    deux (ne touche jamais au mécanisme de raccourcissement déjà établi et
    vérifié, n'agit qu'en complément sur ce qu'il n'a pas pu résoudre).
    Réservée à la reprise "telle quelle", jamais au nettoyage complet — le
    même principe déjà établi pour `_shorten_impossible_zones` elle-même
    (voir sa propre docstring) : un nettoyage complet régénère de toute
    façon un motif entièrement neuf via `make_pattern`, qui peut déjà
    allonger/raccourcir n'importe quelle zone par construction.

    Même structure de boucle par tours que `_shorten_impossible_zones` —
    `remaining_cells` identifie chaque emplacement encore à traiter par
    ses propres cases (jamais par indice numérique, qui se périmerait dès
    qu'une case noire ajoutée ailleurs décale l'ordre des emplacements),
    et `cur_slots`/`cell_to_slots`/`known`/`used_words` sont recalculés à
    neuf avant CHAQUE emplacement traité — pas seulement une fois par
    tour — pour que l'examen d'un emplacement tienne toujours compte de ce
    que l'emplacement précédent vient tout juste de poser. `_known_slot_
    boundary_cells` (voir sa propre docstring) est également recalculé à
    chaque emplacement traité, puisque le motif change sous ses pieds au
    fil de la boucle.

    Renvoie `(grid, slots, assignment, impossible_slots)` inchangés, par
    référence, si aucun allongement n'a jamais pu être appliqué (le cas
    courant), sinon un nouveau quadruplet motif/emplacements/mots/
    emplacements-encore-impossibles reflétant l'état après cette étape,
    exactement le même contrat de retour que `_shorten_impossible_zones`.

    `permanent_black_cells` (`None`/vide par défaut — aucun effet pour tout
    appelant existant avant "Finir la zone") : bug réel rapporté en direct
    par l'utilisateur ("le bouton 'Finir la zone' ne verrouille pas
    correctement les cases grisées") — une case bordant une zone impossible
    n'est protégée ici que si `_known_slot_boundary_cells` la reconnaît
    comme bornant un mot déjà connu ; une case gelée en permanence par
    "Finir la zone" (hors de la zone sélectionnée, sans lettre, donc jamais
    "connue") n'y figure pas et pouvait donc être déplacée/supprimée comme
    n'importe quelle autre case bordante ordinaire, rouvrant une case censée
    rester noire pour toujours. Fusionnée dans `protected` au même titre.

    `permanent_locked_letters` fusionné dans `known` dès le départ, pour la
    même raison et le même bug réel que `_shorten_impossible_zones`
    (voir sa propre docstring) : sans cette fusion, une case verrouillée
    par l'utilisateur mais dont aucun des deux emplacements qui la
    croisent n'était encore entièrement résolu restait invisible à
    `_known_slot_boundary_cells`/au contrôle `new_boundary in known` de
    `_find_longer_word_for_zone`, et pouvait donc être noircie comme
    n'importe quelle case blanche ordinaire lors d'un allongement."""
    known = {}
    for i, word in enumerate(assignment):
        if word is None:
            continue
        for cell, ch in zip(slots[i], word):
            known[cell] = ch
    if permanent_locked_letters:
        known.update(permanent_locked_letters)

    new_grid = [row[:] for row in grid]
    changed = False
    remaining_cells = [tuple(slots[i]) for i in impossible_slots]

    while remaining_cells:
        progressed = False
        for cells_tuple in remaining_cells:
            cur_slots = extract_slots(new_grid, rows, cols)
            try:
                own_idx = cur_slots.index(list(cells_tuple))
            except ValueError:
                continue
            cells = cur_slots[own_idx]
            cell_to_slots = defaultdict(list)
            for i, s in enumerate(cur_slots):
                for c in s:
                    cell_to_slots[c].append(i)
            used_words = {
                "".join(known[c] for c in s) for s in cur_slots
                if all(c in known for c in s)
            }
            protected = _known_slot_boundary_cells(new_grid, rows, cols, cur_slots, known)
            if permanent_black_cells:
                protected = protected | permanent_black_cells
            result = _find_longer_word_for_zone(
                new_grid, rows, cols, cells, cur_slots, cell_to_slots, own_idx,
                protected, index, known, used_words, rng,
            )
            if result is None:
                continue
            word, new_cells, old_boundary, new_boundary = result
            for c, ch in zip(new_cells, word):
                known[c] = ch
            new_grid[old_boundary[0]][old_boundary[1]] = WHITE
            if new_boundary is not None:
                new_grid[new_boundary[0]][new_boundary[1]] = BLACK
            progressed = True
            changed = True
        if not progressed:
            break
        cur_slots = extract_slots(new_grid, rows, cols)
        remaining_idx = _impossible_indices(cur_slots, index, known)
        remaining_cells = [tuple(cur_slots[j]) for j in remaining_idx]

    if not changed:
        return grid, slots, assignment, impossible_slots

    final_slots = extract_slots(new_grid, rows, cols)
    invalid_full = set(
        _invalid_fully_known_indices(final_slots, index, known, exempt=permanent_locked_letters)
    )
    final_assignment = [
        "".join(known[c] for c in cells)
        if all(c in known for c in cells) and j not in invalid_full else None
        for j, cells in enumerate(final_slots)
    ]
    final_impossible = sorted(set(_impossible_indices(final_slots, index, known)) | invalid_full)
    return new_grid, final_slots, final_assignment, final_impossible


def _clean_blocked_slots(slots, assignment, impossible_slots, locked_letters=None,
                          exclude_impossible_locked=False, index=None, rng=None,
                          grid=None, rows=None, cols=None, permanent_locked_letters=None):
    """Étapes 1 et 2 de `_build_retry_seed` (voir sa propre docstring pour
    l'historique complet), extraites dans leur propre fonction à la demande
    explicite de l'utilisateur : "à la fin d'un tour, nettoyer
    automatiquement les emplacements bloqués, mais pas les noires." —
    `generate_grid` appelle désormais cette fonction seule, à la fin de
    *chaque* palier (qu'il reparte "telle quelle" ou par un nettoyage
    complet), pour retirer tout mot croisant directement un emplacement
    impossible, sans jamais toucher aux cases noires elles-mêmes ni
    régénérer de motif — `_build_retry_seed` (le nettoyage complet, motif
    et cases noires compris) l'appelle en interne comme sa propre première
    étape, plutôt que de dupliquer ce calcul.

    Recompose d'abord, si `locked_letters` est fourni, le mot déjà
    entièrement déterminé de tout emplacement encore à `None` mais dont
    toutes les cases sont verrouillées (voir `_build_retry_seed`'s propre
    docstring pour le bug que ce préremplissage corrige) — un no-op sans
    `locked_letters` (le cas du nettoyage "telle quelle" en fin de palier,
    qui a déjà un `assignment` complet, mot par mot, sans rien à
    recomposer). Cette reconstruction valide désormais la combinaison
    (`index` fourni) via `_slot_candidates` avant de l'accepter — bug réel
    trouvé en direct, via l'API réelle, juste après avoir corrigé un bug
    similaire dans `_optimize_before_cleanup` (voir CLAUDE.md, "UNT") :
    "AMN", absent du dictionnaire, assemblé ici tel quel à partir de
    lettres verrouillées individuellement correctes mais jamais vérifiées
    ensemble — exactement la même classe de bug que `_invalid_fully_known_
    indices` corrige déjà pour `_shorten_impossible_zones`, mais jamais
    corrigée ici (le seul autre endroit du fichier qui recompose un mot
    entier à partir de `locked_letters` sans jamais interroger le
    dictionnaire). Un emplacement dont la combinaison verrouillée est
    invalide reste `None` — jamais signalé "impossible" explicitement ici
    (il ne l'est pas encore, au sens de `impossible_slots`), mais le
    palier suivant le redécouvrira naturellement : ses lettres restent
    verrouillées, donc `Filler.exclude_immediately_impossible_slots()` (au
    tout début de la prochaine recherche) l'exclura de lui-même dès que
    cette même combinaison invalide sera à nouveau soumise, le faisant
    apparaître dans `impossible_slots` par la voie normale plutôt que de
    laisser un mot inventé survivre.

    Retire ensuite TOUS les mots croisant chaque emplacement de
    `impossible_slots`, d'un coup — comportement à nouveau en vigueur, à
    la demande explicite de l'utilisateur : "Actuellement : pour un
    emplacement réputé injouable, on ne supprime qu'un seul mot croisant.
    Modifier : on retire tous les mots croisants (situation antérieure)."
    Une évolution intermédiaire de cette fonction avait remplacé ce
    retrait global par un retrait un mot à la fois, qui s'arrêtait dès
    qu'au moins un vrai candidat redevenait possible (voir CLAUDE.md pour
    l'historique complet de cette évolution, y compris la mesure en
    direct — 55 % de mots retirés en moins — qui l'avait motivée) ; ce
    changement intermédiaire est désormais annulé, à la demande explicite
    de l'utilisateur, sans toucher à l'alternative case noire ci-dessous
    (introduite après coup, mais indépendante du nombre de mots retirés
    par ailleurs) : elle reste tentée une fois par emplacement impossible,
    et seulement si elle échoue (ou n'est pas tentée) que TOUS les mots
    croisants encore assignés sont retirés en une seule fois, jamais un
    seul à la fois.

    Avant de retirer un mot croisant, tente — avec une probabilité
    `BLACK_CELL_INSTEAD_OF_REMOVAL_PROBABILITY` (1/10, abaissée de 1/3
    initial — voir le commentaire de cette constante) — une alternative,
    à la demande explicite de l'utilisateur (voir le commentaire de cette
    constante pour son raisonnement complet) : noircir une case de
    l'emplacement impossible lui-même plutôt que de retirer le mot qui le
    croise. Seulement disponible quand `grid`/`rows`/`cols` sont fournis
    (`None` par défaut — no-op pour tout appelant qui ne les fournit pas,
    en particulier `_build_retry_seed`/`_cleaned_playable_score`, qui
    restent volontairement retrait-de-mot uniquement). Parmi les cases de
    l'emplacement, celles *pas déjà* déterminées par un mot croisant
    encore assigné (`known`) sont essayées en priorité — noircir une case
    déjà couverte par un mot confirmé détruirait ce mot-là aussi, un
    résultat plus destructeur qu'une case encore libre — mais, à la
    demande explicite de l'utilisateur, une case déjà connue est tentée en
    second recours plutôt que de renoncer entièrement à cette alternative
    quand l'emplacement est déjà entièrement croisé (le cas le plus
    fréquent en fin de partie, quand peu de cases restent réellement
    libres) : dans ce cas, cette case noire retire alors, comme effet de
    bord, le mot croisant qui l'occupait — exactement comme le ferait un
    retrait de mot classique, mais en éliminant en plus, définitivement,
    cette case de l'emplacement impossible plutôt que de simplement
    libérer sa contrainte. Ce n'est que si aucune case de l'emplacement
    (libre ou déjà connue) ne reste structurellement valide une fois
    noircie que l'on retombe sur le retrait de mot habituel. Parmi
    chacun des deux groupes de cases, tirage sans biais positionnel
    (mélange avant essai, comme partout ailleurs dans ce fichier) puis
    premier candidat qui reste structurellement valide
    (`is_structurally_valid(..., min_interior_free=1)`) une fois noirci ;
    tout mot *autre* que celui de l'emplacement impossible lui-même mais
    passant par cette case précise est désassigné (il ne peut plus exister
    une fois la case noire). Contrairement au retrait de mot (qui ne fait
    que libérer une contrainte sur le MÊME emplacement i, qui continue
    d'exister sous sa forme actuelle ce palier-ci), poser une case noire
    *élimine* l'emplacement i sous sa forme actuelle — dès qu'une case a
    été noircie avec succès pour i, plus aucun retrait de mot n'est tenté
    pour lui ce tour-ci : ses fragments réels ne seront redécouverts qu'au
    prochain `extract_slots` sur la grille mise à jour, exactement comme
    pour toute autre case noire ajoutée ailleurs dans ce fichier.

    Zone strictement sans issue, à la demande explicite de l'utilisateur :
    une fois tous les mots croisants effectivement retirés (le cas normal
    ci-dessus, quand la case noire n'a pas été tentée ou a échoué), si
    l'emplacement n'a *toujours* strictement aucun candidat réel une fois
    toute contrainte de croisement ainsi levée (`count == 0` —
    typiquement une longueur que le dictionnaire ne couvre pas du tout),
    plus aucun retrait de mot ne pourra jamais débloquer cette zone :
    toutes ses cases restantes sont alors noircies directement (même
    garde-fou `is_structurally_valid(min_interior_free=1)` par case,
    jamais un passe-droit), plutôt que de la laisser resurgir identique à
    chaque nettoyage futur.

    `permanent_locked_letters` (`None` par défaut — aucun effet pour tout
    appelant existant avant "Finir la grille", voir la docstring de
    `generate_grid`) : ni la recomposition ci-dessus, ni l'alternative
    case noire, ni la case noire de la "zone sans issue" ne touchent
    jamais une case qu'il couvre — ces lettres, posées par l'utilisateur
    lui-même en mode Interactif, ne sont jamais remises en cause ni
    jamais noircies, quel que soit le mot qu'elles épellent ou la
    situation de l'emplacement (à `i`, ou d'un autre emplacement qui le
    croise) censé les concerner.

    Retourne `(cleaned_assignment, confirmed, new_black_cells)` —
    `cleaned_assignment` est une nouvelle liste (jamais une mutation de
    `assignment` reçu), avec un `None` explicite pour chaque emplacement
    retiré, prête à servir directement de `preseed_assignment` au palier
    suivant ; `new_black_cells` est l'ensemble (potentiellement vide) des
    cases nouvellement noircies par cette alternative — à fondre dans le
    motif transmis au palier suivant par l'appelant, `_clean_blocked_
    slots` elle-même ne mutant jamais `grid` en place (une copie de
    travail interne, jetée après l'appel)."""
    if locked_letters:
        assignment = list(assignment)
        impossible_set = set(impossible_slots) if exclude_impossible_locked else set()
        for i, cells in enumerate(slots):
            if (
                assignment[i] is None
                and i not in impossible_set
                and all(cell in locked_letters for cell in cells)
            ):
                # Un emplacement entièrement couvert par `permanent_locked_
                # letters` (voir la docstring de `generate_grid`) est
                # toujours recomposé, sans jamais interroger le
                # dictionnaire — ces lettres sont posées par l'utilisateur
                # lui-même en mode Interactif et doivent être considérées
                # comme bonnes, quel que soit le mot qu'elles épellent.
                if (
                    index is not None
                    and not (permanent_locked_letters and all(
                        cell in permanent_locked_letters for cell in cells
                    ))
                    and not _slot_candidates(index, len(cells), cells, locked_letters)
                ):
                    continue
                assignment[i] = "".join(locked_letters[cell] for cell in cells)
    else:
        assignment = list(assignment)

    cell_to_slots = defaultdict(list)
    for i, cells in enumerate(slots):
        for cell in cells:
            cell_to_slots[cell].append(i)

    new_black_cells = set()
    black_cell_capable = (
        grid is not None and rows is not None and cols is not None
    )
    working_grid = [row[:] for row in grid] if black_cell_capable else None

    if index is not None and rng is not None:
        for i in impossible_slots:
            crossing = sorted({
                j for cell in slots[i] for j in cell_to_slots[cell]
                if j != i and assignment[j] is not None
            })

            # Alternative case noire, à la demande explicite de
            # l'utilisateur (voir le commentaire de
            # BLACK_CELL_INSTEAD_OF_REMOVAL_PROBABILITY pour son
            # raisonnement complet) — tentée une seule fois par
            # emplacement impossible, indépendamment du retrait de mots
            # ci-dessous (qui, lui, retire à nouveau TOUS les mots
            # croisants d'un coup, voir la docstring ci-dessus).
            placed_black = False
            if crossing and black_cell_capable and rng.random() < BLACK_CELL_INSTEAD_OF_REMOVAL_PROBABILITY:
                known = {}
                for cell in slots[i]:
                    for j in cell_to_slots[cell]:
                        if j != i and assignment[j] is not None:
                            known[cell] = assignment[j][slots[j].index(cell)]
                            break
                # Une case de `permanent_locked_letters` (voir la docstring
                # de `generate_grid`) n'est jamais candidate à cette
                # alternative — la noircir détruirait un mot posé par
                # l'utilisateur lui-même en mode Interactif, y compris
                # quand elle n'appartient à l'emplacement impossible `i`
                # que par croisement avec un autre emplacement, réellement
                # verrouillé, qui la partage.
                blank_candidates = [
                    cell for cell in slots[i]
                    if cell not in known
                    and not (permanent_locked_letters and cell in permanent_locked_letters)
                ]
                known_candidates = [
                    cell for cell in slots[i]
                    if cell in known
                    and not (permanent_locked_letters and cell in permanent_locked_letters)
                ]
                rng.shuffle(blank_candidates)
                rng.shuffle(known_candidates)
                for (br, bc) in blank_candidates + known_candidates:
                    working_grid[br][bc] = BLACK
                    if is_structurally_valid(working_grid, rows, cols, min_interior_free=1):
                        new_black_cells.add((br, bc))
                        for j in cell_to_slots[(br, bc)]:
                            if j != i and assignment[j] is not None:
                                assignment[j] = None
                        placed_black = True
                        break
                    working_grid[br][bc] = WHITE
            if placed_black:
                continue

            for j in crossing:
                assignment[j] = None

            # Zone strictement sans issue, à la demande explicite de
            # l'utilisateur : tous les mots croisants viennent d'être
            # retirés (ci-dessus) et, une fois toute contrainte de
            # croisement ainsi levée, l'emplacement n'a toujours
            # strictement aucun candidat réel (`count == 0` —
            # typiquement une longueur que le dictionnaire ne couvre pas
            # du tout) : plus aucun retrait de mot ne pourra jamais
            # débloquer cette zone, donc on noircit directement toutes ses
            # cases restantes plutôt que de la laisser resurgir identique
            # à chaque nettoyage futur (voir CLAUDE.md pour le point fixe
            # réel que cette situation a fini par causer sur une grande
            # grille). Comme pour l'alternative ci-dessus, chaque case est
            # essayée avec `is_structurally_valid(min_interior_free=1)`
            # avant d'être noircie — jamais un passe-droit sur cet
            # invariant absolu, même ici.
            if black_cell_capable:
                count = _slot_candidate_count(index, len(slots[i]), slots[i], {})
                if count == 0:
                    for (br, bc) in slots[i]:
                        if working_grid[br][bc] == BLACK:
                            continue
                        if permanent_locked_letters and (br, bc) in permanent_locked_letters:
                            continue
                        working_grid[br][bc] = BLACK
                        if is_structurally_valid(working_grid, rows, cols, min_interior_free=1):
                            new_black_cells.add((br, bc))
                            for j in cell_to_slots[(br, bc)]:
                                if j != i and assignment[j] is not None:
                                    assignment[j] = None
                        else:
                            working_grid[br][bc] = WHITE
    else:
        for i in impossible_slots:
            for cell in slots[i]:
                for j in cell_to_slots[cell]:
                    if j != i and assignment[j] is not None:
                        assignment[j] = None

    confirmed = {}
    for i, word in enumerate(assignment):
        if word is None:
            continue
        for cell, ch in zip(slots[i], word):
            confirmed[cell] = ch

    return assignment, confirmed, new_black_cells


def _plug_isolated_cells(grid, rows, cols, slots, assignment, index, permanent_locked_letters=None):
    """Dernier recours tenté à la fin d'un palier en échec, à la demande
    explicite de l'utilisateur : "Lorsque toutes les recherches échouent en
    laissant une grille avec [ne reste] plus que des cases blanches
    isolées, boucher les cases isolées avec une case noire. Si le résultat
    donne une grille où tous les emplacements possibles sont remplis et
    valides, déclarer la grille réussie."

    Une case blanche encore sans lettre ("non remplie") est ici toute case
    qu'aucun emplacement assigné (`assignment[i] is not None`) ne couvre —
    y compris une case dont l'emplacement croisé (l'autre direction) EST
    assigné, ce qui lui donne déjà une vraie lettre malgré tout : `known`
    ci-dessous reflète exactement cette réalité, case par case, pas
    emplacement par emplacement.

    Une case non remplie est dite "isolée" si aucune de ses 4 cases
    voisines orthogonales n'est, elle aussi, non remplie — c'est-à-dire que
    tous ses voisins sont déjà noirs ou déjà pourvus d'une vraie lettre.
    C'est une définition volontairement prudente : si une case non remplie
    a ne serait-ce qu'un seul voisin non rempli, cela signifie qu'un vrai
    emplacement d'au moins 2 lettres reste encore ouvert à cet endroit (un
    mot qui pourrait encore, en principe, être trouvé) — ce n'est alors
    plus "rien que des cases isolées", et cette fonction n'y touche pas du
    tout : ni cette case, ni aucune autre de la grille, n'est modifiée. Une
    case isolée, à l'inverse, ne peut par construction jamais faire partie
    d'un emplacement encore ouvert d'au moins 2 cases : boucher une telle
    case ne raccourcit jamais un mot déjà confirmé, ni ne retire aucune
    vraie lettre déjà posée.

    Ne fait rien (renvoie `None`) dans trois cas : (1) il reste au moins une
    case non remplie qui n'est pas isolée (un vrai emplacement encore
    ouvert existe ailleurs — pas seulement des cases isolées) ; (2) noircir
    l'ensemble des cases isolées casserait la validité structurelle de la
    grille (connexité, ou une case blanche orpheline ailleurs —
    `is_structurally_valid` au niveau le plus strict, `min_interior_free=
    1`) ; (3) une fois les cases isolées bouchées, au moins un emplacement
    du nouveau motif (`extract_slots` recalculé sur la grille modifiée)
    reste soit sans lettre connue à toutes ses cases, soit rempli d'une
    combinaison qui ne correspond à aucun mot réel du dictionnaire — la
    grille obtenue n'est alors PAS "remplie et valide" au sens de la
    demande, donc pas question de la déclarer réussie. Sinon (tous les
    emplacements du nouveau motif sont entièrement connus et forment un mot
    réel), renvoie `(new_grid, new_slots, new_assignment)` — un résultat
    directement utilisable comme une réussite complète de génération, au
    même titre qu'un remplissage CSP qui aurait abouti normalement."""
    known = {}
    for i, cells in enumerate(slots):
        word = assignment[i]
        if word is not None:
            for pos, cell in enumerate(cells):
                known[cell] = word[pos]
    unfilled = {
        (r, c)
        for r in range(rows)
        for c in range(cols)
        if grid[r][c] == WHITE and (r, c) not in known
    }
    if not unfilled:
        return None
    for (r, c) in unfilled:
        for dr, dc in ((-1, 0), (1, 0), (0, -1), (0, 1)):
            if (r + dr, c + dc) in unfilled:
                return None
    new_grid = [row[:] for row in grid]
    for (r, c) in unfilled:
        new_grid[r][c] = BLACK
    if not is_structurally_valid(new_grid, rows, cols, min_interior_free=1):
        return None
    new_slots = extract_slots(new_grid, rows, cols)
    new_assignment = []
    for cells in new_slots:
        if any(cell not in known for cell in cells):
            return None
        # Un emplacement entièrement couvert par `permanent_locked_letters`
        # (voir la docstring de generate_grid) est toujours accepté tel
        # quel — ces lettres sont posées par l'utilisateur lui-même en
        # mode Interactif et doivent être considérées comme bonnes, quel
        # que soit le mot qu'elles épellent (probablement un nom propre).
        if permanent_locked_letters and all(cell in permanent_locked_letters for cell in cells):
            new_assignment.append("".join(known[cell] for cell in cells))
            continue
        candidates = _slot_candidates(index, len(cells), cells, known)
        if not candidates:
            return None
        new_assignment.append(next(iter(candidates)))
    return new_grid, new_slots, new_assignment


# `_impossible_cell_groups`/`_lock_one_impossible_cell` — the single-cell
# lock this project's history called "the mechanism tied to cleanup" — were
# removed entirely, at the user's explicit request, right after quoting
# their own prior description of the mechanism back and saying to delete
# it: no black cell is added anywhere by the cleanup path any more, either
# (see `generate_grid`'s own nettoyage branches below and `make_pattern`'s
# docstring above for the sibling per-step density draw removed the same
# session). See CLAUDE.md for the removed mechanism's full history.


def _build_retry_seed(grid, rows, cols, slots, assignment, impossible_slots, locked_letters=None,
                       exclude_impossible_locked=False, seed_grid=None, index=None, rng=None,
                       permanent_locked_letters=None, permanent_black_cells=None):
    """Construit le point de départ du palier suivant à partir de la
    meilleure tentative échouée du palier courant, à la demande explicite de
    l'utilisateur — nouvel algorithme de reprise entre paliers, distinct du
    mécanisme de "patch" essayé puis entièrement abandonné plus tôt dans
    l'historique de ce projet (voir la SKILL project-best-practices) : celui-
    là retouchait la MÊME tentative en ajoutant une case noire à la fois et
    en relançant une recherche complète depuis zéro à chaque fois ; celui-ci
    ne relance jamais la même tentative — il conserve ce qui a déjà été
    résolu avec confiance (des lettres réellement posées, pas une simple
    case noire de plus) et ne fait porter la prochaine recherche que sur ce
    qui reste réellement incertain.

    Trois étapes, dans l'ordre exact demandé :

    1. **Retirer les mots directement connectés aux emplacements en échec.**
       `impossible_slots` (voir Filler.impossible_zone_slots) désigne les
       emplacements non assignés dont le domaine était vide à l'instant où
       la recherche a le plus progressé (`best_assignment`) — c'est
       précisément la cause du blocage. Un emplacement *assigné* qui
       partage une case avec l'un d'eux (donc qui le croise, et dont la
       lettre partagée fait partie des contraintes qui ont vidé son domaine)
       est retiré à son tour : `to_remove` ne va pas plus loin qu'un niveau
       (« directement » — pas de propagation en cascade), à la demande
       explicite de l'utilisateur.
    2. **Ce qui reste devient les lettres pré-définies du prochain palier.**
       Chaque case encore couverte par un emplacement assigné (donc ni
       impossible ni retiré à l'étape 1) devient une entrée `{case: lettre}`
       dans le dict retourné — les seules lettres considérées comme du
       vrai progrès, jamais un indice statistique de `forced_letters` (qui
       n'a jamais été un fait acquis).
    3. **Conserver toute case noire existante adjacente à une lettre
       confirmée ; rouvrir toutes les autres.** Ce critère a une histoire en
       plusieurs temps. Une première version ne gardait noires que les deux
       cases qui bornent effectivement chaque mot restant — immédiatement
       avant sa première lettre et immédiatement après sa dernière, dans la
       direction propre de ce mot (horizontale ou verticale, jamais
       l'autre) — rouvrant toute case simplement adjacente sur le côté
       (au-dessus/en dessous d'une lettre du milieu d'un mot horizontal, par
       exemple), au motif qu'une telle case ne borne ce mot-là en rien.
       Cela laissait plus de marge de manœuvre (et donc plus de diversité
       entre les PARALLEL_ATTEMPTS tentatives parallèles du palier suivant)
       au placement de nouvelles cases noires — mais s'est révélé être la
       cause d'un problème différent, diagnostiqué par l'utilisateur à
       partir d'un cas réel : rouvrir une case latérale à côté d'une lettre
       confirmée ouvre un passage qui peut créer, dans l'autre direction, un
       tout nouvel emplacement immédiatement contraint par cette lettre (et
       potentiellement par d'autres lettres confirmées voisines) — un
       emplacement susceptible de n'avoir que très peu ou aucun mot candidat
       réel, obligeant le pré-remplissage du palier suivant à noircir
       beaucoup plus que nécessaire pour compenser (voir le
       `_prefill_unfillable_slots` ci-dessus, et le bug de colonne
       entièrement noire qu'il a fini par produire). Élargi, à la demande
       explicite de l'utilisateur, à la règle la plus large possible : toute
       case noire actuelle orthogonalement adjacente (les 4 côtés) à
       *n'importe quelle* case de `confirmed` reste noire ; seules les cases
       ne touchant aucune lettre confirmée du tout sont rouvertes. Cette
       règle englobe strictement la version bornes-de-mot (la case qui
       borne un mot est elle-même adjacente à sa première/dernière lettre),
       donc plus besoin de calculer les deux cas séparément.

       Un resserrement à deux branches (case bornant un mot, OU touchant au
       moins deux lettres confirmées à la fois) a été essayé un temps, puis
       abandonné presque aussitôt à la demande explicite de l'utilisateur,
       qui a reformulé la règle voulue plus simplement : "conserver les
       cases noires dont un des 4 côtés ouvre un emplacement où il y a une
       lettre (ça couvre le cas des cases en bout de mot) ; supprimer toutes
       les autres." Une première implémentation de cette reformulation ne
       vérifiait encore que la case immédiatement voisine — revenant, à tort,
       à l'exacte règle la plus large déjà en place. Corrigé à la demande
       explicite de l'utilisateur, qui a précisé le point manqué : "il peut
       y avoir des blancs entre la case noire et la lettre" — la vérification
       porte sur l'*emplacement entier* de chaque côté (la suite de cases
       blanches, potentiellement longue, jusqu'à la prochaine case noire ou
       le bord), pas seulement sur la case immédiatement adjacente —
       réutilisant le même parcours de côté que `_new_black_cell_breaks_
       locked_slot` (une marche le long des cases blanches consécutives dans
       chaque direction jusqu'à une case noire ou le bord), cette fois pour
       chercher une lettre confirmée quelque part dans le parcours plutôt
       que pour compter des candidats du dictionnaire.

       Cette version "un seul côté suffit" a immédiatement été resserrée
       une fois de plus, à la demande explicite de l'utilisateur, qui a
       identifié un cas concret qu'elle protégeait à tort : une case noire
       qui *voit* une lettre d'un seul côté (par exemple en croisant, à
       distance, un mot assigné dans l'autre sens) sans être elle-même la
       borne (début/fin) du mot correspondant ne protège en réalité rien —
       la rouvrir ne menace l'intégrité d'aucun mot existant, puisque la
       lettre aperçue appartient à un mot qui ne s'étend pas jusqu'à cette
       case dans sa propre direction. La règle finale ne conserve donc une
       case noire que dans deux cas, une union de deux conditions
       indépendantes : (1) elle borne effectivement un mot restant —
       immédiatement avant sa première lettre ou immédiatement après sa
       dernière, dans la direction propre de ce mot (le même calcul que la
       toute première version de cette étape, jamais retiré, seulement
       complété) ; (2) elle a une lettre confirmée *des deux côtés à la
       fois* d'un même axe — en haut ET en bas, ou à gauche ET à droite (pas
       besoin des deux axes en même temps) — une case "prise en sandwich"
       entre deux segments de mots sur le même axe, où la rouvrir
       fusionnerait deux emplacements distincts en un seul qui ne
       correspond peut-être à aucun mot réel, perturbant les deux côtés à
       la fois. Une case qui ne voit une lettre que d'un seul côté d'un
       axe, sans en borner le mot, est désormais rouverte — y compris le
       cas de croisement à distance qui motivait le passage à la version
       précédente ; ce cas-là n'a jamais menacé l'intégrité d'un mot
       existant, seule la version "un seul côté suffit" le traitait à tort
       comme s'il le fallait. Confirmé par l'utilisateur avec une
       reformulation équivalente : "une case noire se trouvant quelque part
       entre 2 mots existants (horizontalement ou verticalement) doit être
       conservée ; une case noire se trouvant au bout d'un mot (début ou
       fin) doit être conservée ; les autres cases noires peuvent être
       supprimées" — exactement les conditions (2) et (1) ci-dessus. Vérifié
       avec trois grilles construites à la main : une case ne voyant une
       lettre que d'un seul côté (croisement à distance, pas de borne) se
       rouvre désormais ; une case bornant effectivement un mot reste
       noire ; une case prise en sandwich entre deux mots assignés sur le
       même axe vertical reste noire.

       Exception ajoutée à la demande explicite de l'utilisateur : une case
       noire par ailleurs candidate à la réouverture (non adjacente à une
       lettre confirmée) reste tout de même noire si ses 4 voisines (haut,
       bas, gauche, droite) sont *elles-mêmes* toutes noires dans la grille
       d'origine (`_fully_surrounded_by_black`) — la rouvrir créerait une
       case blanche isolée des 4 côtés, un "trou d'une seule lettre" qui
       violerait l'invariant absolu établi ailleurs dans ce fichier (voir
       is_structurally_valid) : une case blanche ne peut jamais être courte
       (1 lettre) dans les deux sens à la fois. Une case en bord de grille
       ne peut jamais remplir cette condition (au moins un voisin hors
       grille), donc cette exception ne s'applique qu'à une case
       strictement intérieure — cohérent avec le fait que ce risque de trou
       isolé n'existe que loin du bord.

    Retourne `(nouveau_motif, lettres_verrouillées)` — `nouveau_motif` sert
    de `seed_grid` et `lettres_verrouillées` de `locked_letters`/
    `forced_letters` à `make_pattern`/`_pattern_attempt` du palier suivant
    (voir generate_grid).

    Bug réel trouvé et corrigé, à partir d'un cas concret fourni par
    l'utilisateur ("beaucoup de lettres, peu de conflit, et l'étape
    d'après, presque tout a été supprimé") et confirmé par un audit
    multi-paliers en direct (pas seulement raisonné) : `assignment` (le
    `best_assignment` du `Filler` de CETTE tentative) ne contient un mot
    pour un emplacement que si le backtracking a réellement fini par
    l'assigner explicitement pendant SA PROPRE recherche — un emplacement
    déjà entièrement déterminé par les lettres verrouillées du palier
    précédent (`locked_letters`, passées en tant que contrainte dure) n'est
    JAMAIS "réassigné" par `_backtrack` si la recherche échoue avant même
    d'atteindre cet emplacement (le cas `checks=1`/`reason="search_
    exhausted"` très rapide : le tout premier domaine vérifié est déjà
    vide). Dans ce cas, `assignment` revient entièrement à `None`, y
    compris pour les emplacements déjà verrouillés, alors que ces lettres
    étaient parfaitement acquises — l'étape 2 ci-dessus les jetait donc à
    tort, systématiquement, à chaque échec immédiat de ce type. Confirmé en
    direct : sur un audit de 8 paliers enchaînés (grille réelle, dictionnaire
    réel), 3 des 8 (paliers 2, 4, 7) montraient `assigned_slots=0` pour les
    6 candidats alors que le palier précédent avait verrouillé 65, 44 et 69
    lettres respectivement — la totalité disparaissait, pas parce qu'elle
    croisait un emplacement impossible, mais parce qu'elle n'apparaissait
    jamais du tout dans `assignment`. Corrigé en traitant tout emplacement
    entièrement couvert par `locked_letters` comme s'il avait été assigné
    au mot que ces lettres épellent, avant d'appliquer exactement les mêmes
    règles (étapes 1 à 3) qu'à n'importe quel autre mot réellement assigné
    — un emplacement verrouillé qui croise un emplacement impossible reste
    retiré comme n'importe quel autre, il n'est pas protégé au-delà de sa
    part légitime.

    Ce premier correctif a lui-même introduit un second bug, trouvé par le
    même type d'audit multi-paliers en direct : un emplacement peut être à
    la fois entièrement couvert par `locked_letters` *et* lui-même présent
    dans `impossible_slots` — la combinaison exacte de lettres verrouillées
    à cet emplacement ne correspond, en fait, à aucun mot réel du
    dictionnaire (c'est précisément *pourquoi* il est impossible). Le
    correctif ci-dessus le "réassignait" quand même depuis `locked_letters`
    sans vérifier ce cas, préservant indéfiniment cette combinaison
    invalide d'un palier à l'autre — puisque cet emplacement n'est jamais
    dans `to_remove` (qui ne retire que les AUTRES emplacements croisant un
    emplacement impossible, jamais l'emplacement impossible lui-même), rien
    ne changeait plus jamais d'un palier au suivant, un vrai point fixe
    bloqué. Reproduit en direct : sur une grille bloquée à ce stade précis,
    29 lettres verrouillées et 2 emplacements impossibles (chacun 2 cases,
    déjà entièrement verrouillées) restaient **identiques bit à bit** sur
    12 paliers consécutifs, jusqu'à épuiser les 40 tentatives sans jamais
    trouver de solution — un cas qui réussissait auparavant.

    Corriger ceci en excluant *systématiquement* un tel emplacement de la
    réassignation (`exclude_impossible_locked=True` en permanence) a été
    essayé, puis affiné après avoir constaté, par comparaison directe
    avant/après sur plusieurs scénarios réels, que ce n'était pas non plus
    la bonne réponse partout : un scénario différent (10×10, vocabulaire
    volontairement restreint à 400 mots) qui réussissait sans cette
    exclusion s'est mis à échouer systématiquement avec elle — l'exclusion,
    appliquée à chaque palier sans distinction, retire aussi des emplacements
    dont la présence ne bloquait en réalité rien du tout, gaspillant du
    contenu par ailleurs récupérable. `exclude_impossible_locked` (`False`
    par défaut, donc le comportement normal — sans exclusion, qui gagne dans
    la majorité des scénarios réels observés) n'est donc utilisé qu'en
    dernier recours, à la demande explicite de l'utilisateur : seulement
    quand `generate_grid` détecte qu'un palier n'a produit *aucun*
    changement par rapport au précédent (les lettres confirmées sont
    rigoureusement identiques, un vrai point fixe), il relance ce même
    nettoyage une seconde fois pour ce palier, cette fois avec
    `exclude_impossible_locked=True`, uniquement pour débloquer ce cas
    précis plutôt que d'appliquer la règle plus agressive partout."""
    assignment, confirmed, _ = _clean_blocked_slots(
        slots, assignment, impossible_slots, locked_letters=locked_letters,
        exclude_impossible_locked=exclude_impossible_locked, index=index, rng=rng,
        permanent_locked_letters=permanent_locked_letters,
    )

    def _direction_has_confirmed_letter(r, c, dr, dc):
        rr, cc = r + dr, c + dc
        while 0 <= rr < rows and 0 <= cc < cols and grid[rr][cc] == WHITE:
            if (rr, cc) in confirmed:
                return True
            rr += dr
            cc += dc
        return False

    protected_black_cells = set()
    for i, word in enumerate(assignment):
        if word is None:
            continue
        cells = slots[i]
        direction = "across" if len(cells) > 1 and cells[1][0] == cells[0][0] else "down"
        dr, dc = (0, 1) if direction == "across" else (1, 0)
        (r0, c0), (r1, c1) = cells[0], cells[-1]
        for br, bc in ((r0 - dr, c0 - dc), (r1 + dr, c1 + dc)):
            if 0 <= br < rows and 0 <= bc < cols:
                protected_black_cells.add((br, bc))

    for r in range(rows):
        for c in range(cols):
            if grid[r][c] != BLACK:
                continue
            vertical_both = _direction_has_confirmed_letter(
                r, c, -1, 0
            ) and _direction_has_confirmed_letter(r, c, 1, 0)
            horizontal_both = _direction_has_confirmed_letter(
                r, c, 0, -1
            ) and _direction_has_confirmed_letter(r, c, 0, 1)
            if vertical_both or horizontal_both:
                protected_black_cells.add((r, c))

    def _fully_surrounded_by_black(r, c):
        for dr, dc in ((-1, 0), (1, 0), (0, -1), (0, 1)):
            rr, cc = r + dr, c + dc
            if not (0 <= rr < rows and 0 <= cc < cols) or grid[rr][cc] != BLACK:
                return False
        return True

    # Protection inconditionnelle des cases noires déjà présentes *avant*
    # que ce palier ne commence (`seed_grid`, le motif reçu en entrée par
    # `_pattern_attempt`/`make_pattern` pour CE palier précis, avant son
    # propre pré-remplissage/placement au ratio/« nettoyage curatif ») — à
    # la demande explicite de l'utilisateur, après un bug réel constaté en
    # direct : "certaines cases noires initiales disparaissent... il ne
    # faut toucher qu'aux cases noires ajoutées [ce palier], pas à celles
    # présentes avant de commencer cette phase." Root cause : la protection
    # ci-dessus (les deux boucles précédentes) ne se fie qu'à `assignment`
    # (le résultat final de la recherche CSP de CETTE tentative précise)
    # pour décider quels mots "survivent" — mais le « nettoyage curatif »
    # (voir `_remove_a_crossing_word`, appelé depuis `_prefill_unfillable_
    # slots`) peut retirer un mot de `locked_letters` *à l'intérieur même*
    # du worker, avant que la recherche ne démarre — un mot pourtant déjà
    # confirmé depuis un palier précédent, présent dans `carry_locked_
    # letters` (la copie du parent, jamais mutée par le worker séparé — voir
    # plus bas), mais absent du worker's own `locked_letters` copy une fois
    # nettoyage curatif passé par là. Si la recherche CSP échoue ensuite à
    # réattribuer ce même emplacement (`assignment[i]` reste `None`), ses
    # cases-frontière — qui faisaient pourtant déjà partie du motif *avant*
    # que ce palier ne commence, sans aucun rapport avec le nettoyage
    # curatif de cette tentative précise — perdaient toute protection et se
    # retrouvaient rouvertes, comme si elles avaient été ajoutées puis
    # échouées ce palier-ci. Reproduit en direct : un diagnostic dédié,
    # comparant les cases noires de l'aperçu "pattern" (motif d'entrée de
    # palier) à celles de l'aperçu "pattern_generated" (motif produit par
    # CETTE tentative), a bien confirmé des cases présentes "avant"
    # totalement absentes "après" pour plusieurs tentatives/paliers réels.
    # `seed_grid` (`None` par défaut — tout appelant existant avant ce
    # correctif, si jamais il y en avait un sans ce paramètre, n'est pas
    # affecté) est le motif d'ENTRÉE de la tentative dont `grid`/`assignment`
    # sont le résultat — n'importe quelle case déjà noire dedans est protégée
    # inconditionnellement ici, indépendamment de la survie ou non d'un mot
    # dans `assignment` : elle n'a, par construction, jamais pu être
    # "ajoutée sans succès" par CE palier, puisqu'elle existait déjà avant
    # qu'il ne commence.
    if seed_grid is not None:
        for r in range(rows):
            for c in range(cols):
                if seed_grid[r][c] == BLACK:
                    protected_black_cells.add((r, c))

    # `permanent_black_cells` (`None`/vide par défaut — aucun effet pour
    # tout appelant existant avant "Finir la zone") : défense supplémentaire,
    # au même titre que la protection `seed_grid` juste au-dessus — la
    # vraie source du bug ("cases noires sur les cases verrouillées") était
    # ailleurs (voir `_pattern_attempt`'s propre docstring, un worker
    # "réinitialisé" qui ignorait totalement ces cases), mais rien
    # n'empêche cette étape-ci de rouvrir l'une d'elles si jamais elle
    # arrivait ici sans être déjà noire dans `seed_grid` pour une raison
    # non encore identifiée — jamais un passe-droit à retirer une fois la
    # cause première corrigée.
    if permanent_black_cells:
        protected_black_cells |= permanent_black_cells

    new_grid = [row[:] for row in grid]
    for r in range(rows):
        for c in range(cols):
            if new_grid[r][c] == BLACK and (r, c) not in protected_black_cells:
                if _fully_surrounded_by_black(r, c):
                    continue
                new_grid[r][c] = WHITE

    return new_grid, confirmed


# Score utilisé pour choisir la meilleure grille nettoyée parmi plusieurs
# candidates — sommme des carrés des longueurs des mots réellement "en
# place" après nettoyage (toutes leurs cases figurent dans `cand_confirmed`).
# Hissé au niveau du module (auparavant une fermeture locale, propre au seul
# nettoyage complet, `else:` dans `generate_grid`) à la demande explicite de
# l'utilisateur, une fois la même logique nécessaire aussi pour la reprise
# "telle quelle" (voir `_clean_continue_candidate`/`_continue_seed_pool` plus
# bas) — favorise quelques mots longs plutôt que beaucoup de mots courts pour
# le même total de lettres, la même formule déjà utilisée pour départager les
# tentatives parallèles réussies dans `generate_grid`.
def _words_in_place_score(cand_slots, cand_confirmed):
    return sum(
        len(cells) ** 2 for cells in cand_slots
        if all(cell in cand_confirmed for cell in cells)
    )


# Départage `_words_in_place_score` à égalité — le nombre de cases noires du
# candidat, à la demande explicite de l'utilisateur, après un vrai blocage
# constaté en direct sur une grande grille très majoritairement verrouillée
# (voir CLAUDE.md pour l'historique complet). Également hissé au niveau du
# module pour la même raison que `_words_in_place_score` ci-dessus.
def _candidate_black_count(cand_seed):
    return sum(row.count(BLACK) for row in cand_seed)


# Trie une liste de candidats nettoyés par (`_words_in_place_score`,
# `_candidate_black_count`) décroissant — chaque candidat est un tuple dont
# les 3 premiers éléments sont `(seed_grid, confirmed, slots)`, dans cet
# ordre précis (les éléments suivants, s'il y en a, ne sont jamais lus ici —
# voir `_clean_continue_candidate` pour un exemple à 6 éléments).
def _sorted_by_score(cleaned_candidates):
    return sorted(
        cleaned_candidates,
        key=lambda sc: (
            _words_in_place_score(sc[2], sc[1]),
            _candidate_black_count(sc[0]),
        ),
        reverse=True,
    )


# Réduit une liste déjà triée (la meilleure d'abord) au vivier transmis au
# prochain palier, en éliminant les `FULL_RESET_ATTEMPT_COUNT` moins bonnes —
# ce nombre éliminé correspond exactement au nombre de tentatives que le
# prochain palier réservera de toute façon à un nouveau départ complètement
# vierge (voir `reset_count` dans `generate_grid`), les grilles survivantes
# remplissant alors, une par une, très exactement le reste des places du
# prochain palier. `max(1, ...)` : ne jamais vider entièrement le vivier,
# même si `FULL_RESET_ATTEMPT_COUNT` dépasse le nombre de candidats
# disponibles — il reste toujours au moins la meilleure grille elle-même.
# `extract` isole, de chaque tuple candidat, exactement ce dont le prochain
# palier a besoin pour relancer une tentative à partir de cette entrée —
# `(seed_grid, locked_letters)` par défaut (le nettoyage complet, motif
# neuf), `(seed_grid, preseed_assignment, excluded_slots)` pour la reprise
# "telle quelle" (voir `_continue_seed_pool`).
def _seed_pool(sorted_candidates, extract=lambda sc: (sc[0], sc[1])):
    keep = max(1, len(sorted_candidates) - FULL_RESET_ATTEMPT_COUNT)
    return [extract(sc) for sc in sorted_candidates[:keep]]


# Construit, pour un palier donné, le numéro de "lignée" (voir generate_grid,
# `process_number`) de chacune de ses PARALLEL_ATTEMPTS tâches AVANT même de
# les soumettre — à la demande explicite de l'utilisateur : "il faut que les
# grilles portent leur propre numéro, et le gardent jusqu'à la fin de la
# résolution", après un rapport direct constaté sur l'affichage ("les
# grilles changent de numéro d'un cycle sur l'autre"). Root-causé : le
# numéro affiché venait auparavant du PID réel du worker qui produisait
# chaque diagnostic (`worker_pid_numbers`) — un PID stable pour toute la
# durée d'un `generate_grid()`, mais dont l'AFFECTATION à une tâche donnée ne
# l'est pas : `ProcessPoolExecutor` distribue chaque tâche au premier worker
# disponible, jamais nécessairement le même d'un palier à l'autre pour "la
# même lignée logique" — un candidat qui continue le même vivier peut donc
# se voir traité par un PID différent à chaque palier, changeant son numéro
# affiché sans que rien n'ait vraiment changé sur la grille elle-même.
#
# `dispatch_lineage[i]` (i = index de soumission, 0..PARALLEL_ATTEMPTS-1,
# jamais l'ordre d'achèvement) vaut `None` pour une tâche réinitialisée
# (`i < reset_count`, motif entièrement neuf — aucune lignée à hériter) et
# `pool_lineage[(i - reset_count) % len(pool_lineage)]` sinon — le même
# calcul cyclique que celui déjà utilisé pour distribuer les grilles du
# vivier aux tâches non réinitialisées (voir `pool`/`continue_pool` dans
# generate_grid), pour que chaque tâche hérite exactement du numéro de
# l'entrée du vivier dont elle repart.
def _build_dispatch_lineage(seeds_count, reset_count, pool_lineage):
    return [
        None if i < reset_count else pool_lineage[(i - reset_count) % len(pool_lineage)]
        for i in range(seeds_count)
    ]


# Complète `raw_lineage` (les numéros hérités par chaque candidat SURVIVANT
# de ce palier, dans le même ordre que le vivier reconstruit — voir
# generate_grid) : une entrée `None` signifie que ce candidat vient d'une
# tâche réinitialisée (motif entièrement neuf, voir `_build_dispatch_
# lineage`) qui n'avait donc aucune lignée à hériter au départ. À la
# demande explicite de l'utilisateur : "La grille entièrement nouvelle doit
# reprendre le numéro de la grille qui disparaît (normalement, la moins
# bonne)." `previous_lineage` est l'ensemble des numéros qui étaient
# ACTIFS ce palier (`dispatch_lineage`, voir ci-dessus, tâches
# réinitialisées comprises — leur `None` est ignoré via le filtre `if n is
# not None` ci-dessous) ; tout numéro qui y figurait mais n'apparaît plus
# parmi les survivants RÉSOLUS de `raw_lineage` s'est donc "libéré" (sa
# propre grille n'a pas survécu au tri par score de `_seed_pool` — la moins
# bonne, par construction, puisque `_seed_pool` élimine toujours les moins
# bonnes en premier) et est réattribué, dans l'ordre, à chaque candidat
# encore non résolu. `next_lineage_number` (compteur persistant tout le
# long d'un `generate_grid()`, jamais réinitialisé) ne sert que de filet de
# sécurité si jamais aucun numéro ne s'était libéré (cas dégénéré, non
# rencontré en pratique avec FULL_RESET_ATTEMPT_COUNT actuel) — pour ne
# jamais laisser un `None` non résolu passer dans le vivier retourné.
# Retourne `(lineage_finalisée, next_lineage_number_mis_à_jour)`.
def _reassign_lineage_numbers(raw_lineage, previous_lineage, next_lineage_number):
    resolved = {n for n in raw_lineage if n is not None}
    freed = iter(sorted({n for n in previous_lineage if n is not None} - resolved))
    finalized = []
    for n in raw_lineage:
        if n is not None:
            finalized.append(n)
            continue
        replacement = next(freed, None)
        if replacement is None:
            replacement = next_lineage_number
            next_lineage_number += 1
        finalized.append(replacement)
    return finalized, next_lineage_number


# Nettoie UNE tentative individuelle d'un palier "reprise telle quelle" (voir
# generate_grid, `if still_has_hope:`) — mêmes étapes que `_clean_blocked_
# slots` (retrait des mots croisant un emplacement impossible, avec son
# alternative 1/10 de case noire), appliquées ici à chaque tentative
# distincte de ce palier plutôt qu'à la seule "meilleure" — à la demande
# explicite de l'utilisateur : "Quand il n'y a pas de déclenchement d'un
# nettoyage complet, chaque process doit repartir à l'étape suivante avec sa
# grille partiellement nettoyée (sauf le pourcentage de grilles entièrement
# neuves)" — le même principe déjà en place pour le nettoyage complet (voir
# `_clean_all_candidates`, dans `generate_grid`) désormais étendu à la
# reprise "telle quelle", jusque-là seule à ne conserver qu'une seule grille
# (`selected_grid`/`selected_diag`, la "meilleure" au sens de `failed_pairs`)
# pour tous les workers non réinitialisés du palier suivant.
#
# Retourne un tuple à 6 éléments — `(cand_seed_grid, cand_confirmed,
# cand_slots, cand_preseed_assignment, cand_excluded_slots, cand_process_
# number)` — les 3 premiers dans le même ordre que les candidats du
# nettoyage complet (compatibles avec `_words_in_place_score`/
# `_sorted_by_score`), les 2 suivants la forme attendue par
# `_pattern_continue` (`cand_seed_grid` doublé, jamais répété dans le
# tuple), le dernier purement diagnostique (voir `carry_seed_pool_
# process_numbers`) — le numéro du process qui a produit `cand_grid`,
# transmis tel quel depuis `cand_diag.get("process_number")`.
#
# Si `_clean_blocked_slots` ajoute une case noire (son alternative 1/10), la
# numérotation des emplacements change — même remède déjà utilisé pour la
# seule grille gagnante avant cette fonctionnalité (voir l'historique complet
# dans CLAUDE.md, "même piège d'indices déjà rencontré... pour le mécanisme
# de verrou à une case, depuis retiré") : reconstruire `cand_slots`/
# `cand_preseed_assignment`/`cand_excluded_slots` depuis un `extract_slots`
# frais sur le motif réellement mis à jour, en s'appuyant sur `confirmed`
# (indexé par case, jamais par indice d'emplacement, donc immunisé contre ce
# décalage) plutôt que sur les anciens indices.
#
# `_shorten_impossible_zones` (juste avant `_clean_blocked_slots` plus haut
# dans ce fichier) tente d'abord de raccourcir chaque emplacement impossible
# (un mot plus court en tête/fin de zone, borné par une case noire) avant
# tout retrait de mot classique — jamais lors du nettoyage complet (voir sa
# propre docstring), seulement ici, pour cette même raison déjà établie pour
# `BLACK_CELL_INSTEAD_OF_REMOVAL_PROBABILITY`. `_lengthen_impossible_zones`
# (juste après elle, même fichier) tente ensuite, sur ce qui reste encore
# impossible, l'opération inverse — allonger la zone en repoussant/
# supprimant l'une de ses cases noires bordantes existantes plutôt qu'en
# ajouter une nouvelle à l'intérieur — à la demande explicite de
# l'utilisateur (voir sa propre docstring pour le détail complet). Le
# motif/liste d'emplacements encore impossibles éventuellement mis à jour
# par ces deux étapes (`cand_grid`/`cand_impossible`) remplacent alors
# `cand_diag["assignment"]`/`cand_diag["impossible_slots"]` pour le reste
# de cette fonction — un no-op complet (mêmes objets, mêmes indices) tant
# qu'aucune des deux n'a rien pu changer.
def _clean_continue_candidate(cand_grid, cand_diag, rows, cols, index, rng,
                               permanent_locked_letters=None, permanent_black_cells=None):
    """Nettoie une seule tentative échouée d'un palier "reprise telle
    quelle" (voir `_continue_seed_pool`) — retire ce qui croise un
    emplacement impossible (`_clean_blocked_slots`), après avoir d'abord
    tenté de raccourcir (`_shorten_impossible_zones`) puis d'allonger
    (`_lengthen_impossible_zones`) ces mêmes emplacements.

    `permanent_locked_letters` (`None` par défaut — aucun effet pour tout
    appelant existant avant "Finir la grille") est transmis tel quel à
    chacune de ces trois fonctions, pour qu'aucune case qu'il couvre ne
    soit jamais noircie ni jamais signalée "impossible" au seul motif
    qu'elle ne correspond à aucun mot réel du dictionnaire — voir la
    docstring de `generate_grid`.

    `permanent_black_cells` (`None`/vide par défaut — aucun effet pour tout
    appelant existant avant "Finir la zone") est transmis uniquement à
    `_lengthen_impossible_zones` (voir sa propre docstring pour le bug réel
    que ça corrige) — jamais à `_shorten_impossible_zones`/`_clean_blocked_
    slots`, qui ne font jamais que noircir une case déjà BLANCHE (une case
    de `permanent_black_cells` est, par construction, déjà noire depuis le
    tout premier palier, donc ne peut structurellement jamais apparaître
    parmi leurs propres candidats).

    Quand `_clean_blocked_slots` a, en plus, posé une nouvelle case noire
    (son alternative à 1/10 — `new_black_cells`), le motif change de
    forme : `new_slots` est réextrait sur cette grille modifiée, et
    `cand_preseed_assignment` recompose alors le mot de CHAQUE emplacement
    de ce nouveau motif entièrement couvert par `confirmed` — y compris un
    tout nouvel emplacement, né de la case noire ajoutée, jamais lui-même
    résolu par une vraie recherche. Ce mot est validé avant d'être promu
    (`_invalid_fully_known_indices`, même garde-fou que `_optimize_before_
    cleanup`/`_clean_blocked_slots` — voir CLAUDE.md, "UI") : une
    combinaison qui ne correspond à aucun mot réel du dictionnaire, même
    entièrement couverte par des lettres individuellement correctes,
    n'est jamais promue — l'emplacement reste `None`, et sera redécouvert
    de lui-même comme impossible dès la prochaine recherche (`Filler.
    exclude_immediately_impossible_slots`), plutôt que d'être verrouillé
    tel quel pour le reste de la génération."""
    cand_slots = extract_slots(cand_grid, rows, cols)
    cand_grid, cand_slots, cand_assignment, cand_impossible = _shorten_impossible_zones(
        cand_grid, rows, cols, cand_slots, cand_diag["assignment"],
        cand_diag["impossible_slots"], index, rng,
        permanent_locked_letters=permanent_locked_letters,
    )
    cand_grid, cand_slots, cand_assignment, cand_impossible = _lengthen_impossible_zones(
        cand_grid, rows, cols, cand_slots, cand_assignment, cand_impossible, index, rng,
        permanent_locked_letters=permanent_locked_letters,
        permanent_black_cells=permanent_black_cells,
    )
    cleaned_assignment, confirmed, new_black_cells = _clean_blocked_slots(
        cand_slots, cand_assignment, cand_impossible,
        index=index, rng=rng, grid=cand_grid, rows=rows, cols=cols,
        permanent_locked_letters=permanent_locked_letters,
    )
    if new_black_cells:
        cand_seed_grid = [row[:] for row in cand_grid]
        for (br, bc) in new_black_cells:
            cand_seed_grid[br][bc] = BLACK
        new_slots = extract_slots(cand_seed_grid, rows, cols)
        cand_preseed_assignment = [
            "".join(confirmed[cell] for cell in cells)
            if all(cell in confirmed for cell in cells) else None
            for cells in new_slots
        ]
        for j in _invalid_fully_known_indices(
            new_slots, index, confirmed, exempt=permanent_locked_letters
        ):
            cand_preseed_assignment[j] = None
        old_impossible_cell_tuples = {
            tuple(cand_slots[i]) for i in cand_impossible
        }
        cand_excluded_slots = {
            j for j, cells in enumerate(new_slots)
            if tuple(cells) in old_impossible_cell_tuples
        }
        return (cand_seed_grid, confirmed, new_slots, cand_preseed_assignment,
                cand_excluded_slots, cand_diag.get("process_number"))
    cand_excluded_slots = set(cand_impossible)
    return (cand_grid, confirmed, cand_slots, cleaned_assignment,
            cand_excluded_slots, cand_diag.get("process_number"))


# Extrait, d'une liste déjà triée de candidats `_clean_continue_candidate`
# (6 éléments), le vivier transmis au prochain palier "reprise telle
# quelle" — `(seed_grid, preseed_assignment, excluded_slots)` par entrée,
# la forme attendue par `_pattern_continue` (le 6e élément, le numéro de
# lignée hérité, n'est jamais transmis à `_pattern_continue` lui-même —
# voir `carry_seed_pool_continue_lineage`, construit séparément avec le
# même `_seed_pool` mais un extracteur différent, pour ce à quoi il sert
# réellement). Simple appel à `_seed_pool` ci-dessus avec l'extracteur
# adapté à cette forme à 6 éléments.
def _continue_seed_pool(sorted_candidates):
    return _seed_pool(sorted_candidates, extract=lambda sc: (sc[0], sc[3], sc[4]))


# ---------- Tentatives (motif + remplissage) en parallèle ----------
#
# `index` (le lexique pré-indexé, potentiellement 100 000+ mots) est envoyé
# une seule fois par worker via l'initializer du pool, plutôt que repicklé à
# chaque tâche soumise — il ne change jamais pendant un generate_grid().
_worker_index = None
# Ensemble (frozenset de MOTs nus, en majuscules) des mots à privilégier —
# la présélection thématique issue de la pré-recherche Qdrant (voir
# generate_grid's `priority_words` et backend/app.py). Vide/`None` = aucune
# thématique, comportement inchangé. Passé une seule fois par worker via
# l'initializer du pool comme `_worker_index` (il peut contenir plusieurs
# milliers de mots et ne change jamais pendant un generate_grid()).
_worker_priority_words = None
# Bouton "Stop" (voir CANCEL_CHECK_INTERVAL/Filler.__init__), à la demande
# explicite de l'utilisateur — comme `_worker_index` juste au-dessus, passé
# une seule fois par worker via l'initializer du pool plutôt qu'en argument
# de chaque tâche soumise. Nécessaire, pas juste une question de style :
# un `multiprocessing.Event` soumis comme argument ordinaire de
# `executor.submit(...)` a été constaté en direct comme provoquant
# `RuntimeError: Condition objects should only be shared between processes
# through inheritance` (la méthode de démarrage "spawn", par défaut sur
# macOS, ne partage jamais la mémoire par héritage — chaque tâche soumise
# est repicklée individuellement) ; le transmettre via l'initializer du
# pool, exactement comme `index`, est le moyen documenté et effectivement
# fonctionnel de partager ce genre d'objet avec des processus workers.
_worker_cancel_event = None
# Signal "tout le batch est bloqué" (voir Filler._backtrack et generate_grid
# ci-dessous), à la demande explicite de l'utilisateur : "quand une
# recherche arrive à une situation jugée 'bloquée', arrêter toutes les
# recherches du batch N, pour passer au batch N+1 sans attendre que toutes
# les recherches arrivent à une situation de blocage." Un seul
# `multiprocessing.Event`, créé une fois par `generate_grid()` (comme
# `cancel_event` juste au-dessus, et pour la même raison technique :
# passé une seule fois par worker via l'initializer du pool, jamais en
# argument de tâche soumise) mais *remis à zéro* par le processus parent
# au début de chaque palier — contrairement à `cancel_event`, qui ne se
# déclenche jamais qu'une fois pour toute la génération, ce signal-ci a un
# sens différent à chaque palier (un blocage constaté au palier N ne doit
# pas influencer le palier N+1). Positionné par n'importe quel worker dont
# le propre `Filler.abandoned` devient vrai (la règle des 30 %, voir
# UNFILLABLE_ABANDON_FRACTION) — vérifié par tous les autres workers du
# même batch, qui s'arrêtent alors eux aussi, sans attendre d'atteindre
# individuellement leur propre seuil d'abandon ou leur propre budget.
#
# N'est plus réellement transmis NULLE PART aujourd'hui — ni à
# `_pattern_attempt` (motif neuf), ni à `_pattern_continue` (reprise
# "telle quelle") — les deux transmettent toujours `None` à `try_fill`
# plutôt que ce global. Historique complet, dans l'ordre :
#
# D'abord désactivé spécifiquement pour `_pattern_attempt`, un vrai bug
# trouvé en direct avant tout déploiement, pas seulement raisonné : les
# PARALLEL_ATTEMPTS tentatives d'un même palier `_pattern_attempt` génèrent
# chacune leur PROPRE motif indépendant (`make_pattern` avec son propre
# `rng`, sur le même `seed_grid`/`locked_letters` de départ mais avec des
# cases noires ajoutées différemment à chaque fois) — la conclusion "30 %
# de CE motif-ci est impossible" d'une tentative ne dit donc rien de
# fiable sur le motif, complètement différent, d'une autre tentative du
# même batch. Reproduit en direct sur la grille de référence 15×10 (seed
# 7, auparavant fiable) : appliquer ce signal aux deux mécanismes à la
# fois faisait échouer cette graine (`None` renvoyé après 200 paliers,
# alors qu'elle réussissait avant ce correctif) — désactiver le signal
# spécifiquement pour `_pattern_attempt` (en lui transmettant toujours
# `None` plutôt que ce global) restaure le succès, confirmant que le
# problème vient bien de cette contamination entre motifs indépendants.
#
# `_pattern_continue`, à l'époque, faisait exactement l'inverse par
# construction : toutes ses tentatives parallèles partageaient
# RIGOUREUSEMENT le même motif et le même verrouillage — seul l'ordre
# d'exploration différait — donc la conclusion d'une tentative sur ce motif
# partagé restait pertinente pour les autres, et le signal restait
# transmis là.
#
# Ce n'est plus vrai depuis `carry_seed_pool_continue` (voir
# `generate_grid`), à la demande explicite de l'utilisateur ("chaque
# process doit repartir à l'étape suivante avec sa grille partiellement
# nettoyée") : deux tentatives parallèles d'un même palier "reprise telle
# quelle" peuvent désormais recevoir des entrées DIFFÉRENTES du vivier (ou
# même un motif entièrement neuf via `_pattern_attempt` pour les
# tentatives réinitialisées, voir `FULL_RESET_ATTEMPT_COUNT`) — exactement
# la même contamination entre motifs indépendants que celle qui a motivé
# de désactiver ce signal pour `_pattern_attempt` s'applique désormais
# aussi ici, alors désactivé de la même façon, préventivement, avant même
# qu'un échec en direct ne le confirme sur cette exacte grille de
# référence (voir `_pattern_continue`'s own docstring/call site).
_worker_batch_abandoned_event = None
# "This palier already has its answer" signal (see Filler.attempt_done_event
# and generate_grid), at the user's explicit request: "interrupt every search
# as soon as one search finishes (success or failure) to move on to the next
# palier." Same technical constraint as `_worker_cancel_event`/
# `_worker_batch_abandoned_event` above (passed once per worker via the
# pool's initializer, never as a per-task argument), but — unlike
# `_worker_batch_abandoned_event` — passed to BOTH `_pattern_attempt` and
# `_pattern_continue`: this signal never makes any inference about a
# specific pattern's own prospects, it only means "the palier's decision is
# already made, stop searching regardless of what you would have found" —
# see Filler.__init__'s own docstring for the full reasoning.
_worker_attempt_done_event = None
# `multiprocessing.Queue` sur laquelle chaque worker publie, en temps réel,
# chaque nouveau record de `Filler.best_assignment` atteint pendant SA
# propre recherche — pas seulement son état final — à la demande explicite
# de l'utilisateur : "Il ne faut pas supprimer les 70% des tentatives
# restantes, mais seulement les interrompre... Il faut conserver les 6
# meilleures grilles échouées des N process trouvées à n'importe quel
# moment des N recherches", précisé ensuite : "Chaque process suit son
# meilleur état, et transmet au process parent l'information que ce
# meilleur état a changé. Le process parent garde les 6 meilleurs états,
# de tous les états dont il a été informé par les N process." Même
# contrainte technique que les autres globals ci-dessus (passé une seule
# fois par worker via l'initializer du pool, jamais en argument de tâche
# soumise). Le volume reste borné : `best_assigned_count` ne peut
# progresser que d'une unité à la fois et ne dépasse jamais le nombre
# d'emplacements de la grille (~50-60 en pratique), donc au plus
# ~50-60 publications par worker et par palier, quel que soit le nombre
# réel d'appels à `_backtrack` (potentiellement des centaines de
# milliers) — voir `Filler._backtrack` pour le point d'appel exact.
_worker_best_state_queue = None
# `multiprocessing.Barrier` du pré-chauffage (voir `_warmup_worker`),
# transmis une seule fois via l'initializer du pool comme les autres
# globals ci-dessus — jamais réutilisé après le tout premier appel à
# `_warmup_worker` de ce worker (les vraies tâches, `_pattern_attempt`/
# `_pattern_continue`, ne le touchent jamais).
_worker_warmup_barrier = None
# Ensemble des mots (forme grille) considérés comme des noms propres pour
# cette langue, et quota maximum autorisé dans la grille finale — voir
# MAX_PROPER_NOUNS/generate_grid, à la demande explicite de l'utilisateur.
# Transmis une seule fois via l'initializer du pool, comme les globals
# ci-dessus (l'ensemble peut être volumineux — pas la peine de le
# re-sérialiser à chaque tâche soumise), plutôt qu'en argument de
# `_pattern_attempt`/`_pattern_continue` directement.
_worker_proper_noun_words = None
_worker_max_proper_nouns = None
# Idem pour les mots absents du dictionnaire de définitions — voir
# MAX_NON_GLOSS_WORDS/generate_grid.
_worker_non_gloss_words = None
_worker_max_non_gloss = None


def _warmup_worker():
    """Tâche factice soumise `PARALLEL_ATTEMPTS` fois d'un coup, juste
    après la création du pool (voir generate_grid, juste après le `with
    ProcessPoolExecutor(...)`), dans le seul but de forcer le démarrage
    RÉEL (spawn + exécution de `_init_worker`, qui désérialise le gros
    `index`) de chacun des `PARALLEL_ATTEMPTS` processus avant que le tout
    premier palier ne soumette ses vraies tâches.

    Diagnostiqué en direct (pas seulement supposé), en deux temps.
    D'abord : `ProcessPoolExecutor` spawn ses processus de façon
    paresseuse — le spawn lui-même (fork/exec + `_init_worker`, lent ici à
    cause de la désérialisation de l'index) se termine de façon
    asynchrone, bien après le `submit()` qui l'a déclenché. Sur le tout
    premier palier d'un `generate_grid()`, ça veut dire que seule une
    poignée de workers sont réellement prêts au moment où les 10 tâches du
    palier sont distribuées — les autres finissent de démarrer trop tard
    et n'en récupèrent aucune. Résultat observé : jusqu'à 5 tâches sur 10
    exécutées par le MÊME PID lors du palier 1 d'un run réel (9x7, seed=5)
    — donc, à l'affichage, un même `process_number` dupliqué plusieurs
    fois dans un seul lot d'aperçus, et ce jusqu'à ce que tous les workers
    aient fini de démarrer (~palier 7 dans ce test) — pas un bug de la
    numérotation elle-même (`worker_pid_numbers`), qui reflète fidèlement
    les PID réels reçus.

    Une première version de cette fonction se contentait de renvoyer
    `os.getpid()` sans synchronisation, soumise soit en une seule salve de
    `PARALLEL_ATTEMPTS` tâches, soit par tours successifs insistant tant
    que l'ensemble des PID distincts vus restait sous `PARALLEL_ATTEMPTS`
    — les deux se sont révélées insuffisantes, découvert en
    re-diagnostiquant en direct après coup :
    `ProcessPoolExecutor._adjust_process_count()` ne demande un nouveau
    spawn que s'il n'a AUCUN worker déjà au repos — dès qu'un seul worker
    devient disponible, TOUT nouveau `submit()` lui est confié en
    priorité plutôt que de déclencher un spawn supplémentaire, quel que
    soit le nombre de tâches encore en attente. Comme cette tâche factice
    est quasi instantanée, ce worker redevient disponible si vite qu'il
    absorbe la quasi-totalité des tâches restantes avant même que les
    autres n'aient jamais eu l'occasion de spawn — mesuré : sur 10 tâches
    soumises d'un coup, seuls 3 PID distincts sont apparus (un seul worker
    en a traité 6 à lui seul) ; en insistant par tours successifs sur
    plusieurs dizaines de tours, seuls 6 PID distincts sur 10 ont fini par
    apparaître, preuve que le plafond n'est pas juste "pas encore atteint"
    mais structurellement bloqué une fois un premier worker déjà au repos.

    Fixé avec une vraie barrière de synchronisation (`warmup_barrier`, un
    `multiprocessing.Barrier(PARALLEL_ATTEMPTS)` — voir `_worker_warmup_
    barrier`) : cette tâche appelle `.wait()` dessus avant de renvoyer son
    PID, donc reste bloquée tant que `PARALLEL_ATTEMPTS` appels n'ont pas
    tous atteint la barrière. Un worker qui attrape une de ces tâches ne
    redevient JAMAIS "au repos" pour le pool tant que la barrière n'a pas
    libéré tout le monde — il ne peut donc structurellement jamais en
    absorber une seconde avant que le pool n'ait été forcé de spawn un
    processus par tâche restante (aucun worker disponible ne peut la
    prendre). `generate_grid` soumet les `PARALLEL_ATTEMPTS` tâches en une
    seule salve puis les attend toutes — la barrière garantit que ce
    n'est possible que si `PARALLEL_ATTEMPTS` processus DISTINCTS ont
    réellement démarré, chacun ayant forcément déjà exécuté `_init_worker`
    pour pouvoir répondre à cette tâche — donc le premier palier ne peut
    démarrer qu'une fois tous les processus réellement prêts, et la
    répartition redevient 1:1 dès le palier 1, pas seulement à partir du
    palier où le pool finit par se stabiliser tout seul.

    Ce pré-chauffage reste utile même après que le numéro affiché a cessé
    d'être basé sur le PID (voir `_build_dispatch_lineage`) : il continue à
    garantir que `PARALLEL_ATTEMPTS` processus distincts existent bien
    avant le premier palier, une condition dont la recherche elle-même
    (répartition réelle du travail) bénéficie toujours, indépendamment de
    ce que l'affichage numérote."""
    # `timeout` (60s, largement suffisant même sur une machine très
    # chargée pour que `PARALLEL_ATTEMPTS` interprètes Python démarrent)
    # évite un blocage éternel si la machine ne peut structurellement
    # jamais faire coexister `PARALLEL_ATTEMPTS` processus à la fois —
    # dans ce cas `threading.BrokenBarrierError` se propage jusqu'au
    # `.result()` du parent (voir generate_grid, qui la rattrape).
    _worker_warmup_barrier.wait(timeout=60)
    return os.getpid()


def _init_worker(index, cancel_event=None, batch_abandoned_event=None, attempt_done_event=None,
                  best_state_queue=None, warmup_barrier=None, proper_noun_words=None,
                  max_proper_nouns=None, non_gloss_words=None, max_non_gloss=None,
                  priority_words=None):
    # Voir GENERATION_PROCESS_NICE_INCREMENT (juste après PARALLEL_ATTEMPTS)
    # pour le raisonnement complet — appliqué une seule fois ici, au tout
    # premier démarrage de ce worker (jamais par tâche soumise), puisque le
    # pool réutilise le même processus pour toute la durée de l'appel à
    # generate_grid() : la niceness d'un processus POSIX persiste jusqu'à sa
    # fin, nul besoin de la réappliquer à chaque tentative. `os.nice()` est
    # un pur ajout à la niceness déjà en vigueur (jamais un remplacement
    # absolu) — appelé une seule fois par worker, il ne peut donc jamais
    # s'accumuler d'un appel à l'autre. Encapsulé dans un `try/except`
    # défensif : `os.nice()` peut échouer sur une plateforme où il n'est pas
    # disponible ou selon des restrictions locales imprévues, ce qui ne
    # doit jamais empêcher le worker de démarrer — la priorité système
    # basse est une optimisation, pas une condition de fonctionnement.
    if GENERATION_PROCESS_NICE_INCREMENT:
        try:
            os.nice(GENERATION_PROCESS_NICE_INCREMENT)
        except OSError:
            pass
    global _worker_index, _worker_cancel_event, _worker_batch_abandoned_event, \
        _worker_attempt_done_event, _worker_best_state_queue, _worker_warmup_barrier, \
        _worker_proper_noun_words, _worker_max_proper_nouns, \
        _worker_non_gloss_words, _worker_max_non_gloss, _worker_priority_words
    _worker_index = index
    _worker_priority_words = priority_words
    _worker_cancel_event = cancel_event
    _worker_batch_abandoned_event = batch_abandoned_event
    _worker_attempt_done_event = attempt_done_event
    _worker_best_state_queue = best_state_queue
    _worker_warmup_barrier = warmup_barrier
    _worker_proper_noun_words = proper_noun_words
    _worker_max_proper_nouns = max_proper_nouns
    _worker_non_gloss_words = non_gloss_words
    _worker_max_non_gloss = max_non_gloss


def _pattern_attempt(rows, cols, ratio, seed, force_letters_fraction=0.0,
                      seed_grid=None, locked_letters=None,
                      black_enrichment_fraction=POST_PREFILL_BLACK_FRACTION,
                      deadline_checks=None, permanent_locked_letters=None,
                      permanent_black_cells=None):
    """Une tentative indépendante (motif + remplissage CSP complet), exécutée
    dans un processus worker séparé — voir PARALLEL_ATTEMPTS/generate_grid().
    Chaque tentative a son propre `random.Random(seed)`, dérivé du seed
    global par l'appelant, pour rester reproductible tout en étant
    différente des autres tentatives du même palier. Retourne
    (grid, result, diagnostics) ; `result` est None en cas d'échec, même
    contrat que try_fill.

    `deadline_checks` (`None` par défaut) est transmis tel quel à
    `try_fill` — voir la docstring de `generate_grid` pour d'où vient cette
    valeur (le sélecteur "Mode" de l'interface web).

    `seed_grid`/`locked_letters` (tous deux `None` par défaut — chaque appel
    existant avant cette fonctionnalité continue de partir d'une grille
    vierge, sans aucune lettre déjà connue), à la demande explicite de
    l'utilisateur : point de départ construit par `_build_retry_seed` à
    partir de la meilleure tentative échouée du palier précédent (voir
    generate_grid) — `make_pattern` continue de poser des cases noires sur
    `seed_grid` plutôt que de repartir d'une grille blanche (avec
    `locked_letters` exclu de son propre pool de candidates, pour ne
    jamais écraser une lettre déjà confirmée), et `locked_letters` est
    fusionné dans `forced_letters` en écrasant tout indice statistique déjà
    présent à la même case (`{**forced_letters, **locked_letters}` : une
    lettre confirmée par une recherche précédente est un fait, pas une
    supposition — elle l'emporte toujours sur le sondage statistique de
    `sample_letter_biases`, jamais l'inverse).

    Avant de lancer le remplissage réel sur ce motif fraîchement choisi, à
    la demande explicite de l'utilisateur : un sondage statistique
    (`sample_letter_biases`) tourne **systématiquement**, quel que soit
    `force_letters_fraction` (y compris à 0.0, le réglage par défaut) —
    ce même sondage fournit à la fois `forced_letters` (des indices de
    lettres, voir Filler._domain) et `letter_scores` (les scores complets
    par lettre et par case que _backtrack utilise pour trier puis
    piocher ses mots candidats, voir Filler._candidate_score), et seul le
    premier des deux dépend réellement de `force_letters_fraction` : à 0.0,
    `sample_letter_biases` retourne `forced_letters` vide (son propre
    calcul de combien de cases forcer donne exactement zéro dans ce cas —
    voir sa docstring), alors que `letter_scores`, lui, reste toujours
    entièrement rempli, à la demande explicite de l'utilisateur — le tri
    des mots candidats par cohérence statistique n'a jamais eu besoin
    d'être conditionné à la présence de lettres réellement forcées.
    `locked_impossible_slots` (calculé juste avant, voir ci-dessus) lui est
    transmis comme `excluded_slots` — à la demande explicite de
    l'utilisateur, pour qu'aucune graine ne soit posée sur un emplacement
    déjà connu impossible (entièrement verrouillé, mais sans mot réel
    correspondant).

    `black_enrichment_fraction` (défaut `POST_PREFILL_BLACK_FRACTION`, voir
    sa propre définition) est transmis tel quel à `make_pattern` — réglable
    depuis l'interface web (voir generate_grid), à la demande explicite de
    l'utilisateur.

    `permanent_locked_letters` (`None` par défaut — aucun effet pour tout
    appelant existant avant "Finir la grille", voir la docstring de
    `generate_grid`) est fusionné dans `locked_letters` avant même
    `make_pattern`, indépendamment de ce que `carry_locked_letters`
    contient par ailleurs pour ce palier (potentiellement `None`, ou déjà
    différent — voir la branche "reprise telle quelle" de generate_grid) :
    ces cases n'obtiennent jamais de case noire, et leur mot n'est jamais
    revalidé auprès du dictionnaire lors du pré-remplissage.

    `permanent_black_cells` (`None`/vide par défaut — aucun effet pour tout
    appelant existant avant "Finir la zone") : bug réel trouvé par un audit
    en direct (une case censée rester noire pour toujours ressortait, dans
    la grille finale, avec une LETTRE à la place). Root cause : un worker
    "réinitialisé" (`FULL_RESET_ATTEMPT_COUNT`, `seed_grid=None` — voir les
    deux sites d'appel dans `generate_grid`) part d'une grille entièrement
    blanche sans jamais avoir connaissance de ces cases, qui redeviennent
    alors des candidates ordinaires pour `make_pattern`, exactement comme
    n'importe quelle autre case blanche — rien ne les distingue plus une
    fois qu'un tel worker gagne et que son propre motif devient le
    `carry_seed_grid` du palier suivant : la protection assurée ailleurs
    (`_build_retry_seed`'s "toute case déjà noire dans le `seed_grid`
    d'entrée reste protégée", `_optimize_before_cleanup`/
    `_lengthen_impossible_zones`'s propre exclusion de `permanent_black_
    cells`) suppose toutes que ces cases sont DÉJÀ noires dans le motif
    qu'elles reçoivent — une hypothèse qui ne tient plus une fois qu'un
    reset l'a effacée. Corrigé ici, à la source, plutôt que dans chacun de
    ces appelants en aval : si `permanent_black_cells` est fourni, `seed_
    grid` (une grille blanche neuve si `None`, une copie défensive sinon)
    reçoit ces cases en noir AVANT même d'être transmis à `make_pattern` —
    un worker réinitialisé se comporte alors, pour ces cases précises,
    exactement comme s'il n'avait jamais été réinitialisé : `make_pattern`
    ne considère jamais une case déjà noire de son `seed_grid` comme une
    candidate (son pool de candidates ne retient que les cases encore
    blanches), donc elles restent noires sur CE worker, et donc sur tout
    `carry_seed_grid` qui en hérite ensuite.

    `make_pattern` elle-même reçoit `available_lengths` (les longueurs ayant
    au moins `PREFILL_MIN_WORD_COUNT` mots dans `_worker_index`, pas
    seulement un seul — voir sa propre définition) pour sa propre phase
    de pré-remplissage (voir `_prefill_unfillable_slots`) — dérivé ici, une
    fois par tentative, plutôt que precalculé côté `generate_grid` (coût
    négligeable : `_worker_index` n'a qu'une poignée de longueurs
    distinctes)."""
    rng = random.Random(seed)
    # `_worker_index` is a DualIndex (see generate_grid's own
    # `bilingual_wordlist_path` docstring) — computed per direction so a
    # length can be "available" for the across dictionary (language A)
    # without necessarily being available for the down one (language B),
    # and vice versa; the exact same set both ways on an ordinary
    # monolingual generation.
    available_lengths = DualSet(
        across={
            length for length, data in _worker_index.across.items()
            if len(data["words"]) >= PREFILL_MIN_WORD_COUNT
        },
        down={
            length for length, data in _worker_index.down.items()
            if len(data["words"]) >= PREFILL_MIN_WORD_COUNT
        },
    )
    # `permanent_locked_letters` (`None` par défaut — aucun effet pour tout
    # appelant existant avant "Finir la grille", voir la docstring de
    # generate_grid) fusionné ici, AVANT `make_pattern`, pour qu'aucun
    # placement de case noire de CE palier ne puisse jamais recouvrir une
    # case posée par l'utilisateur lui-même en mode Interactif — quel que
    # soit l'état de `carry_locked_letters` transmis par le palier
    # précédent, potentiellement `None` ou déjà différent (voir generate_
    # grid, la branche "reprise telle quelle" qui la réinitialise à
    # chaque palier).
    if permanent_locked_letters:
        locked_letters = {**(locked_letters or {}), **permanent_locked_letters}
    # `permanent_black_cells` (voir la docstring ci-dessus pour le bug réel
    # que ceci corrige) : forcé noir dans `seed_grid` avant même `make_
    # pattern`, y compris — surtout — pour un worker "réinitialisé"
    # (`seed_grid` reçu `None`), qui reçoit alors une grille blanche neuve
    # plutôt que `None` littéral, uniquement pour porter ces cases-là.
    if permanent_black_cells:
        seed_grid = (
            [[WHITE] * cols for _ in range(rows)] if seed_grid is None
            else [row[:] for row in seed_grid]
        )
        for (r, c) in permanent_black_cells:
            seed_grid[r][c] = BLACK
    grid = make_pattern(rows, cols, ratio, rng, available_lengths=available_lengths,
                         seed_grid=seed_grid, locked_letters=locked_letters, index=_worker_index,
                         black_enrichment_fraction=black_enrichment_fraction)
    # Récupère, avant même de lancer la recherche (et avant même le sondage
    # sample_letter_biases ci-dessous — voir juste après), le mot déjà
    # entièrement déterminé par `locked_letters` pour chaque emplacement
    # dont TOUTES les cases sont verrouillées — à la demande de
    # l'utilisateur, après avoir constaté en direct qu'une grille très
    # remplie pouvait retomber à `assigned=0` dès le tout premier palier
    # suivant un nettoyage : sans ce préremplissage, ces mots pourtant déjà
    # connus ne comptaient comme "assignés" (`Filler.best_assignment`) que
    # si `_backtrack` finissait par les sélectionner explicitement — et un
    # échec instantané ailleurs dans la grille (`checks=1`, un emplacement
    # différent déjà impossible) empêchait la recherche de jamais les
    # atteindre, jetant tout ce travail déjà fait par le nettoyage
    # précédent. Valide chaque mot recomposé auprès du dictionnaire
    # (`_slot_candidate_count`, la même intersection par position que
    # `Filler._domain`) avant de le préassigner — une combinaison de
    # lettres verrouillées qui ne correspond à aucun mot réel (un
    # emplacement réellement impossible, pas seulement pas encore essayé)
    # doit rester `None` ici : elle sera alors naturellement retrouvée par
    # `try_fill` (domaine vide) et remontée dans `impossible_slots`,
    # exactement comme pour un emplacement bloqué déjà connu — la
    # préassigner à tort la ferait disparaître de ce diagnostic à la place.
    # Réutilise `extract_slots` — même calcul que celui que `try_fill`
    # refait de toute façon en interne, aucun état partagé entre les deux
    # appels à économiser ici. `locked_impossible_slots` (les emplacements
    # entièrement verrouillés dont la combinaison est invalide) est
    # calculé ici, avant sample_letter_biases, spécifiquement pour lui être
    # transmis — à la demande explicite de l'utilisateur : "les graines ne
    # doivent être placées que sur des emplacements réputés jouables (si
    # possible), donc, non verrouillés comme injouables."
    # Avant même de calculer preseed_assignment ou le sondage statistique
    # des graines plus bas, à la demande explicite de l'utilisateur : "quand
    # un emplacement valide ne possède plus qu'une seule possibilité de
    # mot, forcer les lettres restantes pour placer ce mot." Appelé
    # inconditionnellement (pas seulement `if locked_letters:`) — même sans
    # aucune lettre déjà connue au départ, une longueur dont le dictionnaire
    # n'a qu'un seul mot en tout (un cas réel de ce projet, voir
    # `available_lengths`/`PREFILL_MIN_WORD_COUNT` plus haut) est déjà, en
    # elle-même, une "seule possibilité" à forcer. `locked_letters or {}` :
    # `_force_single_candidate_slots` renvoie toujours un dict (jamais
    # `None`), donc `locked_letters` devient ici un dict à coup sûr — les
    # vérifications `if locked_letters:` plus bas continuent de fonctionner
    # à l'identique (un dict vide reste "faux"), aucune régression pour le
    # cas où rien n'a pu être déduit.
    slots = extract_slots(grid, rows, cols)
    locked_letters = _force_single_candidate_slots(slots, _worker_index, locked_letters or {})

    preseed_assignment = None
    locked_impossible_slots = set()
    if locked_letters:
        preseed_assignment = [None] * len(slots)
        for i, cells in enumerate(slots):
            if all(cell in locked_letters for cell in cells):
                word = "".join(locked_letters[cell] for cell in cells)
                # Un emplacement entièrement couvert par `permanent_locked_
                # letters` (voir la docstring de generate_grid) est toujours
                # pré-assigné tel quel, sans jamais interroger le
                # dictionnaire — ces lettres, posées par l'utilisateur
                # lui-même en mode Interactif, doivent être considérées
                # comme bonnes quel que soit le mot qu'elles épellent
                # (probablement un nom propre) : le laisser à `None` ici
                # ferait échouer `truly_complete` pour toujours sur cette
                # grille, puisque cet emplacement ne serait alors plus
                # jamais réellement assigné par `_backtrack`.
                if permanent_locked_letters and all(
                    cell in permanent_locked_letters for cell in cells
                ):
                    preseed_assignment[i] = word
                elif _slot_candidate_count(_worker_index, len(cells), cells, locked_letters) > 0:
                    preseed_assignment[i] = word
                else:
                    locked_impossible_slots.add(i)
    forced_letters, letter_scores = sample_letter_biases(
        grid, rows, cols, _worker_index, rng, force_fraction=force_letters_fraction,
        excluded_slots=locked_impossible_slots, known_letters=locked_letters,
    )
    # Ancienne fusion (`forced_letters = {**forced_letters, **locked_letters}`)
    # retirée à la demande explicite de l'utilisateur, après un bug réel
    # signalé en direct : "les optimisations suivantes montrent les
    # emplacements non vides en bleu [.forced]... ce n'est pas normal."
    # Root-causé : cette fusion rendait `locked_letters` indiscernable de
    # `forced_letters` pour `build_partial_letters_grid` (voir plus bas,
    # `diagnostics["forced_cells"]` = `sorted(forced_letters)`) — chaque
    # case réellement verrouillée (contenu confirmé, reporté d'un palier au
    # suivant) se retrouvait donc à tort listée comme une simple graine
    # statistique. Ce n'était jamais visible avant : `diagnostics["locked_
    # cells"]` (calculé séparément, à partir du même `locked_letters` non
    # fusionné) désignait déjà les mêmes cases, et la cascade CSS
    # (`.locked` déclarée après `.forced` dans style.css) faisait toujours
    # gagner le liseré orange sur le bleu pour l'aperçu habituel — jusqu'à
    # ce que `_optimize_before_cleanup` (voir sa propre docstring) calcule
    # SON PROPRE `locked_cells`, une définition différente ("emplacements
    # entièrement vides"), qui ne recouvre plus forcément les mêmes cases
    # : la contamination est alors devenue visible, sans rien override.
    # Vérifié directement : `Filler._domain` (seul lecteur de `self.forced_
    # letters`) consulte déjà `self.locked_letters` en premier, sans
    # condition, avant même d'envisager `self.forced_letters` en repli —
    # cette fusion n'a donc jamais été nécessaire pour la recherche
    # elle-même depuis que `Filler` distingue les deux séparément (voir son
    # propre historique) ; elle ne servait plus, de fait, qu'à contaminer
    # ce diagnostic.
    diag = {}
    # `batch_abandoned_event` toujours `None` ici, jamais `_worker_batch_
    # abandoned_event` — délibéré, voir la docstring de cette variable
    # globale pour pourquoi (chaque tentative de ce batch a son propre
    # motif indépendant ; le signal partagé n'a de sens que pour
    # `_pattern_continue`, où le motif est rigoureusement le même partout).
    result = try_fill(grid, rows, cols, _worker_index, rng, deadline_checks=deadline_checks,
                       diagnostics=diag,
                       forced_letters=forced_letters, letter_scores=letter_scores,
                       preseed_assignment=preseed_assignment, cancel_event=_worker_cancel_event,
                       batch_abandoned_event=None,
                       attempt_done_event=_worker_attempt_done_event,
                       locked_letters=locked_letters,
                       best_state_queue=_worker_best_state_queue,
                       attempt_id=seed,
                       proper_noun_words=_worker_proper_noun_words,
                       max_proper_nouns=_worker_max_proper_nouns,
                       non_gloss_words=_worker_non_gloss_words,
                       max_non_gloss=_worker_max_non_gloss,
                       priority_words=_worker_priority_words)
    return grid, result, diag


def _pattern_continue(rows, cols, seed, seed_grid, preseed_assignment, excluded_slots,
                       force_letters_fraction=0.0, deadline_checks=None,
                       permanent_locked_letters=None):
    """Tentative de la mécanique de reprise « telle-quelle » entre paliers, à
    la demande explicite de l'utilisateur ("Nouvelle version") — exécutée
    dans un processus worker séparé, comme _pattern_attempt, mais qui n'appelle
    JAMAIS make_pattern : `seed_grid` (le motif noir/blanc du palier
    précédent, sélectionné parce qu'il restait encore au moins un
    emplacement où un mot pouvait être ajouté — voir generate_grid) est
    repris à l'identique, sans une seule case noire de plus ou de moins.

    `deadline_checks` (`None` par défaut) est transmis tel quel à
    `try_fill` — voir la docstring de `generate_grid` pour d'où vient cette
    valeur (le sélecteur "Mode" de l'interface web).

    `preseed_assignment` (l'affectation du palier précédent, un mot ou None
    par emplacement) verrouille tel quel chaque emplacement déjà rempli —
    `try_fill` initialise `Filler.assignment` (et used_words/best_assignment)
    directement dessus plutôt que de repartir d'une grille vide.
    `excluded_slots` (les emplacements déjà identifiés comme impossibles au
    palier précédent, voir `Filler.excluded_slots`) reste ignoré de cette
    recherche : "le tour N+1 doit ignorer les situations de blocage sur les
    cases verrouillées, et essayer de continuer à remplir la grille" — sans
    quoi le simple contrôle de domaine de `_backtrack` ferait échouer la
    recherche dès le premier appel (`checks=1`), même pour des emplacements
    sans aucun rapport avec le blocage déjà connu.

    Chaque tentative parallèle du même palier reçoit son propre seed, comme
    _pattern_attempt — `seed_grid`/`preseed_assignment`/`excluded_slots`
    restent, pour UN appel donné, rigoureusement identiques d'un appel à
    l'autre de `Filler`/`try_fill` à l'intérieur de cette même recherche
    (rien de nouveau à générer une fois cette tentative lancée), seul
    l'ordre d'exploration diffère (sondage statistique `sample_letter_
    biases`, tri/tirage des mots candidats dans `_backtrack`) : suffisant
    pour que plusieurs tentatives parallèles, parties du même point,
    atteignent des états d'avancement différents.

    Ceci ne veut plus dire, depuis que le vivier `carry_seed_pool_continue`
    existe (voir `generate_grid`), que TOUTES les tentatives parallèles d'un
    même palier "reprise telle quelle" reçoivent nécessairement le même
    triplet `(seed_grid, preseed_assignment, excluded_slots)` — à la
    demande explicite de l'utilisateur ("chaque process doit repartir à
    l'étape suivante avec sa grille partiellement nettoyée"), le parent peut
    désormais dispatcher une entrée différente du vivier à chaque tentative
    non réinitialisée du même palier ; seule une tentative *individuelle*
    (un seul appel à cette fonction) garde un point de départ fixe pour
    elle-même.

    Un `try_fill` complet (`truly_complete`, voir sa docstring) implique ici
    que même les emplacements exclus ont fini par être remplis — impossible
    tant qu'ils restent dans `excluded_slots` (jamais assignés par
    construction), donc `result` vaut toujours None ici : la seule sortie
    utile de cette fonction est `diag` (assignment/impossible_slots à jour),
    que generate_grid réexamine pour décider s'il reste encore un
    emplacement où ajouter un mot (auquel cas la reprise "telle-quelle"
    continue au palier suivant, avec un `excluded_slots` éventuellement
    élargi) ou si c'est un vrai blocage total (plus aucun emplacement non
    exclu n'a de domaine non vide), auquel cas le palier suivant repasse par
    le nettoyage existant (`_build_retry_seed`) et un motif neuf."""
    rng = random.Random(seed)
    # Lettres déjà connues avec certitude à ce stade (voir `known_letters`
    # dans la docstring de `sample_letter_biases`) : tout emplacement déjà
    # entièrement rempli par `preseed_assignment` — verrouillé tel quel,
    # jamais remis en question par cette recherche (voir plus haut). Un
    # second appel à `extract_slots` sur le même motif noir/blanc (déjà
    # recalculé de toute façon par `try_fill` juste en dessous) — un calcul
    # bon marché, pas la peine de le faire remonter par un paramètre
    # supplémentaire juste pour l'éviter ici.
    slots = extract_slots(seed_grid, rows, cols)
    known_letters = {
        cell: letter
        for cells, word in zip(slots, preseed_assignment)
        if word is not None
        for cell, letter in zip(cells, word)
    }
    # `permanent_locked_letters` (`None` par défaut — aucun effet pour tout
    # appelant existant avant "Finir la grille", voir la docstring de
    # generate_grid) toujours fusionné ici, INCONDITIONNELLEMENT — même si
    # le nettoyage du palier précédent a laissé `preseed_assignment[i]` à
    # `None` pour l'emplacement qu'elles couvrent (ce qui est sans
    # conséquence : la case reste de toute façon verrouillée par ce
    # dict), pour que ces lettres restent une contrainte dure de CETTE
    # recherche aussi, quel que soit ce que `preseed_assignment` en dit
    # par ailleurs.
    if permanent_locked_letters:
        known_letters = {**known_letters, **permanent_locked_letters}
    # Avant le sondage statistique des graines, à la demande explicite de
    # l'utilisateur (voir _force_single_candidate_slots) : force les
    # emplacements dont les lettres déjà connues ne laissent plus qu'une
    # seule possibilité réelle dans le dictionnaire.
    known_letters = _force_single_candidate_slots(
        slots, _worker_index, known_letters, excluded_slots=excluded_slots,
    )
    # Un emplacement fraîchement entièrement déterminé par la déduction
    # ci-dessus (pas seulement par `preseed_assignment` d'origine) devient
    # lui aussi une véritable affectation, pas seulement un indice
    # statistique — même principe que `_pattern_attempt`'s propre
    # préremplissage : sans cette promotion, `Filler._domain` ne verrait ces
    # lettres que comme un indice (voir `forced_letters` plus bas), jamais
    # comme la certitude qu'elles sont réellement. Revalidé exactement comme
    # `_pattern_attempt` (`_slot_candidate_count(...) > 0`) plutôt que
    # simplement assigné tel quel : un emplacement peut se retrouver
    # entièrement connu par le seul jeu des croisements, sans que
    # `_force_single_candidate_slots` lui-même ait jamais vérifié que cette
    # combinaison précise correspond à un vrai mot pour SA propre longueur
    # (son propre passage l'aurait alors simplement ignoré comme "déjà
    # connu", sans le valider) — laissé à `None` si invalide : `try_fill`
    # le retrouvera de lui-même comme un domaine vide, exactement comme
    # n'importe quel autre emplacement bloqué. Les emplacements de
    # `excluded_slots` ne sont jamais promus ainsi, par cohérence avec
    # `_force_single_candidate_slots` qui ne les traite déjà jamais.
    preseed_assignment = list(preseed_assignment)
    excluded = excluded_slots or set()
    for i, cells in enumerate(slots):
        if i in excluded or preseed_assignment[i] is not None:
            continue
        if all(cell in known_letters for cell in cells):
            word = "".join(known_letters[cell] for cell in cells)
            # Même exemption que _pattern_attempt : un emplacement
            # entièrement couvert par `permanent_locked_letters` est
            # toujours promu tel quel, jamais revalidé auprès du
            # dictionnaire — ces lettres sont posées par l'utilisateur
            # lui-même en mode Interactif et doivent être considérées
            # comme bonnes.
            if permanent_locked_letters and all(
                cell in permanent_locked_letters for cell in cells
            ):
                preseed_assignment[i] = word
            elif _slot_candidate_count(_worker_index, len(cells), cells, known_letters) > 0:
                preseed_assignment[i] = word
    forced_letters, letter_scores = sample_letter_biases(
        seed_grid, rows, cols, _worker_index, rng, force_fraction=force_letters_fraction,
        excluded_slots=excluded_slots, known_letters=known_letters,
    )
    # Fusion retirée à la demande explicite de l'utilisateur — même
    # correctif, même raisonnement, que celui appliqué à `_pattern_attempt`
    # (voir son propre commentaire pour le bug réel signalé et le
    # root-cause complet). Cette fusion était justifiée par le commentaire
    # d'origine ("une lettre déduite ici... n'aurait autrement aucun moyen
    # d'atteindre Filler comme contrainte réelle") à une époque où `Filler`
    # ne recevait pas encore son propre paramètre `locked_letters` dédié —
    # ce n'est plus vrai depuis : l'appel à `try_fill` juste en dessous
    # transmet déjà `locked_letters=known_letters` séparément, et `Filler.
    # _domain` consulte `self.locked_letters` sans condition avant même
    # d'envisager `self.forced_letters` — toute lettre de `known_letters`
    # atteint donc déjà `Filler` comme contrainte réelle, fusion ou non.
    diag = {}
    # `batch_abandoned_event` toujours `None` ici désormais — n'était vrai
    # que tant que TOUTES les tentatives parallèles d'un même palier
    # "reprise telle quelle" partageaient rigoureusement le même
    # `seed_grid`/`preseed_assignment` (voir la docstring de cette fonction,
    # et `_worker_batch_abandoned_event` pour l'historique complet de cette
    # règle). Depuis `carry_seed_pool_continue` (voir `generate_grid`), deux
    # tentatives parallèles du même palier peuvent désormais recevoir des
    # entrées DIFFÉRENTES du vivier — un `_pattern_attempt` (motif neuf,
    # pour les tentatives réinitialisées) mélangé à plusieurs `_pattern_
    # continue` sur des grilles distinctes — donc la conclusion "30 % de MA
    # grille est impossible" d'une tentative ne dit plus rien de fiable sur
    # la grille, potentiellement différente, d'une autre tentative de ce
    # même palier : exactement le même raisonnement, appliqué au même
    # global, qui a déjà motivé de le désactiver pour `_pattern_attempt`
    # (voir juste au-dessus) — désactivé ici aussi pour la même raison,
    # avant même qu'un vrai échec en direct ne le confirme.
    result = try_fill(seed_grid, rows, cols, _worker_index, rng, deadline_checks=deadline_checks,
                       diagnostics=diag,
                       forced_letters=forced_letters, letter_scores=letter_scores,
                       preseed_assignment=preseed_assignment, excluded_slots=excluded_slots,
                       cancel_event=_worker_cancel_event,
                       batch_abandoned_event=None,
                       attempt_done_event=_worker_attempt_done_event,
                       locked_letters=known_letters,
                       best_state_queue=_worker_best_state_queue,
                       attempt_id=seed,
                       proper_noun_words=_worker_proper_noun_words,
                       max_proper_nouns=_worker_max_proper_nouns,
                       non_gloss_words=_worker_non_gloss_words,
                       max_non_gloss=_worker_max_non_gloss,
                       priority_words=_worker_priority_words)
    return seed_grid, result, diag


# ---------- "Continuer" button: resuming a total failure from where it left off ----------
#
# At the user's explicit request: when generate_grid() exhausts every one of
# `attempts` (200 by default) paliers without ever finding a fillable grid,
# the web UI shows a "Continuer" button that relaunches another `attempts`
# paliers, picking up from the exact same seed_grid/locked_letters/
# preseed_assignment/excluded_slots the failed run's own cross-palier retry
# mechanism last produced — instead of the user's only other option, starting
# a brand new generation from a blank grid. `generate_grid`'s own progress()
# call for the "pattern_failed" event carries a `resume_state=...` kwarg
# built by `_serialize_resume_state` right where total failure is detected;
# `backend/app.py` persists it on the job so a later `POST /api/generate/
# continue/{job_id}` can hand it straight back to a fresh `generate_grid()`
# call's own `resume_state` parameter, deserialized by `_deserialize_
# resume_state`.
#
# JSON-safe by construction, since it travels through the job dict returned
# directly by `GET /api/generate/status/{job_id}`: `locked_letters`'s native
# shape (`{(row, col): letter}`, tuple keys) isn't valid JSON — encoded here
# as a flat `[[row, col, letter], ...]` list instead — and `excluded_slots`'s
# native `set` isn't JSON either, so it's encoded as a sorted list. `None`
# is preserved as `None` (JSON `null`) rather than collapsed into an empty
# list/dict for either field, since `carry_locked_letters`/`carry_preseed_
# assignment` being `None` vs. merely empty is what `generate_grid`'s own
# palier loop uses to tell the two mutually-exclusive resume mechanisms
# apart (see the loop's own `if carry_preseed_assignment is not None:`
# dispatch) — collapsing that distinction here would silently corrupt which
# mechanism a resumed run starts from.
def _serialize_resume_state(seed_grid, locked_letters, preseed_assignment, excluded_slots):
    return {
        "seed_grid": seed_grid,
        "locked_letters": (
            None if locked_letters is None
            else [[r, c, letter] for (r, c), letter in locked_letters.items()]
        ),
        "preseed_assignment": preseed_assignment,
        "excluded_slots": None if excluded_slots is None else sorted(excluded_slots),
    }


def _deserialize_resume_state(state):
    seed_grid = [row[:] for row in state["seed_grid"]]
    raw_locked_letters = state.get("locked_letters")
    locked_letters = (
        None if raw_locked_letters is None
        else {(r, c): letter for r, c, letter in raw_locked_letters}
    )
    preseed_assignment = state.get("preseed_assignment")
    raw_excluded_slots = state.get("excluded_slots")
    excluded_slots = None if raw_excluded_slots is None else set(raw_excluded_slots)
    return seed_grid, locked_letters, preseed_assignment, excluded_slots


def generate_grid(width=DEFAULT_WIDTH, height=DEFAULT_HEIGHT, difficulty="easy",
                   max_words=None, black_ratio=0.0, attempts=200, seed=None,
                   wordlist_path="data/wordlist_fr_full.tsv", on_progress=None,
                   force_letters_fraction=0.0, cancel_event=None,
                   black_enrichment_fraction=POST_PREFILL_BLACK_FRACTION,
                   deadline_checks=None, resume_state=None, should_pause=None,
                   bilingual_wordlist_path=None, priority_words=None,
                   bilingual_priority_words=None, permanent_locked_letters=None,
                   permanent_black_cells=None):
    """`permanent_black_cells` (`None`/vide par défaut — aucun effet pour
    tout appelant existant) — pour le bouton "Finir la zone" (backend/
    app.py's `interactive_finish`), l'ensemble des cases converties en
    case noire permanente parce qu'elles sont hors de la zone
    sélectionnée. Ces cases sont déjà, par construction, noires dans le
    `seed_grid` fourni via `resume_state` dès le tout premier palier, ce
    qui protège `_build_retry_seed` (le nettoyage complet, qui ne rouvre
    jamais une case déjà noire dans le `seed_grid` d'ENTRÉE du palier —
    voir sa propre docstring) sans avoir besoin de connaître ce paramètre
    du tout.

    Mais un `seed_grid` noir au départ d'un palier n'empêche pas, à
    l'intérieur même de ce palier, un autre mécanisme de retirer/déplacer
    une de ces cases avant que `_build_retry_seed` n'entre en jeu — bug réel
    rapporté en direct par l'utilisateur : "le bouton 'Finir la zone' ne
    verrouille pas correctement les cases grisées, le remplissage
    automatique continue à essayer de les alimenter." Deux endroits
    retirent effectivement une case noire déjà en place, chacun sans avoir
    jamais connu ce paramètre avant ce correctif : `_optimize_before_
    cleanup` (le passage d'optimisation à chaque tentative de chaque
    palier, avant même le nettoyage habituel — voir sa propre docstring) et
    `_lengthen_impossible_zones` (l'allongement d'un emplacement impossible
    en repoussant/supprimant l'une de ses cases noires bordantes — voir sa
    propre docstring), tous deux réservés à la reprise "telle quelle". Les
    deux reçoivent désormais `permanent_black_cells` et excluent
    explicitement ces cases de tout retrait/déplacement, exactement comme
    `minimize_black_squares` (voir sa propre docstring) le fait déjà pour
    sa propre passe finale.

    Ce même paramètre sert désormais aussi une SECONDE raison, pour "Finir
    la grille" comme pour "Finir la zone" alike (backend/app.py's
    `interactive_finish`) : protéger la case noire bordant immédiatement un
    mot qui porte déjà une définition tapée par le joueur (`preserved_
    clues`), pour que cette définition ne se retrouve jamais associée, en
    silence, à un mot allongé/fusionné par la même passe d'optimisation
    finale — voir `interactive_finish`'s own `protected_black_cells`, à la
    demande explicite de l'utilisateur : "il ne faut pas re-générer des
    définitions pour des emplacements qui en ont déjà une." `locked_
    letters`/`permanent_locked_letters` protège déjà les LETTRES d'un mot
    verrouillé ; ceci protège en plus sa FORME (là où une case noire
    bordante existe déjà), les deux ensemble garantissant qu'un mot déjà
    posé (et, le cas échéant, déjà défini) ne change jamais de forme.

    Génère une grille remplie de bout en bout (motif + CSP + minimisation).

    `permanent_locked_letters` (`None`/vide par défaut — aucun effet pour
    tout appelant existant, y compris le CLI et un "Continuer" ordinaire) —
    un dict `{(row, col): lettre}`, à la demande explicite de l'utilisateur
    pour le bouton "Finir la grille" (voir backend/app.py's
    `interactive_finish`) : "la génération ne doit pas toucher aux lettres
    verrouillées, y compris ne pas poser de case noire sur ces lettres."
    Complété par : "Les lettres posées en mode interactif sont à
    considérer comme bonnes, même si un emplacement contient un mot
    impossible (probablement un nom propre voulu par l'utilisateur)... ne
    doivent pas être remis en cause par la génération." Contrairement à
    `resume_state`'s propre `locked_letters` (une image de départ,
    seulement du tout premier palier, ensuite recalculée/remplacée palier
    après palier par la progression normale de la recherche —
    `carry_locked_letters`, voir plus bas), celui-ci reste identique,
    fusionné dans le `locked_letters`/`known_letters` réellement transmis
    à CHAQUE worker de CHAQUE palier (`_pattern_attempt`/`_pattern_
    continue`), quel que soit l'état de `carry_locked_letters`/
    `carry_preseed_assignment` à ce moment précis — ces cases n'obtiennent
    donc jamais de case noire (make_pattern les exclut systématiquement de
    son pool de candidates) et leur mot n'est jamais revalidé auprès du
    dictionnaire ni jamais retiré par un nettoyage, quelle que soit sa
    validité réelle (voir `_invalid_fully_known_indices`'s propre
    paramètre `exempt`, et l'exemption équivalente dans
    `minimize_black_squares`/`_clean_blocked_slots`).

    `priority_words` (`None`/vide par défaut — aucun effet pour tout
    appelant existant, notamment le CLI), à la demande explicite de
    l'utilisateur : la présélection thématique. Un itérable de mots (nus
    ou accentués — normalisés ici en MOTs majuscules sans accent, comme la
    colonne MOT du lexique) issus d'une pré-recherche vectorielle Qdrant
    des ~5000 mots les plus proches d'une thématique saisie (voir
    backend/app.py). Le lexique complet reste chargé (indispensable pour
    le repli) ; c'est le solveur CSP qui, pour chaque emplacement, essaie
    d'abord tous ses candidats présents dans `priority_words` et ne
    descend vers un mot ordinaire du dictionnaire que lorsque le
    backtracking a épuisé sans solution les mots thématiques qui y
    tenaient (voir `Filler._backtrack`).

    `bilingual_priority_words` (`None` par défaut) : sur une grille
    bilingue, le glossaire thématique de la langue des mots VERTICAUX, à
    la demande explicite de l'utilisateur ("Quand une grille est
    bilingue, il faut générer un glossaire thématique par langue").
    `priority_words` sert alors aux mots horizontaux (langue A),
    `bilingual_priority_words` aux verticaux (langue B) ; les deux sont
    enveloppés dans un `DualSet` résolu par direction (`_priority_words_
    for`), exactement comme `index`/`available_lengths`. Ignoré sur une
    grille monolingue (`priority_words` reste une simple frozenset).
    `width` est le nombre de colonnes (horizontal), `height` le nombre de lignes
    (vertical). Retourne un dict {width, height, pattern, solution, words,
    word_count, black_count, black_ratio, language, bilingual_language}, ou
    None si aucune grille remplissable n'a été trouvée en `attempts` essais.

    `bilingual_wordlist_path` (`None` par défaut — aucun effet pour tout
    appelant existant, notamment le CLI et toute grille "normale"), à la
    demande explicite de l'utilisateur ("Ajouter la possibilité de générer
    des grille bilingues... toutes les étapes utilisent la première
    langue pour les mots horizontaux, et la seconde langue pour les mots
    verticaux") : un second chemin de dictionnaire, dans le même format
    que `wordlist_path`. Quand il est fourni et diffère réellement de
    `wordlist_path` (un même chemin, ou `None`, dégénère proprement en
    génération monolingue ordinaire — aucun second `load_wordlist`/
    `build_index` n'est même appelé dans ce cas), un second lexique est
    chargé pour cette langue et le solveur CSP (voir `DualIndex`/`DualSet`
    ci-dessus, `Filler._domain`) tire chaque mot horizontal ("across") du
    premier dictionnaire et chaque mot vertical ("down") du second — les
    deux langues ne sont donc jamais mélangées au sein d'un même
    emplacement. Chaque entrée de `result["words"]` porte alors son propre
    `language` (le code de la langue réellement utilisée pour CE mot
    précis, selon sa direction) en plus de `accented`/`canonical` déjà
    résolus dans cette même langue — consommé par `backend/clues.py`
    (chaque mot obtient sa définition dans sa propre langue, voir
    `LLMClueGenerator.generate`) et par le ChatBot (`backend/chatbot.py`,
    pour donner un indice dans la bonne langue selon le mot concerné).
    Le quota de noms propres (`MAX_PROPER_NOUNS`) et le quota de mots sans
    entrée de définitions (`MAX_NON_GLOSS_WORDS`) restent un seul quota
    partagé pour la grille entière (l'union des deux ensembles de mots
    "à risque", un par langue) plutôt que dédoublés par direction — une
    simplification délibérée : les deux quotas bornent déjà un nombre
    total de mots sur toute la grille, pas une proportion par direction.

    `should_pause` (`None` par défaut — aucun effet pour tout appelant
    existant, notamment le CLI), à la demande explicite de l'utilisateur —
    voir GenerationPaused's own docstring : un callable optionnel, sans
    argument, vérifié à la même frontière entre deux paliers que
    `cancel_event` (jamais à l'intérieur d'un palier lui-même — la
    décision de céder son tour n'a de sens qu'entre deux cycles complets,
    jamais en interrompant une recherche déjà en cours) — s'il renvoie
    vrai, lève `GenerationPaused` avec l'état de reprise exact (le même
    mécanisme que le bouton "Continuer"), pour qu'un appel ultérieur avec
    `resume_state=...` reprenne au palier suivant, sans rien perdre de la
    progression déjà accumulée.

    `on_progress`, si fourni, est appelé `on_progress(step, **data)` à chaque
    étape notable (voir backend/app.py, qui s'en sert à la fois pour tracer
    backend.log et pour exposer un statut d'avancement à l'interface via
    l'API de polling) — aucun effet sur la génération elle-même, purement
    un point d'observation.

    `deadline_checks` (`None` par défaut — aucun effet pour tout appelant
    existant, notamment le CLI), à la demande explicite de l'utilisateur :
    transmis tel quel à chaque `_pattern_attempt`/`_pattern_continue` puis à
    `try_fill` (voir sa propre docstring), qui retombe sur son calcul par
    défaut (`largeur × hauteur × 2000`) tant que cette valeur reste `None`.
    L'interface web (voir backend/app.py) expose ceci comme un sélecteur
    "Mode" à choix fixes (Flash/Turbo/Rapide/Moyen/Ultra) plutôt qu'un champ
    libre — chaque mode fixe directement le nombre de vérifications par
    tentative, sans rapport avec la taille de la grille, contrairement à la
    formule par défaut.

    `cancel_event` (un `threading.Event`, `None` par défaut — aucun effet
    pour tout appelant existant, notamment le CLI), à la demande explicite
    de l'utilisateur : bouton "Stop" de l'interface web (voir
    backend/app.py), permettant d'interrompre une génération en cours
    quelle que soit l'étape. Vérifié au début de chaque palier (voir la
    boucle plus bas) et transmis à `minimize_black_squares` pour la phase
    de minimisation — lève `GenerationCancelled` dès que l'événement est
    déclenché, plutôt que de renvoyer `None` (qui signifie déjà autre
    chose : aucune grille remplissable trouvée après épuisement de
    `attempts`, un échec géniune, pas une interruption demandée). Un
    signal purement coopératif (voir GenerationCancelled) : l'arrêt
    effectif peut prendre jusqu'à la fin du palier en cours (borné par
    `deadline_checks` de chaque tentative parallèle), pas instantané —
    aucune tentative de tuer de force un processus worker déjà lancé.

    `black_enrichment_fraction` (défaut `POST_PREFILL_BLACK_FRACTION`, voir
    sa propre définition), à la demande explicite de l'utilisateur :
    réglable depuis l'interface web (un sélecteur "Taux noir", un entier
    libre 0-100, 14 % par défaut — voir `GenerateRequest.black_enrichment_
    percent` dans backend/app.py). Transmis tel quel à `_pattern_attempt`
    (jamais à `_pattern_continue`, qui ne rappelle jamais `make_pattern` —
    un palier de reprise "telle-quelle" ne peut par construction ajouter
    aucune case noire, voir _pattern_continue's propre docstring), donc
    uniquement pertinent pour un palier qui part d'une grille vierge ou
    d'un nettoyage (`_build_retry_seed`).

    A separate, unrelated per-cycle single-cell lock
    (`_impossible_cell_groups`/`_lock_one_impossible_cell`, which used to
    add one extra black cell on every palier's own impossible/blockage
    slot(s), including "reprise telle-quelle" ones) was removed entirely
    in this same session, at the user's explicit request — but this
    `black_enrichment_fraction` mechanism itself was never meant to be
    removed, only that separate lock; a first attempt mistakenly removed
    both together and was corrected once the user clarified the scope.
    See CLAUDE.md for the full history of both.

    `force_letters_fraction` (0.0 par défaut, c'est-à-dire désactivé), à la
    demande explicite de l'utilisateur : active ou non le sondage
    statistique de lettres forcées (`sample_letter_biases`, voir
    `_pattern_attempt`) en tout début de remplissage, et avec quelle
    fraction des cases de la grille. Réglable depuis l'interface web (un
    sélecteur de pourcentage — 0/1/2/5/10 %, 0 % par défaut — voir
    `GenerateRequest` dans backend/app.py et frontend/static/index.html),
    qui valide la valeur puis la convertit en fraction (`percent / 100`)
    avant de la transmettre ici ; auparavant une fraction fixe
    (`LETTER_BIAS_FORCE_FRACTION`, 5 %) appliquée systématiquement à toute
    tentative. Simplement transmis tel quel à chaque tentative, aucune
    autre partie du pipeline n'a besoin de le connaître.

    `resume_state` (`None` par défaut — aucun effet pour tout appelant
    existant, notamment le CLI), à la demande explicite de l'utilisateur :
    bouton "Continuer" de l'interface web, affiché quand une génération a
    épuisé tous ses `attempts` sans trouver de grille remplissable — voir
    `_serialize_resume_state`/`_deserialize_resume_state` juste au-dessus.
    Si fourni, initialise `carry_seed_grid`/`carry_locked_letters`/
    `carry_preseed_assignment`/`carry_excluded_slots` (voir la boucle plus
    bas) depuis l'état final d'un appel précédent qui a échoué, au lieu de
    partir d'une grille vierge — le premier palier de cet appel reprend
    ainsi exactement là où l'appel précédent s'est arrêté, avec un nouveau
    budget complet de `attempts` paliers."""
    def progress(step, **data):
        if on_progress:
            on_progress(step, **data)

    # Normalisé une bonne fois pour toutes en un dict réel (jamais `None`)
    # — chaque site qui le fusionne plus bas (`if permanent_locked_letters:
    # ...`) reste inchangé pour tout appelant qui ne le fournit pas du
    # tout, un dict vide étant tout aussi "faux" que `None` dans ce
    # contexte.
    permanent_locked_letters = dict(permanent_locked_letters) if permanent_locked_letters else {}

    rng = random.Random(seed)
    mw = max_words or DIFFICULTY_PRESETS.get(difficulty)
    by_length, accents, canonicals, frequencies = load_wordlist(
        wordlist_path, mw, require_gloss=(difficulty == "easy"),
        # Seul "easy" exclut désormais totalement les noms propres du
        # lexique — "medium"/"hard" les tolèrent maintenant, mais dans la
        # limite d'un vrai quota par grille (voir MAX_PROPER_NOUNS/
        # proper_noun_words ci-dessous), à la demande explicite de
        # l'utilisateur : "en mode FACILE ne pas autoriser à placer des
        # noms propres, en mode MOYEN autoriser au plus 2 noms propres, en
        # mode DIFFICILE autoriser jusqu'à 5 noms propres." Remplace
        # l'ancienne règle tout-ou-rien qui excluait "medium" aussi
        # strictement que "easy".
        exclude_proper_nouns=(difficulty == "easy"),
    )
    language = _lang_from_path(wordlist_path) or "fr"

    # Grille bilingue (voir la docstring de `bilingual_wordlist_path` plus
    # haut) : un second lexique n'est chargé que si `bilingual_wordlist_
    # path` est réellement fourni ET différent de `wordlist_path` — `None`
    # ou un chemin identique dégrade proprement en génération monolingue
    # ordinaire, sans second `load_wordlist` ni second `build_index`
    # (`by_length_down`/`accents_down`/`canonicals_down`/`frequencies_down`
    # aliasent alors simplement les valeurs déjà chargées ci-dessus).
    bilingual_active = bool(bilingual_wordlist_path) and bilingual_wordlist_path != wordlist_path
    if bilingual_active:
        by_length_down, accents_down, canonicals_down, frequencies_down = load_wordlist(
            bilingual_wordlist_path, mw, require_gloss=(difficulty == "easy"),
            exclude_proper_nouns=(difficulty == "easy"),
        )
        bilingual_language = _lang_from_path(bilingual_wordlist_path) or bilingual_wordlist_path
    else:
        by_length_down, accents_down, canonicals_down, frequencies_down = (
            by_length, accents, canonicals, frequencies
        )
        bilingual_language = None

    # Quota de noms propres pour cette génération (voir MAX_PROPER_NOUNS) et
    # l'ensemble des mots (forme grille) réellement considérés comme des
    # noms propres pour cette langue — même signal, calculé au même endroit,
    # que celui déjà utilisé par `exclude_proper_nouns` ci-dessus
    # (`accents[mot][:1].isupper()`), jamais recalculé une seconde fois.
    # Toujours calculé, même pour "easy" : `by_length`/`accents` n'y
    # contiennent alors déjà plus aucun nom propre (exclu ci-dessus), donc
    # cet ensemble ressort naturellement vide et ce quota (0) n'a
    # simplement jamais l'occasion de s'appliquer. Sur une grille
    # bilingue, l'union des noms propres des deux langues (voir la
    # docstring de `bilingual_wordlist_path`) — un seul quota partagé pour
    # toute la grille, pas un quota par direction.
    max_proper_nouns = MAX_PROPER_NOUNS.get(difficulty, MAX_PROPER_NOUNS["hard"])

    def _proper_noun_words_for(path, accents_map):
        lang = _lang_from_path(path)
        if lang in PROPER_NOUN_EXCLUDED_LANGS:
            return set()
        return {w for w, acc in accents_map.items() if acc[:1].isupper()}

    proper_noun_words = _proper_noun_words_for(wordlist_path, accents)
    if bilingual_active:
        proper_noun_words = proper_noun_words | _proper_noun_words_for(
            bilingual_wordlist_path, accents_down
        )
    # Quota de mots absents du dictionnaire de définitions pour cette
    # génération (voir MAX_NON_GLOSS_WORDS) et l'ensemble des mots (forme
    # grille) réellement sans entrée gloss pour cette langue — même signal
    # que `load_wordlist(require_gloss=...)`, réutilisé ici. Vide (donc
    # quota jamais déclenché) si la langue ne peut pas être déduite du
    # chemin, si le dictionnaire n'est pas construit, ou pour "easy" (où
    # `require_gloss=True` a déjà retiré ces mots du lexique en amont).
    # Union des deux langues sur une grille bilingue, même principe que
    # `proper_noun_words` ci-dessus.
    max_non_gloss = MAX_NON_GLOSS_WORDS.get(difficulty, MAX_NON_GLOSS_WORDS["hard"])

    def _non_gloss_words_for(path, accents_map, canonicals_map):
        lang = _lang_from_path(path)
        if not lang:
            return set()
        has_any_gloss, has_gloss_dictionary = _try_import_gloss_lookup()
        if not (has_any_gloss and has_gloss_dictionary and has_gloss_dictionary(lang)):
            return set()
        return {
            w for w in accents_map
            if not has_any_gloss([accents_map[w], *canonicals_map.get(w, [])], lang)
        }

    non_gloss_words = _non_gloss_words_for(wordlist_path, accents, canonicals)
    if bilingual_active:
        non_gloss_words = non_gloss_words | _non_gloss_words_for(
            bilingual_wordlist_path, accents_down, canonicals_down
        )
    # `index` est désormais un DualIndex (voir sa docstring) — le même
    # dictionnaire des deux côtés (across/down) sur une grille
    # monolingue, deux dictionnaires distincts sur une grille bilingue.
    index_across = build_index(by_length, frequencies)
    index_down = build_index(by_length_down, frequencies_down) if bilingual_active else index_across
    index = DualIndex(index_across, index_down)

    # Présélection thématique (voir la docstring / `priority_words`). Les
    # mots arrivent déjà sous forme MOT (majuscules, sans accent — le
    # `word` du payload Qdrant, identique à la colonne MOT du lexique et
    # aux clés de `index`) ; on se contente de mettre en majuscules et de
    # restreindre aux mots effectivement présents dans le lexique chargé
    # (un mot d'une langue/graphie absente de ce lexique ne servirait à
    # rien comme priorité). `frozenset` vide s'il n'y a aucune thématique
    # ou aucune correspondance — `Filler._backtrack` ne change alors rien.
    # Sur une grille bilingue, un glossaire par langue (à la demande
    # explicite de l'utilisateur : "Quand une grille est bilingue, il
    # faut générer un glossaire thématique par langue") — `priority_
    # words` pour les mots horizontaux (langue A), `bilingual_priority_
    # words` pour les verticaux (langue B) — enveloppés dans un DualSet,
    # exactement comme `index`/`available_lengths`. Sur une grille
    # monolingue, `priority_words` reste une simple frozenset (comportement
    # inchangé, `bilingual_priority_words` ignoré).
    if priority_words or bilingual_priority_words:
        _known_across = set(accents)
        across_pw = frozenset(
            u for w in (priority_words or ()) if (u := str(w).upper()) in _known_across
        )
        if bilingual_active:
            _known_down = set(accents_down)
            down_pw = frozenset(
                u for w in (bilingual_priority_words or ())
                if (u := str(w).upper()) in _known_down
            )
            priority_words = DualSet(across_pw, down_pw)
        else:
            priority_words = across_pw
    else:
        priority_words = frozenset()
    # Précalculé une seule fois (pas par palier) — mêmes longueurs pour
    # toute la génération, `index` ne change jamais. Reproduit exactement
    # le calcul propre à chaque worker dans `_pattern_attempt` (voir sa
    # propre docstring), mais côté processus PARENT cette fois — utilisé
    # uniquement par l'aperçu "cases noires posées" précoce ci-dessous,
    # jamais par la recherche CSP elle-même (qui reste toujours calculée
    # dans les processus workers, avec leur propre `_worker_index`).
    available_lengths_preview = DualSet(
        across={
            length for length, data in index.across.items()
            if len(data["words"]) >= PREFILL_MIN_WORD_COUNT
        },
        down={
            length for length, data in index.down.items()
            if len(data["words"]) >= PREFILL_MIN_WORD_COUNT
        },
    )
    # Logged once per request, not per attempt: the CSP's failure mode
    # (below) can't be told apart from a genuinely empty word list for
    # some length without this — a `require_gloss`/`max_words` combination
    # that shrinks a length to 0 words fails every pattern instantly, in
    # a way that looks identical in the per-attempt log to ordinary bad
    # luck unless this baseline is on record too.
    progress("wordlist_loaded", word_count=sum(len(w) for w in by_length.values()),
             length_counts=dict(sorted((length, len(words)) for length, words in by_length.items())),
             bilingual_language=bilingual_language,
             bilingual_word_count=(
                 sum(len(w) for w in by_length_down.values()) if bilingual_active else None
             ))

    rows, cols = height, width
    # Même résolution que `try_fill`'s propre `None`-fallback (largeur ×
    # hauteur × 2000), calculée ici une seule fois — plutôt que dans chaque
    # worker séparément — pour que le rapport de progression du budget
    # ci-dessous (`BUDGET_PROGRESS_REPORT_INTERVAL_S`) sache contre quelle
    # valeur comparer `checks` sans avoir à la redemander à un worker.
    resolved_deadline_checks = (
        deadline_checks if deadline_checks is not None else rows * cols * 2000
    )
    ratio = black_ratio
    best, best_result = None, None
    # Diagnostics du candidat gagnant (voir la branche `if successes:` plus
    # bas) — conservé uniquement pour son propre `process_number` (voir
    # `seed_to_lineage`), afin que l'aperçu "minimizing"/le champ
    # `winning_process_number` du résultat final puissent toujours afficher
    # le numéro de la lignée qui a réellement produit la grille retenue. `None` pour tout
    # chemin de réussite qui n'a jamais de diag réel (`_plug_isolated_
    # cells`, un dernier recours qui construit sa propre grille directement
    # dans le processus parent — jamais un vrai worker).
    best_diag = None
    last_diag = None
    last_examples = []
    # Compteur cumulatif du nombre de grilles réellement échouées depuis le
    # début (tous paliers confondus), à la demande explicite de l'utilisateur
    # — pour l'affichage du statut côté interface (voir describeStep() dans
    # frontend/static/script.js) : sans lui, l'utilisateur voit "tentative
    # X/attempts" (le numéro du *palier*) sans savoir combien de grilles
    # PARALLEL_ATTEMPTS-à-la-fois ont réellement été générées et rejetées
    # jusqu'ici. Incrémenté du nombre de tentatives *échouées* de chaque
    # palier (pas de `len(outcomes)` tel quel) — à la demande explicite de
    # l'utilisateur, après un premier réglage qui comptait aussi les
    # tentatives réussies du palier final comme des échecs : ce compteur doit
    # refléter le nombre de grilles réellement rejetées, pas le nombre brut
    # de tentatives lancées (qui, au palier gagnant, inclut une ou plusieurs
    # réussites).
    total_attempts_tried = 0
    # Chaque palier lance PARALLEL_ATTEMPTS tentatives indépendantes en
    # parallèle (processus séparés, un seed dérivé de `rng` chacune) plutôt
    # qu'une seule tentative séquentielle : la machine est loin de saturer
    # son CPU avec une seule tentative à la fois, donc plusieurs chances par
    # palier ne coûtent, en temps réel, quasiment que le temps de la
    # tentative la plus lente du lot — pas la somme des cinq.
    # Point de départ (motif + lettres verrouillées) transmis au palier
    # suivant après un échec complet, à la demande explicite de
    # l'utilisateur — voir _build_retry_seed pour l'algorithme en 3 étapes
    # (retirer les mots connectés aux emplacements en échec, garder le
    # reste comme lettres pré-définies, rouvrir les cases noires qui ne
    # touchent plus aucune lettre confirmée). `None` tant qu'aucun palier
    # n'a encore échoué — le tout premier palier part toujours d'une
    # grille vierge, exactement comme avant cette fonctionnalité.
    carry_seed_grid = None
    carry_locked_letters = None
    # Vivier de grilles nettoyées candidates pour le prochain palier « motif
    # neuf » — une par tentative parallèle du palier qui vient d'échouer
    # (jusqu'à `PARALLEL_ATTEMPTS`, moins les pires éliminées, voir plus
    # bas), pas une seule grille reprise par tous les workers non
    # réinitialisés — à la demande explicite de l'utilisateur : "on garde
    # la meilleure grille de tous les process, soit N grilles pour N
    # process, et on relance toutes les meilleures grilles après nettoyage
    # en ayant éliminé les moins bonnes en fonction du nombre de nouvelles
    # grilles paramétrées." `carry_seed_grid`/`carry_locked_letters`
    # ci-dessus restent la MEILLEURE grille de ce vivier (toujours en tête
    # une fois trié) — utilisés tels quels partout ailleurs dans cette
    # fonction (aperçus autres que le tout prochain palier, `resume_state`,
    # détection de point fixe...) exactement comme avant cette
    # fonctionnalité ; seul le tout prochain palier « motif neuf » puise
    # dans ce vivier pour diversifier son propre lancement plutôt que de
    # reprendre `carry_seed_grid` identique pour tous ses workers non
    # réinitialisés. `None` tant qu'aucun nettoyage complet n'a encore eu
    # lieu (voir plus bas, où seule la branche de nettoyage le renseigne).
    carry_seed_pool = None
    # {position dans `carry_seed_pool` -> numéro de "lignée" affiché},
    # STRICTEMENT parallèle à `carry_seed_pool` (même longueur, même ordre)
    # — remplace un ancien mécanisme basé sur le PID réel du worker
    # (`carry_seed_pool_process_numbers`, un dict par contenu de grille),
    # à la demande explicite de l'utilisateur après un rapport direct :
    # "les grilles changent de numéro d'un cycle sur l'autre... il faut que
    # les grilles portent leur propre numéro, et le gardent jusqu'à la fin
    # de la résolution." Le PID d'un worker est stable pour toute la durée
    # d'un `generate_grid()`, mais son AFFECTATION à une lignée donnée ne
    # l'est pas (voir `_build_dispatch_lineage`) — d'où le passage à une
    # numérotation qui suit la LIGNÉE elle-même (héritée d'un palier à
    # l'autre via la position dans ce vivier), jamais le processus OS qui
    # l'a produite. `None` tant qu'aucun nettoyage complet n'a encore eu
    # lieu — voir `_reassign_lineage_numbers`/`next_lineage_number` pour la
    # construction complète, y compris la reprise du numéro d'une lignée
    # qui disparaît par une grille entièrement neuve qui la remplace.
    carry_seed_pool_lineage = None
    # Compteur persistant pour toute la durée d'un `generate_grid()` (jamais
    # réinitialisé, y compris par le mécanisme de réinitialisation complète
    # de GRID_REPEAT_INFEASIBLE_THRESHOLD plus bas) — sert uniquement de
    # filet de sécurité à `_reassign_lineage_numbers` quand aucun numéro ne
    # s'est libéré ce palier (cas dégénéré, non rencontré en pratique).
    # Démarre après la plage 1..PARALLEL_ATTEMPTS déjà attribuée directement
    # au tout premier palier (voir plus bas, `carry_seed_grid is None`).
    next_lineage_number = PARALLEL_ATTEMPTS + 1
    # Reprise "telle-quelle" (voir _pattern_continue), à la demande
    # explicite de l'utilisateur ("Nouvelle version") : tant que le palier
    # échoué sélectionné a encore au moins un emplacement où un mot peut
    # être ajouté (pas seulement des emplacements impossibles), le palier
    # suivant repart du MÊME motif, sans passer par make_pattern ni par le
    # nettoyage `_build_retry_seed` — `carry_preseed_assignment`/
    # `carry_excluded_slots` pilotent ce mode ; `None` tous les deux (leur
    # valeur par défaut) signifie qu'on est en mode "motif neuf" normal
    # (via `_pattern_attempt`, `carry_seed_grid`/`carry_locked_letters`
    # ci-dessus, inchangé). Les deux mécanismes de reprise sont mutuellement
    # exclusifs à chaque palier : un seul est actif à la fois, jamais les
    # deux (voir plus bas, où chaque branche remet l'autre à None).
    carry_preseed_assignment = None
    carry_excluded_slots = None
    # Vivier de grilles nettoyées candidates pour le prochain palier de
    # reprise "telle quelle" — le pendant de `carry_seed_pool` ci-dessus,
    # mais pour `_pattern_continue` au lieu de `_pattern_attempt` : une
    # entrée `(seed_grid, preseed_assignment, excluded_slots)` par tentative
    # distincte du palier qui vient de se terminer, pas une seule grille
    # reprise par tous les workers non réinitialisés — à la demande explicite
    # de l'utilisateur : "Quand il n'y a pas de déclenchement d'un nettoyage
    # complet, chaque process doit repartir à l'étape suivante avec sa
    # grille partiellement nettoyée (sauf le pourcentage de grilles
    # entièrement neuves)." Voir `_clean_continue_candidate`/
    # `_continue_seed_pool` (niveau module) et `if still_has_hope:` plus bas
    # pour la construction ; `carry_seed_grid`/`carry_preseed_assignment`/
    # `carry_excluded_slots` ci-dessus restent la MEILLEURE entrée de ce
    # vivier (toujours en tête une fois trié) — utilisés tels quels partout
    # ailleurs dans cette fonction (aperçus autres que le tout prochain
    # palier, `resume_state`...) exactement comme avant cette fonctionnalité
    # ; seul le tout prochain palier de reprise "telle quelle" puise dans ce
    # vivier pour diversifier son propre lancement. `None` tant qu'aucun
    # palier "telle quelle" n'a encore eu lieu (voir plus bas, où seule cette
    # branche le renseigne) — jamais transmis par `resume_state` (comme
    # `carry_seed_pool` lui-même), un run repris reconstruit ce vivier
    # normalement dès son premier palier "telle quelle".
    carry_seed_pool_continue = None
    # Pendant de `carry_seed_pool_lineage` ci-dessus, pour le vivier de
    # reprise "telle quelle" — même rôle, même mécanisme.
    carry_seed_pool_continue_lineage = None
    # "Continuer" button on the web UI, at the user's explicit request: when
    # every one of `attempts` (200 by default) paliers has failed, the user
    # can relaunch another `attempts` paliers starting from the exact state
    # the failed run left off at, instead of starting over from a blank
    # grid. `resume_state` (`None` by default — no effect for any
    # pre-existing caller, including the CLI), if given, seeds the four
    # carry_* variables above from a previous, failed `generate_grid()`
    # call's own final state (see `_serialize_resume_state`/`_deserialize_
    # resume_state` and the matching `resume_state=...` kwarg on the
    # "pattern_failed" progress event further below) rather than starting
    # every one of them at `None`. `consecutive_continue_paliers` (below)
    # deliberately still starts at 0 regardless: this "Continuer" click
    # gets its own fresh budget of up to 5 consecutive "reprise
    # telle-quelle" paliers before a forced cleanup, exactly like any other
    # top-level `generate_grid()` call, rather than carrying over wherever
    # the previous run's own counter happened to be.
    if resume_state is not None:
        carry_seed_grid, carry_locked_letters, carry_preseed_assignment, carry_excluded_slots = (
            _deserialize_resume_state(resume_state)
        )
    # Nombre de paliers "continue" consécutifs déjà enchaînés sans passer
    # par un nettoyage, à la demande explicite de l'utilisateur : "Limiter
    # le nombre de tours réalisés sans nettoyage à 5 consécutifs maximum. A
    # partir de 5, déclencher un nettoyage." Remis à 0 chaque fois qu'un
    # nettoyage a réellement lieu (voir plus bas) — ce compteur ne mesure
    # que la série en cours, pas un total cumulé sur toute la génération.
    consecutive_continue_paliers = 0
    # Mémorisation de l'état (motif + contenu confirmé) obtenu à la fin de
    # chaque NETTOYAGE COMPLET, à la demande explicite de l'utilisateur —
    # voir GRID_REPEAT_INFEASIBLE_THRESHOLD's own docstring pour la
    # demande complète et l'historique des deux régressions mesurées avant
    # d'arriver à cette portée finale (nettoyage seul, jamais "reprise
    # telle quelle"). `last_cycle_end_grid` garde l'état (sous forme
    # hashable, un tuple de tuples produit par `_cycle_start_preview`) du
    # dernier nettoyage ; `same_grid_streak` compte combien de nettoyages
    # CONSÉCUTIFS (aucune "reprise telle quelle" entre-temps ne le
    # remet à zéro ni ne l'incrémente — cette branche n'y touche jamais)
    # ont reproduit ce même état.
    last_cycle_end_grid = None
    same_grid_streak = 0
    # True right after a palier that did a full cleanup ("nettoyage
    # complet", the `else:` branch below), False right after one that did
    # "reprise telle quelle" instead — at the user's explicit request (see
    # FULL_RESET_ATTEMPT_COUNT's own docstring): used only once, by the
    # very next palier's own worker-submission code below, to decide
    # whether a handful of that palier's PARALLEL_ATTEMPTS workers should
    # start from a blank grid instead of the just-cleaned carry_seed_grid/
    # carry_locked_letters every other worker gets. Always overwritten
    # again at the end of every single palier (whichever branch runs), so
    # this never lingers past the one palier it's meant for.
    just_cleaned = False
    # Voir _worker_batch_abandoned_event — un seul Event pour toute la
    # génération (créé ici, jamais recréé palier après palier, pour la
    # même raison technique que cancel_event : un Event soumis en argument
    # de tâche plutôt que via l'initializer du pool provoque une
    # RuntimeError sur macOS), mais remis à zéro avant chaque nouveau batch
    # (voir plus bas) puisque son sens ne vaut que pour le palier en cours.
    batch_abandoned_event = multiprocessing.Event()
    # Barrière de pré-chauffage (voir `_warmup_worker`) : un seul objet
    # pour toute la génération, transmis aux workers via l'initializer du
    # pool comme les autres primitives `multiprocessing` de ce fichier (un
    # `multiprocessing.Barrier` soumis en argument de tâche plutôt que via
    # l'initializer provoquerait la même RuntimeError sur macOS que
    # `cancel_event`/`batch_abandoned_event`). Exactement `PARALLEL_
    # ATTEMPTS` places : tant que tous ne sont pas arrivées, chaque worker
    # qui en attrape une reste bloqué dedans (ne redevient jamais "au
    # repos" pour le pool tant que la barrière n'a pas libéré tout le
    # monde) — c'est précisément ce qui force le pool à spawn un NOUVEAU
    # processus pour chacune des `PARALLEL_ATTEMPTS` tâches de
    # pré-chauffage plutôt que de laisser un worker déjà prêt en absorber
    # plusieurs (voir le diagnostic complet dans le docstring de
    # `_warmup_worker`, qui explique pourquoi une simple salve de tâches
    # factices sans synchronisation ne suffisait pas).
    warmup_barrier = multiprocessing.Barrier(PARALLEL_ATTEMPTS)
    # Signal "this palier's outcome is already decided", at the user's
    # explicit request: "interrupt every search as soon as one search
    # finishes (success or failure) to move on to the next palier." One
    # Event for the whole generation (same technical reason as
    # batch_abandoned_event/cancel_event above: a multiprocessing.Event
    # passed as a per-task argument raises a RuntimeError on macOS's
    # "spawn" start method — it must go through the pool's initializer
    # instead), cleared at the start of every palier below since its
    # meaning only ever applies to the palier currently running.
    attempt_done_event = multiprocessing.Event()
    # "Chaque process suit son meilleur état, et transmet au process parent
    # l'information que ce meilleur état a changé. Le process parent garde
    # les 6 meilleurs états, de tous les états dont il a été informé par
    # les N process" — décision explicite de l'utilisateur. Une
    # `multiprocessing.Queue` (pas un `Event` : il faut transporter des
    # données, pas juste un signal) transmise via l'initializer du pool,
    # pour la même raison technique que les trois `Event` ci-dessus — un
    # objet `multiprocessing` passé comme simple argument de tâche à
    # `executor.submit(...)` provoque une `RuntimeError` sur macOS
    # ("spawn"). Un seul objet pour toute la génération, jamais recréé
    # palier après palier.
    best_state_queue = multiprocessing.Queue()
    # Un vrai interblocage a été constaté en direct (temps CPU des workers
    # figé d'une lecture à l'autre, l'utilisateur observant lui-même « il
    # n'y a plus que 2 process qui tourne ») avec une première version qui
    # ne drainait `best_state_queue` qu'une seule fois par palier, juste
    # après que `as_completed` a récupéré tous les futures : le tube
    # (« pipe ») sous-jacent d'une `multiprocessing.Queue` a une capacité
    # bornée côté OS — si assez de messages s'accumulent sans jamais être
    # lus pendant qu'un worker est encore profondément dans sa recherche
    # (chaque tentative peut publier jusqu'à ~50-60 fois, voir
    # _worker_best_state_queue), son propre `put()` finit par bloquer tant
    # que personne ne lit le tube ; mais personne ne le lit tant que TOUS
    # les workers de ce palier n'ont pas terminé — et ce worker-là ne peut
    # justement jamais terminer tant que son propre `put()` reste bloqué. Un
    # classique interblocage producteur/consommateur, pas un problème de
    # détection du seuil des 30 % (`attempt_done_event`) ni de workers qui
    # ne s'arrêteraient pas correctement.
    #
    # Corrigé en drainant la file en continu, dans un thread dédié
    # (`threading`, pas `multiprocessing` — ce thread tourne dans le
    # processus PARENT, où GIL ou pas, une boucle qui ne fait qu'attendre
    # sur `Queue.get(timeout=...)` puis `list.append(...)` ne se dispute
    # jamais le GIL avec quoi que ce soit de coûteux) démarré une seule fois
    # pour toute la génération, jamais recréé palier après palier — tant que
    # ce thread tourne, le tube ne peut plus jamais s'accumuler assez pour
    # bloquer un `put()`. `_best_state_buffer`/`_best_state_buffer_lock`
    # accumulent chaque message reçu ; le code de chaque palier (plus bas)
    # n'interagit plus jamais directement avec `best_state_queue` — il vide
    # `_best_state_buffer` sous verrou à la place, ce qui revient exactement
    # au même du point de vue de ce qu'il reçoit, sans jamais risquer de
    # lire directement dans la file pendant qu'un worker y écrit encore.
    best_state_buffer = []
    best_state_buffer_lock = threading.Lock()
    stop_best_state_drain = threading.Event()
    # Horodatage (monotonic) de la dernière publication du pourcentage de
    # budget consommé — voir BUDGET_PROGRESS_REPORT_INTERVAL_S. Une liste
    # à un seul élément (pas une simple variable) uniquement pour rester
    # mutable depuis l'intérieur de la boucle ci-dessous sans `nonlocal`.
    last_budget_progress_report = [0.0]

    def _drain_best_state_queue_continuously():
        while not stop_best_state_drain.is_set():
            try:
                msg = best_state_queue.get(timeout=0.1)
            except queue.Empty:
                msg = None
            if msg is not None:
                with best_state_buffer_lock:
                    best_state_buffer.append(msg)
            # Vérifié à chaque itération de cette boucle (~10 fois par
            # seconde, voir le timeout de get() ci-dessus), pas seulement
            # quand un message vient d'arriver — sinon, un palier dont
            # aucune tentative n'améliore plus son record pendant un long
            # moment ne republierait plus jamais rien du tout, alors que
            # `deadline_checks` continue, lui, réellement de se consommer
            # en arrière-plan dans les workers.
            now = time.monotonic()
            if now - last_budget_progress_report[0] >= BUDGET_PROGRESS_REPORT_INTERVAL_S:
                last_budget_progress_report[0] = now
                with best_state_buffer_lock:
                    checks_seen = [m["checks"] for m in best_state_buffer]
                if checks_seen:
                    percent = min(
                        100, round(100 * max(checks_seen) / resolved_deadline_checks)
                    )
                    progress("budget_progress", percent=percent)

    # Démarré avant même la création du pool, `daemon=True` : ce thread ne
    # doit jamais empêcher le processus de se terminer, y compris sur un
    # chemin de sortie anticipé (`GenerationCancelled`, levée depuis
    # l'intérieur de la boucle ci-dessous) qui ne passerait pas par l'arrêt
    # explicite tout en bas de cette fonction — dans ce cas rare, le thread
    # reste simplement inactif (bloqué sur `get(timeout=0.1)`, sans rien à
    # lire) jusqu'à la fin du processus, un coût négligeable, plutôt qu'un
    # `try`/`finally` englobant toute la boucle des paliers (des centaines
    # de lignes) qui aurait exigé de la réindenter en bloc.
    best_state_drain_thread = threading.Thread(
        target=_drain_best_state_queue_continuously, daemon=True
    )
    best_state_drain_thread.start()
    with concurrent.futures.ProcessPoolExecutor(
        max_workers=PARALLEL_ATTEMPTS, initializer=_init_worker,
        initargs=(index, cancel_event, batch_abandoned_event, attempt_done_event, best_state_queue,
                  warmup_barrier, proper_noun_words, max_proper_nouns,
                  non_gloss_words, max_non_gloss, priority_words)
    ) as executor:
        # Pré-chauffage du pool : force tous les workers à finir leur
        # démarrage réel avant le tout premier palier (voir le docstring de
        # `_warmup_worker`/`warmup_barrier` pour le diagnostic complet et
        # le raisonnement — deux versions plus simples, sans barrière,
        # ont été essayées et mesurées insuffisantes avant d'en arriver
        # là). Soumet exactement `PARALLEL_ATTEMPTS` tâches d'un coup et
        # les attend toutes : la barrière garantit qu'aucun worker ne peut
        # en absorber plusieurs avant que tous les autres n'aient
        # réellement démarré, donc cette attente ne se termine que si
        # `PARALLEL_ATTEMPTS` processus distincts existent bel et bien.
        # `BrokenBarrierError` est rattrapée par sécurité (jamais
        # observée en pratique) plutôt que de faire planter toute la
        # génération si, sur une machine donnée, moins de
        # `PARALLEL_ATTEMPTS` processus ne peuvent structurellement
        # jamais coexister — dans ce cas le pré-chauffage échoue,
        # mais generate_grid continue quand même (au pire, la
        # numérotation redevient sujette au même warm-up qu'avant ce
        # correctif, jamais un blocage total).
        try:
            warmup_futures = [executor.submit(_warmup_worker) for _ in range(PARALLEL_ATTEMPTS)]
            concurrent.futures.wait(warmup_futures)
        except threading.BrokenBarrierError:
            pass
        for attempt in range(attempts):
            if cancel_event is not None and cancel_event.is_set():
                raise GenerationCancelled()
            if should_pause is not None and should_pause():
                # Même mécanisme de sérialisation que la sortie "attempts
                # épuisés" plus bas (voir _serialize_resume_state) — voir
                # GenerationPaused's own docstring. `None` seulement si
                # cette toute première itération (attempt == 0) est déjà
                # celle qui cède son tour, avant qu'aucun palier n'ait
                # jamais tourné du tout.
                raise GenerationPaused(
                    _serialize_resume_state(
                        carry_seed_grid, carry_locked_letters,
                        carry_preseed_assignment, carry_excluded_slots,
                    )
                    if carry_seed_grid is not None else None
                )
            batch_abandoned_event.clear()
            attempt_done_event.clear()
            # Vivier des grilles de départ candidates pour ce palier (voir
            # `carry_seed_pool`, sa propre définition plus haut) — calculé
            # ici, avant même de savoir si ce palier sera une reprise
            # "telle quelle" ou un motif neuf, pour que cet aperçu de
            # DÉBUT de cycle puisse déjà en tenir compte, pas seulement
            # celui de "cases noires posées" plus bas (qui, lui, le
            # calculait déjà). Repli sur `[(carry_seed_grid, carry_locked_
            # letters)]` (comportement d'avant cette fonctionnalité) tant
            # qu'aucun nettoyage complet n'a encore renseigné `carry_seed_
            # pool` — voir sa propre définition pour le détail complet.
            pool = carry_seed_pool if carry_seed_pool else [(carry_seed_grid, carry_locked_letters)]
            # Parallèle à `pool` (même longueur, même ordre) — voir
            # `_build_dispatch_lineage`/`carry_seed_pool_lineage`'s propre
            # définition. `[1]` de repli tant qu'aucun nettoyage complet n'a
            # encore renseigné `carry_seed_pool_lineage`, cohérent avec le
            # repli de `pool` lui-même (une seule entrée dans les deux cas).
            pool_lineage = carry_seed_pool_lineage if carry_seed_pool_lineage else [1]
            if carry_preseed_assignment is not None:
                # Un aperçu par grille du vivier (`carry_seed_pool_
                # continue`), pas un seul, à la demande explicite de
                # l'utilisateur (voir la définition de `carry_seed_pool_
                # continue`) — même principe que le "motif neuf" ci-dessous,
                # désormais aussi vrai pour la reprise "telle quelle" :
                # chaque tentative distincte du palier précédent a pu être
                # nettoyée différemment (mots retirés différents, parfois une
                # case noire ajoutée), donc le prochain palier peut
                # réellement démarrer sur plusieurs motifs/affectations
                # distincts, pas un seul comme avant cette fonctionnalité.
                # Repli sur `[(carry_seed_grid, carry_preseed_assignment,
                # carry_excluded_slots)]` (comportement d'avant cette
                # fonctionnalité) tant qu'aucun palier "telle quelle" n'a
                # encore renseigné `carry_seed_pool_continue`. Les
                # tentatives réinitialisées de ce palier (`reset_count` plus
                # bas, un motif entièrement neuf) ne sont volontairement pas
                # préviewées séparément ici — même convention que la branche
                # "motif neuf" ci-dessous, dont le propre vivier ne les
                # préviewe pas non plus.
                continue_pool = carry_seed_pool_continue if carry_seed_pool_continue else [
                    (carry_seed_grid, carry_preseed_assignment, carry_excluded_slots)
                ]
                # Parallèle à `continue_pool` — même rôle que `pool_lineage`
                # ci-dessus, pour le vivier de reprise "telle quelle".
                continue_pool_lineage = (
                    carry_seed_pool_continue_lineage if carry_seed_pool_continue_lineage else [1]
                )
                seen_continue_patterns = set()
                cycle_start_examples = []
                for pool_idx, (pool_grid, pool_preseed, _pool_excluded) in enumerate(continue_pool):
                    pattern_key = tuple(tuple(row) for row in pool_grid)
                    if pattern_key in seen_continue_patterns:
                        continue
                    seen_continue_patterns.add(pattern_key)
                    start_grid, start_locked_cells = _cycle_start_preview(
                        rows, cols, pool_grid, None, pool_preseed,
                    )
                    cycle_start_examples.append({
                        "example_grid": start_grid,
                        "impossible_cells": [],
                        "forced_cells": [],
                        "locked_cells": start_locked_cells,
                        "theme_cells": _theme_cells_from_preview_state(
                            pool_grid, rows, cols, None, pool_preseed, priority_words
                        ),
                        "low_candidate_cells": [],
                        "noise_cells": [],
                        "process_number": continue_pool_lineage[pool_idx % len(continue_pool_lineage)],
                        # `continue_pool[0]` (jamais un doublon — le premier
                        # examiné, `seen_continue_patterns` encore vide à ce
                        # moment-là) est la meilleure grille du vivier, voir
                        # `_continue_seed_pool`/`_sort_examples_by_process`.
                        "is_best": pool_idx == 0,
                    })
            else:
                # Un aperçu par grille du vivier, pas un seul, à la demande
                # explicite de l'utilisateur : "Les extraits 'Génération du
                # motif de cases noires' ne montrent qu'une seule grille.
                # Il devrait maintenant y en avoir N pour N process." —
                # même principe et même dédoublonnage (par motif noir/blanc
                # réel, `pool_grid`, jamais l'état déjà recouvert de
                # lettres) que l'aperçu "cases noires posées" plus bas, qui
                # avait déjà cette diversité ; seul cet aperçu de tout début
                # de cycle en manquait encore. Cases sous le seuil de
                # remplissage (< PREFILL_LOCKED_MIN_WORD_COUNT candidats, à
                # la demande explicite de l'utilisateur — voir _low_
                # candidate_slot_cells) calculées désormais pour chaque
                # grille du vivier individuellement, sur ses propres
                # lettres verrouillées — jamais celles d'une autre entrée du
                # vivier. `pool_grid is None` seulement pour le tout premier
                # palier d'une génération (rien encore verrouillé nulle
                # part) — une seule grille vierge dans le vivier dans ce
                # cas, donc rien à dédupliquer ni à évaluer.
                seen_cycle_start_patterns = set()
                cycle_start_examples = []
                for pool_idx, (pool_grid, pool_locked) in enumerate(pool):
                    pattern_key = None
                    if pool_grid is not None:
                        pattern_key = tuple(tuple(row) for row in pool_grid)
                        if pattern_key in seen_cycle_start_patterns:
                            continue
                        seen_cycle_start_patterns.add(pattern_key)
                    start_grid, start_locked_cells = _cycle_start_preview(
                        rows, cols, pool_grid, pool_locked, None,
                    )
                    low_candidate_cells = (
                        _low_candidate_slot_cells(pool_grid, rows, cols, index, pool_locked)
                        if pool_grid is not None else []
                    )
                    # Cases sans aucune proposition réellement jouable
                    # (`NOISE_FREQUENCY_THRESHOLD`), à la demande explicite
                    # de l'utilisateur — même calcul par grille du vivier
                    # individuelle, même portée (jamais pour un palier de
                    # reprise "telle quelle") que low_candidate_cells
                    # ci-dessus, voir _noise_slot_cells.
                    noise_cells = (
                        _noise_slot_cells(pool_grid, rows, cols, index, pool_locked)
                        if pool_grid is not None else []
                    )
                    cycle_start_examples.append({
                        "example_grid": start_grid,
                        "impossible_cells": [],
                        "forced_cells": [],
                        "locked_cells": start_locked_cells,
                        "theme_cells": _theme_cells_from_preview_state(
                            pool_grid, rows, cols, pool_locked, None, priority_words
                        ),
                        "low_candidate_cells": low_candidate_cells,
                        "noise_cells": noise_cells,
                        "process_number": (
                            pool_lineage[pool_idx % len(pool_lineage)]
                            if pattern_key is not None else None
                        ),
                        # `pool[0]` (jamais un doublon — le premier examiné,
                        # `seen_cycle_start_patterns` encore vide à ce
                        # moment-là) est la meilleure grille du vivier, voir
                        # `_seed_pool`/`_sort_examples_by_process`.
                        "is_best": pool_idx == 0,
                    })
            progress("pattern", attempt=attempt + 1, attempts=attempts, parallel=PARALLEL_ATTEMPTS,
                     total_attempts=total_attempts_tried,
                     examples=_sort_examples_by_process(cycle_start_examples))
            seeds = [rng.randrange(2**31) for _ in range(PARALLEL_ATTEMPTS)]
            if carry_preseed_assignment is not None:
                # `reset_count` tentatives de ce palier "reprise telle
                # quelle" repartent d'un motif entièrement neuf
                # (`_pattern_attempt`, seed_grid=None — jamais `_pattern_
                # continue`, puisqu'il n'y a alors ni motif ni verrouillage
                # antérieur à reprendre) au lieu de la reprise individuelle
                # sur leur propre entrée du vivier — à la demande explicite
                # de l'utilisateur : "chaque process doit repartir à l'étape
                # suivante avec sa grille partiellement nettoyée (sauf le
                # pourcentage de grilles entièrement neuves)." Contrairement
                # au "motif neuf" ci-dessous (`reset_count` conditionné par
                # `just_cleaned`, seulement juste après un nettoyage
                # complet), s'applique ici inconditionnellement à CHAQUE
                # palier "reprise telle quelle" — il n'y a pas d'équivalent
                # de `just_cleaned` à distinguer, puisqu'un tel palier est
                # déjà, par construction, toujours la suite d'un état
                # précédent (jamais un tout premier palier, qui part
                # toujours de `carry_seed_grid is None`, donc de la branche
                # "motif neuf" ci-dessous). Chaque tentative non
                # réinitialisée (`i >= reset_count`) reçoit sa propre entrée
                # du vivier (`continue_pool`, déjà calculé plus haut pour
                # l'aperçu de ce même palier) — un simple parcours cyclique
                # (`% len(continue_pool)`) répartit les entrées disponibles
                # sur les places non réinitialisées, comme pour le "motif
                # neuf" ci-dessous.
                reset_count = FULL_RESET_ATTEMPT_COUNT
                # Numéro de "lignée" (voir `_build_dispatch_lineage`) hérité
                # par chacune des PARALLEL_ATTEMPTS tâches de CE palier,
                # avant même de les soumettre — `continue_pool_lineage`,
                # calculé plus haut pour l'aperçu de ce même palier, reste
                # parallèle à `continue_pool` (même ordre, même longueur).
                dispatch_lineage = _build_dispatch_lineage(
                    PARALLEL_ATTEMPTS, reset_count, continue_pool_lineage
                )
                futures = []
                for i, s in enumerate(seeds):
                    if i < reset_count:
                        futures.append(executor.submit(
                            _pattern_attempt, rows, cols, ratio, s, force_letters_fraction,
                            None, None,
                            black_enrichment_fraction, deadline_checks,
                            permanent_locked_letters, permanent_black_cells,
                        ))
                    else:
                        task_seed_grid, task_preseed_assignment, task_excluded_slots = (
                            continue_pool[(i - reset_count) % len(continue_pool)]
                        )
                        futures.append(executor.submit(
                            _pattern_continue, rows, cols, s, task_seed_grid,
                            task_preseed_assignment, task_excluded_slots,
                            force_letters_fraction, deadline_checks,
                            permanent_locked_letters,
                        ))
            else:
                # A fraction of this palier's own workers start from a
                # totally blank grid instead of the just-cleaned
                # carry_seed_grid/carry_locked_letters every other worker
                # gets, right after a full cleanup — at the user's explicit
                # request, see FULL_RESET_ATTEMPT_COUNT's own docstring.
                # No particular reason to prefer one seed over another for
                # which of them get reset — `seeds` are already
                # independently random, so simply resetting the first
                # `reset_count` of them is as good as any other choice.
                # Never applies right after a "reprise telle quelle"
                # palier (`just_cleaned` is only ever True right after the
                # `else:`/nettoyage branch below) nor on the very first
                # palier of a call with no prior cleanup at all.
                reset_count = FULL_RESET_ATTEMPT_COUNT if just_cleaned else 0
                # `pool` (voir `carry_seed_pool`'s propre définition plus
                # haut) déjà calculé au tout début de ce palier, pour
                # l'aperçu "Génération du motif de cases noires" — réutilisé
                # tel quel ici, jamais recalculé une seconde fois pour les
                # workers non réinitialisés de CE palier ni pour l'aperçu
                # "cases noires posées" juste en dessous.
                # Aperçu "cases noires posées, recherche des mots en
                # cours" publié dès MAINTENANT — avant même de soumettre
                # la moindre tentative parallèle à l'executor, donc bien
                # avant que la recherche CSP (la partie lente) de ce
                # palier ne termine — à la demande explicite de
                # l'utilisateur : "Le Front n'affiche les aperçus qu'après
                # la fin d'un cycle. Les états d'initialisation
                # n'apparaissent pas avant la fin du cycle. Il faut que la
                # stack Back soit proprement alimentée à chaque étape du
                # cycle." Root-causé directement dans le code, pas
                # supposé : le `pattern_generated` existant plus bas
                # (`_cycle_start_preview` sur `failed_pairs`/`best`) n'est
                # calculable qu'une fois TOUTES les tentatives parallèles
                # de ce palier terminées (`concurrent.futures.
                # as_completed`, plus bas) — pour un palier "motif neuf"
                # (celui-ci, pas la reprise "telle quelle" ci-dessus, dont
                # le propre `pattern_generated` coïncide déjà avec l'état
                # de départ du cycle), l'étape "cases noires posées" ne
                # pouvait donc jamais réellement apparaître avant la fin
                # du cycle, quelle que soit la rapidité du Front à
                # l'afficher — le Back lui-même ne l'avait tout simplement
                # pas encore calculée. Reconstruit ici, dans le processus
                # PARENT, avec les mêmes paramètres qu'un worker réel
                # utilisera dans son propre processus séparé plus bas —
                # `make_pattern` étant une fonction pure de ses arguments,
                # appelée deux fois avec le même seed produit le même motif
                # les deux fois, donc jamais de fausse impression de case
                # noire "déplacée" une fois le véritable `pattern_generated`
                # (calculé après coup, voir plus bas) reçu.
                #
                # Une initialisation PAR PROCESS, mais réservée à la toute
                # première initialisation de la génération (`carry_seed_grid
                # is None` — aucun palier précédent n'a encore tourné) — à
                # la demande explicite de l'utilisateur : "la toute première
                # initialisation des cases noires ne prépare qu'une seule
                # grille. Intégrer cette première initialisation au début du
                # cycle, de manière à créer une initialisation par process."
                # Clarifié par l'utilisateur lui-même après une première
                # implémentation qui l'appliquait à *chaque* cycle "motif
                # neuf" (pas seulement le tout premier), provoquant un vrai
                # ralentissement mesuré en direct (jusqu'à +250 % par palier)
                # et, plus grave, un vrai risque de faire échouer une
                # génération qui aurait sinon réussi (le calcul séquentiel
                # supplémentaire dans le processus parent décale le timing
                # réel auquel les tentatives parallèles sont soumises, et ce
                # palier utilise un mécanisme d'interruption sensible à
                # l'ordre réel d'achèvement — `attempt_done_event`/
                # `batch_abandoned_event` — pas seulement à la graine) :
                # "Il ne faut pas changer le budget, juste initialiser N
                # grilles au premier cycle au lieu d'une seule. Les cycles
                # suivants, à partir de 2, reprendront la meilleure grille
                # (sauf 20% de nouvelles grilles)." À partir du 2e palier,
                # `carry_seed_grid` porte déjà le contenu du meilleur essai
                # précédent (ou repart d'une grille vierge pour les
                # `reset_count` tentatives réinitialisées, déjà sa propre
                # source de diversité) — la diversité "une grille par
                # process" n'a donc de sens réel qu'au tout premier palier,
                # où rien ne distingue encore les tentatives entre elles à
                # part leur propre graine.
                if carry_seed_grid is None:
                    # Chaque worker part ici d'une grille vierge, indépendamment
                    # des autres (voir plus bas) — chacun reçoit donc SA PROPRE
                    # lignée dès sa création, numérotée 1..PARALLEL_ATTEMPTS
                    # selon son propre index de soumission, plutôt que le
                    # `None`/pas-de-numéro d'avant cette fonctionnalité : à la
                    # demande explicite de l'utilisateur, une grille doit
                    # porter son numéro dès l'instant où elle existe, pas
                    # seulement une fois son premier résultat réel connu.
                    dispatch_lineage = list(range(1, PARALLEL_ATTEMPTS + 1))
                    # Une par process (jusqu'à PARALLEL_ATTEMPTS), à la
                    # demande explicite de l'utilisateur : "il n'y a jamais
                    # eu 6 grilles par process, mais 1 grille par process
                    # (1 process par processeur)." — même principe ici : un
                    # calcul par tentative sur le point d'être soumise,
                    # dédupliqué par motif noir/blanc réel (deux workers
                    # peuvent légitimement retomber sur le même motif), sans
                    # aucun plafond au-delà de cette déduplication — à la
                    # demande explicite de l'utilisateur ("Afficher toutes
                    # les meilleures grilles dans l'aperçu, pas seulement les
                    # 6 meilleures"), qui retire le plafond `FAILED_ATTEMPT_
                    # EXAMPLES` (6) auparavant appliqué ici — même convention
                    # (déduplication, sans plafond) que celle déjà utilisée
                    # plus bas pour les motifs réellement recherchés
                    # (`failed_unique`).
                    seen_early_patterns = set()
                    early_examples = []
                    for i, s in enumerate(seeds):
                        early_pattern = make_pattern(
                            rows, cols, ratio, random.Random(s),
                            available_lengths=available_lengths_preview,
                            seed_grid=None, locked_letters=permanent_locked_letters or None,
                            index=index, black_enrichment_fraction=black_enrichment_fraction,
                        )
                        pattern_key = tuple(tuple(row) for row in early_pattern)
                        if pattern_key in seen_early_patterns:
                            continue
                        seen_early_patterns.add(pattern_key)
                        early_pattern_grid, early_pattern_locked = _cycle_start_preview(
                            rows, cols, early_pattern, None, None,
                        )
                        early_examples.append({
                            "example_grid": early_pattern_grid,
                            "impossible_cells": [],
                            "forced_cells": [],
                            "locked_cells": early_pattern_locked,
                            # Tout premier palier : rien n'est encore verrouillé
                            # ni assigné, donc aucun mot thématique à signaler.
                            "theme_cells": [],
                            "process_number": dispatch_lineage[i],
                            # Aucune comparaison n'a encore eu lieu à ce
                            # stade (tout premier palier, chaque tentative
                            # part indépendamment d'une grille vierge) —
                            # aucune n'est donc "la meilleure" pour l'instant.
                            "is_best": False,
                        })
                else:
                    # Un aperçu par grille du vivier (pas un seul), à la
                    # demande explicite de l'utilisateur : depuis que le
                    # prochain palier peut réellement démarrer sur plusieurs
                    # motifs distincts (voir `pool` ci-dessus), un aperçu
                    # unique reconstruit à partir de `carry_seed_grid` seul
                    # ne correspondrait plus forcément à ce qu'un worker réel
                    # calculera — exactement la classe de bug déjà rencontrée
                    # plusieurs fois dans ce fichier pour un motif "modèle"
                    # qui finit par diverger de la réalité une fois plusieurs
                    # variantes en jeu (voir CLAUDE.md). Pour chaque entrée du
                    # vivier, reconstruit ici, dans le processus PARENT,
                    # exactement le même motif (mêmes paramètres, même
                    # graine) que le PREMIER worker réel à qui cette entrée
                    # sera effectivement assignée dans `futures` plus bas
                    # (`seeds[reset_count + p]` pour la p-ième entrée du
                    # vivier — toujours un index valide : le vivier ne
                    # contient jamais plus d'entrées que de places non
                    # réinitialisées, voir `_seed_pool`). Même dédoublonnage
                    # par motif réel, sans aucun plafond, que la branche
                    # "tout premier palier" ci-dessus, pas un mécanisme
                    # distinct — seule la source (le vivier, plutôt que
                    # `seeds` sur une grille vierge commune) diffère.
                    dispatch_lineage = _build_dispatch_lineage(
                        PARALLEL_ATTEMPTS, reset_count, pool_lineage
                    )
                    seen_pool_patterns = set()
                    early_examples = []
                    for p, (pool_grid, pool_locked) in enumerate(pool):
                        # `min(..., len(seeds) - 1)` : filet de sécurité pour
                        # un cas dégénéré (PARALLEL_ATTEMPTS <= FULL_RESET_
                        # ATTEMPT_COUNT, jamais le cas avec les valeurs par
                        # défaut) où `reset_count + p` déborderait sinon de
                        # `seeds` — jamais atteint en pratique (voir
                        # `_seed_pool`, qui garantit déjà `len(pool) <=
                        # PARALLEL_ATTEMPTS - reset_count` dans le cas normal),
                        # mais un aperçu approximatif reste préférable à un
                        # plantage pur et simple.
                        early_pattern = make_pattern(
                            rows, cols, ratio,
                            random.Random(seeds[min(reset_count + p, len(seeds) - 1)]),
                            available_lengths=available_lengths_preview,
                            seed_grid=pool_grid,
                            locked_letters=(
                                {**(pool_locked or {}), **permanent_locked_letters}
                                if permanent_locked_letters else pool_locked
                            ),
                            index=index, black_enrichment_fraction=black_enrichment_fraction,
                        )
                        pattern_key = tuple(tuple(row) for row in early_pattern)
                        if pattern_key in seen_pool_patterns:
                            continue
                        seen_pool_patterns.add(pattern_key)
                        early_pattern_grid, early_pattern_locked = _cycle_start_preview(
                            rows, cols, early_pattern, pool_locked, None,
                        )
                        # Le numéro de lignée se lit directement sur la
                        # POSITION de cette entrée dans le vivier
                        # (`pool_lineage`, parallèle à `pool` — voir sa
                        # propre définition), pas sur le contenu de
                        # `pool_grid`/`early_pattern` : deux entrées
                        # distinctes du vivier peuvent, en théorie, produire
                        # un motif identique sans être la même lignée, donc
                        # seule la position fait foi.
                        early_examples.append({
                            "example_grid": early_pattern_grid,
                            "impossible_cells": [],
                            "forced_cells": [],
                            "locked_cells": early_pattern_locked,
                            "theme_cells": _theme_cells_from_preview_state(
                                early_pattern, rows, cols, pool_locked, None, priority_words
                            ),
                            "process_number": pool_lineage[p % len(pool_lineage)],
                            # `pool[0]` (jamais un doublon — le premier
                            # examiné, `seen_pool_patterns` encore vide à ce
                            # moment-là) est la meilleure grille du vivier.
                            "is_best": p == 0,
                        })
                progress(
                    "pattern_generated", attempt=attempt + 1, attempts=attempts,
                    total_attempts=total_attempts_tried,
                    examples=_sort_examples_by_process(early_examples),
                )
                # Chaque worker non réinitialisé (`i >= reset_count`) reçoit
                # sa propre entrée du vivier (`pool`, voir sa définition plus
                # haut), pas systématiquement `carry_seed_grid` — à la
                # demande explicite de l'utilisateur. Un simple parcours
                # cyclique (`% len(pool)`) répartit les entrées disponibles
                # sur les places non réinitialisées ; dans le cas normal
                # (`len(pool) == PARALLEL_ATTEMPTS - reset_count`, garanti
                # par `_seed_pool`), ce cycle ne boucle jamais réellement —
                # chaque place reçoit une entrée distincte, une seule fois.
                # Il ne boucle que si `failed_pairs` avait, exceptionnellement,
                # moins d'entrées que de places à pourvoir (dédoublonnage par
                # contenu, voir son propre commentaire) — dans ce cas précis
                # seulement, une même grille nettoyée peut légitimement se
                # retrouver reprise par plus d'un worker, chacun avec sa
                # propre graine.
                futures = []
                for i, s in enumerate(seeds):
                    if i < reset_count:
                        task_seed_grid, task_locked_letters = None, None
                    else:
                        task_seed_grid, task_locked_letters = pool[(i - reset_count) % len(pool)]
                    futures.append(executor.submit(
                        _pattern_attempt, rows, cols, ratio, s, force_letters_fraction,
                        task_seed_grid, task_locked_letters,
                        black_enrichment_fraction, deadline_checks,
                        permanent_locked_letters, permanent_black_cells,
                    ))
            # Récupérés dans l'ordre d'achèvement (`as_completed`), pas
            # l'ordre de soumission, à la demande explicite de l'utilisateur
            # ("le bouton Stop ne s'applique pas rapidement... prévoir
            # l'arrêt dans toutes les phases") : dès qu'une des
            # PARALLEL_ATTEMPTS tentatives lève GenerationCancelled (chaque
            # worker vérifie le même `cancel_event`, voir Filler._backtrack),
            # on relance l'exception immédiatement plutôt que d'attendre
            # aussi le résultat des autres — `outcomes`'s propre ordre
            # n'a pas d'importance pour le reste de cette boucle (le
            # meilleur résultat est toujours choisi via `max`/tri, jamais
            # par position). Les autres tentatives encore en cours
            # détecteront la même annulation à leur propre prochain point de
            # contrôle (au plus CANCEL_CHECK_INTERVAL vérifications plus
            # tard) et s'arrêteront à leur tour — `with ... as executor`
            # attend leur fin normale à la sortie du bloc (comportement par
            # défaut de ProcessPoolExecutor), mais ce délai reste court,
            # sans rapport avec le budget `deadline_checks` complet d'un
            # palier.
            # Collected in completion order (`as_completed`), not submission
            # order — once PALIER_ATTEMPT_INTERRUPT_FRACTION (30%) of this
            # batch's attempts have finished (success or failure alike),
            # `attempt_done_event` is set so every other still-running
            # attempt stops at its own next checkpoint (see
            # PALIER_ATTEMPT_DONE_CHECK_INTERVAL), at the user's explicit
            # request — refining the initial design, which interrupted as
            # soon as the very first attempt finished: "à partir de 30% des
            # tentatives qui se terminent, interrompre toutes les
            # tentatives." `math.ceil` (never floor/round) so a small
            # PARALLEL_ATTEMPTS still interrupts on genuine 30%-or-more
            # progress rather than possibly rounding down to 0 and
            # interrupting immediately again; `max(1, ...)` as a floor so
            # this can never itself require MORE completions than
            # `interrupt_threshold == 1` would already need. We still call
            # `.result()` on every future here (draining them all) rather
            # than abandoning them outright: with
            # `max_workers=PARALLEL_ATTEMPTS` persistent worker processes,
            # every future is already running by the time we get here, and
            # there is no way to reclaim a worker process without either
            # waiting for its current task to return or forcibly killing
            # it (never done anywhere in this file — see cancel_event's own
            # cooperative, non-destructive design) — but that wait is now
            # bounded to roughly PALIER_ATTEMPT_DONE_CHECK_INTERVAL more
            # checks per straggler instead of its full deadline_checks
            # budget, the same kind of bound already measured for
            # cancel_event (~0.79s in that case). A future that raises
            # GenerationCancelled (the user's own "Stop" button, a
            # different and higher-priority signal — see Filler._backtrack)
            # still propagates immediately here, exactly as before this
            # feature.
            # `dispatch_lineage` (voir sa propre construction plus haut, dans
            # chacune des deux branches ci-dessus) associe, à chaque INDEX de
            # soumission (0..PARALLEL_ATTEMPTS-1), le numéro de lignée dont
            # cette tâche hérite — jamais l'ordre d'achèvement, qui n'a
            # aucun rapport avec la lignée. `seeds[i]` est déjà l'`attempt_id`
            # que chaque diagnostic réel (voir `diag["attempt_id"]` dans
            # `_pattern_attempt`/`_pattern_continue`) et chaque état publié
            # en cours de route (voir `_publish_new_best`) porte déjà, donc
            # cette correspondance seed -> lignée suffit à retrouver le bon
            # numéro pour n'importe lequel des deux, sans avoir besoin de
            # savoir quel worker (PID) l'a produit.
            seed_to_lineage = {seeds[i]: dispatch_lineage[i] for i in range(PARALLEL_ATTEMPTS)}
            interrupt_threshold = max(1, math.ceil(PALIER_ATTEMPT_INTERRUPT_FRACTION * len(futures)))
            outcomes = []
            for f in concurrent.futures.as_completed(futures):
                outcomes.append(f.result())
                if len(outcomes) == interrupt_threshold:
                    attempt_done_event.set()
            # Attache à chaque diag de ce palier (succès et échecs confondus)
            # le numéro de lignée hérité de la tâche qui l'a produit
            # (`seed_to_lineage`, voir sa propre construction plus haut) —
            # fait une seule fois ici, pour que tout ce qui lit
            # `d["process_number"]` plus loin (aperçus, sélection du
            # vainqueur) le trouve déjà prêt. `None` pour une tâche
            # réinitialisée dont la lignée n'a pas encore été résolue (voir
            # `_reassign_lineage_numbers`, plus bas, qui ne s'applique
            # qu'aux candidats SURVIVANTS du vivier du prochain palier).
            for _, _, d in outcomes:
                d["process_number"] = seed_to_lineage.get(d.get("attempt_id"))
            successes = [(g, r, d) for g, r, d in outcomes if r is not None]
            # Dédoublonnage des tentatives échouées, à la demande explicite de
            # l'utilisateur, après un bug réel constaté en direct : une fois
            # qu'une bonne partie de la grille est verrouillée par le
            # mécanisme de reprise entre paliers ci-dessous, la zone encore
            # libre peut devenir si restreinte que les PARALLEL_ATTEMPTS
            # tentatives parallèles — pourtant lancées avec des seeds
            # différents — convergent vers EXACTEMENT le même motif et la
            # même impasse (reproduit en direct : dès le 6e palier d'une
            # grille durcie, les 10 tentatives donnaient un seul motif
            # distinct au lieu de 6+). Sans dédoublonnage, l'aperçu montrait
            # la même grille répétée 6 fois au lieu de 6 tentatives
            # réellement différentes. Deux tentatives comptent comme
            # identiques seulement si leur motif noir/blanc *et* leur
            # affectation de mots sont tous deux rigoureusement égaux (pas
            # seulement le motif seul, au cas où deux motifs identiques
            # aboutiraient malgré tout à des lettres différentes). Ce
            # dédoublonnage ne sert plus qu'à choisir *quels* aperçus
            # montrer (voir `failed_pairs` plus bas) — plus au calcul de
            # `total_attempts` lui-même, voir juste en dessous.
            failed_all = [(g, d) for g, r, d in outcomes if r is None]
            # Attempts cut short by attempt_done_event (see above) used to
            # be excluded entirely from "which failed attempt is the best
            # one" (failed_unique/failed_pairs below) — reverted at the
            # user's explicit request: "Il ne faut pas supprimer les 70%
            # des tentatives restantes, mais seulement les interrompre.
            # Chacune d'elle porte normalement la mémorisation de sa
            # meilleure grille échouée, qu'il faut prendre en compte." An
            # interrupted worker's own diagnostics (`example_grid`/
            # `assignment`/`impossible_cells`) are never empty placeholders
            # — `try_fill` already builds them from `Filler.best_assignment`
            # (the same high-water-mark snapshot a naturally-concluded
            # failure uses too, see its own docstring), a real, genuine
            # state that worker actually reached before being told to
            # stop, not a fabricated or trivial one — discarding it
            # outright threw away real, already-computed progress for no
            # benefit. `total_attempts_tried`'s own `checks` summation
            # below is unaffected either way (it already summed over every
            # raw outcome, interrupted ones included, both before and
            # after this change).
            #
            # `failed_real` (kept as its own name so nothing below needs to
            # change) is therefore now just `failed_all` — no more `reason
            # != "interrupted_other_attempt_done"` filtering. A worker
            # interrupted so early it never made any real progress at all
            # — "encore en train d'essayer de construire", per the user's
            # own words — naturally shows up with `assigned_letter_count
            # == 0`/an all-blank `example_grid` instead; nothing special
            # needs to filter it out on purpose, since `_backtrack`'s own
            # interruption check only ever runs from *inside* the search
            # loop (never before it starts), so `Filler.best_assignment`
            # already reflects whatever little (or, in the rare worst
            # case, nothing at all) that worker managed before being told
            # to stop.
            #
            # Caveat carried over from the design this reverts, worth
            # keeping in mind rather than silently dropping: an
            # interrupted worker's own `impossible_cells` count (the sort
            # key `failed_pairs` uses just below) can be misleadingly LOW
            # simply because it had less time to explore and so discover
            # fewer of them — not necessarily because it's genuinely
            # closer to a solution than a naturally-concluded worker that
            # explored much further and found more. Not addressed by this
            # change (the user asked specifically to stop discarding this
            # data, not to redesign the sort criterion) — flagged here so
            # a future report of "the carried-forward grid looks worse
            # than a failed one that explored further" has a documented,
            # plausible starting hypothesis.
            failed_real = failed_all
            seen_keys = set()
            failed_unique = []
            for g, d in failed_real:
                key = (tuple(map(tuple, g)), tuple(d["assignment"]))
                if key not in seen_keys:
                    seen_keys.add(key)
                    failed_unique.append((g, d))
            # `failed_unique` reste construit uniquement à partir de
            # résultats *réels* de recherches (`failed_all`, ci-dessus) —
            # jamais des états publiés en temps réel par `best_state_queue`
            # (voir plus bas) : c'est ce pool, et lui seul, qui décide de
            # `failed_pairs`/`selected_grid`/`selected_diag`/`still_has_hope`
            # /`_build_retry_seed` — tout ce qui influence réellement la
            # progression de la recherche d'un palier à l'autre. Un état
            # publié par la file reste visible dans l'aperçu affiché à
            # l'écran (voir `display_pairs`/`last_examples` plus bas), mais
            # ne peut plus jamais devenir la base du palier suivant à la
            # place d'un résultat réellement abouti — à la demande explicite
            # de l'utilisateur, après un vrai échec mesuré en direct : même
            # une fois le critère de tri corrigé (voir `_playable_score`
            # plus bas), laisser un état intermédiaire concourir pour cette
            # sélection restait risqué, puisque la recherche qui l'a produit
            # n'était pas allée assez loin pour détecter tous les vrais
            # conflits — son propre `impossible_slots` peut donc être
            # incomplet par rapport à celui d'une recherche réellement
            # terminée, ce qui rendrait le nettoyage du palier suivant
            # (`_build_retry_seed`, qui se fie justement à `impossible_
            # slots` pour décider quels mots retirer) lui-même incomplet.
            # `total_attempts` compte les grilles réellement essayées et
            # abandonnées au sens propre du mot, à la demande explicite de
            # l'utilisateur — pas le nombre de processus parallèles lancés
            # (10 par palier), qui ne reflète absolument pas le travail
            # réel effectué : le remplissage CSP procède par essais
            # successifs avec retour en arrière (voir Filler._backtrack) —
            # chaque tentative de poser un mot (`filler.checks`, incrémenté
            # une fois par mot candidat essayé dans la boucle de
            # `_backtrack`, qu'il mène ou non à une descente récursive plus
            # loin — voir le commentaire de cette boucle pour pourquoi ce
            # compteur n'est plus lié à la seule profondeur de récursion)
            # représente une configuration de grille réellement tentée puis
            # abandonnée dès que la recherche recule ou rejette ce mot.
            # Sommé sur TOUTES les tentatives
            # échouées de ce palier, y compris les doublons ci-dessus — un
            # motif identique retrouvé par deux workers différents (seeds
            # différents) a quand même nécessité, dans chaque worker, son
            # propre travail de recherche réel (un chemin de retours en
            # arrière qui peut différer même si le résultat final converge),
            # donc aucune des deux quantités de travail n'est à ignorer.
            total_attempts_tried += sum(d["checks"] for _, d in failed_all)
            if successes:
                # Several of the PARALLEL_ATTEMPTS attempts can succeed in
                # the same palier — as_completed(futures) above already
                # drained every one of them before this code runs, so this
                # choice always sees the palier's full, final outcome, never
                # a partial one. When there's more than one, each is
                # actually optimized (a trial minimize_black_squares pass on
                # its own defensive grid copy — that function mutates its
                # `grid` argument in place, so a real attempt's own grid is
                # never touched) before deciding which to keep, at the
                # user's explicit request: "Vérifier que toutes les grilles
                # terminées passent par la phase d'optimisation, et que
                # c'est bien la meilleure après optimisation qui est
                # gardée." Previously only one candidate — whichever
                # maximized the sum of squares of every word's length
                # BEFORE any optimization — was ever chosen and optimized;
                # every other success of the same palier was silently
                # discarded, unoptimized and never compared, even though a
                # candidate that looks weaker before optimization can
                # legitimately end up with fewer black cells once optimized
                # (optimization depends on the pattern's precise shape, not
                # just how many words/letters were already placed). The
                # real comparison criterion is now the number of black
                # cells AFTER optimization (fewer is better, consistent
                # with this whole project's own black-cell-minimization
                # goal — see minimize_black_squares), with the pre-existing
                # sum-of-squares-of-word-lengths score as a tie-break at
                # equal black-cell count.
                #
                # `best`/`best_result` are still set to the WINNING
                # candidate's own state BEFORE optimization, exactly as
                # before this change — the trial optimization above is only
                # used to rank candidates against each other, its own
                # result is thrown away. The single, real optimization that
                # actually produces the grid used downstream still happens
                # exactly once, in the unchanged minimize_black_squares
                # call right after this whole palier loop — so the
                # "minimizing" preview built from `best_result` there still
                # faithfully shows the true pre-optimization state of
                # whichever candidate really does end up chosen, and
                # `optimization_duration_seconds` (backend/app.py) still
                # measures exactly one real optimization pass, not several.
                #
                # Skipped entirely when there's only a single success (the
                # overwhelmingly common case) — nothing to compare, so
                # trialing it here would just pay for the exact same
                # optimization work twice (once as a throwaway trial, once
                # for real right after the loop) for no benefit.
                if len(successes) == 1:
                    best, best_result, best_diag = successes[0]
                else:
                    scored = []
                    for g, r, d in successes:
                        cand_slots, cand_assignment = r
                        trial_grid = [row[:] for row in g]
                        opt_grid, opt_slots, opt_assignment = minimize_black_squares(
                            trial_grid, (cand_slots, cand_assignment), rows, cols,
                            index, rng, cancel_event=cancel_event,
                            proper_noun_words=proper_noun_words,
                            max_proper_nouns=max_proper_nouns,
                            non_gloss_words=non_gloss_words,
                            max_non_gloss=max_non_gloss,
                            priority_words=priority_words,
                            permanent_locked_letters=permanent_locked_letters,
                            permanent_black_cells=permanent_black_cells,
                        )
                        opt_black = sum(row.count(BLACK) for row in opt_grid)
                        # Tie-break, at the user's explicit request: "au
                        # lieu d'évaluer la grille avec les mots les plus
                        # longs sur tous les mots, évaluer uniquement sur
                        # les mots du glossaire thématique" — for a
                        # themed generation (priority_words non-empty),
                        # the sum-of-squares score now only counts a
                        # placed word that's actually a member of the
                        # theme glossary, rather than every slot
                        # regardless of content — so the candidate that
                        # ends up surfacing more/longer theme words wins
                        # the tie-break, not merely the one with the
                        # longest words overall. An ordinary, non-themed
                        # generation (priority_words empty) is completely
                        # unaffected: opt_score still sums every slot's
                        # own length, exactly as before this change.
                        if priority_words:
                            opt_score = sum(
                                len(w) ** 2
                                for w, slot in zip(opt_assignment, opt_slots)
                                if w is not None
                                and w in _priority_words_for(priority_words, slot)
                            )
                        else:
                            opt_score = sum(len(slot) ** 2 for slot in opt_slots)
                        scored.append((opt_black, -opt_score, g, r, d))
                    _, _, best, best_result, best_diag = min(scored, key=lambda t: (t[0], t[1]))
                break
            # Toutes les tentatives réellement distinctes de ce palier,
            # triées par nombre de cases noires croissant — à la demande
            # explicite de
            # l'utilisateur, qui remplace ainsi le critère précédent (le plus
            # de lettres réellement posées, `assigned_letter_count`, toujours
            # calculé et disponible dans les diagnostics mais plus utilisé
            # pour ce tri) : la "meilleure" tentative échouée est désormais
            # celle dont le motif a le moins de cases noires, pas celle qui a
            # le plus avancé dans son remplissage — cohérent avec l'objectif
            # général du projet de minimiser les cases noires (voir
            # minimize_black_squares), y compris parmi les tentatives
            # échouées servant de base au palier suivant. `last_diag` (la
            # première diagnostics une fois triées, donc désormais celle du
            # motif le plus économe en cases injouables) reste transmis tel
            # quel en plus, pour le log détaillé (slot_count/length_counts/
            # checks/reason) déjà en place. Gardé apparié à son propre motif
            # (`failed_pairs`, pas seulement les diagnostics) puisque
            # `_build_retry_seed` ci-dessous a besoin du motif noir/blanc réel
            # de la meilleure tentative, pas seulement de ses diagnostics.
            # Critère de tri revu à la demande explicite de l'utilisateur :
            # "la meilleure grille est celle qui minimise le nombre de
            # caractères considérés comme injouables" — remplace l'ancien
            # critère (le moins de cases noires), qui ne disait rien de
            # combien de cases étaient réellement bloquées.
            #
            # Un instant remplacé par `_playable_score` (racine carrée de la
            # somme des carrés des longueurs jouables, sur l'état BRUT) une
            # fois la fusion des états publiés par `best_state_queue` mise
            # en place, le temps de corriger un biais réel : "le moins de
            # cases injouables" favorisait à tort un état publié tôt dans
            # une recherche encore très peu avancée sur un résultat
            # réellement abouti d'une autre tentative. Un instant remis à
            # `len(impossible_cells)` une fois les états publiés par la
            # file structurellement écartés de cette sélection (voir
            # `failed_unique`/`display_pairs` plus haut/plus bas).
            #
            # Remplacé une dernière fois par `_cleaned_playable_score`, à
            # la demande explicite de l'utilisateur : "Il faut montrer les
            # emplacements avant nettoyage, évaluer la grille après
            # nettoyage (qui sera transmise au cycle suivant si
            # sélectionnée)." Ni `len(impossible_cells)` ni `_playable_
            # score` n'évaluaient l'état qui compte réellement pour cette
            # sélection : celui qui sera transmis au palier suivant si
            # cette tentative gagne — c'est-à-dire l'état APRÈS `_clean_
            # blocked_slots`, pas l'état brut d'avant nettoyage. Deux
            # tentatives à égalité de cases injouables brutes peuvent
            # perdre des quantités de contenu très différentes une fois
            # nettoyées, selon la longueur du mot qui croise l'emplacement
            # impossible — voir `_cleaned_playable_score`'s propre
            # docstring pour le détail complet.
            failed_pairs = sorted(
                failed_unique,
                key=lambda gd: _cleaned_playable_score(gd[0], gd[1], rows, cols, index, rng),
                reverse=True,
            )
            last_diag = failed_pairs[0][1]
            # Pool séparé, réservé à l'affichage — jamais utilisé pour
            # `selected_grid`/`selected_diag`/`still_has_hope`/`_build_
            # retry_seed` plus bas, qui continuent de se fier exclusivement
            # à `failed_pairs` (construit ci-dessus à partir de `failed_
            # unique`, lui-même uniquement des résultats réels — voir son
            # propre commentaire). Part de `failed_unique` (une copie, pour
            # ne jamais muter la liste qui sert par ailleurs à la sélection
            # réelle) puis y fusionne les états publiés en temps réel par
            # `best_state_queue`, à la demande explicite de l'utilisateur :
            # "Chaque process suit son meilleur état, et transmet au
            # process parent l'information que ce meilleur état a changé.
            # Le process parent garde les 6 meilleurs états, de tous les
            # états dont il a été informé par les N process" — restreint
            # ensuite à l'affichage seul, après un vrai échec mesuré en
            # direct (voir le commentaire de `failed_unique` plus haut)
            # une fois confirmé que laisser ces états concourir pour la
            # sélection réelle dégradait la progression d'un palier à
            # l'autre. Voir Filler.on_new_best/_publish_new_best (try_fill)
            # pour la publication ; voir best_state_queue plus haut pour
            # pourquoi c'est une Queue (et pas juste un Event) et pourquoi
            # elle est créée une seule fois pour toute la génération.
            #
            # Ne lit plus jamais `best_state_queue` directement ici — un
            # thread dédié (`best_state_drain_thread`, démarré une seule
            # fois avant la création du pool, voir son propre commentaire)
            # la vide en continu dans `best_state_buffer`, précisément pour
            # éviter un vrai interblocage constaté en direct : un worker
            # encore profondément dans sa recherche peut publier des
            # dizaines de fois avant de rendre la main, et le tube sous-
            # jacent d'une `multiprocessing.Queue` a une capacité bornée —
            # ne le lire qu'une fois par palier, une fois tous les workers
            # revenus, laissait le temps à ce tube de se remplir et de
            # bloquer un `put()` avant même que quiconque ne le lise.
            #
            # `as_completed` a déjà épuisé tous les futures de ce palier
            # plus haut, donc chaque worker a déjà terminé sa recherche et
            # émis son dernier `put()` — mais le thread de drainage ne
            # l'aura pas forcément encore consommé au moment exact où ce
            # code s'exécute (il ne fait qu'interroger la file toutes les
            # BEST_STATE_QUEUE_DRAIN_GRACE_S secondes). Une courte pause,
            # de deux fois cet intervalle, laisse au thread au moins un
            # cycle complet pour rattraper un message tout juste publié
            # avant que ce code ne lise `best_state_buffer` — un compromis
            # borné (quelques dizaines de millisecondes par palier, jamais
            # plus), pas une garantie absolue, mais un message manqué ici
            # serait simplement traité au palier suivant plutôt que
            # celui-ci, sans jamais risquer de reproduire l'interblocage.
            time.sleep(2 * BEST_STATE_QUEUE_DRAIN_GRACE_S)
            with best_state_buffer_lock:
                published_this_palier = best_state_buffer[:]
                best_state_buffer.clear()
            display_seen_keys = set(seen_keys)
            display_unique = list(failed_unique)
            for published in published_this_palier:
                # `grid` retiré du dict après lecture (pop, pas juste get) :
                # une fois extrait dans `pub_grid` (le premier élément du
                # couple `(grid, diag)`, exactement la même forme que
                # failed_all/failed_unique ci-dessus), il n'a plus sa place
                # à l'intérieur du diagnostic lui-même — le laisser dedans
                # ferait fuiter une copie de la grille (redondante avec
                # `example_grid`) dans le JSON envoyé au Front.
                pub_grid = published.pop("grid")
                # Même traduction `attempt_id` -> `process_number` que celle
                # déjà appliquée à `outcomes` plus haut (voir
                # `seed_to_lineage`) — indispensable ici aussi : un état
                # publié par `best_state_queue`/`_publish_new_best` porte
                # bien `attempt_id` (voir son propre commentaire), mais
                # n'est jamais passé par la boucle de traduction ci-dessus,
                # qui ne parcourt que `outcomes` (les résultats bruts par
                # tâche), jamais `best_state_buffer`. Sans cette ligne, un
                # état publié en cours de route garderait `process_number`
                # absent malgré un `attempt_id` bien réel.
                published["process_number"] = seed_to_lineage.get(published.get("attempt_id"))
                pub_key = (tuple(map(tuple, pub_grid)), tuple(published["assignment"]))
                if pub_key not in display_seen_keys:
                    display_seen_keys.add(pub_key)
                    display_unique.append((pub_grid, published))
            # Réduit à une seule grille par tentative parallèle (process), à
            # la demande explicite de l'utilisateur : "Actuellement : on
            # garde toutes les meilleures grilles de tous les process (6
            # max). Modifier : on ne garde qu'une seule meilleure grille par
            # process." Jusqu'ici, `display_unique` pouvait contenir
            # plusieurs entrées distinctes issues de la MÊME tentative — son
            # résultat final (`failed_unique`) ET une ou plusieurs de ses
            # propres publications intermédiaires (`best_state_queue`,
            # chacune un instantané différent puisque `assigned_count`
            # augmente à chaque nouveau record) — puisque le dédoublonnage
            # ci-dessus ne compare que le contenu (motif + affectation),
            # jamais quelle tentative l'a produit ; une seule tentative très
            # productive pouvait ainsi à elle seule occuper plusieurs des
            # places affichées (alors limitées à 6, depuis retiré — voir plus
            # bas), au détriment des autres tentatives du même palier.
            # `attempt_id` (la graine
            # de cette tentative précise, voir `try_fill`'s propre
            # docstring) identifie maintenant de façon fiable, pour chaque
            # entrée — qu'elle vienne d'un résultat final ou d'une
            # publication intermédiaire —, de quelle tentative elle
            # provient ; regroupées par cet identifiant, seule celle au
            # score le plus élevé (`_playable_score`, l'état BRUT — même
            # critère que le tri de `display_rest` juste en dessous) survit
            # par groupe. `None` (un appelant hypothétique qui n'aurait
            # jamais fourni cet identifiant — aucun cas réel aujourd'hui)
            # reste traité comme une entrée à part entière à chaque fois,
            # jamais fusionné avec quoi que ce soit d'autre, pour ne
            # collapser aucune entrée distincte par erreur faute
            # d'identifiant.
            best_by_attempt = {}
            for idx, (g, d) in enumerate(display_unique):
                attempt_key = d.get("attempt_id")
                if attempt_key is None:
                    attempt_key = ("__no_attempt_id__", idx)
                score = _playable_score(d)
                if attempt_key not in best_by_attempt or score > best_by_attempt[attempt_key][0]:
                    best_by_attempt[attempt_key] = (score, (g, d))
            display_unique = [gd for _, gd in best_by_attempt.values()]
            # `failed_pairs[0]` (le vainqueur réel — celui qui va être
            # nettoyé via `_clean_blocked_slots` et transmis au palier
            # suivant, voir plus bas) est TOUJOURS placé en premier ici,
            # quel que soit son propre score — jamais laissé au tri normal.
            # Bug réel constaté en direct, avec des captures d'écran à
            # l'appui : `display_pairs` et `failed_pairs` utilisant deux
            # critères de tri différents (le premier par `_playable_score`,
            # le second par `len(impossible_cells)`), la toute première
            # grille montrée à l'écran pouvait être une tentative
            # complètement différente de celle réellement conservée —
            # jusqu'à un motif noir/blanc entièrement différent, pas
            # seulement un contenu différent. L'utilisateur comparait alors
            # à raison "cette grille" (la première montrée) à l'étape
            # suivante (le début du palier suivant, qui affiche le vrai
            # motif conservé) et y voyait des mots croisant une situation
            # impossible jamais retirés — alors qu'en réalité ce n'était
            # simplement pas la même grille : celle réellement conservée et
            # nettoyée n'était jamais celle affichée en premier. Garantir
            # que la première grille montrée est toujours la grille
            # réellement conservée rend la comparaison "avant nettoyage
            # (ici) / après nettoyage (au palier suivant)" valide.
            winner_grid, winner_diag = failed_pairs[0]
            winner_key = (tuple(map(tuple, winner_grid)), tuple(winner_diag["assignment"]))
            # Exclut aussi, en plus du contenu exact ci-dessus, toute autre
            # entrée partageant la MÊME tentative (`attempt_id`) que le
            # vainqueur — un vrai doublon trouvé en direct une fois le
            # plafond d'affichage retiré (voir plus bas) : `winner_grid`/
            # `winner_diag` viennent de `failed_pairs[0]` (trié par
            # `_cleaned_playable_score`, sur l'état APRÈS nettoyage), tandis
            # que la réduction "une grille par tentative" de `display_unique`
            # ci-dessus trie par `_playable_score` (l'état BRUT) — deux
            # critères différents qui peuvent légitimement retenir, pour la
            # MÊME tentative, deux représentants différents : le résultat
            # final réel (devenu le vainqueur) d'un côté, un instantané
            # intermédiaire publié plus tôt par cette même tentative de
            # l'autre. Sans cette exclusion supplémentaire, la même
            # tentative pouvait apparaître deux fois dans `display_pairs` —
            # une fois comme vainqueur, une fois via son propre instantané
            # antérieur — violant "une seule grille par tentative" alors
            # même que ce filtre par contenu seul ne les jugeait pas
            # identiques (deux états réellement différents, pris à deux
            # moments différents de la même recherche).
            winner_attempt_id = winner_diag.get("attempt_id")
            display_rest = sorted(
                (gd for gd in display_unique
                 if (tuple(map(tuple, gd[0])), tuple(gd[1]["assignment"])) != winner_key
                 and (winner_attempt_id is None or gd[1].get("attempt_id") != winner_attempt_id)),
                key=lambda gd: _playable_score(gd[1]), reverse=True,
            )
            display_pairs = [(winner_grid, winner_diag)] + display_rest
            # Chaque grille affichée montre l'état AVANT nettoyage (`d[
            # "example_grid"]`, tel quel) — brièvement remplacé par une
            # version déjà nettoyée (`_cleaned_example_preview`), reverti
            # à la demande explicite de l'utilisateur : "la visualisation
            # des extraits montre maintenant les grilles nettoyées avec
            # des emplacements impossibles vides. On ne comprend plus ce
            # qui se passe. Il faut montrer les emplacements avant
            # nettoyage, évaluer la grille après nettoyage." Voir la
            # sélection de `failed_pairs` plus haut (`_cleaned_playable_
            # score`) pour l'évaluation, désormais bien faite sur l'état
            # après nettoyage — seul l'AFFICHAGE reste sur l'état brut,
            # pour que les cases marquées `impossible_cells` restent
            # entourées d'un vrai contexte (les mots qui ont créé le
            # conflit) plutôt que de rester vides sans explication.
            # Toutes les grilles de `display_pairs`, sans troncature — à la
            # demande explicite de l'utilisateur : "Afficher toutes les
            # meilleures grilles dans l'aperçu, pas seulement les 6
            # meilleures." Un plafond fixe (`FAILED_ATTEMPT_EXAMPLES`, 6)
            # limitait auparavant cette liste ; `display_pairs` elle-même
            # est déjà réduite à une seule entrée par tentative parallèle
            # (voir plus haut), donc cette liste ne peut de toute façon
            # jamais dépasser `PARALLEL_ATTEMPTS` grilles.
            last_examples = _sort_examples_by_process([
                {
                    "example_grid": d["example_grid"],
                    "impossible_cells": d["impossible_cells"],
                    "forced_cells": d["forced_cells"],
                    "locked_cells": d.get("locked_cells", []),
                    "theme_cells": d.get("theme_cells", []),
                    "process_number": d.get("process_number"),
                    # `display_pairs[0]` est TOUJOURS le vainqueur réel
                    # (voir son propre commentaire plus haut) — marqué ici,
                    # avant le tri par process ci-dessous qui peut le
                    # déplacer n'importe où dans la liste affichée, à la
                    # demande explicite de l'utilisateur : "Entourer d'un
                    # filet vert la grille considérée comme la meilleure."
                    "is_best": idx == 0,
                }
                for idx, (g, d) in enumerate(display_pairs)
            ])
            # Un aperçu tardif "cases noires posées" (motif sans les
            # lettres) vivait ici, juste avant `pattern_attempt_failed` —
            # supprimé à la demande explicite de l'utilisateur, une fois
            # confirmé 100 % redondant avec lui : `pattern_attempt_failed`
            # (juste en dessous) montre déjà les mêmes motifs, avec en plus
            # les lettres réellement trouvées et les diagnostics complets.
            # Le seul aperçu "cases noires posées" qui reste est désormais
            # le précoce (voir plus haut, avant `executor.submit`), publié
            # avant même que la recherche ne démarre — l'aperçu tardif
            # n'ajoutait rien de plus, seulement une redite plus tôt dans
            # la séquence, ce qui donnait l'impression trompeuse d'un
            # nouveau tirage de cases noires ("il a refait une génération
            # de cases noires, qui a déjà été faite à l'étape précédente").
            progress("pattern_attempt_failed", attempt=attempt + 1, attempts=attempts,
                     ratio=round(ratio, 3),
                     total_attempts=total_attempts_tried, examples=last_examples,
                     **_public_diag(last_diag))
            # "Nouvelle version" du mécanisme de reprise entre paliers, à la
            # demande explicite de l'utilisateur, remplaçant l'essai
            # précédent ("continuer avant de nettoyer", tenté puis reverti —
            # voir plus bas pour l'historique conservé) : on regarde d'abord
            # si la grille échouée déjà sélectionnée (`failed_pairs[0]`,
            # celle avec le moins de cases injouables, déjà utilisée pour
            # `last_diag`/`last_examples` ci-dessus) a encore au moins un
            # emplacement non assigné qui n'est PAS impossible — un endroit
            # où un mot pourrait encore être ajouté sans rien nettoyer ni
            # regénérer. Si oui, le palier suivant reprend ce motif TEL
            # QUEL (`_pattern_continue`, aucun appel à `make_pattern`),
            # verrouillant toutes les cases déjà remplies et ignorant les
            # blocages sur les emplacements déjà connus comme impossibles —
            # exactement le point qui faisait échouer instantanément
            # (`checks=1`) l'essai précédent une fois composé dans la
            # boucle complète (voir plus bas), puisqu'ici aucune nouvelle
            # grille n'est générée par-dessus un contenu verrouillé
            # grandissant : le motif reste rigoureusement le même d'un
            # palier "continue" à l'autre, seul le contenu verrouillé/exclu
            # grandit. Si non (chaque emplacement non assigné restant est
            # impossible — un vrai blocage total pour ce motif), on retombe
            # sur le nettoyage existant (`_build_retry_seed`) et un motif
            # neuf au palier suivant, exactement comme avant cette
            # fonctionnalité.
            selected_grid, selected_diag = failed_pairs[0]
            # Dernier recours avant toute décision "reprise telle quelle" /
            # nettoyage, à la demande explicite de l'utilisateur : voir
            # `_plug_isolated_cells`'s propre docstring pour la définition
            # précise d'une case "isolée" et les conditions qui la
            # déclenchent. Un `None` (le cas normal, largement le plus
            # fréquent) laisse tout le reste de ce palier inchangé —
            # seule une grille où il ne reste plus RIEN que des cases
            # isolées à boucher, formant après coup une grille entièrement
            # remplie et valide, court-circuite la suite en la déclarant
            # directement réussie, exactement comme une réussite CSP
            # normale (`best`/`best_result`, utilisés tels quels par tout
            # le code qui suit la boucle des paliers).
            plugged = _plug_isolated_cells(
                selected_grid, rows, cols,
                extract_slots(selected_grid, rows, cols),
                selected_diag["assignment"], index,
                permanent_locked_letters=permanent_locked_letters,
            )
            if plugged is not None:
                new_grid, new_slots, new_assignment = plugged
                best, best_result = new_grid, (new_slots, new_assignment)
                break
            selected_impossible = set(selected_diag["impossible_slots"])
            # `_slots_touching`, à la demande explicite de l'utilisateur
            # ("ne pas essayer de remplir les emplacements qui croisent un
            # emplacement réputé impossible", voir Filler.__init__'s propre
            # `_crossing_excluded_slots`) : un emplacement qui croise un
            # emplacement impossible ne sera de toute façon jamais tenté au
            # palier "continue" suivant, donc il ne représente pas un
            # véritable espoir de progrès — un vrai bug trouvé en direct
            # sans ce correctif : `still_has_hope` restait indéfiniment
            # `True` (ces emplacements comptaient comme non-impossibles,
            # donc "encore prometteurs", alors qu'ils ne seraient jamais
            # essayés), empêchant à tort le nettoyage de jamais se
            # déclencher — confirmé par 3 générations réelles échouant
            # intégralement (200 paliers "continue" épuisés sans jamais
            # nettoyer) avant ce correctif.
            selected_slots = extract_slots(selected_grid, rows, cols)
            selected_dead = selected_impossible | _slots_touching(selected_slots, selected_impossible)
            still_has_hope = any(
                w is None and i not in selected_dead
                for i, w in enumerate(selected_diag["assignment"])
            )
            # Nettoyage forcé si les 10 tentatives de ce palier ont TOUTES
            # été abandonnées via la règle des 30 % (voir
            # UNFILLABLE_ABANDON_FRACTION, Filler.abandoned, reason ==
            # "abandoned_too_unfillable"), à la demande explicite de
            # l'utilisateur : quand chacune, indépendamment, a jugé son
            # propre motif trop largement condamné pour continuer à
            # chercher, c'est un signal fort qu'une reprise "telle quelle"
            # sur ce même motif serait vaine — on force donc un nettoyage
            # immédiatement, sur la meilleure de ces grilles
            # (`failed_pairs[0]`, déjà la base du nettoyage ci-dessous),
            # plutôt que de laisser `still_has_hope` en décider seul.
            #
            # Checked against `failed_real` (excluding attempts cut short by
            # attempt_done_event), not the raw `failed_all` — with
            # interruption now in play, `failed_all` will typically contain
            # up to `interrupt_threshold` real outcomes plus several
            # "interrupted_other_attempt_done" stragglers, which would never
            # satisfy this `all(...)` check and so would silently stop this
            # rule from ever firing again. In practice `failed_real` now
            # usually holds just the handful of attempts that completed
            # before `interrupt_threshold` was reached (up to
            # PALIER_ATTEMPT_INTERRUPT_FRACTION of PARALLEL_ATTEMPTS, e.g. 3
            # out of 10 on a 10-core machine) rather than all PARALLEL_ATTEMPTS — a
            # faster-firing version of the same original intent, no longer
            # needing every single attempt to each independently reach that
            # same conclusion before this palier is even allowed to finish.
            if failed_real and all(d["reason"] == "abandoned_too_unfillable" for _, d in failed_real):
                still_has_hope = False
            # Plafond de MAX_CONSECUTIVE_CONTINUE_PALIERS paliers "continue"
            # consécutifs (relevé de 5 à 10 puis à 50, puis ramené à 10 puis
            # à 5, puis nommé et ramené à 1, toujours à la demande explicite
            # de l'utilisateur) — même quand `still_has_hope` reste `True`,
            # on force un nettoyage dès que ce plafond est atteint, plutôt
            # que de laisser la reprise "telle quelle" s'enchaîner
            # indéfiniment sur un motif qui ne progresse peut-être plus
            # vraiment d'un palier à l'autre.
            if consecutive_continue_paliers >= MAX_CONSECUTIVE_CONTINUE_PALIERS:
                still_has_hope = False

            # Nouvelle étape, à la demande explicite de l'utilisateur,
            # insérée ici — après la décision "reprise telle quelle" /
            # nettoyage (`still_has_hope`, déjà figée ci-dessus) mais AVANT
            # le nettoyage lui-même, quel qu'il soit : "verrouiller tous
            # les emplacements entièrement vides et les éventuelles cases
            # noires avant/après ces emplacements vides, [puis] lancer un
            # cycle d'optimisation... qui ne doit pas toucher aux cases
            # blanches ou noires verrouillées." Appliquée à CHAQUE
            # tentative distincte de ce palier (`failed_pairs`), pas
            # seulement la meilleure — voir `_optimize_before_cleanup`'s
            # propre docstring pour le détail complet et pourquoi. Son
            # résultat REMPLACE `failed_pairs` pour tout le reste de ce
            # palier (`optimized_pairs`) : le nettoyage qui suit, quel que
            # soit le mode choisi, opère désormais sur la grille
            # optimisée, jamais sur l'état brut d'avant cette étape.
            #
            # Cette liste en compréhension peut prendre du temps sur une
            # grille dense en cases noires (voir PER_CYCLE_OPTIMIZATION_
            # SAMPLE_SIZE, qui borne ce coût sans l'annuler) — sans le
            # `progress(...)` juste en dessous, rien ne le signale à
            # l'écran pendant tout ce calcul : le statut affiché restait
            # celui du tout dernier événement déjà connu (typiquement
            # "pattern_attempt_failed", "nouvelle tentative en cours…"),
            # ce qui pouvait laisser croire à tort qu'une toute nouvelle
            # recherche de motif était en cours plutôt qu'une optimisation
            # de la meilleure grille déjà trouvée. Corrigé à la demande
            # explicite de l'utilisateur ("indiquer clairement qu'une
            # optimisation est en cours") par un événement dédié, fixé
            # juste avant que le calcul ne démarre — sans `examples` (rien
            # à montrer encore), donc sans effet sur `job["examples_
            # history"]`, seulement sur le texte de statut affiché en
            # direct pendant que `_optimize_before_cleanup` tourne.
            progress("pre_cleanup_optimizing", attempt=attempt + 1, attempts=attempts,
                     total_attempts=total_attempts_tried)
            optimized_pairs = [
                _optimize_before_cleanup(cand_grid, cand_diag, rows, cols, index, rng,
                                          cancel_event=cancel_event,
                                          permanent_locked_letters=permanent_locked_letters,
                                          permanent_black_cells=permanent_black_cells)
                for cand_grid, cand_diag in failed_pairs
            ]
            # Aperçu "avant" : déjà `last_examples`/`pattern_attempt_failed`
            # ci-dessus, sur l'état brut de `failed_pairs` — inchangé, rien
            # à ajouter ici. Aperçu "après" : un nouvel événement, sur
            # l'état de CHAQUE candidat une fois optimisé, avant tout
            # nettoyage — même format que `last_examples` (`example_grid`/
            # `impossible_cells`/`forced_cells`/`locked_cells`/
            # `process_number`) pour que le mécanisme d'affichage déjà en
            # place côté Front n'ait besoin d'aucun changement.
            optimized_examples = []
            for idx, ((g, d), (_, cand_diag)) in enumerate(zip(optimized_pairs, failed_pairs)):
                g_slots = extract_slots(g, rows, cols)
                optimized_examples.append({
                    "example_grid": d["example_grid"],
                    "impossible_cells": [cell for i in d["impossible_slots"] for cell in g_slots[i]],
                    # `forced_cells` : jamais recalculé par `_optimize_before_
                    # cleanup` (aucun sondage statistique n'a lieu pendant
                    # cette étape, voir sa propre docstring) — `d`/`cand_diag`
                    # portent donc rigoureusement la même valeur ici, `cand_
                    # diag` gardé par simplicité.
                    "forced_cells": cand_diag.get("forced_cells", []),
                    # `locked_cells` : DOIT venir de `d` (le résultat de
                    # `_optimize_before_cleanup`), jamais de `cand_diag` (l'état
                    # D'AVANT cette étape) — bug réel trouvé et corrigé, signalé
                    # directement par l'utilisateur : "La grille après
                    # optimisation de fin de cycle montre encore les cases
                    # verrouillées du cycle... elles ne sont pas entourées."
                    # `_optimize_before_cleanup` avait déjà été corrigée pour
                    # reconstruire son propre `locked_cells` à partir de zéro
                    # (voir sa docstring), mais CE site d'appel continuait de
                    # lire l'ancien `cand_diag["locked_cells"]` — la valeur
                    # corrigée n'atteignait donc jamais réellement l'aperçu
                    # affiché. Comme `cand_diag["locked_cells"]` ne contient
                    # jamais de case noire (ce concept n'existe que dans le
                    # résultat de cette étape), ce même bug explique aussi
                    # pourquoi aucune case noire verrouillée n'apparaissait
                    # jamais dans cet aperçu.
                    "locked_cells": d.get("locked_cells", []),
                    # `theme_cells` : jamais recalculé par `_optimize_before_
                    # cleanup` (pas de sondage/recomposition de mots pendant
                    # cette étape) — `cand_diag` (l'état d'avant, issu de
                    # `try_fill`) porte la valeur pertinente, même choix que
                    # `forced_cells` juste au-dessus.
                    "theme_cells": cand_diag.get("theme_cells", []),
                    "process_number": d.get("process_number"),
                    # `failed_pairs[0]` (index 0, avant tout tri par process
                    # ci-dessous) est le vainqueur réel de ce palier — voir
                    # `_sort_examples_by_process`'s propre docstring.
                    "is_best": idx == 0,
                })
            optimized_examples = _sort_examples_by_process(optimized_examples)
            progress("pre_cleanup_optimized", attempt=attempt + 1, attempts=attempts,
                     total_attempts=total_attempts_tried, examples=optimized_examples)

            if still_has_hope:
                consecutive_continue_paliers += 1
                just_cleaned = False
                # Nettoyage automatique des emplacements bloqués, à la
                # demande explicite de l'utilisateur : "à la fin d'un tour,
                # nettoyer automatiquement les emplacements bloqués, mais
                # pas les noires." Retire, avant même de reprendre "telle
                # quelle" au palier suivant, tout mot qui croise directement
                # un emplacement impossible (`_clean_blocked_slots`, les
                # étapes 1-2 de `_build_retry_seed` sans sa 3e étape) —
                # désormais appliqué à CHAQUE tentative distincte de ce
                # palier (`failed_pairs`), pas seulement la "meilleure"
                # (`selected_grid`/`selected_diag`) comme avant cette
                # fonctionnalité — à la demande explicite de l'utilisateur :
                # "Regression : après un cycle, le cycle suivant repart
                # maintenant avec une seule grille. Quand il n'y a pas de
                # déclenchement d'un nettoyage complet, chaque process doit
                # repartir à l'étape suivante avec sa grille partiellement
                # nettoyée (sauf le pourcentage de grilles entièrement
                # neuves)." Voir `_clean_continue_candidate` (niveau module,
                # juste après `_build_retry_seed`) pour le détail exact —
                # même logique, y compris l'alternative case noire à 1/10
                # (`BLACK_CELL_INSTEAD_OF_REMOVAL_PROBABILITY`, voir sa
                # propre docstring pour le raisonnement complet), appliquée
                # une fois par tentative au lieu d'une seule fois sur le
                # vainqueur.
                cleaned_continue_candidates = _sorted_by_score(
                    _clean_continue_candidate(
                        cand_grid, cand_diag, rows, cols, index, rng,
                        permanent_locked_letters=permanent_locked_letters,
                        permanent_black_cells=permanent_black_cells,
                    )
                    for cand_grid, cand_diag in optimized_pairs
                )
                carry_seed_pool_continue = _continue_seed_pool(cleaned_continue_candidates)
                carry_seed_grid, carry_preseed_assignment, carry_excluded_slots = (
                    carry_seed_pool_continue[0]
                )
                carry_locked_letters = None
                # Voir `carry_seed_pool_lineage`'s propre définition (avant
                # la boucle des paliers) pour le rôle de cette liste —
                # extraite via `_seed_pool` une seconde fois (même sélection,
                # même ordre que `carry_seed_pool_continue` ci-dessus,
                # puisque construite sur le même `cleaned_continue_
                # candidates` déjà trié) mais en tirant `sc[5]` (le numéro de
                # lignée hérité de chaque candidat, voir `_clean_continue_
                # candidate`) au lieu de `(sc[0], sc[3], sc[4])`. `None` pour
                # un candidat issu d'une tâche réinitialisée sans lignée à
                # hériter — `_reassign_lineage_numbers` lui en attribue une,
                # en priorité celle d'une lignée qui n'a pas survécu ce
                # palier (`dispatch_lineage`, celles actives à l'entrée de
                # CE palier), à la demande explicite de l'utilisateur : "La
                # grille entièrement nouvelle doit reprendre le numéro de la
                # grille qui disparaît (normalement, la moins bonne)."
                raw_continue_lineage = _seed_pool(
                    cleaned_continue_candidates, extract=lambda sc: sc[5]
                )
                carry_seed_pool_continue_lineage, next_lineage_number = _reassign_lineage_numbers(
                    raw_continue_lineage, dispatch_lineage, next_lineage_number
                )
            else:
                consecutive_continue_paliers = 0
                carry_preseed_assignment = None
                carry_excluded_slots = None
                # Nouvel algorithme de reprise entre paliers, à la demande
                # explicite de l'utilisateur (voir _build_retry_seed) : nettoyer
                # toutes les tentatives distinctes de CE palier (voir
                # `_clean_all_candidates` plus bas pour l'étendue exacte),
                # pas seulement la première — chacune perd un nombre différent
                # de lettres à l'étape 1 du nettoyage (retrait des mots croisant
                # un emplacement impossible) selon la forme précise de son propre
                # blocage, donc celle qui semblait "la meilleure" avant nettoyage
                # (le moins de cases noires) n'est pas forcément celle qui
                # conserve le plus d'information une fois nettoyée. `best_slots`
                # est recalculé ici (au lieu d'être renvoyé par le worker) — un
                # calcul déterministe et bon marché à partir du motif noir/blanc
                # seul, pas la peine d'élargir le contrat de retour de
                # `_pattern_attempt`/`try_fill` juste pour l'éviter.
                # "Continuer à ajouter des mots avant de nettoyer" (donner à
                # chaque candidate une seconde chance de remplissage, en
                # excluant l'emplacement déjà identifié comme impossible via
                # `Filler.excluded_slots`) a été essayé ici puis reverti, à la
                # demande explicite de l'utilisateur, après un test réel montrant
                # une régression sérieuse : vérifié correct en isolation (voir
                # `Filler.excluded_slots`, toujours en place et fonctionnel) mais,
                # composé dans la boucle complète, un palier auparavant sain
                # (15×10, seed 2, 62.7s, 0 incohérence juste avant ce changement)
                # se bloquait instantanément (`checks=1`) sur 199 des 200 paliers
                # — le contenu verrouillé grossissant progressivement à chaque
                # tour (`slot_count` 43→56) sans jamais redevenir réellement
                # remplissable. Cause exacte non identifiée avant de revenir à
                # la version d'avant ce mécanisme ; remplacé par la "Nouvelle
                # version" ci-dessus, qui reprend le motif tel quel (aucune
                # régénération) au lieu de composer une reprise-avec-exclusion
                # par-dessus un motif encore régénéré à chaque palier — voir la
                # SKILL project-best-practices pour l'historique complet.
                def _clean_all_candidates(force_exclude):
                    # TOUTES les tentatives distinctes de ce palier (jusqu'à
                    # PARALLEL_ATTEMPTS, pas seulement les FAILED_ATTEMPT_
                    # EXAMPLES (6) affichées à l'écran — ce plafond reste un
                    # plafond d'AFFICHAGE, voir `display_pairs`/`last_
                    # examples` plus haut, sans rapport avec la sélection
                    # réelle ici), à la demande explicite de l'utilisateur :
                    # "on garde la meilleure grille de tous les process, soit
                    # N grilles pour N process." `failed_pairs` porte déjà,
                    # par construction, au plus une entrée par tentative
                    # (voir son propre commentaire plus haut) — nul besoin
                    # d'un dédoublonnage par tentative supplémentaire ici.
                    result = []
                    for cand_grid, cand_diag in optimized_pairs:
                        cand_slots = extract_slots(cand_grid, rows, cols)
                        cand_seed, cand_confirmed = _build_retry_seed(
                            cand_grid, rows, cols, cand_slots,
                            cand_diag["assignment"], cand_diag["impossible_slots"],
                            locked_letters=carry_locked_letters,
                            exclude_impossible_locked=force_exclude,
                            seed_grid=carry_seed_grid, index=index, rng=rng,
                            permanent_locked_letters=permanent_locked_letters,
                            permanent_black_cells=permanent_black_cells,
                        )
                        result.append((cand_seed, cand_confirmed, cand_slots,
                                        cand_diag.get("process_number")))
                    return result

                # Parmi les grilles nettoyées, celle qui l'emporte maximise la
                # somme des carrés des longueurs des mots en place *après*
                # nettoyage (un mot est "en place" si toutes ses cases
                # figurent dans `confirmed`) — à la demande explicite de
                # l'utilisateur, remplace l'ancien critère (le plus de
                # lettres restantes, départagé par le moins de cases noires).
                # Même formule de score que celle qui départage les
                # tentatives parallèles réussies plus haut dans cette
                # fonction (favorise quelques mots longs plutôt que beaucoup
                # de mots courts pour le même total de lettres) — appliquée
                # ici au résultat *après* nettoyage (le vrai signal utile
                # pour repartir), pas à un critère pré-nettoyage comme
                # précédemment.
                #
                # `_words_in_place_score`/`_candidate_black_count`/
                # `_sorted_by_score`/`_seed_pool` (le score, son départage par
                # le nombre de cases noires, le tri qui les combine, et la
                # réduction au vivier transmis au palier suivant) sont
                # désormais des fonctions de niveau module, juste après
                # `_build_retry_seed` — hissées hors de cette fermeture locale
                # à la demande explicite de l'utilisateur, une fois la même
                # logique nécessaire aussi pour la reprise "telle quelle" (voir
                # `_clean_continue_candidate`/`_continue_seed_pool`, et plus
                # bas, `if still_has_hope:`) ; voir leurs propres docstrings
                # pour le raisonnement complet (notamment le départage par
                # cases noires, ajouté après un vrai blocage constaté en
                # direct sur une grande grille 30×30 très majoritairement
                # verrouillée).

                previous_locked_letters = carry_locked_letters
                cleaned_candidates = _sorted_by_score(_clean_all_candidates(force_exclude=False))
                carry_seed_pool = _seed_pool(cleaned_candidates)
                carry_seed_grid, carry_locked_letters = carry_seed_pool[0]
                # Voir `carry_seed_pool_lineage`'s propre définition (avant
                # la boucle des paliers) pour le rôle de cette liste — même
                # mécanisme que pour la reprise "telle quelle" ci-dessus
                # (voir `raw_continue_lineage`), mais sur `cleaned_
                # candidates` (position 3 = numéro de lignée hérité, voir
                # `_clean_all_candidates`). Reconstruit une seconde fois plus
                # bas si le point fixe ci-dessous force un second nettoyage
                # plus agressif, pour toujours refléter le `cleaned_
                # candidates` réellement utilisé en dernier.
                raw_lineage = _seed_pool(cleaned_candidates, extract=lambda sc: sc[3])
                carry_seed_pool_lineage, next_lineage_number = _reassign_lineage_numbers(
                    raw_lineage, dispatch_lineage, next_lineage_number
                )
                # Point fixe détecté : ce palier n'a produit aucun changement du
                # tout (les lettres confirmées sont rigoureusement identiques à
                # celles du palier précédent) — un vrai blocage qui, sans
                # intervention, se reproduirait à l'identique indéfiniment (voir
                # `_build_retry_seed`'s docstring pour l'historique complet de ce
                # cas). À la demande explicite de l'utilisateur, ce n'est
                # traité qu'en dernier recours, seulement une fois ce blocage
                # réellement constaté : le même nettoyage est relancé sur tous
                # les mêmes candidats avec `exclude_impossible_locked=True`, qui
                # retire spécifiquement tout emplacement verrouillé dont la
                # combinaison ne correspond à aucun mot réel — cassant le point
                # fixe sans jamais appliquer cette règle plus agressive aux
                # paliers qui progressent normalement.
                #
                # Une version plus fine (comparant, pour chaque tentative
                # parallèle brute plutôt que seulement la gagnante, si une
                # affectation réelle a eu lieu) a été essayée puis abandonnée
                # à la demande explicite de l'utilisateur ("il n'essaye pas
                # vraiment de remplir les grilles partielles... revenir à la
                # situation précédente") — retour à cette comparaison plus
                # simple sur la seule gagnante (la nouvelle diversité du
                # vivier ci-dessus, elle, porte sur TOUTES les grilles
                # nettoyées, pas seulement la gagnante — deux préoccupations
                # distinctes, l'une sur la détection du point fixe, l'autre
                # sur la diversité du prochain lancement).
                if previous_locked_letters is not None and carry_locked_letters == previous_locked_letters:
                    cleaned_candidates = _sorted_by_score(_clean_all_candidates(force_exclude=True))
                    carry_seed_pool = _seed_pool(cleaned_candidates)
                    carry_seed_grid, carry_locked_letters = carry_seed_pool[0]
                    raw_lineage = _seed_pool(cleaned_candidates, extract=lambda sc: sc[3])
                    carry_seed_pool_lineage, next_lineage_number = _reassign_lineage_numbers(
                        raw_lineage, dispatch_lineage, next_lineage_number
                    )
                # No black cell is added here any more either (the
                # single-cell lock that used to run at this exact point,
                # shared with the "reprise telle quelle" branch above, was
                # removed entirely at the user's explicit request — see
                # CLAUDE.md for its full history) — a full cleanup now only
                # ever changes which words/black cells survive from the
                # already-generated pattern, never adds a new one.
                just_cleaned = True
                # Mémorisation de l'état obtenu à la fin de CE nettoyage
                # (motif noir/blanc ET contenu confirmé) et détection d'un
                # état qui se répète à l'identique d'un nettoyage au
                # suivant — voir GRID_REPEAT_INFEASIBLE_THRESHOLD's own
                # docstring pour la demande complète et son historique.
                # Réservé à cette seule branche (`if still_has_hope:` ci-
                # dessus, "reprise telle quelle", n'y touche jamais) — à la
                # demande explicite de l'utilisateur, après DEUX régressions
                # mesurées en direct sur le benchmark standard 15×10
                # (Flash) : une première version comparait uniquement le
                # motif noir/blanc, sur les deux branches — le motif reste
                # très souvent identique plusieurs cycles "reprise telle
                # quelle" de suite par construction (le nettoyage n'ajoute
                # une case noire qu'une fois sur dix, voir BLACK_CELL_
                # INSTEAD_OF_REMOVAL_PROBABILITY) alors même que le contenu
                # progresse normalement, confondant ça avec un vrai blocage.
                # Une deuxième version comparait motif+contenu, toujours sur
                # les deux branches — un diagnostic détaillé (branche +
                # compteur consecutive_continue_paliers à chaque
                # déclenchement) a montré que la majorité des déclenchements
                # coïncidaient, sur la branche "reprise telle quelle", très
                # exactement avec le moment où MAX_CONSECUTIVE_CONTINUE_
                # PALIERS force déjà, tout seul, un passage en nettoyage —
                # ce mécanisme faisait alors doublon avec un garde-fou déjà
                # réglé, mais avec une réponse bien plus destructrice
                # (grille entièrement vierge au lieu d'un nettoyage
                # classique qui conserve le contenu valide). Restreindre la
                # détection à cette seule branche règle les deux problèmes
                # à la fois : un cycle "reprise telle quelle" ne compte
                # jamais dans la série (déjà borné ailleurs), et seul un
                # vrai point fixe du nettoyage LUI-MÊME (celui que la
                # relance unique avec `exclude_impossible_locked=True`,
                # juste au-dessus, ne résout pas toujours) déclenche la
                # réinitialisation. Réutilise `_cycle_start_preview` (déjà
                # appelée ailleurs dans cette même boucle pour l'aperçu
                # "début de cycle") pour fusionner motif + contenu en une
                # seule grille comparable — `carry_preseed_assignment` vaut
                # toujours `None` sur cette branche, donc `_cycle_start_
                # preview` construit systématiquement à partir de `carry_
                # locked_letters` ici. Comparé comme un tuple de tuples
                # (hashable, comparaison de contenu, pas d'identité) plutôt
                # que la liste elle-même — `locked_cells` (2e valeur de
                # retour) n'est pas utile ici, seule la grille fusionnée
                # sert de clé.
                current_state_grid, _ = _cycle_start_preview(
                    rows, cols, carry_seed_grid, carry_locked_letters, carry_preseed_assignment,
                )
                current_pattern_key = tuple(tuple(row) for row in current_state_grid)
                if current_pattern_key == last_cycle_end_grid:
                    same_grid_streak += 1
                else:
                    last_cycle_end_grid = current_pattern_key
                    same_grid_streak = 1
                if same_grid_streak > GRID_REPEAT_INFEASIBLE_THRESHOLD:
                    # Motif jugé infaisable : réinitialisation complète, le
                    # prochain cycle repart d'une grille entièrement vierge
                    # — exactement l'état initial de cette fonction (voir
                    # `carry_seed_grid = None` tout en haut), y compris les
                    # deux viviers et le compteur de série "reprise telle
                    # quelle", pour qu'un palier "motif neuf" reparte bien
                    # de zéro plutôt que de réutiliser un vivier construit
                    # à partir du motif désormais abandonné.
                    carry_seed_grid = None
                    carry_locked_letters = None
                    carry_preseed_assignment = None
                    carry_excluded_slots = None
                    carry_seed_pool = None
                    carry_seed_pool_continue = None
                    carry_seed_pool_lineage = None
                    carry_seed_pool_continue_lineage = None
                    # `next_lineage_number`, lui, n'est délibérément PAS remis
                    # à zéro ici : une grille née d'un futur palier ne doit
                    # jamais réutiliser le numéro d'une lignée abandonnée par
                    # cette réinitialisation, au risque de faire croire à
                    # l'utilisateur qu'il s'agit de la même grille qu'avant.
                    consecutive_continue_paliers = 0
                    last_cycle_end_grid = None
                    same_grid_streak = 0
            # Le ratio cible ne progresse plus d'un palier à l'autre (reste
            # fixé à `black_ratio`, 0.0 par défaut), à la demande explicite
            # de l'utilisateur : le pré-remplissage (au moins
            # PREFILL_MIN_WORD_COUNT candidats par emplacement) combiné à la
            # reprise sur la grille nettoyée du palier précédent
            # (_build_retry_seed juste au-dessus) suffit à faire progresser
            # la recherche, sans avoir besoin de densifier artificiellement
            # la grille palier après palier.

    # Arrêt propre du thread de drainage de `best_state_queue` (voir sa
    # propre docstring plus haut) — la recherche elle-même est terminée
    # (succès ou épuisement de `attempts`), rien de plus ne sera jamais
    # publié dessus. `daemon=True` garantirait de toute façon qu'il ne
    # bloque jamais la fin du processus si ce point n'était pas atteint
    # (par exemple `GenerationCancelled`, levée depuis l'intérieur de la
    # boucle ci-dessus, sans jamais repasser par ici) — cet arrêt explicite
    # est purement une question d'hygiène dans le cas normal, pas une
    # protection dont la correction du programme dépendrait.
    stop_best_state_drain.set()
    best_state_drain_thread.join(timeout=1.0)

    if best is None:
        # "Continuer" button on the web UI (see _serialize_resume_state's
        # own docstring above), at the user's explicit request — `None`
        # only in the degenerate case where the palier loop never ran at
        # all (`attempts=0`, never used by any real caller), since
        # `carry_seed_grid` is otherwise always set by the very first
        # failed palier onward.
        resume_state = (
            _serialize_resume_state(
                carry_seed_grid, carry_locked_letters,
                carry_preseed_assignment, carry_excluded_slots,
            )
            if carry_seed_grid is not None else None
        )
        progress("pattern_failed", attempts=attempts,
                  last_attempt=_public_diag(last_diag) if last_diag is not None else None,
                  examples=last_examples, total_attempts=total_attempts_tried,
                  resume_state=resume_state)
        return None
    progress("pattern_found", attempt=attempt + 1, total_attempts=total_attempts_tried)

    # Aperçu de la grille juste avant l'optimisation, réutilisant le même
    # mécanisme que l'aperçu d'une tentative échouée (try_fill's
    # diagnostics["example_grid"]). Contient désormais les vraies lettres
    # (`build_letters_grid`, la même fonction déjà utilisée pour
    # `result["solution"]` plus bas), pas seulement le motif noir/blanc nu
    # — reverti à la demande explicite de l'utilisateur par rapport à la
    # toute première version de cet aperçu (qui l'omettait délibérément) :
    # côté client, `renderAttemptPreview()` masque déjà ces lettres par
    # défaut et ne les révèle que si l'utilisateur active
    # #attempt-preview-reveal-btn (voir style-guide SKILL), donc les
    # transmettre ici ne les affiche pas pour autant — c'est le même
    # mécanisme de masquage qu'une tentative échouée, pas un nouveau.
    # `best_result` est `(slots, assignment)` (voir _pattern_attempt/
    # try_fill's contrat de retour) — passé tel quel à build_letters_grid,
    # qui construit une toute nouvelle grille (jamais une modification de
    # `best` en place), donc aucune copie défensive n'est nécessaire ici
    # contrairement à l'ancienne version qui transmettait `best` lui-même.
    # `impossible_cells`/`forced_cells`/`locked_cells` sont explicitement
    # vidées (et non simplement omises) pour effacer un éventuel aperçu
    # resté affiché d'une tentative précédemment échouée pendant la
    # recherche du motif — un motif entièrement réussi n'a ni case
    # impossible, ni lettre forcée, ni case verrouillée à signaler.
    # Transmis via `examples` (une liste d'un seul élément) — même format
    # que `pattern_attempt_failed`/`pattern_failed` ci-dessus (jusqu'à 6
    # éléments) — pour que backend/app.py et le frontend n'aient qu'un
    # seul mécanisme d'aperçu à gérer, que ce soit 1 grille ou 6.
    best_slots, best_assignment = best_result
    # Numéro de lignée de la tâche qui a réellement produit `best` (voir
    # `best_diag`, `seed_to_lineage`) — `None` pour le seul chemin de réussite sans
    # vrai worker derrière (`_plug_isolated_cells`). Transmis à la fois
    # dans l'aperçu "minimizing" ci-dessous et dans le résultat final, pour
    # que backend/app.py puisse aussi l'attacher à l'aperçu du "clues".
    winning_process_number = best_diag.get("process_number") if best_diag else None
    progress(
        "minimizing",
        examples=[{
            "example_grid": build_letters_grid(rows, cols, best_slots, best_assignment),
            "impossible_cells": [],
            "forced_cells": [],
            "locked_cells": [],
            "theme_cells": _theme_word_cells(best_slots, best_assignment, priority_words),
            "process_number": winning_process_number,
            "is_best": True,
        }],
    )
    grid, slots, assignment = minimize_black_squares(
        best, best_result, rows, cols, index, rng, cancel_event=cancel_event,
        proper_noun_words=proper_noun_words, max_proper_nouns=max_proper_nouns,
        non_gloss_words=non_gloss_words, max_non_gloss=max_non_gloss,
        priority_words=priority_words, permanent_locked_letters=permanent_locked_letters,
        permanent_black_cells=permanent_black_cells,
    )
    n_black = sum(row.count(BLACK) for row in grid)
    words = build_word_entries(grid, rows, cols, slots, assignment)
    # Sur une grille bilingue, chaque mot vertical ("down") reçoit sa
    # propre orthographe accentuée/racine(s) canonique(s) DANS LA SECONDE
    # LANGUE plutôt que dans la première, et porte désormais son propre
    # `language` — le code de la langue réellement utilisée pour CE mot
    # précis (voir la docstring de `bilingual_wordlist_path`) — consommé
    # par backend/clues.py (une définition par mot dans sa propre langue)
    # et backend/chatbot.py (un indice dans la bonne langue selon le mot).
    # Sur une grille monolingue (`bilingual_active` faux), chaque mot
    # reçoit `language` tout de même — toujours la même valeur — sans
    # aucun changement au reste du comportement.
    for w in words:
        if bilingual_active and w["direction"] == "down":
            w["accented"] = accents_down.get(w["answer"], w["answer"])
            w["canonical"] = canonicals_down.get(w["answer"], [w["accented"]])
            w["language"] = bilingual_language
        else:
            w["accented"] = accents.get(w["answer"], w["answer"])
            w["canonical"] = canonicals.get(w["answer"], [w["accented"]])
            w["language"] = language
    progress("grid_ready", word_count=len(slots), black_count=n_black)
    return {
        "width": cols,
        "height": rows,
        "pattern": grid,
        "solution": build_letters_grid(rows, cols, slots, assignment),
        "words": words,
        "word_count": len(slots),
        "black_count": n_black,
        "black_ratio": n_black / (rows * cols),
        "winning_process_number": winning_process_number,
        # Langue primaire (mots horizontaux) et langue de la grille
        # bilingue (mots verticaux, `None` pour une grille monolingue
        # ordinaire) — à la demande explicite de l'utilisateur, pour que
        # backend/app.py/backend/grid_store.py puissent enregistrer les
        # deux sans avoir à les redériver de `wordlist_path` eux-mêmes.
        "language": language,
        "bilingual_language": bilingual_language,
    }


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--width", type=int, default=DEFAULT_WIDTH,
                     help=f"largeur de la grille, nombre de colonnes (défaut : {DEFAULT_WIDTH})")
    ap.add_argument("--height", type=int, default=DEFAULT_HEIGHT,
                     help=f"hauteur de la grille, nombre de lignes (défaut : {DEFAULT_HEIGHT})")
    ap.add_argument("--wordlist", default="data/wordlist_fr_full.tsv",
                     help="lexique MOT<TAB>ACCENTUE<TAB>FREQUENCE généré par "
                          "build_wordlist_freq.py (ou fichier texte libre en repli)")
    _difficulty_help = (
        "limite le vocabulaire à une fraction des mots les plus fréquents au "
        "global (toutes longueurs confondues), calculée sur la taille réelle "
        "du lexique de la langue chargée : easy={:.0%} (défaut), medium={:.0%}, "
        "hard=100% (tout le lexique)"
    ).format(DIFFICULTY_PRESETS["easy"], DIFFICULTY_PRESETS["medium"])
    ap.add_argument(
        "--difficulty", choices=sorted(DIFFICULTY_PRESETS), default="easy",
        # argparse fait lui-même une passe de substitution % sur les help
        # strings (pour %(default)s etc.) — un "%" litéral issu de {:.0%}
        # ci-dessus doit être échappé en "%%" *après* le formatage (jamais
        # dans le format-spec de .format() lui-même, qui n'accepte que "%"),
        # sinon argparse lève une ValueError.
        help=_difficulty_help.replace("%", "%%"),
    )
    ap.add_argument("--max-words", type=int, default=None,
                     help="surcharge manuelle du nombre max de mots au global "
                          "(prioritaire sur --difficulty)")
    ap.add_argument("--black-ratio", type=float, default=0.0,
                     help="densité de cases noires visée au départ (0-1), en plus de "
                          "celles déjà posées par la phase de pré-remplissage")
    ap.add_argument("--attempts", type=int, default=200,
                     help="nombre de motifs essayés avant d'abandonner")
    ap.add_argument("--seed", type=int, default=None)
    args = ap.parse_args()

    result = generate_grid(
        width=args.width,
        height=args.height,
        difficulty=args.difficulty,
        max_words=args.max_words,
        black_ratio=args.black_ratio,
        attempts=args.attempts,
        seed=args.seed,
        wordlist_path=args.wordlist,
    )

    if result is None:
        print(f"Échec : aucune grille remplissable trouvée en {args.attempts} essais.",
              file=sys.stderr)
        print("Essayez une grille plus petite, --black-ratio plus élevé, ou un dictionnaire plus riche.",
              file=sys.stderr)
        sys.exit(1)

    print(f"Grille {result['width']}x{result['height']} — {result['black_count']} cases noires "
          f"({100 * result['black_ratio']:.1f}%)\n")
    print("Motif :")
    print_grid(result["pattern"])
    print("\nSolution :")
    print_grid(result["solution"])
    print(f"\n{result['word_count']} mots placés.")


if __name__ == "__main__":
    main()
