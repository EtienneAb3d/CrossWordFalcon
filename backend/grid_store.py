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


def save_grid_json(result, language, difficulty, mode, title, bilingual=None, pseudo=None,
                   theme=None, interactive=False, origin=None):
    """Writes the grid to GRID_STORE/<language>/<id>.json — or, for a
    genuinely bilingual grid, GRID_STORE/bilingual/<id>.json instead — and
    returns the new record's own id (its filename stem, without the .json
    extension) — best-effort, like svg_export.py's own saves: a write
    failure here should never break an otherwise-successful generation,
    so the one caller (backend/app.py) wraps this in its own try/except,
    exactly like it already does for save_grid_svg/save_grid_png.

    `bilingual` (`None` by default — every pre-existing caller, and every
    ordinary monolingual grid, unaffected) is the grid's own second
    language (its vertical words' language — see crossword_gen.py's
    `generate_grid`'s own `bilingual_language`), at the user's explicit
    request: "les grilles sont sauvegardées avec la configuration des
    deux langues 'language' et 'bilingual'. Les grilles bilingues vont
    dans le STORE bilingual." A grid is only ever treated as genuinely
    bilingual when `bilingual` is both given AND different from
    `language` — the record's own `language` field always stays the
    grid's primary (horizontal-words) language either way, matching
    every other field crossword_gen.py already returns; only the
    directory it's filed under, and the extra `bilingual` field itself,
    change. `list_grids`/`_iter_stored_grids` below need no change to
    find these: `GRID_STORE_DIR.glob("*/*.json")` already walks every
    language subdirectory, "bilingual" included, since it's just one
    more folder name to that glob.

    `pseudo` (`None` by default — every pre-existing caller, and every
    grid saved by a user who never set one, unaffected) is the nickname
    of whoever generated the grid, at the user's explicit request:
    "Quand une grille est sauvegardée, si un pseudo est défini,
    sauvegarder le pseudo dans le JSON de la grille." Stored verbatim in
    the record's own `pseudo` field (blank/whitespace normalised to
    `None`) so the web UI can show an author column and offer a "Mes
    grilles" filter (see backend/app.py's `_library_page`).

    `origin` (`None` by default — every pre-existing caller unaffected)
    is set only for a grid saved after being hand-edited from an
    existing library grid (the "Ouvrir en mode Interactif" icon button —
    see backend/app.py's `_library_record_to_interactive`/POST
    `/api/interactive/from-library`), at the user's explicit request:
    "Quand un utilisateur modifie une grille sélectionnée dans la
    Bibliothèque, conserver dans la sauvegarde de la nouvelle grille, les
    information sur la grille d'origine : nom de la grille, date de
    création de la grille, auteur de la grille, ID de la grille." A plain
    `{"id", "title", "pseudo", "created_at"}` dict, a straight snapshot of
    the origin grid's own record at the moment editing started — never
    re-read from the origin grid later (which may itself since have been
    edited or deleted), so this stays a durable, self-contained record of
    "what this grid was derived from" even if the origin's own file
    later changes or disappears. Threaded through unchanged whenever the
    edited grid is itself autosaved to GRID_WORK and back (see
    `save_grid_work`'s own `origin` parameter) and re-published, so the
    provenance survives a pause/resume of the editing session too. Drives
    the "(créée depuis ...)" provenance tag the web UI's own Bibliothèque
    list shows next to such a grid's title (see `_iter_stored_grids`
    below and `frontend/static/script.js`'s `renderLibraryList`)."""
    is_bilingual = bool(bilingual) and bilingual != language
    pseudo = (pseudo or "").strip() or None
    # Thématique saisie par l'utilisateur (liste de mots), à la demande
    # explicite : "Lors de la sauvegarde de la grille, enregistrer les
    # mots de la thématique si il y en a." Stockée telle quelle (la
    # chaîne saisie, pas les ~5000 mots présélectionnés par la
    # pré-recherche Qdrant) dans le champ `theme` du record ;
    # blanc/espaces -> None. Affichée dans la colonne "Thématique" de la
    # Bibliothèque (voir _iter_stored_grids et frontend/static/script.js).
    theme = (theme or "").strip() or None
    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
    slug = _slugify_title(title)
    code = f"{secrets.randbelow(10_000):04d}"
    grid_id = f"{timestamp}_{slug}_{code}"
    directory = GRID_STORE_DIR / ("bilingual" if is_bilingual else language)
    directory.mkdir(parents=True, exist_ok=True)
    record = {
        **result,
        "id": grid_id,
        "title": title,
        "language": language,
        "bilingual": bilingual if is_bilingual else None,
        "pseudo": pseudo,
        "theme": theme,
        # True for a grid hand-authored via the web UI's "Interactif" mode
        # (word-by-word construction + hand-written clues) — stored as
        # None rather than False so an older record reads identically via
        # .get(). Shown as a "(Création)" tag next to the author in the
        # library list.
        "interactive": bool(interactive) or None,
        # See this function's own docstring — a snapshot of the origin
        # grid's {id, title, pseudo, created_at}, or None for a grid not
        # derived from an existing library grid.
        "origin": origin,
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
    created_at, language, bilingual, pseudo, interactive, origin, theme,
    difficulty, title, width, height}) — never the full pattern/solution/
    words payload, so listing
    many grids stays cheap even though each file can run to several
    dozen KB.
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
            # The grid's own second (vertical-words) language — see
            # save_grid_json's own docstring — `None`/absent for every
            # ordinary, monolingual grid. Lets GET /api/library's own
            # `language_filter=="bilingual"` (backend/app.py) pick out
            # exactly these entries without needing a directory-name
            # convention of its own.
            "bilingual": record.get("bilingual"),
            # Nickname of whoever generated the grid (see save_grid_json)
            # — `None`/absent for a grid saved before this field existed
            # or by a user who never set a pseudo. Lets GET /api/library
            # show an author column and offer a "Mes grilles" filter
            # (backend/app.py's `_library_page`).
            "pseudo": record.get("pseudo"),
            # True for a grid hand-authored via the "Interactif" mode (see
            # save_grid_json) — drives the "(Création)" tag next to the
            # author in the library list (frontend/static/script.js,
            # renderLibraryList). None/absent for every ordinary grid.
            "interactive": record.get("interactive"),
            # Snapshot of the origin grid this one was edited from (see
            # save_grid_json's own `origin` parameter) — None/absent for
            # any grid not created by editing an existing library grid.
            # Drives the "(créée depuis ...)" provenance tag next to the
            # title in the library list (frontend/static/script.js).
            "origin": record.get("origin"),
            # Thématique saisie à la génération (voir save_grid_json) —
            # `None`/absent pour une grille sans thématique ou d'avant ce
            # champ. Affichée dans la colonne "Thématique" de la
            # Bibliothèque (frontend/static/script.js, renderLibraryList).
            "theme": record.get("theme"),
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


