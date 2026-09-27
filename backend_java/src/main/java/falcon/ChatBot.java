package falcon;

import java.io.IOException;
import java.net.URI;
import java.net.http.HttpRequest;
import java.net.http.HttpResponse;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.time.Duration;
import java.util.ArrayList;
import java.util.HashSet;
import java.util.Iterator;
import java.util.List;
import java.util.Map;
import java.util.Set;
import java.util.function.Consumer;
import java.util.stream.Stream;

/**
 * "David FALCON", the in-app chat assistant (mirrors backend/chatbot.py).
 * Same OpenAI-compatible endpoint family as {@link Clues}; replies stream
 * chunk by chunk, with an optional {@code <think>} block filter
 * ({@code CHATBOT_THINK_FILTER}).
 */
public final class ChatBot {
    public static final double DEFAULT_TIMEOUT = 120.0;
    public static final double TEMPERATURE = 0.5;
    public static final int MAX_TOKENS = 1024;
    static final String THINK_OPEN = "<think>";
    static final String THINK_CLOSE = "</think>";
    public static final List<String> CHATBOT_THINK_FILTER_CHOICES = List.of("open_close", "close_only", "none");
    public static final String DEFAULT_CHATBOT_THINK_FILTER = "open_close";
    static final Path DOC_USER_PATH = Env.path("DOC_USER", "EN", "ReadMe.md");
    // Routing of a question asked while a playable grid is on screen (see
    // classifyQuestion): a first, non-streamed LLM call answers with one of
    // these two keywords, and the reply is then written from a prompt holding
    // only what that kind of question needs. Anything else the classifier
    // answers (or a failed call) falls back to the combined prompt.
    public static final String ROUTE_USAGE = "USAGE";
    public static final String ROUTE_CLUE = "CLUE";
    // An explicit request for the answer of a word: written from the CLUE
    // prompt, never checked for leaks (it IS the answer).
    public static final String ROUTE_ANSWER = "ANSWER";
    static final double CLASSIFY_TEMPERATURE = 0.0;
    // Room for a model that reasons a few lines before answering anyway.
    static final int CLASSIFY_MAX_TOKENS = 200;
    static final double CLASSIFY_TIMEOUT = 30.0;
    // Earlier messages shown to the classifier, each cut to
    // CLASSIFY_CONTEXT_CHARS.
    static final int CLASSIFY_HISTORY_MESSAGES = 2;
    static final int CLASSIFY_CONTEXT_CHARS = 300;
    // Earlier messages kept in a CLUE reply's prompt.
    static final int CLUE_HISTORY_MESSAGES = 4;
    // Shortest root of the answer a CLUE prompt forbids in a hint.
    static final int CLUE_STEM_MIN_LETTERS = 4;
    // Attempts at a hint that gives no part of the answer before its leaked
    // words are masked (see checkedHint).
    static final int CLUE_HINT_ATTEMPTS = 3;
    // Dictionary grounding of a CLUE prompt: at most this many base forms of
    // the answer, and this many definitions for each.
    static final int CLUE_GLOSS_LEMMAS = 2;
    static final int CLUE_GLOSSES_PER_LEMMA = 3;
    // Example synonyms of a CLUE prompt, found by a Qdrant similarity search
    // (see qdrantSynonyms).
    static final int CLUE_SYNONYM_COUNT = 3;
    static final double CLUE_SYNONYM_MIN_SCORE = 0.80;
    static final int CLUE_SYNONYM_POOL = 100;
    // The lowest similarity kept between an example and the definitions of
    // the answer's base form, and how many of those definitions are embedded.
    static final double CLUE_SYNONYM_MIN_MEANING = 0.40;
    static final int CLUE_SYNONYM_GLOSSES = 2;
    static final double CLUE_SYNONYM_TIMEOUT = 5.0;
    // Real sentences of the reference corpus shown in a CLUE prompt's
    // SENTENCES section (see corpusSentences).
    static final int CLUE_SENTENCE_COUNT = 3;
    private static volatile QdrantStore synonymStore;
    private static volatile Embedder synonymEmbedder;
    private static final java.util.regex.Pattern COMBINING_RE = java.util.regex.Pattern.compile("\\p{Mn}+");
    private static final java.util.regex.Pattern ROUTE_RE = java.util.regex.Pattern.compile(
            "\\b(USAGE|CLUE|ANSWER)\\b", java.util.regex.Pattern.UNICODE_CHARACTER_CLASS);

    // A markup tag in a chat reply; dropped by TagStripper, its text kept.
    static final java.util.regex.Pattern TAG_RE = java.util.regex.Pattern.compile(
            "</?[A-Za-z][A-Za-z0-9_:-]*(?:\\s[^<>]*)?/?>", java.util.regex.Pattern.UNICODE_CHARACTER_CLASS);
    // Longest text after a "<" still awaited as a possible tag.
    static final int MAX_TAG_CHARS = 64;

    /** A classifier verdict: the keyword (null when undetermined), the raw answer and the request sent. */
    public record Route(String route, String raw, List<Object> messages) {}
    private static volatile String docUserCache;

    public static final class ChatError extends RuntimeException {
        public ChatError(String msg, Throwable cause) {
            super(msg, cause);
        }
    }

    public final String baseUrl;
    public final String model;
    public final String apiKey;
    public final String thinkFilter;

    public ChatBot(String baseUrl, String model, String apiKey) {
        this.baseUrl = baseUrl != null && !baseUrl.isEmpty() ? baseUrl : Env.get("LLM_BASE_URL", Clues.DEFAULT_LLM_BASE_URL);
        this.model = model != null && !model.isEmpty() ? model : Env.get("LLM_MODEL", Clues.DEFAULT_LLM_MODEL);
        this.apiKey = apiKey != null && !apiKey.isEmpty() ? apiKey : Env.get("LLM_API_KEY", Clues.DEFAULT_LLM_API_KEY);
        String tf = Env.get("CHATBOT_THINK_FILTER", DEFAULT_CHATBOT_THINK_FILTER);
        if (!CHATBOT_THINK_FILTER_CHOICES.contains(tf)) {
            Log.warning("CHATBOT_THINK_FILTER=%s is not one of %s — falling back to %s.", Log.repr(tf),
                    CHATBOT_THINK_FILTER_CHOICES, Log.repr(DEFAULT_CHATBOT_THINK_FILTER));
            tf = DEFAULT_CHATBOT_THINK_FILTER;
        }
        this.thinkFilter = tf;
    }

    static int longestTagPrefixSuffix(String buffer, String tag) {
        int maxLen = Math.min(buffer.length(), tag.length() - 1);
        for (int len = maxLen; len > 0; len--) {
            if (buffer.endsWith(tag.substring(0, len))) return len;
        }
        return 0;
    }

    static String loadDocUser() {
        if (docUserCache == null) {
            try {
                docUserCache = Files.readString(DOC_USER_PATH, StandardCharsets.UTF_8);
            } catch (IOException e) {
                Log.warning("could not read %s: %s", DOC_USER_PATH, e.getMessage());
                docUserCache = "";
            }
        }
        return docUserCache;
    }

    /** Intro + DOC_USER: the fixed head shared by the combined and USAGE prompts. */
    static String fixedPromptHead(String docUser) {
        return "You are David FALCON, the friendly in-app assistant of CrossWordFalcon, a "
                + "crossword-puzzle web app. You help the player use the interface and solve the "
                + "crossword grid currently on screen (explaining a clue, giving a hint, or, if "
                + "explicitly asked, the answer itself).\n\n"
                + "Reference documentation for how the interface itself works "
                + "(frontend/static/index.html and script.js, described here for a player, not a "
                + "developer). This documentation is written in English, but that is NOT the "
                + "language to reply in: use its content to answer, rephrased in your own words in "
                + "the player's language set by the rules below — never copy an English passage "
                + "from it into your reply:\n"
                + docUser + "\n\n";
    }

    /** The last routing keyword in a classifier answer, or null. */
    static String parseRoute(String content) {
        java.util.regex.Matcher m = ROUTE_RE.matcher(content.toUpperCase(java.util.Locale.ROOT));
        String last = null;
        while (m.find()) last = m.group(1);
        return last;
    }

    /** {@code content} without a {@code <think>...</think>} block. */
    static String stripThinkBlock(String content) {
        int idx = content.lastIndexOf(THINK_CLOSE);
        if (idx >= 0) return content.substring(idx + THINK_CLOSE.length());
        if (content.contains(THINK_OPEN)) return "";
        return content;
    }

    /** The last {@code count} messages of {@code history}, never starting on an assistant turn. */
    static List<Object> trimHistory(List<Object> history, int count) {
        List<Object> kept = new ArrayList<>(count > 0
                ? history.subList(Math.max(0, history.size() - count), history.size()) : List.of());
        while (!kept.isEmpty() && !"user".equals(Json.get(kept.get(0), "role"))) kept.remove(0);
        return kept;
    }

