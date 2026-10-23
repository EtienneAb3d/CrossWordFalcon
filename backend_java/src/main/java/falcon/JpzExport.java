package falcon;

import java.nio.charset.StandardCharsets;
import java.util.ArrayList;
import java.util.HashMap;
import java.util.List;
import java.util.Locale;
import java.util.Map;
import java.util.Set;

/**
 * JPZ export of a library grid ({@code GET /api/library/{grid_id}/jpz}): the Crossword Compiler XML format
 * (crossword-compiler-applet / rectangular-puzzle, uncompressed), which draws word breaks as bars between cells
 * (right-bar/bottom-bar) and carries each expression's enumeration ("3-2-4") in its clue's format attribute. Mirrors
 * backend/jpz_export.py.
 */
public final class JpzExport {
    private JpzExport() {}

    public static final String JPZ_MEDIA_TYPE = "application/xml";
    static final String JPZ_COPYRIGHT = "CrossWordFalcon";

    /** Clue-list headings, by grid language (English for any other) (mirrors JPZ_DIRECTION_TITLES). */
    static final Map<String, String[]> JPZ_DIRECTION_TITLES = Map.of(
            "fr", new String[]{"Horizontalement", "Verticalement"},
            "en", new String[]{"Across", "Down"},
            "de", new String[]{"Waagerecht", "Senkrecht"},
            "es", new String[]{"Horizontales", "Verticales"},
            "it", new String[]{"Orizzontali", "Verticali"},
            "pt", new String[]{"Horizontais", "Verticais"});

    /** {@code text} escaped for XML text and double-quoted attributes. */
    static String xml(String text) {
        return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace("\"", "&quot;");
    }

    /** The .jpz file (UTF-8 XML) of a library record, titled {@code title} (mirrors render_jpz). */
    @SuppressWarnings("unchecked")
    public static byte[] renderJpz(Map<String, Object> record, String title) {
        List<List<Object>> solution = (List<List<Object>>) (List<?>) record.get("solution");
        int width = Json.integer(record, "width", 0), height = Json.integer(record, "height", 0);
        Set<String> breaks = PuzExport.breakSet(record);
        List<PuzExport.Slot> slots = PuzExport.numberedSlots(solution);
        Map<String, Integer> numberAt = new HashMap<>();
        for (PuzExport.Slot s : slots) numberAt.put(s.row() + "," + s.col(), s.number());
        Map<String, Map<String, Object>> wordAt = PuzExport.wordsAt(record);
        String[] titles = JPZ_DIRECTION_TITLES.getOrDefault(String.valueOf(record.get("language")), JPZ_DIRECTION_TITLES.get("en"));
        String pseudo = record.get("pseudo") instanceof String p && !p.isEmpty() ? p : "CrossWordFalcon";
        List<String> lines = new ArrayList<>();
        lines.add("<?xml version=\"1.0\" encoding=\"UTF-8\"?>");
        lines.add("<crossword-compiler-applet xmlns=\"http://crossword.info/xml/crossword-compiler-applet\">");
        lines.add("<rectangular-puzzle xmlns=\"http://crossword.info/xml/rectangular-puzzle\" "
                + "alphabet=\"ABCDEFGHIJKLMNOPQRSTUVWXYZ\">");
        lines.add("<metadata>");
        lines.add("<title>" + xml(title == null || title.isEmpty() ? "CrossWordFalcon" : title) + "</title>");
        lines.add("<creator>" + xml(pseudo) + "</creator>");
        lines.add("<copyright>" + xml(JPZ_COPYRIGHT) + "</copyright>");
        lines.add("<description></description>");
        lines.add("</metadata>");
        lines.add("<crossword>");
        lines.add("<grid width=\"" + width + "\" height=\"" + height + "\">");
        lines.add("<grid-look numbering-scheme=\"normal\"/>");
        for (int r = 0; r < solution.size(); r++) {
            List<Object> row = solution.get(r);
            for (int c = 0; c < row.size(); c++) {
                String cell = String.valueOf(row.get(c));
                if ("#".equals(cell)) {
                    lines.add("<cell x=\"" + (c + 1) + "\" y=\"" + (r + 1) + "\" type=\"block\"/>");
                    continue;
                }
                StringBuilder attrs = new StringBuilder("x=\"" + (c + 1) + "\" y=\"" + (r + 1) + "\" solution=\""
                        + xml(cell.isEmpty() ? "" : cell.substring(0, 1).toUpperCase(Locale.ROOT)) + "\"");
                Integer n = numberAt.get(r + "," + c);
                if (n != null) attrs.append(" number=\"").append(n).append("\"");
                if (breaks.contains(r + "," + c + ",across")) attrs.append(" right-bar=\"true\"");
                if (breaks.contains(r + "," + c + ",down")) attrs.append(" bottom-bar=\"true\"");
                lines.add("<cell " + attrs + "/>");
            }
        }
        lines.add("</grid>");
        for (int k = 0; k < slots.size(); k++) {
            PuzExport.Slot s = slots.get(k);
            if ("across".equals(s.direction())) {
                lines.add("<word id=\"" + (k + 1) + "\" x=\"" + (s.col() + 1) + "-" + (s.col() + s.length()) + "\" y=\"" + (s.row() + 1) + "\"/>");
            } else {
                lines.add("<word id=\"" + (k + 1) + "\" x=\"" + (s.col() + 1) + "\" y=\"" + (s.row() + 1) + "-" + (s.row() + s.length()) + "\"/>");
            }
        }
        String[] wanted = {"across", "down"};
        for (int d = 0; d < 2; d++) {
            lines.add("<clues ordering=\"normal\">");
            lines.add("<title><b>" + xml(titles[d]) + "</b></title>");
            for (int k = 0; k < slots.size(); k++) {
                PuzExport.Slot s = slots.get(k);
                if (!wanted[d].equals(s.direction())) continue;
                Map<String, Object> word = wordAt.getOrDefault(s.row() + "," + s.col() + "," + s.direction(), Map.of());
                String enumeration = word.isEmpty() || breaks.isEmpty() ? null : PuzExport.wordEnumeration(word, breaks);
                String fmt = enumeration == null ? "" : " format=\"" + xml(enumeration) + "\"";
                String clue = word.get("clue") instanceof String t ? t : "";
                lines.add("<clue word=\"" + (k + 1) + "\" number=\"" + s.number() + "\"" + fmt + ">" + xml(clue) + "</clue>");
            }
            lines.add("</clues>");
        }
        lines.add("</crossword>");
        lines.add("</rectangular-puzzle>");
        lines.add("</crossword-compiler-applet>");
        lines.add("");
        return String.join("\n", lines).getBytes(StandardCharsets.UTF_8);
    }
}
