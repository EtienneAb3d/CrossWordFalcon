const BLACK = "#";

// I18N (the translation config for every language) lives in its own file,
// i18n.js, loaded before this one — see index.html.

let uiLanguage = "fr";

function applyTranslations() {
  const t = I18N[uiLanguage];
  document.documentElement.lang = uiLanguage;
  document.title = t.pageTitle;
  document.querySelectorAll("[data-i18n]").forEach((el) => {
    const key = el.getAttribute("data-i18n");
    if (t[key]) el.textContent = t[key];
  });
  document.querySelectorAll("[data-i18n-aria]").forEach((el) => {
    const key = el.getAttribute("data-i18n-aria");
    if (t[key]) el.setAttribute("aria-label", t[key]);
  });
  renderSystemInfoTooltip();
}

const form = document.getElementById("generate-form");
const languageSelect = document.getElementById("language");
const button = document.getElementById("generate-btn");
const status = document.getElementById("status");
const result = document.getElementById("result");
const stats = document.getElementById("stats");
const gridEl = document.getElementById("grid");
const cluesAcross = document.getElementById("clues-across");
const cluesDown = document.getElementById("clues-down");
const solutionBtn = document.getElementById("solution-btn");
const checkBtn = document.getElementById("check-btn");
const versionBadge = document.getElementById("version-badge");
const infoBadge = document.getElementById("info-badge");
const infoTooltip = document.getElementById("info-tooltip");

fetch("/api/version")
  .then((r) => r.json())
  .then((data) => {
    if (data.version) {
      versionBadge.textContent = `v${data.version}`;
      versionBadge.hidden = false;
    }
  })
  .catch(() => {});

// Filled in once the fetch below resolves; kept as raw data (not
// pre-rendered text) so renderSystemInfoTooltip() can redraw it in
// whichever language the user later switches the UI to, without a
// second network round-trip.
let systemInfo = null;

function renderSystemInfoTooltip() {
  if (!systemInfo) return;
  const t = I18N[uiLanguage];
  const lines = [t.systemInfoModel(systemInfo.llm_model)];
  lines.push(systemInfo.compute === "gpu" ? t.systemInfoComputeGpu : t.systemInfoComputeCpu);
  if (systemInfo.compute === "gpu" && systemInfo.gpu_name) {
    lines.push(t.systemInfoGpuName(systemInfo.gpu_name));
    if (systemInfo.gpu_vram_mb) {
      const gb = Math.round(systemInfo.gpu_vram_mb / 1024);
      lines.push(systemInfo.unified_memory ? t.systemInfoUnifiedMemory(gb) : t.systemInfoVram(gb));
    }
  }
  infoTooltip.replaceChildren(
    ...lines.map((line) => {
      const div = document.createElement("div");
      div.textContent = line;
      return div;
    })
  );
}

fetch("/api/system_info")
  .then((r) => r.json())
  .then((data) => {
    systemInfo = data;
    renderSystemInfoTooltip();
    infoBadge.hidden = false;
  })
  .catch(() => {});

// Current puzzle state.
let puzzle = null; // { width, height, pattern, solution, words }
let userLetters = []; // [row][col] -> letter typed by the player, or ""
let selected = null; // { row, col } or null
let showSolution = false;
let checking = false;

// Grid <-> clue-list hover highlighting. cellElements lets highlightWordAt()
// look up a cell's DOM node by position without a fresh querySelector per
// cell; both are rebuilt from scratch on every renderGrid() call, since the
// grid itself is fully re-rendered rather than patched.
let cellElements = new Map(); // "row,col" -> cell element (white cells only)
let hoveredGridCell = null; // { row, col } while the mouse is over a grid cell, else null

// Finds every cell of the white-cell run through (row, col) in `direction`
// ("across"/"down") by scanning the pattern outward until a black cell or
// the grid edge — not a lookup against puzzle.words — so it works for any
// cell of the word, not just its numbered start. Every white run is at
// least 3 cells long (see backend/crossword_gen.py's structural-validity
// check), so this always returns a real word, never a 1-2 cell fragment.
function wordCellsAt(row, col, direction) {
  const { width, height } = puzzle;
  const cells = [];
  if (direction === "across") {
    let c = col;
    while (c > 0 && isWhite(row, c - 1)) c--;
    for (; c < width && isWhite(row, c); c++) cells.push({ row, col: c });
  } else {
    let r = row;
    while (r > 0 && isWhite(r - 1, col)) r--;
    for (; r < height && isWhite(r, col); r++) cells.push({ row: r, col });
  }
  return cells;
}

