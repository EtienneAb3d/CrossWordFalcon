#!/usr/bin/env python3
"""One-off/scheduled script: reproduces, once a day, a small hand-picked
aggregation of French crossword sources — direct links to each
publisher's own stable "today's crossword" page — at the user's explicit
request: "Le journal ne doit pas scrapper grillesdujour.fr à chaque fois,
mais reproduire son fonctionnement maintenant qu'on connaît les URL des
pages donnant des mots croisés. L'URL grillesdujour.fr n'a donc pas de
raison d'être affiché dans la liste, on ne passe pas par lui au
quotidien." (See CLAUDE.md for the full trail of how these 21 direct
source URLs were themselves discovered and verified, replacing an even
earlier version of this script that queried grillesdujour.fr's own
WordPress REST API every day.)

**Every one of `SOURCES` below was individually re-verified with a real
fetch, at the user's own explicit follow-up request**: "Dans SCRAPP, il
faut scrapper les pages pour récupérer les informations utiles (notamment
le numéro de grille). Fais le pour chaque lien ajouté à la liste des URL
à scrapper pour vérifier qu'on peut bien récupérer l'info en automatique,
et éliminer les pages qui ne sont pas des proposition de grilles." Three
sources from the previous version were dropped after this check, each
for a concrete, confirmed reason (not a guess):
  - `franceinfo_classique` (the generic, no-slug "classique" landing
    page) genuinely **redirects to an unrelated page**
    (`https://www.franceinfo.fr/culture/musique/classique/`, France
    Info's own "classical music" section) — confirmed live, exactly the
    failure the user's own example named. The "Mini" variant of the same
    site (`franceinfo_mini` below) does NOT redirect and works correctly.
  - `cnews` returns Cloudflare's own bot-challenge interstitial ("Just a
    moment...") rather than real content — confirmed by reading the raw
    HTML directly, not merely a 403 status code alone (a 403 for a
    *different* reason wouldn't necessarily mean this).
  - `lebelage` is a client-side-rendered app (a real page shell, but its
    actual crossword content never appears in the raw HTML at all — this
    project's own fetch mechanism, a plain `httpx` GET, never executes
    JavaScript) — confirmed live by measuring the visible text extracted
    from the raw response (7 characters, versus thousands for every
    other source), not merely assumed from the URL alone.

For the 18 sources that remain, four expose a real, per-day grid number
that can genuinely be extracted automatically — confirmed live by
fetching each page and finding a real, human-readable number (not a CSS
hex color or another false positive — verified by reading the actual
surrounding text/markup, e.g. "Mots croisés #1272", "Grille n°1512"):
`franceinfo_mini`, `notretemps`, `telesept` (via their own visible page
text) and `rustica` (via its own embedded game iframe's `?id=YYMMDD`
query parameter, a date-encoded id rather than plain visible text). Each
of these four carries its own `extract` rule in `SOURCES` below, applied
by `_extract_number()`; the other 14 sources are kept with no `extract`
rule at all — their own crossword content was independently confirmed
live (real "mots croisés"/"crossword" text, or a real embedded game
iframe pointing at an actual game platform), they simply don't expose a
human-readable number the same way, and dropping a genuine, working
source purely for that reason would defeat the whole point of listing as
many real publishers as this project already went to the trouble of
verifying.

**A systematic sweep for a date (not only a grid number) was run once
more across every source without an `extract` rule yet**, at the user's
own later, explicit request: "Chaque fois que c'est possible, il faut
que le SCRAPP récupère l'information de la date et du numéro de grille.
Par exemple, c'est indiqué '<h3 class="date">Saturday, September 5th
</h3>' sur https://www.foxnews.com/games/daily-crossword-puzzle, mais
pas mentionné dans le fil Actu." Every remaining source's page was
fetched fresh and its visible text searched for a real date pattern
(day names + month names, per language) — two more genuine, safely-
extractable dates were found this way (`foxnews`, matching the user's
own exact example — "Saturday, September 5th"; `letelegramme`, "L'édition
numérique du 5 septembre 2026", the newspaper's own daily digital-edition
date rather than the crossword's own dedicated date, but a genuine,
reliable "today" signal all the same). Three further date-shaped matches
were found and *rejected* as false positives, confirmed by reading their
own surrounding context rather than taken at face value: `ledevoir`
("28 août 2026") and `tf1info` ("2/3 septembre 2026") both belonged to
an unrelated news article elsewhere on the same page (a general games/
news hub, not the crossword itself) — and neither date even matched the
actual fetch date, a second, independent tell that they were unrelated;
`weserkurier` ("21. Dezember 1913") was the crossword's own invention-
history trivia blurb, not a current date at all. No further genuinely
new extractable signal was found among the other 20+ sources checked
this same way — most simply never expose a date/number in their own
visible text at all, which the earlier check (see above) already
established doesn't disqualify them.

Called once a day by backend/app.py's own background scheduler (see
`_rss_daily_scheduler`) — but also runnable directly (`python3
scrapper/fetch_grid_links.py`) for a manual refresh or a first, one-off run."""

