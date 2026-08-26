#!/usr/bin/env python3
"""
Generates crossword clues via an OpenAI-compatible chat completions API.

All LLM handling lives in LLMClueGenerator below. One request per word:
each word in the solution gets its own call, keyed by its accented/inflected
spelling (not the grid's bare uppercase, accent-stripped form — see
backend/crossword_gen.py's `accents` map) so the model can respect gender,
number, and conjugation. The model is asked for 3 candidate clues per word
(for variety across regenerations); one is picked at random on our side —
the LLM doesn't pick for itself.

Each call sends two chat messages, not one: a `system` message
(`_build_system_prompt()`) holding everything that's the same on every
call — role, difficulty style, rules, worked examples — and a `user`
message (`_build_user_message()`) holding only what's specific to this
one word — its accented spelling plus its grounding block (dictionary
definitions/example sentences, when available). Both are written for a
single word throughout, not a batch — there is only ever one word per
call (`_BATCH_SIZE = 1` below), and the wording reflects that rather than
describing a list of words that never actually arrives.

Output format is the simplest thing that could work, given there's only
ever one word per call: 3 plain-text lines, one candidate clue per line,
nothing else — no JSON, and no word/label to echo back either.
`_parse_response()` just splits the response into lines; every non-empty
line is trusted directly as one candidate, no header or delimiter syntax
to get right. This replaced an earlier single-line "WORD: clue 1; clue 2;
clue 3" format that needed the model to echo the target word as a header
before any of the response could be trusted as belonging to it — a real,
observed failure mode on the local model: it would sometimes echo the
format template's own literal placeholder text ("word:", or a bare
"clue 2") instead of filling it in, which made an otherwise-fine answer
unparseable. With one word and 3 plain lines, there's no header to get
wrong and no template text left to leak (see the project-best-practices
SKILL for the two incidents that motivated dropping the structured
format rather than continuing to patch around it).

The LLM endpoint is configurable via three environment variables (see
env.sh at the project root):
  - LLM_BASE_URL : full chat-completions URL (default: local llama.cpp
    server, see run_llm.sh)
  - LLM_MODEL    : model name/id to request
  - LLM_API_KEY  : bearer token (default "EMPTY" — llama.cpp ignores it
    unless configured to require one)
This lets the same code target a local llama.cpp server or a cloud API
(e.g. Mistral) just by changing env.sh, with no code change.
"""
import json
import logging
import os
import random
import re
import unicodedata
from datetime import datetime
from pathlib import Path

import httpx

from .example_sentences import find_examples_for_words
from .gloss_lookup import find_glosses_for_canonicals

# Child of backend/app.py's "crosswordfalcon" logger — same handler/format
# (configured once, by app.py's logging.basicConfig call), so these lines
# land in the same backend.log, just distinguishable by logger name. Added
# so a word that ends up showing the "no definition available" placeholder
# (see backend/svg_export.py's/frontend's _NO_DEFINITION) has a real,
# inspectable reason in the log instead of just vanishing silently — was
# it never answered by the model at all, did every candidate get rejected
# by our own copy/non-Latin/grammar filter, or did the HTTP call itself
# fail?
logger = logging.getLogger("crosswordfalcon.clues")

DEFAULT_LLM_BASE_URL = "http://127.0.0.1:8002/v1/chat/completions"
DEFAULT_LLM_MODEL = "Qwen/Qwen3.5-9B"
DEFAULT_LLM_API_KEY = "EMPTY"
# Generous relative to a non-reasoning model's ~2s/word (Qwen3/Qwen3.5 with
# thinking disabled): kept high enough to also cover DeepSeek-R1-Distill (a
# supported alternative, see env.sh), which reasons through a `<think>` block
# (see _strip_reasoning below) before every single word's answer — a request
# to whichever model is actually configured simply returns well before this
# ceiling, so one shared value works for both rather than needing to track
# which model is active.
DEFAULT_TIMEOUT = 300.0

# Added on top of the per-word answer budget below (max_tokens formula in
# generate()) so a reasoning model's `<think>` block has room to finish
# before the answer itself is due — a non-reasoning model (e.g. Qwen3.5 with
# `enable_thinking: false`) simply never uses this much and stops earlier,
# so it's harmless to always include. Calibrated against
# DeepSeek-R1-Distill-Qwen-14B directly: measured live across several words,
# a single word's full response (thinking + answer) ran anywhere from ~300
# to ~1300 tokens — kept comfortably above that observed high end.
REASONING_TOKEN_BUDGET = 2048

