"""Per-user profiles: `USER_PROFILES/<pseudo slug>/`.

Each user's folder holds `preferences.json` (the order, on/off state and
colour of the user's dictionaries and of the two Falcon glossaries, the
Scrabble dictionary and the others), every uploaded
word file as sent (`upload_<id><ext>`), its converted form (`dict_<id>.tsv`,
`MOT<TAB>ACCENTUE<TAB>SCORE`: grid form, `;`-separated spellings with the
main one first, Falcon usage score 0-5) and, per language, the merged
lexicon of the enabled dictionaries the grid engine reads
(`lexicon_<lang>_<hash>.tsv`, `MOT<TAB>ACCENTUE<TAB>CANONIQUE<TAB>SCORE<TAB>
RANK<TAB>ID`, see `lexicon_spec`).

The glossary order is: "Mots Défi", theme glossary (both fixed, outside
these preferences), then the reorderable list — user dictionaries, the
Scrabble dictionary (`SCRABBLE_ID`) and the other words of the Falcon
lexicon (`OTHERS_ID`: the corpus-statistics wordlist and the Wiktionary
expressions) — each of which can be disabled, at least one staying
enabled.

Java mirror: `falcon.UserProfiles`.
"""

import hashlib
import json
import math
import os
import re
import secrets
import threading
import time
import unicodedata
from pathlib import Path

from .grid_store import _slugify_pseudo

PROFILE_DIR = Path(__file__).resolve().parent.parent / "USER_PROFILES"
PREFERENCES_FILE = "preferences.json"
# The two glossaries of the Falcon lexicon, in their default order.
SCRABBLE_ID = "scrabble"
OTHERS_ID = "others"
FALCON_IDS = (SCRABBLE_ID, OTHERS_ID)
# The former single Falcon-dictionary entry, read as both.
_LEGACY_FALCON_ID = "falcon"
# Largest word file accepted, in bytes.
MAX_UPLOAD_BYTES = 20 * 1024 * 1024
# Longest grid form kept (the largest grid side).
MAX_WORD_LENGTH = 50
# Share (%) of the file's non-empty lines that must hold a recognisable
# entry: below it, the format is reported as not recognised.
MIN_RECOGNISED_PERCENT = 50
# Falcon usage score of an entry of a file with no scores.
UNSCORED_USAGE = 5
SUPPORTED_LANGUAGES = ("fr", "en", "de", "es", "it", "pt")

# The two fixed glossaries ahead of the reorderable list, which only carry
# a display colour.
FIXED_IDS = ("challenge", "theme")
_COLOR_RE = re.compile(r"^#[0-9a-f]{6}$")

_LOCK = threading.Lock()
_ID_RE = re.compile(r"^d[0-9a-f]{12}$")
_EXT_RE = re.compile(r"^\.[A-Za-z0-9]{1,8}$")
# One scored entry: the entry, a separator, a number (Crossfire .dict and
# Crossword Compiler .txt: `word;50`).
_SCORED_RE = re.compile(r"^\s*(.+?)\s*[;\t,:|]\s*([0-9]+(?:\.[0-9]+)?)\s*$")
# Punctuation splitting the entries of a file with no scores.
_SPLIT_RE = re.compile(r"[;,\t|/:.!?()\[\]{}\"«»…=+*#<>\\“”]+")
_APOSTROPHES = "'’"
_LIGATURE_FOLD = str.maketrans({"œ": "oe", "Œ": "OE", "æ": "ae", "Æ": "AE"})


class WordlistUnrecognised(ValueError):
    """The uploaded file holds no clearly identifiable words."""


def profile_dir(pseudo):
    return PROFILE_DIR / _slugify_pseudo(pseudo)


def _usage_of(score):
    """Falcon usage score (0-5) of a word-list score: >= 50 gives 5, >= 40
    gives 4, and so on down to 0."""
    return max(0, min(5, int(math.floor(score / 10))))


def _clean_entry(text):
    """The entry `text` with every character other than a letter, a space,
    a hyphen or an apostrophe removed, its spaces collapsed and its
    separators trimmed off both ends."""
    kept = "".join(
        c if c.isalpha() or c in "-" + _APOSTROPHES else " " if c.isspace() else ""
        for c in text
    )
    kept = re.sub(" +", " ", kept)
    return kept.strip(" -" + _APOSTROPHES)


