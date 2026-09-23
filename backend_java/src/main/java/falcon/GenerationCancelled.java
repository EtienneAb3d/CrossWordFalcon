package falcon;

/** Cooperative "Stop" signal, raised at the next natural checkpoint of a
 * running generation (mirrors crossword_gen.GenerationCancelled). */
public class GenerationCancelled extends RuntimeException {
    public GenerationCancelled() {
        super("generation cancelled", null, false, false);
    }
}
