package falcon.gen;

import falcon.GenerationCancelled;
import falcon.gen.Words.DualIndex;
import falcon.gen.Words.PW;

import java.util.ArrayList;
import java.util.Arrays;
import java.util.Collection;
import java.util.HashMap;
import java.util.HashSet;
import java.util.LinkedHashMap;
import java.util.LinkedHashSet;
import java.util.List;
import java.util.Map;
import java.util.Set;
import java.util.TreeSet;
import java.util.concurrent.atomic.AtomicBoolean;

import static falcon.gen.Grids.BLACK;
import static falcon.gen.Grids.WHITE;

/** Impossible-zone analysis and every cross-palier repair / cleanup pass
 * (mirrors the matching parts of crossword_gen.py). */
public final class Cleanup {
    private Cleanup() {}

    public static final double BLACK_CELL_INSTEAD_OF_REMOVAL_PROBABILITY = 1.0 / 10;
    public static final int PER_CYCLE_OPTIMIZATION_SAMPLE_SIZE = 50;
    public static final int WIDEN_BLACK_CELL_WINDOW = 40;
    public static final int WIDEN_PRIORITY_WORDS_LIMIT = 30;
    /** Words of each family a search node tries to reshape its slot for (mirrors RESHAPE_WORDS_PER_NODE). */
    public static final int RESHAPE_WORDS_PER_NODE = 5;
    /** Theme words get widening/shortening only while fewer than this many are placed. */
    public static final int THEME_RESHAPE_MAX_PLACED_WORDS = 5;

    /** Mirrors _theme_reshape_allowed. */
    public static boolean themeReshapeAllowed(Set<String> placedThemeWords) {
        return placedThemeWords.size() < THEME_RESHAPE_MAX_PLACED_WORDS;
    }
    public static final int SHORTEN_SLOT_WINDOW = (int) Math.max(1, Math.rint(Filler.FALLBACK_PHASE_BUDGET_FRACTION * WIDEN_BLACK_CELL_WINDOW));

    static Map<Integer, Character> subKnown(int[] cells, Map<Integer, Character> known) {
        Map<Integer, Character> m = new HashMap<>();
        for (int c : cells) {
            Character ch = known.get(c);
            if (ch != null) m.put(c, ch);
        }
        return m;
    }

    static Set<String> usedFromKnown(List<int[]> slots, Map<Integer, Character> known) {
        Set<String> used = new HashSet<>();
        for (int[] cells : slots) {
            String w = Grids.wordAt(cells, known);
            if (w != null) used.add(w);
        }
        return used;
    }

    // ================================================================== analysis

    public static Set<Integer> crossingDeadlockIndices(List<int[]> slots, DualIndex index, Map<Integer, Character> known,
                                                       Set<String> challenge) {
        Set<String> used = usedFromKnown(slots, known);
        Set<String> available = new LinkedHashSet<>();
        if (challenge != null) for (String w : challenge) if (!used.contains(w)) available.add(w);
        List<Integer> open = new ArrayList<>();
        for (int j = 0; j < slots.size(); j++) if (!Grids.allKnown(slots.get(j), known)) open.add(j);
        Map<Integer, List<int[]>> cellToSlots = new LinkedHashMap<>();
        for (int j : open) {
            int[] cells = slots.get(j);
            for (int p = 0; p < cells.length; p++) cellToSlots.computeIfAbsent(cells[p], k -> new ArrayList<>()).add(new int[]{j, p});
        }
        Map<Integer, List<Set<Character>>> lettersBySlot = new HashMap<>();
        for (int j : open) {
            int[] cells = slots.get(j);
            Map<Integer, Character> sub = subKnown(cells, known);
            List<Set<Character>> letters = new ArrayList<>();
            for (int p = 0; p < cells.length; p++) letters.add(new HashSet<>());
            for (String w : Words.slotCandidates(index, cells.length, cells, sub)) {
                if (used.contains(w)) continue;
                for (int p = 0; p < cells.length; p++) letters.get(p).add(w.charAt(p));
            }
            for (String w : available) {
                if (w.length() != cells.length) continue;
                boolean ok = true;
                for (int p = 0; p < cells.length; p++) {
                    Character k = sub.get(cells[p]);
                    if (k != null && k != w.charAt(p)) {
                        ok = false;
                        break;
                    }
                }
                if (ok) for (int p = 0; p < cells.length; p++) letters.get(p).add(w.charAt(p));
            }
            lettersBySlot.put(j, letters);
        }
        Set<Integer> deadlocked = new TreeSet<>();
        cellToSlots.forEach((cell, entries) -> {
            for (int a = 0; a < entries.size(); a++) {
                Set<Character> li = lettersBySlot.get(entries.get(a)[0]).get(entries.get(a)[1]);
                if (li.isEmpty()) continue;
                for (int b = a + 1; b < entries.size(); b++) {
                    Set<Character> lj = lettersBySlot.get(entries.get(b)[0]).get(entries.get(b)[1]);
                    if (lj.isEmpty()) continue;
                    boolean shared = false;
                    for (Character ch : li) if (lj.contains(ch)) {
                        shared = true;
                        break;
                    }
                    if (!shared) {
                        deadlocked.add(entries.get(a)[0]);
                        deadlocked.add(entries.get(b)[0]);
                    }
                }
            }
        });
        return deadlocked;
    }

    public static List<Integer> impossibleIndices(List<int[]> slots, DualIndex index, Map<Integer, Character> known,
                                                  Set<String> challenge) {
        TreeSet<Integer> result = new TreeSet<>();
        for (int j = 0; j < slots.size(); j++) {
            int[] cells = slots.get(j);
            if (Grids.allKnown(cells, known)) continue;
            if (Words.slotCandidates(index, cells.length, cells, subKnown(cells, known)).isEmpty()) result.add(j);
        }
        result.addAll(crossingDeadlockIndices(slots, index, known, challenge));
        return new ArrayList<>(result);
    }

    public static List<Integer> invalidFullyKnownIndices(List<int[]> slots, DualIndex index, Map<Integer, Character> known,
                                                         Set<Integer> exempt) {
        List<Integer> result = new ArrayList<>();
        for (int j = 0; j < slots.size(); j++) {
            int[] cells = slots.get(j);
            if (!Grids.allKnown(cells, known)) continue;
            if (exempt != null && !exempt.isEmpty() && Grids.allIn(cells, exempt)) continue;
            if (Words.slotCandidates(index, cells.length, cells, subKnown(cells, known)).isEmpty()) result.add(j);
        }
        return result;
    }

    public static Set<Integer> challengeWordCells(List<int[]> slots, Map<Integer, Character> known, Set<String> challenge) {
        Set<Integer> cells = new HashSet<>();
        if (challenge == null || challenge.isEmpty()) return cells;
        for (int[] sc : slots) {
            String w = Grids.wordAt(sc, known);
            if (w != null && challenge.contains(w)) for (int c : sc) cells.add(c);
        }
        return cells;
    }

    public static Set<Integer> challengeFillableSlotIndices(List<int[]> slots, Map<Integer, Character> known,
                                                            Set<String> challenge) {
        Set<Integer> out = new HashSet<>();
        if (challenge == null || challenge.isEmpty()) return out;
        Set<String> used = usedFromKnown(slots, known);
        List<String> available = new ArrayList<>();
        for (String w : challenge) if (!used.contains(w)) available.add(w);
        if (available.isEmpty()) return out;
        for (int i = 0; i < slots.size(); i++) {
            int[] cells = slots.get(i);
            if (Grids.allKnown(cells, known)) continue;
            for (String w : available) {
                if (w.length() != cells.length) continue;
                boolean ok = true;
                for (int p = 0; p < cells.length; p++) {
                    Character k = known.get(cells[p]);
                    if (k != null && k != w.charAt(p)) {
                        ok = false;
                        break;
                    }
                }
                if (ok) {
                    out.add(i);
                    break;
                }
            }
        }
        return out;
    }

