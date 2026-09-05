#!/usr/bin/env python3
"""One-off/scheduled script: downloads a small, hand-picked set of
crossword-specific RSS feeds and saves them, at the user's explicit
request: "Configure un demon qui lit tous ces flux RSS une fois par jour
(par exemple, le matin à 8H, et sauvegarde chaque flux RSS dans un dossier
RSS (écrasé chaque jour)."

`RSS_FEEDS` only lists sources individually verified live, by a real HTTP
fetch, right before being added — not merely picked from a generic
"puzzle" RSS directory. That mattered here: the first broad candidate
list (pulled from general-purpose puzzle-RSS listing sites) turned out to
mix in unrelated content once actually checked — the user explicitly
reported "il y a aussi des flux Jeux d'Echec mélangés aux listes
fournies" (chess feeds mixed into the very listings this list was drawn
from) — and, separately, 3 of the first 4 individual blog candidates
tried turned out to be dead/archived or broken on direct inspection: one
blog's own most recent post read "archival blog... it's been 15 years";
another's most recent post was literally titled "Concluding Thoughts"
("Sally's Final Takes"); a third's feed URL 302-redirected to a URL that
came back with zero items. Only two survived this check, both confirmed
live and unambiguously, exclusively about crosswords (post titles are
literally full of crossword clue text, dated up to the very day they were
checked):
  - Rex Parker Does the NYT Crossword Puzzle
  - Diary of a Crossword Fiend (the successor site of a blog that had
    itself moved off the URL a generic directory listing pointed at)

Called once a day by backend/app.py's own background scheduler (see
`_rss_daily_scheduler`) — but also runnable directly (`python3
fetch_rss_feeds.py`) for a manual refresh or a first, one-off run."""

import json
import os
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime

import httpx

# Namespace WordPress/Blogger both use for a richer <content:encoded> body
# (see fetch_all() below) — a plain <description> is often just a short
# excerpt, <content:encoded> (when present) is the full post.
_CONTENT_NS = "{http://purl.org/rss/1.0/modules/content/}encoded"

# "language" — one of this app's own 5 supported UI/puzzle languages
# (fr/en/de/es/it) — powers the web UI's own per-language filter on the
# "Actu Croisée" panel, at the user's explicit request: "afficher un
# sélecteur permettant de ne voir que les flux RSS dans une des langues de
# l'appli." Both feeds verified so far happen to be English-language
# sources (no French/German/Spanish/Italian crossword-specific feed has
# been found and verified yet) — this is honestly just where the search
# stopped, not a deliberate English-only design; adding a feed in another
# supported language later needs nothing more than one more entry here.
RSS_FEEDS = {
    "rexwordpuzzle": {
        "name": "Rex Parker Does the NYT Crossword Puzzle",
        "url": "https://rexwordpuzzle.blogspot.com/feeds/posts/default?alt=rss",
        "language": "en",
    },
    "crosswordfiend": {
        "name": "Diary of a Crossword Fiend",
        "url": "https://crosswordfiend.com/feed/",
        "language": "en",
    },
}

RSS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "RSS")


def _parse_pub_date(raw):
    """`email.utils.parsedate_to_datetime` handles every RFC 822-ish
    pubDate format actually seen from these two feeds — returns `None`
    (never raises) for anything malformed, so a single bad date can't
    crash the whole run, just leaves that one item unsortable (sorted
    last, see fetch_all's own sort key)."""
    if not raw:
        return None
    try:
        return parsedate_to_datetime(raw)
    except (TypeError, ValueError):
        return None


def fetch_all():
    """Downloads every feed in RSS_FEEDS, saves each one's raw XML under
    RSS/<key>.xml (overwritten every call, per the user's own "écrasé
    chaque jour" — RSS/ itself is gitignored, a generated artifact like
    every other data cache in this project, never source content), and
    writes a single RSS/combined.json — every item from every feed,
    sorted by publication date descending (most recent first) — for the
    web UI's own panel to read directly with no XML parsing needed
    client-side. Returns the combined list.

    Best-effort per feed: a single feed failing to download or parse is
    logged (stdout) and skipped, never aborts the whole run — the panel
    should still show whatever did succeed rather than nothing at all."""
    os.makedirs(RSS_DIR, exist_ok=True)
    combined = []
    with httpx.Client(
        timeout=20, follow_redirects=True,
        headers={"User-Agent": "CrossWordFalcon/1.0 (+https://github.com/; RSS reader)"},
    ) as client:
        for key, feed in RSS_FEEDS.items():
            try:
                resp = client.get(feed["url"])
                resp.raise_for_status()
            except httpx.HTTPError as e:
                print(f"[fetch_rss_feeds] {key}: echec du telechargement ({e})")
                continue
            raw_path = os.path.join(RSS_DIR, f"{key}.xml")
            with open(raw_path, "wb") as f:
                f.write(resp.content)
            try:
                root = ET.fromstring(resp.content)
            except ET.ParseError as e:
                print(f"[fetch_rss_feeds] {key}: XML invalide ({e})")
                continue
            for item in root.findall(".//item"):
                title = item.findtext("title") or "(sans titre)"
                link = item.findtext("link") or ""
                pub_raw = item.findtext("pubDate")
                pub_dt = _parse_pub_date(pub_raw)
                description = item.findtext("description") or ""
                content_encoded = item.findtext(_CONTENT_NS)
                combined.append({
                    "source": feed["name"],
                    "language": feed.get("language", "en"),
                    "title": title,
                    "link": link,
                    "pub_date": pub_dt.isoformat() if pub_dt else None,
                    "content_html": content_encoded or description,
                    # Discriminant ajouté à la demande de l'utilisateur pour
                    # fusionner ce journal avec celui de fetch_grid_links.py
                    # (SCRAPP/) dans le même panneau "Actu Croisée" — un clic
                    # sur une entrée "rss" ouvre l'aperçu interne existant,
                    # un clic sur une entrée "grid" (voir ce module) ouvre
                    # directement l'URL externe, sans aperçu du tout.
                    "kind": "rss",
                })
    # Un item sans date valide (pub_date=None) trie en dernier (chaine
    # vide < toute vraie date ISO 8601) plutot que de faire planter le tri
    # ou de finir arbitrairement en tete.
    combined.sort(key=lambda it: it["pub_date"] or "", reverse=True)
    combined_path = os.path.join(RSS_DIR, "combined.json")
    with open(combined_path, "w", encoding="utf-8") as f:
        json.dump(
            {"fetched_at": datetime.now(timezone.utc).isoformat(), "items": combined},
            f, ensure_ascii=False, indent=2,
        )
    return combined


if __name__ == "__main__":
    items = fetch_all()
    print(f"{len(items)} articles enregistres dans {RSS_DIR}/combined.json")
