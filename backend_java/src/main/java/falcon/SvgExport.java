package falcon;

import java.io.ByteArrayOutputStream;
import java.io.IOException;
import java.io.InputStream;
import java.io.OutputStream;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.time.LocalDate;
import java.time.LocalDateTime;
import java.time.format.DateTimeFormatter;
import java.util.ArrayList;
import java.util.Base64;
import java.util.HashMap;
import java.util.List;
import java.util.Map;
import java.util.TreeMap;

/**
 * Renders a generate_grid()-shaped result (a JSON-shaped Map) to a
 * self-contained SVG, and converts it to PNG/PDF through the external
 * {@code rsvg-convert} binary (mirrors backend/svg_export.py).
 */
public final class SvgExport {
    private SvgExport() {}

    public static final Path GRID_SVG_DIR = Env.path("GRID_SVG");
    public static final Path GRID_PNG_DIR = Env.path("GRID_PNG");
    private static final Path LOGO_PATH = Env.path("frontend", "static", "logo.png");
    private static final Path VERSION_PATH = Env.path("VERSION.txt");

    static final int CELL_SIZE = 26;
    static final int LINE_HEIGHT = 16;
    static final int MARGIN = 16;
    static final int MIN_CANVAS_WIDTH = 720;
    static final int HEADER_LOGO_SIZE = 48;
    static final int HEADER_HEIGHT = HEADER_LOGO_SIZE + 16 + 18;
    static final int GRID_SIDEBAR_GAP = 24;
    static final int DOWN_COLUMN_GAP = 24;
    static final double MAX_GRID_WIDTH_FRACTION = 0.6;
    static final int PNG_DPI = 300;
    static final double CHAR_WIDTH_FACTOR = 0.58;
    static final double BOLD_CHAR_WIDTH_FACTOR = 0.65;
    public static final String PLAY_ONLINE_BASE_URL = "https://falcon.cubaix.com/";

    private static final Map<String, String[]> HEADINGS = Map.of(
            "fr", new String[]{"Horizontalement", "Verticalement", "Solution"},
            "en", new String[]{"Across", "Down", "Solution"},
            "de", new String[]{"Waagerecht", "Senkrecht", "Lösung"},
            "es", new String[]{"Horizontales", "Verticales", "Solución"},
            "it", new String[]{"Orizzontali", "Verticali", "Soluzione"},
            "pt", new String[]{"Horizontais", "Verticais", "Solução"});
    private static final Map<String, String> NO_DEFINITION = Map.of(
            "fr", "Définition indisponible", "en", "No definition available",
            "de", "Keine Definition verfügbar", "es", "Definición no disponible",
            "it", "Definizione non disponibile", "pt", "Definição indisponível");
    private static final Map<String, String> NATIVE_LANGUAGE_NAMES = Map.of(
            "fr", "Français", "en", "English", "de", "Deutsch", "es", "Español", "it", "Italiano", "pt", "Português");
    private static final Map<String, String> DIFFICULTY_LABEL = Map.of(
            "fr", "Difficulté", "en", "Difficulty", "de", "Schwierigkeit", "es", "Dificultad", "it", "Difficoltà",
            "pt", "Dificuldade");
    private static final Map<String, Map<String, String>> DIFFICULTY_NAMES = Map.of(
            "fr", Map.of("easy", "Facile", "medium", "Moyenne", "hard", "Difficile"),
            "en", Map.of("easy", "Easy", "medium", "Medium", "hard", "Hard"),
            "de", Map.of("easy", "Leicht", "medium", "Mittel", "hard", "Schwer"),
            "es", Map.of("easy", "Fácil", "medium", "Media", "hard", "Difícil"),
            "it", Map.of("easy", "Facile", "medium", "Media", "hard", "Difficile"),
            "pt", Map.of("easy", "Fácil", "medium", "Média", "hard", "Difícil"));
    private static final Map<String, String> MODE_LABEL = Map.of(
            "fr", "Mode", "en", "Mode", "de", "Modus", "es", "Modo", "it", "Modalità", "pt", "Modo");
    private static final Map<String, Map<String, String>> MODE_NAMES = Map.of(
            "fr", Map.of("flash", "Flash", "turbo", "Turbo", "fast", "Rapide", "medium", "Moyen", "ultra", "Ultra"),
            "en", Map.of("flash", "Flash", "turbo", "Turbo", "fast", "Fast", "medium", "Medium", "ultra", "Ultra"),
            "de", Map.of("flash", "Flash", "turbo", "Turbo", "fast", "Schnell", "medium", "Mittel", "ultra", "Ultra"),
            "es", Map.of("flash", "Flash", "turbo", "Turbo", "fast", "Rápido", "medium", "Medio", "ultra", "Ultra"),
            "it", Map.of("flash", "Flash", "turbo", "Turbo", "fast", "Veloce", "medium", "Medio", "ultra", "Ultra"),
            "pt", Map.of("flash", "Flash", "turbo", "Turbo", "fast", "Rápido", "medium", "Médio", "ultra", "Ultra"));
    private static final Map<String, String[]> DURATION_LABELS = Map.of(
            "fr", new String[]{"Grille générée en", "Optimisation en", "Définitions générées en"},
            "en", new String[]{"Grid generated in", "Optimized in", "Definitions generated in"},
            "de", new String[]{"Gitter erzeugt in", "Optimiert in", "Definitionen erzeugt in"},
            "es", new String[]{"Crucigrama generado en", "Optimizado en", "Definiciones generadas en"},
            "it", new String[]{"Griglia generata in", "Ottimizzata in", "Definizioni generate in"},
            "pt", new String[]{"Grelha gerada em", "Otimizada em", "Definições geradas em"});
    private static final Map<String, String> BLACK_RATIO_LABELS = Map.of(
            "fr", "{p} % noir", "en", "{p}% black", "de", "{p}% schwarz", "es", "{p}% negro", "it", "{p}% nero",
            "pt", "{p}% preto");
    private static final Map<String, String> PLAY_ONLINE_LABELS = Map.of(
            "fr", "Jouer en ligne (avec solution) : {url}",
            "en", "Play online (with solution): {url}",
            "de", "Online spielen (mit Lösung): {url}",
            "es", "Jugar en línea (con solución): {url}",
            "it", "Gioca online (con soluzione): {url}",
            "pt", "Jogar online (com solução): {url}");