    static Set<Integer> exemptCells(Map<Integer, Character> permanentLocked, List<int[]> slots,
                                    Map<Integer, Character> known, Set<String> challenge) {
        Set<Integer> ex = new HashSet<>(permanentLocked == null ? Set.of() : permanentLocked.keySet());
        ex.addAll(challengeWordCells(slots, known, challenge));
        return ex;
    }

    // ================================================================== shorter / longer words

    static boolean newCrossingImpossibility(List<int[]> curSlots, Map<Integer, List<Integer>> cellToSlots, int ownIdx,
                                            int[] sub, String word, Map<Integer, Character> known, DualIndex index) {
        for (int p = 0; p < sub.length; p++) {
            int cell = sub[p];
            char letter = word.charAt(p);
            for (int j : cellToSlots.getOrDefault(cell, List.of())) {
                if (j == ownIdx) continue;
                int[] cross = curSlots.get(j);
                Map<Integer, Character> before = subKnown(cross, known);
                if (Words.slotCandidates(index, cross.length, cross, before).isEmpty()) continue;
                Map<Integer, Character> after = new HashMap<>(before);
                after.put(cell, letter);
                if (Words.slotCandidates(index, cross.length, cross, after).isEmpty()) return true;
            }
        }
        return false;
    }

    /** Returns {word, sub cells, boundary cell} or null. */
    static Object[] findShorterWordForZone(char[][] grid, int rows, int cols, int[] cells, List<int[]> curSlots,
                                           Map<Integer, List<Integer>> cellToSlots, int ownIdx, DualIndex index,
                                           Map<Integer, Character> known, Set<String> used, Rng rng) {
        int length = cells.length;
        List<Object[]> options = new ArrayList<>();
        for (int m = length - 1; m > 2; m--) {
            for (int side = 0; side < 2; side++) {
                int[] sub, leftover;
                int boundary;
                if (side == 0) {
                    sub = Arrays.copyOfRange(cells, 0, m);
                    boundary = cells[m];
                    leftover = Arrays.copyOfRange(cells, m + 1, length);
                } else {
                    sub = Arrays.copyOfRange(cells, length - m, length);
                    boundary = cells[length - m - 1];
                    leftover = Arrays.copyOfRange(cells, 0, length - m - 1);
                }
                int br = Cells.r(boundary), bc = Cells.c(boundary);
                if (grid[br][bc] == BLACK || known.containsKey(boundary)) continue;
                grid[br][bc] = BLACK;
                boolean valid = Grids.isStructurallyValid(grid, rows, cols, 1);
                grid[br][bc] = WHITE;
                if (!valid) continue;
                if (leftover.length >= 2 && Grids.allKnown(leftover, known)
                        && Words.slotCandidates(index, leftover.length, leftover, subKnown(leftover, known)).isEmpty()) continue;
                if (Grids.allKnown(sub, known)) continue;
                for (String w : Words.slotCandidates(index, m, sub, subKnown(sub, known))) {
                    if (!used.contains(w)) options.add(new Object[]{w, sub, boundary});
                }
            }
        }
        rng.shuffle(options);
        for (Object[] o : options) {
            if (newCrossingImpossibility(curSlots, cellToSlots, ownIdx, (int[]) o[1], (String) o[0], known, index)) continue;
            return o;
        }
        return null;
    }

    static Set<Integer> knownSlotBoundaryCells(char[][] g, int rows, int cols, List<int[]> curSlots,
                                               Map<Integer, Character> known) {
        Set<Integer> protectedCells = new HashSet<>();
        for (int[] cells : curSlots) {
            if (!Grids.allKnown(cells, known)) continue;
            boolean across = cells.length > 1 && Cells.r(cells[1]) == Cells.r(cells[0]);
            int dr = across ? 0 : 1, dc = across ? 1 : 0;
            int r0 = Cells.r(cells[0]), c0 = Cells.c(cells[0]), r1 = Cells.r(cells[cells.length - 1]),
                    c1 = Cells.c(cells[cells.length - 1]);
            int[][] bs = {{r0 - dr, c0 - dc}, {r1 + dr, c1 + dc}};
            for (int[] b : bs) if (b[0] >= 0 && b[0] < rows && b[1] >= 0 && b[1] < cols) protectedCells.add(Cells.of(b[0], b[1]));
        }
        for (int r = 0; r < rows; r++) {
            for (int c = 0; c < cols; c++) {
                if (g[r][c] != BLACK) continue;
                boolean vertical = dirHasKnown(g, rows, cols, r, c, -1, 0, known) && dirHasKnown(g, rows, cols, r, c, 1, 0, known);
                boolean horizontal = dirHasKnown(g, rows, cols, r, c, 0, -1, known) && dirHasKnown(g, rows, cols, r, c, 0, 1, known);
                if (vertical || horizontal) protectedCells.add(Cells.of(r, c));
            }
        }
        return protectedCells;
    }

    static boolean dirHasKnown(char[][] g, int rows, int cols, int r, int c, int dr, int dc, Map<Integer, Character> known) {
        int rr = r + dr, cc = c + dc;
        while (rr >= 0 && rr < rows && cc >= 0 && cc < cols && g[rr][cc] == WHITE) {
            if (known.containsKey(Cells.of(rr, cc))) return true;
            rr += dr;
            cc += dc;
        }
        return false;
    }

    static boolean newBoundaryCrossingImpossible(char[][] grid, int rows, int cols, int boundary, char letter, int ownDr,
                                                 int ownDc, Map<Integer, Character> known, DualIndex index) {
        int pdr = ownDc, pdc = ownDr;
        int br = Cells.r(boundary), bc = Cells.c(boundary);
        List<Integer> cells = new ArrayList<>();
        cells.add(boundary);
        int rr = br - pdr, cc = bc - pdc;
        while (rr >= 0 && rr < rows && cc >= 0 && cc < cols && grid[rr][cc] == WHITE) {
            cells.add(0, Cells.of(rr, cc));
            rr -= pdr;
            cc -= pdc;
        }
        rr = br + pdr;
        cc = bc + pdc;
        while (rr >= 0 && rr < rows && cc >= 0 && cc < cols && grid[rr][cc] == WHITE) {
            cells.add(Cells.of(rr, cc));
            rr += pdr;
            cc += pdc;
        }
        if (cells.size() < 2) return false;
        int[] arr = cells.stream().mapToInt(Integer::intValue).toArray();
        Map<Integer, Character> before = new HashMap<>();
        for (int c : arr) if (known.containsKey(c) && c != boundary) before.put(c, known.get(c));
        if (Words.slotCandidates(index, arr.length, arr, before).isEmpty()) return false;
        Map<Integer, Character> after = new HashMap<>(before);
        after.put(boundary, letter);
        return Words.slotCandidates(index, arr.length, arr, after).isEmpty();
    }

