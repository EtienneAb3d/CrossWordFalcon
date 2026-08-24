#!/usr/bin/env python3
"""
Generates crossword clues via an OpenAI-compatible chat completions API.

All LLM handling lives in LLMClueGenerator below. One request per grid:
every word in the solution is sent at once, keyed by its accented/inflected
spelling (not the grid's bare uppercase, accent-stripped form — see
backend/crossword_gen.py's `accents` map) so the model can respect gender,
number, and conjugation. The model is asked for 3 candidate clues per word
(for variety across regenerations); one is picked at random on our side —
the LLM doesn't pick for itself.

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
import os
import random
import re
import unicodedata

import httpx

DEFAULT_LLM_BASE_URL = "http://127.0.0.1:8002/v1/chat/completions"
DEFAULT_LLM_MODEL = "Qwen/Qwen3.5-9B"
DEFAULT_LLM_API_KEY = "EMPTY"
DEFAULT_TIMEOUT = 120.0

# Kept low (rather than a high-temperature "creative" setting) because the
# 3-candidates-per-word instruction in the prompt is what drives variety —
# a higher temperature mostly added a risk of the response degenerating
# (dropped entries, malformed output, off-language text) before finishing,
# especially on small local models without constrained decoding.
TEMPERATURE = 0.4

# Small local models (no constrained decoding) reliably answer a handful
# of words but tend to degenerate well before
# hitting the token budget once the list gets long — batching keeps every
# request in the size range that's actually reliable (validated manually
# up to ~5-6 words per call).
_BATCH_SIZE = 6

# A worked example per level, not just an abstract description — small
# models follow a concrete style anchor far more reliably than an adjective
# list (verified: without an example, "easy" and "hard" clues came out
# barely distinguishable for the same word).
DIFFICULTY_STYLE = {
    "easy": (
        "very easy: simple, literal, everyday vocabulary, no wordplay, no "
        'ambiguity — a clue a child could answer. Example: for CHAT, '
        '"Animal domestique qui miaule."'
    ),
    "medium": (
        "medium: classic newspaper-crossword style — reworded and a "
        "little indirect, but still fair, no trick needed to get it. "
        'Example: for CHAT, "Compagnon à quatre pattes qui ronronne."'
    ),
    "hard": (
        "hard: elliptical and witty — puns, double meanings, misdirection, "
        "figurative or cultural references, expert-level grid style. "
        'Example: for CHAT, "Il retombe toujours sur ses pattes."'
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


def _chunks(items, size):
    for i in range(0, len(items), size):
        yield items[i:i + size]


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

    def generate(self, word_entries, difficulty, language="fr", timeout=DEFAULT_TIMEOUT):
        """`word_entries` is an iterable of (answer, accented) pairs —
        `answer` is the grid's bare uppercase form (used as the returned
        dict's key, to match backend/crossword_gen.py's
        `words[i]["answer"]`), `accented` is its natural accented/inflected
        spelling (see crossword_gen.load_wordlist), shown to the LLM instead
        of `answer` so it can write a grammatically accurate clue. Returns
        {ANSWER: clue}, written in `language` (fr/en/de/es/it), in the style
        matching `difficulty` (easy/medium/hard)."""
        entries = list({(answer.upper(), accented) for answer, accented in word_entries})
        if not entries:
            return {}

        clues = {}
        errors = []
        for batch in _chunks(entries, _BATCH_SIZE):
            accented_to_answer = {accented: answer for answer, accented in batch}
            prompt = self._build_prompt(batch, difficulty, language)
            max_tokens = 300 + 90 * len(batch)
            try:
                content = self._call(prompt, max_tokens, timeout)
                raw = self._parse_response(content, accented_to_answer.keys())
                clues.update(self._pick_clues(raw, accented_to_answer))
            except ClueGenerationError as e:
                errors.append(e)

        if errors and not clues:
            raise errors[0]
        return clues

    def _build_prompt(self, entries, difficulty, language):
        """All of the crossword-clue-writing instructions live here, kept
        separate from the HTTP/parsing plumbing below."""
        style = DIFFICULTY_STYLE.get(difficulty, DIFFICULTY_STYLE["medium"])
        language_name = LANGUAGE_NAMES.get(language, LANGUAGE_NAMES["fr"])
        words_block = "\n".join(f"- {accented}" for _, accented in entries)
        return (
            f"You are a crossword compiler writing in {language_name}, at "
            f"{difficulty.upper()} difficulty. This difficulty level is the "
            f"single most important constraint on every clue you write:\n"
            f"{style}\n\n"
            "Each word below is given in its correctly accented, inflected "
            "written form (right gender, number, and conjugation) — use "
            "that to write grammatically accurate clues.\n\n"
            "For each word, propose exactly 3 different possible crossword "
            "clues, all matching the difficulty level above. Rules for "
            "every clue:\n"
            "- Never mention the word itself, a same-family word, or just "
            "restate its spelling.\n"
            "- Do not write a bare grammatical/technical description "
            '(bad example: "verbe avoir à la deuxième personne du présent '
            'de l\'indicatif" for a verb form) — write an actual clue a '
            "crossword solver would enjoy, not a label.\n"
            "- A synonym or near-synonym is a perfectly good clue.\n"
            "- One short line each.\n\n"
            "Words:\n" + words_block + "\n\n"
            "Respond with exactly one line per word, in this exact plain-text "
            "format (no JSON, no markdown, no numbering, no extra commentary):\n"
            "word: clue 1; clue 2; clue 3\n"
            "One line per word listed above, using the exact accented "
            "spelling given above before the colon, and the 3 clues after "
            "it separated by semicolons."
        )

    def _call(self, prompt, max_tokens, timeout):
        try:
            response = httpx.post(
                self.base_url,
                headers={"Authorization": f"Bearer {self.api_key}"},
                json={
                    "model": self.model,
                    "messages": [{"role": "user", "content": prompt}],
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
        return response.json()["choices"][0]["message"]["content"]

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
        choice out of the LLM's hands as requested."""
        clues = {}
        for key, candidates in raw.items():
            answer = accented_to_answer.get(key) or accented_to_answer.get(key.upper())
            if answer is None:
                continue
            candidates = [
                c for c in candidates
                if c and _NON_LATIN_RE.search(c) is None
            ]
            if candidates:
                clues[answer] = random.choice(candidates)
        return clues
