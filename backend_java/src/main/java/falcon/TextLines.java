package falcon;

import java.io.ByteArrayOutputStream;
import java.io.IOException;
import java.io.RandomAccessFile;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;

/**
 * Byte-offset access to the lines of a UTF-8 text file — lets a lexicon keep
 * only a line's position in RAM (the wordlists' accented forms and lemmas,
 * the gloss dictionary's entries) and read the line back when it is needed.
 * Mirrors backend/text_lines.py.
 */
public final class TextLines {
    private TextLines() {}

    /** Receives one line and the byte offset of its first byte. */
    @FunctionalInterface
    public interface LineConsumer {
        void accept(long offset, String line);
    }

    /** Calls {@code consumer} for every line of {@code path}, without its trailing newline (nor a trailing
     * carriage return), with the byte position of its first byte (mirrors iter_lines_with_offsets). */
    public static void forEachLine(Path path, LineConsumer consumer) throws IOException {
        byte[] data = Files.readAllBytes(path);
        int start = 0;
        while (start < data.length) {
            int nl = start;
            while (nl < data.length && data[nl] != '\n') nl++;
            int end = nl;
            while (end > start && (data[end - 1] == '\r' || data[end - 1] == '\n')) end--;
            consumer.accept(start, new String(data, start, end - start, StandardCharsets.UTF_8));
            start = nl + 1;
        }
    }

    /** The line of {@code path} starting at byte {@code offset}, decoded, without its trailing newline (nor a
     * trailing carriage return) (mirrors read_line_at). */
    public static String readLineAt(Path path, long offset) throws IOException {
        try (RandomAccessFile f = new RandomAccessFile(path.toFile(), "r")) {
            f.seek(offset);
            ByteArrayOutputStream out = new ByteArrayOutputStream(128);
            byte[] buf = new byte[512];
            outer:
            while (true) {
                int n = f.read(buf);
                if (n <= 0) break;
                for (int i = 0; i < n; i++) {
                    if (buf[i] == '\n') {
                        out.write(buf, 0, i);
                        break outer;
                    }
                }
                out.write(buf, 0, n);
            }
            byte[] line = out.toByteArray();
            int end = line.length;
            while (end > 0 && line[end - 1] == '\r') end--;
            return new String(line, 0, end, StandardCharsets.UTF_8);
        }
    }
}
