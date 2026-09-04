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
ever one word per call: 3 lines, one candidate clue per line, nothing
else — no JSON, and no *word* to echo back either. Each line is asked to
start with a "C1="/"C2="/"C3=" label (see the OUTPUT FORMAT block in
`_build_system_prompt()`) purely to help a small model understand the
expected shape — not because parsing needs it: `_parse_response()` just
splits the response into lines and strips a leading label
(`_LEADING_MARKER_RE`) if present, but every non-empty line is trusted
directly as one candidate regardless, no header/delimiter syntax to get
right, no label ever required. This replaced an earlier single-line
"WORD: clue 1; clue 2; clue 3" format that needed the model to echo the
*target word itself* as a header before any of the response could be
trusted as belonging to it — a real, observed failure mode on the local
model: it would sometimes echo the format template's own literal
placeholder text ("word:", or a bare "clue 2") instead of filling it in,
which made an otherwise-fine answer unparseable. With one word per call
and no target-word echo required, there's no header to get wrong and no
template text left to leak (see the project-best-practices SKILL for the
two incidents that motivated dropping the structured format rather than
continuing to patch around it).

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

from .crossword_gen import GenerationCancelled
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

DEFAULT_LLM_BASE_URL = "http://127.0.0.1:3002/v1/chat/completions"
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

# Grid title (see LLMClueGenerator.generate_title), at the user's explicit
# request: "Pas plus de 3 mots." Prompt-side only, deliberately never
# enforced by truncating the model's actual answer afterward — a first
# version of _clean_title did clamp to this many words as a hard safety
# net, but the user asked for that to be removed: "Ne pas couper un titre
# trop long, ce qui lui enlève son sens. Faire confiance au LLM pour
# respecter la consigne (le LLM sur cette machine est un tout petit
# modèle qui a du mal à appliquer les consignes très fidèlement)" — a
# title is meant to read as one coherent phrase, and slicing off its last
# words the moment the model runs one word over the limit can silently
# turn a real, meaningful title into a fragment with no sense of its own
# (see this project's own live example, "La clé du mystère" truncated
# into the meaningless "La clé du"). Kept only as the number named in the
# system prompt's own rule text.
MAX_TITLE_WORDS = 3

# A wrapping quote pair the model sometimes puts around a title despite
# rule 2 explicitly forbidding it (e.g. '"Vol de Nuit"') — stripped by
# _clean_title. Deliberately narrow (quote characters only, not general
# punctuation): a title legitimately ending in "!"/"?" is fine and must
# not be touched.
_TITLE_QUOTES_RE = re.compile(r'^[\'"“”«»]+|[\'"“”«»]+$')

# A leaked "Title: " (or its equivalent in each of the 5 supported
# languages) label the model sometimes echoes despite rule 2 explicitly
# forbidding exactly this — mirrors a failure mode already documented for
# clue generation itself (backend/clues.py's own history, see CLAUDE.md:
# a small model echoing a format template's literal placeholder text
# instead of just answering). Checked case-insensitively, stripped by
# _clean_title after the leading numbered/bulleted marker (so a line like
# "1. Title: Vol de Nuit" is still fully cleaned, not just partially).
_TITLE_LABEL_RE = re.compile(
    r"^\s*(?:title|titre|titel|título|titolo)\s*:\s*", re.IGNORECASE,
)


