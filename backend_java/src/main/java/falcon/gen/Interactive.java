package falcon.gen;

import falcon.gen.Words.DualIndex;
import falcon.gen.Words.PW;

import java.util.ArrayList;
import java.util.Arrays;
import java.util.Collection;
import java.util.HashMap;
import java.util.HashSet;
import java.util.Iterator;
import java.util.LinkedHashMap;
import java.util.LinkedHashSet;
import java.util.List;
import java.util.Map;
import java.util.NoSuchElementException;
import java.util.Set;
import java.util.TreeMap;
import java.util.TreeSet;
import java.util.function.BiPredicate;
import java.util.function.Consumer;
import java.util.function.IntConsumer;
import java.util.function.Predicate;

import static falcon.gen.Grids.BLACK;
import static falcon.gen.Grids.WHITE;

/** "Interactif" authoring mode (mirrors the interactive_* functions of
 * crossword_gen.py). Grids mix '#', '.' and letters. */
public final class Interactive {
    private Interactive() {}

    public static final int PLACEMENT_LEVEL_STRICT = 0;
    public static final int PLACEMENT_LEVEL_DISJOINT = 1;
    public static final int PLACEMENT_LEVEL_BREAKING = 2;
    public static final int INTERACTIVE_SLOT_CANDIDATES_LIMIT = 300;
    static final Map<String, Integer> BREAK_VERDICT_RANK = Map.of("other", 1, "crossing", 2, "crossing_blocked", 3);

    // ================================================================== diagnostics

    /** Returns {impossible, low, deadlock} as sorted [[r, c]] lists. */
    @SuppressWarnings("unchecked")
    public static List<Object>[] fillDiagnostics(char[][] grid, int rows, int cols, DualIndex index, Set<String> challenge) {
        char[][] pattern = Grids.patternOf(grid);
        List<int[]> slots = Grids.extractSlots(pattern, rows, cols);
        if (slots.isEmpty()) return new List[]{new ArrayList<>(), new ArrayList<>(), new ArrayList<>()};
        Map<Integer, Character> known = Grids.knownLetters(grid);
        Rng scratch = new Rng(0);
        Object[] biases = Fill.sampleLetterBiases(pattern, rows, cols, index, scratch, 0.0, null, known);
        Filler filler = new Filler(slots, index, scratch, null, (Map<Integer, int[][]>) biases[1], null, null, null, null,
                null, known, null, null, rows, cols);
        for (int i = 0; i < slots.size(); i++) {
            String w = Grids.wordAt(slots.get(i), known);
            if (w != null) {
                filler.assignment[i] = w;
                filler.usedWords.add(w);
            }
        }
        Set<String> cw = challenge == null ? Set.of() : challenge;
        Set<Integer> fillable = Cleanup.challengeFillableSlotIndices(slots, known, cw);
        Object[] dl = filler.crossingDeadlockSlots(filler.usedWords, cw);
        Set<Integer> deadlocked = (Set<Integer>) dl[0];
        Set<Integer> deadlockCells = (Set<Integer>) dl[1];
        Set<Integer> impossible = new HashSet<>(), low = new HashSet<>();
        for (int i = 0; i < slots.size(); i++) {
            if (filler.assignment[i] != null) continue;
            int n = filler.domain(i).minus(filler.usedWords).size();
            int[] cells = slots.get(i);
            if (n == 0) {
                for (int c : cells) (fillable.contains(i) ? low : impossible).add(c);
            } else if (deadlocked.contains(i)) {
                for (int c : cells) impossible.add(c);
            } else if (n < Grids.PREFILL_MIN_WORD_COUNT) {
                for (int c : cells) low.add(c);
            }
        }
        Set<Integer> exempt = Cleanup.challengeWordCells(slots, known, cw);
        for (int i : Cleanup.invalidFullyKnownIndices(slots, index, known, exempt)) for (int c : slots.get(i)) impossible.add(c);
        return new List[]{Cells.sortedJson(impossible), Cells.sortedJson(low), Cells.sortedJson(deadlockCells)};
    }

    public static List<Object> letterStats(char[][] grid, int rows, int cols, DualIndex index) {
        char[][] pattern = Grids.patternOf(grid);
        List<int[]> slots = Grids.extractSlots(pattern, rows, cols);
        List<Object> out = new ArrayList<>();
        if (slots.isEmpty()) return out;
        Map<Integer, Character> known = Grids.knownLetters(grid);
        Object[] biases = Fill.sampleLetterBiases(pattern, rows, cols, index, new Rng(0), 0.0, null, known);
        @SuppressWarnings("unchecked")
        Map<Integer, int[][]> scores = (Map<Integer, int[][]>) biases[1];
        for (int r = 0; r < rows; r++) {
            for (int c = 0; c < cols; c++) {
                if (grid[r][c] != WHITE) continue;
                int[][] byDir = scores.get(Cells.of(r, c));
                if (byDir == null) continue;
                Character letter = Tally.mostProbable(byDir);
                if (letter == null) continue;
                out.add(List.of(r, c, String.valueOf(letter)));
            }
        }
        return out;
    }

    // ================================================================== placement checks

    static boolean placementAccepted(String verdict, int level) {
        if (verdict == null) return true;
        if (verdict.equals("crossing_blocked")) return false;
        if (verdict.equals("other")) return level >= PLACEMENT_LEVEL_DISJOINT;
        return level >= PLACEMENT_LEVEL_BREAKING;
    }

    /** One open slot's candidate set before a candidate is tried (domain minus
     * the words used at that moment). */
    static final class Base {
        final Dom dom;
        final Set<String> used;
        final int size;

        Base(Dom dom, Set<String> used) {
            this.dom = dom;
            this.used = used;
            int n = 0;
            for (String w : dom) if (!used.contains(w)) n++;
            this.size = n;
        }

        boolean contains(String w) {
            return dom.contains(w) && !used.contains(w);
        }

        boolean emptyWithout(String w) {
            return size == 0 || (size == 1 && contains(w));
        }
    }

    static Map<Integer, Base> openSlotBaseline(Filler f, int exclude) {
        Map<Integer, Base> baseline = new LinkedHashMap<>();
        Set<String> used = new HashSet<>(f.usedWords);
        for (int j = 0; j < f.slots.size(); j++) {
            if (j == exclude || f.assignment[j] != null) continue;
            baseline.put(j, new Base(f.domain(j), used));
        }
        return baseline;
    }

    static String wordBreaksOpenSlot(Filler f, int i, String w, Set<String> active, Map<Integer, Base> baseline) {
        f.assignment[i] = w;
        f.usedWords.add(w);
        String broken = null;
        Map<Object, Filler.OptionsEntry> cache = new HashMap<>();
        Set<Integer> crossing = new HashSet<>();
        for (int j : f.crossingSlots[i]) crossing.add(j);
        for (Map.Entry<Integer, Base> e : baseline.entrySet()) {
            int j = e.getKey();
            Base before = e.getValue();
            boolean isCrossing = crossing.contains(j);
            if (before.size == 0 && !isCrossing) continue;
            boolean blocked;
            if (isCrossing) {
                blocked = f.slotIsBlocked(j, f.usedWords, active, cache, f.crossingSlots[i], w);
            } else {
                blocked = before.emptyWithout(w) && !challengeCanFill(f, j, active);
            }
            if (!blocked) continue;
            String verdict;
            if (!isCrossing) verdict = "other";
            else if (before.size > 0 || challengeCanFill(f, j, active)) verdict = "crossing";
            else verdict = "crossing_blocked";
            if (broken == null || BREAK_VERDICT_RANK.get(verdict) > BREAK_VERDICT_RANK.get(broken)) broken = verdict;
            if (verdict.equals("crossing_blocked")) break;
        }
        f.assignment[i] = null;
        f.usedWords.remove(w);
        return broken;
    }