    /** Python-style number formatting: ints print bare, doubles in their
     * shortest repr ("13.0", "1003.3333333333334"). */
    static String num(double d) {
        return Double.toString(d);
    }

    static String f1(double d) {
        return Py.fmt(d, 1);
    }

    static String escape(String s) {
        return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;");
    }

    static String formatDuration(double seconds) {
        long total = Math.max(0, Py.round(seconds));
        long h = total / 3600, rem = total % 3600, m = rem / 60, s = rem % 60;
        StringBuilder out = new StringBuilder();
        if (h > 0) out.append(h).append("h");
        if (h > 0 || m > 0) out.append(m).append("mn");
        out.append(s).append("s");
        return out.toString();
    }

    static double textWidth(String text, int fontSize, boolean bold) {
        return text.length() * fontSize * (bold ? BOLD_CHAR_WIDTH_FACTOR : CHAR_WIDTH_FACTOR);
    }

    static List<String> wrapLine(String text, int fontSize, double maxWidth) {
        List<String> lines = new ArrayList<>();
        String current = "";
        for (String word : text.split(" ", -1)) {
            String candidate = current.isEmpty() ? word : current + " " + word;
            if (!current.isEmpty() && textWidth(candidate, fontSize, false) > maxWidth) {
                lines.add(current);
                current = word;
            } else {
                current = candidate;
            }
        }
        lines.add(current);
        return lines;
    }

    static String headingSvg(Object x, int y, String text) {
        return "<text x=\"" + x + "\" y=\"" + (y + 12) + "\" font-size=\"13\" font-family=\"sans-serif\" "
                + "font-weight=\"bold\">" + escape(text) + "</text>";
    }

    record Line(int pos, String text) {}

