package falcon.gen;

import java.util.Collections;
import java.util.List;
import java.util.SplittableRandom;

/** Seedable RNG with the handful of Python {@code random.Random} methods
 * the engine uses. Not thread-safe: one instance per attempt/thread. */
public final class Rng {
    private final SplittableRandom r;

    public Rng(long seed) {
        this.r = new SplittableRandom(seed);
    }

    public Rng() {
        this.r = new SplittableRandom();
    }

    public static Rng of(Long seed) {
        return seed == null ? new Rng() : new Rng(seed);
    }

    /** Python {@code randrange(n)}. */
    public int randrange(int n) {
        return r.nextInt(n);
    }

    /** Python {@code randrange(2 ** 31)}. */
    public long seed31() {
        return r.nextLong(1L << 31);
    }

    public double random() {
        return r.nextDouble();
    }

    public <T> void shuffle(List<T> list) {
        for (int i = list.size() - 1; i > 0; i--) Collections.swap(list, i, r.nextInt(i + 1));
    }

    public void shuffle(int[] a) {
        for (int i = a.length - 1; i > 0; i--) {
            int j = r.nextInt(i + 1);
            int t = a[i];
            a[i] = a[j];
            a[j] = t;
        }
    }

    public <T> T choice(List<T> list) {
        return list.get(r.nextInt(list.size()));
    }
}
