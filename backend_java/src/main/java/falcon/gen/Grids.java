package falcon.gen;

import falcon.gen.Words.DualIndex;
import falcon.gen.Words.LengthSets;

import java.util.ArrayList;
import java.util.Collection;
import java.util.HashSet;
import java.util.LinkedHashMap;
import java.util.LinkedHashSet;
import java.util.List;
import java.util.Map;
import java.util.Set;

/** Grid geometry and black-cell pattern generation (mirrors the
 * "Black-cell pattern generation" section of backend/crossword_gen.py). */
public final class Grids {
    private Grids() {}

    public static final char BLACK = '#';
    public static final char WHITE = '.';
    public static final int STRUCTURAL_MIN_INTERIOR_FREE = 8;
    public static final int PREFILL_MIN_WORD_COUNT = 3;
    public static final int PREFILL_LOCKED_MIN_WORD_COUNT = 3;
    public static final int NOISE_FREQUENCY_THRESHOLD = 5;
    public static final int PREFILL_ZONE_BLACK_BUDGET_FLOOR = 1;
    public static final double POST_PREFILL_BLACK_FRACTION = 0.10;

    // ---------------------------------------------------------------- grid helpers

    public static char[][] copy(char[][] g) {
        char[][] out = new char[g.length][];
        for (int i = 0; i < g.length; i++) out[i] = g[i].clone();
        return out;
    }

    public static char[][] blank(int rows, int cols) {
        char[][] g = new char[rows][cols];
        for (char[] row : g) java.util.Arrays.fill(row, WHITE);
        return g;
    }

    public static char[][] allBlack(int rows, int cols) {
        char[][] g = new char[rows][cols];
        for (char[] row : g) java.util.Arrays.fill(row, BLACK);
        return g;
    }

    public static int countBlack(char[][] g) {
        int n = 0;
        for (char[] row : g) for (char ch : row) if (ch == BLACK) n++;
        return n;
    }

    /** Pattern-only view: '#' stays, everything else becomes '.'. */
    public static char[][] patternOf(char[][] g) {
        char[][] out = new char[g.length][];
        for (int r = 0; r < g.length; r++) {
            out[r] = new char[g[r].length];
            for (int c = 0; c < g[r].length; c++) out[r][c] = g[r][c] == BLACK ? BLACK : WHITE;
        }
        return out;
    }

    /** Letters already written on a mixed grid (neither '#' nor '.'). */
    public static Map<Integer, Character> knownLetters(char[][] g) {
        Map<Integer, Character> known = new LinkedHashMap<>();
        for (int r = 0; r < g.length; r++) {
            for (int c = 0; c < g[r].length; c++) {
                char ch = g[r][c];
                if (ch != BLACK && ch != WHITE) known.put(Cells.of(r, c), ch);
            }
        }
        return known;
    }

    public static String key(char[][] g) {
        StringBuilder sb = new StringBuilder();
        for (char[] row : g) sb.append(row).append('\n');
        return sb.toString();
    }

    /** JSON shape: list of rows, each a list of one-character strings. */
    public static List<Object> toJson(char[][] g) {
        List<Object> rows = new ArrayList<>(g.length);
        for (char[] row : g) {
            List<Object> r = new ArrayList<>(row.length);
            for (char ch : row) r.add(String.valueOf(ch));
            rows.add(r);
        }
        return rows;
    }

    /** Parses a JSON grid (list of rows of strings, or list of strings). An
     * empty/blank cell string becomes {@code emptyAs}. */
    public static char[][] fromJson(Object json, char emptyAs) {
        List<?> rows = (List<?>) json;
        char[][] g = new char[rows.size()][];
        for (int r = 0; r < rows.size(); r++) {
            Object row = rows.get(r);
            if (row instanceof List<?> l) {
                g[r] = new char[l.size()];
                for (int c = 0; c < l.size(); c++) {
                    Object v = l.get(c);
                    String s = v == null ? "" : v.toString();
                    g[r][c] = s.isEmpty() ? emptyAs : s.charAt(0);
                }
            } else {
                g[r] = row.toString().toCharArray();
            }
        }
        return g;
    }

    // ---------------------------------------------------------------- structure

