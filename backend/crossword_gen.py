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
import unicodedata
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

        # This exact slot can be fixed neither by an available black cell
        # nor by removing a locked word crossing it (typically: all of
        # its cells are already locked by letters from a previous palier
        # with no crossing word itself locked, or no cell preserves
        # connectivity) — at the user's explicit request, this isn't a
        # reason to abandon the whole pre-fill: it's marked so it's never
        # offered again (`unfixable`) and the loop continues on the other
        # slots, which remain independently fixable. This leftover, if it
        # survives all the way to the CSP fill, will simply fail there
        # normally — and, in the case of a locked word, will be removed at
        # the next palier by the same cleanup mechanism that already
        # removes any word crossing an impossible slot (see
        # _build_retry_seed).
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
    # A defensive copy of `locked_letters`, on the same footing as the one
    # already made for `seed_grid` right below — a real bug observed
    # live, screenshot in hand: locked letters genuinely present in the
    # "pattern" preview (cycle start) were disappearing from that same
    # cycle's own "pattern_generated" preview, and not just on screen.
    # Cause: this preview speculatively reconstructs, in the PARENT
    # process, the pattern the last non-reset worker will itself
    # recompute — by calling `make_pattern` directly on `carry_locked_
    # letters`, the SHARED object genuinely passed right afterward to the
    # real dispatched workers (`_pattern_attempt`). But `_prefill_
    # unfillable_slots`/`_remove_a_crossing_word` ("curative cleanup")
    # mutate their own `locked_letters` parameter in place (removing
    # cells via `.pop`) — harmless behavior for a real worker, which only
    # ever receives an independent copy once its arguments are passed to
    # its own separate process, but which here damaged the parent
    # process's own shared state: a plain preview reconstruction, meant
    # to be throwaway, was genuinely removing confirmed letters from
    # `carry_locked_letters` even before this palier's real workers were
    # ever submitted — so those workers also received an already-
    # amputated version. This copy protects every caller, not just this
    # one, exactly as the `seed_grid` copy already protects every caller
    # against a similar mutation of the grid itself.
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
        # Excludes `locked_letters` from the candidate pool even without
        # `seed_grid` — no effect for any pre-existing caller before
        # "Finir la grille" (none ever passed `locked_letters` without
        # `seed_grid` at all), but needed for a "reset" worker
        # (`FULL_RESET_ATTEMPT_COUNT`, an entirely fresh pattern) on a
        # generation where some letters stay permanently locked
        # (`permanent_locked_letters`, see generate_grid): these must
        # never receive a black cell, even on a pattern restarted from
        # scratch.
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

    # At the user's explicit request: the fixed rate ("Taux noir",
    # `black_enrichment_fraction`) is no longer applied as-is during the
    # pre-fill phases — it's multiplied by the proportion of remaining
    # white cells (remaining white cells / the grid's total cells),
    # measured on THIS exact palier (via `seed_grid` if there is one)
    # before its own pre-fill starts. For the very first grid (no `seed_
    # grid`, entirely white), `initial_white_count == rows * cols` so this
    # proportion equals 1 — "le taux reste donc 1 pour la toute première
    # grille" — and it mechanically decreases, palier after palier, as
    # the grid gets blacker, with no calling code needing to compute it
    # itself: `initial_white_count` already reflected this reality, it
    # simply wasn't yet used to modulate the rate itself.
    white_proportion = initial_white_count / (rows * cols)
    black_enrichment_fraction = black_enrichment_fraction * white_proportion

    # "Curative cleanup" (see _prefill_unfillable_slots): reuses this same
    # whole-grid black-fill objective as the threshold beyond which an
    # impossible zone switches from a plain black-cell addition to
    # removing an already-locked word crossing it — at the user's
    # explicit request, rather than inventing a new, separate threshold
    # for this rule. `black_ratio` is almost always 0.0 today (see
    # above), so `black_enrichment_fraction` dominates in practice; both
    # are accounted for here purely for robustness, for a caller (the
    # CLI) that might still set `--black-ratio`.
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


def challenge_word_grid_form(word):
    """Bare, accent-stripped, uppercase grid form of one "Mots Défi" entry
    — the wordlist's own MOT-column convention (see `data_builder/build_
    wordlist_freq.py`'s `strip_accents`), the only form a grid cell (or
    `Filler.challenge_words`) ever holds. The author's own typed spelling
    is kept verbatim everywhere else (the web UI's own list,
    `InteractiveStepRequest.challenge_words`, the `GRID_WORK` record) —
    this is derived from it on demand, at the point `backend/app.py`'s
    `interactive_step` builds the frozenset `interactive_place_word`
    actually searches with, never stored itself. NFD-normalizing before
    dropping combining marks keeps each base letter (`unicodedata.
    combining`) rather than deleting the accented character outright, at
    the user's explicit request: an earlier, JS-side-only version of this
    same conversion used a bare `[^A-Z]` filter on the *typed* value
    itself, which discarded an accented letter wholesale instead of
    folding it to its base form — "randonnées" was silently stored (and
    therefore ever matched) as "RANDONNES", not "RANDONNEES"."""
    stripped = "".join(
        c for c in unicodedata.normalize("NFKD", word)
        if not unicodedata.combining(c)
    )
    return re.sub(r"[^A-Z]", "", stripped.upper())


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

# Fraction of the search's own `deadline_checks` budget one candidate
# tier may consume in failed placement attempts (each one a genuine
# crossing-slot break, see `Filler._backtrack`'s `crossing_broken` check)
# before that tier gives up and falls through to the next one. Shared by
# all three candidate tiers `_backtrack` tries in order for a slot —
# "Mots Défi" (challenge words), the theme glossary (`priority_words`),
# then the general dictionary — each with its own budget, counted
# separately:
#   - A challenge word or a theme word that keeps breaking crossings is
#     abandoned for the rest of THIS attempt once its own share of the
#     budget is spent: placing it is never attempted again (it stops
#     being injected into a slot's candidate list — and, for a challenge
#     word, stops exempting a crossing slot's dry domain too), letting
#     the search spend the rest of its own budget on the remainder of
#     the grid instead of chasing one particularly hard-to-place word.
#     A challenge word is trusted at face value (`Filler._challenge_
#     word_fits`, never checked against the dictionary), so nothing else
#     ever bounds how many crossing slots it might keep breaking on a
#     large, heavily-constrained grid; a theme word, though a real
#     dictionary entry, can just as easily be geometrically unplaceable
#     wherever it still fits — without this cap, either kind could
#     otherwise eat into the whole attempt's budget one failed crossing
#     at a time.
#   - The general dictionary has no further tier to fall back to: once a
#     given SLOT's own share of the budget is spent on candidates that
#     all broke some crossing, `_backtrack` stops insisting and accepts
#     the next candidate even though it breaks one — deliberately
#     creating a known "impossible" zone rather than paying for
#     exhaustive backtracking first. The cross-palier retry machinery
#     (`_clean_blocked_slots`/`_build_retry_seed`) already repairs this
#     kind of zone on the next palier regardless of how it arose.
# Mirrors `UNFILLABLE_ABANDON_FRACTION`'s own shape (a fraction of a
# whole-search resource, here `deadline_checks` rather than white-cell
# count) — `interactive_place_word` applies the same principle to its own
# challenge-word combo search, with a differently-shaped budget (a
# combination count rather than a check count, see its own docstring);
# its theme/general-dictionary fallback instead scans every available
# candidate at its one target slot exhaustively (see its own docstring
# for why no separate budget is needed there).
FALLBACK_PHASE_BUDGET_FRACTION = 0.10


class Filler:
    def __init__(self, slots, index, rng, forced_letters=None, letter_scores=None,
                 excluded_slots=None, cancel_event=None, batch_abandoned_event=None,
                 attempt_done_event=None, on_new_best=None, locked_letters=None,
                 priority_words=None, challenge_words=None):
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
        # "Mots Défi" (web UI "Interactif" mode only, see backend/app.py's
        # `POST /api/interactive/step`), at the user's explicit request: a
        # free-form list of words the author wants to force into the grid,
        # given priority over the theme glossary above (see
        # `_select_target_slot` level 2 and `interactive_place_word`'s own
        # word draw). Always a plain frozenset — unlike `priority_words`,
        # never a DualSet: a challenge word is typed by the author with no
        # language attached, so it is checked against a slot purely
        # geometrically (`_challenge_word_fits`: matching length + already-
        # known letters), never against `_domain(i)`/the loaded lexicon —
        # at the user's own explicit follow-up request, "modifier le code
        # pour qu'il accepte de placer un Mot Défi même s'il ne fait pas
        # partie du dictionnaire (vérité utilisateur)": a real surname (or
        # any other non-dictionary word) typed here must still be place-
        # able by "Suivant", not silently ignored just because the lexicon
        # doesn't happen to contain it. `generate_grid`'s own automatic CSP
        # fill (`_backtrack`/`try_fill`) never supplies this parameter, so
        # it always stays empty there — every check below is then a
        # guaranteed no-op, exactly like an empty `priority_words`.
        self.challenge_words = challenge_words or frozenset()
        # Per-attempt bookkeeping for the crossing-safety retry mechanism
        # described at FALLBACK_PHASE_BUDGET_FRACTION: how many
        # times each "Mots Défi" word has already broken a crossing slot
        # in THIS attempt (_challenge_attempt_counts), and which ones have
        # consequently been given up on for the rest of it
        # (_challenge_abandoned, checked by _active_challenge_words()).
        # _challenge_word_budget (the per-word cap itself, a fraction of
        # deadline_checks) is only known once solve() receives that
        # argument, so it stays None here and is resolved lazily there.
        self._challenge_attempt_counts = Counter()
        self._challenge_abandoned = set()
        self._challenge_word_budget = None
        # Same shape as the three fields right above, applied to the
        # theme glossary (self.priority_words) instead of challenge
        # words — see FALLBACK_PHASE_BUDGET_FRACTION and
        # _active_priority_words_for.
        self._theme_attempt_counts = Counter()
        self._theme_abandoned = set()
        self._theme_word_budget = None
        # Same principle once more, but for the general dictionary tier,
        # which has no word identity of its own to track (any candidate
        # from the loaded lexicon is eligible) — keyed by SLOT index
        # instead of by word: once a given slot's own share of the budget
        # has been spent on candidates that all broke some crossing,
        # `_backtrack` stops insisting on a crossing-safe candidate for
        # that slot and accepts the next one anyway (see
        # FALLBACK_PHASE_BUDGET_FRACTION).
        self._domain_break_counts = Counter()
        self._domain_break_abandoned = set()
        self._domain_word_budget = None
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
        # request, taking priority over `_backtrack`'s own 8 levels: never
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

    def _challenge_word_fits(self, i, word):
        """True if `word` (a "Mots Défi" entry — see self.challenge_words'
        own docstring) can legally go into slot i RIGHT NOW, at the user's
        explicit request: "modifier le code pour qu'il accepte de placer un
        Mot Défi même si il ne fait pas partie du dictionnaire (vérité
        utilisateur)" — a challenge word is trusted at face value and never
        required to be a real dictionary entry, unlike an ordinary
        candidate (which must come from self._domain(i), i.e. the loaded
        lexicon). Only two things are actually checked: the word's own
        length must match the slot's, and it must agree, letter for
        letter, with every cell of the slot already determined by a REAL
        fact — a crossing slot already assigned during this same attempt,
        or a letter locked from a previous palier (self.locked_letters) —
        the same "fait-acquis" set _domain(i, ignore_forced=True) itself
        constrains against, gathered here directly instead of filtered
        through the dictionary. A mere statistical seed (self.forced_
        letters) is deliberately never treated as a real constraint here,
        same reasoning as _has_known_letter/_placed_letter_count: it's only
        an unverified guess, and a "Mots Défi" word — the author's own
        explicit intent — must win over it, never be blocked by it.
        Placing a word this way can still make a CROSSING slot impossible
        (no real dictionary word left matching the letters it now
        imposes) — an accepted, unavoidable consequence of trusting a
        non-dictionary word at all, already true of this panel's own
        direct click-to-insert (script.js's insertInteractiveChallengeWord,
        "never checks target cells against a real slot/dictionary fit
        first") and still surfaced by this file's own diagnostics
        (_interactive_fill_diagnostics/_invalid_fully_known_indices still
        flag a fully-filled CROSSING slot spelling a non-word this way).
        The slot i itself never gets flagged this way once its own
        content is exactly this challenge word — a "Mots Défi" word is
        considered part of the dictionary for that check, at the user's
        explicit request (see `_challenge_word_cells`'s own `exempt`
        role in `_invalid_fully_known_indices`)."""
        cells = self.slots[i]
        if len(word) != len(cells):
            return False
        for pos, cell in enumerate(cells):
            letter = None
            for j, other_pos in self.cell_to_slots[cell]:
                if j != i and self.assignment[j] is not None:
                    letter = self.assignment[j][other_pos]
                    break
            if letter is None:
                letter = self.locked_letters.get(cell)
            if letter is not None and word[pos] != letter:
                return False
        return True

    def _active_challenge_words(self):
        """`self.challenge_words` minus every word already given up on for
        this attempt (`self._challenge_abandoned`, see FALLBACK_
        PHASE_BUDGET_FRACTION) — the set still eligible to be offered as a
        candidate, to restrict slot selection (`_select_target_slot`
        level 2), or to exempt a crossing slot's dry dictionary domain
        from counting as broken (`_backtrack`'s own domain check and
        `crossing_broken` check). Called instead of reading `self.
        challenge_words` directly everywhere one of those three things is
        decided, so an abandoned word stops influencing the search the
        moment it's given up, exactly as if it had never been typed."""
        if not self.challenge_words or not self._challenge_abandoned:
            return self.challenge_words
        return self.challenge_words - self._challenge_abandoned

    def _register_challenge_word_break(self, word):
        """Counts one more crossing-slot break caused by trying to place
        `word` (see FALLBACK_PHASE_BUDGET_FRACTION) and abandons it
        for the rest of this attempt once its own share of the attempt's
        `deadline_checks` budget is used up. `self._challenge_word_budget`
        is always set by the time this is called (solve() resolves it
        before the search ever starts) whenever `self.challenge_words` is
        non-empty, which is the only case this is ever called from."""
        count = self._challenge_attempt_counts[word] + 1
        self._challenge_attempt_counts[word] = count
        if count >= self._challenge_word_budget:
            self._challenge_abandoned.add(word)

    def _active_priority_words_for(self, cells):
        """`_priority_words_for(self.priority_words, cells)` minus every
        theme word already given up on for this attempt (`self.
        _theme_abandoned`, see FALLBACK_PHASE_BUDGET_FRACTION) — the theme-
        glossary counterpart of `_active_challenge_words`, used the same
        way by `_select_target_slot` (level 5) and `_backtrack`'s own
        theme front-loading, so an abandoned theme word stops being
        preferred the moment it's given up."""
        pw = _priority_words_for(self.priority_words, cells)
        if not pw or not self._theme_abandoned:
            return pw
        return pw - self._theme_abandoned

    def _register_theme_word_break(self, word):
        """Theme-glossary counterpart of `_register_challenge_word_break`
        (see FALLBACK_PHASE_BUDGET_FRACTION): counts one more crossing-slot
        break caused by trying to place theme word `word`, abandoning it
        for the rest of this attempt once its own share of the budget is
        used up. `self._theme_word_budget` is always set by the time this
        is called (solve() resolves it whenever `self.priority_words` is
        non-empty, the only case this is ever called from)."""
        count = self._theme_attempt_counts[word] + 1
        self._theme_attempt_counts[word] = count
        if count >= self._theme_word_budget:
            self._theme_abandoned.add(word)

    def _register_domain_break(self, slot_index):
        """General-dictionary counterpart of `_register_challenge_word_
        break`/`_register_theme_word_break` (see FALLBACK_PHASE_BUDGET_
        FRACTION): counts one more crossing-slot break caused by trying an
        ordinary candidate at `slot_index`, keyed by the SLOT rather than
        by word (an ordinary candidate has no identity worth tracking on
        its own — any dictionary word is as good as another). Once this
        slot's own share of the budget is used up, `_backtrack` stops
        insisting on a crossing-safe candidate for it and accepts the next
        one anyway — see its own `domain_abandoned` check.
        `self._domain_word_budget` is always set by the time this is
        called (solve() resolves it unconditionally, unlike the challenge/
        theme budgets, since the general dictionary is used on every
        grid)."""
        count = self._domain_break_counts[slot_index] + 1
        self._domain_break_counts[slot_index] = count
        if count >= self._domain_word_budget:
            self._domain_break_abandoned.add(slot_index)

    def _placed_letter_count(self, i):
        """Number of cells of slot i already determined by a real letter —
        a crossing word already assigned during this same attempt, or a
        letter locked from a previous palier (self.locked_letters). Same
        fait-acquis/mere-guess distinction as _has_known_letter
        (self.forced_letters, a mere statistical seed, never counts here)
        — see its own docstring. Used by _backtrack to re-sort level 6's
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

        Used by _backtrack as level 8's final tie-break criterion (see its
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
        """At the user's explicit request: "Le tour après une régénération
        semble s'arrêter dès qu'un emplacement est impossible, ce qui peut
        se produire immédiatement à cause du tirage des cases noires.
        Tous les tours doivent se dérouler aussi longtemps qu'on peut
        ajouter des mots en respectant les règles d'ajout."

        To be called once, right before `solve()` (so after the caller
        has applied `preseed_assignment`, if any — see try_fill) and
        before any call to `_backtrack`: at this exact moment, `self.
        assignment` still only contains genuinely locked cells (no search
        decision has been made yet), so every still-unassigned slot's own
        domain reflects only definitive constraints — if it's already
        empty (or entirely already used) at this point, it will stay that
        way for the rest of this search, whatever the search tries
        elsewhere (`_domain` only depends on genuinely assigned/locked
        crossings, never on a choice still to be made).

        Without this fix, `_backtrack`'s own domain check (which runs for
        *every* unassigned slot even before choosing which one to handle)
        found this same slot empty at absolutely every call, whatever
        search path was taken — the search then failed immediately
        (`checks=1` or close to it), with no chance at all to try filling
        the rest of the grid, which was often otherwise perfectly
        fillable. Every slot identified this way is added to `excluded_
        slots` (the same mechanism as for a slot already known impossible
        from a previous palier, see `_pattern_continue`) — never assigned,
        but letting the search continue freely on everything else;
        `_crossing_excluded_slots` is recomputed accordingly, so the new
        "never try to fill a slot crossing a slot deemed impossible" rule
        also applies to these exclusions discovered here, not just to the
        ones received as an argument.

        A single pass is enough (no need to loop back to a fixed point):
        excluding a slot never changes any other slot's own computed
        domain — `_domain` never consults `excluded_slots` at all, it only
        determines which ones `_backtrack` is allowed to select."""
        # No word can have been abandoned yet (see _active_challenge_words):
        # this method only ever runs once, before solve()/_backtrack ever
        # gets a chance to break a crossing slot — resolved once here
        # regardless, for consistency with every other caller of this
        # method rather than reading self.challenge_words directly.
        active_challenge_words = self._active_challenge_words()
        newly_excluded = {
            i for i in range(len(self.slots))
            if self.assignment[i] is None
            and i not in self.excluded_slots
            and i not in self._crossing_excluded_slots
            and all(w in self.used_words for w in self._domain(i))
            # Same "Mots Défi" exemption as _backtrack's own domain check
            # (see its comment): a slot with a dry dictionary domain must
            # not be excluded here, permanently, before the search even
            # starts, if an unused challenge word still fits it.
            and not (active_challenge_words and any(
                w not in self.used_words and self._challenge_word_fits(i, w)
                for w in active_challenge_words
            ))
        }
        if newly_excluded:
            self.excluded_slots = self.excluded_slots | newly_excluded
            self._crossing_excluded_slots = _slots_touching(self.slots, self.excluded_slots)
        return newly_excluded

    def solve(self, deadline_checks):
        # Resolved here (once, from the same `deadline_checks` every
        # recursive `_backtrack` call receives unchanged) rather than in
        # __init__, which never sees this search's own budget — see
        # FALLBACK_PHASE_BUDGET_FRACTION. `max(1, ...)` guarantees a word/
        # slot is never abandoned before even one genuine attempt.
        if self.challenge_words:
            self._challenge_word_budget = max(
                1, round(FALLBACK_PHASE_BUDGET_FRACTION * deadline_checks)
            )
        if self.priority_words:
            self._theme_word_budget = max(
                1, round(FALLBACK_PHASE_BUDGET_FRACTION * deadline_checks)
            )
        # Unlike the two budgets above, always resolved: the general
        # dictionary is used on every grid, themed or not, challenge-word
        # or not.
        self._domain_word_budget = max(
            1, round(FALLBACK_PHASE_BUDGET_FRACTION * deadline_checks)
        )
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
        # A slot whose real dictionary domain is entirely used elsewhere
        # is still not a genuine dead end as long as an unused, still-
        # active "Mots Défi" word could legally go there instead (see
        # `Filler._backtrack`'s own identical exemption during the actual
        # search) — at the user's explicit request: "Les Mots Défi
        # doivent être considérés comme faisant partie du dictionnaire,"
        # so this failed-attempt preview never shows such a slot in red
        # either. `_challenge_word_fits` reads crossing letters off
        # `self.assignment`, already swapped to `best_assignment` above.
        active_challenge_words = self._active_challenge_words()
        result = [
            i for i, word in enumerate(self.best_assignment)
            if word is None
            and all(w in used_at_best for w in self._domain(i, ignore_forced=True))
            and not any(
                cw not in used_at_best and self._challenge_word_fits(i, cw)
                for cw in active_challenge_words
            )
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
        `_backtrack`), via the 8-level cascade documented below.

        Factored out of `_backtrack` to be reused as-is by `interactive_
        place_word` (the web UI's "Interactif" mode), at the user's
        explicit request — a hand-written duplicate of this logic used to
        live there (a plain MRV: the smallest domain, then an already
        partially-known slot, then random), with neither level 3's length
        threshold (which excludes 2-3-letter slots) nor level 6's
        geometric score (which favors the top-left corner) — which made
        interactive fill start with 2-letter slots scattered across the
        grid instead of following the same rules as automatic generation.
        Takes `unassigned`/`domains` as parameters (rather than
        recomputing them) since `interactive_place_word` has already built
        them in a slightly different shape (`viable`, filtered by `used_
        words`) for its own use."""
        # 8-level selection rule, at the user's explicit request (MRV was
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
        # 2. **"Mots Défi", at the user's explicit request, taking priority
        #    over every level below**: if at
        #    least one slot of the drawn category has a not-yet-used, not
        #    yet abandoned "Mots Défi" word (`_active_challenge_words()` —
        #    empty, hence a guaranteed no-op, whenever no challenge list
        #    was supplied at all) still fitting its known letters
        #    (`_challenge_word_fits` — length + already-known letters
        #    only, deliberately NOT required to be a genuine dictionary
        #    entry: a "Mots Défi" word is trusted at face value, at the
        #    user's own explicit follow-up request "modifier le code pour
        #    qu'il accepte de placer un Mot Défi même s'il ne fait pas
        #    partie du dictionnaire"), the choice is restricted to those
        #    slots outright, ahead of every criterion below — moved here,
        #    right after the category draw, after live testing showed the
        #    previous position (after levels 3/4 below) left it starved in
        #    practice: a well-advanced grid almost always has at least one
        #    slot with fewer than `PREFILL_MIN_WORD_COUNT` candidates in
        #    whichever direction gets drawn, and that level, then applied
        #    BEFORE this one, could keep excluding every "Mots Défi"-
        #    fitting slot indefinitely, for reasons having nothing to do
        #    with the challenge word itself. If no slot of the category is
        #    placeable this way, this level changes nothing: level 3 then
        #    applies to the whole category, exactly as before this level
        #    existed;
        # 3. **New, at the user's explicit request, taking priority over
        #    the domain criterion below**: among the slots of the group
        #    obtained at the previous level that are **4 letters and
        #    longer** (at the user's explicit request — a 2-3-letter slot
        #    has a naturally restricted vocabulary, this priority brings
        #    nothing there), if at least one has a domain (`domains[i]`,
        #    already computed right above) with strictly fewer than
        #    `PREFILL_MIN_WORD_COUNT` candidate words — the same threshold
        #    pre-fill's own step 1 uses to decide a slot needs a black
        #    cell — the choice is restricted to those slots only. Goal:
        #    try to resolve these fragile slots with a real word while the
        #    search is still making progress, before a future cleanup
        #    palier judges them insufficient and adds a black cell to fix
        #    them (see `_prefill_unfillable_slots`, step 1) — a word
        #    genuinely placed here avoids that black cell. First tried
        #    with a different criterion ("a single still-empty cell"),
        #    replaced by this one at the user's explicit request, which
        #    directly targets the same threshold pre-fill uses rather than
        #    a geometric proxy. If no slot of the group is in this case,
        #    this level changes nothing: level 4 then applies to the whole
        #    group, exactly as before this level was added;
        # 4. **New, at the user's explicit request**: among the slots of
        #    the group obtained at the previous level, if at least one
        #    already has at least one cell determined by a real letter
        #    (`_has_known_letter` — an already-assigned crossing word, or a
        #    letter locked from a previous palier — never a mere
        #    statistical seed), the choice is restricted to those slots
        #    only, excluding entirely blank slots as long as at least one
        #    already-partially-known one remains — finish an already-
        #    started slot rather than opening a new one. If every slot of
        #    the group is entirely blank, this level changes nothing:
        #    level 5 then applies to the whole group, exactly as before
        #    this level was added;
        # 5. **Themed grid only, at the user's explicit request**: applies
        #    after the "few candidates" and "at least one known cell"
        #    levels above — but since "Mots Défi" (level 2) already ran
        #    ahead of both, this group may already be challenge-narrowed
        #    by the time this level sees it, which is exactly what still
        #    gives challenge words priority over the theme glossary. If,
        #    among the slots of the group obtained at the previous level,
        #    at least one slot exists where a word from the theme glossary
        #    (`self.priority_words`, not yet used) still fits given the
        #    known letters, the choice is restricted to those slots.
        #    Filling therefore starts with thematically achievable zones
        #    (and places a theme word there in priority, see candidate
        #    sorting further below). With no theme, or if no slot of the
        #    group accepts a theme word, this level changes nothing: the
        #    next level then applies to the whole group;
        # 6. among the slots of the group obtained at the previous level, a
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
        # 7. by the number of letters already placed in each slot
        #    (`_placed_letter_count`, the most letters first), reduced to
        #    its own first `SLOT_SELECTION_REFINE_FRACTION` slots (see this
        #    constant's own docstring);
        # 8. by `_slot_letter_frequency_score` (see its own docstring), the
        #    highest score first — the slot whose own zone statistically
        #    offers the most fill options — whose very first entry directly
        #    becomes the chosen slot. Each of these two reductions
        #    re-shuffles its own input window beforehand (same reason as
        #    level 6's own shuffle: since `sorted` is stable, this shuffle
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
        # "Mots Défi" level: at the user's explicit request, applied here —
        # right after the category draw, AHEAD of every other level below
        # including "few candidates" — after a live report that the
        # previous position (below "few candidates"/"known letter", sharing
        # a spot with the theme level) left it starved in practice: a
        # well-advanced grid almost always has at least one slot with
        # fewer than `PREFILL_MIN_WORD_COUNT` candidates in whichever
        # direction gets drawn, and that level used to run BEFORE this one
        # — so it could keep excluding every "Mots Défi"-fitting slot,
        # click after click, before this level ever got a chance to run at
        # all: a real challenge word the author explicitly typed could
        # then go unplaced indefinitely, for reasons having nothing to do
        # with it. "Mots Défi" now overrides that urgency
        # instead: if at least one slot of the drawn category has a
        # not-yet-used, not-yet-abandoned challenge word (`_active_
        # challenge_words()` — see FALLBACK_PHASE_BUDGET_FRACTION;
        # empty, hence a guaranteed no-op, whenever no challenge list was
        # supplied at all) still fitting its known letters
        # (`_challenge_word_fits` — length + already-known letters only,
        # deliberately NOT required to be a genuine dictionary entry, at
        # the user's own explicit follow-up request: "modifier le code
        # pour qu'il accepte de placer un Mot Défi même s'il ne fait pas
        # partie du dictionnaire (vérité utilisateur)" — `domains[i]` only
        # ever holds genuine dictionary candidates, so intersecting
        # against it would silently exclude a real surname or any other
        # word the lexicon doesn't happen to contain), the choice narrows
        # to those slots outright — and every level below (including "few
        # candidates") then only ever narrows further WITHIN this already-
        # challenge-preferred group, never widening back out to the whole
        # category. Skipped if there's no challenge list at all (or none
        # of it still active), or if no slot of the category accepts any
        # challenge word (nothing to restrict);
        active_challenge_words = self._active_challenge_words()
        if active_challenge_words:
            challenge_placeable = [
                i for i in direction_pool
                if any(
                    w not in self.used_words and self._challenge_word_fits(i, w)
                    for w in active_challenge_words
                )
            ]
            if challenge_placeable:
                direction_pool = challenge_placeable
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
        # Theme level: applies after the two levels above ("few
        # candidates" then "at least one known cell"), at the user's
        # explicit request — but, since "Mots Défi" now runs before all
        # three (see above), this group can already be challenge-narrowed
        # by the time this level ever sees it, which is exactly what still
        # gives challenge words priority over the theme glossary in that
        # case. For a themed grid, the choice is restricted to the slots
        # of the group obtained at the previous level where at least one
        # word from the theme glossary (self.priority_words) still fits,
        # given the letters already known and the words already placed
        # elsewhere — filling therefore favors thematically achievable
        # zones (and places a theme word there in priority, see candidate
        # sorting further below). Skipped if there's no theme at all, or
        # if no slot of the group accepts a theme word (nothing to
        # restrict).
        if self.priority_words:
            # `selection_pool` always stays within a single direction
            # (across or down) — it only ever narrows `direction_pool`,
            # never mixes the two — so the applicable glossary (the same
            # for all its slots) is resolved once — a single frozenset on
            # a monolingual grid, that direction's language glossary on a
            # bilingual grid (see `_priority_words_for`). `_active_
            # priority_words_for` (rather than `_priority_words_for`
            # directly) excludes a theme word already abandoned this
            # attempt (see FALLBACK_PHASE_BUDGET_FRACTION), so slot
            # selection stops favoring a slot only a hopeless theme word
            # still fits.
            _pw = self._active_priority_words_for(self.slots[selection_pool[0]])
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
        # Resolved once for the whole node (never changes mid-node, only
        # between recursive calls — see _register_challenge_word_break):
        # every place below that needs to know which "Mots Défi" words are
        # still worth trying uses this instead of self.challenge_words
        # directly, so a word already abandoned this attempt (see
        # FALLBACK_PHASE_BUDGET_FRACTION) stops influencing anything,
        # exactly as if it had never been supplied at all.
        active_challenge_words = self._active_challenge_words()
        domains = {}
        for i in unassigned:
            domain = self._domain(i)
            if all(w in self.used_words for w in domain):
                # A dictionary-empty domain is only a genuine dead end if
                # no unused "Mots Défi" word can still fill this exact
                # slot — a challenge word is trusted purely geometrically
                # (_challenge_word_fits), never against this domain at
                # all, so a slot reserved for one (a real surname, say,
                # absent from the lexicon) must not abort this whole
                # branch just because _domain(i) itself came back dry.
                if not (active_challenge_words and any(
                    w not in self.used_words and self._challenge_word_fits(i, w)
                    for w in active_challenge_words
                )):
                    return False
            domains[i] = domain

        # Selects which slot to fill next via the 8-level cascade,
        # factored out into _select_target_slot (reused as-is by
        # interactive_place_word — see its own docstring for the full
        # history of every level).
        best_i = self._select_target_slot(unassigned, domains)
        # Resolved once for the whole node, same timing as
        # active_challenge_words above: whether the general-dictionary
        # tier's own budget for THIS slot is already spent (see
        # FALLBACK_PHASE_BUDGET_FRACTION/_register_domain_break) — if so,
        # the crossing_broken forward-check below is relaxed for this
        # slot's ordinary candidates instead of rejecting them forever.
        domain_abandoned = best_i in self._domain_break_abandoned

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
        pri_set = frozenset()
        if self.priority_words:
            # Theme preselection: the order already obtained above is
            # stabilized into two blocks — theme candidates first, then
            # the rest — so `for w in cands:` tries every theme word that
            # fits this slot before moving on to an ordinary dictionary
            # word. Backtracking does the rest: a non-theme word is only
            # reached if no theme word led to a solution here (nor
            # further down). Skipped if every candidate — or none of
            # them — is thematic (nothing to reorder). `_active_priority_
            # words_for` (rather than `_priority_words_for` directly)
            # excludes a theme word already abandoned this attempt (see
            # FALLBACK_PHASE_BUDGET_FRACTION). `pri_set` is kept regardless
            # of whether `cands` was actually reordered, so the crossing-
            # break registration below can still recognize a theme word
            # even in the (rare) case every candidate here is thematic.
            _pw = self._active_priority_words_for(self.slots[best_i])
            pri = [w for w in cands if w in _pw]
            if pri:
                pri_set = frozenset(pri)
                if len(pri) != len(cands):
                    cands = pri + [w for w in cands if w not in pri_set]
        challenged_set = frozenset()
        if active_challenge_words:
            # "Mots Défi" win over both the theme reorder above and the
            # ordinary dictionary domain — exactly the same precedence as
            # `interactive_place_word`'s own `pool = challenged or themed
            # or cands`. Unlike that one-shot placement, injected here
            # into `cands` rather than replacing it outright, so a
            # challenge word that turns out to make some crossing slot
            # unfillable still lets backtracking fall back to `cands`'
            # other entries instead of failing this slot outright — the
            # "chercher un autre Mot Défi ou un autre emplacement" retry
            # described at FALLBACK_PHASE_BUDGET_FRACTION: another
            # challenge word fitting this same slot (if any) is simply the
            # next entry of `challenged` itself, tried right below; another
            # LOCATION for this same word is instead found across separate
            # recursive calls, as `_select_target_slot`'s own level 2 keeps
            # preferring any slot it still fits, for as long as it stays
            # unused and unabandoned.
            challenged = [
                w for w in active_challenge_words
                if w not in self.used_words and self._challenge_word_fits(best_i, w)
            ]
            if challenged:
                challenged_set = frozenset(challenged)
                cands = challenged + [w for w in cands if w not in challenged_set]
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
                    if all(w2 in self.used_words for w2 in domain) and not (
                        # Same "Mots Défi" exemption as the domain check
                        # above and at the top of this method: a crossing
                        # slot left with no real dictionary candidate is
                        # only a genuine break if no unused, not-yet-
                        # abandoned challenge word can still fill IT in
                        # turn — otherwise placing `w` here hasn't actually
                        # closed off that neighbor, it's merely handed it
                        # to the "Mots Défi" mechanism instead of the
                        # ordinary dictionary.
                        active_challenge_words and any(
                            w2 not in self.used_words
                            and self._challenge_word_fits(j, w2)
                            for w2 in active_challenge_words
                        )
                    ):
                        crossing_broken = True
                        break
            # "Backtrack immédiat" (see FALLBACK_PHASE_BUDGET_FRACTION):
            # every candidate here, whichever of the three tiers it comes
            # from, already gets reverted on the spot the moment it breaks
            # a crossing slot (`crossing_broken`, checked right above) —
            # the loop then simply moves on to the next entry of `cands`,
            # which is exactly "try another candidate of the same tier, or
            # fall through to the next one" for this slot. What's tracked
            # here on top of that is each tier's own budget: a challenge
            # or theme word that has spent its own share of the whole
            # attempt's `deadline_checks` on nothing but broken crossings
            # is abandoned for the rest of this attempt, so it stops being
            # offered anywhere else in the grid too (`_register_challenge_
            # word_break`/`_register_theme_word_break`). The general
            # dictionary has no further tier to fall back to: once THIS
            # slot's own share of the budget is spent the same way
            # (`_register_domain_break`), `domain_abandoned` (resolved
            # once at the top of this node) lets an ordinary candidate
            # through anyway instead of rejecting it forever — accepting a
            # known "impossible" crossing rather than paying for
            # exhaustive backtracking first; the cross-palier retry
            # machinery (`_clean_blocked_slots`/`_build_retry_seed`)
            # already repairs this kind of zone on the next palier
            # regardless of how it arose.
            if crossing_broken:
                if w in challenged_set:
                    self._register_challenge_word_break(w)
                elif w in pri_set:
                    self._register_theme_word_break(w)
                elif domain_abandoned:
                    crossing_broken = False
                else:
                    self._register_domain_break(best_i)
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
             non_gloss_words=None, max_non_gloss=None, priority_words=None,
             challenge_words=None, required_cells=None):
    """`non_gloss_words`/`max_non_gloss` (both `None` by default — every
    pre-existing caller unaffected) work exactly like `proper_noun_words`/
    `max_proper_nouns` below, but count words absent from the definition
    dictionary `data/gloss_dictionary/<lang>_glosses.jsonl` instead of
    likely proper nouns — same final-guardrail mechanism, same
    difficulty caps by request (see MAX_NON_GLOSS_WORDS): "FACILE :
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

    `required_cells` (`None` by default — no effect for any pre-existing
    caller) relaxes `truly_complete` further still, at the user's explicit
    request for "Finir la zone" (backend/app.py's `interactive_finish`):
    "quand toutes les cases non verrouillées sont remplies, et que les
    emplacements complets sont des mots valides qui ne créent pas de zones
    impossibles, la grille doit être considérée comme réussie, même si il
    reste des emplacements non complets couvrant les cases verrouillées."
    A set of `(row, col)` cells the caller actually cares about resolving
    — for "Finir la zone", every cell inside the selected zone that was
    still blank when the button was clicked; for plain "Finir la grille"
    (no zone), every blank cell of the whole grid, which makes this a
    total no-op there (see below). When given, `truly_complete` only
    requires `filler.assignment[i]` to be non-`None` for a slot `i` that
    touches at least one of these cells — a slot entirely made of cells
    OUTSIDE `required_cells` (already locked/lettered before the search
    started, or lying outside the selected zone and reverted afterward by
    `zone_revert`, see `_run_generate_job`) is allowed to stay unresolved
    forever, whether it's genuinely impossible (a locked letter clashing
    with whatever the search chose for a crossing word) or the search
    simply never got to it. This can never make `truly_complete` weaker
    than the strict rule above: any slot resolved under the strict rule
    stays resolved here too, so a fully-solved grid is always accepted
    either way. `generate_grid()`'s own final result construction drops
    every entry of `result["words"]` whose `answer` ended up `None` this
    way (an unresolved slot is never a genuine word — no clue is ever
    generated for it), and `build_letters_grid` simply skips writing a
    word it never received, leaving that word's own cells to whichever
    crossing slot (if any) supplies them, exactly as `zone_revert` already
    expects. For "Finir la grille" (`required_cells` = every currently
    blank cell of the whole grid), a slot with zero `required_cells` cells
    can only be one entirely covered by already-locked letters — already
    unconditionally promoted as-is elsewhere in this file regardless of
    dictionary validity (see `_pattern_attempt`/`_pattern_continue`'s own
    "promoted as-is" comment) — so this parameter is provably a no-op
    there: nothing this rule would otherwise exempt was ever capable of
    staying unresolved under the strict rule in the first place.

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
                     priority_words=priority_words, challenge_words=challenge_words)
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
                "challenge_cells": _challenge_word_cells_from_assignment(
                    slots, best_assignment, challenge_words
                ),
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
    # `required_cells` (see this function's own docstring): relaxes the
    # strict rule above — only a slot touching at least one of these cells
    # must be resolved. Provably never stricter than the rule above (every
    # slot resolved there is trivially resolved here too), so this can
    # only ever turn a `False` into a `True`, never the reverse.
    if required_cells is not None:
        truly_complete = all(
            w is not None
            for i, w in enumerate(filler.assignment)
            if any(cell in required_cells for cell in slots[i])
        )
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
            diagnostics["challenge_cells"] = _challenge_word_cells_from_assignment(
                slots, filler.best_assignment, challenge_words
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
    cell.

    Deliberately never receives `required_cells` (see `try_fill`'s own
    docstring, "Finir la zone"): its own internal `try_fill` calls below
    always require FULL, strict completeness for the candidate grid being
    tried, never the relaxed rule. A genuinely optional slot left
    unresolved by the original search is therefore simply never re-solved
    correctly here either — every candidate black-cell removal near it
    fails this strict re-check and is reverted, so this whole function
    quietly skips optimizing that area rather than risking a crash or an
    incorrect result. A safe, accepted trade-off: the grid `minimize_
    black_squares` receives is returned unchanged wherever it can't find
    a valid improvement, never corrupted."""
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


