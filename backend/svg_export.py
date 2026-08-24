#!/usr/bin/env python3
"""
Renders a generated grid (backend/crossword_gen.py's `generate_grid()`
result) to a single, self-contained SVG file: the empty puzzle with its
clue lists — the same view the web UI shows before any letter is typed in
— followed by the fully-solved grid underneath. This is a durable, on-disk
record of each grid the app produces; the web UI itself has no export
feature and never persists a grid once the browser tab is closed.

Called by backend/app.py after a grid + its clues are both ready; saved
under GRIDS/ (project root, gitignored — these are generated artifacts,
not source content), one file per grid, named `<timestamp>_<language>.svg`
so files sort chronologically by filename. A PNG rendering of the same
grid is also saved under GRID_SAMPLES/ (project root, deliberately
*not* gitignored — see save_grid_png) via `rsvg-convert`.
"""
import subprocess
from datetime import datetime
from pathlib import Path
from xml.sax.saxutils import escape

from .crossword_gen import BLACK

GRIDS_DIR = Path(__file__).resolve().parent.parent / "GRIDS"
GRID_SAMPLES_DIR = Path(__file__).resolve().parent.parent / "GRID_SAMPLES"

CELL_SIZE = 26
LINE_HEIGHT = 16
MARGIN = 16
MIN_CANVAS_WIDTH = 720

# Mirrors frontend/static/script.js's I18N table (acrossHeading/downHeading,
# noDefinition) — kept in sync by hand since this is Python, not JS; update
# both places together if a heading or the placeholder text ever changes.
_HEADINGS = {
    "fr": ("Horizontalement", "Verticalement", "Solution"),
    "en": ("Across", "Down", "Solution"),
    "de": ("Waagerecht", "Senkrecht", "Lösung"),
    "es": ("Horizontales", "Verticales", "Solución"),
    "it": ("Orizzontali", "Verticali", "Soluzione"),
}

_NO_DEFINITION = {
    "fr": "Définition indisponible",
    "en": "No definition available",
    "de": "Keine Definition verfügbar",
    "es": "Definición no disponible",
    "it": "Definizione non disponibile",
}


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


def _grid_svg(pattern, letters, words, y_offset):
    """SVG markup for one grid (black/white cells, clue numbers, and
    1-based row/column index headers matching the web UI) starting at
    `y_offset`; `letters` fills in each white cell's letter when given
    (the solution view), or leaves cells blank when None (the empty
    puzzle). Returns (markup, height_in_px, width_in_px)."""
    rows, cols = len(pattern), len(pattern[0])
    number_by_cell = {(w["row"], w["col"]): w["number"] for w in words}
    parts = []
    grid_x0 = MARGIN + CELL_SIZE
    grid_y0 = y_offset + CELL_SIZE

    for c in range(cols):
        x = grid_x0 + c * CELL_SIZE
        parts.append(
            f'<text x="{x + CELL_SIZE / 2}" y="{y_offset + CELL_SIZE / 2 + 4}" font-size="10" '
            f'font-family="sans-serif" text-anchor="middle" fill="#4b5563">{c + 1}</text>'
        )
    for r in range(rows):
        y = grid_y0 + r * CELL_SIZE
        parts.append(
            f'<text x="{MARGIN + CELL_SIZE / 2}" y="{y + CELL_SIZE / 2 + 4}" font-size="10" '
            f'font-family="sans-serif" text-anchor="middle" fill="#4b5563">{r + 1}</text>'
        )

    for r in range(rows):
        for c in range(cols):
            x, y = grid_x0 + c * CELL_SIZE, grid_y0 + r * CELL_SIZE
            if pattern[r][c] == BLACK:
                parts.append(
                    f'<rect x="{x}" y="{y}" width="{CELL_SIZE}" height="{CELL_SIZE}" fill="#1f2937"/>'
                )
                continue
            parts.append(
                f'<rect x="{x}" y="{y}" width="{CELL_SIZE}" height="{CELL_SIZE}" '
                f'fill="#ffffff" stroke="#1f2937" stroke-width="1"/>'
            )
            number = number_by_cell.get((r, c))
            if number:
                parts.append(
                    f'<text x="{x + 2}" y="{y + 9}" font-size="7" font-family="sans-serif">{number}</text>'
                )
            if letters is not None:
                letter = letters[r][c]
                if letter and letter != BLACK:
                    parts.append(
                        f'<text x="{x + CELL_SIZE / 2}" y="{y + CELL_SIZE - 7}" '
                        f'font-size="{CELL_SIZE * 0.55:.0f}" font-family="sans-serif" '
                        f'text-anchor="middle">{escape(letter)}</text>'
                    )
    return "".join(parts), CELL_SIZE + rows * CELL_SIZE, CELL_SIZE + cols * CELL_SIZE