    /** Returns [markup, height]. */
    static Object[] clueLinesSvg(Object x, double width, int y0, List<Line> lines) {
        int fontSize = 11;
        StringBuilder parts = new StringBuilder();
        int y = y0;
        double xNum = ((Number) x).doubleValue();
        for (Line l : lines) {
            String prefix = (l.pos + 1) + " ";
            double indent = textWidth(prefix, fontSize, true);
            List<String> wrapped = wrapLine(l.text, fontSize, width - indent);
            parts.append("<text x=\"").append(x).append("\" y=\"").append(y + 10).append("\" font-size=\"")
                    .append(fontSize).append("\" font-family=\"sans-serif\"><tspan font-weight=\"bold\">")
                    .append(l.pos + 1).append("</tspan> ").append(escape(wrapped.get(0))).append("</text>");
            y += LINE_HEIGHT;
            for (String cont : wrapped.subList(1, wrapped.size())) {
                parts.append("<text x=\"").append(f1(xNum + indent)).append("\" y=\"").append(y + 10)
                        .append("\" font-size=\"").append(fontSize).append("\" font-family=\"sans-serif\">")
                        .append(escape(cont)).append("</text>");
                y += LINE_HEIGHT;
            }
        }
        return new Object[]{parts.toString(), y - y0};
    }

    private static volatile String logoCache;

    static String logoDataUri() {
        if (logoCache == null) {
            try {
                logoCache = "data:image/png;base64," + Base64.getEncoder().encodeToString(Files.readAllBytes(LOGO_PATH));
            } catch (IOException e) {
                logoCache = "";
            }
        }
        return logoCache;
    }

    static List<Line> groupClueLines(List<Object> words, String direction, String positionKey, String language) {
        String noDef = NO_DEFINITION.getOrDefault(language, NO_DEFINITION.get("en"));
        String secondary = positionKey.equals("row") ? "col" : "row";
        TreeMap<Integer, List<Object>> byPos = new TreeMap<>();
        for (Object w : words) {
            if (!direction.equals(Json.str(w, "direction", ""))) continue;
            byPos.computeIfAbsent(Json.integer(w, positionKey, 0), k -> new ArrayList<>()).add(w);
        }
        List<Line> lines = new ArrayList<>();
        for (Map.Entry<Integer, List<Object>> e : byPos.entrySet()) {
            List<Object> entries = new ArrayList<>(e.getValue());
            entries.sort((a, b) -> Integer.compare(Json.integer(a, secondary, 0), Json.integer(b, secondary, 0)));
            List<String> bits = new ArrayList<>();
            for (Object w : entries) {
                Object clue = Json.get(w, "clue");
                String c = Json.truthy(clue) ? clue.toString() : noDef;
                bits.add("(" + Json.get(w, "number") + ") " + c);
            }
            lines.add(new Line(e.getKey(), String.join(" — ", bits)));
        }
        return lines;
    }

    static String cell(List<Object> grid, int r, int c) {
        Object row = grid.get(r);
        if (row instanceof List<?> l) {
            Object v = l.get(c);
            return v == null ? null : v.toString();
        }
        String s = row.toString();
        return c < s.length() ? String.valueOf(s.charAt(c)) : null;
    }

    static int rowLength(Object row) {
        return row instanceof List<?> l ? l.size() : row.toString().length();
    }