    public static boolean isStructurallyValid(char[][] grid, int rows, int cols) {
        return isStructurallyValid(grid, rows, cols, STRUCTURAL_MIN_INTERIOR_FREE);
    }

    public static boolean isStructurallyValid(char[][] grid, int rows, int cols, int minInteriorFree) {
        int[][] rowRun = new int[rows][cols];
        int[][] colRun = new int[rows][cols];
        for (int r = 0; r < rows; r++) {
            int run = 0, start = 0;
            for (int c = 0; c < cols; c++) {
                if (grid[r][c] == WHITE) {
                    if (run == 0) start = c;
                    run++;
                } else {
                    if (run > 0 && !(run >= minInteriorFree || start == 0 || start + run == cols)) return false;
                    for (int cc = start; cc < start + run; cc++) rowRun[r][cc] = run;
                    run = 0;
                }
            }
            if (run > 0 && !(run >= minInteriorFree || start == 0 || start + run == cols)) return false;
            for (int cc = start; cc < start + run; cc++) rowRun[r][cc] = run;
        }
        for (int c = 0; c < cols; c++) {
            int run = 0, start = 0;
            for (int r = 0; r < rows; r++) {
                if (grid[r][c] == WHITE) {
                    if (run == 0) start = r;
                    run++;
                } else {
                    if (run > 0 && !(run >= minInteriorFree || start == 0 || start + run == rows)) return false;
                    for (int rr = start; rr < start + run; rr++) colRun[rr][c] = run;
                    run = 0;
                }
            }
            if (run > 0 && !(run >= minInteriorFree || start == 0 || start + run == rows)) return false;
            for (int rr = start; rr < start + run; rr++) colRun[rr][c] = run;
        }
        int whiteCount = 0, firstR = -1, firstC = -1;
        for (int r = 0; r < rows; r++) {
            for (int c = 0; c < cols; c++) {
                if (grid[r][c] == WHITE) {
                    if (rowRun[r][c] < 2 && colRun[r][c] < 2) return false;
                    if (whiteCount == 0) {
                        firstR = r;
                        firstC = c;
                    }
                    whiteCount++;
                }
            }
        }
        if (whiteCount == 0) return false;
        boolean[][] seen = new boolean[rows][cols];
        int[] stack = new int[whiteCount + 4];
        int sp = 0, count = 1;
        stack[sp++] = Cells.of(firstR, firstC);
        seen[firstR][firstC] = true;
        int[] dr = {1, -1, 0, 0}, dc = {0, 0, 1, -1};
        while (sp > 0) {
            int cell = stack[--sp];
            int r = Cells.r(cell), c = Cells.c(cell);
            for (int k = 0; k < 4; k++) {
                int nr = r + dr[k], nc = c + dc[k];
                if (nr >= 0 && nr < rows && nc >= 0 && nc < cols && grid[nr][nc] == WHITE && !seen[nr][nc]) {
                    seen[nr][nc] = true;
                    count++;
                    stack[sp++] = Cells.of(nr, nc);
                }
            }
        }
        return count == whiteCount;
    }

    static boolean hasBlackNeighbor(char[][] grid, int rows, int cols, int r, int c) {
        return (r + 1 < rows && grid[r + 1][c] == BLACK) || (r - 1 >= 0 && grid[r - 1][c] == BLACK)
                || (c + 1 < cols && grid[r][c + 1] == BLACK) || (c - 1 >= 0 && grid[r][c - 1] == BLACK);
    }

    /** Every white run of at least 2 cells: across (row-major) then down (column-major). */
    public static List<int[]> extractSlots(char[][] grid, int rows, int cols) {
        List<int[]> slots = new ArrayList<>();
        for (int r = 0; r < rows; r++) {
            int c = 0;
            while (c < cols) {
                if (grid[r][c] == WHITE) {
                    int start = c;
                    while (c < cols && grid[r][c] == WHITE) c++;
                    if (c - start >= 2) {
                        int[] cells = new int[c - start];
                        for (int cc = start; cc < c; cc++) cells[cc - start] = Cells.of(r, cc);
                        slots.add(cells);
                    }
                } else {
                    c++;
                }
            }
        }
        for (int c = 0; c < cols; c++) {
            int r = 0;
            while (r < rows) {
                if (grid[r][c] == WHITE) {
                    int start = r;
                    while (r < rows && grid[r][c] == WHITE) r++;
                    if (r - start >= 2) {
                        int[] cells = new int[r - start];
                        for (int rr = start; rr < r; rr++) cells[rr - start] = Cells.of(rr, c);
                        slots.add(cells);
                    }
                } else {
                    r++;
                }
            }
        }
        return slots;
    }

