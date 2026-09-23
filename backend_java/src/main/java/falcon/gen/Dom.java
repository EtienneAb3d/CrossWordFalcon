package falcon.gen;

import java.util.ArrayList;
import java.util.BitSet;
import java.util.Collection;
import java.util.HashSet;
import java.util.Iterator;
import java.util.List;
import java.util.NoSuchElementException;
import java.util.Set;
import java.util.function.Consumer;

/**
 * A slot's candidate word set: either every word of the length ("full", the
 * Python {@code domain is idx['words']} case) or a BitSet over the
 * {@link LenIndex}'s word ids. Immutable.
 */
public final class Dom implements Iterable<String> {
    public static final Dom EMPTY = new Dom(null, null);

    public final LenIndex idx;
    private final BitSet bits;
    private int size = -1;

    Dom(LenIndex idx, BitSet bits) {
        this.idx = idx;
        this.bits = bits;
    }

    static Dom full(LenIndex idx) {
        return new Dom(idx, null);
    }

    public boolean isFull() {
        return idx != null && bits == null;
    }

    public int size() {
        if (size < 0) size = idx == null ? 0 : bits == null ? idx.size() : bits.cardinality();
        return size;
    }

    public boolean isEmpty() {
        return size() == 0;
    }

    public boolean contains(String w) {
        if (idx == null || w == null || w.length() != idx.length) return false;
        Integer id = idx.ids.get(w);
        if (id == null) return false;
        return bits == null || bits.get(id);
    }

    /** Python {@code all(w in used for w in domain)}. */
    public boolean allIn(Set<String> used) {
        int n = size();
        if (n == 0) return true;
        if (n > used.size()) return false;
        for (String w : this) if (!used.contains(w)) return false;
        return true;
    }

    public String first() {
        Iterator<String> it = iterator();
        return it.hasNext() ? it.next() : null;
    }

    /** Python {@code rng.choices(list(domain), k=k)}: k draws with replacement. */
    public List<String> choices(Rng rng, int k) {
        List<String> out = new ArrayList<>(k);
        int n = size();
        if (n == 0) return out;
        if (bits == null) {
            for (int i = 0; i < k; i++) out.add(idx.words[rng.randrange(n)]);
            return out;
        }
        int[] members = ids();
        for (int i = 0; i < k; i++) out.add(idx.words[members[rng.randrange(members.length)]]);
        return out;
    }

    /** Ids of every member, in order. */
    public int[] ids() {
        if (idx == null) return new int[0];
        if (bits == null) {
            int[] out = new int[idx.size()];
            for (int i = 0; i < out.length; i++) out[i] = i;
            return out;
        }
        return bits.stream().toArray();
    }

    public List<String> toList() {
        List<String> out = new ArrayList<>(size());
        for (String w : this) out.add(w);
        return out;
    }

    public Set<String> toSet() {
        Set<String> out = new HashSet<>(Math.max(16, size() * 2));
        for (String w : this) out.add(w);
        return out;
    }

    /** Members not in {@code used}. */
    public List<String> minus(Collection<String> used) {
        List<String> out = new ArrayList<>();
        for (String w : this) if (!used.contains(w)) out.add(w);
        return out;
    }

    @Override
    public Iterator<String> iterator() {
        if (idx == null) return java.util.Collections.emptyIterator();
        if (bits == null) return idx.wordList.iterator();
        return new Iterator<>() {
            int next = bits.nextSetBit(0);

            @Override
            public boolean hasNext() {
                return next >= 0;
            }

            @Override
            public String next() {
                if (next < 0) throw new NoSuchElementException();
                String w = idx.words[next];
                next = bits.nextSetBit(next + 1);
                return w;
            }
        };
    }

    @Override
    public void forEach(Consumer<? super String> action) {
        for (String w : this) action.accept(w);
    }
}
