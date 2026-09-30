"""Downloads the Scrabble word lists of the six supported languages from
the GitHub repository FlandersBurger/scrabble-dictionary into
`data/scrabble/<lang>/` (plus that repository's `LICENSE` and
`reference.json`). Idempotent: a file already on disk is kept unless
`--force` is given; given language codes, only those languages' lists
(plus the two shared files). Run from the repository root:

    python3 data_builder/download_scrabble_dictionaries.py [fr ...] [--force]
"""

import argparse
import sys
import urllib.request
from pathlib import Path

BASE_URL = "https://raw.githubusercontent.com/FlandersBurger/scrabble-dictionary/main"
OUT_DIR = Path(__file__).resolve().parent.parent / "data" / "scrabble"
FILES = [
    "fr/ods8.txt",
    "en/sowpods.txt",
    "en/twl.txt",
    "de/wordlist.txt",
    "es/file2017.txt",
    "es/fise2.txt",
    "it/zinga.txt",
    "pt/wordlist.txt",
    "LICENSE",
    "reference.json",
]


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("languages", nargs="*", help="langues à télécharger (toutes par défaut)")
    ap.add_argument("--force", action="store_true", help="re-télécharge les fichiers déjà présents")
    args = ap.parse_args()
    for rel in FILES:
        if args.languages and "/" in rel and rel.split("/")[0] not in args.languages:
            continue
        dest = OUT_DIR / rel
        if dest.exists() and not args.force:
            print(f"déjà présent : {dest}")
            continue
        dest.parent.mkdir(parents=True, exist_ok=True)
        tmp = dest.with_suffix(dest.suffix + ".part")
        with urllib.request.urlopen(f"{BASE_URL}/{rel}", timeout=120) as resp, open(tmp, "wb") as f:
            f.write(resp.read())
        tmp.replace(dest)
        print(f"téléchargé : {dest}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
