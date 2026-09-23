package falcon;

import java.io.IOException;
import java.util.ArrayList;
import java.util.Comparator;
import java.util.List;
import java.util.Map;

/**
 * Multilingual text embeddings via an OpenAI-compatible /v1/embeddings API
 * (mirrors backend/embedder.py). Vectors are L2-normalized by default so a
 * dot product is the cosine similarity.
 */
public final class Embedder {
    public static final String DEFAULT_EMBED_BASE_URL = "http://127.0.0.1:3003/v1";
    public static final String DEFAULT_EMBED_MODEL = "bge-m3";
    public static final String DEFAULT_EMBED_API_KEY = "EMPTY";
    public static final double DEFAULT_TIMEOUT = 60.0;

    public static final class EmbedderError extends RuntimeException {
        public EmbedderError(String msg, Throwable cause) {
            super(msg, cause);
        }
    }

    public final String baseUrl;
    public final String model;
    private final String apiKey;
    private final double timeout;
    private volatile Integer dimension;

    public Embedder() {
        this(null, null, null, DEFAULT_TIMEOUT);
    }

    public Embedder(String baseUrl, String model, String apiKey, double timeout) {
        String b = baseUrl != null ? baseUrl : Env.get("EMBED_BASE_URL", DEFAULT_EMBED_BASE_URL);
        this.baseUrl = b.replaceAll("/+$", "");
        this.model = model != null ? model : Env.get("EMBED_MODEL", DEFAULT_EMBED_MODEL);
        this.apiKey = apiKey != null ? apiKey : Env.get("EMBED_API_KEY", DEFAULT_EMBED_API_KEY);
        this.timeout = timeout;
    }

    static double[] l2Normalize(double[] v) {
        double s = 0;
        for (double x : v) s += x * x;
        double n = Math.sqrt(s);
        if (n == 0.0) return v.clone();
        double[] out = new double[v.length];
        for (int i = 0; i < v.length; i++) out[i] = v[i] / n;
        return out;
    }

    public double[] embed(String text) {
        return embedBatch(List.of(text), true).get(0);
    }

    public List<double[]> embedBatch(List<String> texts, boolean normalize) {
        if (texts.isEmpty()) return List.of();
        Object payload;
        try {
            Http.Response r = Http.postJson(baseUrl + "/embeddings",
                    Json.obj("model", model, "input", new ArrayList<>(texts)),
                    Map.of("Authorization", "Bearer " + apiKey), timeout);
            if (r.status() >= 400) throw new IOException("HTTP " + r.status() + ": " + abbreviate(r.body()));
            payload = r.json();
        } catch (IOException | IllegalArgumentException e) {
            throw new EmbedderError("embedding request to " + baseUrl + " failed: " + e.getMessage()
                    + ". Is the embed server running? (./run_embed.sh)", e);
        }
        List<Object> rows = Json.listOrEmpty(Json.get(payload, "data"));
        if (rows.isEmpty() || rows.size() != texts.size()) {
            throw new EmbedderError("unexpected embeddings response: " + abbreviate(String.valueOf(payload)), null);
        }
        rows = new ArrayList<>(rows);
        rows.sort(Comparator.comparingInt(r -> Json.integer(r, "index", 0)));
        List<double[]> vecs = new ArrayList<>();
        for (Object row : rows) {
            List<Object> emb = Json.listOrEmpty(Json.get(row, "embedding"));
            double[] v = new double[emb.size()];
            for (int i = 0; i < v.length; i++) v[i] = ((Number) emb.get(i)).doubleValue();
            vecs.add(normalize ? l2Normalize(v) : v);
        }
        if (dimension == null && !vecs.isEmpty()) dimension = vecs.get(0).length;
        return vecs;
    }

    public int dimension() {
        if (dimension == null) embed("dimension probe");
        return dimension;
    }

    private static String abbreviate(String s) {
        return s.length() <= 200 ? s : s.substring(0, 200);
    }
}
