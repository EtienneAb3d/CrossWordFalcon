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
import java.util.List;
import java.util.Locale;
import java.util.Map;

/**
 * Across Lite (.puz) export of a library grid ({@code GET /api/library/{grid_id}/puz}): the binary format most
 * crossword apps open (version 1.3, text in Windows-1252, a character it lacks written "?"). Mirrors
 * backend/puz_export.py.
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

    /**
     * The clues in the format's order: cells row by row, for each cell the across word starting there, then the down
     * word. A word is a run of at least 2 white cells; one the record has no clue for gets "".
     */
    @SuppressWarnings("unchecked")
    static List<String> cluesInOrder(List<List<Object>> solution, List<Object> words) {
        int rows = solution.size(), cols = rows == 0 ? 0 : solution.get(0).size();
        Map<String, String> clueAt = new HashMap<>();
        for (Object o : words) {
            Map<String, Object> w = (Map<String, Object>) o;
            Object clue = w.get("clue");
            String text = clue instanceof String s ? s : "";
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

    private static boolean white(List<List<Object>> solution, int r, int c) {
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
        List<String> clues = cluesInOrder(solution, wordsObj instanceof List<?> l ? (List<Object>) l : List.of());
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
