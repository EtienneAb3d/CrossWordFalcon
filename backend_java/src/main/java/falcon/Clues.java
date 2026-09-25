package falcon;

import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.text.Normalizer;
import java.time.LocalDateTime;
import java.time.format.DateTimeFormatter;
import java.util.ArrayList;
import java.util.Collection;
import java.util.Collections;
import java.util.HashMap;
import java.util.HashSet;
import java.util.LinkedHashMap;
import java.util.LinkedHashSet;
import java.util.List;
import java.util.Locale;
import java.util.Map;
import java.util.Set;
import java.util.TreeSet;
import java.util.concurrent.ConcurrentHashMap;
import java.util.concurrent.ExecutionException;
import java.util.concurrent.ExecutorCompletionService;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;
import java.util.concurrent.Future;
import java.util.concurrent.ThreadLocalRandom;
import java.util.concurrent.atomic.AtomicBoolean;
import java.util.function.BooleanSupplier;
import java.util.regex.Matcher;
import java.util.regex.Pattern;

/**
 * Crossword clue/title/definition/paraphrase/theme generation through an
 * OpenAI-compatible chat-completions endpoint (mirrors backend/clues.py,
 * {@code LLMClueGenerator}). Prompts are kept word for word identical to
 * the Python version.
 */
public final class Clues {

    public static final String DEFAULT_LLM_BASE_URL = "http://127.0.0.1:3002/v1/chat/completions";
    public static final String DEFAULT_LLM_MODEL = "Qwen/Qwen3.5-9B";
    public static final String DEFAULT_LLM_API_KEY = "EMPTY";
    public static final double DEFAULT_TIMEOUT = 300.0;
    public static final int REASONING_TOKEN_BUDGET = 2048;
    public static final double TEMPERATURE = 0.4;
    public static final int MAX_CLUE_WORDS = 20;
    public static final int MAX_TITLE_WORDS = 3;
    public static final int MAX_TITLE_WORDS_ACCEPTED = 6;
    static final int TITLE_COUNT = 3;
    static final int TITLE_RETRIES = 3;
    public static final int DEFINE_RETRIES = 2;
    public static final int TITLE_PROPOSALS_COUNT = 10;
    public static final int TITLE_PROPOSALS_RETRIES = 2;
    public static final int THEME_DESCRIPTION_RETRIES = 2;
    public static final int RANDOM_THEME_RETRIES = 2;
    static final int BATCH_SIZE = 1;
    public static final int CLUE_BATCH_PARALLELISM = Math.max(1, Env.getInt("CLUE_BATCH_PARALLELISM", 10));

    private static final int RE_FLAGS = Pattern.CASE_INSENSITIVE | Pattern.UNICODE_CASE | Pattern.UNICODE_CHARACTER_CLASS;

    static final Pattern TITLE_QUOTES_RE = Pattern.compile("^['\"“”«»]+|['\"“”«»]+$");
    static final Pattern TITLE_LABEL_RE = Pattern.compile("^\\s*(?:title|titre|titel|título|titolo)\\s*:\\s*", RE_FLAGS);
    static final Pattern THEME_SUGGESTION_LABEL_RE = Pattern.compile("^\\s*(?:theme|th[eè]me|thema|tema)\\s*:\\s*", RE_FLAGS);
    static final Pattern TITLE_INTRO_RE = Pattern.compile(
            "^\\s*(?:"
                    + "bonjour|salut|hola|ciao|hallo|guten\\s+tag|"
                    + "je\\s+(?:propose|sugg[eè]re|dirais|choisis|pense\\s+[àa])|"
                    + "voici(?:\\s+(?:le|un|mon|les|mes|des)\\s+titres?)?|"
                    + "le\\s+titre\\s+(?:est|pourrait\\s+[êe]tre|serait)|"
                    + "les\\s+titres\\s+(?:sont|pourraient\\s+[êe]tre)|"
                    + "un\\s+titre\\s+possible|mon\\s+titre|mes\\s+titres|"
                    + "propongo|el\\s+t[íi]tulo\\s+(?:es|ser[íi]a)|aqu[íi]\\s+(?:est[áa]|tienes)|"
                    + "propongo\\s+il\\s+titolo|il\\s+titolo\\s+(?:[èe]|potrebbe\\s+essere)|ecco(?:\\s+il\\s+titolo)?|"
                    + "proponho|sugiro|o\\s+t[íi]tulo\\s+(?:[ée]|seria)|aqui\\s+est[áa](?:\\s+o\\s+t[íi]tulo)?|"
                    + "ich\\s+schlage\\s+vor|der\\s+titel\\s+(?:ist|lautet|k[öo]nnte)|hier\\s+ist(?:\\s+der\\s+titel)?|"
                    + "here\\s+is(?:\\s+(?:a|the|my)\\s+title)?|how\\s+about|i\\s+(?:propose|suggest)|my\\s+title|the\\s+title\\s+(?:is|would\\s+be)"
                    + ")(?:\\s*[:\\-–—,]\\s*|\\s+(?=[\"'“”«»]))", RE_FLAGS);
    static final Pattern TITLE_TRAILING_COMMENT_RE = Pattern.compile(
            "\\s*(?:"
                    + "[,;]\\s*(?:car|parce\\s+que|puisque|because|weil|denn|porque|perch[ée]|poich[ée])\\b"
                    + "|\\s+\\((?:en\\s+r[ée]f[ée]rence|r[ée]f[ée]rence|allusion|clin\\b|in\\s+reference|weil|porque)"
                    + ").*$", RE_FLAGS | Pattern.DOTALL);
    static final Pattern LEADING_MARKER_RE = Pattern.compile(
            "^\\s*(?:"
                    + "[-–—*•]"
                    + "|\\d+\\s*[.):]"
                    + "|c(?:lue|andidate)?\\s*[1-3]?\\s*[=:.)\\-–—]"
                    + "|c(?:lue|andidate)?\\s*[1-3]"
                    + ")\\s*", RE_FLAGS);
    static final Pattern ANALYSIS_LINE_RE = Pattern.compile("^\\s*(?:a\\s*\\d*|anal[iíy]\\w*|análi\\w*)\\s*[=:.)]", RE_FLAGS);
    static final Pattern LEADING_LABEL_RE = Pattern.compile("^\\s*(\\S+)\\s*[:,\\-–—]\\s*", Pattern.UNICODE_CHARACTER_CLASS);
    static final Map<String, Pattern> WORD_IS_A_PREFIX_RE = Map.of(
            "fr", Pattern.compile("^\\s*est\\s+(?:une?\\s+|l['’]\\s*|les?\\s+)?", RE_FLAGS),
            "en", Pattern.compile("^\\s*is\\s+(?:an?\\s+|the\\s+)?", RE_FLAGS),
            "de", Pattern.compile("^\\s*ist\\s+(?:ein(?:e)?\\s+|der\\s+|die\\s+|das\\s+)?", RE_FLAGS),
            "es", Pattern.compile("^\\s*es\\s+(?:una?\\s+|el\\s+|la\\s+)?", RE_FLAGS),
            "it", Pattern.compile("^\\s*è\\s+(?:un['’]?a?\\s+|il\\s+|lo\\s+|la\\s+)?", RE_FLAGS),
            "pt", Pattern.compile("^\\s*é\\s+(?:um(?:a)?\\s+|o\\s+|a\\s+)?", RE_FLAGS));
    static final Pattern HEAD_TOKEN_RE = Pattern.compile("^\\s*(\\S+)\\s*", Pattern.UNICODE_CHARACTER_CLASS);
    static final Pattern THINK_BLOCK_RE = Pattern.compile("^.*?</think>", Pattern.DOTALL);
    static final Pattern NON_LATIN_RE = Pattern.compile("[\\u0370-\\u1fff\\u3000-\\u9fff\\uac00-\\ud7ff\\uff00-\\uffef]");
    public static final Pattern WORD_TOKEN_RE = Pattern.compile("[^\\W\\d_]+", Pattern.UNICODE_CHARACTER_CLASS);
    static final String TITLE_ALLOWED_PUNCT = " ,'’-";

    static final Map<String, String> DIFFICULTY_STYLE = Map.of(
            "easy", "very easy: simple, literal, everyday vocabulary, no wordplay, no "
                    + "ambiguity — a clue a child could answer. THE MOST IMPORTANT "
                    + "THING at this level: when the word has more than one real "
                    + "meaning, always clue its SIMPLEST, most common, most everyday "
                    + "sense — the one an ordinary person thinks of first — and NEVER "
                    + "its rarest, most technical or most specialized sense. Reject "
                    + "outright any meaning that refers to a person's name, a city or "
                    + "other place name, a river or mountain, a brand or work title, a "
                    + "scientific/medical/legal/technical term, or anything that needs "
                    + "advanced general knowledge to recognize. Those senses exist only "
                    + "for medium/hard difficulty. If the only senses available are of "
                    + "that kind, fall back to the plainest possible description of the "
                    + "word rather than leaning into the technical or proper-noun one.",
            "medium", "medium: classic newspaper-crossword style — reworded and a "
                    + "little indirect, but still fair, no trick needed to get it.",
            "hard", "hard: elliptical and witty — puns, double meanings, misdirection, "
                    + "figurative or cultural references, expert-level grid style.");

    public static final Map<String, String> LANGUAGE_NAMES = Map.of(
            "fr", "French", "en", "English", "de", "German", "es", "Spanish", "it", "Italian", "pt", "Portuguese");

    static final Path PROMPT_CONFIG_DIR = Env.path("data");
    static final Path CALL_LOG_DIR = Env.path("LOG_LLM");
    private static final Map<String, Map<String, Object>> PROMPT_CONFIG_CACHE = new ConcurrentHashMap<>();

    static final Set<String> NOUN_POS = Set.of("noun", "name");
    static final Map<String, String> POS_LABELS = Map.ofEntries(
            Map.entry("noun", "noun"), Map.entry("name", "proper noun"), Map.entry("verb", "verb"),
            Map.entry("adj", "adjective"), Map.entry("adv", "adverb"), Map.entry("num", "numeral"),
            Map.entry("pron", "pronoun"), Map.entry("det", "determiner"), Map.entry("article", "article"),
            Map.entry("prep", "preposition"), Map.entry("postp", "postposition"), Map.entry("conj", "conjunction"),
            Map.entry("intj", "interjection"), Map.entry("particle", "particle"), Map.entry("phrase", "phrase"));
    static final Set<String> NOUN_PLURAL_LANGS = Set.of("fr", "en", "es", "it", "pt");

    static final Map<String, Set<String>> TITLE_HOLLOW_WORDS;
    static final Map<String, Set<String>> LANGUAGE_STOPWORDS;
    static final int WRONG_LANGUAGE_MIN_STOPWORDS = 2;
    static final double THEME_ECHO_OVERLAP_THRESHOLD = 0.7;

