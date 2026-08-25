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

Output format is deliberately plain text, not JSON: one line per word,
"WORD: clue 1; clue 2; clue 3". Small local models without constrained
decoding were unreliable at producing syntactically valid JSON (mismatched
quotes/brackets/escaping) — a flat line format has far fewer ways to come
out unparseable, and a half-broken line just loses that one word's clues
rather than derailing the whole response.

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
import os
import random
import re
import unicodedata
from pathlib import Path

import httpx

from .example_sentences import find_examples_for_words
from .gloss_lookup import find_glosses_for_canonicals

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
# French/Spanish/Italian, so their rule4_bad/rule4_good examples lean on
# what those languages actually have: modal auxiliaries, participles,
# Konjunktiv II, irregular plurals).
PROMPT_CONFIG_DIR = Path(__file__).resolve().parent.parent / "data"
_prompt_config_cache = {}


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

# One line per word: "word: clue 1; clue 2; clue 3", tolerating a leading
# bullet/number the model adds despite being asked not to (e.g. "- word:"
# or "1. word:"). Matched against the batch's known words (case-insensitive)
# before being trusted as a header line — see _parse_response — since a
# clue can itself contain a colon.
_WORD_LINE_RE = re.compile(r"^\s*(?:[-*•]|\d+[.)])?\s*([^:]+):\s*(.*)$")

# A clue given on its own line under a "word:" header instead of after the
# colon on the same line — models sometimes switch to this numbered/bulleted
# style despite being asked for one flat line. Marker is optional so a bare
# continuation line still counts.
_LIST_ITEM_RE = re.compile(r"^\s*(?:[-*•]|\d+[.)])?\s*(.+)$")

# A "1. "/"2)"/"- " marker left on an individual candidate — happens when the
# model numbers clues within the semicolon-separated line itself instead of
# (or as well as) using separate lines.
_LEADING_MARKER_RE = re.compile(r"^\s*(?:[-*•]|\d+[.)])\s*")

# DeepSeek-R1-distill models (unlike Qwen3.5 with `enable_thinking: false`,
# see run_llm.sh) always reason through a `<think>...</think>` block before
# the actual answer — there's no template flag to turn this off. Left in,
# the reasoning text itself risks false-triggering `_WORD_LINE_RE` (a model
# thinking out loud about the word by name, followed by a colon, reads
# exactly like a header line) and contaminating `_parse_response`'s output
# with reasoning fragments instead of the deliberate final answer.
_THINK_BLOCK_RE = re.compile(r"^.*?</think>", re.DOTALL)


def _strip_reasoning(content):
    """Removes a leading `<think>...</think>` reasoning block, if present,
    so only the model's actual final answer ever reaches `_parse_response`.
    A no-op for a model that doesn't emit one (e.g. Qwen3.5 with thinking
    disabled) — `<think>` never appears in its output at all. If the closing
    `</think>` is missing (the reasoning itself ran out of `max_tokens`
    before ever reaching an answer), returns "" rather than the raw
    in-progress reasoning text — `_parse_response` already treats empty/
    unparsable content as "no clue yet, retry next round", which is the
    correct outcome here."""
    if "<think>" not in content:
        return content
    stripped, count = _THINK_BLOCK_RE.subn("", content, count=1)
    return stripped if count else ""


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


def _chunks(items, size):
    for i in range(0, len(items), size):
        yield items[i:i + size]