# Kept low (rather than a high-temperature "creative" setting) because the
# 3-candidates-per-word instruction in the prompt is what drives variety —
# a higher temperature mostly added a risk of the response degenerating
# (dropped entries, malformed output, off-language text) before finishing,
# especially on small local models without constrained decoding.
TEMPERATURE = 0.4

# A real, observed failure mode: for a hard/ambiguous word, the model can
# lapse into writing out its reasoning as if it were the answer itself
# (e.g. "Given the length (3 letters), it's likely an abbreviation... "
# "However, looking at the prompt rules: ...") instead of a short clue —
# several sentences long, sometimes quoting these very instructions back.
# A genuine crossword clue is always short; nothing legitimate needs more
# than a handful of words, so a hard word-count ceiling is a safe,
# language-agnostic way to reject this whole failure mode outright rather
# than trying to detect "sounds like reasoning" — see _pick_clue. Also
# spelled out in the prompt itself (_build_system_prompt's rule 7) so the
# model is asked for this directly, not just filtered after the fact.
MAX_CLUE_WORDS = 20

# Even a modest batch (5-6 words) was unreliable on the small local model —
# it would produce good clues for the first couple of words then degrade
# into empty/off-topic/malformed lines for the rest of the same response.
# One word per call sidesteps that entirely: every request is as simple as
# the model can be given, at the cost of one HTTP round-trip per word
# instead of one per handful — see generate()'s retry loop and
# frontend/server.py's generous proxy timeout, both already sized to
# absorb many sequential calls per grid.
_BATCH_SIZE = 1

# A worked example per level, not just an abstract description — small
# models follow a concrete style anchor far more reliably than an adjective
# list (verified: without an example, "easy" and "hard" clues came out
# barely distinguishable for the same word). The style description itself
# is language-agnostic prose (this project's engineering language); the
# worked example word/clue pair appended to it is language-specific and
# comes from PROMPT_CONFIG_DIR/<lang>_prompt_config.json's
# "difficulty_examples" instead (see _load_prompt_config/_build_system_
# prompt) — it used to be hardcoded here in French only, regardless of
# which language the grid/clue was actually in.
DIFFICULTY_STYLE = {
    "easy": (
        "very easy: simple, literal, everyday vocabulary, no wordplay, no "
        "ambiguity — a clue a child could answer."
    ),
    "medium": (
        "medium: classic newspaper-crossword style — reworded and a "
        "little indirect, but still fair, no trick needed to get it."
    ),
    "hard": (
        "hard: elliptical and witty — puns, double meanings, misdirection, "
        "figurative or cultural references, expert-level grid style."
    ),
}

# Language the words/clues are written in — must match the grid's wordlist
# (see backend/app.py's WORDLISTS). The clue itself is written in this same
# language, not translated to another one.
LANGUAGE_NAMES = {
    "fr": "French",
    "en": "English",
    "de": "German",
    "es": "Spanish",
    "it": "Italian",
}

# Every worked example in the system prompt below — the difficulty-style
# example, every "bad"/"good" illustration for rules 1-5, the ~20-example
# inflection-agreement bank, and the list of subject pronouns rule 4 names
# — used to be hardcoded in French, regardless of which language the grid/
# clue was actually being generated in (the model was just expected to
# generalize the underlying grammatical *concept* to the target language).
# Moved out to one JSON file per language
# (PROMPT_CONFIG_DIR/<lang>_prompt_config.json) so a German, Spanish,
# Italian, or English request is illustrated with real, grammatically
# verified words and clues in that language instead. See
# data/fr_prompt_config.json for the schema (every key this loader/
# _build_system_prompt expects) — each of the other four languages'
# content was authored to fit that language's own grammar rather than
# forcing a French-shaped template onto it (e.g. English and German have
# no single-word synthetic future/conditional for most verbs, unlike
# French/Spanish/Italian, so their rule_bad/rule_good examples lean on
# what those languages actually have: modal auxiliaries, participles,
# Konjunktiv II, irregular plurals).
PROMPT_CONFIG_DIR = Path(__file__).resolve().parent.parent / "data"
_prompt_config_cache = {}

