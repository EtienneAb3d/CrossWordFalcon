package falcon;

import java.io.BufferedReader;
import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.HashMap;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Locale;
import java.util.Map;
import java.util.concurrent.ConcurrentHashMap;

/**
 * Real dictionary definitions for a word's canonical form(s), read from
 * data/gloss_dictionary/&lt;lang&gt;_glosses.jsonl (Wiktionary via Kaikki.org).
 * Mirrors backend/gloss_lookup.py: one lazily-built index per language,
 * cached for the process lifetime, keyed by lower-cased lemma.
 */
public final class GlossLookup {
    private GlossLookup() {}

    public static final Path GLOSS_DIR = Env.path("data", "gloss_dictionary");

    /** language -> {lemma_lower: {"word": ..., "entries": [{"pos", "glosses"}]}} */
    private static final Map<String, Map<String, Map<String, Object>>> CACHE = new ConcurrentHashMap<>();

    public static Map<String, Map<String, Object>> load(String language) {
        return CACHE.computeIfAbsent(language, GlossLookup::build);
    }

    private static Map<String, Map<String, Object>> build(String language) {
        Map<String, Map<String, Object>> index = new HashMap<>();
        Path path = GLOSS_DIR.resolve(language + "_glosses.jsonl");
        if (!Files.exists(path)) return index;
        try (BufferedReader r = Files.newBufferedReader(path, StandardCharsets.UTF_8)) {
            String line;
            while ((line = r.readLine()) != null) {
                line = Py.strip(line);
                if (line.isEmpty()) continue;
                Map<String, Object> entry;
                try {
                    entry = Json.parseObject(line);
                } catch (IllegalArgumentException e) {
                    continue;
                }
                Object word = entry.get("word");
                if (word == null) continue;
                index.put(word.toString().toLowerCase(Locale.ROOT), entry);
            }
        } catch (IOException e) {
            Env.log("gloss_lookup: cannot read " + path + ": " + e.getMessage());
        }
        return index;
    }

    /** {lemma: entries} for every lemma of {@code canonicalForms} that has an
     * entry, matched case-insensitively. */
    public static Map<String, List<Object>> findGlossesForCanonicals(Iterable<String> canonicalForms, String language) {
        Map<String, Map<String, Object>> index = load(language);
        Map<String, List<Object>> result = new LinkedHashMap<>();
        for (String lemma : canonicalForms) {
            Map<String, Object> entry = index.get(lemma.toLowerCase(Locale.ROOT));
            if (entry != null) result.put(lemma, Json.listOrEmpty(entry.get("entries")));
        }
        return result;
    }

    /** True if the language has a built gloss dictionary at all. */
    public static boolean hasGlossDictionary(String language) {
        return !load(language).isEmpty();
    }

    /** True if any of {@code candidates} has an entry in the language's
     * gloss dictionary. */
    public static boolean hasAnyGloss(Iterable<String> candidates, String language) {
        Map<String, Map<String, Object>> index = load(language);
        for (String c : candidates) {
            if (index.containsKey(c.toLowerCase(Locale.ROOT))) return true;
        }
        return false;
    }
}
