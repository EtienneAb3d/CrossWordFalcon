package falcon;

import java.nio.file.Path;

/** Process-wide environment helpers: project root and env-var parsing. */
public final class Env {
    private Env() {}

    /** Repository root — every data/log directory is resolved against it,
     * exactly like the Python modules' {@code Path(__file__).parent.parent}.
     * run_FalconJ.sh launches the jar from the repo root; the
     * {@code falcon.root} system property or {@code CROSSWORDFALCON_ROOT}
     * env var override it. */
    public static final Path ROOT = resolveRoot();

    private static Path resolveRoot() {
        String p = System.getProperty("falcon.root");
        if (p == null || p.isBlank()) p = System.getenv("CROSSWORDFALCON_ROOT");
        if (p == null || p.isBlank()) p = System.getProperty("user.dir");
        return Path.of(p).toAbsolutePath().normalize();
    }

    public static Path path(String first, String... more) {
        return ROOT.resolve(Path.of(first, more));
    }

    public static String get(String name, String def) {
        String v = System.getenv(name);
        return v == null ? def : v;
    }

    /** Like Python's {@code os.environ.get(name) or def}: an empty value
     * also falls back to the default. */
    public static String getNonEmpty(String name, String def) {
        String v = System.getenv(name);
        return (v == null || v.isEmpty()) ? def : v;
    }

    public static int getInt(String name, int def) {
        String v = System.getenv(name);
        if (v == null || v.isBlank()) return def;
        try {
            return Integer.parseInt(v.trim());
        } catch (NumberFormatException e) {
            return def;
        }
    }

    public static double getDouble(String name, double def) {
        String v = System.getenv(name);
        if (v == null || v.isBlank()) return def;
        try {
            return Double.parseDouble(v.trim());
        } catch (NumberFormatException e) {
            return def;
        }
    }

    public static void log(String msg) {
        System.out.println(msg);
        System.out.flush();
    }
}
