package falcon.gen;

import falcon.gen.Words.DualIndex;

import java.util.ArrayList;
import java.util.Collection;
import java.util.HashMap;
import java.util.HashSet;
import java.util.List;
import java.util.Map;
import java.util.Set;

/**
 * The words a fill may no longer place: every word placed ({@link #placed}) plus every word sharing a canonical form
 * (a lemma of its CANONIQUE column, {@link LenIndex#lemmasOf}) with one of them. The set's own content IS that
 * blocked set, so every {@code contains}/{@code minus} test applies the rule; only {@link #add}/{@link #remove} know
 * about placed words. Two words supporting the same blocked word are counted: removing one keeps it blocked. A word
 * outside the lexicon (a "Mots Défi" word) blocks only itself (mirrors UsedWords).
 */
public final class UsedWords extends HashSet<String> {
    private final DualIndex index;
    public final Set<String> placed = new HashSet<>();
    private final Map<Integer, Integer> lemmaCount = new HashMap<>();
    private final Map<String, Integer> support = new HashMap<>();
    private final Map<String, int[]> lemmas = new HashMap<>();
    private final Map<Integer, List<String>> siblings = new HashMap<>();
    private final Map<String, List<String>> exclusive = new HashMap<>();
    /** The last word added and the words its add raised from no support: exclusiveTo's answer for that word, while
     * nothing else has changed. */
    private String lastAdded;
    private List<String> lastRaised;

    public UsedWords(DualIndex index) {
        this.index = index;
    }

    public UsedWords(DualIndex index, String[] words) {
        this(index);
        if (words != null) for (String w : words) if (w != null) add(w);
    }

    public UsedWords(DualIndex index, Collection<String> words) {
        this(index);
        for (String w : words) if (w != null) add(w);
    }

    private List<Map<Integer, LenIndex>> sides() {
        List<Map<Integer, LenIndex>> out = new ArrayList<>(2);
        if (index == null) return out;
        out.add(index.across);
        if (index.down != index.across) out.add(index.down);
        return out;
    }

    /** Every lemma id {@code word} carries in the index (both sides of a bilingual grid) (mirrors
     * _word_lemma_ids). */
    public int[] lemmasOf(String word) {
        int[] ids = lemmas.get(word);
        if (ids == null) {
            ids = new int[0];
            for (Map<Integer, LenIndex> side : sides()) {
                LenIndex li = side.get(word.length());
                if (li == null) continue;
                int[] more = li.lemmasOf(word);
                if (more.length == 0) continue;
                int[] merged = java.util.Arrays.copyOf(ids, ids.length + more.length);
                System.arraycopy(more, 0, merged, ids.length, more.length);
                ids = merged;
            }
            lemmas.put(word, ids);
        }
        return ids;
    }

    /** Every word of the index carrying the lemma {@code lemmaId} (mirrors _lemma_words). */
    private List<String> wordsOf(int lemmaId) {
        List<String> words = siblings.get(lemmaId);
        if (words == null) {
            words = new ArrayList<>();
            for (Map<Integer, LenIndex> side : sides()) for (LenIndex li : side.values()) li.wordsOfLemma(lemmaId, words);
            siblings.put(lemmaId, words);
        }
        return words;
    }

    private void raise(String word, List<String> raised) {
        if (support.merge(word, 1, Integer::sum) == 1) {
            super.add(word);
            raised.add(word);
        }
    }

    private void lower(String word) {
        Integer n = support.get(word);
        if (n == null) return;
        if (n <= 1) {
            support.remove(word);
            super.remove(word);
        } else {
            support.put(word, n - 1);
        }
    }

    @Override
    public boolean add(String word) {
        if (!placed.add(word)) return false;
        exclusive.clear();
        List<String> raised = new ArrayList<>();
        raise(word, raised);
        for (int lemma : lemmasOf(word)) {
            if (lemmaCount.merge(lemma, 1, Integer::sum) == 1) for (String w : wordsOf(lemma)) raise(w, raised);
        }
        lastAdded = word;
        lastRaised = raised;
        return true;
    }

    @Override
    public boolean remove(Object o) {
        if (!(o instanceof String word) || !placed.remove(word)) return false;
        exclusive.clear();
        lastAdded = null;
        lower(word);
        for (int lemma : lemmasOf(word)) {
            int n = lemmaCount.getOrDefault(lemma, 0);
            if (n <= 1) {
                lemmaCount.remove(lemma);
                for (String w : wordsOf(lemma)) lower(w);
            } else {
                lemmaCount.put(lemma, n - 1);
            }
        }
        return true;
    }

    @Override
    public boolean addAll(Collection<? extends String> words) {
        boolean changed = false;
        for (String w : words) changed |= add(w);
        return changed;
    }

    @Override
    public void clear() {
        placed.clear();
        lemmaCount.clear();
        support.clear();
        exclusive.clear();
        lastAdded = null;
        super.clear();
    }

    /** The words blocked only because {@code word} is placed — those {@code remove(word)} would unblock ({@code word}
     * itself included) (mirrors UsedWords.exclusive_to). */
    public List<String> exclusiveTo(String word) {
        if (!placed.contains(word)) return List.of();
        if (word.equals(lastAdded)) return lastRaised;
        List<String> out = exclusive.get(word);
        if (out != null) return out;
        int[] mine = lemmasOf(word);
        Set<Integer> sole = new HashSet<>();
        for (int lemma : mine) if (lemmaCount.getOrDefault(lemma, 0) == 1) sole.add(lemma);
        out = new ArrayList<>();
        int soleOfWord = 0;
        for (int lemma : mine) if (sole.contains(lemma)) soleOfWord++;
        if (support.getOrDefault(word, 0) == 1 + soleOfWord) out.add(word);
        Set<String> seen = new HashSet<>(out);
        for (int lemma : sole) {
            for (String w : wordsOf(lemma)) {
                if (w.equals(word) || placed.contains(w) || seen.contains(w)) continue;
                boolean all = true;
                for (int x : lemmasOf(w)) if (lemmaCount.getOrDefault(x, 0) > 0 && !sole.contains(x)) all = false;
                if (all) {
                    out.add(w);
                    seen.add(w);
                }
            }
        }
        exclusive.put(word, out);
        return out;
    }
}
