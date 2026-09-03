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
     chaque palier, `PARALLEL_ATTEMPTS` (le nombre de processeurs de la
     machine par défaut, paramétrable via CROSSWORDFALCON_PARALLEL_ATTEMPTS
     dans env.sh) tentatives indépendantes
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
import queue
import random
import re
import sys
import threading
import time
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

# Number of attempts (pattern + CSP fill) launched in parallel at each
# black-cell-ratio palier — see generate_grid(). The machine is typically far
# from saturating its CPU with a single sequential attempt at a time, so
# running one attempt per available CPU makes full use of it. Defaults to
# `os.cpu_count()` (the number of CPUs this machine reports, at the user's
# explicit request: "Nombre de process lancés en parallèle = nombre de
# processeurs de la machine" — replacing a previous fixed default of 10,
# which had no relationship to the actual hardware a given deployment runs
# on) — `or 1` guards the documented edge case where `os.cpu_count()` itself
# can't determine a count and returns `None`, so this never becomes `0` (which
# would make `ProcessPoolExecutor(max_workers=0)` fail outright). Still
# overridable via the CROSSWORDFALCON_PARALLEL_ATTEMPTS environment variable
# (see env.sh/env_default.sh — sourced by run_Falcon.sh before starting the
# back end, so effective for the web API; the CLI, launched directly, reads
# it too if already exported in the current shell), at the user's own earlier
# explicit request, for a deployment that wants a different number regardless
# of the machine's own core count.
PARALLEL_ATTEMPTS = (
    int(os.environ["CROSSWORDFALCON_PARALLEL_ATTEMPTS"])
    if os.environ.get("CROSSWORDFALCON_PARALLEL_ATTEMPTS")
    else (os.cpu_count() or 1)
)