function clearHighlights() {
  document.querySelectorAll(".cell.word-highlight").forEach((el) => el.classList.remove("word-highlight"));
  document.querySelectorAll(".clue-segment.hover-highlight").forEach((el) => el.classList.remove("hover-highlight"));
}

// Shared by both hover directions (grid -> clue list and clue list ->
// grid, see the event listeners in renderGrid()/renderClueLines()): frames
// every cell of the word through (row, col) in `direction`, and highlights
// the matching clue-segment span by its own data-row/col/direction — set
// from the same word-start position wordCellsAt() itself resolves to
// (cells[0]), so this never needs to search puzzle.words to find "which
// word is this".
function highlightWordAt(row, col, direction) {
  clearHighlights();
  if (!puzzle || !isWhite(row, col)) return;
  const cells = wordCellsAt(row, col, direction);
  for (const { row: r, col: c } of cells) {
    const el = cellElements.get(`${r},${c}`);
    if (el) el.classList.add("word-highlight");
  }
  const start = cells[0];
  const selector = `.clue-segment[data-row="${start.row}"][data-col="${start.col}"][data-direction="${direction}"]`;
  const segment = document.querySelector(selector);
  if (segment) segment.classList.add("hover-highlight");
}

// Vertical word on Shift or CapsLock (either one), horizontal otherwise —
// getModifierState() is part of the DOM's shared modifier-key mixin, so it
// works on a MouseEvent (mouseenter) exactly like on a KeyboardEvent, no
// separate key-tracking state needed.
function hoverDirectionFromEvent(event) {
  return event.getModifierState("Shift") || event.getModifierState("CapsLock") ? "down" : "across";
}

// Shift/CapsLock can be toggled while the mouse sits still over the same
// cell — re-evaluate the hover direction on every key change too, not just
// on mouseenter, so the highlighted word switches live rather than only on
// the next mouse movement.
document.addEventListener("keydown", updateHoverForModifierKey);
document.addEventListener("keyup", updateHoverForModifierKey);

function updateHoverForModifierKey(event) {
  if (!hoveredGridCell || (event.key !== "Shift" && event.key !== "CapsLock")) return;
  highlightWordAt(hoveredGridCell.row, hoveredGridCell.col, hoverDirectionFromEvent(event));
}

function setStatus(message, isError) {
  status.textContent = message;
  status.classList.toggle("error", Boolean(isError));
}

function isWhite(r, c) {
  return puzzle.pattern[r][c] !== BLACK;
}

function selectCell(r, c) {
  if (showSolution || !isWhite(r, c)) return;
  selected = { row: r, col: c };
  renderGrid();
}

// Scans forward from the current selection (right for across entries typed
// in lowercase, down for across entries typed in uppercase/Shift/CapsLock),
// skipping black cells, and stops at the next white cell found.
function moveSelection(direction) {
  if (!selected) return;
  const { width, height } = puzzle;
  let { row, col } = selected;
  while (true) {
    if (direction === "right") col += 1;
    else row += 1;
    if (row >= height || col >= width) return;
    if (isWhite(row, col)) {
      selected = { row, col };
      renderGrid();
      return;
    }
  }
}

function handleKeydown(event) {
  if (!puzzle || !selected || showSolution) return;
  const key = event.key;

  if (key.length === 1 && /[a-zA-Z]/.test(key)) {
    event.preventDefault();
    const isUpper = key !== key.toLowerCase();
    userLetters[selected.row][selected.col] = key.toUpperCase();
    moveSelection(isUpper ? "down" : "right");
    renderGrid();
  } else if (key === "Backspace" || key === "Delete") {
    event.preventDefault();
    userLetters[selected.row][selected.col] = "";
    renderGrid();
  }
}

