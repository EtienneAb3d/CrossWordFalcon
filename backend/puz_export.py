"""Across Lite (.puz) export of a library grid (`GET /api/library/{grid_id}/
puz`): the binary format most crossword apps open (version 1.3, text in
Windows-1252, a character it lacks written "?"). A clue of a word holding
word breaks (a multi-word expression) ends with its enumeration, "(3-2-4)"
(`word_enumeration`), the format having no way to draw them on the grid.
The helpers shared with the ipuz/jpz exports (`numbered_slots`,
`word_enumeration`, `break_set`) live here too."""

from .crossword_gen import challenge_word_grid_form

PUZ_VERSION = b"1.3\0"
PUZ_ENCODING = "cp1252"
PUZ_COPYRIGHT = "CrossWordFalcon"
PUZ_MEDIA_TYPE = "application/x-crossword"


def _cksum(data, cksum=0):
    """The format's 16-bit rotating checksum of `data`, continuing `cksum`."""
    for byte in data:
        cksum = (cksum >> 1) | 0x8000 if cksum & 1 else cksum >> 1
        cksum = (cksum + byte) & 0xFFFF
    return cksum


def _encode(text):
    return (text or "").encode(PUZ_ENCODING, errors="replace")


# Characters separating two words of an expression's spelling (the same as
# crossword_gen's _WORD_SEPARATORS).
WORD_SEPARATORS = frozenset(" -'\u2019")


def break_set(record):
    """The record's word breaks (`breaks`, `[row, col, direction]`: the line
    between that cell and the next one in `direction`) as a set of
    (row, col, direction) tuples."""
    out = set()
    for b in record.get("breaks") or ():
        if isinstance(b, (list, tuple)) and len(b) == 3:
            out.add((int(b[0]), int(b[1]), str(b[2])))
    return out


def _spelling_separators(spelling):
    """{letter count: "-" or ","} of every place `spelling` separates two
    words, in grid letters: "-" where a hyphen separates them, "," for a
    space or an apostrophe ("arc-en-ciel" -> {3: "-", 5: "-"})."""
    seps = {}
    n = 0
    for ch in spelling:
        if ch in WORD_SEPARATORS:
            if n:
                if ch == "-":
                    seps[n] = "-"
                else:
                    seps.setdefault(n, ",")
            continue
        n += len(challenge_word_grid_form(ch))
    return seps


def word_enumeration(word, breaks):
    """The enumeration of a record word ("3-2-4" for ARCENCIEL, spelled
    "arc-en-ciel"): the letter count of each of its pieces between two of
    the grid's word breaks (`breaks`, `break_set`), joined by "-" where its
    natural spelling (`accented`) has a hyphen there, "," otherwise; `None`
    for a word with no break."""
    answer = str(word.get("answer") or "")
    direction = word.get("direction")
    dr, dc = (0, 1) if direction == "across" else (1, 0)
    r0, c0 = int(word.get("row", 0)), int(word.get("col", 0))
    cuts = [i + 1 for i in range(len(answer) - 1)
            if (r0 + dr * i, c0 + dc * i, direction) in breaks]
    if not cuts:
        return None
    seps = _spelling_separators(str(word.get("accented") or ""))
    out, prev = "", 0
    for cut in cuts:
        out += str(cut - prev) + seps.get(cut, ",")
        prev = cut
    return out + str(len(answer) - prev)


def numbered_slots(solution):
    """Every word of the grid in the formats' order — cells row by row, for
    each cell the across word starting there, then the down word — as
    (number, row, col, direction, length) tuples; a word is a run of at
    least 2 white cells, numbered like a printed grid."""
    rows, cols = len(solution), len(solution[0]) if solution else 0

    def white(r, c):
        return 0 <= r < rows and 0 <= c < cols and solution[r][c] != "#"

    slots = []
    number = 0
    for r in range(rows):
        for c in range(cols):
            if not white(r, c):
                continue
            across = not white(r, c - 1) and white(r, c + 1)
            down = not white(r - 1, c) and white(r + 1, c)
            if not (across or down):
                continue
            number += 1
            if across:
                length = 0
                while white(r, c + length):
                    length += 1
                slots.append((number, r, c, "across", length))
            if down:
                length = 0
                while white(r + length, c):
                    length += 1
                slots.append((number, r, c, "down", length))
    return slots