def _interactive_fill_diagnostics(grid, rows, cols, index, challenge_words=None):
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
    computation helper, like `interactive_place_word`.

    `challenge_words` (`None`/empty by default — no effect for any
    pre-existing caller) is the session's current "Mots Défi" list, in
    grid form: a "Mots Défi" word is considered part of the dictionary
    for this diagnostic, at the user's explicit request — never flagged
    red, whether it's a still-open slot only a challenge word can fill
    (`_challenge_fillable_slot_indices`, folded into "low candidates"
    instead of "impossible", since the real dictionary genuinely has
    nothing there) or an already-fully-typed slot spelling a challenge
    word verbatim (`_challenge_word_cells`, folded into `_invalid_fully_
    known_indices`'s own `exempt` set — never flagged at all, exactly
    like a genuine dictionary word)."""
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

    fillable = _challenge_fillable_slot_indices(slots, known, challenge_words)
    impossible, low = set(), set()
    for i, cells in enumerate(slots):
        if filler.assignment[i] is not None:
            continue
        n = len(set(filler._domain(i)) - filler.used_words)
        if n == 0:
            if i in fillable:
                low.update(cells)
            else:
                impossible.update(cells)
        elif n < PREFILL_MIN_WORD_COUNT:
            low.update(cells)
    exempt = _challenge_word_cells(slots, known, challenge_words)
    for i in _invalid_fully_known_indices(slots, index, known, exempt=exempt):
        impossible.update(slots[i])
    return (
        sorted([r, c] for (r, c) in impossible),
        sorted([r, c] for (r, c) in low),
    )


def _open_slot_baseline(filler, exclude_index):
    """Snapshot, for every still-open slot other than `exclude_index` (the
    slot a candidate is about to be tried at), of its own currently-viable
    candidate set (`_domain(j) - used_words`) — computed with `exclude_
    index` itself left unassigned, i.e. the grid exactly as it stands
    right now, before any candidate is even considered for it.

    Feeds `_word_breaks_open_slot`'s own "was this slot already broken,
    independent of whatever candidate ends up tried" distinction: a slot
    already empty here is a pre-existing condition (already visible in
    this same diagnostics pass's red/orange highlighting) that must never
    be blamed on the candidate under test — without this baseline, one
    unrelated impossible zone anywhere in the grid would make every
    single candidate at every slot look "unsafe" forever after, since a
    permanently-empty domain trivially satisfies "all remaining
    candidates are used up" regardless of what gets placed elsewhere.

    Candidate-independent (nothing about which word ends up tried at
    `exclude_index` changes any OTHER slot's own domain, except one
    literally CROSSING it — see `_word_breaks_open_slot`'s own per-
    candidate recompute for that one case), so it only needs computing
    once per `exclude_index` and can be reused across every candidate
    considered for it."""
    baseline = {}
    for j in range(len(filler.slots)):
        if j == exclude_index or filler.assignment[j] is not None:
            continue
        baseline[j] = set(filler._domain(j)) - filler.used_words
    return baseline


def _word_breaks_open_slot(filler, i, w, active_challenge_words, baseline):
    """Tentatively assigns `w` to slot `i` on `filler` (a `Filler` built
    purely as a domain/scoring helper, no search running), checks whether
    any OTHER still-open slot in the grid — not only one literally
    CROSSING `i` — becomes impossible to fill as a result, then reverts
    the tentative assignment either way, leaving `filler` exactly as it
    found it. A themed glossary is typically narrow, so the very word
    chosen for one slot can just as easily be the LAST unused dictionary
    candidate of some entirely disjoint slot elsewhere (no shared cell at
    all) as of a crossing one — placing it still empties that other
    slot's own domain, exactly the "impossible zone" this whole mechanism
    exists to avoid, whether or not the two slots ever touch.

    `baseline` (see `_open_slot_baseline`, computed once by the caller for
    this same `i`) is what tells a slot genuinely broken BY this candidate
    apart from one that was already broken beforehand for an unrelated
    reason — only the former counts. For a slot `j` sharing a cell with
    `i` (`filler._crossing_slots[i]`), `w`'s own letters can change `j`'s
    domain, so it's recomputed fresh from the tentative assignment; for
    every other still-open slot, its domain never depends on what ends up
    placed at `i`, so `baseline[j]` is reused as-is, only removing `w`
    itself in case it was the one candidate keeping `j` alive. Either way,
    a slot with no real dictionary candidate left only counts as broken if
    no unused, still-active challenge word (`active_challenge_words`) can
    fill it in turn — the same two-way exemption `Filler._backtrack`'s own
    `crossing_broken` check applies.

    Used by `interactive_place_word`'s own "Mots Défi" crossing-safety
    retry (see FALLBACK_PHASE_BUDGET_FRACTION) to evaluate a
    candidate (word, slot) combination without committing to it, and
    reused as-is for its theme-glossary/general-dictionary crossing-safety
    scan too (see `_first_crossing_safe`) — this check has no notion of
    which tier `w` belongs to, only whether placing it breaks some other
    slot. Unlike `_backtrack`'s own version (scoped to direct crossings
    only, and self-healed palier to palier by the cross-palier retry
    machinery `interactive_place_word` has no equivalent of), there is no
    `excluded_slots`/`_crossing_excluded_slots` to check here —
    `interactive_place_word` never builds a `Filler` with either
    non-empty."""
    filler.assignment[i] = w
    filler.used_words.add(w)
    broken = False
    for j, before in baseline.items():
        if not before:
            continue
        if j in filler._crossing_slots[i]:
            remaining = set(filler._domain(j)) - filler.used_words
        else:
            remaining = before - {w}
        if not remaining and not (
            active_challenge_words and any(
                w2 not in filler.used_words and filler._challenge_word_fits(j, w2)
                for w2 in active_challenge_words
            )
        ):
            broken = True
            break
    filler.assignment[i] = None
    filler.used_words.discard(w)
    return broken


def _free_matching_slot(by_length, word, locked_letters, claimed):
    """Like `_has_free_matching_slot` but returns the matching slot's own
    cell tuple (or `None`) instead of a bare bool — `interactive_place_
    word` needs the actual cells to use directly as a candidate slot, not
    merely a yes/no answer that one exists. Still claims the slot in place
    on a match (same `claimed` bookkeeping, shared across every word of one
    pool scanned in the same pass), so a same-length slot is never handed
    to two different words."""
    for cells in by_length.get(len(word), ()):
        key = tuple(cells)
        if key in claimed:
            continue
        if all(locked_letters.get(cell, letter) == letter
               for cell, letter in zip(cells, word)):
            claimed.add(key)
            return cells
    return None


def _try_reshape_for_word(base_pattern, rows, cols, rng, word, locked_letters, index):
    """Tries ONE independent widen-then-shorten reshape for `word` alone,
    on a FRESH copy of `base_pattern` — `base_pattern` itself is never
    touched, and this attempt never sees, or is influenced by, any other
    word's own attempt: each candidate is judged purely on its own,
    isolated copy of the grid, at the user's explicit request: "chaque
    tentative doit être considérée comme indépendante des autres... [si
    plusieurs sont testées] il faut qu'elles soient isolées sur des copies
    de la situation." Returns `(cells, pattern)` — `pattern` is the mutated
    copy, ready to become the real grid if this candidate ends up being the
    one placed — or `None` if neither move can accommodate `word` at all."""
    copy = [row[:] for row in base_pattern]
    change = _widen_one_floating_black_cell(copy, rows, cols, rng, word, locked_letters, set(), index)
    if change is None:
        change = _shorten_one_slot_for_word(copy, rows, cols, rng, word, locked_letters, index)
    if change is None:
        return None
    return change["slot_cells"], copy


def _build_interactive_filler(pattern, rows, cols, index, rng, known, priority_words, challenge_words):
    """Builds a fresh `Filler` (plus its own `extract_slots` list) purely
    from `pattern` — the one and only place `interactive_place_word`
    constructs a `Filler`, called once for the grid's own base pattern and
    again, independently, for every isolated per-word reshape copy it goes
    on to consider (`_try_reshape_for_word`) — never for a pattern carrying
    more than one word's own reshape at a time. Every already-fully-known
    slot is pre-assigned so it can neither be re-selected nor counted as a
    new placement, and its word blocks a duplicate elsewhere."""
    slots = extract_slots(pattern, rows, cols)
    _, letter_scores = sample_letter_biases(
        pattern, rows, cols, index, rng, force_fraction=0.0, known_letters=known,
    )
    filler = Filler(
        slots, index, rng,
        letter_scores=letter_scores, locked_letters=known,
        priority_words=priority_words, challenge_words=challenge_words,
    )
    for i, cells in enumerate(slots):
        if all(cell in known for cell in cells):
            word = "".join(known[cell] for cell in cells)
            filler.assignment[i] = word
            filler.used_words.add(word)
    return filler, slots


def _find_priority_word_placement(
    pool_flat, viable, filler, slots, target, base_pattern, rows, cols, rng, index,
    known, priority_words, challenge_words, fits_ordinary, is_eligible, register_break,
    set_budget,
):
    """Shared search behind BOTH of `interactive_place_word`'s priority
    tiers ("Mots Défi" first, then the theme glossary): tries every
    ordinary, already-dictionary-viable slot this word pool fits
    (`target`'s own combos first, each ranked by the same statistical
    score/frequency the final word draw uses) purely against `filler`/
    `slots` — the grid's own base pattern, never touched by any reshape,
    so nothing here can ever be contaminated by another candidate's own
    speculative change. Only once every ordinary combo has failed does it
    give each remaining word with no natural or ordinary slot at all its
    own, fully ISOLATED widen-or-shorten attempt (`_try_reshape_for_word`,
    one independent copy of `base_pattern` per word — never a shared,
    cumulatively-reshaped one) and test it on a dedicated `Filler` built
    from that one copy alone (`_build_interactive_filler`) — at the user's
    explicit request that concurrent attempts must never share or
    influence each other's state, unlike `_pattern_attempt` (automatic
    generation's own, unrelated caller of the same low-level widen/shorten
    primitives), which is free to batch them because it never has to
    settle on just one winner out of several. `register_break`/`is_
    eligible`/`set_budget` are this tier's own bookkeeping hooks (`Filler.
    _register_challenge_word_break`/`_register_theme_word_break` and
    friends) — always applied to the outer `filler`, never to a per-
    candidate isolated one, so abandonment persists across both the
    ordinary and the reshape phase regardless of which specific `Filler`
    ends up confirming any one candidate. The exemption `_word_breaks_
    open_slot` itself checks (some OTHER still-active "Mots Défi" word
    able to bail out a slot this candidate would otherwise break) is
    always drawn fresh from `filler`'s own current challenge pool,
    regardless of which tier is running — that check is hard-coded to
    challenge words specifically, not to whichever pool this call happens
    to be searching. Returns `(target_index, word, cells, pattern_to_
    commit)` on success, `None` once every ordinary and reshaped candidate
    has been tried and rejected."""
    combos = [(i, w) for i in viable for w in pool_flat if fits_ordinary(i, w)]

    def _combo_key(iw):
        i, w = iw
        freq = index.for_cells(slots[i]).get(len(slots[i]), {}).get("freq", {})
        return (i != target, -filler._candidate_score(i, w), -freq.get(w, 0.0))

    combos.sort(key=_combo_key)

    claimed = set()
    by_length = _slots_by_length(base_pattern, rows, cols)
    ordinary_words = {w for _, w in combos}
    reshape_words = [
        w for w in pool_flat
        if w not in ordinary_words and len(w) >= 2
        and _free_matching_slot(by_length, w, known, claimed) is None
    ]
    rng.shuffle(reshape_words)
    reshape_words = reshape_words[:WIDEN_PRIORITY_WORDS_LIMIT]

    set_budget(max(1, round(FALLBACK_PHASE_BUDGET_FRACTION * (len(combos) + len(reshape_words)))))

    baseline_cache = {}
    for i, w in combos:
        if w in filler.used_words or not is_eligible(w):
            continue
        exemption_pool = filler._active_challenge_words() - filler.used_words
        baseline = baseline_cache.get(i)
        if baseline is None:
            baseline = baseline_cache[i] = _open_slot_baseline(filler, i)
        if _word_breaks_open_slot(filler, i, w, exemption_pool, baseline):
            register_break(w)
            continue
        return i, w, slots[i], base_pattern

    for w in reshape_words:
        if w in filler.used_words or not is_eligible(w):
            continue
        found = _try_reshape_for_word(base_pattern, rows, cols, rng, w, known, index)
        if found is None:
            register_break(w)
            continue
        cand_cells, cand_pattern = found
        cand_filler, cand_slots = _build_interactive_filler(
            cand_pattern, rows, cols, index, rng, known, priority_words, challenge_words,
        )
        # `extract_slots` returns each slot as a plain list of (row, col)
        # tuples, while `cand_cells` (from `_try_reshape_for_word`, itself
        # from `_try_widen_black_cell`/`_try_shorten_slot`'s own `tuple(
        # span)`) is a tuple of the same — `list.index` never matches a
        # tuple against a list even with identical elements, so compare
        # both sides as tuples here instead of relying on `list.index`.
        j = next(k for k, c in enumerate(cand_slots) if tuple(c) == cand_cells)
        exemption_pool = filler._active_challenge_words() - filler.used_words
        baseline = _open_slot_baseline(cand_filler, j)
        if _word_breaks_open_slot(cand_filler, j, w, exemption_pool, baseline):
            register_break(w)
            continue
        return j, w, cand_cells, cand_pattern

    return None


def interactive_place_word(grid, rows, cols, index, rng, priority_words=None,
                            challenge_words=None):
    """Place EXACTLY ONE additional word into `grid`, honouring every letter
    already present, with NO backtracking — the single-step primitive behind
    the web UI's "Interactif" authoring mode (see backend/app.py's
    `_run_interactive_job` / `POST /api/interactive/step`).

    `grid` is a 2D list where each cell is `"#"` (black), `"."` (empty
    white) or an uppercase letter (filled white). `extract_slots` already
    treats any non-`"#"` cell as white, so a grid carrying letters feeds
    straight into it. `index` is a DualIndex; `priority_words` a
    frozenset/DualSet (theme glossary) or `None`; `challenge_words` the
    "Mots Défi" list (a plain frozenset of bare uppercase words, or `None`)
    — given priority over `priority_words`, see `Filler.challenge_words`
    and `_select_target_slot`'s own docstring. Unlike `priority_words`, a
    challenge word is trusted at face value and never required to be a
    genuine dictionary entry (`Filler._challenge_word_fits`) — a word not
    in the lexicon can therefore still be placed automatically, though it
    then shows up flagged as invalid by this same function's own
    diagnostics below, same as a hand-typed one.

    A "Mots Défi"/theme word with no matching-length slot anywhere yet can
    still be reached, via `_try_reshape_for_word` relocating or inserting a
    black cell — the same low-level widen/shorten mechanics automatic
    generation's own `_pattern_attempt` uses (`_widen_one_floating_black_
    cell`/`_shorten_one_slot_for_word`) — but, unlike that caller, never as
    a shared batch applied cumulatively to one pattern for the whole word
    list at once: every word considered for a reshape gets its OWN,
    independent attempt on its OWN fresh copy of the grid's base pattern,
    at the user's explicit request: "chaque tentative doit être considérée
    comme indépendante des autres... si plusieurs tentatives sont testées
    simultanément, il faut qu'elles soient isolées sur des copies de la
    situation." See `_find_priority_word_placement`'s own docstring for
    why: a shared, cumulative pattern let one word's own reshape silently
    depend on — or get silently undone by — a completely unrelated word's
    own reshape tried later in the same call, corrupting the very safety
    check meant to prevent an impossible zone.

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
    known = {
        (r, c): grid[r][c]
        for r in range(rows)
        for c in range(cols)
        if grid[r][c] not in (BLACK, WHITE)
    }
    filler, slots = _build_interactive_filler(
        pattern, rows, cols, index, rng, known, priority_words, challenge_words,
    )
    if not slots:
        return {"impossible": True}

    # `domains` keeps the RAW domain (like in _backtrack, never filtered by
    # used_words) — that's what _select_target_slot expects, in
    # particular for its own "fewer than PREFILL_MIN_WORD_COUNT
    # candidates" threshold (level 3), which genuinely compares the raw
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
        imp, low = _interactive_fill_diagnostics(grid, rows, cols, index, challenge_words)
        return {"impossible": True, "impossible_cells": imp, "low_candidate_cells": low}

    # Target slot: the same 8-level cascade as automatic generation (see
    # Filler._select_target_slot), reused as-is rather than a hand-rolled,
    # simple MRV — at the user's explicit request, after confirming live
    # that this MRV (smallest domain first) made Interactive mode start
    # with 2-letter slots scattered across the grid, honoring neither the
    # length threshold (level 3, >=4 letters) nor the top-left front
    # sought by automatic generation's own geometric score (level 6). Only
    # ever resolved against the grid's own BASE pattern — never against a
    # reshaped one, since which reshape (if any) ends up mattering is only
    # known once a specific tier below actually settles on one.
    target = filler._select_target_slot(list(viable.keys()), domains)

    # Tier 1: "Mots Défi", at the user's explicit request: a challenge word
    # is never placed without first checking it leaves every OTHER
    # still-open slot in the grid still fillable (`_word_breaks_open_slot`,
    # the same exemption `Filler._backtrack`'s own `crossing_broken` check
    # applies) — not only a slot it directly crosses, but also a disjoint
    # one whose own domain this word happened to be the last unused
    # candidate for. A combination that would break either kind is
    # abandoned on the spot ("backtrack immédiat") in favor of the next
    # one — see `_find_priority_word_placement`'s own docstring for the
    # full two-phase search (ordinary slots first, each ranked by
    # statistical score/frequency, `target`'s own combos first; then an
    # isolated reshape attempt for any word left with no slot at all).
    placed_from = None
    placed_target = placed_word = cells = pattern_to_commit = None

    challenge_pool = filler._active_challenge_words() - filler.used_words
    if challenge_pool:
        found = _find_priority_word_placement(
            challenge_pool, viable, filler, slots, target, pattern, rows, cols, rng, index,
            known, priority_words, challenge_words,
            fits_ordinary=filler._challenge_word_fits,
            is_eligible=lambda w: w not in filler._challenge_abandoned,
            register_break=filler._register_challenge_word_break,
            set_budget=lambda v: setattr(filler, "_challenge_word_budget", v),
        )
        if found is not None:
            placed_target, placed_word, cells, pattern_to_commit = found
            placed_from = "challenge"

    # Tier 2: the theme glossary, tried only once every "Mots Défi"
    # combination and reshape attempt has failed (or none was typed) —
    # same two-phase search, restricted to genuine dictionary candidates
    # (`w in viable[i]`) belonging to the glossary applicable to slot i's
    # own direction (`_priority_words_for`), so a bilingual grid's two
    # per-language glossaries are never mixed up.
    if placed_from is None and priority_words:
        theme_pool = _flatten_priority_words(priority_words) - filler.used_words
        if theme_pool:
            def _theme_fits(i, w):
                return w in viable[i] and w in _priority_words_for(filler.priority_words, slots[i])

            found = _find_priority_word_placement(
                theme_pool, viable, filler, slots, target, pattern, rows, cols, rng, index,
                known, priority_words, challenge_words,
                fits_ordinary=_theme_fits,
                is_eligible=lambda w: w not in filler._theme_abandoned,
                register_break=filler._register_theme_word_break,
                set_budget=lambda v: setattr(filler, "_theme_word_budget", v),
            )
            if found is not None:
                placed_target, placed_word, cells, pattern_to_commit = found
                placed_from = "theme"

    # Tier 3: the general dictionary, at `target` only (this tier's own
    # candidates are never reshaped for — automatic generation's own
    # widening never reshapes for a plain dictionary word either, only for
    # "Mots Défi"/theme words). Both other pools are excluded outright: any
    # "Mots Défi"/theme word still present in `viable[target]` was
    # necessarily already tried, across every slot in the grid, by one of
    # the two tiers above — reconsidering it here would just let the
    # "accept anyway" compromise below re-select that exact same word for
    # the exact same reason it was already rejected. Excluding both pools
    # empty is the one case this must not apply: if nothing else at all can
    # go here, `viable[target]` (unfiltered) is still tried as an absolute
    # last resort, so "Suivant" never gets stuck.
    if placed_from is None:
        target_cells = slots[target]
        cands = viable[target]
        exclude = set(challenge_words or ()) | _flatten_priority_words(priority_words)
        plain_cands = cands - exclude if exclude else cands
        freq = index.for_cells(target_cells).get(len(target_cells), {}).get("freq", {})
        active_challenge_words = filler._active_challenge_words() - filler.used_words
        baseline = _open_slot_baseline(filler, target)

        def _rank(pool):
            return sorted(
                pool,
                key=lambda w: (filler._candidate_score(target, w), freq.get(w, 0.0)),
                reverse=True,
            )

        def _first_crossing_safe(ranked):
            return next(
                (w for w in ranked
                 if not _word_breaks_open_slot(filler, target, w, active_challenge_words, baseline)),
                None,
            )

        ranked_plain = _rank(plain_cands)
        word = _first_crossing_safe(ranked_plain)
        if word is None:
            # Every plain candidate available at this slot breaks some
            # crossing: accept the best one anyway rather than leaving
            # "Suivant" stuck — the same final compromise `_backtrack`'s
            # own general-dictionary tier falls back to once its own
            # budget is spent. Still never a "Mots Défi"/theme word here,
            # unless `plain_cands` was empty to begin with.
            word = ranked_plain[0] if ranked_plain else _rank(cands)[0]
        placed_target, placed_word, cells, pattern_to_commit = target, word, target_cells, pattern

    word = placed_word

    # Reconcile the winning candidate's own pattern (the grid's base one,
    # untouched, for an ordinary or general-dictionary pick; a fresh,
    # single-word isolated reshape copy for a "Mots Défi"/theme pick that
    # needed one) with the real letter grid — a cell can only ever move
    # from a plain, letter-free WHITE to BLACK or back (never the reverse
    # for an already-known cell, per `_try_widen_black_cell`/`_try_shorten_
    # slot`'s own `locked_letters` guard), so this is always a safe,
    # letter-preserving merge — then write this round's own word in.
    new_grid = [
        [
            BLACK if pattern_to_commit[r][c] == BLACK
            else (WHITE if grid[r][c] == BLACK else grid[r][c])
            for c in range(cols)
        ]
        for r in range(rows)
    ]
    for (r, c), ch in zip(cells, word):
        new_grid[r][c] = ch
    # Diagnostics computed on the grid AFTER placement — that's the state
    # the player sees after "Suivant".
    imp, low = _interactive_fill_diagnostics(new_grid, rows, cols, index, challenge_words)
    return {
        "impossible": False,
        "grid": new_grid,
        "impossible_cells": imp,
        "low_candidate_cells": low,
        "placed": {
            "cells": [[r, c] for (r, c) in cells],
            "word": word,
            "direction": slot_direction(cells),
            # `from_theme`/`from_challenge`: which tier actually won —
            # shown in magenta/green respectively on the interface
            # ("Interactif"), at the user's explicit request. Mutually
            # exclusive by construction (`placed_from` is set at most
            # once, by whichever tier succeeds first).
            "from_theme": placed_from == "theme",
            "from_challenge": placed_from == "challenge",
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


def interactive_boundary_candidates(grid, rows, cols, index, cells, side, priority_words=None):
    """"Début"/"Fin" buttons (mode "Interactif"), next to "Croisés", at the
    user's explicit request: "à côté du bouton Croisés, ajouter un bouton
    'Début' qui liste le mots pouvant commencer l'emplacement sélectionné en
    tenant compte des lettres posées, même si il ne fait pas la longueur
    totale de l'emplacement. Ne pas lister les mots qui écraseraient une
    lettre existante avec une autre lettre ou une case noire obligatoire
    pour terminer le mot... ajouter un bouton 'Fin' qui fait la même chose
    pour lister les mots qui peuvent terminer l'emplacement. Classer les
    mots proposés par ordre de longueur, puis alphabétique."

    Unlike `interactive_slot_candidates` (exact slot length only), this
    tries every length from 2 up to `len(cells)`, keeping only the `side`
    ("start" or "end") portion of `cells` for each length — the first
    `length` cells for "start", the last `length` cells for "end" — so a
    shorter word can be proposed even while the rest of the slot stays
    unresolved. A candidate shorter than the whole slot additionally
    requires the single boundary cell right beyond it (the one that would
    have to turn black to terminate the word there) to be actually free to
    do so: not already carrying a letter (never overwrite one, whether it's
    the player's own or a crossing word's), and structurally valid at the
    bare `min_interior_free=1` floor (the real correctness limit — no
    orphaned cell, no disconnection — never the aesthetic 8-cell minimum
    `_place_black_cells` itself enforces, which never applies to a manual
    edit). The full-length case (`length == len(cells)`) needs no such
    check, same as `interactive_slot_candidates`.

    Returns `(theme_words, other_words)` exactly like `interactive_slot_
    candidates`, both sorted by `(length, word)` (length first, then
    alphabetically) rather than plain alphabetical order, and mixing every
    accepted length together — the word's own length already tells the
    caller how many of `cells` (from `side`'s end) it covers, no separate
    field needed. `INTERACTIVE_SLOT_CANDIDATES_LIMIT` is applied PER
    length rather than once over the combined pool: an empty/lightly-
    constrained slot can easily have hundreds of 2- or 3-letter matches,
    which would otherwise fill the entire cap on their own and silently
    hide every longer (more specific, usually more useful) length behind
    them — including the full-length matches `interactive_slot_candidates`
    itself would have shown. A word already used elsewhere in the grid is
    excluded, like `interactive_slot_candidates`/`interactive_crossing_
    words`."""
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
    full_length = len(cells)
    theme_words = []
    other_words = []
    for length in range(2, full_length + 1):
        sub_cells = cells[:length] if side == "start" else cells[-length:]
        if length < full_length:
            boundary_cell = cells[length] if side == "start" else cells[-(length + 1)]
            if boundary_cell in known:
                continue
            br, bc = boundary_cell
            pattern[br][bc] = BLACK
            valid = is_structurally_valid(pattern, rows, cols, min_interior_free=1)
            pattern[br][bc] = WHITE
            if not valid:
                continue
        sub_known = {cell: known[cell] for cell in sub_cells if cell in known}
        candidates = set(_slot_candidates(index, length, sub_cells, sub_known)) - used_words
        themed = _priority_words_for(priority_words, sub_cells) & candidates
        other = candidates - themed
        theme_words.extend(sorted(themed))
        other_words.extend(sorted(other)[:INTERACTIVE_SLOT_CANDIDATES_LIMIT])
    return theme_words, other_words


def interactive_clean_impossible_zones(grid, rows, cols, index, rng, challenge_words=None):
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
    itself.

    `challenge_words` (`None`/empty by default), in grid form: a "Mots
    Défi" word is never "impossible" here either, at the user's explicit
    request — same exemption as `_interactive_fill_diagnostics`
    (`_challenge_fillable_slot_indices` for a still-open slot, `_challenge_
    word_cells` folded into `_invalid_fully_known_indices`'s own `exempt`
    for an already-typed one) — so "Nettoyer" never strips out a validly
    placed challenge word thinking it's an invented, dictionary-less
    invention."""
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
    challenge_fillable = _challenge_fillable_slot_indices(slots, known, challenge_words)
    challenge_exempt = _challenge_word_cells(slots, known, challenge_words)
    impossible = sorted(
        (set(_impossible_indices(slots, index, known)) - challenge_fillable)
        | set(_invalid_fully_known_indices(slots, index, known, exempt=challenge_exempt))
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


def interactive_minimize_black_cells(grid, rows, cols, index, rng, challenge_words=None):
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
    removing a cell must never MAKE THINGS WORSE. One rule is absolute,
    never weighed against anything else: a black cell touching a letter
    the player has already placed (any of its up/down/left/right
    neighbors) is an extremity of a word already "posé" (placed) and is
    NEVER removed, whatever the dictionary might say about the longer
    slot the removal would create — the whole point of "Nettoyer
    (+noires)" is to drop cells that aren't doing any real job, never to
    reopen a word the player already finished (`_touches_placed_letter`).
    On top of that: the grid must stay structurally valid
    (`is_structurally_valid`, the absolute connectivity/orphaned-cell
    invariant already used everywhere else in this file), and neither
    slot now running through the freed cell (the merged/extended
    horizontal and vertical slot at that row/column) may be among the
    slots deemed impossible (the same combined criterion as
    `_interactive_fill_diagnostics`/`interactive_clean_impossible_zones`:
    `_impossible_indices` ∪ `_invalid_fully_known_indices`) — this second
    check only ever matters when both sides of the freed cell are still
    empty (no letter to trigger the rule above), and catches the case
    where merging two open slots produces a length the dictionary can't
    fill at all. Scoped to only the two slots the freed cell itself
    belongs to: no other slot's own cells or known letters ever change
    from flipping one black cell, so no other slot's impossible/invalid
    status can change either — comparing a whole-grid total instead once
    let an unrelated slot's own, unconnected improvement from the very
    same flip numerically offset a newly broken merge, silently accepting
    it (the original bug here, before the extremity rule above was added:
    a black cell touching a real, already-placed word — e.g. "VIRANT",
    "TRANS" — got removed anyway because some other, real dictionary word
    happened to fit the longer slot the removal created, which the old
    per-slot dictionary check alone could never rule out; the player's
    own placed word carries no such veto power on its own).

    Returns `{"changed": bool, "grid": <updated or identical grid>,
    "removed_count": <number of black cells actually removed>}`.

    `challenge_words` (`None`/empty by default), in grid form: excluded
    from this "impossible" count the same way as `interactive_clean_
    impossible_zones`/`_interactive_fill_diagnostics`, at the user's
    explicit request — a "Mots Défi" word is part of the dictionary for
    this purpose, so it never counts against a removal, and can never
    itself get merged/blackened away by this pass either."""
    black_cells = [(r, c) for r in range(rows) for c in range(cols) if grid[r][c] == BLACK]
    if not black_cells:
        return {"changed": False, "grid": grid, "removed_count": 0}

    def _touches_placed_letter(g, r, c):
        for nr, nc in ((r - 1, c), (r + 1, c), (r, c - 1), (r, c + 1)):
            if 0 <= nr < rows and 0 <= nc < cols and g[nr][nc] not in (BLACK, WHITE):
                return True
        return False

    def _bad_slot_indices(g):
        pattern = [["#" if ch == BLACK else "." for ch in row] for row in g]
        slots = extract_slots(pattern, rows, cols)
        if not slots:
            return slots, set()
        known = {
            (r, c): g[r][c]
            for r in range(rows)
            for c in range(cols)
            if g[r][c] not in (BLACK, WHITE)
        }
        challenge_fillable = _challenge_fillable_slot_indices(slots, known, challenge_words)
        challenge_exempt = _challenge_word_cells(slots, known, challenge_words)
        bad = (
            (set(_impossible_indices(slots, index, known)) - challenge_fillable)
            | set(_invalid_fully_known_indices(slots, index, known, exempt=challenge_exempt))
        )
        return slots, bad

    rng.shuffle(black_cells)
    working = [row[:] for row in grid]
    removed_count = 0
    for (r, c) in black_cells:
        if _touches_placed_letter(working, r, c):
            continue
        working[r][c] = WHITE
        # `is_structurally_valid` only understands a plain black/white
        # PATTERN (BLACK/WHITE cells only) — calling it directly on
        # `working` (which still carries real letters for every
        # already-filled cell) makes its row/col run-length scan misread
        # every letter as an obstacle, exactly like a black cell, so it
        # rejected almost any removal next to already-typed content — a
        # real bug, reported directly by the user: "Nettoyer (+noires) ne
        # supprime pas les cases noires isolées." Fixed by converting to
        # the same pattern-only view `_bad_slot_indices` above already
        # builds for its own purpose, reused here for the structural check
        # too.
        pattern_check = [["#" if ch == BLACK else "." for ch in row] for row in working]
        if not is_structurally_valid(pattern_check, rows, cols, min_interior_free=1):
            working[r][c] = BLACK
            continue
        slots, bad = _bad_slot_indices(working)
        if any((r, c) in slots[j] for j in bad):
            working[r][c] = BLACK
            continue
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
        # `word` can be `None` here — a slot `try_fill` left unresolved
        # under its own `required_cells`-relaxed completeness rule (see
        # its docstring, "Finir la zone"). Its own cells, if they carry a
        # real letter at all, get it from whichever CROSSING slot was
        # actually assigned instead — nothing to write for this one.
        if word is None:
            continue
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


def _challenge_word_cells_from_assignment(slots, assignment, challenge_words):
    """Cells of every slot whose assigned word is one of `challenge_words`
    — the `(slots, assignment)` counterpart of `_challenge_word_cells`
    (which reads from a `{cell: letter}` map instead), at the user's
    explicit request: "In the preview grids, show 'Mots Défi' words in
    green, like on the grid in Interactive mode." Mirrors `_theme_word_
    cells` exactly, just tested against `challenge_words` instead of the
    theme glossary. `assignment` can contain `None` entries (slot not
    reached yet) — simply ignored."""
    if not challenge_words:
        return []
    out = set()
    for cells, word in zip(slots, assignment):
        if word is not None and word in challenge_words:
            out.update((r, c) for (r, c) in cells)
    return sorted(out)


def _challenge_cells_from_preview_state(seed_grid, rows, cols, locked_letters,
                                         preseed_assignment, challenge_words):
    """`_challenge_word_cells_from_assignment` for a cycle-START preview —
    the `challenge_words` counterpart of `_theme_cells_from_preview_state`,
    same two resume-state shapes handled the same way."""
    if not challenge_words or seed_grid is None:
        return []
    slots = extract_slots(seed_grid, rows, cols)
    if preseed_assignment is not None:
        return _challenge_word_cells_from_assignment(slots, preseed_assignment, challenge_words)
    if locked_letters:
        assignment = [
            "".join(locked_letters[c] for c in cells)
            if all(c in locked_letters for c in cells) else None
            for cells in slots
        ]
        return _challenge_word_cells_from_assignment(slots, assignment, challenge_words)
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
    """Cells where **no letter** satisfies both the horizontal slot and the
    vertical slot crossing there — each of the two slots' own raw domain
    can very well be non-empty on its own (so neither one is flagged by
    the red `.impossible` highlight, a fully empty domain), but if none of
    their respective real *playable* words share the same letter at that
    exact cell, it can never actually be filled in practice.

    "Really playable", for a *partially* locked slot (the same restriction
    as `_low_candidate_slot_cells` above — a fully locked slot is already
    a confirmed word, a fully blank slot is out of scope for this
    function), means a dictionary candidate (`_slot_candidates`) that is
    neither:
    - already used elsewhere in this same grid (any other fully locked
      slot, `used_words`, computed here directly from `locked_letters` —
      a word already placed can't be placed again);
    - below `NOISE_FREQUENCY_THRESHOLD` in raw frequency (`index[length]
      ["freq"]`, see `build_index`) — see that constant's own docstring
      for its calibration.

    A cell crossed by only ONE still-open slot (the other direction is
    already fully locked there, or doesn't even form a real slot at all —
    a single-cell zone) is a degenerate case of the same rule: it's
    flagged if and only if that single slot, on its own, has no playable
    word left at all.

    Concrete case that motivated this feature, see CLAUDE.md: a grid stuck
    for 11 consecutive paliers on 3 cells, one of which (the intersection
    between a horizontal slot `_S_` and a vertical slot `ER_`) was in fact
    entirely blocked by this exact criterion — each slot taken in
    isolation genuinely did have playable words (`OST`/`PST`/... on one
    side, `ERG`/`ERS`/`ERE` on the other), but no letter common to both
    sets at their shared cell; a first draft of this function, which only
    ever checked each slot in isolation (without the crossing), therefore
    reported nothing at all on this grid — confirmed by replaying it
    directly against that same generation's real history.

    Purely diagnostic, on the "Génération du motif de cases noires"
    preview (the `pattern` event), at the user's explicit request — same
    scope as `_low_candidate_slot_cells`, never computed for a "reprise
    telle quelle" palier (see its own caller in `generate_grid`)."""
    if not locked_letters:
        return []
    all_slots = extract_slots(grid, rows, cols)
    used_words = {
        "".join(locked_letters[cell] for cell in slot)
        for slot in all_slots
        if all(cell in locked_letters for cell in slot)
    }
    # Playable words per partially locked slot (None for every other
    # slot — fully locked or fully blank — out of scope for this
    # function, see the docstring).
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
    # For every still-free cell, the open slots (in the above sense)
    # crossing through it, with its exact position in each — just 1 for a
    # cell bordered on one side by locked content/black, 2 for a genuine
    # horizontal/vertical crossing.
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


# Added to a "Mots Défi" word's own length before squaring it into
# `_content_score`'s sum, at the user's explicit request: "Inclus les
# Mots Défis dans le calcul (avec ou sans glossaire thématique) en
# attribuant un bonus +2 sur leur longueur." Applies on top of whatever
# else already makes a word eligible for the sum (see `_content_score`'s
# own docstring) — a challenge word that also happens to be a theme word
# is not counted twice, just scored with the bonus once.
CHALLENGE_WORD_SCORE_BONUS = 2


def _content_score(pairs, priority_words=None, challenge_words=None):
    """Sum of squares of each placed word's own "scored length", over
    `pairs` (an iterable of `(word, cells)`, `word` possibly `None` for an
    unassigned slot — ignored). This is the ONE formula shared by every
    content-scoring use in `generate_grid`: breaking ties among several
    *successful* attempts of the same palier, and picking a *failed*
    palier's own "best" attempt (raw state via `_playable_score`, post-
    cleanup state via `_cleaned_playable_score`) to carry forward — at the
    user's explicit request: "Le scoring réussi et échoué doivent utiliser
    la même logique qui favorise le glossaire thématique." Before this,
    only the successful-attempt tie-break restricted itself to theme
    words; the failed-attempt scores summed every placed word regardless
    of theme, an inconsistency this function removes by being the only
    place either kind of score is actually computed.

    `priority_words` (`None`/empty by default — no effect for any pre-
    existing caller before theming existed): when non-empty, a word only
    counts toward the sum at all if it belongs to ITS OWN slot's theme
    glossary (`_priority_words_for(priority_words, cells)`, handling a
    bilingual grid's per-direction `DualSet` the same way every other
    theme-aware check in this file already does) — so a themed generation
    favors the attempt that surfaces more/longer theme words, not merely
    more/longer words overall. Empty/`None`: every placed word counts, at
    its own plain length — unchanged from before theming existed.

    `challenge_words` (`None`/empty by default — no effect for any pre-
    existing caller before this feature existed): a "Mots Défi" word
    always counts toward the sum, REGARDLESS of `priority_words` — even
    one that isn't itself a theme word, and even with no theme at all —
    so a challenge word's own placement is never invisible to either
    score. Its own scored length additionally gets `CHALLENGE_WORD_SCORE_
    BONUS` added before squaring, so an attempt that manages to place a
    challenge word is favored over one that doesn't, all else equal."""
    total = 0
    for w, cells in pairs:
        if w is None:
            continue
        is_challenge = bool(challenge_words) and w in challenge_words
        if priority_words and not (
            is_challenge or w in _priority_words_for(priority_words, cells)
        ):
            continue
        scored_length = len(w) + (CHALLENGE_WORD_SCORE_BONUS if is_challenge else 0)
        total += scored_length ** 2
    return total


def _playable_score(grid, diag, rows, cols, priority_words=None, challenge_words=None):
    """Measures the amount of content genuinely placed and confirmed in
    `diag["assignment"]` — the square root of `_content_score` over every
    already-assigned word paired with its own slot (`None` ignored) — at
    the user's explicit request: "Au lieu d'un score sur les injouables,
    mesurer les jouables (racine carré des sommes des carrés des longueurs
    jouables)." Used by `generate_grid` to sort `failed_unique` when
    selecting a palier's own "best" failed attempt — see its own comment
    for the bias this criterion corrects (an earlier sort by "fewest
    impossible cells" wrongly favored a state published early in a still-
    barely-advanced search, where few placed words mechanically means few
    cells that could already be judged impossible).

    Same principle as the score `generate_grid` already uses to break ties
    among several *successful* attempts of the same palier — favoring a
    handful of long words over many short ones for the same total letter
    count, and, with `priority_words`/`challenge_words` given, the same
    theme/"Mots Défi" preference too (see `_content_score`) — with the
    square root added on top to bring this score back to a scale
    comparable to a plain length rather than a sum of squares. An
    assigned word's length is read directly via `len(word)` (never
    recomputed from the pattern): a word can only ever be assigned to a
    slot of its own length, so the two values are always rigorously
    equal. `slots` is recomputed from `grid`/`rows`/`cols` (the same
    pattern `diag["assignment"]` was itself built against) purely to pair
    each word with its own cells for `_content_score`'s theme/challenge
    lookup — `grid` is never otherwise read."""
    slots = extract_slots(grid, rows, cols)
    if len(slots) != len(diag["assignment"]):
        pairs = zip(diag["assignment"], [None] * len(diag["assignment"]))
    else:
        pairs = zip(diag["assignment"], slots)
    return _content_score(pairs, priority_words, challenge_words) ** 0.5


def _cleaned_playable_score(grid, diag, rows, cols, index, rng,
                             priority_words=None, challenge_words=None):
    """Like `_playable_score`, but on the state AFTER cleanup — the
    content that would genuinely survive once removed one at a time, as
    needed to resolve every impossible situation in `diag["impossible_
    slots"]` (`_clean_blocked_slots`, see its own docstring for the "one
    word at a time" algorithm now used) — rather than on the raw
    `diag["assignment"]`, at the user's explicit request: "Il faut
    montrer les emplacements avant nettoyage, évaluer la grille après
    nettoyage (qui sera transmise au cycle suivant si sélectionnée)."
    `index`/`rng` passed straight through to `_clean_blocked_slots` — the
    same random generator already shared by the whole `generate_grid()`
    call, so this score stays reproducible from the same seed instead of
    introducing a second, independent source of randomness. `priority_
    words`/`challenge_words` passed straight through to `_content_score`
    — see `_playable_score`'s own docstring for why this now matters here
    too, on the same footing as the successful-attempt tie-break.

    Used to sort `failed_unique`/choose `failed_pairs[0]` — the winning
    attempt is therefore now the one that keeps the most genuinely placed
    content once cleaned, not the one that, before any cleanup, has the
    fewest impossible cells or the most raw content: two attempts with the
    same number of raw impossible cells can lose very different amounts
    of content once cleaned (an attempt whose word crossing the impossible
    slot is short loses less than one whose crossing word is long), and
    it's exactly this *post*-cleanup quantity that determines what will
    actually be carried forward to the next palier if this attempt is
    chosen — so that's what needs evaluating, not the raw state.

    Recomputes `slots` directly from the real black/white pattern `grid`
    (never from an `example_grid` with letters overlaid, which would
    throw off `extract_slots`) — each attempt has its own pattern and its
    own assignment, nothing to share between them. Falls back to
    `_playable_score(grid, diag, rows, cols, priority_words, challenge_
    words)` (the raw state) if `slots`'s length doesn't match `diag[
    "assignment"]`'s — should never happen in real use, a safety net
    rather than an expected case."""
    slots = extract_slots(grid, rows, cols)
    if len(slots) != len(diag["assignment"]):
        return _playable_score(grid, diag, rows, cols, priority_words, challenge_words)
    cleaned_assignment, _, _ = _clean_blocked_slots(
        slots, diag["assignment"], diag["impossible_slots"], index=index, rng=rng,
    )
    return _content_score(
        zip(cleaned_assignment, slots), priority_words, challenge_words,
    ) ** 0.5


def _public_diag(diag):
    """A copy of `diag` safe to spread into a `progress(...)` event — a
    generic safety net against some future diagnostic field that isn't
    JSON-safe as-is (e.g. a dict indexed by cell `(row, col)`, a tuple used
    as a dict *key*), rather than a filter for one specific field today.

    Root-caused live, the first (and so far only) time this problem
    actually occurred: a real `GET /api/generate/status/{job_id}` came
    back 500 (the web UI then showed a "JSON.parse: unexpected
    character..." error, since the response body was no longer valid
    JSON) — `backend.log` showed `TypeError: cannot use 'list' as a dict
    key` right in the middle of `fastapi.encoders.jsonable_encoder`. The
    offending field at the time, `own_locked_letters` (see `_pattern_
    attempt`'s own history), encoded every cell as a dict key —
    `jsonable_encoder` recursively encodes each key to make it JSON-safe,
    which turns a tuple into a list, then tries to use that as a key of a
    plain Python dict to build the encoded result — a list not being
    hashable, that raises this same `TypeError`. Every other `diag` field
    holding cells (`locked_cells`, `impossible_cells`, `forced_cells`...)
    carries them as elements of a plain list, never as dict keys — none of
    them run into this problem. `own_locked_letters` itself has since been
    removed entirely (its one and only reader, `_preview_locked_source`,
    disappeared along with the late-preview mechanism it fed) — this
    function is nonetheless kept in place, deliberately, as a safeguard
    against the same bug class should some future diagnostic field ever
    take a similar shape."""
    return {k: v for k, v in diag.items() if k != "own_locked_letters"}


# Probability of trying a black cell instead of removing a crossing word,
# in `_clean_blocked_slots`'s own "one at a time" loop below — at the
# user's explicit request, restricted to "reprise telle quelle" only (see
# `generate_grid`, branch `if still_has_hope:`), never to a full nettoyage
# (`_build_retry_seed`, which already regenerates a brand-new pattern via
# `make_pattern` and so can already add black cells through that route):
# "En l'état, nettoyer les zones impossibles et les connectés, supprime
# beaucoup de mots, ce qui oblige plus tard à rajouter des cases noires
# par d'autres mécanismes. Autant tenter la case noire tout de suite, et
# supprimer moins de mots. Par ailleurs, sur des toutes petites zones, la
# suppression de mots ne supprime pas grand chose, et la recherche tourne
# en rond sur très peu de lettres modifiables. Ajouter des noires peut
# permettre de réellement finir ces petites zones où la vraie solution
# n'existe peut-être pas."
# Lowered from 1/3 to 1/10 right after, at the user's explicit request
# ("trop de cases noires à 1/3") — same mechanism, value revised downward
# after a first real use judged too aggressive.
BLACK_CELL_INSTEAD_OF_REMOVAL_PROBABILITY = 1 / 10


def _impossible_indices(slots_list, index, known):
    """Indices of the slots of `slots_list` deemed impossible in the local
    sense used by `_shorten_impossible_zones` below: not entirely covered
    by `known`, and with no real candidate once this constraint is applied
    (partial, or entirely absent — a length the dictionary doesn't cover
    at all is just as blocked as an invalid letter combination, the same
    criterion as the "zone strictement sans issue" of `_clean_blocked_
    slots`) (`_slot_candidates` empty). A slot entirely covered by `known`
    is an already-confirmed word — whatever its eventual validity may be
    is a completely different question, already handled elsewhere in this
    file (see the AVALAS bug in CLAUDE.md). Same criterion used at both
    places where `_shorten_impossible_zones` needs it: to redetect still-
    blocked slots after every black cell placed, and to compute the final
    list returned to the caller."""
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
    """Indices of the slots in `slots_list` entirely covered by `known` —
    every one of their cells fixed, directly or indirectly, by a crossing
    word — but whose letter combination matches NO real dictionary word.
    `_impossible_indices` deliberately ignores this case (see its own
    docstring: "whatever its eventual validity may be is a completely
    different question, already handled elsewhere in this file") — a
    design choice that's valid for its own original callers (`_pattern_
    attempt`/`_pattern_continue`, which already separately validate any
    fully locked slot, see `locked_impossible_slots`), but `_shorten_
    impossible_zones` below, by placing new letters that can end up fully
    covering a crossing slot without ever querying the dictionary for
    that exact combination, had nowhere else to perform this same check —
    a real bug observed live: an invented word ("ATEIRS", "TENLES"...),
    never genuinely chosen by anyone, simply recomposed as-is from
    individually correct letters that were never checked together.

    `exempt` (`None` by default — no effect for any pre-existing caller
    before this feature): a dict/set of cells — typically `permanent_
    locked_letters`, see `generate_grid`'s own docstring — for which a
    fully covered slot is NEVER flagged here, whatever its real validity
    against the dictionary, at the user's explicit request: "Les lettres
    posées en mode interactif sont à considérer comme bonnes, même si un
    emplacement contient un mot impossible (probablement un nom propre
    voulu par l'utilisateur)... ne doivent pas être remis en cause par la
    génération de la grille 'Finir la grille'." Only "Interactif" mode
    (Impossibles/Vérifier) still needs to flag such a word to the player —
    this function is never called with `exempt` there; only its callers
    internal to `generate_grid` (`_shorten_impossible_zones`/`_lengthen_
    impossible_zones`/`_optimize_before_cleanup`/`_clean_continue_
    candidate`) do."""
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


def _challenge_word_cells(slots_list, known, challenge_words):
    """Cells of every slot in `slots_list` fully covered by `known` whose
    spelled-out word is one of `challenge_words` — meant to be folded into
    the `exempt` set passed to `_invalid_fully_known_indices`, exactly
    like `permanent_locked_letters` already is: a "Mots Défi" word placed
    by `Filler._backtrack` (see its own `self.challenge_words`) is trusted
    at face value, same as a letter locked in Interactive mode, and must
    never be silently wiped out by one of generate_grid's own cleanup
    passes just because it isn't a real dictionary entry."""
    if not challenge_words:
        return frozenset()
    cells = set()
    for slot_cells in slots_list:
        if all(c in known for c in slot_cells):
            word = "".join(known[c] for c in slot_cells)
            if word in challenge_words:
                cells.update(slot_cells)
    return cells


def _challenge_fillable_slot_indices(slots_list, known, challenge_words):
    """Indices of the still-OPEN (not entirely covered by `known`) slots
    of `slots_list` that an unused "Mots Défi" word could still legally
    fill — used to exempt such a slot from being reported "impossible" in
    Interactive mode (`_interactive_fill_diagnostics`, `interactive_clean_
    impossible_zones`, `interactive_minimize_black_cells`) purely because
    the real dictionary has nothing left for it, at the user's explicit
    request: "Les Mots Défi doivent être considérés comme faisant partie
    du dictionnaire" — a slot with no real dictionary candidate left is
    not a genuine dead end as long as a challenge word can still legally
    go there, mirroring `Filler._backtrack`'s own crossing-safety
    exemption (`self.challenge_words`) during automatic placement. Purely
    geometric (length + already-known letters), like `Filler.
    _challenge_word_fits` — a challenge word is trusted at face value,
    never required to be a real dictionary entry. A challenge word
    already spelled out in full by some OTHER slot is excluded first
    (mirrors `Filler.used_words`), so it can't also exempt a second,
    different open slot."""
    if not challenge_words:
        return set()
    used = {
        "".join(known[c] for c in cells)
        for cells in slots_list
        if all(c in known for c in cells)
    }
    available = challenge_words - used
    if not available:
        return set()
    out = set()
    for i, cells in enumerate(slots_list):
        if all(c in known for c in cells):
            continue
        length = len(cells)
        for w in available:
            if len(w) != length:
                continue
            if all(w[pos] == known[c] for pos, c in enumerate(cells) if c in known):
                out.add(i)
                break
    return out


def _new_crossing_impossibility(cur_slots, cell_to_slots, own_idx, sub, word, known, index):
    """True if placing `word` on `sub` (once `known` is updated with its
    letters) would make a CROSSING slot impossible — another slot of
    `cur_slots`, in the other direction, sharing one of `sub`'s cells —
    that wasn't already impossible before this exact placement. A
    crossing slot already without a candidate BEFORE this placement is
    never counted here (it wasn't "created" by this word, it already was,
    for some completely different reason) — only a NEW degradation,
    directly caused by this exact letter, should reject the candidate.
    Same per-position intersection mechanics as `_impossible_indices`/
    `Filler._domain` (`_slot_candidates`), applied here before/after a
    single letter added rather than on the final state."""
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
    """For ONE slot deemed impossible (`cells`, index `own_idx` in
    `cur_slots`), looks for a shorter word to place at the head or tail of
    the zone, leaving at least one empty cell on the other side — so a
    maximum length of `len(cells) - 1`, never the slot's own full length,
    minimum `3` (never `2` or below — at the user's explicit request: "ne
    pas tester un remplissage partiel de moins de 2 lettres... comprendre
    : au moins 3 lettres"). FIRST collects every valid candidate, across
    every length and side at once — a candidate is only kept if its
    boundary cell (the one separating the word from the remaining empty
    cell) stays structurally valid once blackened (`is_structurally_valid
    (min_interior_free=1)`, never a bypass of this absolute invariant),
    that the remaining piece (the other side of the zone, once the
    boundary cell is blackened) isn't already covered by an invalid letter
    combination (see below), that the word itself adds at least one
    genuinely new letter (a cell that wasn't already known — placing a
    "word" whose every letter was already acquired brings no progress at
    all, only a black cell), and that the word isn't already used
    elsewhere in the grid (`used_words`) — then draws at random from this
    whole set (the length itself varies, not just the side at an equal
    length, nor a "longest first" order).

    For each candidate drawn this way, checks it doesn't create a new
    impossible crossing slot (`_new_crossing_impossibility`) before
    keeping it — if it does, that candidate is discarded and another is
    drawn, until the set is exhausted. Returns `(word, word_cells,
    boundary_cell)` of the first candidate passing all these checks, or
    `None` if none fit — in which case this slot isn't touched at all,
    neither black cell nor word placed.

    A boundary cell already covered by a confirmed letter (`known`) is
    never a candidate — blackening it would destroy the crossing word
    that already fixes it there, leaving its other cells assigned to a
    fragment that may no longer be a real dictionary word (a real bug
    observed live: "génère des mots qui n'existent pas... ne pas poser de
    case noire sur une case qui contient déjà une lettre").

    Both resulting pieces of the original zone — the placed word itself,
    and whatever leftover exists on the other side of the boundary cell —
    are each checked if they turn out fully determined: the placed word
    always is, by construction (validated against the dictionary before
    being kept as a candidate); the leftover, if it has at least two cells
    (otherwise it isn't a real slot at all, it will never itself be tested
    as a word) and is already fully covered by `known` (via other
    crossings, independently of this specific placement), must also match
    a real word — at the user's explicit request: "bien tester les 2
    parties de l'emplacement initialement vide, si les 2 parties sont
    complètes. Si un des deux morceaux complets n'est pas un mot valide,
    ne pas faire le remplacement partiel." If this leftover is fully
    determined but matches no real word, the whole combination (length,
    side) is discarded outright — without even looking for a word for
    `sub`, since the leftover's own validity doesn't depend on the chosen
    word."""
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
    """Black cells that bound, or are sandwiched by, a slot already fully
    determined by `known` — never to be removed/moved, on pain of
    breaking an already-acquired word. Reuses the exact same double
    criterion already established and thoroughly tuned for `_build_retry_
    seed`'s own step 3 (see its docstring for the full history of
    versions tried): (1) it directly bounds an entirely known slot —
    immediately before its first cell or after its last, in that slot's
    own direction; (2) it has a known letter on BOTH sides at once of the
    same axis (top AND bottom, or left AND right — never both axes
    together needed) — removing it would merge two distinct slots into
    one that may match no real word, disturbing both sides at once. Used
    by `_find_longer_word_for_zone`/`_lengthen_impossible_zones` to decide
    which black cell bounding an impossible zone can be safely removed/
    moved — the same "bounds an already-placed word" notion as `_build_
    retry_seed`, just recomputed here from `cur_slots`/`known` (a single
    round's own state within the cleanup loop) rather than from a global
    `assignment`."""
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
    """Like `_new_crossing_impossibility`, but for the `boundary` cell
    itself — black up to now, so absent from `cell_to_slots` (computed on
    the old pattern, where this cell belonged to no slot at all). Making
    it white can give rise, in the direction PERPENDICULAR to `own_dr`/
    `own_dc` (the zone's own direction, the one being lengthened), to a
    brand-new crossing slot that `_new_crossing_impossibility` can't see —
    this check recomputes that perpendicular run directly (the run of
    consecutive white cells on either side of `boundary`, up to a black
    cell or the edge), exactly the same principle as `_new_crossing_
    impossibility`, applied to this one cell it can't reach. Returns
    `False` if the perpendicular slot found this way has fewer than 2
    cells (not a real slot) or already had no candidate BEFORE `letter` was
    even added (so not a NEW degradation caused by this exact
    placement)."""
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
    """For ONE slot deemed impossible (`cells`, index `own_idx` in
    `cur_slots`), at the user's explicit request: "si un emplacement ne
    trouve pas de mot... mais qu'au moins une des cases noires limitant la
    zone peut être supprimée ou déplacée (parce qu'il y a de la place
    avant ou après, et que cette case noire n'est pas une limite d'un mot
    déjà posé), tester des longueurs différentes en supprimant ou
    déplaçant la case noire." The exact complement of `_find_shorter_word_
    for_zone` (which SHORTENS the zone by adding a black cell inside it):
    this one LENGTHENS it by pushing back one of its TWO existing bounding
    black cells (head or tail, never both at once in a single call) —
    either by moving it a few cells further out (a new black cell further
    in the same direction), or by removing it outright once the next
    natural obstacle (another black cell, or the grid edge) already
    suffices to bound the lengthened zone.

    A bounding cell is only even a candidate if (1) it's genuinely black
    and inside the grid — otherwise the zone already touches the edge on
    that side, nothing to push back — and (2) it doesn't appear in
    `protected` (see `_known_slot_boundary_cells`) — a cell already
    bounding a different, genuinely placed word must never be touched.
    Beyond that, the available room on that side (the run of consecutive
    white cells immediately after this bounding cell, up to the next
    black cell or the edge) determines how many different lengths are
    tried: lengthening by 1 cell (the bounding cell itself joins the zone,
    a new black cell is placed right after it), by 2, ..., all the way up
    to absorbing the entire available room (no new black cell placed at
    all, the next obstacle already bounds the lengthened zone).

    A black cell candidate for this move is never placed on an already-
    known cell (`known`) — blackening it would destroy the crossing word
    that already fixes it there — and must itself stay structurally valid
    (`is_structurally_valid(min_interior_free=1)`, the same absolute
    invariant used everywhere else in this file for adding a black cell);
    no such check is needed when the next obstacle is already in place
    (nothing new is added — and REMOVING a black cell can never violate
    this invariant, which only concerns additions).

    First collects every valid candidate — both sides, every possible
    lengthening amount, every real word matching it given the letters
    already known on the lengthened zone — then draws at random from this
    whole set. For each candidate drawn this way, checks it doesn't create
    a new impossible situation: `_new_crossing_impossibility` for cells
    that already belonged to a slot before this lengthening, and
    `_new_boundary_crossing_impossible` for the bounding cell itself
    (black up to now, so absent from `cell_to_slots`) — a new
    perpendicular slot can arise right at this exact spot once this cell
    is made white. Returns `(word, word_cells, old_boundary_cell,
    new_boundary_cell_or_None)` of the first candidate passing all these
    checks, or `None` if no side/length fits."""
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
                # Validate the REAL state once this candidate is applied — the
                # old bounding cell turns back white AT THE SAME TIME the new
                # one turns black, never one without the other: testing them
                # separately (e.g. the new black cell placed while the old one
                # is still black too) would evaluate a hypothetical state that
                # will never actually be committed — a bug found live: the two
                # adjacent black cells then split the grid into two
                # disconnected components, a false connectivity failure that
                # doesn't exist in the state actually being targeted.
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
    """Sorts a list of preview examples (see `generate_grid`'s own
    `examples=[...]` lists) by ascending process number (1..N), at the
    user's explicit request: "Afficher les prévisualisations toujours dans
    l'ordre des process." A stable display order, independent of the score
    that otherwise decides which one is the "best" (see the `is_best`
    field, added on each example before this sort, never recomputed
    afterward — it's this sort that must adapt to the selection already
    made, not the other way around). An example with no process number
    (the very first palier of a generation, before any real submission to
    a worker) is placed at the end of the list rather than making the sort
    fail — `None` is never comparable to an integer in Python."""
    return sorted(examples, key=lambda ex: (ex.get("process_number") is None, ex.get("process_number") or 0))


# On a large, heavily black grid, `_optimize_before_cleanup` (below) could
# try removing every one of its own unlocked black cells on EVERY distinct
# attempt of a palier — potentially several hundred cells, each costing a
# full `try_fill` — making this step, run on *every* cycle, very slow on
# this kind of grid. At the user's explicit request: "Ne faire
# l'optimisation complète que sur la grille finale [minimize_black_
# squares, qui garde son propre retrait exhaustif, inchangé]. Sur les
# optimisations à chaque cycle, au dessus de 50 cases noires,
# échantillonner 50 cases au hasard à optimiser." A single constant serves
# both requested roles — the threshold that triggers sampling AND the
# sample size itself share the same value (50).
PER_CYCLE_OPTIMIZATION_SAMPLE_SIZE = 50


def _optimize_before_cleanup(cand_grid, cand_diag, rows, cols, index, rng,
                              deadline_checks=6_000, cancel_event=None,
                              permanent_locked_letters=None, permanent_black_cells=None,
                              challenge_words=None):
    """A new step inserted BEFORE even `_shorten_impossible_zones`/
    `_clean_blocked_slots` (so before any cleanup at all), at the user's
    explicit request: "verrouiller tous les emplacements entièrement vides
    et les éventuelles cases noires avant/après ces emplacements vides
    [puis] lancer un cycle d'optimisation comme celui fait à toute fin
    [minimize_black_squares], qui ne doit pas toucher aux cases blanches
    ou noires verrouillées." Applied to EVERY distinct attempt of a failed
    palier (`failed_pairs`), not just the best one — at the user's
    explicit request, the same principle already established for
    `_clean_continue_candidate`/`_clean_all_candidates`.

    An "entirely empty" slot is one where NO cell carries a letter,
    whether from its own assignment or from a crossing (read directly
    from `cand_diag["example_grid"]`, never from `cand_diag["assignment"]`
    alone, which says nothing about letters brought in by a perpendicular
    slot) — a different category from an "impossible" slot (which can
    already carry some letters, just none that any real word satisfies
    all at once). Its cells, and the black cell(s) that immediately border
    it (before its first cell, after its last, in its own direction), are
    locked: never offered up for black-cell removal below, never required
    by the fill (`excluded_slots`, like a slot already known impossible —
    see `Filler.excluded_slots`) — they come out of this step exactly
    as-is.

    Slots already known to be impossible (`cand_diag["impossible_
    slots"]`) are also excluded from the fill required here (without
    this, the mere presence of an impossible slot anywhere else in the
    grid would make any `try_fill` fail outright, before ever attempting
    a single black-cell removal) — but their own bordering black cells
    are NOT locked: the ordinary cleanup that follows this step remains
    completely free to act on them, exactly as before this feature. If a
    black-cell removal nonetheless merges an impossible slot with a
    neighbor (a slot whose cells no longer exactly match either of the
    two original ones), this new merged slot is no longer excluded and
    the fill genuinely tries to solve it — a real black-cell removal can
    therefore sometimes unblock a slot that used to be impossible.

    Proceeds in two stages, both via the same partial fill (`preseed_
    assignment` locking in everything already confirmed, `excluded_slots`
    covering both the empty slots and the original impossible ones,
    recomputed on every call by cell matching to stay valid despite a
    possible slot-index shift):

    1. first tries to complete whatever can be completed elsewhere in the
       grid, without removing a single black cell — a search budget
       (`try_fill`) left unused by the original search (interrupted by
       `attempt_done_event`/`batch_abandoned_event`, or by its own budget)
       can buy real, free progress here;
    2. then, like `minimize_black_squares`, removes NON-locked black cells
       one at a time (shuffled order, until a full pass no longer improves
       anything), only keeping a removal if the grid stays structurally
       valid (`min_interior_free=1`, the same absolute invariant as
       `minimize_black_squares`) and still fillable under these same
       conditions.

       Unlike `minimize_black_squares` (only ever run once, on the
       already-successful final grid), this step runs on *every* attempt
       of *every* palier — a real cost on a grid dense in black cells.
       Above `PER_CYCLE_OPTIMIZATION_SAMPLE_SIZE` (50) removal-candidate
       black cells, only a single random sample of 50 of them is tried per
       pass, rather than the whole set — the moment one of them is
       actually removed, the current sample is abandoned and a fresh draw
       of 50, recomputed against the grid's up-to-date state, immediately
       takes its place, at the user's explicit request. Below this
       threshold, behavior stays exhaustive, unchanged: every candidate
       cell of a given pass is tried before checking whether another pass
       is needed.

    Returns `(new_grid, new_diag)` — `new_diag` a copy of `cand_diag` with
    only `assignment`/`impossible_slots`/`example_grid` updated (everything
    else, `process_number` included, passed through unchanged) — ready to
    replace `(cand_grid, cand_diag)` wherever the ordinary cleanup was
    expecting them, with no caller needing to know this step's own
    details. `assignment`/`impossible_slots` are recomputed from scratch
    on the FINAL state (never reprojected from `cand_diag` by plain cell
    matching) — see the comment right before their computation, further
    down, for the real bug ("UNT") this recomputation fixes.

    `permanent_black_cells` (`None`/empty by default — no effect for any
    pre-existing caller before "Finir la zone"): a real bug reported live
    by the user — "le bouton 'Finir la zone' ne verrouille pas
    correctement les cases grisées, le remplissage automatique continue à
    essayer de les alimenter." This step removes black cells one at a time
    (see the `while improved` loop below) for every one not in `locked_
    black_cells`, with no knowledge at all of cells permanently frozen by
    "Finir la zone" (cells outside the selected zone, with no letter, so
    never covered by `permanent_locked_letters` nor by `locked_black_
    cells`, which only protects the boundaries of an empty slot) — such a
    frozen cell, if it doesn't otherwise bound any empty slot, was
    therefore a removal candidate like any other ordinary black cell,
    reopening it and letting the fill assign it a letter. Now excluded
    from `removable` the same way as `locked_black_cells`."""
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
        # Sampling above PER_CYCLE_OPTIMIZATION_SAMPLE_SIZE (see the
        # docstring above and the constant's own comment): `sampling`
        # distinguishes the two regimes — below the threshold, `break` is
        # never reached further down, exhaustive behavior unchanged; above
        # it, the first successful removal of the sample immediately
        # interrupts this pass (`break`) to draw a BRAND-NEW sample of 50,
        # recomputed against `grid`'s up-to-date state, on the very next
        # pass through `while improved`.
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
    # Recomputes impossible slots from the REAL state after optimization,
    # at the user's explicit request: "les mots pouvant changer pendant
    # l'optimisation, il est important que cette optimisation recalcule
    # les mots impossibles avant de passer la main au nettoyage." The old
    # version just reprojected the ORIGINAL `impossible_slots` list (by
    # cell matching, `tuple(cells) in impossible_cell_tuples`) without
    # ever revalidating whether a slot still listed as "impossible" had,
    # in the meantime, been fully recomposed — via `confirmed`, above —
    # purely by its own crossings (each individually valid) into a word
    # that itself exists in the dictionary for NO combination of those
    # exact letters. Real bug reported live by the user: an optimized grid
    # containing "UNT" (absent from the French dictionary), already
    # flagged "deemed impossible" before optimization — but whose
    # invalidity was no longer reflected anywhere once this function
    # finished, since `_clean_blocked_slots` (the cleanup that follows)
    # never removes a word already present on the impossible slot itself,
    # only the ones CROSSING it — so "UNT" stayed exactly as-is, locked
    # into the next palier. `_invalid_fully_known_indices` (a slot fully
    # covered by `confirmed` but whose letter combination matches no real
    # word — the same bug class already fixed once for `_shorten_
    # impossible_zones`, see its own docstring) wipes out this kind of
    # invented word before it's ever passed further along; `_impossible_
    # indices` (a slot not fully covered, with no real candidate once its
    # known letters are applied) captures the complementary case — a slot
    # still blocked, whether touched by optimization or not. The union of
    # the two, no longer just reprojecting the old list, is the correct
    # definition of "impossible" once this step is done — including the
    # case, already anticipated earlier in this docstring, where a
    # black-cell removal legitimately unblocks a slot that used to be
    # impossible: neither function flags it in that case, and it
    # naturally disappears from `final_impossible`.
    invalid_fully_known = set(
        _invalid_fully_known_indices(
            final_slots, index, confirmed,
            exempt=set(permanent_locked_letters or ())
            | _challenge_word_cells(final_slots, confirmed, challenge_words),
        )
    )
    for j in invalid_fully_known:
        final_assignment[j] = None
    # Same "Mots Défi" exemption as `interactive_clean_impossible_zones`/
    # `Filler.impossible_zone_slots` (see their own docstrings, at the
    # user's explicit request: "la détection des emplacements impossibles
    # doit tenir compte des Mots Défi, pour ne pas nettoyer là où un mot
    # défi pourrait être placé"): a still-open slot with no real
    # dictionary candidate left is not a genuine dead end as long as an
    # unused challenge word can still legally go there — `final_impossible`
    # feeds straight into the cleanup that follows (`_shorten_impossible_
    # zones`/`_clean_blocked_slots`), so never flagging such a slot here
    # is what keeps that cleanup from reshaping/blackening/emptying it.
    final_impossible = sorted(
        invalid_fully_known | (
            set(_impossible_indices(final_slots, index, confirmed))
            - _challenge_fillable_slot_indices(final_slots, confirmed, challenge_words)
        )
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
    # Fixed at the user's explicit request: "Pour fonctionner correctement,
    # l'optimisation doit déverrouiller toute la grille avant de verrouiller
    # les emplacements vides et les cases noires avant/après." Before this
    # fix, `new_diag` used to inherit — via `**cand_diag` right below —
    # the old `cand_diag["locked_cells"]` as-is, computed by the ORIGINAL
    # search from its own `locked_letters`/`preseed_assignment` (so
    # reflecting the PREVIOUS palier), never recomputed to reflect what
    # THIS optimization step actually locks — a genuine stale leftover,
    # never reset. `locked_cells` is now rebuilt entirely from scratch
    # ("unlock the whole grid" — starting from no inherited state at all)
    # then refilled with exactly what this function protects throughout:
    # the cells of every entirely empty slot (`empty_cell_tuples`,
    # flattened cell by cell) and their own bordering black cells
    # (`locked_black_cells`) — the only definition of "locked" that makes
    # sense for this specific step, independent of anything locked before
    # it. Both sets remain valid for the FINAL grid, not just the starting
    # one: a `locked_black_cells` cell can never be removed by the `while
    # improved` loop above (explicitly excluded from `removable`), and an
    # `empty_cell_tuples` cell stays what it was at the moment this step
    # started protecting its zone, whether it ended up receiving a real
    # letter in the meantime (via `_try_complete`) or not.
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
                               index, rng, permanent_locked_letters=None,
                               challenge_words=None):
    """A new step inserted BEFORE the ordinary blocked-slot cleanup
    (`_clean_blocked_slots` below), at the user's explicit request,
    reserved for "reprise telle quelle" (see `_clean_continue_candidate`)
    — never for a full nettoyage, which regenerates a brand-new pattern
    via `make_pattern` anyway and so can already add its own black cells
    through that route, exactly the same principle already established
    for `BLACK_CELL_INSTEAD_OF_REMOVAL_PROBABILITY` right above.

    For each slot in `impossible_slots`, tries to place a shorter word at
    the head or tail of the zone (see `_find_shorter_word_for_zone`, which
    draws at random among every valid candidate — across every length —
    and rejects any candidate that would create a new impossible crossing
    slot) rather than directly removing the words crossing it. A found
    word is placed, a black cell is added at the end that leaves an empty
    cell; a slot for which NO shorter word fits (either none exists, or
    each would create a new blockage elsewhere) isn't touched at all —
    no black cell, no word — and simply waits for the next round, or the
    ordinary cleanup if no further progress is possible anywhere. Once
    every slot of this round has been processed this way, blocked-slot
    detection is rerun on the updated grid (the new pattern may have
    resolved some slots, or shortened others that remain too constrained,
    or even revealed new ones via the crossing checks) — until no shorter
    word can be placed anywhere at all. The function then hands back
    control, with the list of slots still genuinely impossible at this
    point, so `_clean_blocked_slots` can take over with its own mechanism
    (word removal, or its black-cell alternative).

    The "impossible" criterion used here (`_impossible_indices`, a plain
    per-position candidate intersection) is deliberately simpler than the
    real CSP solver's own (`Filler.impossible_zone_slots`, which also
    accounts for `used_words`/`forced_letters` at the exact moment the
    search made the most progress) — this function operates AFTER the
    search, on a pattern that's going to be reshaped anyway, so
    rederiving this same criterion locally on every loop round (rather
    than relaunching a real `Filler`, far more costly) is enough and
    consistent with `_low_candidate_slot_cells`/`_noise_slot_cells`,
    which already make this same choice elsewhere in this file.

    Returns `(grid, slots, assignment, impossible_slots)` — unchanged, by
    reference, if no shorter word was ever placed (the common case),
    otherwise a new pattern/slots/words triple reflecting the state after
    this preliminary cleanup, with the list of still-impossible slots
    reindexed against the new pattern.

    The pattern, the crossing slots (`cell_to_slots`), and the already-
    used words are all recomputed fresh before EVERY slot processed — not
    just once per round — so that examining a slot always accounts for
    the word the previous slot just placed (a real bug observed live: a
    slot processed right after another, with a still-stale pattern
    snapshot, could accept a word actually already incompatible with what
    had just been placed). Every slot still to process is identified by
    its own cells (a tuple of coordinates), never by a numeric index into
    the slot list — an index would go stale the moment a black cell added
    elsewhere shifts the order/count of slots, the same index pitfall
    already encountered elsewhere in this file.

    `permanent_locked_letters` (`None` by default — no effect for any
    pre-existing caller before "Finir la grille"/"Finir la zone") is
    merged into `known` right from the start — a real bug reported live
    by the user: "'Finir la zone' semble correctement marquer les cases en
    vert, mais continue à placer des cases noires là où il y a du vert
    (donc sur des cases verrouillées, supposées intouchables)." Without
    this merge, `known` only ever reflected already fully ASSIGNED slots
    (`assignment`) — a cell locked by the user but where NEITHER of the
    two slots crossing it was yet fully resolved at this specific palier
    (e.g. a still partially filled slot) stayed absent from `known`, so
    never protected by `_find_shorter_word_for_zone`'s own `boundary in
    known` check: such a cell could then be chosen as a new boundary cell
    and blackened, overwriting a letter that was nonetheless supposed to
    stay final."""
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
                # This slot no longer exists as such (one of its own
                # cells was blackened/covered in the meantime) — no
                # normal case should ever reach this (see the `boundary
                # in known`/`grid[br][bc] == BLACK` guard above, which
                # explicitly protects every processed slot's own cells),
                # but this remains a defensive safeguard rather than a
                # crash.
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
        # Same "Mots Défi" exemption as everywhere else "impossible" is
        # computed (see `_challenge_fillable_slot_indices`'s own
        # docstring, at the user's explicit request): a slot an unused
        # challenge word could still legally fill is never fed back into
        # this shortening loop, which would otherwise reshape a zone that
        # isn't a genuine dead end.
        remaining_idx = (
            set(_impossible_indices(cur_slots, index, known))
            - _challenge_fillable_slot_indices(cur_slots, known, challenge_words)
        )
        remaining_cells = [tuple(cur_slots[j]) for j in remaining_idx]

    if not changed:
        return grid, slots, assignment, impossible_slots

    final_slots = extract_slots(new_grid, rows, cols)
    invalid_full = set(
        _invalid_fully_known_indices(
            final_slots, index, known,
            exempt=set(permanent_locked_letters or ())
            | _challenge_word_cells(final_slots, known, challenge_words),
        )
    )
    final_assignment = [
        "".join(known[c] for c in cells)
        if all(c in known for c in cells) and j not in invalid_full else None
        for j, cells in enumerate(final_slots)
    ]
    # Same exemption for the final list handed back to the caller (which
    # feeds `_lengthen_impossible_zones` then `_clean_blocked_slots`) —
    # see the comment on `remaining_idx` above.
    final_impossible = sorted(
        invalid_full | (
            set(_impossible_indices(final_slots, index, known))
            - _challenge_fillable_slot_indices(final_slots, known, challenge_words)
        )
    )
    return new_grid, final_slots, final_assignment, final_impossible


def _lengthen_impossible_zones(grid, rows, cols, slots, assignment, impossible_slots,
                                index, rng, permanent_locked_letters=None,
                                permanent_black_cells=None, challenge_words=None):
    """A new step, the exact complement of `_shorten_impossible_zones`
    above, at the user's explicit request: "si un emplacement ne trouve
    pas de mot dans le glossaire thématique (ou le glossaire normal si ce
    n'est pas une grille thématique, donc emplacement devenu impossible),
    mais qu'au moins une des cases noires limitant la zone peut être
    supprimée ou déplacée..., tester des longueurs différentes en
    supprimant ou déplaçant la case noire." The "impossible" used here is
    the same generic criterion already established for `_shorten_
    impossible_zones` (`_impossible_indices` — no real word, whatever its
    source, matches the letters already known): a themed generation never
    restricts the notion of "impossible" itself to the theme glossary
    alone — `priority_words` is only a try-order preference during the
    CSP fill (see `Filler._backtrack`), never a restriction on the
    dictionary actually queried here, exactly like `_find_shorter_word_
    for_zone`, which has likewise never had any knowledge of the theme.

    Called, inside `_clean_continue_candidate`, right AFTER `_shorten_
    impossible_zones` — on whatever is still impossible once shortening
    has already been tried — rather than before or instead of it: a
    deliberate but not explicitly requested ordering choice, the less
    risky of the two (never touches the already-established and verified
    shortening mechanism, only acts as a complement on what it couldn't
    resolve). Reserved for "reprise telle quelle", never for a full
    nettoyage — the same principle already established for `_shorten_
    impossible_zones` itself (see its own docstring): a full nettoyage
    regenerates an entirely new pattern via `make_pattern` anyway, which
    can already lengthen/shorten any zone by construction.

    Same round-based loop structure as `_shorten_impossible_zones` —
    `remaining_cells` identifies every slot still to process by its own
    cells (never by numeric index, which would go stale the moment a
    black cell added elsewhere shifts the slots' order), and `cur_slots`/
    `cell_to_slots`/`known`/`used_words` are all recomputed fresh before
    EVERY slot processed — not just once per round — so examining a slot
    always accounts for what the previous slot just placed. `_known_slot_
    boundary_cells` (see its own docstring) is likewise recomputed for
    every slot processed, since the pattern shifts underneath it as the
    loop runs.

    Returns `(grid, slots, assignment, impossible_slots)` unchanged, by
    reference, if no lengthening was ever applied (the common case),
    otherwise a new pattern/slots/words/still-impossible-slots quadruple
    reflecting the state after this step — exactly the same return
    contract as `_shorten_impossible_zones`.

    `permanent_black_cells` (`None`/empty by default — no effect for any
    pre-existing caller before "Finir la zone"): a real bug reported live
    by the user ("le bouton 'Finir la zone' ne verrouille pas correctement
    les cases grisées") — a cell bordering an impossible zone is only
    protected here if `_known_slot_boundary_cells` recognizes it as
    bounding an already-known word; a cell permanently frozen by "Finir la
    zone" (outside the selected zone, with no letter, so never "known")
    isn't in there, and could therefore be moved/removed like any other
    ordinary bounding cell, reopening a cell meant to stay black forever.
    Merged into `protected` on the same footing.

    `permanent_locked_letters` merged into `known` right from the start,
    for the same reason and the same real bug as `_shorten_impossible_
    zones` (see its own docstring): without this merge, a cell locked by
    the user but where neither of the two slots crossing it was yet fully
    resolved stayed invisible to `_known_slot_boundary_cells`/`_find_
    longer_word_for_zone`'s own `new_boundary in known` check, and could
    therefore be blackened like any ordinary white cell during a
    lengthening."""
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
        # Same "Mots Défi" exemption as `_shorten_impossible_zones` above
        # (see its own comment here, and `_challenge_fillable_slot_
        # indices`'s docstring): a slot an unused challenge word could
        # still legally fill is never fed back into this lengthening loop.
        remaining_idx = (
            set(_impossible_indices(cur_slots, index, known))
            - _challenge_fillable_slot_indices(cur_slots, known, challenge_words)
        )
        remaining_cells = [tuple(cur_slots[j]) for j in remaining_idx]

    if not changed:
        return grid, slots, assignment, impossible_slots

    final_slots = extract_slots(new_grid, rows, cols)
    invalid_full = set(
        _invalid_fully_known_indices(
            final_slots, index, known,
            exempt=set(permanent_locked_letters or ())
            | _challenge_word_cells(final_slots, known, challenge_words),
        )
    )
    final_assignment = [
        "".join(known[c] for c in cells)
        if all(c in known for c in cells) and j not in invalid_full else None
        for j, cells in enumerate(final_slots)
    ]
    # Same exemption for the final list handed to `_clean_blocked_slots` —
    # see the comment on `remaining_idx` above.
    final_impossible = sorted(
        invalid_full | (
            set(_impossible_indices(final_slots, index, known))
            - _challenge_fillable_slot_indices(final_slots, known, challenge_words)
        )
    )
    return new_grid, final_slots, final_assignment, final_impossible


def _clean_blocked_slots(slots, assignment, impossible_slots, locked_letters=None,
                          exclude_impossible_locked=False, index=None, rng=None,
                          grid=None, rows=None, cols=None, permanent_locked_letters=None):
    """Steps 1 and 2 of `_build_retry_seed` (see its own docstring for the
    complete history), extracted into their own function at the user's
    explicit request: "à la fin d'un tour, nettoyer automatiquement les
    emplacements bloqués, mais pas les noires." — `generate_grid` now
    calls this function alone, at the end of *every* palier (whether it
    resumes "telle quelle" or via a full nettoyage), to remove any word
    directly crossing an impossible slot, without ever touching the black
    cells themselves or regenerating the pattern — `_build_retry_seed`
    (the full nettoyage, pattern and black cells included) calls it
    internally as its own first step, rather than duplicating this
    computation.

    First recomposes, if `locked_letters` is given, the already fully
    determined word of any slot still at `None` but whose every cell is
    locked (see `_build_retry_seed`'s own docstring for the bug this
    pre-fill corrects) — a no-op without `locked_letters` (the "telle
    quelle" end-of-palier cleanup case, which already has a complete,
    word-by-word `assignment` with nothing to recompose). This
    reconstruction now validates the combination (`index` given) via
    `_slot_candidates` before accepting it — a real bug found live,
    through the real API, right after fixing a similar bug in `_optimize_
    before_cleanup` (see CLAUDE.md, "UNT"): "AMN", absent from the
    dictionary, assembled here as-is from individually correct locked
    letters that were never checked together — exactly the same bug class
    `_invalid_fully_known_indices` already fixes for `_shorten_impossible_
    zones`, but never fixed here (the one other spot in the file that
    recomposes a whole word from `locked_letters` without ever querying
    the dictionary). A slot whose locked combination is invalid stays
    `None` — never explicitly flagged "impossible" here (it isn't yet, in
    the `impossible_slots` sense), but the next palier will naturally
    rediscover it: its letters stay locked, so `Filler.exclude_
    immediately_impossible_slots()` (at the very start of the next search)
    will exclude it on its own the moment this same invalid combination is
    submitted again, making it surface in `impossible_slots` through the
    normal channel rather than letting an invented word survive.

    Then removes ALL words crossing each slot in `impossible_slots`, at
    once — behavior back in effect, at the user's explicit request:
    "Actuellement : pour un emplacement réputé injouable, on ne supprime
    qu'un seul mot croisant. Modifier : on retire tous les mots croisants
    (situation antérieure)." An intermediate evolution of this function
    had replaced this global removal with a one-word-at-a-time removal,
    stopping as soon as at least one real candidate became possible again
    (see CLAUDE.md for the full history of this evolution, including the
    live measurement — 55% fewer words removed — that motivated it); this
    intermediate change is now reverted, at the user's explicit request,
    without touching the black-cell alternative below (introduced
    afterward, but independent of how many words are otherwise removed):
    it's still tried once per impossible slot, and only if it fails (or
    isn't tried) are ALL still-assigned crossing words removed in one go,
    never one at a time.

    Before removing a crossing word, tries — with a probability
    `BLACK_CELL_INSTEAD_OF_REMOVAL_PROBABILITY` (1/10, lowered from an
    initial 1/3 — see that constant's own comment) — an alternative, at
    the user's explicit request (see that constant's own comment for its
    full reasoning): blackening a cell of the impossible slot itself
    rather than removing the word crossing it. Only available when
    `grid`/`rows`/`cols` are given (`None` by default — a no-op for any
    caller that doesn't supply them, in particular `_build_retry_seed`/
    `_cleaned_playable_score`, which deliberately stay word-removal-only).
    Among the slot's own cells, those *not already* determined by a
    still-assigned crossing word (`known`) are tried first — blackening a
    cell already covered by a confirmed word would destroy that word too,
    a more destructive outcome than a still-free cell — but, at the
    user's explicit request, an already-known cell is tried as a second
    resort rather than giving up on this alternative entirely when the
    slot is already fully crossed (the most common case late in the
    process, when few cells remain genuinely free): in that case, this
    black cell then removes, as a side effect, the crossing word that
    occupied it — exactly like an ordinary word removal would, but also
    permanently eliminating this cell from the impossible slot instead of
    just freeing its constraint. Only if no cell of the slot (free or
    already known) stays structurally valid once blackened does this fall
    back to the ordinary word removal. Within each of the two cell
    groups, candidates are ranked by how many OTHER slots in `impossible_
    slots` also pass through that same cell — at the user's explicit
    request: prioritize an intersection of several impossible slots over
    a cell that is impossible in only one direction, so one black cell
    has a chance to resolve more than one impossible slot at once — ties
    broken by a draw with no positional bias (shuffled before ranking, as
    everywhere else in this file); the first candidate, in that order,
    that stays structurally valid (`is_structurally_valid(...,
    min_interior_free=1)`) once blackened is kept; any word *other* than
    the impossible slot's own but passing through this exact cell is
    unassigned (it can no longer exist once the cell is black). Unlike a word removal (which only ever
    frees a constraint on the SAME slot i, which continues to exist in
    its current shape this palier), placing a black cell *eliminates*
    slot i in its current shape — the moment a cell has been successfully
    blackened for i, no further word removal is attempted for it this
    round: its real fragments will only be rediscovered on the next
    `extract_slots` call on the updated grid, exactly like any other
    black cell added elsewhere in this file.

    Genuinely dead-end zone, at the user's explicit request: once every
    crossing word has actually been removed (the normal case above, when
    the black cell wasn't tried or failed), if the slot *still* has
    strictly zero real candidates once every crossing constraint is thus
    lifted (`count == 0` — typically a length the dictionary doesn't
    cover at all), no further word removal could ever unblock this zone:
    every one of its remaining cells is then blackened directly (the same
    `is_structurally_valid(min_interior_free=1)` safeguard per cell, never
    a bypass), rather than letting it resurface identically at every
    future cleanup.

    `permanent_locked_letters` (`None` by default — no effect for any
    pre-existing caller before "Finir la grille", see `generate_grid`'s
    own docstring): neither the reconstruction above, nor the black-cell
    alternative, nor the "dead-end zone" black cell ever touch a cell it
    covers — these letters, placed by the user themselves in Interactive
    mode, are never called into question nor ever blackened, whatever
    word they spell or the situation of the slot (at `i`, or of another
    slot crossing it) they might otherwise concern.

    Returns `(cleaned_assignment, confirmed, new_black_cells)` —
    `cleaned_assignment` is a new list (never a mutation of the received
    `assignment`), with an explicit `None` for every removed slot, ready
    to directly serve as `preseed_assignment` for the next palier;
    `new_black_cells` is the (possibly empty) set of cells newly
    blackened by this alternative — to be folded into the pattern passed
    to the next palier by the caller, `_clean_blocked_slots` itself never
    mutating `grid` in place (an internal working copy, discarded after
    the call)."""
    if locked_letters:
        assignment = list(assignment)
        impossible_set = set(impossible_slots) if exclude_impossible_locked else set()
        for i, cells in enumerate(slots):
            if (
                assignment[i] is None
                and i not in impossible_set
                and all(cell in locked_letters for cell in cells)
            ):
                # A slot entirely covered by `permanent_locked_letters`
                # (see `generate_grid`'s own docstring) is always
                # recomposed, without ever querying the dictionary — these
                # letters are placed by the user themselves in Interactive
                # mode and must be treated as correct, whatever word they
                # spell.
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
        impossible_slot_set = set(impossible_slots)

        def _impossible_intersection_count(cell):
            return sum(1 for j in cell_to_slots[cell] if j in impossible_slot_set)

        for i in impossible_slots:
            crossing = sorted({
                j for cell in slots[i] for j in cell_to_slots[cell]
                if j != i and assignment[j] is not None
            })

            # Black-cell alternative, at the user's explicit request (see
            # BLACK_CELL_INSTEAD_OF_REMOVAL_PROBABILITY's own comment for
            # its full reasoning) — tried once per impossible slot,
            # independently of the word removal below (which, itself,
            # removes ALL crossing words at once again, see the docstring
            # above).
            placed_black = False
            if crossing and black_cell_capable and rng.random() < BLACK_CELL_INSTEAD_OF_REMOVAL_PROBABILITY:
                known = {}
                for cell in slots[i]:
                    for j in cell_to_slots[cell]:
                        if j != i and assignment[j] is not None:
                            known[cell] = assignment[j][slots[j].index(cell)]
                            break
                # A `permanent_locked_letters` cell (see `generate_grid`'s
                # own docstring) is never a candidate for this alternative
                # — blackening it would destroy a word placed by the user
                # themselves in Interactive mode, including when it only
                # belongs to impossible slot `i` via a crossing with
                # another, genuinely locked slot that shares it.
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
                # Shuffle first (random tie-break with no positional bias),
                # then a stable sort by how many other impossible slots
                # intersect each cell — an intersection of several
                # impossible slots is tried before a cell that is
                # impossible in only one direction, so one black cell has
                # a chance to resolve more than one impossible slot at
                # once.
                rng.shuffle(blank_candidates)
                rng.shuffle(known_candidates)
                blank_candidates.sort(key=_impossible_intersection_count, reverse=True)
                known_candidates.sort(key=_impossible_intersection_count, reverse=True)
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

            # Genuinely dead-end zone, at the user's explicit request:
            # every crossing word has just been removed (above) and, once
            # every crossing constraint is thus lifted, the slot still
            # has strictly zero real candidates (`count == 0` —
            # typically a length the dictionary doesn't cover at all): no
            # further word removal could ever unblock this zone, so every
            # one of its remaining cells is blackened directly rather
            # than letting it resurface identically at every future
            # cleanup (see CLAUDE.md for the real fixed point this
            # situation ended up causing on a large grid). Same as the
            # alternative above, each cell is tried with `is_structurally_
            # valid(min_interior_free=1)` before being blackened — never a
            # bypass of this absolute invariant, even here.
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
    """Last resort tried at the end of a failed palier, at the user's
    explicit request: "Lorsque toutes les recherches échouent en laissant
    une grille avec [ne reste] plus que des cases blanches isolées, boucher
    les cases isolées avec une case noire. Si le résultat donne une grille
    où tous les emplacements possibles sont remplis et valides, déclarer la
    grille réussie."

    A white cell still with no letter ("unfilled") is, here, any cell no
    assigned slot (`assignment[i] is not None`) covers — including a cell
    whose crossing slot (the other direction) IS assigned, giving it a
    real letter anyway: `known` below reflects exactly this reality, cell
    by cell, not slot by slot.

    An unfilled cell is said to be "isolated" if none of its 4 orthogonal
    neighbor cells is also unfilled — i.e. every one of its neighbors is
    already black or already carries a real letter. This is a deliberately
    conservative definition: if an unfilled cell has even a single
    unfilled neighbor, that means a real slot of at least 2 letters is
    still open right there (a word that could, in principle, still be
    found) — this is then no longer "nothing but isolated cells", and this
    function doesn't touch anything at all: neither this cell, nor any
    other cell of the grid, is modified. An isolated cell, by contrast,
    can by construction never be part of a still-open slot of at least 2
    cells: plugging such a cell never shortens an already-confirmed word,
    nor removes any real letter already placed.

    Does nothing (returns `None`) in three cases: (1) at least one
    unfilled cell that isn't isolated remains (a genuine still-open slot
    exists elsewhere — not just isolated cells); (2) blackening every
    isolated cell would break the grid's structural validity
    (connectivity, or an orphaned white cell elsewhere — `is_structurally_
    valid` at the strictest level, `min_interior_free=1`); (3) once the
    isolated cells are plugged, at least one slot of the new pattern
    (`extract_slots` recomputed on the modified grid) still either has no
    known letter at all its cells, or is filled with a combination that
    matches no real dictionary word — the resulting grid is then NOT
    "filled and valid" in the sense of the request, so declaring it
    successful is out of the question. Otherwise (every slot of the new
    pattern is fully known and forms a real word), returns `(new_grid,
    new_slots, new_assignment)` — a result directly usable as a complete
    generation success, on the same footing as a CSP fill that would have
    concluded normally."""
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
        # A slot entirely covered by `permanent_locked_letters` (see
        # generate_grid's own docstring) is always accepted as-is — these
        # letters are placed by the user themselves in Interactive mode
        # and must be treated as correct, whatever word they spell
        # (probably a proper noun).
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
    """Builds the next palier's starting point from the current palier's
    own best failed attempt, at the user's explicit request — a new
    cross-palier resume algorithm, distinct from the "patch" mechanism
    tried and then entirely abandoned earlier in this project's history
    (see the project-best-practices SKILL): that one retouched the SAME
    attempt by adding one black cell at a time and relaunching a complete
    search from scratch every time; this one never relaunches the same
    attempt — it keeps whatever has already been confidently resolved
    (letters genuinely placed, not just one more black cell) and only
    makes the next search bear on what's genuinely still uncertain.

    Three steps, in the exact requested order:

    1. **Remove words directly connected to the failed slots.**
       `impossible_slots` (see Filler.impossible_zone_slots) names the
       unassigned slots whose domain was empty at the moment the search
       made the most progress (`best_assignment`) — that's precisely the
       cause of the blockage. An *assigned* slot sharing a cell with one
       of them (so crossing it, its shared letter being among the
       constraints that emptied its domain) is removed in turn:
       `to_remove` never goes further than one level ("directly" — no
       cascading propagation), at the user's explicit request.
    2. **Whatever remains becomes the next palier's pre-defined
       letters.** Every cell still covered by an assigned slot (so
       neither impossible nor removed in step 1) becomes a `{cell:
       letter}` entry in the returned dict — the only letters ever
       treated as real progress, never a statistical hint from `forced_
       letters` (which was never a genuine fact).
    3. **Keep every existing black cell adjacent to a confirmed letter;
       reopen every other one.** This criterion has a multi-stage
       history. A first version only ever kept black the two cells that
       genuinely bound each surviving word — immediately before its
       first letter and immediately after its last, in that word's own
       direction (horizontal or vertical, never the other one) —
       reopening any cell merely adjacent on the side (above/below a
       middle letter of a horizontal word, for instance), on the
       reasoning that such a cell bounds that word in no way at all. This
       left more room to maneuver (and so more diversity among the next
       palier's own PARALLEL_ATTEMPTS parallel attempts) for placing new
       black cells — but turned out to cause a different problem,
       diagnosed by the user from a real case: reopening a lateral cell
       next to a confirmed letter opens a passage that can create, in the
       other direction, a brand-new slot immediately constrained by that
       letter (and potentially by other nearby confirmed letters) — a
       slot potentially left with very few or zero real candidate words,
       forcing the next palier's own pre-fill to blacken far more than
       necessary to compensate (see `_prefill_unfillable_slots` above,
       and the fully-black-column bug it eventually produced). Widened,
       at the user's explicit request, to the broadest possible rule: any
       current black cell orthogonally adjacent (all 4 sides) to *any*
       cell of `confirmed` stays black; only cells touching no confirmed
       letter at all are reopened. This rule strictly encompasses the
       word-boundary version (a cell bounding a word is itself adjacent
       to its first/last letter), so there's no longer a need to compute
       the two cases separately.

       A two-branch tightening (a cell bounding a word, OR touching at
       least two confirmed letters at once) was tried for a while, then
       abandoned almost immediately at the user's explicit request, who
       restated the intended rule more simply: "conserver les cases
       noires dont un des 4 côtés ouvre un emplacement où il y a une
       lettre (ça couvre le cas des cases en bout de mot) ; supprimer
       toutes les autres." A first implementation of this restatement
       still only checked the immediately neighboring cell — wrongly
       reverting to the exact broadest rule already in place. Fixed at
       the user's explicit request, who pointed out the missed detail:
       "il peut y avoir des blancs entre la case noire et la lettre" —
       the check now covers the *entire slot* on each side (the run of
       white cells, potentially long, up to the next black cell or the
       edge), not just the immediately adjacent cell — reusing the same
       side-walk as `_new_black_cell_breaks_locked_slot` (a walk along
       consecutive white cells in each direction up to a black cell or
       the edge), this time to look for a confirmed letter somewhere
       along the way rather than to count dictionary candidates.

       This "one side suffices" version was immediately tightened once
       more, at the user's explicit request, who identified a concrete
       case it wrongly protected: a black cell that *sees* a letter on
       only one side (for instance by crossing, at a distance, a word
       assigned in the other direction) without itself being the
       boundary (start/end) of the corresponding word doesn't actually
       protect anything — reopening it threatens no existing word's
       integrity, since the letter it sees belongs to a word that
       doesn't extend to this cell in its own direction. The final rule
       therefore only keeps a black cell in two cases, a union of two
       independent conditions: (1) it genuinely bounds a surviving word —
       immediately before its first letter or immediately after its
       last, in that word's own direction (the same computation as the
       very first version of this step, never removed, only completed);
       (2) it has a confirmed letter on *both* sides at once of the same
       axis — above AND below, or left AND right (no need for both axes
       at once) — a cell "sandwiched" between two word segments, never a
       cell merely sitting between two word segments on the same axis,
       where reopening it would merge two distinct slots into one that
       may match no real word, disturbing both sides at once. A cell
       that only sees a letter on one side of an axis, without bounding
       that word, is now
       reopened — including the distant-crossing case that motivated the
       previous version; that case never threatened any existing word's
       integrity, only the "one side suffices" version wrongly treated it
       as if it did. Confirmed by the user with an equivalent restatement:
       "une case noire se trouvant quelque part entre 2 mots existants
       (horizontalement ou verticalement) doit être conservée ; une case
       noire se trouvant au bout d'un mot (début ou fin) doit être
       conservée ; les autres cases noires peuvent être supprimées" —
       exactly conditions (2) and (1) above. Verified with three hand-built
       grids: a cell seeing a letter on only one side (distant crossing,
       no boundary) now reopens; a cell genuinely bounding a word stays
       black; a cell sandwiched between two assigned words on the same
       vertical axis stays black.

       An exception was added at the user's explicit request: a black
       cell otherwise a candidate for reopening (not adjacent to a
       confirmed letter) still stays black if all 4 of its neighbors
       (up, down, left, right) are *themselves* all black in the original
       grid (`_fully_surrounded_by_black`) — reopening it would create a
       white cell isolated on all 4 sides, a "single-letter hole" that
       would violate the absolute invariant established elsewhere in this
       file (see is_structurally_valid): a white cell can never be short
       (1 letter) in both directions at once. A border cell can never
       satisfy this condition (at least one neighbor is off-grid), so
       this exception only ever applies to a strictly interior cell —
       consistent with the fact that this isolated-hole risk only exists
       away from the border.

    Returns `(new_pattern, locked_letters)` — `new_pattern` serves as
    `seed_grid` and `locked_letters` as `locked_letters`/`forced_letters`
    for the next palier's `make_pattern`/`_pattern_attempt` (see
    generate_grid).

    A real bug was found and fixed, from a concrete case the user
    supplied ("beaucoup de lettres, peu de conflit, et l'étape d'après,
    presque tout a été supprimé") and confirmed by a live multi-palier
    audit (not just reasoned about): `assignment` (this attempt's own
    `Filler`'s `best_assignment`) only ever holds a word for a slot if
    backtracking genuinely ended up explicitly assigning it during ITS
    OWN search — a slot already entirely determined by the previous
    palier's own locked letters (`locked_letters`, passed as a hard
    constraint) is NEVER "reassigned" by `_backtrack` if the search fails
    before ever reaching that slot (the very fast `checks=1`/
    `reason="search_exhausted"` case: the very first domain checked is
    already empty). In that case, `assignment` reverts entirely to
    `None`, including for already-locked slots, even though those
    letters were perfectly settled — step 2 above therefore wrongly,
    systematically discarded them on every such immediate failure.
    Confirmed live: on an audit of 8 chained paliers (real grid, real
    dictionary), 3 of the 8 (paliers 2, 4, 7) showed `assigned_slots=0`
    for all 6 candidates even though the previous palier had locked 65,
    44, and 69 letters respectively — all of it vanished, not because it
    crossed an impossible slot, but because it never appeared in
    `assignment` at all. Fixed by treating any slot entirely covered by
    `locked_letters` as if it had been assigned the word those letters
    spell, before applying exactly the same rules (steps 1 through 3) as
    to any other genuinely assigned word — a locked slot crossing an
    impossible slot still gets removed like any other, it's not
    protected beyond its legitimate share.

    This first fix itself introduced a second bug, found by the same
    kind of live multi-palier audit: a slot can be both entirely covered
    by `locked_letters` *and* itself present in `impossible_slots` — the
    exact combination of letters locked at that slot doesn't, in fact,
    match any real dictionary word (that's precisely *why* it's
    impossible). The fix above "reassigned" it from `locked_letters`
    regardless without checking for this case, preserving this invalid
    combination indefinitely from one palier to the next — since this
    slot is never in `to_remove` (which only removes the OTHER slots
    crossing an impossible slot, never the impossible slot itself),
    nothing ever changed again from one palier to the next, a genuine,
    stuck fixed point. Reproduced live: on a grid stuck at exactly this
    point, 29 locked letters and 2 impossible slots (each 2 cells,
    already entirely locked) stayed **bit-for-bit identical** across 12
    consecutive paliers, until all 40 attempts were exhausted with no
    solution ever found — a case that used to succeed.

    Fixing this by *systematically* excluding such a slot from
    reassignment (`exclude_impossible_locked=True` permanently) was
    tried, then refined after observing, through a direct before/after
    comparison on several real scenarios, that this wasn't the right
    answer everywhere either: a different scenario (10×10, vocabulary
    deliberately restricted to 400 words) that succeeded without this
    exclusion started failing systematically with it — the exclusion,
    applied to every palier without distinction, also removes slots
    whose presence didn't actually block anything at all, wasting
    content that was otherwise recoverable. `exclude_impossible_locked`
    (`False` by default, so the normal behavior — no exclusion, which
    wins in the majority of real scenarios observed) is therefore only
    used as a last resort, at the user's explicit request: only when
    `generate_grid` detects that a palier produced *no* change at all
    compared to the previous one (the confirmed letters are rigorously
    identical, a genuine fixed point), it reruns this same cleanup a
    second time for that palier, this time with `exclude_impossible_
    locked=True`, solely to unblock this specific case rather than
    applying the more aggressive rule everywhere."""
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

    # Unconditional protection of black cells already present *before*
    # this palier started (`seed_grid`, the pattern `_pattern_attempt`/
    # `make_pattern` received as input for THIS exact palier, before its
    # own pre-fill/ratio placement/"curative cleanup") — at the user's
    # explicit request, after a real bug observed live: "certaines cases
    # noires initiales disparaissent... il ne faut toucher qu'aux cases
    # noires ajoutées [ce palier], pas à celles présentes avant de
    # commencer cette phase." Root cause: the protection above (the two
    # preceding loops) only ever trusts `assignment` (this exact attempt's
    # final CSP-search result) to decide which words "survive" — but the
    # "curative cleanup" (see `_remove_a_crossing_word`, called from
    # `_prefill_unfillable_slots`) can remove a word from `locked_letters`
    # *inside the worker itself*, before the search even starts — a word
    # that was nonetheless already confirmed from a previous palier,
    # present in `carry_locked_letters` (the parent's own copy, never
    # mutated by the separate worker — see below), but absent from the
    # worker's own `locked_letters` copy once curative cleanup has gone
    # through it. If the CSP search subsequently fails to reassign that
    # same slot (`assignment[i]` stays `None`), its boundary cells — which
    # were nonetheless already part of the pattern *before* this palier
    # started, with nothing to do with this specific attempt's own
    # curative cleanup — lost all protection and ended up reopened, as if
    # they had been added and then failed this very palier. Reproduced
    # live: a dedicated diagnostic, comparing the black cells of the
    # "pattern" preview (this palier's own entry pattern) against those of
    # the "pattern_generated" preview (the pattern produced by THIS
    # attempt), confirmed cells present "before" totally absent "after"
    # for several real attempts/paliers. `seed_grid` (`None` by default —
    # any pre-existing caller before this fix, if there ever was one
    # without this parameter, is unaffected) is the ENTRY pattern of the
    # attempt whose result is `grid`/`assignment` — any cell already
    # black in it is unconditionally protected here, regardless of
    # whether a word survives in `assignment` or not: by construction it
    # can never have been "added without success" by THIS palier, since
    # it already existed before it started.
    if seed_grid is not None:
        for r in range(rows):
            for c in range(cols):
                if seed_grid[r][c] == BLACK:
                    protected_black_cells.add((r, c))

    # `permanent_black_cells` (`None`/empty by default — no effect for any
    # pre-existing caller before "Finir la zone"): an extra line of
    # defense, the same as the `seed_grid` protection just above — the
    # real source of the bug ("cases noires sur les cases verrouillées")
    # was elsewhere (see `_pattern_attempt`'s own docstring, a "reset"
    # worker that ignored these cells entirely), but nothing stops this
    # step here from reopening one of them if it were ever to arrive here
    # without already being black in `seed_grid`, for some not-yet-
    # identified reason — never a free pass to remove once the root cause
    # is fixed.
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


# Score used to pick the best cleaned grid among several candidates — the
# same shared `_content_score` formula used everywhere else in this file
# (successful-attempt tie-break, failed-attempt selection — see its own
# docstring), computed here over every word genuinely "in place" after
# cleanup (all of its cells appear in `cand_confirmed`; its own letters
# read directly off `cand_confirmed` to recover the actual word, since
# this function is only ever given cells/letters, never a `Filler.
# assignment`-style word list). Hoisted to module level (previously a
# local closure, specific to the full nettoyage only, `else:` in
# `generate_grid`) at the user's explicit request, once the same logic
# was also needed for "reprise telle quelle" (see `_clean_continue_
# candidate`/`_continue_seed_pool` further below).
def _words_in_place_score(cand_slots, cand_confirmed, priority_words=None, challenge_words=None):
    pairs = (
        ("".join(cand_confirmed[cell] for cell in cells), cells)
        for cells in cand_slots
        if all(cell in cand_confirmed for cell in cells)
    )
    return _content_score(pairs, priority_words, challenge_words)


# Breaks a `_words_in_place_score` tie — the candidate's own black-cell
# count, at the user's explicit request, after a real stuck state observed
# live on a large, heavily locked grid (see CLAUDE.md for the full
# history). Also hoisted to module level for the same reason as `_words_
# in_place_score` above.
def _candidate_black_count(cand_seed):
    return sum(row.count(BLACK) for row in cand_seed)


# Sorts a list of cleaned candidates by (`_words_in_place_score`,
# `_candidate_black_count`) descending — each candidate is a tuple whose
# first 3 elements are `(seed_grid, confirmed, slots)`, in this exact
# order (any further elements, if present, are never read here — see
# `_clean_continue_candidate` for a 6-element example). `priority_words`/
# `challenge_words` passed straight through to `_words_in_place_score` —
# see `_content_score`'s own docstring: the same theme/"Mots Défi"
# preference already applied to every other content score in this file
# now applies here too, at the user's explicit request ("le scoring
# réussi et échoué doivent utiliser la même logique qui favorise le
# glossaire thématique").
def _sorted_by_score(cleaned_candidates, priority_words=None, challenge_words=None):
    return sorted(
        cleaned_candidates,
        key=lambda sc: (
            _words_in_place_score(sc[2], sc[1], priority_words, challenge_words),
            _candidate_black_count(sc[0]),
        ),
        reverse=True,
    )


# Reduces an already-sorted list (best first) down to the pool passed to
# the next palier, eliminating the `FULL_RESET_ATTEMPT_COUNT` worst ones —
# this eliminated count exactly matches the number of attempts the next
# palier will reserve anyway for a completely fresh, blank start (see
# `reset_count` in `generate_grid`), the surviving grids then filling, one
# by one, exactly the rest of the next palier's own slots. `max(1, ...)`:
# never fully empty the pool, even if `FULL_RESET_ATTEMPT_COUNT` exceeds
# the number of available candidates — at least the best grid itself
# always remains. `extract` picks out, from each candidate tuple, exactly
# what the next palier needs to relaunch an attempt from this entry —
# `(seed_grid, locked_letters)` by default (the full nettoyage, a fresh
# pattern), `(seed_grid, preseed_assignment, excluded_slots)` for "reprise
# telle quelle" (see `_continue_seed_pool`).
def _seed_pool(sorted_candidates, extract=lambda sc: (sc[0], sc[1])):
    keep = max(1, len(sorted_candidates) - FULL_RESET_ATTEMPT_COUNT)
    return [extract(sc) for sc in sorted_candidates[:keep]]


# Builds, for a given palier, the "lineage" number (see generate_grid,
# `process_number`) of each of its PARALLEL_ATTEMPTS tasks BEFORE even
# submitting them — at the user's explicit request: "il faut que les
# grilles portent leur propre numéro, et le gardent jusqu'à la fin de la
# résolution", after a report observed directly on the display ("les
# grilles changent de numéro d'un cycle sur l'autre"). Root-caused: the
# displayed number used to come from the real PID of the worker that
# produced each diagnostic (`worker_pid_numbers`) — a PID stable for the
# whole duration of a `generate_grid()` call, but whose ASSIGNMENT to a
# given task is not: `ProcessPoolExecutor` hands each task to the first
# available worker, never necessarily the same one from one palier to the
# next for "the same logical lineage" — a candidate continuing the same
# pool could therefore be handled by a different PID at every palier,
# changing its displayed number even though nothing about the grid itself
# had really changed.
#
# `dispatch_lineage[i]` (i = submission index, 0..PARALLEL_ATTEMPTS-1,
# never completion order) is `None` for a reset task (`i < reset_count`,
# an entirely new pattern — no lineage to inherit) and `pool_lineage[(i -
# reset_count) % len(pool_lineage)]` otherwise — the same cyclic
# computation already used to distribute pool grids to non-reset tasks
# (see `pool`/`continue_pool` in generate_grid), so each task inherits
# exactly the number of the pool entry it's resuming from.
def _build_dispatch_lineage(seeds_count, reset_count, pool_lineage):
    return [
        None if i < reset_count else pool_lineage[(i - reset_count) % len(pool_lineage)]
        for i in range(seeds_count)
    ]


# Complète `raw_lineage` (les numéros hérités par chaque candidat SURVIVANT
# of this palier, in the same order as the reconstructed pool — see
# generate_grid): an entry of `None` means this candidate comes from a
# reset task (an entirely new pattern, see `_build_dispatch_lineage`),
# which therefore had no lineage to inherit to begin with. At the user's
# explicit request: "La grille entièrement nouvelle doit reprendre le
# numéro de la grille qui disparaît (normalement, la moins bonne)."
# `previous_lineage` is the set of numbers that were ACTIVE this palier
# (`dispatch_lineage`, see above, reset tasks included — their `None` is
# ignored via the `if n is not None` filter below); any number that was
# there but no longer appears among `raw_lineage`'s own RESOLVED survivors
# has thus been "freed" (its own grid didn't survive `_seed_pool`'s own
# score-based sort — the worst one, by construction, since `_seed_pool`
# always eliminates the worst ones first) and is reassigned, in order, to
# each still-unresolved candidate. `next_lineage_number` (a counter
# persisting across the whole `generate_grid()` call, never reset) only
# ever serves as a safety net if no number was ever freed (a degenerate
# case, not encountered in practice with the current FULL_RESET_ATTEMPT_
# COUNT) — so an unresolved `None` never slips into the returned pool.
# Returns `(finalized_lineage, updated_next_lineage_number)`.
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


# Cleans UP ONE individual failed attempt of a "reprise telle quelle"
# palier (see generate_grid, `if still_has_hope:`) — the same steps as
# `_clean_blocked_slots` (removing words crossing an impossible slot, with
# its 1/10 black-cell alternative), applied here to every distinct attempt
# of this palier rather than only the "best" one — at the user's explicit
# request: "Quand il n'y a pas de déclenchement d'un nettoyage complet,
# chaque process doit repartir à l'étape suivante avec sa grille
# partiellement nettoyée (sauf le pourcentage de grilles entièrement
# neuves)" — the same principle already in place for the full nettoyage
# (see `_clean_all_candidates`, in `generate_grid`) now extended to
# "reprise telle quelle", which until now was the only mode keeping just
# one single grid (`selected_grid`/`selected_diag`, the "best" in
# `failed_pairs`'s sense) for every non-reset worker of the next palier.
#
# Returns a 6-element tuple — `(cand_seed_grid, cand_confirmed,
# cand_slots, cand_preseed_assignment, cand_excluded_slots, cand_process_
# number)` — the first 3 in the same order as the full-nettoyage
# candidates (compatible with `_words_in_place_score`/`_sorted_by_score`),
# the next 2 the shape `_pattern_continue` expects (`cand_seed_grid`
# duplicated, never repeated within the tuple), the last one purely
# diagnostic (see `carry_seed_pool_process_numbers`) — the number of the
# process that produced `cand_grid`, passed through as-is from
# `cand_diag.get("process_number")`.
#
# If `_clean_blocked_slots` also placed a new black cell (its 1/10
# alternative), the slot numbering shifts — the same remedy already used
# for the single winning grid before this feature existed (see the full
# history in CLAUDE.md, "the same index pitfall already encountered...
# for the single-cell lock mechanism, since removed"): rebuild `cand_
# slots`/`cand_preseed_assignment`/`cand_excluded_slots` from a fresh
# `extract_slots` call on the genuinely updated pattern, relying on
# `confirmed` (indexed by cell, never by slot index, so immune to this
# shift) rather than on the old indices.
#
# `_shorten_impossible_zones` (right before `_clean_blocked_slots` earlier
# in this file) first tries to shorten each impossible slot (a shorter
# word at the head/tail of the zone, bounded by a black cell) before any
# ordinary word removal — never during a full nettoyage (see its own
# docstring), only here, for the same reason already established for
# `BLACK_CELL_INSTEAD_OF_REMOVAL_PROBABILITY`. `_lengthen_impossible_
# zones` (right after it, same file) then tries, on whatever is still
# impossible, the reverse operation — lengthening the zone by pushing
# back/removing one of its own existing bounding black cells rather than
# adding a new one inside it — at the user's explicit request (see its
# own docstring for the complete detail). The pattern/still-impossible-
# slots list possibly updated by these two steps (`cand_grid`/`cand_
# impossible`) then replace `cand_diag["assignment"]`/`cand_diag[
# "impossible_slots"]` for the rest of this function — a complete no-op
# (same objects, same indices) as long as neither one changed anything.
def _clean_continue_candidate(cand_grid, cand_diag, rows, cols, index, rng,
                               permanent_locked_letters=None, permanent_black_cells=None,
                               challenge_words=None):
    """Cleans up a single failed attempt of a "reprise telle quelle"
    palier (see `_continue_seed_pool`) — removes whatever crosses an
    impossible slot (`_clean_blocked_slots`), after first trying to
    shorten (`_shorten_impossible_zones`) then lengthen (`_lengthen_
    impossible_zones`) those same slots.

    `permanent_locked_letters` (`None` by default — no effect for any
    pre-existing caller before "Finir la grille") is passed as-is to each
    of these three functions, so no cell it covers is ever blackened or
    ever flagged "impossible" on the sole ground that it matches no real
    dictionary word — see `generate_grid`'s own docstring.

    `permanent_black_cells` (`None`/empty by default — no effect for any
    pre-existing caller before "Finir la zone") is passed only to
    `_lengthen_impossible_zones` (see its own docstring for the real bug
    this fixes) — never to `_shorten_impossible_zones`/`_clean_blocked_
    slots`, which only ever blacken an already WHITE cell (a `permanent_
    black_cells` cell is, by construction, already black since the very
    first palier, so it can structurally never appear among their own
    candidates).

    When `_clean_blocked_slots` also placed a new black cell (its 1/10
    alternative — `new_black_cells`), the pattern's own shape changes:
    `new_slots` is re-extracted on this modified grid, and `cand_preseed_
    assignment` then recomposes the word of EVERY slot of this new
    pattern entirely covered by `confirmed` — including a brand-new slot,
    born from the added black cell, never itself resolved by a real
    search. This word is validated before being promoted (`_invalid_
    fully_known_indices`, the same safeguard as `_optimize_before_
    cleanup`/`_clean_blocked_slots` — see CLAUDE.md, "UI"): a combination
    matching no real dictionary word, even entirely covered by
    individually correct letters, is never promoted — the slot stays
    `None`, and will be rediscovered on its own as impossible at the very
    next search (`Filler.exclude_immediately_impossible_slots`), rather
    than being locked in as-is for the rest of the generation."""
    cand_slots = extract_slots(cand_grid, rows, cols)
    cand_grid, cand_slots, cand_assignment, cand_impossible = _shorten_impossible_zones(
        cand_grid, rows, cols, cand_slots, cand_diag["assignment"],
        cand_diag["impossible_slots"], index, rng,
        permanent_locked_letters=permanent_locked_letters,
        challenge_words=challenge_words,
    )
    cand_grid, cand_slots, cand_assignment, cand_impossible = _lengthen_impossible_zones(
        cand_grid, rows, cols, cand_slots, cand_assignment, cand_impossible, index, rng,
        permanent_locked_letters=permanent_locked_letters,
        permanent_black_cells=permanent_black_cells,
        challenge_words=challenge_words,
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
            new_slots, index, confirmed,
            exempt=set(permanent_locked_letters or ())
            | _challenge_word_cells(new_slots, confirmed, challenge_words),
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


# Extracts, from an already-sorted list of `_clean_continue_candidate`
# candidates (6 elements), the pool passed to the next "reprise telle
# quelle" palier — `(seed_grid, preseed_assignment, excluded_slots)` per
# entry, the shape `_pattern_continue` expects (the 6th element, the
# inherited lineage number, is never passed to `_pattern_continue` itself
# — see `carry_seed_pool_continue_lineage`, built separately with the
# same `_seed_pool` but a different extractor, for what it's actually
# used for). A plain call to `_seed_pool` above with the extractor
# adapted to this 6-element shape.
def _continue_seed_pool(sorted_candidates):
    return _seed_pool(sorted_candidates, extract=lambda sc: (sc[0], sc[3], sc[4]))


# ---------- Parallel attempts (pattern + fill) ----------
#
# `index` (the pre-indexed lexicon, potentially 100,000+ words) is sent
# once per worker via the pool's initializer, rather than re-pickled for
# every submitted task — it never changes during a generate_grid() call.
_worker_index = None
# Set (frozenset of bare, uppercase MOT words) of the words to prefer —
# the theme preselection coming from the Qdrant pre-search (see
# generate_grid's `priority_words` and backend/app.py). Empty/`None` = no
# theme, unchanged behavior. Passed once per worker via the pool's
# initializer, like `_worker_index` (it can hold several thousand words
# and never changes during a generate_grid() call).
_worker_priority_words = None
# "Mots Défi" (see generate_grid's `challenge_words` and Filler.
# challenge_words): a small, plain frozenset (never a DualSet, unlike
# `_worker_priority_words` above — a challenge word carries no language),
# shared the same way for the same reason.
_worker_challenge_words = None
# "Stop" button (see CANCEL_CHECK_INTERVAL/Filler.__init__), at the
# user's explicit request — like `_worker_index` right above, passed once
# per worker via the pool's initializer rather than as an argument of
# every submitted task. Necessary, not just a matter of style: a
# `multiprocessing.Event` submitted as an ordinary `executor.submit(...)`
# argument was observed live to trigger `RuntimeError: Condition objects
# should only be shared between processes through inheritance` (the
# "spawn" start method, macOS's default, never shares memory by
# inheritance — every submitted task is individually re-pickled); passing
# it via the pool's initializer, exactly like `index`, is the documented
# and genuinely functional way to share this kind of object with worker
# processes.
_worker_cancel_event = None
# "The whole batch is blocked" signal (see Filler._backtrack and
# generate_grid below), at the user's explicit request: "quand une
# recherche arrive à une situation jugée 'bloquée', arrêter toutes les
# recherches du batch N, pour passer au batch N+1 sans attendre que toutes
# les recherches arrivent à une situation de blocage." A single
# `multiprocessing.Event`, created once per `generate_grid()` call (like
# `cancel_event` right above, and for the same technical reason: passed
# once per worker via the pool's initializer, never as a submitted-task
# argument) but *reset* by the parent process at the start of every
# palier — unlike `cancel_event`, which only ever fires once for the
# whole generation, this signal has a different meaning at every palier
# (a blockage observed at palier N must not influence palier N+1). Set
# by any worker whose own `Filler.abandoned` becomes true (the 30% rule,
# see UNFILLABLE_ABANDON_FRACTION) — checked by every other worker of the
# same batch, which then also stop, without waiting to individually reach
# their own abandon threshold or their own budget.
#
# No longer actually passed ANYWHERE today — neither to `_pattern_attempt`
# (fresh pattern) nor to `_pattern_continue` ("reprise telle quelle") —
# both always pass `None` to `try_fill` instead of this global. Full
# history, in order:
#
# First disabled specifically for `_pattern_attempt`, a real bug found
# live before any deployment, not just reasoned about: a `_pattern_
# attempt` palier's own PARALLEL_ATTEMPTS attempts each generate their
# OWN independent pattern (`make_pattern` with its own `rng`, on the same
# starting `seed_grid`/`locked_letters` but with black cells added
# differently each time) — one attempt's "30% of THIS pattern is
# impossible" conclusion therefore says nothing reliable about another
# attempt's completely different pattern in the same batch. Reproduced
# live on the reference 15×10 grid (seed 7, previously reliable):
# applying this signal to both mechanisms at once made this seed fail
# (`None` returned after 200 paliers, whereas it succeeded before this
# fix) — disabling the signal specifically for `_pattern_attempt` (by
# always passing it `None` instead of this global) restores success,
# confirming the problem really does come from this contamination
# between independent patterns.
#
# `_pattern_continue`, at the time, did exactly the opposite by
# construction: all of its parallel attempts shared RIGOROUSLY the same
# pattern and the same locking — only the exploration order differed —
# so one attempt's conclusion on this shared pattern stayed relevant for
# the others, and the signal stayed passed there.
#
# This is no longer true since `carry_seed_pool_continue` (see
# `generate_grid`), at the user's explicit request ("chaque process doit
# repartir à l'étape suivante avec sa grille partiellement nettoyée"): two
# parallel attempts of the same "reprise telle quelle" palier can now
# receive DIFFERENT pool entries (or even an entirely new pattern via
# `_pattern_attempt` for reset attempts, see `FULL_RESET_ATTEMPT_COUNT`)
# — exactly the same contamination between independent patterns that
# motivated disabling this signal for `_pattern_attempt` now applies here
# too, so it was disabled the same way, preventively, before a live
# failure ever confirmed it on this exact reference grid (see `_pattern_
# continue`'s own docstring/call site).
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
# `multiprocessing.Queue` on which each worker publishes, in real time,
# every new `Filler.best_assignment` record reached during ITS OWN
# search — not just its final state — at the user's explicit request:
# "Il ne faut pas supprimer les 70% des tentatives restantes, mais
# seulement les interrompre... Il faut conserver les 6 meilleures grilles
# échouées des N process trouvées à n'importe quel moment des N
# recherches", later refined: "Chaque process suit son meilleur état, et
# transmet au process parent l'information que ce meilleur état a
# changé. Le process parent garde les 6 meilleurs états, de tous les
# états dont il a été informé par les N process." Same technical
# constraint as the other globals above (passed once per worker via the
# pool's initializer, never as a submitted-task argument). The volume
# stays bounded: `best_assigned_count` can only progress one unit at a
# time and never exceeds the grid's own slot count (~50-60 in practice),
# so at most ~50-60 publications per worker per palier, whatever the
# real number of `_backtrack` calls (potentially hundreds of thousands) —
# see `Filler._backtrack` for the exact call site.
_worker_best_state_queue = None
# Warm-up `multiprocessing.Barrier` (see `_warmup_worker`), passed once
# via the pool's initializer like the other globals above — never reused
# after this worker's very first call to `_warmup_worker` (the real
# tasks, `_pattern_attempt`/`_pattern_continue`, never touch it).
_worker_warmup_barrier = None
# Set of words (grid form) considered proper nouns for this language, and
# the maximum quota allowed in the final grid — see MAX_PROPER_NOUNS/
# generate_grid, at the user's explicit request. Passed once via the
# pool's initializer, like the globals above (the set can be sizable —
# no point re-serializing it for every submitted task), rather than as a
# direct argument of `_pattern_attempt`/`_pattern_continue`.
_worker_proper_noun_words = None
_worker_max_proper_nouns = None
# Same for words absent from the gloss/definitions dictionary — see
# MAX_NON_GLOSS_WORDS/generate_grid.
_worker_non_gloss_words = None
_worker_max_non_gloss = None


def _warmup_worker():
    """A dummy task submitted `PARALLEL_ATTEMPTS` times at once, right
    after the pool is created (see generate_grid, right after `with
    ProcessPoolExecutor(...)`), for the sole purpose of forcing the REAL
    startup (spawn + running `_init_worker`, which deserializes the large
    `index`) of each of the `PARALLEL_ATTEMPTS` processes before the very
    first palier ever submits its real tasks.

    Diagnosed live (not just assumed), in two stages. First:
    `ProcessPoolExecutor` spawns its processes lazily — the spawn itself
    (fork/exec + `_init_worker`, slow here due to deserializing the
    index) completes asynchronously, well after the `submit()` call that
    triggered it. On a `generate_grid()` call's very first palier, this
    means only a handful of workers are genuinely ready by the time the
    palier's 10 tasks get distributed — the others finish starting up too
    late and receive none. Observed result: up to 5 of 10 tasks executed
    by the SAME PID during palier 1 of a real run (9x7, seed=5) — so, on
    screen, the same `process_number` duplicated several times within a
    single batch of previews, and this until every worker has finished
    starting up (~palier 7 in this test) — not a bug in the numbering
    itself (`worker_pid_numbers`), which faithfully reflects the real
    PIDs it receives.

    A first version of this function simply returned `os.getpid()` with
    no synchronization, submitted either as a single batch of
    `PARALLEL_ATTEMPTS` tasks, or in successive rounds retrying as long as
    the set of distinct PIDs seen stayed below `PARALLEL_ATTEMPTS` — both
    turned out insufficient, discovered by re-diagnosing live afterward:
    `ProcessPoolExecutor._adjust_process_count()` only requests a new
    spawn if it has NO worker already idle — the moment a single worker
    becomes available, EVERY new `submit()` is handed to it first rather
    than triggering another spawn, regardless of how many tasks are
    still queued. Since this dummy task is near-instantaneous, that
    worker becomes available again so fast that it absorbs nearly all the
    remaining tasks before the others ever get a chance to spawn at all —
    measured: out of 10 tasks submitted at once, only 3 distinct PIDs
    ever appeared (a single worker handled 6 of them alone); insisting
    across several dozen successive rounds, only 6 of 10 distinct PIDs
    ever showed up, proof the ceiling isn't just "not yet reached" but
    structurally blocked once a first worker is already idle.

    Fixed with a real synchronization barrier (`warmup_barrier`, a
    `multiprocessing.Barrier(PARALLEL_ATTEMPTS)` — see `_worker_warmup_
    barrier`): this task calls `.wait()` on it before returning its PID,
    so it stays blocked until all `PARALLEL_ATTEMPTS` calls have reached
    the barrier. A worker that grabs one of these tasks NEVER becomes
    "idle" for the pool again until the barrier has released everyone —
    so it can structurally never absorb a second one before the pool has
    been forced to spawn one process per remaining task (no available
    worker can take it). `generate_grid` submits the `PARALLEL_ATTEMPTS`
    tasks in a single batch then waits for all of them — the barrier
    guarantees this is only possible once `PARALLEL_ATTEMPTS` DISTINCT
    processes have genuinely started, each one necessarily having already
    run `_init_worker` to be able to answer this task — so the first
    palier can only start once every process is genuinely ready, and the
    distribution goes back to 1:1 from palier 1 onward, not only from
    whichever palier the pool eventually stabilizes at on its own.

    This warm-up remains useful even after the displayed number stopped
    being PID-based (see `_build_dispatch_lineage`): it still guarantees
    that `PARALLEL_ATTEMPTS` distinct processes genuinely exist before
    the first palier, a condition the search itself (genuine work
    distribution) still benefits from, independent of whatever the
    display numbers."""
    # `timeout` (60s, comfortably enough even on a heavily loaded machine
    # for `PARALLEL_ATTEMPTS` Python interpreters to start) avoids an
    # eternal hang if the machine can structurally never run
    # `PARALLEL_ATTEMPTS` processes at once — in that case `threading.
    # BrokenBarrierError` propagates up to the parent's `.result()` call
    # (see generate_grid, which catches it).
    _worker_warmup_barrier.wait(timeout=60)
    return os.getpid()


def _init_worker(index, cancel_event=None, batch_abandoned_event=None, attempt_done_event=None,
                  best_state_queue=None, warmup_barrier=None, proper_noun_words=None,
                  max_proper_nouns=None, non_gloss_words=None, max_non_gloss=None,
                  priority_words=None, challenge_words=None):
    # See GENERATION_PROCESS_NICE_INCREMENT (right after PARALLEL_ATTEMPTS)
    # for the full reasoning — applied only once here, the very first time
    # this worker starts up (never per submitted task), since the pool
    # reuses the same process for the whole duration of the generate_
    # grid() call: a POSIX process's niceness persists until it ends, no
    # need to reapply it on every attempt. `os.nice()` is a pure addition
    # to the niceness already in effect (never an absolute replacement) —
    # called only once per worker, it can therefore never accumulate from
    # one call to the next. Wrapped in a defensive `try/except`:
    # `os.nice()` can fail on a platform where it isn't available or
    # under unexpected local restrictions, which must never prevent the
    # worker from starting — the low system priority is an optimization,
    # not a functioning requirement.
    if GENERATION_PROCESS_NICE_INCREMENT:
        try:
            os.nice(GENERATION_PROCESS_NICE_INCREMENT)
        except OSError:
            pass
    global _worker_index, _worker_cancel_event, _worker_batch_abandoned_event, \
        _worker_attempt_done_event, _worker_best_state_queue, _worker_warmup_barrier, \
        _worker_proper_noun_words, _worker_max_proper_nouns, \
        _worker_non_gloss_words, _worker_max_non_gloss, _worker_priority_words, \
        _worker_challenge_words
    _worker_index = index
    _worker_priority_words = priority_words
    _worker_challenge_words = challenge_words
    _worker_cancel_event = cancel_event
    _worker_batch_abandoned_event = batch_abandoned_event
    _worker_attempt_done_event = attempt_done_event
    _worker_best_state_queue = best_state_queue
    _worker_warmup_barrier = warmup_barrier
    _worker_proper_noun_words = proper_noun_words
    _worker_max_proper_nouns = max_proper_nouns
    _worker_non_gloss_words = non_gloss_words
    _worker_max_non_gloss = max_non_gloss


# ---------- Floating-black-cell widening for "Mots Défi"/theme words ----------
#
# At the user's explicit request: when a "Mots Défi" (challenge) word has no
# slot of its own length anywhere in a freshly generated pattern, look for a
# "floating" black cell — one not protected by `permanent_black_cells`, whose
# relocation to the far side of the word still leaves the grid structurally
# valid (`is_structurally_valid`'s relaxed `min_interior_free=1` threshold,
# the same bar `minimize_black_squares` already uses for the same kind of
# cell) — and relocate it there instead of its current position, carving out
# a right-sized empty slot before the CSP search even starts. The theme
# glossary (`priority_words`) gets the exact same treatment, tried only after
# every challenge word has had its turn (see `_pattern_attempt`'s own call).
# This only ever touches cells with no word on either side yet (the pattern
# is examined before any `Filler` exists), so "no damage to an already-
# placed word" is true by construction — never a mid-search operation, and
# never applied to `_pattern_continue` (a continuation's pattern already
# carries real placed words on some of its slots; see that function's own
# docstring for why it never calls `make_pattern` again either). Once a
# right-sized slot exists, the ALREADY-existing `_select_target_slot`/
# candidate-priority cascade (see CLAUDE.md's "Grid generation" section)
# picks it up and places the word on its own — this widening step never
# writes a single letter itself, only reshapes the black-cell pattern.

WIDEN_BLACK_CELL_WINDOW = 40
WIDEN_PRIORITY_WORDS_LIMIT = 30
WIDEN_MAX_SUCCESSFUL = 6

# The "shorten" fallback (see `_shorten_one_slot_for_word` below) scans, per
# still-unplaced word, up to this many existing empty slots strictly longer
# than the word itself — the same 10% fraction (`FALLBACK_PHASE_BUDGET_
# FRACTION`) the crossing-safety retry mechanism already spends on a
# challenge/theme word before giving up on it, applied here to the widening
# scan's own window (`WIDEN_BLACK_CELL_WINDOW`) rather than to a
# `deadline_checks` budget that doesn't exist yet at this pre-search stage.
SHORTEN_SLOT_WINDOW = max(1, round(FALLBACK_PHASE_BUDGET_FRACTION * WIDEN_BLACK_CELL_WINDOW))


def _white_run(grid, rows, cols, r, c, dr, dc):
    """Contiguous WHITE cells starting one step from (r, c) in direction
    (dr, dc), stopping at the first non-WHITE cell or the grid border — same
    walk as the nested `_run_cells` closure inside
    `_new_black_cell_breaks_locked_slot`, factored out at module scope here
    since this side needs it from a plain black cell, not from inside a
    method."""
    cells = []
    rr, cc = r + dr, c + dc
    while 0 <= rr < rows and 0 <= cc < cols and grid[rr][cc] == WHITE:
        cells.append((rr, cc))
        rr += dr
        cc += dc
    return cells


def _slot_has_domain(cells, index, locked_letters):
    """True if at least one real dictionary word can still fill `cells`,
    given whichever of them are already in `locked_letters` — a
    lightweight, `Filler`-independent cousin of `Filler._domain` (no
    crossing-assignment/forced-letter awareness needed here, only the
    plain already-known facts), used by `_perpendicular_slot_stays_valid`
    below. `cells` need not be one of `index`'s own registered slots."""
    idx = index.for_cells(cells).get(len(cells))
    if idx is None:
        return False
    constraints = {
        pos: locked_letters[cell] for pos, cell in enumerate(cells) if cell in locked_letters
    }
    if not constraints:
        return True
    sets = []
    for pos, ch in constraints.items():
        s = idx["pos"][pos].get(ch)
        if not s:
            return False
        sets.append(s)
    sets.sort(key=len)
    result = sets[0]
    for s in sets[1:]:
        result = result & s
        if not result:
            return False
    return bool(result)


def _perpendicular_slot_stays_valid(grid, rows, cols, r, c, dr, dc, index, locked_letters):
    """Checks, along the axis PERPENDICULAR to `(dr, dc)` (a 90° rotation,
    `(dc, dr)`), that touching `(r, c)` — freeing it if it is currently
    BLACK, or blackening it if currently WHITE — can never turn an
    existing or resulting perpendicular slot into one with no real
    dictionary candidate left, given whatever is already known there.
    Used by `_try_widen_black_cell` for both `(r, c)` itself and `new_
    black`, at the user's explicit request: "Le placement des Mots Défi
    doit se faire en respectant les règles fondamentales du placement
    d'un mot (ne pas créer d'emplacement impossible)." Neither `is_
    structurally_valid` (shape-only, no notion of "known letter" or
    "dictionary word") nor the existing `locked_letters` span check
    (only ever checks the widened word's OWN cells) can catch this: only
    this perpendicular check does. `locked_letters` must already include
    whatever letter `(r, c)` itself is about to receive as part of the
    widened word, when checking the freed-cell side (see the caller). A
    no-op (always True) when `locked_letters` is empty (`_pattern_
    attempt`'s own ordinary, letter-free caller)."""
    if not locked_letters:
        return True
    pdr, pdc = dc, dr
    before = list(reversed(_white_run(grid, rows, cols, r, c, -pdr, -pdc)))
    after = _white_run(grid, rows, cols, r, c, pdr, pdc)
    if grid[r][c] == BLACK:
        # About to turn WHITE: `(r, c)` merges into one combined slot
        # together with both perpendicular neighbor runs — too short to be
        # a real (>=2-cell) slot at all is always safe (nothing to break).
        cells = before + [(r, c)] + after
        return len(cells) < 2 or _slot_has_domain(cells, index, locked_letters)
    # About to turn BLACK: the combined run through `(r, c)` (which used
    # to include it) is cut into up to two independent pieces — each,
    # if still long enough to be a real slot, must keep a real candidate.
    return (
        (len(before) < 2 or _slot_has_domain(before, index, locked_letters))
        and (len(after) < 2 or _slot_has_domain(after, index, locked_letters))
    )


def _flatten_priority_words(words):
    """The flat union of a priority-word collection, whether a plain
    frozenset (monolingual) or a `DualSet` (bilingual, one glossary per
    direction) — only used here to decide which words are worth trying to
    widen a slot for; the actual placement afterward still resolves the
    right per-direction glossary itself (`_priority_words_for`), so getting
    this pre-check exactly right for a bilingual grid isn't required."""
    if not words:
        return frozenset()
    if isinstance(words, DualSet):
        return frozenset(words.across) | frozenset(words.down)
    return frozenset(words)


def _try_widen_black_cell(grid, rows, cols, r, c, dr, dc, word, locked_letters, index):
    """Tries to relocate the black cell at (r, c) to make room for `word`
    along the (dr, dc) axis, using the freed cell itself plus whichever side
    the word needs to reach. On success, mutates `grid` in place (leaving
    the mutation applied) and returns a dict describing exactly what
    changed — `{"freed_cell": (r, c), "new_black_cell": new_black_or_None,
    "slot_cells": tuple(span)}` — so a caller that needs to undo this one
    reshape later (see `interactive_place_word`'s own revert-if-unused
    pass) can do so precisely, without touching any other cell. Returns
    `None` and leaves `grid` untouched otherwise.

    `new_black` (the one extra cell beyond the word's own span that has to
    absorb the relocated black cell) must never be a cell already in
    `locked_letters` — on `_pattern_attempt`'s own ordinary caller this
    never matters (that pattern carries no letters at all yet), but the
    same function is also reused, unmodified, by Interactive mode's
    "Suivant" (`interactive_place_word`, at the user's explicit request:
    "Suivant doit utiliser le même code que l'inférence automatique"),
    where `grid` cells outside the word's own span can genuinely already
    hold a real, previously-placed letter — without this guard, widening
    could silently blacken (and so destroy) an already-placed crossing
    letter that happens to sit right next to the relocated black cell.

    Two more checks (`_perpendicular_slot_stays_valid`), for the same
    Interactive-mode reason, refuse any relocation that would turn a
    PERPENDICULAR slot — the one crossing `(r, c)` itself once it takes
    its own letter from `word`, or the one crossing `new_black` once it's
    split/shortened by it — into one with no real dictionary candidate
    left, given whatever is already known there — at the user's explicit
    request: "Le placement des Mots Défi doit se faire en respectant les
    règles fondamentales du placement d'un mot (ne pas créer d'emplacement
    impossible)." Without them, freeing `(r, c)` could silently attach a
    stray extra cell onto an already-placed crossing word (leaving it with
    a trailing blank cell no black cell ever closes) or form a new,
    unfillable short crossing slot, and blackening `new_black` could
    silently truncate an existing crossing word into an invalid one —
    none of this is visible to `is_structurally_valid` below, which only
    ever reasons about the black/white shape, never about which cells
    already carry a real letter or what the dictionary actually allows."""
    before = list(reversed(_white_run(grid, rows, cols, r, c, -dr, -dc)))
    after = _white_run(grid, rows, cols, r, c, dr, dc)
    merged = before + [(r, c)] + after
    total = len(merged)
    word_len = len(word)
    if word_len > total:
        return None
    rc_index_in_merged = len(before)
    starts = set()
    if word_len > len(before):
        starts.add(0)
    if word_len > len(after):
        starts.add(total - word_len)
    for start in starts:
        span = merged[start:start + word_len]
        if any(locked_letters.get(cell, letter) != letter
               for cell, letter in zip(span, word)):
            continue
        new_black = None
        if start == 0:
            if word_len < total:
                new_black = merged[word_len]
        else:
            new_black = merged[start - 1]
        if new_black is not None and new_black in locked_letters:
            continue
        rc_letter = word[rc_index_in_merged - start]
        rc_locked_letters = {**locked_letters, (r, c): rc_letter}
        if not _perpendicular_slot_stays_valid(grid, rows, cols, r, c, dr, dc, index, rc_locked_letters):
            continue
        if new_black is not None and not _perpendicular_slot_stays_valid(
            grid, rows, cols, new_black[0], new_black[1], dr, dc, index, locked_letters,
        ):
            continue
        saved_new_black = grid[new_black[0]][new_black[1]] if new_black else None
        grid[r][c] = WHITE
        if new_black:
            grid[new_black[0]][new_black[1]] = BLACK
        if is_structurally_valid(grid, rows, cols, min_interior_free=1):
            return {"freed_cell": (r, c), "new_black_cell": new_black, "slot_cells": tuple(span)}
        grid[r][c] = BLACK
        if new_black:
            grid[new_black[0]][new_black[1]] = saved_new_black
    return None


def _widen_one_floating_black_cell(grid, rows, cols, rng, word,
                                    locked_letters, permanent_black_cells, index):
    """Scans up to `WIDEN_BLACK_CELL_WINDOW` shuffled floating black cells
    (never one in `permanent_black_cells`), across then down, for one whose
    relocation makes room for `word`. Returns `_try_widen_black_cell`'s own
    change-description dict on the first success, `None` if none of the
    scanned cells works."""
    black_cells = [
        (r, c) for r in range(rows) for c in range(cols)
        if grid[r][c] == BLACK and (r, c) not in permanent_black_cells
    ]
    rng.shuffle(black_cells)
    for (r, c) in black_cells[:WIDEN_BLACK_CELL_WINDOW]:
        for dr, dc in ((0, 1), (1, 0)):
            change = _try_widen_black_cell(grid, rows, cols, r, c, dr, dc, word, locked_letters, index)
            if change is not None:
                return change
    return None


def _try_shorten_slot(grid, rows, cols, cells, word, locked_letters, index):
    """Tries to carve a right-sized slot for `word` out of an existing empty
    slot `cells` that is strictly longer than it, by turning one interior
    cell black right past the word's own end — the mirror operation of
    `_try_widen_black_cell` above (which grows a run by relocating an
    existing black cell) rather than a relocation: no black cell moves here,
    a brand new one is inserted into what was, until now, entirely open
    space. Tries `word` flush against both ends of `cells` (start and end),
    same as `_try_widen_black_cell`'s own two `starts`. On success, mutates
    `grid` in place (leaving the mutation applied) and returns a dict
    describing the change — `{"freed_cell": None, "new_black_cell":
    new_black, "slot_cells": tuple(span)}` (`freed_cell` is always `None`
    here, unlike `_try_widen_black_cell`'s own return: no existing black
    cell moves, only a brand new one is inserted) — in the same shape
    `_try_widen_black_cell` returns, so a caller can undo either kind of
    reshape identically (see `interactive_place_word`'s own revert-if-
    unused pass). Returns `None` and leaves `grid` untouched otherwise.

    `new_black` (the interior cell right past the word's own span) must
    never be a cell already in `locked_letters` — reused, unmodified, by
    Interactive mode's "Suivant" the same way `_try_widen_black_cell` is,
    where `cells` can genuinely already carry real crossing letters outside
    the word's own span. `_perpendicular_slot_stays_valid` guards `new_
    black` the same way it already guards `_try_widen_black_cell`'s own
    `new_black`: blackening it must never turn the PERPENDICULAR slot
    crossing it into one with no real dictionary candidate left, given
    whatever is already known there — at the user's explicit request that
    this mechanism, like the widening one it complements, must never create
    an impossible zone by itself."""
    total = len(cells)
    word_len = len(word)
    if word_len >= total or word_len < 2:
        return None
    dr = cells[1][0] - cells[0][0]
    dc = cells[1][1] - cells[0][1]
    for start, new_black in ((0, cells[word_len]), (total - word_len, cells[total - word_len - 1])):
        if new_black in locked_letters:
            continue
        span = cells[start:start + word_len]
        if any(locked_letters.get(cell, letter) != letter
               for cell, letter in zip(span, word)):
            continue
        if not _perpendicular_slot_stays_valid(
            grid, rows, cols, new_black[0], new_black[1], dr, dc, index, locked_letters,
        ):
            continue
        saved_new_black = grid[new_black[0]][new_black[1]]
        grid[new_black[0]][new_black[1]] = BLACK
        if is_structurally_valid(grid, rows, cols, min_interior_free=1):
            return {"freed_cell": None, "new_black_cell": new_black, "slot_cells": tuple(span)}
        grid[new_black[0]][new_black[1]] = saved_new_black
    return None


def _shorten_one_slot_for_word(grid, rows, cols, rng, word, locked_letters, index):
    """Scans up to `SHORTEN_SLOT_WINDOW` shuffled existing empty slots
    strictly longer than `word`, for one where `word` can be cased flush at
    the start or the end (`_try_shorten_slot`). Returns `_try_shorten_
    slot`'s own change-description dict on the first success — the mirror
    fallback of `_widen_one_floating_black_cell`: instead of growing a run
    to fit a longer word, this shrinks a longer run down to fit a shorter
    one. Returns `None` if none of the scanned slots works."""
    word_len = len(word)
    slots = [cells for cells in extract_slots(grid, rows, cols) if len(cells) > word_len]
    rng.shuffle(slots)
    for cells in slots[:SHORTEN_SLOT_WINDOW]:
        change = _try_shorten_slot(grid, rows, cols, cells, word, locked_letters, index)
        if change is not None:
            return change
    return None


def _slots_by_length(grid, rows, cols):
    """`extract_slots(grid, rows, cols)`, grouped by each slot's own length —
    the lookup shape `_has_free_matching_slot` needs."""
    by_length = {}
    for cells in extract_slots(grid, rows, cols):
        by_length.setdefault(len(cells), []).append(cells)
    return by_length


def _has_free_matching_slot(by_length, word, locked_letters, claimed):
    """True if some EMPTY slot of exactly `len(word)` is both letter-
    compatible with `word` (honoring any letter already locked in it) and
    not already claimed by an earlier word of the same length in this same
    scan (`claimed`, keyed by the slot's own cell tuple) — in that case the
    ordinary slot-selection/candidate cascade can be trusted to route `word`
    there on its own, with no black-cell reshaping needed for it. Unlike a
    bare length check, this rules out both a same-length slot two Mots
    Défi/theme words would otherwise both assume is "theirs" (only one of
    them can ever actually land there) and a same-length slot whose already-
    locked letters don't even spell `word` in the first place. Claims the
    slot in place on a match, so the caller can reuse `claimed` across every
    word of one glossary group in one pass."""
    for cells in by_length.get(len(word), ()):
        key = tuple(cells)
        if key in claimed:
            continue
        if all(locked_letters.get(cell, letter) == letter
               for cell, letter in zip(cells, word)):
            claimed.add(key)
            return True
    return False


def _widen_floating_black_cells_for_priority_words(
    grid, rows, cols, rng, priority_word_groups, index,
    locked_letters=None, permanent_black_cells=None,
):
    """Runs `_widen_one_floating_black_cell` for every word of every group in
    `priority_word_groups`, in order — "Mots Défi" first, the theme glossary
    second, at the user's explicit request: "Ne placer des mots autres que
    Mots Défi ou Thématique que quand on a épuisé les possibilités de
    manipuler des cases noires flottantes" (only the ordinary dictionary
    domain is placed once this has run its course; that part is already true
    by construction, since this only ever runs before the CSP search even
    starts). A word is skipped only when a genuinely free, letter-compatible
    EMPTY slot of its own exact length is still available for it
    (`_has_free_matching_slot`) — no need to reshape the grid for it, the
    ordinary cascade already handles it; a slot whose length merely matches
    somewhere in the grid, without being reachable/compatible/unclaimed by a
    sibling word, still gets a genuine widening/shortening attempt. `index`
    (a `DualIndex`) grounds the perpendicular-slot safety check inside
    `_try_widen_black_cell` — see its own docstring — in the real
    dictionary; harmless busywork when `locked_letters` is empty
    (`_pattern_attempt`'s own ordinary caller), since that check is then a
    no-op regardless.

    Once every word of one glossary group has had its own widening attempt,
    at the user's explicit request, a second pass (`_shorten_one_slot_for_
    word`) runs over whichever of that same group's words are still
    unplaced, before moving on to the next glossary group: rather than
    relocating a black cell to grow a run up to the word's own length, it
    looks for an existing empty slot already longer than the word and casts
    a new black cell partway through it, flush against either end, to carve
    out a right-sized slot without touching any black cell at all. This
    keeps each glossary tier's own best-effort search complete (grow, then
    shrink) before the next, lower-priority group ever gets a turn. Mutates
    `grid` in place; returns the list of successful reshapes, each
    `{"word": word, **change}` where `change` is whatever `_try_widen_
    black_cell`/`_try_shorten_slot` themselves returned on success (see
    either docstring) — `_pattern_attempt`'s own ordinary caller ignores
    this return value (every reshape made on a still-blank pattern is kept
    unconditionally), but `interactive_place_word` uses it to undo whichever
    of this call's reshapes don't end up backing the one word actually
    placed this round (see that function's own revert-if-unused pass, at
    the user's explicit request: "les tentatives de cases noires flottantes
    et ajout de mot plus court avec nouvelle case noire doivent être
    annulés si ça n'aboutit pas")."""
    locked_letters = locked_letters or {}
    permanent_black_cells = permanent_black_cells or set()
    by_length = _slots_by_length(grid, rows, cols)
    reshapes = []
    successful = 0
    for words in priority_word_groups:
        if successful >= WIDEN_MAX_SUCCESSFUL:
            break
        flat = _flatten_priority_words(words)
        claimed = set()
        pending = [
            w for w in flat
            if len(w) >= 2 and not _has_free_matching_slot(by_length, w, locked_letters, claimed)
        ]
        if not pending:
            continue
        rng.shuffle(pending)
        still_pending = []
        for word in pending[:WIDEN_PRIORITY_WORDS_LIMIT]:
            if successful >= WIDEN_MAX_SUCCESSFUL:
                break
            if _has_free_matching_slot(by_length, word, locked_letters, claimed):
                continue
            change = _widen_one_floating_black_cell(grid, rows, cols, rng, word,
                                                      locked_letters, permanent_black_cells, index)
            if change is not None:
                successful += 1
                reshapes.append({"word": word, **change})
                by_length = _slots_by_length(grid, rows, cols)
            else:
                still_pending.append(word)
        for word in still_pending:
            if successful >= WIDEN_MAX_SUCCESSFUL:
                break
            if _has_free_matching_slot(by_length, word, locked_letters, claimed):
                continue
            change = _shorten_one_slot_for_word(grid, rows, cols, rng, word, locked_letters, index)
            if change is not None:
                successful += 1
                reshapes.append({"word": word, **change})
                by_length = _slots_by_length(grid, rows, cols)
    return reshapes


def _pattern_attempt(rows, cols, ratio, seed, force_letters_fraction=0.0,
                      seed_grid=None, locked_letters=None,
                      black_enrichment_fraction=POST_PREFILL_BLACK_FRACTION,
                      deadline_checks=None, permanent_locked_letters=None,
                      permanent_black_cells=None, required_cells=None):
    """Une tentative indépendante (motif + remplissage CSP complet), exécutée
    dans un processus worker séparé — voir PARALLEL_ATTEMPTS/generate_grid().
    Each attempt has its own `random.Random(seed)`, derived from the
    global seed by the caller, to stay reproducible while differing from
    the other attempts of the same palier. Returns (grid, result,
    diagnostics); `result` is None on failure, the same contract as
    try_fill.

    `deadline_checks` (`None` by default) is passed straight through to
    `try_fill` — see `generate_grid`'s own docstring for where this value
    comes from (the web UI's "Mode" selector).

    `seed_grid`/`locked_letters` (both `None` by default — every call
    that existed before this feature keeps starting from a blank grid,
    with no letter already known), at the user's explicit request: a
    starting point built by `_build_retry_seed` from the previous
    palier's own best failed attempt (see generate_grid) — `make_pattern`
    keeps placing black cells on top of `seed_grid` rather than starting
    over from a blank grid (with `locked_letters` excluded from its own
    candidate pool, so a already-confirmed letter is never overwritten),
    and `locked_letters` is merged into `forced_letters`, overwriting any
    statistical hint already present at the same cell (`{**forced_
    letters, **locked_letters}`: a letter confirmed by a previous search
    is a fact, not a guess — it always wins over `sample_letter_biases`'s
    own statistical sampling, never the other way around).

    Before launching the real fill on this freshly chosen pattern, at the
    user's explicit request: a statistical sampling (`sample_letter_
    biases`) always runs, **unconditionally**, whatever `force_letters_
    fraction` is (including at 0.0, the default setting) — this same
    sampling supplies both `forced_letters` (letter hints, see Filler.
    _domain) and `letter_scores` (the full per-letter, per-cell scores
    _backtrack uses to sort then draw its candidate words, see Filler.
    _candidate_score), and only the first of the two genuinely depends on
    `force_letters_fraction`: at 0.0, `sample_letter_biases` returns an
    empty `forced_letters` (its own computation of how many cells to
    force gives exactly zero in that case — see its docstring), while
    `letter_scores` itself always stays fully populated, at the user's
    explicit request — sorting candidate words by statistical consistency
    never needed to be conditioned on any letter actually being forced.
    `locked_impossible_slots` (computed just before, see above) is passed
    to it as `excluded_slots` — at the user's explicit request, so no
    seed is ever placed on a slot already known impossible (fully locked,
    but with no matching real word).

    `black_enrichment_fraction` (default `POST_PREFILL_BLACK_FRACTION`,
    see its own definition) is passed straight through to `make_pattern`
    — tunable from the web UI (see generate_grid), at the user's explicit
    request.

    `permanent_locked_letters` (`None` by default — no effect for any
    pre-existing caller before "Finir la grille", see `generate_grid`'s
    own docstring) is merged into `locked_letters` even before
    `make_pattern`, independently of whatever `carry_locked_letters`
    otherwise holds for this palier (possibly `None`, or already
    different — see generate_grid's "reprise telle quelle" branch): these
    cells never get a black cell, and their word is never revalidated
    against the dictionary during pre-fill.

    `permanent_black_cells` (`None`/empty by default — no effect for any
    pre-existing caller before "Finir la zone"): a real bug found by a
    live audit (a cell meant to stay black forever came back, in the
    final grid, with a LETTER instead). Root cause: a "reset" worker
    (`FULL_RESET_ATTEMPT_COUNT`, `seed_grid=None` — see the two call
    sites in `generate_grid`) starts from an entirely blank grid with no
    knowledge of these cells at all, which then become ordinary
    candidates for `make_pattern`, exactly like any other white cell —
    nothing distinguishes them anymore once such a worker wins and its
    own pattern becomes the next palier's `carry_seed_grid`: the
    protection provided elsewhere (`_build_retry_seed`'s "any cell
    already black in the entry `seed_grid` stays protected", `_optimize_
    before_cleanup`/`_lengthen_impossible_zones`'s own exclusion of
    `permanent_black_cells`) all assume these cells are ALREADY black in
    the pattern they receive — an assumption that no longer holds once a
    reset has erased it. Fixed here, at the source, rather than in each
    of these downstream callers: if `permanent_black_cells` is given,
    `seed_grid` (a fresh blank grid if `None`, a defensive copy
    otherwise) receives these cells as black BEFORE it's even passed to
    `make_pattern` — a reset worker then behaves, for these exact cells,
    exactly as if it had never been reset: `make_pattern` never considers
    a cell already black in its own `seed_grid` as a candidate (its
    candidate pool only ever keeps still-white cells), so they stay black
    on THIS worker, and so on any `carry_seed_grid` that inherits from it
    afterward.

    `make_pattern` itself receives `available_lengths` (the lengths with
    at least `PREFILL_MIN_WORD_COUNT` words in `_worker_index`, not just
    a single one — see its own definition) for its own pre-fill phase
    (see `_prefill_unfillable_slots`) — derived here, once per attempt,
    rather than precomputed on `generate_grid`'s own side (a negligible
    cost: `_worker_index` only has a handful of distinct lengths)."""
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
    # `permanent_locked_letters` (`None` by default — no effect for any
    # pre-existing caller before "Finir la grille", see generate_grid's
    # own docstring) merged in here, BEFORE `make_pattern`, so no black
    # cell placement by THIS palier can ever cover a cell placed by the
    # user themselves in Interactive mode — whatever state `carry_locked_
    # letters` otherwise holds from the previous palier (possibly `None`,
    # or already different — see generate_grid's "reprise telle quelle"
    # branch, which resets it at every palier).
    if permanent_locked_letters:
        locked_letters = {**(locked_letters or {}), **permanent_locked_letters}
    # `permanent_black_cells` (see the docstring above for the real bug
    # this fixes): forced black in `seed_grid` even before `make_pattern`,
    # including — especially — for a "reset" worker (`seed_grid` received
    # as `None`), which then receives a fresh blank grid instead of a
    # literal `None`, solely to carry these cells.
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
    # Floating-black-cell widening for "Mots Défi"/theme words — see that
    # section's own docstring just above `_pattern_attempt`. Runs on this
    # freshly generated pattern, before any word exists anywhere in it, so
    # it can never damage an already-placed word; "Mots Défi" tried before
    # the theme glossary, at the user's explicit request.
    if _worker_challenge_words or _worker_priority_words:
        _widen_floating_black_cells_for_priority_words(
            grid, rows, cols, rng,
            (_worker_challenge_words, _worker_priority_words), _worker_index,
            locked_letters=locked_letters, permanent_black_cells=permanent_black_cells,
        )
    # Retrieves, even before launching the search (and even before the
    # sample_letter_biases sampling below — see right after), the word
    # already entirely determined by `locked_letters` for every slot
    # whose cells are ALL locked — at the user's request, after observing
    # live that a very full grid could drop back to `assigned=0` right at
    # the very first palier following a cleanup: without this pre-fill,
    # these already-known words only ever counted as "assigned" (`Filler.
    # best_assignment`) if `_backtrack` ended up explicitly selecting them
    # — and an instant failure elsewhere in the grid (`checks=1`, a
    # different slot already impossible) prevented the search from ever
    # reaching them, throwing away all the work already done by the
    # previous cleanup. Validates every recomposed word against the
    # dictionary (`_slot_candidate_count`, the same per-position
    # intersection as `Filler._domain`) before pre-assigning it — a
    # combination of locked letters matching no real word (a genuinely
    # impossible slot, not just one not yet tried) must stay `None` here:
    # it will then naturally be rediscovered by `try_fill` (empty domain)
    # and surface in `impossible_slots`, exactly like an already-known
    # blocked slot — wrongly pre-assigning it would instead make it
    # disappear from this diagnostic. Reuses `extract_slots` — the same
    # computation `try_fill` redoes internally anyway, no shared state
    # between the two calls worth saving here. `locked_impossible_slots`
    # (the fully locked slots whose combination is invalid) is computed
    # here, before sample_letter_biases, specifically to be passed to it —
    # at the user's explicit request: "les graines ne doivent être
    # placées que sur des emplacements réputés jouables (si possible),
    # donc, non verrouillés comme injouables."
    # Even before computing preseed_assignment or the statistical seed
    # sampling below, at the user's explicit request: "quand un
    # emplacement valide ne possède plus qu'une seule possibilité de mot,
    # forcer les lettres restantes pour placer ce mot." Called
    # unconditionally (not just `if locked_letters:`) — even with no
    # letter known at all to start with, a length whose dictionary has
    # only a single word at all (a real case in this project, see
    # `available_lengths`/`PREFILL_MIN_WORD_COUNT` above) is already, on
    # its own, a "single possibility" to force. `locked_letters or {}`:
    # `_force_single_candidate_slots` always returns a dict (never
    # `None`), so `locked_letters` becomes a genuine dict here regardless
    # — the `if locked_letters:` checks further down keep working
    # identically (an empty dict is still "false"), no regression for the
    # case where nothing could be deduced.
    slots = extract_slots(grid, rows, cols)
    locked_letters = _force_single_candidate_slots(slots, _worker_index, locked_letters or {})

    preseed_assignment = None
    locked_impossible_slots = set()
    if locked_letters:
        preseed_assignment = [None] * len(slots)
        for i, cells in enumerate(slots):
            if all(cell in locked_letters for cell in cells):
                word = "".join(locked_letters[cell] for cell in cells)
                # A slot entirely covered by `permanent_locked_letters`
                # (see generate_grid's own docstring) is always
                # pre-assigned as-is, without ever querying the
                # dictionary — these letters, placed by the user
                # themselves in Interactive mode, must be treated as
                # correct whatever word they spell (probably a proper
                # noun): leaving it at `None` here would make
                # `truly_complete` fail forever on this grid, since this
                # slot would then never actually be assigned by
                # `_backtrack` again.
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
    # The old merge (`forced_letters = {**forced_letters, **locked_letters}`)
    # was removed at the user's explicit request, after a real bug
    # reported live: "les optimisations suivantes montrent les
    # emplacements non vides en bleu [.forced]... ce n'est pas normal."
    # Root-caused: this merge made `locked_letters` indistinguishable from
    # `forced_letters` for `build_partial_letters_grid` (see below,
    # `diagnostics["forced_cells"]` = `sorted(forced_letters)`) — every
    # genuinely locked cell (confirmed content, carried from one palier to
    # the next) therefore ended up wrongly listed as a mere statistical
    # seed. This was never visible before: `diagnostics["locked_cells"]`
    # (computed separately, from the same unmerged `locked_letters`)
    # already named the same cells, and the CSS cascade (`.locked`
    # declared after `.forced` in style.css) always made the orange
    # border win over the blue one for the ordinary preview — until
    # `_optimize_before_cleanup` (see its own docstring) computed ITS OWN
    # `locked_cells`, a different definition ("entirely empty slots"),
    # which no longer necessarily covers the same cells: the
    # contamination then became visible, with nothing left to override
    # it. Verified directly: `Filler._domain` (the only reader of `self.
    # forced_letters`) already consults `self.locked_letters` first,
    # unconditionally, before ever considering `self.forced_letters` as a
    # fallback — this merge was therefore never necessary for the search
    # itself since `Filler` started distinguishing the two separately
    # (see its own history); in practice it no longer served any purpose
    # other than contaminating this diagnostic.
    diag = {}
    # `batch_abandoned_event` always `None` here, never `_worker_batch_
    # abandoned_event` — deliberate, see that global variable's own
    # docstring for why (every attempt of this batch has its own
    # independent pattern; the shared signal only makes sense for
    # `_pattern_continue`, where the pattern is rigorously the same
    # everywhere).
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
                       priority_words=_worker_priority_words,
                       challenge_words=_worker_challenge_words,
                       required_cells=required_cells)
    return grid, result, diag


def _pattern_continue(rows, cols, seed, seed_grid, preseed_assignment, excluded_slots,
                       force_letters_fraction=0.0, deadline_checks=None,
                       permanent_locked_letters=None, required_cells=None):
    """Attempt at the "reprise telle quelle" (carry-forward-as-is) mechanism
    between paliers, at the user's explicit request ("New version") —
    runs in its own separate worker process, like _pattern_attempt, but
    NEVER calls make_pattern: `seed_grid` (the previous palier's own black/white
    pattern, chosen because at least one slot where a word could still be
    added remained — see generate_grid) is reused verbatim, with not a
    single black cell more or fewer.

    `deadline_checks` (`None` by default) is passed straight through to
    `try_fill` — see `generate_grid`'s own docstring for where this value
    comes from (the web UI's "Mode" selector).

    `preseed_assignment` (the previous palier's own assignment, a word or
    None per slot) locks in every already-filled slot as-is — `try_fill`
    initializes `Filler.assignment` (and used_words/best_assignment)
    directly from it instead of starting over from a blank grid.
    `excluded_slots` (the slots already identified as impossible at the
    previous palier, see `Filler.excluded_slots`) is left ignored by this
    search: "le tour N+1 doit ignorer les situations de blocage sur les
    cases verrouillées, et essayer de continuer à remplir la grille" —
    without this, `_backtrack`'s own plain domain check would fail the
    search on the very first call (`checks=1`), even for slots with
    nothing at all to do with the already-known blockage.

    Each parallel attempt of the same palier receives its own seed, like
    _pattern_attempt — `seed_grid`/`preseed_assignment`/`excluded_slots`
    stay, for ONE given call, rigorously identical from one `Filler`/
    `try_fill` call to the next inside this same search (nothing new to
    generate once this attempt is launched), only the exploration order
    differs (`sample_letter_biases`'s own statistical sampling, `_
    backtrack`'s own candidate-word sorting/drawing): enough for several
    parallel attempts, starting from the same point, to reach different
    states of progress.

    This no longer means, since the `carry_seed_pool_continue` pool
    exists (see `generate_grid`), that ALL parallel attempts of the same
    "reprise telle quelle" palier necessarily receive the same `(seed_
    grid, preseed_assignment, excluded_slots)` triple — at the user's
    explicit request ("chaque process doit repartir à l'étape suivante
    avec sa grille partiellement nettoyée"), the parent can now dispatch a
    different pool entry to each non-reset attempt of the same palier;
    only an *individual* attempt (a single call to this function) keeps a
    fixed starting point for itself.

    `required_cells` (`None` by default — no effect for any pre-existing
    caller) is passed straight through to `try_fill`, see its own
    docstring: for "Finir la zone", this lets a "reprise telle quelle"
    palier ALSO succeed directly, the moment every cell of the selected
    zone is genuinely resolved — the excluded/still-`None` slots reported
    below then simply stay as they are, exempt from ever needing to be
    "closed" by a further palier.

    A complete `try_fill` (`truly_complete`, see its docstring) implies
    here that even the excluded slots ended up filled — impossible as
    long as they stay in `excluded_slots` (never assigned by
    construction), so `result` is always None here UNLESS `required_cells`
    is given and every excluded (or otherwise unresolved) slot happens to
    touch none of it (see below) — this function's only other useful
    output is `diag` (up-to-date assignment/impossible_slots),
    which generate_grid re-examines to decide whether a slot still
    remains where a word could be added (in which case "reprise telle-
    quelle" continues at the next palier, with a possibly widened
    `excluded_slots`) or whether it's a genuine total blockage (no
    non-excluded slot has a non-empty domain left), in which case the
    next palier goes back through the existing cleanup (`_build_retry_
    seed`) and a fresh pattern."""
    rng = random.Random(seed)
    # Letters already known for certain at this point (see `known_letters`
    # in `sample_letter_biases`'s own docstring): every slot already
    # entirely filled by `preseed_assignment` — locked in as-is, never
    # called into question by this search (see above). A second call to
    # `extract_slots` on the same black/white pattern (already recomputed
    # by `try_fill` right below anyway) — a cheap computation, not worth
    # threading through as an extra parameter just to avoid it here.
    slots = extract_slots(seed_grid, rows, cols)
    known_letters = {
        cell: letter
        for cells, word in zip(slots, preseed_assignment)
        if word is not None
        for cell, letter in zip(cells, word)
    }
    # `permanent_locked_letters` (`None` by default — no effect for any
    # pre-existing caller before "Finir la grille", see generate_grid's
    # own docstring) always merged here, UNCONDITIONALLY — even if the
    # previous palier's own cleanup left `preseed_assignment[i]` at
    # `None` for the slot they cover (harmless: the cell stays locked by
    # this dict regardless), so these letters stay a hard constraint of
    # THIS search too, whatever `preseed_assignment` otherwise says.
    if permanent_locked_letters:
        known_letters = {**known_letters, **permanent_locked_letters}
    # Before the seed statistical sampling, at the user's explicit
    # request (see _force_single_candidate_slots): forces any slot whose
    # already-known letters leave only a single real possibility in the
    # dictionary.
    known_letters = _force_single_candidate_slots(
        slots, _worker_index, known_letters, excluded_slots=excluded_slots,
    )
    # A slot freshly entirely determined by the deduction above (not just
    # by the original `preseed_assignment`) also becomes a real
    # assignment, not just a statistical hint — the same principle as
    # `_pattern_attempt`'s own pre-fill: without this promotion, `Filler.
    # _domain` would only see these letters as a hint (see `forced_
    # letters` below), never as the certainty they actually are.
    # Revalidated exactly like `_pattern_attempt` (`_slot_candidate_
    # count(...) > 0`) rather than simply assigned as-is: a slot can end
    # up entirely known purely through crossings, without `_force_
    # single_candidate_slots` itself ever having checked that this exact
    # combination matches a real word for ITS OWN length (its own pass
    # would then simply have ignored it as "already known", without
    # validating it) — left at `None` if invalid: `try_fill` will find it
    # on its own as an empty domain, exactly like any other blocked slot.
    # `excluded_slots` entries are never promoted this way, consistent
    # with `_force_single_candidate_slots`, which already never touches
    # them either.
    preseed_assignment = list(preseed_assignment)
    excluded = excluded_slots or set()
    for i, cells in enumerate(slots):
        if i in excluded or preseed_assignment[i] is not None:
            continue
        if all(cell in known_letters for cell in cells):
            word = "".join(known_letters[cell] for cell in cells)
            # Same exemption as _pattern_attempt: a slot entirely covered
            # by `permanent_locked_letters` is always promoted as-is,
            # never revalidated against the dictionary — these letters
            # are placed by the user themselves in Interactive mode and
            # must be treated as correct.
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
    # Merge removed at the user's explicit request — the same fix, the
    # same reasoning, as applied to `_pattern_attempt` (see its own
    # comment for the real bug reported and the full root cause). This
    # merge was justified by the original comment ("a letter deduced
    # here... would otherwise have no way to reach Filler as a real
    # constraint") back when `Filler` didn't yet receive its own
    # dedicated `locked_letters` parameter — that's no longer true since:
    # the `try_fill` call right below already passes `locked_letters=
    # known_letters` separately, and `Filler._domain` consults `self.
    # locked_letters` unconditionally before ever considering `self.
    # forced_letters` — any letter of `known_letters` therefore already
    # reaches `Filler` as a real constraint, merge or not.
    diag = {}
    # `batch_abandoned_event` always `None` here now — this was only true
    # as long as ALL parallel attempts of the same "reprise telle quelle"
    # palier rigorously shared the same `seed_grid`/`preseed_assignment`
    # (see this function's own docstring, and `_worker_batch_abandoned_
    # event` for this rule's full history). Since `carry_seed_pool_
    # continue` (see `generate_grid`), two parallel attempts of the same
    # palier can now receive DIFFERENT pool entries — a `_pattern_attempt`
    # (fresh pattern, for reset attempts) mixed with several `_pattern_
    # continue` attempts on distinct grids — so one attempt's "30% of MY
    # grid is impossible" conclusion no longer says anything reliable
    # about another attempt's potentially different grid in the same
    # palier: exactly the same reasoning, applied to the same global,
    # that already motivated disabling it for `_pattern_attempt` (see
    # right above) — disabled here too for the same reason, before a
    # real live failure ever confirmed it.
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
                       priority_words=_worker_priority_words,
                       challenge_words=_worker_challenge_words,
                       required_cells=required_cells)
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
                   permanent_black_cells=None, required_cells=None, challenge_words=None):
    """`challenge_words` (`None`/empty by default — no effect for any
    pre-existing caller): "Mots Défi", the same free-form, author-typed
    word list as Interactive mode's own panel (see `Filler.challenge_
    words`/`InteractiveStepRequest.challenge_words` in backend/app.py),
    threaded through the automatic CSP search so it applies "la même
    mécanique que Suivant" — a challenge word is given priority over both
    the theme glossary and the ordinary dictionary domain for whichever
    slot it geometrically fits (`Filler._backtrack`), and is never
    required to be a real dictionary entry itself. Always a plain
    frozenset, normalized via `challenge_word_grid_form` (bare, accent-
    stripped, uppercase — never filtered against the loaded lexicon,
    unlike `priority_words` right below, precisely because a challenge
    word is allowed to be absent from it) — never a DualSet: a challenge
    word carries no language, checked purely geometrically wherever it's
    tried. A best-effort mechanic, not a hard guarantee: unlike
    `permanent_locked_letters`, a challenge word's target cells are never
    reserved ahead of time in the pattern itself (`make_pattern`/
    `_prefill_unfillable_slots`, which run before any Filler exists, know
    nothing about it) — exactly the same limitation Interactive mode's
    own "Suivant" already has (no code path there reserves geometry for a
    challenge word ahead of time either); only its actual PLACEMENT, once
    a slot of the right shape exists, is favored. It is however protected
    once placed: `_optimize_before_cleanup`/`_shorten_impossible_zones`/
    `_lengthen_impossible_zones`/`_clean_continue_candidate`'s own calls
    to `_invalid_fully_known_indices` all additionally exempt a
    challenge word's cells (`_challenge_word_cells`), so a non-dictionary
    challenge word already placed by `_backtrack` survives cross-palier
    cleanup exactly like a `permanent_locked_letters` cell already does.

    `required_cells` (`None`/empty by default — no effect for any
    pre-existing caller) is passed straight through to every `_pattern_
    attempt`/`_pattern_continue` call (see `try_fill`'s own docstring for
    the full reasoning): the set of cells "Finir la zone" (backend/app.py's
    `interactive_finish`) actually needs resolved — every still-blank cell
    of the selected zone — to accept a palier as a genuine success, even
    while some OTHER slot elsewhere (entirely outside this set) stays
    unresolved. `result["words"]` never carries an entry for such an
    unresolved slot (`answer` would be `None` — never a genuine word, so
    it's dropped outright below, right after `build_word_entries`) —
    consistent with `_run_generate_job`'s own `preserved_clues`/
    `words_needing_clue`, which never sends anything but a real, complete
    word to the LLM.

    `permanent_black_cells` (`None`/empty by default — no effect for
    any pre-existing caller) — for the "Finir la zone" button (backend/
    app.py's `interactive_finish`), the set of cells converted to a
    permanent black cell because they're outside the selected zone.
    These cells are already, by construction, black in the `seed_grid`
    supplied via `resume_state` from the very first palier, which
    protects `_build_retry_seed` (the full nettoyage, which never
    reopens a cell already black in the palier's ENTRY `seed_grid` — see
    its own docstring) without needing to know about this parameter at
    all.

    But a `seed_grid` that's black at the start of a palier doesn't
    prevent, inside that very palier, another mechanism from removing/
    moving one of these cells before `_build_retry_seed` ever comes into
    play — a real bug reported live by the user: "le bouton 'Finir la
    zone' ne verrouille pas correctement les cases grisées, le
    remplissage automatique continue à essayer de les alimenter." Two
    places actually remove an already-placed black cell, neither of them
    ever knowing about this parameter before this fix: `_optimize_before_
    cleanup` (the optimization pass on every attempt of every palier,
    even before the ordinary cleanup — see its own docstring) and
    `_lengthen_impossible_zones` (lengthening an impossible slot by
    pushing back/removing one of its bordering black cells — see its own
    docstring), both reserved for "reprise telle quelle". Both now
    receive `permanent_black_cells` and explicitly exclude these cells
    from any removal/move, exactly like `minimize_black_squares` (see
    its own docstring) already does for its own final pass.

    This same parameter now also serves a SECOND reason, for "Finir la
    grille" alike "Finir la zone" (backend/app.py's `interactive_finish`):
    protecting the black cell immediately bordering a word that already
    carries a definition typed by the player (`preserved_clues`), so that
    definition never silently ends up attached to a word lengthened/
    merged by that same final optimization pass — see `interactive_
    finish`'s own `protected_black_cells`, at the user's explicit
    request: "il ne faut pas re-générer des définitions pour des
    emplacements qui en ont déjà une." `locked_letters`/`permanent_
    locked_letters` already protects a locked word's own LETTERS; this
    additionally protects its SHAPE (wherever a bordering black cell
    already exists), both together guaranteeing that an already-placed
    (and, where applicable, already-defined) word never changes shape.

    Generates a fully filled grid end to end (pattern + CSP + minimization).

    `permanent_locked_letters` (`None`/empty by default — no effect for
    any pre-existing caller, CLI and an ordinary "Continuer" included) —
    a `{(row, col): letter}` dict, at the user's explicit request for the
    "Finir la grille" button (see backend/app.py's `interactive_finish`):
    "la génération ne doit pas toucher aux lettres verrouillées, y
    compris ne pas poser de case noire sur ces lettres." Completed by:
    "Les lettres posées en mode interactif sont à considérer comme
    bonnes, même si un emplacement contient un mot impossible
    (probablement un nom propre voulu par l'utilisateur)... ne doivent
    pas être remis en cause par la génération." Unlike `resume_state`'s
    own `locked_letters` (a starting snapshot, only for the very first
    palier, then recomputed/replaced palier after palier by the search's
    own normal progress — `carry_locked_letters`, see below), this one
    stays identical, merged into the `locked_letters`/`known_letters`
    genuinely passed to EVERY worker of EVERY palier (`_pattern_attempt`/
    `_pattern_continue`), whatever state `carry_locked_letters`/`carry_
    preseed_assignment` happens to be in at that exact moment — these
    cells therefore never get a black cell at all (`make_pattern`
    systematically excludes them from its own candidate pool), and their
    word is never revalidated against the dictionary nor ever removed by
    a cleanup, whatever its real validity (see `_invalid_fully_known_
    indices`'s own `exempt` parameter, and the equivalent exemption in
    `minimize_black_squares`/`_clean_blocked_slots`).

    `priority_words` (`None`/empty by default — no effect for any
    pre-existing caller, CLI included), at the user's explicit request:
    the theme preselection. An iterable of words (bare or accented —
    normalized here into uppercase, accent-stripped MOTs, like the
    lexicon's own MOT column) coming from a Qdrant vector pre-search of
    the ~5000 words closest to a typed theme (see backend/app.py). The
    full lexicon stays loaded (indispensable for the fallback); it's the
    CSP solver that, for every slot, first tries every one of its
    candidates present in `priority_words` and only falls back to an
    ordinary dictionary word once backtracking has exhausted, with no
    solution, the theme words that fit there (see `Filler._backtrack`).

    `bilingual_priority_words` (`None` by default): on a bilingual grid,
    the theme glossary of the VERTICAL words' own language, at the
    user's explicit request ("Quand une grille est bilingue, il faut
    générer un glossaire thématique par langue"). `priority_words` then
    serves the horizontal words (language A), `bilingual_priority_words`
    the vertical ones (language B); both are wrapped in a `DualSet`
    resolved by direction (`_priority_words_for`), exactly like `index`/
    `available_lengths`. Ignored on a monolingual grid (`priority_words`
    stays a plain frozenset). `width` is the number of columns
    (horizontal), `height` the number of rows (vertical). Returns a dict
    {width, height, pattern, solution, words, word_count, black_count,
    black_ratio, language, bilingual_language}, or None if no fillable
    grid was found within `attempts` tries.

    `bilingual_wordlist_path` (`None` by default — no effect for any
    pre-existing caller, CLI and any "normal" grid included), at the
    user's explicit request ("Ajouter la possibilité de générer des
    grille bilingues... toutes les étapes utilisent la première langue
    pour les mots horizontaux, et la seconde langue pour les mots
    verticaux"): a second dictionary path, in the same format as
    `wordlist_path`. When given and genuinely different from
    `wordlist_path` (an identical path, or `None`, cleanly degrades into
    ordinary monolingual generation — no second `load_wordlist`/`build_
    index` call is even made in that case), a second lexicon is loaded
    for that language and the CSP solver (see `DualIndex`/`DualSet`
    above, `Filler._domain`) draws every horizontal ("across") word from
    the first dictionary and every vertical ("down") word from the
    second — the two languages are therefore never mixed within a single
    slot. Every entry of `result["words"]` then carries its own
    `language` (the code of the language genuinely used for THIS exact
    word, based on its direction) alongside `accented`/`canonical`
    already resolved in that same language — consumed by backend/
    clues.py (each word gets its own definition in its own language, see
    `LLMClueGenerator.generate`) and by the ChatBot (backend/chatbot.py,
    to give a hint in the right language depending on the word
    concerned). The proper-noun quota (`MAX_PROPER_NOUNS`) and the
    quota of words with no gloss entry (`MAX_NON_GLOSS_WORDS`) both
    remain a single quota shared across the whole grid (the union of the
    two "at-risk" word sets, one per language) rather than doubled per
    direction — a deliberate simplification: both quotas already bound a
    total word count over the whole grid, not a proportion per
    direction.

    `should_pause` (`None` by default — no effect for any pre-existing
    caller, CLI included), at the user's explicit request — see
    GenerationPaused's own docstring: an optional, argument-less
    callable, checked at the same boundary between two paliers as
    `cancel_event` (never inside a palier itself — yielding a turn only
    makes sense between two complete cycles, never by interrupting a
    search already in progress) — if it returns true, raises
    `GenerationPaused` with the exact resume state (the same mechanism as
    the "Continuer" button), so a later call with `resume_state=...`
    resumes at the next palier, losing nothing of the progress already
    accumulated.

    `on_progress`, if given, is called `on_progress(step, **data)` at
    every notable step (see backend/app.py, which uses it both to trace
    backend.log and to expose a progress status to the interface via the
    polling API) — no effect on the generation itself, purely an
    observation point.

    `deadline_checks` (`None` by default — no effect for any pre-existing
    caller, CLI included), at the user's explicit request: passed
    straight through to every `_pattern_attempt`/`_pattern_continue` call
    and then to `try_fill` (see its own docstring), which falls back to
    its own default formula (`width × height × 2000`) as long as this
    value stays `None`. The web UI (see backend/app.py) exposes this as a
    fixed-choice "Mode" selector (Flash/Turbo/Rapide/Moyen/Ultra) rather
    than a free-form field — each mode directly fixes the number of
    checks per attempt, unrelated to the grid's own size, unlike the
    default formula.

    `cancel_event` (a `threading.Event`, `None` by default — no effect
    for any pre-existing caller, CLI included), at the user's explicit
    request: the web UI's "Stop" button (see backend/app.py), letting a
    generation in progress be interrupted whatever the current phase.
    Checked at the start of every palier (see the loop below) and passed
    to `minimize_black_squares` for the minimization phase — raises
    `GenerationCancelled` the moment the event is set, rather than
    returning `None` (which already means something else: no fillable
    grid found after `attempts` is exhausted, a genuine failure, not a
    requested interruption). A purely cooperative signal (see
    GenerationCancelled): the actual stop can take up to the end of the
    current palier (bounded by every parallel attempt's own `deadline_
    checks`), not instant — no attempt to forcibly kill an already-
    launched worker process.

    `black_enrichment_fraction` (default `POST_PREFILL_BLACK_FRACTION`,
    see its own definition), at the user's explicit request: tunable
    from the web UI (a "Taux noir" selector, a free integer 0-100, 14%
    by default — see `GenerateRequest.black_enrichment_percent` in
    backend/app.py). Passed straight through to `_pattern_attempt`
    (never to `_pattern_continue`, which never calls `make_pattern` again
    — a "reprise telle-quelle" palier can by construction never add a
    black cell, see _pattern_continue's own docstring), so only relevant
    for a palier starting from a blank grid or from a cleanup
    (`_build_retry_seed`).

    A separate, unrelated per-cycle single-cell lock
    (`_impossible_cell_groups`/`_lock_one_impossible_cell`, which used to
    add one extra black cell on every palier's own impossible/blockage
    slot(s), including "reprise telle-quelle" ones) was removed entirely
    in this same session, at the user's explicit request — but this
    `black_enrichment_fraction` mechanism itself was never meant to be
    removed, only that separate lock; a first attempt mistakenly removed
    both together and was corrected once the user clarified the scope.
    See CLAUDE.md for the full history of both.

    `force_letters_fraction` (0.0 by default, i.e. disabled), at the
    user's explicit request: enables or not the statistical forced-
    letters sampling (`sample_letter_biases`, see `_pattern_attempt`) at
    the very start of filling, and with what fraction of the grid's
    cells. Tunable from the web UI (a percentage selector — 0/1/2/5/10%,
    0% by default — see `GenerateRequest` in backend/app.py and
    frontend/static/index.html), which validates the value then converts
    it to a fraction (`percent / 100`) before passing it here; previously
    a fixed fraction (`LETTER_BIAS_FORCE_FRACTION`, 5%) systematically
    applied to every attempt. Simply passed straight through to every
    attempt, no other part of the pipeline needs to know it.

    `resume_state` (`None` by default — no effect for any pre-existing
    caller, CLI included), at the user's explicit request: the web UI's
    "Continuer" button, shown when a generation has exhausted every one
    of its `attempts` without finding a fillable grid — see `_serialize_
    resume_state`/`_deserialize_resume_state` right above. If given,
    initializes `carry_seed_grid`/`carry_locked_letters`/`carry_preseed_
    assignment`/`carry_excluded_slots` (see the loop below) from a
    previous, failed call's own final state, instead of starting from a
    blank grid — this call's very first palier thus resumes exactly
    where the previous call left off, with a fresh, full budget of
    `attempts` paliers."""
    def progress(step, **data):
        if on_progress:
            on_progress(step, **data)

    # Normalized once and for all into a real dict (never `None`) — every
    # site that merges it further down (`if permanent_locked_letters:
    # ...`) stays unchanged for any caller that doesn't supply it at all,
    # an empty dict being just as "false" as `None` in this context.
    permanent_locked_letters = dict(permanent_locked_letters) if permanent_locked_letters else {}

    rng = random.Random(seed)
    mw = max_words or DIFFICULTY_PRESETS.get(difficulty)
    by_length, accents, canonicals, frequencies = load_wordlist(
        wordlist_path, mw, require_gloss=(difficulty == "easy"),
        # Only "easy" now excludes proper nouns from the lexicon entirely
        # — "medium"/"hard" now tolerate them, but within a real per-grid
        # quota (see MAX_PROPER_NOUNS/proper_noun_words below), at the
        # user's explicit request: "en mode FACILE ne pas autoriser à
        # placer des noms propres, en mode MOYEN autoriser au plus 2 noms
        # propres, en mode DIFFICILE autoriser jusqu'à 5 noms propres."
        # Replaces the old all-or-nothing rule that excluded "medium" just
        # as strictly as "easy".
        exclude_proper_nouns=(difficulty == "easy"),
    )
    language = _lang_from_path(wordlist_path) or "fr"

    # Bilingual grid (see `bilingual_wordlist_path`'s own docstring
    # above): a second lexicon is only loaded when `bilingual_wordlist_
    # path` is genuinely given AND different from `wordlist_path` — `None`
    # or an identical path cleanly degrades into ordinary monolingual
    # generation, with no second `load_wordlist`/`build_index` call
    # (`by_length_down`/`accents_down`/`canonicals_down`/`frequencies_down`
    # then simply alias the values already loaded above).
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

    # Proper-noun quota for this generation (see MAX_PROPER_NOUNS) and the
    # set of words (grid form) genuinely considered proper nouns for this
    # language — the same signal, computed the same way, as the one
    # `exclude_proper_nouns` above already uses (`accents[word][:1].
    # isupper()`), never recomputed a second time. Always computed, even
    # for "easy": `by_length`/`accents` then already contain no proper
    # noun at all (excluded above), so this set naturally comes out empty
    # and this quota (0) simply never gets a chance to apply. On a
    # bilingual grid, the union of both languages' own proper nouns (see
    # `bilingual_wordlist_path`'s own docstring) — a single quota shared
    # across the whole grid, not one per direction.
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
    # Quota of words with no entry in the gloss (definitions) dictionary
    # for this generation (see MAX_NON_GLOSS_WORDS), and the set of words
    # (grid form) genuinely without a gloss entry for this language — the
    # same signal `load_wordlist(require_gloss=...)` already uses,
    # reused here. Empty (so the quota never triggers) if the language
    # can't be inferred from the path, if the dictionary isn't built, or
    # for "easy" (where
    # `require_gloss=True` has already removed these words from the
    # lexicon upstream). Union of both languages on a bilingual grid, the
    # same principle as `proper_noun_words` above.
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
    # `index` is now a DualIndex (see its own docstring) — the same
    # dictionary on both sides (across/down) on a monolingual grid, two
    # distinct dictionaries on a bilingual one.
    index_across = build_index(by_length, frequencies)
    index_down = build_index(by_length_down, frequencies_down) if bilingual_active else index_across
    index = DualIndex(index_across, index_down)

    # Theme preselection (see the docstring / `priority_words`). Words
    # already arrive in MOT form (uppercase, accent-stripped — Qdrant's
    # own payload `word`, identical to the lexicon's own MOT column and
    # to `index`'s own keys); this just uppercases them and restricts to
    # words actually present in the loaded lexicon (a word from a
    # language/spelling absent from this lexicon would be useless as a
    # priority). An empty `frozenset` if there's no theme at all or no
    # match — `Filler._backtrack` then changes nothing. On a bilingual
    # grid, one glossary per language (at the user's explicit request:
    # "Quand une grille est bilingue, il faut générer un glossaire
    # thématique par langue") — `priority_words` for horizontal words
    # (language A), `bilingual_priority_words` for vertical ones
    # (language B) — wrapped in a DualSet, exactly like `index`/
    # `available_lengths`. On a monolingual grid, `priority_words` stays
    # a plain frozenset (unchanged behavior, `bilingual_priority_words`
    # ignored).
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
    # "Mots Défi" (see this function's own docstring): normalized once
    # here, deliberately with NO lexicon filter (unlike priority_words
    # right above) — a challenge word is trusted even when absent from
    # the loaded dictionary.
    challenge_words = frozenset(
        gf for w in (challenge_words or ()) if (gf := challenge_word_grid_form(w))
    )
    # Precomputed once (not per palier) — same lengths for the whole
    # generation, `index` never changes. Reproduces exactly the same
    # computation each worker does in `_pattern_attempt` (see its own
    # docstring), but on the PARENT process's side this time — used only
    # by the early "cases noires posées" preview below, never by the CSP
    # search itself (which is still always computed inside the worker
    # processes, with their own `_worker_index`).
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
    # The same resolution as `try_fill`'s own `None`-fallback (width ×
    # height × 2000), computed here once — rather than separately in each
    # worker — so the budget progress report below (`BUDGET_PROGRESS_
    # REPORT_INTERVAL_S`) knows what value to compare `checks` against
    # without having to ask a worker for it again.
    resolved_deadline_checks = (
        deadline_checks if deadline_checks is not None else rows * cols * 2000
    )
    ratio = black_ratio
    best, best_result = None, None
    # The winning candidate's own diagnostics (see the `if successes:`
    # branch below) — kept only for its own `process_number` (see
    # `seed_to_lineage`), so the "minimizing" preview/the final result's
    # `winning_process_number` field can always show the number of the
    # lineage that actually produced the chosen grid. `None` for any
    # success path that never has a real diag (`_plug_isolated_cells`, a
    # last resort that builds its own grid directly in the parent process
    # — never a real worker).
    best_diag = None
    last_diag = None
    last_examples = []
    # Cumulative count of how many grids have genuinely failed since the
    # start (across every palier), at the user's explicit request — for
    # the status display on the interface side (see describeStep() in
    # frontend/static/script.js): without it, the user only sees "attempt
    # X/attempts" (the *palier* number) with no idea how many PARALLEL_
    # ATTEMPTS-at-a-time grids have actually been generated and rejected
    # so far. Incremented by the number of *failed* attempts of each
    # palier (not `len(outcomes)` as-is) — at the user's explicit request,
    # after a first version that also counted the final palier's own
    # successful attempts as failures: this counter must reflect the
    # number of genuinely rejected grids, not the raw number of launched
    # attempts (which, at the winning palier, includes one or more
    # successes).
    total_attempts_tried = 0
    # Every palier launches PARALLEL_ATTEMPTS independent attempts in
    # parallel (separate processes, each with its own seed derived from
    # `rng`) rather than a single sequential attempt: the machine is far
    # from saturating its CPU with just one attempt at a time, so several
    # chances per palier cost, in wall-clock time, almost exactly the
    # slowest attempt of the batch — not the sum of all of them.
    # Starting point (pattern + locked letters) passed to the next palier
    # after a total failure, at the user's explicit request — see
    # _build_retry_seed for the 3-step algorithm (remove words connected
    # to the failed slots, keep the rest as pre-defined letters, reopen
    # black cells no longer touching any confirmed letter). `None` as
    # long as no palier has failed yet — the very first palier always
    # starts from a blank grid, exactly as before this feature.
    carry_seed_grid = None
    carry_locked_letters = None
    # Pool of cleaned candidate grids for the next "fresh pattern" palier —
    # one per parallel attempt of the palier that just failed (up to
    # `PARALLEL_ATTEMPTS`, minus the worst ones eliminated, see below), not
    # a single grid reused by every non-reset worker — at the user's
    # explicit request: "on garde la meilleure grille de tous les process,
    # soit N grilles pour N process, et on relance toutes les meilleures
    # grilles après nettoyage en ayant éliminé les moins bonnes en fonction
    # du nombre de nouvelles grilles paramétrées." `carry_seed_grid`/
    # `carry_locked_letters` above stay the BEST grid of this pool (always
    # first once sorted) — used as-is everywhere else in this function
    # (previews other than the very next palier, `resume_state`, fixed-
    # point detection...) exactly as before this feature; only the very
    # next "fresh pattern" palier draws from this pool to diversify its
    # own launch instead of reusing an identical `carry_seed_grid` for all
    # of its non-reset workers. `None` as long as no full nettoyage has
    # happened yet (see below, where only the nettoyage branch populates
    # it).
    carry_seed_pool = None
    # {position in `carry_seed_pool` -> displayed "lineage" number},
    # STRICTLY parallel to `carry_seed_pool` (same length, same order) —
    # replaces an old mechanism based on the worker's own real PID
    # (`carry_seed_pool_process_numbers`, a dict keyed by grid content), at
    # the user's explicit request after a direct report: "les grilles
    # changent de numéro d'un cycle sur l'autre... il faut que les grilles
    # portent leur propre numéro, et le gardent jusqu'à la fin de la
    # résolution." A worker's own PID is stable for the whole duration of
    # a `generate_grid()` call, but its ASSIGNMENT to a given lineage isn't
    # (see `_build_dispatch_lineage`) — hence the switch to a numbering
    # that follows the LINEAGE itself (inherited from one palier to the
    # next via its position in this pool), never the OS process that
    # produced it. `None` as long as no full nettoyage has happened yet —
    # see `_reassign_lineage_numbers`/`next_lineage_number` for the
    # complete construction, including a disappearing lineage's own number
    # being taken over by a brand-new grid that replaces it.
    carry_seed_pool_lineage = None
    # A counter persisting for the whole duration of a `generate_grid()`
    # call (never reset, including by GRID_REPEAT_INFEASIBLE_THRESHOLD's
    # own full-reset mechanism further below) — only ever serves as a
    # safety net for `_reassign_lineage_numbers` when no number was freed
    # this palier (a degenerate case, not encountered in practice). Starts
    # after the 1..PARALLEL_ATTEMPTS range already directly assigned to
    # the very first palier (see below, `carry_seed_grid is None`).
    next_lineage_number = PARALLEL_ATTEMPTS + 1
    # "Reprise telle-quelle" (see _pattern_continue), at the user's
    # explicit request ("Nouvelle version"): as long as the selected
    # failed palier still has at least one slot where a word can be
    # added (not just impossible slots), the next palier starts again
    # from the SAME pattern, without going through make_pattern or the
    # `_build_retry_seed` cleanup — `carry_preseed_assignment`/`carry_
    # excluded_slots` drive this mode; both `None` (their default value)
    # means we're in the normal "fresh pattern" mode (via `_pattern_
    # attempt`, `carry_seed_grid`/`carry_locked_letters` above,
    # unchanged). The two resume mechanisms are mutually exclusive at
    # every palier: only one is ever active at a time, never both (see
    # below, where each branch resets the other to None).
    carry_preseed_assignment = None
    carry_excluded_slots = None
    # Pool of cleaned candidate grids for the next "reprise telle quelle"
    # palier — the counterpart of `carry_seed_pool` above, but for
    # `_pattern_continue` instead of `_pattern_attempt`: one `(seed_grid,
    # preseed_assignment, excluded_slots)` entry per distinct attempt of
    # the palier that just finished, not a single grid reused by every
    # non-reset worker — at the user's explicit request: "Quand il n'y a
    # pas de déclenchement d'un nettoyage complet, chaque process doit
    # repartir à l'étape suivante avec sa grille partiellement nettoyée
    # (sauf le pourcentage de grilles entièrement neuves)." See `_clean_
    # continue_candidate`/`_continue_seed_pool` (module level) and `if
    # still_has_hope:` further below for how it's built; `carry_seed_
    # grid`/`carry_preseed_assignment`/`carry_excluded_slots` above stay
    # the BEST entry of this pool (always first once sorted) — used as-is
    # everywhere else in this function (previews other than the very next
    # palier, `resume_state`...) exactly as before this feature; only the
    # very next "reprise telle quelle" palier draws from this pool to
    # diversify its own launch. `None` as long as no "telle quelle" palier
    # has happened yet (see below, where only this branch populates it) —
    # never passed via `resume_state` (like `carry_seed_pool` itself), a
    # resumed run rebuilds this pool normally from its own first "telle
    # quelle" palier.
    carry_seed_pool_continue = None
    # Counterpart of `carry_seed_pool_lineage` above, for the "reprise
    # telle quelle" pool — same role, same mechanism.
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
    # Number of consecutive "continue" paliers already chained without
    # going through a cleanup, at the user's explicit request: "Limiter
    # le nombre de tours réalisés sans nettoyage à 5 consécutifs maximum.
    # A partir de 5, déclencher un nettoyage." Reset to 0 every time a
    # cleanup genuinely happens (see below) — this counter only measures
    # the current streak, not a cumulative total over the whole
    # generation.
    consecutive_continue_paliers = 0
    # Remembering the state (pattern + confirmed content) obtained at the
    # end of every FULL NETTOYAGE, at the user's explicit request —
    # see GRID_REPEAT_INFEASIBLE_THRESHOLD's own docstring for the full
    # request and the history of the two regressions measured before
    # reaching this final scope (nettoyage only, never "reprise telle
    # quelle"). `last_cycle_end_grid` keeps the state (in hashable form, a
    # tuple of tuples produced by `_cycle_start_preview`) of the last
    # cleanup; `same_grid_streak` counts how many CONSECUTIVE cleanups (no
    # "reprise telle quelle" in between ever resets or increments it —
    # this branch never touches it) have reproduced this same state.
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
    # See _worker_batch_abandoned_event — a single Event for the whole
    # generation (created here, never recreated palier after palier, for
    # the same technical reason as cancel_event: an Event submitted as a
    # task argument rather than via the pool's initializer triggers a
    # RuntimeError on macOS), but reset before every new batch (see below)
    # since its meaning only applies to the current palier.
    batch_abandoned_event = multiprocessing.Event()
    # Warm-up barrier (see `_warmup_worker`): a single object for the
    # whole generation, passed to workers via the pool's initializer like
    # this file's other `multiprocessing` primitives (a `multiprocessing.
    # Barrier` submitted as a task argument rather than via the
    # initializer would trigger the same RuntimeError on macOS as
    # `cancel_event`/`batch_abandoned_event`). Exactly `PARALLEL_ATTEMPTS`
    # slots: until all of them have arrived, every worker that grabs one
    # stays blocked inside it (never becomes "idle" for the pool again
    # until the barrier has released everyone) — this is precisely what
    # forces the pool to spawn a NEW process for each of the
    # `PARALLEL_ATTEMPTS` warm-up tasks rather than letting an
    # already-ready worker absorb several of them (see the full
    # diagnostic in `_warmup_worker`'s own docstring, which explains why
    # a plain batch of dummy tasks with no synchronization wasn't
    # enough).
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
    # les N process" — the user's own explicit decision. A `multiprocessing.
    # Queue` (not an `Event`: real data needs to be carried, not just a
    # signal) passed via the pool's initializer, for the same technical
    # reason as the three `Event`s above — a `multiprocessing` object
    # passed as a plain task argument to `executor.submit(...)` triggers a
    # `RuntimeError` on macOS ("spawn"). A single object for the whole
    # generation, never recreated palier after palier.
    best_state_queue = multiprocessing.Queue()
    # A genuine deadlock was observed live (workers' own CPU time frozen
    # from one reading to the next, the user themselves observing "il n'y
    # a plus que 2 process qui tourne") with a first version that only
    # ever drained `best_state_queue` once per palier, right after
    # `as_completed` had collected every future: a `multiprocessing.
    # Queue`'s own underlying pipe has a bounded OS-side capacity — if
    # enough messages pile up without ever being read while a worker is
    # still deep in its own search (each attempt can publish up to ~50-60
    # times, see _worker_best_state_queue), its own `put()` eventually
    # blocks as long as nobody reads the pipe; but nobody reads it until
    # ALL of this palier's workers have finished — and that specific
    # worker can never finish precisely because its own `put()` stays
    # blocked. A classic producer/consumer deadlock, not a problem with
    # the 30% threshold detection (`attempt_done_event`) nor with workers
    # failing to stop correctly.
    #
    # Fixed by draining the queue continuously, in a dedicated thread
    # (`threading`, not `multiprocessing` — this thread runs in the
    # PARENT process, where GIL or not, a loop that only ever waits on
    # `Queue.get(timeout=...)` then does `list.append(...)` never
    # competes for the GIL with anything costly) started only once for
    # the whole generation, never recreated palier after palier — as long
    # as this thread runs, the pipe can never again pile up enough to
    # block a `put()`. `_best_state_buffer`/`_best_state_buffer_lock`
    # accumulate every message received; each palier's own code (below)
    # never interacts directly with `best_state_queue` anymore — it
    # drains `_best_state_buffer` under lock instead, which amounts to
    # exactly the same thing from the point of view of what it receives,
    # never risking a direct read from the queue while a worker is still
    # writing to it.
    best_state_buffer = []
    best_state_buffer_lock = threading.Lock()
    stop_best_state_drain = threading.Event()
    # (Monotonic) timestamp of the last budget-consumption-percentage
    # report — see BUDGET_PROGRESS_REPORT_INTERVAL_S. A single-element
    # list (not a plain variable) purely to stay mutable from inside the
    # loop below without `nonlocal`.
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
            # Checked on every iteration of this loop (~10 times per
            # second, see get()'s own timeout above), not only when a
            # message has just arrived — otherwise, a palier where no
            # attempt improves its own record for a long stretch would
            # never publish anything again at all, even though `deadline_
            # checks` itself keeps genuinely being consumed in the
            # background inside the workers.
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

    # Started even before the pool is created, `daemon=True`: this thread
    # must never prevent the process from ending, including on an early
    # exit path (`GenerationCancelled`, raised from inside the loop below)
    # that wouldn't go through the explicit stop right at the end of this
    # function — in that rare case, the thread simply stays idle (blocked
    # on `get(timeout=0.1)`, with nothing to read) until the process ends,
    # a negligible cost, rather than a `try`/`finally` wrapping the whole
    # palier loop (hundreds of lines) that would have needed reindenting
    # in one block.
    best_state_drain_thread = threading.Thread(
        target=_drain_best_state_queue_continuously, daemon=True
    )
    best_state_drain_thread.start()
    with concurrent.futures.ProcessPoolExecutor(
        max_workers=PARALLEL_ATTEMPTS, initializer=_init_worker,
        initargs=(index, cancel_event, batch_abandoned_event, attempt_done_event, best_state_queue,
                  warmup_barrier, proper_noun_words, max_proper_nouns,
                  non_gloss_words, max_non_gloss, priority_words, challenge_words)
    ) as executor:
        # Pool warm-up: forces every worker to finish its real startup
        # before the very first palier (see `_warmup_worker`/`warmup_
        # barrier`'s own docstring for the full diagnostic and reasoning
        # — two simpler versions, with no barrier, were tried and found
        # insufficient before arriving here). Submits exactly `PARALLEL_
        # ATTEMPTS` tasks at once and waits for all of them: the barrier
        # guarantees no worker can absorb more than one before every
        # other one has genuinely started, so this wait only ends once
        # `PARALLEL_ATTEMPTS` distinct processes genuinely exist.
        # `BrokenBarrierError` is caught defensively (never observed in
        # practice) rather than crashing the whole generation if, on a
        # given machine, fewer than `PARALLEL_ATTEMPTS` processes can
        # structurally ever coexist — in that case the warm-up fails, but
        # generate_grid keeps going anyway (at worst, the numbering
        # becomes subject to the same warm-up issue as before this fix,
        # never a total hang).
        try:
            warmup_futures = [executor.submit(_warmup_worker) for _ in range(PARALLEL_ATTEMPTS)]
            concurrent.futures.wait(warmup_futures)
        except threading.BrokenBarrierError:
            pass
        for attempt in range(attempts):
            if cancel_event is not None and cancel_event.is_set():
                raise GenerationCancelled()
            if should_pause is not None and should_pause():
                # The same serialization mechanism as the "attempts
                # exhausted" exit further below (see _serialize_resume_
                # state) — see GenerationPaused's own docstring. `None`
                # only if this very first iteration (attempt == 0) is
                # already the one yielding its turn, before any palier has
                # ever run at all.
                raise GenerationPaused(
                    _serialize_resume_state(
                        carry_seed_grid, carry_locked_letters,
                        carry_preseed_assignment, carry_excluded_slots,
                    )
                    if carry_seed_grid is not None else None
                )
            batch_abandoned_event.clear()
            attempt_done_event.clear()
            # Pool of candidate starting grids for this palier (see
            # `carry_seed_pool`, its own definition above) — computed
            # here, even before knowing whether this palier will be a
            # "reprise telle quelle" or a fresh pattern, so this cycle-
            # START preview can already account for it, not just the
            # "cases noires posées" one further below (which already
            # computed it). Falls back to `[(carry_seed_grid, carry_
            # locked_letters)]` (the behavior before this feature) as
            # long as no full nettoyage has populated `carry_seed_pool`
            # yet — see its own definition for the complete detail.
            pool = carry_seed_pool if carry_seed_pool else [(carry_seed_grid, carry_locked_letters)]
            # Parallel to `pool` (same length, same order) — see `_build_
            # dispatch_lineage`/`carry_seed_pool_lineage`'s own
            # definition. `[1]` as a fallback as long as no full nettoyage
            # has populated `carry_seed_pool_lineage` yet, consistent with
            # `pool`'s own fallback (a single entry in both cases).
            pool_lineage = carry_seed_pool_lineage if carry_seed_pool_lineage else [1]
            if carry_preseed_assignment is not None:
                # One preview per pool grid (`carry_seed_pool_continue`),
                # not just one, at the user's explicit request (see
                # `carry_seed_pool_continue`'s own definition) — the same
                # principle as "fresh pattern" below, now also true for
                # "reprise telle quelle": each distinct attempt of the
                # previous palier may have been cleaned differently
                # (different words removed, sometimes a black cell added),
                # so the next palier can genuinely start from several
                # distinct patterns/assignments, not just one like before
                # this feature. Falls back to `[(carry_seed_grid, carry_
                # preseed_assignment, carry_excluded_slots)]` (the behavior
                # before this feature) as long as no "telle quelle" palier
                # has populated `carry_seed_pool_continue` yet. This
                # palier's own reset attempts (`reset_count` further
                # below, an entirely fresh pattern) are deliberately not
                # previewed separately here — the same convention as the
                # "fresh pattern" branch below, whose own pool doesn't
                # preview them either.
                continue_pool = carry_seed_pool_continue if carry_seed_pool_continue else [
                    (carry_seed_grid, carry_preseed_assignment, carry_excluded_slots)
                ]
                # Parallel to `continue_pool` — the same role as `pool_
                # lineage` above, for the "reprise telle quelle" pool.
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
                        "challenge_cells": _challenge_cells_from_preview_state(
                            pool_grid, rows, cols, None, pool_preseed, challenge_words
                        ),
                        "low_candidate_cells": [],
                        "noise_cells": [],
                        "process_number": continue_pool_lineage[pool_idx % len(continue_pool_lineage)],
                        # `continue_pool[0]` (never a duplicate — the
                        # first one examined, `seen_continue_patterns`
                        # still empty at that point) is the pool's best
                        # grid, see `_continue_seed_pool`/`_sort_examples_
                        # by_process`.
                        "is_best": pool_idx == 0,
                    })
            else:
                # One preview per pool grid, not just one, at the user's
                # explicit request: "Les extraits 'Génération du motif de
                # cases noires' ne montrent qu'une seule grille. Il
                # devrait maintenant y en avoir N pour N process." — the
                # same principle and the same dedup (by real black/white
                # pattern, `pool_grid`, never the state already overlaid
                # with letters) as the "cases noires posées" preview
                # further below, which already had this diversity; only
                # this very-start-of-cycle preview was still missing it.
                # Below-fill-threshold cells (< PREFILL_LOCKED_MIN_WORD_
                # COUNT candidates, at the user's explicit request — see
                # _low_candidate_slot_cells) are now computed for each
                # pool grid individually, on its own
                # locked letters — never another pool entry's own. `pool_
                # grid is None` only for a generation's very first palier
                # (nothing locked anywhere yet) — a single blank grid in
                # the pool in that case, so nothing to deduplicate or
                # evaluate.
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
                    # Cells with no genuinely playable proposal at all
                    # (`NOISE_FREQUENCY_THRESHOLD`), at the user's
                    # explicit request — the same per-pool-grid individual
                    # computation, the same scope (never for a "reprise
                    # telle quelle" palier) as low_candidate_cells above,
                    # see _noise_slot_cells.
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
                        "challenge_cells": _challenge_cells_from_preview_state(
                            pool_grid, rows, cols, pool_locked, None, challenge_words
                        ),
                        "low_candidate_cells": low_candidate_cells,
                        "noise_cells": noise_cells,
                        "process_number": (
                            pool_lineage[pool_idx % len(pool_lineage)]
                            if pattern_key is not None else None
                        ),
                        # `pool[0]` (never a duplicate — the first one
                        # examined, `seen_cycle_start_patterns` still
                        # empty at that point) is the pool's best grid,
                        # see `_seed_pool`/`_sort_examples_by_process`.
                        "is_best": pool_idx == 0,
                    })
            progress("pattern", attempt=attempt + 1, attempts=attempts, parallel=PARALLEL_ATTEMPTS,
                     total_attempts=total_attempts_tried,
                     examples=_sort_examples_by_process(cycle_start_examples))
            seeds = [rng.randrange(2**31) for _ in range(PARALLEL_ATTEMPTS)]
            if carry_preseed_assignment is not None:
                # `reset_count` attempts of this "reprise telle quelle"
                # palier start again from an entirely new pattern
                # (`_pattern_attempt`, seed_grid=None — never `_pattern_
                # continue`, since there's then no prior pattern or
                # locking to resume) instead of the individual resume on
                # their own pool entry — at the user's explicit request:
                # "chaque process doit repartir à l'étape suivante avec sa
                # grille partiellement nettoyée (sauf le pourcentage de
                # grilles entièrement neuves)." Unlike "fresh pattern"
                # below (`reset_count` conditioned on `just_cleaned`, only
                # right after a full nettoyage), this applies here
                # unconditionally to EVERY "reprise telle quelle" palier —
                # there's no `just_cleaned` equivalent to distinguish here,
                # since such a palier is already, by construction, always
                # the continuation of a previous state (never a genuine
                # first palier, which always starts from `carry_seed_grid
                # is None`, so from the "fresh pattern" branch below).
                # Every non-reset attempt (`i >= reset_count`) receives its
                # own pool entry (`continue_pool`, already computed above
                # for this same palier's own preview) — a plain cyclic
                # walk (`% len(continue_pool)`) distributes the available
                # entries across the non-reset slots, just like for "fresh
                # pattern" below.
                reset_count = FULL_RESET_ATTEMPT_COUNT
                # "Lineage" number (see `_build_dispatch_lineage`)
                # inherited by each of THIS palier's own PARALLEL_ATTEMPTS
                # tasks, even before submitting them — `continue_pool_
                # lineage`, computed above for this same palier's own
                # preview, stays parallel to `continue_pool` (same order,
                # same length).
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
                            required_cells=required_cells,
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
                            required_cells=required_cells,
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
                # `pool` (see `carry_seed_pool`'s own definition above)
                # already computed at the very start of this palier, for
                # the "Génération du motif de cases noires" preview —
                # reused as-is here, never recomputed a second time for
                # this palier's own non-reset workers nor for the "cases
                # noires posées" preview right below.
                # "Cases noires posées, recherche des mots en cours"
                # preview published RIGHT NOW — even before submitting a
                # single parallel attempt to the executor, so well before
                # this palier's own CSP search (the slow part) finishes —
                # at the user's explicit request: "Le Front n'affiche les
                # aperçus qu'après la fin d'un cycle. Les états
                # d'initialisation n'apparaissent pas avant la fin du
                # cycle. Il faut que la stack Back soit proprement
                # alimentée à chaque étape du cycle." Root-caused directly
                # in the code, not assumed: the existing `pattern_
                # generated` further below (`_cycle_start_preview` on
                # `failed_pairs`/`best`) can only be computed once ALL of
                # this palier's parallel attempts have finished
                # (`concurrent.futures.as_completed`, further below) —
                # for a "fresh pattern" palier (this one, not the "reprise
                # telle quelle" above, whose own `pattern_generated`
                # already coincides with the cycle's own starting state),
                # the "cases noires posées" step could therefore never
                # actually appear before the end of the cycle, however
                # fast the Front tried to display it — the Back itself
                # simply hadn't computed it yet. Reconstructed here, in
                # the PARENT process, with the same parameters a real
                # worker will use in its own separate process further
                # below — since `make_pattern` is a pure function of its
                # arguments, calling it twice with the same seed produces
                # the same pattern both times, so there's never a false
                # impression of a "moved" black cell once the real
                # `pattern_generated` (computed afterward, see further
                # below) is received.
                #
                # An initialization PER PROCESS, but reserved for the very
                # first initialization of the generation (`carry_seed_grid
                # is None` — no previous palier has run yet) — at the
                # user's explicit request: "la toute première
                # initialisation des cases noires ne prépare qu'une seule
                # grille. Intégrer cette première initialisation au début
                # du cycle, de manière à créer une initialisation par
                # process." Clarified by the user themselves after a first
                # implementation that applied it to *every* "fresh
                # pattern" cycle (not just the very first one), causing a
                # real slowdown measured live (up to +250% per palier)
                # and, worse, a genuine risk of making a generation fail
                # that would otherwise have succeeded (the extra
                # sequential computation in the parent process shifts the
                # real timing at which the parallel attempts get
                # submitted, and this palier uses an interruption
                # mechanism sensitive to the real completion order —
                # `attempt_done_event`/`batch_abandoned_event` — not just
                # to the seed): "Il ne faut pas changer le budget, juste
                # initialiser N grilles au premier cycle au lieu d'une
                # seule. Les cycles suivants, à partir de 2, reprendront la
                # meilleure grille (sauf 20% de nouvelles grilles)." From
                # the 2nd palier onward, `carry_seed_grid` already carries
                # the previous best attempt's own content (or starts from
                # a blank grid for the `reset_count` reset attempts,
                # already its own source of diversity) — the "one grid per
                # process" diversity therefore only has real meaning at
                # the very first palier, where nothing yet distinguishes
                # the attempts from each other besides their own seed.
                if carry_seed_grid is None:
                    # Every worker here starts from a blank grid,
                    # independently of the others (see below) — each
                    # therefore receives ITS OWN lineage from the moment
                    # it's created, numbered 1..PARALLEL_ATTEMPTS by its
                    # own submission index, rather than the `None`/no-
                    # number behavior before this feature: at the user's
                    # explicit request, a grid must carry its own number
                    # from the moment it exists, not only once its first
                    # real result is known.
                    dispatch_lineage = list(range(1, PARALLEL_ATTEMPTS + 1))
                    # One per process (up to PARALLEL_ATTEMPTS), at the
                    # user's explicit request: "il n'y a jamais eu 6
                    # grilles par process, mais 1 grille par process (1
                    # process par processeur)." — same principle here: one
                    # computation per attempt about to be submitted,
                    # deduplicated by real black/white pattern (two
                    # workers can legitimately land on the same pattern),
                    # with no cap at all beyond this dedup — at the user's
                    # explicit request ("Afficher toutes les meilleures
                    # grilles dans l'aperçu, pas seulement les 6
                    # meilleures"), which removes the `FAILED_ATTEMPT_
                    # EXAMPLES` (6) cap previously applied here — the same
                    # convention (dedup, no cap) already used further
                    # below for the patterns genuinely searched
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
                            # Very first palier: nothing is locked or
                            # assigned yet, so no theme/challenge word to
                            # report.
                            "theme_cells": [],
                            "challenge_cells": [],
                            "process_number": dispatch_lineage[i],
                            # No comparison has happened yet at this
                            # point (very first palier, every attempt
                            # starts independently from a blank grid) —
                            # so none of them is "the best" for now.
                            "is_best": False,
                        })
                else:
                    # One preview per pool grid (not just one), at the
                    # user's explicit request: since the next palier can
                    # genuinely start from several distinct patterns (see
                    # `pool` above), a single preview rebuilt from `carry_
                    # seed_grid` alone would no longer necessarily match
                    # what a real worker will compute — exactly the bug
                    # class already encountered several times in this
                    # file for a "model" pattern that ends up diverging
                    # from reality once several variants are in play (see
                    # CLAUDE.md). For every pool entry, rebuilds here, in
                    # the PARENT process, exactly the same pattern (same
                    # parameters, same seed) as the FIRST real worker this
                    # entry will actually be assigned to in `futures`
                    # further below (`seeds[reset_count + p]` for the
                    # p-th pool entry — always a valid index: the pool
                    # never holds more entries than non-reset slots, see
                    # `_seed_pool`). The same real-pattern dedup, with no
                    # cap at all, as the "very first palier" branch above
                    # — not a distinct mechanism, only the source (the
                    # pool, rather than `seeds` on a shared blank grid)
                    # differs.
                    dispatch_lineage = _build_dispatch_lineage(
                        PARALLEL_ATTEMPTS, reset_count, pool_lineage
                    )
                    seen_pool_patterns = set()
                    early_examples = []
                    for p, (pool_grid, pool_locked) in enumerate(pool):
                        # `min(..., len(seeds) - 1)`: a safety net for a
                        # degenerate case (PARALLEL_ATTEMPTS <= FULL_
                        # RESET_ATTEMPT_COUNT, never the case with default
                        # values) where `reset_count + p` would otherwise
                        # overflow `seeds` — never reached in practice
                        # (see `_seed_pool`, which already guarantees
                        # `len(pool) <= PARALLEL_ATTEMPTS - reset_count`
                        # in the normal case), but an approximate preview
                        # is still preferable to an outright crash.
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
                        # The lineage number is read directly from this
                        # entry's own POSITION in the pool (`pool_
                        # lineage`, parallel to `pool` — see its own
                        # definition), never from `pool_grid`/`early_
                        # pattern`'s own content: two distinct pool
                        # entries can, in theory, produce an identical
                        # pattern without being the same lineage, so only
                        # the position is authoritative.
                        early_examples.append({
                            "example_grid": early_pattern_grid,
                            "impossible_cells": [],
                            "forced_cells": [],
                            "locked_cells": early_pattern_locked,
                            "theme_cells": _theme_cells_from_preview_state(
                                early_pattern, rows, cols, pool_locked, None, priority_words
                            ),
                            "challenge_cells": _challenge_cells_from_preview_state(
                                early_pattern, rows, cols, pool_locked, None, challenge_words
                            ),
                            "process_number": pool_lineage[p % len(pool_lineage)],
                            # `pool[0]` (never a duplicate — the first
                            # one examined, `seen_pool_patterns` still
                            # empty at that point) is the pool's best
                            # grid.
                            "is_best": p == 0,
                        })
                progress(
                    "pattern_generated", attempt=attempt + 1, attempts=attempts,
                    total_attempts=total_attempts_tried,
                    examples=_sort_examples_by_process(early_examples),
                )
                # Every non-reset worker (`i >= reset_count`) receives its
                # own pool entry (`pool`, see its own definition above),
                # not systematically `carry_seed_grid` — at the user's
                # explicit request. A plain cyclic walk (`% len(pool)`)
                # distributes the available entries across the non-reset
                # slots; in the normal case (`len(pool) == PARALLEL_
                # ATTEMPTS - reset_count`, guaranteed by `_seed_pool`),
                # this cycle never actually loops back — each slot
                # receives a distinct entry, exactly once. It only loops
                # if `failed_pairs` exceptionally had fewer entries than
                # slots to fill (content-based dedup, see its own
                # comment) — only in that specific case can the same
                # cleaned grid legitimately end up reused by more than
                # one worker, each with its own seed.
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
                        required_cells=required_cells,
                    ))
            # Collected in completion order (`as_completed`), not
            # submission order, at the user's explicit request ("le
            # bouton Stop ne s'applique pas rapidement... prévoir l'arrêt
            # dans toutes les phases"): the moment one of the PARALLEL_
            # ATTEMPTS attempts raises GenerationCancelled (every worker
            # checks the same `cancel_event`, see Filler._backtrack), the
            # exception is re-raised immediately rather than also waiting
            # for the other results — `outcomes`'s own order doesn't
            # matter for the rest of this loop (the best result is always
            # chosen via `max`/sort, never by position). The other
            # still-running attempts will detect the same cancellation at
            # their own next checkpoint (at most CANCEL_CHECK_INTERVAL
            # checks later) and stop in turn — `with ... as executor`
            # waits for their normal end when the block exits
            # (ProcessPoolExecutor's own default behavior), but this delay
            # stays short, unrelated to a palier's own full `deadline_
            # checks` budget.
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
            # `dispatch_lineage` (see its own construction above, in each
            # of the two branches above) associates, to each submission
            # INDEX (0..PARALLEL_ATTEMPTS-1), the lineage number this task
            # inherits — never the completion order, which has nothing to
            # do with lineage. `seeds[i]` is already the `attempt_id`
            # every real diagnostic (see `diag["attempt_id"]` in
            # `_pattern_attempt`/`_pattern_continue`) and every state
            # published along the way (see `_publish_new_best`) already
            # carries, so this seed -> lineage mapping is enough to find
            # the right number for either one, with no need to know which
            # worker (PID) produced it.
            seed_to_lineage = {seeds[i]: dispatch_lineage[i] for i in range(PARALLEL_ATTEMPTS)}
            interrupt_threshold = max(1, math.ceil(PALIER_ATTEMPT_INTERRUPT_FRACTION * len(futures)))
            outcomes = []
            for f in concurrent.futures.as_completed(futures):
                outcomes.append(f.result())
                if len(outcomes) == interrupt_threshold:
                    attempt_done_event.set()
            # Attaches, to every diag of this palier (successes and
            # failures alike), the lineage number inherited from the task
            # that produced it (`seed_to_lineage`, see its own
            # construction above) — done once here, so anything reading
            # `d["process_number"]` further down (previews, winner
            # selection) already finds it ready. `None` for a reset task
            # whose lineage hasn't been resolved yet (see `_reassign_
            # lineage_numbers`, further below, which only applies to the
            # next palier's own SURVIVING pool candidates).
            for _, _, d in outcomes:
                d["process_number"] = seed_to_lineage.get(d.get("attempt_id"))
            successes = [(g, r, d) for g, r, d in outcomes if r is not None]
            # Dedup of failed attempts, at the user's explicit request,
            # after a real bug observed live: once a good part of the grid
            # is locked by the cross-palier retry mechanism below, the
            # still-free area can become so restricted that the PARALLEL_
            # ATTEMPTS parallel attempts — despite launched with different
            # seeds — converge on EXACTLY the same pattern and the same
            # dead end (reproduced live: as early as the 6th palier of a
            # hardened grid, the 10 attempts yielded a single distinct
            # pattern instead of 6+). Without dedup, the preview showed
            # the same grid repeated 6 times instead of 6 genuinely
            # different attempts. Two attempts only count as identical if
            # their black/white pattern *and* their word assignment are
            # both rigorously equal (not just the pattern alone, in case
            # two identical patterns nonetheless ended up with different
            # letters). This dedup now only ever decides *which* previews
            # to show (see `failed_pairs` below) — no longer `total_
            # attempts`'s own computation, see right below.
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
            # `failed_unique` is still built only from *real* search
            # results (`failed_all`, above) — never from states published
            # in real time by `best_state_queue` (see below): it's this
            # pool, and only this pool, that decides `failed_pairs`/
            # `selected_grid`/`selected_diag`/`still_has_hope`/`_build_
            # retry_seed` — everything that genuinely influences the
            # search's progress from one palier to the next. A state
            # published via the queue stays visible in the preview shown
            # on screen (see `display_pairs`/`last_examples` below), but
            # can never again become the next palier's own basis in place
            # of a genuinely completed result — at the user's explicit
            # request, after a real failure measured live: even once the
            # sort criterion was fixed (see `_playable_score` below),
            # letting an intermediate state compete for this selection
            # remained risky, since the search that produced it hadn't
            # gone far enough to detect every real conflict — its own
            # `impossible_slots` can therefore be incomplete compared to a
            # genuinely finished search's, which would in turn make the
            # next palier's own cleanup (`_build_retry_seed`, which
            # precisely relies on `impossible_slots` to decide which words
            # to remove) itself incomplete.
            # `total_attempts` counts grids genuinely tried and abandoned
            # in the literal sense of the word, at the user's explicit
            # request — not the number of parallel processes launched (10
            # per palier), which doesn't reflect the real work done at
            # all: the CSP fill proceeds by successive trial-and-
            # backtrack (see Filler._backtrack) — every attempt to place a
            # word (`filler.checks`, incremented once per candidate word
            # tried in `_backtrack`'s own loop, whether or not it leads to
            # a further recursive descent — see that loop's own comment
            # for why this counter is no longer tied to recursion depth
            # alone) represents a genuinely attempted grid configuration
            # then abandoned the moment the search backtracks or rejects
            # that word. Summed over EVERY failed attempt of this palier,
            # duplicates above included — an identical pattern found by
            # two different workers (different seeds) still required, in
            # each worker, its own genuine search work (a backtracking
            # path that can differ even if the final result converges), so
            # neither quantity of work is to be ignored.
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
                        # Tie-break — see `_content_score`'s own docstring
                        # for the shared formula (also used by the FAILED-
                        # palier scores, `_playable_score`/`_cleaned_
                        # playable_score`, on the same footing): with a
                        # theme (`priority_words` non-empty), only a
                        # placed word actually belonging to the glossary
                        # counts at all, so the candidate that surfaces
                        # more/longer theme words wins the tie-break, not
                        # merely the one with the longest words overall;
                        # a "Mots Défi" word always counts regardless, at
                        # a bonus-boosted length. An ordinary, non-themed,
                        # challenge-free generation sums every placed
                        # word's own plain length, unchanged.
                        opt_score = _content_score(
                            zip(opt_assignment, opt_slots), priority_words, challenge_words,
                        )
                        scored.append((opt_black, -opt_score, g, r, d))
                    _, _, best, best_result, best_diag = min(scored, key=lambda t: (t[0], t[1]))
                break
            # Every genuinely distinct attempt of this palier, sorted by
            # ascending black-cell count — at the user's explicit
            # request, replacing the previous criterion (the most real
            # letters placed, `assigned_letter_count`, still computed and
            # available in the diagnostics but no longer used for this
            # sort): the "best" failed attempt is now the one whose
            # pattern has the fewest black cells, not the one that
            # advanced furthest in its own fill — consistent with this
            # project's own overall goal of minimizing black cells (see
            # minimize_black_squares), including among the failed
            # attempts used as the next palier's own basis. `last_diag`
            # (the first diagnostics once sorted, so now the one for the
            # pattern most sparing in impossible cells) is still passed
            # through as-is on top, for the detailed log (slot_count/
            # length_counts/checks/reason) already in place. Kept paired
            # with its own pattern (`failed_pairs`, not just the
            # diagnostics) since `_build_retry_seed` below needs the best
            # attempt's real black/white pattern, not just its
            # diagnostics.
            # Sort criterion revised at the user's explicit request: "la
            # meilleure grille est celle qui minimise le nombre de
            # caractères considérés comme injouables" — replaces the old
            # criterion (fewest black cells), which said nothing about how
            # many cells were actually blocked.
            #
            # Briefly replaced by `_playable_score` (the square root of
            # the sum of squares of playable lengths, on the RAW state)
            # once the merge of states published by `best_state_queue` was
            # put in place, to fix a real bias: "fewest impossible cells"
            # wrongly favored a state published early in a still barely-
            # advanced search over a genuinely completed result from
            # another attempt. Briefly reverted to `len(impossible_cells)`
            # once queue-published states were structurally excluded from
            # this selection (see `failed_unique`/`display_pairs` above/
            # below).
            #
            # Replaced one last time by `_cleaned_playable_score`, at the
            # user's explicit request: "Il faut montrer les emplacements
            # avant nettoyage, évaluer la grille après nettoyage (qui sera
            # transmise au cycle suivant si sélectionnée)." Neither
            # `len(impossible_cells)` nor `_playable_score` evaluated the
            # state that actually matters for this selection: the one
            # that will be passed to the next palier if this attempt
            # wins — i.e. the state AFTER `_clean_blocked_slots`, not the
            # raw pre-cleanup state. Two attempts tied on raw impossible-
            # cell count can lose very different amounts of content once
            # cleaned, depending on the length of the word crossing the
            # impossible slot — see `_cleaned_playable_score`'s own
            # docstring for the complete detail.
            failed_pairs = sorted(
                failed_unique,
                key=lambda gd: _cleaned_playable_score(
                    gd[0], gd[1], rows, cols, index, rng, priority_words, challenge_words,
                ),
                reverse=True,
            )
            last_diag = failed_pairs[0][1]
            # A separate pool, reserved for display — never used for
            # `selected_grid`/`selected_diag`/`still_has_hope`/`_build_
            # retry_seed` below, which keep relying exclusively on
            # `failed_pairs` (built above from `failed_unique`, itself
            # built only from real results — see its own comment). Starts
            # from `failed_unique` (a copy, so the list that also drives
            # the real selection is never mutated) then merges in the
            # states published in real time by `best_state_queue`, at the
            # user's explicit request: "Chaque process suit son meilleur
            # état, et transmet au process parent l'information que ce
            # meilleur état a changé. Le process parent garde les 6
            # meilleurs états, de tous les états dont il a été informé par
            # les N process" — later restricted to display only, after a
            # real failure measured live (see `failed_unique`'s own
            # comment above) once it was confirmed that letting these
            # states compete for the real selection degraded progress
            # from one palier to the next. See Filler.on_new_best/
            # _publish_new_best (try_fill) for the publishing side; see
            # best_state_queue above for why it's a Queue (not just an
            # Event) and why it's created only once for the whole
            # generation.
            #
            # Never reads `best_state_queue` directly here anymore — a
            # dedicated thread (`best_state_drain_thread`, started once
            # before the pool is created, see its own comment) drains it
            # continuously into `best_state_buffer`, precisely to avoid a
            # real deadlock observed live: a worker still deep in its own
            # search can publish dozens of times before returning, and a
            # `multiprocessing.Queue`'s own underlying pipe has a bounded
            # capacity — reading it only once per palier, once every
            # worker has come back, gave this pipe time to fill up and
            # block a `put()` before anyone ever read it.
            #
            # `as_completed` has already drained every future of this
            # palier above, so every worker has already finished its own
            # search and issued its last `put()` — but the drain thread
            # may not necessarily have consumed it yet at the exact moment
            # this code runs (it only polls the queue every BEST_STATE_
            # QUEUE_DRAIN_GRACE_S seconds). A short pause, twice this
            # interval, gives the thread at least one full cycle to catch
            # a message just published before this code reads `best_
            # state_buffer` — a bounded trade-off (a few dozen
            # milliseconds per palier, never more), not an absolute
            # guarantee, but a message missed here would simply be
            # handled at the next palier instead of this one, never
            # risking reproducing the deadlock.
            time.sleep(2 * BEST_STATE_QUEUE_DRAIN_GRACE_S)
            with best_state_buffer_lock:
                published_this_palier = best_state_buffer[:]
                best_state_buffer.clear()
            display_seen_keys = set(seen_keys)
            display_unique = list(failed_unique)
            for published in published_this_palier:
                # `grid` removed from the dict after reading (pop, not
                # just get): once extracted into `pub_grid` (the `(grid,
                # diag)` pair's own first element, exactly the same shape
                # as failed_all/failed_unique above), it no longer belongs
                # inside the diagnostic itself — leaving it in would leak
                # a copy of the grid (redundant with `example_grid`) into
                # the JSON sent to the Front.
                pub_grid = published.pop("grid")
                # The same `attempt_id` -> `process_number` translation
                # already applied to `outcomes` above (see `seed_to_
                # lineage`) — needed here too: a state published by
                # `best_state_queue`/`_publish_new_best` does carry
                # `attempt_id` (see its own comment), but never goes
                # through the translation loop above, which only iterates
                # `outcomes` (the raw per-task results), never `best_
                # state_buffer`. Without this line, a state published
                # along the way would keep `process_number` absent
                # despite a genuinely real `attempt_id`.
                published["process_number"] = seed_to_lineage.get(published.get("attempt_id"))
                pub_key = (tuple(map(tuple, pub_grid)), tuple(published["assignment"]))
                if pub_key not in display_seen_keys:
                    display_seen_keys.add(pub_key)
                    display_unique.append((pub_grid, published))
            # Reduced to a single grid per parallel attempt (process), at
            # the user's explicit request: "Actuellement : on garde toutes
            # les meilleures grilles de tous les process (6 max).
            # Modifier : on ne garde qu'une seule meilleure grille par
            # process." Until now, `display_unique` could hold several
            # distinct entries coming from the SAME attempt — its final
            # result (`failed_unique`) AND one or more of its own
            # intermediate publications (`best_state_queue`, each a
            # different snapshot since `assigned_count` grows with every
            # new record) — since the dedup above only compares content
            # (pattern + assignment), never which attempt produced it; a
            # single, very productive attempt could therefore occupy
            # several of the displayed slots on its own (then capped at 6,
            # since removed — see below), at the expense of the same
            # palier's other attempts. `attempt_id` (this exact attempt's
            # own seed, see `try_fill`'s own docstring) now reliably
            # identifies, for every entry — whether it comes from a final
            # result or an intermediate publication — which attempt it
            # came from; grouped by this identifier, only the one with
            # the highest score (`_playable_score`, the RAW state — the
            # same criterion as `display_rest`'s own sort right below)
            # survives per group. `None` (a hypothetical caller that never
            # supplied this identifier — no real case today) is still
            # treated as its own separate entry every time, never merged
            # with anything else, so no distinct entry is ever wrongly
            # collapsed for lack of an identifier.
            best_by_attempt = {}
            for idx, (g, d) in enumerate(display_unique):
                attempt_key = d.get("attempt_id")
                if attempt_key is None:
                    attempt_key = ("__no_attempt_id__", idx)
                score = _playable_score(g, d, rows, cols, priority_words, challenge_words)
                if attempt_key not in best_by_attempt or score > best_by_attempt[attempt_key][0]:
                    best_by_attempt[attempt_key] = (score, (g, d))
            display_unique = [gd for _, gd in best_by_attempt.values()]
            # `failed_pairs[0]` (the real winner — the one that's going to
            # be cleaned via `_clean_blocked_slots` and carried forward to
            # the next palier, see further below) is ALWAYS placed first
            # here, whatever its own score — never left to the normal sort.
            # A real bug observed live, with screenshots backing it up:
            # `display_pairs` and `failed_pairs` using two different sort
            # criteria (the first by `_playable_score`, the second by
            # `len(impossible_cells)`), the very first grid shown on
            # screen could be a completely different attempt from the one
            # genuinely carried forward — even a completely different
            # black/white pattern, not just different content. The user
            # was then correctly comparing "this grid" (the first one
            # shown) against the next step (the next palier's own start,
            # which shows the true carried-forward pattern) and seeing
            # words crossing an impossible situation never removed — when
            # in reality it simply wasn't the same grid at all: the one
            # genuinely carried forward and cleaned was never the one
            # shown first. Guaranteeing the first shown grid is always the
            # one genuinely carried forward makes the "before cleanup
            # (here) / after cleanup (next palier)" comparison valid.
            winner_grid, winner_diag = failed_pairs[0]
            winner_key = (tuple(map(tuple, winner_grid)), tuple(winner_diag["assignment"]))
            # Also excludes, on top of the exact content above, any other
            # entry sharing the SAME attempt (`attempt_id`) as the winner —
            # a genuine duplicate found live once the display cap was
            # removed (see below): `winner_grid`/`winner_diag` come from
            # `failed_pairs[0]` (sorted by `_cleaned_playable_score`, on
            # the state AFTER cleanup), while `display_unique`'s own "one
            # grid per attempt" reduction above sorts by `_playable_score`
            # (the RAW state) — two different criteria that can
            # legitimately keep, for the SAME attempt, two different
            # representatives: the real final result (which became the
            # winner) on one side, an intermediate snapshot published
            # earlier by that same attempt on the other. Without this
            # extra exclusion, the same attempt could appear twice in
            # `display_pairs` — once as the winner, once via its own
            # earlier snapshot — violating "one grid per attempt" even
            # though this content-only filter didn't judge them identical
            # (two genuinely different states, taken at two different
            # moments of the same search).
            winner_attempt_id = winner_diag.get("attempt_id")
            display_rest = sorted(
                (gd for gd in display_unique
                 if (tuple(map(tuple, gd[0])), tuple(gd[1]["assignment"])) != winner_key
                 and (winner_attempt_id is None or gd[1].get("attempt_id") != winner_attempt_id)),
                key=lambda gd: _playable_score(
                    gd[0], gd[1], rows, cols, priority_words, challenge_words,
                ),
                reverse=True,
            )
            display_pairs = [(winner_grid, winner_diag)] + display_rest
            # Every displayed grid shows the state BEFORE cleanup (`d[
            # "example_grid"]`, as-is) — briefly replaced by an already-
            # cleaned version (`_cleaned_example_preview`), reverted at
            # the user's explicit request: "la visualisation des extraits
            # montre maintenant les grilles nettoyées avec des
            # emplacements impossibles vides. On ne comprend plus ce qui
            # se passe. Il faut montrer les emplacements avant nettoyage,
            # évaluer la grille après nettoyage." See `failed_pairs`'s own
            # selection above (`_cleaned_playable_score`) for the
            # evaluation, now genuinely done on the post-cleanup state —
            # only the DISPLAY stays on the raw state, so cells flagged
            # `impossible_cells` stay surrounded by real context (the
            # words that created the conflict) rather than staying blank
            # with no explanation.
            # Every grid of `display_pairs`, with no truncation — at the
            # user's explicit request: "Afficher toutes les meilleures
            # grilles dans l'aperçu, pas seulement les 6 meilleures." A
            # fixed cap (`FAILED_ATTEMPT_EXAMPLES`, 6) used to limit this
            # list; `display_pairs` itself is already reduced to a single
            # entry per parallel attempt (see above), so this list can
            # never exceed `PARALLEL_ATTEMPTS` grids anyway.
            last_examples = _sort_examples_by_process([
                {
                    "example_grid": d["example_grid"],
                    "impossible_cells": d["impossible_cells"],
                    "forced_cells": d["forced_cells"],
                    "locked_cells": d.get("locked_cells", []),
                    "theme_cells": d.get("theme_cells", []),
                    "challenge_cells": d.get("challenge_cells", []),
                    "process_number": d.get("process_number"),
                    # `display_pairs[0]` is ALWAYS the real winner (see
                    # its own comment above) — marked here, before the
                    # sort-by-process below that can move it anywhere in
                    # the displayed list, at the user's explicit request:
                    # "Entourer d'un filet vert la grille considérée
                    # comme la meilleure."
                    "is_best": idx == 0,
                }
                for idx, (g, d) in enumerate(display_pairs)
            ])
            # A late "cases noires posées" preview (pattern with no
            # letters) used to live here, right before `pattern_attempt_
            # failed` — removed at the user's explicit request, once
            # confirmed 100% redundant with it: `pattern_attempt_failed`
            # (right below) already shows the same patterns, plus the
            # genuinely found letters and the complete diagnostics. The
            # only "cases noires posées" preview left is now the early
            # one (see above, before `executor.submit`), published even
            # before the search starts — the late preview added nothing
            # more, only a repeat earlier in the sequence, which gave the
            # misleading impression of a fresh black-cell draw ("il a
            # refait une génération de cases noires, qui a déjà été faite
            # à l'étape précédente").
            progress("pattern_attempt_failed", attempt=attempt + 1, attempts=attempts,
                     ratio=round(ratio, 3),
                     total_attempts=total_attempts_tried, examples=last_examples,
                     **_public_diag(last_diag))
            # "New version" of the cross-palier retry mechanism, at the
            # user's explicit request, replacing the previous attempt
            # ("continue before cleaning", tried then reverted — see below
            # for the history kept): first checks whether the already-
            # selected failed grid (`failed_pairs[0]`, the one with the
            # fewest impossible cells, already used for `last_diag`/
            # `last_examples` above) still has at least one unassigned
            # slot that is NOT impossible — a spot where a word could
            # still be added without cleaning or regenerating anything.
            # If so, the next palier resumes this exact pattern AS-IS
            # (`_pattern_continue`, no call to `make_pattern` at all),
            # locking in every cell already filled and ignoring blockages
            # on slots already known to be impossible — exactly the point
            # that made the previous attempt fail instantly (`checks=1`)
            # once composed into the full loop (see below), since here no
            # new grid is generated on top of an ever-growing locked
            # content: the pattern stays rigorously the same from one
            # "continue" palier to the next, only the locked/excluded
            # content grows. If not (every remaining unassigned slot is
            # impossible — a genuine total blockage for this pattern), it
            # falls back to the existing cleanup (`_build_retry_seed`) and
            # a fresh pattern at the next palier, exactly as before this
            # feature.
            selected_grid, selected_diag = failed_pairs[0]
            # A last resort before any "reprise telle quelle" / nettoyage
            # decision, at the user's explicit request: see `_plug_
            # isolated_cells`'s own docstring for the precise definition
            # of an "isolated" cell and the conditions that trigger it. A
            # `None` (the normal, by far most common case) leaves the rest
            # of this palier completely unchanged — only a grid where
            # NOTHING remains but isolated cells to plug, forming, once
            # done, an entirely filled and valid grid, short-circuits the
            # rest by declaring it directly successful, exactly like a
            # normal CSP success (`best`/`best_result`, used as-is by all
            # the code following the palier loop).
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
            # `_slots_touching`, at the user's explicit request ("ne pas
            # essayer de remplir les emplacements qui croisent un
            # emplacement réputé impossible", see Filler.__init__'s own
            # `_crossing_excluded_slots`): a slot crossing an impossible
            # slot will never be attempted at the next "continue" palier
            # anyway, so it doesn't represent a genuine hope of progress —
            # a real bug found live without this fix: `still_has_hope`
            # stayed `True` indefinitely (these slots counted as non-
            # impossible, so "still promising", even though they would
            # never be attempted), wrongly preventing cleanup from ever
            # triggering — confirmed by 3 real generations failing
            # entirely (200 "continue" paliers exhausted without ever
            # cleaning up) before this fix.
            selected_slots = extract_slots(selected_grid, rows, cols)
            selected_dead = selected_impossible | _slots_touching(selected_slots, selected_impossible)
            still_has_hope = any(
                w is None and i not in selected_dead
                for i, w in enumerate(selected_diag["assignment"])
            )
            # Forced cleanup if all 10 of this palier's attempts were
            # abandoned via the 30% rule (see UNFILLABLE_ABANDON_FRACTION,
            # Filler.abandoned, reason == "abandoned_too_unfillable"), at
            # the user's explicit request: when each one, independently,
            # judged its own pattern too broadly doomed to keep searching,
            # that's a strong signal that a "reprise telle quelle" on this
            # same pattern would be futile — so a cleanup is forced
            # immediately, on the best of these grids (`failed_pairs[0]`,
            # already the basis for the cleanup below), rather than
            # leaving `still_has_hope` to decide alone.
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
            # Cap of MAX_CONSECUTIVE_CONTINUE_PALIERS consecutive
            # "continue" paliers (raised from 5 to 10 then to 50, then
            # brought back down to 10 then to 5, then named and brought
            # down to 1, always at the user's explicit request) — even
            # when `still_has_hope` stays `True`, a cleanup is forced the
            # moment this cap is reached, rather than letting "reprise
            # telle quelle" chain on indefinitely on a pattern that may no
            # longer genuinely be making progress from one palier to the
            # next.
            if consecutive_continue_paliers >= MAX_CONSECUTIVE_CONTINUE_PALIERS:
                still_has_hope = False

            # A new step, at the user's explicit request, inserted here —
            # after the "reprise telle quelle" / nettoyage decision
            # (`still_has_hope`, already settled above) but BEFORE the
            # cleanup itself, whichever kind: "verrouiller tous les
            # emplacements entièrement vides et les éventuelles cases
            # noires avant/après ces emplacements vides, [puis] lancer un
            # cycle d'optimisation... qui ne doit pas toucher aux cases
            # blanches ou noires verrouillées." Applied to EVERY distinct
            # attempt of this palier (`failed_pairs`), not just the best
            # one — see `_optimize_before_cleanup`'s own docstring for the
            # complete detail and why. Its result REPLACES `failed_pairs`
            # for the rest of this palier (`optimized_pairs`): the cleanup
            # that follows, whichever mode is chosen, now operates on the
            # optimized grid, never on the raw pre-step state.
            #
            # This list comprehension can take a while on a grid dense in
            # black cells (see PER_CYCLE_OPTIMIZATION_SAMPLE_SIZE, which
            # bounds this cost without eliminating it) — without the
            # `progress(...)` right below, nothing signals this on screen
            # during this whole computation: the displayed status stayed
            # whatever the very last known event said (typically
            # "pattern_attempt_failed", "nouvelle tentative en cours…"),
            # which could wrongly suggest a brand-new pattern search was
            # underway rather than an optimization of the already-found
            # best grid. Fixed at the user's explicit request ("indiquer
            # clairement qu'une optimisation est en cours") with a
            # dedicated event, fired right before the computation starts —
            # with no `examples` (nothing to show yet), so no effect on
            # `job["examples_history"]`, only on the status text shown
            # live while `_optimize_before_cleanup` runs.
            progress("pre_cleanup_optimizing", attempt=attempt + 1, attempts=attempts,
                     total_attempts=total_attempts_tried)
            optimized_pairs = [
                _optimize_before_cleanup(cand_grid, cand_diag, rows, cols, index, rng,
                                          cancel_event=cancel_event,
                                          permanent_locked_letters=permanent_locked_letters,
                                          permanent_black_cells=permanent_black_cells,
                                          challenge_words=challenge_words)
                for cand_grid, cand_diag in failed_pairs
            ]
            # "Before" preview: already `last_examples`/`pattern_attempt_
            # failed` above, on `failed_pairs`'s own raw state —
            # unchanged, nothing to add here. "After" preview: a new
            # event, on EVERY candidate's own state once optimized,
            # before any cleanup — the same shape as `last_examples`
            # (`example_grid`/`impossible_cells`/`forced_cells`/
            # `locked_cells`/`process_number`) so the display mechanism
            # already in place on the Front side needs no change at all.
            optimized_examples = []
            for idx, ((g, d), (_, cand_diag)) in enumerate(zip(optimized_pairs, failed_pairs)):
                g_slots = extract_slots(g, rows, cols)
                optimized_examples.append({
                    "example_grid": d["example_grid"],
                    "impossible_cells": [cell for i in d["impossible_slots"] for cell in g_slots[i]],
                    # `forced_cells`: never recomputed by `_optimize_
                    # before_cleanup` (no statistical sampling happens
                    # during this step, see its own docstring) — `d`/
                    # `cand_diag` therefore carry rigorously the same
                    # value here, `cand_diag` kept for simplicity.
                    "forced_cells": cand_diag.get("forced_cells", []),
                    # `locked_cells`: MUST come from `d` (`_optimize_
                    # before_cleanup`'s own result), never from `cand_
                    # diag` (the state FROM BEFORE this step) — a real bug
                    # found and fixed, reported directly by the user: "La
                    # grille après optimisation de fin de cycle montre
                    # encore les cases verrouillées du cycle... elles ne
                    # sont pas entourées." `_optimize_before_cleanup` had
                    # already been fixed to rebuild its own `locked_cells`
                    # from scratch (see its docstring), but THIS call site
                    # kept reading the old `cand_diag["locked_cells"]` —
                    # the corrected value therefore never actually reached
                    # the displayed preview. Since `cand_diag["locked_
                    # cells"]` never contains a black cell (this concept
                    # only exists in this step's own result), this same
                    # bug also explains why no locked black cell ever
                    # appeared in this preview at all.
                    "locked_cells": d.get("locked_cells", []),
                    # `theme_cells`: never recomputed by `_optimize_
                    # before_cleanup` (no word sampling/recomposition
                    # happens during this step) — `cand_diag` (the
                    # before-state, from `try_fill`) carries the relevant
                    # value, the same choice as `forced_cells` right
                    # above.
                    "theme_cells": cand_diag.get("theme_cells", []),
                    "challenge_cells": cand_diag.get("challenge_cells", []),
                    "process_number": d.get("process_number"),
                    # `failed_pairs[0]` (index 0, before any sort-by-
                    # process below) is this palier's genuine winner — see
                    # `_sort_examples_by_process`'s own docstring.
                    "is_best": idx == 0,
                })
            optimized_examples = _sort_examples_by_process(optimized_examples)
            progress("pre_cleanup_optimized", attempt=attempt + 1, attempts=attempts,
                     total_attempts=total_attempts_tried, examples=optimized_examples)

            if still_has_hope:
                consecutive_continue_paliers += 1
                just_cleaned = False
                # Automatic cleanup of blocked slots, at the user's
                # explicit request: "à la fin d'un tour, nettoyer
                # automatiquement les emplacements bloqués, mais pas les
                # noires." Removes, even before resuming "telle quelle" at
                # the next palier, any word directly crossing an
                # impossible slot (`_clean_blocked_slots`, steps 1-2 of
                # `_build_retry_seed` without its 3rd step) — now applied
                # to EVERY distinct attempt of this palier (`failed_
                # pairs`), not just the "best" one (`selected_grid`/
                # `selected_diag`) as before this feature — at the user's
                # explicit request: "Regression : après un cycle, le cycle
                # suivant repart maintenant avec une seule grille. Quand il
                # n'y a pas de déclenchement d'un nettoyage complet, chaque
                # process doit repartir à l'étape suivante avec sa grille
                # partiellement nettoyée (sauf le pourcentage de grilles
                # entièrement neuves)." See `_clean_continue_candidate`
                # (module level, right after `_build_retry_seed`) for the
                # exact detail — the same logic, including the 1/10
                # black-cell alternative (`BLACK_CELL_INSTEAD_OF_REMOVAL_
                # PROBABILITY`, see its own docstring for the complete
                # reasoning), applied once per attempt instead of once on
                # the winner alone.
                cleaned_continue_candidates = _sorted_by_score(
                    (
                        _clean_continue_candidate(
                            cand_grid, cand_diag, rows, cols, index, rng,
                            permanent_locked_letters=permanent_locked_letters,
                            permanent_black_cells=permanent_black_cells,
                            challenge_words=challenge_words,
                        )
                        for cand_grid, cand_diag in optimized_pairs
                    ),
                    priority_words=priority_words, challenge_words=challenge_words,
                )
                carry_seed_pool_continue = _continue_seed_pool(cleaned_continue_candidates)
                carry_seed_grid, carry_preseed_assignment, carry_excluded_slots = (
                    carry_seed_pool_continue[0]
                )
                carry_locked_letters = None
                # See `carry_seed_pool_lineage`'s own definition (before
                # the palier loop) for this list's own role — extracted
                # via `_seed_pool` a second time (same selection, same
                # order as `carry_seed_pool_continue` above, since built
                # on the same already-sorted `cleaned_continue_
                # candidates`) but drawing `sc[5]` (each candidate's own
                # inherited lineage number, see `_clean_continue_
                # candidate`) instead of `(sc[0], sc[3], sc[4])`. `None`
                # for a candidate coming from a reset task with no
                # lineage to inherit — `_reassign_lineage_numbers`
                # assigns it one, preferring a lineage that didn't survive
                # this palier (`dispatch_lineage`, the ones active at
                # this palier's own entry), at the user's explicit
                # request: "La grille entièrement nouvelle doit reprendre
                # le numéro de la grille qui disparaît (normalement, la
                # moins bonne)."
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
                # A new cross-palier retry algorithm, at the user's
                # explicit request (see _build_retry_seed): cleans every
                # distinct attempt of THIS palier (see `_clean_all_
                # candidates` below for the exact scope), not just the
                # first one — each one loses a different number of
                # letters at cleanup's own step 1 (removing words
                # crossing an impossible slot) depending on the precise
                # shape of its own blockage, so the one that looked "best"
                # before cleanup (fewest black cells) isn't necessarily
                # the one that keeps the most information once cleaned.
                # `best_slots` is recomputed here (instead of being
                # returned by the worker) — a deterministic, cheap
                # computation from the black/white pattern alone, not
                # worth widening `_pattern_attempt`/`try_fill`'s own
                # return contract just to avoid it.
                # "Continue adding words before cleaning" (giving each
                # candidate a second fill chance, excluding the slot
                # already identified as impossible via `Filler.excluded_
                # slots`) was tried here then reverted, at the user's
                # explicit request, after a real test showed a serious
                # regression: verified correct in isolation (see `Filler.
                # excluded_slots`, still in place and functional) but,
                # composed into the full loop, a previously healthy
                # palier (15×10, seed 2, 62.7s, 0 mismatch right before
                # this change) got stuck instantly (`checks=1`) on 199 of
                # 200 paliers — the locked content growing progressively
                # every round (`slot_count` 43→56) without ever becoming
                # genuinely fillable again. Exact cause not identified
                # before reverting to the version before this mechanism;
                # replaced by the "New version" above, which resumes the
                # pattern as-is (no regeneration) instead of composing a
                # resume-with-exclusion on top of a pattern still
                # regenerated every palier — see the project-best-
                # practices SKILL for the complete history.
                def _clean_all_candidates(force_exclude):
                    # EVERY distinct attempt of this palier (up to
                    # PARALLEL_ATTEMPTS, not just the FAILED_ATTEMPT_
                    # EXAMPLES (6) shown on screen — that cap remains a
                    # DISPLAY cap, see `display_pairs`/`last_examples`
                    # above, unrelated to the real selection here), at the
                    # user's explicit request: "on garde la meilleure
                    # grille de tous les process, soit N grilles pour N
                    # process." `failed_pairs` already carries, by
                    # construction, at most one entry per attempt (see its
                    # own comment above) — no need for an extra per-
                    # attempt dedup here.
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

                # Among the cleaned grids, the winner maximizes the sum of
                # squares of the lengths of words in place *after*
                # cleanup (a word is "in place" if all of its cells appear
                # in `confirmed`) — at the user's explicit request,
                # replacing the old criterion (most letters remaining,
                # tie-broken by fewest black cells). The same score
                # formula that breaks ties among successful parallel
                # attempts earlier in this function (favors a few long
                # words over many short ones for the same total letter
                # count) — applied here to the result *after* cleanup (the
                # real, useful signal for restarting), not to a pre-
                # cleanup criterion as before.
                #
                # `_words_in_place_score`/`_candidate_black_count`/
                # `_sorted_by_score`/`_seed_pool` (the score, its tie-break
                # by black-cell count, the sort combining them, and the
                # reduction to the pool passed to the next palier) are now
                # module-level functions, right after `_build_retry_seed`
                # — hoisted out of this local closure at the user's
                # explicit request, once the same logic was also needed
                # for "reprise telle quelle" (see `_clean_continue_
                # candidate`/`_continue_seed_pool`, and further below, `if
                # still_has_hope:`); see their own docstrings for the
                # complete reasoning (in particular the black-cell tie-
                # break, added after a real stuck state observed live on a
                # large, heavily locked 30×30 grid).

                previous_locked_letters = carry_locked_letters
                cleaned_candidates = _sorted_by_score(
                    _clean_all_candidates(force_exclude=False),
                    priority_words=priority_words, challenge_words=challenge_words,
                )
                carry_seed_pool = _seed_pool(cleaned_candidates)
                carry_seed_grid, carry_locked_letters = carry_seed_pool[0]
                # See `carry_seed_pool_lineage`'s own definition (before
                # the palier loop) for this list's own role — the same
                # mechanism as for "reprise telle quelle" above (see
                # `raw_continue_lineage`), but on `cleaned_candidates`
                # (position 3 = inherited lineage number, see `_clean_all_
                # candidates`). Rebuilt a second time further below if the
                # fixed point below forces a second, more aggressive
                # cleanup, so it always reflects whichever `cleaned_
                # candidates` was actually used last.
                raw_lineage = _seed_pool(cleaned_candidates, extract=lambda sc: sc[3])
                carry_seed_pool_lineage, next_lineage_number = _reassign_lineage_numbers(
                    raw_lineage, dispatch_lineage, next_lineage_number
                )
                # A fixed point detected: this palier produced no change
                # at all (the confirmed letters are rigorously identical
                # to the previous palier's own) — a genuine blockage that,
                # without intervention, would reproduce identically
                # forever (see `_build_retry_seed`'s own docstring for the
                # complete history of this case). At the user's explicit
                # request, this is only handled as a last resort, only
                # once this blockage is genuinely observed: the same
                # cleanup is rerun on all the same candidates with
                # `exclude_impossible_locked=True`, which specifically
                # removes any locked slot whose combination matches no
                # real word — breaking the fixed point without ever
                # applying this more aggressive rule to paliers that are
                # progressing normally.
                #
                # A finer version (comparing, for every raw parallel
                # attempt rather than just the winner, whether a real
                # assignment happened) was tried then abandoned at the
                # user's explicit request ("il n'essaye pas vraiment de
                # remplir les grilles partielles... revenir à la situation
                # précédente") — back to this simpler comparison on the
                # winner alone (the pool's own new diversity above, by
                # contrast, covers EVERY cleaned grid, not just the
                # winner — two distinct concerns, one about fixed-point
                # detection, the other about diversity for the next
                # launch).
                if previous_locked_letters is not None and carry_locked_letters == previous_locked_letters:
                    cleaned_candidates = _sorted_by_score(
                        _clean_all_candidates(force_exclude=True),
                        priority_words=priority_words, challenge_words=challenge_words,
                    )
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
                # Remembering the state obtained at the end of THIS
                # cleanup (black/white pattern AND confirmed content) and
                # detecting a state that repeats identically from one
                # cleanup to the next — see GRID_REPEAT_INFEASIBLE_
                # THRESHOLD's own docstring for the full request and its
                # history. Reserved for this branch alone (`if still_has_
                # hope:` above, "reprise telle quelle", never touches it)
                # — at the user's explicit request, after TWO regressions
                # measured live on the standard 15×10 (Flash) benchmark: a
                # first version only compared the black/white pattern, on
                # both branches — the pattern very often stays identical
                # across several consecutive "reprise telle quelle" cycles
                # by construction (cleanup only adds a black cell one time
                # in ten, see BLACK_CELL_INSTEAD_OF_REMOVAL_PROBABILITY)
                # even while content is progressing normally, mistaking
                # this for a genuine blockage. A second version compared
                # pattern+content, still on both branches — a detailed
                # diagnostic (branch + the consecutive_continue_paliers
                # counter at every trigger) showed most triggers coincided,
                # on the "reprise telle quelle" branch, almost exactly with
                # the moment MAX_CONSECUTIVE_CONTINUE_PALIERS already
                # forces a cleanup on its own — this mechanism was then
                # duplicating an already-tuned safeguard, but with a far
                # more destructive response (an entirely blank grid instead
                # of an ordinary cleanup that keeps the valid content).
                # Restricting detection to this branch alone fixes both
                # problems at once: a "reprise telle quelle" cycle never
                # counts toward the streak (already bounded elsewhere),
                # and only a genuine fixed point of the cleanup ITSELF
                # (one the single relaunch with `exclude_impossible_
                # locked=True`, right above, doesn't always resolve)
                # triggers the reset. Reuses `_cycle_start_preview`
                # (already called elsewhere in this same loop for the
                # "cycle start" preview) to merge pattern + content into a
                # single comparable grid — `carry_preseed_assignment` is
                # always `None` on this branch, so `_cycle_start_preview`
                # always builds from `carry_locked_letters` here. Compared
                # as a tuple of tuples (hashable, content comparison, not
                # identity) rather than the list itself — `locked_cells`
                # (the 2nd return value) isn't useful here, only the
                # merged grid serves as the key.
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
                    # Pattern deemed infeasible: a full reset, the next
                    # cycle starts again from an entirely blank grid —
                    # exactly this function's own initial state (see
                    # `carry_seed_grid = None` at the very top), including
                    # both pools and the "reprise telle quelle" streak
                    # counter, so a "fresh pattern" palier genuinely
                    # starts from scratch rather than reusing a pool
                    # built from the now-abandoned pattern.
                    carry_seed_grid = None
                    carry_locked_letters = None
                    carry_preseed_assignment = None
                    carry_excluded_slots = None
                    carry_seed_pool = None
                    carry_seed_pool_continue = None
                    carry_seed_pool_lineage = None
                    carry_seed_pool_continue_lineage = None
                    # `next_lineage_number` itself is deliberately NOT reset
                    # here: a grid born from a future palier must never
                    # reuse the number of a lineage abandoned by this
                    # reset, at the risk of making the user think it's the
                    # same grid as before.
                    consecutive_continue_paliers = 0
                    last_cycle_end_grid = None
                    same_grid_streak = 0
            # The target ratio no longer progresses from one palier to the
            # next (stays fixed at `black_ratio`, 0.0 by default), at the
            # user's explicit request: pre-fill (at least PREFILL_MIN_
            # WORD_COUNT candidates per slot) combined with resuming on
            # the previous palier's own cleaned grid (_build_retry_seed
            # right above) is enough to make the search progress, with no
            # need to artificially densify the grid palier after palier.

    # Clean shutdown of the `best_state_queue` drain thread (see its own
    # docstring above) — the search itself is finished (success or
    # `attempts` exhausted), nothing more will ever be published to it.
    # `daemon=True` would guarantee it never blocks the process from
    # ending anyway if this point weren't reached (e.g.
    # `GenerationCancelled`, raised from inside the loop above, never
    # coming back through here) — this explicit shutdown is purely a
    # matter of hygiene in the normal case, not a safeguard the program's
    # correctness depends on.
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

    # Preview of the grid right before optimization, reusing the same
    # mechanism as a failed attempt's own preview (try_fill's
    # diagnostics["example_grid"]). Now contains the real letters
    # (`build_letters_grid`, the same function already used for
    # `result["solution"]` below), not just the bare black/white pattern —
    # reverted at the user's explicit request from this preview's very
    # first version (which deliberately omitted them): on the client
    # side, `renderAttemptPreview()` already hides these letters by
    # default and only reveals them if the user activates #attempt-
    # preview-reveal-btn (see style-guide SKILL), so passing them here
    # doesn't actually display them — it's the same hiding mechanism as a
    # failed attempt, not a new one. `best_result` is `(slots,
    # assignment)` (see _pattern_attempt/try_fill's own return contract)
    # — passed as-is to build_letters_grid, which builds a brand-new grid
    # (never a modification of `best` in place), so no defensive copy is
    # needed here, unlike the old version which passed `best` itself.
    # `impossible_cells`/`forced_cells`/`locked_cells` are explicitly
    # cleared (not simply omitted) to erase any preview left over from a
    # previously failed attempt during the pattern search — an entirely
    # successful pattern has no impossible cell, no forced letter, and no
    # locked cell to report.
    # Passed via `examples` (a single-element list) — the same shape as
    # `pattern_attempt_failed`/`pattern_failed` above (up to 6 elements) —
    # so backend/app.py and the frontend only need one single preview
    # mechanism to handle, whether it's 1 grid or 6.
    best_slots, best_assignment = best_result
    # Lineage number of the task that genuinely produced `best` (see
    # `best_diag`, `seed_to_lineage`) — `None` for the one success path
    # with no real worker behind it (`_plug_isolated_cells`). Passed both
    # into the "minimizing" preview below and into the final result, so
    # backend/app.py can also attach it to the "clues" preview.
    winning_process_number = best_diag.get("process_number") if best_diag else None
    progress(
        "minimizing",
        examples=[{
            "example_grid": build_letters_grid(rows, cols, best_slots, best_assignment),
            "impossible_cells": [],
            "forced_cells": [],
            "locked_cells": [],
            "theme_cells": _theme_word_cells(best_slots, best_assignment, priority_words),
            "challenge_cells": _challenge_word_cells_from_assignment(
                best_slots, best_assignment, challenge_words
            ),
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
    # `required_cells` (see this function's own docstring/try_fill's own)
    # — "Finir la zone" — can leave a slot genuinely unresolved
    # (`answer` is `None`) as long as it never touched a required cell.
    # Such a slot is never a real word: no clue could ever be generated
    # for it, and the word-verification table/theme-cells computation
    # downstream (backend/app.py) must never see it either — dropped here,
    # once and for all, rather than relying on every caller to filter it
    # out itself. A complete no-op whenever `required_cells` was never
    # given (every `answer` is already non-`None` in that case).
    words = [w for w in words if w["answer"] is not None]
    # On a bilingual grid, every vertical ("down") word receives its own
    # accented spelling/canonical root(s) IN THE SECOND LANGUAGE rather
    # than the first, and now carries its own `language` — the code of
    # the language genuinely used for THIS exact word (see `bilingual_
    # wordlist_path`'s own docstring) — consumed by backend/clues.py (one
    # definition per word in its own language) and backend/chatbot.py (a
    # hint in the right language depending on the word). On a monolingual
    # grid (`bilingual_active` false), every word still receives
    # `language` — always the same value — with no other change to
    # behavior.
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
    solution = build_letters_grid(rows, cols, slots, assignment)
    # Safety net for `required_cells`/"Finir la zone": a locked cell whose
    # BOTH crossing slots end up unresolved (an edge case — one of the two
    # is virtually always assigned in practice, but never guaranteed once
    # a slot can legitimately stay `None` forever) would otherwise show up
    # as a stray black cell in `solution`, silently losing a letter the
    # player typed themselves. `permanent_locked_letters` is always
    # correct regardless of how the search went, so it's reapplied here
    # unconditionally — a genuine no-op whenever every one of these cells
    # was already covered by a real assignment (the overwhelmingly common
    # case, and the ONLY case for every pre-existing caller).
    if permanent_locked_letters:
        for (r, c), ch in permanent_locked_letters.items():
            solution[r][c] = ch
    return {
        "width": cols,
        "height": rows,
        "pattern": grid,
        "solution": solution,
        "words": words,
        "word_count": len(slots),
        "black_count": n_black,
        "black_ratio": n_black / (rows * cols),
        "winning_process_number": winning_process_number,
        # Primary language (horizontal words) and the bilingual grid's own
        # language (vertical words, `None` for an ordinary monolingual
        # grid) — at the user's explicit request, so backend/app.py/
        # backend/grid_store.py can record both without having to
        # re-derive them from `wordlist_path` themselves.
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
        # argparse itself runs a % substitution pass over help strings
        # (for %(default)s etc.) — a literal "%" coming from {:.0%} above
        # must be escaped to "%%" *after* formatting (never inside
        # .format()'s own format-spec, which only accepts "%"), otherwise
        # argparse raises a ValueError.
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
