package falcon.gen;

import falcon.GenerationCancelled;
import falcon.gen.Words.DualIndex;
import falcon.gen.Words.PW;

import java.util.ArrayList;
import java.util.Collection;
import java.util.HashMap;
import java.util.HashSet;
import java.util.LinkedHashMap;
import java.util.LinkedHashSet;
import java.util.List;
import java.util.Map;
import java.util.Set;
import java.util.TreeMap;
import java.util.TreeSet;
import java.util.concurrent.atomic.AtomicBoolean;
import java.util.concurrent.atomic.AtomicIntegerArray;
import java.util.concurrent.atomic.AtomicLongArray;
import java.util.function.Consumer;

import static falcon.gen.Grids.BLACK;
import static falcon.gen.Grids.WHITE;

/** Statistical seeding, one fill attempt (try_fill), minimization and the
 * cell-overlay helpers (mirrors the matching parts of crossword_gen.py). */
public final class Fill {
    private Fill() {}

    public static final int LETTER_BIAS_SAMPLE_SIZE = 10;
    public static final double LETTER_BIAS_FORCE_FRACTION = 0.05;
    public static final int LETTER_BIAS_MIN_COUNT = 1;
    public static final int THEME_WORD_SCORE_BONUS = 2;
    public static final int CHALLENGE_WORD_SCORE_BONUS = 4;
    public static final int CONTENT_SCORE_LENGTH_CAP = 7;

    /** A solved fill: slots + one word per slot. */
    public record Result(List<int[]> slots, String[] assignment) {}

    // ================================================================== seeding

    public static Map<Integer, Character> forceSingleCandidateSlots(List<int[]> slots, DualIndex index,
                                                                    Map<Integer, Character> knownLetters,
                                                                    Set<Integer> excluded) {
        Map<Integer, Character> known = new LinkedHashMap<>(knownLetters == null ? Map.of() : knownLetters);
        boolean changed = true;
        while (changed) {
            changed = false;
            for (int s = 0; s < slots.size(); s++) {
                if (excluded != null && excluded.contains(s)) continue;
                int[] cells = slots.get(s);
                if (Grids.allKnown(cells, known)) continue;
                Dom cands = Words.slotCandidates(index, cells.length, cells, known);
                if (cands.size() != 1) continue;
                String word = cands.first();
                for (int p = 0; p < cells.length; p++) {
                    if (!known.containsKey(cells[p])) {
                        known.put(cells[p], word.charAt(p));
                        changed = true;
                    }
                }
            }
        }
        return known;
    }

    public static void closeImpliedSlots(List<int[]> slots, DualIndex index, String[] assignment, Set<String> usedWords,
                                         Set<Integer> excluded) {
        Set<Integer> ex = excluded == null ? Set.of() : excluded;
        Map<Integer, Character> known = new HashMap<>();
        for (int i = 0; i < slots.size(); i++) {
            String w = assignment[i];
            if (w == null) continue;
            int[] cells = slots.get(i);
            for (int p = 0; p < cells.length; p++) known.put(cells[p], w.charAt(p));
        }
        Map<Integer, List<Integer>> cellToSlots = Grids.cellToSlotIndices(slots);
        boolean changed = true;
        while (changed) {
            changed = false;
            for (int i = 0; i < slots.size(); i++) {
                if (ex.contains(i) || assignment[i] != null) continue;
                int[] cells = slots.get(i);
                Dom cands = Words.slotCandidates(index, cells.length, cells, known);
                if (cands.size() - usedWords.size() > 1) continue;
                List<String> real = cands.minus(usedWords);
                if (real.size() != 1) continue;
                String word = real.get(0);
                Map<Integer, Character> trial = new HashMap<>(known);
                for (int p = 0; p < cells.length; p++) trial.put(cells[p], word.charAt(p));
                boolean breaks = false;
                outer:
                for (int cell : cells) {
                    for (int j : cellToSlots.getOrDefault(cell, List.of())) {
                        if (j == i || ex.contains(j) || assignment[j] != null) continue;
                        int[] jc = slots.get(j);
                        boolean allGone = true;
                        for (String w2 : Words.slotCandidates(index, jc.length, jc, trial)) {
                            if (!w2.equals(word) && !usedWords.contains(w2)) {
                                allGone = false;
                                break;
                            }
                        }
                        if (allGone) {
                            breaks = true;
                            break outer;
                        }
                    }
                }
                if (breaks) continue;
                assignment[i] = word;
                usedWords.add(word);
                for (int p = 0; p < cells.length; p++) known.put(cells[p], word.charAt(p));
                changed = true;
            }
        }
    }

