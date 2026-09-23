package falcon;

import com.sun.net.httpserver.HttpExchange;
import com.sun.net.httpserver.HttpServer;

import java.io.IOException;
import java.io.InputStream;
import java.io.OutputStream;
import java.net.InetSocketAddress;
import java.net.URLDecoder;
import java.nio.charset.StandardCharsets;
import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.concurrent.Executors;
import java.util.regex.Matcher;
import java.util.regex.Pattern;

/** Minimal JSON web layer over the JDK HttpServer, reproducing FastAPI's
 * observable behavior (JSON bodies, {"detail": ...} errors, 404/405/422). */
public final class Web {

    /** An HTTP error (FastAPI's HTTPException). */
    public static final class HttpError extends RuntimeException {
        public final int status;
        public final Object detail;

        public HttpError(int status, Object detail) {
            super(String.valueOf(detail), null, false, false);
            this.status = status;
            this.detail = detail;
        }
    }

    public static final class Request {
        public final String method;
        public final String path;
        public final Map<String, String> query;
        public final Map<String, String> pathParams;
        private final byte[] body;
        private Map<String, Object> json;

        Request(String method, String path, Map<String, String> query, Map<String, String> pathParams, byte[] body) {
            this.method = method;
            this.path = path;
            this.query = query;
            this.pathParams = pathParams;
            this.body = body;
        }

        /** The JSON object body; 422 when absent or malformed. */
        public Map<String, Object> json() {
            if (json == null) {
                String text = new String(body, StandardCharsets.UTF_8);
                try {
                    Object o = Json.parse(text);
                    if (!(o instanceof Map<?, ?>)) throw new IllegalArgumentException("not an object");
                    json = Json.asMap(o);
                } catch (IllegalArgumentException e) {
                    throw validation("body", "json_invalid", "JSON decode error", text);
                }
            }
            return json;
        }

        public String q(String name, String def) {
            String v = query.get(name);
            return v == null ? def : v;
        }

        public String qRequired(String name) {
            String v = query.get(name);
            if (v == null) throw validation("query", name, "missing", "Field required", null);
            return v;
        }

        public int qInt(String name, int def) {
            String v = query.get(name);
            if (v == null) return def;
            try {
                return Integer.parseInt(v.trim());
            } catch (NumberFormatException e) {
                throw validation("query", name, "int_parsing", "Input should be a valid integer, unable to parse string as an integer", v);
            }
        }

        public double qDouble(String name, double def) {
            String v = query.get(name);
            if (v == null) return def;
            try {
                return Double.parseDouble(v.trim());
            } catch (NumberFormatException e) {
                throw validation("query", name, "float_parsing", "Input should be a valid number, unable to parse string as a number", v);
            }
        }
    }

    /** A handler's non-JSON reply (bytes + content type + headers). */
    public record Raw(int status, String contentType, byte[] body, Map<String, String> headers) {}

    /** A handler that writes its own streamed reply. */
    public interface Streamer {
        void stream(OutputStream out) throws IOException;
    }

    public record Stream(String contentType, Streamer streamer) {}

    public interface Handler {
        Object handle(Request req) throws Exception;
    }

    private record Route(String method, Pattern pattern, List<String> names, Handler handler, int status) {}

    private final List<Route> routes = new ArrayList<>();

    public void add(String method, String template, int status, Handler h) {
        List<String> names = new ArrayList<>();
        StringBuilder rx = new StringBuilder("^");
        Matcher m = Pattern.compile("\\{([a-z_]+)}").matcher(template);
        int last = 0;
        while (m.find()) {
            rx.append(Pattern.quote(template.substring(last, m.start())));
            rx.append("([^/]+)");
            names.add(m.group(1));
            last = m.end();
        }
        rx.append(Pattern.quote(template.substring(last))).append("$");
        routes.add(new Route(method, Pattern.compile(rx.toString()), names, h, status));
    }

    public void get(String t, Handler h) {
        add("GET", t, 200, h);
    }

    public void post(String t, Handler h) {
        add("POST", t, 200, h);
    }

    public void post(String t, int status, Handler h) {
        add("POST", t, status, h);
    }

    // ---------------------------------------------------------------- validation helpers (422)

    public static HttpError validation(String where, String type, String msg, Object input) {
        return new HttpError(422, List.of(Json.obj("type", type, "loc", List.of(where), "msg", msg, "input", input)));
    }

