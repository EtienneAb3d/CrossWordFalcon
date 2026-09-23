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
     attempt. A single success is never enough to conclude the search on
     its own (`MIN_SUCCESSFUL_ATTEMPTS`, cumulative across every palier
     of the whole search, currently 2, at the user's explicit request):
     until that many genuine successes have been found, every worker
     process a success frees up is immediately reassigned to a brand-new,
     from-scratch attempt instead of sitting idle for the rest of its own
     palier. Once enough successes are in hand, whichever one maximizes
     the sum of squares of all its own word lengths (after a fewest-
     black-cells comparison) is kept, not simply the first one found.
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
    (`False` by default, kept only for a caller outside `make_pattern`
    that might still want the old last-resort behavior; `make_pattern`
    itself always passes `forbid_adjacency=True`, at the user's explicit
    request — originally only for the very first, entirely white grid of
    a `generate_grid()` call ("When first initializing black cells,
    forbid any draw that would place 2 black cells with an adjacent
    side"), then widened to every palier without exception, including
    one resuming an already partially-blackened `seed_grid` from a
    previous palier: "La génération de motif cases noires (au début de
    chaque cycle) ne doit jamais poser de case noire adjacente à une case
    noire déjà posée. Si l'objectif de pourcentage ne peut pas être
    atteint sans poser des cases adjacentes, laisser la grille telle
    quelle." — in which case this very last attempt (accepting adjacency)
    is skipped entirely). No isolated candidate found across the whole
    window at this point then behaves exactly like the residual case
    below — the best candidate is refused and removed from the pool, the
    loop continues with the rest of the pool, never a crash or a
    deadlock — so a palier whose density target can't be reached without
    adjacency simply ends up short of `target`, left as-is, rather than
    forcing an adjacent cell: the CSP fill is attempted on that grid as
    it stands, exactly like any other case where `target` isn't fully
    reached (see this function's own `Returns` note and `make_pattern`'s
    own docstring). In the residual case where even this finds nothing
    across the whole window (all 32 candidates break connectivity or
    create an orphaned cell, or — with `forbid_adjacency` — are all
    adjacent to an already-black cell), the best candidate by the main
    criterion is simply refused and removed from the pool, to guarantee
    the loop always makes progress.

    This prohibition is scoped to pattern generation itself
    (`make_pattern`, called once at the start of every palier/cycle,
    before the CSP fill even starts) — it says nothing about, and never
    constrains, the cross-palier cleanup mechanisms (`_clean_blocked_
    slots`/`_build_retry_seed`) or the impossible-zone-resolution passes
    (`_shorten_impossible_zones`/`_lengthen_impossible_zones`/
    `interactive_clean_impossible_zones`), every one of which may still
    place or relocate a black cell adjacent to an existing one when
    that's what repairing an already-impossible zone requires.

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
    cell, or unfixable).

    `forbid_adjacency` (`False` by default): when true, a candidate cell
    of the problematic slot that would touch an already-black cell is
    never tried at all — only `non_adjacent` options are considered,
    rather than falling back to an adjacent one once the non-adjacent
    ones are exhausted. `make_pattern` (this function's only real caller)
    always passes `True`, at the user's explicit request that pattern
    generation itself never place two black cells with an adjacent side,
    on any palier — see `make_pattern`'s own docstring. A slot that can't
    be fixed this way falls through to `_remove_a_crossing_word`, then to
    `unfixable`, exactly as already described above — never to placing an
    adjacent black cell."""
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

    `black_enrichment_fraction` ("Taux noir") is a fraction of the WHOLE
    grid (`rows * cols`), black cells already present included: `target`
    is `round(black_enrichment_fraction * rows * cols)`, compared against
    `placed`, which already counts `seed_grid`'s own black cells and
    whatever pre-fill placed. Only the shortfall is added by
    `_place_black_cells`; a grid already at or above the target gets no
    further cell for this reason. The same unscaled rate is the curative-
    cleanup zone budget inside `_prefill_unfillable_slots`
    (`fill_objective_fraction`). The rate applies at the very first
    palier and at every palier immediately following a full cleanup —
    never at a "reprise telle quelle" palier, which never calls this
    function.

    Adjacency between two black cells is never accepted at all, on any
    palier — not just the very first, entirely white one (`seed_grid is
    None`), but equally one resuming an already partially-black `seed_
    grid` carried forward from a previous palier — at the user's explicit
    request: "La génération de motif cases noires (au début de chaque
    cycle) ne doit jamais poser de case noire adjacente à une case noire
    déjà posée. Si l'objectif de pourcentage ne peut pas être atteint sans
    poser des cases adjacentes, laisser la grille telle quelle." Every one
    of this function's own three calls into `_place_black_cells`/
    `_prefill_unfillable_slots` therefore always passes `forbid_
    adjacency=True` — see `_place_black_cells`'s own docstring for the
    mechanics, and for why this only ever leaves the grid short of its
    own density target rather than forcing an adjacent cell to reach it.
    This is a property of pattern generation alone: the cross-palier
    cleanup mechanism (`_build_retry_seed`, run *between* paliers, not by
    this function) and the impossible-zone-resolution passes (`_shorten_
    impossible_zones`/`_lengthen_impossible_zones`) remain free to place
    or relocate a black cell adjacent to an existing one, exactly as
    before, when repairing an already-impossible zone calls for it."""
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
            forbid_adjacency=True,
        )

    placed = sum(row.count(BLACK) for row in grid)
    # "Taux noir" is a fraction of the whole grid, black cells already
    # present included: `placed` already counts the seed's own black cells
    # and pre-fill's, so only the shortfall is added below.
    target = max(
        placed,
        round(rows * cols * black_ratio),
        round(black_enrichment_fraction * rows * cols),
    )
    _place_black_cells(grid, rows, cols, row_black, col_black, candidates, target, placed,
                        index=index, locked_letters=locked_letters, available_lengths=available_lengths,
                        forbid_adjacency=True)

    if available_lengths is not None and locked_letters:
        _prefill_unfillable_slots(
            grid, rows, cols, row_black, col_black, candidates, available_lengths,
            index, locked_letters, rng, fill_objective_fraction,
            forbid_adjacency=True,
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


_LIGATURE_FOLD = str.maketrans({"œ": "oe", "Œ": "OE", "æ": "ae", "Æ": "AE"})


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
    therefore ever matched) as "RANDONNES", not "RANDONNEES". A ligature
    letter (French `œ`/`Œ`/`æ`/`Æ`) is folded into its two separate
    letters BEFORE that NFKD step, the same way `strip_accents` does —
    NFKD alone never decomposes it (it's an atomic code point, not a
    combining-mark sequence), so without this the final `[^A-Z]` filter
    would otherwise just delete it outright: "sœur" would become "SUR",
    not "SOEUR"."""
    stripped = "".join(
        c for c in unicodedata.normalize("NFKD", word.translate(_LIGATURE_FOLD))
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

# Frequency (in checks elapsed, see Filler._periodic_checkpoints) at which a CSP search checks
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

# Check frequency (in checks elapsed) for `Filler.on_checks_
# progress` (see its own docstring and `_worker_checks_progress`): cheap
# enough (a single shared-memory write, no lock contention worth avoiding)
# to report far more often than `on_new_best` itself ever fires — the
# whole point is to keep the web UI's "% budget consumed" indicator
# genuinely live even through a long stretch with no new record to
# publish, at the user's explicit request: "Ce n'est pas normal que rien
# ne bouge... le Backtracking devrait tester des combinaisons
# différentes" (it does; the previous indicator just couldn't show it).
CHECKS_PROGRESS_REPORT_INTERVAL = 500

# Check frequency (in checks elapsed) for `Filler.on_live_
# state`: a periodic snapshot of the search's CURRENT assignment — as
# opposed to `on_new_best`, which only fires on a new record — at the
# user's explicit request: "La grille doit bouger aussi. L'algo doit
# tester de nouvelles combinaisons [et ça doit se voir]." During a long
# stretch with no new record (see CHECKS_PROGRESS_REPORT_INTERVAL above),
# the live preview grid used to stay frozen on the last record reached,
# even though `_backtrack` keeps placing and reverting many genuinely
# different words underneath the whole time — indistinguishable, from the
# player's own point of view, from the search having actually stopped.
# Coarser than CHECKS_PROGRESS_REPORT_INTERVAL: each firing costs a real
# (if cheap — no dictionary/domain lookup, purely a per-cell copy over the
# already-known slots/cells) `build_partial_letters_grid` call, so this
# stays roughly at the same order of magnitude as how often `on_new_best`
# already fires in practice (~50-60 times over a 300 000-check attempt,
# i.e. every ~5000-6000 checks) rather than piggy-backing on the much
# tighter interval above.
LIVE_STATE_HEARTBEAT_INTERVAL = 5000

# Whether early abandonment of an attempt (see UNFILLABLE_ABANDON_SLOT_COUNT
# right below) is active at all. Optional, at the user's explicit request,
# and currently disabled: when False, Filler._backtrack's own checkpoint for
# this mechanism is a no-op — an attempt is never abandoned early just for
# accumulating too many impossible slots, whatever UNFILLABLE_ABANDON_
# SLOT_COUNT is set to; it only ever stops via the deadline/cancel/sibling-
# done checks around it, or by naturally exhausting its own search tree.
UNFILLABLE_ABANDON_ENABLED = False

# Threshold (a fixed number of impossible slots) and check frequency (in
# checks elapsed) for early abandonment of an attempt: the
# moment more than this many still-unassigned slots are deemed impossible
# (see Filler.impossible_zone_slots), the attempt is judged to have no
# reasonable hope left and is abandoned on the spot. See Filler._backtrack —
# checked periodically (like CANCEL_CHECK_INTERVAL above, not on every
# call) since computing impossible slots (impossible_zone_slots) has a
# real, non-negligible cost if repeated at every node of a search that can
# visit hundreds of thousands of them. Only takes effect when UNFILLABLE_
# ABANDON_ENABLED (above) is True.
UNFILLABLE_ABANDON_SLOT_COUNT = 3
UNFILLABLE_ABANDON_CHECK_INTERVAL = 500

# Check frequency (in checks elapsed) for the "another
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

# Minimum number of genuinely successful grids (cumulative across the
# WHOLE search, every palier included — not just the current one)
# required before generate_grid() stops the search and keeps the best
# one, at the user's explicit request: a single success is no longer
# enough to conclude. Until this threshold is reached, every worker
# process freed by a success is immediately reassigned to a brand-new,
# from-scratch attempt (see the palier's harvesting loop further below)
# instead of sitting idle — the existing `attempts` budget (200 paliers
# by default) remains the only cap: once it's exhausted, whichever
# single success was actually found is accepted rather than declaring a
# total failure (see the `best is None and accumulated_successes:`
# fallback near the end of generate_grid).
MIN_SUCCESSFUL_ATTEMPTS = 2

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
# end a search attempt (deadline exceeded, too-many-impossible-slots
# abandon, interrupted by a
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

# Repetition thresholds of one cleaned grid across CONSECUTIVE full
# cleanups (a "reprise telle quelle" palier in between neither counts nor
# resets them). Each cleaned candidate of a full cleanup is compared, by
# its own state (black/white pattern AND confirmed content, merged by
# `_cycle_start_preview`), with every candidate of the previous full
# cleanup; `streak` is how many consecutive cleanups have produced it.
# Always measured on the ORDINARY cleanup's result, so a grid the deep
# cleanup below has just reshaped is still recognized if its next attempt
# rebuilds the same dead end. At `GRID_REPEAT_DEEP_CLEANUP_STREAK` (2) the
# candidate is cleaned deeper instead (`_build_retry_seed(deep=True)`:
# also removes every word crossing a word removed by the ordinary
# cleanup, plus any fully-locked slot spelling no real word); at
# `GRID_REPEAT_DISCARD_STREAK` (3) it is dropped from the pool and its
# place in the next palier goes to a worker starting from a blank grid,
# while every other candidate — the grids still progressing — is kept.
# Only when every candidate is dropped does the whole search restart
# from a blank grid. Scoped to the full cleanup: on "reprise telle
# quelle" a stable pattern is normal, and MAX_CONSECUTIVE_CONTINUE_PALIERS
# already bounds that branch.
GRID_REPEAT_DEEP_CLEANUP_STREAK = 2
GRID_REPEAT_DISCARD_STREAK = 3

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
    `target_indices` — used by `generate_grid` so the "still_has_hope"
    computation treats a slot crossing an impossible one as hopeless too,
    rather than as a still-promising slot (see below for why)."""
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
# sorted by `_candidate_score` (see `Filler.ordered_candidates`, the one
# place this draw is implemented, used by the automatic search and by
# Interactive mode's "Suivant" alike) — a bit like `_place_black_cells`'s
# own 32-cell window for black cells: it keeps the overall priority on the
# statistically best-scored words while avoiding trying them in exactly
# the sort order, which would amount to an entirely deterministic choice
# (for a given seed) rather than genuine exploration.
#
# The value is what arbitrates between those two ends. A window wider than
# a slot's whole domain cancels the scoring out entirely — every candidate
# is then in the window at every draw, so the order is a plain uniform
# shuffle and a rare word is as likely as a well-scored one. A narrow
# window keeps the statistical ranking genuinely in charge, letting only
# the best-scored candidates be reached first, while still leaving enough
# room for two attempts (or two "Suivant" clicks) on the same state to
# diverge.
CANDIDATE_SCORE_WINDOW = 50

# Maximum number of recursive descents a single `Filler._backtrack` node
# makes before giving up and handing control back to its parent. A
# descent is a candidate that passed the crossing check and was recursed
# into; a candidate rejected on the spot by that check does not count,
# and neither does a "Mots Défi" or theme-glossary candidate (the node
# tries every hypothesis from those two glossaries, whatever its count).
# The count spans the whole node — every slot it tries, the released
# "écarté" stage and the `allow_breaking` pass alike. Without this cap a
# node only fails once its whole subtree is exhausted, which on a real
# dictionary never happens within the budget, so backtracking never
# climbs more than a few levels and a hard word placed early stays in
# place for the rest of the attempt. `<= 0` disables the cap (every
# option of the node is tried).
MAX_DESCENTS_PER_NODE = 3

# Early-attempt relaxation of MAX_DESCENTS_PER_NODE: a node reached while
# the search has placed fewer than `EARLY_DESCENTS_WORD_COUNT` words on top
# of the attempt's own initial state (the words already in place when
# `Filler.solve` starts: preseeded/locked ones) may make up to
# `EARLY_MAX_DESCENTS_PER_NODE` descents instead. Measured per node, from
# the words in place when the node is entered.
EARLY_DESCENTS_WORD_COUNT = 5
EARLY_MAX_DESCENTS_PER_NODE = 10

# Conflict-directed backjumping in `Filler._backtrack`. A node that fails
# reports WHICH already-placed words its failure depends on (its conflict
# set: the words crossing the slot it could not fill, or holding the last
# word that slot could have taken). Each ancestor whose own word is not in
# that set cannot be the cause — changing it would replay the very same
# failure — so it passes the failure straight up instead of trying its
# other candidates. Backtracking thereby jumps directly to the most recent
# word actually involved, however many unrelated levels lie in between,
# instead of re-exploring them one by one. False restores plain
# chronological backtracking.
BACKJUMPING_ENABLED = True

# Maximum number of "emplacements écartés" `Filler._impossible_this_attempt`
# holds at once: only the most recently added ones are kept, the oldest
# being dropped as a new one comes in. The list exists to steer the search
# away from the slots that just proved troublesome; once most of the grid
# is in it, deprioritizing it no longer steers anything.
MAX_EXCLUDED_SLOTS = 3


class _RecentSlots:
    """Set of slot indices capped at `capacity` members, keeping the most
    recently added: adding a slot already present makes it the most recent
    again, and adding one beyond capacity drops the oldest. Supports the
    set operations the engine uses (`add`, `|=`, `discard`, `in`,
    iteration, `len`)."""

    def __init__(self, capacity):
        self.capacity = capacity
        self._order = {}

    def add(self, i):
        self._order.pop(i, None)
        self._order[i] = None
        while len(self._order) > self.capacity:
            del self._order[next(iter(self._order))]

    def __ior__(self, other):
        for i in other:
            self.add(i)
        return self

    def discard(self, i):
        self._order.pop(i, None)

    def __contains__(self, i):
        return i in self._order

    def __iter__(self):
        return iter(list(self._order))

    def __len__(self):
        return len(self._order)

# Level 1 of the slot-selection cascade (see `Filler._select_target_slot`
# below): whether to first split `unassigned` by direction and draw which
# one (across or down) to restrict the rest of the cascade to, weighted by
# each direction's own remaining open-slot count. Optional, at the user's
# explicit request, and currently disabled: when False, level 1 is a
# no-op and the cascade starts straight from the whole `unassigned` pool
# (both directions together) at level 2.
ALTERNATE_DIRECTION_ENABLED = False

# Size (fixed, not a proportion of the group) of the final draw window
# among the retained group of slots (`selection_pool`, see `Filler.
# _backtrack`, "Choose which slot to fill first"): only the `SLOT_
# SELECTION_WINDOW_SIZE` slots with the smallest geometric score (see the
# computation right above) are kept, regardless of `selection_pool`'s own
# size — never fewer if the group has fewer slots than this size (`[:N]`
# on a shorter list simply returns the whole list).
SLOT_SELECTION_WINDOW_SIZE = 10

# Level 7 of the slot-selection cascade (most-constrained cell, see
# `Filler._select_target_slot`): slot-length threshold, lowered one step
# at a time. Only slots of at least `MOST_CONSTRAINED_START_LENGTH`
# letters are measured first; if none of the window has a measurable free
# cell at that threshold, the threshold drops by one and the measure is
# repeated, down to `MOST_CONSTRAINED_MIN_LENGTH`. Applied within level
# 6's geometric window, not the whole group. Long slots are thus
# resolved on their tightest cell first, short ones only once no longer
# slot is left to measure.
MOST_CONSTRAINED_START_LENGTH = 7
MOST_CONSTRAINED_MIN_LENGTH = 2

# Once the window above is obtained (`window`, sorted by ascending
# geometric score, then narrowed to its most-constrained slots),
# `Filler._backtrack` re-sorts it a second time by the
# number of letters already placed in each slot (the most letters first —
# `_placed_letter_count`, the same fait-acquis/mere-guess distinction as
# `_has_known_letter`), then reduces it again to its own first `SLOT_
# SELECTION_REFINE_FRACTION` slots (the ones best supplied with already-
# known letters) before the final draw — at the user's explicit request,
# who also raised this proportion from 1/4 to 1/2 in the same move (a
# milder reduction, keeping half rather than a quarter of `window`).
# Floor **always at 1 slot, never 0**: `window` itself holds at most
# `SLOT_SELECTION_WINDOW_SIZE` slots and can hold as few as one, and a
# higher floor here would cancel out the requested reduction in this very
# common case (it would force the whole window to be kept as-is) — this
# reduced window (`refined_window`) can therefore never end up empty,
# whatever `window`'s size or this fraction's value.
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
# Also a fraction of a whole-search resource (`deadline_checks`), the same
# shape `UNFILLABLE_ABANDON_SLOT_COUNT` above would have if it were a
# fraction rather than a fixed slot count — `interactive_place_word`
# applies the same principle to its own challenge-word combo search, with
# a differently-shaped budget (a combination count rather than a check
# count, see its own docstring); its theme/general-dictionary fallback
# instead scans every available candidate at its one target slot
# exhaustively (see its own docstring for why no separate budget is needed
# there).
FALLBACK_PHASE_BUDGET_FRACTION = 0.10



# Per-length letter counts of a whole index length list, keyed by the
# list's own `id` (the dict is kept alongside, so a reused id can never be
# mistaken for it): an entirely blank slot's domain is that whole list, and
# counting it on every query cost 10+ ms per call on a large lexicon.
# Built once per process per length, the lists being immutable for the
# index's lifetime.
_BLANK_LETTER_COUNTS = {}


def _blank_letter_counts(idx, length):
    """`(counts, members)` for `idx["words"]` (every word of `length`):
    one `{letter: count}` dict per position, and the words as a
    frozenset."""
    entry = _BLANK_LETTER_COUNTS.get(id(idx))
    if entry is None or entry[0] is not idx:
        counts = [{} for _ in range(length)]
        for w in idx["words"]:
            for pos, ch in enumerate(w):
                c = counts[pos]
                c[ch] = c.get(ch, 0) + 1
        entry = (idx, counts, frozenset(idx["words"]))
        _BLANK_LETTER_COUNTS[id(idx)] = entry
    return entry[1], entry[2]


def _letters_from_counts(counts, removed_words):
    """Per-position letter sets of `counts` once each word of
    `removed_words` (all members of the counted domain, each counted
    once) is taken out."""
    deltas = [{} for _ in counts]
    for w in removed_words:
        for pos, ch in enumerate(w):
            d = deltas[pos]
            d[ch] = d.get(ch, 0) + 1
    return [
        {ch for ch, n in c.items() if n - d.get(ch, 0) > 0}
        for c, d in zip(counts, deltas)
    ]


