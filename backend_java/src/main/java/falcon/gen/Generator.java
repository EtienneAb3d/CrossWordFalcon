package falcon.gen;

import falcon.GenerationCancelled;
import falcon.GenerationPaused;
import falcon.GlossLookup;
import falcon.Json;
import falcon.gen.Words.DualIndex;
import falcon.gen.Words.LengthSets;
import falcon.gen.Words.PW;

import java.io.IOException;
import java.util.ArrayList;
import java.util.Arrays;
import java.util.HashMap;
import java.util.HashSet;
import java.util.LinkedHashMap;
import java.util.LinkedHashSet;
import java.util.List;
import java.util.Map;
import java.util.Objects;
import java.util.Set;
import java.util.TreeMap;
import java.util.TreeSet;
import java.util.concurrent.BlockingQueue;
import java.util.concurrent.ConcurrentHashMap;
import java.util.concurrent.ExecutionException;
import java.util.concurrent.ExecutorCompletionService;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;
import java.util.concurrent.Future;
import java.util.concurrent.LinkedBlockingQueue;
import java.util.concurrent.ThreadFactory;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.atomic.AtomicBoolean;
import java.util.concurrent.atomic.AtomicIntegerArray;
import java.util.concurrent.atomic.AtomicLongArray;
import java.util.function.BooleanSupplier;
import java.util.function.Consumer;

import static falcon.gen.Grids.BLACK;
import static falcon.gen.Grids.WHITE;

/** The palier loop (mirrors crossword_gen.generate_grid and its worker
 * functions). Parallel attempts run on threads instead of processes. */
public final class Generator {
    private Generator() {}

    public static final int DEFAULT_WIDTH = 15;
    public static final int DEFAULT_HEIGHT = 10;
    public static final int PARALLEL_ATTEMPTS = resolveParallel();
    public static final double PALIER_ATTEMPT_INTERRUPT_FRACTION = 1.0;
    public static final int MIN_SUCCESSFUL_ATTEMPTS = 2;
    public static final double BEST_STATE_QUEUE_DRAIN_GRACE_S = 0.02;
    public static final double BUDGET_PROGRESS_REPORT_INTERVAL_S = 2.0;
    public static final int MAX_CONSECUTIVE_CONTINUE_PALIERS = 4;
    public static final int GRID_REPEAT_DEEP_CLEANUP_STREAK = 2;
    public static final int GRID_REPEAT_DISCARD_STREAK = 3;
    public static final int FULL_RESET_ATTEMPT_COUNT = 1;

    private static int resolveParallel() {
        String v = System.getenv("CROSSWORDFALCON_PARALLEL_ATTEMPTS");
        if (v != null && !v.isBlank()) {
            try {
                return Integer.parseInt(v.trim());
            } catch (NumberFormatException ignored) { }
        }
        return Math.max(1, Runtime.getRuntime().availableProcessors());
    }

    /** Progress callback: Python's {@code on_progress(step, **data)}. */
    public interface Progress {
        void on(String step, Map<String, Object> data);
    }

    /** Niceness increment for the search threads (CROSSWORDFALCON_GENERATION_NICE,
     * 10 by default, 0 disables), so interface requests stay responsive
     * while every core is busy searching. */
    public static final int GENERATION_NICE_INCREMENT = resolveNice();

    private static int resolveNice() {
        String v = System.getenv("CROSSWORDFALCON_GENERATION_NICE");
        if (v != null && !v.isBlank()) {
            try {
                return Integer.parseInt(v.trim());
            } catch (NumberFormatException ignored) { }
        }
        return 10;
    }

    /** Raises the calling thread's own niceness (Linux: a thread is a
     * schedulable task with its own id, and raising one's niceness needs no
     * privilege). A no-op elsewhere or on any failure. */
    static void lowerOwnPriority() {
        if (GENERATION_NICE_INCREMENT == 0) return;
        try {
            java.nio.file.Path self = java.nio.file.Files.readSymbolicLink(java.nio.file.Path.of("/proc/thread-self"));
            String tid = self.getFileName().toString();
            Process p = new ProcessBuilder("renice", "-n", String.valueOf(GENERATION_NICE_INCREMENT), "-p", tid)
                    .redirectErrorStream(true).redirectOutput(ProcessBuilder.Redirect.DISCARD).start();
            p.waitFor();
        } catch (Exception ignored) {
            // not Linux, or renice unavailable: keep the default priority
        }
    }

    // ================================================================== loaded languages (cached)

    /** A language's lexicon + index + quota word sets, cached per load key. */
    public record Loaded(Words.Lexicon lexicon, Map<Integer, LenIndex> index, Set<String> properNouns,
                         Set<String> nonGloss) {}

    private static final Map<String, Loaded> LOADED = new ConcurrentHashMap<>();
    private static final int LOADED_CACHE_MAX = 8;

    public static Loaded load(String path, Number mw, boolean easy) throws IOException {
        String key = path + "|" + mw + "|" + easy;
        Loaded l = LOADED.get(key);
        if (l != null) return l;
        synchronized (LOADED) {
            l = LOADED.get(key);
            if (l != null) return l;
            Words.Lexicon lex = Words.loadWordlist(path, mw, easy, easy);
            Map<Integer, LenIndex> idx = Words.buildIndex(lex.byLength(), lex.frequencies());
            String lang = Words.langFromPath(path);
            Set<String> proper = new HashSet<>();
            if (!Words.PROPER_NOUN_EXCLUDED_LANGS.contains(lang)) {
                lex.accents().forEach((w, acc) -> {
                    if (!acc.isEmpty() && Character.isUpperCase(acc.charAt(0))) proper.add(w);
                });
            }
            Set<String> nonGloss = new HashSet<>();
            if (lang != null && GlossLookup.hasGlossDictionary(lang)) {
                lex.accents().forEach((w, acc) -> {
                    List<String> cands = new ArrayList<>();
                    cands.add(acc);
                    cands.addAll(lex.canonicals().getOrDefault(w, List.of()));
                    if (!GlossLookup.hasAnyGloss(cands, lang)) nonGloss.add(w);
                });
            }
            l = new Loaded(lex, idx, proper, nonGloss);
            if (LOADED.size() >= LOADED_CACHE_MAX) LOADED.clear();
            LOADED.put(key, l);
            return l;
        }
    }

    // ================================================================== worker context

    /** Everything the Python workers received through the pool initializer. */
    static final class Ctx {
        DualIndex index;
        PW priorityWords;
        Set<String> challengeWords;
        AtomicBoolean cancelEvent, attemptDoneEvent;
        Consumer<Diag> bestStateQueue;
        AtomicLongArray checksProgress;
        AtomicIntegerArray attemptActive;
        Set<String> properNounWords, nonGlossWords;
        Integer maxProperNouns, maxNonGloss;
    }

    /** One finished attempt: the grid it worked on, the solved fill (or
     * null) and its diagnostics. */
    public record Outcome(char[][] grid, Fill.Result result, Diag diag) {}

    static Fill.FillArgs baseArgs(Ctx ctx, Long deadlineChecks, Diag diag, Integer checksSlot, long seed,
                                  Set<Integer> requiredCells) {
        Fill.FillArgs a = new Fill.FillArgs();
        a.deadlineChecks = deadlineChecks;
        a.diagnostics = diag;
        a.cancelEvent = ctx.cancelEvent;
        a.attemptDoneEvent = ctx.attemptDoneEvent;
        a.bestStateQueue = ctx.bestStateQueue;
        a.checksProgress = ctx.checksProgress;
        a.checksSlot = checksSlot;
        a.attemptActive = ctx.attemptActive;
        a.attemptId = seed;
        a.properNounWords = ctx.properNounWords;
        a.maxProperNouns = ctx.maxProperNouns;
        a.nonGlossWords = ctx.nonGlossWords;
        a.maxNonGloss = ctx.maxNonGloss;
        a.priorityWords = ctx.priorityWords;
        a.challengeWords = ctx.challengeWords;
        a.requiredCells = requiredCells;
        return a;
    }

