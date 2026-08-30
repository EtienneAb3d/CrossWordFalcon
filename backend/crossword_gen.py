#!/usr/bin/env python3
"""
Générateur de grilles de mots croisés denses.

Approche en deux temps :
  1. Génération d'un motif de cases noires (sans contrainte de symétrie — chaque
     case noire est placée indépendamment) respectant les règles structurelles
     (aucun emplacement interne de moins de 3 cases sauf en bord de grille,
     aucune case blanche orpheline dans les deux sens à la fois, grille blanche
     connexe — voir is_structurally_valid). Le ratio de cases noires cible
     reste fixé à 0 % (`black_ratio`) sur tous les paliers — plus
     d'augmentation par paliers, à la demande explicite de l'utilisateur :
     "le principe de pré-remplissage (avec au moins 10 solutions par
     emplacement) après conservation de la grille précédente, devrait
     suffire à faire progresser la grille." La grille est déjà préremplie
     de cases noires par la phase de pré-remplissage ci-dessous quand c'est
     nécessaire pour garantir assez de mots candidats par emplacement, et
     le mécanisme de reprise inter-palier (`_build_retry_seed`, voir
     generate_grid) construit chaque palier suivant sur ce qui a déjà été
     résolu au palier précédent plutôt que sur une grille vierge — ces deux
     mécanismes suffisent à faire progresser la recherche d'un palier à
     l'autre, sans qu'il soit nécessaire de densifier artificiellement la
     grille en escaladant un ratio cible. Voir generate_grid pour l'ancien
     mécanisme d'escalade (+2 points par échec, jusqu'à 45 %) et son
     historique de réglage, remplacé par ce principe plus simple. Jusqu'à
     200 paliers (relevé de 40, à la demande explicite de l'utilisateur,
     après un rapport de cycles se bloquant très vite — certaines grilles
     ont besoin de nettement plus de paliers que 40 pour trouver une issue
     via le mécanisme de reprise inter-palier). À
     chaque palier, `PARALLEL_ATTEMPTS` (10 par défaut, paramétrable via
     CROSSWORDFALCON_PARALLEL_ATTEMPTS dans env.sh) tentatives indépendantes
     (motif + remplissage CSP complet) sont lancées en parallèle sur des
     processus séparés — la machine étant loin de saturer son CPU avec une
     seule tentative à la fois, ce parallélisme donne plusieurs chances par
     palier pour un coût en temps réel proche de celui d'une seule tentative ;
     si plusieurs réussissent au même palier, celle qui maximise la somme des
     carrés des longueurs de tous ses mots est retenue, pas simplement la
     première trouvée.
  2. Remplissage par CSP (backtracking) avec un vrai dictionnaire, puis
     minimisation locale : on essaie de retirer chaque case noire une par une
     et on ne garde le retrait que si la grille reste remplissable.

La grille peut être rectangulaire : `width` (nombre de colonnes, horizontal) et
`height` (nombre de lignes, vertical) se règlent indépendamment (15x10 par défaut).

Usage (depuis la racine du projet) :
    python3 backend/crossword_gen.py --width 15 --height 10 --wordlist data/wordlist_fr_full.tsv
"""
import argparse
import concurrent.futures
import math
import multiprocessing
import os
import random
import re
import sys
from collections import Counter, defaultdict

BLACK = "#"
WHITE = "."


class GenerationCancelled(Exception):
    """Levée par generate_grid()/minimize_black_squares() quand le
    `cancel_event` (threading.Event) optionnel qu'on leur a fourni est
    déclenché en cours de route — à la demande explicite de l'utilisateur
    (bouton "Stop" de l'interface web, voir backend/app.py), pour
    interrompre une génération en cours quelle que soit l'étape (recherche
    de motif, minimisation, génération des définitions — cette dernière
    dans backend/clues.py's LLMClueGenerator.generate(), qui lève la même
    exception). Un simple signal *coopératif* : chaque boucle longue
    concernée vérifie l'événement à ses propres points de contrôle
    naturels (entre deux paliers, entre deux cases noires retirées, entre
    deux mots) et lève cette exception plutôt que de continuer — jamais
    une interruption forcée d'un thread ou d'un sous-processus déjà en
    cours d'exécution (voir generate_grid's propre docstring pour la
    limite que ça implique : l'arrêt peut prendre jusqu'à la fin du point
    de contrôle en cours, pas instantané)."""

DEFAULT_WIDTH = 15
DEFAULT_HEIGHT = 10

# Nombre de tentatives (motif + remplissage CSP) lancées en parallèle à chaque
# palier de ratio de cases noires — voir generate_grid(). Choisi à la demande
# explicite de l'utilisateur, la machine étant loin de saturer son CPU avec
# une seule tentative séquentielle à la fois. Paramétrable via la variable
# d'environnement CROSSWORDFALCON_PARALLEL_ATTEMPTS (voir env.sh/env_default.sh
# — sourcée par run_Falcon.sh avant de lancer le back, donc effective pour
# l'API web ; le CLI, lancé directement, la lit aussi si elle est déjà
# exportée dans le shell courant), à la demande explicite de l'utilisateur —
# 10 par défaut si la variable n'est pas définie.
PARALLEL_ATTEMPTS = int(os.environ.get("CROSSWORDFALCON_PARALLEL_ATTEMPTS", "10"))

# Nombre d'exemples de tentatives échouées montrés en aperçu (voir
# generate_grid's "pattern_attempt_failed"/"pattern_failed", et
# frontend/static/script.js's renderAttemptPreview), à la demande explicite
# de l'utilisateur — auparavant un seul exemple (toujours la dernière
# tentative de la liste, sans raison particulière de choisir celle-là plutôt
# qu'une autre) ; affiché maintenant sur 2 lignes de 3 grilles, un aperçu
# différent par tentative parallèle plutôt qu'une seule vue répétée, pour
# donner une idée plus représentative de la diversité des motifs essayés à
# ce palier. Chaque palier lance PARALLEL_ATTEMPTS (10 par défaut) tentatives
# en parallèle, largement de quoi fournir 6 échecs distincts dès qu'aucune ne
# réussit.
FAILED_ATTEMPT_EXAMPLES = 6


# ---------- Dictionnaire ----------

# Presets de difficulté : fraction du lexique conservée (classement global
# par fréquence, toutes longueurs confondues), pas par longueur — un plafond
# par longueur ne filtre rien pour les longueurs qui ont moins de mots au
# total que le plafond lui-même (ex. il n'existe que ~700 mots de 3 lettres
# en français, donc un ancien plafond de 600 "par longueur" laissait passer
# TOUS les mots de 3 lettres, y compris des mots obscurs comme "ABD" — bug
# réel signalé par l'utilisateur, score 103, ~33 000e position globale).
# Moins de mots -> vocabulaire plus reconnaissable mais grille parfois plus
# dure à remplir ; "hard" garde tout le lexique (100 %).
#
# Volontairement une FRACTION du lexique de chaque langue, pas un nombre
# absolu de mots — à la demande explicite de l'utilisateur, après avoir
# constaté qu'un seuil fixe (ex. 80 000 mots) n'a pas du tout le même effet
# suivant la langue : le français a ~113 000 mots dans sa table de
# fréquences, l'allemand ~436 000 (l'allemand compose énormément de mots
# composés, ce qui gonfle son vocabulaire) — un même seuil absolu de 80 000
# garderait ~70 % du lexique français mais seulement ~18 % de l'allemand,
# rendant "facile" nettement plus dur en allemand qu'en français sans que ce
# soit voulu. `load_wordlist()` calcule le nombre de mots réel à partir de
# cette fraction une fois le lexique de la langue effectivement chargé (donc
# après le filtrage `require_gloss` de "easy", le cas échéant) — voir
# `max_words` dans `load_wordlist()`, qui distingue une fraction (float,
# 0 < x <= 1) d'un nombre absolu (int, toujours le comportement de
# `--max-words` en ligne de commande) par son type.
DIFFICULTY_PRESETS = {
    "easy": 0.66,
    "medium": 0.80,
    "hard": 1.0,
}


def _lang_from_path(path):
    match = re.search(r"wordlist_([a-z]{2})_full\.tsv$", os.path.basename(str(path)))
    return match.group(1) if match else None


def _try_import_gloss_lookup():
    """`backend/gloss_lookup.py`'s `has_any_gloss`/`has_gloss_dictionary`,
    imported lazily and tolerantly: crossword_gen.py is also run standalone
    as a CLI script (`python3 backend/crossword_gen.py`, see the module
    docstring) — a relative import at module scope would break that (no
    package context to resolve `.gloss_lookup` against), so this is only
    ever attempted from inside a function, and a failure just means
    `require_gloss` silently has no effect rather than crashing the CLI."""
    try:
        from .gloss_lookup import has_any_gloss, has_gloss_dictionary
        return has_any_gloss, has_gloss_dictionary
    except ImportError:
        return None, None


def load_wordlist(path, max_words=None, require_gloss=False):
    """Charge un lexique au format
    `MOT<TAB>ACCENTUE<TAB>FREQUENCE<TAB>CANONIQUE` (build_wordlist_freq.py)
    ou, en repli, un format à 3 ou 2 colonnes (sans CANONIQUE), ou un simple
    texte libre (un ou plusieurs mots par ligne, fréquence inconnue -> 0,
    pas de forme accentuée/canonique disponible). Si `require_gloss` est
    vrai, un mot est aussi exclu s'il n'a de définition trouvable ni sous sa
    forme fléchie ni sous aucune de ses formes canoniques (voir
    backend/gloss_lookup.py — la fréquence seule ne suffit pas à repérer un
    mot courant mais indéfinissable, ex. l'abréviation "ABD"), en repli
    silencieux si la langue ne peut pas être déduite du nom de fichier ou si
    aucun dictionnaire de définitions n'a été construit pour elle. `max_words`
    accepte deux types, avec des sens différents : un `int` est un nombre
    absolu de mots à garder (comportement historique, utilisé par
    `--max-words` en ligne de commande) ; un `float` (0 < x <= 1, voir
    DIFFICULTY_PRESETS) est une *fraction* du lexique effectivement chargé
    pour cette langue — le nombre absolu correspondant n'est calculé qu'ici,
    une fois la taille réelle du lexique connue (donc après dédoublonnage et
    après le filtrage `require_gloss` le cas échéant), pour que la même
    valeur de `difficulty` retienne une proportion comparable du vocabulaire
    quelle que soit la langue, plutôt qu'un nombre de mots fixe qui n'a pas
    le même effet suivant la taille du lexique de chaque langue. Retourne
    (by_length, accents, canonicals) :
    - by_length = {longueur: [mots]} — seuls les `max_words` mots les plus
      fréquents *au global* (toutes longueurs confondues), si fourni, sont
      conservés, puis regroupés par longueur pour le solveur CSP ;
    - accents = {MOT: forme accentuée/naturelle}, pour les mots retenus dans
      by_length (sert à donner au LLM la vraie orthographe — genre, nombre,
      conjugaison — quand il génère les définitions ; voir backend/clues.py) ;
    - canonicals = {MOT: [forme(s) canonique(s)/lemme(s)]}, pour les mots
      retenus dans by_length (sert à chercher une définition de dictionnaire
      par lemme plutôt que par forme fléchie ; voir backend/clues.py)."""
    entries = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.rstrip("\n")
            if not line or line.startswith("#"):
                continue
            parts = line.split("\t")
            if len(parts) >= 4:
                word = parts[0].upper()
                accented = parts[1]
                try:
                    freq = float(parts[2])
                except ValueError:
                    freq = 0.0
                canonical = [c for c in parts[3].split(";") if c]
                if word.isalpha():
                    entries.append((word, accented, freq, canonical or [accented]))
            elif len(parts) == 3:
                word = parts[0].upper()
                accented = parts[1]
                try:
                    freq = float(parts[2])
                except ValueError:
                    freq = 0.0
                if word.isalpha():
                    entries.append((word, accented, freq, [accented]))
            elif len(parts) == 2:
                word = parts[0].upper()
                try:
                    freq = float(parts[1])
                except ValueError:
                    freq = 0.0
                if word.isalpha():
                    entries.append((word, word, freq, [word]))
            else:
                for tok in line.upper().split():
                    if tok.isalpha():
                        entries.append((tok, tok, 0.0, [tok]))

    best = {}  # word -> (accented, best_freq, canonical)
    for word, accented, freq, canonical in entries:
        if word not in best or freq > best[word][1]:
            best[word] = (accented, freq, canonical)

    if require_gloss:
        has_any_gloss, has_gloss_dictionary = _try_import_gloss_lookup()
        lang = _lang_from_path(path)
        # Guard on the language actually *having* a gloss dictionary built,
        # not just on the import succeeding — `has_any_gloss` returning
        # False for a word with no dictionary at all is indistinguishable
        # from it returning False for a word genuinely undefinable in a
        # real dictionary; without this check, a language with no gloss
        # dictionary built (an optional, gitignored artifact a deploy can
        # easily skip — see build_gloss_dictionary.py) would have every
        # single word rejected instead of the filter no-op'ing as intended.
        if has_any_gloss and lang and has_gloss_dictionary(lang):
            best = {
                word: v for word, v in best.items()
                if has_any_gloss([v[0], *v[2]], lang)
            }

    # Global frequency ranking (see DIFFICULTY_PRESETS above for why this
    # replaced a per-length cap) — then group into by_length for the CSP
    # solver. Order within a length no longer matters here: Filler already
    # shuffles its candidate list with the seeded rng before trying them.
    ranked = sorted(best.items(), key=lambda kv: -kv[1][1])
    if max_words:
        # Un float est une fraction du lexique réel (DIFFICULTY_PRESETS),
        # résolue ici en nombre absolu maintenant que la taille réelle du
        # lexique (post dédoublonnage/require_gloss) est connue ; un int
        # reste un nombre absolu de mots (--max-words).
        if isinstance(max_words, float):
            max_words = round(len(ranked) * max_words)
        ranked = ranked[:max_words]

    result = defaultdict(list)
    accents = {}
    canonicals = {}
    for word, (accented, _, canonical) in ranked:
        result[len(word)].append(word)
        accents[word] = accented
        canonicals[word] = canonical
    return dict(result), accents, canonicals


# ---------- Génération du motif de cases noires ----------

def is_structurally_valid(grid, rows, cols, min_interior_free=3):
    """Une grille est valide si :
    - toute zone blanche *interne* (encadrée par une case noire des deux
      côtés) fait au moins `min_interior_free` cases (3 par défaut),
      **sauf** si l'une de ses deux extrémités touche directement le bord de
      la grille (ligne/colonne 0, ou la dernière) : une telle zone de bord
      reste toujours autorisée, quelle que soit sa longueur (y compris 1 ou
      2 cases) et quel qu'en soit le nombre sur la grille entière — aucun
      budget ni compteur, contrairement à un ancien système à ce sujet
      (voir le SKILL project-best-practices). `min_interior_free` existe
      pour `_place_black_cells`, à la demande explicite de l'utilisateur :
      si l'exigence par défaut (3) ne laisse plus que des cases adjacentes
      à une autre case noire, elle est abaissée à 2 puis 1 pour cette
      tentative de placement précise (voir sa docstring) — tous les autres
      appelants (`minimize_black_squares` compris) utilisent la valeur par
      défaut, inchangée. Une zone d'une seule lettre ne devient jamais un
      emplacement à définir (extract_slots l'exclut toujours, voir plus
      bas) — elle sert juste de passage pour un mot plus long dans l'autre
      sens — mais une zone de DEUX lettres devient un véritable emplacement
      à part entière (extract_slots, seuil >= 2), rempli par un vrai mot de
      2 lettres du dictionnaire et doté de sa propre définition ("et",
      "ou", "no", etc.) ;
    - aucune case blanche ne se retrouve à la fois dans une zone de 1 lettre
      horizontalement ET de 1 lettre verticalement (une case blanche
      totalement isolée, entourée de cases noires des 4 côtés) : une telle
      case ne ferait partie d'aucun emplacement d'au moins 2 lettres et ne
      recevrait donc jamais de lettre — contrainte de correction (une case
      blanche sans emplacement est un bug), jamais assouplie, quel que soit
      `min_interior_free` ;
    - la grille blanche reste entièrement connexe."""
    row_run_len = [[0] * cols for _ in range(rows)]
    col_run_len = [[0] * cols for _ in range(rows)]

    def _short_zone_ok(run, run_start, run_end, line_length):
        """Une zone de moins de `min_interior_free` cases n'est acceptée que
        si elle touche le bord de la grille (run_start == 0 ou
        run_end == line_length) — sans aucune autre limite (ni sur sa
        longueur exacte, ni sur leur nombre total). Une zone d'au moins
        `min_interior_free` cases est toujours acceptée, bord ou pas."""
        if run >= min_interior_free:
            return True
        return run_start == 0 or run_end == line_length

    for r in range(rows):
        run = 0
        run_start = 0
        for c in range(cols):
            if grid[r][c] == WHITE:
                if run == 0:
                    run_start = c
                run += 1
            else:
                if run > 0:
                    if not _short_zone_ok(run, run_start, run_start + run, cols):
                        return False
                for cc in range(run_start, run_start + run):
                    row_run_len[r][cc] = run
                run = 0
        if run > 0:
            if not _short_zone_ok(run, run_start, run_start + run, cols):
                return False
        for cc in range(run_start, run_start + run):
            row_run_len[r][cc] = run

    for c in range(cols):
        run = 0
        run_start = 0
        for r in range(rows):
            if grid[r][c] == WHITE:
                if run == 0:
                    run_start = r
                run += 1
            else:
                if run > 0:
                    if not _short_zone_ok(run, run_start, run_start + run, rows):
                        return False
                for rr in range(run_start, run_start + run):
                    col_run_len[rr][c] = run
                run = 0
        if run > 0:
            if not _short_zone_ok(run, run_start, run_start + run, rows):
                return False
        for rr in range(run_start, run_start + run):
            col_run_len[rr][c] = run

    for r in range(rows):
        for c in range(cols):
            if grid[r][c] == WHITE and row_run_len[r][c] < 2 and col_run_len[r][c] < 2:
                return False

    white = [(r, c) for r in range(rows) for c in range(cols) if grid[r][c] == WHITE]
    if not white:
        return False
    whiteset = set(white)
    seen = {white[0]}
    stack = [white[0]]
    while stack:
        r, c = stack.pop()
        for dr, dc in ((1, 0), (-1, 0), (0, 1), (0, -1)):
            nb = (r + dr, c + dc)
            if nb in whiteset and nb not in seen:
                seen.add(nb)
                stack.append(nb)
    return len(seen) == len(white)


def _has_black_neighbor(grid, rows, cols, r, c):
    """True if at least one of (r, c)'s up-to-4 orthogonal neighbors is
    already black (diagonal contact doesn't count) — used by
    `_place_black_cells` to prefer an isolated cell over one that would
    touch another black cell, when a choice is available."""
    for dr, dc in ((1, 0), (-1, 0), (0, 1), (0, -1)):
        nr, nc = r + dr, c + dc
        if 0 <= nr < rows and 0 <= nc < cols and grid[nr][nc] == BLACK:
            return True
    return False


def _new_black_cell_breaks_locked_slot(grid, rows, cols, r, c, index, locked_letters,
                                        available_lengths=None):
    """Vérifie, pour une case candidate (r, c) actuellement blanche, si la
    transformer en case noire couperait l'un de ses 4 côtés (au sens des 4
    directions depuis cette case, pas ses 4 cases voisines directement :
    le morceau d'emplacement restant de chaque côté, jusqu'à la prochaine
    case noire ou le bord de la grille) en un morceau d'au moins 2 cases
    (« n'étant pas un bord ni une case unique » — un morceau d'une seule
    case n'est jamais un vrai emplacement, voir extract_slots) touchant au
    moins une lettre déjà verrouillée, et dont la combinaison de lettres
    fixées n'a plus assez de candidats réels dans le dictionnaire — un
    filtre préventif à la demande explicite de l'utilisateur, appliqué à
    *chaque* case candidate avant qu'elle ne soit acceptée dans
    `_place_black_cells`, plutôt que de la poser sans le vérifier et de
    compter uniquement sur la passe de réparation après coup (voir
    `_prefill_unfillable_slots`, appelée une seconde fois après le
    placement au ratio) pour rattraper le dégât. Les deux mécanismes
    coexistent délibérément plutôt que l'un remplaçant l'autre : ce filtre
    évite de créer le problème en premier lieu (donc, en général, sans
    case noire supplémentaire pour le réparer ensuite), la passe de
    réparation reste le filet de sécurité pour le cas résiduel où aucune
    case candidate ne passerait ce filtre sur toute la fenêtre disponible.

    Étendu à la demande explicite de l'utilisateur pour couvrir un second
    cas, distinct du verrouillage : un bug réel trouvé en direct dès la
    réintroduction du tirage de densité fixe après pré-remplissage
    (`POST_PREFILL_BLACK_FRACTION`, voir make_pattern) — ce filtre ne
    vérifiait jusque-là que les morceaux touchant une lettre déjà
    verrouillée, jamais la simple *longueur* du morceau contre
    `available_lengths` ; sur un palier sans aucun mot verrouillé (ou dont
    le morceau coupé n'en touche aucun), rien n'empêchait ce tirage de
    créer un emplacement d'une longueur trop rare dans le dictionnaire —
    confirmé en direct : le tout premier palier réussissait encore
    (recherche normale), mais chaque palier suivant échouait quasi
    instantanément (`checks` de 5 à 7 à chaque tentative parallèle, sur
    36 paliers consécutifs) dès que ce tirage de 5% supplémentaire
    entrait en jeu. Chaque morceau est maintenant aussi rejeté
    (indépendamment de tout verrouillage) si sa longueur n'est pas dans
    `available_lengths` — le même critère que `_prefill_unfillable_slots`
    utilise déjà pour décider qu'une longueur est "disponible" — avant même
    de regarder si des lettres verrouillées y sont présentes.

    `False` immédiatement si `index` est absent (aucun dictionnaire à
    vérifier — un appelant qui n'a jamais besoin de ce filtre) ; sans
    `locked_letters` ni `available_lengths`, ce filtre ne fait plus rien
    du tout non plus, ne coûtant rien au cas qui n'en a besoin d'aucun des
    deux."""
    if index is None or (not locked_letters and available_lengths is None):
        return False

    def _run_cells(dr, dc):
        cells = []
        rr, cc = r + dr, c + dc
        while 0 <= rr < rows and 0 <= cc < cols and grid[rr][cc] == WHITE:
            cells.append((rr, cc))
            rr += dr
            cc += dc
        return cells

    for cells in (
        list(reversed(_run_cells(0, -1))),
        _run_cells(0, 1),
        list(reversed(_run_cells(-1, 0))),
        _run_cells(1, 0),
    ):
        length = len(cells)
        if length < 2:
            continue
        if available_lengths is not None and length not in available_lengths:
            return True
        if locked_letters and any(cell in locked_letters for cell in cells):
            if _slot_candidate_count(index, length, cells, locked_letters) < PREFILL_MIN_WORD_COUNT:
                return True
    return False


def _place_black_cells(grid, rows, cols, row_black, col_black, candidates, target, placed,
                        index=None, locked_letters=None, available_lengths=None):
    """Cœur du placement des cases noires, partagé par make_pattern et sa
    phase de pré-remplissage (voir plus bas) — mélangé une seule fois, tiré
    depuis un petit avant-goût de `candidates` déjà mélangée. Place au plus
    `target - placed` nouvelles cases noires ; s'arrête aussi tôt que
    `candidates` est épuisée. À chaque tirage, on considère une fenêtre de
    32 candidates, classées par un critère unique — la ligne et la colonne
    ayant, ensemble, le moins de cases noires déjà posées.

    Parmi cette fenêtre, à la demande explicite de l'utilisateur, on
    préfère toujours une case qui ne touche aucune autre case noire
    (`_has_black_neighbor`) : on cherche la meilleure candidate (au sens du
    critère ci-dessus) qui soit à la fois isolée et structurellement
    valide avec l'exigence normale (`is_structurally_valid`,
    `min_interior_free=3` — au moins 3 cases libres par emplacement
    interne). Si l'exigence de 3 cases ne laisse plus aucune candidate à
    la fois isolée et valide, elle est abaissée à 2, puis à 1, avant
    d'accepter l'adjacence : cette relaxation ne s'applique qu'à cette
    tentative de placement précise, pas à la grille entière ni aux
    tentatives suivantes. Seulement si aucune candidate isolée ne
    fonctionne à aucun de ces trois niveaux, on accepte l'adjacence et on
    retente les mêmes trois niveaux (3, puis 2, puis 1) sans plus exiger
    l'isolement. Dans le cas résiduel où même cela ne trouve rien dans
    toute la fenêtre (les 32 candidates cassent toutes la connexité ou
    créent une case orpheline), la meilleure candidate au sens du critère
    principal est simplement refusée et retirée du lot, pour garantir que
    la boucle progresse toujours.

    Retourne (placed, rejected) — `rejected` couvre TOUTES les cases non
    placées, que la boucle s'arrête faute de candidates ou parce que
    `target` est atteint : les cases refusées, suivies de celles qui
    restaient dans `candidates` sans même avoir été essayées (uniquement
    possible quand `target` est atteint avant d'épuiser `candidates`) —
    un appelant qui a besoin de continuer (comme `_prefill_unfillable_slots`,
    qui appelle cette fonction avec un `target` délibérément petit, une
    seule case à la fois) part de cette liste complète plutôt que de perdre
    silencieusement des candidates jamais essayées (un bug réel trouvé par
    test direct avant que `_prefill_unfillable_slots` ne soit considérée
    terminée : sans ce correctif, une réussite dès le tout premier essai
    renvoyait un `rejected` vide, alors que la quasi-totalité du pool de
    candidates original restait parfaitement valable pour l'étape
    suivante).

    `index`/`locked_letters` (tous deux `None` par défaut — chaque appelant
    existant avant cette fonctionnalité, ainsi que tout appel sans lettres
    verrouillées, est inchangé), à la demande explicite de l'utilisateur, en
    plus de `is_structurally_valid` : une case candidate qui casserait, sur
    l'un de ses 4 côtés, un emplacement d'au moins 2 cases touchant une
    lettre déjà verrouillée sans assez de candidats réels dans le
    dictionnaire (`_new_black_cell_breaks_locked_slot`, voir sa propre
    docstring) est refusée au même titre qu'une case structurellement
    invalide — un filtre préventif, pas seulement une réparation après
    coup. Si aucune case de toute la fenêtre ne passe ce filtre, le
    comportement existant prend le relais sans changement : la meilleure
    candidate est refusée et retirée du lot comme n'importe quel autre cas
    résiduel, laissant la boucle progresser normalement avec, au pire, moins
    de cases posées que `target` — à la demande explicite de l'utilisateur,
    ce n'est délibérément pas traité comme un blocage à contourner ici mais
    laissé remonter tel quel : le remplissage CSP sera simplement tenté sur
    le motif obtenu, et s'il échoue, le mécanisme de nettoyage inter-palier
    déjà en place (`_build_retry_seed`) lui redonne une chance au palier
    suivant en libérant à nouveau de la place, exactement comme il le fait
    déjà pour toute autre cause d'échec."""
    window = 32
    rejected = []
    remaining = candidates

    def _first_valid(indices, min_free):
        for idx in indices:
            r, c = remaining[idx]
            if grid[r][c] == BLACK:
                continue
            if _new_black_cell_breaks_locked_slot(grid, rows, cols, r, c, index, locked_letters,
                                                   available_lengths):
                continue
            grid[r][c] = BLACK
            ok = is_structurally_valid(grid, rows, cols, min_interior_free=min_free)
            grid[r][c] = WHITE
            if ok:
                return idx
        return None

    while remaining and placed < target:
        sample_size = min(window, len(remaining))
        order = sorted(
            range(sample_size),
            key=lambda i: row_black[remaining[i][0]] + col_black[remaining[i][1]],
        )
        non_adjacent = [i for i in order if not _has_black_neighbor(grid, rows, cols, *remaining[i])]

        chosen = None
        for min_free in (3, 2, 1):
            chosen = _first_valid(non_adjacent, min_free)
            if chosen is not None:
                break
        if chosen is None:
            for min_free in (3, 2, 1):
                chosen = _first_valid(order, min_free)
                if chosen is not None:
                    break

        if chosen is None:
            r, c = remaining.pop(order[0])
            rejected.append((r, c))
            continue

        r, c = remaining.pop(chosen)
        grid[r][c] = BLACK
        row_black[r] += 1
        col_black[c] += 1
        placed += 1
    return placed, rejected + remaining