# Le nombre d'exemples de tentatives échouées montrés en aperçu (voir
# generate_grid's "pattern_attempt_failed"/"pattern_failed", et
# frontend/static/script.js's renderAttemptPreview) n'est plus plafonné à
# une valeur fixe (`FAILED_ATTEMPT_EXAMPLES`, autrefois 6) — à la demande
# explicite de l'utilisateur : "Afficher toutes les meilleures grilles
# dans l'aperçu, pas seulement les 6 meilleures." Toutes les tentatives
# distinctes du palier (jusqu'à PARALLEL_ATTEMPTS, le nombre de
# processeurs de la machine par défaut) sont désormais montrées, sans
# aucune troncature — affichées sur autant de lignes de 3 grilles que
# nécessaire (`grid-template-columns: repeat(3, auto)`, voir style.css,
# qui n'a jamais imposé de nombre de lignes fixe et n'a donc eu besoin
# d'aucun changement pour ce retrait). Historique complet (la première
# version, un seul exemple ; puis un plafond fixe de 6 sur 2 lignes de 3 ;
# puis ce retrait complet du plafond) dans CLAUDE.md.


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
    (by_length, accents, canonicals, frequencies) :
    - by_length = {longueur: [mots]} — seuls les `max_words` mots les plus
      fréquents *au global* (toutes longueurs confondues), si fourni, sont
      conservés, puis regroupés par longueur pour le solveur CSP ;
    - accents = {MOT: forme accentuée/naturelle}, pour les mots retenus dans
      by_length (sert à donner au LLM la vraie orthographe — genre, nombre,
      conjugaison — quand il génère les définitions ; voir backend/clues.py) ;
    - canonicals = {MOT: [forme(s) canonique(s)/lemme(s)]}, pour les mots
      retenus dans by_length (sert à chercher une définition de dictionnaire
      par lemme plutôt que par forme fléchie ; voir backend/clues.py) ;
    - frequencies = {MOT: fréquence brute (float)}, pour les mots retenus
      dans by_length — passée à `build_index` (voir `NOISE_FREQUENCY_
      THRESHOLD`/`_noise_slot_cells`), pour distinguer un candidat
      statistiquement crédible d'une entrée quasi nulle du dictionnaire
      (bruit de corpus, sigle, fragment étranger)."""
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
    frequencies = {}
    for word, (accented, freq, canonical) in ranked:
        result[len(word)].append(word)
        accents[word] = accented
        canonicals[word] = canonical
        frequencies[word] = freq
    return dict(result), accents, canonicals, frequencies


# ---------- Génération du motif de cases noires ----------

# Longueur minimale "normale" (esthétique, pas absolue — la vraie limite
# jamais franchie, connexité/absence de case orpheline, reste le littéral
# `min_interior_free=1` passé explicitement par tous les autres appelants,
# voir plus bas) d'une zone blanche *interne* (encadrée par une case noire
# des deux
# côtés), utilisée comme valeur par défaut de `is_structurally_valid` et
# comme point de départ de la cascade de relaxation de `_place_black_
# cells` (voir sa propre docstring) — nommée et fixée à 8 (relevée de 3) à
# la demande explicite de l'utilisateur : "Attribuer un nom de variable à
# cette règle **au moins 3 cases**. Fixer ce nombre à 8. Si aucune case ne
# peut être posée en respectant ce nombre pour atteindre l'objectif de
# remplissage des noires, abaisser le nombre et recommencer à essayer de
# placer des noires." Ce dernier point — abaisser le nombre et
# retenter — est exactement ce que `_place_black_cells` faisait déjà,
# jusqu'ici avec une cascade figée à 3 niveaux (3, 2, 1) : généralisé
# pour redescendre d'un cran à la fois depuis cette constante jusqu'à 1
# (`range(STRUCTURAL_MIN_INTERIOR_FREE, 0, -1)`), pour que la relaxation
# progressive reste cohérente quelle que soit la valeur choisie ici,
# plutôt que 3 paliers fixes indépendants de ce nombre.
STRUCTURAL_MIN_INTERIOR_FREE = 8


def is_structurally_valid(grid, rows, cols, min_interior_free=STRUCTURAL_MIN_INTERIOR_FREE):
    """Une grille est valide si :
    - toute zone blanche *interne* (encadrée par une case noire des deux
      côtés) fait au moins `min_interior_free` cases (`STRUCTURAL_MIN_
      INTERIOR_FREE`, 8, par défaut),
      **sauf** si l'une de ses deux extrémités touche directement le bord de
      la grille (ligne/colonne 0, ou la dernière) : une telle zone de bord
      reste toujours autorisée, quelle que soit sa longueur (y compris 1 ou
      2 cases) et quel qu'en soit le nombre sur la grille entière — aucun
      budget ni compteur, contrairement à un ancien système à ce sujet
      (voir le SKILL project-best-practices). `min_interior_free` existe
      pour `_place_black_cells`, à la demande explicite de l'utilisateur :
      si l'exigence par défaut (`STRUCTURAL_MIN_INTERIOR_FREE`) ne laisse
      plus que des cases adjacentes à une autre case noire, elle est
      abaissée d'un cran à la fois jusqu'à 1 pour cette tentative de
      placement précise (voir sa docstring) — tous les autres appelants
      (`minimize_black_squares` compris) utilisent `min_interior_free=1`
      explicitement (la vraie limite absolue — connexité et absence de
      case orpheline, jamais l'exigence esthétique ci-dessus), jamais la
      valeur par défaut de cette fonction. Une zone d'une seule lettre ne
      devient jamais un
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
    deux.

    Un morceau **entièrement** verrouillé (chacune de ses cases déjà dans
    `locked_letters` — donc un mot déjà réel et confirmé) ne compte jamais
    comme "cassé" ici, quel que soit son propre nombre de candidats — même
    correctif que `_slot_with_insufficient_candidates` ci-dessus, et pour
    la même raison exacte : la quasi-totalité des mots réels ne
    correspondent qu'à eux-mêmes dans le dictionnaire (1 seul candidat),
    un nombre presque toujours sous `PREFILL_LOCKED_MIN_WORD_COUNT` — sans
    ce correctif, poser une case noire qui isolerait proprement un mot déjà
    confirmé pouvait être rejeté à tort, comme si cette case noire
    "cassait" un emplacement, alors qu'elle ne fait qu'isoler un mot déjà
    résolu n'ayant besoin d'aucun candidat supplémentaire."""
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
        if locked_letters:
            locked_count = sum(1 for cell in cells if cell in locked_letters)
            if 0 < locked_count < length:
                if _slot_candidate_count(index, length, cells, locked_letters) < PREFILL_LOCKED_MIN_WORD_COUNT:
                    return True
    return False


def _place_black_cells(grid, rows, cols, row_black, col_black, candidates, target, placed,
                        index=None, locked_letters=None, available_lengths=None,
                        forbid_adjacency=False):
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
    `min_interior_free=STRUCTURAL_MIN_INTERIOR_FREE`, 8 — au moins 8
    cases libres par emplacement interne). Si cette exigence ne laisse
    plus aucune candidate à la fois isolée et valide, elle est abaissée
    d'un cran à la fois (7, puis 6, ... jusqu'à 1), à la demande explicite
    de l'utilisateur ("si aucune case ne peut être posée en respectant ce
    nombre... abaisser le nombre et recommencer à essayer de placer des
    noires"), avant d'accepter l'adjacence : cette relaxation ne s'applique
    qu'à cette tentative de placement précise, pas à la grille entière ni
    aux tentatives suivantes. Seulement si aucune candidate isolée ne
    fonctionne à aucun de ces niveaux, on accepte l'adjacence et on
    retente la même cascade (`STRUCTURAL_MIN_INTERIOR_FREE` jusqu'à 1, un
    cran à la fois) sans plus exiger l'isolement — **sauf si
    `forbid_adjacency` est vrai** (`False` par
    défaut, chaque appelant existant avant ce paramètre inchangé), auquel
    cas cette toute dernière tentative (accepter l'adjacence) est
    entièrement sautée : à la demande explicite de l'utilisateur, "Lors de
    la première initialisation des cases noires, interdire tout tirage qui
    placerait 2 cases noires avec un côté adjacent" — `make_pattern` passe
    `forbid_adjacency=True` uniquement quand `seed_grid` est `None` (la
    toute première grille d'un appel à `generate_grid()`, entièrement
    blanche), jamais pour un palier qui reprend un motif déjà
    partiellement noirci d'un palier précédent, où l'adjacence reste
    acceptée en dernier recours exactement comme avant. Aucune candidate
    isolée trouvée dans toute la fenêtre à ce stade se comporte alors
    exactement comme le cas résiduel ci-dessous — la meilleure candidate
    est refusée et retirée du lot, la boucle continue avec le reste du
    pool, jamais un plantage ni un blocage. Dans le cas résiduel où même
    cela ne trouve rien dans toute la fenêtre (les 32 candidates cassent
    toutes la connexité ou créent une case orpheline, ou — avec
    `forbid_adjacency` — sont toutes adjacentes à une case déjà noire), la
    meilleure candidate au sens du critère principal est simplement
    refusée et retirée du lot, pour garantir que la boucle progresse
    toujours.

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
        for min_free in range(STRUCTURAL_MIN_INTERIOR_FREE, 0, -1):
            chosen = _first_valid(non_adjacent, min_free)
            if chosen is not None:
                break
        if chosen is None and not forbid_adjacency:
            for min_free in range(STRUCTURAL_MIN_INTERIOR_FREE, 0, -1):
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
# ci-dessous. Historiquement fixé à 10 (contre un seuil initial d'un seul
# mot, relevé après une régression réelle sur le banc d'essai 15×10 — voir
# l'historique de PREFILL_LOCKED_MIN_WORD_COUNT juste en dessous pour le
# même type de mesure), puis aligné à 3 à la demande explicite de
# l'utilisateur, pour rester cohérent avec le critère d'impossibilité de
# remplissage utilisé par `_slot_with_insufficient_candidates`/
# `_new_black_cell_breaks_locked_slot` (voir PREFILL_LOCKED_MIN_WORD_COUNT
# ci-dessous) — les deux constantes valent désormais 3, même si elles
# restent deux constantes séparées (elles s'appliquent à deux vérifications
# différentes : une longueur seule ici, une combinaison exacte de lettres
# verrouillées à des positions précises pour l'autre). Vérifié en direct
# après l'alignement : le banc d'essai standard 15×10 (graines 2 et 7)
# réussit toujours sans régression à cette nouvelle valeur.
PREFILL_MIN_WORD_COUNT = 3

# Same idea as PREFILL_MIN_WORD_COUNT above, but for the position-aware
# re-check the pre-fill phase also runs against a slot touching at least
# one already-locked letter (see _slot_with_insufficient_candidates/
# _new_black_cell_breaks_locked_slot) — a deliberately separate constant
# from PREFILL_MIN_WORD_COUNT above, even though both now share the same
# value (3): this one guarantees genuine *existence* of at least one real
# word matching a slot's *exact* locked letters at their *exact*
# positions (a far stronger, much rarer condition than merely having
# enough words of the right length), while the other only ever checks
# length alone.
#
# First set to the user's own literal value (1), then verified live —
# not just assumed safe: two real `generate_grid()` runs on the standard
# 15×10 benchmark (seeds 2 and 7, previously reliable throughout this
# project's entire history) both failed outright at threshold 1 (73.3s
# and 88.5s respectively, exhausting all 200 paliers) — a real
# regression, confirmed reproducible, not a fluke: a slot locked down to
# exactly one real candidate word is extremely fragile, since that one
# word conflicting with even a single crossing letter anywhere makes the
# slot permanently impossible, with pre-fill no longer stepping in to
# shorten/avoid it. Reported to the user with this measurement; the user
# chose an intermediate threshold (3) over keeping 1 (accepting the
# regression) or reverting to 10 outright.
PREFILL_LOCKED_MIN_WORD_COUNT = 3

# Fréquence brute (colonne FREQUENCE de data/wordlist_<lang>_full.tsv, voir
# load_wordlist) en dessous de laquelle un candidat n'est plus compté comme
# "jouable" par `_noise_slot_cells` (voir sa propre docstring) — à la
# demande explicite de l'utilisateur, pour distinguer une case réellement
# injouable (aucun candidat réel, déjà couverte par le surlignage rouge
# .impossible) d'une case techniquement non vide mais dont les seuls
# candidats restants sont soit déjà utilisés ailleurs dans la grille, soit
# du bruit de corpus (sigle, fragment étranger, artefact d'OCR) plutôt que
# de vrais mots crédibles.
#
# Calibrée en direct sur le lexique français réel : les 10 plus faibles
# fréquences de longueur 2 et 3 sont sans exception du bruit reconnaissable
# (« ΔT », « Nʼ », « ZL », « ΜG »... ; « GLX », « ITO », « TEO », « ZIO »...),
# tandis que le cas concret ayant motivé cette fonctionnalité (une grille
# bloquée sur 3 cases pendant 11 paliers consécutifs, voir CLAUDE.md)
# montrait `ESR`=3.0, `GSR`=1.0, `KSS`=1.0, `TSS`=4.0 — aucun n'est un vrai mot
# français — contre `VOS`=27330.0 et `SE`, tous deux courants. Un seuil de 5
# écarte ces quatre entrées de bruit sans toucher aux mots réels, mais
# reste délibérément conservateur : à ce seuil, seuls 4.5 % des mots de
# longueur 2 et 10.3 % de ceux de longueur 3 du lexique français réel sont
# exclus (mesuré en direct, `data/wordlist_fr_full.tsv`).
NOISE_FREQUENCY_THRESHOLD = 5

# Minimum number of new black cells always guaranteed to a single zone
# during the "nettoyage curatif" budget check (see
# `_prefill_unfillable_slots`) before that zone's own percentage-scaled
# budget (`fill_objective_fraction * zone_white_count`) is allowed to
# restrict it further — at the user's explicit request, after a real
# regression measured live: the grid's own overall black-cell fill
# objective (10-14% by default) applied *directly* to a single zone's own
# size (typically 8-15 cells) left a budget of 0 or 1 cell for almost
# every zone (e.g. exactly 0 for any zone of 9 cells or fewer at 10%) —
# confirmed by re-running the standard 15×10 benchmark (seeds 2 and 7,
# previously reliable throughout this project's entire history), which
# both failed outright (148.2s/180.4s, exhausting all 200 paliers) once
# this was wired in without a floor. Reported to the user with this
# measurement; the user chose a guaranteed per-zone floor over either a
# single grid-wide cumulative budget or reverting the whole mechanism.
#
# Tried as a *shared* budget instead (one pool of 2 across every zone
# `_prefill_unfillable_slots` is tracking at once, not 2 per zone) later
# in this project's history, at the user's own explicit request, after
# they pointed out the arithmetic the per-zone scoping implied ("si 3
# emplacements à problème, on monterait à 6 cases en plus autorisées") —
# then reverted again immediately, by the user's own explicit follow-up
# instruction, once a real regression was measured live on the exact
# same standard benchmark this floor was originally introduced to fix:
# seed 2 failed outright, seed 7 succeeded but 3.5× slower (355.4s vs.
# ~102s). Back to the original per-zone scoping — see the
# `project_nettoyage_curatif_paused` memory note for the full trail of
# both attempts, kept for any future session that revisits this area.
#
# Lowered from 2 to 1, at the user's own later explicit request, quoting
# this exact floor's own docstring back: "2 cases minimum crée trop de
# cases noires" — a real, opposite-direction complaint from the one that
# originally raised this floor from 0/1 to 2 (that earlier regression was
# about too FEW guaranteed cells causing outright failures; this one is
# about too MANY black cells being added in practice once every zone gets
# at least 2). Verified live rather than assumed safe, given this exact
# constant's own fraught history: two real `generate_grid()` runs on the
# standard 15×10 benchmark (seeds 2 and 7, Flash mode) both succeeded — 0
# mismatches, 0 empty white cells each — confirming this specific value
# (unlike the *shared*-budget alternative tried and reverted above, and
# unlike the unrelated `PREFILL_LOCKED_MIN_WORD_COUNT=1` regression noted
# further above) doesn't reproduce either of those earlier failures.
PREFILL_ZONE_BLACK_BUDGET_FLOOR = 1

# Fraction of extra black cells drawn after the pre-fill phase (see
# make_pattern), at the user's explicit request: "rétablir un tirage de 5%
# de nouvelles cases ajoutées (5% par rapport au nombre de cases blanches
# restantes)", raised to 10% at the user's own explicit (and immediate)
# follow-up request right after. Reinstated after the old cross-palier
# ratio-escalation mechanism was removed entirely (see generate_grid) — but
# deliberately different from that old mechanism: this is a fixed draw
# (never escalated palier to palier) expressed as a percentage of the cells
# *still white after pre-fill*, not of the grid's total cell count.
# Pre-fill already places whatever is structurally necessary (at least
# PREFILL_MIN_WORD_COUNT candidates per slot); this further draw, purely
# aesthetic/density-driven, only ever applies on top and never removes
# anything pre-fill has already placed.
# Became a *default* rather than a fixed constant, at the user's explicit
# request: `generate_grid`/`make_pattern` now accept `black_enrichment_
# fraction` as a parameter — tunable from the web UI (a "Taux noir"
# selector, a free-text 0-100 integer field, 14% by default — see
# GenerateRequest.black_enrichment_percent in backend/app.py) rather than
# fixed at 10% for everyone. This constant remains the default value for
# any caller that doesn't specify one (the CLI, notably).
#
# Applies only at a fresh-pattern palier — the very first one, or any
# palier immediately following a full cleanup (`_build_retry_seed`) —
# never to a "reprise telle-quelle" palier (`_pattern_continue`), which
# never calls `make_pattern` at all. A SEPARATE mechanism that used to add
# one extra black cell on top of this, on every single palier including
# "reprise telle-quelle" ones (`_impossible_cell_groups`/`_lock_one_
# impossible_cell`, a single-cell lock targeting whichever cells belonged
# to an impossible/blockage slot), was removed entirely, at the user's
# explicit request — this density-percentage mechanism itself was never
# meant to be removed, only that separate per-cycle single-cell lock. See
# CLAUDE.md for the full history of both mechanisms, including the
# removal of the single-cell lock and this mechanism's own brief,
# mistaken removal and restoration in the same session.
POST_PREFILL_BLACK_FRACTION = 0.10


def _slot_candidates(index, length, cells, known_letters):
    """Mots candidats réels pour un emplacement de longueur `length`
    couvrant `cells`, compte tenu des lettres déjà connues à certaines de
    ses cases (`known_letters`, un dict case->lettre) — pas seulement de sa
    longueur. Même logique d'intersection par position que `Filler._domain`
    (`idx["pos"][pos][lettre]`, filtré/intersecté position par position),
    mais utilisable ici en dehors de toute recherche CSP en cours (avant
    même qu'elle démarre, pendant la génération du motif, ou pour le
    sondage statistique des graines) — voir `_slot_candidate_count`
    (compte seulement) et `_force_single_candidate_slots`/
    `sample_letter_biases` (mots réels, pas seulement leur nombre) qui s'en
    servent tous les trois plutôt que de dupliquer cette même intersection.
    Renvoie `idx["words"]` (le lexique entier de cette longueur, une liste)
    si aucune case de cet emplacement n'est encore connue ; un ensemble
    vide si l'index n'a aucun mot de cette longueur, ou si les lettres
    connues ne correspondent à aucun mot réel."""
    idx = index.get(length)
    if idx is None:
        return ()
    constraints = {}
    for pos, cell in enumerate(cells):
        letter = known_letters.get(cell)
        if letter is not None:
            constraints[pos] = letter
    if not constraints:
        return idx["words"]
    sets = [idx["pos"][pos].get(ch) for pos, ch in constraints.items()]
    if any(not s for s in sets):
        return ()
    sets = sorted(sets, key=len)
    result = sets[0]
    for s in sets[1:]:
        result = result & s
        if not result:
            return ()
    return result


def _slot_candidate_count(index, length, cells, locked_letters):
    """Nombre de mots candidats pour un emplacement — voir `_slot_candidates`
    pour la logique elle-même ; ne calcule que ce qui est nécessaire pour
    savoir si le compte atteint `PREFILL_MIN_WORD_COUNT` ou non (voir son
    propre appelant), pas un besoin de connaître les mots eux-mêmes."""
    return len(_slot_candidates(index, length, cells, locked_letters))


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
            if _slot_candidate_count(index, length, slot, locked_letters) < PREFILL_LOCKED_MIN_WORD_COUNT:
                return True
    return False


def _slot_with_insufficient_candidates(grid, rows, cols, available_lengths, index=None,
                                        locked_letters=None, skip=None):
    """Comme `_has_slot_without_candidate` (True/False), mais renvoie
    l'emplacement lui-même (sa liste de cases) dès qu'il en trouve un dont
    la longueur n'est pas dans `available_lengths` (moins de
    `PREFILL_MIN_WORD_COUNT` mots dans le dictionnaire pour cette longueur
    en général) — ou, quand `locked_letters` couvre au moins une de ses
    cases, dont l'intersection avec ces lettres précises (voir
    `_slot_candidate_count`) laisse moins de `PREFILL_LOCKED_MIN_WORD_COUNT`
    candidats — un seuil bien plus bas que celui de la longueur seule, à la
    demande explicite de l'utilisateur (un seuil d'1 seul mot a été essayé
    puis écarté après vérification en direct : le banc d'essai standard
    15×10 échouait alors sur des seeds qui réussissaient jusque-là de façon
    fiable, un emplacement réduit à un unique candidat étant trop fragile
    au moindre conflit croisé — voir la définition de
    `PREFILL_LOCKED_MIN_WORD_COUNT` pour la mesure complète) ; `None` si
    tous les emplacements sont corrects (ou déjà dans
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
    progresser sur les *autres* emplacements réellement corrigibles.

    Un emplacement **entièrement** couvert par `locked_letters` (chacune de
    ses cases déjà verrouillée — donc déjà un mot réel et confirmé, pas un
    emplacement encore à résoudre) n'est **jamais** considéré insuffisant,
    quel que soit son propre nombre de candidats — bug réel trouvé et
    corrigé après un rapport direct de l'utilisateur, deux captures
    d'écran à l'appui : un mot déjà verrouillé et confirmé (ex. "AVALAS")
    disparaissait entre l'aperçu "avant" et l'aperçu "après" d'une même
    tentative, alors que les cases noires restaient rigoureusement
    identiques (donc pas une nouvelle grille indépendante — voir plus haut
    pour ce cas-là, déjà corrigé séparément). Root-causé en direct plutôt
    que supposé : `_slot_candidate_count` compte, pour la vaste majorité
    des mots réels d'une longueur donnée, **exactement 1** résultat (le mot
    lui-même — la plupart des mots de 5 lettres ou plus sont la seule
    entrée du dictionnaire à correspondre exactement à leur propre
    orthographe), un nombre presque toujours strictement inférieur à
    `PREFILL_LOCKED_MIN_WORD_COUNT` (3) — avant ce correctif, ce test
    considérait donc à tort la quasi-totalité des mots déjà confirmés comme
    des emplacements "insuffisants" à corriger, déclenchant `_remove_a_
    crossing_word` (voir ci-dessous) pour un mot pourtant déjà résolu et
    n'ayant besoin d'aucune correction — reproduit et confirmé avec un
    dictionnaire miniature entièrement contrôlé : un mot fictif entièrement
    verrouillé ("AVOIR", seul mot de 5 lettres du dictionnaire à
    correspondre à cette orthographe précise, donc 1 candidat) était bien
    la toute première case retournée par cette fonction, avant même
    l'emplacement réellement problématique qu'il croise. Un emplacement
    entièrement verrouillé mais dont la combinaison ne correspond à
    *aucun* mot réel (véritablement impossible, pas seulement rare) n'est
    pas non plus renvoyé ici désormais — ce cas reste correctement détecté
    plus tard, une fois `make_pattern` revenu, par le mécanisme dédié de
    `_pattern_attempt`/`_pattern_continue` (`preseed_assignment`/
    `locked_impossible_slots`, qui valide chaque emplacement entièrement
    verrouillé auprès du dictionnaire et le laisse `None` s'il ne
    correspond à aucun mot réel — voir leurs propres docstrings) : le
    pré-remplissage n'a de toute façon aucun moyen utile d'agir sur un tel
    emplacement (aucune case n'y est disponible pour une case noire —
    toutes déjà verrouillées — et retirer un mot qui le *croise* ne change
    rien à ses propres lettres, déjà fixées par construction)."""
    for slot in extract_slots(grid, rows, cols):
        if skip and tuple(slot) in skip:
            continue
        length = len(slot)
        if length not in available_lengths:
            return slot
        if locked_letters:
            locked_count = sum(1 for cell in slot if cell in locked_letters)
            if 0 < locked_count < length:
                if _slot_candidate_count(index, length, slot, locked_letters) < PREFILL_LOCKED_MIN_WORD_COUNT:
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


def _remove_a_crossing_word(slot, grid, rows, cols, locked_letters, rng=None):
    """« Nettoyage curatif », à la demande explicite de l'utilisateur : au
    lieu de continuer à noircir `slot` (l'emplacement le moins remplissable —
    dont l'intersection avec les lettres déjà verrouillées ne laisse plus
    assez de candidats, voir `_slot_with_insufficient_candidates` — qui a
    déjà des lettres positionnées sur certaines de ses cases), retire un mot
    déjà confirmé qui **participe** à ces lettres déjà positionnées : un
    emplacement croisant `slot` — forcément dans l'autre sens, puisqu'il
    partage au moins une case avec lui — dont *toutes* les cases sont dans
    `locked_letters` (donc un vrai mot déjà verrouillé, pas seulement une
    case isolée), lui-même responsable d'au moins une des lettres qui rendent
    `slot` difficile à remplir. Plutôt que de choisir, parmi tous ces mots
    croisants, celui qui a lui-même le moins de possibilités de remplissage —
    un critère essayé puis explicitement écarté par l'utilisateur après une
    régression mesurée en direct (voir plus bas) — un choix est tiré au
    hasard (mélangé avec `rng`, l'aléa déjà seedé de cette tentative, pour
    rester reproductible et éviter tout biais de position, le même principe
    que partout ailleurs dans ce fichier) parmi tous les mots croisants
    trouvés, sans aucun critère de fragilité. Mute `locked_letters` en place
    (retire chacune des cases du mot choisi) et renvoie `True` si un mot a
    bien été retiré ; `False` si `slot` ne touche aucun mot verrouillé du
    tout (rien à retirer — le seul recours reste alors une case noire, ou
    déclarer l'emplacement irréparable).

    Anciennement `_remove_least_fillable_crossing_word` : une première
    version choisissait le mot croisant ayant lui-même le moins de
    candidats — l'idée étant de sacrifier le mot déjà le plus fragile,
    de toute façon le plus proche de devenir lui-même impossible au moindre
    autre conflit. Cette idée s'est révélée nuisible en pratique, constatée
    en direct sur le banc d'essai standard : même en désactivant entièrement
    le budget de cases noires (voir `PREFILL_ZONE_BLACK_BUDGET_FLOOR`), le
    seul fait de retirer un mot dans ce cas limite (aucune case noire
    disponible dans `slot`) suffisait à faire échouer une seed auparavant
    fiable — retirer *spécifiquement* le mot le plus fragile s'est avéré
    plus nuisible que bénéfique, sans doute parce qu'un mot déjà fragile
    n'est pas pour autant redondant : sa disparition peut priver la suite de
    la recherche d'une confirmation utile ailleurs dans la grille. À la
    demande explicite de l'utilisateur, ce critère de sélection est
    abandonné : n'importe quel mot croisant qui participe au problème peut
    être retiré, sans chercher à deviner lequel serait le "moins coûteux" à
    perdre."""
    if not locked_letters:
        return False
    slot_tuple = tuple(slot)
    slot_cells = set(slot)
    candidates = []
    for other in extract_slots(grid, rows, cols):
        if tuple(other) == slot_tuple:
            continue
        if not (slot_cells & set(other)):
            continue
        if not all(cell in locked_letters for cell in other):
            continue
        candidates.append(other)
    if not candidates:
        return False
    if rng is not None:
        rng.shuffle(candidates)
    chosen = candidates[0]
    for cell in chosen:
        locked_letters.pop(cell, None)
    return True


def _prefill_unfillable_slots(grid, rows, cols, row_black, col_black, candidates,
                               available_lengths, index=None, locked_letters=None, rng=None,
                               fill_objective_fraction=1.0, forbid_adjacency=False):
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
    limite accepté que ci-dessus).

    **« Nettoyage curatif »**, à la demande explicite de l'utilisateur,
    ajouté sur ce même mécanisme pour le cas d'un emplacement rendu
    insuffisant par des lettres déjà verrouillées (jamais pour le cas d'une
    longueur simplement trop rare — voir plus bas) : au lieu de continuer à
    noircir cet emplacement indéfiniment, un budget est maintenant respecté.
    Pour chaque emplacement problématique rencontré, sa taille d'origine
    (nombre de cases blanches qu'il couvrait à sa toute première détection,
    avant toute case noire ajoutée pour le corriger — suivie par
    `zone_footprints`, une liste de `[cases_d_origine, cases_noires_déjà_
    ajoutées]`, puisqu'une même zone peut être retrouvée plusieurs fois de
    suite, coupée en morceaux de plus en plus courts au fil des cases
    ajoutées ; un morceau est rattaché à la zone d'origine dont il est un
    sous-ensemble de cases, pas recréé comme une zone indépendante) sert de
    référence : tant que le nombre de nouvelles cases noires déjà ajoutées
    dans cette zone reste sous son propre budget (`zone_budget` —
    `fill_objective_fraction`, le même objectif de remplissage en noir que
    celui appliqué à la grille entière, voir `make_pattern`, appliqué à la
    taille d'origine de cette zone, mais **jamais moins de
    `PREFILL_ZONE_BLACK_BUDGET_FLOOR` (1) case garantie** — voir sa propre
    définition pour la régression réelle mesurée en direct, sans ce
    plancher, qui a motivé son ajout : ce pourcentage, 10-14 % par défaut,
    ramené directement à la taille typique d'une seule zone (souvent 8-15
    cases), laissait un budget de 0 ou 1 case pour la quasi-totalité des
    zones, au lieu d'un vrai budget proportionnel), une case noire continue
    d'être tentée normalement. Une fois ce budget dépassé (ou si aucune case
    noire disponible ne convient), plutôt que de déclarer aussitôt
    l'emplacement irréparable, `_remove_a_crossing_word` est tenté : retirer
    un mot déjà verrouillé qui croise cet emplacement (tiré au hasard parmi
    ceux qui y participent, sans chercher à deviner lequel serait le moins
    coûteux à perdre — voir sa propre docstring pour la régression que ce
    choix corrige) relâche une contrainte de lettre sans ajouter la moindre
    case noire supplémentaire — une façon de corriger l'emplacement qui
    évite de sur-noircir une seule zone bien au-delà de ce que l'objectif de
    remplissage global de la
    grille prévoit. Seulement marqué irréparable si ni une case noire ni un
    retrait de mot ne débloquent la situation — l'unique cas limite conservé
    de la version précédente. Pour un emplacement insuffisant à cause de sa
    seule longueur (`length not in available_lengths`, jamais causé par des
    lettres verrouillées), retirer un mot ne changerait rien à sa longueur —
    le budget/retrait est donc ignoré dans ce cas, qui garde exactement le
    comportement d'origine (case noire, ou irréparable)."""
    count = 0
    unfixable = set()
    zone_footprints = []  # [cases_d_origine (set), cases_noires_ajoutées (int)]
    while candidates:
        slot = _slot_with_insufficient_candidates(
            grid, rows, cols, available_lengths, index, locked_letters, skip=unfixable
        )
        if slot is None:
            break
        length = len(slot)
        is_length_problem = length not in available_lengths

        slot_set = set(slot)
        footprint = None
        for fp in zone_footprints:
            if slot_set <= fp[0]:
                footprint = fp
                break
        if footprint is None:
            footprint = [slot_set, 0]
            zone_footprints.append(footprint)
        zone_white_count = len(footprint[0])

        candidate_set = set(candidates)
        cells_in_slot = [cell for cell in slot if cell in candidate_set]
        if rng is not None:
            rng.shuffle(cells_in_slot)
        options = sorted(
            cells_in_slot,
            key=lambda cell: row_black[cell[0]] + col_black[cell[1]],
        )

        # Le budget par zone est le pourcentage de l'objectif de remplissage
        # global appliqué à la taille de *cette* zone, mais jamais moins de
        # `PREFILL_ZONE_BLACK_BUDGET_FLOOR` (1) case noire garantie — à
        # la demande explicite de l'utilisateur, après une régression réelle
        # constatée en direct : ce pourcentage (10-14 % par défaut), une fois
        # ramené à la taille typique d'une seule zone (souvent 8-15 cases),
        # laissait un budget de 0 ou 1 case pour la quasi-totalité des
        # zones — voir la docstring plus bas pour la mesure complète.
        #
        # Un budget PARTAGÉ entre toutes les zones (plutôt que par zone) a
        # été essayé un temps, à la demande explicite de l'utilisateur —
        # puis explicitement annulé par l'utilisateur lui-même une fois la
        # régression réelle qu'il causait mesurée en direct sur le banc
        # standard (seed 2 en échec complet, seed 7 3.5× plus lent) :
        # "Il ne faut pas changer le budget..." Revenu au budget par zone
        # d'origine — voir `PREFILL_ZONE_BLACK_BUDGET_FLOOR`'s propre
        # définition pour l'historique complet des deux essais.
        zone_budget = max(PREFILL_ZONE_BLACK_BUDGET_FLOOR,
                           int(fill_objective_fraction * zone_white_count))
        within_budget = (
            is_length_problem
            or zone_white_count == 0
            or (footprint[1] + 1) <= zone_budget
        )
        placed_one = False
        if within_budget:
            non_adjacent = [
                cell for cell in options if not _has_black_neighbor(grid, rows, cols, *cell)
            ]
            ordered_options = non_adjacent if forbid_adjacency else (
                non_adjacent + [cell for cell in options if cell not in set(non_adjacent)]
            )
            for (r, c) in ordered_options:
                grid[r][c] = BLACK
                if is_structurally_valid(grid, rows, cols, min_interior_free=1):
                    row_black[r] += 1
                    col_black[c] += 1
                    candidates.remove((r, c))
                    count += 1
                    footprint[1] += 1
                    placed_one = True
                    break
                grid[r][c] = WHITE
        if placed_one:
            continue

        if not is_length_problem and _remove_a_crossing_word(
            slot, grid, rows, cols, locked_letters, rng
        ):
            continue

        # Cet emplacement précis ne peut être corrigé ni par une case noire
        # disponible, ni par le retrait d'un mot verrouillé qui le croise
        # (typiquement : toutes ses cases sont déjà verrouillées par des
        # lettres d'un palier précédent sans qu'aucun mot croisant ne soit
        # lui-même verrouillé, ou aucune case ne préserve la connexité) — à
        # la demande explicite de l'utilisateur, ce n'est pas une raison
        # d'abandonner tout le pré-remplissage : on le marque pour ne plus
        # jamais le reproposer (`unfixable`) et on continue sur les autres
        # emplacements, qui restent corrigibles indépendamment. Ce résidu,
        # s'il subsiste jusqu'au remplissage CSP, échouera simplement là
        # normalement — et, dans le cas d'un mot verrouillé, sera retiré au
        # palier suivant par le même mécanisme de nettoyage qui retire déjà
        # tout mot croisant un emplacement impossible (voir _build_retry_seed).
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
    must be at least `min_interior_free` cells long
    (`STRUCTURAL_MIN_INTERIOR_FREE`, 8 by default — named and raised from
    an original 3 at the user's own later explicit request, see that
    constant's own docstring for the full reasoning); a zone
    touching the grid's own border on at least one side is always allowed,
    whatever its length and however many of them the grid ends up with.
    `_place_black_cells` reintroduces a preference for keeping black cells
    apart, at the user's explicit request, but expressed by relaxing this
    structural minimum rather than by a secondary tie-break criterion: among
    the window, it first looks for the best candidate (by the row/column
    criterion) that is *not* adjacent to any existing black cell and valid
    at `min_interior_free=STRUCTURAL_MIN_INTERIOR_FREE`; if that requirement
    leaves no such isolated candidate, it's relaxed one step at a time (7,
    then 6, ... down to 1), still only considering isolated candidates —
    only once even the most relaxed level finds none is adjacency accepted
    at all, again cascading `STRUCTURAL_MIN_INTERIOR_FREE` down to 1 before
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
    top by `_place_black_cells` below.

    This mechanism was briefly (mistakenly) removed entirely in the same
    session, along with a separate, unrelated per-cycle single-cell lock
    (`_impossible_cell_groups`/`_lock_one_impossible_cell`, see
    `generate_grid`'s own history) — the user's own follow-up correction
    clarified that only that separate lock was meant to go, not this
    density-percentage mechanism, which is still meant to apply at the
    very first palier and at every palier immediately following a full
    cleanup, exactly as it always has.

    `black_enrichment_fraction` is no longer applied as the fixed
    percentage it's given as, at the user's explicit later request: it is
    scaled by `initial_white_count / (rows * cols)` — the proportion of
    the grid still white, measured on *this* call's own starting state
    (including whatever `seed_grid` already carries forward), before this
    call's own pre-fill runs — right after `initial_white_count` is
    captured. This scaled value is what feeds both `fill_objective_
    fraction` (and therefore the curative-cleanup zone budget inside
    `_prefill_unfillable_slots`) and the `target` computation below — the
    raw, caller-supplied `black_enrichment_fraction` is never used
    directly again past this point. For the very first palier of a call
    (`seed_grid is None`, an entirely white grid), this proportion is
    always exactly 1 (`initial_white_count == rows * cols`), so the
    scaled rate equals the raw one — the very first grid's own behavior
    is unchanged. From then on, as successive paliers accumulate more
    black cells (whatever `seed_grid` is carried forward already has), the
    proportion — and so the effective rate applied by pre-fill — shrinks
    accordingly, without any caller needing to compute or pass this
    shrinking rate itself.

    Adjacency between two black cells is never accepted at all for the
    very first palier of a call (`seed_grid is None`), at the user's
    explicit request — see `_place_black_cells`'s own `forbid_adjacency`
    parameter (passed here as `seed_grid is None`) for the mechanics.
    Every later palier, which always starts from an already-partially-
    black `seed_grid` carried forward from a previous one, keeps the
    pre-existing behavior unchanged (adjacency still accepted as a last
    resort when no isolated candidate can be found)."""
    # Copie défensive de `locked_letters`, au même titre que celle déjà
    # faite pour `seed_grid` juste en dessous — bug réel constaté en
    # direct, capture d'écran à l'appui : des lettres verrouillées bien
    # présentes dans l'aperçu "pattern" (début de cycle) disparaissaient
    # de l'aperçu "pattern_generated" du même cycle, et pas seulement à
    # l'écran. Cause : cet aperçu reconstruit spéculativement, dans le
    # processus PARENT, le motif que le dernier worker non réinitialisé
    # va lui-même recalculer — en appelant `make_pattern` directement sur
    # `carry_locked_letters`, l'objet PARTAGÉ réellement transmis juste
    # après aux vrais workers dispatchés (`_pattern_attempt`). Or
    # `_prefill_unfillable_slots`/`_remove_a_crossing_word` (« nettoyage
    # curatif ») mutent leur propre paramètre `locked_letters` sur place
    # (retrait de cases par `.pop`) — un comportement sans risque pour un
    # vrai worker, qui ne reçoit jamais qu'une copie indépendante une fois
    # ses arguments transmis à son propre processus séparé, mais qui
    # endommageait ici l'état partagé du processus parent lui-même : une
    # simple reconstruction d'aperçu, censée être jetable, retirait
    # réellement des lettres confirmées de `carry_locked_letters` avant
    # même que les vrais workers de ce palier ne soient soumis — ceux-ci
    # recevaient donc, eux aussi, une version déjà amputée. Cette copie
    # protège tout appelant, pas seulement celui-là, exactement comme la
    # copie de `seed_grid` protège déjà tout appelant contre une mutation
    # similaire de la grille elle-même.
    locked_letters = dict(locked_letters) if locked_letters else locked_letters
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
    # Count of white cells *before* pre-fill — the base for the
    # `black_enrichment_fraction` computation below, at the user's
    # explicit request ("les cases noires ajoutées en pré-remplissage
    # comptent pour l'objectif de remplissage en noir"): the target
    # percentage is computed on this fixed total, not on however much
    # white remains once pre-fill is done.
    initial_white_count = len(candidates)

    # À la demande explicite de l'utilisateur : le taux fixe ("Taux noir",
    # `black_enrichment_fraction`) n'est plus appliqué tel quel dans les
    # phases de pré-remplissage — il est multiplié par la proportion de
    # cases blanches restantes (cases blanches restantes / cases totales
    # de la grille), mesurée sur CE palier précis (via `seed_grid` s'il y
    # en a un) avant que son propre pré-remplissage ne démarre. Pour la
    # toute première grille (aucun `seed_grid`, entièrement blanche),
    # `initial_white_count == rows * cols` donc cette proportion vaut 1 —
    # "le taux reste donc 1 pour la toute première grille" — et elle
    # diminue mécaniquement, palier après palier, à mesure que la grille
    # se noircit, sans qu'aucun code appelant n'ait besoin de le calculer
    # lui-même : `initial_white_count` reflétait déjà cette réalité, il
    # ne servait simplement pas encore à moduler le taux lui-même.
    white_proportion = initial_white_count / (rows * cols)
    black_enrichment_fraction = black_enrichment_fraction * white_proportion

    # « Nettoyage curatif » (voir _prefill_unfillable_slots) : réutilise ce
    # même objectif de remplissage en noir de la grille entière comme seuil
    # au-delà duquel une zone impossible bascule du simple ajout de cases
    # noires vers le retrait d'un mot déjà verrouillé qui la croise — à la
    # demande explicite de l'utilisateur, plutôt que d'inventer un nouveau
    # seuil séparé pour cette règle. `black_ratio` est presque toujours 0.0
    # aujourd'hui (voir plus haut), donc `black_enrichment_fraction` domine
    # en pratique ; les deux sont pris en compte ici par simple robustesse
    # pour un appelant (le CLI) qui fixerait encore `--black-ratio`.
    fill_objective_fraction = max(black_ratio, black_enrichment_fraction)

    if available_lengths is not None:
        candidates = _prefill_unfillable_slots(
            grid, rows, cols, row_black, col_black, candidates, available_lengths,
            index, locked_letters, rng, fill_objective_fraction,
            forbid_adjacency=(seed_grid is None),
        )

    placed = sum(row.count(BLACK) for row in grid)
    # `placed` already includes whatever pre-fill placed above — counting
    # it toward the target means this last term
    # (`black_enrichment_fraction * initial_white_count`, computed on the
    # white total *before* pre-fill, never on what's left after it) is a
    # third argument to `max`, on the same footing as `placed` and the
    # `black_ratio` floor — no longer added on top unconditionally, at the
    # user's explicit request: if pre-fill already placed more cells than
    # this percentage calls for, no further cell is added for this reason
    # (`placed` already wins the max); if it placed fewer, only the
    # difference is completed.
    target = max(
        placed,
        round(rows * cols * black_ratio),
        round(black_enrichment_fraction * initial_white_count),
    )
    _place_black_cells(grid, rows, cols, row_black, col_black, candidates, target, placed,
                        index=index, locked_letters=locked_letters, available_lengths=available_lengths,
                        forbid_adjacency=(seed_grid is None))

    if available_lengths is not None and locked_letters:
        _prefill_unfillable_slots(
            grid, rows, cols, row_black, col_black, candidates, available_lengths,
            index, locked_letters, rng, fill_objective_fraction,
            forbid_adjacency=(seed_grid is None),
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

def build_index(by_length, frequencies=None):
    """`frequencies` (un dict {MOT: fréquence}, `None` par défaut) alimente
    `index[length]["freq"]` — utilisé uniquement par `_noise_slot_cells`
    (voir `NOISE_FREQUENCY_THRESHOLD`) pour distinguer un candidat
    statistiquement crédible d'une entrée quasi nulle du dictionnaire.
    Omis (`None`), chaque mot de `index[length]["freq"]` retombe sur `0.0`
    — un no-op pour tout appelant qui n'utilise pas cette fonctionnalité
    (aucun caller réel autre que `generate_grid` aujourd'hui, mais un test
    isolé qui construit son propre petit lexique n'a pas besoin de fournir
    ce paramètre pour continuer à fonctionner comme avant)."""
    frequencies = frequencies or {}
    index = {}
    for length, words in by_length.items():
        pos_sets = [defaultdict(set) for _ in range(length)]
        for w in words:
            for p, ch in enumerate(w):
                pos_sets[p][ch].add(w)
        index[length] = {
            "words": words,
            "pos": pos_sets,
            "freq": {w: frequencies.get(w, 0.0) for w in words},
        }
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
#
# Fixed, for the time being, at 1.0 (100%) — i.e. every single attempt of
# the batch must finish before any interruption ever happens, which in
# practice means it never fires at all (the batch's own last attempt to
# finish has, by definition, nothing left to interrupt) — at the user's
# explicit later request: "Donner un nom de variable à la quantité de
# process qui échouent avant de décider d'interrompre tous les process
# (actuellement 30%). Fixer cette proportion pour le moment à 100% (on
# attend que tous les process terminent)." The variable already had a name
# from the original request above; only the value changed here. With
# `math.ceil(1.0 * len(futures)) == len(futures)`, `interrupt_threshold`
# below always equals the full batch size, so `attempt_done_event` is only
# ever set once every attempt has already completed on its own — a
# temporary, deliberately conservative setting the user may revisit later.
PALIER_ATTEMPT_INTERRUPT_FRACTION = 1.0

# Délai de grâce (secondes) laissé au drainage de `best_state_queue` (voir
# generate_grid) pour rattraper un message publié juste avant qu'un worker
# ne rende la main, à la demande explicite de l'utilisateur ("Chaque process
# suit son meilleur état, et transmet au process parent l'information que ce
# meilleur état a changé"). Nécessaire à cause d'une particularité bien
# connue de `multiprocessing.Queue`, confirmée en direct par un test isolé :
# `put()` ne bloque pas — il remet l'objet à un thread interne dédié à
# l'alimentation du tube sous-jacent, qui peut ne pas avoir fini son travail
# au moment exact où le processus appelant rend la main (`f.result()` dans
# le parent) ; un simple `get_nowait()` juste après peut donc légitimement
# renvoyer `Empty` alors qu'un message vient tout juste d'être publié
# (reproduit : un `try_fill` isolé qui publie un état, suivi immédiatement
# d'un drainage sans délai, ne récupérait rien — le même drainage après un
# `time.sleep(0.1)` récupérait le message publié). Une valeur courte (20ms)
# suffit très largement en pratique : ce délai n'est payé qu'une seule fois
# par palier, seulement une fois que le drainage "rapide" (get_nowait en
# boucle) a déjà tout consommé de ce qui était immédiatement disponible — et
# seulement si un message arrive réellement pendant ce court délai
# supplémentaire ; sinon, le palier suivant démarre sans attendre.
BEST_STATE_QUEUE_DRAIN_GRACE_S = 0.02

# Cadence (secondes) à laquelle `generate_grid` republie, tant qu'une
# recherche est en cours, le pourcentage du budget de vérifications
# (`deadline_checks`) déjà consommé par la tentative la plus avancée du
# palier courant — à la demande explicite de l'utilisateur : "sur la ligne
# de statut de l'interface, ajouter le pourcentage du budget déjà consommé
# par la phase de remplissage en cours." Réutilise `best_state_buffer`
# (déjà drainé en continu par `_drain_best_state_queue_continuously`, voir
# sa propre définition) plutôt qu'un nouveau canal dédié : chaque message
# qui y arrive porte déjà `checks` (voir `_publish_new_best`), donc le
# maximum de cette valeur parmi les messages du palier en cours est déjà
# une estimation raisonnable de « jusqu'où la recherche est allée » — une
# estimation, pas une mesure exacte à l'instant T, puisqu'elle ne progresse
# qu'aux instants où l'une des tentatives parallèles bat son propre record
# de mots placés (voir `Filler.on_new_best`), pas à chaque vérification
# individuelle ; un worker profondément coincé dans du retour en arrière
# sans jamais améliorer son record affiche donc un pourcentage figé sur son
# dernier record connu, plutôt qu'une progression continue — la valeur
# affichée est donc un plancher (« au moins X % déjà consommé »), jamais
# une survalorisation. 2 secondes, la même cadence que le sondage de
# l'interface (`POLL_INTERVAL_MS`, frontend/static/script.js) : assez
# fréquent pour rester "en direct" à l'œil, sans republier à chaque
# publication individuelle (jusqu'à ~50-60 par tentative, voir
# `_worker_best_state_queue`), qui spammerait `backend.log` pour un gain
# de fraîcheur imperceptible.
BUDGET_PROGRESS_REPORT_INTERVAL_S = 2.0

# Maximum number of consecutive "reprise telle quelle" paliers (see
# generate_grid's own `if still_has_hope:` branch) allowed before a full
# cleanup ("nettoyage complet") is triggered unconditionally, even if the
# current pattern still has real hope of progress. Named out of a previously
# unnamed inline literal (`consecutive_continue_paliers >= 5`), at the user's
# explicit request.
#
# Set to 0 — every single palier triggers a full cleanup, no "reprise telle
# quelle" streak at all. This was first tried, then briefly reverted to 5
# after appearing to cause a severe regression (a real generation stuck for
# 150+ consecutive paliers on a pattern already 100% filled with real,
# crossing-confirmed letters and 0% impossible) — but the user pushed back
# directly on that diagnosis: "La reprise telle quelle ou le nettoyage est
# un mécanisme qui intervient après la tentative de remplissage, qui doit
# aller jusqu'au bout... tous les emplacements restants doivent être testés
# avant de terminer un cycle... Si on a bien testé tous les emplacements
# restant, et que tous les mots en place sont valides, la grille est alors
# réputée réussie." The real bug was elsewhere: `Filler`/`try_fill` could
# end a search attempt (deadline exceeded, 30% abandon, interrupted by a
# sibling attempt, or genuinely exhausted) while a handful of slots were
# already fully and validly determined by real crossing letters — matching
# exactly one still-available dictionary word each — yet never explicitly
# confirmed by `_backtrack`, simply because its own selection order never
# reached them in time. Fixed at the actual root (`_close_implied_slots`,
# called from `try_fill` right after `filler.solve()` returns): any such
# trivially-implied slot is now confirmed as a final, cheap closing pass
# before a search attempt's outcome is decided — so 0 no longer needs
# "reprise telle quelle" to paper over this gap, restoring the 0 value the
# user actually asked for. See `_close_implied_slots`'s own docstring for
# the full mechanism and the live evidence that motivated it.
#
# Raised from 0 to 4, at the user's own later explicit request ("Régler
# MAX_CONSECUTIVE_CONTINUE_PALIERS à 4") — a plain value change, no
# reasoning given beyond the number itself; every mechanic this constant
# gates (the counter's own increment/reset points, the all-abandoned
# force-nettoyage rule, `_close_implied_slots`'s own fix above, which
# remains what makes even a small non-zero value here safe) is untouched.
#
# A real regression on the standard 15×10 benchmark's seed 7 was found and
# confirmed causal by a direct A/B (this exact constant, nothing else,
# flipped back to 0 and re-tested) before shipping this value: in **Flash**
# mode (`deadline_checks=1000`, the tightest of the 5 real `BUDGET_MODES`)
# seed 7 fails reproducibly at 4 (two runs, 17.2s/17.3s, all 200 paliers
# exhausted) but succeeds reliably at 0 (41.6s, 60 words) — seed 2
# succeeds either way. Reported to the user with this measurement; they
# chose to keep 4 regardless, the same trade-off already accepted for the
# `Filler._backtrack` checks-per-candidate change earlier this session:
# Flash's own tiny budget is the mode most exposed to this kind of
# reliability cost, not necessarily representative of the real default
# budget (300 000+) or a larger `BUDGET_MODES` choice.
MAX_CONSECUTIVE_CONTINUE_PALIERS = 4

# Nombre de nettoyages complets CONSÉCUTIFS pendant lesquels `generate_
# grid` peut produire exactement le même état (motif noir/blanc ET
# contenu confirmé, voir `_cycle_start_preview`) avant d'être déclaré
# infaisable et réinitialisé à une grille entièrement vierge au cycle
# suivant — à la demande explicite de l'utilisateur : "Mémoriser les
# grilles en fin de cycle. Quand une même grille est produite plus de 3
# cycles, déclarer cette grille infaisable, et supprime là au cycle
# suivant (elle devient la grille entièrement vierge du tour suivant)."
# Voir generate_grid, dans le `else:` (nettoyage complet) du `if still_
# has_hope: ... else: ...`, pour le mécanisme lui-même — délibérément
# limité à cette seule branche, jamais à "reprise telle quelle", après
# deux régressions mesurées en direct sur le benchmark standard 15×10
# (Flash) et deux allers-retours avec l'utilisateur (voir le commentaire
# du mécanisme lui-même pour le détail complet) : comparer le seul motif
# sur les deux branches confondait un motif stable (normal en "reprise
# telle quelle", où une case noire n'est ajoutée qu'une fois sur dix,
# voir BLACK_CELL_INSTEAD_OF_REMOVAL_PROBABILITY) avec un vrai blocage ;
# comparer motif+contenu sur les deux branches faisait toujours doublon
# avec MAX_CONSECUTIVE_CONTINUE_PALIERS, qui borne déjà "reprise telle
# quelle" avec une réponse plus douce (un nettoyage classique, pas une
# grille vierge). Restreint au nettoyage, ce garde-fou couvre un point
# fixe plus profond que celui déjà géré juste à côté (comparaison des
# seules lettres confirmées, une seule relance avec `exclude_impossible_
# locked=True`, voir `previous_locked_letters` plus bas) — qui ne
# résout pas toujours le blocage du premier coup. Nommée séparément de
# MAX_CONSECUTIVE_CONTINUE_PALIERS ci-dessus : les deux plafonds
# répondent à deux questions différentes (combien de cycles "reprise
# telle quelle" enchaîner sans nettoyage du tout, vs. combien de
# nettoyages consécutifs tolérer un état qui ne change plus malgré eux)
# et n'ont aucun lien entre elles.
GRID_REPEAT_INFEASIBLE_THRESHOLD = 3

# Number of PARALLEL_ATTEMPTS workers that, right after a full cleanup
# ("nettoyage complet" — see generate_grid's own `else:` branch, as opposed
# to "reprise telle quelle"), start the very next palier from a completely
# blank grid instead of the just-cleaned seed_grid/locked_letters every
# other worker of that palier gets — at the user's explicit request: "A
# chaque nettoyage complet (tous les 5 cycles) redémarrer 20% des process
# avec une grille réinitialisée totalement." All PARALLEL_ATTEMPTS workers
# normally start from the exact same carried-forward state after a cleanup
# (only their own random seed differs), which can make every one of them
# converge on the same kind of dead end again and again — deliberately
# sacrificing a small share of the batch to a genuinely fresh start gives
# the search a chance to escape that instead. See generate_grid's own
# `just_cleaned` flag.
#
# Originally a fraction (`FULL_RESET_ATTEMPT_FRACTION = 0.20`, resolving to
# `round(0.20 * PARALLEL_ATTEMPTS)` workers — 2 on a 10-core machine).
# Reduced to a fixed count of 1, at the user's explicit later request:
# "Réduire le nombre de process qui calculent une grille totalement
# nouvelle à 1 seul (au lieu de 20%)." A single from-scratch worker is
# already enough to give the search a genuinely fresh escape route from a
# repeated dead end, at a smaller cost to the batch's own carried-forward
# progress than sacrificing 2+ workers to it every single cleanup.
FULL_RESET_ATTEMPT_COUNT = 1


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


# Fenêtre de tirage au hasard parmi les meilleurs mots candidats d'un
# emplacement, une fois triés par `_candidate_score` (voir `Filler.
# _backtrack`) — un peu comme la fenêtre de 32 cases de `_place_black_
# cells` pour les cases noires : garde la priorité globale aux mots les
# mieux notés statistiquement tout en évitant de les tester très
# exactement dans l'ordre du tri, qui reviendrait à un choix entièrement
# déterministe (à seedage égal) plutôt qu'à une vraie exploration.
# Augmentée de 20 à 200, à la demande explicite de l'utilisateur : "évite
# les mots trop rares, tout en laissant plus de latitude à l'exploration
# de solutions variées" — une fenêtre plus large touche encore, en
# pratique, essentiellement des mots bien classés statistiquement (jamais
# les tout derniers du dictionnaire), mais parmi un choix nettement plus
# large qu'auparavant, pour plus de diversité d'une tentative à l'autre.
#
# Relevée de 200 à 5000, à la demande explicite de l'utilisateur : "Les
# 200 meilleurs obligent à commencer les emplacements vierges avec un
# vocabulaire très restreint. Relâcher la contrainte... (ça aura sans
# doute l'effet d'annuler l'intérêt du scoring, mais je voudrais voir ce
# que ça donne)" — l'utilisateur anticipe lui-même qu'une fenêtre aussi
# large, sur un emplacement encore entièrement vierge (aucune case fixée
# par un croisement, donc `letter_scores` sans le moindre effet
# discriminant sur le tri — voir `_candidate_score`), revient en pratique
# à un tirage quasiment uniforme parmi tout le dictionnaire de cette
# longueur, plutôt qu'à une vraie priorité aux mots les mieux notés.
#
# Relevée à nouveau de 5000 à 20000, à la demande explicite de
# l'utilisateur, fixée "pour le moment" à "tout ce qui est dispo pour les
# 8 lettres" — le dictionnaire FR compte 19 066 mots de 8 lettres (compté
# en direct sur data/wordlist_fr_full.tsv), donc 20000 couvre déjà la
# totalité du dictionnaire pour n'importe quelle longueur de slot jusqu'à
# 8 lettres inclus (et la quasi-totalité au-delà — seules quelques
# longueurs bien plus rares/longues en comptent davantage) : à cette
# valeur, la fenêtre n'exclut plus aucun mot pour la grande majorité des
# emplacements réels, réduisant d'autant plus le tirage à un choix
# quasiment uniforme sur tout le dictionnaire de la longueur concernée
# (même conséquence que celle déjà anticipée ci-dessus lors du passage à
# 5000, mais plus marquée encore).
CANDIDATE_SCORE_WINDOW = 20000

# Proportion du groupe d'emplacements retenu (`selection_pool`, voir
# `Filler._backtrack`, "Choisir quel emplacement remplir en premier") qui
# détermine la taille de la fenêtre de tirage final — `window_size =
# max(5, int(len(selection_pool) * SLOT_SELECTION_WINDOW_FRACTION))`, un
# plancher de 5 dans tous les cas. Nommée et abaissée de 1/4 à 1/10, à la
# demande explicite de l'utilisateur — une fenêtre plus étroite resserre
# le tirage final sur une plus petite fraction des emplacements les mieux
# classés (les plus proches du coin en haut à gauche, voir le score
# géométrique juste au-dessus), au lieu d'un quart du groupe retenu.
SLOT_SELECTION_WINDOW_FRACTION = 1 / 10

# Une fois la fenêtre ci-dessus obtenue (`window`, triée par score
# géométrique croissant), `Filler._backtrack` la retrie une seconde fois
# par nombre de lettres déjà posées dans chaque emplacement (le plus de
# lettres en premier — `_placed_letter_count`, la même distinction
# fait-acquis/simple-supposition que `_has_known_letter`), puis la
# réduit à nouveau à ses `SLOT_SELECTION_REFINE_FRACTION` premiers
# emplacements (les mieux pourvus en lettres déjà connues) avant le
# tirage final — à la demande explicite de l'utilisateur, qui a aussi
# relevé cette proportion de 1/4 à 1/2 dans le même mouvement (une
# réduction plus douce, gardant la moitié plutôt que le quart de
# `window`). Plancher **toujours à 1 emplacement, jamais 0** (jamais 5
# non plus, comme la fenêtre précédente) : `window` elle-même peut être
# aussi petite que son propre plancher de 5, et un plancher plus élevé
# ici annulerait la réduction demandée dans ce cas très courant (la
# moitié de 5 vaut 2, mais un tiers ou un quart de 5 vaudrait déjà 1,
# sous un plancher de 5 qui forcerait alors la fenêtre entière à être
# reprise telle quelle) — cette fenêtre réduite (`refined_window`) ne
# peut donc jamais finir vide, quelle que soit la taille de `window` ou
# la valeur de cette fraction.
# Nommée séparément de `SLOT_SELECTION_WINDOW_FRACTION` ci-dessus : les
# deux fractions s'appliquent à deux fenêtres différentes, l'une après
# l'autre (la seconde opère sur `window`, pas sur `selection_pool`), pas
# à la même grandeur — leur valeur numérique n'a aucun lien entre elles.
SLOT_SELECTION_REFINE_FRACTION = 1 / 2


class Filler:
    def __init__(self, slots, index, rng, forced_letters=None, letter_scores=None,
                 excluded_slots=None, cancel_event=None, batch_abandoned_event=None,
                 attempt_done_event=None, on_new_best=None, locked_letters=None):
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
        # case -> lettre réellement verrouillée par un palier précédent (le
        # `locked_letters` de `_pattern_attempt`/`_pattern_continue`), à la
        # demande explicite de l'utilisateur — gardée ici *séparément* de
        # `self.forced_letters`, contrairement à avant, où l'appelant la
        # fusionnait directement dans `forced_letters` avant même de
        # construire ce `Filler` (`{**forced_letters, **locked_letters}`).
        # Cette fusion perdait une distinction réelle : `_domain(i,
        # ignore_forced=True)` (utilisé uniquement par `impossible_zone_
        # slots`, voir plus bas) ignore intentionnellement tout
        # `self.forced_letters` — correct pour une simple graine
        # statistique jamais vérifiée, mais `locked_letters` n'en est pas
        # une : c'est du contenu réellement confirmé, porté d'un palier au
        # suivant. Bug réel constaté en direct : un emplacement entièrement
        # verrouillé par `locked_letters`, dont la combinaison ne
        # correspond à aucun mot réel (donc exclu de la recherche, jamais
        # assigné), n'était presque jamais signalé « impossible » une fois
        # cette fusion ignorée par `ignore_forced=True` — 330 instances sur
        # 349 mesurées en direct sur une même graine de test. Résultat :
        # `_clean_blocked_slots`/`_build_retry_seed` (le nettoyage entre
        # paliers) ne voyait jamais cet emplacement comme un problème à
        # corriger, donc ne retirait jamais le mot croisant responsable —
        # la même combinaison invalide se reconstruisait alors à
        # l'identique, cycle après cycle, parfois pendant plus de 70 cycles
        # consécutifs sur une seule et même case, sans jamais progresser ni
        # jamais être signalée. Séparer les deux dicts et vérifier
        # `self.locked_letters` sans condition (voir _domain ci-dessous,
        # jamais ignoré même avec `ignore_forced=True`) corrige ça à la
        # racine.
        self.locked_letters = locked_letters or {}
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
        # Pour chaque emplacement, l'ensemble des AUTRES emplacements qui
        # partagent au moins une case avec lui (précalculé une fois ici, à
        # partir de cell_to_slots juste au-dessus — la géométrie des
        # emplacements ne change jamais une fois `slots` extrait). Sert à
        # `_backtrack`, à la demande explicite de l'utilisateur, pour
        # évaluer tout de suite, juste après avoir posé un mot, seulement
        # les emplacements que ce mot croise réellement — plutôt que
        # d'attendre l'appel récursif suivant, qui recalcule le domaine de
        # TOUS les emplacements encore libres de la grille, y compris ceux
        # que ce mot ne pouvait de toute façon pas affecter.
        self._crossing_slots = [
            {j for cell in cells for j, _ in self.cell_to_slots[cell] if j != i}
            for i, cells in enumerate(slots)
        ]
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
        # Rappelé (voir _backtrack) chaque fois que best_assignment vient
        # d'être amélioré, avec ce nouvel état en argument — permet à
        # try_fill de publier ce nouvel état vers le processus parent en
        # temps réel plutôt qu'une seule fois à la toute fin de la
        # recherche, à la demande explicite de l'utilisateur (voir
        # `_worker_best_state_queue`, plus bas dans ce fichier, pour
        # l'historique complet). `None` par défaut — aucun effet pour tout
        # appelant existant.
        self.on_new_best = on_new_best

    def _domain(self, i, ignore_forced=False):
        """Ensemble/liste des mots compatibles avec les lettres déjà connues de
        la case i (sans encore exclure les mots utilisés ailleurs — voir _pick).
        Une lettre "conseillée" par self.forced_letters (voir __init__) compte
        comme une contrainte au même titre qu'une lettre vraiment imposée par
        un emplacement croisé déjà assigné — mais seulement tant qu'aucun
        emplacement croisé n'est réellement assigné à cette case : une vraie
        affectation l'emporte toujours sur un simple indice statistique.

        `ignore_forced` (`False` par défaut — comportement inchangé pour tout
        appelant existant), à la demande explicite de l'utilisateur : ignore
        entièrement `self.forced_letters` (la simple graine statistique),
        ne retenant que les lettres réellement imposées par un emplacement
        croisé déjà assigné — voir `impossible_zone_slots` (seul appelant à
        le passer à `True`), qui a besoin d'une notion d'« impossible »
        fondée uniquement sur des faits confirmés, jamais sur une simple
        graine statistique non vérifiée. `self.locked_letters` (du contenu
        réellement confirmé, porté d'un palier au suivant — voir __init__)
        n'est en revanche JAMAIS ignoré, même avec `ignore_forced=True` :
        ce n'est pas une supposition, donc `impossible_zone_slots` doit
        pouvoir s'en servir tout autant que d'une vraie affectation
        croisée."""
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
                letter = self.locked_letters.get(cell)
            if letter is None and not ignore_forced:
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
        """Nombre de cases de l'emplacement i déjà déterminées par une
        vraie lettre — un mot croisé déjà assigné pendant cette même
        tentative, ou une lettre verrouillée d'un palier précédent
        (self.locked_letters). Même distinction fait-acquis/simple-
        supposition que _has_known_letter (self.forced_letters, une
        simple graine statistique, ne compte jamais ici) — voir sa propre
        docstring. Utilisée par _backtrack pour retrier la fenêtre de
        sélection du niveau 4 (le plus de lettres déjà posées en
        premier), à la demande explicite de l'utilisateur."""
        count = 0
        for cell in self.slots[i]:
            if cell in self.locked_letters:
                count += 1
                continue
            for j, _ in self.cell_to_slots[cell]:
                if j != i and self.assignment[j] is not None:
                    count += 1
                    break
        return count

    def _has_known_letter(self, i):
        """True si l'emplacement i a déjà au moins une case déterminée par
        une vraie lettre — un mot croisé déjà assigné pendant cette même
        tentative, ou une lettre verrouillée d'un palier précédent
        (self.locked_letters). Une simple graine statistique
        (self.forced_letters) ne compte jamais ici, comme partout ailleurs
        dans ce fichier (voir _domain/impossible_zone_slots) — ce n'est
        qu'une supposition non vérifiée, pas un fait acquis. Utilisée par
        _backtrack pour prioriser les emplacements déjà partiellement
        connus plutôt qu'un emplacement entièrement vierge."""
        for cell in self.slots[i]:
            if cell in self.locked_letters:
                return True
            for j, _ in self.cell_to_slots[cell]:
                if j != i and self.assignment[j] is not None:
                    return True
        return False

    def _slot_letter_frequency_score(self, i):
        """Somme des carrés des fréquences mesurées (self.letter_scores —
        la même statistique que sample_letter_biases calcule pour choisir
        les graines/forced_letters, voir _candidate_score plus bas) de la
        lettre la plus fréquente à chaque case ENCORE LIBRE de l'emplacement
        i — une case déjà déterminée par une vraie lettre (un mot croisé
        déjà assigné pendant cette même tentative, ou self.locked_letters)
        n'offre plus aucune option de remplissage, donc n'est pas comptée
        ici, même exclusion que _placed_letter_count/_has_known_letter.

        Utilisée par _backtrack comme dernier critère de départage du
        niveau 4 (voir sa propre docstring), à la demande explicite de
        l'utilisateur : favorise l'emplacement dont la zone offre
        statistiquement le plus d'options de remplissage — c'est-à-dire,
        pour chaque case encore libre, plusieurs mots réels différents s'y
        accordant plutôt qu'une lettre isolée dominant le reste — et donc,
        pour les emplacements voisins qui croisent ces mêmes cases, le plus
        de lettres crédibles avec lesquelles composer à leur tour. Mettre
        les fréquences au carré favorise un emplacement dont plusieurs
        cases encore libres ont toutes un consensus statistique marqué
        plutôt qu'un emplacement qui ne doit un score élevé qu'à une seule
        case exceptionnelle — même raisonnement déjà appliqué ailleurs dans
        ce fichier (_candidate_score, la somme des carrés des longueurs de
        mots dans generate_grid)."""
        total = 0
        for cell in self.slots[i]:
            if cell in self.locked_letters:
                continue
            fixed = False
            for j, _ in self.cell_to_slots[cell]:
                if j != i and self.assignment[j] is not None:
                    fixed = True
                    break
            if fixed:
                continue
            counts = self.letter_scores.get(cell)
            if counts:
                total += max(counts.values()) ** 2
        return total

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
        avancé (`best_assignment`) que ce diagnostic examine.

        `_domain(i, ignore_forced=True)` — jamais la version par défaut, qui
        laisserait une simple graine statistique (`forced_letters`, un
        indice non vérifié, voir `sample_letter_biases`) compter comme une
        contrainte dure. Bug réel constaté en direct : un emplacement pouvait
        être déclaré « impossible » (surligné en rouge dans l'aperçu, exclu
        du remplissage, ciblé par le nettoyage entre paliers) alors
        qu'aucune de ses lettres n'était en réalité imposée par un mot
        croisé confirmé — juste une supposition statistique jamais
        confirmée ni infirmée, qui ne redevient plus jamais pertinente une
        fois la recherche arrêtée sur cet état. Conséquence directe : le
        nettoyage (`_clean_blocked_slots`) recalculait alors, lui, un vrai
        candidat pour ce même emplacement (puisqu'il ne regarde jamais
        `forced_letters`) et ne faisait donc littéralement rien — ni retrait
        de mot, ni case noire via la règle des 1/10 — laissant l'emplacement
        marqué « impossible » indéfiniment, cycle après cycle, sans qu'aucun
        mécanisme de nettoyage ne puisse jamais agir dessus. Confirmé en
        direct sur une grille réelle : 41 % des emplacements déclarés
        impossibles avaient en fait, une fois les graines statistiques
        ignorées, au moins un candidat réel — un désaccord aussi fréquent
        entre ce diagnostic et le nettoyage qui doit s'en servir ne pouvait
        pas être un simple cas limite rare."""
        saved = self.assignment
        self.assignment = self.best_assignment
        used_at_best = {w for w in self.best_assignment if w is not None}
        result = [
            i for i, word in enumerate(self.best_assignment)
            if word is None
            and all(w in used_at_best for w in self._domain(i, ignore_forced=True))
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
        # `self.checks` n'est plus incrémenté ici (une fois par appel/nœud)
        # mais une fois par mot candidat réellement essayé, dans la boucle
        # `for w in cands:` plus bas — voir son propre commentaire pour la
        # raison (à la demande explicite de l'utilisateur, "pour éviter
        # d'itérer longtemps sur des cas impossibles"). Ce premier appel
        # (depuis `Filler.solve()`) démarre donc avec `self.checks` encore à
        # sa valeur d'entrée (0 pour une recherche neuve) ; les contrôles
        # ci-dessous restent corrects avec cette valeur telle quelle.
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
            if self.on_new_best is not None:
                self.on_new_best(self.best_assignment)
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

        # Règle de sélection à 4 niveaux, à la demande explicite de
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
        # 2. **Nouveau, à la demande explicite de l'utilisateur, prioritaire
        #    sur le critère de domaine ci-dessous** : parmi les emplacements
        #    de la catégorie tirée, s'il en existe au moins un dont le
        #    domaine (`domains[i]`, déjà calculé juste au-dessus) compte
        #    strictement moins de `PREFILL_MIN_WORD_COUNT` mots candidats —
        #    le même seuil que le pré-remplissage de l'étape 1 utilise pour
        #    décider qu'un emplacement a besoin d'une case noire — le choix
        #    se restreint à ces emplacements-là uniquement. But : essayer de
        #    résoudre ces emplacements fragiles par un vrai mot pendant que
        #    la recherche progresse encore, avant qu'un futur palier de
        #    nettoyage ne les juge insuffisants et n'y ajoute une case
        #    noire pour les corriger (voir `_prefill_unfillable_slots`,
        #    étape 1) — un mot réellement posé ici évite cette case noire.
        #    D'abord expérimenté avec un critère différent ("une seule case
        #    encore vide"), remplacé par celui-ci à la demande explicite de
        #    l'utilisateur, qui vise directement le même seuil que le
        #    pré-remplissage plutôt qu'un proxy géométrique. Si aucun
        #    emplacement de la catégorie n'est dans ce cas, ce niveau ne
        #    change rien : le niveau 3 s'applique alors à l'ensemble de la
        #    catégorie, exactement comme avant l'ajout de ce niveau ;
        # 3. **Nouveau, à la demande explicite de l'utilisateur** : parmi
        #    les emplacements du groupe obtenu au niveau précédent, s'il en
        #    existe au moins un qui a déjà au moins une case déterminée par
        #    une vraie lettre (`_has_known_letter` — un mot croisé déjà
        #    assigné, ou une lettre verrouillée d'un palier précédent —
        #    jamais une simple graine statistique), le choix se restreint à
        #    ces emplacements-là uniquement, excluant les emplacements
        #    entièrement vierges tant qu'il en reste au moins un déjà
        #    partiellement connu — finir un emplacement déjà entamé plutôt
        #    que d'en ouvrir un nouveau. Si tous les emplacements du groupe
        #    sont entièrement vierges, ce niveau ne change rien : le niveau
        #    4 s'applique alors à l'ensemble du groupe, exactement comme
        #    avant l'ajout de ce niveau ;
        # 4. les niveaux 2/3/4 précédents (le moins de cases encore
        #    blanches en priorité, le plus de lettres déjà fixées en
        #    départage, un tirage pondéré par la longueur en dernier
        #    recours) ont été remplacés par une règle unique : parmi les
        #    emplacements du groupe obtenu au niveau précédent, on calcule
        #    pour chacun un score, et on tire au hasard, uniformément,
        #    **parmi les emplacements ayant obtenu le plus petit score**,
        #    **dans une fenêtre de max(5, int(taille_du_groupe *
        #    SLOT_SELECTION_WINDOW_FRACTION))** (1/10, voir sa propre
        #    docstring)
        #    emplacements — une fenêtre qui s'élargit quand ce groupe compte
        #    encore beaucoup d'emplacements, et se resserre (jusqu'à ce
        #    plancher de 5) une fois qu'il n'en reste plus beaucoup, plutôt
        #    qu'une taille fixe ou liée à la seule taille de la grille. Ce
        #    critère a déjà changé plusieurs fois : "le moins de cases
        #    encore blanches", puis "le plus de lettres déjà remplies" en
        #    compte brut sur une fenêtre de 30, puis remplies/longueur sur
        #    une fenêtre de 15, puis l'égalité stricte sans fenêtre, puis
        #    une fenêtre fixe de 10, puis une fenêtre en int(sqrt(largeur ×
        #    hauteur)), puis cette fenêtre proportionnelle au nombre
        #    d'emplacements encore libres (÷10, puis ÷2, puis ÷3), puis
        #    int(100 * remplies / sqrt(longueur)), puis le simple compte
        #    brut de lettres déjà remplies (sans normalisation par la
        #    longueur du tout), puis ce critère inversé (le plus de cases
        #    *encore blanches*, plutôt que le plus de lettres déjà
        #    remplies), puis le domaine lui-même (le nombre réel de mots
        #    candidats, `len(domains[i])`, déjà calculé plus haut pour la
        #    détection d'impasse), trié par ordre croissant (le plus petit
        #    score, donc l'emplacement le plus contraint, en premier) — et
        #    enfin, à la demande explicite de l'utilisateur, ce score
        #    purement **géométrique** : `x + y`, où `(y, x)` est la
        #    première case de l'emplacement (`self.slots[i][0]`, toujours
        #    la case la plus en haut/à gauche parmi les siennes — voir
        #    `extract_slots`), mesurées par rapport au coin en haut à
        #    gauche de la grille (la même origine que `(row, col)` partout
        #    ailleurs dans ce fichier) — voir le calcul lui-même plus bas
        #    pour le détail, y compris une première version mesurée par
        #    rapport au coin en haut à *droite*, corrigée à la demande de
        #    l'utilisateur après un diagnostic en direct. Contrairement à
        #    tous les critères précédents de ce niveau, celui-ci ne dépend
        #    plus du tout de l'état de remplissage de l'emplacement (ni ses
        #    lettres connues, ni son domaine) — seulement de sa position
        #    fixe dans la grille — ce qui tend à faire progresser le
        #    remplissage selon un front géométrique plutôt que selon la
        #    difficulté de chaque emplacement.
        #    Fenêtre resserrée de ÷3 à ÷4 dans le même mouvement, toujours
        #    à la demande explicite de l'utilisateur — puis nommée
        #    (`SLOT_SELECTION_WINDOW_FRACTION`) et resserrée une fois de
        #    plus, de 1/4 à 1/10, à la demande explicite de l'utilisateur.
        #    Les emplacements sont
        #    mélangés (avec le RNG seedé de cette tentative, donc
        #    reproductible) avant d'être triés par score : sans ce mélange
        #    préalable, l'ordre de tri (`sorted` est stable) déciderait
        #    quels emplacements à égalité passent la coupure de la fenêtre,
        #    réintroduisant le même biais positionnel déjà rencontré
        #    ailleurs dans ce fichier (voir plus haut, les bugs "colonne
        #    noire"/"triangle" du pré-remplissage) — d'autant plus pertinent
        #    maintenant que le score lui-même est géométrique, donc que de
        #    nombreux emplacements peuvent partager exactement le même
        #    score (toute la diagonale à une distance donnée du coin).
        #    Cette fenêtre géométrique (`window`) est ensuite retriée deux
        #    fois de plus, chaque fois en la réduisant encore, avant que le
        #    choix final ne se fasse :
        # 5. par nombre de lettres déjà posées dans chaque emplacement
        #    (`_placed_letter_count`, le plus de lettres en premier),
        #    réduite à ses `SLOT_SELECTION_REFINE_FRACTION` premiers
        #    emplacements (voir la docstring de cette constante) ;
        # 6. par `_slot_letter_frequency_score` (voir sa propre docstring),
        #    le score le plus haut en premier — l'emplacement dont la zone
        #    propose statistiquement le plus d'options de remplissage —
        #    dont le premier devient directement l'emplacement choisi.
        #    Chacune de ces deux réductions remélange sa propre fenêtre
        #    d'entrée au préalable (même raison que le mélange du niveau 4 :
        #    `sorted` étant stable, ce mélange est ce qui départage les
        #    emplacements à égalité de score, pas l'ordre hérité du tri
        #    précédent).
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
        few_candidates = [i for i in direction_pool if len(domains[i]) < PREFILL_MIN_WORD_COUNT]
        selection_pool = few_candidates if few_candidates else direction_pool
        # Nouveau niveau, à la demande explicite de l'utilisateur : parmi
        # le groupe obtenu au niveau précédent, s'il en existe au moins un
        # qui a déjà au moins une case déterminée par une vraie lettre
        # (`_has_known_letter` — un mot croisé déjà assigné, ou une lettre
        # verrouillée d'un palier précédent), le choix se restreint à ces
        # emplacements-là uniquement, excluant les emplacements
        # entièrement vierges tant qu'il en reste au moins un déjà
        # partiellement connu. Si tous les emplacements du groupe sont
        # entièrement vierges, ce niveau ne change rien.
        non_blank = [i for i in selection_pool if self._has_known_letter(i)]
        if non_blank:
            selection_pool = non_blank
        # Score géométrique, à la demande explicite de l'utilisateur : x + y,
        # où (y, x) est la première case de l'emplacement (self.slots[i][0],
        # toujours la case la plus en haut/à gauche parmi les siennes — voir
        # extract_slots), x/y mesurées par rapport au coin en haut à
        # gauche de la grille — la même origine que `(row, col)` partout
        # ailleurs dans ce fichier, donc x = colonne, y = ligne directement.
        # Un emplacement dont la première case est déjà au coin en haut à
        # gauche obtient le score le plus bas possible (0) ; le score
        # augmente à mesure qu'un emplacement démarre plus bas et/ou plus à
        # droite. Une première version mesurait ce même score par rapport
        # au coin en haut à DROITE (x = distance au bord droit plutôt qu'au
        # bord gauche), conformément à la toute première formulation de la
        # demande — un diagnostic en direct (fenêtre de sélection capturée
        # sur une vraie recherche) a confirmé que ce calcul produisait
        # exactement ce que cette formule impliquait mathématiquement
        # (favoriser les emplacements proches du coin en haut à droite),
        # sans bug de calcul ; mais l'utilisateur a signalé que le
        # remplissage semblait démarrer du mauvais coin, et a choisi,
        # via une question directe, de repasser au coin en haut à gauche
        # plutôt que de garder ce comportement — cette version n'a donc
        # plus besoin de connaître la largeur de la grille du tout (`cols`
        # a été retiré de `Filler.__init__`, qui ne l'utilisait que pour
        # ce calcul).
        scores = {
            i: self.slots[i][0][1] + self.slots[i][0][0]
            for i in selection_pool
        }
        shuffled_pool = list(selection_pool)
        self.rng.shuffle(shuffled_pool)
        window_size = max(5, int(len(selection_pool) * SLOT_SELECTION_WINDOW_FRACTION))
        window = sorted(shuffled_pool, key=lambda i: scores[i])[:window_size]
        # Nouveau, à la demande explicite de l'utilisateur : retrier cette
        # fenêtre par nombre de lettres déjà posées dans chaque
        # emplacement (le plus de lettres en premier), puis la réduire à
        # nouveau à ses SLOT_SELECTION_REFINE_FRACTION premiers
        # emplacements — voir la docstring de cette constante. Remélangée
        # d'abord (avec le RNG seedé de cette tentative) pour la même
        # raison que le mélange précédent : `sorted` est stable, donc sans
        # ce second mélange l'ordre issu du premier tri (par score
        # géométrique) déciderait quels emplacements à égalité de lettres
        # déjà posées passent la coupure de cette seconde fenêtre.
        shuffled_window = list(window)
        self.rng.shuffle(shuffled_window)
        placed_counts = {i: self._placed_letter_count(i) for i in window}
        refined_window_size = max(1, int(len(window) * SLOT_SELECTION_REFINE_FRACTION))
        refined_window = sorted(shuffled_window, key=lambda i: -placed_counts[i])[:refined_window_size]
        # Nouveau, à la demande explicite de l'utilisateur : classer les
        # emplacements de cette fenêtre réduite par _slot_letter_frequency_
        # score (voir sa propre docstring), le score le plus haut en
        # premier — donc l'emplacement dont la zone propose statistiquement
        # le plus d'options de remplissage, y compris pour les emplacements
        # voisins qui croisent ses cases encore libres. Remélangée d'abord
        # (avec le RNG seedé de cette tentative), pour la même raison que
        # les deux mélanges précédents : `sorted` est stable, donc sans ce
        # troisième mélange l'ordre issu des deux tris précédents
        # déciderait, à égalité de score, quel emplacement l'emporte.
        shuffled_refined = list(refined_window)
        self.rng.shuffle(shuffled_refined)
        freq_scores = {i: self._slot_letter_frequency_score(i) for i in refined_window}
        best_i = sorted(shuffled_refined, key=lambda i: -freq_scores[i])[0]

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
            # pioche au hasard parmi les `CANDIDATE_SCORE_WINDOW` meilleurs
            # mots *encore restants* du tri (pas les `CANDIDATE_SCORE_
            # WINDOW` premiers du tri d'origine, figés une fois pour
            # toutes — la fenêtre glisse au fur et à mesure que des mots en
            # sont retirés) — voir la docstring de la constante elle-même
            # pour le détail de ce qu'elle équilibre.
            window = CANDIDATE_SCORE_WINDOW
            reordered = []
            remaining = cands
            while remaining:
                take = min(window, len(remaining))
                idx = self.rng.randrange(take)
                reordered.append(remaining.pop(idx))
            cands = reordered
        for w in cands:
            # Compter cette tentative de pose immédiatement, qu'elle mène ou
            # non à une descente récursive plus loin — à la demande
            # explicite de l'utilisateur ("modifie de manière à incrémenter
            # le décompte du budget à chaque fois qu'on essaye de poser un
            # mot, que ça génère une descente récursive ou pas"), pour
            # éviter d'itérer longtemps sur des cas impossibles. Avant ce
            # changement, `self.checks` n'était incrémenté qu'au tout début
            # de `_backtrack`, donc seulement quand la récursion descendait
            # réellement plus loin (voir le commentaire juste au-dessus du
            # contrôle de croisement, plus bas) : un emplacement dont
            # presque tous les candidats cassent un croisement (voir ce même
            # contrôle) ne recule jamais dans `_backtrack`, donc ce compteur
            # ne bougeait pas du tout pendant que cette boucle parcourait
            # potentiellement des centaines de candidats rejetés un par un —
            # ni le budget (`deadline_checks`) ni `self.abandoned` n'étaient
            # jamais reconsultés tant que la boucle continuait, puisque ces
            # deux contrôles ne sont autrement évalués qu'à l'entrée de
            # `_backtrack`. Compter — et vérifier — dès cette tentative,
            # avant même de poser le mot, borne enfin ce cas : la boucle
            # s'arrête au plus tard `deadline_checks` tentatives après son
            # dernier passage par le haut de la fonction, plutôt que de
            # pouvoir continuer indéfiniment sur un emplacement condamné.
            # `self.abandoned` est revérifié ici pour la même raison : il
            # peut avoir été mis à `True` par une tentative sœur déjà
            # explorée plus tôt dans cette même boucle (un candidat qui a
            # récursé, plus profondément déclaré la recherche sans espoir,
            # puis rendu la main) — sans ce contrôle, les candidats suivants
            # continueraient d'être essayés (et leur propre contrôle de
            # croisement calculé, un vrai coût) avant que le prochain appel
            # récursif ne le remarque enfin via son propre `if self.
            # abandoned: return False`.
            self.checks += 1
            if self.abandoned or self.checks > deadline_checks:
                return False
            self.assignment[best_i] = w
            self.used_words.add(w)
            # Évaluer tout de suite, avant de descendre plus loin dans la
            # récursion, si le mot qu'on vient de poser rend l'un des
            # emplacements qui le CROISENT (self._crossing_slots, précalculé
            # dans __init__) impossible à remplir — même critère
            # d'impossibilité que le contrôle de domaine plus haut (domaine
            # vide, ou entièrement déjà utilisé ailleurs), mais restreint
            # aux seuls emplacements que ce mot peut réellement avoir
            # affectés, à la demande explicite de l'utilisateur. Poser un
            # mot ne peut jamais changer le domaine d'un emplacement qui ne
            # partage aucune case avec lui (`_domain` ne lit que les cases
            # de l'emplacement lui-même) — vérifier seulement les voisins
            # donne donc exactement le même résultat que le contrôle de
            # domaine "tous les emplacements encore libres" du prochain
            # appel récursif, sans avoir à le déclencher (ni son propre
            # compteur `checks`, ni son propre balayage de toute la grille)
            # pour un mot déjà condamné : si un seul des voisins est
            # devenu impossible, ce mot est retiré immédiatement et le
            # suivant est essayé, sans jamais descendre plus loin. Un
            # emplacement déjà mis de côté (`excluded_slots`/
            # `_crossing_excluded_slots`) n'est jamais concerné — il ne
            # bloque déjà jamais rien pour ce même motif.
            crossing_broken = False
            for j in self._crossing_slots[best_i]:
                if (
                    self.assignment[j] is None
                    and j not in self.excluded_slots
                    and j not in self._crossing_excluded_slots
                ):
                    domain = self._domain(j)
                    if all(w2 in self.used_words for w2 in domain):
                        crossing_broken = True
                        break
            if not crossing_broken and self._backtrack(deadline_checks):
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


def _force_single_candidate_slots(slots, index, known_letters, excluded_slots=None):
    """À la demande explicite de l'utilisateur : "Avant de calculer les
    statistiques pour placer les graines, ajouter un traitement : quand un
    emplacement valide ne possède plus qu'une seule possibilité de mot,
    forcer les lettres restantes pour placer ce mot." Contrairement au
    sondage statistique de `sample_letter_biases` (un simple consensus sur
    100 mots tirés au hasard, jamais une certitude), un emplacement dont les
    lettres déjà connues (`known_letters`) ne laissent plus qu'un seul mot
    du dictionnaire possible n'est plus une question de probabilité : c'est
    ce mot-là, ou aucun. Force alors directement les lettres pas encore
    connues de cet emplacement dans le dict renvoyé — au même titre qu'une
    lettre déjà verrouillée par un palier précédent, pas comme un simple
    indice statistique.

    Répété jusqu'à ce qu'un passage complet sur tous les emplacements ne
    change plus rien : forcer les lettres d'un emplacement peut, via une
    case de croisement, faire elle aussi passer un emplacement voisin pas
    encore résolu à une seule possibilité — un seul passage pourrait rater
    ce genre de réaction en chaîne selon l'ordre de balayage.

    Un emplacement de `excluded_slots` (déjà connu impossible — voir
    `Filler.excluded_slots`) n'est jamais testé : il ne sera de toute façon
    jamais tenté par la recherche, inutile d'y chercher une déduction. Un
    emplacement déjà entièrement connu (chaque case déjà dans
    `known_letters`) n'a lui non plus plus rien à déduire — il ne reste
    plus qu'à vérifier, ailleurs (voir `_pattern_attempt`'s propre
    `preseed_assignment`), que le mot qu'il épelle est bien réel.

    Ne modifie jamais `known_letters` sur place : renvoie un nouveau dict,
    copié une seule fois au tout début, laissant l'appelant décider quoi
    faire de l'original (par exemple le comparer à la version augmentée
    pour savoir si quelque chose a changé)."""
    excluded = excluded_slots or set()
    known = dict(known_letters or {})
    changed = True
    while changed:
        changed = False
        for slot_idx, cells in enumerate(slots):
            if slot_idx in excluded:
                continue
            if all(cell in known for cell in cells):
                continue
            candidates = _slot_candidates(index, len(cells), cells, known)
            if len(candidates) != 1:
                continue
            word = next(iter(candidates))
            for pos, cell in enumerate(cells):
                if cell not in known:
                    known[cell] = word[pos]
                    changed = True
    return known


def _close_implied_slots(slots, index, assignment, used_words, excluded_slots=None):
    """Referme, en une dernière passe bon marché, les emplacements dont la
    recherche a laissé toutes les lettres déjà déterminées par de vrais
    mots croisants réellement assignés — mais que `_backtrack` lui-même
    n'a jamais explicitement confirmés (il n'a simplement pas eu
    l'occasion de le sélectionner avant que la tentative ne se termine,
    quelle qu'en soit la raison : budget épuisé, abandon à 30 %,
    interruption par un frère de palier, ou recherche réellement
    épuisée). Un tel emplacement est visuellement "complet" (chaque case
    porte déjà une vraie lettre) mais reste formellement `None` dans
    `assignment` — donc ni compté comme réussi, ni jamais signalé
    injouable (`Filler.impossible_zone_slots()` ne le flague pas : son
    seul mot possible n'est pas encore utilisé ailleurs).

    Corrige un vrai bug rapporté en direct, capture d'écran à l'appui :
    "77% rempli" (= 100% des cases blanches déjà pourvues d'une lettre,
    23% de cases noires) avec 0% d'injouable, et pourtant une génération
    qui recommençait indéfiniment sans jamais aboutir — l'utilisateur l'a
    posé explicitement comme principe : "tous les emplacements restants
    doivent être testés avant de terminer un cycle... si tous les mots en
    place sont valides, la grille est alors réputée réussie." Cette
    fonction est exactement ce dernier test, appliqué une fois la
    recherche terminée plutôt que de compter sur `_backtrack` pour
    l'avoir fait de lui-même.

    Contrairement à `_force_single_candidate_slots` (utilisée avant même
    que la recherche ne démarre, sur les seules lettres déjà verrouillées
    d'un palier précédent — jamais de mot déjà placé à exclure à ce
    stade), celle-ci doit tenir compte de `used_words` : un mot déjà
    utilisé ailleurs dans la grille ne peut pas être confirmé une seconde
    fois, même s'il correspond exactement aux lettres déjà en place.

    Répétée jusqu'à un point fixe (confirmer un emplacement peut, via une
    case de croisement, en déterminer un autre à son tour) ; mute
    `assignment`/`used_words` sur place, aucune valeur de retour.

    Ne place jamais un mot deviné ou statistique, et ne fait jamais
    progresser la recherche elle-même : si aucun emplacement encore
    non-assigné (et non exclu) n'est déjà réduit à exactement un seul mot
    réel et disponible, cette fonction ne change rien du tout — elle ne
    fait que confirmer ce qui est déjà, implicitement, la seule
    possibilité restante.

    Le garde `len(candidates) - len(used_words) > 1` évite le coût d'un
    filtrage `not in used_words` sur un emplacement encore largement
    ouvert (des milliers de candidats bruts pour une longueur donnée,
    contre quelques dizaines/centaines de mots déjà utilisés) : retirer
    au plus `len(used_words)` mots ne peut jamais faire descendre un
    ensemble plus grand que `len(used_words) + 1` jusqu'à exactement 1,
    donc un tel emplacement ne peut de toute façon jamais se refermer ici
    — inutile de payer le filtrage pour le vérifier."""
    excluded = excluded_slots or set()
    known = {}
    for i, cells in enumerate(slots):
        word = assignment[i]
        if word is not None:
            for pos, cell in enumerate(cells):
                known[cell] = word[pos]
    changed = True
    while changed:
        changed = False
        for i, cells in enumerate(slots):
            if i in excluded or assignment[i] is not None:
                continue
            candidates = _slot_candidates(index, len(cells), cells, known)
            if len(candidates) - len(used_words) > 1:
                continue
            real_candidates = [w for w in candidates if w not in used_words]
            if len(real_candidates) != 1:
                continue
            word = real_candidates[0]
            assignment[i] = word
            used_words.add(word)
            for pos, cell in enumerate(cells):
                known[cell] = word[pos]
            changed = True


def sample_letter_biases(grid, rows, cols, index, rng,
                          sample_size=LETTER_BIAS_SAMPLE_SIZE,
                          force_fraction=LETTER_BIAS_FORCE_FRACTION,
                          excluded_slots=None, known_letters=None):
    """Avant de lancer le remplissage CSP réel sur une grille de cases
    noires/blanches fraîchement choisie, à la demande explicite de
    l'utilisateur : pour chaque emplacement, tire au hasard `sample_size`
    mots de la bonne longueur, compte pour chaque case de cet emplacement
    quelle lettre y apparaît le plus souvent dans l'échantillon, ne retient
    que les cases dont cette lettre dépasse `LETTER_BIAS_MIN_COUNT` (10)
    occurrences (un consensus trop faible — une lettre qui ne l'emporte que
    parce que les autres étaient encore plus dispersées — ne garantit pas
    qu'il reste
    assez de mots compatibles une fois cette lettre figée), puis pioche au
    hasard parmi ces cases éligibles jusqu'à couvrir `force_fraction` du
    nombre de cases blanches *encore sans lettre connue* de la grille — pas
    du nombre total de cases blanches, à la demande explicite de
    l'utilisateur (voir le calcul de `target` plus bas pour le
    raisonnement complet) — au plus UNE case forcée par emplacement
    (jamais deux cases forcées sur le même mot). Le tirage
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

    `known_letters` (un dict {case: lettre}, `None` par défaut — aucun
    effet pour un appelant qui n'en fournit pas), à la demande explicite de
    l'utilisateur : "Ne tirer que des mots valides par rapport aux lettres
    déjà en place sur les emplacements." Auparavant, l'échantillon d'un
    emplacement était tiré au hasard parmi *tous* les mots de la bonne
    longueur, sans tenir compte des lettres déjà connues à certaines de ses
    cases (`_pattern_attempt`'s `locked_letters`, reporté d'un palier à
    l'autre par le mécanisme de reprise — voir `generate_grid` — ou les
    lettres déjà fixées par `_pattern_continue`'s `preseed_assignment`) —
    un sondage moins informatif que nécessaire, puisqu'une bonne partie des
    100 mots tirés pouvait déjà être incompatible avec ce qui était pourtant
    déjà su avec certitude. Pour un emplacement dont au moins une case
    figure dans `known_letters`, l'échantillon est désormais tiré
    uniquement parmi les mots réellement compatibles avec ces lettres
    (même intersection par position que `Filler._domain`/
    `_slot_candidate_count`) plutôt que parmi le lexique entier de cette
    longueur. Si aucun mot ne correspond — un emplacement réputé impossible
    au sens propre du terme, puisque ses lettres déjà en place ne
    correspondent à aucun mot réel — l'échantillon est simplement vide et
    cet emplacement ne contribue ni à `forced` ni à `letter_scores` pour ce
    palier : à la demande explicite de l'utilisateur ("ne pas tester les
    emplacements réputés impossible... la modification doit rendre
    impossible les tirages valides"), ce filtrage suffit à lui seul à
    garantir qu'aucun tirage valide n'est possible sur un tel emplacement,
    sans avoir besoin d'un test explicite séparé — contrairement à
    `excluded_slots` ci-dessus (dont le rôle reste nécessaire pour
    `_pattern_continue` : un emplacement qui y figure peut être impossible
    pour une raison structurelle plus large que ses seules lettres déjà
    connues prises isolément, auquel cas ce filtrage-ci ne suffit pas à lui
    seul à l'exclure de l'échantillonnage). Une case déjà présente dans
    `known_letters` n'est jamais non plus proposée comme candidate à
    `eligible` (voir plus bas) : le mot déjà connu à cette position n'a
    besoin d'aucun indice statistique supplémentaire, et la retenir aurait
    gaspillé le quota d'une seule graine par emplacement au profit d'une
    case qui, elle, en aurait réellement eu besoin.

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
    known = known_letters or {}
    eligible = []  # (compte, case, lettre) — cases dépassant LETTER_BIAS_MIN_COUNT
    letter_scores = defaultdict(Counter)
    for slot_idx, cells in enumerate(slots):
        length = len(cells)
        idx = index.get(length)
        if not idx or not idx["words"]:
            continue
        # Restreint le lexique tiré aux mots réellement compatibles avec les
        # lettres déjà connues de cet emplacement (`_slot_candidates`, même
        # intersection par position que `Filler._domain`), à la demande
        # explicite de l'utilisateur — voir la docstring de `known_letters`
        # ci-dessus. Aucune contrainte connue : retombe sur le lexique
        # entier de cette longueur, exactement comme avant cette
        # fonctionnalité. Un ensemble vide (aucun mot réel ne correspond
        # aux lettres déjà en place — cet emplacement est impossible au
        # sens propre du terme) : aucun tirage valide n'existe, donc aucun
        # n'est fait (ni `forced` ni `letter_scores` pour lui à ce palier).
        pool = _slot_candidates(index, length, cells, known)
        if not pool:
            continue
        sample = rng.choices(list(pool), k=sample_size)
        for pos, cell in enumerate(cells):
            counts = Counter(word[pos] for word in sample)
            letter_scores[cell].update(counts)
            letter, count = counts.most_common(1)[0]
            if cell not in known and count > LETTER_BIAS_MIN_COUNT and slot_idx not in excluded:
                eligible.append((count, cell, letter))
    rng.shuffle(eligible)

    # À la demande explicite de l'utilisateur : le nombre de graines visé
    # doit être calculé par rapport au nombre de cases blanches *encore
    # sans lettre connue*, pas par rapport au nombre total de cases
    # blanches de la grille — sans quoi, une fois qu'un palier de reprise
    # "telle quelle" a déjà confirmé une bonne partie de la grille (voir
    # `known_letters` ci-dessus), le compte de cases blanches au sens brut
    # reste presque inchangé (seules de nouvelles cases noires le font
    # baisser), donnant l'impression trompeuse d'un nombre de graines
    # "constant" d'un cycle à l'autre alors que de moins en moins de cases
    # ont réellement besoin d'un indice statistique. Une case déjà dans
    # `known` n'est de toute façon jamais elle-même éligible à devenir une
    # graine (voir plus haut) — l'exclure aussi de la base de calcul du
    # nombre cible aligne les deux. Pour la toute première grille d'un
    # palier (`known` vide), ce compte est rigoureusement identique au
    # nombre total de cases blanches — comportement inchangé.
    remaining_white = sum(
        1 for r in range(rows) for c in range(cols)
        if grid[r][c] == WHITE and (r, c) not in known
    )
    target = round(remaining_white * force_fraction)
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
             attempt_done_event=None, locked_letters=None, best_state_queue=None,
             attempt_id=None):
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
    qui n'a jamais fourni son propre budget, jamais à ce cas-là.

    `best_state_queue` (`None` par défaut — aucun effet pour tout appelant
    existant), à la demande explicite de l'utilisateur : quand fourni, un
    callback est posé sur le `Filler` construit ici (`Filler.on_new_best`)
    pour publier, en temps réel, chaque nouveau record de `best_assignment`
    atteint pendant la recherche — pas seulement l'état final renvoyé par
    cette fonction une fois `filler.solve()` revenu. Le callback reconstruit
    l'aperçu complet (`example_grid`/`impossible_cells`/`forced_cells`/
    `locked_cells`) exactement comme le fait ce même `try_fill` sur échec
    plus bas, à partir de ce nouveau `best_assignment` — même fonctions,
    même forme de résultat — puis le publie sur `best_state_queue` (voir
    `_worker_best_state_queue`/`generate_grid` pour ce qu'il en fait
    ensuite). Un `grid` défensivement copié (`[row[:] for row in grid]`)
    accompagne chaque publication : `grid` lui-même ne change jamais après
    `make_pattern` (voir son propre docstring), mais chaque publication
    doit rester un instantané indépendant plutôt qu'une référence partagée,
    pour rester cohérente une fois désérialisée côté parent, où elle vivra
    plus longtemps que cet appel à `try_fill`.

    `attempt_id` (`None` par défaut — aucun effet pour tout appelant
    existant), à la demande explicite de l'utilisateur : "on ne garde
    qu'une seule meilleure grille par process." Un identifiant opaque
    (la graine de cette tentative précise, voir `_pattern_attempt`/
    `_pattern_continue`) recopié tel quel, sans aucun traitement, à la
    fois dans chaque état publié sur `best_state_queue` ci-dessus et dans
    `diagnostics["attempt_id"]` en cas d'échec — pour que `generate_grid`
    puisse reconnaître, parmi tous les états qu'un même palier lui fait
    remonter (le résultat final ET chacun des états publiés en cours de
    route), lesquels proviennent de la MÊME tentative parallèle, afin de
    n'en garder qu'un seul (le meilleur) par tentative dans l'aperçu
    affiché à l'écran (voir `generate_grid`)."""
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
    # une fois ici, avant que `solve()` ne s'exécute, puisque ni `preseed_
    # assignment` ni `locked_letters` ne changent au cours de cette
    # recherche (un emplacement déjà assigné dans `preseed_assignment` n'est
    # jamais reconsidéré — voir `Filler._backtrack`, qui ne retient que les
    # emplacements encore à `None` —, et `locked_letters` lui-même n'est
    # jamais modifié après ce point).
    #
    # Corrigé après un rapport direct de l'utilisateur, avec deux
    # captures d'écran à l'appui : « il y a des cas où le processus de
    # génération des mots ne préserve pas les cases verrouillées » —
    # l'aperçu montré juste après la pose des cases noires (avant la
    # recherche) affichait beaucoup de cases entourées comme
    # verrouillées, mais l'aperçu montré après l'échec de la recherche
    # n'en montrait plus qu'une poignée. La cause n'était pas une
    # perte réelle de contrainte : `locked_letters` (une fois fusionné
    # dans `forced_letters` par l'appelant — voir `_pattern_attempt`/
    # `_pattern_continue`) reste bien appliqué comme contrainte dure
    # par `Filler._domain` sur chaque emplacement touchant une de ses
    # cases, dans les deux directions, donc la lettre elle-même n'a
    # jamais changé. Le bug était uniquement dans ce diagnostic
    # `locked_cells` : il ne listait, avant ce correctif, que les cases
    # d'un emplacement *entièrement* couvert par `locked_letters` (donc
    # déjà promu en un mot réel dans `preseed_assignment`) — une case
    # verrouillée appartenant à un emplacement seulement *partiellement*
    # couvert (le reste de ses lettres restant à découvrir par la
    # recherche) n'apparaissait jamais dans `locked_cells`, alors
    # qu'elle est tout aussi verrouillée et contrainte que les autres.
    # `locked_letters`, quand fourni, est donc maintenant la source
    # principale de ce diagnostic — la même liste complète de cases que
    # celle déjà affichée par `_cycle_start_preview` avant la recherche
    # (voir generate_grid) — plutôt que `preseed_assignment` seul, qui
    # reste un simple repli pour un appelant qui ne fournirait que ce
    # dernier (aucun cas réel aujourd'hui : `_pattern_attempt`/
    # `_pattern_continue` fournissent toujours les deux ensemble).
    all_slot_cells = {cell for s in slots for cell in s}
    if locked_letters:
        locked_cells = sorted(cell for cell in locked_letters if cell in all_slot_cells)
    elif preseed_assignment is not None:
        locked_cells = sorted({cell for i, word in enumerate(preseed_assignment) if word is not None
                                for cell in slots[i]})
    else:
        locked_cells = []
    filler = Filler(slots, index, rng, forced_letters=forced_letters, letter_scores=letter_scores,
                     excluded_slots=excluded_slots, cancel_event=cancel_event,
                     batch_abandoned_event=batch_abandoned_event,
                     attempt_done_event=attempt_done_event, locked_letters=locked_letters)
    if best_state_queue is not None:
        # Assigné après construction, pas passé à Filler(...) directement
        # ci-dessus : la fermeture ci-dessous a besoin de `filler` lui-même
        # (pour lire filler.impossible_zone_cells(), qui dépend de l'état
        # complet du Filler, pas seulement du best_assignment reçu en
        # argument) — `filler` n'existe pas encore au moment où l'appel à
        # Filler(...) est construit, mais existe déjà par le temps que ce
        # callback sera réellement invoqué (depuis _backtrack, bien après).
        def _publish_new_best(best_assignment):
            example_grid, forced_cells, _ = build_partial_letters_grid(
                grid, slots, best_assignment, forced_letters
            )
            # `impossible_slots` (pas seulement `impossible_cells`) est
            # indispensable ici : côté parent, un état publié par cette file
            # peut se retrouver sélectionné comme `failed_pairs[0]`/parmi les
            # candidats du nettoyage (`_build_retry_seed`/`_clean_all_
            # candidates`), qui lisent tous deux `cand_diag["impossible_
            # slots"]` directement — l'omettre
            # ferait planter ce chemin dès qu'un état publié ici gagne la
            # sélection. `checks`/`reason` sont inclus par simple cohérence
            # de forme avec le diagnostic final produit plus bas (utile pour
            # backend.log si cet état gagne `last_diag`) mais ne sont lus
            # nulle part côté parent pour cet état intermédiaire — `reason`
            # porte une valeur dédiée (`"best_state_snapshot"`), distincte de
            # toutes celles produites en fin de recherche, pour qu'on
            # reconnaisse sans ambiguïté, dans les logs, un état publié en
            # cours de route plutôt qu'un résultat final de tentative.
            best_state_queue.put({
                "grid": [row[:] for row in grid],
                "assignment": list(best_assignment),
                "example_grid": example_grid,
                "impossible_cells": filler.impossible_zone_cells(),
                "impossible_slots": filler.impossible_zone_slots(),
                "forced_cells": forced_cells,
                "locked_cells": locked_cells,
                "checks": filler.checks,
                "reason": "best_state_snapshot",
                "attempt_id": attempt_id,
            })
        filler.on_new_best = _publish_new_best
    if preseed_assignment is not None:
        filler.assignment = list(preseed_assignment)
        filler.used_words = {w for w in preseed_assignment if w is not None}
        filler.best_assignment = list(preseed_assignment)
        filler.best_assigned_count = sum(1 for w in preseed_assignment if w is not None)
    filler.exclude_immediately_impossible_slots()
    solved_internally = filler.solve(deadline_checks)
    # Referme les emplacements déjà entièrement déterminés par de vrais
    # mots croisants mais jamais explicitement confirmés par `_backtrack`
    # lui-même — voir `_close_implied_slots`'s propre docstring pour le
    # bug réel que ceci corrige. Opère sur `filler.best_assignment` (le
    # plus haut niveau de progrès jamais atteint, pas l'état courant de
    # `filler.assignment`, potentiellement déjà partiellement "dépilé" par
    # le retour en arrière si la recherche s'est terminée en échec) —
    # c'est aussi cet état, jamais `filler.assignment` directement, que
    # tout le reste de ce fichier (diagnostics, `_build_retry_seed`,
    # l'aperçu affiché) lit déjà plus bas.
    _close_implied_slots(slots, index, filler.best_assignment, filler.used_words, filler.excluded_slots)
    # `filler.assignment` resynchronisé depuis `best_assignment` une fois
    # cette fermeture appliquée : sur un succès natif (`solved_internally`
    # sans emplacement exclu), les deux coïncidaient déjà, donc cette
    # ligne ne change rien ; sur tout autre issue, c'est `best_assignment`
    # — jamais réduit par un retour en arrière, seulement augmenté ici —
    # qui reflète l'état réel à prendre en compte pour décider si cette
    # tentative est réellement complète.
    filler.assignment = list(filler.best_assignment)
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
            diagnostics["attempt_id"] = attempt_id
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


