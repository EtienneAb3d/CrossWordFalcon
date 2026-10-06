#!/usr/bin/env python3
"""
Renders a generated grid (backend/crossword_gen.py's `generate_grid()`
result, or a grid_store record) as A4 landscape SVG pages: the printable,
answer-free puzzle sheet behind the library's PDF download
(render_puzzle_pages), and the same layout plus a solution page as the
durable on-disk record of each published grid (render_export_pages).

Called by backend/app.py once a grid is saved to the library; the record
is written under GRID_SVG/ (project root, gitignored — generated
artifacts, not source content), one file per page, named
`<timestamp>_<language>_<SUFFIX>.svg` (GRID, CLUES, SOLUTION) so files
sort chronologically by filename. A PNG rendering of each page is also
saved under GRID_PNG/ (project root, gitignored too — see save_grid_png)
via `rsvg-convert`. Neither directory is GRID_SAMPLES/: that one is a
separate, hand-curated selection of examples, never written to by this
module — see save_grid_png's own docstring.
"""
import base64
import subprocess
import tempfile
from datetime import datetime
from pathlib import Path
from xml.sax.saxutils import escape

from .crossword_gen import BLACK

PROJECT_ROOT = Path(__file__).resolve().parent.parent
GRID_SVG_DIR = PROJECT_ROOT / "GRID_SVG"
GRID_PNG_DIR = PROJECT_ROOT / "GRID_PNG"
_LOGO_PATH = PROJECT_ROOT / "frontend" / "static" / "logo.png"
_VERSION_PATH = PROJECT_ROOT / "VERSION.txt"

# Colour of a black cell's centered square (the web UI's --black-cell).
BLACK_CELL_FILL = "#2563eb"
# Printable puzzle sheet (render_puzzle_pages, the library's PDF): A4
# landscape pages (297x210 mm, in 96-DPI user units). A grid whose sides
# are both at most PDF_ONE_PAGE_MAX_SIDE cells takes one page, the grid on
# the right and every clue flowed in columns on its left, then under it,
# the cell size and clue font size being the largest that make everything
# fit (render_puzzle_svg). A larger one takes two: the header and the grid
# alone, then the clues in one full-width column (render_two_page_puzzle).
PDF_PAGE_WIDTH = 1122.52
PDF_PAGE_HEIGHT = 793.7
PDF_MARGIN = 34
PDF_LOGO_SIZE = 44
PDF_HEADER_GAP = 14
PDF_FOOTER_HEIGHT = 24
PDF_GRID_CLUES_GAP = 20
PDF_CLUE_COLUMN_GAP = 16
PDF_MAX_CELL_SIZE = 40
PDF_MAX_GRID_WIDTH_FRACTION = 0.6
PDF_MAX_CLUE_COLUMNS = 3
PDF_MIN_CLUE_COLUMN_WIDTH = 120
PDF_LINE_HEIGHT_FACTOR = 1.3
PDF_ONE_PAGE_MAX_SIDE = 20
# Room taken above the grid by the solution export's heading.
PDF_SOLUTION_HEADING_HEIGHT = 24
# Two-page sheet: clue pages added past the second when even the smallest
# font does not fit on it.
PDF_MAX_CLUE_PAGES = 8
# Clue font sizes tried, largest first: 12 down to 4 by halves.
PDF_FONT_SIZES = [(24 - i) / 2 for i in range(17)]
# Cell sizes tried, as fractions of the largest cell the body allows:
# 1.0 down to 0.4 by 0.025.
PDF_CELL_SIZE_STEPS = 25
PDF_CELL_SIZE_STEP = 0.025
# The chosen sizes maximise the smaller of cell/PDF_TARGET_CELL_SIZE and
# font/PDF_TARGET_FONT_SIZE (each capped at 1), then their sum: a 5 mm
# cell to write in, 7 pt clue text.
PDF_TARGET_CELL_SIZE = 18.9
PDF_TARGET_FONT_SIZE = 9.33
# GRID_PNG/ is a print-quality record (see save_grid_png): its pages are
# rendered at 300 DPI rather than rsvg-convert's default 96.
PNG_DPI = 300

# Mirrors frontend/static/script.js's I18N table (acrossHeading/downHeading,
# noDefinition) — kept in sync by hand since this is Python, not JS; update
# both places together if a heading or the placeholder text ever changes.
_HEADINGS = {
    "fr": ("Horizontalement", "Verticalement", "Solution"),
    "en": ("Across", "Down", "Solution"),
    "de": ("Waagerecht", "Senkrecht", "Lösung"),
    "es": ("Horizontales", "Verticales", "Solución"),
    "it": ("Orizzontali", "Verticali", "Soluzione"),
    "pt": ("Horizontais", "Verticais", "Solução"),
}

