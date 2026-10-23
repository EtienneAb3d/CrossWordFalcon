package falcon;

import java.io.ByteArrayOutputStream;
import java.nio.ByteBuffer;
import java.nio.CharBuffer;
import java.nio.charset.CharacterCodingException;
import java.nio.charset.Charset;
import java.nio.charset.CharsetEncoder;
import java.nio.charset.CodingErrorAction;
import java.util.ArrayList;
import java.util.HashMap;
import java.util.HashSet;
import java.util.List;
import java.util.Locale;
import java.util.Map;
import java.util.Set;

/**
 * Across Lite (.puz) export of a library grid ({@code GET /api/library/{grid_id}/puz}): the binary format most
 * crossword apps open (version 1.3, text in Windows-1252, a character it lacks written "?"). A clue of a word holding
 * word breaks (a multi-word expression) ends with its enumeration, "(3-2-4)" (wordEnumeration), the format having no
 * way to draw them on the grid. The helpers shared with the ipuz/jpz exports (numberedSlots, wordEnumeration,
 * breakSet) live here too. Mirrors backend/puz_export.py.
 */
public final class PuzExport {
    private PuzExport() {}

    public static final String PUZ_MEDIA_TYPE = "application/x-crossword";
    static final byte[] PUZ_VERSION = {'1', '.', '3', 0};
    static final Charset PUZ_ENCODING = Charset.forName("windows-1252");
    static final String PUZ_COPYRIGHT = "CrossWordFalcon";

    /** The format's 16-bit rotating checksum of {@code data}, continuing {@code cksum}. */
    static int cksum(byte[] data, int cksum) {
        for (byte b : data) {
            cksum = (cksum & 1) != 0 ? (cksum >> 1) | 0x8000 : cksum >> 1;
            cksum = (cksum + (b & 0xFF)) & 0xFFFF;
        }
        return cksum;
    }

    static byte[] encode(String text) {
        if (text == null || text.isEmpty()) return new byte[0];
        CharsetEncoder enc = PUZ_ENCODING.newEncoder().onMalformedInput(CodingErrorAction.REPLACE)
                .onUnmappableCharacter(CodingErrorAction.REPLACE).replaceWith(new byte[]{'?'});
        try {
            ByteBuffer buf = enc.encode(CharBuffer.wrap(text));
            byte[] out = new byte[buf.remaining()];
            buf.get(out);
            return out;
        } catch (CharacterCodingException e) {
            throw new IllegalStateException(e);
        }
    }

    /** Characters separating two words of an expression's spelling (the same as Words' WORD_SEPARATORS). */
    static final String WORD_SEPARATORS = " -'\u2019";

    /** The record's word breaks ([row, col, direction]) as "row,col,direction" keys (mirrors break_set). */
    public static Set<String> breakSet(Map<String, Object> record) {
        Set<String> out = new HashSet<>();
        if (record.get("breaks") instanceof List<?> list) {
            for (Object o : list) {
                if (o instanceof List<?> b && b.size() == 3 && b.get(0) instanceof Number r && b.get(1) instanceof Number c) {
                    out.add(r.intValue() + "," + c.intValue() + "," + b.get(2));
                }
            }
        }
        return out;
    }

    /** {letter count: "-" or ","} of every place {@code spelling} separates two words, in grid letters: "-" where a
     * hyphen separates them, "," for a space or an apostrophe (mirrors _spelling_separators). */
    static Map<Integer, String> spellingSeparators(String spelling) {
        Map<Integer, String> seps = new HashMap<>();
        int n = 0;
        for (int i = 0; i < spelling.length(); ) {
            int cp = spelling.codePointAt(i);
            i += Character.charCount(cp);
            if (WORD_SEPARATORS.indexOf(cp) >= 0) {
                if (n > 0) {
                    if (cp == '-') seps.put(n, "-");
                    else seps.putIfAbsent(n, ",");
                }
                continue;
            }
            n += falcon.gen.Words.challengeWordGridForm(new String(Character.toChars(cp))).length();
        }
        return seps;
    }

