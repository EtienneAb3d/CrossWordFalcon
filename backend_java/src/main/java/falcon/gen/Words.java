package falcon.gen;

import falcon.Py;

import falcon.GlossLookup;

import java.io.BufferedReader;
import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.text.Normalizer;
import java.util.ArrayList;
import java.util.BitSet;
import java.util.Collection;
import java.util.HashMap;
import java.util.HashSet;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Locale;
import java.util.Map;
import java.util.Set;
import java.util.regex.Matcher;
import java.util.regex.Pattern;

/** Dictionary loading and word-index lookups (mirrors the "Dictionary" and
 * "Lexicon index" sections of backend/crossword_gen.py). */
public final class Words {
    private Words() {}

    public static final Map<String, Double> DIFFICULTY_PRESETS = Map.of("easy", 0.66, "medium", 0.80, "hard", 1.0);
    public static final Map<String, Integer> MAX_PROPER_NOUNS = Map.of("easy", 0, "medium", 2, "hard", 5);
    public static final Map<String, Integer> MAX_NON_GLOSS_WORDS = Map.of("easy", 0, "medium", 2, "hard", 5);
    public static final Set<String> PROPER_NOUN_EXCLUDED_LANGS = Set.of("de");

    private static final Pattern LANG_RE = Pattern.compile("wordlist_([a-z]{2})_freq\\.tsv$");

    public static String langFromPath(String path) {
        if (path == null) return null;
        String base = Path.of(path).getFileName().toString();
        Matcher m = LANG_RE.matcher(base);
        return m.find() ? m.group(1) : null;
    }

    /** The loaded lexicon (Python's {@code (by_length, refs, frequencies, proper_nouns, non_gloss)}): each word's
     * row reference stands for its accented form and lemmas, which stay on disk ({@link #wordForms}). */
    public record Lexicon(Map<Integer, List<String>> byLength, Map<String, Integer> refs,
                          Map<String, Double> frequencies, Set<String> properNouns, Set<String> nonGloss) {}

    private record Entry(String word, String accented, double freq, List<String> canonical, int ref) {}

    static boolean isAlpha(String s) {
        if (s.isEmpty()) return false;
        for (int i = 0; i < s.length(); i++) {
            char ch = s.charAt(i);
            if (Character.isSurrogate(ch) || !Character.isLetter(ch)) return false;
        }
        return true;
    }

    static double parseFreq(String s) {
        try {
            return Double.parseDouble(Py.strip(s));
        } catch (NumberFormatException e) {
            return 0.0;
        }
    }

    /** Row reference of a freq-wordlist row starting at byte {@code offset} (mirrors _freq_row_ref). */
    static int freqRowRef(long offset) {
        return (int) (offset << 1);
    }

    /** Row reference of a Scrabble-wordlist row starting at byte {@code offset} (mirrors _scrabble_row_ref). */
    static int scrabbleRowRef(long offset) {
        return (int) ((offset << 1) | 1);
    }