    /** Python's {@code s[:n]} (code points). */
    static String cpHead(String s, int n) {
        int len = s.codePointCount(0, s.length());
        return n >= len ? s : s.substring(0, s.offsetByCodePoints(0, Math.max(0, n)));
    }

    /** The answer's canonical forms, from a list or a ";"-separated string. */
    static List<String> canonicalForms(Object word) {
        Object c = Json.get(word, "canonical");
        List<String> out = new ArrayList<>();
        if (c instanceof String str) {
            for (String part : str.split(";", -1)) if (!part.isEmpty()) out.add(part);
        } else if (Json.truthy(c)) {
            for (Object o : Json.asList(c)) out.add(o.toString());
        }
        return out;
    }

    /** Inflected forms and base forms of the answer: wordlist rows first, then the word's own fields (see chatbot.py _word_forms). */
    static List<List<String>> wordForms(Object word, String language) {
        String answer = Json.str(word, "answer", "");
        List<String> forms = new ArrayList<>(), bases = new ArrayList<>();
        if (!answer.isEmpty()) {
            for (Map.Entry<String, List<String>> row : DictionaryLookup.wordForms(answer, language)) {
                forms.add(row.getKey());
                bases.addAll(row.getValue());
            }
        }
        Object acc = Json.get(word, "accented");
        if (Json.truthy(acc)) forms.add(acc.toString());
        bases.addAll(canonicalForms(word));
        return List.of(uniqueCi(forms), uniqueCi(bases));
    }

    static List<String> uniqueCi(List<String> items) {
        Set<String> seen = new HashSet<>();
        List<String> out = new ArrayList<>();
        for (String item : items) {
            if (item != null && !item.isEmpty() && seen.add(item.toLowerCase(java.util.Locale.ROOT))) out.add(item);
        }
        return out;
    }

    record Grounding(List<String> grammar, List<String[]> meanings, List<String> baseForms) {}

    /** Parts of speech, base-form definitions and base forms of the answer (see chatbot.py _clue_grounding). */
    static Grounding clueGrounding(Object word, String language) {
        String answer = Json.str(word, "answer", "");
        List<List<String>> wf = wordForms(word, language);
        List<String> forms = wf.get(0), canonical = wf.get(1);
        List<InflectionLookup.Analysis> analyses = new ArrayList<>();
        for (String form : forms.isEmpty() ? List.of(answer) : forms) {
            if (form.isEmpty()) continue;
            for (InflectionLookup.Analysis a : InflectionLookup.describeForm(form, language)) {
                if (!analyses.contains(a)) analyses.add(a);
            }
        }
        List<String> grammar = new ArrayList<>();
        Set<String> poses = new HashSet<>();
        for (InflectionLookup.Analysis a : analyses) {
            String text = a.description();
            int comma = text.indexOf(',');
            String label = comma >= 0 ? text.substring(0, comma) : text;
            int paren = label.indexOf(" (");
            if (paren >= 0) label = label.substring(0, paren);
            if (!label.isEmpty() && !grammar.contains(label)) grammar.add(label);
            if (a.pos() != null && !a.pos().isEmpty()) poses.add(a.pos());
        }
        Set<String> own = new HashSet<>();
        for (String f : forms) own.add(f.toLowerCase(java.util.Locale.ROOT));
        own.add(answer.toLowerCase(java.util.Locale.ROOT));
        List<String> baseForms = new ArrayList<>();
        for (String c : canonical) {
            if (!own.contains(c.toLowerCase(java.util.Locale.ROOT))) baseForms.add(c);
        }
        List<String[]> meanings = new ArrayList<>();
        List<String> lemmas = canonical.subList(0, Math.min(CLUE_GLOSS_LEMMAS, canonical.size()));
        for (Map.Entry<String, List<Object>> e : GlossLookup.findGlossesForCanonicals(lemmas, language).entrySet()) {
            List<Object> matching = new ArrayList<>();
            for (Object entry : e.getValue()) {
                if (poses.contains(Json.str(entry, "pos", null))) matching.add(entry);
            }
            int kept = 0;
            for (Object entry : matching.isEmpty() ? e.getValue() : matching) {
                Object pos = Json.get(entry, "pos");
                for (Object gloss : Json.listOrEmpty(Json.get(entry, "glosses"))) {
                    if (kept < CLUE_GLOSSES_PER_LEMMA) {
                        meanings.add(new String[] {e.getKey(), Json.truthy(pos) ? pos.toString() : "?", gloss.toString()});
                        kept++;
                    }
                }
            }
        }
        return new Grounding(grammar, meanings, baseForms);
    }

    /**
     * Up to CLUE_SENTENCE_COUNT reference-corpus sentences using the answer's
     * exact inflected form — the first of its forms that has any (the same
     * lookup as the clue generator's examples, see chatbot.py _corpus_sentences).
     */
    static List<String> corpusSentences(Object word, String language) {
        List<String> forms = wordForms(word, language).get(0);
        List<String> tried = forms.isEmpty()
                ? List.of(Json.str(word, "answer", "").toLowerCase(java.util.Locale.ROOT)) : forms;
        for (String form : tried) {
            if (form.isEmpty()) continue;
            List<String> sentences = ExampleSentences.findExamplesForWords(List.of(form), language, CLUE_SENTENCE_COUNT).get(form);
            if (sentences != null && !sentences.isEmpty()) return sentences;
        }
        return List.of();
    }

    /**
     * Example synonyms of the answer for the CLUE prompt, as {base form,
     * definition}: neighbours over one search per grid form, inflected form
     * and base form, scored by their best search score and by their
     * similarity to the base form's definitions (see chatbot.py
     * _qdrant_synonyms). Empty when Qdrant or the embedding server is
     * unavailable.
     */
    static List<String[]> qdrantSynonyms(Object word, String language) {
        String answer = Json.str(word, "answer", "");
        List<List<String>> wf = wordForms(word, language);
        List<String> forms = wf.get(0), canonical = wf.get(1);
        List<String[]> meanings = clueGrounding(word, language).meanings();
        String lemma = meanings.isEmpty() ? "" : meanings.get(0)[0];
        List<String> glosses = new ArrayList<>();
        for (String[] m : meanings) if (m[0].equals(lemma) && glosses.size() < CLUE_SYNONYM_GLOSSES) glosses.add(m[2]);
        String meaning = String.join(" ", glosses);
        if (meaning.isEmpty()) return List.of();
        java.util.TreeSet<String> forbidden = new java.util.TreeSet<>();
        String af = gridForm(answer);
        if (!af.isEmpty()) forbidden.add(af);
        for (String c : canonical) {
            String f = gridForm(c);
            if (!f.isEmpty()) forbidden.add(f);
        }
        List<String> roots = new ArrayList<>();
        for (String f : forbidden) {
            String r = root(f);
            if (r.codePointCount(0, r.length()) >= CLUE_STEM_MIN_LETTERS) roots.add(r);
        }
        List<String> queries = new ArrayList<>();
        List<String> all = new ArrayList<>();
        all.add(answer);
        all.addAll(forms);
        all.addAll(canonical);
        for (String q : all) if (!q.isEmpty() && !queries.contains(q)) queries.add(q);
        Map<String, Double> bestScore = new java.util.LinkedHashMap<>();
        Map<String, Object> bestPayload = new java.util.LinkedHashMap<>();
        List<String[]> candidates = new ArrayList<>();
        double[] meaningVector;
        Map<String, double[]> vectors;
        try {
            if (synonymStore == null) {
                synonymStore = new QdrantStore(CLUE_SYNONYM_TIMEOUT);
                synonymEmbedder = new Embedder(null, null, null, CLUE_SYNONYM_TIMEOUT);
            }
            for (String query : queries) {
                for (Object hit : synonymStore.search(synonymEmbedder.embed(query), language, CLUE_SYNONYM_POOL, 0)) {
                    Object payload = Json.get(hit, "payload");
                    Object wo = Json.get(payload, "word");
                    Object sc = Json.get(hit, "score");
                    double score = sc instanceof Number n ? n.doubleValue() : 0.0;
                    if (!Json.truthy(wo)) continue;
                    String w = wo.toString();
                    Double prev = bestScore.get(w);
                    if (prev == null || score > prev) {
                        bestScore.put(w, score);
                        bestPayload.put(w, payload);
                    }
                }
            }
            List<String> ranked = new ArrayList<>(bestScore.keySet());
            ranked.sort((x, y) -> Double.compare(bestScore.get(y), bestScore.get(x)));
            Set<String> seen = new HashSet<>();
            for (String w : ranked) {
                if (bestScore.get(w) < CLUE_SYNONYM_MIN_SCORE) break;
                Object payload = bestPayload.get(w);
                Object can = Json.get(payload, "canonical");
                Object pacc = Json.get(payload, "accented");
                String baseRaw = Json.truthy(can) ? can.toString() : Json.truthy(pacc) ? pacc.toString() : w;
                List<String> bases = new ArrayList<>();
                for (String b : baseRaw.split(";", -1)) if (!b.isEmpty()) bases.add(b);
                List<String> hitForms = new ArrayList<>();
                hitForms.add(gridForm(w));
                for (String b : bases) hitForms.add(gridForm(b));
                boolean skip = false;
                for (String f : hitForms) {
                    if (forbidden.contains(f)) skip = true;
                    for (String r : roots) skip |= f.startsWith(r);
                }
                if (skip) continue;
                String base = bases.isEmpty() ? w : bases.get(0);
                int first = base.isEmpty() ? -1 : base.codePointAt(0);
                if (seen.contains(base) || (first >= 0 && Character.isUpperCase(first))) continue;
                seen.add(base);
                List<Object> entries = GlossLookup.findGlossesForCanonicals(List.of(base), language).getOrDefault(base, List.of());
                String gloss = null;
                for (Object e : entries) {
                    List<Object> gl = Json.listOrEmpty(Json.get(e, "glosses"));
                    if (!gl.isEmpty()) {
                        gloss = String.valueOf(gl.get(0));
                        break;
                    }
                }
                if (gloss != null && !gloss.isEmpty()) candidates.add(new String[] {w, base, gloss});
            }
            meaningVector = candidates.isEmpty() ? new double[0] : synonymEmbedder.embed(meaning);
            List<String> ws = new ArrayList<>();
            for (String[] c : candidates) ws.add(c[0]);
            vectors = synonymStore.retrieveWordVectors(language, ws, 2000);
        } catch (QdrantStore.QdrantStoreError | Embedder.EmbedderError e) {
            Log.warning("chat: Qdrant synonyms unavailable for %s: %s", Log.repr(answer), e.getMessage());
            return List.of();
        }
        List<String[]> found = new ArrayList<>();
        for (String[] c : candidates) {
            double[] v = vectors.get(c[0]);
            if (v == null) continue;
            double sim = 0.0;
            for (int k = 0; k < Math.min(v.length, meaningVector.length); k++) sim += v[k] * meaningVector[k];
            if (sim < CLUE_SYNONYM_MIN_MEANING) continue;
            found.add(new String[] {c[1], c[2]});
            if (found.size() >= CLUE_SYNONYM_COUNT) break;
        }
        List<String> shown = new ArrayList<>();
        for (String[] f : found) shown.add(f[0]);
        Log.info("chat: Qdrant synonyms for %s (queries %s): %s", Log.repr(answer), queries, shown);
        return found;
    }

