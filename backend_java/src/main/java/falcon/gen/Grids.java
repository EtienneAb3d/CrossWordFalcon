package falcon.gen;

import falcon.gen.Words.DualIndex;
import falcon.gen.Words.LengthSets;

import java.util.ArrayList;
import java.util.Collection;
import java.util.HashMap;
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
    public static final int STRUCTURAL_MIN_INTERIOR_FREE = 6;
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
     * Number of black cells {@link NearestBlacks#score} averages over: the closest aligned one in each of the four
     * directions (the grid's edges included, so always four), then the closest non-aligned ones up to this total.
     */
    static final int BLACK_DISTANCE_NEIGHBORS = 7;

    /**
     * A candidate's closest black cells: the smallest {@link #blackDistanceSq} per direction (left, right, up, down;
     * -1 while none) and the {@code BLACK_DISTANCE_NEIGHBORS - 4} smallest among non-aligned cells, ascending.
     */
    static final class NearestBlacks {
        final long[] aligned = {-1, -1, -1, -1};
        final List<Long> others = new ArrayList<>();

        /** Adds the black cell (br, bc) seen from (r, c); returns whether anything changed. */
        boolean record(int r, int c, int br, int bc) {
            long d = blackDistanceSq(r, c, br, bc);
            int direction = br == r ? (bc < c ? 0 : 1) : bc == c ? (br < r ? 2 : 3) : -1;
            if (direction >= 0) {
                if (aligned[direction] < 0 || d < aligned[direction]) {
                    aligned[direction] = d;
                    return true;
                }
                return false;
            }
            int keep = BLACK_DISTANCE_NEIGHBORS - aligned.length;
            if (others.size() >= keep && d >= others.get(others.size() - 1)) return false;
            int k = 0;
            while (k < others.size() && others.get(k) <= d) k++;
            others.add(k, d);
            while (others.size() > keep) others.remove(others.size() - 1);
            return true;
        }

        /**
         * {@link #placeBlackCells}' ranking criterion: the mean, over these black cells, of the square root of the
         * weighted distance (the aligned factor applied before the root).
         */
        double score() {
            double sum = 0;
            int n = 0;
            for (long d : aligned) {
                if (d >= 0) {
                    sum += Math.sqrt(Math.sqrt((double) d));
                    n++;
                }
            }
            for (long d : others) {
                sum += Math.sqrt(Math.sqrt((double) d));
                n++;
            }
            return n == 0 ? Double.POSITIVE_INFINITY : sum / n;
        }
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
     * {@link #placeBlackCells}' draw window, in percent, and its widening step: the share of the white runs
     * ({@link #runScore}-ranked) the draw is restricted to, among their valid cells the share with the highest
     * {@link #crossingLengthScore}, and among those the share farthest from every black cell placed.
     */
    static final int BLACK_DRAW_WINDOW_PERCENT = 5;

    /**
     * Black-cell symmetries of the "Symétrie" selector ({@link #makePattern}'s {@code symmetry},
     * {@link #symmetryCells}): every black cell the ratio draw places brings its images under the chosen symmetry.
     */
    public static final List<String> BLACK_SYMMETRIES =
            List.of("none", "horizontal", "vertical", "diagonal", "bidirectional", "rotation");

    /**
     * The cells a black cell at (r, c) brings with it under {@code symmetry}, (r, c) included, sorted: "horizontal"
     * mirrors the column, "vertical" the row, "diagonal" both, "bidirectional" all three images, "rotation" — on a
     * square grid only, "bidirectional" otherwise — its three quarter-turn images about the grid's center,
     * (c, cols-1-r), (rows-1-r, cols-1-c) and (rows-1-c, r).
     */
    public static List<Integer> symmetryCells(int rows, int cols, int r, int c, String symmetry) {
        int w = cols - 1, h = rows - 1;
        String sym = "rotation".equals(symmetry) && rows != cols ? "bidirectional" : symmetry == null ? "none" : symmetry;
        java.util.TreeSet<Integer> out = new java.util.TreeSet<>();
        out.add(Cells.of(r, c));
        switch (sym) {
            case "horizontal" -> out.add(Cells.of(r, w - c));
            case "vertical" -> out.add(Cells.of(h - r, c));
            case "diagonal" -> out.add(Cells.of(h - r, w - c));
            case "bidirectional" -> {
                out.add(Cells.of(r, w - c));
                out.add(Cells.of(h - r, c));
                out.add(Cells.of(h - r, w - c));
            }
            case "rotation" -> {
                out.add(Cells.of(c, w - r));
                out.add(Cells.of(h - r, w - c));
                out.add(Cells.of(h - c, r));
            }
            default -> { }
        }
        return new ArrayList<>(out);
    }

    /**
     * {@link #makePattern}'s short-slot limit: once the ratio draw has reached its target, a pattern holding more
     * than {@link #SHORT_SLOT_MAX_COUNT} slots of at most {@link #SHORT_SLOT_MAX_LENGTH} letters (across and down, a
     * slot touching the border never counted) has the black cells this draw placed that bound them reopened, and the
     * draw runs again to reach the target — at most {@link #SHORT_SLOT_REDRAW_MAX_ROUNDS} times.
     */
    static final int SHORT_SLOT_MAX_LENGTH = 3;
    static final int SHORT_SLOT_MAX_COUNT = 10;
    static final int SHORT_SLOT_REDRAW_MAX_ROUNDS = 10;

    /**
     * Number of slots of at most {@link #SHORT_SLOT_MAX_LENGTH} letters (a slot touching the grid's border not
     * counted); the cells of {@code removable} that are the black cell right before or right after one of them are
     * added to {@code bounding}.
     */
    static int shortSlotBoundingBlacks(char[][] grid, int rows, int cols, Set<Integer> removable,
                                       Set<Integer> bounding) {
        int count = 0;
        for (int[] slot : extractSlots(grid, rows, cols)) {
            if (slot.length > SHORT_SLOT_MAX_LENGTH) continue;
            int r0 = Cells.r(slot[0]), c0 = Cells.c(slot[0]);
            int r1 = Cells.r(slot[slot.length - 1]), c1 = Cells.c(slot[slot.length - 1]);
            int dr = r0 == r1 ? 0 : 1, dc = r0 == r1 ? 1 : 0;
            boolean touchesBorder = dr == 0 ? c0 == 0 || c1 == cols - 1 : r0 == 0 || r1 == rows - 1;
            if (touchesBorder) continue;
            count++;
            int before = r0 - dr >= 0 && c0 - dc >= 0 ? Cells.of(r0 - dr, c0 - dc) : -1;
            int after = r1 + dr < rows && c1 + dc < cols ? Cells.of(r1 + dr, c1 + dc) : -1;
            if (before >= 0 && removable.contains(before)) bounding.add(before);
            if (after >= 0 && removable.contains(after)) bounding.add(after);
        }
        return count;
    }

    /** Number of items a {@code percent} % window keeps out of {@code count} (at least one, at most all). */
    static int windowSize(int count, int percent) {
        return Math.min(count, Math.max(1, (int) Math.ceil(count * percent / 100.0)));
    }

    /**
     * Per non-black cell, its across (index 0) and down (index 1) run — the maximal run of non-black cells holding it
     * in that direction, a single cell included.
     */
    static Map<Integer, int[][]> cellRuns(char[][] grid, int rows, int cols) {
        Map<Integer, int[][]> runAt = new HashMap<>();
        for (int direction = 0; direction < 2; direction++) {
            int outer = direction == 0 ? rows : cols, inner = direction == 0 ? cols : rows;
            for (int a = 0; a < outer; a++) {
                List<Integer> run = new ArrayList<>();
                for (int b = 0; b <= inner; b++) {
                    int r = direction == 0 ? a : b, c = direction == 0 ? b : a;
                    if (b < inner && grid[r][c] != BLACK) {
                        run.add(Cells.of(r, c));
                    } else if (!run.isEmpty()) {
                        int[] cells = run.stream().mapToInt(Integer::intValue).toArray();
                        for (int member : cells) runAt.computeIfAbsent(member, k -> new int[2][])[direction] = cells;
                        run.clear();
                    }
                }
            }
        }
        return runAt;
    }

    /**
     * Updates {@code runAt} ({@link #cellRuns}) for {@code cell} just blackened: its across and down runs are each
     * replaced, for their other cells, by the piece on their side of it. Returns the two runs it held before.
     */
    static int[][] splitCellRuns(Map<Integer, int[][]> runAt, int cell) {
        int[][] old = runAt.remove(cell);
        for (int direction = 0; direction < 2; direction++) {
            int[] run = old[direction];
            int k = 0;
            while (run[k] != cell) k++;
            int[][] pieces = {java.util.Arrays.copyOfRange(run, 0, k),
                    java.util.Arrays.copyOfRange(run, k + 1, run.length)};
            for (int[] piece : pieces) {
                for (int member : piece) runAt.get(member)[direction] = piece;
            }
        }
        return old;
    }

    /**
     * Reach of {@link #crossingLengthScore} around a cell, in each of the four directions: 3 = the cross of the 7x7
     * square centered on it.
     */
    static final int CROSSING_SCORE_RADIUS = 3;

    private static final int[][] CROSS_DIRECTIONS = {{0, -1}, {0, 1}, {-1, 0}, {1, 0}};

    /**
     * The cells {@link #crossingLengthScore} measures around the white cell (r, c): itself, then up to
     * {@link #CROSSING_SCORE_RADIUS} cells in each of the four directions, each direction stopping at the first black
     * cell or the grid's edge (at most 13 cells).
     */
    static List<Integer> crossingScoreCells(Map<Integer, int[][]> runAt, int r, int c) {
        List<Integer> cells = new ArrayList<>();
        cells.add(Cells.of(r, c));
        for (int[] d : CROSS_DIRECTIONS) {
            for (int k = 1; k <= CROSSING_SCORE_RADIUS; k++) {
                int rr = r + d[0] * k, cc = c + d[1] * k;
                if (rr < 0 || cc < 0 || !runAt.containsKey(Cells.of(rr, cc))) break;
                cells.add(Cells.of(rr, cc));
            }
        }
        return cells;
    }

    /**
     * {@link #placeBlackCells}' score of the white cell (r, c): the sum of the lengths of the words (runs of at least
     * 2 cells, across and down, each counted once) crossing the cells of its cross ({@link #crossingScoreCells}: up to
     * {@link #CROSSING_SCORE_RADIUS} cells in each direction, stopping at a black cell), multiplied by the full cross's
     * cell count (1 + 4 x {@link #CROSSING_SCORE_RADIUS}) over the count of its cells lying inside the grid, so a cell
     * near the edge is not underrated.
     */
    static double crossingLengthScore(Map<Integer, int[][]> runAt, int rows, int cols, int r, int c) {
        int reach = CROSSING_SCORE_RADIUS;
        int inside = 1 + Math.min(reach, c) + Math.min(reach, cols - 1 - c) + Math.min(reach, r)
                + Math.min(reach, rows - 1 - r);
        Set<int[]> seen = java.util.Collections.newSetFromMap(new java.util.IdentityHashMap<>());
        int total = 0;
        for (int cell : crossingScoreCells(runAt, r, c)) {
            for (int[] run : runAt.get(cell)) {
                if (run.length >= 2 && seen.add(run)) total += run.length;
            }
        }
        return (double) (total * (1 + 4 * reach)) / inside;
    }

    /**
     * Every run of {@code runAt} ({@link #cellRuns}) once, the same arrays: the across runs in row-major order, then
     * the down runs in column-major order.
     */
    static List<int[]> orderedRuns(Map<Integer, int[][]> runAt, int rows, int cols) {
        List<int[]> runs = new ArrayList<>();
        for (int direction = 0; direction < 2; direction++) {
            int outer = direction == 0 ? rows : cols, inner = direction == 0 ? cols : rows;
            for (int a = 0; a < outer; a++) {
                for (int b = 0; b < inner; b++) {
                    int cell = direction == 0 ? Cells.of(a, b) : Cells.of(b, a);
                    int[][] held = runAt.get(cell);
                    if (held != null && held[direction][0] == cell) runs.add(held[direction]);
                }
            }
        }
        return runs;
    }

    /**
     * {@code runs} ({@link #orderedRuns}) once {@code cell} was blackened and {@code runAt} updated by
     * {@link #splitCellRuns} (which returned {@code oldRuns}): each of the two runs holding it is replaced, in place
     * in the order, by its non-empty pieces on either side of it, the arrays {@code runAt} now holds.
     */
    static List<int[]> splitRunList(List<int[]> runs, Map<Integer, int[][]> runAt, int[][] oldRuns, int cell) {
        Map<int[], List<int[]>> replaced = new java.util.IdentityHashMap<>();
        for (int direction = 0; direction < 2; direction++) {
            int[] run = oldRuns[direction];
            int k = 0;
            while (run[k] != cell) k++;
            List<int[]> pieces = new ArrayList<>(2);
            if (k > 0) pieces.add(runAt.get(run[0])[direction]);
            if (k < run.length - 1) pieces.add(runAt.get(run[run.length - 1])[direction]);
            replaced.put(run, pieces);
        }
        List<int[]> out = new ArrayList<>(runs.size() + 2);
        for (int[] run : runs) {
            List<int[]> pieces = replaced.get(run);
            if (pieces == null) out.add(run);
            else out.addAll(pieces);
        }
        return out;
    }

    /** {@link #placeBlackCells}' ranking score of a white run: the sum of its cells' {@link #crossingLengthScore}. */
    static double runScore(int[] run, Map<Integer, Double> cellScore) {
        double total = 0.0;
        for (int cell : run) total += cellScore.get(cell);
        return total;
    }

    /**
     * Ratio-based black-cell placement: every white cell is scored by {@link #crossingLengthScore} (the lengths of
     * the words crossing its cross, up to 3 cells in each direction short of a black cell) and every white run (across and down, single cells included)
     * by the sum of its cells' scores ({@link #runScore}), both kept up to date as each black cell splits its across
     * and down runs; each draw ranks the runs by decreasing score (ties in random order), keeps the
     * {@link #BLACK_DRAW_WINDOW_PERCENT} % best, keeps their candidates satisfying the hard constraints, then the same
     * percentage of those with the highest {@link #crossingLengthScore} (ties keeping the shuffled order), ranks those
     * by spread score (highest first, ties keeping the shuffled order) and draws at random among the same percentage
     * best; with no valid cell, the percentage grows by the same step. Every run selected without a valid cell
     * relaxes {@code minInteriorFree} one step (down to 1), then accepts adjacency unless {@code forbidAdjacency};
     * failing that, it stops short of {@code target}. With a {@code symmetry} ({@link #BLACK_SYMMETRIES}, null or
     * "none" = none) the drawn cell is placed with its images ({@link #symmetryCells}), the hard constraints holding
     * for the whole group: every image still white is a candidate satisfying them on its own, no cell of the group
     * touches a black cell nor another cell of the group, and the grid with the whole group blackened stays
     * structurally valid. {@code candidates} is updated in place. Returns the cells still unplaced.
     */
    static List<Integer> placeBlackCells(char[][] grid, int rows, int cols, int[] rowBlack, int[] colBlack,
                                         List<Integer> candidates, int target, int placed, Rng rng,
                                         DualIndex index, Map<Integer, Character> locked, LengthSets available,
                                         boolean forbidAdjacency, String symmetry) {
        boolean symmetric = symmetry != null && !"none".equals(symmetry);
        List<Integer> remaining = candidates;
        Map<Integer, int[][]> runAt = cellRuns(grid, rows, cols);
        List<int[]> runs = orderedRuns(runAt, rows, cols);
        Map<Integer, Double> cellScore = new HashMap<>();
        for (int cell : runAt.keySet()) {
            cellScore.put(cell, crossingLengthScore(runAt, rows, cols, Cells.r(cell), Cells.c(cell)));
        }
        List<Integer> blacks = new ArrayList<>();
        for (int br = 0; br < rows; br++) {
            for (int bc = 0; bc < cols; bc++) if (grid[br][bc] == BLACK) blacks.add(Cells.of(br, bc));
        }
        // The grid's edges count as black cells: a ring of virtual black cells just outside the grid.
        List<int[]> virtualBlacks = new ArrayList<>();
        for (int bc = -1; bc <= cols; bc++) {
            virtualBlacks.add(new int[]{-1, bc});
            virtualBlacks.add(new int[]{rows, bc});
        }
        for (int br = 0; br < rows; br++) {
            virtualBlacks.add(new int[]{br, -1});
            virtualBlacks.add(new int[]{br, cols});
        }
        // Per candidate, parallel to remaining and updated per placement: its closest aligned and non-aligned black
        // cells, and the resulting score.
        List<NearestBlacks> nearest = new ArrayList<>();
        List<Double> dist = new ArrayList<>();
        for (int cell : remaining) {
            int r = Cells.r(cell), c = Cells.c(cell);
            NearestBlacks closest = new NearestBlacks();
            for (int b : blacks) closest.record(r, c, Cells.r(b), Cells.c(b));
            for (int[] b : virtualBlacks) closest.record(r, c, b[0], b[1]);
            nearest.add(closest);
            dist.add(closest.score());
        }
        while (!remaining.isEmpty() && placed < target) {
            Map<Integer, Integer> position = new HashMap<>();
            for (int i = 0; i < remaining.size(); i++) position.put(remaining.get(i), i);
            List<int[]> runOrder = new ArrayList<>(runs);
            rng.shuffle(runOrder);
            Map<int[], Double> runScores = new java.util.IdentityHashMap<>();
            for (int[] run : runOrder) runScores.put(run, runScore(run, cellScore));
            runOrder.sort((x, y) -> Double.compare(runScores.get(y), runScores.get(x)));
            Integer chosen = null;
            for (int adj = 0; adj < (forbidAdjacency ? 1 : 2) && chosen == null; adj++) {
                for (int minFree = STRUCTURAL_MIN_INTERIOR_FREE; minFree > 0 && chosen == null; minFree--) {
                    chosen = drawBlackCell(grid, rows, cols, remaining, dist, cellScore, runOrder, position, minFree,
                            adj == 1,
                            rng, index, locked, available, symmetric ? symmetry : null);
                }
            }
            if (chosen == null) break;
            int drawn = remaining.get(chosen);
            List<Integer> group = symmetric ? whiteImages(grid, rows, cols, drawn, symmetry) : List.of(drawn);
            for (int cell : group) {
                runs = placeOneBlackCell(grid, rowBlack, colBlack, remaining, dist, nearest, runAt, runs, cellScore,
                        cell);
                placed++;
            }
        }
        return new ArrayList<>(remaining);
    }

    /** The cells of {@code cell}'s symmetry group ({@link #symmetryCells}) that are not black yet. */
    private static List<Integer> whiteImages(char[][] grid, int rows, int cols, int cell, String symmetry) {
        List<Integer> out = new ArrayList<>();
        for (int image : symmetryCells(rows, cols, Cells.r(cell), Cells.c(cell), symmetry)) {
            if (grid[Cells.r(image)][Cells.c(image)] != BLACK) out.add(image);
        }
        return out;
    }

    /** Blackens the candidate {@code cell} of {@link #placeBlackCells}, keeping its parallel structures current. */
    private static List<int[]> placeOneBlackCell(char[][] grid, int[] rowBlack, int[] colBlack, List<Integer> remaining,
                                                 List<Double> dist, List<NearestBlacks> nearest,
                                                 Map<Integer, int[][]> runAt, List<int[]> runs,
                                                 Map<Integer, Double> cellScore, int cell) {
        int chosen = remaining.indexOf(cell);
        remaining.remove(chosen);
        dist.remove(chosen);
        nearest.remove(chosen);
        int r = Cells.r(cell), c = Cells.c(cell);
        int[][] oldRuns = splitCellRuns(runAt, cell);
        runs = splitRunList(runs, runAt, oldRuns, cell);
        cellScore.remove(cell);
        Set<Integer> rescored = new HashSet<>();
        for (int[] run : oldRuns) {
            for (int member : run) {
                for (int dr = -CROSSING_SCORE_RADIUS; dr <= CROSSING_SCORE_RADIUS; dr++) {
                    for (int dc = -CROSSING_SCORE_RADIUS; dc <= CROSSING_SCORE_RADIUS; dc++) {
                        int rr = Cells.r(member) + dr, cc = Cells.c(member) + dc;
                        if (rr >= 0 && cc >= 0) rescored.add(Cells.of(rr, cc));
                    }
                }
            }
        }
        for (int other : rescored) {
            if (cellScore.containsKey(other)) {
                cellScore.put(other, crossingLengthScore(runAt, grid.length, grid[0].length, Cells.r(other),
                        Cells.c(other)));
            }
        }
        for (int i = 0; i < remaining.size(); i++) {
            int other = remaining.get(i);
            if (nearest.get(i).record(Cells.r(other), Cells.c(other), r, c)) {
                dist.set(i, nearest.get(i).score());
            }
        }
        grid[r][c] = BLACK;
        rowBlack[r]++;
        colBlack[c]++;
        return runs;
    }

    private static boolean validBlackCell(char[][] grid, int rows, int cols, int r, int c, BlackCellValidity structure,
                                          boolean allowAdjacency, DualIndex index, Map<Integer, Character> locked,
                                          LengthSets available) {
        return grid[r][c] != BLACK
                && (allowAdjacency || !hasBlackNeighbor(grid, rows, cols, r, c))
                && !newBlackCellBreaksLockedSlot(grid, rows, cols, r, c, index, locked, available)
                && structure.validWithBlack(r, c);
    }

    /** {@link #validBlackCell} for the whole symmetry group of (r, c) (see {@link #placeBlackCells}). */
    private static boolean validBlackGroup(char[][] grid, int rows, int cols, int r, int c, BlackCellValidity structure,
                                           boolean allowAdjacency, DualIndex index, Map<Integer, Character> locked,
                                           LengthSets available, String symmetry, Map<Integer, Integer> position,
                                           int minFree) {
        if (!validBlackCell(grid, rows, cols, r, c, structure, allowAdjacency, index, locked, available)) return false;
        if (symmetry == null) return true;
        List<Integer> group = whiteImages(grid, rows, cols, Cells.of(r, c), symmetry);
        if (group.size() == 1) return true;
        for (int cell : group) {
            if (!position.containsKey(cell)) return false;
            if (!validBlackCell(grid, rows, cols, Cells.r(cell), Cells.c(cell), structure, allowAdjacency, index, locked,
                    available)) {
                return false;
            }
        }
        for (int cell : group) grid[Cells.r(cell)][Cells.c(cell)] = BLACK;
        try {
            if (!allowAdjacency) {
                for (int cell : group) if (hasBlackNeighbor(grid, rows, cols, Cells.r(cell), Cells.c(cell))) return false;
            }
            return isStructurallyValid(grid, rows, cols, minFree);
        } finally {
            for (int cell : group) grid[Cells.r(cell)][Cells.c(cell)] = WHITE;
        }
    }

    private static Integer drawBlackCell(char[][] grid, int rows, int cols, List<Integer> remaining, List<Double> dist,
                                         Map<Integer, Double> cellScore, List<int[]> runOrder, Map<Integer, Integer> position, int minFree,
                                         boolean allowAdjacency, Rng rng, DualIndex index,
                                         Map<Integer, Character> locked, LengthSets available, String symmetry) {
        BlackCellValidity structure = new BlackCellValidity(grid, rows, cols, minFree);
        Map<Integer, Boolean> checked = new HashMap<>(); // candidate index -> valid at this level, during this draw
        for (int percent = BLACK_DRAW_WINDOW_PERCENT; ; percent += BLACK_DRAW_WINDOW_PERCENT) {
            int runCount = windowSize(runOrder.size(), percent);
            java.util.TreeSet<Integer> inRuns = new java.util.TreeSet<>();
            for (int[] run : runOrder.subList(0, runCount)) {
                for (int cell : run) {
                    Integer i = position.get(cell);
                    if (i != null) inRuns.add(i);
                }
            }
            List<Integer> valid = new ArrayList<>();
            for (int i : inRuns) {
                Boolean ok = checked.get(i);
                if (ok == null) {
                    int cell = remaining.get(i);
                    ok = validBlackGroup(grid, rows, cols, Cells.r(cell), Cells.c(cell), structure, allowAdjacency,
                            index, locked, available, symmetry, position, minFree);
                    checked.put(i, ok);
                }
                if (ok) valid.add(i);
            }
            if (!valid.isEmpty()) {
                valid.sort((x, y) -> Double.compare(cellScore.get(remaining.get(y)), cellScore.get(remaining.get(x))));
                valid = new ArrayList<>(valid.subList(0, windowSize(valid.size(), percent)));
                valid.sort((x, y) -> Double.compare(dist.get(y), dist.get(x)));
                List<Integer> window = valid.subList(0, windowSize(valid.size(), percent));
                return window.get(rng.randrange(window.size()));
            }
            if (runCount == runOrder.size()) return null;
        }
    }

    /**
     * Whether blackening one white cell leaves the grid structurally valid — always exactly
     * {@link #isStructurallyValid} on the modified grid, without rescanning it: zones, isolated cells and the white
     * graph's articulation points (Tarjan) are computed once per grid state, each query re-examining only the row
     * and column of the cell.
     */
    static final class BlackCellValidity {
        private final char[][] grid;
        private final int rows, cols, minFree;
        private final int[][] hStart, hEnd, vStart, vEnd; // -1 for a non-white cell
        private final int[] badRows, badCols;
        private int totalBadRows, totalBadCols;
        private final Set<Integer> isolated = new HashSet<>();
        private final Set<Integer> articulations = new HashSet<>();
        private final List<Integer> componentSizes = new ArrayList<>();

        BlackCellValidity(char[][] grid, int rows, int cols, int minFree) {
            this.grid = grid;
            this.rows = rows;
            this.cols = cols;
            this.minFree = minFree;
            hStart = new int[rows][cols];
            hEnd = new int[rows][cols];
            vStart = new int[rows][cols];
            vEnd = new int[rows][cols];
            for (int[] row : hStart) java.util.Arrays.fill(row, -1);
            for (int[] row : vStart) java.util.Arrays.fill(row, -1);
            badRows = new int[rows];
            badCols = new int[cols];
            for (int r = 0; r < rows; r++) {
                int start = -1;
                for (int c = 0; c <= cols; c++) {
                    if (c < cols && grid[r][c] == WHITE) {
                        if (start < 0) start = c;
                    } else if (start >= 0) {
                        if (!zoneOk(start, c, cols)) badRows[r]++;
                        for (int cc = start; cc < c; cc++) {
                            hStart[r][cc] = start;
                            hEnd[r][cc] = c;
                        }
                        start = -1;
                    }
                }
                if (badRows[r] > 0) totalBadRows++;
            }
            for (int c = 0; c < cols; c++) {
                int start = -1;
                for (int r = 0; r <= rows; r++) {
                    if (r < rows && grid[r][c] == WHITE) {
                        if (start < 0) start = r;
                    } else if (start >= 0) {
                        if (!zoneOk(start, r, rows)) badCols[c]++;
                        for (int rr = start; rr < r; rr++) {
                            vStart[rr][c] = start;
                            vEnd[rr][c] = r;
                        }
                        start = -1;
                    }
                }
                if (badCols[c] > 0) totalBadCols++;
            }
            for (int r = 0; r < rows; r++) {
                for (int c = 0; c < cols; c++) {
                    if (hStart[r][c] >= 0 && hEnd[r][c] - hStart[r][c] < 2 && vEnd[r][c] - vStart[r][c] < 2) {
                        isolated.add(Cells.of(r, c));
                    }
                }
            }
            componentsAndArticulations();
        }

        private boolean zoneOk(int start, int end, int length) {
            return end - start >= minFree || start == 0 || end == length;
        }

        private boolean white(int r, int c) {
            return r >= 0 && r < rows && c >= 0 && c < cols && hStart[r][c] >= 0;
        }

        private List<Integer> neighbors(int cell) {
            int r = Cells.r(cell), c = Cells.c(cell);
            List<Integer> out = new ArrayList<>(4);
            int[][] steps = {{1, 0}, {-1, 0}, {0, 1}, {0, -1}};
            for (int[] d : steps) if (white(r + d[0], c + d[1])) out.add(Cells.of(r + d[0], c + d[1]));
            return out;
        }

        private void componentsAndArticulations() {
            Map<Integer, Integer> disc = new HashMap<>(), low = new HashMap<>();
            int counter = 0;
            for (int r0 = 0; r0 < rows; r0++) {
                for (int c0 = 0; c0 < cols; c0++) {
                    int root = Cells.of(r0, c0);
                    if (hStart[r0][c0] < 0 || disc.containsKey(root)) continue;
                    int size = 1, rootChildren = 0;
                    disc.put(root, counter);
                    low.put(root, counter);
                    counter++;
                    // Frames: cell, parent (or -1), neighbor list, next neighbor position.
                    List<int[]> frames = new ArrayList<>();
                    List<List<Integer>> nbLists = new ArrayList<>();
                    frames.add(new int[]{root, -1, 0});
                    nbLists.add(neighbors(root));
                    while (!frames.isEmpty()) {
                        int top = frames.size() - 1;
                        int[] f = frames.get(top);
                        List<Integer> nbs = nbLists.get(top);
                        int cell = f[0], parent = f[1];
                        boolean advanced = false;
                        while (f[2] < nbs.size()) {
                            int nb = nbs.get(f[2]++);
                            if (nb == parent) continue;
                            Integer dn = disc.get(nb);
                            if (dn != null) {
                                low.put(cell, Math.min(low.get(cell), dn));
                            } else {
                                disc.put(nb, counter);
                                low.put(nb, counter);
                                counter++;
                                size++;
                                if (cell == root) rootChildren++;
                                frames.add(new int[]{nb, cell, 0});
                                nbLists.add(neighbors(nb));
                                advanced = true;
                                break;
                            }
                        }
                        if (advanced) continue;
                        frames.remove(top);
                        nbLists.remove(top);
                        if (parent >= 0) {
                            low.put(parent, Math.min(low.get(parent), low.get(cell)));
                            if (parent != root && low.get(cell) >= disc.get(parent)) articulations.add(parent);
                        }
                    }
                    if (rootChildren > 1) articulations.add(root);
                    componentSizes.add(size);
                }
            }
        }

        boolean validWithBlack(int r, int c) {
            if (hStart[r][c] < 0) {
                char previous = grid[r][c];
                grid[r][c] = BLACK;
                boolean ok = isStructurallyValid(grid, rows, cols, minFree);
                grid[r][c] = previous;
                return ok;
            }
            if (totalBadRows - (badRows[r] > 0 ? 1 : 0) > 0) return false;
            if (totalBadCols - (badCols[c] > 0 ? 1 : 0) > 0) return false;
            int h0 = hStart[r][c], h1 = hEnd[r][c], v0 = vStart[r][c], v1 = vEnd[r][c];
            if (badRows[r] - (zoneOk(h0, h1, cols) ? 0 : 1) > 0) return false;
            if (badCols[c] - (zoneOk(v0, v1, rows) ? 0 : 1) > 0) return false;
            if (c > h0 && !zoneOk(h0, c, cols)) return false;
            if (h1 > c + 1 && !zoneOk(c + 1, h1, cols)) return false;
            if (r > v0 && !zoneOk(v0, r, rows)) return false;
            if (v1 > r + 1 && !zoneOk(r + 1, v1, rows)) return false;
            for (int cell : isolated) {
                int ir = Cells.r(cell), ic = Cells.c(cell);
                boolean changed = (ir == r && ic >= h0 && ic < h1) || (ic == c && ir >= v0 && ir < v1);
                if (!changed) return false;
            }
            for (int cc = h0; cc < h1; cc++) {
                if (cc == c) continue;
                int piece = cc < c ? c - h0 : h1 - c - 1;
                if (piece < 2 && vEnd[r][cc] - vStart[r][cc] < 2) return false;
            }
            for (int rr = v0; rr < v1; rr++) {
                if (rr == r) continue;
                int piece = rr < r ? r - v0 : v1 - r - 1;
                if (piece < 2 && hEnd[rr][c] - hStart[rr][c] < 2) return false;
            }
            if (componentSizes.size() == 1) {
                return componentSizes.get(0) > 1 && !articulations.contains(Cells.of(r, c));
            }
            return componentSizes.size() == 2 && componentSizes.contains(1) && neighbors(Cells.of(r, c)).isEmpty();
        }
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
            // A cell of a corner 2x2 square (inCornerSquare) comes last,
            // tried only once every other cell of the slot has failed.
            List<Integer> options = new ArrayList<>(cellsInSlot);
            options.sort((a, b) -> {
                int ca = inCornerSquare(rows, cols, Cells.r(a), Cells.c(a)) ? 1 : 0;
                int cb = inCornerSquare(rows, cols, Cells.r(b), Cells.c(b)) ? 1 : 0;
                if (ca != cb) return Integer.compare(ca, cb);
                return Integer.compare(rowBlack[Cells.r(a)] + colBlack[Cells.c(a)],
                        rowBlack[Cells.r(b)] + colBlack[Cells.c(b)]);
            });
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
        return makePattern(rows, cols, blackRatio, rng, available, seedGrid, lockedLetters, index,
                blackEnrichmentFraction, null);
    }

    /**
     * {@link #makePattern} with the ratio draw's black-cell {@code symmetry} ("Symétrie", {@link #BLACK_SYMMETRIES},
     * null = none): every drawn cell comes with its images, and the short-slot limit reopens a bounding cell with its
     * images; pre-fill stays unpaired.
     */
    public static char[][] makePattern(int rows, int cols, double blackRatio, Rng rng, LengthSets available,
                                       char[][] seedGrid, Map<Integer, Character> lockedLetters, DualIndex index,
                                       double blackEnrichmentFraction, String symmetry) {
        boolean symmetric = symmetry != null && !"none".equals(symmetry);
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
        Set<Integer> drawn = new HashSet<>(ratioCandidates);
        placeBlackCells(grid, rows, cols, rowBlack, colBlack, ratioCandidates, target, placed, rng, index, locked, available,
                true, symmetry);
        drawn.removeAll(new HashSet<>(ratioCandidates));
        // Short-slot limit: once the target is reached, too many slots of at most SHORT_SLOT_MAX_LENGTH letters
        // reopen the drawn black cells bounding them, and the draw runs again to reach the target.
        for (int round = 0; round < SHORT_SLOT_REDRAW_MAX_ROUNDS; round++) {
            Set<Integer> bounding = new HashSet<>();
            int shortCount = shortSlotBoundingBlacks(grid, rows, cols, drawn, bounding);
            if (shortCount <= SHORT_SLOT_MAX_COUNT || bounding.isEmpty()) break;
            if (symmetric) {
                Set<Integer> closed = new HashSet<>();
                for (int cell : bounding) {
                    for (int image : symmetryCells(rows, cols, Cells.r(cell), Cells.c(cell), symmetry)) {
                        if (drawn.contains(image)) closed.add(image);
                    }
                }
                bounding = closed;
            }
            List<Integer> reopened = new ArrayList<>(new java.util.TreeSet<>(bounding));
            rng.shuffle(reopened);
            for (int cell : reopened) {
                int r = Cells.r(cell), c = Cells.c(cell);
                grid[r][c] = WHITE;
                rowBlack[r]--;
                colBlack[c]--;
            }
            drawn.removeAll(bounding);
            ratioCandidates.addAll(reopened);
            Set<Integer> before = new HashSet<>(ratioCandidates);
            placeBlackCells(grid, rows, cols, rowBlack, colBlack, ratioCandidates, target, countBlack(grid), rng, index,
                    locked, available, true, symmetry);
            before.removeAll(new HashSet<>(ratioCandidates));
            drawn.addAll(before);
        }
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