    /** {@code maxWords}: null, an Integer (absolute count) or a Double
     * (fraction of the loaded lexicon). */
    public static Lexicon loadWordlist(String path, Number maxWords, boolean requireGloss, boolean excludeProperNouns)
            throws IOException {
        List<Entry> entries = new ArrayList<>();
        falcon.TextLines.forEachLine(Path.of(path), (offset, line) -> {
            if (line.isEmpty() || line.startsWith("#")) return;
            int ref = freqRowRef(offset);
            String[] parts = line.split("\t", -1);
            if (parts.length >= 4) {
                String word = parts[0].toUpperCase(Locale.ROOT);
                List<String> canonical = new ArrayList<>();
                for (String c : parts[3].split(";", -1)) if (!c.isEmpty()) canonical.add(c);
                if (isAlpha(word)) entries.add(new Entry(word, parts[1], parseFreq(parts[2]),
                        canonical.isEmpty() ? List.of(parts[1]) : canonical, ref));
            } else if (parts.length == 3) {
                String word = parts[0].toUpperCase(Locale.ROOT);
                if (isAlpha(word)) entries.add(new Entry(word, parts[1], parseFreq(parts[2]), List.of(parts[1]), ref));
            } else if (parts.length == 2) {
                String word = parts[0].toUpperCase(Locale.ROOT);
                if (isAlpha(word)) entries.add(new Entry(word, word, parseFreq(parts[1]), List.of(word), ref));
            } else {
                String t = Py.strip(line.toUpperCase(Locale.ROOT));
                if (t.isEmpty()) return;
                for (String tok : Py.split(t)) if (isAlpha(tok)) entries.add(new Entry(tok, tok, 0.0, List.of(tok), ref));
            }
        });
        LinkedHashMap<String, Entry> best = new LinkedHashMap<>();
        for (Entry e : entries) {
            Entry cur = best.get(e.word);
            if (cur == null || e.freq > cur.freq) {
                if (cur == null) best.put(e.word, e);
                else best.replace(e.word, e);
            }
        }
        entries.clear();
        String lang = langFromPath(path);
        boolean glossAvailable = lang != null && GlossLookup.hasGlossDictionary(lang);
        if (requireGloss && glossAvailable) {
            best.values().removeIf(v -> !hasAnyGloss(v, lang));
        }
        if (excludeProperNouns && lang != null && !PROPER_NOUN_EXCLUDED_LANGS.contains(lang)) {
            best.values().removeIf(v -> !v.accented.isEmpty() && Character.isUpperCase(v.accented.charAt(0)));
        }
        List<Entry> ranked = new ArrayList<>(best.values());
        best.clear();
        ranked.sort((a, b) -> Double.compare(b.freq, a.freq));
        if (maxWords != null && maxWords.doubleValue() != 0) {
            int keep = maxWords instanceof Double d ? (int) Math.rint(ranked.size() * d) : maxWords.intValue();
            if (keep < ranked.size()) ranked = new ArrayList<>(ranked.subList(0, Math.max(0, keep)));
        }
        Map<Integer, List<String>> byLength = new LinkedHashMap<>();
        Map<String, Integer> refs = new HashMap<>();
        Map<String, Double> frequencies = new HashMap<>();
        Set<String> properNouns = new HashSet<>();
        Set<String> nonGloss = new HashSet<>();
        boolean checkProper = lang == null || !PROPER_NOUN_EXCLUDED_LANGS.contains(lang);
        for (Entry e : ranked) {
            byLength.computeIfAbsent(e.word.length(), k -> new ArrayList<>()).add(e.word);
            refs.put(e.word, e.ref);
            frequencies.put(e.word, e.freq);
            if (checkProper && !e.accented.isEmpty() && Character.isUpperCase(e.accented.charAt(0))) properNouns.add(e.word);
            if (glossAvailable && !hasAnyGloss(e, lang)) nonGloss.add(e.word);
        }
        return new Lexicon(byLength, refs, frequencies, properNouns, nonGloss);
    }

    private static boolean hasAnyGloss(Entry e, String lang) {
        List<String> cands = new ArrayList<>();
        cands.add(e.accented);
        cands.addAll(e.canonical);
        return GlossLookup.hasAnyGloss(cands, lang);
    }

    /** (accented, [canonical, ...]) of {@code word} read from its wordlist row {@code line} — the same parsing as
     * loadWordlist (freq wordlist) or the Scrabble merge ({@code scrabble}) (mirrors _row_forms). */
    static Object[] rowForms(String line, String word, boolean scrabble) {
        String[] parts = line.split("\t", -1);
        if (scrabble) {
            if (parts.length < 3) return new Object[]{word, List.of(word)};
            return new Object[]{parts[1], splitCanonical(parts[2], parts[1])};
        }
        if (parts.length >= 4) return new Object[]{parts[1], splitCanonical(parts[3], parts[1])};
        if (parts.length == 3) return new Object[]{parts[1], List.of(parts[1])};
        return new Object[]{word, List.of(word)};
    }

