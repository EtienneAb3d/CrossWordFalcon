package falcon.gen;

import java.util.ArrayDeque;
import java.util.ArrayList;
import java.util.BitSet;
import java.util.Comparator;
import java.util.HashMap;
import java.util.HashSet;
import java.util.LinkedHashSet;
import java.util.List;
import java.util.Map;
import java.util.Set;
import java.util.function.ToIntFunction;

/**
 * The "ingrid" fill method (mirrors crossword_gen.py's IngridSolver): a search modelled on the Ingrid crossword
 * filler (github.com/rf-/ingrid_core) — arc consistency (AC-3) maintained over every slot's whole domain, slot
 * choice by dom/wdeg with adaptive branching, binary branching (a word, then "not this word"), randomized restarts
 * under a growing backtrack limit, crossing weights learnt across restarts. Run by {@link Fill#tryFill} on its
 * Filler instead of {@link Filler#solve}: it reads the Filler's slots, lexicon, locked letters, already placed
 * words, candidate families and multi-word cap, and reports through the Filler's own fields and callbacks, so
 * tryFill builds its result and diagnostics exactly as for the "falcon" method.
 *
 * <p>The CSP covers every slot except excludedSlots and the slots already dry before the search, which stay
 * unfilled like a tolerated dry slot of Filler.solve. A slot already holding a word keeps it. Each slot's domain is
 * the lexicon's words fitting its known letters, minus the words already used, plus the "Mots Défi" words fitting
 * it ({@code extras} when outside the lexicon).
 */
public final class IngridSolver {
    /** The generation form's "Méthode" selector (mirrors FILL_METHODS / FILL_METHOD_DEFAULT). */
    public static final List<String> FILL_METHODS = List.of("falcon", "ingrid");
    public static final String FILL_METHOD_DEFAULT = "ingrid";
    public static final double ADAPTIVE_BRANCHING_THRESHOLD = 0.15;
    public static final double WEIGHT_AGE_FACTOR = 0.99;
    public static final int[] RANDOM_SLOT_WEIGHTS = {4, 2, 1};
    public static final int[] RANDOM_WORD_WEIGHTS = {32, 16, 8, 4, 2, 1};
    public static final int INITIAL_MAX_BACKTRACKS = 500;
    public static final double RETRY_GROWTH_FACTOR = 1.1;
    public static final double FILL_SCORE_WEIGHT = 900;
    public static final double USAGE_WEIGHT = 60;
    public static final double FREQ_WEIGHT = 20;
    public static final int WORDS_PER_CHECK = 10;

    private final Filler f;
    private final long deadline;
    private final int n;
    private final String[] initial;
    private final LenIndex[] idx;
    private final BitSet[] dom;
    private final Set<String>[] extraDom;
    private final int[] size;
    private final int[][][] counts;
    private final Set<String>[] extras;
    private final boolean[] active;
    /** Crossings between two slots of the CSP: {other slot, position here, position there, cell}. */
    private final List<int[]>[] cross;
    private final Map<Integer, Double> cellWeight = new HashMap<>();
    private final List<Removal> trail = new ArrayList<>();
    private final List<String>[] order;
    private int[][][] initCounts;
    private List<String>[] initDom;
    private int bestCount = -1;
    private boolean extensionDenied;
    private Long lastExtensionEval;
    /** No letter on the grid when the search starts (no locked letter, no word already placed): its first word is
     * chosen the "falcon" way (falconPick) (mirrors empty_root). */
    private final boolean emptyRoot;
    private long work;
    private final UsedWords lemmas;
    /** The two slots crossing at each weighted cell, as their cells: the key a crossing weight is kept under between
     * "Suivant" clicks (loadWeights/exportWeights). */
    private final Map<Integer, String> crossingKey = new HashMap<>();
    private int wiped = -1;
    /** The best 3 slots of the last chooseSlot. */
    List<Integer> lastWindow = new ArrayList<>();
    private final Map<String, Set<String>> blockerCache = new HashMap<>();
    private final int alpha;