    /** Returns {forced letters, letter_scores (cell -> int[2][] by direction)}. */
    public static Object[] sampleLetterBiases(char[][] grid, int rows, int cols, DualIndex index, Rng rng,
                                              double forceFraction, Set<Integer> excludedSlots,
                                              Map<Integer, Character> knownLetters) {
        List<int[]> slots = Grids.extractSlots(grid, rows, cols);
        Map<Integer, List<Integer>> cellToSlots = Grids.cellToSlotIndices(slots);
        Set<Integer> excluded = excludedSlots == null ? Set.of() : excludedSlots;
        Map<Integer, Character> known = knownLetters == null ? Map.of() : knownLetters;
        List<Object[]> eligible = new ArrayList<>();
        Map<Integer, int[][]> letterScores = new LinkedHashMap<>();
        for (int s = 0; s < slots.size(); s++) {
            int[] cells = slots.get(s);
            int length = cells.length;
            int dir = Words.isAcross(cells) ? 0 : 1;
            LenIndex idx = index.forCells(cells).get(length);
            if (idx == null || idx.size() == 0) continue;
            Dom pool = Words.slotCandidates(index, length, cells, known);
            if (pool.isEmpty()) continue;
            List<String> sample = pool.choices(rng, LETTER_BIAS_SAMPLE_SIZE);
            for (int p = 0; p < cells.length; p++) {
                int[] counts = Tally.ofLetters(sample, p);
                letterScores.computeIfAbsent(cells[p], k -> new int[2][])[dir] = counts;
                int best = -1, bestCount = 0;
                for (int id = 0; id < counts.length; id++) {
                    if (counts[id] > bestCount) {
                        bestCount = counts[id];
                        best = id;
                    }
                }
                if (!known.containsKey(cells[p]) && bestCount > LETTER_BIAS_MIN_COUNT && !excluded.contains(s)) {
                    eligible.add(new Object[]{bestCount, cells[p], Alpha.letter(best)});
                }
            }
        }
        rng.shuffle(eligible);
        int remainingWhite = 0;
        for (int r = 0; r < rows; r++) {
            for (int c = 0; c < cols; c++) if (grid[r][c] == WHITE && !known.containsKey(Cells.of(r, c))) remainingWhite++;
        }
        int target = (int) Math.rint(remainingWhite * forceFraction);
        Map<Integer, Character> forced = new LinkedHashMap<>();
        Set<Integer> usedSlots = new HashSet<>();
        for (Object[] e : eligible) {
            if (forced.size() >= target) break;
            int cell = (int) e[1];
            if (forced.containsKey(cell)) continue;
            List<Integer> touching = cellToSlots.get(cell);
            boolean clash = false;
            for (int s : touching) if (usedSlots.contains(s)) clash = true;
            if (clash) continue;
            forced.put(cell, (char) e[2]);
            usedSlots.addAll(touching);
        }
        return new Object[]{forced, letterScores};
    }

    // ================================================================== overlays

    public static List<Integer> themeWordCells(List<int[]> slots, String[] assignment, PW priorityWords) {
        if (priorityWords == null || priorityWords.isEmpty()) return new ArrayList<>();
        TreeSet<Integer> out = new TreeSet<>();
        for (int i = 0; i < slots.size(); i++) {
            String w = assignment[i];
            if (w != null && priorityWords.forCells(slots.get(i)).contains(w)) for (int c : slots.get(i)) out.add(c);
        }
        return new ArrayList<>(out);
    }

    /** Mirrors _placed_theme_words: distinct theme words already placed, each against its own direction's glossary. */
    public static Set<String> placedThemeWords(List<int[]> slots, String[] assignment, PW priorityWords) {
        Set<String> out = new LinkedHashSet<>();
        if (priorityWords == null || priorityWords.isEmpty()) return out;
        for (int i = 0; i < slots.size(); i++) {
            String w = assignment[i];
            if (w != null && priorityWords.forCells(slots.get(i)).contains(w)) out.add(w);
        }
        return out;
    }

