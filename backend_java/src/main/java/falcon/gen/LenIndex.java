package falcon.gen;

import java.util.BitSet;
import java.util.HashMap;
import java.util.HashSet;
import java.util.List;
import java.util.Map;
import java.util.Set;

/** Every word of one length, indexed by (position, letter) as BitSets over
 * the words' own ids (mirrors one entry of Python's build_index). */
public final class LenIndex {
    public final int length;
    public final String[] words;
    public final List<String> wordList;
    public final Map<String, Integer> ids;
    public final Set<String> wordSet;
    /** pos[p][alphaId] -> ids of the words carrying that letter at p (null = none). */
    public final BitSet[][] pos;
    public final double[] freq;
    /** Lazily computed per-position letter counts over the whole list. */
    private volatile int[][] blankCounts;

    public LenIndex(int length, List<String> words, Map<String, Double> frequencies) {
        this.length = length;
        this.words = words.toArray(new String[0]);
        this.wordList = List.of(this.words);
        this.ids = new HashMap<>(this.words.length * 2);
        this.wordSet = new HashSet<>(this.words.length * 2);
        this.freq = new double[this.words.length];
        for (int i = 0; i < this.words.length; i++) {
            String w = this.words[i];
            ids.put(w, i);
            wordSet.add(w);
            Double f = frequencies == null ? null : frequencies.get(w);
            freq[i] = f == null ? 0.0 : f;
            for (int p = 0; p < length; p++) Alpha.id(w.charAt(p));
        }
        int alpha = Alpha.size();
        this.pos = new BitSet[length][alpha];
        for (int i = 0; i < this.words.length; i++) {
            String w = this.words[i];
            for (int p = 0; p < length; p++) {
                int a = Alpha.id(w.charAt(p));
                BitSet bs = pos[p][a];
                if (bs == null) pos[p][a] = bs = new BitSet(this.words.length);
                bs.set(i);
            }
        }
    }

    public int size() {
        return words.length;
    }

    /** Words carrying {@code ch} at {@code p}; null when none. */
    public BitSet at(int p, char ch) {
        int a = Alpha.idOrMinus(ch);
        if (a < 0 || a >= pos[p].length) return null;
        BitSet bs = pos[p][a];
        return bs == null || bs.isEmpty() ? null : bs;
    }

    public double freqOf(String w) {
        Integer id = ids.get(w);
        return id == null ? 0.0 : freq[id];
    }

    /** Per-position letter counts over every word of this length (cached). */
    public int[][] blankCounts() {
        int[][] bc = blankCounts;
        if (bc == null) {
            int alpha = Alpha.size();
            bc = new int[length][alpha];
            for (String w : words) {
                for (int p = 0; p < length; p++) bc[p][Alpha.id(w.charAt(p))]++;
            }
            blankCounts = bc;
        }
        return bc;
    }
}