def _low_candidate_slot_cells(grid, rows, cols, index, locked_letters):
    """Toutes les cases d'un emplacement *partiellement* verrouillé (au
    moins une case verrouillée, mais pas toutes — voir `_slot_with_
    insufficient_candidates` pour pourquoi un emplacement *entièrement*
    verrouillé n'est jamais concerné : il est déjà un mot réel et
    confirmé, pas un emplacement encore fragile) dont l'intersection avec
    les lettres déjà verrouillées laisse strictement moins de
    `PREFILL_LOCKED_MIN_WORD_COUNT` (3) candidats réels dans le
    dictionnaire (`_slot_candidate_count`, la même intersection par
    position que `Filler._domain`).

    Purement diagnostique, pour l'aperçu web — à la demande explicite de
    l'utilisateur, sur la grille "Génération du motif de cases noires"
    (l'événement `pattern`, l'état de départ d'un cycle) : "afficher en
    fond orange les cases en dessous du seuil des possibilités de
    remplissage (< 3 possibilités)", pour rendre visible, avant même que
    le pré-remplissage/nettoyage curatif n'agisse dessus, quels
    emplacements sont déjà fragiles. Contrairement à `_slot_with_
    insufficient_candidates` (utilisée par le pré-remplissage lui-même
    pour *décider* d'une action, et qui s'arrête au tout premier
    emplacement problématique trouvé, avec `skip`/longueur-seule en
    plus), cette fonction-ci renvoie l'ensemble complet des cases
    concernées, sur tous les emplacements à la fois — rien n'a besoin
    d'être ciblé un par un pour un simple affichage.

    Renvoie une liste triée de cases `(r, c)`, vide si `locked_letters`
    est vide/`None` (rien à signaler) ou si aucun emplacement ne passe
    sous le seuil."""
    if not locked_letters:
        return []
    cells = set()
    for slot in extract_slots(grid, rows, cols):
        length = len(slot)
        locked_count = sum(1 for cell in slot if cell in locked_letters)
        if 0 < locked_count < length:
            if _slot_candidate_count(index, length, slot, locked_letters) < PREFILL_LOCKED_MIN_WORD_COUNT:
                cells.update(slot)
    return sorted(cells)