    static Outcome patternAttempt(Ctx ctx, int rows, int cols, double ratio, long seed, double forceFraction,
                                  char[][] seedGrid, Map<Integer, Character> locked, double enrichment,
                                  Long deadlineChecks, Map<Integer, Character> permanentLocked,
                                  Set<Integer> permanentBlack, Set<Integer> requiredCells, Integer checksSlot,
                                  boolean racing) {
        Rng rng = new Rng(seed);
        LengthSets available = LengthSets.available(ctx.index, Grids.PREFILL_MIN_WORD_COUNT);
        if (permanentLocked != null && !permanentLocked.isEmpty()) {
            Map<Integer, Character> m = new LinkedHashMap<>(locked == null ? Map.of() : locked);
            m.putAll(permanentLocked);
            locked = m;
        }
        if (permanentBlack != null && !permanentBlack.isEmpty()) {
            seedGrid = seedGrid == null ? Grids.blank(rows, cols) : Grids.copy(seedGrid);
            for (int cell : permanentBlack) seedGrid[Cells.r(cell)][Cells.c(cell)] = BLACK;
        }
        char[][] grid = Grids.makePattern(rows, cols, ratio, rng, available, seedGrid, locked, ctx.index, enrichment);
        if (!ctx.challengeWords.isEmpty()) {
            Cleanup.widenFloatingBlackCellsForPriorityWords(grid, rows, cols, rng, List.of(ctx.challengeWords), ctx.index,
                    locked, permanentBlack);
        }
        List<int[]> slots = Grids.extractSlots(grid, rows, cols);
        locked = Fill.forceSingleCandidateSlots(slots, ctx.index, locked == null ? Map.of() : locked, null);
        String[] preseed = null;
        Set<Integer> lockedImpossible = new HashSet<>();
        if (!locked.isEmpty()) {
            preseed = new String[slots.size()];
            for (int i = 0; i < slots.size(); i++) {
                int[] cells = slots.get(i);
                if (!Grids.allKnown(cells, locked)) continue;
                String word = Grids.wordAt(cells, locked);
                if (permanentLocked != null && !permanentLocked.isEmpty() && Grids.allKnown(cells, permanentLocked)) {
                    preseed[i] = word;
                } else if (Words.slotCandidateCount(ctx.index, cells.length, cells, locked) > 0) {
                    preseed[i] = word;
                } else {
                    lockedImpossible.add(i);
                }
            }
        }
        Object[] biases = Fill.sampleLetterBiases(grid, rows, cols, ctx.index, rng, forceFraction, lockedImpossible, locked);
        Diag diag = new Diag();
        Fill.FillArgs a = baseArgs(ctx, deadlineChecks, diag, checksSlot, seed, requiredCells);
        @SuppressWarnings("unchecked")
        Map<Integer, Character> forced = (Map<Integer, Character>) biases[0];
        @SuppressWarnings("unchecked")
        Map<Integer, int[][]> scores = (Map<Integer, int[][]>) biases[1];
        a.forcedLetters = forced;
        a.letterScores = scores;
        a.preseedAssignment = preseed;
        a.lockedLetters = locked;
        boolean flag = racing && checksSlot != null && ctx.attemptActive != null;
        if (flag) ctx.attemptActive.set(checksSlot, 1);
        Fill.Result result;
        try {
            result = Fill.tryFill(grid, rows, cols, ctx.index, rng, a);
        } finally {
            if (flag) ctx.attemptActive.set(checksSlot, 0);
        }
        return new Outcome(grid, result, diag);
    }

    static Outcome patternContinue(Ctx ctx, int rows, int cols, long seed, char[][] seedGrid, String[] preseedIn,
                                   Set<Integer> excludedSlots, double forceFraction, Long deadlineChecks,
                                   Map<Integer, Character> permanentLocked, Set<Integer> requiredCells,
                                   Integer checksSlot) {
        Rng rng = new Rng(seed);
        List<int[]> slots = Grids.extractSlots(seedGrid, rows, cols);
        Map<Integer, Character> known = new LinkedHashMap<>();
        for (int i = 0; i < slots.size(); i++) {
            String w = preseedIn[i];
            if (w == null) continue;
            int[] cells = slots.get(i);
            for (int p = 0; p < cells.length; p++) known.put(cells[p], w.charAt(p));
        }
        if (permanentLocked != null) known.putAll(permanentLocked);
        known = Fill.forceSingleCandidateSlots(slots, ctx.index, known, excludedSlots);
        String[] preseed = preseedIn.clone();
        Set<Integer> excluded = excludedSlots == null ? Set.of() : excludedSlots;
        for (int i = 0; i < slots.size(); i++) {
            if (excluded.contains(i) || preseed[i] != null) continue;
            int[] cells = slots.get(i);
            if (!Grids.allKnown(cells, known)) continue;
            String word = Grids.wordAt(cells, known);
            if (permanentLocked != null && !permanentLocked.isEmpty() && Grids.allKnown(cells, permanentLocked)) {
                preseed[i] = word;
            } else if (Words.slotCandidateCount(ctx.index, cells.length, cells, known) > 0) {
                preseed[i] = word;
            }
        }
        Object[] biases = Fill.sampleLetterBiases(seedGrid, rows, cols, ctx.index, rng, forceFraction, excludedSlots, known);
        Diag diag = new Diag();
        Fill.FillArgs a = baseArgs(ctx, deadlineChecks, diag, checksSlot, seed, requiredCells);
        @SuppressWarnings("unchecked")
        Map<Integer, Character> forced = (Map<Integer, Character>) biases[0];
        @SuppressWarnings("unchecked")
        Map<Integer, int[][]> scores = (Map<Integer, int[][]>) biases[1];
        a.forcedLetters = forced;
        a.letterScores = scores;
        a.preseedAssignment = preseed;
        a.excludedSlots = excludedSlots;
        a.lockedLetters = known;
        boolean flag = checksSlot != null && ctx.attemptActive != null;
        if (flag) ctx.attemptActive.set(checksSlot, 1);
        Fill.Result result;
        try {
            result = Fill.tryFill(seedGrid, rows, cols, ctx.index, rng, a);
        } finally {
            if (flag) ctx.attemptActive.set(checksSlot, 0);
        }
        return new Outcome(seedGrid, result, diag);
    }

    // ================================================================== resume state

    public static Map<String, Object> serializeResumeState(char[][] seedGrid, Map<Integer, Character> locked,
                                                           String[] preseed, Set<Integer> excluded) {
        Map<String, Object> m = new LinkedHashMap<>();
        m.put("seed_grid", Grids.toJson(seedGrid));
        if (locked == null) {
            m.put("locked_letters", null);
        } else {
            List<Object> l = new ArrayList<>();
            locked.forEach((cell, ch) -> l.add(Json.list(Cells.r(cell), Cells.c(cell), String.valueOf(ch))));
            m.put("locked_letters", l);
        }
        m.put("preseed_assignment", preseed == null ? null : Diag.assignmentJson(preseed));
        m.put("excluded_slots", excluded == null ? null : new ArrayList<>(new TreeSet<>(excluded)));
        return m;
    }

    /** Returns {seedGrid, locked, preseed, excluded}. */
    public static Object[] deserializeResumeState(Map<String, Object> state) {
        char[][] seed = Grids.fromJson(state.get("seed_grid"), WHITE);
        Object rawLocked = state.get("locked_letters");
        Map<Integer, Character> locked = null;
        if (rawLocked != null) {
            locked = new LinkedHashMap<>();
            for (Object e : Json.asList(rawLocked)) {
                List<Object> t = Json.asList(e);
                locked.put(Cells.of(((Number) t.get(0)).intValue(), ((Number) t.get(1)).intValue()), t.get(2).toString().charAt(0));
            }
        }
        Object rawPre = state.get("preseed_assignment");
        String[] preseed = null;
        if (rawPre != null) {
            List<Object> l = Json.asList(rawPre);
            preseed = new String[l.size()];
            for (int i = 0; i < l.size(); i++) preseed[i] = l.get(i) == null ? null : l.get(i).toString();
        }
        Object rawEx = state.get("excluded_slots");
        Set<Integer> excluded = null;
        if (rawEx != null) {
            excluded = new HashSet<>();
            for (Object o : Json.asList(rawEx)) excluded.add(((Number) o).intValue());
        }
        return new Object[]{seed, locked, preseed, excluded};
    }

    // ================================================================== helpers

    static Map<String, Object> copyWithoutPrevious(Map<String, Object> entry) {
        if (entry == null) return null;
        Map<String, Object> m = new LinkedHashMap<>(entry);
        m.remove("previous");
        return m;
    }

    static Map<String, Object> example(char[][] grid, List<Integer> impossible, List<Integer> deadlock,
                                       List<Integer> excluded, List<Integer> forced, List<Integer> locked,
                                       List<Integer> theme, List<Integer> challenge, Integer processNumber,
                                       boolean isBest) {
        Map<String, Object> m = new LinkedHashMap<>();
        m.put("example_grid", Grids.toJson(grid));
        m.put("impossible_cells", Cells.toJson(impossible));
        m.put("deadlock_cells", Cells.toJson(deadlock));
        m.put("excluded_cells", Cells.toJson(excluded));
        m.put("forced_cells", Cells.toJson(forced));
        m.put("locked_cells", Cells.toJson(locked));
        m.put("theme_cells", Cells.toJson(theme));
        m.put("challenge_cells", Cells.toJson(challenge));
        m.put("process_number", processNumber);
        m.put("is_best", isBest);
        return m;
    }