    public static int indexOfSlot(List<int[]> slots, int[] cells) {
        for (int i = 0; i < slots.size(); i++) if (java.util.Arrays.equals(slots.get(i), cells)) return i;
        return -1;
    }

    // ---------------------------------------------------------------- pattern generation

    static boolean newBlackCellBreaksLockedSlot(char[][] grid, int rows, int cols, int r, int c, DualIndex index,
                                                Map<Integer, Character> locked, LengthSets available) {
        if (index == null || ((locked == null || locked.isEmpty()) && available == null)) return false;
        int[][] dirs = {{0, -1}, {0, 1}, {-1, 0}, {1, 0}};
        for (int[] d : dirs) {
            List<Integer> run = new ArrayList<>();
            int rr = r + d[0], cc = c + d[1];
            while (rr >= 0 && rr < rows && cc >= 0 && cc < cols && grid[rr][cc] == WHITE) {
                run.add(Cells.of(rr, cc));
                rr += d[0];
                cc += d[1];
            }
            if (d[0] < 0 || d[1] < 0) java.util.Collections.reverse(run);
            int length = run.size();
            if (length < 2) continue;
            int[] cells = run.stream().mapToInt(Integer::intValue).toArray();
            if (available != null && !available.forCells(cells).contains(length)) return true;
            if (locked != null && !locked.isEmpty()) {
                int lockedCount = 0;
                for (int cell : cells) if (locked.containsKey(cell)) lockedCount++;
                if (lockedCount > 0 && lockedCount < length
                        && Words.slotCandidateCount(index, length, cells, locked) < PREFILL_LOCKED_MIN_WORD_COUNT) {
                    return true;
                }
            }
        }
        return false;
    }

    /**
     * Indices into {@code remaining} (shuffled order kept) of the candidates lying both in a column and in a row
     * holding the fewest black cells, the minimum being taken over the rows/columns that still own a candidate.
     * When no candidate sits at the intersection of such a row and such a column, both bounds are raised together,
     * one black cell at a time, until one does.
     */
    static List<Integer> leastLoadedPool(List<Integer> remaining, int[] rowBlack, int[] colBlack) {
        int rowMin = Integer.MAX_VALUE, colMin = Integer.MAX_VALUE;
        for (int cell : remaining) {
            rowMin = Math.min(rowMin, rowBlack[Cells.r(cell)]);
            colMin = Math.min(colMin, colBlack[Cells.c(cell)]);
        }
        for (int slack = 0; ; slack++) {
            List<Integer> pool = new ArrayList<>();
            for (int i = 0; i < remaining.size(); i++) {
                int cell = remaining.get(i);
                if (rowBlack[Cells.r(cell)] <= rowMin + slack && colBlack[Cells.c(cell)] <= colMin + slack) pool.add(i);
            }
            if (!pool.isEmpty()) return pool;
        }
    }