    /** Returns [markup, height, width]. */
    static Object[] gridSvg(List<Object> pattern, List<Object> letters, List<Object> words, int yOffset, Object xOffset) {
        int rows = pattern.size(), cols = rowLength(pattern.get(0));
        Map<String, Object> numberByCell = new HashMap<>();
        for (Object w : words) numberByCell.put(Json.integer(w, "row", 0) + "," + Json.integer(w, "col", 0), Json.get(w, "number"));
        StringBuilder parts = new StringBuilder();
        double xo = ((Number) xOffset).doubleValue();
        boolean xInt = xOffset instanceof Integer;
        // Python keeps an int x-offset an int and a float one a float.
        java.util.function.DoubleFunction<String> fmt = v -> xInt ? String.valueOf((long) v) : num(v);
        double gridX0 = xo + CELL_SIZE;
        int gridY0 = yOffset + CELL_SIZE;
        for (int c = 0; c < cols; c++) {
            double x = gridX0 + c * CELL_SIZE;
            parts.append("<text x=\"").append(num(x + CELL_SIZE / 2.0)).append("\" y=\"")
                    .append(num(yOffset + CELL_SIZE / 2.0 + 4)).append("\" font-size=\"10\" font-family=\"sans-serif\" "
                            + "text-anchor=\"middle\" fill=\"#4b5563\">").append(c + 1).append("</text>");
        }
        for (int r = 0; r < rows; r++) {
            int y = gridY0 + r * CELL_SIZE;
            parts.append("<text x=\"").append(num(xo + CELL_SIZE / 2.0)).append("\" y=\"")
                    .append(num(y + CELL_SIZE / 2.0 + 4)).append("\" font-size=\"10\" font-family=\"sans-serif\" "
                            + "text-anchor=\"middle\" fill=\"#4b5563\">").append(r + 1).append("</text>");
        }
        for (int r = 0; r < rows; r++) {
            for (int c = 0; c < cols; c++) {
                double x = gridX0 + c * CELL_SIZE;
                int y = gridY0 + r * CELL_SIZE;
                String xs = fmt.apply(x);
                if ("#".equals(cell(pattern, r, c))) {
                    parts.append("<rect x=\"").append(xs).append("\" y=\"").append(y).append("\" width=\"")
                            .append(CELL_SIZE).append("\" height=\"").append(CELL_SIZE).append("\" fill=\"#1f2937\"/>");
                    continue;
                }
                parts.append("<rect x=\"").append(xs).append("\" y=\"").append(y).append("\" width=\"")
                        .append(CELL_SIZE).append("\" height=\"").append(CELL_SIZE)
                        .append("\" fill=\"#ffffff\" stroke=\"#1f2937\" stroke-width=\"1\"/>");
                Object number = numberByCell.get(r + "," + c);
                if (Json.truthy(number)) {
                    parts.append("<text x=\"").append(fmt.apply(x + 2)).append("\" y=\"").append(y + 9)
                            .append("\" font-size=\"7\" font-family=\"sans-serif\">").append(number).append("</text>");
                }
                if (letters != null) {
                    String letter = cell(letters, r, c);
                    if (letter != null && !letter.isEmpty() && !letter.equals("#")) {
                        parts.append("<text x=\"").append(num(x + CELL_SIZE / 2.0)).append("\" y=\"")
                                .append(y + CELL_SIZE - 7).append("\" font-size=\"")
                                .append(Py.fmt(CELL_SIZE * 0.55, 0))
                                .append("\" font-family=\"sans-serif\" text-anchor=\"middle\">")
                                .append(escape(letter)).append("</text>");
                    }
                }
            }
        }
        return new Object[]{parts.toString(), CELL_SIZE + rows * CELL_SIZE, CELL_SIZE + cols * CELL_SIZE};
    }

    private static String version() {
        try {
            return Py.strip(Files.readString(VERSION_PATH, StandardCharsets.UTF_8));
        } catch (IOException e) {
            return "";
        }
    }

