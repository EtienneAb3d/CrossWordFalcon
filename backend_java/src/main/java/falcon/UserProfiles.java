package falcon;

import falcon.gen.Words;

import java.io.BufferedReader;
import java.io.BufferedWriter;
import java.io.IOException;
import java.nio.ByteBuffer;
import java.nio.charset.CharacterCodingException;
import java.nio.charset.CodingErrorAction;
import java.nio.charset.StandardCharsets;
import java.nio.file.DirectoryStream;
import java.nio.file.Files;
import java.nio.file.Path;
import java.nio.file.StandardCopyOption;
import java.security.MessageDigest;
import java.security.SecureRandom;
import java.text.Normalizer;
import java.time.LocalDateTime;
import java.time.format.DateTimeFormatter;
import java.util.ArrayList;
import java.util.HashMap;
import java.util.HashSet;
import java.util.HexFormat;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Locale;
import java.util.Map;
import java.util.Set;
import java.util.regex.Matcher;
import java.util.regex.Pattern;

/** Per-user profiles: USER_PROFILES/&lt;pseudo slug&gt;/ (mirrors backend/user_profiles.py) — preferences.json (order,
 * on/off state and colour of the user's dictionaries and of the two Falcon glossaries, the Scrabble dictionary and the
 * others, colours of the fixed glossaries),
 * every uploaded word file as sent (upload_&lt;id&gt;&lt;ext&gt;), its converted form (dict_&lt;id&gt;.tsv,
 * MOT TAB ACCENTUE TAB SCORE) and, per language, the merged lexicon of the enabled dictionaries the grid engine
 * reads (lexicon_&lt;lang&gt;_&lt;hash&gt;.tsv, {@link #lexiconSpec}). */
public final class UserProfiles {
    private UserProfiles() {}

    public static final Path PROFILE_DIR = Env.ROOT.resolve("USER_PROFILES");
    static final String PREFERENCES_FILE = "preferences.json";
    /** The two glossaries of the Falcon lexicon, in their default order. */
    public static final String SCRABBLE_ID = "scrabble", OTHERS_ID = "others";
    public static final List<String> FALCON_IDS = List.of(SCRABBLE_ID, OTHERS_ID);
    /** The former single Falcon-dictionary entry, read as both. */
    static final String LEGACY_FALCON_ID = "falcon";
    /** Largest word file accepted, in bytes. */
    public static final int MAX_UPLOAD_BYTES = 20 * 1024 * 1024;
    /** Longest grid form kept (the largest grid side). */
    static final int MAX_WORD_LENGTH = 50;
    /** Share (%) of the file's non-empty lines that must hold a recognisable entry. */
    static final int MIN_RECOGNISED_PERCENT = 50;
    /** Falcon usage score of an entry of a file with no scores. */
    static final int UNSCORED_USAGE = 5;
    static final List<String> SUPPORTED_LANGUAGES = List.of("fr", "en", "de", "es", "it", "pt");
    /** The two fixed glossaries ahead of the reorderable list, which only carry a display colour. */
    static final List<String> FIXED_IDS = List.of("challenge", "theme");

    private static final Object LOCK = new Object();
    private static final Pattern ID_RE = Pattern.compile("^d[0-9a-f]{12}$");
    private static final Pattern EXT_RE = Pattern.compile("^\\.[A-Za-z0-9]{1,8}$");
    private static final Pattern COLOR_RE = Pattern.compile("^#[0-9a-f]{6}$");
    private static final Pattern SCORED_RE = Pattern.compile(
            "^\\s*(.+?)\\s*[;\\t,:|]\\s*([0-9]+(?:\\.[0-9]+)?)\\s*$", Pattern.UNICODE_CHARACTER_CLASS);
    private static final Pattern SPLIT_RE = Pattern.compile(
            "[;,\\t|/:.!?()\\[\\]{}\"«»…=+*#<>\\\\“”]+");
    private static final Pattern LINE_BREAK = Pattern.compile(
            "\r\n|[\n\r\u000B\u000C\u001C\u001D\u001E\u0085  ]");
    private static final String APOSTROPHES = "'’";
    private static final SecureRandom RANDOM = new SecureRandom();