    /** Returns the cells still unplaced (rejected ones, then untried ones). */
    static List<Integer> placeBlackCells(char[][] grid, int rows, int cols, int[] rowBlack, int[] colBlack,
                                         List<Integer> candidates, int target, int placed, DualIndex index,
                                         Map<Integer, Character> locked, LengthSets available,
                                         boolean forbidAdjacency) {
        final int window = 32;
        List<Integer> rejected = new ArrayList<>();
        List<Integer> remaining = candidates;
        while (!remaining.isEmpty() && placed < target) {
            List<Integer> pool = leastLoadedPool(remaining, rowBlack, colBlack);
            List<Integer> order = new ArrayList<>(pool.subList(0, Math.min(window, pool.size())));
            final List<Integer> rem = remaining;
            order.sort((a, b) -> Integer.compare(rowBlack[Cells.r(rem.get(a))] + colBlack[Cells.c(rem.get(a))],
                    rowBlack[Cells.r(rem.get(b))] + colBlack[Cells.c(rem.get(b))]));
            List<Integer> nonAdjacent = new ArrayList<>();
            for (int i : order) {
                int cell = remaining.get(i);
                if (!hasBlackNeighbor(grid, rows, cols, Cells.r(cell), Cells.c(cell))) nonAdjacent.add(i);
            }
            Integer chosen = null;
            for (int minFree = STRUCTURAL_MIN_INTERIOR_FREE; minFree > 0 && chosen == null; minFree--) {
                chosen = firstValid(grid, rows, cols, remaining, nonAdjacent, minFree, index, locked, available);
            }
            if (chosen == null && !forbidAdjacency) {
                for (int minFree = STRUCTURAL_MIN_INTERIOR_FREE; minFree > 0 && chosen == null; minFree--) {
                    chosen = firstValid(grid, rows, cols, remaining, order, minFree, index, locked, available);
                }
            }
            if (chosen == null) {
                rejected.add(remaining.remove((int) order.get(0)));
                continue;
            }
            int cell = remaining.remove((int) chosen);
            int r = Cells.r(cell), c = Cells.c(cell);
            grid[r][c] = BLACK;
            rowBlack[r]++;
            colBlack[c]++;
            placed++;
        }
        List<Integer> out = new ArrayList<>(rejected);
        out.addAll(remaining);
        return out;
    }

    private static Integer firstValid(char[][] grid, int rows, int cols, List<Integer> remaining, List<Integer> indices,
                                      int minFree, DualIndex index, Map<Integer, Character> locked, LengthSets available) {
        for (int idx : indices) {
            int cell = remaining.get(idx);
            int r = Cells.r(cell), c = Cells.c(cell);
            if (grid[r][c] == BLACK) continue;
            if (newBlackCellBreaksLockedSlot(grid, rows, cols, r, c, index, locked, available)) continue;
            grid[r][c] = BLACK;
            boolean ok = isStructurallyValid(grid, rows, cols, minFree);
            grid[r][c] = WHITE;
            if (ok) return idx;
        }
        return null;
    }

    static int[] slotWithInsufficientCandidates(char[][] grid, int rows, int cols, LengthSets available,
                                                DualIndex index, Map<Integer, Character> locked,
                                                Set<Cells.Key> skip) {
        for (int[] slot : extractSlots(grid, rows, cols)) {
            if (skip != null && skip.contains(Cells.key(slot))) continue;
            int length = slot.length;
            if (!available.forCells(slot).contains(length)) return slot;
            if (locked != null && !locked.isEmpty()) {
                int lockedCount = 0;
                for (int cell : slot) if (locked.containsKey(cell)) lockedCount++;
                if (lockedCount > 0 && lockedCount < length
                        && Words.slotCandidateCount(index, length, slot, locked) < PREFILL_LOCKED_MIN_WORD_COUNT) {
                    return slot;
                }
            }
        }
        return null;
    }

    static boolean removeACrossingWord(int[] slot, char[][] grid, int rows, int cols, Map<Integer, Character> locked,
                                       Rng rng) {
        if (locked == null || locked.isEmpty()) return false;
        Set<Integer> slotCells = new HashSet<>();
        for (int c : slot) slotCells.add(c);
        List<int[]> candidates = new ArrayList<>();
        for (int[] other : extractSlots(grid, rows, cols)) {
            if (java.util.Arrays.equals(other, slot)) continue;
            boolean shares = false, allLocked = true;
            for (int c : other) {
                if (slotCells.contains(c)) shares = true;
                if (!locked.containsKey(c)) allLocked = false;
            }
            if (shares && allLocked) candidates.add(other);
        }
        if (candidates.isEmpty()) return false;
        if (rng != null) rng.shuffle(candidates);
        for (int cell : candidates.get(0)) locked.remove(cell);
        return true;
    }