# ---------------------------------------------------------------------------
# GRID_WORK — autosaved, in-progress "Interactif" authoring sessions (project
# root, gitignored, same convention as GRID_STORE — a generated work-in-
# progress artifact, not source content), at the user's explicit request:
# "chaque appui sur Suivant/Précédent sauvegarde l'état en cours du process
# de création dans le dossier GRID_WORK. Préfixer le fichier avec un
# timestamp, puis le nom de l'auteur. Quand un utilisateur ouvre l'interface
# (ou la recharge), si il a des grilles sauvegardées dans GRID_WORK, afficher
# un panneau avec la liste. Il peut cliquer pour relancer sa session
# interactive où elle s'était arrêtée, cliquer sur un bouton icône pour
# supprimer la tâche." See backend/app.py's POST /api/interactive/save_work
# (autosave), GET /api/interactive/work (the "Créations" panel's own list),
# POST /api/interactive/work/delete, and POST /api/interactive/resume.
#
# Unlike GRID_STORE (one brand-new file per *finished* grid, never touched
# again), a GRID_WORK file is the single, continuously-updated snapshot of
# ONE still-in-progress session — every autosave OVERWRITES the same file
# rather than piling up a new one, found again across the session's whole
# lifetime by its own stable `job_id` suffix (the one part of the filename
# that never changes once assigned, unlike the pseudo segment — see
# save_grid_work's own docstring). This is why the filename shape below has
# an extra segment (the full job_id) beyond GRID_STORE's own
# `<timestamp>_<slug>_<4-digit code>` — a random 4-digit code is only ever
# meant to break a same-second collision for two otherwise-unrelated saves,
# never to serve as a stable lookup key the way a job_id already, uniquely,
# does on its own.
# ---------------------------------------------------------------------------

GRID_WORK_DIR = Path(__file__).resolve().parent.parent / "GRID_WORK"

