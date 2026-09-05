#!/usr/bin/env python3
""""David FALCON", the web UI's in-app chat assistant, at the user's
explicit request: "En bas à droite de l'interface, ajoute un ChatBot
(ouvert par défaut) avec l'icône de l'application. Le ChatBot utilise le
LLM pour répondre à l'utilisateur. Il s'appelle David FALCON." Talks to
the exact same OpenAI-compatible chat-completions endpoint as
backend/clues.py's LLMClueGenerator (same LLM_BASE_URL/LLM_MODEL/
LLM_API_KEY environment configuration — there is only ever one local/
remote LLM server configured for this whole app) — deliberately its own,
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
import json
import logging
import os
from pathlib import Path

import httpx

from .clues import DEFAULT_LLM_API_KEY, DEFAULT_LLM_BASE_URL, DEFAULT_LLM_MODEL, LANGUAGE_NAMES

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


def _format_words_block(words):
    """One line per grid word, at the user's explicit request: "il est
    informé de la liste des définitions qui doit contenir toutes les
    infos utiles pour comprendre la grille : numéro de ligne et de
    colonne de chaque mot, vertical ou horizontal, définition, valeur du
    mot (réponse)." `words` is the frontend's own `puzzle.words` shape
    (row/col are 0-based internally — shown 1-based here, matching what
    the player actually sees on screen, since header row/column numbers
    in the grid are 1-based too)."""
    lines = []
    for w in words:
        direction = "Down" if w.get("direction") == "down" else "Across"
        lines.append(
            f"- ({w.get('row', 0) + 1}, {w.get('col', 0) + 1}) {direction}: "
            f"clue={w.get('clue') or '(none yet)'!r}, answer={w.get('answer', '')!r}"
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


class ChatBot:
    """One instance is enough for the process's lifetime — construct once
    (e.g. at module level in backend/app.py) and call `reply()` per
    message, the same usage convention as LLMClueGenerator."""

    def __init__(self):
        self.base_url = os.environ.get("LLM_BASE_URL", DEFAULT_LLM_BASE_URL)
        self.model = os.environ.get("LLM_MODEL", DEFAULT_LLM_MODEL)
        self.api_key = os.environ.get("LLM_API_KEY", DEFAULT_LLM_API_KEY)

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
        with it."""
        language_name = LANGUAGE_NAMES.get(language, language)
        doc_user = _load_doc_user()

        puzzle_loaded = bool(ui_context.get("puzzle_loaded"))
        hovered_word = ui_context.get("hovered_word")
        filling_cell = ui_context.get("filling_cell")
        words = ui_context.get("words") or []

        state_lines = [f"A crossword puzzle is currently loaded: {puzzle_loaded}."]
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
                # 0-based internally, shown 1-based to match what the
                # player actually sees (grid row/column headers). The
                # word(s) at that exact cell are computed directly here
                # (_words_touching_cell), not left for the model to work
                # out from raw coordinates — see that function's own
                # docstring for why.
                filling_words = _words_touching_cell(filling_cell, words)
                state_lines.append(
                    f"Separately (this is NOT the hovered word further below), the player "
                    f"has clicked cell (row {filling_cell.get('row', 0) + 1}, column "
                    f"{filling_cell.get('col', 0) + 1}) to type an answer into."
                )
                if filling_words:
                    state_lines.append(
                        "The word(s) occupying that exact clicked cell right now "
                        "(the word being filled in — only relevant if the player asks "
                        "about that specifically, not about the hovered word further "
                        "below):\n" + _format_words_block(filling_words)
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
                resolved = _find_word_by_start(hovered_word, words)
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
                state_lines.append(
                    "No word is currently under the player's mouse (hover) right now. If "
                    "the player asks what word is selected/hovered ('mot sélectionné'), "
                    "you MUST reply that no word is currently hovered and ask them to "
                    "move their mouse over a word in the grid or a clue first — do NOT "
                    "name any word from the list above as if it were hovered, not even "
                    "the first one listed."
                )

        return (
            f"You are David FALCON, the friendly in-app assistant of CrossWordFalcon, a "
            "crossword-puzzle web app. You help the player use the interface and solve the "
            "crossword grid currently on screen (explaining a clue, giving a hint, or, if "
            "explicitly asked, the answer itself).\n\n"
            "STRICT RULES:\n"
            "1. Always reply extremely politely.\n"
            f"2. Always reply entirely in {language_name}, regardless of what language the "
            "player wrote in.\n"
            "3. You must ONLY answer questions about using this interface, or about solving/"
            "understanding the crossword grid currently on screen. For ANY other question "
            "(general knowledge, other software, personal questions, anything unrelated to "
            "this app or its current grid — e.g. 'what is the capital of...'), politely "
            "decline and suggest the player consult an appropriate website or resource for "
            "that topic instead. Do this and NOTHING else: never actually answer the "
            "out-of-scope question afterward, not even briefly, not even after declining — "
            "declining and then still giving the answer right after is exactly what this "
            "rule forbids.\n"
            "4. Distinguish clearly between a HINT request and an EXPLICIT ANSWER request "
            "when the player asks for help with a word:\n"
            "   - A HINT ('un indice', 'aide-moi', 'je suis bloqué', asking about a clue, or "
            "anything not explicitly asking for the answer itself) must NEVER contain the "
            "exact answer text — not the word itself, not spelled out letter by letter, not "
            "even inside a sentence. Even though each word's own answer is given to you above "
            "(the 'answer=' field in the word list/state), that text is for your own internal "
            "use only when replying to a hint — copying or stating it is exactly what this "
            "rule forbids. Instead, give an INDIRECT reply: rephrase the clue in different "
            "words, describe the meaning without naming it, mention how many letters it has, "
            "or confirm/deny one specific letter the player proposes.\n"
            "   - Reveal the exact answer text ONLY when the player unambiguously and "
            "explicitly asks for it (e.g. 'donne-moi la réponse', 'quel est le mot exact', "
            "'quelle est la solution', 'dis-moi le mot'). If genuinely unsure which the "
            "player wants, treat it as a hint request, never as an answer request.\n"
            "   - Example 1: asked for a hint about MAISON (clue 'Habitation'), a BAD reply "
            "says 'Le mot est MAISON' or spells out 'M-A-I-S-O-N' — a GOOD reply stays "
            "indirect, e.g. 'C'est un lieu où l'on vit, avec plusieurs pièces — le mot compte "
            "6 lettres.'\n"
            "   - Example 2: asked for a hint about SOLEIL (clue 'Astre du jour'), a BAD "
            "reply says 'Le mot est SOLEIL' or 'C'est SOLEIL' — a GOOD reply stays indirect, "
            "e.g. 'C'est l'étoile autour de laquelle tourne la Terre, celle qui nous éclaire "
            "le jour — le mot compte 6 lettres.' These two examples both illustrate the SAME "
            "general rule — apply it the same way to ANY word the player asks a hint about, "
            "not just these two.\n"
            "5. Keep replies reasonably short and conversational — this is a chat, not an "
            "essay.\n"
            "6. NEVER start your reply with a greeting (no \"Hello\", \"Hi\", \"Bonjour\", "
            "\"Hi there\", introducing yourself again by name, or any equivalent) — do this "
            "in NONE of your replies, not just most of them. A greeting has already been "
            "shown exactly once, separately, as this chat's own welcome message, before the "
            "player ever asked anything — that is the ONLY greeting this conversation will "
            "ever have. Every single reply after it, including your very first one, must "
            "start directly with the actual answer, with zero greeting or self-introduction "
            "of any kind.\n\n"
            "Reference documentation for how the interface itself works "
            "(frontend/static/index.html and script.js, described here for a player, not a "
            "developer):\n"
            f"{doc_user}\n\n"
            "Current state of the interface:\n" + "\n".join(state_lines)
        )

    async def reply_stream(self, history, message, language="fr", ui_context=None, timeout=DEFAULT_TIMEOUT):
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
        _build_system_prompt). Raises ChatError up front on a connection
        failure that happens before any chunk was ever read; a failure
        *mid-stream* (rarer, but possible) simply ends the generator
        early with whatever was already yielded — the caller has no
        clean way to retroactively signal "actually, that was incomplete"
        once real content already reached the player, so this is treated
        as an accepted, disclosed edge case rather than something worth
        adding a second failure-signaling channel for.

        A `<think>...</think>` reasoning block (only ever emitted by a
        reasoning-capable model — this project's own default, Qwen3.5,
        has thinking disabled) is buffered and discarded rather than
        streamed to the player raw, mirroring backend/clues.py's own
        `_strip_reasoning()` behavior for the non-streaming case — a
        model that never closes its own `<think>` block within its token
        budget therefore yields nothing at all, exactly like `_strip_
        reasoning`'s own `""` case. Unlike the non-streaming case, either
        tag can arrive split across an arbitrary number of separate
        chunks (a real model can, in principle, emit `<think>` one
        character at a time) — `_longest_tag_prefix_suffix` is what makes
        sure a still-forming tag is never mistaken for ordinary visible
        text and flushed to the player prematurely, holding back only the
        exact trailing slice of the buffer that could still complete
        `_THINK_OPEN`, never more. The inner `while progressed:` loop
        exists specifically so both tags can be found and consumed within
        the very same incoming chunk when a small/fast response happens
        to deliver them together (e.g. `"<think>x</think>Answer"` all at
        once) — without it, the close-tag check would only ever run on
        the *next* chunk's arrival, one iteration too late."""
        system_prompt = self._build_system_prompt(language, ui_context or {})
        messages = [{"role": "system", "content": system_prompt}]
        messages.extend(history)
        messages.append({"role": "user", "content": message})
        buffer = ""
        in_reasoning = False
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
                        buffer += delta
                        progressed = True
                        while progressed:
                            progressed = False
                            if in_reasoning:
                                if _THINK_CLOSE in buffer:
                                    buffer = buffer.split(_THINK_CLOSE, 1)[1]
                                    in_reasoning = False
                                    progressed = True
                                # else: still inside <think>...</think> —
                                # keep buffering, yield nothing this round.
                            elif _THINK_OPEN in buffer:
                                buffer = buffer.split(_THINK_OPEN, 1)[1]
                                in_reasoning = True
                                progressed = True
                            elif buffer:
                                hold = _longest_tag_prefix_suffix(buffer, _THINK_OPEN)
                                if hold < len(buffer):
                                    to_flush = buffer[:len(buffer) - hold] if hold else buffer
                                    buffer = buffer[len(buffer) - hold:] if hold else ""
                                    if to_flush:
                                        yield to_flush
        except httpx.HTTPError as e:
            logger.warning("chat stream failed (%s, model=%r): %s", self.base_url, self.model, e)
            raise ChatError(f"Le serveur de langage est indisponible ({e}).") from e


class ChatError(RuntimeError):
    """Raised by ChatBot.reply() when the LLM call itself fails (a
    connection error, a non-2xx response) — mirrors backend/clues.py's
    own ClueGenerationError, kept as a separate class since a chat
    failure and a clue-generation failure are handled at different HTTP
    endpoints with different error codes (see backend/app.py)."""
