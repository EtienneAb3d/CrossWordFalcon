package falcon;

import falcon.gen.Cells;
import falcon.gen.Fill;
import falcon.gen.Generator;
import falcon.gen.Grids;
import falcon.gen.Interactive;
import falcon.gen.LenIndex;
import falcon.gen.Rng;
import falcon.gen.Words;
import falcon.gen.Words.DualIndex;
import falcon.gen.Words.PW;

import java.io.BufferedReader;
import java.io.IOException;
import java.io.OutputStream;
import java.io.Writer;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.nio.file.StandardOpenOption;
import java.time.LocalDate;
import java.time.LocalDateTime;
import java.time.format.DateTimeFormatter;
import java.util.ArrayList;
import java.util.Collection;
import java.util.Collections;
import java.util.HashMap;
import java.util.HashSet;
import java.util.Iterator;
import java.util.LinkedHashMap;
import java.util.LinkedHashSet;
import java.util.List;
import java.util.Locale;
import java.util.Map;
import java.util.Set;
import java.util.TreeMap;
import java.util.TreeSet;
import java.util.UUID;
import java.util.concurrent.CompletableFuture;
import java.util.concurrent.ConcurrentHashMap;
import java.util.concurrent.ThreadLocalRandom;
import java.util.function.BooleanSupplier;
import java.util.regex.Matcher;
import java.util.regex.Pattern;

/**
 * The CrossWordFalcon API server (Java port of backend/app.py): same port,
 * same routes, same JSON shapes, same on-disk stores. Single process, like
 * the Python back end: JOBS, the GRID/CLUES queues and the schedulers live
 * in this JVM's memory.
 */
public final class App {
    private App() {}

    // ================================================================== configuration

    static final Path PROJECT_ROOT = Env.ROOT;
    static final Path DATA_DIR = Env.path("data");
    static final Map<String, Path> WORDLISTS = new LinkedHashMap<>();

    static {
        for (String l : List.of("fr", "en", "de", "es", "it", "pt")) WORDLISTS.put(l, DATA_DIR.resolve("wordlist_" + l + "_full.tsv"));
    }

    static final Map<String, Long> BUDGET_MODES = new TreeMap<>(Map.of("flash", 1000L, "turbo", 10000L, "fast", 100000L,
            "medium", 500000L, "ultra", 5000000L));
    static final Path RSS_DIR = Env.path("RSS");
    static final Path SCRAPP_DIR = Env.path("SCRAPP");
    static final int RSS_FETCH_HOUR = 8;
    static final Path CHAT_LOG_DIR = Env.path("LOG_CHAT");
    static final Path USERS_LOG_DIR = Env.path("LOG_USERS");
    static final boolean CHATBOT_DEBUG = List.of("1", "true", "yes", "on").contains(Py.strip(Env.get("CHATBOT_DEBUG", "")).toLowerCase(Locale.ROOT));
    static final boolean EXPERIMENTAL_NOTICE = !List.of("0", "false", "no", "off").contains(
            Py.strip(Env.get("CROSSWORDFALCON_EXPERIMENTAL_NOTICE", "1")).toLowerCase(Locale.ROOT));
    static final int MAX_JOBS = 50;
    static final int LIBRARY_PAGE_SIZE = 20;
    static final int MAX_PSEUDO_LENGTH = 15;
    static final int MAX_SECRET_LENGTH = 60;
    static final int PRESENCE_TTL_S = 60;
    static final int MAX_PRESENCE_ENTRIES = 5000;
    static final int PRESENCE_SWEEP_INTERVAL_S = 10;
    static final double RESOURCE_USAGE_SAMPLE_INTERVAL_S = 2.0;
    static final double QUEUE_STATUS_POLL_INTERVAL_S = 2.0;
    static final long MAX_TURN_DURATION_S = 15 * 60;
    static final String DEFINE_DIFFICULTY = "medium";
    static final int DEFINE_COUNT = 10;
    static final int PARAPHRASE_COUNT = 5;
    static final double PARAPHRASE_TIMEOUT_S = 90.0;
    static final double CORRECT_TIMEOUT_S = 90.0;
    static final int RANDOM_THEME_WORD_MIN_LEN = 5;
    static final int RANDOM_THEME_WORD_MAX_LEN = 10;

    static final Clues CLUE_GENERATOR = new Clues();
    static final ChatBot CHATBOT = new ChatBot(null, null, null);
    static final Clues INTERACTIVE_CLUE_GENERATOR;
    static final ChatBot INTERACTIVE_CHATBOT;

    static {
        String url = Py.strip(Env.get("LLM_BASE_URL_INTERACTIVE", ""));
        if (!url.isEmpty() && !url.equals(CLUE_GENERATOR.baseUrl)) {
            String model = Py.strip(Env.get("LLM_MODEL_INTERACTIVE", ""));
            if (model.isEmpty()) model = CLUE_GENERATOR.model;
            String key = Py.strip(Env.get("LLM_API_KEY_INTERACTIVE", ""));
            if (key.isEmpty()) key = CLUE_GENERATOR.apiKey;
            INTERACTIVE_CLUE_GENERATOR = new Clues(url, model, key);
            INTERACTIVE_CHATBOT = new ChatBot(url, model, key);
            Log.info("Second LLM instance for interactive requests: base_url=%s model=%s", url, model);
        } else {
            INTERACTIVE_CLUE_GENERATOR = CLUE_GENERATOR;
            INTERACTIVE_CHATBOT = CHATBOT;
        }
    }

    // ================================================================== state

    static final LinkedHashMap<String, Job> JOBS = new LinkedHashMap<>();
    static final Map<String, Session> INTERACTIVE_SESSIONS = new ConcurrentHashMap<>();
    static final List<Task> GRID_QUEUE = Collections.synchronizedList(new ArrayList<>());
    static final List<Task> CLUES_QUEUE = Collections.synchronizedList(new ArrayList<>());
    static final Map<String, Object[]> PRESENCE = new HashMap<>();
    static final Object PRESENCE_LOCK = new Object();
    static List<String> lastLoggedPseudos;
    static volatile Map<String, Object> LATEST_RESOURCE_USAGE = Json.obj("cpu_percent", null, "gpu_percent", List.of());
    static final Map<String, Path> CHAT_LOG_PATHS = new ConcurrentHashMap<>();

    /** An interactive authoring session's non-serializable state. */
    static final class Session {
        final DualIndex index;
        final Set<String> priorityWords;
        final Rng rng;
        final Object seed;
        final String resumedFrom;

        Session(DualIndex index, Set<String> priorityWords, Rng rng, Object seed, String resumedFrom) {
            this.index = index;
            this.priorityWords = priorityWords;
            this.rng = rng;
            this.seed = seed;
            this.resumedFrom = resumedFrom;
        }

        PW pw() {
            return PW.single(priorityWords);
        }
    }

    /** One entry of GRID_QUEUE / CLUES_QUEUE. */
    static final class Task {
        final String jobId;
        final GenReq req;

        Task(String jobId, GenReq req) {
            this.jobId = jobId;
            this.req = req;
        }

        boolean isPopulate() {
            return req != null && "populate".equals(req.source);
        }
    }

    // ================================================================== jobs & queues

    static String newJob() {
        synchronized (JOBS) {
            if (JOBS.size() >= MAX_JOBS) {
                String oldest = JOBS.keySet().iterator().next();
                JOBS.remove(oldest);
                INTERACTIVE_SESSIONS.remove(oldest);
            }
            String id = UUID.randomUUID().toString().replace("-", "");
            JOBS.put(id, new Job(id));
            return id;
        }
    }

    static Job job(String id) {
        synchronized (JOBS) {
            return JOBS.get(id);
        }
    }

    static void runInBackground(String name, Runnable r) {
        Thread t = new Thread(r, name);
        t.setDaemon(true);
        t.start();
    }

    static void sleepS(double s) {
        try {
            Thread.sleep((long) (s * 1000));
        } catch (InterruptedException e) {
            Thread.currentThread().interrupt();
        }
    }

    static void waitInQueue(List<Task> queue, Task task, Job job, String stepCode) {
        while (true) {
            synchronized (queue) {
                if (!queue.isEmpty() && queue.get(0) == task) return;
                if (job.cancel.get()) {
                    queue.remove(task);
                    throw new GenerationCancelled();
                }
                job.put("step", Json.obj("code", stepCode, "position", queue.indexOf(task) + 1, "queue_length", queue.size()));
            }
            sleepS(QUEUE_STATUS_POLL_INTERVAL_S);
        }
    }

    static BooleanSupplier makeShouldPause(List<Task> queue, Task task) {
        long turnStart = System.nanoTime();
        boolean isPopulate = task.isPopulate();
        return () -> {
            synchronized (queue) {
                if (isPopulate) {
                    for (Task other : queue) if (other != task && !other.isPopulate()) return true;
                }
                return (System.nanoTime() - turnStart) / 1e9 >= MAX_TURN_DURATION_S && queue.size() > 1;
            }
        };
    }

    static void requeue(List<Task> queue, Task task) {
        synchronized (queue) {
            queue.remove(task);
            queue.add(task);
        }
    }

    // ================================================================== helpers

    static Web.HttpError http(int status, Object detail) {
        return new Web.HttpError(status, detail);
    }

    static boolean wordlistExists(String lang) {
        return lang != null && WORDLISTS.containsKey(lang) && Files.exists(WORDLISTS.get(lang));
    }

    static String sortedLangs() {
        return "['de', 'en', 'es', 'fr', 'it', 'pt']";
    }

    static void validateGenerateRequest(GenReq req) {
        if (!WORDLISTS.containsKey(req.language)) {
            throw http(400, "langue inconnue : " + Log.repr(req.language) + " (attendu : " + sortedLangs() + ")");
        }
        if (!Files.exists(WORDLISTS.get(req.language))) {
            throw http(400, "le dictionnaire pour " + Log.repr(req.language) + " n'est pas encore construit sur ce serveur — réessayez plus tard.");
        }
        if (req.bilingualLanguage != null && !req.bilingualLanguage.equals(req.language)) {
            if (!WORDLISTS.containsKey(req.bilingualLanguage)) {
                throw http(400, "langue bilingue inconnue : " + Log.repr(req.bilingualLanguage) + " (attendu : " + sortedLangs() + ")");
            }
            if (!Files.exists(WORDLISTS.get(req.bilingualLanguage))) {
                throw http(400, "le dictionnaire pour " + Log.repr(req.bilingualLanguage) + " n'est pas encore construit sur ce serveur — réessayez plus tard.");
            }
        }
        if (!Words.DIFFICULTY_PRESETS.containsKey(req.difficulty)) {
            throw http(400, "difficulté inconnue : " + Log.repr(req.difficulty) + " (attendu : ['easy', 'hard', 'medium'])");
        }
        if (!BUDGET_MODES.containsKey(req.mode)) {
            throw http(400, "mode inconnu : " + Log.repr(req.mode) + " (attendu : ['fast', 'flash', 'medium', 'turbo', 'ultra'])");
        }
    }

    static void requireLang(String lang) {
        if (!WORDLISTS.containsKey(lang)) throw http(400, "langue inconnue : " + Log.repr(lang));
    }

    static String pseudoOf(String p) {
        String s = p == null ? "" : Py.strip(p);
        if (s.length() > MAX_PSEUDO_LENGTH) s = s.substring(0, MAX_PSEUDO_LENGTH);
        return s.isEmpty() ? null : s;
    }

    static Set<String> challengeSet(List<String> raw) {
        Set<String> out = new LinkedHashSet<>();
        for (String w : raw) {
            if (w == null || w.isEmpty()) continue;
            String gf = Words.challengeWordGridForm(w);
            if (!gf.isEmpty()) out.add(gf);
        }
        return out;
    }

    static int[] cellsOf(List<int[]> raw) {
        int[] out = new int[raw.size()];
        for (int i = 0; i < raw.size(); i++) out[i] = Cells.of(raw.get(i)[0], raw.get(i)[1]);
        return out;
    }

    static char[][] gridOf(List<Object> json) {
        return Grids.fromJson(json, Grids.WHITE);
    }

    static Session session(String jobId) {
        Session s = INTERACTIVE_SESSIONS.get(jobId);
        if (s == null) throw http(404, "session interactive inconnue (expirée ?)");
        return s;
    }

    static int[] dims(List<Object> grid) {
        int rows = grid.size();
        int cols = rows > 0 ? Json.asList(grid.get(0)).size() : 0;
        if (rows < 1 || cols < 1) throw http(400, "grille vide");
        return new int[]{rows, cols};
    }

    static String truncate(String s, int n) {
        return s.length() <= n ? s : s.substring(0, n) + "…";
    }

    // ================================================================== presence

    static Object[] presenceSnapshot(String sid, String pseudo) {
        synchronized (PRESENCE_LOCK) {
            long now = System.nanoTime();
            if (sid != null) PRESENCE.put(sid, new Object[]{now, pseudo});
            PRESENCE.entrySet().removeIf(e -> (now - (long) e.getValue()[0]) / 1e9 > PRESENCE_TTL_S);
            if (PRESENCE.size() > MAX_PRESENCE_ENTRIES) {
                List<Map.Entry<String, Object[]>> entries = new ArrayList<>(PRESENCE.entrySet());
                entries.sort((a, b) -> Long.compare((long) a.getValue()[0], (long) b.getValue()[0]));
                for (int k = 0; k < entries.size() - MAX_PRESENCE_ENTRIES; k++) PRESENCE.remove(entries.get(k).getKey());
            }
            Set<String> named = new HashSet<>();
            int anonymous = 0;
            for (Object[] e : PRESENCE.values()) {
                String p = (String) e[1];
                if (p != null && !p.isEmpty()) named.add(p);
                else anonymous++;
            }
            int count = named.size() + anonymous;
            List<String> pseudos = new ArrayList<>(named);
            pseudos.sort((a, b) -> a.toLowerCase(Locale.ROOT).compareTo(b.toLowerCase(Locale.ROOT)));
            for (int k = 0; k < anonymous; k++) pseudos.add("(anonyme)");
            boolean changed = !pseudos.equals(lastLoggedPseudos);
            if (changed) lastLoggedPseudos = pseudos;
            return new Object[]{count, pseudos, changed};
        }
    }

