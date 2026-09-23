package falcon;

import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.nio.file.DirectoryStream;
import java.nio.file.Files;
import java.nio.file.Path;
import java.security.SecureRandom;
import java.text.Normalizer;
import java.time.LocalDateTime;
import java.time.format.DateTimeFormatter;
import java.util.ArrayList;
import java.util.Collection;
import java.util.Comparator;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Locale;
import java.util.Map;
import java.util.TreeSet;
import java.util.regex.Pattern;

/**
 * Filesystem persistence (mirrors backend/grid_store.py, identical on-disk
 * formats so both back ends share GRID_STORE/GRID_WORK/GRID_GAME/STOP_DUMP):
 * published grids, in-progress "Interactif" drafts, per-player play state,
 * and "Stop" diagnostic dumps.
 */
public final class GridStore {
    private GridStore() {}

    public static final Path GRID_STORE_DIR = Env.path("GRID_STORE");
    public static final Path GRID_WORK_DIR = Env.path("GRID_WORK");
    public static final Path GRID_GAME_DIR = Env.path("GRID_GAME");
    public static final Path STOP_DUMP_DIR = Env.path("STOP_DUMP");

    public static final int MAX_SLUG_LENGTH = 40;
    private static final Pattern SLUG_RE = Pattern.compile("[^a-zA-Z0-9]+");
    public static final Pattern GRID_ID_RE = Pattern.compile("^\\d{8}-\\d{6}-\\d{6}_[a-z0-9_-]+_\\d{4}$");
    public static final Pattern WORK_ID_RE = Pattern.compile("^\\d{8}-\\d{6}-\\d{6}_[a-z0-9_]+_[0-9a-f]{32}$");
    private static final DateTimeFormatter STAMP = DateTimeFormatter.ofPattern("yyyyMMdd-HHmmss-SSSSSS");
    private static final SecureRandom RNG = new SecureRandom();

    /** Python's {@code datetime.now().isoformat()}. */
    public static String isoNow() {
        LocalDateTime now = LocalDateTime.now();
        int micros = now.getNano() / 1000;
        String base = now.format(DateTimeFormatter.ofPattern("yyyy-MM-dd'T'HH:mm:ss"));
        return micros == 0 ? base : base + String.format(Locale.ROOT, ".%06d", micros);
    }

    public static String timestamp() {
        return LocalDateTime.now().format(STAMP);
    }

    private static String asciiFold(String s) {
        String d = Normalizer.normalize(s == null ? "" : s, Normalizer.Form.NFKD);
        StringBuilder sb = new StringBuilder();
        for (int i = 0; i < d.length(); i++) if (d.charAt(i) < 128) sb.append(d.charAt(i));
        return sb.toString();
    }

    private static String stripUnderscores(String s) {
        int a = 0, b = s.length();
        while (a < b && s.charAt(a) == '_') a++;
        while (b > a && s.charAt(b - 1) == '_') b--;
        return s.substring(a, b);
    }

    private static String slugify(String text, String fallback) {
        String slug = stripUnderscores(SLUG_RE.matcher(asciiFold(text)).replaceAll("_")).toLowerCase(Locale.ROOT);
        slug = stripUnderscores(slug.substring(0, Math.min(MAX_SLUG_LENGTH, slug.length())));
        return slug.isEmpty() ? fallback : slug;
    }

    public static String slugifyTitle(String title) {
        return slugify(title, "grille");
    }

    public static String slugifyPseudo(String pseudo) {
        return slugify(pseudo, "anonyme");
    }

    private static String blankToNull(String s) {
        if (s == null) return null;
        String t = Py.strip(s);
        return t.isEmpty() ? null : t;
    }

    private static void write(Path path, Object record) throws IOException {
        Files.createDirectories(path.getParent());
        Files.writeString(path, Json.dumps(record), StandardCharsets.UTF_8);
    }

    private static Map<String, Object> readRecord(Path path) {
        try {
            Object o = Json.readFile(path);
            return o instanceof Map<?, ?> ? Json.asMap(o) : null;
        } catch (IOException | IllegalArgumentException e) {
            return null;
        }
    }

    private static List<Path> glob(Path dir, String pattern) {
        List<Path> out = new ArrayList<>();
        if (!Files.isDirectory(dir)) return out;
        try (DirectoryStream<Path> ds = Files.newDirectoryStream(dir, pattern)) {
            for (Path p : ds) out.add(p);
        } catch (IOException ignored) { }
        return out;
    }

    private static List<Path> subdirs(Path dir) {
        List<Path> out = new ArrayList<>();
        if (!Files.isDirectory(dir)) return out;
        try (DirectoryStream<Path> ds = Files.newDirectoryStream(dir)) {
            for (Path p : ds) if (Files.isDirectory(p)) out.add(p);
        } catch (IOException ignored) { }
        return out;
    }

    private static String stem(Path p) {
        String n = p.getFileName().toString();
        return n.endsWith(".json") ? n.substring(0, n.length() - 5) : n;
    }

    // ------------------------------------------------------------------ GRID_STORE

