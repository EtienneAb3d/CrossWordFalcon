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
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path

import httpx

from .crossword_gen import GenerationCancelled, GenerationPaused
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

# generate_title asks the model for this many distinct candidate titles
# (one per line) and picks one at random — same "generate N, pick one"
# diversity trick _pick_clue already uses for clues. At the user's
# explicit request: "Pour favoriser la diversité... générer 3 titres (un
# par ligne) et tire une version au hasard."
_TITLE_COUNT = 3

# How many times generate_title re-asks the model when a whole response
# yields no usable candidate at all: empty reply, only lead-in/header
# lines, every candidate wrong-language, every candidate reusing an exact
# grid word (see _TITLE_HOLLOW_WORDS / _title_grid_word_reuse further
# below), or the HTTP call itself failed. At the user's explicit request
# ("Si le titre est vide, demander une nouvelle génération"). Small — a
# title is cosmetic, not worth many retries — and it only ever loops on a
# genuinely unusable response, so a normal run still makes exactly one
# call.
_TITLE_RETRIES = 3

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

# A leading greeting / introductory phrase the small local model sometimes
# prepends despite the system prompt forbidding it ("Je propose : ...",
# "Voici le titre : ...", "Le titre est ...", "Bonjour, ...", "Here is
# ..."). Stripped by _clean_title after _TITLE_LABEL_RE. Kept deliberately
# to a fixed, well-known set of lead-in openers across the 5 supported
# languages (plus English, which the model drifts into) rather than a
# broad "any words then a colon" rule — a real, valid title can itself
# open with an ordinary word, and only an explicit opener list can tell
# "Je propose : X" (leak) apart from a genuine title that happens to
# start similarly. The trailing separator (":", "-", "—", ",") is
# required so a title beginning with one of these words but no separator
# (unlikely, but possible) is never wrongly truncated.
_TITLE_INTRO_RE = re.compile(
    r"^\s*(?:"
    r"bonjour|salut|hola|ciao|hallo|guten\s+tag|"
    r"je\s+(?:propose|sugg[eè]re|dirais|choisis|pense\s+[àa])|"
    r"voici(?:\s+(?:le|un|mon|les|mes|des)\s+titres?)?|"
    r"le\s+titre\s+(?:est|pourrait\s+[êe]tre|serait)|"
    r"les\s+titres\s+(?:sont|pourraient\s+[êe]tre)|"
    r"un\s+titre\s+possible|mon\s+titre|mes\s+titres|"
    r"propongo|el\s+t[íi]tulo\s+(?:es|ser[íi]a)|aqu[íi]\s+(?:est[áa]|tienes)|"
    r"propongo\s+il\s+titolo|il\s+titolo\s+(?:[èe]|potrebbe\s+essere)|ecco(?:\s+il\s+titolo)?|"
    r"ich\s+schlage\s+vor|der\s+titel\s+(?:ist|lautet|k[öo]nnte)|hier\s+ist(?:\s+der\s+titel)?|"
    r"here\s+is(?:\s+(?:a|the|my)\s+title)?|how\s+about|i\s+(?:propose|suggest)|my\s+title|the\s+title\s+(?:is|would\s+be)"
    r")(?:\s*[:\-–—,]\s*|\s+(?=[\"'“”«»]))",
    re.IGNORECASE,
)

# A trailing explanation/justification the model sometimes appends after
# an otherwise-fine title (", car il évoque la mer.", " parce que…",
# " (en référence à…)"). Stripped by _clean_title. Only fires on an
# explicit causal/parenthetical opener, so a title legitimately
# containing a comma ("Frost, and embers") is left alone.
_TITLE_TRAILING_COMMENT_RE = re.compile(
    r"\s*(?:"
    r"[,;]\s*(?:car|parce\s+que|puisque|because|weil|denn|porque|perch[ée]|poich[ée])\b"
    r"|\s+\((?:en\s+r[ée]f[ée]rence|r[ée]f[ée]rence|allusion|clin\b|in\s+reference|weil|porque)"
    r").*$",
    re.IGNORECASE | re.DOTALL,
)


