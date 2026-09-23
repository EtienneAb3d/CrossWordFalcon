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

    private static final Pattern LANG_RE = Pattern.compile("wordlist_([a-z]{2})_full\\.tsv$");

    public static String langFromPath(String path) {
        if (path == null) return null;
        String base = Path.of(path).getFileName().toString();
        Matcher m = LANG_RE.matcher(base);
        return m.find() ? m.group(1) : null;
    }

    /** The loaded lexicon (Python's {@code (by_length, accents, canonicals, frequencies)}). */
    public record Lexicon(Map<Integer, List<String>> byLength, Map<String, String> accents,
                          Map<String, List<String>> canonicals, Map<String, Double> frequencies) {}

    private record Entry(String word, String accented, double freq, List<String> canonical) {}

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

    /** {@code maxWords}: null, an Integer (absolute count) or a Double
     * (fraction of the loaded lexicon). */
    public static Lexicon loadWordlist(String path, Number maxWords, boolean requireGloss, boolean excludeProperNouns)
            throws IOException {
        List<Entry> entries = new ArrayList<>();
        try (BufferedReader r = Files.newBufferedReader(Path.of(path), StandardCharsets.UTF_8)) {
            String line;
            while ((line = r.readLine()) != null) {
                if (line.isEmpty() || line.startsWith("#")) continue;
                String[] parts = line.split("\t", -1);
                if (parts.length >= 4) {
                    String word = parts[0].toUpperCase(Locale.ROOT);
                    List<String> canonical = new ArrayList<>();
                    for (String c : parts[3].split(";", -1)) if (!c.isEmpty()) canonical.add(c);
                    if (isAlpha(word)) entries.add(new Entry(word, parts[1], parseFreq(parts[2]),
                            canonical.isEmpty() ? List.of(parts[1]) : canonical));
                } else if (parts.length == 3) {
                    String word = parts[0].toUpperCase(Locale.ROOT);
                    if (isAlpha(word)) entries.add(new Entry(word, parts[1], parseFreq(parts[2]), List.of(parts[1])));
                } else if (parts.length == 2) {
                    String word = parts[0].toUpperCase(Locale.ROOT);
                    if (isAlpha(word)) entries.add(new Entry(word, word, parseFreq(parts[1]), List.of(word)));
                } else {
                    String t = Py.strip(line.toUpperCase(Locale.ROOT));
                    if (t.isEmpty()) continue;
                    for (String tok : Py.split(t)) if (isAlpha(tok)) entries.add(new Entry(tok, tok, 0.0, List.of(tok)));
                }
            }
        }
        LinkedHashMap<String, Entry> best = new LinkedHashMap<>();
        for (Entry e : entries) {
            Entry cur = best.get(e.word);
            if (cur == null || e.freq > cur.freq) {
                if (cur == null) best.put(e.word, e);
                else best.replace(e.word, e);
            }
        }
        String lang = langFromPath(path);
        if (requireGloss && lang != null && GlossLookup.hasGlossDictionary(lang)) {
            best.values().removeIf(v -> {
                List<String> cands = new ArrayList<>();
                cands.add(v.accented);
                cands.addAll(v.canonical);
                return !GlossLookup.hasAnyGloss(cands, lang);
            });
        }
        if (excludeProperNouns && lang != null && !PROPER_NOUN_EXCLUDED_LANGS.contains(lang)) {
            best.values().removeIf(v -> !v.accented.isEmpty() && Character.isUpperCase(v.accented.charAt(0)));
        }
        List<Entry> ranked = new ArrayList<>(best.values());
        ranked.sort((a, b) -> Double.compare(b.freq, a.freq));
        if (maxWords != null && maxWords.doubleValue() != 0) {
            int keep = maxWords instanceof Double d ? (int) Math.rint(ranked.size() * d) : maxWords.intValue();
            if (keep < ranked.size()) ranked = new ArrayList<>(ranked.subList(0, Math.max(0, keep)));
        }
        Map<Integer, List<String>> byLength = new LinkedHashMap<>();
        Map<String, String> accents = new LinkedHashMap<>();
        Map<String, List<String>> canonicals = new LinkedHashMap<>();
        Map<String, Double> frequencies = new LinkedHashMap<>();
        for (Entry e : ranked) {
            byLength.computeIfAbsent(e.word.length(), k -> new ArrayList<>()).add(e.word);
            accents.put(e.word, e.accented);
            canonicals.put(e.word, e.canonical);
            frequencies.put(e.word, e.freq);
        }
        return new Lexicon(byLength, accents, canonicals, frequencies);
    }

    public static Map<Integer, LenIndex> buildIndex(Map<Integer, List<String>> byLength, Map<String, Double> frequencies) {
        Map<Integer, LenIndex> index = new LinkedHashMap<>();
        byLength.forEach((len, words) -> index.put(len, new LenIndex(len, words, frequencies)));
        return index;
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