class Filler:
    def __init__(self, slots, index, rng, forced_letters=None, letter_scores=None,
                 excluded_slots=None, cancel_event=None, batch_abandoned_event=None,
                 attempt_done_event=None, on_new_best=None, locked_letters=None,
                 priority_words=None, challenge_words=None, rows=None, cols=None):
        self.slots = slots
        self.index = index
        self.rng = rng
        # Grid dimensions, at the user's explicit request: needed only to
        # compute the grid's own center for `_select_target_slot`'s level 6
        # (see there) — every real caller (`try_fill`/`interactive_place_
        # word`/`_interactive_fill_diagnostics`/`_build_interactive_filler`)
        # already has `rows`/`cols` in scope and passes them through; `None`
        # remains a safeguard for a direct caller that doesn't (e.g. a
        # test), in which case they're inferred from the slots' own cells —
        # correct as long as at least one slot actually reaches the grid's
        # last row/column, which is true for every real grid (a wholly
        # black last row/column would never happen in practice, but isn't
        # guaranteed by construction either).
        self.rows = rows if rows is not None else (
            max((r for cells in slots for r, c in cells), default=-1) + 1
        )
        self.cols = cols if cols is not None else (
            max((c for cells in slots for r, c in cells), default=-1) + 1
        )
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
        #
        # `letter_scores` arrives per direction ({cell: {"across"/"down":
        # Counter}}, see sample_letter_biases) and is kept that way in
        # `letter_scores_by_dir` — refreshed direction by direction as
        # words are placed (`_refresh_letter_scores_around`) and crossed
        # (`_crossed_letter_counts`) wherever the letters actually still
        # possible at a cell are wanted (`_slot_min_letter_options`,
        # `stat_letters`). `self.letter_scores` is the summed view the
        # candidate ordering reads (`_candidate_score`/`_slot_letter_
        # frequency_score`).
        self.letter_scores_by_dir = {
            cell: dict(by_dir) for cell, by_dir in (letter_scores or {}).items()
        }
        self.letter_scores = {
            cell: _combined_letter_counts(by_dir)
            for cell, by_dir in self.letter_scores_by_dir.items()
        }
        # Direction of every slot, for `_refresh_letter_scores_around`.
        self.slot_directions = [slot_direction(cells) for cells in slots]
        # The previews' statistical letters (`stat_letters`) as they stood
        # when `best_assignment` was recorded — `letter_scores` follows the
        # search's CURRENT assignment and is unwound with it, so by the
        # time a caller reports `best_assignment` it may no longer describe
        # it. `None` until the first record (see `best_stat_letters_for`).
        self.best_stat_letters = None
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
        # Turns True the moment an attempt is abandoned along the way for
        # lack of reasonable hope (see _backtrack and UNFILLABLE_ABANDON_
        # SLOT_COUNT) — once set, every following call to _backtrack fails
        # immediately, with no further exploration.
        self.abandoned = False
        # Set when a `_backtrack` call returned because the check budget
        # ran out (see `_deadline_reached_without_extension`), so `solve()`
        # can tell a strict search cut short from one genuinely exhausted.
        self._budget_exhausted = False
        # Whether a node may enter its last-resort `allow_breaking` stage.
        # False for the whole strict search; `solve()` turns it on only
        # once that strict search has been exhausted from the root.
        self.breaking_permitted = False
        # Open slots whose dry domain is NOT a reason to backtrack: those
        # already dry before the search placed anything (set in `solve()`
        # — undoing this search's own placements can never revive them),
        # plus, in the last-resort pass only, the slots an accepted
        # `allow_breaking` word deliberately dried (added on placement,
        # removed as it is reverted). Every other dry slot a node finds
        # was dried by a placement of this search, and makes it backtrack.
        self._tolerated_dry = set()
        # Conflict set of the last `_backtrack` call that returned False
        # (see BACKJUMPING_ENABLED): the slots holding the placed words its
        # failure depends on, or None when the failure says nothing about
        # the grid (budget, abandon, backjumping disabled) and the caller
        # must backtrack chronologically.
        self._last_conflict = None
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
        # Slots structurally outside this search's own scope: never
        # selected for an assignment attempt, never required by
        # `truly_complete` (see try_fill), and never counted as a broken
        # crossing. This is NOT an "emplacement écarté" (see `_impossible_
        # this_attempt` below and `DOC_ALGO/FR/Lexicon.md`) — an écarté
        # slot always stays available as a last resort, whereas a slot
        # named here is deliberately left out of the grid this search has
        # to solve at all, and is never surfaced by any diagnostic.
        # `_optimize_before_cleanup` is its only caller: it needs a fill
        # that genuinely succeeds while leaving an entirely-empty or
        # already-impossible zone untouched. Empty for every other caller,
        # in which case this whole mechanism is a no-op.
        self.excluded_slots = excluded_slots if excluded_slots is not None else set()
        self.used_words = set()
        self.checks = 0
        # "Emplacements écartés" (see `DOC_ALGO/FR/Lexicon.md`): slots
        # found blocked at least once during THIS attempt. Set aside —
        # tried only once no other slot can take a word — but never walled
        # off: they stay available as a last resort, and never trigger a
        # backtrack on their own. Reset to nothing at the start of every
        # attempt, never inherited from a previous palier. Fed from three
        # sources:
        # - `mark_immediately_impossible_slots()`, run once before the
        #   search starts, for a slot already dry under the definitive
        #   constraints alone. Purely a head start: `_backtrack`'s own
        #   per-node check below would find exactly the same slots on its
        #   very first call anyway.
        # - `_backtrack`'s own ordinary per-node domain check finding an
        #   unassigned slot dry — that event already happens on every node
        #   regardless of this feature, so recording it here costs nothing
        #   extra. The frequent, common case.
        # - `excluded_zone_cells`'s own `include_deadlock=True` calls (see
        #   its docstring), which merge in the other way a slot can be
        #   "impossible" — non-empty domain in isolation, yet jointly
        #   unfillable with a slot it crosses (`_crossing_deadlock_slots`).
        #   Deliberately NOT also scanned proactively inside `_backtrack`
        #   itself on every node: that scan iterates every still-open
        #   slot's own full domain, which can be tens of thousands of words
        #   on an early, still-mostly-empty grid — even throttled to once
        #   every few hundred calls, a search with a large check budget
        #   (e.g. the web UI's "Ultra" mode) still triggers it often enough
        #   for its cost to dwarf the search itself, stalling visible
        #   progress for a long time on exactly the grids with the most
        #   empty space left to fill. So a deadlock is only ever caught at
        #   the comparatively rare cadence `_publish_new_best`/the final
        #   diagnostics snapshot already pay this same cost at regardless —
        #   one arising and resolving entirely between two such snapshots
        #   is simply never seen.
        # Capped at the MAX_EXCLUDED_SLOTS most recently added slots, and a
        # slot leaves it as soon as a word placed across it leaves it no
        # longer blocked (see `_backtrack`) — a slot that just went dry is a
        # cheap, if imperfect, signal that it's worth avoiding as a search
        # target while a less troublesome slot is still available.
        # Only ever used to bias which of several already-viable slots
        # `_backtrack` works on next, never to reject one outright: a slot
        # in `unassigned` always has a genuinely non-empty domain regardless
        # of this set's contents, and this set is dropped back to only
        # deprioritizing (not excluding) the moment it would otherwise leave
        # no slot selectable at all. This is also exactly the set surfaced
        # as the yellow "écarté" overlay (`excluded_zone_cells`).
        self._impossible_this_attempt = _RecentSlots(MAX_EXCLUDED_SLOTS)
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
        # Words already in place when `solve()` starts (see
        # EARLY_DESCENTS_WORD_COUNT).
        self._initial_assigned_count = 0
        # Called back (see _backtrack) every time best_assignment has just
        # been improved, with this new state as an argument — lets
        # try_fill publish this new state to the parent process in real
        # time instead of only once, right at the very end of the search,
        # at the user's explicit request (see `_worker_best_state_queue`,
        # further down in this file, for the full history). `None` by
        # default — no effect for any pre-existing caller.
        self.on_new_best = on_new_best
        # Called back (see _backtrack, CHECKS_PROGRESS_REPORT_INTERVAL)
        # every so many checks, REGARDLESS of whether a new record was
        # reached — unlike `on_new_best` above, so a caller can report
        # genuine, continuous search progress (see `_worker_checks_
        # progress`'s own docstring) even through a long stretch where no
        # attempt beats its own previous best, which `on_new_best` alone
        # would leave completely silent. Set directly on the instance by
        # `try_fill`, never passed through this constructor — same
        # convention already used for `on_new_best` itself. `None` by
        # default — no effect for any caller that never sets it.
        self.on_checks_progress = None
        # Called back (see _backtrack, LIVE_STATE_HEARTBEAT_INTERVAL) every
        # so many checks with `self.assignment` (the search's CURRENT
        # state — can be LOWER than `self.best_assignment` at that exact
        # moment, since backtracking freely retreats and re-advances)
        # REGARDLESS of whether it's a new record — unlike `on_new_best`,
        # which only ever sees the high-water mark. Lets a caller show the
        # live preview grid genuinely changing while the search works
        # through a long unproductive stretch, instead of freezing on the
        # last record reached. Set directly on the instance by `try_fill`,
        # same convention as `on_new_best`/`on_checks_progress` above.
        # `None` by default — no effect for any caller that never sets it.
        self.on_live_state = None
        # Set directly on the instance by `try_fill`, same convention as
        # `on_checks_progress`/`on_live_state` above — read-only shared
        # references (`checks_progress`/`attempt_active`, see `generate_
        # grid`'s own definitions) plus this attempt's own array slot,
        # letting `_backtrack`'s deadline check (below) see whether any
        # sibling attempt of the same palier is still genuinely racing
        # towards its own `deadline_checks`, at the user's explicit
        # request: "Autoriser les grilles à dépasser leur budget, tant
        # qu'il reste des grilles qui ne l'ont pas atteint (CPU en attente
        # pour rien)." All three stay `None` for every caller that never
        # sets them (interactive mode, `minimize_black_squares`, etc.) —
        # `_backtrack` then falls back to the original, unconditional
        # "stop the instant the budget is exceeded" behavior.
        self._sibling_checks_progress = None
        self._sibling_attempt_active = None
        self._checks_slot = None
        # Once a periodic re-check (see `_deadline_reached_without_
        # extension`) finds no sibling still racing, this flips to `True`
        # and STAYS `True` for the rest of this attempt — a "stop" verdict
        # must be sticky, exactly like the plain `self.checks > deadline_
        # checks` condition it replaces: that plain condition is itself
        # irreversibly true forever once `self.checks` first exceeds
        # `deadline_checks` (a monotonic counter), which is what makes
        # ordinary backtracking unwind the ENTIRE remaining search tree —
        # every candidate at every level hits the same unconditional
        # `return False`, not just the one candidate being tried at the
        # moment the deadline was first crossed. A throttled, non-sticky
        # re-check would only reject ONE candidate at a time: the very
        # next candidate in that same slot's own loop calls `_backtrack`
        # again on a check count that no longer happens to land on a
        # checkpoint, sees no reason to stop, and gets placed — silently
        # defeating the whole mechanism (found live: an attempt with no
        # racing sibling ran to full completion regardless, instead of
        # stopping within a handful of checks past its own deadline).
        self._deadline_extension_denied = False
        # Last `self.checks` value at which each periodic checkpoint of
        # `_periodic_checkpoints` fired, keyed by its interval constant's
        # role. `self.checks` advances once per candidate tried, most of
        # which are rejected without recursing, so it routinely jumps over
        # any exact multiple of an interval between two passes through a
        # checkpoint — a checkpoint fires once at least its interval has
        # elapsed since it last did, never on an exact-multiple match.
        self._last_checkpoint_checks = {}

    def _checkpoint_due(self, name, interval):
        """True once `interval` checks have elapsed since checkpoint
        `name` last fired (or on its very first call), recording the
        current `self.checks` as its new reference when it does. A
        `self.checks` lower than the recorded reference (the counter was
        restarted) counts as due too."""
        last = self._last_checkpoint_checks.get(name)
        if last is None or self.checks - last >= interval or self.checks < last:
            self._last_checkpoint_checks[name] = self.checks
            return True
        return False

    def _periodic_checkpoints(self):
        """Every periodic, interval-throttled signal of the search —
        cancellation, progress/heartbeat reporting, sibling-driven early
        stops — evaluated at `_backtrack`'s entry AND after every candidate
        counted in its candidate loop, so a long stretch of rejected
        candidates that never recurses still reports and still stops.
        Raises `GenerationCancelled`; returns True when the search must
        stop now (`self.abandoned` already set), False otherwise."""
        if (
            self.cancel_event is not None
            and self._checkpoint_due("cancel", CANCEL_CHECK_INTERVAL)
            and self.cancel_event.is_set()
        ):
            raise GenerationCancelled()
        if (
            self.on_checks_progress is not None
            and self._checkpoint_due("progress", CHECKS_PROGRESS_REPORT_INTERVAL)
        ):
            self.on_checks_progress(self.checks)
        if (
            self.on_live_state is not None
            and self._checkpoint_due("heartbeat", LIVE_STATE_HEARTBEAT_INTERVAL)
        ):
            self.on_live_state(self.assignment)
        # Early stop of the WHOLE batch the moment a sibling attempt has
        # abandoned itself (see _worker_batch_abandoned_event and
        # UNFILLABLE_ABANDON_SLOT_COUNT): no point waiting for this attempt
        # to reach its own abandon threshold or budget once another one has
        # already judged the shared pattern hopeless.
        if (
            self.batch_abandoned_event is not None
            and self._checkpoint_due("batch_abandoned", UNFILLABLE_ABANDON_CHECK_INTERVAL)
            and self.batch_abandoned_event.is_set()
        ):
            self.abandoned = True
            return True
        # Early stop as soon as ANOTHER attempt of the same palier already
        # answered (see attempt_done_event in generate_grid). Applies to
        # both _pattern_attempt and _pattern_continue (see Filler.__init__'s
        # own docstring); `interrupted_by_sibling` lets try_fill report this
        # specific cause in `diagnostics["reason"]`.
        if (
            self.attempt_done_event is not None
            and self._checkpoint_due("attempt_done", PALIER_ATTEMPT_DONE_CHECK_INTERVAL)
            and self.attempt_done_event.is_set()
        ):
            self.abandoned = True
            self.interrupted_by_sibling = True
            return True
        # Early abandonment of an attempt (see UNFILLABLE_ABANDON_ENABLED/
        # UNFILLABLE_ABANDON_SLOT_COUNT): more than that many still-
        # unassigned slots deemed impossible (impossible_zone_slots, on
        # best_assignment) means no reasonable hope is left. Currently
        # disabled, and throttled when enabled: impossible_zone_slots
        # recomputes every unassigned slot's domain.
        if (
            UNFILLABLE_ABANDON_ENABLED
            and self._checkpoint_due("unfillable", UNFILLABLE_ABANDON_CHECK_INTERVAL)
            and len(self.impossible_zone_slots()) > UNFILLABLE_ABANDON_SLOT_COUNT
        ):
            self.abandoned = True
            # Signals every other attempt of the same batch that it can
            # stop too — see the batch_abandoned checkpoint above.
            if self.batch_abandoned_event is not None:
                self.batch_abandoned_event.set()
            return True
        return False

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

        Used by _backtrack as level 9's final tie-break criterion (see its
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

    def _slot_min_letter_options(self, i):
        """The smallest number of letters still possible on any STILL-FREE
        cell of slot i, or `None` when it has no such cell to measure.

        Reads `self.letter_scores_by_dir` — the very tally Interactive
        mode's "Stats" button displays (`_interactive_letter_stats`, the
        same `sample_letter_biases` sampling), crossed between the two
        directions (`_crossed_letter_option_count`): how many distinct
        letters were observed at that cell by BOTH the across and the down
        slot through it, among real words compatible with the letters
        already known. A cell whose two directions share no letter counts
        0 — the tightest a cell can be, so its slot is taken first. Being
        a sample, a 0 is never treated as a crossing deadlock: only the
        real-domain check (`slot_is_blocked`) decides that. A cell already determined by a real letter
        (a crossing word assigned this attempt, or `self.locked_letters`)
        is skipped, exactly as in `_slot_letter_frequency_score`/`_placed_
        letter_count` — its letter is settled, so it offers no remaining
        choice to measure, and counting it would report 1 for every
        partially-filled slot in the grid. A cell absent from `letter_
        scores` (every slot through it sampled empty) is skipped too, the
        same omission `_interactive_letter_stats` itself makes.

        Used by `_select_target_slot`'s own most-constrained-cell level.
        """
        best = None
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
            by_dir = self.letter_scores_by_dir.get(cell)
            if not by_dir:
                continue
            count = _crossed_letter_option_count(by_dir)
            if count is None:
                continue
            if best is None or count < best:
                best = count
        return best

    def stat_letters(self, assignment=None):
        """The most likely letter of every white cell `assignment`
        (`self.assignment` by default) leaves undetermined — neither
        written by an assigned slot, nor in `self.locked_letters`, nor
        carrying a seed (`self.forced_letters`, which the preview already
        shows as a letter of its own) — read
        from the cell's crossed tally (`_most_probable_letter`), as a
        sorted `[r, c, letter]` list. What the automatic-generation
        previews show in light gray, the same figure as Interactive mode's
        "Stats" button. A cell with no tally, or whose two directions share
        no letter, is omitted. Only meaningful for the assignment the
        tally currently follows (see `best_stat_letters`)."""
        if assignment is None:
            assignment = self.assignment
        fixed = set(self.locked_letters) | set(self.forced_letters)
        for i, word in enumerate(assignment):
            if word is not None:
                fixed.update(self.slots[i])
        out = []
        for cell, by_dir in self.letter_scores_by_dir.items():
            if cell in fixed:
                continue
            letter = _most_probable_letter(by_dir)
            if letter is not None:
                out.append([cell[0], cell[1], letter])
        out.sort()
        return out

    def best_stat_letters_for(self):
        """`stat_letters` for `best_assignment`: the snapshot taken when it
        was recorded, or, before any record (a preseeded `best_
        assignment`), the tally as it stands, which has not moved yet. The
        snapshot is filtered against `best_assignment` as it is NOW, since
        `_close_implied_slots` can still write words into it after the
        search ends — a cell it filled no longer has a letter to guess."""
        if self.best_stat_letters is None:
            return self.stat_letters(self.best_assignment)
        filled = set()
        for i, word in enumerate(self.best_assignment):
            if word is not None:
                filled.update(self.slots[i])
        return [entry for entry in self.best_stat_letters
                if (entry[0], entry[1]) not in filled]

    def _refresh_letter_scores_around(self, i):
        """Re-sample the letter statistics of every still-open slot crossing
        slot `i`, right after a word has been written on `i`, and return
        what is needed to undo it (`_restore_letter_scores`).

        `self.letter_scores` otherwise comes from one `sample_letter_
        biases` pass run before the search starts, so it only ever knows
        the letters that were locked back then. The cells a crossing slot
        still has free are exactly the ones the word just placed on `i`
        has newly constrained, so they are re-tallied here against that
        slot's own CURRENT domain (`_domain`, which already accounts for
        the new assignment): the same sampling `sample_letter_biases`
        does, restricted to the slots the placement can actually have
        changed anything for.

        Bounded by construction: at most one crossing slot per cell of
        `i`, each costing one `_domain` call plus `LETTER_BIAS_SAMPLE_
        SIZE` draws — `_backtrack` already computes `_domain` for every
        unassigned slot on every node, so this adds a small fraction of
        what the node pays anyway. A slot with no valid letter left (an
        empty domain) is skipped rather than re-tallied, as is one whose
        every cell is already determined: there is nothing left to
        measure, and the stale tally is simply left alone.

        Each refreshed slot REPLACES the tally of its own cells rather
        than adding to it. `sample_letter_biases` sums the contributions
        of both slots crossing a cell, but the other contributor here is
        `i`, whose letters are now fixed — its tally says nothing useful
        about a cell whose letter is settled — so the crossing slot's own
        fresh sample is the whole of what is still to be measured there.

        The per-direction tally (`letter_scores_by_dir`) is updated in step:
        the refreshed slot's own direction entry is replaced at each of its
        cells, the other direction's entry left as it stands, so crossing
        the two (`_crossed_letter_counts`) always confronts the freshest
        sample of each side. Both views are restored together.
        """
        saved = {}
        seen = set()
        for cell in self.slots[i]:
            for j, _pos in self.cell_to_slots[cell]:
                if j == i or j in seen or self.assignment[j] is not None:
                    continue
                seen.add(j)
                cands = self._domain(j)
                if not cands:
                    continue
                sample = self.rng.choices(list(cands), k=LETTER_BIAS_SAMPLE_SIZE)
                direction = self.slot_directions[j]
                for pos, jcell in enumerate(self.slots[j]):
                    if jcell not in saved:
                        by_dir = self.letter_scores_by_dir.get(jcell)
                        saved[jcell] = (
                            self.letter_scores.get(jcell),
                            dict(by_dir) if by_dir is not None else None,
                        )
                    fresh = Counter(word[pos] for word in sample)
                    self.letter_scores[jcell] = fresh
                    self.letter_scores_by_dir.setdefault(jcell, {})[direction] = fresh
        return saved

    def _restore_letter_scores(self, saved):
        """Undo `_refresh_letter_scores_around` — called as the placement it
        followed is reverted, so a tally never outlives the assignment it
        was measured against. A cell that had no tally at all before is
        removed again rather than left holding an empty Counter."""
        for cell, (counts, by_dir) in saved.items():
            if counts is None:
                self.letter_scores.pop(cell, None)
            else:
                self.letter_scores[cell] = counts
            if by_dir is None:
                self.letter_scores_by_dir.pop(cell, None)
            else:
                self.letter_scores_by_dir[cell] = by_dir

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

    def ordered_candidates(self, i, cands):
        """The order in which slot `i`'s candidate words are to be tried —
        the ONE candidate-ordering rule of this engine, shared by the
        automatic search (`_backtrack`) and by Interactive mode's own
        "Suivant" (`_general_dictionary_pick`/`_find_priority_word_
        placement`), which is a step-by-step version of the same search
        and must therefore draw its word exactly the same way.

        Three stages, in this order:

        1. the candidates are always shuffled first (with this search's
           own seeded RNG, hence reproducible) — whether that shuffle
           serves as the final draw (`letter_scores` empty, the case for
           a direct `Filler` caller that supplies none) or only to break
           ties in the sort right below, `sort` being stable;
        2. they are sorted by `_candidate_score` (sum of squares of the
           statistical letter scores over the cells no crossing word has
           fixed yet), so a word matching the statistical consensus on
           several free cells is tried before one that matches it nowhere;
        3. the order is NOT strictly descending even so: each successive
           pick is drawn at random among the `CANDIDATE_SCORE_WINDOW` best
           words *still remaining* (not the first `CANDIDATE_SCORE_WINDOW`
           of the original sort, fixed once and for all — the window
           slides as words leave it). See that constant's own docstring
           for what it balances.

        `cands` is never modified: the returned list is a fresh one."""
        cands = list(cands)
        self.rng.shuffle(cands)
        if not self.letter_scores:
            return cands
        cands.sort(key=lambda w: self._candidate_score(i, w), reverse=True)
        window = CANDIDATE_SCORE_WINDOW
        reordered = []
        remaining = cands
        while remaining:
            take = min(window, len(remaining))
            idx = self.rng.randrange(take)
            reordered.append(remaining.pop(idx))
        return reordered

    def mark_immediately_impossible_slots(self):
        """Flags as "écarté" (`_impossible_this_attempt`, see its own
        docstring in `__init__` and `DOC_ALGO/FR/Lexicon.md`) every slot
        already blocked under this search's definitive constraints alone,
        before the search itself starts.

        To be called once, right before `solve()` (so after the caller
        has applied `preseed_assignment`, if any — see try_fill) and
        before any call to `_backtrack`: at this exact moment, `self.
        assignment` still only contains genuinely locked cells (no search
        decision has been made yet), so every still-unassigned slot's own
        domain reflects only definitive constraints.

        Purely a head start for the deprioritization: `_backtrack`'s own
        per-node domain check would flag exactly the same slots on its
        very first call anyway. Flagging them here simply means the very
        first slot selection already prefers a healthier slot, instead of
        spending that one node discovering it. Like every other écarté
        slot, one flagged here stays fully available as a last resort and
        is picked back up on its own the moment backtracking makes its
        domain non-empty again.

        A single pass is enough (no need to loop back to a fixed point):
        flagging a slot never changes any other slot's own computed
        domain — `_domain` never consults this set at all, it only biases
        which slot `_backtrack` selects first."""
        # No word can have been abandoned yet (see _active_challenge_words):
        # this method only ever runs once, before solve()/_backtrack ever
        # gets a chance to break a crossing slot — resolved once here
        # regardless, for consistency with every other caller of this
        # method rather than reading self.challenge_words directly.
        newly_flagged = self._dry_open_slots()
        self._impossible_this_attempt |= newly_flagged
        return newly_flagged

    def _dry_open_slots(self):
        """Every still-open slot (not in `excluded_slots`) with no
        candidate left in the CURRENT assignment: dictionary domain minus
        `used_words` empty, and no unused, still-active "Mots Défi" word
        fitting it — the same test `_backtrack`'s own per-node domain
        check applies."""
        active_challenge_words = self._active_challenge_words()
        return {
            i for i in range(len(self.slots))
            if self.assignment[i] is None
            and i not in self.excluded_slots
            and all(w in self.used_words for w in self._domain(i))
            # Same "Mots Défi" exemption as _backtrack's own domain check
            # (see its comment): a slot with a dry dictionary domain is
            # not dry if an unused challenge word still fits it — the
            # challenge mechanism, not the dictionary, is what would fill
            # it.
            and not (active_challenge_words and any(
                w not in self.used_words and self._challenge_word_fits(i, w)
                for w in active_challenge_words
            ))
        }

    def _assigned_crossers(self, i):
        """Slots crossing slot `i` that currently hold a word — the
        placements that fixed `i`'s known letters."""
        return {k for k in self._crossing_slots[i] if self.assignment[k] is not None}

    def _dry_slot_conflict(self, i):
        """Conflict set of dry slot `i`: the words crossing it, plus the
        slots holding a word `i` could otherwise have taken (its domain is
        non-empty but every word of it is already used elsewhere)."""
        conflict = self._assigned_crossers(i)
        domain = self._domain(i)
        if domain:
            holder = {w: k for k, w in enumerate(self.assignment) if w is not None}
            conflict.update(holder[w] for w in domain if w in holder)
        return conflict

    def _fail(self, conflict):
        """Return False from `_backtrack`, reporting `conflict` (a set of
        slots, or None for "unknown") to the caller."""
        self._last_conflict = (
            frozenset(conflict) if BACKJUMPING_ENABLED and conflict is not None else None
        )
        return False

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
        # Strict search first: no node may create an impossible slot. Only
        # if it is exhausted from the root — every word placed first having
        # been put back into question, within MAX_DESCENTS_PER_NODE per
        # node — and not merely cut short by the budget or an abandon, is
        # the search replayed with the last-resort stage allowed. The first
        # pass leaves the assignment exactly as it found it (every
        # placement is reverted on the way back up), and `best_assignment`
        # keeps whatever record it reached.
        self._tolerated_dry = self._dry_open_slots()
        self._initial_assigned_count = sum(1 for a in self.assignment if a is not None)
        if self._backtrack(deadline_checks):
            return True
        if self.abandoned or self._budget_exhausted:
            return False
        self.breaking_permitted = True
        return self._backtrack(deadline_checks)

    def _slot_letter_options(self, i, used_words, challenge_words=()):
        """Per-position sets of letters slot `i` can still legitimately
        take, given the CURRENT assignment: every letter appearing at that
        position in one of its genuinely available candidates (`_domain(i,
        ignore_forced=True)` minus `used_words`, plus any still-fitting
        challenge word — exactly the sources `impossible_zone_slots`
        itself reads).

        The single place this computation lives: `_crossing_deadlock_
        slots` (the whole-grid diagnostic) and `slot_is_blocked` (the
        per-candidate check both the automatic search and Interactive
        mode's "Suivant" go through) both call it, so "which letters can
        still go here" can never mean two different things in two places.

        A slot with no known letter at all has the whole length list as
        its domain; its letters are then read from that list's
        precomputed per-position counts (`_blank_letter_counts`) minus the
        used words it contains, instead of a scan of tens of thousands of
        words.

        An empty set at position 0 means the slot has no candidate at all
        — every other position is then empty too, so `not options[0]` is
        the whole "plus aucun mot possible" test."""
        cells = self.slots[i]
        domain = self._domain(i, ignore_forced=True)
        idx = self.index.for_cells(cells).get(len(cells))
        if idx is not None and domain is idx["words"]:
            base, members = _blank_letter_counts(idx, len(cells))
            used_in_domain = [u for u in used_words if len(u) == len(cells) and u in members]
            letters = _letters_from_counts(base, used_in_domain)
        else:
            letters = [set() for _ in cells]
            for w in domain:
                if w in used_words:
                    continue
                for pos, ch in enumerate(w):
                    letters[pos].add(ch)
        self._add_challenge_letters(i, letters, used_words, challenge_words)
        return letters

    def _add_challenge_letters(self, i, letters, used_words, challenge_words):
        for cw in challenge_words:
            if cw not in used_words and self._challenge_word_fits(i, cw):
                for pos, ch in enumerate(cw):
                    letters[pos].add(ch)

    def slot_is_blocked(self, i, used_words, challenge_words=(), options_cache=None,
                        fresh=(), placed_word=None):
        """Is slot `i` an "emplacement bloqué" (red) in the CURRENT
        assignment? The ONE definition of that term used everywhere in the
        engine — `DOC_ALGO/FR/Lexicon.md`, and exactly what the red
        overlay renders:

        1. no candidate left at all (its own domain, minus words already
           placed elsewhere, is empty and no unused challenge word fits);
        2. or a crossing cell where it and the still-open slot crossing it
           there agree on no letter at all ("case croisée bloquée") — each
           slot's own domain can be perfectly healthy in isolation.

        Case 2 is the reason this method exists rather than the plain
        empty-domain test the per-candidate checks used to do on their
        own: that test cannot see a deadlock, so a word could be placed
        crossing a slot the interface was already painting red.

        `options_cache` (a plain dict, one per search node) memoises
        `_slot_letter_options` across the many candidates tried at the
        same node. Two things can invalidate an entry, and both are
        handled rather than approximated: placing a word on slot `k` only
        ever changes the domain of a slot CROSSING `k`, so the caller
        passes exactly `self._crossing_slots[k]` as `fresh` and those are
        cached per known-letter signature rather than per slot; and the
        word being tried is itself now used, so any
        OTHER slot whose own domain happens to contain it loses whatever
        letters only that word supplied — `placed_word` names it, and an
        entry's per-letter counts say exactly which letters it was the last
        supporter of (see `_letter_options_cached`). A
        stale entry here would read as "this slot still has options",
        i.e. would hide a real blockage, which is exactly what this
        method exists to catch."""
        opts = self._letter_options_cached(i, used_words, challenge_words,
                                           options_cache, fresh, placed_word)
        if not opts or not opts[0]:
            return True
        for pos, cell in enumerate(self.slots[i]):
            own = opts[pos]
            for j, jpos in self.cell_to_slots[cell]:
                if j == i or self.assignment[j] is not None or j in self.excluded_slots:
                    continue
                other = self._letter_options_cached(j, used_words, challenge_words,
                                                    options_cache, fresh, placed_word)
                # A crossing slot with no option at all is blocked in its
                # own right (case 1, reported when IT is examined) and is
                # never made the cause of a deadlock here — same asymmetry
                # `_crossing_deadlock_slots` applies.
                if other and other[jpos] and not (own & other[jpos]):
                    return True
        return False

    def _letter_options_cached(self, i, used_words, challenge_words, options_cache,
                               fresh, placed_word=None):
        """See `slot_is_blocked` for what this cache may and may not
        reuse. Always returns exactly what `_slot_letter_options(i,
        used_words, challenge_words)` would.

        An entry holds the slot's per-position letter COUNTS over its
        domain minus the node's own used words — `used_words` without the
        candidate being tried (`placed_word`), the only word that varies
        between two queries of one node — together with that used-word
        set, the letters those counts yield and the domain itself. Placing
        `placed_word` then removes a letter only where it was that
        letter's last supporter, which the counts answer directly instead
        of rescanning the domain. An entry whose recorded used-word set no
        longer matches is recomputed, never reused. Challenge-word letters
        are added fresh on every query: the active challenge pool can
        shrink within a node.

        A slot in `fresh` (crossing the slot the candidate was just placed
        on) has a domain that changes with the candidate, so its entry is
        keyed by its known-letter signature (`_known_letters_signature`)
        rather than by the slot alone: that signature is exactly what its
        domain depends on, and it only differs between candidates by the
        one crossing letter, so a node computes at most one entry per
        distinct letter there instead of one per candidate."""
        if options_cache is None:
            return self._slot_letter_options(i, used_words, challenge_words)
        key = (i, self._known_letters_signature(i)) if i in fresh else i
        node_used = used_words - {placed_word} if placed_word is not None else set(used_words)
        entry = options_cache.get(key)
        if entry is None or entry[0] != node_used:
            entry = (frozenset(node_used),) + self._slot_letter_counts(i, node_used)
            options_cache[key] = entry
        _, counts, letters, domain = entry
        if (placed_word is not None and placed_word not in node_used
                and placed_word in domain
                and any(counts[pos][ch] == 1 for pos, ch in enumerate(placed_word))):
            letters = _letters_from_counts(counts, [placed_word])
        elif challenge_words:
            letters = [set(s) for s in letters]
        else:
            return letters
        self._add_challenge_letters(i, letters, used_words, challenge_words)
        return letters

    def _known_letters_signature(self, i):
        """The letters `_domain(i, ignore_forced=True)` constrains slot `i`
        with, one entry per cell (`None` for a free one): an assigned
        crossing slot's letter first, else a locked letter — the same
        derivation, in the same order, so two equal signatures always mean
        two equal domains."""
        sig = []
        for cell in self.slots[i]:
            letter = None
            for j, other_pos in self.cell_to_slots[cell]:
                if j != i and self.assignment[j] is not None:
                    letter = self.assignment[j][other_pos]
                    break
            if letter is None:
                letter = self.locked_letters.get(cell)
            sig.append(letter)
        return tuple(sig)

    def _slot_letter_counts(self, i, used_words):
        """`(counts, letters, domain)` for slot `i`: per-position letter
        counts over its dictionary domain minus `used_words`, the letters
        with a non-zero count, and the domain as a set (for membership
        tests). Challenge words are not included — see `_letter_options_
        cached`."""
        cells = self.slots[i]
        domain = self._domain(i, ignore_forced=True)
        idx = self.index.for_cells(cells).get(len(cells))
        if idx is not None and domain is idx["words"]:
            base, members = _blank_letter_counts(idx, len(cells))
            counts = [dict(c) for c in base]
            for u in used_words:
                if len(u) == len(cells) and u in members:
                    for pos, ch in enumerate(u):
                        counts[pos][ch] -= 1
            domain = members
        else:
            counts = [{} for _ in cells]
            for w in domain:
                if w in used_words:
                    continue
                for pos, ch in enumerate(w):
                    c = counts[pos]
                    c[ch] = c.get(ch, 0) + 1
            if not isinstance(domain, (set, frozenset)):
                domain = set(domain)
        letters = [{ch for ch, n in c.items() if n > 0} for c in counts]
        return counts, letters, domain

    def _crossing_deadlock_slots(self, used_words, challenge_words=()):
        """Indices of every still-unassigned slot that crosses another
        still-unassigned slot at a cell where the two slots' own
        remaining candidates agree on no single letter at all — added to
        the "impossible emplacement" definition at the user's explicit
        request: "Ajouter à la définition les emplacements qui possèdent
        au moins une case qui ne peut pas être remplie dans les 2 sens
        (donc avec une lettre commune pour les 2 sens). Dans ce cas, il y
        a donc 2 emplacements impossibles." However healthy each slot's
        own domain (`self._domain(i, ignore_forced=True)`, `used_words`
        excluded, plus any still-fitting `challenge_words` entry — same
        sources `impossible_zone_slots` itself already reads) looks in
        isolation, no combination of the two could ever be validly
        completed together, so both are reported here.

        A slot whose own domain is already empty contributes an empty
        letter set at every one of its positions and is therefore never
        the CAUSE of a deadlock here (an empty set never fails the "both
        sides non-empty" guard below in a way that blames the other
        side) — that case is already covered by `impossible_zone_slots`'s
        own pre-existing empty-domain check, one level up.

        Only ever called with `self.assignment` already pointing at the
        state to diagnose (`self.best_assignment` for every current
        caller — see `impossible_zone_slots`), exactly like `_domain`
        itself expects.

        Returns `(deadlocked, deadlock_cells)`: `deadlocked` is the set of
        slot indices described above; `deadlock_cells` is the (usually
        much smaller) set of actual CROSSING cells where a conflict was
        found — the specific root-cause cell(s), as opposed to every
        other cell of the same two slots, which is only impossible by
        association. Lets a caller (`_interactive_fill_diagnostics`)
        highlight that exact cell more prominently than the rest of the
        slot, at the user's explicit request: "la case ... devrait être
        en rouge vif (plus vif que les mots impossibles qui passent par
        cette case)"."""
        open_slots = [i for i, w in enumerate(self.assignment) if w is None]
        letters_by_slot = {
            i: self._slot_letter_options(i, used_words, challenge_words)
            for i in open_slots
        }
        deadlocked = set()
        deadlock_cells = set()
        for cell, entries in self.cell_to_slots.items():
            open_here = [(i, pos) for i, pos in entries if i in letters_by_slot]
            for a in range(len(open_here)):
                i, pi = open_here[a]
                li = letters_by_slot[i][pi]
                if not li:
                    continue
                for b in range(a + 1, len(open_here)):
                    j, pj = open_here[b]
                    lj = letters_by_slot[j][pj]
                    if lj and not (li & lj):
                        deadlocked.add(i)
                        deadlocked.add(j)
                        deadlock_cells.add(cell)
        return deadlocked, deadlock_cells

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
        diagnostic, wrongly preventing `generate_grid`'s "still_has_hope"
        from ever treating it as blocked (see _backtrack
        below for the same fix on the search side). `used_at_best` is
        recomputed directly from `best_assignment` rather than reading
        `self.used_words` — the latter reflects `self.assignment`'s
        *current* state (which may have fully backtracked to its starting
        state once the search has ended), not necessarily that of the most
        advanced point (`best_assignment`) this diagnostic examines.

        A slot also counts as impossible when it crosses another
        still-open slot at a cell where the two agree on no letter at all
        — see `_crossing_deadlock_slots` for the full reasoning. Unlike
        the empty-domain case above, this one can flag a slot whose own
        domain is perfectly non-empty in isolation; both slots of such a
        pair are reported.

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
        # Second, additional criterion — see _crossing_deadlock_slots'
        # own docstring, at the user's explicit request: two still-open
        # slots crossing at a cell where their own remaining candidates
        # share no letter at all are both impossible too, even though
        # each one's domain, in isolation, is perfectly non-empty.
        deadlocked, _deadlock_cells = self._crossing_deadlock_slots(used_at_best, active_challenge_words)
        result = [
            i for i, word in enumerate(self.best_assignment)
            if word is None
            and (
                i in deadlocked
                or (
                    all(w in used_at_best for w in self._domain(i, ignore_forced=True))
                    and not any(
                        cw not in used_at_best and self._challenge_word_fits(i, cw)
                        for cw in active_challenge_words
                    )
                )
            )
        ]
        self.assignment = saved
        return result

    def empty_domain_zone_cells(self, assignment):
        """Cells of every still-unassigned slot whose domain is empty in
        `assignment` — the plain empty-domain half of `impossible_zone_
        cells` (same red overlay), deliberately WITHOUT the crossing-
        deadlock scan.

        Exists for `try_fill`'s live heartbeat, which publishes far too
        often to afford `impossible_zone_cells()`: that method's cost is
        almost entirely `_crossing_deadlock_slots`, which builds a letter
        set out of every open slot's full domain. What is left here is one
        `_domain` call per unassigned slot — the very same work
        `_backtrack` already does on every single node, so paying it again
        once per `LIVE_STATE_HEARTBEAT_INTERVAL` checks is negligible.

        Without this, a heartbeat tile could never show red at all, so a
        slot whose crossing letters had just spelled something impossible
        showed only the weaker yellow "écarté" background — understating a
        genuine dead end for as long as that attempt kept heartbeating."""
        saved = self.assignment
        self.assignment = assignment
        used = {w for w in assignment if w is not None}
        active_challenge_words = self._active_challenge_words()
        cells = set()
        for i, word in enumerate(assignment):
            if word is not None or i in self.excluded_slots:
                continue
            if all(w in used for w in self._domain(i, ignore_forced=True)) and not any(
                cw not in used and self._challenge_word_fits(i, cw)
                for cw in active_challenge_words
            ):
                cells.update(self.slots[i])
        self.assignment = saved
        return sorted(cells)

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
        was still non-empty, just not yet resolved in time.

        Deliberately does NOT also include every cell of an "emplacement
        écarté" (`_impossible_this_attempt`) on that basis alone: that set
        only records that a slot went dry at least once during this
        attempt, a scheduling signal — it says nothing about whether the
        slot's domain is still empty right now, and backtracking routinely
        makes such a slot viable again. Painting it red regardless would
        conflate "deprioritized" with "impossible," breaking the very
        invariant this method exists to uphold (a plain white cell always
        means the attempt would still complete it, given enough budget) in
        the other direction: a RED cell would no longer reliably mean
        "unfixable" either. An écarté slot gets its own separate, weaker
        signal instead — see `excluded_zone_cells` (yellow in the web UI,
        distinct from this method's own red); see also
        `Filler.on_checks_progress`/
        `on_live_state` for the mechanism that proves, live, that a
        stalled-looking attempt is still genuinely working rather than
        stuck."""
        cells = set()
        for i in self.impossible_zone_slots():
            cells.update(self.slots[i])
        return sorted(cells)

    def deadlock_zone_cells(self):
        """Like `impossible_zone_cells` above, but only the exact crossing
        cell(s) actually responsible for a crossing-letter deadlock (see
        `_crossing_deadlock_slots`) — always a subset of `impossible_zone_
        cells`'s own result, at the user's explicit request: "les
        prévisualisations [doivent montrer] les cases impossibles en rouge
        vif... comme sur le mode Interactif," which already highlights
        this narrower set more prominently than the rest of the same
        impossible slot(s) (`_interactive_fill_diagnostics`'s own third
        return value)."""
        used_at_best = {w for w in self.best_assignment if w is not None}
        active_challenge_words = self._active_challenge_words()
        saved = self.assignment
        self.assignment = self.best_assignment
        _deadlocked, cells = self._crossing_deadlock_slots(used_at_best, active_challenge_words)
        self.assignment = saved
        return sorted(cells)

    def excluded_zone_cells(self, assignment=None, include_deadlock=False):
        """Cells of every "emplacement écarté" of THIS attempt (see
        `DOC_ALGO/FR/Lexicon.md`) — a slot found blocked at least once,
        set aside so it is only retried once no other slot can take a
        word, but never walled off. Highlighted with a distinct, weaker
        signal than `impossible_zone_cells`/`deadlock_zone_cells` (yellow
        rather than red in the web UI): "impossible détecté, mais non
        définitif".

        This is exactly `_impossible_this_attempt` — the one set
        `_backtrack`'s own slot selection deprioritizes — so the overlay
        and the search behavior can never disagree. Two things feed that
        set (see its own docstring in `__init__`), plus one computed
        here:

        - `_backtrack`'s own per-node domain check, the moment a slot's
          domain first goes dry, and `mark_immediately_impossible_slots`'s
          pre-search pass for a slot already dry under the definitive
          constraints alone — the common case. A slot stays flagged for
          the rest of THIS attempt even once backtracking later makes it
          viable again, which is exactly the "temporary, not definitive"
          signal this overlay exists to show. Filtered to still-unassigned
          slots in `assignment` (`self.assignment` if not given) — a slot
          that went dry once but was later successfully filled via a
          different path must never show this overlay on top of its own
          real letters.
        - A crossing-letter deadlock (`_crossing_deadlock_slots`)
          currently found in `assignment`, ONLY when `include_deadlock` is
          true — at the user's explicit follow-up request: the plain
          empty-domain case above already gets this "temporarily set
          aside" treatment live, the instant `_backtrack` notices it
          (`_impossible_this_attempt`); a deadlock, without this third
          source, would show red (`impossible_zone_cells`/`deadlock_zone_
          cells`, both of which already fold it in) but never also
          yellow, unlike its plain-empty-domain counterpart — an
          inconsistency between two cases that both mean the same thing
          here ("impossible right now, not yet definitive"). `include_
          deadlock` defaults to `False`, matching every pre-existing
          caller's own expectation that this method "never iterates a
          domain... cheap enough for every cadence" (see `_publish_live_
          state`'s own comment) — `_crossing_deadlock_slots` is exactly
          the domain-iterating cost that comment refers to, the same one
          already responsible for a real, separately-fixed slowdown when
          run too often (see `impossible_zone_cells`/`deadlock_zone_
          cells`/`impossible_zone_slots` already being skipped entirely at
          `_publish_live_state`'s own tight heartbeat cadence). Pass `True`
          only where the caller already pays for that same cost anyway on
          the very same call (`_publish_new_best`, the final diagnostics
          snapshot) — one more `_crossing_deadlock_slots` call there is a
          bounded, proportionate addition; at the heartbeat's own much
          tighter cadence it would reintroduce the exact slowdown these
          three were deliberately skipped to avoid. Computed fresh on
          every call (temporarily swapping `self.assignment` to whichever
          snapshot this call examines, exactly like `deadlock_zone_cells`
          itself does) — never cached, since the two callers that opt in
          already recompute `deadlock_zone_cells()`'s own equivalent call
          on every invocation too. `_crossing_deadlock_slots` only ever
          reports still-*unassigned* slots (`open_slots`, computed
          internally from `self.assignment`), so no extra filter is
          needed here either. A slot found this way is reported for THIS
          snapshot only and is NEVER merged into `_impossible_this_
          attempt` (`DOC_ALGO/FR/Lexicon.md`: nothing on a display path
          may write to that set). A crossing deadlock is a property of
          the assignment being examined, not a lasting fact about the
          slot — the next placement can dissolve it — so memorising it
          would let a display call permanently rewrite the search's own
          scheduling set; see the inline comment at the merge point below
          for what that measured.

        Every source here is a scheduling/deprioritization decision, not
        a confirmed dead end — see `impossible_zone_cells`'s own docstring
        for why this attempt never paints any of them red on that basis
        alone, even though a slot's domain may well still be genuinely
        empty right now. This can therefore legitimately overlap with a
        fresh `impossible_zone_cells()`/`deadlock_zone_cells()` result —
        the web UI lets the stronger red win via CSS cascade order rather
        than this method subtracting one set from the other, so neither
        list here needs the other's result to stay correct on its own."""
        if assignment is None:
            assignment = self.assignment
        cells = set()
        for i in self._impossible_this_attempt:
            if assignment[i] is None:
                cells.update(self.slots[i])
        if include_deadlock:
            used_words = {w for w in assignment if w is not None}
            active_challenge_words = self._active_challenge_words()
            saved_assignment = self.assignment
            self.assignment = assignment
            deadlocked, _deadlock_cells = self._crossing_deadlock_slots(used_words, active_challenge_words)
            self.assignment = saved_assignment
            # Reported for THIS snapshot only, never merged into
            # `_impossible_this_attempt`. A crossing deadlock is a
            # property of the assignment being examined, not a lasting
            # fact about the slot: the very next placement can dissolve
            # it. Memorising it made a display call permanently rewrite
            # the search's own scheduling set — measured on a replayed
            # STOP_DUMP, wiring the preview queue alone took a 100k-check
            # attempt from 0 slots set aside to 44, until essentially
            # every slot was écarté, the deprioritization was vacuous
            # (7991 of 8967 selections had nothing but écarté slots to
            # choose from, against 0 without the queue) and the grid
            # showed as a frozen block of yellow.
            for i in deadlocked:
                cells.update(self.slots[i])
        return sorted(cells)

    def _select_target_slot(self, unassigned, domains):
        """Chooses which slot to fill next among `unassigned` (already
        guaranteed non-empty, each with at least one genuinely available
        candidate — see the domain check right before this call, in
        `_backtrack`), via the 9-level cascade documented below.

        Factored out of `_backtrack` to be reused as-is by `interactive_
        place_word` (the web UI's "Interactif" mode), at the user's
        explicit request — a hand-written duplicate of this logic used to
        live there (a plain MRV: the smallest domain, then an already
        partially-known slot, then random), with neither level 3's length
        threshold (which excludes 2-3-letter slots) nor level 6's
        geometric score (which favors the grid's own center) — which made
        interactive fill start with 2-letter slots scattered across the
        grid instead of following the same rules as automatic generation.
        Takes `unassigned`/`domains` as parameters (rather than
        recomputing them) since `interactive_place_word` has already built
        them in a slightly different shape (`viable`, filtered by `used_
        words`) for its own use.

        `_backtrack` deprioritizes a slot flagged in `self._impossible_
        this_attempt` (see its own docstring in __init__) BEFORE calling
        this method, by excluding it from its own `unassigned` argument
        whenever at least one other slot remains selectable — this method
        itself has no notion of that set and simply runs the 9-level
        cascade below over whatever list it's handed. `interactive_place_
        word`'s own call site builds its own `viable` list directly and
        never applies this deprioritization at all — there's no `_backtrack`
        recursion there to observe a slot going dry over the course of a
        search in the first place (see that function's own docstring)."""
        # 9-level selection rule, at the user's explicit request (MRV was
        # removed — see the comment further up, before the Filler class,
        # for why):
        # 1. **Optional, currently disabled** (`ALTERNATE_DIRECTION_
        #    ENABLED`, see its own docstring): first alternate across/
        #    down: draw the category (across or down) at random, with a
        #    probability proportional to the number of still-open slots
        #    in each of the 2 categories (self.directions, precomputed in
        #    __init__) — a category that still has many unfilled slots
        #    has a better chance of being chosen than the other, which
        #    naturally tends to alternate/balance the two as the fill
        #    progresses without fixing a strict order. While disabled,
        #    this level is a no-op: `direction_pool` starts as the whole
        #    `unassigned` group, both directions together, and level 2
        #    below applies to it directly;
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
        #    purely **geometric** score is computed for each: the squared
        #    distance between the slot's own CLOSEST cell to the grid's own
        #    center (every cell of `self.slots[i]` is considered
        #    individually, the smallest of their own squared distances
        #    being the slot's score — NOT the midpoint of its own span, so
        #    a long slot only needs to REACH toward the center to score
        #    well) and that center itself (the same `(row, col)` origin as
        #    everywhere else in this file) — see the computation itself
        #    further below for the detail. This score
        #    doesn't depend at all on the slot's own fill state (neither
        #    its known letters nor its domain) — only on its fixed position
        #    in the grid — which tends to make the fill progress along a
        #    geometric front rather than by each slot's own difficulty.
        #    Only **the `SLOT_SELECTION_WINDOW_SIZE` (10) slots with the
        #    smallest score** (the closest to the grid's own center) are
        #    kept — a fixed window size, not a
        #    proportion of the group (see its own docstring). The slots are
        #    shuffled (with this attempt's own
        #    seeded RNG, hence reproducible) before being sorted by score:
        #    without this prior shuffle, the sort order (`sorted` is
        #    stable) would decide which tied slots pass the window's own
        #    cutoff, reintroducing the same positional bias already
        #    encountered elsewhere in this file (see further up, pre-
        #    fill's own "black column"/"triangle" bugs) — all the more
        #    relevant here since the score is geometric, so many slots can
        #    share exactly the same score (the whole ring at a given
        #    Euclidean distance from the center). This geometric window
        #    (`window`) is then narrowed three more times before the final
        #    choice is made:
        # 7. **Most-constrained cell**: among the slots of the geometric
        #    window obtained at the previous level, the smallest number of
        #    letters still possible on any one STILL-FREE cell is found
        #    (`_slot_min_letter_options`, reading the same `letter_scores`
        #    tally Interactive mode's own "Stats" button displays — see
        #    `_interactive_letter_stats`), and the choice is restricted to
        #    the slots that actually own a cell with that count. The
        #    tightest cell in the grid is where the fill can go wrong
        #    soonest, so it gets resolved while the search still has room
        #    to manoeuvre, instead of being left for some crossing word to
        #    settle by accident. Only slots of at least
        #    `MOST_CONSTRAINED_START_LENGTH` (7) letters are measured
        #    first; when none has a measurable free cell, the length
        #    threshold drops by one, down to `MOST_CONSTRAINED_MIN_LENGTH`
        #    (2). If no slot of the window has a measurable free cell even
        #    then, this level changes nothing: the next one then applies
        #    to the whole window;
        # 8. by the number of letters already placed in each slot
        #    (`_placed_letter_count`, the most letters first), reduced to
        #    its own first `SLOT_SELECTION_REFINE_FRACTION` slots (see this
        #    constant's own docstring);
        # 9. by `_slot_letter_frequency_score` (see its own docstring), the
        #    highest score first — the slot whose own zone statistically
        #    offers the most fill options — whose very first entry directly
        #    becomes the chosen slot. Each of these two reductions
        #    re-shuffles its own input window beforehand (same reason as
        #    level 6's own shuffle: since `sorted` is stable, this shuffle
        #    is what breaks ties between slots with equal scores, not the
        #    order inherited from the previous sort).
        if ALTERNATE_DIRECTION_ENABLED:
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
        else:
            direction_pool = list(unassigned)
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
        # Geometric score, at the user's explicit request: squared distance
        # between the slot's own CLOSEST cell to the grid's own center, and
        # that center itself — not the slot's own midpoint. Every cell of
        # the slot (`self.slots[i]`, a straight run of cells along one axis
        # — see `extract_slots`) is considered individually, and the
        # smallest squared distance among them is the slot's own score, so
        # a long slot only needs to REACH toward the grid's own center to
        # score well, even when most of its span sits far from it. The
        # grid's own center is `((self.rows - 1) / 2, (self.cols - 1) / 2)`
        # (the same origin as `(row, col)` everywhere else in this file). A
        # slot with at least one cell exactly on the grid's own center gets
        # the lowest possible score (0); the score grows as its closest
        # cell sits further away, in any direction. Squaring each
        # coordinate's own distance before summing them (a squared
        # Euclidean distance, not a Manhattan one) penalizes a slot whose
        # closest cell is markedly off-center on a single axis more
        # heavily than one at an equal Manhattan distance but spread
        # across both axes — a fill front held more tightly around the
        # grid's own center, rather than a flat diamond. Replaces an
        # earlier version of this same score, measured from the slot's own
        # CENTRAL position (the midpoint of its span) instead of its
        # closest cell — at the user's explicit request.
        center_row = (self.rows - 1) / 2
        center_col = (self.cols - 1) / 2
        scores = {
            i: min(
                (col - center_col) ** 2 + (row - center_row) ** 2
                for row, col in self.slots[i]
            )
            for i in selection_pool
        }
        shuffled_pool = list(selection_pool)
        self.rng.shuffle(shuffled_pool)
        window = sorted(shuffled_pool, key=lambda i: scores[i])[:SLOT_SELECTION_WINDOW_SIZE]
        # Most-constrained-cell level: among the geometric window obtained
        # at the previous level, find the smallest number of letters still
        # possible on any one still-free cell (`_slot_min_letter_options`,
        # reading the same `letter_scores` tally Interactive mode's
        # "Stats" button displays), and restrict the choice to the slots
        # that actually own a cell with that count. The tightest cell in
        # the grid is where the fill can go wrong soonest, so it is
        # resolved while the search still has room to manoeuvre rather
        # than left to a crossing word to settle by accident. Skipped when
        # no slot of the window has a measurable free cell (nothing to
        # restrict), and it can never empty the pool: whichever slot owns
        # the minimum is in the result by construction. The measure is
        # scoped by a decreasing slot-length threshold: only slots of at
        # least `MOST_CONSTRAINED_START_LENGTH` (7) letters are measured
        # first; when none of the window has a measurable free cell at that
        # threshold, it drops by one (6, 5, ...) down to `MOST_CONSTRAINED_
        # MIN_LENGTH` (2), and the first threshold yielding a measurable
        # slot is the one applied. Long slots are thus resolved on their
        # tightest cell before short ones, whose naturally narrow
        # vocabulary makes a tight cell a weaker signal.
        all_counts = {}
        for i in window:
            if len(self.slots[i]) < MOST_CONSTRAINED_MIN_LENGTH:
                continue
            count = self._slot_min_letter_options(i)
            if count is not None:
                all_counts[i] = count
        for threshold in range(MOST_CONSTRAINED_START_LENGTH, MOST_CONSTRAINED_MIN_LENGTH - 1, -1):
            option_counts = {
                i: count for i, count in all_counts.items()
                if len(self.slots[i]) >= threshold
            }
            if option_counts:
                fewest = min(option_counts.values())
                window = [i for i in window if option_counts.get(i) == fewest]
                break
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

    def _siblings_still_racing(self, deadline_checks):
        """True iff some OTHER slot of `_sibling_attempt_active` is both
        active (a real attempt currently running there, not merely a slot
        never dispatched or already freed) and still below its own
        `deadline_checks` — see `_backtrack`'s own deadline-check comment
        for why this governs whether this attempt may keep searching past
        its nominal budget. `_sibling_checks_progress` alone can't answer
        this: a slot whose own attempt already stopped early (`search_
        exhausted`, cancelled, abandoned) keeps its last-reported, possibly
        low `checks` value forever, which would otherwise look exactly
        like "still racing" — `_sibling_attempt_active` is what tells the
        two apart."""
        active = self._sibling_attempt_active
        progress = self._sibling_checks_progress
        for i in range(len(active)):
            if i == self._checks_slot:
                continue
            if active[i] and progress[i] < deadline_checks:
                return True
        return False

    def _deadline_reached_without_extension(self, deadline_checks):
        """True once `self.checks` has exceeded `deadline_checks` AND no
        extension applies — the single source of truth both deadline
        checkpoints in `_backtrack` below consult (the cheap one at the
        top of the function, evaluated once per recursive call, and the
        per-candidate one inside the `for w in cands:` loop, evaluated far
        more often — a search that mostly rejects candidates without ever
        recursing further would otherwise never re-consult the top-level
        check at all, see that loop's own comment). Returns `False`
        immediately if `self.checks` hasn't reached `deadline_checks` yet
        — nothing to decide.

        For a caller with no visibility into its siblings (`_checks_slot`/
        `_sibling_checks_progress`/`_sibling_attempt_active` all `None` —
        interactive mode, `minimize_black_squares`, a solitary CLI run),
        this is the plain, original, unconditional "budget's up" rule.

        For a palier's own parallel attempts, at the user's explicit
        request ("Autoriser les grilles à dépasser leur budget, tant
        qu'il reste des grilles qui ne l'ont pas atteint"), the budget is
        elastic: this attempt may keep going past its own `deadline_
        checks` as long as some sibling attempt of the same palier is
        still genuinely racing towards its own deadline — stopping this
        one right now would just leave its CPU core idle until that
        slower sibling finishes anyway, since the palier can't conclude
        before it does. The "stop" verdict, once reached, is STICKY for
        the rest of this attempt (`self._deadline_extension_denied`) —
        essential, not a mere optimization: ordinary backtracking only
        ever reacts to a `False` return by trying the NEXT candidate at
        that same slot, so a single, transient "stop" would just reject
        one candidate and let the loop move on to place a different one
        right after, on a check count that no longer happens to fall on a
        checkpoint — never actually unwinding the search the way the
        plain, irreversible `self.checks > deadline_checks` condition
        this replaces always did. Re-evaluated (while not yet denied)
        immediately the first time the deadline is crossed (`self.checks
        += 1` in the per-candidate loop makes this exact-match reliable
        there — a genuinely solo attempt still stops right away, exactly
        like before this feature), then only every `CHECKS_PROGRESS_
        REPORT_INTERVAL` checks past that point (the same cadence every
        sibling's own `checks_progress` cell is itself refreshed at, so
        checking more often couldn't see fresher data regardless) — not
        on every check past the deadline, to keep this extension's own
        cost negligible."""
        if self._deadline_extension_denied:
            return True
        if self.checks <= deadline_checks:
            return False
        if (
            self._checks_slot is None
            or self._sibling_checks_progress is None
            or self._sibling_attempt_active is None
        ):
            self._deadline_extension_denied = True
            return True
        checks_past_deadline = self.checks - deadline_checks
        if (
            checks_past_deadline == 1
            or checks_past_deadline % CHECKS_PROGRESS_REPORT_INTERVAL == 0
        ) and not self._siblings_still_racing(deadline_checks):
            self._deadline_extension_denied = True
            return True
        return False

    def _backtrack(self, deadline_checks, released=False):
        # `self.checks` is no longer incremented here (once per call/node)
        # but once per candidate word genuinely attempted, in the `for w in
        # cands:` loop further below — see its own comment for the reason
        # (at the user's explicit request, "to avoid spending a long time
        # iterating over hopeless cases"). This first call (from `Filler.
        # solve()`) therefore starts with `self.checks` still at its entry
        # value (0 for a fresh search); the checks below remain correct
        # with this value as-is.
        # Every early exit below reports no conflict (see `_fail`).
        self._last_conflict = None
        if self.abandoned:
            return False
        # See `_deadline_reached_without_extension`'s own docstring — the
        # per-candidate check further below (`for w in cands:`) is the one
        # that actually bounds a slot whose candidates mostly get rejected
        # without ever recursing back here; this one covers every other
        # path back into `_backtrack`.
        if self._deadline_reached_without_extension(deadline_checks):
            self._budget_exhausted = True
            return False
        if self._periodic_checkpoints():
            return False
        unassigned = [
            i for i in range(len(self.slots))
            if self.assignment[i] is None
            and i not in self.excluded_slots
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
            self.best_stat_letters = self.stat_letters(self.assignment)
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
                # absent from the lexicon) must not be treated as dry just
                # because _domain(i) itself came back dry.
                if not (active_challenge_words and any(
                    w not in self.used_words and self._challenge_word_fits(i, w)
                    for w in active_challenge_words
                )):
                    # Flags `i` "écarté" (see `_impossible_this_attempt`'s
                    # own docstring in __init__). A slot that cannot be
                    # filled in the current state was dried by a placement
                    # of this search, so the node backtracks at once:
                    # continuing below it could only build on a grid that
                    # already has a hole. The exceptions are the slots in
                    # `_tolerated_dry` — dry before the search started, or
                    # deliberately dried by a last-resort word — which no
                    # backtracking can revive: those are simply left out of
                    # `domains` and the node carries on. The flag itself
                    # stays: once the slot is viable again, it is still
                    # only picked up in the node's released stage.
                    self._impossible_this_attempt.add(i)
                    if i not in self._tolerated_dry:
                        return self._fail(self._dry_slot_conflict(i))
                    continue
            domains[i] = domain
        if not domains:
            # Every remaining unassigned slot is dry at once, and all of
            # them are tolerated (dry before the search started): no
            # placement of this search is to blame.
            return self._fail(set())
        # Memoises `_slot_letter_options` for this node across every
        # candidate tried here (see `slot_is_blocked`): a slot's own
        # options only change when a word lands on a slot CROSSING it, so
        # everything outside the chosen slot's own crossings is stable for
        # the whole node and computed at most once.
        options_cache = {}

        # Crossing a slot that is dry right now is NOT, in itself, a
        # reason to refuse a placement: what a candidate is judged on is
        # the STATE IT LEAVES BEHIND, never which of its neighbours
        # already happened to be blocked. The two per-candidate checks
        # below are the whole rule, and both judge the state the candidate
        # LEAVES: it is rejected when it newly blocks a crossing slot that
        # was healthy right before it (`crossing_broken`, relaxed only by
        # the last-resort pass below), and when a crossing slot that was
        # blocked already is still blocked after it (`crossing_still_
        # impossible`, relaxed nowhere, so a word never crosses a slot
        # that is blocked already). Sealing a still-healthy crossing slot
        # into a fully-lettered run spelling nothing real is `crossing_
        # broken` like any other way of blocking it — the strict rule
        # refuses it, and the last-resort pass below accepts it along
        # with the rest, so a failed attempt's own grid can carry a few
        # such runs. The cleanup between paliers is what removes them:
        # `_invalid_fully_known_indices` flags every one of them
        # impossible before the next palier ever sees the grid. A
        # placement valid on both counts is always allowed, even right
        # next to a blocked slot, since such a slot comes back into play
        # the same way either way — the moment backtracking frees one of
        # its crossing letters.
        #
        # An "emplacement écarté" is an ordinary slot in every respect —
        # its domain is recomputed from scratch on every node, it is
        # protected by the `crossing_broken` check below exactly like any
        # other open slot, and once a word goes on it, it is filled like
        # any other. Its one and only difference is that it is not
        # considered for a new placement while a word can still be placed
        # elsewhere; the staged loop right below is what implements that,
        # and what guarantees it is never walled off.
        # The écarté release (see `DOC_ALGO/FR/Lexicon.md`) followed by
        # the last-resort relaxation. Four stages, in order:
        #   1. place a word on a NON-écarté slot — écarté slots are
        #      temporarily out of the running, the one and only way a
        #      blocked slot ever restricts anything;
        #   2. nothing placeable that way: release the écarté slots and
        #      carry on, still without backtracking;
        #   3. still nothing, and `self.breaking_permitted` is on — i.e.
        #      `solve()`'s second pass, reached only once the whole strict
        #      search had been exhausted from the root: accept a word that
        #      creates an impossible slot rather than failing the grid and
        #      leaving it sparse. This is the ONLY case where the "never
        #      create an impossible slot" rule yields, and it yields only
        #      once backtracking has run out of alternatives across the
        #      whole grid, not merely below this node;
        #   4. only if even that places nothing does the node fail.
        # All four stages share one cap, MAX_DESCENTS_PER_NODE: once the
        # node has recursed that many times without success it fails right
        # away, whatever stage it has reached.
        # `released` is a plain recursion parameter, so it is inherited by
        # everything placed below a release and restores itself as the
        # backtrack unwinds back above the node that released — precisely
        # "réactivés à l'étape où ils avaient été libérés".
        # `allow_breaking` is deliberately NOT inherited: every node makes
        # its own decision, and only after exhausting its own strict
        # options, so relaxing here never licenses a child to relax before
        # it has itself explored everything.
        selectable = list(domains)
        set_aside = [i for i in selectable if i in self._impossible_this_attempt]
        primary = selectable if released else [i for i in selectable if i not in set_aside]
        if not primary:
            # Only écarté slots left: release them at once rather than
            # leaving this node with nothing to try.
            primary, released = selectable, True
        allow_breaking = False
        tried_slots = set()
        # Recursive descents made by this node so far, and this node's own
        # cap (see MAX_DESCENTS_PER_NODE/EARLY_DESCENTS_WORD_COUNT).
        descents = 0
        max_descents = MAX_DESCENTS_PER_NODE
        if 0 < max_descents and (
            assigned_count - self._initial_assigned_count < EARLY_DESCENTS_WORD_COUNT
        ):
            max_descents = max(max_descents, EARLY_MAX_DESCENTS_PER_NODE)
        # Union of the conflict sets of every alternative this node has
        # tried and seen fail (see BACKJUMPING_ENABLED); `conflict_unknown`
        # once one of them reported none.
        node_conflict = set()
        conflict_unknown = False
        while True:
            avail = [i for i in primary if i not in tried_slots]
            if not avail:
                if not released:
                    # Stage 2: release the écarté slots for the rest of
                    # this descent and carry on without backtracking.
                    primary, released = selectable, True
                    continue
                if not allow_breaking and self.breaking_permitted:
                    # Stage 3, only in `solve()`'s second pass: the strict
                    # search has already been exhausted from the root, so
                    # every backtracking possibility that creates no
                    # impossible slot — the words placed first included —
                    # has been tried, which is exactly the one condition
                    # under which creating one is allowed: re-run
                    # every slot with `crossing_broken` relaxed. Candidates
                    # keep their normal priority order, so a crossing-safe
                    # one is always preferred and only gets overtaken once
                    # its own subtree has failed too. `crossing_still_
                    # impossible` is NOT relaxed here (nor anywhere): a
                    # word never crosses a slot that stays blocked.
                    allow_breaking = True
                    primary = list(domains)
                    tried_slots = set()
                    continue
                # Stage 4: nothing at all, even allowing damage.
                return self._fail(None if conflict_unknown else node_conflict)
            best_i = self._select_target_slot(avail, domains)
            tried_slots.add(best_i)
            # `ordered_candidates` is the one candidate-ordering rule of
            # this engine (shuffle, statistical sort, sliding-window random
            # draw), shared verbatim with Interactive mode's own "Suivant".
            cands = self.ordered_candidates(
                best_i, [w for w in domains[best_i] if w not in self.used_words],
            )
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
            # Set to True by the first candidate this slot actually
            # accepts (see the "emplacement écarté" flagging right after
            # this loop ends).
            placed_any = False
            blameable_rejection = False
            # Words the rejections of this slot's candidates depend on.
            slot_conflict = set()
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
                if self.abandoned:
                    return self._fail(None)
                if self._deadline_reached_without_extension(deadline_checks):
                    self._budget_exhausted = True
                    return self._fail(None)
                if self._periodic_checkpoints():
                    return self._fail(None)
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
                # further. A slot structurally outside this search's scope
                # (`excluded_slots`) is never affected by this — nothing is
                # ever required to fill it in the first place.
                #
                # `j in domains` is the baseline this check is measured
                # against, and it is free: `domains` was built at the top of
                # this node, BEFORE any candidate was placed, and a slot found
                # dry there was deliberately left out of it (and flagged
                # "écarté"). So `j in domains` means exactly "j still had a
                # real candidate before `w` was placed" — a slot that goes dry
                # only now is `w`'s own doing and rejects it, while one that
                # was already dry is never blamed on whichever candidate
                # happens to be tried next. Without this baseline, a single
                # already-dry neighbour made EVERY candidate at `best_i` look
                # unsafe, which is what forced the budget-based escape hatch
                # that used to let a genuinely destructive word through.
                # An "écarté" slot that is still healthy right now (flagged
                # earlier in this attempt, non-dry again since) IS in
                # `domains`, and so IS protected: the search may legitimately
                # come back to it, so emptying it is a real break.
                # `crossing_broken`: this candidate has NEWLY made a
                # crossing slot impossible (it was fine right before it).
                crossing_broken = False
                # `crossing_still_impossible`: after this candidate, a
                # crossing slot that was ALREADY impossible still is — i.e.
                # `w` crosses an "emplacement bloqué" (red) and leaves it
                # blocked. Rejected at every stage, with no exception: the
                # last-resort relaxation below covers only CREATING a new
                # impossible slot, never writing into one that is blocked
                # already. A slot that this candidate's own letter puts
                # back in play (a statistical seed it supersedes, say) is
                # not blocked afterwards, so it never lands here — what
                # both flags judge is the state the candidate LEAVES.
                crossing_still_impossible = False
                # Crossing slots this candidate newly blocks; only kept
                # beyond the rejection when `allow_breaking` accepts it.
                broken_slots = []
                # Crossing slots found NOT blocked once this candidate is in
                # place (see the écarté re-evaluation below).
                unblocked_crossers = []
                for j in self._crossing_slots[best_i]:
                    if self.assignment[j] is not None or j in self.excluded_slots:
                        continue
                    # ONE definition of "emplacement bloqué" for the whole
                    # engine (`Filler.slot_is_blocked`, shared verbatim
                    # with Interactive mode's own "Suivant"): no candidate
                    # left at all — the "Mots Défi" exemption included, a
                    # crossing slot handed to the challenge mechanism is
                    # not closed off — OR a crossing cell where this slot
                    # and the still-open slot crossing it there agree on no
                    # letter. The second case is what a plain empty-domain
                    # test could never see, letting a word be placed across
                    # a slot the interface was already painting red.
                    if self.slot_is_blocked(
                        j, self.used_words, active_challenge_words,
                        options_cache, self._crossing_slots[best_i], w,
                    ):
                        if j in domains:
                            # `j` was fine before `w` was placed (see the
                            # baseline reasoning below), so blocking it is
                            # this candidate's own doing.
                            crossing_broken = True
                            broken_slots.append(j)
                            if not allow_breaking:
                                break
                        else:
                            crossing_still_impossible = True
                            break
                    else:
                        unblocked_crossers.append(j)
                # "Backtrack immédiat": a candidate that NEWLY makes a
                # crossing slot unfillable is reverted on the spot, whichever
                # of the three tiers it comes from, and the loop moves on to
                # the next entry of `cands` — "try another candidate of the
                # same tier, or fall through to the next one" for this slot.
                # This rule is absolute and has no escape hatch: a word is
                # never placed where it would create an impossible slot, so a
                # grid can never end up holding a run of letters that spells
                # nothing real. If every candidate at `best_i` breaks
                # something, the staged loop above moves on to the next
                # slot, then to the released écarté ones, and only then —
                # every strict subtree of this node having been explored
                # and failed — comes back round with `allow_breaking`, at
                # which point a breaking word IS accepted rather than
                # leaving the grid sparse.
                # What each tier tracks on top of that is only a per-word
                # give-up budget: a challenge or theme word that has spent its
                # own share of the attempt's `deadline_checks` on nothing but
                # broken crossings is abandoned for the rest of the attempt,
                # so it stops being offered anywhere else in the grid
                # (`_register_challenge_word_break`/`_register_theme_word_
                # break`). Only counted when the break actually causes a
                # rejection — a word accepted under `allow_breaking` was
                # not given up on, so it must not count against its own
                # budget. The general dictionary has no word identity worth
                # tracking that way, and needs none: it simply keeps trying
                # its remaining candidates.
                # Crossing a slot that stays blocked rejects the candidate
                # on its own, whatever stage this is (see `crossing_still_
                # impossible` above); newly blocking a slot that was fine
                # only rejects it while the strict rule still applies.
                rejected = crossing_still_impossible or (crossing_broken and not allow_breaking)
                if rejected and crossing_broken:
                    # Rejected for blocking a slot that was still healthy:
                    # a failure the state above this node is responsible
                    # for, as opposed to crossing a tolerated dead slot.
                    # It depends on the words fixing this slot's letters
                    # and those fixing the letters of the slot it blocks.
                    blameable_rejection = True
                    slot_conflict |= self._assigned_crossers(best_i) - {best_i}
                    for j in broken_slots:
                        slot_conflict |= self._assigned_crossers(j) - {best_i}
                if rejected:
                    if w in challenged_set:
                        self._register_challenge_word_break(w)
                    elif w in pri_set:
                        self._register_theme_word_break(w)
                if not rejected:
                    placed_any = True
                    # Re-evaluate the écarté slots this word crosses: the
                    # check above has just established, for every open one,
                    # whether it is still blocked with this word in place.
                    # One that no longer is leaves the list for good (it
                    # is flagged again if it goes dry later on).
                    for j in unblocked_crossers:
                        self._impossible_this_attempt.discard(j)
                    # The letters this word just wrote constrain every slot
                    # it crosses: re-tally those, so the levels reading
                    # `letter_scores` (`_slot_min_letter_options`, `_slot_
                    # letter_frequency_score`, `_candidate_score`) see the
                    # grid as it is now rather than as it was before the
                    # search started. Restored as the placement is reverted,
                    # so a tally never outlives its own assignment.
                    saved_scores = self._refresh_letter_scores_around(best_i)
                    # A last-resort word dries the slots it breaks on
                    # purpose: the nodes below must not backtrack on them.
                    newly_tolerated = [j for j in broken_slots if j not in self._tolerated_dry]
                    self._tolerated_dry.update(newly_tolerated)
                    if self._backtrack(deadline_checks, released):
                        return True
                    child_conflict = self._last_conflict
                    self._tolerated_dry.difference_update(newly_tolerated)
                    self._restore_letter_scores(saved_scores)
                    # A "Mots Défi" or theme-glossary candidate never counts
                    # toward MAX_DESCENTS_PER_NODE: the node explores every
                    # such hypothesis before its cap can end it.
                    if w not in challenged_set and w not in pri_set:
                        descents += 1
                    if child_conflict is None:
                        conflict_unknown = True
                    elif best_i not in child_conflict:
                        # Backjump: the failure below does not depend on
                        # this word, so no other candidate here could
                        # avoid it — pass it straight up.
                        self.assignment[best_i] = None
                        self.used_words.discard(w)
                        return self._fail(child_conflict)
                    else:
                        node_conflict |= child_conflict - {best_i}
                self.assignment[best_i] = None
                self.used_words.discard(w)
                if 0 < max_descents <= descents:
                    # This node has used up its own share of descents:
                    # hand control back to the parent, which then tries
                    # its own next candidate — so backtracking climbs
                    # toward the words placed early instead of staying
                    # stuck exploring the bottom of the tree.
                    return self._fail(None if conflict_unknown else node_conflict | slot_conflict)
            if not placed_any:
                # Every candidate of `best_i` was rejected: this slot can
                # no longer take a word without creating an impossible
                # one, so it becomes an "emplacement écarté" (see `DOC_ALGO/
                # FR/Lexicon.md`) exactly like a slot whose domain went dry
                # above — set aside from the selection cascade until
                # nothing can be placed anywhere else, shown yellow in the
                # previews, and in every other respect an ordinary slot: it
                # keeps a real, non-empty domain, so a word crossing it is
                # still placed freely as long as it does not block it
                # (`crossing_broken`), unlike a slot that is blocked (red)
                # and may never be crossed at all.
                #
                # Free to detect here: the candidate loop just above has
                # already paid for every one of this slot's own candidates.
                # A third feed into the same set, still purely internal to
                # the search — no display path ever writes to it.
                self._impossible_this_attempt.add(best_i)
                if blameable_rejection and not self.breaking_permitted:
                    # The slot cannot be filled in the current state
                    # because every candidate would block a slot that is
                    # still healthy: backtrack, exactly as for a dry slot.
                    # When every rejection only came from crossing a
                    # tolerated dead slot, backtracking cannot help, so the
                    # node moves on to its next slot instead. In the
                    # last-resort pass the node carries on too, so that it
                    # can still reach its `allow_breaking` stage. The
                    # failure depends only on why THIS slot is unfillable.
                    return self._fail(slot_conflict)
            node_conflict |= slot_conflict


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

    A slot from `excluded_slots` (a slot this attempt already knows it
    will never fill — see `Filler.excluded_slots`) is never tested
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
    the reason: budget exhausted, too-many-impossible-slots abandonment,
    interrupted by a sibling palier, or genuinely exhausted search). Such a slot is
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
    # Crossing map, built once: a confirmation here can write letters on
    # cells the slot did not already know (`known` only has to determine
    # ENOUGH of them to leave a single candidate, not all of them), so it
    # can affect the slots crossing it exactly like an ordinary placement
    # would — and must be held to the same rule (see the crossing check
    # right below).
    cell_to_slots = {}
    for i, cells in enumerate(slots):
        for cell in cells:
            cell_to_slots.setdefault(cell, []).append(i)
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
            # Same absolute rule as `Filler._backtrack`'s own crossing
            # check: a word is never written where it would leave a still-
            # open slot it crosses with no available word at all. Without
            # it, this closure could fill a crossing slot's last empty
            # cell with a letter spelling nothing real — sealing it for
            # good, since this pass runs once the search is over and
            # nothing downstream can ever reopen it, and leaving the
            # preview showing a red zone already full of letters
            # (`SRUA`, `EOFU`…) instead of an empty, genuinely repairable
            # one. Skipping the confirmation costs nothing: this slot's
            # single candidate was its only option either way, so the grid
            # was not completable whichever of the two slots gave way —
            # leaving both open simply keeps the cleanup's own repair
            # (`_clean_blocked_slots`) able to act on real empty cells.
            # Cheap: only ever reached by a slot already narrowed down to
            # exactly one available candidate.
            trial = dict(known)
            for pos, cell in enumerate(cells):
                trial[cell] = word[pos]
            breaks_crossing = False
            for cell in cells:
                for j in cell_to_slots.get(cell, ()):
                    if j == i or j in excluded or assignment[j] is not None:
                        continue
                    if all(
                        w2 == word or w2 in used_words
                        for w2 in _slot_candidates(index, len(slots[j]), slots[j], trial)
                    ):
                        breaks_crossing = True
                        break
                if breaks_crossing:
                    break
            if breaks_crossing:
                continue
            assignment[i] = word
            used_words.add(word)
            for pos, cell in enumerate(cells):
                known[cell] = word[pos]
            changed = True


def _combined_letter_counts(by_dir):
    """Sum of a cell's per-direction letter tallies (see `sample_letter_
    biases`'s `letter_scores`): every letter either crossing slot observed
    there, with both slots' occurrences added up."""
    total = Counter()
    for counts in by_dir.values():
        total.update(counts)
    return total


def _crossed_letter_counts(by_dir):
    """A cell's per-direction letter tallies (see `sample_letter_biases`'s
    `letter_scores`) crossed with each other: only the letters observed by
    BOTH the across and the down slot through that cell, each kept at the
    lower of its two counts — a letter only one direction can take is not
    really possible at a crossing cell, and the scarcer side is the one
    that limits it. A cell tallied in a single direction (a 1-letter run in
    the other direction is not a slot, or that slot has no candidate left
    to sample) has nothing to cross with and keeps that one tally as is."""
    tallies = [counts for counts in by_dir.values() if counts]
    if not tallies:
        return Counter()
    if len(tallies) == 1:
        return Counter(tallies[0])
    first, second = tallies
    return Counter({
        letter: min(count, second[letter])
        for letter, count in first.items()
        if second.get(letter)
    })


def _crossed_letter_option_count(by_dir):
    """`len(_crossed_letter_counts(by_dir))`, without building the Counter
    — `Filler._slot_min_letter_options` asks this for every free cell of
    every candidate slot on every search node."""
    tallies = [counts for counts in by_dir.values() if counts]
    if not tallies:
        return None
    if len(tallies) == 1:
        return len(tallies[0])
    first, second = tallies
    return sum(1 for letter in first if second.get(letter))


def _most_probable_letter(by_dir):
    """The single most likely letter of a cell, read from its crossed
    tally (`_crossed_letter_counts`), or `None` when the two directions
    share no letter at all."""
    crossed = _crossed_letter_counts(by_dir)
    if not crossed:
        return None
    return crossed.most_common(1)[0][0]


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
    (`_pattern_attempt`'s own `locked_impossible_slots`: entirely locked,
    yet spelling no real word) never offers any of its own cells as a
    candidate to become a seed — placing a seed there would be a wasted
    hint on a slot that has no letter left to choose. Only affects
    `forced`: `letter_
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
    a separate explicit check — unlike `excluded_slots` above, which
    covers a slot whose own already-known letters spell no real word at
    all, a case this length-and-letter filtering alone doesn't catch. A
    cell
    already present in `known_letters` is also never offered as an
    `eligible` candidate (see below): the word already known at that
    position needs no further statistical hint, and keeping it would have
    wasted the single-seed-per-slot quota on a cell that genuinely
    would have needed it.

    Returns `(forced, letter_scores)`:
    - `forced`: a {cell: letter} dict — the "hints" Filler treats as
      constraints as long as no crossing slot is genuinely assigned at
      that cell (see Filler._domain), not as definitively placed letters;
    - `letter_scores`: a {cell: {direction: Counter(letter ->
      occurrences)}} dict — the *complete* tally of the sampling above at
      every white cell of the grid (not just the winning letter kept for
      `forced`), kept SEPARATELY for each of the two slots crossing a
      cell (`"across"`/`"down"`, `slot_direction`), each contributing its
      own sample. `_combined_letter_counts` sums both directions — what
      `Filler._candidate_score` sorts a slot's candidate words by (sum of
      squares over its still-free cells) and what `Filler._slot_letter_
      frequency_score` reads; `_crossed_letter_counts` keeps only the
      letters BOTH directions observed, each at the lower of its two
      counts — what `Filler._slot_min_letter_options`, the "Stats" button
      (`_interactive_letter_stats`) and the previews' statistical letters
      (`Filler.stat_letters`) read."""
    slots = extract_slots(grid, rows, cols)
    cell_to_slots = defaultdict(list)
    for slot_idx, cells in enumerate(slots):
        for cell in cells:
            cell_to_slots[cell].append(slot_idx)

    excluded = excluded_slots or set()
    known = known_letters or {}
    eligible = []  # (count, cell, letter) — cells exceeding LETTER_BIAS_MIN_COUNT
    letter_scores = defaultdict(dict)
    for slot_idx, cells in enumerate(slots):
        length = len(cells)
        direction = slot_direction(cells)
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
            letter_scores[cell][direction] = counts
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


def _quota_overflow_slot_indices(assignment, offending_words):
    """Indices of every slot whose assigned word is one of `offending_
    words` — used by `try_fill` to flag, as genuinely impossible, the
    exact word(s) responsible for a grid being rejected by its own
    proper-noun/non-gloss quota safety net alone (see `MAX_PROPER_NOUNS`/
    `MAX_NON_GLOSS_WORDS`), at the user's explicit request: such a grid is
    otherwise perfectly filled and internally consistent — every domain
    check passes, no crossing deadlock — so without this, the rejected
    attempt's own preview showed no red/yellow highlighting at all,
    looking inexplicably "clean" while still being marked failed, and the
    cross-palier retry machinery (`impossible_slots`, `_clean_blocked_
    slots`/`_build_retry_seed`) had no signal at all to remove and replace
    the offending word(s) with a different candidate. `offending_words`
    is every word from `proper_noun_words`/`non_gloss_words` actually
    present in `assignment` — the whole set over quota, not only the
    excess beyond the allowed count, since there is no principled way to
    single out which specific instance(s) are "the extra ones"."""
    if not offending_words:
        return []
    return [i for i, word in enumerate(assignment) if word in offending_words]


def try_fill(grid, rows, cols, index, rng, deadline_checks=None, diagnostics=None,
             forced_letters=None, letter_scores=None, preseed_assignment=None,
             excluded_slots=None, cancel_event=None, batch_abandoned_event=None,
             attempt_done_event=None, locked_letters=None, best_state_queue=None,
             checks_progress=None, checks_slot=None, attempt_active=None, attempt_id=None,
             proper_noun_words=None, max_proper_nouns=None,
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
    excluded_slots`) drops a slot out of the grid this search has to solve
    at all: never selected, never required by `truly_complete`, never
    counted as a broken crossing, never surfaced by any diagnostic. It is
    NOT an "emplacement écarté" (`Filler._impossible_this_attempt`, the
    yellow overlay — see `DOC_ALGO/FR/Lexicon.md`), which is only ever a
    deprioritization and always stays available as a last resort.
    `_optimize_before_cleanup` is the only caller that passes it, to
    complete whatever else it can while deliberately leaving an
    entirely-empty or already-impossible zone untouched; every generation
    palier (`_pattern_attempt`/`_pattern_continue`) leaves it `None`.

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
    `UNFILLABLE_ABANDON_SLOT_COUNT` (3) still-unassigned slots already
    were deemed impossible (see `Filler.abandoned`) — at
    that point continuing to search elsewhere on the same pattern isn't
    worth the remaining budget; only possible when `UNFILLABLE_ABANDON_
    ENABLED` is True (currently False, so this reason never occurs at
    present); "interrupted_other_attempt_done" means this
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
            diagnostics["deadlock_cells"] = []
            diagnostics["excluded_cells"] = []
            diagnostics["stat_letters"] = []
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
                     priority_words=priority_words, challenge_words=challenge_words,
                     rows=rows, cols=cols)
    if checks_progress is not None and checks_slot is not None:
        # See `_worker_checks_progress`'s own docstring — `checks_progress`
        # is a `multiprocessing.Array`, one cell per concurrent slot of the
        # current palier (0..PARALLEL_ATTEMPTS-1); this one attempt owns
        # `checks_slot` exclusively for as long as it runs (no other task
        # writes to the same cell concurrently), so a plain overwrite is
        # enough — no need to guard against going backwards the way a
        # single value shared by every attempt at once would. The parent's
        # periodic budget-percentage report reads the AVERAGE of every
        # cell, not any one attempt's own value. Wired unconditionally
        # whenever a caller supplies both, independent of `best_state_
        # queue` right below — this is real, continuous progress, not a
        # "new record" event.
        def _publish_checks_progress(checks):
            checks_progress[checks_slot] = checks
        filler.on_checks_progress = _publish_checks_progress
    if checks_progress is not None and attempt_active is not None and checks_slot is not None:
        # Lets `Filler._backtrack`'s own deadline check (see its own
        # comment) see every sibling attempt's live progress — at the
        # user's explicit request: "Autoriser les grilles à dépasser leur
        # budget, tant qu'il reste des grilles qui ne l'ont pas atteint."
        # Plain references to the same shared arrays `_publish_checks_
        # progress` above already writes into — read-only from this
        # Filler's own point of view, no new synchronization needed.
        filler._sibling_checks_progress = checks_progress
        filler._sibling_attempt_active = attempt_active
        filler._checks_slot = checks_slot
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
                "deadlock_cells": filler.deadlock_zone_cells(),
                "excluded_cells": filler.excluded_zone_cells(best_assignment, include_deadlock=True),
                "stat_letters": filler.best_stat_letters_for(),
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
        # Sibling of `_publish_new_best` above, for `Filler.on_live_state`
        # (see its own docstring): a periodic snapshot of the CURRENT
        # assignment, regardless of whether it's a new record — at the
        # user's explicit request: "La grille doit bouger aussi. L'algo
        # doit tester de nouvelles combinaisons [et ça doit se voir]."
        # Deliberately cheap, unlike `_publish_new_best`: skips `filler.
        # deadlock_zone_cells()`/`impossible_zone_slots()` entirely, and
        # publishes the cheaper `empty_domain_zone_cells()` in place of
        # `impossible_zone_cells()` (see that method): what is skipped is
        # exactly `_crossing_deadlock_slots`, which builds a letter set
        # out of every open slot's full domain — the cost profile that
        # already caused a real, separately-fixed slowdown when run too
        # often. The plain empty-domain red IS shown, so a heartbeat tile
        # never understates a genuine dead end as a mere yellow "écarté";
        # only the more vivid crossing-deadlock red is missing until the
        # next real record.
        # `excluded_zone_cells()` is included regardless, but with its own
        # `include_deadlock` left at its default `False` — unlike the
        # three skipped above, its DEFAULT source
        # (`_impossible_this_attempt`, already maintained for free by
        # `_backtrack` itself) is a plain set lookup, cheap enough for
        # every cadence — its OPTIONAL extra
        # source (`include_deadlock=True`, see its own docstring) is
        # exactly the same domain-iterating cost as the three skipped
        # above, so it stays off here, at this heartbeat's own much
        # tighter cadence. Marked `"kind":
        # "heartbeat"` so the parent's
        # drain thread (`_drain_best_state_queue_continuously`) forwards it
        # straight to the live display without ever appending it to
        # `best_state_buffer` — that buffer feeds the end-of-palier
        # candidate selection (`display_unique`/`_playable_score`), which
        # must only ever see genuine recorded bests, never a mid-
        # backtracking state that can be LESS complete than one already
        # reported. Carries neither `grid` nor `assignment` (never read for
        # a message excluded from that buffer) nor `impossible_slots`
        # (same reason `_publish_new_best`'s own comment gives it — only
        # relevant to a message that can actually win the selection).
        def _publish_live_state(current_assignment):
            example_grid, forced_cells, _ = build_partial_letters_grid(
                grid, slots, current_assignment, forced_letters, locked_letters
            )
            best_state_queue.put({
                "example_grid": example_grid,
                "impossible_cells": filler.empty_domain_zone_cells(current_assignment),
                "deadlock_cells": [],
                "excluded_cells": filler.excluded_zone_cells(current_assignment),
                "stat_letters": filler.stat_letters(current_assignment),
                "forced_cells": forced_cells,
                "locked_cells": locked_cells,
                "theme_cells": _theme_word_cells(slots, current_assignment, priority_words),
                "challenge_cells": _challenge_word_cells_from_assignment(
                    slots, current_assignment, challenge_words
                ),
                "checks": filler.checks,
                "reason": "live_heartbeat",
                "attempt_id": attempt_id,
                "kind": "heartbeat",
            })
        filler.on_live_state = _publish_live_state
    if preseed_assignment is not None:
        filler.assignment = list(preseed_assignment)
        filler.used_words = {w for w in preseed_assignment if w is not None}
        filler.best_assignment = list(preseed_assignment)
        filler.best_assigned_count = sum(1 for w in preseed_assignment if w is not None)
    filler.mark_immediately_impossible_slots()
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
    # fill failure; the cross-palier retry mechanism already in place
    # (generate_grid) already retries normally on any `None`, and the
    # offending word(s) are folded into `impossible_slots`/`impossible_
    # cells` below (see `_quota_overflow_slot_indices`) so that machinery
    # — and the preview — treat them exactly like any other impossible
    # slot, rather than needing a separate recovery path of their own.
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
            diagnostics["deadlock_cells"] = filler.deadlock_zone_cells()
            diagnostics["excluded_cells"] = filler.excluded_zone_cells(
                filler.best_assignment, include_deadlock=True
            )
            diagnostics["stat_letters"] = filler.best_stat_letters_for()
            diagnostics["assigned_letter_count"] = assigned_letter_count
            diagnostics["assignment"] = list(filler.best_assignment)
            diagnostics["impossible_slots"] = filler.impossible_zone_slots()
            # Quota-overflow safety net (see over_proper_noun_budget/
            # over_non_gloss_budget above): a grid rejected only for this
            # reason has no unassigned or crossing-deadlocked slot at all
            # in the CSP sense — `impossible_zone_slots()` above comes back
            # empty. The word(s) actually responsible are folded in here
            # too, so this kind of rejection gets the same red highlighting
            # in the preview, and the same cross-palier repair (removed by
            # `_clean_blocked_slots`/`_build_retry_seed` like any other
            # impossible slot, then replaced by a different candidate on
            # retry) as any other failed attempt — see
            # `_quota_overflow_slot_indices`'s own docstring.
            if over_proper_noun_budget or over_non_gloss_budget:
                offending_words = set()
                if over_proper_noun_budget:
                    offending_words |= {w for w in filler.best_assignment if w in proper_noun_words}
                if over_non_gloss_budget:
                    offending_words |= {w for w in filler.best_assignment if w in non_gloss_words}
                quota_slots = _quota_overflow_slot_indices(filler.best_assignment, offending_words)
                diagnostics["impossible_slots"] = sorted(set(diagnostics["impossible_slots"]) | set(quota_slots))
                diagnostics["impossible_cells"] = sorted(
                    set(diagnostics["impossible_cells"]) | {cell for i in quota_slots for cell in slots[i]}
                )
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
                            permanent_locked_letters=None, permanent_black_cells=None,
                            challenge_words=None):
    """`challenge_words` (`None`/empty by default — no effect for any
    pre-existing caller): a "Mots Défi" word already placed in `result`'s
    own `assignment` is protected the exact same way a `permanent_locked_
    letters` cell already is — its own cells are folded into the hard
    per-cell constraint AND into the preseed passed to every trial's own
    `try_fill` (recomputed on every accepted removal, since a challenge
    word can move to a different slot across trials the same way any
    other placed word already can), and exempted from this function's own
    final "every word must be a real dictionary entry" acceptance check.
    Without this, a black cell removed near a challenge word's own slot
    would rerun the CSP fill with no memory that this slot was meant to
    keep that exact, possibly non-dictionary, word — either silently
    replacing it with a different real word the fresh search happens to
    prefer, or, if the crossings alone still forced back the same
    non-dictionary spelling, rejecting an otherwise perfectly legitimate
    removal for having no dictionary-entry exemption to fall back on. This
    mirrors the protection every other cross-palier repair stage already
    gives a challenge word (`_optimize_before_cleanup`/`_shorten_
    impossible_zones`/`_lengthen_impossible_zones`/`_clean_continue_
    candidate`'s own `_challenge_word_cells` exemption) — this was the one
    stage of the pipeline that didn't yet.

    `permanent_black_cells` (`None`/empty by default — no effect for
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
                # `challenge_words` gets the exact same treatment, merged
                # into the same map — recomputed from the CURRENT (slots,
                # assignment) on every trial, since an earlier accepted
                # removal in this same pass may have already changed which
                # slot a challenge word occupies. `locked_letters` provides
                # the hard constraint (`Filler.locked_letters`, consulted
                # for every cell it covers, including a slot only partially
                # covered by it); `preseed_assignment` additionally
                # promotes, verbatim and without ever revalidating it
                # against the dictionary, every slot ENTIRELY covered by
                # these cells — recomputed here on `grid`, the pattern this
                # same `try_fill` will itself re-query via `extract_slots`,
                # to stay aligned with the structure it will actually use.
                merged_locked_letters = dict(permanent_locked_letters or {})
                for cells, word in zip(slots, assignment):
                    if word is not None and challenge_words and word in challenge_words:
                        merged_locked_letters.update(zip(cells, word))
                permanent_preseed = None
                if merged_locked_letters:
                    trial_slots = extract_slots(grid, rows, cols)
                    permanent_preseed = [
                        "".join(merged_locked_letters[cell] for cell in cells)
                        if all(cell in merged_locked_letters for cell in cells) else None
                        for cells in trial_slots
                    ]
                new_result = try_fill(grid, rows, cols, index, rng, deadline_checks,
                                       cancel_event=cancel_event,
                                       proper_noun_words=proper_noun_words,
                                       max_proper_nouns=max_proper_nouns,
                                       non_gloss_words=non_gloss_words,
                                       max_non_gloss=max_non_gloss,
                                       priority_words=priority_words,
                                       locked_letters=merged_locked_letters or None,
                                       preseed_assignment=permanent_preseed)
                if new_result is not None:
                    new_slots, new_assignment = new_result
                    if all(
                        w is not None and (
                            w in word_sets.for_cells(new_slots[i]).get(len(w), ())
                            or (merged_locked_letters and all(
                                cell in merged_locked_letters for cell in new_slots[i]
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
    word`), returns `(impossible_cells, low_candidate_cells, deadlock_
    cells)` — three sorted `[r, c]` lists, to display on the interface in
    "Interactif" mode just like on the previews: red for a still-open
    slot no dictionary word fits anymore (given the letters already
    placed and the words already used elsewhere), orange for a slot with
    strictly fewer than `PREFILL_MIN_WORD_COUNT` options left, and —
    always a subset of `impossible_cells` — a more vivid red for the
    exact crossing cell(s) where two still-open slots' own remaining
    candidates share no letter at all (`Filler._crossing_deadlock_slots`),
    as opposed to the rest of those same two slots' cells, impossible only
    by association. A slot already entirely filled
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
        return [], [], []
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
        rows=rows, cols=cols,
    )
    for i, cells in enumerate(slots):
        if all(cell in known for cell in cells):
            filler.assignment[i] = "".join(known[cell] for cell in cells)
            filler.used_words.add(filler.assignment[i])

    fillable = _challenge_fillable_slot_indices(slots, known, challenge_words)
    # Second, additional "impossible" criterion — see Filler._crossing_
    # deadlock_slots' own docstring, at the user's explicit request: two
    # still-open slots crossing at a cell where their own remaining
    # candidates share no letter at all are both impossible too, whatever
    # their own individual candidate count otherwise looks like.
    deadlocked, deadlock_cells = filler._crossing_deadlock_slots(filler.used_words, challenge_words or ())
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
        elif i in deadlocked:
            impossible.update(cells)
        elif n < PREFILL_MIN_WORD_COUNT:
            low.update(cells)
    exempt = _challenge_word_cells(slots, known, challenge_words)
    for i in _invalid_fully_known_indices(slots, index, known, exempt=exempt):
        impossible.update(slots[i])
    return (
        sorted([r, c] for (r, c) in impossible),
        sorted([r, c] for (r, c) in low),
        sorted([r, c] for (r, c) in deadlock_cells),
    )


def _interactive_letter_stats(grid, rows, cols, index):
    """"Stats" button: for every still-empty white cell, the single most
    statistically likely letter — the exact same mechanism `generate_grid`
    uses to pick its own "graines" preview letters (`sample_letter_biases`,
    `force_fraction=0.0` so nothing is ever forced, only tallied), just
    read out for every cell instead of only the few crossing the sampling
    threshold. The letter is read from the cell's crossed tally
    (`_crossed_letter_counts`: only letters both the across and the down
    slot observed there, each at the lower of its two counts), the same
    figure the slot-selection cascade's most-constrained-cell level reads.
    Returns a sorted `[r, c, letter]` list; a cell whose every crossing
    slot is already impossible contributes nothing to `letter_scores`, and
    a cell whose two directions share no letter has none to show — both
    are simply omitted.
    """
    pattern = [["#" if ch == BLACK else "." for ch in row] for row in grid]
    slots = extract_slots(pattern, rows, cols)
    if not slots:
        return []
    known = {
        (r, c): grid[r][c]
        for r in range(rows)
        for c in range(cols)
        if grid[r][c] not in (BLACK, WHITE)
    }
    scratch_rng = random.Random(0)
    _, letter_scores = sample_letter_biases(
        pattern, rows, cols, index, scratch_rng, force_fraction=0.0, known_letters=known,
    )
    out = []
    for r in range(rows):
        for c in range(cols):
            if grid[r][c] != WHITE:
                continue
            by_dir = letter_scores.get((r, c))
            if not by_dir:
                continue
            letter = _most_probable_letter(by_dir)
            if letter is None:
                continue
            out.append([r, c, letter])
    return out


# Severity order of `_word_breaks_open_slot`'s own verdicts, worst last:
# a candidate that both empties a disjoint slot and blocks one it crosses
# is reported on the worse of the two, so a scan can keep looking for a
# worse verdict without ever downgrading what it already found.
_BREAK_VERDICT_RANK = {"other": 1, "crossing": 2, "crossing_blocked": 3}

# How tolerant one pass of `interactive_place_word`'s own three-tier
# search is, from strictest to last resort — the step-by-step equivalent
# of `Filler._backtrack`'s own staged node (strict slots, then released
# "emplacements écartés", then `allow_breaking`), which Interactive mode
# reaches by re-running the whole three-tier search at the next level
# instead of by recursing.
PLACEMENT_LEVEL_STRICT = 0       # only a candidate that breaks nothing
PLACEMENT_LEVEL_DISJOINT = 1     # + one that empties a DISJOINT slot
PLACEMENT_LEVEL_BREAKING = 2     # + one that blocks a slot it CROSSES


def _placement_accepted(verdict, level):
    """Whether `_word_breaks_open_slot`'s `verdict` may be accepted at
    this `PLACEMENT_LEVEL_*`.

    `"crossing_blocked"` is never accepted, at any level: the last-resort
    relaxation covers CREATING an "emplacement bloqué", never writing
    across one that is blocked already (`DOC_ALGO/FR/Lexicon.md`, "case
    croisée bloquée") — exactly the line `Filler._backtrack` draws
    between `crossing_broken` (relaxed by `allow_breaking`) and
    `crossing_still_impossible` (rejected at every stage)."""
    if verdict is None:
        return True
    if verdict == "crossing_blocked":
        return False
    if verdict == "other":
        return level >= PLACEMENT_LEVEL_DISJOINT
    return level >= PLACEMENT_LEVEL_BREAKING


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

    Returns `"crossing_blocked"`, `"crossing"`, `"other"` or `None`
    (falsy) — every caller may still use it as a plain bool, but
    `interactive_place_word`'s own staged search needs to tell them
    apart, since each one is accepted at a different, later stage (see
    `_placement_accepted`): `None` always, `"other"` once no fully safe
    candidate exists anywhere, `"crossing"` (this candidate NEWLY blocks
    an emplacement it crosses) only as the very last resort, and
    `"crossing_blocked"` (the crossed emplacement was blocked already and
    stays blocked) never.

    `baseline` (see `_open_slot_baseline`, computed once by the caller for
    this same `i`) tells a slot genuinely broken BY this candidate apart
    from one already broken beforehand for an unrelated reason. For a slot
    `i` does NOT cross, only the former counts: `w` cannot touch it, and
    blaming a pre-existing blocked zone anywhere in the grid on every
    candidate everywhere would leave "Suivant" permanently stuck. For a
    slot `i` DOES cross, both count, but they are reported apart: a slot
    alive in the baseline and blocked afterwards was blocked BY this
    candidate (`"crossing"`), while one already dead in the baseline and
    still blocked afterwards means this word crosses an "emplacement
    bloqué" (`"crossing_blocked"`, `DOC_ALGO/FR/Lexicon.md`) — the same
    split `Filler._backtrack` makes between `crossing_broken` and
    `crossing_still_impossible`, and for the same reason: only the former
    may ever be accepted, and only in the last-resort pass. Such a slot is
    therefore examined even when its own baseline is already empty. For a
    slot `j` sharing a cell with
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
    `excluded_slots` to check here — `interactive_place_word` never builds
    a `Filler` with it non-empty."""
    filler.assignment[i] = w
    filler.used_words.add(w)
    broken = None
    options_cache = {}

    def _challenge_can_fill(j):
        return bool(active_challenge_words) and any(
            w2 not in filler.used_words and filler._challenge_word_fits(j, w2)
            for w2 in active_challenge_words
        )

    for j, before in baseline.items():
        crossing = j in filler._crossing_slots[i]
        if not before and not crossing:
            continue
        if crossing:
            # Exactly the same call `Filler._backtrack` makes for its own
            # per-candidate check — one definition of "emplacement
            # bloqué", crossing deadlocks included, so Interactive mode
            # can never place a word across a slot the automatic search
            # would have refused (and that the interface is already
            # painting red).
            blocked = filler.slot_is_blocked(
                j, filler.used_words, active_challenge_words,
                options_cache, filler._crossing_slots[i], w,
            )
        else:
            blocked = not (before - {w}) and not _challenge_can_fill(j)
        if not blocked:
            continue
        if not crossing:
            verdict = "other"
        elif before or _challenge_can_fill(j):
            verdict = "crossing"
        else:
            verdict = "crossing_blocked"
        if broken is None or _BREAK_VERDICT_RANK[verdict] > _BREAK_VERDICT_RANK[broken]:
            broken = verdict
        if verdict == "crossing_blocked":
            # Nothing outranks it, so no later slot could change the
            # answer: stop scanning at once.
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
        rows=rows, cols=cols,
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
    set_budget, level, allow_reshape=True,
):
    """Shared search behind BOTH of `interactive_place_word`'s priority
    tiers ("Mots Défi" first, then the theme glossary): tries every
    ordinary, already-dictionary-viable slot this word pool fits
    (`target`'s own combos first, each slot's own words drawn by `Filler.
    ordered_candidates`, the same rule the automatic search uses) purely
    against `filler`/
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
    to be searching.

    `level` (a `PLACEMENT_LEVEL_*`) is how tolerant this pass is: which
    `_word_breaks_open_slot` verdicts count as acceptable here
    (`_placement_accepted`). `interactive_place_word` re-runs its whole
    three-tier search once per level, so a "Mots Défi" word keeps its
    priority over the theme glossary and the general dictionary at every
    level, and a word is only ever accepted at a tolerant level once the
    grid has no stricter option left anywhere. A candidate accepted at
    this level never counts against the tier's own give-up budget — only
    a real rejection does, the same rule `Filler._backtrack` applies to a
    word accepted under `allow_breaking`.

    `allow_reshape=False` skips the reshape phase entirely: only the
    "Mots Défi" tier reshapes the grid for a word, the theme tier passes
    `False` and only ever takes a slot the base pattern already offers.

    Returns `(target_index, word, cells, pattern_to_
    commit)` on success, `None` once every ordinary and reshaped candidate
    has been tried and rejected."""
    # One group per slot, `target`'s own first (the cascade already chose
    # it), and inside each group the pool's words are drawn by `Filler.
    # ordered_candidates` — the same shuffle/statistical-sort/sliding-
    # window draw `_backtrack` applies to the slot it selects, so a tier
    # never returns the same word on every click for a given grid state.
    # The groups themselves keep following their own best-scored word, so
    # the slot a strong candidate belongs to is still tried early.
    by_slot = {}
    for i in viable:
        words = [w for w in pool_flat if fits_ordinary(i, w)]
        if words:
            by_slot[i] = filler.ordered_candidates(i, words)
    order = sorted(
        by_slot,
        key=lambda i: (i != target, -max(filler._candidate_score(i, w) for w in by_slot[i])),
    )
    combos = [(i, w) for i in order for w in by_slot[i]]

    claimed = set()
    by_length = _slots_by_length(base_pattern, rows, cols)
    ordinary_words = {w for _, w in combos}
    reshape_words = [
        w for w in pool_flat
        if w not in ordinary_words and len(w) >= 2
        and _free_matching_slot(by_length, w, known, claimed) is None
    ] if allow_reshape else []
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
        verdict = _word_breaks_open_slot(filler, i, w, exemption_pool, baseline)
        if not _placement_accepted(verdict, level):
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
        verdict = _word_breaks_open_slot(cand_filler, j, w, exemption_pool, baseline)
        if not _placement_accepted(verdict, level):
            register_break(w)
            continue
        return j, w, cand_cells, cand_pattern

    return None