    static void writeUsersLog(int count, List<String> pseudos) {
        try {
            Files.createDirectories(USERS_LOG_DIR);
            LocalDateTime now = LocalDateTime.now();
            Path path = USERS_LOG_DIR.resolve(now.format(DateTimeFormatter.ofPattern("yyyy-MM-dd")) + ".log");
            String line = now.format(DateTimeFormatter.ofPattern("yyyy-MM-dd HH:mm:ss")) + " | " + count + " | "
                    + (pseudos.isEmpty() ? "—" : String.join(", ", pseudos)) + "\n";
            Files.writeString(path, line, StandardCharsets.UTF_8, StandardOpenOption.CREATE, StandardOpenOption.APPEND);
        } catch (IOException e) {
            Log.warning("failed to write LOG_USERS journal: %s", e.getMessage());
        }
    }

    // ================================================================== background schedulers

    static Path pythonExecutable() {
        Path venv = PROJECT_ROOT.resolve(".venv/bin/python");
        return Files.isExecutable(venv) ? venv : Path.of("python3");
    }

    /** Runs a scraper module's fetch_all() (scrapper/ stays in Python). */
    static void runScraper(String module, String label) {
        try {
            ProcessBuilder pb = new ProcessBuilder(pythonExecutable().toString(), "-c",
                    "import sys; sys.path.insert(0, '.'); from scrapper import " + module + " as m; r = m.fetch_all(); "
                            + "print('COUNT', len(r) if r is not None else 0)")
                    .directory(PROJECT_ROOT.toFile()).redirectErrorStream(true);
            Process p = pb.start();
            String out;
            try (BufferedReader r = new BufferedReader(new java.io.InputStreamReader(p.getInputStream(), StandardCharsets.UTF_8))) {
                out = r.lines().reduce("", (a, b) -> a + b + "\n");
            }
            int code = p.waitFor();
            Matcher m = Pattern.compile("COUNT (\\d+)").matcher(out);
            if (code == 0 && m.find()) {
                Log.info("%s: %s %s", label, m.group(1), label.equals("rss") ? "articles rafraichis" : "grilles rafraichies");
            } else {
                Log.error("%s: echec du rafraichissement\n%s", label, truncate(out, 4000));
            }
        } catch (IOException e) {
            Log.error("%s: echec du rafraichissement (%s)", label, e.getMessage());
        } catch (InterruptedException e) {
            Thread.currentThread().interrupt();
        }
    }

    static boolean combinedJsonIsFresh(Path path) {
        try {
            Map<String, Object> data = Json.asMap(Json.readFile(path));
            String fetched = (String) data.get("fetched_at");
            java.time.temporal.TemporalAccessor t = DateTimeFormatter.ISO_DATE_TIME.parseBest(fetched,
                    java.time.OffsetDateTime::from, LocalDateTime::from);
            LocalDate d = t instanceof java.time.OffsetDateTime odt
                    ? odt.atZoneSameInstant(java.time.ZoneId.systemDefault()).toLocalDate()
                    : ((LocalDateTime) t).toLocalDate();
            return d.equals(LocalDate.now());
        } catch (Exception e) {
            return false;
        }
    }

    static void startSchedulers() {
        runInBackground("rss-daily", () -> {
            while (true) {
                LocalDateTime now = LocalDateTime.now();
                LocalDateTime next = now.withHour(RSS_FETCH_HOUR).withMinute(0).withSecond(0).withNano(0);
                if (!next.isAfter(now)) next = next.plusDays(1);
                sleepS(java.time.Duration.between(now, next).toMillis() / 1000.0);
                runScraper("fetch_rss_feeds", "rss");
                runScraper("fetch_grid_links", "scrapp");
            }
        });
        runInBackground("rss-catchup", () -> {
            if (!combinedJsonIsFresh(RSS_DIR.resolve("combined.json"))) runScraper("fetch_rss_feeds", "rss");
            if (!combinedJsonIsFresh(SCRAPP_DIR.resolve("combined.json"))) runScraper("fetch_grid_links", "scrapp");
        });
        runInBackground("presence-sweep", () -> {
            while (true) {
                sleepS(PRESENCE_SWEEP_INTERVAL_S);
                try {
                    Object[] snap = presenceSnapshot(null, null);
                    @SuppressWarnings("unchecked")
                    List<String> pseudos = (List<String>) snap[1];
                    if ((boolean) snap[2]) writeUsersLog((int) snap[0], pseudos);
                } catch (RuntimeException e) {
                    Log.exception("presence: echec du balayage periodique", e);
                }
            }
        });
        runInBackground("resource-usage", () -> {
            while (true) {
                try {
                    LATEST_RESOURCE_USAGE = SystemInfo.sampleResourceUsage();
                } catch (RuntimeException e) {
                    Log.exception("resource usage: echec de l'echantillonnage", e);
                }
                sleepS(RESOURCE_USAGE_SAMPLE_INTERVAL_S);
            }
        });
    }

    // ================================================================== chat log

    static Path chatLogPath(String sessionId) {
        if (sessionId == null || sessionId.isEmpty()) sessionId = "sans-session-" + UUID.randomUUID().toString().replace("-", "").substring(0, 8);
        final String sid = sessionId;
        return CHAT_LOG_PATHS.computeIfAbsent(sid, k -> {
            try {
                Files.createDirectories(CHAT_LOG_DIR);
            } catch (IOException ignored) { }
            String ts = LocalDateTime.now().format(DateTimeFormatter.ofPattern("yyyyMMdd-HHmmss"));
            StringBuilder safe = new StringBuilder();
            for (char ch : sid.toCharArray()) safe.append(Character.isLetterOrDigit(ch) || ch == '-' || ch == '_' ? ch : '_');
            String s = safe.length() > 64 ? safe.substring(0, 64) : safe.toString();
            return CHAT_LOG_DIR.resolve(ts + "_" + s + ".md");
        });
    }

    static void appendChatLog(String sessionId, String language, String message, String reply, Double firstTokenS,
                              Double totalS, List<Object> promptMessages) {
        Path path = chatLogPath(sessionId);
        StringBuilder sb = new StringBuilder();
        sb.append("## ").append(LocalDateTime.now().format(DateTimeFormatter.ofPattern("yyyy-MM-dd HH:mm:ss")))
                .append(" (").append(language).append(")\n\n");
        sb.append("**Utilisateur** : ").append(message).append("\n\n");
        if (promptMessages != null && !promptMessages.isEmpty()) {
            int total = 0;
            List<String> parts = new ArrayList<>();
            for (int i = 0; i < promptMessages.size(); i++) {
                Object m = promptMessages.get(i);
                String content = Json.str(m, "content", "");
                total += content.length();
                parts.add("========== [" + (i + 1) + "/" + promptMessages.size() + "] role=" + Json.str(m, "role", "?")
                        + " ==========\n" + content);
            }
            sb.append("<details>\n<summary>Prompt complet envoyé au LLM — ").append(promptMessages.size())
                    .append(" messages, ").append(total).append(" caractères</summary>\n\n~~~~~~\n")
                    .append(String.join("\n\n", parts)).append("\n~~~~~~\n\n</details>\n\n");
        }
        sb.append("**David FALCON** : ").append(reply).append("\n\n");
        if (firstTokenS != null || totalS != null) {
            List<String> bits = new ArrayList<>();
            if (firstTokenS != null) bits.add("premier mot reçu après " + Py.fmt(firstTokenS, 2) + "s");
            if (totalS != null) bits.add("temps total : " + Py.fmt(totalS, 2) + "s");
            sb.append("*").append(String.join(" — ", bits)).append("*\n\n");
        }
        sb.append("---\n\n");
        try {
            Files.writeString(path, sb.toString(), StandardCharsets.UTF_8, StandardOpenOption.CREATE, StandardOpenOption.APPEND);
        } catch (IOException e) {
            Log.exception("chat: echec d'ecriture du log pour la session " + sessionId, e);
        }
    }

    // ================================================================== word verification table

    static Map<String, String> loadWordlistRawLines(String language) {
        Map<String, String> lines = new HashMap<>();
        Path p = WORDLISTS.get(language);
        if (p == null) return lines;
        try (BufferedReader r = Files.newBufferedReader(p, StandardCharsets.UTF_8)) {
            String line;
            while ((line = r.readLine()) != null) {
                if (line.isEmpty() || line.startsWith("#")) continue;
                int tab = line.indexOf('\t');
                String mot = (tab < 0 ? line : line.substring(0, tab)).toUpperCase(Locale.ROOT);
                lines.putIfAbsent(mot, line);
            }
        } catch (IOException ignored) { }
        return lines;
    }

    static Map<String, String> loadGlossRawLines(String language) {
        Map<String, String> lines = new HashMap<>();
        Path p = DATA_DIR.resolve("gloss_dictionary").resolve(language + "_glosses.jsonl");
        if (!Files.exists(p)) return lines;
        try (BufferedReader r = Files.newBufferedReader(p, StandardCharsets.UTF_8)) {
            String line;
            while ((line = r.readLine()) != null) {
                String s = Py.strip(line);
                if (s.isEmpty()) continue;
                try {
                    Object e = Json.parse(s);
                    String word = Json.str(e, "word", null);
                    if (word != null && !word.isEmpty()) lines.putIfAbsent(word.toLowerCase(Locale.ROOT), s);
                } catch (IllegalArgumentException ignored) { }
            }
        } catch (IOException ignored) { }
        return lines;
    }

    static List<Object> buildWordVerificationTable(List<Object> words, String language, String bilingualLanguage) {
        Map<String, Map<String, String>> wl = new HashMap<>(), gl = new HashMap<>();
        wl.put(language, loadWordlistRawLines(language));
        gl.put(language, loadGlossRawLines(language));
        if (bilingualLanguage != null && !bilingualLanguage.equals(language)) {
            wl.put(bilingualLanguage, loadWordlistRawLines(bilingualLanguage));
            gl.put(bilingualLanguage, loadGlossRawLines(bilingualLanguage));
        }
        List<Object> sorted = new ArrayList<>(words);
        sorted.sort((a, b) -> {
            int c = Integer.compare(Json.integer(a, "row", 0), Json.integer(b, "row", 0));
            if (c != 0) return c;
            c = Integer.compare(Json.integer(a, "col", 0), Json.integer(b, "col", 0));
            return c != 0 ? c : Json.str(a, "direction", "").compareTo(Json.str(b, "direction", ""));
        });
        List<Object> rows = new ArrayList<>();
        for (Object w : sorted) {
            String answer = Json.str(w, "answer", "");
            String lang = Json.str(w, "language", language);
            Map<String, String> wlines = wl.getOrDefault(lang, wl.get(language));
            Map<String, String> glines = gl.getOrDefault(lang, gl.get(language));
            String wline = wlines.get(answer);
            List<Object> matched = new ArrayList<>();
            if (wline != null) {
                Object canon = Json.get(w, "canonical");
                List<Object> canonList = Json.truthy(canon) && canon instanceof List<?> ? Json.asList(canon)
                        : Json.list(Json.str(w, "accented", answer));
                for (Object lemma : canonList) {
                    String g = glines.get(String.valueOf(lemma).toLowerCase(Locale.ROOT));
                    if (g != null) matched.add(g);
                }
            }
            Map<String, Object> row = new LinkedHashMap<>();
            row.put("row", Json.get(w, "row"));
            row.put("col", Json.get(w, "col"));
            row.put("direction", Json.get(w, "direction"));
            row.put("answer", answer);
            row.put("in_wordlist", wline != null);
            row.put("wordlist_line", wline);
            row.put("gloss_lines", matched);
            rows.add(row);
        }
        return rows;
    }

    static List<Object> wordCellsJson(List<Object> words, Set<String> answers) {
        TreeSet<Integer> cells = new TreeSet<>();
        for (Object w : words) {
            String ans = Json.str(w, "answer", "");
            if (!answers.contains(ans)) continue;
            boolean across = "across".equals(Json.get(w, "direction"));
            int r = Json.integer(w, "row", 0), c = Json.integer(w, "col", 0);
            for (int dk = 0; dk < ans.length(); dk++) cells.add(Cells.of(r + (across ? 0 : dk), c + (across ? dk : 0)));
        }
        return Cells.toJson(cells);
    }

    // ================================================================== generation job

    /** Optional arguments of _run_generate_job. */
    static final class GenJobArgs {
        Map<String, Object> resumeState;
        List<String> overridePriorityWords;
        boolean hasOverride;
        String overrideThemeDescription = "";
        Map<String, String> preservedClues;
        Map<Integer, Character> permanentLocked;
        Set<Integer> permanentBlack;
        boolean publish = true;
        Object origin;
        Map<Integer, String> zoneRevert;
        Set<Integer> requiredCells;
    }

    @SuppressWarnings("unchecked")
    static void applyZoneRevert(Map<Integer, String> zoneRevert, Object examples) {
        if (zoneRevert == null || zoneRevert.isEmpty() || !(examples instanceof List<?> l) || l.isEmpty()) return;
        for (Object o : l) {
            Map<String, Object> ex = (Map<String, Object>) o;
            Object eg = ex.get("example_grid");
            if (!(eg instanceof List<?> rows) || rows.isEmpty()) continue;
            zoneRevert.forEach((cell, original) -> {
                List<Object> row = (List<Object>) rows.get(Cells.r(cell));
                row.set(Cells.c(cell), original.equals("#") ? "#" : original.equals(".") ? "." : original);
            });
            TreeSet<Integer> locked = new TreeSet<>(zoneRevert.keySet());
            for (Object c : Json.listOrEmpty(ex.get("locked_cells"))) {
                List<Object> rc = Json.asList(c);
                locked.add(Cells.of(((Number) rc.get(0)).intValue(), ((Number) rc.get(1)).intValue()));
            }
            ex.put("locked_cells", Cells.toJson(locked));
        }
    }