def _clean_title(content):
    """Turns a raw LLM response into a usable title, or "" if nothing
    usable survives — never raises, since a missing/blank title is a
    purely cosmetic degrade for the caller (see LLMClueGenerator.
    generate_title's own docstring), not worth treating as an error.
    Takes only the first non-empty line (a model asked for a bare title
    occasionally still pads its answer with a second, explanatory line
    despite the prompt forbidding it), strips a leading numbered/bulleted
    marker the same way _parse_response already does for clues
    (_LEADING_MARKER_RE), then a leaked "Title: "-style label
    (_TITLE_LABEL_RE), then a wrapping quote pair (_TITLE_QUOTES_RE) —
    and returns the result exactly as the model wrote it from there,
    deliberately never truncated to MAX_TITLE_WORDS: see that constant's
    own comment for why cutting a too-long title short was tried and then
    explicitly reverted at the user's request (it can silently turn a
    real, meaningful title into a fragment with no sense of its own)."""
    line = ""
    for candidate_line in content.splitlines():
        candidate_line = candidate_line.strip()
        if candidate_line:
            line = candidate_line
            break
    if not line:
        return ""
    line = _LEADING_MARKER_RE.sub("", line).strip()
    line = _TITLE_LABEL_RE.sub("", line).strip()
    line = _TITLE_QUOTES_RE.sub("", line).strip()
    return line


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
    # Second sentence added at the user's explicit request: "quand une
    # définition simple existe pour un mot, ne pas utiliser une définition
    # qui renvoie à un nom de personne, de ville, de fleuve, un terme
    # technique spécialisé, ou de façon générale qui nécessite une culture
    # générale très avancée." Scoped to "easy" only, per the request's own
    # wording — a word that's ALSO a person's/place's name (e.g. a common
    # noun that happens to double as a river or given name) can still be
    # clued at medium/hard difficulty via that sense; at easy, the plain,
    # everyday sense must be preferred whenever one exists at all. This is
    # a genuinely different concern from rule 5 in _build_system_prompt
    # ("the clue must reflect the word's actual meaning") — that rule is
    # about correctness (not inventing a meaning), this one is about
    # *which* real, correct meaning to pick when more than one exists.
    "easy": (
        "very easy: simple, literal, everyday vocabulary, no wordplay, no "
        "ambiguity — a clue a child could answer. If the word has more "
        "than one real meaning and a simple, everyday one exists, always "
        "use that one — never a sense that refers to a person's name, a "
        "city or other place name, a river, a specialized/technical term, "
        "or in general any sense that would require advanced general "
        "knowledge/culture to recognize. Those senses are for medium/hard "
        "difficulty, not easy."
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

# Where every single LLM call gets its own diagnostic Markdown file — see
# generate()'s call to _write_call_log(). Project root, gitignored — a
# debugging artifact for reproducing a specific call by hand or reviewing
# a whole grid's worth of calls after the fact, not source content or a
# durable record like GRID_SVG/ or GRID_PNG/. Originally written only
# for a word that exhausted all 3 retries, extended at the user's
# explicit request to cover every call, successes included — one file
# per attempt, not per word, since a word retried across multiple rounds
# makes more than one call. Folder renamed from the original "LOG" to
# "LOG_LLM" (also at the user's request) once it became clear this
# project could plausibly grow other, unrelated kinds of logs later —
# "LOG_LLM" says specifically what this one is for.
CALL_LOG_DIR = Path(__file__).resolve().parent.parent / "LOG_LLM"


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
# from a plain hyphen), or a "C1="/"C2="/"C3=" label (the OUTPUT FORMAT
# block in _build_system_prompt() asks for one per line, to help a small
# model understand the expected shape — this strips it if the model
# echoes it back, without ever requiring/parsing for it: a line missing
# its label, or in the wrong order, is still trusted just the same).
# Left on an individual line — the only structural cleanup
# _parse_response still does, now that there's no header/delimiter
# syntax left to validate — everything else in a non-empty line is
# trusted as-is.
_LEADING_MARKER_RE = re.compile(r"^\s*(?:[-–—*•]|\d+[.)]|[Cc][123]\s*=)\s*")

# A leaked "word - " (or "word:"/"word,") label at the very start of a
# candidate — the model restating the word it's defining as if labeling
# its own answer, before the actual definition, e.g. "slips -
# sous-vêtement féminin" for SLIPS. Matches the leading token plus a
# separating punctuation mark (colon, comma, or a hyphen/en-dash/em-dash
# variant) and any surrounding whitespace — whether that leading token
# actually *is* the target word is checked separately (see
# _strip_leading_word_label), since a plain regex has no way to know
# that on its own.
_LEADING_LABEL_RE = re.compile(r"^\s*(\S+)\s*[:,\-–—]\s*")


def _strip_leading_word_label(candidate, answer, accented, canonical):
    """Salvages a candidate that only fails `_contains_target_word`
    because it opens with exactly this leaked "word - definition" label
    pattern: strips the label and returns just the definition that
    follows, so a perfectly good definition isn't thrown away — and a
    whole retry round wasted — over a mechanically fixable formatting
    slip. Returns `candidate` unchanged if it doesn't start with the
    target word (or its accented spelling, or a candidate canonical
    form) followed by one of those punctuation marks — this must stay
    narrow, matching only that exact leading-label shape, so it can
    never accidentally rewrite an unrelated candidate that legitimately
    starts with a colon/dash/comma of its own."""
    match = _LEADING_LABEL_RE.match(candidate)
    if not match:
        return candidate
    targets = {_normalize(answer), _normalize(accented)}
    targets.update(_normalize(c) for c in canonical)
    if _normalize(match.group(1)) not in targets:
        return candidate
    rest = candidate[match.end():].strip()
    return rest or candidate


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

# A real, observed failure mode `_NON_LATIN_RE` can't catch (still Latin
# script) and the length cap can't catch either (can be short): the model
# lapsing into a *different* language mid-response — including leaked
# meta-commentary that isn't even an attempted clue, e.g. "All good. Let
# me also make sure they're short (≤20 words each)" for a French word.
# Not a full language-ID model (no new runtime dependency — see
# _detect_wrong_language's docstring for why this deliberately doesn't
# reuse build_sentence_corpus.py's Hunspell-based approach) — just each
# language's most common function words, written naturally per language
# without worrying about overlap by hand (see the auto-dedup below).
_LANGUAGE_STOPWORDS_RAW = {
    "fr": {
        "le", "la", "les", "de", "des", "un", "une", "et", "que", "qui",
        "est", "dans", "pour", "avec", "sur", "cette", "ne", "pas", "plus",
        "vous", "nous", "elle", "il", "être", "leur", "alors", "aussi",
        "mais", "donc", "était", "sont",
    },
    "en": {
        "the", "a", "an", "is", "are", "of", "and", "to", "that", "in",
        "let", "me", "also", "make", "sure", "they", "their", "each",
        "with", "all", "good", "you", "we", "was", "were", "your", "but",
        "then",
    },
    "de": {
        "der", "die", "das", "und", "ist", "ein", "eine", "mit", "für",
        "auf", "nicht", "zu", "von", "im", "den", "dem", "sie", "wir",
        "sind", "war", "aber", "dann", "auch",
    },
    "es": {
        "el", "la", "los", "las", "un", "una", "que", "es", "y", "para",
        "con", "en", "no", "por", "más", "usted", "nosotros", "ella",
        "está", "son", "era", "también", "pero", "entonces",
    },
    "it": {
        "il", "lo", "la", "gli", "le", "di", "un", "una", "che", "è", "e",
        "per", "con", "non", "in", "del", "della", "voi", "noi", "lei",
        "sono", "anche", "però", "quindi", "questo",
    },
}
# Several function words are spelled identically across two Romance
# languages purely by coincidence of shared Latin origin (e.g. "que" in
# both French and Spanish, "il" in both French and Italian) — such a word
# matching would be genuinely ambiguous between the two, undermining the
# whole point of this check, so it's dropped from every language's set
# entirely rather than left in either. Done programmatically, not just by
# careful hand-picking, so a future edit to either list can't silently
# reintroduce a collision unnoticed.
_ambiguous_stopwords = {
    w for words in _LANGUAGE_STOPWORDS_RAW.values() for w in words
    if sum(w in other for other in _LANGUAGE_STOPWORDS_RAW.values()) > 1
}
_LANGUAGE_STOPWORDS = {
    lang: words - _ambiguous_stopwords
    for lang, words in _LANGUAGE_STOPWORDS_RAW.items()
}

# How many *distinct* stopwords from one other language must show up
# before a candidate is treated as "looks like that language instead" —
# more than one guards against a single coincidental match even after
# the cross-language dedup above.
_WRONG_LANGUAGE_MIN_STOPWORDS = 2


def _detect_wrong_language(candidate, target_language):
    """Best-effort check for a candidate written in a different language
    than `target_language`: counts how many of each *other* language's
    common function words appear as whole tokens in `candidate`; if any
    other language reaches `_WRONG_LANGUAGE_MIN_STOPWORDS` distinct hits,
    returns that language's code. Returns None if no other language's
    stopwords showed up strongly enough (including when `target_language`
    has no stopword list of its own — nothing to compare against)."""
    if target_language not in _LANGUAGE_STOPWORDS:
        return None
    tokens = {t.lower() for t in _WORD_TOKEN_RE.findall(candidate)}
    if not tokens:
        return None
    for lang, stopwords in _LANGUAGE_STOPWORDS.items():
        if lang == target_language:
            continue
        if len(tokens & stopwords) >= _WRONG_LANGUAGE_MIN_STOPWORDS:
            return lang
    return None


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
                 on_progress=None, cancel_event=None):
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
        since this is by far the slowest phase of grid generation.

        `cancel_event` (a `threading.Event`, `None` by default — no effect
        for any pre-existing caller), at the user's explicit request:
        checked once per word, right before starting its own round of up
        to 3 LLM calls — raises `crossword_gen.GenerationCancelled` (see
        its own docstring) rather than continuing, letting the "Stop"
        button interrupt clue generation too, not just pattern search/
        minimization (see backend/app.py). This is by far the slowest
        phase of a generation (see this module's docstring), so a coarse,
        once-per-word checkpoint is still frequent enough in practice —
        the interruption can take up to one word's own remaining LLM
        round-trip(s) to actually take effect, never mid-call."""
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
            if cancel_event is not None and cancel_event.is_set():
                raise GenerationCancelled()
            answer, accented, canonical = entry
            system_prompt = self._build_system_prompt(difficulty, language)
            user_message = self._build_user_message(entry, language)
            for attempt in range(3):
                content = None
                error = None
                candidate_details = []
                try:
                    content = self._call(
                        answer, accented, attempt + 1,
                        system_prompt, user_message, max_tokens, timeout,
                    )
                    candidates = self._parse_response(content)
                    if not candidates:
                        outcome = "model gave no candidate lines at all"
                        logger.warning(
                            "clue round %d/3: %r (%r) — model gave no "
                            "candidate lines at all",
                            attempt + 1, answer, accented,
                        )
                    else:
                        clue, candidate_details = self._pick_clue(
                            candidates, answer, accented, canonical, language, attempt + 1,
                        )
                        if clue:
                            clues[answer] = clue
                            outcome = f"selected: {clue!r}"
                        else:
                            # Each candidate's own rejection reason was already
                            # logged individually inside _pick_clue() — this is
                            # just the round-level "so none of them worked" verdict.
                            outcome = (
                                f"all {len(candidates)} candidate(s) rejected "
                                "(see the Candidates section below, or backend.log)"
                            )
                            logger.warning(
                                "clue round %d/3: %r (%r) — all %d candidate(s) "
                                "rejected (see the per-candidate reasons just above)",
                                attempt + 1, answer, accented, len(candidates),
                            )
                except ClueGenerationError as e:
                    errors.append(e)
                    error = e
                    outcome = f"LLM call failed: {e}"
                    logger.warning(
                        "clue round %d/3: %r (%r) — LLM call failed: %s",
                        attempt + 1, answer, accented, e,
                    )
                # Every single call gets its own record, successes included —
                # not just failures — at the user's explicit request, so a
                # whole grid's worth of calls can be reviewed after the fact,
                # not just the ones that went wrong. `success` (this specific
                # attempt produced a usable clue) drives the filename's own
                # SUCCES/ERROR suffix — also requested explicitly, so a
                # directory listing alone shows which calls need attention
                # without opening every file.
                self._write_call_log(
                    answer, accented, language, difficulty, attempt + 1,
                    system_prompt, user_message, content, error, outcome,
                    candidate_details, success=answer in clues,
                )
                if on_progress:
                    on_progress(len(clues), total)
                if answer in clues:
                    break

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

    def generate_title(self, word_entries, language="fr", timeout=DEFAULT_TIMEOUT,
                        cancel_event=None):
        """Asks the LLM for a short (see MAX_TITLE_WORDS), catchy title for
        the whole grid, from the list of every one of its solution words —
        called once per grid, at the user's explicit request, right after
        every clue is already generated (see backend/app.py's
        _run_generate_job) and shown above the finished, playable grid
        (frontend/static/script.js's displayFinalGrid). Unlike generate()
        above, this is a single best-effort attempt with no retry loop and
        no per-call LOG_LLM/ record: a title is a purely cosmetic addition,
        not worth tripling the number of LLM round-trips per grid over (a
        real cost — see clue generation's own documented per-word timing)
        the way a missing clue would be. Any failure here — a connection
        error, an empty/unusable/wrong-language response — simply returns
        "" rather than raising; the caller treats that exactly like a
        title-less grid generated before this feature existed (see
        backend/grid_store.py), never as a reason to fail the request.

        `word_entries` is the same (answer, accented, canonical) shape
        generate() takes; only each word's accented spelling is actually
        used here (the model is shown the real, natural spelling of every
        answer, the same reasoning as generate()'s own accented/inflected
        choice) — deduplicated and sorted so the prompt is stable/
        reproducible regardless of the words' own original grid order."""
        if cancel_event is not None and cancel_event.is_set():
            raise GenerationCancelled()
        words = sorted({accented for _, accented, _ in word_entries})
        if not words:
            return ""
        language_name = LANGUAGE_NAMES.get(language, language)
        system_prompt = (
            "You are naming a crossword puzzle. You will be given the list "
            "of every answer word placed in the grid.\n\n"
            f"Reply with a short, catchy title for this puzzle, entirely in "
            f"{language_name}, loosely evoking its words/theme if a theme is "
            "apparent from them — never a literal list of the words "
            "themselves.\n\n"
            "STRICT RULES:\n"
            f"1. At most {MAX_TITLE_WORDS} words.\n"
            "2. Reply with the title only — no quotes, no ending "
            "punctuation, no explanation, no leading label such as "
            "\"Title:\".\n"
            f"3. The title must be entirely in {language_name}, even if "
            "some of the words below are foreign proper nouns.\n"
        )
        user_message = "Words: " + ", ".join(words)
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
                    "max_tokens": REASONING_TOKEN_BUDGET + 30,
                },
                timeout=timeout,
            )
            response.raise_for_status()
            content = response.json()["choices"][0]["message"]["content"]
        except httpx.HTTPError as e:
            logger.warning(
                "title generation failed (%s, model=%r): %s", self.base_url, self.model, e,
            )
            return ""
        logger.info("title generation: raw LLM response: %r", content)
        title = _clean_title(_strip_reasoning(content))
        if title and _detect_wrong_language(title, language):
            logger.warning(
                "title generation: %r looks like the wrong language, discarding", title,
            )
            return ""
        logger.info("title generation: %r", title)
        return title

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
        "suis" -> "être" or "suivre") can have more than one candidate
        lemma, and a single lemma can itself carry several distinct senses
        (French "chat" -> domestic animal, an online chat, a zodiac sign,
        ...) — every definition found, for every candidate lemma, is shown.
        The accompanying prompt text asks the model to treat multiple
        senses as an opportunity for variety across its 3 candidates
        (drawing on different real senses instead of 3 variations on one),
        rather than collapsing to a single "best" sense and discarding the
        rest — an earlier version of this instruction did exactly that
        ("only one may be the meaning that fits... ignore the others"),
        found to be counter-productive at the user's explicit request.
        Returns "" when this word has no canonical form with dictionary
        coverage."""
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
            "the word's actual meaning(s) before writing your clues. If "
            "more than one distinct sense is shown, the word may genuinely "
            "have several different meanings — use this as an opportunity: "
            "make your 3 candidates as different from each other as "
            "possible by drawing on different senses across them, rather "
            "than writing 3 variations of the same single meaning. Each "
            "candidate must still stay true to one of the word's real, "
            "genuine senses shown above — never invent a meaning that "
            "isn't actually there."
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

        The output-format instructions ask for exactly 3 lines, each
        labeled "C1="/"C2="/"C3=" — a concrete template, to help a small
        model understand the shape of the expected answer, but never the
        *target word* itself for the model to echo back, unlike an
        earlier "word: clue 1; clue 2; clue 3" format that needed the
        target word repeated as a header before any of the response could
        be trusted (a real, observed failure mode: the model would
        sometimes echo the format template's own literal placeholder text
        instead of filling it in correctly). `_parse_response()` strips a
        leading "C1="/"C2="/"C3=" label if the model echoes it back
        (`_LEADING_MARKER_RE`), but never requires or parses for it — a
        line missing its label, or out of order, is still trusted just
        the same. See `_parse_response()` and the project-best-practices
        SKILL for the two incidents that motivated dropping the
        structured single-line format entirely.

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
            "isn't byte-for-byte the same. This also means never opening "
            "a candidate with the word itself as a label, followed by a "
            "colon, comma, or dash, before the actual definition (e.g. "
            "\"word - definition\" or \"word: definition\") — that is "
            "still the word appearing in the clue, just as a prefix "
            "instead of embedded in a sentence; write only the "
            "definition itself, with nothing labeling it.\n"
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
            "adjective/participle — a person noun like \"a house\"/\"a "
            "runner\", or just as easily an ordinary, unremarkable one "
            "like \"grass\" or \"soil\" that doesn't feel specially "
            "gendered — that noun must itself carry the EXACT SAME "
            "gender and number as the word being defined. Before "
            "finalizing each candidate, explicitly check this one "
            "pairing — the target word's own gender/number against the "
            "gender/number of the noun your clue names — and rewrite it "
            "if they don't match exactly; never let it silently "
            "disagree.\n"
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
            "OUTPUT FORMAT — respond with exactly these 3 lines and "
            "nothing else:\n"
            "C1=short sentence (even a single word) indirectly defining "
            "the target word without giving it away\n"
            "C2=short sentence (even a single word) indirectly defining "
            "the target word without giving it away\n"
            "C3=short sentence (even a single word) indirectly defining "
            "the target word without giving it away\n\n"
            "No JSON, no markdown, no blank lines, no repeating the word "
            "itself anywhere, and no extra commentary before, between, or "
            "after these 3 lines."
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

    def _write_call_log(self, answer, accented, language, difficulty, round_number,
                         system_prompt, user_message, content, error, outcome,
                         candidate_details, success):
        """Writes a self-contained Markdown record of one LLM call — every
        single call `generate()` makes, successes included, not just
        failures (originally this only fired for a word that exhausted
        all 3 retries; extended to cover every call at the user's
        explicit request, so a whole grid's worth of calls can be
        reviewed after the fact, not just the ones that went wrong).
        Captures everything needed to replay this *specific* call by
        hand: the complete system + user prompt, the raw LLM output (or
        `None` if the call itself errored), any `ClueGenerationError`,
        a one-line outcome summary, and — as the very last section, at
        the user's explicit request ("précise les propositions
        rejetées, et la proposition finalement retenue") —
        `candidate_details` (`_pick_clue()`'s own `[(candidate, verdict),
        ...]`, empty when the model gave no parsable candidates or the
        call errored outright) rendered as one bullet per candidate, so
        every rejected proposal and the one finally selected are all
        visible together at a glance, not just the outcome line's own
        summary. Written to CALL_LOG_DIR (LOG_LLM/, project root, gitignored
        — a debugging artifact, not a durable record like GRID_SVG/), one
        file per call (so a word retried across multiple rounds gets
        more than one), named `<timestamp>_<answer>_<SUCCES|ERROR>.md` —
        `answer` is the grid's bare uppercase, accent-stripped form
        already (crossword convention), so no extra normalization was
        needed to put it in the filename as requested; the trailing
        SUCCES/ERROR suffix (`success`, also requested explicitly) lets
        a directory listing alone show which calls need attention
        without opening every file — SUCCES means this specific attempt
        produced a usable clue, ERROR covers every other outcome (no
        candidates, all rejected, or the call itself failed). Best-
        effort, like backend/svg_export.py's own saves: a failure to
        write this is logged, never allowed to break grid generation,
        since a missing diagnostic file is far less important than the
        grid itself finishing."""
        CALL_LOG_DIR.mkdir(parents=True, exist_ok=True)
        timestamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
        suffix = "SUCCES" if success else "ERROR"
        path = CALL_LOG_DIR / f"{timestamp}_{answer}_{suffix}.md"
        error_section = str(error) if error is not None else "None"
        output_section = content if content is not None else "(no response — see error above)"
        if candidate_details:
            candidates_section = "\n".join(
                f"- **{verdict}**: {c!r}" for c, verdict in candidate_details
            )
        else:
            candidates_section = "(none — see Error above, or the model gave no parsable candidate lines)"
        body = (
            f"# Clue generation call — {answer} ({accented})\n\n"
            f"- **Date**: {datetime.now().isoformat()}\n"
            f"- **Language**: {language}\n"
            f"- **Difficulty**: {difficulty}\n"
            f"- **LLM endpoint**: {self.base_url}\n"
            f"- **Model**: {self.model}\n"
            f"- **Attempt**: {round_number}/3\n"
            f"- **Outcome**: {outcome}\n\n"
            f"## Error\n\n{error_section}\n\n"
            f"## System prompt\n\n```\n{system_prompt}\n```\n\n"
            f"## User message\n\n```\n{user_message}\n```\n\n"
            f"## Raw LLM output\n\n```\n{output_section}\n```\n\n"
            f"## Candidates\n\n{candidates_section}\n"
        )
        try:
            path.write_text(body, encoding="utf-8")
        except OSError as e:
            logger.warning("failed to write call log for %r: %s", answer, e)

    @staticmethod
    def _parse_response(content):
        """One candidate clue per line — safe now that there's only ever
        one word per call (`_BATCH_SIZE = 1`): there's no word/header to
        match a line against anymore, so every non-empty line is trusted
        directly as one candidate. The only cleanup still applied, before
        a line is trusted: normalizing a non-breaking space (U+00A0,
        which `str.strip()` alone doesn't remove — some models emit these
        instead of a plain space) to a regular one, then stripping a
        leading numbered/bulleted/dash marker, or a "C1="/"C2="/"C3="
        label (`_LEADING_MARKER_RE`) — the label is asked for in the
        prompt (see the OUTPUT FORMAT block in `_build_system_prompt()`)
        purely to help the model, never required here: a line missing
        it, or with a different one, is trusted just the same —
        everything else is used as-is, no delimiter syntax to get right.
        Returns a list of candidate strings (empty if the model's response
        had no non-empty lines at all)."""
        return [
            cleaned
            for line in content.splitlines()
            if (cleaned := _LEADING_MARKER_RE.sub("", line.replace("\xa0", " ")).strip())
        ]

    @staticmethod
    def _pick_clue(candidates, answer, accented, canonical, language, round_number):
        """Picks one of this word's (up to 3) candidate clues at random —
        favors variety across regenerations of the same word, and keeps
        the choice out of the LLM's hands as requested. Drops any
        candidate that isn't actually a clue: longer than `MAX_CLUE_WORDS`
        words (a real, observed failure mode — the model writing out its
        reasoning, several sentences long, instead of a short clue — see
        `MAX_CLUE_WORDS`'s comment), non-Latin-script drift, written in a
        different language than `language` (see
        `_detect_wrong_language` — a real, observed failure mode neither
        of the previous two checks catches, since leaked meta-commentary
        in another Latin-script language can be short and script-valid,
        e.g. "All good. Let me also make sure they're short" for a
        French word), or the word being defined (or a same-family word
        sharing its canonical form/lemma, e.g. singular "maman" leaking
        into a clue for plural MAMANS) appearing anywhere in it, whether
        as the whole clue or embedded in a longer sentence (the prompt
        forbids this, but small local models sometimes do it anyway —
        see `_contains_target_word`). Every rejected candidate is logged
        individually with a qualifier naming which check(s) it failed (a
        candidate can fail more than one at once — all of them are
        named, not just the first found), and the one ultimately chosen
        is logged too — so a deployed instance's log always shows the
        full fate of every candidate the model proposed, not just the
        final verdict. Before any of that, each candidate is first run
        through `_strip_leading_word_label()` — a candidate that would
        otherwise be rejected purely for opening with a leaked "word -
        definition" label gets that label stripped instead, salvaging
        what's usually a perfectly good definition rather than burning a
        whole retry round on a mechanically fixable formatting slip.
        Returns `(chosen, details)`: `chosen` is the selected clue text,
        or None if every candidate was rejected (which `generate()` reads
        as still needing a clue and retries); `details` is `[(candidate,
        verdict), ...]` for every candidate in order — `verdict` is
        `"selected"`, `"accepted (not selected)"` (a candidate that
        passed every check but wasn't the one randomly chosen), or
        `"rejected: <reason(s)>"` — passed straight through to
        `_write_call_log()` so its own diagnostic file can show the full
        list of what was proposed and rejected, not just the final pick,
        at the user's explicit request."""
        details = []
        accepted_indices = []
        for c in candidates:
            if c:
                stripped = _strip_leading_word_label(c, answer, accented, canonical)
                if stripped != c:
                    logger.info(
                        "clue round %d/3: %r (%r) — stripped leaked word-label "
                        "prefix: %r -> %r",
                        round_number, answer, accented, c, stripped,
                    )
                    c = stripped
            reasons = []
            if not c:
                reasons.append("empty")
            else:
                word_count = len(c.split())
                if word_count > MAX_CLUE_WORDS:
                    reasons.append(f"too long ({word_count} words > {MAX_CLUE_WORDS})")
                if _NON_LATIN_RE.search(c) is not None:
                    reasons.append("non-Latin script")
                wrong_lang = _detect_wrong_language(c, language)
                if wrong_lang:
                    reasons.append(f"looks like {wrong_lang} instead of {language}")
                if _contains_target_word(c, answer, accented, canonical):
                    reasons.append("contains the target word (copy/same-family/embedded)")
            if reasons:
                logger.info(
                    "clue round %d/3: %r (%r) — candidate rejected (%s): %r",
                    round_number, answer, accented, "; ".join(reasons), c,
                )
                details.append((c, "rejected: " + "; ".join(reasons)))
            else:
                accepted_indices.append(len(details))
                details.append((c, "accepted (not selected)"))
        if not accepted_indices:
            return None, details
        chosen_index = random.choice(accepted_indices)
        chosen = details[chosen_index][0]
        logger.info(
            "clue round %d/3: %r (%r) — candidate selected: %r",
            round_number, answer, accented, chosen,
        )
        details[chosen_index] = (chosen, "selected")
        return chosen, details
