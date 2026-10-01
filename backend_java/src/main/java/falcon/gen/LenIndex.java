package falcon.gen;

import java.util.Arrays;
import java.util.BitSet;
import java.util.Collections;
import java.util.HashMap;
import java.util.List;
import java.util.Map;

/** Every word of one length, indexed by (position, letter) as BitSets over
 * the words' own ids (mirrors one entry of Python's build_index). */
public final class LenIndex {
    public final int length;
    public final String[] words;
    public final List<String> wordList;
    public final Map<String, Integer> ids;
    /** pos[p][alphaId] -> ids of the words carrying that letter at p (null = none). */
    public final BitSet[][] pos;
    /** Each word's lexicon frequency, as float32 (index[length]["freq"]). */
    public final float[] freq;
    /** Each word's frequency in the freq wordlist itself, 0.0 for a word absent from it, as float32
     * (index[length]["dict_freq"]). */
    public final float[] dictFreq;
    /** Each word's row reference (byte offset of its wordlist row << 1 | 1 for the Scrabble wordlist), or null
     * (index[length]["refs"]). The accented form and lemmas stay on disk: see {@link Words#wordForms}. */
    public final int[] refs;
    /** The (freq wordlist, Scrabble wordlist) paths {@link #refs} point into, or null (index[length]["sources"]). */
    public final String[] sources;
    /** Lazily computed per-position letter counts over the whole list. */
    private volatile int[][] blankCounts;

    public LenIndex(int length, List<String> words, Map<String, Double> frequencies,
                    Map<String, Double> dictionaryFrequencies) {
        this(length, words, frequencies, dictionaryFrequencies, null, null);
    }

    public LenIndex(int length, List<String> words, Map<String, Double> frequencies,
                    Map<String, Double> dictionaryFrequencies, Map<String, Integer> wordRefs, String[] sources) {
        this.length = length;
        this.words = words.toArray(new String[0]);
        this.wordList = Collections.unmodifiableList(Arrays.asList(this.words));
        this.ids = new HashMap<>(this.words.length * 2);
        this.freq = new float[this.words.length];
        this.dictFreq = new float[this.words.length];
        this.refs = wordRefs == null ? null : new int[this.words.length];
        this.sources = sources;
        for (int i = 0; i < this.words.length; i++) {
            String w = this.words[i];
            ids.put(w, i);
            Double f = frequencies == null ? null : frequencies.get(w);
            freq[i] = f == null ? 0.0f : (float) (double) f;
            Double d = dictionaryFrequencies == null ? null : dictionaryFrequencies.get(w);
            dictFreq[i] = d == null ? 0.0f : (float) (double) d;
            if (refs != null) refs[i] = wordRefs.get(w);
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

    /** True if {@code w} is a word of this length's lexicon. */
    public boolean contains(String w) {
        return ids.containsKey(w);
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

    public double dictFreqOf(String w) {
        Integer id = ids.get(w);
        return id == null ? 0.0 : dictFreq[id];
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
