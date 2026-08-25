# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

**CrossWordFalcon** — a crossword grid generator (French, English, German, Spanish, or
Italian), usable from the CLI or from a web UI backed by two FastAPI servers:

- `build_sentence_corpus.py` — one-off preprocessing script: downloads a partial
  chunk (`--max-bytes`, default 50MB per source) of three OPUS (opus.nlpl.eu) corpora —
  OpenSubtitles (colloquial/dialogue vocabulary), Wikipedia (formal/technical
  vocabulary, and rare-but-real words dialogue rarely uses), and Books (literary/
  narrative prose, mostly older translated novels — a third, descriptive-vocabulary
  register neither dialogue nor encyclopedic text tends to use; added at the user's
  explicit request) — per language, merges
  them, and filters out sentences likely to contain a wrong-language part: dropped if
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
  launching the real, full-scale reprocessing for all 5 languages.
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
  down to very few (or zero) words is visible without guessing.
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
  are both ready, it calls `backend/svg_export.py` to save a durable copy to `GRIDS/`,
  then a PNG rendering of that same SVG to `GRID_SAMPLES/` (`save_grid_png`, via
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
  ~2s/word with the current default, Qwen3.5-9B (thinking disabled — this project's
  very first default), ~3s/word with Qwen3.5-4B unquantized (thinking disabled —
  smallest model, close to Qwen3.5-9B's speed despite being full-precision rather
  than quantized), ~8-9s/word with Qwen3-14B (also thinking disabled — larger model,
  same non-reasoning behavior, so slower per token but not per-word-reasoning-slow),
  ~20-40s/word (a 9×9 grid's 32 words took ~13 minutes of clue generation) with
  Qwen3.8-27B (thinking disabled, Unsloth Dynamic `UD-Q2_K_XL` — the largest
  non-reasoning model tried, and the slowest of the non-reasoning ones, but also the
  one with the strongest observed clue quality so far, see the project-best-practices
  SKILL — a good choice with a GPU with at least 12GB VRAM, per README.md), and
  20-70s/word (potentially 15-40+ minutes per grid) with
  DeepSeek-R1-Distill-Qwen-14B, since it reasons through a `<think>` block before every
  single word's answer — see `_strip_reasoning`/`REASONING_TOKEN_BUDGET` below and
  `run_llm.sh`. Output is plain text, not
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
  filtering, or that the model never answered at all, gets re-queried in a follow-up
  round — `generate()` loops up to 3 rounds, each re-sending only words still missing a
  clue. The prompt is split into a `system` message (`_build_system_prompt()` — role,
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
  `GRIDS/` (project root, gitignored — generated output, not source content), named
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
  a PNG of the same basename under `GRID_SAMPLES/` (project root) via the
  `rsvg-convert` CLI (part of `librsvg` — `brew install librsvg` / `apt-get install
  librsvg2-bin`; the same tool already used for `frontend/static/logo.png`, see the
  style-guide SKILL), at `PNG_DPI` (300, print quality rather than the screen-oriented
  96 DPI default) via `rsvg-convert -z (PNG_DPI / 96)` — `--dpi-x`/`--dpi-y` alone have
  no effect here (verified directly) since the SVG's root has no physical width/height
  unit (in/mm/pt) for librsvg to rescale against, only the zoom flag actually scales a
  unitless SVG's pixel output — unlike `GRIDS/`, `GRID_SAMPLES/` is deliberately **not**
  gitignored: a growing, checked-in visual record of what the app actually produces,
  meant to be committed and evolve across versions, at the user's explicit request.
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
  Mistral) instead.
- `frontend/server.py` — **middleware** FastAPI server: serves the static UI
  (`frontend/static/index.html`, `script.js`, `style.css`) and proxies `/api/*` to the
  backend (via `httpx`, base URL from `CROSSWORDFALCON_BACKEND_URL`, default
  `http://127.0.0.1:8001`) so the browser only ever talks to one origin. `run_Falcon.sh`
  binds it to `0.0.0.0` (LAN-reachable, e.g. from a phone on the same network) — the
  back end stays on `127.0.0.1` only, it's never meant to be reached directly. Proxies
  both `POST /api/generate` (now fast — the backend responds immediately with a
  `job_id`, see above — so this needs only a short timeout, not the long one a single
  blocking call used to require), `GET /api/generate/status/{job_id}` the
  frontend polls for progress, and `GET /api/system_info` (see `backend/system_info.py`
  below) for the info badge's tooltip. A blanket `@app.middleware("http")` sets
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
  `POLL_INTERVAL_MS`, 700ms) and turns each `{code, ...data}` step into a localized
  message via `describeStep()` — falls back to the generic "generating…" message for a
  step code it doesn't recognize, so an older frontend build never breaks against a
  newer backend. Errors are localized the same way: a backend/proxy error carries a
  machine-readable `code` (`error_code` on a job, or `{"code": ...}` as
  `frontend/server.py`'s own `HTTPException` detail when it can't reach the back end at
  all) that `describeErrorCode()` maps to the UI's current language, falling back to
  whatever raw text the backend sent for a code it doesn't recognize. UI
  styling decisions live in the `style-guide` SKILL, not here.
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
every generated grid to save a `GRID_SAMPLES/` PNG (see `backend/svg_export.py`
below), best-effort so a missing binary only logs a warning rather than failing the
request — which is exactly why this was easy to go unnoticed until a fresh machine
hit it.

```bash
# Full pipeline to rebuild one language's wordlist from scratch (only needed to
# refresh the source corpus/frequencies; data/wordlist_*.tsv are already checked
# into data/). Each language is independent — no particular build order required.
python3 build_sentence_corpus.py fr    # downloads OpenSubtitles+Wikipedia+Books, filters
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

# Web UI: run both servers (separate terminals), then open http://127.0.0.1:8000
uvicorn backend.app:app --port 8001                       # 127.0.0.1 only, internal
uvicorn frontend.server:app --host 0.0.0.0 --port 8000    # LAN-reachable

# Or simply:
./run_Falcon.sh   # stops any server already running on 8000/8001, sources
                  # env.sh (LLM_BASE_URL/LLM_MODEL/LLM_API_KEY) if present,
                  # then relaunches both

# Local LLM for clue generation (default — see env.sh to use a cloud API instead).
# Downloads the GGUF into models/ on first run, then serves it on :8002. Works
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

1. **Black-square pattern generation** (`make_symmetric_pattern`): places black
   cells in 180°-symmetric pairs, rejecting any placement that violates
   `is_structurally_valid` (no white run shorter than 3 cells, and all white cells must
   stay 4-connected). Picks each cell from a 32-cell look-ahead window (lowest
   row+column black-cell count wins) rather than strict shuffle order — a soft bias
   against black cells piling up in a few rows/columns ("walls" that split the grid
   into disconnected-looking blocks and force many neighboring words to share the
   same length). Real but partial fix: `minimize_black_squares` below washes out
   much of this bias since it removes cells by fillability alone, not aesthetics —
   see the `project-best-practices` SKILL for what was tried and rejected (a hard
   per-row/column cap cut alignment much further but cost ~55% more generation
   time). Retries with an increasing black-cell ratio (`--black-ratio`, up to
   `--attempts` times) until a fillable pattern is found.

2. **CSP fill** (`Filler` / `_backtrack`): `extract_slots` turns the black/white pattern
   into across/down word slots; `build_index` pre-indexes the word list by
   `(length, position, letter)` so slot domains can be computed by set intersection
   instead of a linear scan (needed because the full lexicon is 100k+ words). Backtracking
   search picks the most-constrained slot first (MRV heuristic) and respects a
   `deadline_checks` budget so a bad grid pattern fails fast instead of hanging. `Filler`
   takes the same seeded `rng` as pattern generation (not the global `random` module —
   that was a real bug: unreproducible fills and, in the web server, a shared-state race
   across concurrent requests) and shuffles candidate words with it. At the 15×10
   default, `_domain()` is still called for every unassigned slot at every backtracking
   node (MRV's cost), so a single `/api/generate` request commonly takes 15-35 seconds —
   this is expected, not a hang; don't "fix" it by adding a timeout without addressing
   the actual solver cost.

3. **Minimization** (`minimize_black_squares`): after a successful fill, greedily tries
   removing each symmetric pair of black cells and re-running the CSP fill; a removal is
   kept only if the grid is still structurally valid and fillable, otherwise it's
   reverted. This densifies the grid (fewer black squares) without ever downgrading from
   a known-good solution.

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
keeps the entire lexicon. The cap (`DIFFICULTY_PRESETS`: easy=25 000, medium=50 000,
hard=uncapped) is applied *globally* (ranked across all lengths together), not per
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
strips accents/diacritics and uppercases (crossword convention), excludes words under 3
letters after normalization, and keeps the occurrence count as the frequency. All five
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