    static {
        Map<String, String> hollowRaw = Map.of(
                "fr", "le la les l un une de des du d au aux a et ou ni mais donc or car ce cet cette ces ca cela "
                        + "ceci celui celle ceux celles mon ma mes ton ta tes son sa ses notre nos votre vos leur leurs "
                        + "en y dans sur sous vers chez par pour avec sans entre contre selon ne pas plus si que qui dont "
                        + "quel quelle quels quelles zero deux trois quatre cinq six sept huit neuf dix",
                "en", "the a an of and or nor but so for yet to in on at by up as if no not with from into onto over "
                        + "under this that these those my your his her its our their one two three four five six seven "
                        + "eight nine ten",
                "de", "der die das den dem des ein eine einer eines einem einen und oder aber doch nicht kein keine "
                        + "dieser diese dieses jener jene mein dein sein ihr unser euer in an auf bei mit nach seit von "
                        + "zu aus zum zur im am eins zwei drei vier funf sechs sieben acht neun zehn",
                "es", "el la los las un una unos unas de del al a y o u ni pero sino que se lo le les este esta estos "
                        + "estas ese esa eso mi tu su nuestro vuestro en con sin por para sobre bajo entre hacia no mas "
                        + "muy uno dos tres cuatro cinco seis siete ocho nueve diez",
                "it", "il lo la i gli le un uno una di del della dei delle a al alla e o ne che si questo questa "
                        + "quello quella mio tuo suo nostro vostro in con su per tra fra non piu molto due tre quattro "
                        + "cinque sei sette otto nove dieci",
                "pt", "o a os as um uma uns umas de do da dos das ao aos no na nos nas e ou nem mas porem logo pois "
                        + "que se este esta estes estas esse essa isso isto aquele aquela meu minha teu tua seu sua "
                        + "nosso vosso em com sem por para sobre sob entre ate nao mais muito dois duas tres quatro "
                        + "cinco seis sete oito nove dez");
        Map<String, Set<String>> hollow = new HashMap<>();
        hollowRaw.forEach((lang, words) -> {
            Set<String> s = new HashSet<>();
            for (String w : words.split(" ")) s.add(normalize(w));
            hollow.put(lang, s);
        });
        TITLE_HOLLOW_WORDS = hollow;

        Map<String, String> stopRaw = Map.of(
                "fr", "le la les de des du un une et que qui est dans pour avec sur ce cette cela ne pas plus vous "
                        + "nous elle il être leur alors aussi mais donc était sont",
                "en", "the a an is are of and to that in let me also make sure they their each with all good you we "
                        + "was were your but then",
                "de", "der die das und ist ein eine mit für auf nicht zu von im den dem sie wir sind war aber dann auch",
                "es", "el la los las un una que es y para con en no por más usted nosotros ella está son era también "
                        + "pero entonces",
                "it", "il lo la gli le di un una che è e per con non in del della voi noi lei sono anche però quindi "
                        + "questo",
                "pt", "o os um uma que de do da dos das em no na com não para por mais você está são também então mas "
                        + "isso seu sua");
        Map<String, Set<String>> raw = new HashMap<>();
        stopRaw.forEach((lang, words) -> raw.put(lang, new HashSet<>(List.of(words.split(" ")))));
        Set<String> ambiguous = new HashSet<>();
        for (Set<String> words : raw.values()) {
            for (String w : words) {
                int n = 0;
                for (Set<String> other : raw.values()) if (other.contains(w)) n++;
                if (n > 1) ambiguous.add(w);
            }
        }
        Map<String, Set<String>> stop = new LinkedHashMap<>();
        for (String lang : List.of("fr", "en", "de", "es", "it", "pt")) {
            Set<String> s = new HashSet<>(raw.get(lang));
            s.removeAll(ambiguous);
            stop.put(lang, s);
        }
        LANGUAGE_STOPWORDS = stop;
    }

    public static final class ClueGenerationError extends RuntimeException {
        public ClueGenerationError(String msg, Throwable cause) {
            super(msg, cause);
        }
    }

    /** One word to clue: bare grid form, accented form, lemma(s), language. */
    public record Entry(String answer, String accented, List<String> canonical, String language) {}

    public final String baseUrl;
    public final String model;
    public final String apiKey;

    public Clues() {
        this(null, null, null);
    }

    public Clues(String baseUrl, String model, String apiKey) {
        this.baseUrl = baseUrl != null && !baseUrl.isEmpty() ? baseUrl : Env.get("LLM_BASE_URL", DEFAULT_LLM_BASE_URL);
        this.model = model != null && !model.isEmpty() ? model : Env.get("LLM_MODEL", DEFAULT_LLM_MODEL);
        this.apiKey = apiKey != null && !apiKey.isEmpty() ? apiKey : Env.get("LLM_API_KEY", DEFAULT_LLM_API_KEY);
    }

    // ================================================================== text helpers

    /** Lowercased, accent-stripped form (NFKD, combining marks dropped). */
    public static String normalize(String word) {
        String d = Normalizer.normalize(word, Normalizer.Form.NFKD);
        StringBuilder sb = new StringBuilder(d.length());
        d.codePoints().forEach(cp -> {
            int t = Character.getType(cp);
            if (t != Character.NON_SPACING_MARK && t != Character.ENCLOSING_MARK && t != Character.COMBINING_SPACING_MARK) {
                sb.appendCodePoint(cp);
            }
        });
        return sb.toString().toLowerCase(Locale.ROOT);
    }

    /** Python's {@code " ".join(s.split())}. */
    public static String collapseWs(String s) {
        if (s == null) return "";
        return String.join(" ", Py.split(s));
    }

    static List<String> splitWs(String s) {
        return Py.split(s);
    }

    static List<String> wordTokens(String s) {
        List<String> out = new ArrayList<>();
        Matcher m = WORD_TOKEN_RE.matcher(s);
        while (m.find()) out.add(m.group());
        return out;
    }

    static String sub(Pattern p, String s) {
        return p.matcher(s).replaceAll("");
    }

    static String bullets(List<?> items) {
        List<String> lines = new ArrayList<>();
        for (Object i : items) lines.add("- " + i);
        return String.join("\n", lines);
    }

    static List<String> splitLines(String s) {
        List<String> out = new ArrayList<>();
        for (String l : s.split("\\r\\n|\\r|\\n", -1)) out.add(l);
        if (!out.isEmpty() && out.get(out.size() - 1).isEmpty()) out.remove(out.size() - 1);
        return out;
    }

    static String cleanTitleLine(String line) {
        line = Py.strip(line);
        if (line.isEmpty()) return "";
        line = Py.strip(sub(LEADING_MARKER_RE, line));
        line = Py.strip(sub(TITLE_LABEL_RE, line));
        for (int i = 0; i < 3; i++) {
            String stripped = Py.strip(sub(TITLE_INTRO_RE, line));
            if (stripped.equals(line)) break;
            line = stripped;
        }
        line = Py.strip(sub(TITLE_QUOTES_RE, line));
        line = Py.strip(sub(TITLE_TRAILING_COMMENT_RE, line));
        line = Py.strip(sub(TITLE_QUOTES_RE, line));
        return line;
    }

    public static List<String> cleanTitles(String content) {
        List<String> titles = new ArrayList<>();
        Set<String> seen = new HashSet<>();
        for (String raw : splitLines(content)) {
            String cleaned = cleanTitleLine(raw);
            if (cleaned.isEmpty()) continue;
            if (Py.rstrip(cleaned).endsWith(":")) continue;
            String key = cleaned.toLowerCase(Locale.ROOT);
            if (!seen.add(key)) continue;
            titles.add(cleaned);
        }
        return titles;
    }

    public static String cleanThemeSuggestion(String content) {
        for (String raw : splitLines(content)) {
            String line = Py.strip(raw);
            if (line.isEmpty()) continue;
            line = Py.strip(sub(LEADING_MARKER_RE, line));
            line = Py.strip(sub(THEME_SUGGESTION_LABEL_RE, line));
            line = Py.strip(sub(TITLE_QUOTES_RE, line));
            int end = line.length();
            while (end > 0 && line.charAt(end - 1) == '.') end--;
            line = Py.strip(line.substring(0, end));
            if (!line.isEmpty() && !line.endsWith(":")) return line;
        }
        return "";
    }

    public static boolean titleHasBadPunctuation(String title) {
        for (int i = 0; i < title.length(); ) {
            int cp = title.codePointAt(i);
            if (!(Character.isLetterOrDigit(cp) || TITLE_ALLOWED_PUNCT.indexOf(cp) >= 0)) return true;
            i += Character.charCount(cp);
        }
        return false;
    }

    static boolean titleTooLong(String title) {
        return splitWs(title).size() > MAX_TITLE_WORDS_ACCEPTED;
    }

    static Set<String> targets(String answer, String accented, Collection<String> canonical) {
        Set<String> t = new HashSet<>();
        t.add(normalize(answer));
        t.add(normalize(accented));
        for (String c : canonical) t.add(normalize(c));
        return t;
    }

    static String stripLeadingWordLabel(String candidate, String answer, String accented, Collection<String> canonical) {
        Matcher m = LEADING_LABEL_RE.matcher(candidate);
        if (!m.lookingAt()) return candidate;
        if (!targets(answer, accented, canonical).contains(normalize(m.group(1)))) return candidate;
        String rest = Py.strip(candidate.substring(m.end()));
        return rest.isEmpty() ? candidate : rest;
    }

    static String stripLeadingWordIsAPrefix(String candidate, String answer, String accented,
                                            Collection<String> canonical, String language) {
        Pattern pattern = WORD_IS_A_PREFIX_RE.get(language);
        if (pattern == null) return candidate;
        Matcher head = HEAD_TOKEN_RE.matcher(candidate);
        if (!head.lookingAt()) return candidate;
        if (!targets(answer, accented, canonical).contains(normalize(head.group(1)))) return candidate;
        String rest = candidate.substring(head.end());
        Matcher cop = pattern.matcher(rest);
        if (!cop.lookingAt()) return candidate;
        String stripped = Py.strip(rest.substring(cop.end()));
        return stripped.isEmpty() ? candidate : stripped;
    }

    public static String stripReasoning(String content) {
        if (content.contains("</think>")) {
            Matcher m = THINK_BLOCK_RE.matcher(content);
            return m.find() ? content.substring(0, m.start()) + content.substring(m.end()) : content;
        }
        if (content.contains("<think>")) return "";
        return content;
    }

    static String singularize(String word, String language) {
        String w = word.toLowerCase(Locale.ROOT);
        if (language.equals("fr")) {
            if (w.endsWith("aux")) return w.substring(0, w.length() - 3) + "al";
            if (w.endsWith("s") || w.endsWith("x")) return w.substring(0, w.length() - 1);
            return w;
        }
        if (w.endsWith("s")) return w.substring(0, w.length() - 1);
        return w;
    }

    static boolean nounSenseMatchesWord(String lemma, String accented, String language) {
        if (!NOUN_PLURAL_LANGS.contains(language)) return true;
        String l = lemma.toLowerCase(Locale.ROOT), a = accented.toLowerCase(Locale.ROOT);
        return a.equals(l) || singularize(a, language).equals(l);
    }

    public static Set<String> titleGridWordReuse(String title, Set<String> gridNorm, Set<String> hollow) {
        Set<String> tokens = new TreeSet<>();
        for (String t : wordTokens(title)) tokens.add(normalize(t));
        tokens.retainAll(gridNorm);
        tokens.removeAll(hollow);
        return tokens;
    }

