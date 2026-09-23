package falcon.gen;

import falcon.GenerationCancelled;
import falcon.gen.Words.DualIndex;
import falcon.gen.Words.PW;

import java.util.ArrayList;
import java.util.Arrays;
import java.util.BitSet;
import java.util.Collection;
import java.util.HashMap;
import java.util.HashSet;
import java.util.Iterator;
import java.util.LinkedHashMap;
import java.util.LinkedHashSet;
import java.util.List;
import java.util.Map;
import java.util.Set;
import java.util.TreeSet;
import java.util.concurrent.atomic.AtomicBoolean;
import java.util.concurrent.atomic.AtomicIntegerArray;
import java.util.concurrent.atomic.AtomicLongArray;
import java.util.function.Consumer;
import java.util.function.LongConsumer;

/** The backtracking CSP solver (mirrors crossword_gen.Filler). */
public final class Filler {
    public static final int CANCEL_CHECK_INTERVAL = 500;
    public static final int CHECKS_PROGRESS_REPORT_INTERVAL = 500;
    public static final int LIVE_STATE_HEARTBEAT_INTERVAL = 5000;
    public static final boolean UNFILLABLE_ABANDON_ENABLED = false;
    public static final int UNFILLABLE_ABANDON_SLOT_COUNT = 3;
    public static final int UNFILLABLE_ABANDON_CHECK_INTERVAL = 500;
    public static final int PALIER_ATTEMPT_DONE_CHECK_INTERVAL = 500;
    public static final int CANDIDATE_SCORE_WINDOW = 50;
    public static final int MAX_DESCENTS_PER_NODE = 3;
    public static final int EARLY_DESCENTS_WORD_COUNT = 5;
    public static final int EARLY_MAX_DESCENTS_PER_NODE = 10;
    public static final boolean BACKJUMPING_ENABLED = true;
    public static final int MAX_EXCLUDED_SLOTS = 3;
    public static final boolean ALTERNATE_DIRECTION_ENABLED = false;
    public static final int SLOT_SELECTION_WINDOW_SIZE = 10;
    public static final int MOST_CONSTRAINED_START_LENGTH = 7;
    public static final int MOST_CONSTRAINED_MIN_LENGTH = 2;
    public static final double SLOT_SELECTION_REFINE_FRACTION = 0.5;
    public static final double FALLBACK_PHASE_BUDGET_FRACTION = 0.1;
    public static final int LETTER_BIAS_SAMPLE_SIZE = 100;

    /** Insertion-ordered set keeping only the {@code capacity} most recent
     * members (Python's _RecentSlots). */
    public static final class RecentSlots implements Iterable<Integer> {
        private final int capacity;
        private final LinkedHashSet<Integer> order = new LinkedHashSet<>();

        RecentSlots(int capacity) {
            this.capacity = capacity;
        }

        public void add(int i) {
            order.remove(i);
            order.add(i);
            while (order.size() > capacity) {
                Iterator<Integer> it = order.iterator();
                it.next();
                it.remove();
            }
        }

        public void addAll(Collection<Integer> other) {
            for (int i : other) add(i);
        }

        public void discard(int i) {
            order.remove(i);
        }

        public boolean contains(int i) {
            return order.contains(i);
        }

        public int size() {
            return order.size();
        }

        @Override
        public Iterator<Integer> iterator() {
            return new ArrayList<>(order).iterator();
        }
    }

    public final List<int[]> slots;
    public final DualIndex index;
    public final Rng rng;
    public final int rows, cols;
    public final PW priorityWords;
    public final Set<String> challengeWords;
    final Map<String, Integer> challengeAttemptCounts = new HashMap<>();
    public final Set<String> challengeAbandoned = new HashSet<>();
    public Integer challengeWordBudget;
    final Map<String, Integer> themeAttemptCounts = new HashMap<>();
    public final Set<String> themeAbandoned = new HashSet<>();
    public Integer themeWordBudget;
    public AtomicBoolean batchAbandonedEvent, attemptDoneEvent, cancelEvent;
    public final Map<Integer, Character> forcedLetters;
    public final Map<Integer, Character> lockedLetters;
    public final Map<Integer, int[][]> letterScoresByDir;
    public final Map<Integer, int[]> letterScores;
    final boolean[] slotAcross;
    List<Object> bestStatLetters;
    /** For slot i and position p: the crossing slot (or -1) and its position there. */
    final int[][] crossSlot, crossPos;
    /** cell -> [(slot, pos)...] in insertion order. */
    final Map<Integer, int[][]> cellToSlots;
    final int[][] crossingSlots;
    public boolean abandoned, budgetExhausted, breakingPermitted, interruptedBySibling;
    public Set<Integer> toleratedDry = new HashSet<>();
    Set<Integer> lastConflict;
    public String[] assignment;
    public Set<Integer> excludedSlots;
    public Set<String> usedWords = new HashSet<>();
    public long checks;
    public final RecentSlots impossibleThisAttempt = new RecentSlots(MAX_EXCLUDED_SLOTS);
    public String[] bestAssignment;
    public int bestAssignedCount;
    int initialAssignedCount;
    public Consumer<String[]> onNewBest;
    public LongConsumer onChecksProgress;
    public Consumer<String[]> onLiveState;
    public AtomicLongArray siblingChecksProgress;
    public AtomicIntegerArray siblingAttemptActive;
    public Integer checksSlot;
    boolean deadlineExtensionDenied;
    private final long[] lastCheckpoint = new long[6];