    /** The uploaded file holds no clearly identifiable words. */
    public static final class WordlistUnrecognised extends Exception {
        WordlistUnrecognised(String message) {
            super(message);
        }
    }

    /** One converted entry: grid form, spellings in order of appearance, Falcon usage score. */
    record Entry(String mot, List<String> spellings, int usage) {}

    static Path profileDir(String pseudo) {
        return PROFILE_DIR.resolve(GridStore.slugifyPseudo(pseudo));
    }

    /** Falcon usage score (0-5) of a word-list score (mirrors _usage_of). */
    static int usageOf(double score) {
        return Math.max(0, Math.min(5, (int) Math.floor(score / 10)));
    }

    /** The entry with every character other than a letter, a space, a hyphen or an apostrophe removed, its spaces
     * collapsed and its separators trimmed off both ends (mirrors _clean_entry). */
    static String cleanEntry(String text) {
        StringBuilder sb = new StringBuilder();
        text.codePoints().forEach(c -> {
            if (Character.isLetter(c) || c == '-' || APOSTROPHES.indexOf(c) >= 0) sb.appendCodePoint(c);
            else if (c <= 0xFFFF && Py.isSpace((char) c)) sb.append(' ');
        });
        String kept = sb.toString().replaceAll(" +", " ");
        int a = 0, b = kept.length();
        String trim = " -" + APOSTROPHES;
        while (a < b && trim.indexOf(kept.charAt(a)) >= 0) a++;
        while (b > a && trim.indexOf(kept.charAt(b - 1)) >= 0) b--;
        return kept.substring(a, b);
    }

    /** A-Z grid form of {@code spelling}, null when one of its letters has no A-Z form (mirrors grid_form). */
    static String gridForm(String spelling) {
        String folded = spelling.replace("œ", "oe").replace("Œ", "OE").replace("æ", "ae").replace("Æ", "AE");
        StringBuilder out = new StringBuilder();
        int[] cps = folded.codePoints().toArray();
        for (int c : cps) {
            if (!Character.isLetter(c)) continue;
            String nfkd = Normalizer.normalize(new String(Character.toChars(c)), Normalizer.Form.NFKD);
            StringBuilder base = new StringBuilder();
            nfkd.codePoints().forEach(d -> {
                if (Character.getType(d) != Character.NON_SPACING_MARK && Character.getType(d) != Character.ENCLOSING_MARK
                        && Character.getType(d) != Character.COMBINING_SPACING_MARK) base.appendCodePoint(d);
            });
            String up = base.toString().toUpperCase(Locale.ROOT);
            if (up.isEmpty()) return null;
            for (int i = 0; i < up.length(); i++) if (up.charAt(i) < 'A' || up.charAt(i) > 'Z') return null;
            out.append(up);
        }
        return out.toString();
    }

    private static String decode(byte[] data) {
        int start = data.length >= 3 && (data[0] & 0xff) == 0xEF && (data[1] & 0xff) == 0xBB && (data[2] & 0xff) == 0xBF
                ? 3 : 0;
        try {
            return StandardCharsets.UTF_8.newDecoder().onMalformedInput(CodingErrorAction.REPORT)
                    .onUnmappableCharacter(CodingErrorAction.REPORT)
                    .decode(ByteBuffer.wrap(data, start, data.length - start)).toString();
        } catch (CharacterCodingException e) {
            return new String(data, StandardCharsets.ISO_8859_1);
        }
    }