    static boolean challengeCanFill(Filler f, int j, Set<String> active) {
        if (active == null || active.isEmpty()) return false;
        for (String w : active) if (!f.usedWords.contains(w) && f.challengeWordFits(j, w)) return true;
        return false;
    }

    static Object[] buildInteractiveFiller(char[][] pattern, int rows, int cols, DualIndex index, Rng rng,
                                           Map<Integer, Character> known, PW pw, Set<String> challenge) {
        List<int[]> slots = Grids.extractSlots(pattern, rows, cols);
        Object[] biases = Fill.sampleLetterBiases(pattern, rows, cols, index, rng, 0.0, null, known);
        @SuppressWarnings("unchecked")
        Map<Integer, int[][]> scores = (Map<Integer, int[][]>) biases[1];
        Filler f = new Filler(slots, index, rng, null, scores, null, null, null, null, null, known, pw,
                challenge == null ? Set.of() : challenge, rows, cols);
        for (int i = 0; i < slots.size(); i++) {
            String w = Grids.wordAt(slots.get(i), known);
            if (w != null) {
                f.assignment[i] = w;
                f.usedWords.add(w);
            }
        }
        return new Object[]{f, slots};
    }

    /** A reshaped candidate's extra acceptance test (mirrors fits_reshaped). */
    interface ReshapeFit {
        boolean test(Filler f, int j, String w);
    }

    /** Returns {slot index, word, cells, pattern} or null. */
    static Object[] findPriorityWordPlacement(Collection<String> poolFlat, Map<Integer, Set<String>> viable, Filler f,
                                              List<int[]> slots, int target, char[][] basePattern, int rows, int cols,
                                              Rng rng, DualIndex index, Map<Integer, Character> known, PW pw,
                                              Set<String> challenge, BiPredicate<Integer, String> fitsOrdinary,
                                              Predicate<String> isEligible, Consumer<String> registerBreak,
                                              IntConsumer setBudget, int level, boolean allowReshape,
                                              ReshapeFit fitsReshaped) {
        Map<Integer, List<String>> bySlot = new LinkedHashMap<>();
        for (int i : viable.keySet()) {
            List<String> words = new ArrayList<>();
            for (String w : poolFlat) if (fitsOrdinary.test(i, w)) words.add(w);
            if (!words.isEmpty()) bySlot.put(i, f.orderedCandidates(i, words));
        }
        List<Integer> order = new ArrayList<>(bySlot.keySet());
        Map<Integer, Double> maxScore = new HashMap<>();
        for (int i : order) {
            double m = Double.NEGATIVE_INFINITY;
            for (String w : bySlot.get(i)) m = Math.max(m, f.candidateScore(i, w));
            maxScore.put(i, m);
        }
        order.sort((a, b) -> {
            int ta = a == target ? 0 : 1, tb = b == target ? 0 : 1;
            if (ta != tb) return Integer.compare(ta, tb);
            return Double.compare(maxScore.get(b), maxScore.get(a));
        });
        List<Object[]> combos = new ArrayList<>();
        for (int i : order) for (String w : bySlot.get(i)) combos.add(new Object[]{i, w});
        Set<Cells.Key> claimed = new HashSet<>();
        Map<Integer, List<int[]>> byLength = Cleanup.slotsByLength(basePattern, rows, cols);
        Set<String> ordinaryWords = new HashSet<>();
        for (Object[] c : combos) ordinaryWords.add((String) c[1]);
        List<String> reshapeWords = new ArrayList<>();
        if (allowReshape) {
            for (String w : poolFlat) {
                if (!ordinaryWords.contains(w) && w.length() >= 2 && Cleanup.freeMatchingSlot(byLength, w, known, claimed) == null) {
                    reshapeWords.add(w);
                }
            }
        }
        rng.shuffle(reshapeWords);
        if (reshapeWords.size() > Cleanup.WIDEN_PRIORITY_WORDS_LIMIT) {
            reshapeWords = new ArrayList<>(reshapeWords.subList(0, Cleanup.WIDEN_PRIORITY_WORDS_LIMIT));
        }
        setBudget.accept((int) Math.max(1, Math.rint(Filler.FALLBACK_PHASE_BUDGET_FRACTION * (combos.size() + reshapeWords.size()))));
        Map<Integer, Map<Integer, Base>> baselineCache = new HashMap<>();
        for (Object[] c : combos) {
            int i = (int) c[0];
            String w = (String) c[1];
            if (f.usedWords.contains(w) || !isEligible.test(w)) continue;
            Set<String> exemption = new LinkedHashSet<>(f.activeChallengeWords());
            exemption.removeAll(f.usedWords);
            Map<Integer, Base> baseline = baselineCache.computeIfAbsent(i, k -> openSlotBaseline(f, k));
            String verdict = wordBreaksOpenSlot(f, i, w, exemption, baseline);
            if (!placementAccepted(verdict, level)) {
                registerBreak.accept(w);
                continue;
            }
            return new Object[]{i, w, slots.get(i), basePattern};
        }
        for (String w : reshapeWords) {
            if (f.usedWords.contains(w) || !isEligible.test(w)) continue;
            char[][] copy = Grids.copy(basePattern);
            int[] change = Cleanup.widenOneFloatingBlackCell(copy, rows, cols, rng, w, known, new HashSet<>(), index);
            if (change == null) change = Cleanup.shortenOneSlotForWord(copy, rows, cols, rng, w, known, index);
            if (change == null) {
                registerBreak.accept(w);
                continue;
            }
            Object[] built = buildInteractiveFiller(copy, rows, cols, index, rng, known, pw, challenge);
            Filler cf = (Filler) built[0];
            @SuppressWarnings("unchecked")
            List<int[]> cs = (List<int[]>) built[1];
            int j = Grids.indexOfSlot(cs, change);
            if (j < 0) {
                registerBreak.accept(w);
                continue;
            }
            if (fitsReshaped != null && !fitsReshaped.test(cf, j, w)) {
                registerBreak.accept(w);
                continue;
            }
            Set<String> exemption = new LinkedHashSet<>(f.activeChallengeWords());
            exemption.removeAll(f.usedWords);
            String verdict = wordBreaksOpenSlot(cf, j, w, exemption, openSlotBaseline(cf, j));
            if (!placementAccepted(verdict, level)) {
                registerBreak.accept(w);
                continue;
            }
            return new Object[]{j, w, change, copy};
        }
        return null;
    }

    static List<Object> slotCellsOf(List<int[]> slots, Collection<Integer> indices) {
        List<Object> cells = new ArrayList<>();
        Set<Integer> seen = new HashSet<>();
        for (int i : new TreeSet<>(indices)) {
            for (int cell : slots.get(i)) {
                if (!seen.add(cell)) continue;
                cells.add(List.of(Cells.r(cell), Cells.c(cell)));
            }
        }
        return cells;
    }

    /** Every candidate in cascade order, each paired with the level-6 window
     *  its selection drew it from (firstWindow for first) (mirrors
     *  _cascade_slot_order). */
    static Iterable<Map.Entry<Integer, List<Integer>>> cascadeSlotOrder(Filler f, List<Integer> candidates,
                                              Map<Integer, Dom> domains, Integer first, List<Integer> firstWindow,
                                              boolean challengeLevel, boolean themeLevel) {
        return () -> new Iterator<>() {
            final List<Integer> remaining = new ArrayList<>(candidates);
            boolean firstDone = !(first != null && candidates.contains(first));

            @Override
            public boolean hasNext() {
                return !remaining.isEmpty();
            }

            @Override
            public Map.Entry<Integer, List<Integer>> next() {
                if (remaining.isEmpty()) throw new NoSuchElementException();
                if (!firstDone) {
                    firstDone = true;
                    remaining.remove(first);
                    return Map.entry(first, new ArrayList<>(firstWindow == null ? List.of() : firstWindow));
                }
                int i = f.selectTargetSlot(new ArrayList<>(remaining), domains, challengeLevel, themeLevel);
                remaining.remove(Integer.valueOf(i));
                return Map.entry(i, new ArrayList<>(f.lastSelectionWindow));
            }
        };
    }

