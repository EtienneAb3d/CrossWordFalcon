package falcon.gen;

import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

/** One attempt's diagnostics / published snapshot (Python's `diag` dicts
 * and best_state_queue messages). Null fields are "key absent". */
public final class Diag {
    public Integer slotCount;
    public Map<String, Object> lengthCounts;
    public Long checks;
    public String reason;
    public char[][] grid;
    public char[][] exampleGrid;
    public List<Integer> forcedCells, impossibleCells, deadlockCells, excludedCells, lockedCells, themeCells,
            challengeCells;
    public List<Object> statLetters;
    public Integer assignedLetterCount;
    public String[] assignment;
    public List<Integer> impossibleSlots;
    public Long attemptId;
    public Integer processNumber;
    public String kind;

    public Diag copy() {
        Diag d = new Diag();
        d.slotCount = slotCount;
        d.lengthCounts = lengthCounts;
        d.checks = checks;
        d.reason = reason;
        d.grid = grid;
        d.exampleGrid = exampleGrid;
        d.forcedCells = forcedCells;
        d.impossibleCells = impossibleCells;
        d.deadlockCells = deadlockCells;
        d.excludedCells = excludedCells;
        d.lockedCells = lockedCells;
        d.themeCells = themeCells;
        d.challengeCells = challengeCells;
        d.statLetters = statLetters;
        d.assignedLetterCount = assignedLetterCount;
        d.assignment = assignment;
        d.impossibleSlots = impossibleSlots;
        d.attemptId = attemptId;
        d.processNumber = processNumber;
        d.kind = kind;
        return d;
    }

    static List<Object> cells(List<Integer> cells) {
        return cells == null ? null : Cells.toJson(cells);
    }

    public static List<Object> assignmentJson(String[] a) {
        List<Object> out = new ArrayList<>(a.length);
        for (String w : a) out.add(w);
        return out;
    }

    /** JSON view; {@code grid} is never included, {@code assignment} only
     * when asked. */
    public Map<String, Object> toJson(boolean includeAssignment) {
        Map<String, Object> m = new LinkedHashMap<>();
        if (slotCount != null) m.put("slot_count", slotCount);
        if (lengthCounts != null) m.put("length_counts", lengthCounts);
        if (checks != null) m.put("checks", checks);
        if (reason != null) m.put("reason", reason);
        if (exampleGrid != null) m.put("example_grid", Grids.toJson(exampleGrid));
        if (impossibleCells != null) m.put("impossible_cells", cells(impossibleCells));
        if (deadlockCells != null) m.put("deadlock_cells", cells(deadlockCells));
        if (excludedCells != null) m.put("excluded_cells", cells(excludedCells));
        if (statLetters != null) m.put("stat_letters", statLetters);
        if (forcedCells != null) m.put("forced_cells", cells(forcedCells));
        if (assignedLetterCount != null) m.put("assigned_letter_count", assignedLetterCount);
        if (includeAssignment && assignment != null) m.put("assignment", assignmentJson(assignment));
        if (impossibleSlots != null) m.put("impossible_slots", new ArrayList<>(impossibleSlots));
        if (lockedCells != null) m.put("locked_cells", cells(lockedCells));
        if (themeCells != null) m.put("theme_cells", cells(themeCells));
        if (challengeCells != null) m.put("challenge_cells", cells(challengeCells));
        if (attemptId != null) m.put("attempt_id", attemptId);
        if (kind != null) m.put("kind", kind);
        if (processNumber != null) m.put("process_number", processNumber);
        return m;
    }
}