    /** The entries of an uploaded word file and whether it carries scores (mirrors parse_wordlist). */
    static Object[] parseWordlist(byte[] data) throws WordlistUnrecognised {
        for (byte b : data) if (b == 0) throw new WordlistUnrecognised("binary file");
        String text = decode(data);
        List<String> lines = new ArrayList<>();
        for (String line : LINE_BREAK.split(text, -1)) if (!Py.strip(line).isEmpty()) lines.add(line);
        if (lines.isEmpty()) throw new WordlistUnrecognised("empty file");
        List<Matcher> matches = new ArrayList<>();
        int scoredLines = 0;
        for (String line : lines) {
            Matcher m = SCORED_RE.matcher(line);
            if (m.matches()) {
                matches.add(m);
                scoredLines++;
            } else {
                matches.add(null);
            }
        }
        boolean scored = (long) scoredLines * 100 >= (long) lines.size() * MIN_RECOGNISED_PERCENT;
        Map<String, Object[]> found = new HashMap<>();
        List<String> order = new ArrayList<>();
        int recognised = 0;
        for (int k = 0; k < lines.size(); k++) {
            Matcher m = matches.get(k);
            if (scored) {
                if (m != null && add(m.group(1), usageOf(Double.parseDouble(m.group(2))), found, order)) recognised++;
                continue;
            }
            boolean hit = false;
            for (String piece : SPLIT_RE.split(lines.get(k), -1)) {
                if (add(piece, UNSCORED_USAGE, found, order)) hit = true;
            }
            if (hit) recognised++;
        }
        if (order.isEmpty() || (long) recognised * 100 < (long) lines.size() * MIN_RECOGNISED_PERCENT) {
            throw new WordlistUnrecognised("no clearly identifiable words");
        }
        List<Entry> entries = new ArrayList<>();
        for (String mot : order) {
            Object[] e = found.get(mot);
            @SuppressWarnings("unchecked")
            List<String> spellings = (List<String>) e[0];
            entries.add(new Entry(mot, spellings, (Integer) e[1]));
        }
        return new Object[]{entries, scored};
    }

    private static boolean add(String raw, int usage, Map<String, Object[]> found, List<String> order) {
        String spelling = cleanEntry(raw);
        String mot = spelling.isEmpty() ? null : gridForm(spelling);
        if (mot == null || mot.length() < 2 || mot.length() > MAX_WORD_LENGTH) return false;
        Object[] entry = found.get(mot);
        if (entry == null) {
            entry = new Object[]{new ArrayList<String>(), usage};
            found.put(mot, entry);
            order.add(mot);
        }
        @SuppressWarnings("unchecked")
        List<String> spellings = (List<String>) entry[0];
        if (!spellings.contains(spelling)) spellings.add(spelling);
        entry[1] = Math.max((Integer) entry[1], usage);
        return true;
    }

    private static Object readJson(Path path) {
        try {
            return Json.readFile(path);
        } catch (IOException | RuntimeException e) {
            return null;
        }
    }

    private static void writeJson(Path path, Object value) throws IOException {
        Path tmp = path.resolveSibling(path.getFileName() + ".tmp");
        Files.writeString(tmp, Json.dumpsPretty(value), StandardCharsets.UTF_8);
        Files.move(tmp, path, StandardCopyOption.REPLACE_EXISTING, StandardCopyOption.ATOMIC_MOVE);
    }

    private static Path dictPath(Path folder, String id) {
        return folder.resolve("dict_" + id + ".tsv");
    }

    static boolean validColor(Object value) {
        return value instanceof String s && COLOR_RE.matcher(s).matches();
    }