import json
import os
import re
from datetime import date, datetime, timezone
from html.parser import HTMLParser

import httpx

# A realistic browser header — needed for at least one source
# (notretemps.com), which blocks a too-bare request (403) but responds
# normally (200) once Accept/Accept-Language are present, confirmed live
# by a direct before/after comparison rather than assumed.
_REQUEST_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "fr-FR,fr;q=0.9",
}


class _VisibleTextExtractor(HTMLParser):
    """Extracts only the visible text of a page (never the content of
    <script>/<style>/<noscript>) — needed to search for a real,
    human-readable grid number without hitting a false positive (a CSS
    color code like #039 looks, on the surface, like a grid number if
    searching the raw HTML unfiltered)."""
    def __init__(self):
        super().__init__()
        self._skip_depth = 0
        self.chunks = []

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style", "noscript"):
            self._skip_depth += 1

    def handle_endtag(self, tag):
        if tag in ("script", "style", "noscript") and self._skip_depth > 0:
            self._skip_depth -= 1

    def handle_data(self, data):
        if self._skip_depth == 0:
            stripped = data.strip()
            if stripped:
                self.chunks.append(stripped)

    def text(self):
        return " | ".join(self.chunks)


def _extract_from_visible_text(html, pattern):
    """The number being searched for must appear in the page's actually
    visible text (see _VisibleTextExtractor) — never inside a <script>/
    <style> block, where a 2-6-digit number is far too often a false
    positive (a CSS color, a technical identifier). Returns the full
    tuple of groups captured by `pattern` (never just the first) — so
    rustica.fr, whose pattern captures 3 groups (day/month/year), and the
    3 other sources, which only capture one, share the same call
    interface inside fetch_all()."""
    parser = _VisibleTextExtractor()
    try:
        parser.feed(html)
    except Exception:
        return None
    match = re.search(pattern, parser.text(), re.IGNORECASE)
    return match.groups() if match else None


def _extract_from_iframe_src(html, pattern):
    """For rustica.fr: the number (a date encoded as YYMMDD) lives in the
    src attribute of an <iframe> pointing at the embedded game platform
    (rcijeux.fr), never in the visible text of the page itself. Same
    return contract as _extract_from_visible_text above (the full tuple
    of captured groups)."""
    iframes = re.findall(r'<iframe[^>]+src=["\']([^"\']+)["\']', html, re.IGNORECASE)
    for src in iframes:
        match = re.search(pattern, src)
        if match:
            return match.groups()
    return None