def _clean_title_line(line):
    """Cleans ONE candidate line down to a bare title, or "" if nothing
    usable survives. Strips, in order: a leading numbered/bulleted marker
    the same way _parse_response already does for clues
    (_LEADING_MARKER_RE), a leaked "Title: "-style label
    (_TITLE_LABEL_RE), a greeting/introductory phrase (_TITLE_INTRO_RE —
    "Je propose : ", "Bonjour, ", …, looped since the model can stack two
    openers), a wrapping quote pair (_TITLE_QUOTES_RE), and a trailing
    explanation (_TITLE_TRAILING_COMMENT_RE — ", car …", " (en référence
    à …)"). Returns what's left exactly as the model wrote it,
    deliberately never truncated to MAX_TITLE_WORDS: see that constant's
    own comment for why clamping a too-long title was tried and then
    explicitly reverted (it can silently turn a real, meaningful title
    into a fragment with no sense of its own)."""
    line = line.strip()
    if not line:
        return ""
    line = _LEADING_MARKER_RE.sub("", line).strip()
    line = _TITLE_LABEL_RE.sub("", line).strip()
    for _ in range(3):
        stripped = _TITLE_INTRO_RE.sub("", line).strip()
        if stripped == line:
            break
        line = stripped
    line = _TITLE_QUOTES_RE.sub("", line).strip()
    line = _TITLE_TRAILING_COMMENT_RE.sub("", line).strip()
    line = _TITLE_QUOTES_RE.sub("", line).strip()
    return line


def _clean_titles(content):
    """Every usable candidate title from a raw multi-line LLM response,
    in order, case-insensitively de-duplicated — generate_title asks for
    _TITLE_COUNT titles (one per line) and picks one at random from this
    list. Never raises: an empty list just means the caller falls back to
    a title-less grid (see generate_title's own docstring). A model told
    to output N lines still sometimes pads with a lone lead-in line
    ("Voici les titres :") or a blank line; those clean down to "" and
    are dropped."""
    titles = []
    seen = set()
    for raw_line in content.splitlines():
        cleaned = _clean_title_line(raw_line)
        if not cleaned:
            continue
        # A real title never ends with a bare colon — a line that still
        # does after cleaning is a leftover list header ("Voici les
        # titres :", "Titres :") the intro regex didn't fully catch.
        if cleaned.rstrip().endswith(":"):
            continue
        key = cleaned.casefold()
        if key in seen:
            continue
        seen.add(key)
        titles.append(cleaned)
    return titles


def _clean_title(content):
    """The single-title convenience wrapper kept for any caller/test that
    still wants one: the first usable candidate from _clean_titles, or ""
    ."""
    titles = _clean_titles(content)
    return titles[0] if titles else ""


# Even a modest batch (5-6 words) was unreliable on the small local model —
# it would produce good clues for the first couple of words then degrade
# into empty/off-topic/malformed lines for the rest of the same response.
# One word per call sidesteps that entirely: every request is as simple as
# the model can be given, at the cost of one HTTP round-trip per word
# instead of one per handful — see generate()'s retry loop and
# frontend/server.py's generous proxy timeout, both already sized to
# absorb many sequential calls per grid.
_BATCH_SIZE = 1