# Nombre minimal de mots d'une longueur donnée dans le dictionnaire pour que
# cette longueur soit considérée "disponible" par la phase de pré-remplissage
# ci-dessous — à la demande explicite de l'utilisateur, remplaçant un seuil
# initial d'un seul mot (une longueur avec ne serait-ce qu'un mot était alors
# jugée suffisante). Un emplacement dont la longueur ne compte que quelques
# mots dans tout le dictionnaire reste en pratique très difficile à remplir
# (surtout si plusieurs emplacements de cette même longueur se disputent le
# même petit lot de mots), même s'il n'est techniquement pas impossible —
# relever ce seuil pousse le pré-remplissage à continuer à poser des cases
# noires dans ce cas-là aussi, plutôt que de s'arrêter dès qu'un seul mot
# existe.
PREFILL_MIN_WORD_COUNT = 10

# Fraction de cases noires supplémentaires tirées après la phase de
# pré-remplissage (voir make_pattern), à la demande explicite de
# l'utilisateur : "rétablir un tirage de 5% de nouvelles cases ajoutées
# (5% par rapport au nombre de cases blanches restantes)", relevé à 10% à
# la demande explicite (et immédiate) de l'utilisateur juste après.
# Rétabli après le retrait complet de l'ancienne escalade de ratio entre
# paliers (voir generate_grid) — mais délibérément différent de cet ancien
# mécanisme : ici, un tirage fixe (jamais escaladé d'un palier à l'autre)
# exprimé en pourcentage des cases *encore blanches après le
# pré-remplissage*, pas du nombre total de cases de la grille. Le
# pré-remplissage place déjà tout ce qui est structurellement nécessaire (au
# moins PREFILL_MIN_WORD_COUNT candidats par emplacement) ; ce tirage
# supplémentaire, purement esthétique/de densité, ne s'applique qu'ensuite
# et ne retire jamais rien de ce que le pré-remplissage a déjà posé.
# Devenue une valeur *par défaut* plutôt qu'une constante figée, à la
# demande explicite de l'utilisateur : `generate_grid`/`make_pattern`
# acceptent désormais `black_enrichment_fraction` en paramètre — réglable
# depuis l'interface web (un sélecteur "Ajout noires", 0/1/3/5/10 %, 3 %
# par défaut — voir GenerateRequest.black_enrichment_percent dans
# backend/app.py) — plutôt que figée à 10 % pour tout le monde. Cette
# constante reste la valeur par défaut pour tout appelant qui ne précise
# rien (le CLI, notamment).
POST_PREFILL_BLACK_FRACTION = 0.10


def _slot_candidate_count(index, length, cells, locked_letters):
    """Nombre de mots candidats pour un emplacement de longueur `length`
    couvrant `cells`, en tenant compte des lettres déjà verrouillées à
    certaines de ses cases (`locked_letters`, un dict case->lettre) — pas
    seulement de sa longueur. Même logique d'intersection par position que
    `Filler._domain`, mais utilisée ici *avant* même que la recherche CSP ne
    démarre, pendant la génération du motif : à la demande explicite de
    l'utilisateur, après un bug réel constaté en direct (voir
    `_has_slot_without_candidate` plus bas pour le contexte complet). Ne
    calcule que ce qui est nécessaire pour savoir si le compte atteint
    `PREFILL_MIN_WORD_COUNT` ou non (voir son propre appelant) — pas un
    besoin de connaître le compte exact au-delà de ce seuil."""
    idx = index.get(length)
    if idx is None:
        return 0
    constraints = {}
    for pos, cell in enumerate(cells):
        letter = locked_letters.get(cell)
        if letter is not None:
            constraints[pos] = letter
    if not constraints:
        return len(idx["words"])
    sets = [idx["pos"][pos].get(ch) for pos, ch in constraints.items()]
    if any(not s for s in sets):
        return 0
    sets.sort(key=len)
    result = sets[0]
    for s in sets[1:]:
        result = result & s
        if not result:
            return 0
    return len(result)


def _has_slot_without_candidate(grid, rows, cols, available_lengths, index=None, locked_letters=None):
    """True if the grid, as it currently stands, has at least one slot
    (`extract_slots`) whose length has fewer than `PREFILL_MIN_WORD_COUNT`
    candidate words in `available_lengths` (the set of slot lengths the
    word list has *enough* words for — see `PREFILL_MIN_WORD_COUNT`) — a
    slot that would be either impossible or merely very hard to fill,
    regardless of which letters end up assigned to its crossings.

    `index`/`locked_letters` (both `None` by default — every caller before
    the cross-palier retry mechanism unaffected), at the user's explicit
    request, after a real bug found and confirmed live with the user's own
    reported data: `available_lengths` alone only checks that a length is
    *generally* well-covered by the dictionary — it says nothing about
    whether a *specific* slot of that length, once some of its cells are
    already pinned to specific letters by `locked_letters` (carried forward
    from a previous palier by `_build_retry_seed`), still has *any* matching
    candidate at all. Reproduced live: a palier that reopened almost every
    black cell around just two surviving locked down-words left several
    18-25-cell-long across slots each needing two fixed letters at specific
    positions — a combination no French word of that length actually has —
    and `try_fill` failed at `checks=1`, before assigning a single new
    letter, confirming the pre-fill phase's own "this length is fine" check
    was silently wrong for slots touching a locked cell. When both are
    given, a slot whose cells include at least one locked cell is checked
    with the more precise `_slot_candidate_count` (a real per-position
    intersection against the word index) instead of the cheap
    length-only lookup; a slot with no locked cell at all still uses the
    fast path unchanged, since nothing there needs the more expensive check."""
    for slot in extract_slots(grid, rows, cols):
        length = len(slot)
        if length not in available_lengths:
            return True
        if locked_letters and any(cell in locked_letters for cell in slot):
            if _slot_candidate_count(index, length, slot, locked_letters) < PREFILL_MIN_WORD_COUNT:
                return True
    return False


def _slot_with_insufficient_candidates(grid, rows, cols, available_lengths, index=None,
                                        locked_letters=None, skip=None):
    """Comme `_has_slot_without_candidate` (True/False), mais renvoie
    l'emplacement lui-même (sa liste de cases) dès qu'il en trouve un dont
    la longueur — ou l'intersection avec `locked_letters` quand fourni,
    voir `_slot_candidate_count` — laisse moins de `PREFILL_MIN_WORD_COUNT`
    candidats ; `None` si tous les emplacements sont corrects (ou déjà dans
    `skip`, voir plus bas). À la demande explicite de l'utilisateur :
    `_prefill_unfillable_slots` en a besoin pour cibler directement la case
    noire à poser *dans cet emplacement* plutôt que n'importe où dans la
    grille (voir sa propre docstring pour le bug que ce ciblage corrige).

    `skip` (un ensemble de tuples de cases, `None` par défaut), à la
    demande explicite de l'utilisateur : un emplacement dont toutes les
    cases sont déjà verrouillées (typiquement deux mots verrouillés
    adjacents, voir `_prefill_unfillable_slots`) ne peut par construction
    jamais être corrigé en y posant une case noire — sans `skip`, cette
    fonction renverrait indéfiniment ce même emplacement irréparable à
    chaque nouvel appel, empêchant `_prefill_unfillable_slots` de jamais
    progresser sur les *autres* emplacements réellement corrigibles."""
    for slot in extract_slots(grid, rows, cols):
        if skip and tuple(slot) in skip:
            continue
        length = len(slot)
        if length not in available_lengths:
            return slot
        if locked_letters and any(cell in locked_letters for cell in slot):
            if _slot_candidate_count(index, length, slot, locked_letters) < PREFILL_MIN_WORD_COUNT:
                return slot
    return None


def _has_slot_without_candidate(grid, rows, cols, available_lengths, index=None, locked_letters=None):
    """True if the grid, as it currently stands, has at least one slot
    (`extract_slots`) whose length has fewer than `PREFILL_MIN_WORD_COUNT`
    candidate words in `available_lengths` — see `_slot_with_insufficient_
    candidates`, which this is now a thin wrapper around (kept as a
    separate, plain boolean helper for the few callers — e.g. unit tests —
    that only need the yes/no answer, not the offending slot itself)."""
    return _slot_with_insufficient_candidates(grid, rows, cols, available_lengths, index, locked_letters) is not None


def _prefill_unfillable_slots(grid, rows, cols, row_black, col_black, candidates,
                               available_lengths, index=None, locked_letters=None, rng=None):
    """Phase de pré-remplissage, à la demande explicite de l'utilisateur :
    tant que la grille comporte un emplacement (`extract_slots`) dont la
    longueur a moins de `PREFILL_MIN_WORD_COUNT` mots candidats dans le
    dictionnaire (`available_lengths` — typiquement un emplacement trop
    long pour le dictionnaire, ou d'une longueur si rare qu'il ne reste
    quasiment aucun mot pour la remplir, augmenté depuis un seuil d'un
    seul mot à la demande explicite de l'utilisateur) — ou, quand
    `index`/`locked_letters` sont fournis (voir `_has_slot_without_
    candidate`), un emplacement dont la longueur est correcte en général
    mais dont l'intersection avec des lettres déjà verrouillées ne laisse
    plus assez de candidats — on continue à poser des cases noires, une
    case à la fois pour pouvoir revérifier après chaque case si un
    emplacement sans assez de candidats subsiste. S'arrête dès que ce n'est
    plus le cas, ou si plus aucune case ne peut être ajoutée (`candidates`
    épuisée) alors qu'un tel emplacement subsiste toujours — un cas limite
    accepté, pas une erreur : la grille est rendue telle quelle, et un
    motif qui ne peut pas être corrigé ainsi échouera simplement au
    remplissage CSP ensuite, de la façon normale.

    La case posée à chaque itération est choisie **directement dans
    l'emplacement problématique lui-même** (`_slot_with_insufficient_
    candidates`, parmi ses propres cases encore disponibles dans
    `candidates`) plutôt qu'ailleurs dans la grille via le critère
    générique ligne/colonne de `_place_black_cells` — corrigé à la demande
    explicite de l'utilisateur, après un bug réel constaté en direct : le
    critère générique n'a aucune raison de finir par toucher précisément
    l'emplacement en cause, donc le pré-remplissage pouvait noircir de
    nombreuses cases ailleurs dans la grille, sans le moindre rapport avec
    le problème à corriger, avant qu'une case y atterrisse enfin par pur
    hasard — observé en direct sur une vraie grille : une tentative
    "réussie" en noircissant la quasi-totalité de la grille (à peine deux
    colonnes blanches restantes sur 25), une grille de mots croisés
    inutilisable, alors qu'une poignée de cases bien ciblées auraient
    suffi. Le ciblage choisit, parmi les cases de l'emplacement en cause qui
    restent dans `candidates`, celle qui a le moins de cases noires déjà
    posées sur sa ligne+colonne (`row_black[r] + col_black[c]`, le même
    critère de "zone la plus disponible" que `_place_black_cells` utilise
    déjà ailleurs dans ce fichier) — la proximité au partage équilibré de
    l'emplacement (`abs(2*position - (longueur-1))`) ne sert plus que de
    départage entre cases à égalité sur ce premier critère. Corrigé à la
    demande explicite de l'utilisateur après un second bug réel constaté en
    direct : une version antérieure ne triait que par ce partage équilibré,
    sans jamais regarder `row_black`/`col_black` — sur une grille large où
    de nombreux emplacements de même longueur ont besoin d'être coupés (par
    exemple, au tout début de la génération, une rangée entière de 25
    cases sans encore aucune case noire), la coupe "la plus équilibrée" est
    systématiquement la même position géométrique (le milieu exact) pour
    chacun d'eux, faisant retomber toutes les rangées sur exactement la
    même colonne — une colonne entièrement noire du haut en bas, l'exact
    opposé de "chercher les zones les plus disponibles". Le nouveau critère
    fait qu'une fois une case posée dans une colonne donnée, cette colonne
    (et sa ligne) devient moins attractive pour le prochain emplacement à
    corriger, qui préfère alors une colonne encore vierge — la répartition
    en résulte naturellement, sans règle dédiée "pas deux fois la même
    colonne". Si aucune case de cet emplacement n'est disponible dans
    `candidates` (déjà toutes exclues, par exemple parce qu'elles sont
    toutes verrouillées — deux mots verrouillés adjacents, typiquement) ou
    qu'aucune ne préserve la connexité, cet emplacement précis est marqué
    irréparable (`unfixable`, voir `_slot_with_insufficient_candidates`) et
    ignoré pour le reste de cet appel — à la demande explicite de
    l'utilisateur, ce n'est pas une raison d'abandonner le pré-remplissage
    pour la grille entière, seulement pour cet emplacement précis ; les
    autres emplacements problématiques, eux, restent corrigés normalement.
    Chaque case candidate est aussi vérifiée avec
    `is_structurally_valid(min_interior_free=1)` avant d'être acceptée —
    l'invariant absolu (connexité, aucune case orpheline) que toute case
    noire posée n'importe où dans ce fichier doit respecter ; les
    candidates de l'emplacement sont essayées dans cet ordre de préférence
    jusqu'à en trouver une qui le respecte, ou jusqu'à épuisement (même cas
    limite accepté que ci-dessus)."""
    count = 0
    unfixable = set()
    while candidates:
        slot = _slot_with_insufficient_candidates(
            grid, rows, cols, available_lengths, index, locked_letters, skip=unfixable
        )
        if slot is None:
            break
        candidate_set = set(candidates)
        length = len(slot)
        cells_in_slot = [cell for cell in slot if cell in candidate_set]
        if rng is not None:
            rng.shuffle(cells_in_slot)
        options = sorted(
            cells_in_slot,
            key=lambda cell: row_black[cell[0]] + col_black[cell[1]],
        )
        placed_one = False
        for (r, c) in options:
            grid[r][c] = BLACK
            if is_structurally_valid(grid, rows, cols, min_interior_free=1):
                row_black[r] += 1
                col_black[c] += 1
                candidates.remove((r, c))
                count += 1
                placed_one = True
                break
            grid[r][c] = WHITE
        if not placed_one:
            # Cet emplacement précis ne peut être corrigé par aucune case
            # noire disponible (typiquement : toutes ses cases sont déjà
            # verrouillées par des lettres d'un palier précédent, ou aucune
            # ne préserve la connexité) — à la demande explicite de
            # l'utilisateur, ce n'est pas une raison d'abandonner tout le
            # pré-remplissage : on le marque pour ne plus jamais le
            # reproposer (`unfixable`) et on continue sur les autres
            # emplacements, qui restent corrigibles indépendamment. Ce
            # résidu, s'il subsiste jusqu'au remplissage CSP, échouera
            # simplement là normalement — et, dans le cas d'un mot
            # verrouillé, sera retiré au palier suivant par le même
            # mécanisme de nettoyage qui retire déjà tout mot croisant un
            # emplacement impossible (voir _build_retry_seed).
            unfixable.add(tuple(slot))
    return candidates


def make_pattern(rows, cols, black_ratio, rng, available_lengths=None,
                  seed_grid=None, locked_letters=None, index=None,
                  black_enrichment_fraction=POST_PREFILL_BLACK_FRACTION):
    """Places black cells one at a time, independently (no symmetry
    constraint — dropped at the user's explicit request, since the CSP
    fill is fast enough that trying more patterns is cheap, and a
    non-symmetric search can reach a much lower black-cell ratio while
    staying structurally valid, in a way pairing every cell with its
    180° mirror could not always do), biased to keep black cells apart
    from each other.

    A purely random placement order (just shuffling every cell) tends to
    let black cells end up touching each other by chance, forming small
    clumps/"walls" — which both look worse and force many neighboring
    words to share the same length (the length is just the gap between
    black cells in that row/column). Keeping black cells apart avoids
    that directly.

    Implemented as a small look-ahead (`_place_black_cells`): at each step,
    sample a window of 32 still-untried cells and prefer the one whose
    row+column currently have, together, the fewest black cells already
    placed — a single main criterion, at the user's explicit request,
    reverting a much more elaborate design this area had grown into (a
    cascade of strict-then-tolerant phases with per-length zone budgets, a
    row/column discount that varied by phase, and an adjacency secondary
    tie-break) — see the project-best-practices SKILL for that whole
    history. Falls back to shuffle order once the window is exhausted, so
    even this one criterion is a soft preference, not a hard constraint —
    it never makes a fillable ratio/size combination infeasible.

    Structural validity itself (`is_structurally_valid`) is equally simple
    now: an *interior* white zone (bounded by a black cell on both sides)
    must be at least `min_interior_free` cells long (3 by default); a zone
    touching the grid's own border on at least one side is always allowed,
    whatever its length and however many of them the grid ends up with.
    `_place_black_cells` reintroduces a preference for keeping black cells
    apart, at the user's explicit request, but expressed by relaxing this
    structural minimum rather than by a secondary tie-break criterion: among
    the window, it first looks for the best candidate (by the row/column
    criterion) that is *not* adjacent to any existing black cell and valid
    at `min_interior_free=3`; if the 3-cell requirement leaves no such
    isolated candidate, it's relaxed to 2, then to 1, still only considering
    isolated candidates — only once even the most relaxed level finds none
    is adjacency accepted at all, again trying 3, then 2, then 1 before
    giving up on that specific placement attempt.

    `available_lengths` (`None` by default — every existing caller
    unaffected), at the user's explicit request: the set of slot lengths
    the word list has *at least* `PREFILL_MIN_WORD_COUNT` candidate words
    for (10, not just 1 — raised at the user's own explicit follow-up
    request, since a length with only a handful of words in the entire
    list stays very hard to fill even when it's not literally impossible,
    especially once more than one slot of that length competes for the
    same tiny pool). When given, a
    **pre-fill phase** (`_prefill_unfillable_slots`, see its own docstring)
    runs first, before the ratio-based placement below even starts: as long
    as the grid has a slot whose length isn't in `available_lengths` (too
    long for any word the list has, or simply too poorly covered), it keeps
    placing black cells with this exact same look-ahead algorithm until
    that's no longer the case. Cells placed this way are never counted
    against `black_ratio`'s own target — placement below only starts
    counting *after* the pre-fill phase returns, so a slot that would
    otherwise have too few candidate words (and make the whole pattern hard
    or impossible to fill at the CSP-fill stage no matter how the rest of
    it turns out) gets fixed for free, without eating into the ratio the
    rest of the grid still needs.

    `seed_grid` (`None` by default — every existing caller unaffected), at
    the user's explicit request: instead of starting from an all-white
    grid, continue placing black cells on top of an already-partially-black
    grid (see `generate_grid`'s cross-palier retry-seed mechanism,
    `_build_retry_seed`) — `row_black`/`col_black` and `placed` (the
    running count `_place_black_cells` compares against `target`) are
    initialized from `seed_grid`'s own existing black cells instead of
    zero, and only `seed_grid`'s still-white cells become placement
    candidates, so every already-black cell is preserved exactly as given
    rather than being re-decided. `locked_letters` (a `{(r, c): letter}`
    dict, meaningful only together with `seed_grid`) excludes its cells
    from the candidate pool entirely — at the user's explicit request,
    verified necessary rather than assumed: without it, a white cell that
    already holds a real, confirmed letter from the previous palier (see
    `_build_retry_seed`) would be just as eligible for a *new* black cell
    as any other still-white cell, silently destroying that confirmed
    letter the moment a black cell landed on it. Also threaded through to
    `_prefill_unfillable_slots` (together with `index`, the word index —
    both required together for that check to go beyond the plain
    length-only one; see `_has_slot_without_candidate`'s own docstring for
    the real bug this fixes).

    A further, distinct bug was found and fixed here, reported by the user
    from a real generation: "le tirage de nouvelles cases noires peut
    enfermer des groupes de lettres qui ne correspondent pas à un mot
    possible, et donc rendre la grille immédiatement injouable... la
    probabilité de produire une telle situation augmente avec le
    remplissage de plus en plus complet de la grille." The pre-fill phase
    above only runs *once*, before the ratio-based placement below —
    `_place_black_cells` (the generic row/column-balance heuristic used for
    that ratio-based placement) has no `locked_letters`/`index` parameter at
    all, so nothing stops it from truncating a slot that crosses an
    already-locked letter (carried forward from a previous palier) into a
    shape that no longer has enough real candidates given that fixed
    letter — exactly the "enfermer des lettres" the user described. This
    risk is close to zero on a fresh, unlocked palier (nothing is fixed yet
    for a new cell to conflict with) but grows every palier a search
    fails and more letters get locked in — matching the user's own
    observation that the probability increases "avec le remplissage de
    plus en plus complet de la grille." Confirmed directly before fixing:
    seeding 30 real grids with a handful of locked words each (mimicking a
    genuine carried-forward core) and running the ratio-based phase at a
    non-trivial ratio (0.10) on top produced at least one locked-touching
    slot with fewer than `PREFILL_MIN_WORD_COUNT` real candidates — several
    with *zero* — on **30/30** seeds. Fixed by running the exact same
    `_prefill_unfillable_slots` repair pass a second time, after the
    ratio-based placement, whenever `locked_letters`/`index` are present —
    reusing the existing mechanism rather than adding a new one: its own
    internal loop already re-scans every slot in the grid after each cell it
    places, so a single extra call is enough to reach a fresh fixed point
    against whatever the ratio-based phase just did, including marking a
    genuinely irreducible case `unfixable` for the next palier's own cleanup
    to resolve, exactly as it already does for the first pre-fill pass.

    A fixed, non-escalating extra density draw was reinstated after
    pre-fill, at the user's explicit request, once the previous ratio-
    escalation-across-paliers mechanism had been fully removed elsewhere
    (see `generate_grid`): "rétablir un tirage de 5% de nouvelles cases
    ajoutées (5% par rapport au nombre de cases blanches restantes)."
    Deliberately different from the old ladder it replaces: this fraction
    (`POST_PREFILL_BLACK_FRACTION`) never escalates from one palier to the
    next — the same fixed fraction applies every time. `black_ratio`
    itself is still honored as a floor (`round(rows*cols*black_ratio)`)
    for a caller that still passes a non-zero value (e.g. the CLI's
    `--black-ratio`), but `generate_grid`'s own default (`0.0`, never
    escalated) means this floor contributes nothing in the common case.

    Whether pre-fill's own cells count toward this fraction's target has
    itself changed, at the user's explicit request. Originally, the
    fraction was computed on the cells still white right *after* pre-fill
    had already placed whatever was structurally necessary (not the
    grid's total size), and added *on top* of `placed` (`placed` itself
    recomputed right after pre-fill so `_place_black_cells` never
    double-counted pre-fill's own cells as still-to-place) — meaning
    pre-fill's own cells never counted toward this specific percentage
    target: however many pre-fill needed, the same fixed fraction of
    whatever remained was *always* added on top. Changed later, again at
    the user's explicit request ("les cases noires ajoutées en
    pré-remplissage comptent pour l'objectif de remplissage en noir"): the
    fraction is now computed once, on the count of white cells *before*
    pre-fill ever runs (`initial_white_count`, captured right after the
    initial shuffle), and folded into the same `max(...)` as `placed` and
    the `black_ratio` floor, rather than added on top of `placed`
    unconditionally. Since `placed` already includes whatever pre-fill
    itself placed, this means pre-fill's own cells now genuinely count
    toward reaching this percentage: if pre-fill alone already placed more
    cells than the target percentage of the *original* white-cell count
    calls for, no further cells are added for this reason at all (`placed`
    wins the `max`); if it placed fewer, only the shortfall is added on
    top by `_place_black_cells` below."""
    if seed_grid is not None:
        grid = [row[:] for row in seed_grid]
        row_black = [row.count(BLACK) for row in grid]
        col_black = [sum(1 for r in range(rows) if grid[r][c] == BLACK) for c in range(cols)]
        locked = set(locked_letters) if locked_letters else set()
        candidates = [
            (r, c) for r in range(rows) for c in range(cols)
            if grid[r][c] == WHITE and (r, c) not in locked
        ]
        placed = sum(row_black)
    else:
        grid = [[WHITE] * cols for _ in range(rows)]
        row_black = [0] * rows
        col_black = [0] * cols
        candidates = [(r, c) for r in range(rows) for c in range(cols)]
        placed = 0
    rng.shuffle(candidates)
    # Nombre de cases blanches *avant* le pré-remplissage — base du calcul
    # de `black_enrichment_fraction` ci-dessous, à la demande explicite de
    # l'utilisateur ("les cases noires ajoutées en pré-remplissage
    # comptent pour l'objectif de remplissage en noir") : le pourcentage
    # visé est désormais calculé sur ce total fixe, pas sur ce qu'il reste
    # de blanc une fois le pré-remplissage terminé — voir plus bas.
    initial_white_count = len(candidates)

    if available_lengths is not None:
        candidates = _prefill_unfillable_slots(
            grid, rows, cols, row_black, col_black, candidates, available_lengths,
            index, locked_letters, rng,
        )

    placed = sum(row.count(BLACK) for row in grid)
    # `placed` inclut déjà les cases posées par le pré-remplissage
    # ci-dessus — les faire compter dans l'objectif signifie que ce
    # dernier terme (`black_enrichment_fraction * initial_white_count`,
    # calculé sur le total blanc *avant* pré-remplissage, jamais sur ce
    # qu'il en reste après) est un troisième argument de `max`, au même
    # titre que `placed` et le plancher `black_ratio` — et non plus un
    # ajout systématique par-dessus, à la demande explicite de
    # l'utilisateur : si le pré-remplissage a déjà posé plus de cases que
    # ce que ce pourcentage réclame, aucune case supplémentaire n'est
    # ajoutée pour cet objectif (`placed` l'emporte déjà dans le max) ;
    # s'il en a posé moins, seule la différence est complétée.
    target = max(
        placed,
        round(rows * cols * black_ratio),
        round(black_enrichment_fraction * initial_white_count),
    )
    _place_black_cells(grid, rows, cols, row_black, col_black, candidates, target, placed,
                        index=index, locked_letters=locked_letters, available_lengths=available_lengths)

    if available_lengths is not None and locked_letters:
        _prefill_unfillable_slots(
            grid, rows, cols, row_black, col_black, candidates, available_lengths,
            index, locked_letters, rng,
        )

    return grid