    static String generalDictionaryPick(Filler f, Map<Integer, Set<String>> viable, int i, Set<String> exclude,
                                        Map<Integer, Map<Integer, Base>> baselines, int level) {
        Set<String> cands = viable.get(i);
        Set<String> plain = cands;
        if (exclude != null && !exclude.isEmpty()) {
            plain = new LinkedHashSet<>(cands);
            plain.removeAll(exclude);
        }
        Set<String> active = new LinkedHashSet<>(f.activeChallengeWords());
        active.removeAll(f.usedWords);
        Map<Integer, Base> baseline = baselines.computeIfAbsent(i, k -> openSlotBaseline(f, k));
        // Same order as the automatic search: the Scrabble dictionary's words ahead of the rest.
        String word = firstAcceptable(f, i, f.scrabbleFirst(i, f.orderedCandidates(i, plain)), active, baseline, level);
        if (word == null && !plain.equals(cands)) {
            word = firstAcceptable(f, i, f.scrabbleFirst(i, f.orderedCandidates(i, cands)), active, baseline, level);
        }
        return word;
    }

    static String firstAcceptable(Filler f, int i, List<String> ranked, Set<String> active, Map<Integer, Base> baseline,
                                  int level) {
        for (String w : ranked) {
            if (placementAccepted(wordBreaksOpenSlot(f, i, w, active, baseline), level)) return w;
        }
        return null;
    }

    /** The last word "Suivant" placed ({row, col} pairs) as Cells, or null
     *  unless they are at least two cells forming one straight, contiguous
     *  run whose every cell still carries a letter in known (mirrors
     *  _placed_word_origin_cells). */
    static int[] placedWordOriginCells(List<int[]> cells, Map<Integer, Character> known) {
        if (cells == null) return null;
        TreeSet<Long> keys = new TreeSet<>();
        for (int[] rc : cells) {
            if (rc.length != 2) return null;
            keys.add(((long) rc[0] << 32) | (rc[1] & 0xffffffffL));
        }
        if (keys.size() < 2) return null;
        int[] out = new int[keys.size()];
        int k = 0, r0 = 0, c0 = 0;
        boolean row = true, col = true;
        for (long key : keys) {
            int r = (int) (key >> 32), c = (int) key;
            if (k == 0) { r0 = r; c0 = c; }
            row &= r == r0 && c == c0 + k;
            col &= c == c0 && r == r0 + k;
            if (r < 0 || c < 0 || r > 0xffff || c > 0xffff) return null;
            out[k++] = Cells.of(r, c);
            if (!known.containsKey(out[k - 1])) return null;
        }
        return row || col ? out : null;
    }

    /** Each slot's cell(s) closest to the level-6 origin — the midpoint of
     *  the grid's top-left corner and the center of the last word "Suivant"
     *  placed, or the top-left corner before any (mirrors
     *  _origin_closest_cells). */
    static List<Object> originClosestCells(Filler f, Collection<Integer> slotIndices) {
        double[] origin = f.selectionOrigin();
        double cr = origin[0], cc = origin[1];
        TreeSet<Long> out = new TreeSet<>();
        for (int i : slotIndices) {
            int[] slot = f.slots.get(i);
            double best = Double.MAX_VALUE;
            for (int cell : slot) {
                double dr = Cells.r(cell) - cr, dc = Cells.c(cell) - cc;
                best = Math.min(best, dr * dr + dc * dc);
            }
            for (int cell : slot) {
                double dr = Cells.r(cell) - cr, dc = Cells.c(cell) - cc;
                if (dr * dr + dc * dc == best) out.add(((long) Cells.r(cell) << 32) | Cells.c(cell));
            }
        }
        List<Object> cells = new ArrayList<>();
        for (long k : out) cells.add(List.of((int) (k >> 32), (int) k));
        return cells;
    }

    static Map<String, Object> impossibleResult(char[][] grid, int rows, int cols, DualIndex index, Set<String> challenge,
                                                List<Object> excluded, List<Object> windowCells) {
        List<Object>[] d = fillDiagnostics(grid, rows, cols, index, challenge);
        Map<String, Object> m = new LinkedHashMap<>();
        m.put("impossible", true);
        m.put("impossible_cells", d[0]);
        m.put("low_candidate_cells", d[1]);
        m.put("deadlock_cells", d[2]);
        m.put("excluded_cells", excluded);
        m.put("window_cells", windowCells);
        return m;
    }

    // ================================================================== "Suivant"

    public static Map<String, Object> placeWord(char[][] grid, int rows, int cols, DualIndex index, Rng rng, PW pwIn,
                                                Set<String> challengeIn, PW scrabble, int multiWordPercent) {
        return placeWord(grid, rows, cols, index, rng, pwIn, challengeIn, null, scrabble, multiWordPercent, null, null);
    }

    /** The slots lying entirely inside the zone selected in the grid ({@code zoneCells}, [row, col] pairs), the only
     * ones "Suivant" may write while a zone is selected; null when none is (mirrors _zone_slot_indices). */
    static Set<Integer> zoneSlotIndices(List<int[]> slots, List<int[]> zoneCells) {
        if (zoneCells == null || zoneCells.isEmpty()) return null;
        Set<Integer> zone = new HashSet<>();
        for (int[] rc : zoneCells) if (rc.length == 2) zone.add(Cells.of(rc[0], rc[1]));
        Set<Integer> out = new LinkedHashSet<>();
        for (int i = 0; i < slots.size(); i++) {
            boolean inside = true;
            for (int cell : slots.get(i)) if (!zone.contains(cell)) inside = false;
            if (inside) out.add(i);
        }
        return out;
    }

    /** True when every slot of {@code zoneSlots} has all its cells lettered (mirrors _zone_complete). */
    static boolean zoneComplete(List<int[]> slots, Set<Integer> zoneSlots, Map<Integer, Character> known) {
        for (int i : zoneSlots) for (int cell : slots.get(i)) if (!known.containsKey(cell)) return false;
        return true;
    }

    /** "Suivant"'s answer when the selected zone is complete (mirrors _zone_complete_result). */
    static Map<String, Object> zoneCompleteResult(char[][] grid, int rows, int cols, DualIndex index,
                                                  Set<String> challenge) {
        List<Object>[] d = fillDiagnostics(grid, rows, cols, index, challenge);
        Map<String, Object> m = new LinkedHashMap<>();
        m.put("impossible", false);
        m.put("zone_complete", true);
        m.put("grid", Grids.toJson(grid));
        m.put("impossible_cells", d[0]);
        m.put("low_candidate_cells", d[1]);
        m.put("deadlock_cells", d[2]);
        m.put("excluded_cells", new ArrayList<>());
        m.put("window_cells", new ArrayList<>());
        return m;
    }

    /** Index in {@code slots} of the emplacement selected in the grid ({@code selectedCells}, [row, col] pairs),
     * when it still holds an empty cell; null otherwise (mirrors _selected_slot_index). */
    static Integer selectedSlotIndex(List<int[]> slots, List<int[]> selectedCells, Map<Integer, Character> known) {
        if (selectedCells == null || selectedCells.isEmpty()) return null;
        int[] wanted = new int[selectedCells.size()];
        for (int k = 0; k < wanted.length; k++) {
            int[] rc = selectedCells.get(k);
            if (rc.length != 2) return null;
            wanted[k] = Cells.of(rc[0], rc[1]);
        }
        for (int i = 0; i < slots.size(); i++) {
            if (!Arrays.equals(slots.get(i), wanted)) continue;
            for (int cell : wanted) if (!known.containsKey(cell)) return i;
            return null;
        }
        return null;
    }

