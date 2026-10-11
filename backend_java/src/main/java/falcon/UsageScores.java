package falcon;

import java.io.BufferedReader;
import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.HashMap;
import java.util.Locale;
import java.util.Map;
import java.util.Optional;
import java.util.concurrent.ConcurrentHashMap;

/**
 * Usage score of a word (data/usage_score_&lt;lang&gt;_scrabble.tsv and, for the freq words absent from the
 * Scrabble wordlist, data/usage_score_&lt;lang&gt;_scrabble_out.tsv) for the
 * "Dictionnaire" panel, read once per language and cached (mirrors
 * backend/usage_scores.py).
 */
public final class UsageScores {
    private UsageScores() {}

    private static final Path DIR = Env.path("data");
    private static final Map<String, Optional<Map<String, Integer>>> CACHE = new ConcurrentHashMap<>();

    private static Optional<Map<String, Integer>> load(String language) {
        return CACHE.computeIfAbsent(language, lang -> {
            Map<String, Integer> scores = null;
            for (String name : new String[]{"usage_score_" + lang + "_scrabble.tsv",
                    "usage_score_" + lang + "_scrabble_out.tsv"}) {
                Path path = DIR.resolve(name);
                if (!Files.exists(path)) continue;
                if (scores == null) scores = new HashMap<>();
                try (BufferedReader r = Files.newBufferedReader(path, StandardCharsets.UTF_8)) {
                    String line;
                    while ((line = r.readLine()) != null) {
                        String[] parts = line.split("\t", -1);
                        if (parts.length >= 3 && !parts[2].isEmpty()
                                && parts[2].chars().allMatch(Character::isDigit)) {
                            scores.put(parts[0].toUpperCase(Locale.ROOT), Integer.parseInt(parts[2]));
                        }
                    }
                } catch (IOException e) {
                    return Optional.empty();
                }
            }
            return Optional.ofNullable(scores);
        });
    }

    /** The usage score of the grid form {@code mot}, null when unknown. */
    public static Integer usageScore(String mot, String language) {
        return load(language).map(m -> m.get(mot.toUpperCase(Locale.ROOT))).orElse(null);
    }
}
