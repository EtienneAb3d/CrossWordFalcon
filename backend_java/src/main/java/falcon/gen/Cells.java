package falcon.gen;

import java.util.ArrayList;
import java.util.Arrays;
import java.util.Collection;
import java.util.List;

/** Grid cell packing: a cell is one {@code int}, {@code (row << 16) | col}. */
public final class Cells {
    private Cells() {}

    public static int of(int r, int c) {
        return (r << 16) | c;
    }

    public static int r(int cell) {
        return cell >>> 16;
    }

    public static int c(int cell) {
        return cell & 0xFFFF;
    }

    /** [[r, c], ...] (JSON shape), in the given order. */
    public static List<Object> toJson(Collection<Integer> cells) {
        List<Object> out = new ArrayList<>(cells.size());
        for (int cell : cells) out.add(List.of(r(cell), c(cell)));
        return out;
    }

    /** Sorted by (row, col) — the order of Python's {@code sorted()} on tuples. */
    public static List<Object> sortedJson(Collection<Integer> cells) {
        int[] arr = new int[cells.size()];
        int i = 0;
        for (int cell : cells) arr[i++] = cell;
        Arrays.sort(arr);
        List<Object> out = new ArrayList<>(arr.length);
        int prev = -1;
        boolean first = true;
        for (int cell : arr) {
            if (!first && cell == prev) continue;
            out.add(List.of(r(cell), c(cell)));
            prev = cell;
            first = false;
        }
        return out;
    }

    public static List<Object> toJson(int[] cells) {
        List<Object> out = new ArrayList<>(cells.length);
        for (int cell : cells) out.add(List.of(r(cell), c(cell)));
        return out;
    }

    /** Hashable wrapper for a slot's cell list (Python's {@code tuple(cells)}). */
    public static final class Key {
        public final int[] cells;
        private final int hash;

        public Key(int[] cells) {
            this.cells = cells;
            this.hash = Arrays.hashCode(cells);
        }

        @Override
        public boolean equals(Object o) {
            return o instanceof Key k && Arrays.equals(cells, k.cells);
        }

        @Override
        public int hashCode() {
            return hash;
        }
    }

    public static Key key(int[] cells) {
        return new Key(cells);
    }
}