    static void runGenerateJob(String jobId, GenReq req, GenJobArgs a) {
        Job job = job(jobId);
        if (job == null) return;
        String shortId = jobId.substring(0, 8);
        job.put("request", req.dump());
        Task task = new Task(jobId, req);
        Map<String, Long> phaseTimes = new ConcurrentHashMap<>();
        Generator.Progress progress = (step, data) -> {
            if (step.equals("budget_progress")) {
                job.update(d -> {
                    Map<String, Object> s = new LinkedHashMap<>(Json.mapOrEmpty(d.get("step")));
                    s.put("budget_percent", data.get("percent"));
                    d.put("step", s);
                });
                return;
            }
            if (step.equals("success_count")) {
                // A counter, not a status: never replaces "step".
                job.update(d -> d.put("success_count", data.getOrDefault("count", 0)));
                return;
            }
            applyZoneRevert(a.zoneRevert, data.get("examples"));
            Map<String, Object> newStep = new LinkedHashMap<>();
            newStep.put("code", step);
            newStep.putAll(data);
            if (step.equals("minimizing") || step.equals("grid_ready")) phaseTimes.put(step, System.nanoTime());
            job.update(d -> {
                d.put("step", newStep);
                Object ex = data.get("examples");
                if (Json.truthy(ex) && !step.equals("pattern")) {
                    Map<String, Object> sw = new LinkedHashMap<>(newStep);
                    sw.remove("examples");
                    sw.remove("word_table");
                    Map<String, Object> entry = new LinkedHashMap<>();
                    entry.put("step", sw);
                    entry.put("examples", ex);
                    if (data.containsKey("word_table")) entry.put("word_table", data.get("word_table"));
                    Json.asList(d.get("examples_history")).add(entry);
                }
                if (data.containsKey("resume_state")) d.put("resume_state", data.get("resume_state"));
                Object nc = data.get("new_clue");
                if (Json.truthy(nc)) Json.asList(d.get("clues_progress")).add(nc);
            });
            Log.info("[%s] %s %s", shortId, step, truncate(Json.dumps(data), 600));
        };
        try {
            Log.info("[%s] starting generation: language=%s bilingual_language=%s width=%s height=%s difficulty=%s "
                            + "force_letters_percent=%s black_enrichment_percent=%s mode=%s theme_precision=%s source=%s", shortId,
                    req.language, req.bilingualLanguage, req.width, req.height, req.difficulty, req.forceLettersPercent,
                    req.blackEnrichmentPercent, req.mode, req.themePrecision, req.source);
            String theme = req.theme == null ? "" : Py.strip(req.theme);
            List<String> themePriority = null, bilingualThemePriority = null;
            String themeDescription = "";
            if (a.hasOverride) {
                themePriority = a.overridePriorityWords;
                themeDescription = a.overrideThemeDescription;
            } else if (!theme.isEmpty()) {
                progress.on("theme", Json.obj("theme", theme));
                if (req.isBilingual()) {
                    CompletableFuture<Object[]> f1 = CompletableFuture.supplyAsync(() -> Themes.buildThemeGlossary(theme,
                            req.language, req.themePrecision, shortId, job.cancel, shortId, null, CLUE_GENERATOR));
                    CompletableFuture<Object[]> f2 = CompletableFuture.supplyAsync(() -> Themes.buildThemeGlossary(theme,
                            req.bilingualLanguage, req.themePrecision, shortId, job.cancel, shortId + "_" + req.bilingualLanguage,
                            req.language, CLUE_GENERATOR));
                    Object[] r1 = joinUnwrapped(f1), r2 = joinUnwrapped(f2);
                    @SuppressWarnings("unchecked")
                    List<String> p1 = (List<String>) r1[0];
                    @SuppressWarnings("unchecked")
                    List<String> p2 = (List<String>) r2[0];
                    themePriority = p1;
                    themeDescription = (String) r1[1];
                    bilingualThemePriority = p2;
                } else {
                    Object[] r = Themes.buildThemeGlossary(theme, req.language, req.themePrecision, shortId, job.cancel, shortId,
                            null, CLUE_GENERATOR);
                    @SuppressWarnings("unchecked")
                    List<String> p1 = (List<String>) r[0];
                    themePriority = p1;
                    themeDescription = (String) r[1];
                }
            }
            Set<String> challengeWords = challengeSet(req.challengeWords);
            Map<String, Object> result;
            long gridStart = System.nanoTime();
            double gridPausedS = 0;
            GRID_QUEUE.add(task);
            try {
                Map<String, Object> gridResume = a.resumeState;
                while (true) {
                    waitInQueue(GRID_QUEUE, task, job, "queued_grid");
                    gridStart = System.nanoTime();
                    try {
                        Generator.Params p = new Generator.Params();
                        p.width = req.width;
                        p.height = req.height;
                        p.difficulty = req.difficulty;
                        p.seed = req.seed;
                        p.wordlistPath = WORDLISTS.get(req.language).toString();
                        p.bilingualWordlistPath = req.isBilingual() ? WORDLISTS.get(req.bilingualLanguage).toString() : null;
                        p.onProgress = progress;
                        p.onLivePreview = examples -> {
                            applyZoneRevert(a.zoneRevert, examples);
                            job.put("live_preview", examples);
                        };
                        p.forceLettersFraction = req.forceLettersPercent / 100.0;
                        p.blackEnrichmentFraction = req.blackEnrichmentPercent / 100.0;
                        p.cancelEvent = job.cancel;
                        p.deadlineChecks = BUDGET_MODES.get(req.mode);
                        p.resumeState = gridResume;
                        p.shouldPause = makeShouldPause(GRID_QUEUE, task);
                        p.priorityWords = themePriority == null ? null : new LinkedHashSet<>(themePriority);
                        p.bilingualPriorityWords = bilingualThemePriority == null ? null : new LinkedHashSet<>(bilingualThemePriority);
                        p.permanentLockedLetters = a.permanentLocked;
                        p.permanentBlackCells = a.permanentBlack;
                        p.requiredCells = a.requiredCells;
                        p.challengeWords = new ArrayList<>(challengeWords);
                        result = Generator.generateGrid(p);
                        break;
                    } catch (GenerationPaused paused) {
                        gridPausedS += (System.nanoTime() - gridStart) / 1e9;
                        @SuppressWarnings("unchecked")
                        Map<String, Object> st = (Map<String, Object>) paused.state;
                        gridResume = st;
                        Log.info("[%s] grid turn paused, back of the queue", shortId);
                        requeue(GRID_QUEUE, task);
                    }
                }
            } finally {
                GRID_QUEUE.remove(task);
            }
            if (result == null) {
                job.update(d -> {
                    d.put("status", "error");
                    d.put("error_code", "no_fillable_grid");
                    d.put("error", "Aucune grille remplissable trouvée avec ces paramètres, réessayez ou changez la taille/difficulté.");
                });
                Log.warning("[%s] no fillable grid found", shortId);
                return;
            }
            if (a.zoneRevert != null && !a.zoneRevert.isEmpty()) {
                List<Object> pattern = Json.asList(result.get("pattern"));
                List<Object> solution = Json.asList(result.get("solution"));
                Map<Integer, String> revertedPattern = new HashMap<>();
                a.zoneRevert.forEach((cell, original) -> {
                    String reverted = original.equals("#") ? "#" : ".";
                    Json.asList(pattern.get(Cells.r(cell))).set(Cells.c(cell), reverted);
                    Json.asList(solution.get(Cells.r(cell))).set(Cells.c(cell),
                            !original.equals("#") && !original.equals(".") ? original : reverted);
                    revertedPattern.put(cell, reverted);
                });
                List<Object> kept = new ArrayList<>();
                for (Object w : Json.asList(result.get("words"))) {
                    String ans = Json.str(w, "answer", "");
                    boolean across = "across".equals(Json.get(w, "direction"));
                    int r = Json.integer(w, "row", 0), c = Json.integer(w, "col", 0);
                    boolean touches = false;
                    for (int dk = 0; dk < ans.length(); dk++) {
                        if (".".equals(revertedPattern.get(Cells.of(r + (across ? 0 : dk), c + (across ? dk : 0))))) touches = true;
                    }
                    if (!touches) kept.add(w);
                }
                result.put("words", kept);
                Log.info("[%s] zone_revert: %d cell(s) reverted outside the selected zone, %d word(s) kept", shortId,
                        a.zoneRevert.size(), kept.size());
            }
            long searchDone = phaseTimes.getOrDefault("minimizing", gridStart);
            long optimizationDone = phaseTimes.getOrDefault("grid_ready", searchDone);
            result.put("generation_duration_seconds", gridPausedS + (searchDone - gridStart) / 1e9);
            result.put("optimization_duration_seconds", (optimizationDone - searchDone) / 1e9);
            result.put("difficulty", req.difficulty);
            result.put("theme", theme.isEmpty() ? null : theme);
            List<Object> words = Json.asList(result.get("words"));
            List<Object> wordTable = buildWordVerificationTable(words, req.language, (String) result.get("bilingual_language"));
            Set<String> themeSet = themePriority == null ? Set.of() : new HashSet<>(themePriority);
            List<Object> themeCells = wordCellsJson(words, themeSet);
            List<Object> challengeCells = wordCellsJson(words, challengeWords);
            Map<String, String> preservedByWord = a.preservedClues == null ? Map.of() : a.preservedClues;
            List<Object> needingClue = new ArrayList<>();
            for (Object w : words) {
                String pc = preservedByWord.get(Json.str(w, "answer", ""));
                if (pc == null || pc.isEmpty()) needingClue.add(w);
            }
            int totalForClues = needingClue.size();
            Map<String, Object> clueExample = new LinkedHashMap<>();
            clueExample.put("example_grid", result.get("solution"));
            clueExample.put("impossible_cells", List.of());
            clueExample.put("forced_cells", List.of());
            clueExample.put("locked_cells", List.of());
            clueExample.put("theme_cells", themeCells);
            clueExample.put("challenge_cells", challengeCells);
            clueExample.put("process_number", result.get("winning_process_number"));
            clueExample.put("is_best", true);
            progress.on("clues", Json.obj("current", 0, "total", totalForClues, "examples", Json.list(clueExample), "word_table", wordTable));
            String title;
            double cluesComputeS = 0;
            CLUES_QUEUE.add(task);
            try {
                List<Clues.Entry> remaining = new ArrayList<>();
                Map<String, Object> wordsByAnswer = new HashMap<>();
                for (Object w : words) wordsByAnswer.put(Json.str(w, "answer", ""), w);
                for (Object w : needingClue) remaining.add(entryOf(w));
                if (!themeDescription.isEmpty()) Log.info("[%s] clue generation steered by theme keyword list: %s", shortId, Log.repr(themeDescription));
                Map<String, String> accumulated = new ConcurrentHashMap<>();
                final String td = themeDescription;
                while (true) {
                    waitInQueue(CLUES_QUEUE, task, job, "queued_clues");
                    long cluesStart = System.nanoTime();
                    try {
                        final int before = accumulated.size();
                        Map<String, String> newClues = CLUE_GENERATOR.generate(remaining, req.difficulty, req.language,
                                Clues.DEFAULT_TIMEOUT, (current, total, answer, clue) -> progress.on("clues", Json.obj(
                                        "current", before + current, "total", totalForClues, "new_clue", clue != null
                                                ? Json.obj("answer", answer, "accented", Json.str(wordsByAnswer.get(answer), "accented", answer), "clue", clue)
                                                : null)),
                                job.cancel, makeShouldPause(CLUES_QUEUE, task), td, "populate".equals(req.source) ? 1 : null);
                        accumulated.putAll(newClues);
                        cluesComputeS += (System.nanoTime() - cluesStart) / 1e9;
                        break;
                    } catch (GenerationPaused paused) {
                        cluesComputeS += (System.nanoTime() - cluesStart) / 1e9;
                        Object[] st = (Object[]) paused.state;
                        @SuppressWarnings("unchecked")
                        Map<String, String> partial = (Map<String, String>) st[0];
                        @SuppressWarnings("unchecked")
                        List<Clues.Entry> rem = (List<Clues.Entry>) st[1];
                        accumulated.putAll(partial);
                        remaining = rem;
                        Log.info("[%s] clues turn paused, back of the queue", shortId);
                        requeue(CLUES_QUEUE, task);
                    }
                }
                result.put("clues_duration_seconds", cluesComputeS);
                for (Object w : words) {
                    String ans = Json.str(w, "answer", "");
                    String preserved = preservedByWord.get(ans);
                    Json.asMap(w).put("clue", preserved != null && !preserved.isEmpty() ? preserved : accumulated.getOrDefault(ans, ""));
                }
                List<Clues.Entry> titleEntries = new ArrayList<>();
                for (Object w : words) titleEntries.add(entryOf(w));
                title = CLUE_GENERATOR.generateTitle(titleEntries, req.language, Clues.DEFAULT_TIMEOUT, job.cancel, themeDescription);
                result.put("title", title);
                Log.info("[%s] title: %s", shortId, Log.repr(title));
            } finally {
                CLUES_QUEUE.remove(task);
            }
            progress.on("saving", new LinkedHashMap<>());
            String pseudo = pseudoOf(req.pseudo);
            Map<String, Object> generationParams = Json.obj("black_enrichment_percent", req.blackEnrichmentPercent,
                    "force_letters_percent", req.forceLettersPercent, "mode", req.mode, "theme_precision", req.themePrecision);
            if (a.publish) {
                try {
                    Path svg = SvgExport.saveGridSvg(result, req.language, req.difficulty, req.mode);
                    Log.info("[%s] saved %s", shortId, svg);
                    try {
                        Log.info("[%s] saved %s", shortId, SvgExport.saveGridPng(svg));
                    } catch (IOException e) {
                        Log.warning("[%s] failed to save grid PNG sample: %s", shortId, e.getMessage());
                    }
                } catch (IOException e) {
                    Log.warning("[%s] failed to save grid SVG: %s", shortId, e.getMessage());
                }
                try {
                    String gridId = GridStore.saveGridJson(result, req.language, req.difficulty, req.mode, title,
                            (String) result.get("bilingual_language"), pseudo, theme.isEmpty() ? null : theme, false, null,
                            generationParams, req.challengeWords.isEmpty() ? null : req.challengeWords);
                    Log.info("[%s] saved to library: %s (pseudo=%s)", shortId, gridId, Log.repr(pseudo));
                    result.put("id", gridId);
                } catch (IOException e) {
                    Log.warning("[%s] failed to save grid to library: %s", shortId, e.getMessage());
                }
            } else {
                try {
                    List<Object> definitions = new ArrayList<>();
                    for (Object w : words) {
                        definitions.add(Json.obj("row", Json.get(w, "row"), "col", Json.get(w, "col"), "direction",
                                Json.get(w, "direction"), "clue", Json.str(w, "clue", "")));
                    }
                    String workId = GridStore.saveGridWork(jobId, result.get("solution"), definitions, title, req.language,
                            req.difficulty, theme.isEmpty() ? null : theme, themePriority == null ? List.of() : themePriority,
                            req.seed != null && req.seed != 0 ? req.seed : 0, pseudo, null, a.origin,
                            result.get("bilingual_language"), generationParams, null, null);
                    Log.info("[%s] saved to Créations: %s (pseudo=%s)", shortId, workId, Log.repr(pseudo));
                    result.put("grid_work_id", workId);
                } catch (Exception e) {
                    Log.warning("[%s] failed to save grid to Créations: %s", shortId, e.getMessage());
                }
            }
            progress.on("done", new LinkedHashMap<>());
            final Map<String, Object> finalResult = result;
            job.update(d -> {
                d.put("status", "done");
                d.put("result", finalResult);
            });
            Log.info("[%s] done", shortId);
        } catch (GenerationCancelled e) {
            job.put("status", "cancelled");
            Log.info("[%s] cancelled by user", shortId);
            try {
                String dumpId = GridStore.saveStopDump(jobId, req.pseudo, job.copyOf("request"), job.copyOf("step"),
                        job.copyOf("live_preview"), job.copyOf("examples_history"), job.copyOf("resume_state"));
                Log.info("[%s] stop dump saved: %s", shortId, dumpId);
            } catch (Exception ex) {
                Log.warning("[%s] failed to save stop dump: %s", shortId, ex.getMessage());
            }
        } catch (Clues.ClueGenerationError e) {
            job.update(d -> {
                d.put("status", "error");
                d.put("error_code", "clue_generation_failed");
                d.put("error", e.getMessage());
            });
            Log.warning("[%s] clue generation failed: %s", shortId, e.getMessage());
        } catch (Throwable e) {
            job.update(d -> {
                d.put("status", "error");
                d.put("error_code", "internal_error");
                d.put("error", "Erreur interne.");
            });
            Log.exception("[" + shortId + "] unhandled error during generation", e);
        }
    }