    public static String detectWrongLanguage(String candidate, String target) {
        if (!LANGUAGE_STOPWORDS.containsKey(target)) return null;
        Set<String> tokens = new HashSet<>();
        for (String t : wordTokens(candidate)) tokens.add(t.toLowerCase(Locale.ROOT));
        if (tokens.isEmpty()) return null;
        for (Map.Entry<String, Set<String>> e : LANGUAGE_STOPWORDS.entrySet()) {
            if (e.getKey().equals(target)) continue;
            int hits = 0;
            for (String t : tokens) if (e.getValue().contains(t)) hits++;
            if (hits >= WRONG_LANGUAGE_MIN_STOPWORDS) return e.getKey();
        }
        return null;
    }

    public static boolean themeEchoesInput(String sentence, String themeText) {
        Set<String> theme = new HashSet<>();
        for (String t : wordTokens(themeText)) theme.add(t.toLowerCase(Locale.ROOT));
        if (theme.size() < 3) return false;
        Set<String> sent = new HashSet<>();
        for (String t : wordTokens(sentence)) sent.add(t.toLowerCase(Locale.ROOT));
        Set<String> overlap = new HashSet<>(theme);
        overlap.retainAll(sent);
        return (double) overlap.size() / theme.size() >= THEME_ECHO_OVERLAP_THRESHOLD;
    }

    public static boolean themeIsRepetitive(String theme) {
        List<String> tokens = wordTokens(theme.toLowerCase(Locale.ROOT));
        if (tokens.size() < 2) return true;
        return new HashSet<>(tokens).size() < tokens.size();
    }

    static boolean containsTargetWord(String candidate, String answer, String accented, Collection<String> canonical) {
        Set<String> t = targets(answer, accented, canonical);
        for (String tok : wordTokens(candidate)) if (t.contains(normalize(tok))) return true;
        return false;
    }

    public static String maskTargetWord(String candidate, String answer, String accented, Collection<String> canonical) {
        Set<String> t = targets(answer, accented, canonical);
        Matcher m = WORD_TOKEN_RE.matcher(candidate);
        StringBuilder sb = new StringBuilder();
        while (m.find()) m.appendReplacement(sb, Matcher.quoteReplacement(t.contains(normalize(m.group())) ? "_" : m.group()));
        m.appendTail(sb);
        return sb.toString();
    }

    static Map<String, Object> loadPromptConfig(String language) {
        return PROMPT_CONFIG_CACHE.computeIfAbsent(language, lang -> {
            Path path = PROMPT_CONFIG_DIR.resolve(lang + "_prompt_config.json");
            if (!Files.exists(path)) path = PROMPT_CONFIG_DIR.resolve("fr_prompt_config.json");
            try {
                return Json.asMap(Json.readFile(path));
            } catch (IOException e) {
                throw new IllegalStateException("cannot read " + path, e);
            }
        });
    }

    // ================================================================== HTTP

    /** One chat-completions call; returns the raw message content. */
    private String chat(String system, String user, double temperature, int maxTokens, double timeout) throws IOException {
        Map<String, Object> body = Json.obj(
                "model", model,
                "messages", Json.list(Json.obj("role", "system", "content", system),
                        Json.obj("role", "user", "content", user)),
                "temperature", temperature,
                "max_tokens", maxTokens,
                "reasoning_effort", "none");
        Http.Response r = Http.postJson(baseUrl, body, Map.of("Authorization", "Bearer " + apiKey), timeout);
        if (r.status() >= 400) {
            throw new IOException("Client error '" + r.status() + "' for url '" + baseUrl + "': "
                    + (r.body().length() > 300 ? r.body().substring(0, 300) : r.body()));
        }
        try {
            Object choices = Json.get(r.json(), "choices");
            Object content = Json.get(Json.get(Json.asList(choices).get(0), "message"), "content");
            return content == null ? "" : content.toString();
        } catch (RuntimeException e) {
            throw new IOException("unexpected LLM response: " + (r.body().length() > 300 ? r.body().substring(0, 300) : r.body()), e);
        }
    }

    private String call(String answer, String accented, int roundNumber, String system, String user, int maxTokens,
                        double timeout, int totalRounds) {
        String content;
        try {
            content = chat(system, user, TEMPERATURE, maxTokens, timeout);
        } catch (IOException e) {
            throw new ClueGenerationError("LLM call failed (" + baseUrl + ", model=" + Log.repr(model) + "): "
                    + e.getMessage() + ". If you're using the default local llama.cpp server, make sure it's running "
                    + "(./run_llm.sh); otherwise check LLM_BASE_URL/LLM_MODEL/LLM_API_KEY in env.sh.", e);
        }
        Log.info("clue round %d/%d: %s (%s) — raw LLM response: %s", roundNumber, totalRounds, Log.repr(answer),
                Log.repr(accented), Log.repr(content));
        return stripReasoning(content);
    }

    // ================================================================== grid clues

    public interface ProgressCallback {
        void onProgress(int current, int total, String answer, String clue);
    }

    /**
     * Returns {ANSWER: clue}. Words are generated concurrently,
     * {@code batchParallelism} (default CLUE_BATCH_PARALLELISM) at a time.
     * Throws {@link GenerationCancelled}, or {@link GenerationPaused} whose
     * state is {@code Object[]{Map<String,String> clues, List<Entry> remaining}}.
     */
    public Map<String, String> generate(Collection<Entry> wordEntries, String difficulty, String language,
                                        double timeout, ProgressCallback onProgress, AtomicBoolean cancelEvent,
                                        BooleanSupplier shouldPause, String themeDescription,
                                        Integer batchParallelism) {
        LinkedHashSet<Entry> dedup = new LinkedHashSet<>();
        for (Entry e : wordEntries) {
            String lang = e.language() == null || e.language().isEmpty() ? language : e.language();
            dedup.add(new Entry(e.answer().toUpperCase(Locale.ROOT), e.accented(), List.copyOf(e.canonical()), lang));
        }
        List<Entry> entries = new ArrayList<>(dedup);
        Map<String, String> clues = new LinkedHashMap<>();
        if (entries.isEmpty()) return clues;
        int total = entries.size();
        List<RuntimeException> errors = new ArrayList<>();
        int maxTokens = REASONING_TOKEN_BUDGET + 300 + 90 * BATCH_SIZE;
        Map<String, String> systemPrompts = new HashMap<>();
        String theme = themeDescription == null ? "" : themeDescription;

        List<String> userMessages = new ArrayList<>();
        for (Entry e : entries) userMessages.add(buildUserMessage(e, e.language(), difficulty, theme));

        if (cancelEvent != null && cancelEvent.get()) throw new GenerationCancelled();
        if (shouldPause != null && shouldPause.getAsBoolean()) {
            throw new GenerationPaused(new Object[]{clues, new ArrayList<>(entries)});
        }
        int parallelism = batchParallelism == null ? CLUE_BATCH_PARALLELISM : Math.max(1, batchParallelism);
        ExecutorService executor = Executors.newFixedThreadPool(Math.min(parallelism, total));
        try {
            ExecutorCompletionService<Object[]> ecs = new ExecutorCompletionService<>(executor);
            List<Future<Object[]>> futures = new ArrayList<>();
            for (int i = 0; i < entries.size(); i++) {
                Entry e = entries.get(i);
                String sys = systemPrompts.computeIfAbsent(e.language(), l -> buildSystemPrompt(difficulty, l));
                String um = userMessages.get(i);
                futures.add(ecs.submit(() -> generateOne(e, um, sys, maxTokens, timeout, e.language(), difficulty,
                        cancelEvent, shouldPause, theme)));
            }
            for (int n = 0; n < futures.size(); n++) {
                Object[] res;
                try {
                    res = ecs.take().get();
                } catch (InterruptedException ie) {
                    Thread.currentThread().interrupt();
                    throw new GenerationCancelled();
                } catch (ExecutionException ee) {
                    Throwable c = ee.getCause();
                    if (c instanceof RuntimeException re) throw re;
                    throw new IllegalStateException(c);
                }
                String answer = (String) res[0];
                String clue = (String) res[1];
                @SuppressWarnings("unchecked")
                List<RuntimeException> wordErrors = (List<RuntimeException>) res[2];
                if (clue != null) clues.put(answer, clue);
                errors.addAll(wordErrors);
                if (onProgress != null) onProgress.onProgress(clues.size(), total, answer, clue);
                if (cancelEvent != null && cancelEvent.get()) {
                    executor.shutdownNow();
                    throw new GenerationCancelled();
                }
                if (shouldPause != null && shouldPause.getAsBoolean()) {
                    executor.shutdownNow();
                    List<Entry> remaining = new ArrayList<>();
                    for (Entry e : entries) if (!clues.containsKey(e.answer())) remaining.add(e);
                    throw new GenerationPaused(new Object[]{clues, remaining});
                }
            }
        } finally {
            executor.shutdownNow();
        }
        List<String> missing = new ArrayList<>();
        for (Entry e : entries) if (!clues.containsKey(e.answer())) missing.add(e.answer());
        if (!missing.isEmpty()) {
            Log.warning("clue generation: %d/%d word(s) still have no clue after all retry rounds (will show as the "
                    + "\"no definition available\" placeholder) — see the per-round warnings above for why each one "
                    + "failed: %s", missing.size(), total, missing);
        }
        if (!errors.isEmpty() && clues.isEmpty()) throw errors.get(0);
        return clues;
    }

