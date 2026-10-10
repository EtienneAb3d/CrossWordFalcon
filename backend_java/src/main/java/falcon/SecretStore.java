package falcon;

import javax.crypto.SecretKeyFactory;
import javax.crypto.spec.PBEKeySpec;
import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.security.SecureRandom;
import java.text.Normalizer;
import java.util.ArrayList;
import java.util.Comparator;
import java.util.HexFormat;
import java.util.List;
import java.util.Map;
import java.util.regex.Pattern;
import java.util.stream.Stream;

/**
 * Pseudo/secret-word pairs under SECRET/ (mirrors backend/secret_store.py,
 * same on-disk format: file named by the pseudo's save-file slug
 * ({@link GridStore#slugifyPseudo}), PBKDF2-HMAC-SHA256 salted hash, so both
 * back ends read each other's files). Files of the earlier
 * {@code <sha256(pseudo)>.json} layout are moved to their slug name once per
 * process, the earliest claim winning a shared slug.
 */
public final class SecretStore {
    private SecretStore() {}

    public static final Path SECRET_DIR = Env.path("SECRET");
    private static final int PBKDF2_ITERATIONS = 200_000;
    private static final int SALT_BYTES = 16;
    private static final Object LOCK = new Object();

    private static final Pattern LEGACY_NAME_RE = Pattern.compile("^[0-9a-f]{64}\\.json$");
    private static final String PLAIN_PSEUDO_CHARS =
            "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_ ";
    private static boolean legacyMigrated = false;

    private static boolean isAsciiLetter(char c) {
        return (c >= 'a' && c <= 'z') || (c >= 'A' && c <= 'Z');
    }

    private static boolean isPseudoLetter(char c) {
        String d = Normalizer.normalize(String.valueOf(c), Normalizer.Form.NFD);
        if (!isAsciiLetter(d.charAt(0))) return false;
        for (int i = 1; i < d.length(); i++) {
            if (Character.getType(d.charAt(i)) != Character.NON_SPACING_MARK) return false;
        }
        return true;
    }

    /** True when {@code pseudo} (already stripped) holds only letters, accented
     * or not, digits, "-", "_" and inner spaces, with at least one letter. */
    public static boolean isValidPseudo(String pseudo) {
        pseudo = Normalizer.normalize(pseudo, Normalizer.Form.NFC);
        if (pseudo.isEmpty() || pseudo.startsWith(" ") || pseudo.endsWith(" ")) return false;
        boolean hasLetter = false;
        for (int i = 0; i < pseudo.length(); i++) {
            char c = pseudo.charAt(i);
            if (isPseudoLetter(c)) hasLetter = true;
            else if (PLAIN_PSEUDO_CHARS.indexOf(c) < 0) return false;
        }
        return hasLetter;
    }

    private static Path pseudoPath(String pseudo) {
        return SECRET_DIR.resolve(GridStore.slugifyPseudo(pseudo) + ".json");
    }

    /** False when {@code pseudo} has no ASCII letter or digit once accents are
     * stripped, i.e. when its slug would fall back on "anonyme". */
    private static boolean hasSlug(String pseudo) {
        String d = Normalizer.normalize(pseudo, Normalizer.Form.NFKD);
        for (int i = 0; i < d.length(); i++) {
            char c = d.charAt(i);
            if (isAsciiLetter(c) || (c >= '0' && c <= '9')) return true;
        }
        return false;
    }

    private record Legacy(double createdAt, String name, Path path, String slug) {}

    private static void migrateLegacyFiles() {
        if (legacyMigrated) return;
        legacyMigrated = true;
        if (!Files.isDirectory(SECRET_DIR)) return;
        List<Legacy> records = new ArrayList<>();
        try (Stream<Path> files = Files.list(SECRET_DIR)) {
            for (Path path : (Iterable<Path>) files::iterator) {
                String name = path.getFileName().toString();
                if (!LEGACY_NAME_RE.matcher(name).matches()) continue;
                try {
                    Map<String, Object> data = Json.asMap(Json.readFile(path));
                    if (!(data.get("pseudo") instanceof String pseudo) || !hasSlug(pseudo)) continue;
                    double createdAt = data.get("created_at") instanceof Number n ? n.doubleValue() : 0.0;
                    records.add(new Legacy(createdAt, name, path, GridStore.slugifyPseudo(pseudo)));
                } catch (Exception ignored) {
                    // Unreadable: left as it is.
                }
            }
        } catch (IOException e) {
            return;
        }
        records.sort(Comparator.comparingDouble(Legacy::createdAt).thenComparing(Legacy::name));
        for (Legacy rec : records) {
            Path target = SECRET_DIR.resolve(rec.slug() + ".json");
            try {
                if (Files.exists(target)) {
                    Files.move(rec.path(), rec.path().resolveSibling(rec.name() + ".duplicate"));
                } else {
                    Files.move(rec.path(), target);
                }
            } catch (IOException ignored) {
                // Left as it is.
            }
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

    /** True if {@code secret} matches the one stored for the pseudo's slug,
     * or if that slug was free and is now claimed with it; false only on a
     * mismatch. */
    public static boolean verifyOrClaim(String pseudo, String secret) {
        Path path = pseudoPath(pseudo);
        synchronized (LOCK) {
            migrateLegacyFiles();
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
