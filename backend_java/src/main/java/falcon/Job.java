package falcon;

import java.nio.charset.StandardCharsets;
import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.concurrent.atomic.AtomicBoolean;
import java.util.function.Consumer;

/** One entry of JOBS: a JSON-shaped state map mutated and serialized under
 * the job's own lock (Python relied on the GIL for this). */
public final class Job {
    public final String id;
    public final AtomicBoolean cancel = new AtomicBoolean(false);
    private final Map<String, Object> data = new LinkedHashMap<>();

    Job(String id) {
        this.id = id;
        data.put("status", "running");
        data.put("step", Json.obj("code", "starting"));
        data.put("result", null);
        data.put("error", null);
        data.put("error_code", null);
        data.put("examples_history", new ArrayList<>());
        data.put("live_preview", null);
        data.put("clues_progress", new ArrayList<>());
        data.put("resume_state", null);
        data.put("request", null);
        data.put("interactive", null);
    }

    public synchronized Object get(String key) {
        return data.get(key);
    }

    public synchronized void put(String key, Object value) {
        data.put(key, value);
    }

    public synchronized void update(Consumer<Map<String, Object>> fn) {
        fn.accept(data);
    }

    @SuppressWarnings("unchecked")
    public synchronized void append(String listKey, Object value) {
        ((List<Object>) data.get(listKey)).add(value);
    }

    @SuppressWarnings("unchecked")
    public synchronized Map<String, Object> step() {
        Object s = data.get("step");
        return s instanceof Map<?, ?> ? new LinkedHashMap<>((Map<String, Object>) s) : new LinkedHashMap<>();
    }

    /** Status JSON (live_preview entries stripped of "previous"). */
    @SuppressWarnings("unchecked")
    public synchronized byte[] statusJson() {
        Object lp = data.get("live_preview");
        Object view = data;
        if (lp instanceof List<?> l && !l.isEmpty()) {
            Map<String, Object> copy = new LinkedHashMap<>(data);
            List<Object> stripped = new ArrayList<>();
            for (Object e : l) {
                Map<String, Object> m = new LinkedHashMap<>((Map<String, Object>) e);
                m.remove("previous");
                stripped.add(m);
            }
            copy.put("live_preview", stripped);
            view = copy;
        }
        return Json.dumps(view).getBytes(StandardCharsets.UTF_8);
    }

    /** Deep JSON copy of one field (for the STOP_DUMP). */
    public synchronized Object copyOf(String key) {
        Object v = data.get(key);
        return v == null ? null : Json.parse(Json.dumps(v));
    }
}