    private static List<String> splitCanonical(String column, String accented) {
        List<String> canonical = new ArrayList<>();
        for (String c : column.split(";", -1)) if (!c.isEmpty()) canonical.add(c);
        return canonical.isEmpty() ? List.of(accented) : canonical;
    }

    /** {accented, [canonical, ...]} of {@code word}, read back from its own wordlist row through {@code idx} (one
     * length's index); {word, [word]} for a word with no row reference (mirrors word_forms). */
    public static Object[] wordForms(LenIndex idx, String word) {
        if (idx == null || idx.refs == null || idx.sources == null) return new Object[]{word, List.of(word)};
        Integer id = idx.ids.get(word);
        if (id == null) return new Object[]{word, List.of(word)};
        int ref = idx.refs[id];
        boolean scrabble = (ref & 1) != 0;
        String path = scrabble ? idx.sources[1] : idx.sources[0];
        if (path == null) return new Object[]{word, List.of(word)};
        try {
            return rowForms(falcon.TextLines.readLineAt(Path.of(path), Integer.toUnsignedLong(ref) >>> 1), word, scrabble);
        } catch (IOException e) {
            return new Object[]{word, List.of(word)};
        }
    }

    public static Map<Integer, LenIndex> buildIndex(Map<Integer, List<String>> byLength, Map<String, Double> frequencies,
                                                    Map<String, Double> dictionaryFrequencies) {
        return buildIndex(byLength, frequencies, dictionaryFrequencies, null, null);
    }

    public static Map<Integer, LenIndex> buildIndex(Map<Integer, List<String>> byLength, Map<String, Double> frequencies,
                                                    Map<String, Double> dictionaryFrequencies, Map<String, Integer> refs,
                                                    String[] sources) {
        Map<Integer, LenIndex> index = new LinkedHashMap<>();
        byLength.forEach((len, words) -> index.put(len,
                new LenIndex(len, words, frequencies, dictionaryFrequencies, refs, sources)));
        return index;
    }

    /** {MOT: FREQUENCE} for every word of the freq wordlist at {@code path} (the highest value when a MOT has
     * several rows), whatever the difficulty cut. Transient: only read while a lexicon is being indexed
     * (mirrors load_dictionary_frequencies). */
    public static Map<String, Double> loadDictionaryFrequencies(String path) throws IOException {
        Map<String, Double> result = new HashMap<>();
        try (BufferedReader r = Files.newBufferedReader(Path.of(path), StandardCharsets.UTF_8)) {
            String line;
            while ((line = r.readLine()) != null) {
                if (line.isEmpty() || line.startsWith("#")) continue;
                String[] parts = line.split("\t", -1);
                if (parts.length < 2) continue;
                double freq = parseFreq(parts.length >= 3 ? parts[2] : parts[1]);
                String word = parts[0].toUpperCase(Locale.ROOT);
                if (freq > result.getOrDefault(word, 0.0)) result.put(word, freq);
            }
        }
        return result;
    }

    // ---------------------------------------------------------------- dual structures

    /** Two word indices, one per direction (the same object on a
     * monolingual grid). */
    public static final class DualIndex {
        public final Map<Integer, LenIndex> across;
        public final Map<Integer, LenIndex> down;

        public DualIndex(Map<Integer, LenIndex> across, Map<Integer, LenIndex> down) {
            this.across = across;
            this.down = down;
        }

        public Map<Integer, LenIndex> forDirection(String direction) {
            return "across".equals(direction) ? across : down;
        }

        public Map<Integer, LenIndex> forCells(int[] cells) {
            return isAcross(cells) ? across : down;
        }

        public LenIndex get(int[] cells) {
            return forCells(cells).get(cells.length);
        }
    }

    /** Available slot lengths per direction (Python's DualSet of ints). */
    public static final class LengthSets {
        public final Set<Integer> across;
        public final Set<Integer> down;

        public LengthSets(Set<Integer> across, Set<Integer> down) {
            this.across = across;
            this.down = down;
        }

        public Set<Integer> forCells(int[] cells) {
            return isAcross(cells) ? across : down;
        }

