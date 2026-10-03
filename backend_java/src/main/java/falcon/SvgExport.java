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
    // Colour of a black cell's centered square (the web UI's --black-cell).
    static final String BLACK_CELL_FILL = "#2563eb";
    static final int LINE_HEIGHT = 16;
    static final int MARGIN = 16;
    static final int MIN_CANVAS_WIDTH = 720;
    static final int HEADER_LOGO_SIZE = 48;
    static final int HEADER_HEIGHT = HEADER_LOGO_SIZE + 16 + 18;
    static final int GRID_SIDEBAR_GAP = 24;
    static final int DOWN_COLUMN_GAP = 24;
    // Printable puzzle sheet (renderPuzzlePages): A4 landscape pages — one
    // (renderPuzzleSvg) while both sides are at most PDF_ONE_PAGE_MAX_SIDE,
    // the grid then the clues on two otherwise (renderTwoPagePuzzle).
    static final double PDF_PAGE_WIDTH = 1122.52;
    static final double PDF_PAGE_HEIGHT = 793.7;
    static final int PDF_MARGIN = 34;
    static final int PDF_LOGO_SIZE = 44;
    static final int PDF_HEADER_GAP = 14;
    static final int PDF_FOOTER_HEIGHT = 24;
    static final int PDF_GRID_CLUES_GAP = 20;
    static final int PDF_CLUE_COLUMN_GAP = 16;
    static final int PDF_MAX_CELL_SIZE = 40;
    static final double PDF_MAX_GRID_WIDTH_FRACTION = 0.6;
    static final int PDF_MAX_CLUE_COLUMNS = 3;
    static final int PDF_MIN_CLUE_COLUMN_WIDTH = 120;
    static final double PDF_LINE_HEIGHT_FACTOR = 1.3;
    static final int PDF_ONE_PAGE_MAX_SIDE = 20;
    static final int PDF_MAX_CLUE_PAGES = 8;
    static final double[] PDF_FONT_SIZES = new double[17];
    static {
        for (int i = 0; i < 17; i++) PDF_FONT_SIZES[i] = (24 - i) / 2.0;
    }
    static final int PDF_CELL_SIZE_STEPS = 25;
    static final double PDF_CELL_SIZE_STEP = 0.025;
    static final double PDF_TARGET_CELL_SIZE = 18.9;
    static final double PDF_TARGET_FONT_SIZE = 9.33;
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
            "fr", Map.of("flash", "Flash", "turbo", "Turbo", "fast", "Rapide", "medium", "Moyen", "ultra", "Ultra", "megatron", "Megatron", "gridzilla", "GridZilla"),
            "en", Map.of("flash", "Flash", "turbo", "Turbo", "fast", "Fast", "medium", "Medium", "ultra", "Ultra", "megatron", "Megatron", "gridzilla", "GridZilla"),
            "de", Map.of("flash", "Flash", "turbo", "Turbo", "fast", "Schnell", "medium", "Mittel", "ultra", "Ultra", "megatron", "Megatron", "gridzilla", "GridZilla"),
            "es", Map.of("flash", "Flash", "turbo", "Turbo", "fast", "Rápido", "medium", "Medio", "ultra", "Ultra", "megatron", "Megatron", "gridzilla", "GridZilla"),
            "it", Map.of("flash", "Flash", "turbo", "Turbo", "fast", "Veloce", "medium", "Medio", "ultra", "Ultra", "megatron", "Megatron", "gridzilla", "GridZilla"),
            "pt", Map.of("flash", "Flash", "turbo", "Turbo", "fast", "Rápido", "medium", "Médio", "ultra", "Ultra", "megatron", "Megatron", "gridzilla", "GridZilla"));
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

    static double textWidth(String text, double fontSize, boolean bold) {
        return text.length() * fontSize * (bold ? BOLD_CHAR_WIDTH_FACTOR : CHAR_WIDTH_FACTOR);
    }

    static List<String> wrapLine(String text, double fontSize, double maxWidth) {
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
                    // A black cell is a white cell carrying a centered square
                    // half its size, filled with BLACK_CELL_FILL — the web UI's
                    // own `.cell.black` look.
                    parts.append("<rect x=\"").append(xs).append("\" y=\"").append(y).append("\" width=\"")
                            .append(CELL_SIZE).append("\" height=\"").append(CELL_SIZE)
                            .append("\" fill=\"#ffffff\" stroke=\"#1f2937\" stroke-width=\"1\"/>");
                    parts.append("<rect x=\"").append(num(x + CELL_SIZE / 4.0)).append("\" y=\"")
                            .append(num(y + CELL_SIZE / 4.0)).append("\" width=\"").append(num(CELL_SIZE / 2.0))
                            .append("\" height=\"").append(num(CELL_SIZE / 2.0)).append("\" fill=\"")
                            .append(BLACK_CELL_FILL).append("\"/>");
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

    /** Two-decimal formatting for renderPuzzleSvg (Python's {@code _n}). */
    static String n2(double v) {
        return Py.fmt(v, 2);
    }

    /** Empty grid for renderPuzzleSvg (Python's {@code _puzzle_grid_svg}). */
    static String puzzleGridSvg(List<Object> pattern, List<Object> words, double x0, double y0, double cell) {
        int rows = pattern.size(), cols = rowLength(pattern.get(0));
        Map<String, Object> numberByCell = new HashMap<>();
        for (Object w : words) numberByCell.put(Json.integer(w, "row", 0) + "," + Json.integer(w, "col", 0), Json.get(w, "number"));
        StringBuilder parts = new StringBuilder();
        double gridX0 = x0 + cell, gridY0 = y0 + cell, headerFont = cell * 0.4;
        for (int c = 0; c < cols; c++) {
            parts.append("<text x=\"").append(n2(gridX0 + c * cell + cell / 2)).append("\" y=\"").append(n2(y0 + cell * 0.65))
                    .append("\" font-size=\"").append(n2(headerFont)).append("\" font-family=\"sans-serif\" text-anchor=\"middle\" ")
                    .append("fill=\"#4b5563\">").append(c + 1).append("</text>");
        }
        for (int r = 0; r < rows; r++) {
            parts.append("<text x=\"").append(n2(x0 + cell / 2)).append("\" y=\"").append(n2(gridY0 + r * cell + cell * 0.65))
                    .append("\" font-size=\"").append(n2(headerFont)).append("\" font-family=\"sans-serif\" text-anchor=\"middle\" ")
                    .append("fill=\"#4b5563\">").append(r + 1).append("</text>");
        }
        for (int r = 0; r < rows; r++) {
            for (int c = 0; c < cols; c++) {
                double x = gridX0 + c * cell, y = gridY0 + r * cell;
                parts.append("<rect x=\"").append(n2(x)).append("\" y=\"").append(n2(y)).append("\" width=\"").append(n2(cell))
                        .append("\" height=\"").append(n2(cell)).append("\" fill=\"#ffffff\" stroke=\"#1f2937\" stroke-width=\"1\"/>");
                if ("#".equals(cell(pattern, r, c))) {
                    parts.append("<rect x=\"").append(n2(x + cell / 4)).append("\" y=\"").append(n2(y + cell / 4))
                            .append("\" width=\"").append(n2(cell / 2)).append("\" height=\"").append(n2(cell / 2))
                            .append("\" fill=\"").append(BLACK_CELL_FILL).append("\"/>");
                    continue;
                }
                Object number = numberByCell.get(r + "," + c);
                if (Json.truthy(number)) {
                    parts.append("<text x=\"").append(n2(x + cell * 0.08)).append("\" y=\"").append(n2(y + cell * 0.33))
                            .append("\" font-size=\"").append(n2(cell * 0.27)).append("\" font-family=\"sans-serif\">")
                            .append(number).append("</text>");
                }
            }
        }
        return parts.toString();
    }

    record ClueColumn(double x, double top, double width, double height) {}

    /** One placed clue item: a heading ({@code text} set) or a wrapped clue line. */
    record ClueItem(double x, double top, String heading, int pos, List<String> wrapped, double indent) {}

    private record ClueEntry(String heading, Line line, double lead) {}

    /** Python's {@code _layout_puzzle_clues}: null when the clues do not fit. */
    static List<ClueItem> layoutPuzzleClues(List<Map.Entry<String, List<Line>>> sections, double fontSize,
                                            List<ClueColumn> columns, boolean overflow) {
        double lineHeight = fontSize * PDF_LINE_HEIGHT_FACTOR;
        double headingHeight = (fontSize + 2) * 1.6;
        double sectionGap = lineHeight * 0.5;
        List<List<ClueEntry>> blocks = new ArrayList<>();
        for (int index = 0; index < sections.size(); index++) {
            List<ClueEntry> pending = new ArrayList<>();
            pending.add(new ClueEntry(sections.get(index).getKey(), null, index > 0 ? sectionGap : 0.0));
            for (Line l : sections.get(index).getValue()) {
                pending.add(new ClueEntry(null, l, 0.0));
                blocks.add(pending);
                pending = new ArrayList<>();
            }
            if (!pending.isEmpty()) blocks.add(pending);
        }
        List<ClueItem> items = new ArrayList<>();
        int column = 0;
        double y = 0.0;
        for (List<ClueEntry> block : blocks) {
            ClueColumn col;
            List<Object[]> measured;
            double lead;
            while (true) {
                col = columns.get(column);
                measured = new ArrayList<>();
                double total = 0.0;
                for (ClueEntry e : block) {
                    if (e.heading() != null) {
                        measured.add(new Object[]{e, null, 0.0, headingHeight});
                        total += headingHeight;
                        continue;
                    }
                    double indent = textWidth((e.line().pos() + 1) + " ", fontSize, true);
                    List<String> wrapped = wrapLine(e.line().text(), fontSize, col.width() - indent);
                    double h = lineHeight * wrapped.size();
                    measured.add(new Object[]{e, wrapped, indent, h});
                    total += h;
                }
                lead = y > 0 ? block.get(0).lead() : 0.0;
                total = lead + total;
                if (y + total <= col.height() || (overflow && column == columns.size() - 1)) break;
                if (y == 0 && !overflow && column == columns.size() - 1) return null;
                column++;
                y = 0.0;
                if (column >= columns.size()) return null;
            }
            y += lead;
            for (Object[] m : measured) {
                ClueEntry e = (ClueEntry) m[0];
                @SuppressWarnings("unchecked")
                List<String> wrapped = (List<String>) m[1];
                items.add(e.heading() != null
                        ? new ClueItem(col.x(), col.top() + y, e.heading(), 0, null, 0.0)
                        : new ClueItem(col.x(), col.top() + y, null, e.line().pos(), wrapped, (double) m[2]));
                y += (double) m[3];
            }
        }
        return items;
    }

    private record PuzzleLayout(double fontSize, double cell, List<ClueItem> items) {}

    public static String renderPuzzleSvg(Map<String, Object> result, String language, String title, String difficulty) {
        List<Object> words = Json.listOrEmpty(result.get("words"));
        List<Object> pattern = Json.listOrEmpty(result.get("pattern"));
        int rows = pattern.size(), cols = rowLength(pattern.get(0));
        List<Map.Entry<String, List<Line>>> sections = puzzleSections(words, language);
        Object gridId = result.get("id");
        boolean hasId = Json.truthy(gridId);

        double pageW = PDF_PAGE_WIDTH, pageH = PDF_PAGE_HEIGHT;
        int margin = PDF_MARGIN;
        double contentW = pageW - 2 * margin;
        int bodyTop = margin + PDF_LOGO_SIZE + PDF_HEADER_GAP;
        double bodyBottom = pageH - margin - (hasId ? PDF_FOOTER_HEIGHT : 0);
        double bodyH = bodyBottom - bodyTop;
        double maxCell = Math.min(Math.min(bodyH / (rows + 1), contentW * PDF_MAX_GRID_WIDTH_FRACTION / (cols + 1)),
                PDF_MAX_CELL_SIZE);

        java.util.function.BiFunction<Double, Integer, List<ClueColumn>> clueColumns = (cell, columnCount) -> {
            double gridW = cell * (cols + 1), gridH = cell * (rows + 1);
            double gridX0 = pageW - margin - gridW;
            double leftW = gridX0 - PDF_GRID_CLUES_GAP - margin;
            double columnW = (leftW - (columnCount - 1) * PDF_CLUE_COLUMN_GAP) / columnCount;
            if (columnW < PDF_MIN_CLUE_COLUMN_WIDTH) return null;
            List<ClueColumn> columns = new ArrayList<>();
            for (int i = 0; i < columnCount; i++) {
                columns.add(new ClueColumn(margin + i * (columnW + PDF_CLUE_COLUMN_GAP), bodyTop, columnW, bodyH));
            }
            double underTop = bodyTop + gridH + PDF_GRID_CLUES_GAP;
            double underH = bodyBottom - underTop;
            if (underH > 0 && gridW >= PDF_MIN_CLUE_COLUMN_WIDTH) {
                int underCount = Math.max(1, (int) Math.floor((gridW + PDF_CLUE_COLUMN_GAP) / (columnW + PDF_CLUE_COLUMN_GAP)));
                double underW = (gridW - (underCount - 1) * PDF_CLUE_COLUMN_GAP) / underCount;
                for (int i = 0; i < underCount; i++) {
                    columns.add(new ClueColumn(gridX0 + i * (underW + PDF_CLUE_COLUMN_GAP), underTop, underW, underH));
                }
            }
            return columns;
        };
        java.util.function.IntToDoubleFunction cellAt = step -> maxCell * (1 - step * PDF_CELL_SIZE_STEP);

        PuzzleLayout layout = null;
        double[] bestBalance = null;
        for (double fontSize : PDF_FONT_SIZES) {
            for (int columnCount = 1; columnCount <= PDF_MAX_CLUE_COLUMNS; columnCount++) {
                int low = 0, high = PDF_CELL_SIZE_STEPS - 1;
                PuzzleLayout found = attemptPuzzle(sections, fontSize, cellAt.applyAsDouble(high), columnCount, clueColumns);
                if (found == null) continue;
                while (low < high) {
                    int middle = (low + high) / 2;
                    PuzzleLayout candidate = attemptPuzzle(sections, fontSize, cellAt.applyAsDouble(middle), columnCount, clueColumns);
                    if (candidate == null) {
                        low = middle + 1;
                    } else {
                        found = candidate;
                        high = middle;
                    }
                }
                double cellRatio = found.cell() / PDF_TARGET_CELL_SIZE, fontRatio = found.fontSize() / PDF_TARGET_FONT_SIZE;
                double[] balance = {Math.min(Math.min(cellRatio, fontRatio), 1.0), cellRatio + fontRatio};
                if (layout == null || balance[0] > bestBalance[0]
                        || (balance[0] == bestBalance[0] && balance[1] > bestBalance[1])) {
                    layout = found;
                    bestBalance = balance;
                }
            }
        }
        if (layout == null) {
            // Nothing fits: the smallest configuration, its last column running past the page.
            double fontSize = PDF_FONT_SIZES[PDF_FONT_SIZES.length - 1];
            double cell = cellAt.applyAsDouble(PDF_CELL_SIZE_STEPS - 1);
            List<ClueColumn> columns = clueColumns.apply(cell, PDF_MAX_CLUE_COLUMNS);
            if (columns == null) columns = clueColumns.apply(cell, 1);
            layout = new PuzzleLayout(fontSize, cell, layoutPuzzleClues(sections, fontSize, columns, true));
        }
        double fontSize = layout.fontSize(), cell = layout.cell();

        return puzzlePageSvg(puzzleHeaderSvg(language, title, difficulty)
                + puzzleGridSvg(pattern, words, pageW - margin - cell * (cols + 1), bodyTop, cell)
                + puzzleClueItemsSvg(layout.items(), fontSize, 0.0)
                + puzzleFooterSvg(language, gridId));
    }

    /** Python's {@code _puzzle_sections}: across then down clue sections. */
    static List<Map.Entry<String, List<Line>>> puzzleSections(List<Object> words, String language) {
        String[] headings = HEADINGS.getOrDefault(language, HEADINGS.get("en"));
        return List.of(
                Map.entry(headings[0], groupClueLines(words, "across", "row", language)),
                Map.entry(headings[1], groupClueLines(words, "down", "col", language)));
    }

    /** Python's {@code _puzzle_header_svg}: logo, software name, title, identity line. */
    static String puzzleHeaderSvg(String language, String title, String difficulty) {
        int margin = PDF_MARGIN;
        StringBuilder parts = new StringBuilder();
        parts.append("<image x=\"").append(n2(margin)).append("\" y=\"").append(n2(margin)).append("\" width=\"")
                .append(n2(PDF_LOGO_SIZE)).append("\" height=\"").append(n2(PDF_LOGO_SIZE)).append("\" href=\"")
                .append(logoDataUri()).append("\"/>");
        int textX = margin + PDF_LOGO_SIZE + 12;
        String languageName = NATIVE_LANGUAGE_NAMES.getOrDefault(language, language);
        String diffLabel = DIFFICULTY_LABEL.getOrDefault(language, DIFFICULTY_LABEL.get("en"));
        Map<String, String> diffNames = DIFFICULTY_NAMES.getOrDefault(language, DIFFICULTY_NAMES.get("en"));
        String diffName = difficulty == null ? "" : diffNames.getOrDefault(difficulty, difficulty);
        parts.append("<text x=\"").append(n2(textX)).append("\" y=\"").append(n2(margin + 14))
                .append("\" font-size=\"16\" font-family=\"sans-serif\" font-weight=\"bold\">CrossWordFalcon</text>");
        if (title != null && !title.isEmpty()) {
            parts.append("<text x=\"").append(n2(textX)).append("\" y=\"").append(n2(margin + 30))
                    .append("\" font-size=\"14\" font-family=\"sans-serif\" font-weight=\"bold\" fill=\"#111827\">")
                    .append(escape(title)).append("</text>");
        }
        parts.append("<text x=\"").append(n2(textX)).append("\" y=\"").append(n2(margin + 44))
                .append("\" font-size=\"11\" font-family=\"sans-serif\" fill=\"#4b5563\">v").append(escape(version()))
                .append(" — ").append(escape(LocalDate.now().toString())).append(" — ").append(escape(languageName))
                .append(" — ").append(escape(diffLabel)).append(" : ").append(escape(diffName)).append("</text>");
        return parts.toString();
    }

    /** Python's {@code _puzzle_clue_items_svg}: laid-out clues, moved up by {@code yShift}. */
    static String puzzleClueItemsSvg(List<ClueItem> items, double fontSize, double yShift) {
        StringBuilder parts = new StringBuilder();
        double headingSize = fontSize + 2;
        double lineHeight = fontSize * PDF_LINE_HEIGHT_FACTOR;
        for (ClueItem item : items) {
            double top = item.top() - yShift;
            if (item.heading() != null) {
                parts.append("<text x=\"").append(n2(item.x())).append("\" y=\"").append(n2(top + headingSize * 1.15))
                        .append("\" font-size=\"").append(n2(headingSize)).append("\" font-family=\"sans-serif\" font-weight=\"bold\">")
                        .append(escape(item.heading())).append("</text>");
                continue;
            }
            double baseline = top + fontSize;
            parts.append("<text x=\"").append(n2(item.x())).append("\" y=\"").append(n2(baseline)).append("\" font-size=\"")
                    .append(n2(fontSize)).append("\" font-family=\"sans-serif\"><tspan font-weight=\"bold\">")
                    .append(item.pos() + 1).append("</tspan> ").append(escape(item.wrapped().get(0))).append("</text>");
            for (int index = 1; index < item.wrapped().size(); index++) {
                parts.append("<text x=\"").append(n2(item.x() + item.indent())).append("\" y=\"")
                        .append(n2(baseline + index * lineHeight)).append("\" font-size=\"").append(n2(fontSize))
                        .append("\" font-family=\"sans-serif\">").append(escape(item.wrapped().get(index))).append("</text>");
            }
        }
        return parts.toString();
    }

    /** Python's {@code _puzzle_footer_svg}: the "play online" link, only for a record with an id. */
    static String puzzleFooterSvg(String language, Object gridId) {
        if (!Json.truthy(gridId)) return "";
        double pageW = PDF_PAGE_WIDTH, pageH = PDF_PAGE_HEIGHT;
        int margin = PDF_MARGIN;
        String playUrl = PLAY_ONLINE_BASE_URL + "?grid=" + gridId;
        String tpl = PLAY_ONLINE_LABELS.getOrDefault(language, PLAY_ONLINE_LABELS.get("en"));
        double lineY = pageH - margin - PDF_FOOTER_HEIGHT + 8;
        return "<line x1=\"" + n2(margin) + "\" y1=\"" + n2(lineY) + "\" x2=\"" + n2(pageW - margin) + "\" y2=\""
                + n2(lineY) + "\" stroke=\"#d1d5db\"/><text x=\"" + n2(margin) + "\" y=\"" + n2(pageH - margin)
                + "\" font-size=\"10\" font-family=\"sans-serif\" fill=\"#4b5563\">"
                + escape(tpl.replace("{url}", playUrl)) + "</text>";
    }

    /** Python's {@code _puzzle_page_svg}: one A4 landscape page around {@code body}. */
    static String puzzlePageSvg(String body) {
        double pageW = PDF_PAGE_WIDTH, pageH = PDF_PAGE_HEIGHT;
        double wm = pageH * 0.8;
        return "<svg xmlns=\"http://www.w3.org/2000/svg\" width=\"297mm\" height=\"210mm\" viewBox=\"0 0 " + n2(pageW) + " "
                + n2(pageH) + "\"><rect x=\"0\" y=\"0\" width=\"" + n2(pageW) + "\" height=\"" + n2(pageH)
                + "\" fill=\"#ffffff\"/><image x=\"" + n2((pageW - wm) / 2) + "\" y=\"" + n2((pageH - wm) / 2)
                + "\" width=\"" + n2(wm) + "\" height=\"" + n2(wm) + "\" href=\"" + logoDataUri() + "\" opacity=\"0.1\"/>"
                + body + "</svg>";
    }

    /**
     * Python's {@code render_two_page_puzzle}: page 1 the header, the grid
     * centered and the footer; page 2 the clues in one full-width column at
     * the largest font that fits, running on over further pages (at most
     * PDF_MAX_CLUE_PAGES) when even the smallest one does not.
     */
    public static List<String> renderTwoPagePuzzle(Map<String, Object> result, String language, String title,
                                                   String difficulty) {
        List<Object> words = Json.listOrEmpty(result.get("words"));
        List<Object> pattern = Json.listOrEmpty(result.get("pattern"));
        int rows = pattern.size(), cols = rowLength(pattern.get(0));
        List<Map.Entry<String, List<Line>>> sections = puzzleSections(words, language);
        Object gridId = result.get("id");
        boolean hasId = Json.truthy(gridId);

        double pageW = PDF_PAGE_WIDTH, pageH = PDF_PAGE_HEIGHT;
        int margin = PDF_MARGIN;
        double contentW = pageW - 2 * margin;
        int bodyTop = margin + PDF_LOGO_SIZE + PDF_HEADER_GAP;
        double bodyBottom = pageH - margin - (hasId ? PDF_FOOTER_HEIGHT : 0);
        double cell = Math.min(Math.min((bodyBottom - bodyTop) / (rows + 1), contentW / (cols + 1)), PDF_MAX_CELL_SIZE);
        double gridX0 = margin + (contentW - cell * (cols + 1)) / 2;
        String firstPage = puzzleHeaderSvg(language, title, difficulty)
                + puzzleGridSvg(pattern, words, gridX0, bodyTop, cell)
                + puzzleFooterSvg(language, gridId);

        double clueH = pageH - 2 * margin;
        double fontSize = 0;
        List<ClueItem> items = null;
        for (double size : PDF_FONT_SIZES) {
            items = layoutPuzzleClues(sections, size, List.of(new ClueColumn(margin, margin, contentW, clueH)), false);
            if (items != null) {
                fontSize = size;
                break;
            }
        }
        if (items == null) {
            fontSize = PDF_FONT_SIZES[PDF_FONT_SIZES.length - 1];
            List<ClueColumn> columns = new ArrayList<>();
            for (int k = 0; k < PDF_MAX_CLUE_PAGES; k++) columns.add(new ClueColumn(margin, k * pageH + margin, contentW, clueH));
            items = layoutPuzzleClues(sections, fontSize, columns, true);
        }
        TreeMap<Integer, List<ClueItem>> byPage = new TreeMap<>();
        for (ClueItem item : items) {
            byPage.computeIfAbsent((int) Math.floor(item.top() / pageH), k -> new ArrayList<>()).add(item);
        }
        List<String> pages = new ArrayList<>();
        pages.add(puzzlePageSvg(firstPage));
        for (Map.Entry<Integer, List<ClueItem>> e : byPage.entrySet()) {
            pages.add(puzzlePageSvg(puzzleClueItemsSvg(e.getValue(), fontSize, e.getKey() * pageH)));
        }
        return pages;
    }

    /** Python's {@code render_puzzle_pages}: one page, or two for a side above PDF_ONE_PAGE_MAX_SIDE. */
    public static List<String> renderPuzzlePages(Map<String, Object> result, String language, String title,
                                                 String difficulty) {
        List<Object> pattern = Json.listOrEmpty(result.get("pattern"));
        if (Math.max(pattern.size(), rowLength(pattern.get(0))) > PDF_ONE_PAGE_MAX_SIDE) {
            return renderTwoPagePuzzle(result, language, title, difficulty);
        }
        return List.of(renderPuzzleSvg(result, language, title, difficulty));
    }

    private static PuzzleLayout attemptPuzzle(List<Map.Entry<String, List<Line>>> sections, double fontSize, double cell,
                                              int columnCount,
                                              java.util.function.BiFunction<Double, Integer, List<ClueColumn>> clueColumns) {
        List<ClueColumn> columns = clueColumns.apply(cell, columnCount);
        if (columns == null) return null;
        List<ClueItem> items = layoutPuzzleClues(sections, fontSize, columns, false);
        return items == null ? null : new PuzzleLayout(fontSize, cell, items);
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

    /** One PDF page per SVG: several pages go through a temporary directory, in order. */
    public static byte[] svgToPdfBytes(List<String> pages) throws IOException {
        if (pages.size() == 1) return svgToPdfBytes(pages.get(0));
        Path tmp = Files.createTempDirectory("crosswordfalcon-pdf");
        try {
            List<String> cmd = new ArrayList<>(List.of("rsvg-convert", "-f", "pdf"));
            for (int i = 0; i < pages.size(); i++) {
                Path page = tmp.resolve("page" + (i + 1) + ".svg");
                Files.writeString(page, pages.get(i), StandardCharsets.UTF_8);
                cmd.add(page.toString());
            }
            return runRsvg(cmd, null);
        } finally {
            try (var files = Files.list(tmp)) {
                for (Path f : (Iterable<Path>) files::iterator) Files.deleteIfExists(f);
            }
            Files.deleteIfExists(tmp);
        }
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