    static Object[] joinUnwrapped(CompletableFuture<Object[]> f) {
        try {
            return f.join();
        } catch (java.util.concurrent.CompletionException e) {
            if (e.getCause() instanceof RuntimeException re) throw re;
            throw e;
        }
    }

    static Clues.Entry entryOf(Object w) {
        String ans = Json.str(w, "answer", "");
        String acc = Json.str(w, "accented", ans);
        List<String> canon = new ArrayList<>();
        Object c = Json.get(w, "canonical");
        if (c instanceof List<?> l) for (Object o : l) canon.add(String.valueOf(o));
        else if (c != null) canon.add(String.valueOf(c));
        return new Clues.Entry(ans, acc, canon, Json.str(w, "language", null));
    }

    // ================================================================== interactive jobs

    static Object[] loadInteractiveIndex(String language, String difficulty, String bilingual) throws IOException {
        Number mw = Words.DIFFICULTY_PRESETS.get(difficulty);
        boolean easy = "easy".equals(difficulty);
        Generator.Loaded a = Generator.load(WORDLISTS.get(language).toString(), mw, easy);
        boolean isBilingual = bilingual != null && !bilingual.isEmpty() && !bilingual.equals(language);
        if (!isBilingual) return new Object[]{new DualIndex(a.index(), a.index()), new HashSet<>(a.lexicon().accents().keySet())};
        Generator.Loaded d = Generator.load(WORDLISTS.get(bilingual).toString(), mw, easy);
        Set<String> known = new HashSet<>(a.lexicon().accents().keySet());
        known.addAll(d.lexicon().accents().keySet());
        return new Object[]{new DualIndex(a.index(), d.index()), known};
    }

    static Set<String> knownUpper(Collection<?> words, Set<String> known) {
        Set<String> out = new LinkedHashSet<>();
        if (words == null) return out;
        for (Object w : words) {
            String u = String.valueOf(w).toUpperCase(Locale.ROOT);
            if (known.contains(u)) out.add(u);
        }
        return out;
    }

    static void runInteractiveJob(String jobId, GenReq req) {
        Job job = job(jobId);
        if (job == null) return;
        String shortId = jobId.substring(0, 8);
        job.put("request", req.dump());
        try {
            String theme = req.theme == null ? "" : Py.strip(req.theme);
            List<String> themePriority = null;
            String themeDescription = "";
            if (!theme.isEmpty()) {
                job.put("step", Json.obj("code", "theme", "theme", theme));
                Object[] r = Themes.buildThemeGlossary(theme, req.language, req.themePrecision, shortId, job.cancel, shortId,
                        null, INTERACTIVE_CLUE_GENERATOR);
                @SuppressWarnings("unchecked")
                List<String> p = (List<String>) r[0];
                themePriority = p;
                themeDescription = (String) r[1];
            }
            job.put("step", Json.obj("code", "interactive_building"));
            boolean isBilingual = req.isBilingual();
            Object[] loaded = loadInteractiveIndex(req.language, req.difficulty, req.bilingualLanguage);
            DualIndex index = (DualIndex) loaded[0];
            @SuppressWarnings("unchecked")
            Set<String> known = (Set<String>) loaded[1];
            Set<String> priority = knownUpper(themePriority, known);
            Rng rng = Rng.of(req.seed);
            int rows = req.height, cols = req.width;
            char[][] grid = Grids.makePattern(rows, cols, 0.0, rng, Words.LengthSets.available(index, Grids.PREFILL_MIN_WORD_COUNT),
                    null, null, index, req.blackEnrichmentPercent / 100.0);
            Set<String> challenge = challengeSet(req.challengeWords);
            Map<String, Object> placed = Interactive.placeWord(grid, rows, cols, index, rng, PW.single(priority), challenge);
            INTERACTIVE_SESSIONS.put(jobId, new Session(index, priority, rng, req.seed, null));
            Map<String, Object> meta = new LinkedHashMap<>();
            meta.put("language", req.language);
            meta.put("bilingual_language", isBilingual ? req.bilingualLanguage : null);
            meta.put("difficulty", req.difficulty);
            meta.put("width", req.width);
            meta.put("height", req.height);
            meta.put("theme", theme.isEmpty() ? null : theme);
            meta.put("has_theme", !priority.isEmpty());
            meta.put("theme_description", themeDescription);
            meta.put("challenge_words", new ArrayList<>(req.challengeWords));
            boolean impossible = Boolean.TRUE.equals(placed.get("impossible"));
            Map<String, Object> result = new LinkedHashMap<>();
            result.put("width", req.width);
            result.put("height", req.height);
            result.put("grid", impossible ? Grids.toJson(grid) : placed.get("grid"));
            result.put("placed", impossible ? null : placed.get("placed"));
            result.put("impossible", impossible);
            result.put("has_theme", !priority.isEmpty());
            result.put("impossible_cells", placed.getOrDefault("impossible_cells", List.of()));
            result.put("low_candidate_cells", placed.getOrDefault("low_candidate_cells", List.of()));
            result.put("deadlock_cells", placed.getOrDefault("deadlock_cells", List.of()));
            result.put("excluded_cells", placed.getOrDefault("excluded_cells", List.of()));
            result.put("window_cells", placed.getOrDefault("window_cells", List.of()));
            result.put("theme", theme.isEmpty() ? null : theme);
            result.put("language", req.language);
            result.put("bilingual_language", meta.get("bilingual_language"));
            result.put("difficulty", req.difficulty);
            result.put("challenge_words", new ArrayList<>(req.challengeWords));
            job.update(d -> {
                d.put("interactive", meta);
                d.put("result", result);
                d.put("step", Json.obj("code", "done"));
                d.put("status", "done");
            });
        } catch (GenerationCancelled e) {
            job.put("status", "cancelled");
        } catch (Throwable e) {
            job.update(d -> {
                d.put("status", "error");
                d.put("error_code", "internal_error");
                d.put("error", "Erreur interne.");
            });
            Log.exception("[" + shortId + "] unhandled error during interactive start", e);
        }
    }

    static void runInteractiveResumeJob(String jobId, Map<String, Object> record) {
        Job job = job(jobId);
        if (job == null) return;
        String shortId = jobId.substring(0, 8);
        try {
            String language = Json.str(record, "language", "fr");
            String difficulty = Json.str(record, "difficulty", "easy");
            String bilingual = (String) record.get("bilingual_language");
            if (bilingual != null && !bilingual.isEmpty() && !wordlistExists(bilingual)) bilingual = null;
            if (!wordlistExists(language)) {
                job.update(d -> {
                    d.put("status", "error");
                    d.put("error_code", "internal_error");
                    d.put("error", "Langue inconnue ou dictionnaire absent.");
                });
                return;
            }
            job.put("step", Json.obj("code", "interactive_building"));
            Object[] loaded = loadInteractiveIndex(language, difficulty, bilingual);
            DualIndex index = (DualIndex) loaded[0];
            @SuppressWarnings("unchecked")
            Set<String> known = (Set<String>) loaded[1];
            Set<String> priority = knownUpper(Json.listOrEmpty(record.get("priority_words")), known);
            Object seed = record.containsKey("seed") ? record.get("seed") : 0;
            Rng rng = seed instanceof Number n ? new Rng(n.longValue()) : new Rng();
            Object generationParams = record.get("generation_params");
            List<Object> gridJson = Json.listOrEmpty(record.get("grid"));
            int rows = gridJson.size();
            int cols = rows > 0 ? Json.asList(gridJson.get(0)).size() : 0;
            List<String> rawChallenge = new ArrayList<>();
            for (Object o : Json.listOrEmpty(record.get("challenge_words"))) if (o != null) rawChallenge.add(o.toString());
            Set<String> challenge = challengeSet(rawChallenge);
            List<Object>[] diag = rows > 0 ? Interactive.fillDiagnostics(gridOf(gridJson), rows, cols, index, challenge)
                    : new List[]{List.of(), List.of(), List.of()};
            INTERACTIVE_SESSIONS.put(jobId, new Session(index, priority, rng, seed, (String) record.get("id")));
            Map<String, Object> meta = new LinkedHashMap<>();
            meta.put("language", language);
            meta.put("bilingual_language", bilingual);
            meta.put("difficulty", difficulty);
            meta.put("width", cols);
            meta.put("height", rows);
            meta.put("theme", record.get("theme"));
            meta.put("has_theme", !priority.isEmpty());
            meta.put("theme_description", null);
            meta.put("generation_params", generationParams);
            meta.put("origin", record.get("origin"));
            meta.put("challenge_words", Json.listOrEmpty(record.get("challenge_words")));
            Map<String, Object> result = new LinkedHashMap<>();
            result.put("width", cols);
            result.put("height", rows);
            result.put("grid", gridJson);
            result.put("placed", null);
            result.put("impossible", false);
            result.put("has_theme", !priority.isEmpty());
            result.put("impossible_cells", diag[0]);
            result.put("low_candidate_cells", diag[1]);
            result.put("deadlock_cells", diag[2]);
            result.put("excluded_cells", List.of());
            result.put("window_cells", List.of());
            result.put("definitions", Json.listOrEmpty(record.get("definitions")));
            Object title = record.get("title");
            result.put("title", Json.truthy(title) ? title : "");
            result.put("theme", record.get("theme"));
            result.put("language", language);
            result.put("bilingual_language", bilingual);
            result.put("difficulty", difficulty);
            result.put("generation_params", generationParams);
            result.put("challenge_words", Json.listOrEmpty(record.get("challenge_words")));
            job.update(d -> {
                d.put("interactive", meta);
                d.put("result", result);
                d.put("step", Json.obj("code", "done"));
                d.put("status", "done");
            });
        } catch (GenerationCancelled e) {
            job.put("status", "cancelled");
        } catch (Throwable e) {
            job.update(d -> {
                d.put("status", "error");
                d.put("error_code", "internal_error");
                d.put("error", "Erreur interne.");
            });
            Log.exception("[" + shortId + "] unhandled error during interactive resume", e);
        }
    }

    static Map<String, Object> libraryRecordToInteractive(Map<String, Object> record) {
        Object grid = Json.truthy(record.get("solution")) ? record.get("solution")
                : Json.truthy(record.get("pattern")) ? record.get("pattern") : new ArrayList<>();
        List<Object> defs = new ArrayList<>();
        for (Object w : Json.listOrEmpty(record.get("words"))) {
            defs.add(Json.obj("row", Json.get(w, "row"), "col", Json.get(w, "col"), "direction", Json.get(w, "direction"),
                    "clue", Json.str(w, "clue", "")));
        }
        Map<String, Object> m = new LinkedHashMap<>();
        m.put("language", Json.str(record, "language", "fr"));
        m.put("bilingual_language", record.get("bilingual"));
        m.put("difficulty", Json.str(record, "difficulty", "easy"));
        m.put("grid", grid);
        m.put("definitions", defs);
        m.put("title", Json.truthy(record.get("title")) ? record.get("title") : "");
        m.put("theme", record.get("theme"));
        m.put("generation_params", record.get("generation_params"));
        m.put("challenge_words", Json.listOrEmpty(record.get("challenge_words")));
        m.put("priority_words", new ArrayList<>());
        m.put("seed", 0);
        m.put("origin", Json.obj("id", record.get("id"), "title", Json.truthy(record.get("title")) ? record.get("title") : "",
                "pseudo", record.get("pseudo"), "created_at", record.get("created_at"),
                // The origin grid's own automatic generation durations, see interactiveSave.
                "generation_duration_seconds", record.get("generation_duration_seconds"),
                "optimization_duration_seconds", record.get("optimization_duration_seconds"),
                "clues_duration_seconds", record.get("clues_duration_seconds")));
        return m;
    }