    public Filler(List<int[]> slots, DualIndex index, Rng rng, Map<Integer, Character> forcedLetters,
                  Map<Integer, int[][]> letterScores, Set<Integer> excludedSlots, AtomicBoolean cancelEvent,
                  AtomicBoolean batchAbandonedEvent, AtomicBoolean attemptDoneEvent, Consumer<String[]> onNewBest,
                  Map<Integer, Character> lockedLetters, PW priorityWords, Set<String> challengeWords,
                  Integer rows, Integer cols) {
        this.slots = slots;
        this.index = index;
        this.rng = rng;
        int maxR = -1, maxC = -1;
        for (int[] cells : slots) {
            for (int cell : cells) {
                maxR = Math.max(maxR, Cells.r(cell));
                maxC = Math.max(maxC, Cells.c(cell));
            }
        }
        this.rows = rows != null ? rows : maxR + 1;
        this.cols = cols != null ? cols : maxC + 1;
        this.priorityWords = priorityWords == null ? PW.EMPTY : priorityWords;
        this.challengeWords = challengeWords == null ? Set.of() : challengeWords;
        this.batchAbandonedEvent = batchAbandonedEvent;
        this.attemptDoneEvent = attemptDoneEvent;
        this.cancelEvent = cancelEvent;
        this.forcedLetters = forcedLetters == null ? new HashMap<>() : forcedLetters;
        this.lockedLetters = lockedLetters == null ? new HashMap<>() : lockedLetters;
        this.letterScoresByDir = new LinkedHashMap<>();
        this.letterScores = new HashMap<>();
        if (letterScores != null) {
            letterScores.forEach((cell, byDir) -> {
                int[][] copy = new int[][]{byDir[0], byDir[1]};
                letterScoresByDir.put(cell, copy);
                this.letterScores.put(cell, Tally.combined(copy));
            });
        }
        int n = slots.size();
        this.slotAcross = new boolean[n];
        this.crossSlot = new int[n][];
        this.crossPos = new int[n][];
        this.cellToSlots = new LinkedHashMap<>();
        Map<Integer, List<int[]>> tmp = new LinkedHashMap<>();
        for (int i = 0; i < n; i++) {
            int[] cells = slots.get(i);
            slotAcross[i] = cells.length > 1 && Cells.r(cells[1]) == Cells.r(cells[0]);
            for (int p = 0; p < cells.length; p++) tmp.computeIfAbsent(cells[p], k -> new ArrayList<>()).add(new int[]{i, p});
        }
        tmp.forEach((cell, list) -> cellToSlots.put(cell, list.toArray(new int[0][])));
        this.crossingSlots = new int[n][];
        for (int i = 0; i < n; i++) {
            int[] cells = slots.get(i);
            crossSlot[i] = new int[cells.length];
            crossPos[i] = new int[cells.length];
            TreeSet<Integer> crossing = new TreeSet<>();
            for (int p = 0; p < cells.length; p++) {
                crossSlot[i][p] = -1;
                for (int[] jp : cellToSlots.get(cells[p])) {
                    if (jp[0] != i) {
                        if (crossSlot[i][p] < 0) {
                            crossSlot[i][p] = jp[0];
                            crossPos[i][p] = jp[1];
                        }
                        crossing.add(jp[0]);
                    }
                }
            }
            crossingSlots[i] = crossing.stream().mapToInt(Integer::intValue).toArray();
        }
        this.assignment = new String[n];
        this.excludedSlots = excludedSlots != null ? excludedSlots : new HashSet<>();
        this.bestAssignment = assignment.clone();
        this.onNewBest = onNewBest;
        Arrays.fill(lastCheckpoint, Long.MIN_VALUE);
    }

    // ================================================================== checkpoints

    private boolean checkpointDue(int name, int interval) {
        long last = lastCheckpoint[name];
        if (last == Long.MIN_VALUE || checks - last >= interval || checks < last) {
            lastCheckpoint[name] = checks;
            return true;
        }
        return false;
    }

    boolean periodicCheckpoints() {
        if (cancelEvent != null && checkpointDue(0, CANCEL_CHECK_INTERVAL) && cancelEvent.get()) {
            throw new GenerationCancelled();
        }
        if (onChecksProgress != null && checkpointDue(1, CHECKS_PROGRESS_REPORT_INTERVAL)) onChecksProgress.accept(checks);
        if (onLiveState != null && checkpointDue(2, LIVE_STATE_HEARTBEAT_INTERVAL)) onLiveState.accept(assignment);
        if (batchAbandonedEvent != null && checkpointDue(3, UNFILLABLE_ABANDON_CHECK_INTERVAL) && batchAbandonedEvent.get()) {
            abandoned = true;
            return true;
        }
        if (attemptDoneEvent != null && checkpointDue(4, PALIER_ATTEMPT_DONE_CHECK_INTERVAL) && attemptDoneEvent.get()) {
            abandoned = true;
            interruptedBySibling = true;
            return true;
        }
        if (UNFILLABLE_ABANDON_ENABLED && checkpointDue(5, UNFILLABLE_ABANDON_CHECK_INTERVAL)
                && impossibleZoneSlots().size() > UNFILLABLE_ABANDON_SLOT_COUNT) {
            abandoned = true;
            if (batchAbandonedEvent != null) batchAbandonedEvent.set(true);
            return true;
        }
        return false;
    }

    // ================================================================== domains

    /** The letter currently fixed at position p of slot i by a crossing
     * assigned word, else the locked letter, else (optionally) the seed. */
    Character constraintAt(int i, int p, boolean ignoreForced) {
        int j = crossSlot[i][p];
        if (j >= 0 && assignment[j] != null) return assignment[j].charAt(crossPos[i][p]);
        int cell = slots.get(i)[p];
        Character letter = lockedLetters.get(cell);
        if (letter == null && !ignoreForced) letter = forcedLetters.get(cell);
        return letter;
    }

    public Dom domain(int i) {
        return domain(i, false);
    }

    public Dom domain(int i, boolean ignoreForced) {
        int[] cells = slots.get(i);
        LenIndex idx = index.forCells(cells).get(cells.length);
        if (idx == null) return Dom.EMPTY;
        BitSet result = null;
        for (int p = 0; p < cells.length; p++) {
            Character ch = constraintAt(i, p, ignoreForced);
            if (ch == null) continue;
            BitSet s = idx.at(p, ch);
            if (s == null) return Dom.EMPTY;
            if (result == null) {
                result = (BitSet) s.clone();
            } else {
                result.and(s);
                if (result.isEmpty()) return Dom.EMPTY;
            }
        }
        if (result == null) return Dom.full(idx);
        return new Dom(idx, result);
    }