    private record Removal(int slot, BitSet ids, List<String> extras, int[][] old) {}

    @SuppressWarnings("unchecked")
    public IngridSolver(Filler filler, long deadlineChecks) {
        this.f = filler;
        this.deadline = deadlineChecks;
        List<int[]> slots = filler.slots;
        n = slots.size();
        initial = filler.assignment.clone();
        idx = new LenIndex[n];
        dom = new BitSet[n];
        extraDom = new Set[n];
        size = new int[n];
        counts = new int[n][][];
        extras = new Set[n];
        active = new boolean[n];
        cross = new List[n];
        order = new List[n];
        lemmas = new UsedWords(filler.index);
        boolean anyInitial = false;
        for (String w : initial) if (w != null) anyInitial = true;
        emptyRoot = filler.lockedLetters.isEmpty() && !anyInitial;
        for (String w : filler.challengeWords) for (int p = 0; p < w.length(); p++) Alpha.id(w.charAt(p));
        alpha = Alpha.size();
        Set<String> used = filler.usedWords == null ? Set.of() : filler.usedWords;
        Set<Integer> excluded = filler.excludedSlots == null ? Set.of() : filler.excludedSlots;
        for (int i = 0; i < n; i++) {
            int[] cells = slots.get(i);
            idx[i] = filler.index.forCells(cells).get(cells.length);
            dom[i] = new BitSet();
            extraDom[i] = new LinkedHashSet<>();
            extras[i] = Set.of();
            cross[i] = new ArrayList<>();
            if (excluded.contains(i)) continue;
            String word = filler.assignment[i];
            if (word != null) {
                if (inLexicon(i, word)) dom[i].set(idx[i].ids.get(word));
                else {
                    extras[i] = Set.of(word);
                    extraDom[i].add(word);
                }
            } else {
                Set<String> ex = new LinkedHashSet<>();
                for (String w : filler.challengeWords) {
                    if (w.length() == cells.length && !used.contains(w) && !inLexicon(i, w)
                            && filler.challengeWordFits(i, w)) ex.add(w);
                }
                extras[i] = ex;
                dom[i] = initialDomain(i, used);
                extraDom[i].addAll(ex);
            }
            size[i] = dom[i].cardinality() + extraDom[i].size();
            if (size[i] == 0) continue;
            active[i] = true;
            counts[i] = counted(i);
        }
        for (int i = 0; i < n; i++) {
            if (!active[i]) continue;
            int[] cells = slots.get(i);
            for (int p = 0; p < cells.length; p++) {
                int j = filler.crossSlot[i][p];
                if (j >= 0 && active[j]) {
                    cross[i].add(new int[]{j, p, filler.crossPos[i][p], cells[p]});
                    cellWeight.put(cells[p], 1.0);
                    String a = java.util.Arrays.toString(cells), b = java.util.Arrays.toString(slots.get(j));
                    crossingKey.put(cells[p], a.compareTo(b) <= 0 ? a + "|" + b : b + "|" + a);
                }
            }
        }
    }

    boolean isActive(int i) {
        return active[i];
    }

    int domainSize(int i) {
        return size[i];
    }

    boolean inDomain(int i, String w) {
        return contains(i, w);
    }

    int trailMark() {
        return trail.size();
    }

    private boolean inLexicon(int i, String word) {
        return idx[i] != null && idx[i].contains(word);
    }

    private BitSet initialDomain(int i, Set<String> used) {
        Dom d = f.domain(i, true);
        BitSet out = new BitSet();
        if (d.idx == null) return out;
        for (int id : d.ids()) out.set(id);
        for (String w : used) {
            if (w.length() != d.idx.length) continue;
            Integer id = d.idx.ids.get(w);
            if (id != null) out.clear(id);
        }
        return out;
    }