def _clues_in_order(solution, words, breaks=frozenset()):
    """The clues in the format's order: cells row by row, for each cell the
    across word starting there, then the down word. A word is a run of at
    least 2 white cells; one the record has no clue for gets "". A word
    with an enumeration (`word_enumeration`) gets it appended, "(3-2-4)"."""
    rows, cols = len(solution), len(solution[0]) if solution else 0
    clue_at = {}
    for w in words:
        clue = w.get("clue") or ""
        enumeration = word_enumeration(w, breaks) if breaks else None
        if enumeration:
            clue = f"{clue} ({enumeration})" if clue else f"({enumeration})"
        clue_at[(w["row"], w["col"], w["direction"])] = clue

    def white(r, c):
        return 0 <= r < rows and 0 <= c < cols and solution[r][c] != "#"

    clues = []
    for r in range(rows):
        for c in range(cols):
            if not white(r, c):
                continue
            if not white(r, c - 1) and white(r, c + 1):
                clues.append(clue_at.get((r, c, "across"), ""))
            if not white(r - 1, c) and white(r + 1, c):
                clues.append(clue_at.get((r, c, "down"), ""))
    return clues


def render_puz(record, title):
    """The .puz file of a library record (`width`/`height`/`solution`/
    `words`), titled `title`, its author the record's pseudo (else
    "CrossWordFalcon")."""
    solution = record["solution"]
    width, height = int(record["width"]), int(record["height"])
    answer = "".join("." if cell == "#" else str(cell)[:1].upper() for row in solution for cell in row)
    fill = "".join("." if cell == "#" else "-" for row in solution for cell in row)
    answer_bytes, fill_bytes = _encode(answer), _encode(fill)
    clues = _clues_in_order(solution, record.get("words") or [], break_set(record))
    title_bytes = _encode(title or "CrossWordFalcon")
    author_bytes = _encode(record.get("pseudo") or "CrossWordFalcon")
    copyright_bytes = _encode(PUZ_COPYRIGHT)
    clue_bytes = [_encode(clue) for clue in clues]
    notes_bytes = b""

    cib = bytes([width, height]) + len(clues).to_bytes(2, "little") + (1).to_bytes(2, "little") + b"\0\0"
    cib_cksum = _cksum(cib)

    def text_cksum(cksum):
        for part in (title_bytes, author_bytes, copyright_bytes):
            if part:
                cksum = _cksum(part + b"\0", cksum)
        for clue in clue_bytes:
            if clue:
                cksum = _cksum(clue, cksum)
        if notes_bytes:
            cksum = _cksum(notes_bytes + b"\0", cksum)
        return cksum

    overall = text_cksum(_cksum(fill_bytes, _cksum(answer_bytes, cib_cksum)))
    parts = (cib_cksum, _cksum(answer_bytes), _cksum(fill_bytes), text_cksum(0))
    magic = b"ICHEATED"
    masked = bytes([magic[i] ^ (parts[i] & 0xFF) for i in range(4)]
                   + [magic[4 + i] ^ (parts[i] >> 8) for i in range(4)])

    header = (
        overall.to_bytes(2, "little") + b"ACROSS&DOWN\0" + cib_cksum.to_bytes(2, "little")
        + masked + PUZ_VERSION + b"\0\0" + b"\0\0" + b"\0" * 12 + cib
    )
    body = answer_bytes + fill_bytes
    for part in (title_bytes, author_bytes, copyright_bytes, *clue_bytes, notes_bytes):
        body += part + b"\0"
    return header + body