# Each source: a STABLE link (never a dated/unpredictable-id URL) to the
# page that shows this publisher's own current grid — verified live, one
# by one, before being added here (see the module docstring for the
# method and the 3 exclusions). "language" defaults to "fr" (fetch_all()
# applies this fallback via .get(), see below) — omitted on each of the
# 18 French-language sources below to avoid touching them unnecessarily;
# explicit ("en"/"de"/"it"/"es"/"pt") on non-French sources, or a list of
# codes for a multilingual source (e.g. WordsCroisés, "en"+"fr").
# "extract" (optional): (function, pattern, title_template) for sources
# whose real, current grid number can be automatically recovered — see
# the module docstring for how each one was confirmed live.
SOURCES = {
    "20minutes": {"name": "20 Minutes", "url": "https://www.20minutes.fr/services/jeux/mots-croises"},
    # "franceinfo_classique" (the generic "classique/" URL, no slug) is
    # deliberately absent here — see the module docstring: it really does
    # redirect to https://www.franceinfo.fr/culture/musique/classique/,
    # completely unrelated to crosswords, confirmed live. "cnews" and
    # "lebelage" are excluded for their own reasons, also detailed in the
    # docstring.
    "franceinfo_mini": {
        "name": "Franceinfo – Mini", "url": "https://jeux.franceinfo.fr/mots-croises/mini/",
        "extract": (_extract_from_visible_text, r'Mots croisés\s*#(\d{2,6})', "Franceinfo – Mini #{}"),
    },
    "isbooth": {"name": "Isbooth", "url": "https://isbooth.com/mots-croises-du-jour"},
    "lacroix": {"name": "La Croix", "url": "https://www.la-croix.com/mots-croises"},
    "lanouvellerepublique": {"name": "La Nouvelle République", "url": "https://www.lanouvellerepublique.fr/loisirs/jeux/mots-croises"},
    "larousse": {"name": "Larousse", "url": "https://jeux.larousse.fr/mots-croises-mini"},
    "canadafrancais": {"name": "Le Canada Français", "url": "https://www.canadafrancais.com/mots-croises/"},
    "ledevoir": {"name": "Le Devoir", "url": "https://www.ledevoir.com/jeux"},
    "leparisien": {"name": "Le Parisien", "url": "https://www.leparisien.fr/jeux/mots-croises"},
    "letelegramme": {
        "name": "Le Télégramme", "url": "https://www.letelegramme.fr/jeux/mots-croises/",
        # The date of the day's own digital edition ("L'édition numérique
        # du 5 septembre 2026"), confirmed live to genuinely be today,
        # not the crossword's own dedicated date (never found on this
        # page) — but a real, reliable date signal all the same, unlike
        # Le Devoir/TF1 Info (see below), whose only found dates belonged
        # to other, unrelated articles, not to today.
        "extract": (_extract_from_visible_text, r"édition numérique du\s*\|\s*(\d{1,2} \w+ \d{4})", "Le Télégramme – Édition du {}"),
    },
    "maximag": {"name": "Maximag", "url": "https://www.maxi-mag.fr/jeux/mots-croises"},
    "meteocity": {"name": "Météocity", "url": "https://jeux.meteocity.com/jeux/mots-croises-du-jour"},
    "notretemps": {
        "name": "Notre Temps", "url": "https://www.notretemps.com/jeux/jeux-en-ligne/mots-croises/",
        "extract": (_extract_from_visible_text, r'Grille\s*n[o°]\s*(\d{2,6})', "Notre Temps – Grille n°{}"),
    },
    "rustica": {
        "name": "Rustica", "url": "https://www.rustica.fr/jeux/mots-croises/1",
        # This "number" is actually a date encoded as YYMMDD (confirmed
        # live: "id=260905" for September 5, 2026), not a sequential
        # grid number like the 3 other sources — reformatted as a
        # readable date rather than shown as-is, which would wrongly
        # suggest a real grid number.
        "extract": (_extract_from_iframe_src, r'[?&]id=(\d{2})(\d{2})(\d{2})\b', "Rustica – Grille du {2}/{1}/20{0}"),
    },
    "sudouest": {"name": "Sud Ouest", "url": "https://www.sudouest.fr/jeux/mots-croises/"},
    "tf1info": {"name": "TF1 Info", "url": "https://www.tf1info.fr/jeux/mots-croises/"},
    "telesept": {
        "name": "Télé 7 Jours", "url": "https://www.programme-television.org/jeux/mots-croises",
        "extract": (_extract_from_visible_text, r'Grille\s*n[o°]\s*(\d{2,6})', "Télé 7 Jours – Grille n°{}"),
    },
    "telepro": {"name": "Télépro", "url": "https://www.telepro.be/jeux/mots-croises/"},

    # English-language sources, at the user's explicit request: "Ajoute
    # EN aussi (complémentaire des flux RSS)" ("Add EN too, complementary
    # to the RSS feeds") — the RSS feeds already in place
    # (fetch_rss_feeds.py) are blogs *about* crosswords (Rex Parker, Diary
    # of a Crossword Fiend), never a direct link to a playable grid; these
    # 4 sources fill that gap, verified live with the same method as for
    # French (see the module docstring). Two serious candidates were
    # rejected along the way: Washington Post (no connection at all
    # possible from this machine — HTTP 000/403 depending on the tool, a
    # genuine network failure, not assumed) and USA Today
    # (games.usatoday.com really does redirect to
    # "eu.usatoday.com/unsupported-eu/", a geographic block against
    # European visitors — exactly the same failure class as Franceinfo
    # Classique in French: a redirect to an unrelated page).
    "foxnews": {
        "name": "Fox News", "url": "https://www.foxnews.com/games/daily-crossword-puzzle", "language": "en",
        # The exact example given directly by the user: "<h3
        # class='date'>Saturday, September 5th</h3>" — confirmed live
        # (real visible text, genuinely matching today's date).
        "extract": (_extract_from_visible_text, r'((?:Sunday|Monday|Tuesday|Wednesday|Thursday|Friday|Saturday), \w+ \d{1,2}(?:st|nd|rd|th)?)', "Fox News – {}"),
    },
    "guardian": {
        "name": "The Guardian – Cryptic", "url": "https://www.theguardian.com/crosswords/series/cryptic",
        "language": "en",
        # An archive page listing several recent grids (like Notre
        # Temps/Télé 7 Jours in French) — the first entry is always the
        # most recent one, confirmed live.
        "extract": (_extract_from_visible_text, r'Cryptic crossword No ([\d,]+)', "The Guardian – Cryptic No {}"),
    },
    "bestcrosswords": {
        # URL corrected by the user: "Sur ce site, la page de mots
        # croisés du jour est en fait ici" ("On this site, the daily
        # crossword page is actually here") — the old URL (the site's own
        # homepage) wasn't wrong as such, just not the specific daily-
        # crossword page. Confirmed live (200, no redirect, real content),
        # and a genuine extractable date found along the way: "Puzzles
        # for Saturday, September 5, 2026".
        "name": "BestCrosswords", "url": "https://www.bestcrosswords.com/daily-crossword-puzzles", "language": "en",
        "extract": (_extract_from_visible_text, r'Puzzles for ((?:Sunday|Monday|Tuesday|Wednesday|Thursday|Friday|Saturday), \w+ \d{1,2}, \d{4})', "BestCrosswords – {}"),
    },
    "onlinecrosswords": {
        # URL corrected by the user, same note as for bestcrosswords.com:
        # the homepage wasn't the specific right page. Confirmed live
        # (200, no redirect). Number AND date both extractable here
        # together: "This is the online crossword puzzle #1 for Sep 5,
        # 2026." — 4 groups captured separately (number, month, day,
        # year) rather than one single date block, so as not to copy the
        # real double space found in the source text verbatim
        # ("Sep  5, 2026").
        "name": "OnlineCrosswords.net", "url": "https://www.onlinecrosswords.net/online-daily-crosswords-1.php", "language": "en",
        "extract": (
            _extract_from_visible_text,
            r"crossword puzzle\s*\|\s*#(\d+)\s*\|\s*for\s*\|\s*([A-Za-z]+)\s+(\d{1,2}),\s*(\d{4})",
            "OnlineCrosswords.net – #{} ({} {}, {})",
        ),
    },

    # Bilingual (English + French) source, added by the user. Every
    # puzzle is a single grid solved DOWN in English and ACROSS in
    # French, so "language" is a list: the entry appears under both the
    # EN and the FR filter (fetch_all() passes the value through as-is;
    # the web UI's own language filter accepts a string or a list).
    # Verified live: HTTP 200, no redirect, ~10 KB of real visible text,
    # heavy "crossword"/"mots croisés"/"bilingual"/"bilingue" content,
    # not JS-rendered. Puzzles are numbered and listed newest-first, so
    # the latest number is the first visible "Crossword / Mots croisés N"
    # heading.
    "wordscroises": {
        "name": "WordsCroisés",
        "url": "https://wordscroises.wordpress.com/puzzles-grilles/",
        "language": ["en", "fr"],
        "extract": (
            _extract_from_visible_text,
            r"Crossword\s*/\s*Mots croisés\s+(\d{1,4})",
            "WordsCroisés – Crossword / Mots croisés {}",
        ),
    },

    # German/Italian/Spanish sources, at the user's explicit request:
    # "Cherche des sites donnant des grilles quotidiennes en DE/IT/ES pour
    # les scrapper une fois par jour et donner un lien précis dans
    # l'actu." ("Find sites giving daily grids in DE/IT/ES to scrape once
    # a day and give a precise link in the news feed.") Researched via a
    # dedicated agent (WebSearch + WebFetch), then each one re-verified
    # live a second time via curl (same method, same realistic header, as
    # for the French/English sources) before being added here — never
    # added on the strength of the agent's own report alone. This second
    # verification actually corrected a false negative: the agent had
    # rejected eldiario.es, believing it redirected to a generic page, but
    # a direct fetch instead confirms a real crucigramas page ("Crucigramas
    # en Juegos elDiario.es", no redirect) — kept on the strength of this
    # independent verification rather than the initial report.
    # t-online.de does redirect, but to an almost-identical URL (same
    # numeric id, only the slug's own word order changed) — the final
    # (canonical) URL is the one used below, not the original one.
    "tonline": {"name": "T-Online", "url": "https://www.t-online.de/spiele/t-online-spiele/id_87469764/kniffliges-kreuzwortraetsel-kostenlos-taeglich-online-spielen.html", "language": "de"},
    "nzz": {"name": "NZZ", "url": "https://spiele.nzz.ch/kreuzwortraetsel/", "language": "de"},
    "rheinpfalz": {"name": "Die Rheinpfalz", "url": "https://www.rheinpfalz.de/spiele/kreuzwortraetsel.html", "language": "de"},
    "ruhrnachrichten": {"name": "Ruhr Nachrichten", "url": "https://www.ruhrnachrichten.de/spiele/kreuzwortraetsel/", "language": "de"},
    "weserkurier": {"name": "Weser-Kurier", "url": "https://www.weser-kurier.de/thema/kreuzwortraetsel-q83207/", "language": "de"},
    "focusde": {"name": "Focus", "url": "https://focus.arkadiumarena.com/games/taeglisches-kreuzwortraetsel/", "language": "de"},

    # IT: markedly weaker search results than for the other languages
    # (see CLAUDE.md) — the major Italian dailies either have no findable
    # stable cruciverba page (La Repubblica, Il Fatto Quotidiano), or are
    # paywalled (Corriere della Sera, La Settimana Enigmistica) — only
    # these 2 independent sites passed verification. iltuocruciverba.com
    # actually publishes every WEEK, not daily ("Ogni settimana
    # pubblichiamo un nuovo cruciverba" — "Every week we publish a new
    # crossword") — kept anyway: the page itself stays stable and real,
    # only its own update cadence differs from the other sources.
    "cruciverbalab": {
        # URL corrected by the user, same note as for bestcrosswords.com/
        # onlinecrosswords.net: the homepage wasn't the specific page for
        # the cruciverba itself. Confirmed live (200, no redirect), and a
        # genuine extractable date found along the way: "Cruciverba Lab |
        # 5 settembre 2026" (real visible text, genuinely matching
        # today).
        "name": "Cruciverba Lab", "url": "https://cruciverba-lab.it/cruciverba", "language": "it",
        "extract": (_extract_from_visible_text, r"Cruciverba Lab\s*\|\s*(\d{1,2} \w+ \d{4})", "Cruciverba Lab – {}"),
    },
    "iltuocruciverba": {"name": "Il Tuo Cruciverba", "url": "https://www.iltuocruciverba.com/cruciverba-online-gratis/", "language": "it"},

    "eldebate": {"name": "El Debate", "url": "https://www.eldebate.com/juegos/crucigrama/", "language": "es"},
    "lanacion": {"name": "La Nación", "url": "https://www.lanacion.com.ar/juegos/crucigrama/", "language": "es"},
    "eldiario": {"name": "elDiario.es", "url": "https://www.eldiario.es/juegos/game/crossword/", "language": "es"},

    # PT: only one source passed verification, out of ~15 candidates
    # tested live. No Portuguese daily is usable with this project's own
    # mechanism (plain httpx, no browser): Público returns a Cloudflare
    # anti-bot page (HTTP 202, "verify you're not a robot"); Record,
    # Sábado and Correio da Manhã (Medialivre group) only serve a section
    # page whose own grid is JS-rendered, with no grid or game iframe in
    # the raw HTML; Jornal de Notícias and Diário de Notícias return 404
    # on every /passatempos/palavras-cruzadas path tried; palavrascruzadas.
    # pt (author Paulo Freixinho's own site) is a library/shop organized
    # by series, with no stable "today's grid" URL. On the Brazilian
    # side: geniol.com.br is blocked by Cloudflare (a hard 403);
    # ojogos.com.br embeds a generic, reskinned foreign game, not a
    # Brazilian grid; sopalavrascruzadas.com.br has nothing recent left
    # (its latest grids are dated 2024); rachacuca.com.br only has a
    # small numbered archive (#1..#55, not a daily publication) and its
    # grid is JS-only. Cruzadas Clube, on the other hand, publishes a new,
    # dated grid every day (confirmed live: "Cruzadas clássicas 682",
    # "postado em 04/09/2026") — the category page is stable and lists
    # the most recent one first, hence the "extract" rule taking the
    # first visible number.
    "cruzadasclube": {
        "name": "Cruzadas Clube",
        "url": "https://cruzadasclube.com.br/jogo/categoria/id/1/n/cruzadas-classicas",
        "language": "pt",
        "extract": (_extract_from_visible_text, r"Cruzadas cl[aá]ssicas\s+(\d{2,5})", "Cruzadas Clube – Clássicas {}"),
    },
    "onlinecrosswords_pt": {
        # Added directly by the user: "Site Portugais à ajouter au SCAPP :
        # https://www.onlinecrosswords.net/br/online-daily-crosswords-1.php"
        # ("Portuguese site to add to SCRAPP: ...") — the Brazilian/
        # Portuguese variant of the English "onlinecrosswords" source
        # already present above, on the same site (OnlineCrosswords.net).
        # Confirmed live (200, no redirect, ~450 characters of real
        # visible text, genuinely Portuguese content: "Palavras Cruzadas
        # Online... Este é o puzzle #1 para Sep 6, 2026"). Number AND
        # date both extractable together here, exactly like the English
        # source (same site, same page structure, only "for" becomes
        # "para") — 4 groups captured separately (number, month, day,
        # year), same reason as the English version: so as not to copy
        # the real double space found in the source text verbatim
        # ("Sep  6, 2026").
        "name": "OnlineCrosswords.net (BR)",
        "url": "https://www.onlinecrosswords.net/br/online-daily-crosswords-1.php",
        "language": "pt",
        "extract": (
            _extract_from_visible_text,
            r"puzzle\s*\|\s*#(\d+)\s*\|\s*para\s*\|\s*([A-Za-z]+)\s+(\d{1,2}),\s*(\d{4})",
            "OnlineCrosswords.net (BR) – #{} ({} {}, {})",
        ),
    },
}