    public boolean challengeWordFits(int i, String word) {
        int[] cells = slots.get(i);
        if (word.length() != cells.length) return false;
        for (int p = 0; p < cells.length; p++) {
            int j = crossSlot[i][p];
            Character letter = null;
            if (j >= 0 && assignment[j] != null) letter = assignment[j].charAt(crossPos[i][p]);
            if (letter == null) letter = lockedLetters.get(cells[p]);
            if (letter != null && word.charAt(p) != letter) return false;
        }
        return true;
    }

    public Set<String> activeChallengeWords() {
        if (challengeWords.isEmpty() || challengeAbandoned.isEmpty()) return challengeWords;
        Set<String> out = new LinkedHashSet<>(challengeWords);
        out.removeAll(challengeAbandoned);
        return out;
    }

    public void registerChallengeWordBreak(String word) {
        int count = challengeAttemptCounts.merge(word, 1, Integer::sum);
        if (challengeWordBudget != null && count >= challengeWordBudget) challengeAbandoned.add(word);
    }

    public Set<String> activePriorityWordsFor(int[] cells) {
        Set<String> pw = priorityWords.forCells(cells);
        if (pw.isEmpty() || themeAbandoned.isEmpty()) return pw;
        Set<String> out = new HashSet<>(pw);
        out.removeAll(themeAbandoned);
        return out;
    }

    public void registerThemeWordBreak(String word) {
        int count = themeAttemptCounts.merge(word, 1, Integer::sum);
        if (themeWordBudget != null && count >= themeWordBudget) themeAbandoned.add(word);
    }

    boolean cellFixedByCrosser(int i, int p) {
        int j = crossSlot[i][p];
        return j >= 0 && assignment[j] != null;
    }

    int placedLetterCount(int i) {
        int count = 0;
        int[] cells = slots.get(i);
        for (int p = 0; p < cells.length; p++) {
            if (lockedLetters.containsKey(cells[p]) || cellFixedByCrosser(i, p)) count++;
        }
        return count;
    }

    boolean hasKnownLetter(int i) {
        int[] cells = slots.get(i);
        for (int p = 0; p < cells.length; p++) {
            if (lockedLetters.containsKey(cells[p]) || cellFixedByCrosser(i, p)) return true;
        }
        return false;
    }

    long slotLetterFrequencyScore(int i) {
        long total = 0;
        int[] cells = slots.get(i);
        for (int p = 0; p < cells.length; p++) {
            if (lockedLetters.containsKey(cells[p]) || cellFixedByCrosser(i, p)) continue;
            int[] counts = letterScores.get(cells[p]);
            if (Tally.nonEmpty(counts)) {
                long m = Tally.max(counts);
                total += m * m;
            }
        }
        return total;
    }

    Integer slotMinLetterOptions(int i) {
        Integer best = null;
        int[] cells = slots.get(i);
        for (int p = 0; p < cells.length; p++) {
            if (lockedLetters.containsKey(cells[p]) || cellFixedByCrosser(i, p)) continue;
            int[][] byDir = letterScoresByDir.get(cells[p]);
            if (byDir == null || (byDir[0] == null && byDir[1] == null)) continue;
            Integer count = Tally.crossedOptionCount(byDir);
            if (count == null) continue;
            if (best == null || count < best) best = count;
        }
        return best;
    }

    // ================================================================== stat letters / tallies

    public List<Object> statLetters(String[] asg) {
        if (asg == null) asg = assignment;
        Set<Integer> fixed = new HashSet<>(lockedLetters.keySet());
        fixed.addAll(forcedLetters.keySet());
        for (int i = 0; i < asg.length; i++) {
            if (asg[i] != null) for (int c : slots.get(i)) fixed.add(c);
        }
        List<Object[]> rowsOut = new ArrayList<>();
        letterScoresByDir.forEach((cell, byDir) -> {
            if (fixed.contains(cell)) return;
            Character letter = Tally.mostProbable(byDir);
            if (letter != null) rowsOut.add(new Object[]{cell, letter});
        });
        rowsOut.sort((a, b) -> {
            int cmp = Integer.compare((int) a[0], (int) b[0]);
            return cmp != 0 ? cmp : Character.compare((char) a[1], (char) b[1]);
        });
        List<Object> out = new ArrayList<>();
        for (Object[] e : rowsOut) out.add(List.of(Cells.r((int) e[0]), Cells.c((int) e[0]), String.valueOf(e[1])));
        return out;
    }

    public List<Object> bestStatLettersFor() {
        if (bestStatLetters == null) return statLetters(bestAssignment);
        Set<Integer> filled = new HashSet<>();
        for (int i = 0; i < bestAssignment.length; i++) {
            if (bestAssignment[i] != null) for (int c : slots.get(i)) filled.add(c);
        }
        List<Object> out = new ArrayList<>();
        for (Object e : bestStatLetters) {
            List<?> l = (List<?>) e;
            if (!filled.contains(Cells.of((int) l.get(0), (int) l.get(1)))) out.add(e);
        }
        return out;
    }

    Map<Integer, Object[]> refreshLetterScoresAround(int i) {
        Map<Integer, Object[]> saved = new HashMap<>();
        Set<Integer> seen = new HashSet<>();
        int[] cells = slots.get(i);
        for (int cell : cells) {
            for (int[] jp : cellToSlots.get(cell)) {
                int j = jp[0];
                if (j == i || seen.contains(j) || assignment[j] != null) continue;
                seen.add(j);
                Dom cands = domain(j);
                if (cands.isEmpty()) continue;
                List<String> sample = cands.choices(rng, LETTER_BIAS_SAMPLE_SIZE);
                int dir = slotAcross[j] ? 0 : 1;
                int[] jcells = slots.get(j);
                for (int pos = 0; pos < jcells.length; pos++) {
                    int jcell = jcells[pos];
                    if (!saved.containsKey(jcell)) {
                        int[][] byDir = letterScoresByDir.get(jcell);
                        saved.put(jcell, new Object[]{letterScores.get(jcell),
                                byDir == null ? null : new int[][]{byDir[0], byDir[1]}});
                    }
                    int[] fresh = Tally.ofLetters(sample, pos);
                    letterScores.put(jcell, fresh);
                    letterScoresByDir.computeIfAbsent(jcell, k -> new int[2][])[dir] = fresh;
                }
            }
        }
        return saved;
    }