# ---------- Extraction des cases (slots across / down) ----------

def extract_slots(grid, rows, cols):
    """A white run of exactly 2 cells is now a real, cluable slot (a 2-letter
    word — "et", "ou", "no", etc.), not just a passthrough for a crossing
    word; a run of exactly 1 cell never becomes a slot at all (see
    is_structurally_valid's border-zone/orphan-check discussion for why both
    are tolerated in the grid at all)."""
    slots = []
    for r in range(rows):
        c = 0
        while c < cols:
            if grid[r][c] == WHITE:
                start = c
                while c < cols and grid[r][c] == WHITE:
                    c += 1
                if c - start >= 2:
                    slots.append([(r, cc) for cc in range(start, c)])
            else:
                c += 1
    for c in range(cols):
        r = 0
        while r < rows:
            if grid[r][c] == WHITE:
                start = r
                while r < rows and grid[r][c] == WHITE:
                    r += 1
                if r - start >= 2:
                    slots.append([(rr, c) for rr in range(start, r)])
            else:
                r += 1
    return slots


# ---------- Index du lexique : mots par (longueur, position, lettre) ----------
#
# Avec 100 000+ mots, filtrer par recherche linéaire à chaque case est trop lent.
# On indexe une fois par longueur : pos[p][lettre] -> ensemble des mots de cette
# longueur ayant `lettre` en position p. Les intersections de quelques ensembles
# (un par lettre déjà connue) remplacent le scan complet du lexique.

def build_index(by_length):
    index = {}
    for length, words in by_length.items():
        pos_sets = [defaultdict(set) for _ in range(length)]
        for w in words:
            for p, ch in enumerate(w):
                pos_sets[p][ch].add(w)
        index[length] = {"words": words, "pos": pos_sets}
    return index


# ---------- CSP : remplissage par backtracking ----------

# Le MRV (Minimum Remaining Values — présélectionner l'emplacement le moins
# contraint, voir l'historique complet dans la SKILL project-best-practices)
# a été retiré de `Filler._backtrack`'s règle de sélection, à la demande
# explicite de l'utilisateur : il n'a plus sa place dans la façon dont la
# grille se construit désormais. Sa justification d'origine — repérer au
# plus tôt l'emplacement le plus susceptible de bloquer, au sein d'une
# unique tentative de remplissage complète — ne tient plus une fois que la
# progression se fait par petites étapes qui se succèdent et s'accumulent
# entre paliers (la reprise "telle-quelle", `_pattern_continue`, et la
# détection d'impossibilité consciente de `used_words`, voir plus bas) :
# un emplacement à très peu de candidats une fois des lettres verrouillées
# est maintenant, dans les faits, presque toujours un signe qu'il est
# authentiquement bloqué (ou sur le point de l'être) plutôt qu'un
# emplacement méritant une attention prioritaire — le MRV le faisait
# pourtant systématiquement passer devant tous les autres, y compris des
# emplacements faciles et abondamment pourvus en candidats, ce qui pouvait
# faire tourner la recherche en rond sur un cas quasi désespéré au lieu de
# progresser ailleurs. Voir le project-best-practices SKILL pour le
# diagnostic complet qui a mené à ce retrait.
#
# **Ce retrait reste en vigueur** : une réintégration du MRV en priorité
# absolue a été brièvement essayée pour corriger un remplissage clairsemé
# du tout premier palier (grille vierge, rien de verrouillé), puis
# explicitement rejetée par l'utilisateur — "ma dernière demande sur ce
# sujet était justement de ne plus donner la priorité au MRV." Le vrai
# correctif pour ce cas précis n'implique pas le MRV ; voir CLAUDE.md pour
# la solution retenue une fois trouvée.

# Fréquence (en nombre d'appels à _backtrack) à laquelle une recherche CSP
# vérifie `cancel_event` (bouton "Stop", voir Filler.__init__), à la demande
# explicite de l'utilisateur ("le bouton Stop ne s'applique pas rapidement...
# prévoir l'arrêt dans toutes les phases") — une recherche peut appeler
# _backtrack des centaines de milliers de fois (jusqu'à `deadline_checks`)
# sans jamais rendre la main autrement, donc un point de contrôle *externe*
# entre deux paliers (déjà en place) ne suffit pas à rendre "Stop" réactif
# pendant qu'un palier est en cours. Vérifié toutes les CANCEL_CHECK_INTERVAL
# fois plutôt qu'à chaque appel : `multiprocessing.Event.is_set()` reste bon
# marché, mais autant ne pas payer ce coût à chaque nœud d'une recherche qui
# peut en visiter des centaines de milliers.
CANCEL_CHECK_INTERVAL = 500

# Seuil (fraction des cases blanches de la grille) et fréquence de
# vérification (en nombre d'appels à _backtrack) pour l'abandon anticipé
# d'une tentative, à la demande explicite de l'utilisateur : "Quand une
# situation de génération atteint plus de 30% de la grille réputée non
# remplissable, considérer le tour sur cette tentative comme échoué, et
# qu'il ne faut plus tenter d'ajouter des mots." Voir Filler._backtrack —
# vérifié périodiquement (comme CANCEL_CHECK_INTERVAL ci-dessus, pas à
# chaque appel) puisque calculer les cases impossibles (impossible_zone_
# cells) a un coût réel, pas négligeable si répété à chaque nœud d'une
# recherche qui peut en visiter des centaines de milliers.
UNFILLABLE_ABANDON_FRACTION = 0.30
UNFILLABLE_ABANDON_CHECK_INTERVAL = 500

# Check frequency (in number of calls to _backtrack) for the "another
# attempt of the same palier has already finished" signal (see
# attempt_done_event/generate_grid), at the user's explicit request:
# "interrupt every search as soon as one search finishes (success or
# failure) to move on to the next palier." Same value and same reasoning as
# CANCEL_CHECK_INTERVAL/UNFILLABLE_ABANDON_CHECK_INTERVAL above (a
# `multiprocessing.Event.is_set()` stays cheap but not free to repeat at
# every node of a search that can visit hundreds of thousands of them) —
# kept as its own constant, for consistency with the style already used
# here, rather than reusing one of the two existing constants whose name
# carries an entirely different meaning.
PALIER_ATTEMPT_DONE_CHECK_INTERVAL = 500

# Fraction of a palier's PARALLEL_ATTEMPTS attempts that must have finished
# (success or failure) before every other still-running attempt of the same
# palier is interrupted, at the user's explicit request — refining the
# initial design (interrupt as soon as the very first attempt finishes):
# "à partir de 30% des tentatives qui se terminent... interrompre toutes
# les tentatives." See attempt_done_event/generate_grid.
PALIER_ATTEMPT_INTERRUPT_FRACTION = 0.30


def _slots_touching(slots, target_indices):
    """Renvoie l'ensemble des indices d'emplacement (hors `target_indices`
    eux-mêmes) qui partagent au moins une case avec l'un des emplacements
    de `target_indices` — utilisé à la fois par `Filler.__init__` (pour ne
    jamais essayer de remplir un emplacement qui croise un emplacement déjà
    connu comme impossible, voir `_crossing_excluded_slots`) et par
    `generate_grid` (pour que le calcul de "still_has_hope" traite ces
    mêmes emplacements comme sans espoir eux aussi, et non comme un
    emplacement encore prometteur — voir plus bas pour pourquoi ce second
    usage est nécessaire)."""
    target_indices = set(target_indices)
    if not target_indices:
        return set()
    cell_to_slots = defaultdict(list)
    for i, cells in enumerate(slots):
        for cell in cells:
            cell_to_slots[cell].append(i)
    touching = set()
    for i in target_indices:
        for cell in slots[i]:
            for j in cell_to_slots[cell]:
                if j != i:
                    touching.add(j)
    return touching