# Still one word per LLM *request* (_BATCH_SIZE above) — but generate()
# now fires up to this many of those single-word requests concurrently,
# at the user's explicit request ("SGLang fait du continuous batching.
# Parallélise la génération des questions 10 par 10."). SGLang's
# continuous batching runs the concurrent requests together on the GPU,
# turning what used to be N sequential round-trips per grid into about
# N/CLUE_BATCH_PARALLELISM. Safe on any OpenAI-compatible server: one
# without request concurrency (e.g. a plain llama.cpp build with no
# --parallel) simply serialises them — no speed-up but no harm either.
# Env-overridable, same convention as CROSSWORDFALCON_PARALLEL_ATTEMPTS —
# set to 1 to force the old fully-sequential behaviour.
CLUE_BATCH_PARALLELISM = max(1, int(os.environ.get("CLUE_BATCH_PARALLELISM", "10")))

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
    # Reinforced further at the user's explicit follow-up request ("bien
    # préciser de choisir la signification la plus simple, et éviter de
    # définir un mot avec son sens le plus technique") — hence the blunt,
    # repeated framing below.
    "easy": (
        "very easy: simple, literal, everyday vocabulary, no wordplay, no "
        "ambiguity — a clue a child could answer. THE MOST IMPORTANT "
        "THING at this level: when the word has more than one real "
        "meaning, always clue its SIMPLEST, most common, most everyday "
        "sense — the one an ordinary person thinks of first — and NEVER "
        "its rarest, most technical or most specialized sense. Reject "
        "outright any meaning that refers to a person's name, a city or "
        "other place name, a river or mountain, a brand or work title, a "
        "scientific/medical/legal/technical term, or anything that needs "
        "advanced general knowledge to recognize. Those senses exist only "
        "for medium/hard difficulty. If the only senses available are of "
        "that kind, fall back to the plainest possible description of the "
        "word rather than leaning into the technical or proper-noun one."
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

# "Mots creux" — function words a grid title may contain even when the
# same word happens to be an answer in the grid: articles, prepositions,
# conjunctions, demonstratives, possessives, small cardinals. Anything
# else in a title that matches a grid word EXACTLY (accent- and
# case-insensitively, whole token) gets that candidate rejected in favour
# of another / a re-ask, at the user's explicit request ("Si un titre...
# contient un mot exacte (sans accent ni casse) de la grille (autre qu'un
# mot creux comme un, une, deux, ce, cela, etc), en choisir un autre, ou
# en redemander un autre"). The prompt already forbids grid-word reuse;
# this is the code-level guarantee the small model doesn't provide on its
# own — the same "prompt says it, a filter enforces it" split already
# used for _contains_target_word in clue generation. Stored accent-
# stripped/lowercased via _normalize (the exact form grid words are
# compared in).
_TITLE_HOLLOW_WORDS = {
    "fr": {
        "le", "la", "les", "l", "un", "une", "de", "des", "du", "d", "au",
        "aux", "a", "et", "ou", "ni", "mais", "donc", "or", "car", "ce",
        "cet", "cette", "ces", "ca", "cela", "ceci", "celui", "celle",
        "ceux", "celles", "mon", "ma", "mes", "ton", "ta", "tes", "son",
        "sa", "ses", "notre", "nos", "votre", "vos", "leur", "leurs", "en",
        "y", "dans", "sur", "sous", "vers", "chez", "par", "pour", "avec",
        "sans", "entre", "contre", "selon", "ne", "pas", "plus", "si",
        "que", "qui", "dont", "quel", "quelle", "quels", "quelles",
        "zero", "deux", "trois", "quatre", "cinq", "six", "sept", "huit",
        "neuf", "dix",
    },
    "en": {
        "the", "a", "an", "of", "and", "or", "nor", "but", "so", "for",
        "yet", "to", "in", "on", "at", "by", "up", "as", "if", "no", "not",
        "with", "from", "into", "onto", "over", "under", "this", "that",
        "these", "those", "my", "your", "his", "her", "its", "our",
        "their", "one", "two", "three", "four", "five", "six", "seven",
        "eight", "nine", "ten",
    },
    "de": {
        "der", "die", "das", "den", "dem", "des", "ein", "eine", "einer",
        "eines", "einem", "einen", "und", "oder", "aber", "doch", "nicht",
        "kein", "keine", "dieser", "diese", "dieses", "jener", "jene",
        "mein", "dein", "sein", "ihr", "unser", "euer", "in", "an", "auf",
        "bei", "mit", "nach", "seit", "von", "zu", "aus", "zum", "zur",
        "im", "am", "eins", "zwei", "drei", "vier", "funf", "sechs",
        "sieben", "acht", "neun", "zehn",
    },
    "es": {
        "el", "la", "los", "las", "un", "una", "unos", "unas", "de",
        "del", "al", "a", "y", "o", "u", "ni", "pero", "sino", "que", "se",
        "lo", "le", "les", "este", "esta", "estos", "estas", "ese", "esa",
        "eso", "mi", "tu", "su", "nuestro", "vuestro", "en", "con", "sin",
        "por", "para", "sobre", "bajo", "entre", "hacia", "no", "mas",
        "muy", "uno", "dos", "tres", "cuatro", "cinco", "seis", "siete",
        "ocho", "nueve", "diez",
    },
    "it": {
        "il", "lo", "la", "i", "gli", "le", "un", "uno", "una", "di",
        "del", "della", "dei", "delle", "a", "al", "alla", "e", "o", "ne",
        "che", "si", "questo", "questa", "quello", "quella", "mio", "tuo",
        "suo", "nostro", "vostro", "in", "con", "su", "per", "tra", "fra",
        "non", "piu", "molto", "due", "tre", "quattro", "cinque", "sei",
        "sette", "otto", "nove", "dieci",
    },
}
_TITLE_HOLLOW_WORDS = {
    lang: {_normalize(w) for w in words}
    for lang, words in _TITLE_HOLLOW_WORDS.items()
}


def _title_grid_word_reuse(title, grid_norm, hollow):
    """The set of grid words a title reuses verbatim: each title token
    accent-stripped/lowercased (via _normalize) and matched EXACTLY (no
    stemming — "marins" does NOT match a grid "marin") against
    `grid_norm`, minus `hollow` (function words a title may always
    contain). Empty set == the title is clean. `grid_norm` / `hollow` are
    passed already normalized so this stays cheap to call per candidate."""
    tokens = {_normalize(t) for t in _WORD_TOKEN_RE.findall(title)}
    return (tokens & grid_norm) - hollow

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
                 on_progress=None, cancel_event=None, should_pause=None):
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

        Words are generated in parallel, CLUE_BATCH_PARALLELISM at a time
        (one LLM request per word, `_BATCH_SIZE=1`, but many in flight at
        once so SGLang's continuous batching decodes them together — see
        that constant). Each word's own up-to-3 retry attempts still run
        in immediate succession within its own worker.

        `on_progress`, if given, is called `on_progress(current, total)`
        as each word finishes (its clue found, or its 3 attempts
        exhausted) — `current` is how many words have a clue so far,
        `total` how many were asked for; used to surface live progress
        (see backend/app.py) since this is by far the slowest phase of
        grid generation.

        `cancel_event` (a `threading.Event`, `None` by default — no effect
        for any pre-existing caller), at the user's explicit request:
        checked once per batch (right before dispatching the next
        CLUE_BATCH_PARALLELISM words) and again by each worker before each
        of its own attempts — raises `crossword_gen.GenerationCancelled`
        (see its own docstring) rather than continuing, letting the "Stop"
        button interrupt clue generation too, not just pattern search/
        minimization (see backend/app.py). This is by far the slowest
        phase of a generation (see this module's docstring), so a coarse
        checkpoint is still frequent enough in practice — the interruption
        can take up to the currently-running batch's own remaining LLM
        round-trip(s) to actually take effect, never mid-call.

        `should_pause` (`None` by default — no effect for any pre-existing
        caller), at the user's explicit request (backend/app.py's own
        CLUES_QUEUE, the GPU/LLM job queue's fair-scheduling mechanism —
        see its own docstring): a callable, checked at the exact same
        once-per-batch (and per-attempt inside a worker) points as
        `cancel_event`. Unlike `cancel_event`,
        which discards everything and raises to abort outright, this
        raises `crossword_gen.GenerationPaused` carrying `(clues,
        remaining_entries)` — every clue already found so far, and the
        list of `word_entries` not yet attempted — so the caller can
        merge the partial result immediately and, whenever this job's
        turn comes back around, resume by calling `generate()` again with
        only `remaining_entries`, never losing the words already done."""
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
        # Identical for every word of this call (only difficulty+language
        # feed it, and both are fixed here) — built once rather than
        # rebuilt inside the per-word loop as it used to be.
        system_prompt = self._build_system_prompt(difficulty, language)

        # User messages are built here, up front and sequentially, on
        # purpose: the gloss / example-sentence lookups they trigger
        # lazily populate module-level caches (gloss_lookup.py /
        # example_sentences.py) that aren't safe for concurrent first
        # access — the worker threads below only ever do the HTTP call
        # plus parsing / filtering / logging.
        prepared = [(entry, self._build_user_message(entry, language, difficulty)) for entry in entries]

        # All words dispatched at once to a rolling pool of
        # CLUE_BATCH_PARALLELISM worker threads (see that constant): a
        # worker that finishes a word immediately starts the next one, so
        # a word that has to retry never leaves the other workers idle
        # (an earlier batch-of-N design did — the whole batch waited on
        # its slowest retrier). SGLang's continuous batching decodes the
        # up-to-CLUE_BATCH_PARALLELISM concurrent requests together.
        #
        # Each word's own up-to-3 attempts still run in immediate
        # succession inside its own worker — never spread across passes —
        # so one word's log still reads "round 1, round 2, round 3" in
        # order (a property an earlier change deliberately established,
        # see the note below).
        #
        # cancel_event / should_pause: checked once here before dispatch,
        # then again as each word completes; each worker also re-checks
        # both before every one of its own attempts (_generate_one), so
        # in-flight work winds down promptly instead of running every
        # remaining attempt first. A running worker can't be force-killed
        # mid-call, so the interruption still takes up to one word's own
        # remaining round-trip(s) to fully take effect.
        if cancel_event is not None and cancel_event.is_set():
            raise GenerationCancelled()
        if should_pause is not None and should_pause():
            raise GenerationPaused((clues, list(entries)))

        executor = ThreadPoolExecutor(max_workers=min(CLUE_BATCH_PARALLELISM, total))
        try:
            futures = [
                executor.submit(
                    self._generate_one, entry, user_message, system_prompt,
                    max_tokens, timeout, language, difficulty,
                    cancel_event, should_pause,
                )
                for entry, user_message in prepared
            ]
            for future in as_completed(futures):
                answer, clue, word_errors = future.result()
                if clue is not None:
                    clues[answer] = clue
                errors.extend(word_errors)
                if on_progress:
                    on_progress(len(clues), total)
                # Checked here, as each word lands, rather than only after
                # the whole pool drains: cancel_futures=True drops every
                # word not yet started so we don't wait it out, and we
                # raise straight away — the ≤CLUE_BATCH_PARALLELISM still
                # mid-call finish in the background (an interrupted call
                # can't be force-killed) and their results are discarded.
                if cancel_event is not None and cancel_event.is_set():
                    executor.shutdown(wait=False, cancel_futures=True)
                    raise GenerationCancelled()
                if should_pause is not None and should_pause():
                    executor.shutdown(wait=False, cancel_futures=True)
                    remaining = [e for e in entries if e[0] not in clues]
                    raise GenerationPaused((clues, remaining))
        finally:
            executor.shutdown(wait=False, cancel_futures=True)

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

    def _generate_one(self, entry, user_message, system_prompt, max_tokens,
                      timeout, language, difficulty, cancel_event, should_pause):
        """One word's complete clue-generation work — up to 3 immediate
        retry attempts on the *same* word — run in its own worker thread
        by generate()'s batched-parallel loop. Returns
        `(answer, clue_or_None, errors)`, where `errors` is the list of
        ClueGenerationError raised across this word's attempts (merged
        into generate()'s own `errors` by the single-threaded caller).

        Mutates no shared state: `_write_call_log` (distinct
        microsecond-stamped filenames) and `logger` (the stdlib logging
        lock) are its only side effects and both are thread-safe.
        Re-checks cancel_event/should_pause before each attempt so an
        interrupted batch stops retrying promptly — it simply returns
        whatever it has; generate() re-checks after the batch and raises
        GenerationCancelled / GenerationPaused as appropriate."""
        answer, accented, canonical = entry
        clue = None
        errors = []
        for attempt in range(3):
            if cancel_event is not None and cancel_event.is_set():
                break
            if should_pause is not None and should_pause():
                break
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
                    picked, candidate_details = self._pick_clue(
                        candidates, answer, accented, canonical, language, attempt + 1,
                    )
                    if picked:
                        clue = picked
                        outcome = f"selected: {picked!r}"
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
                candidate_details, success=clue is not None,
            )
            if clue is not None:
                break
        return answer, clue, errors

    def generate_title(self, word_entries, language="fr", timeout=DEFAULT_TIMEOUT,
                        cancel_event=None):
        """Asks the LLM for _TITLE_COUNT short (see MAX_TITLE_WORDS),
        catchy candidate titles for the whole grid (one per line), from
        the list of every one of its solution words, and returns ONE of
        them chosen at random — "generate N, pick one" for diversity, at
        the user's explicit request ("Pour favoriser la diversité...
        générer 3 titres... et tire une version au hasard"), the same
        trick _pick_clue uses for clues. Called once per grid, right
        after every clue is already generated (see backend/app.py's
        _run_generate_job) and shown above the finished, playable grid
        (frontend/static/script.js's displayFinalGrid). Re-asks the model
        up to _TITLE_RETRIES times, but ONLY when a whole response yields
        nothing usable at all (empty reply, only header/lead-in lines,
        every candidate wrong-language, or the HTTP call failing) — at the
        user's request "Si le titre est vide, demander une nouvelle
        génération"; a normal run still makes exactly one call. There is
        no per-call LOG_LLM/ record (unlike generate()): a title is a
        purely cosmetic addition. If every attempt fails, returns ""
        rather than raising; the caller treats that exactly like a
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
        # Normalized once for the per-candidate grid-word-reuse check
        # (see _title_grid_word_reuse). A grid word that is itself a
        # "mot creux" is dropped from grid_norm too, so a hollow answer
        # (e.g. the grid literally contains "UNE") never causes a
        # rejection — the check only ever fires on a content word.
        hollow = _TITLE_HOLLOW_WORDS.get(language, set())
        grid_norm = {_normalize(w) for w in words} - hollow
        # History: first rewritten (after "Titre de mots croisés en
        # français") to carry three concrete worked GOOD examples
        # (words -> title), following this module's usual small-model
        # pattern. That backfired badly here — Qwen3-4B simply COPIED the
        # first example's title ("The salty horizon"), emitting
        # "L'horizon salé" for every grid regardless of its words (the
        # string was also the most-repeated token in the prompt, since
        # the WRONG-answers block quoted it three more times). Titles are
        # short and creative, so a tiny model latches onto any literal
        # title string it sees far more than it does for a full-sentence
        # clue. Fix: NO concrete good-example title strings at all — only
        # an abstract shape description plus a "build it from THESE grid
        # words" construction step, plus a higher per-request temperature
        # (below) so the output actually varies with the input.
        system_prompt = (
            "You invent TITLES for a crossword puzzle — short names, like "
            "the title of a book, a song, or a film. You are given the "
            "list of every answer word in the grid.\n\n"
            f"Output exactly {_TITLE_COUNT} DIFFERENT titles, ONE PER "
            f"LINE. Each title is 1 to {MAX_TITLE_WORDS} words, entirely "
            f"in {language_name}, loosely evoking the words or their "
            "shared theme if one is apparent. The "
            f"{_TITLE_COUNT} must be genuinely different from each other "
            "— a different key word or a different angle each time, not "
            "near-duplicates.\n\n"
            f"Your ENTIRE reply is those {_TITLE_COUNT} lines and nothing "
            "else. Each line's first character is that title's first "
            "character. No numbering, no bullet, no blank line between "
            "them, no greeting, no preamble, no comment before or after.\n\n"
            "NEVER do any of these:\n"
            f"- Give fewer than {_TITLE_COUNT} lines, or repeat the same "
            "title twice.\n"
            "- Describe the task or explain yourself. A line must NOT "
            "mean things like \"a crossword title\", \"title in "
            f"{language_name}\", \"here is a title\", \"puzzle name\" — "
            "that is a description, not a title.\n"
            "- Start a line with a greeting or an introductory phrase "
            "such as \"Bonjour\", \"Je propose\", \"Voici\", \"Voici les "
            "titres\", \"Le titre est\", \"Un titre possible\", \"Je "
            "suggère\", \"Here is\", \"How about\" — or any equivalent in "
            f"{language_name}. Write each bare title with no such "
            "lead-in.\n"
            "- Add any comment, justification or explanation after a "
            "title (\"car il évoque…\", \"parce que…\", \"(en référence "
            "à…)\"). Stop each line the moment its title is complete.\n"
            "- Output a whole sentence, a definition, or a list of the "
            "grid words.\n"
            "- Add quotes, a trailing period, or a label such as "
            "\"Title:\" / \"Titre :\".\n"
            f"- Write in any language other than {language_name}, even if "
            "some answers are foreign names.\n"
            "- MOST IMPORTANT RULE: never put a grid word into a title. "
            "Not the word itself, not its singular/plural, not another "
            "tense of it, not it with or without an accent. If a grid "
            "word (or any form of it) appears in your title, that title "
            "is rejected. Only tiny function words (a, the, of, in, and "
            "their equivalents) are allowed to coincide.\n\n"
            "SHAPE of a good title: 2 or 3 words, an evocative noun "
            "phrase or a small play on words — an image or a mood, never "
            "a sentence and never a definition. (No sample titles are "
            "given on purpose: any example would just get copied. Invent "
            "your own.)\n\n"
            "HOW TO BUILD THEM:\n"
            "1. Read the grid words in the user message. Note the mood, "
            "place, season, time of day, or action they bring to mind.\n"
            f"2. Build {_TITLE_COUNT} short names in "
            f"{language_name} that EVOKE that mood/place/idea WITHOUT "
            "naming any of the grid words. Say it sideways: a related "
            "word, a broader word, a metaphor. Each of the "
            f"{_TITLE_COUNT} anchored on a DIFFERENT idea. A title that "
            "could sit on top of any random grid is also wrong — it must "
            "clearly fit THESE words while never containing one.\n"
            "3. Before writing each line, scan it word by word against "
            "the grid list. If any word matches, replace it with a "
            "synonym or a related image and scan again.\n\n"
            "WRONG answers, never produce anything like these:\n"
            "- \"Titre de mots croisés\", \"Titre de la grille\", "
            f"\"{language_name} crossword\" — that names the task, not "
            "this puzzle.\n"
            "- Any line opening with \"Je propose\", \"Voici les "
            "titres\", \"Bonjour\" or the like — each line's first "
            "character is its title's first character.\n"
            "- A title followed by \", car…\" / \"(en référence à…)\" — "
            "stop the instant the title is complete.\n"
            "- A title with no visible link to the grid words below.\n"
            "- A title that contains any grid word from the list below "
            "(this is the rule broken most often — check every line "
            "against the list before sending).\n"
        )
        user_message = (
            "Grid words: " + ", ".join(words) + f"\n{_TITLE_COUNT} titles, "
            "one per line:"
        )
        # Retry only when a whole response is unusable — an empty reply,
        # only header/lead-in lines, every candidate wrong-language, or
        # the HTTP call itself failing. At the user's explicit request
        # ("Si le titre est vide, demander une nouvelle génération").
        for attempt in range(_TITLE_RETRIES):
            if cancel_event is not None and cancel_event.is_set():
                raise GenerationCancelled()
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
                        # Deliberately higher than the shared TEMPERATURE
                        # (0.4, tuned for clue accuracy): a title is
                        # creative, not factual, and the small model was
                        # collapsing to one constant output ("L'horizon
                        # salé") for every grid. More randomness here makes
                        # the title actually track the grid words — and
                        # also makes a retry likely to produce something
                        # different from the attempt that just failed.
                        "temperature": 0.9,
                        # Room for _TITLE_COUNT short lines (reasoning
                        # itself is disabled by reasoning_effort:none, so
                        # this is essentially just the answer budget).
                        "max_tokens": REASONING_TOKEN_BUDGET + 60,
                        # A per-request reinforcement of the same intent as
                        # LLAMA_CHAT_TEMPLATE_KWARGS/SGLANG_CHAT_TEMPLATE_KWARGS's
                        # own enable_thinking:false (run_llm.sh/run_sglang.sh) —
                        # harmless for a server that doesn't recognize this
                        # field at all (verified live for llama_cpp.server:
                        # its own request schema has no `model_config =
                        # {"extra": "forbid"}`, so Pydantic's default
                        # behavior silently ignores it), but a real,
                        # request-level "none" for a server that does —
                        # confirmed live earlier in this project's own
                        # SGLang investigation: SGLang accepts this exact
                        # field and only "none" (not "low") actually
                        # disables thinking for a Qwen3 chat template.
                        "reasoning_effort": "none",
                    },
                    timeout=timeout,
                )
                response.raise_for_status()
                content = response.json()["choices"][0]["message"]["content"]
            except httpx.HTTPError as e:
                logger.warning(
                    "title generation attempt %d/%d failed (%s, model=%r): %s",
                    attempt + 1, _TITLE_RETRIES, self.base_url, self.model, e,
                )
                continue
            logger.info(
                "title generation attempt %d/%d: raw LLM response: %r",
                attempt + 1, _TITLE_RETRIES, content,
            )
            candidates = _clean_titles(_strip_reasoning(content))
            # A title is short, so _detect_wrong_language is only a weak
            # signal here — but a clearly-wrong-language candidate is
            # still dropped rather than risk showing it (same as the
            # single-title version did).
            kept = [t for t in candidates if not _detect_wrong_language(t, language)]
            if len(kept) != len(candidates):
                logger.warning(
                    "title generation: discarded wrong-language candidate(s): %r",
                    [t for t in candidates if t not in kept],
                )
            # Reject any candidate that reuses an exact grid word (other
            # than a "mot creux") — at the user's explicit request,
            # "en choisir un autre" (another candidate this same attempt)
            # "ou en redemander un autre" (a fresh attempt if none is
            # clean). The prompt forbids this too; the model just doesn't
            # obey reliably.
            clean = []
            for t in kept:
                reused = _title_grid_word_reuse(t, grid_norm, hollow)
                if reused:
                    logger.info(
                        "title generation attempt %d/%d: rejecting %r "
                        "(reuses grid word(s): %s)",
                        attempt + 1, _TITLE_RETRIES, t, ", ".join(sorted(reused)),
                    )
                else:
                    clean.append(t)
            if clean:
                # "Generate N, pick one at random" — the same diversity
                # trick _pick_clue uses for clues.
                title = random.choice(clean)
                logger.info(
                    "title generation attempt %d/%d: candidates=%r chosen=%r",
                    attempt + 1, _TITLE_RETRIES, clean, title,
                )
                return title
            logger.info(
                "title generation attempt %d/%d: no usable candidate "
                "(empty / wrong-language / all reuse a grid word), retrying",
                attempt + 1, _TITLE_RETRIES,
            )
        logger.info(
            "title generation: no usable candidate after %d attempts, returning no title",
            _TITLE_RETRIES,
        )
        return ""

    @staticmethod
    def _build_examples_block(entry, language, difficulty):
        """Real sentences (from the OpenSubtitles+Wikipedia reference
        corpus, see backend/example_sentences.py) using this word's exact
        accented form, if any exist — grounds the model's sense of what a
        rare or ambiguous word actually means instead of leaving it to
        guess (a real, observed failure: the small local model defined
        French `are` — the 100 m² land-area unit — as the English verb "to
        be", since it had never reliably learned the rare French sense).
        Returns "" (no section added) when no example sentences were found
        for this word.

        In "easy" difficulty, an extra warning is appended, at the user's
        explicit request: these corpus sentences often contain proper
        nouns (people, cities, brands, work titles) sitting right next to
        the target word, and the small model tends to latch onto one and
        clue the everyday word via that proper-noun reading — the warning
        tells it to ignore any proper noun appearing in the examples and
        use them only to confirm the ordinary sense."""
        _, accented, _ = entry
        sentences = find_examples_for_words([accented], language).get(accented)
        if not sentences:
            return ""
        lines = "\n".join(f"- {s}" for s in sentences)
        block = (
            f'Real example sentences using "{accented}":\n{lines}\n\n'
            "These are genuine sentences, not hints about difficulty or "
            "style — use them only to confirm what the word actually means "
            "(this matters most for short or unusual words that might look "
            "like a word from another language) before writing your clues."
        )
        if difficulty == "easy":
            block += (
                " These sentences may contain proper nouns (names of "
                "people, places, brands, works) near the target word — "
                "IGNORE those completely. They are not the meaning to "
                "clue; at this difficulty you must define the word's "
                "plain, everyday sense only."
            )
        return block

    @staticmethod
    def _build_gloss_block(entry, language, difficulty):
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
        In "easy" difficulty, proper-noun senses (`pos == "name"`) are
        dropped, at the user's explicit request: an ordinary word can also
        have a `name` sense in the dictionary (French "manga" is a
        Japanese comic *and* a village in Burkina Faso), and passing that
        sense to the model risks it cluing the everyday word via the
        obscure proper-noun reading — exactly what "easy" must avoid.
        medium/hard keep every sense.

        Returns "" when this word has no canonical form with dictionary
        coverage (or, in "easy", none left once `name` senses are
        removed)."""
        _, accented, canonical = entry
        glosses_by_lemma = find_glosses_for_canonicals(canonical, language)
        drop_name_senses = difficulty == "easy"
        word_parts = [
            f'- "{lemma}" ({sense["pos"]}): {gloss}'
            for lemma in canonical
            for sense in glosses_by_lemma.get(lemma, [])
            if not (drop_name_senses and sense.get("pos") == "name")
            for gloss in sense["glosses"]
        ]
        if not word_parts:
            return ""
        return (
            f'Dictionary definition(s) related to "{accented}":\n'
            + "\n".join(word_parts) + "\n\nThese are real dictionary "
            "definitions of the word's root form(s), and they are the "
            "ONLY meanings you are allowed to clue (see the ABSOLUTE RULE "
            "in the instructions). Every one of your 3 clues must come "
            "from a definition line above and from nothing else — not "
            "from what the word 'reminds you of', not from a similar-"
            "looking word in another language, not from a meaning you "
            "half-remember. If a root form above resembles a more "
            "familiar word, that resemblance is a trap: define only what "
            "the text after the colon says. If more than one distinct "
            "sense is shown, treat that as a chance to make your 3 "
            "candidates genuinely different by drawing on different "
            "senses, rather than 3 rewordings of one — but each must "
            "still trace back to a specific line above."
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
            "word.\n\n"
            "ABSOLUTE RULE — THE DICTIONARY DEFINITION IS THE ONLY SOURCE "
            "OF MEANING. If the user message contains a \"Dictionary "
            "definition(s)\" section, every one of your 3 clues MUST be "
            "built from a meaning written there, and from NOTHING ELSE. "
            "You may not clue any sense that is not in that section. If "
            "your own memory of the word disagrees with the definition "
            "given, your memory is wrong — follow the definition. If a "
            "listed root form happens to look like a word in another "
            "language, or like a different, more familiar word, ignore "
            "that resemblance completely: only the definition TEXT next "
            "to it counts (e.g. a French entry 'choir (verb): Tomber.' "
            "means the verb 'to fall' — it has nothing to do with an "
            "English 'choir'/a singing group). Inventing a plausible-"
            "sounding meaning that is not in the definitions is the single "
            "worst mistake you can make here.\n\n"
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
            "never an unrelated sentence that merely sounds plausible, and "
            "never a meaning you 'recognise' that isn't in the definitions "
            "you were given. This is the same point as the ABSOLUTE RULE "
            "above, restated as a check: for EACH of your 3 candidates, "
            "before writing it, point to the exact dictionary definition "
            "line it comes from. If you cannot, that candidate is invalid "
            "— rewrite it from a definition that IS listed. If no "
            "dictionary section was provided at all, only then may you "
            "rely on your own knowledge, and even then stay to the "
            "plainest, most certain everyday sense.\n"
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

    def _build_user_message(self, entry, language, difficulty):
        """The one thing that varies per call: the word itself, plus its
        grounding block (real dictionary definitions/example sentences,
        when available) — sent as the `user` message, paired with the
        fixed `system` message from `_build_system_prompt()`. `difficulty`
        only reaches the gloss block, which drops proper-noun senses in
        "easy" (see `_build_gloss_block`)."""
        _, accented, _ = entry
        parts = [f"Word: {accented}"]
        gloss_block = self._build_gloss_block(entry, language, difficulty)
        if gloss_block:
            parts.append(gloss_block)
        examples_block = self._build_examples_block(entry, language, difficulty)
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
                    # See the identical field on generate_title's own call
                    # above for why this is here and why it's safe for a
                    # server that doesn't recognize it.
                    "reasoning_effort": "none",
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
