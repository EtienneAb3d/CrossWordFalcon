package falcon;

import java.io.IOException;
import java.io.Writer;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.time.LocalDateTime;
import java.time.format.DateTimeFormatter;
import java.util.ArrayList;
import java.util.HashMap;
import java.util.HashSet;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Locale;
import java.util.Map;
import java.util.Set;
import java.util.concurrent.atomic.AtomicBoolean;
import java.util.regex.Matcher;
import java.util.regex.Pattern;

/** Theme glossary construction and the Qdrant-backed "Thématique" /
 * "Synonymes" searches (mirrors the matching parts of backend/app.py). */
public final class Themes {
    private Themes() {}

    public static final int THEME_LENGTH_MIN = 3;
    public static final int THEME_LENGTH_MAX = 15;
    public static final int THEME_LENGTH_SEARCH_PAGE = 1000;
    public static final double THEME_MIN_SCORE = 0.78;
    public static final double WHOLE_THEME_REJECT_LENIENCY = 1.5;
    public static final int THEME_MIN_KEYWORDS = 300;
    public static final int THEME_KEYWORD_LLM_MAX_LOOPS = 3;
    public static final double THEME_KEYWORD_LLM_TEMPERATURE = 0.9;
    public static final double SIMILAR_TIMEOUT_S = 10.0;
    public static final double SIMILAR_DESCRIBE_TIMEOUT_S = 45.0;
    public static final Path THEME_LOG_DIR = Env.path("LOG_THEME");

    public static final QdrantStore QDRANT = new QdrantStore(SIMILAR_TIMEOUT_S);
    public static final Embedder EMBEDDER = new Embedder(null, null, null, SIMILAR_TIMEOUT_S);

    static final Pattern THEME_TOKEN_RE = Pattern.compile("[^\\W\\d_]+(?:['’\\-][^\\W\\d_]+)*", Pattern.UNICODE_CHARACTER_CLASS);
    static final Map<String, Set<String>> STOPWORDS_RAW = Map.of(
            "fr", Set.of("le", "la", "les", "de", "des", "du", "un", "une", "et", "que", "qui", "est", "dans", "pour", "avec",
                    "sur", "ce", "cette", "cela", "ne", "pas", "plus", "vous", "nous", "elle", "il", "être", "leur", "alors",
                    "aussi", "mais", "donc", "était", "sont"),
            "en", Set.of("the", "a", "an", "is", "are", "of", "and", "to", "that", "in", "let", "me", "also", "make", "sure",
                    "they", "their", "each", "with", "all", "good", "you", "we", "was", "were", "your", "but", "then"),
            "de", Set.of("der", "die", "das", "und", "ist", "ein", "eine", "mit", "für", "auf", "nicht", "zu", "von", "im",
                    "den", "dem", "sie", "wir", "sind", "war", "aber", "dann", "auch"),
            "es", Set.of("el", "la", "los", "las", "un", "una", "que", "es", "y", "para", "con", "en", "no", "por", "más",
                    "usted", "nosotros", "ella", "está", "son", "era", "también", "pero", "entonces"),
            "it", Set.of("il", "lo", "la", "gli", "le", "di", "un", "una", "che", "è", "e", "per", "con", "non", "in", "del",
                    "della", "voi", "noi", "lei", "sono", "anche", "però", "quindi", "questo"),
            "pt", Set.of("o", "os", "um", "uma", "que", "de", "do", "da", "dos", "das", "em", "no", "na", "com", "não", "para",
                    "por", "mais", "você", "está", "são", "também", "então", "mas", "isso", "seu", "sua"));

    static List<String> findAll(Pattern p, String text) {
        List<String> out = new ArrayList<>();
        Matcher m = p.matcher(text == null ? "" : text);
        while (m.find()) out.add(m.group());
        return out;
    }