    public static List<Integer> challengeWordCellsFromAssignment(List<int[]> slots, String[] assignment,
                                                                 Set<String> challengeWords) {
        if (challengeWords == null || challengeWords.isEmpty()) return new ArrayList<>();
        TreeSet<Integer> out = new TreeSet<>();
        for (int i = 0; i < slots.size(); i++) {
            String w = assignment[i];
            if (w != null && challengeWords.contains(w)) for (int c : slots.get(i)) out.add(c);
        }
        return new ArrayList<>(out);
    }

    static String[] assignmentFromLocked(List<int[]> slots, Map<Integer, Character> locked) {
        String[] a = new String[slots.size()];
        for (int i = 0; i < slots.size(); i++) a[i] = Grids.wordAt(slots.get(i), locked);
        return a;
    }

    public static List<Integer> themeCellsFromPreviewState(char[][] seedGrid, int rows, int cols,
                                                           Map<Integer, Character> locked, String[] preseed, PW pw) {
        if (pw == null || pw.isEmpty() || seedGrid == null) return new ArrayList<>();
        List<int[]> slots = Grids.extractSlots(seedGrid, rows, cols);
        if (preseed != null) return themeWordCells(slots, preseed, pw);
        if (locked != null && !locked.isEmpty()) return themeWordCells(slots, assignmentFromLocked(slots, locked), pw);
        return new ArrayList<>();
    }

    public static List<Integer> challengeCellsFromPreviewState(char[][] seedGrid, int rows, int cols,
                                                               Map<Integer, Character> locked, String[] preseed,
                                                               Set<String> cw) {
        if (cw == null || cw.isEmpty() || seedGrid == null) return new ArrayList<>();
        List<int[]> slots = Grids.extractSlots(seedGrid, rows, cols);
        if (preseed != null) return challengeWordCellsFromAssignment(slots, preseed, cw);
        if (locked != null && !locked.isEmpty()) {
            return challengeWordCellsFromAssignment(slots, assignmentFromLocked(slots, locked), cw);
        }
        return new ArrayList<>();
    }

    public static List<Integer> lowCandidateSlotCells(char[][] grid, int rows, int cols, DualIndex index,
                                                      Map<Integer, Character> locked) {
        if (locked == null || locked.isEmpty()) return new ArrayList<>();
        TreeSet<Integer> cells = new TreeSet<>();
        for (int[] slot : Grids.extractSlots(grid, rows, cols)) {
            int lc = 0;
            for (int c : slot) if (locked.containsKey(c)) lc++;
            if (lc > 0 && lc < slot.length
                    && Words.slotCandidateCount(index, slot.length, slot, locked) < Grids.PREFILL_LOCKED_MIN_WORD_COUNT) {
                for (int c : slot) cells.add(c);
            }
        }
        return new ArrayList<>(cells);
    }

    public static List<Integer> noiseSlotCells(char[][] grid, int rows, int cols, DualIndex index,
                                               Map<Integer, Character> locked) {
        if (locked == null || locked.isEmpty()) return new ArrayList<>();
        List<int[]> all = Grids.extractSlots(grid, rows, cols);
        Set<String> used = new HashSet<>();
        for (int[] slot : all) {
            String w = Grids.wordAt(slot, locked);
            if (w != null) used.add(w);
        }
        List<List<String>> playable = new ArrayList<>();
        for (int[] slot : all) {
            int lc = 0;
            for (int c : slot) if (locked.containsKey(c)) lc++;
            if (!(lc > 0 && lc < slot.length)) {
                playable.add(null);
                continue;
            }
            List<String> list = new ArrayList<>();
            for (String w : Words.slotCandidates(index, slot.length, slot, locked)) {
                LenIndex li = index.forCells(slot).get(slot.length);
                if (!used.contains(w) && li != null && li.freqOf(w) >= Grids.NOISE_FREQUENCY_THRESHOLD) list.add(w);
            }
            playable.add(list);
        }
        Map<Integer, List<int[]>> cellSlots = new LinkedHashMap<>();
        for (int i = 0; i < all.size(); i++) {
            if (playable.get(i) == null) continue;
            int[] slot = all.get(i);
            for (int p = 0; p < slot.length; p++) {
                if (!locked.containsKey(slot[p])) cellSlots.computeIfAbsent(slot[p], k -> new ArrayList<>()).add(new int[]{i, p});
            }
        }
        TreeSet<Integer> cells = new TreeSet<>();
        cellSlots.forEach((cell, entries) -> {
            List<Set<Character>> letterSets = new ArrayList<>();
            for (int[] e : entries) {
                List<String> pl = playable.get(e[0]);
                if (pl.isEmpty()) {
                    letterSets.clear();
                    break;
                }
                Set<Character> s = new HashSet<>();
                for (String w : pl) s.add(w.charAt(e[1]));
                letterSets.add(s);
            }
            if (letterSets.isEmpty()) {
                cells.add(cell);
                return;
            }
            Set<Character> common = new HashSet<>(letterSets.get(0));
            for (int k = 1; k < letterSets.size(); k++) common.retainAll(letterSets.get(k));
            if (common.isEmpty()) cells.add(cell);
        });
        return new ArrayList<>(cells);
    }