def grid_form(spelling):
    """A-Z grid form of `spelling` (ligatures folded, accents dropped,
    uppercase, separators removed), `None` when one of its letters has no
    A-Z form."""
    out = []
    for c in spelling.translate(_LIGATURE_FOLD):
        if not c.isalpha():
            continue
        base = "".join(d for d in unicodedata.normalize("NFKD", c) if not unicodedata.combining(d))
        up = base.upper()
        if not up or any(not ("A" <= x <= "Z") for x in up):
            return None
        out.append(up)
    return "".join(out)


def parse_wordlist(data):
    """`(entries, scored)` of an uploaded word file: `entries` a list of
    `(MOT, [spelling, ...], usage)` in file order, one per grid form (its
    spellings in order of appearance, its highest usage), `scored` whether
    the file carries scores.

    A file whose lines are mostly `entry<sep>number` (`;`, tab, `,`, `:` or
    `|`) is a scored list (Crossfire .dict, Crossword Compiler .txt): each
    score becomes a Falcon usage score (`_usage_of`). Otherwise every line
    is split on punctuation and each piece is an entry, scored
    `UNSCORED_USAGE`. An entry keeps letters, spaces, hyphens and
    apostrophes only (multi-word expressions and compounds allowed), and
    is kept when its grid form has 2 to `MAX_WORD_LENGTH` letters.
    Raises `WordlistUnrecognised` when fewer than `MIN_RECOGNISED_PERCENT` %
    of the non-empty lines give an entry."""
    if b"\x00" in data:
        raise WordlistUnrecognised("binary file")
    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError:
        text = data.decode("latin-1")
    lines = [line for line in text.splitlines() if line.strip()]
    if not lines:
        raise WordlistUnrecognised("empty file")
    matches = [_SCORED_RE.match(line) for line in lines]
    scored = sum(1 for m in matches if m) * 100 >= len(lines) * MIN_RECOGNISED_PERCENT
    found = {}
    order = []
    recognised = 0

    def add(raw, usage):
        spelling = _clean_entry(raw)
        mot = grid_form(spelling) if spelling else None
        if not mot or len(mot) < 2 or len(mot) > MAX_WORD_LENGTH:
            return False
        entry = found.get(mot)
        if entry is None:
            found[mot] = entry = [[], usage]
            order.append(mot)
        if spelling not in entry[0]:
            entry[0].append(spelling)
        entry[1] = max(entry[1], usage)
        return True

    for line, m in zip(lines, matches):
        if scored:
            if m and add(m.group(1), _usage_of(float(m.group(2)))):
                recognised += 1
            continue
        hit = False
        for piece in _SPLIT_RE.split(line):
            if add(piece, UNSCORED_USAGE):
                hit = True
        if hit:
            recognised += 1
    if not order or recognised * 100 < len(lines) * MIN_RECOGNISED_PERCENT:
        raise WordlistUnrecognised("no clearly identifiable words")
    return [(mot, found[mot][0], found[mot][1]) for mot in order], scored


