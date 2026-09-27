#!/usr/bin/env python3
""""David FALCON", the web UI's in-app chat assistant, at the user's
explicit request: "En bas à droite de l'interface, ajoute un ChatBot
(ouvert par défaut) avec l'icône de l'application. Le ChatBot utilise le
LLM pour répondre à l'utilisateur. Il s'appelle David FALCON." Talks to
the exact same OpenAI-compatible chat-completions endpoint as
backend/clues.py's LLMClueGenerator (same LLM_BASE_URL/LLM_MODEL/
LLM_API_KEY environment configuration by default — on a single-GPU
machine there is only ever one local/remote LLM server configured for
this whole app; on a dual-GPU one, backend/app.py builds this class'
own second instance pointed at a second, interactive-dedicated LLM
server instead — see this class' own __init__) — deliberately its own,
separate class rather than reusing LLMClueGenerator directly: clue/title
generation and chatting are different enough concerns (retry loops and
per-call LOG_LLM/ records make no sense for a live conversation) that
sharing a class would mean more special-casing than code actually
shared.

Unlike grid/clue generation, a chat reply is never queued behind
GRID_QUEUE/CLUES_QUEUE (see backend/app.py) — a stand-alone question is
a quick, single LLM call a player expects to answer promptly, not a
multi-minute batch job; it competes for the same LLM server as clue
writing on an ordinary best-effort basis, exactly like any two clients of
one shared server would, with no explicit fairness mechanism between the
two (not asked for, and a real one would need the same kind of care as
GRID_QUEUE/CLUES_QUEUE's own preemption logic for comparatively little
benefit — a chat reply is one call, not a long-running batch that could
starve someone else for 15 minutes)."""
import asyncio
import json
import logging
import os
import re
import unicodedata
from pathlib import Path

import httpx

from .clues import DEFAULT_LLM_API_KEY, DEFAULT_LLM_BASE_URL, DEFAULT_LLM_MODEL, LANGUAGE_NAMES
from .dictionary_lookup import word_forms
from .embedder import Embedder, EmbedderError
from .example_sentences import find_examples_for_words
from .gloss_lookup import find_glosses_for_canonicals
from .inflection_lookup import describe_form
from .qdrant_store import QdrantStore, QdrantStoreError

logger = logging.getLogger("crosswordfalcon.chatbot")

DEFAULT_TIMEOUT = 120.0
TEMPERATURE = 0.5
# Generous for a genuine multi-sentence conversational answer (longer than
# a single clue/title, but still a chat message, not an essay) — no
# REASONING_TOKEN_BUDGET-style addition here: this project's default model
# (Qwen3.5, thinking disabled) never needs it, and a reasoning-capable
# alternative would simply take longer per reply, same as it does for
# clue generation, without needing a special case here.
MAX_TOKENS = 1024

# Reasoning-block tags (see ChatBot.reply_stream's own docstring) — a
# streamed response can split either tag across an arbitrary number of
# chunk boundaries (even character by character, in principle), so
# detecting them can't just check "is the whole tag in this one chunk".
_THINK_OPEN = "<think>"
_THINK_CLOSE = "</think>"

# How ChatBot.reply_stream() should treat a reasoning block, set via
# CHATBOT_THINK_FILTER (env.sh/env_default.sh) — configured explicitly
# rather than guessed at dynamically (an earlier version of this file
# tried a window-based auto-detection instead, buffering the first
# _REASONING_LEAK_WINDOW_CHARS of every reply before deciding; reverted
# at the user's own explicit request, since which behavior a given
# model/server needs is already known in advance from its own config —
# guessing dynamically only ever cost every reply a real, needless
# streaming delay for no benefit). Three real, live-measured model
# behaviors this project has actually run into, see reply_stream()'s own
# docstring for the two-real-model story behind each:
#   "open_close" (default) — the safe, general default: wait to see a
#     real, visible <think> opening tag before ever holding anything
#     back; a model that never emits one (the common case for a non-
#     reasoning model) streams every chunk immediately from the first
#     token, exactly as if no filtering existed at all.
#   "close_only" — for a setup where the opening tag is silently
#     injected into the *prompt* itself and never echoed back in the
#     completion (confirmed live for this project's own SGLang/Qwen3
#     setup) — starts already "inside" a reasoning block, discarding
#     everything up to the first </think>. Never use this for a model
#     that can also produce an entirely non-reasoning reply with no tag
#     at all: with no </think> to end on, that reply's every chunk would
#     be held back and silently lost in full (confirmed live too — the
#     original bug report this whole mechanism exists to fix).
#   "none" — never look for either tag at all; every chunk streams the
#     instant it arrives. For a model already confirmed to never emit
#     either tag (this project's own current default, llama.cpp serving
#     Qwen3.8-27B with thinking disabled) — the one setting with zero
#     runtime cost, and immune to a real reply that happens to contain
#     the literal text "<think>" being mistaken for a reasoning block.
CHATBOT_THINK_FILTER_CHOICES = ("open_close", "close_only", "none")
DEFAULT_CHATBOT_THINK_FILTER = "open_close"

# Routing of a question asked while a playable grid is on screen (see
# ChatBot.classify_question): a first, non-streamed LLM call answers with
# one of these keywords, and the reply is then written from a prompt
# holding only what that kind of question needs — the whole DOC_USER text
# for USAGE, the one selected word for CLUE. Anything else the classifier
# answers (or a failed call) falls back to the combined prompt.
ROUTE_USAGE = "USAGE"
ROUTE_CLUE = "CLUE"
# An explicit request for the answer of a word: written from the CLUE
# prompt like a hint, but never checked for leaks (it IS the answer).
ROUTE_ANSWER = "ANSWER"
CLASSIFY_TEMPERATURE = 0.0
# Room for a model that reasons a few lines before answering anyway.
CLASSIFY_MAX_TOKENS = 200
CLASSIFY_TIMEOUT = 30.0
# Earlier messages shown to the classifier, each cut to
# CLASSIFY_CONTEXT_CHARS — enough to read a follow-up ("et l'autre ?")
# without paying for the whole conversation.
CLASSIFY_HISTORY_MESSAGES = 2
CLASSIFY_CONTEXT_CHARS = 300
# Earlier messages kept in a CLUE reply's prompt: the last exchanges only
# (a previous hint on the same word), never the whole conversation.
CLUE_HISTORY_MESSAGES = 4
# Shortest root of the answer a CLUE prompt forbids in a hint.
CLUE_STEM_MIN_LETTERS = 4
# Attempts at a hint that gives no part of the answer before its leaked
# words are masked (see ChatBot._checked_hint).
CLUE_HINT_ATTEMPTS = 3
# Dictionary grounding of a CLUE prompt: at most this many base forms of
# the answer, and this many definitions for each.
CLUE_GLOSS_LEMMAS = 2
CLUE_GLOSSES_PER_LEMMA = 3
# Example synonyms of a CLUE prompt, found by a Qdrant similarity search
# (see _qdrant_synonyms): how many are shown, the lowest similarity score
# kept, how many nearest neighbours a search reads, and the timeout of
# those calls — the hint goes without examples when Qdrant or the
# embedding server does not answer in time.
CLUE_SYNONYM_COUNT = 3
CLUE_SYNONYM_MIN_SCORE = 0.80
CLUE_SYNONYM_POOL = 100
# The lowest similarity kept between an example and the definitions of the
# answer's base form, and how many of those definitions are embedded.
CLUE_SYNONYM_MIN_MEANING = 0.40
CLUE_SYNONYM_GLOSSES = 2
CLUE_SYNONYM_TIMEOUT = 5.0
# Real sentences of the reference corpus shown in a CLUE prompt's
# SENTENCES section (see _corpus_sentences).
CLUE_SENTENCE_COUNT = 3


def _longest_tag_prefix_suffix(buffer, tag):
    """Longest suffix of `buffer` that's also a (strict, not full-tag)
    prefix of `tag` — e.g. a buffer ending in "...<thi" against tag
    "<think>" returns 4 ("<thi"). Used by reply_stream to know exactly
    how many trailing characters of an as-yet-tag-free buffer might still
    turn into the start of `tag` on a future chunk, and so must be held
    back rather than flushed as if they were ordinary visible text."""
    max_len = min(len(buffer), len(tag) - 1)
    for length in range(max_len, 0, -1):
        if buffer.endswith(tag[:length]):
            return length
    return 0


# A markup tag in a chat reply: `<name>`, `</name>`, `<name attr="…">` or
# `<name/>`. The model sometimes wraps its reply in structuring tags of its
# own (<preamble>, <answer>, <output>…); the tags are dropped, their text
# is kept.
_TAG_RE = re.compile(r"</?[A-Za-z][A-Za-z0-9_:-]*(?:\s[^<>]*)?/?>")
# Longest text after a "<" still awaited as a possible tag before it is
# released as plain text.
MAX_TAG_CHARS = 64


class _TagStripper:
    """Removes every markup tag (`_TAG_RE`) from a streamed reply. A tag
    can arrive split across chunks, so the text from a "<" onwards is held
    back until it either closes as a tag (dropped) or can no longer be one
    (a newline, a "<" or MAX_TAG_CHARS reached — released as is)."""

    def __init__(self):
        self.pending = ""

    def feed(self, text):
        out = []
        for ch in text:
            if not self.pending:
                if ch == "<":
                    self.pending = ch
                else:
                    out.append(ch)
                continue
            if ch == "<":
                out.append(self.pending)
                self.pending = ch
            elif ch == ">":
                candidate = self.pending + ch
                self.pending = ""
                if not _TAG_RE.fullmatch(candidate):
                    out.append(candidate)
            elif ch == "\n" or len(self.pending) >= MAX_TAG_CHARS:
                out.append(self.pending + ch)
                self.pending = ""
            else:
                self.pending += ch
        return "".join(out)

    def flush(self):
        rest, self.pending = self.pending, ""
        return rest


DOC_USER_PATH = Path(__file__).resolve().parent.parent / "DOC_USER" / "EN" / "ReadMe.md"

_doc_user_cache = None


def _load_doc_user():
    """DOC_USER/EN/ReadMe.md's own content, read once and cached for the
    process's lifetime (it never changes while the server is running) —
    the chatbot's own knowledge of how the interface works, at the user's
    explicit request: "A chaque question de l'utilisateur, le LLM est
    informé de la doc contenue dans DOC_USER." Falls back to an empty
    string (never raises) if the file is missing for some reason — the
    chatbot should still work, just without this extra grounding, rather
    than fail every single message over a missing reference file."""
    global _doc_user_cache
    if _doc_user_cache is None:
        try:
            _doc_user_cache = DOC_USER_PATH.read_text(encoding="utf-8")
        except OSError as e:
            logger.warning("could not read %s: %s", DOC_USER_PATH, e)
            _doc_user_cache = ""
    return _doc_user_cache