    /** {@code multiWordPercent}: the cap on multi-word expressions (Filler.MULTI_WORD_PERCENT), applied to every
     * candidate drawn through Filler.orderedCandidates (mirrors interactive_place_word's multi_word_percent).
     * {@code selectedCells}: the emplacement selected in the grid, tried first while incomplete (mirrors
     * selected_cells). {@code zoneCells}: the zone selected in the grid, the only slots written being those inside it
     * (mirrors zone_cells). */
    public static Map<String, Object> placeWord(char[][] grid, int rows, int cols, DualIndex index, Rng rng, PW pwIn,
                                                Set<String> challengeIn, List<int[]> lastPlacedCells, PW scrabble,
                                                int multiWordPercent, List<int[]> selectedCells,
                                                List<int[]> zoneCells) {
        PW pw = pwIn == null ? PW.EMPTY : pwIn;
        Set<String> challenge = challengeIn == null ? Set.of() : challengeIn;
        char[][] pattern = Grids.patternOf(grid);
        Map<Integer, Character> known = Grids.knownLetters(grid);
        Object[] built = buildInteractiveFiller(pattern, rows, cols, index, rng, known, pw, challenge);
        Filler f = (Filler) built[0];
        f.scrabbleWords = scrabble == null ? PW.EMPTY : scrabble;
        f.multiWordPercent = multiWordPercent;
        @SuppressWarnings("unchecked")
        List<int[]> slots = (List<int[]>) built[1];
        if (slots.isEmpty()) return new LinkedHashMap<>(Map.of("impossible", true));
        f.lastPlacedCells = placedWordOriginCells(lastPlacedCells, known);
        Set<Integer> zoneSlots = zoneSlotIndices(slots, zoneCells);
        if (zoneSlots != null && zoneComplete(slots, zoneSlots, known)) {
            return zoneCompleteResult(grid, rows, cols, index, challenge);
        }
        Map<Integer, Dom> domains = new LinkedHashMap<>();
        Map<Integer, Set<String>> viable = new LinkedHashMap<>();
        for (int i = 0; i < slots.size(); i++) {
            if (f.assignment[i] != null || (zoneSlots != null && !zoneSlots.contains(i))) continue;
            Dom d = f.domain(i);
            Set<String> cands = new LinkedHashSet<>(d.minus(f.usedWords));
            if (!cands.isEmpty()) {
                domains.put(i, d);
                viable.put(i, cands);
            }
        }
        if (viable.isEmpty()) return impossibleResult(grid, rows, cols, index, challenge, new ArrayList<>(), new ArrayList<>());
        // Whether the theme tier may still reshape the grid (mirrors theme_reshape).
        final boolean themeReshape = zoneSlots == null && Cleanup.themeReshapeAllowed(Fill.placedThemeWords(slots, f.assignment, f.priorityWords));
        Set<Integer> blockedTargets = new HashSet<>(Cleanup.impossibleIndices(slots, index, known, challenge));
        List<Integer> selectable = new ArrayList<>();
        for (int i : viable.keySet()) if (!blockedTargets.contains(i)) selectable.add(i);
        if (selectable.isEmpty()) selectable = new ArrayList<>(viable.keySet());
        // Each tier's own target, selected against that tier's own glossary
        // (mirrors _tier_target): "challenge" -> level 2 alone, "theme" ->
        // level 5 alone, "dictionary" -> neither; resolved lazily, once per call.
        final List<Integer> selectableF = selectable;
        Map<String, Object[]> tierTargets = new HashMap<>();
        List<Object>[] windowCellsRef = new List[]{new ArrayList<>()};
        java.util.function.Function<String, Integer> tierTarget = tier -> {
            Object[] t = tierTargets.computeIfAbsent(tier, k -> {
                int ti = f.selectTargetSlot(selectableF, domains, k.equals("challenge"), k.equals("theme"));
                List<Integer> window = new ArrayList<>(f.lastSelectionWindow);
                return new Object[]{ti, originClosestCells(f, window), window};
            });
            @SuppressWarnings("unchecked") List<Object> w = (List<Object>) t[1];
            windowCellsRef[0] = w;
            return (Integer) t[0];
        };
        String placedFrom = null;
        Integer placedTarget = null;
        String placedWord = null;
        int[] cells = null;
        char[][] patternToCommit = null;
        Filler.RecentSlots setAside = new Filler.RecentSlots(Filler.MAX_EXCLUDED_SLOTS);
        Set<String> exclude = new HashSet<>(challenge);
        exclude.addAll(pw.flatten());
        Set<Integer> selectableSet = new HashSet<>(selectable);
        List<Integer> blockedViable = new ArrayList<>();
        for (int i : viable.keySet()) if (!selectableSet.contains(i)) blockedViable.add(i);
        Map<Integer, Map<Integer, Base>> baselines = new HashMap<>();
        // The selected emplacement, while incomplete, takes the word whenever one can go there: the same tiers and
        // levels restricted to it, no reshape (mirrors the focus pass of interactive_place_word).
        Integer focus = selectedSlotIndex(slots, selectedCells, known);
        if (zoneSlots != null && focus != null && !zoneSlots.contains(focus)) focus = null;
        if (focus != null) {
            Map<Integer, Set<String>> focusViable = new LinkedHashMap<>();
            focusViable.put(focus, viable.getOrDefault(focus, new LinkedHashSet<>()));
            for (int level : new int[]{PLACEMENT_LEVEL_STRICT, PLACEMENT_LEVEL_DISJOINT, PLACEMENT_LEVEL_BREAKING}) {
                Object[] found = null;
                Set<String> challengePool = new LinkedHashSet<>(f.activeChallengeWords());
                challengePool.removeAll(f.usedWords);
                if (!challengePool.isEmpty()) {
                    found = findPriorityWordPlacement(challengePool, focusViable, f, slots, focus, pattern, rows, cols,
                            rng, index, known, pw, challenge, f::challengeWordFits,
                            w -> !f.challengeAbandoned.contains(w), f::registerChallengeWordBreak,
                            v -> f.challengeWordBudget = v, level, false, null);
                    if (found != null) placedFrom = "challenge";
                }
                if (found == null && !pw.isEmpty()) {
                    Set<String> themePool = new LinkedHashSet<>(pw.flatten());
                    themePool.removeAll(f.usedWords);
                    if (!themePool.isEmpty()) {
                        BiPredicate<Integer, String> themeFits = (i, w) -> focusViable.get(i).contains(w)
                                && f.priorityWords.forCells(slots.get(i)).contains(w);
                        found = findPriorityWordPlacement(themePool, focusViable, f, slots, focus, pattern, rows, cols,
                                rng, index, known, pw, challenge, themeFits, w -> !f.themeAbandoned.contains(w),
                                f::registerThemeWordBreak, v -> f.themeWordBudget = v, level, false, null);
                        if (found != null) placedFrom = "theme";
                    }
                }
                if (found != null) {
                    placedTarget = (Integer) found[0];
                    placedWord = (String) found[1];
                    cells = (int[]) found[2];
                    patternToCommit = (char[][]) found[3];
                    break;
                }
                String word = generalDictionaryPick(f, focusViable, focus, exclude, baselines, level);
                if (word != null) {
                    placedTarget = focus;
                    placedWord = word;
                    cells = slots.get(focus);
                    patternToCommit = pattern;
                    placedFrom = "dictionary";
                    break;
                }
            }
            if (placedFrom != null) windowCellsRef[0] = originClosestCells(f, List.of(focus));
        }
        for (int level : new int[]{PLACEMENT_LEVEL_STRICT, PLACEMENT_LEVEL_DISJOINT, PLACEMENT_LEVEL_BREAKING}) {
            if (placedFrom != null) break;
            Set<String> challengePool = new LinkedHashSet<>(f.activeChallengeWords());
            challengePool.removeAll(f.usedWords);
            if (!challengePool.isEmpty()) {
                Object[] found = findPriorityWordPlacement(challengePool, viable, f, slots, tierTarget.apply("challenge"), pattern, rows, cols, rng,
                        index, known, pw, challenge, f::challengeWordFits, w -> !f.challengeAbandoned.contains(w),
                        f::registerChallengeWordBreak, v -> f.challengeWordBudget = v, level, zoneSlots == null, null);
                if (found != null) {
                    placedTarget = (Integer) found[0];
                    placedWord = (String) found[1];
                    cells = (int[]) found[2];
                    patternToCommit = (char[][]) found[3];
                    placedFrom = "challenge";
                }
            }
            if (placedFrom == null && !pw.isEmpty()) {
                Set<String> themePool = new LinkedHashSet<>(pw.flatten());
                themePool.removeAll(f.usedWords);
                if (!themePool.isEmpty()) {
                    BiPredicate<Integer, String> themeFits = (i, w) -> viable.get(i).contains(w)
                            && f.priorityWords.forCells(slots.get(i)).contains(w);
                    Object[] found = findPriorityWordPlacement(themePool, viable, f, slots, tierTarget.apply("theme"), pattern, rows, cols, rng,
                            index, known, pw, challenge, themeFits, w -> !f.themeAbandoned.contains(w),
                            f::registerThemeWordBreak, v -> f.themeWordBudget = v, level, themeReshape,
                            (cf, j, w) -> cf.priorityWords.forCells(cf.slots.get(j)).contains(w));
                    if (found != null) {
                        placedTarget = (Integer) found[0];
                        placedWord = (String) found[1];
                        cells = (int[]) found[2];
                        patternToCommit = (char[][]) found[3];
                        placedFrom = "theme";
                    }
                }
            }
            if (placedFrom == null) {
                String word = null;
                int dictionaryTarget = tierTarget.apply("dictionary");
                for (List<Integer> group : List.of(selectable, blockedViable)) {
                    @SuppressWarnings("unchecked")
                    List<Integer> firstWindow = (List<Integer>) tierTargets.get("dictionary")[2];
                    for (Map.Entry<Integer, List<Integer>> e : cascadeSlotOrder(f, group, domains,
                            group == selectable ? dictionaryTarget : null, firstWindow, false, false)) {
                        int i = e.getKey();
                        word = generalDictionaryPick(f, viable, i, exclude, baselines, level);
                        if (word == null) {
                            setAside.add(i);
                            continue;
                        }
                        placedTarget = i;
                        // Blue cells: the window of the selection that yielded
                        // the slot actually placed on (mirrors Python).
                        windowCellsRef[0] = originClosestCells(f, e.getValue());
                        break;
                    }
                    if (word != null) break;
                }
                if (word != null) {
                    placedWord = word;
                    cells = slots.get(placedTarget);
                    patternToCommit = pattern;
                    placedFrom = "dictionary";
                }
            }
            if (placedFrom != null) break;
        }
        if (placedTarget != null) setAside.discard(placedTarget);
        List<Object> excluded = slotCellsOf(slots, setAside.toList());
        List<Object> windowCells = windowCellsRef[0];
        if (placedFrom == null) return impossibleResult(grid, rows, cols, index, challenge, excluded, windowCells);
        char[][] newGrid = new char[rows][cols];
        for (int r = 0; r < rows; r++) {
            for (int c = 0; c < cols; c++) {
                newGrid[r][c] = patternToCommit[r][c] == BLACK ? BLACK : grid[r][c] == BLACK ? WHITE : grid[r][c];
            }
        }
        for (int p = 0; p < cells.length; p++) newGrid[Cells.r(cells[p])][Cells.c(cells[p])] = placedWord.charAt(p);
        List<Object>[] d = fillDiagnostics(newGrid, rows, cols, index, challenge);
        Map<String, Object> placed = new LinkedHashMap<>();
        placed.put("cells", Cells.toJson(cells));
        placed.put("word", placedWord);
        placed.put("direction", Words.slotDirection(cells));
        placed.put("from_theme", placedFrom.equals("theme"));
        placed.put("from_challenge", placedFrom.equals("challenge"));
        // A general-dictionary word belonging to the Scrabble dictionary of its direction — shown in dark cyan.
        placed.put("from_scrabble", !placedFrom.equals("theme") && !placedFrom.equals("challenge")
                && f.scrabbleWords.forCells(cells).contains(placedWord));
        Map<String, Object> m = new LinkedHashMap<>();
        m.put("impossible", false);
        m.put("grid", Grids.toJson(newGrid));
        m.put("impossible_cells", d[0]);
        m.put("low_candidate_cells", d[1]);
        m.put("deadlock_cells", d[2]);
        m.put("excluded_cells", excluded);
        m.put("window_cells", windowCells);
        m.put("placed", placed);
        return m;
    }