    /** Returns {word, new cells, old boundary, new boundary (Integer or null)} or null. */
    static Object[] findLongerWordForZone(char[][] grid, int rows, int cols, int[] cells, List<int[]> curSlots,
                                          Map<Integer, List<Integer>> cellToSlots, int ownIdx, Set<Integer> protectedCells,
                                          DualIndex index, Map<Integer, Character> known, Set<String> used, Rng rng) {
        int length = cells.length;
        boolean across = length > 1 && Cells.r(cells[1]) == Cells.r(cells[0]);
        int dr = across ? 0 : 1, dc = across ? 1 : 0;
        int r0 = Cells.r(cells[0]), c0 = Cells.c(cells[0]), r1 = Cells.r(cells[length - 1]), c1 = Cells.c(cells[length - 1]);
        List<Object[]> options = new ArrayList<>();
        Object[][] sides = {{"head", r0 - dr, c0 - dc, -dr, -dc}, {"tail", r1 + dr, c1 + dc, dr, dc}};
        for (Object[] s : sides) {
            String side = (String) s[0];
            int br = (int) s[1], bc = (int) s[2], edr = (int) s[3], edc = (int) s[4];
            if (!(br >= 0 && br < rows && bc >= 0 && bc < cols)) continue;
            int boundary = Cells.of(br, bc);
            if (grid[br][bc] != BLACK || protectedCells.contains(boundary)) continue;
            List<Integer> avail = new ArrayList<>();
            int rr = br + edr, cc = bc + edc;
            while (rr >= 0 && rr < rows && cc >= 0 && cc < cols && grid[rr][cc] == WHITE) {
                avail.add(Cells.of(rr, cc));
                rr += edr;
                cc += edc;
            }
            if (avail.isEmpty()) continue;
            for (int k = 0; k <= avail.size(); k++) {
                Integer newBoundary = k < avail.size() ? avail.get(k) : null;
                if (newBoundary != null) {
                    if (known.containsKey(newBoundary)) continue;
                    int nbr = Cells.r(newBoundary), nbc = Cells.c(newBoundary);
                    grid[br][bc] = WHITE;
                    grid[nbr][nbc] = BLACK;
                    boolean valid = Grids.isStructurallyValid(grid, rows, cols, 1);
                    grid[br][bc] = BLACK;
                    grid[nbr][nbc] = WHITE;
                    if (!valid) continue;
                }
                List<Integer> absorbed = avail.subList(0, k);
                List<Integer> newCells = new ArrayList<>();
                if (side.equals("head")) {
                    for (int q = absorbed.size() - 1; q >= 0; q--) newCells.add(absorbed.get(q));
                    newCells.add(boundary);
                    for (int c : cells) newCells.add(c);
                } else {
                    for (int c : cells) newCells.add(c);
                    newCells.add(boundary);
                    newCells.addAll(absorbed);
                }
                int[] nc = newCells.stream().mapToInt(Integer::intValue).toArray();
                for (String w : Words.slotCandidates(index, nc.length, nc, subKnown(nc, known))) {
                    if (!used.contains(w)) options.add(new Object[]{w, nc, boundary, newBoundary});
                }
            }
        }
        rng.shuffle(options);
        for (Object[] o : options) {
            String word = (String) o[0];
            int[] nc = (int[]) o[1];
            int oldB = (int) o[2];
            if (newCrossingImpossibility(curSlots, cellToSlots, ownIdx, nc, word, known, index)) continue;
            int bIdx = 0;
            for (int q = 0; q < nc.length; q++) if (nc[q] == oldB) bIdx = q;
            if (newBoundaryCrossingImpossible(grid, rows, cols, oldB, word.charAt(bIdx), dr, dc, known, index)) continue;
            return o;
        }
        return null;
    }

    /** A word's own black-cell change ("shorten": added boundary;
     * "lengthen": old boundary freed, new one blackened). */
    public record Link(String kind, int boundary, Integer newBoundary) {}

    /** Result of a shorten/lengthen pass. */
    public record Zones(char[][] grid, List<int[]> slots, String[] assignment, List<Integer> impossible,
                        Map<Cells.Key, Link> links) {}

    static Map<Integer, Character> knownFromAssignment(List<int[]> slots, String[] assignment,
                                                       Map<Integer, Character> permanentLocked) {
        Map<Integer, Character> known = new LinkedHashMap<>();
        for (int i = 0; i < slots.size(); i++) {
            String w = assignment[i];
            if (w == null) continue;
            int[] cells = slots.get(i);
            for (int p = 0; p < cells.length; p++) known.put(cells[p], w.charAt(p));
        }
        if (permanentLocked != null) known.putAll(permanentLocked);
        return known;
    }

    static List<Integer> remainingImpossible(List<int[]> curSlots, DualIndex index, Map<Integer, Character> known,
                                             Set<String> challenge) {
        Set<Integer> idx = new LinkedHashSet<>(impossibleIndices(curSlots, index, known, challenge));
        idx.removeAll(challengeFillableSlotIndices(curSlots, known, challenge));
        return new ArrayList<>(idx);
    }

    static Zones finalizeZones(char[][] newGrid, int rows, int cols, DualIndex index, Map<Integer, Character> known,
                               Map<Integer, Character> permanentLocked, Set<String> challenge, Map<Cells.Key, Link> links) {
        List<int[]> finalSlots = Grids.extractSlots(newGrid, rows, cols);
        Set<Integer> invalid = new HashSet<>(invalidFullyKnownIndices(finalSlots, index, known,
                exemptCells(permanentLocked, finalSlots, known, challenge)));
        String[] finalAssignment = new String[finalSlots.size()];
        for (int j = 0; j < finalSlots.size(); j++) {
            finalAssignment[j] = invalid.contains(j) ? null : Grids.wordAt(finalSlots.get(j), known);
        }
        TreeSet<Integer> imp = new TreeSet<>(invalid);
        imp.addAll(impossibleIndices(finalSlots, index, known, challenge));
        imp.removeAll(challengeFillableSlotIndices(finalSlots, known, challenge));
        return new Zones(newGrid, finalSlots, finalAssignment, new ArrayList<>(imp), links);
    }

    public static Zones shortenImpossibleZones(char[][] grid, int rows, int cols, List<int[]> slots, String[] assignment,
                                               List<Integer> impossibleSlots, DualIndex index, Rng rng,
                                               Map<Integer, Character> permanentLocked, Set<String> challenge) {
        Map<Integer, Character> known = knownFromAssignment(slots, assignment, permanentLocked);
        char[][] newGrid = Grids.copy(grid);
        boolean changed = false;
        List<int[]> remaining = new ArrayList<>();
        for (int i : impossibleSlots) remaining.add(slots.get(i));
        Map<Cells.Key, Link> links = new LinkedHashMap<>();
        while (!remaining.isEmpty()) {
            boolean progressed = false;
            for (int[] cellsT : remaining) {
                List<int[]> cur = Grids.extractSlots(newGrid, rows, cols);
                int own = Grids.indexOfSlot(cur, cellsT);
                if (own < 0) continue;
                int[] cells = cur.get(own);
                Map<Integer, List<Integer>> c2s = Grids.cellToSlotIndices(cur);
                Set<String> used = usedFromKnown(cur, known);
                Object[] res = findShorterWordForZone(newGrid, rows, cols, cells, cur, c2s, own, index, known, used, rng);
                if (res == null) continue;
                String word = (String) res[0];
                int[] sub = (int[]) res[1];
                int boundary = (int) res[2];
                for (int p = 0; p < sub.length; p++) known.put(sub[p], word.charAt(p));
                newGrid[Cells.r(boundary)][Cells.c(boundary)] = BLACK;
                links.put(Cells.key(sub), new Link("shorten", boundary, null));
                progressed = true;
                changed = true;
            }
            if (!progressed) break;
            List<int[]> cur = Grids.extractSlots(newGrid, rows, cols);
            remaining = new ArrayList<>();
            for (int j : remainingImpossible(cur, index, known, challenge)) remaining.add(cur.get(j));
        }
        if (!changed) return new Zones(grid, slots, assignment, impossibleSlots, new LinkedHashMap<>());
        return finalizeZones(newGrid, rows, cols, index, known, permanentLocked, challenge, links);
    }