    static List<Object> sortExamplesByProcess(List<Map<String, Object>> examples) {
        List<Map<String, Object>> copy = new ArrayList<>(examples);
        copy.sort((a, b) -> {
            Object pa = a.get("process_number"), pb = b.get("process_number");
            int na = pa == null ? 1 : 0, nb = pb == null ? 1 : 0;
            if (na != nb) return Integer.compare(na, nb);
            int va = pa == null ? 0 : ((Number) pa).intValue(), vb = pb == null ? 0 : ((Number) pb).intValue();
            return Integer.compare(va, vb);
        });
        return new ArrayList<>(copy);
    }

    static String outcomeKey(char[][] g, String[] assignment) {
        return Grids.key(g) + "|" + Arrays.toString(assignment);
    }

    static Set<Integer> slotsTouching(List<int[]> slots, Set<Integer> targets) {
        Set<Integer> touching = new HashSet<>();
        if (targets.isEmpty()) return touching;
        Map<Integer, List<Integer>> c2s = Grids.cellToSlotIndices(slots);
        for (int i : targets) for (int cell : slots.get(i)) for (int j : c2s.get(cell)) if (j != i) touching.add(j);
        return touching;
    }

    static Map<String, Object> data(Object... kv) {
        return Json.obj(kv);
    }

    // ================================================================== parameters

    /** generate_grid's keyword arguments. */
    public static final class Params {
        public int width = DEFAULT_WIDTH, height = DEFAULT_HEIGHT;
        public String difficulty = "easy";
        public Number maxWords;
        public double blackRatio = 0.0;
        public int attempts = 200;
        public Long seed;
        public String wordlistPath = "data/wordlist_fr_full.tsv";
        public Progress onProgress;
        public double forceLettersFraction = 0.0;
        public AtomicBoolean cancelEvent;
        public double blackEnrichmentFraction = Grids.POST_PREFILL_BLACK_FRACTION;
        public Long deadlineChecks;
        public Map<String, Object> resumeState;
        public BooleanSupplier shouldPause;
        public String bilingualWordlistPath;
        public Set<String> priorityWords;
        public Set<String> bilingualPriorityWords;
        public Map<Integer, Character> permanentLockedLetters;
        public Set<Integer> permanentBlackCells;
        public Set<Integer> requiredCells;
        public List<String> challengeWords;
        public Consumer<List<Object>> onLivePreview;
    }

    // ================================================================== generate_grid

