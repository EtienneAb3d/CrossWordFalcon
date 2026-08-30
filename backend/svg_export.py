#!/usr/bin/env python3
"""
Renders a generated grid (backend/crossword_gen.py's `generate_grid()`
result) to a single, self-contained SVG file: the empty puzzle with its
clue lists — the same view the web UI shows before any letter is typed in
— followed by the fully-solved grid underneath. This is a durable, on-disk
record of each grid the app produces; the web UI itself has no export
feature and never persists a grid once the browser tab is closed.

Called by backend/app.py after a grid + its clues are both ready; saved
under GRID_SVG/ (project root, gitignored — these are generated
artifacts, not source content), one file per grid, named
`<timestamp>_<language>.svg` so files sort chronologically by filename.
A PNG rendering of the same grid is also saved under GRID_PNG/ (project
root, gitignored too — see save_grid_png) via `rsvg-convert`. Neither
directory is GRID_SAMPLES/: that one is a separate, hand-curated
selection of examples, never written to by this module — see
save_grid_png's own docstring.
"""
import base64
import subprocess
from datetime import datetime
from pathlib import Path
from xml.sax.saxutils import escape

from .crossword_gen import BLACK

PROJECT_ROOT = Path(__file__).resolve().parent.parent
GRID_SVG_DIR = PROJECT_ROOT / "GRID_SVG"
GRID_PNG_DIR = PROJECT_ROOT / "GRID_PNG"
_LOGO_PATH = PROJECT_ROOT / "frontend" / "static" / "logo.png"
_VERSION_PATH = PROJECT_ROOT / "VERSION.txt"

CELL_SIZE = 26
LINE_HEIGHT = 16
MARGIN = 16
MIN_CANVAS_WIDTH = 720
HEADER_LOGO_SIZE = 48
# +18 (une ligne de texte supplémentaire) à la demande explicite de
# l'utilisateur — la 3e ligne d'information (mode + durées, voir
# render_grid_svg) fait désormais dépasser la hauteur du texte au-delà de
# celle du logo lui-même (48px), qui dictait seule HEADER_HEIGHT jusqu'ici.
HEADER_HEIGHT = HEADER_LOGO_SIZE + 16 + 18
# Layout mirroring the web UI's own #board (frontend/static/style.css), at
# the user's explicit request: the across clues sit in a sidebar to the
# *left* of the empty grid (like #clues next to #grid), and the down
# clues span the full row's width in 2 columns underneath (like
# #down-clues-section's own CSS multi-column layout) rather than every
# clue list being stacked in one single column below the grid, as this
# export used to do. The across sidebar and the grid each take 50% of that
# row's width, at the user's own explicit follow-up request — since the
# grid itself can't stretch (it's a fixed number of fixed-size cells), an
# even 50/50 split means giving the sidebar exactly the grid's own
# rendered width (see render_grid_svg), not a separately-chosen constant.
GRID_SIDEBAR_GAP = 24
DOWN_COLUMN_GAP = 24
# rsvg-convert defaults to 96 DPI (screen resolution) when the source SVG has
# no physical units — GRID_PNG/ is a print-quality visual record (see
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

# Mirrors frontend/static/i18n.js's modeLabel/modeFlash/modeTurbo/modeFast/
# modeMedium/modeUltra per language, for the metadata header line (see
# backend/app.py's BUDGET_MODES for the internal key -> budget mapping).
_MODE_LABELS = {
    "fr": ("Mode", {
        "flash": "Flash", "turbo": "Turbo", "fast": "Rapide",
        "medium": "Moyen", "ultra": "Ultra",
    }),
    "en": ("Mode", {
        "flash": "Flash", "turbo": "Turbo", "fast": "Fast",
        "medium": "Medium", "ultra": "Ultra",
    }),
    "de": ("Modus", {
        "flash": "Flash", "turbo": "Turbo", "fast": "Schnell",
        "medium": "Mittel", "ultra": "Ultra",
    }),
    "es": ("Modo", {
        "flash": "Flash", "turbo": "Turbo", "fast": "Rápido",
        "medium": "Medio", "ultra": "Ultra",
    }),
    "it": ("Modalità", {
        "flash": "Flash", "turbo": "Turbo", "fast": "Veloce",
        "medium": "Medio", "ultra": "Ultra",
    }),
}

# Mirrors frontend/static/script.js's gridGenerationTime/gridOptimizationTime/
# cluesGenerationTime labels per language, for the metadata header line.
_DURATION_LABELS = {
    "fr": ("Grille générée en", "Optimisation en", "Définitions générées en"),
    "en": ("Grid generated in", "Optimized in", "Definitions generated in"),
    "de": ("Gitter erzeugt in", "Optimiert in", "Definitionen erzeugt in"),
    "es": ("Crucigrama generado en", "Optimizado en", "Definiciones generadas en"),
    "it": ("Griglia generata in", "Ottimizzata in", "Definizioni generate in"),
}