_NO_DEFINITION = {
    "fr": "Définition indisponible",
    "en": "No definition available",
    "de": "Keine Definition verfügbar",
    "es": "Definición no disponible",
    "it": "Definizione non disponibile",
    "pt": "Definição indisponível",
}

# The language <select>'s own option labels in frontend/static/index.html are
# always shown in that language's own native name, not translated per UI
# language — mirrored here the same way.
_NATIVE_LANGUAGE_NAMES = {
    "fr": "Français",
    "en": "English",
    "de": "Deutsch",
    "es": "Español",
    "it": "Italiano",
    "pt": "Português",
}

# Mirrors frontend/static/i18n.js's difficultyLabel/difficultyEasy/
# difficultyMedium/difficultyHard per language, for the metadata header line.
_DIFFICULTY_LABELS = {
    "fr": ("Difficulté", {"easy": "Facile", "medium": "Moyenne", "hard": "Difficile"}),
    "en": ("Difficulty", {"easy": "Easy", "medium": "Medium", "hard": "Hard"}),
    "de": ("Schwierigkeit", {"easy": "Leicht", "medium": "Mittel", "hard": "Schwer"}),
    "es": ("Dificultad", {"easy": "Fácil", "medium": "Media", "hard": "Difícil"}),
    "it": ("Difficoltà", {"easy": "Facile", "medium": "Media", "hard": "Difficile"}),
    "pt": ("Dificuldade", {"easy": "Fácil", "medium": "Média", "hard": "Difícil"}),
}

# Public base for the "play online" link printed in the PDF's footer
# (render_puzzle_svg). Must stay aligned with SHARE_BASE_URL in
# frontend/static/script.js (the Library's "Link" column).
PLAY_ONLINE_BASE_URL = "https://falcon.cubaix.com/"

_PLAY_ONLINE_LABELS = {
    "fr": "Jouer en ligne (avec solution) : {url}",
    "en": "Play online (with solution): {url}",
    "de": "Online spielen (mit Lösung): {url}",
    "es": "Jugar en línea (con solución): {url}",
    "it": "Gioca online (con soluzione): {url}",
    "pt": "Jogar online (com solução): {url}",
}


# Rough per-character width estimates (as a fraction of font-size), used only
# to decide where a clue line needs to wrap — not pixel-perfect (that depends
# on the actual font metrics of whichever renderer draws the SVG/PNG), but
# biased slightly high on purpose: wrapping a touch earlier than strictly
# necessary just leaves a little unused margin, while underestimating would
# let a line run past the canvas edge and get clipped — the exact bug this
# is meant to fix. Bold text (the clue-number prefix) is a little wider per
# character than regular text at the same font-size.
_CHAR_WIDTH_FACTOR = 0.58
_BOLD_CHAR_WIDTH_FACTOR = 0.65


def _text_width(text, font_size, bold=False):
    """Estimated rendered width in px for plain sans-serif `text` at
    `font_size` — see _CHAR_WIDTH_FACTOR above for why this is an estimate,
    not a measurement."""
    factor = _BOLD_CHAR_WIDTH_FACTOR if bold else _CHAR_WIDTH_FACTOR
    return len(text) * font_size * factor


def _wrap_line(text, font_size, max_width):
    """Greedy word-wrap of `text` into as many lines as needed to each fit
    within `max_width` px at `font_size` (per _text_width's estimate).
    A single word wider than `max_width` on its own is kept whole rather
    than split mid-word — this only ever avoids an overflowing *line*, it
    doesn't guarantee every individual word fits. Always returns at least
    one (possibly empty) line."""
    words = text.split(" ")
    lines = []
    current = ""
    for word in words:
        candidate = f"{current} {word}" if current else word
        if current and _text_width(candidate, font_size) > max_width:
            lines.append(current)
            current = word
        else:
            current = candidate
    lines.append(current)
    return lines


_logo_data_uri_cache = None


def _logo_data_uri():
    """Base64 data: URI for frontend/static/logo.png, so the exported SVG
    stays self-contained (no external file reference) — read once per
    process and cached, same pattern as backend/gloss_lookup.py's/
    backend/example_sentences.py's lazy caches."""
    global _logo_data_uri_cache
    if _logo_data_uri_cache is None:
        data = _LOGO_PATH.read_bytes()
        _logo_data_uri_cache = f"data:image/png;base64,{base64.b64encode(data).decode('ascii')}"
    return _logo_data_uri_cache