# Where a word that exhausts all 3 retry attempts without ever getting a
# clue gets its own diagnostic Markdown file — see generate()'s call to
# _write_failure_log(). Project root, gitignored — a debugging artifact
# for reproducing a specific failure by hand, not source content or a
# durable record like GRIDS/ or GRID_SAMPLES/.
FAILURE_LOG_DIR = Path(__file__).resolve().parent.parent / "LOG"


def _load_prompt_config(language):
    if language not in _prompt_config_cache:
        path = PROMPT_CONFIG_DIR / f"{language}_prompt_config.json"
        if not path.exists():
            language = "fr"
            path = PROMPT_CONFIG_DIR / "fr_prompt_config.json"
        with open(path, encoding="utf-8") as f:
            _prompt_config_cache[language] = json.load(f)
    return _prompt_config_cache.get(language) or _prompt_config_cache["fr"]


def _bullets(items):
    return "\n".join(f"- {item}" for item in items)

# A "1. "/"2)"/"- " marker (or an em/en-dash variant of the same thing —
# "— " and "– ", both real, observed introductory-dash styles distinct
# from a plain hyphen) left on an individual line — happens when the
# model numbers/bullets its 3 lines despite being asked for plain,
# unnumbered ones. The only structural cleanup _parse_response still
# does, now that there's no header/delimiter syntax left to validate —
# everything else in a non-empty line is trusted as-is.
_LEADING_MARKER_RE = re.compile(r"^\s*(?:[-–—*•]|\d+[.)])\s*")


# DeepSeek-R1-distill models (unlike Qwen3.5 with `enable_thinking: false`,
# see run_llm.sh) always reason through a `<think>...</think>` block before
# the actual answer — there's no template flag to turn this off. Left in,
# the reasoning text would be parsed as if it were real candidate lines
# (see _parse_response, which now trusts every non-empty line directly),
# contaminating the output with reasoning fragments instead of the
# deliberate final answer.
_THINK_BLOCK_RE = re.compile(r"^.*?</think>", re.DOTALL)


def _strip_reasoning(content):
    """Removes a leading `<think>...</think>` reasoning block, if present,
    so only the model's actual final answer ever reaches `_parse_response`.
    A no-op for a model that emits neither tag (e.g. Qwen3.5 with thinking
    disabled). Gates on `</think>` specifically, not `<think>` — some
    chat-template/server setups inject the opening `<think>` as part of
    the *prompt* itself rather than echoing it back in the completion's
    `content` field, so a real response can start directly with raw
    reasoning text and only a stray `</think>` marking where it ends,
    with no literal `<think>` anywhere in `content` at all; gating on
    `<think>` alone (an earlier version of this function did) would skip
    stripping entirely in that case and leak the reasoning text straight
    into `_parse_response`. If `<think>` is present with no closing
    `</think>` (the reasoning itself ran out of `max_tokens` before ever
    reaching an answer), returns "" rather than the raw in-progress
    reasoning text — `_parse_response` already treats empty content as
    "no clue yet, retry next round", which is the correct outcome here."""
    if "</think>" in content:
        stripped, count = _THINK_BLOCK_RE.subn("", content, count=1)
        return stripped if count else content
    if "<think>" in content:
        return ""
    return content


def _normalize(word):
    """Lowercased, accent-stripped form, used to match a word the model
    echoed back without its accent against the accented form we sent it."""
    stripped = "".join(
        c for c in unicodedata.normalize("NFKD", word)
        if not unicodedata.combining(c)
    )
    return stripped.lower()

# All five supported languages (fr/en/de/es/it) use the Latin alphabet —
# small local models occasionally drift into a CJK/Cyrillic/Hebrew/etc.
# fragment mid-clue (seen in testing); reject any candidate that does.
_NON_LATIN_RE = re.compile(
    "["
    "Ͱ-῿"    # Greek, Cyrillic, Armenian, Hebrew, Arabic, Indic scripts...
    "　-鿿"    # CJK punctuation, Hiragana, Katakana, CJK ideographs
    "가-퟿"    # Hangul
    "＀-￯"    # fullwidth/halfwidth CJK forms
    "]"
)

# Splits a clue into whole words (letters only, any script/accents) so a
# containment check can match a *word*, not a raw substring — a clue
# mentioning "château" shouldn't be flagged just because it contains the
# letters of "chat".
_WORD_TOKEN_RE = re.compile(r"[^\W\d_]+", re.UNICODE)


