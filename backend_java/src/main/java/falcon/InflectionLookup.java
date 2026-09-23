package falcon;

import java.io.BufferedReader;
import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.ArrayList;
import java.util.HashMap;
import java.util.List;
import java.util.Locale;
import java.util.Map;
import java.util.concurrent.ConcurrentHashMap;

/**
 * Pure-local grammatical analysis of an exact inflected word form, read from
 * data/inflection/&lt;lang&gt;.jsonl (mirrors backend/inflection_lookup.py).
 */
public final class InflectionLookup {
    private InflectionLookup() {}

    private static final Path DIR = Env.path("data", "inflection");

    private static final Map<String, String> POS_LABEL = Map.of(
            "adj", "adjective",
            "adv", "adverb",
            "name", "proper noun",
            "num", "numeral",
            "pron", "pronoun",
            "det", "determiner",
            "prep", "preposition",
            "conj", "conjunction",
            "intj", "interjection");

    /** (pos_code, description) pair. */
    public record Analysis(String pos, String description) {}

    private static final Map<String, Map<String, List<Analysis>>> CACHE = new ConcurrentHashMap<>();

    private static Map<String, List<Analysis>> load(String language) {
        return CACHE.computeIfAbsent(language, InflectionLookup::build);
    }

    private static Map<String, List<Analysis>> build(String language) {
        Map<String, List<Analysis>> index = new HashMap<>();
        Path path = DIR.resolve(language + ".jsonl");
        if (!Files.exists(path)) return index;
        try (BufferedReader r = Files.newBufferedReader(path, StandardCharsets.UTF_8)) {
            String line;
            while ((line = r.readLine()) != null) {
                line = Py.strip(line);
                if (line.isEmpty()) continue;
                Map<String, Object> rec;
                try {
                    rec = Json.parseObject(line);
                } catch (IllegalArgumentException e) {
                    continue;
                }
                String form = Json.str(rec, "form", null);
                if (form == null || form.isEmpty()) continue;
                List<Analysis> out = new ArrayList<>();
                for (Object a : Json.listOrEmpty(rec.get("analyses"))) {
                    String pos = Json.str(a, "pos", null);
                    String tags = Json.str(a, "tags", null);
                    String lemma = Json.str(a, "lemma", null);
                    List<String> bits = new ArrayList<>();
                    if (pos != null && !pos.isEmpty()) bits.add(POS_LABEL.getOrDefault(pos, pos));
                    if (tags != null && !tags.isEmpty()) bits.add(tags);
                    String text = String.join(", ", bits);
                    if (lemma != null && !lemma.isEmpty() && !lemma.toLowerCase(Locale.ROOT).equals(form)) {
                        text += " (of \"" + lemma + "\")";
                    }
                    Analysis pair = new Analysis(pos, text);
                    if (!out.contains(pair)) out.add(pair);
                }
                index.put(form, out);
            }
        } catch (IOException e) {
            Env.log("inflection_lookup: cannot read " + path + ": " + e.getMessage());
        }
        return index;
    }

    /** Analyses of the exact {@code word}, or an empty list. Never throws. */
    public static List<Analysis> describeForm(String word, String language) {
        return load(language).getOrDefault(word.toLowerCase(Locale.ROOT), List.of());
    }
}