    private Object[] generateOne(Entry entry, String userMessage, String systemPrompt, int maxTokens, double timeout,
                                 String language, String difficulty, AtomicBoolean cancelEvent,
                                 BooleanSupplier shouldPause, String themeDescription) {
        String answer = entry.answer(), accented = entry.accented();
        List<String> canonical = entry.canonical();
        String clue = null;
        List<RuntimeException> errors = new ArrayList<>();
        List<String> maskablePool = new ArrayList<>();
        String lastContent = null;
        for (int attempt = 0; attempt < 3; attempt++) {
            if (cancelEvent != null && cancelEvent.get()) break;
            if (shouldPause != null && shouldPause.getAsBoolean()) break;
            String content = null;
            RuntimeException error = null;
            List<String[]> details = new ArrayList<>();
            String outcome;
            try {
                content = call(answer, accented, attempt + 1, systemPrompt, userMessage, maxTokens, timeout, 3);
                lastContent = content;
                List<String> candidates = parseResponse(content);
                if (candidates.isEmpty()) {
                    outcome = "model gave no candidate lines at all";
                    Log.warning("clue round %d/3: %s (%s) — model gave no candidate lines at all", attempt + 1,
                            Log.repr(answer), Log.repr(accented));
                } else {
                    Object[] picked = pickClue(candidates, answer, accented, canonical, language, attempt + 1);
                    @SuppressWarnings("unchecked")
                    List<String[]> d = (List<String[]>) picked[1];
                    details = d;
                    @SuppressWarnings("unchecked")
                    List<String> maskable = (List<String>) picked[2];
                    maskablePool.addAll(maskable);
                    if (picked[0] != null) {
                        clue = (String) picked[0];
                        outcome = "selected: " + Log.repr(clue);
                    } else {
                        outcome = "all " + candidates.size() + " candidate(s) rejected (see the Candidates section "
                                + "below, or backend.log)";
                        Log.warning("clue round %d/3: %s (%s) — all %d candidate(s) rejected (see the per-candidate "
                                + "reasons just above)", attempt + 1, Log.repr(answer), Log.repr(accented), candidates.size());
                    }
                }
            } catch (ClueGenerationError e) {
                errors.add(e);
                error = e;
                outcome = "LLM call failed: " + e.getMessage();
                Log.warning("clue round %d/3: %s (%s) — LLM call failed: %s", attempt + 1, Log.repr(answer),
                        Log.repr(accented), e.getMessage());
            }
            writeCallLog(answer, accented, language, difficulty, attempt + 1, systemPrompt, userMessage, content, error,
                    outcome, details, clue != null, themeDescription);
            if (clue != null) break;
        }
        if (clue == null && !maskablePool.isEmpty()) {
            List<String> uniq = new ArrayList<>(new LinkedHashSet<>(maskablePool));
            String base = uniq.get(ThreadLocalRandom.current().nextInt(uniq.size()));
            clue = maskTargetWord(base, answer, accented, canonical);
            Log.warning("clue: %s (%s) — no clue clear of the target word after 3 rounds; masking it in a rejected "
                    + "candidate: %s -> %s", Log.repr(answer), Log.repr(accented), Log.repr(base), Log.repr(clue));
            List<String[]> det = new ArrayList<>();
            det.add(new String[]{clue, "selected (target word masked, fallback)"});
            writeCallLog(answer, accented, language, difficulty, 3, systemPrompt, userMessage, lastContent, null,
                    "fallback: target word masked with '_' in a rejected candidate (" + Log.repr(base) + ")", det, true,
                    themeDescription);
        }
        return new Object[]{answer, clue, errors};
    }

    // ================================================================== titles

    private static List<String> sortedAccented(Collection<Entry> entries) {
        return new ArrayList<>(new TreeSet<String>() {{
            for (Entry e : entries) add(e.accented());
        }});
    }

    private static List<String> sampleSorted(List<String> words, int n) {
        List<String> copy = new ArrayList<>(words);
        Collections.shuffle(copy);
        List<String> shown = new ArrayList<>(copy.subList(0, n));
        Collections.sort(shown);
        return shown;
    }

    public String generateTitle(Collection<Entry> wordEntries, String language, double timeout,
                                AtomicBoolean cancelEvent, String themeDescription) {
        if (cancelEvent != null && cancelEvent.get()) throw new GenerationCancelled();
        List<String> words = sortedAccented(wordEntries);
        if (words.isEmpty()) return "";
        String languageName = LANGUAGE_NAMES.getOrDefault(language, language);
        Set<String> hollow = TITLE_HOLLOW_WORDS.getOrDefault(language, Set.of());
        Set<String> gridNorm = new HashSet<>();
        for (String w : words) gridNorm.add(normalize(w));
        gridNorm.removeAll(hollow);
        String system = buildTitleSystemPrompt(TITLE_COUNT, languageName, themeDescription);
        int sampleSize = Math.max(1, (int) Math.rint(words.size() / 3.0));
        for (int attempt = 0; attempt < TITLE_RETRIES; attempt++) {
            if (cancelEvent != null && cancelEvent.get()) throw new GenerationCancelled();
            List<String> shown = sampleSorted(words, sampleSize);
            String user = "Some words from the grid: " + String.join(", ", shown) + "\n" + TITLE_COUNT
                    + " titles, one per line:";
            String content;
            try {
                content = chat(system, user, 0.9, REASONING_TOKEN_BUDGET + 60, timeout);
            } catch (IOException e) {
                Log.warning("title generation attempt %d/%d failed (%s, model=%s): %s", attempt + 1, TITLE_RETRIES,
                        baseUrl, Log.repr(model), e.getMessage());
                continue;
            }
            Log.info("title generation attempt %d/%d: raw LLM response: %s", attempt + 1, TITLE_RETRIES, Log.repr(content));
            List<String> candidates = cleanTitles(stripReasoning(content));
            List<String> kept = new ArrayList<>();
            for (String t : candidates) if (detectWrongLanguage(t, language) == null) kept.add(t);
            if (kept.size() != candidates.size()) {
                List<String> dropped = new ArrayList<>(candidates);
                dropped.removeAll(kept);
                Log.warning("title generation: discarded wrong-language candidate(s): %s", dropped);
            }
            List<String> clean = new ArrayList<>();
            for (String t : kept) {
                if (titleTooLong(t)) {
                    Log.info("title generation attempt %d/%d: rejecting %s (more than %d words)", attempt + 1,
                            TITLE_RETRIES, Log.repr(t), MAX_TITLE_WORDS_ACCEPTED);
                    continue;
                }
                if (titleHasBadPunctuation(t)) {
                    Log.info("title generation attempt %d/%d: rejecting %s (punctuation other than space/comma)",
                            attempt + 1, TITLE_RETRIES, Log.repr(t));
                    continue;
                }
                Set<String> reused = titleGridWordReuse(t, gridNorm, hollow);
                if (!reused.isEmpty()) {
                    Log.info("title generation attempt %d/%d: rejecting %s (reuses grid word(s): %s)", attempt + 1,
                            TITLE_RETRIES, Log.repr(t), String.join(", ", reused));
                } else {
                    clean.add(t);
                }
            }
            if (!clean.isEmpty()) {
                String title = clean.get(ThreadLocalRandom.current().nextInt(clean.size()));
                Log.info("title generation attempt %d/%d: candidates=%s chosen=%s", attempt + 1, TITLE_RETRIES, clean,
                        Log.repr(title));
                return title;
            }
            Log.info("title generation attempt %d/%d: no usable candidate (empty / wrong-language / too long / bad "
                    + "punctuation / all reuse a grid word), retrying", attempt + 1, TITLE_RETRIES);
        }
        Log.info("title generation: no usable candidate after %d attempts, returning no title", TITLE_RETRIES);
        return "";
    }

    public List<String> generateTitles(Collection<Entry> wordEntries, String language, int count, double timeout,
                                       AtomicBoolean cancelEvent, String themeDescription) {
        if (cancelEvent != null && cancelEvent.get()) throw new GenerationCancelled();
        List<String> words = sortedAccented(wordEntries);
        if (words.isEmpty()) return new ArrayList<>();
        String languageName = LANGUAGE_NAMES.getOrDefault(language, language);
        Set<String> hollow = TITLE_HOLLOW_WORDS.getOrDefault(language, Set.of());
        Set<String> gridNorm = new HashSet<>();
        for (String w : words) gridNorm.add(normalize(w));
        gridNorm.removeAll(hollow);
        String system = buildTitleSystemPrompt(count, languageName, themeDescription);
        int sampleSize = Math.max(1, (int) Math.rint(words.size() / 3.0));
        int totalRounds = 1 + TITLE_PROPOSALS_RETRIES;
        List<String> titles = new ArrayList<>();
        Set<String> seen = new HashSet<>();
        int attempt = 0;
        for (attempt = 0; attempt < totalRounds; attempt++) {
            if (cancelEvent != null && cancelEvent.get()) throw new GenerationCancelled();
            List<String> shown = sampleSorted(words, sampleSize);
            String user = "Some words from the grid: " + String.join(", ", shown) + "\n" + count + " titles, one per line:";
            String content;
            try {
                content = chat(system, user, 0.9, REASONING_TOKEN_BUDGET + 20 * count, timeout);
            } catch (IOException e) {
                Log.warning("title proposals attempt %d/%d failed (%s, model=%s): %s", attempt + 1, totalRounds, baseUrl,
                        Log.repr(model), e.getMessage());
                continue;
            }
            Log.info("title proposals attempt %d/%d: raw LLM response: %s", attempt + 1, totalRounds, Log.repr(content));
            for (String t : cleanTitles(stripReasoning(content))) {
                if (detectWrongLanguage(t, language) != null) continue;
                if (titleTooLong(t)) continue;
                if (titleHasBadPunctuation(t)) continue;
                if (!titleGridWordReuse(t, gridNorm, hollow).isEmpty()) continue;
                if (!seen.add(t.toLowerCase(Locale.ROOT))) continue;
                titles.add(t);
                if (titles.size() >= count) break;
            }
            if (!titles.isEmpty()) break;
            Log.info("title proposals attempt %d/%d: no usable candidate (empty / wrong-language / too long / bad "
                    + "punctuation / all reuse a grid word), retrying", attempt + 1, totalRounds);
        }
        Log.info("title proposals: %d/%d title(s) kept (%d attempt(s))", titles.size(), count,
                Math.min(attempt + 1, totalRounds));
        return titles;
    }

