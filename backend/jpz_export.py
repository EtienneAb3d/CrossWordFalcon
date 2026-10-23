"""JPZ export of a library grid (`GET /api/library/{grid_id}/jpz`): the
Crossword Compiler XML format (`crossword-compiler-applet` /
`rectangular-puzzle`, uncompressed), which draws word breaks as bars
between cells (`right-bar`/`bottom-bar`) and carries each expression's
enumeration ("3-2-4") in its clue's `format` attribute."""

from .puz_export import break_set, numbered_slots, word_enumeration

JPZ_MEDIA_TYPE = "application/xml"
JPZ_COPYRIGHT = "CrossWordFalcon"

# Clue-list headings, by grid language (English for any other).
JPZ_DIRECTION_TITLES = {
    "fr": ("Horizontalement", "Verticalement"),
    "en": ("Across", "Down"),
    "de": ("Waagerecht", "Senkrecht"),
    "es": ("Horizontales", "Verticales"),
    "it": ("Orizzontali", "Verticali"),
    "pt": ("Horizontais", "Verticais"),
}


def _xml(text):
    """`text` escaped for XML text and double-quoted attributes."""
    return (str(text).replace("&", "&amp;").replace("<", "&lt;")
            .replace(">", "&gt;").replace('"', "&quot;"))


def render_jpz(record, title):
    """The .jpz file (UTF-8 XML) of a library record (`width`/`height`/
    `solution`/`words`/`breaks`/`language`), titled `title`, its author the
    record's pseudo (else "CrossWordFalcon")."""
    solution = record["solution"]
    width, height = int(record["width"]), int(record["height"])
    breaks = break_set(record)
    slots = numbered_slots(solution)
    number_at = {(r, c): n for n, r, c, _, _ in slots}
    word_at = {(w["row"], w["col"], w["direction"]): w for w in record.get("words") or []}
    across_title, down_title = JPZ_DIRECTION_TITLES.get(
        record.get("language"), JPZ_DIRECTION_TITLES["en"])
    lines = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        '<crossword-compiler-applet xmlns="http://crossword.info/xml/crossword-compiler-applet">',
        '<rectangular-puzzle xmlns="http://crossword.info/xml/rectangular-puzzle" '
        'alphabet="ABCDEFGHIJKLMNOPQRSTUVWXYZ">',
        "<metadata>",
        f"<title>{_xml(title or 'CrossWordFalcon')}</title>",
        f"<creator>{_xml(record.get('pseudo') or 'CrossWordFalcon')}</creator>",
        f"<copyright>{_xml(JPZ_COPYRIGHT)}</copyright>",
        "<description></description>",
        "</metadata>",
        "<crossword>",
        f'<grid width="{width}" height="{height}">',
        '<grid-look numbering-scheme="normal"/>',
    ]
    for r, row in enumerate(solution):
        for c, cell in enumerate(row):
            if cell == "#":
                lines.append(f'<cell x="{c + 1}" y="{r + 1}" type="block"/>')
                continue
            attrs = f'x="{c + 1}" y="{r + 1}" solution="{_xml(str(cell)[:1].upper())}"'
            if (r, c) in number_at:
                attrs += f' number="{number_at[(r, c)]}"'
            if (r, c, "across") in breaks:
                attrs += ' right-bar="true"'
            if (r, c, "down") in breaks:
                attrs += ' bottom-bar="true"'
            lines.append(f"<cell {attrs}/>")
    lines.append("</grid>")
    for k, (_, r, c, direction, length) in enumerate(slots, start=1):
        if direction == "across":
            lines.append(f'<word id="{k}" x="{c + 1}-{c + length}" y="{r + 1}"/>')
        else:
            lines.append(f'<word id="{k}" x="{c + 1}" y="{r + 1}-{r + length}"/>')
    for wanted, heading in (("across", across_title), ("down", down_title)):
        lines.append('<clues ordering="normal">')
        lines.append(f"<title><b>{_xml(heading)}</b></title>")
        for k, (n, r, c, direction, _) in enumerate(slots, start=1):
            if direction != wanted:
                continue
            word = word_at.get((r, c, direction), {})
            enumeration = word_enumeration(word, breaks) if word and breaks else None
            fmt = f' format="{_xml(enumeration)}"' if enumeration else ""
            lines.append(f'<clue word="{k}" number="{n}"{fmt}>{_xml(word.get("clue") or "")}</clue>')
        lines.append("</clues>")
    lines += ["</crossword>", "</rectangular-puzzle>", "</crossword-compiler-applet>", ""]
    return "\n".join(lines).encode("utf-8")