    public static List<String> splitKeywords(String text, String lang) {
        List<String> pieces = new ArrayList<>();
        for (String piece : (text == null ? "" : text).split("[,;\\n]+")) {
            String kw = Py.strip(piece);
            int a = 0, b = kw.length();
            while (a < b && kw.charAt(a) == '.') a++;
            while (b > a && kw.charAt(b - 1) == '.') b--;
            kw = Py.strip(kw.substring(a, b));
            if (kw.length() >= 2) pieces.add(kw);
        }
        if (pieces.size() <= 1) pieces = findAll(THEME_TOKEN_RE, text);
        Set<String> stop = lang == null ? null : STOPWORDS_RAW.get(lang);
        if (stop != null) {
            List<String> kept = new ArrayList<>();
            for (String kw : pieces) if (!stop.contains(kw.toLowerCase(Locale.ROOT))) kept.add(kw);
            pieces = kept;
        }
        return pieces;
    }

    public static List<String> themeTokens(String theme) {
        Set<String> seen = new HashSet<>();
        List<String> out = new ArrayList<>();
        for (String tok : findAll(THEME_TOKEN_RE, theme)) {
            if (tok.length() < 2) continue;
            if (!seen.add(tok.toLowerCase(Locale.ROOT))) continue;
            out.add(tok);
        }
        return out;
    }

    /** (word, score) — score may be null. */
    public record Scored(String word, Double score) {}

    public record ScoredKw(String word, Double score, String keyword) {}

    static List<Scored> iterScoredWords(double[] vec, String lang, double minScore) {
        List<Scored> out = new ArrayList<>();
        Set<String> seen = new HashSet<>();
        int offset = 0;
        while (true) {
            List<Object> hits = QDRANT.search(vec, lang, THEME_LENGTH_SEARCH_PAGE, offset);
            if (hits.isEmpty()) return out;
            for (Object hit : hits) {
                Object sc = Json.get(hit, "score");
                Double score = sc instanceof Number n ? n.doubleValue() : null;
                if (score != null && score < minScore) return out;
                String word = Json.str(Json.get(hit, "payload"), "word", null);
                if (word != null && !word.isEmpty() && seen.add(word)) out.add(new Scored(word, score));
            }
            offset += hits.size();
            if (hits.size() < THEME_LENGTH_SEARCH_PAGE) return out;
        }
    }

    static boolean isStoreError(RuntimeException e) {
        return e instanceof QdrantStore.QdrantStoreError || e instanceof Embedder.EmbedderError;
    }

    public static List<Scored> compiledSimilarWords(List<String> keywords, String lang, double minScore) {
        Map<String, Double> merged = new LinkedHashMap<>();
        boolean anyOk = false;
        for (String kw : keywords) {
            List<Scored> pairs;
            try {
                pairs = iterScoredWords(EMBEDDER.embed(kw), lang, minScore);
            } catch (RuntimeException e) {
                if (!isStoreError(e) || !anyOk) throw e;
                continue;
            }
            anyOk = true;
            for (Scored s : pairs) {
                if (!merged.containsKey(s.word())) merged.put(s.word(), s.score());
                else if (s.score() != null && (merged.get(s.word()) == null || s.score() > merged.get(s.word()))) merged.put(s.word(), s.score());
            }
        }
        List<Scored> out = new ArrayList<>();
        merged.forEach((w, s) -> out.add(new Scored(w, s)));
        out.sort((a, b) -> Double.compare(b.score() == null ? 0.0 : b.score(), a.score() == null ? 0.0 : a.score()));
        return out;
    }