    void restoreLetterScores(Map<Integer, Object[]> saved) {
        saved.forEach((cell, pair) -> {
            if (pair[0] == null) letterScores.remove(cell);
            else letterScores.put(cell, (int[]) pair[0]);
            if (pair[1] == null) letterScoresByDir.remove(cell);
            else letterScoresByDir.put(cell, (int[][]) pair[1]);
        });
    }

    public long candidateScore(int i, String word) {
        int[] cells = slots.get(i);
        long total = 0;
        for (int p = 0; p < cells.length; p++) {
            if (cellFixedByCrosser(i, p)) continue;
            long v = Tally.get(letterScores.get(cells[p]), word.charAt(p));
            total += v * v;
        }
        return total;
    }

    /** Shuffle, rank by statistical score, then draw at random inside a
     * sliding window of the CANDIDATE_SCORE_WINDOW best remaining words. */
    public List<String> orderedCandidates(int i, Collection<String> candsIn) {
        List<String> cands = new ArrayList<>(candsIn);
        rng.shuffle(cands);
        if (letterScores.isEmpty()) return cands;
        int n = cands.size();
        long[] scores = new long[n];
        Integer[] order = new Integer[n];
        for (int k = 0; k < n; k++) {
            scores[k] = candidateScore(i, cands.get(k));
            order[k] = k;
        }
        Arrays.sort(order, (a, b) -> Long.compare(scores[b], scores[a]));
        List<String> reordered = new ArrayList<>(n);
        List<String> window = new ArrayList<>(CANDIDATE_SCORE_WINDOW);
        int next = 0;
        while (next < n && window.size() < CANDIDATE_SCORE_WINDOW) window.add(cands.get(order[next++]));
        while (!window.isEmpty()) {
            int idx = rng.randrange(window.size());
            reordered.add(window.remove(idx));
            if (next < n) window.add(cands.get(order[next++]));
        }
        return reordered;
    }

    // ================================================================== dryness / conflicts

    public Set<Integer> markImmediatelyImpossibleSlots() {
        Set<Integer> flagged = dryOpenSlots();
        impossibleThisAttempt.addAll(flagged);
        return flagged;
    }

    boolean challengeCanFill(int i, Set<String> active) {
        if (active.isEmpty()) return false;
        for (String w : active) if (!usedWords.contains(w) && challengeWordFits(i, w)) return true;
        return false;
    }

    Set<Integer> dryOpenSlots() {
        Set<String> active = activeChallengeWords();
        Set<Integer> out = new LinkedHashSet<>();
        for (int i = 0; i < slots.size(); i++) {
            if (assignment[i] != null || excludedSlots.contains(i)) continue;
            if (domain(i).allIn(usedWords) && !challengeCanFill(i, active)) out.add(i);
        }
        return out;
    }

    Set<Integer> assignedCrossers(int i) {
        Set<Integer> out = new HashSet<>();
        for (int k : crossingSlots[i]) if (assignment[k] != null) out.add(k);
        return out;
    }

    Set<Integer> drySlotConflict(int i) {
        Set<Integer> conflict = assignedCrossers(i);
        Dom d = domain(i);
        if (!d.isEmpty()) {
            for (int k = 0; k < assignment.length; k++) {
                if (assignment[k] != null && d.contains(assignment[k])) conflict.add(k);
            }
        }
        return conflict;
    }

    boolean fail(Set<Integer> conflict) {
        lastConflict = BACKJUMPING_ENABLED && conflict != null ? new HashSet<>(conflict) : null;
        return false;
    }

    public boolean solve(long deadlineChecks) {
        if (!challengeWords.isEmpty()) challengeWordBudget = (int) Math.max(1, Math.rint(FALLBACK_PHASE_BUDGET_FRACTION * deadlineChecks));
        if (!priorityWords.isEmpty()) themeWordBudget = (int) Math.max(1, Math.rint(FALLBACK_PHASE_BUDGET_FRACTION * deadlineChecks));
        toleratedDry = dryOpenSlots();
        int count = 0;
        for (String a : assignment) if (a != null) count++;
        initialAssignedCount = count;
        if (backtrack(deadlineChecks, false)) return true;
        if (abandoned || budgetExhausted) return false;
        breakingPermitted = true;
        return backtrack(deadlineChecks, false);
    }

    // ================================================================== letter options

    static BitSet[] lettersFromCounts(int[][] counts, Collection<String> removed) {
        int[][] deltas = new int[counts.length][];
        for (String w : removed) {
            for (int p = 0; p < w.length() && p < counts.length; p++) {
                if (deltas[p] == null) deltas[p] = new int[Alpha.size()];
                int id = Alpha.id(w.charAt(p));
                if (id < deltas[p].length) deltas[p][id]++;
            }
        }
        BitSet[] out = new BitSet[counts.length];
        for (int p = 0; p < counts.length; p++) {
            BitSet s = new BitSet();
            int[] c = counts[p];
            for (int id = 0; id < c.length; id++) {
                if (c[id] - Tally.get(deltas[p], id) > 0) s.set(id);
            }
            out[p] = s;
        }
        return out;
    }

    BitSet[] slotLetterOptions(int i, Set<String> used, Collection<String> challenge) {
        int[] cells = slots.get(i);
        Dom d = domain(i, true);
        LenIndex idx = index.forCells(cells).get(cells.length);
        BitSet[] letters;
        if (idx != null && d.isFull()) {
            List<String> usedIn = new ArrayList<>();
            for (String u : used) if (u.length() == cells.length && idx.wordSet.contains(u)) usedIn.add(u);
            letters = lettersFromCounts(idx.blankCounts(), usedIn);
        } else {
            letters = new BitSet[cells.length];
            for (int p = 0; p < cells.length; p++) letters[p] = new BitSet();
            for (String w : d) {
                if (used.contains(w)) continue;
                for (int p = 0; p < cells.length; p++) letters[p].set(Alpha.id(w.charAt(p)));
            }
        }
        addChallengeLetters(i, letters, used, challenge);
        return letters;
    }