    /** Returns {grid, lockedCells sorted}. */
    public static Object[] cycleStartPreview(int rows, int cols, char[][] seedGrid, Map<Integer, Character> locked,
                                             String[] preseed) {
        if (seedGrid == null) return new Object[]{Grids.blank(rows, cols), new ArrayList<Integer>()};
        char[][] grid = Grids.copy(seedGrid);
        if (preseed != null) {
            List<int[]> slots = Grids.extractSlots(seedGrid, rows, cols);
            TreeSet<Integer> lockedCells = new TreeSet<>();
            List<Integer> all = new ArrayList<>();
            for (int i = 0; i < slots.size(); i++) {
                String w = preseed[i];
                if (w == null) continue;
                int[] cells = slots.get(i);
                for (int p = 0; p < cells.length; p++) {
                    int r = Cells.r(cells[p]), c = Cells.c(cells[p]);
                    if (grid[r][c] == BLACK) continue;
                    grid[r][c] = w.charAt(p);
                    all.add(cells[p]);
                }
            }
            all.sort(null);
            return new Object[]{grid, all};
        }
        if (locked != null && !locked.isEmpty()) {
            List<Integer> lockedCells = new ArrayList<>();
            locked.forEach((cell, ch) -> {
                int r = Cells.r(cell), c = Cells.c(cell);
                if (grid[r][c] == BLACK) return;
                grid[r][c] = ch;
                lockedCells.add(cell);
            });
            lockedCells.sort(null);
            return new Object[]{grid, lockedCells};
        }
        return new Object[]{grid, new ArrayList<Integer>()};
    }

    // ================================================================== content scores

    public static long contentScore(String[] words, List<int[]> cellsList, PW pw, Set<String> challenge) {
        long total = 0;
        for (int k = 0; k < words.length; k++) {
            String w = words[k];
            if (w == null) continue;
            int[] cells = cellsList == null ? null : cellsList.get(k);
            int bonus;
            if (challenge != null && !challenge.isEmpty() && challenge.contains(w)) bonus = CHALLENGE_WORD_SCORE_BONUS;
            else if (pw != null && !pw.isEmpty() && cells != null && pw.forCells(cells).contains(w)) bonus = THEME_WORD_SCORE_BONUS;
            else bonus = 0;
            long scored = Math.min(w.length(), CONTENT_SCORE_LENGTH_CAP) + bonus;
            total += scored * scored;
        }
        return total;
    }

    // ================================================================== one fill attempt

    /** Parameters of one try_fill call (Python keyword arguments). */
    public static final class FillArgs {
        public Long deadlineChecks;
        public Diag diagnostics;
        public Map<Integer, Character> forcedLetters;
        public Map<Integer, int[][]> letterScores;
        public String[] preseedAssignment;
        public Set<Integer> excludedSlots;
        public AtomicBoolean cancelEvent, batchAbandonedEvent, attemptDoneEvent;
        public Map<Integer, Character> lockedLetters;
        public Consumer<Diag> bestStateQueue;
        public AtomicLongArray checksProgress;
        public Integer checksSlot;
        public AtomicIntegerArray attemptActive;
        public Long attemptId;
        public Set<String> properNounWords;
        public Integer maxProperNouns;
        public Set<String> nonGlossWords;
        public Integer maxNonGloss;
        public PW priorityWords;
        public Set<String> challengeWords;
        public Set<Integer> requiredCells;
        /** Lets the search reshape the pattern for a "Mots Défi"/theme word (mirrors reshape_black_cells). */
        public boolean reshapeBlackCells;
        public Set<Integer> permanentBlackCells;
    }