    public static String buildTitleSystemPrompt(int count, String languageName, String themeDescription) {
        String theme = collapseWs(themeDescription);
        String themeNote = theme.isEmpty() ? "" : "THEME INSPIRATION — this grid was built around a theme; here "
                + "is a description of it, for context only: \"" + theme + "\". "
                + "You may let it inform the mood, imagery or setting of your "
                + "titles if that helps, but you are NOT required to reference "
                + "it directly, and it never overrides the rules above — in "
                + "particular, never quote or repeat this description verbatim "
                + "(a title is 1 to "
                + MAX_TITLE_WORDS + " words, not a sentence), and a title must "
                + "still never contain a grid word.\n\n";
        return "You invent TITLES for a crossword puzzle — short names, like "
                + "the title of a book, a song, or a film. You are given the "
                + "list of every answer word in the grid.\n\n"
                + "Output exactly " + count + " DIFFERENT titles, ONE PER "
                + "LINE. Each title is 1 to " + MAX_TITLE_WORDS + " words, entirely "
                + "in " + languageName + ", loosely evoking the words or their "
                + "shared theme if one is apparent. The "
                + count + " must be genuinely different from each other "
                + "— a different key word or a different angle each time, not "
                + "near-duplicates.\n\n"
                + "Your ENTIRE reply is those " + count + " lines and nothing "
                + "else. Each line's first character is that title's first "
                + "character. No numbering, no bullet, no blank line between "
                + "them, no greeting, no preamble, no comment before or after.\n\n"
                + "NEVER do any of these:\n"
                + "- Give fewer than " + count + " lines, or repeat the same "
                + "title twice.\n"
                + "- Describe the task or explain yourself. A line must NOT "
                + "mean things like \"a crossword title\", \"title in "
                + languageName + "\", \"here is a title\", \"puzzle name\" — "
                + "that is a description, not a title.\n"
                + "- Start a line with a greeting or an introductory phrase "
                + "such as \"Bonjour\", \"Je propose\", \"Voici\", \"Voici les "
                + "titres\", \"Le titre est\", \"Un titre possible\", \"Je "
                + "suggère\", \"Here is\", \"How about\" — or any equivalent in "
                + languageName + ". Write each bare title with no such "
                + "lead-in.\n"
                + "- Add any comment, justification or explanation after a "
                + "title (\"car il évoque…\", \"parce que…\", \"(en référence "
                + "à…)\"). Stop each line the moment its title is complete.\n"
                + "- Output a whole sentence, a definition, or a list of the "
                + "grid words.\n"
                + "- Add quotes, a trailing period, or a label such as "
                + "\"Title:\" / \"Titre :\".\n"
                + "- Write in any language other than " + languageName + ", even if "
                + "some answers are foreign names.\n"
                + "- MOST IMPORTANT RULE: never put a grid word into a title. "
                + "Not the word itself, not its singular/plural, not another "
                + "tense of it, not it with or without an accent. If a grid "
                + "word (or any form of it) appears in your title, that title "
                + "is rejected. Only tiny function words (a, the, of, in, and "
                + "their equivalents) are allowed to coincide.\n\n"
                + "SHAPE of a good title: 2 or 3 words, an evocative noun "
                + "phrase or a small play on words — an image or a mood, never "
                + "a sentence and never a definition. (No sample titles are "
                + "given on purpose: any example would just get copied. Invent "
                + "your own.)\n\n"
                + themeNote
                + "HOW TO BUILD THEM:\n"
                + "1. Read the grid words in the user message. Note the mood, "
                + "place, season, time of day, or action they bring to mind.\n"
                + "2. Build " + count + " short names in "
                + languageName + " that EVOKE that mood/place/idea WITHOUT "
                + "naming any of the grid words. Say it sideways: a related "
                + "word, a broader word, a metaphor. Each of the "
                + count + " anchored on a DIFFERENT idea. A title that "
                + "could sit on top of any random grid is also wrong — it must "
                + "clearly fit THESE words while never containing one.\n"
                + "3. Before writing each line, scan it word by word against "
                + "the grid list. If any word matches, replace it with a "
                + "synonym or a related image and scan again.\n\n"
                + "WRONG answers, never produce anything like these:\n"
                + "- \"Titre de mots croisés\", \"Titre de la grille\", "
                + "\"" + languageName + " crossword\" — that names the task, not "
                + "this puzzle.\n"
                + "- Any line opening with \"Je propose\", \"Voici les "
                + "titres\", \"Bonjour\" or the like — each line's first "
                + "character is its title's first character.\n"
                + "- A title followed by \", car…\" / \"(en référence à…)\" — "
                + "stop the instant the title is complete.\n"
                + "- A title with no visible link to the grid words below.\n"
                + "- A title that contains any grid word from the list below "
                + "(this is the rule broken most often — check every line "
                + "against the list before sending).\n";
    }

    // ================================================================== themes

    public String describeTheme(String themeWords, String language, double timeout, AtomicBoolean cancelEvent,
                                double temperature, boolean verifyTranslation) {
        String themeText = collapseWs(themeWords);
        if (themeText.isEmpty()) return "";
        String languageName = LANGUAGE_NAMES.getOrDefault(language, language);
        String system = "You are given a few words or short phrases that state the "
                + "intended THEME of a crossword puzzle. Reply with a "
                + "description, entirely in " + languageName + ", of about 30 "
                + "words (25 to 35 is fine). Write in a TELEGRAPHIC style: as "
                + "few words as possible, as many distinct on-topic concepts as "
                + "possible, separated by commas. Drop ONLY articles, "
                + "prepositions and connectors (the small grammatical words). "
                + "Deliberately MIX grammatical categories — include on-topic "
                + "NOUNS, VERBS (in the infinitive), ADJECTIVES and ADVERBS, not "
                + "just nouns: the field it belongs to, related concepts, "
                + "typical vocabulary, actions performed, qualities/properties, "
                + "the places, people, objects and activities it evokes. This "
                + "text is used to search a dictionary for words semantically "
                + "close to the theme, so pack it with on-topic terms of every "
                + "part of speech and no filler or meta-commentary.\n\n"
                + "IMPORTANT: the theme words you are given may be written in "
                + "a DIFFERENT language than " + languageName + ". If so, you must "
                + "genuinely TRANSLATE the underlying concepts into "
                + languageName + " — do NOT simply copy, repeat, or extend "
                + "the input words in their own original language. Every "
                + "single word of your reply must be a real " + languageName + " "
                + "word, never a word from the input's own language.\n\n"
                + "Your ENTIRE reply is that description and nothing "
                + "else: no preamble, no title, no bullet list, no quotes, no "
                + "note before or after. Write only in " + languageName + ".";
        String user = "Theme words (possibly in a different language than required): " + themeText + "\n"
                + "Write your ~30-word telegraphic description ENTIRELY in "
                + languageName + ", translating as needed — do not reuse the "
                + "input words verbatim unless they already are real "
                + languageName + " words:";
        String last = "";
        for (int attempt = 0; attempt <= THEME_DESCRIPTION_RETRIES; attempt++) {
            if (cancelEvent != null && cancelEvent.get()) throw new GenerationCancelled();
            String content;
            try {
                content = chat(system, user, temperature, REASONING_TOKEN_BUDGET + 150, timeout);
            } catch (IOException e) {
                Log.warning("theme description failed (%s, model=%s): %s", baseUrl, Log.repr(model), e.getMessage());
                continue;
            }
            Log.info("theme description: raw LLM response: %s", Log.repr(content));
            String sentence = collapseWs(stripReasoning(content));
            if (sentence.isEmpty()) continue;
            List<String> parts = splitWs(sentence);
            if (parts.size() > 70) sentence = String.join(" ", parts.subList(0, 70));
            last = sentence;
            if (!verifyTranslation) return sentence;
            String wrong = detectWrongLanguage(sentence, language);
            boolean echoed = themeEchoesInput(sentence, themeText);
            if (wrong == null && !echoed) return sentence;
            Log.warning("theme description attempt %d/%d for language=%s looked wrong (wrong_language=%s, "
                            + "echoed_input=%s): %s — %s", attempt + 1, THEME_DESCRIPTION_RETRIES + 1, language,
                    wrong == null ? "None" : wrong, echoed ? "True" : "False", Log.repr(sentence),
                    attempt < THEME_DESCRIPTION_RETRIES ? "retrying" : "no attempts left, using it anyway");
        }
        return last;
    }

    public String generateRandomTheme(String language, String hintWord, double timeout, AtomicBoolean cancelEvent,
                                      double temperature) {
        if (cancelEvent != null && cancelEvent.get()) throw new GenerationCancelled();
        String languageName = LANGUAGE_NAMES.getOrDefault(language, language);
        String system = "You invent an original THEME for a crossword puzzle's "
                + "vocabulary, entirely in " + languageName + ". Reply with a "
                + "short keyword phrase (2 to 5 words) naming one concrete "
                + "subject or field (e.g. \"cuisine italienne\", \"exploration "
                + "spatiale\", \"vie sous-marine\") — exactly the kind of short "
                + "phrase a player would type into a crossword generator's "
                + "own theme field, never a full sentence, never an "
                + "explanation.\n\n"
                + "Be genuinely creative and varied: avoid the most obvious, "
                + "most commonly picked themes (cooking, animals, sports, "
                + "space) unless the indicative word below truly leads you "
                + "somewhere more specific and interesting.\n\n"
                + "Your ENTIRE reply is that short phrase and nothing else: no "
                + "preamble, no title, no quotes, no punctuation besides a "
                + "comma, no note before or after. Write only in " + languageName + ".";
        String hint = hintWord != null && !hintWord.isEmpty() ? " (indicative word: " + hintWord + ")" : "";
        String user = "Invent one random, original crossword theme." + hint + " Reply with only the short theme phrase:";
        for (int attempt = 0; attempt <= RANDOM_THEME_RETRIES; attempt++) {
            if (cancelEvent != null && cancelEvent.get()) throw new GenerationCancelled();
            String content;
            try {
                content = chat(system, user, temperature, REASONING_TOKEN_BUDGET + 40, timeout);
            } catch (IOException e) {
                Log.warning("random theme generation attempt %d/%d failed (%s, model=%s): %s", attempt + 1,
                        RANDOM_THEME_RETRIES + 1, baseUrl, Log.repr(model), e.getMessage());
                continue;
            }
            Log.info("random theme generation attempt %d/%d: raw LLM response: %s", attempt + 1,
                    RANDOM_THEME_RETRIES + 1, Log.repr(content));
            String theme = cleanThemeSuggestion(stripReasoning(content));
            if (!theme.isEmpty() && detectWrongLanguage(theme, language) == null && !themeIsRepetitive(theme)) {
                Log.info("random theme generation attempt %d/%d: hint_word=%s chosen=%s", attempt + 1,
                        RANDOM_THEME_RETRIES + 1, Log.repr(hintWord), Log.repr(theme));
                return theme;
            }
            Log.info("random theme generation attempt %d/%d: no usable candidate (theme=%s), retrying", attempt + 1,
                    RANDOM_THEME_RETRIES + 1, Log.repr(theme));
        }
        Log.info("random theme generation: no usable candidate after %d attempts", RANDOM_THEME_RETRIES + 1);
        return "";
    }

    // ================================================================== definitions / paraphrases

    public List<String> generateDefinitions(String text, String language, String difficulty, int count, double timeout,
                                            String themeDescription) {
        text = collapseWs(text);
        if (text.isEmpty()) return new ArrayList<>();
        Entry entry = new Entry(text.toUpperCase(Locale.ROOT), text, List.of(text.toLowerCase(Locale.ROOT)), language);
        String system = buildDefinitionsSystemPrompt(difficulty, language, count);
        List<String> parts = new ArrayList<>();
        parts.add("Word or expression: " + text);
        for (String block : List.of(buildGlossBlock(entry, language, difficulty), buildPosBlock(entry, language, difficulty),
                buildExamplesBlock(entry, language, difficulty))) {
            if (!block.isEmpty()) parts.add(block);
        }
        String theme = collapseWs(themeDescription);
        if (!theme.isEmpty()) {
            parts.add("THEME — these definitions are being written in the "
                    + "context of a theme. Here is that theme: "
                    + "\"" + theme + "\". STRONGLY steer each of your "
                    + count + " definitions toward this theme: whenever the "
                    + "word/expression's real meaning leaves you ANY latitude "
                    + "in angle, wording, imagery, chosen example or register, "
                    + "deliberately pick the formulation that best evokes this "
                    + "theme — its vocabulary, its setting, its people and "
                    + "activities — rather than a neutral one. Prefer a "
                    + "synonym, an example or a turn of phrase drawn from the "
                    + "theme's world. The ONE thing this must never do is make "
                    + "a definition wrong: it must still be accurate for THIS "
                    + "EXACT word/expression (its real meaning — see the "
                    + "ABSOLUTE RULE above) and point at nothing else. If a "
                    + "theme-flavoured phrasing would be inaccurate or "
                    + "ambiguous, drop the flavour for that definition and stay "
                    + "plain — accuracy always wins that trade. Never quote "
                    + "this theme text verbatim.");
        }
        String user = String.join("\n\n", parts);
        int maxTokens = REASONING_TOKEN_BUDGET + 200 + 40 * count;
        List<String> definitions = new ArrayList<>();
        int attempt;
        for (attempt = 0; attempt < 1 + DEFINE_RETRIES; attempt++) {
            String content = call(entry.answer(), text, attempt + 1, system, user, maxTokens, timeout, 1 + DEFINE_RETRIES);
            List<String> candidates = parseResponse(content);
            Object[] filtered = filterCandidates(candidates, entry.answer(), text, entry.canonical(), language,
                    attempt + 1, 1 + DEFINE_RETRIES);
            @SuppressWarnings("unchecked")
            List<Object[]> accepted = (List<Object[]>) filtered[0];
            Set<String> seen = new HashSet<>();
            for (Object[] a : accepted) {
                String c = (String) a[0];
                if (!seen.add(c)) continue;
                definitions.add(c);
                if (definitions.size() >= count) break;
            }
            if (!definitions.isEmpty()) break;
        }
        Log.info("define: %s (%s) -> %d/%d definition(s) kept (%d attempt(s))", Log.repr(text), language,
                definitions.size(), count, Math.min(attempt + 1, 1 + DEFINE_RETRIES));
        return definitions;
    }

