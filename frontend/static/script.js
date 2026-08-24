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

fetch("/api/version")
  .then((r) => r.json())
  .then((data) => {
    if (data.version) {
      versionBadge.textContent = `v${data.version}`;
      versionBadge.hidden = false;
    }
  })
  .catch(() => {});

// Current puzzle state.
let puzzle = null; // { width, height, pattern, solution, words }
let userLetters = []; // [row][col] -> letter typed by the player, or ""
let selected = null; // { row, col } or null
let showSolution = false;
let checking = false;

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
    line.appendChild(document.createTextNode(
      " " + entries.map((w) => `(${w.number}) ${w.clue || noDefinition}`).join(" — ")
    ));
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