SCRAPP_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "SCRAPP")


def fetch_all():
    """Checks, for every source in SOURCES, that its stable URL still
    responds (best-effort, like every RSS feed taken individually in
    fetch_rss_feeds.py) — a source that fails is logged and simply
    skipped for this run, never left to interrupt the others. For
    sources carrying an "extract" rule, also tries to pull today's real
    grid number out of the page — an extraction failure (the page
    responds normally but the pattern isn't found, e.g. if the site's own
    format changed) is logged but doesn't exclude the source: it's still
    kept, with its own plain generic name as the title instead of a
    number that can no longer be guaranteed. Writes SCRAPP/combined.json
    (overwritten on every run, like RSS/combined.json) in the same
    {"fetched_at", "items"} shape already used by fetch_rss_feeds.py."""
    os.makedirs(SCRAPP_DIR, exist_ok=True)
    today = date.today().isoformat()
    combined = []
    with httpx.Client(timeout=20, follow_redirects=True, headers=_REQUEST_HEADERS) as client:
        for key, source in SOURCES.items():
            try:
                resp = client.get(source["url"])
                resp.raise_for_status()
            except httpx.HTTPError as e:
                print(f"[fetch_grid_links] {key}: echec de verification ({e})")
                continue
            title = source["name"]
            if "extract" in source:
                extractor, pattern, template = source["extract"]
                groups = extractor(resp.text, pattern)
                if groups:
                    title = template.format(*groups)
                else:
                    print(f"[fetch_grid_links] {key}: numero de grille introuvable (motif inchange ?)")
            combined.append({
                "source": source["name"],
                "language": source.get("language", "fr"),
                "title": title,
                "link": source["url"],
                "pub_date": f"{today}T00:00:00+00:00",
                "content_html": None,
                "kind": "grid",
            })

    combined.sort(key=lambda it: it["source"])
    combined_path = os.path.join(SCRAPP_DIR, "combined.json")
    with open(combined_path, "w", encoding="utf-8") as f:
        json.dump(
            {"fetched_at": datetime.now(timezone.utc).isoformat(), "items": combined},
            f, ensure_ascii=False, indent=2,
        )
    return combined


if __name__ == "__main__":
    items = fetch_all()
    print(f"{len(items)} sources verifiees et enregistrees dans {SCRAPP_DIR}/combined.json")