    /** Interactive session state of the "ingrid" fill method between "Suivant" clicks (mirrors the session's
     * "ingrid" dict): the learnt crossing weights by pair of crossing slots, and the cells of the slot last filled. */
    public static final class IngridState {
        public Map<String, Double> weights = new HashMap<>();
        public int[] lastSlot;
    }

    /** Last resort of an "ingrid" "Suivant": the first word of slot {@code i}'s order still in its domain and not
     * refused by the multi-word cap, whatever it leaves; null when none (mirrors _first_fitting). */
    private static String firstFitting(IngridSolver solver, Filler f, DualIndex index, List<int[]> slots, int i) {
        boolean blockedMulti = solver.multiBlocked();
        for (String w : solver.order(i)) {
            if (solver.inDomain(i, w) && !(blockedMulti && !f.challengeWords.contains(w)
                    && Fill.isMultiWord(index, slots.get(i), w))) return w;
        }
        return null;
    }

    /** Check budget of one "Suivant" click with the "ingrid" fill method (mirrors INGRID_INTERACTIVE_BUDGET). */
    public static final long INGRID_INTERACTIVE_BUDGET = 20000;

    /** "Suivant" with the "ingrid" fill method: places exactly one more word the way IngridSolver takes one decision,
     * everything recomputed from {@code grid}, {@code state} updated in place (mirrors interactive_place_word_ingrid). */
    public static Map<String, Object> placeWordIngrid(char[][] grid, int rows, int cols, DualIndex index, Rng rng,
                                                      PW pwIn, Set<String> challengeIn, PW scrabble,
                                                      int multiWordPercent, IngridState state,
                                                      List<int[]> selectedCells, List<int[]> zoneCells) {
        PW pw = pwIn == null ? PW.EMPTY : pwIn;
        Set<String> challenge = challengeIn == null ? Set.of() : challengeIn;
        char[][] pattern = Grids.patternOf(grid);
        Map<Integer, Character> known = Grids.knownLetters(grid);
        Object[] built = buildInteractiveFiller(pattern, rows, cols, index, rng, known, pw, challenge);
        Filler f = (Filler) built[0];
        f.scrabbleWords = scrabble == null ? PW.EMPTY : scrabble;
        f.multiWordPercent = multiWordPercent;
        @SuppressWarnings("unchecked")
        List<int[]> slots = (List<int[]>) built[1];
        if (slots.isEmpty()) return new LinkedHashMap<>(Map.of("impossible", true));
        Set<Integer> zoneSlots = zoneSlotIndices(slots, zoneCells);
        if (zoneSlots != null && zoneComplete(slots, zoneSlots, known)) {
            return zoneCompleteResult(grid, rows, cols, index, challenge);
        }
        IngridSolver solver = new IngridSolver(f, INGRID_INTERACTIVE_BUDGET);
        solver.loadWeights(state.weights);
        solver.settle();
        solver.freezeInitial();
        int root = solver.trailMark();
        List<Integer> blocked = new ArrayList<>();
        for (int i = 0; i < slots.size(); i++) if (!solver.isActive(i) && f.assignment[i] == null) blocked.add(i);
        Set<Integer> forbidden = new HashSet<>();
        for (int i : blocked) for (int cell : slots.get(i)) if (!known.containsKey(cell)) forbidden.add(cell);
        List<Integer> candidates = new ArrayList<>();
        for (int i = 0; i < slots.size(); i++) {
            if (!solver.isActive(i) || f.assignment[i] != null) continue;
            if (zoneSlots != null && !zoneSlots.contains(i)) continue;
            boolean crossesBlocked = false;
            for (int cell : slots.get(i)) if (forbidden.contains(cell)) crossesBlocked = true;
            if (!crossesBlocked) candidates.add(i);
        }
        int last = -1;
        if (state.lastSlot != null) {
            for (int i = 0; i < slots.size(); i++) if (Arrays.equals(slots.get(i), state.lastSlot)) last = i;
        }
        Filler.RecentSlots setAside = new Filler.RecentSlots(Filler.MAX_EXCLUDED_SLOTS);
        Set<Integer> triedOut = new HashSet<>();
        int placedSlot = -1;
        String placedWord = null;
        // The selected emplacement, while incomplete, takes the word whenever one can go there (mirrors the focus
        // pass of interactive_place_word_ingrid).
        Integer focusIdx = selectedSlotIndex(slots, selectedCells, known);
        int focus = focusIdx != null && candidates.contains(focusIdx) ? focusIdx : -1;
        if (focus >= 0) {
            while (placedWord == null && solver.domainSize(focus) > 0 && !solver.step()) {
                Object[] picked = solver.pickWord(focus, 0);
                if (picked == null) break;
                String word = (String) picked[1];
                int mark = solver.trailMark();
                solver.keepOnly(focus, word);
                Map<Integer, Double> updates = solver.propagate(List.of(focus), List.of(focus));
                if (updates == null) {
                    placedSlot = focus;
                    placedWord = word;
                    break;
                }
                solver.learn(updates);
                solver.undoTo(mark);
                mark = solver.trailMark();
                solver.refute(focus, word);
                if (solver.domainSize(focus) == 0) updates = Map.of();
                else updates = solver.propagate(List.of(focus),
                        solver.domainSize(focus) == 1 ? List.of(focus) : List.of());
                if (updates != null) {
                    solver.learn(updates);
                    solver.undoTo(mark);
                    break;
                }
            }
            if (placedWord == null) {
                solver.undoTo(root);
                String word = firstFitting(solver, f, index, slots, focus);
                if (word != null) {
                    placedSlot = focus;
                    placedWord = word;
                }
            }
        }
        while (placedWord == null && !solver.step()) {
            List<Integer> pool = new ArrayList<>();
            for (int i : candidates) if (!triedOut.contains(i) && solver.domainSize(i) > 1) pool.add(i);
            if (pool.isEmpty()) for (int i : candidates) if (!triedOut.contains(i) && solver.domainSize(i) == 1) pool.add(i);
            if (pool.isEmpty()) break;
            int i;
            Object[] picked;
            if (known.isEmpty()) {
                // Empty grid: its first word is chosen the "falcon" way.
                Object[] fp = solver.falconPick(pool);
                i = (int) fp[0];
                picked = fp[1] != null ? new Object[]{0, fp[1]} : solver.pickWord(i, 0);
            } else {
                i = solver.chooseSlot(pool.contains(last) ? last : -1, pool);
                picked = solver.pickWord(i, 0);
            }
            if (picked == null) {
                triedOut.add(i);
                setAside.add(i);
                last = -1;
                continue;
            }
            String word = (String) picked[1];
            int mark = solver.trailMark();
            solver.keepOnly(i, word);
            Map<Integer, Double> updates = solver.propagate(List.of(i), List.of(i));
            if (updates == null) {
                placedSlot = i;
                placedWord = word;
                break;
            }
            solver.learn(updates);
            solver.undoTo(mark);
            mark = solver.trailMark();
            solver.refute(i, word);
            if (solver.domainSize(i) == 0) updates = Map.of();
            else updates = solver.propagate(List.of(i), solver.domainSize(i) == 1 ? List.of(i) : List.of());
            if (updates != null) {
                solver.learn(updates);
                solver.undoTo(mark);
                triedOut.add(i);
                setAside.add(i);
                last = -1;
                continue;
            }
            last = i;
        }
        if (placedWord == null) {
            // Last resort: the slot of best priority, its first word still fitting the grid, whatever it leaves.
            solver.undoTo(root);
            List<Integer> pool = new ArrayList<>();
            for (int i : candidates) if (solver.domainSize(i) > 1) pool.add(i);
            if (pool.isEmpty()) pool = candidates;
            int i = pool.isEmpty() ? -1 : solver.chooseSlot(-1, pool);
            if (i >= 0) {
                String w = firstFitting(solver, f, index, slots, i);
                if (w != null) {
                    placedSlot = i;
                    placedWord = w;
                }
            }
        }
        state.weights = solver.exportWeights();
        List<Object> windowCells = new ArrayList<>();
        List<Integer> windowSlots = focus >= 0 && placedWord != null && placedSlot == focus
                ? List.of(focus) : solver.lastWindow;
        for (int j : windowSlots) windowCells.add(List.of(Cells.r(slots.get(j)[0]), Cells.c(slots.get(j)[0])));
        if (placedWord == null) {
            List<Object>[] d = fillDiagnostics(grid, rows, cols, index, challenge);
            TreeSet<Integer> imp = new TreeSet<>();
            for (Object o : d[0]) {
                List<?> rc = (List<?>) o;
                imp.add(Cells.of(((Number) rc.get(0)).intValue(), ((Number) rc.get(1)).intValue()));
            }
            for (int j : blocked) for (int cell : slots.get(j)) imp.add(cell);
            List<Object> impCells = new ArrayList<>();
            for (int cell : imp) impCells.add(List.of(Cells.r(cell), Cells.c(cell)));
            Map<String, Object> m = new LinkedHashMap<>();
            m.put("impossible", true);
            m.put("impossible_cells", impCells);
            m.put("low_candidate_cells", d[1]);
            m.put("deadlock_cells", d[2]);
            m.put("excluded_cells", slotCellsOf(slots, setAside.toList()));
            m.put("window_cells", windowCells);
            return m;
        }
        int[] cells = slots.get(placedSlot);
        state.lastSlot = cells.clone();
        setAside.discard(placedSlot);
        char[][] newGrid = new char[rows][];
        for (int r = 0; r < rows; r++) newGrid[r] = grid[r].clone();
        for (int p = 0; p < cells.length; p++) newGrid[Cells.r(cells[p])][Cells.c(cells[p])] = placedWord.charAt(p);
        List<Object>[] d = fillDiagnostics(newGrid, rows, cols, index, challenge);
        int family = solver.familyOf(placedSlot).applyAsInt(placedWord);
        Map<String, Object> placed = new LinkedHashMap<>();
        placed.put("cells", Cells.toJson(cells));
        placed.put("word", placedWord);
        placed.put("direction", Words.slotDirection(cells));
        placed.put("from_theme", family == 1);
        placed.put("from_challenge", family == 0);
        placed.put("from_scrabble", family == 2);
        Map<String, Object> m = new LinkedHashMap<>();
        m.put("impossible", false);
        m.put("grid", Grids.toJson(newGrid));
        m.put("impossible_cells", d[0]);
        m.put("low_candidate_cells", d[1]);
        m.put("deadlock_cells", d[2]);
        m.put("excluded_cells", slotCellsOf(slots, setAside.toList()));
        m.put("window_cells", windowCells);
        m.put("placed", placed);
        return m;
    }