    private int[][] counted(int i) {
        int length = f.slots.get(i).length;
        int[][] c = new int[length][alpha];
        BitSet d = dom[i];
        if (idx[i] != null) {
            String[] words = idx[i].words;
            for (int id = d.nextSetBit(0); id >= 0; id = d.nextSetBit(id + 1)) {
                String w = words[id];
                for (int p = 0; p < length; p++) c[p][Alpha.id(w.charAt(p))]++;
            }
        }
        for (String w : extraDom[i]) for (int p = 0; p < length; p++) c[p][Alpha.id(w.charAt(p))]++;
        return c;
    }

    /** {@code word} and every word sharing a canonical form with it (the words its placement makes unavailable). */
    private Set<String> blockers(String word) {
        Set<String> out = blockerCache.get(word);
        if (out == null) {
            out = lemmas.blockersOf(word);
            blockerCache.put(word, out);
        }
        return out;
    }

    /** Takes the words {@code ids} and {@code ex} (all members of slot {@code i}'s domain) out of it, on the trail,
     * keeping its per-position letter counts. */
    private void remove(int i, BitSet ids, List<String> ex) {
        int[][] old = counts[i];
        trail.add(new Removal(i, ids, ex, old));
        int removed = ids.cardinality() + ex.size();
        dom[i].andNot(ids);
        extraDom[i].removeAll(ex);
        size[i] -= removed;
        int length = old.length;
        if (removed <= size[i]) {
            int[][] c = new int[length][];
            for (int p = 0; p < length; p++) c[p] = old[p].clone();
            if (idx[i] != null) {
                String[] words = idx[i].words;
                for (int id = ids.nextSetBit(0); id >= 0; id = ids.nextSetBit(id + 1)) {
                    String w = words[id];
                    for (int p = 0; p < length; p++) c[p][Alpha.id(w.charAt(p))]--;
                }
            }
            for (String w : ex) for (int p = 0; p < length; p++) c[p][Alpha.id(w.charAt(p))]--;
            counts[i] = c;
            work += removed;
        } else {
            counts[i] = counted(i);
            work += size[i];
        }
    }

    void undoTo(int mark) {
        while (trail.size() > mark) {
            Removal r = trail.remove(trail.size() - 1);
            dom[r.slot].or(r.ids);
            extraDom[r.slot].addAll(r.extras);
            size[r.slot] += r.ids.cardinality() + r.extras.size();
            counts[r.slot] = r.old;
        }
    }

    private String single(int i) {
        if (!extraDom[i].isEmpty()) return extraDom[i].iterator().next();
        return idx[i].words[dom[i].nextSetBit(0)];
    }

    private boolean contains(int i, String w) {
        if (w.length() != f.slots.get(i).length) return false;
        if (extraDom[i].contains(w)) return true;
        if (idx[i] == null) return false;
        Integer id = idx[i].ids.get(w);
        return id != null && dom[i].get(id);
    }

