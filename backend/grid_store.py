#!/usr/bin/env python3
"""Persists every finished grid as a durable, self-contained JSON record
under GRID_STORE/<language>/ (project root, gitignored — a generated
artifact, not source content, the same convention as GRID_SVG/GRID_PNG,
see backend/svg_export.py), at the user's explicit request: "Génère un
fichier JSON dans GRID_STORE/<lang> décrivant toute la configuration de
la grille, son titre, ses définitions et les infos de création (date,
mode, langue, etc, comme sur la sauvegarde SVG)."

Feeds the web UI's "Bibliothèque" button (see frontend/static/script.js):
a stored record IS a generate_grid() result dict (pattern/solution/words
with their own clue, plus the three duration fields backend/app.py
already adds) — just extended with the handful of metadata fields the
frontend doesn't otherwise get from a live job (id/title/language/
difficulty/mode/created_at) — so loading a past grid back into the
player renders it through the exact same code path as a grid that just
finished generating, with no special-casing needed on the frontend.

Filenames, at the user's own explicit follow-up request ("les noms de
fichiers sont préfixés par la date, puis le titre de la grille, et
finalement un code sur 4 chiffres aléatoire pour éviter que 2 mêmes
titres à la même date ne s'écrasent l'un l'autre"):
`<timestamp>_<title-slug>_<4-digit code>.json` — the timestamp alone
(unlike svg_export.py's own `<timestamp>_<language>.json`, which never
needed more than that) isn't a safe-enough uniqueness guarantee once the
title is baked into the name too: two grids of the same language finished
in the same second, with an LLM-generated title empty or short enough to
collide, would otherwise overwrite one another. The random 4-digit
suffix rules that out without needing a global counter or a lock.
"""
import json
import re
import secrets
import unicodedata
from datetime import datetime
from pathlib import Path

GRID_STORE_DIR = Path(__file__).resolve().parent.parent / "GRID_STORE"

# Longest a title slug is ever allowed to grow to, regardless of how long
# the LLM-generated title itself turned out to be (already clamped to a
# few words by backend/clues.py's MAX_TITLE_WORDS, but a handful of very
# long words could still make an unwieldy filename) — purely a filename-
# length safety margin, never shown to the user (the real title, kept
# verbatim in the JSON's own "title" field, is what the UI displays).
MAX_SLUG_LENGTH = 40

_SLUG_RE = re.compile(r"[^a-zA-Z0-9]+")

# Matches exactly the shape save_grid_json() below produces
# (YYYYMMDD-HHMMSS-ffffff_<slug>_<4-digit code>) — used both to validate a
# caller-supplied id (GET /api/library/{grid_id}, see backend/app.py)
# before it's ever interpolated into a glob pattern, and to reject
# anything else outright, so a hand-crafted id can never walk out of
# GRID_STORE_DIR via "../" or similar: no slash, "..", or other path
# metacharacter can ever match this pattern. The slug segment's own
# charset (`[a-z0-9_-]+`) deliberately still accepts a hyphen too, even
# though _slugify_title() itself only ever *produces* underscores now
# (see its own history) — a handful of real grids saved before that
# switch already exist on disk with hyphen-based slugs, and narrowing
# this pattern to underscore-only would make GET /api/library/{grid_id}
# 404 on every one of them (list_grids() itself never validates a
# filename against this regex at all, only get_grid() does, so they'd
# still be listed — just impossible to actually load and play).
_GRID_ID_RE = re.compile(r"^\d{8}-\d{6}-\d{6}_[a-z0-9_-]+_\d{4}$")


def _slugify_title(title):
    """ASCII, filesystem-safe slug built from a grid's own title (see
    backend/clues.py's LLMClueGenerator.generate_title) — accents
    stripped via NFKD decomposition + ASCII-only re-encode (the words
    themselves stay fully readable in the filename for anyone browsing
    GRID_STORE/ by hand, just without diacritics), every run of
    non-alphanumeric characters (including plain spaces between words)
    collapsed to a single underscore, capped at MAX_SLUG_LENGTH —
    underscore rather than hyphen, at the user's explicit request:
    "Les titres des grilles étant ajoutées aux noms de fichiers, remplace
    les caractères spéciaux du titre, y compris les espaces, par des '_'
    pour la sauvegarde." (a plain hyphen was already what the very first
    version of this function used — this only changes which character,
    never whether non-alphanumeric runs get collapsed at all). Falls back
    to the generic "grille" for an empty/unusable title (title generation
    itself failed — see generate_title's own "" return on failure) rather
    than leaving the filename's own title segment blank, which would look
    like a mistake to anyone browsing the directory."""
    ascii_title = unicodedata.normalize("NFKD", title or "").encode("ascii", "ignore").decode("ascii")
    slug = _SLUG_RE.sub("_", ascii_title).strip("_").lower()
    return slug[:MAX_SLUG_LENGTH].strip("_") or "grille"