    /** Returns {keywords, scored}. */
    public static Object[] similarWordsImpl(Clues clueGen, String query, String lang, double minScore) {
        String desc;
        try {
            desc = clueGen.describeTheme(query, lang, SIMILAR_DESCRIBE_TIMEOUT_S, null, 0.7, false);
        } catch (RuntimeException e) {
            Log.warning("similar_words: theme expansion failed (%s) — searching the raw query", e.getMessage());
            desc = "";
        }
        List<String> keywords = new ArrayList<>();
        Set<String> seen = new HashSet<>();
        for (String kw : splitKeywords(desc, lang)) if (seen.add(kw.toLowerCase(Locale.ROOT))) keywords.add(kw);
        if (keywords.isEmpty()) keywords.add(query);
        Log.info("similar_words: %s -> %d keywords (min_score=%s)", Log.repr(query), keywords.size(), minScore);
        return new Object[]{keywords, compiledSimilarWords(keywords, lang, minScore)};
    }

    static List<Scored> themeWordsByLength(String query, String lang, double minScore) {
        double[] vec = EMBEDDER.embed(query);
        List<Scored> words = new ArrayList<>();
        for (Scored s : iterScoredWords(vec, lang, minScore)) {
            int len = s.word().length();
            if (len >= THEME_LENGTH_MIN && len <= THEME_LENGTH_MAX) words.add(s);
        }
        words.sort((a, b) -> Integer.compare(a.word().length(), b.word().length()));
        return words;
    }

    static List<ScoredKw> compiledThemeWordsByLength(List<String> keywords, String lang, double minScore) {
        Map<String, ScoredKw> merged = new LinkedHashMap<>();
        boolean anyOk = false;
        for (String kw : keywords) {
            List<Scored> pairs;
            try {
                pairs = themeWordsByLength(kw, lang, minScore);
            } catch (RuntimeException e) {
                if (!isStoreError(e) || !anyOk) throw e;
                continue;
            }
            anyOk = true;
            for (Scored s : pairs) {
                ScoredKw cur = merged.get(s.word());
                if (cur == null) merged.put(s.word(), new ScoredKw(s.word(), s.score(), kw));
                else if (s.score() != null && (cur.score() == null || s.score() > cur.score())) {
                    merged.put(s.word(), new ScoredKw(s.word(), s.score(), kw));
                }
            }
        }
        List<ScoredKw> out = new ArrayList<>(merged.values());
        out.sort((a, b) -> {
            int c = Integer.compare(a.word().length(), b.word().length());
            if (c != 0) return c;
            return Double.compare(b.score() == null ? 0.0 : b.score(), a.score() == null ? 0.0 : a.score());
        });
        return out;
    }

    static Map<String, Double> wholeThemeProximityScores(List<String> words, String themeText, String lang) {
        List<String> themeWords = themeTokens(themeText);
        if (themeWords.isEmpty() && themeText != null && !themeText.isEmpty()) themeWords = List.of(themeText);
        Map<String, Double> out = new HashMap<>();
        if (words.isEmpty() || themeWords.isEmpty()) return out;
        List<double[]> themeVecs = new ArrayList<>();
        for (String tok : themeWords) themeVecs.add(EMBEDDER.embed(tok));
        Map<String, double[]> wordVecs = QDRANT.retrieveWordVectors(lang, words, 2000);
        wordVecs.forEach((w, vec) -> {
            double best = Double.NEGATIVE_INFINITY;
            for (double[] tv : themeVecs) {
                double dot = 0;
                for (int i = 0; i < Math.min(vec.length, tv.length); i++) dot += vec[i] * tv[i];
                best = Math.max(best, dot);
            }
            out.put(w, best);
        });
        return out;
    }