    public static Map<String, Object> generateGrid(Params p) throws IOException {
        Progress progress = (step, d) -> {
            if (p.onProgress != null) p.onProgress.on(step, d);
        };
        Map<Integer, Character> permanentLocked = p.permanentLockedLetters == null ? new LinkedHashMap<>()
                : new LinkedHashMap<>(p.permanentLockedLetters);
        Set<Integer> permanentBlack = p.permanentBlackCells;
        Rng rng = Rng.of(p.seed);
        Number mw = p.maxWords != null ? p.maxWords : Words.DIFFICULTY_PRESETS.get(p.difficulty);
        boolean easy = "easy".equals(p.difficulty);
        Loaded across = load(p.wordlistPath, mw, easy);
        String language = Words.langFromPath(p.wordlistPath);
        if (language == null) language = "fr";
        boolean bilingual = p.bilingualWordlistPath != null && !p.bilingualWordlistPath.isEmpty()
                && !p.bilingualWordlistPath.equals(p.wordlistPath);
        Loaded down = bilingual ? load(p.bilingualWordlistPath, mw, easy) : across;
        String bilingualLanguage = bilingual ? Objects.requireNonNullElse(Words.langFromPath(p.bilingualWordlistPath),
                p.bilingualWordlistPath) : null;
        Integer maxProperNouns = Words.MAX_PROPER_NOUNS.getOrDefault(p.difficulty, Words.MAX_PROPER_NOUNS.get("hard"));
        Set<String> properNouns = new HashSet<>(across.properNouns());
        if (bilingual) properNouns.addAll(down.properNouns());
        Integer maxNonGloss = Words.MAX_NON_GLOSS_WORDS.getOrDefault(p.difficulty, Words.MAX_NON_GLOSS_WORDS.get("hard"));
        Set<String> nonGloss = new HashSet<>(across.nonGloss());
        if (bilingual) nonGloss.addAll(down.nonGloss());
        DualIndex index = new DualIndex(across.index(), down.index());
        PW priority;
        boolean anyPriority = (p.priorityWords != null && !p.priorityWords.isEmpty())
                || (p.bilingualPriorityWords != null && !p.bilingualPriorityWords.isEmpty());
        if (anyPriority) {
            Set<String> ap = new HashSet<>();
            if (p.priorityWords != null) for (String w : p.priorityWords) {
                String u = w.toUpperCase(java.util.Locale.ROOT);
                if (across.lexicon().accents().containsKey(u)) ap.add(u);
            }
            if (bilingual) {
                Set<String> dp = new HashSet<>();
                if (p.bilingualPriorityWords != null) for (String w : p.bilingualPriorityWords) {
                    String u = w.toUpperCase(java.util.Locale.ROOT);
                    if (down.lexicon().accents().containsKey(u)) dp.add(u);
                }
                priority = new PW(ap, dp, true);
            } else {
                priority = PW.single(ap);
            }
        } else {
            priority = PW.EMPTY;
        }
        Set<String> challenge = new LinkedHashSet<>(Words.challengeSet(p.challengeWords));
        LengthSets availablePreview = LengthSets.available(index, Grids.PREFILL_MIN_WORD_COUNT);
        Map<String, Object> lengthCounts = new LinkedHashMap<>();
        new TreeMap<>(across.lexicon().byLength()).forEach((len, ws) -> lengthCounts.put(String.valueOf(len), ws.size()));
        int wordCount = 0;
        for (List<String> ws : across.lexicon().byLength().values()) wordCount += ws.size();
        Integer bilingualWordCount = null;
        if (bilingual) {
            int n = 0;
            for (List<String> ws : down.lexicon().byLength().values()) n += ws.size();
            bilingualWordCount = n;
        }
        progress.on("wordlist_loaded", data("word_count", wordCount, "length_counts", lengthCounts,
                "bilingual_language", bilingualLanguage, "bilingual_word_count", bilingualWordCount));
        final int rows = p.height, cols = p.width;
        final long resolvedDeadline = p.deadlineChecks != null ? p.deadlineChecks : (long) rows * cols * 2000;
        final double ratio = p.blackRatio;
        final int PA = PARALLEL_ATTEMPTS;

        char[][] best = null;
        Fill.Result bestResult = null;
        Object[] bestMinimized = null;
        List<Outcome> accumulatedSuccesses = new ArrayList<>();
        Diag bestDiag = null;
        Diag lastDiag = null;
        List<Object> lastExamples = new ArrayList<>();
        long totalAttemptsTried = 0;
        char[][] carrySeedGrid = null;
        Map<Integer, Character> carryLocked = null;
        List<Object[]> carrySeedPool = null;
        List<Integer> carrySeedPoolLineage = null;
        int nextLineage = PA + 1;
        String[] carryPreseed = null;
        Set<Integer> carryExcluded = null;
        List<Object[]> carrySeedPoolContinue = null;
        List<Integer> carrySeedPoolContinueLineage = null;
        if (p.resumeState != null) {
            Object[] st = deserializeResumeState(p.resumeState);
            carrySeedGrid = (char[][]) st[0];
            @SuppressWarnings("unchecked")
            Map<Integer, Character> l = (Map<Integer, Character>) st[1];
            carryLocked = l;
            carryPreseed = (String[]) st[2];
            @SuppressWarnings("unchecked")
            Set<Integer> e = (Set<Integer>) st[3];
            carryExcluded = e;
        }
        int consecutiveContinue = 0;
        Map<String, Integer> carryCleanupStreaks = new HashMap<>();
        int carryDiscardedCount = 0;
        boolean justCleaned = false;

        AtomicBoolean attemptDoneEvent = new AtomicBoolean(false);
        BlockingQueue<Diag> bestStateQueue = new LinkedBlockingQueue<>();
        AtomicLongArray checksProgress = new AtomicLongArray(PA);
        AtomicIntegerArray attemptActive = new AtomicIntegerArray(PA);
        List<Diag> bestStateBuffer = new ArrayList<>();
        AtomicBoolean stopDrain = new AtomicBoolean(false);
        Map<Integer, Map<String, Object>> liveState = new HashMap<>();
        Object liveLock = new Object();
        @SuppressWarnings("unchecked")
        Map<Long, Integer>[] seedToLineageRef = new Map[]{new ConcurrentHashMap<Long, Integer>()};
        @SuppressWarnings("unchecked")
        Map<Long, Integer>[] seedToSlotRef = new Map[]{new ConcurrentHashMap<Long, Integer>()};

        java.util.function.Function<Integer, Integer> budgetPercent = slot ->
                slot == null ? null : (int) falcon.Py.round(100.0 * checksProgress.get(slot) / resolvedDeadline);
        java.util.function.BiConsumer<Integer, Map<String, Object>> storeLive = (pn, entry) -> {
            synchronized (liveLock) {
                entry.put("previous", copyWithoutPrevious(liveState.get(pn)));
                liveState.put(pn, entry);
            }
        };
        Runnable publishLive = () -> {
            if (p.onLivePreview == null) return;
            List<Map<String, Object>> snap;
            synchronized (liveLock) {
                snap = new ArrayList<>(liveState.values());
            }
            p.onLivePreview.accept(sortExamplesByProcess(snap));
        };

        Ctx ctx = new Ctx();
        ctx.index = index;
        ctx.priorityWords = priority;
        ctx.challengeWords = challenge;
        ctx.cancelEvent = p.cancelEvent;
        ctx.attemptDoneEvent = attemptDoneEvent;
        ctx.bestStateQueue = bestStateQueue::add;
        ctx.checksProgress = checksProgress;
        ctx.attemptActive = attemptActive;
        ctx.properNounWords = properNouns;
        ctx.maxProperNouns = maxProperNouns;
        ctx.nonGlossWords = nonGloss;
        ctx.maxNonGloss = maxNonGloss;

        Thread drain = new Thread(() -> {
            long lastReport = 0;
            while (!stopDrain.get()) {
                Diag msg = null;
                try {
                    msg = bestStateQueue.poll(100, TimeUnit.MILLISECONDS);
                } catch (InterruptedException e) {
                    break;
                }
                try {
                    if (msg != null) {
                        if (!"heartbeat".equals(msg.kind)) {
                            synchronized (bestStateBuffer) {
                                bestStateBuffer.add(msg);
                            }
                        }
                        if (p.onLivePreview != null) {
                            Integer pn = msg.attemptId == null ? null : seedToLineageRef[0].get(msg.attemptId);
                            Map<String, Object> current;
                            synchronized (liveLock) {
                                current = liveState.get(pn);
                            }
                            boolean frozen = current != null
                                    && List.of("succeeded", "failed", "interrupted").contains(current.get("live_status"))
                                    && Objects.equals(current.get("attempt_id"), msg.attemptId);
                            if (!frozen) {
                                Map<String, Object> entry = msg.toJson(false);
                                entry.put("process_number", pn);
                                entry.put("live_status", "computing");
                                entry.put("budget_percent", msg.attemptId == null ? null : budgetPercent.apply(seedToSlotRef[0].get(msg.attemptId)));
                                storeLive.accept(pn, entry);
                                publishLive.run();
                            }
                        }
                    }
                    long now = System.nanoTime();
                    if ((now - lastReport) / 1e9 >= BUDGET_PROGRESS_REPORT_INTERVAL_S) {
                        lastReport = now;
                        long sum = 0;
                        for (int i = 0; i < PA; i++) sum += checksProgress.get(i);
                        if (sum != 0) {
                            double avg = (double) sum / PA;
                            progress.on("budget_progress", data("percent", (int) falcon.Py.round(100 * avg / resolvedDeadline)));
                        }
                        if (p.onLivePreview != null) {
                            boolean changed = false;
                            Map<Long, Integer> s2slot = seedToSlotRef[0];
                            Map<Integer, Integer> processToSlot = new HashMap<>();
                            seedToLineageRef[0].forEach((sd, pn) -> processToSlot.put(pn, s2slot.get(sd)));
                            synchronized (liveLock) {
                                for (Map.Entry<Integer, Map<String, Object>> e : liveState.entrySet()) {
                                    Map<String, Object> entry = e.getValue();
                                    if (!"computing".equals(entry.get("live_status"))) continue;
                                    Integer pct = budgetPercent.apply(processToSlot.get(e.getKey()));
                                    if (pct != null && !pct.equals(entry.get("budget_percent"))) {
                                        entry.put("budget_percent", pct);
                                        changed = true;
                                    }
                                }
                            }
                            if (changed) publishLive.run();
                        }
                    }
                } catch (RuntimeException e) {
                    falcon.Log.warning("best-state drain: %s", e);
                }
            }
        }, "best-state-drain");
        drain.setDaemon(true);
        drain.start();

        ThreadFactory tf = r -> {
            Thread t = new Thread(null, () -> {
                lowerOwnPriority();
                r.run();
            }, "csp-worker", 256L * 1024 * 1024);
            t.setDaemon(true);
            t.setPriority(Thread.MIN_PRIORITY);
            return t;
        };
        ExecutorService executor = Executors.newFixedThreadPool(PA, tf);
        int attempt = 0;
        try {
            for (attempt = 0; attempt < p.attempts; attempt++) {
                if (p.cancelEvent != null && p.cancelEvent.get()) throw new GenerationCancelled();
                if (p.shouldPause != null && p.shouldPause.getAsBoolean()) {
                    throw new GenerationPaused(carrySeedGrid != null
                            ? serializeResumeState(carrySeedGrid, carryLocked, carryPreseed, carryExcluded) : null);
                }
                attemptDoneEvent.set(false);
                for (int i = 0; i < PA; i++) {
                    checksProgress.set(i, 0);
                    attemptActive.set(i, 0);
                }
                List<Object[]> pool = carrySeedPool != null && !carrySeedPool.isEmpty() ? carrySeedPool
                        : List.<Object[]>of(new Object[]{carrySeedGrid, carryLocked});
                List<Integer> poolLineage = carrySeedPoolLineage != null && !carrySeedPoolLineage.isEmpty()
                        ? carrySeedPoolLineage : List.of(1);
                List<Object[]> continuePool = null;
                List<Integer> continuePoolLineage = null;
                List<Map<String, Object>> cycleStart = new ArrayList<>();
                if (carryPreseed != null) {
                    continuePool = carrySeedPoolContinue != null && !carrySeedPoolContinue.isEmpty() ? carrySeedPoolContinue
                            : List.<Object[]>of(new Object[]{carrySeedGrid, carryPreseed, carryExcluded});
                    continuePoolLineage = carrySeedPoolContinueLineage != null && !carrySeedPoolContinueLineage.isEmpty()
                            ? carrySeedPoolContinueLineage : List.of(1);
                    Set<String> seen = new HashSet<>();
                    for (int k = 0; k < continuePool.size(); k++) {
                        char[][] pg = (char[][]) continuePool.get(k)[0];
                        String[] pp = (String[]) continuePool.get(k)[1];
                        if (!seen.add(Grids.key(pg))) continue;
                        Object[] st = Fill.cycleStartPreview(rows, cols, pg, null, pp);
                        @SuppressWarnings("unchecked")
                        List<Integer> lc = (List<Integer>) st[1];
                        Map<String, Object> ex = example((char[][]) st[0], List.of(), List.of(), List.of(), List.of(), lc,
                                Fill.themeCellsFromPreviewState(pg, rows, cols, null, pp, priority),
                                Fill.challengeCellsFromPreviewState(pg, rows, cols, null, pp, challenge),
                                continuePoolLineage.get(k % continuePoolLineage.size()), k == 0);
                        ex.put("low_candidate_cells", List.of());
                        ex.put("noise_cells", List.of());
                        cycleStart.add(ex);
                    }
                } else {
                    Set<String> seen = new HashSet<>();
                    for (int k = 0; k < pool.size(); k++) {
                        char[][] pg = (char[][]) pool.get(k)[0];
                        @SuppressWarnings("unchecked")
                        Map<Integer, Character> pl = (Map<Integer, Character>) pool.get(k)[1];
                        boolean keyed = pg != null;
                        if (keyed && !seen.add(Grids.key(pg))) continue;
                        Object[] st = Fill.cycleStartPreview(rows, cols, pg, pl, null);
                        @SuppressWarnings("unchecked")
                        List<Integer> lc = (List<Integer>) st[1];
                        Map<String, Object> ex = example((char[][]) st[0], List.of(), List.of(), List.of(), List.of(), lc,
                                Fill.themeCellsFromPreviewState(pg, rows, cols, pl, null, priority),
                                Fill.challengeCellsFromPreviewState(pg, rows, cols, pl, null, challenge),
                                keyed ? poolLineage.get(k % poolLineage.size()) : null, k == 0);
                        ex.put("low_candidate_cells", Cells.toJson(pg != null ? Fill.lowCandidateSlotCells(pg, rows, cols, index, pl) : List.of()));
                        ex.put("noise_cells", Cells.toJson(pg != null ? Fill.noiseSlotCells(pg, rows, cols, index, pl) : List.of()));
                        cycleStart.add(ex);
                    }
                }
                progress.on("pattern", data("attempt", attempt + 1, "attempts", p.attempts, "parallel", PA,
                        "total_attempts", totalAttemptsTried, "examples", sortExamplesByProcess(cycleStart)));
                if (p.onLivePreview != null) {
                    synchronized (liveLock) {
                        Map<Integer, Map<String, Object>> carried = new HashMap<>();
                        liveState.forEach((pn, e) -> carried.put(pn, copyWithoutPrevious(e)));
                        liveState.clear();
                        for (Map<String, Object> ex : cycleStart) {
                            Integer pn = (Integer) ex.get("process_number");
                            if (pn == null) continue;
                            Map<String, Object> e = new LinkedHashMap<>(ex);
                            e.put("live_status", "computing");
                            e.put("budget_percent", 0);
                            e.put("previous", carried.get(pn));
                            liveState.put(pn, e);
                        }
                    }
                    publishLive.run();
                }
                long[] seeds = new long[PA];
                for (int i = 0; i < PA; i++) seeds[i] = rng.seed31();
                ExecutorCompletionService<Outcome> ecs = new ExecutorCompletionService<>(executor);
                Map<Future<Outcome>, Long> futureSeed = new HashMap<>();
                Set<Future<Outcome>> origFutures = new HashSet<>();
                List<Integer> dispatchLineage;
                final char[][] fSeedGridNull = null;
                if (carryPreseed != null) {
                    int resetCount = FULL_RESET_ATTEMPT_COUNT;
                    dispatchLineage = Cleanup.buildDispatchLineage(PA, resetCount, continuePoolLineage);
                    for (int i = 0; i < PA; i++) {
                        final int slot = i;
                        final long s = seeds[i];
                        Future<Outcome> f;
                        if (i < resetCount) {
                            f = ecs.submit(() -> patternAttempt(ctx, rows, cols, ratio, s, p.forceLettersFraction, null,
                                    null, p.blackEnrichmentFraction, p.deadlineChecks, permanentLocked, permanentBlack,
                                    p.requiredCells, slot, true));
                        } else {
                            Object[] task = continuePool.get((i - resetCount) % continuePool.size());
                            f = ecs.submit(() -> patternContinue(ctx, rows, cols, s, (char[][]) task[0], (String[]) task[1],
                                    null, p.forceLettersFraction, p.deadlineChecks, permanentLocked, p.requiredCells, slot));
                        }
                        futureSeed.put(f, s);
                        origFutures.add(f);
                    }
                } else {
                    int resetCount = justCleaned ? Math.min(PA, FULL_RESET_ATTEMPT_COUNT + carryDiscardedCount) : 0;
                    List<Map<String, Object>> early = new ArrayList<>();
                    if (carrySeedGrid == null) {
                        dispatchLineage = new ArrayList<>();
                        for (int i = 1; i <= PA; i++) dispatchLineage.add(i);
                        Set<String> seen = new HashSet<>();
                        for (int i = 0; i < PA; i++) {
                            char[][] ep = Grids.makePattern(rows, cols, ratio, new Rng(seeds[i]), availablePreview, null,
                                    permanentLocked.isEmpty() ? null : permanentLocked, index, p.blackEnrichmentFraction);
                            if (!seen.add(Grids.key(ep))) continue;
                            Object[] st = Fill.cycleStartPreview(rows, cols, ep, null, null);
                            @SuppressWarnings("unchecked")
                            List<Integer> lc = (List<Integer>) st[1];
                            early.add(example((char[][]) st[0], List.of(), List.of(), List.of(), List.of(), lc, List.of(),
                                    List.of(), dispatchLineage.get(i), false));
                        }
                    } else {
                        dispatchLineage = Cleanup.buildDispatchLineage(PA, resetCount, poolLineage);
                        Set<String> seen = new HashSet<>();
                        for (int k = 0; k < pool.size(); k++) {
                            char[][] pg = (char[][]) pool.get(k)[0];
                            @SuppressWarnings("unchecked")
                            Map<Integer, Character> pl = (Map<Integer, Character>) pool.get(k)[1];
                            Map<Integer, Character> merged = pl;
                            if (!permanentLocked.isEmpty()) {
                                merged = new LinkedHashMap<>(pl == null ? Map.of() : pl);
                                merged.putAll(permanentLocked);
                            }
                            char[][] ep = Grids.makePattern(rows, cols, ratio,
                                    new Rng(seeds[Math.min(resetCount + k, seeds.length - 1)]), availablePreview, pg,
                                    merged, index, p.blackEnrichmentFraction);
                            if (!seen.add(Grids.key(ep))) continue;
                            Object[] st = Fill.cycleStartPreview(rows, cols, ep, pl, null);
                            @SuppressWarnings("unchecked")
                            List<Integer> lc = (List<Integer>) st[1];
                            early.add(example((char[][]) st[0], List.of(), List.of(), List.of(), List.of(), lc,
                                    Fill.themeCellsFromPreviewState(ep, rows, cols, pl, null, priority),
                                    Fill.challengeCellsFromPreviewState(ep, rows, cols, pl, null, challenge),
                                    poolLineage.get(k % poolLineage.size()), k == 0));
                        }
                    }
                    progress.on("pattern_generated", data("attempt", attempt + 1, "attempts", p.attempts,
                            "total_attempts", totalAttemptsTried, "examples", sortExamplesByProcess(early)));
                    for (int i = 0; i < PA; i++) {
                        final int slot = i;
                        final long s = seeds[i];
                        char[][] tg;
                        Map<Integer, Character> tl;
                        if (i < resetCount) {
                            tg = null;
                            tl = null;
                        } else {
                            Object[] task = pool.get((i - resetCount) % pool.size());
                            tg = (char[][]) task[0];
                            @SuppressWarnings("unchecked")
                            Map<Integer, Character> l = (Map<Integer, Character>) task[1];
                            tl = l;
                        }
                        Future<Outcome> f = ecs.submit(() -> patternAttempt(ctx, rows, cols, ratio, s, p.forceLettersFraction,
                                tg, tl, p.blackEnrichmentFraction, p.deadlineChecks, permanentLocked, permanentBlack,
                                p.requiredCells, slot, true));
                        futureSeed.put(f, s);
                        origFutures.add(f);
                    }
                }
                Map<Long, Integer> seedToLineage = new ConcurrentHashMap<>();
                Map<Long, Integer> seedToSlot = new ConcurrentHashMap<>();
                for (int i = 0; i < PA; i++) {
                    if (dispatchLineage.get(i) != null) seedToLineage.put(seeds[i], dispatchLineage.get(i));
                    seedToSlot.put(seeds[i], i);
                }
                seedToLineageRef[0] = seedToLineage;
                seedToSlotRef[0] = seedToSlot;
                int interruptThreshold = Math.max(1, (int) Math.ceil(PALIER_ATTEMPT_INTERRUPT_FRACTION * origFutures.size()));
                Set<Future<Outcome>> pending = new HashSet<>(futureSeed.keySet());
                List<Outcome> outcomes = new ArrayList<>();
                int origDone = 0;
                while (!pending.isEmpty()) {
                    List<Future<Outcome>> done = new ArrayList<>();
                    Future<Outcome> first;
                    try {
                        first = ecs.poll(500, TimeUnit.MILLISECONDS);
                    } catch (InterruptedException e) {
                        Thread.currentThread().interrupt();
                        throw new GenerationCancelled();
                    }
                    if (first != null) {
                        done.add(first);
                        Future<Outcome> more;
                        while ((more = ecs.poll()) != null) done.add(more);
                    }
                    pending.removeAll(done);
                    if (!attemptDoneEvent.get()) {
                        boolean allSpent = true;
                        for (Future<Outcome> f : pending) {
                            if (!origFutures.contains(f)) continue;
                            if (checksProgress.get(seedToSlot.get(futureSeed.get(f))) < resolvedDeadline) {
                                allSpent = false;
                                break;
                            }
                        }
                        if (allSpent) attemptDoneEvent.set(true);
                    }
                    for (Future<Outcome> f : done) {
                        Outcome res;
                        try {
                            res = f.get();
                        } catch (ExecutionException ee) {
                            Throwable c = ee.getCause();
                            if (c instanceof RuntimeException re) throw re;
                            if (c instanceof Error er) throw er;
                            throw new IllegalStateException(c);
                        } catch (InterruptedException ie) {
                            Thread.currentThread().interrupt();
                            throw new GenerationCancelled();
                        }
                        outcomes.add(res);
                        if (origFutures.contains(f)) {
                            origDone++;
                            if (origDone == interruptThreshold) attemptDoneEvent.set(true);
                        }
                        long seed = futureSeed.get(f);
                        if (p.onLivePreview != null) {
                            Integer pn = seedToLineage.get(seed);
                            Map<String, Object> entry;
                            if (res.result() != null) {
                                List<int[]> fs = Grids.extractSlots(res.grid(), rows, cols);
                                String[] asg = res.result().assignment();
                                entry = example(Grids.buildLettersGrid(rows, cols, fs, asg), List.of(), List.of(), List.of(),
                                        List.of(), List.of(), Fill.themeWordCells(fs, asg, priority),
                                        Fill.challengeWordCellsFromAssignment(fs, asg, challenge), null, false);
                                entry.remove("process_number");
                                entry.remove("is_best");
                                entry.put("live_status", "succeeded");
                            } else {
                                Map<String, Object> dj = res.diag().toJson(false);
                                entry = new LinkedHashMap<>();
                                for (String k : List.of("example_grid", "impossible_cells", "deadlock_cells", "excluded_cells",
                                        "forced_cells", "locked_cells", "theme_cells", "challenge_cells", "stat_letters")) {
                                    if (dj.containsKey(k)) entry.put(k, dj.get(k));
                                }
                                entry.put("live_status", "interrupted_other_attempt_done".equals(res.diag().reason)
                                        ? "interrupted" : "failed");
                            }
                            entry.put("process_number", pn);
                            entry.put("attempt_id", seed);
                            entry.put("budget_percent", budgetPercent.apply(seedToSlot.get(seed)));
                            storeLive.accept(pn, entry);
                            publishLive.run();
                        }
                        if (!attemptDoneEvent.get()) {
                            long newSeed = rng.seed31();
                            Integer freed = seedToSlot.get(seed);
                            if (freed != null) checksProgress.set(freed, 0);
                            final Integer fslot = freed;
                            Future<Outcome> nf = ecs.submit(() -> patternAttempt(ctx, rows, cols, ratio, newSeed,
                                    p.forceLettersFraction, null, null, p.blackEnrichmentFraction, p.deadlineChecks,
                                    permanentLocked, permanentBlack, p.requiredCells, fslot, false));
                            pending.add(nf);
                            futureSeed.put(nf, newSeed);
                            if (freed != null) seedToSlot.put(newSeed, freed);
                            seedToLineage.put(newSeed, nextLineage++);
                        }
                    }
                }
                for (Outcome o : outcomes) o.diag().processNumber = o.diag().attemptId == null ? null : seedToLineage.get(o.diag().attemptId);
                List<Outcome> successes = new ArrayList<>();
                List<Outcome> failedAll = new ArrayList<>();
                for (Outcome o : outcomes) (o.result() != null ? successes : failedAll).add(o);
                accumulatedSuccesses.addAll(successes);
                Set<String> seenKeys = new HashSet<>();
                List<Outcome> failedUnique = new ArrayList<>();
                for (Outcome o : failedAll) {
                    if (seenKeys.add(outcomeKey(o.grid(), o.diag().assignment))) failedUnique.add(o);
                }
                for (Outcome o : failedAll) totalAttemptsTried += o.diag().checks == null ? 0 : o.diag().checks;
                if (accumulatedSuccesses.size() >= MIN_SUCCESSFUL_ATTEMPTS) {
                    List<Map<String, Object>> exs = new ArrayList<>();
                    for (Outcome o : accumulatedSuccesses) {
                        List<int[]> s = o.result().slots();
                        String[] a = o.result().assignment();
                        exs.add(example(Grids.buildLettersGrid(rows, cols, s, a), List.of(), List.of(), List.of(), List.of(),
                                List.of(), Fill.themeWordCells(s, a, priority),
                                Fill.challengeWordCellsFromAssignment(s, a, challenge), o.diag().processNumber, false));
                    }
                    progress.on("minimizing", data("count", accumulatedSuccesses.size(), "examples", new ArrayList<Object>(exs)));
                    List<Future<Object[]>> trials = new ArrayList<>();
                    for (Outcome o : accumulatedSuccesses) {
                        final char[][] gcopy = Grids.copy(o.grid());
                        final long ts = rng.seed31();
                        trials.add(executor.submit(() -> Fill.minimizeBlackSquares(gcopy, o.result(), rows, cols, index,
                                new Rng(ts), 6000, p.cancelEvent, properNouns, maxProperNouns, nonGloss, maxNonGloss,
                                priority, permanentLocked, permanentBlack, challenge)));
                    }
                    Object[] bestScored = null;
                    for (int k = 0; k < accumulatedSuccesses.size(); k++) {
                        Object[] opt;
                        try {
                            opt = trials.get(k).get();
                        } catch (ExecutionException ee) {
                            Throwable c = ee.getCause();
                            if (c instanceof RuntimeException re) throw re;
                            throw new IllegalStateException(c);
                        } catch (InterruptedException ie) {
                            Thread.currentThread().interrupt();
                            throw new GenerationCancelled();
                        }
                        char[][] og = (char[][]) opt[0];
                        @SuppressWarnings("unchecked")
                        List<int[]> os = (List<int[]>) opt[1];
                        String[] oa = (String[]) opt[2];
                        int ob = Grids.countBlack(og);
                        long osc = Fill.contentScore(oa, os, priority, challenge);
                        if (bestScored == null || ob < (int) bestScored[0] || (ob == (int) bestScored[0] && -osc < (long) bestScored[1])) {
                            bestScored = new Object[]{ob, -osc, accumulatedSuccesses.get(k), opt};
                        }
                    }
                    Outcome bo = (Outcome) bestScored[2];
                    best = bo.grid();
                    bestResult = bo.result();
                    bestDiag = bo.diag();
                    bestMinimized = (Object[]) bestScored[3];
                    break;
                }
                final Map<Outcome, Double> cleanedScores = new HashMap<>();
                for (Outcome o : failedUnique) {
                    cleanedScores.put(o, Cleanup.cleanedPlayableScore(o.grid(), o.diag(), rows, cols, index, rng, priority, challenge));
                }
                List<Outcome> failedPairs = new ArrayList<>(failedUnique);
                failedPairs.sort((a, b) -> Double.compare(cleanedScores.get(b), cleanedScores.get(a)));
                lastDiag = failedPairs.get(0).diag();
                try {
                    Thread.sleep((long) (2 * BEST_STATE_QUEUE_DRAIN_GRACE_S * 1000));
                } catch (InterruptedException e) {
                    Thread.currentThread().interrupt();
                }
                List<Diag> published;
                synchronized (bestStateBuffer) {
                    published = new ArrayList<>(bestStateBuffer);
                    bestStateBuffer.clear();
                }
                Set<String> displaySeen = new HashSet<>(seenKeys);
                List<Object[]> displayUnique = new ArrayList<>();
                for (Outcome o : failedUnique) displayUnique.add(new Object[]{o.grid(), o.diag()});
                for (Diag pub : published) {
                    char[][] pg = pub.grid;
                    pub.grid = null;
                    pub.processNumber = pub.attemptId == null ? null : seedToLineage.get(pub.attemptId);
                    if (displaySeen.add(outcomeKey(pg, pub.assignment))) displayUnique.add(new Object[]{pg, pub});
                }
                LinkedHashMap<Object, Object[]> bestByAttempt = new LinkedHashMap<>();
                for (int idx = 0; idx < displayUnique.size(); idx++) {
                    char[][] g = (char[][]) displayUnique.get(idx)[0];
                    Diag d = (Diag) displayUnique.get(idx)[1];
                    Object key = d.attemptId != null ? (Object) d.attemptId : (Object) ("__no_attempt_id__" + idx);
                    double score = Cleanup.playableScore(g, d, rows, cols, priority, challenge);
                    Object[] cur = bestByAttempt.get(key);
                    if (cur == null || score > (double) cur[0]) bestByAttempt.put(key, new Object[]{score, g, d});
                }
                Outcome winner = failedPairs.get(0);
                String winnerKey = outcomeKey(winner.grid(), winner.diag().assignment);
                Long winnerAttempt = winner.diag().attemptId;
                List<Object[]> rest = new ArrayList<>();
                for (Object[] e : bestByAttempt.values()) {
                    char[][] g = (char[][]) e[1];
                    Diag d = (Diag) e[2];
                    if (outcomeKey(g, d.assignment).equals(winnerKey)) continue;
                    if (winnerAttempt != null && winnerAttempt.equals(d.attemptId)) continue;
                    rest.add(e);
                }
                rest.sort((a, b) -> Double.compare((double) b[0], (double) a[0]));
                List<Diag> displayDiags = new ArrayList<>();
                displayDiags.add(winner.diag());
                for (Object[] e : rest) displayDiags.add((Diag) e[2]);
                List<Map<String, Object>> exs = new ArrayList<>();
                for (int idx = 0; idx < displayDiags.size(); idx++) {
                    Diag d = displayDiags.get(idx);
                    Map<String, Object> m = new LinkedHashMap<>();
                    m.put("example_grid", Grids.toJson(d.exampleGrid));
                    m.put("impossible_cells", Cells.toJson(d.impossibleCells));
                    m.put("deadlock_cells", Cells.toJson(d.deadlockCells == null ? List.of() : d.deadlockCells));
                    m.put("excluded_cells", Cells.toJson(d.excludedCells == null ? List.of() : d.excludedCells));
                    m.put("stat_letters", d.statLetters == null ? List.of() : d.statLetters);
                    m.put("forced_cells", Cells.toJson(d.forcedCells));
                    m.put("locked_cells", Cells.toJson(d.lockedCells == null ? List.of() : d.lockedCells));
                    m.put("theme_cells", Cells.toJson(d.themeCells == null ? List.of() : d.themeCells));
                    m.put("challenge_cells", Cells.toJson(d.challengeCells == null ? List.of() : d.challengeCells));
                    m.put("process_number", d.processNumber);
                    m.put("is_best", idx == 0);
                    exs.add(m);
                }
                lastExamples = sortExamplesByProcess(exs);
                Map<String, Object> failData = data("attempt", attempt + 1, "attempts", p.attempts, "ratio",
                        Math.rint(ratio * 1000) / 1000.0, "total_attempts", totalAttemptsTried, "examples", lastExamples);
                failData.putAll(lastDiag.toJson(true));
                progress.on("pattern_attempt_failed", failData);
                char[][] selGrid = winner.grid();
                Diag selDiag = winner.diag();
                Object[] plugged = Cleanup.plugIsolatedCells(selGrid, rows, cols, Grids.extractSlots(selGrid, rows, cols),
                        selDiag.assignment, index, permanentLocked);
                if (plugged != null) {
                    best = (char[][]) plugged[0];
                    @SuppressWarnings("unchecked")
                    List<int[]> ps = (List<int[]>) plugged[1];
                    bestResult = new Fill.Result(ps, (String[]) plugged[2]);
                    break;
                }
                Set<Integer> selImpossible = new HashSet<>(selDiag.impossibleSlots);
                List<int[]> selSlots = Grids.extractSlots(selGrid, rows, cols);
                Set<Integer> selDead = new HashSet<>(selImpossible);
                selDead.addAll(slotsTouching(selSlots, selImpossible));
                boolean stillHasHope = false;
                for (int i = 0; i < selDiag.assignment.length; i++) {
                    if (selDiag.assignment[i] == null && !selDead.contains(i)) {
                        stillHasHope = true;
                        break;
                    }
                }
                if (!failedAll.isEmpty()) {
                    boolean allAbandoned = true;
                    for (Outcome o : failedAll) if (!"abandoned_too_unfillable".equals(o.diag().reason)) allAbandoned = false;
                    if (allAbandoned) stillHasHope = false;
                }
                if (consecutiveContinue >= MAX_CONSECUTIVE_CONTINUE_PALIERS) stillHasHope = false;
                progress.on("pre_cleanup_optimizing", data("attempt", attempt + 1, "attempts", p.attempts,
                        "total_attempts", totalAttemptsTried));
                List<Object[]> optimized = new ArrayList<>();
                for (Outcome o : failedPairs) {
                    optimized.add(Cleanup.optimizeBeforeCleanup(o.grid(), o.diag(), rows, cols, index, rng, 6000,
                            p.cancelEvent, permanentLocked, permanentBlack, challenge));
                }
                List<Map<String, Object>> optEx = new ArrayList<>();
                for (int idx = 0; idx < optimized.size(); idx++) {
                    char[][] g = (char[][]) optimized.get(idx)[0];
                    Diag d = (Diag) optimized.get(idx)[1];
                    Diag cd = failedPairs.get(idx).diag();
                    List<int[]> gs = Grids.extractSlots(g, rows, cols);
                    List<Integer> imp = new ArrayList<>();
                    for (int i : d.impossibleSlots) for (int c : gs.get(i)) imp.add(c);
                    Map<String, Object> m = new LinkedHashMap<>();
                    m.put("example_grid", Grids.toJson(d.exampleGrid));
                    m.put("impossible_cells", Cells.toJson(imp));
                    m.put("forced_cells", Cells.toJson(cd.forcedCells == null ? List.of() : cd.forcedCells));
                    m.put("locked_cells", Cells.toJson(d.lockedCells == null ? List.of() : d.lockedCells));
                    m.put("theme_cells", Cells.toJson(cd.themeCells == null ? List.of() : cd.themeCells));
                    m.put("challenge_cells", Cells.toJson(cd.challengeCells == null ? List.of() : cd.challengeCells));
                    m.put("process_number", d.processNumber);
                    m.put("is_best", idx == 0);
                    optEx.add(m);
                }
                progress.on("pre_cleanup_optimized", data("attempt", attempt + 1, "attempts", p.attempts,
                        "total_attempts", totalAttemptsTried, "examples", sortExamplesByProcess(optEx)));
                if (stillHasHope) {
                    consecutiveContinue++;
                    justCleaned = false;
                    List<Cleanup.ContinueCandidate> cc = new ArrayList<>();
                    for (Object[] o : optimized) {
                        cc.add(Cleanup.cleanContinueCandidate((char[][]) o[0], (Diag) o[1], rows, cols, index, rng,
                                permanentLocked, permanentBlack, challenge));
                    }
                    cc = sortContinue(cc, priority, challenge);
                    int keep = Cleanup.seedPoolKeep(cc.size(), PA, FULL_RESET_ATTEMPT_COUNT);
                    carrySeedPoolContinue = new ArrayList<>();
                    List<Integer> rawLineage = new ArrayList<>();
                    for (int k = 0; k < keep && k < cc.size(); k++) {
                        Cleanup.ContinueCandidate c = cc.get(k);
                        carrySeedPoolContinue.add(new Object[]{c.seedGrid(), c.preseed(), c.excluded()});
                        rawLineage.add(c.processNumber());
                    }
                    carrySeedGrid = (char[][]) carrySeedPoolContinue.get(0)[0];
                    carryPreseed = (String[]) carrySeedPoolContinue.get(0)[1];
                    @SuppressWarnings("unchecked")
                    Set<Integer> ex0 = (Set<Integer>) carrySeedPoolContinue.get(0)[2];
                    carryExcluded = ex0;
                    carryLocked = null;
                    Object[] rl = Cleanup.reassignLineageNumbers(rawLineage, dispatchLineage, nextLineage);
                    @SuppressWarnings("unchecked")
                    List<Integer> lin = (List<Integer>) rl[0];
                    carrySeedPoolContinueLineage = lin;
                    nextLineage = (int) rl[1];
                } else {
                    consecutiveContinue = 0;
                    carryPreseed = null;
                    carryExcluded = null;
                    Map<String, Integer> cleanupStreaks = new HashMap<>();
                    List<Object[]> kept = new ArrayList<>();
                    int discarded = 0;
                    for (Object[] o : optimized) {
                        char[][] cg = (char[][]) o[0];
                        Diag cd = (Diag) o[1];
                        Object[] cand = cleanCandidate(cg, cd, rows, cols, carryLocked, carrySeedGrid, index, rng,
                                permanentLocked, permanentBlack, false);
                        @SuppressWarnings("unchecked")
                        Map<Integer, Character> conf = (Map<Integer, Character>) cand[1];
                        String key = Grids.key((char[][]) Fill.cycleStartPreview(rows, cols, (char[][]) cand[0], conf, null)[0]);
                        int streak = carryCleanupStreaks.getOrDefault(key, 0) + 1;
                        cleanupStreaks.put(key, Math.max(cleanupStreaks.getOrDefault(key, 0), streak));
                        if (streak >= GRID_REPEAT_DISCARD_STREAK) {
                            discarded++;
                            continue;
                        }
                        if (streak >= GRID_REPEAT_DEEP_CLEANUP_STREAK) {
                            cand = cleanCandidate(cg, cd, rows, cols, carryLocked, carrySeedGrid, index, rng,
                                    permanentLocked, permanentBlack, true);
                        }
                        kept.add(cand);
                    }
                    carryCleanupStreaks = cleanupStreaks;
                    justCleaned = true;
                    if (!kept.isEmpty()) {
                        List<Object[]> sorted = sortCleaned(kept, priority, challenge);
                        int nextReset = Math.min(PA, FULL_RESET_ATTEMPT_COUNT + discarded);
                        int keep = Cleanup.seedPoolKeep(sorted.size(), PA, nextReset);
                        carrySeedPool = new ArrayList<>();
                        List<Integer> rawLineage = new ArrayList<>();
                        for (int k = 0; k < keep && k < sorted.size(); k++) {
                            carrySeedPool.add(new Object[]{sorted.get(k)[0], sorted.get(k)[1]});
                            rawLineage.add((Integer) sorted.get(k)[3]);
                        }
                        carrySeedGrid = (char[][]) carrySeedPool.get(0)[0];
                        @SuppressWarnings("unchecked")
                        Map<Integer, Character> cl = (Map<Integer, Character>) carrySeedPool.get(0)[1];
                        carryLocked = cl;
                        Object[] rl = Cleanup.reassignLineageNumbers(rawLineage, dispatchLineage, nextLineage);
                        @SuppressWarnings("unchecked")
                        List<Integer> lin = (List<Integer>) rl[0];
                        carrySeedPoolLineage = lin;
                        nextLineage = (int) rl[1];
                        carryDiscardedCount = discarded;
                    } else {
                        carrySeedGrid = null;
                        carryLocked = null;
                        carryPreseed = null;
                        carryExcluded = null;
                        carrySeedPool = null;
                        carrySeedPoolContinue = null;
                        carrySeedPoolLineage = null;
                        carrySeedPoolContinueLineage = null;
                        consecutiveContinue = 0;
                        carryCleanupStreaks = new HashMap<>();
                        carryDiscardedCount = 0;
                    }
                }
            }
        } finally {
            stopDrain.set(true);
            executor.shutdownNow();
            try {
                drain.join(1000);
            } catch (InterruptedException ignored) {
                Thread.currentThread().interrupt();
            }
        }
        if (p.onLivePreview != null) p.onLivePreview.accept(new ArrayList<>());
        if (best == null && !accumulatedSuccesses.isEmpty()) {
            Outcome o = accumulatedSuccesses.get(0);
            best = o.grid();
            bestResult = o.result();
            bestDiag = o.diag();
        }
        if (best == null) {
            Map<String, Object> resume = carrySeedGrid != null
                    ? serializeResumeState(carrySeedGrid, carryLocked, carryPreseed, carryExcluded) : null;
            progress.on("pattern_failed", data("attempts", p.attempts, "last_attempt",
                    lastDiag != null ? lastDiag.toJson(true) : null, "examples", lastExamples, "total_attempts",
                    totalAttemptsTried, "resume_state", resume));
            return null;
        }
        progress.on("pattern_found", data("attempt", attempt + 1, "total_attempts", totalAttemptsTried));
        Integer winningProcess = bestDiag != null ? bestDiag.processNumber : null;
        char[][] grid;
        List<int[]> slots;
        String[] assignment;
        if (bestMinimized != null) {
            grid = (char[][]) bestMinimized[0];
            @SuppressWarnings("unchecked")
            List<int[]> s = (List<int[]>) bestMinimized[1];
            slots = s;
            assignment = (String[]) bestMinimized[2];
        } else {
            List<int[]> bs = bestResult.slots();
            String[] ba = bestResult.assignment();
            progress.on("minimizing", data("examples", Json.list(example(Grids.buildLettersGrid(rows, cols, bs, ba),
                    List.of(), List.of(), List.of(), List.of(), List.of(), Fill.themeWordCells(bs, ba, priority),
                    Fill.challengeWordCellsFromAssignment(bs, ba, challenge), winningProcess, true))));
            Object[] m = Fill.minimizeBlackSquares(best, bestResult, rows, cols, index, rng, 6000, p.cancelEvent,
                    properNouns, maxProperNouns, nonGloss, maxNonGloss, priority, permanentLocked, permanentBlack,
                    challenge);
            grid = (char[][]) m[0];
            @SuppressWarnings("unchecked")
            List<int[]> s = (List<int[]>) m[1];
            slots = s;
            assignment = (String[]) m[2];
        }
        int nBlack = Grids.countBlack(grid);
        List<Map<String, Object>> words = new ArrayList<>();
        for (Map<String, Object> w : Fill.buildWordEntries(grid, rows, cols, slots, assignment)) {
            if (w.get("answer") != null) words.add(w);
        }
        for (Map<String, Object> w : words) {
            String ans = (String) w.get("answer");
            Words.Lexicon lex = bilingual && "down".equals(w.get("direction")) ? down.lexicon() : across.lexicon();
            String acc = lex.accents().getOrDefault(ans, ans);
            w.put("accented", acc);
            w.put("canonical", new ArrayList<>(lex.canonicals().getOrDefault(ans, List.of(acc))));
            w.put("language", bilingual && "down".equals(w.get("direction")) ? bilingualLanguage : language);
        }
        progress.on("grid_ready", data("word_count", slots.size(), "black_count", nBlack));
        char[][] solution = Grids.buildLettersGrid(rows, cols, slots, assignment);
        permanentLocked.forEach((cell, ch) -> solution[Cells.r(cell)][Cells.c(cell)] = ch);
        Map<String, Object> out = new LinkedHashMap<>();
        out.put("width", cols);
        out.put("height", rows);
        out.put("pattern", Grids.toJson(grid));
        out.put("solution", Grids.toJson(solution));
        out.put("words", new ArrayList<Object>(words));
        out.put("word_count", slots.size());
        out.put("black_count", nBlack);
        out.put("black_ratio", (double) nBlack / (rows * cols));
        out.put("winning_process_number", winningProcess);
        out.put("language", language);
        out.put("bilingual_language", bilingualLanguage);
        return out;
    }

