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
import base64
import subprocess
from datetime import datetime
from pathlib import Path
from xml.sax.saxutils import escape

from .crossword_gen import BLACK

PROJECT_ROOT = Path(__file__).resolve().parent.parent
GRIDS_DIR = PROJECT_ROOT / "GRIDS"
GRID_SAMPLES_DIR = PROJECT_ROOT / "GRID_SAMPLES"
_LOGO_PATH = PROJECT_ROOT / "frontend" / "static" / "logo.png"
_VERSION_PATH = PROJECT_ROOT / "VERSION.txt"

CELL_SIZE = 26
LINE_HEIGHT = 16
MARGIN = 16
MIN_CANVAS_WIDTH = 720
HEADER_LOGO_SIZE = 48
HEADER_HEIGHT = HEADER_LOGO_SIZE + 16
# rsvg-convert defaults to 96 DPI (screen resolution) when the source SVG has
# no physical units — GRID_SAMPLES/ is a print-quality visual record (see
# save_grid_png), so it's rendered at 300 DPI instead, scaling up the output
# pixel dimensions (not the SVG's own layout) accordingly.
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
}

_NO_DEFINITION = {
    "fr": "Définition indisponible",
    "en": "No definition available",
    "de": "Keine Definition verfügbar",
    "es": "Definición no disponible",
    "it": "Definizione non disponibile",
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
}

# Mirrors frontend/static/i18n.js's difficultyLabel/difficultyEasy/
# difficultyMedium/difficultyHard per language, for the metadata header line.
_DIFFICULTY_LABELS = {
    "fr": ("Difficulté", {"easy": "Facile", "medium": "Moyenne", "hard": "Difficile"}),
    "en": ("Difficulty", {"easy": "Easy", "medium": "Medium", "hard": "Hard"}),
    "de": ("Schwierigkeit", {"easy": "Leicht", "medium": "Mittel", "hard": "Schwer"}),
    "es": ("Dificultad", {"easy": "Fácil", "medium": "Media", "hard": "Difícil"}),
    "it": ("Difficoltà", {"easy": "Facile", "medium": "Media", "hard": "Difficile"}),
}

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


def render_grid_svg(result, language, difficulty=None):
    """Builds the full SVG document (as a string) for one generate_grid()
    result: a header identifying the grid (logo, software name, version,
    date, language, difficulty), the empty grid + clue lists, then the
    solved grid."""
    words = result["words"]
    across_heading, down_heading, solution_heading = _HEADINGS.get(language, _HEADINGS["en"])
    across_lines = _group_clue_lines(words, "across", "row", language)
    down_lines = _group_clue_lines(words, "down", "col", language)

    canvas_width = max(result["width"] * CELL_SIZE + CELL_SIZE + 2 * MARGIN, MIN_CANVAS_WIDTH)
    parts = []
    y = MARGIN

    # Header: logo + software name/version on one line, generation date +
    # grid language + difficulty on the next — identifies the file at a
    # glance without needing to trust its filename/timestamp alone.
    logo_x, logo_y = MARGIN, y
    parts.append(
        f'<image x="{logo_x}" y="{logo_y}" width="{HEADER_LOGO_SIZE}" height="{HEADER_LOGO_SIZE}" '
        f'href="{_logo_data_uri()}"/>'
    )
    text_x = logo_x + HEADER_LOGO_SIZE + 12
    version = _VERSION_PATH.read_text(encoding="utf-8").strip()
    date_str = datetime.now().strftime("%Y-%m-%d")
    language_name = _NATIVE_LANGUAGE_NAMES.get(language, language)
    difficulty_label, difficulty_names = _DIFFICULTY_LABELS.get(language, _DIFFICULTY_LABELS["en"])
    difficulty_name = difficulty_names.get(difficulty, difficulty or "")
    parts.append(
        f'<text x="{text_x}" y="{logo_y + 20}" font-size="18" font-family="sans-serif" '
        f'font-weight="bold">CrossWordFalcon</text>'
        f'<text x="{text_x}" y="{logo_y + 38}" font-size="12" font-family="sans-serif" '
        f'fill="#4b5563">v{escape(version)} — {escape(date_str)} — {escape(language_name)} — '
        f'{escape(difficulty_label)} : {escape(difficulty_name)}</text>'
    )
    y += HEADER_HEIGHT

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
    # Faint logo watermark behind the whole page — mirrors the web UI's own
    # watermark (frontend/static/style.css's `body::before`), sized/
    # positioned differently since this is a fixed document rather than a
    # viewport: 90% of the canvas's width (logo.png is ~square, ~1022x1024,
    # so height uses the same value rather than a separate aspect-ratio
    # calculation) and centered vertically in the *final* page height `y`
    # (known only now, after the header/grids/clues above have all been
    # laid out) rather than some intermediate/partial height — placed right
    # after the background rect and before every real element (`body`), so
    # it paints behind all of them in SVG's document-order paint model.
    # `opacity="0.1"` on the <image> itself is the same 90%-transparent
    # treatment as the web UI, not a filter on the embedded PNG.
    watermark_size = canvas_width * 0.9
    watermark_x = (canvas_width - watermark_size) / 2
    watermark_y = (y - watermark_size) / 2
    watermark_svg = (
        f'<image x="{watermark_x:.1f}" y="{watermark_y:.1f}" '
        f'width="{watermark_size:.1f}" height="{watermark_size:.1f}" '
        f'href="{_logo_data_uri()}" opacity="0.1"/>'
    )
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{canvas_width}" height="{y}" '
        f'viewBox="0 0 {canvas_width} {y}">'
        f'<rect x="0" y="0" width="{canvas_width}" height="{y}" fill="#ffffff"/>'
        f"{watermark_svg}"
        f"{body}</svg>"
    )


def save_grid_svg(result, language, difficulty=None, grids_dir=GRIDS_DIR):
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
    path.write_text(render_grid_svg(result, language, difficulty), encoding="utf-8")
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
            # `--dpi-x`/`--dpi-y` only rescale physical units (in/mm/pt) —
            # this SVG's root <svg> has none (its width/height are bare
            # numbers, i.e. CSS px), so librsvg's default of "1 px = 1/96
            # inch" means those flags alone would have no effect (verified
            # directly: identical output size with or without them). `-z`
            # (zoom) is what actually scales pixel output on a unitless
            # SVG — PNG_DPI/96 reproduces the same effect a true 300 DPI
            # setting would have on a document authored at the standard
            # 96 CSS-px-per-inch baseline.
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
