package falcon;

import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.AbstractMap;
import java.util.AbstractSet;
import java.util.Collections;
import java.util.HashMap;
import java.util.HashSet;
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
 * parsed from disk when looked up. The multi-word expressions' dictionary
 * (&lt;lang&gt;_glosses_multi.jsonl) is read with it, an entry of the
 * single-word one winning a shared key.
 */
public final class GlossLookup {
    private GlossLookup() {}

    public static final Path GLOSS_DIR = Env.path("data", "gloss_dictionary");

    /** language -> {lemma_lower: entry offset} */
    private static final Map<String, GlossIndex> CACHE = new ConcurrentHashMap<>();

    /** {lemma key: {"word": ..., "entries": [{"pos", "glosses"}]}} of one language, backed by entry offsets (mirrors
     * _GlossIndex): {@link #get} reads the entry from disk. */
    public static final class GlossIndex extends AbstractMap<String, Map<String, Object>> {
        private final Path[] paths;
        /** key -> entry offset shifted left one bit, the low bit the index of its file in {@code paths}. */
        private final Map<String, Long> offsets;
        /** Keys whose entry has only proper-noun senses (every "pos" "name"). */
        final Set<String> nameOnly;

        GlossIndex(Path[] paths, Map<String, Long> offsets, Set<String> nameOnly) {
            this.paths = paths;
            this.offsets = offsets;
            this.nameOnly = nameOnly;
        }

        @Override
        public Map<String, Object> get(Object key) {
            Long ref = offsets.get(key);
            if (ref == null) return null;
            try {
                return Json.parseObject(Py.strip(TextLines.readLineAt(paths[(int) (ref & 1)], ref >>> 1)));
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
        Set<String> nameOnly = new HashSet<>();
        Path[] paths = {GLOSS_DIR.resolve(language + "_glosses.jsonl"),
                GLOSS_DIR.resolve(language + "_glosses_multi.jsonl")};
        for (int part = 0; part < paths.length; part++) {
            Path path = paths[part];
            if (!Files.exists(path)) continue;
            // A key of an earlier file wins (mirrors _GlossIndex's parts order).
            Set<String> earlier = new HashSet<>(offsets.keySet());
            long bit = part;
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
                    String key = Py.lookupKey(word.toString());
                    if (earlier.contains(key)) return;
                    offsets.put(key, offset << 1 | bit);
                    List<Object> senses = Json.listOrEmpty(entry.get("entries"));
                    boolean onlyName = !senses.isEmpty();
                    for (Object s : senses) {
                        if (!(s instanceof Map<?, ?> m) || !"name".equals(m.get("pos"))) {
                            onlyName = false;
                            break;
                        }
                    }
                    if (onlyName) nameOnly.add(key);
                    else nameOnly.remove(key);
                });
            } catch (IOException e) {
                Env.log("gloss_lookup: cannot read " + path + ": " + e.getMessage());
            }
        }
        return new GlossIndex(paths, offsets, Collections.unmodifiableSet(nameOnly));
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

    /** True if {@code candidates} (a word's spelling with its canonical forms) have at least one gloss-dictionary
     * entry and every entry found has only proper-noun ("name") senses (mirrors only_proper_noun_glosses). */
    public static boolean onlyProperNounGlosses(Iterable<String> candidates, String language) {
        GlossIndex index = load(language);
        boolean found = false;
        for (String c : candidates) {
            String key = Py.lookupKey(c);
            if (!index.containsKey(key)) continue;
            if (!index.nameOnly.contains(key)) return false;
            found = true;
        }
        return found;
    }
}