def _slot_cells_of(slots, slot_indices):
    """Every cell of every slot of `slot_indices`, as `[[row, col], ...]`
    — the wire shape every other interactive diagnostic uses.

    An "emplacement écarté" is a property of a whole EMPLACEMENT, so the
    whole emplacement is reported, letters included: exactly what
    `Filler.excluded_zone_cells` does for the automatic previews
    (`cells.update(self.slots[i])`). Reporting only the still-empty cells
    instead would scatter the overlay into isolated cells — an écarté
    emplacement crossed by placed words would show as a few disconnected
    squares rather than as the emplacement it actually is."""
    cells = []
    seen = set()
    for i in sorted(slot_indices):
        for cell in slots[i]:
            if cell in seen:
                continue
            seen.add(cell)
            cells.append([cell[0], cell[1]])
    return cells


def _cascade_slot_order(filler, candidates, domains, first=None):
    """Yield every index of `candidates` in `Filler._select_target_slot`'s
    own 9-level cascade order, best first — repeatedly re-selecting from
    the shrinking pool rather than sorting once, so each successive pick
    is made by the exact same cascade automatic generation uses (its
    level-3/4/5 groupings are relative to the pool it is handed, so they
    only stay meaningful when recomputed after every removal).

    `first`, when given and present in `candidates`, is yielded before
    anything else: `interactive_place_word` already resolved its own
    `target` through the same cascade before its three tiers ran, and the
    cascade draws at random within its final window, so re-selecting from
    scratch would otherwise hand tier 3 a different slot than the one the
    two tiers above were built around."""
    remaining = list(candidates)
    if first is not None and first in remaining:
        remaining.remove(first)
        yield first
    while remaining:
        i = filler._select_target_slot(remaining, domains)
        yield i
        remaining.remove(i)


