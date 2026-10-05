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

    /** One language's table: analyses per form, and the lemmas each form is an inflection of. */
    private record Table(Map<String, List<Analysis>> analyses, Map<String, List<String>> lemmas) {}

    private static final Map<String, Table> CACHE = new ConcurrentHashMap<>();

    private static Table load(String language) {
        return CACHE.computeIfAbsent(language, InflectionLookup::build);
    }

    private static Table build(String language) {
        Map<String, List<Analysis>> index = new HashMap<>();
        Map<String, List<String>> lemmas = new HashMap<>();
        Table table = new Table(index, lemmas);
        Path path = DIR.resolve(language + ".jsonl");
        if (!Files.exists(path)) return table;
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
                form = Py.lookupKey(form);
                List<Analysis> out = new ArrayList<>();
                List<String> formLemmas = new ArrayList<>();
                for (Object a : Json.listOrEmpty(rec.get("analyses"))) {
                    String pos = Json.str(a, "pos", null);
                    String tags = Json.str(a, "tags", null);
                    String lemma = Json.str(a, "lemma", null);
                    List<String> bits = new ArrayList<>();
                    if (pos != null && !pos.isEmpty()) bits.add(POS_LABEL.getOrDefault(pos, pos));
                    if (tags != null && !tags.isEmpty()) bits.add(tags);
                    String text = String.join(", ", bits);
                    if (lemma != null && !lemma.isEmpty() && !Py.lookupKey(lemma).equals(form)) {
                        text += " (of \"" + lemma + "\")";
                        if (!formLemmas.contains(lemma)) formLemmas.add(lemma);
                    }
                    Analysis pair = new Analysis(pos, text);
                    if (!out.contains(pair)) out.add(pair);
                }
                index.put(form, out);
                if (!formLemmas.isEmpty()) lemmas.put(form, formLemmas);
            }
        } catch (IOException e) {
            Env.log("inflection_lookup: cannot read " + path + ": " + e.getMessage());
        }
        return table;
    }

    /** Analyses of the exact {@code word}, or an empty list. Never throws. */
    public static List<Analysis> describeForm(String word, String language) {
        return load(language).analyses().getOrDefault(Py.lookupKey(word), List.of());
    }

    /** The lemmas the table gives the exact {@code word} as an inflected form of, in file order
     *  (backend/inflection_lookup.py, lemmas_of). */
    public static List<String> lemmasOf(String word, String language) {
        return load(language).lemmas().getOrDefault(Py.lookupKey(word), List.of());
    }
}