    public static String renderGridSvg(Map<String, Object> result, String language, String difficulty, String mode) {
        List<Object> words = Json.listOrEmpty(result.get("words"));
        String[] headings = HEADINGS.getOrDefault(language, HEADINGS.get("en"));
        List<Line> across = groupClueLines(words, "across", "row", language);
        List<Line> down = groupClueLines(words, "down", "col", language);
        int gridWidthPx = CELL_SIZE + Json.integer(result, "width", 0) * CELL_SIZE;
        int sidebarWidth = gridWidthPx;
        int canvasWidth = Math.max(2 * gridWidthPx + GRID_SIDEBAR_GAP + 2 * MARGIN, MIN_CANVAS_WIDTH);
        StringBuilder parts = new StringBuilder();
        int y = MARGIN;
        int logoX = MARGIN, logoY = y;
        parts.append("<image x=\"").append(logoX).append("\" y=\"").append(logoY).append("\" width=\"")
                .append(HEADER_LOGO_SIZE).append("\" height=\"").append(HEADER_LOGO_SIZE).append("\" href=\"")
                .append(logoDataUri()).append("\"/>");
        int textX = logoX + HEADER_LOGO_SIZE + 12;
        String languageName = NATIVE_LANGUAGE_NAMES.getOrDefault(language, language);
        String diffLabel = DIFFICULTY_LABEL.getOrDefault(language, DIFFICULTY_LABEL.get("en"));
        Map<String, String> diffNames = DIFFICULTY_NAMES.getOrDefault(language, DIFFICULTY_NAMES.get("en"));
        String diffName = difficulty == null ? "" : diffNames.getOrDefault(difficulty, difficulty);
        String date = LocalDate.now().toString();
        parts.append("<text x=\"").append(textX).append("\" y=\"").append(logoY + 20)
                .append("\" font-size=\"18\" font-family=\"sans-serif\" font-weight=\"bold\">CrossWordFalcon</text>")
                .append("<text x=\"").append(textX).append("\" y=\"").append(logoY + 38)
                .append("\" font-size=\"12\" font-family=\"sans-serif\" fill=\"#4b5563\">v").append(escape(version()))
                .append(" — ").append(escape(date)).append(" — ").append(escape(languageName)).append(" — ")
                .append(escape(diffLabel)).append("\u00a0: ").append(escape(diffName)).append("</text>");
        String modeLabel = MODE_LABEL.getOrDefault(language, MODE_LABEL.get("en"));
        Map<String, String> modeNames = MODE_NAMES.getOrDefault(language, MODE_NAMES.get("en"));
        String[] dl = DURATION_LABELS.getOrDefault(language, DURATION_LABELS.get("en"));
        List<String> info = new ArrayList<>();
        if (mode != null) info.add(modeLabel + " " + modeNames.getOrDefault(mode, mode));
        if (result.containsKey("generation_duration_seconds"))
            info.add(dl[0] + " " + formatDuration(Json.dbl(result, "generation_duration_seconds", 0)));
        if (result.containsKey("optimization_duration_seconds"))
            info.add(dl[1] + " " + formatDuration(Json.dbl(result, "optimization_duration_seconds", 0)));
        if (result.containsKey("clues_duration_seconds"))
            info.add(dl[2] + " " + formatDuration(Json.dbl(result, "clues_duration_seconds", 0)));
        if (result.containsKey("black_ratio")) {
            String tpl = BLACK_RATIO_LABELS.getOrDefault(language, BLACK_RATIO_LABELS.get("en"));
            info.add(tpl.replace("{p}", String.valueOf(Py.round(100 * Json.dbl(result, "black_ratio", 0)))));
        }
        if (!info.isEmpty()) {
            parts.append("<text x=\"").append(textX).append("\" y=\"").append(logoY + 56)
                    .append("\" font-size=\"12\" font-family=\"sans-serif\" fill=\"#4b5563\">")
                    .append(escape(String.join(" — ", info))).append("</text>");
        }
        y += HEADER_HEIGHT;

        parts.append(headingSvg(MARGIN, y, headings[0]));
        Object[] al = clueLinesSvg(MARGIN, sidebarWidth, y + 22, across);
        parts.append(al[0]);
        int sidebarHeight = 22 + (int) al[1];
        int gridX0 = MARGIN + sidebarWidth + GRID_SIDEBAR_GAP;
        List<Object> pattern = Json.listOrEmpty(result.get("pattern"));
        Object[] eg = gridSvg(pattern, null, words, y, gridX0);
        parts.append(eg[0]);
        y += Math.max(sidebarHeight, (int) eg[1]) + 24;

        parts.append(headingSvg(MARGIN, y, headings[1]));
        y += 22;
        int half = (down.size() + 1) / 2;
        double downColWidth = (canvasWidth - 2 * MARGIN - DOWN_COLUMN_GAP) / 2.0;
        Object[] left = clueLinesSvg(MARGIN, downColWidth, y, down.subList(0, half));
        double rightX = MARGIN + downColWidth + DOWN_COLUMN_GAP;
        Object[] right = clueLinesSvg(rightX, downColWidth, y, down.subList(half, down.size()));
        parts.append(left[0]).append(right[0]);
        y += Math.max((int) left[1], (int) right[1]) + 10;
        y += 8;
        parts.append("<line x1=\"").append(MARGIN).append("\" y1=\"").append(y).append("\" x2=\"")
                .append(canvasWidth - MARGIN).append("\" y2=\"").append(y).append("\" stroke=\"#9ca3af\"/>");
        y += 24;
        parts.append(headingSvg(MARGIN, y, headings[2]));
        y += 22;
        Object[] sg = gridSvg(pattern, Json.listOrEmpty(result.get("solution")), words, y, MARGIN);
        parts.append(sg[0]);
        y += (int) sg[1] + MARGIN;
        return wrapDocument(String.valueOf(canvasWidth), canvasWidth, y, parts.toString());
    }