def _format_duration(seconds):
    """Mirrors frontend/static/script.js's formatDuration exactly (same
    "XhXmnXs" format, leading-zero units omitted) — a separate
    implementation since this file has no access to the frontend's own JS,
    not a shared one; keep both in sync if the format ever changes."""
    total = max(0, round(seconds or 0))
    h, rem = divmod(total, 3600)
    m, s = divmod(rem, 60)
    out = ""
    if h > 0:
        out += f"{h}h"
    if h > 0 or m > 0:
        out += f"{m}mn"
    out += f"{s}s"
    return out

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


def _heading_svg(x, y, text):
    """A single bold clue-list heading ("Horizontalement"/"Verticalement"/
    "Solution") at (x, y) — its own small helper since render_grid_svg now
    places a heading in more than one distinct layout position (the across
    sidebar, the down clues spanning the full row, the solution heading)."""
    return (
        f'<text x="{x}" y="{y + 12}" font-size="13" font-family="sans-serif" '
        f'font-weight="bold">{escape(text)}</text>'
    )


def _clue_lines_svg(x, width, y0, lines, font_size=11):
    """Word-wrapped clue lines (no heading — see _heading_svg for that)
    rendered in a column starting at (x, y0), each at most `width` px wide.
    Returns (svg_markup, height_px) so a caller laying out more than one
    such column side by side (the across sidebar next to the grid, or the
    down clues' own 2 columns — see render_grid_svg) can size/align them
    against whatever else shares that same row. Bold row/column-number
    prefix, wrapped continuation lines indented under the first line's own
    text — same convention as before this function existed, just no
    longer tied to a single shared `y`/`parts` closure so it can run more
    than once per document with independent coordinates."""
    parts = []
    y = y0
    for pos, line in lines:
        prefix = f"{pos + 1} "
        indent = _text_width(prefix, font_size, bold=True)
        wrapped = _wrap_line(line, font_size, width - indent)
        parts.append(
            f'<text x="{x}" y="{y + 10}" font-size="{font_size}" font-family="sans-serif">'
            f'<tspan font-weight="bold">{pos + 1}</tspan> {escape(wrapped[0])}</text>'
        )
        y += LINE_HEIGHT
        for continuation in wrapped[1:]:
            parts.append(
                f'<text x="{x + indent:.1f}" y="{y + 10}" font-size="{font_size}" '
                f'font-family="sans-serif">{escape(continuation)}</text>'
            )
            y += LINE_HEIGHT
    return "".join(parts), y - y0


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


def _grid_svg(pattern, letters, words, y_offset, x_offset=MARGIN):
    """SVG markup for one grid (black/white cells, clue numbers, and
    1-based row/column index headers matching the web UI) starting at
    `(x_offset, y_offset)`; `letters` fills in each white cell's letter
    when given (the solution view), or leaves cells blank when None (the
    empty puzzle). Returns (markup, height_in_px, width_in_px). `x_offset`
    (defaults to `MARGIN`, the original always-at-the-left placement) lets
    render_grid_svg's empty-puzzle grid start further right, next to the
    across clues sidebar, at the user's explicit request — the solution
    grid at the bottom keeps the default, since it has no sidebar next to
    it."""
    rows, cols = len(pattern), len(pattern[0])
    number_by_cell = {(w["row"], w["col"]): w["number"] for w in words}
    parts = []
    grid_x0 = x_offset + CELL_SIZE
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
            f'<text x="{x_offset + CELL_SIZE / 2}" y="{y + CELL_SIZE / 2 + 4}" font-size="10" '
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