    /** {@code text} as the grid writes it: ligatures split, accents dropped, uppercase. */
    static String gridForm(String text) {
        text = text.replace("œ", "oe").replace("Œ", "OE").replace("æ", "ae").replace("Æ", "AE");
        text = COMBINING_RE.matcher(java.text.Normalizer.normalize(text, java.text.Normalizer.Form.NFKD)).replaceAll("");
        return text.toUpperCase(java.util.Locale.ROOT);
    }

    /** {start, end} (char indices) of every run of letters in {@code text}. */
    static List<int[]> letterRuns(String text) {
        List<int[]> runs = new ArrayList<>();
        int start = -1;
        for (int i = 0; i < text.length(); ) {
            int cp = text.codePointAt(i);
            if (Character.isLetter(cp)) {
                if (start < 0) start = i;
            } else if (start >= 0) {
                runs.add(new int[] {start, i});
                start = -1;
            }
            i += Character.charCount(cp);
        }
        if (start >= 0) runs.add(new int[] {start, text.length()});
        return runs;
    }

    /** The word minus its last two letters, never shorter than CLUE_STEM_MIN_LETTERS. */
    static String root(String word) {
        int n = word.codePointCount(0, word.length());
        return cpHead(word, Math.max(CLUE_STEM_MIN_LETTERS, n - 2));
    }

    /** Spans of the words of hint {@code text} giving away part of the answer (see chatbot.py _hint_leak_spans). */
    static List<int[]> hintLeakSpans(String text, Object word, String message, String language) {
        List<String> forbidden = new ArrayList<>();
        String answer = gridForm(Json.str(word, "answer", ""));
        if (!answer.isEmpty()) forbidden.add(answer);
        for (String c : wordForms(word, language).get(1)) {
            String f = gridForm(c);
            if (!f.isEmpty()) forbidden.add(f);
        }
        List<String> roots = new ArrayList<>();
        for (String f : forbidden) {
            String r = root(f);
            if (r.codePointCount(0, r.length()) >= CLUE_STEM_MIN_LETTERS) roots.add(r);
        }
        Set<String> allowed = new HashSet<>();
        for (int[] run : letterRuns(message)) allowed.add(gridForm(message.substring(run[0], run[1])));
        Set<String> bases = new HashSet<>();
        for (String c : wordForms(word, language).get(1)) bases.add(c.toLowerCase(java.util.Locale.ROOT));
        List<int[]> spans = new ArrayList<>();
        for (int[] run : letterRuns(text)) {
            String raw = text.substring(run[0], run[1]);
            String token = gridForm(raw);
            if (allowed.contains(token)) continue;
            boolean leak = forbidden.contains(token);
            for (String r : roots) leak |= token.startsWith(r);
            if (!leak && token.codePointCount(0, token.length()) >= CLUE_STEM_MIN_LETTERS) {
                // Another form of one of the answer's base forms, through the wordlist.
                for (Map.Entry<String, List<String>> row : DictionaryLookup.wordForms(raw, language)) {
                    for (String c : row.getValue()) leak |= bases.contains(c.toLowerCase(java.util.Locale.ROOT));
                }
            }
            if (leak) spans.add(run);
        }
        return spans;
    }

    static int ival(Object w, String key) {
        return Json.integer(w, key, 0);
    }

    static String formatWordsBlock(List<Object> words) {
        List<String> lines = new ArrayList<>();
        for (Object w : words) {
            String direction = "down".equals(Json.get(w, "direction")) ? "Down" : "Across";
            Object clue = Json.get(w, "clue");
            Object lang = Json.get(w, "language");
            lines.add("- (" + (ival(w, "row") + 1) + ", " + (ival(w, "col") + 1) + ") " + direction + ": clue="
                    + Log.repr(Json.truthy(clue) ? clue.toString() : "(none yet)") + ", answer="
                    + Log.repr(Json.str(w, "answer", "")) + ", language="
                    + Log.repr(Json.truthy(lang) ? lang.toString() : "?"));
        }
        return String.join("\n", lines);
    }

    static List<Object> wordsTouchingCell(Object cell, List<Object> words) {
        List<Object> matches = new ArrayList<>();
        if (!Json.truthy(cell)) return matches;
        int row = ival(cell, "row"), col = ival(cell, "col");
        for (Object w : words) {
            int wr = ival(w, "row"), wc = ival(w, "col");
            Object a = Json.get(w, "answer");
            int length = Json.truthy(a) ? a.toString().length() : 0;
            if ("down".equals(Json.get(w, "direction"))) {
                if (col == wc && wr <= row && row < wr + length) matches.add(w);
            } else if (row == wr && wc <= col && col < wc + length) {
                matches.add(w);
            }
        }
        return matches;
    }

    static Object findWordByStart(Object start, List<Object> words) {
        if (!Json.truthy(start)) return null;
        int row = ival(start, "row"), col = ival(start, "col");
        Object direction = Json.get(start, "direction");
        for (Object w : words) {
            if (ival(w, "row") == row && ival(w, "col") == col && java.util.Objects.equals(Json.get(w, "direction"), direction)) {
                return w;
            }
        }
        return null;
    }

    record Selection(boolean puzzleLoaded, List<Object> words, Object hoveredWord, Object fillingCell,
                     Object activeDirection, Object hoveredResolved, List<Object> fillingWords, Object fillingWord,
                     Object helpWord) {}

    static Selection resolveSelection(Map<String, Object> ui) {
        boolean puzzleLoaded = Json.truthy(ui.get("puzzle_loaded"));
        List<Object> words = Json.listOrEmpty(ui.get("words"));
        Object hovered = ui.get("hovered_word");
        Object filling = ui.get("filling_cell");
        Object activeDirection = ui.get("active_direction");
        Object hoveredResolved = Json.truthy(hovered) ? findWordByStart(hovered, words) : null;
        List<Object> fillingWords = Json.truthy(filling) && puzzleLoaded ? wordsTouchingCell(filling, words) : new ArrayList<>();
        Object fillingWord = null;
        if (fillingWords.size() == 1) {
            fillingWord = fillingWords.get(0);
        } else if (fillingWords.size() >= 2 && Json.truthy(activeDirection)) {
            for (Object w : fillingWords) {
                if (activeDirection.equals(Json.get(w, "direction"))) {
                    fillingWord = w;
                    break;
                }
            }
        }
        return new Selection(puzzleLoaded, words, hovered, filling, activeDirection, hoveredResolved, fillingWords,
                fillingWord, hoveredResolved != null ? hoveredResolved : fillingWord);
    }