    private static String wrapDocument(String canvasWidthStr, double canvasWidth, int y, String body) {
        double wm = canvasWidth * 0.9;
        double wx = (canvasWidth - wm) / 2;
        double wy = (y - wm) / 2;
        String watermark = "<image x=\"" + f1(wx) + "\" y=\"" + f1(wy) + "\" width=\"" + f1(wm) + "\" height=\""
                + f1(wm) + "\" href=\"" + logoDataUri() + "\" opacity=\"0.1\"/>";
        return "<svg xmlns=\"http://www.w3.org/2000/svg\" width=\"" + canvasWidthStr + "\" height=\"" + y
                + "\" viewBox=\"0 0 " + canvasWidthStr + " " + y + "\"><rect x=\"0\" y=\"0\" width=\""
                + canvasWidthStr + "\" height=\"" + y + "\" fill=\"#ffffff\"/>" + watermark + body + "</svg>";
    }

    public static String renderPuzzleSvg(Map<String, Object> result, String language, String title, String difficulty) {
        List<Object> words = Json.listOrEmpty(result.get("words"));
        String[] headings = HEADINGS.getOrDefault(language, HEADINGS.get("en"));
        List<Line> across = groupClueLines(words, "across", "row", language);
        List<Line> down = groupClueLines(words, "down", "col", language);
        int gridWidthPx = CELL_SIZE + Json.integer(result, "width", 0) * CELL_SIZE;
        double natural = gridWidthPx / MAX_GRID_WIDTH_FRACTION;
        boolean isFloat = natural > MIN_CANVAS_WIDTH;
        double canvasWidth = isFloat ? natural : MIN_CANVAS_WIDTH;
        String cw = isFloat ? num(canvasWidth) : String.valueOf(MIN_CANVAS_WIDTH);
        double gridX0d = canvasWidth - MARGIN - gridWidthPx;
        Object gridX0 = isFloat ? (Object) gridX0d : (Object) (int) gridX0d;
        double sidebarWidth = gridX0d - GRID_SIDEBAR_GAP - MARGIN;
        StringBuilder parts = new StringBuilder();
        int y = MARGIN;
        int logoX = MARGIN, logoY = y;
        parts.append("<image x=\"").append(logoX).append("\" y=\"").append(logoY).append("\" width=\"")
                .append(HEADER_LOGO_SIZE).append("\" height=\"").append(HEADER_LOGO_SIZE).append("\" href=\"")
                .append(logoDataUri()).append("\"/>");
        int textX = logoX + HEADER_LOGO_SIZE + 12;
        String languageName = NATIVE_LANGUAGE_NAMES.getOrDefault(language, language);
        String diffLabel = DIFFICULTY_LABEL.getOrDefault(language, DIFFICULTY_LABEL.get("en"));
        Map<String, String> diffNames = DIFFICULTY_NAMES.getOrDefault(language, DIFFICULTY_NAMES.get("en"));
        String diffName = difficulty == null ? "" : diffNames.getOrDefault(difficulty, difficulty);
        parts.append("<text x=\"").append(textX).append("\" y=\"").append(logoY + 20)
                .append("\" font-size=\"18\" font-family=\"sans-serif\" font-weight=\"bold\">CrossWordFalcon</text>");
        if (title != null && !title.isEmpty()) {
            parts.append("<text x=\"").append(textX).append("\" y=\"").append(logoY + 40)
                    .append("\" font-size=\"15\" font-family=\"sans-serif\" font-weight=\"bold\" fill=\"#111827\">")
                    .append(escape(title)).append("</text>");
        }
        parts.append("<text x=\"").append(textX).append("\" y=\"").append(logoY + 58)
                .append("\" font-size=\"12\" font-family=\"sans-serif\" fill=\"#4b5563\">v").append(escape(version()))
                .append(" — ").append(escape(LocalDate.now().toString())).append(" — ").append(escape(languageName))
                .append(" — ").append(escape(diffLabel)).append(" : ").append(escape(diffName)).append("</text>");
        y += Math.max(HEADER_LOGO_SIZE, 58) + 12;
        parts.append(headingSvg(MARGIN, y, headings[0]));
        Object[] al = clueLinesSvg(MARGIN, sidebarWidth, y + 22, across);
        parts.append(al[0]);
        int sidebarHeight = 22 + (int) al[1];
        Object[] eg = gridSvg(Json.listOrEmpty(result.get("pattern")), null, words, y, gridX0);
        parts.append(eg[0]);
        y += Math.max(sidebarHeight, (int) eg[1]) + 24;
        parts.append(headingSvg(MARGIN, y, headings[1]));
        y += 22;
        int half = (down.size() + 1) / 2;
        double downColWidth = (canvasWidth - 2 * MARGIN - DOWN_COLUMN_GAP) / 2;
        Object[] left = clueLinesSvg(MARGIN, downColWidth, y, down.subList(0, half));
        double rightX = MARGIN + downColWidth + DOWN_COLUMN_GAP;
        Object[] right = clueLinesSvg(rightX, downColWidth, y, down.subList(half, down.size()));
        parts.append(left[0]).append(right[0]);
        y += Math.max((int) left[1], (int) right[1]) + MARGIN;
        Object gridId = result.get("id");
        if (Json.truthy(gridId)) {
            String playUrl = PLAY_ONLINE_BASE_URL + "?grid=" + gridId;
            String tpl = PLAY_ONLINE_LABELS.getOrDefault(language, PLAY_ONLINE_LABELS.get("en"));
            y += 6;
            parts.append("<line x1=\"").append(MARGIN).append("\" y1=\"").append(y).append("\" x2=\"")
                    .append(isFloat ? num(canvasWidth - MARGIN) : String.valueOf((int) canvasWidth - MARGIN))
                    .append("\" y2=\"").append(y).append("\" stroke=\"#d1d5db\"/>");
            y += 16;
            parts.append("<text x=\"").append(MARGIN).append("\" y=\"").append(y)
                    .append("\" font-size=\"11\" font-family=\"sans-serif\" fill=\"#4b5563\">")
                    .append(escape(tpl.replace("{url}", playUrl))).append("</text>");
            y += MARGIN;
        }
        return wrapDocument(cw, canvasWidth, y, parts.toString());
    }