def save_grid_json(result, language, difficulty, mode, title):
    """Writes the grid to GRID_STORE/<language>/<id>.json and returns the
    new record's own id (its filename stem, without the .json extension)
    — best-effort, like svg_export.py's own saves: a write failure here
    should never break an otherwise-successful generation, so the one
    caller (backend/app.py) wraps this in its own try/except, exactly
    like it already does for save_grid_svg/save_grid_png."""
    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
    slug = _slugify_title(title)
    code = f"{secrets.randbelow(10_000):04d}"
    grid_id = f"{timestamp}_{slug}_{code}"
    directory = GRID_STORE_DIR / language
    directory.mkdir(parents=True, exist_ok=True)
    record = {
        **result,
        "id": grid_id,
        "title": title,
        "language": language,
        "difficulty": difficulty,
        "mode": mode,
        "created_at": datetime.now().isoformat(),
    }
    (directory / f"{grid_id}.json").write_text(
        json.dumps(record, ensure_ascii=False), encoding="utf-8",
    )
    return grid_id


def _iter_stored_grids():
    """Yields every stored grid's own compact metadata dict ({id,
    created_at, language, difficulty, title, width, height}) — never the
    full pattern/solution/words payload, so listing many grids stays
    cheap even though each individual file can run to several dozen KB.
    A file that fails to parse (corrupted, or written by some future,
    incompatible version of save_grid_json) is skipped rather than
    failing the whole listing."""
    if not GRID_STORE_DIR.is_dir():
        return
    for path in GRID_STORE_DIR.glob("*/*.json"):
        try:
            with open(path, encoding="utf-8") as f:
                record = json.load(f)
        except (OSError, json.JSONDecodeError):
            continue
        yield {
            "id": record.get("id", path.stem),
            "created_at": record.get("created_at"),
            "language": record.get("language"),
            "difficulty": record.get("difficulty"),
            "title": record.get("title"),
            "width": record.get("width"),
            "height": record.get("height"),
        }


def list_grids(preferred_language=None):
    """Every stored grid's compact metadata, sorted at the user's explicit
    request: grids in `preferred_language` first, then English (unless
    that's already `preferred_language`, in which case there's no separate
    "English second" group to carve out), then every other language —
    most recent first within each of those three groups. `preferred_
    language` is whatever language the web UI's own single language
    selector is currently set to (it drives both the puzzle language and
    the UI's own language — see CLAUDE.md), not necessarily the language
    of any specific stored grid.

    Implemented as two separate, stable sort passes rather than one
    combined key: first by `created_at` descending (a plain string sort
    already gives the right order, since `created_at` is always an ISO
    8601 timestamp — lexicographic order matches chronological order for
    that format), then by `group` ascending. Python's sort is stable, so
    the "most recent first" order the first pass established survives
    intact within each group after the second pass reorders the groups
    themselves — no need to compute a single combined sort key."""
    def group(entry):
        lang = entry.get("language")
        if lang == preferred_language:
            return 0
        if lang == "en" and preferred_language != "en":
            return 1
        return 2

    grids = list(_iter_stored_grids())
    grids.sort(key=lambda e: e.get("created_at") or "", reverse=True)
    grids.sort(key=group)
    return grids


def get_grid(grid_id):
    """The full stored record for `grid_id` (everything save_grid_json
    wrote, including the entire pattern/solution/words/clues payload,
    ready to hand straight to the frontend exactly as a live job's own
    `result` would be) — or None if `grid_id` doesn't match the expected
    shape at all (see _GRID_ID_RE) or no matching file exists. The id
    itself carries no language (see save_grid_json's own docstring for
    why the filename is built this way) — GRID_STORE_DIR only ever has a
    handful of language subdirectories, so a glob restricted to an
    already-validated id is simple and cheap rather than needing the
    caller to also supply the language."""
    if not _GRID_ID_RE.match(grid_id):
        return None
    matches = list(GRID_STORE_DIR.glob(f"*/{grid_id}.json"))
    if not matches:
        return None
    try:
        with open(matches[0], encoding="utf-8") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return None