class Filler:
    def __init__(self, slots, index, rng, forced_letters=None, letter_scores=None,
                 excluded_slots=None, cancel_event=None, batch_abandoned_event=None,
                 attempt_done_event=None):
        self.slots = slots
        self.index = index
        self.rng = rng
        # Signal partagé entre les tentatives parallèles d'un même batch,
        # voir sa propre définition (`_worker_batch_abandoned_event`) —
        # positionné par n'importe laquelle dès qu'elle s'abandonne
        # elle-même (voir plus bas), vérifié par toutes les autres.
        self.batch_abandoned_event = batch_abandoned_event
        # "This palier already has its answer" signal (see attempt_done_event
        # in generate_grid), at the user's explicit request: "interrupt every
        # search as soon as one search finishes (success or failure) to move
        # on to the next palier." Unlike `batch_abandoned_event` above, this
        # one is passed by both `_pattern_attempt` and `_pattern_continue`:
        # it never judges the quality or prospects of THIS attempt's own
        # pattern (which, for `_pattern_attempt`, would say nothing reliable
        # about a sibling attempt's independent pattern — see
        # `_worker_batch_abandoned_event`) — it only announces that ANOTHER
        # attempt of the same palier already finished (success or failure)
        # and that continuing to search here is now pointless, regardless of
        # what this attempt would eventually have found.
        self.attempt_done_event = attempt_done_event
        # Bouton "Stop" de l'interface web (voir GenerationCancelled), à la
        # demande explicite de l'utilisateur : contrairement aux points de
        # contrôle déjà en place entre deux paliers (generate_grid) ou entre
        # deux cases noires retirées (minimize_black_squares), une recherche
        # CSP peut à elle seule tourner très longtemps (jusqu'à
        # `deadline_checks`, largeur × hauteur × 2000 vérifications — voir
        # try_fill) sans jamais rendre la main — sans un point de contrôle
        # *à l'intérieur même* de cette
        # recherche, "Stop" pouvait rester sans effet visible pendant toute
        # la durée du palier en cours. Vérifié tous les CANCEL_CHECK_INTERVAL
        # appels à _backtrack (voir plus bas) plutôt qu'à chaque appel — un
        # `multiprocessing.Event.is_set()` reste bon marché, mais des
        # centaines de milliers d'appels par recherche justifient quand même
        # de ne pas le vérifier littéralement à chaque nœud.
        self.cancel_event = cancel_event
        # case -> lettre "conseillée" par l'échantillonnage statistique
        # préalable (voir sample_letter_biases) — un simple indice utilisé
        # par _domain pour orienter la recherche dès le départ, jamais une
        # affectation réelle : dès qu'un emplacement croisé est vraiment
        # assigné, sa propre lettre prend le pas sur cet indice (voir
        # _domain ci-dessous).
        self.forced_letters = forced_letters or {}
        # case -> Counter(lettre -> occurrences), le même échantillonnage
        # statistique que forced_letters ci-dessus mais gardé dans son
        # intégralité (voir sample_letter_biases) — utilisé par _backtrack
        # pour trier les mots candidats d'un emplacement plutôt que les
        # tirer au hasard, à la demande explicite de l'utilisateur (voir
        # _candidate_score ci-dessous). Toujours rempli par `_pattern_attempt`
        # (à la demande explicite de l'utilisateur : ce tri ne dépend plus de
        # `force_letters_fraction`, seul `forced_letters` en dépend encore) —
        # `or {}` ici reste une protection pour un appelant direct de Filler
        # qui n'en fournirait pas (ex. un test), auquel cas _backtrack
        # retombe simplement sur le tirage aléatoire pur.
        self.letter_scores = letter_scores or {}
        # cell -> [(slot_index, position_within_that_slot), ...]. Precomputed
        # once here rather than looked up with list.index() inside _domain
        # (the hot path, called millions of times per grid) since a cell's
        # position within a slot never changes once slots are extracted.
        self.cell_to_slots = defaultdict(list)
        for i, cells in enumerate(slots):
            for pos, cell in enumerate(cells):
                self.cell_to_slots[cell].append((i, pos))
        # Nombre total de cases blanches de la grille (une case par clé de
        # cell_to_slots, indépendamment du nombre d'emplacements qui la
        # traversent) — dénominateur de UNFILLABLE_ABANDON_FRACTION, voir
        # _backtrack. Calculé une seule fois ici, jamais recalculé.
        self._total_white_cells = len(self.cell_to_slots)
        # Passe à True dès qu'une tentative est abandonnée en cours de
        # route faute d'espoir raisonnable (voir _backtrack et
        # UNFILLABLE_ABANDON_FRACTION) — une fois positionné, chaque appel
        # suivant à _backtrack échoue immédiatement, sans plus explorer.
        self.abandoned = False
        # Distinct from `self.abandoned` above (which it still reuses as a
        # fast short-circuit, see _backtrack): set specifically when this
        # attempt was cut short because ANOTHER attempt of the same palier
        # already answered (`attempt_done_event`), never because this one
        # judged its own pattern hopeless — try_fill uses it to distinguish
        # the two in `diagnostics["reason"]`.
        self.interrupted_by_sibling = False
        # "across" ou "down" par emplacement, précalculé une fois pour
        # l'alternance horizontal/vertical de _backtrack (voir plus bas) —
        # même convention que build_word_entries : un emplacement de plus
        # d'une case est horizontal si sa 2e case est sur la même ligne que
        # la 1re, vertical sinon (un emplacement d'une seule case — cas
        # inexistant ici puisque extract_slots exige au moins 2 cases — n'a
        # pas d'importance pour ce cas de figure).
        self.directions = [
            "across" if len(cells) > 1 and cells[1][0] == cells[0][0] else "down"
            for cells in slots
        ]
        self.assignment = [None] * len(slots)
        # Emplacements en dehors de toute considération de _backtrack — ni
        # jamais sélectionnés pour une tentative d'affectation, ni jamais
        # source d'échec immédiat via le contrôle de domaine ci-dessous —
        # à la demande explicite de l'utilisateur : "avant de nettoyer
        # l'emplacement identifié comme bloquée, continuer à ajouter des
        # mots tant que c'est possible." Un emplacement déjà identifié
        # comme impossible lors d'une tentative précédente sur ce même
        # motif recommence, sans cette exclusion, par faire échouer le
        # tout premier appel à `_backtrack` (le contrôle de domaine
        # s'exécute sur *tous* les emplacements non assignés avant même de
        # choisir lequel traiter, donc un domaine vide pour cet
        # emplacement précis suffisait à empêcher toute nouvelle
        # affectation ailleurs dans la grille, même sans aucun rapport
        # avec lui) — vu en direct, `checks=1` à chaque tentative. Un
        # ensemble vide par défaut (`set()` plutôt que `None`, jamais
        # réévalué à chaque appel) laisse tout appelant existant inchangé.
        self.excluded_slots = excluded_slots if excluded_slots is not None else set()
        # Emplacements qui croisent (partagent au moins une case avec) un
        # emplacement de `excluded_slots` — nouvelle règle de sélection, à
        # la demande explicite de l'utilisateur, prioritaire sur les 4
        # niveaux de `_backtrack` : ne jamais essayer de remplir un tel
        # emplacement. Un mot qui y serait posé serait de toute façon
        # retiré par le prochain nettoyage (`_build_retry_seed`, qui retire
        # tout mot croisant directement un emplacement impossible) s'il
        # n'était jamais remis en cause avant — autant ne jamais le poser
        # plutôt que de dépenser du budget de recherche sur un mot voué à
        # disparaître. Calculé une seule fois ici, pas à chaque appel à
        # _backtrack : `excluded_slots` ne change jamais après __init__.
        self._crossing_excluded_slots = _slots_touching(slots, self.excluded_slots)
        self.used_words = set()
        self.checks = 0
        # Copie de l'assignation au moment où le plus grand nombre
        # d'emplacements ont été remplis simultanément au cours de toute la
        # recherche, quel que soit l'endroit exact où elle a fini par
        # échouer — contrairement à self.assignment (qui revient à
        # [None, ...] une fois la recherche entièrement défaite par le
        # backtracking), best_assignment garde la trace de l'état le plus
        # avancé atteint. Pur diagnostic, à la demande explicite de
        # l'utilisateur : ne déclenche aucune tentative de récupération (ni
        # retouche, ni retry) — seulement un aperçu à faire remonter en cas
        # d'échec, voir try_fill/diagnostics["example_grid"].
        self.best_assignment = list(self.assignment)
        self.best_assigned_count = 0

    def _domain(self, i):
        """Ensemble/liste des mots compatibles avec les lettres déjà connues de
        la case i (sans encore exclure les mots utilisés ailleurs — voir _pick).
        Une lettre "conseillée" par self.forced_letters (voir __init__) compte
        comme une contrainte au même titre qu'une lettre vraiment imposée par
        un emplacement croisé déjà assigné — mais seulement tant qu'aucun
        emplacement croisé n'est réellement assigné à cette case : une vraie
        affectation l'emporte toujours sur un simple indice statistique."""
        cells = self.slots[i]
        length = len(cells)
        idx = self.index.get(length)
        if idx is None:
            return ()
        constraints = {}
        for pos, cell in enumerate(cells):
            letter = None
            for j, other_pos in self.cell_to_slots[cell]:
                if j != i and self.assignment[j] is not None:
                    letter = self.assignment[j][other_pos]
                    break
            if letter is None:
                letter = self.forced_letters.get(cell)
            if letter is not None:
                constraints[pos] = letter
        if not constraints:
            return idx["words"]
        sets = []
        for pos, ch in constraints.items():
            s = idx["pos"][pos].get(ch)
            if not s:
                return ()
            sets.append(s)
        sets.sort(key=len)
        result = sets[0]
        for s in sets[1:]:
            result = result & s
            if not result:
                return ()
        return result

    def _placed_letter_count(self, i):
        """Nombre de cases de l'emplacement i dont la lettre est déjà connue
        — soit imposée par un emplacement croisé déjà assigné, soit une
        "graine" (self.forced_letters, l'ancien "lettres forcées" —
        renommé à la demande explicite de l'utilisateur, voir sample_
        letter_biases) — à la demande explicite de l'utilisateur, après un
        rapport direct confirmant un vrai manque : sur une grille vierge
        avec une graine posée, aucun emplacement n'avait alors de
        croisement réellement assigné, donc l'emplacement de la graine
        n'avait cette même priorité que tous les autres — il pouvait très
        bien ne jamais être choisi tôt, à l'exact opposé du rôle voulu pour
        une graine ("emplacements qui initient les premiers placements, ou
        les influencent quand il y a déjà d'autres lettres"). Une case
        comptée une seule fois même si elle est à la fois une graine et
        fixée par un croisement réellement assigné — ce compte ne mesure
        qu'un ensemble de cases déjà connues, pas deux critères distincts.
        Utilisé par _backtrack comme critère de sélection principal (voir
        ci-dessous), pas par _domain (qui a besoin du détail case->lettre,
        pas juste du compte)."""
        cells = self.slots[i]
        count = 0
        for cell in cells:
            known = cell in self.forced_letters
            if not known:
                for j, _ in self.cell_to_slots[cell]:
                    if j != i and self.assignment[j] is not None:
                        known = True
                        break
            if known:
                count += 1
        return count

    def _candidate_score(self, i, word):
        """Somme des carrés des scores statistiques (self.letter_scores,
        voir sample_letter_biases) de `word` sur les cases de l'emplacement
        i qui ne sont *pas* déjà fixées par un emplacement croisé assigné —
        une case déjà fixée n'a besoin d'aucun classement supplémentaire,
        puisque `word` doit déjà la respecter exactement pour figurer dans
        le domaine (voir _domain). Utilisée par _backtrack pour trier les
        mots candidats d'un emplacement, à la demande explicite de
        l'utilisateur, plutôt que les tirer au hasard — mettre les scores
        au carré favorise un mot dont plusieurs cases encore libres
        correspondent toutes bien au consensus statistique plutôt qu'un mot
        qui ne doit un score élevé qu'à une seule case exceptionnelle,
        cohérent avec le même choix déjà fait ailleurs dans ce projet (la
        somme des carrés des longueurs de mots pour départager les
        tentatives parallèles, voir generate_grid)."""
        cells = self.slots[i]
        total = 0
        for pos, cell in enumerate(cells):
            fixed = any(
                j != i and self.assignment[j] is not None
                for j, _ in self.cell_to_slots[cell]
            )
            if fixed:
                continue
            total += self.letter_scores.get(cell, {}).get(word[pos], 0) ** 2
        return total

    def exclude_immediately_impossible_slots(self):
        """À la demande explicite de l'utilisateur : "Le tour après une
        régénération semble s'arrêter dès qu'un emplacement est impossible,
        ce qui peut se produire immédiatement à cause du tirage des cases
        noires. Tous les tours doivent se dérouler aussi longtemps qu'on
        peut ajouter des mots en respectant les règles d'ajout."

        À appeler une seule fois, juste avant `solve()` (donc après
        l'application éventuelle de `preseed_assignment` par l'appelant,
        voir try_fill) et avant tout appel à `_backtrack` : à ce moment
        précis, `self.assignment` ne contient encore que les cases
        réellement verrouillées (aucune décision de recherche n'a encore
        été prise), donc le domaine de chaque emplacement encore non
        assigné ne reflète que des contraintes définitives — s'il est déjà
        vide (ou entièrement déjà utilisé) à cet instant, il le restera
        pour le reste de cette recherche, quoi que la recherche essaie par
        ailleurs (`_domain` ne dépend que des croisements réellement
        assignés/verrouillés, jamais d'un choix encore à faire).

        Sans ce correctif, le contrôle de domaine de `_backtrack` (qui
        s'exécute pour *tous* les emplacements non assignés avant même de
        choisir lequel traiter) trouvait ce même emplacement vide à
        absolument chaque appel, quel que soit le chemin de recherche
        emprunté — la recherche échouait alors immédiatement (`checks=1`
        ou presque), sans jamais avoir la moindre chance d'essayer de
        remplir le reste de la grille, pourtant souvent parfaitement
        remplissable par ailleurs. Chaque emplacement ainsi identifié est
        ajouté à `excluded_slots` (même mécanisme que pour un emplacement
        déjà connu impossible d'un palier précédent, voir `_pattern_
        continue`) — jamais assigné, mais laissant la recherche continuer
        librement sur tout le reste ; `_crossing_excluded_slots` est
        recalculé en conséquence, pour que la nouvelle règle "ne pas
        essayer de remplir les emplacements qui croisent un emplacement
        réputé impossible" s'applique aussi à ces exclusions découvertes
        ici, pas seulement à celles reçues en argument.

        Un seul passage suffit (pas besoin de reboucler jusqu'à un point
        fixe) : exclure un emplacement ne change le domaine calculé
        d'aucun autre — `_domain` ne consulte jamais `excluded_slots`, il
        ne fait que déterminer lesquels `_backtrack` a le droit de
        sélectionner."""
        newly_excluded = {
            i for i in range(len(self.slots))
            if self.assignment[i] is None
            and i not in self.excluded_slots
            and i not in self._crossing_excluded_slots
            and all(w in self.used_words for w in self._domain(i))
        }
        if newly_excluded:
            self.excluded_slots = self.excluded_slots | newly_excluded
            self._crossing_excluded_slots = _slots_touching(self.slots, self.excluded_slots)
        return newly_excluded

    def solve(self, deadline_checks):
        return self._backtrack(deadline_checks)

    def impossible_zone_slots(self):
        """Comme impossible_zone_cells (voir plus bas), mais renvoie les
        *indices d'emplacement* plutôt que les cases elles-mêmes — à la
        demande explicite de l'utilisateur, pour l'algorithme de reprise
        entre paliers (voir generate_grid/_build_retry_seed) qui a besoin de
        savoir *quels emplacements* sont bloqués afin d'en retirer les mots
        directement connectés, pas seulement quelles cases surligner dans
        l'aperçu. `impossible_zone_cells` est réécrite en termes de cette
        méthode plutôt que de dupliquer le même calcul deux fois.

        Un emplacement compte comme impossible non seulement quand son
        domaine brut (`_domain`, qui ignore les mots déjà utilisés ailleurs
        dans la grille) est vide, mais aussi quand *chacun* de ses
        candidats est déjà utilisé par un autre mot déjà placé dans
        `best_assignment` — sinon un tel emplacement (un domaine non vide
        en apparence, mais dont plus aucun candidat n'est réellement
        disponible) reste invisible à ce diagnostic, empêchant à tort
        `generate_grid`'s "still_has_hope"/`excluded_slots` de jamais le
        traiter comme bloqué (voir _backtrack ci-dessous pour le même
        correctif côté recherche). `used_at_best` est recalculé directement
        depuis `best_assignment` plutôt que de lire `self.used_words` — ce
        dernier reflète l'état *courant* de `self.assignment` (qui peut
        avoir entièrement reculé jusqu'à son état de départ une fois la
        recherche terminée), pas nécessairement celui du point le plus
        avancé (`best_assignment`) que ce diagnostic examine."""
        saved = self.assignment
        self.assignment = self.best_assignment
        used_at_best = {w for w in self.best_assignment if w is not None}
        result = [
            i for i, word in enumerate(self.best_assignment)
            if word is None and all(w in used_at_best for w in self._domain(i))
        ]
        self.assignment = saved
        return result

    def impossible_zone_cells(self):
        """Cases appartenant à un emplacement non assigné, dans l'état
        self.best_assignment (le point le plus avancé atteint avant
        l'abandon — voir __init__), dont le domaine est vide (aucun mot ne
        convient compte tenu des lettres déjà fixées par les emplacements
        croisés) — les "zones impossibles" à mettre en évidence dans
        l'aperçu d'une tentative échouée (voir try_fill,
        diagnostics["impossible_cells"]), à la demande explicite de
        l'utilisateur. Peut être vide : le point le plus avancé atteint
        n'est pas forcément celui où la recherche a fini par échouer — par
        exemple un échec par épuisement du budget de vérifications
        (`deadline_exceeded`) peut survenir alors que tous les domaines à
        ce moment-là restaient non vides, juste pas encore résolus à
        temps."""
        cells = set()
        for i in self.impossible_zone_slots():
            cells.update(self.slots[i])
        return sorted(cells)

    def _backtrack(self, deadline_checks):
        self.checks += 1
        if self.abandoned:
            return False
        if self.checks > deadline_checks:
            return False
        if (
            self.cancel_event is not None
            and self.checks % CANCEL_CHECK_INTERVAL == 0
            and self.cancel_event.is_set()
        ):
            raise GenerationCancelled()
        # Arrêt anticipé de TOUT le batch dès qu'une tentative sœur s'est
        # elle-même abandonnée (voir _worker_batch_abandoned_event et
        # UNFILLABLE_ABANDON_FRACTION ci-dessous) — à la demande explicite
        # de l'utilisateur : ne pas attendre que cette tentative-ci atteigne
        # elle aussi son propre seuil d'abandon ou son propre budget une
        # fois qu'une autre a déjà jugé le motif commun sans espoir. Même
        # fréquence de vérification que les autres signaux ci-dessus/
        # dessous — un coût réel à ne pas payer à chaque nœud.
        if (
            self.batch_abandoned_event is not None
            and self.checks % UNFILLABLE_ABANDON_CHECK_INTERVAL == 0
            and self.batch_abandoned_event.is_set()
        ):
            self.abandoned = True
            return False
        # Early stop as soon as ANOTHER attempt of the same palier already
        # answered (success or failure), at the user's explicit request
        # ("interrupt every search as soon as one search finishes (success
        # or failure) to move on to the next palier") — see
        # attempt_done_event in generate_grid. Unlike the batch_abandoned_
        # event checkpoint above, this one applies to both _pattern_attempt
        # and _pattern_continue (see Filler.__init__'s own docstring for why
        # the distinction made for batch_abandoned_event doesn't apply
        # here). Reuses `self.abandoned` as a fast short-circuit (same
        # mechanism as just above), but also sets
        # `self.interrupted_by_sibling` so try_fill can distinguish this
        # specific cause in `diagnostics["reason"]`.
        if (
            self.attempt_done_event is not None
            and self.checks % PALIER_ATTEMPT_DONE_CHECK_INTERVAL == 0
            and self.attempt_done_event.is_set()
        ):
            self.abandoned = True
            self.interrupted_by_sibling = True
            return False
        # Abandon anticipé d'une tentative, à la demande explicite de
        # l'utilisateur (voir UNFILLABLE_ABANDON_FRACTION ci-dessus) : dès
        # que plus de 30 % des cases blanches de la grille appartiennent à
        # un emplacement réputé impossible (au sens de impossible_zone_
        # cells, calculé sur best_assignment), cette tentative est jugée
        # sans espoir raisonnable et abandonnée sur-le-champ — inutile de
        # continuer à essayer d'ajouter des mots ailleurs sur un motif déjà
        # aussi largement compromis. Vérifié seulement toutes les
        # UNFILLABLE_ABANDON_CHECK_INTERVAL fois (comme cancel_event
        # ci-dessus), pas à chaque appel : impossible_zone_cells recalcule
        # le domaine de chaque emplacement non assigné, un coût réel à ne
        # pas payer à chaque nœud.
        if (
            self._total_white_cells > 0
            and self.checks % UNFILLABLE_ABANDON_CHECK_INTERVAL == 0
            and len(self.impossible_zone_cells())
            > UNFILLABLE_ABANDON_FRACTION * self._total_white_cells
        ):
            self.abandoned = True
            # Signale aux autres tentatives du même batch qu'elles peuvent,
            # elles aussi, s'arrêter — voir le commentaire de
            # _worker_batch_abandoned_event et le point de contrôle
            # correspondant plus haut dans cette même méthode.
            if self.batch_abandoned_event is not None:
                self.batch_abandoned_event.set()
            return False
        unassigned = [
            i for i in range(len(self.slots))
            if self.assignment[i] is None
            and i not in self.excluded_slots
            and i not in self._crossing_excluded_slots
        ]
        # Compté directement depuis self.assignment (pas dérivé de
        # `len(self.slots) - len(unassigned)`) : avec `excluded_slots` non
        # vide, cette dernière formule compterait à tort chaque emplacement
        # exclu comme "assigné" alors qu'il reste bel et bien à None.
        assigned_count = sum(1 for a in self.assignment if a is not None)
        if assigned_count > self.best_assigned_count:
            self.best_assigned_count = assigned_count
            self.best_assignment = list(self.assignment)
        if not unassigned:
            return True

        # On calcule le domaine de chaque emplacement non assigné ici (et on
        # échoue immédiatement si l'un d'eux est déjà à sec), pour détecter
        # une branche morte le plus tôt possible — ce domaine sert aussi à
        # trier les mots candidats de l'emplacement finalement choisi (voir
        # plus bas), quel que soit le critère qui l'a désigné.
        #
        # `_domain` ne tient compte que des contraintes de lettres (croisements
        # déjà assignés / indices statistiques) — jamais de `self.used_words`.
        # Un emplacement dont le domaine brut est non vide peut donc, dans une
        # grille déjà très remplie, n'avoir en réalité PLUS AUCUN candidat
        # disponible (chacun de ses mots déjà utilisé ailleurs) — un vrai
        # blocage, identique en pratique à un domaine vide, mais invisible à
        # ce contrôle sans vérifier aussi `used_words` ici. Bug réel constaté
        # en direct : une grille de 143 emplacements restait bloquée à
        # exactement 115 assignés/3 impossibles pendant plus de 180 paliers
        # consécutifs d'affilée, `checks=1` à chaque fois — l'emplacement qui
        # bloquait réellement la recherche avait un domaine techniquement non
        # vide (une quinzaine de candidats), mais chacun d'eux était déjà
        # utilisé par un autre mot de la grille, donc `impossible_zone_slots`
        # (voir plus haut, même correctif) ne le remontait jamais non plus.
        domains = {}
        for i in unassigned:
            domain = self._domain(i)
            if all(w in self.used_words for w in domain):
                return False
            domains[i] = domain

        # Règle de sélection à 2 niveaux, à la demande explicite de
        # l'utilisateur (le MRV a été retiré — voir le commentaire plus
        # haut, avant la classe Filler, pour pourquoi) :
        # 1. on alterne d'abord horizontal/vertical : on tire la
        #    catégorie (across ou down) au hasard, avec une probabilité
        #    proportionnelle au nombre d'emplacements encore libres dans
        #    chacune des 2 catégories (self.directions, précalculé dans
        #    __init__) — une catégorie qui a encore beaucoup d'emplacements
        #    non remplis a plus de chances d'être choisie que l'autre, ce
        #    qui tend naturellement à alterner/équilibrer les deux au fil du
        #    remplissage sans figer un ordre strict ;
        # 2. les niveaux 2/3/4 précédents (le moins de cases encore
        #    blanches en priorité, le plus de lettres déjà fixées en
        #    départage, un tirage pondéré par la longueur en dernier
        #    recours) ont été remplacés par une règle unique, à la demande
        #    explicite de l'utilisateur : parmi les emplacements de la
        #    catégorie tirée, on calcule pour chacun le score
        #    int(100 * lettres_déjà_remplies / sqrt(longueur)) (une première
        #    version, int(remplies / longueur²) sans le facteur 100, donnait
        #    toujours 0 — remplies ne dépassant jamais longueur, le ratio
        #    reste toujours ≤ 1/longueur < 1 — corrigée à la demande
        #    explicite de l'utilisateur ; le dénominateur lui-même est passé
        #    ensuite de longueur² à sqrt(longueur), toujours à la demande
        #    explicite de l'utilisateur — un dénominateur qui croît beaucoup
        #    plus lentement avec la longueur, ce qui favorise davantage les
        #    emplacements longs déjà bien avancés face aux emplacements
        #    courts, contrairement à longueur² qui pénalisait très fortement
        #    tout emplacement long quel que soit son propre remplissage),
        #    et on tire au hasard, uniformément, **parmi les emplacements
        #    ayant obtenu le meilleur score, dans une fenêtre de
        #    max(5, int(emplacements_libres_de_cette_catégorie / 10))**
        #    emplacements — une fenêtre qui s'élargit quand il reste
        #    beaucoup d'emplacements encore à remplir dans la catégorie
        #    tirée, et se resserre (jusqu'à ce plancher de 5) une fois qu'il
        #    n'en reste plus beaucoup, plutôt qu'une taille fixe ou liée à
        #    la seule taille de la grille. Ce critère a déjà changé
        #    plusieurs fois : "le moins de cases encore blanches", puis "le
        #    plus de lettres déjà remplies" en compte brut sur une fenêtre
        #    de 30, puis remplies/longueur sur une fenêtre de 15, puis
        #    l'égalité stricte sans fenêtre, puis une fenêtre fixe de 10,
        #    puis une fenêtre en int(sqrt(largeur × hauteur)), puis cette
        #    fenêtre proportionnelle au nombre d'emplacements encore libres
        #    actuelle. Les emplacements sont mélangés (avec le RNG seedé de
        #    cette tentative, donc reproductible) avant d'être triés par
        #    score décroissant : sans ce mélange préalable, l'ordre de tri
        #    (`sorted` est stable) déciderait quels emplacements à égalité
        #    passent la coupure de la fenêtre, réintroduisant le même biais
        #    positionnel déjà rencontré ailleurs dans ce fichier (voir plus
        #    haut, les bugs "colonne noire"/"triangle" du pré-remplissage).
        free_across = [i for i in unassigned if self.directions[i] == "across"]
        free_down = [i for i in unassigned if self.directions[i] == "down"]
        if free_across and free_down:
            direction_pool = self.rng.choices(
                [free_across, free_down],
                weights=[len(free_across), len(free_down)],
                k=1,
            )[0]
        else:
            direction_pool = free_across or free_down
        scores = {
            i: int(100 * self._placed_letter_count(i) / (len(self.slots[i]) ** 0.5))
            for i in direction_pool
        }
        shuffled_pool = list(direction_pool)
        self.rng.shuffle(shuffled_pool)
        window_size = max(5, int(len(direction_pool) / 10))
        window = sorted(shuffled_pool, key=lambda i: -scores[i])[:window_size]
        best_i = self.rng.choice(window)

        cands = [w for w in domains[best_i] if w not in self.used_words]
        # Toujours mélangé d'abord (avec le RNG seedé de cette tentative,
        # donc reproductible) — que ce mélange serve de tirage final
        # (letter_scores vide, comportement inchangé) ou seulement à
        # départager les ex-æquo du tri qui suit juste en dessous, `sort`
        # étant stable : sans letter_scores, un mot n'a jamais deux fois le
        # même score (toujours 0), donc l'ordre du mélange lui-même decide.
        self.rng.shuffle(cands)
        if self.letter_scores:
            # À la demande explicite de l'utilisateur : essayer les mots
            # candidats de l'emplacement choisi en priorité selon la somme
            # des carrés de leurs scores statistiques sur les cases encore
            # libres (voir _candidate_score), au lieu d'un tirage purement
            # aléatoire — appliqué systématiquement dès que `letter_scores`
            # est fourni, ce qui est désormais le cas à chaque tentative,
            # que `force_letters_fraction` soit à 0 ou non (voir __init__ et
            # _pattern_attempt) : seul `forced_letters` (les cases
            # réellement figées) dépend encore de ce réglage. Ce bloc ne
            # reste inactif — et le mélange juste au-dessus reste alors le
            # tirage final, exactement comme avant cette fonctionnalité —
            # que pour un appelant direct de Filler qui ne fournirait
            # aucun `letter_scores` du tout.
            cands.sort(key=lambda w: self._candidate_score(best_i, w), reverse=True)
            # Pas un ordre de test strictement décroissant pour autant, à la
            # demande explicite de l'utilisateur : à chaque tirage, on
            # pioche au hasard parmi les `CANDIDATE_SCORE_WINDOW` (20)
            # meilleurs mots *encore restants* du tri (pas les 20 premiers
            # du tri d'origine, figés une fois pour toutes — la fenêtre
            # glisse au fur et à mesure que des mots en sont retirés), un
            # peu comme la fenêtre de 32 cases de _place_black_cells pour
            # les cases noires : garde la priorité globale aux mots les
            # mieux notés tout en évitant de tester très exactement dans
            # l'ordre du tri, qui reviendrait à un choix entièrement
            # déterministe (à seedage égal) plutôt qu'une vraie exploration.
            window = 20
            reordered = []
            remaining = cands
            while remaining:
                take = min(window, len(remaining))
                idx = self.rng.randrange(take)
                reordered.append(remaining.pop(idx))
            cands = reordered
        for w in cands:
            self.assignment[best_i] = w
            self.used_words.add(w)
            if self._backtrack(deadline_checks):
                return True
            self.assignment[best_i] = None
            self.used_words.discard(w)
        return False


# ---------- Pré-remplissage statistique avant le CSP ----------

# Nombre de mots tirés au hasard par emplacement (uniquement filtrés par
# longueur, sans aucune validation contre les autres emplacements) pour
# estimer, par simple sondage, quelle lettre a le plus de chances d'occuper
# chaque case avant même de lancer le remplissage réel — à la demande
# explicite de l'utilisateur.
LETTER_BIAS_SAMPLE_SIZE = 100

# Fraction du nombre total de cases blanches de la grille que l'on fige
# d'avance avec la lettre la plus fréquemment observée à cet endroit dans
# l'échantillonnage ci-dessus — seules les cases où cette lettre est
# ressortie le plus souvent (au sens large : cases les plus "consensuelles"
# en premier) sont retenues, jusqu'à atteindre cette fraction. Abaissée de
# 10 % à 5 % à la demande explicite de l'utilisateur.
LETTER_BIAS_FORCE_FRACTION = 0.05

# Nombre minimal de mots de l'échantillon de LETTER_BIAS_SAMPLE_SIZE qui
# doivent partager la lettre retenue pour qu'une case soit éligible à être
# figée — à la demande explicite de l'utilisateur, en plus de la limite
# d'une seule case forcée par emplacement : un consensus trop faible (une
# lettre qui ne l'emporte que parce que les autres étaient encore plus
# dispersées, sans réellement dominer) ne garantit pas qu'il reste assez de
# mots compatibles pour remplir l'emplacement une fois cette lettre figée.
LETTER_BIAS_MIN_COUNT = 10


def sample_letter_biases(grid, rows, cols, index, rng,
                          sample_size=LETTER_BIAS_SAMPLE_SIZE,
                          force_fraction=LETTER_BIAS_FORCE_FRACTION,
                          excluded_slots=None):
    """Avant de lancer le remplissage CSP réel sur une grille de cases
    noires/blanches fraîchement choisie, à la demande explicite de
    l'utilisateur : pour chaque emplacement, tire au hasard `sample_size`
    mots de la bonne longueur (uniquement filtrés par longueur, sans les
    valider les uns contre les autres — un simple sondage, pas un
    remplissage), compte pour chaque case de cet emplacement quelle lettre
    y apparaît le plus souvent dans l'échantillon, ne retient que les cases
    dont cette lettre dépasse `LETTER_BIAS_MIN_COUNT` (10) occurrences (un
    consensus trop faible — une lettre qui ne l'emporte que parce que les
    autres étaient encore plus dispersées — ne garantit pas qu'il reste
    assez de mots compatibles une fois cette lettre figée), puis pioche au
    hasard parmi ces cases éligibles jusqu'à couvrir `force_fraction` du
    nombre total de cases blanches de la grille — au plus UNE case forcée
    par emplacement (jamais deux cases forcées sur le même mot). Le tirage
    au hasard (plutôt que les cases au consensus le plus fort en premier,
    une version précédente de cette règle) est à la demande explicite de
    l'utilisateur, après un rapport : pour une longueur donnée, prendre
    systématiquement les cases les plus consensuelles en premier revenait
    trop souvent à figer la même lettre dominante (la plus fréquente de la
    langue à cette position) sur la plupart des emplacements de cette
    longueur, au lieu de varier. La limite d'une seule case forcée par
    emplacement reste nécessaire pour la même raison qu'avant : plusieurs
    cases forcées indépendamment sur un même emplacement long peuvent ne
    correspondre à aucun mot réel (chaque case est choisie indépendamment
    des autres, sans qu'aucun mot réel n'ait forcément toutes ces lettres à
    la fois), ce qui a été mesuré en direct : jusqu'à 9 tentatives sur 10
    échouaient dès la toute première vérification. Une case appartenant à
    deux emplacements (croisement) consomme le quota des deux à la fois —
    si l'un des deux a déjà sa case forcée, l'autre ne peut plus en
    proposer une nouvelle, même sur une case différente. Le nombre de cases
    réellement forcées peut donc rester en dessous de `force_fraction` —
    soit parce que la grille n'a pas assez d'emplacements distincts pour
    l'atteindre (rare en pratique), soit parce que peu de cases atteignent
    le seuil de consensus (plus fréquent, et volontaire : mieux vaut forcer
    moins de cases que d'en forcer une sur un consensus faible).

    `excluded_slots` (un ensemble d'indices d'emplacement, `None` par
    défaut — aucun effet pour un appelant qui n'en fournit pas), à la
    demande explicite de l'utilisateur : "les graines ne doivent être
    placées que sur des emplacements réputés jouables (si possible), donc,
    non verrouillés comme injouables." Un emplacement de cet ensemble
    (déjà connu impossible — voir `Filler.excluded_slots`) ne propose plus
    jamais l'une de ses propres cases comme candidate à devenir une graine
    — poser une graine dessus serait un indice gaspillé, puisque cet
    emplacement ne sera de toute façon jamais tenté par la recherche.
    N'affecte que `forced` : `letter_scores` continue d'être alimenté pour
    *tous* les emplacements sans exception, y compris ceux exclus — une
    case de croisement partagée avec un emplacement non exclu a toujours
    besoin de sa contribution statistique complète pour trier correctement
    les mots candidats de ce second emplacement (voir plus bas).
    "Si possible" : si tous les emplacements de la grille sont exclus (un
    cas limite, jamais rencontré en pratique), `eligible` reste simplement
    vide et aucune graine n'est posée du tout, plutôt que de forcer une
    case sur un emplacement injouable faute d'alternative.

    Retourne `(forced, letter_scores)` :
    - `forced` : un dict {case: lettre} — les "indices" que Filler traite
      comme des contraintes tant qu'aucun emplacement croisé n'est
      réellement assigné à cette case (voir Filler._domain), pas comme des
      lettres définitivement posées ;
    - `letter_scores` : un dict {case: Counter(lettre -> occurrences)} —
      le décompte *complet* de l'échantillonnage ci-dessus à chaque case
      blanche de la grille (pas seulement la lettre gagnante retenue pour
      `forced`), combinant les deux emplacements d'une case de croisement
      (chacun contribue son propre échantillon à cette même case). À la
      demande explicite de l'utilisateur : sert à `Filler._backtrack` à
      trier les mots candidats d'un emplacement par la somme des carrés de
      ces scores sur ses cases encore libres, du plus grand au plus petit,
      au lieu d'un tirage purement aléatoire — voir `Filler.__init__`/
      `_candidate_score`."""
    slots = extract_slots(grid, rows, cols)
    cell_to_slots = defaultdict(list)
    for slot_idx, cells in enumerate(slots):
        for cell in cells:
            cell_to_slots[cell].append(slot_idx)

    excluded = excluded_slots or set()
    eligible = []  # (compte, case, lettre) — cases dépassant LETTER_BIAS_MIN_COUNT
    letter_scores = defaultdict(Counter)
    for slot_idx, cells in enumerate(slots):
        length = len(cells)
        idx = index.get(length)
        if not idx or not idx["words"]:
            continue
        sample = rng.choices(idx["words"], k=sample_size)
        for pos, cell in enumerate(cells):
            counts = Counter(word[pos] for word in sample)
            letter_scores[cell].update(counts)
            letter, count = counts.most_common(1)[0]
            if count > LETTER_BIAS_MIN_COUNT and slot_idx not in excluded:
                eligible.append((count, cell, letter))
    rng.shuffle(eligible)

    total_white = sum(row.count(WHITE) for row in grid)
    target = round(total_white * force_fraction)
    forced = {}
    used_slots = set()
    for count, cell, letter in eligible:
        if len(forced) >= target:
            break
        if cell in forced:
            continue
        touching = cell_to_slots[cell]
        if any(slot_idx in used_slots for slot_idx in touching):
            continue
        forced[cell] = letter
        used_slots.update(touching)
    return forced, dict(letter_scores)