    /** AC-3 from {@code queueSlots} and the no-repeat rule from {@code singletons} (mirrors IngridSolver._propagate):
     * null, or on a domain wipe-out the crossing-weight increments it learns. */
    Map<Integer, Double> propagate(List<Integer> queueSlots, List<Integer> singletons) {
        ArrayDeque<Integer> queue = new ArrayDeque<>(queueSlots);
        Set<Integer> queued = new HashSet<>(queueSlots);
        List<Integer> pending = new ArrayList<>(singletons);
        Map<Integer, Integer> startSize = new HashMap<>();
        Map<Long, Integer> blame = new HashMap<>();
        while (!queue.isEmpty() || !pending.isEmpty()) {
            while (!pending.isEmpty()) {
                int s = pending.remove(pending.size() - 1);
                if (size[s] != 1) continue;
                Set<String> blk = blockers(single(s));
                for (int j = 0; j < n; j++) {
                    if (j == s || !active[j]) continue;
                    BitSet ids = new BitSet();
                    List<String> ex = new ArrayList<>();
                    int length = f.slots.get(j).length;
                    for (String w : blk) {
                        if (w.length() != length) continue;
                        if (extraDom[j].contains(w)) ex.add(w);
                        else if (idx[j] != null) {
                            Integer id = idx[j].ids.get(w);
                            if (id != null && dom[j].get(id)) ids.set(id);
                        }
                    }
                    if (ids.isEmpty() && ex.isEmpty()) continue;
                    startSize.putIfAbsent(j, size[j]);
                    remove(j, ids, ex);
                    if (size[j] == 0) return wipeout(j, startSize, blame);
                    if (size[j] == 1) pending.add(j);
                    if (queued.add(j)) queue.add(j);
                }
            }
            if (queue.isEmpty()) break;
            int b = queue.poll();
            queued.remove(b);
            int[][] countsB = counts[b];
            for (int[] c : cross[b]) {
                int a = c[0], pb = c[1], pa = c[2];
                int[] lettersB = countsB[pb];
                int[] lettersA = counts[a][pa];
                BitSet ids = null;
                List<String> ex = new ArrayList<>();
                for (int l = 0; l < alpha; l++) {
                    if (lettersA[l] <= 0 || lettersB[l] > 0) continue;
                    if (idx[a] != null && l < idx[a].pos[pa].length) {
                        BitSet s = idx[a].pos[pa][l];
                        if (s != null) {
                            BitSet t = (BitSet) s.clone();
                            t.and(dom[a]);
                            if (ids == null) ids = t;
                            else ids.or(t);
                        }
                    }
                    for (String w : extraDom[a]) if (Alpha.id(w.charAt(pa)) == l) ex.add(w);
                }
                int removed = (ids == null ? 0 : ids.cardinality()) + ex.size();
                if (removed == 0) continue;
                startSize.putIfAbsent(a, size[a]);
                blame.merge(((long) a << 16) | pa, removed, Integer::sum);
                remove(a, ids == null ? new BitSet() : ids, ex);
                if (size[a] == 0) return wipeout(a, startSize, blame);
                if (size[a] == 1) pending.add(a);
                if (queued.add(a)) queue.add(a);
            }
        }
        return null;
    }

    private Map<Integer, Double> wipeout(int a, Map<Integer, Integer> startSize, Map<Long, Integer> blame) {
        wiped = a;
        int start = startSize.getOrDefault(a, 1);
        if (start == 0) start = 1;
        Map<Integer, Double> out = new HashMap<>();
        for (int[] c : cross[a]) out.put(c[3], blame.getOrDefault(((long) a << 16) | c[1], 0) / (double) start);
        return out;
    }

    /** Ages every crossing weight and adds a wipe-out's increments. */
    void learn(Map<Integer, Double> updates) {
        for (Map.Entry<Integer, Double> e : cellWeight.entrySet()) {
            e.setValue(1.0 + (e.getValue() - 1.0) * WEIGHT_AGE_FACTOR + updates.getOrDefault(e.getKey(), 0.0));
        }
    }

    private double slotWeight(int i) {
        double w = 0.0;
        for (int[] c : cross[i]) if (size[c[0]] > 1) w += cellWeight.get(c[3]);
        return w;
    }

    private int weightedPick(int[] weights, int k) {
        double total = 0;
        for (int i = 0; i < k; i++) total += weights[i];
        double x = f.rng.random() * total, cum = 0;
        for (int i = 0; i < k; i++) {
            cum += weights[i];
            if (x < cum) return i;
        }
        return k - 1;
    }

    /** Takes back the crossing weights exportWeights kept, for the crossings of this grid whose two slots are
     * unchanged (mirrors IngridSolver.load_weights). */
    void loadWeights(Map<String, Double> weights) {
        if (weights == null) return;
        for (Map.Entry<Integer, String> e : crossingKey.entrySet()) {
            Double w = weights.get(e.getValue());
            if (w != null) cellWeight.put(e.getKey(), w);
        }
    }

    /** Every crossing weight of this grid by crossing key — a crossing whose slots changed is left behind (mirrors
     * IngridSolver.export_weights). */
    Map<String, Double> exportWeights() {
        Map<String, Double> out = new HashMap<>();
        for (Map.Entry<Integer, Double> e : cellWeight.entrySet()) out.put(crossingKey.get(e.getKey()), e.getValue());
        return out;
    }