    static List<Integer> prefillUnfillableSlots(char[][] grid, int rows, int cols, int[] rowBlack, int[] colBlack,
                                                List<Integer> candidates, LengthSets available, DualIndex index,
                                                Map<Integer, Character> locked, Rng rng, double fillObjectiveFraction,
                                                boolean forbidAdjacency) {
        Set<Cells.Key> unfixable = new HashSet<>();
        List<Object[]> footprints = new ArrayList<>();
        while (!candidates.isEmpty()) {
            int[] slot = slotWithInsufficientCandidates(grid, rows, cols, available, index, locked, unfixable);
            if (slot == null) break;
            int length = slot.length;
            boolean isLengthProblem = !available.forCells(slot).contains(length);
            Set<Integer> slotSet = new HashSet<>();
            for (int c : slot) slotSet.add(c);
            Object[] footprint = null;
            for (Object[] fp : footprints) {
                @SuppressWarnings("unchecked")
                Set<Integer> fpCells = (Set<Integer>) fp[0];
                if (fpCells.containsAll(slotSet)) {
                    footprint = fp;
                    break;
                }
            }
            if (footprint == null) {
                footprint = new Object[]{slotSet, 0};
                footprints.add(footprint);
            }
            int zoneWhiteCount = ((Set<?>) footprint[0]).size();
            Set<Integer> candidateSet = new HashSet<>(candidates);
            List<Integer> cellsInSlot = new ArrayList<>();
            for (int c : slot) if (candidateSet.contains(c)) cellsInSlot.add(c);
            if (rng != null) rng.shuffle(cellsInSlot);
            List<Integer> options = new ArrayList<>(cellsInSlot);
            options.sort((a, b) -> Integer.compare(rowBlack[Cells.r(a)] + colBlack[Cells.c(a)],
                    rowBlack[Cells.r(b)] + colBlack[Cells.c(b)]));
            int zoneBudget = Math.max(PREFILL_ZONE_BLACK_BUDGET_FLOOR, (int) (fillObjectiveFraction * zoneWhiteCount));
            boolean withinBudget = isLengthProblem || zoneWhiteCount == 0 || ((int) footprint[1]) + 1 <= zoneBudget;
            boolean placedOne = false;
            if (withinBudget) {
                List<Integer> nonAdjacent = new ArrayList<>();
                for (int cell : options) {
                    if (!hasBlackNeighbor(grid, rows, cols, Cells.r(cell), Cells.c(cell))) nonAdjacent.add(cell);
                }
                List<Integer> ordered = new ArrayList<>(nonAdjacent);
                if (!forbidAdjacency) {
                    Set<Integer> na = new HashSet<>(nonAdjacent);
                    for (int cell : options) if (!na.contains(cell)) ordered.add(cell);
                }
                for (int cell : ordered) {
                    int r = Cells.r(cell), c = Cells.c(cell);
                    grid[r][c] = BLACK;
                    if (isStructurallyValid(grid, rows, cols, 1)) {
                        rowBlack[r]++;
                        colBlack[c]++;
                        candidates.remove(Integer.valueOf(cell));
                        footprint[1] = ((int) footprint[1]) + 1;
                        placedOne = true;
                        break;
                    }
                    grid[r][c] = WHITE;
                }
            }
            if (placedOne) continue;
            if (!isLengthProblem && removeACrossingWord(slot, grid, rows, cols, locked, rng)) continue;
            unfixable.add(Cells.key(slot));
        }
        return candidates;
    }