    static final Pattern VERSION_SUFFIX_RE = Pattern.compile("\\s*\\(V(\\d+)\\)\\s*$");

    static String nextVersionTitle(String title) {
        title = title == null ? "" : Py.strip(title);
        Matcher m = VERSION_SUFFIX_RE.matcher(title);
        String base;
        int n;
        if (m.find()) {
            base = Py.rstrip(title.substring(0, m.start()));
            n = Integer.parseInt(m.group(1)) + 1;
        } else {
            base = title;
            n = 2;
        }
        return Py.strip((base + " (V" + n + ")"));
    }

    static void runRecomputeJob(String jobId, String gridId) {
        Job job = job(jobId);
        if (job == null) return;
        String shortId = jobId.substring(0, 8);
        Generator.Progress progress = (step, data) -> {
            Map<String, Object> newStep = new LinkedHashMap<>();
            newStep.put("code", step);
            newStep.putAll(data);
            job.update(d -> {
                d.put("step", newStep);
                Object ex = data.get("examples");
                if (Json.truthy(ex)) {
                    Map<String, Object> sw = new LinkedHashMap<>(newStep);
                    sw.remove("examples");
                    sw.remove("word_table");
                    Map<String, Object> entry = new LinkedHashMap<>();
                    entry.put("step", sw);
                    entry.put("examples", ex);
                    if (data.containsKey("word_table")) entry.put("word_table", data.get("word_table"));
                    Json.asList(d.get("examples_history")).add(entry);
                }
                Object nc = data.get("new_clue");
                if (Json.truthy(nc)) Json.asList(d.get("clues_progress")).add(nc);
            });
            Log.info("[%s] %s %s", shortId, step, truncate(Json.dumps(data), 600));
        };
        Task task = new Task(jobId, null);
        try {
            Map<String, Object> record = GridStore.getGrid(gridId);
            if (record == null) {
                job.update(d -> {
                    d.put("status", "error");
                    d.put("error_code", "grid_not_found");
                    d.put("error", "grille introuvable dans la bibliothèque");
                });
                Log.warning("[%s] recompute: grid %s not found", shortId, Log.repr(gridId));
                return;
            }
            String language = Json.truthy(record.get("language")) ? (String) record.get("language") : "fr";
            String bilingual = (String) record.get("bilingual_language");
            String difficulty = Json.truthy(record.get("difficulty")) ? (String) record.get("difficulty") : "easy";
            String mode = Json.truthy(record.get("mode")) ? (String) record.get("mode") : "medium";
            Map<String, Object> result = new LinkedHashMap<>(record);
            result.remove("id");
            result.remove("created_at");
            result.remove("bilingual");
            Object t = result.remove("title");
            String originalTitle = t == null ? "" : Py.strip(t.toString());
            String newTitle = nextVersionTitle(originalTitle);
            List<Object> words = Json.listOrEmpty(result.get("words"));
            Log.info("[%s] recompute: grid=%s language=%s bilingual_language=%s difficulty=%s mode=%s words=%d", shortId, gridId,
                    language, bilingual, difficulty, mode, words.size());
            List<Object> wordTable = buildWordVerificationTable(words, language, bilingual);
            Map<String, Object> ex = new LinkedHashMap<>();
            ex.put("example_grid", result.get("solution"));
            ex.put("impossible_cells", List.of());
            ex.put("forced_cells", List.of());
            ex.put("locked_cells", List.of());
            ex.put("theme_cells", List.of());
            ex.put("process_number", result.get("winning_process_number"));
            ex.put("is_best", true);
            progress.on("clues", Json.obj("current", 0, "total", words.size(), "examples", Json.list(ex), "word_table", wordTable));
            CLUES_QUEUE.add(task);
            try {
                List<Clues.Entry> remaining = new ArrayList<>();
                Map<String, Object> wordsByAnswer = new HashMap<>();
                for (Object w : words) {
                    remaining.add(entryOf(w));
                    wordsByAnswer.put(Json.str(w, "answer", ""), w);
                }
                Map<String, String> accumulated = new ConcurrentHashMap<>();
                double cluesComputeS = 0;
                while (true) {
                    waitInQueue(CLUES_QUEUE, task, job, "queued_clues");
                    long start = System.nanoTime();
                    try {
                        final int before = accumulated.size();
                        Map<String, String> nc = CLUE_GENERATOR.generate(remaining, difficulty, language, Clues.DEFAULT_TIMEOUT,
                                (current, total, answer, clue) -> progress.on("clues", Json.obj("current", before + current,
                                        "total", words.size(), "new_clue", clue != null ? Json.obj("answer", answer, "accented",
                                                Json.str(wordsByAnswer.get(answer), "accented", answer), "clue", clue) : null)),
                                job.cancel, makeShouldPause(CLUES_QUEUE, task), null, null);
                        accumulated.putAll(nc);
                        cluesComputeS += (System.nanoTime() - start) / 1e9;
                        break;
                    } catch (GenerationPaused paused) {
                        cluesComputeS += (System.nanoTime() - start) / 1e9;
                        Object[] st = (Object[]) paused.state;
                        @SuppressWarnings("unchecked")
                        Map<String, String> partial = (Map<String, String>) st[0];
                        @SuppressWarnings("unchecked")
                        List<Clues.Entry> rem = (List<Clues.Entry>) st[1];
                        accumulated.putAll(partial);
                        remaining = rem;
                        Log.info("[%s] recompute clues turn paused, back of the queue", shortId);
                        requeue(CLUES_QUEUE, task);
                    }
                }
                result.put("clues_duration_seconds", cluesComputeS);
                for (Object w : words) Json.asMap(w).put("clue", accumulated.getOrDefault(Json.str(w, "answer", ""), ""));
            } finally {
                CLUES_QUEUE.remove(task);
            }
            result.put("title", newTitle);
            progress.on("saving", new LinkedHashMap<>());
            try {
                Path svg = SvgExport.saveGridSvg(result, language, difficulty, mode);
                Log.info("[%s] recompute saved %s", shortId, svg);
                try {
                    Log.info("[%s] recompute saved %s", shortId, SvgExport.saveGridPng(svg));
                } catch (IOException e) {
                    Log.warning("[%s] recompute failed to save grid PNG sample: %s", shortId, e.getMessage());
                }
            } catch (IOException e) {
                Log.warning("[%s] recompute failed to save grid SVG: %s", shortId, e.getMessage());
            }
            try {
                Object cw = result.get("challenge_words");
                String newId = GridStore.saveGridJson(result, language, difficulty, mode, newTitle, bilingual,
                        (String) result.get("pseudo"), (String) result.get("theme"), false, null, result.get("generation_params"),
                        cw instanceof List<?> l && !l.isEmpty() ? l : null);
                Log.info("[%s] recompute saved to library: %s", shortId, newId);
                result.put("id", newId);
            } catch (IOException e) {
                Log.warning("[%s] recompute failed to save grid to library: %s", shortId, e.getMessage());
            }
            progress.on("done", new LinkedHashMap<>());
            job.update(d -> {
                d.put("status", "done");
                d.put("result", result);
            });
            Log.info("[%s] recompute done", shortId);
        } catch (GenerationCancelled e) {
            job.put("status", "cancelled");
            Log.info("[%s] recompute cancelled by user", shortId);
        } catch (Clues.ClueGenerationError e) {
            job.update(d -> {
                d.put("status", "error");
                d.put("error_code", "clue_generation_failed");
                d.put("error", e.getMessage());
            });
            Log.warning("[%s] recompute clue generation failed: %s", shortId, e.getMessage());
        } catch (Throwable e) {
            job.update(d -> {
                d.put("status", "error");
                d.put("error_code", "internal_error");
                d.put("error", "Erreur interne.");
            });
            Log.exception("[" + shortId + "] unhandled error during recompute", e);
        }
    }

    // ================================================================== library

    static Map<String, Object> libraryPage(String preferredLanguage, int page, String seenFilter, Collection<String> seenIds,
                                           String languageFilter, String difficultyFilter, String pseudo) {
        if (!List.of("all", "unseen", "seen", "mine").contains(seenFilter)) seenFilter = "all";
        boolean onlyBilingual = "bilingual".equals(languageFilter);
        String onlyLanguage = WORDLISTS.containsKey(languageFilter) ? languageFilter : null;
        String onlyDifficulty = List.of("easy", "medium", "hard").contains(difficultyFilter) ? difficultyFilter : null;
        Set<String> seen = new HashSet<>(seenIds == null ? List.of() : seenIds);
        String myPseudo = pseudo == null ? "" : Py.strip(pseudo);
        List<Map<String, Object>> rows = new ArrayList<>();
        for (Map<String, Object> g : GridStore.listGrids(preferredLanguage)) {
            if (onlyDifficulty != null && !onlyDifficulty.equals(g.get("difficulty"))) continue;
            if (onlyBilingual) {
                if (!Json.truthy(g.get("bilingual"))) continue;
            } else if (onlyLanguage != null) {
                if (!onlyLanguage.equals(g.get("language")) || Json.truthy(g.get("bilingual"))) continue;
            }
            boolean isSeen = seen.contains(String.valueOf(g.get("id")));
            if (seenFilter.equals("unseen") && isSeen) continue;
            if (seenFilter.equals("seen") && !isSeen) continue;
            if (seenFilter.equals("mine") && (myPseudo.isEmpty() || !Py.strip(Json.str(g, "pseudo", "")).equals(myPseudo))) continue;
            Map<String, Object> row = new LinkedHashMap<>(g);
            row.put("seen", isSeen);
            rows.add(row);
        }
        if (onlyLanguage == null) {
            rows.sort((a, b) -> Json.str(b, "created_at", "").compareTo(Json.str(a, "created_at", "")));
        }
        page = Math.max(1, page);
        int start = (page - 1) * LIBRARY_PAGE_SIZE;
        List<Object> slice = new ArrayList<>();
        for (int k = start; k < Math.min(rows.size(), start + LIBRARY_PAGE_SIZE); k++) slice.add(rows.get(k));
        return Json.obj("grids", slice, "total", rows.size(), "page", page, "page_size", LIBRARY_PAGE_SIZE);
    }

    static String randomDictionaryWord(String language) {
        Path p = WORDLISTS.get(language);
        if (p == null) return null;
        String chosen = null;
        int seen = 0;
        try (BufferedReader r = Files.newBufferedReader(p, StandardCharsets.UTF_8)) {
            String line;
            while ((line = r.readLine()) != null) {
                if (line.isEmpty() || line.startsWith("#")) continue;
                String[] cols = line.split("\t", -1);
                if (cols.length < 2) continue;
                String word = cols[1];
                if (word.length() < RANDOM_THEME_WORD_MIN_LEN || word.length() > RANDOM_THEME_WORD_MAX_LEN) continue;
                seen++;
                if (ThreadLocalRandom.current().nextInt(seen) == 0) chosen = word;
            }
        } catch (IOException e) {
            return null;
        }
        return chosen;
    }

    static Map<String, Object> qdrantAdmin() {
        QdrantStore store = Themes.QDRANT;
        Map<String, Object> out = new LinkedHashMap<>();
        out.put("base_url", store.baseUrl);
        out.put("collection", store.collection);
        out.put("dashboard_url", store.baseUrl + "/dashboard");
        if (!store.ping()) {
            out.put("reachable", false);
            return out;
        }
        out.put("reachable", true);
        if (!store.collectionExists()) {
            out.put("exists", false);
            return out;
        }
        out.put("exists", true);
        Map<String, Object> info = store.collectionInfo();
        Object vec = Json.get(Json.get(Json.get(info, "config"), "params"), "vectors");
        out.put("vector", Json.obj("size", Json.get(vec, "size"), "distance", Json.get(vec, "distance"), "on_disk", Json.get(vec, "on_disk")));
        out.put("status", info.get("status"));
        out.put("optimizer_status", info.get("optimizer_status"));
        out.put("points_count", info.get("points_count"));
        out.put("indexed_vectors_count", info.get("indexed_vectors_count"));
        out.put("segments_count", info.get("segments_count"));
        Map<String, Object> langSchema = Json.mapOrEmpty(Json.get(info.get("payload_schema"), "lang"));
        out.put("tenant_index", !langSchema.isEmpty() && (Boolean.TRUE.equals(Json.get(langSchema.get("params"), "is_tenant"))
                || "keyword".equals(langSchema.get("data_type"))));
        Map<String, Object> counts = new LinkedHashMap<>();
        for (String code : WORDLISTS.keySet()) {
            try {
                counts.put(code, store.count(code));
            } catch (QdrantStore.QdrantStoreError e) {
                counts.put(code, null);
            }
        }
        out.put("languages", counts);
        return out;
    }

    static List<String> sseGuard = List.of();

    // ================================================================== routes