    private static byte[] runRsvg(List<String> cmd, byte[] stdin) throws IOException {
        Process p;
        try {
            p = new ProcessBuilder(cmd).start();
        } catch (IOException e) {
            throw new IOException("`rsvg-convert` not found (install it with `brew install librsvg` or "
                    + "`apt-get install librsvg2-bin`)", e);
        }
        ByteArrayOutputStream err = new ByteArrayOutputStream();
        Thread errPump = new Thread(() -> {
            try (InputStream es = p.getErrorStream()) {
                es.transferTo(err);
            } catch (IOException ignored) { }
        });
        errPump.start();
        if (stdin != null) {
            try (OutputStream os = p.getOutputStream()) {
                os.write(stdin);
            }
        } else {
            p.getOutputStream().close();
        }
        byte[] out;
        try (InputStream is = p.getInputStream()) {
            out = is.readAllBytes();
        }
        try {
            int code = p.waitFor();
            errPump.join();
            if (code != 0) throw new IOException("rsvg-convert failed: " + err.toString(StandardCharsets.UTF_8));
        } catch (InterruptedException e) {
            Thread.currentThread().interrupt();
            throw new IOException("interrupted", e);
        }
        return out;
    }

    public static byte[] svgToPdfBytes(String svg) throws IOException {
        return runRsvg(List.of("rsvg-convert", "-f", "pdf"), svg.getBytes(StandardCharsets.UTF_8));
    }

    public static Path saveGridSvg(Map<String, Object> result, String language, String difficulty, String mode)
            throws IOException {
        Files.createDirectories(GRID_SVG_DIR);
        String ts = LocalDateTime.now().format(DateTimeFormatter.ofPattern("yyyyMMdd-HHmmss-SSSSSS"));
        Path path = GRID_SVG_DIR.resolve(ts + "_" + language + ".svg");
        Files.writeString(path, renderGridSvg(result, language, difficulty, mode), StandardCharsets.UTF_8);
        return path;
    }

    public static Path saveGridPng(Path svgPath) throws IOException {
        Files.createDirectories(GRID_PNG_DIR);
        String name = svgPath.getFileName().toString();
        String stem = name.endsWith(".svg") ? name.substring(0, name.length() - 4) : name;
        Path png = GRID_PNG_DIR.resolve(stem + ".png");
        runRsvg(List.of("rsvg-convert", "-z", Double.toString(PNG_DPI / 96.0), "-o", png.toString(), svgPath.toString()),
                null);
        return png;
    }
}
