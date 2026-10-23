package falcon;

import java.nio.charset.StandardCharsets;
import java.util.ArrayList;
import java.util.HashMap;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Locale;
import java.util.Map;
import java.util.Set;

/**
 * ipuz export of a library grid ({@code GET /api/library/{grid_id}/ipuz}): the open JSON crossword format (ipuz v2,
 * crossword kind), which draws word breaks as bars between cells ({@code "style": {"barred": ...}}) and carries each
 * expression's enumeration ("3-2-4") with its clue. Mirrors backend/ipuz_export.py.
 */
public final class IpuzExport {
    private IpuzExport() {}

    public static final String IPUZ_MEDIA_TYPE = "application/json";
    static final String IPUZ_COPYRIGHT = "CrossWordFalcon";

    /** The ipuz "barred" sides of cell (r, c): "R" for a break after it across, "B" for one after it down. */
    static String cellBars(int r, int c, Set<String> breaks) {
        return (breaks.contains(r + "," + c + ",across") ? "R" : "") + (breaks.contains(r + "," + c + ",down") ? "B" : "");
    }

    /** The ipuz document of a library record, titled {@code title} (mirrors ipuz_document). */
    @SuppressWarnings("unchecked")
    public static Map<String, Object> ipuzDocument(Map<String, Object> record, String title) {
        List<List<Object>> solution = (List<List<Object>>) (List<?>) record.get("solution");
        int width = Json.integer(record, "width", 0), height = Json.integer(record, "height", 0);
        Set<String> breaks = PuzExport.breakSet(record);
        List<PuzExport.Slot> slots = PuzExport.numberedSlots(solution);
        Map<String, Integer> numberAt = new HashMap<>();
        for (PuzExport.Slot s : slots) numberAt.put(s.row() + "," + s.col(), s.number());
        Map<String, Map<String, Object>> wordAt = PuzExport.wordsAt(record);
        List<Object> puzzle = new ArrayList<>(), answers = new ArrayList<>();
        for (int r = 0; r < solution.size(); r++) {
            List<Object> prow = new ArrayList<>(), srow = new ArrayList<>();
            List<Object> row = solution.get(r);
            for (int c = 0; c < row.size(); c++) {
                String cell = String.valueOf(row.get(c));
                if ("#".equals(cell)) {
                    prow.add("#");
                    srow.add("#");
                    continue;
                }
                int value = numberAt.getOrDefault(r + "," + c, 0);
                String bars = cellBars(r, c, breaks);
                if (bars.isEmpty()) {
                    prow.add(value);
                } else {
                    Map<String, Object> styled = new LinkedHashMap<>();
                    styled.put("cell", value);
                    styled.put("style", Map.of("barred", bars));
                    prow.add(styled);
                }
                srow.add(cell.isEmpty() ? "" : cell.substring(0, 1).toUpperCase(Locale.ROOT));
            }
            puzzle.add(prow);
            answers.add(srow);
        }
        List<Object> across = new ArrayList<>(), down = new ArrayList<>();
        for (PuzExport.Slot s : slots) {
            Map<String, Object> word = wordAt.getOrDefault(s.row() + "," + s.col() + "," + s.direction(), Map.of());
            Map<String, Object> entry = new LinkedHashMap<>();
            entry.put("number", s.number());
            entry.put("clue", word.get("clue") instanceof String t ? t : "");
            String enumeration = word.isEmpty() || breaks.isEmpty() ? null : PuzExport.wordEnumeration(word, breaks);
            if (enumeration != null) entry.put("enumeration", enumeration);
            ("across".equals(s.direction()) ? across : down).add(entry);
        }
        Map<String, Object> clues = new LinkedHashMap<>();
        clues.put("Across", across);
        clues.put("Down", down);
        Map<String, Object> doc = new LinkedHashMap<>();
        doc.put("version", "http://ipuz.org/v2");
        doc.put("kind", List.of("http://ipuz.org/crossword#1"));
        doc.put("title", title == null || title.isEmpty() ? "CrossWordFalcon" : title);
        doc.put("author", record.get("pseudo") instanceof String p && !p.isEmpty() ? p : "CrossWordFalcon");
        doc.put("copyright", IPUZ_COPYRIGHT);
        doc.put("publisher", IPUZ_COPYRIGHT);
        Map<String, Object> dims = new LinkedHashMap<>();
        dims.put("width", width);
        dims.put("height", height);
        doc.put("dimensions", dims);
        doc.put("block", "#");
        doc.put("empty", 0);
        doc.put("puzzle", puzzle);
        doc.put("solution", answers);
        doc.put("clues", clues);
        return doc;
    }

    /** The .ipuz file (UTF-8 JSON, compact) of a library record (mirrors render_ipuz). */
    public static byte[] renderIpuz(Map<String, Object> record, String title) {
        return Json.dumps(ipuzDocument(record, title)).getBytes(StandardCharsets.UTF_8);
    }
}