def _contains_target_word(candidate, answer, accented):
    """True if the word being defined — the grid's bare form or its
    accented spelling, matched case- and accent-insensitively — appears
    anywhere in `candidate` as a whole word: either the clue is just the
    word itself (old "copy" case), or the word is embedded inside a longer
    sentence (e.g. "je serais s'il pleuvait demain" to define "serais") —
    both give away the answer and neither is an actual clue."""
    targets = {_normalize(answer), _normalize(accented)}
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
        after every word attempt (one LLM call each, `_BATCH_SIZE=1`) —
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
        pending = entries
        # A word can end up with no clue after filtering (every candidate
        # was a copy of the word itself, or non-Latin drift) just as easily
        # as from the LLM never answering for it — either way, ask again
        # rather than leaving it without a clue. Each round only re-sends
        # words still missing one, so this shrinks fast; capped so a word
        # the model just can't do (e.g. a stubborn acronym) doesn't loop
        # forever.
        for _round in range(3):
            if not pending:
                break
            for batch in _chunks(pending, _BATCH_SIZE):
                entry = batch[0]
                accented_to_answer = {entry[1]: entry[0]}
                system_prompt = self._build_system_prompt(difficulty, language)
                user_message = self._build_user_message(entry, language)
                max_tokens = REASONING_TOKEN_BUDGET + 300 + 90 * len(batch)
                try:
                    content = self._call(system_prompt, user_message, max_tokens, timeout)
                    raw = self._parse_response(content, accented_to_answer.keys())
                    clues.update(self._pick_clues(raw, accented_to_answer))
                except ClueGenerationError as e:
                    errors.append(e)
                if on_progress:
                    on_progress(len(clues), total)
            pending = [e for e in pending if e[0] not in clues]

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
        on the specific word — role, difficulty style, rules, and a
        clearly delimited EXAMPLES section illustrating them — sent as the
        `system` message; kept separate from the HTTP/parsing plumbing
        below. Pairs with `_build_user_message()`, which carries the one
        thing that *does* vary per call: the word itself plus its
        grounding block.

        Every concrete word/clue example (and the subject-pronoun list
        rule 4 names) comes from PROMPT_CONFIG_DIR/<language>_prompt_
        config.json, not hardcoded here — see _load_prompt_config."""
        config = _load_prompt_config(language)
        style = DIFFICULTY_STYLE.get(difficulty, DIFFICULTY_STYLE["medium"])
        language_name = LANGUAGE_NAMES.get(language, LANGUAGE_NAMES["fr"])
        diff_examples = config["difficulty_examples"]
        diff_example = diff_examples.get(difficulty, diff_examples["medium"])
        style_line = f'{style} Example: for {diff_example["word"]}, "{diff_example["clue"]}"'
        rule2_lines = (
            [f"Bad: {b}" for b in config["rule2_bad"]]
            + [f"Good: {g}" for g in config["rule2_good"]]
        )
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
            "root) is also forbidden.\n"
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
            "a same-meaning idea in a different form.\n"
            "5. The clue must reflect the word's actual, real meaning — "
            "never an unrelated sentence that merely sounds plausible. "
            "Check any dictionary definition(s)/example(s) you were given "
            "and confirm your clue actually corresponds to that meaning, "
            "rather than free-associating a clue that merely sounds like "
            "it could be one.\n"
            "6. A synonym or near-synonym is a perfectly good clue.\n"
            "7. Keep each candidate clue to one short line.\n\n"
            "=== EXAMPLES ===\n"
            "These illustrate the rules above using words other than the "
            "one you are actually being asked about — never reuse them as "
            "your answer.\n\n"
            "Rule 1 (never repeat the word) — bad:\n"
            f"{_bullets(config['rule1_bad'])}\n\n"
            "Rule 2 (never a bare grammatical label) — bad vs. good:\n"
            f"{_bullets(rule2_lines)}\n\n"
            "Rule 3 (never describe spelling/letters) — bad:\n"
            f"{_bullets(config['rule3_bad'])}\n\n"
            "Rule 4 (exact conjugation/number/gender agreement) — bad:\n"
            f"{_bullets(config['rule4_bad'])}\n\n"
            "Rule 4 (exact conjugation/number/gender agreement) — good:\n"
            f"{_bullets(config['rule4_good'])}\n\n"
            "Rule 5 (the real meaning, not an invented one) — bad:\n"
            f"{_bullets(config['rule5_bad'])}\n\n"
            "=== END OF EXAMPLES ===\n\n"
            "Respond with exactly one line, in this exact plain-text "
            "format (no JSON, no markdown, no numbering, no extra "
            "commentary):\n"
            "word: clue 1; clue 2; clue 3\n"
            "Use the exact accented spelling you were given before the "
            "colon, then the 3 clues separated by semicolons."
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

    def _call(self, system_prompt, user_message, max_tokens, timeout):
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
        return _strip_reasoning(content)

    @staticmethod
    def _parse_response(content, known_words):
        """Parses the "word: clue 1; clue 2; clue 3" line format. A "word:"
        line is only trusted as a header when the word matches one of
        `known_words` — the words actually sent in this batch — since a
        clue can itself contain a colon; matched case- and accent-
        insensitively, since a model given "élevées" sometimes echoes back
        "elevees". Also tolerates a model that switches to a numbered/
        bulleted list of clues under the header instead of the requested
        single semicolon-joined line (a real, observed failure mode on
        small local models): any line that isn't itself a recognized header
        is treated as one more candidate for whichever word header came
        before it. Returns {word_as_given: [candidate, ...]}."""
        known = {}
        for w in known_words:
            known[w.lower()] = w
            known[_normalize(w)] = w
        result = {}
        current = None
        for line in content.splitlines():
            line = line.strip()
            if not line:
                continue
            header = _WORD_LINE_RE.match(line)
            header_word = header.group(1).strip() if header else None
            if header and (header_word.lower() in known or _normalize(header_word) in known):
                current = known.get(header_word.lower()) or known[_normalize(header_word)]
                rest = header.group(2).strip()
                if rest:
                    result.setdefault(current, []).extend(
                        _LEADING_MARKER_RE.sub("", c).strip()
                        for c in rest.split(";") if c.strip()
                    )
                continue
            if current is not None:
                item = _LIST_ITEM_RE.match(line)
                if item and item.group(1).strip():
                    result.setdefault(current, []).append(item.group(1).strip())
        if not result:
            raise ClueGenerationError(f"invalid LLM response (no parsable line): {content!r}")
        return result

    @staticmethod
    def _pick_clues(raw, accented_to_answer):
        """Picks one of the (up to 3) candidate clues per word at random —
        favors variety across regenerations of the same word, and keeps the
        choice out of the LLM's hands as requested. Drops any candidate
        that isn't actually a clue: empty, non-Latin-script drift, or the
        word being defined appearing anywhere in it, whether as the whole
        clue or embedded in a longer sentence (the prompt forbids this, but
        small local models sometimes do it anyway — see
        `_contains_target_word`) — a word left with zero candidates after
        filtering gets no entry here, which `generate()` reads as still
        needing a clue and retries."""
        clues = {}
        for key, candidates in raw.items():
            answer = accented_to_answer.get(key) or accented_to_answer.get(key.upper())
            if answer is None:
                continue
            candidates = [
                c for c in candidates
                if c and _NON_LATIN_RE.search(c) is None
                and not _contains_target_word(c, answer, key)
            ]
            if candidates:
                clues[answer] = random.choice(candidates)
        return clues