    /** The preferences of {@code prefs}, normalized (mirrors _normalized). */
    static Map<String, Object> normalized(Path folder, Object prefs) {
        List<Map<String, Object>> items = new ArrayList<>();
        Set<String> seen = new HashSet<>();
        for (Object o : Json.listOrEmpty(Json.get(prefs instanceof Map ? prefs : null, "glossaries"))) {
            if (!(o instanceof Map<?, ?> item)) continue;
            Object gid = item.get("id");
            if (gid instanceof String g && seen.contains(g)) continue;
            Object enabledRaw = item.containsKey("enabled") ? item.get("enabled") : Boolean.TRUE;
            boolean enabled = Json.truthy(enabledRaw);
            if (LEGACY_FALCON_ID.equals(gid)) {
                for (String fid : FALCON_IDS) {
                    if (seen.contains(fid)) continue;
                    Map<String, Object> e = new LinkedHashMap<>();
                    e.put("id", fid);
                    e.put("enabled", enabled);
                    if (fid.equals(SCRABBLE_ID) && validColor(item.get("color"))) e.put("color", item.get("color"));
                    items.add(e);
                    seen.add(fid);
                }
                continue;
            }
            if (FALCON_IDS.contains(gid)) {
                Map<String, Object> e = new LinkedHashMap<>();
                e.put("id", gid);
                e.put("enabled", enabled);
                if (validColor(item.get("color"))) e.put("color", item.get("color"));
                items.add(e);
            } else if (gid instanceof String g && ID_RE.matcher(g).matches() && Files.exists(dictPath(folder, g))) {
                Map<String, Object> e = new LinkedHashMap<>();
                e.put("id", g);
                e.put("enabled", enabled);
                Object name = item.get("name");
                e.put("name", Json.truthy(name) ? String.valueOf(name) : g);
                Object lang = item.get("language");
                e.put("language", SUPPORTED_LANGUAGES.contains(lang) ? lang : "fr");
                Object entries = item.get("entries");
                e.put("entries", entries instanceof Number n ? n.intValue() : 0);
                e.put("scored", Json.truthy(item.get("scored")));
                Object at = item.get("uploaded_at");
                e.put("uploaded_at", Json.truthy(at) ? String.valueOf(at) : "");
                if (validColor(item.get("color"))) e.put("color", item.get("color"));
                items.add(e);
            } else {
                continue;
            }
            seen.add((String) gid);
        }
        for (String fid : FALCON_IDS) {
            if (seen.contains(fid)) continue;
            Map<String, Object> e = new LinkedHashMap<>();
            e.put("id", fid);
            e.put("enabled", true);
            items.add(e);
        }
        if (items.stream().noneMatch(i -> Boolean.TRUE.equals(i.get("enabled")))) {
            for (Map<String, Object> i : items) if (FALCON_IDS.contains(i.get("id"))) i.put("enabled", true);
        }
        Map<String, Object> colors = new LinkedHashMap<>();
        Object raw = Json.get(prefs instanceof Map ? prefs : null, "colors");
        if (raw instanceof Map<?, ?> m) {
            m.forEach((k, v) -> {
                if (FIXED_IDS.contains(k) && validColor(v)) colors.put((String) k, v);
            });
        }
        Map<String, Object> out = new LinkedHashMap<>();
        out.put("glossaries", items);
        out.put("colors", colors);
        return out;
    }

    /** The user's preferences, defaults when none saved (mirrors load_preferences). */
    public static Map<String, Object> loadPreferences(String pseudo) {
        Path folder = profileDir(pseudo);
        return normalized(folder, readJson(folder.resolve(PREFERENCES_FILE)));
    }

    /** Stores the order, on/off state and colour of the glossaries and the fixed glossaries' colours; null when
     * every item would be disabled (mirrors save_preferences). */
    @SuppressWarnings("unchecked")
    public static Map<String, Object> savePreferences(String pseudo, List<Object> glossaries, Object colors)
            throws IOException {
        synchronized (LOCK) {
            Path folder = profileDir(pseudo);
            Map<String, Object> saved = normalized(folder, readJson(folder.resolve(PREFERENCES_FILE)));
            List<Map<String, Object>> current = (List<Map<String, Object>>) saved.get("glossaries");
            Map<String, Map<String, Object>> byId = new HashMap<>();
            for (Map<String, Object> item : current) byId.put((String) item.get("id"), item);
            List<Map<String, Object>> ordered = new ArrayList<>();
            Set<String> placed = new HashSet<>();
            for (Object o : glossaries == null ? List.of() : glossaries) {
                if (!(o instanceof Map<?, ?> item)) continue;
                Object gid = item.get("id");
                if (!(gid instanceof String g) || !byId.containsKey(g) || placed.contains(g)) continue;
                Map<String, Object> entry = new LinkedHashMap<>(byId.get(g));
                entry.put("enabled", Json.truthy(item.containsKey("enabled") ? item.get("enabled") : Boolean.TRUE));
                entry.remove("color");
                if (validColor(item.get("color"))) entry.put("color", item.get("color"));
                ordered.add(entry);
                placed.add(g);
            }
            for (Map<String, Object> item : current) if (!placed.contains((String) item.get("id"))) ordered.add(item);
            if (ordered.stream().noneMatch(i -> Boolean.TRUE.equals(i.get("enabled")))) return null;
            Files.createDirectories(folder);
            Map<String, Object> fixed;
            if (colors instanceof Map<?, ?> m) {
                fixed = new LinkedHashMap<>();
                Map<String, Object> f = fixed;
                m.forEach((k, v) -> {
                    if (FIXED_IDS.contains(k) && validColor(v)) f.put((String) k, v);
                });
            } else {
                fixed = (Map<String, Object>) saved.get("colors");
            }
            Map<String, Object> prefs = new LinkedHashMap<>();
            prefs.put("glossaries", ordered);
            prefs.put("colors", fixed);
            writeJson(folder.resolve(PREFERENCES_FILE), prefs);
            return prefs;
        }
    }

