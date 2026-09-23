package falcon;

import java.io.BufferedReader;
import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.text.Normalizer;
import java.util.ArrayList;
import java.util.Comparator;
import java.util.HashMap;
import java.util.HashSet;
import java.util.LinkedHashMap;
import java.util.LinkedHashSet;
import java.util.List;
import java.util.Locale;
import java.util.Map;
import java.util.Optional;
import java.util.Set;
import java.util.TreeSet;
import java.util.concurrent.ConcurrentHashMap;

/**
 * Accent- and case-insensitive root-family search for the "Dictionnaire"
 * panel (mirrors backend/dictionary_lookup.py).
 */
public final class DictionaryLookup {
    private DictionaryLookup() {}

    private static final Path WORDLIST_DIR = Env.path("data");
    public static final int MAX_ROWS = 300;

    private static final class LangIndex {
        final List<String> forms = new ArrayList<>();
        final List<List<String>> canonicals = new ArrayList<>();
        final Map<String, List<Integer>> byKey = new HashMap<>();
        final Map<String, Set<Integer>> byCanon = new HashMap<>();
    }

    private static final Map<String, Optional<LangIndex>> CACHE = new ConcurrentHashMap<>();

    /** Folds the French ligatures, NFKD-decomposes, drops every non-ASCII
     * code point, lower-cases and trims. */
    public static String norm(String text) {
        String folded = text.replace("œ", "oe").replace("Œ", "OE").replace("æ", "ae").replace("Æ", "AE");
        String decomposed = Normalizer.normalize(folded, Normalizer.Form.NFKD);
        StringBuilder sb = new StringBuilder(decomposed.length());
        for (int i = 0; i < decomposed.length(); i++) {
            char ch = decomposed.charAt(i);
            if (ch < 128) sb.append(ch);
        }
        return Py.strip(sb.toString().toLowerCase(Locale.ROOT));
    }

    private static Optional<LangIndex> build(String language) {
        Path path = WORDLIST_DIR.resolve("wordlist_" + language + "_full.tsv");
        if (!Files.exists(path)) return Optional.empty();
        LangIndex idx = new LangIndex();
        try (BufferedReader r = Files.newBufferedReader(path, StandardCharsets.UTF_8)) {
            String line;
            while ((line = r.readLine()) != null) {
                if (line.isEmpty() || line.startsWith("#")) continue;
                String[] parts = line.split("\t", -1);
                if (parts.length < 2) continue;
                String mot = parts[0];
                String accented = parts[1].isEmpty() ? mot : parts[1];
                String canonField = (parts.length >= 4 && !parts[3].isEmpty()) ? parts[3] : accented;
                List<String> canon = new ArrayList<>();
                for (String p : canonField.split(";", -1)) {
                    String c = Py.strip(p);
                    if (!c.isEmpty()) canon.add(c);
                }
                if (canon.isEmpty()) canon.add(accented);
                int rowI = idx.forms.size();
                idx.forms.add(accented);
                idx.canonicals.add(canon);
                for (String key : new LinkedHashSet<>(List.of(norm(mot), norm(accented)))) {
                    if (!key.isEmpty()) idx.byKey.computeIfAbsent(key, k -> new ArrayList<>()).add(rowI);
                }
                for (String c : canon) {
                    String ck = norm(c);
                    if (!ck.isEmpty()) idx.byCanon.computeIfAbsent(ck, k -> new HashSet<>()).add(rowI);
                }
            }
        } catch (IOException e) {
            return Optional.empty();
        }
        return Optional.of(idx);
    }

    private static List<Object> definitionsFor(List<String> canonicals, Map<String, Map<String, Object>> glossIndex,
                                               boolean showLemma) {
        List<Object> out = new ArrayList<>();
        Set<List<String>> seen = new HashSet<>();
        for (String lemma : canonicals) {
            Map<String, Object> entry = glossIndex.get(lemma.toLowerCase(Locale.ROOT));
            if (entry == null) continue;
            for (Object sense : Json.listOrEmpty(entry.get("entries"))) {
                String pos = Json.str(sense, "pos", "");
                for (Object g : Json.listOrEmpty(Json.get(sense, "glosses"))) {
                    String gloss = String.valueOf(g);
                    List<String> key = List.of(lemma.toLowerCase(Locale.ROOT), pos, gloss);
                    if (!seen.add(key)) continue;
                    out.add(Json.obj("pos", pos, "gloss", gloss, "lemma", showLemma ? lemma : ""));
                }
            }
        }
        return out;
    }

    public static Map<String, Object> search(String query, String language) {
        String q = query == null ? "" : Py.strip(query);
        Map<String, Object> result = Json.obj("query", q, "language", language, "rows", new ArrayList<>(),
                "truncated", false);
        if (q.isEmpty()) return result;
        Optional<LangIndex> oidx = CACHE.computeIfAbsent(language, DictionaryLookup::build);
        if (oidx.isEmpty()) return result;
        LangIndex idx = oidx.get();
        Map<String, Map<String, Object>> glossIndex = GlossLookup.load(language);
        String qn = norm(q);

        Set<String> roots = new LinkedHashSet<>();
        if (idx.byCanon.containsKey(qn)) roots.add(qn);
        for (int i : idx.byKey.getOrDefault(qn, List.of())) {
            for (String c : idx.canonicals.get(i)) roots.add(norm(c));
        }
        if (roots.isEmpty()) {
            for (String w : glossIndex.keySet()) {
                if (norm(w).equals(qn)) {
                    roots.add(qn);
                    break;
                }
            }
        }
        if (roots.isEmpty()) return result;

        Set<Integer> family = new TreeSet<>();
        for (String r : roots) family.addAll(idx.byCanon.getOrDefault(r, Set.of()));

        record Row(String form, String canonical, Set<String> canonNorm, List<Object> definitions) {}
        List<Row> rows = new ArrayList<>();
        for (int i : family) {
            List<String> canon = idx.canonicals.get(i);
            Set<String> cn = new HashSet<>();
            for (String c : canon) cn.add(norm(c));
            rows.add(new Row(idx.forms.get(i), String.join("; ", canon), cn,
                    definitionsFor(canon, glossIndex, canon.size() > 1)));
        }
        if (roots.contains(qn)) {
            boolean present = false;
            for (Row row : rows) {
                if (row.canonNorm.contains(qn) && norm(row.form).equals(qn)) {
                    present = true;
                    break;
                }
            }
            if (!present) {
                for (String w : glossIndex.keySet()) {
                    if (norm(w).equals(qn)) {
                        rows.add(new Row(w, w, Set.of(qn), definitionsFor(List.of(w), glossIndex, false)));
                        break;
                    }
                }
            }
        }
        rows.sort(Comparator.<Row>comparingInt(row -> norm(row.form).equals(qn) ? 0 : 1)
                .thenComparingInt(row -> roots.contains(norm(row.form)) ? 0 : 1)
                .thenComparing(row -> norm(row.form)));
        if (rows.size() > MAX_ROWS) {
            rows = new ArrayList<>(rows.subList(0, MAX_ROWS));
            result.put("truncated", true);
        }
        List<Object> outRows = new ArrayList<>();
        for (Row row : rows) {
            Map<String, Object> m = new LinkedHashMap<>();
            m.put("form", row.form);
            m.put("canonical", row.canonical);
            m.put("definitions", row.definitions);
            outRows.add(m);
        }
        result.put("rows", outRows);
        return result;
    }
}