    static void writeThemeLog(String shortId, String theme, String description, List<Object[]> words, String language,
                              List<Object[]> keywordLists, List<String> searched, Double minScore, Double rejectThreshold) {
        try {
            Files.createDirectories(THEME_LOG_DIR);
            LocalDateTime now = LocalDateTime.now();
            Path path = THEME_LOG_DIR.resolve(now.format(DateTimeFormatter.ofPattern("yyyyMMdd-HHmmss-SSSSSS")) + "_" + shortId + ".log");
            try (Writer w = Files.newBufferedWriter(path, StandardCharsets.UTF_8)) {
                String first = description != null && !description.isEmpty() ? description : theme;
                w.write(Py.strip(first) + "\n");
                w.write("\n# generated " + now.format(DateTimeFormatter.ofPattern("yyyy-MM-dd HH:mm:ss")) + "\n");
                if (language != null) w.write("# language: " + language + "\n");
                w.write("# theme (as typed): " + theme + "\n");
                if (minScore != null) w.write("# score threshold (theme_precision): " + minScore + "\n");
                if (rejectThreshold != null) w.write("# whole-theme reject threshold: " + rejectThreshold + "\n");
                if (keywordLists != null && !keywordLists.isEmpty()) {
                    w.write("# " + keywordLists.size() + " keyword list(s) from the LLM:\n");
                    for (Object[] kl : keywordLists) {
                        String tag = kl[0] == null ? "(whole theme)" : (String) kl[0];
                        @SuppressWarnings("unchecked")
                        List<String> kws = (List<String>) kl[1];
                        w.write("#   [" + tag + "] " + String.join(", ", kws) + "\n");
                    }
                }
                if (searched != null && !searched.isEmpty()) {
                    w.write("# " + searched.size() + " distinct keywords searched in Qdrant: " + String.join(", ", searched) + "\n");
                }
                w.write("# preselected words: " + words.size() + "\n");
                if (!words.isEmpty()) {
                    w.write("\n");
                    List<String> lines = new ArrayList<>();
                    for (Object[] e : words) {
                        String word = (String) e[0];
                        Double score = (Double) e[1];
                        String kw = (String) e[2];
                        Double whole = (Double) e[3];
                        String wt = whole == null ? "" : Py.fmt(whole, 4);
                        lines.add(word + "\t" + word.length() + "\t" + (score == null ? "" : Py.fmt(score, 4))
                                + "\t" + kw + "\t" + wt);
                    }
                    w.write(String.join("\n", lines) + "\n");
                }
            }
        } catch (IOException e) {
            Log.warning("failed to write LOG_THEME journal: %s", e.getMessage());
        }
    }