    /** Initial propagation that sets aside every slot it would leave with no word, until the remaining slots agree
     * (mirrors IngridSolver.settle). Returns the slots dropped. */
    List<Integer> settle() {
        List<Integer> dropped = new ArrayList<>();
        while (true) {
            List<Integer> act = new ArrayList<>();
            List<Integer> singletons = new ArrayList<>();
            for (int i = 0; i < n; i++) {
                if (!active[i]) continue;
                act.add(i);
                if (size[i] == 1) singletons.add(i);
            }
            int mark = trail.size();
            Map<Integer, Double> updates = propagate(act, singletons);
            if (updates == null) return dropped;
            learn(updates);
            undoTo(mark);
            dropped.add(wiped);
            dropSlot(wiped);
        }
    }

    private void dropSlot(int x) {
        active[x] = false;
        for (int[] c : cross[x]) cross[c[0]].removeIf(e -> e[0] == x);
        cross[x] = new ArrayList<>();
    }

    /** The domains and counts the static word order is computed from: the current ones (mirrors freeze_initial). */
    @SuppressWarnings("unchecked")
    void freezeInitial() {
        initDom = new List[n];
        initCounts = new int[n][][];
        for (int i = 0; i < n; i++) {
            List<String> words = new ArrayList<>();
            if (idx[i] != null) {
                for (int id = dom[i].nextSetBit(0); id >= 0; id = dom[i].nextSetBit(id + 1)) words.add(idx[i].words[id]);
            }
            words.addAll(extraDom[i]);
            initDom[i] = words;
            initCounts[i] = counts[i];
        }
    }

    /** The slot to fill next (mirrors IngridSolver._choose_slot); -1 once every slot of the CSP holds one word. */
    private int chooseSlot(int last) {
        return chooseSlot(last, null);
    }

    /** {@code pool}: the slots to choose among instead of every open slot. The best 3 are left in lastWindow. */
    int chooseSlot(int last, List<Integer> poolIn) {
        List<Integer> pool = poolIn;
        if (pool == null) {
            pool = new ArrayList<>();
            for (int i = 0; i < n; i++) if (active[i] && size[i] > 1) pool.add(i);
        }
        if (pool.isEmpty()) return -1;
        if (!f.challengeWords.isEmpty()) {
            List<Integer> fitting = new ArrayList<>();
            for (int i : pool) {
                for (String w : f.challengeWords) {
                    if (contains(i, w)) {
                        fitting.add(i);
                        break;
                    }
                }
            }
            if (!fitting.isEmpty()) pool = fitting;
        }
        List<double[]> prio = new ArrayList<>();
        for (int i : pool) {
            double weight = slotWeight(i);
            prio.add(new double[]{weight > 0 ? size[i] / weight : Double.POSITIVE_INFINITY, i});
        }
        prio.sort(Comparator.<double[]>comparingDouble(p -> p[0]).thenComparingDouble(p -> p[1]));
        lastWindow = new ArrayList<>();
        for (int k = 0; k < Math.min(prio.size(), RANDOM_SLOT_WEIGHTS.length); k++) lastWindow.add((int) prio.get(k)[1]);
        if (last >= 0) {
            for (double[] p : prio) {
                if ((int) p[1] == last) {
                    if (p[0] - prio.get(0)[0] < ADAPTIVE_BRANCHING_THRESHOLD) return last;
                    break;
                }
            }
        }
        int k = Math.min(prio.size(), RANDOM_SLOT_WEIGHTS.length);
        return (int) prio.get(weightedPick(RANDOM_SLOT_WEIGHTS, k))[1];
    }

    /** Slot {@code i}'s candidate family of a word: 0 "Mots Défi", 1 theme glossary, 2 Scrabble dictionary, 3 the
     * rest. */
    ToIntFunction<String> familyOf(int i) {
        int[] cells = f.slots.get(i);
        Set<String> challenge = f.challengeWords;
        Set<String> theme = f.priorityWords.forCells(cells);
        Set<String> scrabble = f.scrabbleWords.forCells(cells);
        return w -> challenge.contains(w) ? 0 : theme.contains(w) ? 1 : scrabble.contains(w) ? 2 : 3;
    }