def _noise_slot_cells(grid, rows, cols, index, locked_letters):
    """Cases où **aucune lettre** ne satisfait à la fois l'emplacement
    horizontal et l'emplacement vertical qui s'y croisent — le domaine brut
    de chacun des deux emplacements, pris séparément, peut très bien être
    non vide (donc ni l'un ni l'autre n'est signalé par le surlignage rouge
    `.impossible`, un domaine totalement vide), mais si aucun de leurs mots
    réellement *jouables* respectifs ne partage la même lettre à cette case
    précise, elle ne peut en pratique jamais être remplie.

    « Réellement jouable », pour un emplacement *partiellement* verrouillé
    (même restriction que `_low_candidate_slot_cells` ci-dessus — un
    emplacement entièrement verrouillé est déjà un mot confirmé, un
    emplacement entièrement vierge est hors du champ de cette fonction),
    signifie un candidat du dictionnaire (`_slot_candidates`) qui n'est ni :
    - déjà utilisé ailleurs dans cette même grille (tout autre emplacement
      entièrement verrouillé, `used_words`, calculé ici directement depuis
      `locked_letters` — un mot déjà posé ne peut plus être reposé) ;
    - en dessous de `NOISE_FREQUENCY_THRESHOLD` en fréquence brute
      (`index[length]["freq"]`, voir `build_index`) — voir la docstring de
      cette constante pour sa calibration.

    Une case ne croisée QUE par un seul emplacement encore ouvert (l'autre
    direction y est déjà entièrement verrouillée, ou n'y forme même pas un
    véritable emplacement — une zone d'une seule case) est un cas
    dégénéré de la même règle : elle est signalée si et seulement si ce
    seul emplacement, à lui seul, n'a plus aucun mot jouable du tout.

    Cas concret ayant motivé cette fonctionnalité, voir CLAUDE.md : une
    grille bloquée 11 paliers d'affilée sur 3 cases, dont une seule
    (l'intersection entre un emplacement horizontal `_S_` et un
    emplacement vertical `ER_`) était en réalité totalement bloquée par ce
    critère — chaque emplacement pris isolément avait pourtant bel et bien
    des mots jouables (`OST`/`PST`/...  d'un côté, `ERG`/`ERS`/`ERE` de
    l'autre), mais aucune lettre commune aux deux ensembles à leur case
    partagée ; un premier jet de cette fonction, qui ne vérifiait chaque
    emplacement qu'isolément (sans le croisement), ne signalait donc rien
    du tout sur cette grille — vérifié en la rejouant explicitement contre
    l'historique réel de cette même génération.

    Purement diagnostique, sur l'aperçu "Génération du motif de cases
    noires" (l'événement `pattern`), à la demande explicite de
    l'utilisateur — même portée que `_low_candidate_slot_cells`, jamais
    calculée pour un palier de reprise "telle quelle" (voir son propre
    appelant dans `generate_grid`)."""
    if not locked_letters:
        return []
    all_slots = extract_slots(grid, rows, cols)
    used_words = {
        "".join(locked_letters[cell] for cell in slot)
        for slot in all_slots
        if all(cell in locked_letters for cell in slot)
    }
    # Mots jouables par emplacement partiellement verrouillé (None pour
    # tout autre emplacement — entièrement verrouillé ou entièrement
    # vierge — hors du champ de cette fonction, voir la docstring).
    playable_by_slot = []
    for slot in all_slots:
        length = len(slot)
        locked_count = sum(1 for cell in slot if cell in locked_letters)
        if not (0 < locked_count < length):
            playable_by_slot.append(None)
            continue
        candidates = _slot_candidates(index, length, slot, locked_letters)
        freq_map = index.get(length, {}).get("freq", {})
        playable_by_slot.append([
            w for w in candidates
            if w not in used_words and freq_map.get(w, 0.0) >= NOISE_FREQUENCY_THRESHOLD
        ])
    # Pour chaque case encore libre, les emplacements ouverts (au sens
    # ci-dessus) qui la traversent, avec sa position exacte dans chacun —
    # 1 seul pour une case bordée d'un côté par du verrouillé/du noir, 2
    # pour un vrai croisement horizontal/vertical.
    cell_slots = defaultdict(list)
    for i, slot in enumerate(all_slots):
        if playable_by_slot[i] is None:
            continue
        for pos, cell in enumerate(slot):
            if cell not in locked_letters:
                cell_slots[cell].append((i, pos))
    cells = set()
    for cell, entries in cell_slots.items():
        letter_sets = []
        for slot_i, pos in entries:
            playable = playable_by_slot[slot_i]
            if not playable:
                letter_sets = []
                break
            letter_sets.append({w[pos] for w in playable})
        if not letter_sets:
            cells.add(cell)
            continue
        common = letter_sets[0]
        for s in letter_sets[1:]:
            common &= s
        if not common:
            cells.add(cell)
    return sorted(cells)