def _fixed_prompt_head(doc_user):
    """The part of every DOC_USER-carrying system prompt that never varies
    between two requests — the introduction and the whole DOC_USER text —
    shared verbatim by the combined prompt and the USAGE prompt, so the
    LLM server's prefix cache reuses it across both."""
    return (
        "You are David FALCON, the friendly in-app assistant of CrossWordFalcon, a "
        "crossword-puzzle web app. You help the player use the interface and solve the "
        "crossword grid currently on screen (explaining a clue, giving a hint, or, if "
        "explicitly asked, the answer itself).\n\n"
        "Reference documentation for how the interface itself works "
        "(frontend/static/index.html and script.js, described here for a player, not a "
        "developer). This documentation is written in English, but that is NOT the "
        "language to reply in: use its content to answer, rephrased in your own words in "
        "the player's language set by the rules below — never copy an English passage "
        "from it into your reply:\n"
        f"{doc_user}\n\n"
    )


_CLASSIFY_SYSTEM_PROMPT = (
    "You sort the messages a player sends to the assistant of a crossword web "
    "app while a crossword grid is on screen. Answer with exactly one word:\n"
    "USAGE — the message is about using the software: its interface, buttons, "
    "panels, settings, features, how something works, or anything else that is "
    "not help with filling in the grid.\n"
    "CLUE — the message asks for help filling in the grid: a hint, an "
    "explanation of a clue, whether a letter or a word is right.\n"
    "ANSWER — the message EXPLICITLY asks for the answer, the solution or the "
    "exact word to write.\n"
    "A question about grids as objects of the software (the library, generating, "
    "saving, printing or opening a grid) is USAGE. Examples: 'Un indice ?' -> "
    "CLUE; 'Ça commence par un B ?' -> CLUE; 'C'est MAISON ?' -> CLUE (a proposal "
    "to check, not a request for the answer); 'Donne-moi la réponse' -> ANSWER; "
    "'What is the answer?' -> ANSWER; "
    "'Comment vérifier mes réponses ?' -> USAGE; 'Où sont les grilles déjà "
    "créées ?' -> USAGE; 'How do I change the language?' -> USAGE.\n"
    "Reply with USAGE, CLUE or ANSWER only, nothing else."
)


def _build_classify_messages(history, message):
    """The two messages of the classifier call: the fixed instructions,
    then the player's message after a short excerpt of the conversation
    (the last CLASSIFY_HISTORY_MESSAGES messages, each cut to
    CLASSIFY_CONTEXT_CHARS) so a follow-up can be read in context."""
    context_lines = []
    for m in _trim_history(history, CLASSIFY_HISTORY_MESSAGES):
        who = "Player" if m.get("role") == "user" else "Assistant"
        context_lines.append(f"{who}: {(m.get('content') or '')[:CLASSIFY_CONTEXT_CHARS]}")
    context = (
        "Previous messages (context only):\n" + "\n".join(context_lines) + "\n\n"
        if context_lines else ""
    )
    return [
        {"role": "system", "content": _CLASSIFY_SYSTEM_PROMPT},
        {"role": "user", "content": (
            f"{context}Message to sort: {message}\n\nAnswer with one word, USAGE, CLUE or ANSWER:"
        )},
    ]


def _word_forms(word, language):
    """`(inflected forms, base forms)` of `word`'s answer: the natural
    spellings and canonical forms of every wordlist row of its grid form
    (`dictionary_lookup.word_forms` — the same lookup the Dictionnaire
    panel makes), then the grid word's own `accented`/`canonical` fields,
    each list without repeats (case-insensitive). The wordlist comes first
    because a grid word can carry placeholders instead (a grid published
    from Interactive mode stores its answer as both), and the Dictionnaire
    finds "boire" for BUSSENT whatever the grid holds."""
    answer = word.get("answer") or ""
    forms, bases = [], []
    for accented, canonicals in (word_forms(answer, language) if answer else []):
        forms.append(accented)
        bases.extend(canonicals)
    if word.get("accented"):
        forms.append(word["accented"])
    canonical = word.get("canonical") or []
    if isinstance(canonical, str):
        canonical = [c for c in canonical.split(";") if c]
    bases.extend(canonical)

    def unique(items):
        seen, out = set(), []
        for item in items:
            if item and item.lower() not in seen:
                seen.add(item.lower())
                out.append(item)
        return out

    return unique(forms), unique(bases)


def _clue_grounding(word, language):
    """What the CLUE prompt tells the model about the answer itself, so a
    hint describes the word in its exact form rather than the words of its
    clue: `(grammar, meanings, base_forms)`. `grammar` is the parts of
    speech of the exact form (backend/inflection_lookup.py), `meanings`
    `(lemma, pos, gloss)` triples from the gloss dictionary for its base
    forms (the senses of the analysed part of speech when there are any),
    `base_forms` the base forms other than the word itself — forbidden in a
    hint like the word. The forms come from `_word_forms`."""
    answer = word.get("answer") or ""
    forms, canonical = _word_forms(word, language)
    analyses = []
    for form in forms or [answer]:
        for analysis in describe_form(form, language) if form else []:
            if analysis not in analyses:
                analyses.append(analysis)
    # Only the part of speech of each analysis ("verb" of "verb, third-
    # person plural imperfect subjunctive (of "boire")"): the English tense
    # and person labels made the model write them out, even in Chinese.
    grammar = []
    for _, text in analyses:
        label = text.split(",", 1)[0].split(" (", 1)[0]
        if label and label not in grammar:
            grammar.append(label)
    poses = {pos for pos, _ in analyses if pos}
    own = {f.lower() for f in forms + [answer]}
    base_forms = [c for c in canonical if c.lower() not in own]
    meanings = []
    lemmas = list(canonical)[:CLUE_GLOSS_LEMMAS]
    for lemma, entries in find_glosses_for_canonicals(lemmas, language).items():
        matching = [e for e in entries if e.get("pos") in poses]
        kept = []
        for entry in matching or entries:
            for gloss in entry.get("glosses") or []:
                if len(kept) < CLUE_GLOSSES_PER_LEMMA:
                    kept.append((lemma, entry.get("pos") or "?", gloss))
        meanings.extend(kept)
    return grammar, meanings, base_forms


def _grid_form(text):
    """`text` as the grid writes it: a ligature letter split into two,
    accents dropped, uppercase — to compare a reply's words with the
    answer whatever their spelling."""
    for lig, split in (("œ", "oe"), ("Œ", "OE"), ("æ", "ae"), ("Æ", "AE")):
        text = text.replace(lig, split)
    text = "".join(c for c in unicodedata.normalize("NFKD", text) if not unicodedata.combining(c))
    return text.upper()


def _letter_runs(text):
    """`(start, end)` of every run of letters in `text`."""
    runs, start = [], None
    for i, ch in enumerate(text):
        if ch.isalpha():
            if start is None:
                start = i
        elif start is not None:
            runs.append((start, i))
            start = None
    if start is not None:
        runs.append((start, len(text)))
    return runs


def _root(word):
    """The part of `word` a hint must not start a word with: the word
    minus its last two letters, never shorter than CLUE_STEM_MIN_LETTERS."""
    return word[:max(CLUE_STEM_MIN_LETTERS, len(word) - 2)]


def _hint_leak_spans(text, word, message, language):
    """`(start, end)` of every word of hint `text` that gives away part of
    `word`'s answer: the answer itself, one of its base forms, a word
    starting with the root of either (a root of CLUE_STEM_MIN_LETTERS
    letters at least), or a word of CLUE_STEM_MIN_LETTERS letters or more
    that the wordlist gives as a form of one of those base forms. A word the player wrote in `message` is never
    counted — a proposal they typed can be echoed back. The base forms come
    from `_word_forms`."""
    answer = _grid_form(word.get("answer") or "")
    _, canonical = _word_forms(word, language)
    forbidden = [f for f in [answer] + [_grid_form(c) for c in canonical] if f]
    roots = [_root(f) for f in forbidden if len(_root(f)) >= CLUE_STEM_MIN_LETTERS]
    allowed = {_grid_form(message[a:b]) for a, b in _letter_runs(message)}
    bases = {c.lower() for c in canonical}
    spans = []
    for start, end in _letter_runs(text):
        token = _grid_form(text[start:end])
        if token in allowed:
            continue
        if token in forbidden or any(token.startswith(r) for r in roots):
            spans.append((start, end))
        elif len(token) >= CLUE_STEM_MIN_LETTERS and any(
                c.lower() in bases for _, cs in word_forms(text[start:end], language) for c in cs):
            # Another form of one of the answer's base forms ("boivent" for
            # BUSSENT), found through the wordlist.
            spans.append((start, end))
    return spans


_synonym_clients = None


def _corpus_sentences(word, language):
    """Up to CLUE_SENTENCE_COUNT real sentences of the reference corpus
    (data/reference_corpus/, `example_sentences.find_examples_for_words`,
    drawn at random) using the answer's exact inflected form — the first of
    its forms (`_word_forms`) that has any. Empty when none has."""
    forms, _ = _word_forms(word, language)
    for form in forms or [(word.get("answer") or "").lower()]:
        if not form:
            continue
        sentences = find_examples_for_words([form], language, limit=CLUE_SENTENCE_COUNT).get(form)
        if sentences:
            return sentences
    return []