    public static Zones lengthenImpossibleZones(char[][] grid, int rows, int cols, List<int[]> slots, String[] assignment,
                                                List<Integer> impossibleSlots, DualIndex index, Rng rng,
                                                Map<Integer, Character> permanentLocked, Set<Integer> permanentBlack,
                                                Set<String> challenge, Map<Cells.Key, Link> linksIn) {
        Map<Integer, Character> known = knownFromAssignment(slots, assignment, permanentLocked);
        char[][] newGrid = Grids.copy(grid);
        boolean changed = false;
        List<int[]> remaining = new ArrayList<>();
        for (int i : impossibleSlots) remaining.add(slots.get(i));
        Map<Cells.Key, Link> links = new LinkedHashMap<>(linksIn == null ? Map.of() : linksIn);
        while (!remaining.isEmpty()) {
            boolean progressed = false;
            for (int[] cellsT : remaining) {
                List<int[]> cur = Grids.extractSlots(newGrid, rows, cols);
                int own = Grids.indexOfSlot(cur, cellsT);
                if (own < 0) continue;
                int[] cells = cur.get(own);
                Map<Integer, List<Integer>> c2s = Grids.cellToSlotIndices(cur);
                Set<String> used = usedFromKnown(cur, known);
                Set<Integer> prot = knownSlotBoundaryCells(newGrid, rows, cols, cur, known);
                if (permanentBlack != null && !permanentBlack.isEmpty()) {
                    prot = new HashSet<>(prot);
                    prot.addAll(permanentBlack);
                }
                Object[] res = findLongerWordForZone(newGrid, rows, cols, cells, cur, c2s, own, prot, index, known, used, rng);
                if (res == null) continue;
                String word = (String) res[0];
                int[] nc = (int[]) res[1];
                int oldB = (int) res[2];
                Integer newB = (Integer) res[3];
                for (int p = 0; p < nc.length; p++) known.put(nc[p], word.charAt(p));
                newGrid[Cells.r(oldB)][Cells.c(oldB)] = WHITE;
                if (newB != null) newGrid[Cells.r(newB)][Cells.c(newB)] = BLACK;
                links.put(Cells.key(nc), new Link("lengthen", oldB, newB));
                progressed = true;
                changed = true;
            }
            if (!progressed) break;
            List<int[]> cur = Grids.extractSlots(newGrid, rows, cols);
            remaining = new ArrayList<>();
            for (int j : remainingImpossible(cur, index, known, challenge)) remaining.add(cur.get(j));
        }
        if (!changed) return new Zones(grid, slots, assignment, impossibleSlots, links);
        return finalizeZones(newGrid, rows, cols, index, known, permanentLocked, challenge, links);
    }

    // ================================================================== cleaning

    /** Returns {assignment, confirmed, newBlackCells, reopenedCells}. */
    public static Object[] cleanBlockedSlots(List<int[]> slots, String[] assignmentIn, Collection<Integer> impossibleSlots,
                                             Map<Integer, Character> lockedLetters, boolean excludeImpossibleLocked,
                                             DualIndex index, Rng rng, char[][] grid, Integer rows, Integer cols,
                                             Map<Integer, Character> permanentLocked, Map<Cells.Key, Link> links,
                                             Set<Integer> deadlockedSlots, boolean deep) {
        String[] assignment = assignmentIn.clone();
        if (lockedLetters != null && !lockedLetters.isEmpty()) {
            Set<Integer> impossibleSet = excludeImpossibleLocked ? new HashSet<>(impossibleSlots) : Set.of();
            for (int i = 0; i < slots.size(); i++) {
                int[] cells = slots.get(i);
                if (assignment[i] == null && !impossibleSet.contains(i) && Grids.allKnown(cells, lockedLetters)) {
                    if (index != null && !(permanentLocked != null && !permanentLocked.isEmpty() && Grids.allKnown(cells, permanentLocked))
                            && Words.slotCandidates(index, cells.length, cells, lockedLetters).isEmpty()) continue;
                    assignment[i] = Grids.wordAt(cells, lockedLetters);
                }
            }
        }
        String[] before = assignment.clone();
        Map<Integer, List<Integer>> c2s = Grids.cellToSlotIndices(slots);
        Set<Integer> newBlack = new LinkedHashSet<>();
        Set<Integer> reopened = new LinkedHashSet<>();
        boolean capable = grid != null && rows != null && cols != null;
        char[][] working = capable ? Grids.copy(grid) : null;
        Map<Integer, Link> linksBySlot = new HashMap<>();
        if (links != null && !links.isEmpty()) {
            for (int i = 0; i < slots.size(); i++) {
                Link l = links.get(Cells.key(slots.get(i)));
                if (l != null) linksBySlot.put(i, l);
            }
        }
        java.util.function.IntConsumer revert = j -> {
            Link link = linksBySlot.remove(j);
            if (link == null) return;
            if (link.kind().equals("shorten")) {
                newBlack.remove(link.boundary());
                reopened.add(link.boundary());
                if (capable) working[Cells.r(link.boundary())][Cells.c(link.boundary())] = WHITE;
            } else {
                reopened.remove(link.boundary());
                newBlack.add(link.boundary());
                if (capable) working[Cells.r(link.boundary())][Cells.c(link.boundary())] = BLACK;
                if (link.newBoundary() != null) {
                    newBlack.remove(link.newBoundary());
                    reopened.add(link.newBoundary());
                    if (capable) working[Cells.r(link.newBoundary())][Cells.c(link.newBoundary())] = WHITE;
                }
            }
        };
        if (index != null && rng != null) {
            Set<Integer> impossibleSet = new HashSet<>(impossibleSlots);
            for (int i : impossibleSlots) {
                TreeSet<Integer> crossing = new TreeSet<>();
                for (int cell : slots.get(i)) for (int j : c2s.get(cell)) if (j != i && assignment[j] != null) crossing.add(j);
                boolean isDeadlock = deadlockedSlots != null && !deadlockedSlots.isEmpty() && deadlockedSlots.contains(i);
                boolean placedBlack = false;
                if (!crossing.isEmpty() && !isDeadlock && capable && rng.random() < BLACK_CELL_INSTEAD_OF_REMOVAL_PROBABILITY) {
                    Map<Integer, Character> known = new HashMap<>();
                    for (int cell : slots.get(i)) {
                        for (int j : c2s.get(cell)) {
                            if (j != i && assignment[j] != null) {
                                int[] jc = slots.get(j);
                                int pos = 0;
                                for (int q = 0; q < jc.length; q++) if (jc[q] == cell) pos = q;
                                known.put(cell, assignment[j].charAt(pos));
                                break;
                            }
                        }
                    }
                    List<Integer> blankC = new ArrayList<>(), knownC = new ArrayList<>();
                    for (int cell : slots.get(i)) {
                        if (permanentLocked != null && permanentLocked.containsKey(cell)) continue;
                        (known.containsKey(cell) ? knownC : blankC).add(cell);
                    }
                    rng.shuffle(blankC);
                    rng.shuffle(knownC);
                    java.util.Comparator<Integer> byImp = (a, b) -> {
                        int ca = 0, cb = 0;
                        for (int j : c2s.get(a)) if (impossibleSet.contains(j)) ca++;
                        for (int j : c2s.get(b)) if (impossibleSet.contains(j)) cb++;
                        return Integer.compare(cb, ca);
                    };
                    blankC.sort(byImp);
                    knownC.sort(byImp);
                    List<Integer> order = new ArrayList<>(blankC);
                    order.addAll(knownC);
                    for (int cell : order) {
                        int br = Cells.r(cell), bc = Cells.c(cell);
                        working[br][bc] = BLACK;
                        if (Grids.isStructurallyValid(working, rows, cols, 1)) {
                            newBlack.add(cell);
                            for (int j : c2s.get(cell)) {
                                if (j != i && assignment[j] != null) {
                                    assignment[j] = null;
                                    revert.accept(j);
                                }
                            }
                            placedBlack = true;
                            break;
                        }
                        working[br][bc] = WHITE;
                    }
                }
                if (placedBlack) continue;
                for (int j : crossing) {
                    assignment[j] = null;
                    revert.accept(j);
                }
                if (capable) {
                    int[] si = slots.get(i);
                    if (Words.slotCandidateCount(index, si.length, si, Map.of()) == 0) {
                        for (int cell : si) {
                            int br = Cells.r(cell), bc = Cells.c(cell);
                            if (working[br][bc] == BLACK) continue;
                            if (permanentLocked != null && permanentLocked.containsKey(cell)) continue;
                            working[br][bc] = BLACK;
                            if (Grids.isStructurallyValid(working, rows, cols, 1)) {
                                newBlack.add(cell);
                                for (int j : c2s.get(cell)) {
                                    if (j != i && assignment[j] != null) {
                                        assignment[j] = null;
                                        revert.accept(j);
                                    }
                                }
                            } else {
                                working[br][bc] = WHITE;
                            }
                        }
                    }
                }
            }
        } else {
            for (int i : impossibleSlots) {
                for (int cell : slots.get(i)) {
                    for (int j : c2s.get(cell)) {
                        if (j != i && assignment[j] != null) {
                            assignment[j] = null;
                            revert.accept(j);
                        }
                    }
                }
            }
        }
        if (deep) {
            List<Integer> firstLevel = new ArrayList<>();
            for (int j = 0; j < assignment.length; j++) if (assignment[j] == null && before[j] != null) firstLevel.add(j);
            for (int j : firstLevel) {
                for (int cell : slots.get(j)) {
                    for (int k : c2s.get(cell)) {
                        if (k != j && assignment[k] != null) {
                            assignment[k] = null;
                            revert.accept(k);
                        }
                    }
                }
            }
        }
        Map<Integer, Character> confirmed = new LinkedHashMap<>();
        for (int i = 0; i < assignment.length; i++) {
            String w = assignment[i];
            if (w == null) continue;
            int[] cells = slots.get(i);
            for (int p = 0; p < cells.length; p++) confirmed.put(cells[p], w.charAt(p));
        }
        return new Object[]{assignment, confirmed, newBlack, reopened};
    }