    public static String buildDefinitionsSystemPrompt(String difficulty, String language, int count) {
        String style = DIFFICULTY_STYLE.getOrDefault(difficulty, DIFFICULTY_STYLE.get("medium"));
        String languageName = LANGUAGE_NAMES.getOrDefault(language, LANGUAGE_NAMES.get("fr"));
        return "You are writing dictionary-style crossword definitions in "
                + languageName + ", at " + difficulty.toUpperCase(Locale.ROOT) + " difficulty: " + style + "\n\n"
                + "The user message gives you one word or short expression. "
                + "Write exactly " + count + " different short definitions of it — "
                + "each one on its own line, nothing else on that line. It may "
                + "also include real dictionary definitions and/or real "
                + "example sentences for it.\n\n"
                + "ABSOLUTE RULE — if the user message contains a \"Dictionary "
                + "definition(s)\" section, every one of your definitions MUST "
                + "be built from a meaning written there, and from NOTHING "
                + "ELSE; never invent a meaning that is not listed there.\n\n"
                + "Rules:\n"
                + "1. Never include the word/expression itself, or a close "
                + "same-family variant of it, anywhere in a definition — this "
                + "includes using it as the sentence's own grammatical subject "
                + "(e.g. never start a definition with \"<word> is a ...\"/\"<word> "
                + "est un/une ...\" — write it as an impersonal, subject-less "
                + "definition instead, e.g. \"Small domesticated feline\" rather "
                + "than \"A cat is a small domesticated feline\").\n"
                + "2. Each definition must be a real, self-contained definition "
                + "a reader would understand on its own — at most "
                + MAX_CLUE_WORDS + " words — never a bare grammatical label and "
                + "never a description of the word's own spelling or letters.\n"
                + "3. Vary the " + count + " definitions: different real senses, "
                + "angles, or phrasing when more than one is possible, rather "
                + "than repeating the same one reworded.\n"
                + "4. Write entirely in " + languageName + ", every definition, "
                + "from the first word to the last.\n\n"
                + "OUTPUT FORMAT — exactly " + count + " lines and nothing else: no "
                + "numbering, no bullets, no labels, no blank lines, and no "
                + "commentary before, between, or after them.";
    }

    public List<String> generateParaphrases(String text, String language, int count, double timeout) {
        text = collapseWs(text);
        if (text.isEmpty()) return new ArrayList<>();
        String system = buildParaphraseSystemPrompt(language, count);
        int maxTokens = REASONING_TOKEN_BUDGET + 200 + 60 * count;
        String head = text.length() > 60 ? text.substring(0, 60) : text;
        String content = call(head.toUpperCase(Locale.ROOT), text, 1, system, "Text: " + text, maxTokens, timeout, 1);
        Set<String> seen = new HashSet<>();
        List<String> out = new ArrayList<>();
        for (String line : parseResponse(content)) {
            if (!seen.add(line.toLowerCase(Locale.ROOT))) continue;
            out.add(line);
            if (out.size() >= count) break;
        }
        Log.info("paraphrase: %s (%s) -> %d/%d kept", Log.repr(text), language, out.size(), count);
        return out;
    }

    public static String buildParaphraseSystemPrompt(String language, int count) {
        String languageName = LANGUAGE_NAMES.getOrDefault(language, LANGUAGE_NAMES.get("fr"));
        return "You are a paraphrasing assistant writing in " + languageName + ".\n\n"
                + "The user message gives you one sentence or short text. Write "
                + "exactly " + count + " different paraphrases of it — each one on "
                + "its own line, nothing else on that line.\n\n"
                + "Rules:\n"
                + "1. Each paraphrase must keep EXACTLY the same meaning as the "
                + "original — never add information that wasn't there, never "
                + "drop information that was, never change a fact, a number, a "
                + "name, or the overall tone.\n"
                + "2. Genuinely reword it: use different vocabulary and/or a "
                + "different sentence structure — do not just swap one or two "
                + "words and leave the rest identical.\n"
                + "3. Vary the " + count + " paraphrases from each other, not only "
                + "from the original.\n"
                + "4. Write entirely in " + languageName + ", every paraphrase, "
                + "from the first word to the last.\n\n"
                + "OUTPUT FORMAT — exactly " + count + " lines and nothing else: no "
                + "numbering, no bullets, no labels, no blank lines, and no "
                + "commentary before, between, or after them.";
    }

    /** "Corriger" button (Interactive mode) — mirrors clues.py's correct_text. */
    public String correctText(String text, String language, double timeout) {
        text = collapseWs(text);
        if (text.isEmpty()) return "";
        String system = buildCorrectionSystemPrompt(language);
        int maxTokens = REASONING_TOKEN_BUDGET + 100 + 2 * text.length();
        String head = text.length() > 60 ? text.substring(0, 60) : text;
        String content = call(head.toUpperCase(Locale.ROOT), text, 1, system, "Text: " + text, maxTokens, timeout, 1);
        List<String> lines = parseResponse(content);
        String corrected = lines.isEmpty() ? text : lines.get(0);
        Log.info("correct: %s (%s) -> %s", Log.repr(text), language, Log.repr(corrected));
        return corrected;
    }

    public static String buildCorrectionSystemPrompt(String language) {
        String languageName = LANGUAGE_NAMES.getOrDefault(language, LANGUAGE_NAMES.get("fr"));
        return "You are a proofreader for short texts written in " + languageName + ".\n\n"
                + "The user message gives you one short text (a crossword clue or a "
                + "crossword grid title). "
                + "Return the same text with its mistakes corrected.\n\n"
                + "Correct ONLY these mistakes:\n"
                + "1. Grammatical agreement errors: number (singular/plural) and "
                + "gender (masculine/feminine) between words that must agree.\n"
                + "2. Typing errors: a wrong letter, a missing or extra letter, two "
                + "letters swapped.\n"
                + "3. Missing spaces: two words accidentally merged into one must be "
                + "split back into separate words.\n"
                + "4. Missing or wrong accents: accents and other diacritics are part "
                + "of the spelling. A word written without the accent(s) its correct "
                + "spelling requires, or with a wrong one, is a mistake: restore the "
                + "correct accented spelling (for example, in French, \"Releve\" -> "
                + "\"Relève\", \"eleve\" -> \"élève\", \"foret\" -> \"forêt\"). Check every "
                + "word of the text for this.\n"
                + "5. A lowercase first letter: the VERY FIRST character of the text "
                + "must be a capital letter. Capitalize only that first letter — "
                + "never any other word.\n"
                + "6. Wrong word order: an order that is not natural in the text's "
                + "language, typically from a non-native writer following the rules "
                + "of another language — above all an adjective on the wrong side of "
                + "its noun (for example, in French, \"une noire voiture\" -> \"une "
                + "voiture noire\"; in English, \"a car red\" -> \"a red car\"). A moved "
                + "adjective stays with the noun it describes: only its side of that "
                + "noun changes, never the noun it belongs to, and it is never replaced "
                + "by another adjective. Move "
                + "only the misplaced words; an order that is already correct in the "
                + "language (e.g. French \"une grande maison\") stays as it is.\n\n"
                + "Rules:\n"
                + "- Keep the original wording as closely as possible: never "
                + "rephrase, never replace a correct word with a synonym, never "
                + "reorder words (except to fix mistake 6), never add or remove "
                + "information.\n"
                + "- Apart from the first letter, keep the original capitalization "
                + "and punctuation unless they are part of a mistake listed above.\n"
                + "- If the text has no mistake, return it exactly as it is.\n"
                + "- Write in " + languageName + ".\n\n"
                + "OUTPUT FORMAT — exactly one line: the corrected text, starting "
                + "with a capital letter, and nothing else — no label, no quotes, no "
                + "explanation.";
    }

    // ================================================================== grounding blocks

    static String buildExamplesBlock(Entry entry, String language, String difficulty) {
        String accented = entry.accented();
        List<String> sentences = ExampleSentences.findExamplesForWords(List.of(accented), language,
                ExampleSentences.DEFAULT_LIMIT).get(accented);
        if (sentences == null || sentences.isEmpty()) return "";
        List<String> lines = new ArrayList<>();
        for (String s : sentences) lines.add("- " + s);
        String block = "Real example sentences using \"" + accented + "\":\n" + String.join("\n", lines) + "\n\n"
                + "These are genuine sentences, not hints about difficulty or "
                + "style — use them only to confirm what the word actually means "
                + "(this matters most for short or unusual words that might look "
                + "like a word from another language) before writing your clues.";
        if (difficulty.equals("easy")) {
            block += " These sentences may contain proper nouns (names of "
                    + "people, places, brands, works) near the target word — "
                    + "IGNORE those completely. They are not the meaning to "
                    + "clue; at this difficulty you must define the word's "
                    + "plain, everyday sense only.";
        }
        return block;
    }