    /** Slot {@code i}'s static word order (mirrors IngridSolver._order). */
    List<String> order(int i) {
        if (order[i] != null) return order[i];
        int[] cells = f.slots.get(i);
        int length = cells.length;
        int[][] crossing = new int[length][];
        for (int[] c : cross[i]) crossing[c[1]] = initCounts[c[0]][c[2]];
        LenIndex li = idx[i];
        ToIntFunction<String> family = familyOf(i);
        List<String> words = initDom[i];
        int m = words.size();
        int[] fam = new int[m];
        double[] score = new double[m];
        for (int k = 0; k < m; k++) {
            String w = words.get(k);
            double fill = 0.0;
            for (int p = 0; p < length; p++) {
                int[] c = crossing[p];
                if (c == null) continue;
                int cnt = c[Alpha.id(w.charAt(p))];
                fill += cnt > 0 ? Math.log10(cnt) : -1e9;
            }
            double s = FILL_SCORE_WEIGHT * fill / length;
            if (li != null) {
                int u = li.usageOf(w);
                if (u != Words.USAGE_SCORE_UNKNOWN) s += USAGE_WEIGHT * u;
                s += FREQ_WEIGHT * Math.log10(1.0 + li.dictFreqOf(w));
            }
            fam[k] = family.applyAsInt(w);
            score[k] = s;
        }
        Integer[] ix = new Integer[m];
        for (int k = 0; k < m; k++) ix[k] = k;
        java.util.Arrays.sort(ix, (x, y) -> {
            if (fam[x] != fam[y]) return Integer.compare(fam[x], fam[y]);
            int c = Double.compare(-score[x], -score[y]);
            return c != 0 ? c : words.get(x).compareTo(words.get(y));
        });
        List<String> out = new ArrayList<>(m);
        for (int k : ix) out.add(words.get(k));
        order[i] = out;
        return out;
    }

    /** {slot, word or null} of the first word on an empty grid, chosen the "falcon" way for diversity: the slot by
     * Filler.selectTargetSlot among {@code pool}, the word as the first of Filler.orderedCandidates with the candidate
     * families pulled ahead (mirrors IngridSolver._falcon_pick). */
    Object[] falconPick(List<Integer> pool) {
        Map<Integer, Dom> domains = new java.util.LinkedHashMap<>();
        for (int j : pool) domains.put(j, new Dom(idx[j], (BitSet) dom[j].clone()));
        int i = f.selectTargetSlot(pool, domains);
        lastWindow = new ArrayList<>(f.lastSelectionWindow);
        List<String> words = new ArrayList<>();
        if (idx[i] != null) {
            for (int id = dom[i].nextSetBit(0); id >= 0; id = dom[i].nextSetBit(id + 1)) words.add(idx[i].words[id]);
        }
        List<String> cands = new ArrayList<>(f.orderedCandidates(i, words));
        cands.addAll(extraDom[i]);
        ToIntFunction<String> family = familyOf(i);
        cands.sort(Comparator.comparingInt(family));
        return new Object[]{i, cands.isEmpty() ? null : cands.get(0)};
    }

    private String[] currentAssignment() {
        String[] out = new String[n];
        for (int i = 0; i < n; i++) out[i] = active[i] ? (size[i] == 1 ? single(i) : null) : initial[i];
        return out;
    }

    private void record() {
        int count = 0;
        for (int i = 0; i < n; i++) if (active[i] && size[i] == 1) count++;
        if (count <= bestCount) return;
        bestCount = count;
        String[] asg = currentAssignment();
        int assigned = 0;
        for (String w : asg) if (w != null) assigned++;
        if (assigned <= f.bestAssignedCount && f.bestAssignment != null) return;
        f.bestAssignedCount = assigned;
        f.bestAssignment = asg;
        f.bestSlots = f.slots;
        f.bestPattern = f.pattern;
        f.bestStatLetters = f.statLetters(asg);
        if (f.onNewBest != null) f.onNewBest.accept(f.bestAssignment);
    }