    /** Returns {grid, slots, assignment} or null. */
    public static Object[] plugIsolatedCells(char[][] grid, int rows, int cols, List<int[]> slots, String[] assignment,
                                             DualIndex index, Map<Integer, Character> permanentLocked) {
        Map<Integer, Character> known = knownFromAssignment(slots, assignment, null);
        Set<Integer> unfilled = new LinkedHashSet<>();
        for (int r = 0; r < rows; r++) for (int c = 0; c < cols; c++) {
            if (grid[r][c] == WHITE && !known.containsKey(Cells.of(r, c))) unfilled.add(Cells.of(r, c));
        }
        if (unfilled.isEmpty()) return null;
        for (int cell : unfilled) {
            int r = Cells.r(cell), c = Cells.c(cell);
            if (unfilled.contains(Cells.of(r - 1, c)) || unfilled.contains(Cells.of(r + 1, c))
                    || (c > 0 && unfilled.contains(Cells.of(r, c - 1))) || unfilled.contains(Cells.of(r, c + 1))) {
                return null;
            }
        }
        char[][] ng = Grids.copy(grid);
        for (int cell : unfilled) ng[Cells.r(cell)][Cells.c(cell)] = BLACK;
        if (!Grids.isStructurallyValid(ng, rows, cols, 1)) return null;
        List<int[]> newSlots = Grids.extractSlots(ng, rows, cols);
        String[] na = new String[newSlots.size()];
        for (int k = 0; k < newSlots.size(); k++) {
            int[] cells = newSlots.get(k);
            if (!Grids.allKnown(cells, known)) return null;
            if (permanentLocked != null && !permanentLocked.isEmpty() && Grids.allKnown(cells, permanentLocked)) {
                na[k] = Grids.wordAt(cells, known);
                continue;
            }
            Dom cands = Words.slotCandidates(index, cells.length, cells, known);
            if (cands.isEmpty()) return null;
            na[k] = cands.first();
        }
        return new Object[]{ng, newSlots, na};
    }

    /** Returns {new grid, confirmed letters}. */
    public static Object[] buildRetrySeed(char[][] grid, int rows, int cols, List<int[]> slots, String[] assignment,
                                          List<Integer> impossibleSlots, Map<Integer, Character> lockedLetters,
                                          boolean excludeImpossibleLocked, char[][] seedGrid, DualIndex index, Rng rng,
                                          Map<Integer, Character> permanentLocked, Set<Integer> permanentBlack,
                                          boolean deep) {
        Object[] cleaned = cleanBlockedSlots(slots, assignment, impossibleSlots, lockedLetters, excludeImpossibleLocked,
                index, rng, null, null, null, permanentLocked, null, null, deep);
        String[] asg = (String[]) cleaned[0];
        @SuppressWarnings("unchecked")
        Map<Integer, Character> confirmed = (Map<Integer, Character>) cleaned[1];
        Set<Integer> protectedCells = new HashSet<>();
        for (int i = 0; i < asg.length; i++) {
            if (asg[i] == null) continue;
            int[] cells = slots.get(i);
            boolean across = cells.length > 1 && Cells.r(cells[1]) == Cells.r(cells[0]);
            int dr = across ? 0 : 1, dc = across ? 1 : 0;
            int r0 = Cells.r(cells[0]), c0 = Cells.c(cells[0]), r1 = Cells.r(cells[cells.length - 1]),
                    c1 = Cells.c(cells[cells.length - 1]);
            if (r0 - dr >= 0 && c0 - dc >= 0) protectedCells.add(Cells.of(r0 - dr, c0 - dc));
            if (r1 + dr < rows && c1 + dc < cols) protectedCells.add(Cells.of(r1 + dr, c1 + dc));
        }
        for (int r = 0; r < rows; r++) {
            for (int c = 0; c < cols; c++) {
                if (grid[r][c] != BLACK) continue;
                boolean v = dirHasKnown(grid, rows, cols, r, c, -1, 0, confirmed) && dirHasKnown(grid, rows, cols, r, c, 1, 0, confirmed);
                boolean h = dirHasKnown(grid, rows, cols, r, c, 0, -1, confirmed) && dirHasKnown(grid, rows, cols, r, c, 0, 1, confirmed);
                if (v || h) protectedCells.add(Cells.of(r, c));
            }
        }
        if (seedGrid != null) {
            for (int r = 0; r < rows; r++) for (int c = 0; c < cols; c++) if (seedGrid[r][c] == BLACK) protectedCells.add(Cells.of(r, c));
        }
        if (permanentBlack != null) protectedCells.addAll(permanentBlack);
        char[][] ng = Grids.copy(grid);
        for (int r = 0; r < rows; r++) {
            for (int c = 0; c < cols; c++) {
                if (ng[r][c] == BLACK && !protectedCells.contains(Cells.of(r, c))) {
                    if (fullySurroundedByBlack(grid, rows, cols, r, c)) continue;
                    ng[r][c] = WHITE;
                }
            }
        }
        return new Object[]{ng, confirmed};
    }