    static Web routes() {
        Web w = new Web();
        w.get("/api/health", r -> Json.obj("status", "ok"));
        w.post("/api/presence", r -> {
            Body b = new Body(r.json());
            String sid = b.requiredMin1("session_id");
            if (sid.length() > 200) throw Web.validation("body", "session_id", "string_too_long", "String should have at most 200 characters", sid);
            String p = b.str("pseudo", null);
            String pseudo = p == null ? "" : Py.strip(p);
            if (pseudo.length() > MAX_PSEUDO_LENGTH) pseudo = pseudo.substring(0, MAX_PSEUDO_LENGTH);
            Object[] snap = presenceSnapshot(sid, pseudo);
            @SuppressWarnings("unchecked")
            List<String> pseudos = (List<String>) snap[1];
            if ((boolean) snap[2]) writeUsersLog((int) snap[0], pseudos);
            return Json.obj("count", snap[0], "resource_usage", LATEST_RESOURCE_USAGE, "queue_lengths",
                    Json.obj("grid", GRID_QUEUE.size(), "clues", CLUES_QUEUE.size()));
        });
        w.post("/api/pseudo/claim", r -> {
            Body b = new Body(r.json());
            String pseudo = Py.strip(b.requiredMin1("pseudo"));
            String secret = Py.strip(b.requiredMin1("secret"));
            if (pseudo.length() > MAX_PSEUDO_LENGTH) pseudo = pseudo.substring(0, MAX_PSEUDO_LENGTH);
            if (secret.length() > MAX_SECRET_LENGTH) secret = secret.substring(0, MAX_SECRET_LENGTH);
            if (pseudo.isEmpty() || secret.isEmpty()) throw http(400, "pseudo ou mot secret vide");
            if (SecretStore.verifyOrClaim(pseudo, secret)) return Json.obj("ok", true);
            return Json.obj("ok", false, "code", "pseudo_taken");
        });
        for (String[] rs : new String[][]{{"/api/rss", "rss"}, {"/api/scrapp", "scrapp"}}) {
            Path dir = rs[1].equals("rss") ? RSS_DIR : SCRAPP_DIR;
            w.get(rs[0], r -> {
                Path combined = dir.resolve("combined.json");
                if (!Files.exists(combined)) return Json.obj("fetched_at", null, "items", List.of());
                try {
                    return Json.readFile(combined);
                } catch (IOException | IllegalArgumentException e) {
                    Log.exception(rs[1] + ": echec de lecture de " + combined, e);
                    return Json.obj("fetched_at", null, "items", List.of());
                }
            });
        }
        w.get("/api/system_info", r -> {
            int layers;
            try {
                String v = Py.strip(Env.get("EMBED_N_GPU_LAYERS", "0"));
                layers = Integer.parseInt(v.isEmpty() ? "0" : v);
            } catch (NumberFormatException e) {
                layers = 0;
            }
            Map<String, Object> info = SystemInfo.getSystemInfo(CLUE_GENERATOR.model,
                    INTERACTIVE_CLUE_GENERATOR != CLUE_GENERATOR ? INTERACTIVE_CLUE_GENERATOR.model : null,
                    Themes.EMBEDDER.model, layers > 0);
            info.put("experimental_notice", EXPERIMENTAL_NOTICE);
            return info;
        });
        w.get("/api/library", r -> libraryPage(r.q("preferred_language", "fr"), r.qInt("page", 1), "all", List.of(), "all", "all", null));
        w.post("/api/library", r -> {
            Body b = new Body(r.json());
            List<String> seenIds = b.strList("seen_ids");
            if (seenIds.size() > 100000) throw Web.validation("body", "seen_ids", "too_long", "List should have at most 100000 items", null);
            return libraryPage(b.str("preferred_language", "fr"), b.integer("page", 1, null, null), b.str("seen_filter", "all"),
                    seenIds, b.str("language_filter", "all"), b.str("difficulty_filter", "all"), b.str("pseudo", null));
        });
        w.get("/api/library/{grid_id}", r -> {
            String gridId = r.pathParams.get("grid_id");
            Map<String, Object> record = GridStore.getGrid(gridId);
            if (record == null) throw http(404, "grille introuvable dans la bibliothèque");
            String pseudo = Py.strip(r.q("pseudo", ""));
            if (!pseudo.isEmpty()) {
                Map<String, Object> saved = GridStore.getGridGame(gridId, pseudo);
                if (saved != null) {
                    record = new LinkedHashMap<>(record);
                    record.put("saved_game", Json.obj("user_letters", saved.get("user_letters"), "elapsed_seconds",
                            saved.getOrDefault("elapsed_seconds", 0)));
                }
            }
            return record;
        });
        w.get("/api/library/{grid_id}/pdf", r -> {
            Map<String, Object> record = GridStore.getGrid(r.pathParams.get("grid_id"));
            if (record == null) throw http(404, "grille introuvable dans la bibliothèque");
            String title = Json.str(record, "title", "");
            title = title == null ? "" : Py.strip(title);
            byte[] pdf;
            try {
                String svg = SvgExport.renderPuzzleSvg(record, Json.str(record, "language", "fr"), title, (String) record.get("difficulty"));
                pdf = SvgExport.svgToPdfBytes(svg);
            } catch (IOException e) {
                throw http(503, e.getMessage());
            }
            String slug = title.isEmpty() ? "grille" : GridStore.slugifyTitle(title);
            return new Web.Raw(200, "application/pdf", pdf, Map.of("Content-Disposition", "attachment; filename=\"" + slug + ".pdf\""));
        });
        w.post("/api/game/save", r -> {
            Body b = new Body(r.json());
            String gridId = b.required("grid_id");
            String pseudo = b.required("pseudo");
            List<Object> letters = b.grid("user_letters");
            int elapsed = b.integer("elapsed_seconds", 0, 0, null);
            if (!GridStore.saveGridGame(gridId, pseudo, letters, elapsed)) throw http(400, "identifiant de grille ou pseudo invalide");
            return Json.obj("ok", true);
        });
        w.get("/api/dictionary", r -> {
            String q = r.qRequired("q");
            String lang = r.q("lang", "fr");
            requireLang(lang);
            return DictionaryLookup.search(q, lang);
        });
        w.get("/api/dictionary/define", r -> {
            String q = r.qRequired("q");
            String lang = r.q("lang", "fr");
            requireLang(lang);
            String text = Py.strip(q);
            if (text.isEmpty()) throw http(400, "expression vide");
            String theme = Py.strip(r.q("theme", ""));
            try {
                List<String> defs = INTERACTIVE_CLUE_GENERATOR.generateDefinitions(text, lang, DEFINE_DIFFICULTY, DEFINE_COUNT, 90.0,
                        theme.isEmpty() ? null : theme);
                return Json.obj("query", text, "lang", lang, "definitions", defs);
            } catch (Clues.ClueGenerationError e) {
                Log.warning("dictionary_define unavailable: %s", e.getMessage());
                throw http(503, Json.obj("code", "define_unavailable", "message", e.getMessage()));
            }
        });
        w.get("/api/paraphrase", r -> {
            String q = r.qRequired("q");
            String lang = r.q("lang", "fr");
            requireLang(lang);
            String text = Py.strip(q);
            if (text.isEmpty()) throw http(400, "texte vide");
            try {
                return Json.obj("query", text, "lang", lang, "paraphrases",
                        INTERACTIVE_CLUE_GENERATOR.generateParaphrases(text, lang, PARAPHRASE_COUNT, PARAPHRASE_TIMEOUT_S));
            } catch (Clues.ClueGenerationError e) {
                Log.warning("paraphrase unavailable: %s", e.getMessage());
                throw http(503, Json.obj("code", "paraphrase_unavailable", "message", e.getMessage()));
            }
        });
        w.get("/api/correct", r -> {
            String q = r.qRequired("q");
            String lang = r.q("lang", "fr");
            requireLang(lang);
            String text = Py.strip(q);
            if (text.isEmpty()) throw http(400, "texte vide");
            try {
                return Json.obj("query", text, "lang", lang, "corrected",
                        INTERACTIVE_CLUE_GENERATOR.correctText(text, lang, CORRECT_TIMEOUT_S));
            } catch (Clues.ClueGenerationError e) {
                Log.warning("correct unavailable: %s", e.getMessage());
                throw http(503, Json.obj("code", "correct_unavailable", "message", e.getMessage()));
            }
        });
        w.get("/api/theme/random", r -> {
            String lang = r.q("lang", "fr");
            requireLang(lang);
            String hint = randomDictionaryWord(lang);
            String theme = INTERACTIVE_CLUE_GENERATOR.generateRandomTheme(lang, hint, Clues.DEFAULT_TIMEOUT, null, 0.95);
            return Json.obj("language", lang, "hint_word", hint, "theme", theme);
        });
        w.get("/api/similar_words", r -> {
            String q = r.qRequired("q");
            String lang = r.q("lang", "fr");
            requireLang(lang);
            String query = Py.strip(q);
            if (query.isEmpty()) throw http(400, "expression vide");
            double minScore = Math.max(0.0, Math.min(1.0, r.qDouble("min_score", Themes.THEME_MIN_SCORE)));
            Object[] res;
            try {
                res = Themes.similarWordsImpl(INTERACTIVE_CLUE_GENERATOR, query, lang, minScore);
            } catch (RuntimeException e) {
                if (!Themes.isStoreError(e)) throw e;
                Log.warning("similar_words unavailable: %s", e.getMessage());
                throw http(503, Json.obj("code", "similar_unavailable", "message", e.getMessage()));
            }
            @SuppressWarnings("unchecked")
            List<Themes.Scored> scored = (List<Themes.Scored>) res[1];
            List<Object> words = new ArrayList<>();
            for (Themes.Scored s : scored) words.add(Json.obj("word", s.word(), "score", s.score()));
            return Json.obj("query", query, "lang", lang, "keywords", res[0], "words", words);
        });
        w.get("/api/synonyms", r -> {
            String q = r.qRequired("q");
            String lang = r.q("lang", "fr");
            requireLang(lang);
            String query = Py.strip(q);
            if (query.isEmpty()) throw http(400, "expression vide");
            double minScore = Math.max(0.0, Math.min(1.0, r.qDouble("min_score", Themes.THEME_MIN_SCORE)));
            List<Themes.Scored> scored;
            try {
                scored = Themes.compiledSimilarWords(List.of(query), lang, minScore);
            } catch (RuntimeException e) {
                if (!Themes.isStoreError(e)) throw e;
                Log.warning("synonyms unavailable: %s", e.getMessage());
                throw http(503, Json.obj("code", "similar_unavailable", "message", e.getMessage()));
            }
            List<Object> words = new ArrayList<>();
            for (Themes.Scored s : scored) words.add(Json.obj("word", s.word(), "score", s.score()));
            return Json.obj("query", query, "lang", lang, "words", words);
        });
        w.get("/api/qdrant/admin", r -> qdrantAdmin());
        w.post("/api/qdrant/admin/recreate", r -> {
            try {
                Map<String, Object> info = Themes.QDRANT.ensureCollection(null, true);
                return Json.obj("ok", true, "points_count", info.getOrDefault("points_count", 0));
            } catch (RuntimeException e) {
                if (!Themes.isStoreError(e)) throw e;
                Log.warning("qdrant_admin recreate failed: %s", e.getMessage());
                throw http(503, Json.obj("code", "qdrant_unavailable", "message", e.getMessage()));
            }
        });
        w.post("/api/qdrant/admin/delete-tenant", r -> {
            String lang = new Body(r.json()).required("lang");
            requireLang(lang);
            try {
                Themes.QDRANT.deleteLang(lang);
                long remaining = Themes.QDRANT.count(lang);
                return Json.obj("ok", true, "lang", lang, "remaining", remaining);
            } catch (RuntimeException e) {
                if (!Themes.isStoreError(e)) throw e;
                Log.warning("qdrant_admin delete-tenant failed: %s", e.getMessage());
                throw http(503, Json.obj("code", "qdrant_unavailable", "message", e.getMessage()));
            }
        });
        w.post("/api/chat", r -> {
            Body b = new Body(r.json());
            String message = b.required("message");
            List<Object> history = new ArrayList<>();
            for (Object m : b.list("history", false)) {
                Body mb = new Body(Json.mapOrEmpty(m));
                history.add(Json.obj("role", mb.required("role"), "content", mb.required("content")));
            }
            String language = b.str("language", "fr");
            Map<String, Object> ui = b.dict("ui_context");
            String sessionId = b.str("session_id", null);
            return new Web.Stream("text/event-stream; charset=utf-8", out -> {
                StringBuilder full = new StringBuilder();
                long start = System.nanoTime();
                Double[] firstToken = {null};
                List<Object> captured = new ArrayList<>();
                try {
                    INTERACTIVE_CHATBOT.replyStream(history, message, language, ui == null ? new LinkedHashMap<>() : ui,
                            ChatBot.DEFAULT_TIMEOUT, CHATBOT_DEBUG ? captured::addAll : null, chunk -> {
                                if (firstToken[0] == null) firstToken[0] = (System.nanoTime() - start) / 1e9;
                                full.append(chunk);
                                writeSse(out, "data: " + Json.dumpsAscii(Json.obj("delta", chunk)) + "\n\n");
                            });
                    double total = (System.nanoTime() - start) / 1e9;
                    writeSse(out, "data: [DONE]\n\n");
                    appendChatLog(sessionId, language, message, full.toString(), firstToken[0], total, captured.isEmpty() ? null : captured);
                } catch (ChatBot.ChatError e) {
                    double total = (System.nanoTime() - start) / 1e9;
                    writeSse(out, "data: " + Json.dumpsAscii(Json.obj("error", e.getMessage())) + "\n\n");
                    String sofar = full.toString();
                    appendChatLog(sessionId, language, message, !sofar.isEmpty()
                                    ? sofar + "\n\n*(échec en cours de réponse : " + e.getMessage() + ")*"
                                    : "*(échec : " + e.getMessage() + ")*", firstToken[0], total,
                            captured.isEmpty() ? null : captured);
                }
            });
        });
        w.post("/api/generate", 202, r -> {
            GenReq req = GenReq.parse(r.json());
            validateGenerateRequest(req);
            String id = newJob();
            runInBackground("generate-" + id.substring(0, 8), () -> runGenerateJob(id, req, new GenJobArgs()));
            return Json.obj("job_id", id);
        });
        w.get("/api/generate/status/{job_id}", r -> {
            Job job = job(r.pathParams.get("job_id"));
            if (job == null) throw http(404, "job inconnu (expiré ou jamais existé)");
            return new Web.Raw(200, "application/json", job.statusJson(), null);
        });
        w.get("/api/generate/phase/{job_id}", r -> {
            String id = r.pathParams.get("job_id");
            Job job = job(id);
            if (job == null) throw http(404, "job inconnu (expiré ou jamais existé)");
            Map<String, Object> step = job.step();
            String status = (String) job.get("status");
            String code = (String) step.get("code");
            String phase;
            if (List.of("done", "error", "cancelled").contains(status)) phase = status;
            else if ("queued_grid".equals(code)) phase = "grid_queue";
            else if ("queued_clues".equals(code)) phase = "clues_queue";
            else if ("clues".equals(code) || "saving".equals(code)) phase = "clues_generation";
            else phase = "grid_generation";
            Map<String, Object> out = Json.obj("job_id", id, "phase", phase, "finished",
                    List.of("done", "error", "cancelled").contains(phase), "status", status, "step_code", code);
            if ("queued_grid".equals(code) || "queued_clues".equals(code)) {
                out.put("queue_position", step.get("position"));
                out.put("queue_length", step.get("queue_length"));
            }
            if ("clues".equals(code)) {
                out.put("clues_done", step.get("current"));
                out.put("clues_total", step.get("total"));
            }
            Object ec = job.get("error_code");
            if (Json.truthy(ec)) out.put("error_code", ec);
            if (phase.equals("error") && Json.truthy(job.get("error"))) out.put("error", job.get("error"));
            return out;
        });
        w.post("/api/generate/cancel/{job_id}", r -> {
            Job job = job(r.pathParams.get("job_id"));
            if (job == null) throw http(404, "job inconnu (expiré ou jamais existé)");
            job.cancel.set(true);
            return Json.obj("status", "cancelling");
        });
        w.post("/api/generate/continue/{job_id}", 202, r -> {
            Job job = job(r.pathParams.get("job_id"));
            if (job == null) throw http(404, "job inconnu (expiré ou jamais existé)");
            Object resume = job.copyOf("resume_state");
            Object request = job.copyOf("request");
            if (resume == null || request == null) throw http(400, "aucun état de reprise disponible pour ce job");
            GenReq req = GenReq.parse(Json.asMap(request));
            validateGenerateRequest(req);
            String id = newJob();
            GenJobArgs a = new GenJobArgs();
            a.resumeState = Json.asMap(resume);
            runInBackground("generate-" + id.substring(0, 8), () -> runGenerateJob(id, req, a));
            return Json.obj("job_id", id);
        });
        w.post("/api/recompute", 202, r -> {
            String gridId = new Body(r.json()).required("grid_id");
            String id = newJob();
            runInBackground("recompute-" + id.substring(0, 8), () -> runRecomputeJob(id, gridId));
            return Json.obj("job_id", id);
        });
        interactiveRoutes(w);
        return w;
    }

