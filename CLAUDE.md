# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

**CrossWordFalcon** — a crossword grid generator (French, English, German, Spanish, or
Italian), usable from the CLI or from a web UI backed by two FastAPI servers:

- `build_sentence_corpus.py` — one-off preprocessing script: downloads a partial
  chunk (`--max-bytes`, default 50MB per source) of five OPUS (opus.nlpl.eu) corpora —
  OpenSubtitles (colloquial/dialogue vocabulary), Wikipedia (formal/technical
  vocabulary, and rare-but-real words dialogue rarely uses), Books (literary/
  narrative prose, mostly older translated novels — a third, descriptive-vocabulary
  register neither dialogue nor encyclopedic text tends to use; added at the user's
  explicit request), TED2013 (TED talk transcripts — a fourth register, spoken
  but prepared/explanatory rather than casual back-and-forth dialogue, closer to how
  someone actually explains something aloud to a broad audience; added at the user's
  explicit request), and CCMatrix (large-scale bitext mined from CommonCrawl — a
  fifth register, contemporary general-purpose written web text (news, blogs,
  product/service descriptions, forum posts) covering a far broader mix of topics
  and vocabulary than any of the other four alone; its full per-language file dwarfs
  every other source's, 10-37GB depending on the language, verified live, but
  `--max-bytes`'s partial-download-via-HTTP-Range mechanism is unaffected by a
  source's total size either way; added at the user's explicit request) — per
  language, merges
  them, keeps only sentences between `MIN_WORDS_PER_SENTENCE` (5) and
  `MAX_WORDS_PER_SENTENCE` (50) words — the lower bound added at the user's explicit
  request, dropping fragments too thin to carry real meaning ("Oui.", "Ça va ?", a lone
  name) as either a grounding example or a word-frequency data point; OpenSubtitles
  (dialogue-heavy, lots of short exchanges) lost roughly half its candidate sentences
  to this, Wikipedia/Books/TED2013 far less (~5-10%) — and filters out sentences likely
  to contain a wrong-language part: dropped if
  either a contiguous run of `MAX_INVALID_RUN` (3+) words the language's own Hunspell
  dictionary doesn't recognize, or too high an overall fraction of them
  (`MAX_INVALID_WORD_FRACTION`), calibrated by hand against real contamination (English
  dialogue/quotes leaking into every language's file) vs. genuine sentences with a
  proper noun or two. Output: `data/reference_corpus/<lang>_sentences.txt` (gitignored
  — generated, not source content). Used two ways: `build_wordlist_freq.py` counts
  word occurrences in it (`_count_word_frequencies`); `backend/example_sentences.py`
  looks up real usage examples of a specific inflected form in it (see
  backend/clues.py). Each source's own raw (pre-language-filter) sentences are also
  cached under `CORPUS/` (project root, gitignored — added at the user's explicit
  request, "keep the corpora for a possible later reprocessing"): a source already
  cached there is read from disk instead of re-downloaded, so a future change to the
  filtering strategy, the scoring, or an added source doesn't need to re-fetch
  multi-hundred-thousand-line partial downloads from opus.nlpl.eu every single time —
  distinct from `data/reference_corpus/`, which stays the final, filtered-and-merged
  output the rest of the pipeline actually reads. Verified live: confirmed the OPUS
  Books URL pattern (`https://object.pouta.csc.fi/OPUS-Books/v1/mono/{lang}.txt.gz`)
  resolves (HTTP 200) for all 5 supported languages before wiring it in, then ran a
  small (`--max-bytes 2000000`) smoke test end-to-end for Italian — confirmed the
  Books source downloads and merges correctly, and that a second run reuses all 3
  cached raw sources (`CORPUS/it_*.txt`) instead of re-downloading (~1.25s vs. the
  first run's real network time) — before deleting that tiny smoke-test cache and
  launching the real, full-scale reprocessing for all 5 languages. TED2013 (TED talk
  transcripts) was added the same way later: confirmed the OPUS TED2013 URL pattern
  (`https://object.pouta.csc.fi/OPUS-TED2013/v1.1/mono/{lang}.txt.gz` — note the
  `v1.1` version, unlike Books' plain `v1`) resolves for all 5 languages first, then
  ran the same small Italian smoke test (confirmed TED2013 downloads/merges
  correctly, and that a second run reuses all 4 cached raw sources, `CORPUS/it_*.txt`,
  this time) before deleting that cache and launching the real, full-scale
  reprocessing for all 5 languages — `build_wordlist_freq.py` and
  `build_gloss_dictionary.py` (both below) rebuilt for every language too, per rule 6
  of the project-best-practices SKILL (the corpus source list changed, so the entire
  downstream pipeline is recomputed, not just this first stage). CCMatrix was added
  the same way again later: confirmed the OPUS CCMatrix URL pattern
  (`https://object.pouta.csc.fi/OPUS-CCMatrix/v1/mono/{lang}.txt.gz`) resolves for
  all 5 languages first (also checked each language's full file size directly —
  10-37GB, by far the largest source in this pipeline, since CCMatrix is mined at
  CommonCrawl scale — confirming the `--max-bytes` partial-download mechanism was
  the right call even more so here than for the smaller sources), ran the same small
  Italian smoke test (confirmed CCMatrix downloads/merges correctly — 56,848
  candidate sentences from just a 2MB partial download, denser than any other single
  source at that same byte budget — and that a second run reuses all 5 cached raw
  sources) before deleting that cache and launching the real, full-scale
  reprocessing for all 5 languages, again rebuilding `build_wordlist_freq.py` and
  `build_gloss_dictionary.py` for every language per rule 6.
- `build_wordlist_freq.py` — one-off preprocessing script that reads a language's
  reference corpus (`data/reference_corpus/<lang>_sentences.txt`, counting word
  occurrences itself — `_count_word_frequencies`) and writes
  `data/wordlist_<lang>_full.tsv`, a `MOT<TAB>ACCENTUE<TAB>FREQUENCE<TAB>CANONIQUE`
  word list ready for the generator — the accented/inflected column preserves the
  word's natural spelling (gender, number, conjugation) alongside the grid's bare
  accent-stripped uppercase form, for `backend/clues.py` to use when writing clues.
  Used for all five languages (fr/en/de/es/it), so they're all built the same way
  from the same kind of source — no language gets special treatment. There's no
  separate persisted frequency-table file anymore (`data/raw/` and the script that
  used to write it, `compute_word_frequencies.py`, were both retired) — that hand-off
  file only existed for a since-removed HermitDave-based version of this pipeline,
  and once the corpus became this project's own, keeping a distinct on-disk stage
  between two scripts served no purpose. The source corpus isn't purely monolingual
  even after `build_sentence_corpus.py`'s own filtering, so every candidate is
  additionally validated against a real dictionary of its own language: a Hunspell
  dictionary
  (downloaded from LibreOffice/dictionaries, cached in `data/hunspell_cache/`,
  gitignored) is queried with the `hunspell` command-line spellchecker itself (`brew
  install hunspell` / `apt-get install hunspell`) — checking both the word's corpus
  spelling and a title-cased variant, since the corpus lowercases everything but
  languages like German require every noun capitalized. A candidate not recognized
  either way is dropped as noise/contamination; one that's only valid title-cased
  (e.g. German `haus`) is kept with its correct capitalization as the `ACCENTED` value
  (`Haus`), not the corpus's lowercase spelling. Deliberately not `unmunch` (which
  pre-expands a dictionary into every surface form): verified by hand that it silently
  drops many irregular verb conjugations (French être/avoir/vouloir — `SUIS`, `ÉTAIT`,
  `VEUX`...) that `hunspell`'s own spellcheck engine correctly recognizes. `CANONIQUE`
  is every candidate canonical form/lemma Hunspell's morphological analysis
  (`hunspell -m`) finds for the word (semicolon-separated when more than one — a word
  can be genuinely ambiguous between stems, e.g. French `SUIS` -> `suivre;être`) — used
  both to blend 90% the most-frequent candidate's own frequency into `FREQUENCE`
  (correcting subtitle/dialogue-frequency distortion, e.g. French `déterminées` ranked
  far below its infinitive `déterminer` despite being an equally easy word) and, kept
  in full, so `backend/gloss_lookup.py` can look up a real definition by lemma without
  the ambiguity being silently resolved at dictionary-build time. A likely proper noun
  (person/place/brand name) has its final `FREQUENCE` multiplied by
  `PROPER_NOUN_SCORE_FACTOR` (0.5), at the user's explicit request after "easy"
  difficulty grids came out with too many proper nouns (see `backend/crossword_gen.py`'s
  `DIFFICULTY_PRESETS`, which caps "easy" to the globally-highest-scored words —
  demoting a proper noun's score is what actually keeps it out of that cutoff more
  often, not a hard exclusion, so a genuinely very common name can still qualify).
  Detection reuses a signal `_spellcheck_valid` already computes for every candidate
  rather than adding real named-entity recognition: in French/English/Spanish/Italian
  (`PROPER_NOUN_LANGS` — every `HUNSPELL_SOURCE` language except German), an ordinary
  word is normally written lowercase in running text, so a candidate Hunspell only
  recognized once title-cased is overwhelmingly likely to be a name that happened to
  appear lowercased in the corpus, not a common word. Deliberately excludes German:
  every German noun, common or proper, requires capitalization, so the exact same
  "needed title-case" signal that identifies a likely proper noun in the other four
  languages would just flag ordinary nouns (`Haus`) there — a discussion the user's
  own request surfaced directly, since a blanket rule across all 5 languages would
  have been wrong for German specifically. Verified live: ran the updated script
  end-to-end for Italian (smoke-test-scale corpus) — 3,340 of 62,247 words were
  flagged and scored at 50%, spot-checked a sample of the flagged words directly in
  the output TSV (`ANNA`, `LOMBARDIA`, `ROMA`, `ITALIA`, `GIORGIO`, `GERMANIA`,
  `MARIA`, `FRANCIA`, `CAMPANIA` — all genuine proper nouns, no false positives
  spotted in the sample) — before deleting that smoke-test output and re-running for
  real as part of the full 5-language reprocessing.
- `backend/crossword_gen.py` — the core grid generator library/CLI (all the grid-generation
  business logic lives in `backend/`). `generate_grid()` is the reusable entry point (used
  by both the CLI `main()` and `backend/app.py`); it takes a word list and produces a
  filled, symmetric crossword grid of `width`×`height` cells (15×10 by default —
  independent dimensions, not necessarily square). Takes an optional `on_progress(step,
  **data)` callback, invoked at each of the pipeline's real phase transitions (a pattern
  attempt, a successful fill, minimizing, the finished grid) — `None` by default (a no-op)
  so the CLI and any other caller that doesn't care about progress needs no change;
  `backend/app.py` is the only caller that passes one. A failed pattern attempt
  (black-square placement produced a grid the CSP solver couldn't fill) is logged with
  real diagnostics, not just "it failed" — `try_fill`'s optional `diagnostics` dict
  records `slot_count`/`length_counts` (the pattern's shape) and `checks`/`reason`
  (how the search actually ended: `"deadline_exceeded"` — the `200_000`-check budget
  ran out, inconclusive; `"search_exhausted"` — every candidate was tried and none
  worked, a genuine dead end for that specific pattern; `"no_slots"` — no white run
  ≥3 cells at all) — added after a report of "no fillable grid found" with all 40
  attempts finishing in ~150ms total, far faster than a real CSP search (confirmed
  by direct testing: a genuine `deadline_exceeded`/`search_exhausted` failure takes
  seconds, not milliseconds) — a sign worth being able to see directly in
  `backend.log` next time instead of re-deriving it by hand. `generate_grid()` also
  logs the loaded word list's `length_counts` once per request (`wordlist_loaded`)
  so a `require_gloss`/`max_words` combination that starves a specific slot length
  down to very few (or zero) words is visible without guessing. On failure,
  `diagnostics` also carries `example_grid` — a letter/black-cell snapshot
  (`build_partial_letters_grid`) of whichever point during the search had the most
  slots simultaneously filled in (`Filler.best_assignment`, a lightweight, purely
  diagnostic high-water-mark tracked in `_backtrack` — this is *not* the retry/patch
  mechanism explored and fully reverted earlier in this project's history, just a
  snapshot, no behavior change), regardless of exactly where the search eventually
  gave up — added at the user's explicit request so a failed attempt has some visible
  trace of what was tried instead of vanishing outright. `generate_grid()`'s
  `progress("pattern_attempt_failed", ...)` call already spreads every diagnostics key
  (including this one) into the event, and `progress("pattern_failed", ...,
  last_attempt=last_diag)` nests it the same way on total failure — reaching
  `job["step"]` in `backend/app.py` either way with no further backend wiring needed
  for logging. Surfacing it to the *polled* API needed one more step, though: a bug
  reported live (the frontend preview never actually appeared) traced back to
  `job["step"]` being fully overwritten by every single progress event, including the
  very next one after a failure (e.g. the next palier's plain "pattern" step, which
  carries no `example_grid` of its own) — a client polling every
  `POLL_INTERVAL_MS` (`frontend/static/script.js`) could easily poll right past that
  one-event-wide window and never see it at all. Fixed by persisting it separately as
  `job["last_example_grid"]`, updated only when a *new* one actually arrives (checking
  both the direct and `last_attempt`-nested shapes) and otherwise left untouched, so
  the polled API's `last_example_grid` field always reflects the most recent one
  regardless of exactly when a client happens to poll. Verified live against the real
  running backend, restarted to pick up the fix: polled a real failing generation job
  repeatedly and confirmed `last_example_grid` turned non-null at one poll and then
  stayed that way on every subsequent poll (20/20), unlike the old
  `job["step"]`-only version, which — confirmed separately by direct testing — could
  show it on one poll and lose it on the very next. `diagnostics` also carries
  `impossible_cells` on failure — the cells of whichever unassigned slot(s), at that
  same `best_assignment` snapshot, had an empty domain (no candidate word fit at all,
  given the letters already fixed by assigned crossing slots), computed by
  `Filler.impossible_zone_cells()`. Can legitimately be empty: the snapshot's own
  high-water-mark point isn't necessarily where the search finally gave up (e.g. a
  `deadline_exceeded` failure can occur with every domain at that point still
  non-empty, just not resolved in time), so absence of any impossible cell there
  doesn't mean anything is wrong. Persisted through the same `job["last_example_grid"]`
  mechanism in `backend/app.py` (as `job["last_impossible_cells"]`, sharing a small
  `_latest()` helper rather than duplicating the direct-vs-`last_attempt`-nested lookup
  a second time), at the user's explicit request, to highlight those specific cells in
  the web UI. `build_partial_letters_grid` also takes the same `forced_letters` dict
  (see `sample_letter_biases` above) and overlays it onto `example_grid` for any cell no
  real assignment already covers, returning `(letters_grid, forced_cells)` — the latter
  becomes `diagnostics["forced_cells"]`, persisted the same way as the other two
  (`job["last_forced_cells"]`) — at the user's explicit request, so the preview also
  shows *which* letters are statistical hints rather than real search progress.
  `forced_cells` lists *every* cell `forced_letters` ever set, not only the ones still
  showing the guessed letter (a covered cell shows its real, confirmed letter instead) —
  this replaced an initial version that only reported the still-unconfirmed subset, after
  a live report that doing so made the preview's highlight nearly disappear as a
  generation progressed: `sample_letter_biases` itself kept producing a stable 6-7 forced
  cells per attempt across every black-cell ratio tested, but the "still-unconfirmed"
  filter alone could and did legitimately reach zero once a search made enough real
  progress to cover most of them — a correct computation, but a confusing signal, since
  it looked indistinguishable from a broken pre-fill.

  This same `example_grid`/`impossible_cells`/`forced_cells` mechanism is no longer
  limited to *failed* attempts — at the user's explicit request, `generate_grid()`
  now also emits a preview right at the start of minimization, just before calling
  `minimize_black_squares`, showing the successful pattern **without any letters** —
  the same visual style as a failed attempt's own preview, reusing the exact same
  rendering path client-side with no frontend change needed. This works for free
  because `best` (the winning pattern from the ratio-search loop) is already the
  plain black/white grid — letters are never written into it at all, they live
  separately in `best_result`'s `(slots, assignment)` — so there's nothing to strip
  out, just a defensive copy (`[row[:] for row in best]`) to hand to `progress()`,
  since `minimize_black_squares` receives and mutates that same `best` object
  immediately afterward; passing the reference directly would have let the "before
  optimization" preview silently keep changing as cells got removed, instead of
  staying frozen on the true starting state. `impossible_cells`/`forced_cells` are
  passed as empty lists explicitly (not omitted) specifically to clear out whatever
  a *prior* failed pattern attempt during the search phase may have left in the
  preview — omitting them would have left those stale overlays visually attached to
  this brand new, successful pattern, which has neither. `frontend/static/i18n.js`'s
  `attemptPreviewLabel` was reworded in all 5 languages to also mention
  "optimizing"/"en optimisation" alongside "in progress"/"failed", since the same
  label now legitimately covers this third case too. Verified live: polled a real
  generation job through to completion and confirmed the preview was already
  populated by the time the job reached the `minimizing` step, containing only
  `.`/`#` characters (no letters at all) — matching the raw pattern exactly — with
  no impossible cells/forced cells overlaid. See `frontend/static/script.js`'s
  `renderAttemptPreview()` (style-guide SKILL) for how the web UI displays this.

  This "no letters at all" design was later reversed, at the user's own
  explicit follow-up request, once the web UI gained a dedicated
  letter-visibility toggle for the whole attempt-preview mechanism
  (`showPreviewLetters`, see `style-guide` SKILL): `example_grid` for this
  specific `"minimizing"` preview is now built with
  `build_letters_grid(rows, cols, best_slots, best_assignment)` (the same
  function used for `result["solution"]` further below) instead of a bare
  copy of `best`'s own black/white pattern — `best_result` is unpacked as
  `best_slots, best_assignment = best_result` right before this call.
  `build_letters_grid` always returns a brand-new grid rather than
  modifying its inputs in place, so the earlier defensive-copy reasoning
  (`[row[:] for row in best]`, needed only because `minimize_black_squares`
  mutates `best` itself immediately afterward) no longer applies here.
  This needed no frontend change at all: `renderAttemptPreview()` already
  hides/reveals whatever letters are embedded in any `example_grid` it's
  given, uniformly across every step that can populate one — only the
  backend needed to start actually including them for this one step.
  Verified live: a real `generate_grid()` run (9×9, `difficulty="hard"`,
  seed 3) with `on_progress` capturing the `"minimizing"` event directly
  confirmed every white cell now carries a real letter (0 remaining `.`
  placeholders, `impossible_cells`/`forced_cells`/`locked_cells` all still
  correctly empty) and that every one of those letters matches the
  eventual `result["solution"]` at the same position (0 mismatches) — the
  preview's letters are genuine, not placeholder/stale data.

  Extended twice more, both at the user's explicit request. First: instead of a
  single failed-attempt example, the preview now shows up to `FAILED_ATTEMPT_
  EXAMPLES` (6) distinct examples, laid out 2 rows of 3 — one per parallel attempt
  at the same palier (`PARALLEL_ATTEMPTS`, 10 by default) rather than an arbitrary
  single one (previously always `outcomes[-1]`, the last of the batch, with no
  particular reason to prefer it over any other). `generate_grid()`'s progress
  events were generalized from three flat kwargs (`example_grid`/`impossible_cells`/
  `forced_cells`) to a single `examples` list of `{example_grid, impossible_cells,
  forced_cells}` dicts — one element for the "minimizing" preview above (and the
  "clues" preview below), up to `FAILED_ATTEMPT_EXAMPLES` for
  `pattern_attempt_failed`/`pattern_failed`, sorted by real progress (see further
  below for that sorting refinement) — so
  `backend/app.py` and the frontend have a single, uniform mechanism to handle
  regardless of whether 1 or 6 grids are being shown. `backend/app.py`'s job dict
  gained a single `last_examples` list (replacing the earlier `last_example_grid`/
  `last_impossible_cells`/`last_forced_cells` trio), updated the same way as before
  (only when a fresh, non-empty `examples` list actually arrives in a progress
  event, so a client polling `GET /api/generate/status/{job_id}` never sees it blink
  away between polls). `frontend/static/script.js`'s `renderAttemptPreview(examples)`
  now takes that list directly and builds one `.attempt-preview-grid` mini-grid per
  entry inside a new `#attempt-preview-grids` wrapper (`frontend/static/index.html`),
  laid out as a fixed 3-column CSS grid (`style-guide` SKILL) so 6 examples always
  read as 2 full rows of 3 regardless of viewport width, rather than however many
  happen to fit per row at a given window size. `frontend/static/i18n.js`'s
  `attemptPreviewLabel` reworded again, this time to plural ("dernières
  tentatives"/"latest attempts"/etc.) in all 5 languages, since it can now show
  several grids at once. Verified live: a deterministic test that monkeypatches
  `_pattern_attempt` (and `ProcessPoolExecutor` with a synchronous stand-in, so no
  real multiprocessing/CSP search is needed) to always fail confirmed every
  `pattern_attempt_failed` event carries exactly `FAILED_ATTEMPT_EXAMPLES` (6)
  examples, each with the correct grid dimensions and diagnostics, and that the
  final `pattern_failed` event carries the last palier's own 6 examples too; a real
  JS syntax check (temporarily `pip install`ed `esprima`, removed again afterward —
  see the same pattern used elsewhere in this project) confirmed `script.js`/
  `i18n.js` still parse correctly after the rewrite.

  Second: a **cumulative count of every grid that has actually failed since the
  start of generation** (`total_attempts`), shown in the status message during
  pattern search, at the user's explicit request — without it, the UI only ever
  showed the current *palier* number (e.g. "attempt 3/40"), with no visibility into
  how many of the `PARALLEL_ATTEMPTS` (10 by default) grids-per-palier had actually
  been generated and rejected so far. `generate_grid()` tracks
  `total_attempts_tried`, incremented by `len(outcomes) - len(successes)` after
  every palier completes — first written as `len(outcomes)` unconditionally (every
  parallel attempt, success or failure alike), then corrected at the user's own
  explicit follow-up request once it was pointed out that this over-counted: the
  winning palier can have more than one of its `PARALLEL_ATTEMPTS` attempts
  actually succeed, and those aren't failed grids — the counter must reflect only
  genuinely rejected attempts, not the raw number of attempts launched. Threaded
  into the `pattern`/`pattern_found`/`pattern_attempt_failed`/`pattern_failed`
  progress events. `frontend/static/script.js`'s `describeStep()` passes it through
  to `i18n.js`'s `statusPattern`/`statusPatternFound`, both extended with a third
  parameter and worded in all 5 languages around "failed" (e.g. "tentative 3/40, 20
  grilles échouées jusqu'ici…" — reworded from an initial "grilles générées"/"grids
  generated" phrasing to match the corrected, failures-only semantics). Verified
  live: the monkeypatched deterministic all-failure test confirmed `total_attempts`
  reads `0, 10, 20` across the three successive `pattern` events (before each
  palier runs) and `10, 20, 30` across the three `pattern_attempt_failed` events
  (after each palier's 10 parallel attempts complete, all of which fail in that
  test), matching `PARALLEL_ATTEMPTS × palier count` exactly; a second,
  mixed-outcome test (2 paliers of 10 failures each, then a third palier with 2
  successes and 8 failures among its 10 parallel attempts, still exercising the
  real `minimize_black_squares`/`build_word_entries` pipeline on a hand-built
  passing grid rather than mocking those too) confirmed the final `pattern_found`
  event reports `total_attempts=28` (20 already-failed plus 8 more from the
  winning palier), not `30` — proving the fix actually excludes that palier's own
  successes from the tally rather than just not regressing the all-failure case.

  Third: the up-to-6 failed-attempt examples above are now **sorted by real
  progress, most-advanced first**, at the user's explicit request — previously
  shown in whatever order the `PARALLEL_ATTEMPTS` futures happened to be
  submitted in, with no relationship to which attempt actually got furthest.
  `build_partial_letters_grid` (already computing, per failed attempt, which
  cells a *real* assignment covers — as opposed to `forced_letters`'s purely
  statistical overlay, see its own docstring) now also returns that count
  (`len(covered)`), surfaced as `try_fill`'s `diagnostics["assigned_letter_count"]`
  (`0` for the separate `no_slots` failure branch, which has no assignment at
  all). `generate_grid()` sorts `failed_diags` by this count, descending, before
  truncating to `FAILED_ATTEMPT_EXAMPLES` — so the first of the 6 shown grids (and
  `last_diag`, reused for `backend.log`'s own detailed failure fields) is now
  whichever of the palier's parallel attempts placed the most real letters, not an
  arbitrary one. Verified live: a deterministic test (the same monkeypatched
  `_pattern_attempt`/`ProcessPoolExecutor` stand-in as above) fed 10 attempts per
  palier with deliberately non-monotonic, individually-tagged
  `assigned_letter_count` values (`5, 42, 13, 0, 27, 8, 19, 33, 2, 50`, tagged via
  a marker cell in each fake `example_grid` so the actual shown order could be
  traced back to its source count) — confirmed the 6 examples come back as
  exactly `50, 42, 33, 27, 19, 13` in that order, matching the true top 6 sorted
  descending, not the arbitrary submission order (which would have been
  `5, 42, 13, 0, 27, 8` — the first 6 by index — under the old code).

  Separately, at the user's explicit request: the same letters-free preview
  mechanism now also fires once more, right at the very start of clue generation
  (`backend/app.py`'s `progress("clues", current=0, total=len(result["words"]),
  examples=[...])`, just before the first call into `LLMClueGenerator.generate()`)
  — showing the final, already-minimized grid (`result["pattern"]`, needing no
  further stripping since letters live separately in `result["solution"]`) for the
  entire, often slow (`backend/clues.py`'s own per-word LLM round-trips) clue-
  generation phase, not just during pattern search/minimization. `impossible_cells`/
  `forced_cells` are passed empty, same reasoning as the "minimizing" preview: a
  fully solved, minimized grid has neither. Verified live: submitted a real small
  (6×6) generation job against the running backend and LLM server, polled it to
  completion, and confirmed `last_examples` was already populated with exactly one
  grid — containing only `.`/`#` characters, matching `result["pattern"]` — by the
  time the job reached the `clues` step, and stayed populated through to `done`.

  A "Continuer" button was added to the web UI, at the user's explicit
  request: "Quand tout le processus échoue (200 cycles par défaut) afficher
  un bouton 'Continuer' qui permet de relancer 200 cycles à partir du
  dernier état." Previously, once `generate_grid()` exhausted every one of
  its `attempts` (200 by default) paliers without ever finding a fillable
  grid, the only option was to start an entirely new generation from a
  blank grid, discarding every bit of progress the cross-palier retry
  mechanism (`carry_seed_grid`/`carry_locked_letters`/`carry_preseed_
  assignment`/`carry_excluded_slots`, see the whole history above) had
  built up. `generate_grid()` gained a new `resume_state=None` parameter
  (no effect for any pre-existing caller, including the CLI): if given, it
  seeds those same four `carry_*` variables from a previous, failed run's
  own final state instead of starting them all at `None` — the very first
  palier of this new call picks up exactly where the failed run left off,
  with a fresh full budget of `attempts` paliers (`consecutive_continue_
  paliers` deliberately still starts at 0, its own fresh 50-consecutive-
  continue budget, rather than carrying over wherever the previous run's
  counter happened to be). Two small helpers, `_serialize_resume_state`/
  `_deserialize_resume_state`, convert the four `carry_*` variables to and
  from a JSON-safe shape — needed because `carry_locked_letters`'s native
  form (`{(row, col): letter}`, tuple keys) and `carry_excluded_slots`'s
  native form (a `set`) are both invalid JSON, encoded instead as a flat
  `[[row, col, letter], ...]` list and a sorted list respectively; `None`
  is preserved as `None` rather than collapsed into an empty list/dict for
  either field, since `generate_grid`'s own palier loop tells its two
  mutually-exclusive resume mechanisms apart by exactly this None-vs-set
  distinction (`if carry_preseed_assignment is not None:` dispatches to
  `_pattern_continue`, else to `_pattern_attempt`) — collapsing that
  distinction during serialization would silently corrupt which mechanism
  a resumed run starts from. On total failure (`best is None`), `generate_
  grid()` now builds this resume state (guarded by `carry_seed_grid is not
  None`, always true from the very first failed palier onward, so this
  never applies in practice — the one exception, `attempts=0`, is never
  used by any real caller) and passes it as a new `resume_state=...` kwarg
  on the existing `progress("pattern_failed", ...)` event, alongside
  `last_attempt`/`examples`/`total_attempts`.

  `backend/app.py` persists this on the job (`job["resume_state"]`, set
  from inside the existing `progress()` closure whenever a `"pattern_
  failed"` event carries one — a plain unconditional assignment, unlike
  `examples_history`'s own append-across-many-events pattern, since a job
  only ever emits this once) alongside the original request's own
  parameters (`job["request"]`, `req.model_dump()` — a plain JSON-safe
  dict, not the pydantic instance itself, set once at the very start of
  `_run_generate_job`). A new `POST /api/generate/continue/{job_id}`
  endpoint reads both back, rebuilds an equivalent `GenerateRequest`,
  re-validates it (`_validate_generate_request`, factored out of `POST
  /api/generate`'s own inline checks so both endpoints share exactly the
  same validation rather than risking the two drifting apart), and starts
  a **new** job (its own fresh `job_id`, not a continuation of the old
  one's own record — the failed job stays exactly as it was, still
  inspectable) via `_run_generate_job(new_job_id, req, resume_state=job
  ["resume_state"])`. Rejects with a 400 if the target job has no
  `resume_state`/`request` (never failed this way, or never existed with
  that shape), a 404 if the job_id itself is unknown — mirroring `POST
  /api/generate/cancel/{job_id}`'s own error-handling conventions.

  The web UI (`frontend/static/script.js`) factored its own submit
  handler's body into a shared `runGeneration(startJob)` function — at the
  user's implicit requirement that "Continuer" behave identically to a
  fresh generation from here on (same button-state setup, polling,
  rendering, error handling), rather than a parallel, easily-drifting code
  path — with `startJob(t)` as the one part that differs between a form
  submission (`POST /api/generate`) and a "Continuer" click (`POST /api/
  generate/continue/{job_id}`). A new `GenerationFailedError` (extends
  `Error`, carries `jobId`/`errorCode`) replaces the plain `Error` `pollJob()`
  used to throw on `data.status === "error"`, so `runGeneration()`'s catch
  block can tell *which* job just failed and *why* — needed to decide
  whether to reveal `#continue-btn` at all: only ever shown for `error_code
  === "no_fillable_grid"` (the one failure with a real resume state to
  offer), left hidden for any other failure (a lost connection, an
  internal error, clue generation failing), exactly as before this
  feature. `#continue-btn` stores the failed job's id in its own `dataset.
  jobId`, updated again on every subsequent failure — so clicking
  "Continuer" repeatedly chains from whichever job most recently failed,
  not always the original one. See the `style-guide` SKILL for the
  button's own visual treatment (no dedicated CSS — inherits the shared
  accent-blue look, unlike `#stop-btn`'s deliberate red).

  Verified live, not just reasoned about: an isolated round-trip test of
  `_serialize_resume_state`/`_deserialize_resume_state` confirmed `None`
  stays distinct from an empty list/set through the JSON boundary for both
  mutually-exclusive resume shapes (the "nettoyage" shape with
  `locked_letters` set, the "continue" shape with `preseed_assignment`/
  `excluded_slots` set) and that the result is genuinely JSON-serializable.
  A direct, real `generate_grid()` test forced a quick total failure
  (`attempts=2` on the standard 15×10 easy benchmark, seed 2 — nowhere near
  enough to normally succeed) and captured its `resume_state`; round-tripped
  it through `json.dumps`/`json.loads` (exactly as it would travel through
  the real job dict); then called `generate_grid()` again with a full
  `attempts=200` budget, a *different* seed (99), and that resume state —
  succeeded, `0` mismatches, `0` empty white cells, confirming a resumed
  run can succeed using state carried from an entirely different RNG
  stream than the one that produced it. A second, more targeted check
  called `_pattern_continue` directly on the deserialized resume state and
  confirmed the number of already-assigned words only ever grows (never
  drops) from one continuation attempt to the next, proving the resumed
  attempt genuinely builds on the carried-forward content rather than
  silently starting fresh. Finally, the real `POST /api/generate/continue/
  {job_id}` endpoint itself was verified end-to-end at the HTTP/ASGI level
  (`httpx.ASGITransport` driving the real FastAPI app in-process, `backend.
  app.generate_grid` monkeypatched to a fast stub so the test didn't need
  to wait out a real 200-palier failure): a first job forced to fail
  showed `resume_state`/`request` correctly populated on `GET /api/
  generate/status/{job_id}`; `POST /api/generate/continue/{job_id}`
  returned 202 with a distinct new `job_id`; the stub's second call
  received exactly the first call's own `resume_state`, byte-for-byte;
  continuing a job with no resume state returned 400, and continuing an
  unknown job_id returned 404. **Not yet verified**: the actual visual
  appearance and click behavior of `#continue-btn` in a real browser — the
  same tooling limitation already noted for other UI work this session.
- `backend/app.py` — **back** FastAPI server: exposes `generate_grid()` as a JSON API
  via a relative import (`from .crossword_gen import ...`). No static files, no
  `/docs`/`/openapi.json` (disabled) — any other path 404s by default. The request's
  `language` field (`fr`/`en`/`de`/`es`/`it`) selects the wordlist via the `WORDLISTS`
  dict — add a language by adding an entry there plus a
  `data/wordlist_<code>_full.tsv`. Generation is asynchronous, not a single blocking
  call: `POST /api/generate` validates the request, starts the work as a background
  `asyncio` task (`_run_generate_job`, itself running the CPU-bound
  `generate_grid()`/clue-generation calls via `asyncio.to_thread` so the event loop
  stays free), and immediately returns `{"job_id": ...}` (HTTP 202) — the client then
  polls `GET /api/generate/status/{job_id}` for progress and the eventual result.
  `JOBS` is a plain in-memory dict (one uvicorn process, no `--workers`, see
  `run_Falcon.sh` — no locking or external store needed), bounded to `MAX_JOBS` (50)
  entries so a long-running process doesn't grow it forever; each background task is
  kept in `_BACKGROUND_TASKS` purely so `asyncio.create_task`'s result isn't
  garbage-collected mid-run (a documented asyncio footgun — it only holds a weak
  reference otherwise). Every generation step calls a shared `progress(step, **data)`
  closure that both logs to `backend.log` via the standard `logging` module (captured
  by uvicorn's stdout redirect, see `run_Falcon.sh`) and updates the job's `step` field
  for the next status poll to pick up — see `generate_grid()`'s and
  `LLMClueGenerator.generate()`'s `on_progress` parameter. After a grid and its clues
  are both ready, it calls `backend/svg_export.py` to save a durable copy to `GRID_SVG/`,
  then a PNG rendering of that same SVG to `GRID_PNG/` (`save_grid_png`, via
  `rsvg-convert`), before marking the job done; a failure to save either is logged but
  never fails the request — it's a nice-to-have record, not the point of asking for a
  grid. If the LLM
  call fails, the job ends in `status: "error"` (the old direct 502 doesn't apply
  anymore now that the request itself always returns 202 immediately). `GET
  /api/system_info` returns `backend/system_info.py`'s `get_system_info(clue_
  generator.model)` — computed fresh per request (a couple of cheap subprocess
  probes, not worth caching) rather than once at startup, since the info badge this
  feeds (`frontend/static/script.js`) is a nice-to-have status display that could
  plausibly reflect a hardware change (e.g. a hot-swapped external GPU) across a
  long-running server process's lifetime.

  The "clues" step's own preview (shown right as clue generation starts —
  see `crossword_gen.py`'s matching "minimizing" preview for the earlier
  half of this same story) now shows real letters too, at the user's
  explicit follow-up request, once a dedicated letter-visibility toggle
  existed on the web UI to gate them (`showPreviewLetters`, see
  `style-guide` SKILL): `example_grid` is built from `result["solution"]`
  (already a full letters grid via `build_letters_grid` — every white
  cell has its real letter, no `.` placeholders) instead of
  `result["pattern"]` (the bare black/white pattern this preview
  originally showed on purpose). No frontend change needed for this
  specific request: `renderAttemptPreview()` already hides/reveals
  whatever letters are embedded in any `example_grid` it's handed,
  uniformly across every step that can populate one.

  A "Stop" button was added to the web UI, at the user's explicit
  request, to interrupt a generation in progress "quelle que soit
  l'étape" (whatever the current phase). `CANCEL_EVENTS` (job_id ->
  `threading.Event`) is a *separate* module-level dict from `JOBS`
  itself — deliberately not folded into the `JOBS[job_id]` entry, since a
  `threading.Event` isn't JSON-serializable and `GET /api/generate/status/
  {job_id}` returns that entry directly (`return job`); putting the event
  there would break that response the moment a client polled a running
  job. `_new_job()` creates one alongside every new job and evicts it in
  lockstep with `JOBS`'s own `MAX_JOBS` bound, so it never outlives its
  own job. A new `POST /api/generate/cancel/{job_id}` endpoint just calls
  `.set()` on the job's event — a pure signal, never a forced kill of the
  background task or its worker processes (see `crossword_gen.
  GenerationCancelled`'s own docstring for why: only a *cooperative*
  checkpoint mechanism was built, not process termination). `_run_generate_
  job` passes this same event to both `generate_grid()` (as `cancel_event`)
  and `clue_generator.generate()` (also `cancel_event`) and catches
  `GenerationCancelled` as a *third* distinct job outcome — `status:
  "cancelled"` — never folded into the existing `"error"` status, since a
  user-requested stop isn't a failure and the web UI deliberately shows it
  without the error styling (see `frontend/static/script.js`'s
  `CancelledError`). Verified live against the real running API: started a
  real generation (22×18, easy), confirmed its status was `"running"`
  mid-pattern-search, called the cancel endpoint, and confirmed a
  subsequent poll showed `status: "cancelled"` with the expected diagnostic
  detail (`checks`, `assignment`, etc.) still present in `step` from
  wherever the cooperative checkpoint caught it.

  Reported next: "le bouton Stop ne s'applique pas rapidement. Prévoir
  l'arrêt dans toutes les phases." True at the time — the only checkpoints
  were *between* paliers (`generate_grid`'s own loop), inside
  `minimize_black_squares`'s removal loop, and *between words* in
  `LLMClueGenerator.generate()` — a single palier's own CSP search
  (`Filler._backtrack`, potentially hundreds of thousands of calls before
  hitting `deadline_checks`) had no checkpoint of its own at all, so "Stop"
  could sit unresponsive for however long that search happened to take.
  Fixed by giving `Filler` its own `cancel_event` parameter, checked once
  every `CANCEL_CHECK_INTERVAL` (500) calls to `_backtrack` — frequent
  enough to interrupt within a fraction of a second even on a
  fast-running search, infrequent enough that `multiprocessing.Event.
  is_set()`'s own small overhead is never paid at every single node.
  Threaded through `try_fill` → `_pattern_attempt`/`_pattern_continue` →
  `generate_grid`'s executor calls, and through to `minimize_black_
  squares`'s own `try_fill` call too (on top of its pre-existing per-
  candidate check, for full coverage inside that phase as well).

  This first version passed the event as a normal argument to
  `executor.submit(...)`, which immediately failed with a real,
  reproduced-live error: `RuntimeError: Condition objects should only be
  shared between processes through inheritance`. Root cause: each
  parallel attempt runs in its own OS process
  (`concurrent.futures.ProcessPoolExecutor`), and on macOS's default
  "spawn" start method, a freshly-spawned worker shares *no* memory with
  the parent — every argument passed to `submit()` is pickled and sent
  over a pipe, and a `multiprocessing.Event`/`Condition` is explicitly not
  meant to be reconstructed that way outside of the one supported channel:
  a pool's own `initializer`/`initargs`, evaluated once per worker at
  startup, the same mechanism already used to hand each worker its own
  copy of the word `index` (`_init_worker`/`_worker_index`). Fixed by
  passing `cancel_event` the same way — `_init_worker(index, cancel_event)`
  now also sets a `_worker_cancel_event` module global, and
  `_pattern_attempt`/`_pattern_continue` read that instead of receiving
  their own `cancel_event` parameter (removed again after this fix,
  since the initializer-based global replaces it entirely for these two
  functions specifically — `try_fill`/`Filler`/`minimize_black_squares`
  keep their own explicit parameter, since those run in the parent
  process/thread, where passing arguments normally is never a problem).
  Also switched `backend/app.py`'s `CANCEL_EVENTS[job_id]` from a plain
  `threading.Event()` to `multiprocessing.Event()` — a `threading.Event`
  has no meaning at all across a process boundary in the first place
  (pickling one into a worker silently reconstructs an independent,
  disconnected copy that never observes `.set()` calls made in the
  parent), so this swap was necessary regardless of the initializer fix.
  Verified live end to end through the real running API (not just in
  isolation): first confirmed the isolated mechanism directly — a hand-built
  hard-to-solve 10×10 grid with a `multiprocessing.Event` set from a
  background thread after a short delay correctly raised
  `GenerationCancelled` within milliseconds of that delay, well before
  either `deadline_checks` or a natural solve; then, after the
  initializer fix, started a real 22×18 generation through the actual
  API, called `POST /api/generate/cancel/{job_id}` ~2s in, and confirmed
  `status: "cancelled"` appeared on polling **0.79s** later — down from
  a worst case that could previously span an entire palier's own
  multi-second-to-multi-minute search, confirming the fix's whole point.
  A follow-up real generation (15×10, seed 2, no cancellation) confirmed
  no regression to the ordinary, successful case.

  Separately, `POST_PREFILL_BLACK_FRACTION` (`crossword_gen.py`, a fixed
  10% constant until now) became a web UI setting too, at the user's
  explicit request: a new "Ajout noires" selector (0/1/3/5/10%, 3% by
  default — `GenerateRequest.black_enrichment_percent`, validated against
  `BLACK_ENRICHMENT_PERCENTS`, mirroring `force_letters_percent`'s own
  existing pattern) converts to a fraction and passes straight through to
  `generate_grid(black_enrichment_fraction=...)`. Explicitly clarified by
  the user: this rate only ever applies to a palier that starts from a
  blank grid or from a cleanup (`_build_retry_seed`) — never to a
  "reprise telle-quelle" palier (`_pattern_continue`), which needed no
  code change to honor this, since that mechanism never calls
  `make_pattern` at all (see `crossword_gen.py`'s own docstring for
  `_pattern_continue`) and so can never add a black cell regardless of
  this setting. `FORCE_LETTERS_PERCENTS` was realigned from its previous
  `(0, 1, 2, 5, 10)` to the same `(0, 1, 3, 5, 10)` list, at the user's own
  further explicit request, so the web UI's two percentage selectors
  offer the same set of choices.

  Much later, both fixed-choice `<select>` fields were replaced by a
  free-text integer `<input>` each (`min="0" max="100" step="1">`), at the
  user's explicit request — `FORCE_LETTERS_PERCENTS`/
  `BLACK_ENRICHMENT_PERCENTS` deleted outright, `GenerateRequest`'s two
  fields switched from a manual "must be one of this tuple" check to a
  plain `Field(ge=0, le=100)` (matching how `width`/`height` are already
  validated). "Ajout noires" gained a client-side auto-fill,
  `updateDefaultBlackEnrichment()` in `script.js`: `round(0.3 * sqrt(width
  * height))`, recomputed on every width/height change, until the player
  edits the field themselves (`blackEnrichmentManuallyEdited`, flipped
  only by a genuine `input` event — a programmatic `.value` assignment
  never fires one, so the formula's own updates can't trip its own
  lockout). "Lettres forcées" keeps a plain static "0" default, no
  formula, per the user's own explicit distinction between the two
  fields. A new `.input-with-suffix`/`.input-suffix` CSS pair (see the
  style-guide SKILL) shows a plain "%" next to each field, no longer
  baked into an `<option>`'s own text. Verified live: a real `POST
  /api/generate` with `black_enrichment_percent=4` returned 202; `150` and
  `-1` were both correctly rejected with a 422 naming the violated bound;
  the real served page was fetched and confirmed to contain the new
  markup.

  `GenerateRequest.width`/`.height`'s upper bound (`le=25`) was removed
  entirely, at the user's explicit request — only the lower bound (`ge=5`,
  a grid smaller than that stops making sense as a crossword) remains.
  This brings the web API in line with the CLI (`crossword_gen.py`'s
  `main()`), which never had an upper bound on `--width`/`--height` at
  all — the 25 cap was specific to `GenerateRequest`, undocumented as a
  deliberate decision anywhere in this file, and evidently just an
  arbitrary early default rather than a load-bearing safety limit.
  `frontend/static/index.html`'s matching `max="25"` on both `<input
  type="number">` fields was removed the same way (`min="5"` kept). No
  replacement guard was added anywhere — a very large grid can
  legitimately take a long time or a lot of memory to generate, but that
  trade-off is now the caller's own choice to make, not something this
  API second-guesses. Verified live: restarted the backend/middleware and
  confirmed a request with `width=30, height=28` (previously rejected
  with a 422 naming the old `le=25` constraint) is now accepted and
  starts a real generation job instead of being rejected at validation.

  Three generation-phase durations are now measured and returned on the
  job's `result`, at the user's explicit request: "Durées affichées en
  haut de la grille finale à jour... 'grille générée en XhXmnXs'...
  'définitions générée en XhXmnXs'" — later refined to a third duration
  in between: "Ajouter la durée d'optimisation de la grille." Measured
  with `time.monotonic()` (never `time.time()` — a wall clock can jump
  backward on an NTP adjustment or a DST change, which would corrupt a
  duration computed by plain subtraction; a monotonic clock never does).
  `_run_generate_job`'s own `progress()` closure — already called
  synchronously by `generate_grid()` at every phase transition, even
  while the overall blocking call is still in flight inside its
  `asyncio.to_thread` — is where the two *internal* phase boundaries get
  captured: a `phase_times` dict (mutated from inside the closure, so no
  `nonlocal` needed per key) records a timestamp whenever `step` is
  `"minimizing"` (fired by `generate_grid()` right before it calls
  `minimize_black_squares` — the pattern-search-and-fill phase is done,
  optimization is about to start) or `"grid_ready"` (fired right after
  optimization finishes). `result["generation_duration_seconds"]` is
  `phase_times["minimizing"] - grid_start`; `result["optimization_
  duration_seconds"]` is `phase_times["grid_ready"] -
  phase_times["minimizing"]`; `result["clues_duration_seconds"]` is
  timed the same way as before around the separate `clue_generator.
  generate` call (`clues_start`/`time.monotonic()` right after it
  returns) — unaffected by this split, since clue generation was always
  its own distinct blocking call with no internal sub-phases to further
  divide. `.get(key, fallback)` guards both lookups defensively (falling
  back to `grid_start`/`search_done` respectively) — `result is not None`
  already guarantees the whole pipeline ran to completion, so both keys
  should always be present, but this avoids letting an unexpected gap in
  that guarantee crash an otherwise-successful job over a duration
  calculation alone. Verified live end to end through the real running
  API (not just direct `generate_grid()` calls): a small real generation
  (8×8, `mode=flash`) polled to completion showed
  `generation_duration_seconds=1.67`, `optimization_duration_seconds=
  1.52`, `clues_duration_seconds=138.29` — all plausible, positive, and
  clearly distinct phases, not a single lumped-together number as
  before.

  The web UI's own display of these three durations went through two
  more rounds of refinement in the same session, after the frontend
  side (see `frontend/static/script.js`/`index.html`/`i18n.js`) was
  first built as two separate `<p>` elements above `#stats`: "Sur la
  grille à jouer, afficher le rapport des temps en petits caractères sur
  une seule ligne," then "Le rapport des temps doit s'afficher à la
  suite du rapport '% mots placés'/'% cases noires'" — see the
  style-guide SKILL for the final `#generation-times` design (one
  combined line, styled like `.attempt-preview-stats`, placed right
  after `#stats` rather than before it).
- `backend/system_info.py` — `get_system_info(llm_model)`, best-effort *local
  machine* hardware detection for that info badge: `nvidia-smi --query-gpu=name,
  memory.total` for a discrete NVIDIA GPU's exact name and dedicated VRAM if present,
  else (on macOS) `system_profiler SPDisplaysDataType`'s `Chipset Model:` line for
  Apple Silicon's on-die GPU name — verified directly that this output has no VRAM
  figure to parse for Apple Silicon (unlike a discrete GPU), since it shares the
  machine's own RAM rather than having dedicated video memory, so `sysctl -n hw.
  memsize` (total system RAM) is reported instead, flagged via `unified_memory: true`
  so the frontend labels it correctly ("unified memory", not "VRAM"). This is **not**
  a live query of the separate LLM server process (`llama_cpp.server`, see
  `run_llm.sh`) — that process exposes no such endpoint — so it reports what hardware
  is present and, per `run_llm.sh`'s own `GPU_CMAKE_ARGS` detection, would normally
  be used; on a machine where `run_llm.sh` actually fell back to CPU despite a GPU
  being present (a missing CUDA Toolkit/Xcode Command Line Tools, see `run_llm.sh`),
  this can overstate GPU usage — a documented, accepted limitation, not a bug, since
  there's no cheaper way to know for certain without instrumenting the LLM server
  process itself. A probe failing (missing binary, non-zero exit, timeout) is treated
  as "no GPU found" — never an error, since this is a status display, not worth
  failing a request over.

  One exception to "no cheaper way to know for certain": `LLAMA_FORCE_CPU` (see
  `run_llm.sh`) is a deliberate, known-in-advance choice, not a hardware-capability
  guess — reported next as a real bug: after adding `LLAMA_FORCE_CPU`, the info badge
  kept showing "GPU" even with it set, since `get_system_info()` never looked at it at
  all. Fixed: `get_system_info()` now checks `os.environ.get("LLAMA_FORCE_CPU")` first
  and returns `compute: "cpu"` unconditionally when set, skipping GPU detection
  entirely — this works because `run_Falcon.sh` (which starts this very process)
  sources the same `env.sh` `run_llm.sh` does, so the flag reaches both processes
  alike without needing to pass it through any other channel. Verified live: unit-
  tested both branches directly, then restarted the real servers with `LLAMA_FORCE_
  CPU=1` actually set in `env.sh` — confirmed `/api/system_info` (both the direct
  backend port and the proxied frontend port) reports `"compute":"cpu"`, and
  cross-checked via `ps` that the actual running `llama_cpp.server` process was
  genuinely launched with `--n_gpu_layers 0` to match.
- `backend/clues.py` — `LLMClueGenerator`, the one class that owns all LLM handling
  (endpoint config, prompt text, the HTTP call, response parsing); `backend/app.py`
  builds a single instance at module scope and calls `.generate()` per grid. Talks to
  an OpenAI-compatible chat-completions endpoint, one word per request (`_BATCH_SIZE
  = 1`) rather than a bigger batch — even a handful of words per request degenerated
  (dropped entries, off-language text) on the small local model before finishing; one
  word per call is the size that's actually reliable, at the cost of one HTTP
  round-trip per word — this is by far the slowest phase of generating a grid, which
  is why `generate()` takes an optional `on_progress(current, total)` callback, called
  after every word attempt, for `backend/app.py` to surface live progress instead of
  one static "generating…" message. How slow varies a lot by model: measured live at
  ~2s/word with Qwen3.5-9B (thinking disabled — this project's very first default, and
  its default again for a long stretch of this project's history), ~3s/word with
  Qwen3.5-4B unquantized (thinking disabled — smallest model tried at the time, close
  to Qwen3.5-9B's speed despite being full-precision rather than quantized), ~8-9s/word
  with Qwen3-14B (also thinking disabled — larger model,
  same non-reasoning behavior, so slower per token but not per-word-reasoning-slow),
  ~20-40s/word (a 9×9 grid's 32 words took ~13 minutes of clue generation) with
  Qwen3.8-27B (thinking disabled, Unsloth Dynamic `UD-Q2_K_XL` — the largest
  non-reasoning model tried, and the slowest of the non-reasoning ones, but also the
  one with the strongest observed clue quality so far, see the project-best-practices
  SKILL — a good choice with a GPU with at least 12GB VRAM, per README.md), and
  20-70s/word (potentially 15-40+ minutes per grid) with
  DeepSeek-R1-Distill-Qwen-14B, since it reasons through a `<think>` block before every
  single word's answer — see `_strip_reasoning`/`REASONING_TOKEN_BUDGET` below and
  `run_llm.sh`. A genuinely surprising result from measuring two much smaller models
  added later, Qwen3.5-0.8B and Qwen3.5-2B (both unquantized bf16, thinking disabled):
  **not faster** than Qwen3.5-9B despite being far smaller — measured live at
  25-40s/word for Qwen3.5-0.8B (~25-29s/word on GPU/Metal, ~39-40s/word forced onto
  CPU), in the same ballpark as Qwen3.8-27B despite being ~34x smaller. Root cause
  isn't runaway generation (the raw logged output stays short and clean, no repetition
  or reasoning leakage) — almost certainly this project's own system prompt, which is
  unusually long (many worked rule examples per language), makes prompt *processing*
  (prefill), not answer generation, the dominant cost per call; prefill time doesn't
  shrink proportionally with parameter count the way decode speed does, so a tiny
  model gains little to nothing here despite its much lower per-token compute cost.
  This means "smaller = faster" does not reliably hold for this project's specific
  workload — verify live with your own hardware before assuming a smaller model will
  be quicker in practice. Qwen3.5-0.8B is still the project's default regardless
  (chosen at the user's explicit request specifically so a fresh checkout can generate
  a clue end-to-end on CPU alone with no GPU required — it does that, just not
  quickly), see env_default.sh. Output is plain text, not
  JSON, and — since a redesign, see the long entry below — not even a single delimited
  line anymore: the model is asked for exactly 3 plain lines, one candidate clue per
  line, nothing else (`_parse_response`) — small local models without constrained
  decoding were unreliable at valid JSON syntax, and even the line-based format that
  replaced JSON originally needed the target word repeated as a per-line header before
  anything could be trusted, which turned out to be its own source of unparsable
  responses (see below) — every non-empty line is now trusted directly as one
  candidate, no header or delimiter syntax left to get wrong. `_call()`
  strips a leading `<think>...</think>` block (`_strip_reasoning`) from the raw
  response before it ever reaches `_parse_response` — a no-op for a model that doesn't
  emit one (e.g. Qwen3.5 with thinking disabled), but load-bearing for
  DeepSeek-R1-Distill: left in, the reasoning text would be parsed as if it were real
  candidate lines, contaminating the output with reasoning fragments instead of the
  deliberate final answer. `max_tokens` includes a
  dedicated `REASONING_TOKEN_BUDGET` (2048) on top of the per-word answer budget, sized
  generously against DeepSeek-R1-Distill-Qwen-14B directly (measured live across several
  words: a single word's full response, thinking included, ran anywhere from ~300 to
  ~1300 tokens) —
  harmless for a non-reasoning model, which simply stops well before using it. Each word
  is shown to the model by its accented/inflected spelling (`words[i]["accented"]`, not
  the grid's bare form) and the model is asked for 3 candidate clues per word; one is
  picked at random on our side (`_pick_clue`) — never the model's choice — filtering
  out anything that isn't a recognizable clue: longer than `MAX_CLUE_WORDS` (20) words
  — a real, observed failure mode on a hard/ambiguous word: the model writing out its
  reasoning as if it were the answer itself ("Given the length (3 letters), it's
  likely an abbreviation or a specific proper noun/brand, but in crossword contexts...
  However, looking at the prompt rules: ...") instead of a short clue, sometimes even
  quoting these very instructions back — a hard word-count ceiling rejects this whole
  failure mode outright, language-agnostically, rather than trying to detect "sounds
  like reasoning" — non-Latin-script characters (a
  drift failure mode seen in testing), or the word itself appearing anywhere in the
  clue — as the whole clue, or embedded inside a longer sentence (e.g. "je serais s'il
  pleuvait demain" for `SERAIS`) — in any case/accent (`_contains_target_word`,
  tokenized so it doesn't misfire on an unrelated word sharing the same letters, e.g.
  "château" vs. `CHAT`; the prompt forbids this too, but small models do it anyway —
  the filter is the actual guarantee). A word left with zero candidates after
  filtering, or that the model never answered at all, gets retried up to 3 times in a
  row, immediately, before `generate()` moves on to the next word — this was originally
  round-based (every word got one attempt per pass over the whole list, before any
  word got a second attempt), changed to immediate per-word retry at the user's
  explicit request after the round-based log ordering read confusingly: two
  consecutive "round 1/3" lines for two *different* words looked like a retry that
  silently skipped to the next word, when it was really just two different words' own
  first attempts (see the project-best-practices SKILL). The prompt is split into a `system` message (`_build_system_prompt()` — role,
  difficulty style, rules, worked examples: everything that's identical on every call)
  and a `user` message (`_build_user_message()` — just this one word's accented
  spelling plus its grounding block), rather than one long combined message; both are
  phrased throughout for a single word, not a batch — there's only ever one word per
  call (`_BATCH_SIZE = 1`), and earlier phrasing ("each word below", "Words:" as a
  list, "one line per word") was a leftover from before that was true, since fixed at
  the user's request. The rules and their illustrating examples are also no longer
  interleaved prose — each rule is a short, numbered directive, and every "bad"/"good"
  illustration lives in one clearly delimited `=== EXAMPLES ===` block afterward,
  labeled by which rule it illustrates, so a rule reads as a rule and an example reads
  as an example rather than the two being blended into the same sentence. The system
  prompt explicitly forbids restating the
  word/spelling, a bare grammatical label ("verbe avoir 2e personne...", or
  "Pluriel du mot 'une fée'" for `FEES` — naming the grammatical operation instead of
  defining the word, a real observed failure mode even though it's still just a label
  by another name), or describing the word's spelling/letters instead of its meaning
  (e.g. "word starting with T and ending in EE" for TEE — a real observed failure
  mode) and allows synonyms. Also requires a conjugated verb form's clue to match its exact person,
  number, AND mood/tense together (and, for a noun or adjective, its number —
  singular/plural — and gender), not just the rough meaning (e.g. a third-person
  conditional like SERRERAIT needs a clue that's also third-person conditional, not a
  first-person-future one that only gets the general idea right; a plural noun like
  ANS, "years", needs a clue that itself reads as plural, not one describing a single
  12-month span) — a partial fix, not a complete one, and the failure rate varies a lot
  by word: sampled essentially 0/6 wrong for `SERRERAIT` after the rule's second
  iteration, but `ÉTAIS` (a form of "être", the single most overloaded/high-frequency
  French verb) still failed most of a 6-sample re-test — plausibly harder to correct
  because the model has much stronger competing priors for that one verb specifically;
  `ANS` after the third (noun-number) iteration only reached ~3/8 clearly-plural
  samples; a fourth iteration added `MENTIRA` (third-person-singular FUTURE, e.g. "il
  mentira") as a worked example after a bare-infinitive clue ("Cacher le vrai" instead
  of future-tense "Cachera le vrai") was reported — this one sampled a clean 6/6
  correctly future-tense, with no regression on a `SERRERAIT`/`ANS` re-test (6/6 and
  ~half-plural respectively, matching earlier results). No post-filter for this (unlike
  the copy-of-word/non-Latin checks):
  grammatical agreement needs real parsing of the *clue text*, not just the target
  word, which isn't a lightweight, reliable, five-languages-at-once check the way
  those are. A separate rule requires the clue's *meaning* to actually correspond to
  the word's real meaning, after `SERIONS` (conditional "nous serions", "we would be")
  was given the clue "Il existe des solutions" — an unrelated, hallucinated sentence
  with no connection to "être" at all, not merely a grammar mismatch. Verified live:
  the specific reported symptom (an unrelated hallucinated clue) didn't reproduce in a
  6-sample re-test, but two related issues remain — a bare grammatical label still
  slipped through once each for `FEES` ("Pluriel de la fée.") and `SERIONS` ("Forme
  hypothétique du verbe être pour le pluriel inclusif."), and twice `SERIONS` was
  answered with a *different* verb's conjugation entirely ("dirions"/"dirons", from
  "dire" — same "-rions"/"-rons" ending, wrong root) rather than "être" — a
  same-family-verb confusion distinct from the reported bug, flagged but not
  separately addressed this round. A further ~20 correct worked examples were added
  on top of the "bad example" failure illustrations above — originally a French-only
  module-level constant (`_AGREEMENT_EXAMPLES`), later moved into the per-language
  JSON config described below once every other worked example followed — spanning
  several persons/tenses/moods for regular `-er`/`-ir`/`-re` verbs plus "être"/"avoir"
  specifically, and masculine/feminine singular/plural noun/adjective agreement
  including classic irregular plurals (`nouveau`→`nouveaux`, `vieux`→`vieilles`,
  `cheval`→`chevaux`, `travail`→`travaux`) — each clue hand-checked to never contain
  the target word or a same-family form, and to itself be phrased in the matching
  mood/tense/number/gender, not just gesture at the right idea. Verified live on both
  known-hard words and
  brand-new ones not in the list itself (to check the model generalizes the *concept*
  rather than memorizing the examples): `MENTIRA`/`SERRERAIT` both came back cleanly
  correct on mood/tense every time sampled; `BELLES` and `CHANTAIENT` (novel words)
  came back correctly agreed too; but `SERIONS` ("être" again) still produced a bare
  grammatical label in 2/6 samples ("Forme conditionnelle du verbe être pour nous.")
  even though the rule right above explicitly forbids exactly that, and two other
  novel words, `MANGERIEZ` and `JOYEUSES`, came back without clearly matching mood/
  number either — reported honestly as a real, partial improvement, not a fix: this
  is still a small-model reliability ceiling, most visible on "être" specifically,
  consistent with every previous iteration on this rule.

  Every *concrete* worked example in `_build_system_prompt()` — the difficulty-style
  example word/clue, every "bad"/"good" illustration for rules 1-5, the ~20-example
  agreement bank, and the subject-pronoun list rule 4 names — used to be hardcoded in
  French only, regardless of which of the 5 languages the request was actually in (the
  model was just expected to generalize the underlying grammatical *concept*, e.g.
  "match person and mood", to whichever language it was writing in). These now live in
  `data/<lang>_prompt_config.json` (one file per supported language: fr/en/de/es/it),
  loaded and cached per language by `_load_prompt_config()` (falls back to `fr` if a
  language's file is missing) and assembled by `_build_system_prompt()` via a small
  `_bullets()` helper — the *structure*/explanatory prose of the prompt (the numbered
  rules, the English-language "why this is wrong" reasoning) stays in
  `backend/clues.py` itself, since that's this project's engineering language; only
  the concrete target-language words and clue text moved out. Each JSON file has:
  `subject_pronouns` (a pre-formatted string for rule 4, e.g. French's `"je", "tu",
  "il/elle", "nous", "vous", or "ils/elles"`), `difficulty_examples` (one `{word,
  clue}` pair per `easy`/`medium`/`hard`, appended to `DIFFICULTY_STYLE`'s now-generic
  English description), and `rule_bad`/`rule_good` — two flat lists of bullet-content
  strings, covering every rule's illustrations together rather than one list per rule
  number. Deliberately *not* `rule1_bad`/`rule2_bad`/.../`rule5_bad` (an earlier,
  short-lived version of this schema): a fixed one-list-per-rule-number contract would
  force every language to supply exactly the same shape of illustration for exactly
  the same rules, when in practice different languages need different *numbers* of
  examples for a given point (or an example that doesn't map cleanly onto a single
  rule) — the flat lists let each language's file be sized to what that language
  actually needs, with no cross-language structural coupling. The four
  non-French files were authored to fit each language's *own* grammar rather than
  forcing the French template onto it — verified directly rather than assumed: English
  and German have no single-word synthetic future/conditional for most verbs (unlike
  French/Spanish/Italian's `mentira`/`serrerait`-style forms), so their examples lean
  on what those two languages actually have instead (modal
  auxiliaries — `WILL`/`WOULD`/`COULD`; participles distinct from simple past —
  `SUNG` vs `SANG`; German's Konjunktiv II, which *does* exist as a single word for
  common irregular verbs — `WÄRE`, `KÄME`; irregular plurals — `MICE`, `PFERDE`).
  Verified live for all 5 languages: generated a real system prompt per language and
  read it in full, then ran an isolated `generate()` call per language through the
  actual local LLM server (confirming correct JSON loading and assembly, not
  exhaustive grammar grading the way the French agreement rule's own iterations were)
  and a full grid generated end-to-end in German specifically, since that most
  exercises the newly-added non-French path.
  Phrasing style is calibrated by `difficulty` (easy/medium/hard) via
  `DIFFICULTY_STYLE`, language by `LANGUAGE_NAMES[language]` (must match the grid's
  wordlist). The endpoint is configurable via `LLM_BASE_URL`/`LLM_MODEL`/`LLM_API_KEY`
  (see `env.sh`) so it can target either the local llama.cpp server (default, see
  `run_llm.sh`) or a cloud API (e.g. Mistral) with no code change. Used only by
  `backend/app.py` — the offline CLI (`crossword_gen.py`) never calls it.
  `_build_user_message` also grounds the model with real definitions/examples when
  available
  (`_build_gloss_block`/`_build_examples_block`, backed by `backend/gloss_lookup.py` and
  `backend/example_sentences.py`) — added after the small local model defined French
  `ARE` (the 100 m² land-area unit) as the English verb "to be", confirmed by direct
  testing to be a knowledge gap rather than a prompt-following problem (giving it the
  correct gloss directly fixed the answer every time). Gloss lookup is by the word's
  candidate canonical form(s) (`words[i]["canonical"]`, from `build_wordlist_freq.py`'s
  4th column) — a genuinely ambiguous word (French `suis` -> `être` or `suivre`) shows
  every candidate's definition rather than the dictionary silently picking one; example-
  sentence lookup is by the exact inflected form instead, since Wiktionary is indexed by
  lemma but real usage sentences are not. Both sections are omitted entirely when
  nothing is found for a word — not every word has dictionary or corpus coverage.

  Three real bad clues reported by hand after the schema/rule work above led to a
  further round of filter/prompt fixes: (1) French `MAMANS` (plural "mums") got
  "Personnes à qui l'on dit maman pour les appeler" — the singular `maman` (the exact
  word's own Hunspell-derived lemma) leaking in unfiltered, a same-family-word case
  rule 1 already forbids in the prompt but `_contains_target_word` never actually
  checked for. Fixed in code, not just prompt text: `_contains_target_word` now also
  takes the word's `canonical` form(s) (already computed per word for gloss lookup —
  see `words[i]["canonical"]`) and blocks any of them appearing in the clue too, not
  just the exact target spelling — `generate()`/`_pick_clues()` thread the whole
  `(answer, accented, canonical)` entry through instead of just an accented→answer
  map, so this is available where the filtering happens. Verified with an isolated
  call: the literal MAMANS reproduction case is caught, a legitimate MAMANS clue that
  doesn't touch the root isn't false-flagged, and the existing SERAIS-embedded-in-a-
  sentence case still works. (2) French `SEMAI` (1st person singular passé simple of
  "semer", "I sowed") got "Action de répandre des graines dans la terre" — a generic
  dictionary-style definition of the infinitive, not phrased for the specific
  conjugated form; the clue never contains "semer"/"semai" literally so no
  containment filter could ever catch this — it's the same class of grammar-agreement
  gap already documented above as an accepted, unfixed limitation ("no post-filter
  for this... needs real parsing of the clue text"). (3) French `TENU` (MASCULINE
  singular past participle of "tenir") got "Se dit d'une maison soignée et propre" —
  `maison` is feminine, so the clue's own subject noun silently disagreed with the
  masculine target. Both (2) and (3) are rule-4 grammar mismatches, not containment
  leaks, so the fix for those two is prompt-only: rule 4 in `_build_system_prompt()`
  now explicitly names both traps (a generic infinitive-style "the act of doing X"
  definition standing in for a specific conjugated form; a clue's own subject noun
  disagreeing in gender/number with the adjective/participle it's meant to describe),
  and one new `rule_bad` illustration per trap was added to every language's prompt
  config where the trap actually applies — the SEMAI-style infinitive-vs-conjugated
  trap in all 5 (`SEMAI`/`SEMBRÉ`/`SEMINAI`/`SÄTE`/`SOWED`), the TENU-style gender-
  disagreement trap only in the three languages with participle/adjective gender
  agreement (`TENU`/`CANSADO`/`STANCO` — French/Spanish/Italian; German predicative
  adjectives don't inflect for gender at all, and English has no grammatical gender,
  so this trap can't occur in either). Verified live: resampled the exact `MAMANS`/
  `TENU`/`SEMAI` words several times each through the real local LLM server after the
  fix — no more root leak, correct masculine agreement, and (after one still-imperfect
  present-tense sample, consistent with the known grammar-matching ceiling) correct
  passé simple on repeat.

  A separate, structural bug class was reported next: some words (French `MESURONS`,
  `SES`, `TEL` among them) always ended up showing the frontend's/`svg_export.py`'s
  "no definition available" placeholder, with no visibility into why. Added
  diagnostic logging (`logger = logging.getLogger("crosswordfalcon.clues")`, a child
  of `backend/app.py`'s own logger, same handler/format via propagation) at every
  point a word can end up without a clue: per retry round, whether the model gave no
  parsable candidate line at all vs. gave candidates that were all rejected by the
  copy/non-Latin/same-family filter vs. the HTTP call itself failed, plus a final
  summary of which words never got a clue after all 3 rounds. This immediately
  surfaced the real root cause on the very first live test of the reported words: the
  model was echoing the format template's literal placeholder text ("word:") instead
  of substituting the actual target word before the colon — e.g. `"word: mesurons;
  Action que nous accomplissons..."` instead of `"mesurons: Action que nous
  accomplissons..."` — which `_WORD_LINE_RE`'s known-word check correctly refused to
  trust as a header (by design, to avoid a colon inside a clue being mistaken for a
  new header), so the entire response was discarded as unparsable, every round, for
  every one of the 3 reported words. Root-cause fixed in `_build_system_prompt()`:
  the format-line example now uses the call's own actual word
  (`f"{accented}: clue 1; clue 2; clue 3"`) instead of the literal, ambiguous
  placeholder string `"word: clue 1; clue 2; clue 3"` — removing the ambiguity that
  let a small model interpret "word" as literal text to echo rather than a
  placeholder to replace. `_parse_response()` also gained a narrow defense-in-depth
  fallback for any future variant of the same slip: if the normal parse finds nothing
  at all and exactly one word was asked for (always true given `_BATCH_SIZE=1`), it
  falls back to treating the first line's own header-shaped content as that one
  word's candidates (stripping a leading echo of the word itself, e.g. the
  `"mesurons;"` fragment in the example above, if present) rather than discarding a
  real answer outright — engages only when the strict header-matching path already
  found nothing, so it can never steal a colon-in-a-clue line away from a header that
  matched correctly. Verified live: resampled `MESURONS`/`SES`/`TEL` after the fix —
  all 3 resolved cleanly on the very first round, zero warnings, vs. 9/9 failures
  (3 words × 3 rounds) before it.

  While stress-testing the fix against a broader word list, a related but distinct
  leak surfaced: French `MAISON` came back with the literal, un-filled-in candidate
  `"clue 2"` — the model left one of its 3 slots as an unmodified echo of the format
  template's own `"clue 1; clue 2; clue 3"` placeholder text, and since "clue 2"
  passes every existing filter (not a copy of the answer, not non-Latin), it got
  picked and shown verbatim as if it were a real clue. A second, broader form of the
  same leak was then found by resampling further: the model sometimes keeps a real
  clue but prefixes it with the same leaked label, e.g. `"clue 3: Édifice destiné à
  abriter une famille."` instead of just the clue text. Fixed both: `_LEAKED_TEMPLATE_
  RE` (`^clue\s*\d*$`, case-insensitive) rejects a candidate that's nothing but the
  bare placeholder, wired into `_pick_clues()` alongside the existing non-Latin/
  same-family checks; `_LEAKED_TEMPLATE_PREFIX_RE` (`^clue\s*\d*\s*:\s*`) strips a
  leaked `"clue N:"` label as a *prefix*, applied via a new `_clean_candidate()`
  helper everywhere a candidate is split out of the model's raw text in
  `_parse_response()` (replacing three separate inline `_LEADING_MARKER_RE.sub(...)`
  call sites), so the real clue text underneath survives instead of the whole
  candidate being wasted. "clue" isn't a genuine word in any of the 5 supported
  languages outside this exact leaked-template case, so both regexes are safe to
  apply unconditionally rather than needing a per-language allowlist. Verified live:
  resampled `MAISON` 11 times total across both fixes — no further leaked "clue N"/
  "clue N:" artifacts — then re-ran the full 15-word stress list (`MESURONS`/`SES`/
  `TEL` plus 12 more, including several short French function words: `TOI`/`MOI`/
  `CELA`/`DONC`/`AINSI`/`QUI`/`QUE`) end-to-end: 15/15 resolved with zero warnings
  logged.

  Both incidents above were two separate patches around the same root cause: a
  single-line `"word: clue 1; clue 2; clue 3"` format that needed the model to get a
  header/delimiter syntax exactly right before any of its answer could be trusted, on
  top of literal template text (`"word:"`, `"clue N"`) the model would sometimes echo
  verbatim instead of filling in. At the user's explicit prompt — since there's only
  ever one word per call (`_BATCH_SIZE = 1`, true since this project's very first
  batch-size tuning pass), there was never a real need for a header to match a word
  against in the first place — the whole format was redesigned instead of patched a
  third time: the model is now asked for exactly 3 plain lines, one candidate clue per
  line, nothing else, and `_parse_response()` just splits on newlines, trusting every
  non-empty line directly as a candidate (stripping only a leading numbered/bulleted
  marker, `_LEADING_MARKER_RE`, the one piece of cleanup that's still needed since a
  model can still number its lines despite being asked not to). This retires
  `_WORD_LINE_RE`/`_LIST_ITEM_RE`/`_LEAKED_TEMPLATE_RE`/`_LEAKED_TEMPLATE_PREFIX_RE`/
  `_clean_candidate()` entirely (all now-dead code, deleted rather than left unused)
  and renames `_pick_clues()` (used to operate on a dict of every word in a batch) to
  `_pick_clue()` (operates on one word's own candidate list directly, matching
  `_BATCH_SIZE = 1`'s reality) — its filtering logic (non-Latin drift,
  `_contains_target_word`'s copy/same-family check) is otherwise unchanged, since that
  filtering is about clue *content*, not response *structure*, and remains just as
  relevant under the new format. `_parse_response()` can no longer raise
  `ClueGenerationError` at all (there's no structural way for a line-based response to
  be "unparsable" — a blank response just yields an empty candidate list, which
  `generate()`'s existing empty-candidates handling already covers) — the only
  remaining raiser is `_call()`'s own HTTP-failure path. Verified live: re-ran the
  same 15-word stress list end-to-end (15/15 resolved, zero warnings), unit-tested
  `_parse_response()` directly against a numbered/bulleted 3-line response, an empty
  response, and a single bare line (all handled correctly), then generated a real
  20-word grid end-to-end through the actual running API (`POST /api/generate` →
  polled to completion) — 20/20 words got a clue, zero warnings logged, SVG/PNG saved
  successfully.

  Reported next: a real clue that came back as a multi-sentence reasoning trace
  instead of a short definition (a hard/ambiguous word, several dozen words long,
  reading like the model thinking out loud in English: "Given the length (3 letters),
  it's likely an abbreviation or a specific proper noun/brand, but in crossword
  contexts, 3-letter words are often abbreviations or short common nouns. However,
  looking at the prompt rules: 'The user message will give you a single word...'" —
  even quoting this very prompt's own instructions back). Two fixes, one per the
  user's own two-part request: (1) a hard length ceiling, `MAX_CLUE_WORDS = 20`,
  wired into `_pick_clue()` alongside the existing non-Latin/same-family checks —
  language-agnostic and structural, so it rejects this whole failure mode outright
  regardless of *why* the model rambled, rather than trying to pattern-match
  "sounds like reasoning" text; (2) `_build_system_prompt()`'s rule 7 (previously
  just "keep each candidate clue to one short line") now spells out the same
  `MAX_CLUE_WORDS` limit explicitly and explicitly forbids writing out reasoning,
  discussing the word's letters/length, or quoting these instructions, and a new
  rule 8 explicitly requires every clue to stay entirely in the target language,
  start to finish, forbidding a language switch even for a single word — direct
  reinforcement of the "answer in the right language" requirement, since the
  reported example itself was in English regardless of the target word's own
  language. Verified live: unit-tested `_pick_clue()` against the exact reported
  30-word example (rejected) and a normal 5-word clue (kept); rebuilt and read the
  new rule 7/8 text; then ran a 10-word live batch through the real local LLM server
  (including several short/abbreviation-like words — `ABC`/`ETC`/`ONU`/`PDG` — the
  kind of word most likely to trigger this exact "is it an abbreviation?" reasoning
  spiral) — 10/10 resolved, every clue well under the 20-word cap, no reasoning
  leaks or language drift observed.

  A report from a separately deployed instance (a different machine/model than this
  session's own) showed a word ending up with no clue despite the log seeming to
  show a nearby success right after its round-1 failure — inconclusive from the
  existing warning-only logging alone, since `generate()`'s retry rounds process the
  *entire* pending word list once before circling back to any word that failed (so a
  `current` progress count rising right after one word's failure is very likely a
  *different* word's unrelated success, not that word being retried immediately).
  Rather than keep reasoning about log timing, `_call()` now logs the LLM's exact,
  unmodified response for every single call, unconditionally — `logger.info("clue
  round %d/3: %r (%r) — raw LLM response: %r", ...)`, placed right after extracting
  `content` from the HTTP response and before `_strip_reasoning()` touches it or any
  of `generate()`'s own parsing/filtering runs, so a deployed instance's log always
  has the ground truth for what the model actually said — not just this codebase's
  after-the-fact verdict on it (empty/rejected/failed). Needed `answer`/`accented`/
  the current round number threaded into `_call()` as parameters (previously an
  HTTP-only method) purely so the log line can be tied to the same word/round
  identifiers already used by the round-outcome warnings next to it. Verified live:
  called `generate()` directly for `CHIER` (the exact word from the report) with
  logging enabled — confirmed the new line fires with the full raw multi-line
  response *before* any success/failure verdict is logged, for every call, matching
  what was asked for.

  Extended further at the user's explicit request: `_pick_clue()` used to filter
  rejected candidates with a single list comprehension, silently discarding *why* any
  one of them didn't make it — only a round-level summary ("all N candidate(s)
  rejected by the too-long/copy/non-Latin/same-family filter") was ever logged,
  naming every *possible* reason rather than which one(s) actually applied to which
  candidate. Rewritten to classify each candidate individually and log it right away
  — `logger.info("clue round %d/3: %r (%r) — candidate rejected (%s): %r", ...)` with
  every applicable reason joined together (a candidate can fail more than one check
  at once — too long *and* containing the target word, say — and all of them are
  named, not just the first found), then a matching "candidate selected: %r" line for
  whichever one was ultimately chosen. `round_number` threaded into `_pick_clue()` as
  a parameter for the same reason it was threaded into `_call()` before — tying the
  line to the same round identifier used everywhere else. The round-level summary
  warning stayed, simplified to just point at the per-candidate detail just above it
  rather than re-listing every possible reason generically. Verified live: unit-
  tested `_pick_clue()` directly with a synthetic 4-candidate mix (one clean, one
  containing the target word, one too long, one non-Latin) — each rejected candidate
  logged with its own correct, specific reason, the clean one logged as selected;
  also tested a candidate deliberately failing two checks at once (too long *and*
  containing the target word) — both reasons appeared together, joined with "; ";
  then ran a real end-to-end call through the local LLM server to confirm the format
  holds for genuine model output, not just synthetic test data.

  Another reported bad clue: French `RASÉE` (FEMININE past participle of "raser", "to
  shave") got "Il est rasé de près pour la fête" — leaking "rasé", a *different
  inflection* (masculine) of this exact same word, not a different lexeme like
  `MAMANS`'s "maman" or a grammatically-mismatched unrelated noun like `TENU`'s
  "maison". The user explicitly requested a prompt-level fix (`_build_system_prompt()`'s
  worked examples), not a code-level structural filter, and gave the reason: a
  code-level ban matching any inflected variant of the target's own canonical lemma
  would also reject legitimate, hard-to-avoid clue text for words whose lemma is a
  fundamental verb like "être" — some form of "être" shows up almost everywhere in
  French, including inside otherwise-valid clues for *other* être-conjugations, so a
  blanket morphological-family filter risks making être-based words unclueable far
  more often than it prevents leaks. This is exactly why `_contains_target_word`
  (used by `_pick_clue`) only ever matches the exact answer/accented spelling plus the
  word's own known canonical form(s) — never a full morphological expansion — a
  deliberate scope limit, not an oversight. Fix: rule 1 in `_build_system_prompt()`
  now explicitly names "a different inflection of this exact same word" (not just an
  unrelated same-family word) as forbidden, and one new `rule_bad` illustration was
  added per language with adjective/participle gender inflection — `RASÉE`/`AFEITADA`/
  `RASATA` in French/Spanish/Italian, mirroring the same `TENU`/`CANSADO`/`STANCO`
  language split (German/English have no predicative gender agreement, so this exact
  trap can't occur in either). Verified live: resampled `RASÉE` 5 times through the
  real local LLM server — no leak of "rasé"/"raser" in any sample — then re-tested
  `TENU`/`LÉGALE`/`MAMANS` to confirm the rule 1 wording change didn't regress any
  previously-fixed case.

  A word that exhausts all 3 retry attempts without ever getting a clue now also gets
  its own diagnostic Markdown file (originally `LLMClueGenerator._write_failure_log()`
  — see below for how this was later generalized), at the user's explicit request, so
  a specific failure can be inspected and reproduced by hand without digging through
  `backend.log`: the *last* attempt's complete system + user prompt (identical across
  all 3 attempts, since nothing about the prompt varies between retries), its raw LLM
  output (or `None` if the call itself errored, e.g. a timeout), and any
  `ClueGenerationError` message. Written to `LOG/` (project root, gitignored — a
  debugging artifact, not a durable record like `GRIDS/`), one file per failed word,
  named `<timestamp>_<answer>_ERROR.md` — best-effort like `backend/svg_export.py`'s
  own saves, a write failure is logged but never allowed to break grid generation.
  Verified live: unit-tested the file format directly with synthetic data, then
  forced a *real* total failure (pointed `base_url` at an unreachable port) —
  confirmed all 3 attempts logged the connection error and the resulting `LOG/
  <timestamp>_CET_ERROR.md` correctly captured the exact endpoint/model/prompt/error.

  Later expanded well beyond failures, at the user's explicit request: "liste tous les
  appels LLM (et pas seulement les erreurs)" — every single call now gets its own
  file, not just the ones a word never recovered from, so a whole grid's worth of
  calls can be reviewed after the fact. `_write_failure_log()` (only called once per
  word, after all retries were exhausted) became `_write_call_log()` (called once per
  *attempt*, unconditionally, right after each one finishes — up to 3 files per word
  now, one per round), with a new `outcome` field summarizing what `generate()` did
  with that specific attempt (`"selected: ..."` / `"all N candidate(s) rejected"` /
  `"LLM call failed: ..."`). Filename generalized from `<timestamp>_<answer>_ERROR.md`
  to `<timestamp>_<answer>.md` (the "_ERROR" suffix no longer fit a file that's just
  as often a success) — `answer` is already the grid's bare, accent-stripped uppercase
  form (crossword convention), so it already matched "le mot recherché (forme
  majuscule sans accent) à la fin du nom de fichier" with no extra normalization
  needed. `CALL_LOG_DIR` (renamed from `FAILURE_LOG_DIR`) still points at the same
  `LOG/` folder — no path change, just no longer a misnomer. Verified live: generated
  a real clue for `CHAT` end to end — confirmed exactly one file was written even
  though the call *succeeded*, named `<timestamp>_CHAT.md`, with `**Outcome**:
  selected: '...'` and the full prompt/raw-output content intact.

  Refined twice more, both at the user's explicit request. First: every filename now
  ends with `_SUCCES` or `_ERROR` (`<timestamp>_<answer>_SUCCES.md` /
  `<timestamp>_<answer>_ERROR.md`) — SUCCES means that specific attempt produced a
  usable clue, ERROR covers every other outcome (no candidates at all, every
  candidate rejected, or the HTTP call itself failing), so a directory listing alone
  shows which calls need attention without opening every file. Second: each file's
  very last section now lists every candidate the model proposed for that call, each
  tagged `selected` / `accepted (not selected)` / `rejected: <reason(s)>` — not just
  the header's one-line `Outcome` summary. This needed `_pick_clue()` itself to
  change shape: it used to just return the chosen clue (or `None`); now it returns
  `(chosen, details)`, `details` being `[(candidate, verdict), ...]` for every
  candidate in order, tracked by *index* into that list (not by comparing candidate
  text) specifically so two candidates that happen to be textually identical can't
  both get mismarked as "selected". `generate()` threads `candidate_details` through
  to `_write_call_log()` unchanged; the "no candidates at all" and "HTTP call failed"
  branches pass an empty list, rendered as "(none — see Error above...)". Third,
  separately: the folder itself was renamed from `LOG/` to `LOG_LLM/` (`.gitignore`
  updated to match) once it became clear this project could plausibly grow other,
  unrelated kinds of logs later — `LOG_LLM/` says specifically what this one is for.
  Verified live: unit-tested `_pick_clue()`'s new `(chosen, details)` return directly
  against a 3-candidate mix (one rejected, one accepted-but-not-chosen, one selected)
  — all 3 verdicts correct and in the original order; forced both a real success and
  a real total failure end to end — confirmed `_SUCCES.md`/`_ERROR.md` filenames
  respectively, and confirmed the Candidates section at the bottom of a real
  generated file correctly listed all 3 raw candidates with their true verdicts.

  Separately, tightened artifact filtering ahead of any content analysis, at the
  user's request after reviewing the new raw-response logging output. Found and fixed
  a real bug in `_strip_reasoning()` while doing this: it gated on `"<think>" in
  content`, but the regex that actually does the stripping only needs `</think>` —
  some chat-template/server setups inject the opening `<think>` into the *prompt*
  itself rather than echoing it back in the completion's own `content` field, so a
  real response can start directly with raw reasoning text and only a stray
  `</think>` marking where it ends, with no literal `<think>` anywhere in `content`;
  the old gate would skip stripping entirely in exactly that case and leak the
  reasoning text straight into `_parse_response`. Now gates on `</think>` instead (and
  still returns `""` for the inverse case — `<think>` present with no `</think>`,
  meaning the reasoning ran out of `max_tokens` before ever reaching an answer).
  `_LEADING_MARKER_RE` also broadened from `-`/`*`/`•`/numbered markers to include
  em-dash (`—`) and en-dash (`–`) introductory-dash variants, and `_parse_response()`
  now normalizes a non-breaking space (U+00A0 — some models emit these instead of a
  plain space, which `str.strip()` alone doesn't remove) to a regular one before any
  other cleanup. Verified live: unit-tested all 4 `_strip_reasoning()` cases (normal
  `<think>...</think>` pair, stray `</think>`-only, `<think>`-only/unterminated, no
  tags at all) plus `_parse_response()` against a mix of em-dash/en-dash bullets and
  an embedded non-breaking space — all handled correctly — then re-ran a 5-word live
  batch through the real LLM server to confirm no regression.

  A related bad clue reported next: a full visible self-correction inside the answer
  itself — "Elle raserait... (wait, no) -> Elle l'abattra au sol d'un geste sec" — not
  a `<think>` tag at all, just stream-of-consciousness drafting left in the final
  line. Rule 7 in `_build_system_prompt()` already told the model not to "think out
  loud", but didn't name this specific pattern; it now explicitly forbids inline
  self-correction ("starting one answer, then writing something like 'wait, no' or
  'actually' before giving a different one") and instructs deciding the final answer
  entirely before writing anything down. One new `rule_bad` illustration was added to
  all 5 languages (not just Romance ones — this failure mode isn't tied to any
  particular grammar, so every language gets it): `ABATTRA`/`FELLED`/`FÄLLTE`/
  `DERRIBARÁ`/`ABBATTERÀ`, each built around a "knock down/fell" verb so the corrected
  answer stays close to the reported example's own domain. Verified live: resampled
  `ABATTRA` (the exact reported word) alongside `RASÉE`/`SEMAI`/`LÉGALE`/`TENU` (prior
  fixed cases) — all 5 resolved with no visible self-correction and no regression.

  Separately reported: a wrong-*person* conjugation mismatch — "Ce que tu fais quand
  une blague te fait plaisir" (second person, "tu") for `RIT` (third person, "il/elle
  rit"). This is the same rule-4 grammar-agreement class `ÉTAIS`/`SERRERAIT` already
  illustrate, but specifically the "right periphrastic template, wrong pronoun slot"
  failure — plausible since `rule_good`'s own worked examples repeat a "Ce que
  tu/il/elle/nous/vous/ils font..." template across many different pronouns, and the
  model can pick the wrong one for a new word. One new `rule_bad` illustration added
  per language, again all 5 (person mismatch isn't Romance-specific either):
  `RIT`/`LAUGHS`/`LACHT`/`RÍE`/`RIDE`, all built around "to laugh" so the wrong-person
  clue text ("what you do...") and its correction ("what he/she does...") read
  naturally in each language. Verified live: resampled `RIT` (the exact reported word)
  4 times — all correctly third person, no second-person leak.

  Next reported: another gender-mismatch clue — "Se dit d'une herbe privée
  d'humidité" (feminine "herbe") for `SEC` (masculine "dry") — the exact class rule
  4's trap (b) and the `TENU`/`LÉGALE` examples already cover, but the user asked to
  reinforce the instruction itself, not just add one more example. Trap (b)'s text
  now explicitly calls out that the offending noun can be just as easily an ordinary,
  unremarkable one ("grass", "soil") as an obviously-gendered one, and adds an
  explicit final self-check step: before finalizing each candidate, check the target
  word's own gender/number against the gender/number of the noun the clue names, and
  rewrite if they don't match exactly. One new `rule_bad` illustration
  (`SEC`/`SECO`/`SECCO`) added to French/Spanish/Italian — the same three-language
  split as `TENU`/`CANSADO`/`STANCO`, since German/English have no predicative gender
  agreement to violate. Verified live: resampled `SEC` 5 times through the real local
  LLM server — every sample used a correctly masculine noun (one, coincidentally,
  matched the `rule_bad` entry's own corrected suggestion word-for-word) — then
  re-tested `TENU`/`LÉGALE`/`RIT` to confirm no regression.

  Next reported: French `SLIPS` came back with every one of its 3 candidates opening
  with `"slips - "` before the actual definition (e.g. `"slips - sous-vêtement
  féminin"`) — the model labeling its own answer before defining it, which
  `_contains_target_word` correctly flagged and rejected every single time (the word
  genuinely is in there), but that meant burning a whole retry round over a
  mechanically fixable formatting slip rather than salvaging what were otherwise 3
  perfectly good definitions. Fixed two ways, matching the user's two-part request:
  rule 1 in `_build_system_prompt()` now explicitly forbids opening a candidate with
  the word itself as a label before a colon/comma/dash (not just embedding it
  mid-sentence, which the rule already covered), and `_pick_clue()` now runs every
  candidate through a new `_strip_leading_word_label()` first — if a candidate opens
  with exactly the target word (or its accented spelling or a canonical form)
  followed by one of those punctuation marks, the label is stripped and the
  definition underneath is kept, rather than the whole candidate being thrown away.
  Deliberately narrow (matches only that exact leading shape) so it can never rewrite
  an unrelated candidate that happens to start with its own colon/dash/comma. One new
  `rule_bad` example added per language (`SLIPS`/`OWLS`/`EULEN`/`BÚHOS`/`GUFI`, all
  plural nouns, so the example reinforces number agreement too — the original
  `"slips - sous-vêtement féminin"` mistake was also singular for a plural target).
  Verified live: unit-tested `_strip_leading_word_label()` against the exact reported
  example (all 3 leaked-label candidates correctly cleaned) and several false-
  positive-risk cases (an unrelated leading word + comma, no label at all, a
  different word sharing letters) — none wrongly rewritten; a real end-to-end call
  for `SLIPS` resolved cleanly with a correctly plural, unlabeled definition.

  Also reported: a candidate that wasn't even an attempted clue — leaked English
  meta-commentary ("All good. Let me also make sure they're short (≤20 words each)")
  for a French word. Neither `_NON_LATIN_RE` (still Latin script) nor `MAX_CLUE_WORDS`
  (short enough) could catch this, so at the user's explicit request for "un typage
  de langue" (a language-typing check), `_pick_clue()` gained a new
  `_detect_wrong_language()` check: counts how many of each *other* supported
  language's common function words appear in a candidate as whole tokens, and flags
  it if any one language reaches `_WRONG_LANGUAGE_MIN_STOPWORDS` (2) distinct hits.
  Deliberately not a real language-ID library or a Hunspell-based check like
  `build_sentence_corpus.py`'s own `_filter_by_language` — either would add a new
  *runtime* dependency this backend has never needed (Hunspell has only ever run at
  preprocessing time, in the one-off `build_*.py` scripts); a small hardcoded
  stopword list needs neither. First draft produced a real false positive of its
  own, caught before shipping: legitimate Spanish ("Compañero de cuatro patas que
  ronronea.") was flagged as French, because "de" and "que" are spelled identically
  in both languages by shared Latin origin. Fixed by computing cross-language overlap
  *programmatically* — `_LANGUAGE_STOPWORDS_RAW`'s 5 lists are written naturally per
  language with no manual overlap-checking, then any word appearing in more than one
  language's list is dropped from all of them (`_ambiguous_stopwords`) before use —
  robust against a future edit to any list reintroducing a collision unnoticed, not
  just correct for today's specific lists. Verified live: the exact reported example
  now correctly flags as `en`; the Spanish false positive no longer triggers; swept a
  dozen more real clues (including several generated earlier in this very session,
  across all 5 languages) with zero further false positives.

  `LLMClueGenerator.generate()` gained a `cancel_event` parameter (a
  `threading.Event`, `None` by default), at the user's explicit request: a
  "Stop" button on the web UI (see `backend/app.py`) that interrupts a
  generation in progress, whichever phase it's currently in — clue
  generation is by far the slowest phase (see this file's own docstring),
  so it needed the same cooperative cancellation mechanism already added
  to `crossword_gen.py`'s `generate_grid()`/`minimize_black_squares()`
  (see `GenerationCancelled`). Checked once per word, right before
  starting that word's own round of up to 3 LLM calls — a call already in
  flight is never interrupted mid-request, only the *next* word's calls
  are skipped, so the actual stop can take up to one word's own remaining
  round-trip(s) to take effect (a few seconds up to tens of seconds
  depending on the model, per this file's own measured per-word timings).
  Raises `crossword_gen.GenerationCancelled` rather than returning
  early with a partial `{ANSWER: clue}` dict, matching the same
  distinction already made in `generate_grid()`: a cancellation is not the
  same outcome as "ran out of retries for this word" or any other
  legitimate partial result, so it needs its own signal rather than being
  silently folded into an existing return path.
- `backend/gloss_lookup.py` — `find_glosses_for_canonicals()`, looks up real
  definitions in the per-language gloss dictionary built by `build_gloss_dictionary.py`
  (`data/gloss_dictionary/<lang>_glosses.jsonl`, checked into the repo — unlike most
  other generated data files here, this one is small enough (a few tens of MB total)
  and important enough at runtime — see `require_gloss` below — to ship directly
  rather than rely on every deploy re-running the one-off, multi-GB-download build
  script). Loaded and cached in
  memory per language on first use (lazy, once per process lifetime — same pattern as
  `backend/example_sentences.py` below).
- `backend/example_sentences.py` — `find_examples_for_words()`, looks up real usage
  sentences in the per-language reference corpus (`build_sentence_corpus.py`'s output).
  Indexes the *entire* language wordlist once per process lifetime, not just the current
  grid's ~30-50 words, the first time that language is needed (lazy-built,
  `_index_cache`) — tokenizing every line of a multi-million-line corpus and checking
  each token against a large target set costs about the same as checking a small one
  (both are O(1) hash lookups per token), so indexing the whole lexicon up front is
  barely slower than a narrower index would have been, and every later lookup (any
  word, any future grid, same language) becomes a cached-dict read instead of a
  multi-second rescan. Reservoir sampling (per word, capped at `RESERVOIR_SIZE`) keeps
  memory bounded across tens of thousands of words while still returning a uniform
  random sample across every match — not just the first few encountered — each time
  `find_examples_for_words()` is called, so a common word doesn't always show the same
  handful of examples. `CORPUS_DIR` must point at `data/reference_corpus/` (the
  directory `build_sentence_corpus.py` actually writes to) — it silently pointed at a
  stale `data/opensubtitles_corpus/` (this corpus's pre-Wikipedia, OpenSubtitles-only
  name) for some time, a leftover from before the corpus was renamed; since
  `_build_index()` treats a missing directory as "no corpus available" (a legitimate,
  documented degrade-gracefully case for a language nobody's built yet), this failed
  silently — no error, just an empty example-sentences section on every single LLM
  call, for every word, in every language, ever since. Caught only because the user
  noticed examples were never actually appearing in a shown prompt; fixed by pointing
  `CORPUS_DIR` at the real directory. There is no `data/opensubtitles_corpus/` on disk
  either now or previously in this session — the mismatch was a pure stale code
  reference, not a leftover directory to clean up.

  A second, similarly silent gap of the same shape was found next, reported by the
  user from a real `LOG_LLM/*_ERROR.md` failure file: French `élu` had no "Real example
  sentences" section in its user prompt, despite the corpus visibly containing real
  sentences using it. Root cause: `_load_wordlist_words()` indexed the wordlist's bare
  `MOT` column (1st — accent-stripped, uppercase, e.g. `ELU`) as its target set, while
  `_build_index()`'s corpus scan tokenizes and lowercases actual corpus text *without*
  stripping accents (e.g. `élu` stays `élu`) — the two could only ever agree for words
  with no diacritics to begin with, so every genuinely accented word (a large fraction
  of French/Spanish/Italian/German vocabulary) silently never matched anything, no
  error, just an empty examples section on every single LLM call for that word, in
  every language, apparently since this file was written. Fixed by reading the
  wordlist's `ACCENTED` column (2nd) instead — the natural, accented spelling that
  actually matches what appears in the corpus and what `find_examples_for_words()` is
  called with in the first place. Verified live: `élu` now finds 5 real examples
  (e.g. "Le 16 novembre 1995, Liamine Zéroual fut élu président..."); spot-checked
  more accented words across every language (`déterminées`/`être` in French,
  `más`/`año`/`está` in Spanish, `città`/`più`/`perché` in Italian, `über`/`für` in
  German) — all now find real examples where they previously found none; confirmed via
  `_build_user_message()` directly that a real generated prompt for `élu` now includes
  the full "Real example sentences" section end to end.
- `build_gloss_dictionary.py` — one-off preprocessing script: downloads a language's
  Wiktionary extract in full from Kaikki.org (kaikki.org, CC-BY-SA/GFDL like
  Wiktionary itself) — for English, the primary (English-Wiktionary-sourced)
  extraction, already in English; for French/German/Spanish/Italian, that same
  extraction's glosses are in *English* (English Wiktionary's take on a foreign word),
  so this uses Kaikki's own-language Wiktionary edition instead (`frwiktionary`,
  `dewiktionary`, `eswiktionary`, `itwiktionary`), which gives native-language
  definitions. These files can't usefully be partially downloaded like
  `build_sentence_corpus.py`'s sources — they're not sorted by frequency, so a partial
  download would only ever cover words starting with the first few letters of the
  alphabet — so each is downloaded in full (multi-gigabyte: ~3.2GB for English, ~3.2GB
  French, ~3.3GB German, ~1.4GB Spanish, ~460MB Italian) and filtered down
  to just the lemmas `data/wordlist_<lang>_full.tsv`'s `CANONIQUE` column actually
  needs (a few hundred thousand words at most) — the small filtered result
  (`data/gloss_dictionary/<lang>_glosses.jsonl`, checked into the repo — see
  `backend/gloss_lookup.py` above) is what the app itself reads. The raw download
  itself is kept too, not deleted after filtering — cached under `DICS/` (project
  root, gitignored, mirroring `build_sentence_corpus.py`'s own `CORPUS/` raw cache),
  at the user's explicit request: a lemma set that changes again later (a wordlist
  rebuild adds/drops words, `MAX_GLOSSES_PER_WORD` changes, etc.) can re-filter
  against the already-downloaded dump instead of re-fetching several gigabytes per
  language from kaikki.org every time — a source already cached there is read from
  disk instead of downloaded again. This is why rule 6 of the project-best-practices
  SKILL treats `build_sentence_corpus.py` → `build_wordlist_freq.py` →
  `build_gloss_dictionary.py` as one atomic pipeline: a corpus-source change can
  ripple all the way to which lemmas this script needs to look up, so all three
  stages get re-run together, not just the first one or two.
- `backend/svg_export.py` — `save_grid_svg()`, called by `backend/app.py` once a grid
  and its clues are both ready: renders a single self-contained SVG (no external
  assets/fonts) — a header (logo, "CrossWordFalcon", `VERSION.txt`'s version, the
  generation date, the grid's language in its own native name, and its difficulty
  level — identifies a file at a glance rather than relying on its filename/timestamp
  alone), the empty puzzle (grid + clue lists, grouped/chained the same way
  `frontend/static/script.js`'s `renderClueLines()` does, reimplemented in Python since
  this is backend-only), then the fully-solved grid underneath — and writes it to
  `GRID_SVG/` (project root, gitignored — generated output, not source content), named
  `<timestamp>_<language>.svg` (microsecond precision so two grids finishing in the
  same second, e.g. from two browser tabs, don't collide). A durable record of every
  grid the app produces, since the web UI itself has no export feature and forgets the
  grid the moment the tab closes. The logo is embedded as a base64 `data:` URI
  (`_logo_data_uri()`, read from `frontend/static/logo.png` once per process and
  cached) so the SVG stays self-contained even though the logo itself lives outside
  `backend/`; the difficulty label and the language's native name (`_NATIVE_LANGUAGE_
  NAMES`, `_DIFFICULTY_LABELS`) duplicate `frontend/static/index.html`'s `<select>`
  option text and `i18n.js`'s `difficultyLabel`/`difficultyEasy`/etc. by hand, the same
  pattern already used for the clue headings below — keep all of them in sync if any
  of those strings ever change. Clue-heading text (`_HEADINGS`) duplicates
  `frontend/static/i18n.js`'s `acrossHeading`/`downHeading` strings by hand — keep both
  in sync if a heading ever changes. `save_grid_png()` additionally renders that SVG to
  a PNG of the same basename under `GRID_PNG/` (project root, gitignored, same as
  `GRID_SVG/`) via the
  `rsvg-convert` CLI (part of `librsvg` — `brew install librsvg` / `apt-get install
  librsvg2-bin`; the same tool already used for `frontend/static/logo.png`, see the
  style-guide SKILL), at `PNG_DPI` (300, print quality rather than the screen-oriented
  96 DPI default) via `rsvg-convert -z (PNG_DPI / 96)` — `--dpi-x`/`--dpi-y` alone have
  no effect here (verified directly) since the SVG's root has no physical width/height
  unit (in/mm/pt) for librsvg to rescale against, only the zoom flag actually scales a
  unitless SVG's pixel output. `GRID_SAMPLES/` (project root) is a separate directory,
  never written to by either function: a small, hand-curated selection of example
  grids, deliberately **not** gitignored so it stays checked into the repo — until this
  split, every single generated grid's PNG accumulated there without bound; now that's
  `GRID_PNG/`'s job (gitignored, one per request, like `GRID_SVG/`), and `GRID_SAMPLES/`
  only grows when someone deliberately picks an example and adds it by hand, at the
  user's explicit request.
  Both saves are best-effort (a missing `rsvg-convert`, or any other failure, is logged
  as a warning by `backend/app.py`, never fails the request). `_group_clue_lines()`
  takes the grid's `language` and shows a translated "no definition available"
  placeholder (`_NO_DEFINITION`) for any word whose clue is missing — never the bare
  answer. That used to be the fallback (a word with no clue displayed as its own
  definition), a deliberate early design choice later found to defeat the whole
  point of `backend/clues.py`'s copy/embedded-word filtering: a word that exhausts
  all 3 retry rounds without a valid clue is exactly the case those filters are
  meant to catch, so silently falling back to the answer let "word == definition"
  back in through a side door. `frontend/static/script.js`'s `renderClueLines()`
  mirrors the same fix client-side (`I18N[uiLanguage].noDefinition`, `script.js`
  below).

  `render_grid_svg()` also draws a faint logo watermark behind the whole
  document, at the user's explicit request — mirrors the web UI's own
  watermark (`frontend/static/style.css`'s `body::before`, see the
  style-guide SKILL), sized/positioned differently since this is a fixed
  document rather than a live viewport: 90% of the canvas's width (reusing
  `_logo_data_uri()`'s already-embedded PNG rather than adding SVG-in-SVG
  handling; `frontend/static/logo.png` is ~square, ~1022x1024, so height
  uses the same value as width rather than a separate aspect-ratio
  calculation) and centered vertically in the document's *final* height —
  computed only once the header/grids/clues above are fully laid out, not
  some intermediate/partial height, avoiding the exact truncation bug the
  web UI version hit and had to be fixed for (see the style-guide SKILL).
  Placed right after the background `<rect>` and before every real
  element, so it paints behind all of them in SVG's document-order paint
  model; `opacity="0.1"` on the `<image>` itself, not a filter on the PNG,
  is the same 90%-transparent treatment as the web UI. Verified visually:
  generated a real small grid end-to-end, rendered it to PNG via
  `save_grid_png()`, and read the actual image — the watermark shows
  faintly across the full page (both the empty puzzle and the solution
  grid below it), correctly centered and sized, confirming the math
  (`x = (canvas_width - watermark_size) / 2`, same for `y` against the
  final height) is right, not just that the SVG markup parses.

  The empty-puzzle section's layout was reworked to mirror the web UI's
  own `#board` (across clues beside the grid, down clues below in 2
  columns — see `frontend/static/style.css`), at the user's explicit
  request: this export used to stack every clue list (across, then down)
  in one single column below the grid, unlike the web page it's meant to
  match. `_grid_svg()` gained an `x_offset` parameter (defaults to
  `MARGIN`, the original always-flush-left placement — the solution grid
  at the bottom keeps this default, since it has no sidebar next to it)
  so the *empty* grid alone can start further right, next to the across
  clues. Two new small helpers, `_heading_svg()` and `_clue_lines_svg()`,
  replace the old `add_heading`/`add_lines` closures (which mutated a
  single shared `y`/`parts` pair, unable to lay out two independent
  columns side by side) — `_clue_lines_svg()` returns `(markup,
  height_px)` so a caller can run it more than once at different
  coordinates and compare/combine the resulting heights, exactly what
  laying out a sidebar next to a grid (or two down-clue columns side by
  side) needs. The across sidebar and the grid each take exactly 50% of
  their shared row's width, at the user's own explicit follow-up request
  — since the grid itself is a fixed number of fixed-size cells (it can't
  stretch to fit a percentage), an even split means computing
  `grid_width_px` first and giving the sidebar that same width, rather
  than a separately-chosen constant; `canvas_width` itself is derived from
  `2 * grid_width_px + GRID_SIDEBAR_GAP + 2 * MARGIN` (still floored by
  `MIN_CANVAS_WIDTH`, unchanged, for a very small grid where that would
  otherwise make the page uncomfortably narrow). The row's own height is
  `max(sidebar_height, grid_height)` — whichever of the two clue text or
  the grid itself ends up taller — before the down-clues section begins;
  the down clues are then split into 2 columns spanning the full row width
  (`down_col_width = (canvas_width - 2*MARGIN - DOWN_COLUMN_GAP) / 2`) by
  plain count (`down_lines[:half]`/`down_lines[half:]`) rather than
  balancing by rendered height — simple and deterministic, and CSS's own
  `column-count` (the web UI's mechanism) only balances approximately too,
  so this isn't a meaningfully worse approximation. Verified visually: a
  real `generate_grid()` result (11×9, seed 1) rendered to SVG then PNG
  (`rsvg-convert`) and read directly — first with deliberately long,
  multi-line clue text (confirming the sidebar can legitimately end up
  much taller than the grid, and that the row correctly waits for
  whichever is taller before starting the down-clues section), then again
  with realistic, short clue text (confirming a normal-looking page: the
  sidebar and grid read as genuinely equal-width columns, and the down
  clues form 2 clean columns below, matching the web UI's own layout).

  A third header line was added, at the user's explicit request: "En
  tête d'export SVG, ajouter une ligne d'information avec le mode
  sélectionné : 'Mode XXX' / 'Grille générée en XhXmnXs' / 'Optimisation
  en XhXmnXs' / 'Définitions générées en XhXmnXs'." `render_grid_svg`/
  `save_grid_svg` both gained a new `mode` parameter (`None` by default —
  the CLI, which has no such concept, is unaffected and simply never
  shows this line at all). Two new per-language dicts mirror this
  project's own established pattern for header metadata (`_DIFFICULTY_
  LABELS`, already hand-duplicating `i18n.js`'s own difficulty strings):
  `_MODE_LABELS` (mirrors `i18n.js`'s `modeLabel`/`modeFlash`/`modeTurbo`/
  `modeFast`/`modeMedium`/`modeUltra`) and `_DURATION_LABELS` (mirrors
  `script.js`'s three duration labels, kept as 3 separate strings here
  rather than the web UI's newer single combined `generationTimes`
  function, since the SVG header's own layout — "Mode X — Grille
  générée en Y — Optimisation en Z — Définitions générées en W", all 4
  pieces on one joined line via `" — ".join(info_bits)` — doesn't map
  onto the same one-function-per-language shape anyway). A `_format_
  duration(seconds)` function reproduces `script.js`'s `formatDuration`
  exactly (same "XhXmnXs", leading-zero units omitted) — a separate
  Python implementation, not a shared one, since this file has no access
  to the frontend's own JS; keep both in sync if the format ever
  changes. Each of the 4 pieces (mode, and the 3 durations) is added to
  the line independently and only if actually available — `mode is not
  None` for the first, `"generation_duration_seconds" in result` (etc.)
  for the other three — so a caller supplying none of them (the CLI)
  gets no 3rd header line at all, rather than a line of empty dashes;
  `HEADER_HEIGHT` grew by one line-height (+18px) to fit this new line
  without it colliding with the row below, since the header's own height
  used to be governed solely by the logo's fixed 48px, not by a
  3-line-tall text block. Verified live: a real small generation (6×6,
  `mode="flash"`) through the actual running API, saved via the real
  `save_grid_svg()`/`save_grid_png()` path, produced a header whose 3rd
  line reads exactly `"Mode Flash — Grille générée en 2s — Optimisation
  en 1s — Définitions générées en 1mn9s"` — both the SVG's raw markup and
  the rendered PNG were inspected directly, not just reasoned about.
- `run_llm.sh` — default local LLM launcher: serves whichever quantized GGUF
  `env.sh` (or, absent that, the checked-in `env_default.sh`) names, downloading it
  into `models/` (gitignored) the first time, via `llama_cpp.server` (llama.cpp's
  built-in OpenAI-compatible server — no hand-written wrapper needed). `run_llm.sh`
  itself carries **no hardcoded model default** — `GGUF_REPO`/`GGUF_FILE`/the
  `--chat_template_kwargs` value are all required (`${LLAMA_GGUF_REPO:?...}` etc.,
  erroring clearly if genuinely unset) from `LLAMA_GGUF_REPO`/`LLAMA_GGUF_FILE`/
  `LLAMA_CHAT_TEMPLATE_KWARGS`, sourced from `env.sh`/`env_default.sh` — not
  duplicated as a second, separately-maintained fallback the way earlier versions of
  this script did. That duplication was a real, previously-unnoticed footgun: every
  time the project's chosen default model changed, both `env_default.sh`'s active
  block *and* this script's own hardcoded `${VAR:-default}` values had to be updated
  in lockstep, and a missed update wouldn't necessarily error — `LLM_MODEL` (the label
  `backend/clues.py` sends) and the GGUF actually served could silently disagree.
  `env_default.sh`'s active block is checked into the repo and always complete
  (all four of `LLM_MODEL`/`LLAMA_GGUF_REPO`/`LLAMA_GGUF_FILE`/
  `LLAMA_CHAT_TEMPLATE_KWARGS` set together, never just one), so falling back to it
  when `env.sh` doesn't exist yet still gives a correct, current default with zero
  duplication — verified directly: with neither file present the script now fails
  immediately with a clear "not set" error rather than silently reaching for a stale
  built-in value. This project's very first default (Qwen3.5-9B) is restored again
  after trying four alternatives (Qwen3.5-4B unquantized, Qwen3-14B, DeepSeek-R1-
  Distill-Qwen-14B, Qwen3.8-27B) — all four remain fully supported, see
  `env_default.sh`; Qwen3.8-27B specifically is called out in README.md as a good
  choice for anyone with a GPU with at least 12GB VRAM, since it showed the strongest
  observed clue-agreement quality of every model tried (see the project-best-practices
  SKILL) at the cost of being much slower (~20-40s/word vs. this default's ~2s/word).
  One package (`requirements-llama.txt`) covers Linux and macOS alike (Metal on Apple
  Silicon, CUDA on Linux with a GPU, CPU everywhere) — but a plain `pip install` only
  builds llama-cpp-python for CPU; before starting the server, the script checks
  `llama_cpp.llama_supports_gpu_offload()` against whether a GPU should be present
  (macOS, or `nvidia-smi -L` succeeding) and force-reinstalls with the right
  `CMAKE_ARGS` (`-DGGML_METAL=on` / `-DGGML_CUDA=on`) if they disagree, so
  `--n_gpu_layers -1` below isn't silently a no-op. For CUDA specifically, also
  checks `nvcc`/`$CUDACXX` is actually available first (`nvidia-smi` only proves the
  driver is installed, not the CUDA Toolkit needed to compile) and never lets a
  failed rebuild abort the script — falls back to whatever's already installed and
  runs on CPU rather than not starting at all. Qwen3 and Qwen3.5 are hybrid thinking/
  non-thinking models whose chat template reads an `enable_thinking` flag — verified
  directly by inspecting each GGUF's own embedded template (e.g. `{%- if
  enable_thinking is defined and enable_thinking is false %}` — an explicit check, not
  a guess) — without setting it `false`, the model burns the whole
  token budget on a `<think>...</think>` block before ever answering, starving
  `backend/clues.py`'s calls of any usable output (verified on Qwen3.5: 28s and no
  parsable line without the flag, 4s and a clean answer with it). DeepSeek-R1-Distill
  has no such flag at all — its own chat template never references `enable_thinking`,
  so passing it is silently ignored either way; it always reasons through a `<think>`
  block before answering (see `backend/clues.py`'s `_strip_reasoning`/`REASONING_TOKEN_
  BUDGET` below) — `--n_ctx` is kept at 8192 (bumped from an original 4096) so either
  model's prompt + reasoning (when applicable) + answer has room to fit; this is a
  shared setting, not swapped per model, since it's a safe/sufficient value for all
  three GGUFs this project has actually run against. This is the only local LLM
  backend in the repo — see `LLM_BASE_URL` in `env.sh` to point at a cloud API (e.g.
  Mistral) instead. GPU is used by default whenever the detection above finds one
  (Metal/CUDA); `LLAMA_FORCE_CPU` (`env.sh`/`env_default.sh`, unset by default — any
  non-empty value counts as set) forces CPU regardless, at the user's explicit
  request — e.g. to free the GPU for another process, or sidestep a flaky/unsupported
  GPU build. Computed once, early, into `N_GPU_LAYERS` (`-1` = offload every layer
  llama.cpp can, the GPU-preferring default; `0` = CPU-only), which the final
  `--n_gpu_layers` flag then just passes through — when `LLAMA_FORCE_CPU` is set, the
  entire GPU detection/rebuild block (Metal/CUDA probing, the `llama-cpp-python`
  rebuild-with-CMAKE_ARGS step) is skipped outright rather than run and then
  discarded, since there's no point probing for or rebuilding GPU support that will
  go unused anyway. Verified live: ran the script directly with `LLAMA_FORCE_CPU=1`
  set — confirmed the "running on CPU regardless of detected hardware" message
  printed, the actual server process launched with `--n_gpu_layers 0` (checked via
  `ps`), and it served a real request correctly — then restarted normally (unset)
  and confirmed the process went back to `--n_gpu_layers -1`.
- `frontend/server.py` — **middleware** FastAPI server: serves the static UI
  (`frontend/static/index.html`, `script.js`, `style.css`) and proxies `/api/*` to the
  backend (via `httpx`, base URL from `CROSSWORDFALCON_BACKEND_URL`, default
  `http://127.0.0.1:3001`) so the browser only ever talks to one origin. `run_Falcon.sh`
  binds it to `0.0.0.0` (LAN-reachable, e.g. from a phone on the same network) — the
  back end stays on `127.0.0.1` only, it's never meant to be reached directly.

  All three ports this project uses (frontend/middleware, backend, local LLM server)
  are configured in exactly one place, at the user's explicit request:
  `CROSSWORDFALCON_FRONTEND_PORT`/`CROSSWORDFALCON_BACKEND_PORT`/`LLM_PORT`, declared
  at the very top of `env.sh`/`env_default.sh` (each as `"${VAR:-default}"`, so a
  value already set in the calling shell's own environment is preserved rather than
  clobbered by sourcing the file) — 3000/3001/3002 by default, moved there from an
  original 8000/8001/8002 range after live-diagnosing a real collision: a VS Code
  helper process was also listening on `127.0.0.1:8000`, silently shadowing the real
  (otherwise perfectly healthy) frontend server for any browser connecting via
  `127.0.0.1` specifically (see the `project-best-practices` SKILL for the full
  diagnosis). `CROSSWORDFALCON_BACKEND_URL` and `LLM_BASE_URL` are themselves
  *derived* from `CROSSWORDFALCON_BACKEND_PORT`/`LLM_PORT` via shell variable
  interpolation right there in `env.sh` — changing a port number in one place updates
  every URL built from it automatically, rather than needing the same literal port
  edited separately in each of `run_Falcon.sh` (`BACKEND_PORT`/`FRONTEND_PORT`,
  read from the env vars with a hardcoded fallback matching the same default, for a
  caller that runs without ever sourcing `env.sh`), `run_llm.sh` (`LLM_PORT`, same
  fallback pattern), `frontend/server.py`'s own `BACKEND_URL` fallback, and
  `backend/clues.py`'s `DEFAULT_LLM_BASE_URL` fallback. Verified live: restarted all
  three processes after this change (killing the stale ones still bound to the old
  800x ports directly, since the updated scripts' own stop-existing-server logic
  now only looks for the new 300x ports) and confirmed each is healthy on its new
  port — `GET /` on the frontend (3000), `GET /api/health` on the backend (3001)
  and proxied through the frontend, and `GET /v1/models` on the LLM server (3002)
  all returned successfully, and `GET /api/system_info` through the backend
  correctly reported the running LLM model — confirming the whole chain (frontend
  → backend → LLM server) still resolves correctly end-to-end through the derived
  URLs, not just that each port individually opened.

  Proxies
  both `POST /api/generate` (fast — the backend responds immediately with a `job_id`,
  see above), `GET /api/generate/status/{job_id}` the frontend polls for progress, and
  `GET /api/system_info` (see `backend/system_info.py` below) for the info badge's
  tooltip. Every proxied call shares one `httpx.AsyncClient(timeout=PROXY_TIMEOUT_S)`
  (30s) — raised from a previous 10s/5s split (shorter for `/api/health`/
  `/api/system_info`, per the reasoning that only `/api/generate`/`/api/generate/status`
  needed a longer one) at the user's explicit request, after a sporadic
  `/api/generate/status` 502 was reported with *zero* corresponding trace in the
  backend's own access log — meaning that specific connection never reached FastAPI at
  all, pointing at either a backend restart or (more likely, given `generate_grid()` can
  run up to `PARALLEL_ATTEMPTS` parallel CSP-search processes, see `crossword_gen.py`)
  its single event loop being briefly too CPU-starved to `accept()` a new connection
  before the old, shorter timeout fired. One shared value everywhere now, rather than a
  per-endpoint split with no strong reason to differ. Verified live: restarted the
  frontend server to pick up the change, confirmed a real generation job still starts
  and polls correctly end to end through the updated proxy (`GET /api/health` and a
  real `POST /api/generate` → `GET /api/generate/status/{job_id}` round trip both
  returned normally). A blanket `@app.middleware("http")` sets
  `Cache-Control: no-store` (plus `Pragma`/`Expires`) on every response — static files
  and `/api/*` alike — so the browser never caches a stale `script.js`/`style.css`/etc.
  while the app is being edited directly. Static files are
  served via Starlette's `StaticFiles`, which 404s on anything not present in
  `frontend/static/` (including path traversal attempts) — keep that directory limited to
  the files the page actually needs. The page itself is a playable crossword, not just a
  solution viewer: the grid starts empty, the player types letters into selected cells
  (`script.js` tracks `userLetters` separately from the API's `solution`), with "Solution"
  and "Vérification" toggle buttons to reveal/check answers. After submitting the form,
  `script.js`'s `pollJob()` repeatedly polls the status endpoint (every
  `POLL_INTERVAL_MS`, 2000ms — raised from 700ms at the user's explicit request
  alongside the `PROXY_TIMEOUT_S` change above, since sub-second status freshness
  isn't actually needed and polling less often means fewer chances to catch the
  backend mid-stall during a heavy generation) and turns each `{code, ...data}` step
  into a localized
  message via `describeStep()` — falls back to the generic "generating…" message for a
  step code it doesn't recognize, so an older frontend build never breaks against a
  newer backend. Errors are localized the same way: a backend/proxy error carries a
  machine-readable `code` (`error_code` on a job, or `{"code": ...}` as
  `frontend/server.py`'s own `HTTPException` detail when it can't reach the back end at
  all) that `describeErrorCode()` maps to the UI's current language, falling back to
  whatever raw text the backend sent for a code it doesn't recognize. UI
  styling decisions live in the `style-guide` SKILL, not here.

  A real bug was reported live right after the "Continuer" button (see
  `backend/crossword_gen.py`'s `_serialize_resume_state`/`backend/app.py`'s
  `POST /api/generate/continue/{job_id}`) shipped: clicking it in the
  actual browser produced "Method Not Allowed" instead of relaunching a
  generation. Root cause: this file only ever proxies the small, explicit
  set of `/api/*` routes declared above (`proxy_generate`/
  `proxy_generate_status`/`proxy_generate_cancel`/etc.) — the new backend
  endpoint had no matching proxy route here at all, so a `POST` to
  `/api/generate/continue/{job_id}` fell through to the catch-all
  `app.mount("/", StaticFiles(...))` mounted at the very end of this file,
  which only ever serves `GET`/`HEAD` for on-disk files — hence the 405,
  not a connectivity problem with the back end at all (confirmed live: the
  same request sent directly to the back end's own port, 3001, worked
  correctly the whole time). Fixed by adding `proxy_generate_continue`, a
  `POST /api/generate/continue/{job_id}` route mirroring `proxy_generate_
  cancel`'s own shape exactly (same `PROXY_TIMEOUT_S`, same `httpx.
  RequestError` → 502 `{"code": "backend_unavailable"}` handling). This is
  a straightforward instance of a pattern this file's own design already
  makes easy to miss: every new backend endpoint needs an explicit,
  hand-written matching proxy route here, since there's no generic
  passthrough — only the routes actually written down ever proxy correctly,
  everything else silently falls through to the static-file mount instead
  of erroring in an obviously-backend-shaped way. Verified live: restarted
  both servers and confirmed `POST /api/generate/continue/doesnotexist`
  against the frontend's own port (3000) now returns the backend's real
  404 (`"job inconnu (expiré ou jamais existé)"`) instead of a 405; a full
  real generation submitted through the frontend (`POST /api/generate` →
  polled `GET /api/generate/status/{job_id}` to `"done"`) confirmed no
  regression to the ordinary, successful path from this same change.
- `frontend/static/i18n.js` — the internationalization config: every user-visible
  interface string (labels, buttons, headings, progress/status messages, error
  messages), for every supported language (fr/en/de/es/it), as one `I18N` object —
  pure data, no logic, loaded as a plain `<script>` before `script.js` in
  `index.html` (both are classic non-module scripts sharing the global scope, so
  order in `index.html` is what makes `I18N` available to `script.js`, not an
  import). `applyTranslations()` (in `script.js`) walks every `[data-i18n]` element in
  the DOM plus `document.title` on each language change; `describeStep()`/
  `describeErrorCode()` (also `script.js`) look up the rest (parameterized progress/
  error messages, which can't be plain `data-i18n` text since they need values
  interpolated in). Generated crossword content itself (the words/clues) is never in
  this file — that's written by the backend LLM in the request's `language`, a
  separate concern from the UI's own chrome. Keep this file current whenever any
  UI-visible string changes anywhere in `frontend/static/` — see the
  `project-best-practices` SKILL.

`data/wordlist_fr_full.tsv` is the CLI's default dictionary (`--wordlist`); the backend
picks among `data/wordlist_{fr,en,de,es,it}_full.tsv` per the request's `language` (see
`backend/app.py`'s `WORDLISTS`). There is no plain-text fallback list checked into the
repo; `load_wordlist` still accepts a free-text format as a fallback parser, but no file
of that kind ships here.

## Commands

Run everything with the venv's Python (`.venv`, Python 3.14). `pip install -r
requirements.txt` (or `./Install.sh`) installs `fastapi`, `uvicorn[standard]`, `httpx`.
`./Install.sh` also installs the `librsvg` system package (`rsvg-convert`) if missing
— a real runtime dependency, not just a dev tool: `backend/app.py` calls it after
every generated grid to save a `GRID_PNG/` PNG (see `backend/svg_export.py`
below), best-effort so a missing binary only logs a warning rather than failing the
request — which is exactly why this was easy to go unnoticed until a fresh machine
hit it.

```bash
# Full pipeline to rebuild one language's wordlist from scratch (only needed to
# refresh the source corpus/frequencies; data/wordlist_*.tsv are already checked
# into data/). Each language is independent — no particular build order required.
python3 build_sentence_corpus.py fr    # downloads OpenSubtitles+Wikipedia+Books+TED2013, filters
python3 build_wordlist_freq.py fr      # counts words, validates, writes wordlist_fr_full.tsv

# Optional: rebuild a language's gloss dictionary from scratch (large
# one-time download — see build_gloss_dictionary.py). Not needed for a normal
# clone: data/gloss_dictionary/*.jsonl is already checked into the repo.
python3 build_gloss_dictionary.py fr

# Generate a crossword grid from the CLI (defaults: 15x10, easy difficulty)
python3 backend/crossword_gen.py

# Common CLI options
python3 backend/crossword_gen.py --width 15 --height 15 --difficulty hard --seed 42

# Generate in another language (CLI has no --language flag, just point --wordlist
# at the language's dictionary; en/de/es/it are all pre-built in data/)
python3 backend/crossword_gen.py --wordlist data/wordlist_en_full.tsv

# Web UI: run both servers (separate terminals), then open http://127.0.0.1:3000
uvicorn backend.app:app --port 3001                       # 127.0.0.1 only, internal
uvicorn frontend.server:app --host 0.0.0.0 --port 3000    # LAN-reachable

# Or simply:
./run_Falcon.sh   # stops any server already running on 3000/3001, sources
                  # env.sh (LLM_BASE_URL/LLM_MODEL/LLM_API_KEY) if present,
                  # then relaunches both

# Local LLM for clue generation (default — see env.sh to use a cloud API instead).
# Downloads the GGUF into models/ on first run, then serves it on :3002. Works
# the same way on Linux and macOS (Metal/CUDA/CPU autodetected).
pip install -r requirements-llama.txt
./run_llm.sh
```

`backend/app.py` needs a reachable LLM at `LLM_BASE_URL` to generate word
definitions. Copy `env_default.sh` to `env.sh` (gitignored, not checked in) and
edit it — defaults to the local llama.cpp server above; comment/uncomment the
block in `env.sh` to point at a cloud API (e.g. Mistral) instead.
`run_Falcon.sh` sources `env.sh` automatically before starting the backend.

There is no test suite, linter, or build step in this repo.

## Architecture

`backend/crossword_gen.py` runs a two-phase pipeline, driven by `main()`:

1. **Black-square pattern generation** (`make_pattern`): places black cells one at a
   time, independently — **not** in 180°-symmetric pairs (dropped at the user's
   explicit request: with the CSP fill this fast, trying more patterns is cheap,
   and a non-symmetric search can reach a much lower black-cell ratio while
   staying structurally valid than pairing every cell with its mirror ever
   could). Picks each cell from a 32-cell look-ahead window rather than strict
   shuffle order, ranked by a single criterion — the row+column with, together,
   the fewest black cells already placed (`row_black[r] + col_black[c]`) — a
   deliberately simple, single-criterion design, at the user's explicit request,
   reverting a considerably more elaborate mechanism this area had grown into
   over this project's history (a cascade of strict-then-tolerant phases with
   per-length zone budgets and a per-phase row/column discount, plus an
   adjacency secondary tie-break) — see the `project-best-practices` SKILL for
   that whole iteration-by-iteration history, including everything that was
   tried and measured at each step before being reverted here. Falls back to
   shuffle order once the window is exhausted, so even this one criterion is a
   soft preference, not a hard constraint — it never makes a fillable ratio/size
   combination infeasible.

   Rejects any placement that violates `is_structurally_valid`, whose own rule
   is equally simple now: an *interior* white zone (bounded by a black cell on
   both sides) must be at least `min_interior_free` cells long (a parameter
   defaulting to 3); a zone touching the grid's own border on at least one
   side is always allowed, whatever its length and however many of them the
   grid ends up with — no count-based budget, no per-phase distinction, at the
   user's explicit request, replacing the `MAX_SHORT_ZONE_COUNT`-based
   tolerance system this project had built up over several iterations (again,
   see the SKILL for that history). A 1-letter zone is still never a real slot
   (`extract_slots` never creates one — it's purely a passthrough cell, filled
   only by the word crossing it in the other direction); a 2-letter zone, by
   contrast, *is* a real slot (`extract_slots`'s threshold `>= 2`) — a
   genuine, cluable 2-letter word ("et", "ou", "no", ...), which needed
   `build_wordlist_freq.py`'s own minimum word length lowered to match and
   both the wordlist and gloss dictionary rebuilt for all 5 languages. One
   invariant stays absolute, never folded into any tolerance rule regardless
   of `min_interior_free`: a white cell can never be short (1 letter) in
   *both* directions at once (a fully isolated cell, surrounded by black on
   all 4 sides) — such a cell would belong to no slot at all in either
   direction and could never receive a letter, a correctness bug, not a style
   choice.

   `_place_black_cells` reintroduces a preference for keeping black cells
   apart, at the user's explicit request — but this time expressed by
   *relaxing* `min_interior_free` for one specific placement attempt, rather
   than by a secondary tie-break criterion (the earlier, removed design):
   among the window, it first looks for the best candidate (by the row/column
   criterion) that is not adjacent to any existing black cell
   (`_has_black_neighbor`) and valid at `min_interior_free=3`; if the 3-cell
   requirement leaves no such isolated candidate, it's relaxed to 2, then to
   1, still restricted to isolated candidates at each level; only once even
   `min_interior_free=1` finds no isolated candidate does it accept adjacency
   at all, retrying the same three levels (3, then 2, then 1) without the
   isolation requirement. A grid can therefore legitimately end up with a
   *permanent* interior zone shorter than 3 cells — a deliberate trade-off,
   not a bug: the alternative would have been accepting adjacency instead,
   which is exactly what this feature exists to avoid when it can. This means
   the plain `is_structurally_valid(grid, rows, cols)` (default
   `min_interior_free=3`) is no longer a guarantee that holds for every grid
   `make_pattern` produces — only `min_interior_free=1` (the absolute
   invariants: no fully-orphaned cell, full connectivity) still is.

   This surfaced a real bug in `minimize_black_squares`, found by live
   testing before this feature was considered done: its own call to
   `is_structurally_valid(grid, rows, cols)` used the default
   `min_interior_free=3`, silently assuming every grid it's ever handed
   satisfies that — a fair assumption before this change, but no longer one.
   Since `minimize_black_squares` only ever *removes* black cells (which can
   only lengthen existing zones, never shorten one), a grid entering it with
   even a single legitimate short interior zone from generation would fail
   that check for literally any candidate removal, regardless of which cell
   was actually being removed — silently disabling the entire minimization
   step. Fixed by passing `min_interior_free=1` there instead, matching what
   `minimize_black_squares` actually needs to preserve (connectivity, no
   orphaned cell), not the aesthetic preference that's now only a soft target
   at generation time. Verified live: two unit tests on hand-crafted grids
   confirming the core trade-off directly — a non-adjacent candidate
   preferred over an adjacent one even when both are valid at the strict
   level, and a non-adjacent candidate requiring relaxation to
   `min_interior_free=2` still preferred over an adjacent candidate valid at
   the strict level 3; a sweep of 1920 combinations (60 seeds × 8 ratios from
   5% to 40%, across 4 grid shapes) confirmed the true invariant
   (`min_interior_free=1`) holds with zero failures, while the plain default
   check now legitimately fails on a large share of grids at higher ratios —
   broken down by ratio on the 15×10 grid, the relaxation triggers on 0/60
   seeds at 5%/10%, rising to an average of ~13 interior short zones per grid
   at 35-40%, confirming the trade-off is density-driven (rare when the
   grid is sparse, common only once isolation becomes structurally hard) and
   not a flat, indiscriminate relaxation. A direct before/after measurement of
   the isolation rate itself (the number this whole feature was requested to
   improve) showed a large, real improvement: 74.9% of black cells end up
   fully isolated with this feature, up from the 23.7% baseline measured
   without it. A real `generate_grid()` run (15×10, seed 2, easy) succeeded in
   221.94s — every word matching the solution, `min_interior_free=1` valid
   (the true invariant holds) while the plain default check is legitimately
   `False` on this same grid, exactly as expected under the new design.

   A **pre-fill phase** (`_prefill_unfillable_slots`) runs ahead of the
   ratio-based placement above, at the user's explicit request, and was kept
   through the simplification above: as long as the grid has a slot whose
   length has fewer than `PREFILL_MIN_WORD_COUNT` (10 — raised from 1 at the
   user's own explicit follow-up request, see below) candidate words in the
   word list (`available_lengths`,
   derived once per attempt from `_worker_index` in `_pattern_attempt`), it
   keeps placing black cells with this exact same look-ahead algorithm — one
   cell at a time, re-checking after each — until that's no longer the case, or
   until it can no longer place another cell trying. Cells placed this way are
   never counted against `black_ratio`'s own target — placement below only
   starts counting from 0 once pre-fill returns — so a structurally-necessary
   pre-fill cell never eats into the ratio budget the rest of the grid still
   needs; the final grid can legitimately end up denser than the nominal ratio
   alone would suggest, by design. This pre-fill phase itself relies on
   `_place_black_cells` returning every candidate not yet placed (tried-and-
   rejected ones *and* never-tried ones) rather than just the tried-and-
   rejected subset — a real bug found and fixed while building this feature: a
   level succeeding on its very first try returned an empty "rejected" list,
   silently discarding the rest of the original candidate pool even though it
   was still perfectly usable, collapsing pre-fill to a single placed cell
   regardless of how many oversized slots remained. Harmless for `make_pattern`'s
   own ratio-based placement (which only cares about the return value when its
   own candidate pool is genuinely exhausted, at which point nothing was ever
   silently dropped anyway), but load-bearing for pre-fill's own one-cell-at-
   a-time calls.

   Verified live after this whole simplification: unit tests on hand-crafted
   grids for the new `is_structurally_valid` rule (an interior 1- or 2-letter
   zone rejected, a 3-letter interior zone accepted, a border zone of any
   length accepted — including 10 independent border zones on one grid, no cap
   at all, unlike the removed budget system); a sweep of 1920 combinations (60
   seeds × 8 ratios from 5% to 40%, across 4 grid shapes — 10×15, 15×15, 6×6,
   20×20) with zero `is_structurally_valid` failures; the pre-fill mechanism
   re-verified directly (a realistic `available_lengths = {2..12}`, missing
   only the 3 longest lengths a 15-wide grid could ever need, resolving cleanly
   on 20/20 seeds, same as before the simplification); a real `generate_grid()`
   run (15×10, seed 2, easy) succeeding in 242.35s — structurally valid, every
   one of its 53 words matching the solution, comparable to every prior
   version's timing, not a regression; and, through the real pipeline
   end-to-end, the real French word list artificially truncated to 8-letter
   words or shorter still resolved cleanly in 16.21s with zero slots longer
   than 8 letters, confirming the pre-fill wiring survived the simplification
   intact.

   `PREFILL_MIN_WORD_COUNT` was raised from 1 to 10, at the user's own explicit
   follow-up request: a length with only a handful of words anywhere in the
   whole word list (the real French list has exactly this for lengths 22-25 —
   5, 1, 1, and 1 word respectively) used to count as "available" the moment
   it had even a single word, but a slot that thin is still very likely to
   make the CSP fill hard or outright impossible in practice, especially once
   more than one slot of that same length competes for the same tiny pool.
   `_pattern_attempt`'s own `available_lengths` computation changed from
   `if data["words"]` (any word at all) to
   `if len(data["words"]) >= PREFILL_MIN_WORD_COUNT`; `_has_slot_without_
   candidate`/`_prefill_unfillable_slots` themselves needed no change at all
   — both only ever check set membership against whatever `available_lengths`
   they're handed, so the entire behavior change lives in how that set gets
   built, not in the pre-fill mechanism itself. Verified live: a real
   `_pattern_attempt` run on a 22×22 grid (a size that actually reaches those
   low-count lengths) across 5 seeds, using the real, untruncated French word
   list — zero slots at any of the 22-25-length range survived pre-fill in
   any of the 5 runs, confirming the raised threshold correctly drives the
   mechanism to eliminate them now, unlike before (when a length with even
   1 word, like 23/24/25, would have passed as "available"); a real
   `generate_grid()` run on the standard 15×10 grid (seed 2, easy) succeeded
   in 163.81s — every word matching the solution, confirming no
   regression to the common case, where every length the grid could ever need
   is already covered by well over 10 words in the real word list, so the
   raised threshold changes nothing in practice for a normal-sized grid.

   Starts from a black-cell ratio of **0** (`--black-ratio`, default `0.0` — lowered
   from a previous 0.05 at the user's own explicit follow-up request, itself lowered
   from an even earlier 0.22 default before that), since `make_pattern`'s own
   pre-fill phase (see above) already adds whatever black cells are structurally
   necessary to guarantee enough candidate words per slot before this ratio-based
   step ever runs — a ratio target of 0 simply means "don't add any *more* cells
   beyond what pre-fill already placed," not "the grid has zero black cells no
   matter what." If the pattern that results still can't be filled, the ratio-based
   retry loop below picks up exactly where it always has, escalating the ratio
   until something works. Retries
   with an increasing ratio (up to `--attempts` times, default 40 at the time — since
   raised to 200, see further below — each an
   independent *palier*/step) until a fillable pattern is found. The increment itself
   went through several live-tuned revisions: the original +0.02 was widened to +0.05
   when it turned out to make nearly every attempt below ~20-30% hit `try_fill`'s own
   search deadline without resolving either way (not proven infeasible, just
   inconclusive), then narrowed to +0.03 at the user's explicit request (a
   finer-grained search of the low-ratio region — this traded some wall-clock time for
   that finer granularity, since more steps are typically needed to reach a fillable
   ratio: measured 70.6s on the same 15×10 grid/seed used throughout this whole
   exploration, up from the ~42-51s seen with +0.05), and — after several other,
   independent changes made to `Filler._backtrack`'s own selection rule and to
   `make_pattern`'s heuristic since that measurement (all documented above/in the
   project-best-practices SKILL) — narrowed again to **+0.02** at the user's explicit
   request, back to the very first value tried in this whole exploration. Verified
   live rather than assuming the original +0.02-was-too-slow finding still holds
   unchanged after everything else that's since been revised: a real `generate_grid()`
   run (15×10, seed 2) succeeded in 191.79s, still comfortably within this whole
   exploration's measured range and no worse than the finer-grained +0.03 step it
   replaced.

   The starting ratio's own drop from 0.05 to 0.0 was verified the same way, not
   just assumed harmless because pre-fill exists: two real `generate_grid()` runs
   on the standard 15×10 grid (seeds 2 and 7) succeeded in 225.43s and 225.63s
   respectively — both comfortably within the range already measured throughout
   this whole exploration for a *non-zero* starting ratio, confirming the change
   doesn't meaningfully slow down the common case (a handful of extra +0.02 paliers
   climbing from 0% costs little next to the CSP search itself, which dominates the
   total time either way) while still saving the wasted cells a flat 5%-or-higher
   floor would have added on a pattern that pre-fill (or an even lower ratio) could
   already handle on its own. At each
   step, `PARALLEL_ATTEMPTS` independent attempts — different `random.Random`
   seeds, each its own full motif + CSP fill — run concurrently in separate
   processes (`concurrent.futures.ProcessPoolExecutor`, `_pattern_attempt`/
   `_init_worker`), at the user's explicit request after noting the machine was far
   from saturating its CPU with only one attempt in flight at a time; the pool waits
   for all of them before deciding whether the step succeeded (bounded by the
   *slowest* one, not the fastest — a deliberate simplicity/safety tradeoff over an
   early-return design, which would need to either wait for or explicitly kill
   still-running worker processes). `PARALLEL_ATTEMPTS` itself is configurable via
   `CROSSWORDFALCON_PARALLEL_ATTEMPTS` (`env.sh`/`env_default.sh`, near the top of
   the file, right after the port variables — see `frontend/server.py`'s own
   entry below — a non-LLM setting placed there deliberately, at the user's explicit request; sourced by
   `run_Falcon.sh` before starting the backend, same as every
   other `env.sh` value), default 10 — it was a hardcoded 5 (with no override
   mechanism at all) when the specific timings cited in this section were measured,
   so those numbers describe a 5-way batch, not today's 10-way default; re-measure
   before assuming they transfer directly. When more than one attempt in a step
   succeeds, the one kept is no longer just whichever finished first: at the user's
   explicit request, it's whichever maximizes the sum of squares of every one of its
   words' lengths (`sum(len(slot) ** 2 for slot in result[0])`) — a scoring choice
   that rewards concentrating letters into a few long words over spreading the same
   letter count across many short ones (a single 10-letter word scores 100; ten
   2-letter words covering the same 20 letters only scores 40). See the
   `project-best-practices` SKILL for the full measured progression across all
   changes to this function.

   **The "wait for the slowest attempt" design described just above was
   reversed much later in this project's history**, at the user's explicit
   request, quoting this exact paragraph back and asking to "interrupt every
   search as soon as one search finishes (success or failure) to move on to
   the next palier." A new shared `multiprocessing.Event`,
   `attempt_done_event` (`generate_grid`, created once per `generate_grid()`
   call, same technical reason as `cancel_event`/`batch_abandoned_event`
   above — a `multiprocessing.Event` passed as a per-task argument to
   `executor.submit(...)` raises a `RuntimeError` on macOS's "spawn" start
   method, so it must go through the pool's `initializer` instead — and
   cleared at the start of every palier since its meaning only ever applies
   to the palier currently running), is `.set()` right after the very first
   of a palier's `PARALLEL_ATTEMPTS` futures completes (`concurrent.futures.
   as_completed`), so every other still-running attempt of that same batch
   observes it at its own next checkpoint and stops. `Filler` gained a
   matching `attempt_done_event` parameter (and a new `self.
   interrupted_by_sibling` flag, distinct from — but internally reusing, as
   its own fast short-circuit — the pre-existing `self.abandoned`), checked
   in `_backtrack` at the same `PALIER_ATTEMPT_DONE_CHECK_INTERVAL` (500)
   cadence as the other two signals (`cancel_event`/`batch_abandoned_event`)
   for the same reason (a `multiprocessing.Event.is_set()` call is cheap but
   not free to repeat at every one of the hundreds of thousands of nodes a
   search can visit). `try_fill`'s `diagnostics["reason"]` gained a new
   value, `"interrupted_other_attempt_done"`, checked ahead of
   `"abandoned_too_unfillable"` in the reason cascade (since this checkpoint
   also sets `self.abandoned` as its fast-path reuse, it would otherwise be
   misreported as the unrelated 30%-unfillable reason).

   Unlike `batch_abandoned_event` (deliberately wired only into
   `_pattern_continue`, never `_pattern_attempt` — see that global's own
   docstring for the real, previously-found regression explaining why),
   `attempt_done_event` is threaded into *both* `_pattern_attempt` and
   `_pattern_continue`'s own `try_fill` calls: unlike
   `batch_abandoned_event`, which announces a judgment about *this specific
   attempt's own pattern* (unreliable across `_pattern_attempt`'s
   independent, differently-shaped patterns), `attempt_done_event`
   announces nothing about pattern quality at all — only that the palier's
   outcome is already decided elsewhere, which is equally true regardless of
   whether this attempt's own pattern was independent or shared.

   `generate_grid`'s own outcome-collection loop still drains every one of
   the `PARALLEL_ATTEMPTS` futures via `.result()` (there is no way to
   reclaim a `ProcessPoolExecutor` worker process without either waiting for
   its current task to return or forcibly killing it — never done anywhere
   in this file, see `cancel_event`'s own cooperative, non-destructive
   design) — but that wait is now bounded to roughly
   `PALIER_ATTEMPT_DONE_CHECK_INTERVAL` more checks per straggler instead of
   its full `deadline_checks` budget, the same kind of bound already
   measured for `cancel_event` (~0.79s in that case). A future that raises
   `GenerationCancelled` (the user's own "Stop" button, a different and
   higher-priority signal) still propagates immediately, exactly as before
   this feature.

   This collapses, in the typical case, the palier's previous "compare up to
   `PARALLEL_ATTEMPTS` outcomes, pick the best" design down to "take
   whichever answered first" — since every attempt but the first-completed
   one is now cut short almost immediately, `successes`/`failed_unique`/
   `failed_pairs` will typically hold just that one real outcome per palier.
   Three downstream mechanisms were adjusted to stay correct under this
   collapse rather than silently degrade: (1) the failed-attempt dedup loop
   now filters out any outcome tagged `"interrupted_other_attempt_done"`
   before building `failed_unique`/`failed_pairs` (a straggler's own
   `impossible_cells` count — the sort key `failed_pairs` uses — would
   otherwise be a meaningless, often misleadingly *low* number, since it
   barely had time to explore before being cut off, not a genuine measure of
   how close it got) — the first-completed outcome is, by construction,
   never itself interrupted this way (the event is only set right after
   it's already in hand), so this filter can never leave `failed_unique`
   empty whenever there was at least one real failure; (2) `total_attempts_
   tried` still sums `checks` over every raw failed outcome, interrupted
   stragglers included — real backtracking work was done before each was
   told to stop, so it still counts toward this "grilles réellement
   essayées" tally exactly as before; (3) the "force cleanup when every
   attempt of this palier was independently abandoned via the 30% rule"
   check now reads the same *filtered* list instead of the raw one — checked
   against the raw, unfiltered list, this rule would almost never fire
   anymore (a palier will typically have several `"interrupted_other_
   attempt_done"` entries mixed in, and `all(...)` over that list would
   almost always be `False`); in practice this now usually reduces to "force
   cleanup when the one real failure that triggered the interruption already
   judged its own pattern hopeless" — a faster-firing version of the exact
   same original intent, no longer needing all `PARALLEL_ATTEMPTS` attempts
   to each independently reach that same conclusion before the palier is
   even allowed to finish.

   Verified live: an isolated `Filler._backtrack` test (`self.checks` preset
   to one call short of `PALIER_ATTEMPT_DONE_CHECK_INTERVAL`, the event
   pre-set) confirmed the very next call returns `False` with
   `self.interrupted_by_sibling` now `True` and `self.abandoned` also `True`
   (its fast-path reuse) — while an otherwise-identical `batch_abandoned_
   event`-triggered call correctly leaves `interrupted_by_sibling` `False`,
   confirming the two causes stay distinguishable; a call with the event
   left unset showed neither flag set. A direct, production-path comparison
   through `_pattern_attempt` itself (the real French wordlist, the standard
   15×10 grid, `deadline_checks=300_000`) showed a baseline (no interrupt)
   attempt running 64,993 checks in 3.27s before `abandoned_too_unfillable`,
   versus 0.17s (reporting `interrupted_other_attempt_done`) when the event
   was pre-set — confirming the real wall-clock stop is fast even though the
   internal `checks` counter (25,559 in that run) keeps climbing for a while
   after abandonment, an already-established, accepted characteristic of
   reusing `self.abandoned` as the fast-path flag (the in-flight recursive
   backtracking calls already queued up at each level of the call stack
   still each cost one quick "return `False`" as they unwind, exactly as
   already true for the pre-existing `batch_abandoned_event`/30%-rule
   triggers of the same flag — not a new cost introduced by this feature).
   Two full end-to-end `generate_grid()` runs on the standard 15×10
   benchmark completed in 17.3s (seed 2) and 41.0s (seed 7) — both `0`
   mismatches, `0` empty white cells, and dramatically faster than every
   previously recorded measurement of this same benchmark throughout this
   project's history (which had ranged from tens to hundreds of seconds
   depending on the mechanism version in place at the time) — direct
   confirmation the interruption meaningfully shortens real generation time,
   not just a synthetic one. A `difficulty="hard"` (full lexicon, no
   artificially small dictionary — see the `project-best-practices` SKILL's
   own permanent rule against that) 10×10 run also succeeded in 20.9s, `0`
   mismatches, with its logged `pattern_attempt_failed` reasons showing a
   genuine mix of `"abandoned_too_unfillable"` and `"blocked_on_excluded_
   slot"` — confirming `last_diag`'s own reported reason (always among the
   first-completed, never-interrupted outcomes) stays meaningful and never
   shows the new `"interrupted_other_attempt_done"` value itself, exactly as
   intended.

   **This "interrupt after the very first attempt" design was immediately
   refined at the user's own explicit follow-up request**, quoting the
   paragraph above back and asking instead for "à partir de 30% des
   tentatives qui se terminent... interrompre toutes les tentatives" — wait
   for `PALIER_ATTEMPT_INTERRUPT_FRACTION` (0.30) of the batch to complete
   (success or failure) before interrupting the rest, rather than cutting
   off after literally the first one. `interrupt_threshold =
   max(1, math.ceil(PALIER_ATTEMPT_INTERRUPT_FRACTION * len(futures)))` —
   `math.ceil` (never floor/round) so a genuine 30%-or-more of completions
   is required rather than possibly rounding down and interrupting after
   just one anyway; `max(1, ...)` as a floor so a very small
   `PARALLEL_ATTEMPTS` can never need more than 1 completion. With the
   default `PARALLEL_ATTEMPTS=10`, this resolves to 3. The outcome-
   collection loop's own `attempt_done_event.set()` call moved from firing
   at `len(outcomes) == 1` to `len(outcomes) == interrupt_threshold` — no
   other part of the mechanism changed (the checkpoint cadence, the
   `interrupted_by_sibling` flag/reason, the `failed_real` filtering, all
   unaffected). This gives `successes`/`failed_real` up to `interrupt_
   threshold` genuine candidates per palier instead of always exactly one,
   restoring a bit more of the original "compare several, pick the best"
   signal (the sum-of-squares choice among successes, and the "were all of
   them independently judged hopeless" cleanup-forcing rule now gets an
   actual multi-attempt sample again, rather than trivially checking just
   one) while still cutting off the remaining ~70% majority early. Verified
   live: an isolated check of the threshold formula across several
   `PARALLEL_ATTEMPTS` values (1→1, 2→1, 3→1, 4→2, 5→2, 10→3, 20→6)
   confirmed it resolves as expected; two full end-to-end `generate_grid()`
   runs on the standard 15×10 benchmark completed in 35.7s (seed 2, 53
   words, 44 black cells) and 13.7s (seed 7, 46 words, 52 black cells) —
   both `0` mismatches, `0` empty white cells, still comfortably faster than
   this benchmark's pre-interruption history despite now waiting for 3
   completions instead of 1; the same `difficulty="hard"` 10×10 scenario
   (seed 7) succeeded again in 20.5s, `0` mismatches, with its logged
   reasons now also including a `"deadline_exceeded"` alongside
   `"abandoned_too_unfillable"`/`"blocked_on_excluded_slot"` — a wider mix
   than the single-completion version showed, consistent with `last_diag`
   now potentially reflecting whichever of up to 3 real completions ranked
   best rather than always the literal first.

   **The ratio-escalation mechanism described in the two paragraphs above
   (the +0.02-per-palier increment, up to 45%) was removed entirely**, at
   the user's own later explicit request, once the cross-palier retry-seed
   mechanism (`_build_retry_seed`, further below) existed: "Ne plus faire
   progresser le taux de remplissage (toujours à zéro). Le principe de
   pré-remplissage (avec au moins 10 solutions par emplacement) après
   conservation de la grille précédente, devrait suffire à faire progresser
   la grille." `ratio` (initialized once from `black_ratio`, `0.0` by
   default) is no longer ever incremented across paliers — every palier now
   runs at the *same* target ratio, relying entirely on two mechanisms that
   didn't exist when the increment was first tuned: the pre-fill phase
   (guaranteeing `PREFILL_MIN_WORD_COUNT` real candidates per slot) and
   `_build_retry_seed` carrying forward whatever was already resolved at the
   previous palier (rather than starting the next palier from a blank grid)
   — between them, a fresh, structurally-necessary pattern is generated at
   every palier without needing to artificially densify the grid step by
   step to eventually stumble onto a fillable shape. The whole tuning
   history above (the two paragraphs on the increment size and the starting
   ratio) is kept as a historical record of how this project arrived at
   +0.02/0.0 before the mechanism was removed outright, not a description of
   current behavior. Verified live across the standard benchmark (15×10
   seeds 2/7), a deliberately hard 10×10/400-word run, 18×12 seed 30, and
   25×15 seed 2 — every one still succeeds, 0 mismatches, confirming
   pre-fill plus the carried-forward seed alone is enough to make genuine
   progress palier to palier without any ratio escalation at all.

   A fixed, non-escalating extra density draw was reinstated after pre-fill
   later in this same investigation, at the user's explicit request:
   "rétablir un tirage de 5% de nouvelles cases ajoutées (5% par rapport au
   nombre de cases blanches restantes)." Deliberately different from the
   ratio-escalation mechanism just removed: `POST_PREFILL_BLACK_FRACTION`
   (5%) is of the cells still white right *after* pre-fill (not of the
   grid's total size), and it never escalates palier to palier — the same
   flat 5% applies every time, on top of whatever pre-fill already placed.
   `make_pattern` now recomputes `placed` directly from the grid's own
   state right after pre-fill (rather than reusing the stale pre-pre-fill
   count), so the ratio-based `_place_black_cells` call correctly treats
   pre-fill's own cells as already spent instead of double-counting them.

   Reinstating this exposed a real, previously-dormant bug immediately:
   with a real end-to-end test on the standard 15×10 benchmark (seed 2, a
   case that had succeeded reliably throughout this project's history), the
   first palier still searched normally, but *every single palier after
   that* failed almost instantly (`checks` of 5-7 per parallel attempt,
   across 36 consecutive paliers) instead of exploring a real search space.
   Root cause: `_new_black_cell_breaks_locked_slot` (the preventive filter
   protecting `_place_black_cells`'s candidate acceptance) only ever checked
   a candidate's resulting side-runs against *locked letters* — it never
   checked a run's plain *length* against `available_lengths` at all, even
   though `available_lengths` was never even passed to it. As long as the
   ratio-based phase added zero cells (black_ratio pinned at 0.0, no 5% draw
   yet), this gap was never exercised — pre-fill's own targeted placement
   already respects `available_lengths` directly. The moment a real,
   nonzero density draw runs again, it's free to cut a slot into a length
   the dictionary barely covers, with nothing to stop it. Fixed by
   threading `available_lengths` through `_place_black_cells` into
   `_new_black_cell_breaks_locked_slot`, which now rejects a candidate if
   *any* of its 4 side-runs (≥2 cells) has a length outside
   `available_lengths` — checked unconditionally, before even looking at
   locked letters, so it engages on the very first palier too, not just
   once locked content exists. Verified live: re-ran the exact failing
   scenario after the fix — palier 1 still searches normally
   (`deadline_exceeded`, 211,238 checks), and every later palier now shows a
   genuine mix of quick rejections and real, substantial searches (11,183;
   836; two more `deadline_exceeded` runs past 200,000 checks each) rather
   than a uniform instant failure — succeeding at palier 13, 144.7s total,
   48 words, 33 black cells.

   `POST_PREFILL_BLACK_FRACTION` was then raised from 0.05 to **0.10**, at
   the user's own explicit, immediate follow-up request (deployed directly,
   without waiting on a background sanity check that was still running at
   the time — stopped on request rather than left to finish).

   `generate_grid`'s own `attempts` default (and the CLI's `--attempts`
   default) was raised from **40 to 200**, at the user's explicit request:
   "certains cycles vont très vite (grille très vite bloquées). Augmenter à
   200 essais avant de capituler." Some grids genuinely need more than 40
   paliers of the cross-palier retry mechanism to work through a run of
   quick, unproductive cycles before finding a workable state — a ceiling
   of 40 could give up on a grid that a longer run would have resolved.
   `backend/app.py` never passes `attempts` explicitly, so this change
   takes effect for every real web-UI generation immediately, not just the
   bare CLI.

   A "patch the same pattern in place on fill failure" mechanism was tried this
   session and then reverted, at the user's explicit request, once live measurement
   showed it was too costly even after being capped: on a fill failure,
   `_pattern_attempt` would add a black cell restricted to whichever slots had
   actually held a letter at the deepest point the search reached (rather than
   letting the outer palier loop regenerate an entirely fresh pattern), then retry —
   up to a capped number of times per attempt. A single isolated worker chained 44
   such patches over 448.77s and still failed; even capped at 5 patches per attempt,
   full end-to-end `generate_grid()` runs on the standard benchmark seed swung
   between under 600s and over 1500s (too costly and too unpredictable to keep) — the
   user asked for the whole mechanism to be removed rather than tuned further. Fully
   reverted: no `add_restricted_black_cell` function, no `Filler.best_assignment`
   tracking, no `try_fill` `filled_cells` diagnostic, no `MAX_RESTRICTED_PATCHES`
   constant — `_pattern_attempt` is back to exactly one `make_pattern` + `try_fill`
   call per attempt, letting the ratio-ladder loop below handle every failure by
   regenerating a fresh pattern at the next palier, same as before this session. See
   the `project-best-practices` SKILL for the full episode (what was tried, measured,
   and why it was removed).

   A **different** cross-palier retry mechanism was introduced later, at the user's
   explicit request, with a precise 3-step recipe — deliberately not a revival of the
   reverted patch mechanism above, which retried the *same* attempt in place, one
   added black cell at a time, restarting the CSP search from scratch on every single
   patch: this one instead carries confirmed progress *forward* across paliers, and
   never restarts the same attempt — each palier is still exactly one fresh
   `make_pattern` + `try_fill` call, same as ever, just no longer starting from a
   blank grid once a previous palier has something worth keeping. At the end of a
   failed palier (`generate_grid`'s main loop): (1) among the `PARALLEL_ATTEMPTS`
   parallel attempts that all failed, take the one judged "best" (originally the one
   with the most real letters placed, `assigned_letter_count` — later changed to the
   one with the *fewest black cells* instead, at the user's own explicit follow-up
   request; see the dedicated entry much further below for exactly why and when) and
   remove, from *that* attempt's best-reached state, every assigned word directly
   connected to (crossing) one of its "impossible" slots
   (`Filler.impossible_zone_slots`, a new slot-index-returning sibling of the
   existing `impossible_zone_cells`) — one level only, no cascading removal, at the
   user's explicit request; (2) whatever real letters remain covered by a surviving
   assigned word become locked, pre-defined constraints for the next palier; (3)
   every black cell of that same attempt's pattern that is not a true *boundary* of a
   surviving word is reopened (turned white). Step 3 was refined at the user's own
   explicit follow-up request from an initial, cruder "any orthogonal adjacency to a
   confirmed letter" rule (the same 4-neighbor convention as `_has_black_neighbor`
   elsewhere in this file) to this more surgical one: a black cell only needs to stay
   black if it is the cell immediately *before* a surviving word's first letter or
   immediately *after* its last letter, in that word's own direction (never the
   perpendicular one) — a black cell merely sitting above/below a horizontal word's
   *middle* letter (or left/right of a vertical word's middle letter) doesn't bound
   that word at all and has no remaining reason to stay black. The cruder adjacency
   rule kept far more of the grid locked down than actually necessary, which (see
   the dedicated entries below) turned out to measurably starve the next palier's
   `PARALLEL_ATTEMPTS` of any real room to differ from one another. `_build_retry_seed`
   implements exactly these three steps and returns `(new_grid, confirmed_letters)`,
   threaded into the *next*
   palier's `PARALLEL_ATTEMPTS` calls as `_pattern_attempt`'s new `seed_grid`/
   `locked_letters` parameters (both `None` for the very first palier, and for every
   palier immediately following a success — there is nothing to carry forward once
   the search either finds a full solution or has not yet failed at all).
   `make_pattern` itself gained matching `seed_grid`/`locked_cells` parameters:
   given a seed, it continues placing black cells on top of it (row/column black
   counts and the already-placed count initialized from the seed's own cells, rather
   than from zero) instead of starting from an all-white grid, and its placement
   candidates exclude `locked_cells` entirely — verified live and necessary, not
   just assumed: without this exclusion, a white cell holding a real confirmed
   letter from the previous palier would be just as eligible for a *new* black cell
   as any other still-white cell, silently destroying that confirmed letter.
   `locked_letters` is merged into `_pattern_attempt`'s own `forced_letters`
   (`{**forced_letters, **locked_letters}`, locked always winning over
   `sample_letter_biases`'s statistical guess at the same cell) — reusing
   `Filler._domain`'s existing forced-letter mechanism as a hard constraint rather
   than adding a second, parallel constraint system: since a locked letter is
   genuine ground truth (not a guess), and `_domain` already applies a per-cell
   forced letter to *every* slot touching that cell regardless of assignment order,
   a fully-locked slot's domain naturally narrows to exactly the one word it already
   was — including across a pattern change that gives it entirely new crossing
   neighbors, which cell-level locking (rather than trying to carry over a
   slot-indexed assignment) handles for free, with no special-casing needed for
   slots whose boundaries shifted between paliers. `try_fill`'s `diagnostics` gained
   two fields needed for this — `assignment` (`Filler.best_assignment` itself, not
   just the letter-grid derived from it) and `impossible_slots` (slot indices, not
   cells) — since `_build_retry_seed` needs the real slot/word structure directly,
   not the letter-grid the UI preview shows (which also overlays purely statistical
   `forced_letters` hints, indistinguishable there from a real placed letter).

   Verified live, extensively, before trusting this over the previously-reverted
   design: hand-built unit tests confirmed `_build_retry_seed`'s own logic in
   isolation (a word crossing an impossible slot is correctly removed; an unrelated
   word elsewhere in the grid survives untouched; a black cell that is a true word
   boundary stays black on both sides of a mid-grid word, while a far-away black
   cell and one merely adjacent to a *middle* letter — not a boundary at all — both
   reopen; a word spanning a full row/column, with no in-bounds boundary cell at
   all, correctly reopens every surrounding black cell) and that `make_pattern`
   never places a black cell on a locked cell (20 random trials, a
   fully-locked row, zero violations). A specific edge case the user raised directly
   — new black cells added on top of a seed could close off every remaining free
   cell, leaving a grid that's already fully determined by locked letters alone,
   with no CSP "search" left to do — was tested explicitly with a real dictionary
   word: seeded a grid with one 5-letter slot fully locked to a real word and
   nothing else white, at a black-cell ratio high enough to blacken everything
   else; `make_pattern` correctly left the locked row untouched (no candidates
   available there at all) and `try_fill` resolved it in 2 checks, `reason:
   "solved"`, recovering the *exact* original word — confirming this degenerate
   case is already handled correctly by the existing domain-filtering machinery,
   with no special-casing needed. Two real, non-mocked end-to-end `generate_grid()`
   runs then confirmed the whole mechanism under genuine multi-palier failure/
   recovery, not just synthetic pieces: a deliberately hard 10×10 grid restricted to
   400 words (difficulty="hard", so no gloss filtering) went through 8 real failed
   paliers before succeeding — 12.66s total, 37 words, 0 mismatches between the
   placed words and the solution grid; the standard 15×10 benchmark (seed 2, easy,
   the full real French word list) went through 10 real failed paliers before
   succeeding — **76.98s**, against this exact same seed/shape's previously
   documented 150-290s range across this project's whole history of tuning this
   area — a real, substantial speed-up, not just a "no regression" result, and the
   opposite outcome from the previously-reverted patch mechanism (which made things
   slower and less predictable). 0 mismatches confirmed again on this run too. A
   second seed on the same standard benchmark (seed 7) confirmed this wasn't a
   one-off: 9 real failed paliers, **98.68s** total — against this seed's own
   previously documented 225.63s — 57 words, 0 mismatches, a consistent
   speed-up rather than a lucky single measurement.

   Two real problems surfaced once this mechanism was actually watched across many
   consecutive paliers of the same generation, both reported directly by the user
   and both traced to the same root cause: **as more of the grid gets locked down
   round after round, the still-open area can shrink enough that every one of the
   `PARALLEL_ATTEMPTS` parallel attempts at a palier converges on the exact same
   pattern and the exact same failure** — reproduced live with a dedicated
   diagnostic script: a deliberately hard 10×10/400-word run showed a clean 6
   distinct examples per palier for the first 5 paliers, then dropped to exactly 1
   distinct grid (out of 10 parallel attempts) from the 6th palier onward. This broke
   two things at once: (1) `total_attempts` (see above) counted all 10 of those
   identical attempts as 10 separate failures, when only 1 distinct grid had actually
   been tried; (2) the up-to-6-examples preview showed the same grid repeated 6
   times instead of 6 meaningfully different attempts. Fixed with a **dedup pass**,
   at the user's explicit request: before anything else at a failed (or partially-
   successful) palier, `generate_grid()` now deduplicates the parallel outcomes by
   `(pattern grid, best_assignment)` — two attempts count as the same grid only if
   *both* are identical, not the pattern alone (in case an identical pattern
   happened to resolve to different letters) — and both `total_attempts` and the
   examples-selection/sorting now operate on this deduplicated list. Computed
   *before* checking whether the palier succeeded (a first version of this fix
   computed it only in the failure branch, which silently stopped counting the
   winning palier's own failed attempts entirely — caught by re-running the
   existing mixed-outcome unit test, which expects `total_attempts=28`, not `20`).

   The *cause* of the convergence itself was fixed separately, at the user's own
   further explicit request: `_build_retry_seed`'s step 3 (which black cells stay
   black) was refined from "any cell orthogonally adjacent to a confirmed letter"
   to "only a true word-boundary cell" (see the step-3 description above) — the
   old rule kept far more of the grid locked than any surviving word actually
   needed, which is what was starving later paliers of room to differ at all.
   Verified live, with the same diagnostic script re-run after this fix: the
   10×10/400-word scenario now shows a clean 6 distinct examples at *every* palier
   (no drop-off at all across the 5 paliers it took to succeed this time), and the
   number of cells flagged "forced" in the best example per palier stayed a modest,
   varying fraction of the grid's white cells (`0, 15, 0, 21, 25` out of ~80 white
   cells across paliers 1-5) rather than anywhere close to "every cell" — both
   directly contradicting what the old, cruder rule was doing. The fix also sped
   the same hard scenario up further (12.62s, still 0 mismatches) and is re-verified
   against the standard 15×10 benchmark (see timing above, re-measured after this
   fix) for a full before/after comparison. See the `project-best-practices` SKILL
   for the complete diagnostic trail (exact reproduction numbers, before and after).

   `total_attempts`'s own unit was redefined once more right after, at the user's
   explicit request, based on a direct, correct observation about what the CSP fill
   (`Filler`/`_backtrack`, see section 2 below) actually does: it fills by successive
   trial-and-backtrack — every slot that can no longer take any valid word (given the
   letters already fixed by its crossing neighbors) forces the search to undo its
   most recent placement and try a different word — so a single `_pattern_attempt`
   worker can internally abandon and retry *far* more candidate configurations than
   the `PARALLEL_ATTEMPTS` (10) count alone could ever suggest. Counting only
   distinct top-level worker attempts (even correctly deduped, per the fix just
   above) drastically undercounted "grilles échouées" in this literal, accurate
   sense — the true number of abandoned configurations is `filler.checks`
   (incremented once per `_backtrack` call — see `Filler.__init__`/`_backtrack` —
   already computed and exposed as `diagnostics["checks"]`), not the count of
   worker-level attempts. `total_attempts_tried` now sums `checks` across every
   *raw* failed outcome of a palier (not the deduped subset — even when two workers'
   *final* states converge to an identical grid, per the dedup fix above, each still
   did its own genuine internal search work to get there, along its own path of
   backtracks, so neither worker's `checks` total is safe to discard). The
   deduplicated `failed_unique`/`failed_pairs` lists are unaffected by this and
   still drive the up-to-6 shown examples exactly as before (see the note in the
   `project-best-practices` SKILL confirming this split is intentional: the
   6-best-examples selection and the retry-seed's own "keep the single best grid"
   step both still operate on the deduplicated, grid-level view — only the
   *counter* shown in the status text needed to change unit). Verified live: a real
   run (10×10, 400-word restricted vocabulary, seed 7, the same scenario used
   throughout this whole cross-palier investigation) showed `total_attempts`
   climbing `0 → 572 → 582 → 2471 → 2527 → 2537` across 5 failed paliers — orders of
   magnitude more informative than the old `0, 10, 20, 30, 40, 50` sequence, and
   genuinely reflective of how much backtracking search each palier actually
   required (very unevenly — palier 2 added only 10 checks total, palier 3 added
   1889, matching how CSP difficulty can vary sharply between one random pattern
   and the next) — then jumped to `404833` on the winning 6th palier, since even a
   palier with a successful attempt among its 10 can have several *other* attempts
   that each burned deep into their own `deadline_checks` (200,000) budget before
   the search moved on.

   A real bug was then found and fixed in how the failed-attempt preview is
   *delivered* to the browser, reported directly by the user from a real generation
   and confirmed by direct HTTP-level testing: `backend/app.py`'s job dict used to
   keep a single `last_examples` slot, overwritten every time a new `examples`
   batch arrived from `generate_grid()`'s `progress()` calls — meaning a palier
   that resolves fast enough (confirmed directly: several paliers completing
   within a single `POLL_INTERVAL_MS`, 2000ms, poll window) could have its own
   preview silently overwritten before the browser ever polled it even once. In
   the worst case this meant the *very first* preview a user ever saw already
   belonged to a *later* palier than palier 1 — which, since later paliers
   legitimately do carry locked/forced letters from the cross-palier retry
   mechanism above, made palier 1 itself look like it had started out already
   forced/locked, which it never actually does (verified directly and repeatedly:
   palier 1 always has zero forced cells). Fixed by replacing the single
   overwritten slot with `job["examples_history"]`, an ever-growing list — every
   `progress()` call with a non-empty `examples` now *appends* to it rather than
   overwriting, so no palier's own end-state is ever silently discarded.
   `frontend/static/script.js`'s `pollJob()` keeps a local `nextExampleIndex`
   cursor and shows exactly one *new* entry per poll (`history[nextExampleIndex]`,
   the oldest one not yet shown) rather than always jumping to whatever is
   currently newest — so the client works through the full history in order,
   guaranteeing every palier's own preview gets its moment on screen at least
   once, even when several complete between two polls; once the whole job
   reaches `status: "done"`, `pollJob()` still returns immediately as before,
   rather than forcing the user through the rest of a stale slideshow after the
   real result is already available. Verified live: submitted a real generation
   through the actual running backend and polled it — `examples_history` grew to
   13 entries by the time clue generation started (4 paliers' worth of entries
   had already accumulated within the very first ~14 seconds, confirming multiple
   paliers really can complete inside one poll window), and `examples_history[0]`
   — palier 1's own entry, confirmed never dropped — had zero forced cells across
   all 6 of its examples, exactly as expected.

   **This one-at-a-time cursor caused a real regression much later in this
   project's history**, reported directly by the user: "quand
   l'optimisation de la grille démarre, puis la génération des
   définitions, la recherche semble continuer en affichant des évolutions
   nouvelles de la recherche." Confirmed live rather than assumed: a
   Python script polling a real generation job (15×10, seed 2) at the
   exact 2s cadence `pollJob()` uses found that the moment
   `job["step"]["code"]` reached `"minimizing"`, `examples_history`
   already held 11 entries while the client-side cursor was only at 6 — a
   5-entry backlog that would otherwise keep surfacing stale search-phase
   previews for roughly 10 more seconds *after* the backend had already
   moved on to minimizing the grid. This got substantially worse later in
   this project's own history specifically because of two mechanisms
   added well after this cursor was first built: the 30%-unfillable
   abandon rule and the 5-consecutive-continue cap (both in
   `crossword_gen.py`, see its own section) make many paliers fail far
   faster than before, piling up a much bigger backlog before a grid is
   even found — the cursor design itself never changed, but the
   assumptions it was built under (a handful of paliers, not dozens
   completing in seconds) no longer held. Fixed in `frontend/static/
   script.js`: a new `POST_SEARCH_STEP_CODES` set (`"minimizing"`/
   `"grid_ready"`/`"clues"`/`"saving"` — deliberately excluding
   `"starting"`/`"pattern"`/`"pattern_attempt_failed"`/`"pattern_found"`,
   where the original one-at-a-time guarantee this section documents still
   matters) — once `data.step.code` is in that set, `nextExampleIndex`
   jumps forward to `history.length - 1` via `Math.max` (never backward)
   right before the existing "show one new entry" check runs, so the
   preview catches up to whatever the backend is *currently* doing instead
   of continuing to drain an old backlog. During the search phase itself
   this is a pure no-op — palier 1's own preview still can't be silently
   skipped the way this whole mechanism was originally built to prevent.
   Verified live: re-ran the same 2s-cadence polling simulation with both
   the old and the new cursor logic side by side against the same real
   job — the old cursor still showed a lingering 5-entry backlog the
   instant `"minimizing"` was reached, the new cursor caught up to 0
   immediately at that same poll.

   Separately, the criterion for picking the "best" failed attempt — which one
   becomes `_build_retry_seed`'s input, and which is shown first among the up-to-6
   examples — was changed once more, at the user's explicit request: from "most
   real letters placed" (`assigned_letter_count`, described above) to **fewest
   black cells**. `assigned_letter_count` itself is untouched (`try_fill`/
   `build_partial_letters_grid` still compute and expose it, unused now for this
   particular sort) — `failed_pairs` sorts on
   `sum(row.count(BLACK) for row in grid)` instead, ascending. This keeps the
   cross-palier retry mechanism aligned with this project's own long-standing goal
   of minimizing black cells (see `minimize_black_squares`, the ratio-ladder's own
   history) even among *failed* attempts being used as a stepping stone, rather
   than optimizing purely for "how far did the CSP search get" — a fuller-looking
   but denser failed pattern is no longer preferred over a sparser one that made
   less raw progress. Verified live: updated the existing deterministic sort test
   to tag each fake attempt with a distinct black-cell count (`5, 42, 13, 0, 27, 8,
   19, 33, 2, 50`, same digits reused from the old letter-count test, black cells
   placed at the tail of each fake grid so a marker cell at position `[0][0]`
   stays reliably white regardless of count) — confirmed the 6 shown examples come
   back as exactly `0, 2, 5, 8, 13, 19`, the true fewest-6 sorted ascending.

   A much deeper, genuinely serious bug was found next, reported by the user from a
   real generation and pinned down with painstaking precision using the exact raw
   API data they provided (not a guessed reproduction): **an entire palier could
   complete with *zero* new real progress** — every one of its `PARALLEL_ATTEMPTS`
   examples showed only the letters already carried forward as locked, with not a
   single fresh letter placed anywhere, confirmed cell by cell against the user's
   own JSON. The user's own reasoning ruled out a cleanup bug directly: if
   `_build_retry_seed` genuinely removes every word crossing an impossible slot (as
   verified exhaustively — see the entry above), the freed cells must be
   *reachable* by a fresh search, so a palier reproducing the old deadlock exactly,
   with zero new letters, "n'a pas d'explication" other than a bug somewhere in the
   cleanup. Rather than accept or reject that on reasoning alone, the exact palier
   was reconstructed byte-for-byte from the user's own JSON (the real pattern, the
   real locked letters, the real impossible slot) and rerun directly against the
   real code: `_build_retry_seed`'s own output matched the real next palier's
   `forced_cells` exactly, and — critically — contained *zero* overlap with the old
   impossible zone, proving the cleanup itself was correct. The real cause was one
   level deeper: `_pattern_attempt`'s pre-fill phase (`_prefill_unfillable_slots`,
   via `available_lengths`) only ever checked that a slot's *length* was generally
   well-covered by the dictionary (`PREFILL_MIN_WORD_COUNT` words of that length,
   anywhere in the lexicon) — it had no way to know that a *specific* slot, once
   several of its cells are pinned to specific letters by locked_letters carried
   forward from the previous palier, might have *zero* candidates matching those
   exact fixed letters even though plenty of words of that raw length exist
   elsewhere in the dictionary. Reproduced directly: after the aggressive
   word-boundary reopening (see the entry further above) freed nearly every black
   cell around just two surviving locked down-words, several 18-25-cell-long across
   slots each needed two specific fixed letters at specific positions — a
   combination no French word of that length actually has — and `try_fill` failed
   at `checks=1`, before ever assigning a single new letter, for every one of the 10
   parallel attempts alike (they all inherited the identical over-permissive seed).
   Fixed with a new `_slot_candidate_count(index, length, cells, locked_letters)`
   helper (the same per-position set-intersection logic already used by
   `Filler._domain`, just invoked here *before* the CSP search even starts, during
   pattern generation itself) and a locked-letter-aware `_has_slot_without_
   candidate`/`_prefill_unfillable_slots` (both gained optional `index`/
   `locked_letters` parameters, `None` by default so every pre-existing caller is
   unaffected) — a slot touching at least one locked cell is now checked with the
   real per-position intersection instead of the cheap length-only lookup; a slot
   with no locked cell still uses the fast path unchanged, so the common (first
   palier, nothing locked yet) case pays no extra cost. `make_pattern` itself
   dropped its old `locked_cells` parameter (a bare coordinate set, exclusion-only)
   in favor of `locked_letters` (the full `{cell: letter}` dict) plus a new `index`
   parameter, both threaded down into pre-fill — `locked_cells` for exclusion is now
   derived internally (`set(locked_letters)`) rather than needing a second,
   redundant argument from the caller.

   This first version of the fix (v1) still called the existing, generic
   `_place_black_cells` (the same row/column-balance look-ahead heuristic
   `make_pattern`'s own ratio-based placement uses) whenever a slot needed a new
   black cell to become fillable — it just gated the *decision* to add one on the
   new locked-letter-aware check, without changing *where* that cell actually
   landed. This caused a real, separate regression, reported live by the user from
   an actual run: `_place_black_cells` has no notion of *which* slot is failing the
   compatibility check, so it can (and did) blacken large, unrelated swaths of the
   grid before, by chance, happening to land a cell inside the one slot that
   actually needed it — observed going from a handful of cells at the target ratio
   up to 352 black cells on a 375-cell (25×15) grid in the worst case, an almost
   entirely black "successful" pattern with barely any playable slots left, exactly
   the opposite of what pre-fill is for. The user's own diagnosis of the right fix
   was explicit and unconditional: "Le préremplissage doit garantir que tous les
   emplacements permettent de placer au moins 10 mots. Mais, pour ça, il faut tenir
   compte des lettres forcées, et des cases noires déjà existantes" — ruling out an
   easier alternative (capping pre-fill at the target black ratio) that would have
   left the ≥10-candidate guarantee merely best-effort instead of absolute.

   Fixed properly (v2) by making the placement itself *targeted* instead of
   generic: `_has_slot_without_candidate`'s boolean check became
   `_slot_with_insufficient_candidates(...)`, returning the actual problematic slot
   (not just `True`/`False`) so the caller knows exactly which cells are eligible.
   `_prefill_unfillable_slots` was rewritten around it: for the returned slot, it
   only ever considers candidate black-cell positions that are *inside that slot's
   own cells*, ranked by how evenly they'd split the slot in two
   (`key=lambda cell: abs(2*slot.index(cell) - (length-1))`, favoring a
   near-the-middle cut over one right at an edge, which would barely shorten
   anything), tries each in that order, and commits the first one that keeps the
   grid structurally valid (`is_structurally_valid(..., min_interior_free=1)`) —
   this can never touch a cell outside the slot that actually needs fixing, so it
   can no longer blacken unrelated grid area by chance. A first version of this
   loop had its own bug, caught before it ever reached the user: it picked the
   *first* problematic slot every iteration and `break`-ed the whole pre-fill
   process the moment that particular slot turned out to have no valid placement
   at all (e.g. every cell of a 2-letter slot already locked by a crossing word,
   an inherent deadlock no black cell can resolve) — silently leaving every other,
   genuinely fixable problematic slot untouched. Fixed with an `unfixable`
   skip-set: a slot with no valid placement is added to it and the loop
   *continues* addressing the next problematic slot instead of aborting outright;
   `_slot_with_insufficient_candidates` accepts that same set (as `skip`) so it
   never re-offers a slot already known unfixable within the same pre-fill call.
   Re-verified on the exact reconstructed scenario that first exposed the
   regression: black-cell count for that one pattern dropped from 352 (the
   generic-placement bug) to 25 with the targeted fix, with only the two
   genuinely-unavoidable 2-letter slots (`TT`/`II` between two adjacent locked
   down-words, both cells of each already locked) left `unfixable` — down from an
   intermediate 35 measured on an earlier, less-refined pass of the same targeted
   approach.

   A follow-up question from the user, about exactly those 2 residual `unfixable`
   slots (two adjacent locked down-words forcing an impossible fixed 2-letter
   combination, `TT`/`II`/`RO` in the reconstructed case — no amount of *added*
   black cells can shrink a slot below 2 cells to fix that, since 2 is already the
   structural floor) — was answered by the same principle already established for
   the cleanup mechanism itself, and the user stated the expected resolution
   explicitly before it was verified: "Dans le pire des cas, la solution de
   blocage de l'étape N-1 doit permettre d'effectuer un cycle avec production de
   lettres... Admettons que l'étape N+1 puisse produire une situation immédiatement
   bloquée. Dans ce cas, cela doit déclencher un nouveau nettoyage libérant des
   lettres et probablement des cases noires à l'étape N+2." Verified directly, not
   just reasoned about: replaying the exact reconstructed scenario one palier
   further (the `TT`/`II` slots correctly identified by `Filler.impossible_zone_
   slots()` as impossible, `checks=1`, both zero-progress by construction since
   every cell of each is already locked) and feeding that through `_build_retry_
   seed` again — the very same, unmodified cleanup mechanism — confirmed it
   correctly removes *both* adjacent locked words (each shares a cell with one of
   the two new impossible slots), landing on a completely fresh palier with **zero**
   cells still locked and zero black cells carried forward, exactly the "cycle
   with letter production" the user predicted, with no new code required for this
   case at all — the existing "remove every word crossing an impossible slot"
   cleanup already generalizes to a deadlock introduced by pre-fill itself, not
   just one introduced by a failed CSP search.

   Verified live end to end after the full v2 fix, not just on the one
   reconstructed scenario: re-ran the exact 3 seeds that previously needed 10-13
   real failed paliers (150-254s each) before this whole fix arc — every one of
   them now succeeds in exactly **2 paliers**, 26.9-62.7s total (roughly 4-9×
   faster), 0 mismatches, with *zero* zero-progress paliers detected across all
   three. A broader sweep specifically targeting the over-blackening regression
   (5 seeds: 30/31/32 at 18×12, 2/7 at the 15×10 benchmark, tracking the worst
   black-cell ratio seen in *any* example of *any* palier, not just the final
   grid) confirmed the fix generalizes: seed 30 finished in 289.2s (11 failed
   paliers, 81 words, 48 black cells, 0 mismatches) with a worst-seen black ratio
   of 0.29 — nowhere near the ~94% (352/375) the v1 regression produced; seed 2
   finished in 39.4s (1 failed palier, 44 words, 50 black cells, 0 mismatches);
   seed 7 finished in 140.5s (8 failed paliers, 57 words, 24 black cells, 0
   mismatches) — confirming the targeted placement keeps pre-fill's own footprint
   proportionate to the actual problem across a range of grid shapes and seeds,
   not just the one hand-reconstructed case that first exposed the bug.

   A third bug in this same pre-fill mechanism was found next, reported live by
   the user from a real generation, with a screenshot showing an entire grid
   column solid black from top to bottom: "Dans cet étape, il y a une colonne
   complète de cases noires. Qu'est-ce qui a permis de faire ça ? Les règles
   d'ajout de cases noires doivent chercher les zones les plus disponibles, pas
   accumuler des cases noires en colonne." Root cause: v2's targeted placement
   (above) chose which cell, *within* the problematic slot, to blacken by
   `abs(2*position - (length-1))` alone — purely how close the cut is to a
   perfectly balanced split — with no reference at all to `row_black`/
   `col_black` (the row/column-availability counters `_place_black_cells`
   itself already ranks by, elsewhere in this same file). On a wide grid, many
   rows start out as one single full-width open slot with no black cells at
   all yet; if that raw length has too few dictionary words (very plausible at
   25 cells), *every* row's own prefill call needs to cut it, and the
   "balanced split" for a fixed length is always the exact same geometric
   middle column — so every row picked the identical column, building a solid
   black wall the user could see directly. Confirmed with a direct before/after
   comparison (not just reasoned about): reproducing the old, pure-balanced-
   split key against a real 25×15 grid/wordlist reproduced the *exact* reported
   symptom on 10/10 seeds tried (column 12 black on every row); switching to
   the fix below produced it on 0/10. Fixed by making row/column availability
   the *primary* sort key for which in-slot cell to blacken
   (`row_black[cell[0]] + col_black[cell[1]]`, ascending — the exact same
   "fewest black cells already placed" criterion `_place_black_cells` uses),
   with the balanced-split distance demoted to a tie-break only. This is a
   direct, minimal fix rather than a new mechanism: once one row's prefill
   blackens column 12, that column's own count rises, making it less
   attractive for the *next* row's own cut — so different rows naturally
   spread across different columns with no dedicated "don't repeat a column"
   rule needed, exactly the "chercher les zones les plus disponibles" the user
   asked for. Verified live: a real `make_pattern(25, 15, black_ratio=0.0,
   ...)` sweep across 20 seeds, using the real French wordlist/index, found
   zero full or near-full (≥80%) black columns or rows with the fix, where the
   reconstructed old-key comparison above found the reported column pile-up on
   every single seed tried.

   The user then proposed a deeper explanation for *why* pre-fill needed to
   blacken so much in the first place, tying it back to `_build_retry_seed`'s
   own step 3 (see above): "ne garder que les cases noires en bout de mots
   conservés, ça ouvre des passages sur les côtés en enlevant des cases
   latérales, créant des emplacements potentiellement impossibles avec les
   autres lettres" — reopening a black cell that sits laterally next to a
   confirmed letter (not at the word's own start/end, but beside one of its
   *middle* letters) can open a brand-new, tightly-constrained crossing slot
   right at that letter's position — one that may have very few or zero real
   candidates once combined with whatever other confirmed letters sit nearby
   — forcing the next palier's pre-fill to blacken heavily to compensate. This
   is a direct reversal of the word-boundary refinement documented above (the
   one that fixed the "6 distinct examples collapsing to 1" starvation bug):
   step 3's "keep black" rule went from "any cell adjacent to a confirmed
   letter" (the original, cruder version) to "only a true word-boundary cell"
   (narrower — the fix for that starvation bug) and now, at the user's
   explicit request, back to "any cell adjacent to a confirmed letter" —
   implemented as a strict superset of the word-boundary rule (a word's own
   boundary cell is itself adjacent to its first/last letter, so the new,
   single rule needs no special case for it) rather than as two separate
   criteria. Verified live rather than assumed safe against reintroducing the
   starvation bug this reverts past: re-ran the exact same 10×10/400-word
   diagnostic that first exposed that bug (seed 7, `difficulty="hard"`) —
   every one of its 10 failed paliers still shows a clean 6 distinct examples
   (no collapse to 1), 0 mismatches, max black ratio seen 0.32 — confirming
   the two problems (over-locking causing starvation vs. under-locking
   causing impossible new slots) are independent enough that fixing the
   second doesn't reintroduce the first, at least on this reproduction.

   A fourth, related gap was reported next, this time about the *ratio-based*
   black-cell placement itself rather than pre-fill or the retry-seed cleanup:
   "le tirage de nouvelles cases noires peut enfermer des groupes de lettres
   qui ne correspondent pas à un mot possible, et donc rendre la grille
   immédiatement injouable... la probabilité de produire une telle situation
   augmente avec le remplissage de plus en plus complet de la grille." Root
   cause: `_prefill_unfillable_slots` (the locked-letter-aware repair pass)
   only ever runs *once*, before `_place_black_cells` (the ratio-based
   placement used to reach the target black-cell ratio) — and
   `_place_black_cells` itself has no `locked_letters`/`index` parameter at
   all, so nothing stops it from truncating a slot that crosses an
   already-locked letter (carried forward from a previous palier) into a
   shape with too few real candidates, undoing what pre-fill had just
   guaranteed. This risk is close to zero on a fresh, unlocked palier (there
   is nothing fixed yet to conflict with a new black cell) and grows every
   palier a search fails and more letters get carried forward as locked —
   exactly matching the user's own observation that the probability rises
   "avec le remplissage de plus en plus complet de la grille." Confirmed
   directly before fixing, with a careful before/after methodology (a check
   that only flags a slot as genuinely broken if it has *some* but not *all*
   of its cells locked — a fully-locked slot is the confirmed word itself,
   already resolved, not a problem, and an earlier draft of this same check
   wrongly flagged those too): 30 real grids, each seeded with 3 locked words
   (mimicking a genuine carried-forward core) and run through the ratio-based
   phase at a non-trivial ratio (0.10) — **22/30** ended with at least one
   genuinely broken, partially-locked slot (several with *zero* real
   candidates) without the fix, **0/30** with it. Fixed by running
   `_prefill_unfillable_slots` a *second* time, right after the ratio-based
   placement, whenever `locked_letters`/`index` are present — the exact same
   function, not a new mechanism: its own internal loop already re-scans
   every slot after each cell it places, so one extra call reaches a fresh
   fixed point against whatever the ratio-based phase just did, correctly
   marking a genuinely irreducible case `unfixable` for the next palier's own
   cleanup to resolve, exactly as the first pre-fill pass already does.

   The user then proposed a complementary, *preventive* mechanism instead of
   relying solely on that after-the-fact repair: "à chaque ajout d'une case
   noire, valider que les 4 côtés de cette case ne sont pas bloqués compte
   tenu d'éventuels lettres déjà en place. Si un des 4 côtés est bloqué
   (n'étant pas un bord ni une case unique, donc au moins 2 lettres
   bloquées), refuser l'ajout de la case noire à cet emplacement." Implemented
   as `_new_black_cell_breaks_locked_slot(grid, rows, cols, r, c, index,
   locked_letters)`: for a *candidate* cell about to be blackened, computes
   the 4 resulting runs on its own 4 sides (left/right/up/down from that
   cell, out to the next black cell or the grid edge — not its 4 immediate
   neighbor cells), and for any of them that would be ≥2 cells long (a real
   slot, not a passthrough — matching `extract_slots`'s own threshold) *and*
   touches at least one already-locked letter, checks the exact same
   `_slot_candidate_count` used everywhere else in this pipeline; if any
   side comes back under `PREFILL_MIN_WORD_COUNT`, the candidate is refused
   — exactly like a structurally-invalid candidate — rather than being
   placed and left for the repair pass to clean up afterward. Wired into
   `_place_black_cells`'s own `_first_valid` alongside `is_structurally_
   valid` (both `_place_black_cells` and `make_pattern`'s call to it gained
   matching `index`/`locked_letters` parameters, `None` by default so every
   existing caller — including the very common first-palier, no-locked-
   letters case — is entirely unaffected and pays no extra cost). Returns
   `False` immediately whenever `locked_letters`/`index` are absent, for the
   same reason. Deliberately kept *alongside* the repair pass above rather
   than replacing it: this filter stops the problem from being created in
   the first place (so, generally, with no extra black cell needed to fix
   it afterward), while the repair pass remains the safety net for the
   residual case where no candidate in the whole window passes the filter.
   When that residual case does happen, the user was explicit that it
   should *not* be treated as a special deadlock to route around here: "Si
   une situation ne permet plus d'ajouter des cases noires, tenter le
   remplissage. Si il échoue, un nouveau nettoyage doit redonner une chance
   en libérant de la place" — `_place_black_cells`'s existing behavior
   already matches this exactly with no change needed (it simply rejects the
   best candidate and moves on, same as any other exhausted case, tolerating
   fewer cells placed than `target`); the CSP fill is attempted on whatever
   pattern results, and if it fails, the existing cross-palier
   `_build_retry_seed` cleanup already gives the next palier a fresh chance
   by freeing letters/cells, exactly as it already does for every other
   failure cause. Verified live: re-ran the exact same 30-seed reproduction
   used to confirm the repair pass (3 locked words per grid, ratio-based
   phase at 0.10) with the preventive filter active *and the repair pass
   disabled* — **0/30** broken slots, confirming the filter is independently
   effective on its own, not merely riding on the repair pass; with both
   mechanisms active together (the real, shipped configuration), still 0/30.

   A fifth bug was found next, reported by the user directly from a real
   generation's palier-1 preview: "il produit des structures régulières
   (triangle de cases noires), laissant penser qu'une partie de l'aléatoire
   a été retiré au profit d'un algo plus déterministe." Root cause: the
   column-pileup fix above (`_prefill_unfillable_slots`'s candidate sort,
   `row_black[cell[0]] + col_black[cell[1]]` primary, balanced-split
   distance as tie-break) picks its options via `sorted(...)` — a *stable*
   sort — over a generator that iterates the slot's own cells strictly
   left-to-right (`for pos, cell in enumerate(slot)`), never shuffled. At
   the very start of generation, every candidate in a fresh row's own
   wide-open slot ties at `row_black + col_black == 0`, so the outcome was
   decided *entirely* by the tie-break, itself completely positional — with
   no randomness anywhere in the path, every single seed produced the exact
   same sequence of cuts. Confirmed directly, not just reasoned about:
   `make_pattern` called on a fresh 15×25 grid with `black_ratio=0.0` across
   5 different seeds (0-4) produced **byte-for-byte identical** black-cell
   placement for every one of the 15 rows in every seed — proof the RNG had
   zero influence on the outcome, exactly matching the reported symmetric
   staircase (row 0 cuts the exact middle column; each following row cuts
   the next-closest column to the middle among the ones not yet used,
   alternating outward in a perfectly regular zig-zag). Fixed by threading
   an `rng` parameter through `_prefill_unfillable_slots` (both of
   `make_pattern`'s call sites, the initial pre-fill and the post-ratio
   repair pass, now pass their own already-seeded `rng`) and shuffling the
   slot's own eligible cells *before* the stable sort, rather than deriving
   them from the slot's fixed left-to-right order — ties among
   equally-scored candidates are now broken by the seeded shuffle instead of
   by position, while the sort's own two real criteria (availability, then
   balance) are completely unaffected. Verified live: re-ran the exact same
   5-seed reproduction after the fix — row 0 still always cuts the true
   middle (the only cell with zero tie to break at that point, so nothing
   *should* vary there), but every following row now differs across at
   least some seeds (e.g. row 3 cut column 14, 14, then 10 across seeds 0/1/2
   respectively, no longer a fixed universal sequence) — confirming genuine
   randomness is restored to the tie-break exactly where it was previously
   completely absent.

   The user still saw a visibly regular pattern at palier 1 even after that
   fix ("j'ai encore une grille régulière au tour 1"), correctly sensing
   something deeper than a plain missing-shuffle bug. Confirmed directly by
   printing the actual grids (not just column lists) for several seeds:
   every one produced the *exact same shape* — a symmetric "V"/triangle of
   black cells fanning outward from the true center column, row by row —
   even though *which* column, left or right of center, got used by any
   given row now varied seed to seed. Root cause: randomizing the tie-break
   only randomizes *which side* wins a tie between two equally-good
   candidates — it does nothing to stop the *set* of columns eventually used
   from being, deterministically, "the N columns closest to the center" for
   whatever N rows have been processed so far, since the sort's own second
   key (`abs(2*position - (length-1))`, the balanced-split distance) always
   ranks a candidate nearer the center above one farther out, with no
   randomness in *that* ranking at all — so the overall silhouette stays a
   rigid, predictable band expanding outward regardless of tie order. This
   distance-based key was never load-bearing for correctness (structural
   validity is enforced separately by `is_structurally_valid`) — it existed
   purely so a cut wouldn't leave one side a tiny, lopsided sliver, a
   cosmetic goal, not a structural requirement. Fixed by dropping it from
   the sort entirely: `_prefill_unfillable_slots` now ranks candidates
   *only* by `row_black[cell[0]] + col_black[cell[1]]` (availability), with
   every tie — which, on a fresh grid, means *every* candidate at the very
   start — broken purely by the already-seeded shuffle. Verified live:
   printing full grids (not just per-row cut columns) for 6 seeds shows
   visibly organic, non-repeating black-cell scatter with no trace of the
   former triangle in any of them; the same 30-seed locked-letter
   reproduction used throughout this whole investigation still shows 0/30
   broken slots, confirming this purely cosmetic change doesn't reopen any
   of the correctness fixes above.

   Two further refinements to the cross-palier retry mechanism itself were
   requested next, both at the user's explicit, precisely-specified request.
   First, an isolated-hole protection for `_build_retry_seed`'s step 3
   (which black cells stay black): a black cell not adjacent to any
   confirmed letter — normally reopened — now stays black if *all 4* of its
   own neighbors (up/down/left/right) are themselves black in the original
   grid (`_fully_surrounded_by_black`, checked against the grid *before* any
   reopening in this same pass, so the result never depends on iteration
   order). Reopening such a cell would create a white cell isolated on all 4
   sides — exactly the one invariant this whole file treats as absolute and
   non-negotiable (see `is_structurally_valid`'s discussion of a "case
   blanche orpheline dans les deux sens à la fois"), not a style preference
   like the border-zone tolerance. A border cell can never satisfy this
   check (at least one neighbor is off-grid), so the exception only ever
   applies to a strictly interior cell, consistent with the isolated-hole
   risk only existing away from the border. Verified with two hand-built
   5×5 grids: a black cell with all 4 neighbors black stays black; the same
   cell with even one neighbor turned white beforehand gets correctly
   reopened.

   Second, and more substantial: the winning grid to carry into the next
   palier is no longer chosen *before* cleanup (previously: the single
   `failed_pairs[0]`, the deduped failed attempt with the fewest black
   cells, was the only one ever passed through `_build_retry_seed`) — all
   `FAILED_ATTEMPT_EXAMPLES` (6) best failed attempts (the same ones already
   shown in the preview) are each run through `_build_retry_seed`
   individually first, and *then* the actual winner is picked from among
   those 6 *cleaned* results, by whichever retains the most confirmed
   letters (descending), fewest black cells as the tie-break. Rationale:
   each of the 6 candidates loses a different number of letters at step 1
   (removing words crossing its own impossible slot(s)) depending on the
   exact shape of its own dead end — the one that looked "best" by a
   pre-cleanup metric (fewest black cells, or previously most letters
   placed) isn't necessarily the one that retains the most *useful*
   information once cleaned, which is the only thing that actually matters
   for the next palier's own search. Verified with a hand-built two-candidate
   scenario: candidate A (a 10-letter word, no impossible slot, nothing
   removed by cleanup) correctly beats candidate B (the same shape, but an
   injected impossible slot forces its entire word removed, leaving 0
   confirmed letters) — the selection correctly favors A's 10 surviving
   letters over B's 0, which a pre-cleanup, black-cell-count-only comparison
   could not have distinguished this way. A real end-to-end sweep (15×10
   seeds 2/7, a deliberately hard 10×10/400-word run, 18×12 seed 30)
   confirmed no regression: every run still succeeds with 0 mismatches
   between the placed words and the solution grid.

   A genuinely serious bug was found next, reported directly by the user
   from a real generation with an exact description of the symptom: "Une
   grille très remplie avec peu de conflit a été presque entièrement
   vidée." Live audit (not reasoning alone) with a hand-instrumented
   multi-palier script mirroring `generate_grid()`'s own loop, run on a
   real grid/dictionary across 8 successive paliers: 3 of the 8 (paliers 2,
   4, 7) showed **`assigned_slots=0` for all 6 candidates** — the search
   made zero fresh progress — while the *previous* palier had carried
   forward 65, 44, and 69 locked letters respectively; every one of those
   letters vanished. Root cause: `assignment` (`Filler.best_assignment`)
   only contains a word for a slot the moment `_backtrack` actually assigns
   it explicitly during *that attempt's own* search — an already fully
   locked slot (every cell fixed by `locked_letters` carried over from the
   previous palier, passed in as a hard constraint) is never "re-assigned"
   if the search fails before ever reaching it (the fast `checks=1`,
   empty-domain-on-the-very-first-check failure mode). `_build_retry_seed`
   only ever looked at `assignment` to decide what survives — with no way
   to know a cell was already locked, it silently dropped all of it,
   indistinguishable from those letters never having existed. Fixed by
   adding a `locked_letters` parameter: for any slot where `assignment[i]`
   is still `None` but every one of its cells is covered by
   `locked_letters`, the word those letters spell is filled in *before* the
   normal three-step cleanup runs — treated identically to a genuinely
   fresh assignment from here on (including still being removed if it
   directly crosses an impossible slot; this fix restores dropped
   information, it doesn't add new protection beyond what a real assignment
   already gets). The one caller (`generate_grid`'s per-candidate cleanup
   loop) now passes the palier's own `carry_locked_letters` through.
   Verified live: re-ran the exact same 8-palier audit with the fix — the
   previously catastrophic paliers 2/4/7 now correctly carry forward 58,
   26, and 49 letters (built on top of what survived from before, not
   reset to zero) instead of 0, confirming already-locked content is no
   longer silently discarded just because a given attempt's own search
   never got the chance to re-derive it.

   That first fix immediately introduced a second, equally serious bug,
   caught by an end-to-end sanity sweep run right after deploying it (not
   just the isolated audit above): the standard 15×10 benchmark's own
   seed 7 — which had succeeded reliably throughout this project's entire
   history — now failed outright, exhausting all 40 paliers with no
   solution. A deeper multi-palier audit pinned it down precisely: a slot
   can be *both* fully covered by `locked_letters` *and* simultaneously
   listed in `impossible_slots` — meaning the exact combination of letters
   already locked at that position doesn't spell any real dictionary word
   at all (that mismatch is *why* it's impossible in the first place). The
   fix above blindly "recovered" such a slot as assigned anyway (reading
   its word straight from `locked_letters`, with no check against
   `impossible_slots`), which preserved that invalid combination forever:
   since the slot is never in `to_remove` (which only ever removes *other*
   slots crossing an impossible one, never the impossible slot itself), the
   broken combination could never be cleared, and the exact same dead end
   reproduced identically, palier after palier, with zero progress.
   Reproduced live: on the failing scenario, 29 locked letters and 2
   impossible slots (each a 2-cell down-slot, already fully locked) stayed
   **byte-for-byte identical across 12 consecutive paliers**, exhausting
   all 40 attempts with no solution — exactly matching the reported
   regression. First fixed by excluding any slot already present in
   `impossible_slots` from this locked-letters recovery unconditionally: it
   stays `None`, so step 1's own removal logic can now correctly identify
   and clear whatever *other* slot(s) fixed those invalid letters in the
   first place, freeing the zone for the next palier instead of reproducing
   the same broken state forever.

   That unconditional version immediately caused its own regression too,
   caught by a routine end-to-end sweep and confirmed with a direct
   before/after comparison, not assumed: a deliberately hard 10×10 grid
   restricted to a 400-word vocabulary (seed 7) — which had succeeded
   throughout this whole investigation — started failing every time with
   the unconditional exclusion, while succeeding reliably (4.37s, 0
   mismatches) with it disabled. The exclusion, applied at *every* palier
   regardless of whether anything was actually stuck, also strips content
   from cases where the locked-and-impossible slot wasn't blocking progress
   at all — a real cost with no matching benefit in the common case.
   `exclude_impossible_locked` is now a parameter (`False` by default — the
   plain recovery behavior, which turned out to be the right choice in the
   large majority of real scenarios observed) rather than baked-in
   behavior; `generate_grid`'s own loop applies the two variants adaptively:
   clean all 6 candidates normally first, and only if the resulting winner's
   confirmed letters are *rigorously identical* to the previous palier's —
   a genuine, detected fixed point, not merely reasoned about — does it
   re-clean the same 6 candidates a second time with
   `exclude_impossible_locked=True` and use that result instead. This keeps
   the more aggressive rule as a targeted, detected-on-demand escape hatch
   rather than a blanket policy that costs the common case to fix a rare
   one. Verified live: all three scenarios succeed together with this
   adaptive version — the 10×10/400-word case (no longer forced through the
   exclusion, since it never actually gets stuck) and the 15×10 seed-7 case
   that previously deadlocked (now correctly triggering the exclusion only
   once a true fixed point is detected). **Not fully resolved**: a
   follow-up sweep found 25×15 seed 7 still exhausts all 40 paliers without
   a solution even with this fix — the strict "identical confirmed letters
   between consecutive paliers" check apparently doesn't catch every way
   this search can fail to make real progress (e.g. cycling through
   slightly different, equally unproductive states rather than a literal
   fixed point); left as a known open gap, not yet root-caused.

   The fixed-point detection itself was refined further, at the user's
   explicit request: "à chaque cycle, même si une situation de blocage est
   constatée, continuer tant qu'on arrive à ajouter des mots... n'arrêter
   [d'escalader] que quand il n'y a plus que des situations de blocage." A
   genuine ambiguity here was resolved by asking directly (as with the
   tier 3/4 inversion above) rather than guessing between two materially
   different implementations — the user confirmed: a *partial* blockage
   within one palier (some of the `PARALLEL_ATTEMPTS` parallel attempts add
   words, others don't) must never trigger the stronger cleanup; it's
   reserved strictly for the case where *every single* parallel attempt of
   that palier is a pure blocking situation. The previous detection
   (comparing the *winner's* confirmed letters to the previous palier's)
   had a real gap here: if the winning candidate — chosen purely by most
   confirmed letters — happened to coincide with the prior state, the
   stronger cleanup could fire even though some *other*, non-winning
   attempt in that same palier had genuinely progressed. Fixed by checking
   directly across all of the palier's raw parallel outcomes
   (`failed_all`, before dedup) for *any* slot assignment at all
   (`any(word is not None for word in d["assignment"]) for _, d in
   failed_all`) — the stronger cleanup now only ever engages when this is
   `False` for every single attempt, never on a partial blockage. Verified
   with an isolated test of the two cases directly (a `failed_all` list
   with one progressing attempt among several blocked ones vs. one where
   every attempt is fully blocked) — the new condition correctly
   distinguishes them.

   **Reverted almost immediately**, at the user's own explicit follow-up
   request, after observing it in practice: "on dirait qu'il n'essaye pas
   vraiment de remplir les grilles partielles. quelque chose ne fonctionne
   pas correctement. revenir à la situation précédente." Back to comparing
   the winner's own confirmed letters to the previous palier's (the version
   documented just above, before this per-parallel-attempt refinement) —
   the finer-grained check, while logically more precise on paper, made the
   real generation behave visibly worse in practice, for reasons not yet
   root-caused. Not investigated further before reverting, at the user's
   explicit request to prioritize reverting over diagnosing.

   Right after this revert, the user pointed at what turned out to be the
   actual root cause of "il n'essaye pas vraiment de remplir les grilles
   partielles": "à la fin d'un tour, quand la grille échouée est
   sélectionnée pour le tour suivant, avant de nettoyer l'emplacement
   identifié comme bloquée, continuer à ajouter des mots tant que c'est
   possible." A genuine implementation ambiguity was resolved by asking
   directly rather than guessing (given how costly the previous guess on
   this exact area turned out to be): confirmed to mean re-running the CSP
   fill on each candidate's own pattern, *excluding* the already-identified
   impossible slot(s) from consideration, rather than simply raising the
   search's `deadline_checks` budget. Root cause, confirmed directly:
   `_backtrack`'s very first step computes every unassigned slot's domain
   and fails *immediately* if any single one is empty — a slot already
   known to be impossible (carried forward from a previous palier's own
   failed search on this same pattern) has, by definition, an empty domain,
   so simply re-attempting the fill on the same pattern hit this fail-fast
   check on its very first call (`checks=1`), before ever getting a chance
   to try filling anything else in the grid — completely unrelated to that
   one slot or not. Fixed by adding `Filler.excluded_slots` (a set of slot
   indices excluded from `unassigned` entirely — never selected, never
   domain-checked, never counted toward "solved") and, in `generate_grid`'s
   per-candidate cleanup loop, giving each of the 6 candidates a second
   `Filler` pass on its own pattern *before* `_build_retry_seed` runs:
   the existing assignment is pre-seeded as already-fixed (never
   reconsidered), the already-identified impossible slot(s) go into
   `excluded_slots`, and the search continues freely on everything else.
   `impossible_zone_slots()` is recomputed afterward (this second pass can
   surface new impossible slots elsewhere) and fed into `_build_retry_seed`
   in place of the original, pre-continuation diagnostics. Verified live in
   isolation first, on a hand-built 5×5 grid with one genuinely impossible
   2-cell slot (forced letters with no matching real word) crossed with an
   unrelated, perfectly fillable 5-letter slot: without `excluded_slots`,
   the fill fails at `checks=1` with nothing assigned at all; with the
   impossible slot excluded, it succeeds at `checks=2`, correctly filling
   the unrelated slot while leaving the excluded one `None` as intended.

   **Wiring this into `generate_grid`'s real loop was itself reverted**, at
   the user's own explicit request, after a real end-to-end test exposed a
   serious regression the isolated unit test hadn't caught: the standard
   15×10 benchmark's seed 2 — which had just succeeded cleanly (62.7s, 0
   mismatches) right before this change — started hitting `checks=1` on
   199 of 200 paliers once the continuation step was wired in, with
   `slot_count` slowly growing (43 → 56) across those failed paliers rather
   than ever recovering. `Filler.excluded_slots` itself is kept (verified
   correct and potentially useful later), but `generate_grid`'s per-
   candidate call to it was removed, restoring the exact pre-continuation
   behavior — confirmed live: the same seed 2 succeeds again (71.3s)
   immediately after reverting just this wiring. Root cause not yet
   pinned down before reverting (at the user's own priority: revert first,
   diagnose later, matching the earlier per-parallel-attempt-progress
   revert above) — the leading hypothesis is that always growing the
   locked-letter footprint a little further every single palier (rather
   than only when a palier's own search made it there unassisted)
   compounds with `make_pattern`'s own locked-letter-aware placement in a
   way that makes each freshly-generated pattern *more* fragile, not less,
   over successive paliers — but this is not yet confirmed.

   Separately, this same investigation surfaced a genuine, independent
   reproducibility problem worth flagging even though it's not yet fixed:
   the *exact same* `generate_grid()` call (same `seed` argument in every
   respect) produced a different final result (success vs. failure) across
   separate process invocations of the 10×10/400-word scenario — confirmed
   directly by running it 3 times in a row (`True`, `False`, `True`) and
   then confirmed *stable* across repeats once `PYTHONHASHSEED=0` was fixed
   for the process. This points at some step in the pipeline whose order
   depends on Python's per-process string-hash randomization (e.g. a `set`
   of words or cells converted to a list before sampling/iterating)
   somewhere upstream of the per-attempt seeded `random.Random`, rather than
   the RNG itself being unreproducible — not yet localized to a specific
   function, left for a future investigation.

   A further black-cell-retention refinement was requested next, in two
   passes. First: "conserver les cases noires dont un des 4 côtés est sur
   un emplacement (plusieurs cases) contenant une lettre... il peut y avoir
   des blancs entre la case noire et la lettre" — the existing rule only
   checked the *immediately adjacent* cell in each of the 4 directions, not
   the *whole* run of white cells (the real emplacement/slot) that
   direction leads into. This matters because a slot can have some of its
   cells confirmed via a *crossing* slot's own assignment while the slot
   itself remains formally unassigned — e.g. an unassigned vertical run
   whose middle cell is fixed by a crossing, assigned horizontal word: the
   cell immediately next to the black cell (the run's first cell) carries
   no letter, but a cell further along the *same* uninterrupted white run
   does. `protected_black_cells` now walks the full white run in each of
   the 4 directions (`_direction_has_confirmed_letter`, reusing the same
   walk-until-black-or-edge pattern as `_new_black_cell_breaks_locked_slot`
   above) looking for *any* confirmed cell anywhere along it, rather than
   checking only the one adjacent cell — a black cell stays black if at
   least one of its 4 directional emplacements contains a confirmed letter
   anywhere within it, and is only reopened once all 4 are entirely
   letter-free. Verified with a hand-built grid reproducing exactly this
   gap shape: an unassigned 3-cell vertical run whose middle cell is fixed
   by a crossing assigned word ("HORSE") — the black cell bordering that
   run's near end correctly stays black even though its own immediate
   neighbor carries no letter at all.

   This "any one side suffices" version was immediately tightened once
   more, at the user's explicit request, once they identified exactly the
   case it wrongly protected: a black cell that merely *sees* a letter on
   one side (e.g. crossing, at a distance, a word assigned in the other
   direction) without itself being that word's own boundary doesn't
   actually protect anything — reopening it can't disturb that word's
   integrity, since the seen letter belongs to a word that doesn't extend
   into this cell in its own direction at all. The final rule keeps a black
   cell only under the union of two independent conditions: (1) it
   genuinely bounds a surviving word — immediately before its first letter
   or immediately after its last, in that word's own direction (the same
   computation as the very first version of this step, never removed, only
   supplemented); (2) it has a confirmed letter on *both* sides of the same
   axis at once — both up and down, or both left and right (not both axes
   together) — a cell sandwiched between two word segments on the same
   axis, where reopening it would merge two distinct emplacements into one
   that may not correspond to any real word, disturbing both sides at
   once. A cell seeing a letter on only one side of an axis, without
   bounding that word, is now reopened — including the distant-crossing
   case above, which never actually threatened anything; only the
   "one-side-suffices" version treated it as if it did. The user then
   confirmed this with an equivalent restatement: "une case noire se
   trouvant quelque part entre 2 mots existants... doit être conservée ;
   une case noire se trouvant au bout d'un mot... doit être conservée ; les
   autres cases noires peuvent être supprimées" — exactly conditions (2)
   and (1) above. Verified with three hand-built grids: a cell seeing a
   letter on only one side (distant crossing, no boundary) now reopens; a
   cell genuinely bounding a word stays black; a cell sandwiched between
   two assigned words on the same vertical axis stays black.

   A **"Nouvelle version"** of the cross-palier retry mechanism replaced
   the still-mysterious "exclude and re-run before cleanup" regression
   documented above, at the user's explicit, precisely-specified request:
   "quand un tour termine et que la grille échouée a été sélectionnée, si
   il reste des emplacements où des mots peuvent être trouvés, transmettre
   cette grille telle-quelle au tour suivant en verrouillant toutes les
   cases remplies. Le tour N+1 doit ignorer les situations de blocage sur
   les cases verrouillées, et essayer de continuer à remplir la grille. Si
   un tour termine sans emplacement où il est possible de rajouter un mot,
   nettoyer les emplacements impossibles avant de relancer le tour
   suivant." The key structural difference from the reverted attempt:
   this one never calls `make_pattern` (or `_build_retry_seed`) while
   there's still hope of adding more words — the exact same pattern is
   reused verbatim, round after round, rather than a fresh/cleaned pattern
   being (re)generated on top of an ever-growing locked footprint, which
   was the leading (unconfirmed) suspect for the earlier regression.

   Implementation: `try_fill` gained `preseed_assignment`/`excluded_slots`
   parameters (both `None` by default — every existing caller unaffected).
   `preseed_assignment`, when given, initializes `Filler.assignment` (and
   `used_words`/`best_assignment`/`best_assigned_count` to match) directly
   from the previous palier's own result instead of starting from a blank
   grid — every already-assigned slot is locked in as-is, `_backtrack`
   never reconsiders it. `excluded_slots` (already built and verified in
   isolation earlier, but never wired in until now) is passed straight
   through to `Filler`, so a slot already known impossible from the
   previous palier is skipped by `_backtrack`'s domain-check entirely,
   rather than instantly failing the whole search (`checks=1`) the way it
   did before this mechanism existed. Because an excluded slot can never
   be assigned by construction, `Filler.solve()`'s own internal "solved"
   (no more *non-excluded* slot to try) no longer implies a genuinely
   complete grid whenever `excluded_slots` is non-empty — `try_fill` now
   computes `truly_complete = all(w is not None for w in
   filler.assignment)` separately, and only that (not the internal
   `solved`) decides whether the caller gets back a real, usable result;
   with `excluded_slots` empty (every pre-existing caller), the two always
   coincide, so this is provably a no-op for them. `diagnostics["reason"]`
   gained a fourth value, `"blocked_on_excluded_slot"`, for exactly the
   case where the internal search finished (every open slot got a real
   word) but at least one excluded slot remains — distinct from
   `"search_exhausted"` (a real dead end reached via backtracking) since
   nothing was actually exhausted here, the slot was deliberately never
   attempted.

   A new worker function, `_pattern_continue(rows, cols, seed, seed_grid,
   preseed_assignment, excluded_slots, force_letters_fraction=0.0)`,
   mirrors `_pattern_attempt` but — unlike it — never calls `make_pattern`
   at all: `seed_grid` (the previous palier's own pattern, unchanged) is
   passed straight into `try_fill` alongside the preseed/exclusion pair.
   Each of the `PARALLEL_ATTEMPTS` parallel workers at a "continue" palier
   still gets its own seed, so the pattern/lock stay identical across them
   but the search order (`sample_letter_biases`, candidate tie-breaking)
   still differs — enough for different workers to reach different
   amounts of further progress from the same starting point.

   `generate_grid`'s loop now decides, right after computing `failed_pairs`
   (unchanged: sorted by fewest black cells, still what drives
   `last_diag`/`last_examples`): take the already-selected best failed
   candidate (`failed_pairs[0]`) and check whether it still has *any*
   unassigned slot that isn't impossible (`impossible_count <
   unassigned_count`, both read straight from its own diagnostics). If
   yes, the next palier reuses that exact grid verbatim
   (`carry_seed_grid`/`carry_preseed_assignment`/`carry_excluded_slots`,
   routed to `_pattern_continue` instead of `_pattern_attempt`) — no
   cleanup, no new pattern. If no (every remaining unassigned slot is
   impossible — a genuine total dead end for this exact pattern), the
   existing `_build_retry_seed`-based cleanup + fresh `make_pattern` path
   runs exactly as before this feature (`carry_preseed_assignment`/
   `carry_excluded_slots` reset to `None`, so the two retry mechanisms are
   always mutually exclusive at any given palier). Since a "continue"
   palier's own grid never changes, this check is re-evaluated fresh at
   the end of *every* palier (continue or cleanup alike) against whatever
   `failed_pairs[0]` that palier itself produced — a "continue" streak can
   run for several consecutive paliers (each carrying forward more locked
   letters and, possibly, more excluded slots) before either succeeding or
   finally hitting the true dead end that triggers cleanup.

   Verified live: two isolated `try_fill` reproductions confirmed the core
   mechanics directly — a synthetic grid with a genuinely impossible slot
   crossing a fillable one showed the old instant `checks=1` failure
   without `preseed_assignment`/`excluded_slots`, and real further
   progress (`checks=6`, a new word assigned) with them; a second,
   fully-independent-slots reproduction (no crossing at all) confirmed
   `reason="blocked_on_excluded_slot"` fires exactly when the internal
   search completes everything *except* the excluded slot, distinct from
   `search_exhausted`. Three real end-to-end `generate_grid()` runs then
   confirmed the mechanism under genuine multi-palier conditions: the
   standard 15×10 benchmark's seed 2 succeeded in 86.6s (50 words, 25
   black cells, 0 mismatches) and seed 7 in 138.4s (54 words, 21 black
   cells, 0 mismatches) — both comfortably within this whole
   investigation's historical range for this benchmark; a deliberately
   hard 10×10 grid restricted to 400 words (difficulty="hard", seed 7)
   succeeded in just 9.3s (37 words, 33 black cells, 0 mismatches).

   `try_fill` gained one more diagnostics field, `locked_cells`, at the
   user's explicit request, once it became clear the attempt-preview grids
   shown during a "reprise telle-quelle" streak could now display cells
   whose letter is a *real*, previously-confirmed one (carried over via
   `preseed_assignment`), visually indistinguishable from a letter placed
   by *this* round's own search — the user asked these two to be
   highlighted differently, in a different color from the existing
   `forced_cells` (a purely statistical guess), if the mechanism producing
   them is genuinely different, which it is. `locked_cells` is computed
   once in `try_fill`, right before `Filler.solve()` runs, as every cell
   belonging to a slot already non-`None` in `preseed_assignment` — safe
   to compute *before* the search rather than after, since a preseeded
   slot is never reconsidered by `_backtrack` (it's already excluded from
   `unassigned` by having a non-`None` assignment, independently of
   `excluded_slots`), so this set never changes during the call. `[]` for
   every pre-existing caller (`preseed_assignment=None`) and for the
   `no_slots` early-return branch, matching the shape of the other
   diagnostics fields already initialized there. Threaded through
   `generate_grid`'s `last_examples` list comprehension (`d.get(
   "locked_cells", [])`) and both of the success-only preview blocks
   ("minimizing" here, "clues" in `backend/app.py`) that already
   explicitly empty out `impossible_cells`/`forced_cells` for a fully
   solved grid — `locked_cells: []` added there for the same reason (a
   successful, fully-minimized/solved grid has no locked-vs-fresh
   distinction left to show).

   `frontend/static/script.js`'s `renderAttemptPreview()` reads this new
   field the same way as `forced_cells`/`impossible_cells` (a final overlay
   pass, once every cell already exists in the DOM) and applies a new
   `.locked` class, styled in `style.css` with a new `--locked-bg` token
   (light orange) — see the `style-guide` SKILL for the exact visual
   reasoning (why a different color/mechanism from `.forced`, why a
   background fill is safe here unlike `.forced`'s border-only style).
   Separately, at the user's own further request in the same message: the
   preview grids' *letters* themselves (real progress and statistical
   hints alike) are now hidden by default (`showPreviewLetters`, initially
   `false`) — once "reprise telle-quelle" can carry forward a large,
   mostly-real fraction of the eventual solution across several paliers,
   these preview grids stopped being purely diagnostic and started risking
   spoiling the actual puzzle before the player gets to play it. A new
   bi-stable button, `#attempt-preview-reveal-btn` ("Lettres", same
   `.toggle-btn`/`.active` styling as the real grid's own `solutionBtn`/
   `checkBtn`), toggles `showPreviewLetters` and immediately re-renders
   whichever batch of examples is currently on screen (`lastPreviewExamples`,
   a new module-level variable holding the last `examples` array passed to
   `renderAttemptPreview()`) — the highlight classes
   (`.impossible`/`.forced`/`.locked`) stay visible regardless of this
   toggle, since they convey *where* something happened, not *what* letter
   is there. The button needs no dedicated show/hide logic of its own: as
   a child of `#attempt-preview`, it disappears automatically whenever
   that whole section is hidden by `hideAttemptPreview()` — already called
   both at the start of a new generation and once the real, playable grid
   is ready — matching the user's explicit requirement that this button
   disappear once the solution is established, at which point the real
   `solutionBtn` on the playable grid takes over that same role.
   `hideAttemptPreview()` also resets `showPreviewLetters` to `false` and
   clears the button's own `.active` state, so a revealed state never
   survives from one generation into the next. Verified live: a real
   `generate_grid()` run (15×10, seed 2, the same benchmark as above,
   `on_progress` inspecting every `examples` batch directly) confirmed at
   least one progress event carried a non-empty `locked_cells` list —
   proof the "reprise telle-quelle" mechanism genuinely produces this data
   in a real run, not just in the isolated `try_fill` tests above — and a
   real JS syntax check (`esprima`, temporarily installed and removed
   again afterward, same pattern used elsewhere in this project) confirmed
   `script.js`/`i18n.js` still parse correctly after the change. **Not yet
   visually confirmed in an actual browser** — same limitation already
   noted elsewhere for this project's UI work this session; verified
   structurally instead (the syntax check, and reading the real diagnostics
   data a live run produced).

   A **`GenerationCancelled`** exception was added (module-level, next to
   `BLACK`/`WHITE`), at the user's explicit request: a "Stop" button on
   the web UI (see `backend/app.py`) needs to interrupt a generation in
   progress "quelle que soit l'étape" (whatever the current phase).
   `generate_grid()` gained a `cancel_event` parameter (a `threading.
   Event`, `None` by default — no effect for any pre-existing caller,
   including the CLI) checked at the top of every palier
   (`for attempt in range(attempts):`) and threaded into `minimize_black_
   squares` (its own new `cancel_event` parameter, checked once per
   candidate black cell in its removal loop) — raising
   `GenerationCancelled` rather than returning `None`, since `None`
   already means something else entirely (no fillable grid found after
   exhausting `attempts`, a genuine failure, not an interruption).
   Deliberately a purely *cooperative* signal, never a forced kill of an
   already-running worker process: each long loop checks the event at its
   own natural boundary and stops there, so the actual interruption can
   take up to one palier's own worst-case duration to become visible
   (bounded by every parallel attempt's own `deadline_checks` budget, not
   unbounded) — a documented, accepted trade-off over the complexity/risk
   of forcibly terminating `ProcessPoolExecutor` worker processes mid-
   computation. `LLMClueGenerator.generate()` (`backend/clues.py`) got the
   same treatment for the clue-generation phase — see its own CLAUDE.md
   entry.

   Separately, `POST_PREFILL_BLACK_FRACTION` (the fixed density draw
   applied after pre-fill, see `make_pattern`'s own docstring above)
   became a `black_enrichment_fraction` parameter on `make_pattern`,
   `_pattern_attempt`, and `generate_grid` alike (each defaulting to the
   module constant, so every pre-existing caller — the CLI included — is
   unaffected), at the user's explicit request: a new "Ajout noires"
   selector on the web UI (0/1/3/5/10%, 3% by default — see
   `backend/app.py`'s `GenerateRequest.black_enrichment_percent`) lets
   this be tuned per request instead of being fixed at 10% for everyone.
   Threaded only into the `_pattern_attempt` executor call, never into
   `_pattern_continue` — deliberately, since a "reprise telle-quelle"
   palier never calls `make_pattern` at all (it reuses the previous
   palier's own pattern verbatim, see `_pattern_continue`'s own
   docstring), so it can never add a black cell regardless of this
   setting — exactly the behavior the user explicitly asked for ("le tour
   de remplissage sans nettoyage reprend la grille précédente sans
   ajout").

   Verified live against the real running API (not just `generate_grid()`
   called directly): started a real generation (22×18, `black_enrichment_
   percent=3`), confirmed via `GET /api/generate/status/{job_id}` that it
   was genuinely `"running"` mid-pattern-search, called the new `POST
   /api/generate/cancel/{job_id}` endpoint, and confirmed a subsequent
   poll showed `status: "cancelled"` — the cooperative checkpoint caught
   it at the very next palier boundary, with the diagnostics from
   whichever attempt was in flight at that moment still present in
   `step`. A second real request with `black_enrichment_percent=0`
   confirmed the edge case (no enrichment at all, pre-fill-only density)
   still completes normally; a request with an invalid value (`7`, not in
   `BLACK_ENRICHMENT_PERCENTS`) was correctly rejected with a 400 and a
   clear French error naming the allowed values.

   A serious bug was found next, investigated after the user reported —
   in general terms, no specific reproduction given — that "les phases de
   remplissage avec verrouillage semblent laisser beaucoup d'emplacements
   non remplis... particulièrement visible sur un tour avec de très
   grandes grilles déjà partiellement bien remplies." A dedicated
   diagnostic script (a temporary `on_progress` hook logging per-palier
   mode/checks/assigned/impossible counts) run against a real 22×18 grid
   surfaced it directly: right after a "fresh" (cleanup) palier, the
   winning attempt's own diagnostics showed **`assigned=0`** — every one
   of the confirmed words the cleanup had just carried forward as
   `locked_letters` vanished from `best_assignment`, even though a large
   fraction of the grid was already fully determined by those same locked
   letters. Root cause: `_pattern_attempt` only ever merged `locked_letters`
   into `forced_letters` (a per-cell *hint* for `Filler._domain`, see
   `sample_letter_biases`'s own docstring) — never into an actual
   pre-assignment the way `_pattern_continue`'s own `preseed_assignment`
   already does. A slot fully determined by locked letters still had to be
   explicitly *selected and confirmed* by `_backtrack` before counting as
   "assigned" in `best_assignment` — and if the search died at `checks=1`
   (an unrelated slot elsewhere already impossible, the exact scenario
   `excluded_slots` was built to handle) before ever reaching it, that
   already-known content was silently discarded, exactly mirroring an
   earlier-fixed bug ("Une grille très remplie... a été presque entièrement
   vidée," see above) but through a different code path this time
   (`_pattern_attempt`'s own diagnostics, not `_build_retry_seed`'s).

   Fixed by giving `_pattern_attempt` the same recovery `_build_retry_seed`
   already does internally: when `locked_letters` is non-empty, it now
   also computes a `preseed_assignment` — for every slot whose cells are
   *all* covered by `locked_letters`, the word they spell
   (`"".join(locked_letters[cell] for cell in cells)`) is validated
   against the real dictionary via `_slot_candidate_count(_worker_index,
   len(cells), cells, locked_letters)` (the same per-position
   set-intersection already used elsewhere in this file) — only a
   genuinely real word gets preseeded; a locked-but-invalid combination
   (an already-known-impossible slot) is deliberately left `None` so it
   still surfaces correctly through the normal empty-domain path and
   `impossible_slots`, rather than being silently hidden by a bogus
   preseed. This `preseed_assignment` is passed straight into `try_fill`
   — the exact same parameter `_pattern_continue` already uses, no new
   mechanism needed, just wiring an existing one into the other code path
   that needed it too. Verified live: re-ran the exact 22×18 scenario that
   exposed the bug — the palier right after a cleanup now shows a
   substantial recovered `assigned` count (69, then later 61, 46 — never
   0 again) instead of discarding everything.

   **Whether pre-fill's own cells count toward `black_enrichment_fraction`'s
   target was inverted next**, at the user's explicit request: "les cases
   noires ajoutées en pré-remplissage comptent pour l'objectif de
   remplissage en noir." Previously, `make_pattern` computed the fraction
   on the cells still white right *after* pre-fill (`len(candidates)`,
   reassigned by `_prefill_unfillable_slots`'s own return value) and added
   it *on top* of `placed` unconditionally (`target = max(placed,
   round(rows*cols*black_ratio)) + round(black_enrichment_fraction *
   len(candidates))`) — so pre-fill's own cells never counted toward this
   specific percentage target, however many of them there were. Fixed by
   capturing `initial_white_count = len(candidates)` right after the
   initial shuffle, *before* pre-fill ever runs, and folding the fraction
   into the same `max(...)` as `placed` and the `black_ratio` floor
   instead of adding it on top: `target = max(placed, round(rows*cols*
   black_ratio), round(black_enrichment_fraction * initial_white_count))`.
   Since `placed` already includes whatever pre-fill itself placed (it's
   recomputed right after pre-fill runs), this means pre-fill's cells now
   genuinely count toward reaching the percentage: if pre-fill alone
   already placed more cells than the target percentage of the *original*
   white-cell count calls for, `_place_black_cells` adds nothing further
   for this reason at all (`placed` wins the `max`); if it placed fewer,
   only the shortfall gets added on top. Verified: an isolated arithmetic
   reproduction of both formulas confirmed the expected divergence — with
   a 15×10 grid (150 cells) and a 10% enrichment fraction, a scenario
   where pre-fill already placed 30 cells kept the new target at exactly
   30 (no more added) while the old formula would have added another 12
   on top (target 42); a scenario where pre-fill placed only 2 cells gave
   both formulas a similar target (17 old vs. 15 new, the small gap being
   the "before" vs. "after pre-fill" white-cell-count basis difference) —
   a real `generate_grid()` run on both seeds of the standard 15×10
   benchmark (with `black_enrichment_fraction=0.14`, matching the UI's own
   new default just below) confirmed no regression (0 empty white cells
   and 0 mismatches each: seed 2 in 119.3s, 54 words, 42 black cells;
   seed 7 in 152.7s, 55 words, 31 black cells).

   This landed alongside two related web-UI changes, both at the user's
   explicit request. First: "Ajout noires"'s client-side auto-computed
   default (`round(0.3 * sqrt(width * height))`, recalculated on every
   width/height change until the player edited it themselves — see the
   entry above) was replaced by a plain fixed default of **14%**
   ("Intialiser 'Ajout noires' à une valeur fixe de 14%") — matching
   "Lettres forcées"/"Graines"'s own long-standing plain-static-default
   convention (no formula at all) rather than remaining a special case;
   `updateDefaultBlackEnrichment()`, `blackEnrichmentManuallyEdited`, and
   the width/height listeners that drove them were deleted from
   `script.js` entirely. `#black-enrichment`'s static HTML `value`
   (`index.html`) changed from `"3"` to `"14"`, and
   `GenerateRequest.black_enrichment_percent`'s own backend default was
   raised from 3 to 14 to match, keeping the UI's initial value and the
   API's default in sync (the same convention already followed for
   `force_letters_percent`). The fixed 14% default reflects the new
   semantics above (a percentage of the *original*, pre-pre-fill
   white-cell count) rather than the old auto-formula, which was tuned
   for the old semantics and stopped making sense once the percentage's
   own meaning changed. Second: the field itself was renamed from "Ajout
   noires" to **"Taux noir"** ("Renommer 'Ajout noires' en 'Taux noir'"),
   translated per language in `frontend/static/i18n.js`'s
   `blackEnrichmentLabel` (en "Black rate", de "Schwarzanteil", es "Tasa
   de negro", it "Tasso di nero") rather than left untranslated, matching
   how every other UI label in this project is handled — only the
   internal identifiers (`black_enrichment_percent`, `id="black-
   enrichment"`) stayed unchanged, per this project's own convention
   (English code identifiers, translated UI text).

   **A new rule was added to the full-nettoyage path** (`_build_retry_seed`
   plus `generate_grid`'s own selection loop), at the user's explicit
   request: "lors du nettoyage des emplacements injouables, ajouter une
   case noire (une seule en tout) au hasard sur les cases qui étaient dans
   les emplacements injouables (tentative de ne pas reproduire les mêmes
   erreurs en verrouillant progressivement les configurations
   problématiques)." `_clean_all_candidates` (in `generate_grid`'s nettoyage
   branch) now also computes, per candidate, the set of cells belonging to
   any of its `impossible_slots`, returned as a 4th tuple element
   (`cand_impossible_cells`) alongside the existing `(cand_seed,
   cand_confirmed, cand_slots)`. Once the winning cleaned candidate is
   selected via the existing `_words_in_place_score` `max(...)` (both the
   normal pass and the fixed-point-breaking `exclude_impossible_locked=True`
   pass, if it runs), exactly one cell from that winner's own
   `winning_impossible_cells` is drawn — shuffled with the palier's own
   seeded `rng` (the same one already used to derive each parallel
   attempt's own seed, so this stays reproducible from the top-level
   `seed`) and tried in order, turning the first one that keeps
   `is_structurally_valid(min_interior_free=1)` (the absolute connectivity/
   no-orphaned-cell invariant) into a black cell on `carry_seed_grid`; if
   none of the candidates validate, no cell is added at all (accepted
   limit case, matching this file's established pattern elsewhere for an
   unsatisfiable placement). Placed *once per full nettoyage*, never once
   per candidate — the draw happens only after the winner is already known
   — and never applies to the "continue verbatim" path at all, which by
   its own design never touches a black cell (see its own entry above).
   Verified: two isolated tests confirmed the core mechanics directly — a
   random draw among impossible-slot cells always lands one of them black
   and exactly one cell total; a candidate whose blackening would break
   structural validity (reproduced against a real generated pattern, not a
   contrived one: brute-forced every white cell of a real `make_pattern()`
   output to find one that genuinely invalidates the grid once blackened)
   is correctly skipped in favor of a valid one, regardless of which order
   the two are tried in.

   **A follow-up precision from the user changed *which* cell within that
   set gets preferred**: "Précision : ajouter la case noire avant de
   nettoyer les emplacements impossible. S'il existe des cases encore
   blanches dans ces emplacements impossibles, privilégier de noircir une
   case blanche. Sinon, noircir une case avec une lettre." This matters
   because, as originally implemented, the candidate cells were gathered
   from the *already-cleaned* grid — at which point every impossible-slot
   cell is unconditionally blank (`_clean_blocked_slots` has already
   stripped every crossing word through it), making a "blank vs. lettered"
   distinction impossible to ever observe. Fixed by moving the
   classification earlier: `_clean_all_candidates` now builds a
   `cand_raw_letters` map (cell → letter) directly from the candidate's
   *raw*, pre-cleanup `cand_diag["assignment"]` — before `_build_retry_seed`
   (and its own internal `_clean_blocked_slots` call) ever runs — and
   splits `cand_impossible_cells` into `cand_blank_impossible_cells` (no
   crossing assigned word touches this cell) and
   `cand_lettered_impossible_cells` (one does), returned as two separate
   tuple elements instead of one. The final draw becomes `list(winning_
   blank_impossible_cells) or list(winning_lettered_impossible_cells)` —
   Python's `or` naturally picks the blank group whenever it's non-empty,
   falling back to the lettered group only when every impossible-slot cell
   already carries a letter — with the same shuffle-then-first-valid logic
   as before applied within whichever group was chosen (no fallback to the
   *other* group if every candidate in the chosen one fails structural
   validity, matching the literal existence-based wording of the request
   rather than adding an unrequested extra fallback tier). Note that this
   reordering doesn't change *which* crossing words get removed by
   `_clean_blocked_slots` — its own removal criterion ("does some assigned
   word cross any cell of this impossible slot") already covers every cell
   of the slot unconditionally, whether or not one of them later gets
   individually blackened — so no change was needed to that step itself,
   only to *when* and *from what state* the blank/lettered classification
   is read. Verified: an isolated test of the group-preference logic
   (`or` fallback) confirmed the blank group is used whenever non-empty,
   without ever touching the lettered group's own cells, and that the
   lettered group is used only when the blank group is empty; a second
   isolated test reproduced the classification itself against a hand-built
   crossing scenario (an assigned slot crossing an impossible one at
   exactly one cell) — confirmed only that one shared cell is classified
   "lettered", the impossible slot's two other cells "blank"; a real
   `generate_grid()` run on both seeds of the standard 15×10 benchmark
   confirmed no regression (0 empty white cells and 0 mismatches each: seed
   2 in 234.8s, 56 words; seed 7 in 76.9s, 55 words); the deliberately hard
   10×10-grid/full-French-dictionary scenario (`difficulty="hard"`, not
   the retired 400-word restriction — see the `project-best-practices`
   SKILL's new permanent rule 12, added after this same session's own
   diagnostic script mistakenly reached for a 400-word restriction again)
   also succeeded after the refinement, its own black-cell count climbing
   across paliers (10 → 17-30) exactly as expected of a search that's
   still making progress rather than stuck.

   **The lock-one-cell rule was extended to the "continue verbatim" path
   too**, at the user's explicit request, reported directly from live
   observation: "il faut ajouter une case noire à tous les tours où on
   nettoie les emplacements injouables (pas seulement quand on nettoie
   aussi les cases noires). Est-ce le cas ? Je ne vois pas les cases
   noires ajoutées à chaque tour avec une zone injouable." Correct as
   reported: the rule only ever ran in the full-nettoyage (`else`) branch;
   the continue-verbatim (`if still_has_hope:`) branch already runs its
   own `_clean_blocked_slots` call (removing words crossing an impossible
   slot, per its own long-standing "nettoyer les emplacements bloqués,
   mais pas les noires" rule) but never applied the new lock at all.
   `_impossible_cell_groups`/`_lock_one_impossible_cell` were factored out
   as two small module-level functions (right after `_clean_blocked_slots`)
   from what used to be inline logic in the nettoyage branch's own
   `_clean_all_candidates`/final-draw code, so both branches could share
   the exact same selection logic (blank-preferred, pre-cleanup
   classification, shuffle-then-first-valid) without duplicating it a
   third time.

   Extending this naively (add the same call right after the continue
   branch's own `_clean_blocked_slots`, using `selected_grid` directly)
   would have introduced a real, serious bug — caught by code review
   before ever running it live, not from a failure report: `carry_seed_
   grid = selected_grid` was a direct reference (no copy) to `failed_
   pairs[0][0]`, an object also used earlier in the same iteration for
   `last_examples`/`last_diag`; mutating it in place to add the lock cell
   would have silently corrupted that already-recorded preview data.
   Fixed with a defensive copy (`carry_seed_grid = [row[:] for row in
   selected_grid]`), the same pattern already used everywhere else in this
   file before mutating a grid meant to be modified (see `_build_retry_
   seed`'s own `new_grid`, or the "before optimization" preview snapshot
   elsewhere in this file).

   A second, deeper bug was also caught by code review at the same time,
   specific to this path (the full-nettoyage path never had it, see
   below): the continue-verbatim path carries forward `carry_preseed_
   assignment` (a *position-indexed* list, one entry per slot) and
   `carry_excluded_slots` (slot *indices*) to the next palier, both
   derived from `selected_slots = extract_slots(selected_grid, rows,
   cols)` computed *before* the lock cell is added. Locking a cell that
   splits or shortens the targeted impossible slot changes `extract_slots`'s
   own row-then-column scan order for every slot that comes after it in
   the grid — potentially shifting the numeric index of many other,
   completely unrelated slots too (verified directly: in a hand-built
   5×5 grid, splitting one row/column pair by locking a single cell in the
   middle shifted a same-shape, entirely untouched slot elsewhere in the
   grid from index 9 to index 11). Reusing the old indices against the
   *new* slot structure the next palier's own `try_fill` would compute
   (fresh, from the now-mutated grid) would silently apply confirmed
   letters to the wrong slots, or exclude the wrong ones — this concern
   never applied before, since `carry_seed_grid` was previously always
   left byte-for-byte identical to `selected_grid` (an unmutated grid
   always regenerates the exact same slot list/order from `extract_slots`,
   a pure function). Fixed by rebuilding both, after the lock, against a
   *fresh* `extract_slots(carry_seed_grid, rows, cols)` call: `carry_
   preseed_assignment` is reconstructed from `cleaned_confirmed`
   (`_clean_blocked_slots`'s own second return value, a cell → letter
   dict, immune to index shifting by construction since it's keyed by
   coordinates, never by slot number) — a new slot's word is only filled
   in if *every* one of its cells is present in `cleaned_confirmed`;
   `carry_excluded_slots` is rebuilt by matching each old impossible
   slot's exact cell tuple against the new slot list — a slot untouched by
   the lock (its cells identical) is re-excluded at its new index; the one
   just split/shortened by the lock is not (no new slot has that exact
   old cell tuple anymore), so its fresh fragment(s) become freely
   attemptable again — which is the entire point of locking that cell in
   the first place. The full-nettoyage path never had this problem: it
   only ever threads `locked_letters` (cell-keyed, never slot-indexed) to
   the next palier, so nothing needed rebuilding there regardless of
   whether the lock cell shifts any slot's numbering.

   Verified: an isolated reproduction (a 5×5 all-white grid, one slot
   assigned a real 5-letter word in a column disjoint from either of two
   declared-impossible columns, one of which gets locked/split by the new
   rule while the other stays untouched) confirmed three things at once —
   the assigned word's confirmed letters land correctly on the *new* index
   of its own slot (not the stale old one, which now denotes a completely
   different slot after the shift) and are correctly absent from that
   stale old index; the untouched impossible column is correctly
   re-excluded at its own shifted new index; the just-split impossible
   column is correctly no longer excluded at all under either of its new
   fragments. A real end-to-end `generate_grid()` run on both seeds of the
   standard 15×10 benchmark confirmed no regression (0 empty white cells
   and 0 mismatches each: seed 2 in 86.4s, 57 words; seed 7 in 79.8s, 52
   words); the same hard 10×10/full-French-dictionary
   scenario as above (`difficulty="hard"`, seed 7) succeeded again, this
   time showing the black-cell count climbing by exactly +1 per
   consecutive continue-verbatim palier (10, 11, 12, 13, 14 across 5
   successive paliers) — direct, unambiguous confirmation that the rule
   now genuinely fires on this path too, unlike before this extension
   where the continue path's own black-cell count never moved at all
   between consecutive paliers.

2. **CSP fill** (`Filler` / `_backtrack`): `extract_slots` turns the black/white pattern
   into across/down word slots; `build_index` pre-indexes the word list by
   `(length, position, letter)` so slot domains can be computed by set intersection
   instead of a linear scan (needed because the full lexicon is 100k+ words). Before the
   real search even starts, `_pattern_attempt` calls `sample_letter_biases` —
   **unconditionally, on every single pattern attempt regardless of
   `force_letters_fraction`** (`0.0` by default, i.e. no cells actually forced, both in
   `generate_grid()` and in `backend/app.py`'s `GenerateRequest`) — using that fraction
   only to control how many cells (if any) get *forced*, not whether the statistical
   sampling itself runs at all. This used to run unconditionally on every attempt at a
   fixed 5% fraction, then became conditional on `force_letters_fraction > 0` once it
   became a web UI option (a `<select>` in `frontend/static/index.html` with five fixed
   choices — 0%, 1%, 2%, 5%, 10%, defaulting to 0% — rather than a free-form field,
   validated server-side against that exact set in `backend/app.py`'s `generate()`
   endpoint, `FORCE_LETTERS_PERCENTS = (0, 1, 2, 5, 10)` — and converted from a whole
   percent to a fraction, `req.force_letters_percent / 100`, right before calling
   `generate_grid()` — see `frontend/static/script.js`'s form submit handler and
   `i18n.js`'s `forceLettersLabel` for all 5 UI languages), at the user's explicit
   request — an earlier version of this same option was a plain on/off checkbox
   (always the fixed 5% fraction when checked), then replaced by this percent selector
   at the user's own explicit follow-up request for finer control over the fraction
   itself. That conditional-sampling version was itself later reverted at the user's
   own explicit follow-up request, once the candidate-word ranking mechanism below
   (`Filler.letter_scores`/`_candidate_score`) existed: that ranking is valuable
   independently of whether any cell actually gets *forced*, so gating the whole
   sampling step behind `force_letters_fraction > 0` was silently disabling a useful,
   unrelated feature (word-candidate ranking) whenever letter-forcing itself was left
   off (the default) — `sample_letter_biases` now always runs, and only its own
   `forced` dict (not `letter_scores`) actually depends on `force_letters_fraction`
   (its own `target = round(total_white * force_fraction)` naturally resolves to `0`
   forced cells when the fraction is `0.0`, no special-casing needed — see its
   docstring). Verified live: called `_pattern_attempt` directly with
   `force_letters_fraction` at `0.0`, `0.01`, and `0.10` on the same seed/grid — the
   failure diagnostics' `forced_cells` came back `None` (fill succeeded, so the
   failure-only diagnostics branch never ran) at `0.0`, one forced cell at `0.01`, and
   five at `0.10`, scaling with the fraction as expected; submitted real
   `POST /api/generate` requests with `force_letters_percent` at `5`, omitted
   (defaults to `0`), and an invalid `3` — confirming `backend.log` shows
   `force_letters_percent=5`/`force_letters_percent=0` for the first two, and the third
   correctly rejected with a 400 and a clear French error naming the allowed values.
   Separately re-verified after decoupling the sampling call from
   `force_letters_fraction`: called `sample_letter_biases` directly at
   `force_fraction=0.0` on a real 15×10 pattern — `forced` came back empty (0 cells)
   while `letter_scores` came back fully populated (one `Counter` per white cell, 150
   on that grid), and a `Filler` built from that same `letter_scores` correctly
   reports it as truthy, confirming the candidate-ranking step below now genuinely
   engages even at the 0% default; a real end-to-end `generate_grid()` run (15×10,
   seed 2, easy, the default `force_letters_fraction=0.0`) succeeded in 288.26s — 56
   words, 26 black cells, zero mismatches between the placed words and the solution
   grid — comparable to this project's prior timings for this same seed/shape (the
   70-290s range measured throughout this whole exploration), confirming
   `sample_letter_biases` now running on every attempt (rather than being skipped
   entirely at the 0% default) doesn't meaningfully regress the common case. For
   every slot, draw
   `LETTER_BIAS_SAMPLE_SIZE` (100) words at random — filtered only by length, not validated
   against any other slot, a plain uniform sample, not weighted by the wordlist's own
   frequency column — then tally, per cell, which letter came up most often in that sample.
   Any cell whose winning letter's tally exceeds `LETTER_BIAS_MIN_COUNT` (10) out of the
   100-word sample is *eligible* to be locked in as a soft hint (not a real assignment — see
   `Filler.forced_letters`/`_domain` below) — a weak consensus (a letter that only "won"
   because every other letter was even more scattered, not because it genuinely dominates)
   doesn't reliably mean enough real words remain once forced. Eligible cells are then drawn
   **at random** (not "highest tally first", an earlier version of this rule — see below) up
   to `force_fraction` (the UI-selected percent, 5% by default in this constant's own
   original single-fraction design — `LETTER_BIAS_FORCE_FRACTION` — lowered from 10% at
   the user's explicit request, before the percent selector existed) of
   the grid's white-cell count, **at most one forced cell per slot** — a crossing cell spends
   the quota of *both* slots touching it. That one-per-slot cap was added after live testing
   found the literal per-cell-independent version broken: two or more cells forced
   independently on the same long slot are each just "the mode of 100 separate, position-
   blind samples," with no guarantee any single real word actually has all of those letters
   at once — measured directly, one 15-letter slot's forced letters matched *zero* real
   words, and up to 9 of 10 benchmark seeds failed the very first `_backtrack` check outright
   (`checks=1`) before the cap; zero such instant failures after it, verified on the same 10
   seeds. The random draw (`rng.shuffle(eligible)` before the same one-per-slot selection
   loop) replaced an earlier "sort eligible candidates by tally descending, take the highest
   first" version, at the user's explicit request, after a reported problem: taking the
   strongest consensus first meant the *same* dominant letter of the language (whichever
   letter happens to be most common at a given position across many same-length slots) ended
   up forced onto most slots of that length, run after run — not much actual variety.
   Verified live with a direct before/after comparison across 15 patterns: for length-10
   slots (the most common length on the standard benchmark), the old sorted-first version
   forced only 4 distinct letters across 55 forced cells with one letter ('S') dominating
   58% of them; the random version reached 8 distinct letters across 60 forced cells with
   the top letter down to 40% — roughly double the variety, less dominated by any one
   letter. `Filler.forced_letters` (a `{cell: letter}` dict, empty by default)
   is folded into `_domain`'s own constraint-gathering as a fallback: a cell's real crossing
   assignment (once made) always wins over the forced hint, which only applies as long as
   neither slot through that cell has actually been assigned yet. Backtracking
   search runs a 5-tier selection rule at every pick, at the user's explicit request:
   `LOW_DOMAIN_MRV_THRESHOLD` (10 — raised from 5 earlier in the same exploration, after a
   report of fill attempts failing too often, to switch to MRV earlier, while a shrinking
   slot still has more room to be caught before it starves completely) still takes absolute
   priority — if *any* unassigned slot's domain (not just the one that would otherwise be
   picked) has dropped below it, the candidate pool narrows to just the slot(s) tied for the
   smallest domain (classic MRV pre-selection), skipping every later tier entirely.
   Otherwise, the pool is first narrowed by *direction*: `Filler.directions` (precomputed
   once per slot in `__init__`, same across-vs-down convention as `build_word_entries`) picks
   between the still-unassigned across slots and the still-unassigned down slots with a
   weighted random draw — probability proportional to how many free slots remain in each of
   the two categories (`self.rng.choices([free_across, free_down], weights=[len(free_across),
   len(free_down)])`) — so the fill naturally alternates/self-balances between the two
   categories over the course of a search rather than exhausting one before touching the
   other, without pinning down a strict fixed order. Within whichever direction was drawn,
   the next priority is *most remaining free (still-undetermined) cells* — `len(slot) -
   Filler._placed_letter_count(i)` — on the reasoning that a slot with many cells not yet
   fixed by a crossing is more exposed to picking up an unfavorable constraint from a
   not-yet-placed crossing slot later on, so it's worth resolving while it still has the most
   room to maneuver. Only *then*, among whichever slot(s) tie on that, does
   `Filler._placed_letter_count(i)` itself (the most already-placed letters) break the tie —
   this is deliberately the *opposite* preference from the tier just above (most free cells
   vs. most placed letters), so it only ever matters among slots that already tied on free-
   cell count, not as a competing global ranking. Whichever tier ends up mattering, the final
   draw among the resulting candidate pool is the same length-weighted random choice used
   throughout this whole exploration (favors longer slots) — one shared weight formula, only
   the candidate pool differs depending on which tier applies. This replaced a 4-tier design
   (MRV, then direction, then placed-letter-count directly, no free-cell-count step at all)
   that itself had replaced a simpler 3-tier one, which itself had replaced a single-tier
   design, which itself had replaced a much more elaborate pick-count-windowed design; see the
   project-best-practices SKILL for the full history of this whole area. Verified live: a
   real-search trace (15,160 picks) against an independently recomputed "which slots should be
   eligible" check, now covering every one of the 5 tiers (direction consistency, then the
   free-cell-count pool, then the placed-letter-count pool within it) — zero mismatches; a
   real `generate_grid()` run (15×10, seed 2) succeeded in 151.02s, structurally valid, every
   placed word matching the solution grid.

   Tiers 3 and 4 were later inverted, at the user's explicit request — a
   genuine ambiguity surfaced first and was resolved by asking directly
   rather than guessing: the user's own detailed description of "tier 3"/
   "tier 4" matched the *already-implemented* order exactly (most free
   cells primary, most placed letters as tiebreak), so "invert 3 and 4"
   read literally would have undone itself; asked directly, the user
   confirmed they wanted a genuine inversion of priority, not a
   restatement of the current behavior. `Filler._backtrack` now filters by
   `_placed_letter_count(i)` (most already-placed letters) *first*, then
   breaks ties among that pool by remaining free-cell count — the exact
   reverse of the pool-then-subpool order used before. Verified with an
   isolated reproduction of the new selection logic against hand-picked
   counts (three candidates, two tied for most placed letters despite very
   different free-cell counts, one clearly behind on placed letters despite
   having the most free cells of the three) — the tied-on-placed-letters
   pair is correctly selected as the tier-3 pool (excluding the
   most-free-cells-but-fewest-placed-letters candidate), and the tier-4
   tiebreak correctly picks the one with more free cells from within that
   pair, not from the full set of three.

   Tier 4's own direction was inverted again later, at the user's explicit
   request, this time on its own (tier 3 unchanged) — from "most free
   cells" back to **fewest free cells**, with a deliberately different
   rationale than the tier-3 preference it once shared: "pour tenter de
   construire des zones solides qui surviveront au nettoyage en fin de
   tour." Among slots already tied on tier 3 (most placed letters), the
   one with fewer *remaining* free cells is proportionally closer to
   being fully cross-locked — treating it next tends to finish
   solidifying a compact, densely-crossed zone rather than spreading
   attention to a longer slot with the same absolute placed-letter count
   but more of its own length still undetermined. This matters
   specifically for the cross-palier retry mechanism (`_build_retry_seed`,
   see section 1 above): a fully or near-fully cross-locked slot is far
   more likely to survive a cleanup pass intact (its own boundary/
   sandwiched-black-cell logic protects fully-determined zones more
   reliably) than a longer, still-partially-open one with the same raw
   progress. Just one line changed (`max_free`/`free_cell_counts[i] ==
   max_free` to `min_free`/`== min_free`) — tier 3's own selection
   (`max_placed`) is untouched. Verified with the same style of isolated
   reproduction as the original tier 3/4 inversion: four candidates tied
   in pairs on tier 3 (placed_counts), each pair with different lengths
   (hence different free-cell counts) — confirmed the tiebreak now
   correctly picks the *shorter* (fewer-free-cells) slot from each
   tied-on-tier-3 pair, the reverse of the previous behavior; a real
   `generate_grid()` run (15×10, seed 2) confirmed no regression to the
   common case — still succeeds, 0 mismatches between the placed words
   and the solution grid.

   A deep investigation into "les grilles d'étape ne sont pas remplies,
   alors qu'il semble y avoir beaucoup de vide facile à remplir" was
   carried out next, at the user's explicit request to verify the
   pre-fill phase (`_prefill_unfillable_slots`) itself was working
   correctly. Temporary instrumentation (a postcondition check right
   after every `make_pattern` call, confirming every slot really has
   `>= PREFILL_MIN_WORD_COUNT` candidates given `locked_letters`, plus a
   log of *why* `_prefill_unfillable_slots`'s own loop exits) run against
   a real 22×18 generation found the postcondition genuinely violated —
   but not for the reason first suspected (the candidate-cell pool
   running out before every deficient slot could even be examined): the
   loop always exits with `reason=no_more_deficient_slots` (every
   deficient slot *was* examined), but a large and *growing* fraction of
   them — 83, then 99, 128, 147, 149 across successive continue-mode
   paliers on this one grid — end up marked `unfixable` (no black-cell
   placement found that both targets the slot and passes
   `is_structurally_valid`), not fixed. This is a direct consequence of
   the same statistical reality documented below (almost any locked
   letter drives a slot's real candidate count below 10, for nearly any
   length) colliding with pre-fill's only available remedy (shortening
   the slot via a black cell) — which very often either isn't available
   (the slot's own free cells are already exhausted/locked) or fails
   structural validity, so the "unfixable, rare edge case" fallback the
   original design anticipated turns out to be the *dominant* outcome on
   a well-progressed grid, not an exception. This diagnostic finding is
   what directly motivated the MRV-removal decision documented next: a
   growing population of genuinely near-hopeless, low-candidate slots
   is normal now, so a rule that unconditionally prioritizes searching
   them over everything else needed to go.

   The **MRV tier itself (tier 1, `LOW_DOMAIN_MRV_THRESHOLD`) was removed
   entirely**, at the user's explicit request, after a deep investigation
   (triggered by a report that "les grilles d'étape ne sont pas remplies,
   alors qu'il semble y avoir beaucoup de vide facile à remplir") found
   that MRV's own original justification no longer held. Live diagnostics
   on a real 22×18 grid confirmed: once a decent fraction of the grid is
   locked (via the "reprise telle-quelle" mechanism, see section 1), the
   *vast majority* of slots touching a locked letter end up with very few
   real dictionary candidates — not a rare edge case, but the normal,
   expected statistical outcome of intersecting almost any single fixed
   letter against a finite word list, regardless of length. MRV's rule
   forced the search to prioritize *any* such slot over everything else,
   including plentiful, easy, well-covered slots elsewhere — meaning the
   search could spend a large share of its budget wrestling with a
   near-hopeless short slot instead of making progress elsewhere, exactly
   matching the reported symptom. The user's own diagnosis for why this
   is safe to remove now: MRV's rationale ("fail fast on the most
   constrained variable, within one single monolithic fill attempt")
   assumed a single fill attempt had to succeed outright — it no longer
   fits how the grid is actually built today, progressively, across many
   small paliers that carry confirmed progress forward and cooperatively
   detect genuine impossibility along the way (`used_words`-aware
   `impossible_zone_slots()`, `_pattern_continue`'s own preseed/exclusion
   mechanism — both added earlier in this same investigation). `_backtrack`
   now always goes straight to the direction/placed-letters/free-cells/
   length-weighted selection (renumbered 1-4, the former tiers 2-5) with
   no domain-size-based override at all — a low-domain slot is now
   selected (or not) purely on the same footing as every other slot,
   never force-prioritized. `LOW_DOMAIN_MRV_THRESHOLD` was deleted outright
   (unused elsewhere) rather than left as dead code. Verified live: a real
   `generate_grid()` run on the standard 15×10 benchmark (seed 2) confirmed
   no regression (succeeds, 0 mismatches); a real run on the exact 22×18
   scenario that previously plateaued for 180+ paliers under the old MRV
   rule was re-run under the new one to confirm whether removing MRV
   actually resolves the reported stall (see the entry immediately above
   this one for the full diagnostic trail that led here).

   **Reinstating the MRV tier was tried next, then explicitly rejected by
   the user.** Reported directly from a real generated grid (screenshot:
   "Génération interrompue" with an attempt preview showing only a small,
   well-crossed island of words surrounded by a very large blank area)
   alongside "Il y a très peu de mots, et du blanc partout, alors que pour
   le premier tour, rien n'était bloqué. Le remplissage ne fonctionne pas
   correctement !" — pointing specifically at the very first palier, where
   nothing is locked at all yet, which the MRV-removal rationale above
   never actually addressed (it only reasoned about paliers that
   *reprennent* a locked content). Root-caused by tracing `_backtrack`'s
   selection code directly: on a completely blank grid, every slot ties at
   0 for "most already-placed letters" (tier 2 of the 4-tier rule, since
   nothing is crossed yet at all), so the tiebreak right below it ("fewest
   remaining free cells") degenerates into "shortest slot first" — a
   purely geometric ordering with zero regard for how many real dictionary
   candidates a slot actually has, which can leave whole regions of the
   grid starved of crossing letters until the search's budget runs out,
   matching the reported symptom. Restoring `LOW_DOMAIN_MRV_THRESHOLD` and
   the tier-1 MRV pre-selection verbatim was tried as a fix and verified to
   resolve the symptom in isolation (a real `generate_grid()` run on the
   standard 15×10 benchmark, seed 2, completed in 53.3s with zero white
   cells left without a letter) — but the user rejected this specific fix
   outright: "Je ne t'ai pas demandé de réintégrer la priorité au MRV. Ma
   dernière demande sur ce sujet était justement de ne plus donner la
   priorité au MRV." Reverted immediately, in full: `LOW_DOMAIN_MRV_
   THRESHOLD` and the tier-1 MRV pre-selection are gone again, `_backtrack`
   is back to exactly the 4-tier rule described above with no domain-size
   override at all. The sparse-first-palier symptom itself is still real
   and still unresolved — whatever fixes it must not reinstate MRV's
   absolute priority.

   **Tiers 2 and 3 of the (now-MRV-free) 4-tier rule were inverted next**,
   at the user's explicit request ("Il faut inverser l'ordre de ces
   critère 2 et 3"), quoting the doc's own numbering from the post-MRV-
   removal 4-tier list: tier 2 was "most already-placed letters" (primary)
   and tier 3 was "fewest remaining free cells" (tiebreak) — now tier 2 is
   "fewest remaining free cells" (primary) and tier 3 is "most
   already-placed letters" (tiebreak), tiers 1 (direction) and 4
   (length-weighted random) untouched. In code: `_backtrack` used to
   compute `placed_pool` (filter by `max_placed`) first and take `min_free`
   only within that pool; it now computes `min_free` over the whole
   direction pool first (`free_pool`) and takes `max_placed` only within
   that. Note this swap is a no-op for the still-open sparse-first-palier
   symptom above: on a completely blank grid every slot ties at 0 placed
   letters regardless of which tier runs first, so both orderings reduce
   to the exact same "shortest slot first" degenerate case — this change
   only affects a palier where *some* crossings already exist. Verified:
   an isolated reproduction (three hand-picked slots — one with the fewest
   free cells but middling placed-letter count, one with the most placed
   letters but not the fewest free cells, one with neither) confirmed the
   slot with fewest free cells now wins regardless of its placed-letter
   count, matching the new tier-2-primary order; a real `generate_grid()`
   run on both seeds of the standard 15×10 benchmark (2 and 7) confirmed no
   regression to the ordinary case.

   **A new rule was added on top of the 4-tier rule, ahead of it in
   priority**, at the user's explicit request ("Nouvelle règle prioritaire
   sur les autres... Ne pas essayer de remplir les emplacements qui
   croisent un emplacement réputé impossible"): `_backtrack` now never
   selects a slot that shares a cell with one already in `excluded_slots`
   (a slot already known impossible, carried forward from a previous
   palier's own diagnostics — see `_pattern_continue`) — computed once as
   `Filler._crossing_excluded_slots` in `__init__` (via `cell_to_slots`,
   already precomputed there), since `excluded_slots` never changes after
   that point. Rationale: a word placed in such a slot would very likely
   get stripped right back out by the next cleanup anyway
   (`_build_retry_seed`'s own step 1 already removes any assigned word
   directly crossing an impossible slot) — so there's no point spending
   search budget filling it in the first place, only to have it undone
   later. This is a pure narrowing of the candidate pool, evaluated before
   any of the 4 tiers even run — it only has any effect at all once
   `excluded_slots` is non-empty (a "reprise telle-quelle" continuation
   palier); a fresh/cleaned-up palier (`excluded_slots` empty) is
   completely unaffected. Verified in isolation first: a hand-built 4-slot
   layout (one excluded slot, two slots each sharing a different cell with
   it, one entirely unrelated slot) confirmed exactly the two crossing
   slots land in `_crossing_excluded_slots` and the unrelated one doesn't.

   This first version immediately caused a **total regression** in real
   end-to-end testing, caught before it was ever reported to the user:
   three separate real `generate_grid()` runs (the standard benchmark's
   both seeds, and the deliberately hard 10×10/400-word scenario) all
   returned `None` — no fillable grid found in 200 paliers, every one of
   them. Root cause: `generate_grid`'s own `still_has_hope` check (which
   decides whether the next palier reuses the pattern "telle quelle" or
   falls back to a full nettoyage) only ever compared "how many slots are
   unassigned" against "how many are genuinely impossible" — it had no
   notion of a slot merely *avoided* because it crosses an impossible one.
   Such a slot is unassigned, but not counted as impossible either, so
   `still_has_hope` kept reporting `True` forever: the palier loop stayed
   stuck reusing the exact same pattern "telle quelle" for all 200
   attempts, since the newly-avoided crossing slot(s) could never be
   assigned (by design, per the new rule) yet never got flagged as
   hopeless either — nettoyage never triggered, so the grid could never
   actually finish. Fixed by adding a shared helper, `_slots_touching(slots,
   target_indices)` (the same cell-to-slot crossing computation
   `Filler.__init__` already needed, now factored out so both call sites
   share it instead of duplicating the logic), and using it in
   `generate_grid`'s own `still_has_hope` computation too: a slot crossing
   a known-impossible one is now folded into the same "dead" set as the
   impossible slots themselves, so `still_has_hope` is `True` only when a
   genuinely available (never excluded, never crossing an exclusion) slot
   remains — restoring the ability to correctly detect a true dead end and
   trigger nettoyage. Verified live after the fix: the same three scenarios
   that all failed outright before now all succeed — standard benchmark
   seed 2 in 174.8s (57 words, 0 empty white cells), seed 7 in 118.3s (60
   words, 0 empty white cells), and the hard 10×10/400-word scenario in
   21.5s (34 words, 0 empty white cells) — confirming the fix, not just the
   original request, actually works end to end.

   **Tiers 2-4 of the (now-MRV-free) rule were replaced by a single rule**,
   at the user's explicit request: "Remplacer les règles 2, 3, et 4, par
   une règle unique : tirer au hasard un emplacements dans les 10
   emplacements ayant le moins de cases blanche." `_backtrack` now computes
   `free_cell_counts` for the drawn direction pool, shuffles the pool with
   the attempt's own seeded RNG (specifically to avoid a repeat of the
   exact positional-bias bug class already fixed twice in this file —
   `_prefill_unfillable_slots`'s "solid black column"/"triangle" bugs —
   for ties at the window's own boundary), sorts by fewest remaining free
   cells, takes the first 10 (`window`), and draws uniformly at random from
   that window with `self.rng.choice(window)` — no more cascading
   placed-letters/length-weighted tiebreaks at all. Verified live: an
   isolated reproduction (12 slots tied at the same free-cell count, more
   than the window size of 10, plus 3 slots with a higher count) confirmed
   across 2000 seeded trials that every one of the 12 tied slots appears in
   the window a comparable number of times (1641-1690 out of 2000, no slot
   systematically favored or excluded) — the shuffle-before-sort genuinely
   removes the positional bias a plain stable sort would otherwise have
   introduced at the window boundary.

   A real end-to-end check confirmed the standard 15×10 benchmark still
   succeeds on both reference seeds (62.4s/56.1s, 0 empty white cells each
   — noticeably *faster* than the previous rule's 174.8s/118.3s on the
   same seeds). A first sweep also tried the deliberately hard
   10×10/400-word restricted-vocabulary scenario used throughout this
   project's earlier history to stress-test the cross-palier retry
   mechanism, and found it now failing outright where it used to succeed —
   but the user then retired that scenario as a test case entirely, at
   their explicit request: "Enlève ton test utilisant 400 mots, on
   n'utilisera jamais un dictionnaire aussi petit." A 400-word dictionary
   is not a realistic deployment scenario for this app (the real wordlists
   run into the tens or hundreds of thousands of words per language — see
   the data-pipeline section above), so a failure specific to that
   artificially small vocabulary is not treated as a real regression for
   this change; the standard-benchmark result above is what actually
   matters here.

   **A hard cap of 5 consecutive "continue" paliers was added next**, at
   the user's explicit request: "Limiter le nombre de tour réalisés sans
   nettoyage à 5 consécutifs maximum. A partir de 5, déclencher un
   nettoyage." A new `consecutive_continue_paliers` counter (initialized to
   0 alongside the other `carry_*` state before the palier loop) increments
   every time `still_has_hope` leads to a "reprise telle-quelle" decision,
   and resets to 0 every time a nettoyage actually runs; right before that
   decision, `still_has_hope` is forcibly overridden to `False` once the
   counter has already reached 5, regardless of what it would otherwise
   have been — so the 6th consecutive candidate for "continue" mode always
   triggers a nettoyage instead. This is a safety net independent of the
   `still_has_hope` correctness fix above: even with that fix in place,
   `still_has_hope` can still legitimately stay `True` for a long real
   streak (some other slot genuinely remains open and not yet proven
   impossible, but the search simply hasn't reached it many paliers in a
   row) — this cap guarantees a periodic nettoyage regardless, rather than
   letting a "continue" streak run for an unbounded number of paliers on
   the strength of `still_has_hope` alone. Verified: an isolated simulation
   of the exact increment/reset/override logic (a synthetic scenario where
   `still_has_hope` is always `True` on its own) confirmed the sequence of
   chosen modes is exactly 5 "continue" then 1 forced "nettoyage", repeating
   every cycle; a real `generate_grid()` run on both seeds of the standard
   15×10 benchmark (the *full* wordlist, not the now-retired 400-word
   scenario) confirmed no regression to the ordinary case.

   **This cap was later raised from 5 to 10**, at the user's explicit
   request: "Passer de 5 tours avant nettoyage, à 10 tours avant
   nettoyage." Only the literal threshold changed
   (`consecutive_continue_paliers >= 5` became `>= 10`) — every other
   mechanic (the counter's own increment/reset points, `still_has_hope`'s
   own correctness check, the all-abandoned force-nettoyage rule right
   above it) is untouched. Verified: the same isolated simulation as
   above, re-run with the new threshold, confirmed the sequence of chosen
   modes is now exactly 10 "continue" then 1 forced "nettoyage", repeating
   every cycle; a real `generate_grid()` run on both seeds of the standard
   15×10 benchmark confirmed no regression (0 empty white cells and 0
   mismatches each, 72.9s/70.2s).

   **This cap was raised again, from 10 to 50**, at the user's explicit
   request (quoting the doc's own current wording back, then asking to
   change "10 paliers" to "50"). Same minimal change as the previous raise
   — only `consecutive_continue_paliers >= 10` became `>= 50`, nothing
   else in the mechanic touched. Verified: the same isolated simulation
   re-run with the new threshold confirmed the sequence of chosen modes is
   now exactly 50 "continue" then 1 forced "nettoyage", repeating every
   cycle; a real `generate_grid()` run on both seeds of the standard
   15×10 benchmark confirmed no regression (0 empty white cells and 0
   mismatches each: seed 2 in 135.6s, 53 words; seed 7 in 133.6s, 62
   words).

   **This cap was lowered back down, from 50 to 10**, much later in this
   project's history, at the user's explicit request: "Nettoyage avec
   cases noires tous les 10 cycles (au lieu de 50)." Same minimal change
   as every previous adjustment to this cap — only
   `consecutive_continue_paliers >= 50` became `>= 10`, nothing else in
   the mechanic touched. Verified: the same isolated simulation, re-run
   with the restored threshold, confirmed the sequence of chosen modes is
   once again exactly 10 "continue" then 1 forced "nettoyage", repeating
   every cycle (the same pattern this cap produced the first time it was
   set to 10, earlier in this same history); a real `generate_grid()` run
   on both seeds of the standard 15×10 benchmark confirmed no regression
   (0 empty white cells and 0 mismatches each: seed 2 in 32.0s, 53 words;
   seed 7 in 19.5s, 48 words).

   **This cap was lowered once more, from 10 to 5**, right after, at the
   user's explicit request: "5 cycles au lieu de 10." Same minimal change
   as every previous adjustment — only `consecutive_continue_paliers >= 10`
   became `>= 5`. Verified: the same isolated simulation, re-run with the
   new threshold, confirmed the sequence of chosen modes is now exactly 5
   "continue" then 1 forced "nettoyage", repeating every cycle; a real
   `generate_grid()` run on both seeds of the standard 15×10 benchmark
   confirmed no regression (0 empty white cells and 0 mismatches each: seed
   2 in 119.1s, 59 words — noticeably slower than the cap-of-10 measurement
   just above, consistent with a lower cap forcing a full pattern-
   regenerating cleanup more often, each of which costs more than simply
   continuing on the same pattern; seed 7 in 42.8s, 53 words).

   **A live investigation into a user-reported symptom** ("après un
   nettoyage, la grille ne montre que les mots restants du tour précédent,
   pas de nouveaux mots") directly motivated the two rules described next.
   A real diagnostic run (22×18, seed 5, `on_progress` capturing every
   `pattern_attempt_failed` event's `reason`/`assigned_letter_count`)
   confirmed the symptom is real but transient: one fresh-pattern attempt
   right after a nettoyage occasionally makes exactly zero progress beyond
   the locked baseline (`checks=1`, `assigned_letter_count` identical to
   the carried-forward locked-cell count) — but the very next palier's own
   fresh attempt recovered with substantial real progress (90 → 189
   letters in the observed run). Confirmed this isn't a regression from
   any of this session's recent tier-selection changes by reproducing the
   identical failure with the *previous* 4-tier cascade rule on the same
   seed, before it was replaced by the window-of-10 rule above.

   **A slot-search abandon rule was added** to `Filler._backtrack`, at the
   user's explicit request: "Quand une situation de génération atteint
   plus de 30% de la grille réputée non remplissable, considérer le tour
   sur cette tentative comme échoué, et qu'il ne faut plus tenter d'ajouter
   des mots." `UNFILLABLE_ABANDON_FRACTION` (0.30) and
   `UNFILLABLE_ABANDON_CHECK_INTERVAL` (500, same throttling pattern as
   `CANCEL_CHECK_INTERVAL` — `impossible_zone_cells()` recomputes every
   unassigned slot's domain against `best_assignment`, a real cost not
   worth paying every single node) gate a new check near the top of
   `_backtrack`: every 500 calls, if `len(self.impossible_zone_cells()) >
   0.30 * self._total_white_cells` (`_total_white_cells`, `len(self.
   cell_to_slots)`, computed once in `__init__`), `self.abandoned` is set
   and every subsequent call returns `False` immediately with no further
   exploration. `try_fill`'s `diagnostics["reason"]` gained a new value,
   `"abandoned_too_unfillable"`, checked ahead of `"deadline_exceeded"`/
   `"blocked_on_excluded_slot"`/`"search_exhausted"`. Verified: an isolated
   test confirmed `self.abandoned` short-circuits `_backtrack` immediately
   (still counted in `self.checks`, but no exploration) and that the 30%
   threshold is a strict `>` (exactly 30% does not abandon, 31% does); a
   real `generate_grid()` run on both seeds of the standard 15×10 benchmark
   confirmed no regression (0 empty white cells each).

   **A second rule layers on top of the first**, at the user's explicit
   follow-up request: "Si les 10 tentative d'un tour sont étiquetées
   'échouées', déclencher un nettoyage (sur la meilleure grille)" — read in
   context as specifically about the new `"abandoned_too_unfillable"`
   label just added, not a generic "the palier failed" (which is already
   the precondition for reaching this code at all). In `generate_grid`'s
   loop, right after computing `still_has_hope`: if every one of the
   palier's raw `failed_all` outcomes has `reason ==
   "abandoned_too_unfillable"` — every one of the `PARALLEL_ATTEMPTS`
   workers independently concluded its own pattern was too far gone to be
   worth continuing — `still_has_hope` is forced to `False` regardless of
   what it would otherwise be, skipping straight to nettoyage on
   `failed_pairs[0]` (the existing "best" selection, already what
   nettoyage operates on) instead of letting a "continue" streak start on
   a pattern every worker already gave up on independently. Verified: an
   isolated check of the exact condition (all-abandoned → `True`; one
   dissenting reason among ten → `False`; empty list → `False`) behaves
   correctly; a real `generate_grid()` run on both seeds of the standard
   benchmark, combining both new rules, confirmed no regression (0 empty
   white cells each).

   **The window-of-10 ranking criterion itself was changed next**, at the
   user's explicit request: "Remplacer 2 : les 10 emplacements qui ont le
   moins de cases encore blanches / Part 2 : les 10 emplacements qui ont
   le plus de lettres déjà remplies." `_backtrack` now sorts
   `direction_pool` by `-placed_counts[i]` (most `_placed_letter_count`
   first) instead of `free_cell_counts[i]` (fewest remaining free cells) —
   `free_cell_counts` is no longer computed at all in this function. Same
   window size (10), same shuffle-before-sort tie-breaking, same uniform
   draw from the window — only the ranking criterion itself changed.
   Verified: an isolated reproduction (15 slots split into three groups of
   5, tied at placed-letter counts of 8/5/2 respectively) confirmed the
   window always contains exactly the 10 slots with the two highest tied
   counts (8 and 5), never any of the 5 weakest (2), across 200 seeded
   trials; a real `generate_grid()` run on both seeds of the standard
   15×10 benchmark confirmed no regression (0 empty white cells each,
   74.3s/60.3s).

   **A genuinely doomed-from-the-start slot no longer poisons an entire
   fresh search**, at the user's explicit request: "Le tour après une
   régénération semble s'arrêter dès qu'un emplacement est impossible, ce
   qui peut se produire immédiatement à cause du tirage des cases noires.
   Tous les tours doivent se dérouler aussi longtemps qu'on peut ajouter
   des mots en respectant les règles d'ajout." Root cause: `_backtrack`'s
   own domain-check loop (already existing, the `used_words`-aware fix
   from earlier in this file) unconditionally checks *every* unassigned
   slot's domain at *every* single node, and fails the whole call the
   moment any one is empty — correct in general (a real forward-checking
   dead end), but when a specific slot's domain is empty purely because of
   *fixed* constraints (locked letters from a previous palier, combined
   with a newly-drawn black cell that happens to cut it into an
   unmatchable shape) rather than anything the search itself chose, that
   emptiness can never change no matter what gets tried elsewhere — so
   `_backtrack` fails this same way on *every single call*, `checks=1` or
   close to it, before the search ever gets a chance to fill anything else
   in an otherwise perfectly fillable grid. New method
   `Filler.exclude_immediately_impossible_slots()`, called once in
   `try_fill` right after `preseed_assignment` is applied (if any) and
   right before `filler.solve()` — at that exact point, `self.assignment`
   reflects only genuinely fixed constraints, nothing the search itself
   has decided yet, so any slot found empty there is truly permanent for
   the rest of this search, not merely a transient conflict. Every slot it
   finds is folded into `self.excluded_slots` (exactly the mechanism
   already used for a slot known impossible from a previous palier) and
   `self._crossing_excluded_slots` is recomputed to match — one pass is
   enough, since excluding a slot never changes any other slot's own
   domain (`_domain` never consults `excluded_slots`). Verified: an
   isolated `Filler` built from two independent (non-crossing) slots — one
   forced, via `forced_letters`, into a letter no real word of its length
   starts with; one perfectly fillable — confirmed
   `exclude_immediately_impossible_slots()` correctly identifies and
   excludes only the doomed slot, and that `solve()` then fills the other
   slot normally in `checks=2`, rather than failing outright; a real
   `generate_grid()` run on both seeds of the standard 15×10 benchmark
   confirmed no regression (0 empty white cells each, 63.4s/77.4s).

   **Two "best grid" selection criteria were revised**, at the user's
   explicit request, both operating on `failed_pairs`/the cross-palier
   retry mechanism above, not on `_backtrack`'s own slot-selection rule:

   1. "Considérer qu'à la fin d'un tour, la meilleure grille est celle qui
      minimise le nombre de caractères considérés comme injouables."
      `failed_pairs` (the sort that picks `last_diag`/`last_examples` and
      the basis for "reprise telle-quelle") now sorts by
      `len(gd[1]["impossible_cells"])` ascending — fewest cells belonging
      to an impossible slot — replacing the previous criterion (fewest
      black cells), which said nothing about how much of the grid was
      actually blocked.
   2. "Après nettoyage, la grille à conserver pour le tour suivant, [est]
      celle qui maximise la somme des carrés des longueurs des mots en
      place après nettoyage." `_clean_all_candidates` now also returns each
      candidate's own `cand_slots` (a third tuple element, alongside the
      existing seed grid and confirmed-letters dict), and a new
      `_words_in_place_score(cand_slots, cand_confirmed)` helper sums
      `len(cells) ** 2` for every slot whose *every* cell is present in
      `cand_confirmed` (a word only counts if it's genuinely, fully
      "in place" — a partially-confirmed slot contributes nothing).
      Both `max(cleaned_candidates, key=...)` calls (the normal pass and
      the `exclude_impossible_locked=True` fixed-point-breaking pass) now
      use this score instead of the previous `(len(confirmed_letters),
      -black_cell_count)` tuple — the same sum-of-squares-of-word-lengths
      principle already used elsewhere in this function to pick among
      successful parallel attempts (a few long words outweigh many short
      ones for the same total letter count), now applied to picking among
      *cleaned* candidates instead of a raw letter/black-cell count.

   Verified: an isolated test of `_words_in_place_score`'s exact logic
   confirmed a single fully-confirmed 10-letter word (score 100) outscores
   ten fully-confirmed 2-letter words with the same total letter count
   (score 40), and that a slot missing even one confirmed cell contributes
   zero, not a partial score; a real `generate_grid()` run on both seeds of
   the standard 15×10 benchmark confirmed no regression (0 empty white
   cells each, 90.2s/85.4s).

   **The tier-2 window was widened from 10 to 30**, at the user's explicit
   request ("Augmenter à 30 emplacements qui ont le plus de lettres
   préremplies (plus d'exploration de la grille)") — `_backtrack`'s
   `window = sorted(shuffled_pool, key=lambda i: -placed_counts[i])[:10]`
   became `[:30]`, nothing else about the rule changed (same shuffle-
   before-sort tie-breaking, same uniform draw from the window). Verified:
   a real `generate_grid()` run on both seeds of the standard 15×10
   benchmark confirmed no regression (0 empty white cells each,
   86.2s/52.8s).

   **The tier-2 criterion itself changed from a raw count to a ratio, and
   the window shrank from 30 to 15**, at the user's explicit request:
   "tirer au hasard parmi les 15 emplacements ayant le meilleur score (nb
   de lettres assignées / longueur)." `placed_counts` (raw
   `_placed_letter_count`) was replaced by `placed_ratios`, dividing each
   slot's placed-letter count by its own length (`len(self.slots[i])`) —
   so a nearly-full short slot now ranks above a partially-full long one
   even if the long slot has more letters in absolute terms (e.g. a
   4-letter slot with 3 placed, ratio 0.75, now outranks a 20-letter slot
   with 10 placed, ratio 0.5, despite the second having more raw letters).
   Same shuffle-before-sort tie-breaking, same uniform draw from the
   window — only the ranking key and the window size (`[:30]` → `[:15]`)
   changed. Verified: an isolated reproduction (a 20-letter/10-placed slot
   vs. a 4-letter/3-placed slot vs. a 10-letter/9-placed slot) confirmed
   the ranking correctly favors the highest *ratio* (0.9, then 0.75, then
   0.5) regardless of raw placed-letter counts; a real `generate_grid()`
   run on both seeds of the standard 15×10 benchmark confirmed no
   regression (0 empty white cells each, 96.5s/77.9s).

   **The fixed-size window was dropped entirely in favor of an exact-tie
   selection**, at the user's explicit request: "tirer uniquement sur les
   emplacements avec le meilleur score int(remplies/(longueur*longueur))."
   Caught before implementing it literally: since a slot's placed-letter
   count can never exceed its own length, `remplies/longueur²` is always
   `≤ 1/longueur < 1` for every slot (length is always ≥ 2), so
   `int(...)` truncates to 0 for every single slot, unconditionally — the
   rule as literally written would have made tier 2 a complete no-op
   (uniform draw across the *entire* direction pool, no prioritization at
   all). Flagged directly to the user with the proof, rather than
   implementing something provably always-zero; the user confirmed the
   intended formula was `int(100 * remplies / (longueur * longueur))` (a
   ×100 scaling was missing). `_backtrack` now computes `scores = {i:
   int(100 * self._placed_letter_count(i) / (len(self.slots[i]) ** 2)) for
   i in direction_pool}`, takes `best_score = max(scores.values())`, and
   restricts `candidates` to exactly the slots tied for that score — no
   window size at all, and no shuffle-before-sort needed anymore either:
   unlike a fixed-size window (where sort order decided which tied slots
   fell on the right side of the cutoff), every slot tied for the true
   best score is always kept, so their original order can never bias which
   one `self.rng.choice(candidates)` ultimately picks. Verified: an
   isolated reproduction confirmed a uniquely-best-scoring slot (a fully-
   filled 2-letter slot, score 50) is the only candidate when it's alone,
   and that two slots genuinely tied for best (both fully-filled 2-letter
   slots) are both eligible and both get drawn across repeated trials with
   different seeds (no positional bias); a real `generate_grid()` run on
   both seeds of the standard 15×10 benchmark confirmed no regression (0
   empty white cells each, 65.5s/90.8s).

   **A real gap in this exact-tie score was found next, from a sharp
   diagnostic question rather than a bug report**: "Dans la règle
   précédente, est-ce que les lettres forcées (paramètre d'initialisation
   du tour) sont prises en compte ? Si oui, avec la règle, sur une grille
   vierge avec 1 case forcée, il devrait obligatoirement générer un mot
   sur la lettre forcée. Or, il la laisse sans essayer." Confirmed by
   reading `_placed_letter_count` directly: it only ever counted a cell as
   "already known" when a *crossing slot is really assigned*
   (`self.assignment[j] is not None`) — `self.forced_letters` (the
   statistical hint mechanism, see `sample_letter_biases`) was never
   consulted at all. On a genuinely blank grid with one forced cell,
   nothing has a real crossing assignment yet, so *every* slot scores 0 —
   including the one holding the forced letter — meaning it had no
   structural priority whatsoever and could easily go untouched for a long
   time, exactly the reported symptom. Answered directly with this proof
   before making any change, then fixed at the user's explicit follow-up
   request ("Modifie pour que ces lettres forcées soient prises en
   compte"): `_placed_letter_count` now also counts a cell as known when
   `cell in self.forced_letters`, in addition to the existing crossing-
   assignment check (a cell counted once even if it satisfies both,
   since this is a single "already known" set, not two separate criteria
   added together). Because a forced cell belongs to both its across-slot
   and its down-slot simultaneously, this fix works regardless of which
   direction tier 1 happens to draw first. Verified: an isolated
   reproduction (two independent blank 4-letter slots, one with a single
   forced cell) confirmed the seeded slot now scores `_placed_letter_count
   = 1` while the unseeded one stays at 0; a real `generate_grid()` run on
   both seeds of the standard 15×10 benchmark confirmed no regression (0
   empty white cells each, 96.9s/75.1s).

   **"Lettres forcées"/"forced letters" were renamed to "Graines"/"Seeds"
   throughout the UI** in the same request, at the user's explicit
   definition: "emplacements qui initient les premiers placements, ou les
   influences quand il y a déjà d'autres lettres" — matching exactly what
   the fix above now makes true. Only the *displayed* label changed, in
   all 5 UI languages (`frontend/static/i18n.js`'s `forceLettersLabel`:
   fr "Graines", en "Seeds", de "Saatbuchstaben", es "Semillas", it
   "Semi") — the internal Python/JS identifiers (`forced_letters`,
   `force_letters_percent`, the HTML `id="force-letters"`) stay unchanged,
   consistent with this project's own convention (English code
   identifiers, translated UI text). The field's own default moved from 0
   to 1 (both `frontend/static/index.html`'s `value="1"` and
   `GenerateRequest.force_letters_percent`'s own `default=1`, kept in sync
   the same way `black_enrichment_percent`'s UI/backend defaults already
   were), at the user's explicit follow-up request ("Initialiser
   l'interface avec 'Graines' = 1%"). Verified live: the real served page
   shows the new label and `value="1"`; a real `POST /api/generate`
   omitting `force_letters_percent` entirely logged
   `force_letters_percent=1` in `backend.log`, confirming the new default
   actually takes effect end to end.

   **A second, distinct constraint on seeds was added next**, at the
   user's explicit request: "Les graines ne doivent être placées que sur
   des emplacements réputés jouables (si possible), donc, non verrouillés
   comme injouables." `sample_letter_biases` gained an `excluded_slots`
   parameter (`None` by default, no effect for any pre-existing caller):
   when building `eligible` (the candidate cells a seed can be drawn
   from), a slot whose index is in `excluded_slots` never contributes a
   candidate — `letter_scores` is still populated for that slot's own
   cells regardless (a cell shared with a crossing, non-excluded slot
   still needs its full statistical contribution to rank that other
   slot's candidate words correctly — see `Filler._candidate_score`).
   Threaded through both call sites: `_pattern_continue` already receives
   `excluded_slots` as a parameter (impossible slots carried forward from
   the previous palier), so it's passed straight through. `_pattern_attempt`
   had no such parameter at all — its own `locked_letters`-driven
   preseed-assignment computation (which already validates each fully-
   locked slot's word against the real dictionary via
   `_slot_candidate_count`) was reordered to run *before*
   `sample_letter_biases` instead of after, collecting the fully-locked-
   but-invalid slot indices into a new `locked_impossible_slots` set passed
   in as `excluded_slots` — the exact same validation that already existed,
   just moved earlier and its by-product (which slots are impossible)
   reused for a second purpose instead of only feeding `preseed_assignment`.
   Verified: an isolated `sample_letter_biases` call (two independent
   4-letter slots, one excluded, `force_fraction=1.0` so every eligible
   cell would normally become a seed) confirmed the excluded slot never
   receives a seed while the other still does, and that `letter_scores`
   stays fully populated for the excluded slot's own cells regardless; a
   real `generate_grid()` run on both seeds of the standard 15×10
   benchmark confirmed no regression (0 empty white cells each,
   69.6s/85.1s).

   **The "continue" path (reprise telle-quelle) now also cleans blocked
   slots automatically, but never touches black cells**, at the user's
   explicit request, describing current vs. desired behavior directly:
   "à la fin d'un tour, on conserve les emplacements bloqués, et on
   continue à remplir... on nettoie les emplacements bloqués et les cases
   noires quand les grilles sont complètes ou un nombre d'itérations sans
   nettoyage atteint" (current) → "à la fin d'un tour, nettoyer
   automatiquement les emplacements bloqués, mais pas les noires... comme
   avant, on nettoie les emplacements bloqués et les cases noires quand
   les grilles sont complètes ou un nombre d'itérations sans nettoyage
   atteint" (desired) — i.e. every palier should now strip words crossing
   an impossible slot before handing off to the next one, regardless of
   which of the two paths (continue vs. full nettoyage) it takes; only the
   full-nettoyage path should still touch black cells. `_build_retry_seed`'s
   own steps 1-2 (remove words crossing an impossible slot; build
   `confirmed` from what survives) were extracted into a new standalone
   function, `_clean_blocked_slots(slots, assignment, impossible_slots,
   locked_letters=None, exclude_impossible_locked=False)` — returns
   `(cleaned_assignment, confirmed)`, a fresh list (never mutates the
   `assignment` it's given) with `None` for every removed slot.
   `_build_retry_seed` now calls it as its own first step instead of
   duplicating that logic, then proceeds with step 3 (black-cell
   reopening) exactly as before — this refactor changes nothing about the
   full-nettoyage path's own behavior. `generate_grid`'s `if
   still_has_hope:` branch (the "continue" path) now also calls
   `_clean_blocked_slots` — using `selected_slots`/`selected_diag[
   "assignment"]`/`selected_diag["impossible_slots"]`, no `locked_letters`
   (this path never has a separate one — the assignment already holds
   real per-slot words directly) — and passes the *cleaned* assignment as
   `carry_preseed_assignment` instead of the raw one; `carry_seed_grid`
   stays `selected_grid` completely untouched (no black cell ever reopened
   here), and `carry_excluded_slots` is unchanged (the impossible slots
   themselves don't become any less impossible just because their
   crossing words got removed). Verified: an isolated `_clean_blocked_
   slots` test (three slots — one independent, one crossing a declared-
   impossible slot at one shared cell, the impossible slot itself)
   confirmed the crossing slot's word is removed, the independent slot's
   word survives, `confirmed` only reflects the survivor, and the input
   `assignment` list is never mutated; a real `generate_grid()` run on
   both seeds of the standard 15×10 benchmark confirmed no regression (0
   empty white cells each, 120.0s/128.7s).

   **The tier-2 denominator itself changed from `longueur²` to
   `sqrt(longueur)`**, at the user's explicit request: "Etape 2, remplacer
   la formule : int(100 × lettres déjà remplies / longueur²) Par : int(100
   × lettres déjà remplies / sqrt(longueur))." `_backtrack`'s `scores`
   dict comprehension changed from `len(self.slots[i]) ** 2` to
   `len(self.slots[i]) ** 0.5` — the numerator (`100 *
   self._placed_letter_count(i)`) and every surrounding mechanic (the
   direction-alternation tier above it, the exact-tie-only candidate pool,
   no shuffle needed since ties are never truncated) are unchanged. A
   denominator that grows with the square root of length rather than its
   square makes a long, already well-advanced slot far more competitive
   against a short one instead of being structurally penalized just for
   being long: e.g. a 12-letter slot with 8 letters already placed used to
   score `int(100*8/144) = 5` (badly trailing a 3-letter slot with 2
   placed, `int(100*2/9) = 22`), but now scores `int(100*8/√12) = 230`,
   clearly ahead of that same 3-letter slot's `int(100*2/√3) = 115`.
   Verified: an isolated reproduction using `Filler._placed_letter_count`
   directly (a 3-cell slot with 2 cells forced vs. a 12-cell slot with 8
   cells forced) confirmed the long slot now outscores the short one
   (230 vs. 115), the reverse of the old formula's own ranking on the same
   two slots; a real `generate_grid()` run on both seeds of the standard
   15×10 benchmark confirmed no regression (0 empty white cells and 0
   mismatches each, 37.8s/57.2s).

   **The tier-2 selection was changed once more, from an exact tie on the
   single best score to a fixed window of 10**, at the user's explicit
   request: "Tirer au hasard sur les 10 emplacements ayant obtenu le
   meilleur score." `_backtrack` now shuffles `direction_pool` with the
   attempt's own seeded `rng` before sorting by score descending
   (`sorted(shuffled_pool, key=lambda i: -scores[i])[:10]`), then draws
   uniformly from that top-10 window — replacing the previous `best_score
   = max(scores.values()); candidates = [i for i in direction_pool if
   scores[i] == best_score]`. The shuffle-before-sort step is necessary
   here in a way it wasn't for the exact-tie version it replaces: `sorted`
   is stable, so without shuffling first, the slots' own original list
   order would silently decide which ones fall just inside vs. just
   outside the window whenever several are tied right at the 10th-place
   cutoff — the same positional-bias risk already documented earlier in
   this file for `_prefill_unfillable_slots`'s "solid black column"/
   "triangle" bugs, applied preventively here rather than discovered as a
   live bug. Verified: an isolated reproduction (12 slots tied at the same
   score via `forced_letters`, plus 3 slots with a clearly lower score) ran
   2000 seeded trials — the window always contained exactly 10 slots, all
   from the tied group (never one of the 3 weaker ones), and each of the 12
   tied slots appeared in a comparable number of windows (1641-1690 out of
   2000, no slot systematically favored or excluded); a real
   `generate_grid()` run on both seeds of the standard 15×10 benchmark
   confirmed no regression (0 empty white cells and 0 mismatches each).

   **The window size itself was changed from a fixed 10 to
   `int(sqrt(largeur × hauteur))`**, at the user's explicit request: "passer
   de 10 emplacements à int(sqrt(largeur*hauteur)) de la grille." A fixed
   window had no particular reason to stay the same size on a small grid
   (few slots total) and a large one (many more) — this ties the window to
   the grid's own area instead, growing on a bigger grid and shrinking on a
   smaller one (12 on the standard 15×10 benchmark, 5 on a 5×5 grid, 19 on
   a 25×15 grid). `Filler.__init__` gained two new required parameters,
   `rows`/`cols` (threaded through from `try_fill`'s own `rows`/`cols`, its
   one and only call site), used to compute `self._tier2_window_size =
   int((rows * cols) ** 0.5)` once per `Filler` instance rather than
   recomputing it on every `_backtrack` call — `rows`/`cols` never change
   during a single search, same reasoning already applied to
   `_total_white_cells`. `_backtrack`'s window slice changed from
   `[:10]` to `[:self._tier2_window_size]`; nothing else about the
   mechanic (shuffle-before-sort, the uniform draw from the resulting
   window) changed. Verified: an isolated test confirmed
   `_tier2_window_size` computes correctly for five different grid shapes
   (15×10→12, 5×5→5, 20×20→20, 9×9→9, 25×15→19); the same no-positional-
   bias reproduction as the previous window-of-10 change, re-run with a
   15×10 grid's own real window size (12, not a hardcoded 10) and one
   clearly-weaker slot among 14 tied ones, confirmed the window always
   contains exactly 12 slots, never the weaker one, and every tied slot
   appears in a comparable number of windows across 2000 seeded trials
   (1678-1726 out of 2000); a real `generate_grid()` run on both seeds of
   the standard 15×10 benchmark confirmed no regression (0 empty white
   cells and 0 mismatches each).

   **The window's basis was changed once more, from the grid's fixed area
   to how many slots are still open**, at the user's explicit request: "la
   fenêtre devient max(5,int(nombre d'emplacements libres / 10))." Unlike
   the grid-area version, this can't be computed once up front — the pool
   of still-unassigned slots in the drawn direction (`direction_pool`)
   shrinks as the search fills in slots, so `int((rows * cols) ** 0.5)`
   (fixed for the whole search) was replaced by `max(5, int(len(
   direction_pool) / 10))`, computed fresh every single `_backtrack` call
   from whatever's left to fill *right now* in that direction, floored at
   5 once few slots remain rather than shrinking to nothing. Since this no
   longer needs `rows`/`cols` at all, the `Filler.__init__`
   parameters added for the previous version (and its precomputed
   `self._tier2_window_size`) were removed again — `Filler(slots, index,
   rng, forced_letters=..., ...)` is back to its pre-grid-area-window
   signature, only the actual window-size expression inside `_backtrack`
   changed. Verified: an isolated test confirmed the formula itself for
   several pool sizes (20→5, 49→5, 50→5, 51→5, 100→10, 237→23, 300→30 —
   the floor of 5 engaging exactly below a pool of 50, matching
   `int(50/10) = 5` being the first value that already meets the floor on
   its own); a no-positional-bias reproduction (100 slots tied at the same
   score via `forced_letters`, plus 5 clearly-weaker untouched ones, in a
   105-slot pool → window size 10) over 3000 seeded trials confirmed the
   window always contains exactly 10 slots, never one of the 5 weaker
   ones, with no slot suspiciously under-represented; a real
   `generate_grid()` run on both seeds of the standard 15×10 benchmark
   confirmed no regression (0 empty white cells and 0 mismatches each:
   seed 2 in 109.5s, 55 words; seed 7 in 185.5s, 55 words).

   This 2-tier rule picks *which slot* to work on next; a separate, later step decides
   *which candidate word* to try first within that slot's own domain — at the user's
   explicit request, using `Filler.letter_scores` (a `{cell: Counter(letter ->
   occurrences)}` dict — the same statistical sampling `sample_letter_biases` already
   does for `forced_letters`, kept in full this time instead of collapsing each cell
   down to just its single winning letter — see `sample_letter_biases`'s own updated
   docstring), candidate words for the chosen slot are ranked by
   `Filler._candidate_score(i, word)` — the sum of squares of
   `letter_scores[cell][word[pos]]` over every cell of that slot that *isn't* already
   fixed by a crossing slot's own assignment (a cell already fixed needs no further
   ranking: any candidate still in the domain already matches it exactly, by
   construction — see `_domain`) — highest first, rather than the fully random order
   used before this feature existed. This ranking was originally gated behind
   `force_letters_fraction > 0` (only active when `sample_letter_biases` was itself
   only called conditionally, see above) — at the user's own explicit follow-up
   request, `_pattern_attempt` now calls `sample_letter_biases` unconditionally, so
   `letter_scores` is populated on every single pattern attempt regardless of
   `force_letters_fraction`, and this whole ranking/reordering step is active by
   default too, fully decoupled from whether any cell actually gets *forced* — the
   only remaining code path where `letter_scores` stays empty (and this step is a true
   no-op, falling back to the plain shuffle) is a direct caller of `Filler` that
   doesn't supply `letter_scores` at all (e.g. a hand-written unit test), not any
   normal `generate_grid()`/CLI/web-UI call. Squaring mirrors the same reasoning already applied
   elsewhere in this project (the sum-of-squares-of-word-lengths score used to pick
   among successful parallel pattern attempts, see `generate_grid`): it favors a
   candidate where *several* still-free cells all score well with the statistical
   consensus over one that only scores well thanks to a single standout cell. The
   candidate list is still shuffled with the attempt's own seeded `rng` immediately
   before this sort — a deliberate no-op when `letter_scores` is empty (the shuffle
   *is* the final order, unchanged from before), but when it's active, `list.sort`'s
   stability means this shuffle is what breaks ties between candidates that land on
   the exact same score, keeping that tie-break reproducible from the same seed
   rather than depending on whatever arbitrary order the domain's underlying set
   iteration happens to produce. Verified live: two isolated unit tests against a
   hand-built two-slot crossing (not the real word list) — one confirming the sum-of-
   squares computation itself against a hand-computed expectation for three candidate
   words, ranking them correctly from highest to lowest; one confirming a cell already
   fixed by a crossing slot's assignment is correctly excluded from the score (a
   candidate's score dropped by exactly that cell's own squared contribution once the
   crossing slot was marked assigned).

   Trying candidates in that ranking *strictly* (highest score first, always) was
   itself revised at the user's own explicit follow-up request, to a windowed random
   draw instead — mirroring `_place_black_cells`'s own 32-cell look-ahead window for
   black-cell placement, but for candidate words this time: after sorting, a local
   `window = 20` reordering pass repeatedly picks a *random* index among the top
   `min(window, len(remaining))` still-remaining candidates (`self.rng.randrange`),
   removes it, and repeats until none are left — so a candidate's own rank still
   matters a great deal (a candidate never gets tried before it's within the top 20
   of what's left, and the very first word tried is always within the original top
   20), but the exact order isn't fully deterministic given the same scores, avoiding
   what would otherwise be a purely score-driven, no-real-exploration search whenever
   `letter_scores` is active — which, per the decoupling above, is now every normal
   attempt, not only ones with letter-forcing enabled. Only applies inside the same
   `if self.letter_scores:` block — a no-op only for a direct `Filler` caller that
   supplies no `letter_scores` at all, not for any real `force_letters_fraction=0`
   call anymore. Verified live: a standalone reproduction of the exact reordering logic
   confirmed it always yields a permutation of the input (no candidate lost or
   duplicated); confirmed the first candidate tried is always drawn from the top-20
   scores of the *original* full list; at a realistic domain size (200 candidates,
   closer to what a real word list's slot domain can look like than a small
   hand-built test), the top-5-scored candidates averaged position ~19 (right at the
   window's own edge, as expected) while the bottom-5-scored ones averaged position
   ~188 (near the very end) across 500 trials — confirming a strong, real preference
   for high-scoring candidates survives the added randomization, not just a coin
   flip between all candidates regardless of score; a real `_pattern_attempt` batch
   (8 seeds, `force_letters_fraction=0.10`) completed without error, confirming the
   new reordering runs correctly inside a genuine backtracking search, not just in
   isolation. And
   respects a
   `deadline_checks` budget so a bad grid pattern fails fast instead of hanging. `Filler`
   takes its own seeded `rng` (see `_pattern_attempt` above — one independent RNG per
   parallel attempt, not the global `random` module or a single shared instance:
   both would be real bugs, unreproducible fills and, across either concurrent
   requests or concurrent parallel attempts within one request, a shared-state race)
   and shuffles candidate words with it. At the 15×10 default, `_domain()` is still
   called for every unassigned slot at every backtracking node (MRV's cost); a single
   failed pattern attempt at a low black-cell ratio routinely burns its *entire*
   `deadline_checks` budget (very few black cells means very long word slots, which
   are dramatically harder to satisfy than short ones). Measured live across the
   whole progression on a real 15×10 grid: ~107-140s right after black-square
   placement started this low (sequential, +0.02 ratio increment) → ~57s after
   widening the increment to +0.05 → ~44s after adding the 5-way parallel attempts
   per step — still slower than the ~15-35s typical before black-square placement
   started this low, but the gap has narrowed a lot across these three tuning
   passes. This is expected, not a hang; don't "fix" it by adding a timeout without
   addressing the actual solver cost.

   **`try_fill`'s own `deadline_checks` default was changed from a flat
   200,000 to a grid-size-proportional formula**, at the user's explicit
   request: "le budget est calculé par rapport à la taille de la grille =
   largeur * longueur * 100." A flat 200,000-check budget applied
   identically regardless of grid size — far more generous than a small
   grid could ever need, and with no guarantee of being enough for a very
   large one. `try_fill`'s own signature changed from `deadline_checks=
   200_000` to `deadline_checks=None`, with `if deadline_checks is None:
   deadline_checks = rows * cols * 100` as the very first line of its
   body — a default value can't reference another parameter directly in
   Python, and `rows`/`cols` are only known once `try_fill` is actually
   called, so this resolution has to happen inside the function rather
   than in the signature itself. On the standard 15×10 benchmark this
   resolves to 15,000 — smaller than the old flat 200,000, since a 15×10
   grid never actually needed that much budget in practice (measured
   throughout this project's history: a genuine `deadline_exceeded`
   failure is rare at this grid size once black-square placement and the
   cross-palier retry mechanism matured, see the extensive history above).
   `minimize_black_squares` (step 3, once a grid is already fully filled)
   keeps its own, separately smaller budget (`deadline_checks=6_000`,
   explicitly passed to every one of its own `try_fill` calls) —
   unaffected by this change, since it always supplies a real value,
   never `None`, so the new formula never applies to it; this was already
   a deliberate, distinct budget for a different phase of the pipeline,
   not something this change was meant to touch. Verified: an isolated
   test confirmed the formula itself for five grid shapes (15×10→15,000,
   5×5→2,500, 25×15→37,500, 9×9→8,100, 20×20→40,000); a direct call to
   `try_fill` with an explicit `deadline_checks=1` confirmed the new
   default-resolution logic never overrides an explicitly-supplied value
   (the search stopped at `checks=1`, `reason="deadline_exceeded"`, exactly
   as requested); a real `generate_grid()` run on both seeds of the
   standard 15×10 benchmark confirmed no regression (0 empty white cells
   and 0 mismatches each: seed 2 in 26.4s, 54 words; seed 7 in 32.1s, 52
   words — noticeably *faster* than every previous measurement of this
   same benchmark throughout this project's history, consistent with far
   less time wasted grinding through a 200,000-check budget on patterns
   that were never going to resolve) despite the much smaller per-attempt
   budget.

   **The multiplier itself was raised from ×100 to ×300**, at the user's
   explicit request: "Nouvelle formule budget : largeur × hauteur × 300."
   Only the constant in `try_fill`'s `deadline_checks = rows * cols * 300`
   changed — the `None`-default resolution mechanism, and
   `minimize_black_squares`'s own separate, unaffected `deadline_checks=
   6_000` budget, are untouched. On the standard 15×10 benchmark this
   raises the per-attempt budget from 15,000 to 45,000. Verified: an
   isolated arithmetic check confirmed the new formula for five grid
   shapes (15×10→45,000, 5×5→7,500, 25×15→112,500, 9×9→24,300,
   20×20→120,000); a real `generate_grid()` run on both seeds of the
   standard 15×10 benchmark confirmed no regression (0 empty white cells
   and 0 mismatches each: seed 2 in 30.2s, 55 words; seed 7 in 25.8s, 58
   words).

   **The multiplier was raised again, from ×300 to ×2000**, at the user's
   explicit request: "Nouveau budget : largeur × hauteur × 2000." Same
   minimal change as the previous two raises — only the constant in
   `deadline_checks = rows * cols * 2000` changed. On the standard 15×10
   benchmark this raises the per-attempt budget from 45,000 to 300,000 —
   1.5× the very first flat 200,000 budget this whole formula replaced.
   Verified: an isolated arithmetic check confirmed the new formula for
   five grid shapes (15×10→300,000, 5×5→50,000, 25×15→750,000,
   9×9→162,000, 20×20→800,000); a real `generate_grid()` run on both seeds
   of the standard 15×10 benchmark confirmed no regression (0 empty white
   cells and 0 mismatches each: seed 2 in 138.8s, 58 words; seed 7 in
   68.6s, 55 words — noticeably slower than the ×300 measurement above,
   consistent with a much larger per-attempt budget now being spent before
   a failing pattern gives up).

   **A web-UI "Mode" selector was added to override this formula entirely
   with a user-chosen, fixed budget**, at the user's explicit request:
   "Sur l'interface, avant 'Générer la grille' ajouter un sélecteur 'Mode'
   permettant de définir le budget (nombre de cycles par tour), avec ces
   paires de clefs/valeurs : Flash/1000 Turbo/10000 Rapide/100000
   Moyen/500000 Ultra/5000000." `deadline_checks` (`None` by default) was
   threaded as a new parameter all the way through `generate_grid` →
   `_pattern_attempt`/`_pattern_continue` → `try_fill`'s own
   `Filler`/`_backtrack` — every pre-existing caller (the CLI included)
   still gets `None`, which `try_fill` still resolves to the `rows * cols
   * 2000` formula exactly as before; the web UI is the only caller that
   now ever supplies an explicit value. `backend/app.py` gained
   `BUDGET_MODES = {"flash": 1_000, "turbo": 10_000, "fast": 100_000,
   "medium": 500_000, "ultra": 5_000_000}` and `GenerateRequest.mode`
   (default `"medium"` — the closest in order of magnitude to the
   formula's own 300,000 on the standard 15×10 benchmark), validated
   against `BUDGET_MODES` the same way `difficulty`/`language` already are
   (a 400 with the allowed set on an unknown value). Internal key names
   are English, per this project's own established convention (English
   code identifiers, translated UI text) — the display labels
   ("Flash"/"Turbo"/"Rapide"/"Moyen"/"Ultra" in French, translated per
   language in `frontend/static/i18n.js`: en "Fast"/"Medium", de
   "Schnell"/"Mittel", es "Rápido"/"Medio", it "Veloce"/"Medio" — "Flash"/
   "Turbo"/"Ultra" kept as-is in all 5, already common loanwords) are a
   separate concern from the `mode` value actually sent to the API,
   exactly like `difficulty`'s own `easy`/`medium`/`hard`. A new `<select
   id="mode">` in `frontend/static/index.html`, right before the "Générer
   la grille" button as requested, mirrors the `difficulty` select's own
   markup pattern. Verified: an isolated check confirmed `BUDGET_MODES`
   matches the requested key/value pairs exactly; real `generate_grid()`
   calls with an explicit `deadline_checks=1_000` ("flash") and `=500_000`
   ("medium") on the standard 15×10 benchmark (seed 2) both succeeded — a
   genuine surprise for "flash" specifically (0 empty white cells, 0
   mismatches in just 11.4s despite the tiny per-attempt budget, thanks to
   the cross-palier retry mechanism carrying real progress forward across
   many quick paliers rather than needing any single attempt to succeed
   outright) — confirming the explicit value really does flow through and
   take effect rather than silently falling back to the formula.

   **A related, separate request landed alongside this one**: "Dans un
   batch de N process de recherche (10 par défaut), quand une recherche
   arrive à une situation jugée 'bloquée', arrêter toutes les recherches
   du batch N, pour passer au batch N+1 sans attendre que toutes les
   recherches arrivent à une situation de blocage." Previously, a palier's
   `PARALLEL_ATTEMPTS` (10) parallel workers each ran fully independently
   — even once one worker's own `Filler.abandoned` fired (the 30% rule,
   see above), the other 9 kept searching all the way to their own
   individual abandon threshold or `deadline_checks` budget, which could
   waste a lot of time especially now that budget can be very large (up
   to 5,000,000 with "Ultra"). Implemented via a new shared
   `multiprocessing.Event`, `_worker_batch_abandoned_event` — created
   once per `generate_grid()` call (not per palier: a fresh `Event`
   submitted directly as a per-task argument would hit the exact same
   macOS "spawn" pickling `RuntimeError` already documented for
   `cancel_event`, "Condition objects should only be shared between
   processes through inheritance") and passed to every worker via the
   pool's `_init_worker` initializer, exactly like `index`/`cancel_event`.
   Unlike `cancel_event` (meaningful once for the entire generation), this
   one is `.clear()`ed by the parent at the very start of every palier
   (right where each new batch's seeds are drawn) — a blockage detected at
   palier N must never bleed into palier N+1's own fresh attempts.
   `Filler.__init__` gained a `batch_abandoned_event` parameter, threaded
   through `try_fill` (a new parameter there too). `_backtrack` gained a
   new checkpoint (same `UNFILLABLE_ABANDON_CHECK_INTERVAL` cadence as the
   existing cancel/abandon checks, right between them): if the shared
   event is already set, this worker also sets its own `self.abandoned =
   True` and returns `False` immediately — and right where a worker's
   *own* 30% rule fires, it now also calls
   `self.batch_abandoned_event.set()`, propagating the signal to every
   sibling still running. A worker that stopped this way still reports
   `reason == "abandoned_too_unfillable"` (the same bucket as the original
   30% case) — indistinguishable in `try_fill`'s own diagnostics from a
   worker that independently hit the same threshold itself, an accepted
   simplification.

   **A real, reproduced regression was found before this shipped**, not
   just reasoned about: the first version threaded `_worker_batch_
   abandoned_event` into *both* `_pattern_attempt` and `_pattern_continue`
   equally. A routine end-to-end regression sweep (both seeds of the
   standard 15×10 benchmark) caught it immediately — seed 2 still
   succeeded, but seed 7, reliable throughout this project's entire prior
   history, came back `None` (every one of the 200 paliers exhausted).
   Root cause: `_pattern_attempt`'s own `PARALLEL_ATTEMPTS` workers each
   build a *genuinely different* random pattern (`make_pattern` with each
   worker's own `rng`, on top of the same starting `seed_grid`/
   `locked_letters` but with independently-placed new black cells) —
   unlike `_pattern_continue`, whose workers all search the *exact same*
   shared pattern (only their exploration order differs, per its own
   long-standing docstring: "le motif et le verrouillage restent
   rigoureusement identiques d'une tentative à l'autre"). Sharing the
   abandon signal is sound for `_pattern_continue` (one worker's "30% of
   this shared pattern is impossible" finding really does generalize to
   its siblings) but not for `_pattern_attempt` (one worker's bad luck on
   its own, unrelated random pattern says nothing reliable about a
   sibling's differently-shaped one) — killing off an unrelated sibling
   mid-search purely because another worker's own independent pattern
   happened to be bad throws away runs that might well have gone on to
   succeed. Confirmed directly, not just reasoned about: disabling the
   signal specifically for `_pattern_attempt` (passing `batch_abandoned_
   event=None` there unconditionally, regardless of the worker-global)
   while leaving `_pattern_continue` untouched restored seed 7's success
   (96.8s, 60 words) on its own, in isolation, before the fix was even
   combined with anything else. Shipped with this scoping: `_pattern_
   attempt`'s own `try_fill` call always passes `batch_abandoned_event=
   None`; only `_pattern_continue`'s passes the real
   `_worker_batch_abandoned_event`. A side effect worth noting for the
   `_pattern_continue` case specifically: `generate_grid`'s own "force
   nettoyage when every attempt of a palier was abandoned" rule (see
   above) will now trigger far more often on a "continue" streak than
   before, since it used to require all 10 workers to *independently*
   reach 30% and now only requires one to notice, with the rest following
   within one check interval.

   Verified: three isolated tests directly against `Filler._backtrack`
   confirmed (1) a worker whose shared event is already set abandons
   itself at the very next check-interval-aligned call: `checks` bumped
   to exactly one call short of `UNFILLABLE_ABANDON_CHECK_INTERVAL`, the
   event pre-set, and the next `_backtrack` call returned `False` with
   `self.abandoned` now `True`; (2) the same check with the event left
   unset ran through without tripping that specific path; (3) a
   deliberately impossible single-slot scenario (`forced_letters` set to
   letters no dictionary word could ever match, against an empty index)
   confirmed a worker's own 30% rule firing correctly both sets its own
   `self.abandoned` *and* the shared event, proving the signal a sibling
   would actually observe gets set at the right moment. After scoping the
   fix to `_pattern_continue` only, a real `generate_grid()` run on both
   seeds of the standard 15×10 benchmark (with the Mode-selector wiring
   above also present, since both changes landed in the same file at the
   same time) confirmed no regression (0 empty white cells and 0
   mismatches each: seed 2 in 117.5s, 54 words; seed 7 in 220.7s, 57
   words).

   A genuinely serious bug was found next, reported by the user in general
   terms rather than with a specific reproduction: "les phases de
   remplissage avec verrouillage semblent laisser beaucoup d'emplacements
   non remplis, alors que ça semble facile de les remplir (particulièrement
   visible sur un tour avec de très grandes grilles déjà partiellement bien
   remplies)." Investigated with a dedicated diagnostic script (a temporary
   `on_progress` hook logging, per palier: mode, `checks`, assigned/
   unassigned/impossible counts) run against a real 22×18 grid (seed 5) —
   confirmed the report directly: after climbing steadily to 115 of 143
   slots assigned (28 unassigned, only 3 flagged impossible — 25
   apparently-open slots left), the generation got **completely stuck**:
   every single subsequent palier, for over 180 consecutive paliers in a
   row, reported the *exact same* state (`checks=1`, 115 assigned, 3
   impossible, `still_has_hope=True`) — no progress whatsoever until
   `attempts` (200) ran out and the whole generation failed outright.

   Root cause: `Filler._domain(i)` computes a slot's candidate words from
   letter constraints alone (crossing assignments / forced hints) — it
   never checks `self.used_words` (which other words are already placed
   elsewhere in the grid). On a small or sparsely-filled grid this rarely
   matters, but on a large, already densely-filled one (exactly what the
   user's report called out), a slot can end up with a domain that's
   *technically* non-empty (several real dictionary words fit the
   position constraints) yet has **zero actually available candidates**
   — every one of them is already used by another word placed elsewhere
   in that same grid. Two places treated a non-empty-but-fully-consumed
   domain as if it were perfectly fine: `_backtrack`'s own pre-selection
   domain-check loop (`if not domain: return False` — passes right
   through when `domain` is non-empty, regardless of `used_words`), and
   `impossible_zone_slots()` (used by `generate_grid`'s "still_has_hope"
   check to decide whether to keep reusing the pattern verbatim or fall
   back to cleanup — see section 1 above) — both only ever tested `_domain(i)`
   directly, never "is there at least one candidate not already used."
   This meant the one slot that was *actually* the entire blockage stayed
   permanently invisible to `impossible_slots`, so `still_has_hope` kept
   reporting `True` forever (25 slots looked "open" when, in the
   dictionary-availability sense that actually matters, one specific slot
   among them had nothing left to try) — the cross-palier retry mechanism
   had no way to ever exclude it and correctly trigger a cleanup.

   Fixed both call sites the same way: `_backtrack`'s pre-selection loop
   now fails (`return False`) when `all(w in self.used_words for w in
   domain)` — a single check that also naturally covers the pre-existing
   empty-domain case (`all()` over an empty domain is vacuously `True`).
   `impossible_zone_slots()` now computes `used_at_best = {w for w in
   self.best_assignment if w is not None}` (recomputed directly from the
   `best_assignment` snapshot being examined, not read from the live
   `self.used_words` — which, by the time a failed search finishes
   unwinding, reflects wherever `self.assignment` ended up reverting to,
   not necessarily the high-water-mark state `best_assignment` itself
   captures) and flags a slot impossible when every one of its domain
   candidates is in that set. Deliberately minimal in scope: the MRV
   domain-*size* comparison (`min_size = min(len(d) for d in
   domains.values())`) still uses the raw, unfiltered domain size
   unchanged — accounting for `used_words` there too would be a legitimate
   further refinement (a slot with 15 candidates, 14 already used, is
   really a 1-candidate slot for MRV's own purposes) but is a separate,
   secondary optimization concern, not the correctness bug actually
   reported.

   Verified live in isolation first: a hand-built 3×3 grid (two
   independent, non-crossing 3-letter slots, a 2-word dictionary) with
   slot 0 preseeded to `"CAT"` and slot 1's every cell forced (via
   `forced_letters`) to spell `"CAT"` too — its raw domain is `{"CAT"}`
   (non-empty), but `"CAT"` is already used by slot 0. Before the fix this
   would have gone entirely undetected; after the fix, `try_fill` correctly
   reports `checks=1` (an immediate, correct dead end) and
   `impossible_slots=[1]`. Then confirmed against the exact real scenario
   that first exposed the bug: the same 22×18 seed-5 grid that previously
   plateaued at 115/143 for 180+ paliers and ultimately failed now
   **succeeds at palier 28** (was: complete failure after 200 paliers),
   294.0-414.7s across two separate runs, 141 words placed, 0 mismatches
   between the placed words and the solution grid. A regression check on
   the unrelated standard 15×10 benchmark (seed 2) confirmed no behavior
   change for the common case: still succeeds (116.3s, 52 words, 0
   mismatches).

3. **Minimization** (`minimize_black_squares`): after a successful fill, greedily tries
   removing each black cell individually (independently, not by symmetric pair — kept
   consistent with `make_pattern` no longer placing cells in pairs either) and
   re-running the CSP fill; a removal is kept only if the grid is still structurally
   valid (`is_structurally_valid(grid, rows, cols, min_interior_free=1)` — not the
   default `min_interior_free=3`, since this function only ever removes cells, never
   places new ones, so the only invariant it genuinely needs to preserve is
   connectivity and the absence of an orphaned cell, not `make_pattern`'s own
   aesthetic preference for 3-cell-minimum interior zones, which is no longer a
   guarantee every grid satisfies — see `make_pattern`'s adjacency-avoidance
   fallback above) and fillable, otherwise it's reverted. This densifies the grid
   (fewer black squares) without ever downgrading from a known-good solution.

Word lists are loaded via `load_wordlist`, which expects the
`MOT<TAB>ACCENTUE<TAB>FREQUENCE<TAB>CANONIQUE` format produced by `build_wordlist_freq.py`
(falling back to a 3- or 2-column format, or a plain free-text parser, if a file doesn't
match that format — accents/canonical then just default to the bare word). It returns
`(by_length, accents, canonicals)`: `by_length` feeds the CSP solver as before, `accents`
maps each grid-usable word to its natural accented/inflected spelling (threaded through
`generate_grid()`'s `words[i]["accented"]`), `canonicals` maps it to its candidate
canonical form(s)/lemma(s) (`words[i]["canonical"]`) — both used by `backend/clues.py`.
`--difficulty` (`easy`/`medium`/`hard`) caps how many of the most frequent words are
kept — fewer words means more recognizable vocabulary but a harder-to-fill grid; `hard`
keeps the entire lexicon (100%). The cap (`DIFFICULTY_PRESETS`: easy=66%, medium=80%,
hard=100%) is a *fraction* of the lexicon, not a fixed word count — changed from a
previous fixed-count design (easy=80 000, medium=100 000) at the user's explicit
request, after noticing a fixed count doesn't have a comparable effect across
languages: French's frequency table has ~127 000 words, German's ~436 000 (German
compounds words heavily, inflating its vocabulary size) — the same absolute 80 000
cap would keep ~63% of French's words but only ~18% of German's, making "easy"
considerably harder in German than in French without that being the intent.
`load_wordlist()` resolves the fraction to an absolute count itself, once it knows
the *actual* size of the candidate pool for that call (`max_words` accepts either an
`int`, an absolute count — the historical behavior, still what `--max-words` passes
— or a `float` between 0 and 1, a fraction, resolved via `round(len(ranked) *
max_words)`, dispatched on the value's Python type). This resolution happens *after*
"easy"'s own `require_gloss` filtering (see below) has already dropped whatever
words have no findable definition — so easy's 66% is 66% of the gloss-filtered pool,
not 66% of the raw frequency table; verified live this really does produce a
comparable, non-degenerate result across languages with very different gloss
coverage rates (French: 66% cap ≈ 65% of its ~127k raw table, since French's gloss
coverage is high; German: 66% cap ≈ 28% of its ~436k raw table, since a much smaller
fraction of German's compound-heavy vocabulary has Wiktionary coverage in the first
place — a real, expected consequence of resolving the fraction post-filter, not a
bug). The cap (whichever type it resolves to) is applied *globally* (ranked across all
lengths together), not per
length — a per-length cap doesn't actually restrict short lengths that have fewer total
words than the cap itself (a real bug: French has only ~700 3-letter words, so an
earlier 600-per-length "easy" cap let through every one of them, including obscure
entries like `ABD`, score 103, ~33 000th globally). "Easy" specifically also requires a
findable definition: `load_wordlist(..., require_gloss=True)` drops any word with no
gloss-dictionary entry (`backend/gloss_lookup.py`'s `has_any_gloss`) under either its
inflected spelling or any candidate canonical form — frequency rank alone doesn't catch
a word that's merely common yet undefinable (verified: 106 additional words dropped
from French `easy` this way, mostly foreign proper nouns that had passed Hunspell
validation as loanword-like tokens). Falls back to no-op (silently skips the
gloss-filter) if the wordlist's language can't be inferred from its filename or it has
no gloss dictionary built (`backend/gloss_lookup.py`'s `has_gloss_dictionary`) —
`require_gloss` never breaks a caller that doesn't have one. This guard is load-
bearing, not a redundant safety net: `has_any_gloss` itself returns `False` for a word
with no dictionary entry *and* for a word in a language with no dictionary built at
all (both look identical from inside `has_any_gloss`) — without checking
`has_gloss_dictionary` separately first, a deployed instance missing
`data/gloss_dictionary/<lang>_glosses.jsonl` would have every single word rejected
instead of the filter no-op'ing, since `require_gloss=True` is the default
`easy`-difficulty behavior. Caught live: a deployed instance with a wordlist file but
no gloss dictionary built produced `wordlist_loaded {'word_count': 0, ...}` in
`backend.log` and "no fillable grid found" on every request.

`build_wordlist_freq.py` counts word occurrences directly from a language's reference
corpus (`data/reference_corpus/<lang>_sentences.txt`, `_count_word_frequencies`) —
strips accents/diacritics and uppercases (crossword convention), excludes words under 2
letters after normalization (raised from a previous under-3 exclusion, at the user's
explicit request — 2-letter grid slots are now real, cluable words, see
`backend/crossword_gen.py`'s `is_structurally_valid` discussion below; a bare 1-letter
word is still excluded, since a 1-letter grid zone never becomes a slot at all), and
keeps the occurrence count as the frequency. All five
languages (fr/en/de/es/it) go through this same script. This pipeline used to read the
third-party HermitDave FrequencyWords lists as a persisted `data/raw/<lang>_50k.txt`
(and before that, French alone used Lexique383 via a since-removed `build_wordlist.py`)
— switched to a self-built corpus (`build_sentence_corpus.py`) so every step from raw
corpus to frequency number is something this project controls, after a concrete
quality problem (French `ARE`'s frequency count included English-contamination
occurrences under HermitDave) couldn't be fixed without leaving HermitDave's own
processing behind; `data/raw/` and the intermediate script that wrote it
(`compute_word_frequencies.py`) were later retired too, once persisting that count
between two scripts no longer served a purpose. Every candidate (all five languages) is additionally checked against
a Hunspell dictionary for that language (`HUNSPELL_SOURCE`, fetched from
LibreOffice/dictionaries and cached in `data/hunspell_cache/`, gitignored) using the
`hunspell` CLI's own spellchecker (`_spellcheck_valid`) — both the word's corpus
spelling and a title-cased variant, since some languages (German) require every noun
capitalized while the corpus lowercases everything; whichever form actually validated
becomes the `ACCENTED` value, not necessarily the corpus's original casing. A first
attempt used `unmunch` to pre-expand each Hunspell dictionary into a static wordlist and
tested membership against that — rejected after finding it silently drops many irregular
verb conjugations (French être/avoir/vouloir) that the real `hunspell` spellchecker
correctly recognizes, almost certainly an affix-flag-chaining limitation in `unmunch`'s
naive enumeration rather than in the dictionaries themselves. Requires the `hunspell`
package (`brew install hunspell` / `apt-get install hunspell`) only when (re)building
wordlists — not a runtime dependency of the app itself.

The `FREQUENCY` column written to `data/wordlist_<lang>_full.tsv` (the value
`crossword_gen.load_wordlist()` sorts on and caps per `--difficulty`) is not the raw
source count as-is: dialogue-heavy corpora underrepresent some inflected forms that are
nevertheless perfectly easy, common words (e.g. French "déterminées" is rarer in
dialogue than its infinitive "déterminer", despite being an equally easy word).
`_stem_map` runs `hunspell -m` (morphological analysis) on every kept word to find its
candidate canonical form(s) — when a word is genuinely ambiguous between stems (French
"suis" parses as either "être" or "suivre"), whichever candidate is itself most
frequent in this same wordlist is used for the frequency blend below, but **every**
candidate is kept in the `CANONICAL` column (semicolon-separated) for gloss lookup —
see `backend/gloss_lookup.py`; committing to a single "most likely" candidate would be
the wrong layer to resolve a genuine ambiguity the LLM should resolve using the word's
actual clue-writing context. The written frequency score is `CANONICAL_WEIGHT` (0.9)
times the (most-frequent candidate) canonical form's own frequency plus 0.1 times the
word's own raw frequency — enough to correct the corpus-frequency distortion while
still ranking different inflected forms of the same stem against each other (rather
than collapsing them all to one identical score). This is a two-edged blend, not a
one-way boost: it can also pull an already very-common conjugated form's score *down*
when its own raw frequency happens to exceed its infinitive's (verified: French
"suis"/"était" both dropped somewhat, since "être"'s own raw count is lower than
either) — an accepted, symmetric consequence of weighting toward the canonical form,
not a bug.