    /** The enumeration of a record word ("3-2-4" for ARCENCIEL, spelled "arc-en-ciel"), null for a word with no
     * break (mirrors word_enumeration). */
    public static String wordEnumeration(Map<String, Object> word, Set<String> breaks) {
        Object a = word.get("answer");
        String answer = a == null ? "" : String.valueOf(a);
        Object direction = word.get("direction");
        boolean across = "across".equals(direction);
        int r0 = Json.integer(word, "row", 0), c0 = Json.integer(word, "col", 0);
        List<Integer> cuts = new ArrayList<>();
        for (int i = 0; i < answer.length() - 1; i++) {
            int r = r0 + (across ? 0 : i), c = c0 + (across ? i : 0);
            if (breaks.contains(r + "," + c + "," + direction)) cuts.add(i + 1);
        }
        if (cuts.isEmpty()) return null;
        Object acc = word.get("accented");
        Map<Integer, String> seps = spellingSeparators(acc == null ? "" : String.valueOf(acc));
        StringBuilder out = new StringBuilder();
        int prev = 0;
        for (int cut : cuts) {
            out.append(cut - prev).append(seps.getOrDefault(cut, ","));
            prev = cut;
        }
        return out.append(answer.length() - prev).toString();
    }

    /** One word of the grid: its number, first cell, direction and length (mirrors numbered_slots' tuples). */
    public record Slot(int number, int row, int col, String direction, int length) {}

    /** Every word of the grid in the formats' order — cells row by row, for each cell the across word starting
     * there, then the down word —, numbered like a printed grid (mirrors numbered_slots). */
    public static List<Slot> numberedSlots(List<List<Object>> solution) {
        List<Slot> slots = new ArrayList<>();
        int number = 0;
        for (int r = 0; r < solution.size(); r++) {
            for (int c = 0; c < solution.get(r).size(); c++) {
                if (!white(solution, r, c)) continue;
                boolean across = !white(solution, r, c - 1) && white(solution, r, c + 1);
                boolean down = !white(solution, r - 1, c) && white(solution, r + 1, c);
                if (!(across || down)) continue;
                number++;
                if (across) {
                    int len = 0;
                    while (white(solution, r, c + len)) len++;
                    slots.add(new Slot(number, r, c, "across", len));
                }
                if (down) {
                    int len = 0;
                    while (white(solution, r + len, c)) len++;
                    slots.add(new Slot(number, r, c, "down", len));
                }
            }
        }
        return slots;
    }

    /** "row,col,direction" -> record word (the words' own first cell and direction). */
    @SuppressWarnings("unchecked")
    public static Map<String, Map<String, Object>> wordsAt(Map<String, Object> record) {
        Map<String, Map<String, Object>> out = new HashMap<>();
        if (record.get("words") instanceof List<?> list) {
            for (Object o : list) {
                Map<String, Object> w = (Map<String, Object>) o;
                out.put(Json.integer(w, "row", 0) + "," + Json.integer(w, "col", 0) + "," + w.get("direction"), w);
            }
        }
        return out;
    }

    /**
     * The clues in the format's order: cells row by row, for each cell the across word starting there, then the down
     * word. A word is a run of at least 2 white cells; one the record has no clue for gets "". A word with an
     * enumeration (wordEnumeration) gets it appended, "(3-2-4)".
     */
    @SuppressWarnings("unchecked")
    static List<String> cluesInOrder(List<List<Object>> solution, List<Object> words, Set<String> breaks) {
        int rows = solution.size(), cols = rows == 0 ? 0 : solution.get(0).size();
        Map<String, String> clueAt = new HashMap<>();
        for (Object o : words) {
            Map<String, Object> w = (Map<String, Object>) o;
            Object clue = w.get("clue");
            String text = clue instanceof String s ? s : "";
            String enumeration = breaks.isEmpty() ? null : wordEnumeration(w, breaks);
            if (enumeration != null) text = text.isEmpty() ? "(" + enumeration + ")" : text + " (" + enumeration + ")";
            clueAt.put(Json.integer(w, "row", 0) + "," + Json.integer(w, "col", 0) + "," + w.get("direction"), text);
        }
        List<String> clues = new ArrayList<>();
        for (int r = 0; r < rows; r++) {
            for (int c = 0; c < cols; c++) {
                if (!white(solution, r, c)) continue;
                if (!white(solution, r, c - 1) && white(solution, r, c + 1)) {
                    clues.add(clueAt.getOrDefault(r + "," + c + ",across", ""));
                }
                if (!white(solution, r - 1, c) && white(solution, r + 1, c)) {
                    clues.add(clueAt.getOrDefault(r + "," + c + ",down", ""));
                }
            }
        }
        return clues;
    }