    static List<Integer> quotaOverflowSlotIndices(String[] assignment, Set<String> offending) {
        List<Integer> out = new ArrayList<>();
        if (offending.isEmpty()) return out;
        for (int i = 0; i < assignment.length; i++) if (assignment[i] != null && offending.contains(assignment[i])) out.add(i);
        return out;
    }

    public static Result tryFill(char[][] grid, int rows, int cols, DualIndex index, Rng rng, FillArgs a) {
        long deadline = a.deadlineChecks != null ? a.deadlineChecks : (long) rows * cols * 2000;
        List<int[]> slots = Grids.extractSlots(grid, rows, cols);
        Diag diag = a.diagnostics;
        if (diag != null) {
            diag.slotCount = slots.size();
            TreeMap<Integer, Integer> lc = new TreeMap<>();
            for (int[] s : slots) lc.merge(s.length, 1, Integer::sum);
            Map<String, Object> lcj = new LinkedHashMap<>();
            lc.forEach((k, v) -> lcj.put(String.valueOf(k), v));
            diag.lengthCounts = lcj;
        }
        if (slots.isEmpty()) {
            if (diag != null) {
                diag.checks = 0L;
                diag.reason = "no_slots";
                diag.exampleGrid = grid;
                diag.impossibleCells = new ArrayList<>();
                diag.deadlockCells = new ArrayList<>();
                diag.excludedCells = new ArrayList<>();
                diag.statLetters = new ArrayList<>();
                diag.forcedCells = new ArrayList<>();
                diag.assignedLetterCount = 0;
                diag.assignment = new String[0];
                diag.impossibleSlots = new ArrayList<>();
                diag.lockedCells = new ArrayList<>();
            }
            return null;
        }
        Set<Integer> allSlotCells = new HashSet<>();
        for (int[] s : slots) for (int c : s) allSlotCells.add(c);
        List<Integer> lockedCells;
        if (a.lockedLetters != null && !a.lockedLetters.isEmpty()) {
            TreeSet<Integer> t = new TreeSet<>();
            for (int cell : a.lockedLetters.keySet()) if (allSlotCells.contains(cell)) t.add(cell);
            lockedCells = new ArrayList<>(t);
        } else if (a.preseedAssignment != null) {
            TreeSet<Integer> t = new TreeSet<>();
            for (int i = 0; i < a.preseedAssignment.length; i++) {
                if (a.preseedAssignment[i] != null) for (int c : slots.get(i)) t.add(c);
            }
            lockedCells = new ArrayList<>(t);
        } else {
            lockedCells = new ArrayList<>();
        }
        PW pw = a.priorityWords == null ? PW.EMPTY : a.priorityWords;
        Set<String> cw = a.challengeWords == null ? Set.of() : a.challengeWords;
        Filler filler = new Filler(slots, index, rng, a.forcedLetters, a.letterScores, a.excludedSlots, a.cancelEvent,
                a.batchAbandonedEvent, a.attemptDoneEvent, null, a.lockedLetters, pw, cw, rows, cols);
        filler.pattern = Grids.copy(grid);
        filler.bestPattern = filler.pattern;
        filler.reshapeEnabled = a.reshapeBlackCells && (a.excludedSlots == null || a.excludedSlots.isEmpty())
                && Filler.MAX_BACKGHOSTS_PER_DESCENT <= 0;
        filler.permanentBlackCells = a.permanentBlackCells == null ? Set.of() : a.permanentBlackCells;
        if (a.checksProgress != null && a.checksSlot != null) {
            final int slot = a.checksSlot;
            filler.onChecksProgress = checks -> a.checksProgress.set(slot, checks);
        }
        if (a.checksProgress != null && a.attemptActive != null && a.checksSlot != null) {
            filler.siblingChecksProgress = a.checksProgress;
            filler.siblingAttemptActive = a.attemptActive;
            filler.checksSlot = a.checksSlot;
        }
        if (a.bestStateQueue != null) {
            filler.onNewBest = best -> {
                // Called right as the record is taken: the Filler's current
                // pattern and slots are the ones `best` lives on.
                Object[] partial = Grids.buildPartialLettersGrid(filler.pattern, filler.slots, best, a.forcedLetters,
                        a.lockedLetters);
                Diag m = new Diag();
                m.grid = Grids.copy(filler.pattern);
                m.assignment = best.clone();
                m.exampleGrid = (char[][]) partial[0];
                m.impossibleCells = filler.impossibleZoneCells();
                m.deadlockCells = filler.deadlockZoneCells();
                m.excludedCells = filler.excludedZoneCells(best, true);
                m.statLetters = filler.bestStatLettersFor();
                m.impossibleSlots = filler.impossibleZoneSlots();
                @SuppressWarnings("unchecked")
                List<Integer> fc = (List<Integer>) partial[1];
                m.forcedCells = fc;
                m.lockedCells = lockedCells;
                m.themeCells = themeWordCells(filler.slots, best, pw);
                m.challengeCells = challengeWordCellsFromAssignment(filler.slots, best, cw);
                m.checks = filler.checks;
                m.reason = "best_state_snapshot";
                m.attemptId = a.attemptId;
                a.bestStateQueue.accept(m);
            };
            filler.onLiveState = current -> {
                Object[] partial = Grids.buildPartialLettersGrid(filler.pattern, filler.slots, current, a.forcedLetters,
                        a.lockedLetters);
                Diag m = new Diag();
                m.exampleGrid = (char[][]) partial[0];
                m.impossibleCells = filler.emptyDomainZoneCells(current);
                m.deadlockCells = new ArrayList<>();
                m.excludedCells = filler.excludedZoneCells(current, false);
                m.statLetters = filler.statLetters(current);
                @SuppressWarnings("unchecked")
                List<Integer> fc = (List<Integer>) partial[1];
                m.forcedCells = fc;
                m.lockedCells = lockedCells;
                m.themeCells = themeWordCells(filler.slots, current, pw);
                m.challengeCells = challengeWordCellsFromAssignment(filler.slots, current, cw);
                m.checks = filler.checks;
                m.reason = "live_heartbeat";
                m.attemptId = a.attemptId;
                m.kind = "heartbeat";
                a.bestStateQueue.accept(m);
            };
        }
        if (a.preseedAssignment != null) {
            filler.assignment = a.preseedAssignment.clone();
            filler.usedWords = Filler.usedOf(a.preseedAssignment);
            filler.bestAssignment = a.preseedAssignment.clone();
            int n = 0;
            for (String w : a.preseedAssignment) if (w != null) n++;
            filler.bestAssignedCount = n;
        }
        filler.markImmediatelyImpossibleSlots();
        boolean solvedInternally = filler.solve(deadline);
        // A failed search has undone every reshape, but its record may have
        // been taken on a reshaped grid: carry on from that grid, and hand its
        // pattern back to the caller through `grid` (mirrors try_fill).
        filler.adoptBestStructure();
        slots = filler.slots;
        for (int r = 0; r < rows; r++) System.arraycopy(filler.pattern[r], 0, grid[r], 0, cols);
        closeImpliedSlots(slots, index, filler.bestAssignment, filler.usedWords, filler.excludedSlots);
        filler.assignment = filler.bestAssignment.clone();
        boolean complete = true;
        for (String w : filler.assignment) if (w == null) complete = false;
        if (a.requiredCells != null) {
            complete = true;
            for (int i = 0; i < slots.size(); i++) {
                if (filler.assignment[i] != null) continue;
                for (int c : slots.get(i)) if (a.requiredCells.contains(c)) complete = false;
            }
        }
        boolean overProper = false;
        if (complete && a.maxProperNouns != null && a.properNounWords != null && !a.properNounWords.isEmpty()) {
            int n = 0;
            for (String w : filler.assignment) if (w != null && a.properNounWords.contains(w)) n++;
            overProper = n > a.maxProperNouns;
            if (overProper) complete = false;
        }
        boolean overNonGloss = false;
        if (complete && a.maxNonGloss != null && a.nonGlossWords != null && !a.nonGlossWords.isEmpty()) {
            int n = 0;
            for (String w : filler.assignment) if (w != null && a.nonGlossWords.contains(w)) n++;
            overNonGloss = n > a.maxNonGloss;
            if (overNonGloss) complete = false;
        }
        if (diag != null) {
            diag.checks = filler.checks;
            diag.reason = overProper ? "too_many_proper_nouns" : overNonGloss ? "too_many_non_gloss_words"
                    : complete ? "solved" : filler.interruptedBySibling ? "interrupted_other_attempt_done"
                    : filler.abandoned ? "abandoned_too_unfillable" : filler.checks >= deadline ? "deadline_exceeded"
                    : solvedInternally ? "blocked_on_excluded_slot" : "search_exhausted";
            if (!complete) {
                Object[] partial = Grids.buildPartialLettersGrid(grid, slots, filler.bestAssignment, a.forcedLetters,
                        a.lockedLetters);
                diag.exampleGrid = (char[][]) partial[0];
                @SuppressWarnings("unchecked")
                List<Integer> fc = (List<Integer>) partial[1];
                diag.forcedCells = fc;
                diag.impossibleCells = filler.impossibleZoneCells();
                diag.deadlockCells = filler.deadlockZoneCells();
                diag.excludedCells = filler.excludedZoneCells(filler.bestAssignment, true);
                diag.statLetters = filler.bestStatLettersFor();
                diag.assignedLetterCount = (int) partial[2];
                diag.assignment = filler.bestAssignment.clone();
                diag.impossibleSlots = filler.impossibleZoneSlots();
                if (overProper || overNonGloss) {
                    Set<String> offending = new HashSet<>();
                    for (String w : filler.bestAssignment) {
                        if (w == null) continue;
                        if (overProper && a.properNounWords.contains(w)) offending.add(w);
                        if (overNonGloss && a.nonGlossWords.contains(w)) offending.add(w);
                    }
                    List<Integer> quota = quotaOverflowSlotIndices(filler.bestAssignment, offending);
                    TreeSet<Integer> is = new TreeSet<>(diag.impossibleSlots);
                    is.addAll(quota);
                    diag.impossibleSlots = new ArrayList<>(is);
                    TreeSet<Integer> ic = new TreeSet<>(diag.impossibleCells);
                    for (int i : quota) for (int c : slots.get(i)) ic.add(c);
                    diag.impossibleCells = new ArrayList<>(ic);
                }
                diag.lockedCells = lockedCells;
                diag.themeCells = themeWordCells(slots, filler.bestAssignment, pw);
                diag.challengeCells = challengeWordCellsFromAssignment(slots, filler.bestAssignment, cw);
                diag.attemptId = a.attemptId;
            }
        }
        if (complete) return new Result(slots, filler.assignment);
        return null;
    }