    public static String saveGridJson(Map<String, Object> result, String language, String difficulty, String mode,
                                      String title, String bilingual, String pseudo, String theme,
                                      boolean interactive, Object origin, Object generationParams,
                                      Collection<?> challengeWords) throws IOException {
        boolean isBilingual = bilingual != null && !bilingual.isEmpty() && !bilingual.equals(language);
        String gridId = timestamp() + "_" + slugifyTitle(title) + "_" + String.format(Locale.ROOT, "%04d", RNG.nextInt(10_000));
        Path dir = GRID_STORE_DIR.resolve(isBilingual ? "bilingual" : language);
        Map<String, Object> record = new LinkedHashMap<>(result);
        record.put("id", gridId);
        record.put("title", title);
        record.put("language", language);
        record.put("bilingual", isBilingual ? bilingual : null);
        record.put("pseudo", blankToNull(pseudo));
        record.put("theme", blankToNull(theme));
        record.put("interactive", interactive ? Boolean.TRUE : null);
        record.put("origin", origin);
        record.put("difficulty", difficulty);
        record.put("mode", mode);
        record.put("generation_params", generationParams);
        record.put("challenge_words", challengeWords != null && !challengeWords.isEmpty() ? new ArrayList<>(challengeWords) : null);
        record.put("created_at", isoNow());
        write(dir.resolve(gridId + ".json"), record);
        return gridId;
    }

    public static List<Map<String, Object>> iterStoredGrids() {
        List<Map<String, Object>> out = new ArrayList<>();
        for (Path sub : subdirs(GRID_STORE_DIR)) {
            for (Path path : glob(sub, "*.json")) {
                Map<String, Object> r = readRecord(path);
                if (r == null) continue;
                Map<String, Object> m = new LinkedHashMap<>();
                m.put("id", r.getOrDefault("id", stem(path)));
                for (String k : List.of("created_at", "language", "bilingual", "pseudo", "interactive", "origin",
                        "theme", "difficulty", "title", "width", "height")) {
                    m.put(k, r.get(k));
                }
                out.add(m);
            }
        }
        return out;
    }

    public static List<Map<String, Object>> listGrids(String preferredLanguage) {
        List<Map<String, Object>> grids = iterStoredGrids();
        grids.sort(Comparator.comparing((Map<String, Object> e) -> Json.str(e, "created_at", "")).reversed());
        grids.sort(Comparator.comparingInt(e -> {
            Object lang = e.get("language");
            if (lang != null && lang.equals(preferredLanguage)) return 0;
            if ("en".equals(lang) && !"en".equals(preferredLanguage)) return 1;
            return 2;
        }));
        return grids;
    }

    public static Map<String, Object> getGrid(String gridId) {
        if (gridId == null || !GRID_ID_RE.matcher(gridId).matches()) return null;
        for (Path sub : subdirs(GRID_STORE_DIR)) {
            Path p = sub.resolve(gridId + ".json");
            if (Files.isRegularFile(p)) return readRecord(p);
        }
        return null;
    }

    // ------------------------------------------------------------------ GRID_WORK

    public static String saveGridWork(String jobId, Object grid, Object definitions, Object title, Object language,
                                      Object difficulty, Object theme, Collection<String> priorityWords, Object seed,
                                      String pseudo, String resumedFrom, Object origin, Object bilingualLanguage,
                                      Object generationParams, Collection<?> challengeWords, Object diagnostics)
            throws IOException {
        pseudo = blankToNull(pseudo);
        List<Path> existing = glob(GRID_WORK_DIR, "*_" + jobId + ".json");
        Object createdAt = null;
        Object previous = null;
        Path path;
        String workId;
        if (!existing.isEmpty()) {
            path = existing.get(0);
            workId = stem(path);
            Map<String, Object> old = readRecord(path);
            if (old != null) {
                createdAt = old.get("created_at");
                Map<String, Object> prev = new LinkedHashMap<>(old);
                prev.remove("previous");
                previous = prev;
            }
        } else if (resumedFrom != null && WORK_ID_RE.matcher(resumedFrom).matches()
                && Files.isRegularFile(GRID_WORK_DIR.resolve(resumedFrom + ".json"))) {
            Path oldPath = GRID_WORK_DIR.resolve(resumedFrom + ".json");
            Map<String, Object> old = readRecord(oldPath);
            if (old != null) createdAt = old.get("created_at");
            String prefix = resumedFrom.substring(0, resumedFrom.lastIndexOf('_'));
            workId = prefix + "_" + jobId;
            path = GRID_WORK_DIR.resolve(workId + ".json");
            Files.move(oldPath, path);
        } else {
            Files.createDirectories(GRID_WORK_DIR);
            workId = timestamp() + "_" + slugifyPseudo(pseudo) + "_" + jobId;
            path = GRID_WORK_DIR.resolve(workId + ".json");
        }
        String now = isoNow();
        Map<String, Object> record = new LinkedHashMap<>();
        record.put("id", workId);
        record.put("job_id", jobId);
        record.put("grid", grid);
        record.put("definitions", definitions);
        record.put("title", title);
        record.put("language", language);
        record.put("bilingual_language", bilingualLanguage);
        record.put("difficulty", difficulty);
        record.put("theme", theme);
        record.put("priority_words", new ArrayList<>(new TreeSet<>(priorityWords == null ? List.of() : priorityWords)));
        record.put("seed", seed);
        record.put("pseudo", pseudo);
        record.put("origin", origin);
        record.put("generation_params", generationParams);
        record.put("challenge_words", challengeWords == null ? new ArrayList<>() : new ArrayList<>(challengeWords));
        record.put("diagnostics", diagnostics);
        record.put("previous", previous);
        record.put("created_at", createdAt != null ? createdAt : now);
        record.put("updated_at", now);
        write(path, record);
        return workId;
    }