    void addChallengeLetters(int i, BitSet[] letters, Set<String> used, Collection<String> challenge) {
        if (challenge == null) return;
        for (String cw : challenge) {
            if (!used.contains(cw) && challengeWordFits(i, cw)) {
                for (int p = 0; p < cw.length(); p++) letters[p].set(Alpha.id(cw.charAt(p)));
            }
        }
    }

    /** Cached (used-word set, counts, letters, domain) of one slot. */
    static final class OptionsEntry {
        final Set<String> used;
        final int[][] counts;
        final BitSet[] letters;
        final Dom domain;
        final Set<String> fullMembers;

        OptionsEntry(Set<String> used, int[][] counts, BitSet[] letters, Dom domain, Set<String> fullMembers) {
            this.used = used;
            this.counts = counts;
            this.letters = letters;
            this.domain = domain;
            this.fullMembers = fullMembers;
        }

        boolean contains(String w) {
            return fullMembers != null ? fullMembers.contains(w) : domain.contains(w);
        }
    }

    public boolean slotIsBlocked(int i, Set<String> used, Collection<String> challenge, Map<Object, OptionsEntry> cache,
                                 int[] fresh, String placedWord) {
        BitSet[] opts = letterOptionsCached(i, used, challenge, cache, fresh, placedWord);
        if (opts.length == 0 || opts[0].isEmpty()) return true;
        int[] cells = slots.get(i);
        for (int p = 0; p < cells.length; p++) {
            BitSet own = opts[p];
            for (int[] jp : cellToSlots.get(cells[p])) {
                int j = jp[0];
                if (j == i || assignment[j] != null || excludedSlots.contains(j)) continue;
                BitSet[] other = letterOptionsCached(j, used, challenge, cache, fresh, placedWord);
                if (other.length > 0 && !other[jp[1]].isEmpty() && !own.intersects(other[jp[1]])) return true;
            }
        }
        return false;
    }

    BitSet[] letterOptionsCached(int i, Set<String> used, Collection<String> challenge, Map<Object, OptionsEntry> cache,
                                 int[] fresh, String placedWord) {
        if (cache == null) return slotLetterOptions(i, used, challenge);
        boolean isFresh = false;
        if (fresh != null) for (int f : fresh) if (f == i) {
            isFresh = true;
            break;
        }
        Object key = isFresh ? i + "|" + knownLettersSignature(i) : (Object) i;
        Set<String> nodeUsed = new HashSet<>(used);
        if (placedWord != null) nodeUsed.remove(placedWord);
        OptionsEntry entry = cache.get(key);
        if (entry == null || !entry.used.equals(nodeUsed)) {
            entry = slotLetterCounts(i, nodeUsed);
            cache.put(key, entry);
        }
        BitSet[] letters = entry.letters;
        boolean copied = false;
        if (placedWord != null && !nodeUsed.contains(placedWord) && entry.contains(placedWord)) {
            boolean last = false;
            for (int p = 0; p < placedWord.length() && p < entry.counts.length; p++) {
                if (Tally.get(entry.counts[p], placedWord.charAt(p)) == 1) {
                    last = true;
                    break;
                }
            }
            if (last) {
                letters = lettersFromCounts(entry.counts, List.of(placedWord));
                copied = true;
            }
        }
        if (!copied) {
            if (challenge == null || challenge.isEmpty()) return letters;
            BitSet[] c = new BitSet[letters.length];
            for (int p = 0; p < letters.length; p++) c[p] = (BitSet) letters[p].clone();
            letters = c;
        }
        addChallengeLetters(i, letters, used, challenge);
        return letters;
    }

    String knownLettersSignature(int i) {
        int[] cells = slots.get(i);
        StringBuilder sb = new StringBuilder(cells.length);
        for (int p = 0; p < cells.length; p++) {
            int j = crossSlot[i][p];
            Character letter = null;
            if (j >= 0 && assignment[j] != null) letter = assignment[j].charAt(crossPos[i][p]);
            if (letter == null) letter = lockedLetters.get(cells[p]);
            sb.append(letter == null ? '\0' : letter);
        }
        return sb.toString();
    }

    OptionsEntry slotLetterCounts(int i, Set<String> used) {
        int[] cells = slots.get(i);
        Dom d = domain(i, true);
        LenIndex idx = index.forCells(cells).get(cells.length);
        int[][] counts;
        Set<String> fullMembers = null;
        if (idx != null && d.isFull()) {
            int[][] base = idx.blankCounts();
            counts = new int[base.length][];
            for (int p = 0; p < base.length; p++) counts[p] = base[p].clone();
            for (String u : used) {
                if (u.length() == cells.length && idx.wordSet.contains(u)) {
                    for (int p = 0; p < u.length(); p++) counts[p][Alpha.id(u.charAt(p))]--;
                }
            }
            fullMembers = idx.wordSet;
        } else {
            counts = new int[cells.length][Alpha.size()];
            for (String w : d) {
                if (used.contains(w)) continue;
                for (int p = 0; p < cells.length; p++) counts[p][Alpha.id(w.charAt(p))]++;
            }
        }
        BitSet[] letters = new BitSet[counts.length];
        for (int p = 0; p < counts.length; p++) {
            BitSet s = new BitSet();
            for (int id = 0; id < counts[p].length; id++) if (counts[p][id] > 0) s.set(id);
            letters[p] = s;
        }
        return new OptionsEntry(Set.copyOf(used), counts, letters, d, fullMembers);
    }