    public static char[][] makePattern(int rows, int cols, double blackRatio, Rng rng, LengthSets available,
                                       char[][] seedGrid, Map<Integer, Character> lockedLetters, DualIndex index,
                                       double blackEnrichmentFraction) {
        Map<Integer, Character> locked = lockedLetters == null || lockedLetters.isEmpty() ? lockedLetters
                : new LinkedHashMap<>(lockedLetters);
        char[][] grid;
        int[] rowBlack = new int[rows], colBlack = new int[cols];
        List<Integer> candidates = new ArrayList<>();
        Set<Integer> lockedSet = locked == null ? Set.of() : locked.keySet();
        if (seedGrid != null) {
            grid = copy(seedGrid);
            for (int r = 0; r < rows; r++) {
                for (int c = 0; c < cols; c++) {
                    if (grid[r][c] == BLACK) {
                        rowBlack[r]++;
                        colBlack[c]++;
                    } else if (grid[r][c] == WHITE && !lockedSet.contains(Cells.of(r, c))) {
                        candidates.add(Cells.of(r, c));
                    }
                }
            }
        } else {
            grid = blank(rows, cols);
            for (int r = 0; r < rows; r++) {
                for (int c = 0; c < cols; c++) if (!lockedSet.contains(Cells.of(r, c))) candidates.add(Cells.of(r, c));
            }
        }
        rng.shuffle(candidates);
        double fillObjective = Math.max(blackRatio, blackEnrichmentFraction);
        if (available != null) {
            candidates = prefillUnfillableSlots(grid, rows, cols, rowBlack, colBlack, candidates, available, index,
                    locked, rng, fillObjective, true);
        }
        int placed = countBlack(grid);
        int target = (int) Math.max(placed, Math.max(Math.rint(rows * cols * blackRatio),
                Math.rint(blackEnrichmentFraction * rows * cols)));
        placeBlackCells(grid, rows, cols, rowBlack, colBlack, candidates, target, placed, index, locked, available, true);
        if (available != null && locked != null && !locked.isEmpty()) {
            prefillUnfillableSlots(grid, rows, cols, rowBlack, colBlack, candidates, available, index, locked, rng,
                    fillObjective, true);
        }
        return grid;
    }

    // ---------------------------------------------------------------- letters grids

    public static char[][] buildLettersGrid(int rows, int cols, List<int[]> slots, String[] assignment) {
        char[][] letters = allBlack(rows, cols);
        for (int i = 0; i < slots.size(); i++) {
            String w = assignment[i];
            if (w == null) continue;
            int[] cells = slots.get(i);
            for (int p = 0; p < cells.length; p++) letters[Cells.r(cells[p])][Cells.c(cells[p])] = w.charAt(p);
        }
        return letters;
    }

    /** Returns {letters, forcedCellsSorted (List<Integer>), coveredCount}. */
    public static Object[] buildPartialLettersGrid(char[][] grid, List<int[]> slots, String[] assignment,
                                                   Map<Integer, Character> forced, Map<Integer, Character> locked) {
        char[][] letters = copy(grid);
        Set<Integer> covered = new HashSet<>();
        for (int i = 0; i < slots.size(); i++) {
            String w = assignment[i];
            if (w == null) continue;
            int[] cells = slots.get(i);
            for (int p = 0; p < cells.length; p++) {
                letters[Cells.r(cells[p])][Cells.c(cells[p])] = w.charAt(p);
                covered.add(cells[p]);
            }
        }
        if (locked != null) {
            locked.forEach((cell, ch) -> {
                if (!covered.contains(cell)) letters[Cells.r(cell)][Cells.c(cell)] = ch;
            });
        }
        List<Integer> forcedSorted = new ArrayList<>();
        if (forced != null && !forced.isEmpty()) {
            forced.forEach((cell, ch) -> {
                if (!covered.contains(cell)) letters[Cells.r(cell)][Cells.c(cell)] = ch;
            });
            forcedSorted.addAll(new java.util.TreeSet<>(forced.keySet()));
        }
        return new Object[]{letters, forcedSorted, covered.size()};
    }

    public static Map<Integer, List<Integer>> cellToSlotIndices(List<int[]> slots) {
        Map<Integer, List<Integer>> m = new LinkedHashMap<>();
        for (int i = 0; i < slots.size(); i++) {
            for (int cell : slots.get(i)) m.computeIfAbsent(cell, k -> new ArrayList<>()).add(i);
        }
        return m;
    }

    public static String wordAt(int[] cells, Map<Integer, Character> known) {
        StringBuilder sb = new StringBuilder(cells.length);
        for (int c : cells) {
            Character ch = known.get(c);
            if (ch == null) return null;
            sb.append(ch);
        }
        return sb.toString();
    }

    public static boolean allKnown(int[] cells, Map<Integer, Character> known) {
        for (int c : cells) if (!known.containsKey(c)) return false;
        return true;
    }

    public static boolean allIn(int[] cells, Collection<Integer> set) {
        for (int c : cells) if (!set.contains(c)) return false;
        return true;
    }

    public static Set<Integer> cellSet(int[] cells) {
        Set<Integer> s = new LinkedHashSet<>();
        for (int c : cells) s.add(c);
        return s;
    }
}
