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
    public static final int STRUCTURAL_MIN_INTERIOR_FREE = 4;
    /** Side of the corner squares the ratio-based ("Taux noir") draw never blackens. */
    public static final int CORNER_SQUARE_SIZE = 2;
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

    /** True for a cell of one of the grid's four corner 2x2 squares (never drawn by the ratio-based placement). */
    static boolean inCornerSquare(int rows, int cols, int r, int c) {
        return (r < CORNER_SQUARE_SIZE || r >= rows - CORNER_SQUARE_SIZE)
                && (c < CORNER_SQUARE_SIZE || c >= cols - CORNER_SQUARE_SIZE);
    }

    /**
     * {@link #blackDistanceSq} from (r, c) to the closest of {@code blacks} ({@code Long.MAX_VALUE} when there is
     * none) — {@link #placeBlackCells}' ranking criterion.
     */
    static long nearestBlackDistanceSq(List<Integer> blacks, int r, int c) {
        long best = Long.MAX_VALUE;
        for (int b : blacks) best = Math.min(best, blackDistanceSq(r, c, Cells.r(b), Cells.c(b)));
        return best;
    }

    /** Factor applied to the distance between two cells of the same row or column in {@link #blackDistanceSq}. */
    static final int BLACK_ALIGNED_DISTANCE_FACTOR = 10;

    /** Squared Euclidean distance, multiplied by {@link #BLACK_ALIGNED_DISTANCE_FACTOR} for a shared row or column. */
    static long blackDistanceSq(int r, int c, int br, int bc) {
        long dr = r - br, dc = c - bc;
        long d = dr * dr + dc * dc;
        if (dr == 0 || dc == 0) d *= (long) BLACK_ALIGNED_DISTANCE_FACTOR * BLACK_ALIGNED_DISTANCE_FACTOR;
        return d;
    }

    /**
     * {@link #placeBlackCells}' draw windows, in percent, and their widening step: the share of rows and of columns
     * holding the fewest black cells the draw is restricted to, and, within those, the share of candidates farthest
     * from every black cell already placed.
     */
    static final int BLACK_DRAW_WINDOW_PERCENT = 5;

    /** Number of items a {@code percent} % window keeps out of {@code count} (at least one, at most all). */
    static int windowSize(int count, int percent) {
        return Math.min(count, Math.max(1, (int) Math.ceil(count * percent / 100.0)));
    }

    /**
     * Ratio-based black-cell placement: each draw restricts the candidates to whole rows and whole columns among the
     * {@link #BLACK_DRAW_WINDOW_PERCENT} % rows and columns holding the fewest black cells (ties in random order),
     * ranks them by distance to the closest black cell (farthest first, ties keeping the shuffled order), draws at
     * random among the {@link #BLACK_DRAW_WINDOW_PERCENT} % farthest, redraws while the drawn cell breaks a hard
     * constraint, widens the distance window by the same step up to the whole restriction, then widens the row and
     * column windows and restarts the distance window. Every row and column failing relaxes {@code minInteriorFree}
     * one step (down to 1), then accepts adjacency unless {@code forbidAdjacency}; failing that, it stops short of
     * {@code target}. {@code candidates} is updated in place. Returns the cells still unplaced.
     */
    static List<Integer> placeBlackCells(char[][] grid, int rows, int cols, int[] rowBlack, int[] colBlack,
                                         List<Integer> candidates, int target, int placed, Rng rng,
                                         DualIndex index, Map<Integer, Character> locked, LengthSets available,
                                         boolean forbidAdjacency) {
        List<Integer> remaining = candidates;
        List<Integer> blacks = new ArrayList<>();
        for (int br = 0; br < rows; br++) {
            for (int bc = 0; bc < cols; bc++) if (grid[br][bc] == BLACK) blacks.add(Cells.of(br, bc));
        }
        // Squared distance of each candidate to its closest black cell, parallel to remaining, updated per placement.
        List<Long> dist = new ArrayList<>();
        for (int cell : remaining) dist.add(nearestBlackDistanceSq(blacks, Cells.r(cell), Cells.c(cell)));
        while (!remaining.isEmpty() && placed < target) {
            List<Integer> order = new ArrayList<>();
            for (int i = 0; i < remaining.size(); i++) order.add(i);
            order.sort((a, b) -> Long.compare(dist.get(b), dist.get(a)));
            Set<Integer> rowSet = new LinkedHashSet<>(), colSet = new LinkedHashSet<>();
            for (int cell : remaining) {
                rowSet.add(Cells.r(cell));
                colSet.add(Cells.c(cell));
            }
            List<Integer> rowOrder = leastLoaded(rowSet, rowBlack, rng);
            List<Integer> colOrder = leastLoaded(colSet, colBlack, rng);
            Integer chosen = null;
            for (int adj = 0; adj < (forbidAdjacency ? 1 : 2) && chosen == null; adj++) {
                for (int minFree = STRUCTURAL_MIN_INTERIOR_FREE; minFree > 0 && chosen == null; minFree--) {
                    Set<Integer> setAside = new HashSet<>();
                    for (int percent = BLACK_DRAW_WINDOW_PERCENT; chosen == null; percent += BLACK_DRAW_WINDOW_PERCENT) {
                        int rowCount = windowSize(rowOrder.size(), percent);
                        int colCount = windowSize(colOrder.size(), percent);
                        Set<Integer> selRows = new HashSet<>(rowOrder.subList(0, rowCount));
                        Set<Integer> selCols = new HashSet<>(colOrder.subList(0, colCount));
                        List<Integer> restricted = new ArrayList<>();
                        for (int i : order) {
                            int cell = remaining.get(i);
                            if (selRows.contains(Cells.r(cell)) || selCols.contains(Cells.c(cell))) restricted.add(i);
                        }
                        chosen = drawBlackCell(grid, rows, cols, remaining, restricted, minFree, adj == 1, setAside,
                                rng, index, locked, available);
                        if (rowCount == rowOrder.size() && colCount == colOrder.size()) break;
                    }
                }
            }
            if (chosen == null) break;
            int cell = remaining.remove((int) chosen);
            dist.remove((int) chosen);
            int r = Cells.r(cell), c = Cells.c(cell);
            for (int i = 0; i < remaining.size(); i++) {
                long d = blackDistanceSq(Cells.r(remaining.get(i)), Cells.c(remaining.get(i)), r, c);
                if (d < dist.get(i)) dist.set(i, d);
            }
            grid[r][c] = BLACK;
            rowBlack[r]++;
            colBlack[c]++;
            placed++;
        }
        return new ArrayList<>(remaining);
    }

    /** {@code lines} by increasing black-cell count, ties in random order. */
    private static List<Integer> leastLoaded(Set<Integer> lines, int[] counts, Rng rng) {
        List<Integer> out = new ArrayList<>(lines);
        rng.shuffle(out);
        out.sort((a, b) -> Integer.compare(counts[a], counts[b]));
        return out;
    }

    /** Distance window over {@code order}; {@code setAside} holds the cells already found invalid at this level. */
    private static Integer drawBlackCell(char[][] grid, int rows, int cols, List<Integer> remaining,
                                         List<Integer> order, int minFree, boolean allowAdjacency,
                                         Set<Integer> setAside, Rng rng, DualIndex index,
                                         Map<Integer, Character> locked, LengthSets available) {
        int percent = BLACK_DRAW_WINDOW_PERCENT;
        int start = 0;
        int n = order.size();
        while (start < n) {
            int size = windowSize(n, percent);
            List<Integer> window = new ArrayList<>();
            for (int i : order.subList(start, size)) if (!setAside.contains(i)) window.add(i);
            while (!window.isEmpty()) {
                int idx = window.remove(rng.randrange(window.size()));
                int cell = remaining.get(idx);
                int r = Cells.r(cell), c = Cells.c(cell);
                boolean ok = grid[r][c] != BLACK
                        && (allowAdjacency || !hasBlackNeighbor(grid, rows, cols, r, c))
                        && !newBlackCellBreaksLockedSlot(grid, rows, cols, r, c, index, locked, available);
                if (ok) {
                    grid[r][c] = BLACK;
                    ok = isStructurallyValid(grid, rows, cols, minFree);
                    grid[r][c] = WHITE;
                }
                if (ok) return idx;
                setAside.add(idx);
            }
            start = size;
            percent += BLACK_DRAW_WINDOW_PERCENT;
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
        // The ratio-based draw never blackens a corner 2x2 square; pre-fill and later repairs still may.
        List<Integer> ratioCandidates = new ArrayList<>();
        for (int cell : candidates) if (!inCornerSquare(rows, cols, Cells.r(cell), Cells.c(cell))) ratioCandidates.add(cell);
        placeBlackCells(grid, rows, cols, rowBlack, colBlack, ratioCandidates, target, placed, rng, index, locked, available,
                true);
        Set<Integer> stillCandidates = new HashSet<>(ratioCandidates);
        List<Integer> kept = new ArrayList<>();
        for (int cell : candidates) {
            if (stillCandidates.contains(cell) || inCornerSquare(rows, cols, Cells.r(cell), Cells.c(cell))) kept.add(cell);
        }
        candidates = kept;
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
                if (!covered.contains(cell) && letters[Cells.r(cell)][Cells.c(cell)] != BLACK) {
                    letters[Cells.r(cell)][Cells.c(cell)] = ch;
                }
            });
        }
        List<Integer> forcedSorted = new ArrayList<>();
        if (forced != null && !forced.isEmpty()) {
            // A seed can sit on a cell a search reshape has since turned black.
            forced.forEach((cell, ch) -> {
                if (!covered.contains(cell) && letters[Cells.r(cell)][Cells.c(cell)] != BLACK) {
                    letters[Cells.r(cell)][Cells.c(cell)] = ch;
                }
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
