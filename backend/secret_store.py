"""Stocke les couples pseudo/mot secret permettant à un utilisateur de
prouver qu'un pseudo lui appartient, à la demande explicite de
l'utilisateur : "ajouter une entrée 'Mot secret' permettant à
l'utilisateur de prouver que le pseudo lui appartient... Stocker les
couples Pseudo/Secret dans le dossier SECRET."

Un fichier JSON par pseudo sous SECRET/ (racine du projet, gitignoré —
même convention que GRID_STORE/, GRID_WORK/, LOG_CHAT/, etc. : un
répertoire déclaré comme constante de module, créé paresseusement via
`mkdir(parents=True, exist_ok=True)` juste avant la première écriture,
jamais au chargement du module). Le fichier est nommé par le hachage
SHA-256 du pseudo lui-même (comparaison exacte, sensible à la casse —
la même convention que le reste du projet pour ce champ, ex. le filtre
"Mes grilles" de `GET /api/library`) plutôt qu'un slug, pour éviter tout
souci de caractères invalides dans un nom de fichier sans avoir à écrire
une fonction de slugification séparée rien que pour ça.

Le mot secret lui-même n'est jamais stocké en clair : haché avec un sel
aléatoire propre à chaque pseudo via `hashlib.pbkdf2_hmac` (uniquement la
bibliothèque standard, pas de nouvelle dépendance). Ce n'est pas un
mécanisme d'authentification à haute sécurité — juste "prouver qu'on est
bien le premier à avoir choisi ce pseudo" pour un jeu de mots croisés —
mais il n'y a aucune raison de stocker un secret en clair sur disque
quand le hachage ne coûte presque rien.
"""
import hashlib
import json
import os
import threading
import time
from pathlib import Path

SECRET_DIR = Path(__file__).resolve().parent.parent / "SECRET"

# Nombre d'itérations PBKDF2 — une valeur usuelle pour ce niveau
# d'enjeu (pas un coffre-fort bancaire, juste éviter le vol de pseudo
# entre deux joueurs), sans ralentir perceptiblement une requête.
_PBKDF2_ITERATIONS = 200_000
_SALT_BYTES = 16

# Protège la lecture-puis-écriture d'un même fichier pseudo contre une
# course entre deux requêtes simultanées qui tenteraient de revendiquer
# le même pseudo tout neuf en même temps — même principe que
# `_PRESENCE_LOCK` dans backend/app.py. Un seul verrou global suffit :
# ce mécanisme n'est jamais sur un chemin chaud (un appel par ouverture
# du panneau d'accueil, jamais par frappe de touche).
_SECRET_LOCK = threading.Lock()


def _pseudo_path(pseudo: str) -> Path:
    digest = hashlib.sha256(pseudo.encode("utf-8")).hexdigest()
    return SECRET_DIR / f"{digest}.json"


def _hash_secret(secret: str, salt: bytes) -> str:
    return hashlib.pbkdf2_hmac(
        "sha256", secret.encode("utf-8"), salt, _PBKDF2_ITERATIONS
    ).hex()


def verify_or_claim(pseudo: str, secret: str) -> bool:
    """Vérifie que `secret` correspond au mot secret déjà enregistré pour
    `pseudo` — ou, si ce pseudo n'a encore jamais été revendiqué,
    l'enregistre avec ce `secret` (première utilisation = revendication).

    Renvoie `True` dans les deux cas de succès (mot secret correct, ou
    pseudo tout juste revendiqué) ; `False` uniquement si `pseudo` existe
    déjà sous un mot secret différent — le seul cas où l'appelant doit
    refuser et garder le panneau ouvert."""
    path = _pseudo_path(pseudo)
    with _SECRET_LOCK:
        if path.exists():
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                salt = bytes.fromhex(data["salt"])
                stored_hash = data["secret_hash"]
            except (OSError, ValueError, KeyError):
                # Fichier corrompu/illisible : traité comme absent plutôt
                # que de bloquer indéfiniment ce pseudo — réécrit ci-dessous
                # comme une nouvelle revendication.
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