    public static String buildGlossBlock(Entry entry, String language, String difficulty) {
        String accented = entry.accented();
        Map<String, List<Object>> byLemma = GlossLookup.findGlossesForCanonicals(entry.canonical(), language);
        boolean dropName = difficulty.equals("easy");
        List<String> wordParts = new ArrayList<>();
        for (String lemma : entry.canonical()) {
            for (Object sense : byLemma.getOrDefault(lemma, List.of())) {
                String pos = Json.str(sense, "pos", null);
                if (dropName && "name".equals(pos)) continue;
                if (pos != null && NOUN_POS.contains(pos) && !nounSenseMatchesWord(lemma, accented, language)) continue;
                for (Object gloss : Json.listOrEmpty(Json.get(sense, "glosses"))) {
                    wordParts.add("- \"" + lemma + "\" (" + (pos == null ? "None" : pos) + "): " + gloss);
                }
            }
        }
        if (wordParts.isEmpty()) return "";
        return "Dictionary definition(s) related to \"" + accented + "\":\n"
                + String.join("\n", wordParts) + "\n\nThese are real dictionary "
                + "definitions of the word's root form(s), and they are the "
                + "ONLY meanings you are allowed to clue (see the ABSOLUTE RULE "
                + "in the instructions). Every one of your 3 clues must come "
                + "from a definition line above and from nothing else — not "
                + "from what the word 'reminds you of', not from a similar-"
                + "looking word in another language, not from a meaning you "
                + "half-remember. If a root form above resembles a more "
                + "familiar word, that resemblance is a trap: define only what "
                + "the text after the colon says. If more than one distinct "
                + "sense is shown, treat that as a chance to make your 3 "
                + "candidates genuinely different by drawing on different "
                + "senses, rather than 3 rewordings of one — but each must "
                + "still trace back to a specific line above.\n\n"
                + "NOTE — every line above was found by looking up this word's "
                + "ROOT / canonical form(s), NOT the exact form "
                + "\"" + accented + "\" that goes in the grid. A root's entry can "
                + "therefore list senses that \"" + accented + "\" itself cannot "
                + "actually carry. Example: English \"ares\" is looked up under "
                + "its root \"are\", whose entry covers BOTH \"are\" = a unit "
                + "of area (100 m²), of which \"ares\" is the valid plural, AND "
                + "\"are\" = a present-tense form of the verb \"be\" — but "
                + "\"ares\", with the -s, can never be a form of \"be\". Before "
                + "you build a clue from any sense above, check it is "
                + "grammatically possible for \"" + accented + "\" itself: right part "
                + "of speech, right number for a noun, right person/tense for a "
                + "verb form. Silently drop any sense \"" + accented + "\" cannot "
                + "express, even though it is printed above.";
    }

    public static String buildPosBlock(Entry entry, String language, String difficulty) {
        String accented = entry.accented();
        boolean dropName = difficulty.equals("easy");
        List<InflectionLookup.Analysis> analyses = InflectionLookup.describeForm(accented, language);
        if (!analyses.isEmpty()) {
            List<String> descs = new ArrayList<>();
            for (InflectionLookup.Analysis a : analyses) {
                if (!(dropName && "name".equals(a.pos()))) descs.add(a.description());
            }
            if (!descs.isEmpty()) {
                List<String> lines = new ArrayList<>();
                for (String d : descs) lines.add("- " + d);
                return "Grammatical analysis of the exact form \"" + accented + "\" "
                        + "(Wiktionary):\n" + String.join("\n", lines) + "\n"
                        + "Base your A= line on this, and match each clue's own "
                        + "grammar to it (a verb's person, number and mood/tense; "
                        + "a noun's or adjective's number and gender).";
            }
        }
        Map<String, List<Object>> byLemma = GlossLookup.findGlossesForCanonicals(entry.canonical(), language);
        List<String> types = new ArrayList<>();
        for (String lemma : entry.canonical()) {
            for (Object sense : byLemma.getOrDefault(lemma, List.of())) {
                String pos = Json.str(sense, "pos", null);
                if (pos == null || pos.isEmpty()) continue;
                if (dropName && pos.equals("name")) continue;
                if (NOUN_POS.contains(pos) && !nounSenseMatchesWord(lemma, accented, language)) continue;
                String label = POS_LABELS.getOrDefault(pos, pos);
                if (!types.contains(label)) types.add(label);
            }
        }
        if (types.isEmpty()) return "";
        return "Grammatical type(s) the exact form \"" + accented + "\" can be "
                + "(Hunspell stem analysis + dictionary): " + String.join(", ", types) + ".\n"
                + "Use this to fix the part of speech in your A= line; you must "
                + "still read the precise inflection off the written form itself "
                + "(for a verb: person, number, mood/tense; for a noun or "
                + "adjective: number and gender).";
    }

    // ================================================================== system / user prompts

    public String buildSystemPrompt(String difficulty, String language) {
        Map<String, Object> config = loadPromptConfig(language);
        String style = DIFFICULTY_STYLE.getOrDefault(difficulty, DIFFICULTY_STYLE.get("medium"));
        String languageName = LANGUAGE_NAMES.getOrDefault(language, LANGUAGE_NAMES.get("fr"));
        Map<String, Object> diffExamples = Json.mapOrEmpty(config.get("difficulty_examples"));
        Object diffExample = diffExamples.containsKey(difficulty) ? diffExamples.get(difficulty) : diffExamples.get("medium");
        String styleLine = style + " Example: for " + Json.get(diffExample, "word") + ", \"" + Json.get(diffExample, "clue") + "\"";
        return "You are a crossword compiler writing in " + languageName + ", at "
                + difficulty.toUpperCase(Locale.ROOT) + " difficulty. This difficulty level is the "
                + "single most important constraint on every clue you write:\n"
                + styleLine + "\n\n"
                + "The user message will give you a single word to write a clue "
                + "for, in its correctly accented, inflected written form (right "
                + "gender, number, and conjugation). Your clues must MATCH that "
                + "exact form: whatever tense, mood, person, number and gender "
                + "the word carries, the wording of each clue must carry the "
                + "same features in its own grammar — a clue that only points at "
                + "the right meaning, in the wrong tense or the wrong "
                + "number/gender, does NOT fit. It may also include real "
                + "dictionary definitions and/or real example sentences for that "
                + "word.\n\n"
                + "ABSOLUTE RULE — THE DICTIONARY DEFINITION IS THE ONLY SOURCE "
                + "OF MEANING. If the user message contains a \"Dictionary "
                + "definition(s)\" section, every one of your 3 clues MUST be "
                + "built from a meaning written there, and from NOTHING ELSE. "
                + "You may not clue any sense that is not in that section. If "
                + "your own memory of the word disagrees with the definition "
                + "given, your memory is wrong — follow the definition. If a "
                + "listed root form happens to look like a word in another "
                + "language, or like a different, more familiar word, ignore "
                + "that resemblance completely: only the definition TEXT next "
                + "to it counts (e.g. a French entry 'choir (verb): Tomber.' "
                + "means the verb 'to fall' — it has nothing to do with an "
                + "English 'choir'/a singing group). Inventing a plausible-"
                + "sounding meaning that is not in the definitions is the single "
                + "worst mistake you can make here. One further care: that "
                + "section is looked up by the word's ROOT form, so it can also "
                + "list a sense that only a DIFFERENT inflection of the word "
                + "could carry (e.g. a verb-form sense for a word that is "
                + "plainly a plural noun — English \"ares\" is the plural of "
                + "\"are\" the area unit, never a form of \"be\", even though "
                + "its root \"are\" is one). Drop any such sense, per rule 4 "
                + "and the NOTE at the end of that section.\n\n"
                + "Propose exactly 3 different possible crossword clues for that "
                + "single word, all matching the difficulty level above.\n\n"
                + "Rules:\n"
                + "1. Never include the word being defined anywhere in the clue "
                + "— not as the whole answer, and not embedded inside a longer "
                + "sentence either — in any spelling, case, or with/without "
                + "accents. A same-family word (a different form of the same "
                + "root) is also forbidden — including a different inflection "
                + "of this exact same word (e.g. the masculine equivalent of a "
                + "feminine target, or a different tense/person of the same "
                + "verb): a near-identical variant still gives the answer away "
                + "just as much as the exact spelling would, even though it "
                + "isn't byte-for-byte the same. This also means never opening "
                + "a candidate with the word itself as a label, followed by a "
                + "colon, comma, or dash, before the actual definition (e.g. "
                + "\"word - definition\" or \"word: definition\") — that is "
                + "still the word appearing in the clue, just as a prefix "
                + "instead of embedded in a sentence; write only the "
                + "definition itself, with nothing labeling it.\n"
                + "2. Do not write a bare grammatical/technical description — "
                + "write an actual clue a crossword solver would enjoy, not a "
                + "label. Describe what the word actually means.\n"
                + "3. Do not describe the word's spelling or letters instead of "
                + "its meaning. A clue must always be about the meaning, never "
                + "the letters.\n"
                + "4. The clue must match the word's EXACT inflected form in "
                + "every way that applies — for a verb: person, number, AND "
                + "mood/tense together; for a noun or adjective: number "
                + "(singular/plural) and gender. Getting the general meaning "
                + "right is never enough if the grammar doesn't match. Before "
                + "answering, identify the word's specific grammatical form (for "
                + "a verb: its subject — " + config.get("subject_pronouns") + " — and "
                + "mood/tense; for a noun or adjective: singular or plural, and "
                + "gender) and confirm your clue matches that exactly, not just "
                + "a same-meaning idea in a different form. Two specific traps: "
                + "(a) a generic dictionary-style definition of the bare action "
                + "or state (e.g. \"the act of doing X\"), or one whose own verb "
                + "sits in the plain present, describes the infinitive or the "
                + "present — not a specific conjugated form. If the target word "
                + "is a future, conditional, past, imperfect or subjunctive "
                + "form, the main verb of your clue MUST be in that same "
                + "tense/mood: a present-tense clue for a future-tense word is "
                + "wrong even when the meaning is exactly right (a word like "
                + "\"HUMERA\", the future of \"humer\", needs a clue whose own "
                + "verb is future too). Rephrase the clue so it is unmistakably "
                + "tied to that exact person AND tense/mood; "
                + "(b) if your clue names a person or thing to carry the word's "
                + "adjective/participle — a person noun like \"a house\"/\"a "
                + "runner\", or just as easily an ordinary, unremarkable one "
                + "like \"grass\" or \"soil\" that doesn't feel specially "
                + "gendered — that noun must itself carry the EXACT SAME "
                + "gender and number as the word being defined. Before "
                + "finalizing each candidate, explicitly check this one "
                + "pairing — the target word's own gender/number against the "
                + "gender/number of the noun your clue names — and rewrite it "
                + "if they don't match exactly; never let it silently "
                + "disagree.\n"
                + "5. The clue must reflect the word's actual, real meaning — "
                + "never an unrelated sentence that merely sounds plausible, and "
                + "never a meaning you 'recognise' that isn't in the definitions "
                + "you were given. This is the same point as the ABSOLUTE RULE "
                + "above, restated as a check: for EACH of your 3 candidates, "
                + "before writing it, point to the exact dictionary definition "
                + "line it comes from. If you cannot, that candidate is invalid "
                + "— rewrite it from a definition that IS listed. If no "
                + "dictionary section was provided at all, only then may you "
                + "rely on your own knowledge, and even then stay to the "
                + "plainest, most certain everyday sense.\n"
                + "6. A synonym or near-synonym is a perfectly good clue.\n"
                + "7. Keep each candidate clue short: a single clause or "
                + "sentence, at most " + MAX_CLUE_WORDS + " words. Never write out "
                + "your reasoning or think out loud about the word (its "
                + "length, its letters, whether it might be an abbreviation, "
                + "etc.), and never self-correct inline — starting one answer, "
                + "then writing something like \"wait, no\" or \"actually\" "
                + "before giving a different one. Decide on your final answer "
                + "entirely on your own, before writing anything down, and "
                + "write only that one finished result — never discuss or "
                + "quote these instructions, and never leave a discarded first "
                + "attempt visible before the real one.\n"
                + "8. Write every clue entirely in " + languageName + " — the same "
                + "language as the word itself — from the very first word to "
                + "the last. Never switch to another language partway "
                + "through, even for a single stray word.\n\n"
                + "=== EXAMPLES ===\n"
                + "These illustrate the rules above using words other than the "
                + "one you are actually being asked about — never reuse them as "
                + "your answer.\n\n"
                + "Examples of what NOT to do:\n"
                + bullets(Json.listOrEmpty(config.get("rule_bad"))) + "\n\n"
                + "Examples of what TO do (correct conjugation, number, and "
                + "gender agreement, and a real definition rather than a "
                + "grammatical label):\n"
                + bullets(Json.listOrEmpty(config.get("rule_good"))) + "\n\n"
                + "=== END OF EXAMPLES ===\n\n"
                + "GRAMMAR CHECK — before you output C1, C2 and C3, work out the "
                + "target word's exact grammatical form, then check EACH "
                + "candidate against it:\n"
                + "- Tense/mood: if the target is a future, conditional, past, "
                + "imperfect or subjunctive form (not the plain present or the "
                + "infinitive), the main verb of your clue must be in that SAME "
                + "tense/mood.\n"
                + "- Person and number of a verb: your clue must be framed for "
                + "the same subject as the target (I / you / he-she / we / "
                + "you-plural / they).\n"
                + "- Noun or adjective: match singular vs. plural AND gender — "
                + "any noun your clue names to carry the meaning must itself "
                + "have the target's number and gender.\n"
                + "Rewrite any candidate whose own grammar does not match before "
                + "you output it. A right meaning in the wrong grammatical form "
                + "is a wrong answer here.\n\n"
                + "OUTPUT FORMAT — respond with exactly these 4 lines and "
                + "nothing else:\n"
                + "A=the target word's grammatical type and inflection: its "
                + "part of speech (noun, verb, adjective, adverb, ...) and then, "
                + "for a verb, its person + number + mood/tense; for a noun or "
                + "adjective, its number + gender. Example: for a word that is "
                + "the third-person-singular future of a verb, "
                + "\"A=verb, third person singular, future\".\n"
                + "C1=short sentence (even a single word) indirectly defining "
                + "the target word without giving it away, its own grammar "
                + "matching the A= line above\n"
                + "C2=short sentence (even a single word) indirectly defining "
                + "the target word without giving it away, its own grammar "
                + "matching the A= line above\n"
                + "C3=short sentence (even a single word) indirectly defining "
                + "the target word without giving it away, its own grammar "
                + "matching the A= line above\n\n"
                + "The A= line is an analysis step to force you to nail the "
                + "grammar before writing the clues — it is discarded and never "
                + "shown to anyone, so it does not need to read like a clue. "
                + "C1, C2 and C3 are the actual clues.\n"
                + "No JSON, no markdown, no blank lines, no repeating the word "
                + "itself anywhere in A/C1/C2/C3, and no extra commentary "
                + "before, between, or after these 4 lines.";
    }