    public String buildSystemPrompt(String language, Map<String, Object> ui) {
        String docUser = loadDocUser();
        Selection sel = resolveSelection(ui);
        boolean puzzleLoaded = sel.puzzleLoaded();
        Object fillingCell = sel.fillingCell();
        Object activeDirection = sel.activeDirection();
        List<Object> words = sel.words();
        List<String> stateLines = new ArrayList<>();
        stateLines.add("A crossword puzzle is currently loaded: " + (puzzleLoaded ? "True" : "False") + ".");
        List<Object> fillingWords = sel.fillingWords();
        Object hoveredResolved = sel.hoveredResolved();
        Object fillingWord = sel.fillingWord();
        Set<Object> wordLanguages = new HashSet<>();
        for (Object w : words) {
            Object l = Json.get(w, "language");
            if (Json.truthy(l)) wordLanguages.add(l);
        }
        boolean isBilingualGrid = wordLanguages.size() > 1;
        Object helpWord = sel.helpWord();
        Object helpLang = helpWord != null ? Json.get(helpWord, "language") : null;
        String helpDirLabel = helpWord != null && "down".equals(Json.get(helpWord, "direction"))
                ? "DOWN (vertical)" : "ACROSS (horizontal)";
        String replyLanguage = puzzleLoaded && Json.truthy(helpLang) ? helpLang.toString() : language;
        boolean replyLanguageDiffers = !replyLanguage.equals(language);
        String languageName = Clues.LANGUAGE_NAMES.getOrDefault(replyLanguage, replyLanguage);
        String interfaceLanguageName = Clues.LANGUAGE_NAMES.getOrDefault(language, language);
        if (puzzleLoaded) {
            if (!words.isEmpty()) {
                stateLines.add("Every word currently in the grid, with its starting (row, column) "
                        + "(1-based, matching the grid's own on-screen headers), direction, clue, "
                        + "and answer:\n" + formatWordsBlock(words));
            }
            if (Json.truthy(fillingCell)) {
                stateLines.add("Separately (this is NOT the hovered word further below), the player "
                        + "has clicked cell (row " + (ival(fillingCell, "row") + 1) + ", column "
                        + (ival(fillingCell, "col") + 1) + ") to type an answer into.");
                if (!fillingWords.isEmpty()) {
                    String crossingNote;
                    if (fillingWords.size() >= 2 && fillingWord != null) {
                        crossingNote = " Two words cross at that cell, but the grid is in "
                                + ("down".equals(activeDirection) ? "DOWN (vertical)" : "ACROSS (horizontal)")
                                + " fill mode, so the word being filled — and the one any hint/help "
                                + "is about — is the "
                                + ("down".equals(Json.get(fillingWord, "direction")) ? "DOWN (vertical)" : "ACROSS (horizontal)")
                                + " one: (row " + (ival(fillingWord, "row") + 1) + ", column "
                                + (ival(fillingWord, "col") + 1) + "). Do NOT ask the player which "
                                + "word — it is that one.";
                    } else if (fillingWords.size() >= 2) {
                        crossingNote = " Two words cross at that cell and the grid's fill direction is "
                                + "unknown — ask the player which of the two they mean, unless they "
                                + "already said.";
                    } else {
                        crossingNote = "";
                    }
                    stateLines.add("The word(s) occupying that exact clicked cell right now (the "
                            + "word being filled in). If the player asks for a hint and NO word "
                            + "is hovered (see below), this is the word the hint is about."
                            + crossingNote
                            + " The line(s) below carry 'clue=' and "
                            + "'answer=' for your internal use — NEVER quote such a line back to "
                            + "the player, and never let its 'answer=' value appear in your "
                            + "reply:\n"
                            + formatWordsBlock(fillingWords));
                } else {
                    stateLines.add("No listed word currently covers that exact clicked cell "
                            + "(unexpected — treat this as if no cell were selected for typing).");
                }
            } else {
                stateLines.add("No cell is currently selected for typing (separate from the hovered "
                        + "word further below).");
            }
            if (Json.truthy(sel.hoveredWord())) {
                if (hoveredResolved != null) {
                    stateLines.add("The word currently under the player's mouse cursor (hover) — "
                            + "THIS is the answer to \"what word is selected\" / \"quel est le "
                            + "mot sélectionné\", NOT the filling-cell state above:\n"
                            + formatWordsBlock(List.of(hoveredResolved)));
                } else {
                    stateLines.add("The player's mouse is over a word in the grid, but it could not "
                            + "be matched against the word list (unexpected) — treat this as if "
                            + "no word were hovered.");
                }
            } else {
                boolean noTarget = fillingWords.isEmpty();
                stateLines.add("No word is currently under the player's mouse (hover) right now. If "
                        + "the player asks what word is selected/hovered ('mot sélectionné'), "
                        + "you MUST reply that no word is currently hovered and ask them to "
                        + "move their mouse over a word in the grid or a clue first — do NOT "
                        + "name any word from the list above as if it were hovered, not even "
                        + "the first one listed."
                        + (noTarget ? " The same applies to a HINT request: with no word hovered AND no "
                        + "cell clicked (see above), you have NO word to give a hint for — "
                        + "you MUST ask the player to hover a word or click a cell first, "
                        + "and say NOTHING else. Do NOT pick a word from the list, do NOT "
                        + "use a word from the examples in rule 4, do NOT give any hint." : ""));
            }
            if (isBilingualGrid && replyLanguageDiffers) {
                stateLines.add("This is a BILINGUAL grid. The word currently selected in it (the "
                        + helpDirLabel + " word) is written in " + languageName + " — DIFFERENT from "
                        + "the interface language (" + interfaceLanguageName + "). Write your ENTIRE "
                        + "reply to this message (preamble, hint/definition/answer, and every "
                        + "other sentence) in " + languageName + ". Do NOT use "
                        + interfaceLanguageName + " anywhere in this reply.");
            } else if (isBilingualGrid) {
                stateLines.add("This is a BILINGUAL grid (across and down words are in two different "
                        + "languages). The currently-selected word, if any, is in " + languageName + ": "
                        + "reply in " + languageName + " as usual.");
            }
        }
        String finalReminder = "write your entire reply in " + languageName + ", "
                + "starting directly with the answer and no greeting."
                + (puzzleLoaded ? " And if the player asked for a HINT (not an explicit answer request), the "
                + "solution word must NOT appear anywhere in your reply — never end a hint "
                + "with \"the answer is ...\", never spell it out, never confirm it, never give "
                + "any of its letters. A hint gives a synonym of another root or a new "
                + "definition, never just a paraphrase of its clue." : "");
        String selectedWordBlock;
        if (!puzzleLoaded) {
            selectedWordBlock = "";
        } else if (helpWord == null) {
            selectedWordBlock = "\n\n==================================================\n"
                    + "SELECTED WORD RIGHT NOW: none (nothing hovered, no single word at a "
                    + "clicked cell). A request for a hint/help/the answer that names no word "
                    + "must be met by asking the player to hover a word or click a cell — never "
                    + "by reusing a word from an earlier reply.\n"
                    + "==================================================";
        } else {
            int swr = ival(helpWord, "row") + 1, swc = ival(helpWord, "col") + 1;
            String swk = hoveredResolved != null ? "the word under the player's mouse (hovered)"
                    : "the word at the cell the player clicked";
            selectedWordBlock = "\n\n==================================================\n"
                    + "SELECTED WORD RIGHT NOW: the " + helpDirLabel + " word starting at (row "
                    + swr + ", column " + swc + ") — " + swk + ". Its clue and answer are its own line in "
                    + "the word list above.\n"
                    + "Any request for a hint / help / the answer that does not explicitly name "
                    + "a DIFFERENT word is about THIS " + helpDirLabel + " word at (row " + swr + ", "
                    + "column " + swc + "). Do NOT answer about a word from an earlier reply in this "
                    + "conversation — the player may have selected a new one since.\n"
                    + "==================================================";
        }
        String preambleLangNote = replyLanguageDiffers
                ? " Write this preamble in " + languageName + " as well: the French example above "
                + "shows the STYLE only, so do NOT copy its French words — in " + languageName + " it "
                + "would read, for instance, \"Hint for the vertical word at (l, c), the one at "
                + "the clicked cell:\" (and, for an ANSWER request in rule 4e, \"The vertical word "
                + "at (l, c) is: …\")."
                : "";
        // Everything that never varies between two requests (the
        // introduction and the whole DOC_USER text) comes first, so the LLM
        // server's prefix cache reuses it across every chat request.
        return fixedPromptHead(docUser)
                + "Write EVERY reply entirely in " + languageName + ". This is not optional and applies "
                + "to every message you ever send.\n\n"
                + "STRICT RULES:\n"
                + "1. Always reply extremely politely.\n"
                + "2. LANGUAGE. Your entire reply MUST be written in " + languageName + " — every word "
                + "of it, not just the first sentence. This holds no matter what language the "
                + "player writes to you in: reply in " + languageName + " anyway. It also holds even "
                + "though many examples in these rules happen to be written in French — those "
                + "French snippets illustrate FORMAT and WORDING STYLE only, never the language "
                + "to answer in. If " + languageName + " is not French, do NOT reply in French. "
                + "The same goes for the reference documentation above: it is in English, but "
                + "whatever you take from it must be written in " + languageName + ", rephrased, never "
                + "quoted in English."
                + (isBilingualGrid
                ? " " + languageName + " is already the language of the direction currently "
                + "selected in this bilingual grid (the across and down words are in two "
                + "different languages, and this whole prompt is built in whichever one the "
                + "selected direction uses) — there is no exception to weigh and nothing to "
                + "switch mid-reply: the WHOLE reply is in " + languageName + ", start to finish.\n"
                : "\n")
                + "3. You must ONLY answer questions about using this interface, or about solving/"
                + "understanding the crossword grid currently on screen. For ANY other question "
                + "(general knowledge, other software, personal questions, anything unrelated to "
                + "this app or its current grid — e.g. 'what is the capital of...'), politely "
                + "decline and suggest the player consult an appropriate website or resource for "
                + "that topic instead. Do this and NOTHING else: never actually answer the "
                + "out-of-scope question afterward, not even briefly, not even after declining — "
                + "declining and then still giving the answer right after is exactly what this "
                + "rule forbids.\n"
                + "4. Helping with a word. FIRST decide which kind of request it is. If the "
                + "player EXPLICITLY asks for the answer/solution/exact word (e.g. 'donne-moi la "
                + "réponse', 'la réponse exacte', 'quel est le mot exact', 'quelle est la "
                + "solution', 'dis-moi le mot'), that is an ANSWER request: skip straight to "
                + "rule 4e and give the answer. Otherwise ('un indice', 'aide-moi', 'je suis "
                + "bloqué', a question about a clue — anything not explicitly asking for the "
                + "answer) it is a HINT request; if genuinely unsure, treat it as a HINT. "
                + "Handle a HINT request with steps a–d below; handle an ANSWER request with "
                + "step e:\n"
                + "   a. FIRST work out WHICH word the hint is about. Every one of the player's "
                + "messages is prefixed with a short 'NOTE — right now the player has ... "
                + "selected' line stating which word is selected AT THE MOMENT of that message. "
                + "Trust that NOTE above everything else: it is what is true NOW, even if an "
                + "earlier reply of yours in this conversation was about a different word — the "
                + "player may have selected a new one since. NEVER carry a previous reply's word "
                + "into a new one. If the NOTE and the fuller interface state below ever seem to "
                + "disagree, the NOTE wins. Failing a NOTE, use the interface state below:\n"
                + "      - If a word is under the player's mouse (the hovered / 'selected' word — "
                + "'mot sélectionné' — see the state below), the hint is about THAT word.\n"
                + "      - Otherwise, if the player has a cell clicked for typing (the 'filling' "
                + "cell in the state below), the hint is about the word — or the two crossing "
                + "words — at that clicked cell.\n"
                + "      - If the player's own message names a word (a clue number, a position "
                + "like '3 horizontal', a direction), use that word instead.\n"
                + "      - If NONE of these identifies a word (nothing hovered, no cell clicked, "
                + "and the message does not say which word), do NOT guess and do NOT pick a word "
                + "from the list: politely ask the player to move their mouse over a word (or a "
                + "clue), or to click a cell of the word they need help with, and stop there.\n"
                + "      - If a cell is clicked at the crossing of two words and nothing is "
                + "hovered, say so and ask which of the two they mean (or, if both hints are "
                + "short, give one for each, each clearly labelled).\n"
                + "   b. Begin with a SHORT preamble naming WHICH word it is about — ONLY its "
                + "starting (row, column), its direction, and whether it is the word under the "
                + "mouse (hovered / selected) or the word at the clicked cell — so the player "
                + "can correct you. One short clause, e.g. « Indice pour le mot vertical en "
                + "(l, c), celui de la case cliquée : »."
                + preambleLangNote
                + " The preamble must contain NOTHING else: "
                + "do NOT repeat the on-screen clue text in it, do NOT include the word's answer, "
                + "and NEVER paste a raw line from the interface state below (those lines contain "
                + "'clue=' and 'answer=' fields that must not appear in your reply). This "
                + "preamble is NOT the hint. (If the word's clue shows '(none yet)', just say its "
                + "clue has not been generated yet.)\n"
                + "   c. THE HINT MUST HELP FIND THE WORD WITHOUT GIVING ANY PART OF IT. Unless "
                + "the player explicitly asks for it, never give a letter of the solution (not "
                + "its first letter, not its last letter, no letter at all), its letter count, "
                + "or a word of its family. Instead give ONE of these: one or two synonyms of "
                + "the word that do NOT share its root (not the same canonical form, not the "
                + "same word family), or a NEW definition of your own, clearly DIFFERENT from "
                + "the clue the player already sees on screen (another angle: what it is used "
                + "for, the category it belongs to, a typical example of it). It is forbidden to "
                + "copy the existing 'clue=' text, to lightly reword or reorder it, or to reply "
                + "with just the position + that clue (the player already has every bit of "
                + "that). You MAY confirm or deny one specific letter the player proposes.\n"
                + "   c-bis. Describe the word EXACTLY as it appears in the grid — its part of "
                + "speech, and for an inflected form its number, tense and person. Do NOT slide "
                + "into a meaning that belongs only to a similar-looking word, or to a DIFFERENT "
                + "form of the same root. A dictionary lookup is done on the root form and can "
                + "list senses the exact grid word cannot carry: e.g. English \"ares\" is the "
                + "plural of \"are\" (a unit of area) and must NOT be hinted as a form of the "
                + "verb \"be\", even though its root \"are\" is one. If the on-screen 'clue=' "
                + "itself looks like it describes the wrong form, hint the word by the sense "
                + "that actually fits its spelling in the grid.\n"
                + "   d. A HINT reply must NEVER reveal the solution word. Do NOT write it, do NOT "
                + "spell it out letter by letter, do NOT quote it from a state line, do NOT embed "
                + "it in a sentence — and, in particular, do NOT end (or begin) the hint with a "
                + "phrase such as \"The answer is ...\", \"It is ...\", \"The word is ...\", \"so "
                + "the word is ...\", \"La réponse est ...\" followed by the solution. The "
                + "'answer=' value in the state below is for your own silent check ONLY. Before "
                + "you send a HINT, re-read your own draft: if that exact 'answer=' value appears "
                + "in it in ANY form (any case, with or without spaces/dashes between letters), "
                + "delete that part and send the rest. (This restriction is for HINT replies only "
                + "— it does NOT apply to an explicit ANSWER request, see e.)\n"
                + "   e. ANSWER request (the player explicitly asked for the answer/solution/"
                + "exact word — see the top of rule 4). Here you DO give it: state which word "
                + "(same short preamble as 4b) and then the exact answer plainly, e.g. « Le mot "
                + "vertical en (l, c) est : … ». Rules b–d do not restrict this case.\n"
                + "   - Example 1: word MAISON, clue on screen 'Habitation'. BAD: 'Le mot est "
                + "MAISON' / 'M-A-I-S-O-N'. ALSO BAD (gives letters of the solution): '… : 6 "
                + "lettres, commence par un M et finit par un N.' ALSO BAD (echoes the clue, or "
                + "stays vague): '… : c'est une habitation.' / '… : un terme lié au logement.' "
                + "GOOD: '… : un synonyme est « demeure ».' or '… : on y rentre le soir, et on y "
                + "reçoit ses amis.'\n   - Example 2 (answer leak — the most common mistake): "
                + "word CHEVAL. BAD: '… : un synonyme est « destrier ». La réponse est : "
                + "CHEVAL.' — the final sentence RUINS the hint. ALSO BAD: a synonym of the same "
                + "family (« chevaux », « chevalin »). GOOD: the same reply WITHOUT that last "
                + "sentence.\n   A hint NEVER names the word and never gives any of its letters. "
                + "The synonyms and definitions above belong to their own example word — find "
                + "new ones for the player's OWN word, never reuse one from these rules.\n"
                + "   These examples illustrate the SAME general rule — apply it to ANY word the "
                + "player asks a hint about. The positions in the examples are illustrative only: "
                + "NEVER copy a position from an example; always take the real one from the "
                + "interface state below, and if the state says nothing is hovered and no cell "
                + "is clicked, ask the player instead (rule 4a) — do not invent one.\n"
                + "5. Keep replies reasonably short and conversational — this is a chat, not an "
                + "essay.\n"
                + "6. NEVER start your reply with a greeting (no \"Hello\", \"Hi\", \"Bonjour\", "
                + "\"Hi there\", introducing yourself again by name, or any equivalent) — do this "
                + "in NONE of your replies, not just most of them. A greeting has already been "
                + "shown exactly once, separately, as this chat's own welcome message, before the "
                + "player ever asked anything — that is the ONLY greeting this conversation will "
                + "ever have. Every single reply after it, including your very first one, must "
                + "start directly with the actual answer, with zero greeting or self-introduction "
                + "of any kind.\n"
                + "7. NEVER think out loud or show your reasoning process. Do not write things like "
                + "\"Let me check...\", \"Okay, the user is asking...\", \"Looking at the rules...\", "
                + "or any other deliberation about how you are deciding what to answer. Do not weigh "
                + "several possible interpretations of the question in your reply, and do not start "
                + "one answer then correct yourself mid-reply (\"wait, no\", \"actually\"...). Decide "
                + "the final answer entirely before writing anything down, then write ONLY that "
                + "final answer, starting directly with it — nothing before it.\n\n"
                + "Current state of the interface:\n" + String.join("\n", stateLines)
                + "\n\nFINAL REMINDER: " + finalReminder
                + selectedWordBlock;
    }

