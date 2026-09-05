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
fetch_grid_links.py`) for a manual refresh or a first, one-off run."""

import json
import os
import re
from datetime import date, datetime, timezone
from html.parser import HTMLParser

import httpx

# Un en-tête réaliste de navigateur — nécessaire pour au moins une source
# (notretemps.com) qui bloque une requête trop nue (403) mais répond
# normalement (200) une fois Accept/Accept-Language présents, vérifié en
# direct par comparaison avant/après plutôt que supposé.
_REQUEST_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "fr-FR,fr;q=0.9",
}


class _VisibleTextExtractor(HTMLParser):
    """Extrait uniquement le texte visible d'une page (jamais le contenu
    de <script>/<style>/<noscript>) — nécessaire pour chercher un vrai
    numéro de grille lu par un humain sans tomber sur un faux positif
    (un code couleur CSS de la forme #039 ressemble, en apparence, à un
    numéro de grille si on cherche dans le HTML brut sans filtrer)."""
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
    """Le numéro cherché doit apparaître dans le texte réellement visible
    de la page (voir _VisibleTextExtractor) — jamais dans un <script>/
    <style>, où un nombre à 2-6 chiffres est bien trop souvent un faux
    positif (couleur CSS, identifiant technique). Renvoie le tuple complet
    des groupes captés par `pattern` (jamais seulement le premier) — pour
    que rustica.fr, dont le motif capture 3 groupes (jour/mois/année), et
    les 3 autres sources, qui n'en capturent qu'un, partagent la même
    interface d'appel dans fetch_all()."""
    parser = _VisibleTextExtractor()
    try:
        parser.feed(html)
    except Exception:
        return None
    match = re.search(pattern, parser.text(), re.IGNORECASE)
    return match.groups() if match else None


def _extract_from_iframe_src(html, pattern):
    """Pour rustica.fr : le numéro (une date encodée AAMMJJ) vit dans
    l'attribut src d'un <iframe> pointant vers la plateforme de jeu
    embarquée (rcijeux.fr), jamais dans le texte visible de la page
    elle-même. Même contrat de retour que _extract_from_visible_text
    ci-dessus (le tuple complet des groupes captés)."""
    iframes = re.findall(r'<iframe[^>]+src=["\']([^"\']+)["\']', html, re.IGNORECASE)
    for src in iframes:
        match = re.search(pattern, src)
        if match:
            return match.groups()
    return None


# Chaque source : un lien STABLE (jamais une URL datée/à identifiant
# imprévisible) vers la page qui affiche la grille courante de ce
# support — vérifié en direct, un par un, avant d'être ajouté ici (voir
# le docstring du module pour la méthode et les 3 exclusions). "language"
# vaut "fr" par défaut (fetch_all() applique ce repli via .get(), voir
# plus bas) — omis sur chacune des 18 sources francophones ci-dessous
# pour ne pas les retoucher inutilement ; explicite ("en") uniquement
# sur les 4 sources anglophones ajoutées après coup. "extract" (optionnel)
# : (fonction, pattern, title_template) pour les sources dont un vrai
# numéro de grille est automatiquement récupérable — voir le docstring
# du module pour comment chacune a été confirmée en direct.
SOURCES = {
    "20minutes": {"name": "20 Minutes", "url": "https://www.20minutes.fr/services/jeux/mots-croises"},
    # "franceinfo_classique" (l'URL générique "classique/", sans slug) est
    # délibérément absente ici — voir le docstring du module : elle
    # redirige réellement vers https://www.franceinfo.fr/culture/musique/
    # classique/, sans aucun rapport avec les mots croisés, confirmé en
    # direct. "cnews" et "lebelage" sont exclues pour leurs propres
    # raisons, également détaillées dans le docstring.
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
        # La date de l'édition numérique du jour ("L'édition numérique du
        # 5 septembre 2026"), confirmée en direct comme étant bien
        # aujourd'hui, pas la date propre à la grille elle-même (jamais
        # trouvée sur cette page) — mais un signal de date réel et fiable
        # malgré tout, contrairement à Le Devoir/TF1 Info (voir plus bas),
        # dont les seules dates trouvées appartenaient à d'autres articles
        # sans rapport, pas à aujourd'hui.
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
        # Ce "numéro" est en réalité une date encodée AAMMJJ (confirmé
        # en direct : "id=260905" pour le 5 septembre 2026), pas un
        # numéro de grille séquentiel comme pour les 3 autres sources —
        # reformaté en date lisible plutôt que montré tel quel, qui
        # laisserait croire, à tort, à un vrai numéro de grille.
        "extract": (_extract_from_iframe_src, r'[?&]id=(\d{2})(\d{2})(\d{2})\b', "Rustica – Grille du {2}/{1}/20{0}"),
    },
    "sudouest": {"name": "Sud Ouest", "url": "https://www.sudouest.fr/jeux/mots-croises/"},
    "tf1info": {"name": "TF1 Info", "url": "https://www.tf1info.fr/jeux/mots-croises/"},
    "telesept": {
        "name": "Télé 7 Jours", "url": "https://www.programme-television.org/jeux/mots-croises",
        "extract": (_extract_from_visible_text, r'Grille\s*n[o°]\s*(\d{2,6})', "Télé 7 Jours – Grille n°{}"),
    },
    "telepro": {"name": "Télépro", "url": "https://www.telepro.be/jeux/mots-croises/"},

    # Sources anglophones, à la demande explicite de l'utilisateur :
    # "Ajoute EN aussi (complémentaire des flux RSS)" — les flux RSS déjà
    # en place (fetch_rss_feeds.py) sont des blogs *à propos* des mots
    # croisés (Rex Parker, Diary of a Crossword Fiend), jamais des liens
    # directs vers une grille jouable ; ces 4 sources comblent ce manque,
    # vérifiées en direct avec la même méthode que pour le français (voir
    # le docstring du module). Deux candidats sérieux ont été rejetés au
    # passage : Washington Post (aucune connexion possible depuis cette
    # machine — HTTP 000/403 selon l'outil, un vrai échec réseau, pas
    # supposé) et USA Today (games.usatoday.com redirige réellement vers
    # "eu.usatoday.com/unsupported-eu/", un blocage géographique des
    # visiteurs européens — exactement la même classe d'échec que
    # Franceinfo Classique en français : une redirection vers une page
    # sans rapport).
    "foxnews": {
        "name": "Fox News", "url": "https://www.foxnews.com/games/daily-crossword-puzzle", "language": "en",
        # L'exemple donné directement par l'utilisateur : "<h3
        # class='date'>Saturday, September 5th</h3>" — confirmé en direct
        # (texte visible réel, correspondant bien à la date du jour).
        "extract": (_extract_from_visible_text, r'((?:Sunday|Monday|Tuesday|Wednesday|Thursday|Friday|Saturday), \w+ \d{1,2}(?:st|nd|rd|th)?)', "Fox News – {}"),
    },
    "guardian": {
        "name": "The Guardian – Cryptic", "url": "https://www.theguardian.com/crosswords/series/cryptic",
        "language": "en",
        # Une page d'archive listant plusieurs grilles récentes (comme
        # Notre Temps/Télé 7 Jours en français) — la première entrée est
        # toujours la plus récente, confirmé en direct.
        "extract": (_extract_from_visible_text, r'Cryptic crossword No ([\d,]+)', "The Guardian – Cryptic No {}"),
    },
    "bestcrosswords": {
        # URL corrigée par l'utilisateur : "Sur ce site, la page de mots
        # croisés du jour est en fait ici" — l'ancienne URL (la page
        # d'accueil du site) n'était pas fausse en soi, mais pas la bonne
        # page spécifique aux mots croisés quotidiens. Confirmée en direct
        # (200, aucune redirection, contenu réel), et une vraie date
        # extractible trouvée au passage : "Puzzles for Saturday,
        # September 5, 2026".
        "name": "BestCrosswords", "url": "https://www.bestcrosswords.com/daily-crossword-puzzles", "language": "en",
        "extract": (_extract_from_visible_text, r'Puzzles for ((?:Sunday|Monday|Tuesday|Wednesday|Thursday|Friday|Saturday), \w+ \d{1,2}, \d{4})', "BestCrosswords – {}"),
    },
    "onlinecrosswords": {
        # URL corrigée par l'utilisateur, même remarque que pour
        # bestcrosswords.com : la page d'accueil n'était pas la bonne
        # page spécifique. Confirmée en direct (200, aucune redirection).
        # Numéro ET date extractibles ensemble ici : "This is the online
        # crossword puzzle #1 for Sep 5, 2026." — 4 groupes captés
        # séparément (numéro, mois, jour, année) plutôt qu'un bloc de
        # date unique, pour ne pas recopier tel quel le double espace
        # réel trouvé dans le texte source ("Sep  5, 2026").
        "name": "OnlineCrosswords.net", "url": "https://www.onlinecrosswords.net/online-daily-crosswords-1.php", "language": "en",
        "extract": (
            _extract_from_visible_text,
            r"crossword puzzle\s*\|\s*#(\d+)\s*\|\s*for\s*\|\s*([A-Za-z]+)\s+(\d{1,2}),\s*(\d{4})",
            "OnlineCrosswords.net – #{} ({} {}, {})",
        ),
    },

    # Sources allemandes/italiennes/espagnoles, à la demande explicite de
    # l'utilisateur : "Cherche des sites donnant des grilles quotidiennes
    # en DE/IT/ES pour les scrapper une fois par jour et donner un lien
    # précis dans l'actu." Recherchées via un agent dédié (WebSearch +
    # WebFetch), puis chacune revérifiée en direct une seconde fois par
    # curl (même méthode, même en-tête réaliste, que pour les sources
    # françaises/anglaises) avant d'être ajoutée ici — jamais ajoutée sur
    # la seule foi du rapport de l'agent. Cette seconde vérification a
    # d'ailleurs corrigé un faux négatif : l'agent avait rejeté eldiario.es
    # en le croyant redirigé vers une page générique, mais un fetch direct
    # confirme au contraire une vraie page de crucigramas ("Crucigramas
    # en Juegos elDiario.es", aucune redirection) — gardée sur la base de
    # cette vérification indépendante plutôt que sur le rapport initial.
    # t-online.de redirige bien, mais vers une URL quasi identique (même
    # identifiant numérique, ordre des mots du slug seulement changé) —
    # l'URL finale (canonique) est celle utilisée ci-dessous, pas
    # l'originale.
    "tonline": {"name": "T-Online", "url": "https://www.t-online.de/spiele/t-online-spiele/id_87469764/kniffliges-kreuzwortraetsel-kostenlos-taeglich-online-spielen.html", "language": "de"},
    "nzz": {"name": "NZZ", "url": "https://spiele.nzz.ch/kreuzwortraetsel/", "language": "de"},
    "rheinpfalz": {"name": "Die Rheinpfalz", "url": "https://www.rheinpfalz.de/spiele/kreuzwortraetsel.html", "language": "de"},
    "ruhrnachrichten": {"name": "Ruhr Nachrichten", "url": "https://www.ruhrnachrichten.de/spiele/kreuzwortraetsel/", "language": "de"},
    "weserkurier": {"name": "Weser-Kurier", "url": "https://www.weser-kurier.de/thema/kreuzwortraetsel-q83207/", "language": "de"},
    "focusde": {"name": "Focus", "url": "https://focus.arkadiumarena.com/games/taeglisches-kreuzwortraetsel/", "language": "de"},

    # IT : recherche nettement plus faible que pour les autres langues
    # (voir CLAUDE.md) — les grands quotidiens italiens n'ont soit pas de
    # page de cruciverba stable trouvable (La Repubblica, Il Fatto
    # Quotidiano), soit sont payants (Corriere della Sera, La Settimana
    # Enigmistica) — seuls ces 2 sites indépendants ont passé la
    # vérification. iltuocruciverba.com publie en réalité chaque
    # SEMAINE, pas chaque jour ("Ogni settimana pubblichiamo un nuovo
    # cruciverba") — gardé quand même : la page elle-même reste stable et
    # réelle, seule sa cadence de mise à jour diffère des autres sources.
    "cruciverbalab": {
        # URL corrigée par l'utilisateur, même remarque que pour
        # bestcrosswords.com/onlinecrosswords.net : la page d'accueil
        # n'était pas la page spécifique au cruciverba lui-même. Confirmée
        # en direct (200, aucune redirection), et une vraie date
        # extractible trouvée au passage : "Cruciverba Lab | 5 settembre
        # 2026" (texte visible réel, correspondant bien à aujourd'hui).
        "name": "Cruciverba Lab", "url": "https://cruciverba-lab.it/cruciverba", "language": "it",
        "extract": (_extract_from_visible_text, r"Cruciverba Lab\s*\|\s*(\d{1,2} \w+ \d{4})", "Cruciverba Lab – {}"),
    },
    "iltuocruciverba": {"name": "Il Tuo Cruciverba", "url": "https://www.iltuocruciverba.com/cruciverba-online-gratis/", "language": "it"},

    "eldebate": {"name": "El Debate", "url": "https://www.eldebate.com/juegos/crucigrama/", "language": "es"},
    "lanacion": {"name": "La Nación", "url": "https://www.lanacion.com.ar/juegos/crucigrama/", "language": "es"},
    "eldiario": {"name": "elDiario.es", "url": "https://www.eldiario.es/juegos/game/crossword/", "language": "es"},
}

SCRAPP_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "SCRAPP")


def fetch_all():
    """Vérifie, pour chaque source de SOURCES, que son URL stable répond
    toujours (best-effort, comme chaque flux RSS pris individuellement
    dans fetch_rss_feeds.py) — une source qui échoue est journalisée et
    simplement omise de ce run, jamais laissée à interrompre les autres.
    Pour les sources porteuses d'une règle "extract", tente en plus d'en
    tirer le vrai numéro de grille du jour — un échec d'extraction (page
    répondant normalement mais motif introuvable, par ex. si le format du
    site changeait) est journalisé mais n'exclut pas la source : elle est
    tout de même gardée, avec son simple nom générique comme titre plutôt
    qu'un numéro qu'on ne peut plus garantir. Écrit SCRAPP/combined.json
    (écrasé à chaque exécution, comme RSS/combined.json) dans la même
    forme {"fetched_at", "items"} déjà utilisée par fetch_rss_feeds.py."""
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