def _qdrant_synonyms(word, language):
    """Example synonyms of `word`'s answer for the CLUE prompt, as
    `(base form, definition)` pairs, from the Qdrant `words` collection.
    One search per query — the answer's grid form, its inflected forms and
    its possible base forms (`_word_forms`) — reading CLUE_SYNONYM_POOL
    nearest neighbours each; a neighbour's score is its best one over
    those searches, and must reach CLUE_SYNONYM_MIN_SCORE. The collection
    embeds only spellings, so those neighbours are often look-alikes: a
    neighbour must also reach CLUE_SYNONYM_MIN_MEANING of similarity (its
    stored vector against the embedded first CLUE_SYNONYM_GLOSSES
    definitions of the answer's base form, from `_clue_grounding`) — no
    example at all without such a definition. A neighbour is skipped as
    well when it or one of its base forms is the answer or one of its base
    forms, or starts with the root (`_root`) of either, when its first base
    form was already taken or is capitalized (a proper noun), or when that
    base form has no definition in the gloss dictionary (the model checks
    each example against it). Best score first, up to CLUE_SYNONYM_COUNT.
    Empty when Qdrant or the embedding server is unavailable."""
    global _synonym_clients
    answer = word.get("answer") or ""
    forms, canonical = _word_forms(word, language)
    _, meanings, _ = _clue_grounding(word, language)
    lemma = meanings[0][0] if meanings else ""
    meaning = " ".join([g for l, _, g in meanings if l == lemma][:CLUE_SYNONYM_GLOSSES])
    if not meaning:
        return []
    forbidden = {f for f in [_grid_form(answer)] + [_grid_form(c) for c in canonical] if f}
    roots = [r for r in (_root(f) for f in sorted(forbidden)) if len(r) >= CLUE_STEM_MIN_LETTERS]
    queries = []
    for q in [answer] + forms + canonical:
        if q and q not in queries:
            queries.append(q)
    best = {}
    try:
        if _synonym_clients is None:
            _synonym_clients = (QdrantStore(timeout=CLUE_SYNONYM_TIMEOUT),
                                Embedder(timeout=CLUE_SYNONYM_TIMEOUT))
        store, embedder = _synonym_clients
        for query in queries:
            for hit in store.search(embedder.embed(query), lang=language, limit=CLUE_SYNONYM_POOL):
                payload = hit.get("payload") or {}
                w = payload.get("word")
                score = hit.get("score", 0.0)
                if w and (w not in best or score > best[w][0]):
                    best[w] = (score, payload)
        candidates, seen = [], set()
        for w, (score, payload) in sorted(best.items(), key=lambda kv: -kv[1][0]):
            if score < CLUE_SYNONYM_MIN_SCORE:
                break
            bases = [b for b in (payload.get("canonical") or payload.get("accented") or w).split(";") if b]
            forms_of_hit = [_grid_form(w)] + [_grid_form(b) for b in bases]
            if any(f in forbidden or any(f.startswith(r) for r in roots) for f in forms_of_hit):
                continue
            base = bases[0] if bases else w
            if base in seen or base[:1].isupper():
                continue
            seen.add(base)
            entries = find_glosses_for_canonicals([base], language).get(base) or []
            gloss = next((g for e in entries for g in (e.get("glosses") or [])), "")
            if gloss:
                candidates.append((w, base, gloss))
        meaning_vector = embedder.embed(meaning) if candidates else []
        vectors = store.retrieve_word_vectors(language, [w for w, _, _ in candidates])
    except (QdrantStoreError, EmbedderError) as e:
        logger.warning("chat: Qdrant synonyms unavailable for %r: %s", answer, e)
        return []
    found = []
    for w, base, gloss in candidates:
        vector = vectors.get(w)
        if vector is None or sum(a * b for a, b in zip(vector, meaning_vector)) < CLUE_SYNONYM_MIN_MEANING:
            continue
        found.append((base, gloss))
        if len(found) >= CLUE_SYNONYM_COUNT:
            break
    logger.info("chat: Qdrant synonyms for %r (queries %r): %r", answer, queries, found)
    return found


def _parse_route(content):
    """The routing keyword in a classifier answer (ROUTE_USAGE/ROUTE_CLUE/
    ROUTE_ANSWER):
    its last one, since a model that reasons before answering ends on its
    verdict; `None` when it names neither."""
    found = re.findall(r"\b(USAGE|CLUE|ANSWER)\b", content.upper())
    return found[-1] if found else None


def _strip_think_block(content):
    """`content` without a `<think>...</think>` block — the non-streamed
    classifier call's counterpart of reply_stream's streaming filter: the
    text after the last `</think>` when there is one, "" for a block that
    never closed, `content` unchanged otherwise."""
    if _THINK_CLOSE in content:
        return content.rsplit(_THINK_CLOSE, 1)[1]
    if _THINK_OPEN in content:
        return ""
    return content


def _trim_history(history, count):
    """The last `count` messages of `history`, never starting on an
    assistant turn (a chat template expects the user to speak first)."""
    kept = list(history[-count:]) if count > 0 else []
    while kept and kept[0].get("role") != "user":
        kept.pop(0)
    return kept


def _format_words_block(words):
    """One line per grid word, at the user's explicit request: "il est
    informé de la liste des définitions qui doit contenir toutes les
    infos utiles pour comprendre la grille : numéro de ligne et de
    colonne de chaque mot, vertical ou horizontal, définition, valeur du
    mot (réponse)." `words` is the frontend's own `puzzle.words` shape
    (row/col are 0-based internally — shown 1-based here, matching what
    the player actually sees on screen, since header row/column numbers
    in the grid are 1-based too).

    Each line also carries `language=` — `w.get("language")`, already set
    per word by crossword_gen.generate_grid (its own `language` for an
    ordinary grid, or, on a bilingual grid, the word's own direction-based
    language — see that function's docstring) — at the user's explicit
    request: "répond dans la langue du mot quand une aide est demandée
    pour remplir la grille (langue différente suivant si c'est horizontal
    ou vertical)." On an ordinary, monolingual grid every word shares the
    exact same `language` value, so this is a no-op for the system
    prompt's own per-word-language rule below (see _build_system_prompt);
    it only ever matters once two different values actually appear here,
    i.e. a genuinely bilingual grid."""
    lines = []
    for w in words:
        direction = "Down" if w.get("direction") == "down" else "Across"
        lines.append(
            f"- ({w.get('row', 0) + 1}, {w.get('col', 0) + 1}) {direction}: "
            f"clue={w.get('clue') or '(none yet)'!r}, answer={w.get('answer', '')!r}, "
            f"language={w.get('language') or '?'!r}"
        )
    return "\n".join(lines)


def _words_touching_cell(cell, words):
    """Every word from `words` whose own cell span — starting at its own
    (row, col), running `len(answer)` cells in its own direction (Across
    = rightward, Down = downward) — includes `cell`. Computed here in
    Python rather than left for the LLM to derive from raw coordinates
    (an earlier version of this prompt only ever stated a cell's own
    position and left the spatial reasoning itself to the model), at the
    user's own explicit follow-up report: a real test showed the small
    local model couldn't reliably answer "what is the selected word" even
    though the raw position and full word list were both already present
    in the prompt. Whether that was a genuine model limitation or a real
    information gap, computing the actual answer here and stating it
    directly removes the ambiguity either way — returns 0 words (a cell
    not covered by any slot, shouldn't normally happen), 1 (only one
    direction has a real word through that cell), or 2 (an across word
    and a down word both touching the same cell). Used for `filling_cell`
    (the clicked cell — see _build_system_prompt's own docstring for why
    that's a different concept from the hovered word, which is instead
    resolved by _find_word_by_start below)."""
    if not cell:
        return []
    row, col = cell.get("row", 0), cell.get("col", 0)
    matches = []
    for w in words:
        w_row, w_col = w.get("row", 0), w.get("col", 0)
        length = len(w.get("answer") or "")
        if w.get("direction") == "down":
            if col == w_col and w_row <= row < w_row + length:
                matches.append(w)
        else:
            if row == w_row and w_col <= col < w_col + length:
                matches.append(w)
    return matches


def _find_word_by_start(word_start, words):
    """The single word from `words` whose own (row, col, direction) exactly
    matches `word_start` — used for `hovered_word`, which the frontend
    (script.js's highlightWordAt()) already resolves down to one specific
    word's own starting position and direction before ever sending it, so
    (unlike `_words_touching_cell`, keyed by a bare click position that
    can belong to two crossing words at once) there's at most one exact
    match here. Returns `None` if nothing matches (unexpected — the
    frontend only ever sends a real word's own start)."""
    if not word_start:
        return None
    row, col = word_start.get("row", 0), word_start.get("col", 0)
    direction = word_start.get("direction")
    for w in words:
        if w.get("row", 0) == row and w.get("col", 0) == col and w.get("direction") == direction:
            return w
    return None


def _resolve_selection(ui_context):
    """The word currently selected in the grid, resolved once from
    `ui_context`, in the priority order rule 4a uses: the hovered word
    if any, else the word being filled at the clicked cell (the lone
    word there, or — at a crossing — the one matching the grid's current
    fill direction `active_direction`). Shared by `_build_system_prompt`
    (the full interface-state block) and `ChatBot._current_selection_line`
    (the one-line restatement prepended to every player message), so the
    two can never disagree about which word is selected."""
    puzzle_loaded = bool(ui_context.get("puzzle_loaded"))
    words = ui_context.get("words") or []
    hovered_word = ui_context.get("hovered_word")
    filling_cell = ui_context.get("filling_cell")
    active_direction = ui_context.get("active_direction")
    hovered_resolved = _find_word_by_start(hovered_word, words) if hovered_word else None
    filling_words = (
        _words_touching_cell(filling_cell, words) if (filling_cell and puzzle_loaded) else []
    )
    if len(filling_words) == 1:
        filling_word = filling_words[0]
    elif len(filling_words) >= 2 and active_direction:
        filling_word = next(
            (w for w in filling_words if w.get("direction") == active_direction), None
        )
    else:
        filling_word = None
    return {
        "puzzle_loaded": puzzle_loaded,
        "words": words,
        "hovered_word": hovered_word,
        "filling_cell": filling_cell,
        "active_direction": active_direction,
        "hovered_resolved": hovered_resolved,
        "filling_words": filling_words,
        "filling_word": filling_word,
        "help_word": hovered_resolved or filling_word,
    }