    boolean multiBlocked() {
        Integer cap = f.multiWordPercent;
        if (cap == null || cap >= 100) return false;
        return Fill.multiWordsBlocked(f.slots, f.index, currentAssignment(), cap);
    }

    /** Filler.deadlineReachedWithoutExtension's rule, re-evaluated every CHECKS_PROGRESS_REPORT_INTERVAL checks past
     * the budget whatever the step checks advances by, the refusal sticky (mirrors IngridSolver._deadline_reached). */
    private boolean deadlineReached() {
        if (extensionDenied) return true;
        if (f.checks <= deadline) return false;
        if (f.checksSlot == null || f.siblingChecksProgress == null || f.siblingAttemptActive == null) {
            extensionDenied = true;
            return true;
        }
        if (lastExtensionEval == null || f.checks - lastExtensionEval >= Filler.CHECKS_PROGRESS_REPORT_INTERVAL) {
            lastExtensionEval = f.checks;
            if (!f.siblingsStillRacing(deadline)) {
                extensionDenied = true;
                return true;
            }
        }
        return false;
    }

    boolean step() {
        f.checks += 1 + work / WORDS_PER_CHECK;
        work %= WORDS_PER_CHECK;
        if (f.onLiveState != null || f.onChecksProgress != null) f.assignment = currentAssignment();
        if (f.periodicCheckpoints()) return true;
        if (deadlineReached()) {
            f.budgetExhausted = true;
            return true;
        }
        return false;
    }

    /** {index in the order of the first word left, index of the word picked} for slot {@code i}, or null (mirrors
     * IngridSolver._pick_word). */
    Object[] pickWord(int i, int start) {
        List<String> ord = order(i);
        boolean blocked = multiBlocked();
        int[] cells = f.slots.get(i);
        List<Integer> found = new ArrayList<>();
        for (int k = start; k < ord.size(); k++) {
            String w = ord.get(k);
            if (!contains(i, w)) continue;
            if (blocked && !f.challengeWords.contains(w) && Fill.isMultiWord(f.index, cells, w)) continue;
            found.add(k);
            if (found.size() == RANDOM_WORD_WEIGHTS.length) break;
        }
        if (found.isEmpty()) return null;
        ToIntFunction<String> family = familyOf(i);
        int first = family.applyAsInt(ord.get(found.get(0)));
        if (first < 3) found.removeIf(k -> family.applyAsInt(ord.get(k)) != first);
        int pick = weightedPick(RANDOM_WORD_WEIGHTS, found.size());
        return new Object[]{found.get(0), ord.get(found.get(pick))};
    }

    /** Every member of slot {@code i}'s domain but {@code word}, as (ids, extras). */
    void keepOnly(int i, String word) {
        BitSet ids = (BitSet) dom[i].clone();
        List<String> ex = new ArrayList<>(extraDom[i]);
        if (!ex.remove(word) && idx[i] != null) ids.clear(idx[i].ids.get(word));
        remove(i, ids, ex);
    }

    void refute(int i, String word) {
        BitSet ids = new BitSet();
        List<String> ex = new ArrayList<>();
        if (extraDom[i].contains(word)) ex.add(word);
        else ids.set(idx[i].ids.get(word));
        remove(i, ids, ex);
    }

    private record Choice(int slot, String word, int mark) {}