    // ================================================================== candidate lists

    static Map<Integer, int[]> crossingCellsMap(List<int[]> slots, int[] own) {
        Set<Integer> ownSet = Grids.cellSet(own);
        Map<Integer, int[]> result = new LinkedHashMap<>();
        for (int cell : own) {
            int[] cross = null;
            for (int[] sc : slots) {
                boolean contains = false;
                for (int c : sc) if (c == cell) contains = true;
                if (contains && !Grids.cellSet(sc).equals(ownSet)) {
                    cross = sc;
                    break;
                }
            }
            result.put(cell, cross);
        }
        return result;
    }

    static boolean challengeWordFitsCells(int[] cells, Map<Integer, Character> known, Set<String> challenge, Set<String> used) {
        if (challenge == null || challenge.isEmpty()) return false;
        for (String w : challenge) {
            if (used.contains(w) || w.length() != cells.length) continue;
            boolean ok = true;
            for (int p = 0; p < cells.length; p++) {
                Character k = known.get(cells[p]);
                if (k != null && k != w.charAt(p)) {
                    ok = false;
                    break;
                }
            }
            if (ok) return true;
        }
        return false;
    }

    static Set<Integer> unsafeLetterPositions(int[] cells, String word, Map<Integer, int[]> crossingMap, DualIndex index,
                                              Map<Integer, Character> known, Set<String> used, Set<String> challenge) {
        Set<Integer> unsafe = new TreeSet<>();
        for (int p = 0; p < cells.length; p++) {
            int[] cross = crossingMap.get(cells[p]);
            if (cross == null) continue;
            Map<Integer, Character> before = Cleanup.subKnown(cross, known);
            boolean beforeOk = !Words.slotCandidates(index, cross.length, cross, before).minus(used).isEmpty()
                    || challengeWordFitsCells(cross, before, challenge, used);
            if (!beforeOk) continue;
            Map<Integer, Character> after = new HashMap<>(before);
            after.put(cells[p], word.charAt(p));
            boolean afterOk = !Words.slotCandidates(index, cross.length, cross, after).minus(used).isEmpty()
                    || challengeWordFitsCells(cross, after, challenge, used);
            if (!afterOk) unsafe.add(p);
        }
        return unsafe;
    }