    // ================================================================== minimization

    /** Returns {grid, slots, assignment}. */
    public static Object[] minimizeBlackSquares(char[][] grid, Result result, int rows, int cols, DualIndex index,
                                                Rng rng, long deadlineChecks, AtomicBoolean cancelEvent,
                                                Set<String> properNounWords, Integer maxProperNouns,
                                                Set<String> nonGlossWords, Integer maxNonGloss, PW priorityWords,
                                                Map<Integer, Character> permanentLocked, Set<Integer> permanentBlack,
                                                Set<String> challengeWords) {
        List<int[]> slots = result.slots();
        String[] assignment = result.assignment();
        boolean improved = true;
        while (improved) {
            improved = false;
            List<Integer> blacks = new ArrayList<>();
            for (int r = 0; r < rows; r++) for (int c = 0; c < cols; c++) if (grid[r][c] == BLACK) blacks.add(Cells.of(r, c));
            rng.shuffle(blacks);
            for (int cell : blacks) {
                if (cancelEvent != null && cancelEvent.get()) throw new GenerationCancelled();
                int r = Cells.r(cell), c = Cells.c(cell);
                if (grid[r][c] != BLACK) continue;
                if (permanentBlack != null && permanentBlack.contains(cell)) continue;
                char saved = grid[r][c];
                grid[r][c] = WHITE;
                if (Grids.isStructurallyValid(grid, rows, cols, 1)) {
                    Map<Integer, Character> merged = new LinkedHashMap<>(permanentLocked == null ? Map.of() : permanentLocked);
                    for (int i = 0; i < slots.size(); i++) {
                        String w = assignment[i];
                        if (w != null && challengeWords != null && challengeWords.contains(w)) {
                            int[] cells = slots.get(i);
                            for (int p = 0; p < cells.length; p++) merged.put(cells[p], w.charAt(p));
                        }
                    }
                    String[] preseed = null;
                    if (!merged.isEmpty()) {
                        List<int[]> trialSlots = Grids.extractSlots(grid, rows, cols);
                        preseed = new String[trialSlots.size()];
                        for (int k = 0; k < trialSlots.size(); k++) preseed[k] = Grids.wordAt(trialSlots.get(k), merged);
                    }
                    FillArgs fa = new FillArgs();
                    fa.deadlineChecks = deadlineChecks;
                    fa.cancelEvent = cancelEvent;
                    fa.properNounWords = properNounWords;
                    fa.maxProperNouns = maxProperNouns;
                    fa.nonGlossWords = nonGlossWords;
                    fa.maxNonGloss = maxNonGloss;
                    fa.priorityWords = priorityWords;
                    fa.lockedLetters = merged.isEmpty() ? null : merged;
                    fa.preseedAssignment = preseed;
                    Result nr = tryFill(grid, rows, cols, index, rng, fa);
                    if (nr != null) {
                        boolean allReal = true;
                        for (int i = 0; i < nr.assignment().length; i++) {
                            String w = nr.assignment()[i];
                            int[] cells = nr.slots().get(i);
                            if (w == null) {
                                allReal = false;
                                break;
                            }
                            LenIndex li = index.forCells(cells).get(w.length());
                            boolean real = li != null && li.wordSet.contains(w);
                            if (!real && !(!merged.isEmpty() && Grids.allKnown(cells, merged))) {
                                allReal = false;
                                break;
                            }
                        }
                        if (allReal) {
                            slots = nr.slots();
                            assignment = nr.assignment();
                            improved = true;
                            continue;
                        }
                    }
                }
                grid[r][c] = saved;
            }
        }
        return new Object[]{grid, slots, assignment};
    }

