package falcon;

import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.util.ArrayList;
import java.util.HashMap;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Locale;
import java.util.Map;
import java.util.UUID;

/**
 * Plain-HTTP client for the Qdrant "words" collection, one tenant per
 * language (mirrors backend/qdrant_store.py, runtime subset — the populator
 * stays in data_builder/).
 */
public final class QdrantStore {
    public static final String DEFAULT_QDRANT_HOST = "127.0.0.1";
    public static final String DEFAULT_QDRANT_PORT = "6333";
    public static final String DEFAULT_COLLECTION = "words";
    public static final String DEFAULT_DISTANCE = "Cosine";
    public static final double DEFAULT_TIMEOUT = 30.0;
    public static final String TENANT_FIELD = "lang";
    public static final int QDRANT_REQUEST_RETRIES = 2;
    public static final double QDRANT_REQUEST_RETRY_DELAY_S = 1.0;
    private static final UUID POINT_ID_NAMESPACE = UUID.fromString("9f1c0b2a-3d4e-5f60-8a90-b0c1d2e3f405");

    public static final class QdrantStoreError extends RuntimeException {
        public QdrantStoreError(String msg) {
            super(msg);
        }
    }

    public final String baseUrl;
    public final String collection;
    private final String apiKey;
    private final double timeout;

    public QdrantStore() {
        this(DEFAULT_TIMEOUT);
    }

    public QdrantStore(double timeout) {
        String url = Env.getNonEmpty("QDRANT_URL", null);
        if (url == null) {
            url = "http://" + Env.get("QDRANT_HOST", DEFAULT_QDRANT_HOST) + ":" + Env.get("QDRANT_PORT", DEFAULT_QDRANT_PORT);
        }
        this.baseUrl = url.replaceAll("/+$", "");
        this.collection = Env.get("QDRANT_COLLECTION", DEFAULT_COLLECTION);
        this.apiKey = Env.get("QDRANT_API_KEY", "");
        this.timeout = timeout;
    }

    /** uuid5(namespace, "lang:word") — identical ids to the Python side. */
    public static String wordPointId(String lang, String word) {
        try {
            MessageDigest sha1 = MessageDigest.getInstance("SHA-1");
            long msb = POINT_ID_NAMESPACE.getMostSignificantBits();
            long lsb = POINT_ID_NAMESPACE.getLeastSignificantBits();
            byte[] ns = new byte[16];
            for (int i = 0; i < 8; i++) ns[i] = (byte) (msb >>> (8 * (7 - i)));
            for (int i = 0; i < 8; i++) ns[8 + i] = (byte) (lsb >>> (8 * (7 - i)));
            sha1.update(ns);
            sha1.update((lang + ":" + word).getBytes(StandardCharsets.UTF_8));
            byte[] h = sha1.digest();
            h[6] = (byte) ((h[6] & 0x0f) | 0x50);
            h[8] = (byte) ((h[8] & 0x3f) | 0x80);
            long m = 0, l = 0;
            for (int i = 0; i < 8; i++) m = (m << 8) | (h[i] & 0xff);
            for (int i = 8; i < 16; i++) l = (l << 8) | (h[i] & 0xff);
            return new UUID(m, l).toString();
        } catch (Exception e) {
            throw new IllegalStateException(e);
        }
    }

    private static Map<String, Object> langFilter(String lang) {
        return Json.obj("must", Json.list(Json.obj("key", TENANT_FIELD, "match", Json.obj("value", lang))));
    }

    private Map<String, String> headers() {
        Map<String, String> h = new HashMap<>();
        if (!apiKey.isEmpty()) h.put("api-key", apiKey);
        return h;
    }

    private Http.Response request(String method, String path, Object body) {
        Exception last = null;
        for (int attempt = 0; attempt <= QDRANT_REQUEST_RETRIES; attempt++) {
            try {
                return Http.request(method, baseUrl + path, body, headers(), timeout);
            } catch (IOException e) {
                last = e;
                if (attempt < QDRANT_REQUEST_RETRIES) {
                    try {
                        Thread.sleep((long) (QDRANT_REQUEST_RETRY_DELAY_S * 1000 * (attempt + 1)));
                    } catch (InterruptedException ie) {
                        Thread.currentThread().interrupt();
                        break;
                    }
                }
            } catch (IllegalArgumentException e) {
                throw new QdrantStoreError("Qdrant request " + method + " " + path + " failed: " + e.getMessage()
                        + ". Is Qdrant running? (./run_qdrant.sh)");
            }
        }
        throw new QdrantStoreError("Qdrant request " + method + " " + path + " failed after "
                + (QDRANT_REQUEST_RETRIES + 1) + " attempts: " + last + ". Is Qdrant running? (./run_qdrant.sh)");
    }

    private Http.Response ok(Http.Response resp, String method, String path, int... extraOk) {
        int s = resp.status();
        if (s == 200 || s == 201 || s == 202) return resp;
        for (int e : extraOk) if (s == e) return resp;
        String t = resp.body();
        throw new QdrantStoreError("Qdrant " + method + " " + path + " -> " + s + ": "
                + (t.length() > 300 ? t.substring(0, 300) : t));
    }

    public boolean ping() {
        try {
            return request("GET", "/", null).status() == 200;
        } catch (QdrantStoreError e) {
            return false;
        }
    }