    static final String CLASSIFY_SYSTEM_PROMPT = "You sort the messages a player sends to the assistant of a crossword web app "
            + "while a crossword grid is on screen. Answer with exactly one word:\nUSAGE — "
            + "the message is about using the software: its interface, buttons, panels, "
            + "settings, features, how something works, or anything else that is not help "
            + "with filling in the grid.\nCLUE — the message asks for help filling in the "
            + "grid: a hint, an explanation of a clue, whether a letter or a word is right.\n"
            + "ANSWER — the message EXPLICITLY asks for the answer, the solution or the "
            + "exact word to write.\nA question about grids as objects of the software (the "
            + "library, generating, saving, printing or opening a grid) is USAGE. Examples: "
            + "'Un indice ?' -> CLUE; 'Ça commence par un B ?' -> CLUE; 'C'est MAISON ?' -> "
            + "CLUE (a proposal to check, not a request for the answer); 'Donne-moi la "
            + "réponse' -> ANSWER; 'What is the answer?' -> ANSWER; 'Comment vérifier mes "
            + "réponses ?' -> USAGE; 'Où sont les grilles déjà créées ?' -> USAGE; 'How do I "
            + "change the language?' -> USAGE.\nReply with USAGE, CLUE or ANSWER only, "
            + "nothing else.";