def _contains_target_word(candidate, answer, accented, canonical=()):
    """True if the word being defined — its bare/accented spelling, or one
    of its candidate canonical form(s)/lemma(s) (see backend/crossword_gen.
    py's `words[i]["canonical"]`) — matched case- and accent-insensitively,
    appears anywhere in `candidate` as a whole word: the clue is just the
    word itself (old "copy" case), the word is embedded inside a longer
    sentence (e.g. "je serais s'il pleuvait demain" to define "serais"), or
    a same-family word from the same root is used instead (e.g. singular
    "maman" to define plural "MAMANS" — rule 1 forbids this, but the model
    does it anyway; `canonical` is what lets this specific case be caught
    automatically, since "maman" is MAMANS' own Hunspell-derived lemma) —
    all three give away the answer and none is an actual clue. This still
    can't catch every same-family leak (only ones matching a known
    canonical form exactly, not a fully general stem check), but is a real
    improvement over checking the target word alone."""
    targets = {_normalize(answer), _normalize(accented)}
    targets.update(_normalize(c) for c in canonical)
    tokens = {_normalize(t) for t in _WORD_TOKEN_RE.findall(candidate)}
    return bool(targets & tokens)


class ClueGenerationError(RuntimeError):
    """Raised when the LLM call fails or returns an unusable response."""