def try_fill(grid, rows, cols, index, rng, deadline_checks=None, diagnostics=None,
             forced_letters=None, letter_scores=None, preseed_assignment=None,
             excluded_slots=None, cancel_event=None, batch_abandoned_event=None,
             attempt_done_event=None):
    """`preseed_assignment`/`excluded_slots` (both `None` by default — every
    pre-existing caller is unaffected), à la demande explicite de
    l'utilisateur : mécanique de reprise « telle-quelle » d'un palier sur
    l'autre (voir generate_grid/_pattern_continue), distincte de la reprise
    par nettoyage (`_build_retry_seed`) déjà en place. `preseed_assignment`,
    si fourni, initialise `Filler.assignment` (et `used_words`/
    `best_assignment`/`best_assigned_count` en conséquence) avec l'état déjà
    connu du palier précédent au lieu de partir d'une grille vide — chaque
    emplacement déjà assigné y reste verrouillé, `_backtrack` ne le remet
    jamais en question. `excluded_slots` (voir `Filler.excluded_slots`)
    ignore, le temps de cette recherche, tout emplacement déjà identifié
    comme impossible au palier précédent — sans cette exclusion, le simple
    contrôle de domaine de `_backtrack` (qui s'exécute pour *tous* les
    emplacements non assignés avant même de choisir lequel traiter)
    ferait échouer toute la recherche dès le premier appel, même pour des
    emplacements sans aucun rapport avec celui-là.

    Avec `excluded_slots` non vide, un emplacement volontairement exclu ne
    peut plus jamais être assigné par cette recherche : `Filler.solve()` peut
    donc renvoyer `True` (au sens interne de `_backtrack` : plus aucun
    emplacement *non exclu* à traiter) alors que la grille reste
    incomplète — ce n'est pas une réussite véritable pour l'appelant.
    `truly_complete` (ci-dessous) fait la distinction : seule une grille
    entièrement remplie, exclusions comprises, compte comme un succès
    réel ; sinon, les diagnostics sont renseignés comme pour tout autre
    échec (voir generate_grid, qui a besoin de `assignment`/
    `impossible_slots` à jour pour décider s'il reste un emplacement où
    ajouter un mot ou s'il faut nettoyer). Sans `excluded_slots` (le cas de
    tout appelant existant), `truly_complete` coïncide exactement avec le
    `solved` interne — aucun changement de comportement pour eux.

    `diagnostics`, if given a dict, is filled in with data useful to
    understand *why* a fill attempt failed (see generate_grid's
    "pattern_failed" logging): `slot_count`/`length_counts` (the CSP's
    shape, independent of the word list), `checks`/`reason` (how far
    the search got — "search_exhausted" means every candidate was tried
    within budget and none worked, a genuine dead end for this pattern;
    "deadline_exceeded" means the `deadline_checks` budget ran out first,
    inconclusive; "abandoned_too_unfillable" means the search itself gave
    up early, well before either of the above, because more than
    `UNFILLABLE_ABANDON_FRACTION` (30%) of the grid's white cells already
    belonged to a slot deemed impossible (see `Filler.abandoned`) — at
    that point continuing to search elsewhere on the same pattern isn't
    worth the remaining budget; "interrupted_other_attempt_done" means this
    attempt was cut short because another attempt of the same palier
    already produced the palier's outcome (success or failure) — see
    `attempt_done_event`/`generate_grid`, at the user's explicit request to
    stop waiting for every parallel attempt once one has already answered;
    "no_slots" means the pattern had no white run >= 3 cells at all), and,
    on failure only, `example_grid` — a snapshot
    (`build_partial_letters_grid`) of the most-filled-in state the search
    ever reached before giving up, at the user's explicit request, so a
    failed attempt can be shown to the user (not just logged) instead of
    disappearing with no visible trace of what was tried — and
    `impossible_cells` (`Filler.impossible_zone_cells()`), the cells of
    whichever unassigned slot(s), at that same snapshot, had no candidate
    word left at all, for the UI to highlight (may be empty — see that
    method's own docstring for why).

    `forced_letters`, if given (see `sample_letter_biases`), seeds the
    search with a statistically-guessed letter for a subset of cells,
    treated by `Filler` as a soft hint rather than a real assignment (see
    `Filler._domain`) — also overlaid onto `example_grid` on failure (cells
    no real assignment already covers), with their own coordinates listed
    separately in `forced_cells`, at the user's explicit request, so the UI
    can show *which* letters in the preview are statistical hints rather
    than real progress from the search.

    On failure, `diagnostics` also carries the raw `assignment` (`Filler.
    best_assignment`, one word-or-None per slot) and `impossible_slots`
    (`Filler.impossible_zone_slots()`, slot *indices* rather than cells) —
    at the user's explicit request, for `generate_grid`'s cross-palier
    retry-seed mechanism (`_build_retry_seed`) to work from the real
    slot/word structure directly rather than re-deriving it from the
    letter-grid shown in the UI (which also overlays purely statistical
    `forced_letters` hints, indistinguishable there from a real placed
    letter).

    `deadline_checks` (`None` par défaut) est calculé à partir de la taille
    de la grille, à la demande explicite de l'utilisateur : `largeur ×
    hauteur × 2000` (relevé depuis × 100 puis × 300, toujours à la demande
    explicite de l'utilisateur), plutôt qu'un budget fixe (200 000, sans
    rapport avec la taille réelle de la grille recherchée — beaucoup trop
    généreux pour une toute petite grille, potentiellement insuffisant pour
    une très grande). `None` plutôt qu'une valeur calculée directement dans
    la signature de la fonction : `rows`/`cols` ne sont connus qu'une fois
    la fonction appelée, une valeur par défaut ne peut pas dépendre d'un
    autre paramètre en Python. `minimize_black_squares` (étape 3, une fois
    la grille déjà remplie) garde son propre budget, bien plus petit
    (`deadline_checks=6_000`), explicitement transmis à chacun de ses
    appels à `try_fill` — cette formule ne s'applique donc qu'à un appelant
    qui n'a jamais fourni son propre budget, jamais à ce cas-là."""
    if deadline_checks is None:
        deadline_checks = rows * cols * 2000
    slots = extract_slots(grid, rows, cols)
    if diagnostics is not None:
        diagnostics["slot_count"] = len(slots)
        diagnostics["length_counts"] = dict(sorted(Counter(len(s) for s in slots).items()))
    if not slots:
        if diagnostics is not None:
            diagnostics["checks"] = 0
            diagnostics["reason"] = "no_slots"
            diagnostics["example_grid"] = grid
            diagnostics["impossible_cells"] = []
            diagnostics["forced_cells"] = []
            diagnostics["assigned_letter_count"] = 0
            diagnostics["assignment"] = []
            diagnostics["impossible_slots"] = []
            diagnostics["locked_cells"] = []
        return None
    # Cases déjà verrouillées *avant même* de lancer cette recherche (voir
    # `preseed_assignment` ci-dessus) — à la demande explicite de
    # l'utilisateur, pour que l'aperçu web puisse les distinguer visuellement
    # des lettres statistiques de `forced_cells` (sample_letter_biases) :
    # une case verrouillée porte une lettre réelle, confirmée par la
    # recherche d'un palier précédent, pas une simple supposition. Calculé
    # une fois ici, avant que `solve()` ne s'exécute, puisque `preseed_
    # assignment` ne change jamais au cours de cette recherche (un
    # emplacement qui y est déjà assigné n'est jamais reconsidéré — voir
    # `Filler._backtrack`, qui ne retient que les emplacements encore à
    # `None`).
    locked_cells = (
        sorted({cell for i, word in enumerate(preseed_assignment) if word is not None
                for cell in slots[i]})
        if preseed_assignment is not None else []
    )
    filler = Filler(slots, index, rng, forced_letters=forced_letters, letter_scores=letter_scores,
                     excluded_slots=excluded_slots, cancel_event=cancel_event,
                     batch_abandoned_event=batch_abandoned_event,
                     attempt_done_event=attempt_done_event)
    if preseed_assignment is not None:
        filler.assignment = list(preseed_assignment)
        filler.used_words = {w for w in preseed_assignment if w is not None}
        filler.best_assignment = list(preseed_assignment)
        filler.best_assigned_count = sum(1 for w in preseed_assignment if w is not None)
    filler.exclude_immediately_impossible_slots()
    solved_internally = filler.solve(deadline_checks)
    # Voir la docstring ci-dessus : avec `excluded_slots` non vide, `solved_
    # internally` (le sens interne de _backtrack — plus aucun emplacement
    # *non exclu* à traiter) ne suffit pas à garantir une grille complète.
    # Sans `excluded_slots` (tout appelant existant), les deux coïncident
    # toujours exactement.
    truly_complete = all(w is not None for w in filler.assignment)
    if diagnostics is not None:
        diagnostics["checks"] = filler.checks
        diagnostics["reason"] = (
            "solved" if truly_complete
            else "interrupted_other_attempt_done" if filler.interrupted_by_sibling
            else "abandoned_too_unfillable" if filler.abandoned
            else "deadline_exceeded" if filler.checks >= deadline_checks
            else "blocked_on_excluded_slot" if solved_internally
            else "search_exhausted"
        )
        if not truly_complete:
            example_grid, forced_cells, assigned_letter_count = build_partial_letters_grid(
                grid, slots, filler.best_assignment, forced_letters
            )
            diagnostics["example_grid"] = example_grid
            diagnostics["forced_cells"] = forced_cells
            diagnostics["impossible_cells"] = filler.impossible_zone_cells()
            diagnostics["assigned_letter_count"] = assigned_letter_count
            diagnostics["assignment"] = list(filler.best_assignment)
            diagnostics["impossible_slots"] = filler.impossible_zone_slots()
            diagnostics["locked_cells"] = locked_cells
    if truly_complete:
        return slots, filler.assignment
    return None


# ---------- Minimisation locale des cases noires ----------

def minimize_black_squares(grid, result, rows, cols, index, rng, deadline_checks=6_000,
                            cancel_event=None):
    """Retire itérativement des cases noires une par une (indépendamment,
    sans les apparier avec une case miroir — cohérent avec make_pattern,
    qui ne pose plus les cases noires par paires symétriques) tant que la
    grille reste remplissable, en gardant la dernière solution connue (on
    évite ainsi un nouveau try_fill final qui pourrait échouer sur une
    recherche difficile alors qu'une solution vient d'être trouvée).

    Appelle `is_structurally_valid` avec `min_interior_free=1` plutôt que
    la valeur par défaut (3) : cette fonction ne fait que RETIRER des
    cases noires (jamais en ajouter), ce qui ne peut qu'allonger les
    emplacements existants, jamais en créer un nouveau plus court —
    l'invariant qu'elle doit vraiment préserver est la connexité et
    l'absence de case orpheline, pas la préférence esthétique de
    `make_pattern` pour des emplacements d'au moins 3 cases. Nécessaire
    depuis que `_place_black_cells` peut légitimement laisser un
    emplacement interne de 1 ou 2 cases quand c'est le seul moyen d'éviter
    l'adjacence à une autre case noire (voir make_pattern) : sans ce
    changement, une grille produite ainsi violerait `is_structurally_valid`
    par défaut dès le premier appel ici, indépendamment de la case
    réellement retirée, bloquant toute minimisation.

    `cancel_event` (voir GenerationCancelled) est vérifié entre deux cases
    noires candidates — cette phase est normalement rapide (chaque essai
    est borné par `deadline_checks`, bien plus petit que la recherche
    principale), mais une grande grille peut avoir beaucoup de cases
    noires à essayer, donc ce point de contrôle reste utile plutôt que
    d'attendre la fin de toute la boucle."""
    slots, assignment = result
    improved = True
    while improved:
        improved = False
        black_cells = [(r, c) for r in range(rows) for c in range(cols) if grid[r][c] == BLACK]
        rng.shuffle(black_cells)
        for (r, c) in black_cells:
            if cancel_event is not None and cancel_event.is_set():
                raise GenerationCancelled()
            if grid[r][c] != BLACK:
                continue
            saved = grid[r][c]
            grid[r][c] = WHITE
            if is_structurally_valid(grid, rows, cols, min_interior_free=1):
                new_result = try_fill(grid, rows, cols, index, rng, deadline_checks,
                                       cancel_event=cancel_event)
                if new_result is not None:
                    slots, assignment = new_result
                    improved = True
                    continue
            grid[r][c] = saved
    return grid, slots, assignment


# ---------- Numérotation des mots (pour les définitions) ----------

def build_word_entries(grid, rows, cols, slots, assignment):
    """Numérote les cases de départ selon la convention standard des mots
    croisés (lecture gauche->droite puis haut->bas, un numéro par case de
    départ, partagé entre le mot horizontal et/ou vertical qui y commence).
    Retourne une liste de {number, direction, row, col, length, answer}."""
    starts = defaultdict(list)
    for i, cells in enumerate(slots):
        cell = cells[0]
        direction = "across" if len(cells) > 1 and cells[1][0] == cell[0] else "down"
        starts[cell].append((direction, i))

    numbers = {}
    counter = 1
    for r in range(rows):
        for c in range(cols):
            if (r, c) in starts:
                numbers[(r, c)] = counter
                counter += 1

    entries = []
    for cell, items in starts.items():
        number = numbers[cell]
        for direction, i in items:
            entries.append({
                "number": number,
                "direction": direction,
                "row": cell[0],
                "col": cell[1],
                "length": len(slots[i]),
                "answer": assignment[i],
            })
    entries.sort(key=lambda e: (e["number"], e["direction"]))
    return entries


# ---------- Affichage ----------

def print_grid(grid):
    for row in grid:
        print(" ".join(row))


# ---------- Génération complète (utilisable en bibliothèque, ex. serveur API) ----------

def build_letters_grid(rows, cols, slots, assignment):
    letters = [[BLACK] * cols for _ in range(rows)]
    for cells, word in zip(slots, assignment):
        for (r, c), ch in zip(cells, word):
            letters[r][c] = ch
    return letters


def build_partial_letters_grid(grid, slots, assignment, forced_letters=None):
    """Comme build_letters_grid, mais pour un remplissage abandonné en
    cours de route (voir try_fill, diagnostics["example_grid"]) — à la
    demande explicite de l'utilisateur, pour donner un aperçu de ce qui a
    été tenté avant qu'une tentative échoue, affiché côté interface.
    Contrairement à build_letters_grid, `assignment` peut contenir des
    `None` (emplacement jamais atteint par la recherche) : part du motif
    noir/blanc réel (`grid`, où chaque case blanche non encore déterminée
    reste WHITE) plutôt que de tout initialiser à BLACK en supposant que
    chaque emplacement sera rempli.

    `forced_letters` (voir sample_letter_biases), si fourni, est superposé
    sur les cases qu'aucune affectation réelle ne couvre déjà — à la
    demande explicite de l'utilisateur, pour que l'aperçu d'une tentative
    échouée montre aussi les indices statistiques, pas seulement les
    lettres réellement posées par la recherche. Une affectation réelle
    l'emporte toujours sur la lettre affichée (une case ne peut de toute
    façon jamais contredire l'indice qui l'a contrainte — voir
    Filler._domain — mais l'ordre de priorité reste explicite ici).

    Retourne (grille_lettres, cases_forcées, nombre_lettres_posées) — le 2e
    élément est la liste triée de TOUTES les cases de `forced_letters`,
    qu'elles soient encore visibles dans la grille retournée ou déjà
    recouvertes par une affectation réelle — à la demande explicite de
    l'utilisateur, après avoir constaté qu'une version précédente (ne
    renvoyant que les cases "encore non confirmées") faisait quasiment
    disparaître l'affichage des lettres forcées côté interface au fur et à
    mesure que la recherche progressait, alors que le sondage statistique
    lui-même restait stable : mesuré en direct, jusqu'à 7 cases forcées par
    sample_letter_biases à chaque tentative, contre parfois 0 encore
    "visibles" une fois filtrées. Voir try_fill, diagnostics["forced_cells"],
    et script.js pour l'encadrement affiché sur toutes ces cases, y compris
    celles qui montrent désormais une vraie lettre plutôt que l'indice
    d'origine. Le 3e élément (`len(covered)`) compte les cases couvertes par
    une affectation *réelle* uniquement (jamais les indices statistiques de
    `forced_letters`) — à la demande explicite de l'utilisateur, pour classer
    plusieurs tentatives échouées entre elles par leur progrès réel (voir
    try_fill, diagnostics["assigned_letter_count"], et generate_grid)."""
    letters = [row[:] for row in grid]
    covered = set()
    for cells, word in zip(slots, assignment):
        if word is None:
            continue
        for (r, c), ch in zip(cells, word):
            letters[r][c] = ch
            covered.add((r, c))
    if forced_letters:
        for cell, letter in forced_letters.items():
            if cell not in covered:
                r, c = cell
                letters[r][c] = letter
    return letters, (sorted(forced_letters) if forced_letters else []), len(covered)


def _clean_blocked_slots(slots, assignment, impossible_slots, locked_letters=None,
                          exclude_impossible_locked=False):
    """Étapes 1 et 2 de `_build_retry_seed` (voir sa propre docstring pour
    l'historique complet), extraites dans leur propre fonction à la demande
    explicite de l'utilisateur : "à la fin d'un tour, nettoyer
    automatiquement les emplacements bloqués, mais pas les noires." —
    `generate_grid` appelle désormais cette fonction seule, à la fin de
    *chaque* palier (qu'il reparte "telle quelle" ou par un nettoyage
    complet), pour retirer tout mot croisant directement un emplacement
    impossible, sans jamais toucher aux cases noires elles-mêmes ni
    régénérer de motif — `_build_retry_seed` (le nettoyage complet, motif
    et cases noires compris) l'appelle en interne comme sa propre première
    étape, plutôt que de dupliquer ce calcul.

    Recompose d'abord, si `locked_letters` est fourni, le mot déjà
    entièrement déterminé de tout emplacement encore à `None` mais dont
    toutes les cases sont verrouillées (voir `_build_retry_seed`'s propre
    docstring pour le bug que ce préremplissage corrige) — un no-op sans
    `locked_letters` (le cas du nettoyage "telle quelle" en fin de palier,
    qui a déjà un `assignment` complet, mot par mot, sans rien à
    recomposer). Retire ensuite tout emplacement *assigné* qui partage une
    case avec un emplacement de `impossible_slots` (un niveau seulement,
    pas de propagation en cascade), et construit `confirmed`
    ({case: lettre}) à partir de ce qui reste.

    Retourne `(cleaned_assignment, confirmed)` — `cleaned_assignment` est
    une nouvelle liste (jamais une mutation de `assignment` reçu), avec un
    `None` explicite pour chaque emplacement retiré, prête à servir
    directement de `preseed_assignment` au palier suivant."""
    if locked_letters:
        assignment = list(assignment)
        impossible_set = set(impossible_slots) if exclude_impossible_locked else set()
        for i, cells in enumerate(slots):
            if (
                assignment[i] is None
                and i not in impossible_set
                and all(cell in locked_letters for cell in cells)
            ):
                assignment[i] = "".join(locked_letters[cell] for cell in cells)

    cell_to_slots = defaultdict(list)
    for i, cells in enumerate(slots):
        for cell in cells:
            cell_to_slots[cell].append(i)

    to_remove = set()
    for i in impossible_slots:
        for cell in slots[i]:
            for j in cell_to_slots[cell]:
                if j != i and assignment[j] is not None:
                    to_remove.add(j)

    cleaned_assignment = [
        None if i in to_remove else word for i, word in enumerate(assignment)
    ]

    confirmed = {}
    for i, word in enumerate(cleaned_assignment):
        if word is None:
            continue
        for cell, ch in zip(slots[i], word):
            confirmed[cell] = ch

    return cleaned_assignment, confirmed


def _impossible_cell_groups(slots, assignment, impossible_slots):
    """Cases des emplacements de `impossible_slots`, réparties entre celles
    qui portent déjà une lettre (un mot croisé assigné passe par cette
    case) et celles qui sont encore blanches — à la demande explicite de
    l'utilisateur, pour `_lock_one_impossible_cell` ci-dessous
    ("privilégier de noircir une case blanche, sinon une case avec une
    lettre"). `assignment` doit être l'affectation **brute**, avant tout
    nettoyage (`_clean_blocked_slots` retire ensuite tout mot croisant un
    emplacement impossible — une fois ça fait, ces cases sont *toutes*
    redevenues blanches sans exception, ce qui rendrait cette distinction
    impossible à observer si elle était calculée après coup plutôt
    qu'avant). Retourne `(blank_cells, lettered_cells)`, deux ensembles
    disjoints dont l'union est l'ensemble des cases de tous les
    `impossible_slots` donnés."""
    raw_letters = {}
    for i, word in enumerate(assignment):
        if word is None:
            continue
        for cell, ch in zip(slots[i], word):
            raw_letters[cell] = ch
    impossible_cells = {cell for i in impossible_slots for cell in slots[i]}
    blank_cells = {cell for cell in impossible_cells if cell not in raw_letters}
    lettered_cells = impossible_cells - blank_cells
    return blank_cells, lettered_cells


def _lock_one_impossible_cell(grid, rows, cols, blank_cells, lettered_cells, rng):
    """Verrouille UNE case noire (une seule) au hasard parmi les cases
    d'emplacements injouables données, à la demande explicite de
    l'utilisateur : "ajouter une case noire (une seule en tout) au hasard
    sur les cases qui étaient dans les emplacements injouables (tentative
    de ne pas reproduire les mêmes erreurs en verrouillant progressivement
    les configurations problématiques)." Privilégie `blank_cells` (voir
    `_impossible_cell_groups`) si ce groupe n'est pas vide ; ne se rabat
    sur `lettered_cells` que s'il l'est — à la demande explicite de
    l'utilisateur, une précision apportée après coup ("s'il existe des
    cases encore blanches ... privilégier de noircir une case blanche.
    Sinon, noircir une case avec une lettre"). Mute `grid` en place ; ne
    retourne rien. Les candidates du groupe retenu sont essayées dans un
    ordre mélangé (le `rng` fourni, reproductible) jusqu'à en trouver une
    qui préserve `is_structurally_valid(min_interior_free=1)` (l'invariant
    absolu de ce fichier — connexité, aucune case blanche isolée) ; si
    aucune ne convient, `grid` n'est pas modifiée du tout (cas limite
    accepté, pas d'essai de repli sur l'autre groupe)."""
    candidates = list(blank_cells) or list(lettered_cells)
    rng.shuffle(candidates)
    for (r, c) in candidates:
        if grid[r][c] != WHITE:
            continue
        grid[r][c] = BLACK
        if is_structurally_valid(grid, rows, cols, min_interior_free=1):
            return
        grid[r][c] = WHITE


