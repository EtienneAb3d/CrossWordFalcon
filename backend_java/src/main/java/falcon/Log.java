package falcon;

import java.time.LocalDateTime;
import java.time.format.DateTimeFormatter;

/**
 * Minimal logger writing the same line format as backend/app.py's
 * {@code logging.basicConfig(format="%(asctime)s %(levelname)s %(message)s")}
 * to stdout (redirected to logs/backend.log by run_FalconJ.sh).
 */
public final class Log {
    private Log() {}

    private static final DateTimeFormatter FMT = DateTimeFormatter.ofPattern("yyyy-MM-dd HH:mm:ss,SSS");

    private static synchronized void emit(String level, String msg) {
        System.out.println(LocalDateTime.now().format(FMT) + " " + level + " " + msg);
        System.out.flush();
    }

    public static void info(String fmt, Object... args) {
        emit("INFO", args.length == 0 ? fmt : String.format(java.util.Locale.ROOT, fmt, args));
    }

    public static void warning(String fmt, Object... args) {
        emit("WARNING", args.length == 0 ? fmt : String.format(java.util.Locale.ROOT, fmt, args));
    }

    public static void error(String fmt, Object... args) {
        emit("ERROR", args.length == 0 ? fmt : String.format(java.util.Locale.ROOT, fmt, args));
    }

    public static void exception(String msg, Throwable t) {
        java.io.StringWriter sw = new java.io.StringWriter();
        t.printStackTrace(new java.io.PrintWriter(sw));
        emit("ERROR", msg + "\n" + sw);
    }

    /** Python-style {@code repr()} of a string (for log lines only). */
    public static String repr(Object o) {
        if (o == null) return "None";
        if (!(o instanceof String s)) return String.valueOf(o);
        char q = s.contains("'") && !s.contains("\"") ? '"' : '\'';
        StringBuilder sb = new StringBuilder().append(q);
        for (char c : s.toCharArray()) {
            switch (c) {
                case '\n' -> sb.append("\\n");
                case '\r' -> sb.append("\\r");
                case '\t' -> sb.append("\\t");
                case '\\' -> sb.append("\\\\");
                default -> {
                    if (c == q) sb.append('\\');
                    sb.append(c);
                }
            }
        }
        return sb.append(q).toString();
    }
}