# Matches exactly the shape save_grid_work() below produces
# (YYYYMMDD-HHMMSS-ffffff_<pseudo slug>_<32-hex job_id>) — same role as
# _GRID_ID_RE above: validated before ever being interpolated into a path,
# so a hand-crafted id can never walk out of GRID_WORK_DIR.
_WORK_ID_RE = re.compile(r"^\d{8}-\d{6}-\d{6}_[a-z0-9_]+_[0-9a-f]{32}$")


def _slugify_pseudo(pseudo):
    """Same ASCII/underscore slugging as _slugify_title above, applied to a
    player's own pseudo instead of a grid title — falls back to "anonyme"
    for an empty/whitespace-only pseudo rather than leaving that segment of
    the filename blank."""
    ascii_pseudo = unicodedata.normalize("NFKD", pseudo or "").encode("ascii", "ignore").decode("ascii")
    slug = _SLUG_RE.sub("_", ascii_pseudo).strip("_").lower()
    return slug[:MAX_SLUG_LENGTH].strip("_") or "anonyme"


def save_grid_work(job_id, grid, definitions, title, language, difficulty, theme,
                    priority_words, seed, pseudo=None, resumed_from=None, origin=None):
    """Autosaves (or updates) the in-progress state of one "Interactif"
    session. The very first call for a given `job_id` creates
    `GRID_WORK/<timestamp>_<pseudo slug>_<job_id>.json`; every later call
    for that same `job_id` finds that exact file again (globbing for its
    own `_<job_id>.json` suffix — always unique, since `job_id` itself
    is) and overwrites it in place — the filename's own timestamp/pseudo
    segments are therefore fixed at first save and never renamed, even if
    the player's own pseudo changes mid-session (the record's own `pseudo`
    field, unlike the filename, always reflects the latest value) — only
    the record's own `updated_at` field, and of course its content, change
    on a later save.

    `resumed_from` (the OLD work id a resumed session started from — see
    POST /api/interactive/resume) matters only the very first time this
    is called for a brand-new `job_id` that came from a resume: without
    it, that first autosave would find no file for the (fresh) `job_id`
    and create a genuinely new one, silently orphaning the original
    file — the exact same in-progress creation would then exist twice,
    one stale and one live. When given and no file for `job_id` exists
    yet, this looks for `resumed_from`'s own file instead and RENAMES it
    to the new `job_id` (keeping its original timestamp/pseudo segments
    and its own `created_at`) rather than starting fresh — a no-op on
    every later call for the same session, since by then the file's own
    name already matches the current `job_id` and the normal lookup above
    finds it directly.

    Carries everything POST /api/interactive/resume needs to rebuild the
    session from scratch without redoing any expensive or non-repeatable
    work: `priority_words` (the already-resolved theme glossary — resuming
    must never re-run the theme LLM/Qdrant lookup, which is neither
    deterministic nor cheap) and `seed` (so a resumed session's own
    Filler/CSP randomness comes from a real, reproducible starting point —
    the exact rng state at pause time is neither persisted nor needed to
    be, since nothing about "Suivant" placing a further word depends on
    continuing the *exact* same random sequence a resumed session would
    have followed had it never been interrupted). Returns the record's own
    id (its filename stem).

    `origin` (`None` by default) is the same `{id, title, pseudo,
    created_at}` snapshot `save_grid_json` accepts — see its own
    docstring — carried through here too so a session's provenance
    survives an autosave/resume/publish cycle: `backend/app.py` reads it
    back from `JOBS[job_id]["interactive"]["origin"]` (set once, at
    session start/resume, from whichever record this session came from —
    a library grid, or a previously-resumed GRID_WORK entry that already
    carried one) and passes it straight through on every autosave, so it
    never has to be re-derived."""
    pseudo = (pseudo or "").strip() or None
    existing = list(GRID_WORK_DIR.glob(f"*_{job_id}.json")) if GRID_WORK_DIR.is_dir() else []
    created_at = None
    if existing:
        path = existing[0]
        work_id = path.stem
        try:
            with open(path, encoding="utf-8") as f:
                created_at = json.load(f).get("created_at")
        except (OSError, json.JSONDecodeError):
            created_at = None
    elif resumed_from and _WORK_ID_RE.match(resumed_from) and (GRID_WORK_DIR / f"{resumed_from}.json").is_file():
        old_path = GRID_WORK_DIR / f"{resumed_from}.json"
        try:
            with open(old_path, encoding="utf-8") as f:
                created_at = json.load(f).get("created_at")
        except (OSError, json.JSONDecodeError):
            created_at = None
        # Keep the old file's own timestamp/pseudo-slug segments — only the
        # trailing job_id changes — so the "when was this really started"
        # information in the filename survives the resume.
        prefix = resumed_from.rsplit("_", 1)[0]
        work_id = f"{prefix}_{job_id}"
        path = GRID_WORK_DIR / f"{work_id}.json"
        old_path.rename(path)
    else:
        GRID_WORK_DIR.mkdir(parents=True, exist_ok=True)
        timestamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
        slug = _slugify_pseudo(pseudo)
        work_id = f"{timestamp}_{slug}_{job_id}"
        path = GRID_WORK_DIR / f"{work_id}.json"
    now = datetime.now().isoformat()
    record = {
        "id": work_id,
        "job_id": job_id,
        "grid": grid,
        "definitions": definitions,
        "title": title,
        "language": language,
        "difficulty": difficulty,
        "theme": theme,
        "priority_words": sorted(priority_words or ()),
        "seed": seed,
        "pseudo": pseudo,
        "origin": origin,
        "created_at": created_at or now,
        "updated_at": now,
    }
    path.write_text(json.dumps(record, ensure_ascii=False), encoding="utf-8")
    return work_id