    public static HttpError validation(String where, String field, String type, String msg, Object input) {
        return new HttpError(422, List.of(Json.obj("type", type, "loc", List.of(where, field), "msg", msg, "input", input)));
    }

    static Map<String, String> parseQuery(String raw) {
        Map<String, String> q = new LinkedHashMap<>();
        if (raw == null || raw.isEmpty()) return q;
        for (String part : raw.split("&")) {
            if (part.isEmpty()) continue;
            int eq = part.indexOf('=');
            String k = eq < 0 ? part : part.substring(0, eq);
            String v = eq < 0 ? "" : part.substring(eq + 1);
            q.putIfAbsent(URLDecoder.decode(k, StandardCharsets.UTF_8), URLDecoder.decode(v, StandardCharsets.UTF_8));
        }
        return q;
    }

    // ---------------------------------------------------------------- server

    public HttpServer start(String host, int port) throws IOException {
        HttpServer server = HttpServer.create(new InetSocketAddress(host, port), 256);
        server.setExecutor(Executors.newVirtualThreadPerTaskExecutor());
        server.createContext("/", this::dispatch);
        server.start();
        return server;
    }

    private void dispatch(HttpExchange ex) throws IOException {
        try {
            String path = ex.getRequestURI().getRawPath();
            String decodedPath = URLDecoder.decode(path.replace("+", "%2B"), StandardCharsets.UTF_8);
            String method = ex.getRequestMethod().toUpperCase(java.util.Locale.ROOT);
            boolean pathMatched = false;
            for (Route r : routes) {
                Matcher m = r.pattern.matcher(decodedPath);
                if (!m.matches()) continue;
                pathMatched = true;
                boolean head = method.equals("HEAD") && r.method.equals("GET");
                if (!r.method.equals(method) && !head) continue;
                Map<String, String> params = new LinkedHashMap<>();
                for (int i = 0; i < r.names.size(); i++) params.put(r.names.get(i), m.group(i + 1));
                byte[] body;
                try (InputStream in = ex.getRequestBody()) {
                    body = in.readAllBytes();
                }
                Request req = new Request(method, decodedPath, parseQuery(ex.getRequestURI().getRawQuery()), params, body);
                Object result;
                try {
                    result = r.handler.handle(req);
                } catch (HttpError e) {
                    sendJson(ex, e.status, Json.obj("detail", e.detail));
                    return;
                } catch (Exception e) {
                    Log.exception("unhandled error on " + method + " " + decodedPath, e);
                    sendText(ex, 500, "Internal Server Error");
                    return;
                }
                if (result instanceof Raw raw) {
                    if (raw.headers() != null) raw.headers().forEach((k, v) -> ex.getResponseHeaders().set(k, v));
                    ex.getResponseHeaders().set("Content-Type", raw.contentType());
                    ex.sendResponseHeaders(raw.status(), raw.body().length == 0 ? -1 : raw.body().length);
                    try (OutputStream os = ex.getResponseBody()) {
                        os.write(raw.body());
                    }
                } else if (result instanceof Stream s) {
                    ex.getResponseHeaders().set("Content-Type", s.contentType());
                    ex.sendResponseHeaders(200, 0);
                    try (OutputStream os = ex.getResponseBody()) {
                        s.streamer().stream(os);
                    }
                } else {
                    sendJson(ex, r.status, result);
                }
                return;
            }
            if (pathMatched) sendJson(ex, 405, Json.obj("detail", "Method Not Allowed"));
            else sendJson(ex, 404, Json.obj("detail", "Not Found"));
        } catch (IOException e) {
            // client went away
        } finally {
            ex.close();
        }
    }

    static void sendJson(HttpExchange ex, int status, Object body) throws IOException {
        byte[] bytes = Json.dumps(body).getBytes(StandardCharsets.UTF_8);
        ex.getResponseHeaders().set("Content-Type", "application/json");
        ex.sendResponseHeaders(status, bytes.length);
        try (OutputStream os = ex.getResponseBody()) {
            os.write(bytes);
        }
    }

    static void sendText(HttpExchange ex, int status, String text) throws IOException {
        byte[] bytes = text.getBytes(StandardCharsets.UTF_8);
        ex.getResponseHeaders().set("Content-Type", "text/plain; charset=utf-8");
        ex.sendResponseHeaders(status, bytes.length);
        try (OutputStream os = ex.getResponseBody()) {
            os.write(bytes);
        }
    }
}
