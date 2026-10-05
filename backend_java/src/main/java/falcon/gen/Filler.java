package falcon.gen;

import falcon.GenerationCancelled;
import falcon.gen.Words.DualIndex;
import falcon.gen.Words.PW;

import java.util.ArrayList;
import java.util.Arrays;
import java.util.BitSet;
import java.util.Collection;
import java.util.Collections;
import java.util.Comparator;
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
    /** Early hardclean (mirrors EARLY_HARDCLEAN_PERCENT): percent (1-100) of the grid's cells allowed in
     * impossible slots (of the record just taken) before a generation attempt hard-cleans its state in place,
     * inside the search (earlyHardclean) — the second chance's own hard clean (Cleanup.cleanBlockedSlots, no
     * black cell touched). The whole backtracking stack is dropped: the search unwinds to solve(), takes the
     * cleaned record back flat as its new root and restarts its backtracking from zero. The attempt is neither
     * failed nor ended, its palier not left. A locked letter the clean erases is unlocked, one it keeps stays locked,
     * and no letter is locked by it. 100 = off. */
    public static final int EARLY_HARDCLEAN_PERCENT = 10;
    /** Repeated word (mirrors MAX_SAME_WORD_PLACEMENTS): once a generation attempt's search places the same word
     * on the same slot more than this many times in a row — no other word placed there in between
     * (lastWordStreak) — that slot is declared impossible and hard-cleaned in place, inside the search
     * (repeatHardclean): Cleanup.cleanBlockedSlots with that slot as its only impossible slot (its own word, the
     * words crossing it and the letters left on it are cleared, no black cell touched), the slot becomes an
     * "emplacement écarté" and a fresh node carries on. Like a backghost, the backtracking stack is kept: every
     * word the clean takes off stays off. The streak restarts from zero. 0 = off. */
    public static final int MAX_SAME_WORD_PLACEMENTS = 1000;
    /** Incremental fill (mirrors INCREMENTAL_FILL_ENABLED), every palier: a node only places a word on a slot
     * holding a still-free cell inside the attention zone, the union of two rectangles anchored at (0, 0)
     * (attentionPool): a horizontal one (the first Rh rows and Ch columns) and a vertical one (the first Rv rows
     * and Cv columns), all four starting at INCREMENTAL_FILL_START_SIZE. Once the node can place nothing more
     * inside the zone, both grow at once (widenAttention): the horizontal one by INCREMENTAL_FILL_COL_STEP columns
     * until it spans the grid's whole width, then by INCREMENTAL_FILL_ROW_STEP rows; the vertical one by
     * INCREMENTAL_FILL_ROW_STEP rows until it spans the grid's whole height, then by INCREMENTAL_FILL_COL_STEP
     * columns; until one of them covers the whole grid. A backtrack recursion parameter, like released, packed
     * as a long (attentionZone). An attempt that starts from locked letters resets the
     * zone to its start size once, the first time a hardclean leaves it no locked letter at all
     * (attentionAfterUnlock). */
    public static final boolean INCREMENTAL_FILL_ENABLED = true;
    public static final int INCREMENTAL_FILL_START_SIZE = 4;
    public static final int INCREMENTAL_FILL_COL_STEP = 2;
    public static final int INCREMENTAL_FILL_ROW_STEP = 2;
    public static final int PALIER_ATTEMPT_DONE_CHECK_INTERVAL = 500;
    public static final int CANDIDATE_SCORE_WINDOW = 100;
    // Of that window, re-sorted by frequency in the freq wordlist, the most frequent words the draw is made among.
    // A node receiving a backjump (a failure passed up through a node that
    // skipped its other candidates) may make only one more descent.
    public static final int MAX_DESCENTS_PER_NODE = 10;
    // Twice the descents a node makes; the whole score window when the descent cap is disabled.
    public static final int CANDIDATE_FREQ_WINDOW =
            MAX_DESCENTS_PER_NODE > 0 ? 2 * MAX_DESCENTS_PER_NODE : CANDIDATE_SCORE_WINDOW;
    public static final int EARLY_DESCENTS_WORD_COUNT = 10;
    public static final int EARLY_MAX_DESCENTS_PER_NODE = 2 * MAX_DESCENTS_PER_NODE;
    // An attempt starting from locked cells (inherited from a previous
    // palier) applies no descent cap at all.
    public static final boolean BACKJUMPING_ENABLED = true;
    // Longest backjump: words placed after the most recent word of a
    // conflict set; a failure beyond that distance is backghosted instead.
    public static final int MAX_BACKJUMP_LEVELS = 5;
    // Backghosts pending on one descent at most; past that a failure
    // backjumps however far. <= 0 disables backghosting.
    public static final int MAX_BACKGHOSTS_PER_DESCENT = 10;
    // Words backtrack places per node (mirrors WORDS_PER_NODE): after the word of the chosen slot, up to
    // WORDS_PER_NODE - 1 more at once (descendGroup), on the slots of that choice's selection window first, then
    // on the node's other selectable slots; a failure below takes them all back off together. <= 1 places a
    // single word per node.
    public static final int WORDS_PER_NODE = 3;
    public static final int MAX_EXCLUDED_SLOTS = 3;
    public static final boolean ALTERNATE_DIRECTION_ENABLED = false;
    // Level 4 of the slot-selection cascade (restrict to slots already
    // carrying a real letter): optional, currently disabled.
    public static final boolean KNOWN_LETTER_LEVEL_ENABLED = false;
    /** Origin {row, col} of level 6's geometric score: the grid's center,
     *  ((rows - 1) / 2, (cols - 1) / 2), half-integer when a dimension is
     *  even (mirrors _slot_selection_origin). */
    public static double[] slotSelectionOrigin(int rows, int cols) {
        return new double[]{(rows - 1) / 2.0, (cols - 1) / 2.0};
    }
    /** Origin {row, col} of level 6's geometric score: the midpoint of the
     *  segment joining the grid's center and the center of the most recent
     *  word the current descent placed and still holds (highest
     *  placementSeq; a word's center is the midpoint of its first and last
     *  cells); while the descent holds no word of its own, the same with
     *  the last word Interactive mode's "Suivant" placed (lastPlacedCells),
     *  else the grid's center itself (mirrors Filler._selection_origin). */
    public double[] selectionOrigin() {
        int[] cells;
        if (placementSeq.isEmpty()) {
            if (lastPlacedCells == null || lastPlacedCells.length == 0) return slotSelectionOrigin(rows, cols);
            cells = lastPlacedCells;
        } else {
            int last = -1;
            long latest = Long.MIN_VALUE;
            for (Map.Entry<Integer, Long> e : placementSeq.entrySet()) {
                if (e.getValue() > latest) { latest = e.getValue(); last = e.getKey(); }
            }
            cells = slots.get(last);
        }
        int minR = Integer.MAX_VALUE, maxR = Integer.MIN_VALUE, minC = Integer.MAX_VALUE, maxC = Integer.MIN_VALUE;
        for (int cell : cells) {
            minR = Math.min(minR, Cells.r(cell)); maxR = Math.max(maxR, Cells.r(cell));
            minC = Math.min(minC, Cells.c(cell)); maxC = Math.max(maxC, Cells.c(cell));
        }
        double[] center = slotSelectionOrigin(rows, cols);
        return new double[]{((minR + maxR) / 2.0 + center[0]) / 2.0, ((minC + maxC) / 2.0 + center[1]) / 2.0};
    }
    public static final int SLOT_SELECTION_WINDOW_SIZE = 10;
    public static final int MOST_CONSTRAINED_START_LENGTH = 4;
    public static final int MOST_CONSTRAINED_MIN_LENGTH = 2;
    public static final double SLOT_SELECTION_REFINE_FRACTION = 0.5;
    public static final double FALLBACK_PHASE_BUDGET_FRACTION = 0.1;
    public static final int LETTER_BIAS_SAMPLE_SIZE = 10;

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

        public List<Integer> toList() {
            return new ArrayList<>(order);
        }

        @Override
        public Iterator<Integer> iterator() {
            return new ArrayList<>(order).iterator();
        }
    }

    public List<int[]> slots;
    public final DualIndex index;
    public final Rng rng;
    public final int rows, cols;
    public final PW priorityWords;
    /** Scrabble dictionary (mirrors Filler.scrabble_words): a slot's candidates belonging to it are tried
     * right after its theme words and before the rest of the dictionary (scrabbleFirst). */
    public PW scrabbleWords = PW.EMPTY;
    public final Set<String> challengeWords;
    final Map<String, Integer> challengeAttemptCounts = new HashMap<>();
    public final Set<String> challengeAbandoned = new HashSet<>();
    public Integer challengeWordBudget;
    final Map<String, Integer> themeAttemptCounts = new HashMap<>();
    public final Set<String> themeAbandoned = new HashSet<>();
    public Integer themeWordBudget;
    public AtomicBoolean batchAbandonedEvent, attemptDoneEvent, cancelEvent;
    public Map<Integer, Character> forcedLetters;
    public Map<Integer, Character> lockedLetters;
    public final Map<Integer, int[][]> letterScoresByDir;
    public final Map<Integer, int[]> letterScores;
    boolean[] slotAcross;
    List<Object> bestStatLetters;
    /** Level 6's geometric window of the last selectTargetSlot call (mirrors last_selection_window). */
    public List<Integer> lastSelectionWindow = new ArrayList<>();
    /** Cells of the last word Interactive mode's "Suivant" placed, set by
     *  Interactive.placeWord: selectionOrigin's fallback before the grid's
     *  center; null everywhere else (mirrors last_placed_cells). */
    public int[] lastPlacedCells;
    /** For slot i and position p: the crossing slot (or -1) and its position there. */
    int[][] crossSlot, crossPos;
    /** cell -> [(slot, pos)...] in insertion order. */
    Map<Integer, int[][]> cellToSlots;
    int[][] crossingSlots;
    /** The pattern `slots` was extracted from, and whether a node may
     * reshape it (mirrors pattern/reshape_enabled/permanent_black_cells). */
    public char[][] pattern;
    public boolean reshapeEnabled;
    public Set<Integer> permanentBlackCells = Set.of();
    /** The slot list and pattern bestAssignment is indexed on. */
    public List<int[]> bestSlots;
    public char[][] bestPattern;
    public boolean abandoned, budgetExhausted, breakingPermitted, interruptedBySibling;
    /** Early hardclean threshold in percent (100 = off, every caller that is not a generation attempt), the
     * cleaned states already produced in this attempt (a repeat switches it off), and the cells
     * Cleanup.cleanBlockedSlots must never clear. */
    public int earlyHardcleanPercent = 100;
    /** Incremental fill (INCREMENTAL_FILL_ENABLED): off unless Fill.tryFill turns it on. */
    public boolean incrementalFill;
    /** Attention zone of the node the search is currently in, and the one bestAssignment was recorded under, as
     * [[Rh, Ch], [Rv, Cv]] (null = the whole grid), published with every preview as attention_size
     * (attentionJson). */
    public volatile List<List<Integer>> attentionSize;
    public List<List<Integer>> bestAttentionSize;
    /** True while the attempt, started from locked letters, has not yet reset its attention zone for losing them
     * all (attentionAfterUnlock). */
    boolean attentionResetPending;
    final Set<String> earlyHardcleanStates = new HashSet<>();
    /** Set by backtrack when a record calls for an early hardclean: every node then unwinds like on an abandon
     * (running its own undo), and solve() restarts the search flat from the record (restartFromRecord). */
    boolean restartPending;
    /** Repeated-word rule (MAX_SAME_WORD_PLACEMENTS): the limit (0 = off, every caller that is not a generation
     * attempt), and the last word the search placed on each slot with its count in a row (slot cells -> word,
     * count). */
    public int sameWordLimit;
    final Map<String, String> lastWord = new HashMap<>();
    final Map<String, Integer> lastWordCount = new HashMap<>();
    public Map<Integer, Character> permanentLockedLetters = Map.of();
    public Set<Integer> toleratedDry = new HashSet<>();
    Set<Integer> lastConflict;
    /** Whether the last failure was passed up by a backjump (mirrors _last_jumped). */
    boolean lastJumped;
    /** Words placed by backtrack still on the grid: slot -> placement sequence number. */
    Map<Integer, Long> placementSeq = new HashMap<>();
    long placementCounter;
    /** Words placed by the search on each slot and how often (slot cells -> word -> count), plus the
     *  per-slot total; keyed by cells so a reshape's renumbering keeps unchanged slots' history. */
    final Map<String, Map<String, Integer>> triedWords = new HashMap<>();
    final Map<String, Integer> triedTotals = new HashMap<>();
    int ghostsInDescent;
    public String[] assignment;
    public Set<Integer> excludedSlots;
    public Set<String> usedWords = new HashSet<>();
    public long checks;
    public RecentSlots impossibleThisAttempt = new RecentSlots(MAX_EXCLUDED_SLOTS);
    public String[] bestAssignment;
    public int bestAssignedCount;
    int initialAssignedCount;
    boolean inherited;
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
                this.letterScores.put(cell, Tally.crossed(copy));
            });
        }
        indexSlots(slots);
        this.bestSlots = slots;
        this.assignment = new String[slots.size()];
        this.excludedSlots = excludedSlots != null ? excludedSlots : new HashSet<>();
        this.bestAssignment = assignment.clone();
        this.onNewBest = onNewBest;
        Arrays.fill(lastCheckpoint, Long.MIN_VALUE);
    }

    /** Sets `slots` and every per-slot lookup derived from it (mirrors _index_slots). */
    void indexSlots(List<int[]> slots) {
        this.slots = slots;
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

    /** Mirrors _record_tried_word: true past sameWordLimit placements of the word on that slot in a row (the
     * streak then restarts from zero and the caller hard-cleans the slot, repeatHardclean). */
    boolean recordTriedWord(int i, String word) {
        String key = Arrays.toString(slots.get(i));
        triedWords.computeIfAbsent(key, k -> new HashMap<>()).merge(word, 1, Integer::sum);
        triedTotals.merge(key, 1, Integer::sum);
        if (sameWordLimit <= 0) return false;
        int n = word.equals(lastWord.get(key)) ? lastWordCount.get(key) + 1 : 1;
        lastWord.put(key, word);
        if (n > sameWordLimit) {
            lastWordCount.put(key, 0);
            return true;
        }
        lastWordCount.put(key, n);
        return false;
    }

    /** Mirrors _descend_group: place up to WORDS_PER_NODE - 1 extra words (extraGroupWords, on the window slots
     * first, then on the pool), count every word of the group, and run a child node — or hard-clean in place the
     * first slot of the group whose word has been placed there too often in a row. On failure the extra words are
     * all taken back off together, and a conflict on an extra slot is charged to i plus the words crossing it. */
    boolean descendGroup(int i, String word, List<Integer> window, List<Integer> pool, long deadlineChecks,
                         boolean released, long attention) {
        List<Object[]> group = new ArrayList<>();
        if (extraGroupWords(window, pool, deadlineChecks, attention, group)) {
            undoGroup(group);
            return fail(null);
        }
        int hit = recordTriedWord(i, word) ? i : -1;
        for (Object[] g : group) {
            if (recordTriedWord((Integer) g[0], (String) g[1]) && hit < 0) hit = (Integer) g[0];
        }
        boolean solved = hit >= 0
                ? repeatHardclean(hit, deadlineChecks, released, attention)
                : backtrack(deadlineChecks, released, attention);
        if (solved) return true;
        Set<Integer> conflict = lastConflict;
        boolean jumped = lastJumped;
        Set<Integer> extras = new HashSet<>();
        for (Object[] g : group) extras.add((Integer) g[0]);
        if (conflict != null) {
            Set<Integer> hits = new HashSet<>(extras);
            hits.retainAll(conflict);
            if (!hits.isEmpty()) {
                Set<Integer> mapped = new HashSet<>(conflict);
                mapped.removeAll(extras);
                mapped.add(i);
                for (int k : hits) mapped.addAll(assignedCrossers(k));
                mapped.removeAll(extras);
                conflict = mapped;
            }
        }
        undoGroup(group);
        lastConflict = conflict;
        lastJumped = jumped;
        return false;
    }

    /** Mirrors _undo_group: the last placed first, letter statistics restored, each word removed unless a
     * backghost or a hardclean below took it off already. */
    @SuppressWarnings("unchecked")
    void undoGroup(List<Object[]> group) {
        for (int n = group.size() - 1; n >= 0; n--) {
            Object[] g = group.get(n);
            int k = (Integer) g[0];
            restoreLetterScores((Map<Integer, Object[]>) g[3]);
            Long current = placementSeq.get(k);
            if (current != null && current == (long) (Long) g[2]) {
                placementSeq.remove(k);
                assignment[k] = null;
                usedWords.remove((String) g[1]);
            }
        }
    }

    /** Mirrors _extra_group_words: fills group with {slot, word, placement seq, saved letter scores}; returns true
     * when the budget, an abandon or a periodic stop signal interrupted it. */
    boolean extraGroupWords(List<Integer> window, List<Integer> pool, long deadlineChecks, long attention,
                            List<Object[]> group) {
        if (WORDS_PER_NODE <= 1) return false;
        Set<Integer> tried = new HashSet<>();
        while (group.size() < WORDS_PER_NODE - 1) {
            Set<String> active = activeChallengeWords();
            Map<Integer, Dom> domains = new LinkedHashMap<>();
            boolean dry = false;
            for (List<Integer> source : List.of(window, pool)) {
                List<Integer> open = new ArrayList<>();
                for (int k : source) {
                    if (!tried.contains(k) && assignment[k] == null && !excludedSlots.contains(k)
                            && !toleratedDry.contains(k)) open.add(k);
                }
                for (int k : attentionPool(open, attention)) {
                    Dom d = domain(k);
                    if (d.allIn(usedWords) && !challengeCanFill(k, active)) {
                        dry = true;
                        break;
                    }
                    domains.put(k, d);
                }
                if (dry || !domains.isEmpty()) break;
            }
            if (dry || domains.isEmpty()) return false;
            int k = selectTargetSlot(new ArrayList<>(domains.keySet()), domains);
            tried.add(k);
            List<String> cands = orderedCandidates(k, domains.get(k).minus(usedWords));
            cands = scrabbleFirst(k, cands);
            if (!priorityWords.isEmpty()) {
                Set<String> pw = activePriorityWordsFor(slots.get(k));
                List<String> pri = new ArrayList<>();
                for (String w : cands) if (pw.contains(w)) pri.add(w);
                if (!pri.isEmpty() && pri.size() != cands.size()) {
                    Set<String> priSet = new HashSet<>(pri);
                    List<String> merged = new ArrayList<>(pri);
                    for (String w : cands) if (!priSet.contains(w)) merged.add(w);
                    cands = merged;
                }
            }
            if (!active.isEmpty()) {
                List<String> challenged = new ArrayList<>();
                for (String w : active) if (!usedWords.contains(w) && challengeWordFits(k, w)) challenged.add(w);
                if (!challenged.isEmpty()) {
                    Set<String> challengedSet = new HashSet<>(challenged);
                    List<String> merged = new ArrayList<>(challenged);
                    for (String w : cands) if (!challengedSet.contains(w)) merged.add(w);
                    cands = merged;
                }
            }
            Map<Object, OptionsEntry> optionsCache = new HashMap<>();
            int[] crossers = crossingSlots[k];
            String placed = null;
            for (String w : cands) {
                checks++;
                if (abandoned || restartPending) return true;
                if (deadlineReachedWithoutExtension(deadlineChecks)) {
                    budgetExhausted = true;
                    return true;
                }
                if (periodicCheckpoints()) return true;
                assignment[k] = w;
                usedWords.add(w);
                List<Integer> unblocked = new ArrayList<>();
                for (int j : crossers) {
                    if (assignment[j] != null || excludedSlots.contains(j)) continue;
                    if (slotIsBlocked(j, usedWords, active, optionsCache, crossers, w)) {
                        unblocked = null;
                        break;
                    }
                    unblocked.add(j);
                }
                if (unblocked != null) {
                    placed = w;
                    for (int j : unblocked) impossibleThisAttempt.discard(j);
                    break;
                }
                assignment[k] = null;
                usedWords.remove(w);
            }
            if (placed == null) return false;
            Map<Integer, Object[]> savedScores = refreshLetterScoresAround(k);
            long seq = placementCounter++;
            placementSeq.put(k, seq);
            group.add(new Object[]{k, placed, seq, savedScores});
        }
        return false;
    }

    /** Mirrors _repeat_hardclean: declare slot i impossible and hard-clean it in place (its own word taken off
     * first, no black cell touched, same locking and orphan-letter rules as the early hardclean). The removed
     * words leave the grid without unwinding any node, like a backghost; the slot becomes an "emplacement écarté"
     * and a fresh node carries on. The letter statistics re-tallied for the removals are restored when that node
     * fails; the removed words stay off. */
    boolean repeatHardclean(int i, long deadlineChecks, boolean released, long attention) {
        String[] work = assignment.clone();
        work[i] = null;
        Set<Integer> cleared = new HashSet<>();
        Object[] cleaned = Cleanup.cleanBlockedSlots(slots, work, List.of(i),
                lockedLetters.isEmpty() ? null : new HashMap<>(lockedLetters), false, index, rng, null, null, null,
                permanentLockedLetters.isEmpty() ? null : permanentLockedLetters, null, null, false, cleared);
        String[] cleanedAssignment = (String[]) cleaned[0];
        List<Integer> removed = new ArrayList<>();
        for (int j = 0; j < assignment.length; j++) {
            if (assignment[j] != null && cleanedAssignment[j] == null) removed.add(j);
        }
        for (int j : removed) {
            usedWords.remove(assignment[j]);
            assignment[j] = null;
            placementSeq.remove(j);
        }
        List<Map<Integer, Object[]>> saved = new ArrayList<>();
        for (int j : removed) saved.add(refreshLetterScoresAround(j));
        if (!cleared.isEmpty()) {
            Map<Integer, Character> kept = new HashMap<>();
            lockedLetters.forEach((cell, ch) -> { if (!cleared.contains(cell)) kept.put(cell, ch); });
            lockedLetters = kept;
        }
        impossibleThisAttempt.add(i);
        attention = attentionAfterUnlock(attention);
        if (backtrack(deadlineChecks, released, attention)) return true;
        for (int k = saved.size() - 1; k >= 0; k--) restoreLetterScores(saved.get(k));
        return false;
    }

    int slotTryCount(int i) {
        return triedTotals.getOrDefault(Arrays.toString(slots.get(i)), 0);
    }

    /** Square root of the sum of the squares of the still-free cells' top frequencies. */
    double slotLetterFrequencyScore(int i) {
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
        return Math.sqrt(total);
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
            // A cell a reshape turned black keeps its old tally but belongs to no slot any more.
            if (fixed.contains(cell) || !cellToSlots.containsKey(cell)) return;
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
                    // Replace this direction's tally only, then re-cross the two.
                    int[][] byDir = letterScoresByDir.get(jcell);
                    byDir = byDir == null ? new int[2][] : new int[][]{byDir[0], byDir[1]};
                    byDir[dir] = Tally.ofLetters(sample, pos);
                    letterScoresByDir.put(jcell, byDir);
                    letterScores.put(jcell, Tally.crossed(byDir));
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

    /** Square root of the sum of the squares of the word's per-cell statistical scores. */
    public double candidateScore(int i, String word) {
        int[] cells = slots.get(i);
        long total = 0;
        for (int p = 0; p < cells.length; p++) {
            if (cellFixedByCrosser(i, p)) continue;
            long v = Tally.get(letterScores.get(cells[p]), word.charAt(p));
            total += v * v;
        }
        return Math.sqrt(total);
    }

    /** {@code cands} (already ordered) stably split into the words of slot i's own Scrabble dictionary first,
     * then the rest; {@code cands} itself when all, or none, belong to it (mirrors Filler.scrabble_first). */
    public List<String> scrabbleFirst(int i, List<String> cands) {
        if (scrabbleWords.isEmpty()) return cands;
        Set<String> words = scrabbleWords.forCells(slots.get(i));
        if (words.isEmpty()) return cands;
        List<String> head = new ArrayList<>();
        List<String> tail = new ArrayList<>();
        for (String w : cands) (words.contains(w) ? head : tail).add(w);
        if (head.isEmpty() || tail.isEmpty()) return cands;
        head.addAll(tail);
        return head;
    }

    /** Shuffle, rank by statistical score divided by (1 + times the word was already placed on this
     * slot), then take each pick from a sliding window of the CANDIDATE_SCORE_WINDOW best remaining words: the
     * window is re-sorted by frequency in the freq wordlist (highest first, 0 for a word absent from it, ties
     * keeping the score order) and the pick is drawn at random among its CANDIDATE_FREQ_WINDOW most frequent. */
    public List<String> orderedCandidates(int i, Collection<String> candsIn) {
        List<String> cands = new ArrayList<>(candsIn);
        rng.shuffle(cands);
        if (letterScores.isEmpty()) return cands;
        int n = cands.size();
        double[] scores = new double[n];
        Integer[] order = new Integer[n];
        Map<String, Integer> tried = triedWords.getOrDefault(Arrays.toString(slots.get(i)), Map.of());
        for (int k = 0; k < n; k++) {
            String w = cands.get(k);
            scores[k] = candidateScore(i, w) / (1 + tried.getOrDefault(w, 0));
            order[k] = k;
        }
        Arrays.sort(order, (a, b) -> Double.compare(scores[b], scores[a]));
        List<String> reordered = new ArrayList<>(n);
        LenIndex li = index.get(slots.get(i));
        double[] dictFreq = new double[n];
        if (li != null) for (int k = 0; k < n; k++) dictFreq[k] = li.dictFreqOf(cands.get(order[k]));
        // window: score ranks of the best-scored words not drawn yet, sorted by (frequency descending, rank).
        Comparator<Integer> byFreq = (a, b) -> {
            int c = Double.compare(dictFreq[b], dictFreq[a]);
            return c != 0 ? c : Integer.compare(a, b);
        };
        List<Integer> window = new ArrayList<>(CANDIDATE_SCORE_WINDOW);
        int next = 0;
        while (next < n || !window.isEmpty()) {
            while (next < n && window.size() < CANDIDATE_SCORE_WINDOW) {
                int at = Collections.binarySearch(window, next, byFreq);
                window.add(-at - 1, next++);
            }
            int idx = rng.randrange(Math.min(CANDIDATE_FREQ_WINDOW, window.size()));
            reordered.add(cands.get(order[window.remove(idx)]));
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
        return fail(conflict, false);
    }

    boolean fail(Set<Integer> conflict, boolean jumped) {
        lastConflict = BACKJUMPING_ENABLED && conflict != null ? new HashSet<>(conflict) : null;
        lastJumped = jumped && lastConflict != null;
        return false;
    }

    /** Slot a backghost should take the word off, or -1 (mirrors _backghost_target). */
    int backghostTarget(Set<Integer> conflict) {
        if (conflict == null || placementSeq.isEmpty() || restartPending
                || ghostsInDescent >= MAX_BACKGHOSTS_PER_DESCENT) return -1;
        int target = -1;
        long targetSeq = -1;
        for (int k : conflict) {
            Long q = placementSeq.get(k);
            if (q != null && q > targetSeq) {
                targetSeq = q;
                target = k;
            }
        }
        if (target < 0) return -1;
        int after = 0;
        for (long q : placementSeq.values()) if (q > targetSeq) after++;
        if (after <= MAX_BACKJUMP_LEVELS) return -1;
        return target;
    }

    /** Attention zone a root node starts with, packed by attentionZone (mirrors _initial_attention); -1 = the
     * whole grid. */
    long initialAttention() {
        if (!incrementalFill) return -1;
        int r = Math.min(INCREMENTAL_FILL_START_SIZE, rows);
        int c = Math.min(INCREMENTAL_FILL_START_SIZE, cols);
        return attentionZone(r, c, r, c);
    }

    /** The attention zone of horizontal rectangle (hr, hc) and vertical rectangle (vr, vc), packed 16 bits each
     * (attentionHr/Hc/Vr/Vc read them back); -1 once one of them, both being anchored at (0, 0), covers the whole
     * grid (mirrors _attention_or_whole). */
    long attentionZone(int hr, int hc, int vr, int vc) {
        if ((hr >= rows && hc >= cols) || (vr >= rows && vc >= cols)) return -1;
        return ((long) hr << 48) | ((long) hc << 32) | ((long) vr << 16) | vc;
    }

    static int attentionHr(long zone) { return (int) (zone >>> 48) & 0xFFFF; }
    static int attentionHc(long zone) { return (int) (zone >>> 32) & 0xFFFF; }
    static int attentionVr(long zone) { return (int) (zone >>> 16) & 0xFFFF; }
    static int attentionVc(long zone) { return (int) zone & 0xFFFF; }

    /** Mirrors _attention_after_unlock: the attention zone a node carries on with after a hardclean that may have
     * unlocked letters (attention otherwise) — the start zone, once per attempt, the first time an attempt
     * started from locked letters is left with none. */
    long attentionAfterUnlock(long attention) {
        if (attentionResetPending && lockedLetters.isEmpty()) {
            attentionResetPending = false;
            return initialAttention();
        }
        return attention;
    }

    /** The attention zone after zone, both rectangles grown at once: the horizontal one by
     * INCREMENTAL_FILL_COL_STEP more columns while it is narrower than the grid, then INCREMENTAL_FILL_ROW_STEP
     * more rows; the vertical one by INCREMENTAL_FILL_ROW_STEP more rows while it is shorter than the grid, then
     * INCREMENTAL_FILL_COL_STEP more columns; -1 once the zone covers the whole grid (mirrors _widen_attention). */
    long widenAttention(long zone) {
        int hr = attentionHr(zone), hc = attentionHc(zone), vr = attentionVr(zone), vc = attentionVc(zone);
        if (hc < cols) hc = Math.min(hc + INCREMENTAL_FILL_COL_STEP, cols);
        else hr = Math.min(hr + INCREMENTAL_FILL_ROW_STEP, rows);
        if (vr < rows) vr = Math.min(vr + INCREMENTAL_FILL_ROW_STEP, rows);
        else vc = Math.min(vc + INCREMENTAL_FILL_COL_STEP, cols);
        return attentionZone(hr, hc, vr, vc);
    }

    /** [[Rh, Ch], [Rv, Cv]] of a packed attention zone, null for -1 (the whole grid): the attention_size JSON
     * shape. */
    static List<List<Integer>> attentionJson(long zone) {
        if (zone < 0) return null;
        return List.of(List.of(attentionHr(zone), attentionHc(zone)), List.of(attentionVr(zone), attentionVc(zone)));
    }

    /** The slots holding a still-free cell (no placed word nor locked letter on it) inside the attention zone
     * zone (rows 0 to Rh - 1 and columns 0 to Ch - 1, or rows 0 to Rv - 1 and columns 0 to Cv - 1); all of them
     * when zone is -1 (mirrors _attention_pool). */
    List<Integer> attentionPool(List<Integer> pool, long zone) {
        if (zone < 0) return new ArrayList<>(pool);
        int hr = attentionHr(zone), hc = attentionHc(zone), vr = attentionVr(zone), vc = attentionVc(zone);
        Set<Integer> known = new HashSet<>(lockedLetters.keySet());
        for (int j = 0; j < assignment.length; j++) {
            if (assignment[j] != null) for (int cell : slots.get(j)) known.add(cell);
        }
        List<Integer> out = new ArrayList<>();
        for (int i : pool) {
            for (int cell : slots.get(i)) {
                int r = Cells.r(cell), c = Cells.c(cell);
                if (((r < hr && c < hc) || (r < vr && c < vc)) && !known.contains(cell)) {
                    out.add(i);
                    break;
                }
            }
        }
        return out;
    }

    /** Report a failure, backghosting first when allowed (mirrors _fail_or_backghost). */
    boolean failOrBackghost(Set<Integer> conflict, long deadlineChecks, boolean released, long attention) {
        while (true) {
            int target = backghostTarget(conflict);
            if (target < 0) return fail(conflict);
            String w = assignment[target];
            placementSeq.remove(target);
            assignment[target] = null;
            usedWords.remove(w);
            Map<Integer, Object[]> savedScores = refreshLetterScoresAround(target);
            ghostsInDescent++;
            boolean solved = backtrack(deadlineChecks, released, attention);
            ghostsInDescent--;
            if (solved) return true;
            Set<Integer> retryConflict = lastConflict;
            restoreLetterScores(savedScores);
            if (retryConflict == null) return fail(null);
            Set<Integer> merged = new HashSet<>(conflict);
            merged.remove(target);
            merged.addAll(retryConflict);
            conflict = merged;
        }
    }

    // ================================================================== in-search reshapes

    /** One way a node can turn its chosen slot into a slot of a family
     * word's own length (mirrors _Reshape). */
    static final class Reshape {
        final String word, family;
        final Map<Integer, Character> changes;
        final char[][] pattern;
        final List<int[]> slots;
        final int target;
        final Map<Integer, Integer> forward;

        Reshape(String word, String family, Map<Integer, Character> changes, char[][] pattern, List<int[]> slots,
                int target, Map<Integer, Integer> forward) {
            this.word = word;
            this.family = family;
            this.changes = changes;
            this.pattern = pattern;
            this.slots = slots;
            this.target = target;
            this.forward = forward;
        }
    }

    Map<Integer, Character> knownCells() {
        Map<Integer, Character> known = new HashMap<>(lockedLetters);
        for (int i = 0; i < assignment.length; i++) {
            String w = assignment[i];
            if (w == null) continue;
            int[] cells = slots.get(i);
            for (int p = 0; p < cells.length; p++) known.put(cells[p], w.charAt(p));
        }
        return known;
    }

    private boolean inside(int r, int c) {
        return r >= 0 && r < rows && c >= 0 && c < cols;
    }

    /** Returns {span int[], changes Map} pairs (mirrors _reshape_geometries). */
    List<Object[]> reshapeGeometries(int i, String word) {
        int[] cells = slots.get(i);
        int length = cells.length, k = word.length();
        List<Object[]> out = new ArrayList<>();
        if (k < length) {
            Map<Integer, Character> c1 = new LinkedHashMap<>();
            c1.put(cells[k], Grids.BLACK);
            out.add(new Object[]{Arrays.copyOfRange(cells, 0, k), c1});
            Map<Integer, Character> c2 = new LinkedHashMap<>();
            c2.put(cells[length - k - 1], Grids.BLACK);
            out.add(new Object[]{Arrays.copyOfRange(cells, length - k, length), c2});
            return out;
        }
        int dr = Cells.r(cells[1]) - Cells.r(cells[0]), dc = Cells.c(cells[1]) - Cells.c(cells[0]);
        for (int sign : new int[]{1, -1}) {
            int edge = sign == 1 ? cells[length - 1] : cells[0];
            int sr = sign * dr, sc = sign * dc;
            int br = Cells.r(edge) + sr, bc = Cells.c(edge) + sc;
            if (!inside(br, bc) || pattern[br][bc] != Grids.BLACK || permanentBlackCells.contains(Cells.of(br, bc))) continue;
            List<Integer> ext = new ArrayList<>();
            ext.add(Cells.of(br, bc));
            int nr = br + sr, nc = bc + sc;
            while (ext.size() < k - length) {
                if (!inside(nr, nc) || pattern[nr][nc] == Grids.BLACK) break;
                ext.add(Cells.of(nr, nc));
                nr += sr;
                nc += sc;
            }
            if (ext.size() < k - length) continue;
            Map<Integer, Character> changes = new LinkedHashMap<>();
            changes.put(Cells.of(br, bc), Grids.WHITE);
            if (inside(nr, nc) && pattern[nr][nc] != Grids.BLACK) changes.put(Cells.of(nr, nc), Grids.BLACK);
            int[] span = new int[k];
            if (sign == 1) {
                System.arraycopy(cells, 0, span, 0, length);
                for (int e = 0; e < ext.size(); e++) span[length + e] = ext.get(e);
            } else {
                for (int e = 0; e < ext.size(); e++) span[ext.size() - 1 - e] = ext.get(e);
                System.arraycopy(cells, 0, span, ext.size(), length);
            }
            out.add(new Object[]{span, changes});
        }
        return out;
    }

    /** Mirrors _reshape_options. */
    @SuppressWarnings("unchecked")
    List<Reshape> reshapeOptions(int i, String word, String family, Map<Integer, Character> known) {
        List<Reshape> options = new ArrayList<>();
        for (Object[] g : reshapeGeometries(i, word)) {
            int[] span = (int[]) g[0];
            Map<Integer, Character> changes = (Map<Integer, Character>) g[1];
            boolean ok = true;
            for (int p = 0; p < span.length && ok; p++) {
                Character ch = known.get(span[p]);
                if (ch != null && ch != word.charAt(p)) ok = false;
            }
            if (!ok) continue;
            for (Map.Entry<Integer, Character> e : changes.entrySet()) {
                if (e.getValue() == Grids.BLACK && known.containsKey(e.getKey())) ok = false;
            }
            if (!ok) continue;
            char[][] newPattern = Grids.copy(pattern);
            changes.forEach((cell, v) -> newPattern[Cells.r(cell)][Cells.c(cell)] = v);
            if (!Grids.isStructurallyValid(newPattern, rows, cols, 1)) continue;
            List<int[]> newSlots = Grids.extractSlots(newPattern, rows, cols);
            Map<Cells.Key, Integer> newIndex = new HashMap<>();
            for (int j = 0; j < newSlots.size(); j++) newIndex.put(Cells.key(newSlots.get(j)), j);
            Integer target = newIndex.get(Cells.key(span));
            if (target == null) continue;
            Map<Integer, Integer> forward = new HashMap<>();
            for (int old = 0; old < slots.size() && ok; old++) {
                Integer j = newIndex.get(Cells.key(slots.get(old)));
                if (j != null) forward.put(old, j);
                else if (assignment[old] != null) ok = false;
            }
            if (ok) options.add(new Reshape(word, family, changes, newPattern, newSlots, target, forward));
        }
        return options;
    }

    /** Mirrors _reshape_candidates. */
    List<Reshape> reshapeCandidates(int i, String family, Set<String> active) {
        List<Reshape> out = new ArrayList<>();
        Collection<String> source;
        if (family.equals("challenge")) {
            source = active;
        } else {
            if (!Cleanup.themeReshapeAllowed(Fill.placedThemeWords(slots, assignment, priorityWords))) return out;
            source = activePriorityWordsFor(slots.get(i));
        }
        int length = slots.get(i).length;
        List<String> pool = new ArrayList<>();
        for (String w : source) if (w.length() != length && w.length() >= 2 && !usedWords.contains(w)) pool.add(w);
        if (pool.isEmpty()) return out;
        Map<Integer, Character> known = knownCells();
        Map<Integer, List<int[]>> openByLength = new HashMap<>();
        for (int j = 0; j < slots.size(); j++) {
            if (assignment[j] == null && !excludedSlots.contains(j)) {
                openByLength.computeIfAbsent(slots.get(j).length, x -> new ArrayList<>()).add(slots.get(j));
            }
        }
        List<String> kept = new ArrayList<>();
        for (String w : pool) {
            boolean fits = false;
            for (int[] cells : openByLength.getOrDefault(w.length(), List.of())) {
                boolean ok = true;
                for (int p = 0; p < cells.length && ok; p++) {
                    Character ch = known.get(cells[p]);
                    if (ch != null && ch != w.charAt(p)) ok = false;
                }
                if (ok) {
                    fits = true;
                    break;
                }
            }
            if (!fits) kept.add(w);
        }
        java.util.Collections.sort(kept);
        rng.shuffle(kept);
        for (String w : kept.subList(0, Math.min(Cleanup.RESHAPE_WORDS_PER_NODE, kept.size()))) {
            out.addAll(reshapeOptions(i, w, family, known));
        }
        return out;
    }

    /** `cands` with each family's reshapes right after its own words, built
     * lazily (mirrors _with_reshape_candidates). */
    Iterator<Object> withReshapeCandidates(int i, List<String> cands, Set<String> challengedSet, Set<String> priSet,
                                           Set<String> active) {
        int nChallenge = 0;
        while (nChallenge < cands.size() && challengedSet.contains(cands.get(nChallenge))) nChallenge++;
        int nTheme = nChallenge;
        while (nTheme < cands.size() && priSet.contains(cands.get(nTheme))) nTheme++;
        final int nc = nChallenge, nt = nTheme;
        return new Iterator<Object>() {
            int stage = 0;
            Iterator<?> current = cands.subList(0, nc).iterator();

            @Override
            public boolean hasNext() {
                while (!current.hasNext()) {
                    stage++;
                    if (stage == 1) current = active.isEmpty() ? List.of().iterator()
                            : reshapeCandidates(i, "challenge", active).iterator();
                    else if (stage == 2) current = cands.subList(nc, nt).iterator();
                    else if (stage == 3) current = priorityWords.isEmpty() ? List.of().iterator()
                            : reshapeCandidates(i, "theme", active).iterator();
                    else if (stage == 4) current = cands.subList(nt, cands.size()).iterator();
                    else return false;
                }
                return true;
            }

            @Override
            public Object next() {
                if (!hasNext()) throw new java.util.NoSuchElementException();
                return current.next();
            }
        };
    }

    /** Mirrors _apply_reshape; returns the saved state. */
    Object[] applyReshape(Reshape option) {
        Object[] saved = {slots, pattern, assignment, toleratedDry, placementSeq, impossibleThisAttempt};
        Map<Integer, Integer> forward = option.forward;
        String[] newAssignment = new String[option.slots.size()];
        forward.forEach((old, j) -> newAssignment[j] = assignment[old]);
        RecentSlots recent = new RecentSlots(MAX_EXCLUDED_SLOTS);
        for (int old : impossibleThisAttempt) if (forward.containsKey(old)) recent.add(forward.get(old));
        Set<Integer> tolerated = new HashSet<>();
        for (int j : toleratedDry) if (forward.containsKey(j)) tolerated.add(forward.get(j));
        Map<Integer, Long> seq = new HashMap<>();
        placementSeq.forEach((j, q) -> {
            if (forward.containsKey(j)) seq.put(forward.get(j), q);
        });
        indexSlots(option.slots);
        pattern = option.pattern;
        assignment = newAssignment;
        toleratedDry = tolerated;
        placementSeq = seq;
        impossibleThisAttempt = recent;
        return saved;
    }

    /** Mirrors _undo_reshape; returns the new -> original index map. */
    @SuppressWarnings("unchecked")
    Map<Integer, Integer> undoReshape(Reshape option, Object[] saved, int i) {
        Map<Integer, Integer> back = new HashMap<>();
        option.forward.forEach((old, j) -> back.put(j, old));
        back.put(option.target, i);
        RecentSlots recent = new RecentSlots(MAX_EXCLUDED_SLOTS);
        for (int old : (RecentSlots) saved[5]) if (!option.forward.containsKey(old)) recent.add(old);
        for (int j : impossibleThisAttempt) if (back.containsKey(j)) recent.add(back.get(j));
        String[] oldAssignment = (String[]) saved[2];
        Map<Integer, Long> oldSeq = (Map<Integer, Long>) saved[4];
        // A word on the grid before the change that is no longer there was
        // taken off by a backghost: it stays off.
        for (int old = 0; old < oldAssignment.length; old++) {
            if (oldAssignment[old] == null) continue;
            Integer j = option.forward.get(old);
            if (j == null || assignment[j] == null) {
                oldSeq.remove(old);
                oldAssignment[old] = null;
            }
        }
        indexSlots((List<int[]>) saved[0]);
        pattern = (char[][]) saved[1];
        assignment = oldAssignment;
        toleratedDry = (Set<Integer>) saved[3];
        placementSeq = oldSeq;
        impossibleThisAttempt = recent;
        return back;
    }

    /** Mirrors _try_reshape: returns {outcome, conflict-or-null, blame-or-null}. */
    Object[] tryReshape(Reshape option, int i, Map<Integer, Dom> domains, Set<String> active, boolean allowBreaking,
                        long deadlineChecks, boolean released, long attention, List<Integer> window,
                        List<Integer> pool) {
        Object[] saved = applyReshape(option);
        int t = option.target;
        String w = option.word;
        assignment[t] = w;
        usedWords.add(w);
        Map<Integer, Integer> inverse = new HashMap<>();
        option.forward.forEach((old, j) -> inverse.put(j, old));
        TreeSet<Integer> toCheck = new TreeSet<>();
        for (int j : crossingSlots[t]) toCheck.add(j);
        for (int j = 0; j < slots.size(); j++) if (!inverse.containsKey(j) && j != t) toCheck.add(j);
        List<Integer> broken = new ArrayList<>(), unblocked = new ArrayList<>();
        boolean stillImpossible = false;
        for (int j : toCheck) {
            if (assignment[j] != null || excludedSlots.contains(j)) continue;
            if (slotIsBlocked(j, usedWords, active, null, null, null)) {
                Integer oldJ = inverse.get(j);
                if (oldJ == null || domains.containsKey(oldJ)) {
                    broken.add(j);
                    if (!allowBreaking) break;
                } else {
                    stillImpossible = true;
                    break;
                }
            } else {
                unblocked.add(j);
            }
        }
        if (stillImpossible || (!broken.isEmpty() && !allowBreaking)) {
            Set<Integer> blame = null;
            if (!broken.isEmpty()) {
                blame = new HashSet<>();
                List<Integer> blamed = new ArrayList<>();
                blamed.add(t);
                blamed.addAll(broken);
                for (int j : blamed) for (int k : assignedCrossers(j)) if (inverse.containsKey(k)) blame.add(inverse.get(k));
                blame.remove(i);
            }
            assignment[t] = null;
            usedWords.remove(w);
            undoReshape(option, saved, i);
            if (option.family.equals("challenge")) registerChallengeWordBreak(w);
            else registerThemeWordBreak(w);
            return new Object[]{"rejected", null, blame};
        }
        for (int j : unblocked) impossibleThisAttempt.discard(j);
        Map<Integer, Object[]> savedScores = refreshLetterScoresAround(t);
        List<Integer> newlyTolerated = new ArrayList<>();
        for (int j : broken) if (!toleratedDry.contains(j)) newlyTolerated.add(j);
        toleratedDry.addAll(newlyTolerated);
        placementSeq.put(t, placementCounter++);
        List<Integer> windowT = new ArrayList<>(), poolT = new ArrayList<>();
        for (int j : window) if (option.forward.containsKey(j)) windowT.add(option.forward.get(j));
        for (int j : pool) if (option.forward.containsKey(j)) poolT.add(option.forward.get(j));
        if (descendGroup(t, w, windowT, poolT, deadlineChecks, released, attention)) {
            return new Object[]{"success", null, null};
        }
        Set<Integer> child = lastConflict;
        toleratedDry.removeAll(newlyTolerated);
        restoreLetterScores(savedScores);
        placementSeq.remove(t);
        assignment[t] = null;
        usedWords.remove(w);
        Map<Integer, Integer> back = undoReshape(option, saved, i);
        if (child != null) {
            Set<Integer> mapped = new HashSet<>();
            for (int j : child) {
                Integer b = back.get(j);
                if (b == null) {
                    mapped = null;
                    break;
                }
                mapped.add(b);
            }
            child = mapped;
        }
        return new Object[]{"failed", child, null};
    }

    /** Mirrors adopt_best_structure. */
    public void adoptBestStructure() {
        if (bestSlots == slots) return;
        Map<Cells.Key, Integer> byCells = new HashMap<>();
        for (int j = 0; j < bestSlots.size(); j++) byCells.put(Cells.key(bestSlots.get(j)), j);
        RecentSlots recent = new RecentSlots(MAX_EXCLUDED_SLOTS);
        for (int j : impossibleThisAttempt) {
            Integer k = byCells.get(Cells.key(slots.get(j)));
            if (k != null) recent.add(k);
        }
        indexSlots(bestSlots);
        pattern = bestPattern;
        assignment = bestAssignment.clone();
        impossibleThisAttempt = recent;
        toleratedDry = new HashSet<>();
        placementSeq = new HashMap<>();
    }

    public boolean solve(long deadlineChecks) {
        if (!challengeWords.isEmpty()) challengeWordBudget = (int) Math.max(1, Math.rint(FALLBACK_PHASE_BUDGET_FRACTION * deadlineChecks));
        if (!priorityWords.isEmpty()) themeWordBudget = (int) Math.max(1, Math.rint(FALLBACK_PHASE_BUDGET_FRACTION * deadlineChecks));
        // An early hardclean ends both passes early (restartPending): the record that called for it is taken
        // back flat (restartFromRecord) and the loop starts over from it as a new root, with no backtracking
        // history. The attempt's descent caps (inherited, initialAssignedCount) are those of its first start.
        int count = 0;
        for (String a : assignment) if (a != null) count++;
        initialAssignedCount = count;
        inherited = !lockedLetters.isEmpty() || count > 0;
        attentionResetPending = incrementalFill && !lockedLetters.isEmpty();
        while (true) {
            // Early hardclean on the state the search starts from (no record is taken until a word is added to
            // it). Nothing to restore: no node is running yet. Every root starts at the attention zone's start
            // size anyway, so a clean leaving no locked letter only uses up the attempt's one zone reset.
            if (earlyHardcleanDue()) {
                earlyHardclean();
                attentionAfterUnlock(-1);
            }
            toleratedDry = dryOpenSlots();
            placementSeq = new HashMap<>();
            ghostsInDescent = 0;
            breakingPermitted = false;
            if (backtrack(deadlineChecks, false, initialAttention())) return true;
            if (restartPending) {
                restartFromRecord();
                continue;
            }
            if (abandoned || budgetExhausted) return false;
            breakingPermitted = true;
            if (backtrack(deadlineChecks, false, initialAttention())) return true;
            if (restartPending) {
                restartFromRecord();
                continue;
            }
            return false;
        }
    }

    /** Mirrors _restart_from_record: take bestAssignment back flat as the search's new root, once an early
     * hardclean has unwound the whole backtracking stack — its slot list and pattern (adoptBestStructure), its
     * words, and letter statistics re-sampled around each of them (the unwinding restored those of the
     * previous root). Its words become part of the root: only a later early hardclean can take them off. */
    void restartFromRecord() {
        restartPending = false;
        adoptBestStructure();
        assignment = bestAssignment.clone();
        usedWords = usedOf(assignment);
        for (int i = 0; i < assignment.length; i++) if (assignment[i] != null) refreshLetterScoresAround(i);
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
            for (String u : used) if (u.length() == cells.length && idx.contains(u)) usedIn.add(u);
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
                if (u.length() == cells.length && idx.contains(u)) {
                    for (int p = 0; p < u.length(); p++) counts[p][Alpha.id(u.charAt(p))]--;
                }
            }
            fullMembers = idx.ids.keySet();
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
    /** True when the cells of the impossible slots of bestAssignment make up at least earlyHardcleanPercent of
     * the grid's cells (never with no impossible cell at all, nor when the threshold is 100). */
    private boolean earlyHardcleanDue() {
        if (earlyHardcleanPercent >= 100 || pattern == null || pattern.length == 0) return false;
        long total = (long) pattern.length * pattern[0].length;
        Set<Integer> cells = new HashSet<>();
        for (int i : impossibleZoneSlots()) for (int c : slots.get(i)) cells.add(c);
        return !cells.isEmpty() && 100L * cells.size() >= (long) earlyHardcleanPercent * total;
    }

    /** Mirrors _early_hardclean: hard-clean the current state in place, on the impossible slots of
     * bestAssignment (the current assignment whenever this runs), no black cell touched. It runs at the root
     * of a search (solve(), after any restartFromRecord), so no node is running: every word the clean removes
     * simply disappears from the grid. A locked letter the clean erases is unlocked, one it keeps stays
     * locked, nothing is locked by it; a kept letter no remaining word or locked letter carries (an orphan
     * letter) is erased. The record restarts from the cleaned state, published like any
     * record; a cleaned state already produced in this attempt switches the early hardclean off. */
    void earlyHardclean() {
        Set<Integer> cleared = new HashSet<>();
        Object[] cleaned = Cleanup.cleanBlockedSlots(slots, assignment.clone(), impossibleZoneSlots(),
                lockedLetters.isEmpty() ? null : new HashMap<>(lockedLetters), false, index, rng, null, null, null,
                permanentLockedLetters.isEmpty() ? null : permanentLockedLetters, null, null, false, cleared);
        String[] cleanedAssignment = (String[]) cleaned[0];
        List<Integer> removed = new ArrayList<>();
        for (int i = 0; i < assignment.length; i++) {
            String w = assignment[i];
            if (w != null && cleanedAssignment[i] == null) {
                assignment[i] = null;
                usedWords.remove(w);
                placementSeq.remove(i);
                removed.add(i);
            }
        }
        for (int i : removed) refreshLetterScoresAround(i);
        if (!cleared.isEmpty()) {
            Map<Integer, Character> kept = new HashMap<>();
            lockedLetters.forEach((cell, ch) -> { if (!cleared.contains(cell)) kept.put(cell, ch); });
            lockedLetters = kept;
        }
        int count = 0;
        for (String a : assignment) if (a != null) count++;
        bestAssignedCount = count;
        bestAssignment = assignment.clone();
        bestSlots = slots;
        bestPattern = pattern;
        bestStatLetters = statLetters(assignment);
        Map<Integer, Character> letters = new java.util.TreeMap<>(knownCells());
        if (!earlyHardcleanStates.add(Grids.key(pattern) + "|" + letters)) earlyHardcleanPercent = 100;
        if (onNewBest != null) onNewBest.accept(bestAssignment);
    }

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
        return selectTargetSlot(unassigned, domains, true, true);
    }

    /** challengeLevel/themeLevel switch cascade levels 2 and 5 (mirrors _select_target_slot's keywords). */
    public int selectTargetSlot(List<Integer> unassigned, Map<Integer, Dom> domains,
                                boolean challengeLevel, boolean themeLevel) {
        List<Integer> directionPool;
        if (ALTERNATE_DIRECTION_ENABLED) {
            List<Integer> a = new ArrayList<>(), d = new ArrayList<>();
            for (int i : unassigned) (slotAcross[i] ? a : d).add(i);
            if (!a.isEmpty() && !d.isEmpty()) directionPool = rng.randrange(a.size() + d.size()) < a.size() ? a : d;
            else directionPool = a.isEmpty() ? d : a;
        } else {
            directionPool = new ArrayList<>(unassigned);
        }
        Set<String> active = challengeLevel ? activeChallengeWords() : Set.of();
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
        if (KNOWN_LETTER_LEVEL_ENABLED) {
            List<Integer> nonBlank = new ArrayList<>();
            for (int i : pool) if (hasKnownLetter(i)) nonBlank.add(i);
            if (!nonBlank.isEmpty()) pool = nonBlank;
        }
        if (themeLevel && !priorityWords.isEmpty()) {
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
        double[] origin = selectionOrigin();
        double cr = origin[0], cc = origin[1];
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
        lastSelectionWindow = window;
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
        Map<Integer, Double> freq = new HashMap<>();
        for (int i : refined) freq.put(i, slotLetterFrequencyScore(i) / (1 + slotTryCount(i)));
        refined.sort((a, b) -> Double.compare(freq.get(b), freq.get(a)));
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

    /** attention: the attention zone (INCREMENTAL_FILL_ENABLED), packed by attentionZone, -1 for the whole
     * grid. */
    boolean backtrack(long deadlineChecks, boolean released, long attention) {
        lastConflict = null;
        lastJumped = false;
        final boolean entryReleased = released;
        final long entryAttention = attention;
        attentionSize = attentionJson(attention);
        if (abandoned || restartPending) return false;
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
            bestSlots = slots;
            bestPattern = pattern;
            bestStatLetters = statLetters(assignment);
            bestAttentionSize = attentionJson(attention);
            if (onNewBest != null) onNewBest.accept(bestAssignment);
            // Early hardclean: checked right as the record is taken, the only moment bestAssignment is the
            // current assignment, on the current slots and pattern. Every node unwinds (restartPending) and
            // solve() restarts flat from this record, cleaned.
            if (earlyHardcleanDue()) {
                restartPending = true;
                return fail(null);
            }
        }
        if (unassigned.isEmpty()) return true;
        Set<String> active = activeChallengeWords();
        Map<Integer, Dom> domains = new LinkedHashMap<>();
        for (int i : unassigned) {
            Dom d = domain(i);
            if (d.allIn(usedWords) && !challengeCanFill(i, active)) {
                impossibleThisAttempt.add(i);
                if (!toleratedDry.contains(i)) {
                    return failOrBackghost(drySlotConflict(i), deadlineChecks, released, entryAttention);
                }
                continue;
            }
            domains.put(i, d);
        }
        if (domains.isEmpty()) return fail(new HashSet<>());
        Map<Object, OptionsEntry> optionsCache = new HashMap<>();
        List<Integer> selectable = new ArrayList<>(domains.keySet());
        // Incremental fill: the stages run inside the attention zone first (zonePool); once they have placed
        // nothing there, the zone grows and the node goes back to its first stage on the larger pool. A zone with
        // nothing left to fill grows at once.
        List<Integer> zonePool = attentionPool(selectable, attention);
        while (zonePool.isEmpty() && attention >= 0) {
            attention = widenAttention(attention);
            zonePool = attentionPool(selectable, attention);
        }
        attentionSize = attentionJson(attention);
        List<Integer> primary;
        if (released) {
            primary = zonePool;
        } else {
            primary = new ArrayList<>();
            for (int i : zonePool) if (!impossibleThisAttempt.contains(i)) primary.add(i);
        }
        if (primary.isEmpty()) {
            primary = zonePool;
            released = true;
        }
        boolean allowBreaking = false;
        Set<Integer> triedSlots = new HashSet<>();
        int descents = 0;
        int maxDescents = inherited ? 0 : MAX_DESCENTS_PER_NODE;
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
                    primary = zonePool;
                    released = true;
                    continue;
                }
                if (attention >= 0) {
                    // Nothing more can be placed inside the attention zone: widen it and start again from stage 1
                    // on the larger pool (slots already tried here stay tried).
                    attention = widenAttention(attention);
                    zonePool = attentionPool(selectable, attention);
                    attentionSize = attentionJson(attention);
                    released = entryReleased;
                    if (released) {
                        primary = zonePool;
                    } else {
                        primary = new ArrayList<>();
                        for (int i : zonePool) if (!impossibleThisAttempt.contains(i)) primary.add(i);
                    }
                    continue;
                }
                if (!allowBreaking && breakingPermitted) {
                    allowBreaking = true;
                    primary = new ArrayList<>(domains.keySet());
                    triedSlots = new HashSet<>();
                    continue;
                }
                return failOrBackghost(conflictUnknown ? null : nodeConflict, deadlineChecks, entryReleased,
                        entryAttention);
            }
            int bestI = selectTargetSlot(avail, domains);
            // The "emplacements candidats" the extra words of this node's group are placed on first.
            List<Integer> window = lastSelectionWindow;
            List<Integer> pool = primary;
            triedSlots.add(bestI);
            List<String> cands = orderedCandidates(bestI, domains.get(bestI).minus(usedWords));
            // Scrabble family: ahead of the rest of the general dictionary; the theme block and the "Mots Défi"
            // below are then pulled ahead of both.
            cands = scrabbleFirst(bestI, cands);
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
            // Each family's reshape candidates right after that family's own words.
            Iterator<?> stream = reshapeEnabled
                    ? withReshapeCandidates(bestI, cands, challengedSet, priSet, active)
                    : cands.iterator();
            while (stream.hasNext()) {
                Object candidate = stream.next();
                checks++;
                if (abandoned || restartPending) return fail(null);
                if (deadlineReachedWithoutExtension(deadlineChecks)) {
                    budgetExhausted = true;
                    return fail(null);
                }
                if (periodicCheckpoints()) return fail(null);
                if (candidate instanceof Reshape rs) {
                    // Its black-cell change is always undone before this
                    // returns, unless it succeeded; counts as a descent.
                    Object[] out = tryReshape(rs, bestI, domains, active, allowBreaking, deadlineChecks, released,
                            attention, window, pool);
                    String outcome = (String) out[0];
                    if (outcome.equals("success")) return true;
                    attentionSize = attentionJson(attention);
                    if (outcome.equals("rejected")) {
                        @SuppressWarnings("unchecked")
                        Set<Integer> blame = (Set<Integer>) out[2];
                        if (blame != null) {
                            blameableRejection = true;
                            slotConflict.addAll(blame);
                        }
                        continue;
                    }
                    boolean jumpedIn = lastJumped;
                    placedAny = true;
                    descents++;
                    @SuppressWarnings("unchecked")
                    Set<Integer> childConflict = (Set<Integer>) out[1];
                    if (childConflict == null) {
                        conflictUnknown = true;
                    } else if (!childConflict.contains(bestI)) {
                        return fail(childConflict, true);
                    } else {
                        for (int k : childConflict) if (k != bestI) nodeConflict.add(k);
                        // A backjump landed here: one more descent only.
                        if (jumpedIn && 0 < maxDescents) maxDescents = Math.min(maxDescents, descents + 1);
                    }
                    if (0 < maxDescents && maxDescents <= descents) {
                        if (conflictUnknown) return failOrBackghost(null, deadlineChecks, entryReleased, entryAttention);
                        Set<Integer> merged = new HashSet<>(nodeConflict);
                        merged.addAll(slotConflict);
                        return failOrBackghost(merged, deadlineChecks, entryReleased, entryAttention);
                    }
                    continue;
                }
                String w = (String) candidate;
                assignment[bestI] = w;
                usedWords.add(w);
                boolean owned = true;
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
                    long seq = placementCounter++;
                    placementSeq.put(bestI, seq);
                    if (descendGroup(bestI, w, window, pool, deadlineChecks, released, attention)) return true;
                    attentionSize = attentionJson(attention);
                    Set<Integer> childConflict = lastConflict;
                    boolean jumpedIn = lastJumped;
                    toleratedDry.removeAll(newlyTolerated);
                    restoreLetterScores(savedScores);
                    Long current = placementSeq.get(bestI);
                    owned = current != null && current == seq;
                    if (owned) placementSeq.remove(bestI);
                    if (!challengedSet.contains(w) && !priSet.contains(w)) descents++;
                    if (childConflict == null) {
                        conflictUnknown = true;
                    } else if (!childConflict.contains(bestI)) {
                        if (owned) {
                            assignment[bestI] = null;
                            usedWords.remove(w);
                        }
                        return fail(childConflict, true);
                    } else {
                        for (int k : childConflict) if (k != bestI) nodeConflict.add(k);
                        // A backjump landed here: one more descent only.
                        if (jumpedIn && 0 < maxDescents) maxDescents = Math.min(maxDescents, descents + 1);
                    }
                }
                if (owned) {
                    assignment[bestI] = null;
                    usedWords.remove(w);
                }
                if (0 < maxDescents && maxDescents <= descents) {
                    if (conflictUnknown) return failOrBackghost(null, deadlineChecks, entryReleased, entryAttention);
                    Set<Integer> merged = new HashSet<>(nodeConflict);
                    merged.addAll(slotConflict);
                    return failOrBackghost(merged, deadlineChecks, entryReleased, entryAttention);
                }
            }
            if (!placedAny) {
                impossibleThisAttempt.add(bestI);
                if (blameableRejection && !breakingPermitted) {
                    return failOrBackghost(slotConflict, deadlineChecks, entryReleased, entryAttention);
                }
            }
            nodeConflict.addAll(slotConflict);
        }
    }
}