    /** Returns {priority words (List<String> or null), theme description}. */
    public static Object[] buildThemeGlossary(String theme, String language, double themePrecision, String shortId,
                                              AtomicBoolean cancel, String logTag, String themeLanguage, Clues clueGen) {
        boolean verify = themeLanguage != null && !themeLanguage.equals(language);
        List<String> priority = null;
        String description;
        try {
            description = clueGen.describeTheme(theme, language, Clues.DEFAULT_TIMEOUT, cancel, THEME_KEYWORD_LLM_TEMPERATURE, verify);
        } catch (GenerationCancelled e) {
            throw e;
        } catch (RuntimeException e) {
            Log.warning("[%s] theme description failed (%s) — searching the raw theme words instead", logTag, e.getMessage());
            description = "";
        }
        Log.info("[%s] theme %s -> description %s", logTag, Log.repr(theme), Log.repr(description));
        List<Object[]> keywordLists = new ArrayList<>();
        List<String> first = splitKeywords(description, language);
        if (first.isEmpty()) first = themeTokens(theme);
        if (first.isEmpty()) first = List.of(theme);
        keywordLists.add(new Object[]{null, first});
        List<String> tokens = themeTokens(theme);
        if (tokens.size() > 1) {
            for (String tok : tokens) {
                String td;
                try {
                    td = clueGen.describeTheme(tok, language, Clues.DEFAULT_TIMEOUT, cancel, THEME_KEYWORD_LLM_TEMPERATURE, verify);
                } catch (GenerationCancelled e) {
                    throw e;
                } catch (RuntimeException e) {
                    Log.warning("[%s] theme word %s description failed (%s) — searching the bare word", logTag, Log.repr(tok), e.getMessage());
                    td = "";
                }
                List<String> kws = splitKeywords(td, language);
                keywordLists.add(new Object[]{tok, kws.isEmpty() ? List.of(tok) : kws});
            }
            Log.info("[%s] theme has %d words -> %d keyword lists", logTag, tokens.size(), keywordLists.size());
        }
        List<String> searched = new ArrayList<>();
        Set<String> seenKw = new HashSet<>();
        java.util.function.Function<List<String>, Integer> addKeywords = kws -> {
            int added = 0;
            for (String kw : kws) if (seenKw.add(kw.toLowerCase(Locale.ROOT))) {
                searched.add(kw);
                added++;
            }
            return added;
        };
        for (Object[] kl : keywordLists) {
            @SuppressWarnings("unchecked")
            List<String> kws = (List<String>) kl[1];
            addKeywords.apply(kws);
        }
        for (int loop = 1; loop <= THEME_KEYWORD_LLM_MAX_LOOPS; loop++) {
            if (searched.size() >= THEME_MIN_KEYWORDS) break;
            String more;
            try {
                more = clueGen.describeTheme(theme, language, Clues.DEFAULT_TIMEOUT, cancel, THEME_KEYWORD_LLM_TEMPERATURE, verify);
            } catch (GenerationCancelled e) {
                throw e;
            } catch (RuntimeException e) {
                Log.warning("[%s] theme keyword top-up %d failed (%s)", logTag, loop, e.getMessage());
                break;
            }
            List<String> kwMore = splitKeywords(more, language);
            int added = addKeywords.apply(kwMore);
            keywordLists.add(new Object[]{"(top-up " + loop + ")", kwMore});
            Log.info("[%s] theme keyword top-up %d -> +%d (%d total)", logTag, loop, added, searched.size());
            if (added == 0) break;
        }
        Log.info("[%s] theme -> %d distinct keywords to search in Qdrant (min_score=%s)", logTag, searched.size(), themePrecision);
        List<ScoredKw> scored = new ArrayList<>();
        try {
            scored = compiledThemeWordsByLength(searched, language, themePrecision);
            Log.info("[%s] theme -> %d preselected words (before whole-theme filter)", logTag, scored.size());
        } catch (RuntimeException e) {
            if (!isStoreError(e)) throw e;
            Log.warning("[%s] theme pre-search unavailable (%s) — generating without a theme", logTag, e.getMessage());
        }
        Map<String, Double> whole = new HashMap<>();
        Double rejectThreshold = null;
        if (!scored.isEmpty()) {
            try {
                List<String> ws = new ArrayList<>();
                for (ScoredKw s : scored) ws.add(s.word());
                whole = wholeThemeProximityScores(ws, theme, language);
                double thr = Math.max(0.0, 1 - (1 - themePrecision) * WHOLE_THEME_REJECT_LENIENCY);
                rejectThreshold = thr;
                List<ScoredKw> kept = new ArrayList<>();
                for (ScoredKw s : scored) if (whole.getOrDefault(s.word(), 1.0) >= thr) kept.add(s);
                scored = kept;
                Log.info("[%s] theme -> %d preselected words after whole-theme filter (reject threshold=%.4f)", logTag, scored.size(), thr);
            } catch (RuntimeException e) {
                if (!isStoreError(e)) throw e;
                Log.warning("[%s] whole-theme proximity scoring unavailable (%s) — LOG_THEME column left blank, no whole-theme filtering applied", logTag, e.getMessage());
            }
            priority = new ArrayList<>();
            for (ScoredKw s : scored) priority.add(s.word());
        }
        List<Object[]> logWords = new ArrayList<>();
        for (ScoredKw s : scored) logWords.add(new Object[]{s.word(), s.score(), s.keyword(), whole.get(s.word())});
        writeThemeLog(logTag, theme, description, logWords, language, keywordLists, searched, themePrecision, rejectThreshold);
        return new Object[]{priority, description};
    }
}