def _group_clue_lines(words, direction, position_key, language):
    """Same grouping rule as script.js's renderClueLines(): one line per
    grid row (across) or column (down), several same-row/column clues
    chained with " — " instead of one line per word. Returns
    [(0-based row/col index, line text), ...] — the index is kept
    alongside the text so callers can print it as the line's bold
    row/column-number prefix, matching the grid's own header numbers.
    A word with no surviving clue gets a translated "no definition
    available" placeholder, never the bare answer — showing the word as
    its own definition is exactly the "copy" bug the backend's own
    filtering works hard to prevent (see the project-best-practices
    SKILL); a missing clue must never look like that by accident."""
    no_definition = _NO_DEFINITION.get(language, _NO_DEFINITION["en"])
    secondary_key = "col" if position_key == "row" else "row"
    by_pos = {}
    for w in words:
        if w["direction"] != direction:
            continue
        by_pos.setdefault(w[position_key], []).append(w)
    lines = []
    for pos in sorted(by_pos):
        entries = sorted(by_pos[pos], key=lambda w: w[secondary_key])
        text = " — ".join(f"({w['number']}) {w.get('clue') or no_definition}" for w in entries)
        lines.append((pos, text))
    return lines


def _n(value):
    """Two-decimal coordinate/size formatting for render_puzzle_svg."""
    return f"{value:.2f}"


def _puzzle_grid_svg(pattern, words, x0, y0, cell, letters=None):
    """Grid (black/white cells, clue numbers, 1-based row/column headers)
    for the puzzle sheets, with its top-left corner (headers included) at
    (x0, y0) and `cell` px cells, every text sized from `cell`. Empty, or
    showing each white cell's letter of `letters` (the solution) when
    given."""
    rows, cols = len(pattern), len(pattern[0])
    number_by_cell = {(w["row"], w["col"]): w["number"] for w in words}
    parts = []
    grid_x0 = x0 + cell
    grid_y0 = y0 + cell
    header_font = cell * 0.4
    for c in range(cols):
        parts.append(
            f'<text x="{_n(grid_x0 + c * cell + cell / 2)}" y="{_n(y0 + cell * 0.65)}" '
            f'font-size="{_n(header_font)}" font-family="sans-serif" text-anchor="middle" '
            f'fill="#4b5563">{c + 1}</text>'
        )
    for r in range(rows):
        parts.append(
            f'<text x="{_n(x0 + cell / 2)}" y="{_n(grid_y0 + r * cell + cell * 0.65)}" '
            f'font-size="{_n(header_font)}" font-family="sans-serif" text-anchor="middle" '
            f'fill="#4b5563">{r + 1}</text>'
        )
    for r in range(rows):
        for c in range(cols):
            x, y = grid_x0 + c * cell, grid_y0 + r * cell
            parts.append(
                f'<rect x="{_n(x)}" y="{_n(y)}" width="{_n(cell)}" height="{_n(cell)}" '
                f'fill="#ffffff" stroke="#1f2937" stroke-width="1"/>'
            )
            if pattern[r][c] == BLACK:
                parts.append(
                    f'<rect x="{_n(x + cell / 4)}" y="{_n(y + cell / 4)}" '
                    f'width="{_n(cell / 2)}" height="{_n(cell / 2)}" fill="{BLACK_CELL_FILL}"/>'
                )
                continue
            number = number_by_cell.get((r, c))
            if number:
                parts.append(
                    f'<text x="{_n(x + cell * 0.08)}" y="{_n(y + cell * 0.33)}" '
                    f'font-size="{_n(cell * 0.27)}" font-family="sans-serif">{number}</text>'
                )
            letter = letters[r][c] if letters is not None else None
            if letter and letter != BLACK:
                parts.append(
                    f'<text x="{_n(x + cell / 2)}" y="{_n(y + cell * 0.8)}" '
                    f'font-size="{_n(cell * 0.55)}" font-family="sans-serif" '
                    f'text-anchor="middle">{escape(letter)}</text>'
                )
    return "".join(parts)