    String buildUserMessage(Entry entry, String language, String difficulty, String themeDescription) {
        List<String> parts = new ArrayList<>();
        parts.add("Word: " + entry.accented() + "\n"
                + "Each of your 3 clues must be phrased so its OWN grammar — "
                + "tense, mood, person, number, gender — matches this exact "
                + "written form, not merely its meaning. If this word is a "
                + "future / conditional / past / imperfect / subjunctive verb "
                + "form, the verbs in your clue must be in that same tense/mood.");
        for (String block : List.of(buildGlossBlock(entry, language, difficulty), buildPosBlock(entry, language, difficulty),
                buildExamplesBlock(entry, language, difficulty))) {
            if (!block.isEmpty()) parts.add(block);
        }
        String theme = collapseWs(themeDescription);
        if (!theme.isEmpty()) {
            parts.add("THEME — this grid was built around a theme. Here is that "
                    + "theme, as the keyword list produced for it: "
                    + "\"" + theme + "\". STRONGLY steer each of your 3 "
                    + "clues toward this theme: whenever THIS word's real "
                    + "meaning and its required grammar leave you ANY latitude "
                    + "in angle, wording, imagery, chosen example or register, "
                    + "deliberately pick the formulation that best evokes this "
                    + "theme — its vocabulary, its setting, its people and "
                    + "activities — rather than a neutral one. Prefer a synonym, "
                    + "an example or a turn of phrase drawn from the theme's "
                    + "world. The ONE thing this must never do is make a clue "
                    + "wrong: it must still be an accurate clue for THIS EXACT "
                    + "word (its real meaning — see the ABSOLUTE RULE), match "
                    + "its exact grammar (rule 4), and point at nothing else. "
                    + "If a theme-flavoured phrasing would be inaccurate, "
                    + "ambiguous, or grammatically mismatched, drop the flavour "
                    + "for that clue and stay plain — accuracy always wins that "
                    + "trade. Never quote this keyword list verbatim.");
        }
        return String.join("\n\n", parts);
    }

    // ================================================================== call log / parsing / filtering

    private void writeCallLog(String answer, String accented, String language, String difficulty, int roundNumber,
                              String systemPrompt, String userMessage, String content, RuntimeException error,
                              String outcome, List<String[]> details, boolean success, String themeDescription) {
        String ts = LocalDateTime.now().format(DateTimeFormatter.ofPattern("yyyyMMdd-HHmmss-SSSSSS"));
        Path path = CALL_LOG_DIR.resolve(ts + "_" + answer + "_" + (success ? "SUCCES" : "ERROR") + ".md");
        String errorSection = error != null ? error.getMessage() : "None";
        String output = content != null ? content : "(no response — see error above)";
        String candidates;
        if (details != null && !details.isEmpty()) {
            List<String> lines = new ArrayList<>();
            for (String[] d : details) lines.add("- **" + d[1] + "**: " + Log.repr(d[0]));
            candidates = String.join("\n", lines);
        } else {
            candidates = "(none — see Error above, or the model gave no parsable candidate lines)";
        }
        String themeLine = themeDescription != null && !themeDescription.isEmpty()
                ? "- **Theme keywords** (whole-theme LLM list, steers the clue): " + collapseWs(themeDescription) + "\n"
                : "";
        String body = "# Clue generation call — " + answer + " (" + accented + ")\n\n"
                + "- **Date**: " + GridStore.isoNow() + "\n"
                + "- **Language**: " + language + "\n"
                + "- **Difficulty**: " + difficulty + "\n"
                + themeLine
                + "- **LLM endpoint**: " + baseUrl + "\n"
                + "- **Model**: " + model + "\n"
                + "- **Attempt**: " + roundNumber + "/3\n"
                + "- **Outcome**: " + outcome + "\n\n"
                + "## Error\n\n" + errorSection + "\n\n"
                + "## System prompt\n\n```\n" + systemPrompt + "\n```\n\n"
                + "## User message\n\n```\n" + userMessage + "\n```\n\n"
                + "## Raw LLM output\n\n```\n" + output + "\n```\n\n"
                + "## Candidates\n\n" + candidates + "\n";
        try {
            Files.createDirectories(CALL_LOG_DIR);
            Files.writeString(path, body, StandardCharsets.UTF_8);
        } catch (IOException e) {
            Log.warning("failed to write call log for %s: %s", Log.repr(answer), e.getMessage());
        }
    }

    public static List<String> parseResponse(String content) {
        List<String> out = new ArrayList<>();
        for (String line : splitLines(content)) {
            String l = line.replace(' ', ' ');
            if (ANALYSIS_LINE_RE.matcher(l).lookingAt()) continue;
            String cleaned = Py.strip(sub(LEADING_MARKER_RE, l));
            if (!cleaned.isEmpty()) out.add(cleaned);
        }
        return out;
    }

    /** Returns {accepted: List<Object[]{text, detailsIndex}>, details: List<String[]{candidate, verdict}>,
     * maskable: List<String>}. */
    public static Object[] filterCandidates(List<String> candidates, String answer, String accented, List<String> canonical,
                                     String language, int roundNumber, int totalRounds) {
        List<String[]> details = new ArrayList<>();
        List<Object[]> accepted = new ArrayList<>();
        List<String> maskable = new ArrayList<>();
        for (String c : candidates) {
            if (c != null && !c.isEmpty()) {
                String stripped = stripLeadingWordLabel(c, answer, accented, canonical);
                if (!stripped.equals(c)) {
                    Log.info("clue round %d/%d: %s (%s) — stripped leaked word-label prefix: %s -> %s", roundNumber,
                            totalRounds, Log.repr(answer), Log.repr(accented), Log.repr(c), Log.repr(stripped));
                    c = stripped;
                }
                stripped = stripLeadingWordIsAPrefix(c, answer, accented, canonical, language);
                if (!stripped.equals(c)) {
                    Log.info("clue round %d/%d: %s (%s) — stripped leaked '<word> is a' prefix: %s -> %s", roundNumber,
                            totalRounds, Log.repr(answer), Log.repr(accented), Log.repr(c), Log.repr(stripped));
                    c = stripped;
                }
            }
            List<String> reasons = new ArrayList<>();
            boolean containsOnly = false;
            if (c == null || c.isEmpty()) {
                reasons.add("empty");
            } else {
                int wc = splitWs(c).size();
                if (wc > MAX_CLUE_WORDS) reasons.add("too long (" + wc + " words > " + MAX_CLUE_WORDS + ")");
                if (NON_LATIN_RE.matcher(c).find()) reasons.add("non-Latin script");
                String wrong = detectWrongLanguage(c, language);
                if (wrong != null) reasons.add("looks like " + wrong + " instead of " + language);
                if (containsTargetWord(c, answer, accented, canonical)) {
                    reasons.add("contains the target word (copy/same-family/embedded)");
                    containsOnly = reasons.size() == 1;
                }
            }
            if (!reasons.isEmpty()) {
                Log.info("clue round %d/%d: %s (%s) — candidate rejected (%s): %s", roundNumber, totalRounds,
                        Log.repr(answer), Log.repr(accented), String.join("; ", reasons), Log.repr(c));
                details.add(new String[]{c, "rejected: " + String.join("; ", reasons)});
                if (containsOnly) maskable.add(c);
            } else {
                accepted.add(new Object[]{c, details.size()});
                details.add(new String[]{c, "accepted (not selected)"});
            }
        }
        return new Object[]{accepted, details, maskable};
    }

    static Object[] pickClue(List<String> candidates, String answer, String accented, List<String> canonical,
                             String language, int roundNumber) {
        Object[] f = filterCandidates(candidates, answer, accented, canonical, language, roundNumber, 3);
        @SuppressWarnings("unchecked")
        List<Object[]> accepted = (List<Object[]>) f[0];
        @SuppressWarnings("unchecked")
        List<String[]> details = (List<String[]>) f[1];
        if (accepted.isEmpty()) return new Object[]{null, details, f[2]};
        Object[] chosen = accepted.get(ThreadLocalRandom.current().nextInt(accepted.size()));
        Log.info("clue round %d/3: %s (%s) — candidate selected: %s", roundNumber, Log.repr(answer), Log.repr(accented),
                Log.repr(chosen[0]));
        details.set((Integer) chosen[1], new String[]{(String) chosen[0], "selected"});
        return new Object[]{chosen[0], details, f[2]};
    }
}
