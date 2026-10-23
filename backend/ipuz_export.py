"""ipuz export of a library grid (`GET /api/library/{grid_id}/ipuz`): the
open JSON crossword format (ipuz v2, crossword kind), which draws word
breaks as bars between cells (`"style": {"barred": ...}`) and carries each
expression's enumeration ("3-2-4") with its clue."""

import json

from .puz_export import break_set, numbered_slots, word_enumeration

IPUZ_MEDIA_TYPE = "application/json"
IPUZ_COPYRIGHT = "CrossWordFalcon"


def _cell_bars(r, c, breaks):
    """The ipuz `barred` sides of cell (r, c): "R" for a break after it
    across, "B" for one after it down; "" for none."""
    return ("R" if (r, c, "across") in breaks else "") + ("B" if (r, c, "down") in breaks else "")


def ipuz_document(record, title):
    """The ipuz document (a dict, keys in output order) of a library record
    (`width`/`height`/`solution`/`words`/`breaks`), titled `title`, its
    author the record's pseudo (else "CrossWordFalcon")."""
    solution = record["solution"]
    width, height = int(record["width"]), int(record["height"])
    breaks = break_set(record)
    slots = numbered_slots(solution)
    number_at = {(r, c): n for n, r, c, _, _ in slots}
    word_at = {(w["row"], w["col"], w["direction"]): w for w in record.get("words") or []}
    puzzle, answers = [], []
    for r, row in enumerate(solution):
        prow, srow = [], []
        for c, cell in enumerate(row):
            if cell == "#":
                prow.append("#")
                srow.append("#")
                continue
            value = number_at.get((r, c), 0)
            bars = _cell_bars(r, c, breaks)
            prow.append({"cell": value, "style": {"barred": bars}} if bars else value)
            srow.append(str(cell)[:1].upper())
        puzzle.append(prow)
        answers.append(srow)
    clues = {"Across": [], "Down": []}
    for n, r, c, direction, _ in slots:
        word = word_at.get((r, c, direction), {})
        entry = {"number": n, "clue": word.get("clue") or ""}
        enumeration = word_enumeration(word, breaks) if word and breaks else None
        if enumeration:
            entry["enumeration"] = enumeration
        clues["Across" if direction == "across" else "Down"].append(entry)
    return {
        "version": "http://ipuz.org/v2",
        "kind": ["http://ipuz.org/crossword#1"],
        "title": title or "CrossWordFalcon",
        "author": record.get("pseudo") or "CrossWordFalcon",
        "copyright": IPUZ_COPYRIGHT,
        "publisher": IPUZ_COPYRIGHT,
        "dimensions": {"width": width, "height": height},
        "block": "#",
        "empty": 0,
        "puzzle": puzzle,
        "solution": answers,
        "clues": clues,
    }


def render_ipuz(record, title):
    """The .ipuz file (UTF-8 JSON, compact) of a library record."""
    return json.dumps(ipuz_document(record, title), ensure_ascii=False,
                      separators=(",", ":")).encode("utf-8")