def _build_retry_seed(grid, rows, cols, slots, assignment, impossible_slots, locked_letters=None,
                       exclude_impossible_locked=False):
    """Construit le point de départ du palier suivant à partir de la
    meilleure tentative échouée du palier courant, à la demande explicite de
    l'utilisateur — nouvel algorithme de reprise entre paliers, distinct du
    mécanisme de "patch" essayé puis entièrement abandonné plus tôt dans
    l'historique de ce projet (voir la SKILL project-best-practices) : celui-
    là retouchait la MÊME tentative en ajoutant une case noire à la fois et
    en relançant une recherche complète depuis zéro à chaque fois ; celui-ci
    ne relance jamais la même tentative — il conserve ce qui a déjà été
    résolu avec confiance (des lettres réellement posées, pas une simple
    case noire de plus) et ne fait porter la prochaine recherche que sur ce
    qui reste réellement incertain.

    Trois étapes, dans l'ordre exact demandé :

    1. **Retirer les mots directement connectés aux emplacements en échec.**
       `impossible_slots` (voir Filler.impossible_zone_slots) désigne les
       emplacements non assignés dont le domaine était vide à l'instant où
       la recherche a le plus progressé (`best_assignment`) — c'est
       précisément la cause du blocage. Un emplacement *assigné* qui
       partage une case avec l'un d'eux (donc qui le croise, et dont la
       lettre partagée fait partie des contraintes qui ont vidé son domaine)
       est retiré à son tour : `to_remove` ne va pas plus loin qu'un niveau
       (« directement » — pas de propagation en cascade), à la demande
       explicite de l'utilisateur.
    2. **Ce qui reste devient les lettres pré-définies du prochain palier.**
       Chaque case encore couverte par un emplacement assigné (donc ni
       impossible ni retiré à l'étape 1) devient une entrée `{case: lettre}`
       dans le dict retourné — les seules lettres considérées comme du
       vrai progrès, jamais un indice statistique de `forced_letters` (qui
       n'a jamais été un fait acquis).
    3. **Conserver toute case noire existante adjacente à une lettre
       confirmée ; rouvrir toutes les autres.** Ce critère a une histoire en
       plusieurs temps. Une première version ne gardait noires que les deux
       cases qui bornent effectivement chaque mot restant — immédiatement
       avant sa première lettre et immédiatement après sa dernière, dans la
       direction propre de ce mot (horizontale ou verticale, jamais
       l'autre) — rouvrant toute case simplement adjacente sur le côté
       (au-dessus/en dessous d'une lettre du milieu d'un mot horizontal, par
       exemple), au motif qu'une telle case ne borne ce mot-là en rien.
       Cela laissait plus de marge de manœuvre (et donc plus de diversité
       entre les PARALLEL_ATTEMPTS tentatives parallèles du palier suivant)
       au placement de nouvelles cases noires — mais s'est révélé être la
       cause d'un problème différent, diagnostiqué par l'utilisateur à
       partir d'un cas réel : rouvrir une case latérale à côté d'une lettre
       confirmée ouvre un passage qui peut créer, dans l'autre direction, un
       tout nouvel emplacement immédiatement contraint par cette lettre (et
       potentiellement par d'autres lettres confirmées voisines) — un
       emplacement susceptible de n'avoir que très peu ou aucun mot candidat
       réel, obligeant le pré-remplissage du palier suivant à noircir
       beaucoup plus que nécessaire pour compenser (voir le
       `_prefill_unfillable_slots` ci-dessus, et le bug de colonne
       entièrement noire qu'il a fini par produire). Élargi, à la demande
       explicite de l'utilisateur, à la règle la plus large possible : toute
       case noire actuelle orthogonalement adjacente (les 4 côtés) à
       *n'importe quelle* case de `confirmed` reste noire ; seules les cases
       ne touchant aucune lettre confirmée du tout sont rouvertes. Cette
       règle englobe strictement la version bornes-de-mot (la case qui
       borne un mot est elle-même adjacente à sa première/dernière lettre),
       donc plus besoin de calculer les deux cas séparément.

       Un resserrement à deux branches (case bornant un mot, OU touchant au
       moins deux lettres confirmées à la fois) a été essayé un temps, puis
       abandonné presque aussitôt à la demande explicite de l'utilisateur,
       qui a reformulé la règle voulue plus simplement : "conserver les
       cases noires dont un des 4 côtés ouvre un emplacement où il y a une
       lettre (ça couvre le cas des cases en bout de mot) ; supprimer toutes
       les autres." Une première implémentation de cette reformulation ne
       vérifiait encore que la case immédiatement voisine — revenant, à tort,
       à l'exacte règle la plus large déjà en place. Corrigé à la demande
       explicite de l'utilisateur, qui a précisé le point manqué : "il peut
       y avoir des blancs entre la case noire et la lettre" — la vérification
       porte sur l'*emplacement entier* de chaque côté (la suite de cases
       blanches, potentiellement longue, jusqu'à la prochaine case noire ou
       le bord), pas seulement sur la case immédiatement adjacente —
       réutilisant le même parcours de côté que `_new_black_cell_breaks_
       locked_slot` (une marche le long des cases blanches consécutives dans
       chaque direction jusqu'à une case noire ou le bord), cette fois pour
       chercher une lettre confirmée quelque part dans le parcours plutôt
       que pour compter des candidats du dictionnaire.

       Cette version "un seul côté suffit" a immédiatement été resserrée
       une fois de plus, à la demande explicite de l'utilisateur, qui a
       identifié un cas concret qu'elle protégeait à tort : une case noire
       qui *voit* une lettre d'un seul côté (par exemple en croisant, à
       distance, un mot assigné dans l'autre sens) sans être elle-même la
       borne (début/fin) du mot correspondant ne protège en réalité rien —
       la rouvrir ne menace l'intégrité d'aucun mot existant, puisque la
       lettre aperçue appartient à un mot qui ne s'étend pas jusqu'à cette
       case dans sa propre direction. La règle finale ne conserve donc une
       case noire que dans deux cas, une union de deux conditions
       indépendantes : (1) elle borne effectivement un mot restant —
       immédiatement avant sa première lettre ou immédiatement après sa
       dernière, dans la direction propre de ce mot (le même calcul que la
       toute première version de cette étape, jamais retiré, seulement
       complété) ; (2) elle a une lettre confirmée *des deux côtés à la
       fois* d'un même axe — en haut ET en bas, ou à gauche ET à droite (pas
       besoin des deux axes en même temps) — une case "prise en sandwich"
       entre deux segments de mots sur le même axe, où la rouvrir
       fusionnerait deux emplacements distincts en un seul qui ne
       correspond peut-être à aucun mot réel, perturbant les deux côtés à
       la fois. Une case qui ne voit une lettre que d'un seul côté d'un
       axe, sans en borner le mot, est désormais rouverte — y compris le
       cas de croisement à distance qui motivait le passage à la version
       précédente ; ce cas-là n'a jamais menacé l'intégrité d'un mot
       existant, seule la version "un seul côté suffit" le traitait à tort
       comme s'il le fallait. Confirmé par l'utilisateur avec une
       reformulation équivalente : "une case noire se trouvant quelque part
       entre 2 mots existants (horizontalement ou verticalement) doit être
       conservée ; une case noire se trouvant au bout d'un mot (début ou
       fin) doit être conservée ; les autres cases noires peuvent être
       supprimées" — exactement les conditions (2) et (1) ci-dessus. Vérifié
       avec trois grilles construites à la main : une case ne voyant une
       lettre que d'un seul côté (croisement à distance, pas de borne) se
       rouvre désormais ; une case bornant effectivement un mot reste
       noire ; une case prise en sandwich entre deux mots assignés sur le
       même axe vertical reste noire.

       Exception ajoutée à la demande explicite de l'utilisateur : une case
       noire par ailleurs candidate à la réouverture (non adjacente à une
       lettre confirmée) reste tout de même noire si ses 4 voisines (haut,
       bas, gauche, droite) sont *elles-mêmes* toutes noires dans la grille
       d'origine (`_fully_surrounded_by_black`) — la rouvrir créerait une
       case blanche isolée des 4 côtés, un "trou d'une seule lettre" qui
       violerait l'invariant absolu établi ailleurs dans ce fichier (voir
       is_structurally_valid) : une case blanche ne peut jamais être courte
       (1 lettre) dans les deux sens à la fois. Une case en bord de grille
       ne peut jamais remplir cette condition (au moins un voisin hors
       grille), donc cette exception ne s'applique qu'à une case
       strictement intérieure — cohérent avec le fait que ce risque de trou
       isolé n'existe que loin du bord.

    Retourne `(nouveau_motif, lettres_verrouillées)` — `nouveau_motif` sert
    de `seed_grid` et `lettres_verrouillées` de `locked_letters`/
    `forced_letters` à `make_pattern`/`_pattern_attempt` du palier suivant
    (voir generate_grid).

    Bug réel trouvé et corrigé, à partir d'un cas concret fourni par
    l'utilisateur ("beaucoup de lettres, peu de conflit, et l'étape
    d'après, presque tout a été supprimé") et confirmé par un audit
    multi-paliers en direct (pas seulement raisonné) : `assignment` (le
    `best_assignment` du `Filler` de CETTE tentative) ne contient un mot
    pour un emplacement que si le backtracking a réellement fini par
    l'assigner explicitement pendant SA PROPRE recherche — un emplacement
    déjà entièrement déterminé par les lettres verrouillées du palier
    précédent (`locked_letters`, passées en tant que contrainte dure) n'est
    JAMAIS "réassigné" par `_backtrack` si la recherche échoue avant même
    d'atteindre cet emplacement (le cas `checks=1`/`reason="search_
    exhausted"` très rapide : le tout premier domaine vérifié est déjà
    vide). Dans ce cas, `assignment` revient entièrement à `None`, y
    compris pour les emplacements déjà verrouillés, alors que ces lettres
    étaient parfaitement acquises — l'étape 2 ci-dessus les jetait donc à
    tort, systématiquement, à chaque échec immédiat de ce type. Confirmé en
    direct : sur un audit de 8 paliers enchaînés (grille réelle, dictionnaire
    réel), 3 des 8 (paliers 2, 4, 7) montraient `assigned_slots=0` pour les
    6 candidats alors que le palier précédent avait verrouillé 65, 44 et 69
    lettres respectivement — la totalité disparaissait, pas parce qu'elle
    croisait un emplacement impossible, mais parce qu'elle n'apparaissait
    jamais du tout dans `assignment`. Corrigé en traitant tout emplacement
    entièrement couvert par `locked_letters` comme s'il avait été assigné
    au mot que ces lettres épellent, avant d'appliquer exactement les mêmes
    règles (étapes 1 à 3) qu'à n'importe quel autre mot réellement assigné
    — un emplacement verrouillé qui croise un emplacement impossible reste
    retiré comme n'importe quel autre, il n'est pas protégé au-delà de sa
    part légitime.

    Ce premier correctif a lui-même introduit un second bug, trouvé par le
    même type d'audit multi-paliers en direct : un emplacement peut être à
    la fois entièrement couvert par `locked_letters` *et* lui-même présent
    dans `impossible_slots` — la combinaison exacte de lettres verrouillées
    à cet emplacement ne correspond, en fait, à aucun mot réel du
    dictionnaire (c'est précisément *pourquoi* il est impossible). Le
    correctif ci-dessus le "réassignait" quand même depuis `locked_letters`
    sans vérifier ce cas, préservant indéfiniment cette combinaison
    invalide d'un palier à l'autre — puisque cet emplacement n'est jamais
    dans `to_remove` (qui ne retire que les AUTRES emplacements croisant un
    emplacement impossible, jamais l'emplacement impossible lui-même), rien
    ne changeait plus jamais d'un palier au suivant, un vrai point fixe
    bloqué. Reproduit en direct : sur une grille bloquée à ce stade précis,
    29 lettres verrouillées et 2 emplacements impossibles (chacun 2 cases,
    déjà entièrement verrouillées) restaient **identiques bit à bit** sur
    12 paliers consécutifs, jusqu'à épuiser les 40 tentatives sans jamais
    trouver de solution — un cas qui réussissait auparavant.

    Corriger ceci en excluant *systématiquement* un tel emplacement de la
    réassignation (`exclude_impossible_locked=True` en permanence) a été
    essayé, puis affiné après avoir constaté, par comparaison directe
    avant/après sur plusieurs scénarios réels, que ce n'était pas non plus
    la bonne réponse partout : un scénario différent (10×10, vocabulaire
    volontairement restreint à 400 mots) qui réussissait sans cette
    exclusion s'est mis à échouer systématiquement avec elle — l'exclusion,
    appliquée à chaque palier sans distinction, retire aussi des emplacements
    dont la présence ne bloquait en réalité rien du tout, gaspillant du
    contenu par ailleurs récupérable. `exclude_impossible_locked` (`False`
    par défaut, donc le comportement normal — sans exclusion, qui gagne dans
    la majorité des scénarios réels observés) n'est donc utilisé qu'en
    dernier recours, à la demande explicite de l'utilisateur : seulement
    quand `generate_grid` détecte qu'un palier n'a produit *aucun*
    changement par rapport au précédent (les lettres confirmées sont
    rigoureusement identiques, un vrai point fixe), il relance ce même
    nettoyage une seconde fois pour ce palier, cette fois avec
    `exclude_impossible_locked=True`, uniquement pour débloquer ce cas
    précis plutôt que d'appliquer la règle plus agressive partout."""
    assignment, confirmed = _clean_blocked_slots(
        slots, assignment, impossible_slots, locked_letters=locked_letters,
        exclude_impossible_locked=exclude_impossible_locked,
    )

    def _direction_has_confirmed_letter(r, c, dr, dc):
        rr, cc = r + dr, c + dc
        while 0 <= rr < rows and 0 <= cc < cols and grid[rr][cc] == WHITE:
            if (rr, cc) in confirmed:
                return True
            rr += dr
            cc += dc
        return False

    protected_black_cells = set()
    for i, word in enumerate(assignment):
        if word is None:
            continue
        cells = slots[i]
        direction = "across" if len(cells) > 1 and cells[1][0] == cells[0][0] else "down"
        dr, dc = (0, 1) if direction == "across" else (1, 0)
        (r0, c0), (r1, c1) = cells[0], cells[-1]
        for br, bc in ((r0 - dr, c0 - dc), (r1 + dr, c1 + dc)):
            if 0 <= br < rows and 0 <= bc < cols:
                protected_black_cells.add((br, bc))

    for r in range(rows):
        for c in range(cols):
            if grid[r][c] != BLACK:
                continue
            vertical_both = _direction_has_confirmed_letter(
                r, c, -1, 0
            ) and _direction_has_confirmed_letter(r, c, 1, 0)
            horizontal_both = _direction_has_confirmed_letter(
                r, c, 0, -1
            ) and _direction_has_confirmed_letter(r, c, 0, 1)
            if vertical_both or horizontal_both:
                protected_black_cells.add((r, c))

    def _fully_surrounded_by_black(r, c):
        for dr, dc in ((-1, 0), (1, 0), (0, -1), (0, 1)):
            rr, cc = r + dr, c + dc
            if not (0 <= rr < rows and 0 <= cc < cols) or grid[rr][cc] != BLACK:
                return False
        return True

    new_grid = [row[:] for row in grid]
    for r in range(rows):
        for c in range(cols):
            if new_grid[r][c] == BLACK and (r, c) not in protected_black_cells:
                if _fully_surrounded_by_black(r, c):
                    continue
                new_grid[r][c] = WHITE

    return new_grid, confirmed


# ---------- Tentatives (motif + remplissage) en parallèle ----------
#
# `index` (le lexique pré-indexé, potentiellement 100 000+ mots) est envoyé
# une seule fois par worker via l'initializer du pool, plutôt que repicklé à
# chaque tâche soumise — il ne change jamais pendant un generate_grid().
_worker_index = None
# Bouton "Stop" (voir CANCEL_CHECK_INTERVAL/Filler.__init__), à la demande
# explicite de l'utilisateur — comme `_worker_index` juste au-dessus, passé
# une seule fois par worker via l'initializer du pool plutôt qu'en argument
# de chaque tâche soumise. Nécessaire, pas juste une question de style :
# un `multiprocessing.Event` soumis comme argument ordinaire de
# `executor.submit(...)` a été constaté en direct comme provoquant
# `RuntimeError: Condition objects should only be shared between processes
# through inheritance` (la méthode de démarrage "spawn", par défaut sur
# macOS, ne partage jamais la mémoire par héritage — chaque tâche soumise
# est repicklée individuellement) ; le transmettre via l'initializer du
# pool, exactement comme `index`, est le moyen documenté et effectivement
# fonctionnel de partager ce genre d'objet avec des processus workers.
_worker_cancel_event = None
# Signal "tout le batch est bloqué" (voir Filler._backtrack et generate_grid
# ci-dessous), à la demande explicite de l'utilisateur : "quand une
# recherche arrive à une situation jugée 'bloquée', arrêter toutes les
# recherches du batch N, pour passer au batch N+1 sans attendre que toutes
# les recherches arrivent à une situation de blocage." Un seul
# `multiprocessing.Event`, créé une fois par `generate_grid()` (comme
# `cancel_event` juste au-dessus, et pour la même raison technique :
# passé une seule fois par worker via l'initializer du pool, jamais en
# argument de tâche soumise) mais *remis à zéro* par le processus parent
# au début de chaque palier — contrairement à `cancel_event`, qui ne se
# déclenche jamais qu'une fois pour toute la génération, ce signal-ci a un
# sens différent à chaque palier (un blocage constaté au palier N ne doit
# pas influencer le palier N+1). Positionné par n'importe quel worker dont
# le propre `Filler.abandoned` devient vrai (la règle des 30 %, voir
# UNFILLABLE_ABANDON_FRACTION) — vérifié par tous les autres workers du
# même batch, qui s'arrêtent alors eux aussi, sans attendre d'atteindre
# individuellement leur propre seuil d'abandon ou leur propre budget.
#
# N'est réellement transmis qu'à `_pattern_continue` (reprise "telle
# quelle"), jamais à `_pattern_attempt` (motif neuf) — un vrai bug trouvé
# en direct avant tout déploiement, pas seulement raisonné : les
# PARALLEL_ATTEMPTS tentatives d'un même palier `_pattern_attempt` génèrent
# chacune leur PROPRE motif indépendant (`make_pattern` avec son propre
# `rng`, sur le même `seed_grid`/`locked_letters` de départ mais avec des
# cases noires ajoutées différemment à chaque fois) — la conclusion "30 %
# de CE motif-ci est impossible" d'une tentative ne dit donc rien de
# fiable sur le motif, complètement différent, d'une autre tentative du
# même batch. Reproduit en direct sur la grille de référence 15×10 (seed
# 7, auparavant fiable) : appliquer ce signal aux deux mécanismes à la
# fois faisait échouer cette graine (`None` renvoyé après 200 paliers,
# alors qu'elle réussissait avant ce correctif) — désactiver le signal
# spécifiquement pour `_pattern_attempt` (en lui transmettant toujours
# `None` plutôt que ce global) restaure le succès, confirmant que le
# problème vient bien de cette contamination entre motifs indépendants.
# `_pattern_continue`, lui, fait exactement l'inverse par construction :
# toutes ses tentatives parallèles partagent RIGOUREUSEMENT le même motif
# et le même verrouillage (voir sa propre docstring, "le motif et le
# verrouillage restent rigoureusement identiques d'une tentative à
# l'autre") — seul l'ordre d'exploration diffère — donc la conclusion
# d'une tentative sur ce motif partagé reste pertinente pour les autres.
_worker_batch_abandoned_event = None
# "This palier already has its answer" signal (see Filler.attempt_done_event
# and generate_grid), at the user's explicit request: "interrupt every search
# as soon as one search finishes (success or failure) to move on to the next
# palier." Same technical constraint as `_worker_cancel_event`/
# `_worker_batch_abandoned_event` above (passed once per worker via the
# pool's initializer, never as a per-task argument), but — unlike
# `_worker_batch_abandoned_event` — passed to BOTH `_pattern_attempt` and
# `_pattern_continue`: this signal never makes any inference about a
# specific pattern's own prospects, it only means "the palier's decision is
# already made, stop searching regardless of what you would have found" —
# see Filler.__init__'s own docstring for the full reasoning.
_worker_attempt_done_event = None


def _init_worker(index, cancel_event=None, batch_abandoned_event=None, attempt_done_event=None):
    global _worker_index, _worker_cancel_event, _worker_batch_abandoned_event, \
        _worker_attempt_done_event
    _worker_index = index
    _worker_cancel_event = cancel_event
    _worker_batch_abandoned_event = batch_abandoned_event
    _worker_attempt_done_event = attempt_done_event


def _pattern_attempt(rows, cols, ratio, seed, force_letters_fraction=0.0,
                      seed_grid=None, locked_letters=None,
                      black_enrichment_fraction=POST_PREFILL_BLACK_FRACTION,
                      deadline_checks=None):
    """Une tentative indépendante (motif + remplissage CSP complet), exécutée
    dans un processus worker séparé — voir PARALLEL_ATTEMPTS/generate_grid().
    Chaque tentative a son propre `random.Random(seed)`, dérivé du seed
    global par l'appelant, pour rester reproductible tout en étant
    différente des autres tentatives du même palier. Retourne
    (grid, result, diagnostics) ; `result` est None en cas d'échec, même
    contrat que try_fill.

    `deadline_checks` (`None` par défaut) est transmis tel quel à
    `try_fill` — voir la docstring de `generate_grid` pour d'où vient cette
    valeur (le sélecteur "Mode" de l'interface web).

    `seed_grid`/`locked_letters` (tous deux `None` par défaut — chaque appel
    existant avant cette fonctionnalité continue de partir d'une grille
    vierge, sans aucune lettre déjà connue), à la demande explicite de
    l'utilisateur : point de départ construit par `_build_retry_seed` à
    partir de la meilleure tentative échouée du palier précédent (voir
    generate_grid) — `make_pattern` continue de poser des cases noires sur
    `seed_grid` plutôt que de repartir d'une grille blanche (avec
    `locked_letters` exclu de son propre pool de candidates, pour ne
    jamais écraser une lettre déjà confirmée), et `locked_letters` est
    fusionné dans `forced_letters` en écrasant tout indice statistique déjà
    présent à la même case (`{**forced_letters, **locked_letters}` : une
    lettre confirmée par une recherche précédente est un fait, pas une
    supposition — elle l'emporte toujours sur le sondage statistique de
    `sample_letter_biases`, jamais l'inverse).

    Avant de lancer le remplissage réel sur ce motif fraîchement choisi, à
    la demande explicite de l'utilisateur : un sondage statistique
    (`sample_letter_biases`) tourne **systématiquement**, quel que soit
    `force_letters_fraction` (y compris à 0.0, le réglage par défaut) —
    ce même sondage fournit à la fois `forced_letters` (des indices de
    lettres, voir Filler._domain) et `letter_scores` (les scores complets
    par lettre et par case que _backtrack utilise pour trier puis
    piocher ses mots candidats, voir Filler._candidate_score), et seul le
    premier des deux dépend réellement de `force_letters_fraction` : à 0.0,
    `sample_letter_biases` retourne `forced_letters` vide (son propre
    calcul de combien de cases forcer donne exactement zéro dans ce cas —
    voir sa docstring), alors que `letter_scores`, lui, reste toujours
    entièrement rempli, à la demande explicite de l'utilisateur — le tri
    des mots candidats par cohérence statistique n'a jamais eu besoin
    d'être conditionné à la présence de lettres réellement forcées.
    `locked_impossible_slots` (calculé juste avant, voir ci-dessus) lui est
    transmis comme `excluded_slots` — à la demande explicite de
    l'utilisateur, pour qu'aucune graine ne soit posée sur un emplacement
    déjà connu impossible (entièrement verrouillé, mais sans mot réel
    correspondant).

    `black_enrichment_fraction` (défaut `POST_PREFILL_BLACK_FRACTION`, voir
    sa propre définition) est transmis tel quel à `make_pattern` — réglable
    depuis l'interface web (voir generate_grid), à la demande explicite de
    l'utilisateur.

    `make_pattern` elle-même reçoit `available_lengths` (les longueurs ayant
    au moins `PREFILL_MIN_WORD_COUNT` mots dans `_worker_index`, pas
    seulement un seul — voir sa propre définition) pour sa propre phase
    de pré-remplissage (voir `_prefill_unfillable_slots`) — dérivé ici, une
    fois par tentative, plutôt que precalculé côté `generate_grid` (coût
    négligeable : `_worker_index` n'a qu'une poignée de longueurs
    distinctes)."""
    rng = random.Random(seed)
    available_lengths = {
        length for length, data in _worker_index.items()
        if len(data["words"]) >= PREFILL_MIN_WORD_COUNT
    }
    grid = make_pattern(rows, cols, ratio, rng, available_lengths=available_lengths,
                         seed_grid=seed_grid, locked_letters=locked_letters, index=_worker_index,
                         black_enrichment_fraction=black_enrichment_fraction)
    # Récupère, avant même de lancer la recherche (et avant même le sondage
    # sample_letter_biases ci-dessous — voir juste après), le mot déjà
    # entièrement déterminé par `locked_letters` pour chaque emplacement
    # dont TOUTES les cases sont verrouillées — à la demande de
    # l'utilisateur, après avoir constaté en direct qu'une grille très
    # remplie pouvait retomber à `assigned=0` dès le tout premier palier
    # suivant un nettoyage : sans ce préremplissage, ces mots pourtant déjà
    # connus ne comptaient comme "assignés" (`Filler.best_assignment`) que
    # si `_backtrack` finissait par les sélectionner explicitement — et un
    # échec instantané ailleurs dans la grille (`checks=1`, un emplacement
    # différent déjà impossible) empêchait la recherche de jamais les
    # atteindre, jetant tout ce travail déjà fait par le nettoyage
    # précédent. Valide chaque mot recomposé auprès du dictionnaire
    # (`_slot_candidate_count`, la même intersection par position que
    # `Filler._domain`) avant de le préassigner — une combinaison de
    # lettres verrouillées qui ne correspond à aucun mot réel (un
    # emplacement réellement impossible, pas seulement pas encore essayé)
    # doit rester `None` ici : elle sera alors naturellement retrouvée par
    # `try_fill` (domaine vide) et remontée dans `impossible_slots`,
    # exactement comme pour un emplacement bloqué déjà connu — la
    # préassigner à tort la ferait disparaître de ce diagnostic à la place.
    # Réutilise `extract_slots` — même calcul que celui que `try_fill`
    # refait de toute façon en interne, aucun état partagé entre les deux
    # appels à économiser ici. `locked_impossible_slots` (les emplacements
    # entièrement verrouillés dont la combinaison est invalide) est
    # calculé ici, avant sample_letter_biases, spécifiquement pour lui être
    # transmis — à la demande explicite de l'utilisateur : "les graines ne
    # doivent être placées que sur des emplacements réputés jouables (si
    # possible), donc, non verrouillés comme injouables."
    preseed_assignment = None
    locked_impossible_slots = set()
    if locked_letters:
        slots = extract_slots(grid, rows, cols)
        preseed_assignment = [None] * len(slots)
        for i, cells in enumerate(slots):
            if all(cell in locked_letters for cell in cells):
                word = "".join(locked_letters[cell] for cell in cells)
                if _slot_candidate_count(_worker_index, len(cells), cells, locked_letters) > 0:
                    preseed_assignment[i] = word
                else:
                    locked_impossible_slots.add(i)
    forced_letters, letter_scores = sample_letter_biases(
        grid, rows, cols, _worker_index, rng, force_fraction=force_letters_fraction,
        excluded_slots=locked_impossible_slots,
    )
    if locked_letters:
        forced_letters = {**forced_letters, **locked_letters}
    diag = {}
    # `batch_abandoned_event` toujours `None` ici, jamais `_worker_batch_
    # abandoned_event` — délibéré, voir la docstring de cette variable
    # globale pour pourquoi (chaque tentative de ce batch a son propre
    # motif indépendant ; le signal partagé n'a de sens que pour
    # `_pattern_continue`, où le motif est rigoureusement le même partout).
    result = try_fill(grid, rows, cols, _worker_index, rng, deadline_checks=deadline_checks,
                       diagnostics=diag,
                       forced_letters=forced_letters, letter_scores=letter_scores,
                       preseed_assignment=preseed_assignment, cancel_event=_worker_cancel_event,
                       batch_abandoned_event=None,
                       attempt_done_event=_worker_attempt_done_event)
    return grid, result, diag


def _pattern_continue(rows, cols, seed, seed_grid, preseed_assignment, excluded_slots,
                       force_letters_fraction=0.0, deadline_checks=None):
    """Tentative de la mécanique de reprise « telle-quelle » entre paliers, à
    la demande explicite de l'utilisateur ("Nouvelle version") — exécutée
    dans un processus worker séparé, comme _pattern_attempt, mais qui n'appelle
    JAMAIS make_pattern : `seed_grid` (le motif noir/blanc du palier
    précédent, sélectionné parce qu'il restait encore au moins un
    emplacement où un mot pouvait être ajouté — voir generate_grid) est
    repris à l'identique, sans une seule case noire de plus ou de moins.

    `deadline_checks` (`None` par défaut) est transmis tel quel à
    `try_fill` — voir la docstring de `generate_grid` pour d'où vient cette
    valeur (le sélecteur "Mode" de l'interface web).

    `preseed_assignment` (l'affectation du palier précédent, un mot ou None
    par emplacement) verrouille tel quel chaque emplacement déjà rempli —
    `try_fill` initialise `Filler.assignment` (et used_words/best_assignment)
    directement dessus plutôt que de repartir d'une grille vide.
    `excluded_slots` (les emplacements déjà identifiés comme impossibles au
    palier précédent, voir `Filler.excluded_slots`) reste ignoré de cette
    recherche : "le tour N+1 doit ignorer les situations de blocage sur les
    cases verrouillées, et essayer de continuer à remplir la grille" — sans
    quoi le simple contrôle de domaine de `_backtrack` ferait échouer la
    recherche dès le premier appel (`checks=1`), même pour des emplacements
    sans aucun rapport avec le blocage déjà connu.

    Chaque tentative parallèle du même palier reçoit son propre seed, comme
    _pattern_attempt — le motif et le verrouillage restent rigoureusement
    identiques d'une tentative à l'autre (rien de nouveau à générer), seul
    l'ordre d'exploration diffère (sondage statistique `sample_letter_
    biases`, tri/tirage des mots candidats dans `_backtrack`) : suffisant
    pour que plusieurs tentatives parallèles, parties du même point,
    atteignent des états d'avancement différents.

    Un `try_fill` complet (`truly_complete`, voir sa docstring) implique ici
    que même les emplacements exclus ont fini par être remplis — impossible
    tant qu'ils restent dans `excluded_slots` (jamais assignés par
    construction), donc `result` vaut toujours None ici : la seule sortie
    utile de cette fonction est `diag` (assignment/impossible_slots à jour),
    que generate_grid réexamine pour décider s'il reste encore un
    emplacement où ajouter un mot (auquel cas la reprise "telle-quelle"
    continue au palier suivant, avec un `excluded_slots` éventuellement
    élargi) ou si c'est un vrai blocage total (plus aucun emplacement non
    exclu n'a de domaine non vide), auquel cas le palier suivant repasse par
    le nettoyage existant (`_build_retry_seed`) et un motif neuf."""
    rng = random.Random(seed)
    forced_letters, letter_scores = sample_letter_biases(
        seed_grid, rows, cols, _worker_index, rng, force_fraction=force_letters_fraction,
        excluded_slots=excluded_slots,
    )
    diag = {}
    result = try_fill(seed_grid, rows, cols, _worker_index, rng, deadline_checks=deadline_checks,
                       diagnostics=diag,
                       forced_letters=forced_letters, letter_scores=letter_scores,
                       preseed_assignment=preseed_assignment, excluded_slots=excluded_slots,
                       cancel_event=_worker_cancel_event,
                       batch_abandoned_event=_worker_batch_abandoned_event,
                       attempt_done_event=_worker_attempt_done_event)
    return seed_grid, result, diag