    /** The two messages of the classifier call (fixed instructions, then an excerpt of the conversation + the message). */
    static List<Object> buildClassifyMessages(List<Object> history, String message) {
        List<String> contextLines = new ArrayList<>();
        for (Object m : trimHistory(history, CLASSIFY_HISTORY_MESSAGES)) {
            String who = "user".equals(Json.get(m, "role")) ? "Player" : "Assistant";
            Object content = Json.get(m, "content");
            contextLines.add(who + ": " + cpHead(Json.truthy(content) ? content.toString() : "", CLASSIFY_CONTEXT_CHARS));
        }
        String context = contextLines.isEmpty() ? ""
                : "Previous messages (context only):\n" + String.join("\n", contextLines) + "\n\n";
        return Json.list(
                Json.obj("role", "system", "content", CLASSIFY_SYSTEM_PROMPT),
                Json.obj("role", "user", "content",
                        context + "Message to sort: " + message + "\n\nAnswer with one word, USAGE, CLUE or ANSWER:"));
    }

    /**
     * The first of the two LLM calls a question asked in play mode costs:
     * one non-streamed call answering USAGE or CLUE. The route is null when
     * the answer names neither keyword or the call fails, and the reply then
     * falls back to the combined prompt.
     */
    public Route classifyQuestion(List<Object> history, String message) {
        List<Object> classifyMessages = buildClassifyMessages(history, message);
        Map<String, Object> body = Json.obj("model", model, "messages", classifyMessages,
                "temperature", CLASSIFY_TEMPERATURE, "max_tokens", CLASSIFY_MAX_TOKENS, "reasoning_effort", "none");
        String content;
        try {
            Http.Response r = Http.postJson(baseUrl, body, Map.of("Authorization", "Bearer " + apiKey), CLASSIFY_TIMEOUT);
            if (r.status() >= 400) throw new IOException("Client error '" + r.status() + "' for url '" + baseUrl + "'");
            Object msg = Json.get(Json.asList(Json.get(r.json(), "choices")).get(0), "message");
            Object c = Json.get(msg, "content");
            content = c == null ? "" : c.toString();
        } catch (IOException | RuntimeException e) {
            Log.warning("chat: question classification failed (%s, model=%s): %s", baseUrl, Log.repr(model), e.getMessage());
            return new Route(null, "", classifyMessages);
        }
        String route = parseRoute(stripThinkBlock(content));
        Log.info("chat: question classified as %s (raw answer %s)", route == null ? "None" : route, Log.repr(content));
        return new Route(route, content, classifyMessages);
    }

    /** System prompt of a USAGE reply: fixed head + interface rules, no grid content, interface language. */
    public String buildUsagePrompt(String language, Map<String, Object> ui) {
        String languageName = Clues.LANGUAGE_NAMES.getOrDefault(language, language);
        return fixedPromptHead(loadDocUser())
                + "Write EVERY reply entirely in " + languageName + ". This is not optional and "
                + "applies to every message you ever send.\n\n"
                + "The player is playing the crossword grid on screen and asks you how the "
                + "interface works.\n\n"
                + "STRICT RULES:\n"
                + "1. Always reply extremely politely.\n"
                + "2. LANGUAGE. Your entire reply MUST be written in " + languageName + " — every word "
                + "of it, whatever language the player writes to you in. The reference "
                + "documentation above is in English: whatever you take from it must be written in "
                + languageName + ", rephrased, never quoted in English.\n"
                + "3. Answer ONLY questions about using this interface, from the documentation "
                + "above. For a question unrelated to this app (general knowledge, other software, "
                + "personal questions...), politely decline, suggest an appropriate website or "
                + "resource for it, and say NOTHING else — never answer it anyway. If the message "
                + "is in fact a request for help with a word of the grid, tell the player to move "
                + "the mouse over that word (or click one of its cells) and ask you for a hint.\n"
                + "4. Keep replies reasonably short and conversational — this is a chat, not an "
                + "essay.\n"
                + "5. NEVER start with a greeting or a self-introduction: the chat already greeted "
                + "the player once. Start directly with the answer.\n"
                + "6. NEVER think out loud or show your reasoning: write only the final answer.\n\n"
                + "FINAL REMINDER: write your entire reply in " + languageName + ", starting directly "
                + "with the answer and no greeting.";
    }

