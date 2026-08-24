#!/usr/bin/env python3
"""
Générateur de grilles de mots croisés denses.

Approche en deux temps :
  1. Génération d'un motif de cases noires symétrique (180°) respectant les règles
     structurelles (aucune séquence blanche < 3, grille blanche connexe).
  2. Remplissage par CSP (backtracking + heuristique MRV) avec un vrai dictionnaire,
     puis minimisation locale : on essaie de retirer chaque paire de cases noires
     symétriques et on ne garde le retrait que si la grille reste remplissable.

La grille peut être rectangulaire : `width` (nombre de colonnes, horizontal) et
`height` (nombre de lignes, vertical) se règlent indépendamment (15x10 par défaut).

Usage (depuis la racine du projet) :
    python3 backend/crossword_gen.py --width 15 --height 10 --wordlist data/wordlist_fr_full.tsv
"""
import argparse
import os
import random
import re
import sys
from collections import Counter, defaultdict

BLACK = "#"
WHITE = "."

DEFAULT_WIDTH = 15
DEFAULT_HEIGHT = 10


# ---------- Dictionnaire ----------

# Presets de difficulté : nombre max de mots conservés au total (classement
# global par fréquence, toutes longueurs confondues), pas par longueur — un
# plafond par longueur ne filtre rien pour les longueurs qui ont moins de
# mots au total que le plafond lui-même (ex. il n'existe que ~700 mots de 3
# lettres en français, donc un ancien plafond de 600 "par longueur" laissait
# passer TOUS les mots de 3 lettres, y compris des mots obscurs comme "ABD" —
# bug réel signalé par l'utilisateur, score 103, ~33 000e position globale).
# Moins de mots -> vocabulaire plus reconnaissable mais grille parfois plus
# dure à remplir ; "hard"/None garde tout le lexique.
DIFFICULTY_PRESETS = {
    "easy": 25_000,
    "medium": 50_000,
    "hard": None,
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
    aucun dictionnaire de définitions n'a été construit pour elle. Retourne
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

def is_structurally_valid(grid, rows, cols):
    for r in range(rows):
        run = 0
        for c in range(cols):
            if grid[r][c] == WHITE:
                run += 1
            else:
                if 0 < run < 3:
                    return False
                run = 0
        if 0 < run < 3:
            return False
    for c in range(cols):
        run = 0
        for r in range(rows):
            if grid[r][c] == WHITE:
                run += 1
            else:
                if 0 < run < 3:
                    return False
                run = 0
        if 0 < run < 3:
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


def make_symmetric_pattern(rows, cols, black_ratio, rng):
    """Places black cells one symmetric pair at a time, biased to keep the
    number of black cells roughly even across rows and across columns.

    A purely random placement order (just shuffling every cell) tends to
    let black cells pile up by chance in a handful of rows/columns while
    others get none, especially at the black ratios needed for larger
    grids. That reads as "walls" of aligned black squares — which is also
    exactly what forces many neighboring words to share the same length
    (the length is just the gap between black cells in that row/column).
    Balancing the density instead spreads black cells out, which both
    reduces alignment and, as a side effect, varies neighboring word
    lengths more.

    Implemented as a small look-ahead: at each step, sample a window of
    still-untried cells and place the one whose row+column currently have
    the fewest black cells, instead of taking cells in strict shuffle
    order. Falls back to shuffle order once the window is exhausted, so
    this is a soft preference, not a hard constraint — it never makes a
    fillable ratio/size combination infeasible.

    Window size of 32 was picked empirically (measuring the variance of
    black-cell counts per row/column across many generated patterns): too
    small a window barely improves on pure shuffling, too large trends
    back toward a full argmin at each step, which paradoxically balances
    *worse* on rectangular grids (ties cascade into clustering). 32 cut
    row-count variance by ~30% and column-count variance by ~20-25%
    compared to no look-ahead at all, on a 15x10 grid."""
    grid = [[WHITE] * cols for _ in range(rows)]
    row_black = [0] * rows
    col_black = [0] * cols
    remaining = [(r, c) for r in range(rows) for c in range(cols)]
    rng.shuffle(remaining)
    target = round(rows * cols * black_ratio)
    window = 32
    placed = 0
    while remaining and placed < target:
        sample_size = min(window, len(remaining))
        best_idx = min(
            range(sample_size),
            key=lambda i: row_black[remaining[i][0]] + col_black[remaining[i][1]],
        )
        r, c = remaining.pop(best_idx)
        if grid[r][c] == BLACK:
            continue
        r2, c2 = rows - 1 - r, cols - 1 - c
        saved = (grid[r][c], grid[r2][c2])
        grid[r][c] = BLACK
        grid[r2][c2] = BLACK
        if is_structurally_valid(grid, rows, cols):
            row_black[r] += 1
            row_black[r2] += 1
            col_black[c] += 1
            col_black[c2] += 1
            placed += 1 if (r, c) == (r2, c2) else 2
        else:
            grid[r][c], grid[r2][c2] = saved
    return grid


# ---------- Extraction des cases (slots across / down) ----------

def extract_slots(grid, rows, cols):
    slots = []
    for r in range(rows):
        c = 0
        while c < cols:
            if grid[r][c] == WHITE:
                start = c
                while c < cols and grid[r][c] == WHITE:
                    c += 1
                if c - start >= 3:
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
                if r - start >= 3:
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


# ---------- CSP : remplissage par backtracking + MRV ----------

class Filler:
    def __init__(self, slots, index, rng):
        self.slots = slots
        self.index = index
        self.rng = rng
        # cell -> [(slot_index, position_within_that_slot), ...]. Precomputed
        # once here rather than looked up with list.index() inside _domain
        # (the hot path, called millions of times per grid) since a cell's
        # position within a slot never changes once slots are extracted.
        self.cell_to_slots = defaultdict(list)
        for i, cells in enumerate(slots):
            for pos, cell in enumerate(cells):
                self.cell_to_slots[cell].append((i, pos))
        self.assignment = [None] * len(slots)
        self.used_words = set()
        self.checks = 0

    def _domain(self, i):
        """Ensemble/liste des mots compatibles avec les lettres déjà connues de
        la case i (sans encore exclure les mots utilisés ailleurs — voir _pick)."""
        cells = self.slots[i]
        length = len(cells)
        idx = self.index.get(length)
        if idx is None:
            return ()
        constraints = {}
        for pos, cell in enumerate(cells):
            for j, other_pos in self.cell_to_slots[cell]:
                if j != i and self.assignment[j] is not None:
                    constraints[pos] = self.assignment[j][other_pos]
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

    def solve(self, deadline_checks):
        return self._backtrack(deadline_checks)

    def _backtrack(self, deadline_checks):
        self.checks += 1
        if self.checks > deadline_checks:
            return False
        unassigned = [i for i in range(len(self.slots)) if self.assignment[i] is None]
        if not unassigned:
            return True

        # MRV : on ne matérialise/filtre (mots déjà utilisés) que le domaine
        # de la case retenue, pas ceux de toutes les cases non assignées.
        best_i, best_domain, best_size = None, None, None
        for i in unassigned:
            domain = self._domain(i)
            size = len(domain)
            if size == 0:
                return False
            if best_size is None or size < best_size:
                best_i, best_domain, best_size = i, domain, size

        cands = [w for w in best_domain if w not in self.used_words]
        self.rng.shuffle(cands)
        for w in cands:
            self.assignment[best_i] = w
            self.used_words.add(w)
            if self._backtrack(deadline_checks):
                return True
            self.assignment[best_i] = None
            self.used_words.discard(w)
        return False


def try_fill(grid, rows, cols, index, rng, deadline_checks=200_000, diagnostics=None):
    """`diagnostics`, if given a dict, is filled in with data useful to
    understand *why* a fill attempt failed (see generate_grid's
    "pattern_failed" logging): `slot_count`/`length_counts` (the CSP's
    shape, independent of the word list), and `checks`/`reason` (how far
    the search got — "search_exhausted" means every candidate was tried
    within budget and none worked, a genuine dead end for this pattern;
    "deadline_exceeded" means the `deadline_checks` budget ran out first,
    inconclusive; "no_slots" means the pattern had no white run >= 3
    cells at all)."""
    slots = extract_slots(grid, rows, cols)
    if diagnostics is not None:
        diagnostics["slot_count"] = len(slots)
        diagnostics["length_counts"] = dict(sorted(Counter(len(s) for s in slots).items()))
    if not slots:
        if diagnostics is not None:
            diagnostics["checks"] = 0
            diagnostics["reason"] = "no_slots"
        return None
    filler = Filler(slots, index, rng)
    solved = filler.solve(deadline_checks)
    if diagnostics is not None:
        diagnostics["checks"] = filler.checks
        diagnostics["reason"] = (
            "solved" if solved
            else "deadline_exceeded" if filler.checks >= deadline_checks
            else "search_exhausted"
        )
    if solved:
        return slots, filler.assignment
    return None


# ---------- Minimisation locale des cases noires ----------

def minimize_black_squares(grid, result, rows, cols, index, rng, deadline_checks=6_000):
    """Retire itérativement des paires de cases noires symétriques tant que la
    grille reste remplissable, en gardant la dernière solution connue (on évite
    ainsi un nouveau try_fill final qui pourrait échouer sur une recherche
    difficile alors qu'une solution vient d'être trouvée)."""
    slots, assignment = result
    improved = True
    while improved:
        improved = False
        black_cells = [(r, c) for r in range(rows) for c in range(cols) if grid[r][c] == BLACK]
        rng.shuffle(black_cells)
        for (r, c) in black_cells:
            if grid[r][c] != BLACK:
                continue
            r2, c2 = rows - 1 - r, cols - 1 - c
            saved = (grid[r][c], grid[r2][c2])
            grid[r][c] = WHITE
            grid[r2][c2] = WHITE
            if is_structurally_valid(grid, rows, cols):
                new_result = try_fill(grid, rows, cols, index, rng, deadline_checks)
                if new_result is not None:
                    slots, assignment = new_result
                    improved = True
                    continue
            grid[r][c], grid[r2][c2] = saved
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


def generate_grid(width=DEFAULT_WIDTH, height=DEFAULT_HEIGHT, difficulty="easy",
                   max_words=None, black_ratio=0.22, attempts=40, seed=None,
                   wordlist_path="data/wordlist_fr_full.tsv", on_progress=None):
    """Génère une grille remplie de bout en bout (motif + CSP + minimisation).
    `width` est le nombre de colonnes (horizontal), `height` le nombre de lignes
    (vertical). Retourne un dict {width, height, pattern, solution, words,
    word_count, black_count, black_ratio}, ou None si aucune grille remplissable
    n'a été trouvée en `attempts` essais.

    `on_progress`, si fourni, est appelé `on_progress(step, **data)` à chaque
    étape notable (voir backend/app.py, qui s'en sert à la fois pour tracer
    backend.log et pour exposer un statut d'avancement à l'interface via
    l'API de polling) — aucun effet sur la génération elle-même, purement
    un point d'observation."""
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
    for attempt in range(attempts):
        progress("pattern", attempt=attempt + 1, attempts=attempts)
        grid = make_symmetric_pattern(rows, cols, ratio, rng)
        diag = {}
        result = try_fill(grid, rows, cols, index, rng, diagnostics=diag)
        last_diag = diag
        if result is not None:
            best, best_result = grid, result
            break
        progress("pattern_attempt_failed", attempt=attempt + 1, ratio=round(ratio, 3), **diag)
        ratio = min(ratio + 0.02, 0.45)

    if best is None:
        progress("pattern_failed", attempts=attempts, last_attempt=last_diag)
        return None
    progress("pattern_found", attempt=attempt + 1)

    progress("minimizing")
    grid, slots, assignment = minimize_black_squares(best, best_result, rows, cols, index, rng)
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
    ap.add_argument("--difficulty", choices=sorted(DIFFICULTY_PRESETS), default="easy",
                     help="limite le vocabulaire aux N mots les plus fréquents au global "
                          "(toutes longueurs confondues) : easy=25000 (défaut), "
                          "medium=50000, hard=tout le lexique")
    ap.add_argument("--max-words", type=int, default=None,
                     help="surcharge manuelle du nombre max de mots au global "
                          "(prioritaire sur --difficulty)")
    ap.add_argument("--black-ratio", type=float, default=0.22,
                     help="densité de cases noires visée au départ (0-1)")
    ap.add_argument("--attempts", type=int, default=40,
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