def _read_json(path):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def _write_json(path, value):
    tmp = path.with_name(path.name + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(value, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


def _dict_path(folder, dict_id):
    return folder / f"dict_{dict_id}.tsv"


def _normalized(folder, prefs):
    """The reorderable glossary list of `prefs`: every user dictionary
    whose converted file exists, plus the Scrabble dictionary and the
    others (appended last, in that order, when missing; a former "falcon"
    entry stands for both, its colour going to the Scrabble one); at least
    one item enabled (both Falcon glossaries are re-enabled otherwise);
    `colors` the display colours of the fixed glossaries ("Mots Défi",
    theme). A colour is `#rrggbb` (lowercase), an item without one keeps
    the interface's default colour."""
    items = []
    seen = set()
    for item in (prefs or {}).get("glossaries") or ():
        if not isinstance(item, dict):
            continue
        gid = item.get("id")
        if gid in seen:
            continue
        if gid == _LEGACY_FALCON_ID:
            for fid in FALCON_IDS:
                if fid not in seen:
                    items.append({"id": fid, "enabled": bool(item.get("enabled", True))})
                    if fid == SCRABBLE_ID and _valid_color(item.get("color")):
                        items[-1]["color"] = item["color"]
                    seen.add(fid)
            continue
        if gid in FALCON_IDS:
            items.append({"id": gid, "enabled": bool(item.get("enabled", True))})
            if _valid_color(item.get("color")):
                items[-1]["color"] = item["color"]
        elif isinstance(gid, str) and _ID_RE.match(gid) and _dict_path(folder, gid).exists():
            items.append({
                "id": gid,
                "enabled": bool(item.get("enabled", True)),
                "name": str(item.get("name") or gid),
                "language": item.get("language") if item.get("language") in SUPPORTED_LANGUAGES else "fr",
                "entries": int(item.get("entries") or 0),
                "scored": bool(item.get("scored")),
                "uploaded_at": str(item.get("uploaded_at") or ""),
            })
            if _valid_color(item.get("color")):
                items[-1]["color"] = item["color"]
        else:
            continue
        seen.add(gid)
    for fid in FALCON_IDS:
        if fid not in seen:
            items.append({"id": fid, "enabled": True})
    if not any(item["enabled"] for item in items):
        for item in items:
            if item["id"] in FALCON_IDS:
                item["enabled"] = True
    colors = (prefs or {}).get("colors")
    colors = {k: v for k, v in colors.items() if k in FIXED_IDS and _valid_color(v)} \
        if isinstance(colors, dict) else {}
    return {"glossaries": items, "colors": colors}


def _valid_color(value):
    return isinstance(value, str) and bool(_COLOR_RE.match(value))


def load_preferences(pseudo):
    """The user's preferences (`_normalized`), defaults when none saved."""
    folder = profile_dir(pseudo)
    return _normalized(folder, _read_json(folder / PREFERENCES_FILE))


def save_preferences(pseudo, glossaries, colors=None):
    """Stores the order, on/off state and colour of the user's glossaries
    (`[{"id", "enabled", "color"}, ...]`, the reorderable list) and the
    colours of the fixed ones (`colors`, {"challenge"|"theme": colour});
    unknown ids are dropped, missing ones appended in their former order,
    an invalid colour removes the item's own. Returns the saved
    preferences; `None` when every item would be disabled."""
    with _LOCK:
        folder = profile_dir(pseudo)
        saved = _normalized(folder, _read_json(folder / PREFERENCES_FILE))
        current = saved["glossaries"]
        by_id = {item["id"]: item for item in current}
        ordered = []
        for item in glossaries or ():
            gid = item.get("id") if isinstance(item, dict) else None
            if gid in by_id and all(o["id"] != gid for o in ordered):
                entry = dict(by_id[gid], enabled=bool(item.get("enabled", True)))
                entry.pop("color", None)
                if _valid_color(item.get("color")):
                    entry["color"] = item["color"]
                ordered.append(entry)
        ordered += [item for item in current if all(o["id"] != item["id"] for o in ordered)]
        if not any(item["enabled"] for item in ordered):
            return None
        folder.mkdir(parents=True, exist_ok=True)
        if isinstance(colors, dict):
            fixed = {k: v for k, v in colors.items() if k in FIXED_IDS and _valid_color(v)}
        else:
            fixed = saved["colors"]
        prefs = {"glossaries": ordered, "colors": fixed}
        _write_json(folder / PREFERENCES_FILE, prefs)
        return prefs


def add_dictionary(pseudo, filename, data, language):
    """Stores an uploaded word file in the user's folder (as sent, and
    converted, `parse_wordlist`) and adds it, enabled, right before the
    first Falcon glossary in the preferences. Returns `(preferences, entry)`;
    raises `WordlistUnrecognised`."""
    entries, scored = parse_wordlist(data)
    language = language if language in SUPPORTED_LANGUAGES else "fr"
    name = os.path.basename(str(filename or "")).strip() or "dictionnaire"
    ext = os.path.splitext(name)[1]
    with _LOCK:
        folder = profile_dir(pseudo)
        folder.mkdir(parents=True, exist_ok=True)
        dict_id = "d" + secrets.token_hex(6)
        (folder / f"upload_{dict_id}{ext if _EXT_RE.match(ext) else ''}").write_bytes(data)
        tmp = folder / f"dict_{dict_id}.tsv.tmp"
        with open(tmp, "w", encoding="utf-8", newline="\n") as f:
            for mot, spellings, usage in entries:
                f.write(f"{mot}\t{';'.join(spellings)}\t{usage}\n")
        os.replace(tmp, _dict_path(folder, dict_id))
        prefs = _normalized(folder, _read_json(folder / PREFERENCES_FILE))
        item = {
            "id": dict_id, "enabled": True, "name": name[:120], "language": language,
            "entries": len(entries), "scored": scored,
            "uploaded_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        }
        items = prefs["glossaries"]
        at = next(i for i, it in enumerate(items) if it["id"] in FALCON_IDS)
        items.insert(at, item)
        _write_json(folder / PREFERENCES_FILE, prefs)
        return prefs, item


def delete_dictionary(pseudo, dict_id):
    """Removes one user dictionary (its files and its preferences entry).
    Returns the preferences, `None` when it does not exist."""
    if not isinstance(dict_id, str) or not _ID_RE.match(dict_id):
        return None
    with _LOCK:
        folder = profile_dir(pseudo)
        if not _dict_path(folder, dict_id).exists():
            return None
        for path in folder.glob(f"*_{dict_id}*"):
            try:
                path.unlink()
            except OSError:
                pass
        prefs = _normalized(folder, _read_json(folder / PREFERENCES_FILE))
        _write_json(folder / PREFERENCES_FILE, prefs)
        return prefs


def lexicon_spec(pseudo, language):
    """`(merged lexicon path, Scrabble rank, others rank, ranked ids)` the
    grid engine merges into `language`'s lexicon for `pseudo`
    (`crossword_gen.load_lexicon`'s `user`), `None` when the player's
    glossaries leave that language's lexicon as it is by default (no
    enabled user dictionary in it, both Falcon glossaries enabled, the
    Scrabble one first) or when no enabled item applies to it.

    The ranked glossaries are the enabled items of the reorderable list
    that apply to `language` (its user dictionaries, and the two Falcon
    glossaries); a rank is a position in that list (`ranked ids`, a tuple),
    a disabled Falcon glossary's rank `None`. The merged file lists every
    word of those user dictionaries once, from the highest-ranked one
    holding it: `MOT<TAB>ACCENTUE<TAB>CANONIQUE<TAB>SCORE<TAB>RANK<TAB>ID`
    (CANONIQUE: the main spelling, ID: the dictionary's id). Its name
    carries a hash of the ranked list, so it is written once and never
    rewritten."""
    if not pseudo or not language:
        return None
    folder = profile_dir(pseudo)
    prefs = _normalized(folder, _read_json(folder / PREFERENCES_FILE))
    ranked = [
        item for item in prefs["glossaries"]
        if item["enabled"] and (item["id"] in FALCON_IDS or item.get("language") == language)
    ]
    ids = tuple(item["id"] for item in ranked)
    if not ids or ids == FALCON_IDS:
        return None
    scrabble_rank = ids.index(SCRABBLE_ID) if SCRABBLE_ID in ids else None
    others_rank = ids.index(OTHERS_ID) if OTHERS_ID in ids else None
    digest = hashlib.sha256("\n".join(ids).encode("utf-8")).hexdigest()[:16]
    path = folder / f"lexicon_{language}_{digest}.tsv"
    with _LOCK:
        if not path.exists():
            folder.mkdir(parents=True, exist_ok=True)
            seen = set()
            tmp = path.with_name(path.name + ".tmp")
            with open(tmp, "w", encoding="utf-8", newline="\n") as out:
                for rank, item in enumerate(ranked):
                    if item["id"] in FALCON_IDS:
                        continue
                    try:
                        src = open(_dict_path(folder, item["id"]), encoding="utf-8")
                    except OSError:
                        continue
                    with src:
                        for line in src:
                            parts = line.rstrip("\n").split("\t")
                            if len(parts) < 3 or parts[0] in seen:
                                continue
                            seen.add(parts[0])
                            main = parts[1].split(";")[0] or parts[0]
                            out.write(
                                f"{parts[0]}\t{parts[1]}\t{main}\t{parts[2]}\t{rank}\t{item['id']}\n"
                            )
            os.replace(tmp, path)
            for old in folder.glob(f"lexicon_{language}_*.tsv"):
                if old != path:
                    try:
                        old.unlink()
                    except OSError:
                        pass
    return (str(path), scrabble_rank, others_rank, ids)


def lexicon_specs(pseudo, language, bilingual_language=None):
    """`(across spec, down spec)` of a grid (`lexicon_spec` per language;
    the same for both directions on a monolingual grid)."""
    across = lexicon_spec(pseudo, language)
    if bilingual_language and bilingual_language != language:
        return across, lexicon_spec(pseudo, bilingual_language)
    return across, across
