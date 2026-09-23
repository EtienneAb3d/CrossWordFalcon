package falcon;

import java.io.IOException;
import java.net.URI;
import java.net.http.HttpClient;
import java.net.http.HttpRequest;
import java.net.http.HttpResponse;
import java.nio.charset.StandardCharsets;
import java.time.Duration;
import java.util.Map;

/** Small wrapper over the JDK HTTP client (the httpx equivalent). */
public final class Http {
    private Http() {}

    public static final HttpClient CLIENT = HttpClient.newBuilder()
            .connectTimeout(Duration.ofSeconds(10))
            .version(HttpClient.Version.HTTP_1_1)
            .followRedirects(HttpClient.Redirect.NORMAL)
            .build();

    public record Response(int status, String body) {
        public Object json() {
            return Json.parse(body);
        }
    }

    public static Response request(String method, String url, Object jsonBody, Map<String, String> headers,
                                   double timeoutS) throws IOException {
        HttpRequest.Builder b = HttpRequest.newBuilder(URI.create(url))
                .timeout(Duration.ofMillis((long) (timeoutS * 1000)));
        if (headers != null) headers.forEach(b::header);
        if (jsonBody != null) {
            b.header("Content-Type", "application/json");
            b.method(method, HttpRequest.BodyPublishers.ofString(Json.dumps(jsonBody), StandardCharsets.UTF_8));
        } else {
            b.method(method, HttpRequest.BodyPublishers.noBody());
        }
        try {
            HttpResponse<String> r = CLIENT.send(b.build(), HttpResponse.BodyHandlers.ofString(StandardCharsets.UTF_8));
            return new Response(r.statusCode(), r.body());
        } catch (InterruptedException e) {
            Thread.currentThread().interrupt();
            throw new IOException("interrupted", e);
        }
    }

    public static Response get(String url, Map<String, String> headers, double timeoutS) throws IOException {
        return request("GET", url, null, headers, timeoutS);
    }

    public static Response postJson(String url, Object body, Map<String, String> headers, double timeoutS)
            throws IOException {
        return request("POST", url, body, headers, timeoutS);
    }
}