class ChatBot:
    """One instance is enough for the process's lifetime — construct once
    (e.g. at module level in backend/app.py) and call `reply()` per
    message, the same usage convention as LLMClueGenerator.

    base_url/model/api_key mirror LLMClueGenerator's own optional
    constructor overrides (`None` by default, i.e. no effect for
    `ChatBot()` — falls back to the environment exactly as before): used
    by backend/app.py to point this one instance at a second, dual-GPU
    LLM server dedicated to interactive requests, without this class ever
    reading a second set of environment variables itself."""

    def __init__(self, base_url=None, model=None, api_key=None):
        self.base_url = base_url or os.environ.get("LLM_BASE_URL", DEFAULT_LLM_BASE_URL)
        self.model = model or os.environ.get("LLM_MODEL", DEFAULT_LLM_MODEL)
        self.api_key = api_key or os.environ.get("LLM_API_KEY", DEFAULT_LLM_API_KEY)
        self.think_filter = os.environ.get("CHATBOT_THINK_FILTER", DEFAULT_CHATBOT_THINK_FILTER)
        if self.think_filter not in CHATBOT_THINK_FILTER_CHOICES:
            logger.warning(
                "CHATBOT_THINK_FILTER=%r is not one of %s — falling back to %r.",
                self.think_filter, CHATBOT_THINK_FILTER_CHOICES, DEFAULT_CHATBOT_THINK_FILTER,
            )
            self.think_filter = DEFAULT_CHATBOT_THINK_FILTER

    def _build_system_prompt(self, language, ui_context):
        """Builds the one system message driving every reply: persona,
        strict scope/politeness rules (at the user's explicit request),
        DOC_USER's own content, and the live UI state (hovered word,
        filling cell, full word list if a grid is loaded) — rebuilt fresh
        on every call rather than cached, since the UI state itself can
        change between messages within the same conversation (a player
        can move the mouse, click a different cell, or a new grid can
        finish generating, mid-chat).

        Reports two genuinely distinct concepts as two separate state
        blocks, never conflated, at the user's own explicit correction:
        "un mot est sélectionné en passant la souris au dessus sans
        forcément cliquer sur une case. Faire la différence entre 'mot
        sélectionné' (survol) et 'case/mot en cours de remplissage'
        (cliqué)." An earlier version of this prompt only ever reported
        the clicked cell under the label "selected" — which is what "quel
        est le mot sélectionné" actually asks about the *hovered* word,
        not the click-to-type target; a real live test with that earlier
        wording showed the model reliably answering with the wrong word
        (whichever happened to be the click target, or — with nothing
        clicked at all — the very first word of the grid, invented
        outright despite the prompt already saying no cell was selected).
        `hovered_word` (script.js's own `hoveredWord`, already resolved to
        one exact word's own start+direction by highlightWordAt() before
        it's ever sent) is what answers "mot sélectionné"; `filling_cell`
        (script.js's own `selected`, the click-to-type target, no
        direction of its own — see selectCell()) is a separate, clearly
        distinctly-labeled concept the model is told never to confuse
        with it.

        Rule 4 (hints) leans on both: a hint request is resolved to the
        hovered word if any, else the word(s) at the clicked cell, else a
        word the player's own message names, else a refusal that asks the
        player to hover/click first. The reply must name which word it
        chose (short preamble only), then give one or two synonyms of
        another root, or a new definition different from its on-screen
        clue — never the answer, a word of its family, any of its letters
        or its letter count, unless the player explicitly asked for it. The clicked-cell word's own clue is
        passed to the model (the `filling_words` block, with `clue=` and
        `answer=`), so it always has what it needs to build that hint.

        Reply language: the ENTIRE prompt (every "reply in X" instruction,
        the state-block note, the final reminder) is built in ONE language
        — `reply_language`: the language of the word/direction currently
        selected in the grid (the hovered word, else the word being filled
        at the clicked cell — resolved by `ui_context["active_direction"]`
        when that cell is a crossing of two words), falling back to the
        interface `language` when nothing is selected or the selection
        shares the interface language. `active_direction` is essential on
        a bilingual grid: a clicked crossing cell touches one across and
        one down word in two different languages, so without knowing which
        direction the player is filling, `reply_language` could not be
        resolved and the whole reply defaulted to the interface language
        (the concrete bug this fixed — vertical fill mode on an FR/EN grid
        still got a French prompt). On a bilingual grid this means selecting an English
        (e.g. down) word makes David FALCON answer that message entirely
        in English, preamble included. There is deliberately no "reply in
        the interface language but write the hint content in the word's
        language" nuance — this project's small local model could not
        follow it (a hint for a selected English word kept coming back in
        French).

        Which word is selected is stated THREE times, deliberately
        redundantly, because a small model kept answering about a word
        from earlier in the conversation instead: in the interface-state
        block (mid-prompt, detailed), in a boxed `SELECTED WORD RIGHT
        NOW` block appended after the FINAL REMINDER (the very end of the
        system prompt), and — outside this method — in a one-line NOTE
        `reply_stream()` prepends to the player's own message (highest
        recency, right before the question). The monolingual system
        prompt is no longer byte-for-byte identical to its pre-bilingual
        form: it now carries the trailing SELECTED WORD block too (the
        FINAL REMINDER text itself is unchanged)."""
        doc_user = _load_doc_user()

        sel = _resolve_selection(ui_context)
        puzzle_loaded = sel["puzzle_loaded"]
        hovered_word = sel["hovered_word"]
        filling_cell = sel["filling_cell"]
        # The grid's current fill/selection direction ("across"/"down") —
        # used to pick which of two crossing words a clicked cell is
        # about, and hence which language to answer in.
        active_direction = sel["active_direction"]
        words = sel["words"]

        state_lines = [f"A crossword puzzle is currently loaded: {puzzle_loaded}."]
        filling_words = sel["filling_words"]
        hovered_resolved = sel["hovered_resolved"]
        # The single word being filled: the lone word at the clicked cell,
        # or — at a crossing — the one matching `active_direction`.
        filling_word = sel["filling_word"]

        # A grid is bilingual when its words don't all share one language
        # (crossword_gen.generate_grid stamps each word with its own
        # direction-based language on a bilingual grid — see
        # _format_words_block).
        word_languages = {w.get("language") for w in words if w.get("language")}
        is_bilingual_grid = len(word_languages) > 1
        # The single word a help request would currently be about, in the
        # same priority order rule 4a uses: hovered word first, else the
        # word being filled (see `filling_word` above — direction-resolved
        # when the clicked cell is a crossing).
        help_word = hovered_resolved or filling_word
        help_lang = help_word.get("language") if help_word else None
        help_dir_label = (
            "DOWN (vertical)" if help_word and help_word.get("direction") == "down"
            else "ACROSS (horizontal)"
        )
        # The language the WHOLE reply is written in. Deliberately NOT
        # "reply in the interface language, but write the hint content in
        # the word's language" — that nuance was hammered on so hard by
        # rule 2 that this project's small local model could not follow it
        # (a hint for a selected English word still came back in French).
        # Instead: whenever the direction currently selected in the grid
        # (hovered word, else the lone word at the clicked cell) is in a
        # different language, the entire prompt — and so the entire reply
        # — switches to that one language, at the user's explicit request:
        # "adapter le prompt pour être entièrement dans la langue
        # sélectionnée suivant le sens la grille." Falls back to the
        # interface language when nothing is selected, or the selected
        # word shares the interface language (an ordinary monolingual grid
        # always lands here, byte-for-byte unchanged).
        reply_language = help_lang if (puzzle_loaded and help_lang) else language
        reply_language_differs = reply_language != language
        language_name = LANGUAGE_NAMES.get(reply_language, reply_language)
        interface_language_name = LANGUAGE_NAMES.get(language, language)
        if puzzle_loaded:
            # Order matters here, deliberately: the full word list is
            # listed FIRST and the two resolved, single-answer states
            # (filling cell, then hover) come LAST, right before the
            # player's own question — the opposite order from this
            # method's first version. A live test with the resolved
            # states listed first (word list last) reliably answered
            # "mot sélectionné" with the grid's first-listed word instead
            # of the real, explicitly-stated hovered word, even though the
            # correct answer was already spelled out unambiguously a few
            # lines above it — a small-model recency bias (the word list,
            # being the very last thing read before the question, kept
            # winning over an earlier, clearly-labeled answer). Moving the
            # resolved hover state to be the actual last line read before
            # the question is a direct countermeasure to that bias, not
            # just a cosmetic reordering.
            if words:
                state_lines.append(
                    "Every word currently in the grid, with its starting (row, column) "
                    "(1-based, matching the grid's own on-screen headers), direction, clue, "
                    "and answer:\n" + _format_words_block(words)
                )
            # --- "case/mot en cours de remplissage" (click-to-type target)
            # — a genuinely separate concept from the hovered word below;
            # a player can have a cell clicked for typing with the mouse
            # hovering somewhere else entirely, or vice versa.
            if filling_cell:
                # `filling_words` (the word(s) at that exact cell) is
                # computed once above, near `state_lines` — directly here
                # in Python, not left for the model to derive from raw
                # coordinates (see _words_touching_cell's own docstring).
                # 0-based internally, shown 1-based below to match the
                # grid's own on-screen row/column headers.
                state_lines.append(
                    f"Separately (this is NOT the hovered word further below), the player "
                    f"has clicked cell (row {filling_cell.get('row', 0) + 1}, column "
                    f"{filling_cell.get('col', 0) + 1}) to type an answer into."
                )
                if filling_words:
                    if len(filling_words) >= 2 and filling_word is not None:
                        crossing_note = (
                            f" Two words cross at that cell, but the grid is in "
                            f"{'DOWN (vertical)' if active_direction == 'down' else 'ACROSS (horizontal)'} "
                            f"fill mode, so the word being filled — and the one any hint/help "
                            f"is about — is the "
                            f"{'DOWN (vertical)' if filling_word.get('direction') == 'down' else 'ACROSS (horizontal)'} "
                            f"one: (row {filling_word.get('row', 0) + 1}, column "
                            f"{filling_word.get('col', 0) + 1}). Do NOT ask the player which "
                            f"word — it is that one."
                        )
                    elif len(filling_words) >= 2:
                        crossing_note = (
                            " Two words cross at that cell and the grid's fill direction is "
                            "unknown — ask the player which of the two they mean, unless they "
                            "already said."
                        )
                    else:
                        crossing_note = ""
                    state_lines.append(
                        "The word(s) occupying that exact clicked cell right now (the "
                        "word being filled in). If the player asks for a hint and NO word "
                        "is hovered (see below), this is the word the hint is about."
                        + crossing_note
                        + " The line(s) below carry 'clue=' and "
                        "'answer=' for your internal use — NEVER quote such a line back to "
                        "the player, and never let its 'answer=' value appear in your "
                        "reply:\n"
                        + _format_words_block(filling_words)
                    )
                else:
                    state_lines.append(
                        "No listed word currently covers that exact clicked cell "
                        "(unexpected — treat this as if no cell were selected for typing)."
                    )
            else:
                state_lines.append(
                    "No cell is currently selected for typing (separate from the hovered "
                    "word further below)."
                )
            # --- "mot sélectionné" (hover) — a genuinely separate concept
            # from the click-to-type target above; kept last, deliberately,
            # per this block's own opening comment.
            if hovered_word:
                resolved = hovered_resolved
                if resolved:
                    state_lines.append(
                        "The word currently under the player's mouse cursor (hover) — "
                        "THIS is the answer to \"what word is selected\" / \"quel est le "
                        "mot sélectionné\", NOT the filling-cell state above:\n"
                        + _format_words_block([resolved])
                    )
                else:
                    state_lines.append(
                        "The player's mouse is over a word in the grid, but it could not "
                        "be matched against the word list (unexpected) — treat this as if "
                        "no word were hovered."
                    )
            else:
                no_target = not filling_words
                state_lines.append(
                    "No word is currently under the player's mouse (hover) right now. If "
                    "the player asks what word is selected/hovered ('mot sélectionné'), "
                    "you MUST reply that no word is currently hovered and ask them to "
                    "move their mouse over a word in the grid or a clue first — do NOT "
                    "name any word from the list above as if it were hovered, not even "
                    "the first one listed."
                    + (
                        " The same applies to a HINT request: with no word hovered AND no "
                        "cell clicked (see above), you have NO word to give a hint for — "
                        "you MUST ask the player to hover a word or click a cell first, "
                        "and say NOTHING else. Do NOT pick a word from the list, do NOT "
                        "use a word from the examples in rule 4, do NOT give any hint."
                        if no_target else ""
                    )
                )

            # Bilingual-grid language note — kept LAST in the state block
            # (highest recency for a small model). No nuance for it to
            # parse: the whole `language_name` used everywhere in this
            # prompt is ALREADY the language of the currently-selected
            # direction, so this line just states plainly, once, that the
            # ENTIRE reply is in that one language.
            if is_bilingual_grid and reply_language_differs:
                state_lines.append(
                    f"This is a BILINGUAL grid. The word currently selected in it (the "
                    f"{help_dir_label} word) is written in {language_name} — DIFFERENT from "
                    f"the interface language ({interface_language_name}). Write your ENTIRE "
                    f"reply to this message (preamble, hint/definition/answer, and every "
                    f"other sentence) in {language_name}. Do NOT use "
                    f"{interface_language_name} anywhere in this reply."
                )
            elif is_bilingual_grid:
                state_lines.append(
                    "This is a BILINGUAL grid (across and down words are in two different "
                    f"languages). The currently-selected word, if any, is in {language_name}: "
                    f"reply in {language_name} as usual."
                )

        # The very last line of the whole prompt — strongest recency for a
        # small model. `language_name` is already the correct per-selection
        # language (the interface language whenever nothing differing is
        # selected). The base sentence is byte-for-byte unchanged; the
        # hint-safety clause is appended only when a grid is loaded (a
        # no-grid chat stays byte-identical), reinforcing rule 4d at the
        # recency position because the small model keeps ending hints with
        # "the answer is <word>".
        final_reminder = (
            f"write your entire reply in {language_name}, "
            "starting directly with the answer and no greeting."
            + (
                " And if the player asked for a HINT (not an explicit answer request), the "
                "solution word must NOT appear anywhere in your reply — never end a hint "
                "with \"the answer is ...\", never spell it out, never confirm it, never give "
                "any of its letters. A hint gives a synonym of another root or a new "
                "definition, never just a paraphrase of its clue."
                if puzzle_loaded else ""
            )
        )

        # A crisp, isolated restatement of the currently-selected word at
        # the VERY END of the system prompt, at the user's explicit
        # request: "le prompt contient bien l'info Horizontal, mais elle
        # est noyée dans d'autres explications. Reforce la visibilité de
        # cette information, en la précisant bien clairement en fin de
        # prompt système." The same fact is in the interface-state block
        # above and in the per-message NOTE prefix (see
        # `_current_selection_line`) — this is the third, deliberately
        # redundant, hardest-to-miss copy.
        if not puzzle_loaded:
            selected_word_block = ""
        elif help_word is None:
            selected_word_block = (
                "\n\n==================================================\n"
                "SELECTED WORD RIGHT NOW: none (nothing hovered, no single word at a "
                "clicked cell). A request for a hint/help/the answer that names no word "
                "must be met by asking the player to hover a word or click a cell — never "
                "by reusing a word from an earlier reply.\n"
                "=================================================="
            )
        else:
            _swr, _swc = help_word.get("row", 0) + 1, help_word.get("col", 0) + 1
            _swk = (
                "the word under the player's mouse (hovered)"
                if sel["hovered_resolved"]
                else "the word at the cell the player clicked"
            )
            selected_word_block = (
                "\n\n==================================================\n"
                f"SELECTED WORD RIGHT NOW: the {help_dir_label} word starting at (row "
                f"{_swr}, column {_swc}) — {_swk}. Its clue and answer are its own line in "
                f"the word list above.\n"
                f"Any request for a hint / help / the answer that does not explicitly name "
                f"a DIFFERENT word is about THIS {help_dir_label} word at (row {_swr}, "
                f"column {_swc}). Do NOT answer about a word from an earlier reply in this "
                f"conversation — the player may have selected a new one since.\n"
                "=================================================="
            )

        # Rules 4b/4e give their preamble examples in French. When the
        # reply language is NOT the interface language, a small model
        # copies those French words ("Indice", "Le mot ... est") verbatim
        # into an otherwise-correct reply — so, only then, spell out that
        # the preamble is in `language_name` too and show how it reads.
        # Empty (nothing spliced) for every same-language case, so the
        # monolingual prompt is byte-for-byte unchanged.
        preamble_lang_note = (
            f" Write this preamble in {language_name} as well: the French example above "
            f"shows the STYLE only, so do NOT copy its French words — in {language_name} it "
            "would read, for instance, \"Hint for the vertical word at (l, c), the one at "
            "the clicked cell:\" (and, for an ANSWER request in rule 4e, \"The vertical word "
            "at (l, c) is: …\")."
            if reply_language_differs else ""
        )

        # Everything that never varies between two requests — the
        # introduction and the whole DOC_USER text (~20k tokens) — comes
        # first, before any language- or state-dependent text, so the LLM
        # server's prefix cache (SGLang's radix cache, llama.cpp's prompt
        # cache) reuses it across every chat request instead of
        # recomputing it whenever the reply language, the loaded grid or
        # the selection changes.
        return (
            _fixed_prompt_head(doc_user)
            + f"Write EVERY reply entirely in {language_name}. This is not optional and applies "
            "to every message you ever send.\n\n"
            "STRICT RULES:\n"
            "1. Always reply extremely politely.\n"
            f"2. LANGUAGE. Your entire reply MUST be written in {language_name} — every word "
            "of it, not just the first sentence. This holds no matter what language the "
            f"player writes to you in: reply in {language_name} anyway. It also holds even "
            "though many examples in these rules happen to be written in French — those "
            "French snippets illustrate FORMAT and WORDING STYLE only, never the language "
            f"to answer in. If {language_name} is not French, do NOT reply in French. "
            "The same goes for the reference documentation above: it is in English, but "
            f"whatever you take from it must be written in {language_name}, rephrased, never "
            "quoted in English."
            + (
                f" {language_name} is already the language of the direction currently "
                "selected in this bilingual grid (the across and down words are in two "
                "different languages, and this whole prompt is built in whichever one the "
                "selected direction uses) — there is no exception to weigh and nothing to "
                f"switch mid-reply: the WHOLE reply is in {language_name}, start to finish.\n"
                if is_bilingual_grid else "\n"
            )
            + "3. You must ONLY answer questions about using this interface, or about solving/"
            "understanding the crossword grid currently on screen. For ANY other question "
            "(general knowledge, other software, personal questions, anything unrelated to "
            "this app or its current grid — e.g. 'what is the capital of...'), politely "
            "decline and suggest the player consult an appropriate website or resource for "
            "that topic instead. Do this and NOTHING else: never actually answer the "
            "out-of-scope question afterward, not even briefly, not even after declining — "
            "declining and then still giving the answer right after is exactly what this "
            "rule forbids.\n"
            "4. Helping with a word. FIRST decide which kind of request it is. If the "
            "player EXPLICITLY asks for the answer/solution/exact word (e.g. 'donne-moi la "
            "réponse', 'la réponse exacte', 'quel est le mot exact', 'quelle est la "
            "solution', 'dis-moi le mot'), that is an ANSWER request: skip straight to "
            "rule 4e and give the answer. Otherwise ('un indice', 'aide-moi', 'je suis "
            "bloqué', a question about a clue — anything not explicitly asking for the "
            "answer) it is a HINT request; if genuinely unsure, treat it as a HINT. "
            "Handle a HINT request with steps a–d below; handle an ANSWER request with "
            "step e:\n"
            "   a. FIRST work out WHICH word the hint is about. Every one of the player's "
            "messages is prefixed with a short 'NOTE — right now the player has ... "
            "selected' line stating which word is selected AT THE MOMENT of that message. "
            "Trust that NOTE above everything else: it is what is true NOW, even if an "
            "earlier reply of yours in this conversation was about a different word — the "
            "player may have selected a new one since. NEVER carry a previous reply's word "
            "into a new one. If the NOTE and the fuller interface state below ever seem to "
            "disagree, the NOTE wins. Failing a NOTE, use the interface state below:\n"
            "      - If a word is under the player's mouse (the hovered / 'selected' word — "
            "'mot sélectionné' — see the state below), the hint is about THAT word.\n"
            "      - Otherwise, if the player has a cell clicked for typing (the 'filling' "
            "cell in the state below), the hint is about the word — or the two crossing "
            "words — at that clicked cell.\n"
            "      - If the player's own message names a word (a clue number, a position "
            "like '3 horizontal', a direction), use that word instead.\n"
            "      - If NONE of these identifies a word (nothing hovered, no cell clicked, "
            "and the message does not say which word), do NOT guess and do NOT pick a word "
            "from the list: politely ask the player to move their mouse over a word (or a "
            "clue), or to click a cell of the word they need help with, and stop there.\n"
            "      - If a cell is clicked at the crossing of two words and nothing is "
            "hovered, say so and ask which of the two they mean (or, if both hints are "
            "short, give one for each, each clearly labelled).\n"
            "   b. Begin with a SHORT preamble naming WHICH word it is about — ONLY its "
            "starting (row, column), its direction, and whether it is the word under the "
            "mouse (hovered / selected) or the word at the clicked cell — so the player "
            "can correct you. One short clause, e.g. « Indice pour le mot vertical en "
            "(l, c), celui de la case cliquée : »."
            + preamble_lang_note
            + " The preamble must contain NOTHING else: "
            "do NOT repeat the on-screen clue text in it, do NOT include the word's answer, "
            "and NEVER paste a raw line from the interface state below (those lines contain "
            "'clue=' and 'answer=' fields that must not appear in your reply). This "
            "preamble is NOT the hint. (If the word's clue shows '(none yet)', just say its "
            "clue has not been generated yet.)\n"
            "   c. THE HINT MUST HELP FIND THE WORD WITHOUT GIVING ANY PART OF IT. Unless "
            "the player explicitly asks for it, never give a letter of the solution (not "
            "its first letter, not its last letter, no letter at all), its letter count, "
            "or a word of its family. Instead give ONE of these: one or two synonyms of "
            "the word that do NOT share its root (not the same canonical form, not the "
            "same word family), or a NEW definition of your own, clearly DIFFERENT from "
            "the clue the player already sees on screen (another angle: what it is used "
            "for, the category it belongs to, a typical example of it). It is forbidden to "
            "copy the existing 'clue=' text, to lightly reword or reorder it, or to reply "
            "with just the position + that clue (the player already has every bit of "
            "that). You MAY confirm or deny one specific letter the player proposes.\n"
            "   c-bis. Describe the word EXACTLY as it appears in the grid — its part of "
            "speech, and for an inflected form its number, tense and person. Do NOT slide "
            "into a meaning that belongs only to a similar-looking word, or to a DIFFERENT "
            "form of the same root. A dictionary lookup is done on the root form and can "
            "list senses the exact grid word cannot carry: e.g. English \"ares\" is the "
            "plural of \"are\" (a unit of area) and must NOT be hinted as a form of the "
            "verb \"be\", even though its root \"are\" is one. If the on-screen 'clue=' "
            "itself looks like it describes the wrong form, hint the word by the sense "
            "that actually fits its spelling in the grid.\n"
            "   d. A HINT reply must NEVER reveal the solution word. Do NOT write it, do NOT "
            "spell it out letter by letter, do NOT quote it from a state line, do NOT embed "
            "it in a sentence — and, in particular, do NOT end (or begin) the hint with a "
            "phrase such as \"The answer is ...\", \"It is ...\", \"The word is ...\", \"so "
            "the word is ...\", \"La réponse est ...\" followed by the solution. The "
            "'answer=' value in the state below is for your own silent check ONLY. Before "
            "you send a HINT, re-read your own draft: if that exact 'answer=' value appears "
            "in it in ANY form (any case, with or without spaces/dashes between letters), "
            "delete that part and send the rest. (This restriction is for HINT replies only "
            "— it does NOT apply to an explicit ANSWER request, see e.)\n"
            "   e. ANSWER request (the player explicitly asked for the answer/solution/"
            "exact word — see the top of rule 4). Here you DO give it: state which word "
            "(same short preamble as 4b) and then the exact answer plainly, e.g. « Le mot "
            "vertical en (l, c) est : … ». Rules b–d do not restrict this case.\n"
            "   - Example 1: word MAISON, clue on screen 'Habitation'. BAD: 'Le mot est "
            "MAISON' / 'M-A-I-S-O-N'. ALSO BAD (gives letters of the solution): '… : 6 "
            "lettres, commence par un M et finit par un N.' ALSO BAD (echoes the clue, or "
            "stays vague): '… : c'est une habitation.' / '… : un terme lié au logement.' "
            "GOOD: '… : un synonyme est « demeure ».' or '… : on y rentre le soir, et on y "
            "reçoit ses amis.'\n   - Example 2 (answer leak — the most common mistake): "
            "word CHEVAL. BAD: '… : un synonyme est « destrier ». La réponse est : "
            "CHEVAL.' — the final sentence RUINS the hint. ALSO BAD: a synonym of the same "
            "family (« chevaux », « chevalin »). GOOD: the same reply WITHOUT that last "
            "sentence.\n   A hint NEVER names the word and never gives any of its letters. "
            "The synonyms and definitions above belong to their own example word — find "
            "new ones for the player's OWN word, never reuse one from these rules.\n"
            "   These examples illustrate the SAME general rule — apply it to ANY word the "
            "player asks a hint about. The positions in the examples are illustrative only: "
            "NEVER copy a position from an example; always take the real one from the "
            "interface state below, and if the state says nothing is hovered and no cell "
            "is clicked, ask the player instead (rule 4a) — do not invent one.\n"
            "5. Keep replies reasonably short and conversational — this is a chat, not an "
            "essay.\n"
            "6. NEVER start your reply with a greeting (no \"Hello\", \"Hi\", \"Bonjour\", "
            "\"Hi there\", introducing yourself again by name, or any equivalent) — do this "
            "in NONE of your replies, not just most of them. A greeting has already been "
            "shown exactly once, separately, as this chat's own welcome message, before the "
            "player ever asked anything — that is the ONLY greeting this conversation will "
            "ever have. Every single reply after it, including your very first one, must "
            "start directly with the actual answer, with zero greeting or self-introduction "
            "of any kind.\n"
            "7. NEVER think out loud or show your reasoning process. Do not write things like "
            "\"Let me check...\", \"Okay, the user is asking...\", \"Looking at the rules...\", "
            "or any other deliberation about how you are deciding what to answer. Do not weigh "
            "several possible interpretations of the question in your reply, and do not start "
            "one answer then correct yourself mid-reply (\"wait, no\", \"actually\"...). Decide "
            "the final answer entirely before writing anything down, then write ONLY that "
            "final answer, starting directly with it — nothing before it.\n\n"
            "Current state of the interface:\n" + "\n".join(state_lines)
            + "\n\nFINAL REMINDER: " + final_reminder
            + selected_word_block
        )

    async def classify_question(self, history, message, timeout=CLASSIFY_TIMEOUT):
        """The first of the two LLM calls a question asked in play mode
        costs: one non-streamed call answering ROUTE_USAGE (using the
        software), ROUTE_CLUE (help filling the grid) or ROUTE_ANSWER (an
        explicit request for a word's answer). Returns `(route,
        raw_answer, messages)` — `messages` the exact request sent, for
        LOG_CHAT/; `route` is `None` when the answer names neither
        keyword or the call fails, and the reply then falls back to the
        combined prompt rather than failing the message."""
        messages = _build_classify_messages(history, message)
        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                response = await client.post(
                    self.base_url,
                    headers={"Authorization": f"Bearer {self.api_key}"},
                    json={
                        "model": self.model,
                        "messages": messages,
                        "temperature": CLASSIFY_TEMPERATURE,
                        "max_tokens": CLASSIFY_MAX_TOKENS,
                        "reasoning_effort": "none",
                    },
                )
                response.raise_for_status()
                content = response.json()["choices"][0]["message"]["content"] or ""
        except (httpx.HTTPError, KeyError, IndexError, TypeError, ValueError) as e:
            logger.warning("chat: question classification failed (%s, model=%r): %s",
                           self.base_url, self.model, e)
            return None, "", messages
        route = _parse_route(_strip_think_block(content))
        logger.info("chat: question classified as %s (raw answer %r)", route, content)
        return route, content, messages

    def _build_usage_prompt(self, language, ui_context):
        """System prompt of a USAGE reply: the fixed head (introduction +
        DOC_USER, shared with the combined prompt for the prefix cache)
        and the rules a question about the interface needs. No grid
        content at all — the words and their answers have no use here —
        and always the interface language."""
        language_name = LANGUAGE_NAMES.get(language, language)
        return (
            _fixed_prompt_head(_load_doc_user())
            + f"Write EVERY reply entirely in {language_name}. This is not optional and "
            "applies to every message you ever send.\n\n"
            "The player is playing the crossword grid on screen and asks you how the "
            "interface works.\n\n"
            "STRICT RULES:\n"
            "1. Always reply extremely politely.\n"
            f"2. LANGUAGE. Your entire reply MUST be written in {language_name} — every word "
            "of it, whatever language the player writes to you in. The reference "
            f"documentation above is in English: whatever you take from it must be written in "
            f"{language_name}, rephrased, never quoted in English.\n"
            "3. Answer ONLY questions about using this interface, from the documentation "
            "above. For a question unrelated to this app (general knowledge, other software, "
            "personal questions...), politely decline, suggest an appropriate website or "
            "resource for it, and say NOTHING else — never answer it anyway. If the message "
            "is in fact a request for help with a word of the grid, tell the player to move "
            "the mouse over that word (or click one of its cells) and ask you for a hint.\n"
            "4. Keep replies reasonably short and conversational — this is a chat, not an "
            "essay.\n"
            "5. NEVER start with a greeting or a self-introduction: the chat already greeted "
            "the player once. Start directly with the answer.\n"
            "6. NEVER think out loud or show your reasoning: write only the final answer.\n\n"
            f"FINAL REMINDER: write your entire reply in {language_name}, starting directly "
            "with the answer and no greeting."
        )

    def _build_clue_prompt(self, language, ui_context, answer_requested=False, message="",
                           synonyms=(), sentences=()):
        """System prompt of a CLUE reply, deliberately small so a small
        model can follow it closely: no DOC_USER, no word list — only the
        word selected right now (position, direction, clue, answer) and
        what to do with it: a hint is a synonym of another root or a new
        definition, never a letter of the answer. Written in that word's own language (the interface
        language when nothing is selected), like the combined prompt."""
        sel = _resolve_selection(ui_context)
        help_word = sel["help_word"]
        reply_language = (help_word.get("language") if help_word else None) or language
        language_name = LANGUAGE_NAMES.get(reply_language, reply_language)
        head = (
            "You are David FALCON, the assistant of a crossword web app. The player is "
            "solving the crossword grid on screen and asks you for help with one word of it.\n"
            f"Write your entire reply in {language_name}, whatever language the player "
            "writes to you in.\n\n"
        )
        if help_word is None:
            return head + (
                "No word is selected in the grid right now (nothing under the mouse, no "
                "single word at a clicked cell), so you do not know which word the player "
                f"means. Reply with ONE polite sentence in {language_name} asking the player "
                "to move the mouse over the word (or click one of its cells) and ask again. "
                "Say nothing else: no hint, no word, no greeting."
            )
        row, col = help_word.get("row", 0) + 1, help_word.get("col", 0) + 1
        direction = (
            "DOWN (vertical)" if help_word.get("direction") == "down" else "ACROSS (horizontal)"
        )
        kind = (
            "the word under the player's mouse" if sel["hovered_resolved"]
            else "the word at the cell the player clicked"
        )
        clue = help_word.get("clue") or ""
        clue_line = repr(clue) if clue else "none yet (no clue has been generated for it)"
        answer = help_word.get("answer", "")
        if answer_requested:
            # ROUTE_ANSWER: the player explicitly asked for the answer.
            return head + (
                f"THE WORD ({kind}, selected right now):\n"
                f"- position: row {row}, column {col}, {direction}\n"
                f"- clue shown to the player: {clue_line}\n"
                f"- solution: {answer!r}\n"
                "\nThe player EXPLICITLY asked for the answer of this word. Reply with ONE "
                f"sentence in {language_name} giving the word's position, its direction (in "
                f"{language_name} words) and its solution, {answer}. Nothing else: no hint, no "
                "greeting."
            )
        grammar, meanings, base_forms = _clue_grounding(help_word, reply_language)
        # The root forbidden in a hint (see _root).
        stem = _root(answer)
        context = (
            f"- The word: {kind}.\n"
            f"- Clue already shown to the player: {clue_line}\n"
            f"- Solution: {answer!r}.\n"
        )
        if grammar:
            context += (
                f"- Part of speech: {' / '.join(grammar)}"
                + (f", an inflected form of {', '.join(repr(b) for b in base_forms)}"
                   if base_forms else "")
                + ".\n"
            )
        if meanings:
            context += (
                "- Dictionary entries of its base form:\n"
                + "".join(f"  - {lemma!r} ({pos}): {gloss}\n" for lemma, pos, gloss in meanings)
            )
        if synonyms:
            context += (
                "- Possible synonyms, found by a similarity search and possibly imperfect "
                "(check each one's definition):\n"
                + "".join(f"  - {base!r}: {gloss}\n" for base, gloss in synonyms)
            )
        # A proposal the model could not check reliably by itself.
        proposed = any(
            _grid_form(message[a:b]) == _grid_form(answer) for a, b in _letter_runs(message)
        )
        base_list = "".join(f", its base form {b!r}" for b in base_forms)
        return head + (
            "# GOAL\n"
            f"Create a new clue for the solution {answer!r} (see CONTEXT), in {language_name}. "
            "The player already has the clue shown in the grid and cannot find the word with "
            "it: your new clue must describe the same word in a DIFFERENT way, so that they can "
            "guess it and write it in the grid themselves. Like a crossword clue, it is one or "
            "two short sentences that point to the word without writing it: another meaning of "
            "it, a synonym, what it is used for, where or when one meets it. It never contains "
            "the solution nor any part of it — unless the player clearly asks for the "
            "solution.\n\n"
            "# HOW TO HELP THE PLAYER (your answer)\n"
            f"Your answer, in {language_name}, is your new clue alone, starting directly "
            "(no greeting, no reasoning, no position, row, column or direction: the player "
            "already has the word selected). Build it from one or two of these:\n"
            "- pertinent synonyms of the solution, in the same form — from the list below "
            "when their definition fits, or your own;\n"
            "- for a conjugated or plural form: say it is one, and give a synonym of its base "
            "form;\n"
            "- a new clue or definition of your own, from another angle than the one shown "
            "(what it is for, what it is made of, who uses it...);\n"
            + ("- a paraphrase, in your own words, of one of the dictionary entries given in "
               "CONTEXT;\n" if meanings else "")
            + "- a context where the word is used: a typical situation, an example, a "
            "well-known expression with « … » in its place"
            + (", or one of the SENTENCES below with the word replaced by « … »" if sentences else "")
            + ".\n"
            "Depending on the message:\n"
            "- another hint: a different kind of help from your previous one;\n"
            "- a letter or a word to check (« C'est MAISON ? », « Ça commence par un B ? »): "
            + ("this message proposes the solution itself: tell them it is right;\n" if proposed
               else "say whether it is right, and if not, add a hint;\n")
            + "- the solution itself: say you will give it if they clearly ask for it;\n"
            "- another word of the grid: ask them to move the mouse over it or click one of "
            "its cells;\n"
            "- something unrelated to the grid: politely decline and suggest an appropriate "
            "resource.\n\n"
            "# CONTEXT\n"
        ) + context + (
            (
                "\n# SENTENCES\nReal sentences using the word, from a reference corpus:\n"
                + "".join(f"- {sentence}\n" for sentence in sentences)
            ) if sentences else ""
        ) + (
            "\n# FORBIDDEN (unless the player clearly asks for the solution) — never:\n"
            f"- write the solution {answer!r}{base_list}, or any word starting with {stem!r};\n"
            "- say how many letters the word has, or which letters it contains;\n"
            "- repeat or rephrase the clue already shown: the player already has it;\n"
            "- give the word's position (row, column) or direction."
        )

    def _current_selection_line(self, ui_context, with_position=True):
        """A one-line restatement of which grid word is selected RIGHT
        NOW, prepended to the player's own message in `reply_stream()` so
        the model answers about the currently-selected word rather than
        one it discussed earlier in the conversation. Reported live: with
        a bilingual grid and vague follow-ups ("Un indice ?"), a small
        model kept giving hints about the word from turn 1 even after the
        player had selected a different one — the interface-state block
        in the system prompt is far from the current question and lost
        the tug-of-war against recent conversation history. Returns "" for
        no puzzle (the system prompt already covers that). `with_position`
        False names the word without its row/column/direction — the CLUE
        route, whose reply must not state the position (the model copies
        whatever position text it is shown into the hint)."""
        sel = _resolve_selection(ui_context or {})
        if not sel["puzzle_loaded"]:
            return ""
        w = sel["hovered_resolved"] or sel["filling_word"]
        if w is None:
            return (
                "NOTE — right now NO single word is selected in the grid (nothing hovered, "
                "and no unambiguous word at a clicked cell). If this message asks for help "
                "without naming a word, ask the player to hover a word or click a cell "
                "first; do NOT continue a word from an earlier reply."
            )
        kind = "under the mouse (hovered)" if sel["hovered_resolved"] else "at the clicked cell being filled"
        if not with_position:
            return (
                f"NOTE — right now the player has a word selected in the grid ({kind}): the "
                f"one of the system prompt. If this message is a request for help / a hint and "
                f"does not explicitly name a different word, it is about THAT word, even if an "
                f"earlier reply in this conversation was about a different word. Do not carry "
                f"over the previous reply's word."
            )
        d = "DOWN (vertical)" if w.get("direction") == "down" else "ACROSS (horizontal)"
        r, c = w.get("row", 0) + 1, w.get("col", 0) + 1
        return (
            f"NOTE — right now the player has the {d} word starting at (row {r}, column {c}) "
            f"selected in the grid ({kind}). If this message is a request for help / a hint "
            f"/ the answer and does not explicitly name a different word, it is about THAT "
            f"word — the {d} word at (row {r}, column {c}) — even if an earlier reply in "
            f"this conversation was about a different word. Do not carry over the previous "
            f"reply's word."
        )

    async def reply_stream(self, history, message, language="fr", ui_context=None,
                           timeout=DEFAULT_TIMEOUT, on_prompt=None, on_route=None):
        """Same purpose as a plain `reply()` would have, but yields the
        assistant's reply incrementally, chunk by chunk, as the LLM
        produces it — at the user's explicit request: "Le Bot doit
        afficher la réponse en streaming." Uses the same OpenAI-compatible
        endpoint's own streaming mode (`"stream": true`, Server-Sent-
        Events, the same protocol llama.cpp's own server implements)
        instead of waiting for the full response — an async generator
        (the one method in this whole module that talks to the endpoint
        with `httpx.AsyncClient` rather than the plain sync `httpx.post`
        used elsewhere in this project's LLM-calling code) since it needs
        to yield control back to the FastAPI event loop between chunks,
        not just block once for the whole call — see backend/app.py's own
        `POST /api/chat`, a real `StreamingResponse` this feeds directly.

        `history`/`message`/`ui_context` are exactly as before (see
        _build_system_prompt). `on_prompt`, if given, is called once with
        the fully-assembled `messages` list (system prompt + history +
        current question) right before the HTTP call — used by
        backend/app.py to log the complete prompt for analysis when
        CHATBOT_DEBUG is enabled. Raises ChatError up front on a connection
        failure that happens before any chunk was ever read; a failure
        *mid-stream* (rarer, but possible) simply ends the generator
        early with whatever was already yielded — the caller has no
        clean way to retroactively signal "actually, that was incomplete"
        once real content already reached the player, so this is treated
        as an accepted, disclosed edge case rather than something worth
        adding a second failure-signaling channel for.

        A `<think>...</think>` reasoning block is buffered and discarded
        rather than streamed to the player raw, mirroring backend/
        clues.py's own `_strip_reasoning()` behavior for the non-streaming
        case — a model that never closes its own `<think>` block within
        its token budget therefore yields nothing at all, exactly like
        `_strip_reasoning`'s own `""` case.

        Which of the 3 filtering behaviors applies is read once, at
        construction time, from `self.think_filter` (`CHATBOT_THINK_
        FILTER`, see its own module-level comment for the 3 real, live-
        measured model behaviors this project has actually run into) —
        deliberately a fixed, explicit setting rather than guessed at
        dynamically per reply. An earlier version of this function tried
        exactly that (buffering the first several thousand characters of
        every single reply, watching for either tag to decide which
        behavior applied), reverted at the user's own explicit request
        after it broke real-time streaming for every reply on a model
        that never reasons at all — which behavior a given model/server
        needs is already known in advance from its own configuration, so
        guessing it per reply only ever cost every reply a real, needless
        delay for no benefit.

        `reasoning_state` starts at one of three values depending on
        `self.think_filter`:
        - `"none"` → `"disabled"`: every chunk is yielded immediately,
          with no tag search of any kind — the cheapest, safest choice
          for a model already confirmed to never emit either tag.
        - `"close_only"` → `"in_reasoning"` directly: for a setup where
          the opening tag lives in the *prompt* itself (silently injected
          ahead of the model's own first generated token) and is never
          echoed back in the completion — confirmed live for this
          project's own SGLang/Qwen3 setup, where the whole reasoning
          block would otherwise stream straight to the player as if it
          were the real reply, `</think>` included, no matter how
          forcefully the system prompt itself asks the model not to
          think out loud (see `_build_system_prompt`'s own rule 7,
          confirmed live to not fix this on its own).
        - `"open_close"` (default) → `"pending"`: the original, safe
          design — wait to *see* a real `_THINK_OPEN` before ever
          holding anything back. A model that never emits one (an
          ordinary non-reasoning reply) never has anything filtered at
          all, streaming from the very first token exactly as if no
          filtering existed. Never pair this with a model whose opening
          tag lives in the prompt (see `"close_only"` above) — with no
          visible `<think>` to ever trigger the switch, the whole
          reasoning block would leak through unfiltered.

        Unlike the non-streaming case, either tag can arrive split across
        an arbitrary number of separate chunks (a real model can, in
        principle, emit `<think>` one character at a time) —
        `_longest_tag_prefix_suffix` is what makes sure a still-forming
        tag is never mistaken for ordinary visible text and flushed to
        the player prematurely, holding back only the exact trailing
        slice of the buffer that could still complete `_THINK_OPEN`,
        never more. The inner `while progressed:` loop exists
        specifically so both tags can be found and consumed within the
        very same incoming chunk when a small/fast response happens
        to deliver them together (e.g. `"<think>x</think>Answer"` all at
        once) — without it, the close-tag check would only ever run on
        the *next* chunk's arrival, one iteration too late."""
        ui = ui_context or {}
        # In play mode (a playable grid on screen) a first call sorts the
        # question (classify_question), and the reply is written from the
        # prompt of that kind only; `on_route`, if given, receives the
        # route, the classifier's raw answer and its request (logged in
        # LOG_CHAT/).
        route = None
        if ui.get("puzzle_loaded"):
            route, raw_route, classify_messages = await self.classify_question(history, message)
            if on_route is not None:
                on_route(route, raw_route, classify_messages)
        if route == ROUTE_USAGE:
            system_prompt = self._build_usage_prompt(language, ui)
            selection_line = ""
        elif route in (ROUTE_CLUE, ROUTE_ANSWER):
            synonyms, sentences = [], []
            help_word = _resolve_selection(ui)["help_word"]
            if route == ROUTE_CLUE and help_word is not None:
                word_language = help_word.get("language") or language
                synonyms, sentences = await asyncio.to_thread(
                    lambda: (_qdrant_synonyms(help_word, word_language),
                             _corpus_sentences(help_word, word_language))
                )
            system_prompt = self._build_clue_prompt(
                language, ui, answer_requested=route == ROUTE_ANSWER, message=message,
                synonyms=synonyms, sentences=sentences,
            )
            history = _trim_history(history, CLUE_HISTORY_MESSAGES)
            # The CLUE prompt names the selected word already; the NOTE
            # still sits right before the question, against an earlier
            # hint's word kept in the trimmed history — without the
            # position for a hint, which must not state it.
            selection_line = self._current_selection_line(ui, with_position=route != ROUTE_CLUE)
        else:
            system_prompt = self._build_system_prompt(language, ui)
            # Prepend the live "which word is selected right now" NOTE to
            # the player's own message, so it sits immediately before the
            # question (highest recency) rather than only in the far-away
            # system prompt — a small model was anchoring on an earlier
            # reply's word instead (see _current_selection_line's own
            # docstring).
            selection_line = self._current_selection_line(ui)
        messages = [{"role": "system", "content": system_prompt}]
        messages.extend(history)
        user_content = f"{selection_line}\n\n{message}" if selection_line else message
        messages.append({"role": "user", "content": user_content})
        # Hand the exact wire payload to the caller before sending it —
        # backend/app.py uses this to write the full prompt (system +
        # history + question) into LOG_CHAT/ when CHATBOT_DEBUG is on.
        # Called here, before the try/HTTP block, so a connection failure
        # still leaves the prompt captured for analysis.
        if on_prompt is not None:
            on_prompt(messages)
        help_word = _resolve_selection(ui)["help_word"]
        if route == ROUTE_CLUE and help_word is not None:
            # A hint is checked before it is sent (see _checked_hint), so it
            # reaches the player in one piece rather than streamed.
            yield await self._checked_hint(
                messages, timeout, help_word, message, help_word.get("language") or language
            )
            return
        async for chunk in self._stream_completion(messages, timeout):
            yield chunk

    async def _checked_hint(self, messages, timeout, word, message, language):
        """A hint that gives no part of the answer: written up to
        CLUE_HINT_ATTEMPTS times until `_hint_leak_spans` finds nothing in
        it and it is not a mere echo of the player's message, the last
        attempt's leaked words replaced by "…" otherwise."""
        text = ""
        for attempt in range(CLUE_HINT_ATTEMPTS):
            text = "".join([c async for c in self._stream_completion(messages, timeout)])
            spans = _hint_leak_spans(text, word, message, language)
            echo = _grid_form(text.strip()) == _grid_form(message.strip())
            if not spans and not echo:
                return text
            if echo:
                logger.info("chat: hint attempt %d/%d only echoes the message: %r",
                            attempt + 1, CLUE_HINT_ATTEMPTS, text)
                continue
            logger.info("chat: hint attempt %d/%d leaks part of the answer: %r",
                        attempt + 1, CLUE_HINT_ATTEMPTS, text)
        for start, end in reversed(spans):
            text = text[:start] + "…" + text[end:]
        return text

    async def _stream_completion(self, messages, timeout):
        """The streamed chat-completions call behind every reply: yields
        the visible text chunk by chunk, `<think>` blocks and markup tags
        removed (see reply_stream's docstring for the filtering)."""
        buffer = ""
        yielded_anything = False
        # Every visible piece goes through the tag filter before it is
        # yielded (see _TagStripper).
        tags = _TagStripper()
        reasoning_state = {
            "none": "disabled",
            "close_only": "in_reasoning",
            "open_close": "pending",
        }[self.think_filter]
        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                async with client.stream(
                    "POST", self.base_url,
                    headers={"Authorization": f"Bearer {self.api_key}"},
                    json={
                        "model": self.model,
                        "messages": messages,
                        "temperature": TEMPERATURE,
                        "max_tokens": MAX_TOKENS,
                        "stream": True,
                        # A per-request reinforcement of the same intent as
                        # LLAMA_CHAT_TEMPLATE_KWARGS/SGLANG_CHAT_TEMPLATE_
                        # KWARGS's own enable_thinking:false (run_llm.sh/
                        # run_sglang.sh) — harmless for a server that
                        # doesn't recognize this field (confirmed live for
                        # llama_cpp.server, no strict extra-field
                        # validation), but a real, request-level "none" for
                        # a server that does (confirmed live for SGLang
                        # earlier in this project's own investigation —
                        # only "none", never "low", actually disables
                        # thinking for a Qwen3 chat template). This is
                        # separate from CHATBOT_THINK_FILTER (env.sh) —
                        # this field asks the model not to reason at all;
                        # that setting controls how a reasoning block is
                        # filtered out of the reply if the model reasons
                        # anyway despite this.
                        "reasoning_effort": "none",
                    },
                ) as response:
                    response.raise_for_status()
                    async for line in response.aiter_lines():
                        if not line or not line.startswith("data: "):
                            continue
                        payload = line[len("data: "):].strip()
                        if payload == "[DONE]":
                            break
                        try:
                            event = json.loads(payload)
                        except json.JSONDecodeError:
                            continue
                        delta = event.get("choices", [{}])[0].get("delta", {}).get("content", "")
                        if not delta:
                            continue
                        logger.info("chat: raw LLM chunk: %r", delta)
                        if reasoning_state == "disabled":
                            # CHATBOT_THINK_FILTER=none — no <think>
                            # search at all, stream every chunk immediately.
                            visible = tags.feed(delta)
                            if visible:
                                yielded_anything = True
                                yield visible
                            continue
                        buffer += delta
                        progressed = True
                        while progressed:
                            progressed = False
                            if reasoning_state == "in_reasoning":
                                if _THINK_CLOSE in buffer:
                                    buffer = buffer.split(_THINK_CLOSE, 1)[1]
                                    reasoning_state = "clear"
                                    progressed = True
                                # else: still inside <think>...</think> —
                                # keep buffering, yield nothing this round.
                            elif buffer:
                                # "pending" (CHATBOT_THINK_FILTER=open_close,
                                # never yet seen an opening tag) and "clear"
                                # (already past a reasoning block, or this
                                # filter never holds anything back to begin
                                # with) share the exact same logic: watch for
                                # a fresh _THINK_OPEN, otherwise flush
                                # everything except a still-forming tag's own
                                # trailing prefix.
                                if _THINK_OPEN in buffer:
                                    buffer = buffer.split(_THINK_OPEN, 1)[1]
                                    reasoning_state = "in_reasoning"
                                    progressed = True
                                else:
                                    hold = _longest_tag_prefix_suffix(buffer, _THINK_OPEN)
                                    if hold < len(buffer):
                                        to_flush = buffer[:len(buffer) - hold] if hold else buffer
                                        buffer = buffer[len(buffer) - hold:] if hold else ""
                                        visible = tags.feed(to_flush) if to_flush else ""
                                        if visible:
                                            yielded_anything = True
                                            yield visible
        except httpx.HTTPError as e:
            logger.warning("chat stream failed (%s, model=%r): %s", self.base_url, self.model, e)
            raise ChatError(f"Le serveur de langage est indisponible ({e}).") from e
        tail = tags.flush()
        if tail:
            yielded_anything = True
            yield tail
        if not yielded_anything:
            # A real, reproduced incident: llama_cpp.server can send a 200
            # OK, start an SSE stream, then raise "Requested tokens (N)
            # exceed context window of ..." *inside* its own streaming
            # generator (a real prompt-too-long case — a grid's own word
            # list pushed the system prompt past --n_ctx, see run_llm.sh) —
            # the client sees a silently empty stream, not an HTTP error,
            # since the failure happens after the response headers (and
            # their 200 status) are already sent. Without this check the
            # player would just see a blank reply with no explanation
            # (confirmed live: LOG_CHAT recorded an empty answer in well
            # under a second, no error at all). Anything else that could
            # leave a stream with zero real content (an unresponsive
            # model, an unexpected response shape) is covered by the same
            # check, not just this one specific cause.
            raise ChatError(
                "Le serveur de langage n'a renvoyé aucun contenu "
                "(le contexte de la conversation est peut-être trop long)."
            )


class ChatError(RuntimeError):
    """Raised by ChatBot.reply() when the LLM call itself fails (a
    connection error, a non-2xx response) — mirrors backend/clues.py's
    own ClueGenerationError, kept as a separate class since a chat
    failure and a clue-generation failure are handled at different HTTP
    endpoints with different error codes (see backend/app.py)."""