def _iter_stored_grid_work():
    """Yields every saved work-in-progress's own compact metadata — never
    the full grid/definitions payload, so listing stays cheap. A file that
    fails to parse is skipped rather than failing the whole listing (same
    tolerance as _iter_stored_grids above)."""
    if not GRID_WORK_DIR.is_dir():
        return
    for path in GRID_WORK_DIR.glob("*.json"):
        try:
            with open(path, encoding="utf-8") as f:
                record = json.load(f)
        except (OSError, json.JSONDecodeError):
            continue
        grid = record.get("grid") or []
        yield {
            "id": record.get("id", path.stem),
            "title": record.get("title"),
            "language": record.get("language"),
            "difficulty": record.get("difficulty"),
            "theme": record.get("theme"),
            "pseudo": record.get("pseudo"),
            # See save_grid_work's own `origin` parameter — None/absent
            # unless this session started by editing an existing library
            # grid. Not currently shown by the "Créations" panel; kept
            # here for parity with _iter_stored_grids and in case a later
            # feature wants it.
            "origin": record.get("origin"),
            "created_at": record.get("created_at"),
            "updated_at": record.get("updated_at"),
            "width": len(grid[0]) if grid and grid[0] else None,
            "height": len(grid) if grid else None,
        }


def list_grid_work(pseudo=None):
    """Every saved work-in-progress's compact metadata, most recently
    UPDATED first (unlike list_grids' own created_at-based sort — what
    matters here is which session was touched most recently, not which
    was started first). `pseudo`, if given (stripped; blank means no
    filter), keeps only entries whose own saved pseudo matches exactly —
    the same convention already established for the library's own "Mes
    grilles" filter (backend/app.py's _library_page) — so a player only
    ever sees their own in-progress creations, never anyone else's."""
    pseudo = (pseudo or "").strip()
    entries = [
        e for e in _iter_stored_grid_work()
        if not pseudo or (e.get("pseudo") or "").strip() == pseudo
    ]
    entries.sort(key=lambda e: e.get("updated_at") or "", reverse=True)
    return entries


def get_grid_work(work_id):
    """The full saved record for `work_id` — or None if it doesn't match
    the expected shape (see _WORK_ID_RE) or no matching file exists."""
    if not _WORK_ID_RE.match(work_id):
        return None
    path = GRID_WORK_DIR / f"{work_id}.json"
    if not path.is_file():
        return None
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return None


def delete_grid_work(work_id):
    """Deletes a saved work-in-progress file. Returns True if a file was
    actually removed, False for a malformed id or one with no matching
    file — never raises either way, mirroring get_grid_work's own
    tolerant validation."""
    if not _WORK_ID_RE.match(work_id):
        return False
    path = GRID_WORK_DIR / f"{work_id}.json"
    try:
        path.unlink()
        return True
    except OSError:
        return False