class LLMClueGenerator:
    """Talks to an OpenAI-compatible chat-completions endpoint to write
    crossword clues. Endpoint configuration (LLM_BASE_URL/LLM_MODEL/
    LLM_API_KEY) is read once, at construction time, from the environment.

    Usage: one instance is enough for the process's lifetime — construct
    once (e.g. at module level in backend/app.py) and call `generate()`
    per grid.
    """

    def __init__(self):
        self.base_url = os.environ.get("LLM_BASE_URL", DEFAULT_LLM_BASE_URL)
        self.model = os.environ.get("LLM_MODEL", DEFAULT_LLM_MODEL)
        self.api_key = os.environ.get("LLM_API_KEY", DEFAULT_LLM_API_KEY)

    def generate(self, word_entries, difficulty, language="fr", timeout=DEFAULT_TIMEOUT,
                 on_progress=None):
        """`word_entries` is an iterable of (answer, accented, canonical)
        triples — `answer` is the grid's bare uppercase form (used as the
        returned dict's key, to match backend/crossword_gen.py's
        `words[i]["answer"]`), `accented` is its natural accented/inflected
        spelling (see crossword_gen.load_wordlist), shown to the LLM instead
        of `answer` so it can write a grammatically accurate clue, and
        `canonical` is its candidate canonical form(s)/lemma(s) (a list —
        more than one when genuinely ambiguous, e.g. French "suis" -> "être"
        or "suivre"; see backend/crossword_gen.py's `words[i]["canonical"]`),
        used to look up a real dictionary definition (backend/gloss_lookup.py)
        for extra grounding. Returns {ANSWER: clue}, written in `language`
        (fr/en/de/es/it), in the style matching `difficulty` (easy/medium/hard).

        `on_progress`, if given, is called `on_progress(current, total)`
        after every attempt (one LLM call each, `_BATCH_SIZE=1`) —
        `current` is how many words have a clue so far, `total` how many
        were asked for; used to surface live progress (see backend/app.py)
        since this is by far the slowest phase of grid generation."""
        entries = list({
            (answer.upper(), accented, tuple(canonical))
            for answer, accented, canonical in word_entries
        })
        if not entries:
            return {}
        total = len(entries)

        clues = {}
        errors = []
        max_tokens = REASONING_TOKEN_BUDGET + 300 + 90 * _BATCH_SIZE
        # A word can end up with no clue after filtering (every candidate
        # was a copy of the word itself, or non-Latin drift) just as easily
        # as from the LLM never answering for it — either way, ask again
        # rather than leaving it without a clue. Retries happen immediately,
        # 3 attempts in a row on the *same* word, before moving to the next
        # one — not spread one-attempt-per-word across 3 passes over the
        # whole list (an earlier design, changed at the user's explicit
        # request: with that design, a word's own final, third attempt
        # only happened after every other word's first attempt had already
        # run, which read confusingly in the log — two consecutive "round
        # 1/3" lines for two different words look like a retry that
        # silently moved on, when it's really just two different words'
        # first attempts).
        for entry in entries:
            answer, accented, canonical = entry
            system_prompt = self._build_system_prompt(difficulty, language)
            user_message = self._build_user_message(entry, language)
            last_content = None
            last_error = None
            for attempt in range(3):
                last_content = None
                last_error = None
                try:
                    content = self._call(
                        answer, accented, attempt + 1,
                        system_prompt, user_message, max_tokens, timeout,
                    )
                    last_content = content
                    candidates = self._parse_response(content)
                    if not candidates:
                        logger.warning(
                            "clue round %d/3: %r (%r) — model gave no "
                            "candidate lines at all",
                            attempt + 1, answer, accented,
                        )
                    else:
                        clue = self._pick_clue(candidates, answer, accented, canonical)
                        if clue:
                            clues[answer] = clue
                        else:
                            logger.warning(
                                "clue round %d/3: %r (%r) — all %d candidate(s) "
                                "rejected by the too-long/copy/non-Latin/"
                                "same-family filter: %r",
                                attempt + 1, answer, accented, len(candidates), candidates,
                            )
                except ClueGenerationError as e:
                    errors.append(e)
                    last_error = e
                    logger.warning(
                        "clue round %d/3: %r (%r) — LLM call failed: %s",
                        attempt + 1, answer, accented, e,
                    )
                if on_progress:
                    on_progress(len(clues), total)
                if answer in clues:
                    break
            if answer not in clues:
                self._write_failure_log(
                    answer, accented, language, difficulty,
                    system_prompt, user_message, last_content, last_error,
                )

        missing = [e for e in entries if e[0] not in clues]
        if missing:
            logger.warning(
                "clue generation: %d/%d word(s) still have no clue after all "
                "retry rounds (will show as the \"no definition available\" "
                "placeholder) — see the per-round warnings above for why "
                "each one failed: %s",
                len(missing), total, [e[0] for e in missing],
            )

        if errors and not clues:
            raise errors[0]
        return clues

    @staticmethod
    def _build_examples_block(entry, language):
        """Real sentences (from the OpenSubtitles+Wikipedia reference
        corpus, see backend/example_sentences.py) using this word's exact
        accented form, if any exist — grounds the model's sense of what a
        rare or ambiguous word actually means instead of leaving it to
        guess (a real, observed failure: the small local model defined
        French `are` — the 100 m² land-area unit — as the English verb "to
        be", since it had never reliably learned the rare French sense).
        Returns "" (no section added) when no example sentences were found
        for this word."""
        _, accented, _ = entry
        sentences = find_examples_for_words([accented], language).get(accented)
        if not sentences:
            return ""
        lines = "\n".join(f"- {s}" for s in sentences)
        return (
            f'Real example sentences using "{accented}":\n{lines}\n\n'
            "These are genuine sentences, not hints about difficulty or "
            "style — use them only to confirm what the word actually means "
            "(this matters most for short or unusual words that might look "
            "like a word from another language) before writing your clues."
        )

    @staticmethod
    def _build_gloss_block(entry, language):
        """Real dictionary definitions (from Wiktionary via Kaikki.org, see
        backend/gloss_lookup.py) for this word's candidate canonical
        form(s), if any exist. Looked up by canonical form/lemma, not the
        grid's inflected spelling — a genuinely ambiguous word (French
        "suis" -> "être" or "suivre") can have more than one candidate, in
        which case every one found is shown so the model resolves the
        ambiguity using the word's actual clue-writing context, rather
        than a definition dictionary silently picking one for it in
        advance. Returns "" when this word has no canonical form with
        dictionary coverage."""
        _, accented, canonical = entry
        glosses_by_lemma = find_glosses_for_canonicals(canonical, language)
        word_parts = [
            f'- "{lemma}" ({sense["pos"]}): {gloss}'
            for lemma in canonical
            for sense in glosses_by_lemma.get(lemma, [])
            for gloss in sense["glosses"]
        ]
        if not word_parts:
            return ""
        return (
            f'Dictionary definition(s) related to "{accented}":\n'
            + "\n".join(word_parts) + "\n\nThese are real dictionary "
            "definitions of the word's root form(s) — use them to confirm "
            "the actual meaning before writing your clues. If more than one "
            "is shown, only one may be the meaning that fits this particular "
            "word; use the one that makes sense, ignore the others."
        )

    def _build_system_prompt(self, difficulty, language):
        """All of the crossword-clue-writing instructions that don't depend
        on the specific word — role, difficulty style, rules, a clearly
        delimited EXAMPLES section illustrating them, and the final
        output-format instructions — sent as the `system` message; kept
        separate from the HTTP/parsing plumbing below. Pairs with
        `_build_user_message()`, which carries the one thing that *does*
        vary per call: the word itself plus its grounding block. Identical
        across every word for a given difficulty/language, so this could
        be cached per (difficulty, language) pair rather than rebuilt on
        every call — not done, since rebuilding a string is cheap relative
        to the LLM call it precedes.

        The output-format instructions ask for exactly 3 plain lines, one
        candidate clue per line — no word/label for the model to echo
        back, unlike an earlier "word: clue 1; clue 2; clue 3" format that
        needed the target word repeated as a header before any of the
        response could be trusted (a real, observed failure mode: the
        model would sometimes echo the format template's own literal
        placeholder text instead of filling it in correctly). See
        `_parse_response()` and the project-best-practices SKILL for the
        two incidents that motivated dropping the structured single-line
        format entirely.

        Every concrete word/clue example (and the subject-pronoun list
        rule 4 names) comes from PROMPT_CONFIG_DIR/<language>_prompt_
        config.json, not hardcoded here — see _load_prompt_config."""
        config = _load_prompt_config(language)
        style = DIFFICULTY_STYLE.get(difficulty, DIFFICULTY_STYLE["medium"])
        language_name = LANGUAGE_NAMES.get(language, LANGUAGE_NAMES["fr"])
        diff_examples = config["difficulty_examples"]
        diff_example = diff_examples.get(difficulty, diff_examples["medium"])
        style_line = f'{style} Example: for {diff_example["word"]}, "{diff_example["clue"]}"'
        return (
            f"You are a crossword compiler writing in {language_name}, at "
            f"{difficulty.upper()} difficulty. This difficulty level is the "
            f"single most important constraint on every clue you write:\n"
            f"{style_line}\n\n"
            "The user message will give you a single word to write a clue "
            "for, in its correctly accented, inflected written form (right "
            "gender, number, and conjugation) — use that to write "
            "grammatically accurate clues. It may also include real "
            "dictionary definitions and/or real example sentences for that "
            "word; use them to confirm its actual meaning before writing.\n\n"
            "Propose exactly 3 different possible crossword clues for that "
            "single word, all matching the difficulty level above.\n\n"
            "Rules:\n"
            "1. Never include the word being defined anywhere in the clue "
            "— not as the whole answer, and not embedded inside a longer "
            "sentence either — in any spelling, case, or with/without "
            "accents. A same-family word (a different form of the same "
            "root) is also forbidden — including a different inflection "
            "of this exact same word (e.g. the masculine equivalent of a "
            "feminine target, or a different tense/person of the same "
            "verb): a near-identical variant still gives the answer away "
            "just as much as the exact spelling would, even though it "
            "isn't byte-for-byte the same.\n"
            "2. Do not write a bare grammatical/technical description — "
            "write an actual clue a crossword solver would enjoy, not a "
            "label. Describe what the word actually means.\n"
            "3. Do not describe the word's spelling or letters instead of "
            "its meaning. A clue must always be about the meaning, never "
            "the letters.\n"
            "4. The clue must match the word's EXACT inflected form in "
            "every way that applies — for a verb: person, number, AND "
            "mood/tense together; for a noun or adjective: number "
            "(singular/plural) and gender. Getting the general meaning "
            "right is never enough if the grammar doesn't match. Before "
            "answering, identify the word's specific grammatical form (for "
            f'a verb: its subject — {config["subject_pronouns"]} — and '
            "mood/tense; for a noun or adjective: singular or plural, and "
            "gender) and confirm your clue matches that exactly, not just "
            "a same-meaning idea in a different form. Two specific traps: "
            "(a) a generic dictionary-style definition of the bare action "
            "or state (e.g. \"the act of doing X\") describes the "
            "infinitive, not a specific conjugated form — rephrase it so "
            "it is unmistakably tied to that exact person/tense instead; "
            "(b) if your clue names a person or thing to carry the word's "
            "adjective/participle (e.g. \"a house\", \"a runner\"), that "
            "noun must itself carry the SAME gender and number as the "
            "word being defined — never let it silently disagree.\n"
            "5. The clue must reflect the word's actual, real meaning — "
            "never an unrelated sentence that merely sounds plausible. "
            "Check any dictionary definition(s)/example(s) you were given "
            "and confirm your clue actually corresponds to that meaning, "
            "rather than free-associating a clue that merely sounds like "
            "it could be one.\n"
            "6. A synonym or near-synonym is a perfectly good clue.\n"
            f"7. Keep each candidate clue short: a single clause or "
            f"sentence, at most {MAX_CLUE_WORDS} words. Never write out "
            f"your reasoning or think out loud about the word (its "
            f"length, its letters, whether it might be an abbreviation, "
            f"etc.), and never self-correct inline — starting one answer, "
            f"then writing something like \"wait, no\" or \"actually\" "
            f"before giving a different one. Decide on your final answer "
            f"entirely on your own, before writing anything down, and "
            f"write only that one finished result — never discuss or "
            f"quote these instructions, and never leave a discarded first "
            f"attempt visible before the real one.\n"
            f"8. Write every clue entirely in {language_name} — the same "
            f"language as the word itself — from the very first word to "
            f"the last. Never switch to another language partway "
            f"through, even for a single stray word.\n\n"
            "=== EXAMPLES ===\n"
            "These illustrate the rules above using words other than the "
            "one you are actually being asked about — never reuse them as "
            "your answer.\n\n"
            "Examples of what NOT to do:\n"
            f"{_bullets(config['rule_bad'])}\n\n"
            "Examples of what TO do (correct conjugation, number, and "
            "gender agreement, and a real definition rather than a "
            "grammatical label):\n"
            f"{_bullets(config['rule_good'])}\n\n"
            "=== END OF EXAMPLES ===\n\n"
            "Respond with exactly 3 lines and nothing else: one candidate "
            "clue per line, in plain text. No JSON, no markdown, no "
            "numbering or bullets, no blank lines, no repeating the word "
            "itself anywhere, and no extra commentary before, between, or "
            "after the 3 lines — just the 3 clues, one per line."
        )

    def _build_user_message(self, entry, language):
        """The one thing that varies per call: the word itself, plus its
        grounding block (real dictionary definitions/example sentences,
        when available) — sent as the `user` message, paired with the
        fixed `system` message from `_build_system_prompt()`."""
        _, accented, _ = entry
        parts = [f"Word: {accented}"]
        gloss_block = self._build_gloss_block(entry, language)
        if gloss_block:
            parts.append(gloss_block)
        examples_block = self._build_examples_block(entry, language)
        if examples_block:
            parts.append(examples_block)
        return "\n\n".join(parts)

    def _call(self, answer, accented, round_number, system_prompt, user_message, max_tokens, timeout):
        try:
            response = httpx.post(
                self.base_url,
                headers={"Authorization": f"Bearer {self.api_key}"},
                json={
                    "model": self.model,
                    "messages": [
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": user_message},
                    ],
                    "temperature": TEMPERATURE,
                    "max_tokens": max_tokens,
                },
                timeout=timeout,
            )
            response.raise_for_status()
        except httpx.HTTPError as e:
            raise ClueGenerationError(
                f"LLM call failed ({self.base_url}, model={self.model!r}): {e}. "
                "If you're using the default local llama.cpp server, make "
                "sure it's running (./run_llm.sh); otherwise check "
                "LLM_BASE_URL/LLM_MODEL/LLM_API_KEY in env.sh."
            ) from e
        content = response.json()["choices"][0]["message"]["content"]
        # Logged here — the exact, unmodified text the LLM returned, before
        # _strip_reasoning touches it and before any of generate()'s own
        # parsing/filtering runs — so a deployed instance's log always has
        # the ground truth for what the model actually said, not just our
        # after-the-fact verdict on it (empty/rejected/etc.). Added after a
        # real diagnosis session where the *reason* a word ended up with no
        # clue couldn't be fully confirmed from the existing warning-only
        # logging alone.
        logger.info(
            "clue round %d/3: %r (%r) — raw LLM response: %r",
            round_number, answer, accented, content,
        )
        return _strip_reasoning(content)

    def _write_failure_log(self, answer, accented, language, difficulty,
                            system_prompt, user_message, content, error):
        """Writes a self-contained Markdown diagnostic file for a word
        that exhausted all 3 retry attempts without ever getting a clue —
        at the user's explicit request, so a specific failure can be
        analyzed and reproduced by hand without digging through backend.
        log. Captures the *last* attempt specifically (the complete
        system + user prompt — identical across all 3 attempts, since
        nothing about the prompt varies between retries — plus that
        attempt's raw output and/or error, e.g. a timeout): the retry
        loop already logs every attempt's own outcome as it happens (see
        _call()/generate()), so this file's job isn't to repeat that —
        it's a single artifact with everything needed to replay the
        exact request that ultimately failed. Written to FAILURE_LOG_DIR
        (LOG/, project root, gitignored — a debugging artifact, not a
        durable record like GRIDS/), one file per failed word, named
        `<timestamp>_<answer>_ERROR.md`. Best-effort, like backend/svg_
        export.py's own saves: a failure to write this is logged, never
        allowed to break grid generation, since a missing diagnostic
        file is far less important than the grid itself finishing."""
        FAILURE_LOG_DIR.mkdir(parents=True, exist_ok=True)
        timestamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
        path = FAILURE_LOG_DIR / f"{timestamp}_{answer}_ERROR.md"
        error_section = (
            str(error) if error is not None else
            "None — a response was received from the LLM, but it produced "
            "no usable clue (see backend.log for the exact rejection "
            "reason: no candidate lines, or every candidate filtered out "
            "as too long/a copy/non-Latin/same-family)."
        )
        output_section = content if content is not None else "(no response — see error above)"
        body = (
            f"# Clue generation failure — {answer} ({accented})\n\n"
            f"- **Date**: {datetime.now().isoformat()}\n"
            f"- **Language**: {language}\n"
            f"- **Difficulty**: {difficulty}\n"
            f"- **LLM endpoint**: {self.base_url}\n"
            f"- **Model**: {self.model}\n"
            f"- **Attempts**: 3/3 failed\n\n"
            f"## Error (last attempt)\n\n{error_section}\n\n"
            f"## System prompt (last attempt)\n\n```\n{system_prompt}\n```\n\n"
            f"## User message (last attempt)\n\n```\n{user_message}\n```\n\n"
            f"## Raw LLM output (last attempt)\n\n```\n{output_section}\n```\n"
        )
        try:
            path.write_text(body, encoding="utf-8")
        except OSError as e:
            logger.warning("failed to write failure log for %r: %s", answer, e)

    @staticmethod
    def _parse_response(content):
        """One candidate clue per line — safe now that there's only ever
        one word per call (`_BATCH_SIZE = 1`): there's no word/header to
        match a line against anymore, so every non-empty line is trusted
        directly as one candidate. The only cleanup still applied, before
        a line is trusted: normalizing a non-breaking space (U+00A0,
        which `str.strip()` alone doesn't remove — some models emit these
        instead of a plain space) to a regular one, then stripping a
        leading numbered/bulleted/dash marker (`_LEADING_MARKER_RE`) the
        model sometimes adds despite being asked for plain lines —
        everything else is used as-is, no delimiter syntax to get right.
        Returns a list of candidate strings (empty if the model's response
        had no non-empty lines at all)."""
        return [
            cleaned
            for line in content.splitlines()
            if (cleaned := _LEADING_MARKER_RE.sub("", line.replace("\xa0", " ")).strip())
        ]

    @staticmethod
    def _pick_clue(candidates, answer, accented, canonical):
        """Picks one of this word's (up to 3) candidate clues at random —
        favors variety across regenerations of the same word, and keeps
        the choice out of the LLM's hands as requested. Drops any
        candidate that isn't actually a clue: longer than `MAX_CLUE_WORDS`
        words (a real, observed failure mode — the model writing out its
        reasoning, several sentences long, instead of a short clue — see
        `MAX_CLUE_WORDS`'s comment), non-Latin-script drift, or the word
        being defined (or a same-family word sharing its canonical
        form/lemma, e.g. singular "maman" leaking into a clue for plural
        MAMANS) appearing anywhere in it, whether as the whole clue or
        embedded in a longer sentence (the prompt forbids this, but small
        local models sometimes do it anyway — see `_contains_target_word`).
        Returns None if every candidate was rejected, which `generate()`
        reads as still needing a clue and retries."""
        candidates = [
            c for c in candidates
            if c and len(c.split()) <= MAX_CLUE_WORDS
            and _NON_LATIN_RE.search(c) is None
            and not _contains_target_word(c, answer, accented, canonical)
        ]
        return random.choice(candidates) if candidates else None