    // ================================================================== result words

    public static List<Map<String, Object>> buildWordEntries(char[][] grid, int rows, int cols, List<int[]> slots,
                                                             String[] assignment) {
        Map<Integer, List<Object[]>> starts = new LinkedHashMap<>();
        for (int i = 0; i < slots.size(); i++) {
            int[] cells = slots.get(i);
            String direction = cells.length > 1 && Cells.r(cells[1]) == Cells.r(cells[0]) ? "across" : "down";
            starts.computeIfAbsent(cells[0], k -> new ArrayList<>()).add(new Object[]{direction, i});
        }
        Map<Integer, Integer> numbers = new HashMap<>();
        int counter = 1;
        for (int r = 0; r < rows; r++) {
            for (int c = 0; c < cols; c++) if (starts.containsKey(Cells.of(r, c))) numbers.put(Cells.of(r, c), counter++);
        }
        List<Map<String, Object>> entries = new ArrayList<>();
        starts.forEach((cell, items) -> {
            int number = numbers.get(cell);
            for (Object[] it : items) {
                int i = (int) it[1];
                Map<String, Object> e = new LinkedHashMap<>();
                e.put("number", number);
                e.put("direction", it[0]);
                e.put("row", Cells.r(cell));
                e.put("col", Cells.c(cell));
                e.put("length", slots.get(i).length);
                e.put("answer", assignment[i]);
                entries.add(e);
            }
        });
        entries.sort((x, y) -> {
            int cmp = Integer.compare((int) x.get("number"), (int) y.get("number"));
            return cmp != 0 ? cmp : ((String) x.get("direction")).compareTo((String) y.get("direction"));
        });
        return entries;
    }

    public static Set<String> usedWordsOf(Collection<String> words) {
        Set<String> out = new HashSet<>();
        for (String w : words) if (w != null) out.add(w);
        return out;
    }
}
