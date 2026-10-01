package falcon;

import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.AbstractMap;
import java.util.AbstractSet;
import java.util.Collections;
import java.util.HashMap;
import java.util.Iterator;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Locale;
import java.util.Map;
import java.util.Set;
import java.util.concurrent.ConcurrentHashMap;

/**
 * Real dictionary definitions for a word's canonical form(s), read from
 * data/gloss_dictionary/&lt;lang&gt;_glosses.jsonl (Wiktionary via Kaikki.org).
 * Mirrors backend/gloss_lookup.py: one lazily-built index per language,
 * cached for the process lifetime, keyed by lower-cased lemma, holding only
 * each entry's byte offset in the JSONL file — the entry itself is read and
 * parsed from disk when looked up.
 */
public final class GlossLookup {
    private GlossLookup() {}

    public static final Path GLOSS_DIR = Env.path("data", "gloss_dictionary");

    /** language -> {lemma_lower: entry offset} */
    private static final Map<String, GlossIndex> CACHE = new ConcurrentHashMap<>();

    /** {lemma key: {"word": ..., "entries": [{"pos", "glosses"}]}} of one language, backed by entry offsets (mirrors
     * _GlossIndex): {@link #get} reads the entry from disk. */
    public static final class GlossIndex extends AbstractMap<String, Map<String, Object>> {
        private final Path path;
        private final Map<String, Long> offsets;

        GlossIndex(Path path, Map<String, Long> offsets) {
            this.path = path;
            this.offsets = offsets;
        }

        @Override
        public Map<String, Object> get(Object key) {
            Long offset = offsets.get(key);
            if (offset == null) return null;
            try {
                return Json.parseObject(Py.strip(TextLines.readLineAt(path, offset)));
            } catch (IOException | IllegalArgumentException e) {
                return null;
            }
        }

        @Override
        public boolean containsKey(Object key) {
            return offsets.containsKey(key);
        }

        @Override
        public Set<String> keySet() {
            return Collections.unmodifiableSet(offsets.keySet());
        }

        @Override
        public int size() {
            return offsets.size();
        }

        @Override
        public boolean isEmpty() {
            return offsets.isEmpty();
        }

        @Override
        public Set<Map.Entry<String, Map<String, Object>>> entrySet() {
            return new AbstractSet<>() {
                @Override
                public Iterator<Map.Entry<String, Map<String, Object>>> iterator() {
                    Iterator<String> keys = offsets.keySet().iterator();
                    return new Iterator<>() {
                        @Override
                        public boolean hasNext() {
                            return keys.hasNext();
                        }

                        @Override
                        public Map.Entry<String, Map<String, Object>> next() {
                            String k = keys.next();
                            return new SimpleImmutableEntry<>(k, get(k));
                        }
                    };
                }

                @Override
                public int size() {
                    return offsets.size();
                }
            };
        }
    }

    public static GlossIndex load(String language) {
        return CACHE.computeIfAbsent(language, GlossLookup::build);
    }

    private static GlossIndex build(String language) {
        Map<String, Long> offsets = new HashMap<>();
        Path path = GLOSS_DIR.resolve(language + "_glosses.jsonl");
        if (!Files.exists(path)) return new GlossIndex(path, offsets);
        try {
            TextLines.forEachLine(path, (offset, line) -> {
                line = Py.strip(line);
                if (line.isEmpty()) return;
                Map<String, Object> entry;
                try {
                    entry = Json.parseObject(line);
                } catch (IllegalArgumentException e) {
                    return;
                }
                Object word = entry.get("word");
                if (word == null) return;
                offsets.put(Py.lookupKey(word.toString()), offset);
            });
        } catch (IOException e) {
            Env.log("gloss_lookup: cannot read " + path + ": " + e.getMessage());
        }
        return new GlossIndex(path, offsets);
    }

    /** {lemma: entries} for every lemma of {@code canonicalForms} that has an
     * entry, matched case-insensitively. */
    public static Map<String, List<Object>> findGlossesForCanonicals(Iterable<String> canonicalForms, String language) {
        GlossIndex index = load(language);
        Map<String, List<Object>> result = new LinkedHashMap<>();
        for (String lemma : canonicalForms) {
            Map<String, Object> entry = index.get(Py.lookupKey(lemma));
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
        GlossIndex index = load(language);
        for (String c : candidates) {
            if (index.containsKey(Py.lookupKey(c))) return true;
        }
        return false;
    }
}
