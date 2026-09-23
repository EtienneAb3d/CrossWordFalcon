package falcon;

import java.io.BufferedReader;
import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.ArrayList;
import java.util.Collections;
import java.util.HashMap;
import java.util.HashSet;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Locale;
import java.util.Map;
import java.util.Set;
import java.util.concurrent.ConcurrentHashMap;
import java.util.concurrent.ThreadLocalRandom;
import java.util.regex.Matcher;
import java.util.regex.Pattern;

/**
 * Real usage examples of a word's exact inflected form in the reference
 * sentence corpus (mirrors backend/example_sentences.py): one streaming pass
 * per language builds a reservoir-sampled index of every wordlist word.
 */
public final class ExampleSentences {
    private ExampleSentences() {}

    private static final Path CORPUS_DIR = Env.path("data", "reference_corpus");
    private static final Path DATA_DIR = Env.path("data");
    private static final Pattern WORD_RE = Pattern.compile("\\p{L}+");

    public static final int DEFAULT_LIMIT = 5;
    public static final int RESERVOIR_SIZE = 20;

    private static final Map<String, Map<String, List<String>>> INDEX_CACHE = new ConcurrentHashMap<>();

    private static Set<String> loadWordlistWords(String language) throws IOException {
        Path path = DATA_DIR.resolve("wordlist_" + language + "_full.tsv");
        Set<String> words = new HashSet<>();
        try (BufferedReader r = Files.newBufferedReader(path, StandardCharsets.UTF_8)) {
            String line;
            while ((line = r.readLine()) != null) {
                String[] parts = line.split("\t", -1);
                if (parts.length >= 2) {
                    String accented = Py.strip(parts[1]);
                    if (!accented.isEmpty()) words.add(accented.toLowerCase(Locale.ROOT));
                }
            }
        }
        return words;
    }

    private static Map<String, List<String>> buildIndex(String language) {
        Path corpus = CORPUS_DIR.resolve(language + "_sentences.txt");
        Map<String, List<String>> reservoirs = new HashMap<>();
        if (!Files.exists(corpus)) return reservoirs;
        try {
            Set<String> targets = loadWordlistWords(language);
            Map<String, Integer> seen = new HashMap<>();
            ThreadLocalRandom rng = ThreadLocalRandom.current();
            try (BufferedReader r = Files.newBufferedReader(corpus, StandardCharsets.UTF_8)) {
                String line;
                while ((line = r.readLine()) != null) {
                    if (line.isEmpty()) continue;
                    Matcher m = WORD_RE.matcher(line);
                    while (m.find()) {
                        String w = m.group().toLowerCase(Locale.ROOT);
                        if (!targets.contains(w)) continue;
                        int count = seen.merge(w, 1, Integer::sum);
                        List<String> reservoir = reservoirs.computeIfAbsent(w, k -> new ArrayList<>());
                        if (reservoir.size() < RESERVOIR_SIZE) {
                            reservoir.add(line);
                        } else {
                            int j = rng.nextInt(count);
                            if (j < RESERVOIR_SIZE) reservoir.set(j, line);
                        }
                    }
                }
            }
        } catch (IOException e) {
            Env.log("example_sentences: " + e.getMessage());
        }
        return reservoirs;
    }

    private static Map<String, List<String>> index(String language) {
        return INDEX_CACHE.computeIfAbsent(language, ExampleSentences::buildIndex);
    }

    /** {word: up to {@code limit} random sentences} for every word with at
     * least one match. */
    public static Map<String, List<String>> findExamplesForWords(Iterable<String> words, String language, int limit) {
        Map<String, List<String>> idx = index(language);
        Map<String, List<String>> result = new LinkedHashMap<>();
        for (String w : words) {
            List<String> reservoir = idx.get(w.toLowerCase(Locale.ROOT));
            if (reservoir != null && !reservoir.isEmpty()) {
                List<String> copy = new ArrayList<>(reservoir);
                Collections.shuffle(copy);
                result.put(w, new ArrayList<>(copy.subList(0, Math.min(limit, copy.size()))));
            }
        }
        return result;
    }
}