    /** Stores an uploaded word file and adds it, enabled, right before the Falcon dictionary; returns
     * {preferences, entry} (mirrors add_dictionary). */
    @SuppressWarnings("unchecked")
    public static Object[] addDictionary(String pseudo, String filename, byte[] data, String language)
            throws IOException, WordlistUnrecognised {
        Object[] parsed = parseWordlist(data);
        List<Entry> entries = (List<Entry>) parsed[0];
        boolean scored = (Boolean) parsed[1];
        String lang = SUPPORTED_LANGUAGES.contains(language) ? language : "fr";
        String base = filename == null ? "" : filename;
        int slash = base.lastIndexOf('/');
        String name = Py.strip(base.substring(slash + 1));
        if (name.isEmpty()) name = "dictionnaire";
        int dot = name.lastIndexOf('.');
        String ext = dot > 0 ? name.substring(dot) : "";
        synchronized (LOCK) {
            Path folder = profileDir(pseudo);
            Files.createDirectories(folder);
            byte[] idBytes = new byte[6];
            RANDOM.nextBytes(idBytes);
            String id = "d" + HexFormat.of().formatHex(idBytes);
            Files.write(folder.resolve("upload_" + id + (EXT_RE.matcher(ext).matches() ? ext : "")), data);
            Path tmp = folder.resolve("dict_" + id + ".tsv.tmp");
            try (BufferedWriter out = Files.newBufferedWriter(tmp, StandardCharsets.UTF_8)) {
                for (Entry e : entries) {
                    out.write(e.mot() + "\t" + String.join(";", e.spellings()) + "\t" + e.usage() + "\n");
                }
            }
            Files.move(tmp, dictPath(folder, id), StandardCopyOption.REPLACE_EXISTING, StandardCopyOption.ATOMIC_MOVE);
            Map<String, Object> prefs = normalized(folder, readJson(folder.resolve(PREFERENCES_FILE)));
            Map<String, Object> item = new LinkedHashMap<>();
            item.put("id", id);
            item.put("enabled", true);
            item.put("name", name.length() > 120 ? name.substring(0, 120) : name);
            item.put("language", lang);
            item.put("entries", entries.size());
            item.put("scored", scored);
            item.put("uploaded_at", LocalDateTime.now().format(DateTimeFormatter.ofPattern("yyyy-MM-dd'T'HH:mm:ss")));
            List<Map<String, Object>> items = (List<Map<String, Object>>) prefs.get("glossaries");
            int at = 0;
            while (!FALCON_IDS.contains(items.get(at).get("id"))) at++;
            items.add(at, item);
            writeJson(folder.resolve(PREFERENCES_FILE), prefs);
            return new Object[]{prefs, item};
        }
    }

    /** Removes one user dictionary; null when it does not exist (mirrors delete_dictionary). */
    public static Map<String, Object> deleteDictionary(String pseudo, String id) throws IOException {
        if (id == null || !ID_RE.matcher(id).matches()) return null;
        synchronized (LOCK) {
            Path folder = profileDir(pseudo);
            if (!Files.exists(dictPath(folder, id))) return null;
            try (DirectoryStream<Path> ds = Files.newDirectoryStream(folder, "*_" + id + "*")) {
                for (Path p : ds) {
                    try {
                        Files.deleteIfExists(p);
                    } catch (IOException ignored) {
                        // best-effort
                    }
                }
            }
            Map<String, Object> prefs = normalized(folder, readJson(folder.resolve(PREFERENCES_FILE)));
            writeJson(folder.resolve(PREFERENCES_FILE), prefs);
            return prefs;
        }
    }

