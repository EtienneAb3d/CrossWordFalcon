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
    /** Minimum size of a compiled theme glossary: while it holds fewer words, the score
     *  threshold is lowered by THEME_PRECISION_STEP and the search re-run, down to 0. */
    public static final int THEME_MIN_GLOSSARY_WORDS = 1000;
    public static final double THEME_PRECISION_STEP = 0.03;

    static double loweredThemePrecision(double precision) {
        return Math.max(0.0, Math.floor((precision - THEME_PRECISION_STEP) * 10000 + 0.5) / 10000);
    }
    public static final int THEME_KEYWORD_LLM_MAX_LOOPS = 3;
    public static final double THEME_KEYWORD_LLM_TEMPERATURE = 0.9;
    public static final double SIMILAR_TIMEOUT_S = 10.0;
    public static final double SIMILAR_DESCRIBE_TIMEOUT_S = 45.0;
    public static final Path THEME_LOG_DIR = Env.path("LOG_THEME");

    public static final QdrantStore QDRANT = new QdrantStore(SIMILAR_TIMEOUT_S);
    /** The multi-word expressions' collection ("words_multi", English and French only),
     *  searched next to QDRANT by every similarity search (multiScoredPoints). */
    public static final QdrantStore QDRANT_MULTI = new QdrantStore(SIMILAR_TIMEOUT_S, QdrantStore.multiCollectionName());
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

    /** A word or expression: its grid form, score and every accented spelling. */
    public record ScoredSpellings(String word, Double score, List<String> spellings) {}

    /** {words, expressions} of compiledSimilarWords. */
    public record Similar(List<ScoredSpellings> words, List<ScoredSpellings> expressions) {}

    /** (word, score, payload) hits of `store` (backend/app.py's _iter_scored_points). */
    record Point(String word, Double score, Object payload) {}

    static List<Point> iterScoredPoints(double[] vec, String lang, double minScore, QdrantStore store) {
        List<Point> out = new ArrayList<>();
        Set<String> seen = new HashSet<>();
        int offset = 0;
        while (true) {
            List<Object> hits = store.search(vec, lang, THEME_LENGTH_SEARCH_PAGE, offset);
            if (hits.isEmpty()) return out;
            for (Object hit : hits) {
                Object sc = Json.get(hit, "score");
                Double score = sc instanceof Number n ? n.doubleValue() : null;
                if (score != null && score < minScore) return out;
                Object payload = Json.get(hit, "payload");
                String word = Json.str(payload, "word", null);
                if (word != null && !word.isEmpty() && seen.add(word)) out.add(new Point(word, score, payload));
            }
            offset += hits.size();
            if (hits.size() < THEME_LENGTH_SEARCH_PAGE) return out;
        }
    }

    static List<Scored> iterScoredWords(double[] vec, String lang, double minScore) {
        List<Scored> out = new ArrayList<>();
        for (Point p : iterScoredPoints(vec, lang, minScore, QDRANT)) out.add(new Scored(p.word(), p.score()));
        return out;
    }

    /** The "words_multi" hits of `vec`; empty when that collection or the language's tenant
     *  is missing or does not answer (backend/app.py's _multi_scored_points). */
    static List<Point> multiScoredPoints(double[] vec, String lang, double minScore) {
        try {
            return iterScoredPoints(vec, lang, minScore, QDRANT_MULTI);
        } catch (QdrantStore.QdrantStoreError e) {
            return List.of();
        }
    }

    /** Every `;`-separated spelling of a point's `accented` (main one first), else its
     *  word (backend/app.py's _spellings). */
    static List<String> spellings(Object payload, String word) {
        String acc = Json.str(payload, "accented", "");
        List<String> out = new ArrayList<>();
        for (String a : (acc == null ? "" : acc).split(";", -1)) {
            String sp = Py.strip(a);
            if (!sp.isEmpty()) out.add(sp);
        }
        return out.isEmpty() ? List.of(word) : out;
    }

    static boolean isStoreError(RuntimeException e) {
        return e instanceof QdrantStore.QdrantStoreError || e instanceof Embedder.EmbedderError;
    }

    public static Similar compiledSimilarWords(List<String> keywords, String lang, double minScore) {
        Map<String, ScoredSpellings> merged = new LinkedHashMap<>();
        Map<String, ScoredSpellings> mergedMulti = new LinkedHashMap<>();
        boolean anyOk = false;
        for (String kw : keywords) {
            double[] vec;
            List<Point> points;
            try {
                vec = EMBEDDER.embed(kw);
                points = iterScoredPoints(vec, lang, minScore, QDRANT);
            } catch (RuntimeException e) {
                if (!isStoreError(e) || !anyOk) throw e;
                continue;
            }
            anyOk = true;
            for (Point p : points) keepBest(merged, p);
            for (Point p : multiScoredPoints(vec, lang, minScore)) keepBest(mergedMulti, p);
        }
        return new Similar(ranked(merged), ranked(mergedMulti));
    }

    private static void keepBest(Map<String, ScoredSpellings> into, Point p) {
        ScoredSpellings best = into.get(p.word());
        if (best == null || (p.score() != null && (best.score() == null || p.score() > best.score()))) {
            into.put(p.word(), new ScoredSpellings(p.word(), p.score(), spellings(p.payload(), p.word())));
        }
    }

    private static List<ScoredSpellings> ranked(Map<String, ScoredSpellings> entries) {
        List<ScoredSpellings> out = new ArrayList<>(entries.values());
        out.sort((a, b) -> Double.compare(b.score() == null ? 0.0 : b.score(), a.score() == null ? 0.0 : a.score()));
        return out;
    }

    /** Returns {keywords, Similar}. */
    public static Object[] similarWordsImpl(Clues clueGen, String query, String lang, double minScore) {
        String desc;
        long llmStarted = System.nanoTime();
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
        Log.info("similar_words: %s -> %d keywords (min_score=%s, LLM expansion %.1fs)", Log.repr(query), keywords.size(),
                minScore, (System.nanoTime() - llmStarted) / 1e9);
        return new Object[]{keywords, compiledSimilarWords(keywords, lang, minScore)};
    }

    static List<Scored> themeWordsByLength(String query, String lang, double minScore) {
        double[] vec = EMBEDDER.embed(query);
        List<Scored> hits = new ArrayList<>(iterScoredWords(vec, lang, minScore));
        for (Point p : multiScoredPoints(vec, lang, minScore)) hits.add(new Scored(p.word(), p.score()));
        Map<String, Double> best = new LinkedHashMap<>();
        for (Scored s : hits) {
            int len = s.word().length();
            if (len < THEME_LENGTH_MIN || len > THEME_LENGTH_MAX) continue;
            if (!best.containsKey(s.word())
                    || (s.score() != null && (best.get(s.word()) == null || s.score() > best.get(s.word())))) {
                best.put(s.word(), s.score());
            }
        }
        List<Scored> words = new ArrayList<>();
        best.forEach((w, s) -> words.add(new Scored(w, s)));
        words.sort((a, b) -> {
            int c = Integer.compare(a.word().length(), b.word().length());
            if (c != 0) return c;
            return Double.compare(b.score() == null ? 0.0 : b.score(), a.score() == null ? 0.0 : a.score());
        });
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
        List<String> missing = new ArrayList<>();
        for (String w : words) if (!wordVecs.containsKey(w)) missing.add(w);
        if (!missing.isEmpty()) {
            // A multi-word expression's vector lives in the "words_multi" collection.
            try {
                wordVecs.putAll(QDRANT_MULTI.retrieveWordVectors(lang, missing, 2000));
            } catch (QdrantStore.QdrantStoreError e) {
                // no expression vector
            }
        }
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
                              List<Object[]> keywordLists, List<String> searched, Double minScore, Double rejectThreshold,
                              Double requestedMinScore) {
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
                if (requestedMinScore != null && !requestedMinScore.equals(minScore))
                    w.write("# requested score threshold: " + requestedMinScore + "\n");
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
        Map<String, Double> whole = new HashMap<>();
        Double rejectThreshold = null;
        double requestedPrecision = themePrecision;
        while (true) {
            if (cancel != null && cancel.get()) throw new GenerationCancelled();
            try {
                scored = compiledThemeWordsByLength(searched, language, themePrecision);
                Log.info("[%s] theme -> %d preselected words (before whole-theme filter)", logTag, scored.size());
            } catch (RuntimeException e) {
                if (!isStoreError(e)) throw e;
                Log.warning("[%s] theme pre-search unavailable (%s) — generating without a theme", logTag, e.getMessage());
                scored = new ArrayList<>();
                priority = null;
                break;
            }
            whole = new HashMap<>();
            rejectThreshold = null;
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
            if (scored.size() >= THEME_MIN_GLOSSARY_WORDS || themePrecision <= 0.0) break;
            themePrecision = loweredThemePrecision(themePrecision);
            Log.info("[%s] theme -> %d words < %d, score threshold lowered to %s", logTag, scored.size(), THEME_MIN_GLOSSARY_WORDS, themePrecision);
        }
        List<Object[]> logWords = new ArrayList<>();
        for (ScoredKw s : scored) logWords.add(new Object[]{s.word(), s.score(), s.keyword(), whole.get(s.word())});
        writeThemeLog(logTag, theme, description, logWords, language, keywordLists, searched, themePrecision, rejectThreshold, requestedPrecision);
        return new Object[]{priority, description};
    }
}