    /** Returns {deadlocked slot indices, conflicting cells}. */
    public Object[] crossingDeadlockSlots(Set<String> used, Collection<String> challenge) {
        Map<Integer, BitSet[]> lettersBySlot = new HashMap<>();
        for (int i = 0; i < assignment.length; i++) {
            if (assignment[i] == null) lettersBySlot.put(i, slotLetterOptions(i, used, challenge));
        }
        Set<Integer> deadlocked = new LinkedHashSet<>();
        Set<Integer> deadlockCells = new LinkedHashSet<>();
        cellToSlots.forEach((cell, entries) -> {
            List<int[]> open = new ArrayList<>();
            for (int[] e : entries) if (lettersBySlot.containsKey(e[0])) open.add(e);
            for (int a = 0; a < open.size(); a++) {
                BitSet li = lettersBySlot.get(open.get(a)[0])[open.get(a)[1]];
                if (li.isEmpty()) continue;
                for (int b = a + 1; b < open.size(); b++) {
                    BitSet lj = lettersBySlot.get(open.get(b)[0])[open.get(b)[1]];
                    if (!lj.isEmpty() && !li.intersects(lj)) {
                        deadlocked.add(open.get(a)[0]);
                        deadlocked.add(open.get(b)[0]);
                        deadlockCells.add(cell);
                    }
                }
            }
        });
        return new Object[]{deadlocked, deadlockCells};
    }

    static Set<String> usedOf(String[] asg) {
        Set<String> out = new HashSet<>();
        for (String w : asg) if (w != null) out.add(w);
        return out;
    }

    @SuppressWarnings("unchecked")
    public List<Integer> impossibleZoneSlots() {
        String[] saved = assignment;
        assignment = bestAssignment;
        Set<String> usedAtBest = usedOf(bestAssignment);
        Set<String> active = activeChallengeWords();
        Set<Integer> deadlocked = (Set<Integer>) crossingDeadlockSlots(usedAtBest, active)[0];
        List<Integer> result = new ArrayList<>();
        for (int i = 0; i < bestAssignment.length; i++) {
            if (bestAssignment[i] != null) continue;
            if (deadlocked.contains(i)) {
                result.add(i);
                continue;
            }
            if (domain(i, true).allIn(usedAtBest)) {
                boolean fits = false;
                for (String cw : active) {
                    if (!usedAtBest.contains(cw) && challengeWordFits(i, cw)) {
                        fits = true;
                        break;
                    }
                }
                if (!fits) result.add(i);
            }
        }
        assignment = saved;
        return result;
    }

    public List<Integer> emptyDomainZoneCells(String[] asg) {
        String[] saved = assignment;
        assignment = asg;
        Set<String> used = usedOf(asg);
        Set<String> active = activeChallengeWords();
        TreeSet<Integer> cells = new TreeSet<>();
        for (int i = 0; i < asg.length; i++) {
            if (asg[i] != null || excludedSlots.contains(i)) continue;
            if (domain(i, true).allIn(used)) {
                boolean fits = false;
                for (String cw : active) {
                    if (!used.contains(cw) && challengeWordFits(i, cw)) {
                        fits = true;
                        break;
                    }
                }
                if (!fits) for (int c : slots.get(i)) cells.add(c);
            }
        }
        assignment = saved;
        return new ArrayList<>(cells);
    }

    public List<Integer> impossibleZoneCells() {
        TreeSet<Integer> cells = new TreeSet<>();
        for (int i : impossibleZoneSlots()) for (int c : slots.get(i)) cells.add(c);
        return new ArrayList<>(cells);
    }

    @SuppressWarnings("unchecked")
    public List<Integer> deadlockZoneCells() {
        Set<String> usedAtBest = usedOf(bestAssignment);
        String[] saved = assignment;
        assignment = bestAssignment;
        Set<Integer> cells = (Set<Integer>) crossingDeadlockSlots(usedAtBest, activeChallengeWords())[1];
        assignment = saved;
        return new ArrayList<>(new TreeSet<>(cells));
    }

    @SuppressWarnings("unchecked")
    public List<Integer> excludedZoneCells(String[] asg, boolean includeDeadlock) {
        if (asg == null) asg = assignment;
        TreeSet<Integer> cells = new TreeSet<>();
        for (int i : impossibleThisAttempt) if (asg[i] == null) for (int c : slots.get(i)) cells.add(c);
        if (includeDeadlock) {
            Set<String> used = usedOf(asg);
            String[] saved = assignment;
            assignment = asg;
            Set<Integer> deadlocked = (Set<Integer>) crossingDeadlockSlots(used, activeChallengeWords())[0];
            assignment = saved;
            for (int i : deadlocked) for (int c : slots.get(i)) cells.add(c);
        }
        return new ArrayList<>(cells);
    }

    // ================================================================== slot selection