    public static List<Map<String, Object>> iterStoredGridWork() {
        List<Map<String, Object>> out = new ArrayList<>();
        for (Path path : glob(GRID_WORK_DIR, "*.json")) {
            Map<String, Object> r = readRecord(path);
            if (r == null) continue;
            List<Object> grid = Json.listOrEmpty(r.get("grid"));
            Map<String, Object> m = new LinkedHashMap<>();
            m.put("id", r.getOrDefault("id", stem(path)));
            for (String k : List.of("title", "language", "difficulty", "theme", "pseudo", "origin", "created_at",
                    "updated_at")) {
                m.put(k, r.get(k));
            }
            Object first = grid.isEmpty() ? null : grid.get(0);
            m.put("width", first instanceof List<?> l && !l.isEmpty() ? l.size() : null);
            m.put("height", grid.isEmpty() ? null : grid.size());
            out.add(m);
        }
        return out;
    }

    public static List<Map<String, Object>> listGridWork(String pseudo) {
        String p = pseudo == null ? "" : Py.strip(pseudo);
        List<Map<String, Object>> entries = new ArrayList<>();
        for (Map<String, Object> e : iterStoredGridWork()) {
            if (p.isEmpty() || Py.strip(Json.str(e, "pseudo", "")).equals(p)) entries.add(e);
        }
        entries.sort(Comparator.comparing((Map<String, Object> e) -> Json.str(e, "updated_at", "")).reversed());
        return entries;
    }

    public static Map<String, Object> getGridWork(String workId) {
        if (workId == null || !WORK_ID_RE.matcher(workId).matches()) return null;
        Path path = GRID_WORK_DIR.resolve(workId + ".json");
        if (!Files.isRegularFile(path)) return null;
        return readRecord(path);
    }

    public static boolean deleteGridWork(String workId) {
        if (workId == null || !WORK_ID_RE.matcher(workId).matches()) return false;
        try {
            return Files.deleteIfExists(GRID_WORK_DIR.resolve(workId + ".json"));
        } catch (IOException e) {
            return false;
        }
    }

    // ------------------------------------------------------------------ GRID_GAME

    public static boolean saveGridGame(String gridId, String pseudo, Object userLetters, double elapsedSeconds)
            throws IOException {
        String p = pseudo == null ? "" : Py.strip(pseudo);
        if (gridId == null || !GRID_ID_RE.matcher(gridId).matches() || p.isEmpty()) return false;
        Path path = GRID_GAME_DIR.resolve(gridId).resolve(slugifyPseudo(p) + ".json");
        Object createdAt = null;
        if (Files.isRegularFile(path)) {
            Map<String, Object> old = readRecord(path);
            if (old != null) createdAt = old.get("created_at");
        }
        String now = isoNow();
        Map<String, Object> record = new LinkedHashMap<>();
        record.put("grid_id", gridId);
        record.put("pseudo", p);
        record.put("user_letters", userLetters);
        record.put("elapsed_seconds", Math.max(0, (long) elapsedSeconds));
        record.put("created_at", createdAt != null ? createdAt : now);
        record.put("updated_at", now);
        write(path, record);
        return true;
    }

    public static Map<String, Object> getGridGame(String gridId, String pseudo) {
        String p = pseudo == null ? "" : Py.strip(pseudo);
        if (gridId == null || !GRID_ID_RE.matcher(gridId).matches() || p.isEmpty()) return null;
        Path path = GRID_GAME_DIR.resolve(gridId).resolve(slugifyPseudo(p) + ".json");
        if (!Files.isRegularFile(path)) return null;
        return readRecord(path);
    }

    // ------------------------------------------------------------------ STOP_DUMP

    public static String saveStopDump(String jobId, String pseudo, Object request, Object step, Object livePreview,
                                      Object examplesHistory, Object resumeState) throws IOException {
        String dumpId = timestamp() + "_" + slugifyPseudo(pseudo) + "_" + jobId;
        Map<String, Object> record = new LinkedHashMap<>();
        record.put("id", dumpId);
        record.put("job_id", jobId);
        record.put("pseudo", blankToNull(pseudo));
        record.put("stopped_at", isoNow());
        record.put("request", request);
        record.put("step", step);
        record.put("live_preview", livePreview);
        record.put("examples_history", examplesHistory);
        record.put("resume_state", resumeState);
        write(STOP_DUMP_DIR.resolve(dumpId + ".json"), record);
        return dumpId;
    }
}