# ---------- "Continuer" button: resuming a total failure from where it left off ----------
#
# At the user's explicit request: when generate_grid() exhausts every one of
# `attempts` (200 by default) paliers without ever finding a fillable grid,
# the web UI shows a "Continuer" button that relaunches another `attempts`
# paliers, picking up from the exact same seed_grid/locked_letters/
# preseed_assignment/excluded_slots the failed run's own cross-palier retry
# mechanism last produced — instead of the user's only other option, starting
# a brand new generation from a blank grid. `generate_grid`'s own progress()
# call for the "pattern_failed" event carries a `resume_state=...` kwarg
# built by `_serialize_resume_state` right where total failure is detected;
# `backend/app.py` persists it on the job so a later `POST /api/generate/
# continue/{job_id}` can hand it straight back to a fresh `generate_grid()`
# call's own `resume_state` parameter, deserialized by `_deserialize_
# resume_state`.
#
# JSON-safe by construction, since it travels through the job dict returned
# directly by `GET /api/generate/status/{job_id}`: `locked_letters`'s native
# shape (`{(row, col): letter}`, tuple keys) isn't valid JSON — encoded here
# as a flat `[[row, col, letter], ...]` list instead — and `excluded_slots`'s
# native `set` isn't JSON either, so it's encoded as a sorted list. `None`
# is preserved as `None` (JSON `null`) rather than collapsed into an empty
# list/dict for either field, since `carry_locked_letters`/`carry_preseed_
# assignment` being `None` vs. merely empty is what `generate_grid`'s own
# palier loop uses to tell the two mutually-exclusive resume mechanisms
# apart (see the loop's own `if carry_preseed_assignment is not None:`
# dispatch) — collapsing that distinction here would silently corrupt which
# mechanism a resumed run starts from.
def _serialize_resume_state(seed_grid, locked_letters, preseed_assignment, excluded_slots):
    return {
        "seed_grid": seed_grid,
        "locked_letters": (
            None if locked_letters is None
            else [[r, c, letter] for (r, c), letter in locked_letters.items()]
        ),
        "preseed_assignment": preseed_assignment,
        "excluded_slots": None if excluded_slots is None else sorted(excluded_slots),
    }


def _deserialize_resume_state(state):
    seed_grid = [row[:] for row in state["seed_grid"]]
    raw_locked_letters = state.get("locked_letters")
    locked_letters = (
        None if raw_locked_letters is None
        else {(r, c): letter for r, c, letter in raw_locked_letters}
    )
    preseed_assignment = state.get("preseed_assignment")
    raw_excluded_slots = state.get("excluded_slots")
    excluded_slots = None if raw_excluded_slots is None else set(raw_excluded_slots)
    return seed_grid, locked_letters, preseed_assignment, excluded_slots