    static boolean fullySurroundedByBlack(char[][] grid, int rows, int cols, int r, int c) {
        int[][] d = {{-1, 0}, {1, 0}, {0, -1}, {0, 1}};
        for (int[] x : d) {
            int rr = r + x[0], cc = c + x[1];
            if (!(rr >= 0 && rr < rows && cc >= 0 && cc < cols) || grid[rr][cc] != BLACK) return false;
        }
        return true;
    }

    // ================================================================== scores / pools

    public static double playableScore(char[][] grid, Diag diag, int rows, int cols, PW pw, Set<String> challenge) {
        List<int[]> slots = Grids.extractSlots(grid, rows, cols);
        if (slots.size() != diag.assignment.length) return Math.sqrt(Fill.contentScore(diag.assignment, null, pw, challenge));
        return Math.sqrt(Fill.contentScore(diag.assignment, slots, pw, challenge));
    }

    public static double cleanedPlayableScore(char[][] grid, Diag diag, int rows, int cols, DualIndex index, Rng rng,
                                              PW pw, Set<String> challenge) {
        List<int[]> slots = Grids.extractSlots(grid, rows, cols);
        if (slots.size() != diag.assignment.length) return playableScore(grid, diag, rows, cols, pw, challenge);
        Object[] cleaned = cleanBlockedSlots(slots, diag.assignment, diag.impossibleSlots, null, false, index, rng, null,
                null, null, null, null, null, false);
        return Math.sqrt(Fill.contentScore((String[]) cleaned[0], slots, pw, challenge));
    }

    public static long wordsInPlaceScore(List<int[]> slots, Map<Integer, Character> confirmed, PW pw, Set<String> challenge) {
        List<String> words = new ArrayList<>();
        List<int[]> cellsList = new ArrayList<>();
        for (int[] cells : slots) {
            String w = Grids.wordAt(cells, confirmed);
            if (w != null) {
                words.add(w);
                cellsList.add(cells);
            }
        }
        return Fill.contentScore(words.toArray(new String[0]), cellsList, pw, challenge);
    }

    // ================================================================== optimize before cleanup

    /** Returns {grid, new diag}. */
    public static Object[] optimizeBeforeCleanup(char[][] candGrid, Diag candDiag, int rows, int cols, DualIndex index,
                                                 Rng rng, long deadlineChecks, AtomicBoolean cancelEvent,
                                                 Map<Integer, Character> permanentLocked, Set<Integer> permanentBlack,
                                                 Set<String> challenge) {
        List<int[]> candSlots = Grids.extractSlots(candGrid, rows, cols);
        char[][] example = candDiag.exampleGrid;
        Set<Cells.Key> emptyTuples = new HashSet<>();
        for (int[] cells : candSlots) {
            boolean allEmpty = true;
            for (int c : cells) if (example[Cells.r(c)][Cells.c(c)] != '.') allEmpty = false;
            if (allEmpty) emptyTuples.add(Cells.key(cells));
        }
        Set<Cells.Key> impossibleTuples = new HashSet<>();
        for (int i : candDiag.impossibleSlots) impossibleTuples.add(Cells.key(candSlots.get(i)));
        Set<Integer> lockedBlack = new TreeSet<>();
        for (int[] cells : candSlots) {
            if (!emptyTuples.contains(Cells.key(cells))) continue;
            int r0 = Cells.r(cells[0]), c0 = Cells.c(cells[0]), r1 = Cells.r(cells[cells.length - 1]),
                    c1 = Cells.c(cells[cells.length - 1]);
            int dr = cells.length > 1 && Cells.r(cells[1]) != r0 ? 1 : 0;
            int dc = cells.length > 1 && Cells.c(cells[1]) != c0 ? 1 : 0;
            int[][] bs = {{r0 - dr, c0 - dc}, {r1 + dr, c1 + dc}};
            for (int[] b : bs) {
                if (b[0] >= 0 && b[0] < rows && b[1] >= 0 && b[1] < cols && candGrid[b[0]][b[1]] == BLACK) {
                    lockedBlack.add(Cells.of(b[0], b[1]));
                }
            }
        }
        Map<Integer, Character> confirmed = new LinkedHashMap<>();
        for (int i = 0; i < candSlots.size(); i++) {
            String w = candDiag.assignment[i];
            if (w == null) continue;
            int[] cells = candSlots.get(i);
            for (int p = 0; p < cells.length; p++) confirmed.put(cells[p], w.charAt(p));
        }
        java.util.function.Consumer<Fill.Result> absorb = res -> {
            for (int i = 0; i < res.slots().size(); i++) {
                String w = res.assignment()[i];
                if (w == null) continue;
                int[] cells = res.slots().get(i);
                for (int p = 0; p < cells.length; p++) confirmed.put(cells[p], w.charAt(p));
            }
        };
        java.util.function.Function<char[][], Fill.Result> tryComplete = g -> {
            List<int[]> ts = Grids.extractSlots(g, rows, cols);
            String[] preseed = new String[ts.size()];
            Set<Integer> excluded = new HashSet<>();
            for (int j = 0; j < ts.size(); j++) {
                preseed[j] = Grids.wordAt(ts.get(j), confirmed);
                Cells.Key k = Cells.key(ts.get(j));
                if (emptyTuples.contains(k) || impossibleTuples.contains(k)) excluded.add(j);
            }
            Fill.FillArgs fa = new Fill.FillArgs();
            fa.deadlineChecks = deadlineChecks;
            fa.preseedAssignment = preseed;
            fa.excludedSlots = excluded;
            fa.cancelEvent = cancelEvent;
            fa.lockedLetters = permanentLocked == null || permanentLocked.isEmpty() ? null : permanentLocked;
            return Fill.tryFill(g, rows, cols, index, rng, fa);
        };
        char[][] grid = Grids.copy(candGrid);
        Fill.Result initial = tryComplete.apply(grid);
        if (initial != null) absorb.accept(initial);
        boolean improved = true;
        while (improved) {
            improved = false;
            List<Integer> removable = new ArrayList<>();
            for (int r = 0; r < rows; r++) for (int c = 0; c < cols; c++) {
                int cell = Cells.of(r, c);
                if (grid[r][c] == BLACK && !lockedBlack.contains(cell) && !(permanentBlack != null && permanentBlack.contains(cell))) {
                    removable.add(cell);
                }
            }
            rng.shuffle(removable);
            boolean sampling = removable.size() > PER_CYCLE_OPTIMIZATION_SAMPLE_SIZE;
            if (sampling) removable = new ArrayList<>(removable.subList(0, PER_CYCLE_OPTIMIZATION_SAMPLE_SIZE));
            for (int cell : removable) {
                if (cancelEvent != null && cancelEvent.get()) throw new GenerationCancelled();
                int r = Cells.r(cell), c = Cells.c(cell);
                if (grid[r][c] != BLACK) continue;
                char saved = grid[r][c];
                grid[r][c] = WHITE;
                if (Grids.isStructurallyValid(grid, rows, cols, 1)) {
                    Fill.Result res = tryComplete.apply(grid);
                    if (res != null) {
                        absorb.accept(res);
                        improved = true;
                        if (sampling) break;
                        continue;
                    }
                }
                grid[r][c] = saved;
            }
        }
        List<int[]> lcSlots = Grids.extractSlots(grid, rows, cols);
        String[] lcPreseed = new String[lcSlots.size()];
        Set<Integer> lcExcluded = new HashSet<>();
        for (int j = 0; j < lcSlots.size(); j++) {
            lcPreseed[j] = Grids.wordAt(lcSlots.get(j), confirmed);
            if (impossibleTuples.contains(Cells.key(lcSlots.get(j)))) lcExcluded.add(j);
        }
        Diag lcDiag = new Diag();
        Fill.FillArgs fa = new Fill.FillArgs();
        fa.deadlineChecks = deadlineChecks;
        fa.preseedAssignment = lcPreseed;
        fa.excludedSlots = lcExcluded;
        fa.cancelEvent = cancelEvent;
        fa.lockedLetters = permanentLocked == null || permanentLocked.isEmpty() ? null : permanentLocked;
        fa.diagnostics = lcDiag;
        Fill.tryFill(grid, rows, cols, index, rng, fa);
        if (lcDiag.assignment != null && lcDiag.assignment.length > 0) absorb.accept(new Fill.Result(lcSlots, lcDiag.assignment));
        List<int[]> finalSlots = Grids.extractSlots(grid, rows, cols);
        String[] finalAssignment = new String[finalSlots.size()];
        for (int j = 0; j < finalSlots.size(); j++) finalAssignment[j] = Grids.wordAt(finalSlots.get(j), confirmed);
        Set<Integer> invalid = new HashSet<>(invalidFullyKnownIndices(finalSlots, index, confirmed,
                exemptCells(permanentLocked, finalSlots, confirmed, challenge)));
        for (int j : invalid) finalAssignment[j] = null;
        TreeSet<Integer> finalImpossible = new TreeSet<>(invalid);
        finalImpossible.addAll(impossibleIndices(finalSlots, index, confirmed, challenge));
        finalImpossible.removeAll(challengeFillableSlotIndices(finalSlots, confirmed, challenge));
        Object[] partial = Grids.buildPartialLettersGrid(grid, finalSlots, finalAssignment, null, null);
        TreeSet<Integer> lockedCells = new TreeSet<>(lockedBlack);
        for (Cells.Key k : emptyTuples) for (int c : k.cells) lockedCells.add(c);
        Diag nd = candDiag.copy();
        nd.assignment = finalAssignment;
        nd.impossibleSlots = new ArrayList<>(finalImpossible);
        nd.exampleGrid = (char[][]) partial[0];
        nd.lockedCells = new ArrayList<>(lockedCells);
        return new Object[]{grid, nd};
    }