    static List<Object> wordsWithUnsafe(List<String> words, int[] cells, Map<Integer, int[]> crossingMap, DualIndex index,
                                        Map<Integer, Character> known, Set<String> used, Set<String> challenge) {
        List<Object> out = new ArrayList<>();
        Map<Integer, LenIndex> lengthIndex = cells.length >= 2 ? index.forCells(cells) : Map.of();
        for (String w : words) {
            Map<String, Object> entry = new LinkedHashMap<>();
            entry.put("word", w);
            entry.put("accented", Words.wordForms(lengthIndex.get(w.length()), w)[0]);
            entry.put("unsafe", new ArrayList<>(unsafeLetterPositions(cells, w, crossingMap, index, known, used, challenge)));
            out.add(entry);
        }
        return out;
    }

    static Set<String> usedExcept(List<int[]> slots, Map<Integer, Character> known, Set<Set<Integer>> exceptSets,
                                  DualIndex index) {
        Set<String> used = new UsedWords(index);
        for (int[] other : slots) {
            if (exceptSets.contains(Grids.cellSet(other))) continue;
            String w = Grids.wordAt(other, known);
            if (w != null) used.add(w);
        }
        return used;
    }

    static List<String> sorted(Collection<String> c) {
        List<String> l = new ArrayList<>(c);
        java.util.Collections.sort(l);
        return l;
    }

    static List<String> head(List<String> l, int n) {
        return l.size() > n ? new ArrayList<>(l.subList(0, n)) : l;
    }

    /** Returns {theme words, other words}. */
    public static List<Object>[] slotCandidates(char[][] grid, int rows, int cols, DualIndex index, int[] cells, PW pw,
                                                Set<String> challenge) {
        char[][] pattern = Grids.patternOf(grid);
        List<int[]> slots = Grids.extractSlots(pattern, rows, cols);
        Map<Integer, Character> known = Grids.knownLetters(grid);
        Set<String> used = usedExcept(slots, known, Set.of(Grids.cellSet(cells)), index);
        Set<String> candidates = new LinkedHashSet<>(Words.slotCandidates(index, cells.length, cells, known).minus(used));
        Set<String> themed = new HashSet<>();
        if (pw != null && !pw.isEmpty()) for (String w : pw.forCells(cells)) if (candidates.contains(w)) themed.add(w);
        Set<String> other = new HashSet<>(candidates);
        other.removeAll(themed);
        Map<Integer, int[]> cm = crossingCellsMap(slots, cells);
        @SuppressWarnings("unchecked")
        List<Object>[] out = new List[]{wordsWithUnsafe(sorted(themed), cells, cm, index, known, used, challenge),
                wordsWithUnsafe(head(sorted(other), INTERACTIVE_SLOT_CANDIDATES_LIMIT), cells, cm, index, known, used, challenge)};
        return out;
    }

    static int[] whiteRunAt(char[][] grid, int rows, int cols, int cell, boolean across) {
        int r = Cells.r(cell), c = Cells.c(cell);
        if (grid[r][c] == BLACK) return new int[0];
        List<Integer> cells = new ArrayList<>();
        if (across) {
            int start = c;
            while (start > 0 && grid[r][start - 1] != BLACK) start--;
            int end = c;
            while (end < cols - 1 && grid[r][end + 1] != BLACK) end++;
            for (int cc = start; cc <= end; cc++) cells.add(Cells.of(r, cc));
        } else {
            int start = r;
            while (start > 0 && grid[start - 1][c] != BLACK) start--;
            int end = r;
            while (end < rows - 1 && grid[end + 1][c] != BLACK) end++;
            for (int rr = start; rr <= end; rr++) cells.add(Cells.of(rr, c));
        }
        return cells.size() >= 2 ? cells.stream().mapToInt(Integer::intValue).toArray() : new int[0];
    }

    /** Returns {across start [r,c] or null, down start or null, letters list}. */
    public static Object[] crossingWords(char[][] grid, int rows, int cols, DualIndex index, int cell, Set<String> challenge) {
        int[] across = whiteRunAt(grid, rows, cols, cell, true);
        int[] down = whiteRunAt(grid, rows, cols, cell, false);
        Object acrossStart = across.length > 0 ? List.of(Cells.r(across[0]), Cells.c(across[0])) : null;
        Object downStart = down.length > 0 ? List.of(Cells.r(down[0]), Cells.c(down[0])) : null;
        if (across.length == 0 || down.length == 0) return new Object[]{acrossStart, downStart, new ArrayList<>()};
        char[][] pattern = Grids.patternOf(grid);
        List<int[]> slots = Grids.extractSlots(pattern, rows, cols);
        Map<Integer, Character> known = Grids.knownLetters(grid);
        Set<String> used = usedExcept(slots, known, Set.of(Grids.cellSet(across), Grids.cellSet(down)), index);
        List<String> aw = Words.slotCandidates(index, across.length, across, known).minus(used);
        List<String> dw = Words.slotCandidates(index, down.length, down, known).minus(used);
        int pa = 0, pd = 0;
        for (int k = 0; k < across.length; k++) if (across[k] == cell) pa = k;
        for (int k = 0; k < down.length; k++) if (down[k] == cell) pd = k;
        Map<Character, List<String>> byA = new TreeMap<>(), byD = new TreeMap<>();
        for (String w : aw) byA.computeIfAbsent(w.charAt(pa), k -> new ArrayList<>()).add(w);
        for (String w : dw) byD.computeIfAbsent(w.charAt(pd), k -> new ArrayList<>()).add(w);
        Map<Integer, int[]> acm = crossingCellsMap(slots, across), dcm = crossingCellsMap(slots, down);
        List<Object> letters = new ArrayList<>();
        for (Character letter : byA.keySet()) {
            if (!byD.containsKey(letter)) continue;
            Map<String, Object> m = new LinkedHashMap<>();
            m.put("letter", String.valueOf(letter));
            m.put("across_words", wordsWithUnsafe(head(sorted(byA.get(letter)), INTERACTIVE_SLOT_CANDIDATES_LIMIT), across, acm,
                    index, known, used, challenge));
            m.put("down_words", wordsWithUnsafe(head(sorted(byD.get(letter)), INTERACTIVE_SLOT_CANDIDATES_LIMIT), down, dcm,
                    index, known, used, challenge));
            letters.add(m);
        }
        return new Object[]{acrossStart, downStart, letters};
    }