function renderGrid() {
  const { width, height, pattern, solution, words } = puzzle;
  const numberByCell = new Map();
  for (const w of words) {
    numberByCell.set(`${w.row},${w.col}`, w.number);
  }

  // One extra row/column for the row/column index headers (1-based) — see
  // the style-guide SKILL. The header row/column share the same #grid CSS
  // grid as the puzzle cells rather than a separate layout, so everything
  // stays aligned automatically.
  gridEl.style.gridTemplateColumns = `repeat(${width + 1}, 2rem)`;
  gridEl.innerHTML = "";
  // The grid is fully rebuilt below, so every previous cell element (and
  // any hover state referring to it) is about to become stale.
  cellElements = new Map();
  hoveredGridCell = null;

  const corner = document.createElement("div");
  corner.className = "cell header-cell";
  gridEl.appendChild(corner);
  for (let c = 0; c < width; c++) {
    const colHeader = document.createElement("div");
    colHeader.className = "cell header-cell";
    colHeader.textContent = c + 1;
    gridEl.appendChild(colHeader);
  }

  for (let r = 0; r < height; r++) {
    const rowHeader = document.createElement("div");
    rowHeader.className = "cell header-cell";
    rowHeader.textContent = r + 1;
    gridEl.appendChild(rowHeader);

    for (let c = 0; c < width; c++) {
      const cell = document.createElement("div");
      if (pattern[r][c] === BLACK) {
        cell.className = "cell black";
        gridEl.appendChild(cell);
        continue;
      }

      cell.className = "cell white";
      const number = numberByCell.get(`${r},${c}`);
      if (number) {
        const label = document.createElement("span");
        label.className = "cell-number";
        label.textContent = number;
        cell.appendChild(label);
      }

      const letter = showSolution ? solution[r][c] : userLetters[r][c];
      cell.appendChild(document.createTextNode(letter || ""));

      if (!showSolution && checking && userLetters[r][c]) {
        cell.classList.add(userLetters[r][c] === solution[r][c] ? "correct" : "incorrect");
      }
      if (!showSolution && selected && selected.row === r && selected.col === c) {
        cell.classList.add("selected");
      }
      if (!showSolution) {
        cell.addEventListener("click", () => selectCell(r, c));
      }
      // Hover word highlighting works the same in every mode (typing,
      // solution shown, checking) — it's a passive reading aid, not tied
      // to the click-to-select input flow above.
      cellElements.set(`${r},${c}`, cell);
      cell.addEventListener("mouseenter", (event) => {
        hoveredGridCell = { row: r, col: c };
        highlightWordAt(r, c, hoverDirectionFromEvent(event));
      });
      cell.addEventListener("mouseleave", () => {
        hoveredGridCell = null;
        clearHighlights();
      });
      gridEl.appendChild(cell);
    }
  }
}

// Format habituel des mots croisés : les définitions horizontales sont
// groupées ligne de grille par ligne de grille, les verticales colonne par
// colonne (pas par numéro de case) ; plusieurs définitions d'une même
// ligne/colonne sont enchaînées sur le même texte, sans retour à la ligne.
function renderClueLines(container, words, direction, positionKey) {
  container.innerHTML = "";
  const byPosition = new Map();
  for (const w of words) {
    if (w.direction !== direction) continue;
    const pos = w[positionKey];
    if (!byPosition.has(pos)) byPosition.set(pos, []);
    byPosition.get(pos).push(w);
  }

  const secondaryKey = positionKey === "row" ? "col" : "row";
  for (const pos of [...byPosition.keys()].sort((a, b) => a - b)) {
    const entries = byPosition.get(pos).sort((a, b) => a[secondaryKey] - b[secondaryKey]);
    const line = document.createElement("p");
    line.className = "clue-line";
    // Bold row/column number prefix — matches the grid's own row/column
    // index headers (renderGrid()), so a definition line can be matched
    // back to a specific row (across) or column (down) on the grid.
    const positionLabel = document.createElement("strong");
    positionLabel.className = "clue-line-position";
    positionLabel.textContent = String(pos + 1);
    line.appendChild(positionLabel);
    // Never fall back to the bare answer when a clue is missing — that
    // would show the word defining itself, exactly the "copy" bug the
    // backend's own filtering works hard to prevent (see the
    // project-best-practices SKILL). An honest placeholder instead.
    const noDefinition = I18N[uiLanguage].noDefinition;
    line.appendChild(document.createTextNode(" "));
    // Each word gets its own hoverable span (not just plain text joined
    // by " — ") so hovering *this* word's definition highlights only its
    // own cells in the grid, not every word chained onto the same line.
    entries.forEach((w, i) => {
      if (i > 0) line.appendChild(document.createTextNode(" — "));
      const segment = document.createElement("span");
      segment.className = "clue-segment";
      segment.dataset.row = w.row;
      segment.dataset.col = w.col;
      segment.dataset.direction = w.direction;
      segment.textContent = `(${w.number}) ${w.clue || noDefinition}`;
      segment.addEventListener("mouseenter", () => highlightWordAt(w.row, w.col, w.direction));
      segment.addEventListener("mouseleave", clearHighlights);
      line.appendChild(segment);
    });
    container.appendChild(line);
  }
}

function renderClues(words) {
  renderClueLines(cluesAcross, words, "across", "row");
  renderClueLines(cluesDown, words, "down", "col");
}

