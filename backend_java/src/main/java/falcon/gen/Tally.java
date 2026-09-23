package falcon.gen;

import java.util.List;

/** Letter tallies (Python Counters of letters) as {@code int[]} indexed by
 * {@link Alpha} id; a per-cell "by direction" pair is {@code int[2][]}
 * (0 = across, 1 = down, null = no tally for that direction). */
public final class Tally {
    private Tally() {}

    public static int get(int[] t, int id) {
        return t == null || id < 0 || id >= t.length ? 0 : t[id];
    }

    public static int get(int[] t, char ch) {
        return get(t, Alpha.idOrMinus(ch));
    }

    public static boolean nonEmpty(int[] t) {
        if (t == null) return false;
        for (int v : t) if (v > 0) return true;
        return false;
    }

    public static int distinct(int[] t) {
        int n = 0;
        if (t != null) for (int v : t) if (v > 0) n++;
        return n;
    }

    public static int max(int[] t) {
        int m = 0;
        if (t != null) for (int v : t) if (v > m) m = v;
        return m;
    }

    public static int[] ofLetters(List<String> sample, int pos) {
        int[] t = new int[Alpha.size()];
        for (String w : sample) t[Alpha.id(w.charAt(pos))]++;
        return t;
    }

    public static int[] combined(int[][] byDir) {
        int[] total = new int[Alpha.size()];
        if (byDir == null) return total;
        for (int[] t : byDir) {
            if (t == null) continue;
            for (int i = 0; i < t.length && i < total.length; i++) total[i] += t[i];
        }
        return total;
    }

    /** Letters both directions observed, each at the lower of its two counts. */
    public static int[] crossed(int[][] byDir) {
        int[] a = byDir == null ? null : byDir[0], b = byDir == null ? null : byDir[1];
        boolean ha = nonEmpty(a), hb = nonEmpty(b);
        if (!ha && !hb) return new int[0];
        if (ha != hb) return (ha ? a : b).clone();
        int n = Math.max(a.length, b.length);
        int[] out = new int[n];
        for (int i = 0; i < n; i++) {
            int x = get(a, i), y = get(b, i);
            if (x > 0 && y > 0) out[i] = Math.min(x, y);
        }
        return out;
    }

    /** Python's {@code _crossed_letter_option_count}; null when no tally. */
    public static Integer crossedOptionCount(int[][] byDir) {
        int[] a = byDir == null ? null : byDir[0], b = byDir == null ? null : byDir[1];
        boolean ha = nonEmpty(a), hb = nonEmpty(b);
        if (!ha && !hb) return null;
        if (ha != hb) return distinct(ha ? a : b);
        int n = 0;
        for (int i = 0; i < a.length; i++) if (a[i] > 0 && get(b, i) > 0) n++;
        return n;
    }

    /** Most common letter of the crossed tally, or null. */
    public static Character mostProbable(int[][] byDir) {
        int[] crossed = crossed(byDir);
        int best = -1, bestCount = 0;
        for (int i = 0; i < crossed.length; i++) {
            if (crossed[i] > bestCount) {
                bestCount = crossed[i];
                best = i;
            }
        }
        return best < 0 ? null : Alpha.letter(best);
    }
}