    static boolean white(List<List<Object>> solution, int r, int c) {
        return r >= 0 && r < solution.size() && c >= 0 && c < solution.get(r).size()
                && !"#".equals(String.valueOf(solution.get(r).get(c)));
    }

    private static void le16(ByteArrayOutputStream out, int v) {
        out.write(v & 0xFF);
        out.write((v >> 8) & 0xFF);
    }

    /**
     * The .puz file of a library record (width/height/solution/words), titled {@code title}, its author the record's
     * pseudo (else "CrossWordFalcon").
     */
    @SuppressWarnings("unchecked")
    public static byte[] renderPuz(Map<String, Object> record, String title) {
        List<List<Object>> solution = (List<List<Object>>) (List<?>) record.get("solution");
        int width = Json.integer(record, "width", 0), height = Json.integer(record, "height", 0);
        StringBuilder answer = new StringBuilder(), fill = new StringBuilder();
        for (List<Object> row : solution) {
            for (Object cell : row) {
                String s = String.valueOf(cell);
                boolean black = "#".equals(s);
                answer.append(black ? "." : s.isEmpty() ? "" : s.substring(0, 1).toUpperCase(Locale.ROOT));
                fill.append(black ? '.' : '-');
            }
        }
        byte[] answerBytes = encode(answer.toString()), fillBytes = encode(fill.toString());
        Object wordsObj = record.get("words");
        List<String> clues = cluesInOrder(solution, wordsObj instanceof List<?> l ? (List<Object>) l : List.of(),
                breakSet(record));
        String pseudo = record.get("pseudo") instanceof String p && !p.isEmpty() ? p : "CrossWordFalcon";
        byte[] titleBytes = encode(title == null || title.isEmpty() ? "CrossWordFalcon" : title);
        byte[] authorBytes = encode(pseudo);
        byte[] copyrightBytes = encode(PUZ_COPYRIGHT);
        List<byte[]> clueBytes = new ArrayList<>();
        for (String clue : clues) clueBytes.add(encode(clue));
        byte[] notesBytes = new byte[0];

        ByteArrayOutputStream cibOut = new ByteArrayOutputStream();
        cibOut.write(width);
        cibOut.write(height);
        le16(cibOut, clues.size());
        le16(cibOut, 1);
        le16(cibOut, 0);
        byte[] cib = cibOut.toByteArray();
        int cibCksum = cksum(cib, 0);

        java.util.function.IntUnaryOperator textCksum = start -> {
            int ck = start;
            for (byte[] part : new byte[][]{titleBytes, authorBytes, copyrightBytes}) {
                if (part.length > 0) ck = cksum(zstring(part), ck);
            }
            for (byte[] clue : clueBytes) if (clue.length > 0) ck = cksum(clue, ck);
            if (notesBytes.length > 0) ck = cksum(zstring(notesBytes), ck);
            return ck;
        };
        int overall = textCksum.applyAsInt(cksum(fillBytes, cksum(answerBytes, cibCksum)));
        int[] parts = {cibCksum, cksum(answerBytes, 0), cksum(fillBytes, 0), textCksum.applyAsInt(0)};
        byte[] magic = "ICHEATED".getBytes(java.nio.charset.StandardCharsets.US_ASCII);

        ByteArrayOutputStream out = new ByteArrayOutputStream();
        le16(out, overall);
        out.writeBytes("ACROSS&DOWN\0".getBytes(java.nio.charset.StandardCharsets.US_ASCII));
        le16(out, cibCksum);
        for (int i = 0; i < 4; i++) out.write(magic[i] ^ (parts[i] & 0xFF));
        for (int i = 0; i < 4; i++) out.write(magic[4 + i] ^ (parts[i] >> 8));
        out.writeBytes(PUZ_VERSION);
        out.writeBytes(new byte[2 + 2 + 12]);
        out.writeBytes(cib);
        out.writeBytes(answerBytes);
        out.writeBytes(fillBytes);
        for (byte[] part : new byte[][]{titleBytes, authorBytes, copyrightBytes}) out.writeBytes(zstring(part));
        for (byte[] clue : clueBytes) out.writeBytes(zstring(clue));
        out.writeBytes(zstring(notesBytes));
        return out.toByteArray();
    }

    private static byte[] zstring(byte[] b) {
        byte[] out = new byte[b.length + 1];
        System.arraycopy(b, 0, out, 0, b.length);
        return out;
    }
}