    /** System prompt of a CLUE (or ANSWER) reply: only the selected word and what to do with it. */
    public String buildCluePrompt(String language, Map<String, Object> ui, boolean answerRequested, String message,
                                  List<String[]> synonyms, List<String> sentences) {
        Selection sel = resolveSelection(ui);
        Object helpWord = sel.helpWord();
        Object wordLang = helpWord != null ? Json.get(helpWord, "language") : null;
        String replyLanguage = Json.truthy(wordLang) ? wordLang.toString() : language;
        String languageName = Clues.LANGUAGE_NAMES.getOrDefault(replyLanguage, replyLanguage);
        String head = "You are David FALCON, the assistant of a crossword web app. The player is "
                + "solving the crossword grid on screen and asks you for help with one word of it.\n"
                + "Write your entire reply in " + languageName + ", whatever language the player "
                + "writes to you in.\n\n";
        if (helpWord == null) {
            return head + "No word is selected in the grid right now (nothing under the mouse, no "
                    + "single word at a clicked cell), so you do not know which word the player "
                    + "means. Reply with ONE polite sentence in " + languageName + " asking the player "
                    + "to move the mouse over the word (or click one of its cells) and ask again. "
                    + "Say nothing else: no hint, no word, no greeting.";
        }
        int row = ival(helpWord, "row") + 1, col = ival(helpWord, "col") + 1;
        String direction = "down".equals(Json.get(helpWord, "direction")) ? "DOWN (vertical)" : "ACROSS (horizontal)";
        String kind = sel.hoveredResolved() != null ? "the word under the player's mouse"
                : "the word at the cell the player clicked";
        Object clueObj = Json.get(helpWord, "clue");
        String clue = Json.truthy(clueObj) ? clueObj.toString() : "";
        String clueLine = !clue.isEmpty() ? Log.repr(clue) : "none yet (no clue has been generated for it)";
        String answer = Json.str(helpWord, "answer", "");
        if (answerRequested) {
            // ROUTE_ANSWER: the player explicitly asked for the answer.
            return head + "THE WORD (" + kind + ", selected right now):\n"
                    + "- position: row " + row + ", column " + col + ", " + direction + "\n"
                    + "- clue shown to the player: " + clueLine + "\n"
                    + "- solution: " + Log.repr(answer) + "\n"
                    + "\nThe player EXPLICITLY asked for the answer of this word. Reply with ONE "
                    + "sentence in " + languageName + " giving the word's position, its direction (in "
                    + languageName + " words) and its solution, " + answer + ". Nothing else: no hint, no "
                    + "greeting.";
        }
        Grounding g = clueGrounding(helpWord, replyLanguage);
        // The root forbidden in a hint (see root).
        String stem = root(answer);
        StringBuilder context = new StringBuilder();
        context.append("- The word: ").append(kind).append(".\n");
        context.append("- Clue already shown to the player: ").append(clueLine).append("\n");
        context.append("- Solution: ").append(Log.repr(answer)).append(".\n");
        List<String> baseReprs = new ArrayList<>();
        for (String b : g.baseForms()) baseReprs.add(Log.repr(b));
        if (!g.grammar().isEmpty()) {
            context.append("- Part of speech: ").append(String.join(" / ", g.grammar()))
                    .append(g.baseForms().isEmpty() ? "" : ", an inflected form of " + String.join(", ", baseReprs))
                    .append(".\n");
        }
        if (!g.meanings().isEmpty()) {
            context.append("- Dictionary entries of its base form:\n");
            for (String[] mm : g.meanings()) {
                context.append("  - ").append(Log.repr(mm[0])).append(" (").append(mm[1]).append("): ").append(mm[2]).append("\n");
            }
        }
        if (synonyms != null && !synonyms.isEmpty()) {
            context.append("- Possible synonyms, found by a similarity search and possibly imperfect "
                    + "(check each one's definition):\n");
            for (String[] x : synonyms) context.append("  - ").append(Log.repr(x[0])).append(": ").append(x[1]).append("\n");
        }
        // A proposal the model could not check reliably by itself.
        String answerForm = gridForm(answer);
        boolean proposed = false;
        for (int[] run : letterRuns(message)) proposed |= gridForm(message.substring(run[0], run[1])).equals(answerForm);
        StringBuilder baseList = new StringBuilder();
        for (String r : baseReprs) baseList.append(", its base form ").append(r);
        boolean hasSentences = sentences != null && !sentences.isEmpty();
        StringBuilder sentenceLines = new StringBuilder();
        if (hasSentences) for (String sentence : sentences) sentenceLines.append("- ").append(sentence).append("\n");
        return head
                + "# GOAL\n"
                + "Create a new clue for the solution " + Log.repr(answer) + " (see CONTEXT), in " + languageName + ". "
                + "The player already has the clue shown in the grid and cannot find the word with "
                + "it: your new clue must describe the same word in a DIFFERENT way, so that they can "
                + "guess it and write it in the grid themselves. Like a crossword clue, it is one or "
                + "two short sentences that point to the word without writing it: another meaning of "
                + "it, a synonym, what it is used for, where or when one meets it. It never contains "
                + "the solution nor any part of it — unless the player clearly asks for the "
                + "solution.\n\n"
                + "# HOW TO HELP THE PLAYER (your answer)\n"
                + "Your answer, in " + languageName + ", is your new clue alone, starting directly "
                + "(no greeting, no reasoning, no position, row, column or direction: the player "
                + "already has the word selected). Build it from one or two of these:\n"
                + "- pertinent synonyms of the solution, in the same form — from the list below "
                + "when their definition fits, or your own;\n"
                + "- for a conjugated or plural form: say it is one, and give a synonym of its base "
                + "form;\n"
                + "- a new clue or definition of your own, from another angle than the one shown "
                + "(what it is for, what it is made of, who uses it...);\n"
                + (!g.meanings().isEmpty() ? "- a paraphrase, in your own words, of one of the dictionary entries given in "
                + "CONTEXT;\n" : "")
                + "- a context where the word is used: a typical situation, an example, a "
                + "well-known expression with « … » in its place"
                + (hasSentences ? ", or one of the SENTENCES below with the word replaced by « … »" : "")
                + ".\n"
                + "Depending on the message:\n"
                + "- another hint: a different kind of help from your previous one;\n"
                + "- a letter or a word to check (« C'est MAISON ? », « Ça commence par un B ? »): "
                + (proposed ? "this message proposes the solution itself: tell them it is right;\n"
                : "say whether it is right, and if not, add a hint;\n")
                + "- the solution itself: say you will give it if they clearly ask for it;\n"
                + "- another word of the grid: ask them to move the mouse over it or click one of "
                + "its cells;\n"
                + "- something unrelated to the grid: politely decline and suggest an appropriate "
                + "resource.\n\n"
                + "# CONTEXT\n"
                + context
                + (hasSentences ? "\n# SENTENCES\nReal sentences using the word, from a reference corpus:\n"
                + sentenceLines : "")
                + "\n# FORBIDDEN (unless the player clearly asks for the solution) — never:\n"
                + "- write the solution " + Log.repr(answer) + baseList + ", or any word starting with " + Log.repr(stem) + ";\n"
                + "- say how many letters the word has, or which letters it contains;\n"
                + "- repeat or rephrase the clue already shown: the player already has it;\n"
                + "- give the word's position (row, column) or direction.";
    }

    /**
     * Removes every markup tag ({@code <name>}, {@code </name>}, {@code <name attr>},
     * {@code <name/>}) from a streamed reply, keeping the text between them. A
     * tag can arrive split across chunks, so the text from a "<" onwards is
     * held back until it closes as a tag (dropped) or can no longer be one (a
     * newline, a "<" or MAX_TAG_CHARS reached — released as is).
     */
    static final class TagStripper {
        private final StringBuilder pending = new StringBuilder();

        String feed(String text) {
            StringBuilder out = new StringBuilder();
            text.codePoints().forEach(cp -> {
                if (pending.length() == 0) {
                    if (cp == '<') pending.appendCodePoint(cp);
                    else out.appendCodePoint(cp);
                    return;
                }
                if (cp == '<') {
                    out.append(pending);
                    pending.setLength(0);
                    pending.appendCodePoint(cp);
                } else if (cp == '>') {
                    String candidate = pending + ">";
                    pending.setLength(0);
                    if (!TAG_RE.matcher(candidate).matches()) out.append(candidate);
                } else if (cp == '\n' || pending.codePointCount(0, pending.length()) >= MAX_TAG_CHARS) {
                    out.append(pending).appendCodePoint(cp);
                    pending.setLength(0);
                } else {
                    pending.appendCodePoint(cp);
                }
            });
            return out.toString();
        }

        String flush() {
            String rest = pending.toString();
            pending.setLength(0);
            return rest;
        }
    }

    public String currentSelectionLine(Map<String, Object> ui) {
        return currentSelectionLine(ui, true);
    }

    /**
     * {@code withPosition} false names the word without its row/column/
     * direction — the CLUE route, whose reply must not state the position
     * (the model copies whatever position text it is shown into the hint).
     */
    public String currentSelectionLine(Map<String, Object> ui, boolean withPosition) {
        Selection sel = resolveSelection(ui);
        if (!sel.puzzleLoaded()) return "";
        Object w = sel.hoveredResolved() != null ? sel.hoveredResolved() : sel.fillingWord();
        if (w == null) {
            return "NOTE — right now NO single word is selected in the grid (nothing hovered, "
                    + "and no unambiguous word at a clicked cell). If this message asks for help "
                    + "without naming a word, ask the player to hover a word or click a cell "
                    + "first; do NOT continue a word from an earlier reply.";
        }
        String kind = sel.hoveredResolved() != null ? "under the mouse (hovered)" : "at the clicked cell being filled";
        if (!withPosition) {
            return "NOTE — right now the player has a word selected in the grid (" + kind + "): the "
                    + "one of the system prompt. If this message is a request for help / a hint and "
                    + "does not explicitly name a different word, it is about THAT word, even if an "
                    + "earlier reply in this conversation was about a different word. Do not carry "
                    + "over the previous reply's word.";
        }
        String d = "down".equals(Json.get(w, "direction")) ? "DOWN (vertical)" : "ACROSS (horizontal)";
        int r = ival(w, "row") + 1, c = ival(w, "col") + 1;
        return "NOTE — right now the player has the " + d + " word starting at (row " + r + ", column " + c + ") "
                + "selected in the grid (" + kind + "). If this message is a request for help / a hint "
                + "/ the answer and does not explicitly name a different word, it is about THAT "
                + "word — the " + d + " word at (row " + r + ", column " + c + ") — even if an earlier reply in "
                + "this conversation was about a different word. Do not carry over the previous "
                + "reply's word.";
    }