def _layout_puzzle_clues(sections, font_size, columns, overflow=False):
    """Flows the clue `sections` ([(heading, [(pos, text), ...]), ...])
    into `columns` ([(x, top, width, height), ...], in reading order) at
    `font_size`, each clue line wrapped at the width of the column it
    lands in. A clue line (with its wrapped continuations) is never split
    across two columns, and a heading always stays with the line
    following it; a heading other than the first gets half a line of
    space above it, except at the top of a column. Returns the
    [(x, y, kind, payload), ...] items (y = top of the item), or None
    when they do not fit — unless `overflow`, where the last column
    simply runs past its height."""
    line_height = font_size * PDF_LINE_HEIGHT_FACTOR
    heading_height = (font_size + 2) * 1.6
    section_gap = line_height * 0.5
    blocks = []
    for index, (heading, lines) in enumerate(sections):
        pending = [("heading", heading, section_gap if index else 0.0)]
        for pos, text in lines:
            pending.append(("clue", (pos, text), 0.0))
            blocks.append(pending)
            pending = []
        if pending:
            blocks.append(pending)

    def measure(block, width):
        entries = []
        for kind, payload, _lead in block:
            if kind == "heading":
                entries.append((kind, payload, heading_height))
                continue
            pos, text = payload
            indent = _text_width(f"{pos + 1} ", font_size, bold=True)
            wrapped = _wrap_line(text, font_size, width - indent)
            entries.append((kind, (pos, wrapped, indent), line_height * len(wrapped)))
        return entries

    items = []
    column, y = 0, 0.0
    for block in blocks:
        while True:
            x, top, width, height = columns[column]
            entries = measure(block, width)
            lead = block[0][2] if y > 0 else 0.0
            total = lead + sum(entry[2] for entry in entries)
            if y + total <= height or (overflow and column == len(columns) - 1):
                break
            if y == 0 and not overflow and column == len(columns) - 1:
                return None
            column, y = column + 1, 0.0
            if column >= len(columns):
                return None
        y += lead
        for kind, payload, h in entries:
            items.append((x, top + y, kind, payload))
            y += h
    return items