# ---------------------------------------------------------------------------
# GRID_GAME — a player's own in-progress PLAY state for one library grid
# (project root, gitignored, same convention as GRID_STORE/GRID_WORK — a
# generated artifact, not source content), at the user's explicit request:
# "A chaque modification de la grille, sauvegarder l'état de la grille dans
# GRID_GAME avec le nom de l'utilisateur pour pouvoir la recharger plus
# tard. Inclure l'état du compteur temps. Dans la Librairie, quand un
# utilisateur clique pour jouer sur une grille, chercher si cette grille
# existe dans GRID_GAME pour la recharger et relancer le compteur de temps
# là où il était à la sauvegarde."
#
# Unlike GRID_STORE (one brand-new, never-touched-again file per finished
# grid) or GRID_WORK (one file per in-progress "Interactif" *authoring*
# session, found by its own job_id), a GRID_GAME record is keyed by the
# pair (grid_id, pseudo) — the same grid can be played, independently, by
# several different players, each with their own saved letters/elapsed
# time. There is exactly one file per pair, always overwritten in place —
# every letter the player types is a fresh snapshot of the whole grid, not
# an append-only log. Filed as GRID_GAME/<grid_id>/<pseudo-slug>.json (one
# subdirectory per grid rather than a single flat directory, or the
# <timestamp>_<slug>_<...> naming GRID_STORE/GRID_WORK use) so the read
# side (backend/app.py's `library_get`, given only a grid_id + a pseudo)
# can find — or fail to find — the exact right file with a single,
# non-glob path lookup: no timestamp is ever part of the key, since a
# player's own saved game for a given grid is a singleton, not a series.
# Both path segments are safe to use directly, without a further
# existence/traversal check: `grid_id` is only ever accepted here after
# matching `_GRID_ID_RE` (no slash/"../" can match it), and the pseudo
# segment is already ASCII/underscore-only via `_slugify_pseudo` (reused
# as-is from the GRID_WORK section above — the same slugging rules apply
# to a player's own nickname whichever of the two stores it ends up in).
# ---------------------------------------------------------------------------

GRID_GAME_DIR = Path(__file__).resolve().parent.parent / "GRID_GAME"


def save_grid_game(grid_id, pseudo, user_letters, elapsed_seconds):
    """Saves (or updates) one player's own play state for `grid_id` — the
    letters they've typed so far (`user_letters`, a plain 2D list of
    strings, "" for a still-empty cell — the exact shape script.js's own
    `userLetters` already uses, so no reshaping is needed on either side of
    the wire) and the elapsed-time counter shown to the left of the grid's
    title. Returns True on success; False (a pure no-op, nothing written)
    if `grid_id` doesn't match the expected shape (see _GRID_ID_RE) or
    `pseudo` is blank — a game state is only ever saved "avec le nom de
    l'utilisateur", per the user's own explicit request, never for an
    anonymous player.

    `created_at` is preserved across updates (read from the existing file,
    if any, before it gets overwritten) the same way save_grid_work already
    does for its own record — so the very first time this grid was played
    stays known even after many later saves; only `updated_at` and the
    content itself change on every call after the first."""
    pseudo = (pseudo or "").strip()
    if not _GRID_ID_RE.match(grid_id) or not pseudo:
        return False
    directory = GRID_GAME_DIR / grid_id
    path = directory / f"{_slugify_pseudo(pseudo)}.json"
    created_at = None
    if path.is_file():
        try:
            with open(path, encoding="utf-8") as f:
                created_at = json.load(f).get("created_at")
        except (OSError, json.JSONDecodeError):
            created_at = None
    now = datetime.now().isoformat()
    record = {
        "grid_id": grid_id,
        "pseudo": pseudo,
        "user_letters": user_letters,
        "elapsed_seconds": max(0, int(elapsed_seconds or 0)),
        "created_at": created_at or now,
        "updated_at": now,
    }
    directory.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(record, ensure_ascii=False), encoding="utf-8")
    return True


def get_grid_game(grid_id, pseudo):
    """The saved play state for (`grid_id`, `pseudo`) — `user_letters` +
    `elapsed_seconds`, see save_grid_game above — or None if `grid_id`
    doesn't match the expected shape, `pseudo` is blank, or no matching
    file exists (this specific player never played this specific grid
    before, or never long enough to trigger an autosave)."""
    pseudo = (pseudo or "").strip()
    if not _GRID_ID_RE.match(grid_id) or not pseudo:
        return None
    path = GRID_GAME_DIR / grid_id / f"{_slugify_pseudo(pseudo)}.json"
    if not path.is_file():
        return None
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return None