    /** Cleaned "reprise telle quelle" candidate: {seed grid, confirmed, slots,
     * preseed assignment, excluded slots, process number}. */
    public record ContinueCandidate(char[][] seedGrid, Map<Integer, Character> confirmed, List<int[]> slots,
                                    String[] preseed, Set<Integer> excluded, Integer processNumber) {}

    public static ContinueCandidate cleanContinueCandidate(char[][] candGrid, Diag candDiag, int rows, int cols,
                                                           DualIndex index, Rng rng,
                                                           Map<Integer, Character> permanentLocked,
                                                           Set<Integer> permanentBlack, Set<String> challenge) {
        List<int[]> slots0 = Grids.extractSlots(candGrid, rows, cols);
        Zones z = shortenImpossibleZones(candGrid, rows, cols, slots0, candDiag.assignment, candDiag.impossibleSlots,
                index, rng, permanentLocked, challenge);
        z = lengthenImpossibleZones(z.grid(), rows, cols, z.slots(), z.assignment(), z.impossible(), index, rng,
                permanentLocked, permanentBlack, challenge, z.links());
        Map<Integer, Character> known = knownFromAssignment(z.slots(), z.assignment(), null);
        Set<Integer> deadlocked = crossingDeadlockIndices(z.slots(), index, known, challenge);
        Object[] cleaned = cleanBlockedSlots(z.slots(), z.assignment(), z.impossible(), null, false, index, rng, z.grid(),
                rows, cols, permanentLocked, z.links(), deadlocked, false);
        String[] cleanedAssignment = (String[]) cleaned[0];
        @SuppressWarnings("unchecked")
        Map<Integer, Character> confirmed = (Map<Integer, Character>) cleaned[1];
        @SuppressWarnings("unchecked")
        Set<Integer> newBlack = (Set<Integer>) cleaned[2];
        @SuppressWarnings("unchecked")
        Set<Integer> reopened = (Set<Integer>) cleaned[3];
        if (!newBlack.isEmpty() || !reopened.isEmpty()) {
            char[][] seed = Grids.copy(z.grid());
            for (int c : newBlack) seed[Cells.r(c)][Cells.c(c)] = BLACK;
            for (int c : reopened) seed[Cells.r(c)][Cells.c(c)] = WHITE;
            List<int[]> newSlots = Grids.extractSlots(seed, rows, cols);
            String[] preseed = new String[newSlots.size()];
            for (int j = 0; j < newSlots.size(); j++) preseed[j] = Grids.wordAt(newSlots.get(j), confirmed);
            for (int j : invalidFullyKnownIndices(newSlots, index, confirmed, exemptCells(permanentLocked, newSlots, confirmed, challenge))) {
                preseed[j] = null;
            }
            Set<Cells.Key> oldImp = new HashSet<>();
            for (int i : z.impossible()) oldImp.add(Cells.key(z.slots().get(i)));
            Set<Integer> excluded = new HashSet<>();
            for (int j = 0; j < newSlots.size(); j++) if (oldImp.contains(Cells.key(newSlots.get(j)))) excluded.add(j);
            return new ContinueCandidate(seed, confirmed, newSlots, preseed, excluded, candDiag.processNumber);
        }
        return new ContinueCandidate(z.grid(), confirmed, z.slots(), cleanedAssignment, new HashSet<>(z.impossible()),
                candDiag.processNumber);
    }

    /** Lineage numbering (Python's _reassign_lineage_numbers). Returns {list, next}. */
    public static Object[] reassignLineageNumbers(List<Integer> raw, List<Integer> previous, int next) {
        Set<Integer> resolved = new HashSet<>();
        for (Integer n : raw) if (n != null) resolved.add(n);
        TreeSet<Integer> freedSet = new TreeSet<>();
        for (Integer n : previous) if (n != null && !resolved.contains(n)) freedSet.add(n);
        java.util.Iterator<Integer> freed = freedSet.iterator();
        List<Integer> out = new ArrayList<>();
        for (Integer n : raw) {
            if (n != null) {
                out.add(n);
                continue;
            }
            Integer rep = freed.hasNext() ? freed.next() : null;
            if (rep == null) rep = next++;
            out.add(rep);
        }
        return new Object[]{out, next};
    }

    public static List<Integer> buildDispatchLineage(int seedsCount, int resetCount, List<Integer> poolLineage) {
        List<Integer> out = new ArrayList<>();
        for (int i = 0; i < seedsCount; i++) {
            out.add(i < resetCount ? null : poolLineage.get((i - resetCount) % poolLineage.size()));
        }
        return out;
    }

    public static int seedPoolKeep(int sortedSize, int parallel, int resetCount) {
        return Math.max(1, Math.min(sortedSize - 1, parallel - resetCount));
    }

    // ================================================================== widen / shorten for "Mots Défi"

    static List<Integer> whiteRun(char[][] grid, int rows, int cols, int r, int c, int dr, int dc) {
        List<Integer> cells = new ArrayList<>();
        int rr = r + dr, cc = c + dc;
        while (rr >= 0 && rr < rows && cc >= 0 && cc < cols && grid[rr][cc] == WHITE) {
            cells.add(Cells.of(rr, cc));
            rr += dr;
            cc += dc;
        }
        return cells;
    }

    static boolean slotHasDomain(int[] cells, DualIndex index, Map<Integer, Character> locked) {
        LenIndex idx = index.forCells(cells).get(cells.length);
        if (idx == null) return false;
        return !Words.slotCandidates(index, cells.length, cells, locked).isEmpty();
    }