def _cycle_start_preview(rows, cols, seed_grid, locked_letters, preseed_assignment):
    """Builds the single-grid preview shown right at the *start* of a
    palier (generate_grid's own `progress("pattern", ...)` call), at the
    user's explicit request: "Ajouter à l'historique (donc stacké) l'état
    initial d'un cycle." Until this, examples_history (backend/app.py)
    only ever stacked a palier's own *end* state (a failed attempt, or the
    winning grid at "minimizing"/"clues") — the state a palier actually
    *starts from* (whatever `_build_retry_seed`/`_clean_blocked_slots`
    carried forward from the previous one, or a blank grid for the very
    first palier) had no entry of its own in that history at all.

    Returns `(example_grid, locked_cells)` — the same two fields every
    other preview entry already carries (`impossible_cells`/`forced_cells`
    are always empty for this one: nothing has been searched yet at this
    point, so nothing is "impossible" yet, and no statistical hint has
    been sampled yet either — that only happens once this palier's own
    `_pattern_attempt`/`_pattern_continue` workers actually run). Wired
    into `progress("pattern", ...)` below, so a fresh grid is stacked into
    `examples_history` right alongside the "Tentative X/Y..." status
    already carried by that same event's own kwargs — the two arrive
    together in one call, so the web UI's history navigation
    (frontend/static/script.js's previewHistory, see CLAUDE.md) always
    shows a cycle's real starting point paired with its own cycle count,
    not just its outcome.

    `locked_cells` mirrors the meaning it already has everywhere else in
    this file (see `build_partial_letters_grid`'s own docstring, `try_
    fill`'s diagnostics): a cell whose shown letter is a real, previously-
    confirmed one carried over from the *previous* palier, not a guess —
    rendered with the same distinct `.locked` highlight already used
    elsewhere in the web UI. Handles both of `generate_grid`'s mutually
    exclusive resume shapes: `preseed_assignment` (the "reprise
    telle-quelle" case, a real word per already-assigned slot) and
    `locked_letters` (the "nettoyage" case, a plain `{cell: letter}` map).
    `seed_grid` being `None` (the very first palier of a call) means a
    blank grid with nothing locked at all, regardless of the other two."""
    if seed_grid is None:
        return [[WHITE] * cols for _ in range(rows)], []
    grid = [row[:] for row in seed_grid]
    # `if grid[r][c] == BLACK: continue` below (both branches) guards
    # against a case found and confirmed live: `generate_grid`'s "reset"
    # mechanism (FULL_RESET_ATTEMPT_COUNT) starts a handful of
    # a palier's own `_pattern_attempt` workers from a totally blank,
    # *independent* grid rather than building on top of `carry_seed_grid`
    # — this function is reused (see progress("pattern_generated", ...)
    # in generate_grid) to preview *each* of a palier's own outcomes, one
    # of which can genuinely be such a reset worker's own unrelated
    # pattern, passed here as `seed_grid`. Without this guard, a locked
    # cell (from `carry_locked_letters`/`carry_preseed_assignment`, both
    # computed against the *previous* palier's own pattern) could
    # coincide with a black cell this specific reset worker's own
    # `make_pattern` call happened to place there, and unconditionally
    # writing a letter over it would silently erase that black cell from
    # the shown preview — reproduced live: a real generate_grid() run's
    # `pattern_generated` event showed *fewer* black cells than the same
    # palier's own "pattern" (cycle-start) event, an impossible outcome
    # without this bug, since `make_pattern` can only ever add black
    # cells on top of a real `carry_seed_grid`, never remove any — this
    # guard is what actually prevents that from ever showing here again.
    # For the ordinary case (this palier's own real carried-forward
    # pattern, or "reprise telle-quelle"'s byte-identical one), a locked
    # cell is already guaranteed to stay white by construction elsewhere
    # in this file, so this guard is a pure no-op there — it only ever
    # changes anything for a reset worker's own independent pattern.
    if preseed_assignment is not None:
        slots = extract_slots(seed_grid, rows, cols)
        locked_cells = []
        for cells, word in zip(slots, preseed_assignment):
            if word is None:
                continue
            for (r, c), ch in zip(cells, word):
                if grid[r][c] == BLACK:
                    continue
                grid[r][c] = ch
                locked_cells.append((r, c))
        return grid, sorted(locked_cells)
    if locked_letters:
        locked_cells = []
        for (r, c), ch in locked_letters.items():
            if grid[r][c] == BLACK:
                continue
            grid[r][c] = ch
            locked_cells.append((r, c))
        return grid, sorted(locked_cells)
    return grid, []


def _playable_score(diag):
    """Mesure la quantité de contenu réellement posé et confirmé dans
    `diag["assignment"]` — racine carrée de la somme des carrés des
    longueurs de chaque mot déjà assigné (`None` ignoré) — à la demande
    explicite de l'utilisateur : "Au lieu d'un score sur les injouables,
    mesurer les jouables (racine carré des sommes des carrés des longueurs
    jouables)." Utilisé par `generate_grid` pour trier `failed_unique` en
    sélectionnant la "meilleure" tentative échouée d'un palier — voir son
    propre commentaire pour le biais que ce critère corrige (un ancien tri
    par "le moins de cases injouables" favorisait à tort un état publié
    tôt dans une recherche encore peu avancée, où peu de mots posés
    signifie mécaniquement peu de cases pouvant déjà être jugées
    injouables).

    Même principe que le score déjà utilisé par `generate_grid` pour
    départager plusieurs tentatives *réussies* du même palier — favoriser
    quelques mots longs plutôt que beaucoup de mots courts pour le même
    total de lettres — avec en plus la racine carrée pour ramener ce score
    à une échelle comparable à une simple longueur plutôt qu'à une somme de
    carrés. La longueur d'un mot assigné est prise directement via
    `len(word)` (jamais recalculée depuis le motif) : un mot ne peut être
    assigné qu'à un emplacement de sa propre longueur, donc les deux
    valeurs sont toujours rigoureusement égales."""
    return sum(len(w) ** 2 for w in diag["assignment"] if w is not None) ** 0.5


def _cleaned_playable_score(grid, diag, rows, cols, index, rng):
    """Comme `_playable_score`, mais sur l'état APRÈS nettoyage — le
    contenu qui survivrait réellement une fois retiré, un par un, ce qui
    est nécessaire pour lever chaque situation impossible de `diag[
    "impossible_slots"]` (`_clean_blocked_slots`, voir sa propre docstring
    pour l'algorithme "un mot à la fois" désormais utilisé) — plutôt que
    sur `diag["assignment"]` brut, à la demande explicite de
    l'utilisateur : "Il faut montrer les emplacements avant nettoyage,
    évaluer la grille après nettoyage (qui sera transmise au cycle
    suivant si sélectionnée)." `index`/`rng` transmis tels quels à
    `_clean_blocked_slots` — le même générateur aléatoire déjà partagé
    par tout `generate_grid()`, pour que ce score reste reproductible
    depuis la même graine plutôt que d'introduire une seconde source
    d'aléatoire indépendante.

    Utilisé pour trier `failed_unique`/choisir `failed_pairs[0]` — la
    tentative qui l'emporte est donc désormais celle qui garde le plus de
    contenu réellement posé une fois nettoyée, pas celle qui, avant tout
    nettoyage, a le moins de cases injouables ou le plus de contenu brut :
    deux tentatives avec le même nombre de cases injouables brutes peuvent
    perdre des quantités de contenu très différentes une fois nettoyées
    (une tentative dont le mot croisant l'emplacement impossible est
    court perd moins qu'une tentative dont il est long), et c'est bien
    cette quantité *après* nettoyage qui détermine ce qui sera réellement
    transmis au palier suivant si cette tentative est retenue — c'est
    donc elle qu'il faut évaluer, pas l'état brut.

    Recalcule `slots` directement depuis le vrai motif noir/blanc `grid`
    (jamais depuis un `example_grid` aux lettres superposées, qui
    fausserait `extract_slots`) — chaque tentative a son propre motif et
    sa propre affectation, rien à partager entre elles. Repli sur
    `_playable_score(diag)` (l'état brut) si `slots` ne correspond pas en
    longueur à `diag["assignment"]` — ne devrait jamais arriver en usage
    réel, un filet de sécurité plutôt qu'un cas attendu."""
    slots = extract_slots(grid, rows, cols)
    if len(slots) != len(diag["assignment"]):
        return _playable_score(diag)
    cleaned_assignment, _, _ = _clean_blocked_slots(
        slots, diag["assignment"], diag["impossible_slots"], index=index, rng=rng,
    )
    return sum(len(w) ** 2 for w in cleaned_assignment if w is not None) ** 0.5


def _public_diag(diag):
    """Copie de `diag` sûre à étaler dans un événement `progress(...)` —
    filet de sécurité générique contre un futur champ de diagnostic qui
    ne serait pas JSON-safe tel quel (par exemple un dict indexé par
    cellule `(row, col)`, un tuple comme *clé* de dict), plutôt qu'un
    filtre pour un champ précis aujourd'hui.

    Root-causé en direct, la première (et jusqu'ici seule) fois que ce
    problème s'est posé : un vrai `GET /api/generate/status/{job_id}`
    tombé en 500 (l'interface web affichait alors une erreur
    "JSON.parse: unexpected character..." puisque le corps de réponse
    n'était plus du JSON valide) — `backend.log` montrait
    `TypeError: cannot use 'list' as a dict key` au beau milieu de
    `fastapi.encoders.jsonable_encoder`. Le champ fautif à l'époque,
    `own_locked_letters` (voir `_pattern_attempt` dans son historique),
    encodait chaque case comme clé de dict — `jsonable_encoder` encode
    récursivement chaque clé pour la rendre JSON-safe, ce qui transforme
    un tuple en liste, puis tente de s'en servir comme clé d'un dict
    Python tout court pour construire le résultat encodé — une liste
    n'étant pas hashable, ça lève cette même `TypeError`. Chaque autre
    champ de `diag` contenant des cellules (`locked_cells`,
    `impossible_cells`, `forced_cells`...) les porte en tant qu'éléments
    d'une simple liste, jamais en tant que clés de dict — aucun d'eux ne
    pose ce problème. `own_locked_letters` lui-même a depuis été retiré
    entièrement (son seul lecteur, `_preview_locked_source`, a disparu en
    même temps que l'aperçu tardif qu'il alimentait) — cette fonction
    reste néanmoins en place, volontairement, comme garde-fou pour la
    même classe de bug si un futur champ de diagnostic prenait une forme
    similaire."""
    return {k: v for k, v in diag.items() if k != "own_locked_letters"}


# Probabilité de tenter une case noire plutôt que de retirer un mot
# croisant, dans la boucle "un par un" de `_clean_blocked_slots` ci-dessous
# — à la demande explicite de l'utilisateur, restreinte à la reprise
# "telle quelle" uniquement (voir `generate_grid`, branche `if
# still_has_hope:`), jamais au nettoyage complet (`_build_retry_seed`, qui
# régénère déjà un motif neuf via `make_pattern` et peut donc déjà ajouter
# des cases noires par ce biais) : "En l'état, nettoyer les zones
# impossibles et les connectés, supprime beaucoup de mots, ce qui oblige
# plus tard à rajouter des cases noires par d'autres mécanismes. Autant
# tenter la case noire tout de suite, et supprimer moins de mots. Par
# ailleurs, sur des toutes petites zones, la suppression de mots ne
# supprime pas grand chose, et la recherche tourne en rond sur très peu de
# lettres modifiables. Ajouter des noires peut permettre de réellement
# finir ces petites zones où la vraie solution n'existe peut-être pas."
# Abaissée de 1/3 à 1/10 juste après, à la demande explicite de
# l'utilisateur ("trop de cases noires à 1/3") — même mécanisme, valeur
# revue à la baisse suite à un premier usage réel jugé trop agressif.
BLACK_CELL_INSTEAD_OF_REMOVAL_PROBABILITY = 1 / 10