    static Object[] cleanCandidate(char[][] cg, Diag cd, int rows, int cols, Map<Integer, Character> carryLocked,
                                   char[][] carrySeed, DualIndex index, Rng rng, Map<Integer, Character> permanentLocked,
                                   Set<Integer> permanentBlack, boolean deep) {
        List<int[]> slots = Grids.extractSlots(cg, rows, cols);
        Object[] seed = Cleanup.buildRetrySeed(cg, rows, cols, slots, cd.assignment, cd.impossibleSlots, carryLocked, deep,
                carrySeed, index, rng, permanentLocked, permanentBlack, deep);
        return new Object[]{seed[0], seed[1], slots, cd.processNumber};
    }

    static List<Object[]> sortCleaned(List<Object[]> cands, PW pw, Set<String> challenge) {
        List<Object[]> out = new ArrayList<>(cands);
        Map<Object[], long[]> keys = new HashMap<>();
        for (Object[] sc : out) {
            @SuppressWarnings("unchecked")
            List<int[]> s = (List<int[]>) sc[2];
            @SuppressWarnings("unchecked")
            Map<Integer, Character> conf = (Map<Integer, Character>) sc[1];
            keys.put(sc, new long[]{Cleanup.wordsInPlaceScore(s, conf, pw, challenge), Grids.countBlack((char[][]) sc[0])});
        }
        out.sort((a, b) -> {
            long[] ka = keys.get(a), kb = keys.get(b);
            int cmp = Long.compare(kb[0], ka[0]);
            return cmp != 0 ? cmp : Long.compare(kb[1], ka[1]);
        });
        return out;
    }

