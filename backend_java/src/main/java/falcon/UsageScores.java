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
 * Scrabble wordlist, data/usage_score_&lt;lang&gt;_scrabble_out.tsv, for the multi-word expressions
 * data/usage_score_&lt;lang&gt;_multi.tsv) for the
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

    private static final Map<String, Optional<Map<String, Integer>>> MULTI_CACHE = new ConcurrentHashMap<>();

    /** {MOT: score} of data/usage_score_&lt;lang&gt;_multi.tsv, empty when the file does not exist (mirrors
     * _load_multi). */
    private static Optional<Map<String, Integer>> loadMulti(String language) {
        return MULTI_CACHE.computeIfAbsent(language, lang -> {
            Path path = DIR.resolve("usage_score_" + lang + "_multi.tsv");
            if (!Files.exists(path)) return Optional.empty();
            Map<String, Integer> scores = new HashMap<>();
            try (BufferedReader r = Files.newBufferedReader(path, StandardCharsets.UTF_8)) {
                String line;
                while ((line = r.readLine()) != null) {
                    String[] parts = line.split("\t", -1);
                    if (parts.length >= 3 && !parts[2].isEmpty() && parts[2].chars().allMatch(Character::isDigit)) {
                        scores.put(parts[0].toUpperCase(Locale.ROOT), Integer.parseInt(parts[2]));
                    }
                }
            } catch (IOException e) {
                return Optional.empty();
            }
            return Optional.of(scores);
        });
    }

    /** The usage score of the grid form {@code mot}, null when unknown: from the single-word files,
     * else the multi-word one (loadMulti) — the multi-word one first when {@code multi} (a spelling
     * holding a space, hyphen or apostrophe). */
    public static Integer usageScore(String mot, String language, boolean multi) {
        String key = mot.toUpperCase(Locale.ROOT);
        List<Optional<Map<String, Integer>>> sources = multi
                ? List.of(loadMulti(language), load(language))
                : List.of(load(language), loadMulti(language));
        for (Optional<Map<String, Integer>> scores : sources) {
            if (scores.isPresent() && scores.get().containsKey(key)) return scores.get().get(key);
        }
        return null;
    }

    /**
     * Number of rated words per usage score, for the localhost-only "Statistiques" panel
     * (GET /api/usage_stats), over both files of each language, plus the number of words
     * rated at least LENGTH_STATS_MIN_SCORE per word length, longest first, and, from
     * {@code lexiconCounts} ({language: {difficulty: {word length: number of words}}}), each
     * difficulty's lexicon total and the "easy" lexicon's words per length; per language, the multi-word
     * expressions' multi_total / multi_rated / multi_rated_min / multi_level_totals, from {@code multiCounts}
     * ({language: {"total": n, "levels": {difficulty: n}}}) and usage_score_&lt;lang&gt;_multi.tsv (mirrors
     * usage_scores.usage_score_counts).
     */
    @SuppressWarnings("unchecked")
    public static Map<String, Object> usageScoreCounts(List<String> languages,
                                                       Map<String, Map<String, Map<Integer, Integer>>> lexiconCounts,
                                                       Map<String, Map<String, Object>> multiCounts) {
        if (multiCounts == null) multiCounts = Map.of();
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
        Map<String, Optional<Map<String, Integer>>> multiPerLanguage = new LinkedHashMap<>();
        for (String language : languages) multiPerLanguage.put(language, loadMulti(language));
        for (Optional<Map<String, Integer>> m : multiPerLanguage.values()) {
            m.ifPresent(x -> x.forEach((w, s) -> {
                if (s >= LENGTH_STATS_MIN_SCORE) lengthSet.add(w.codePointCount(0, w.length()));
            }));
        }
        for (Map<String, Object> c : multiCounts.values()) {
            Map<Integer, Integer> easy = ((Map<String, Map<Integer, Integer>>) c.get("levels")).get("easy");
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
            Map<String, Object> multiOf = multiCounts.get(e.getKey());
            Optional<Map<String, Integer>> multiScores = multiPerLanguage.get(e.getKey());
            row.put("multi_total", multiOf == null ? null : multiOf.get("total"));
            row.put("multi_rated", multiScores.map(m -> (Object) m.size()).orElse(null));
            row.put("multi_rated_min", multiScores.map(m -> (Object) (int) m.values().stream()
                    .filter(s -> s >= LENGTH_STATS_MIN_SCORE).count()).orElse(null));
            Map<String, Map<Integer, Integer>> byLevel = multiOf == null ? Map.of()
                    : (Map<String, Map<Integer, Integer>>) multiOf.get("levels");
            List<Object> multiLevels = null;
            if (multiOf != null) {
                multiLevels = new ArrayList<>();
                for (String d : levels) {
                    Map<Integer, Integer> c = byLevel.get(d);
                    multiLevels.add(c == null ? null : c.values().stream().mapToInt(Integer::intValue).sum());
                }
            }
            row.put("multi_level_totals", multiLevels);
            List<Object> multiLengthCounts = null;
            if (multiScores.isPresent()) {
                Map<Integer, Integer> byLength = new HashMap<>();
                multiScores.get().forEach((w, s) -> {
                    if (s >= LENGTH_STATS_MIN_SCORE) byLength.merge(w.codePointCount(0, w.length()), 1, Integer::sum);
                });
                multiLengthCounts = new ArrayList<>();
                for (int n : lengths) multiLengthCounts.add(byLength.getOrDefault(n, 0));
            }
            row.put("multi_length_counts", multiLengthCounts);
            Map<Integer, Integer> multiEasy = byLevel.get("easy");
            List<Object> multiEasyLengthCounts = null;
            if (multiEasy != null) {
                multiEasyLengthCounts = new ArrayList<>();
                for (int n : lengths) multiEasyLengthCounts.add(multiEasy.getOrDefault(n, 0));
            }
            row.put("multi_easy_length_counts", multiEasyLengthCounts);
            rows.add(row);
        }
        return Json.obj("scores", scores, "levels", levels, "languages", rows, "lengths", lengths);
    }
}