    static void writeSse(OutputStream out, String text) {
        try {
            out.write(text.getBytes(StandardCharsets.UTF_8));
            out.flush();
        } catch (IOException e) {
            throw new java.io.UncheckedIOException(e);
        }
    }

    static void interactiveRoutes(Web w) {
        w.post("/api/interactive/start", 202, r -> {
            GenReq req = GenReq.parse(r.json());
            if (!wordlistExists(req.language)) throw http(400, "langue inconnue ou dictionnaire absent");
            if (req.bilingualLanguage != null && !req.bilingualLanguage.equals(req.language) && !wordlistExists(req.bilingualLanguage)) {
                throw http(400, "langue bilingue inconnue ou dictionnaire absent");
            }
            if (!Words.DIFFICULTY_PRESETS.containsKey(req.difficulty)) throw http(400, "difficulté inconnue");
            String id = newJob();
            runInBackground("interactive-" + id.substring(0, 8), () -> runInteractiveJob(id, req));
            return Json.obj("job_id", id);
        });
        w.post("/api/interactive/step", r -> {
            Body b = new Body(r.json());
            String jobId = b.required("job_id");
            List<Object> gridJson = b.grid("grid");
            Set<String> cw = challengeSet(b.strList("challenge_words"));
            Session s = session(jobId);
            int[] d = dims(gridJson);
            Map<String, Object> placed;
            synchronized (s) {
                placed = Interactive.placeWord(gridOf(gridJson), d[0], d[1], s.index, s.rng, s.pw(), cw);
            }
            boolean impossible = Boolean.TRUE.equals(placed.get("impossible"));
            Map<String, Object> out = new LinkedHashMap<>();
            out.put("width", d[1]);
            out.put("height", d[0]);
            out.put("grid", impossible ? gridJson : placed.get("grid"));
            out.put("placed", impossible ? null : placed.get("placed"));
            out.put("impossible", impossible);
            out.put("impossible_cells", placed.getOrDefault("impossible_cells", List.of()));
            out.put("low_candidate_cells", placed.getOrDefault("low_candidate_cells", List.of()));
            out.put("deadlock_cells", placed.getOrDefault("deadlock_cells", List.of()));
            out.put("excluded_cells", placed.getOrDefault("excluded_cells", List.of()));
            out.put("window_cells", placed.getOrDefault("window_cells", List.of()));
            return out;
        });
        w.post("/api/interactive/clean", r -> {
            Body b = new Body(r.json());
            String jobId = b.required("job_id");
            List<Object> gridJson = b.grid("grid");
            boolean deep = b.bool("deep", false);
            Set<String> cw = challengeSet(b.strList("challenge_words"));
            Session s = session(jobId);
            int[] d = dims(gridJson);
            Map<String, Object> result;
            Object grid;
            int removedBlack = 0;
            synchronized (s) {
                result = Interactive.cleanImpossibleZones(gridOf(gridJson), d[0], d[1], s.index, s.rng, cw);
                grid = result.get("grid");
                if (deep) {
                    Map<String, Object> br = Interactive.minimizeBlackCells(gridOf(Json.asList(grid)), d[0], d[1], s.index, s.rng, cw);
                    if (Boolean.TRUE.equals(br.get("changed"))) {
                        grid = br.get("grid");
                        removedBlack = (int) br.get("removed_count");
                    }
                }
            }
            List<Object>[] diag = Interactive.fillDiagnostics(gridOf(Json.asList(grid)), d[0], d[1], s.index, cw);
            Map<String, Object> out = new LinkedHashMap<>();
            out.put("width", d[1]);
            out.put("height", d[0]);
            out.put("grid", grid);
            out.put("changed", Boolean.TRUE.equals(result.get("changed")) || removedBlack > 0);
            out.put("cleared_count", result.get("cleared_count"));
            out.put("removed_black_count", removedBlack);
            out.put("impossible_cells", diag[0]);
            out.put("low_candidate_cells", diag[1]);
            out.put("deadlock_cells", diag[2]);
            return out;
        });
        w.post("/api/interactive/candidates", r -> {
            Body b = new Body(r.json());
            String jobId = b.required("job_id");
            List<Object> gridJson = b.grid("grid");
            List<int[]> cellsRaw = b.cells("cells", true);
            Set<String> cw = challengeSet(b.strList("challenge_words"));
            Session s = session(jobId);
            int[] d = dims(gridJson);
            if (cellsRaw.size() < 2) throw http(400, "emplacement invalide");
            List<Object>[] res = Interactive.slotCandidates(gridOf(gridJson), d[0], d[1], s.index, cellsOf(cellsRaw), s.pw(), cw);
            return Json.obj("theme_words", res[0], "other_words", res[1]);
        });
        w.post("/api/interactive/crossing", r -> {
            Body b = new Body(r.json());
            String jobId = b.required("job_id");
            List<Object> gridJson = b.grid("grid");
            List<Object> cellRaw = b.list("cell", true);
            Set<String> cw = challengeSet(b.strList("challenge_words"));
            Session s = session(jobId);
            int[] d = dims(gridJson);
            if (cellRaw.size() != 2) throw http(400, "case invalide");
            int cr = ((Number) cellRaw.get(0)).intValue(), cc = ((Number) cellRaw.get(1)).intValue();
            if (!(cr >= 0 && cr < d[0] && cc >= 0 && cc < d[1])) throw http(400, "case hors grille");
            Object[] res = Interactive.crossingWords(gridOf(gridJson), d[0], d[1], s.index, Cells.of(cr, cc), cw);
            return Json.obj("across_start", res[0], "down_start", res[1], "letters", res[2]);
        });
        w.post("/api/interactive/boundary", r -> {
            Body b = new Body(r.json());
            String jobId = b.required("job_id");
            List<Object> gridJson = b.grid("grid");
            List<int[]> cellsRaw = b.cells("cells", true);
            String side = b.required("side");
            Set<String> cw = challengeSet(b.strList("challenge_words"));
            Session s = session(jobId);
            int[] d = dims(gridJson);
            if (cellsRaw.size() < 2) throw http(400, "emplacement invalide");
            if (!side.equals("start") && !side.equals("end")) throw http(400, "côté invalide");
            List<Object>[] res = Interactive.boundaryCandidates(gridOf(gridJson), d[0], d[1], s.index, cellsOf(cellsRaw), side, s.pw(), cw);
            return Json.obj("theme_words", res[0], "other_words", res[1]);
        });
        w.post("/api/interactive/impossible", r -> {
            Body b = new Body(r.json());
            String jobId = b.required("job_id");
            List<Object> gridJson = b.grid("grid");
            Set<String> cw = challengeSet(b.strList("challenge_words"));
            Session s = session(jobId);
            int[] d = dims(gridJson);
            List<Object>[] diag = Interactive.fillDiagnostics(gridOf(gridJson), d[0], d[1], s.index, cw);
            return Json.obj("impossible_cells", diag[0], "low_candidate_cells", diag[1], "deadlock_cells", diag[2]);
        });
        w.post("/api/interactive/stats", r -> {
            Body b = new Body(r.json());
            String jobId = b.required("job_id");
            List<Object> gridJson = b.grid("grid");
            Session s = session(jobId);
            int[] d = dims(gridJson);
            return Json.obj("letters", Interactive.letterStats(gridOf(gridJson), d[0], d[1], s.index));
        });
        w.post("/api/interactive/verify", r -> {
            Body b = new Body(r.json());
            String jobId = b.required("job_id");
            List<Object> wordsRaw = b.list("words", true);
            Set<String> cw = challengeSet(b.strList("challenge_words"));
            Session s = session(jobId);
            List<String> invalid = new ArrayList<>();
            Set<String> seen = new HashSet<>();
            for (Object o : wordsRaw) {
                Body wb = new Body(Json.mapOrEmpty(o));
                String answer = wb.required("answer");
                String direction = wb.str("direction", "across");
                if (!seen.add(direction + "\u0000" + answer)) continue;
                if (cw.contains(answer)) continue;
                LenIndex li = s.index.forDirection(direction).get(answer.length());
                if (li == null || !li.wordSet.contains(answer)) invalid.add(answer);
            }
            return Json.obj("invalid_words", invalid);
        });
        w.post("/api/interactive/title", r -> {
            Body b = new Body(r.json());
            String jobId = b.required("job_id");
            List<Object> wordsRaw = b.list("words", true);
            String language = b.str("language", "fr");
            String theme = b.str("theme", null);
            List<Clues.Entry> entries = new ArrayList<>();
            for (Object o : wordsRaw) {
                if (!Json.truthy(Json.get(o, "answer"))) continue;
                String ans = String.valueOf(Json.get(o, "answer"));
                Object acc = Json.get(o, "accented");
                entries.add(new Clues.Entry(ans, Json.truthy(acc) ? acc.toString() : ans, List.of(ans), language));
            }
            Job job = job(jobId);
            Map<String, Object> meta = job == null ? Map.of() : Json.mapOrEmpty(job.copyOf("interactive"));
            String td = theme == null ? "" : Py.strip(theme);
            if (td.isEmpty()) {
                Object m1 = meta.get("theme_description"), m2 = meta.get("theme");
                td = Json.truthy(m1) ? m1.toString() : Json.truthy(m2) ? m2.toString() : null;
            }
            List<String> titles;
            try {
                titles = INTERACTIVE_CLUE_GENERATOR.generateTitles(entries, language, Clues.TITLE_PROPOSALS_COUNT,
                        Clues.DEFAULT_TIMEOUT, null, td);
            } catch (RuntimeException e) {
                Log.exception("interactive title generation failed", e);
                titles = new ArrayList<>();
            }
            return Json.obj("titles", titles);
        });
        w.post("/api/interactive/save", r -> {
            Body b = new Body(r.json());
            String jobId = b.required("job_id");
            List<Object> gridJson = b.grid("grid");
            List<Object> definitions = b.list("definitions", true);
            String title = b.str("title", "");
            String language = b.str("language", "fr");
            String bilingual = b.str("bilingual_language", null);
            String difficulty = b.str("difficulty", "easy");
            String theme = b.str("theme", null);
            String pseudoRaw = b.str("pseudo", null);
            List<String> challengeWords = b.strList("challenge_words");
            Map<String, Object> diagnostics = b.dict("diagnostics");
            int[] d = dims(gridJson);
            int rows = d[0], cols = d[1];
            char[][] bw = new char[rows][cols];
            for (int rr = 0; rr < rows; rr++) {
                List<Object> row = Json.asList(gridJson.get(rr));
                for (int c = 0; c < cols; c++) bw[rr][c] = "#".equals(row.get(c)) ? '#' : '.';
            }
            List<int[]> slots = Grids.extractSlots(bw, rows, cols);
            String[] assignment = new String[slots.size()];
            for (int i = 0; i < slots.size(); i++) {
                StringBuilder sb = new StringBuilder();
                for (int cell : slots.get(i)) sb.append(Json.asList(gridJson.get(Cells.r(cell))).get(Cells.c(cell)));
                assignment[i] = sb.toString();
            }
            List<Map<String, Object>> words = Fill.buildWordEntries(bw, rows, cols, slots, assignment);
            Map<String, String> clueByKey = new HashMap<>();
            for (Object def : definitions) {
                Object clue = Json.get(def, "clue");
                clueByKey.put(Json.get(def, "row") + "|" + Json.get(def, "col") + "|" + Json.get(def, "direction"),
                        Json.truthy(clue) ? clue.toString() : "");
            }
            boolean isBilingual = bilingual != null && !bilingual.isEmpty() && !bilingual.equals(language);
            for (Map<String, Object> wd : words) {
                wd.put("clue", clueByKey.getOrDefault(wd.get("row") + "|" + wd.get("col") + "|" + wd.get("direction"), ""));
                wd.putIfAbsent("accented", wd.get("answer"));
                wd.putIfAbsent("canonical", wd.get("answer"));
                wd.put("language", isBilingual && "down".equals(wd.get("direction")) ? bilingual : language);
            }
            char[][] solutionChars = new char[rows][cols];
            List<Object> solution = new ArrayList<>();
            for (int rr = 0; rr < rows; rr++) {
                List<Object> row = new ArrayList<>();
                for (int c = 0; c < cols; c++) row.add("#");
                solution.add(row);
            }
            for (int i = 0; i < slots.size(); i++) {
                int[] cells = slots.get(i);
                for (int cell : cells) {
                    Json.asList(solution.get(Cells.r(cell))).set(Cells.c(cell), Json.asList(gridJson.get(Cells.r(cell))).get(Cells.c(cell)));
                }
            }
            int nBlack = Grids.countBlack(bw);
            String themeClean = theme == null || Py.strip(theme).isEmpty() ? null : Py.strip(theme);
            Job job = job(jobId);
            Map<String, Object> meta = job == null ? Map.of() : Json.mapOrEmpty(job.copyOf("interactive"));
            Map<String, Object> origin = Json.mapOrEmpty(meta.get("origin"));
            Map<String, Object> result = new LinkedHashMap<>();
            result.put("width", cols);
            result.put("height", rows);
            result.put("pattern", Grids.toJson(bw));
            result.put("solution", solution);
            result.put("words", new ArrayList<Object>(words));
            result.put("word_count", words.size());
            result.put("black_count", nBlack);
            result.put("black_ratio", rows > 0 && cols > 0 ? (double) nBlack / (rows * cols) : 0);
            result.put("language", language);
            result.put("bilingual_language", isBilingual ? bilingual : null);
            // A grid edited from an automatically generated one keeps that
            // grid's own durations (carried in `origin`); one built by hand
            // from scratch has none (0).
            for (String k : List.of("generation_duration_seconds", "optimization_duration_seconds",
                    "clues_duration_seconds")) {
                Object v = origin.get(k);
                result.put(k, Json.truthy(v) ? v : 0);
            }
            result.put("difficulty", difficulty);
            result.put("theme", themeClean);
            result.put("title", title);
            try {
                Path svg = SvgExport.saveGridSvg(result, language, difficulty, "interactive");
                try {
                    SvgExport.saveGridPng(svg);
                } catch (IOException ignored) { }
            } catch (IOException e) {
                Log.warning("interactive save: SVG/PNG export skipped");
            }
            String pseudo = pseudoOf(pseudoRaw);
            String gridId = GridStore.saveGridJson(result, language, difficulty, "interactive", title, isBilingual ? bilingual : null,
                    pseudo, themeClean, true, meta.get("origin"), meta.get("generation_params"),
                    challengeWords.isEmpty() ? null : challengeWords);
            Session s = INTERACTIVE_SESSIONS.get(jobId);
            if (s != null) {
                try {
                    Object mt = meta.get("theme");
                    GridStore.saveGridWork(jobId, gridJson, definitions, title, meta.getOrDefault("language", language),
                            meta.getOrDefault("difficulty", difficulty), Json.truthy(mt) ? mt : themeClean, s.priorityWords,
                            s.seed == null ? 0 : s.seed, pseudo, s.resumedFrom, meta.get("origin"), meta.get("bilingual_language"),
                            meta.get("generation_params"), challengeWords, diagnostics);
                } catch (Exception e) {
                    Log.warning("interactive save: GRID_WORK snapshot skipped: %s", e.getMessage());
                }
            }
            return Json.obj("grid_id", gridId);
        });
        w.post("/api/interactive/save_work", r -> {
            Body b = new Body(r.json());
            String jobId = b.required("job_id");
            List<Object> gridJson = b.grid("grid");
            List<Object> definitions = b.list("definitions", false);
            String title = b.str("title", "");
            String pseudoRaw = b.str("pseudo", null);
            List<String> challengeWords = b.strList("challenge_words");
            Map<String, Object> diagnostics = b.dict("diagnostics");
            Session s = session(jobId);
            Job job = job(jobId);
            Map<String, Object> meta = job == null ? Map.of() : Json.mapOrEmpty(job.copyOf("interactive"));
            String workId = GridStore.saveGridWork(jobId, gridJson, definitions, title, meta.getOrDefault("language", "fr"),
                    meta.getOrDefault("difficulty", "easy"), meta.get("theme"), s.priorityWords, s.seed == null ? 0 : s.seed,
                    pseudoOf(pseudoRaw), s.resumedFrom, meta.get("origin"), meta.get("bilingual_language"),
                    meta.get("generation_params"), challengeWords, diagnostics);
            return Json.obj("work_id", workId);
        });
        w.get("/api/interactive/work", r -> Json.obj("items", GridStore.listGridWork(r.q("pseudo", ""))));
        w.post("/api/interactive/work/delete", r -> Json.obj("deleted", GridStore.deleteGridWork(new Body(r.json()).required("work_id"))));
        w.post("/api/interactive/resume", 202, r -> {
            Map<String, Object> record = GridStore.getGridWork(new Body(r.json()).required("work_id"));
            if (record == null) throw http(404, "création introuvable (supprimée ?)");
            String id = newJob();
            runInBackground("resume-" + id.substring(0, 8), () -> runInteractiveResumeJob(id, record));
            return Json.obj("job_id", id);
        });
        w.post("/api/interactive/from-library", 202, r -> {
            Map<String, Object> record = GridStore.getGrid(new Body(r.json()).required("grid_id"));
            if (record == null) throw http(404, "grille introuvable dans la bibliothèque");
            String id = newJob();
            Map<String, Object> synthetic = libraryRecordToInteractive(record);
            runInBackground("resume-" + id.substring(0, 8), () -> runInteractiveResumeJob(id, synthetic));
            return Json.obj("job_id", id);
        });
        w.post("/api/interactive/from-attempt", 202, r -> {
            Body b = new Body(r.json());
            List<Object> gridJson = b.grid("grid");
            String language = b.str("language", "fr");
            String bilingual = b.str("bilingual_language", null);
            String difficulty = b.str("difficulty", "easy");
            String theme = b.str("theme", null);
            List<String> challengeWords = b.strList("challenge_words");
            if (!wordlistExists(language)) throw http(400, "langue inconnue ou dictionnaire absent");
            if (bilingual != null && !bilingual.equals(language) && !wordlistExists(bilingual)) {
                throw http(400, "langue bilingue inconnue ou dictionnaire absent");
            }
            if (!Words.DIFFICULTY_PRESETS.containsKey(difficulty)) throw http(400, "difficulté inconnue");
            if (gridJson.isEmpty() || Json.asList(gridJson.get(0)).isEmpty()) throw http(400, "grille vide");
            String id = newJob();
            Map<String, Object> synthetic = new LinkedHashMap<>();
            synthetic.put("language", language);
            synthetic.put("bilingual_language", bilingual);
            synthetic.put("difficulty", difficulty);
            synthetic.put("grid", gridJson);
            synthetic.put("definitions", new ArrayList<>());
            synthetic.put("title", "");
            synthetic.put("theme", theme);
            synthetic.put("generation_params", null);
            synthetic.put("challenge_words", challengeWords);
            synthetic.put("priority_words", new ArrayList<>());
            synthetic.put("seed", 0);
            synthetic.put("origin", null);
            runInBackground("resume-" + id.substring(0, 8), () -> runInteractiveResumeJob(id, synthetic));
            return Json.obj("job_id", id);
        });
        w.post("/api/interactive/finish", 202, r -> {
            Body b = new Body(r.json());
            String jobId = b.required("job_id");
            List<Object> gridJson = b.grid("grid");
            List<Object> definitions = b.list("definitions", false);
            String mode = b.str("mode", "medium");
            int bep = b.integer("black_enrichment_percent", 17, 0, 100);
            int flp = b.integer("force_letters_percent", 1, 0, 100);
            String pseudo = b.str("pseudo", null);
            List<int[]> zoneCells = b.has("zone_cells") ? b.cells("zone_cells", false) : null;
            Session s = INTERACTIVE_SESSIONS.get(jobId);
            Job job = job(jobId);
            Object metaObj = job == null ? null : job.copyOf("interactive");
            if (s == null || job == null || metaObj == null) throw http(404, "session interactive inconnue (expirée ?)");
            Map<String, Object> meta = Json.asMap(metaObj);
            int[] d = dims(gridJson);
            int rows = d[0], cols = d[1];
            char[][] seedGrid = new char[rows][cols];
            Map<Integer, Character> locked = new LinkedHashMap<>();
            for (int rr = 0; rr < rows; rr++) {
                List<Object> row = Json.asList(gridJson.get(rr));
                for (int c = 0; c < cols; c++) {
                    String ch = String.valueOf(row.get(c));
                    seedGrid[rr][c] = ch.equals("#") ? '#' : '.';
                    if (!ch.equals("#") && !ch.equals(".") && !ch.isEmpty()) locked.put(Cells.of(rr, c), ch.charAt(0));
                }
            }
            Set<Integer> permanentBlack = null;
            Map<Integer, String> zoneRevert = null;
            Set<Integer> zone = null;
            if (zoneCells != null && !zoneCells.isEmpty()) {
                zone = new HashSet<>();
                for (int[] zc : zoneCells) zone.add(Cells.of(zc[0], zc[1]));
                permanentBlack = new HashSet<>();
                zoneRevert = new LinkedHashMap<>();
                for (int rr = 0; rr < rows; rr++) {
                    List<Object> row = Json.asList(gridJson.get(rr));
                    for (int c = 0; c < cols; c++) {
                        String ch = String.valueOf(row.get(c));
                        int cell = Cells.of(rr, c);
                        if (ch.equals("#") && zone.contains(cell)) permanentBlack.add(cell);
                        if (!zone.contains(cell)) zoneRevert.put(cell, ch);
                    }
                }
            }
            Set<Integer> required = new HashSet<>();
            for (int rr = 0; rr < rows; rr++) {
                List<Object> row = Json.asList(gridJson.get(rr));
                for (int c = 0; c < cols; c++) {
                    if (".".equals(row.get(c)) && (zone == null || zone.contains(Cells.of(rr, c)))) required.add(Cells.of(rr, c));
                }
            }
            Map<String, Object> resumeState = Generator.serializeResumeState(seedGrid, locked, null, null);
            List<int[]> slots = Grids.extractSlots(seedGrid, rows, cols);
            Map<String, int[]> slotByKey = new HashMap<>();
            for (int[] cells : slots) slotByKey.put(Cells.r(cells[0]) + "|" + Cells.c(cells[0]) + "|" + Words.slotDirection(cells), cells);
            Map<String, String> preserved = new LinkedHashMap<>();
            Set<Integer> protectedBlack = new HashSet<>();
            for (Object def : definitions) {
                Object clueO = Json.get(def, "clue");
                String clue = Json.truthy(clueO) ? Py.strip(clueO.toString()) : "";
                if (clue.isEmpty()) continue;
                String direction = String.valueOf(Json.get(def, "direction"));
                int[] cells = slotByKey.get(Json.get(def, "row") + "|" + Json.get(def, "col") + "|" + direction);
                if (cells == null) continue;
                StringBuilder word = new StringBuilder();
                for (int cell : cells) word.append(Json.asList(gridJson.get(Cells.r(cell))).get(Cells.c(cell)));
                if (word.indexOf(".") >= 0) continue;
                preserved.put(word.toString(), clue);
                int r0 = Cells.r(cells[0]), c0 = Cells.c(cells[0]), r1 = Cells.r(cells[cells.length - 1]),
                        c1 = Cells.c(cells[cells.length - 1]);
                int[][] boundary = direction.equals("across") ? new int[][]{{r0, c0 - 1}, {r1, c1 + 1}}
                        : new int[][]{{r0 - 1, c0}, {r1 + 1, c1}};
                for (int[] bc : boundary) {
                    if (bc[0] >= 0 && bc[0] < rows && bc[1] >= 0 && bc[1] < cols && seedGrid[bc[0]][bc[1]] == '#') {
                        protectedBlack.add(Cells.of(bc[0], bc[1]));
                    }
                }
            }
            if (!protectedBlack.isEmpty()) {
                if (permanentBlack == null) permanentBlack = new HashSet<>();
                permanentBlack.addAll(protectedBlack);
            }
            GenReq genreq = new GenReq();
            genreq.language = (String) meta.get("language");
            genreq.bilingualLanguage = (String) meta.get("bilingual_language");
            genreq.width = cols;
            genreq.height = rows;
            if (cols < 5 || cols > 30 || rows < 5 || rows > 30) {
                throw Web.validation("body", "width", "less_than_equal", "Input should be between 5 and 30", cols);
            }
            genreq.difficulty = Json.str(meta, "difficulty", "easy");
            genreq.seed = ThreadLocalRandom.current().nextLong(1L << 31);
            genreq.forceLettersPercent = flp;
            genreq.blackEnrichmentPercent = bep;
            genreq.mode = mode;
            Object mt = meta.get("theme");
            genreq.theme = Json.truthy(mt) ? mt.toString() : null;
            genreq.pseudo = pseudo;
            validateGenerateRequest(genreq);
            String id = newJob();
            GenJobArgs a = new GenJobArgs();
            a.resumeState = resumeState;
            a.hasOverride = true;
            a.overridePriorityWords = new ArrayList<>(s.priorityWords);
            Object tdesc = meta.get("theme_description");
            a.overrideThemeDescription = Json.truthy(tdesc) ? tdesc.toString() : "";
            a.preservedClues = preserved;
            a.permanentLocked = locked;
            a.permanentBlack = permanentBlack;
            a.publish = false;
            a.origin = meta.get("origin");
            a.zoneRevert = zoneRevert;
            a.requiredCells = required;
            runInBackground("finish-" + id.substring(0, 8), () -> runGenerateJob(id, genreq, a));
            return Json.obj("job_id", id);
        });
    }

    // ================================================================== main

    public static void main(String[] args) throws IOException {
        String host = "127.0.0.1";
        int port = Env.getInt("CROSSWORDFALCON_BACKEND_PORT", 3001);
        for (int i = 0; i < args.length; i++) {
            if (args[i].equals("--port")) port = Integer.parseInt(args[++i]);
            else if (args[i].equals("--host")) host = args[++i];
        }
        Web web = routes();
        web.start(host, port);
        Log.info("CrossWordFalcon Java back end listening on http://%s:%d (root %s, %d parallel attempts)", host, port,
                PROJECT_ROOT, Generator.PARALLEL_ATTEMPTS);
        startSchedulers();
    }
}