    /**
     * Streams the reply: every visible chunk is handed to {@code onChunk} as
     * soon as it is known not to be part of a {@code <think>} block.
     * {@code onPrompt} receives the exact messages sent. Throws ChatError
     * when the call fails or yields no content at all.
     */
    public void replyStream(List<Object> history, String message, String language, Map<String, Object> uiContext,
                            double timeout, Consumer<List<Object>> onPrompt, Consumer<Route> onRoute,
                            Consumer<String> onChunk) {
        Map<String, Object> ui = uiContext == null ? Map.of() : uiContext;
        // In play mode (a playable grid on screen) a first call sorts the
        // question, and the reply is written from the prompt of that kind only.
        String route = null;
        if (Json.truthy(ui.get("puzzle_loaded"))) {
            Route r = classifyQuestion(history, message);
            route = r.route();
            if (onRoute != null) onRoute.accept(r);
        }
        String systemPrompt;
        String selection;
        if (ROUTE_USAGE.equals(route)) {
            systemPrompt = buildUsagePrompt(language, ui);
            selection = "";
        } else if (ROUTE_CLUE.equals(route) || ROUTE_ANSWER.equals(route)) {
            List<String[]> synonyms = List.of();
            List<String> sentences = List.of();
            Object hw = resolveSelection(ui).helpWord();
            if (ROUTE_CLUE.equals(route) && hw != null) {
                Object wl = Json.get(hw, "language");
                String wordLanguage = Json.truthy(wl) ? wl.toString() : language;
                synonyms = qdrantSynonyms(hw, wordLanguage);
                sentences = corpusSentences(hw, wordLanguage);
            }
            systemPrompt = buildCluePrompt(language, ui, ROUTE_ANSWER.equals(route), message, synonyms, sentences);
            history = trimHistory(history, CLUE_HISTORY_MESSAGES);
            selection = currentSelectionLine(ui, !ROUTE_CLUE.equals(route));
        } else {
            systemPrompt = buildSystemPrompt(language, ui);
            selection = currentSelectionLine(ui);
        }
        List<Object> messages = new ArrayList<>();
        messages.add(Json.obj("role", "system", "content", systemPrompt));
        messages.addAll(history);
        String userContent = selection.isEmpty() ? message : selection + "\n\n" + message;
        messages.add(Json.obj("role", "user", "content", userContent));
        if (onPrompt != null) onPrompt.accept(messages);
        Object helpWord = resolveSelection(ui).helpWord();
        if (ROUTE_CLUE.equals(route) && helpWord != null) {
            // A hint is checked before it is sent (see checkedHint), so it
            // reaches the player in one piece rather than streamed.
            Object hl = Json.get(helpWord, "language");
            onChunk.accept(checkedHint(messages, timeout, helpWord, message, Json.truthy(hl) ? hl.toString() : language));
            return;
        }
        streamCompletion(messages, timeout, onChunk);
    }

    /** A hint giving no part of the answer and not echoing the message: retried, then its leaked words masked (see chatbot.py _checked_hint). */
    String checkedHint(List<Object> messages, double timeout, Object word, String message, String language) {
        String text = "";
        List<int[]> spans = List.of();
        for (int attempt = 0; attempt < CLUE_HINT_ATTEMPTS; attempt++) {
            StringBuilder sb = new StringBuilder();
            streamCompletion(messages, timeout, sb::append);
            text = sb.toString();
            spans = hintLeakSpans(text, word, message, language);
            boolean echo = gridForm(Py.strip(text)).equals(gridForm(Py.strip(message)));
            if (spans.isEmpty() && !echo) return text;
            if (echo) {
                Log.info("chat: hint attempt %d/%d only echoes the message: %s", attempt + 1, CLUE_HINT_ATTEMPTS, Log.repr(text));
                continue;
            }
            Log.info("chat: hint attempt %d/%d leaks part of the answer: %s", attempt + 1, CLUE_HINT_ATTEMPTS, Log.repr(text));
        }
        for (int k = spans.size() - 1; k >= 0; k--) {
            int[] sp = spans.get(k);
            text = text.substring(0, sp[0]) + "…" + text.substring(sp[1]);
        }
        return text;
    }

    /** The streamed chat-completions call behind every reply: visible text, think blocks and tags removed. */
    void streamCompletion(List<Object> messages, double timeout, Consumer<String> onChunk) {
        StringBuilder buffer = new StringBuilder();
        boolean yielded = false;
        // Every visible piece goes through the tag filter before it is sent.
        TagStripper tags = new TagStripper();
        String state = switch (thinkFilter) {
            case "none" -> "disabled";
            case "close_only" -> "in_reasoning";
            default -> "pending";
        };
        Map<String, Object> body = Json.obj("model", model, "messages", messages, "temperature", TEMPERATURE,
                "max_tokens", MAX_TOKENS, "stream", true, "reasoning_effort", "none");
        HttpRequest req = HttpRequest.newBuilder(URI.create(baseUrl))
                .timeout(Duration.ofMillis((long) (timeout * 1000)))
                .header("Authorization", "Bearer " + apiKey)
                .header("Content-Type", "application/json")
                .POST(HttpRequest.BodyPublishers.ofString(Json.dumps(body), StandardCharsets.UTF_8))
                .build();
        try {
            HttpResponse<Stream<String>> resp = Http.CLIENT.send(req, HttpResponse.BodyHandlers.ofLines());
            try (Stream<String> lines = resp.body()) {
                if (resp.statusCode() >= 400) {
                    throw new IOException("Client error '" + resp.statusCode() + "' for url '" + baseUrl + "'");
                }
                Iterator<String> it = lines.iterator();
                while (it.hasNext()) {
                    String line = it.next();
                    if (line.isEmpty() || !line.startsWith("data: ")) continue;
                    String payload = Py.strip(line.substring(6));
                    if (payload.equals("[DONE]")) break;
                    Object event;
                    try {
                        event = Json.parse(payload);
                    } catch (IllegalArgumentException e) {
                        continue;
                    }
                    List<Object> choices = Json.listOrEmpty(Json.get(event, "choices"));
                    Object first = choices.isEmpty() ? null : choices.get(0);
                    String delta = Json.str(Json.get(first, "delta"), "content", "");
                    if (delta.isEmpty()) continue;
                    Log.info("chat: raw LLM chunk: %s", Log.repr(delta));
                    if (state.equals("disabled")) {
                        String visible = tags.feed(delta);
                        if (!visible.isEmpty()) {
                            yielded = true;
                            onChunk.accept(visible);
                        }
                        continue;
                    }
                    buffer.append(delta);
                    boolean progressed = true;
                    while (progressed) {
                        progressed = false;
                        if (state.equals("in_reasoning")) {
                            int idx = buffer.indexOf(THINK_CLOSE);
                            if (idx >= 0) {
                                buffer.delete(0, idx + THINK_CLOSE.length());
                                state = "clear";
                                progressed = true;
                            }
                        } else if (buffer.length() > 0) {
                            int idx = buffer.indexOf(THINK_OPEN);
                            if (idx >= 0) {
                                buffer.delete(0, idx + THINK_OPEN.length());
                                state = "in_reasoning";
                                progressed = true;
                            } else {
                                int hold = longestTagPrefixSuffix(buffer.toString(), THINK_OPEN);
                                if (hold < buffer.length()) {
                                    String toFlush = buffer.substring(0, buffer.length() - hold);
                                    buffer.delete(0, buffer.length() - hold);
                                    String visible = toFlush.isEmpty() ? "" : tags.feed(toFlush);
                                    if (!visible.isEmpty()) {
                                        yielded = true;
                                        onChunk.accept(visible);
                                    }
                                }
                            }
                        }
                    }
                }
            }
        } catch (IOException e) {
            Log.warning("chat stream failed (%s, model=%s): %s", baseUrl, Log.repr(model), e.getMessage());
            throw new ChatError("Le serveur de langage est indisponible (" + e.getMessage() + ").", e);
        } catch (InterruptedException e) {
            Thread.currentThread().interrupt();
            throw new ChatError("Le serveur de langage est indisponible (interrupted).", e);
        }
        String tail = tags.flush();
        if (!tail.isEmpty()) {
            yielded = true;
            onChunk.accept(tail);
        }
        if (!yielded) {
            throw new ChatError("Le serveur de langage n'a renvoyé aucun contenu "
                    + "(le contexte de la conversation est peut-être trop long).", null);
        }
    }
}