def generate_grid(width=DEFAULT_WIDTH, height=DEFAULT_HEIGHT, difficulty="easy",
                   max_words=None, black_ratio=0.0, attempts=200, seed=None,
                   wordlist_path="data/wordlist_fr_full.tsv", on_progress=None,
                   force_letters_fraction=0.0, cancel_event=None,
                   black_enrichment_fraction=POST_PREFILL_BLACK_FRACTION,
                   deadline_checks=None, resume_state=None):
    """Génère une grille remplie de bout en bout (motif + CSP + minimisation).
    `width` est le nombre de colonnes (horizontal), `height` le nombre de lignes
    (vertical). Retourne un dict {width, height, pattern, solution, words,
    word_count, black_count, black_ratio}, ou None si aucune grille remplissable
    n'a été trouvée en `attempts` essais.

    `on_progress`, si fourni, est appelé `on_progress(step, **data)` à chaque
    étape notable (voir backend/app.py, qui s'en sert à la fois pour tracer
    backend.log et pour exposer un statut d'avancement à l'interface via
    l'API de polling) — aucun effet sur la génération elle-même, purement
    un point d'observation.

    `deadline_checks` (`None` par défaut — aucun effet pour tout appelant
    existant, notamment le CLI), à la demande explicite de l'utilisateur :
    transmis tel quel à chaque `_pattern_attempt`/`_pattern_continue` puis à
    `try_fill` (voir sa propre docstring), qui retombe sur son calcul par
    défaut (`largeur × hauteur × 2000`) tant que cette valeur reste `None`.
    L'interface web (voir backend/app.py) expose ceci comme un sélecteur
    "Mode" à choix fixes (Flash/Turbo/Rapide/Moyen/Ultra) plutôt qu'un champ
    libre — chaque mode fixe directement le nombre de vérifications par
    tentative, sans rapport avec la taille de la grille, contrairement à la
    formule par défaut.

    `cancel_event` (un `threading.Event`, `None` par défaut — aucun effet
    pour tout appelant existant, notamment le CLI), à la demande explicite
    de l'utilisateur : bouton "Stop" de l'interface web (voir
    backend/app.py), permettant d'interrompre une génération en cours
    quelle que soit l'étape. Vérifié au début de chaque palier (voir la
    boucle plus bas) et transmis à `minimize_black_squares` pour la phase
    de minimisation — lève `GenerationCancelled` dès que l'événement est
    déclenché, plutôt que de renvoyer `None` (qui signifie déjà autre
    chose : aucune grille remplissable trouvée après épuisement de
    `attempts`, un échec géniune, pas une interruption demandée). Un
    signal purement coopératif (voir GenerationCancelled) : l'arrêt
    effectif peut prendre jusqu'à la fin du palier en cours (borné par
    `deadline_checks` de chaque tentative parallèle), pas instantané —
    aucune tentative de tuer de force un processus worker déjà lancé.

    `black_enrichment_fraction` (défaut `POST_PREFILL_BLACK_FRACTION`, voir
    sa propre définition), à la demande explicite de l'utilisateur :
    réglable depuis l'interface web (un sélecteur "Ajout noires", 0/1/3/5/
    10 %, 3 % par défaut — voir `GenerateRequest.black_enrichment_percent`
    dans backend/app.py). Transmis tel quel à `_pattern_attempt` (jamais à
    `_pattern_continue`, qui ne rappelle jamais `make_pattern` — un palier
    de reprise "telle-quelle" ne peut par construction ajouter aucune case
    noire, voir _pattern_continue's propre docstring), donc uniquement
    pertinent pour un palier qui part d'une grille vierge ou d'un
    nettoyage (`_build_retry_seed`).

    `force_letters_fraction` (0.0 par défaut, c'est-à-dire désactivé), à la
    demande explicite de l'utilisateur : active ou non le sondage
    statistique de lettres forcées (`sample_letter_biases`, voir
    `_pattern_attempt`) en tout début de remplissage, et avec quelle
    fraction des cases de la grille. Réglable depuis l'interface web (un
    sélecteur de pourcentage — 0/1/2/5/10 %, 0 % par défaut — voir
    `GenerateRequest` dans backend/app.py et frontend/static/index.html),
    qui valide la valeur puis la convertit en fraction (`percent / 100`)
    avant de la transmettre ici ; auparavant une fraction fixe
    (`LETTER_BIAS_FORCE_FRACTION`, 5 %) appliquée systématiquement à toute
    tentative. Simplement transmis tel quel à chaque tentative, aucune
    autre partie du pipeline n'a besoin de le connaître.

    `resume_state` (`None` par défaut — aucun effet pour tout appelant
    existant, notamment le CLI), à la demande explicite de l'utilisateur :
    bouton "Continuer" de l'interface web, affiché quand une génération a
    épuisé tous ses `attempts` sans trouver de grille remplissable — voir
    `_serialize_resume_state`/`_deserialize_resume_state` juste au-dessus.
    Si fourni, initialise `carry_seed_grid`/`carry_locked_letters`/
    `carry_preseed_assignment`/`carry_excluded_slots` (voir la boucle plus
    bas) depuis l'état final d'un appel précédent qui a échoué, au lieu de
    partir d'une grille vierge — le premier palier de cet appel reprend
    ainsi exactement là où l'appel précédent s'est arrêté, avec un nouveau
    budget complet de `attempts` paliers."""
    def progress(step, **data):
        if on_progress:
            on_progress(step, **data)

    rng = random.Random(seed)
    mw = max_words or DIFFICULTY_PRESETS.get(difficulty)
    by_length, accents, canonicals = load_wordlist(
        wordlist_path, mw, require_gloss=(difficulty == "easy")
    )
    index = build_index(by_length)
    # Logged once per request, not per attempt: the CSP's failure mode
    # (below) can't be told apart from a genuinely empty word list for
    # some length without this — a `require_gloss`/`max_words` combination
    # that shrinks a length to 0 words fails every pattern instantly, in
    # a way that looks identical in the per-attempt log to ordinary bad
    # luck unless this baseline is on record too.
    progress("wordlist_loaded", word_count=sum(len(w) for w in by_length.values()),
             length_counts=dict(sorted((length, len(words)) for length, words in by_length.items())))

    rows, cols = height, width
    ratio = black_ratio
    best, best_result = None, None
    last_diag = None
    last_examples = []
    # Compteur cumulatif du nombre de grilles réellement échouées depuis le
    # début (tous paliers confondus), à la demande explicite de l'utilisateur
    # — pour l'affichage du statut côté interface (voir describeStep() dans
    # frontend/static/script.js) : sans lui, l'utilisateur voit "tentative
    # X/attempts" (le numéro du *palier*) sans savoir combien de grilles
    # PARALLEL_ATTEMPTS-à-la-fois ont réellement été générées et rejetées
    # jusqu'ici. Incrémenté du nombre de tentatives *échouées* de chaque
    # palier (pas de `len(outcomes)` tel quel) — à la demande explicite de
    # l'utilisateur, après un premier réglage qui comptait aussi les
    # tentatives réussies du palier final comme des échecs : ce compteur doit
    # refléter le nombre de grilles réellement rejetées, pas le nombre brut
    # de tentatives lancées (qui, au palier gagnant, inclut une ou plusieurs
    # réussites).
    total_attempts_tried = 0
    # Chaque palier lance PARALLEL_ATTEMPTS tentatives indépendantes en
    # parallèle (processus séparés, un seed dérivé de `rng` chacune) plutôt
    # qu'une seule tentative séquentielle : la machine est loin de saturer
    # son CPU avec une seule tentative à la fois, donc plusieurs chances par
    # palier ne coûtent, en temps réel, quasiment que le temps de la
    # tentative la plus lente du lot — pas la somme des cinq.
    # Point de départ (motif + lettres verrouillées) transmis au palier
    # suivant après un échec complet, à la demande explicite de
    # l'utilisateur — voir _build_retry_seed pour l'algorithme en 3 étapes
    # (retirer les mots connectés aux emplacements en échec, garder le
    # reste comme lettres pré-définies, rouvrir les cases noires qui ne
    # touchent plus aucune lettre confirmée). `None` tant qu'aucun palier
    # n'a encore échoué — le tout premier palier part toujours d'une
    # grille vierge, exactement comme avant cette fonctionnalité.
    carry_seed_grid = None
    carry_locked_letters = None
    # Reprise "telle-quelle" (voir _pattern_continue), à la demande
    # explicite de l'utilisateur ("Nouvelle version") : tant que le palier
    # échoué sélectionné a encore au moins un emplacement où un mot peut
    # être ajouté (pas seulement des emplacements impossibles), le palier
    # suivant repart du MÊME motif, sans passer par make_pattern ni par le
    # nettoyage `_build_retry_seed` — `carry_preseed_assignment`/
    # `carry_excluded_slots` pilotent ce mode ; `None` tous les deux (leur
    # valeur par défaut) signifie qu'on est en mode "motif neuf" normal
    # (via `_pattern_attempt`, `carry_seed_grid`/`carry_locked_letters`
    # ci-dessus, inchangé). Les deux mécanismes de reprise sont mutuellement
    # exclusifs à chaque palier : un seul est actif à la fois, jamais les
    # deux (voir plus bas, où chaque branche remet l'autre à None).
    carry_preseed_assignment = None
    carry_excluded_slots = None
    # "Continuer" button on the web UI, at the user's explicit request: when
    # every one of `attempts` (200 by default) paliers has failed, the user
    # can relaunch another `attempts` paliers starting from the exact state
    # the failed run left off at, instead of starting over from a blank
    # grid. `resume_state` (`None` by default — no effect for any
    # pre-existing caller, including the CLI), if given, seeds the four
    # carry_* variables above from a previous, failed `generate_grid()`
    # call's own final state (see `_serialize_resume_state`/`_deserialize_
    # resume_state` and the matching `resume_state=...` kwarg on the
    # "pattern_failed" progress event further below) rather than starting
    # every one of them at `None`. `consecutive_continue_paliers` (below)
    # deliberately still starts at 0 regardless: this "Continuer" click
    # gets its own fresh budget of up to 50 consecutive "reprise
    # telle-quelle" paliers before a forced cleanup, exactly like any other
    # top-level `generate_grid()` call, rather than carrying over wherever
    # the previous run's own counter happened to be.
    if resume_state is not None:
        carry_seed_grid, carry_locked_letters, carry_preseed_assignment, carry_excluded_slots = (
            _deserialize_resume_state(resume_state)
        )
    # Nombre de paliers "continue" consécutifs déjà enchaînés sans passer
    # par un nettoyage, à la demande explicite de l'utilisateur : "Limiter
    # le nombre de tours réalisés sans nettoyage à 5 consécutifs maximum. A
    # partir de 5, déclencher un nettoyage." Remis à 0 chaque fois qu'un
    # nettoyage a réellement lieu (voir plus bas) — ce compteur ne mesure
    # que la série en cours, pas un total cumulé sur toute la génération.
    consecutive_continue_paliers = 0
    # Voir _worker_batch_abandoned_event — un seul Event pour toute la
    # génération (créé ici, jamais recréé palier après palier, pour la
    # même raison technique que cancel_event : un Event soumis en argument
    # de tâche plutôt que via l'initializer du pool provoque une
    # RuntimeError sur macOS), mais remis à zéro avant chaque nouveau batch
    # (voir plus bas) puisque son sens ne vaut que pour le palier en cours.
    batch_abandoned_event = multiprocessing.Event()
    # Signal "this palier's outcome is already decided", at the user's
    # explicit request: "interrupt every search as soon as one search
    # finishes (success or failure) to move on to the next palier." One
    # Event for the whole generation (same technical reason as
    # batch_abandoned_event/cancel_event above: a multiprocessing.Event
    # passed as a per-task argument raises a RuntimeError on macOS's
    # "spawn" start method — it must go through the pool's initializer
    # instead), cleared at the start of every palier below since its
    # meaning only ever applies to the palier currently running.
    attempt_done_event = multiprocessing.Event()
    with concurrent.futures.ProcessPoolExecutor(
        max_workers=PARALLEL_ATTEMPTS, initializer=_init_worker,
        initargs=(index, cancel_event, batch_abandoned_event, attempt_done_event)
    ) as executor:
        for attempt in range(attempts):
            if cancel_event is not None and cancel_event.is_set():
                raise GenerationCancelled()
            batch_abandoned_event.clear()
            attempt_done_event.clear()
            progress("pattern", attempt=attempt + 1, attempts=attempts, parallel=PARALLEL_ATTEMPTS,
                     total_attempts=total_attempts_tried)
            seeds = [rng.randrange(2**31) for _ in range(PARALLEL_ATTEMPTS)]
            if carry_preseed_assignment is not None:
                futures = [
                    executor.submit(_pattern_continue, rows, cols, s, carry_seed_grid,
                                     carry_preseed_assignment, carry_excluded_slots,
                                     force_letters_fraction, deadline_checks)
                    for s in seeds
                ]
            else:
                futures = [
                    executor.submit(_pattern_attempt, rows, cols, ratio, s, force_letters_fraction,
                                     carry_seed_grid, carry_locked_letters, black_enrichment_fraction,
                                     deadline_checks)
                    for s in seeds
                ]
            # Récupérés dans l'ordre d'achèvement (`as_completed`), pas
            # l'ordre de soumission, à la demande explicite de l'utilisateur
            # ("le bouton Stop ne s'applique pas rapidement... prévoir
            # l'arrêt dans toutes les phases") : dès qu'une des
            # PARALLEL_ATTEMPTS tentatives lève GenerationCancelled (chaque
            # worker vérifie le même `cancel_event`, voir Filler._backtrack),
            # on relance l'exception immédiatement plutôt que d'attendre
            # aussi le résultat des autres — `outcomes`'s propre ordre
            # n'a pas d'importance pour le reste de cette boucle (le
            # meilleur résultat est toujours choisi via `max`/tri, jamais
            # par position). Les autres tentatives encore en cours
            # détecteront la même annulation à leur propre prochain point de
            # contrôle (au plus CANCEL_CHECK_INTERVAL vérifications plus
            # tard) et s'arrêteront à leur tour — `with ... as executor`
            # attend leur fin normale à la sortie du bloc (comportement par
            # défaut de ProcessPoolExecutor), mais ce délai reste court,
            # sans rapport avec le budget `deadline_checks` complet d'un
            # palier.
            # Collected in completion order (`as_completed`), not submission
            # order — once PALIER_ATTEMPT_INTERRUPT_FRACTION (30%) of this
            # batch's attempts have finished (success or failure alike),
            # `attempt_done_event` is set so every other still-running
            # attempt stops at its own next checkpoint (see
            # PALIER_ATTEMPT_DONE_CHECK_INTERVAL), at the user's explicit
            # request — refining the initial design, which interrupted as
            # soon as the very first attempt finished: "à partir de 30% des
            # tentatives qui se terminent, interrompre toutes les
            # tentatives." `math.ceil` (never floor/round) so a small
            # PARALLEL_ATTEMPTS still interrupts on genuine 30%-or-more
            # progress rather than possibly rounding down to 0 and
            # interrupting immediately again; `max(1, ...)` as a floor so
            # this can never itself require MORE completions than
            # `interrupt_threshold == 1` would already need. We still call
            # `.result()` on every future here (draining them all) rather
            # than abandoning them outright: with
            # `max_workers=PARALLEL_ATTEMPTS` persistent worker processes,
            # every future is already running by the time we get here, and
            # there is no way to reclaim a worker process without either
            # waiting for its current task to return or forcibly killing
            # it (never done anywhere in this file — see cancel_event's own
            # cooperative, non-destructive design) — but that wait is now
            # bounded to roughly PALIER_ATTEMPT_DONE_CHECK_INTERVAL more
            # checks per straggler instead of its full deadline_checks
            # budget, the same kind of bound already measured for
            # cancel_event (~0.79s in that case). A future that raises
            # GenerationCancelled (the user's own "Stop" button, a
            # different and higher-priority signal — see Filler._backtrack)
            # still propagates immediately here, exactly as before this
            # feature.
            interrupt_threshold = max(1, math.ceil(PALIER_ATTEMPT_INTERRUPT_FRACTION * len(futures)))
            outcomes = []
            for f in concurrent.futures.as_completed(futures):
                outcomes.append(f.result())
                if len(outcomes) == interrupt_threshold:
                    attempt_done_event.set()
            successes = [(g, r) for g, r, d in outcomes if r is not None]
            # Dédoublonnage des tentatives échouées, à la demande explicite de
            # l'utilisateur, après un bug réel constaté en direct : une fois
            # qu'une bonne partie de la grille est verrouillée par le
            # mécanisme de reprise entre paliers ci-dessous, la zone encore
            # libre peut devenir si restreinte que les PARALLEL_ATTEMPTS
            # tentatives parallèles — pourtant lancées avec des seeds
            # différents — convergent vers EXACTEMENT le même motif et la
            # même impasse (reproduit en direct : dès le 6e palier d'une
            # grille durcie, les 10 tentatives donnaient un seul motif
            # distinct au lieu de 6+). Sans dédoublonnage, l'aperçu montrait
            # la même grille répétée 6 fois au lieu de 6 tentatives
            # réellement différentes. Deux tentatives comptent comme
            # identiques seulement si leur motif noir/blanc *et* leur
            # affectation de mots sont tous deux rigoureusement égaux (pas
            # seulement le motif seul, au cas où deux motifs identiques
            # aboutiraient malgré tout à des lettres différentes). Ce
            # dédoublonnage ne sert plus qu'à choisir *quels* aperçus
            # montrer (voir `failed_pairs` plus bas) — plus au calcul de
            # `total_attempts` lui-même, voir juste en dessous.
            failed_all = [(g, d) for g, r, d in outcomes if r is None]
            # Attempts cut short by attempt_done_event (see above) never
            # count as candidates for "which failed attempt is the best one"
            # (failed_unique/failed_pairs below): their early, arbitrary stop
            # reflects no real dead end, so their own `impossible_cells`
            # count (the sort key used further below) would be a meaningless
            # — and often misleadingly LOW, since they barely had time to
            # explore — signal, not a genuine measure of how close they got.
            # The first `interrupt_threshold` outcomes to complete are, by
            # construction, never themselves interrupted this way (the
            # event is only set right after that many are already in hand),
            # so this filter never leaves `failed_unique` empty whenever
            # `failed_all` itself is non-empty. Their `checks` still count
            # toward
            # `total_attempts_tried` below (real backtracking work was done
            # before they were told to stop).
            failed_real = [
                (g, d) for g, d in failed_all
                if d["reason"] != "interrupted_other_attempt_done"
            ]
            seen_keys = set()
            failed_unique = []
            for g, d in failed_real:
                key = (tuple(map(tuple, g)), tuple(d["assignment"]))
                if key not in seen_keys:
                    seen_keys.add(key)
                    failed_unique.append((g, d))
            # `total_attempts` compte les grilles réellement essayées et
            # abandonnées au sens propre du mot, à la demande explicite de
            # l'utilisateur — pas le nombre de processus parallèles lancés
            # (10 par palier), qui ne reflète absolument pas le travail
            # réel effectué : le remplissage CSP procède par essais
            # successifs avec retour en arrière (voir Filler._backtrack) —
            # chaque case noeud de recherche visité (`filler.checks`,
            # incrémenté une fois par appel à _backtrack, qu'il aboutisse à
            # un retour en arrière ou à une avancée) représente une
            # configuration de grille réellement tentée puis abandonnée dès
            # que la recherche recule. Sommé sur TOUTES les tentatives
            # échouées de ce palier, y compris les doublons ci-dessus — un
            # motif identique retrouvé par deux workers différents (seeds
            # différents) a quand même nécessité, dans chaque worker, son
            # propre travail de recherche réel (un chemin de retours en
            # arrière qui peut différer même si le résultat final converge),
            # donc aucune des deux quantités de travail n'est à ignorer.
            total_attempts_tried += sum(d["checks"] for _, d in failed_all)
            if successes:
                # Plusieurs des PARALLEL_ATTEMPTS tentatives peuvent réussir au
                # même palier ; on ne garde pas simplement la première trouvée
                # mais celle qui maximise la somme des carrés des longueurs de
                # tous ses mots, à la demande explicite de l'utilisateur — ce
                # score favorise quelques mots longs plutôt que beaucoup de
                # mots courts pour le même nombre total de lettres (un mot de
                # 10 lettres pèse 100, dix mots de 2 lettres ne pèsent que 40).
                best, best_result = max(
                    successes, key=lambda gr: sum(len(slot) ** 2 for slot in gr[1][0])
                )
                break
            # Jusqu'à FAILED_ATTEMPT_EXAMPLES (6) aperçus distincts parmi les
            # tentatives réellement distinctes de ce palier, triés par nombre
            # de cases noires croissant — à la demande explicite de
            # l'utilisateur, qui remplace ainsi le critère précédent (le plus
            # de lettres réellement posées, `assigned_letter_count`, toujours
            # calculé et disponible dans les diagnostics mais plus utilisé
            # pour ce tri) : la "meilleure" tentative échouée est désormais
            # celle dont le motif a le moins de cases noires, pas celle qui a
            # le plus avancé dans son remplissage — cohérent avec l'objectif
            # général du projet de minimiser les cases noires (voir
            # minimize_black_squares), y compris parmi les tentatives
            # échouées servant de base au palier suivant. `last_diag` (la
            # première diagnostics une fois triées, donc désormais celle du
            # motif le plus économe en cases injouables) reste transmis tel
            # quel en plus, pour le log détaillé (slot_count/length_counts/
            # checks/reason) déjà en place. Gardé apparié à son propre motif
            # (`failed_pairs`, pas seulement les diagnostics) puisque
            # `_build_retry_seed` ci-dessous a besoin du motif noir/blanc réel
            # de la meilleure tentative, pas seulement de ses diagnostics.
            # Critère de tri revu à la demande explicite de l'utilisateur :
            # "la meilleure grille est celle qui minimise le nombre de
            # caractères considérés comme injouables" — remplace l'ancien
            # critère (le moins de cases noires), qui ne disait rien de
            # combien de cases étaient réellement bloquées.
            failed_pairs = sorted(
                failed_unique, key=lambda gd: len(gd[1]["impossible_cells"]),
            )
            last_diag = failed_pairs[0][1]
            last_examples = [
                {
                    "example_grid": d["example_grid"],
                    "impossible_cells": d["impossible_cells"],
                    "forced_cells": d["forced_cells"],
                    "locked_cells": d.get("locked_cells", []),
                }
                for _, d in failed_pairs[:FAILED_ATTEMPT_EXAMPLES]
            ]
            progress("pattern_attempt_failed", attempt=attempt + 1, ratio=round(ratio, 3),
                     total_attempts=total_attempts_tried, examples=last_examples, **last_diag)
            # "Nouvelle version" du mécanisme de reprise entre paliers, à la
            # demande explicite de l'utilisateur, remplaçant l'essai
            # précédent ("continuer avant de nettoyer", tenté puis reverti —
            # voir plus bas pour l'historique conservé) : on regarde d'abord
            # si la grille échouée déjà sélectionnée (`failed_pairs[0]`,
            # celle avec le moins de cases injouables, déjà utilisée pour
            # `last_diag`/`last_examples` ci-dessus) a encore au moins un
            # emplacement non assigné qui n'est PAS impossible — un endroit
            # où un mot pourrait encore être ajouté sans rien nettoyer ni
            # regénérer. Si oui, le palier suivant reprend ce motif TEL
            # QUEL (`_pattern_continue`, aucun appel à `make_pattern`),
            # verrouillant toutes les cases déjà remplies et ignorant les
            # blocages sur les emplacements déjà connus comme impossibles —
            # exactement le point qui faisait échouer instantanément
            # (`checks=1`) l'essai précédent une fois composé dans la
            # boucle complète (voir plus bas), puisqu'ici aucune nouvelle
            # grille n'est générée par-dessus un contenu verrouillé
            # grandissant : le motif reste rigoureusement le même d'un
            # palier "continue" à l'autre, seul le contenu verrouillé/exclu
            # grandit. Si non (chaque emplacement non assigné restant est
            # impossible — un vrai blocage total pour ce motif), on retombe
            # sur le nettoyage existant (`_build_retry_seed`) et un motif
            # neuf au palier suivant, exactement comme avant cette
            # fonctionnalité.
            selected_grid, selected_diag = failed_pairs[0]
            selected_impossible = set(selected_diag["impossible_slots"])
            # `_slots_touching`, à la demande explicite de l'utilisateur
            # ("ne pas essayer de remplir les emplacements qui croisent un
            # emplacement réputé impossible", voir Filler.__init__'s propre
            # `_crossing_excluded_slots`) : un emplacement qui croise un
            # emplacement impossible ne sera de toute façon jamais tenté au
            # palier "continue" suivant, donc il ne représente pas un
            # véritable espoir de progrès — un vrai bug trouvé en direct
            # sans ce correctif : `still_has_hope` restait indéfiniment
            # `True` (ces emplacements comptaient comme non-impossibles,
            # donc "encore prometteurs", alors qu'ils ne seraient jamais
            # essayés), empêchant à tort le nettoyage de jamais se
            # déclencher — confirmé par 3 générations réelles échouant
            # intégralement (200 paliers "continue" épuisés sans jamais
            # nettoyer) avant ce correctif.
            selected_slots = extract_slots(selected_grid, rows, cols)
            selected_dead = selected_impossible | _slots_touching(selected_slots, selected_impossible)
            still_has_hope = any(
                w is None and i not in selected_dead
                for i, w in enumerate(selected_diag["assignment"])
            )
            # Nettoyage forcé si les 10 tentatives de ce palier ont TOUTES
            # été abandonnées via la règle des 30 % (voir
            # UNFILLABLE_ABANDON_FRACTION, Filler.abandoned, reason ==
            # "abandoned_too_unfillable"), à la demande explicite de
            # l'utilisateur : quand chacune, indépendamment, a jugé son
            # propre motif trop largement condamné pour continuer à
            # chercher, c'est un signal fort qu'une reprise "telle quelle"
            # sur ce même motif serait vaine — on force donc un nettoyage
            # immédiatement, sur la meilleure de ces grilles
            # (`failed_pairs[0]`, déjà la base du nettoyage ci-dessous),
            # plutôt que de laisser `still_has_hope` en décider seul.
            #
            # Checked against `failed_real` (excluding attempts cut short by
            # attempt_done_event), not the raw `failed_all` — with
            # interruption now in play, `failed_all` will typically contain
            # up to `interrupt_threshold` real outcomes plus several
            # "interrupted_other_attempt_done" stragglers, which would never
            # satisfy this `all(...)` check and so would silently stop this
            # rule from ever firing again. In practice `failed_real` now
            # usually holds just the handful of attempts that completed
            # before `interrupt_threshold` was reached (up to
            # PALIER_ATTEMPT_INTERRUPT_FRACTION of PARALLEL_ATTEMPTS, e.g. 3
            # out of 10 by default) rather than all PARALLEL_ATTEMPTS — a
            # faster-firing version of the same original intent, no longer
            # needing every single attempt to each independently reach that
            # same conclusion before this palier is even allowed to finish.
            if failed_real and all(d["reason"] == "abandoned_too_unfillable" for _, d in failed_real):
                still_has_hope = False
            # Plafond de 50 paliers "continue" consécutifs (relevé de 5 à
            # 10 puis à 50, à la demande explicite de l'utilisateur) — même
            # quand `still_has_hope` reste `True`, on force un nettoyage dès
            # que ce plafond est atteint, plutôt que de laisser la reprise
            # "telle quelle" s'enchaîner indéfiniment sur un motif qui ne
            # progresse peut-être plus vraiment d'un palier à l'autre.
            if consecutive_continue_paliers >= 50:
                still_has_hope = False

            if still_has_hope:
                consecutive_continue_paliers += 1
                # Nettoyage automatique des emplacements bloqués, mais pas
                # des cases noires (à l'exception du verrouillage d'une
                # seule case, voir plus bas), à la demande explicite de
                # l'utilisateur : "à la fin d'un tour, nettoyer
                # automatiquement les emplacements bloqués, mais pas les
                # noires." Retire, avant même de reprendre "telle quelle"
                # au palier suivant, tout mot qui croise directement un
                # emplacement impossible (`_clean_blocked_slots`, les
                # étapes 1-2 de `_build_retry_seed` sans sa 3e étape) —
                # `carry_seed_grid` reste le motif rigoureusement identique
                # (aucune case noire rouverte, aucun nouveau motif généré),
                # seul le contenu verrouillé transmis au palier suivant est
                # nettoyé.
                cleaned_assignment, cleaned_confirmed = _clean_blocked_slots(
                    selected_slots, selected_diag["assignment"], selected_diag["impossible_slots"],
                )
                # Copie défensive avant mutation (voir juste en dessous) —
                # `selected_grid` est `failed_pairs[0][0]`, un objet partagé
                # avec d'autres usages de ce palier (déjà utilisé pour
                # `last_examples`/`last_diag` plus haut) ; le muter en place
                # sans copie risquerait de corrompre ces données déjà
                # calculées, le même principe que partout ailleurs dans ce
                # fichier où une grille destinée à être modifiée est
                # d'abord copiée (voir `_build_retry_seed`'s propre
                # `new_grid`).
                carry_seed_grid = [row[:] for row in selected_grid]
                carry_locked_letters = None
                # Verrouiller UNE seule case noire au hasard parmi les cases
                # des emplacements injouables — à la demande explicite de
                # l'utilisateur, qui a signalé que la première version de
                # cette règle ne s'appliquait qu'au nettoyage complet
                # (branche `else` ci-dessous) : "il faut ajouter une case
                # noire à tous les tours où on nettoie les emplacements
                # injouables (pas seulement quand on nettoie aussi les cases
                # noires)." La reprise "telle quelle" nettoie elle aussi les
                # emplacements bloqués (`_clean_blocked_slots` juste
                # au-dessus), donc cette règle s'y applique tout autant —
                # voir `_impossible_cell_groups`/`_lock_one_impossible_cell`
                # pour la logique exacte (priorité aux cases encore
                # blanches), partagée avec la branche `else` ci-dessous pour
                # ne pas dupliquer ce calcul. C'est la SEULE exception au
                # principe "aucune case noire touchée" de ce chemin.
                selected_blank_cells, selected_lettered_cells = _impossible_cell_groups(
                    selected_slots, selected_diag["assignment"], selected_diag["impossible_slots"],
                )
                _lock_one_impossible_cell(
                    carry_seed_grid, rows, cols,
                    selected_blank_cells, selected_lettered_cells, rng,
                )
                # Bug réel évité ici, trouvé par relecture avant tout test
                # en direct : verrouiller cette case peut raccourcir ou
                # scinder l'emplacement injouable visé, ce qui décale la
                # numérotation de TOUS les emplacements suivants dans
                # l'ordre de balayage de `extract_slots` (ligne par ligne
                # pour l'horizontal, puis colonne par colonne pour le
                # vertical — voir sa propre docstring). `selected_slots`/
                # `selected_diag["impossible_slots"]` ne sont donc plus
                # forcément valables comme indices une fois `carry_seed_
                # grid` modifiée : le palier suivant (`_pattern_continue`
                # -> `try_fill`) réextrait ses propres `slots` FRAÎCHEMENT
                # depuis cette même grille modifiée, avec potentiellement
                # une numérotation différente. `carry_preseed_assignment`/
                # `carry_excluded_slots` ne sont donc calculés qu'ICI,
                # après la mutation, à partir de cette nouvelle structure —
                # jamais transmis tels quels depuis `selected_slots`.
                # `cleaned_confirmed` (case -> lettre, déjà renvoyé par
                # `_clean_blocked_slots` ci-dessus) sert de seule source de
                # vérité pour reconstruire l'affectation par position,
                # puisqu'il ne s'appuie que sur des coordonnées, jamais sur
                # un indice d'emplacement — immunisé par construction contre
                # ce décalage. Un ancien emplacement impossible qui a
                # survécu identique (ses cases exactes existent encore telle
                # quelles dans la nouvelle structure) reste exclu, à son
                # nouvel indice ; celui qui vient d'être raccourci/scindé ne
                # l'est plus — ses fragments neufs redeviennent librement
                # tentables, ce qui est précisément le but de lui avoir
                # ajouté une case noire.
                new_slots = extract_slots(carry_seed_grid, rows, cols)
                carry_preseed_assignment = [
                    "".join(cleaned_confirmed[cell] for cell in cells)
                    if all(cell in cleaned_confirmed for cell in cells) else None
                    for cells in new_slots
                ]
                old_impossible_cell_tuples = {
                    tuple(selected_slots[i]) for i in selected_diag["impossible_slots"]
                }
                carry_excluded_slots = {
                    j for j, cells in enumerate(new_slots)
                    if tuple(cells) in old_impossible_cell_tuples
                }
            else:
                consecutive_continue_paliers = 0
                carry_preseed_assignment = None
                carry_excluded_slots = None
                # Nouvel algorithme de reprise entre paliers, à la demande
                # explicite de l'utilisateur (voir _build_retry_seed) : nettoyer
                # les FAILED_ATTEMPT_EXAMPLES (6) meilleures tentatives de CE
                # palier (celles déjà montrées dans `last_examples` ci-dessus),
                # pas seulement la première — chacune perd un nombre différent
                # de lettres à l'étape 1 du nettoyage (retrait des mots croisant
                # un emplacement impossible) selon la forme précise de son propre
                # blocage, donc celle qui semblait "la meilleure" avant nettoyage
                # (le moins de cases noires) n'est pas forcément celle qui
                # conserve le plus d'information une fois nettoyée. `best_slots`
                # est recalculé ici (au lieu d'être renvoyé par le worker) — un
                # calcul déterministe et bon marché à partir du motif noir/blanc
                # seul, pas la peine d'élargir le contrat de retour de
                # `_pattern_attempt`/`try_fill` juste pour l'éviter.
                # "Continuer à ajouter des mots avant de nettoyer" (donner à
                # chaque candidate une seconde chance de remplissage, en
                # excluant l'emplacement déjà identifié comme impossible via
                # `Filler.excluded_slots`) a été essayé ici puis reverti, à la
                # demande explicite de l'utilisateur, après un test réel montrant
                # une régression sérieuse : vérifié correct en isolation (voir
                # `Filler.excluded_slots`, toujours en place et fonctionnel) mais,
                # composé dans la boucle complète, un palier auparavant sain
                # (15×10, seed 2, 62.7s, 0 incohérence juste avant ce changement)
                # se bloquait instantanément (`checks=1`) sur 199 des 200 paliers
                # — le contenu verrouillé grossissant progressivement à chaque
                # tour (`slot_count` 43→56) sans jamais redevenir réellement
                # remplissable. Cause exacte non identifiée avant de revenir à
                # la version d'avant ce mécanisme ; remplacé par la "Nouvelle
                # version" ci-dessus, qui reprend le motif tel quel (aucune
                # régénération) au lieu de composer une reprise-avec-exclusion
                # par-dessus un motif encore régénéré à chaque palier — voir la
                # SKILL project-best-practices pour l'historique complet.
                def _clean_all_candidates(force_exclude):
                    result = []
                    for cand_grid, cand_diag in failed_pairs[:FAILED_ATTEMPT_EXAMPLES]:
                        cand_slots = extract_slots(cand_grid, rows, cols)
                        # Cases des emplacements injouables de CETTE candidate,
                        # réparties entre celles qui portaient déjà une lettre
                        # et celles qui étaient encore blanches, calculé à
                        # partir de l'affectation BRUTE — voir
                        # `_impossible_cell_groups`'s propre docstring pour le
                        # pourquoi (avant tout nettoyage, à la demande
                        # explicite de l'utilisateur : après nettoyage, ces
                        # cases sont de toute façon toutes redevenues
                        # blanches, rendant la distinction impossible).
                        cand_blank_impossible_cells, cand_lettered_impossible_cells = (
                            _impossible_cell_groups(
                                cand_slots, cand_diag["assignment"], cand_diag["impossible_slots"],
                            )
                        )
                        cand_seed, cand_confirmed = _build_retry_seed(
                            cand_grid, rows, cols, cand_slots,
                            cand_diag["assignment"], cand_diag["impossible_slots"],
                            locked_letters=carry_locked_letters,
                            exclude_impossible_locked=force_exclude,
                        )
                        result.append((
                            cand_seed, cand_confirmed, cand_slots,
                            cand_blank_impossible_cells, cand_lettered_impossible_cells,
                        ))
                    return result

                # Parmi les 6 nettoyées, celle qui l'emporte maximise la somme
                # des carrés des longueurs des mots en place *après* nettoyage
                # (un mot est "en place" si toutes ses cases figurent dans
                # `confirmed`) — à la demande explicite de l'utilisateur,
                # remplace l'ancien critère (le plus de lettres restantes,
                # départagé par le moins de cases noires). Même formule de
                # score que celle qui départage les tentatives parallèles
                # réussies plus haut dans cette fonction (favorise quelques
                # mots longs plutôt que beaucoup de mots courts pour le même
                # total de lettres) — appliquée ici au résultat *après*
                # nettoyage (le vrai signal utile pour repartir), pas à un
                # critère pré-nettoyage comme précédemment.
                def _words_in_place_score(cand_slots, cand_confirmed):
                    return sum(
                        len(cells) ** 2 for cells in cand_slots
                        if all(cell in cand_confirmed for cell in cells)
                    )

                previous_locked_letters = carry_locked_letters
                cleaned_candidates = _clean_all_candidates(force_exclude=False)
                (
                    carry_seed_grid, carry_locked_letters, _,
                    winning_blank_impossible_cells, winning_lettered_impossible_cells,
                ) = max(
                    cleaned_candidates,
                    key=lambda sc: _words_in_place_score(sc[2], sc[1]),
                )
                # Point fixe détecté : ce palier n'a produit aucun changement du
                # tout (les lettres confirmées sont rigoureusement identiques à
                # celles du palier précédent) — un vrai blocage qui, sans
                # intervention, se reproduirait à l'identique indéfiniment (voir
                # `_build_retry_seed`'s docstring pour l'historique complet de ce
                # cas). À la demande explicite de l'utilisateur, ce n'est
                # traité qu'en dernier recours, seulement une fois ce blocage
                # réellement constaté : le même nettoyage est relancé sur les 6
                # mêmes candidats avec `exclude_impossible_locked=True`, qui
                # retire spécifiquement tout emplacement verrouillé dont la
                # combinaison ne correspond à aucun mot réel — cassant le point
                # fixe sans jamais appliquer cette règle plus agressive aux
                # paliers qui progressent normalement.
                #
                # Une version plus fine (comparant, pour chaque tentative
                # parallèle brute plutôt que seulement la gagnante, si une
                # affectation réelle a eu lieu) a été essayée puis abandonnée
                # à la demande explicite de l'utilisateur ("il n'essaye pas
                # vraiment de remplir les grilles partielles... revenir à la
                # situation précédente") — retour à cette comparaison plus
                # simple sur la seule gagnante.
                if previous_locked_letters is not None and carry_locked_letters == previous_locked_letters:
                    cleaned_candidates = _clean_all_candidates(force_exclude=True)
                    (
                        carry_seed_grid, carry_locked_letters, _,
                        winning_blank_impossible_cells, winning_lettered_impossible_cells,
                    ) = max(
                        cleaned_candidates,
                        key=lambda sc: _words_in_place_score(sc[2], sc[1]),
                    )
                # Verrouiller UNE seule case noire au hasard parmi les cases
                # des emplacements injouables de la candidate retenue, à la
                # demande explicite de l'utilisateur : "tentative de ne pas
                # reproduire les mêmes erreurs en verrouillant progressivement
                # les configurations problématiques." Une seule case au total
                # pour ce palier — pas une par candidate nettoyée ci-dessus —
                # donc appliquée une fois que la candidate gagnante (et, le
                # cas échéant, sa version "point fixe cassé" ci-dessus) est
                # définitivement connue. S'applique aussi bien ici (le
                # nettoyage complet) qu'à la reprise "telle quelle" ci-dessus
                # (voir son propre commentaire, plus haut) — à la demande
                # explicite de l'utilisateur, après avoir remarqué que la
                # première version ne l'appliquait qu'au nettoyage complet :
                # "il faut ajouter une case noire à tous les tours où on
                # nettoie les emplacements injouables (pas seulement quand on
                # nettoie aussi les cases noires)" — la reprise "telle quelle"
                # nettoie elle aussi les emplacements bloqués (voir
                # `_clean_blocked_slots` juste au-dessus), donc cette règle
                # s'y applique tout autant ; elle reste la SEULE exception au
                # principe "aucune case noire touchée" de ce chemin-là.
                # `_impossible_cell_groups`/`_lock_one_impossible_cell`
                # implémentent la priorité aux cases encore blanches (voir
                # leurs propres docstrings) — la même logique que ci-dessus,
                # factorisée pour être appelée depuis les deux chemins sans
                # dupliquer le calcul.
                _lock_one_impossible_cell(
                    carry_seed_grid, rows, cols,
                    winning_blank_impossible_cells, winning_lettered_impossible_cells, rng,
                )
            # Le ratio cible ne progresse plus d'un palier à l'autre (reste
            # fixé à `black_ratio`, 0.0 par défaut), à la demande explicite
            # de l'utilisateur : le pré-remplissage (au moins
            # PREFILL_MIN_WORD_COUNT candidats par emplacement) combiné à la
            # reprise sur la grille nettoyée du palier précédent
            # (_build_retry_seed juste au-dessus) suffit à faire progresser
            # la recherche, sans avoir besoin de densifier artificiellement
            # la grille palier après palier.

    if best is None:
        # "Continuer" button on the web UI (see _serialize_resume_state's
        # own docstring above), at the user's explicit request — `None`
        # only in the degenerate case where the palier loop never ran at
        # all (`attempts=0`, never used by any real caller), since
        # `carry_seed_grid` is otherwise always set by the very first
        # failed palier onward.
        resume_state = (
            _serialize_resume_state(
                carry_seed_grid, carry_locked_letters,
                carry_preseed_assignment, carry_excluded_slots,
            )
            if carry_seed_grid is not None else None
        )
        progress("pattern_failed", attempts=attempts, last_attempt=last_diag,
                  examples=last_examples, total_attempts=total_attempts_tried,
                  resume_state=resume_state)
        return None
    progress("pattern_found", attempt=attempt + 1, total_attempts=total_attempts_tried)

    # Aperçu de la grille juste avant l'optimisation, réutilisant le même
    # mécanisme que l'aperçu d'une tentative échouée (try_fill's
    # diagnostics["example_grid"]). Contient désormais les vraies lettres
    # (`build_letters_grid`, la même fonction déjà utilisée pour
    # `result["solution"]` plus bas), pas seulement le motif noir/blanc nu
    # — reverti à la demande explicite de l'utilisateur par rapport à la
    # toute première version de cet aperçu (qui l'omettait délibérément) :
    # côté client, `renderAttemptPreview()` masque déjà ces lettres par
    # défaut et ne les révèle que si l'utilisateur active
    # #attempt-preview-reveal-btn (voir style-guide SKILL), donc les
    # transmettre ici ne les affiche pas pour autant — c'est le même
    # mécanisme de masquage qu'une tentative échouée, pas un nouveau.
    # `best_result` est `(slots, assignment)` (voir _pattern_attempt/
    # try_fill's contrat de retour) — passé tel quel à build_letters_grid,
    # qui construit une toute nouvelle grille (jamais une modification de
    # `best` en place), donc aucune copie défensive n'est nécessaire ici
    # contrairement à l'ancienne version qui transmettait `best` lui-même.
    # `impossible_cells`/`forced_cells`/`locked_cells` sont explicitement
    # vidées (et non simplement omises) pour effacer un éventuel aperçu
    # resté affiché d'une tentative précédemment échouée pendant la
    # recherche du motif — un motif entièrement réussi n'a ni case
    # impossible, ni lettre forcée, ni case verrouillée à signaler.
    # Transmis via `examples` (une liste d'un seul élément) — même format
    # que `pattern_attempt_failed`/`pattern_failed` ci-dessus (jusqu'à 6
    # éléments) — pour que backend/app.py et le frontend n'aient qu'un
    # seul mécanisme d'aperçu à gérer, que ce soit 1 grille ou 6.
    best_slots, best_assignment = best_result
    progress(
        "minimizing",
        examples=[{
            "example_grid": build_letters_grid(rows, cols, best_slots, best_assignment),
            "impossible_cells": [],
            "forced_cells": [],
            "locked_cells": [],
        }],
    )
    grid, slots, assignment = minimize_black_squares(
        best, best_result, rows, cols, index, rng, cancel_event=cancel_event
    )
    n_black = sum(row.count(BLACK) for row in grid)
    words = build_word_entries(grid, rows, cols, slots, assignment)
    for w in words:
        w["accented"] = accents.get(w["answer"], w["answer"])
        w["canonical"] = canonicals.get(w["answer"], [w["accented"]])
    progress("grid_ready", word_count=len(slots), black_count=n_black)
    return {
        "width": cols,
        "height": rows,
        "pattern": grid,
        "solution": build_letters_grid(rows, cols, slots, assignment),
        "words": words,
        "word_count": len(slots),
        "black_count": n_black,
        "black_ratio": n_black / (rows * cols),
    }


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--width", type=int, default=DEFAULT_WIDTH,
                     help=f"largeur de la grille, nombre de colonnes (défaut : {DEFAULT_WIDTH})")
    ap.add_argument("--height", type=int, default=DEFAULT_HEIGHT,
                     help=f"hauteur de la grille, nombre de lignes (défaut : {DEFAULT_HEIGHT})")
    ap.add_argument("--wordlist", default="data/wordlist_fr_full.tsv",
                     help="lexique MOT<TAB>ACCENTUE<TAB>FREQUENCE généré par "
                          "build_wordlist_freq.py (ou fichier texte libre en repli)")
    _difficulty_help = (
        "limite le vocabulaire à une fraction des mots les plus fréquents au "
        "global (toutes longueurs confondues), calculée sur la taille réelle "
        "du lexique de la langue chargée : easy={:.0%} (défaut), medium={:.0%}, "
        "hard=100% (tout le lexique)"
    ).format(DIFFICULTY_PRESETS["easy"], DIFFICULTY_PRESETS["medium"])
    ap.add_argument(
        "--difficulty", choices=sorted(DIFFICULTY_PRESETS), default="easy",
        # argparse fait lui-même une passe de substitution % sur les help
        # strings (pour %(default)s etc.) — un "%" litéral issu de {:.0%}
        # ci-dessus doit être échappé en "%%" *après* le formatage (jamais
        # dans le format-spec de .format() lui-même, qui n'accepte que "%"),
        # sinon argparse lève une ValueError.
        help=_difficulty_help.replace("%", "%%"),
    )
    ap.add_argument("--max-words", type=int, default=None,
                     help="surcharge manuelle du nombre max de mots au global "
                          "(prioritaire sur --difficulty)")
    ap.add_argument("--black-ratio", type=float, default=0.0,
                     help="densité de cases noires visée au départ (0-1), en plus de "
                          "celles déjà posées par la phase de pré-remplissage")
    ap.add_argument("--attempts", type=int, default=200,
                     help="nombre de motifs essayés avant d'abandonner")
    ap.add_argument("--seed", type=int, default=None)
    args = ap.parse_args()

    result = generate_grid(
        width=args.width,
        height=args.height,
        difficulty=args.difficulty,
        max_words=args.max_words,
        black_ratio=args.black_ratio,
        attempts=args.attempts,
        seed=args.seed,
        wordlist_path=args.wordlist,
    )

    if result is None:
        print(f"Échec : aucune grille remplissable trouvée en {args.attempts} essais.",
              file=sys.stderr)
        print("Essayez une grille plus petite, --black-ratio plus élevé, ou un dictionnaire plus riche.",
              file=sys.stderr)
        sys.exit(1)

    print(f"Grille {result['width']}x{result['height']} — {result['black_count']} cases noires "
          f"({100 * result['black_ratio']:.1f}%)\n")
    print("Motif :")
    print_grid(result["pattern"])
    print("\nSolution :")
    print_grid(result["solution"])
    print(f"\n{result['word_count']} mots placés.")


if __name__ == "__main__":
    main()
