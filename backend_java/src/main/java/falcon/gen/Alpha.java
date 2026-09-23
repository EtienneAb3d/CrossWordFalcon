package falcon.gen;

import java.util.concurrent.ConcurrentHashMap;

/**
 * Process-wide registry giving every letter ever seen in a word index a
 * small, stable integer id, so letter sets can be {@link java.util.BitSet}s
 * and letter tallies plain {@code int[]} arrays.
 */
public final class Alpha {
    private Alpha() {}

    private static final ConcurrentHashMap<Character, Integer> IDS = new ConcurrentHashMap<>();
    private static volatile char[] letters = new char[0];

    static {
        for (char ch = 'A'; ch <= 'Z'; ch++) id(ch);
    }

    public static int id(char ch) {
        Integer v = IDS.get(ch);
        if (v != null) return v;
        synchronized (Alpha.class) {
            v = IDS.get(ch);
            if (v != null) return v;
            int n = letters.length;
            char[] grown = java.util.Arrays.copyOf(letters, n + 1);
            grown[n] = ch;
            letters = grown;
            IDS.put(ch, n);
            return n;
        }
    }

    /** Existing id, or -1 for a letter no index has ever registered. */
    public static int idOrMinus(char ch) {
        Integer v = IDS.get(ch);
        return v == null ? -1 : v;
    }

    public static char letter(int id) {
        return letters[id];
    }

    public static int size() {
        return letters.length;
    }
}
