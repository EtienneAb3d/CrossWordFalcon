"""Across Lite (.puz) export of a library grid (`GET /api/library/{grid_id}/
puz`): the binary format most crossword apps open (version 1.3, text in
Windows-1252, a character it lacks written "?")."""

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


def _clues_in_order(solution, words):
    """The clues in the format's order: cells row by row, for each cell the
    across word starting there, then the down word. A word is a run of at
    least 2 white cells; one the record has no clue for gets ""."""
    rows, cols = len(solution), len(solution[0]) if solution else 0
    clue_at = {(w["row"], w["col"], w["direction"]): w.get("clue") or "" for w in words}

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
    clues = _clues_in_order(solution, record.get("words") or [])
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
