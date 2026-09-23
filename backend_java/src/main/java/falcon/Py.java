package falcon;

import java.math.BigDecimal;
import java.math.RoundingMode;

/** Python-compatible number formatting/rounding. */
public final class Py {
    private Py() {}

    /** Python's {@code f"{d:.{digits}f}"}: exact binary value, half-even. */
    public static String fmt(double d, int digits) {
        if (Double.isNaN(d)) return "nan";
        if (Double.isInfinite(d)) return d > 0 ? "inf" : "-inf";
        return new BigDecimal(d).setScale(digits, RoundingMode.HALF_EVEN).toPlainString();
    }

    /** Python's {@code str.isspace()} for one char. */
    public static boolean isSpace(char c) {
        return Character.isWhitespace(c) || Character.isSpaceChar(c) || c == '\u0085' || (c >= 0x1c && c <= 0x1f);
    }

    /** Python's {@code str.strip()} (Unicode whitespace, NBSP included). */
    public static String strip(String s) {
        if (s == null) return null;
        int a = 0, b = s.length();
        while (a < b && isSpace(s.charAt(a))) a++;
        while (b > a && isSpace(s.charAt(b - 1))) b--;
        return s.substring(a, b);
    }

    /** Python's {@code str.rstrip()}. */
    public static String rstrip(String s) {
        if (s == null) return null;
        int b = s.length();
        while (b > 0 && isSpace(s.charAt(b - 1))) b--;
        return s.substring(0, b);
    }

    /** Python's {@code str.split()} (no argument). */
    public static java.util.List<String> split(String s) {
        java.util.List<String> out = new java.util.ArrayList<>();
        int i = 0, n = s.length();
        while (i < n) {
            while (i < n && isSpace(s.charAt(i))) i++;
            int start = i;
            while (i < n && !isSpace(s.charAt(i))) i++;
            if (i > start) out.add(s.substring(start, i));
        }
        return out;
    }

    /** Python's {@code round(d)} (to an integer). */
    public static long round(double d) {
        return (long) Math.rint(d);
    }
}