    /** One run from the root (mirrors IngridSolver._run): "solved", "hard", "limit" or "stop". */
    private String run(int maxBacktracks) {
        List<Choice> choices = new ArrayList<>();
        int backtracks = 0;
        int lastSlot = -1;
        Integer lastStart = null;
        while (true) {
            if (step()) return "stop";
            int slot;
            Object[] picked;
            if (emptyRoot && choices.isEmpty()) {
                // No word on the grid yet: the first one is chosen the "falcon" way (falconPick), for diversity.
                List<Integer> pool = new ArrayList<>();
                for (int i = 0; i < n; i++) if (active[i] && size[i] > 1) pool.add(i);
                if (pool.isEmpty()) return "solved";
                Object[] fp = falconPick(pool);
                slot = (int) fp[0];
                picked = fp[1] != null ? new Object[]{0, fp[1]} : pickWord(slot, 0);
            } else {
                slot = chooseSlot(lastSlot);
                if (slot < 0) return "solved";
                int start = slot == lastSlot && lastStart != null ? lastStart : 0;
                picked = pickWord(slot, start);
            }
            Choice undoing;
            if (picked != null) {
                String word = (String) picked[1];
                lastSlot = slot;
                lastStart = (Integer) picked[0];
                int mark = trail.size();
                keepOnly(slot, word);
                Map<Integer, Double> updates = propagate(List.of(slot), List.of(slot));
                if (updates == null) {
                    choices.add(new Choice(slot, word, mark));
                    record();
                    continue;
                }
                learn(updates);
                undoTo(mark);
                undoing = new Choice(slot, word, -1);
            } else {
                // Every word left is refused by the multi-word cap: the previous choice is what has to change.
                undoing = null;
            }
            while (true) {
                backtracks++;
                if (undoing != null) {
                    int mark = trail.size();
                    refute(undoing.slot, undoing.word);
                    Map<Integer, Double> updates;
                    if (size[undoing.slot] == 0) updates = Map.of();
                    else updates = propagate(List.of(undoing.slot),
                            size[undoing.slot] == 1 ? List.of(undoing.slot) : List.of());
                    if (updates == null) break;
                    learn(updates);
                    undoTo(mark);
                }
                if (choices.isEmpty()) return "hard";
                Choice previous = choices.remove(choices.size() - 1);
                undoTo(previous.mark);
                undoing = previous;
                if (backtracks > maxBacktracks) return "limit";
                lastSlot = -1;
                lastStart = null;
                if (step()) return "stop";
            }
        }
    }

    /** Fills the grid (mirrors IngridSolver.solve); leaves the Filler's bestAssignment and ingridBlockedSlots. */
    @SuppressWarnings("unchecked")
    public boolean solve() {
        f.bestAssignment = initial.clone();
        int assigned = 0;
        for (String w : initial) if (w != null) assigned++;
        f.bestAssignedCount = assigned;
        f.bestSlots = f.slots;
        f.bestPattern = f.pattern;
        f.bestStatLetters = f.statLetters(f.bestAssignment);
        f.ingridBlockedSlots = new ArrayList<>();
        List<Integer> act = new ArrayList<>();
        List<Integer> singletons = new ArrayList<>();
        for (int i = 0; i < n; i++) {
            if (!active[i]) continue;
            act.add(i);
            if (size[i] == 1) singletons.add(i);
        }
        boolean solved = false;
        Map<Integer, Double> updates = propagate(act, singletons);
        if (updates == null) {
            freezeInitial();
            record();
            int root = trail.size();
            int maxBacktracks = INITIAL_MAX_BACKTRACKS;
            while (true) {
                String outcome = run(maxBacktracks);
                if (outcome.equals("solved")) {
                    record();
                    solved = true;
                    break;
                }
                if (!outcome.equals("limit")) break;
                undoTo(root);
                maxBacktracks = Math.max(maxBacktracks + 1, (int) (maxBacktracks * RETRY_GROWTH_FACTOR));
            }
        } else {
            learn(updates);
        }
        f.assignment = f.bestAssignment.clone();
        f.usedWords = f.usedOf(f.bestAssignment);
        if (!solved) {
            int worst = -1;
            double worstWeight = 0;
            for (int i : act) {
                if (f.bestAssignment[i] != null) continue;
                double w = -cross[i].size();
                for (int[] c : cross[i]) w += cellWeight.get(c[3]);
                if (worst < 0 || w > worstWeight) {
                    worst = i;
                    worstWeight = w;
                }
            }
            if (worst >= 0 && worstWeight > 0) f.ingridBlockedSlots = new ArrayList<>(List.of(worst));
        }
        return solved;
    }
}