    public int selectTargetSlot(List<Integer> unassigned, Map<Integer, Dom> domains) {
        List<Integer> directionPool;
        if (ALTERNATE_DIRECTION_ENABLED) {
            List<Integer> a = new ArrayList<>(), d = new ArrayList<>();
            for (int i : unassigned) (slotAcross[i] ? a : d).add(i);
            if (!a.isEmpty() && !d.isEmpty()) directionPool = rng.randrange(a.size() + d.size()) < a.size() ? a : d;
            else directionPool = a.isEmpty() ? d : a;
        } else {
            directionPool = new ArrayList<>(unassigned);
        }
        Set<String> active = activeChallengeWords();
        if (!active.isEmpty()) {
            List<Integer> placeable = new ArrayList<>();
            for (int i : directionPool) if (challengeCanFill(i, active)) placeable.add(i);
            if (!placeable.isEmpty()) directionPool = placeable;
        }
        List<Integer> few = new ArrayList<>();
        for (int i : directionPool) {
            Dom d = domains.get(i);
            if (slots.get(i).length >= 4 && (d == null ? 0 : d.size()) < Grids.PREFILL_MIN_WORD_COUNT) few.add(i);
        }
        List<Integer> pool = few.isEmpty() ? directionPool : few;
        List<Integer> nonBlank = new ArrayList<>();
        for (int i : pool) if (hasKnownLetter(i)) nonBlank.add(i);
        if (!nonBlank.isEmpty()) pool = nonBlank;
        if (!priorityWords.isEmpty()) {
            Set<String> pw = activePriorityWordsFor(slots.get(pool.get(0)));
            List<Integer> themePlaceable = new ArrayList<>();
            for (int i : pool) {
                Dom d = domains.get(i);
                for (String w : pw) {
                    if (!usedWords.contains(w) && d != null && d.contains(w)) {
                        themePlaceable.add(i);
                        break;
                    }
                }
            }
            if (!themePlaceable.isEmpty()) pool = themePlaceable;
        }
        double cr = (rows - 1) / 2.0, cc = (cols - 1) / 2.0;
        Map<Integer, Double> scores = new HashMap<>();
        for (int i : pool) {
            double best = Double.MAX_VALUE;
            for (int cell : slots.get(i)) {
                double dcol = Cells.c(cell) - cc, drow = Cells.r(cell) - cr;
                best = Math.min(best, dcol * dcol + drow * drow);
            }
            scores.put(i, best);
        }
        List<Integer> shuffled = new ArrayList<>(pool);
        rng.shuffle(shuffled);
        shuffled.sort((a, b) -> Double.compare(scores.get(a), scores.get(b)));
        List<Integer> window = new ArrayList<>(shuffled.subList(0, Math.min(SLOT_SELECTION_WINDOW_SIZE, shuffled.size())));
        Map<Integer, Integer> allCounts = new LinkedHashMap<>();
        for (int i : window) {
            if (slots.get(i).length < MOST_CONSTRAINED_MIN_LENGTH) continue;
            Integer count = slotMinLetterOptions(i);
            if (count != null) allCounts.put(i, count);
        }
        for (int threshold = MOST_CONSTRAINED_START_LENGTH; threshold >= MOST_CONSTRAINED_MIN_LENGTH; threshold--) {
            Map<Integer, Integer> optionCounts = new LinkedHashMap<>();
            for (Map.Entry<Integer, Integer> e : allCounts.entrySet()) {
                if (slots.get(e.getKey()).length >= threshold) optionCounts.put(e.getKey(), e.getValue());
            }
            if (!optionCounts.isEmpty()) {
                int fewest = optionCounts.values().stream().min(Integer::compare).get();
                List<Integer> kept = new ArrayList<>();
                for (int i : window) if (optionCounts.containsKey(i) && optionCounts.get(i) == fewest) kept.add(i);
                window = kept;
                break;
            }
        }
        List<Integer> shuffledWindow = new ArrayList<>(window);
        rng.shuffle(shuffledWindow);
        Map<Integer, Integer> placed = new HashMap<>();
        for (int i : window) placed.put(i, placedLetterCount(i));
        int refinedSize = Math.max(1, (int) (window.size() * SLOT_SELECTION_REFINE_FRACTION));
        shuffledWindow.sort((a, b) -> Integer.compare(placed.get(b), placed.get(a)));
        List<Integer> refined = new ArrayList<>(shuffledWindow.subList(0, Math.min(refinedSize, shuffledWindow.size())));
        rng.shuffle(refined);
        Map<Integer, Long> freq = new HashMap<>();
        for (int i : refined) freq.put(i, slotLetterFrequencyScore(i));
        refined.sort((a, b) -> Long.compare(freq.get(b), freq.get(a)));
        return refined.get(0);
    }

    // ================================================================== deadline

    boolean siblingsStillRacing(long deadlineChecks) {
        for (int i = 0; i < siblingAttemptActive.length(); i++) {
            if (checksSlot != null && i == checksSlot) continue;
            if (siblingAttemptActive.get(i) != 0 && siblingChecksProgress.get(i) < deadlineChecks) return true;
        }
        return false;
    }

    boolean deadlineReachedWithoutExtension(long deadlineChecks) {
        if (deadlineExtensionDenied) return true;
        if (checks <= deadlineChecks) return false;
        if (checksSlot == null || siblingChecksProgress == null || siblingAttemptActive == null) {
            deadlineExtensionDenied = true;
            return true;
        }
        long past = checks - deadlineChecks;
        if ((past == 1 || past % CHECKS_PROGRESS_REPORT_INTERVAL == 0) && !siblingsStillRacing(deadlineChecks)) {
            deadlineExtensionDenied = true;
            return true;
        }
        return false;
    }

    // ================================================================== search

