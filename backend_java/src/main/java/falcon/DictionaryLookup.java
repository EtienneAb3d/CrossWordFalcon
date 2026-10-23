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
 * panel over the corpus, Scrabble, Hunspell and multi-word wordlists
 * (mirrors backend/dictionary_lookup.py).
 */
public final class DictionaryLookup {
    private DictionaryLookup() {}

    private static final Path WORDLIST_DIR = Env.path("data");
    public static final int MAX_ROWS = 300;

    /** Characters separating the words of a multi-word expression's spelling: a form holding one
     * takes its usage score from the multi-word file first (mirrors WORD_SEPARATORS). */
    public static final String WORD_SEPARATORS = " -'\u2019";

    static boolean hasWordSeparator(String form) {
        for (int i = 0; i < form.length(); i++) {
            if (WORD_SEPARATORS.indexOf(form.charAt(i)) >= 0) return true;
        }
        return false;
    }

    private static final class LangIndex {
        final List<String> forms = new ArrayList<>();
        final List<List<String>> canonicals = new ArrayList<>();
        final Map<String, List<Integer>> byKey = new HashMap<>();
        final Map<String, List<Integer>> byCanon = new HashMap<>();
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

    /** The wordlists read, in this order, with the index of their CANONIQUE column: the corpus wordlist
     * (one spelling per row), then the Scrabble, Hunspell and multi-word ones (ACCENTUE lists every
     * spelling, `;`-separated) — mirrors WORDLIST_SOURCES. */
    private static final List<Map.Entry<String, Integer>> WORDLIST_SOURCES = List.of(
            Map.entry("freq", 3), Map.entry("scrabble", 2), Map.entry("hunspell", 2), Map.entry("multi", 2));

    private static void addRow(LangIndex idx, Map<Object, Object> shared, String mot, String accented,
                               List<String> canon) {
        @SuppressWarnings("unchecked")
        List<String> sharedCanon = (List<String>) shared.computeIfAbsent(canon, k -> canon);
        int rowI = idx.forms.size();
        idx.forms.add(accented);
        idx.canonicals.add(sharedCanon);
        for (String key : new LinkedHashSet<>(List.of(norm(mot), norm(accented)))) {
            if (!key.isEmpty()) idx.byKey.computeIfAbsent(key, k -> new ArrayList<>()).add(rowI);
        }
        Set<String> keys = new LinkedHashSet<>();
        for (String c : sharedCanon) keys.add(norm(c));
        for (String ck : keys) {
            if (ck.isEmpty()) continue;
            String sk = (String) shared.computeIfAbsent(ck, k -> ck);
            idx.byCanon.computeIfAbsent(sk, k -> new ArrayList<>()).add(rowI);
        }
    }

    /** One row per (spelling, lemmas) of every wordlist of WORDLIST_SOURCES, in file order, the corpus
     * wordlist first; a spelling an earlier wordlist already gave a row gets none from a later one. */
    private static Optional<LangIndex> build(String language) {
        if (!Files.exists(WORDLIST_DIR.resolve("wordlist_" + language + "_freq.tsv"))) return Optional.empty();
        LangIndex idx = new LangIndex();
        Set<String> spelled = new HashSet<>();
        Map<Object, Object> shared = new HashMap<>();
        for (Map.Entry<String, Integer> src : WORDLIST_SOURCES) {
            Path path = WORDLIST_DIR.resolve("wordlist_" + language + "_" + src.getKey() + ".tsv");
            if (!Files.exists(path)) continue;
            boolean several = !src.getKey().equals("freq");
            int canonCol = src.getValue();
            Set<String> added = new HashSet<>();
            try (BufferedReader r = Files.newBufferedReader(path, StandardCharsets.UTF_8)) {
                String line;
                while ((line = r.readLine()) != null) {
                    if (line.isEmpty() || line.startsWith("#")) continue;
                    String[] parts = line.split("\t", -1);
                    if (parts.length < 2) continue;
                    String mot = parts[0];
                    List<String> spellings = new ArrayList<>();
                    if (several) {
                        for (String p : parts[1].split(";", -1)) {
                            String sp = Py.strip(p);
                            if (!sp.isEmpty()) spellings.add(sp);
                        }
                    } else if (!parts[1].isEmpty()) {
                        spellings.add(parts[1]);
                    }
                    if (spellings.isEmpty()) spellings.add(mot);
                    String canonField = parts.length > canonCol ? parts[canonCol] : "";
                    List<String> lemmas = new ArrayList<>();
                    for (String p : canonField.split(";", -1)) {
                        String c = Py.strip(p);
                        if (!c.isEmpty()) lemmas.add(c);
                    }
                    for (String accented : spellings) {
                        if (spelled.contains(accented)) continue;
                        added.add(accented);
                        addRow(idx, shared, mot, accented, lemmas.isEmpty() ? List.of(accented) : List.copyOf(lemmas));
                    }
                }
            } catch (IOException e) {
                if (!several) return Optional.empty();
            }
            spelled.addAll(added);
        }
        return Optional.of(idx);
    }

    /** Every wordlist row whose MOT or accented spelling normalizes to {@code word}, as {accented, canonicals}, in file order. */
    public static List<Map.Entry<String, List<String>>> wordForms(String word, String language) {
        Optional<LangIndex> oi = CACHE.computeIfAbsent(language, DictionaryLookup::build);
        List<Map.Entry<String, List<String>>> out = new ArrayList<>();
        if (oi.isEmpty()) return out;
        LangIndex idx = oi.get();
        for (int i : idx.byKey.getOrDefault(norm(word), List.of())) {
            out.add(Map.entry(idx.forms.get(i), idx.canonicals.get(i)));
        }
        return out;
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
        // The inflection table's lemmas of the exact form, which also covers a form the freq wordlist lacks
        // (backend/dictionary_lookup.py, search).
        List<String> inflectionLemmas = InflectionLookup.lemmasOf(q, language);
        for (String lemma : inflectionLemmas) roots.add(norm(lemma));
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
        for (String r : roots) family.addAll(idx.byCanon.getOrDefault(r, List.of()));

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
        Set<String> present = new HashSet<>();
        for (Row row : rows) present.add(norm(row.form));
        if (!inflectionLemmas.isEmpty() && idx.byKey.getOrDefault(qn, List.of()).isEmpty() && !present.contains(qn)) {
            Set<String> cn = new HashSet<>();
            for (String lemma : inflectionLemmas) cn.add(norm(lemma));
            rows.add(new Row(q.toLowerCase(Locale.ROOT), String.join("; ", inflectionLemmas), cn,
                    definitionsFor(inflectionLemmas, glossIndex, inflectionLemmas.size() > 1)));
            present.add(qn);
        }
        for (String lemma : inflectionLemmas) {
            if (!present.contains(norm(lemma)) && glossIndex.get(lemma.toLowerCase(Locale.ROOT)) != null) {
                rows.add(new Row(lemma, lemma, Set.of(norm(lemma)), definitionsFor(List.of(lemma), glossIndex, false)));
                present.add(norm(lemma));
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
            m.put("usage", UsageScores.usageScore(gridForm(row.form), language, hasWordSeparator(row.form)));
            outRows.add(m);
        }
        result.put("rows", outRows);
        return result;
    }

    /** The grid form (MOT column: bare, uppercase, A-Z only — a multi-word expression's separators
     * dropped) of a row's form (mirrors grid_form). */
    public static String gridForm(String form) {
        return norm(form.replace("ß", "ss")).toUpperCase(Locale.ROOT).replaceAll("[^A-Z]", "");
    }
}