def render_puzzle_svg(result, language, title="", difficulty=None):
    """Printable, answer-free puzzle sheet — the empty grid, the clues and
    the grid's title — used by GET /api/library/{grid_id}/pdf. One A4
    landscape page (`PDF_PAGE_WIDTH` x `PDF_PAGE_HEIGHT`, declared as
    297x210 mm): a header (logo, software name, title, identity line), the
    grid at the top right of the body, the across then down clues flowed
    in up to `PDF_MAX_CLUE_COLUMNS` columns on its left then in columns
    under it (`clue_columns`), and the "play online" link at the bottom
    when the record carries an id.

    Fitting: for each clue font size (`PDF_FONT_SIZES`) and column count,
    the largest cell size that fits (from the largest cell the body
    allows, at most `PDF_MAX_CELL_SIZE` and `PDF_MAX_GRID_WIDTH_FRACTION`
    of its width, down to 40 % of it); among those, the one balancing best
    the cell against `PDF_TARGET_CELL_SIZE` and the font against
    `PDF_TARGET_FONT_SIZE` (fewer columns first at equal balance). When nothing fits even at the smallest sizes, the smallest
    grid with the most columns and the smallest font is drawn anyway.

    `result` is a grid_store record (or a generate_grid() result): it
    needs `pattern`, `words` (each with `clue`/`row`/`col`/`direction`/
    `answer`), `width`, `height`."""
    words = result["words"]
    pattern = result["pattern"]
    rows, cols = len(pattern), len(pattern[0])
    sections = _puzzle_sections(words, language)
    grid_id = result.get("id")

    page_w, page_h, margin = PDF_PAGE_WIDTH, PDF_PAGE_HEIGHT, PDF_MARGIN
    content_w = page_w - 2 * margin
    body_top = margin + PDF_LOGO_SIZE + PDF_HEADER_GAP
    body_bottom = page_h - margin - (PDF_FOOTER_HEIGHT if grid_id else 0)
    body_h = body_bottom - body_top
    max_cell = min(
        body_h / (rows + 1),
        content_w * PDF_MAX_GRID_WIDTH_FRACTION / (cols + 1),
        PDF_MAX_CELL_SIZE,
    )

    def clue_columns(cell, column_count):
        """The clue columns for `cell` px cells: `column_count` columns
        left of the grid, then, when there is room under the grid,
        columns about as wide under it. None when a column would be
        narrower than PDF_MIN_CLUE_COLUMN_WIDTH."""
        grid_w = cell * (cols + 1)
        grid_h = cell * (rows + 1)
        grid_x0 = page_w - margin - grid_w
        left_w = grid_x0 - PDF_GRID_CLUES_GAP - margin
        column_w = (left_w - (column_count - 1) * PDF_CLUE_COLUMN_GAP) / column_count
        if column_w < PDF_MIN_CLUE_COLUMN_WIDTH:
            return None
        columns = [
            (margin + i * (column_w + PDF_CLUE_COLUMN_GAP), body_top, column_w, body_h)
            for i in range(column_count)
        ]
        under_top = body_top + grid_h + PDF_GRID_CLUES_GAP
        under_h = body_bottom - under_top
        if under_h > 0 and grid_w >= PDF_MIN_CLUE_COLUMN_WIDTH:
            under_count = max(1, int((grid_w + PDF_CLUE_COLUMN_GAP) // (column_w + PDF_CLUE_COLUMN_GAP)))
            under_w = (grid_w - (under_count - 1) * PDF_CLUE_COLUMN_GAP) / under_count
            columns += [
                (grid_x0 + i * (under_w + PDF_CLUE_COLUMN_GAP), under_top, under_w, under_h)
                for i in range(under_count)
            ]
        return columns

    def attempt(font_size, cell, column_count):
        columns = clue_columns(cell, column_count)
        if columns is None:
            return None
        items = _layout_puzzle_clues(sections, font_size, columns)
        if items is None:
            return None
        return font_size, cell, items

    # For each (font size, column count), the largest cell size that fits
    # (fitting only gets easier as the cell shrinks: the columns widen);
    # among those, the best balance between the cell and the font, each
    # measured against its target size.
    def cell_at(step):
        return max_cell * (1 - step * PDF_CELL_SIZE_STEP)

    def balance(candidate):
        font_size, cell = candidate[0], candidate[1]
        cell_ratio = cell / PDF_TARGET_CELL_SIZE
        font_ratio = font_size / PDF_TARGET_FONT_SIZE
        return (min(cell_ratio, font_ratio, 1.0), cell_ratio + font_ratio)

    layout = None
    for font_size in PDF_FONT_SIZES:
        for column_count in range(1, PDF_MAX_CLUE_COLUMNS + 1):
            low, high = 0, PDF_CELL_SIZE_STEPS - 1
            found = attempt(font_size, cell_at(high), column_count)
            if found is None:
                continue
            while low < high:
                middle = (low + high) // 2
                candidate = attempt(font_size, cell_at(middle), column_count)
                if candidate is None:
                    low = middle + 1
                else:
                    found, high = candidate, middle
            if layout is None or balance(found) > balance(layout):
                layout = found
    if layout is None:
        # Nothing fits: the smallest configuration, its last column
        # running past the page.
        font_size = PDF_FONT_SIZES[-1]
        cell = cell_at(PDF_CELL_SIZE_STEPS - 1)
        columns = clue_columns(cell, PDF_MAX_CLUE_COLUMNS) or clue_columns(cell, 1)
        items = _layout_puzzle_clues(sections, font_size, columns, overflow=True)
        layout = font_size, cell, items
    font_size, cell, items = layout

    parts = [_puzzle_header_svg(language, title, difficulty)]
    parts.append(_puzzle_grid_svg(pattern, words, page_w - margin - cell * (cols + 1), body_top, cell))
    parts.append(_puzzle_clue_items_svg(items, font_size))
    parts.append(_puzzle_footer_svg(language, grid_id))
    return _puzzle_page_svg("".join(parts))


def _puzzle_sections(words, language):
    """The across then down clue sections of a puzzle sheet."""
    across_heading, down_heading, _solution_heading = _HEADINGS.get(language, _HEADINGS["en"])
    return [
        (across_heading, _group_clue_lines(words, "across", "row", language)),
        (down_heading, _group_clue_lines(words, "down", "col", language)),
    ]


def _puzzle_header_svg(language, title, difficulty):
    """Puzzle sheet header: logo + software name, the grid's own title
    (bold), then a small grey identity line (version / date / language /
    difficulty)."""
    margin = PDF_MARGIN
    parts = [
        f'<image x="{_n(margin)}" y="{_n(margin)}" width="{_n(PDF_LOGO_SIZE)}" '
        f'height="{_n(PDF_LOGO_SIZE)}" href="{_logo_data_uri()}"/>'
    ]
    text_x = margin + PDF_LOGO_SIZE + 12
    version = _VERSION_PATH.read_text(encoding="utf-8").strip()
    date_str = datetime.now().strftime("%Y-%m-%d")
    language_name = _NATIVE_LANGUAGE_NAMES.get(language, language)
    difficulty_label, difficulty_names = _DIFFICULTY_LABELS.get(language, _DIFFICULTY_LABELS["en"])
    difficulty_name = difficulty_names.get(difficulty, difficulty or "")
    parts.append(
        f'<text x="{_n(text_x)}" y="{_n(margin + 14)}" font-size="16" font-family="sans-serif" '
        f'font-weight="bold">CrossWordFalcon</text>'
    )
    if title:
        parts.append(
            f'<text x="{_n(text_x)}" y="{_n(margin + 30)}" font-size="14" font-family="sans-serif" '
            f'font-weight="bold" fill="#111827">{escape(title)}</text>'
        )
    parts.append(
        f'<text x="{_n(text_x)}" y="{_n(margin + 44)}" font-size="11" font-family="sans-serif" '
        f'fill="#4b5563">v{escape(version)} — {escape(date_str)} — {escape(language_name)} — '
        f'{escape(difficulty_label)} : {escape(difficulty_name)}</text>'
    )
    return "".join(parts)


def _puzzle_clue_items_svg(items, font_size, y_shift=0.0):
    """The headings and clue lines laid out by _layout_puzzle_clues, each
    moved up by `y_shift`."""
    parts = []
    heading_size = font_size + 2
    line_height = font_size * PDF_LINE_HEIGHT_FACTOR
    for x, top, kind, payload in items:
        top -= y_shift
        if kind == "heading":
            parts.append(
                f'<text x="{_n(x)}" y="{_n(top + heading_size * 1.15)}" font-size="{_n(heading_size)}" '
                f'font-family="sans-serif" font-weight="bold">{escape(payload)}</text>'
            )
            continue
        pos, wrapped, indent = payload
        baseline = top + font_size
        parts.append(
            f'<text x="{_n(x)}" y="{_n(baseline)}" font-size="{_n(font_size)}" font-family="sans-serif">'
            f'<tspan font-weight="bold">{pos + 1}</tspan> {escape(wrapped[0])}</text>'
        )
        for index, continuation in enumerate(wrapped[1:], start=1):
            parts.append(
                f'<text x="{_n(x + indent)}" y="{_n(baseline + index * line_height)}" '
                f'font-size="{_n(font_size)}" font-family="sans-serif">{escape(continuation)}</text>'
            )
    return "".join(parts)


def _puzzle_footer_svg(language, grid_id):
    """Footer: link to play this grid online (with its solution), only
    when the record carries an id (a grid_store record)."""
    if not grid_id:
        return ""
    page_w, page_h, margin = PDF_PAGE_WIDTH, PDF_PAGE_HEIGHT, PDF_MARGIN
    play_url = f"{PLAY_ONLINE_BASE_URL}?grid={grid_id}"
    play_template = _PLAY_ONLINE_LABELS.get(language, _PLAY_ONLINE_LABELS["en"])
    line_y = page_h - margin - PDF_FOOTER_HEIGHT + 8
    return (
        f'<line x1="{_n(margin)}" y1="{_n(line_y)}" x2="{_n(page_w - margin)}" y2="{_n(line_y)}" '
        f'stroke="#d1d5db"/>'
        f'<text x="{_n(margin)}" y="{_n(page_h - margin)}" font-size="10" font-family="sans-serif" '
        f'fill="#4b5563">{escape(play_template.format(url=play_url))}</text>'
    )


def _puzzle_page_svg(body):
    """One A4 landscape page: white background, the logo watermark, then
    `body`."""
    page_w, page_h = PDF_PAGE_WIDTH, PDF_PAGE_HEIGHT
    watermark_size = page_h * 0.8
    watermark_svg = (
        f'<image x="{_n((page_w - watermark_size) / 2)}" y="{_n((page_h - watermark_size) / 2)}" '
        f'width="{_n(watermark_size)}" height="{_n(watermark_size)}" '
        f'href="{_logo_data_uri()}" opacity="0.1"/>'
    )
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="297mm" height="210mm" '
        f'viewBox="0 0 {_n(page_w)} {_n(page_h)}">'
        f'<rect x="0" y="0" width="{_n(page_w)}" height="{_n(page_h)}" fill="#ffffff"/>'
        f"{watermark_svg}"
        f"{body}</svg>"
    )


def _large_grid_page(result, language, title, difficulty, letters=None, heading=None):
    """One page holding the header, the grid centered under it at the
    largest cell size the body allows (at most PDF_MAX_CELL_SIZE) and the
    "play online" footer — the first page of render_two_page_puzzle, and,
    with the solution's `letters` and a `heading` above the grid, the
    solution export (render_solution_page)."""
    words = result["words"]
    pattern = result["pattern"]
    rows, cols = len(pattern), len(pattern[0])
    grid_id = result.get("id")
    page_w, page_h, margin = PDF_PAGE_WIDTH, PDF_PAGE_HEIGHT, PDF_MARGIN
    content_w = page_w - 2 * margin
    body_top = margin + PDF_LOGO_SIZE + PDF_HEADER_GAP
    body_bottom = page_h - margin - (PDF_FOOTER_HEIGHT if grid_id else 0)
    parts = [_puzzle_header_svg(language, title, difficulty)]
    if heading:
        parts.append(
            f'<text x="{_n(margin)}" y="{_n(body_top + 14)}" font-size="14" '
            f'font-family="sans-serif" font-weight="bold">{escape(heading)}</text>'
        )
        body_top += PDF_SOLUTION_HEADING_HEIGHT
    cell = min((body_bottom - body_top) / (rows + 1), content_w / (cols + 1), PDF_MAX_CELL_SIZE)
    grid_x0 = margin + (content_w - cell * (cols + 1)) / 2
    parts.append(_puzzle_grid_svg(pattern, words, grid_x0, body_top, cell, letters))
    parts.append(_puzzle_footer_svg(language, grid_id))
    return _puzzle_page_svg("".join(parts))


def _clue_pages(result, language):
    """The clue pages of render_two_page_puzzle: the across then down
    clues in one column spanning the page's full width, at the largest
    font of PDF_FONT_SIZES that fits on one page. When even the smallest
    one does not, the clues run on over further pages (at most
    PDF_MAX_CLUE_PAGES, the last one running past its bottom)."""
    sections = _puzzle_sections(result["words"], language)
    page_w, page_h, margin = PDF_PAGE_WIDTH, PDF_PAGE_HEIGHT, PDF_MARGIN
    content_w = page_w - 2 * margin
    clue_h = page_h - 2 * margin
    layout = None
    for font_size in PDF_FONT_SIZES:
        items = _layout_puzzle_clues(sections, font_size, [(margin, margin, content_w, clue_h)])
        if items is not None:
            layout = font_size, items
            break
    if layout is None:
        # Too long for one page even at the smallest font: one column per
        # page, stacked page_h apart, the last one running past its bottom.
        font_size = PDF_FONT_SIZES[-1]
        columns = [(margin, k * page_h + margin, content_w, clue_h) for k in range(PDF_MAX_CLUE_PAGES)]
        layout = font_size, _layout_puzzle_clues(sections, font_size, columns, overflow=True)
    font_size, items = layout
    by_page = {}
    for item in items:
        by_page.setdefault(int(item[1] // page_h), []).append(item)
    return [
        _puzzle_page_svg(_puzzle_clue_items_svg(by_page[k], font_size, k * page_h))
        for k in sorted(by_page)
    ]


def render_two_page_puzzle(result, language, title="", difficulty=None):
    """Printable puzzle sheet of a grid with a side above
    PDF_ONE_PAGE_MAX_SIDE, as a list of page SVGs: the header, grid and
    footer page (_large_grid_page), then the clue pages (_clue_pages)."""
    return [_large_grid_page(result, language, title, difficulty)] + _clue_pages(result, language)


def render_puzzle_pages(result, language, title="", difficulty=None):
    """The printable puzzle sheet as a list of page SVGs: one page
    (render_puzzle_svg) while both sides are at most
    PDF_ONE_PAGE_MAX_SIDE cells, two or more (render_two_page_puzzle)
    otherwise."""
    if _is_large_grid(result):
        return render_two_page_puzzle(result, language, title, difficulty)
    return [render_puzzle_svg(result, language, title, difficulty)]


def _is_large_grid(result):
    """True when a side of the grid exceeds PDF_ONE_PAGE_MAX_SIDE cells."""
    pattern = result["pattern"]
    return max(len(pattern), len(pattern[0])) > PDF_ONE_PAGE_MAX_SIDE


def render_solution_page(result, language, title="", difficulty=None):
    """The solution export: a puzzle-sheet page (header, footer) holding
    the solved grid under a "Solution" heading."""
    solution_heading = _HEADINGS.get(language, _HEADINGS["en"])[2]
    return _large_grid_page(
        result, language, title, difficulty, letters=result["solution"], heading=solution_heading,
    )


def render_export_pages(result, language, title="", difficulty=None):
    """The SVG/PNG export of a grid, laid out like its PDF, as
    [(suffix, svg), ...]: a grid with both sides at most
    PDF_ONE_PAGE_MAX_SIDE gives GRID (the one-page puzzle sheet, grid and
    clues) and SOLUTION; a larger one gives GRID (header, grid, footer),
    CLUES (the clue page; CLUES_2, CLUES_3... when they run on over
    further pages) and SOLUTION."""
    if _is_large_grid(result):
        pages = [("GRID", _large_grid_page(result, language, title, difficulty))]
        for index, page in enumerate(_clue_pages(result, language)):
            pages.append(("CLUES" if index == 0 else f"CLUES_{index + 1}", page))
    else:
        pages = [("GRID", render_puzzle_svg(result, language, title, difficulty))]
    pages.append(("SOLUTION", render_solution_page(result, language, title, difficulty)))
    return pages


def svg_to_pdf_bytes(svg_str):
    """Renders an SVG string — or a list of them, one PDF page each — to
    PDF bytes via `rsvg-convert -f pdf`: a single SVG goes through stdin ->
    stdout, several are written to a temporary directory and passed in
    page order. Same `rsvg-convert`/librsvg dependency as save_grid_png.
    Raises OSError if the tool is missing or fails."""
    pages = [svg_str] if isinstance(svg_str, str) else list(svg_str)
    try:
        if len(pages) == 1:
            proc = subprocess.run(
                ["rsvg-convert", "-f", "pdf"],
                input=pages[0].encode("utf-8"),
                check=True, capture_output=True,
            )
        else:
            with tempfile.TemporaryDirectory() as tmp:
                paths = []
                for index, page in enumerate(pages):
                    path = Path(tmp) / f"page{index + 1}.svg"
                    path.write_text(page, encoding="utf-8")
                    paths.append(str(path))
                proc = subprocess.run(
                    ["rsvg-convert", "-f", "pdf", *paths],
                    check=True, capture_output=True,
                )
    except FileNotFoundError as e:
        raise OSError(
            "`rsvg-convert` not found (install it with `brew install "
            "librsvg` or `apt-get install librsvg2-bin`)"
        ) from e
    except subprocess.CalledProcessError as e:
        raise OSError(f"rsvg-convert failed: {e.stderr.decode(errors='replace')}") from e
    return proc.stdout


def save_grid_svgs(result, language, difficulty=None, title="", grid_svg_dir=GRID_SVG_DIR):
    """Renders and writes the SVG export of `result` (render_export_pages),
    one file per page, named `<timestamp>_<language>_<SUFFIX>.svg`
    (sortable, the pages of one grid sharing their timestamp). Returns the
    written Paths in page order."""
    grid_svg_dir = Path(grid_svg_dir)
    grid_svg_dir.mkdir(parents=True, exist_ok=True)
    # Microsecond precision, not just seconds — two requests (different
    # browser tabs, or the polling architecture overlapping two jobs) can
    # otherwise finish within the same second and silently overwrite one
    # another's files.
    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
    paths = []
    for suffix, svg in render_export_pages(result, language, title, difficulty):
        path = grid_svg_dir / f"{timestamp}_{language}_{suffix}.svg"
        path.write_text(svg, encoding="utf-8")
        paths.append(path)
    return paths


def save_grid_png(svg_path, grid_png_dir=GRID_PNG_DIR):
    """Renders `svg_path` (a file already written by save_grid_svgs) to a
    PNG of the same basename under GRID_PNG/ (project root, gitignored —
    a generated artifact like GRID_SVG/, not source content). This is
    *not* GRID_SAMPLES/: that directory is a separate, hand-curated
    selection of example grids, kept in the repo (deliberately not
    gitignored) but no longer written to automatically — every grid the
    app generates used to be copied there, growing without bound; now
    only whichever examples someone deliberately picks and adds live
    there, at the user's explicit request. Requires the `rsvg-convert`
    CLI tool (part of `librsvg` — `brew install librsvg` / `apt-get
    install librsvg2-bin`), the same one already used to render
    frontend/static/logo.png (see the style-guide SKILL for why
    `rsvg-convert` specifically, over e.g. macOS's `qlmanage -t`).
    Raises OSError if `rsvg-convert` is missing or fails — callers should
    treat that the same as save_grid_svgs's own failure: log a warning and
    move on, never fail the actual request over a sample image. Returns
    the written Path."""
    svg_path = Path(svg_path)
    grid_png_dir = Path(grid_png_dir)
    grid_png_dir.mkdir(parents=True, exist_ok=True)
    png_path = grid_png_dir / f"{svg_path.stem}.png"
    try:
        subprocess.run(
            # The pages are 297x210 mm, which rsvg-convert renders at 96
            # DPI; `-z` PNG_DPI/96 scales that to PNG_DPI (an A4 page at
            # 300 DPI, 3508x2480 px).
            ["rsvg-convert", "-z", str(PNG_DPI / 96), "-o", str(png_path), str(svg_path)],
            check=True, capture_output=True,
        )
    except FileNotFoundError as e:
        raise OSError(
            "`rsvg-convert` not found (install it with `brew install "
            "librsvg` or `apt-get install librsvg2-bin`)"
        ) from e
    except subprocess.CalledProcessError as e:
        raise OSError(f"rsvg-convert failed: {e.stderr.decode(errors='replace')}") from e
    return png_path
