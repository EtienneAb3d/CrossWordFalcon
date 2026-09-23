package falcon;

/** Fair-scheduling preemption: carries whatever state the caller needs to
 * resume later (mirrors crossword_gen.GenerationPaused). */
public class GenerationPaused extends RuntimeException {
    public final transient Object state;

    public GenerationPaused(Object state) {
        super("generation paused", null, false, false);
        this.state = state;
    }
}
