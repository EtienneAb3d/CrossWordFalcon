const BLACK = "#";

// UI translations. Only the interface chrome is translated here — the
// generated crossword content (words/clues) is translated by the backend,
// driven by the same `language` value (see the form submit handler).
const I18N = {
  fr: {
    pageTitle: "CrossWordFalcon — générateur de grilles de mots croisés",
    languageLabel: "Langue",
    widthLabel: "Largeur",
    heightLabel: "Hauteur",
    difficultyLabel: "Difficulté",
    difficultyEasy: "Facile",
    difficultyMedium: "Moyenne",
    difficultyHard: "Difficile",
    generateBtn: "Générer la grille",
    solutionBtn: "Solution",
    checkBtn: "Vérification",
    acrossHeading: "Horizontalement",
    downHeading: "Verticalement",
    statusGenerating: "Génération en cours…",
    statusGenerated: "Grille générée.",
    statusErrorFallback: "Erreur inconnue.",
    stats: (words, black, ratio) => `${words} mots placés — ${black} cases noires (${ratio}%)`,
  },
  en: {
    pageTitle: "CrossWordFalcon — crossword grid generator",
    languageLabel: "Language",
    widthLabel: "Width",
    heightLabel: "Height",
    difficultyLabel: "Difficulty",
    difficultyEasy: "Easy",
    difficultyMedium: "Medium",
    difficultyHard: "Hard",
    generateBtn: "Generate grid",
    solutionBtn: "Solution",
    checkBtn: "Check",
    acrossHeading: "Across",
    downHeading: "Down",
    statusGenerating: "Generating…",
    statusGenerated: "Grid generated.",
    statusErrorFallback: "Unknown error.",
    stats: (words, black, ratio) => `${words} words placed — ${black} black squares (${ratio}%)`,
  },
  de: {
    pageTitle: "CrossWordFalcon — Kreuzworträtsel-Generator",
    languageLabel: "Sprache",
    widthLabel: "Breite",
    heightLabel: "Höhe",
    difficultyLabel: "Schwierigkeit",
    difficultyEasy: "Leicht",
    difficultyMedium: "Mittel",
    difficultyHard: "Schwer",
    generateBtn: "Gitter erzeugen",
    solutionBtn: "Lösung",
    checkBtn: "Prüfen",
    acrossHeading: "Waagerecht",
    downHeading: "Senkrecht",
    statusGenerating: "Erzeugung läuft…",
    statusGenerated: "Gitter erzeugt.",
    statusErrorFallback: "Unbekannter Fehler.",
    stats: (words, black, ratio) => `${words} Wörter platziert — ${black} schwarze Felder (${ratio}%)`,
  },
  es: {
    pageTitle: "CrossWordFalcon — generador de crucigramas",
    languageLabel: "Idioma",
    widthLabel: "Ancho",
    heightLabel: "Alto",
    difficultyLabel: "Dificultad",
    difficultyEasy: "Fácil",
    difficultyMedium: "Media",
    difficultyHard: "Difícil",
    generateBtn: "Generar crucigrama",
    solutionBtn: "Solución",
    checkBtn: "Comprobar",
    acrossHeading: "Horizontales",
    downHeading: "Verticales",
    statusGenerating: "Generando…",
    statusGenerated: "Crucigrama generado.",
    statusErrorFallback: "Error desconocido.",
    stats: (words, black, ratio) => `${words} palabras colocadas — ${black} casillas negras (${ratio}%)`,
  },
  it: {
    pageTitle: "CrossWordFalcon — generatore di cruciverba",
    languageLabel: "Lingua",
    widthLabel: "Larghezza",
    heightLabel: "Altezza",
    difficultyLabel: "Difficoltà",
    difficultyEasy: "Facile",
    difficultyMedium: "Media",
    difficultyHard: "Difficile",
    generateBtn: "Genera griglia",
    solutionBtn: "Soluzione",
    checkBtn: "Verifica",
    acrossHeading: "Orizzontali",
    downHeading: "Verticali",
    statusGenerating: "Generazione in corso…",
    statusGenerated: "Griglia generata.",
    statusErrorFallback: "Errore sconosciuto.",
    stats: (words, black, ratio) => `${words} parole inserite — ${black} caselle nere (${ratio}%)`,
  },
};

let uiLanguage = "fr";

function applyTranslations() {
  const t = I18N[uiLanguage];
  document.documentElement.lang = uiLanguage;
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

  gridEl.style.gridTemplateColumns = `repeat(${width}, 2rem)`;
  gridEl.innerHTML = "";
  for (let r = 0; r < height; r++) {
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
    line.textContent = entries
      .map((w) => `${w.number}. ${w.clue || w.answer}`)
      .join(" — ");
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
      throw new Error(data.detail || t.statusErrorFallback);
    }

    puzzle = data;
    userLetters = Array.from({ length: data.height }, () => Array(data.width).fill(""));
    selected = null;
    showSolution = false;
    checking = false;
    solutionBtn.classList.remove("active");
    checkBtn.classList.remove("active");

    renderGrid();
    renderClues(data.words);
    stats.textContent = t.stats(data.word_count, data.black_count, (data.black_ratio * 100).toFixed(1));
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