    boolean backtrack(long deadlineChecks, boolean released) {
        lastConflict = null;
        if (abandoned) return false;
        if (deadlineReachedWithoutExtension(deadlineChecks)) {
            budgetExhausted = true;
            return false;
        }
        if (periodicCheckpoints()) return false;
        List<Integer> unassigned = new ArrayList<>();
        int assignedCount = 0;
        for (int i = 0; i < assignment.length; i++) {
            if (assignment[i] != null) assignedCount++;
            else if (!excludedSlots.contains(i)) unassigned.add(i);
        }
        if (assignedCount > bestAssignedCount) {
            bestAssignedCount = assignedCount;
            bestAssignment = assignment.clone();
            bestStatLetters = statLetters(assignment);
            if (onNewBest != null) onNewBest.accept(bestAssignment);
        }
        if (unassigned.isEmpty()) return true;
        Set<String> active = activeChallengeWords();
        Map<Integer, Dom> domains = new LinkedHashMap<>();
        for (int i : unassigned) {
            Dom d = domain(i);
            if (d.allIn(usedWords) && !challengeCanFill(i, active)) {
                impossibleThisAttempt.add(i);
                if (!toleratedDry.contains(i)) return fail(drySlotConflict(i));
                continue;
            }
            domains.put(i, d);
        }
        if (domains.isEmpty()) return fail(new HashSet<>());
        Map<Object, OptionsEntry> optionsCache = new HashMap<>();
        List<Integer> selectable = new ArrayList<>(domains.keySet());
        List<Integer> primary;
        if (released) {
            primary = selectable;
        } else {
            primary = new ArrayList<>();
            for (int i : selectable) if (!impossibleThisAttempt.contains(i)) primary.add(i);
        }
        if (primary.isEmpty()) {
            primary = selectable;
            released = true;
        }
        boolean allowBreaking = false;
        Set<Integer> triedSlots = new HashSet<>();
        int descents = 0;
        int maxDescents = MAX_DESCENTS_PER_NODE;
        if (0 < maxDescents && assignedCount - initialAssignedCount < EARLY_DESCENTS_WORD_COUNT) {
            maxDescents = Math.max(maxDescents, EARLY_MAX_DESCENTS_PER_NODE);
        }
        Set<Integer> nodeConflict = new HashSet<>();
        boolean conflictUnknown = false;
        while (true) {
            List<Integer> avail = new ArrayList<>();
            for (int i : primary) if (!triedSlots.contains(i)) avail.add(i);
            if (avail.isEmpty()) {
                if (!released) {
                    primary = selectable;
                    released = true;
                    continue;
                }
                if (!allowBreaking && breakingPermitted) {
                    allowBreaking = true;
                    primary = new ArrayList<>(domains.keySet());
                    triedSlots = new HashSet<>();
                    continue;
                }
                return fail(conflictUnknown ? null : nodeConflict);
            }
            int bestI = selectTargetSlot(avail, domains);
            triedSlots.add(bestI);
            List<String> cands = orderedCandidates(bestI, domains.get(bestI).minus(usedWords));
            Set<String> priSet = Set.of();
            if (!priorityWords.isEmpty()) {
                Set<String> pw = activePriorityWordsFor(slots.get(bestI));
                List<String> pri = new ArrayList<>();
                for (String w : cands) if (pw.contains(w)) pri.add(w);
                if (!pri.isEmpty()) {
                    priSet = new HashSet<>(pri);
                    if (pri.size() != cands.size()) {
                        List<String> merged = new ArrayList<>(pri);
                        for (String w : cands) if (!priSet.contains(w)) merged.add(w);
                        cands = merged;
                    }
                }
            }
            Set<String> challengedSet = Set.of();
            if (!active.isEmpty()) {
                List<String> challenged = new ArrayList<>();
                for (String w : active) if (!usedWords.contains(w) && challengeWordFits(bestI, w)) challenged.add(w);
                if (!challenged.isEmpty()) {
                    challengedSet = new HashSet<>(challenged);
                    List<String> merged = new ArrayList<>(challenged);
                    for (String w : cands) if (!challengedSet.contains(w)) merged.add(w);
                    cands = merged;
                }
            }
            boolean placedAny = false;
            boolean blameableRejection = false;
            Set<Integer> slotConflict = new HashSet<>();
            int[] crossers = crossingSlots[bestI];
            for (String w : cands) {
                checks++;
                if (abandoned) return fail(null);
                if (deadlineReachedWithoutExtension(deadlineChecks)) {
                    budgetExhausted = true;
                    return fail(null);
                }
                if (periodicCheckpoints()) return fail(null);
                assignment[bestI] = w;
                usedWords.add(w);
                boolean crossingBroken = false, crossingStillImpossible = false;
                List<Integer> brokenSlots = new ArrayList<>();
                List<Integer> unblockedCrossers = new ArrayList<>();
                for (int j : crossers) {
                    if (assignment[j] != null || excludedSlots.contains(j)) continue;
                    if (slotIsBlocked(j, usedWords, active, optionsCache, crossers, w)) {
                        if (domains.containsKey(j)) {
                            crossingBroken = true;
                            brokenSlots.add(j);
                            if (!allowBreaking) break;
                        } else {
                            crossingStillImpossible = true;
                            break;
                        }
                    } else {
                        unblockedCrossers.add(j);
                    }
                }
                boolean rejected = crossingStillImpossible || (crossingBroken && !allowBreaking);
                if (rejected && crossingBroken) {
                    blameableRejection = true;
                    Set<Integer> ac = assignedCrossers(bestI);
                    ac.remove(bestI);
                    slotConflict.addAll(ac);
                    for (int j : brokenSlots) {
                        Set<Integer> acj = assignedCrossers(j);
                        acj.remove(bestI);
                        slotConflict.addAll(acj);
                    }
                }
                if (rejected) {
                    if (challengedSet.contains(w)) registerChallengeWordBreak(w);
                    else if (priSet.contains(w)) registerThemeWordBreak(w);
                }
                if (!rejected) {
                    placedAny = true;
                    for (int j : unblockedCrossers) impossibleThisAttempt.discard(j);
                    Map<Integer, Object[]> savedScores = refreshLetterScoresAround(bestI);
                    List<Integer> newlyTolerated = new ArrayList<>();
                    for (int j : brokenSlots) if (!toleratedDry.contains(j)) newlyTolerated.add(j);
                    toleratedDry.addAll(newlyTolerated);
                    if (backtrack(deadlineChecks, released)) return true;
                    Set<Integer> childConflict = lastConflict;
                    toleratedDry.removeAll(newlyTolerated);
                    restoreLetterScores(savedScores);
                    if (!challengedSet.contains(w) && !priSet.contains(w)) descents++;
                    if (childConflict == null) {
                        conflictUnknown = true;
                    } else if (!childConflict.contains(bestI)) {
                        assignment[bestI] = null;
                        usedWords.remove(w);
                        return fail(childConflict);
                    } else {
                        for (int k : childConflict) if (k != bestI) nodeConflict.add(k);
                    }
                }
                assignment[bestI] = null;
                usedWords.remove(w);
                if (0 < maxDescents && maxDescents <= descents) {
                    if (conflictUnknown) return fail(null);
                    Set<Integer> merged = new HashSet<>(nodeConflict);
                    merged.addAll(slotConflict);
                    return fail(merged);
                }
            }
            if (!placedAny) {
                impossibleThisAttempt.add(bestI);
                if (blameableRejection && !breakingPermitted) return fail(slotConflict);
            }
            nodeConflict.addAll(slotConflict);
        }
    }
}
