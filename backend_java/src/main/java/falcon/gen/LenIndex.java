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
    /** Each word's usage score (0-5), {@link Words#USAGE_SCORE_UNKNOWN} for a word the usage-score file does not
     * rate; null when the language has none (index[length]["usage"]). */
    public final byte[] usage;
    /** Each word's lemma ids, {@code lemmaIds[lemmaOff[i]..lemmaOff[i + 1])} (index[length]["lemma_off"]/["lemma_ids"]),
     * and the inverse: sorted lemma ids -> ids of the words carrying them, {@code lemmaWords[lemmaKeyOff[k]..
     * lemmaKeyOff[k + 1])} (["lemma_keys"]/["lemma_key_off"]/["lemma_words"]); null until {@link Words#attachLemmas}. */
    public int[] lemmaOff, lemmaIds, lemmaKeys, lemmaKeyOff, lemmaWords;
    /** Lazily computed per-position letter counts over the whole list. */
    private volatile int[][] blankCounts;

    public LenIndex(int length, List<String> words, Map<String, Double> frequencies,
                    Map<String, Double> dictionaryFrequencies) {
        this(length, words, frequencies, dictionaryFrequencies, null, null, null);
    }

    public LenIndex(int length, List<String> words, Map<String, Double> frequencies,
                    Map<String, Double> dictionaryFrequencies, Map<String, Integer> wordRefs, String[] sources) {
        this(length, words, frequencies, dictionaryFrequencies, wordRefs, sources, null);
    }

    public LenIndex(int length, List<String> words, Map<String, Double> frequencies,
                    Map<String, Double> dictionaryFrequencies, Map<String, Integer> wordRefs, String[] sources,
                    Map<String, Integer> usageScores) {
        this.length = length;
        this.words = words.toArray(new String[0]);
        this.wordList = Collections.unmodifiableList(Arrays.asList(this.words));
        this.ids = new HashMap<>(this.words.length * 2);
        this.freq = new float[this.words.length];
        this.dictFreq = new float[this.words.length];
        this.refs = wordRefs == null ? null : new int[this.words.length];
        this.sources = sources;
        this.usage = usageScores == null ? null : new byte[this.words.length];
        for (int i = 0; i < this.words.length; i++) {
            String w = this.words[i];
            ids.put(w, i);
            Double f = frequencies == null ? null : frequencies.get(w);
            freq[i] = f == null ? 0.0f : (float) (double) f;
            Double d = dictionaryFrequencies == null ? null : dictionaryFrequencies.get(w);
            dictFreq[i] = d == null ? 0.0f : (float) (double) d;
            if (refs != null) refs[i] = wordRefs.get(w);
            if (usage != null) usage[i] = (byte) (int) usageScores.getOrDefault(w, Words.USAGE_SCORE_UNKNOWN);
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

    /** {@code w}'s lemma ids; empty when absent or when no lemma is attached. */
    public int[] lemmasOf(String w) {
        if (lemmaOff == null) return EMPTY;
        Integer id = ids.get(w);
        return id == null ? EMPTY : Arrays.copyOfRange(lemmaIds, lemmaOff[id], lemmaOff[id + 1]);
    }

    /** The words of this length carrying the lemma {@code lemmaId}, appended to {@code out}. */
    public void wordsOfLemma(int lemmaId, List<String> out) {
        if (lemmaKeys == null) return;
        int k = Arrays.binarySearch(lemmaKeys, lemmaId);
        if (k < 0) return;
        for (int j = lemmaKeyOff[k]; j < lemmaKeyOff[k + 1]; j++) out.add(words[lemmaWords[j]]);
    }

    private static final int[] EMPTY = new int[0];

    public double dictFreqOf(String w) {
        Integer id = ids.get(w);
        return id == null ? 0.0 : dictFreq[id];
    }

    /** {@code w}'s usage score, {@link Words#USAGE_SCORE_UNKNOWN} when unrated, absent or {@link #usage} is null. */
    public int usageOf(String w) {
        if (usage == null) return Words.USAGE_SCORE_UNKNOWN;
        Integer id = ids.get(w);
        return id == null ? Words.USAGE_SCORE_UNKNOWN : usage[id];
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
