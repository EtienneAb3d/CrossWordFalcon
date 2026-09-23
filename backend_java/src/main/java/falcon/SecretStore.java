package falcon;

import javax.crypto.SecretKeyFactory;
import javax.crypto.spec.PBEKeySpec;
import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.security.MessageDigest;
import java.security.SecureRandom;
import java.util.HexFormat;
import java.util.Map;

/**
 * Pseudo/secret-word pairs under SECRET/ (mirrors backend/secret_store.py,
 * same on-disk format: file named by SHA-256(pseudo), PBKDF2-HMAC-SHA256
 * salted hash, so both back ends read each other's files).
 */
public final class SecretStore {
    private SecretStore() {}

    public static final Path SECRET_DIR = Env.path("SECRET");
    private static final int PBKDF2_ITERATIONS = 200_000;
    private static final int SALT_BYTES = 16;
    private static final Object LOCK = new Object();

    private static Path pseudoPath(String pseudo) {
        try {
            byte[] d = MessageDigest.getInstance("SHA-256").digest(pseudo.getBytes(StandardCharsets.UTF_8));
            return SECRET_DIR.resolve(HexFormat.of().formatHex(d) + ".json");
        } catch (Exception e) {
            throw new IllegalStateException(e);
        }
    }

    private static String hashSecret(String secret, byte[] salt) {
        try {
            PBEKeySpec spec = new PBEKeySpec(secret.toCharArray(), salt, PBKDF2_ITERATIONS, 256);
            byte[] out = SecretKeyFactory.getInstance("PBKDF2WithHmacSHA256").generateSecret(spec).getEncoded();
            return HexFormat.of().formatHex(out);
        } catch (Exception e) {
            throw new IllegalStateException(e);
        }
    }

    /** True if {@code secret} matches the stored one, or if the pseudo was
     * free and is now claimed with it; false only on a mismatch. */
    public static boolean verifyOrClaim(String pseudo, String secret) {
        Path path = pseudoPath(pseudo);
        synchronized (LOCK) {
            if (Files.exists(path)) {
                try {
                    Map<String, Object> data = Json.asMap(Json.readFile(path));
                    byte[] salt = HexFormat.of().parseHex((String) data.get("salt"));
                    String stored = (String) data.get("secret_hash");
                    if (stored != null) return hashSecret(secret, salt).equals(stored);
                } catch (Exception ignored) {
                    // Corrupted/unreadable: rewritten below as a fresh claim.
                }
            }
            byte[] salt = new byte[SALT_BYTES];
            new SecureRandom().nextBytes(salt);
            Map<String, Object> record = Json.obj(
                    "pseudo", pseudo,
                    "salt", HexFormat.of().formatHex(salt),
                    "secret_hash", hashSecret(secret, salt),
                    "created_at", System.currentTimeMillis() / 1000.0);
            try {
                Files.createDirectories(SECRET_DIR);
                Files.writeString(path, Json.dumpsAscii(record), StandardCharsets.UTF_8);
            } catch (IOException e) {
                Env.log("secret_store: cannot write " + path + ": " + e.getMessage());
            }
            return true;
        }
    }
}