    static int[] arr(List<Integer> l) {
        return l.stream().mapToInt(Integer::intValue).toArray();
    }

    static boolean perpendicularSlotStaysValid(char[][] grid, int rows, int cols, int r, int c, int dr, int dc,
                                               DualIndex index, Map<Integer, Character> locked) {
        if (locked == null || locked.isEmpty()) return true;
        int pdr = dc, pdc = dr;
        List<Integer> before = whiteRun(grid, rows, cols, r, c, -pdr, -pdc);
        java.util.Collections.reverse(before);
        List<Integer> after = whiteRun(grid, rows, cols, r, c, pdr, pdc);
        if (grid[r][c] == BLACK) {
            List<Integer> cells = new ArrayList<>(before);
            cells.add(Cells.of(r, c));
            cells.addAll(after);
            return cells.size() < 2 || slotHasDomain(arr(cells), index, locked);
        }
        return (before.size() < 2 || slotHasDomain(arr(before), index, locked))
                && (after.size() < 2 || slotHasDomain(arr(after), index, locked));
    }

    /** Returns the slot cells carved out, or null. */
    static int[] tryWidenBlackCell(char[][] grid, int rows, int cols, int r, int c, int dr, int dc, String word,
                                   Map<Integer, Character> locked, DualIndex index) {
        List<Integer> before = whiteRun(grid, rows, cols, r, c, -dr, -dc);
        java.util.Collections.reverse(before);
        List<Integer> after = whiteRun(grid, rows, cols, r, c, dr, dc);
        List<Integer> merged = new ArrayList<>(before);
        merged.add(Cells.of(r, c));
        merged.addAll(after);
        int total = merged.size(), wl = word.length();
        if (wl > total) return null;
        int rcIndex = before.size();
        Set<Integer> starts = new TreeSet<>();
        if (wl > before.size()) starts.add(0);
        if (wl > after.size()) starts.add(total - wl);
        for (int start : starts) {
            List<Integer> span = merged.subList(start, start + wl);
            boolean clash = false;
            for (int p = 0; p < wl; p++) {
                Character k = locked.get(span.get(p));
                if (k != null && k != word.charAt(p)) clash = true;
            }
            if (clash) continue;
            Integer newBlack = null;
            if (start == 0) {
                if (wl < total) newBlack = merged.get(wl);
            } else {
                newBlack = merged.get(start - 1);
            }
            if (newBlack != null && locked.containsKey(newBlack)) continue;
            char rcLetter = word.charAt(rcIndex - start);
            Map<Integer, Character> rcLocked = new HashMap<>(locked);
            rcLocked.put(Cells.of(r, c), rcLetter);
            if (!perpendicularSlotStaysValid(grid, rows, cols, r, c, dr, dc, index, rcLocked)) continue;
            if (newBlack != null && !perpendicularSlotStaysValid(grid, rows, cols, Cells.r(newBlack), Cells.c(newBlack), dr,
                    dc, index, locked)) continue;
            char savedNb = newBlack != null ? grid[Cells.r(newBlack)][Cells.c(newBlack)] : 0;
            grid[r][c] = WHITE;
            if (newBlack != null) grid[Cells.r(newBlack)][Cells.c(newBlack)] = BLACK;
            if (Grids.isStructurallyValid(grid, rows, cols, 1)) return arr(span);
            grid[r][c] = BLACK;
            if (newBlack != null) grid[Cells.r(newBlack)][Cells.c(newBlack)] = savedNb;
        }
        return null;
    }

    public static int[] widenOneFloatingBlackCell(char[][] grid, int rows, int cols, Rng rng, String word,
                                                  Map<Integer, Character> locked, Set<Integer> permanentBlack,
                                                  DualIndex index) {
        List<Integer> blacks = new ArrayList<>();
        for (int r = 0; r < rows; r++) for (int c = 0; c < cols; c++) {
            if (grid[r][c] == BLACK && (permanentBlack == null || !permanentBlack.contains(Cells.of(r, c)))) blacks.add(Cells.of(r, c));
        }
        rng.shuffle(blacks);
        for (int k = 0; k < Math.min(WIDEN_BLACK_CELL_WINDOW, blacks.size()); k++) {
            int cell = blacks.get(k);
            int[][] dirs = {{0, 1}, {1, 0}};
            for (int[] d : dirs) {
                int[] change = tryWidenBlackCell(grid, rows, cols, Cells.r(cell), Cells.c(cell), d[0], d[1], word, locked, index);
                if (change != null) return change;
            }
        }
        return null;
    }

    static int[] tryShortenSlot(char[][] grid, int rows, int cols, int[] cells, String word, Map<Integer, Character> locked,
                                DualIndex index) {
        int total = cells.length, wl = word.length();
        if (wl >= total || wl < 2) return null;
        int dr = Cells.r(cells[1]) - Cells.r(cells[0]);
        int dc = Cells.c(cells[1]) - Cells.c(cells[0]);
        int[][] options = {{0, cells[wl]}, {total - wl, cells[total - wl - 1]}};
        for (int[] o : options) {
            int start = o[0], newBlack = o[1];
            if (locked.containsKey(newBlack)) continue;
            int[] span = Arrays.copyOfRange(cells, start, start + wl);
            boolean clash = false;
            for (int p = 0; p < wl; p++) {
                Character k = locked.get(span[p]);
                if (k != null && k != word.charAt(p)) clash = true;
            }
            if (clash) continue;
            if (!perpendicularSlotStaysValid(grid, rows, cols, Cells.r(newBlack), Cells.c(newBlack), dr, dc, index, locked)) continue;
            char saved = grid[Cells.r(newBlack)][Cells.c(newBlack)];
            grid[Cells.r(newBlack)][Cells.c(newBlack)] = BLACK;
            if (Grids.isStructurallyValid(grid, rows, cols, 1)) return span;
            grid[Cells.r(newBlack)][Cells.c(newBlack)] = saved;
        }
        return null;
    }

    public static int[] shortenOneSlotForWord(char[][] grid, int rows, int cols, Rng rng, String word,
                                              Map<Integer, Character> locked, DualIndex index) {
        List<int[]> slots = new ArrayList<>();
        for (int[] cells : Grids.extractSlots(grid, rows, cols)) if (cells.length > word.length()) slots.add(cells);
        rng.shuffle(slots);
        for (int k = 0; k < Math.min(SHORTEN_SLOT_WINDOW, slots.size()); k++) {
            int[] change = tryShortenSlot(grid, rows, cols, slots.get(k), word, locked, index);
            if (change != null) return change;
        }
        return null;
    }

    static Map<Integer, List<int[]>> slotsByLength(char[][] grid, int rows, int cols) {
        Map<Integer, List<int[]>> by = new LinkedHashMap<>();
        for (int[] cells : Grids.extractSlots(grid, rows, cols)) by.computeIfAbsent(cells.length, k -> new ArrayList<>()).add(cells);
        return by;
    }

    /** Python's _free_matching_slot (claims it). */
    static int[] freeMatchingSlot(Map<Integer, List<int[]>> byLength, String word, Map<Integer, Character> locked,
                                  Set<Cells.Key> claimed) {
        for (int[] cells : byLength.getOrDefault(word.length(), List.of())) {
            Cells.Key key = Cells.key(cells);
            if (claimed.contains(key)) continue;
            boolean ok = true;
            for (int p = 0; p < cells.length; p++) {
                Character k = locked.get(cells[p]);
                if (k != null && k != word.charAt(p)) {
                    ok = false;
                    break;
                }
            }
            if (ok) {
                claimed.add(key);
                return cells;
            }
        }
        return null;
    }
}