        public static LengthSets available(DualIndex index, int minCount) {
            Set<Integer> a = new HashSet<>(), d = new HashSet<>();
            index.across.forEach((len, li) -> {
                if (li.size() >= minCount) a.add(len);
            });
            index.down.forEach((len, li) -> {
                if (li.size() >= minCount) d.add(len);
            });
            return new LengthSets(a, d);
        }
    }

    /** Theme glossary: a single set on a monolingual grid, one per direction
     * on a bilingual one (Python's frozenset-or-DualSet). */
    public static final class PW {
        public static final PW EMPTY = new PW(Set.of(), Set.of(), false);
        public final Set<String> across;
        public final Set<String> down;
        public final boolean dual;

        public PW(Set<String> across, Set<String> down, boolean dual) {
            this.across = across;
            this.down = down;
            this.dual = dual;
        }

        public static PW single(Set<String> words) {
            return new PW(words, words, false);
        }

        public boolean isEmpty() {
            return across.isEmpty() && (!dual || down.isEmpty());
        }

        public Set<String> forCells(int[] cells) {
            if (!dual) return across;
            return isAcross(cells) ? across : down;
        }

        public Set<String> flatten() {
            if (!dual) return across;
            Set<String> all = new HashSet<>(across);
            all.addAll(down);
            return all;
        }
    }

    public static boolean isAcross(int[] cells) {
        return Cells.r(cells[0]) == Cells.r(cells[1]);
    }

    public static String slotDirection(int[] cells) {
        return isAcross(cells) ? "across" : "down";
    }

    // ---------------------------------------------------------------- lookups

    /** Real candidates for a slot given the letters known on its cells. */
    public static Dom slotCandidates(DualIndex index, int length, int[] cells, Map<Integer, Character> known) {
        LenIndex idx = index.forCells(cells).get(length);
        if (idx == null) return Dom.EMPTY;
        BitSet result = null;
        List<BitSet> sets = new ArrayList<>();
        for (int p = 0; p < cells.length; p++) {
            Character letter = known.get(cells[p]);
            if (letter == null) continue;
            BitSet s = idx.at(p, letter);
            if (s == null) return Dom.EMPTY;
            sets.add(s);
        }
        if (sets.isEmpty()) return Dom.full(idx);
        sets.sort((a, b) -> Integer.compare(a.cardinality(), b.cardinality()));
        result = (BitSet) sets.get(0).clone();
        for (int k = 1; k < sets.size(); k++) {
            result.and(sets.get(k));
            if (result.isEmpty()) return Dom.EMPTY;
        }
        return new Dom(idx, result);
    }

    public static int slotCandidateCount(DualIndex index, int length, int[] cells, Map<Integer, Character> known) {
        return slotCandidates(index, length, cells, known).size();
    }

    /** Bare, accent-stripped, uppercase A-Z form of a "Mots Défi" entry. */
    public static String challengeWordGridForm(String word) {
        String folded = word.replace("œ", "oe").replace("Œ", "OE").replace("æ", "ae").replace("Æ", "AE");
        String d = Normalizer.normalize(folded, Normalizer.Form.NFKD);
        StringBuilder sb = new StringBuilder();
        d.codePoints().forEach(cp -> {
            int t = Character.getType(cp);
            if (t != Character.NON_SPACING_MARK && t != Character.ENCLOSING_MARK && t != Character.COMBINING_SPACING_MARK) {
                sb.appendCodePoint(cp);
            }
        });
        String up = sb.toString().toUpperCase(Locale.ROOT);
        StringBuilder out = new StringBuilder();
        for (int i = 0; i < up.length(); i++) {
            char ch = up.charAt(i);
            if (ch >= 'A' && ch <= 'Z') out.append(ch);
        }
        return out.toString();
    }

    // ---------------------------------------------------------------- Scrabble dictionaries

    /** data/wordlist_&lt;lang&gt;_scrabble.tsv (mirrors crossword_gen.py's scrabble_wordlist_path). */
    public static Path scrabbleWordlistPath(String language) {
        return falcon.Env.ROOT.resolve("data").resolve("wordlist_" + language + "_scrabble.tsv");
    }

