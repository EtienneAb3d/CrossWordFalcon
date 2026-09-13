"""Stores pseudo/secret-word pairs letting a user prove a given pseudo
belongs to them — backing the "Mot secret" field on the welcome overlay.

One JSON file per pseudo under SECRET/ (project root, gitignored — same
convention as GRID_STORE/, GRID_WORK/, LOG_CHAT/, etc.: a directory
declared as a module constant, created lazily via
`mkdir(parents=True, exist_ok=True)` right before the first write, never
at module load time). The file is named by the SHA-256 hash of the
pseudo itself (an exact, case-sensitive comparison — the same convention
this project already uses for that field elsewhere, e.g. `GET /api/
library`'s own "Mes grilles" filter) rather than a slug, avoiding any
invalid-filename-character concern without writing a separate
slugification function just for this.

The secret word itself is never stored in plain text: hashed with a
random salt unique to each pseudo via `hashlib.pbkdf2_hmac` (standard
library only, no new dependency). This is not a high-security
authentication mechanism — just "prove you were genuinely the first to
pick this pseudo" for a crossword game — but there's no reason to store
a secret in plain text on disk when hashing costs almost nothing.
"""
import hashlib
import json
import os
import threading
import time
from pathlib import Path

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


def _pseudo_path(pseudo: str) -> Path:
    digest = hashlib.sha256(pseudo.encode("utf-8")).hexdigest()
    return SECRET_DIR / f"{digest}.json"


def _hash_secret(secret: str, salt: bytes) -> str:
    return hashlib.pbkdf2_hmac(
        "sha256", secret.encode("utf-8"), salt, _PBKDF2_ITERATIONS
    ).hex()


def verify_or_claim(pseudo: str, secret: str) -> bool:
    """Checks that `secret` matches the secret word already stored for
    `pseudo` — or, if that pseudo has never been claimed yet, stores it
    with this `secret` (first use = claim).

    Returns `True` on either success case (correct secret word, or a
    freshly claimed pseudo); `False` only if `pseudo` already exists
    under a different secret word — the one case where the caller
    should refuse and keep the overlay open."""
    path = _pseudo_path(pseudo)
    with _SECRET_LOCK:
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