    public boolean collectionExists() {
        String p = "/collections/" + collection;
        Http.Response r = request("GET", p, null);
        if (r.status() == 200) return true;
        if (r.status() == 404) return false;
        ok(r, "GET", p);
        return true;
    }

    private int resolveDimension(Integer dimension) {
        if (dimension != null && dimension > 0) return dimension;
        String env = Env.getNonEmpty("QDRANT_VECTOR_SIZE", null);
        if (env != null) return Integer.parseInt(env.trim());
        return new Embedder().dimension();
    }

    public Map<String, Object> ensureCollection(Integer dimension, boolean recreate) {
        String distance = Env.get("QDRANT_DISTANCE", DEFAULT_DISTANCE);
        boolean onDisk = List.of("1", "true", "yes", "on").contains(Env.get("QDRANT_ON_DISK", "0").toLowerCase(Locale.ROOT));
        boolean exists = collectionExists();
        String p = "/collections/" + collection;
        if (exists && recreate) {
            ok(request("DELETE", p, null), "DELETE", p);
            exists = false;
        }
        if (!exists) {
            int dim = resolveDimension(dimension);
            Map<String, Object> body = Json.obj(
                    "vectors", Json.obj("size", dim, "distance", distance, "on_disk", onDisk),
                    "on_disk_payload", onDisk);
            ok(request("PUT", p, body), "PUT", p);
        }
        ensureTenantIndex();
        return collectionInfo();
    }

    public void ensureTenantIndex() {
        Map<String, Object> body = Json.obj("field_name", TENANT_FIELD,
                "field_schema", Json.obj("type", "keyword", "is_tenant", true));
        Http.Response r = request("PUT", "/collections/" + collection + "/index?wait=true", body);
        int s = r.status();
        if (s == 200 || s == 201 || s == 202) return;
        if (r.body().toLowerCase(Locale.ROOT).contains("already exists")) return;
        throw new QdrantStoreError("could not create tenant index on '" + TENANT_FIELD + "': " + s + " "
                + (r.body().length() > 300 ? r.body().substring(0, 300) : r.body()));
    }

    public Map<String, Object> collectionInfo() {
        String p = "/collections/" + collection;
        Http.Response r = ok(request("GET", p, null), "GET", p);
        return Json.mapOrEmpty(Json.get(r.json(), "result"));
    }

    public long count(String lang) {
        Map<String, Object> body = Json.obj("exact", true);
        if (lang != null && !lang.isEmpty()) body.put("filter", langFilter(lang));
        String p = "/collections/" + collection + "/points/count";
        Http.Response r = ok(request("POST", p, body), "POST", p);
        return ((Number) Json.get(Json.get(r.json(), "result"), "count")).longValue();
    }

    public Object deleteLang(String lang) {
        String p = "/collections/" + collection + "/points/delete?wait=true";
        return ok(request("POST", p, Json.obj("filter", langFilter(lang))), "POST", p).json();
    }

    /** Ranked nearest neighbours; each hit is {id, score, payload}. */
    public List<Object> search(double[] vector, String lang, int limit, int offset) {
        List<Object> vec = new ArrayList<>(vector.length);
        for (double d : vector) vec.add(d);
        Map<String, Object> body = Json.obj("vector", vec, "limit", limit, "with_payload", true);
        if (lang != null && !lang.isEmpty()) body.put("filter", langFilter(lang));
        if (offset > 0) body.put("offset", offset);
        String p = "/collections/" + collection + "/points/search";
        return Json.listOrEmpty(Json.get(ok(request("POST", p, body), "POST", p).json(), "result"));
    }

    public Map<String, double[]> retrieveVectors(List<String> ids) {
        Map<String, double[]> out = new LinkedHashMap<>();
        if (ids.isEmpty()) return out;
        String p = "/collections/" + collection + "/points";
        Http.Response r = ok(request("POST", p, Json.obj("ids", new ArrayList<>(ids), "with_vector", true,
                "with_payload", false)), "POST", p);
        for (Object point : Json.listOrEmpty(Json.get(r.json(), "result"))) {
            List<Object> v = Json.listOrEmpty(Json.get(point, "vector"));
            double[] arr = new double[v.size()];
            for (int i = 0; i < arr.length; i++) arr[i] = ((Number) v.get(i)).doubleValue();
            out.put(String.valueOf(Json.get(point, "id")), arr);
        }
        return out;
    }

    public Map<String, double[]> retrieveWordVectors(String lang, List<String> words, int batchSize) {
        Map<String, String> idToWord = new LinkedHashMap<>();
        for (String w : words) idToWord.put(wordPointId(lang, w), w);
        List<String> ids = new ArrayList<>(idToWord.keySet());
        Map<String, double[]> out = new LinkedHashMap<>();
        for (int i = 0; i < ids.size(); i += batchSize) {
            List<String> chunk = ids.subList(i, Math.min(ids.size(), i + batchSize));
            for (Map.Entry<String, double[]> e : retrieveVectors(chunk).entrySet()) {
                String w = idToWord.get(e.getKey());
                if (w != null) out.put(w, e.getValue());
            }
        }
        return out;
    }

    public List<Object> searchText(String text, String lang, int limit) {
        return search(new Embedder().embed(text), lang, limit, 0);
    }
}