def render_grid_svg(result, language, difficulty=None, mode=None):
    """Builds the full SVG document (as a string) for one generate_grid()
    result: a header identifying the grid (logo, software name, version,
    date, language, difficulty), the empty grid + clue lists, then the
    solved grid.

    `mode` (`None` par défaut — le CLI/toute génération sans budget choisi
    n'affiche alors pas cette 3e ligne du tout), à la demande explicite de
    l'utilisateur : la clé interne du sélecteur "Mode" de l'interface web
    (voir backend/app.py's BUDGET_MODES), affichée avec les 3 durées déjà
    présentes sur `result` (`generation_duration_seconds`/
    `optimization_duration_seconds`/`clues_duration_seconds`, ajoutées par
    backend/app.py — absentes pour tout appelant qui ne les fournit pas,
    auquel cas cette ligne entière est omise plutôt que d'afficher des
    zéros trompeurs)."""
    words = result["words"]
    across_heading, down_heading, solution_heading = _HEADINGS.get(language, _HEADINGS["en"])
    across_lines = _group_clue_lines(words, "across", "row", language)
    down_lines = _group_clue_lines(words, "down", "col", language)

    # The across sidebar and the empty grid each take 50% of their shared
    # row's width, at the user's explicit request — since the grid itself
    # is a fixed number of fixed-size cells (it can't stretch to fit a
    # percentage), an even split means giving the sidebar exactly the
    # grid's own rendered width, not the other way around.
    grid_width_px = CELL_SIZE + result["width"] * CELL_SIZE
    sidebar_width = grid_width_px
    canvas_width = max(
        2 * grid_width_px + GRID_SIDEBAR_GAP + 2 * MARGIN, MIN_CANVAS_WIDTH
    )
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
    # 3e ligne : mode choisi + les 3 durées, à la demande explicite de
    # l'utilisateur — omise entièrement si l'appelant n'a fourni ni `mode`
    # ni les durées sur `result` (le CLI, qui ne connaît ni l'un ni les
    # autres), plutôt que d'afficher une ligne à moitié vide ou des zéros
    # trompeurs.
    mode_label, mode_names = _MODE_LABELS.get(language, _MODE_LABELS["en"])
    grid_label, optimization_label, clues_label = _DURATION_LABELS.get(
        language, _DURATION_LABELS["en"]
    )
    info_bits = []
    if mode is not None:
        info_bits.append(f"{mode_label} {mode_names.get(mode, mode)}")
    if "generation_duration_seconds" in result:
        info_bits.append(f"{grid_label} {_format_duration(result['generation_duration_seconds'])}")
    if "optimization_duration_seconds" in result:
        info_bits.append(
            f"{optimization_label} {_format_duration(result['optimization_duration_seconds'])}"
        )
    if "clues_duration_seconds" in result:
        info_bits.append(f"{clues_label} {_format_duration(result['clues_duration_seconds'])}")
    if info_bits:
        parts.append(
            f'<text x="{text_x}" y="{logo_y + 56}" font-size="12" font-family="sans-serif" '
            f'fill="#4b5563">{escape(" — ".join(info_bits))}</text>'
        )
    y += HEADER_HEIGHT

    # Row: across clues sidebar (left, 50% width) + empty grid (right, 50%
    # width) side by side — matching the web UI's own #board layout
    # (#clues next to #grid, see frontend/static/style.css), at the user's
    # explicit request. The heading + lines share the sidebar's own local
    # y-cursor, independent of the grid's, since the two run down the page
    # at different rates — the row only advances past both once the
    # taller of the two finishes.
    sidebar_heading_svg = _heading_svg(MARGIN, y, across_heading)
    parts.append(sidebar_heading_svg)
    across_lines_svg, across_lines_height = _clue_lines_svg(
        MARGIN, sidebar_width, y + 22, across_lines
    )
    parts.append(across_lines_svg)
    sidebar_height = 22 + across_lines_height

    grid_x0 = MARGIN + sidebar_width + GRID_SIDEBAR_GAP
    empty_grid_svg, grid_height, _ = _grid_svg(result["pattern"], None, words, y, x_offset=grid_x0)
    parts.append(empty_grid_svg)

    y += max(sidebar_height, grid_height) + 24

    # Down clues span the row's full width, in 2 columns — matching the web
    # UI's own #down-clues-section (CSS multi-column), at the user's
    # explicit request. Split by count into two halves rather than
    # balancing by rendered height (CSS's own column-count only balances
    # approximately too) — simple and deterministic, and each half still
    # reads top-to-bottom in row/column order within its own column.
    parts.append(_heading_svg(MARGIN, y, down_heading))
    y += 22
    half = (len(down_lines) + 1) // 2
    down_col_width = (canvas_width - 2 * MARGIN - DOWN_COLUMN_GAP) / 2
    left_svg, left_height = _clue_lines_svg(MARGIN, down_col_width, y, down_lines[:half])
    right_x = MARGIN + down_col_width + DOWN_COLUMN_GAP
    right_svg, right_height = _clue_lines_svg(right_x, down_col_width, y, down_lines[half:])
    parts.append(left_svg)
    parts.append(right_svg)
    y += max(left_height, right_height) + 10

    y += 8
    parts.append(f'<line x1="{MARGIN}" y1="{y}" x2="{canvas_width - MARGIN}" y2="{y}" stroke="#9ca3af"/>')
    y += 24

    parts.append(_heading_svg(MARGIN, y, solution_heading))
    y += 22
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


def save_grid_svg(result, language, difficulty=None, mode=None, grid_svg_dir=GRID_SVG_DIR):
    """Renders and writes the SVG for `result`, named
    `<timestamp>_<language>.svg` (sortable, one file per generated grid).
    Returns the written Path. `mode` (see render_grid_svg's own docstring)
    threaded straight through."""
    grid_svg_dir = Path(grid_svg_dir)
    grid_svg_dir.mkdir(parents=True, exist_ok=True)
    # Microsecond precision, not just seconds — two requests (different
    # browser tabs, or the polling architecture overlapping two jobs) can
    # otherwise finish within the same second and silently overwrite one
    # another's file.
    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
    path = grid_svg_dir / f"{timestamp}_{language}.svg"
    path.write_text(render_grid_svg(result, language, difficulty, mode), encoding="utf-8")
    return path


def save_grid_png(svg_path, grid_png_dir=GRID_PNG_DIR):
    """Renders `svg_path` (a file already written by save_grid_svg) to a
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
    treat that the same as save_grid_svg's own failure: log a warning and
    move on, never fail the actual request over a sample image. Returns
    the written Path."""
    svg_path = Path(svg_path)
    grid_png_dir = Path(grid_png_dir)
    grid_png_dir.mkdir(parents=True, exist_ok=True)
    png_path = grid_png_dir / f"{svg_path.stem}.png"
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