def _general_dictionary_pick(filler, viable, i, exclude, baselines, level):
    """General-dictionary candidate drawn for slot `i`, or `None` if every
    one of them is refused — the per-slot half of `interactive_place_
    word`'s own tier 3, factored out so the same choice can be retried on
    another slot instead of declaring the whole grid impossible (see that
    function's own tier-3 sweep).

    The candidates are ordered by `Filler.ordered_candidates` — the very
    rule `_backtrack` applies to the slot it selects (shuffle, statistical
    sort, random draw inside the sliding `CANDIDATE_SCORE_WINDOW`) — and
    the first acceptable one wins. Interactive mode is the step-by-step
    version of the automatic search, so it draws its word from the same
    window rather than always taking the single best-scored candidate:
    a strict argmax makes "Suivant" deterministic, returning the same
    word on every click for a given grid state, with no way to reach the
    other candidates a slot genuinely has.

    `exclude` is the union of the "Mots Défi" and theme pools: any such
    word still present in `viable[i]` was necessarily already tried,
    across every slot in the grid, by one of the two tiers above, so
    reconsidering it here would just re-select the exact word already
    rejected. Excluding both pools leaving nothing at all is the one case
    this must not apply: `viable[i]` unfiltered is then tried too, so
    "Suivant" never gets stuck on a slot only such a word can fill.

    `level` (a `PLACEMENT_LEVEL_*`) is how tolerant this pass of the sweep
    is: `_placement_accepted` decides which `_word_breaks_open_slot`
    verdict may be accepted here. A candidate that leaves an emplacement
    it crosses blocked when it was blocked ALREADY is refused at every
    level, with no exception (`DOC_ALGO/FR/Lexicon.md`, "case croisée
    bloquée").

    `baselines` caches `_open_slot_baseline` per slot index across both
    phases of the sweep (it is candidate-independent, so one computation
    per slot is enough however many times that slot is revisited)."""
    cands = viable[i]
    plain_cands = cands - exclude if exclude else cands
    active_challenge_words = filler._active_challenge_words() - filler.used_words
    if i not in baselines:
        baselines[i] = _open_slot_baseline(filler, i)
    baseline = baselines[i]

    def _rank(pool):
        return filler.ordered_candidates(i, pool)

    def _first_acceptable(ranked):
        for w in ranked:
            verdict = _word_breaks_open_slot(filler, i, w, active_challenge_words, baseline)
            if _placement_accepted(verdict, level):
                return w
        return None

    word = _first_acceptable(_rank(plain_cands))
    if word is None and plain_cands != cands:
        word = _first_acceptable(_rank(cands))
    return word


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
        imp, low, deadlock = _interactive_fill_diagnostics(grid, rows, cols, index, challenge_words)
        return {
            "impossible": True, "impossible_cells": imp, "low_candidate_cells": low,
            "deadlock_cells": deadlock, "excluded_cells": [],
        }

    # A word is never placed ON an "emplacement bloqué" (red) while any
    # other emplacement can still take one — the same staging automatic
    # generation applies to an "emplacement écarté" (`DOC_ALGO/FR/
    # Lexicon.md`): such a slot is out of the running first, and only
    # becomes selectable again once nothing else is left, so "Suivant" is
    # never stuck. `_impossible_indices` is the red notion itself, crossing
    # deadlocks included (`_crossing_deadlock_indices`) — which is exactly
    # the case a plain per-slot domain check misses: a slot whose own
    # domain is fine, but which shares a cell with another open slot no
    # letter can satisfy for both at once.
    blocked_targets = set(
        _impossible_indices(slots, index, known, challenge_words=challenge_words)
    )
    selectable_targets = [i for i in viable if i not in blocked_targets] or list(viable)

    # Target slot: the same 9-level cascade as automatic generation (see
    # Filler._select_target_slot), reused as-is rather than a hand-rolled,
    # simple MRV — at the user's explicit request, after confirming live
    # that this MRV (smallest domain first) made Interactive mode start
    # with 2-letter slots scattered across the grid, honoring neither the
    # length threshold (level 3, >=4 letters) nor the centered front
    # sought by automatic generation's own geometric score (level 6). Only
    # ever resolved against the grid's own BASE pattern — never against a
    # reshaped one, since which reshape (if any) ends up mattering is only
    # known once a specific tier below actually settles on one.
    target = filler._select_target_slot(selectable_targets, domains)

    # Three tiers ("Mots Défi", then the theme glossary, then the general
    # dictionary), swept once per acceptance level — the step-by-step
    # equivalent of `Filler._backtrack`'s own staged node, which
    # Interactive mode cannot express by recursing since a click only ever
    # makes one decision:
    #
    #   PLACEMENT_LEVEL_STRICT   — only a candidate that breaks nothing;
    #   PLACEMENT_LEVEL_DISJOINT — + one that leaves a DISJOINT slot with
    #                              no word left;
    #   PLACEMENT_LEVEL_BREAKING — + one that NEWLY blocks an emplacement
    #                              it crosses.
    #
    # A whole level is exhausted, across all three tiers and every
    # still-open slot, before the next one is even tried, so a stricter
    # placement anywhere in the grid always wins over a more damaging one,
    # and "Mots Défi" keeps its precedence over the theme glossary and the
    # general dictionary at every level. The last level is the one and only
    # exception to "never create an impossible emplacement" (`DOC_ALGO/FR/
    # Lexicon.md`, "case croisée bloquée"): once nothing else can be
    # placed, a well-filled grid carrying an impossible zone — which
    # "Nettoyer", a manual edit or "Finir la grille" can then repair, since
    # the zone has to exist before it can be cleaned — beats a grid
    # declared impossible and left half empty. Crossing an emplacement that
    # is blocked ALREADY stays refused at every level, here as in the
    # automatic search (`_placement_accepted`).
    placed_from = None
    placed_target = placed_word = cells = pattern_to_commit = None
    # Slots this call finds unable to take any word without creating an
    # impossible one — "emplacements écartés" (see `DOC_ALGO/FR/Lexicon.
    # md`), collected by the general-dictionary sweep below and reported
    # to the panel as `excluded_cells`. Only that sweep feeds it: the two
    # priority tiers above reject a (word, slot) combination on the
    # word's own account, which says nothing about whether the slot could
    # take some OTHER word. Accumulated across every level, since a slot
    # set aside at a strict level really was set aside — unless a later,
    # more tolerant level ends up placing this round's word on it.
    set_aside_slots = set()

    # Tier 3's own sweep state, built once and reused at every level:
    # `exclude` is the union of the two priority pools (any such word in a
    # slot's domain was necessarily already tried there by a tier above),
    # `blocked_viable` the slots deemed "bloqué" (red), tried only after
    # every other one, and `baselines` the per-slot `_open_slot_baseline`
    # cache (candidate-independent, so one computation per slot is enough
    # however many levels revisit it).
    exclude = set(challenge_words or ()) | _flatten_priority_words(priority_words)
    selectable_set = set(selectable_targets)
    blocked_viable = [i for i in viable if i not in selectable_set]
    baselines = {}

    for level in (PLACEMENT_LEVEL_STRICT, PLACEMENT_LEVEL_DISJOINT,
                  PLACEMENT_LEVEL_BREAKING):
        # Tier 1: "Mots Défi", at the user's explicit request: a challenge
        # word is never placed without first checking what it leaves every
        # OTHER still-open slot in the grid (`_word_breaks_open_slot`, the
        # same exemption `Filler._backtrack`'s own `crossing_broken` check
        # applies) — not only a slot it directly crosses, but also a
        # disjoint one whose own domain this word happened to be the last
        # unused candidate for. A combination rejected at this level is
        # abandoned on the spot ("backtrack immédiat") in favor of the next
        # one — see `_find_priority_word_placement`'s own docstring for the
        # full two-phase search (ordinary slots first, each ranked by
        # statistical score/frequency, `target`'s own combos first; then an
        # isolated reshape attempt for any word left with no slot at all).
        challenge_pool = filler._active_challenge_words() - filler.used_words
        if challenge_pool:
            found = _find_priority_word_placement(
                challenge_pool, viable, filler, slots, target, pattern, rows, cols, rng, index,
                known, priority_words, challenge_words,
                fits_ordinary=filler._challenge_word_fits,
                is_eligible=lambda w: w not in filler._challenge_abandoned,
                register_break=filler._register_challenge_word_break,
                set_budget=lambda v: setattr(filler, "_challenge_word_budget", v),
                level=level,
            )
            if found is not None:
                placed_target, placed_word, cells, pattern_to_commit = found
                placed_from = "challenge"

        # Tier 2: the theme glossary, tried only once every "Mots Défi"
        # combination and reshape attempt has failed at this level (or none
        # was typed) — same two-phase search, restricted to genuine
        # dictionary candidates (`w in viable[i]`) belonging to the glossary
        # applicable to slot i's own direction (`_priority_words_for`), so a
        # bilingual grid's two per-language glossaries are never mixed up.
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
                    level=level,
                    allow_reshape=False,
                )
                if found is not None:
                    placed_target, placed_word, cells, pattern_to_commit = found
                    placed_from = "theme"

        # Tier 3: the general dictionary (this tier's own candidates are
        # never reshaped for — automatic generation's own widening never
        # reshapes for a plain dictionary word either, only for "Mots Défi"/
        # theme words). Unlike the two tiers above, which already search the
        # WHOLE grid for their own pool, this one sweeps every still-open
        # slot in cascade order (`_cascade_slot_order`, `target` first so the
        # slot the tiers above were built around is still tried before any
        # other) rather than considering `target` alone: `target` is only
        # ever the cascade's own FIRST choice, not the only legal one, so a
        # slot whose every candidate is refused is merely set aside and the
        # sweep moves on. Within the sweep, a slot deemed "bloqué" (red) is
        # tried only after every other one, the same staging
        # `selectable_targets` already applies to the cascade's own pick.
        if placed_from is None:
            word = None
            for group in (selectable_targets, blocked_viable):
                for i in _cascade_slot_order(
                    filler, group, domains, first=target if group is selectable_targets else None,
                ):
                    word = _general_dictionary_pick(
                        filler, viable, i, exclude, baselines, level,
                    )
                    if word is None:
                        # This slot can no longer take any word at this
                        # level: it becomes an "emplacement écarté" (see
                        # `DOC_ALGO/FR/Lexicon.md`) — reported yellow in the
                        # panel, and already set aside by this very sweep,
                        # which never comes back to a slot it has failed on
                        # before every untested one has had its turn. It
                        # stays an ordinary slot otherwise: its domain is
                        # genuinely non-empty, so a word crossing it is
                        # still placed freely as long as it does not block
                        # it, unlike a blocked slot (red), which may never
                        # be crossed. The next level releases it along with
                        # every other one, exactly as `Filler._backtrack`'s
                        # own stage 2 releases its écarté slots.
                        set_aside_slots.add(i)
                        continue
                    placed_target = i
                    break
                if word is not None:
                    break
            if word is not None:
                placed_word, cells, pattern_to_commit = word, slots[placed_target], pattern
                placed_from = "dictionary"

        if placed_from is not None:
            break

    # A slot finally placed on at a later level was never really set aside.
    set_aside_slots.discard(placed_target)
    excluded = _slot_cells_of(slots, set_aside_slots)

    if placed_from is None:
        # Not one still-open slot can take a word, even allowing one that
        # creates an impossible emplacement: nothing is placed this round.
        imp, low, deadlock = _interactive_fill_diagnostics(
            grid, rows, cols, index, challenge_words
        )
        return {
            "impossible": True, "impossible_cells": imp,
            "low_candidate_cells": low, "deadlock_cells": deadlock,
            "excluded_cells": excluded,
        }

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
    imp, low, deadlock = _interactive_fill_diagnostics(new_grid, rows, cols, index, challenge_words)
    return {
        "impossible": False,
        "grid": new_grid,
        "impossible_cells": imp,
        "low_candidate_cells": low,
        "deadlock_cells": deadlock,
        # Computed on the grid BEFORE this placement (see `excluded`
        # above): an "emplacement écarté" is a slot this call set aside
        # while looking for somewhere to place its word, and the one it
        # finally placed is never one of them.
        "excluded_cells": excluded,
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


def _crossing_cells_map(slots, own_cells):
    """For every cell of `own_cells` (one slot's own ordered cells), the
    OTHER slot of `slots` running through it in the perpendicular
    direction — `None` where no such slot exists there (a white run < 2
    cells at that spot, so nothing to check). Built once per request and
    reused for every candidate word tried at this slot, by `_unsafe_
    letter_positions` below."""
    own_set = set(own_cells)
    result = {}
    for cell in own_cells:
        cross = None
        for slot_cells in slots:
            if cell in slot_cells and set(slot_cells) != own_set:
                cross = slot_cells
                break
        result[cell] = cross
    return result


def _challenge_word_fits_cells(cells, known, challenge_words, used_words):
    """True if some unused "Mots Défi" word could still legally occupy
    `cells`, given the letters already fixed there (`known`) — a bare
    geometric fit (length + letter agreement), like `Filler._challenge_
    word_fits`, never a real dictionary check. Mirrors `_challenge_
    fillable_slot_indices`'s own inline test, generalized to a raw
    `cells` list rather than an indexed `slots_list` entry."""
    if not challenge_words:
        return False
    length = len(cells)
    for w in challenge_words:
        if w in used_words or len(w) != length:
            continue
        if all(w[pos] == known[c] for pos, c in enumerate(cells) if c in known):
            return True
    return False


def _unsafe_letter_positions(cells, word, crossing_map, index, known, used_words,
                              challenge_words=None):
    """Positions (indices into `cells`/`word`) where placing `word[pos]` at
    `cells[pos]` would NEWLY make the crossing slot through that cell
    impossible to fill — no real dictionary candidate left (`used_words`
    excluded, like `_interactive_fill_diagnostics`), and no still-
    available "Mots Défi" word able to fill it either (same exemption
    `_challenge_fillable_slot_indices` already applies elsewhere). A
    crossing slot already impossible BEFORE this placement, for an
    unrelated reason, is never reported here — only a new degradation
    caused by this exact letter, the same "créerait un emplacement
    impossible" scope `_new_crossing_impossibility` already applies to
    automatic completion's own shorter/longer-word fallback search.
    Backs the red-letter warning on the "Mots"/"Croisés"/"Début"/"Fin"
    panels."""
    unsafe = set()
    for pos, cell in enumerate(cells):
        cross_cells = crossing_map.get(cell)
        if cross_cells is None:
            continue
        known_before = {c: known[c] for c in cross_cells if c in known}
        before_ok = bool(
            set(_slot_candidates(index, len(cross_cells), cross_cells, known_before)) - used_words
        ) or _challenge_word_fits_cells(cross_cells, known_before, challenge_words, used_words)
        if not before_ok:
            continue
        known_after = dict(known_before)
        known_after[cell] = word[pos]
        after_ok = bool(
            set(_slot_candidates(index, len(cross_cells), cross_cells, known_after)) - used_words
        ) or _challenge_word_fits_cells(cross_cells, known_after, challenge_words, used_words)
        if not after_ok:
            unsafe.add(pos)
    return unsafe


def _words_with_unsafe_positions(words, cells, crossing_map, index, known, used_words,
                                  challenge_words):
    """Wraps a sorted word list (as returned so far by `interactive_slot_
    candidates`/`interactive_crossing_words`/`interactive_boundary_
    candidates`) into `{"word", "unsafe"}` dicts, `unsafe` being the
    sorted list of `_unsafe_letter_positions` for that candidate — the
    shape the "Mots"/"Croisés"/"Début"/"Fin" panels use to underline, in
    red, whichever of a candidate's own letters would create a new
    impossible crossing slot if placed."""
    return [
        {
            "word": w,
            "unsafe": sorted(
                _unsafe_letter_positions(cells, w, crossing_map, index, known, used_words,
                                          challenge_words)
            ),
        }
        for w in words
    ]


def interactive_slot_candidates(grid, rows, cols, index, cells, priority_words=None,
                                 challenge_words=None):
    """"Mots" button (mode "Interactif"), at the user's explicit request:
    "add a Mots button listing the possible words for the selected slot...
    First, the theme-glossary words if there are any... then the other
    words... When the player clicks a word, it gets placed on the
    selected slot." `cells` is the ordered `(row, col)` list of the slot
    selected on the interface (computed by `selectedInteractiveWord()`,
    script.js) — never recomputed here from `extract_slots`, to stay
    correct even while the slot is still partially empty (a not-yet-
    complete slot has no stable slot-list index anyway).

    Returns `(theme_words, other_words)`, two sorted lists of `{"word",
    "unsafe"}` dicts for real dictionary words compatible with the
    letters already placed on `cells` (the same per-position intersection
    as `_slot_candidates`, reused as-is) — `theme_words`: the candidates
    belonging to the theme glossary applicable to `cells`'s own direction
    (`_priority_words_for`, like `interactive_place_word`), always
    complete, never truncated; `other_words`: the rest, capped at
    `INTERACTIVE_SLOT_CANDIDATES_LIMIT`. A word already used elsewhere in
    the grid (another slot, entirely filled, already carrying this word)
    is excluded from both lists — like `interactive_place_word`, to never
    offer a duplicate. Each entry's own `unsafe` (`_words_with_unsafe_
    positions`) lists the 0-indexed positions in `word` that would create
    a new impossible crossing slot if placed — `challenge_words` treated
    as part of the dictionary for that check, same exemption `_interactive_
    fill_diagnostics` already applies."""
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
    crossing_map = _crossing_cells_map(slots, cells)
    theme_words = _words_with_unsafe_positions(
        sorted(themed), cells, crossing_map, index, known, used_words, challenge_words,
    )
    other_words = _words_with_unsafe_positions(
        sorted(other)[:INTERACTIVE_SLOT_CANDIDATES_LIMIT], cells, crossing_map, index, known,
        used_words, challenge_words,
    )
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


def interactive_crossing_words(grid, rows, cols, index, cell, challenge_words=None):
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
      `across_words`/`down_words` are the real candidate words, each a
      sorted list of `{"word", "unsafe"}` dicts (`_words_with_unsafe_
      positions`, see `interactive_slot_candidates`) capped at
      `INTERACTIVE_SLOT_CANDIDATES_LIMIT`, carrying this letter at this
      position. A word already used elsewhere in the grid (another slot,
      entirely filled, already carrying this word) is excluded, like
      `interactive_slot_candidates`."""
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
    across_crossing_map = _crossing_cells_map(slots, across_cells)
    down_crossing_map = _crossing_cells_map(slots, down_cells)
    letters = [
        {
            "letter": letter,
            "across_words": _words_with_unsafe_positions(
                sorted(by_letter_across[letter])[:INTERACTIVE_SLOT_CANDIDATES_LIMIT],
                across_cells, across_crossing_map, index, known, used_words, challenge_words,
            ),
            "down_words": _words_with_unsafe_positions(
                sorted(by_letter_down[letter])[:INTERACTIVE_SLOT_CANDIDATES_LIMIT],
                down_cells, down_crossing_map, index, known, used_words, challenge_words,
            ),
        }
        for letter in common_letters
    ]
    return across_start, down_start, letters


def interactive_boundary_candidates(grid, rows, cols, index, cells, side, priority_words=None,
                                     challenge_words=None):
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
    candidates` — lists of `{"word", "unsafe"}` dicts (`_words_with_
    unsafe_positions`, `unsafe` positions counted within that candidate's
    own, possibly shorter-than-`cells`, span) — both sorted by `(length,
    word)` (length first, then alphabetically) rather than plain
    alphabetical order, and mixing every accepted length together — the
    word's own length already tells the caller how many of `cells` (from
    `side`'s end) it covers, no separate field needed.
    `INTERACTIVE_SLOT_CANDIDATES_LIMIT` is applied PER length rather than
    once over the combined pool: an empty/lightly-constrained slot can
    easily have hundreds of 2- or 3-letter matches, which would otherwise
    fill the entire cap on their own and silently hide every longer (more
    specific, usually more useful) length behind them — including the
    full-length matches `interactive_slot_candidates` itself would have
    shown. A word already used elsewhere in the grid is excluded, like
    `interactive_slot_candidates`/`interactive_crossing_words`."""
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
        sub_crossing_map = _crossing_cells_map(slots, sub_cells)
        theme_words.extend(
            _words_with_unsafe_positions(sorted(themed), sub_cells, sub_crossing_map, index,
                                          known, used_words, challenge_words)
        )
        other_words.extend(
            _words_with_unsafe_positions(
                sorted(other)[:INTERACTIVE_SLOT_CANDIDATES_LIMIT], sub_cells, sub_crossing_map,
                index, known, used_words, challenge_words,
            )
        )
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
    # Computed separately from `_impossible_indices` (which already folds
    # this same set in) because `_clean_blocked_slots` needs to know
    # specifically WHICH impossible slots are crossing-letter deadlocks —
    # see its own `deadlocked_slots` parameter — so it never blackens one
    # of them: blackening is a distinct mechanism from plain "nettoyage,"
    # at the user's explicit request, so such a slot only ever has
    # whatever crossing word touches its other cells removed (a no-op
    # when both sides of the deadlock are still open).
    deadlocked = _crossing_deadlock_indices(slots, index, known, challenge_words)
    impossible = sorted(
        (set(_impossible_indices(slots, index, known, challenge_words=challenge_words)) - challenge_fillable)
        | set(_invalid_fully_known_indices(slots, index, known, exempt=challenge_exempt))
    )
    if not impossible:
        return {"changed": False, "grid": grid, "cleared_count": 0}

    cleaned_assignment, _confirmed, new_black_cells, _reopened_cells = _clean_blocked_slots(
        slots, assignment, impossible, index=index, rng=rng,
        grid=grid, rows=rows, cols=cols, deadlocked_slots=deadlocked,
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
            (set(_impossible_indices(slots, index, known, challenge_words=challenge_words)) - challenge_fillable)
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


# Added to a placed word's own length before squaring it into
# `_content_score`'s sum: a theme-glossary word gets `THEME_WORD_SCORE_
# BONUS`, a "Mots Défi" word gets the larger `CHALLENGE_WORD_SCORE_BONUS`
# instead (the two never stack — a challenge word that also happens to be
# a theme word is scored with the challenge bonus only, once).
THEME_WORD_SCORE_BONUS = 2
CHALLENGE_WORD_SCORE_BONUS = 4

# A word's own length is capped at this many letters (before any bonus)
# when computing its `_content_score` contribution, so one very long word
# can't dominate the sum on its own — a themed/challenge attempt is still
# favored by the bonus above, not by chasing raw word length indefinitely.
CONTENT_SCORE_LENGTH_CAP = 7


def _content_score(pairs, priority_words=None, challenge_words=None):
    """Sum of squares of each placed word's own "scored length", over
    `pairs` (an iterable of `(word, cells)`, `word` possibly `None` for an
    unassigned slot — ignored). This is the ONE formula shared by every
    content-scoring use in `generate_grid`: breaking ties among several
    *successful* attempts of the same palier, and picking a *failed*
    palier's own "best" attempt (raw state via `_playable_score`, post-
    cleanup state via `_cleaned_playable_score`) to carry forward.

    Every placed word counts toward the sum, whether or not `priority_
    words`/`challenge_words` is set — a themed or challenge-enriched
    generation is favored by a BONUS on top of the plain word count, never
    by excluding the rest of the grid's own content from the score.

    `priority_words` (`None`/empty by default — no effect for any pre-
    existing caller before theming existed): a word belonging to ITS OWN
    slot's theme glossary (`_priority_words_for(priority_words, cells)`,
    handling a bilingual grid's per-direction `DualSet` the same way every
    other theme-aware check in this file already does) gets `THEME_WORD_
    SCORE_BONUS` added to its own length before squaring, so an attempt
    that surfaces more/longer theme words is favored over an otherwise-
    equal one that doesn't, without ignoring its non-theme words either.

    `challenge_words` (`None`/empty by default — no effect for any pre-
    existing caller before this feature existed): a "Mots Défi" word gets
    `CHALLENGE_WORD_SCORE_BONUS` added to its own length before squaring
    instead of the theme bonus (even one that isn't itself a theme word,
    and even with no theme at all), so an attempt that manages to place a
    challenge word is favored over one that doesn't, all else equal.

    A word's own length is capped at `CONTENT_SCORE_LENGTH_CAP` BEFORE any
    bonus is added — a 12-letter word scores the same raw length as a
    7-letter one, so the sum keeps favoring more/longer-within-reason
    words over one single very long word dominating the whole score."""
    total = 0
    for w, cells in pairs:
        if w is None:
            continue
        is_challenge = bool(challenge_words) and w in challenge_words
        if is_challenge:
            bonus = CHALLENGE_WORD_SCORE_BONUS
        elif priority_words and w in _priority_words_for(priority_words, cells):
            bonus = THEME_WORD_SCORE_BONUS
        else:
            bonus = 0
        scored_length = min(len(w), CONTENT_SCORE_LENGTH_CAP) + bonus
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
    cleaned_assignment, _, _, _ = _clean_blocked_slots(
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


def _crossing_deadlock_indices(slots_list, index, known, challenge_words=None):
    """Module-level counterpart to `Filler._crossing_deadlock_slots` (see
    its own docstring for the full reasoning and the user's explicit
    request behind it), for contexts with no live `Filler`/search running
    — folded into `_impossible_indices` below rather than called
    separately by each of that function's own callers. Indices of every
    still-open slot of `slots_list` (not entirely covered by `known`)
    that crosses another still-open slot at a cell where the two slots'
    own remaining candidates — real dictionary words (`_slot_candidates`,
    the same per-position intersection `_impossible_indices` itself
    already relies on) plus any still-unused `challenge_words` entry that
    geometrically fits — agree on no single letter at all. A slot whose
    own candidates are already empty in isolation contributes an empty
    letter set at every position and can therefore never be the CAUSE of
    a deadlock here (see the same guard in `Filler._crossing_deadlock_
    slots`) — that case is already `_impossible_indices`'s own pre-
    existing empty-domain criterion, one level up.

    A candidate word already fully spelled out elsewhere in the grid
    (`used`, the same "no word placed twice" convention as `Filler.
    used_words`) is excluded from every slot's own achievable-letter set
    — a real bug found live: without this exclusion, this function could
    disagree with `Filler._crossing_deadlock_slots` (which already
    excludes `used_words`) on the exact same grid, since a used-up word
    could paper over a genuine deadlock by counting as "achievable" here
    when it can no longer actually be placed — the live red highlight
    (backed by the Filler-based check) would then show a deadlock
    "Nettoyer" (backed by this one) didn't see at all."""
    used = {
        "".join(known[c] for c in cells)
        for cells in slots_list
        if all(c in known for c in cells)
    }
    available_challenge_words = (set(challenge_words) - used) if challenge_words else set()
    open_indices = [
        j for j, cells in enumerate(slots_list) if not all(c in known for c in cells)
    ]
    cell_to_slots = defaultdict(list)
    for j in open_indices:
        for pos, cell in enumerate(slots_list[j]):
            cell_to_slots[cell].append((j, pos))
    letters_by_slot = {}
    for j in open_indices:
        cells = slots_list[j]
        length = len(cells)
        sub_known = {c: known[c] for c in cells if c in known}
        letters = [set() for _ in cells]
        for w in _slot_candidates(index, length, cells, sub_known):
            if w in used:
                continue
            for pos, ch in enumerate(w):
                letters[pos].add(ch)
        for w in available_challenge_words:
            if len(w) == length and all(
                sub_known.get(c, w[pos]) == w[pos] for pos, c in enumerate(cells)
            ):
                for pos, ch in enumerate(w):
                    letters[pos].add(ch)
        letters_by_slot[j] = letters
    deadlocked = set()
    for cell, entries in cell_to_slots.items():
        for a in range(len(entries)):
            i, pi = entries[a]
            li = letters_by_slot[i][pi]
            if not li:
                continue
            for b in range(a + 1, len(entries)):
                jx, pj = entries[b]
                lj = letters_by_slot[jx][pj]
                if lj and not (li & lj):
                    deadlocked.add(i)
                    deadlocked.add(jx)
    return deadlocked


def _impossible_indices(slots_list, index, known, challenge_words=None):
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
    list returned to the caller.

    `challenge_words` (`None`/empty by default — no effect for any
    pre-existing caller before this addition) additionally folds in
    `_crossing_deadlock_indices`: two still-open slots crossing at a cell
    where their own remaining candidates share no letter at all are both
    reported impossible too, even though each one's own domain, checked
    in isolation by the loop below, is perfectly non-empty."""
    result = []
    for j, cells in enumerate(slots_list):
        length = len(cells)
        if length == sum(1 for c in cells if c in known):
            continue
        sub_known = {c: known[c] for c in cells if c in known}
        if not _slot_candidates(index, length, cells, sub_known):
            result.append(j)
    return sorted(
        set(result) | _crossing_deadlock_indices(slots_list, index, known, challenge_words)
    )


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
    `_clean_continue_candidate`/`_clean_candidate`.

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

    # Last-chance enrichment, the final thing this step does before the
    # cleanup takes over: the palier has failed and this grid is about to
    # be declared "échouée", which is the one moment a word may be placed
    # ACROSS an emplacement already known impossible (`DOC_ALGO/FR/
    # Lexicon.md`, "emplacement bloqué"). The more words the grid carries
    # when the cleanup runs, the more of them survive it and reach the
    # next palier; outside this moment the refusal stays absolute.
    #
    # Three differences from `_try_complete` above, and each one is what
    # makes this pass able to add anything at all:
    #   - the entirely-empty slots are NOT excluded, so the search
    #     genuinely tries to fill them instead of leaving them locked;
    #   - the impossible slots ARE still excluded, which is precisely what
    #     lets a word cross them: `Filler._backtrack` skips an excluded
    #     slot in its own per-candidate crossing check, so neither
    #     `crossing_broken` nor `crossing_still_impossible` can reject a
    #     candidate on its account;
    #   - the result is absorbed even when the fill never completes.
    #     `try_fill` only RETURNS a grid once every required slot is
    #     solved, which is exactly what this grid cannot do — but its own
    #     `diagnostics["assignment"]` carries the best partial state it
    #     reached either way, and that partial state is the whole point.
    #
    # Nothing here can remove or contradict what is already placed
    # (`preseed_assignment` locks every fully-confirmed slot, and a
    # partially-confirmed one keeps its letters through those same
    # crossings), and no black cell is touched — this pass only ever adds
    # letters. A word it places may well seal an impossible emplacement
    # into a fully-lettered run spelling nothing real; the recomputation
    # right below catches that case like any other
    # (`_invalid_fully_known_indices`), so such a slot still reaches the
    # cleanup flagged impossible rather than silently passing for valid.
    last_chance_slots = extract_slots(grid, rows, cols)
    last_chance_diag = {}
    try_fill(
        grid, rows, cols, index, rng, deadline_checks,
        preseed_assignment=[
            "".join(confirmed[cell] for cell in cells)
            if all(cell in confirmed for cell in cells) else None
            for cells in last_chance_slots
        ],
        excluded_slots={
            j for j, cells in enumerate(last_chance_slots)
            if tuple(cells) in impossible_cell_tuples
        },
        cancel_event=cancel_event,
        locked_letters=permanent_locked_letters or None,
        diagnostics=last_chance_diag,
    )
    if last_chance_diag.get("assignment"):
        _absorb((last_chance_slots, last_chance_diag["assignment"]))

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
            set(_impossible_indices(final_slots, index, confirmed, challenge_words=challenge_words))
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

    Returns `(grid, slots, assignment, impossible_slots, black_cell_links)`
    — unchanged, by reference, if no shorter word was ever placed (the
    common case, `black_cell_links` then an empty dict), otherwise a new
    pattern/slots/words quadruple reflecting the state after this
    preliminary cleanup, with the list of still-impossible slots reindexed
    against the new pattern. `black_cell_links` maps each newly-placed
    word's own cells (a tuple, never a numeric slot index — see the same
    reasoning just above for `remaining_cells`) to `("shorten", boundary)`
    — the one cell that was blackened to make room for it. This is what
    lets `_clean_blocked_slots` treat the word and its own boundary cell as
    one atomic unit later on: at the user's explicit request, "chaque mot
    posé plus court ou plus long qui ajoute ou déplace une case noire doit
    être traité comme unitaire avec la case noire ajoutée ou déplacée, de
    sorte qu'un backtrack annule le mot et l'ajout/déplacement de la case
    noire" — without this map, a word placed here that later turns out to
    still cross some OTHER slot that never got resolved (this function
    deliberately allows that, see `_new_crossing_impossibility`'s own
    docstring) could be removed by `_clean_blocked_slots`'s own crossing
    cleanup while the black cell added for it silently stayed behind,
    permanently and pointlessly narrowing that zone.

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
    black_cell_links = {}

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
            black_cell_links[tuple(sub)] = ("shorten", boundary)
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
            set(_impossible_indices(cur_slots, index, known, challenge_words=challenge_words))
            - _challenge_fillable_slot_indices(cur_slots, known, challenge_words)
        )
        remaining_cells = [tuple(cur_slots[j]) for j in remaining_idx]

    if not changed:
        return grid, slots, assignment, impossible_slots, {}

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
            set(_impossible_indices(final_slots, index, known, challenge_words=challenge_words))
            - _challenge_fillable_slot_indices(final_slots, known, challenge_words)
        )
    )
    return new_grid, final_slots, final_assignment, final_impossible, black_cell_links


def _lengthen_impossible_zones(grid, rows, cols, slots, assignment, impossible_slots,
                                index, rng, permanent_locked_letters=None,
                                permanent_black_cells=None, challenge_words=None,
                                black_cell_links=None):
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

    Returns `(grid, slots, assignment, impossible_slots, black_cell_links)`
    unchanged, by reference, if no lengthening was ever applied (the
    common case), otherwise a new pattern/slots/words/still-impossible-
    slots/black-cell-links quintuple reflecting the state after this step
    — exactly the same return contract as `_shorten_impossible_zones`,
    including `black_cell_links` itself: the incoming `black_cell_links`
    (`_shorten_impossible_zones`'s own, threaded straight through by
    `_clean_continue_candidate` — see that function's own docstring) is
    carried over as-is (this function never touches a cell `_known_slot_
    boundary_cells` already protects for bounding one of those words, so
    none of its own entries can ever go stale here) and merged with one
    `("lengthen", old_boundary, new_boundary)` entry per newly-placed word
    here, keyed the same way, by its own cells. `_clean_blocked_slots`
    reads this combined map to revert a word's own black-cell change
    together with the word itself, should it end up removing that word
    later — see `_shorten_impossible_zones`'s own docstring for why.

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
    black_cell_links = dict(black_cell_links or {})

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
            black_cell_links[tuple(new_cells)] = ("lengthen", old_boundary, new_boundary)
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
            set(_impossible_indices(cur_slots, index, known, challenge_words=challenge_words))
            - _challenge_fillable_slot_indices(cur_slots, known, challenge_words)
        )
        remaining_cells = [tuple(cur_slots[j]) for j in remaining_idx]

    if not changed:
        return grid, slots, assignment, impossible_slots, black_cell_links

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
            set(_impossible_indices(final_slots, index, known, challenge_words=challenge_words))
            - _challenge_fillable_slot_indices(final_slots, known, challenge_words)
        )
    )
    return new_grid, final_slots, final_assignment, final_impossible, black_cell_links


def _clean_blocked_slots(slots, assignment, impossible_slots, locked_letters=None,
                          exclude_impossible_locked=False, index=None, rng=None,
                          grid=None, rows=None, cols=None, permanent_locked_letters=None,
                          black_cell_links=None, deadlocked_slots=None, deep=False):
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

    `black_cell_links` (`None`/empty by default — no effect for any pre-
    existing caller): the map `_shorten_impossible_zones`/`_lengthen_
    impossible_zones` hand back describing exactly which black-cell change
    came paired with which word they placed (see their own docstrings).
    Resolved once, against this call's own `slots`, into `links_by_slot`
    — whenever this function's own removal logic (any of the three: the
    black-cell alternative above, the plain crossing removal below, or
    the dead-end-zone black cell) sets `assignment[j] = None` for a slot
    `j` with a link, the paired black-cell change is undone in the very
    same step (`_revert_black_cell_link`) — at the user's explicit
    request: "chaque mot posé plus court ou plus long qui ajoute ou
    déplace une case noire doit être traité comme unitaire avec la case
    noire ajoutée ou déplacée, de sorte qu'un backtrack annule le mot et
    l'ajout/déplacement de la case noire." Without this, a word placed by
    either of those two functions — which both deliberately allow
    crossing an ALREADY impossible slot elsewhere, see `_new_crossing_
    impossibility`'s own docstring — could be removed right here, in the
    very same cleanup pass, while the black cell added or moved
    specifically to fit it stayed behind for good, permanently and
    pointlessly narrowing (or failing to widen) that zone.

    Returns `(cleaned_assignment, confirmed, new_black_cells,
    reopened_cells)` — `cleaned_assignment` is a new list (never a
    mutation of the received `assignment`), with an explicit `None` for
    every removed slot, ready to directly serve as `preseed_assignment`
    for the next palier; `new_black_cells`/`reopened_cells` are the
    (possibly empty, always disjoint) sets of cells respectively newly
    blackened (this function's own black-cell alternative, OR a
    `black_cell_links` "lengthen" entry's own `old_boundary` being
    restored) and newly reopened (a reverted `black_cell_links` entry's
    own boundary) — both to be folded into the pattern passed to the next
    palier by the caller, `_clean_blocked_slots` itself never mutating
    `grid` in place (an internal working copy, discarded after the
    call).

    `deadlocked_slots` (`None`/empty by default — no effect for any
    pre-existing caller before this addition): the subset of
    `impossible_slots` known to be impossible specifically because of a
    crossing-letter deadlock (`Filler._crossing_deadlock_slots`/
    `_crossing_deadlock_indices` — two still-open slots crossing at a
    cell where their own remaining candidates share no letter at all),
    rather than a plain empty domain. At the user's explicit request —
    "il ne faut pas noircir la case. Le noircissement des cases est un
    autre mécanisme que le nettoyage simple. Le nettoyage simple ... ne
    fait que retirer les mots qui croisent un emplacement impossible" —
    a slot in this set is EXCLUDED from the black-cell alternative below
    (never tried at all for it, not even at the usual
    `BLACK_CELL_INSTEAD_OF_REMOVAL_PROBABILITY` rate): the only action
    "nettoyage simple" ever takes for such a slot is removing whatever
    crossing word(s) happen to touch its OTHER cells, exactly like any
    other impossible slot — which does nothing at all when `crossing` is
    empty (both sides of the deadlock still open), leaving it flagged
    impossible until resolved some other way (a manual edit, or
    automatic generation's own pattern-reshaping, which already treats
    black cells as fully mutable on its own terms).

    `deep` (`False` by default): the deep cleanup `generate_grid` applies
    to a cleaned grid that has reproduced the same state on
    GRID_REPEAT_DEEP_CLEANUP_STREAK consecutive full cleanups. After the
    ordinary removal above, every still-assigned word crossing a word that
    removal took out is removed in turn — one extra level, so the letters
    that forced the removed word straight back in (held by those crossing
    words) are freed too."""
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

    assignment_before_removal = list(assignment)

    cell_to_slots = defaultdict(list)
    for i, cells in enumerate(slots):
        for cell in cells:
            cell_to_slots[cell].append(i)

    new_black_cells = set()
    reopened_cells = set()
    black_cell_capable = (
        grid is not None and rows is not None and cols is not None
    )
    working_grid = [row[:] for row in grid] if black_cell_capable else None

    # `black_cell_links` (see `_shorten_impossible_zones`/`_lengthen_
    # impossible_zones`'s own docstrings) pairs a word placed by either of
    # those two functions with the exact black-cell change made to fit it —
    # keyed by the word's own cells (never a numeric slot index, since
    # `slots` here is fixed for the whole call, but the caller's own
    # tuples were built against an intermediate pattern). Resolved once,
    # against THIS call's own `slots`, into `links_by_slot`, so the
    # removal loop below can look a slot's own link up in O(1) instead of
    # re-matching cell tuples on every removal.
    links_by_slot = {}
    if black_cell_links:
        for i, cells in enumerate(slots):
            link = black_cell_links.get(tuple(cells))
            if link is not None:
                links_by_slot[i] = link

    def _revert_black_cell_link(j):
        # At the user's explicit request: "chaque mot posé plus court ou
        # plus long qui ajoute ou déplace une case noire doit être traité
        # comme unitaire avec la case noire ajoutée ou déplacée, de sorte
        # qu'un backtrack annule le mot et l'ajout/déplacement de la case
        # noire." Called right alongside every `assignment[j] = None`
        # below — reverting the black-cell change (if any) that came
        # paired with the word this exact call is removing, never
        # leaving it behind to permanently, pointlessly narrow a zone
        # whose own justification (the word) no longer exists. A no-op
        # for any `j` with no link (the overwhelming majority of removed
        # words: only ones placed by `_shorten_impossible_zones`/`_
        # lengthen_impossible_zones` this same round ever carry one).
        # Reverting never needs its own `is_structurally_valid` check,
        # unlike every ADDITION elsewhere in this function: it only ever
        # restores a cell to the exact color it had before this round's
        # shortening/lengthening touched it, a state already known valid.
        link = links_by_slot.pop(j, None)
        if link is None:
            return
        kind = link[0]
        if kind == "shorten":
            _, boundary = link
            new_black_cells.discard(boundary)
            reopened_cells.add(boundary)
            if black_cell_capable:
                working_grid[boundary[0]][boundary[1]] = WHITE
        else:
            _, old_boundary, new_boundary = link
            reopened_cells.discard(old_boundary)
            new_black_cells.add(old_boundary)
            if black_cell_capable:
                working_grid[old_boundary[0]][old_boundary[1]] = BLACK
            if new_boundary is not None:
                new_black_cells.discard(new_boundary)
                reopened_cells.add(new_boundary)
                if black_cell_capable:
                    working_grid[new_boundary[0]][new_boundary[1]] = WHITE

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
            # above). A crossing-letter deadlock (`deadlocked_slots`, see
            # this function's own docstring) is excluded from this
            # alternative entirely, at the user's explicit request:
            # blackening is a distinct mechanism from plain "nettoyage" —
            # such a slot only ever has whatever crossing word(s) touch
            # its OTHER cells removed below, which is a no-op when
            # `crossing` is empty (both sides of the deadlock still open,
            # nothing assigned to remove) — left flagged impossible until
            # resolved some other way, never blackened by this function.
            is_deadlock = bool(deadlocked_slots) and i in deadlocked_slots
            placed_black = False
            if (
                crossing and not is_deadlock and black_cell_capable
                and rng.random() < BLACK_CELL_INSTEAD_OF_REMOVAL_PROBABILITY
            ):
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
                                _revert_black_cell_link(j)
                        placed_black = True
                        break
                    working_grid[br][bc] = WHITE
            if placed_black:
                continue

            for j in crossing:
                assignment[j] = None
                _revert_black_cell_link(j)

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
                                    _revert_black_cell_link(j)
                        else:
                            working_grid[br][bc] = WHITE
    else:
        for i in impossible_slots:
            for cell in slots[i]:
                for j in cell_to_slots[cell]:
                    if j != i and assignment[j] is not None:
                        assignment[j] = None
                        _revert_black_cell_link(j)

    if deep:
        first_level_removed = [
            j for j, word in enumerate(assignment)
            if word is None and assignment_before_removal[j] is not None
        ]
        for j in first_level_removed:
            for cell in slots[j]:
                for k in cell_to_slots[cell]:
                    if k != j and assignment[k] is not None:
                        assignment[k] = None
                        _revert_black_cell_link(k)

    confirmed = {}
    for i, word in enumerate(assignment):
        if word is None:
            continue
        for cell, ch in zip(slots[i], word):
            confirmed[cell] = ch

    return assignment, confirmed, new_black_cells, reopened_cells


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
                       permanent_locked_letters=None, permanent_black_cells=None,
                       deep=False):
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
    used as a last resort: only for a cleaned grid that has reproduced
    the same state on GRID_REPEAT_DEEP_CLEANUP_STREAK consecutive full
    cleanups, where `generate_grid` passes it together with `deep=True`
    (see `_clean_blocked_slots`, one extra level of word removal).
    Neither is applied to a grid that is still progressing."""
    assignment, confirmed, _, _ = _clean_blocked_slots(
        slots, assignment, impossible_slots, locked_letters=locked_letters,
        exclude_impossible_locked=exclude_impossible_locked, index=index, rng=rng,
        permanent_locked_letters=permanent_locked_letters, deep=deep,
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
# by one, exactly the rest of the next palier's own slots. The pool is
# also capped at those slots (`PARALLEL_ATTEMPTS - reset_count`): a
# palier's mid-palier replacement attempts can hand back more candidates
# than it has workers, and only the best `PARALLEL_ATTEMPTS - reset_count`
# of them are carried forward. `max(1, ...)`: never fully empty the pool,
# even if `FULL_RESET_ATTEMPT_COUNT` exceeds the number of available
# candidates — at least the best grid itself always remains. `extract`
# picks out, from each candidate tuple, exactly what the next palier needs
# to relaunch an attempt from this entry — `(seed_grid, locked_letters)`
# by default (the full nettoyage, a fresh pattern), `(seed_grid,
# preseed_assignment, excluded_slots)` for "reprise telle quelle" (see
# `_continue_seed_pool`).
def _seed_pool(sorted_candidates, extract=lambda sc: (sc[0], sc[1]),
               reset_count=FULL_RESET_ATTEMPT_COUNT):
    keep = max(1, min(len(sorted_candidates) - FULL_RESET_ATTEMPT_COUNT,
                      PARALLEL_ATTEMPTS - reset_count))
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
# (see `_clean_candidate`, in `generate_grid`) now extended to
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

    `cand_black_cell_links` — `_shorten_impossible_zones`'s own map,
    carried through `_lengthen_impossible_zones` (which merges in its own
    entries) and finally handed to `_clean_blocked_slots` — is what lets
    that last call treat a word placed by either of the first two
    functions and its own paired black-cell change as one atomic unit: at
    the user's explicit request, "chaque mot posé plus court ou plus long
    qui ajoute ou déplace une case noire doit être traité comme unitaire
    avec la case noire ajoutée ou déplacée, de sorte qu'un backtrack
    annule le mot et l'ajout/déplacement de la case noire." Both of those
    functions deliberately allow such a word to cross an already
    impossible slot elsewhere (see `_new_crossing_impossibility`'s own
    docstring) — exactly the case `_clean_blocked_slots` can go on to
    remove a few lines later, in this very same call, once it processes
    that other, still-unresolved impossible slot.

    When `_clean_blocked_slots` placed a new black cell (its own 1/10
    alternative, or a `black_cell_links` "lengthen" entry's `old_boundary`
    being restored — both folded into `new_black_cells`) or reopened one
    (a reverted `black_cell_links` entry's own boundary — `reopened_
    cells`), the pattern's own shape changes: `new_slots` is re-extracted
    on this modified grid, and `cand_preseed_assignment` then recomposes
    the word of EVERY slot of this new pattern entirely covered by
    `confirmed` — including a brand-new slot, born from the added black
    cell (or from a reopened one merging back into a longer run), never
    itself resolved by a real search. This word is validated before being
    promoted (`_invalid_fully_known_indices`, the same safeguard as
    `_optimize_before_cleanup`/`_clean_blocked_slots` — see CLAUDE.md,
    "UI"): a combination matching no real dictionary word, even entirely
    covered by individually correct letters, is never promoted — the slot
    stays `None`, and will be rediscovered on its own as impossible at the
    very next search (`Filler.mark_immediately_impossible_slots`),
    rather than being locked in as-is for the rest of the generation."""
    cand_slots = extract_slots(cand_grid, rows, cols)
    cand_grid, cand_slots, cand_assignment, cand_impossible, cand_black_cell_links = (
        _shorten_impossible_zones(
            cand_grid, rows, cols, cand_slots, cand_diag["assignment"],
            cand_diag["impossible_slots"], index, rng,
            permanent_locked_letters=permanent_locked_letters,
            challenge_words=challenge_words,
        )
    )
    cand_grid, cand_slots, cand_assignment, cand_impossible, cand_black_cell_links = (
        _lengthen_impossible_zones(
            cand_grid, rows, cols, cand_slots, cand_assignment, cand_impossible, index, rng,
            permanent_locked_letters=permanent_locked_letters,
            permanent_black_cells=permanent_black_cells,
            challenge_words=challenge_words,
            black_cell_links=cand_black_cell_links,
        )
    )
    # Recomputed here, fresh, from `cand_slots`/`cand_assignment` — not
    # merely inherited from `cand_diag["impossible_slots"]`'s own earlier
    # snapshot, since `_shorten_impossible_zones`/`_lengthen_impossible_
    # zones` above may have already reshaped some of these slots. Needed
    # separately from `cand_impossible` (see `_clean_blocked_slots`'s own
    # `deadlocked_slots` parameter) so a crossing-letter deadlock is never
    # blackened by this cleanup step either — the cross-palier retry
    # machinery's own pattern-reshaping (this same function's own
    # `_shorten_impossible_zones`/`_lengthen_impossible_zones` calls
    # above, or a fresh pattern on a later palier) is what may eventually
    # resolve it instead.
    cand_known = {
        cell: ch
        for cells, word in zip(cand_slots, cand_assignment)
        if word is not None
        for cell, ch in zip(cells, word)
    }
    cand_deadlocked = _crossing_deadlock_indices(cand_slots, index, cand_known, challenge_words)
    cleaned_assignment, confirmed, new_black_cells, reopened_cells = _clean_blocked_slots(
        cand_slots, cand_assignment, cand_impossible,
        index=index, rng=rng, grid=cand_grid, rows=rows, cols=cols,
        permanent_locked_letters=permanent_locked_letters,
        black_cell_links=cand_black_cell_links,
        deadlocked_slots=cand_deadlocked,
    )
    if new_black_cells or reopened_cells:
        cand_seed_grid = [row[:] for row in cand_grid]
        for (br, bc) in new_black_cells:
            cand_seed_grid[br][bc] = BLACK
        for (br, bc) in reopened_cells:
            cand_seed_grid[br][bc] = WHITE
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
# by any worker whose own `Filler.abandoned` becomes true (the too-many-
# impossible-slots rule, see UNFILLABLE_ABANDON_SLOT_COUNT) — checked by
# every other worker of the same batch, which then also stop, without
# waiting to individually reach their own abandon threshold or their own
# budget.
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
# differently each time) — one attempt's "THIS pattern has too many
# impossible slots" conclusion therefore says nothing reliable about
# another attempt's completely different pattern in the same batch. Reproduced
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
# `multiprocessing.Array('l', PARALLEL_ATTEMPTS)`, one cell per concurrent
# slot of the current palier — each attempt periodically overwrites its
# own slot (`checks_slot`, passed as a plain task argument, unlike this
# array reference itself) with its current `Filler.checks` count, at the
# user's explicit request: "Ce n'est pas normal que rien ne bouge... le
# Backtracking devrait tester des combinaisons différentes" — the web
# UI's live "% budget consumed" indicator (see BUDGET_PROGRESS_REPORT_
# INTERVAL_S below) used to be derived exclusively from `_worker_best_
# state_queue`'s own messages, which only get published on a NEW record
# (`Filler.on_new_best`) — during a long stretch where no attempt beats
# its own previous best (a normal, expected occurrence for a genuinely
# hard pattern, see DOC_ALGO/FR/ReadMe.md's own "Limites de la
# recherche"), that queue goes silent, so the percentage froze right
# along with the grid preview, even though `_backtrack` was still
# genuinely working through hundreds of thousands of candidates
# underneath — giving a false impression the search had stopped. This
# array, written far more often (see CHECKS_PROGRESS_REPORT_INTERVAL)
# and completely independent of whether any record is ever beaten, lets
# the parent report real, continuous progress instead — as the AVERAGE of
# every slot's own value, not the single busiest one: a single shared
# scalar (the original design) let one struggling attempt — typically the
# one accumulating checks fastest, since each rejected candidate counts
# as one check — pin the reported percentage at 100% on its own, even
# while every other attempt of the same palier was still comfortably
# below its own budget and genuinely improving the grids shown in the
# live preview. Each slot is reset to 0 at the start of every palier by
# the parent (its meaning, like `attempt_done_event`, only ever applies
# to the palier currently running), and again whenever a freed slot is
# reassigned mid-palier (see `generate_grid`'s own worker-reassignment
# comment) — same technical constraint as every other global here (the
# array itself passed once via the pool's initializer, never as a
# submitted-task argument, to avoid the macOS "spawn" RuntimeError a raw
# `multiprocessing` primitive
# triggers as a plain task argument).
_worker_checks_progress = None
# `multiprocessing.Array('b', PARALLEL_ATTEMPTS)` mirroring `checks_
# progress` above — see `attempt_active`'s own definition in
# `generate_grid` for the full reasoning (letting an attempt exceed its
# own `deadline_checks` while a sibling is still genuinely racing, so a
# freed CPU core never idles for nothing). `_pattern_attempt`/`_pattern_
# continue` set their own `checks_slot` cell to 1 right before calling
# `try_fill` and back to 0 once it returns, whatever the outcome.
_worker_attempt_active = None
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
                  best_state_queue=None, checks_progress=None, attempt_active=None, warmup_barrier=None,
                  proper_noun_words=None,
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
        _worker_attempt_done_event, _worker_best_state_queue, _worker_checks_progress, \
        _worker_attempt_active, \
        _worker_warmup_barrier, \
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
    _worker_checks_progress = checks_progress
    _worker_attempt_active = attempt_active
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


def _minimize_trial(grid, result, rows, cols, seed, permanent_locked_letters,
                    permanent_black_cells):
    """Pool-side `minimize_black_squares` of one successful attempt, for
    `generate_grid`'s choice among several successes: run in the worker
    processes so every success is optimized in parallel, with the lexicon
    and word sets the pool initializer already installed."""
    return minimize_black_squares(
        grid, result, rows, cols, _worker_index, random.Random(seed),
        cancel_event=_worker_cancel_event,
        proper_noun_words=_worker_proper_noun_words,
        max_proper_nouns=_worker_max_proper_nouns,
        non_gloss_words=_worker_non_gloss_words,
        max_non_gloss=_worker_max_non_gloss,
        priority_words=_worker_priority_words,
        permanent_locked_letters=permanent_locked_letters,
        permanent_black_cells=permanent_black_cells,
        challenge_words=_worker_challenge_words,
    )


def _pattern_attempt(rows, cols, ratio, seed, force_letters_fraction=0.0,
                      seed_grid=None, locked_letters=None,
                      black_enrichment_fraction=POST_PREFILL_BLACK_FRACTION,
                      deadline_checks=None, permanent_locked_letters=None,
                      permanent_black_cells=None, required_cells=None,
                      checks_slot=None, racing=True):
    """`racing=False` marks a mid-palier replacement attempt (see
    `generate_grid`): it never flags its slot in `attempt_active`, so it
    never counts as a sibling still racing and never extends the budget of
    the palier's original attempts.

    Une tentative indépendante (motif + remplissage CSP complet), exécutée
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
    # Floating-black-cell widening for "Mots Défi" words — see that
    # section's own docstring just above `_pattern_attempt`. Runs on this
    # freshly generated pattern, before any word exists anywhere in it, so
    # it can never damage an already-placed word. Theme-glossary words never
    # get a slot widened or shortened for them: they only take a slot the
    # pattern already offers.
    if _worker_challenge_words:
        _widen_floating_black_cells_for_priority_words(
            grid, rows, cols, rng,
            (_worker_challenge_words,), _worker_index,
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
    # Marks this attempt's own `checks_progress`/`checks_active` slot as
    # genuinely running for as long as `try_fill` is executing — see
    # `attempt_active`'s own definition in `generate_grid` and `Filler.
    # _siblings_still_racing` for why: a sibling attempt past its own
    # `deadline_checks` reads this flag to decide whether it may keep
    # searching (some slot still marked active and below budget) or
    # should stop now (every slot either finished or itself over budget).
    # Cleared in `finally` so it's reset whatever the outcome — a normal
    # return, a raised `GenerationCancelled`, or anything else.
    if racing and checks_slot is not None and _worker_attempt_active is not None:
        _worker_attempt_active[checks_slot] = 1
    try:
        result = try_fill(grid, rows, cols, _worker_index, rng, deadline_checks=deadline_checks,
                           diagnostics=diag,
                           forced_letters=forced_letters, letter_scores=letter_scores,
                           preseed_assignment=preseed_assignment, cancel_event=_worker_cancel_event,
                           batch_abandoned_event=None,
                           attempt_done_event=_worker_attempt_done_event,
                           locked_letters=locked_letters,
                           best_state_queue=_worker_best_state_queue,
                           checks_progress=_worker_checks_progress,
                           checks_slot=checks_slot,
                           attempt_active=_worker_attempt_active,
                           attempt_id=seed,
                           proper_noun_words=_worker_proper_noun_words,
                           max_proper_nouns=_worker_max_proper_nouns,
                           non_gloss_words=_worker_non_gloss_words,
                           max_non_gloss=_worker_max_non_gloss,
                           priority_words=_worker_priority_words,
                           challenge_words=_worker_challenge_words,
                           required_cells=required_cells)
    finally:
        if racing and checks_slot is not None and _worker_attempt_active is not None:
            _worker_attempt_active[checks_slot] = 0
    return grid, result, diag


def _pattern_continue(rows, cols, seed, seed_grid, preseed_assignment, excluded_slots,
                       force_letters_fraction=0.0, deadline_checks=None,
                       permanent_locked_letters=None, required_cells=None,
                       checks_slot=None):
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
    `excluded_slots` (see `Filler.excluded_slots`) is always `None` here —
    `generate_grid` never populates it from the previous palier's own
    search diagnosis, at the user's explicit, repeated request: an
    "emplacement écarté" (yellow) is reset to nothing at the start of
    every new cycle, "JAMAIS REPRIS" — the slot(s) that were impossible at
    the previous palier are simply unassigned, ordinary slots again as far
    as this brand-new search is concerned (still real progress carried
    forward is `preseed_assignment` above and, separately, whatever
    `_clean_blocked_slots` already stripped out before this palier even
    started — see `_clean_continue_candidate`). Whichever of them turns
    out to still genuinely be dry right now is simply rediscovered live,
    the same way any brand-new pattern's own dead slot would be: `Filler.
    _backtrack`'s own per-node domain check flags it (`_impossible_this_
    attempt`) the moment it's found dry, but — at the user's own further,
    explicit correction — never lets that single dry slot alone abort the
    whole search node (`return False`) the way it once did; it's simply
    left non-priority this round (see that check's own comment) and stays
    fully REUSABLE for the rest of THIS SAME attempt the moment some other
    slot's own assignment changes and its domain becomes non-empty again —
    exactly as available as any other still-open slot, just never
    preferred over one with no such history, and never itself a reason to
    backtrack.

    Each parallel attempt of the same palier receives its own seed, like
    _pattern_attempt — `seed_grid`/`preseed_assignment` stay, for ONE
    given call, rigorously identical from one `Filler`/`try_fill` call to
    the next inside this same search (nothing new to generate once this
    attempt is launched), only the exploration order differs (`sample_
    letter_biases`'s own statistical sampling, `_backtrack`'s own
    candidate-word sorting/drawing): enough for several parallel
    attempts, starting from the same point, to reach different states of
    progress.

    This no longer means, since the `carry_seed_pool_continue` pool
    exists (see `generate_grid`), that ALL parallel attempts of the same
    "reprise telle quelle" palier necessarily receive the same `(seed_
    grid, preseed_assignment)` pair — at the user's explicit request
    ("chaque process doit repartir à l'étape suivante avec sa grille
    partiellement nettoyée"), the parent can now dispatch a different pool
    entry to each non-reset attempt of the same palier; only an
    *individual* attempt (a single call to this function) keeps a fixed
    starting point for itself.

    `required_cells` (`None` by default — no effect for any pre-existing
    caller) is passed straight through to `try_fill`, see its own
    docstring: for "Finir la zone", this lets a "reprise telle quelle"
    palier ALSO succeed directly, the moment every cell of the selected
    zone is genuinely resolved — the excluded/still-`None` slots reported
    below then simply stay as they are, exempt from ever needing to be
    "closed" by a further palier.

    Since `excluded_slots` is always empty here (see above), `truly_
    complete` coincides with the plain internal `solved`: a "reprise telle
    quelle" attempt CAN reach a genuine, complete success directly —
    `result` non-`None` — if the search manages to fill literally every
    remaining slot, whether or not `required_cells` is given (unlike the
    old design, where a non-empty `excluded_slots` made a clean success
    from this function structurally impossible outside of `required_
    cells`, always deferring full completion to some later, fresh-pattern
    palier). When the search instead ends without a complete fill (the
    common case for a still-hard grid), this function's other useful
    output is `diag` (up-to-date assignment/impossible_slots), which
    `generate_grid` re-examines to decide whether a slot still remains
    where a word could be added (in which case "reprise telle-quelle"
    continues at the next palier) or whether it's a genuine total
    blockage (no still-open slot has a non-empty domain left), in which
    case the next palier goes back through the existing cleanup
    (`_build_retry_seed`) and a fresh pattern."""
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
    # continue` attempts on distinct grids — so one attempt's "MY grid has
    # too many impossible slots" conclusion no longer says anything reliable
    # about another attempt's potentially different grid in the same
    # palier: exactly the same reasoning, applied to the same global,
    # that already motivated disabling it for `_pattern_attempt` (see
    # right above) — disabled here too for the same reason, before a
    # real live failure ever confirmed it.
    # See `_pattern_attempt`'s own matching comment for why this is set/
    # cleared around the `try_fill` call.
    if checks_slot is not None and _worker_attempt_active is not None:
        _worker_attempt_active[checks_slot] = 1
    try:
        result = try_fill(seed_grid, rows, cols, _worker_index, rng, deadline_checks=deadline_checks,
                           diagnostics=diag,
                           forced_letters=forced_letters, letter_scores=letter_scores,
                           preseed_assignment=preseed_assignment, excluded_slots=excluded_slots,
                           cancel_event=_worker_cancel_event,
                           batch_abandoned_event=None,
                           attempt_done_event=_worker_attempt_done_event,
                           locked_letters=known_letters,
                           best_state_queue=_worker_best_state_queue,
                           checks_progress=_worker_checks_progress,
                           checks_slot=checks_slot,
                           attempt_active=_worker_attempt_active,
                           attempt_id=seed,
                           proper_noun_words=_worker_proper_noun_words,
                           max_proper_nouns=_worker_max_proper_nouns,
                           non_gloss_words=_worker_non_gloss_words,
                           max_non_gloss=_worker_max_non_gloss,
                           priority_words=_worker_priority_words,
                           challenge_words=_worker_challenge_words,
                           required_cells=required_cells)
    finally:
        if checks_slot is not None and _worker_attempt_active is not None:
            _worker_attempt_active[checks_slot] = 0
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


def _one_step_previous(entry):
    """The live-preview snapshot `entry` as it must be kept under a newer
    snapshot's own `previous` field: a shallow copy without its own,
    now-stale `previous`, so a tile only ever carries ONE step of history
    rather than a full chain back to the palier's own start. `None` for a
    process whose tile has never been published yet.

    Same convention as `grid_store.save_grid_work`'s own `previous`
    field, and for the same reason: an interrupted job's STOP_DUMP is
    replayable on its own, without having to ask which state each tile
    replaced. Which channel published a tile (`reason`) and which attempt
    it belongs to (`attempt_id`) are both already part of the snapshot,
    so a comparison can tell a genuine search step apart from a change of
    publication channel or of attempt."""
    if entry is None:
        return None
    return {k: v for k, v in entry.items() if k != "previous"}


def generate_grid(width=DEFAULT_WIDTH, height=DEFAULT_HEIGHT, difficulty="easy",
                   max_words=None, black_ratio=0.0, attempts=200, seed=None,
                   wordlist_path="data/wordlist_fr_full.tsv", on_progress=None,
                   force_letters_fraction=0.0, cancel_event=None,
                   black_enrichment_fraction=POST_PREFILL_BLACK_FRACTION,
                   deadline_checks=None, resume_state=None, should_pause=None,
                   bilingual_wordlist_path=None, priority_words=None,
                   bilingual_priority_words=None, permanent_locked_letters=None,
                   permanent_black_cells=None, required_cells=None, challenge_words=None,
                   on_live_preview=None):
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
    `minimize_black_squares` (the final black-cell-removal optimization
    pass, run once the grid is fully solved) gives it the same protection
    too, via its own `challenge_words` parameter: a placed challenge
    word's cells are locked/preseeded into every trial's own `try_fill`
    exactly like `permanent_locked_letters`, and exempted from that
    function's own final "every word must be a real dictionary entry"
    check — so optimization can never silently swap it out for a
    different real word, nor reject an otherwise legitimate black-cell
    removal purely because the challenge word itself isn't in the
    dictionary.

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

    `on_live_preview`, if given, is called `on_live_preview(examples)` —
    same per-process preview shape as `on_progress`'s own `examples`
    lists — every time the search reaches a new best state on any still-
    running attempt (`Filler.on_new_best`/`best_state_queue`, see the
    palier loop below), so the caller can hold a single, continuously
    OVERWRITTEN "what does each grid currently being built look like
    right now" snapshot, distinct from `on_progress`'s own once-per-
    palier, append-only history — at the user's explicit request: "Le
    calcul doit publier chaque état du backtrack (écrasé à chaque nouveau
    backtrack)." Also called once at the very start of each palier, with
    that palier's own freshly-generated (or carried-forward) starting
    grid per process, before any backtracking has happened yet — the
    same content `on_progress`'s own "pattern" step reports, so the live
    view is never left showing a now-discarded previous palier's grid
    once a new one has actually started.

    Every entry additionally carries `previous`: the snapshot this exact
    tile replaced, one step only and `None` for a process's own first
    tile (`_one_step_previous`/`_store_live_state`). Diagnostic-only —
    what `grid_store.save_stop_dump` writes, so an interrupted job's dump
    holds both states of every tile rather than only the last one; the
    web UI never reads it (`backend/app.py` drops it from the polled job
    status).

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
    # The winner's already-minimized (grid, slots, assignment) when it was
    # picked among several successes (each one is minimized to compare
    # them), so the final optimization does not redo it.
    best_minimized = None
    # Genuine successes found so far, cumulative across the WHOLE search
    # (every palier included), never reset by the failure-side retry
    # machinery below — see MIN_SUCCESSFUL_ATTEMPTS.
    accumulated_successes = []
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
    # call (never reset, including by GRID_REPEAT_DISCARD_STREAK's
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
    # Per-candidate repetition tracking across consecutive full cleanups
    # (see GRID_REPEAT_DEEP_CLEANUP_STREAK/GRID_REPEAT_DISCARD_STREAK):
    # `carry_cleanup_streaks` maps each cleaned state produced by the last
    # full cleanup (a hashable `_cycle_start_preview` grid) to how many
    # consecutive full cleanups have produced it ("reprise telle quelle"
    # paliers in between neither count nor reset it);
    # `carry_discarded_count` is how many cleaned grids that cleanup
    # dropped, each replaced by one extra blank-grid worker next palier.
    carry_cleanup_streaks = {}
    carry_discarded_count = 0
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
    # See `_worker_checks_progress`'s own docstring: one cell per
    # concurrent slot of the palier (not a `multiprocessing.Queue`, unlike
    # `best_state_queue` above — a plain overwrite per slot is all this
    # needs, no per-message backlog to preserve), each written by whichever
    # attempt currently owns that slot with its own current `Filler.checks`
    # count, letting the parent report genuine, continuous search progress
    # — averaged across every slot, not just the busiest one — even
    # through a long stretch with no new record to publish on `best_state_
    # queue`. One object for the whole generation, like every other
    # `multiprocessing` primitive here — every cell reset to 0 at the
    # start of each palier (see below), since its meaning only ever
    # applies to the palier currently running.
    checks_progress = multiprocessing.Array("l", PARALLEL_ATTEMPTS)
    # Mirrors `checks_progress` above, one byte per concurrent slot: 1
    # while that slot's own attempt is genuinely still running, 0 once it
    # has returned (or before it's ever been dispatched) — at the user's
    # explicit request: "Autoriser les grilles à dépasser leur budget,
    # tant qu'il reste des grilles qui ne l'ont pas atteint (CPU en
    # attente pour rien)." `checks_progress` alone can't answer "is this
    # slot's attempt still racing towards the deadline, or has it already
    # stopped early (search_exhausted/cancelled/abandoned) with a low,
    # now-frozen checks count" — this array disambiguates the two, so
    # `Filler._backtrack` (see its own deadline-check comment) can tell
    # whether letting itself run past `deadline_checks` still buys
    # anything (some sibling is genuinely still working) or would just be
    # burning CPU nobody needs (every sibling already finished or also
    # over its own budget). Reset to 0 at the start of every palier
    # (alongside `checks_progress`, see below) and set/cleared by
    # `_pattern_attempt`/`_pattern_continue` themselves, around their own
    # call to `try_fill`.
    attempt_active = multiprocessing.Array("b", PARALLEL_ATTEMPTS)
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
    # `on_live_preview`'s own backing state (see this function's own
    # docstring) — one entry per still-alive process, keyed by
    # process_number and unconditionally OVERWRITTEN as new states arrive,
    # never appended: this is a "what does it look like right now" snapshot,
    # not a history. `live_state_lock` guards it, since it's written both
    # from this drain thread (below) and from the palier loop's own thread
    # (the palier-start reset, right where `progress("pattern", ...)` is
    # called) — unlike `best_state_buffer`, which only this thread ever
    # writes to. `current_seed_to_lineage_ref` is a single-element list
    # holding the CURRENT palier's own `seed_to_lineage` dict (reassigned
    # by the palier loop each palier, right after it's computed) — needed
    # here to translate a `best_state_queue` message's own `attempt_id`
    # (a seed) into the stable process_number `live_state_by_process` is
    # keyed by; a plain single-element list, no lock, since the palier
    # loop only ever REASSIGNS it wholesale (a single reference write) and
    # every later in-place mutation of that same dict (a mid-palier worker
    # reassignment, see `seed_to_lineage` itself further below) is already
    # visible through this same reference with nothing more to do.
    live_state_by_process = {}
    live_state_lock = threading.Lock()
    current_seed_to_lineage_ref = [{}]
    # Mirrors `current_seed_to_lineage_ref` above, but resolving a seed to
    # its own `checks_progress` array slot instead of its display lineage
    # number — lets `_drain_best_state_queue_continuously` attach a live
    # "% budget consumed" reading to each individual process's own tile
    # (`budget_percent`, at the user's explicit request: "Afficher le taux
    # de budget consommé par le process sur la ligne d'info de chaque
    # grille Live"), the same per-slot `checks_progress` value the
    # PALIER-wide average (`BUDGET_PROGRESS_REPORT_INTERVAL_S`) already
    # reads, just reported per-process instead of averaged away.
    current_seed_to_checks_slot_ref = [{}]

    # Not capped at 100: a palier's per-attempt budget is elastic (see
    # `Filler._siblings_still_racing`), so an attempt keeps searching past
    # its own `deadline_checks` while a sibling is still racing, and its
    # reading legitimately climbs above 100 %.
    def _budget_percent_for_slot(slot):
        if slot is None:
            return None
        return round(100 * checks_progress[slot] / resolved_deadline_checks)

    def _refresh_computing_budget_percents():
        """Re-reads `checks_progress` into every still-"computing" live
        tile's own `budget_percent`, returning whether any value changed.
        A tile's other fields only change when its worker publishes a
        record or a heartbeat; without this, a worker searching through a
        long stretch with neither kept showing the percentage of its last
        message (0 % for a tile still carrying its palier-start preview)
        while `checks_progress` itself kept climbing."""
        # Resolved through the process (lineage) number rather than the
        # tile's own `attempt_id`: a palier-start tile is built before that
        # palier's seeds are even drawn, so it carries no current seed. A
        # lineage number is unique among a palier's dispatched attempts,
        # including mid-palier replacements (each gets a fresh one).
        # `list(...)` snapshots each dict in one step, since the harvesting
        # loop mutates both in place.
        seed_to_slot = current_seed_to_checks_slot_ref[0]
        process_to_slot = {
            process_number: seed_to_slot.get(seed)
            for seed, process_number in list(current_seed_to_lineage_ref[0].items())
        }
        changed = False
        with live_state_lock:
            for process_number, entry in live_state_by_process.items():
                if entry.get("live_status") != "computing":
                    continue
                percent = _budget_percent_for_slot(process_to_slot.get(process_number))
                if percent is not None and percent != entry.get("budget_percent"):
                    entry["budget_percent"] = percent
                    changed = True
        return changed

    def _store_live_state(process_number, entry):
        """The one way a process's own live tile is replaced (the palier
        reset below writes the whole map at once instead, applying the
        same rule inline): the snapshot being overwritten is kept under
        the new one's `previous` field — see `_one_step_previous`."""
        with live_state_lock:
            entry["previous"] = _one_step_previous(
                live_state_by_process.get(process_number)
            )
            live_state_by_process[process_number] = entry

    def _publish_live_preview():
        if on_live_preview is None:
            return
        with live_state_lock:
            snapshot = sorted(
                live_state_by_process.values(),
                key=lambda e: (e["process_number"] is None, e["process_number"]),
            )
        on_live_preview(snapshot)

    def _drain_best_state_queue_continuously():
        while not stop_best_state_drain.is_set():
            try:
                msg = best_state_queue.get(timeout=0.1)
            except queue.Empty:
                msg = None
            if msg is not None:
                # A `"kind": "heartbeat"` message (see `Filler.on_live_
                # state`/`_publish_live_state`) is a mid-backtracking
                # snapshot, not a genuine record — it must never enter
                # `best_state_buffer`, which feeds the end-of-palier
                # candidate selection (`display_unique`/`_playable_score`
                # further below) and carries neither `grid` nor
                # `assignment` besides (that code's own `pop("grid")`
                # would raise `KeyError` on one). It still reaches the
                # live display below exactly like a genuine record does —
                # that's its entire purpose.
                if msg.get("kind") != "heartbeat":
                    with best_state_buffer_lock:
                        best_state_buffer.append(msg)
                if on_live_preview is not None:
                    process_number = current_seed_to_lineage_ref[0].get(msg.get("attempt_id"))
                    with live_state_lock:
                        current = live_state_by_process.get(process_number)
                    if (
                        current is not None
                        and current.get("live_status") in ("succeeded", "failed", "interrupted")
                        and current.get("attempt_id") == msg.get("attempt_id")
                    ):
                        # A record or heartbeat of an attempt whose final
                        # tile is already in place: the worker queued it
                        # just before returning, and the harvest loop got
                        # the result first. Displaying it would turn the
                        # finished tile back to "computing" for good.
                        continue
                    entry = {k: v for k, v in msg.items() if k not in ("grid", "assignment")}
                    entry["process_number"] = process_number
                    # "Encadrer en bleu les étapes qui calculent encore
                    # quelque chose" (style-guide SKILL) — every state
                    # published from inside `_backtrack` (see `Filler.
                    # on_new_best`/`_publish_new_best`) is, by construction,
                    # from an attempt that hasn't finished yet.
                    entry["live_status"] = "computing"
                    entry["budget_percent"] = _budget_percent_for_slot(
                        current_seed_to_checks_slot_ref[0].get(msg.get("attempt_id"))
                    )
                    _store_live_state(process_number, entry)
                    _publish_live_preview()
            # Checked on every iteration of this loop (~10 times per
            # second, see get()'s own timeout above), not only when a
            # message has just arrived — otherwise, a palier where no
            # attempt improves its own record for a long stretch would
            # never publish anything again at all, even though `deadline_
            # checks` itself keeps genuinely being consumed in the
            # background inside the workers. Reads `checks_progress`
            # (see its own docstring) directly, NOT `best_state_buffer`'s
            # own `checks` fields: those are only ever recorded on a NEW
            # record (`Filler.on_new_best`), so during exactly the long
            # unproductive stretch this comment already describes, they
            # stayed frozen at the last record's own checks count — the
            # percentage looked stalled right along with the live preview,
            # even though this very code path already ran, on schedule,
            # the whole time. `checks_progress` is written far more often
            # and completely independently of whether any record is ever
            # beaten, so it keeps climbing through such a stretch instead.
            now = time.monotonic()
            if now - last_budget_progress_report[0] >= BUDGET_PROGRESS_REPORT_INTERVAL_S:
                last_budget_progress_report[0] = now
                # The AVERAGE across every one of this palier's own
                # PARALLEL_ATTEMPTS slots, not the single busiest one — a
                # max-based reading let one attempt alone (typically the
                # one thrashing hardest, since each rejected candidate
                # counts as a check) pin the display at 100% while every
                # other, genuinely progressing attempt of the same palier
                # was still comfortably below its own budget, giving the
                # false impression that the whole palier's own budget was
                # already spent. A still-unused slot (0, either not yet
                # dispatched or freed and not yet reassigned) legitimately
                # counts as 0 in this average, since it isn't consuming any
                # of the palier's own search budget right now.
                checks_so_far = sum(checks_progress)
                if checks_so_far:
                    average_checks = checks_so_far / PARALLEL_ATTEMPTS
                    # Uncapped, like `_budget_percent_for_slot`: the
                    # elastic per-attempt budget can push it past 100.
                    percent = round(100 * average_checks / resolved_deadline_checks)
                    progress("budget_progress", percent=percent)
                if on_live_preview is not None and _refresh_computing_budget_percents():
                    _publish_live_preview()

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
                  checks_progress, attempt_active, warmup_barrier, proper_noun_words, max_proper_nouns,
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
            checks_progress[:] = [0] * PARALLEL_ATTEMPTS
            attempt_active[:] = [0] * PARALLEL_ATTEMPTS
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
                        "deadlock_cells": [],
                        # Never populated here, at the user's own explicit
                        # correction: "les exclusions temporaires ne sont
                        # valables que pendant une étape, et ne doivent
                        # être visibles que sur les grilles Live [...] ces
                        # exclusions temporaires doivent être réinitialisées
                        # en fin d'étape, avant la phase d'optimisation
                        # (qui précède le nettoyage). Ils peuvent
                        # éventuellement être visibles sur l'étape clef
                        # avant optimisation, mais plus ensuite." A genuine
                        # per-attempt lifecycle rule, not merely a display-
                        # priority workaround: this cycle-start preview
                        # belongs to the UPCOMING attempt, past the reset
                        # point (end of the previous attempt, before ITS
                        # own optimization ran) — the last point allowed to
                        # still show it is that previous attempt's own
                        # `pattern_attempt_failed`/`pattern_found` snapshot
                        # (see `last_examples` below), never anything
                        # published afterward.
                        "excluded_cells": [],
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
                        "deadlock_cells": [],
                        # Always empty here, like every `cycle_start_
                        # examples`/`early_examples` entry in this
                        # function — a fresh pattern never carries an
                        # `excluded_slots` set of its own, and regardless,
                        # a cycle-start preview always falls past this
                        # per-attempt signal's own reset point (see the
                        # "reprise telle quelle" branch's own comment
                        # above for the full lifecycle rule).
                        "excluded_cells": [],
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
            # Resets the live-preview snapshot to this palier's own
            # freshly-generated (or carried-forward) starting state, at the
            # user's explicit request — see `on_live_preview`'s own
            # docstring above: without this, a still-running process's own
            # entry in `live_state_by_process` would keep showing the
            # PREVIOUS palier's grid (already discarded) until that
            # process's own first `_publish_new_best` call of the new
            # palier happens to arrive.
            if on_live_preview is not None:
                with live_state_lock:
                    # Each process's own last tile of the palier that just
                    # ended is kept under the new one's `previous` field,
                    # exactly like every other replacement (`_store_live_
                    # state`/`_one_step_previous`) — this reset writes the
                    # whole map at once, so it applies the rule inline
                    # rather than through that helper (whose own lock is
                    # already held here).
                    carried_previous = {
                        pn: _one_step_previous(e)
                        for pn, e in live_state_by_process.items()
                    }
                    live_state_by_process.clear()
                    for ex in cycle_start_examples:
                        if ex.get("process_number") is not None:
                            # A shallow copy (never `ex` itself), so tagging
                            # it "computing" here can never leak onto the
                            # `examples_history` entry `progress("pattern",
                            # ...)` just above built from the very same
                            # `cycle_start_examples` list — moot today since
                            # backend/app.py already excludes the "pattern"
                            # step from `examples_history` entirely, but
                            # kept defensive rather than relying on that.
                            live_state_by_process[ex["process_number"]] = {
                                # `checks_progress` is reset to all zeros
                                # for this new palier a few lines above
                                # (before `progress("pattern", ...)` even
                                # runs — see that reset's own comment), so
                                # every process genuinely starts this
                                # palier's own budget consumption at 0%.
                                **ex, "live_status": "computing", "budget_percent": 0,
                                "previous": carried_previous.get(ex["process_number"]),
                            }
                _publish_live_preview()
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
                            required_cells=required_cells, checks_slot=i,
                        ))
                    else:
                        # The 3rd tuple element (`_task_excluded_slots`) is
                        # this pool entry's OWN previous palier's own
                        # diagnosis — deliberately never forwarded to
                        # `_pattern_continue` below, at the user's explicit,
                        # repeated request: an "emplacement écarté" (yellow)
                        # is reset to nothing at the start of every new
                        # cycle, never carried over from the one before
                        # ("JAMAIS REPRIS") — seeded instead, fresh, by this
                        # new attempt's own `Filler.mark_immediately_
                        # impossible_slots()`/live per-node domain check,
                        # from whatever the state genuinely is right now
                        # (see `_pattern_continue`'s own docstring).
                        task_seed_grid, task_preseed_assignment, _task_excluded_slots = (
                            continue_pool[(i - reset_count) % len(continue_pool)]
                        )
                        futures.append(executor.submit(
                            _pattern_continue, rows, cols, s, task_seed_grid,
                            task_preseed_assignment, None,
                            force_letters_fraction, deadline_checks,
                            permanent_locked_letters,
                            required_cells=required_cells, checks_slot=i,
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
                # Plus one blank-grid worker per cleaned grid the cleanup
                # dropped for repeating the same state (see GRID_REPEAT_
                # DISCARD_STREAK), taking over that grid's own place.
                reset_count = (
                    min(PARALLEL_ATTEMPTS, FULL_RESET_ATTEMPT_COUNT + carry_discarded_count)
                    if just_cleaned else 0
                )
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
                # (the harvesting loop further below, built on
                # `concurrent.futures.wait`) —
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
                            "deadlock_cells": [],
                            # A fresh pattern (`_pattern_attempt`) never
                            # carries an `excluded_slots` set of its own
                            # at all — and, regardless, a cycle-start
                            # preview is always past this session's own
                            # reset point (see the "reprise telle quelle"
                            # branch's own comment above for the full
                            # per-attempt lifecycle rule).
                            "excluded_cells": [],
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
                            "deadlock_cells": [],
                            # A fresh pattern (`_pattern_attempt`) never
                            # carries an `excluded_slots` set of its own
                            # at all — and, regardless, a cycle-start
                            # preview is always past this session's own
                            # reset point (see the "reprise telle quelle"
                            # branch's own comment above for the full
                            # per-attempt lifecycle rule).
                            "excluded_cells": [],
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
                        required_cells=required_cells, checks_slot=i,
                    ))
            # Future -> seed map, for `on_live_preview`'s own use in the
            # harvesting loop below (see its own comment there) — built
            # here, once both branches above have converged on a final
            # `futures` list, always index-aligned with `seeds` regardless
            # of which branch built it. Unlike `d.get("attempt_id")` (only
            # ever set by `try_fill` on the FAILURE branch — see `diag`'s
            # own construction), this mapping is available for every
            # outcome, success included.
            future_seed = dict(zip(futures, seeds))
            # Collected in completion order (`concurrent.futures.wait`,
            # see below), not submission order, at the user's explicit
            # request ("le bouton Stop ne s'applique pas rapidement... prévoir l'arrêt
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
            # Collected in completion order (`concurrent.futures.wait`), not
            # submission order — once PALIER_ATTEMPT_INTERRUPT_FRACTION (30%) of this
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
            # Mirrors `seed_to_lineage` above, but for `checks_progress`'s
            # own array slot rather than the display lineage number: at
            # dispatch time slot `i` and `seeds[i]` are the same submission,
            # so this is just the identity mapping — kept as an explicit
            # dict (like `seed_to_lineage`) so the mid-palier reassignment
            # below can look a freed slot back up by seed alone, the same
            # way it already does for lineage.
            seed_to_checks_slot = {seeds[i]: i for i in range(PARALLEL_ATTEMPTS)}
            # Published for `_drain_best_state_queue_continuously`'s own
            # use (see `current_seed_to_lineage_ref`'s own comment above) —
            # a single reference write; every later in-place mutation of
            # this same dict (the mid-palier reassignment further below)
            # is already visible through it with nothing more to do here.
            current_seed_to_lineage_ref[0] = seed_to_lineage
            # Same reference-publishing convention as `current_seed_to_
            # lineage_ref` right above, for `seed_to_checks_slot` instead.
            current_seed_to_checks_slot_ref[0] = seed_to_checks_slot
            interrupt_threshold = max(1, math.ceil(PALIER_ATTEMPT_INTERRUPT_FRACTION * len(futures)))
            # Harvests this palier's futures with `concurrent.futures.wait`
            # (not the simpler `as_completed`) specifically so `pending` can
            # grow mid-palier: every time a worker's future completes
            # (success or failure) while some original attempt is still
            # racing, the process it just freed is
            # immediately reassigned to a brand-new, from-scratch attempt
            # (the same shape as the "reset" tasks built above — never a
            # continuation of the successful grid) instead of sitting idle
            # for the rest of the palier, at the user's explicit request.
            # `interrupt_threshold`/`attempt_done_event` above keep their
            # existing, unchanged meaning and only ever count the palier's
            # own ORIGINAL `futures` (`orig_futures`) — a reassigned task is
            # never counted towards it, consistent with the fact that,
            # today, PALIER_ATTEMPT_INTERRUPT_FRACTION == 1.0 makes it a
            # no-op (see that constant's own docstring) that this new
            # mechanism must not silently change the meaning of.
            orig_futures = set(futures)
            pending = set(futures)
            outcomes = []
            orig_done_count = 0
            def _originals_all_spent():
                # Every original attempt still running is already past its
                # own budget: none of them is racing any more, so the
                # replacements must stop (see the reassignment below).
                return all(
                    checks_progress[seed_to_checks_slot[future_seed[f]]] >= resolved_deadline_checks
                    for f in pending if f in orig_futures
                )
            while pending:
                done, pending = concurrent.futures.wait(
                    pending, timeout=0.5, return_when=concurrent.futures.FIRST_COMPLETED
                )
                if not attempt_done_event.is_set() and _originals_all_spent():
                    attempt_done_event.set()
                for f in done:
                    result = f.result()
                    outcomes.append(result)
                    if f in orig_futures:
                        orig_done_count += 1
                        if orig_done_count == interrupt_threshold:
                            attempt_done_event.set()
                    g, r, d = result
                    # Needed unconditionally below (checks_progress slot
                    # reassignment), not only when a live preview is wired
                    # up — computed once here rather than duplicated in
                    # both places.
                    seed = future_seed.get(f)
                    if on_live_preview is not None:
                        # "Encadrer en jaune les étapes qui sont arrêtées
                        # parce qu'elle ont réussies, en orange ... elles
                        # sont arrivées en état échouées" (style-guide
                        # SKILL) — this specific attempt (process) just
                        # finished, one way or the other; its own live
                        # tile is updated one final time to reflect that,
                        # frozen until the next palier's own reset (see
                        # `progress("pattern", ...)`'s own sibling block
                        # above) replaces it. `future_seed` (built once per
                        # palier, see its own comment above), never `d.
                        # get("attempt_id")` — `try_fill` only ever sets
                        # `attempt_id` in its own diagnostics dict on the
                        # FAILURE branch, so it's simply absent on a
                        # genuine success.
                        process_number = seed_to_lineage.get(seed)
                        if r is not None:
                            # `r` is `try_fill`'s own raw return value on
                            # success — a `(slots, assignment)` 2-tuple, NOT
                            # the assignment alone (see `try_fill`'s own
                            # `return slots, filler.assignment`). Every
                            # function below this point expects a plain
                            # per-slot assignment list, so `r`'s own
                            # assignment half must be unpacked first —
                            # passing `r` itself corrupted the live preview:
                            # `zip(slots, r)` (a 2-element tuple) only ever
                            # paired the first two slots, one against `r`'s
                            # own `slots` list and one against its
                            # `assignment` list, writing whole lists/words
                            # into single grid cells for those two slots
                            # while leaving every other cell at `build_
                            # letters_grid`'s initial all-black fill —
                            # exactly the "grille presque entièrement noire,
                            # avec des mots en sur-impression en haut à
                            # gauche" symptom reported live.
                            _, assignment = r
                            finished_slots = extract_slots(g, rows, cols)
                            live_entry = {
                                "example_grid": build_letters_grid(rows, cols, finished_slots, assignment),
                                "impossible_cells": [],
                                "deadlock_cells": [],
                                # A genuine success leaves no slot excluded
                                # in a way still worth flagging — and `r`
                                # here carries no live `Filler` to compute
                                # it from regardless.
                                "excluded_cells": [],
                                "forced_cells": [],
                                "locked_cells": [],
                                "theme_cells": _theme_word_cells(finished_slots, assignment, priority_words),
                                "challenge_cells": _challenge_word_cells_from_assignment(
                                    finished_slots, assignment, challenge_words
                                ),
                                "live_status": "succeeded",
                            }
                        else:
                            live_entry = {
                                k: v for k, v in d.items()
                                if k in (
                                    "example_grid", "impossible_cells", "deadlock_cells",
                                    "excluded_cells", "forced_cells", "locked_cells",
                                    "theme_cells", "challenge_cells", "stat_letters",
                                )
                            }
                            # An attempt stopped by `attempt_done_event`
                            # (every original attempt of the palier done or
                            # out of budget) did not fail on its own.
                            live_entry["live_status"] = (
                                "interrupted"
                                if d.get("reason") == "interrupted_other_attempt_done"
                                else "failed"
                            )
                        live_entry["process_number"] = process_number
                        # Identifies the attempt this final tile belongs to,
                        # so the drain thread can drop that same attempt's
                        # messages still in flight (see
                        # `_drain_best_state_queue_continuously`).
                        live_entry["attempt_id"] = seed
                        # Read from `checks_progress` (via `seed_to_checks_
                        # slot`, still valid here — the mid-palier
                        # reassignment below that resets/repurposes this
                        # same slot for a brand-new attempt hasn't run yet
                        # at this point) BEFORE that reassignment zeroes it
                        # out, so this frozen tile keeps the real final
                        # percentage this specific attempt actually
                        # consumed, not the replacement attempt's fresh 0%.
                        live_entry["budget_percent"] = _budget_percent_for_slot(
                            seed_to_checks_slot.get(seed)
                        )
                        _store_live_state(process_number, live_entry)
                        _publish_live_preview()
                    # Any finished attempt, succeeded or failed, frees its
                    # process: it is handed a brand-new, from-scratch
                    # replacement attempt for as long as some ORIGINAL
                    # attempt of the palier is still racing
                    # (`attempt_done_event` is set once every original has
                    # finished or used up its budget, which also interrupts
                    # every replacement still running). A replacement never
                    # counts as racing itself (`racing=False`), so it never
                    # extends the palier.
                    if not attempt_done_event.is_set():
                        new_seed = rng.randrange(2**31)
                        # The freed slot (the one the just-finished attempt
                        # owned) is handed to its replacement, reset to 0
                        # first — the replacement is a brand-new attempt
                        # with nothing yet checked, and must not inherit the
                        # finished attempt's own high checks count into the
                        # palier's own average.
                        freed_slot = seed_to_checks_slot.get(seed)
                        if freed_slot is not None:
                            checks_progress[freed_slot] = 0
                        new_future = executor.submit(
                            _pattern_attempt, rows, cols, ratio, new_seed,
                            force_letters_fraction, None, None,
                            black_enrichment_fraction, deadline_checks,
                            permanent_locked_letters, permanent_black_cells,
                            required_cells=required_cells, checks_slot=freed_slot,
                            racing=False,
                        )
                        pending.add(new_future)
                        future_seed[new_future] = new_seed
                        seed_to_checks_slot[new_seed] = freed_slot
                        # `next_lineage_number` is the next FREE number
                        # (see `_reassign_lineage_numbers`): use it, then
                        # advance it.
                        seed_to_lineage[new_seed] = next_lineage_number
                        next_lineage_number += 1
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
            # Cumulative across the whole search — see MIN_SUCCESSFUL_
            # ATTEMPTS/accumulated_successes's own docstring above.
            accumulated_successes.extend(successes)
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
            if len(accumulated_successes) >= MIN_SUCCESSFUL_ATTEMPTS:
                # A single success is no longer enough to conclude the
                # search — see MIN_SUCCESSFUL_ATTEMPTS's own docstring.
                # `accumulated_successes` spans the WHOLE search (every
                # palier included, this one's own `successes` already
                # merged into it a few lines above), so the comparison
                # below can end up picking a winner from an earlier palier
                # over one just found here, or vice-versa. When there's
                # more than one, each is
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
                # Every success is minimized in parallel on the palier's own
                # pool (`_minimize_trial`, one task per success), and the
                # winner's minimized grid is kept as the final one
                # (`best_minimized`), so the optimization runs once per
                # success and never twice for the winner. The "minimizing"
                # step is published here, before the trials, showing every
                # success being optimized — `optimization_duration_seconds`
                # (backend/app.py) therefore measures this parallel pass.
                #
                # `accumulated_successes` always holds at least
                # MIN_SUCCESSFUL_ATTEMPTS (>= 2) entries here — the gate
                # above guarantees it — so there's always something real to
                # compare; unlike before MIN_SUCCESSFUL_ATTEMPTS existed,
                # the single-success shortcut can no longer apply at this
                # point.
                progress(
                    "minimizing",
                    # Number of successes being optimized/compared: the
                    # frontend's status line says every attempt has stopped
                    # and these grids are now being evaluated.
                    count=len(accumulated_successes),
                    examples=[
                        {
                            "example_grid": build_letters_grid(rows, cols, r[0], r[1]),
                            "impossible_cells": [],
                            "deadlock_cells": [],
                            "excluded_cells": [],
                            "forced_cells": [],
                            "locked_cells": [],
                            "theme_cells": _theme_word_cells(r[0], r[1], priority_words),
                            "challenge_cells": _challenge_word_cells_from_assignment(
                                r[0], r[1], challenge_words
                            ),
                            "process_number": d.get("process_number"),
                            "is_best": False,
                        }
                        for g, r, d in accumulated_successes
                    ],
                )
                trial_futures = [
                    executor.submit(
                        _minimize_trial, [row[:] for row in g], r, rows, cols,
                        rng.randrange(2**31), permanent_locked_letters,
                        permanent_black_cells,
                    )
                    for g, r, d in accumulated_successes
                ]
                scored = []
                for (g, r, d), fut in zip(accumulated_successes, trial_futures):
                    opt_grid, opt_slots, opt_assignment = fut.result()
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
                    scored.append((opt_black, -opt_score, g, r, d, (opt_grid, opt_slots, opt_assignment)))
                _, _, best, best_result, best_diag, best_minimized = min(
                    scored, key=lambda t: (t[0], t[1])
                )
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
            # The harvesting loop above has already drained every future of
            # this palier (originals and any mid-palier reassignments
            # alike), so every worker has already finished its own
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
                    "deadlock_cells": d.get("deadlock_cells", []),
                    # `d.get("excluded_cells", [])` — at the user's own
                    # explicit framing, exclusion is valid "pendant une
                    # étape," reset only "en fin d'étape, avant la phase
                    # d'optimisation," with this exact snapshot named as
                    # the last one still allowed to show it: "ils peuvent
                    # éventuellement être visibles sur l'étape clef avant
                    # optimisation, mais plus ensuite." This entry
                    # (`pattern_attempt_failed`/`pattern_found`) is
                    # precisely that snapshot — THIS attempt's own
                    # concluding state, published before `_optimize_
                    # before_cleanup`/cleanup ever run; the NEXT attempt's
                    # own cycle-start preview is what resets it back to
                    # `[]` (see `cycle_start_examples` above). This also
                    # happens to sidestep a real display-priority detail:
                    # `pollJob`/`advanceLiveDisplay` (`frontend/static/
                    # script.js`) prioritizes draining any `previewHistory`
                    # backlog over rendering the bare `live_preview`
                    # channel, and in a fast mode (many paliers/second)
                    # that backlog rarely empties — so a signal confined to
                    # `live_preview` alone would rarely reach the screen.
                    "excluded_cells": d.get("excluded_cells", []),
                    # The attempt's own statistical letters, same lifecycle
                    # as `excluded_cells` right above: shown on this key
                    # step and on the live tiles, never past the cleanup.
                    "stat_letters": d.get("stat_letters", []),
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
            # `_slots_touching`: a slot crossing an impossible one is
            # counted as hopeless too. Its own words are stripped by
            # `_clean_blocked_slots` at every "continue" palier anyway, so
            # treating it as a genuine hope of progress lets
            # `still_has_hope` stay `True` indefinitely and prevents
            # cleanup from ever triggering.
            selected_slots = extract_slots(selected_grid, rows, cols)
            selected_dead = selected_impossible | _slots_touching(selected_slots, selected_impossible)
            still_has_hope = any(
                w is None and i not in selected_dead
                for i, w in enumerate(selected_diag["assignment"])
            )
            # Forced cleanup if all 10 of this palier's attempts were
            # abandoned via the too-many-impossible-slots rule (see
            # UNFILLABLE_ABANDON_ENABLED/UNFILLABLE_ABANDON_SLOT_COUNT,
            # Filler.abandoned, reason == "abandoned_too_unfillable") — a
            # no-op for now, since that rule is currently disabled and so
            # this reason never occurs, at
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
                def _clean_candidate(cand_grid, cand_diag, deep):
                    # EVERY distinct attempt of this palier (up to
                    # PARALLEL_ATTEMPTS, not just the FAILED_ATTEMPT_
                    # EXAMPLES (6) shown on screen — that cap remains a
                    # DISPLAY cap, see `display_pairs`/`last_examples`
                    # above, unrelated to the real selection here) is
                    # cleaned through this function; `failed_pairs`
                    # already carries at most one entry per attempt.
                    # `deep` is the deep cleanup of a grid repeating the
                    # same state (see GRID_REPEAT_DEEP_CLEANUP_STREAK).
                    cand_slots = extract_slots(cand_grid, rows, cols)
                    cand_seed, cand_confirmed = _build_retry_seed(
                        cand_grid, rows, cols, cand_slots,
                        cand_diag["assignment"], cand_diag["impossible_slots"],
                        locked_letters=carry_locked_letters,
                        exclude_impossible_locked=deep, deep=deep,
                        seed_grid=carry_seed_grid, index=index, rng=rng,
                        permanent_locked_letters=permanent_locked_letters,
                        permanent_black_cells=permanent_black_cells,
                    )
                    return (cand_seed, cand_confirmed, cand_slots,
                            cand_diag.get("process_number"))

                def _cleaned_state_key(cand):
                    # Pattern + confirmed content merged into one
                    # comparable grid (`_cycle_start_preview`, the same
                    # merge the cycle-start preview shows), as a hashable
                    # tuple of tuples.
                    state_grid, _ = _cycle_start_preview(rows, cols, cand[0], cand[1], None)
                    return tuple(tuple(row) for row in state_grid)

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

                # Per-candidate repetition tracking (see GRID_REPEAT_DEEP_
                # CLEANUP_STREAK/GRID_REPEAT_DISCARD_STREAK): the ordinary
                # cleanup's state is compared with the previous full
                # cleanup's states; a grid repeating it is cleaned deeper,
                # and one that still repeats it despite that is dropped,
                # its place going to a blank-grid worker next palier
                # (`carry_discarded_count`). Every other candidate — a
                # grid still progressing — is kept untouched.
                cleanup_streaks = {}
                kept_candidates = []
                discarded_count = 0
                for cand_grid, cand_diag in optimized_pairs:
                    cand = _clean_candidate(cand_grid, cand_diag, deep=False)
                    key = _cleaned_state_key(cand)
                    streak = carry_cleanup_streaks.get(key, 0) + 1
                    cleanup_streaks[key] = max(cleanup_streaks.get(key, 0), streak)
                    if streak >= GRID_REPEAT_DISCARD_STREAK:
                        discarded_count += 1
                        continue
                    if streak >= GRID_REPEAT_DEEP_CLEANUP_STREAK:
                        cand = _clean_candidate(cand_grid, cand_diag, deep=True)
                    kept_candidates.append(cand)
                carry_cleanup_streaks = cleanup_streaks
                just_cleaned = True
                if kept_candidates:
                    cleaned_candidates = _sorted_by_score(
                        kept_candidates,
                        priority_words=priority_words, challenge_words=challenge_words,
                    )
                    # Blank-grid workers of the next palier (see
                    # `reset_count` in the "fresh pattern" branch), which
                    # bound how many cleaned grids it can resume.
                    next_reset_count = min(
                        PARALLEL_ATTEMPTS, FULL_RESET_ATTEMPT_COUNT + discarded_count
                    )
                    carry_seed_pool = _seed_pool(
                        cleaned_candidates, reset_count=next_reset_count
                    )
                    carry_seed_grid, carry_locked_letters = carry_seed_pool[0]
                    # See `carry_seed_pool_lineage`'s own definition
                    # (before the palier loop) for this list's own role —
                    # the same mechanism as for "reprise telle quelle"
                    # above (see `raw_continue_lineage`), but on
                    # `cleaned_candidates` (position 3 = inherited lineage
                    # number, see `_clean_candidate`).
                    raw_lineage = _seed_pool(
                        cleaned_candidates, extract=lambda sc: sc[3],
                        reset_count=next_reset_count,
                    )
                    carry_seed_pool_lineage, next_lineage_number = _reassign_lineage_numbers(
                        raw_lineage, dispatch_lineage, next_lineage_number
                    )
                    carry_discarded_count = discarded_count
                else:
                    # Every cleaned grid was dropped: the next cycle
                    # starts again from an entirely blank grid — exactly
                    # this function's own initial state (see
                    # `carry_seed_grid = None` at the very top), including
                    # both pools and the "reprise telle quelle" streak
                    # counter.
                    carry_seed_grid = None
                    carry_locked_letters = None
                    carry_preseed_assignment = None
                    carry_excluded_slots = None
                    carry_seed_pool = None
                    carry_seed_pool_continue = None
                    carry_seed_pool_lineage = None
                    carry_seed_pool_continue_lineage = None
                    # `next_lineage_number` itself is deliberately NOT
                    # reset: a grid born from a future palier must never
                    # reuse the number of an abandoned lineage.
                    consecutive_continue_paliers = 0
                    carry_cleanup_streaks = {}
                    carry_discarded_count = 0
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
    # Clears the live-preview channel now that the search itself is over —
    # at the user's explicit request: once the last key step (history
    # entry) is reached, "il ne devrait plus y avoir de nouvelle étape
    # Live." Without this, `job["live_preview"]` would keep holding
    # whichever per-process state was published last (a real, but now
    # frozen, search state), and the frontend's own "no new key step yet,
    # fall back to showing Live" rule (script.js's pollJob()) would keep
    # re-rendering that stale snapshot forever instead of simply leaving
    # the last key step on screen.
    if on_live_preview is not None:
        on_live_preview([])

    if best is None and accumulated_successes:
        # The `attempts` budget (200 paliers by default) ran out before
        # MIN_SUCCESSFUL_ATTEMPTS was ever reached — accept the single
        # success genuinely found rather than declaring a total failure,
        # at the user's explicit request (rely on the existing budget as
        # the only cap, rather than adding a new one). Can only ever hold
        # exactly one entry here: with >= MIN_SUCCESSFUL_ATTEMPTS the
        # palier loop above would already have picked a winner and
        # `break`-ed, leaving `best` no longer `None`.
        best, best_result, best_diag = accumulated_successes[0]

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

    def _final_minimize():
        progress(
            "minimizing",
            examples=[{
                "example_grid": build_letters_grid(rows, cols, best_slots, best_assignment),
                "impossible_cells": [],
                "deadlock_cells": [],
                "excluded_cells": [],
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
        return minimize_black_squares(
            best, best_result, rows, cols, index, rng, cancel_event=cancel_event,
            proper_noun_words=proper_noun_words, max_proper_nouns=max_proper_nouns,
            non_gloss_words=non_gloss_words, max_non_gloss=max_non_gloss,
            priority_words=priority_words, permanent_locked_letters=permanent_locked_letters,
            permanent_black_cells=permanent_black_cells, challenge_words=challenge_words,
        )

    if best_minimized is not None:
        # Already optimized, in parallel with the other successes, while
        # picking the winner (the "minimizing" step was published then).
        grid, slots, assignment = best_minimized
    else:
        grid, slots, assignment = _final_minimize()
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
