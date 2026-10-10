"""Stores pseudo/secret-word pairs letting a user prove a given pseudo
belongs to them — backing the "Mot secret" field on the welcome overlay.

One JSON file per pseudo under SECRET/ (project root, gitignored — same
convention as GRID_STORE/, GRID_WORK/, LOG_CHAT/, etc.: a directory
declared as a module constant, created lazily via
`mkdir(parents=True, exist_ok=True)` right before the first write, never
at module load time). The file is named `<slug>.json`, the slug being the
one every per-player save file uses (`grid_store._slugify_pseudo`: accents
stripped, lowercased, each run of non-alphanumeric characters turned into
"_"), so two pseudos sharing a save file ("Étienne"/"etienne", "Jean-Luc"/
"jean luc") are one and the same pseudo here too, claimed by whoever came
first. The record keeps the pseudo as first typed.

A pseudo is valid (`is_valid_pseudo`) when, NFC-normalized, it is made of
letters (A-Z, a-z, or one of them carrying accents: its NFD form is that
ASCII letter followed by combining marks only), digits, "-", "_" and inner
spaces, with at least one letter.

The secret word itself is never stored in plain text: hashed with a
random salt unique to each pseudo via `hashlib.pbkdf2_hmac` (standard
library only, no new dependency). This is not a high-security
authentication mechanism — just "prove you were genuinely the first to
pick this pseudo" for a crossword game — but there's no reason to store
a secret in plain text on disk when hashing costs almost nothing.

Files of the earlier layout (`<sha256 of the exact pseudo>.json`) are
moved to their slug name once per process, on first use
(`_migrate_legacy_files`): when several share a slug the earliest claim
keeps it and the others are renamed `<hash>.json.duplicate`; one whose
pseudo has an empty slug is left as it is (no valid pseudo can reach it).
"""
import hashlib
import json
import os
import re
import threading
import time
import unicodedata
from pathlib import Path

from .grid_store import _slugify_pseudo

SECRET_DIR = Path(__file__).resolve().parent.parent / "SECRET"

# PBKDF2 iteration count — a reasonable value for this level of stakes
# (not a bank vault, just avoiding pseudo theft between two players),
# without perceptibly slowing down a request.
_PBKDF2_ITERATIONS = 200_000
_SALT_BYTES = 16

# Protects a read-then-write of the same pseudo file against a race
# between two simultaneous requests both trying to claim the same
# brand-new pseudo at once — same principle as `_PRESENCE_LOCK` in
# backend/app.py. A single global lock is enough: this mechanism is
# never on a hot path (one call per welcome-overlay open, never per
# keystroke).
_SECRET_LOCK = threading.Lock()

_LEGACY_NAME_RE = re.compile(r"^[0-9a-f]{64}\.json$")
_PLAIN_PSEUDO_CHARS = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_ ")
_legacy_migrated = False


def _is_pseudo_letter(c: str) -> bool:
    decomposed = unicodedata.normalize("NFD", c)
    return (
        ("a" <= decomposed[0] <= "z" or "A" <= decomposed[0] <= "Z")
        and all(unicodedata.category(m) == "Mn" for m in decomposed[1:])
    )


def is_valid_pseudo(pseudo: str) -> bool:
    """True when `pseudo` (already stripped) holds only letters, accented
    or not, digits, "-", "_" and inner spaces, with at least one letter."""
    pseudo = unicodedata.normalize("NFC", pseudo)
    if not pseudo or pseudo != pseudo.strip(" "):
        return False
    has_letter = False
    for c in pseudo:
        if _is_pseudo_letter(c):
            has_letter = True
        elif c not in _PLAIN_PSEUDO_CHARS:
            return False
    return has_letter


def _pseudo_path(pseudo: str) -> Path:
    return SECRET_DIR / f"{_slugify_pseudo(pseudo)}.json"


def _has_slug(pseudo: str) -> bool:
    """False when `pseudo` has no ASCII letter or digit once accents are
    stripped, i.e. when `_slugify_pseudo` would fall back on "anonyme"."""
    ascii_pseudo = unicodedata.normalize("NFKD", pseudo).encode("ascii", "ignore").decode("ascii")
    return any(c.isascii() and c.isalnum() for c in ascii_pseudo)


def _migrate_legacy_files():
    global _legacy_migrated
    if _legacy_migrated:
        return
    _legacy_migrated = True
    if not SECRET_DIR.is_dir():
        return
    records = []
    for path in SECRET_DIR.iterdir():
        if not _LEGACY_NAME_RE.match(path.name):
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            pseudo = data["pseudo"]
        except (OSError, ValueError, KeyError, TypeError):
            continue
        if not isinstance(pseudo, str) or not _has_slug(pseudo):
            continue
        records.append((data.get("created_at") or 0, path.name, path, _slugify_pseudo(pseudo)))
    for _, _, path, slug in sorted(records):
        target = SECRET_DIR / f"{slug}.json"
        try:
            if target.exists():
                path.rename(path.with_name(path.name + ".duplicate"))
            else:
                path.rename(target)
        except OSError:
            pass


def _hash_secret(secret: str, salt: bytes) -> str:
    return hashlib.pbkdf2_hmac(
        "sha256", secret.encode("utf-8"), salt, _PBKDF2_ITERATIONS
    ).hex()


def verify_or_claim(pseudo: str, secret: str) -> bool:
    """Checks that `secret` matches the secret word already stored for
    `pseudo`'s slug — or, if no pseudo with that slug has been claimed
    yet, stores it with this `secret` (first use = claim).

    Returns `True` on either success case (correct secret word, or a
    freshly claimed pseudo); `False` only if the slug already exists
    under a different secret word — the one case where the caller
    should refuse and keep the overlay open."""
    path = _pseudo_path(pseudo)
    with _SECRET_LOCK:
        _migrate_legacy_files()
        if path.exists():
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                salt = bytes.fromhex(data["salt"])
                stored_hash = data["secret_hash"]
            except (OSError, ValueError, KeyError):
                # Corrupted/unreadable file: treated as absent rather
                # than permanently locking out this pseudo — rewritten
                # below as a fresh claim.
                pass
            else:
                return _hash_secret(secret, salt) == stored_hash
        salt = os.urandom(_SALT_BYTES)
        record = {
            "pseudo": pseudo,
            "salt": salt.hex(),
            "secret_hash": _hash_secret(secret, salt),
            "created_at": time.time(),
        }
        SECRET_DIR.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(record), encoding="utf-8")
        return True
