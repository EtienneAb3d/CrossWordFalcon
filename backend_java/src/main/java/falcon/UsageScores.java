package falcon;

import java.io.BufferedReader;
import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.ArrayList;
import java.util.HashMap;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Locale;
import java.util.Map;
import java.util.Optional;
import java.util.TreeSet;
import java.util.concurrent.ConcurrentHashMap;

/**
 * Usage score of a word (data/usage_score_&lt;lang&gt;_scrabble.tsv and, for the freq words absent from the
 * Scrabble wordlist, data/usage_score_&lt;lang&gt;_scrabble_out.tsv) for the
 * "Dictionnaire" panel, read once per language and cached (mirrors
 * backend/usage_scores.py).
 */
public final class UsageScores {
    private UsageScores() {}

    /** Lowest usage score counted by the per-length table of usageScoreCounts. */
    public static final int LENGTH_STATS_MIN_SCORE = 3;

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

    /**
     * Number of rated words per usage score, for the localhost-only "Statistiques" panel
     * (GET /api/usage_stats), over both files of each language, plus the number of words
     * rated at least LENGTH_STATS_MIN_SCORE per word length, longest first, and, from
     * {@code lexiconCounts} ({language: {difficulty: {word length: number of words}}}), each
     * difficulty's lexicon total and the "easy" lexicon's words per length (mirrors
     * usage_scores.usage_score_counts).
     */
    public static Map<String, Object> usageScoreCounts(List<String> languages,
                                                       Map<String, Map<String, Map<Integer, Integer>>> lexiconCounts) {
        if (lexiconCounts == null) lexiconCounts = Map.of();
        List<String> levels = lexiconCounts.isEmpty() ? List.of()
                : new ArrayList<>(lexiconCounts.values().iterator().next().keySet());
        Map<String, Optional<Map<String, Integer>>> perLanguage = new LinkedHashMap<>();
        for (String language : languages) perLanguage.put(language, load(language));
        TreeSet<Integer> scoreSet = new TreeSet<>();
        for (Optional<Map<String, Integer>> m : perLanguage.values()) m.ifPresent(x -> scoreSet.addAll(x.values()));
        List<Integer> scores = new ArrayList<>(scoreSet);
        TreeSet<Integer> lengthSet = new TreeSet<>(java.util.Comparator.reverseOrder());
        for (Optional<Map<String, Integer>> m : perLanguage.values()) {
            m.ifPresent(x -> x.forEach((w, s) -> {
                if (s >= LENGTH_STATS_MIN_SCORE) lengthSet.add(w.codePointCount(0, w.length()));
            }));
        }
        for (Map<String, Map<Integer, Integer>> c : lexiconCounts.values()) {
            Map<Integer, Integer> easy = c.get("easy");
            if (easy != null) lengthSet.addAll(easy.keySet());
        }
        List<Integer> lengths = new ArrayList<>(lengthSet);
        List<Object> rows = new ArrayList<>();
        for (Map.Entry<String, Optional<Map<String, Integer>>> e : perLanguage.entrySet()) {
            Map<String, Object> row = new LinkedHashMap<>();
            row.put("language", e.getKey());
            row.put("counts", null);
            row.put("total", null);
            row.put("length_counts", null);
            if (e.getValue().isPresent()) {
                Map<String, Integer> m = e.getValue().get();
                Map<Integer, Integer> tally = new HashMap<>();
                Map<Integer, Integer> byLength = new HashMap<>();
                for (Map.Entry<String, Integer> w : m.entrySet()) {
                    tally.merge(w.getValue(), 1, Integer::sum);
                    if (w.getValue() >= LENGTH_STATS_MIN_SCORE) {
                        byLength.merge(w.getKey().codePointCount(0, w.getKey().length()), 1, Integer::sum);
                    }
                }
                List<Object> counts = new ArrayList<>();
                for (int s : scores) counts.add(tally.getOrDefault(s, 0));
                List<Object> lengthCounts = new ArrayList<>();
                for (int n : lengths) lengthCounts.add(byLength.getOrDefault(n, 0));
                row.put("counts", counts);
                row.put("total", m.size());
                row.put("length_counts", lengthCounts);
            }
            Map<String, Map<Integer, Integer>> levelsOf = lexiconCounts.get(e.getKey());
            List<Object> levelTotals = null;
            if (levelsOf != null) {
                levelTotals = new ArrayList<>();
                for (String d : levels) {
                    int total = 0;
                    for (int n : levelsOf.get(d).values()) total += n;
                    levelTotals.add(total);
                }
            }
            row.put("level_totals", levelTotals);
            List<Object> easyLengthCounts = null;
            if (levelsOf != null && levelsOf.containsKey("easy")) {
                easyLengthCounts = new ArrayList<>();
                for (int n : lengths) easyLengthCounts.add(levelsOf.get("easy").getOrDefault(n, 0));
            }
            row.put("easy_length_counts", easyLengthCounts);
            rows.add(row);
        }
        return Json.obj("scores", scores, "levels", levels, "languages", rows, "lengths", lengths);
    }
}
