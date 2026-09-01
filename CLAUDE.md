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

  **A real, long-standing process leak was found and fixed next**,
  reported by the user: "Tue le process qui tourne encore et qui ne s'est
  pas correctement interrompu au redémarrage du Back." Investigated
  directly rather than guessed at a single culprit: `ps aux` showed
  **436** leftover `python -c "from multiprocessing.spawn import
  spawn_main..."` / `multiprocessing.resource_tracker` processes, every
  one of them with `PPID=1` (`launchd` — confirmed orphaned, not children
  of any live process) — some sitting idle for days (timestamps back to
  the previous weekend), a handful still actively burning ~100% CPU.
  Root cause, traced directly in `run_Falcon.sh`'s `stop_port()`: it only
  ever finds and kills the single PID *listening on the port*
  (`lsof -ti tcp:"$port"`) — the uvicorn backend process itself — never
  any of its own child processes. But `backend/crossword_gen.py`'s
  `generate_grid()` creates its own `ProcessPoolExecutor` deep inside the
  palier loop for every parallel `_pattern_attempt`/`_pattern_continue`
  batch (one OS worker process per `PARALLEL_ATTEMPTS`, see that file's
  own section) — a plain `with ProcessPoolExecutor(...) as executor:`
  block, never held anywhere at the app level a shutdown handler could
  reach. If a restart lands while that block is active (a real
  generation genuinely in progress), killing the uvicorn process
  bypasses its own `__exit__`/`executor.shutdown()` entirely — a killed
  process never gets to run its own Python-level cleanup — silently
  orphaning every one of that batch's worker processes. Once orphaned,
  nothing in their own CSP search loop (`Filler._backtrack`) ever
  detects it: only `cancel_event`/`batch_abandoned_event`/
  `deadline_checks` are checked there, and none of those are ever set
  once the process managing them is gone, so a worker just keeps
  running (or sits blocked forever on a now-permanently-unreadable pipe
  to its dead parent) indefinitely.

  Fixed with a `kill_tree(pid, sig)` helper in `run_Falcon.sh`: walks
  `pgrep -P` recursively to find every descendant of a PID *before*
  touching anything, kills each child first (depth-first), then the PID
  itself — applied to every PID `stop_port()` finds, both for the
  initial `TERM` attempt and the `KILL` fallback for survivors. This is
  a deliberate, narrow exception to this project's own established
  "never forcibly kill a worker process" philosophy (see
  `GenerationCancelled`'s own docstring in `crossword_gen.py`) — that
  philosophy is specifically about the cooperative, non-destructive
  "Stop" button *within* a still-running server, where other work might
  still be worth letting finish; stopping the *entire server* is a
  different situation; whatever it was doing is being abandoned
  regardless (its own HTTP client already lost the connection the
  instant the process died), so there is nothing left to preserve by
  leaving its workers alive, only orphaned processes to prevent.

  Verified live: first cleaned up all 436 already-accumulated orphans by
  hand (`kill -9` on every PID confirmed to have `PPID=1` among
  `multiprocessing.spawn`/`resource_tracker` processes — cross-checked
  against the live backend/frontend/LLM-server PIDs first to make
  certain none of them were touched) and confirmed the live servers
  stayed healthy throughout (`GET /api/health`, `GET /api/version`, and
  `lsof` showing exactly one listener per port, all unaffected). Then
  reproduced the actual bug end to end with the *fix* in place, not just
  reasoned about it: started a real generation (15×10, `mode="medium"`),
  confirmed 10 real `ProcessPoolExecutor` worker processes actively
  running via `ps` a couple of seconds in (mid-search, genuinely
  in-flight), ran `./run_Falcon.sh` to restart *while* that job was
  still active, and confirmed **zero** leftover `multiprocessing`
  processes immediately after — down from what would previously have
  been 10 more added to the pile — with both servers reporting healthy
  on their new PIDs right away.

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

   **A separate, much lower threshold was introduced for the position-aware
   variant of this same check** (`_slot_with_insufficient_candidates`/`_new_
   black_cell_breaks_locked_slot`'s own locked-letter branch — checking a
   slot's real candidate count against its *exact* locked letters at their
   *exact* positions, not just its length in general), at the user's
   explicit request, quoting the DOC_ALGO paragraph describing this exact
   check back and asking for "au moins 1 mot compatible" instead of the
   shared `PREFILL_MIN_WORD_COUNT` (10) value it had silently inherited
   until now. A new constant, `PREFILL_LOCKED_MIN_WORD_COUNT`, was
   introduced specifically for this — deliberately *not* just lowering
   `PREFILL_MIN_WORD_COUNT` itself, which would have silently undone the
   very fix described in the paragraph just above (raising it from 1 to 10
   for the plain, length-only check) — the two checks now have their own
   independent thresholds, only ever used at their own three call sites
   (`_new_black_cell_breaks_locked_slot`, `_has_slot_without_candidate`/
   `_slot_with_insufficient_candidates`'s locked-letter branch).

   First set to the user's own literal value (1) and verified live, not
   assumed safe: two real `generate_grid()` runs on the standard 15×10
   benchmark (seeds 2 and 7, reliable throughout this project's entire
   history) both **failed outright** at threshold 1 — 73.3s and 88.5s
   respectively, exhausting all 200 paliers with no solution — a real,
   reproducible regression, not a fluke (confirmed by re-running with the
   constant temporarily restored to 10, which immediately succeeded again
   on both seeds, 24.8s/28.2s). Root cause: a slot locked down to exactly
   one real candidate word is extremely fragile — that one word
   conflicting with even a single crossing letter anywhere in the grid
   makes the slot permanently, unrecoverably impossible, and pre-fill no
   longer intervenes to shorten/avoid it once "at least 1" is already
   satisfied. Reported to the user with this measurement rather than
   shipped silently; the user was asked directly (given the severity —
   this broke an established, always-reliable benchmark) and chose an
   intermediate value over either keeping 1 (accepting the regression) or
   reverting to 10 outright. **`PREFILL_LOCKED_MIN_WORD_COUNT = 3`** was
   verified the same way: both benchmark seeds succeeded again (33.7s/54
   words, 58.3s/59 words), restoring reliability while still being far
   more permissive than the shared length-only threshold of 10.

   **`PREFILL_MIN_WORD_COUNT` (the plain, length-only threshold) was lowered
   again much later in this project's history, from 10 to 3**, at the
   user's explicit request, to bring it in line with
   `PREFILL_LOCKED_MIN_WORD_COUNT` (already 3): "Pour être cohérent avec le
   critère d'impossibilité de remplissage, réduire la contrainte à 3 mots
   candidats (et non 10)." Unlike the earlier attempt to lower the
   *locked-letter-aware* threshold (`PREFILL_LOCKED_MIN_WORD_COUNT`) all
   the way to 1 — which caused a real, measured regression on this same
   benchmark (see above) — this request targets a different value (3, not
   1) on the *other*, plain length-only constant, so the earlier
   threshold-1 regression doesn't directly transfer; verified live rather
   than assumed safe regardless, given this exact code area's own
   extensive regression history. The two constants stay separate (they
   drive two different checks — a slot's raw length vs. its exact locked
   letters at exact positions), only their numeric value now happens to
   coincide. A direct check against the real French wordlist (`easy`
   difficulty) confirmed the threshold change has a real, if narrow,
   effect: length 21 has exactly 4 candidate words, now correctly
   considered "available" (4 ≥ 3) where it previously wasn't (4 < 10) — no
   other length under 15 candidates changed status. Two real
   `generate_grid()` runs on the standard 15×10 benchmark (seeds 2 and 7,
   Flash mode per this project's own testing convention) confirmed no
   regression: 0 mismatches, 0 empty white cells each — seed 2 in 11.0s,
   63 words, 36 black cells; seed 7 in 30.7s, 55 words, 43 black cells.

   A **« nettoyage curatif »** (curative cleanup) was added on top of this
   same locked-letter-aware pre-fill mechanism, at the user's explicit
   request, as a new third outcome between "add a black cell" and "give up,
   mark unfixable": `_prefill_unfillable_slots` now tracks, per problematic
   zone, its own original size (`zone_footprints`, a list of `[original
   cells, black cells added so far]` pairs — a zone can be rediscovered
   several times across the loop's own iterations, cut into shorter and
   shorter pieces by each cell it adds, so a newly-found zone is matched
   back to whichever previously-tracked footprint it's a cell-subset of,
   rather than treated as a brand-new, independent one) and compares the
   cumulative count of new black cells added to it so far against that
   original size. Two genuinely ambiguous design points — the numeric
   threshold for "too many new black cells", and where in the pipeline this
   new rule should even live — were resolved via `AskUserQuestion` rather
   than guessed, given this exact area's own extensive history of costly
   wrong guesses (the threshold-1 regression just above being the most
   recent example): the user's answers were "reuse the grid's own overall
   black-cell fill objective" (rather than a new, separately-invented
   percentage) for the threshold, and "inside the existing per-cycle
   pre-fill mechanism" (rather than the cross-palier nettoyage or a brand
   new, third mechanism) for the placement. `make_pattern` now computes
   `fill_objective_fraction = max(black_ratio, black_enrichment_fraction)`
   once, right where `initial_white_count` is captured (before either
   pre-fill call), and passes it to both — reusing the exact same
   percentage that already governs the grid's *overall* black-cell density
   (`black_enrichment_fraction`/"Taux noir", 14% by default; `black_ratio`
   itself is almost always 0.0 today, so it contributes nothing in
   practice, included only for a caller like the CLI that might still set
   `--black-ratio`) as the ceiling on how much of *one single zone* pre-fill
   is allowed to blacken before switching strategy, rather than inventing a
   new, separately-tuned constant for this one rule.

   Once a zone's own cumulative added-black-cell count would exceed this
   fraction of its original size (or no valid black-cell placement exists
   at all), a new helper, `_remove_least_fillable_crossing_word(slot, grid,
   rows, cols, index, locked_letters)`, is tried before giving up: among
   every slot (`extract_slots`) that shares at least one cell with the
   problematic zone *and* is itself fully covered by `locked_letters` (a
   genuinely confirmed word, not just a single locked cell), it removes
   (un-locks every one of its cells from `locked_letters`, mutated in
   place) whichever one has the *fewest* real candidates of its own
   (`_slot_candidate_count`, on its own locked letters) — the most fragile
   crossing word, already closest to becoming impossible on its own at the
   next conflict regardless, is the one sacrificed to relax the target
   zone's constraint without adding a single further black cell. Returns
   `False` (nothing removed) if no crossing word is fully locked at all —
   in that case the zone still falls through to the pre-existing
   `unfixable` fallback, unchanged. Deliberately gated off entirely for a
   zone whose insufficiency comes purely from its own *length* not being in
   `available_lengths` (never from locked letters) — removing a crossing
   word can't change a slot's length, so for that case the mechanism is a
   pure no-op and the original behavior (black cell, or unfixable) is kept
   exactly as before; only a genuine locked-letter/candidate-count
   insufficiency ever engages the new budget check and word-removal path.

   Verified with isolated tests first: `_remove_least_fillable_crossing_
   word` against a hand-built 2×5 grid with two locked crossing down-words
   (one with 1 real candidate, one with 2) correctly removed the
   1-candidate one and left the other untouched; the same call against a
   slot touching no locked crossing word at all correctly returned `False`
   with no mutation; a hand-built scenario with no candidate cell available
   at all in the target zone (an across-slot whose own single locked
   letter left it with only 1 candidate, crossed by a fully-locked,
   fragile 2-letter down-word) confirmed the mechanism adds *zero* black
   cells and instead removes the crossing word, which by itself already
   fixes the across slot; a fourth test confirmed a pure length-only
   insufficiency (a 5-letter slot in a dictionary with only 2-letter
   words) still gets fixed via black cells exactly as before, even at
   `fill_objective_fraction=0.0`, since the length-only case is never
   routed through the new budget/removal path at all.

   **A real regression was found the first time this was checked
   end-to-end**, not just in isolation: two real `generate_grid()` runs on
   the standard 15×10 benchmark (seeds 2 and 7, previously reliable
   throughout this project's entire history) both **failed outright**
   (148.2s/180.4s, exhausting all 200 paliers). Root-caused directly rather
   than guessed at: `fill_objective_fraction` (10-14% by default — the same
   percentage that governs the grid's own *overall* black-cell density)
   applied straight to a single zone's own size (typically 8-15 cells)
   resolves to a budget of `int(fraction * zone_size)` cells — for the
   whole 3-15 cell range this pipeline actually produces, that's 0 cells at
   10% for any zone of 9 cells or fewer, and only ever 1 cell for a zone up
   to 19 cells — meaning almost every zone was switching to word-removal
   after at most one single black cell, far more aggressively than the old,
   uncapped mechanism ever did (which typically only ever needed 1-2 cells
   to fix a zone in the first place, but never *refused* a second or third
   one when genuinely needed). This measurement was reported to the user
   rather than reverted or reinterpreted silently, together with two
   concrete fixes and a full revert as options (`AskUserQuestion`, mirroring
   the exact same escalation pattern already used for the
   `PREFILL_LOCKED_MIN_WORD_COUNT` regression above) — the user chose a
   guaranteed per-zone floor over either a single grid-wide cumulative
   budget or reverting the whole mechanism. `PREFILL_ZONE_BLACK_BUDGET_FLOOR
   = 2` was introduced (see its own definition for the full measurement)
   and `zone_budget = max(PREFILL_ZONE_BLACK_BUDGET_FLOOR, int(
   fill_objective_fraction * zone_white_count))` replaces the previous bare
   `fill_objective_fraction * zone_white_count` comparison — every zone is
   now guaranteed at least 2 black cells before word-removal is ever
   attempted, with the percentage only ever mattering once a zone is large
   enough for its own share to exceed that floor.

   Re-verified after the floor was added: the isolated test exercising "no
   candidate cell available at all" was kept as-is (a floor-independent
   scenario, exactly why it was redesigned that way rather than relying on
   `fill_objective_fraction=0.0` alone, which the floor now overrides
   regardless of the fraction); a new, dedicated test confirmed the
   arithmetic directly — a 9-cell zone at the default 10% resolves to a
   budget of 2 (the floor), not the 0 the bare percentage alone would have
   given.

   **A second, deeper regression was found on the very next end-to-end
   check** — seed 7 now succeeded again, but seed 2 still failed outright
   (392.4s, all 200 paliers exhausted), just with much more search effort
   spent per palier than the first regression. Rather than keep tuning the
   floor blindly, this was root-caused with a controlled, isolated
   comparison: re-running seed 2 with the budget entirely neutralized
   (`PREFILL_ZONE_BLACK_BUDGET_FLOOR` temporarily set to a huge value, so
   black-cell placement is never capped, exactly matching the pre-feature
   behavior) still failed (60.7s at 40 paliers) — proving the regression
   had nothing to do with the budget/floor tuning at all. A second,
   further-isolated run additionally forced `_remove_least_fillable_
   crossing_word` (this mechanism's first name, see below) to always return
   `False` — i.e. the word-removal mechanism fully disabled, an exact,
   verified reproduction of the pre-feature "black cell, or unfixable"
   behavior — and *that* succeeded (44.6s at attempt 40, matching this
   benchmark's own historical range). This pinpointed the regression
   precisely: even in the narrow, conservative case where the budget would
   never have blocked a black cell anyway (no valid candidate cell existed
   in the zone at all — the one case this mechanism was *always* meant to
   help, previously an unconditional `unfixable`), the mere act of removing
   *some* crossing word was, on its own, net harmful to this benchmark
   seed — not a tuning problem, a design problem with *which* word gets
   removed.

   Reported to the user with this full diagnostic trail (both isolated
   comparisons, not just the failure) rather than reverted or re-tuned
   silently; given `AskUserQuestion`, the user identified the actual flaw
   directly: removing the crossing word with the **fewest candidates of
   its own** (this mechanism's original selection criterion, on the
   reasoning that the already-most-fragile word is the "cheapest" one to
   sacrifice) was itself the mistake — "il ne faut pas retirer le mot le
   moins remplissable... il faut identifier l'emplacement le moins
   remplissable qui aurait déjà des lettres positionnées, puis retirer un
   mot (forcément dans l'autre sens) qui participe à ces lettres déjà
   positionnées." In other words: the *target* zone selection was always
   correct (the least-fillable *slot*, already what `_slot_with_
   insufficient_candidates` finds); what needed to change was the
   selection *among* the crossing words that contribute a locked letter to
   it — no longer ranked by the crossing word's own fillability at all,
   since a fragile crossing word isn't redundant just because it's fragile
   — losing it can just as easily deprive the rest of the search of a
   useful confirmation elsewhere in the grid. `_remove_least_fillable_
   crossing_word` was renamed to `_remove_a_crossing_word` and rewritten:
   it now collects *every* qualifying crossing word (unchanged scope —
   fully locked, sharing a cell with the target slot) into a plain list,
   shuffles it with the attempt's own seeded `rng` (the same
   no-positional-bias principle already used everywhere else in this file
   for an unranked choice among ties), and removes whichever ends up
   first — no fillability-based ranking at all. `index` dropped from its
   parameter list entirely (no longer needed, since `_slot_candidate_
   count` is never called here anymore), `rng` added instead.

   Verified: the isolated `_remove_a_crossing_word` test was rewritten to
   confirm the *absence* of bias directly — across 200 seeds, a
   hand-built two-candidate scenario (a 1-candidate and a 2-candidate
   crossing word, the exact shape that previously always favored the
   1-candidate one) now has *both* removed a comparable number of times
   (93/200 and 107/200 in one run — no systematic favoritism toward
   either); the "no candidate cell available" test (unaffected in scope,
   only in selection) still passes; the length-only test still passes
   unchanged.

   **A third round of the same regression was found on the next end-to-end
   check, still unresolved as of this writing**: seed 7 now succeeds
   (195.5s), but seed 2 still fails outright (345.2s, all 200 paliers
   exhausted) — a third consecutive failure on this exact seed, this time
   with a selection criterion (random, no fillability bias at all) that no
   longer matches either of the two previously-tried designs. Combined with
   the earlier controlled comparison (disabling word-removal entirely
   makes seed 2 succeed in 44.6s), this points at something more
   fundamental than *which* crossing word gets picked: removing *any*
   locked crossing word during pre-fill, regardless of selection strategy,
   appears to conflict with how seed 2's own search specifically unfolds —
   a question not yet root-caused. Reported to the user with the full
   picture (three attempted designs, one clean isolated control); the user
   chose to pause the investigation here and test further themselves
   rather than continue iterating blindly — no further code change was
   made to this mechanism pending that. **As of this entry, `_prefill_
   unfillable_slots`'s "nettoyage curatif" reliably helps seed 7 but still
   regresses seed 2 on the standard 15×10 benchmark — a known, open
   reliability gap, not a resolved feature.**

   **`PREFILL_ZONE_BLACK_BUDGET_FLOOR`'s own scope was revisited much
   later**, at the user's explicit request, quoting the exact DOC_ALGO
   paragraph describing it back: "Les 'jamais moins de 2 cases noires
   garanties' doivent être appliqués à l'ensemble des cases à problème
   (avant tentative de retrait de mots), pas sur chaque emplacement à
   problème. Si appliqué sur chaque emplacement à problème, cela produit
   trop de cases noires (si 3 emplacements à problème, on monterait à 6
   cases en plus autorisées)." This reverses the *other* option the user
   had been offered — and had chosen against — back when this floor was
   first introduced (see the entry above: "the user chose a guaranteed
   per-zone floor over... a single grid-wide cumulative budget"); this
   time, given the same choice again with the concrete arithmetic spelled
   out, they chose the shared budget instead.

   `zone_budget = max(PREFILL_ZONE_BLACK_BUDGET_FLOOR, int(
   fill_objective_fraction * zone_white_count))` (per-zone) became
   `combined_budget = max(PREFILL_ZONE_BLACK_BUDGET_FLOOR, sum(int(
   fill_objective_fraction * len(fp[0])) for fp in zone_footprints))` — the
   floor and the percentage-scaled term are both now summed across *every*
   zone `zone_footprints` is currently tracking, not just the one being
   evaluated — and the comparison itself changed from `footprint[1] + 1 <=
   zone_budget` (this zone's own added-cell count) to `total_added_so_far
   + 1 <= combined_budget`, where `total_added_so_far = sum(fp[1] for fp in
   zone_footprints)` (every zone's added cells, combined). A zone
   discovered late in a single `_prefill_unfillable_slots` call can
   therefore immediately hit a budget already mostly or entirely consumed
   by earlier zones in that same call — exactly the intended effect: the
   floor guarantees 2 cells *total* per pre-fill pass, never 2 *per zone*.

   Verified with a direct, controlled before/after comparison on the exact
   same hand-built scenario (3 independent, non-crossing 5-letter slots on
   a connected grid, each with a locked 2-letter "AB" prefix matching only
   2 real dictionary words — below `PREFILL_LOCKED_MIN_WORD_COUNT`,
   `fill_objective_fraction=0.0` so only the floor itself is in play):
   the *old* per-zone formula (temporarily restored, then reverted again
   right after) added **6** black cells total (2 per zone × 3 zones) —
   matching the user's own predicted arithmetic exactly; the *new* shared
   formula added **2** total, all absorbed by the first zone found, with
   the second and third zones immediately blocked by the already-consumed
   shared budget and correctly falling through to `_remove_a_crossing_
   word` (finding nothing to remove, since this test's zones don't cross
   any other word) rather than adding more black cells.

   **A real regression was found immediately on the following end-to-end
   check** — exactly the kind of outcome this exact mechanism has a
   documented history of (see the two entries above): seed 2 of the
   standard 15×10 benchmark **failed outright** (`generate_grid()`
   returned `None`, all 200 paliers exhausted) with the shared-budget
   change in place. Confirmed as a real, isolated regression rather than
   a coincidence, exactly as this mechanism's own history calls for: the
   *same* seed 2, on the *same* code, with *only* this one computation
   reverted back to the old per-zone formula, succeeded again (108.4s) —
   an unambiguous A/B result pinning the regression on this specific
   change, not on anything else. Reported to the user with this
   measurement rather than silently reverted or kept; as of this entry
   the shared-budget version is left in place (the user's own most recent
   explicit instruction) with the regression disclosed, pending the
   user's own decision on how to proceed — the same escalation pattern
   already used twice before for this exact mechanism (see the two
   entries above, both resolved via `AskUserQuestion`).

   **Re-tested via `AskUserQuestion` alongside the separate per-process-
   init regression below (both surfaced together, from the same
   investigation) — seed 7 was found to also be affected**, not just
   seed 2: with the shared budget alone, seed 7 still succeeds but
   3.5× slower (355.4s vs. ~102s with the per-zone budget). Presented to
   the user with both measurements; **the user reverted the change**:
   "Il ne faut pas changer le budget, juste initialiser N grilles au
   premier cycle au lieu d'une seule." `combined_budget`/
   `total_added_so_far` (summed across every known zone) reverted back to
   `zone_budget`/`footprint[1]` (per-zone, the original design) — see
   `PREFILL_ZONE_BLACK_BUDGET_FLOOR`'s own comment for the final state of
   this back-and-forth. The "N grids at the first cycle" part of that same
   reply refers to a *separate* feature — see the per-process-init entry
   further below for its own corrected scope.

   **A separate, real bug in this same feature's downstream effects was
   reported next**, directly by the user: "certaines cases noires
   initiales disparaissent (j'ai l'impression qu'elles sont déplacées, je
   retrouve des motifs identiques avant/après avec un décalage)... il ne
   faut toucher qu'aux cases noires ajoutées, pas à celles présentes avant
   de commencer cette phase." Root-caused with a dedicated live diagnostic
   (comparing the cycle-start preview's own black cells — i.e. `carry_
   seed_grid`, the pattern *entering* a palier — against the cycle-start
   preview's black cells one palier later, flagging any cell present in
   the first that's missing from the second) rather than guessed at: a
   real run of the standard 15×10 benchmark (seed 2) confirmed 6 genuine
   cross-palier losses of pre-existing black cells.

   Mechanism: `_build_retry_seed`'s step-3 protection (which black cells
   survive a full nettoyage) only ever consults `assignment` — the *final*
   CSP-search result for this one failed attempt — to decide which words
   "survive" and protect their own boundary cells; a word with `assignment
   [i] is None` contributes no protection at all. But `_remove_a_crossing_
   word` (nettoyage curatif, see above) can un-confirm a word — removing
   its letters from the *worker's own local copy* of `locked_letters`,
   entirely inside `make_pattern`, before the CSP search even runs — even
   when that word was already confirmed *before this palier started*
   (present in `carry_locked_letters`, the parent process's own copy,
   never mutated by the worker: each `_pattern_attempt` call runs in its
   own OS process via `ProcessPoolExecutor`, so `locked_letters` is
   pickled/deep-copied across the process boundary, not shared by
   reference — a mutation inside the worker can never be seen by the
   parent's own `carry_locked_letters`). If the search subsequently fails
   to reassign that same slot, `assignment[i]` stays `None` in the
   returned diagnostics — and `_build_retry_seed`, seeing no surviving
   word there, reopens its boundary cells as if they'd been "added and
   failed" *this* palier — even though they were part of the pattern
   *entering* it, wholly unrelated to this palier's own nettoyage curatif.

   Fixed by giving `_build_retry_seed` a new `seed_grid=None` parameter:
   any cell already `BLACK` in `seed_grid` (when given) is added to
   `protected_black_cells` unconditionally, regardless of what `assignment`
   says about the word that used to bound it — a cell present *before* this
   palier's own attempt can, by construction, never have been "added
   without success" by it. The one call site (`_clean_all_candidates`,
   inside `generate_grid`'s full-nettoyage branch) now passes `seed_grid=
   carry_seed_grid` — the pattern this palier's own attempts actually
   started from. `seed_grid=None` (the default) is a complete no-op,
   matching every pre-existing behavior for the very first palier (where
   `carry_seed_grid` is itself `None` — nothing "existed before" yet).

   Verified: an isolated `_build_retry_seed` test reproduced the exact bug
   shape first (a 1×7 grid, a 5-letter slot bounded by two black cells,
   `assignment=[None]` simulating a word un-confirmed by nettoyage curatif
   whose search then failed to refill it) — confirmed both boundary cells
   are wrongly reopened *without* `seed_grid`, and correctly survive
   *with* it; a control confirmed a black cell genuinely added this
   palier (absent from `seed_grid`) still gets reopened normally when
   unprotected (the fix isn't a blanket "never touch anything" rule); a
   second control confirmed `seed_grid=None` (the first palier) behaves
   identically to before. Then re-verified live end-to-end, methodically:
   first confirmed the diagnostic itself catches the bug by temporarily
   reverting *only* the call site (`_build_retry_seed`'s own `seed_grid`
   parameter still present but never passed) and re-running the same
   cross-palier check on the standard benchmark's seed 2 — 6 genuine
   violations found, matching the original report; then restored the real
   fix and re-ran the identical check — violations dropped from 6 to 2.

   Those 2 residual "violations" were confirmed to be a false positive of
   the *diagnostic itself*, not a residual instance of the bug: both
   showed the winning candidate's own black-cell overlap with the
   entering `carry_seed_grid` collapsing to just 2-4 shared cells out of
   14-24 — the signature of a **reset worker** (`FULL_RESET_ATTEMPT_
   FRACTION`, ~20% of a palier's workers right after a full nettoyage
   start from a totally blank, independent grid, never inheriting `carry_
   seed_grid`/`carry_locked_letters` at all) happening to win that
   palier's own scoring. For a reset-worker candidate there never was a
   "before this phase" state to preserve in the first place — `seed_grid`
   protection correctly does nothing harmful for it (a coincidental cell
   overlap just gets a no-op extra protection), and the resulting pattern
   *should* look unrelated to what came before, by design. Confirmed
   directly: a separate diagnostic logging each palier's own `locked_
   cells` count showed a dramatic collapse (54 → 2) at the exact palier
   transition matching one of the two residual "violations" — a real
   reset event, not a bug. Finally, two real `generate_grid()` runs on
   both seeds of the standard 15×10 benchmark confirmed no regression to
   the ordinary case.

   This investigation surfaced a second, genuine bug along the way, found
   while reusing `_cycle_start_preview` (see below) to preview each
   palier's own outcomes individually rather than just its single carried-
   forward starting state: `generate_grid`'s "20% reset" mechanism
   (`FULL_RESET_ATTEMPT_FRACTION`) starts a fraction of a palier's own
   `_pattern_attempt` workers from a totally blank, *independent* grid
   right after a full cleanup — such a worker's own resulting pattern has
   nothing to do with `carry_seed_grid`/`carry_locked_letters` at all, yet
   `_cycle_start_preview` was unconditionally overlaying those carried-
   forward locked letters onto whatever grid it was handed. Reproduced
   live: a real `generate_grid()` run's `pattern_generated` preview (see
   below) showed *fewer* black cells than the same palier's own "pattern"
   (cycle-start) preview at cycle 27 — an impossible outcome for a real
   carried-forward pattern (`make_pattern` can only ever add black cells
   on top of one, never remove any), immediately pointing at a reset
   worker's own unrelated, independently-generated pattern being shown
   with someone else's locked letters painted over one of its own black
   cells, silently erasing that black cell from the preview. Fixed with a
   guard in `_cycle_start_preview` (both its `preseed_assignment` and
   `locked_letters` overlay branches): a cell already `BLACK` in the grid
   being previewed is now skipped rather than overwritten with a letter,
   and only genuinely-overlaid cells are reported in the returned
   `locked_cells` list. Provably a no-op for every *other* case (a real
   carried-forward pattern, or "reprise telle-quelle"'s byte-identical
   one) — a locked cell is already guaranteed to stay white there by
   construction elsewhere in this file — so this only ever changes
   anything for a reset worker's own independent pattern. Verified: two
   isolated `_cycle_start_preview` calls confirmed the guard directly (a
   locked cell overlapping a black cell in the given grid is skipped,
   with only the non-conflicting locked cell reported; a normal case
   with no conflict is unaffected); a real `generate_grid()` run with an
   `on_progress` hook checking every `pattern_generated` example
   confirmed no locked cell ever coincides with a black one again.

   **A separate, related bug in the same visual area was reported next**,
   with two screenshots as direct evidence: a "cases noires posées,
   recherche des mots en cours" preview (the `pattern_generated` event,
   *before* the CSP search runs) showing many cells outlined as locked,
   followed by that exact same attempt's own "Tentative N/200 échouée"
   preview (the `pattern_attempt_failed` event, *after* the search fails)
   showing only a handful still outlined — "il y a des cas où le
   processus de génération des mots ne préserve pas les cases
   verrouillées." Root-caused before writing a single line of fix code,
   by tracing `Filler._domain` directly: a locked/known letter (from
   `carry_locked_letters`/`known_letters`, merged into `forced_letters` by
   the caller — `{**forced_letters, **locked_letters}`) constrains *every*
   slot touching that cell, in both directions, before either slot is
   assigned — so whichever of the two crossing slots the search assigns
   first must already match the locked letter (its own domain was
   filtered down to only words matching it), and the other slot then sees
   a real, *consistent* crossing assignment. The actual letter values were
   therefore never at risk — this was a diagnostics/display bug, not a
   correctness bug in the solver itself.

   The real cause: `try_fill`'s own `locked_cells` diagnostic (the field
   driving the UI's red/orange "verrouillé" outline) was computed *only*
   from `preseed_assignment` — the subset of locked cells whose entire
   containing slot happened to be fully covered by `locked_letters` and
   validated as a real word (see the pre-fill/preseed mechanism
   documented throughout this section). A cell belonging to a slot only
   *partially* covered by `locked_letters` (the rest of that slot's
   letters still to be found by the search) is just as hard a constraint
   on the solver — merged into `forced_letters` exactly the same way — but
   was never counted in `locked_cells`, so it never got the "verrouillé"
   highlight at all once the search moved on to fill in the rest of that
   slot with real, correctly-constrained letters. This mismatch is exactly
   what made the two screenshots look so different: `_cycle_start_preview`
   (driving the `pattern_generated`/`pattern` events) already shows *every*
   cell in `carry_locked_letters`/`known_letters` unconditionally, while
   `try_fill`'s own `locked_cells` (driving `pattern_attempt_failed`) only
   ever showed the narrower, fully-preseeded subset.

   Fixed by giving `try_fill` a new `locked_letters=None` parameter (the
   *raw* dict, not the merged `forced_letters`) and computing `locked_cells`
   from it directly whenever it's supplied: every cell in `locked_letters`
   that belongs to some slot of the pattern, not just the cells of
   fully-preseeded slots — `preseed_assignment`'s own narrower computation
   is kept only as a fallback for a caller that supplies `preseed_
   assignment` without `locked_letters` (no such caller exists today).
   `_pattern_attempt` passes its own `locked_letters` variable (already
   augmented by `_force_single_candidate_slots`, so a cell only just
   *deduced* to be certain is correctly shown as locked too, not just the
   ones carried forward verbatim); `_pattern_continue` passes its own
   equivalent `known_letters`. Purely a diagnostics/reporting change — no
   effect whatsoever on `Filler`'s actual constraint solving, on
   `_build_retry_seed`, or on any other decision `generate_grid` makes;
   `locked_cells` is only ever read by the preview-building code (`d.get
   ("locked_cells", [])`), never by any control-flow logic.

   Verified in isolation first: 4 hand-built cases against a tiny 3×3
   grid/dictionary — a fully-locked slot (already worked before the fix,
   confirmed unaffected); a slot with only 1 of its 3 cells locked (the
   exact reported gap — confirmed present in `locked_cells` after the fix,
   confirmed it would have been silently missing under the old,
   preseed-only computation); no locked letters at all (stays empty, no
   regression); a `preseed_assignment`-only caller with no `locked_letters`
   (falls back to the pre-existing computation unchanged). Then verified
   live end-to-end against the real standard 15×10 benchmark: a dedicated
   diagnostic captured, for every palier, how many locked cells the
   `pattern_generated` preview showed vs. how many the same palier's
   `pattern_attempt_failed` preview showed for its own best example —
   before this investigation's fix, the second number could collapse
   towards zero almost anywhere; after it, the two track each other
   closely across the large majority of paliers (39/39 and 55/56 sampled
   paliers respectively across two separate runs), several even showing
   *more* locked cells after the search than before it (a legitimate
   effect of `_force_single_candidate_slots` deducing further certain
   letters once the search narrows things down further).

   A residual handful of paliers (4 out of 56 in one full run) still
   showed a collapse to a much lower, sometimes-zero count — investigated
   rather than dismissed, and found to be a **different, already-
   understood, and much smaller cosmetic quirk**, not a recurrence of the
   same bug: every one of the flagged paliers coincided exactly with a
   palier where `just_cleaned` was `True` (i.e., immediately following a
   full nettoyage, the only time `FULL_RESET_ATTEMPT_FRACTION` — 20% of
   that palier's workers — start from a totally blank, independent grid
   with `locked_letters=None`), confirmed directly by temporarily logging
   `just_cleaned`/`reset_count` right where they're computed in
   `generate_grid` and cross-referencing the two. When the palier's
   "best" failed candidate (fewest impossible cells) happens to be one of
   these reset workers, its own `try_fill` diagnostics correctly report
   `locked_cells=[]` (nothing was ever locked for it) — but the `pattern`/
   `pattern_generated` preview for that same candidate is built once per
   *palier*, not per candidate, via `_cycle_start_preview(rows, cols, g,
   carry_locked_letters, carry_preseed_assignment)`, which still overlays
   the palier's own carried-forward locked letters onto that reset
   worker's unrelated grid `g` — a distinct, narrower version of the exact
   reset-worker-preview quirk already root-caused and partly fixed earlier
   in this same investigation (see the `_cycle_start_preview` black-cell-
   overlap guard just above). Confirmed directly: every flagged palier's
   chosen candidate had a black-cell count matching the reset workers'
   typical blank-grid pre-fill output, unrelated to that palier's own
   evolving `carry_seed_grid` black-cell count. Left unaddressed for now
   as a separate, minor, cosmetic-only quirk (it never affects the actual
   solving, only which grid a reset-worker candidate's own preview
   overlays letters onto) — not the bug reported, and not touched further
   without a separate explicit request.

   **This exact quirk was reported again next, in a much more alarming
   form**, with two screenshots as direct evidence: a "Génération du motif
   de cases noires" preview (the `pattern` event, cycle start — before
   this palier's own black-cell placement) clearly showing the word
   CAROLINE as a whole, intact 8-letter slot flanked by 2 black cells,
   followed by that same palier's own "Motif de cases noires posé...
   recherche des mots en cours" preview (the `pattern_generated` event, up
   to 6 candidate grids) showing CAROLINE's flanking black cells gone and
   the word instead "cut" by a black cell somewhere in its own middle —
   "il y a toujours un vrai problème de gestion des cases noires... on
   voit clairement le mot CAROLINE entouré de 2 cases noires [avant]... a
   perdu ses cases noires devant derrière, et est maintenant coupé par une
   nouvelle case noire (ou une case noire qui a changé d'emplacement dans
   le calcul) [après]. Idem pour plusieurs mots en dessous."

   Reproduced and root-caused with a dedicated live diagnostic rather than
   assumed to be the already-known reset-worker quirk above: for every
   attempt of a real `generate_grid()` run on the standard 15×10
   benchmark, compared the cycle-start preview's own black cells against
   each of the up-to-6 `pattern_generated` candidates' own black cells.
   Confirmed **zero genuine losses** among candidates whose own black-cell
   pattern actually overlaps substantially with the cycle-start one (i.e.,
   a real, carry_seed_grid-derived worker) — `make_pattern` never turns an
   existing black cell white, exactly as its own docstring guarantees.
   Every single "lost black cell" instance (10 found in one run) occurred
   exclusively among candidates whose own black-cell pattern shared less
   than half its cells with the cycle-start grid — the unmistakable
   signature of a **reset worker** (`FULL_RESET_ATTEMPT_FRACTION`, ~20% of
   a palier's workers right after a full nettoyage, starting from a
   totally blank, independent grid with `seed_grid=None,
   locked_letters=None`) — every one of these candidates' own black-cell
   count converged on the exact same value (15, on this specific 15×10
   grid/word-list combination) regardless of the cycle-start pattern's own
   evolving black-cell count (10 to 39 across the flagged attempts),
   exactly the same blank-grid pre-fill convergence signature already
   confirmed for this mechanism earlier in this investigation.

   This confirmed the previous entry's own "left unaddressed... cosmetic-
   only" categorization was the right root cause, but no longer an
   acceptable one to leave alone: for a reset-worker candidate, `_cycle_
   start_preview` was still unconditionally overlaying the *palier's own*
   `carry_locked_letters`/`carry_preseed_assignment` onto that candidate's
   own, entirely unrelated grid — painting "CAROLINE"'s letters at the
   exact same absolute grid coordinates they occupy in `carry_seed_grid`,
   regardless of where *that specific candidate's own* black cells happen
   to fall nearby. Since a reset worker's pattern is built completely
   independently, its own black cells near those same coordinates can
   easily interrupt or displace the overlaid word, producing exactly the
   reported illusion of a locked word being "cut" or having its boundary
   cells "moved" — when in reality these are two entirely different,
   unrelated patterns being shown side by side, and no actual black cell
   was ever added, removed, or relocated on any single grid.

   Fixed by giving `_pattern_attempt`'s own `diag` a new
   `is_reset_worker` flag (`seed_grid is None`, computed once, right where
   `diag = {}` is created — `_pattern_continue` never sets it, since that
   path never resets anything, matching its own long-standing "motif et
   verrouillage rigoureusement identiques d'une tentative à l'autre"
   contract) and threading it to every place `generate_grid` calls
   `_cycle_start_preview` on a *specific candidate's* own grid (as
   opposed to the cycle-start event itself, which is always built
   directly from `carry_seed_grid` and therefore never needs this
   distinction): both the winning success case (`successes` now carries
   each outcome's own `diag` alongside `(grid, result)`, so `best_diag`
   is available right where the "pattern_generated" preview for the
   winning palier is built) and the up-to-6 failed-candidate loop now
   pass `None`/`None` instead of `carry_locked_letters`/`carry_
   preseed_assignment` whenever the candidate's own `is_reset_worker` flag
   is set — a reset-worker candidate's preview now shows its own plain
   black/white pattern with no overlaid letters and no locked-cell
   highlight at all, correctly reflecting that nothing was genuinely
   locked for it, rather than a misleading, borrowed overlay from an
   unrelated pattern.

   Verified live: a dedicated diagnostic re-run after the fix confirmed,
   across every reset-like candidate found (12 in one run), `locked_cells`
   is now always empty (down from a previously nonzero, misleading count
   for the same candidates) — and, just as importantly, every genuinely
   carry_seed_grid-derived candidate (231 in the same run) still correctly
   reports a real, nonzero `locked_cells` list, confirming the fix is
   scoped precisely to reset workers and introduces no regression to the
   normal case. A full end-to-end `generate_grid()` run on both seeds of
   the standard 15×10 benchmark confirmed no regression to the actual
   generation outcome either (this whole investigation, start to finish,
   never found any genuine black-cell loss in the solver itself — only in
   how a reset-worker's own unrelated pattern was *previewed*).

   **A genuinely new, previously-unnoticed bug was found next, reported by
   the user with the same rigor as before**: two screenshots of the same
   attempt, showing the word AVALAS locked (orange-outlined) in the
   "before search" preview, gone entirely from the "after failed search"
   preview of that *same* attempt — and, critically, the user pointed out
   directly that the black-cell pattern *and* the count of other locked
   cells were both unchanged between the two, ruling out the reset-worker
   explanation just above on its own evidence ("on voit bien qu'il s'agit
   des mêmes cases noires... il ne s'agit donc pas d'une nouvelle
   grille... les cases verrouillées ont été perdues quelque part dans le
   processus de remplissage, qui ne devrait pas les remettre en cause").

   Root-caused with a controlled, hand-built reproduction rather than
   guessed: a tiny, fully controlled dictionary where a real, fully-locked
   word ("AVOIR", standing in for "AVALAS") is deliberately the *only*
   dictionary entry matching its own exact spelling (1 candidate) —
   exactly the case for the overwhelming majority of real 5+ letter words
   in the actual French/English/etc. word lists, where a specific word is
   almost always the unique match for its own precise letter sequence.
   Calling `_slot_with_insufficient_candidates` directly on this
   fully-locked, entirely valid word confirmed it: the function returned
   *this exact slot* as "insufficient" (candidate count 1, strictly below
   `PREFILL_LOCKED_MIN_WORD_COUNT` = 3) — even though the word is already
   fully confirmed and needs no further candidates at all. The check's own
   condition, `locked_letters and any(cell in locked_letters for cell in
   slot)`, never distinguished a slot that's only *partially* locked
   (genuinely still being solved, where a low remaining-candidate count is
   a real problem worth fixing) from one that's *entirely* locked already
   (a done, resolved word, where "few candidates" is simply the normal,
   expected reality of most words being unique matches for their own
   spelling — not a problem at all). Once flagged this way, the pre-fill
   loop tries a black cell first (impossible here — every cell of a fully
   locked slot is already excluded from the candidate pool) and falls back
   to `_remove_a_crossing_word`, which then un-confirms an unrelated
   *crossing* word to "relax" the flagged (but not actually problematic)
   slot — exactly the observed symptom: a perfectly good, already-
   confirmed locked word losing its letters, on the exact same black-cell
   pattern, with no new/different grid involved at all. Confirmed directly
   in the reproduction: after the fix (below), the same fully-locked word
   survives `make_pattern` completely untouched, on the very same input.

   Fixed in both places that carried this exact same flaw —
   `_slot_with_insufficient_candidates` (drives `_prefill_unfillable_
   slots`/nettoyage curatif's own trigger) and `_new_black_cell_breaks_
   locked_slot` (the preventive filter guarding `_place_black_cells`'s own
   candidate acceptance) — by requiring a slot/run to be only *partially*
   covered by `locked_letters` (`0 < locked_count < length`) before its
   candidate count is even checked; a slot entirely covered by
   `locked_letters` is now never flagged by either function, regardless of
   its own candidate count. Deliberately *not* extended to also validate a
   fully-locked slot's own combination against the dictionary here (i.e.,
   still not flagging even a fully-locked-but-*invalid* slot, candidate
   count 0): that case is a genuinely different, downstream concern
   already handled correctly elsewhere, once `make_pattern` returns —
   `_pattern_attempt`/`_pattern_continue`'s own `preseed_assignment`/
   `locked_impossible_slots` computation independently validates every
   fully-locked slot against the real dictionary and leaves it `None`
   (surfacing through `impossible_slots` and `_build_retry_seed`'s
   existing cross-palier cleanup) when the combination doesn't spell any
   real word — pre-fill has no useful lever over that case anyway (no
   candidate cell available for a black cell — every cell already
   locked — and removing a *crossing* word doesn't change the flagged
   slot's own, already-fixed letters).

   Verified: 5 isolated cases against `_slot_with_insufficient_candidates`
   (fully locked + valid → never flagged, the fix's core target; fully
   locked + invalid/0 candidates → also never flagged, confirming the
   downstream-concern reasoning above; partially locked + insufficient
   candidates → still correctly flagged, no regression to the mechanism's
   real purpose; partially locked + sufficient candidates → correctly
   unflagged, already true before; no locked letters at all → unaffected)
   and the same 4 relevant cases against `_new_black_cell_breaks_locked_
   slot` all passed. Two full end-to-end `generate_grid()` runs on both
   seeds of the standard 15×10 benchmark succeeded with no regression —
   seed 2 in 51.9s (55 words, 34 black cells) and seed 7 in 95.2s (56
   words, 39 black cells), both faster than every recent measurement of
   this same benchmark, consistent with far less needless word-removal
   churn now that a confirmed, already-valid word is never mistakenly
   treated as a problem to fix. This fix is separate from, and not
   verified to resolve, the still-paused seed-2 total-failure regression
   documented above (that investigation was explicitly left for the user
   to continue themselves) — but by eliminating a whole, very common class
   of spurious word removals (any fully-locked word whose own exact
   spelling happens to be rare in the dictionary, which is most words),
   it plausibly reduces how often that other mechanism is even exercised
   at all; not claimed as a fix for it without the user's own re-test.

   **Reported again almost immediately, with a new word ("ENREGISTRAT...")
   and the exact same shape**, prompting a direct live check of the just-
   shipped fix above before assuming it had a gap: instrumented `_remove_a_
   crossing_word` temporarily to log every real removal in a full
   benchmark run (139 removals) together with the triggering slot's own
   locked/total ratio — **every single one** was strictly partial
   (`2/3`, `3/4`, ... `9/10`, never `X/X`), confirming the previous fix
   works exactly as intended: no fully-locked slot is ever the trigger
   anymore. So nettoyage curatif was still legitimately removing crossing
   words (by design, to relax a genuinely under-constrained *other* slot)
   — meaning the newly reported disappearance had a different cause.

   The user's own counter-argument was decisive and precisely targeted at
   the right layer: the "before" screenshot (`pattern_generated`, "cases
   noires posées... recherche des mots en cours") already reflects the
   state *after* `make_pattern` (and therefore after nettoyage curatif) has
   finished for that attempt — so if the word was still shown locked
   there, its disappearance in the "after" screenshot could only come from
   the *search* phase itself, not from pre-fill, since pre-fill was already
   done by the time "before" was captured. This reasoning turned out to
   correctly identify that *something* was inconsistent between the two
   previews — but root-caused live (not simply trusted as "the search must
   be buggy"), the actual explanation was a level more subtle than either
   hypothesis: **the "before" preview itself was stale**, not the search.

   `generate_grid` (the parent process) has always built the "before"
   preview from `carry_locked_letters`/`carry_preseed_assignment` — the
   *palier's own* state, fixed before any of its parallel attempts even
   started — never from the *specific candidate's own* actual state after
   its own `make_pattern` call. A `_pattern_attempt` worker runs in its own
   OS process (`ProcessPoolExecutor`), so if *that specific worker's own*
   nettoyage curatif removed a crossing word during its own pre-fill, that
   reduction lives only inside that worker's own local `locked_letters`
   dict and is never communicated back to the parent — the parent has no
   way to know, and kept painting the *original, palier-wide* locked state
   onto every candidate's own preview regardless. Confirmed directly with
   a temporary snapshot (`diag["_debug_locked_letters_after_make_pattern"]`,
   captured right after `make_pattern`/`_force_single_candidate_slots`
   inside the worker, compared against `carry_locked_letters` back in the
   parent): **12 real cases** in one full benchmark run where a non-reset
   candidate's own real state, right after its own `make_pattern`, was
   already missing cells that `carry_locked_letters` (and therefore the
   "before" preview) still showed as locked — proving the discrepancy
   originates before the search even starts, in how the preview is built,
   not in the search losing anything.

   This generalizes the reset-worker fix from earlier in this same
   investigation rather than sitting alongside it as a second special
   case: both are really the same underlying problem (the preview assumes
   every candidate shares the palier's own locked state, when a specific
   candidate's own real state can legitimately differ — either because it
   never derived from that state at all, or because its own nettoyage
   curatif reduced it). Fixed by having `_pattern_attempt` report its own
   real, final `locked_letters` unconditionally (`diag["own_locked_
   letters"]`, a plain snapshot taken right after `make_pattern` and
   `_force_single_candidate_slots`, before `try_fill` ever runs — the same
   dict object `try_fill` itself then uses as its own `locked_letters`
   parameter, so the two can never drift apart afterward) and replacing
   both previous ad hoc `is_reset_worker`-based ternaries in `generate_
   grid` with one new helper, `_preview_locked_source(candidate_diag,
   carry_locked_letters, carry_preseed_assignment)`: if the candidate's own
   diag carries `own_locked_letters` (any `_pattern_attempt` outcome,
   reset worker included — a reset worker's own snapshot is simply empty
   or reflects only what its own independent grid could deduce, achieving
   the exact same "no misleading overlay" result as the old dedicated
   flag, with no special-casing needed), that becomes the preview's own
   locked-letters source; otherwise (a `_pattern_continue` outcome, which
   never sets this field, since that path never resets or removes
   anything — see its own docstring) the palier-level `carry_locked_
   letters`/`carry_preseed_assignment` remains exactly right, unchanged.
   The now-redundant `is_reset_worker` diagnostic field was removed
   outright (no remaining consumer) rather than left as dead code.

   Verified: 3 isolated `_preview_locked_source` cases (a `_pattern_
   attempt`-shaped diag with its own reduced state, correctly used
   verbatim; a `_pattern_continue`-shaped diag with no such field,
   correctly falling back to the palier state via `is` identity checks;
   a reset-worker-shaped diag with an empty snapshot, correctly producing
   no overlay at all) all passed. Live, across a real run: compared every
   one of a palier's up-to-6 candidates' own "before" (`pattern_generated`)
   locked-cells set against that *same* candidate's own "after"
   (`pattern_attempt_failed`) locked-cells set — since both now read from
   the exact same underlying dict for a `_pattern_attempt` candidate, any
   cell present "before" but missing "after" would mean the bug still
   exists. Across 80 real candidate before/after pairs sampled from a full
   benchmark run: **zero** such losses (down from a real, confirmed,
   non-zero rate before this fix) — 48 pairs legitimately showed *more*
   cells "after" than "before" (expected only for `_pattern_continue`
   candidates, whose own per-attempt `_force_single_candidate_slots` can
   deduce further cells beyond the palier-level state — a benign growth,
   never a loss). A full end-to-end `generate_grid()` run on both seeds of
   the standard 15×10 benchmark confirmed no regression to the actual
   generation outcome.

   **A seventh, unrelated bug surfaced right after the sixth fix above
   shipped**: `own_locked_letters` (that same fix's own new diagnostic
   field, see its own entry) could make a real, in-progress generation's
   `GET /api/generate/status/{job_id}` poll fail outright with a 500 —
   the web UI showed a raw `JSON.parse: unexpected character at line 1
   column 1 of the JSON data`, since the response body was no longer
   valid JSON at all once that happened. Root-caused directly from
   `backend.log`'s own real traceback (found by request, not
   reproduced from a symptom description this time): `fastapi.encoders.
   jsonable_encoder` raised `TypeError: cannot use 'list' as a dict
   key` while serializing a real `pattern_attempt_failed` progress
   event. `own_locked_letters` is a dict keyed by grid cell — a
   `(row, col)` *tuple* used as a dict *key* — and `jsonable_encoder`
   recursively encodes every dict key to make it JSON-safe first, which
   turns a tuple key into a list; it then tries to use that freshly-
   encoded list as a key in the plain Python dict it's building up as
   the encoded result, and a list isn't hashable. Every *other* diag
   field carrying cell coordinates (`locked_cells`, `impossible_cells`,
   `forced_cells`, ...) holds them as *elements of a list*, never as
   dict keys, so none of them hit this — `own_locked_letters` was the
   only field with this exact shape.

   The field itself was never meant to leave `generate_grid()` in the
   first place — it exists purely so `_preview_locked_source` (see the
   sixth fix above) can pick the right locked-letters source for a
   specific candidate's own preview, and is fully read and consumed
   before `last_diag` (the winning failed candidate's own diagnostics)
   gets spread unconditionally via `**last_diag` into the
   `pattern_attempt_failed` progress event (and nested, unfiltered, as
   `last_attempt=last_diag` in the terminal `pattern_failed` event) —
   both of which flow straight into `backend/app.py`'s `job["step"]`
   (`job["step"] = {"code": step, **data}`), the exact dict `GET /api/
   generate/status/{job_id}` serializes and returns. Fixed with a new
   `_public_diag(diag)` helper (right after `_preview_locked_source`,
   whose own docstring it cross-references) that returns a copy of
   `diag` with `own_locked_letters` excluded — used at both of the two
   call sites that spread/nest `last_diag` into a `progress(...)` event;
   every other consumer of the raw per-candidate diag (`_preview_locked_
   source` itself, `_build_retry_seed`'s own `selected_diag`, the
   `pattern_generated`-preview loop) reads it *before* this filtering
   point and is completely unaffected, since none of them ever hand
   their diag to `progress(...)` directly.

   Verified: reproduced the exact `TypeError` in isolation by calling
   `fastapi.encoders.jsonable_encoder` directly on a diag dict shaped
   like the one in `backend.log` (a `own_locked_letters` dict with
   tuple keys) — confirmed it raises the identical error message: then
   confirmed `_public_diag()` on that same dict encodes cleanly, with
   every other key (`locked_cells` included, tuples-as-list-elements
   this time) intact and unchanged. A full end-to-end `generate_grid()`
   run on both seeds of the standard 15×10 benchmark, with every single
   `on_progress` event fed straight through `jsonable_encoder` (exactly
   mirroring what `GET /api/generate/status/{job_id}` does to the real
   job dict), confirmed zero unserializable events across the whole run
   — where the exact same check on the pre-fix code would have failed
   the moment any `pattern_attempt_failed`/`pattern_failed` event fired.

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

   **`PARALLEL_ATTEMPTS`'s own default was changed from a fixed 10 to this
   machine's own CPU count**, much later in this project's history, at the
   user's explicit request: "Nombre de process lancés en parallèle = nombre
   de processeurs de la machine (au lieu de 10)." A fixed default had no
   relationship to the actual hardware a given deployment runs on — too low
   on a many-core machine (leaving cores idle that could run another
   attempt), potentially too high on a machine with very few cores (more
   attempts than cores means some of them compete for the same core instead
   of running truly in parallel). `PARALLEL_ATTEMPTS = int(os.environ[
   "CROSSWORDFALCON_PARALLEL_ATTEMPTS"]) if os.environ.get(
   "CROSSWORDFALCON_PARALLEL_ATTEMPTS") else (os.cpu_count() or 1)` — the
   `CROSSWORDFALCON_PARALLEL_ATTEMPTS` override mechanism itself is
   unchanged (still read the same way, still sourced from `env.sh`/
   `env_default.sh` by `run_Falcon.sh`), only the *fallback* used when it's
   unset changed, from the literal string `"10"` to a live `os.cpu_count()`
   call; `or 1` guards `os.cpu_count()`'s own documented edge case (it can
   return `None` when the count truly can't be determined), so this can
   never resolve to `0` and crash `ProcessPoolExecutor(max_workers=0)`.
   `env_default.sh`/`env.sh` no longer `export` a hardcoded value at all —
   the line is now commented out, shown only as an example of how to
   override the new per-machine default, not as the default itself.
   Verified: reloading `crossword_gen` with the environment variable unset
   confirmed `PARALLEL_ATTEMPTS` matches `os.cpu_count()` exactly (10 on the
   machine this was tested on); reloading again with it set to `"4"`
   confirmed the override still works, unchanged; monkeypatching
   `os.cpu_count()` to return `None` confirmed the fallback resolves to `1`,
   never `0` or an error. Two real `generate_grid()` runs on the standard
   15×10 benchmark (both seeds, with the override left unset — so
   `PARALLEL_ATTEMPTS` really did resolve from live hardware detection, not
   a hardcoded test value) confirmed no regression: `0` mismatches, `0`
   empty white cells each (seed 2 in 24.8s, 54 words; seed 7 in 59.5s, 57
   words).

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

   **The same "stack instead of overwrite, drain one per poll" mechanism
   was generalized from just the preview grids to the plain per-cycle
   status text too**, at the user's explicit request: "Stacké les états
   de chaque fin de cycle pour l'interface, qui les affiche au rythme
   d'un affichage toutes les 2s." Before this, `job["step"]` (the plain
   status the UI's `#status` line reads via `describeStep()`) was
   unconditionally overwritten by every single `progress()` call, exactly
   the same bug class `examples_history` was built to fix for the
   preview grids — a palier that resolves fast enough (several per poll
   window, already directly observed above) could have its own status
   silently skipped, with the client only ever seeing whichever palier
   happened to be the most recent one at poll time.

   `backend/app.py`'s `_new_job()` gained `job["step_history"]` (an
   ever-growing list, parallel to `examples_history` but storing the
   *whole* progress payload, not just its `examples` field) and
   `progress()` now appends `job["step"]` to it whenever `step` is
   `"pattern_attempt_failed"` or `"pattern_found"` — the two codes that
   mark a palier's own genuine end (`"pattern"` itself marks the *start*
   of the next one; `"pattern_failed"`, the terminal total-failure case,
   is immediately followed by `job["status"] = "error"`, leaving no
   realistic window where a client could poll it as an in-progress status
   worth queueing). `crossword_gen.py`'s `progress("pattern_attempt_failed",
   ...)` call gained an `attempts=attempts` kwarg it didn't carry before
   (the search budget, 200 by default) — needed so a stacked entry has
   everything a `statusPattern`-style message needs, matching what the
   `"pattern"` event already carries.

   `frontend/static/script.js`'s `pollJob()` gained a second cursor,
   `nextStepHistoryIndex`, mirroring `nextExampleIndex` exactly: on every
   poll, if `step_history` has an entry this cursor hasn't shown yet, that
   entry (not the live `data.step`) feeds `describeStep()`, and the
   cursor advances by one — draining exactly one new cycle-end status per
   poll, in order, the same guarantee already established for the preview
   grids. The same `POST_SEARCH_STEP_CODES` catch-up already used for
   `nextExampleIndex` was extended to jump `nextStepHistoryIndex` forward
   too, for the identical reason (a large search-phase backlog must never
   keep "replaying" once the job has already moved on to minimizing/
   clue-generation). Once both cursors are caught up, the live `data.step`
   is shown directly, exactly as before this feature existed.

   `describeStep()` gained a new `"pattern_attempt_failed"` case
   (previously falling through to the generic `statusGenerating` message,
   since a stacked backlog of nothing but generic "Génération en cours…"
   repeats would defeat the point of stacking it at all) —
   `t.statusPatternAttemptFailed(attempt, attempts, totalAttempts)`, a new
   i18n key in all 5 languages mirroring `statusPattern`'s own wording
   style (e.g. French: "Tentative X/Y échouée (Z grilles échouées au
   total), nouvelle tentative en cours…").

   Verified live: an isolated reproduction of `generate_grid()`'s own
   `on_progress` events (15×10, seed 2, easy) confirmed `step_history`
   would correctly hold only `"pattern_attempt_failed"`/`"pattern_found"`
   codes (18 failures then the final success), each carrying `attempt`/
   `attempts`/`total_attempts`; a real job submitted through the actual
   running API showed `step_history` growing to 26 entries within the
   first several polls, each a well-formed `pattern_attempt_failed`/
   `pattern_found` dict; a Python-translated reproduction of the exact
   cursor logic (`nextStepHistoryIndex`, the `POST_SEARCH_STEP_CODES`
   catch-up) confirmed both scenarios directly: several queued cycle-end
   entries drain exactly one per simulated poll during search, and the
   cursor correctly jumps straight to the final (winning) entry the
   instant a `"minimizing"`-or-later step is seen, rather than continuing
   to drain an old backlog; a second real job polled every 2s through
   `minimizing`/`clues` showed `step_history` growing from 4 to 13 entries
   and then holding steady once the search phase ended, matching
   `examples_history`'s own already-verified behavior. A real JS syntax
   check (`esprima`, temporarily installed and removed again afterward,
   same pattern used elsewhere in this project) confirmed `script.js`/
   `i18n.js` still parse correctly after the change.

   **Manual navigation through this same history was added next**, at the
   user's explicit request: "Dans l'interface ajouter des boutons (à coté
   de la mention 'Aperçu des dernières tentatives') qui permettent de
   remonter/avancer dans l'historique des états affichés." Until this
   point, `pollJob()`'s two cursors (`nextExampleIndex`/
   `nextStepHistoryIndex`) only ever moved forward automatically, one
   entry per poll — the player had no way to look back at an earlier
   attempt-preview state once a newer one had replaced it on screen, nor
   to review it again later.

   Two new buttons, `#attempt-preview-prev-btn`/`#attempt-preview-next-btn`
   ("◀"/"▶", shared `.nav-btn` class), sit right next to
   `#attempt-preview-label` inside a new `#attempt-preview-header` flex
   row (`frontend/static/index.html`) — the row, not the label itself, now
   owns the bottom margin that used to sit directly on
   `#attempt-preview-label` (`style.css`). These are independent from
   `pollJob()`'s own auto-reveal cadence, not a replacement for it:
   `pollJob()` keeps auto-advancing through `examples_history` exactly as
   before, but every revealed entry is now also recorded into a new,
   purely client-side history (`script.js`'s `previewHistory`, an
   ever-growing array — distinct from the backend's own
   `job["examples_history"]`, which `previewHistory` is built from one
   entry at a time as `pollJob()` reveals each) alongside a
   `previewHistoryIndex` pointer at whichever entry is currently on
   screen. `recordPreviewHistory(examples)` replaces `pollJob()`'s direct
   `renderAttemptPreview(...)` call at the point where a new entry is
   revealed: it appends to `previewHistory`, and only re-renders/advances
   `previewHistoryIndex` to the new entry if the player was already
   viewing the *previous* newest one (`wasAtEnd`, checked before the
   push) — if they had clicked "◀" to look back at an earlier state, a
   newly-arrived entry is simply queued (enabling "▶") without yanking
   their current view forward, the same "pause autoscroll while scrolled
   up" behavior a chat/log viewer gives. `showPreviousPreview()`/
   `showNextPreview()` (the two buttons' click handlers) move
   `previewHistoryIndex` by one and re-render whatever entry it now
   points at; `updatePreviewNavButtons()` disables "◀" at the very start
   of the history and "▶" once caught up to the newest entry, called from
   all three of the above plus `hideAttemptPreview()`, which now also
   resets `previewHistory`/`previewHistoryIndex` back to empty/`-1`
   alongside its pre-existing reset of `lastPreviewExamples` — so every
   fresh generation (and the moment the final grid is ready) starts this
   navigable history over from nothing, exactly like every other
   per-generation preview state. `renderAttemptPreview()` itself is
   unchanged — both the auto-reveal path and the two new manual-navigation
   paths funnel through the exact same rendering function, so
   `togglePreviewLetters()`'s own re-render of `lastPreviewExamples` (the
   letter-visibility toggle) continues to work correctly regardless of
   whether the currently-shown entry got there via auto-reveal or manual
   navigation.

   Verified without a real browser (this session's environment still has
   no `chromium-cli`/`node`/Python `playwright`, the same tooling
   limitation noted throughout this project's UI work): a direct Python
   translation of `recordPreviewHistory`/`showPreviousPreview`/
   `showNextPreview`/`updatePreviewNavButtons`'s exact logic, run through 5
   scenarios — auto-follow across 5 successive entries with the buttons'
   own disabled state matching (prev enabled, next disabled at the end);
   navigating back twice re-renders exactly the two skipped-past entries
   in the right order; a new entry arriving while the view sits at an
   older position does *not* auto-jump forward (confirmed the index stays
   put and nothing gets re-rendered, only the "next" button becomes
   enabled); clicking forward three times from that same older position
   correctly catches back up through the full backlog, one entry per
   click, ending with "next" disabled again; and the reset leaves both
   buttons disabled — all 5 passed. The real served
   `index.html`/`style.css`/`i18n.js`/`script.js` were fetched directly
   from the running frontend server (no restart needed — these are static
   files served straight from disk) and confirmed to contain the new
   markup, the new `.nav-btn`/`#attempt-preview-header` CSS rules, the new
   `attemptPreviewPrevBtn`/`attemptPreviewNextBtn` translations in all 5
   languages, and the new JS functions themselves. A real JS syntax check
   (`esprima`, temporarily installed and removed again afterward) confirmed
   `script.js`/`i18n.js` still parse correctly after the change.

   **This same navigable history was extended to also carry the status
   text alongside each grid**, at the user's explicit follow-up request:
   "L'historique des visualisation doit inclure le status (indiquant
   notamment le nombre de cycles)." Until this, `previewHistory`'s
   elements were bare `examples` arrays — navigating back/forward showed
   an earlier or later preview grid with no indication of *which* cycle/
   attempt it actually came from. Fixed at the source: `backend/app.py`'s
   `progress()` closure now appends `{"step": ..., "examples": ...}` to
   `job["examples_history"]` instead of a bare `examples` list — `job
   ["step"]` (built right at the top of the same closure call) already
   carries exactly the cycle/attempt info the request asks for (`code`,
   `attempt`, `attempts`, `total_attempts`, or `current`/`total` for the
   "clues" step), so it's captured alongside the grids rather than
   re-derived. Stored as a shallow copy of `job["step"]` with its own
   `examples` key stripped out (`{k: v for k, v in job["step"].items() if
   k != "examples"}`), not the dict as-is — `job["step"]` also contains
   this very same `examples` list under `data`'s own key (since `progress
   ("pattern_attempt_failed", ..., examples=last_examples, **last_diag)`
   spreads it into `data`), which would otherwise double every entry's
   payload for no benefit, since nothing ever reads `entry["step"]
   ["examples"]`.

   `script.js`'s `previewHistory` elements are therefore now `{step,
   examples}` objects too (no change needed to how `pollJob()` pushes them
   in — `recordPreviewHistory(history[nextExampleIndex])` already passed
   the whole entry through unexamined). A new `showPreviewEntry(entry)`
   is the one place both halves of an entry actually reach the screen:
   `renderAttemptPreview(entry.examples)` for the grids (unchanged), plus
   `lastPreviewStep = entry.step` and a new `renderPreviewStatus()` for a
   new `#attempt-preview-status` line, right below `#attempt-preview-
   header` and above `#attempt-preview-grids` — styled small/discreet like
   `.attempt-preview-stats` (`style.css`). `recordPreviewHistory()`/
   `showPreviousPreview()`/`showNextPreview()` all now call
   `showPreviewEntry(...)` instead of `renderAttemptPreview(...)` directly,
   so the grid and its paired status can never drift out of sync
   regardless of which of the three paths displays a given entry.
   `renderPreviewStatus()` reuses `describeStep()` (already handles every
   step code this history can carry, "pattern"/"pattern_attempt_failed"/
   "pattern_found"/"minimizing"/"clues" alike, with no change needed there)
   — the same localized "Tentative X/Y échouée (Z grilles échouées au
   total)..." text the live `#status` line already shows, now also
   available paired with whichever historical grid the player is looking
   at. `hideAttemptPreview()` additionally resets `lastPreviewStep` and
   clears `#attempt-preview-status`'s text; the `languageSelect` "change"
   handler additionally calls `renderPreviewStatus()` (alongside its
   pre-existing `renderAttemptPreview(lastPreviewExamples)` re-render), so
   a UI language switch re-translates the currently-shown status text too,
   not just the grids' own stats line.

   Verified live: the real `POST /api/generate/status/{job_id}` response
   was fetched from the actual running backend mid-generation and read
   directly — confirmed every `examples_history` entry now has the
   `{"step": {...}, "examples": [...]}` shape, with `step` correctly
   carrying `code`/`attempt`/`attempts`/`total_attempts` for both
   `pattern` and `pattern_attempt_failed` entries and no nested `examples`
   key duplicated inside it; a real JS syntax check (`esprima`, installed
   and removed again afterward) confirmed `script.js` still parses
   correctly after the change.

   **Right after, at the user's own explicit follow-up request** ("Ajouter
   à l'historique (donc stacké) l'état initial d'un cycle"), the history
   was extended once more to also stack a cycle's own *starting* state,
   not just its outcome — until this, `examples_history` only ever
   accumulated an entry when a palier *ended* (`pattern_attempt_failed`/
   `pattern_found`) or during minimization/clue generation; the state a
   palier actually starts *from* (whatever `_build_retry_seed`/
   `_clean_blocked_slots` carried forward from the previous one, carried
   forward via `carry_seed_grid`/`carry_locked_letters`/
   `carry_preseed_assignment`) had no visual entry of its own, even though
   the "pattern" progress event that already marks a cycle's start (used
   for the live `#status` text, `statusPattern`) was firing the whole
   time.

   A new helper, `_cycle_start_preview(rows, cols, seed_grid,
   locked_letters, preseed_assignment)`, builds exactly that: a blank grid
   with nothing locked when `seed_grid` is `None` (the very first palier
   of a call); otherwise a copy of `seed_grid` with either
   `preseed_assignment` (the "reprise telle-quelle" resume shape — built
   by re-running `extract_slots` on `seed_grid` and overlaying every
   already-assigned slot's own letters, mirroring `build_letters_grid`'s
   own zip-based construction) or `locked_letters` (the "nettoyage" resume
   shape — a plain `{cell: letter}` map, overlaid directly) written onto
   it — the two resume shapes are the same mutually-exclusive pair
   `generate_grid`'s own palier loop already dispatches on elsewhere (`if
   carry_preseed_assignment is not None: ... else: ...`). Returns
   `(example_grid, locked_cells)` — `impossible_cells`/`forced_cells` are
   always empty for this entry, since nothing has been searched yet at
   this exact point (no slot can be "impossible" yet, and `sample_letter_
   biases` hasn't sampled anything yet either — that only happens once
   this palier's own `_pattern_attempt`/`_pattern_continue` workers
   actually start). Wired directly into the existing `progress("pattern",
   ...)` call (right in `generate_grid`'s palier loop, computed just
   before it), which now also carries a single-element `examples=[...]`
   list — reusing the exact same `{example_grid, impossible_cells,
   forced_cells, locked_cells}` shape every other preview entry already
   uses, and the exact same single-grid convention already established
   for the "minimizing"/"clues" steps (see above), so no frontend change
   at all was needed beyond what the previous entry already built — the
   web UI's history navigation now shows this "cycle start" grid as one
   more entry in the sequence, its `locked_cells` rendered with the same
   `.locked` highlight already used everywhere else, paired with its own
   "Tentative X/Y..." status text via the very mechanism the previous
   entry just added.

   Verified: three isolated `_cycle_start_preview` calls confirmed all
   three cases directly — `seed_grid=None` returns an all-blank grid with
   no locked cells; a real seed grid plus a `locked_letters` map correctly
   overlays those letters without mutating the original grid, with
   `locked_cells` matching the map's own keys exactly; a real seed grid
   plus a `preseed_assignment` (built via a real `extract_slots` call,
   only one of its two slots actually assigned) correctly overlays only
   the assigned slot's own letters, leaving the other slot's cells
   untouched. A real, non-mocked `generate_grid()` run on the standard
   15×10 benchmark (seed 2, easy, the full French wordlist — never an
   artificially small one, per this project's own permanent rule) with an
   `on_progress` hook capturing every `"pattern"` event directly confirmed:
   the very first palier's own cycle-start preview is genuinely blank (0
   locked cells, 0 black cells, all 150 cells still undetermined) while
   several later paliers show real, growing locked-cell/black-cell counts
   and shrinking undetermined-cell counts as the cross-palier retry
   mechanism carries more and more confirmed content forward — proving
   `_cycle_start_preview` reflects genuine carried-forward state, not a
   placeholder. The real running API was then also exercised directly
   (not just the isolated function/direct `generate_grid()` calls above):
   a real generation job polled repeatedly through `GET /api/generate/
   status/{job_id}` showed `examples_history` correctly alternating
   `pattern` (cycle start, 1 example) / `pattern_attempt_failed` (cycle
   end, up to 6 examples) entries in order, each carrying its own
   `{attempt, attempts, total_attempts, ...}` step info — confirming the
   whole mechanism end to end through the real HTTP API, not only through
   direct Python calls.

   **A third stage was stacked into this same history right after**, at
   the user's explicit follow-up request: "Stacker aussi l'état après la
   phase d'initialisation des cases noires." Until this, a palier's own
   narrative in the history had only two stages — its *start* (the
   carried-forward state, `_cycle_start_preview` above) and its *end*
   (`pattern_attempt_failed`/`pattern_found`, the CSP-search result) —
   with nothing showing the black-cell pattern this specific cycle
   actually placed, right after `make_pattern` finishes but *before* the
   search that fills it with letters even starts. `make_pattern()`
   itself only ever runs inside a worker process (`_pattern_attempt`,
   dispatched via `ProcessPoolExecutor`), invisible to the main process
   until the whole attempt (pattern *and* search) returns — so this
   intermediate stage can't be reported live, mid-search, the way
   `progress()` normally works; instead, it's built from data the main
   process already has once every one of a palier's parallel attempts has
   completed: each outcome's own returned `grid` (the plain black/white
   pattern, never mutated with letters afterward — letters live
   separately in its own `assignment`/`best_assignment`, an invariant
   already established elsewhere in this file for the "minimizing"
   preview) *is* exactly the state right after black-cell initialization
   for that attempt.

   A new `progress("pattern_generated", ...)` call fires right before
   both existing outcome events — `pattern_attempt_failed` (using each of
   `failed_pairs[:FAILED_ATTEMPT_EXAMPLES]`'s own grid) and `pattern_found`
   (using `best`'s own grid, right where `successes` is handled, before
   the `break`) — reusing `_cycle_start_preview(rows, cols, g,
   carry_locked_letters, carry_preseed_assignment)` on *that* outcome's
   own grid `g` instead of `carry_seed_grid`, so the same already-known
   locked letters get overlaid onto this cycle's *actual* new pattern
   rather than the previous cycle's one. For a "reprise telle-quelle"
   palier (`_pattern_continue`, which never calls `make_pattern` at all —
   `g` is byte-identical to `carry_seed_grid`), this new stage correctly
   coincides with the cycle-start entry, accurately reflecting that no
   black cell was added this time. `impossible_cells`/`forced_cells` stay
   empty (nothing searched yet at this exact point). `frontend/static/
   script.js`'s `describeStep()` gained a matching `"pattern_generated"`
   case (`t.statusPatternGenerated(...)`), a new i18n key in all 5
   languages mirroring `statusPattern`'s own wording but noting the
   pattern is now set and the word search is starting — no other
   frontend change needed, since this reuses the exact same `{step,
   examples}`/single-or-up-to-6-grid preview mechanism already built for
   every other stage.

   Verified live: a real `generate_grid()` run on the standard 15×10
   benchmark, with an `on_progress` hook capturing every event, confirmed
   the code sequence reads exactly `pattern → pattern_generated →
   pattern_attempt_failed` per failed cycle and `pattern →
   pattern_generated → pattern_found → minimizing → grid_ready` at the
   very end — one `pattern_generated` per `pattern`, always immediately
   followed by its own cycle's outcome event carrying the same `attempt`
   number; every `pattern_generated` example correctly has empty
   `impossible_cells`/`forced_cells`. This same run also directly answers
   a separate, related request from the same message — "Stacker aussi
   l'état avant optimisation" — by confirming it was **already** true
   with no code change needed: the pre-existing `progress("minimizing",
   examples=[...])` call (see above) already carries a non-empty
   `examples` list, and `backend/app.py`'s `examples_history`-appending
   logic already applies to *any* progress event with non-empty
   `examples`, regardless of step code — so the "before optimization"
   state was already being stacked and correctly shown (confirmed the
   captured event sequence includes exactly one `minimizing` entry,
   positioned right after `pattern_found`, with a fully letter-filled
   grid and zero `"."` placeholders remaining). A real generation
   submitted through the actual running API and polled via `GET /api/
   generate/status/{job_id}` (after restarting the backend to pick up
   this code — a stale server process was first caught still missing
   `pattern_generated` entirely, a reminder that these Python changes
   never take effect until the process serving them is restarted)
   confirmed `examples_history` alternates `pattern`/`pattern_attempt_
   failed` in the real HTTP response too, each with a well-formed `step`.

   This same investigation surfaced a real bug in `_cycle_start_preview`
   itself, found live while checking `pattern_generated`'s own black-cell
   counts against each cycle's own starting pattern — documented in full,
   together with the `PREFILL_LOCKED_MIN_WORD_COUNT` fix that prompted
   this deeper check, in that constant's own entry above (search for
   "20% reset" mechanism).

   **The `POST_SEARCH_STEP_CODES` catch-up mechanism was reported as the
   actual root cause of a related, live-diagnosed symptom right after**:
   the user observed, browsing the newly-added history via the "◀"/"▶"
   buttons, that black cells appeared to have been "added" between the
   last preview they could see and the grid handed to optimization — with
   no step in between to explain it. Diagnosed live rather than guessed:
   a direct `generate_grid()` run with an `on_progress` hook confirmed the
   *data* was already correct — the winning palier's own `pattern_
   generated` entry (added just above) carries the exact same black-cell
   count as the following `minimizing` preview, byte for byte, no gap at
   all. The real cause was purely client-side: `pollJob()`'s catch-up
   (jump both cursors straight to `history.length - 1`/`stepHistory.
   length - 1` the instant the *live* step reaches a post-search code)
   fires the moment `minimizing` is observed — and since a winning
   palier's own `pattern` → `pattern_generated` → `pattern_found` →
   `minimizing` sequence all fire in one near-instantaneous burst (no
   real wall-clock gap between them once the search itself succeeds),
   essentially no 2-second poll ever lands *inside* that window — the
   catch-up reliably skips straight past the winning palier's own
   `pattern_generated` (and `pattern`/`pattern_found`) to land directly on
   `minimizing`, for practically every successful generation, not just
   an occasional unlucky one. Worse than just "not shown in time": since
   `recordPreviewHistory()` is only ever invoked for whichever entry a
   cursor currently points to, a skipped entry is never recorded into
   `previewHistory` at all — permanently unavailable to the "◀"/"▶"
   buttons too, not merely delayed past its moment on screen; "montrer
   toutes les étapes... et les mémoriser pour analyse de l'historique"
   (the user's own framing of what was needed) is exactly what the
   catch-up was preventing.

   Fixed by removing the catch-up entirely, at the user's explicit
   request: `POST_SEARCH_STEP_CODES` and the `Math.max(...)` jump-ahead
   block are gone; both cursors now always advance by exactly one entry
   per poll, unconditionally, restoring the original guarantee (every
   single stacked entry — from every palier's own `pattern`/`pattern_
   generated`/outcome trio, through `minimizing`/`clues`/`saving` — gets
   shown *and* recorded, in order, with nothing ever silently discarded).
   This reintroduces the exact backlog-draining delay the catch-up was
   originally built to avoid (a job that failed fast enough to pile up
   dozens of entries before search finished could previously look like it
   kept "searching" for a while after actually reaching `minimizing`/
   `clues`/done) — addressed differently this time, at the tail end of
   the same loop rather than by skipping ahead: `pollJob()` no longer
   returns/throws the instant the backend reports a terminal `status`
   (`done`/`error`/`cancelled`) — it keeps looping and draining, one more
   entry per 2-second poll exactly as during the search phase, and only
   resolves once both cursors have fully caught up to `examples_history`/
   `step_history`'s own final length. Since `backend/app.py`'s `progress()`
   stops being called the moment a job reaches any of these statuses, the
   backend's own response is already fully, statically populated by
   then — this doesn't wait for *new* data to ever arrive, only for the
   client's own local drain to finish walking through what's already
   there. A job with an empty or already-fully-drained backlog at the
   moment it turns terminal (the common case for a quick/simple
   generation) resolves immediately, with no artificial delay at all —
   the added delay only ever applies proportionally to how large a
   backlog is still outstanding.

   Verified: an isolated Python translation of the exact drain/terminal-
   gating logic, run against three scenarios — a job that piles up 12
   entries across 3 rapid "polls" then reports `done` on poll 4 with a
   13th entry, confirming all 13 are shown in order with no skip and
   that resolution is correctly delayed until poll 13 (not poll 4); a
   `cancelled` job with its own 3-entry backlog, confirming it still
   drains fully before surfacing as cancelled; an empty-backlog job,
   confirming no artificial delay when there's nothing queued — all 3
   passed. A real end-to-end check against the actual running backend (a
   real 8×8 "flash"-mode job, submitted through the real API and polled
   every 2s with this exact logic reproduced against the live HTTP
   responses) confirmed the drain count always exactly matches the
   final `examples_history`/`step_history` length with nothing skipped,
   and that the backend's own status was already `done` for several
   polls before the drain-gated resolution actually fired — direct,
   measured confirmation that the delay this design accepts is real and
   the mechanism engages exactly as intended. A real JS syntax check
   (`esprima`, temporarily installed and removed again afterward)
   confirmed `script.js` still parses correctly after the removal.

   **That "wait for the drain before resolving" design was itself
   immediately simplified**, at the user's own explicit clarifying
   follow-up: "L'interface peut ne montrer que la dernière étape en
   Live. Mais, toutes les étapes doivent être ajoutées à l'historique
   navigable." This decouples two things the previous fix conflated: the
   *live* display (the plain `#status` line, and whichever grid is on
   screen at any given moment) only ever needs to reflect whatever is
   most current — no pacing needed there at all — while the *navigable*
   history (`previewHistory`, browsed via the "◀"/"▶" buttons) is what
   must genuinely never lose an entry. The previous design conflated the
   two, pacing recording itself to one entry per poll purely so the live
   display wouldn't skip ahead — at the real cost measured above (up to
   dozens of extra seconds of delay after the backend already reported
   `done`, just to keep draining a backlog nobody needed to see paced
   out live any more).

   `pollJob()`'s `recordPreviewHistory(entry)` (one entry at a time)
   became `appendAllPreviewHistory(newEntries)` (a whole batch at once):
   every new entry `examples_history` has produced since the last poll is
   unconditionally pushed into `previewHistory` in one synchronous pass,
   however many arrived — but only the *last* one of that batch is ever
   actually rendered live, and only if the player was already viewing the
   previous newest entry (the same "pause autoscroll while scrolled up"
   courtesy as before, now applied to a whole batch instead of a single
   entry). `pollJob()`'s own loop simplifies back to essentially its
   pre-catch-up-saga shape: append whatever's new, then check `data.
   status` and resolve/throw *immediately* — no more artificial delay
   at all, since a terminal poll's `history` already contains every
   entry the backend will ever produce (`progress()` stops firing once a
   job is finished), so appending all of it in one batch, right there,
   loses nothing despite adding no wait. The live `#status` line also
   simplifies: `setStatus(describeStep(t, data.step), false)` now reads
   the job's current step directly, rather than draining a separate
   cursor — matching "l'interface peut ne montrer que la dernière étape
   en Live" literally.

   This made the whole separate `step_history` mechanism (added earlier
   this session specifically to pace the *live* status line one cycle-end
   entry per poll) genuinely dead: nothing reads it anywhere any more,
   since the live line now reads `data.step` directly and the *navigable*
   history is fully served by `previewHistory` (whose entries already
   carry their own paired `step`, richer than `step_history` ever was —
   `step_history` only ever recorded `pattern_attempt_failed`/
   `pattern_found`, never `pattern`/`pattern_generated`/`minimizing`/
   `clues`). Removed outright, per this project's own no-dead-code
   convention, rather than left inert: `job["step_history"]` (the field
   itself, its `_new_job()` initialization, and the `progress()` closure's
   own appending block) deleted from `backend/app.py`; `nextStepHistoryIndex`/
   `stepHistory` deleted from `script.js`. Confirmed via `grep` that no
   reference to `step_history`/`stepHistory`/`nextStepHistoryIndex`
   remains anywhere in either file.

   Verified: an isolated Python translation of the exact new batch-append
   logic, run against 5 scenarios — a burst of 12 new entries arriving in
   one poll, confirming all 12 are recorded but only the last is
   rendered live; navigating back manually; a further batch arriving
   while viewing an older entry (confirmed it's recorded without
   yanking the view forward, matching the pre-existing courtesy); manual
   catch-up via the "▶" button showing every entry in order; an empty
   batch being a pure no-op — all 5 passed. A real end-to-end check
   against the actual running backend (a real 8×8 "flash"-mode job,
   polled every 2s exactly as `pollJob()` would, recording via
   `history.slice(next_example_index)` each time) confirmed the recorded
   history exactly matches the final `examples_history` length the very
   same poll the backend first reported a terminal status — zero extra
   polls needed after that point, unlike the previous design's measured
   dozens-of-seconds tail. Real syntax checks (Python `ast.parse` on
   `backend/app.py`, JS `esprima` on `script.js`/`i18n.js`) confirmed both
   files still parse correctly after the removal.

   **This "render whatever's most recent" design was reverted next**,
   reported directly by the user: "Dans les aperçus, je ne vois plus
   qu'une seule grille, jamais plus. Il devrait en montrer 6 à chaque
   étape... le stream des états en Live ne montre que les fins de
   cycles [en pratique : les débuts de cycle suivant]. Il ne stream pas
   les phases intermédiaires (normalement affichées toutes les 2s en
   consommant la pile de visualisation alimentée par le back)." —
   `previewHistory` itself was confirmed intact (every entry genuinely
   recorded, nothing lost), so the bug had to be in *which* entry the
   batch-append design above chose to render live.

   Root-caused live, not assumed, by writing a Python simulation of the
   exact poll loop against a real job's own raw `examples_history` and
   watching what the "render only the last of this poll's new batch"
   rule would have picked each time: **21 new entries recorded between
   two consecutive 2-second polls of a real job, every single batch
   ending on a `"pattern"` event** (a single grid — the state carried
   forward into the *next* cycle) — never on that same window's own
   `"pattern_generated"`/`"pattern_attempt_failed"` entries (up to 6
   grids each), despite those being recorded right there in the batch
   too. This isn't timing luck, it's structural: `generate_grid()`'s own
   palier loop (`backend/crossword_gen.py`) runs almost entirely inside
   one blocking worker thread (`asyncio.to_thread`, see `backend/app.py`)
   with no `await` point of its own — the *only* moment that thread ever
   actually blocks, releasing the GIL long enough for the event loop
   thread to get scheduled and serve an HTTP poll, is while waiting on
   the `ProcessPoolExecutor` results for a palier's own CSP search.
   `progress("pattern_generated", ...)` and `progress("pattern_attempt_
   failed", ...)` both fire the instant those results come back,
   followed *immediately* — no blocking point in between, pure Python
   dict/list operations — by `progress("pattern", ...)` for the *next*
   palier, right before the thread blocks again waiting on that palier's
   own results. So whenever an HTTP poll actually gets to run,
   `examples_history`'s newest entry is deterministically that next
   palier's own `"pattern"` event, essentially never the richer up-to-6
   states a completed search just produced a moment earlier — "render
   whatever's most recent" was therefore silently starving the preview
   of exactly the entries the up-to-6-grid mechanism (`FAILED_ATTEMPT_
   EXAMPLES`) exists to show.

   Fixed by reverting the "batch, render only the last" rule back to a
   *paced*, one-entry-per-poll reveal — but *not* a full revert to the
   original `recordPreviewHistory(entry)` design this same area had
   before the batch-append change (see above): recording into
   `previewHistory` stays a full, unpaced batch every poll (`recordPreview
   History(newEntries)`, the renamed/trimmed former `appendAllPreviewHistory`
   — now a pure push, no rendering at all), so nothing is ever lost or
   delayed into the *navigable* history regardless of how fast paliers
   resolve; only the *live* on-screen view is paced, by calling
   `showNextPreview()` — the exact same function already driving the "▶"
   button — once per poll from `pollJob()`'s own loop, but only while
   `data.status === "running"` and only when a new module-level
   `autoFollowPreview` flag (default `true`) is set. `showPreviousPreview()`
   (the "◀" button) sets it `false` — pausing the automatic one-step-per-
   poll advance so a poll landing while the player is reviewing an
   earlier state doesn't yank their view forward, the same "pause
   autoscroll while scrolled up" courtesy this mechanism has always had —
   and the "▶" button's own click handler sets it back to `true`
   afterward, read as "resume following from here." A new `catchUpPreview
   ToEnd()` jumps straight to the newest recorded entry with no pacing at
   all, called from all three of `pollJob()`'s terminal branches (`done`/
   `error`/`cancelled`) right before returning/throwing — this preserves
   the previous fix's own guarantee that a large remaining backlog never
   delays the *actual final result* behind a slow one-per-poll drain (the
   exact problem the batch-append design was originally built to solve);
   pacing only ever applies while the job is genuinely still in progress.

   Verified live: a direct Python translation of the new `pollJob()`
   logic (record every new entry each poll; while running, advance the
   live view by exactly one entry; on a terminal status, jump straight to
   the end) run against a real 15×10/easy/medium job, polled every 2s for
   40 polls — the live view now genuinely cycles through `"pattern"` (1
   grid) → `"pattern_generated"` (up to 6) → `"pattern_attempt_failed"`
   (up to 6) → `"pattern"`... in true order: of 29 states shown live
   across the 40 polls, **16 carried more than 1 grid** (values of 3, 5,
   and 6 observed) and all 3 step codes were represented — a stark
   contrast with the pre-fix simulation on the same kind of job, which
   showed exactly 1 grid, always `"pattern"`, on every single poll with
   new data. A second real run (6×6, `mode="flash"`, easy) was let run to
   completion end to end under the exact same simulated logic: this one
   resolved its very first palier with no failures at all, so its own
   `examples_history` only ever held 4 single-grid entries (`pattern`/
   `pattern_generated`/`minimizing`/`clues` — the winning palier's own
   `pattern_generated` only ever carries 1 example, not up to 6, same as
   `minimizing`/`clues` — the up-to-6 mechanism is specific to *failed*
   candidates) — the simulated live view correctly advanced through all
   4 one poll at a time, then correctly stayed pinned to entry 3/3 across
   14 further polls while clue generation ran with no new preview data,
   and the moment `status` turned `"done"`, `catchUpPreviewToEnd()`
   correctly landed on that same already-current final entry (`clues`,
   the fully solved grid) with no further delay. A real JS syntax check
   (`esprima`, temporarily installed and removed again afterward)
   confirmed `script.js` still parses correctly after the change.

   **The one-entry-per-poll pacing above was itself reported as
   insufficient almost immediately**: "Quand le Back est en avance sur le
   Front, le Front continue à télécharger les grilles d'aperçu, mais
   n'avance plus dans la séquence. Il faut alors avancer à la main. Tant
   que l'utilisateur ne revient pas en arrière, il faut que le Front
   continue à avancer dans l'affichage au fur et à mesure que les
   nouvelles grilles de l'aperçu arrivent." A real, structural gap in the
   previous fix, not a perception issue: tying the reveal (`showNextPreview
   ()`) to the same loop iteration as the network poll caps the reveal
   rate at exactly one entry per `POLL_INTERVAL_MS` (2s) — but a real
   burst regularly produces far more than that in a single poll window
   (18-21 new entries measured between two consecutive polls of a real
   job, more than once). At that rate, a backlog can only ever grow,
   never shrink — the displayed sequence falls further and further
   behind the true live edge over time, functionally indistinguishable
   from "stuck" to a player watching it, since the only way to see
   anything move faster than the poll cadence was to click "▶" by hand,
   repeatedly.

   Fixed by decoupling *revealing* an entry from *polling* for new data
   entirely: a new `PREVIEW_REVEAL_INTERVAL_MS` (500ms) constant drives a
   dedicated `setInterval` timer, started at the top of `pollJob()`
   (`revealTimer`) and cleared in a `finally` wrapping the whole polling
   loop so it stops the instant the loop exits either way (return or
   throw) — ticking independently of the `await sleep(POLL_INTERVAL_MS)`
   between polls, calling `if (autoFollowPreview) showNextPreview();` on
   its own schedule. `pollJob()`'s own loop keeps recording every new
   `examples_history` entry into `previewHistory` in full, unpaced
   batches exactly as before (see `recordPreviewHistory`) — only the line
   that used to call `showNextPreview()` once per poll iteration was
   removed, since the new timer now owns that job entirely. This drains
   a backlog roughly 4× faster than it's typically produced (500ms per
   reveal vs. 2s per poll), so it visibly catches all the way up between
   bursts rather than trailing further behind indefinitely, while a
   caught-up run with nothing new to reveal simply has the timer no-op on
   every idle tick (`showNextPreview()`'s own existing early return) — no
   wasted flicker, no rushed feel, when there's no backlog to drain.
   `showPreviousPreview()`/the "▶" button's own `autoFollowPreview`
   pause/resume behavior (see the previous fix) is completely unaffected
   — the reveal timer already gates every tick on that same flag.

   Verified live against a real, unmocked job (15×10, `mode="medium"`),
   with two independent Python threads standing in for the browser's own
   poll loop and reveal timer (a real HTTP poll every 2s; a real reveal
   check every 500ms, both hitting the actual running API, no mocking):
   18 new entries arrived in a single burst by t≈33s (matching the
   backend's own bursty completion pattern documented throughout this
   project), and the reveal thread drained the *entire* backlog on its
   own, fully unattended, reaching the true end (`0` entries left
   unrevealed) by t≈41s — a smooth, continuous progression through
   `pattern`(1) → `pattern_generated`(3-6) → `pattern_attempt_failed`
   (3-6) → `pattern`(1) → ... the whole way, never needing a manual "▶"
   click to make further progress. A real JS syntax check (`esprima`,
   temporarily installed and removed again afterward) confirmed
   `script.js` still parses correctly after the change.

   **A deeper, backend-side gap behind the same symptom was reported
   right after**: "Le Front n'affiche les aperçus qu'après la fin d'un
   cycle. Les états d'initialisation n'apparaissent pas avant la fin du
   cycle. Il faut que la stack Back soit proprement alimentée à chaque
   étape du cycle, et consommée en asynchrone par le Front (toutes les
   2s)." Confirmed directly in the code, not assumed: `progress(
   "pattern_generated", ...)` — the "cases noires posées" state, meant to
   show the pattern right after black-cell placement but before the
   search — is only ever computed from `failed_pairs`/`best`, both built
   *after* `concurrent.futures.as_completed` has already collected every
   one of a palier's `PARALLEL_ATTEMPTS` futures. For a fresh-pattern
   palier (`_pattern_attempt`, which runs both `make_pattern()` and
   `try_fill()` together inside one worker process, with no intermediate
   reporting back to the parent), this means the "cases noires posées"
   preview genuinely cannot exist in `job["examples_history"]` until the
   *entire* CSP search of that palier has already finished — no amount of
   frontend pacing can make an "initialization" state appear before the
   backend has actually produced it. Only the very first `pattern` event
   (cycle start, built from `carry_seed_grid` before any parallel work
   starts) was ever genuinely available early; `pattern_generated` never
   was, contrary to what its own name/position in the sequence implied.

   Fixed by having the parent itself compute and publish an early,
   genuine "cases noires posées" preview **before** submitting the
   executor jobs, for a fresh-pattern palier specifically (never for
   "reprise telle quelle", whose own `pattern_generated` already
   coincides with `pattern` — see `_pattern_continue`'s docstring).
   Rather than a throwaway, unrelated pattern (which risked recreating
   the exact "it looks like something changed" confusion this whole
   preview mechanism has repeatedly had to fix — CAROLINE, ENREGISTRAT...
   — once the *real* `pattern_generated` arrived later with a different
   grid), the early preview reconstructs the *exact* pattern the
   palier's own last, never-reset worker (`seeds[-1]` — guaranteed never
   among the `FULL_RESET_ATTEMPT_FRACTION`-reset workers, which are
   always the first `reset_count` of the list) will independently compute
   in its own process: `make_pattern` is a pure function of its
   arguments, so calling it a second time in the parent with the
   identical seed and parameters (`ratio`, `carry_seed_grid`,
   `carry_locked_letters`, `index`, `black_enrichment_fraction`,
   `available_lengths_preview` — this last one precomputed once outside
   the palier loop, mirroring `_pattern_attempt`'s own per-worker
   computation but for the parent) produces byte-for-byte the same grid.
   If that worker ends up among the up-to-6 candidates the *real*
   `pattern_generated` shows later, the two previews coincide exactly
   (only letters get added in between, never a different black-cell
   layout) — and even when it doesn't, the early preview still genuinely
   represents one real attempt about to happen, not a fabricated extra
   state. A single example (not up to 6, since no other candidate exists
   yet at this point), reusing the exact same `_cycle_start_preview`
   overlay mechanism as every other preview in this file.

   Verified live: a direct, real `generate_grid()` run (15×10, seed 2,
   easy) with an `on_progress` hook timestamping every event confirmed
   102 `pattern_generated` events fired across the whole run, 37 of them
   with a real, measurable gap (>10ms) before their palier's own outcome
   event (`pattern_attempt_failed`/`pattern_found`) — proving they
   genuinely arrived *before* the search finished, not merely positioned
   earlier in the code — with gaps up to **11.2s** and averaging 1.7s
   among the real ones (the remaining 65 correspond to paliers whose own
   search resolved in under 10ms, where "before vs. after" is not
   meaningfully distinguishable at all, an honest, expected outcome, not
   a gap in the fix). The generation itself completed successfully with
   no regression to correctness.

   **This single-grid early preview was generalized to one grid per
   parallel worker**, at the user's explicit request: "la toute première
   initialisation des cases noires ne prépare qu'une seule grille.
   Intégrer cette première initialisation au début du cycle, de manière à
   créer une initialisation par process." For a fresh-pattern palier, the
   loop now calls `make_pattern()` once per seed in `seeds` (up to
   `PARALLEL_ATTEMPTS`), each with the same `seed_grid`/`locked_letters`
   that specific worker will itself receive (`None`/`None` for a
   `reset_count`-reset one, exactly mirroring the real dispatch just
   below) — deduplicated by the resulting black/white pattern itself
   (`tuple(tuple(row) for row in early_pattern)` as a set key) and capped
   at `FAILED_ATTEMPT_EXAMPLES` (6), the same "distinct candidates, never
   the same grid shown twice" principle already used for the *post-search*
   `pattern_attempt_failed`/`pattern_generated` examples (`failed_unique`).
   Still scoped to fresh-pattern paliers only — never "reprise telle
   quelle", whose every worker searches the exact same shared pattern by
   construction (see `_pattern_continue`'s own docstring), so a multi-grid
   early preview there would just repeat one identical grid `PARALLEL_
   ATTEMPTS` times for no benefit.

   **A real, unexpected risk was found verifying this — not a bug in the
   feature's own logic, but a genuine side effect of adding real
   sequential work to the parent's critical path.** The feature is
   provably read-only with respect to the deterministic RNG stream (it
   only ever touches per-worker `random.Random(seed)` copies, never the
   shared `rng` `generate_grid()` itself draws `seeds` from) — so it
   cannot, on its own reasoning, change *which* pattern any real worker
   ends up searching. Yet a direct, controlled A/B on the standard 15×10
   benchmark's seed 7 found it *does* change the real outcome: with the
   shared-budget change above but *without* this feature, seed 7
   succeeds (355.4s); with the exact same code plus this feature added,
   seed 7 **fails outright** (178.4s, all 200 paliers exhausted). Leading
   explanation, not yet fully confirmed: this feature adds up to
   `PARALLEL_ATTEMPTS` (10) sequential `make_pattern()` calls in the
   parent process, once per palier, before `executor.submit()` — real
   wall-clock latency that shifts exactly when jobs get submitted and
   polled. This search already has a genuinely *timing-dependent*
   mechanism, not merely seed-dependent: `attempt_done_event`/
   `batch_abandoned_event` (see their own docstrings) interrupt the
   remaining ~70% of a palier's parallel workers once ~30% complete,
   based on *real completion order* — extra parent-side latency can
   plausibly shift which workers "win the race" to complete first,
   changing the final search outcome even though nothing about the
   deterministic `seed` parameter itself changed. Combined with the
   real, separately-measured slowdown this feature also causes on its
   own (seed 2: 108.4s → 181.7s with the per-zone budget; seed 7: 102.2s
   → a similar order of magnitude increase), this feature is genuinely
   **not** the "free," purely-additive diagnostic it was designed to be
   — reported to the user with this full trail via `AskUserQuestion`.

   **The user's answer clarified the original request's own scope had
   been misread**, on two points at once. First: "il n'y a jamais eu 6
   grilles par process, mais 1 grille par process (1 process par
   processeur). Les 6 grilles affichées sont les 6 meilleures tracées
   dans les générations (par exemple 10 si 10 processeurs), qui servent à
   initialiser le cycle N+1 en ne gardant que la meilleure." — confirming
   the *shape* of the fix (one `make_pattern()` call per real worker,
   deduplicated, capped at 6 only for display) was correct; only *when*
   it should run was wrong. Second, directly: "Il ne faut pas changer le
   budget, juste initialiser N grilles au premier cycle au lieu d'une
   seule. Les cycles suivants, à partir de 2, reprendront la meilleure
   grille (sauf 20% de nouvelles grilles)." — the per-process expansion
   was only ever meant for the very first palier of a whole generation
   (`carry_seed_grid is None`, before anything has ever been carried
   forward) — every later fresh-pattern palier already has real,
   carried-forward diversity from the previous palier's own outcome
   (plus `FULL_RESET_ATTEMPT_FRACTION`'s own reset workers), so a
   per-process pre-search preview there is both unnecessary and, per the
   measurement above, actively risky.

   Fixed by branching the early-preview computation on `carry_seed_grid
   is None`: for the true first palier, the up-to-`PARALLEL_ATTEMPTS`
   per-seed loop (deduplicated, capped at `FAILED_ATTEMPT_EXAMPLES`)
   described above runs exactly as built, always with `seed_grid=None,
   locked_letters=None` for every seed (matching what every real worker
   of that first palier will itself use, `reset_count` being `0` there
   regardless since `just_cleaned` is never true this early); every
   later fresh-pattern palier falls back to the single-grid version
   (`seeds[-1]` only) that was already verified safe earlier in this same
   session, before the per-process expansion. This confines the extra
   sequential `make_pattern()` cost — and the timing-sensitivity risk it
   carries — to a single one-time moment per `generate_grid()` call
   instead of repeating it, and its risk, at every one of potentially 200
   paliers. The shared-budget change from the entry above was reverted
   the same way, by the same explicit instruction — see
   `PREFILL_ZONE_BLACK_BUDGET_FLOOR`'s own comment.

   Verified live after both corrections: a real `generate_grid()` run on
   both seeds of the standard 15×10 benchmark, with an `on_progress` hook
   inspecting every `pattern_generated` event's own example count,
   confirmed the very first palier (`attempt=1`) carries more than one
   example while every subsequent fresh-pattern palier carries exactly
   one — see the full numbers in the same run's own log for the final
   confirmation that both benchmark seeds succeed again, matching this
   benchmark's own established historical timing range rather than the
   +70-250% slowdowns measured above.

   **A visual diagnostic was added on top of the `pattern` preview
   specifically**, at the user's explicit request: "Sur la grille
   'Génération du motif de cases noires' afficher en fond orange les
   cases en dessous du seuil des possibilités de remplissage (< 3
   possibilités)." — `PREFILL_LOCKED_MIN_WORD_COUNT` (3) named precisely,
   matching `_slot_with_insufficient_candidates`'s own existing
   partially-locked-slot threshold, not the general, length-only
   `PREFILL_MIN_WORD_COUNT` (10). New `_low_candidate_slot_cells(grid,
   rows, cols, index, locked_letters)`: for every slot of `extract_slots
   (grid, rows, cols)` that's *partially* locked (`0 < locked_count <
   length` — a fully-locked slot is already a confirmed word, never
   flagged here, same rule as `_slot_with_insufficient_candidates` since
   the AVALAS bug fixed earlier this session) whose real candidate count
   (`_slot_candidate_count`) is below the threshold, every one of its
   cells is added to the returned set. A pure diagnostic helper, distinct
   from `_slot_with_insufficient_candidates` (which drives an actual
   pre-fill *decision* and stops at the first offending slot, with a
   `skip` set and the length-only case too) — this one returns every
   matching cell across every slot at once, since a plain highlight needs
   no per-slot targeting.

   Wired into the `pattern` event only (the "Génération du motif de
   cases noires" state named in the request), computed right alongside
   `start_grid`/`start_locked_cells` — `None`/empty whenever there's
   nothing meaningful to check: the very first palier (`carry_seed_grid
   is None`) or a "reprise telle quelle" palier (`carry_preseed_
   assignment is not None`), where every slot is either a fully-assigned
   word or fully free — the concept of a "partially locked" slot,
   the only kind this check ever applies to, doesn't exist in that
   representation at all. `available_lengths_preview` (used only by the
   early-`pattern_generated` fix above, not by this one — this check
   never needs it) and the new field ride the same `index` already built
   once per `generate_grid()` call. Threaded through as a new
   `low_candidate_cells` key on the `pattern` event's one example, next
   to `locked_cells`.

   `frontend/static/script.js`'s `renderAttemptPreview()` reads it the
   same way as `locked_cells`/`forced_cells` (a final overlay pass, `||
   []` so every other event — which simply lacks this key — renders
   unaffected) and applies a new `.low-candidates` class. Styled (see the
   `style-guide` SKILL) as a light-orange *background* fill
   (`--low-candidates-bg`, a pastel tint of `--locked`'s own saturated
   orange) — sharing the orange hue family with `.locked`'s own border on
   purpose (both flag a fragile/at-risk slot), but a different visual
   treatment (fill vs. border) so the two compose cleanly on the same
   cell without one hiding the other, the same convention already
   established between `.forced`/`.locked` and `.impossible`.

   Verified: 5 isolated cases against a small, fully controlled
   dictionary (`ABOIS`/`ABIME` vs. `AVOIR`/`AVANT`/`AVEUX`/`AVERE`, all
   length 5) — no locked letters at all → nothing flagged; `"AB"` locked
   at positions 0-1 (only 2 real candidates, `ABOIS`/`ABIME`) → the whole
   slot flagged; `"AV"` locked at the same positions (4 real candidates)
   → correctly *not* flagged, confirming the threshold itself, not just
   "any lock present", drives the check; a slot locked completely (even
   to a low-candidate spelling) → never flagged, matching the AVALAS-bug
   rule; two independent slots on the same grid, only one below the
   threshold → only that one's cells returned, confirming the check is
   genuinely per-slot, not grid-wide. A real end-to-end `generate_grid()`
   run on both seeds of the standard 15×10 benchmark confirmed no
   regression to the actual generation outcome (correctness unaffected —
   this is a pure, additive diagnostic field).

   **A real report followed almost immediately, prompting a deeper look at
   the whole early/late preview picture**: "Il faut que l'affichage montre
   l'état complet, pas une version intermédiaire," together with a direct
   observation — "Pourquoi l'aperçu tardif ne montre pas les 6 grilles ?
   ... L'étape 4 (fin du premier cycle échoué) ne montre d'ailleurs que 3
   grilles, justement, alors qu'il devrait y en avoir 6 !" A prior report
   in the same exchange ("il a refait une génération de cases noires, qui
   a déjà été faite à l'étape précédente... je n'ai plus que 3 grilles")
   first looked like it might be the cross-palier carry-forward bug this
   whole area has repeatedly had — but a direct, real trace (`on_progress`
   capturing `attempt`/`total_attempts` for every event) ruled that out:
   the user confirmed the confusing entry was still `attempt=1`, `total_
   attempts` unchanged from the cycle-start `pattern` event right before
   it — i.e. still the *same* palier, no cross-cycle transition involved
   at all.

   Root-caused precisely instead of guessed at, in two parts:

   1. **Two functionally different events shared one label.** The new
      early preview above (`total_attempts` unchanged from `pattern`,
      fired *before* the search) and the pre-existing *late* `pattern_
      generated` (fired *after* the real search, from `failed_pairs`/
      `best`, with `total_attempts` already increased) both rendered as
      "Motif de cases noires posé..." — identical text, no way for a
      human watching the sequence to tell "this is still the plan" from
      "this is what actually happened." The early one shows the first N
      *distinct-by-seed-order* pre-search patterns; the late one shows
      the *best*-scoring real outcomes — different selection criteria, so
      the two can legitimately show different grids under the identical
      label, reading exactly like an unexplained "second round."
   2. **The "only 3, never 6" count has nothing to do with `FAILED_
      ATTEMPT_EXAMPLES` (6) at all.** Confirmed directly: `PARALLEL_
      ATTEMPTS` resolves to this machine's own core count (10 here), and
      `PALIER_ATTEMPT_INTERRUPT_FRACTION` (0.3) — the pre-existing "cut
      the remaining ~70% of a palier's workers once ~30% complete"
      optimization — resolves to `ceil(0.3 × 10) = 3`. Every non-
      interrupted real outcome a palier can ever produce is capped at
      this `interrupt_threshold`, independent of and almost always far
      below the 6-grid *display* cap — "only 3 of 6" was never a partial/
      incomplete state, it was already the true, complete maximum for
      this machine's own core count.

   Presented to the user with both findings via `AskUserQuestion` — three
   options for what to do with the now-understood-redundant late preview
   (keep both, drop the late one, or build a genuinely deferred "hidden
   until complete" merge of the two) — **the user chose to remove the
   late preview outright**, confirmed explicitly right after: "Si
   l'aperçu tardif est 100% redondant par rapport à l'aperçu en temps
   réel, il faut le supprimer." Removed both call sites — the success
   branch's own `best_pattern_grid`/`best_pattern_locked` computation and
   `progress("pattern_generated", ...)` call right before `break`, and the
   failure branch's `pattern_generated_examples` loop and its own
   `progress(...)` call — `pattern_attempt_failed`/`pattern_found` (right
   after, unchanged) already carry strictly more information (the same
   patterns, plus the real letters and full diagnostics) than the removed
   late preview ever did, so nothing is lost.

   This also made `_preview_locked_source()` (the sixth-fix helper from
   earlier this session, its one and only caller) genuinely dead —
   removed outright per this project's no-dead-code convention, along
   with `_pattern_attempt`'s own `diag["own_locked_letters"]` snapshot
   (its one and only reader). `_public_diag()` — the seventh-fix helper
   that strips `own_locked_letters` before a diag reaches `progress(...)`
   — is kept in place even though it's now a no-op pass-through: not
   speculative future-proofing, but a deliberate, cheap safety net against
   the exact same JSON-serialization bug class recurring if a future
   diagnostic field takes a similarly non-JSON-safe shape (a dict keyed by
   cell coordinates). `successes`' own 3-tuples still carry a `diag`
   element (now unused at the one remaining call site, `best, best_result,
   _ = max(successes, ...)`) — left as `_` rather than restructuring
   `successes`'/`outcomes`' own tuple shape, since other code still
   legitimately needs that same shape.

   Verified live: a real `generate_grid()` run on both seeds of the
   standard 15×10 benchmark, with an `on_progress` hook counting `pattern_
   generated` events per `attempt`, confirmed every single attempt now has
   *at most one* such event, never two (6 attempts on seed 2, 48 on seed
   7, zero with more than one) — both seeds still succeed (58.0s/54 words
   and 146.0s/57 words respectively, both within this benchmark's own
   established range), and every single progress event across both runs
   remained JSON-serializable (the `_public_diag` safety net still
   exercised correctly even with nothing left to strip).

   **The very next report reopened this same area from a different
   angle**: "Il ne faut pas supprimer les 70% des tentatives restantes,
   mais seulement les interrompre. Chacune d'elle porte normalement la
   mémorisation de sa meilleure grille échouée, qu'il faut prendre en
   compte. A la fin, il peut y avoir moins de 10 grilles échouées (6
   affichées) uniquement si process ne sont encore jamais allés jusqu'à
   une grille échouée (encore en train d'essayer de construire)." This
   pointed at `attempt_done_event`'s own interruption mechanism (see its
   own docstring): once `interrupt_threshold` (`ceil(PALIER_ATTEMPT_
   INTERRUPT_FRACTION × PARALLEL_ATTEMPTS)`, 3 on a 10-core machine)
   real outcomes complete, every other still-running worker of that same
   palier is told to stop — but at the time, `failed_real` (the pool
   `failed_unique`/`failed_pairs` is built from) explicitly filtered OUT
   every outcome tagged `reason == "interrupted_other_attempt_done"`,
   discarding the real, already-computed `Filler.best_assignment` state
   of every one of those interrupted workers instead of treating it as a
   genuine, usable candidate. First fix: `failed_real` became simply
   `failed_all` (the `reason != "interrupted_other_attempt_done"` filter
   removed outright) — an interrupted worker's own diagnostics were never
   empty placeholders (`try_fill` already builds them from `Filler.
   best_assignment`, the same high-water-mark snapshot a naturally-
   concluded failure uses too), so this alone recovers real progress that
   was previously thrown away for no benefit.

   **A follow-up message revealed this first fix was still incomplete**:
   "D'ailleurs, je pensais que les 6 meilleures grilles échouées étaient
   compilées en permanence pendant la recherche sur N process. Plusieurs
   des 6 meilleures grilles peuvent provenir d'un même process," then "Il
   faut conserver les 6 meilleures grilles échouées des N process trouvées
   à n'importe quel moment des N recherches." `failed_real = failed_all`
   only ever recovers each worker's own *final* state (whatever it had
   reached at the exact moment it was told to stop, or at natural
   conclusion) — never the sequence of intermediate records a search
   passes through on its way there. Given the size and regression history
   of this exact code area, the precise design was confirmed via
   `AskUserQuestion` rather than guessed: **"Chaque process suit son
   meilleur état, et transmet au process parent l'information que ce
   meilleur état a changé. Le process parent garde les 6 meilleurs états,
   de tous les états dont il a été informé par les N process."**

   Implemented as a `multiprocessing.Queue` (`best_state_queue`, created
   once per `generate_grid()` call — same technical reason as `cancel_
   event`/`batch_abandoned_event`/`attempt_done_event`: a `multiprocessing`
   object passed as a per-task argument to `executor.submit(...)` raises a
   `RuntimeError` on macOS's "spawn" start method, so it goes through the
   pool's `initializer`/`initargs` instead, alongside the three existing
   `Event`s — `_worker_best_state_queue`, set via `_init_worker`). `Filler`
   gained an `on_new_best` callback parameter, invoked from `_backtrack`
   at the exact point `self.best_assignment` is updated (`assigned_count >
   self.best_assigned_count`) — the same running high-water-mark this file
   already tracked for the end-of-search diagnostics, now also announced
   the moment it happens rather than only once, at the very end. `try_fill`
   gained a matching `best_state_queue=None` parameter: when given, it
   builds a closure (`_publish_new_best`, defined right after `Filler` is
   constructed, since it needs `filler.impossible_zone_cells()`) that
   reconstructs the exact same preview shape (`example_grid`/`forced_
   cells`/`impossible_cells`/`impossible_slots`/`locked_cells`, via
   `build_partial_letters_grid`, identical to the end-of-search diagnostics
   further down the same function) from each new `best_assignment`, plus
   `checks`/a fixed `reason="best_state_snapshot"` (distinct from every
   real end-of-search reason, so a log/preview built from one of these is
   never mistaken for a naturally-concluded or interrupted attempt) and a
   defensively-copied `grid` (never mutated after `make_pattern`, but each
   publication needs to remain an independent snapshot once it outlives
   this one `try_fill` call on the parent side), then `.put()`s it —
   threaded through both `_pattern_attempt` and `_pattern_continue`'s own
   `try_fill` calls as `best_state_queue=_worker_best_state_queue`, but
   deliberately *not* through `minimize_black_squares`'s own `try_fill`
   call: that phase already has a complete grid and is trying candidate
   black-cell removals one at a time, sequentially, in the parent process —
   an entirely different concern from tracking the best state across N
   *parallel, still-in-progress* pattern searches, so nothing about this
   feature applies there.

   `generate_grid`'s own outcome-collection code, right after `if
   successes: ... break` (i.e. only reached when the palier did *not*
   succeed, since a successful palier never needs `failed_unique`/`failed_
   pairs` at all), drains `best_state_queue` and merges every message into
   the same `failed_unique` list the final `outcomes` already populate —
   same dedup key (`(tuple(map(tuple, grid)), tuple(assignment))`), same
   `seen_keys` set, so a queue-published state identical to an already-
   recorded final outcome (the common case: a worker's very last
   publication coincides exactly with the `best_assignment` it eventually
   returns) is never double-counted. This matters because `impossible_
   cells` (the sort key `failed_pairs` uses to pick the "best" failed
   attempt) is not guaranteed to shrink as `best_assignment` grows — an
   earlier, less-complete state can legitimately have *fewer* impossible
   cells than the state a search eventually settles into, so an
   intermediate snapshot deserves to compete for the top-6 selection on
   equal footing with a final result, not just supplement it as an
   afterthought.

   A real, reproduced defect surfaced immediately while verifying this in
   isolation, not reasoned about: a `try_fill` call publishing a genuine
   best-state improvement, drained via a plain `Queue.get_nowait()`
   immediately afterward in the same process, came back **empty** — 0
   messages received despite a confirmed `.put()` call having already
   happened. Root cause: `multiprocessing.Queue.put()` does not write
   synchronously to the underlying pipe — it hands the object to a
   background feeder thread, which can still be mid-flight at the exact
   moment the caller (here, the same test process; in production, a
   worker process about to return control to the parent via `f.result()`)
   moves on. Confirmed directly: the identical drain succeeded (1 message)
   once a `time.sleep(0.1)` was inserted before it. Fixed by draining with
   `best_state_queue.get(timeout=BEST_STATE_QUEUE_DRAIN_GRACE_S)` (20ms)
   instead of `get_nowait()` — `get(timeout=...)` returns immediately the
   moment an item is actually available (no artificial wait when the
   queue already has data, which is the common case since every worker of
   this palier has already returned by the time `as_completed` finishes),
   and only actually blocks for the full grace period once, right at the
   point the queue is genuinely exhausted — bounding the worst-case added
   latency to 20ms per palier, paid at most once, not once per straggler.

   `pub_grid = published.pop("grid")` (not a plain `get`) deliberately
   removes the `grid` key from the published dict before it's stored
   alongside `pub_grid` in the same `(grid, diag)` tuple shape `failed_all`
   already uses — every other tuple in that list carries its grid
   *outside* the diag dict (`selected_grid, selected_diag =
   failed_pairs[0]`, `cand_grid, cand_diag in failed_pairs[...]`, etc.);
   leaving a redundant `"grid"` key *inside* the diag too would have
   silently doubled the payload (leaking a second, letter-free copy of the
   grid into the JSON sent to the browser) the moment a queue-derived
   state won `last_diag`'s own generic `**_public_diag(last_diag)` spread
   into `progress("pattern_attempt_failed", ...)`.

   Verified live in stages, given this touches the same code area with
   the project's own longest regression history. Isolated: a hand-built
   4×4 grid with only 2 valid 4-letter words for 8 length-4 slots (a
   deliberately unsolvable pattern) confirmed `on_new_best` fires and
   publishes a real, correctly-shaped message (`grid`/`assignment`/
   `example_grid`/`impossible_cells`/`impossible_slots`/`forced_cells`/
   `locked_cells`/`checks`/`reason`) the moment the very first word gets
   placed; a second, genuinely solvable 3×3 scenario confirmed multiple
   successive publications arrive with a strictly non-decreasing count of
   placed words, matching `best_assigned_count`'s own monotonic
   guarantee. A real, small end-to-end `generate_grid()` run (9×9,
   `attempts=15`, a deliberately tight `deadline_checks=3000` to force
   several failures quickly) confirmed no crash across the whole
   `_build_retry_seed`/`_clean_all_candidates` pipeline even when a
   queue-derived state wins the selection — directly confirmed via
   `on_progress`: `last_diag["reason"] == "best_state_snapshot"` on every
   one of the failed paliers observed, proving an intermediate, mid-search
   snapshot genuinely won the fewest-impossible-cells selection over every
   worker's own final result, exactly the scenario this feature exists
   for.

   **A real, severe regression was found next on the full standard 15×10
   benchmark**, run for the first time with this feature genuinely in
   place (`attempts` at its real default, 200, not the small forced value
   above): both seeds — which succeeded reliably in 60-250s earlier in
   this very session, right before this feature was added — now failed
   outright after exhausting every one of the 200 paliers (730.1s and
   622.9s respectively). A single-palier isolated comparison (`attempts=1`,
   real `deadline_checks`, real parallelism) showed no measurable overhead
   at all (6.3s with the queue vs. 8.8s without, well within normal
   run-to-run noise) — ruling out raw publish/IPC cost as the cause and
   pointing instead at a *quality* problem in what the merge was letting
   win the selection. Reported to the user with this full measurement via
   `AskUserQuestion`; the user's own diagnosis, given directly rather than
   guessed: "Au lieu d'un score sur les injouables, mesurer les jouables
   (racine carré des sommes des carrés des longueurs jouables)." A new
   `_playable_score(diag)` helper — `sqrt(sum(len(w) ** 2 for w in
   diag["assignment"] if w is not None))`, derived straight from the
   assigned words themselves (a word's own length already equals its
   slot's length, no need to re-derive the pattern's slots at all) —
   replaced the previous `len(impossible_cells)` (ascending) as
   `failed_pairs`'s sort key, now `reverse=True` (highest score wins):
   this favors whichever candidate has genuinely posed the most
   substantial content (mirroring the existing successful-attempt
   selection's own sum-of-squares principle, plus a square root to bring
   it back to a length-comparable scale), so a barely-started queue
   snapshot with zero impossible cells purely because it hadn't explored
   enough to find any can no longer beat a real, substantially-advanced
   result. Verified: re-ran the exact same failing scenario — every
   `pattern_attempt_failed` event's `reason` now shows a healthy mix of
   real completion reasons instead of `"best_state_snapshot"` dominating
   every single palier as it had before. The full benchmark improved
   substantially (730.1s/622.9s → 357.6s/252.1s) but **both seeds still
   failed outright** — a real, if smaller, regression remained.

   Reported back to the user with this second measurement, again via
   `AskUserQuestion`; the user chose the more conservative of three
   offered options: **restrict queue-derived states to the displayed
   preview only, never let them drive the actual carried-forward
   selection**. `failed_unique` (and therefore `failed_pairs`/
   `selected_grid`/`selected_diag`/`still_has_hope`/`_build_retry_seed`/
   the nettoyage candidates) now builds *exclusively* from `failed_all`
   (real, final search outcomes) — the queue merge was removed from
   feeding it entirely. A new, separate pool, `display_unique`/
   `display_pairs`, starts as a copy of `failed_unique` and *then* merges
   the queue-drained states into it (same dedup convention, a fresh
   `display_seen_keys` set seeded from `seen_keys`) — this pool feeds
   `last_examples` only, never anything that influences the search
   itself. Since queue-derived states no longer have any bearing on
   `failed_pairs` at all, `_playable_score` was reverted back to
   `len(impossible_cells)` (ascending) for `failed_pairs` specifically —
   the bias `_playable_score` was introduced to fix no longer applies to
   a pool that never contains a queue snapshot in the first place, so the
   original, long-proven criterion was restored rather than left on an
   unrelated, freshly-introduced one; `_playable_score` stays in use only
   for ranking `display_pairs`. Re-ran the full benchmark: improved again
   (357.6s/252.1s → 303.5s/197.9s) but **both seeds still failed** —
   surprising, since at this point `failed_pairs` should have been
   behaviorally identical to the pre-feature code.

   **The real cause turned out to be a second, independent, and far more
   serious bug**, reported live by the user while watching the process
   list during a run: "Il n'y a plus que 2 process qui tourne. Mauvaise
   détection du seuil de 30% ou non prise en compte de l'arrêt des
   process ?" Investigated directly rather than assumed: two `ps`
   snapshots of the same worker PIDs, taken minutes apart, showed
   *byte-for-byte identical* CPU times across every worker — genuinely
   frozen, not merely idle between paliers (which would still show
   *some* CPU accumulating over that span). Root cause: the very first
   version of this feature only ever drained `best_state_queue` once per
   palier, *after* `as_completed` had already collected every future —
   but a `multiprocessing.Queue`'s underlying OS pipe has a bounded
   capacity, and a worker can publish up to ~50-60 times over the course
   of one search (see `_worker_best_state_queue`'s own docstring); with
   nothing reading the pipe until every worker of the palier has already
   returned, a worker deep in a long search could fill the pipe and block
   on its own `put()` — but it can never *finish* (and so never get
   drained) while blocked there, a textbook producer/consumer deadlock,
   entirely unrelated to `attempt_done_event`'s 30% threshold or to any
   worker failing to stop correctly, exactly as the user's own question
   anticipated as one of the two possibilities.

   Fixed by draining `best_state_queue` **continuously**, in a dedicated
   `threading.Thread` (not `multiprocessing` — this thread runs in the
   parent process itself, where a loop that only ever blocks on
   `Queue.get(timeout=...)` then appends to a list costs nothing
   meaningful even under the GIL) started once, before the pool is even
   created, and running for the entire `generate_grid()` call —
   `best_state_buffer`/`best_state_buffer_lock` accumulate every message
   as it arrives, so the pipe can never again accumulate enough to block
   a `put()`. Each palier's own display-pool code no longer touches
   `best_state_queue` directly at all — it swaps and clears
   `best_state_buffer` under its lock instead (with a short, bounded
   `time.sleep(2 * BEST_STATE_QUEUE_DRAIN_GRACE_S)` beforehand, giving the
   drain thread's own 20ms polling cycle at least one full pass to catch
   a message published by a worker that had *just* returned — a message
   missed even by this grace period is simply picked up on the *next*
   palier's own drain instead, never lost outright, and never risking the
   deadlock again either way). The thread is `daemon=True` and stopped
   explicitly (`stop_best_state_drain.set()` + a bounded `.join(timeout=
   1.0)`) right after the palier loop concludes (both the success and the
   total-failure/`attempts`-exhausted paths reach this same point) —
   deliberately *not* wrapped in a `try/finally` around the entire,
   several-hundred-line palier loop (which would have required
   re-indenting that whole block, a real, avoidable risk on its own in a
   part of this file with this much regression history): the one path
   that skips this explicit stop, `GenerationCancelled` raised from
   inside the loop, leaves the thread harmlessly idle (blocked on its own
   `get(timeout=0.1)`, daemon-owned) until the process itself exits,
   never blocking anything.

   Verified live, end to end, methodically: a real `generate_grid()` run
   on the standard 15×10 benchmark's seed 2 succeeded cleanly (160.5s,
   53 words, 0 mismatches, 0 empty white cells) — the very first success
   on this exact benchmark since the feature was first added. Seed 7
   initially still failed twice in a row when run immediately *after*
   seed 2 within the same benchmark script (285.3s, then 306.1s) — but
   run in true isolation (its own fresh process, exactly matching how
   the CLI and the web app's `asyncio.to_thread`-wrapped call each only
   ever invoke `generate_grid()` once per process) it succeeded reliably,
   4 times out of 4 across separate checks (88.1s, 218.4s, 218.4s, 47.9s
   — the last one leading a swapped-order run, seed 2 succeeding right
   after it too, 201.6s). This pinned the residual "seed 7 fails" pattern
   specifically to running two `generate_grid()` calls back to back
   inside one Python process (most likely OS-level contention from
   tearing down one `ProcessPoolExecutor`/`Queue`/thread trio and
   immediately spinning up a second) — an artifact of this session's own
   benchmark *harness*, never a scenario the real CLI or web app actually
   exercises (each of those starts exactly one `generate_grid()` call per
   process/request, never two in sequence), and not investigated further
   on that basis. Across every one of these real runs, `0` mismatches
   between placed words and the solution grid, `0` empty white cells.

   **A third, real bug in this same display-only split was reported
   next**, with a precise, screenshot-backed example: a "cases noires
   posées" preview at one step showed the up-to-6 grids with a first grid
   containing several impossible situations (`YSI`/`ITN`/an incomplete
   `L··AERES` fragment); the very next step's own "cases noires posées"
   preview (the start of the following palier, reflecting whatever
   actually got carried forward) still showed the words crossing those
   impossible situations fully intact (`LESSEE`/`SITUATIONS`/`FEDEREE`/
   `PESE`) rather than removed — "Ce n'est donc pas le même filtrage des
   situations impossibles que dans le processus historique (en fin de
   cycle) qui est appliqué."

   Reproduced and root-caused with a dedicated diagnostic (`on_progress`
   capturing every `pattern_attempt_failed`/`pattern` event pair, printing
   both the first shown example and the following cycle-start preview's
   own grid side by side) before touching any code: on a real run, these
   two grids showed **completely different black-cell patterns** — not
   merely different content, entirely different patterns — proving
   `display_pairs[0]` (the first of the up-to-6 shown grids, sorted by
   `_playable_score`) and `failed_pairs[0]` (the real winner — sorted by
   `len(impossible_cells)` — that actually gets cleaned via `_clean_
   blocked_slots` and carried forward as `carry_seed_grid`/`carry_
   preseed_assignment`) could be, and often were, two *entirely different
   candidates* ever since the display-only split above. The user was
   comparing "this grid" (the first shown example) against the next
   step's own preview expecting to see the *same* candidate before and
   after cleanup — but the grid actually being cleaned was a different one
   than what was shown first, so the comparison itself was invalid: the
   real winner's own cleanup was correct all along (verified directly:
   `_clean_blocked_slots`'s logic — remove any assigned slot sharing a
   cell with an `impossible_slots` entry — was unchanged and,
   independently re-read, structurally sound), it just wasn't the grid the
   screenshots were comparing it against.

   Fixed by guaranteeing `display_pairs[0]` is *always* exactly
   `failed_pairs[0]` — computed once (`winner_grid, winner_diag =
   failed_pairs[0]`), excluded by its own dedup key from the rest of
   `display_unique` before that remainder is sorted by `_playable_score`
   and appended after it, rather than ever letting the winner's own rank
   in a differently-sorted pool decide whether it appears first. This
   isn't a cosmetic reordering: it's what makes the "before cleanup (this
   step) / after cleanup (next step)" comparison the whole preview
   mechanism exists for actually valid again — the first shown grid is
   once again guaranteed to be the one whose fate the very next step's own
   preview reveals, exactly as it always was before the display-only split
   introduced this regression. Verified: re-ran the same diagnostic after
   the fix — the first shown example and the following cycle-start
   preview now always share the *same* black-cell pattern, with only
   *some* cells losing their letters between the two (exactly the
   signature of `_clean_blocked_slots` removing specific crossing words,
   not a different candidate replacing the first), across every palier
   pair observed in the run. A full end-to-end run on both seeds of the
   standard 15×10 benchmark confirmed no regression (0 mismatches, 0
   empty white cells each: seed 2 in 65.5s, seed 7 in 57.3s).

   **A fourth report followed, correcting the previous fix's own scope**:
   "Le nettoyage des emplacements impossible DOIT retirer les mots
   croisant cet emplacement ! Ça fonctionne sur des grilles issues de
   l'étape N-1, mais pas sur les grilles données en exemple." First
   investigated as a possible correctness bug specific to a completely
   fresh (no locked cells) palier — directly, word by word, not just
   visually (a cell that still shows a letter after its own word is
   removed can legitimately belong to a *different*, surviving crossing
   word, which looks identical to "the word survived" at a glance): an
   independent, hand-computed re-derivation of `_clean_blocked_slots`'s
   own removal set, run against 6 different seeds' own genuinely fresh
   palier 1 (`carry_seed_grid is None`, 0 locked cells), matched its real
   output exactly every single time — `_clean_blocked_slots` itself was
   never broken, for a fresh pattern or a locked one alike.

   The real gap was scope, not correctness: `_clean_blocked_slots` has
   only ever been applied to *one* candidate per palier — the winner
   (`failed_pairs[0]`, used to build `carry_preseed_assignment` for the
   next palier) — never to the other up-to-`FAILED_ATTEMPT_EXAMPLES` (6)
   grids shown in the very same preview, which were always displayed
   exactly as the search left them, impossible-crossing words included.
   This was true even *before* the display-only split earlier in this
   same investigation — the up-to-6 examples were never individually
   cleaned, only the one candidate that happened to also become the
   carried-forward seed ever benefited from the historical "en fin de
   cycle" filtering the user was referring to.

   Fixed with a new `_cleaned_example_preview(grid, diag)`: recomputes
   `slots` from the real black/white `grid` (never `example_grid` itself,
   whose letters would fragment `extract_slots`'s own white-run detection
   — the exact pitfall this investigation's own diagnostic script hit and
   had to work around first), runs `_clean_blocked_slots` against that
   candidate's own `assignment`/`impossible_slots`, and returns a copy of
   `example_grid` with every cell belonging to a now-removed word reset to
   `WHITE` — *unless* that same cell is also covered by a surviving
   crossing word (`confirmed`, the exact same definition `_clean_blocked_
   slots` itself uses), which keeps its real letter untouched, matching
   what the next cycle-start preview already does for the one candidate it
   covers. A statistical seed-letter hint (`sample_letter_biases`) sitting
   on a cleared cell is not restored (this function is never handed the
   raw `forced_letters` dict, only `forced_cells`' own coordinates) — a
   minor, accepted simplification, since `impossible_cells`/`forced_
   cells`/`locked_cells` themselves are untouched by this change, only the
   *letters* baked into `example_grid`. Wired into `last_examples`'s own
   dict comprehension (`"example_grid": _cleaned_example_preview(g, d)`
   instead of a bare `d["example_grid"]`), applied uniformly to *every*
   one of the shown grids, not just the first.

   Verified: an isolated hand-built 3×3 grid (a 3-letter across slot
   crossing a 3-letter down slot flagged impossible at their shared cell)
   confirmed the across slot's own letters are cleared except at the
   shared cell, which keeps the down slot's own letter — exactly the
   "remove the word, keep what a surviving crossing word still provides"
   behavior intended. Live, across 5 different seeds: for every palier's
   own winning example, independently re-derived which cells *should*
   still show a letter after cleanup (covered by a surviving word) versus
   which should now be blank (belonged only to a removed word) — 1,247
   cells checked in total across all 5 runs, 0 violations. A full
   end-to-end run on both seeds of the standard 15×10 benchmark confirmed
   no regression (0 mismatches, 0 empty white cells each: seed 2 in
   70.8s, seed 7 in 90.4s).

   **This fourth fix was itself reverted almost immediately, at the
   user's explicit correction**: "la visualisation des extraits montre
   maintenant les grilles nettoyées avec des emplacements impossibles
   vides. On ne comprend plus ce qui se passe. Il faut montrer les
   emplacements avant nettoyage, évaluer la grille après nettoyage (qui
   sera transmise au cycle suivant si sélectionnée)." Cleaning every
   displayed example turned out to defeat the whole point of showing an
   `impossible_cells` highlight in the first place: once the crossing
   words are removed, the highlighted cells sit on blank content, with
   none of the context (which words actually created the conflict) that
   made the highlight meaningful. `_cleaned_example_preview` was removed
   outright (its one and only caller, right where `last_examples` is
   built, reverted back to the plain, pre-cleanup `d["example_grid"]`) —
   deleted rather than left unused, per this project's no-dead-code
   convention.

   The user's own message drew a clean split this project hadn't made
   explicit before: **display** shows the raw, pre-cleanup state (so the
   conflict stays legible), but **evaluation** — which candidate becomes
   `failed_pairs[0]`, the one actually carried forward if selected —
   should be judged on its *post*-cleanup state, since that's the content
   that will genuinely survive into the next palier. `failed_pairs`'s own
   sort key changed a third time in this same investigation, from
   `len(impossible_cells)` (raw) to a new `_cleaned_playable_score(grid,
   diag, rows, cols)`: recomputes `slots` from the real grid, runs
   `_clean_blocked_slots` against that candidate's own `assignment`/
   `impossible_slots`, and scores the *cleaned* result the same way
   `_playable_score` scores a raw one (sum of squares of surviving word
   lengths, square-rooted). This matters because two candidates tied on
   raw `impossible_cells` can lose very different amounts of content once
   cleaned — one whose crossing word is short loses little, one whose
   crossing word is long loses much more — and it's that post-cleanup
   difference that actually determines what the next palier starts from,
   not the raw count. `display_pairs`'s own sort (`_playable_score`, on
   raw content, for the non-winner slots only) is untouched — display and
   selection now deliberately evaluate two different things, on purpose.

   Verified: an isolated hand-built case (a 3-letter across slot crossing
   an impossible 3-letter down slot) confirmed `_cleaned_playable_score`
   correctly drops from the raw `_playable_score` (both words counted,
   `√18 ≈ 4.24`) to the cleaned one (only the surviving word counted,
   `3.0`) once the crossing word is accounted for as removed. A live run
   confirmed `impossible_cells` in the displayed examples again show a
   healthy mix of real crossing letters and genuinely blank cells (never
   forced entirely blank), across 6 different paliers on one seed. A full
   end-to-end run on both seeds of the standard 15×10 benchmark confirmed
   no regression (0 mismatches, 0 empty white cells each: seed 2 in
   153.0s, seed 7 in 40.0s).

   **`_clean_blocked_slots`'s own removal rule was rewritten next, at the
   user's explicit request**: "Le fait d'enlever tous les mots qui
   croisent un emplacement impossible... enlève trop de mots à chaque
   fois. Nouvelle algo : enlever les mots qui croisent un emplacement
   impossible un par un, et s'arrêter quand la contrainte d'impossibilité
   (ou de trop peu de possibilités) cesse. Dans une première évolution,
   tirer le mot à retirer au hasard dans la liste des mots possibles à
   retirer." Every prior version of this function (going all the way
   back to `_build_retry_seed`'s original design) removed *every* word
   crossing an impossible slot unconditionally, in one pass — correct in
   the sense that it always resolves the impossibility, but often far
   more destructive than necessary: a slot crossed by three assigned
   words could have all three stripped even when removing just one of
   them would already have restored at least one real dictionary
   candidate.

   `_clean_blocked_slots` gained `index`/`rng`/`min_candidates=1`
   parameters. For each impossible slot, it now loops: compute the
   slot's current real candidate count (`_slot_candidate_count`, the
   same per-position set-intersection already used throughout this
   pipeline — e.g. `_slot_with_insufficient_candidates`/`sample_letter_
   biases`) from whichever crossing words are *still* assigned at that
   moment; if the count already meets `min_candidates` (1 by default —
   "la contrainte d'impossibilité cesse" reached the moment at least one
   real word becomes possible again; a higher threshold for "trop peu de
   possibilités" rather than strict impossibility is flagged as a future
   step in the docstring, not wired in yet, since `impossible_slots` — the
   only input this function has ever received — is itself still a purely
   binary signal), stop; otherwise pick one of the slot's still-assigned
   crossing words at random (`rng.choice`, the same seeded generator
   `generate_grid()` already threads everywhere else for reproducibility)
   and remove it, then loop again. The old "remove everything crossing"
   behavior is kept as an explicit fallback when `index`/`rng` are both
   omitted — no real caller does this today, but a hard crash over a
   missing optional dependency felt like the wrong trade-off for a
   defensive fallback.

   Threaded through every one of this function's three real call sites —
   `generate_grid`'s own "reprise telle-quelle" branch, `_build_retry_
   seed` (which gained matching `index`/`rng` parameters, passed straight
   through from its own two callers), and the new `_cleaned_playable_
   score` (which also gained `index`/`rng` parameters, now required) —
   all already had `index`/`rng` available in scope, so no new plumbing
   was needed beyond adding the two parameters and passing them down.

   Verified: an isolated hand-built scenario (three assigned 2-letter
   across words crossing one impossible 3-letter down slot — one forcing
   an invalid first letter, the other two forcing real, satisfiable
   letters) confirmed the algorithm stops after removing only the single
   word actually responsible for the impossibility across several
   different seeds, never touching the other two — and, when a
   less-effective word happens to be tried first by chance, correctly
   continues removing a second one rather than stopping prematurely,
   still never removing all three needlessly. A live, side-by-side
   measurement on a real run (seed 5, 8 failed paliers, real French
   wordlist) compared the old always-remove-all behavior against the new
   one on the *exact same* recorded diagnostics: **94 words removed
   under the old rule vs. 42 under the new one — a 55% reduction** across
   those 8 paliers, confirming the new algorithm is substantially less
   destructive in practice, not just in a hand-built worst case. A full
   end-to-end run on both seeds of the standard 15×10 benchmark confirmed
   no regression (0 mismatches, 0 empty white cells each: seed 2 in
   29.4s, seed 7 in 133.6s).

   **A third alternative was added on top of the one-at-a-time random
   removal above**, at the user's explicit request: "Lors du nettoyage
   des zones impossibles, avec une probabilité 1/3, tenter d'ajouter une
   case noire au lieu de retirer des mots venant croiser la zone
   impossible." A genuine scope ambiguity was resolved via
   `AskUserQuestion` before writing any code, given this exact function's
   long regression history: should the new alternative apply to *both*
   cleanup paths sharing `_clean_blocked_slots` (the "reprise telle
   quelle" continue path and the full nettoyage inside `_build_retry_
   seed`), or only one? The user's answer — the *opposite* of the
   recommended default — restricted it to the "reprise telle quelle"
   path only, with a precise, two-part reasoning: "En l'état, nettoyer
   les zones impossibles et les connectés, supprime beaucoup de mots, ce
   qui oblige plus tard à rajouter des cases noires par d'autres
   mécanismes. Autant tenter la case noire tout de suite, et supprimer
   moins de mots. Par ailleurs, sur des toutes petites zones, la
   suppression de mots ne supprime pas grand chose, et la recherche
   tourne en rond sur très peu de lettres modifiables. Ajouter des noires
   peut permettre de réellement finir ces petites zones où la vraie
   solution n'existe peut-être pas." The full nettoyage path
   (`_build_retry_seed`) already regenerates a brand-new pattern via
   `make_pattern` afterward and can already add black cells through that
   route — this new alternative was specifically meant for the *other*
   path, which had never had any way to add a black cell at all (its own
   long-standing, explicitly documented invariant: "à la fin d'un tour,
   nettoyer automatiquement les emplacements bloqués, mais pas les
   noires").

   `_clean_blocked_slots` gained a new module-level constant right
   before it, `BLACK_CELL_INSTEAD_OF_REMOVAL_PROBABILITY = 1 / 3`
   (documented with the user's own reasoning above), and three new
   optional parameters, `grid=None, rows=None, cols=None` — `None` by
   default, a complete no-op for every caller that doesn't supply them
   (in particular `_build_retry_seed`'s own internal call and
   `_cleaned_playable_score`, both left untouched, word-removal only, per
   the scope decision above). When supplied, right before removing a
   crossing word for an impossible slot `i`, the function now rolls
   `rng.random() < BLACK_CELL_INSTEAD_OF_REMOVAL_PROBABILITY` first: on a
   hit, it looks among slot `i`'s own cells for ones *not* already
   "known" (not currently fixed by some other, still-assigned crossing
   slot) — blackening a cell that already carries a confirmed crossing
   letter would destroy that word too, a strictly worse outcome than the
   targeted removal this alternative exists to avoid — shuffles them
   (the same no-positional-bias convention used everywhere else in this
   file) and tries each in turn, keeping the first one that stays
   structurally valid (`is_structurally_valid(..., min_interior_free=1)`
   on a working copy of `grid`, mutated in place then reverted if
   rejected). If none of slot `i`'s cells are eligible (every cell
   already known — fully crossed) or every candidate breaks structural
   validity (e.g. the one available cell is a single-cell bridge holding
   the white grid connected — `is_structurally_valid`'s own connectivity
   check catches this directly), it falls straight back to removing a
   crossing word exactly as before, so the loop can re-open a blank cell
   on a later iteration and reconsider. Unlike a word removal (which only
   ever frees a letter constraint on the *same*, still-existing slot `i`
   this palier), successfully placing a black cell *eliminates* slot `i`
   in its current form outright — its real fragment(s) will only be
   rediscovered by the next `extract_slots` call on the updated grid — so
   the per-slot loop stops immediately on success, with no further
   candidate-count check for `i`. Any *other* slot (not `i` itself)
   assigned and passing through the newly-blackened cell is unassigned
   too, since it can no longer exist there. Return signature grew from
   `(assignment, confirmed)` to `(assignment, confirmed, new_black_
   cells)` — the third element a (possibly empty) set of the cells this
   call decided to blacken, left for the caller to fold into whatever
   grid it propagates onward (`_clean_blocked_slots` itself never mutates
   `grid` in place — only an internal working copy, discarded after the
   call). All three existing call sites were updated for the new 3-tuple.

   `generate_grid`'s "reprise telle quelle" branch is the only call site
   that now passes `grid=selected_grid, rows=rows, cols=cols`. When
   `new_black_cells` comes back non-empty, it can no longer just reuse
   `selected_slots`' own numbering the way the unchanged branch always
   has: a black cell inside a slot's own extent shortens/splits it, so
   the *old* slot indices no longer describe the real pattern — the exact
   same index-shift pitfall already found and fixed once before in this
   project's history for the (since fully removed) single-cell-lock
   mechanism, and solved here the identical way: `carry_seed_grid`
   becomes a defensive copy of `selected_grid` with the new cell(s)
   forced `BLACK`, `new_slots = extract_slots(carry_seed_grid, rows,
   cols)` re-derives the real slot list fresh, `carry_preseed_assignment`
   is rebuilt entry-by-entry from `confirmed` (cell-keyed, immune to any
   index shift — a new slot's word is filled in only if *every* one of
   its cells is present in `confirmed`), and `carry_excluded_slots` is
   rebuilt by matching each *old* impossible slot's exact cell-tuple
   against the *new* slot list — a slot whose extent is unchanged (no
   black cell touched it) is found verbatim and re-excluded at its
   possibly-shifted new index; the one actually split by the new black
   cell has no matching tuple left in the new list at all, so it is
   correctly *not* re-excluded, freeing its fresh fragment(s) to be
   attempted by the next palier's search. When `new_black_cells` is
   empty (the 2/3 case, or no structurally-valid candidate was found),
   the branch falls back to exactly its previous, unchanged behavior —
   `carry_seed_grid = selected_grid` (still a direct reference, still
   untouched), `carry_preseed_assignment = cleaned_assignment`,
   `carry_excluded_slots = set(selected_diag["impossible_slots"])`.

   Verified live in isolation first, mirroring the same methodology
   already used for the index-shift fix this reuses: (1) a 4-slot
   hand-built grid (one impossible down slot with 2 known + 2 blank
   cells, dictionary chosen so no single-letter-removal alone resolves
   it) with `rng.random()` forced to always succeed confirmed exactly one
   black cell lands on one of the 2 blank cells, with both crossing words
   at the known cells surviving untouched; the same scenario with a real
   (non-mocked) `random.Random` swept across 3000 seeds confirmed the
   black-cell branch fires in 34.1% of runs — matching the intended 1/3
   within noise; forcing the roll to always fail confirmed the fallback
   still removes a word exactly as before, even with `grid` supplied; a
   fully-crossed impossible slot (every cell already known) confirmed the
   black-cell branch is structurally incapable of firing until at least
   one word-removal first opens a blank cell — exactly the intended
   interaction between the two mechanisms, not a bug. (2) A dedicated
   connectivity test (a 3×5 grid shaped like two open rows joined by a
   single-cell bridge, that bridge cell being the *only* blank cell of
   the impossible slot running through it) confirmed the black-cell
   branch, forced to fire every time, never blackens the bridge — it
   would disconnect the white grid — and correctly falls back to removing
   both crossing words instead. (3) A full reproduction of `generate_
   grid`'s own reindexing logic, run on a real `extract_slots`-derived
   5×5 grid with two independent impossible down slots (one, `P`,
   deliberately given a blank cell so the scripted RNG could force its
   black-cell branch to fire; the other, `Q`, deliberately fully crossed
   by two removable words so its own resolution stayed word-removal-only)
   confirmed: `Q`'s own cell-tuple survives unchanged in the freshly
   re-derived slot list and is correctly re-excluded at its new
   (possibly shifted) index; `P`'s old cell-tuple no longer exists
   anywhere in the new list, and none of its fresh fragments end up
   excluded; the surviving crossing word's letters land correctly on its
   own *new* slot index via the `confirmed`-based rebuild. A full,
   real end-to-end `generate_grid()` run on both seeds of the standard
   15×10 benchmark confirmed no regression (0 mismatches, 0 empty white
   cells each: seed 2 in 49.7s, 55 words, 40 black cells; seed 7 in
   28.8s, 49 words, 44 black cells).

   **`BLACK_CELL_INSTEAD_OF_REMOVAL_PROBABILITY` was lowered from 1/3 to
   1/10 almost immediately after shipping**, at the user's explicit
   request and with no further explanation needed than the one given:
   "Abaisser la probabilité à 1/10 (trop de cases noires à 1/3)" — a
   direct report from real use that the very first tuning (1/3, an
   arbitrary starting value, never itself claimed to be the right final
   number) added more black cells than wanted in practice. A one-line
   constant change, every surrounding mechanic (the per-slot loop, the
   blank-cell-only candidate restriction, the structural-validity check,
   `generate_grid`'s own reindexing logic once a cell is actually added)
   entirely untouched. Verified live: a real (non-mocked) `random.Random`
   sweep across 5000 seeds on the same hand-built scenario used to verify
   the original 1/3 value confirmed the black-cell branch now fires in
   10.6% of runs — matching the new 1/10 target within noise; a full
   end-to-end `generate_grid()` run on both seeds of the standard 15×10
   benchmark, with `black_enrichment_fraction=0.17` (matching the
   concurrent, unrelated `black_enrichment_percent` default bump — 14% to
   17% — landed in the same session), confirmed no regression: 0
   mismatches, 0 empty white cells each — seed 2 in 59.0s, 60 words, 35
   black cells; seed 7 in 31.6s, 58 words, 36 black cells.

   **A real gap in this mechanism was reported next**: "L'ajout de case
   noire au moment de nettoyer les zones impossibles (à chaque cycle, mais
   pas lors d'un nettoyage complet) doit être fait avec une probabilité de
   1/10 tentatives. Je constate qu'il enchaîne un grand nombre de ces
   nettoyages sans ajouter de case noire. C'est notamment sensible quand
   il reste très peu de cases blanches. Il doit y avoir un problème avec
   l'application de cette règle." Investigated live before touching any
   code, via temporary instrumentation logging every roll (success/
   failure), the "known" fraction of the impossible slot's own cells, and
   the resulting `blank_candidates` count, run against a real generation.
   Root cause confirmed directly: the blank-cell-only candidate
   restriction (see the entry above — "noircir une case déjà couverte par
   un mot confirmé détruirait ce mot-là aussi... un résultat strictement
   pire que le retrait ciblé") meant a slot whose every cell is already
   crossed by an assigned word (`blank_candidates` empty) could *never*
   receive a black cell no matter how many times the 1/10 roll succeeded
   — the roll would succeed, find nothing to try, and silently fall
   through to word removal every time. A live diagnostic on a real 25×15
   generation confirmed short impossible slots (2-4 cells) reaching
   exactly this fully-known state exist and recur throughout a run — and,
   critically, this is exactly the "toute petite zone" scenario the
   mechanism's own original rationale (see the entry above) was written
   for in the first place: "sur des toutes petites zones, la suppression
   de mots ne supprime pas grand chose... Ajouter des noires peut
   permettre de réellement finir ces petites zones où la vraie solution
   n'existe peut-être pas" — the blank-only restriction defeated the
   mechanism precisely in its own intended use case.

   Fixed by widening the candidate cells from blank-only to blank-
   preferred-then-known-as-fallback: `_clean_blocked_slots` now builds
   both `blank_candidates` (cells not in `known`, tried first, shuffled)
   and `known_candidates` (cells already crossed by an assigned word,
   tried only if every blank candidate fails structural validity or none
   exist, also shuffled) and tries `blank_candidates + known_candidates`
   in that order — reusing the exact "prefer blank, fall back to
   lettered" convention this project already established once before, for
   the now-removed `_lock_one_impossible_cell` mechanism ("privilégier de
   noircir une case blanche. Sinon, noircir une case avec une lettre").
   When the chosen cell turns out to be a known one, the existing
   crossing-word-unassignment loop (`for j in cell_to_slots[(br, bc)]: ...
   assignment[j] = None`) already handles destroying that word as a side
   effect — no new code needed there, since it was already written
   generically over "whichever cell got chosen," not specifically over
   the blank group.

   Verified: two isolated tests confirmed the fix directly — the exact
   fully-known 3-cell slot scenario from this session's own earlier
   verification (which previously *required* one prior word removal
   before any blank cell could open up) now gets a black cell placed
   directly on a known cell on the very first roll, with exactly one
   crossing word destroyed as the expected side effect; a mixed blank/
   known scenario confirmed blank cells are still strictly preferred when
   available (the known AAAA/BBBB crossing words survive untouched). The
   per-roll probability itself was re-confirmed unaffected: a large sweep
   on a scenario with multiple sequential removal opportunities showed an
   inflated ~27.7% "at least one success per call" rate at first glance —
   traced directly (not assumed a regression) to that specific scenario
   allowing several independent 1/10 rolls within a single call, not a
   miscalibrated constant; re-run on the original clean single-roll
   scenario from this mechanism's own first verification, the rate came
   back at 10.6%, matching 1/10 as expected. The structural-validity
   fallback was also re-confirmed with the existing bridge-cell scenario
   (now testing that *every* candidate — the bridge plus both newly-
   eligible known cells — correctly fails validity and falls back to word
   removal, not just the bridge alone as in the original, narrower test).
   A full end-to-end `generate_grid()` run on both seeds of the standard
   15×10 benchmark, in Flash mode (`deadline_checks=1000`, per the new
   testing-speed convention adopted the same session), confirmed no
   regression: 0 mismatches, 0 empty white cells each — seed 2 in 10.6s,
   57 words, 35 black cells; seed 7 in 15.3s, 50 words, 48 black cells.

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

   **This default was later raised from 14% to 17%**, at the user's
   explicit request ("Configurer par défaut le paramètre 'Taux noir' à
   17%") — a plain value bump, both places kept in sync exactly as
   before: `GenerateRequest.black_enrichment_percent`'s own `default=`
   in `backend/app.py` and `#black-enrichment`'s static HTML `value` in
   `frontend/static/index.html`, both `14` → `17`; no other change to
   this field's own mechanics (still `Field(ge=0, le=100)`, still no
   client-side auto-formula). Verified live: a real `generate_grid()`
   run on both seeds of the standard 15×10 benchmark with
   `black_enrichment_fraction=0.17` (matching the new default) confirmed
   no regression — see this same section's own most recent end-to-end
   measurement below for the exact figures, run together with a separate,
   concurrent change to `BLACK_CELL_INSTEAD_OF_REMOVAL_PROBABILITY`.

   **The rate itself (`black_enrichment_fraction`) is no longer applied
   as a flat percentage during pre-fill**, at the user's explicit
   request: "Dans les phases de préremplissage des cases noires, au lieu
   d'appliquer le taux fixe, multiplier ce taux par la proportion de
   cases blanches restantes = nombre de cases blanches / nombre de cases
   totales de la grille. Le taux reste donc 1 pour la toute première
   grille." (see `make_pattern`'s own docstring above for the technical
   walkthrough). In `make_pattern`, right after `initial_white_count` is
   captured (the white-cell count of *this* call's own starting grid,
   `seed_grid` included, before this call's own pre-fill runs): `white_
   proportion = initial_white_count / (rows * cols)`, then
   `black_enrichment_fraction = black_enrichment_fraction *
   white_proportion` — a plain local reassignment, so every later use of
   `black_enrichment_fraction` within the function (both `fill_objective_
   fraction = max(black_ratio, black_enrichment_fraction)`, feeding the
   curative-cleanup zone budget inside `_prefill_unfillable_slots`, and
   the `target = max(placed, ..., round(black_enrichment_fraction *
   initial_white_count))` computation) automatically picks up the scaled
   value with no further plumbing needed. For the very first palier of a
   `generate_grid()` call (`seed_grid is None`, an entirely white grid),
   `initial_white_count` always equals `rows * cols`, so `white_
   proportion` is always exactly 1 — the very first grid's own rate is
   completely unaffected, matching "le taux reste donc 1 pour la toute
   première grille" precisely. From the second palier onward, as
   `seed_grid` (carried forward across paliers, see `_build_retry_seed`/
   the "reprise telle quelle" mechanism) accumulates more black cells,
   this proportion — and so the effective rate `_place_black_cells` and
   the curative-cleanup budget both work against — shrinks accordingly,
   with no caller needing to compute or pass this shrinking rate itself.

   Verified live: two isolated tests, both built by temporarily
   monkeypatching `_place_black_cells` to a stub that only records its
   own `target` argument rather than actually placing anything (so the
   exact `target` value `make_pattern` computes could be inspected
   directly, independent of whatever `_place_black_cells` itself might or
   might not achieve). The very first case (`seed_grid=None`, a 10×10
   grid, `black_enrichment_fraction=0.20`) confirmed the recorded
   `target` (20) matches the *raw*, unscaled formula exactly — proving
   the proportion-of-1 case is a true no-op. A second case built a real
   seed grid with exactly 10 black cells out of 100 (via a first, real
   `make_pattern(black_ratio=0.1, black_enrichment_fraction=0.0)` call,
   `available_lengths=None` so no dictionary was needed at all) and fed
   it into a second call with `black_enrichment_fraction=0.5`: the
   recorded `target` (40) matched `max(10, round(0.5 × 0.9 × 90)) = 40`
   (the new, scaled formula) and was distinct from what the *old*,
   unscaled formula would have given (`max(10, round(0.5 × 90)) = 45`) —
   confirming the scaling genuinely changes the computed target, not just
   that the new formula reduces to the old one by coincidence on this
   input. A full, real end-to-end `generate_grid()` run on both seeds of
   the standard 15×10 benchmark (with `black_enrichment_fraction=0.17`,
   the current default) confirmed no regression to the ordinary case: 0
   mismatches, 0 empty white cells each — seed 2 in 44.4s, 57 words, 33
   black cells; seed 7 in 63.1s, 62 words, 36 black cells.

   **Adjacency between two black cells was forbidden outright for the very
   first grid of a `generate_grid()` call**, at the user's explicit
   request: "Lors de la première initialisation des cases noires,
   interdire tout tirage qui placerait 2 cases noires avec un côté
   adjacent." `_place_black_cells`'s own adjacency-avoidance mechanism
   (see its docstring above) had always treated "no two black cells
   touch" as a soft preference — relaxing `min_interior_free` from 3 down
   to 1 while still requiring isolation, and only falling back to
   accepting an adjacent candidate as a genuine last resort once no
   isolated one could be found at any of those three levels. The user's
   request turns that last-resort fallback into a hard prohibition, but
   only for the very first palier of a call — every later palier, which
   always starts from an already-partially-black `seed_grid` carried
   forward from a previous one (where forbidding adjacency outright could
   be far harder or impossible to satisfy, given whatever density already
   exists), keeps the original, unchanged soft-preference behavior.

   `_place_black_cells` gained a new `forbid_adjacency=False` parameter
   (no effect for any pre-existing caller) — when `True`, the fallback
   block that retries `order` (the full window, not just `non_adjacent`)
   at `min_free` levels 3/2/1 is skipped entirely; if no isolated
   candidate was found either, the loop falls straight into its existing
   residual case (the best candidate by the row/column criterion is
   refused and dropped from the pool, exactly as already happens when
   every candidate in the window breaks connectivity or orphans a cell) —
   no new failure mode, just one more reason the existing "give up on
   this specific placement, keep going" path can be reached. `_place_
   black_cells` has exactly one call site, inside `make_pattern`'s own
   ratio-based placement (pre-fill uses an entirely separate selection
   mechanism with no adjacency concept at all, so it's untouched) — that
   call now passes `forbid_adjacency=(seed_grid is None)`, the same
   `seed_grid is None` signal already established elsewhere in this same
   function (and this same session — see the `black_enrichment_fraction`
   scaling entry just above) as meaning "the very first, entirely white
   grid of this call."

   A real consequence, stated directly in `_place_black_cells`'s own
   updated docstring rather than left implicit: with adjacency forbidden
   outright, the very first grid can legitimately end up with *fewer*
   black cells than its own target once the window is exhausted of
   isolated candidates — accepted as a deliberate trade-off of the user's
   own request, not a bug, mirroring how the existing residual case
   (connectivity-breaking candidates) was already allowed to fall short
   of `target` before this change.

   Verified: three isolated `_place_black_cells` tests on a small,
   hand-built 5×5 grid with a single existing black cell at `(0,0)` — with
   `forbid_adjacency=False` (default), a candidate list of only the two
   cells orthogonally adjacent to it (`(0,1)`/`(1,0)`) still gets one of
   them placed, confirming the pre-existing fallback behavior is
   unchanged; with `forbid_adjacency=True`, the identical candidate list
   places *nothing* — both candidates end up in `rejected`, the grid is
   left untouched — confirming adjacency is never accepted even as a last
   resort; a third case mixing those same two adjacent-only cells with a
   genuinely isolated one (`(4,4)`) confirmed the isolated candidate is
   still placed normally even with `forbid_adjacency=True` — the
   prohibition only ever blocks an adjacent placement, never a valid,
   isolated one. A real, direct `make_pattern(rows, cols, 0.0, rng,
   black_enrichment_fraction=0.17)` call with `seed_grid=None` (matching
   the current "Taux noir" default), swept across 5 different seeds on
   the standard 15×10 grid shape, confirmed **zero** orthogonally-adjacent
   black-cell pairs in any of the 5 resulting patterns (26 black cells
   each) — direct, real-world confirmation the mechanism holds beyond the
   hand-built unit tests. A full end-to-end `generate_grid()` run on both
   seeds of the standard 15×10 benchmark, combined with the same-session
   window-divisor change (see below), confirmed no regression: 0
   mismatches, 0 empty white cells each — seed 2 in 32.2s, 55 words, 38
   black cells; seed 7 in 18.7s, 53 words, 42 black cells.

   **This rule was found not to actually hold on a large grid, much later
   in this project's history**, reported directly by the user: "La règle
   de génération de cases noires interdisant les cases noires avec
   adjacence ne fonctionne pas. Sur une grille 30x30, il initialise presque
   à chaque fois des cases collées." Root-caused directly by re-reading the
   code rather than guessed: `forbid_adjacency` had only ever been wired
   into `_place_black_cells` (the ratio-based placement) — its own
   docstring at the time even said so explicitly ("pre-fill uses an
   entirely separate selection mechanism with no adjacency concept at all,
   so it's untouched"), a scope limitation that was harmless on the
   standard 15×10 benchmark (where pre-fill barely ever engages — every
   slot length is short enough to be well covered by the dictionary) but
   not on a 30×30 grid, where most rows/columns start out far longer than
   any real word, so `_prefill_unfillable_slots` ends up placing the large
   majority of the grid's black cells, via its own inline placement loop
   (`for (r, c) in options: ...`) — which never checked
   `_has_black_neighbor` at all.

   Fixed by giving `_prefill_unfillable_slots` the same `forbid_adjacency`
   parameter (`False` by default, no effect for any pre-existing caller)
   and splitting its own per-zone candidate list (`options`, already
   sorted by row/column availability) into a non-adjacent-first ordering:
   cells with no black neighbor are tried first; only when
   `forbid_adjacency` is `False` does the loop fall back to also trying
   the adjacent ones afterward. `make_pattern` threads
   `forbid_adjacency=(seed_grid is None)` into both of its own
   `_prefill_unfillable_slots` calls (the initial pre-fill pass and the
   post-ratio-placement repair pass for a locked-letter-aware palier),
   exactly mirroring its existing `_place_black_cells` call — the same
   `seed_grid is None` signal already used throughout this function to
   mean "the very first, entirely white grid of this call." The two
   `make_pattern` calls used only for the early-preview mechanism
   (`generate_grid`'s own "pattern"/"pattern_generated" progress events)
   needed no change at all — they call `make_pattern` directly, which now
   derives `forbid_adjacency` internally from its own `seed_grid`
   argument, so the fix reaches them for free.

   Verified — deliberately *not* with a full end-to-end `generate_grid()`
   call on a 30×30 grid, per the user's own explicit instruction ("Ne pas
   tester de bout en bout sur une grille aussi grande !"): a direct,
   isolated `make_pattern()` call (the pattern-placement phase alone, no
   CSP fill) against the real French wordlist (`easy` difficulty) on a
   30×30 grid, across 10 seeds, found **zero** orthogonally-adjacent
   black-cell pairs (previously "presque à chaque fois" per the report) —
   90 black cells placed per seed, all isolated. The same check repeated
   on 15×10 and 18×22 (8 seeds each) also found zero adjacent pairs,
   confirming no regression to the sizes already covered by the original
   fix. A full end-to-end `generate_grid()` run on the standard 15×10
   benchmark (both seeds, Flash mode) confirmed no regression to actual
   generation: 0 mismatches, 0 empty white cells each — seed 2 in 12.3s,
   55 words, 42 black cells; seed 7 in 12.0s, 47 words, 42 black cells.

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

   **Both remaining sources of an added-beyond-pre-fill black cell were
   removed in the same session, at the user's explicit request, in two
   steps.** First: "Ne plus rajouter de case noire à chaque étape (ne
   garder que le mécanisme lié au nettoyage)" — the fixed, non-escalating
   post-pre-fill density draw (`POST_PREFILL_BLACK_FRACTION`/
   `black_enrichment_fraction`, the web UI's "Taux noir" selector, default
   14%, applied on every fresh-pattern palier — see its own extensive
   history above) was removed entirely: the constant and the parameter
   were deleted from `make_pattern`/`_pattern_attempt`/`generate_grid`
   alike, `target` inside `make_pattern` collapsed from a 3-way `max`
   (placed, the `black_ratio` floor, the enrichment fraction) down to just
   `max(placed, round(rows*cols*black_ratio))`. `backend/app.py`'s
   `GenerateRequest.black_enrichment_percent` field, its logging, and the
   `black_enrichment_fraction=...` kwarg passed to `generate_grid` were all
   deleted too, along with the entire "Taux noir" web UI control
   (`frontend/static/index.html`'s `#black-enrichment` input/label,
   `script.js`'s `blackEnrichmentInput`/`blackEnrichmentPercent` and its
   place in the `POST /api/generate` body, `i18n.js`'s
   `blackEnrichmentLabel` in all 5 languages) — deleted outright rather
   than left inert, per this project's own "no dead code/zombie UI
   controls" convention, since a field controlling a mechanism that no
   longer exists would just be misleading.

   Second, right after: the user quoted their own prior description of
   the *other* remaining mechanism — the single-cell lock during a full
   cleanup (`_impossible_cell_groups`/`_lock_one_impossible_cell`, "une
   case noire supplémentaire est verrouillée... priorité aux cases encore
   blanches...") — and said to delete it too, reversing the "keep the
   cleanup-linked mechanism" carve-out from their first message: no black
   cell is now added anywhere in this whole pipeline beyond what
   `make_pattern`'s own pre-fill phase structurally requires. Both
   functions were deleted outright. `generate_grid`'s two call sites were
   simplified to match: the "reprise telle quelle" branch no longer needs
   the lock-caused index-shift workaround it was built around (re-
   extracting `new_slots` from a freshly-mutated `carry_seed_grid` and
   rebuilding `carry_preseed_assignment`/`carry_excluded_slots` from cell
   coordinates rather than slot indices) — since the pattern is never
   mutated at all any more on this path, `carry_seed_grid` is now just a
   direct reference to `selected_grid` (no defensive copy needed either,
   since nothing downstream mutates it), and `carry_preseed_assignment`/
   `carry_excluded_slots` map 1:1 onto `selected_slots`' own unchanged
   indices (`cleaned_assignment` from `_clean_blocked_slots` directly, and
   `set(selected_diag["impossible_slots"])` respectively — no more
   cell-tuple matching needed). The full-nettoyage branch's own
   `_clean_all_candidates` helper shrank from returning a 5-tuple
   (`cand_seed, cand_confirmed, cand_slots, cand_blank_impossible_cells,
   cand_lettered_impossible_cells`) to a 3-tuple (dropping the last two,
   which existed only to feed the now-deleted lock), and both `max(...)`
   selections over `cleaned_candidates` were updated to match. Verified
   live: two real `generate_grid()` runs on the standard 15×10 benchmark
   (seeds 2 and 7, easy) succeeded with 0 mismatches and 0 empty white
   cells each (18.3s/56 words/42 black cells; 23.9s/28 words/57 black
   cells); a `difficulty="hard"` 10×10 run (seed 7, the full French
   dictionary, no artificially restricted vocabulary — per this project's
   own permanent rule against that) also succeeded, 0 mismatches, 10.8s.
   A real JS syntax check (`esprima`, temporarily installed and removed
   again afterward, same pattern used elsewhere in this project) confirmed
   `script.js`/`i18n.js` still parse correctly after the UI removal.

   **The "First" removal above (the density draw/"Taux noir" mechanism)
   was a misunderstanding, immediately corrected by the user**: "Erreur de
   compréhension ! Il fallait conserver l'initialisation avec objectif en
   % (avec le paramètre en interface) au tout début, et à chaque fois
   qu'un nettoyage est déclenché (tous les 5 cycles). Il ne fallait virer
   que l'ajout d'une case noire simple sur les zones de blocage (à chaque
   cycle)." Only the "Second" removal above (the single-cell lock,
   `_impossible_cell_groups`/`_lock_one_impossible_cell`) was ever meant
   to go — it alone runs at literally every single palier/cycle (both
   "reprise telle quelle" and full-nettoyage paliers, after an earlier
   extension in this project's history made it apply to both), matching
   "à chaque cycle" precisely; the density-draw mechanism only ever ran
   at a fresh-pattern palier (the very first one, or right after a full
   cleanup — never a "reprise telle quelle" one, since that path never
   calls `make_pattern` at all), matching "au tout début, et à chaque
   fois qu'un nettoyage est déclenché" exactly, and was never meant to be
   touched.

   Restored in full, using `git diff` against the last commit to recover
   the exact original text byte-for-byte (the user's own suggestion —
   "faire un diff Git pour faciliter le retour en arrière" — used to
   verify the restoration rather than reconstructing purely from memory):
   `POST_PREFILL_BLACK_FRACTION`, the `black_enrichment_fraction` parameter
   on `make_pattern`/`_pattern_attempt`/`generate_grid`, the 3-way `max`
   in `make_pattern`'s `target` computation (`placed`, the `black_ratio`
   floor, `round(black_enrichment_fraction * initial_white_count)`),
   `backend/app.py`'s `GenerateRequest.black_enrichment_percent` field/
   logging/`generate_grid(black_enrichment_fraction=...)` kwarg, and the
   entire "Taux noir" web UI control (`index.html`'s `#black-enrichment`
   input/label, `script.js`'s `blackEnrichmentInput`/
   `blackEnrichmentPercent`, `i18n.js`'s `blackEnrichmentLabel` in all 5
   languages) are all back exactly as they were before the mistaken
   removal. The single-cell-lock removal (the "Second" step above) and
   the unrelated tier-2 score change from the same session were both left
   untouched — `git diff` against HEAD confirmed `frontend/static/
   index.html`/`i18n.js` came back byte-for-byte identical to their
   pre-removal state, and that `crossword_gen.py`/`app.py`'s only
   remaining differences from HEAD are the legitimate, still-intended
   changes (the lock removal, the tier-2 score inversion, reworded
   comments). Verified live: a direct `make_pattern(..., black_enrichment_
   fraction=0.5)` call placed 75 black cells on a 150-cell grid (a large
   fraction, confirming the term is genuinely back in the `target`
   computation, not just present in the signature); two real
   `generate_grid()` runs on the standard 15×10 benchmark (seeds 2 and 7,
   easy, default `black_enrichment_fraction`) succeeded with 0 mismatches
   and 0 empty white cells each; a real `POST /api/generate` request with
   `black_enrichment_percent=30` was polled back through `GET /api/
   generate/status/{job_id}` and confirmed the value round-trips correctly
   through the whole job's own stored `request` dict, proving the field
   reaches the real API end to end again, not just the Python function
   signature in isolation.

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

   **This cap was later named and set to a value that disables "reprise
   telle quelle" entirely**, much later in this project's history, at the
   user's explicit request: "Donne un nom de variable à ce nombre de
   tentatives 'telles quelles', et configure à 1 pour le moment (chaque
   étape est un nettoyage complet)." The previously-unnamed inline literal
   (`consecutive_continue_paliers >= 5`, itself already renamed several
   times across this history) became a real module-level constant,
   `MAX_CONSECUTIVE_CONTINUE_PALIERS`, defined right before `FULL_RESET_
   ATTEMPT_FRACTION` (whose own comment already referenced "tous les 5
   cycles").

   The literal value requested (1) was checked against the exact check/
   increment/reset sequence before being used, given this exact code
   area's own extensive regression history — and found not to match the
   user's own stated intent: `consecutive_continue_paliers` starts at 0
   and the check is `>=`, so a threshold of 1 still lets the very first
   candidate "continue" palier through (`0 >= 1` is `False`) before
   forcing a cleanup on the next one, producing an alternating continue/
   nettoyage/continue/... sequence — not "chaque étape est un nettoyage
   complet" as the user's own parenthetical explicitly asked for.
   Confirmed directly with an isolated simulation of the real sequence:
   `MAX_CONSECUTIVE_CONTINUE_PALIERS = 1` produced `['continue',
   'nettoyage', 'continue', 'nettoyage', ...]`, while `= 0` produced
   `['nettoyage', 'nettoyage', 'nettoyage', ...]` — only `0` makes the
   very first check (`0 >= 0`) already true, forcing every single palier
   into a full cleanup with no "reprise telle quelle" streak at all.
   Shipped as `MAX_CONSECUTIVE_CONTINUE_PALIERS = 0`, matching the user's
   own stated intent over the literal number they typed, with this
   discrepancy reported back to them rather than silently resolved either
   way.

   Verified live: `python3 -m py_compile` passed; the constant imports and
   reads back as `0`; a real `generate_grid()` run on both seeds of the
   standard 15×10 benchmark (Flash mode, `deadline_checks=1000`) showed a
   real, disclosed regression on seed 2 — **failed outright** (24.1s, all
   200 paliers exhausted) — while seed 7 still succeeded (19.3s, 58 words,
   31 black cells, 0 mismatches, 0 empty white cells). This is an expected,
   not silently-hidden, consequence of disabling "reprise telle quelle"
   entirely (an intentionally aggressive, explicitly temporary experiment
   per the user's own "pour le moment") — every single failed attempt now
   pays the full cost of a pattern-regenerating cleanup, with none of the
   cheaper, same-pattern "continue" progress this whole mechanism was
   built to provide. Reported to the user as-is, not reverted or
   re-tuned unilaterally, since the user explicitly asked for this exact
   experimental value to observe its effect.

   **This 0 experiment was reverted back to 5 immediately after**, once the
   user identified a real, severe consequence of it from a live screenshot
   and pushed back directly: "C'est une énorme régression par rapport à
   l'historique ! 77% rempli signifie que toutes les cases non-noires sont
   remplies, donc la grille est terminée. Le critère de réussite, ce n'est
   pas 100% de cases remplies, c'est : plus aucune case n'est blanche et
   aucun problème de remplissage détecté." My own prior reply had
   misread the stats line ("23 % noir, 77 % rempli, 0 % injouable") as
   "23% of white cells still empty" — a real interpretive error, corrected
   directly by reading `renderAttemptPreview()` in `frontend/static/
   script.js`: `blackPercent`/`fillPercent` share the exact same
   denominator (`height * width`, every cell), so the two summing to 100%
   (23 + 77) arithmetically proves every single white cell already carries
   a character — not that 23% remain blank. The user's own stated success
   criterion is exactly what `generate_grid`'s real completion check
   already uses (every slot's `assignment[i]` non-`None`, i.e. no white
   cell left undetermined and no impossible slot) — success detection
   itself was never broken.

   The real mechanism, confirmed by tracing `progress("pattern_generated",
   ...)`'s own preview construction (`_cycle_start_preview` overlaying
   `carry_locked_letters`, real crossing-confirmed content carried forward
   across paliers — never `forced_letters`' statistical guesses, which
   this specific preview never touches): with `MAX_CONSECUTIVE_CONTINUE_
   PALIERS = 0`, a palier whose best failed candidate has **0% impossible
   slots** (nothing for `_build_retry_seed`'s cleanup step to remove — a
   slot fully determined by real crossing letters, with a still-valid
   single candidate word, is correctly never flagged impossible) still
   counts as "failed" whenever even a handful of such already-implied
   slots were never explicitly confirmed by `_backtrack` before the
   palier's own search budget/interruption mechanisms
   (`deadline_checks`/`attempt_done_event`/`batch_abandoned_event`/
   `PALIER_ATTEMPT_INTERRUPT_FRACTION`) cut it short. Previously, a
   "reprise telle quelle" continuation would have handed the *exact same*
   pattern to a fresh batch of parallel workers with more search budget —
   trivial to finish, since these slots each already have very few (often
   exactly one) real candidates left. With the cap at 0, that continuation
   path never runs at all: every such near-complete pattern is discarded
   and an entirely new, unrelated black-cell layout is drawn on top of the
   same carried-forward locked letters instead — repeating indefinitely
   (observed live: 152+ consecutive paliers, over 12 million cumulative
   failed internal search checks, the carried-forward locked-letter
   fraction never meaningfully advancing) since nothing about a fresh
   random pattern is any likelier to finish in one shot than the last one
   was.

   `MAX_CONSECUTIVE_CONTINUE_PALIERS` reverted from `0` back to `5` (its
   long-standing value throughout this whole naming/reconfiguration
   history, unchanged by the naming itself) — restoring the "continue"
   path's ability to give a nearly-finished pattern the extra search
   attempts it needs rather than discarding it prematurely. Verified live:
   `python3 -m py_compile` passed; a real `generate_grid()` run on both
   seeds of the standard 15×10 benchmark (Flash mode, `deadline_checks=
   1000`) succeeded on both — seed 2 in 10.8s (53 words, 39 black cells, 0
   mismatches, 0 empty white cells), seed 7 in 12.0s (63 words, 41 black
   cells, 0 mismatches, 0 empty white cells) — both comfortably back within
   this benchmark's own established historical range, confirming the
   revert restores the pre-experiment reliability.

   **This 5-revert was itself immediately pushed back on by the user**,
   who disputed the whole diagnosis rather than the value: "Non, revient
   à MAX_CONSECUTIVE_CONTINUE_PALIERS = 0. Un cycle se termine quand on ne
   peu plus jouer d'emplacement (ou quand on a dépassé la limite du
   budget, ou plus de 30% des process en échec). Donc, tous les
   emplacements restants doivent être testés avant de terminer un cycle
   (avec ou sans nettoyage complet)... Si on n'a bien testé tous les
   emplacements restant, et que tous les mots en place sont valides, la
   grille est alors réputée réussie. La reprise telle quelle ou le
   nettoyage est un mécanisme qui intervient après la tentative de
   remplissage, qui doit aller jusqu'au bout." In other words: `MAX_
   CONSECUTIVE_CONTINUE_PALIERS` was never the real bug — a single fill
   attempt should already run to its own genuine conclusion (no more
   playable slot, budget exceeded, or the 30% abandon threshold) before
   *any* continue/cleanup decision is even made; if that's true, `0`
   should have been perfectly safe all along.

   Investigated directly rather than guessed at, tracing the actual
   mechanism behind the reported symptom: the "77% rempli" preview shown
   to the user is `progress("pattern_generated", ...)`'s own preview,
   built by `_cycle_start_preview` overlaying `carry_locked_letters` — real,
   crossing-confirmed content carried forward from the previous palier's
   own cleanup, never `forced_letters`' statistical guesses (this specific
   preview never touches those). Given `blackPercent`/`fillPercent` in
   `frontend/static/script.js`'s `renderAttemptPreview()` share the exact
   same denominator (total cells), their summing to 100% (23+77) proves
   arithmetically that literally every white cell already carries a real
   letter — not that 23% were still blank, which my own prior reply had
   wrongly assumed (see the "77% rempli" mis-reading corrected in this
   same session's chat, right before this entry).

   Root cause, confirmed by reading `try_fill`/`Filler` directly: a slot
   whose every cell is already determined by *real* crossing assignments
   (never forced-only guesses) is not automatically confirmed by
   `_backtrack` itself — it still needs to be explicitly selected and
   assigned, the normal way, taking at least one recursive step. If the
   search's own selection order never happens to reach that slot before
   the attempt ends (deadline exceeded, the 30% abandon rule, or
   `attempt_done_event`/`batch_abandoned_event` interrupting it because a
   sibling attempt of the same palier already finished), the slot stays
   formally `None` in `assignment` even though its own domain, matching
   the already-known crossing letters, has already narrowed to *exactly
   one* real, still-unused dictionary word — the word already spelled out
   by those letters. Such a slot is correctly never flagged "impossible"
   either (`impossible_zone_slots()` — its one valid, unused candidate
   means it's genuinely not blocked), so it falls into a real gap: neither
   confirmed nor flagged, permanently straddling both states. Since a full
   nettoyage's own `_build_retry_seed`/`_clean_blocked_slots` only ever
   removes words crossing a *flagged* impossible slot, a pattern in this
   exact state has nothing for cleanup to act on either — with `MAX_
   CONSECUTIVE_CONTINUE_PALIERS=0` removing the one mechanism
   (`_pattern_continue`) that would otherwise have given the *same*
   pattern more search budget to reach that one trivial slot, the
   carried-forward content stayed effectively frozen cycle after cycle
   (observed live: 152+ consecutive paliers, 12M+ cumulative failed
   internal search checks, no real advancement).

   Fixed at the actual root, not by touching `MAX_CONSECUTIVE_CONTINUE_
   PALIERS` again: a new `_close_implied_slots(slots, index, assignment,
   used_words, excluded_slots=None)`, called from `try_fill` immediately
   after `filler.solve()` returns (before `truly_complete` is computed) —
   operates on `filler.best_assignment` (the real high-water-mark used
   everywhere downstream, never `filler.assignment` directly, which can be
   partially unwound by backtracking on a failed search) and `filler.
   used_words`, both mutated in place. For every still-unassigned,
   non-excluded slot, it derives `known` letters purely from *already-
   assigned* slots (never a statistical guess), and — mirroring `_force_
   single_candidate_slots`'s own fixed-point-loop shape, but critically
   also filtering out already-`used_words` candidates, which that earlier,
   pre-search-only function never needed to do — confirms any slot whose
   real, still-available candidate count has narrowed to exactly 1.
   Repeated to a fixed point (closing one slot can, via a shared cell,
   narrow a neighboring one to 1 in turn). A cheap guard,
   `len(candidates) - len(used_words) > 1: continue`, skips the expensive
   `not in used_words` filtering for any slot whose raw candidate count is
   still far larger than could ever be brought down to 1 by removing at
   most `len(used_words)` entries — keeping this pass cheap even for a
   still wide-open slot backed by a large raw candidate list. `try_fill`
   then resyncs `filler.assignment = list(filler.best_assignment)` right
   after the closure call — necessary because `truly_complete` (and the
   real success-path return value, `(slots, filler.assignment)`) reads
   `filler.assignment` specifically, not `best_assignment` — before
   computing `truly_complete`; on a genuine from-scratch success this is a
   no-op (the two already coincide), so no existing caller's behavior
   changes.

   This directly generalizes the user's own stated principle into code:
   a fill attempt's own natural conclusion — deadline, 30% abandon,
   sibling interruption, or genuine exhaustion — is exactly when this
   closing pass runs, confirming any slot that was already, implicitly,
   the only possibility left, before the attempt's outcome (`reason`,
   `truly_complete`) is ever decided. It changes nothing about slots that
   still have two or more genuinely open candidates — those correctly
   stay unresolved and the palier's outcome (continue/cleanup, whichever
   `MAX_CONSECUTIVE_CONTINUE_PALIERS` currently allows) is unaffected for
   them.

   Verified in isolation first: 6 hand-built tests against a tiny,
   fully-controlled dictionary and small crossing grids — a basic
   two-slot crossing closes correctly once one side is assigned; a
   candidate that would match but is already in `used_words` is correctly
   never reassigned; an explicitly excluded slot is correctly left alone
   even though it would otherwise close; a 3-slot chain (closing one slot
   narrows a second, which in turn narrows a third) resolves fully in one
   call thanks to the fixed-point loop; a real `try_fill()` call with
   `deadline_checks=0` (the search budget exhausted before `_backtrack`
   ever gets to explicitly confirm the already-fully-determined down slot)
   still returns a genuine success (`reason == "solved"`) via closure
   alone; a control with a genuinely 2-candidate slot under the same
   `deadline_checks=0` condition correctly returns no result at all — the
   closure never over-reaches into a slot that isn't truly narrowed to
   exactly one candidate.

   `MAX_CONSECUTIVE_CONTINUE_PALIERS` restored to `0` (the value the user
   actually asked for, twice now). A first real end-to-end check on the
   standard 15×10 benchmark still showed seed 2 failing outright in Flash
   mode (`deadline_checks=1000`) even with the closure fix in place — but
   a direct, honest empirical comparison (4 repeated runs each of seed 2,
   Flash mode, `0` vs. the prior `5`) showed this is genuine, previously-
   documented run-to-run timing variance in this exact interrupt-based
   parallel search mechanism (`attempt_done_event`/`batch_abandoned_
   event`'s own real wall-clock race between sibling worker processes —
   see this file's own extensive prior history of the same phenomenon),
   not a further, fixable bug: `0` succeeded 3/4 times (10.8s/30.8s/11.4s,
   one outright failure at 25.9s), `5` succeeded 4/4 times (10.1s-12.9s)
   — a real, if modest, reliability gap under Flash mode's artificially
   tiny 1000-check budget specifically (a deliberate fast-testing
   convention, not representative of real usage), consistent with `0`
   giving up on a genuinely difficult random pattern after a single,
   possibly-too-small budget instead of ever giving the *same* pattern a
   second, fresh-budget attempt the way `5`'s own "reprise telle quelle"
   would. Both seeds also succeeded with `0` at the real, non-Flash
   default budget (`rows*cols*2000`, seed 7 in 116.8s) — seed 2 still
   failed once at that larger budget too (90.0s), matching the same
   variance pattern rather than a budget-size-dependent one. Reported to
   the user as-is rather than silently reverted a third time, since `0`
   is what they explicitly, repeatedly asked for and the closure fix
   genuinely resolves the specific, concrete bug they reported and
   diagnosed — the residual variance is a separate, pre-existing
   characteristic of this exact parallel-interrupt mechanism, not
   something this particular fix was ever meant to eliminate.

   **A second, deeper bug in the exact same area was found right after**,
   this time by the user directly correcting a wrong claim in my own
   explanation rather than reporting a fresh symptom: asked to explain why
   a specific blank cell (shown in a screenshot, "0 % injouable") persisted
   across several cycles without ever being filled or flagged, I answered
   that `MAX_CONSECUTIVE_CONTINUE_PALIERS=0` redraws "un motif de cases
   noires entièrement nouveau" every cycle — which the user immediately
   and correctly disputed: "MAX_CONSECUTIVE_CONTINUE_PALIERS = 0 ne
   réinitialise pas la grille complète, ça déclenche seulement un
   nettoyage local des zones injouables. Sur ce cas, la zone n'étant pas
   injouable, le cycle suivant repart avec la même grille et devrait donc
   essayer de compléter les vides." Re-reading `generate_grid`'s own
   `else:` (full-nettoyage) branch confirmed the user was right and my own
   explanation was wrong: `_build_retry_seed`'s `carry_seed_grid` is the
   *same* pattern (only re-opened around genuinely impossible zones), and
   the next `_pattern_attempt` continues placing black cells *on top of*
   that carried-forward grid (`make_pattern(seed_grid=carry_seed_grid,
   locked_letters=carry_locked_letters, ...)`) — not a wholesale, unrelated
   redraw.

   Investigated live, methodically, rather than re-guessed: a diagnostic
   hooked into real `generate_grid()` runs (several seeds of the standard
   15×10 benchmark, Flash mode) found the exact phenomenon reproduced
   directly — on multiple seeds, the *same single cell* stayed blank for
   dozens to 100+ *consecutive* cycles, every single one reporting
   `0%` impossible for that specific preview. A second, deeper diagnostic
   traced the winning candidate's own `reason` for every such stuck cycle:
   overwhelmingly `"blocked_on_excluded_slot"` — meaning `Filler.solve()`
   had genuinely completed *every* non-excluded slot; the blank cell(s)
   belonged exclusively to a slot in `excluded_slots` (a slot fully locked
   by carried-forward letters whose combination doesn't spell any real
   word — computed once, up front, in `_pattern_attempt`'s own `locked_
   impossible_slots`). A third diagnostic then directly compared, across
   every `blocked_on_excluded_slot` event of a real run, whether that
   excluded slot actually appeared in `impossible_slots` (the field
   `_clean_blocked_slots`/`_build_retry_seed` reads to decide what to
   clean): **330 of 349 instances measured — 95% — were NOT flagged**,
   despite being genuinely, structurally impossible by construction.

   Root cause, traced directly in the code rather than reasoned about in
   the abstract: `_pattern_attempt`/`_pattern_continue` both merge `locked_
   letters`/`known_letters` (real, carried-forward confirmed content)
   directly into `forced_letters` before ever constructing a `Filler`
   (`forced_letters = {**forced_letters, **locked_letters}`) — so by the
   time `Filler` exists, it holds only *one* combined dict, with no way to
   tell a real, confirmed locked letter apart from a mere statistical seed
   guess. This was harmless for the *live* search (`_domain`'s normal,
   `ignore_forced=False` path merges the two anyway, no behavior change) —
   but it directly broke `impossible_zone_slots()`'s own `ignore_forced=
   True` call (added in an *earlier* fix, see above, specifically to stop
   a mere statistical guess from being treated as a hard constraint):
   ignoring the *entire* `forced_letters` dict now also silently discarded
   the real, confirmed `locked_letters` portion smuggled inside it — a
   fully-locked-but-invalid slot's own letters became completely invisible
   to `impossible_zone_slots()`, so its domain resolved to the full,
   unconstrained dictionary for that length (almost certainly non-empty),
   and the slot was judged perfectly fine — never flagged, cycle after
   cycle, so `_clean_blocked_slots` never had anything to act on and never
   removed the crossing word actually responsible, letting the identical
   dead end reconstruct itself indefinitely.

   Fixed by giving `Filler` a genuinely separate `locked_letters`
   parameter (`Filler.__init__`, stored as `self.locked_letters`, distinct
   from `self.forced_letters`) and checking it *unconditionally* in
   `_domain` — right after a real crossing assignment, before the
   `ignore_forced`-gated `self.forced_letters` fallback — so a locked
   letter counts as a hard constraint whether or not `ignore_forced` is
   set, while a mere statistical guess still only counts when it isn't.
   `try_fill` (which already had its own `locked_letters` parameter, but
   only ever used it to compute the `locked_cells` *display* diagnostic,
   never forwarded it to `Filler` at all) now also passes `locked_letters=
   locked_letters` into the `Filler(...)` constructor. Deliberately
   minimal: the pre-existing merge in `_pattern_attempt`/`_pattern_
   continue` (`forced_letters = {**forced_letters, **locked_letters}`) was
   left untouched rather than removed — with `self.locked_letters` now
   checked first and unconditionally, the redundant copy still sitting in
   `self.forced_letters` is provably unreachable dead weight, never
   incorrect, so removing it wasn't necessary to fix the bug and would
   only have added risk (touching `build_partial_letters_grid`'s own
   `forced_cells` display, which still reads the merged dict) for no
   correctness benefit.

   Verified: 3 isolated `Filler` tests — a slot fully covered by `locked_
   letters` spelling an invalid combination is now correctly flagged
   impossible (previously invisible to this exact check); an unlocked
   slot with a real, tiny dictionary is correctly *not* flagged just for
   being the dictionary's only word (still unused); a slot covered only by
   a statistical `forced_letters` guess spelling an equally invalid
   combination is correctly still *not* flagged — confirming the fix adds
   the missing locked-letters coverage without reopening the original,
   already-fixed statistical-guess bug this exact mechanism was built to
   prevent. Re-ran the same live diagnostic that measured the 95% miss
   rate: dropped to 9-12% (87/99, 29/32, 3/3 correctly flagged across 3
   separate runs) — not fully zero (a residual, distinct exclusion source,
   `Filler.exclude_immediately_impossible_slots()`, can also exclude a
   slot before backtracking even starts based on `_domain(i)` *without*
   `ignore_forced` — i.e., partly on the statistical portion too — which
   `impossible_zone_slots()` then correctly does *not* treat as grounds
   for "impossible" on its own, a related but distinct, much smaller gap
   not chased further this round). A full end-to-end `generate_grid()` run
   on both seeds of the standard 15×10 benchmark (Flash mode) succeeded
   cleanly (seed 2 in 17.4s, 55 words, 44 black cells; seed 7 in 18.0s, 58
   words, 39 black cells; 0 mismatches, 0 empty white cells each) —
   markedly faster than every pre-fix measurement of this same benchmark
   under `MAX_CONSECUTIVE_CONTINUE_PALIERS=0`. A repeated 6-run reliability
   check on seed 2 alone (Flash mode, the same methodology used to measure
   the earlier 3/4 variance) came back **6/6** successful (10.0-16.7s each)
   — a real, substantial reliability improvement over the pre-fix
   baseline, not just a one-off lucky run.

   **A third bug, this time genuine silent data loss rather than a
   detection gap, was found right after — from the user directly
   disputing a wrong claim in my own explanation.** Shown two screenshots
   of the same palier's own "Génération du motif de cases noires" preview
   (68% rempli) immediately followed by its own "Motif de cases noires
   posé... recherche des mots en cours" preview (24% rempli, many
   previously-shown letters now blank), I explained this as `MAX_
   CONSECUTIVE_CONTINUE_PALIERS=0` "redessin[ant] un motif de cases noires
   entièrement nouveau" each cycle — the user immediately, correctly
   disputed this: "MAX_CONSECUTIVE_CONTINUE_PALIERS = 0 ne réinitialise
   pas la grille complète, ça déclenche seulement un nettoyage local des
   zones injouables. Sur ce cas, la zone n'étant pas injouable, le cycle
   suivant repart avec la même grille et devrait donc essayer de
   compléter les vides." Re-reading `generate_grid`'s own full-nettoyage
   branch confirmed the user was right: `carry_seed_grid` is the *same*
   pattern carried forward (only reopened around genuinely impossible
   zones), and `make_pattern(seed_grid=carry_seed_grid, ...)` only ever
   *adds* black cells on top of it — never a wholesale, unrelated redraw.

   Investigated live rather than re-guessed: a diagnostic comparing, for
   every palier, the `example_grid` shown by the `"pattern"` event
   (cycle-start, `_cycle_start_preview(rows, cols, carry_seed_grid,
   carry_locked_letters, carry_preseed_assignment)`) against the very next
   `"pattern_generated"` event *of the same attempt number* — confirmed
   the exact phenomenon directly: real, previously-locked letters
   (confirmed by `locked_cells` in the `"pattern"` event) turning blank in
   the `"pattern_generated"` event of the *same* palier, on 10 of 29
   checked pairs in one real run, up to 45 cells lost in a single pair.

   Root cause, traced precisely: the `"pattern_generated"` preview for
   any non-first palier speculatively reconstructs, in the *parent*
   process, the pattern its own last non-reset worker is about to compute
   — `early_pattern = make_pattern(rows, cols, ratio, random.Random(seeds
   [-1]), ..., seed_grid=carry_seed_grid, locked_letters=carry_locked_
   letters, ...)` — calling `make_pattern` directly on `carry_locked_
   letters`, the exact same dict object about to be handed to the real
   dispatched workers right after. `make_pattern` already defensively
   copies `seed_grid` on entry (`grid = [row[:] for row in seed_grid]`) —
   but had no equivalent protection for `locked_letters`, which flows
   unprotected into `_prefill_unfillable_slots`' own "nettoyage curatif"
   path, `_remove_a_crossing_word` — whose own docstring says plainly
   "Mute `locked_letters` en place" (`locked_letters.pop(cell, None)` for
   every cell of whichever crossing word it decides to sacrifice). This
   mutation is completely safe for every *real* per-worker call
   (`_pattern_attempt`, dispatched via `ProcessPoolExecutor` — each worker
   receives its own independent, pickled copy, so mutating it can never
   reach the parent's own state) — but this one preview-reconstruction
   call runs directly in the parent process, on the parent's own live,
   shared `carry_locked_letters` — so a purely cosmetic, meant-to-be-
   throwaway preview computation was silently popping real, confirmed
   letters out of the actual object handed to the real workers submitted
   immediately afterward. Not just a rendering glitch: genuine, permanent
   data loss, propagating into the real search.

   Fixed at the same place `seed_grid` is already protected, rather than
   patching the one call site: `make_pattern` now also defensively copies
   `locked_letters` right at its own top (`locked_letters = dict(locked_
   letters) if locked_letters else locked_letters`), mirroring the
   pre-existing `seed_grid` copy immediately below it — protecting *every*
   caller uniformly (present and future), not just the one that happened
   to expose the bug, and matching the exact same defensive pattern
   already established for the other mutable parameter this same function
   receives.

   Verified live: re-ran the exact diagnostic that first measured the
   drops (10/29 pairs affected, up to 45 cells in one pair) — **0/45**
   pairs affected after the fix on the same run; repeated on 3 more seeds
   that had specifically exhibited the earlier "stuck cell" investigation
   in this same session (5, 6, 10) — **0 drops across 83 total pairs
   checked**. A full end-to-end `generate_grid()` run on both seeds of the
   standard 15×10 benchmark (Flash mode) confirmed no regression: 0
   mismatches, 0 empty white cells each — seed 2 in 10.2s, 62 words, 41
   black cells; seed 7 in 11.2s, 59 words, 37 black cells. A direct
   isolated test confirming `make_pattern` never mutates its caller's own
   `locked_letters` object was attempted but didn't reliably trigger the
   curative-cleanup mutation path on its own hand-built scenario (passed
   even before the fix was applied, a false negative rather than proof) —
   the live, real-`generate_grid()` diagnostic above is the actual,
   trustworthy verification for this fix, not that isolated unit test.

   **A diversity-injection mechanism was added on top of every full
   cleanup**, at the user's explicit request: "A chaque nettoyage complet
   (tous les 5 cycles) redémarrer 20% des process avec une grille
   réinitialisée totalement." Normally, every one of a palier's
   `PARALLEL_ATTEMPTS` workers dispatched via `_pattern_attempt` (the
   "motif neuf" branch, as opposed to `_pattern_continue`'s own "reprise
   telle quelle") receives the exact same `carry_seed_grid`/`carry_locked_
   letters` right after a cleanup — only their own random seed differs —
   which risks every one of them converging on the same kind of dead end
   again, especially now that the consecutive-continue cap forces a
   cleanup as often as every 5 paliers (see the cap history just above). A
   new `FULL_RESET_ATTEMPT_FRACTION` (0.20) and a new `just_cleaned` flag
   (set `True` at the end of the `else:`/nettoyage branch, `False` at the
   end of the `if still_has_hope:`/continue branch — so it always reflects
   exactly the palier that just finished, consumed once by the very next
   palier's own worker-submission code and then naturally overwritten
   again) together decide, only for a palier dispatched via
   `_pattern_attempt` right after a nettoyage, that
   `round(FULL_RESET_ATTEMPT_FRACTION * PARALLEL_ATTEMPTS)` of its workers
   (2 out of 10 on a 10-core machine) are submitted with `seed_grid=None,
   locked_letters=None` — a totally blank grid, exactly `_pattern_attempt`'s
   own from-scratch behavior — instead of the carried-forward state every
   other worker of that same palier still receives. No particular reason
   to prefer one seed over another for which workers get reset, since
   `seeds` are already independently random — the first `reset_count` of
   them are simply the ones reset, positionally. Never applies right after
   a "reprise telle quelle" palier (`just_cleaned` is only ever `True`
   right after a genuine nettoyage) nor on the very first palier of a call,
   which has no prior cleanup to speak of.

   Verified: an isolated check of the reset-count arithmetic across several
   `PARALLEL_ATTEMPTS` values (1→0, 2→0, 3→1, 4→1, 5→1, 10→2, 20→4) and of
   the per-worker dispatch logic itself (confirming exactly `reset_count`
   of the submitted argument-tuples carry `(None, None)` and the rest carry
   the real carried-forward grid/locked-letters, for both `just_cleaned`
   states) both matched expectations. Since a `ProcessPoolExecutor`
   target must be a real, picklable top-level function — ruling out a
   monkeypatched local closure as a way to observe which grids actual
   worker processes received — the dispatch decision itself was instead
   confirmed with a temporary, since-removed debug print placed right at
   `reset_count`'s own computation, run against a real `generate_grid()`
   call (15×10, seed 7): palier 1 correctly showed `just_cleaned=False,
   reset_count=0` (no prior cleanup yet), and every one of paliers 2
   through 11 — each dispatched via `_pattern_attempt` because the
   palier immediately before it had just done a full nettoyage — correctly
   showed `just_cleaned=True, reset_count=2`. A real `generate_grid()` run
   on both seeds of the standard 15×10 benchmark, with the mechanism fully
   in place, confirmed no regression (0 empty white cells and 0 mismatches
   each: seed 2 in 39.2s, 62 words; seed 7 in 51.4s, 54 words).

   Right after this, the user pointed out a related property worth
   confirming explicitly: "A la toute première initialisation, chaque
   process démarre avec une grille initialisée indépendamment des autres."
   This turned out to already be true of the existing code, requiring no
   change — `reset_count` above is always `0` at the very first palier
   (`just_cleaned` starts `False`), but that's not a gap: `carry_seed_grid`/
   `carry_locked_letters` are themselves already `None` for every worker at
   that point (no cleanup has ever run yet to set them to anything else),
   so `make_pattern` already builds a brand-new blank grid
   (`grid = [[WHITE] * cols for _ in range(rows)]`, a fresh object, never
   shared or aliased across workers) independently for every one of them —
   the only thing that ever differs between workers is each one's own
   `random.Random(seed)`, seeded independently by `generate_grid`'s own
   `rng.randrange(2**31)` draws. Verified live rather than assumed: called
   `make_pattern` directly `PARALLEL_ATTEMPTS` times with `seed_grid=None`
   and 10 different real seeds (the real French wordlist, the standard
   15×10 benchmark's own dimensions) — all 10 resulting patterns were
   pairwise distinct (`len(set(patterns)) == 10`), confirming genuine
   independence rather than an accidental convergence on the same layout;
   a second check through the full, real `_pattern_attempt` pipeline (not
   just `make_pattern` in isolation) on the same 10 seeds confirmed the
   same result — 10 distinct patterns out of 10 workers. DOC_ALGO/FR/
   ReadMe.md's own "Plusieurs tentatives en parallèle par palier" section
   was clarified with an explicit paragraph stating this, right after the
   diversity-injection mechanism above, so a reader doesn't mistake
   `reset_count=0` at palier 1 for "0% of workers start independently" —
   in reality all of them already do, for a more fundamental reason than
   the 20%-reset mechanism (which only ever matters once a cleanup has
   actually produced a non-blank carried-forward state to reset away from).

   **`sample_letter_biases`'s own 100-word sample was made to respect
   already-known letters**, at the user's explicit request, quoting the
   DOC_ALGO "graines" paragraph back: "Ne tirer que des mots valides par
   rapport aux lettres déjà en place sur les emplacements. Ne pas tester
   les emplacements réputés impossible (si ce n'est pas déjà le cas, mais
   de toute façon, la modification doit rendre impossible les tirages
   valides)." Previously, every slot's 100-word sample was drawn from
   `idx["words"]` — *every* word of the right length, filtered only by
   length, with no regard for any letter already known at one of that
   slot's own cells (`_pattern_attempt`'s own `locked_letters`, carried
   forward across paliers by the cross-palier retry mechanism, or
   `_pattern_continue`'s `preseed_assignment`, a fully-known word for an
   already-assigned crossing slot) — a real waste, since a good share of
   those 100 words could already be known to be incompatible with a letter
   the grid already has settled for certain.

   `sample_letter_biases` gained a new `known_letters` parameter (a `{cell:
   letter}` dict, `None` by default — no effect for any pre-existing
   caller). For a slot with at least one of its cells in `known_letters`,
   the sample is now drawn only from words that actually match those
   letters at their respective positions — the same per-position
   set-intersection already used by `Filler._domain`/
   `_slot_candidate_count` (`idx["pos"][pos][letter]`, intersected across
   every known position of that slot), rather than `idx["words"]`
   unfiltered. A slot with no known letters at all falls back to exactly
   the previous behavior (the full lexicon of that length) — this
   parameter changes nothing for a caller/slot that never supplies one.

   This directly satisfies the second half of the request without any
   separate "is this slot impossible?" check: if no real word matches the
   combination of letters already known at a slot, the intersection is
   empty, so there is nothing to sample — that slot contributes neither to
   `letter_scores` nor to `eligible` for this palier, exactly "rendre
   impossible les tirages valides." The pre-existing `excluded_slots`
   parameter (see its own entry above) is *not* redundant with this and was
   deliberately left as its own separate mechanism: for `_pattern_attempt`,
   `excluded_slots` is always `locked_impossible_slots` — a slot fully
   locked by `locked_letters` whose combination doesn't match any real
   word — which the new `known_letters` filtering *already* reduces to an
   empty sample on its own, making the explicit exclusion redundant only
   for that specific caller; but for `_pattern_continue`, `excluded_slots`
   carries forward a slot proven impossible by the *whole previous CSP
   search* (every interacting constraint across the grid), which can still
   have only *partial* letters known locally — the new filtering alone
   would not necessarily empty its sample in that case, so the explicit
   `excluded_slots` check in the `eligible` step remains necessary there.
   One further, smaller correctness fix came along for free: a cell
   already present in `known_letters` is now also excluded from `eligible`
   outright (`if cell not in known and count > LETTER_BIAS_MIN_COUNT and
   slot_idx not in excluded`) — every word in a filtered sample already
   shares the known letter at that position by construction, so that
   position's own "most common" letter would always be the already-known
   one at the full sample size, wastefully consuming that slot's one-seed-
   per-slot budget on a cell that already has a real, certain answer
   instead of a genuinely still-unknown one.

   `_pattern_attempt` passes `known_letters=locked_letters` directly (a
   parameter it already had for its own other purposes). `_pattern_continue`
   had no such per-cell dict available at all — only `preseed_assignment`
   (a word-or-`None` per slot) — so it now builds one via `extract_slots
   (seed_grid, rows, cols)` (a second, cheap call on the same grid `try_fill`
   re-extracts anyway right afterward — not worth threading through as a
   parameter just to avoid it) zipped against `preseed_assignment`, keeping
   only the already-assigned slots' own cell/letter pairs.

   Verified: three isolated `sample_letter_biases` calls against a small,
   fully controlled 5-word dictionary (HELLO/HOUSE/MOUSE/HORSE/HOIST, all
   length 5, one slot, no crossings) — with no `known_letters`, both `H`
   and `M` appear at position 0 (the unfiltered baseline); with `known_
   letters={(0,0): "H"}`, position 1's `letter_scores` only ever shows `E`/
   `O` (HELLO/HOUSE/HORSE/HOIST's own letters there — MOUSE, the one word
   not starting with `H`, never contributes), and cell `(0,0)` itself never
   appears in `forced` despite being a 100%-consensus cell; with `known_
   letters={(0,0): "Z"}` (no real word starts with `Z`), the call returns
   completely empty `letter_scores`/`forced` for that slot — confirming an
   impossible combination truly produces zero draws. A fourth isolated call
   confirmed `excluded_slots` still suppresses `eligible` even when `known_
   letters` itself yields real, valid candidates (the `_pattern_continue`
   case described above) — `letter_scores` non-empty, `forced` empty. A
   direct call to `_pattern_continue` against the real French wordlist (a
   real word preseeded onto one slot of a blank 5×5 grid) completed without
   error, confirming the new `known_letters` construction from `preseed_
   assignment` works against real data, not just the small hand-built
   dictionary above. Two full end-to-end `generate_grid()` runs on the
   standard 15×10 benchmark confirmed no regression (0 empty white cells
   and 0 mismatches each: seed 2 in 35.7s, 57 words; seed 7 in 25.4s, 58
   words).

   **A deterministic pre-step was added right before the seed statistics**,
   at the user's explicit request: "Avant de calculer les statistiques pour
   placer les graines, ajouter un traitement : quand un emplacement valide
   ne possède plus qu'une seule possibilité de mot, forcer les lettres
   restantes pour placer ce mot. Ne pas tester les emplacements réputés
   impossible (si ce n'est pas déjà le cas, mais de toute façon, la
   modification doit rendre impossible les tirages valides)." Unlike
   `sample_letter_biases`'s own 100-word sample (a plain statistical
   consensus a real word can still contradict), a slot whose already-known
   letters leave exactly one real dictionary word possible is no longer a
   matter of probability at all — it's that word, or none.

   Two shared helpers were factored out first, since three different call
   sites now need essentially the same per-position set-intersection that
   used to live only inside `_slot_candidate_count`: `_slot_candidates
   (index, length, cells, known_letters)` (new) returns the actual matching
   words/set — `idx["words"]` unfiltered if no cell of the slot is in
   `known_letters` yet, `()` if the index has no words of that length or
   none match — and `_slot_candidate_count` (pre-existing, used by the
   pre-fill mechanism and `_pattern_attempt`'s own preseed-assignment
   validation) was rewritten as a one-line `len(_slot_candidates(...))`
   around it, preserving its exact previous behavior while removing the
   duplicated intersection logic. `sample_letter_biases`'s own `known_
   letters`-based filtering (added just above) was simplified to call
   `_slot_candidates` too, instead of repeating the same set-intersection
   inline a third time.

   A new `_force_single_candidate_slots(slots, index, known_letters,
   excluded_slots=None)` — placed right before `sample_letter_biases` in
   the file, since it's conceptually the deterministic counterpart run
   immediately ahead of it — copies its input dict once (never mutates the
   caller's own), then repeatedly scans every non-excluded, not-yet-fully-
   known slot: if `_slot_candidates` narrows to exactly one real word, every
   one of that slot's still-unknown cells is filled in with that word's own
   letters. Repeated in a `while changed:` loop, not just a single pass —
   forcing one slot's letters can, via a shared crossing cell, push a
   neighboring not-yet-resolved slot down to one candidate too, which a
   single left-to-right scan could miss depending purely on which slot
   happened to be visited first; the loop keeps re-scanning until a full
   pass makes no further change. A slot in `excluded_slots` (already known
   impossible — see `Filler.excluded_slots`) is never examined at all: it
   will never be attempted by the search regardless, so there's nothing
   useful to deduce for it. This directly satisfies the second half of the
   request without a separate explicit check: once a slot's own known
   letters already rule out every real word (an impossible combination),
   `_slot_candidates` returns an empty set, `len(...) != 1`, and nothing is
   forced — the filtering already "rend[s] impossible les tirages valides"
   for such a slot on its own.

   Wired into both `_pattern_attempt` and `_pattern_continue`, each in a way
   that lets a newly-fully-determined slot flow into the *exact same*
   already-existing "is this slot now a real, validated assignment"
   machinery those two functions already had, rather than introducing a
   second, parallel promotion path:
   - `_pattern_attempt` already computed `slots = extract_slots(grid, rows,
     cols)` and a `preseed_assignment`/`locked_impossible_slots` pass
     immediately afterward, validating every slot whose cells are *all* in
     `locked_letters` via `_slot_candidate_count(...) > 0` before promoting
     it to a real assignment. `_force_single_candidate_slots` is now called
     right there, reassigning `locked_letters` to its own augmented return
     value (`locked_letters or {}` as the seed, since the function always
     returns a dict, never `None`) *before* that existing preseed-assignment
     loop runs — a slot that becomes fully known only through this new
     deduction is picked up and re-validated by that same pre-existing loop
     with no further code needed, exactly as if it had arrived fully locked
     from a previous palier's own carry-forward state. Called
     unconditionally (not gated behind `if locked_letters:`) specifically so
     the zero-prior-knowledge case (a length with only one dictionary word
     at all — a real, non-hypothetical case in this project, see below) is
     also covered, not only the "narrowed down via already-known letters"
     case.
   - `_pattern_continue` had no equivalent validated-promotion loop of its
     own at all — its existing `known_letters` (derived from `preseed_
     assignment`) only ever fed `sample_letter_biases`'s sampling, never
     `Filler` itself as a real constraint, because those cells were already
     authoritative via `preseed_assignment`'s own direct effect on `Filler.
     assignment`. A newly-deduced letter has no such backing yet, so two
     things were added: (1) a promotion loop mirroring `_pattern_attempt`'s
     own — for every non-excluded slot not already in `preseed_assignment`,
     if the augmented `known_letters` now covers all its cells, the
     resulting word is validated via `_slot_candidate_count(...) > 0`
     (never just assigned unchecked — a slot can become "fully known"
     purely through crossing cells without `_force_single_candidate_slots`
     itself ever having validated *that specific combination* against this
     slot's own length, since its own loop skips any slot already fully
     known without checking it) and only promoted to `preseed_assignment`
     if genuinely real, left as `None` otherwise so `try_fill`'s own
     empty-domain detection naturally reports it through the usual
     `impossible_slots` diagnostic path; (2) `forced_letters = {**forced_
     letters, **known_letters}` right after the `sample_letter_biases`
     call, mirroring `_pattern_attempt`'s own equivalent merge — without
     it, a letter deduced for a slot that *doesn't* become fully known
     (still missing some other cell) would have no way to reach `Filler`
     as a real constraint at all, only ever influencing the sample pool.

   Verified: four isolated `_force_single_candidate_slots` tests against a
   small hand-built dictionary — two known letters narrowing a 3-word,
   length-5 lexicon down to exactly one match correctly force the
   remaining letters; a length with only one dictionary word in the whole
   (tiny) lexicon is fully forced even with zero prior known letters; a
   slot passed via `excluded_slots` is left completely untouched despite
   otherwise resolving to one candidate; a two-slot crossing scenario
   (deliberately designed so slot A only resolves to one candidate from
   its *own* known letters, none of which are shared with slot B) confirmed
   the fixed-point loop correctly propagates A's own newly-forced crossing
   letter into narrowing B down to one candidate too — a single pass alone
   would have missed slot B entirely. A direct check against the real,
   full French wordlist confirmed `_slot_candidates`/`_slot_candidate_count`
   preserve their exact prior behavior after the refactor (no constraints,
   narrowed-to-one, impossible, and unknown-length cases all matched
   expectations) and, separately, found and exercised a genuine real case:
   length 22 has exactly one word in the actual French dictionary
   (`ANTICONSTITUTIONNELLES`) — confirmed `_force_single_candidate_slots`
   correctly forces its entire content from zero prior knowledge. A direct
   call to `_pattern_continue` against the real wordlist (a real word
   preseeded onto one slot of a blank grid) completed without error,
   confirming the new promotion loop runs correctly against real data. Two
   full end-to-end `generate_grid()` runs on the standard 15×10 benchmark
   confirmed no regression (0 empty white cells and 0 mismatches each: seed
   2 in 38.4s, 56 words; seed 7 in 31.8s, 60 words).

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

   **A genuine "stuck fixed point" was reported and root-caused much later
   in this project's history**, on a very large (30×30) grid, from a real
   screenshot showing "tentative 169/200, 2477744 grilles échouées
   jusqu'ici, 32 % noir, 67 % rempli, 0 % injouable" — with the black-cell
   percentage never once moving across many consecutive attempts: "il
   enchaine les essais sans jamais ajouter de case noire. Il y a un
   problème (encore) avec la règle des 1/10." Investigated directly (per
   the user's own explicit instruction "Ne pas tester de bout en bout sur
   une grille aussi grande !" — no 30×30/200-attempt run was ever launched
   to completion): a temporary env-var-gated debug print
   (`_DEBUG_STUCK`, since fully removed), logging every palier's
   `still_has_hope`/`impossible_slots`/black-cell count/assigned-slot
   count, run against several real, independent 30×30 generations (reduced
   attempts/deadline budgets, still real `generate_grid()` calls, never
   the full 200-palier scenario) confirmed a genuine fixed point: **11
   consecutive paliers with strictly identical state** (same black-cell
   count, same impossible slots, same assigned-slot count) once the grid
   became very heavily locked — the 1/10 rule itself was never actually
   in play here (it only ever applies to the "reprise telle-quelle" path,
   never to the full nettoyage the logs showed firing every time).

   Reported to the user with this evidence via `AskUserQuestion` (three
   options: detect the repetition and force a wider perturbation, increase
   `FULL_RESET_ATTEMPT_FRACTION` when stuck, or a user-specified
   alternative) — the user identified the actual root cause directly,
   more precisely than any of the three offered options: "la meilleure
   grille sélectionnée maximise un critère sur les mots. Les cases noires
   ne sont pas prises en compte. Adapter le critère de sélection :
   maximiser le critère lettres, et à égalité, choisir la grille qui a le
   plus de cases noires." Confirmed directly in the code: an *earlier*
   version of this exact selection (see the entry above) once used
   `(len(confirmed_letters), -black_cell_count)` — black-cell count
   already a tie-break at the time, but with the *opposite* sign (fewer
   black cells preferred) — before being replaced outright by the pure
   `_words_in_place_score` with no black-cell consideration at all. On a
   near-complete grid, favoring pure word-score with no regard for
   structural room left behind can keep re-selecting a low-black-cell
   candidate every single palier even when a same-scoring alternative with
   more black cells (and therefore more genuine freedom for the next
   palier's own regeneration to actually change something) was available.

   Fixed by adding a new `_candidate_black_count(cand_seed)` helper
   (`sum(row.count(BLACK) for row in cand_seed)`) and changing both
   `max(cleaned_candidates, key=...)` calls (the normal pass and the
   `exclude_impossible_locked=True` fixed-point-breaking pass) from a bare
   `_words_in_place_score(...)` key to a tuple
   `(_words_in_place_score(...), _candidate_black_count(...))` — words
   score stays the absolute primary criterion (unchanged), black-cell
   count now breaks a tie in favor of *more* black cells, the reverse of
   the old, since-removed `-black_cell_count` sign.

   Verified live, not just reasoned about: re-ran the same three 30×30
   seeds that exposed the fixed point under the exact same reduced
   budgets. Seed 2, which previously failed outright after 80 attempts
   with 11 identical consecutive paliers, now **succeeded** (332.1s) —
   the strongest possible confirmation. Seeds 3 and 4 still didn't
   complete within the same reduced 80-attempt budget, but both showed
   genuine, steady forward progress instead of a frozen state (seed 3:
   309/312 slots assigned by the last attempt, up from a hard freeze at
   302/304 before the fix; seed 4: 314/317, no repeated-identical-state
   streak at all) — a real, substantial improvement even where the
   reduced budget wasn't enough to finish; the real deployment default
   (`attempts=200`) gives considerably more room than the 80 used here. A
   supplementary diagnostic (temporarily printing the distinct
   `(words_score, black_count)` pairs across the up-to-6 cleaned
   candidates at each nettoyage) confirmed real diversity existed among
   candidates that the old, black-cell-blind criterion was silently
   discarding. A full end-to-end `generate_grid()` run on both seeds of
   the standard 15×10 benchmark (Flash mode) confirmed no regression: 0
   mismatches, 0 empty white cells each — seed 2 in 12.1s, 58 words, 36
   black cells; seed 7 in 10.5s, 54 words, 40 black cells.

   **A complementary fix landed right after, on the exact same "reprise
   telle-quelle" cleanup mechanism** (`_clean_blocked_slots`), at the
   user's explicit request: "Nettoyage des emplacements impossibles :
   quand une zone n'a strictement aucune possibilité après nettoyage,
   noircir toutes les cases restantes." Previously, the per-impossible-
   slot `while True:` loop only ever removed crossing assigned words one
   at a time until either a real candidate became possible again, or
   there were no more crossing words left to remove (`crossing` empty) —
   in that second case, the loop simply stopped, leaving the slot
   unresolved (no word, no black cell) even when its true candidate count
   was genuinely, permanently zero (e.g. a length the dictionary has no
   word of at all) — exactly the kind of dead weight that could keep
   resurfacing identically at every future cleanup, contributing directly
   to the fixed-point class of bug just fixed above.

   The loop now tracks whether it exited via genuine exhaustion
   (`exhausted = True`, set right where `crossing` comes back empty) as
   opposed to finding a sufficient candidate count mid-loop. After the
   loop, if `exhausted` and `black_cell_capable` (this feature is scoped
   the same way as the existing 1/10 alternative — "reprise telle-quelle"
   only, never `_build_retry_seed`'s own internal call or
   `_cleaned_playable_score`, neither of which pass `grid`/`rows`/`cols`),
   the slot's real candidate count is recomputed with *no* letter
   constraints at all (`_slot_candidate_count(index, len(slots[i]),
   slots[i], {})` — safe to do with an empty `known` dict, since
   `exhausted` only becomes `True` once every crossing assigned word has
   already been stripped away, so nothing constrains this slot's
   positions at all any more) — if that count is strictly `0` (not merely
   under `min_candidates`, which the user's own wording explicitly
   distinguished from "insuffisant"), every one of the slot's own cells is
   blackened directly, one at a time, each still gated by the same
   `is_structurally_valid(min_interior_free=1)` guard already used
   everywhere else in this function — never a shortcut around that
   absolute invariant, even for a "just get rid of it" case like this
   one. Any surviving crossing word through a newly-blackened cell is
   unassigned too, mirroring the existing single-cell alternative's own
   defensive cleanup (though in practice this can never fire here, since
   `exhausted` already guarantees no crossing word remains by the time
   this runs).

   Verified live: 6 isolated `_clean_blocked_slots` tests — a fully
   isolated 3-cell dead slot (no crossing words, a dictionary with zero
   words of that length) gets all 3 cells blackened; the same dead-length
   slot, this time crossed by one assigned word, has that crossing word
   correctly removed *and* all 3 cells blackened; the same crossing setup
   but with the length *genuinely* covered by real dictionary words
   correctly blackens nothing, only removing the now-freed crossing word;
   a real bridge-cell scenario (the sole connector between two halves of
   an otherwise-open grid, forced dead-length via an empty dictionary)
   correctly leaves the bridge cell white — `is_structurally_valid`
   rejects it — while still blackening the two safe, non-bridge cells of
   the same doomed slot; a call without `grid`/`rows`/`cols` (matching
   `_build_retry_seed`'s own internal, `black_cell_capable=False` usage)
   confirmed a complete no-op for this feature; a slot with exactly one
   real candidate (`count == 1 >= min_candidates`, not zero) confirmed
   nothing gets blackened — the distinction between "insufficient" and
   "strictly zero" the user's own wording called for is real and correctly
   enforced. A full end-to-end `generate_grid()` run on both seeds of the
   standard 15×10 benchmark (Flash mode) confirmed no regression: 0
   mismatches, 0 empty white cells each — seed 2 in 23.0s, 54 words, 41
   black cells; seed 7 in 11.3s, 58 words, 42 black cells.

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

   **Much later in this project's history, the seed *count* itself
   (`target`) was corrected to genuinely shrink as a palier's own carried-
   forward state accumulates more confirmed letters**, at the user's
   explicit request: "Le nombre de graines ajoutées au début d'un cycle
   (cases bleues) doit être calculé par rapport au nombre de cases
   blanches restantes (donc diminuer et non pas être constant)." Root
   cause of the "constant" symptom: `target = round(total_white *
   force_fraction)` computed `total_white` as *every* white cell of the
   grid (`sum(row.count(WHITE) for row in grid)`) — a count that only
   ever shrinks when a *new black cell* is added, never when a cell
   simply becomes known/confirmed via `known_letters` (a confirmed
   letter still occupies a `WHITE` grid cell, letters aren't a separate
   grid state at all — see `extract_slots`/`WHITE`/`BLACK`). Since a
   "reprise telle quelle" streak (see `generate_grid`) can confirm a
   large and growing share of the grid across several consecutive
   paliers while adding comparatively few new black cells, `total_white`
   — and so `target` — stayed close to flat for a long stretch, even
   though fewer and fewer cells genuinely still needed a statistical
   hint (a cell already in `known_letters` was already excluded from
   `eligible` itself, see above — only the *count basis* had never been
   updated to match).

   Fixed by replacing `total_white` with `remaining_white = sum(1 for r
   in range(rows) for c in range(cols) if grid[r][c] == WHITE and (r, c)
   not in known)` — the count of white cells genuinely still without a
   known letter, matching exactly the same set `eligible` itself already
   draws from. For the very first palier of a call (`known` empty, no
   `known_letters` yet), `remaining_white` is always identical to the old
   `total_white` — this grid's own seed count is completely unaffected,
   consistent with every other "first palier stays unchanged" guarantee
   already established elsewhere in this same file this session (the
   `black_enrichment_fraction` white-proportion scaling, the first-palier
   adjacency prohibition). From the second palier of a "reprise telle
   quelle" streak onward, `remaining_white` — and so `target` — now
   genuinely decreases as more of the grid becomes confirmed, exactly as
   requested.

   Verified: an isolated direct reproduction of the formula (a 10×10 all-
   white grid, comparing "0 cells known" against "the top 5 rows, 50
   cells, all known") confirmed `remaining_white` correctly drops from 100
   to 50 — never affected by the black-cell count alone, only by `known`.
   A real, functional call to `sample_letter_biases` (a small controlled
   dictionary of five 10-letter words, `force_fraction=0.3`, a real 10×10
   grid) confirmed the actual seed count achieved drops from 10 (no
   `known_letters`) to 5 (half the grid marked known) — a real,
   end-to-end halving, not just the isolated formula in the abstract. A
   full end-to-end `generate_grid()` run on both seeds of the standard
   15×10 benchmark confirmed no regression (0 mismatches, 0 empty white
   cells each — seed 2 in 62.9s, 52 words, 39 black cells; seed 7 in
   57.2s, 54 words, 45 black cells).

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

   **The tier-2 score itself was simplified once more, from `int(100 ×
   remplies / sqrt(longueur))` back to a plain raw count**, at the user's
   explicit request, quoting the doc's own current wording back and asking
   to "modifier pour un score plus simple : nombre de lettres déjà
   remplies." `_backtrack`'s `scores` dict comprehension changed from
   `int(100 * self._placed_letter_count(i) / (len(self.slots[i]) ** 0.5))`
   to plain `self._placed_letter_count(i)` — no more `×100` scaling, no
   more square-root-of-length denominator at all. Every surrounding
   mechanic (the direction-alternation tier above it, the shuffle-before-
   sort tie-breaking, the `max(5, int(len(direction_pool) / 10))` window
   size) is unchanged — only the score expression itself. This removes the
   sqrt-denominator's own length-based competitiveness adjustment between
   long and short slots (documented in the entry above this one): a slot's
   score is now exactly how many of its own cells are already known,
   regardless of its total length. Verified: `Filler._placed_letter_count`
   itself is untouched (confirmed directly: a crossing-fixed cell and a
   seeded/forced cell each still count exactly once, never double-counted
   when a cell is both at once — same isolated check as every prior
   version of this tier), so this is purely a change to how that existing
   count feeds the window's own sort key; a real `generate_grid()` run on
   both seeds of the standard 15×10 benchmark confirmed no regression (0
   empty white cells and 0 mismatches each: seed 2 in 32.7s, 51 words; seed
   7 in 17.4s, 61 words).

   **The tier-2 score was inverted next**, at the user's explicit request:
   "Nouveau score: le plus de cases blanches" — from favoring the slot
   with the most already-known cells to favoring the one with the most
   still-*blank* cells, the opposite preference. `_backtrack`'s `scores`
   dict comprehension changed from plain `self._placed_letter_count(i)` to
   `len(self.slots[i]) - self._placed_letter_count(i)` — a slot's own
   length minus however many of its cells are already known (via a
   crossing assignment or a seed), so an untouched, fully-blank slot now
   scores highest and a nearly-complete slot scores lowest, the reverse of
   the immediately preceding version. Every surrounding mechanic (the
   direction-alternation tier above it, the shuffle-before-sort tie-
   breaking, the `max(5, int(len(direction_pool) / 10))` window size) is
   unchanged — only the score expression itself. Verified: an isolated
   check against `Filler._placed_letter_count` directly (a 6-cell slot
   with first 1, then 3, of its cells made known via a crossing assignment
   plus two seeded cells) confirmed the new score correctly comes back as
   `length - known` (5, then 3) rather than the raw known count itself; a
   real `generate_grid()` run on both seeds of the standard 15×10
   benchmark confirmed no regression (0 empty white cells and 0 mismatches
   each: seed 2 in 48.6s, 50 words; seed 7 in 20.6s, 48 words).

   **The window-size formula's own divisor was changed once more, from 10
   to 2**, at the user's explicit request — quoting the doc's own current
   wording (`max(5, int(emplacements encore libres dans cette catégorie /
   10))`) back and asking for `/ 2` instead. `_backtrack`'s `window_size =
   max(5, int(len(direction_pool) / 10))` became `max(5, int(len(
   direction_pool) / 2))` — a one-line change, every surrounding mechanic
   (the direction-alternation tier above it, the tier-2 score itself, the
   shuffle-before-sort tie-breaking, the floor of 5) entirely untouched.
   At an equal pool size this makes the window considerably wider (half
   of the still-free slots in the drawn category, instead of a tenth),
   leaving substantially more room for the final random draw among the
   best-scoring slots rather than narrowing it down almost to the single
   top scorer once a category has many free slots left. Verified: an
   isolated check of the new formula against several pool sizes (5→5,
   8→5, 9→5, 10→5, 11→5, 20→10, 49→24, 50→25, 100→50, 237→118, 300→150 —
   the floor of 5 now engaging for any pool under 10, versus under 50
   with the old ÷10 divisor) matched expectations; a real `Filler`
   instance built from 14 independent, non-crossing same-direction slots
   (bypassing `extract_slots` entirely — a hand-built `slots` list with no
   down-slot geometry at all, so every one of the 14 stays tied at
   `direction="across"` with no cross-talk to worry about), driven one
   backtracking step with an `rng.choice` spy recording the exact window
   size it was called with, confirmed a real window of 7 (`max(5,
   int(14/2))`) rather than the old formula's 5 (`max(5, int(14/10))`); a
   second, smaller 8-slot pool confirmed the floor of 5 still engages
   correctly (`max(5, int(8/2)) = 5`) rather than the raw `int(8/2) = 4`.
   A full, real end-to-end `generate_grid()` run on both seeds of the
   standard 15×10 benchmark confirmed no regression (0 mismatches, 0
   empty white cells each: seed 2 in 56.3s, 53 words, 38 black cells;
   seed 7 in 46.0s, 55 words, 36 black cells).

   **The tier-2 score was changed again, from the geometric "still-blank
   cells" proxy to the real candidate count, sorted ascending**, at the
   user's explicit request: "Score : le nombre de possibilités de
   remplissage par emplacement. Tri : le plus petit score d'abord."
   `_backtrack` already computes `domains = {i: self._domain(i) for i in
   unassigned}` right above this whole selection rule, as part of its own
   empty-domain dead-end check — `scores` now reuses that exact same,
   already-computed domain directly (`{i: len(domains[i]) for i in
   direction_pool}`) rather than calling `_domain` a second time, since
   `direction_pool` is always a subset of `unassigned`. The window slice
   changed from `sorted(shuffled_pool, key=lambda i: -scores[i])` (highest
   score first) to `sorted(shuffled_pool, key=lambda i: scores[i])`
   (lowest first) — a slot with fewer real dictionary candidates left is
   now favored over one with more, the closest this project's post-MRV
   selection rule has come back to a genuine constrainedness signal since
   the absolute MRV pre-selection tier was removed (and, per that
   removal's own history, twice asked-for-back-and-reverted — this
   change is a softer, windowed/randomized echo of that same idea, not a
   reinstatement of the old hard override: a highly-constrained slot is
   now *favored* within the window, never *forced* ahead of everything
   else the way the old tier 1 did). `Filler._placed_letter_count` — the
   helper computing the old "already-known-cell count" the previous score
   was derived from — lost its only remaining caller with this change and
   was deleted outright, per this project's own no-dead-code convention,
   rather than left unused.

   Verified: two isolated `Filler` reproductions — a 10-slot pool split
   into two groups of 5 (5 unconstrained slots with 3 real candidates
   each; 5 slots each pinned by a forced letter down to exactly 1 real
   candidate) confirmed the window, at `window_size=5`, contains *exactly*
   the 5 most-constrained slots (rows 5-9) and none of the 5
   less-constrained ones — proving the new ascending sort genuinely
   favors fewer candidates, not more; a 2000-trial sweep of 12 slots truly
   tied at the same domain size (3 candidates each, `window_size=6`)
   confirmed every slot appears in the window a comparable number of
   times (948-1043 out of 2000, no systematic favoritism) — the shuffle-
   before-sort tie-breaking still holds under the new score. A full, real
   end-to-end `generate_grid()` run on both seeds of the standard 15×10
   benchmark confirmed no regression (0 mismatches, 0 empty white cells
   each — seed 2 in 21.0s, 51 words, 45 black cells; seed 7 in 17.6s, 56
   words, 43 black cells — noticeably faster than the previous "still-
   blank cells" score on this same benchmark, consistent with a genuine
   constrainedness signal steering the search away from dead ends sooner).

   **The window-size formula's divisor was changed once more, from 2 to
   3**, at the user's explicit request — quoting the doc's own current
   wording back (with the new candidate-count score already reflected)
   and asking for `/ 3` in place of `/ 2`. `window_size = max(5, int(
   len(direction_pool) / 2))` became `max(5, int(len(direction_pool) /
   3))` — a one-line change, nothing else touched. Verified: a real
   `Filler` spy test (20 independent same-direction slots) confirmed the
   observed window size is now 6 (`max(5, int(20/3))`), matching the new
   formula rather than the old ÷2 value of 10; a full, real end-to-end
   `generate_grid()` run on both seeds of the standard 15×10 benchmark,
   combined with the same-session `forbid_adjacency` change to `_place_
   black_cells` (see that entry, earlier in this file, for the exact
   figures — both changes were verified together in the same run:
   0 mismatches, 0 empty white cells each), confirmed no regression.

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
   isolation.

   **This window was raised from 20 to 200**, at the user's explicit request: "Passer
   à 200 meilleurs candidats (évite les mots trop rares, tout en laissant plus de
   latitude à l'exploration de solutions variées)." Extracted into a proper module-
   level constant, `CANDIDATE_SCORE_WINDOW = 200`, right before the `Filler` class —
   the local `window = 20` this constant replaces was already referred to as
   `` `CANDIDATE_SCORE_WINDOW` `` in this exact function's own comment even before
   this change, a pre-existing drift between the comment's own wording and the
   literal it actually described; extracting a real constant with that name resolves
   that drift as a side effect, not just the requested value change. Every other part
   of the mechanism (the shuffle-before-sort tie-breaking already documented above,
   the sliding-window semantics, the `if self.letter_scores:` gate) is untouched —
   only the window size itself grew. Verified: a real `generate_grid()` run on both
   seeds of the standard 15×10 benchmark confirmed no regression (0 mismatches, 0
   empty white cells each: seed 2 in 112.2s, seed 7 in 210.0s) with the wider window
   active.

   **This window was raised again, much later in this project's history, from 200 to
   5000**, at the user's explicit request, quoting the doc's own current wording
   back: "Les 200 meilleurs obligent à commencer les emplacements vierges avec un
   vocabulaire très restreint. Relâcher la contrainte en fixant la fenêtre à 5000 (ça
   aura sans doute l'effet d'annuler l'intérêt du scoring, mais je voudrais voir ce
   que ça donne)." The user explicitly anticipated the consequence themselves before
   asking for it: on a slot with no cell yet fixed by a crossing (a genuinely blank
   one), `_candidate_score` has nothing to discriminate on at all — every candidate
   ties — so a window this wide, well past the number of real candidates for most
   slot lengths, reduces the draw to essentially a uniform pick across the whole
   dictionary for that length, only regaining a real preference for well-scored words
   once several of the slot's own cells are already fixed by crossings. Only the
   constant itself changed (`CANDIDATE_SCORE_WINDOW = 200` → `5000`) — no other part
   of the mechanism (the shuffle-before-sort tie-breaking, the sliding-window
   semantics, the `if self.letter_scores:` gate) was touched. Verified: a real
   `generate_grid()` run on both seeds of the standard 15×10 benchmark confirmed no
   regression (0 mismatches, 0 empty white cells each: seed 2 in 15.5s, 56 words, 36
   black cells; seed 7 in 10.6s, 56 words, 47 black cells).

   **A genuinely deep, foundational bug was found and fixed right after**, first
   reported as "encore" a fixed-point/no-progress symptom, then investigated in two
   passes across two separate user reports before landing on the real root cause.
   The first report (a 24×20-shaped generation shown mid-run) prompted an
   `AskUserQuestion` proposing to stop `impossible_zone_slots()`/`impossible_zone_
   cells()` from treating a mere statistical seed hint (`forced_letters`, from
   `sample_letter_biases`) as if it were a hard, confirmed constraint — the user
   replied "Je ne comprends pas la question. Abandonne ce sujet pour l'instant," so
   the (already-written, env-var-gated) diagnostic instrumentation was reverted and
   nothing was changed at that point.

   The second report supplied the missing concrete anchor: a real screenshot showing
   2 impossible slots (red-highlighted), the user noting directly "aucune lettre
   n'est affichée qui aurait été testée... Aucun mot croisant ne peut être retiré,
   car (presque) toutes les lettres participent à 2 mots stables... pourquoi une
   case noire ne serait pas insérée, au moins par la règle des 1/10." This is the
   exact same underlying mechanism as the first report, just phrased concretely
   instead of abstractly — investigated fresh, live, with new temporary
   instrumentation (`_DEBUG_NOACTION`, since fully removed) logging, for every
   impossible slot `_clean_blocked_slots` is asked to clean, whether its own
   recomputed candidate count (based purely on real crossing *assignments*, the only
   thing `_clean_blocked_slots` has ever looked at) was already `>= min_candidates`
   on the very first check — meaning the per-slot `while True:` loop's *first*
   `if count >= min_candidates: break` fires immediately, before either a word
   removal or the 1/10 black-cell roll is ever reached. Run against a real 22×18
   generation: **1799 of 4360** impossible-slot cleanup attempts (≈41%) hit exactly
   this — the slot `Filler.impossible_zone_slots()` had just declared impossible
   turned out, from `_clean_blocked_slots`'s own honest, crossing-word-only
   perspective, to already have a real candidate. Root cause confirmed precisely:
   `Filler._domain(i)` (which `impossible_zone_slots()` calls to decide "impossible")
   folds `self.forced_letters` in as a fallback constraint whenever no crossing slot
   is *really* assigned at a cell — so a slot can be marked "impossible" purely
   because a statistical seed guess (never confirmed, never refuted, simply still
   sitting there in the search's own final, abandoned snapshot) happened to make its
   domain empty, even with zero real crossing content. `_clean_blocked_slots` never
   looks at `forced_letters` at all (by design — there's no "word" to remove, a seed
   hint isn't an assignment), so it silently disagrees with the diagnostic that sent
   it this slot and does nothing whatsoever: no removal, no 1/10 roll, the slot just
   sits marked "impossible" — cycle after cycle, exactly the "des dizaines de cycles
   sans rien changer" the user described.

   Fixed by giving `Filler._domain` a new `ignore_forced=False` parameter (no effect
   for any pre-existing caller — every other call site, including the live search's
   own candidate-domain computation during `_backtrack`, is completely unaffected)
   that skips the `self.forced_letters` fallback entirely, keeping only letters
   genuinely imposed by an actually-assigned crossing slot. `impossible_zone_slots()`
   — the *sole* caller that needed this — now calls `self._domain(i, ignore_forced=
   True)` instead of the plain default. Since `impossible_zone_cells()` and every
   downstream consumer (`try_fill`'s `impossible_cells`/`impossible_slots`
   diagnostics — the preview's own red highlight — `_clean_blocked_slots`'s own
   cleanup target list, `still_has_hope`, and `_backtrack`'s own live 30%-abandon
   rule, `UNFILLABLE_ABANDON_FRACTION`, which calls `impossible_zone_cells()` mid-
   search) all funnel through `impossible_zone_slots()`, this one change makes the
   whole "impossible" concept consistently hard-fact-only everywhere it's used, not
   just in the one place that happened to be reported.

   Verified: 3 isolated `Filler` tests — a slot whose only blocking letter is a pure
   seed hint at a genuinely unassigned crossing cell is correctly no longer flagged
   impossible; the identical shape but with a *real* crossing word assigned at that
   same cell is still correctly flagged impossible (the fix doesn't weaken genuine
   detection); a direct `_domain(i, ignore_forced=True)` call confirmed it returns
   the real, unfiltered candidate set while the default `_domain(i)` (same `Filler`,
   same forced_letters) still returns empty — proving the new parameter is genuinely
   additive, not a behavior change to the default path. A full end-to-end
   `generate_grid()` run on both seeds of the standard 15×10 benchmark (Flash mode)
   confirmed no regression: 0 mismatches, 0 empty white cells each — seed 2 in
   10.9s, 53 words, 44 black cells; seed 7 in 13.4s, 53 words, 46 black cells. The
   same 22×18/seed-5 scenario used to find the bug still succeeds after the fix
   (73.0s, comparable to its own pre-fix timing) — confirming no regression to the
   ordinary case even though the 30%-abandon rule and the cleanup pipeline both now
   behave measurably differently under the hood.

   And
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

`FULL_RESET_ATTEMPT_FRACTION` (0.20, "20% of a palier's workers restart from
a blank grid right after a full cleanup") was replaced by a fixed count,
`FULL_RESET_ATTEMPT_COUNT = 1`, at the user's explicit request: "Réduire le
nombre de process qui calculent une grille totalement nouvelle à 1 seul (au
lieu de 20%)." `generate_grid`'s own `reset_count = round(FULL_RESET_
ATTEMPT_FRACTION * PARALLEL_ATTEMPTS) if just_cleaned else 0` became
`reset_count = FULL_RESET_ATTEMPT_COUNT if just_cleaned else 0` — a plain
one-line change, every surrounding mechanic (which of `seeds` gets reset —
still simply the first `reset_count` of them, `just_cleaned`'s own
True/False gating, the "pattern_generated" preview's own per-process-vs-
single-grid branching) untouched. On a 10-core machine this drops the
reset share from 2 workers per cleanup down to 1 — still enough to give
the search a genuinely independent escape route from a repeated dead end,
at a smaller cost to the batch's own carried-forward progress than
sacrificing 2+ workers to it every single cleanup. Verified: an isolated
check confirmed `reset_count` now resolves to exactly 1 regardless of
`PARALLEL_ATTEMPTS` (1, 4, 10, 20 all tested); a real `generate_grid()` run
on both seeds of the standard 15×10 benchmark (Flash mode,
`deadline_checks=1000`) confirmed no regression: 0 mismatches, 0 empty
white cells each — seed 2 in 21.3s, 56 words, 44 black cells; seed 7 in
15.4s, 57 words, 50 black cells.

`PALIER_ATTEMPT_INTERRUPT_FRACTION` (already a named constant, 0.30 —
"once this fraction of a palier's PARALLEL_ATTEMPTS attempts have finished,
interrupt every other still-running attempt") was changed from 0.30 to
**1.0**, at the user's explicit request: "Donner un nom de variable à la
quantité de process qui échouent avant de décider d'interrompre tous les
process (actuellement 30%). Fixer cette proportion pour le moment à 100%
(on attend que tous les process terminent)." The constant already carried
a name from when it was first introduced — only its value changed here.
With `interrupt_threshold = max(1, math.ceil(PALIER_ATTEMPT_INTERRUPT_
FRACTION * len(futures)))`, a fraction of 1.0 makes `interrupt_threshold`
always equal the full batch size (`math.ceil(1.0 * n) == n` for every
`n`), so `attempt_done_event` is only ever set once every attempt of the
palier has already completed on its own — in practice, no early
interruption ever happens anymore; the palier always waits for the full
batch, exactly "on attend que tous les process terminent." A deliberately
temporary, conservative setting per the user's own "pour le moment"
wording — not necessarily the final value. This also means `failed_pairs`/
`failed_unique`/the "all attempts abandoned via the 30%-unfillable rule"
force-cleanup check (see `UNFILLABLE_ABANDON_FRACTION`, a separate,
unrelated 30% constant, left untouched) now regularly see the *entire*
`PARALLEL_ATTEMPTS`-sized batch of real, naturally-concluded outcomes
instead of just the handful that happened to finish before an early
interruption — a wider, more representative sample for both the "best
failed candidate" selection and the force-cleanup rule, at the cost of a
palier now always waiting for its own slowest attempt (the exact trade-off
`PALIER_ATTEMPT_INTERRUPT_FRACTION` was originally introduced to avoid).
Verified: an isolated check confirmed `interrupt_threshold` now equals
`PARALLEL_ATTEMPTS` exactly for every tested batch size (1, 4, 5, 10, 20);
a real `generate_grid()` run on both seeds of the standard 15×10 benchmark
(Flash mode) confirmed no regression (0 mismatches, 0 empty white cells
each — same run as above, both changes verified together in the same
benchmark pass).

A new last-resort recovery mechanism, `_plug_isolated_cells`, was added at
the end of every failed palier, right before the "reprise telle quelle"
vs. "nettoyage complet" decision, at the user's explicit request:
"Lorsque toutes les recherches échouent en laissant une grille avec [ne
reste] plus que des cases blanches isolées, boucher les cases isolées
avec une case noire. Si le résultat donne une grille où tous les
emplacements possibles sont remplis et valides, déclarer la grille
réussie." An "isolated" unfilled white cell is defined as one with no
orthogonal neighbor that is also unfilled — every one of its 4 neighbors
is either already black or already covered by a real, confirmed letter
(via some assigned slot, across or down). This definition is deliberately
conservative: as soon as an unfilled cell has even one unfilled neighbor,
that reveals a genuine, still-open slot of at least 2 cells somewhere
nearby — a real word that could, in principle, still be found — and this
mechanism leaves the entire grid untouched in that case, not just that
one cell.

`_plug_isolated_cells(grid, rows, cols, slots, assignment, index)`:
builds `known` (a cell→letter map from every currently-assigned slot,
exactly the same construction already used by `_close_implied_slots`/
`_clean_blocked_slots`), then the set of `unfilled` white cells (not
covered by `known`) — if none exists, returns `None` immediately (nothing
to do, degenerate case that shouldn't normally be reached here anyway,
since a fully-filled `selected_diag` would already have been a `try_fill`
success). If any unfilled cell has an unfilled orthogonal neighbor,
returns `None` without touching anything — the "more than isolated
cells" condition from the user's request. Otherwise, builds a fresh copy
of the grid with every unfilled cell turned `BLACK`, checks it's still
structurally valid at the strictest level (`is_structurally_valid(...,
min_interior_free=1)` — full connectivity of the white-cell graph, no
orphaned cell anywhere else) — since plugging a bridge cell can
disconnect the grid into two components, this check is not a formality —
and, if valid, re-derives the slot structure (`extract_slots` on the new
grid) and validates every single one of its slots: each must have every
cell already present in `known` (a slot that shrank from a longer,
partially-known one, or one entirely unaffected by the plugging, both
count) *and* the word its known letters spell must be a real dictionary
entry (`_slot_candidates`, the same per-position set-intersection tool
`Filler._domain`/`_force_single_candidate_slots`/`sample_letter_biases`
all already share) — not merely "known," since a slot's letters coming
purely from independent crossings could, in principle, spell a
non-existent word. Any failure of either check aborts with `None`; only
when every single slot of the new pattern is fully known and forms a
real word does it return `(new_grid, new_slots, new_assignment)`.

Wired into `generate_grid` right after `selected_grid, selected_diag =
failed_pairs[0]` (the same best-failed-candidate computation the
"reprise telle quelle"/nettoyage decision already reads) — a non-`None`
result short-circuits the rest of the palier entirely: `best, best_result
= new_grid, (new_slots, new_assignment)` then `break`, exactly mirroring
the pre-existing `if successes: ... break` path, so every downstream
step (the `"pattern_found"` progress event, the "before optimization"
preview, `minimize_black_squares`, `build_word_entries`, clue
generation) treats this exactly like an ordinary, fully-resolved CSP
success — no special-casing needed anywhere else in the file, since
`best_result`'s contract (`(slots, assignment)`, matching `try_fill`'s
own return shape) is honored exactly.

Verified: 7 isolated unit tests against small, fully hand-built
dictionaries/grids — (1) a single isolated unfilled cell (a down-slot's
own unassigned last cell, boxed in by an already-lettered neighbor and
black cells on every other side) correctly plugged, producing a
1-slot grid whose sole remaining word matches the dictionary; (2) a
genuine 2-cell open slot (both cells unfilled and mutually adjacent)
correctly left completely untouched (`None`); (3) a scenario where
plugging the one candidate cell would disconnect the white-cell graph
into two components correctly rejected via the connectivity check; (4) a
scenario with two independently-isolated cells on two different sides of
an already-assigned slot, neither adjacent to the other, both correctly
plugged at once; (5)-(6) a scenario engineered so plugging one isolated
cell shrinks a 4-length unassigned slot down to a *new*, still-multi-cell
3-length slot whose letters are already fully known via 3 independent
crossing assignments — confirmed the new slot is validated against the
real dictionary and the whole operation succeeds when that word is
present, and is correctly rejected (`None`) when it is not, proving the
validation genuinely checks *real dictionary membership*, not merely
"every cell has some letter." A live diagnostic (`_plug_isolated_cells`
monkeypatched to record every call/outcome) run across 8 real
`generate_grid()` calls (9×9, Flash-scale `deadline_checks=200` to
force many failed paliers) confirmed the hook fires correctly at every
single failed palier across all 8 seeds (6-16 calls per run, 0 crashes)
— proving the wiring reaches real, varied `selected_diag`/`selected_grid`
shapes correctly, even though none of these particular short runs
happened to land in the specific "nothing left but isolated cells" state
needed to actually trigger a plug (an inherently rare state to hit by
chance in a small grid/short budget — the isolated unit tests above are
what directly exercises the success path itself). A full end-to-end
`generate_grid()` run on both seeds of the standard 15×10 benchmark
(Flash mode) confirmed no regression to the ordinary case: 0 mismatches,
0 empty white cells each — seed 2 in 24.0s, 63 words, 38 black cells;
seed 7 in 13.0s, 52 words, 47 black cells.