function toggleSolution() {
  showSolution = !showSolution;
  if (showSolution) checking = false;
  selected = null;
  solutionBtn.classList.toggle("active", showSolution);
  checkBtn.classList.toggle("active", checking);
  renderGrid();
}

function toggleChecking() {
  checking = !checking;
  if (checking) showSolution = false;
  solutionBtn.classList.toggle("active", showSolution);
  checkBtn.classList.toggle("active", checking);
  renderGrid();
}

solutionBtn.addEventListener("click", toggleSolution);
checkBtn.addEventListener("click", toggleChecking);
document.addEventListener("keydown", handleKeydown);

languageSelect.addEventListener("change", () => {
  uiLanguage = languageSelect.value;
  applyTranslations();
});

applyTranslations();

const POLL_INTERVAL_MS = 700;

function sleep(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

// Turns one backend progress step ({code, ...data} — see
// backend/crossword_gen.py's on_progress calls) into a localized status
// message. Falls back to the generic "generating" message for any step
// this version of the frontend doesn't know how to describe yet, so an
// older UI never breaks against a newer backend.
function describeStep(t, step) {
  if (!step) return t.statusGenerating;
  switch (step.code) {
    case "starting":
      return t.statusStarting;
    case "pattern":
      return t.statusPattern(step.attempt, step.attempts);
    case "pattern_found":
      return t.statusPatternFound(step.attempt);
    case "minimizing":
      return t.statusMinimizing;
    case "grid_ready":
      return t.statusGridReady(step.word_count);
    case "clues":
      return t.statusClues(step.current, step.total);
    case "saving":
      return t.statusSaving;
    default:
      return t.statusGenerating;
  }
}

// Turns a backend error code (backend/app.py's job["error_code"], or
// frontend/server.py's {"code": ...} proxy error) into a localized error
// message. Falls back to whatever raw text the backend sent (itself
// falling back to the generic "unknown error" message) for a code this
// version of the frontend doesn't recognize, so an older UI degrades to
// plain text instead of breaking against a newer backend.
function describeErrorCode(t, code, fallbackText) {
  switch (code) {
    case "no_fillable_grid":
      return t.errorNoFillableGrid;
    case "clue_generation_failed":
      return t.errorClueGenerationFailed;
    case "internal_error":
      return t.errorInternal;
    case "backend_unavailable":
      return t.errorBackendUnavailable;
    default:
      return fallbackText || t.statusErrorFallback;
  }
}

// Generation runs as a background job on the backend (grid + clue
// generation together can take anywhere from a few seconds to a few
// minutes) — this polls its status until it's done, updating the on-screen
// status message with each step along the way, and returns the finished
// grid.
async function pollJob(jobId, t) {
  while (true) {
    const response = await fetch(`/api/generate/status/${jobId}`);
    const data = await response.json();
    if (!response.ok) {
      throw new Error(describeErrorCode(t, data.detail && data.detail.code, data.detail));
    }
    if (data.status === "error") {
      throw new Error(describeErrorCode(t, data.error_code, data.error));
    }
    if (data.status === "done") {
      return data.result;
    }
    setStatus(describeStep(t, data.step), false);
    await sleep(POLL_INTERVAL_MS);
  }
}

form.addEventListener("submit", async (event) => {
  event.preventDefault();

  const language = languageSelect.value;
  const width = Number(document.getElementById("width").value);
  const height = Number(document.getElementById("height").value);
  const difficulty = document.getElementById("difficulty").value;
  const t = I18N[uiLanguage];

  button.disabled = true;
  result.hidden = true;
  solutionBtn.hidden = true;
  checkBtn.hidden = true;
  setStatus(t.statusGenerating, false);

  try {
    const response = await fetch("/api/generate", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ language, width, height, difficulty }),
    });

    const data = await response.json();

    if (!response.ok) {
      throw new Error(describeErrorCode(t, data.detail && data.detail.code, data.detail));
    }

    const gridData = await pollJob(data.job_id, t);

    puzzle = gridData;
    userLetters = Array.from({ length: gridData.height }, () => Array(gridData.width).fill(""));
    selected = null;
    showSolution = false;
    checking = false;
    solutionBtn.classList.remove("active");
    checkBtn.classList.remove("active");

    renderGrid();
    renderClues(gridData.words);
    stats.textContent = t.stats(gridData.word_count, gridData.black_count, (gridData.black_ratio * 100).toFixed(1));
    result.hidden = false;
    solutionBtn.hidden = false;
    checkBtn.hidden = false;
    setStatus(t.statusGenerated, false);
  } catch (err) {
    setStatus(err.message, true);
  } finally {
    button.disabled = false;
  }
});