    private static final Map<String, Set<String>> SCRABBLE_WORDS = new java.util.HashMap<>();

    /** {MOT: row reference} of the language's Scrabble wordlist, in file order (empty when the language has none)
     * — transient, built only while a lexicon is loaded. At "easy" difficulty, only the words whose accented form is
     * a form or a lemma of the inflection table (mirrors _scrabble_entries). */
    static Map<String, Integer> scrabbleEntries(String language, boolean easy) {
        Map<String, Integer> refs = new LinkedHashMap<>();
        if (language == null || language.isEmpty()) return refs;
        Path path = scrabbleWordlistPath(language);
        if (!Files.exists(path)) return refs;
        Map<String, String> accented = easy ? new HashMap<>() : null;
        try {
            falcon.TextLines.forEachLine(path, (offset, line) -> {
                String[] parts = line.split("\t", -1);
                if (parts.length < 3 || !isAlpha(parts[0])) return;
                refs.put(parts[0], scrabbleRowRef(offset));
                if (accented != null) accented.put(parts[0], parts[1]);
            });
        } catch (IOException e) {
            throw new java.io.UncheckedIOException(e);
        }
        if (easy) {
            Set<String> known = loadInflectionKeys(language);
            refs.keySet().removeIf(w -> !known.contains(Py.lookupKey(accented.get(w))));
        }
        return refs;
    }

    /** Every form and every lemma of the language's inflection table (data/inflection/&lt;lang&gt;.jsonl),
     * lowercased with ligatures folded — the words with a known inflection. Not cached: only read while an "easy"
     * lexicon's Scrabble words are being selected (mirrors load_inflection_keys). */
    public static Set<String> loadInflectionKeys(String language) {
        if (language == null || language.isEmpty()) return Set.of();
        Set<String> keys = new HashSet<>();
        Path path = falcon.Env.ROOT.resolve("data").resolve("inflection").resolve(language + ".jsonl");
        if (Files.exists(path)) {
            try (BufferedReader r = Files.newBufferedReader(path, StandardCharsets.UTF_8)) {
                String line;
                while ((line = r.readLine()) != null) {
                    Object rec;
                    try {
                        rec = falcon.Json.parse(line);
                    } catch (RuntimeException e) {
                        continue;
                    }
                    if (!(rec instanceof Map<?, ?> m)) continue;
                    if (m.get("form") instanceof String f && !f.isEmpty()) keys.add(Py.lookupKey(f));
                    if (m.get("analyses") instanceof List<?> analyses) {
                        for (Object a : analyses) {
                            if (a instanceof Map<?, ?> am && am.get("lemma") instanceof String l && !l.isEmpty()) {
                                keys.add(Py.lookupKey(l));
                            }
                        }
                    }
                }
            } catch (IOException e) {
                throw new java.io.UncheckedIOException(e);
            }
        }
        return keys;
    }

    /** Stores {@code words} as the language's Scrabble word set for {@code easy}, unless one is cached already;
     * returns the cached set (mirrors _cache_scrabble_words). */
    private static synchronized Set<String> cacheScrabbleWords(String language, boolean easy, Collection<String> words) {
        String key = language + "|" + easy;
        Set<String> cached = SCRABBLE_WORDS.get(key);
        if (cached == null) {
            cached = java.util.Collections.unmodifiableSet(new HashSet<>(words));
            SCRABBLE_WORDS.put(key, cached);
        }
        return cached;
    }

    /** Every Scrabble word (grid form) of the language — at "easy" difficulty, only those with a known inflection.
     * The only Scrabble data kept in memory, once per language and difficulty class (mirrors load_scrabble_words). */
    public static Set<String> loadScrabbleWords(String language, boolean easy) {
        if (language == null || language.isEmpty()) return Set.of();
        synchronized (Words.class) {
            Set<String> cached = SCRABBLE_WORDS.get(language + "|" + easy);
            if (cached != null) return cached;
        }
        return cacheScrabbleWords(language, easy, scrabbleEntries(language, easy).keySet());
    }