def _clean_blocked_slots(slots, assignment, impossible_slots, locked_letters=None,
                          exclude_impossible_locked=False, index=None, rng=None,
                          grid=None, rows=None, cols=None):
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
    recomposer).

    Retire ensuite TOUS les mots croisant chaque emplacement de
    `impossible_slots`, d'un coup — comportement à nouveau en vigueur, à
    la demande explicite de l'utilisateur : "Actuellement : pour un
    emplacement réputé injouable, on ne supprime qu'un seul mot croisant.
    Modifier : on retire tous les mots croisants (situation antérieure)."
    Une évolution intermédiaire de cette fonction avait remplacé ce
    retrait global par un retrait un mot à la fois, qui s'arrêtait dès
    qu'au moins un vrai candidat redevenait possible (voir CLAUDE.md pour
    l'historique complet de cette évolution, y compris la mesure en
    direct — 55 % de mots retirés en moins — qui l'avait motivée) ; ce
    changement intermédiaire est désormais annulé, à la demande explicite
    de l'utilisateur, sans toucher à l'alternative case noire ci-dessous
    (introduite après coup, mais indépendante du nombre de mots retirés
    par ailleurs) : elle reste tentée une fois par emplacement impossible,
    et seulement si elle échoue (ou n'est pas tentée) que TOUS les mots
    croisants encore assignés sont retirés en une seule fois, jamais un
    seul à la fois.

    Avant de retirer un mot croisant, tente — avec une probabilité
    `BLACK_CELL_INSTEAD_OF_REMOVAL_PROBABILITY` (1/10, abaissée de 1/3
    initial — voir le commentaire de cette constante) — une alternative,
    à la demande explicite de l'utilisateur (voir le commentaire de cette
    constante pour son raisonnement complet) : noircir une case de
    l'emplacement impossible lui-même plutôt que de retirer le mot qui le
    croise. Seulement disponible quand `grid`/`rows`/`cols` sont fournis
    (`None` par défaut — no-op pour tout appelant qui ne les fournit pas,
    en particulier `_build_retry_seed`/`_cleaned_playable_score`, qui
    restent volontairement retrait-de-mot uniquement). Parmi les cases de
    l'emplacement, celles *pas déjà* déterminées par un mot croisant
    encore assigné (`known`) sont essayées en priorité — noircir une case
    déjà couverte par un mot confirmé détruirait ce mot-là aussi, un
    résultat plus destructeur qu'une case encore libre — mais, à la
    demande explicite de l'utilisateur, une case déjà connue est tentée en
    second recours plutôt que de renoncer entièrement à cette alternative
    quand l'emplacement est déjà entièrement croisé (le cas le plus
    fréquent en fin de partie, quand peu de cases restent réellement
    libres) : dans ce cas, cette case noire retire alors, comme effet de
    bord, le mot croisant qui l'occupait — exactement comme le ferait un
    retrait de mot classique, mais en éliminant en plus, définitivement,
    cette case de l'emplacement impossible plutôt que de simplement
    libérer sa contrainte. Ce n'est que si aucune case de l'emplacement
    (libre ou déjà connue) ne reste structurellement valide une fois
    noircie que l'on retombe sur le retrait de mot habituel. Parmi
    chacun des deux groupes de cases, tirage sans biais positionnel
    (mélange avant essai, comme partout ailleurs dans ce fichier) puis
    premier candidat qui reste structurellement valide
    (`is_structurally_valid(..., min_interior_free=1)`) une fois noirci ;
    tout mot *autre* que celui de l'emplacement impossible lui-même mais
    passant par cette case précise est désassigné (il ne peut plus exister
    une fois la case noire). Contrairement au retrait de mot (qui ne fait
    que libérer une contrainte sur le MÊME emplacement i, qui continue
    d'exister sous sa forme actuelle ce palier-ci), poser une case noire
    *élimine* l'emplacement i sous sa forme actuelle — dès qu'une case a
    été noircie avec succès pour i, plus aucun retrait de mot n'est tenté
    pour lui ce tour-ci : ses fragments réels ne seront redécouverts qu'au
    prochain `extract_slots` sur la grille mise à jour, exactement comme
    pour toute autre case noire ajoutée ailleurs dans ce fichier.

    Zone strictement sans issue, à la demande explicite de l'utilisateur :
    une fois tous les mots croisants effectivement retirés (le cas normal
    ci-dessus, quand la case noire n'a pas été tentée ou a échoué), si
    l'emplacement n'a *toujours* strictement aucun candidat réel une fois
    toute contrainte de croisement ainsi levée (`count == 0` —
    typiquement une longueur que le dictionnaire ne couvre pas du tout),
    plus aucun retrait de mot ne pourra jamais débloquer cette zone :
    toutes ses cases restantes sont alors noircies directement (même
    garde-fou `is_structurally_valid(min_interior_free=1)` par case,
    jamais un passe-droit), plutôt que de la laisser resurgir identique à
    chaque nettoyage futur.

    Retourne `(cleaned_assignment, confirmed, new_black_cells)` —
    `cleaned_assignment` est une nouvelle liste (jamais une mutation de
    `assignment` reçu), avec un `None` explicite pour chaque emplacement
    retiré, prête à servir directement de `preseed_assignment` au palier
    suivant ; `new_black_cells` est l'ensemble (potentiellement vide) des
    cases nouvellement noircies par cette alternative — à fondre dans le
    motif transmis au palier suivant par l'appelant, `_clean_blocked_
    slots` elle-même ne mutant jamais `grid` en place (une copie de
    travail interne, jetée après l'appel)."""
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
    else:
        assignment = list(assignment)

    cell_to_slots = defaultdict(list)
    for i, cells in enumerate(slots):
        for cell in cells:
            cell_to_slots[cell].append(i)

    new_black_cells = set()
    black_cell_capable = (
        grid is not None and rows is not None and cols is not None
    )
    working_grid = [row[:] for row in grid] if black_cell_capable else None

    if index is not None and rng is not None:
        for i in impossible_slots:
            crossing = sorted({
                j for cell in slots[i] for j in cell_to_slots[cell]
                if j != i and assignment[j] is not None
            })

            # Alternative case noire, à la demande explicite de
            # l'utilisateur (voir le commentaire de
            # BLACK_CELL_INSTEAD_OF_REMOVAL_PROBABILITY pour son
            # raisonnement complet) — tentée une seule fois par
            # emplacement impossible, indépendamment du retrait de mots
            # ci-dessous (qui, lui, retire à nouveau TOUS les mots
            # croisants d'un coup, voir la docstring ci-dessus).
            placed_black = False
            if crossing and black_cell_capable and rng.random() < BLACK_CELL_INSTEAD_OF_REMOVAL_PROBABILITY:
                known = {}
                for cell in slots[i]:
                    for j in cell_to_slots[cell]:
                        if j != i and assignment[j] is not None:
                            known[cell] = assignment[j][slots[j].index(cell)]
                            break
                blank_candidates = [cell for cell in slots[i] if cell not in known]
                known_candidates = [cell for cell in slots[i] if cell in known]
                rng.shuffle(blank_candidates)
                rng.shuffle(known_candidates)
                for (br, bc) in blank_candidates + known_candidates:
                    working_grid[br][bc] = BLACK
                    if is_structurally_valid(working_grid, rows, cols, min_interior_free=1):
                        new_black_cells.add((br, bc))
                        for j in cell_to_slots[(br, bc)]:
                            if j != i and assignment[j] is not None:
                                assignment[j] = None
                        placed_black = True
                        break
                    working_grid[br][bc] = WHITE
            if placed_black:
                continue

            for j in crossing:
                assignment[j] = None

            # Zone strictement sans issue, à la demande explicite de
            # l'utilisateur : tous les mots croisants viennent d'être
            # retirés (ci-dessus) et, une fois toute contrainte de
            # croisement ainsi levée, l'emplacement n'a toujours
            # strictement aucun candidat réel (`count == 0` —
            # typiquement une longueur que le dictionnaire ne couvre pas
            # du tout) : plus aucun retrait de mot ne pourra jamais
            # débloquer cette zone, donc on noircit directement toutes ses
            # cases restantes plutôt que de la laisser resurgir identique
            # à chaque nettoyage futur (voir CLAUDE.md pour le point fixe
            # réel que cette situation a fini par causer sur une grande
            # grille). Comme pour l'alternative ci-dessus, chaque case est
            # essayée avec `is_structurally_valid(min_interior_free=1)`
            # avant d'être noircie — jamais un passe-droit sur cet
            # invariant absolu, même ici.
            if black_cell_capable:
                count = _slot_candidate_count(index, len(slots[i]), slots[i], {})
                if count == 0:
                    for (br, bc) in slots[i]:
                        if working_grid[br][bc] == BLACK:
                            continue
                        working_grid[br][bc] = BLACK
                        if is_structurally_valid(working_grid, rows, cols, min_interior_free=1):
                            new_black_cells.add((br, bc))
                            for j in cell_to_slots[(br, bc)]:
                                if j != i and assignment[j] is not None:
                                    assignment[j] = None
                        else:
                            working_grid[br][bc] = WHITE
    else:
        for i in impossible_slots:
            for cell in slots[i]:
                for j in cell_to_slots[cell]:
                    if j != i and assignment[j] is not None:
                        assignment[j] = None

    confirmed = {}
    for i, word in enumerate(assignment):
        if word is None:
            continue
        for cell, ch in zip(slots[i], word):
            confirmed[cell] = ch

    return assignment, confirmed, new_black_cells


def _plug_isolated_cells(grid, rows, cols, slots, assignment, index):
    """Dernier recours tenté à la fin d'un palier en échec, à la demande
    explicite de l'utilisateur : "Lorsque toutes les recherches échouent en
    laissant une grille avec [ne reste] plus que des cases blanches
    isolées, boucher les cases isolées avec une case noire. Si le résultat
    donne une grille où tous les emplacements possibles sont remplis et
    valides, déclarer la grille réussie."

    Une case blanche encore sans lettre ("non remplie") est ici toute case
    qu'aucun emplacement assigné (`assignment[i] is not None`) ne couvre —
    y compris une case dont l'emplacement croisé (l'autre direction) EST
    assigné, ce qui lui donne déjà une vraie lettre malgré tout : `known`
    ci-dessous reflète exactement cette réalité, case par case, pas
    emplacement par emplacement.

    Une case non remplie est dite "isolée" si aucune de ses 4 cases
    voisines orthogonales n'est, elle aussi, non remplie — c'est-à-dire que
    tous ses voisins sont déjà noirs ou déjà pourvus d'une vraie lettre.
    C'est une définition volontairement prudente : si une case non remplie
    a ne serait-ce qu'un seul voisin non rempli, cela signifie qu'un vrai
    emplacement d'au moins 2 lettres reste encore ouvert à cet endroit (un
    mot qui pourrait encore, en principe, être trouvé) — ce n'est alors
    plus "rien que des cases isolées", et cette fonction n'y touche pas du
    tout : ni cette case, ni aucune autre de la grille, n'est modifiée. Une
    case isolée, à l'inverse, ne peut par construction jamais faire partie
    d'un emplacement encore ouvert d'au moins 2 cases : boucher une telle
    case ne raccourcit jamais un mot déjà confirmé, ni ne retire aucune
    vraie lettre déjà posée.

    Ne fait rien (renvoie `None`) dans trois cas : (1) il reste au moins une
    case non remplie qui n'est pas isolée (un vrai emplacement encore
    ouvert existe ailleurs — pas seulement des cases isolées) ; (2) noircir
    l'ensemble des cases isolées casserait la validité structurelle de la
    grille (connexité, ou une case blanche orpheline ailleurs —
    `is_structurally_valid` au niveau le plus strict, `min_interior_free=
    1`) ; (3) une fois les cases isolées bouchées, au moins un emplacement
    du nouveau motif (`extract_slots` recalculé sur la grille modifiée)
    reste soit sans lettre connue à toutes ses cases, soit rempli d'une
    combinaison qui ne correspond à aucun mot réel du dictionnaire — la
    grille obtenue n'est alors PAS "remplie et valide" au sens de la
    demande, donc pas question de la déclarer réussie. Sinon (tous les
    emplacements du nouveau motif sont entièrement connus et forment un mot
    réel), renvoie `(new_grid, new_slots, new_assignment)` — un résultat
    directement utilisable comme une réussite complète de génération, au
    même titre qu'un remplissage CSP qui aurait abouti normalement."""
    known = {}
    for i, cells in enumerate(slots):
        word = assignment[i]
        if word is not None:
            for pos, cell in enumerate(cells):
                known[cell] = word[pos]
    unfilled = {
        (r, c)
        for r in range(rows)
        for c in range(cols)
        if grid[r][c] == WHITE and (r, c) not in known
    }
    if not unfilled:
        return None
    for (r, c) in unfilled:
        for dr, dc in ((-1, 0), (1, 0), (0, -1), (0, 1)):
            if (r + dr, c + dc) in unfilled:
                return None
    new_grid = [row[:] for row in grid]
    for (r, c) in unfilled:
        new_grid[r][c] = BLACK
    if not is_structurally_valid(new_grid, rows, cols, min_interior_free=1):
        return None
    new_slots = extract_slots(new_grid, rows, cols)
    new_assignment = []
    for cells in new_slots:
        if any(cell not in known for cell in cells):
            return None
        candidates = _slot_candidates(index, len(cells), cells, known)
        if not candidates:
            return None
        new_assignment.append(next(iter(candidates)))
    return new_grid, new_slots, new_assignment


# `_impossible_cell_groups`/`_lock_one_impossible_cell` — the single-cell
# lock this project's history called "the mechanism tied to cleanup" — were
# removed entirely, at the user's explicit request, right after quoting
# their own prior description of the mechanism back and saying to delete
# it: no black cell is added anywhere by the cleanup path any more, either
# (see `generate_grid`'s own nettoyage branches below and `make_pattern`'s
# docstring above for the sibling per-step density draw removed the same
# session). See CLAUDE.md for the removed mechanism's full history.


def _build_retry_seed(grid, rows, cols, slots, assignment, impossible_slots, locked_letters=None,
                       exclude_impossible_locked=False, seed_grid=None, index=None, rng=None):
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
    assignment, confirmed, _ = _clean_blocked_slots(
        slots, assignment, impossible_slots, locked_letters=locked_letters,
        exclude_impossible_locked=exclude_impossible_locked, index=index, rng=rng,
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

    # Protection inconditionnelle des cases noires déjà présentes *avant*
    # que ce palier ne commence (`seed_grid`, le motif reçu en entrée par
    # `_pattern_attempt`/`make_pattern` pour CE palier précis, avant son
    # propre pré-remplissage/placement au ratio/« nettoyage curatif ») — à
    # la demande explicite de l'utilisateur, après un bug réel constaté en
    # direct : "certaines cases noires initiales disparaissent... il ne
    # faut toucher qu'aux cases noires ajoutées [ce palier], pas à celles
    # présentes avant de commencer cette phase." Root cause : la protection
    # ci-dessus (les deux boucles précédentes) ne se fie qu'à `assignment`
    # (le résultat final de la recherche CSP de CETTE tentative précise)
    # pour décider quels mots "survivent" — mais le « nettoyage curatif »
    # (voir `_remove_a_crossing_word`, appelé depuis `_prefill_unfillable_
    # slots`) peut retirer un mot de `locked_letters` *à l'intérieur même*
    # du worker, avant que la recherche ne démarre — un mot pourtant déjà
    # confirmé depuis un palier précédent, présent dans `carry_locked_
    # letters` (la copie du parent, jamais mutée par le worker séparé — voir
    # plus bas), mais absent du worker's own `locked_letters` copy une fois
    # nettoyage curatif passé par là. Si la recherche CSP échoue ensuite à
    # réattribuer ce même emplacement (`assignment[i]` reste `None`), ses
    # cases-frontière — qui faisaient pourtant déjà partie du motif *avant*
    # que ce palier ne commence, sans aucun rapport avec le nettoyage
    # curatif de cette tentative précise — perdaient toute protection et se
    # retrouvaient rouvertes, comme si elles avaient été ajoutées puis
    # échouées ce palier-ci. Reproduit en direct : un diagnostic dédié,
    # comparant les cases noires de l'aperçu "pattern" (motif d'entrée de
    # palier) à celles de l'aperçu "pattern_generated" (motif produit par
    # CETTE tentative), a bien confirmé des cases présentes "avant"
    # totalement absentes "après" pour plusieurs tentatives/paliers réels.
    # `seed_grid` (`None` par défaut — tout appelant existant avant ce
    # correctif, si jamais il y en avait un sans ce paramètre, n'est pas
    # affecté) est le motif d'ENTRÉE de la tentative dont `grid`/`assignment`
    # sont le résultat — n'importe quelle case déjà noire dedans est protégée
    # inconditionnellement ici, indépendamment de la survie ou non d'un mot
    # dans `assignment` : elle n'a, par construction, jamais pu être
    # "ajoutée sans succès" par CE palier, puisqu'elle existait déjà avant
    # qu'il ne commence.
    if seed_grid is not None:
        for r in range(rows):
            for c in range(cols):
                if seed_grid[r][c] == BLACK:
                    protected_black_cells.add((r, c))

    new_grid = [row[:] for row in grid]
    for r in range(rows):
        for c in range(cols):
            if new_grid[r][c] == BLACK and (r, c) not in protected_black_cells:
                if _fully_surrounded_by_black(r, c):
                    continue
                new_grid[r][c] = WHITE

    return new_grid, confirmed


# Score utilisé pour choisir la meilleure grille nettoyée parmi plusieurs
# candidates — sommme des carrés des longueurs des mots réellement "en
# place" après nettoyage (toutes leurs cases figurent dans `cand_confirmed`).
# Hissé au niveau du module (auparavant une fermeture locale, propre au seul
# nettoyage complet, `else:` dans `generate_grid`) à la demande explicite de
# l'utilisateur, une fois la même logique nécessaire aussi pour la reprise
# "telle quelle" (voir `_clean_continue_candidate`/`_continue_seed_pool` plus
# bas) — favorise quelques mots longs plutôt que beaucoup de mots courts pour
# le même total de lettres, la même formule déjà utilisée pour départager les
# tentatives parallèles réussies dans `generate_grid`.
def _words_in_place_score(cand_slots, cand_confirmed):
    return sum(
        len(cells) ** 2 for cells in cand_slots
        if all(cell in cand_confirmed for cell in cells)
    )


# Départage `_words_in_place_score` à égalité — le nombre de cases noires du
# candidat, à la demande explicite de l'utilisateur, après un vrai blocage
# constaté en direct sur une grande grille très majoritairement verrouillée
# (voir CLAUDE.md pour l'historique complet). Également hissé au niveau du
# module pour la même raison que `_words_in_place_score` ci-dessus.
def _candidate_black_count(cand_seed):
    return sum(row.count(BLACK) for row in cand_seed)


# Trie une liste de candidats nettoyés par (`_words_in_place_score`,
# `_candidate_black_count`) décroissant — chaque candidat est un tuple dont
# les 3 premiers éléments sont `(seed_grid, confirmed, slots)`, dans cet
# ordre précis (les éléments suivants, s'il y en a, ne sont jamais lus ici —
# voir `_clean_continue_candidate` pour un exemple à 5 éléments).
def _sorted_by_score(cleaned_candidates):
    return sorted(
        cleaned_candidates,
        key=lambda sc: (
            _words_in_place_score(sc[2], sc[1]),
            _candidate_black_count(sc[0]),
        ),
        reverse=True,
    )


# Réduit une liste déjà triée (la meilleure d'abord) au vivier transmis au
# prochain palier, en éliminant les `FULL_RESET_ATTEMPT_COUNT` moins bonnes —
# ce nombre éliminé correspond exactement au nombre de tentatives que le
# prochain palier réservera de toute façon à un nouveau départ complètement
# vierge (voir `reset_count` dans `generate_grid`), les grilles survivantes
# remplissant alors, une par une, très exactement le reste des places du
# prochain palier. `max(1, ...)` : ne jamais vider entièrement le vivier,
# même si `FULL_RESET_ATTEMPT_COUNT` dépasse le nombre de candidats
# disponibles — il reste toujours au moins la meilleure grille elle-même.
# `extract` isole, de chaque tuple candidat, exactement ce dont le prochain
# palier a besoin pour relancer une tentative à partir de cette entrée —
# `(seed_grid, locked_letters)` par défaut (le nettoyage complet, motif
# neuf), `(seed_grid, preseed_assignment, excluded_slots)` pour la reprise
# "telle quelle" (voir `_continue_seed_pool`).
def _seed_pool(sorted_candidates, extract=lambda sc: (sc[0], sc[1])):
    keep = max(1, len(sorted_candidates) - FULL_RESET_ATTEMPT_COUNT)
    return [extract(sc) for sc in sorted_candidates[:keep]]


# Nettoie UNE tentative individuelle d'un palier "reprise telle quelle" (voir
# generate_grid, `if still_has_hope:`) — mêmes étapes que `_clean_blocked_
# slots` (retrait des mots croisant un emplacement impossible, avec son
# alternative 1/10 de case noire), appliquées ici à chaque tentative
# distincte de ce palier plutôt qu'à la seule "meilleure" — à la demande
# explicite de l'utilisateur : "Quand il n'y a pas de déclenchement d'un
# nettoyage complet, chaque process doit repartir à l'étape suivante avec sa
# grille partiellement nettoyée (sauf le pourcentage de grilles entièrement
# neuves)" — le même principe déjà en place pour le nettoyage complet (voir
# `_clean_all_candidates`, dans `generate_grid`) désormais étendu à la
# reprise "telle quelle", jusque-là seule à ne conserver qu'une seule grille
# (`selected_grid`/`selected_diag`, la "meilleure" au sens de `failed_pairs`)
# pour tous les workers non réinitialisés du palier suivant.
#
# Retourne un tuple à 5 éléments — `(cand_seed_grid, cand_confirmed,
# cand_slots, cand_preseed_assignment, cand_excluded_slots)` — les 3 premiers
# dans le même ordre que les candidats du nettoyage complet (compatibles avec
# `_words_in_place_score`/`_sorted_by_score`), les 2 derniers la forme
# attendue par `_pattern_continue` (`cand_seed_grid` doublé, jamais répété
# dans le tuple).
#
# Si `_clean_blocked_slots` ajoute une case noire (son alternative 1/10), la
# numérotation des emplacements change — même remède déjà utilisé pour la
# seule grille gagnante avant cette fonctionnalité (voir l'historique complet
# dans CLAUDE.md, "même piège d'indices déjà rencontré... pour le mécanisme
# de verrou à une case, depuis retiré") : reconstruire `cand_slots`/
# `cand_preseed_assignment`/`cand_excluded_slots` depuis un `extract_slots`
# frais sur le motif réellement mis à jour, en s'appuyant sur `confirmed`
# (indexé par case, jamais par indice d'emplacement, donc immunisé contre ce
# décalage) plutôt que sur les anciens indices.
def _clean_continue_candidate(cand_grid, cand_diag, rows, cols, index, rng):
    cand_slots = extract_slots(cand_grid, rows, cols)
    cleaned_assignment, confirmed, new_black_cells = _clean_blocked_slots(
        cand_slots, cand_diag["assignment"], cand_diag["impossible_slots"],
        index=index, rng=rng, grid=cand_grid, rows=rows, cols=cols,
    )
    if new_black_cells:
        cand_seed_grid = [row[:] for row in cand_grid]
        for (br, bc) in new_black_cells:
            cand_seed_grid[br][bc] = BLACK
        new_slots = extract_slots(cand_seed_grid, rows, cols)
        cand_preseed_assignment = [
            "".join(confirmed[cell] for cell in cells)
            if all(cell in confirmed for cell in cells) else None
            for cells in new_slots
        ]
        old_impossible_cell_tuples = {
            tuple(cand_slots[i]) for i in cand_diag["impossible_slots"]
        }
        cand_excluded_slots = {
            j for j, cells in enumerate(new_slots)
            if tuple(cells) in old_impossible_cell_tuples
        }
        return cand_seed_grid, confirmed, new_slots, cand_preseed_assignment, cand_excluded_slots
    cand_excluded_slots = set(cand_diag["impossible_slots"])
    return cand_grid, confirmed, cand_slots, cleaned_assignment, cand_excluded_slots


# Extrait, d'une liste déjà triée de candidats `_clean_continue_candidate`
# (5 éléments), le vivier transmis au prochain palier "reprise telle
# quelle" — `(seed_grid, preseed_assignment, excluded_slots)` par entrée,
# la forme attendue par `_pattern_continue`. Simple appel à `_seed_pool`
# ci-dessus avec l'extracteur adapté à cette forme à 5 éléments.
def _continue_seed_pool(sorted_candidates):
    return _seed_pool(sorted_candidates, extract=lambda sc: (sc[0], sc[3], sc[4]))


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
# N'est plus réellement transmis NULLE PART aujourd'hui — ni à
# `_pattern_attempt` (motif neuf), ni à `_pattern_continue` (reprise
# "telle quelle") — les deux transmettent toujours `None` à `try_fill`
# plutôt que ce global. Historique complet, dans l'ordre :
#
# D'abord désactivé spécifiquement pour `_pattern_attempt`, un vrai bug
# trouvé en direct avant tout déploiement, pas seulement raisonné : les
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
#
# `_pattern_continue`, à l'époque, faisait exactement l'inverse par
# construction : toutes ses tentatives parallèles partageaient
# RIGOUREUSEMENT le même motif et le même verrouillage — seul l'ordre
# d'exploration différait — donc la conclusion d'une tentative sur ce motif
# partagé restait pertinente pour les autres, et le signal restait
# transmis là.
#
# Ce n'est plus vrai depuis `carry_seed_pool_continue` (voir
# `generate_grid`), à la demande explicite de l'utilisateur ("chaque
# process doit repartir à l'étape suivante avec sa grille partiellement
# nettoyée") : deux tentatives parallèles d'un même palier "reprise telle
# quelle" peuvent désormais recevoir des entrées DIFFÉRENTES du vivier (ou
# même un motif entièrement neuf via `_pattern_attempt` pour les
# tentatives réinitialisées, voir `FULL_RESET_ATTEMPT_COUNT`) — exactement
# la même contamination entre motifs indépendants que celle qui a motivé
# de désactiver ce signal pour `_pattern_attempt` s'applique désormais
# aussi ici, alors désactivé de la même façon, préventivement, avant même
# qu'un échec en direct ne le confirme sur cette exacte grille de
# référence (voir `_pattern_continue`'s own docstring/call site).
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
# `multiprocessing.Queue` sur laquelle chaque worker publie, en temps réel,
# chaque nouveau record de `Filler.best_assignment` atteint pendant SA
# propre recherche — pas seulement son état final — à la demande explicite
# de l'utilisateur : "Il ne faut pas supprimer les 70% des tentatives
# restantes, mais seulement les interrompre... Il faut conserver les 6
# meilleures grilles échouées des N process trouvées à n'importe quel
# moment des N recherches", précisé ensuite : "Chaque process suit son
# meilleur état, et transmet au process parent l'information que ce
# meilleur état a changé. Le process parent garde les 6 meilleurs états,
# de tous les états dont il a été informé par les N process." Même
# contrainte technique que les autres globals ci-dessus (passé une seule
# fois par worker via l'initializer du pool, jamais en argument de tâche
# soumise). Le volume reste borné : `best_assigned_count` ne peut
# progresser que d'une unité à la fois et ne dépasse jamais le nombre
# d'emplacements de la grille (~50-60 en pratique), donc au plus
# ~50-60 publications par worker et par palier, quel que soit le nombre
# réel d'appels à `_backtrack` (potentiellement des centaines de
# milliers) — voir `Filler._backtrack` pour le point d'appel exact.
_worker_best_state_queue = None


def _init_worker(index, cancel_event=None, batch_abandoned_event=None, attempt_done_event=None,
                  best_state_queue=None):
    global _worker_index, _worker_cancel_event, _worker_batch_abandoned_event, \
        _worker_attempt_done_event, _worker_best_state_queue
    _worker_index = index
    _worker_cancel_event = cancel_event
    _worker_batch_abandoned_event = batch_abandoned_event
    _worker_attempt_done_event = attempt_done_event
    _worker_best_state_queue = best_state_queue


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
    # Avant même de calculer preseed_assignment ou le sondage statistique
    # des graines plus bas, à la demande explicite de l'utilisateur : "quand
    # un emplacement valide ne possède plus qu'une seule possibilité de
    # mot, forcer les lettres restantes pour placer ce mot." Appelé
    # inconditionnellement (pas seulement `if locked_letters:`) — même sans
    # aucune lettre déjà connue au départ, une longueur dont le dictionnaire
    # n'a qu'un seul mot en tout (un cas réel de ce projet, voir
    # `available_lengths`/`PREFILL_MIN_WORD_COUNT` plus haut) est déjà, en
    # elle-même, une "seule possibilité" à forcer. `locked_letters or {}` :
    # `_force_single_candidate_slots` renvoie toujours un dict (jamais
    # `None`), donc `locked_letters` devient ici un dict à coup sûr — les
    # vérifications `if locked_letters:` plus bas continuent de fonctionner
    # à l'identique (un dict vide reste "faux"), aucune régression pour le
    # cas où rien n'a pu être déduit.
    slots = extract_slots(grid, rows, cols)
    locked_letters = _force_single_candidate_slots(slots, _worker_index, locked_letters or {})

    preseed_assignment = None
    locked_impossible_slots = set()
    if locked_letters:
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
        excluded_slots=locked_impossible_slots, known_letters=locked_letters,
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
                       attempt_done_event=_worker_attempt_done_event,
                       locked_letters=locked_letters,
                       best_state_queue=_worker_best_state_queue,
                       attempt_id=seed)
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
    _pattern_attempt — `seed_grid`/`preseed_assignment`/`excluded_slots`
    restent, pour UN appel donné, rigoureusement identiques d'un appel à
    l'autre de `Filler`/`try_fill` à l'intérieur de cette même recherche
    (rien de nouveau à générer une fois cette tentative lancée), seul
    l'ordre d'exploration diffère (sondage statistique `sample_letter_
    biases`, tri/tirage des mots candidats dans `_backtrack`) : suffisant
    pour que plusieurs tentatives parallèles, parties du même point,
    atteignent des états d'avancement différents.

    Ceci ne veut plus dire, depuis que le vivier `carry_seed_pool_continue`
    existe (voir `generate_grid`), que TOUTES les tentatives parallèles d'un
    même palier "reprise telle quelle" reçoivent nécessairement le même
    triplet `(seed_grid, preseed_assignment, excluded_slots)` — à la
    demande explicite de l'utilisateur ("chaque process doit repartir à
    l'étape suivante avec sa grille partiellement nettoyée"), le parent peut
    désormais dispatcher une entrée différente du vivier à chaque tentative
    non réinitialisée du même palier ; seule une tentative *individuelle*
    (un seul appel à cette fonction) garde un point de départ fixe pour
    elle-même.

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
    # Lettres déjà connues avec certitude à ce stade (voir `known_letters`
    # dans la docstring de `sample_letter_biases`) : tout emplacement déjà
    # entièrement rempli par `preseed_assignment` — verrouillé tel quel,
    # jamais remis en question par cette recherche (voir plus haut). Un
    # second appel à `extract_slots` sur le même motif noir/blanc (déjà
    # recalculé de toute façon par `try_fill` juste en dessous) — un calcul
    # bon marché, pas la peine de le faire remonter par un paramètre
    # supplémentaire juste pour l'éviter ici.
    slots = extract_slots(seed_grid, rows, cols)
    known_letters = {
        cell: letter
        for cells, word in zip(slots, preseed_assignment)
        if word is not None
        for cell, letter in zip(cells, word)
    }
    # Avant le sondage statistique des graines, à la demande explicite de
    # l'utilisateur (voir _force_single_candidate_slots) : force les
    # emplacements dont les lettres déjà connues ne laissent plus qu'une
    # seule possibilité réelle dans le dictionnaire.
    known_letters = _force_single_candidate_slots(
        slots, _worker_index, known_letters, excluded_slots=excluded_slots,
    )
    # Un emplacement fraîchement entièrement déterminé par la déduction
    # ci-dessus (pas seulement par `preseed_assignment` d'origine) devient
    # lui aussi une véritable affectation, pas seulement un indice
    # statistique — même principe que `_pattern_attempt`'s propre
    # préremplissage : sans cette promotion, `Filler._domain` ne verrait ces
    # lettres que comme un indice (voir `forced_letters` plus bas), jamais
    # comme la certitude qu'elles sont réellement. Revalidé exactement comme
    # `_pattern_attempt` (`_slot_candidate_count(...) > 0`) plutôt que
    # simplement assigné tel quel : un emplacement peut se retrouver
    # entièrement connu par le seul jeu des croisements, sans que
    # `_force_single_candidate_slots` lui-même ait jamais vérifié que cette
    # combinaison précise correspond à un vrai mot pour SA propre longueur
    # (son propre passage l'aurait alors simplement ignoré comme "déjà
    # connu", sans le valider) — laissé à `None` si invalide : `try_fill`
    # le retrouvera de lui-même comme un domaine vide, exactement comme
    # n'importe quel autre emplacement bloqué. Les emplacements de
    # `excluded_slots` ne sont jamais promus ainsi, par cohérence avec
    # `_force_single_candidate_slots` qui ne les traite déjà jamais.
    preseed_assignment = list(preseed_assignment)
    excluded = excluded_slots or set()
    for i, cells in enumerate(slots):
        if i in excluded or preseed_assignment[i] is not None:
            continue
        if all(cell in known_letters for cell in cells):
            word = "".join(known_letters[cell] for cell in cells)
            if _slot_candidate_count(_worker_index, len(cells), cells, known_letters) > 0:
                preseed_assignment[i] = word
    forced_letters, letter_scores = sample_letter_biases(
        seed_grid, rows, cols, _worker_index, rng, force_fraction=force_letters_fraction,
        excluded_slots=excluded_slots, known_letters=known_letters,
    )
    # Les lettres déjà connues (y compris celles tout juste déduites
    # ci-dessus) l'emportent toujours sur le sondage statistique — même
    # principe que la fusion équivalente dans `_pattern_attempt`. Sans
    # cette fusion, une lettre déduite ici mais dont l'emplacement reste
    # partiellement connu (pas assez pour rejoindre `preseed_assignment`
    # ci-dessus) n'aurait autrement aucun moyen d'atteindre `Filler` comme
    # contrainte réelle.
    forced_letters = {**forced_letters, **known_letters}
    diag = {}
    # `batch_abandoned_event` toujours `None` ici désormais — n'était vrai
    # que tant que TOUTES les tentatives parallèles d'un même palier
    # "reprise telle quelle" partageaient rigoureusement le même
    # `seed_grid`/`preseed_assignment` (voir la docstring de cette fonction,
    # et `_worker_batch_abandoned_event` pour l'historique complet de cette
    # règle). Depuis `carry_seed_pool_continue` (voir `generate_grid`), deux
    # tentatives parallèles du même palier peuvent désormais recevoir des
    # entrées DIFFÉRENTES du vivier — un `_pattern_attempt` (motif neuf,
    # pour les tentatives réinitialisées) mélangé à plusieurs `_pattern_
    # continue` sur des grilles distinctes — donc la conclusion "30 % de MA
    # grille est impossible" d'une tentative ne dit plus rien de fiable sur
    # la grille, potentiellement différente, d'une autre tentative de ce
    # même palier : exactement le même raisonnement, appliqué au même
    # global, qui a déjà motivé de le désactiver pour `_pattern_attempt`
    # (voir juste au-dessus) — désactivé ici aussi pour la même raison,
    # avant même qu'un vrai échec en direct ne le confirme.
    result = try_fill(seed_grid, rows, cols, _worker_index, rng, deadline_checks=deadline_checks,
                       diagnostics=diag,
                       forced_letters=forced_letters, letter_scores=letter_scores,
                       preseed_assignment=preseed_assignment, excluded_slots=excluded_slots,
                       cancel_event=_worker_cancel_event,
                       batch_abandoned_event=None,
                       attempt_done_event=_worker_attempt_done_event,
                       locked_letters=known_letters,
                       best_state_queue=_worker_best_state_queue,
                       attempt_id=seed)
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
    réglable depuis l'interface web (un sélecteur "Taux noir", un entier
    libre 0-100, 14 % par défaut — voir `GenerateRequest.black_enrichment_
    percent` dans backend/app.py). Transmis tel quel à `_pattern_attempt`
    (jamais à `_pattern_continue`, qui ne rappelle jamais `make_pattern` —
    un palier de reprise "telle-quelle" ne peut par construction ajouter
    aucune case noire, voir _pattern_continue's propre docstring), donc
    uniquement pertinent pour un palier qui part d'une grille vierge ou
    d'un nettoyage (`_build_retry_seed`).

    A separate, unrelated per-cycle single-cell lock
    (`_impossible_cell_groups`/`_lock_one_impossible_cell`, which used to
    add one extra black cell on every palier's own impossible/blockage
    slot(s), including "reprise telle-quelle" ones) was removed entirely
    in this same session, at the user's explicit request — but this
    `black_enrichment_fraction` mechanism itself was never meant to be
    removed, only that separate lock; a first attempt mistakenly removed
    both together and was corrected once the user clarified the scope.
    See CLAUDE.md for the full history of both.

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
    by_length, accents, canonicals, frequencies = load_wordlist(
        wordlist_path, mw, require_gloss=(difficulty == "easy")
    )
    index = build_index(by_length, frequencies)
    # Précalculé une seule fois (pas par palier) — mêmes longueurs pour
    # toute la génération, `index` ne change jamais. Reproduit exactement
    # le calcul propre à chaque worker dans `_pattern_attempt` (voir sa
    # propre docstring), mais côté processus PARENT cette fois — utilisé
    # uniquement par l'aperçu "cases noires posées" précoce ci-dessous,
    # jamais par la recherche CSP elle-même (qui reste toujours calculée
    # dans les processus workers, avec leur propre `_worker_index`).
    available_lengths_preview = {
        length for length, data in index.items()
        if len(data["words"]) >= PREFILL_MIN_WORD_COUNT
    }
    # Logged once per request, not per attempt: the CSP's failure mode
    # (below) can't be told apart from a genuinely empty word list for
    # some length without this — a `require_gloss`/`max_words` combination
    # that shrinks a length to 0 words fails every pattern instantly, in
    # a way that looks identical in the per-attempt log to ordinary bad
    # luck unless this baseline is on record too.
    progress("wordlist_loaded", word_count=sum(len(w) for w in by_length.values()),
             length_counts=dict(sorted((length, len(words)) for length, words in by_length.items())))

    rows, cols = height, width
    # Même résolution que `try_fill`'s propre `None`-fallback (largeur ×
    # hauteur × 2000), calculée ici une seule fois — plutôt que dans chaque
    # worker séparément — pour que le rapport de progression du budget
    # ci-dessous (`BUDGET_PROGRESS_REPORT_INTERVAL_S`) sache contre quelle
    # valeur comparer `checks` sans avoir à la redemander à un worker.
    resolved_deadline_checks = (
        deadline_checks if deadline_checks is not None else rows * cols * 2000
    )
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
    # Vivier de grilles nettoyées candidates pour le prochain palier « motif
    # neuf » — une par tentative parallèle du palier qui vient d'échouer
    # (jusqu'à `PARALLEL_ATTEMPTS`, moins les pires éliminées, voir plus
    # bas), pas une seule grille reprise par tous les workers non
    # réinitialisés — à la demande explicite de l'utilisateur : "on garde
    # la meilleure grille de tous les process, soit N grilles pour N
    # process, et on relance toutes les meilleures grilles après nettoyage
    # en ayant éliminé les moins bonnes en fonction du nombre de nouvelles
    # grilles paramétrées." `carry_seed_grid`/`carry_locked_letters`
    # ci-dessus restent la MEILLEURE grille de ce vivier (toujours en tête
    # une fois trié) — utilisés tels quels partout ailleurs dans cette
    # fonction (aperçus autres que le tout prochain palier, `resume_state`,
    # détection de point fixe...) exactement comme avant cette
    # fonctionnalité ; seul le tout prochain palier « motif neuf » puise
    # dans ce vivier pour diversifier son propre lancement plutôt que de
    # reprendre `carry_seed_grid` identique pour tous ses workers non
    # réinitialisés. `None` tant qu'aucun nettoyage complet n'a encore eu
    # lieu (voir plus bas, où seule la branche de nettoyage le renseigne).
    carry_seed_pool = None
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
    # Vivier de grilles nettoyées candidates pour le prochain palier de
    # reprise "telle quelle" — le pendant de `carry_seed_pool` ci-dessus,
    # mais pour `_pattern_continue` au lieu de `_pattern_attempt` : une
    # entrée `(seed_grid, preseed_assignment, excluded_slots)` par tentative
    # distincte du palier qui vient de se terminer, pas une seule grille
    # reprise par tous les workers non réinitialisés — à la demande explicite
    # de l'utilisateur : "Quand il n'y a pas de déclenchement d'un nettoyage
    # complet, chaque process doit repartir à l'étape suivante avec sa
    # grille partiellement nettoyée (sauf le pourcentage de grilles
    # entièrement neuves)." Voir `_clean_continue_candidate`/
    # `_continue_seed_pool` (niveau module) et `if still_has_hope:` plus bas
    # pour la construction ; `carry_seed_grid`/`carry_preseed_assignment`/
    # `carry_excluded_slots` ci-dessus restent la MEILLEURE entrée de ce
    # vivier (toujours en tête une fois trié) — utilisés tels quels partout
    # ailleurs dans cette fonction (aperçus autres que le tout prochain
    # palier, `resume_state`...) exactement comme avant cette fonctionnalité
    # ; seul le tout prochain palier de reprise "telle quelle" puise dans ce
    # vivier pour diversifier son propre lancement. `None` tant qu'aucun
    # palier "telle quelle" n'a encore eu lieu (voir plus bas, où seule cette
    # branche le renseigne) — jamais transmis par `resume_state` (comme
    # `carry_seed_pool` lui-même), un run repris reconstruit ce vivier
    # normalement dès son premier palier "telle quelle".
    carry_seed_pool_continue = None
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
    # gets its own fresh budget of up to 5 consecutive "reprise
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
    # Mémorisation de l'état (motif + contenu confirmé) obtenu à la fin de
    # chaque NETTOYAGE COMPLET, à la demande explicite de l'utilisateur —
    # voir GRID_REPEAT_INFEASIBLE_THRESHOLD's own docstring pour la
    # demande complète et l'historique des deux régressions mesurées avant
    # d'arriver à cette portée finale (nettoyage seul, jamais "reprise
    # telle quelle"). `last_cycle_end_grid` garde l'état (sous forme
    # hashable, un tuple de tuples produit par `_cycle_start_preview`) du
    # dernier nettoyage ; `same_grid_streak` compte combien de nettoyages
    # CONSÉCUTIFS (aucune "reprise telle quelle" entre-temps ne le
    # remet à zéro ni ne l'incrémente — cette branche n'y touche jamais)
    # ont reproduit ce même état.
    last_cycle_end_grid = None
    same_grid_streak = 0
    # True right after a palier that did a full cleanup ("nettoyage
    # complet", the `else:` branch below), False right after one that did
    # "reprise telle quelle" instead — at the user's explicit request (see
    # FULL_RESET_ATTEMPT_COUNT's own docstring): used only once, by the
    # very next palier's own worker-submission code below, to decide
    # whether a handful of that palier's PARALLEL_ATTEMPTS workers should
    # start from a blank grid instead of the just-cleaned carry_seed_grid/
    # carry_locked_letters every other worker gets. Always overwritten
    # again at the end of every single palier (whichever branch runs), so
    # this never lingers past the one palier it's meant for.
    just_cleaned = False
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
    # "Chaque process suit son meilleur état, et transmet au process parent
    # l'information que ce meilleur état a changé. Le process parent garde
    # les 6 meilleurs états, de tous les états dont il a été informé par
    # les N process" — décision explicite de l'utilisateur. Une
    # `multiprocessing.Queue` (pas un `Event` : il faut transporter des
    # données, pas juste un signal) transmise via l'initializer du pool,
    # pour la même raison technique que les trois `Event` ci-dessus — un
    # objet `multiprocessing` passé comme simple argument de tâche à
    # `executor.submit(...)` provoque une `RuntimeError` sur macOS
    # ("spawn"). Un seul objet pour toute la génération, jamais recréé
    # palier après palier.
    best_state_queue = multiprocessing.Queue()
    # Un vrai interblocage a été constaté en direct (temps CPU des workers
    # figé d'une lecture à l'autre, l'utilisateur observant lui-même « il
    # n'y a plus que 2 process qui tourne ») avec une première version qui
    # ne drainait `best_state_queue` qu'une seule fois par palier, juste
    # après que `as_completed` a récupéré tous les futures : le tube
    # (« pipe ») sous-jacent d'une `multiprocessing.Queue` a une capacité
    # bornée côté OS — si assez de messages s'accumulent sans jamais être
    # lus pendant qu'un worker est encore profondément dans sa recherche
    # (chaque tentative peut publier jusqu'à ~50-60 fois, voir
    # _worker_best_state_queue), son propre `put()` finit par bloquer tant
    # que personne ne lit le tube ; mais personne ne le lit tant que TOUS
    # les workers de ce palier n'ont pas terminé — et ce worker-là ne peut
    # justement jamais terminer tant que son propre `put()` reste bloqué. Un
    # classique interblocage producteur/consommateur, pas un problème de
    # détection du seuil des 30 % (`attempt_done_event`) ni de workers qui
    # ne s'arrêteraient pas correctement.
    #
    # Corrigé en drainant la file en continu, dans un thread dédié
    # (`threading`, pas `multiprocessing` — ce thread tourne dans le
    # processus PARENT, où GIL ou pas, une boucle qui ne fait qu'attendre
    # sur `Queue.get(timeout=...)` puis `list.append(...)` ne se dispute
    # jamais le GIL avec quoi que ce soit de coûteux) démarré une seule fois
    # pour toute la génération, jamais recréé palier après palier — tant que
    # ce thread tourne, le tube ne peut plus jamais s'accumuler assez pour
    # bloquer un `put()`. `_best_state_buffer`/`_best_state_buffer_lock`
    # accumulent chaque message reçu ; le code de chaque palier (plus bas)
    # n'interagit plus jamais directement avec `best_state_queue` — il vide
    # `_best_state_buffer` sous verrou à la place, ce qui revient exactement
    # au même du point de vue de ce qu'il reçoit, sans jamais risquer de
    # lire directement dans la file pendant qu'un worker y écrit encore.
    best_state_buffer = []
    best_state_buffer_lock = threading.Lock()
    stop_best_state_drain = threading.Event()
    # Horodatage (monotonic) de la dernière publication du pourcentage de
    # budget consommé — voir BUDGET_PROGRESS_REPORT_INTERVAL_S. Une liste
    # à un seul élément (pas une simple variable) uniquement pour rester
    # mutable depuis l'intérieur de la boucle ci-dessous sans `nonlocal`.
    last_budget_progress_report = [0.0]

    def _drain_best_state_queue_continuously():
        while not stop_best_state_drain.is_set():
            try:
                msg = best_state_queue.get(timeout=0.1)
            except queue.Empty:
                msg = None
            if msg is not None:
                with best_state_buffer_lock:
                    best_state_buffer.append(msg)
            # Vérifié à chaque itération de cette boucle (~10 fois par
            # seconde, voir le timeout de get() ci-dessus), pas seulement
            # quand un message vient d'arriver — sinon, un palier dont
            # aucune tentative n'améliore plus son record pendant un long
            # moment ne republierait plus jamais rien du tout, alors que
            # `deadline_checks` continue, lui, réellement de se consommer
            # en arrière-plan dans les workers.
            now = time.monotonic()
            if now - last_budget_progress_report[0] >= BUDGET_PROGRESS_REPORT_INTERVAL_S:
                last_budget_progress_report[0] = now
                with best_state_buffer_lock:
                    checks_seen = [m["checks"] for m in best_state_buffer]
                if checks_seen:
                    percent = min(
                        100, round(100 * max(checks_seen) / resolved_deadline_checks)
                    )
                    progress("budget_progress", percent=percent)

    # Démarré avant même la création du pool, `daemon=True` : ce thread ne
    # doit jamais empêcher le processus de se terminer, y compris sur un
    # chemin de sortie anticipé (`GenerationCancelled`, levée depuis
    # l'intérieur de la boucle ci-dessous) qui ne passerait pas par l'arrêt
    # explicite tout en bas de cette fonction — dans ce cas rare, le thread
    # reste simplement inactif (bloqué sur `get(timeout=0.1)`, sans rien à
    # lire) jusqu'à la fin du processus, un coût négligeable, plutôt qu'un
    # `try`/`finally` englobant toute la boucle des paliers (des centaines
    # de lignes) qui aurait exigé de la réindenter en bloc.
    best_state_drain_thread = threading.Thread(
        target=_drain_best_state_queue_continuously, daemon=True
    )
    best_state_drain_thread.start()
    with concurrent.futures.ProcessPoolExecutor(
        max_workers=PARALLEL_ATTEMPTS, initializer=_init_worker,
        initargs=(index, cancel_event, batch_abandoned_event, attempt_done_event, best_state_queue)
    ) as executor:
        for attempt in range(attempts):
            if cancel_event is not None and cancel_event.is_set():
                raise GenerationCancelled()
            batch_abandoned_event.clear()
            attempt_done_event.clear()
            # Vivier des grilles de départ candidates pour ce palier (voir
            # `carry_seed_pool`, sa propre définition plus haut) — calculé
            # ici, avant même de savoir si ce palier sera une reprise
            # "telle quelle" ou un motif neuf, pour que cet aperçu de
            # DÉBUT de cycle puisse déjà en tenir compte, pas seulement
            # celui de "cases noires posées" plus bas (qui, lui, le
            # calculait déjà). Repli sur `[(carry_seed_grid, carry_locked_
            # letters)]` (comportement d'avant cette fonctionnalité) tant
            # qu'aucun nettoyage complet n'a encore renseigné `carry_seed_
            # pool` — voir sa propre définition pour le détail complet.
            pool = carry_seed_pool if carry_seed_pool else [(carry_seed_grid, carry_locked_letters)]
            if carry_preseed_assignment is not None:
                # Un aperçu par grille du vivier (`carry_seed_pool_
                # continue`), pas un seul, à la demande explicite de
                # l'utilisateur (voir la définition de `carry_seed_pool_
                # continue`) — même principe que le "motif neuf" ci-dessous,
                # désormais aussi vrai pour la reprise "telle quelle" :
                # chaque tentative distincte du palier précédent a pu être
                # nettoyée différemment (mots retirés différents, parfois une
                # case noire ajoutée), donc le prochain palier peut
                # réellement démarrer sur plusieurs motifs/affectations
                # distincts, pas un seul comme avant cette fonctionnalité.
                # Repli sur `[(carry_seed_grid, carry_preseed_assignment,
                # carry_excluded_slots)]` (comportement d'avant cette
                # fonctionnalité) tant qu'aucun palier "telle quelle" n'a
                # encore renseigné `carry_seed_pool_continue`. Les
                # tentatives réinitialisées de ce palier (`reset_count` plus
                # bas, un motif entièrement neuf) ne sont volontairement pas
                # préviewées séparément ici — même convention que la branche
                # "motif neuf" ci-dessous, dont le propre vivier ne les
                # préviewe pas non plus.
                continue_pool = carry_seed_pool_continue if carry_seed_pool_continue else [
                    (carry_seed_grid, carry_preseed_assignment, carry_excluded_slots)
                ]
                seen_continue_patterns = set()
                cycle_start_examples = []
                for pool_grid, pool_preseed, _pool_excluded in continue_pool:
                    pattern_key = tuple(tuple(row) for row in pool_grid)
                    if pattern_key in seen_continue_patterns:
                        continue
                    seen_continue_patterns.add(pattern_key)
                    start_grid, start_locked_cells = _cycle_start_preview(
                        rows, cols, pool_grid, None, pool_preseed,
                    )
                    cycle_start_examples.append({
                        "example_grid": start_grid,
                        "impossible_cells": [],
                        "forced_cells": [],
                        "locked_cells": start_locked_cells,
                        "low_candidate_cells": [],
                        "noise_cells": [],
                    })
            else:
                # Un aperçu par grille du vivier, pas un seul, à la demande
                # explicite de l'utilisateur : "Les extraits 'Génération du
                # motif de cases noires' ne montrent qu'une seule grille.
                # Il devrait maintenant y en avoir N pour N process." —
                # même principe et même dédoublonnage (par motif noir/blanc
                # réel, `pool_grid`, jamais l'état déjà recouvert de
                # lettres) que l'aperçu "cases noires posées" plus bas, qui
                # avait déjà cette diversité ; seul cet aperçu de tout début
                # de cycle en manquait encore. Cases sous le seuil de
                # remplissage (< PREFILL_LOCKED_MIN_WORD_COUNT candidats, à
                # la demande explicite de l'utilisateur — voir _low_
                # candidate_slot_cells) calculées désormais pour chaque
                # grille du vivier individuellement, sur ses propres
                # lettres verrouillées — jamais celles d'une autre entrée du
                # vivier. `pool_grid is None` seulement pour le tout premier
                # palier d'une génération (rien encore verrouillé nulle
                # part) — une seule grille vierge dans le vivier dans ce
                # cas, donc rien à dédupliquer ni à évaluer.
                seen_cycle_start_patterns = set()
                cycle_start_examples = []
                for pool_grid, pool_locked in pool:
                    if pool_grid is not None:
                        pattern_key = tuple(tuple(row) for row in pool_grid)
                        if pattern_key in seen_cycle_start_patterns:
                            continue
                        seen_cycle_start_patterns.add(pattern_key)
                    start_grid, start_locked_cells = _cycle_start_preview(
                        rows, cols, pool_grid, pool_locked, None,
                    )
                    low_candidate_cells = (
                        _low_candidate_slot_cells(pool_grid, rows, cols, index, pool_locked)
                        if pool_grid is not None else []
                    )
                    # Cases sans aucune proposition réellement jouable
                    # (`NOISE_FREQUENCY_THRESHOLD`), à la demande explicite
                    # de l'utilisateur — même calcul par grille du vivier
                    # individuelle, même portée (jamais pour un palier de
                    # reprise "telle quelle") que low_candidate_cells
                    # ci-dessus, voir _noise_slot_cells.
                    noise_cells = (
                        _noise_slot_cells(pool_grid, rows, cols, index, pool_locked)
                        if pool_grid is not None else []
                    )
                    cycle_start_examples.append({
                        "example_grid": start_grid,
                        "impossible_cells": [],
                        "forced_cells": [],
                        "locked_cells": start_locked_cells,
                        "low_candidate_cells": low_candidate_cells,
                        "noise_cells": noise_cells,
                    })
            progress("pattern", attempt=attempt + 1, attempts=attempts, parallel=PARALLEL_ATTEMPTS,
                     total_attempts=total_attempts_tried,
                     examples=cycle_start_examples)
            seeds = [rng.randrange(2**31) for _ in range(PARALLEL_ATTEMPTS)]
            if carry_preseed_assignment is not None:
                # `reset_count` tentatives de ce palier "reprise telle
                # quelle" repartent d'un motif entièrement neuf
                # (`_pattern_attempt`, seed_grid=None — jamais `_pattern_
                # continue`, puisqu'il n'y a alors ni motif ni verrouillage
                # antérieur à reprendre) au lieu de la reprise individuelle
                # sur leur propre entrée du vivier — à la demande explicite
                # de l'utilisateur : "chaque process doit repartir à l'étape
                # suivante avec sa grille partiellement nettoyée (sauf le
                # pourcentage de grilles entièrement neuves)." Contrairement
                # au "motif neuf" ci-dessous (`reset_count` conditionné par
                # `just_cleaned`, seulement juste après un nettoyage
                # complet), s'applique ici inconditionnellement à CHAQUE
                # palier "reprise telle quelle" — il n'y a pas d'équivalent
                # de `just_cleaned` à distinguer, puisqu'un tel palier est
                # déjà, par construction, toujours la suite d'un état
                # précédent (jamais un tout premier palier, qui part
                # toujours de `carry_seed_grid is None`, donc de la branche
                # "motif neuf" ci-dessous). Chaque tentative non
                # réinitialisée (`i >= reset_count`) reçoit sa propre entrée
                # du vivier (`continue_pool`, déjà calculé plus haut pour
                # l'aperçu de ce même palier) — un simple parcours cyclique
                # (`% len(continue_pool)`) répartit les entrées disponibles
                # sur les places non réinitialisées, comme pour le "motif
                # neuf" ci-dessous.
                reset_count = FULL_RESET_ATTEMPT_COUNT
                futures = []
                for i, s in enumerate(seeds):
                    if i < reset_count:
                        futures.append(executor.submit(
                            _pattern_attempt, rows, cols, ratio, s, force_letters_fraction,
                            None, None,
                            black_enrichment_fraction, deadline_checks,
                        ))
                    else:
                        task_seed_grid, task_preseed_assignment, task_excluded_slots = (
                            continue_pool[(i - reset_count) % len(continue_pool)]
                        )
                        futures.append(executor.submit(
                            _pattern_continue, rows, cols, s, task_seed_grid,
                            task_preseed_assignment, task_excluded_slots,
                            force_letters_fraction, deadline_checks,
                        ))
            else:
                # A fraction of this palier's own workers start from a
                # totally blank grid instead of the just-cleaned
                # carry_seed_grid/carry_locked_letters every other worker
                # gets, right after a full cleanup — at the user's explicit
                # request, see FULL_RESET_ATTEMPT_COUNT's own docstring.
                # No particular reason to prefer one seed over another for
                # which of them get reset — `seeds` are already
                # independently random, so simply resetting the first
                # `reset_count` of them is as good as any other choice.
                # Never applies right after a "reprise telle quelle"
                # palier (`just_cleaned` is only ever True right after the
                # `else:`/nettoyage branch below) nor on the very first
                # palier of a call with no prior cleanup at all.
                reset_count = FULL_RESET_ATTEMPT_COUNT if just_cleaned else 0
                # `pool` (voir `carry_seed_pool`'s propre définition plus
                # haut) déjà calculé au tout début de ce palier, pour
                # l'aperçu "Génération du motif de cases noires" — réutilisé
                # tel quel ici, jamais recalculé une seconde fois pour les
                # workers non réinitialisés de CE palier ni pour l'aperçu
                # "cases noires posées" juste en dessous.
                # Aperçu "cases noires posées, recherche des mots en
                # cours" publié dès MAINTENANT — avant même de soumettre
                # la moindre tentative parallèle à l'executor, donc bien
                # avant que la recherche CSP (la partie lente) de ce
                # palier ne termine — à la demande explicite de
                # l'utilisateur : "Le Front n'affiche les aperçus qu'après
                # la fin d'un cycle. Les états d'initialisation
                # n'apparaissent pas avant la fin du cycle. Il faut que la
                # stack Back soit proprement alimentée à chaque étape du
                # cycle." Root-causé directement dans le code, pas
                # supposé : le `pattern_generated` existant plus bas
                # (`_cycle_start_preview` sur `failed_pairs`/`best`) n'est
                # calculable qu'une fois TOUTES les tentatives parallèles
                # de ce palier terminées (`concurrent.futures.
                # as_completed`, plus bas) — pour un palier "motif neuf"
                # (celui-ci, pas la reprise "telle quelle" ci-dessus, dont
                # le propre `pattern_generated` coïncide déjà avec l'état
                # de départ du cycle), l'étape "cases noires posées" ne
                # pouvait donc jamais réellement apparaître avant la fin
                # du cycle, quelle que soit la rapidité du Front à
                # l'afficher — le Back lui-même ne l'avait tout simplement
                # pas encore calculée. Reconstruit ici, dans le processus
                # PARENT, avec les mêmes paramètres qu'un worker réel
                # utilisera dans son propre processus séparé plus bas —
                # `make_pattern` étant une fonction pure de ses arguments,
                # appelée deux fois avec le même seed produit le même motif
                # les deux fois, donc jamais de fausse impression de case
                # noire "déplacée" une fois le véritable `pattern_generated`
                # (calculé après coup, voir plus bas) reçu.
                #
                # Une initialisation PAR PROCESS, mais réservée à la toute
                # première initialisation de la génération (`carry_seed_grid
                # is None` — aucun palier précédent n'a encore tourné) — à
                # la demande explicite de l'utilisateur : "la toute première
                # initialisation des cases noires ne prépare qu'une seule
                # grille. Intégrer cette première initialisation au début du
                # cycle, de manière à créer une initialisation par process."
                # Clarifié par l'utilisateur lui-même après une première
                # implémentation qui l'appliquait à *chaque* cycle "motif
                # neuf" (pas seulement le tout premier), provoquant un vrai
                # ralentissement mesuré en direct (jusqu'à +250 % par palier)
                # et, plus grave, un vrai risque de faire échouer une
                # génération qui aurait sinon réussi (le calcul séquentiel
                # supplémentaire dans le processus parent décale le timing
                # réel auquel les tentatives parallèles sont soumises, et ce
                # palier utilise un mécanisme d'interruption sensible à
                # l'ordre réel d'achèvement — `attempt_done_event`/
                # `batch_abandoned_event` — pas seulement à la graine) :
                # "Il ne faut pas changer le budget, juste initialiser N
                # grilles au premier cycle au lieu d'une seule. Les cycles
                # suivants, à partir de 2, reprendront la meilleure grille
                # (sauf 20% de nouvelles grilles)." À partir du 2e palier,
                # `carry_seed_grid` porte déjà le contenu du meilleur essai
                # précédent (ou repart d'une grille vierge pour les
                # `reset_count` tentatives réinitialisées, déjà sa propre
                # source de diversité) — la diversité "une grille par
                # process" n'a donc de sens réel qu'au tout premier palier,
                # où rien ne distingue encore les tentatives entre elles à
                # part leur propre graine.
                if carry_seed_grid is None:
                    # Une par process (jusqu'à PARALLEL_ATTEMPTS), à la
                    # demande explicite de l'utilisateur : "il n'y a jamais
                    # eu 6 grilles par process, mais 1 grille par process
                    # (1 process par processeur)." — même principe ici : un
                    # calcul par tentative sur le point d'être soumise,
                    # dédupliqué par motif noir/blanc réel (deux workers
                    # peuvent légitimement retomber sur le même motif), sans
                    # aucun plafond au-delà de cette déduplication — à la
                    # demande explicite de l'utilisateur ("Afficher toutes
                    # les meilleures grilles dans l'aperçu, pas seulement les
                    # 6 meilleures"), qui retire le plafond `FAILED_ATTEMPT_
                    # EXAMPLES` (6) auparavant appliqué ici — même convention
                    # (déduplication, sans plafond) que celle déjà utilisée
                    # plus bas pour les motifs réellement recherchés
                    # (`failed_unique`).
                    seen_early_patterns = set()
                    early_examples = []
                    for s in seeds:
                        early_pattern = make_pattern(
                            rows, cols, ratio, random.Random(s),
                            available_lengths=available_lengths_preview,
                            seed_grid=None, locked_letters=None,
                            index=index, black_enrichment_fraction=black_enrichment_fraction,
                        )
                        pattern_key = tuple(tuple(row) for row in early_pattern)
                        if pattern_key in seen_early_patterns:
                            continue
                        seen_early_patterns.add(pattern_key)
                        early_pattern_grid, early_pattern_locked = _cycle_start_preview(
                            rows, cols, early_pattern, None, None,
                        )
                        early_examples.append({
                            "example_grid": early_pattern_grid,
                            "impossible_cells": [],
                            "forced_cells": [],
                            "locked_cells": early_pattern_locked,
                        })
                else:
                    # Un aperçu par grille du vivier (pas un seul), à la
                    # demande explicite de l'utilisateur : depuis que le
                    # prochain palier peut réellement démarrer sur plusieurs
                    # motifs distincts (voir `pool` ci-dessus), un aperçu
                    # unique reconstruit à partir de `carry_seed_grid` seul
                    # ne correspondrait plus forcément à ce qu'un worker réel
                    # calculera — exactement la classe de bug déjà rencontrée
                    # plusieurs fois dans ce fichier pour un motif "modèle"
                    # qui finit par diverger de la réalité une fois plusieurs
                    # variantes en jeu (voir CLAUDE.md). Pour chaque entrée du
                    # vivier, reconstruit ici, dans le processus PARENT,
                    # exactement le même motif (mêmes paramètres, même
                    # graine) que le PREMIER worker réel à qui cette entrée
                    # sera effectivement assignée dans `futures` plus bas
                    # (`seeds[reset_count + p]` pour la p-ième entrée du
                    # vivier — toujours un index valide : le vivier ne
                    # contient jamais plus d'entrées que de places non
                    # réinitialisées, voir `_seed_pool`). Même dédoublonnage
                    # par motif réel, sans aucun plafond, que la branche
                    # "tout premier palier" ci-dessus, pas un mécanisme
                    # distinct — seule la source (le vivier, plutôt que
                    # `seeds` sur une grille vierge commune) diffère.
                    seen_pool_patterns = set()
                    early_examples = []
                    for p, (pool_grid, pool_locked) in enumerate(pool):
                        # `min(..., len(seeds) - 1)` : filet de sécurité pour
                        # un cas dégénéré (PARALLEL_ATTEMPTS <= FULL_RESET_
                        # ATTEMPT_COUNT, jamais le cas avec les valeurs par
                        # défaut) où `reset_count + p` déborderait sinon de
                        # `seeds` — jamais atteint en pratique (voir
                        # `_seed_pool`, qui garantit déjà `len(pool) <=
                        # PARALLEL_ATTEMPTS - reset_count` dans le cas normal),
                        # mais un aperçu approximatif reste préférable à un
                        # plantage pur et simple.
                        early_pattern = make_pattern(
                            rows, cols, ratio,
                            random.Random(seeds[min(reset_count + p, len(seeds) - 1)]),
                            available_lengths=available_lengths_preview,
                            seed_grid=pool_grid, locked_letters=pool_locked,
                            index=index, black_enrichment_fraction=black_enrichment_fraction,
                        )
                        pattern_key = tuple(tuple(row) for row in early_pattern)
                        if pattern_key in seen_pool_patterns:
                            continue
                        seen_pool_patterns.add(pattern_key)
                        early_pattern_grid, early_pattern_locked = _cycle_start_preview(
                            rows, cols, early_pattern, pool_locked, None,
                        )
                        early_examples.append({
                            "example_grid": early_pattern_grid,
                            "impossible_cells": [],
                            "forced_cells": [],
                            "locked_cells": early_pattern_locked,
                        })
                progress(
                    "pattern_generated", attempt=attempt + 1, attempts=attempts,
                    total_attempts=total_attempts_tried,
                    examples=early_examples,
                )
                # Chaque worker non réinitialisé (`i >= reset_count`) reçoit
                # sa propre entrée du vivier (`pool`, voir sa définition plus
                # haut), pas systématiquement `carry_seed_grid` — à la
                # demande explicite de l'utilisateur. Un simple parcours
                # cyclique (`% len(pool)`) répartit les entrées disponibles
                # sur les places non réinitialisées ; dans le cas normal
                # (`len(pool) == PARALLEL_ATTEMPTS - reset_count`, garanti
                # par `_seed_pool`), ce cycle ne boucle jamais réellement —
                # chaque place reçoit une entrée distincte, une seule fois.
                # Il ne boucle que si `failed_pairs` avait, exceptionnellement,
                # moins d'entrées que de places à pourvoir (dédoublonnage par
                # contenu, voir son propre commentaire) — dans ce cas précis
                # seulement, une même grille nettoyée peut légitimement se
                # retrouver reprise par plus d'un worker, chacun avec sa
                # propre graine.
                futures = []
                for i, s in enumerate(seeds):
                    if i < reset_count:
                        task_seed_grid, task_locked_letters = None, None
                    else:
                        task_seed_grid, task_locked_letters = pool[(i - reset_count) % len(pool)]
                    futures.append(executor.submit(
                        _pattern_attempt, rows, cols, ratio, s, force_letters_fraction,
                        task_seed_grid, task_locked_letters,
                        black_enrichment_fraction, deadline_checks,
                    ))
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
            successes = [(g, r, d) for g, r, d in outcomes if r is not None]
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
            # Attempts cut short by attempt_done_event (see above) used to
            # be excluded entirely from "which failed attempt is the best
            # one" (failed_unique/failed_pairs below) — reverted at the
            # user's explicit request: "Il ne faut pas supprimer les 70%
            # des tentatives restantes, mais seulement les interrompre.
            # Chacune d'elle porte normalement la mémorisation de sa
            # meilleure grille échouée, qu'il faut prendre en compte." An
            # interrupted worker's own diagnostics (`example_grid`/
            # `assignment`/`impossible_cells`) are never empty placeholders
            # — `try_fill` already builds them from `Filler.best_assignment`
            # (the same high-water-mark snapshot a naturally-concluded
            # failure uses too, see its own docstring), a real, genuine
            # state that worker actually reached before being told to
            # stop, not a fabricated or trivial one — discarding it
            # outright threw away real, already-computed progress for no
            # benefit. `total_attempts_tried`'s own `checks` summation
            # below is unaffected either way (it already summed over every
            # raw outcome, interrupted ones included, both before and
            # after this change).
            #
            # `failed_real` (kept as its own name so nothing below needs to
            # change) is therefore now just `failed_all` — no more `reason
            # != "interrupted_other_attempt_done"` filtering. A worker
            # interrupted so early it never made any real progress at all
            # — "encore en train d'essayer de construire", per the user's
            # own words — naturally shows up with `assigned_letter_count
            # == 0`/an all-blank `example_grid` instead; nothing special
            # needs to filter it out on purpose, since `_backtrack`'s own
            # interruption check only ever runs from *inside* the search
            # loop (never before it starts), so `Filler.best_assignment`
            # already reflects whatever little (or, in the rare worst
            # case, nothing at all) that worker managed before being told
            # to stop.
            #
            # Caveat carried over from the design this reverts, worth
            # keeping in mind rather than silently dropping: an
            # interrupted worker's own `impossible_cells` count (the sort
            # key `failed_pairs` uses just below) can be misleadingly LOW
            # simply because it had less time to explore and so discover
            # fewer of them — not necessarily because it's genuinely
            # closer to a solution than a naturally-concluded worker that
            # explored much further and found more. Not addressed by this
            # change (the user asked specifically to stop discarding this
            # data, not to redesign the sort criterion) — flagged here so
            # a future report of "the carried-forward grid looks worse
            # than a failed one that explored further" has a documented,
            # plausible starting hypothesis.
            failed_real = failed_all
            seen_keys = set()
            failed_unique = []
            for g, d in failed_real:
                key = (tuple(map(tuple, g)), tuple(d["assignment"]))
                if key not in seen_keys:
                    seen_keys.add(key)
                    failed_unique.append((g, d))
            # `failed_unique` reste construit uniquement à partir de
            # résultats *réels* de recherches (`failed_all`, ci-dessus) —
            # jamais des états publiés en temps réel par `best_state_queue`
            # (voir plus bas) : c'est ce pool, et lui seul, qui décide de
            # `failed_pairs`/`selected_grid`/`selected_diag`/`still_has_hope`
            # /`_build_retry_seed` — tout ce qui influence réellement la
            # progression de la recherche d'un palier à l'autre. Un état
            # publié par la file reste visible dans l'aperçu affiché à
            # l'écran (voir `display_pairs`/`last_examples` plus bas), mais
            # ne peut plus jamais devenir la base du palier suivant à la
            # place d'un résultat réellement abouti — à la demande explicite
            # de l'utilisateur, après un vrai échec mesuré en direct : même
            # une fois le critère de tri corrigé (voir `_playable_score`
            # plus bas), laisser un état intermédiaire concourir pour cette
            # sélection restait risqué, puisque la recherche qui l'a produit
            # n'était pas allée assez loin pour détecter tous les vrais
            # conflits — son propre `impossible_slots` peut donc être
            # incomplet par rapport à celui d'une recherche réellement
            # terminée, ce qui rendrait le nettoyage du palier suivant
            # (`_build_retry_seed`, qui se fie justement à `impossible_
            # slots` pour décider quels mots retirer) lui-même incomplet.
            # `total_attempts` compte les grilles réellement essayées et
            # abandonnées au sens propre du mot, à la demande explicite de
            # l'utilisateur — pas le nombre de processus parallèles lancés
            # (10 par palier), qui ne reflète absolument pas le travail
            # réel effectué : le remplissage CSP procède par essais
            # successifs avec retour en arrière (voir Filler._backtrack) —
            # chaque tentative de poser un mot (`filler.checks`, incrémenté
            # une fois par mot candidat essayé dans la boucle de
            # `_backtrack`, qu'il mène ou non à une descente récursive plus
            # loin — voir le commentaire de cette boucle pour pourquoi ce
            # compteur n'est plus lié à la seule profondeur de récursion)
            # représente une configuration de grille réellement tentée puis
            # abandonnée dès que la recherche recule ou rejette ce mot.
            # Sommé sur TOUTES les tentatives
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
                # Le 3e élément (diag) de chaque tuple de `successes`
                # n'est plus utilisé ici depuis la suppression de l'aperçu
                # tardif "cases noires posées" (voir plus bas) — il
                # alimentait uniquement `_preview_locked_source` pour cet
                # aperçu, aujourd'hui supprimé.
                best, best_result, _ = max(
                    successes, key=lambda gr: sum(len(slot) ** 2 for slot in gr[1][0])
                )
                break
            # Toutes les tentatives réellement distinctes de ce palier,
            # triées par nombre de cases noires croissant — à la demande
            # explicite de
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
            #
            # Un instant remplacé par `_playable_score` (racine carrée de la
            # somme des carrés des longueurs jouables, sur l'état BRUT) une
            # fois la fusion des états publiés par `best_state_queue` mise
            # en place, le temps de corriger un biais réel : "le moins de
            # cases injouables" favorisait à tort un état publié tôt dans
            # une recherche encore très peu avancée sur un résultat
            # réellement abouti d'une autre tentative. Un instant remis à
            # `len(impossible_cells)` une fois les états publiés par la
            # file structurellement écartés de cette sélection (voir
            # `failed_unique`/`display_pairs` plus haut/plus bas).
            #
            # Remplacé une dernière fois par `_cleaned_playable_score`, à
            # la demande explicite de l'utilisateur : "Il faut montrer les
            # emplacements avant nettoyage, évaluer la grille après
            # nettoyage (qui sera transmise au cycle suivant si
            # sélectionnée)." Ni `len(impossible_cells)` ni `_playable_
            # score` n'évaluaient l'état qui compte réellement pour cette
            # sélection : celui qui sera transmis au palier suivant si
            # cette tentative gagne — c'est-à-dire l'état APRÈS `_clean_
            # blocked_slots`, pas l'état brut d'avant nettoyage. Deux
            # tentatives à égalité de cases injouables brutes peuvent
            # perdre des quantités de contenu très différentes une fois
            # nettoyées, selon la longueur du mot qui croise l'emplacement
            # impossible — voir `_cleaned_playable_score`'s propre
            # docstring pour le détail complet.
            failed_pairs = sorted(
                failed_unique,
                key=lambda gd: _cleaned_playable_score(gd[0], gd[1], rows, cols, index, rng),
                reverse=True,
            )
            last_diag = failed_pairs[0][1]
            # Pool séparé, réservé à l'affichage — jamais utilisé pour
            # `selected_grid`/`selected_diag`/`still_has_hope`/`_build_
            # retry_seed` plus bas, qui continuent de se fier exclusivement
            # à `failed_pairs` (construit ci-dessus à partir de `failed_
            # unique`, lui-même uniquement des résultats réels — voir son
            # propre commentaire). Part de `failed_unique` (une copie, pour
            # ne jamais muter la liste qui sert par ailleurs à la sélection
            # réelle) puis y fusionne les états publiés en temps réel par
            # `best_state_queue`, à la demande explicite de l'utilisateur :
            # "Chaque process suit son meilleur état, et transmet au
            # process parent l'information que ce meilleur état a changé.
            # Le process parent garde les 6 meilleurs états, de tous les
            # états dont il a été informé par les N process" — restreint
            # ensuite à l'affichage seul, après un vrai échec mesuré en
            # direct (voir le commentaire de `failed_unique` plus haut)
            # une fois confirmé que laisser ces états concourir pour la
            # sélection réelle dégradait la progression d'un palier à
            # l'autre. Voir Filler.on_new_best/_publish_new_best (try_fill)
            # pour la publication ; voir best_state_queue plus haut pour
            # pourquoi c'est une Queue (et pas juste un Event) et pourquoi
            # elle est créée une seule fois pour toute la génération.
            #
            # Ne lit plus jamais `best_state_queue` directement ici — un
            # thread dédié (`best_state_drain_thread`, démarré une seule
            # fois avant la création du pool, voir son propre commentaire)
            # la vide en continu dans `best_state_buffer`, précisément pour
            # éviter un vrai interblocage constaté en direct : un worker
            # encore profondément dans sa recherche peut publier des
            # dizaines de fois avant de rendre la main, et le tube sous-
            # jacent d'une `multiprocessing.Queue` a une capacité bornée —
            # ne le lire qu'une fois par palier, une fois tous les workers
            # revenus, laissait le temps à ce tube de se remplir et de
            # bloquer un `put()` avant même que quiconque ne le lise.
            #
            # `as_completed` a déjà épuisé tous les futures de ce palier
            # plus haut, donc chaque worker a déjà terminé sa recherche et
            # émis son dernier `put()` — mais le thread de drainage ne
            # l'aura pas forcément encore consommé au moment exact où ce
            # code s'exécute (il ne fait qu'interroger la file toutes les
            # BEST_STATE_QUEUE_DRAIN_GRACE_S secondes). Une courte pause,
            # de deux fois cet intervalle, laisse au thread au moins un
            # cycle complet pour rattraper un message tout juste publié
            # avant que ce code ne lise `best_state_buffer` — un compromis
            # borné (quelques dizaines de millisecondes par palier, jamais
            # plus), pas une garantie absolue, mais un message manqué ici
            # serait simplement traité au palier suivant plutôt que
            # celui-ci, sans jamais risquer de reproduire l'interblocage.
            time.sleep(2 * BEST_STATE_QUEUE_DRAIN_GRACE_S)
            with best_state_buffer_lock:
                published_this_palier = best_state_buffer[:]
                best_state_buffer.clear()
            display_seen_keys = set(seen_keys)
            display_unique = list(failed_unique)
            for published in published_this_palier:
                # `grid` retiré du dict après lecture (pop, pas juste get) :
                # une fois extrait dans `pub_grid` (le premier élément du
                # couple `(grid, diag)`, exactement la même forme que
                # failed_all/failed_unique ci-dessus), il n'a plus sa place
                # à l'intérieur du diagnostic lui-même — le laisser dedans
                # ferait fuiter une copie de la grille (redondante avec
                # `example_grid`) dans le JSON envoyé au Front.
                pub_grid = published.pop("grid")
                pub_key = (tuple(map(tuple, pub_grid)), tuple(published["assignment"]))
                if pub_key not in display_seen_keys:
                    display_seen_keys.add(pub_key)
                    display_unique.append((pub_grid, published))
            # Réduit à une seule grille par tentative parallèle (process), à
            # la demande explicite de l'utilisateur : "Actuellement : on
            # garde toutes les meilleures grilles de tous les process (6
            # max). Modifier : on ne garde qu'une seule meilleure grille par
            # process." Jusqu'ici, `display_unique` pouvait contenir
            # plusieurs entrées distinctes issues de la MÊME tentative — son
            # résultat final (`failed_unique`) ET une ou plusieurs de ses
            # propres publications intermédiaires (`best_state_queue`,
            # chacune un instantané différent puisque `assigned_count`
            # augmente à chaque nouveau record) — puisque le dédoublonnage
            # ci-dessus ne compare que le contenu (motif + affectation),
            # jamais quelle tentative l'a produit ; une seule tentative très
            # productive pouvait ainsi à elle seule occuper plusieurs des
            # places affichées (alors limitées à 6, depuis retiré — voir plus
            # bas), au détriment des autres tentatives du même palier.
            # `attempt_id` (la graine
            # de cette tentative précise, voir `try_fill`'s propre
            # docstring) identifie maintenant de façon fiable, pour chaque
            # entrée — qu'elle vienne d'un résultat final ou d'une
            # publication intermédiaire —, de quelle tentative elle
            # provient ; regroupées par cet identifiant, seule celle au
            # score le plus élevé (`_playable_score`, l'état BRUT — même
            # critère que le tri de `display_rest` juste en dessous) survit
            # par groupe. `None` (un appelant hypothétique qui n'aurait
            # jamais fourni cet identifiant — aucun cas réel aujourd'hui)
            # reste traité comme une entrée à part entière à chaque fois,
            # jamais fusionné avec quoi que ce soit d'autre, pour ne
            # collapser aucune entrée distincte par erreur faute
            # d'identifiant.
            best_by_attempt = {}
            for idx, (g, d) in enumerate(display_unique):
                attempt_key = d.get("attempt_id")
                if attempt_key is None:
                    attempt_key = ("__no_attempt_id__", idx)
                score = _playable_score(d)
                if attempt_key not in best_by_attempt or score > best_by_attempt[attempt_key][0]:
                    best_by_attempt[attempt_key] = (score, (g, d))
            display_unique = [gd for _, gd in best_by_attempt.values()]
            # `failed_pairs[0]` (le vainqueur réel — celui qui va être
            # nettoyé via `_clean_blocked_slots` et transmis au palier
            # suivant, voir plus bas) est TOUJOURS placé en premier ici,
            # quel que soit son propre score — jamais laissé au tri normal.
            # Bug réel constaté en direct, avec des captures d'écran à
            # l'appui : `display_pairs` et `failed_pairs` utilisant deux
            # critères de tri différents (le premier par `_playable_score`,
            # le second par `len(impossible_cells)`), la toute première
            # grille montrée à l'écran pouvait être une tentative
            # complètement différente de celle réellement conservée —
            # jusqu'à un motif noir/blanc entièrement différent, pas
            # seulement un contenu différent. L'utilisateur comparait alors
            # à raison "cette grille" (la première montrée) à l'étape
            # suivante (le début du palier suivant, qui affiche le vrai
            # motif conservé) et y voyait des mots croisant une situation
            # impossible jamais retirés — alors qu'en réalité ce n'était
            # simplement pas la même grille : celle réellement conservée et
            # nettoyée n'était jamais celle affichée en premier. Garantir
            # que la première grille montrée est toujours la grille
            # réellement conservée rend la comparaison "avant nettoyage
            # (ici) / après nettoyage (au palier suivant)" valide.
            winner_grid, winner_diag = failed_pairs[0]
            winner_key = (tuple(map(tuple, winner_grid)), tuple(winner_diag["assignment"]))
            # Exclut aussi, en plus du contenu exact ci-dessus, toute autre
            # entrée partageant la MÊME tentative (`attempt_id`) que le
            # vainqueur — un vrai doublon trouvé en direct une fois le
            # plafond d'affichage retiré (voir plus bas) : `winner_grid`/
            # `winner_diag` viennent de `failed_pairs[0]` (trié par
            # `_cleaned_playable_score`, sur l'état APRÈS nettoyage), tandis
            # que la réduction "une grille par tentative" de `display_unique`
            # ci-dessus trie par `_playable_score` (l'état BRUT) — deux
            # critères différents qui peuvent légitimement retenir, pour la
            # MÊME tentative, deux représentants différents : le résultat
            # final réel (devenu le vainqueur) d'un côté, un instantané
            # intermédiaire publié plus tôt par cette même tentative de
            # l'autre. Sans cette exclusion supplémentaire, la même
            # tentative pouvait apparaître deux fois dans `display_pairs` —
            # une fois comme vainqueur, une fois via son propre instantané
            # antérieur — violant "une seule grille par tentative" alors
            # même que ce filtre par contenu seul ne les jugeait pas
            # identiques (deux états réellement différents, pris à deux
            # moments différents de la même recherche).
            winner_attempt_id = winner_diag.get("attempt_id")
            display_rest = sorted(
                (gd for gd in display_unique
                 if (tuple(map(tuple, gd[0])), tuple(gd[1]["assignment"])) != winner_key
                 and (winner_attempt_id is None or gd[1].get("attempt_id") != winner_attempt_id)),
                key=lambda gd: _playable_score(gd[1]), reverse=True,
            )
            display_pairs = [(winner_grid, winner_diag)] + display_rest
            # Chaque grille affichée montre l'état AVANT nettoyage (`d[
            # "example_grid"]`, tel quel) — brièvement remplacé par une
            # version déjà nettoyée (`_cleaned_example_preview`), reverti
            # à la demande explicite de l'utilisateur : "la visualisation
            # des extraits montre maintenant les grilles nettoyées avec
            # des emplacements impossibles vides. On ne comprend plus ce
            # qui se passe. Il faut montrer les emplacements avant
            # nettoyage, évaluer la grille après nettoyage." Voir la
            # sélection de `failed_pairs` plus haut (`_cleaned_playable_
            # score`) pour l'évaluation, désormais bien faite sur l'état
            # après nettoyage — seul l'AFFICHAGE reste sur l'état brut,
            # pour que les cases marquées `impossible_cells` restent
            # entourées d'un vrai contexte (les mots qui ont créé le
            # conflit) plutôt que de rester vides sans explication.
            # Toutes les grilles de `display_pairs`, sans troncature — à la
            # demande explicite de l'utilisateur : "Afficher toutes les
            # meilleures grilles dans l'aperçu, pas seulement les 6
            # meilleures." Un plafond fixe (`FAILED_ATTEMPT_EXAMPLES`, 6)
            # limitait auparavant cette liste ; `display_pairs` elle-même
            # est déjà réduite à une seule entrée par tentative parallèle
            # (voir plus haut), donc cette liste ne peut de toute façon
            # jamais dépasser `PARALLEL_ATTEMPTS` grilles.
            last_examples = [
                {
                    "example_grid": d["example_grid"],
                    "impossible_cells": d["impossible_cells"],
                    "forced_cells": d["forced_cells"],
                    "locked_cells": d.get("locked_cells", []),
                }
                for g, d in display_pairs
            ]
            # Un aperçu tardif "cases noires posées" (motif sans les
            # lettres) vivait ici, juste avant `pattern_attempt_failed` —
            # supprimé à la demande explicite de l'utilisateur, une fois
            # confirmé 100 % redondant avec lui : `pattern_attempt_failed`
            # (juste en dessous) montre déjà les mêmes motifs, avec en plus
            # les lettres réellement trouvées et les diagnostics complets.
            # Le seul aperçu "cases noires posées" qui reste est désormais
            # le précoce (voir plus haut, avant `executor.submit`), publié
            # avant même que la recherche ne démarre — l'aperçu tardif
            # n'ajoutait rien de plus, seulement une redite plus tôt dans
            # la séquence, ce qui donnait l'impression trompeuse d'un
            # nouveau tirage de cases noires ("il a refait une génération
            # de cases noires, qui a déjà été faite à l'étape précédente").
            progress("pattern_attempt_failed", attempt=attempt + 1, attempts=attempts,
                     ratio=round(ratio, 3),
                     total_attempts=total_attempts_tried, examples=last_examples,
                     **_public_diag(last_diag))
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
            # Dernier recours avant toute décision "reprise telle quelle" /
            # nettoyage, à la demande explicite de l'utilisateur : voir
            # `_plug_isolated_cells`'s propre docstring pour la définition
            # précise d'une case "isolée" et les conditions qui la
            # déclenchent. Un `None` (le cas normal, largement le plus
            # fréquent) laisse tout le reste de ce palier inchangé —
            # seule une grille où il ne reste plus RIEN que des cases
            # isolées à boucher, formant après coup une grille entièrement
            # remplie et valide, court-circuite la suite en la déclarant
            # directement réussie, exactement comme une réussite CSP
            # normale (`best`/`best_result`, utilisés tels quels par tout
            # le code qui suit la boucle des paliers).
            plugged = _plug_isolated_cells(
                selected_grid, rows, cols,
                extract_slots(selected_grid, rows, cols),
                selected_diag["assignment"], index,
            )
            if plugged is not None:
                new_grid, new_slots, new_assignment = plugged
                best, best_result = new_grid, (new_slots, new_assignment)
                break
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
            # out of 10 on a 10-core machine) rather than all PARALLEL_ATTEMPTS — a
            # faster-firing version of the same original intent, no longer
            # needing every single attempt to each independently reach that
            # same conclusion before this palier is even allowed to finish.
            if failed_real and all(d["reason"] == "abandoned_too_unfillable" for _, d in failed_real):
                still_has_hope = False
            # Plafond de MAX_CONSECUTIVE_CONTINUE_PALIERS paliers "continue"
            # consécutifs (relevé de 5 à 10 puis à 50, puis ramené à 10 puis
            # à 5, puis nommé et ramené à 1, toujours à la demande explicite
            # de l'utilisateur) — même quand `still_has_hope` reste `True`,
            # on force un nettoyage dès que ce plafond est atteint, plutôt
            # que de laisser la reprise "telle quelle" s'enchaîner
            # indéfiniment sur un motif qui ne progresse peut-être plus
            # vraiment d'un palier à l'autre.
            if consecutive_continue_paliers >= MAX_CONSECUTIVE_CONTINUE_PALIERS:
                still_has_hope = False

            if still_has_hope:
                consecutive_continue_paliers += 1
                just_cleaned = False
                # Nettoyage automatique des emplacements bloqués, à la
                # demande explicite de l'utilisateur : "à la fin d'un tour,
                # nettoyer automatiquement les emplacements bloqués, mais
                # pas les noires." Retire, avant même de reprendre "telle
                # quelle" au palier suivant, tout mot qui croise directement
                # un emplacement impossible (`_clean_blocked_slots`, les
                # étapes 1-2 de `_build_retry_seed` sans sa 3e étape) —
                # désormais appliqué à CHAQUE tentative distincte de ce
                # palier (`failed_pairs`), pas seulement la "meilleure"
                # (`selected_grid`/`selected_diag`) comme avant cette
                # fonctionnalité — à la demande explicite de l'utilisateur :
                # "Regression : après un cycle, le cycle suivant repart
                # maintenant avec une seule grille. Quand il n'y a pas de
                # déclenchement d'un nettoyage complet, chaque process doit
                # repartir à l'étape suivante avec sa grille partiellement
                # nettoyée (sauf le pourcentage de grilles entièrement
                # neuves)." Voir `_clean_continue_candidate` (niveau module,
                # juste après `_build_retry_seed`) pour le détail exact —
                # même logique, y compris l'alternative case noire à 1/10
                # (`BLACK_CELL_INSTEAD_OF_REMOVAL_PROBABILITY`, voir sa
                # propre docstring pour le raisonnement complet), appliquée
                # une fois par tentative au lieu d'une seule fois sur le
                # vainqueur.
                cleaned_continue_candidates = _sorted_by_score(
                    _clean_continue_candidate(cand_grid, cand_diag, rows, cols, index, rng)
                    for cand_grid, cand_diag in failed_pairs
                )
                carry_seed_pool_continue = _continue_seed_pool(cleaned_continue_candidates)
                carry_seed_grid, carry_preseed_assignment, carry_excluded_slots = (
                    carry_seed_pool_continue[0]
                )
                carry_locked_letters = None
            else:
                consecutive_continue_paliers = 0
                carry_preseed_assignment = None
                carry_excluded_slots = None
                # Nouvel algorithme de reprise entre paliers, à la demande
                # explicite de l'utilisateur (voir _build_retry_seed) : nettoyer
                # toutes les tentatives distinctes de CE palier (voir
                # `_clean_all_candidates` plus bas pour l'étendue exacte),
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
                    # TOUTES les tentatives distinctes de ce palier (jusqu'à
                    # PARALLEL_ATTEMPTS, pas seulement les FAILED_ATTEMPT_
                    # EXAMPLES (6) affichées à l'écran — ce plafond reste un
                    # plafond d'AFFICHAGE, voir `display_pairs`/`last_
                    # examples` plus haut, sans rapport avec la sélection
                    # réelle ici), à la demande explicite de l'utilisateur :
                    # "on garde la meilleure grille de tous les process, soit
                    # N grilles pour N process." `failed_pairs` porte déjà,
                    # par construction, au plus une entrée par tentative
                    # (voir son propre commentaire plus haut) — nul besoin
                    # d'un dédoublonnage par tentative supplémentaire ici.
                    result = []
                    for cand_grid, cand_diag in failed_pairs:
                        cand_slots = extract_slots(cand_grid, rows, cols)
                        cand_seed, cand_confirmed = _build_retry_seed(
                            cand_grid, rows, cols, cand_slots,
                            cand_diag["assignment"], cand_diag["impossible_slots"],
                            locked_letters=carry_locked_letters,
                            exclude_impossible_locked=force_exclude,
                            seed_grid=carry_seed_grid, index=index, rng=rng,
                        )
                        result.append((cand_seed, cand_confirmed, cand_slots))
                    return result

                # Parmi les grilles nettoyées, celle qui l'emporte maximise la
                # somme des carrés des longueurs des mots en place *après*
                # nettoyage (un mot est "en place" si toutes ses cases
                # figurent dans `confirmed`) — à la demande explicite de
                # l'utilisateur, remplace l'ancien critère (le plus de
                # lettres restantes, départagé par le moins de cases noires).
                # Même formule de score que celle qui départage les
                # tentatives parallèles réussies plus haut dans cette
                # fonction (favorise quelques mots longs plutôt que beaucoup
                # de mots courts pour le même total de lettres) — appliquée
                # ici au résultat *après* nettoyage (le vrai signal utile
                # pour repartir), pas à un critère pré-nettoyage comme
                # précédemment.
                #
                # `_words_in_place_score`/`_candidate_black_count`/
                # `_sorted_by_score`/`_seed_pool` (le score, son départage par
                # le nombre de cases noires, le tri qui les combine, et la
                # réduction au vivier transmis au palier suivant) sont
                # désormais des fonctions de niveau module, juste après
                # `_build_retry_seed` — hissées hors de cette fermeture locale
                # à la demande explicite de l'utilisateur, une fois la même
                # logique nécessaire aussi pour la reprise "telle quelle" (voir
                # `_clean_continue_candidate`/`_continue_seed_pool`, et plus
                # bas, `if still_has_hope:`) ; voir leurs propres docstrings
                # pour le raisonnement complet (notamment le départage par
                # cases noires, ajouté après un vrai blocage constaté en
                # direct sur une grande grille 30×30 très majoritairement
                # verrouillée).

                previous_locked_letters = carry_locked_letters
                cleaned_candidates = _sorted_by_score(_clean_all_candidates(force_exclude=False))
                carry_seed_pool = _seed_pool(cleaned_candidates)
                carry_seed_grid, carry_locked_letters = carry_seed_pool[0]
                # Point fixe détecté : ce palier n'a produit aucun changement du
                # tout (les lettres confirmées sont rigoureusement identiques à
                # celles du palier précédent) — un vrai blocage qui, sans
                # intervention, se reproduirait à l'identique indéfiniment (voir
                # `_build_retry_seed`'s docstring pour l'historique complet de ce
                # cas). À la demande explicite de l'utilisateur, ce n'est
                # traité qu'en dernier recours, seulement une fois ce blocage
                # réellement constaté : le même nettoyage est relancé sur tous
                # les mêmes candidats avec `exclude_impossible_locked=True`, qui
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
                # simple sur la seule gagnante (la nouvelle diversité du
                # vivier ci-dessus, elle, porte sur TOUTES les grilles
                # nettoyées, pas seulement la gagnante — deux préoccupations
                # distinctes, l'une sur la détection du point fixe, l'autre
                # sur la diversité du prochain lancement).
                if previous_locked_letters is not None and carry_locked_letters == previous_locked_letters:
                    cleaned_candidates = _sorted_by_score(_clean_all_candidates(force_exclude=True))
                    carry_seed_pool = _seed_pool(cleaned_candidates)
                    carry_seed_grid, carry_locked_letters = carry_seed_pool[0]
                # No black cell is added here any more either (the
                # single-cell lock that used to run at this exact point,
                # shared with the "reprise telle quelle" branch above, was
                # removed entirely at the user's explicit request — see
                # CLAUDE.md for its full history) — a full cleanup now only
                # ever changes which words/black cells survive from the
                # already-generated pattern, never adds a new one.
                just_cleaned = True
                # Mémorisation de l'état obtenu à la fin de CE nettoyage
                # (motif noir/blanc ET contenu confirmé) et détection d'un
                # état qui se répète à l'identique d'un nettoyage au
                # suivant — voir GRID_REPEAT_INFEASIBLE_THRESHOLD's own
                # docstring pour la demande complète et son historique.
                # Réservé à cette seule branche (`if still_has_hope:` ci-
                # dessus, "reprise telle quelle", n'y touche jamais) — à la
                # demande explicite de l'utilisateur, après DEUX régressions
                # mesurées en direct sur le benchmark standard 15×10
                # (Flash) : une première version comparait uniquement le
                # motif noir/blanc, sur les deux branches — le motif reste
                # très souvent identique plusieurs cycles "reprise telle
                # quelle" de suite par construction (le nettoyage n'ajoute
                # une case noire qu'une fois sur dix, voir BLACK_CELL_
                # INSTEAD_OF_REMOVAL_PROBABILITY) alors même que le contenu
                # progresse normalement, confondant ça avec un vrai blocage.
                # Une deuxième version comparait motif+contenu, toujours sur
                # les deux branches — un diagnostic détaillé (branche +
                # compteur consecutive_continue_paliers à chaque
                # déclenchement) a montré que la majorité des déclenchements
                # coïncidaient, sur la branche "reprise telle quelle", très
                # exactement avec le moment où MAX_CONSECUTIVE_CONTINUE_
                # PALIERS force déjà, tout seul, un passage en nettoyage —
                # ce mécanisme faisait alors doublon avec un garde-fou déjà
                # réglé, mais avec une réponse bien plus destructrice
                # (grille entièrement vierge au lieu d'un nettoyage
                # classique qui conserve le contenu valide). Restreindre la
                # détection à cette seule branche règle les deux problèmes
                # à la fois : un cycle "reprise telle quelle" ne compte
                # jamais dans la série (déjà borné ailleurs), et seul un
                # vrai point fixe du nettoyage LUI-MÊME (celui que la
                # relance unique avec `exclude_impossible_locked=True`,
                # juste au-dessus, ne résout pas toujours) déclenche la
                # réinitialisation. Réutilise `_cycle_start_preview` (déjà
                # appelée ailleurs dans cette même boucle pour l'aperçu
                # "début de cycle") pour fusionner motif + contenu en une
                # seule grille comparable — `carry_preseed_assignment` vaut
                # toujours `None` sur cette branche, donc `_cycle_start_
                # preview` construit systématiquement à partir de `carry_
                # locked_letters` ici. Comparé comme un tuple de tuples
                # (hashable, comparaison de contenu, pas d'identité) plutôt
                # que la liste elle-même — `locked_cells` (2e valeur de
                # retour) n'est pas utile ici, seule la grille fusionnée
                # sert de clé.
                current_state_grid, _ = _cycle_start_preview(
                    rows, cols, carry_seed_grid, carry_locked_letters, carry_preseed_assignment,
                )
                current_pattern_key = tuple(tuple(row) for row in current_state_grid)
                if current_pattern_key == last_cycle_end_grid:
                    same_grid_streak += 1
                else:
                    last_cycle_end_grid = current_pattern_key
                    same_grid_streak = 1
                if same_grid_streak > GRID_REPEAT_INFEASIBLE_THRESHOLD:
                    # Motif jugé infaisable : réinitialisation complète, le
                    # prochain cycle repart d'une grille entièrement vierge
                    # — exactement l'état initial de cette fonction (voir
                    # `carry_seed_grid = None` tout en haut), y compris les
                    # deux viviers et le compteur de série "reprise telle
                    # quelle", pour qu'un palier "motif neuf" reparte bien
                    # de zéro plutôt que de réutiliser un vivier construit
                    # à partir du motif désormais abandonné.
                    carry_seed_grid = None
                    carry_locked_letters = None
                    carry_preseed_assignment = None
                    carry_excluded_slots = None
                    carry_seed_pool = None
                    carry_seed_pool_continue = None
                    consecutive_continue_paliers = 0
                    last_cycle_end_grid = None
                    same_grid_streak = 0
            # Le ratio cible ne progresse plus d'un palier à l'autre (reste
            # fixé à `black_ratio`, 0.0 par défaut), à la demande explicite
            # de l'utilisateur : le pré-remplissage (au moins
            # PREFILL_MIN_WORD_COUNT candidats par emplacement) combiné à la
            # reprise sur la grille nettoyée du palier précédent
            # (_build_retry_seed juste au-dessus) suffit à faire progresser
            # la recherche, sans avoir besoin de densifier artificiellement
            # la grille palier après palier.

    # Arrêt propre du thread de drainage de `best_state_queue` (voir sa
    # propre docstring plus haut) — la recherche elle-même est terminée
    # (succès ou épuisement de `attempts`), rien de plus ne sera jamais
    # publié dessus. `daemon=True` garantirait de toute façon qu'il ne
    # bloque jamais la fin du processus si ce point n'était pas atteint
    # (par exemple `GenerationCancelled`, levée depuis l'intérieur de la
    # boucle ci-dessus, sans jamais repasser par ici) — cet arrêt explicite
    # est purement une question d'hygiène dans le cas normal, pas une
    # protection dont la correction du programme dépendrait.
    stop_best_state_drain.set()
    best_state_drain_thread.join(timeout=1.0)

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
        progress("pattern_failed", attempts=attempts,
                  last_attempt=_public_diag(last_diag) if last_diag is not None else None,
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
