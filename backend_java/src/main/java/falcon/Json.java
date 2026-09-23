package falcon;

import com.fasterxml.jackson.core.JsonGenerator;
import com.fasterxml.jackson.core.JsonParser;
import com.fasterxml.jackson.core.type.TypeReference;
import com.fasterxml.jackson.databind.DeserializationFeature;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.fasterxml.jackson.databind.SerializationFeature;

import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

/**
 * JSON helpers. Every dynamic JSON value is represented the way Python's
 * json module represents it: {@code Map<String,Object>} (insertion-ordered
 * {@link LinkedHashMap}) for an object, {@code List<Object>} for an array,
 * {@code String}/{@code Number}/{@code Boolean}/{@code null} for scalars.
 */
public final class Json {
    private Json() {}

    public static final ObjectMapper MAPPER = new ObjectMapper()
            .configure(DeserializationFeature.USE_BIG_DECIMAL_FOR_FLOATS, false)
            .configure(DeserializationFeature.FAIL_ON_UNKNOWN_PROPERTIES, false)
            .configure(SerializationFeature.FAIL_ON_EMPTY_BEANS, false)
            .configure(JsonParser.Feature.ALLOW_NON_NUMERIC_NUMBERS, true);

    /** Same as MAPPER but escapes every non-ASCII character, like Python's
     * default {@code json.dumps(ensure_ascii=True)}. */
    public static final ObjectMapper ASCII_MAPPER = MAPPER.copy()
            .configure(JsonGenerator.Feature.ESCAPE_NON_ASCII, true);

    private static final ObjectMapper PRETTY = MAPPER.copy()
            .enable(SerializationFeature.INDENT_OUTPUT);

    public static Object parse(String text) {
        try {
            return MAPPER.readValue(text, Object.class);
        } catch (IOException e) {
            throw new IllegalArgumentException("invalid JSON: " + e.getMessage(), e);
        }
    }

    public static Map<String, Object> parseObject(String text) {
        Object o = parse(text);
        if (o instanceof Map<?, ?>) return asMap(o);
        throw new IllegalArgumentException("JSON object expected");
    }

    public static Object readFile(Path path) throws IOException {
        return parse(Files.readString(path, StandardCharsets.UTF_8));
    }

    public static String dumps(Object value) {
        try {
            return MAPPER.writeValueAsString(value);
        } catch (IOException e) {
            throw new IllegalStateException(e);
        }
    }

    public static String dumpsAscii(Object value) {
        try {
            return ASCII_MAPPER.writeValueAsString(value);
        } catch (IOException e) {
            throw new IllegalStateException(e);
        }
    }

    public static String dumpsPretty(Object value) {
        try {
            return PRETTY.writeValueAsString(value);
        } catch (IOException e) {
            throw new IllegalStateException(e);
        }
    }

    /** Converts an arbitrary Java object graph into plain Map/List/scalar
     * form (records, beans, arrays are flattened through Jackson). */
    public static Object plain(Object value) {
        return MAPPER.convertValue(value, new TypeReference<Object>() {});
    }

    @SuppressWarnings("unchecked")
    public static Map<String, Object> asMap(Object o) {
        return (Map<String, Object>) o;
    }

    @SuppressWarnings("unchecked")
    public static List<Object> asList(Object o) {
        return (List<Object>) o;
    }

    public static Map<String, Object> obj(Object... kv) {
        Map<String, Object> m = new LinkedHashMap<>();
        for (int i = 0; i + 1 < kv.length; i += 2) m.put((String) kv[i], kv[i + 1]);
        return m;
    }

    public static List<Object> list(Object... items) {
        List<Object> l = new ArrayList<>(items.length);
        for (Object o : items) l.add(o);
        return l;
    }

    // ---- lenient accessors (Python's dict.get(...) with a default) ----

    public static Object get(Object map, String key) {
        if (map instanceof Map<?, ?> m) return m.get(key);
        return null;
    }

    public static String str(Object map, String key, String def) {
        Object v = get(map, key);
        return v == null ? def : String.valueOf(v);
    }

    public static int integer(Object map, String key, int def) {
        Object v = get(map, key);
        if (v instanceof Number n) return n.intValue();
        if (v instanceof String s) {
            try { return Integer.parseInt(s.trim()); } catch (NumberFormatException ignored) { }
        }
        return def;
    }

    public static double dbl(Object map, String key, double def) {
        Object v = get(map, key);
        if (v instanceof Number n) return n.doubleValue();
        if (v instanceof String s) {
            try { return Double.parseDouble(s.trim()); } catch (NumberFormatException ignored) { }
        }
        return def;
    }

    public static boolean bool(Object map, String key, boolean def) {
        Object v = get(map, key);
        if (v instanceof Boolean b) return b;
        if (v == null) return def;
        return truthy(v);
    }

    /** Python truthiness of a JSON value. */
    public static boolean truthy(Object v) {
        if (v == null) return false;
        if (v instanceof Boolean b) return b;
        if (v instanceof Number n) return n.doubleValue() != 0;
        if (v instanceof String s) return !s.isEmpty();
        if (v instanceof Map<?, ?> m) return !m.isEmpty();
        if (v instanceof List<?> l) return !l.isEmpty();
        return true;
    }

    public static Map<String, Object> mapOrEmpty(Object v) {
        if (v instanceof Map<?, ?>) return asMap(v);
        return new LinkedHashMap<>();
    }

    public static List<Object> listOrEmpty(Object v) {
        if (v instanceof List<?>) return asList(v);
        return new ArrayList<>();
    }

    public static void writeFile(Path path, Object value, boolean pretty) throws IOException {
        Files.createDirectories(path.getParent());
        Files.writeString(path, pretty ? dumpsPretty(value) : dumps(value), StandardCharsets.UTF_8);
    }
}