    /** Adds the language's Scrabble wordlist to a loaded lexicon, in place, whatever its difficulty cut — whole, or
     * only its words with a known inflection when {@code easy}: a missing word joins byLength with the reference of
     * its own Scrabble row, and every added Scrabble word gets a frequency of at least NOISE_FREQUENCY_THRESHOLD.
     * Returns the Scrabble word set used (mirrors merge_scrabble_lexicon). */
    public static Set<String> mergeScrabbleLexicon(String language, Lexicon lex, double noiseThreshold, boolean easy) {
        Map<String, Integer> entries = scrabbleEntries(language, easy);
        entries.forEach((word, ref) -> {
            if (!lex.refs().containsKey(word)) {
                lex.refs().put(word, ref);
                lex.byLength().computeIfAbsent(word.length(), k -> new ArrayList<>()).add(word);
            }
            lex.frequencies().put(word, Math.max(lex.frequencies().getOrDefault(word, 0.0), noiseThreshold));
        });
        if (language == null || language.isEmpty()) return Set.of();
        return cacheScrabbleWords(language, easy, entries.keySet());
    }

    /** The Scrabble word set of a grid: one set, or one per direction on a bilingual grid (mirrors
     * scrabble_words_for_languages). */
    public static PW scrabbleWordsForLanguages(String language, String bilingualLanguage, boolean easy) {
        Set<String> across = loadScrabbleWords(language, easy);
        if (bilingualLanguage != null && !bilingualLanguage.isEmpty() && !bilingualLanguage.equals(language)) {
            return new PW(across, loadScrabbleWords(bilingualLanguage, easy), true);
        }
        return PW.single(across);
    }

    /** [row, col] of every cell of a fully lettered run (2 letters or more, across or down) of a JSON grid
     * (rows of "#", "." or a letter) spelling a word of that run's direction's Scrabble set. Sorted,
     * deduplicated (mirrors scrabble_word_cells). */
    public static List<Object> scrabbleWordCells(Object grid, PW scrabble) {
        java.util.TreeSet<Integer> found = new java.util.TreeSet<>();
        if (scrabble == null || scrabble.isEmpty() || !(grid instanceof List<?> rowsList) || rowsList.isEmpty()) {
            return Cells.toJson(found);
        }
        int rows = rowsList.size();
        Object first = rowsList.get(0);
        int cols = first instanceof String str ? str.length() : ((List<?>) first).size();
        for (int dir = 0; dir < 2; dir++) {
            Set<String> words = dir == 0 ? scrabble.across : (scrabble.dual ? scrabble.down : scrabble.across);
            if (words.isEmpty()) continue;
            int outer = dir == 0 ? rows : cols, inner = dir == 0 ? cols : rows;
            for (int a = 0; a < outer; a++) {
                List<Integer> run = new ArrayList<>();
                StringBuilder word = new StringBuilder();
                boolean complete = true;
                for (int b = 0; b <= inner; b++) {
                    int r = dir == 0 ? a : b, c = dir == 0 ? b : a;
                    Object row = b < inner ? rowsList.get(r) : null;
                    String ch = row == null ? "#" : row instanceof String str
                            ? String.valueOf(str.charAt(c)) : String.valueOf(((List<?>) row).get(c));
                    if (!ch.equals("#")) {
                        run.add(Cells.of(r, c));
                        word.append(ch);
                        if (ch.equals(".")) complete = false;
                        continue;
                    }
                    if (run.size() >= 2 && complete && words.contains(word.toString())) found.addAll(run);
                    run.clear();
                    word.setLength(0);
                    complete = true;
                }
            }
        }
        return Cells.toJson(found);
    }

    public static Set<String> challengeSet(Collection<String> raw) {
        Set<String> out = new HashSet<>();
        if (raw == null) return out;
        for (String w : raw) {
            String gf = challengeWordGridForm(String.valueOf(w));
            if (!gf.isEmpty()) out.add(gf);
        }
        return out;
    }
}
