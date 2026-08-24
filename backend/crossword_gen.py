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
import random
import sys
from collections import defaultdict

BLACK = "#"
WHITE = "."

DEFAULT_WIDTH = 15
DEFAULT_HEIGHT = 10


# ---------- Dictionnaire ----------

# Presets de difficulté : nombre max de mots conservés par longueur, en ne
# gardant que les plus fréquents. Moins de mots -> vocabulaire plus reconnaissable
# mais grille parfois plus dure à remplir ; "hard"/None garde tout le lexique.
DIFFICULTY_PRESETS = {
    "easy": 600,
    "medium": 3000,
    "hard": None,
}


def load_wordlist(path, max_per_length=None):
    """Charge un lexique au format `MOT<TAB>ACCENTUE<TAB>FREQUENCE`
    (build_wordlist_freq.py) ou, en repli, un simple texte libre (un ou
    plusieurs mots par ligne, fréquence inconnue -> 0, pas de forme
    accentuée disponible). Retourne (by_length, accents) :
    - by_length = {longueur: [mots triés du plus fréquent au moins fréquent]},
      tronqué à max_per_length si fourni ;
    - accents = {MOT: forme accentuée/naturelle}, pour les mots retenus dans
      by_length (sert à donner au LLM la vraie orthographe — genre, nombre,
      conjugaison — quand il génère les définitions ; voir backend/clues.py)."""
    entries = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.rstrip("\n")
            if not line or line.startswith("#"):
                continue
            parts = line.split("\t")
            if len(parts) >= 3:
                word = parts[0].upper()
                accented = parts[1]
                try:
                    freq = float(parts[2])
                except ValueError:
                    freq = 0.0
                if word.isalpha():
                    entries.append((word, accented, freq))
            elif len(parts) == 2:
                word = parts[0].upper()
                try:
                    freq = float(parts[1])
                except ValueError:
                    freq = 0.0
                if word.isalpha():
                    entries.append((word, word, freq))
            else:
                for tok in line.upper().split():
                    if tok.isalpha():
                        entries.append((tok, tok, 0.0))

    by_length = defaultdict(dict)  # length -> {word: (accented, best_freq)}
    for word, accented, freq in entries:
        d = by_length[len(word)]
        if word not in d or freq > d[word][1]:
            d[word] = (accented, freq)

    result = {}
    accents = {}
    for length, d in by_length.items():
        words_sorted = sorted(d.items(), key=lambda kv: -kv[1][1])
        if max_per_length:
            words_sorted = words_sorted[:max_per_length]
        result[length] = [w for w, _ in words_sorted]
        for w, (accented, _) in words_sorted:
            accents[w] = accented
    return result, accents


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


def try_fill(grid, rows, cols, index, rng, deadline_checks=200_000):
    slots = extract_slots(grid, rows, cols)
    if not slots:
        return None
    filler = Filler(slots, index, rng)
    if filler.solve(deadline_checks):
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


def generate_grid(width=DEFAULT_WIDTH, height=DEFAULT_HEIGHT, difficulty="medium",
                   max_per_length=None, black_ratio=0.22, attempts=40, seed=None,
                   wordlist_path="data/wordlist_fr_full.tsv"):
    """Génère une grille remplie de bout en bout (motif + CSP + minimisation).
    `width` est le nombre de colonnes (horizontal), `height` le nombre de lignes
    (vertical). Retourne un dict {width, height, pattern, solution, words,
    word_count, black_count, black_ratio}, ou None si aucune grille remplissable
    n'a été trouvée en `attempts` essais."""
    rng = random.Random(seed)
    mpl = max_per_length or DIFFICULTY_PRESETS.get(difficulty)
    by_length, accents = load_wordlist(wordlist_path, mpl)
    index = build_index(by_length)

    rows, cols = height, width
    ratio = black_ratio
    best, best_result = None, None
    for _ in range(attempts):
        grid = make_symmetric_pattern(rows, cols, ratio, rng)
        result = try_fill(grid, rows, cols, index, rng)
        if result is not None:
            best, best_result = grid, result
            break
        ratio = min(ratio + 0.02, 0.45)

    if best is None:
        return None

    grid, slots, assignment = minimize_black_squares(best, best_result, rows, cols, index, rng)
    n_black = sum(row.count(BLACK) for row in grid)
    words = build_word_entries(grid, rows, cols, slots, assignment)
    for w in words:
        w["accented"] = accents.get(w["answer"], w["answer"])
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
    ap.add_argument("--difficulty", choices=sorted(DIFFICULTY_PRESETS), default="medium",
                     help="limite le vocabulaire aux N mots les plus fréquents par "
                          "longueur : easy=600, medium=3000 (défaut), hard=tout le lexique")
    ap.add_argument("--max-per-length", type=int, default=None,
                     help="surcharge manuelle du nombre max de mots par longueur "
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
        max_per_length=args.max_per_length,
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