    static List<Cleanup.ContinueCandidate> sortContinue(List<Cleanup.ContinueCandidate> cands, PW pw, Set<String> challenge) {
        List<Cleanup.ContinueCandidate> out = new ArrayList<>(cands);
        Map<Cleanup.ContinueCandidate, long[]> keys = new HashMap<>();
        for (Cleanup.ContinueCandidate c : out) {
            keys.put(c, new long[]{Cleanup.wordsInPlaceScore(c.slots(), c.confirmed(), pw, challenge),
                    Grids.countBlack(c.seedGrid())});
        }
        out.sort((a, b) -> {
            long[] ka = keys.get(a), kb = keys.get(b);
            int cmp = Long.compare(kb[0], ka[0]);
            return cmp != 0 ? cmp : Long.compare(kb[1], ka[1]);
        });
        return out;
    }

    // ================================================================== CLI

    public static void main(String[] args) throws IOException {
        Params p = new Params();
        for (int i = 0; i < args.length; i++) {
            switch (args[i]) {
                case "--width" -> p.width = Integer.parseInt(args[++i]);
                case "--height" -> p.height = Integer.parseInt(args[++i]);
                case "--wordlist" -> p.wordlistPath = args[++i];
                case "--difficulty" -> p.difficulty = args[++i];
                case "--max-words" -> p.maxWords = Integer.parseInt(args[++i]);
                case "--black-ratio" -> p.blackRatio = Double.parseDouble(args[++i]);
                case "--attempts" -> p.attempts = Integer.parseInt(args[++i]);
                case "--seed" -> p.seed = Long.parseLong(args[++i]);
                case "--deadline-checks" -> p.deadlineChecks = Long.parseLong(args[++i]);
                default -> {
                    System.err.println("unknown option " + args[i]);
                    System.exit(2);
                }
            }
        }
        long t0 = System.currentTimeMillis();
        p.onProgress = (step, d) -> System.err.println("[" + (System.currentTimeMillis() - t0) / 1000.0 + "s] " + step
                + (d.containsKey("attempt") ? " attempt=" + d.get("attempt") : "")
                + (d.containsKey("reason") ? " reason=" + d.get("reason") : ""));
        Map<String, Object> r = generateGrid(p);
        if (r == null) {
            System.err.println("Échec : aucune grille remplissable trouvée en " + p.attempts + " essais.");
            System.exit(1);
        }
        System.out.printf(java.util.Locale.ROOT, "Grille %sx%s — %s cases noires (%.1f%%)%n%n", r.get("width"), r.get("height"),
                r.get("black_count"), 100 * (double) r.get("black_ratio"));
        for (String key : List.of("pattern", "solution")) {
            System.out.println(key.equals("pattern") ? "Motif :" : "\nSolution :");
            for (Object row : Json.asList(r.get(key))) {
                List<String> cells = new ArrayList<>();
                for (Object c : Json.asList(row)) cells.add(c.toString());
                System.out.println(String.join(" ", cells));
            }
        }
        System.out.println("\n" + r.get("word_count") + " mots placés.");
        System.exit(0);
    }
}