    /** The player's glossaries merged into {@code language}'s lexicon, null when they leave it as by default (no
     * enabled user dictionary in that language, both Falcon glossaries enabled, Scrabble first) or when no enabled item
     * applies to it (mirrors lexicon_spec): the merged file (MOT TAB ACCENTUE TAB CANONIQUE TAB SCORE TAB RANK TAB ID,
     * each word once, from the highest-ranked dictionary holding it), named after a hash of the ranked ids, the ranks
     * of the two Falcon glossaries and the ranked ids. */
    @SuppressWarnings("unchecked")
    public static Words.UserLexicon lexiconSpec(String pseudo, String language) {
        if (pseudo == null || pseudo.isEmpty() || language == null || language.isEmpty()) return null;
        Path folder = profileDir(pseudo);
        Map<String, Object> prefs = normalized(folder, readJson(folder.resolve(PREFERENCES_FILE)));
        List<Map<String, Object>> ranked = new ArrayList<>();
        for (Map<String, Object> item : (List<Map<String, Object>>) prefs.get("glossaries")) {
            if (Boolean.TRUE.equals(item.get("enabled"))
                    && (FALCON_IDS.contains(item.get("id")) || language.equals(item.get("language")))) ranked.add(item);
        }
        List<String> ids = new ArrayList<>();
        for (Map<String, Object> item : ranked) ids.add((String) item.get("id"));
        if (ids.isEmpty() || ids.equals(FALCON_IDS)) return null;
        Integer scrabbleRank = ids.contains(SCRABBLE_ID) ? ids.indexOf(SCRABBLE_ID) : null;
        Integer othersRank = ids.contains(OTHERS_ID) ? ids.indexOf(OTHERS_ID) : null;
        String digest;
        try {
            digest = HexFormat.of().formatHex(MessageDigest.getInstance("SHA-256")
                    .digest(String.join("\n", ids).getBytes(StandardCharsets.UTF_8))).substring(0, 16);
        } catch (java.security.NoSuchAlgorithmException e) {
            throw new IllegalStateException(e);
        }
        Path path = folder.resolve("lexicon_" + language + "_" + digest + ".tsv");
        synchronized (LOCK) {
            if (!Files.exists(path)) {
                try {
                    Files.createDirectories(folder);
                    Set<String> seen = new HashSet<>();
                    Path tmp = path.resolveSibling(path.getFileName() + ".tmp");
                    try (BufferedWriter out = Files.newBufferedWriter(tmp, StandardCharsets.UTF_8)) {
                        for (int rank = 0; rank < ranked.size(); rank++) {
                            String id = ids.get(rank);
                            if (FALCON_IDS.contains(id)) continue;
                            Path src = dictPath(folder, id);
                            if (!Files.exists(src)) continue;
                            try (BufferedReader in = Files.newBufferedReader(src, StandardCharsets.UTF_8)) {
                                String line;
                                while ((line = in.readLine()) != null) {
                                    String[] parts = line.split("\t", -1);
                                    if (parts.length < 3 || seen.contains(parts[0])) continue;
                                    seen.add(parts[0]);
                                    String main = parts[1].split(";", -1)[0];
                                    if (main.isEmpty()) main = parts[0];
                                    out.write(parts[0] + "\t" + parts[1] + "\t" + main + "\t" + parts[2] + "\t" + rank
                                            + "\t" + id + "\n");
                                }
                            }
                        }
                    }
                    Files.move(tmp, path, StandardCopyOption.REPLACE_EXISTING, StandardCopyOption.ATOMIC_MOVE);
                    try (DirectoryStream<Path> ds = Files.newDirectoryStream(folder, "lexicon_" + language + "_*.tsv")) {
                        for (Path old : ds) {
                            if (!old.equals(path)) {
                                try {
                                    Files.deleteIfExists(old);
                                } catch (IOException ignored) {
                                    // best-effort
                                }
                            }
                        }
                    }
                } catch (IOException e) {
                    Log.exception("user lexicon: echec d'ecriture de " + path, e);
                    return null;
                }
            }
        }
        return new Words.UserLexicon(path.toString(), scrabbleRank, othersRank, List.copyOf(ids));
    }
}