def render_grid_svg(result, language):
    """Builds the full SVG document (as a string) for one generate_grid()
    result: title, empty grid, clue lists, then the solved grid."""
    words = result["words"]
    across_heading, down_heading, solution_heading = _HEADINGS.get(language, _HEADINGS["en"])
    across_lines = _group_clue_lines(words, "across", "row", language)
    down_lines = _group_clue_lines(words, "down", "col", language)

    canvas_width = max(result["width"] * CELL_SIZE + CELL_SIZE + 2 * MARGIN, MIN_CANVAS_WIDTH)
    parts = []
    y = MARGIN

    title = f"CrossWordFalcon — {language} — {result['width']}×{result['height']}"
    parts.append(
        f'<text x="{MARGIN}" y="{y + 14}" font-size="16" font-family="sans-serif" '
        f'font-weight="bold">{escape(title)}</text>'
    )
    y += 32

    def add_heading(text):
        nonlocal y
        parts.append(
            f'<text x="{MARGIN}" y="{y + 12}" font-size="13" font-family="sans-serif" '
            f'font-weight="bold">{escape(text)}</text>'
        )
        y += 22

    def add_lines(lines):
        # Bold row (across) / column (down) index prefix, matching the
        # grid's own row/column header numbers, so a line can be matched
        # back to a specific row/column on the grid above.
        nonlocal y
        for pos, line in lines:
            parts.append(
                f'<text x="{MARGIN}" y="{y + 10}" font-size="11" font-family="sans-serif">'
                f'<tspan font-weight="bold">{pos + 1}</tspan> {escape(line)}</text>'
            )
            y += LINE_HEIGHT
        y += 10

    empty_grid_svg, grid_height, _ = _grid_svg(result["pattern"], None, words, y)
    parts.append(empty_grid_svg)
    y += grid_height + 24

    add_heading(across_heading)
    add_lines(across_lines)
    add_heading(down_heading)
    add_lines(down_lines)

    y += 8
    parts.append(f'<line x1="{MARGIN}" y1="{y}" x2="{canvas_width - MARGIN}" y2="{y}" stroke="#9ca3af"/>')
    y += 24

    add_heading(solution_heading)
    solution_grid_svg, solution_height, _ = _grid_svg(result["pattern"], result["solution"], words, y)
    parts.append(solution_grid_svg)
    y += solution_height + MARGIN

    body = "".join(parts)
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{canvas_width}" height="{y}" '
        f'viewBox="0 0 {canvas_width} {y}">'
        f'<rect x="0" y="0" width="{canvas_width}" height="{y}" fill="#ffffff"/>'
        f"{body}</svg>"
    )


def save_grid_svg(result, language, grids_dir=GRIDS_DIR):
    """Renders and writes the SVG for `result`, named
    `<timestamp>_<language>.svg` (sortable, one file per generated grid).
    Returns the written Path."""
    grids_dir = Path(grids_dir)
    grids_dir.mkdir(parents=True, exist_ok=True)
    # Microsecond precision, not just seconds — two requests (different
    # browser tabs, or the polling architecture overlapping two jobs) can
    # otherwise finish within the same second and silently overwrite one
    # another's file.
    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
    path = grids_dir / f"{timestamp}_{language}.svg"
    path.write_text(render_grid_svg(result, language), encoding="utf-8")
    return path


def save_grid_png(svg_path, grid_samples_dir=GRID_SAMPLES_DIR):
    """Renders `svg_path` (a file already written by save_grid_svg) to a
    PNG of the same basename under GRID_SAMPLES/ (project root) — unlike
    GRIDS/, this directory is deliberately *not* gitignored: a growing,
    version-controlled visual record of what the app actually produces,
    kept in the repo itself rather than only on whichever machine
    generated it, at the user's request. Requires the `rsvg-convert` CLI
    tool (part of `librsvg` — `brew install librsvg` / `apt-get install
    librsvg2-bin`), the same one already used to render
    frontend/static/logo.png (see the style-guide SKILL for why
    `rsvg-convert` specifically, over e.g. macOS's `qlmanage -t`).
    Raises OSError if `rsvg-convert` is missing or fails — callers should
    treat that the same as save_grid_svg's own failure: log a warning and
    move on, never fail the actual request over a sample image. Returns
    the written Path."""
    svg_path = Path(svg_path)
    grid_samples_dir = Path(grid_samples_dir)
    grid_samples_dir.mkdir(parents=True, exist_ok=True)
    png_path = grid_samples_dir / f"{svg_path.stem}.png"
    try:
        subprocess.run(
            ["rsvg-convert", "-o", str(png_path), str(svg_path)],
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