    /** Returns {theme words, other words}. */
    @SuppressWarnings("unchecked")
    public static List<Object>[] boundaryCandidates(char[][] grid, int rows, int cols, DualIndex index, int[] cells,
                                                    String side, PW pw, Set<String> challenge) {
        char[][] pattern = Grids.patternOf(grid);
        List<int[]> slots = Grids.extractSlots(pattern, rows, cols);
        Map<Integer, Character> known = Grids.knownLetters(grid);
        Set<String> used = usedExcept(slots, known, Set.of(Grids.cellSet(cells)), index);
        int full = cells.length;
        List<Object> theme = new ArrayList<>(), other = new ArrayList<>();
        for (int length = 2; length <= full; length++) {
            int[] sub = side.equals("start") ? Arrays.copyOfRange(cells, 0, length) : Arrays.copyOfRange(cells, full - length, full);
            if (length < full) {
                int boundary = side.equals("start") ? cells[length] : cells[full - length - 1];
                if (known.containsKey(boundary)) continue;
                int br = Cells.r(boundary), bc = Cells.c(boundary);
                pattern[br][bc] = BLACK;
                boolean valid = Grids.isStructurallyValid(pattern, rows, cols, 1);
                pattern[br][bc] = WHITE;
                if (!valid) continue;
            }
            Set<String> candidates = new LinkedHashSet<>(Words.slotCandidates(index, length, sub, Cleanup.subKnown(sub, known)).minus(used));
            Set<String> themed = new HashSet<>();
            if (pw != null && !pw.isEmpty()) for (String w : pw.forCells(sub)) if (candidates.contains(w)) themed.add(w);
            Set<String> rest = new HashSet<>(candidates);
            rest.removeAll(themed);
            Map<Integer, int[]> cm = crossingCellsMap(slots, sub);
            theme.addAll(wordsWithUnsafe(sorted(themed), sub, cm, index, known, used, challenge));
            other.addAll(wordsWithUnsafe(head(sorted(rest), INTERACTIVE_SLOT_CANDIDATES_LIMIT), sub, cm, index, known, used, challenge));
        }
        return new List[]{theme, other};
    }

    // ================================================================== clean / minimize

    public static Map<String, Object> cleanImpossibleZones(char[][] grid, int rows, int cols, DualIndex index, Rng rng,
                                                           Set<String> challengeIn) {
        Set<String> challenge = challengeIn == null ? Set.of() : challengeIn;
        char[][] pattern = Grids.patternOf(grid);
        List<int[]> slots = Grids.extractSlots(pattern, rows, cols);
        Map<String, Object> unchanged = new LinkedHashMap<>(Map.of("changed", false, "grid", Grids.toJson(grid), "cleared_count", 0));
        if (slots.isEmpty()) return unchanged;
        Map<Integer, Character> known = Grids.knownLetters(grid);
        String[] assignment = new String[slots.size()];
        for (int i = 0; i < slots.size(); i++) assignment[i] = Grids.wordAt(slots.get(i), known);
        Set<Integer> fillable = Cleanup.challengeFillableSlotIndices(slots, known, challenge);
        Set<Integer> exempt = Cleanup.challengeWordCells(slots, known, challenge);
        Set<Integer> deadlocked = Cleanup.crossingDeadlockIndices(slots, index, known, challenge);
        TreeSet<Integer> impossible = new TreeSet<>(Cleanup.impossibleIndices(slots, index, known, challenge));
        impossible.removeAll(fillable);
        impossible.addAll(Cleanup.invalidFullyKnownIndices(slots, index, known, exempt));
        if (impossible.isEmpty()) return unchanged;
        Object[] cleaned = Cleanup.cleanBlockedSlots(slots, assignment, new ArrayList<>(impossible), null, false, index, rng,
                grid, rows, cols, null, null, deadlocked, false);
        String[] ca = ((String[]) cleaned[0]).clone();
        @SuppressWarnings("unchecked")
        Set<Integer> newBlack = (Set<Integer>) cleaned[2];
        for (int i : impossible) ca[i] = null;
        Map<Integer, Character> confirmed = new HashMap<>();
        for (int i = 0; i < ca.length; i++) {
            if (ca[i] == null) continue;
            int[] cells = slots.get(i);
            for (int p = 0; p < cells.length; p++) confirmed.put(cells[p], ca[i].charAt(p));
        }
        // No lock exists here: the cleaned grid keeps the letters of its remaining whole words only, a letter no
        // whole word carries any more (an orphan letter) being erased, as in every other cleanup.
        int removed = 0;
        for (int i = 0; i < ca.length; i++) if (assignment[i] != null && ca[i] == null) removed++;
        int cleared = removed + newBlack.size();
        if (cleared == 0) return unchanged;
        char[][] ng = Grids.copy(grid);
        for (int c : newBlack) ng[Cells.r(c)][Cells.c(c)] = BLACK;
        for (int cell : known.keySet()) {
            int r = Cells.r(cell), c = Cells.c(cell);
            if (!confirmed.containsKey(cell) && ng[r][c] != BLACK) ng[r][c] = WHITE;
        }
        return new LinkedHashMap<>(Map.of("changed", true, "grid", Grids.toJson(ng), "cleared_count", cleared));
    }

    public static Map<String, Object> minimizeBlackCells(char[][] grid, int rows, int cols, DualIndex index, Rng rng,
                                                         Set<String> challengeIn) {
        Set<String> challenge = challengeIn == null ? Set.of() : challengeIn;
        List<Integer> blacks = new ArrayList<>();
        for (int r = 0; r < rows; r++) for (int c = 0; c < cols; c++) if (grid[r][c] == BLACK) blacks.add(Cells.of(r, c));
        Map<String, Object> unchanged = new LinkedHashMap<>(Map.of("changed", false, "grid", Grids.toJson(grid), "removed_count", 0));
        if (blacks.isEmpty()) return unchanged;
        rng.shuffle(blacks);
        char[][] working = Grids.copy(grid);
        int removed = 0;
        for (int cell : blacks) {
            int r = Cells.r(cell), c = Cells.c(cell);
            if (touchesPlacedLetter(working, rows, cols, r, c)) continue;
            working[r][c] = WHITE;
            if (!Grids.isStructurallyValid(Grids.patternOf(working), rows, cols, 1)) {
                working[r][c] = BLACK;
                continue;
            }
            char[][] pat = Grids.patternOf(working);
            List<int[]> slots = Grids.extractSlots(pat, rows, cols);
            boolean bad = false;
            if (!slots.isEmpty()) {
                Map<Integer, Character> known = Grids.knownLetters(working);
                Set<Integer> fillable = Cleanup.challengeFillableSlotIndices(slots, known, challenge);
                Set<Integer> exempt = Cleanup.challengeWordCells(slots, known, challenge);
                Set<Integer> badIdx = new HashSet<>(Cleanup.impossibleIndices(slots, index, known, challenge));
                badIdx.removeAll(fillable);
                badIdx.addAll(Cleanup.invalidFullyKnownIndices(slots, index, known, exempt));
                for (int j : badIdx) for (int x : slots.get(j)) if (x == cell) bad = true;
            }
            if (bad) {
                working[r][c] = BLACK;
                continue;
            }
            removed++;
        }
        if (removed == 0) return unchanged;
        return new LinkedHashMap<>(Map.of("changed", true, "grid", Grids.toJson(working), "removed_count", removed));
    }

    static boolean touchesPlacedLetter(char[][] g, int rows, int cols, int r, int c) {
        int[][] d = {{-1, 0}, {1, 0}, {0, -1}, {0, 1}};
        for (int[] x : d) {
            int nr = r + x[0], nc = c + x[1];
            if (nr >= 0 && nr < rows && nc >= 0 && nc < cols && g[nr][nc] != BLACK && g[nr][nc] != WHITE) return true;
        }
        return false;
    }
}
