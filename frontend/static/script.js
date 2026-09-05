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
  document.querySelectorAll("[data-i18n-placeholder]").forEach((el) => {
    const key = el.getAttribute("data-i18n-placeholder");
    if (t[key]) el.setAttribute("placeholder", t[key]);
  });
  renderSystemInfoTooltip();
  // Only redraws the idle-state placeholder, never a live hover
  // definition (that's puzzle content, in the grid's own language, not
  // interface chrome — see highlightWordAt()/clearHighlights() below).
  if (!hoveredGridCell) renderHoverDefinitionPlaceholder();
  renderRssList();
}

// Idle state for #hover-definition (see the style-guide SKILL) — shown
// initially, and restored by clearHighlights() whenever the mouse leaves
// a hoverable word. Kept as its own function, called from applyTranslations()
// too, so a UI language change re-translates it live (mirrors
// renderSystemInfoTooltip()'s "fetch/compute once, redraw per language"
// pattern) without touching a definition currently being shown.
function renderHoverDefinitionPlaceholder() {
  hoverDefinition.textContent = I18N[uiLanguage].hoverDefinitionPlaceholder;
  hoverDefinition.classList.add("placeholder");
}

const form = document.getElementById("generate-form");
const languageSelect = document.getElementById("language");
const button = document.getElementById("generate-btn");
const status = document.getElementById("status");
const result = document.getElementById("result");
const stats = document.getElementById("stats");
const generationTimes = document.getElementById("generation-times-text");
const generationTimesPrevBtn = document.getElementById("generation-times-prev-btn");
const generationTimesNextBtn = document.getElementById("generation-times-next-btn");
const generationTimesPosition = document.getElementById("generation-times-position");
const gridEl = document.getElementById("grid");
const hoverDefinition = document.getElementById("hover-definition");
const cluesAcross = document.getElementById("clues-across");
const cluesDown = document.getElementById("clues-down");
const solutionBtn = document.getElementById("solution-btn");
const checkBtn = document.getElementById("check-btn");
const definitionsBtn = document.getElementById("definitions-btn");
const stopBtn = document.getElementById("stop-btn");
const continueBtn = document.getElementById("continue-btn");
const cluesEl = document.getElementById("clues");
const downCluesSection = document.getElementById("down-clues-section");
const versionBadge = document.getElementById("version-badge");
const infoBadge = document.getElementById("info-badge");
const infoTooltip = document.getElementById("info-tooltip");
const attemptPreview = document.getElementById("attempt-preview");
const attemptPreviewGrids = document.getElementById("attempt-preview-grids");
const attemptPreviewRevealBtn = document.getElementById("attempt-preview-reveal-btn");
const attemptPreviewFirstBtn = document.getElementById("attempt-preview-first-btn");
const attemptPreviewPrevBtn = document.getElementById("attempt-preview-prev-btn");
const attemptPreviewNextBtn = document.getElementById("attempt-preview-next-btn");
const attemptPreviewLastBtn = document.getElementById("attempt-preview-last-btn");
const attemptPreviewPosition = document.getElementById("attempt-preview-position");
const attemptPreviewStatus = document.getElementById("attempt-preview-status");
const wordVerificationWrap = document.getElementById("word-verification-wrap");
const wordVerificationTbody = document.getElementById("word-verification-tbody");
const gridTitleEl = document.getElementById("grid-title");
const libraryBtn = document.getElementById("library-btn");
const libraryPanel = document.getElementById("library");
const libraryCloseBtn = document.getElementById("library-close-btn");
const libraryTbody = document.getElementById("library-tbody");
const libraryEmpty = document.getElementById("library-empty");
const libraryPagination = document.getElementById("library-pagination");
const libraryPrevBtn = document.getElementById("library-prev-btn");
const libraryNextBtn = document.getElementById("library-next-btn");
const libraryPosition = document.getElementById("library-position");
const rssPanel = document.getElementById("rss-panel");
const virtualKeyboardEl = document.getElementById("virtual-keyboard");
const virtualKeyboardToggleBtn = document.getElementById("virtual-keyboard-toggle-btn");
const virtualKeyboardRows = document.getElementById("virtual-keyboard-rows");
const virtualKeyboardAcrossBtn = document.getElementById("virtual-keyboard-across-btn");
const virtualKeyboardDownBtn = document.getElementById("virtual-keyboard-down-btn");
const rssLanguageFilter = document.getElementById("rss-language-filter");
const rssList = document.getElementById("rss-list");
const rssDetail = document.getElementById("rss-detail");
const rssDetailCloseBtn = document.getElementById("rss-detail-close-btn");
const rssDetailTitle = document.getElementById("rss-detail-title");
const rssDetailMeta = document.getElementById("rss-detail-meta");
const rssDetailContent = document.getElementById("rss-detail-content");
const chatbotEl = document.getElementById("chatbot");
const chatbotToggleBtn = document.getElementById("chatbot-toggle-btn");
const chatbotMessages = document.getElementById("chatbot-messages");
const chatbotForm = document.getElementById("chatbot-form");
const chatbotInput = document.getElementById("chatbot-input");
const widthInput = document.getElementById("width");
const heightInput = document.getElementById("height");
const blackEnrichmentInput = document.getElementById("black-enrichment");

// "Taux noir" is a free-text integer field (0-100), initialized to a
// fixed 14% default (see its `value` in index.html) — no client-side
// auto-fill formula, unlike an earlier version tied to grid size.
// Removed once by mistake alongside an unrelated per-cycle single-cell
// lock (`_lock_one_impossible_cell` in crossword_gen.py), then restored:
// only that separate lock was ever meant to go, not this field/mechanism
// — see CLAUDE.md for the full history.

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

// Panneau "Actu Croisée" (flux RSS spécialisés mots croisés — voir
// fetch_rss_feeds.py/backend/app.py), à la demande explicite de
// l'utilisateur. Chargé une seule fois, au démarrage de la page — pas de
// rafraîchissement live pendant la session, le back lui-même ne
// rafraîchit qu'une fois par jour (voir _rss_daily_scheduler côté back).
let rssItems = [];

// Autorise un petit sous-ensemble de balises/attributs HTML issus du
// contenu brut d'un flux RSS (tiers, non maîtrisé) avant de l'insérer
// dans la page — jamais un innerHTML direct du contenu brut, à la
// demande explicite de l'utilisateur : "Assure-toi que les liens
// éventuellement indiqués soient cliquables et renvoient vers un nouvel
// onglet." Parse via le DOMParser réel du navigateur (jamais une
// approche par regex sur une chaîne HTML, trop facile à contourner) puis
// reconstruit un nouvel arbre ne contenant que des éléments/attributs
// explicitement autorisés — tout le reste (balises non listées, tout
// attribut "on*", tout attribut absent de la liste blanche de sa propre
// balise) est silencieusement abandonné plutôt que copié tel quel. Un
// <a> conserve son "href" uniquement s'il commence par http(s):/mailto:
// (jamais "javascript:" ou un schéma inconnu) et se voit systématiquement
// forcer target="_blank" rel="noopener noreferrer", qu'il l'ait déjà eu
// ou non dans le flux d'origine.
const RSS_ALLOWED_TAGS = new Set([
  "p", "br", "b", "strong", "i", "em", "u", "ul", "ol", "li", "blockquote",
  "a", "img", "div", "span", "h1", "h2", "h3", "h4", "h5", "h6",
  "table", "thead", "tbody", "tr", "td", "th", "hr", "code", "pre",
]);
const RSS_ALLOWED_ATTRS = {
  a: ["href"],
  img: ["src", "alt"],
};

function sanitizeRssHtml(rawHtml) {
  const doc = new DOMParser().parseFromString(rawHtml || "", "text/html");
  const out = document.createDocumentFragment();
  function cloneSafe(node) {
    if (node.nodeType === Node.TEXT_NODE) {
      return document.createTextNode(node.textContent);
    }
    if (node.nodeType !== Node.ELEMENT_NODE) return null;
    const tag = node.tagName.toLowerCase();
    if (!RSS_ALLOWED_TAGS.has(tag)) {
      // Balise non autorisée : on garde quand même son propre contenu
      // textuel/enfants (un <script>/<style> n'a normalement aucun enfant
      // "visible" pertinent, mais un simple <font>/<center> inconnu, lui,
      // a un contenu qui mérite de survivre) — seule la balise elle-même
      // est abandonnée, pas ce qu'elle contenait.
      const frag = document.createDocumentFragment();
      node.childNodes.forEach((child) => {
        const safe = cloneSafe(child);
        if (safe) frag.appendChild(safe);
      });
      return frag;
    }
    const el = document.createElement(tag);
    for (const attr of RSS_ALLOWED_ATTRS[tag] || []) {
      const value = node.getAttribute(attr);
      if (!value) continue;
      if (attr === "href" && !/^(https?:|mailto:)/i.test(value.trim())) continue;
      el.setAttribute(attr, value);
    }
    if (tag === "a") {
      el.setAttribute("target", "_blank");
      el.setAttribute("rel", "noopener noreferrer");
    }
    node.childNodes.forEach((child) => {
      const safe = cloneSafe(child);
      if (safe) el.appendChild(safe);
    });
    return el;
  }
  doc.body.childNodes.forEach((child) => {
    const safe = cloneSafe(child);
    if (safe) out.appendChild(safe);
  });
  return out;
}

function renderRssList() {
  const t = I18N[uiLanguage];
  rssList.replaceChildren();
  // Filtre par langue (voir #rss-language-filter), à la demande explicite
  // de l'utilisateur — "all" (Toutes les langues, en tête de liste)
  // n'exclut rien ; toute autre valeur ne garde que les articles de cette
  // langue exacte (voir fetch_rss_feeds.py's own "language" field).
  const filterLang = rssLanguageFilter.value;
  let filteredItems = filterLang === "all"
    ? rssItems
    : rssItems.filter((item) => item.language === filterLang);
  // Repli sur l'anglais si la langue choisie n'a aucun article, à la
  // demande explicite de l'utilisateur ("afficher la liste anglaise" +
  // un message l'expliquant en haut de la liste) — jamais quand la langue
  // choisie est déjà "en" ou "all" (le repli lui-même, ou déjà tout
  // montré), pour ne jamais boucler sur lui-même ni masquer un "aucun
  // article" réellement global (tout repli anglais lui-même vide).
  let showFallbackNotice = false;
  if (!filteredItems.length && filterLang !== "all" && filterLang !== "en") {
    const englishItems = rssItems.filter((item) => item.language === "en");
    if (englishItems.length) {
      filteredItems = englishItems;
      showFallbackNotice = true;
    }
  }
  if (showFallbackNotice) {
    const notice = document.createElement("p");
    notice.id = "rss-language-fallback-notice";
    notice.textContent = t.rssLanguageFallbackNotice;
    rssList.appendChild(notice);
  }
  if (!filteredItems.length) {
    const empty = document.createElement("p");
    empty.id = "rss-empty";
    empty.textContent = t.rssEmpty;
    rssList.appendChild(empty);
    return;
  }
  filteredItems.forEach((item) => {
    const index = rssItems.indexOf(item);
    const li = document.createElement("li");
    li.tabIndex = 0;
    // Petite icône à gauche indiquant l'origine de l'entrée, à la demande
    // explicite de l'utilisateur : "ajoute à gauche une petite icône
    // permettant de savoir si c'est un lien vers une page web ou une
    // infos RSS." Un simple caractère Unicode (comme #virtual-keyboard's
    // own "⌨"/"→"/"↓" ailleurs dans ce fichier), jamais une image/icône
    // externe — 🔗 pour "grid" (un clic ouvre directement la page externe
    // de la grille), 📰 pour "rss" (un clic ouvre l'aperçu d'article
    // interne) — le même repli "rss" qu'ailleurs pour un item sans
    // `kind` du tout (cache écrit avant l'ajout de ce champ).
    const isGrid = item.kind === "grid";
    const kindIcon = document.createElement("span");
    kindIcon.className = "rss-item-kind-icon";
    kindIcon.textContent = isGrid ? "🔗" : "📰";
    kindIcon.title = isGrid ? t.rssItemKindGrid : t.rssItemKindRss;
    kindIcon.setAttribute("aria-hidden", "true");
    li.appendChild(kindIcon);
    const textWrap = document.createElement("div");
    textWrap.className = "rss-item-text";
    const title = document.createElement("div");
    title.textContent = item.title;
    const source = document.createElement("div");
    source.className = "rss-item-source";
    source.textContent = item.source;
    textWrap.appendChild(title);
    textWrap.appendChild(source);
    li.appendChild(textWrap);
    // Deux origines fusionnées dans le même journal, à la demande
    // explicite de l'utilisateur ("Ajoute les entrées de SCRAPP aux
    // journal de la première page, sachant que cette fois, un clic
    // renvoie directement sur la page de la grille — pas d'article à
    // afficher sur notre site") : une entrée RSS (item.kind === "rss",
    // ou absent — repli pour un cache déjà écrit avant l'ajout de ce
    // champ) ouvre toujours l'aperçu interne existant ; une entrée
    // "grid" (fetch_grid_links.py/SCRAPP) ouvre directement l'URL
    // externe de la grille dans un nouvel onglet, sans aucun aperçu.
    // (`isGrid` déjà calculé plus haut pour l'icône.)
    const open = isGrid
      ? () => window.open(item.link, "_blank", "noopener,noreferrer")
      : () => openRssDetail(index);
    li.addEventListener("click", open);
    li.addEventListener("keydown", (event) => {
      if (event.key === "Enter" || event.key === " ") {
        event.preventDefault();
        open();
      }
    });
    rssList.appendChild(li);
  });
}

function openRssDetail(index) {
  const item = rssItems[index];
  if (!item) return;
  rssDetailTitle.textContent = item.title;
  rssDetailMeta.textContent = item.pub_date
    ? `${item.source} — ${new Date(item.pub_date).toLocaleString(uiLanguage)}`
    : item.source;
  rssDetailContent.replaceChildren(sanitizeRssHtml(item.content_html));
  rssDetail.hidden = false;
}

function closeRssDetail() {
  rssDetail.hidden = true;
}

rssDetailCloseBtn.addEventListener("click", closeRssDetail);

// Fermeture au clavier (touche Echap), à la demande explicite de
// l'utilisateur — vérifie `!rssDetail.hidden` en premier, pas de coût ni
// d'effet quand l'overlay n'est de toute façon pas ouvert.
document.addEventListener("keydown", (event) => {
  if (!rssDetail.hidden && event.key === "Escape") {
    closeRssDetail();
  }
});

// Valeur par défaut = langue courante de l'interface au chargement de la
// page, à la demande explicite de l'utilisateur ("Par défaut, la langue
// de l'interface"). Resynchronisée à chaque changement ultérieur de
// langue de l'interface aussi, désormais (voir languageSelect's own
// "change" listener plus bas) — un revirement assumé par rapport à la
// décision initiale ("jamais resynchronisée... pour ne pas écraser un
// choix du joueur"), suite à une demande explicite et plus récente de
// l'utilisateur : "Quand l'utilisateur change la langue de l'interface,
// il faut adapter la langue du fil d'actu."
rssLanguageFilter.value = uiLanguage;
rssLanguageFilter.addEventListener("change", renderRssList);

// Le panneau "Actu Croisée" (#rss-panel) disparaît dès que l'un des trois
// panneaux centraux (#library, #attempt-preview, #result) est affiché, à
// la demande explicite de l'utilisateur : "Le journal d'actu doit
// disparaître quand quelque chose d'autre doit s'afficher, par exemple
// la Bibliothèque (actuellement, la Bibliothèque s'affiche sous le
// journal)." Un revirement assumé par rapport à une demande antérieure
// de ce même projet (le panneau était devenu un frère de ces trois
// sections, jamais caché par elles, justement pour ne plus disparaître
// — voir plus haut dans ce fichier) : la préférence la plus récente de
// l'utilisateur prévaut. Appelée à chaque endroit qui bascule la
// visibilité de l'un des trois panneaux plutôt que de dupliquer cette
// logique — un seul point de vérité pour la règle "au moins un des
// trois est visible => masquer le journal".
// Vrai pendant toute la durée d'une génération de grille (voir
// runGeneration() plus bas), pas seulement une fois qu'un des trois
// panneaux centraux est effectivement visible — corrige un vrai trou
// rapporté par l'utilisateur : "Le panneau d'actu doit disparaître quand
// on doit afficher autre chose sur la partie centrale (Bibliothèque,
// génération de grille, etc)." Entre l'envoi du formulaire et le tout
// premier événement d'aperçu reçu du back, ni #library, ni #attempt-
// preview, ni #result ne sont encore visibles — sans ce drapeau,
// syncRssPanelVisibility() aurait alors, à tort, laissé réapparaître le
// journal pendant cette fenêtre.
let generationInProgress = false;

function syncRssPanelVisibility() {
  rssPanel.hidden = generationInProgress
    || !(libraryPanel.hidden && attemptPreview.hidden && result.hidden);
}
// Etat initial explicite plutôt que de compter sur une simple coïncidence
// entre l'état `hidden` par défaut de index.html et cette règle.
syncRssPanelVisibility();

// Fusionne les deux sources dans un seul journal chronologique, à la
// demande explicite de l'utilisateur — deux fetch indépendants
// (Promise.allSettled : l'échec de l'un ne doit jamais empêcher
// d'afficher ce que l'autre a bien renvoyé), rassemblés puis triés une
// seule fois par date de publication décroissante (même critère de tri
// que chaque script d'origine applique déjà côté serveur — refait ici
// car merger deux listes déjà triées ne garantit pas, à lui seul, que le
// résultat combiné le reste).
Promise.allSettled([
  fetch("/api/rss").then((r) => r.json()),
  fetch("/api/scrapp").then((r) => r.json()),
]).then(([rssResult, scrappResult]) => {
  const rss = rssResult.status === "fulfilled" ? (rssResult.value.items || []) : [];
  const scrapp = scrappResult.status === "fulfilled" ? (scrappResult.value.items || []) : [];
  rssItems = [...rss, ...scrapp].sort((a, b) => (b.pub_date || "").localeCompare(a.pub_date || ""));
  renderRssList();
});

// Current puzzle state.
let puzzle = null; // { width, height, pattern, solution, words }
let userLetters = []; // [row][col] -> letter typed by the player, or ""
let selected = null; // { row, col } or null
let showSolution = false;
let checking = false;
// Hidden by default once a grid is ready to play, at the user's explicit
// request — the hover-definition bar under the grid already gives a
// definition on demand, so the full across/down clue lists are no longer
// shown up front; #definitions-btn (below) brings them back.
let showDefinitions = false;

// Grid <-> clue-list hover highlighting. cellElements lets highlightWordAt()
// look up a cell's DOM node by position without a fresh querySelector per
// cell; both are rebuilt from scratch on every renderGrid() call, since the
// grid itself is fully re-rendered rather than patched.
let cellElements = new Map(); // "row,col" -> cell element (white cells only)
let hoveredGridCell = null; // { row, col } while the mouse is over a grid cell, else null
// The word currently framed by the hover highlight — { row, col, direction }
// of its own starting cell, matching a puzzle.words entry's own shape — or
// null when nothing is hovered. Distinct from `selected` (the clicked cell
// the player is actively typing into): the user explicitly pointed out that
// "quel est le mot sélectionné" refers to whichever word the mouse is
// currently over (hover), never the click-to-type target — David FALCON
// (see buildChatUiContext()) is told about both, under clearly separate
// labels, precisely so it doesn't conflate the two.
let hoveredWord = null;

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
  hoveredWord = null;
  renderHoverDefinitionPlaceholder();
}

// Shared by both hover directions (grid -> clue list and clue list ->
// grid, see the event listeners in renderGrid()/renderClueLines()): frames
// every cell of the word through (row, col) in `direction`, and highlights
// the matching clue-segment span by its own data-row/col/direction — set
// from the same word-start position wordCellsAt() itself resolves to
// (cells[0]), so this never needs to search puzzle.words to find "which
// word is this". Also fills #hover-definition with that same segment's
// own text (reused as-is, "(N) clue text", rather than a fresh
// puzzle.words lookup — one less place that needs the noDefinition
// fallback logic already applied once in renderClueLines()) — the
// user's explicit request for a fixed 3-line panel under the grid, so a
// player can read the currently-hovered word's definition without the
// full across/down clue lists in view at the same time.
function highlightWordAt(row, col, direction) {
  clearHighlights();
  if (!puzzle || !isWhite(row, col)) return;
  const cells = wordCellsAt(row, col, direction);
  for (const { row: r, col: c } of cells) {
    const el = cellElements.get(`${r},${c}`);
    if (el) el.classList.add("word-highlight");
  }
  const start = cells[0];
  hoveredWord = { row: start.row, col: start.col, direction };
  const selector = `.clue-segment[data-row="${start.row}"][data-col="${start.col}"][data-direction="${direction}"]`;
  const segment = document.querySelector(selector);
  if (segment) {
    segment.classList.add("hover-highlight");
    hoverDefinition.textContent = segment.textContent;
    hoverDefinition.classList.remove("placeholder");
  }
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

// Shows small, read-only snapshots of the most-filled-in state a batch of
// failed generation attempts reached before giving up (see
// backend/crossword_gen.py's try_fill, diagnostics["example_grid"]) — at
// the user's explicit request, so a slow or ultimately-failing generation
// isn't a black box: the player gets a visual sense of what was tried.
// `examples` is an array, no fixed length cap (see crossword_gen.py), of
// `{example_grid, impossible_cells, forced_cells, locked_cells}` objects —
// one per parallel attempt at the same palier when every one of them
// failed, laid out as many rows of 3 as needed (see #attempt-preview-grids
// in style.css, a plain CSS grid with no fixed row count); a single-element
// array for the "minimizing"/"clues" steps'
// one-grid preview of the actual, successful pattern. Each `example_grid`
// is a 2D array of single characters — "#" for black, "." for a white cell
// whose word wasn't yet determined, any other character for a placed
// letter — the same shape backend/crossword_gen.py already uses for
// `pattern`/`solution` on a successful grid, so no separate parsing is
// needed here. Each `impossible_cells` (diagnostics["impossible_cells"],
// possibly empty — see Filler.impossible_zone_cells()'s own docstring for
// when it can be empty) is an array of [row, col] pairs, highlighted with a
// light red background (same --incorrect-bg token already used for a wrong
// letter on the real grid, at the user's explicit request) — the cells of
// whichever slot(s) had no candidate word left at all at this snapshot.
// Each `forced_cells` (diagnostics["forced_cells"], possibly empty — see
// build_partial_letters_grid's own docstring — now *every* cell
// sample_letter_biases forced, whether or not the search later covered it
// with a real letter, after a live report that the previous "only cells
// still unconfirmed" version made the highlight nearly vanish as a
// generation progressed) is likewise an array of [row, col] pairs,
// highlighted with a thick blue inset border (--accent, see style.css's
// .cell.white.forced) at the user's explicit request — cells whose shown
// letter came from the statistical pre-fill, whether or not a real letter
// also ended up there. Each `locked_cells` (diagnostics["locked_cells"],
// possibly empty — see try_fill's own docstring) is likewise an array of
// [row, col] pairs, highlighted with a thick orange inset border (--locked,
// see style.css's .cell.white.locked/.cell.black.locked — a border rather
// than a background fill, at the user's own explicit follow-up request, so
// it never covers up .impossible's red background underneath) at the
// user's explicit request — most often cells whose shown letter was
// carried over verbatim, real and already confirmed, from a *previous*
// palier via the "reprise telle-quelle" mechanism (crossword_gen.py's
// preseed_assignment), a genuinely different mechanism from `forced_
// cells`'s statistical guess, so it gets a visually distinct color rather
// than reusing --accent — but also, for the "pre_cleanup_optimized" step
// specifically (crossword_gen.py's `_optimize_before_cleanup`), the black
// cells bordering a still-entirely-empty slot, protected from removal
// during that step's own optimization pass. `cellElementsByCoord` (below)
// registers every cell, black or white, precisely so a locked *black*
// cell can be found by this overlay too — the other three overlays
// (.forced/.low-candidates/.noise) never receive a black-cell coordinate
// from any current backend caller, but finding one harmlessly no-ops
// since their own CSS rules stay scoped to `.white`.
// Applied in a dedicated final pass per mini-grid, *after* every cell of
// that mini-grid already exists in the DOM (see the loop below) — at the
// user's own explicit follow-up request, so the overlay is unambiguously
// on top of everything already drawn (letters, black cells, .impossible)
// rather than a class applied inline while each cell is first being built,
// removing any doubt about draw order affecting whether it's visible.
//
// Letters themselves are hidden unless `showPreviewLetters` is on (see
// #attempt-preview-reveal-btn, now shown from page load right next to
// #generate-btn rather than only appearing once a preview exists — at the
// user's own explicit follow-up request, so the choice can be made
// *before* generating, not only reacted to once letters are already on
// screen). Once the "reprise telle-quelle" mechanism can carry forward a
// large, mostly-real fraction of the final grid across several paliers,
// these preview grids stopped being purely diagnostic and started risking
// spoiling the actual solution before the player ever gets to play it.
// Hiding the letters doesn't touch the .impossible/.forced/.locked
// highlight classes above — those convey *where* something happened, not
// *what* letter is there, so they stay visible regardless of the toggle.
//
// Each mini-grid also gets a small stats line above it (`.attempt-preview-
// stats`, in a new `.attempt-preview-item` wrapper alongside the grid), at
// the user's explicit request: the black-cell rate and the letter-fill
// rate, both computed against the *same* denominator (total cells in that
// example_grid) so the two percentages stay directly comparable — reading
// "62% noir, 30% rempli" also implicitly says 8% is still blank. The fill
// count is derived straight from `example_grid` (any character other than
// "." or "#"), independent of `showPreviewLetters` — it reflects the
// search's real progress at that snapshot, not whatever the toggle
// currently reveals on screen.
let lastPreviewExamples = null;

function renderAttemptPreview(examples) {
  if (!examples || !examples.length) return;
  lastPreviewExamples = examples;
  attemptPreviewGrids.innerHTML = "";
  for (const {
    example_grid: exampleGrid,
    impossible_cells: impossibleCells,
    forced_cells: forcedCells,
    locked_cells: lockedCells,
    low_candidate_cells: lowCandidateCells,
    noise_cells: noiseCells,
    process_number: processNumber,
    is_best: isBest,
  } of examples) {
    if (!exampleGrid || !exampleGrid.length) continue;
    const height = exampleGrid.length;
    const width = exampleGrid[0].length;
    const impossibleSet = new Set((impossibleCells || []).map(([r, c]) => `${r},${c}`));
    const item = document.createElement("div");
    item.className = "attempt-preview-item";
    const miniGrid = document.createElement("div");
    miniGrid.className = "attempt-preview-grid";
    // Green outline around whichever grid the backend considers the real
    // winner of this batch (backend/crossword_gen.py's own `is_best`, see
    // its own docstring on `_sort_examples_by_process`) — at the user's
    // explicit request, added specifically because the display order is
    // now always by process number rather than by score, so the winner's
    // own position in the list no longer says anything about rank on its
    // own.
    if (isBest) miniGrid.classList.add("attempt-preview-best");
    miniGrid.style.gridTemplateColumns = `repeat(${width}, 1.1rem)`;
    const cellElementsByCoord = new Map();
    // Taux affichés au-dessus de chaque grille, à la demande explicite de
    // l'utilisateur — cases noires / total, cases blanches déjà pourvues
    // d'une vraie lettre / total, et cases réputées injouables / total (le
    // même dénominateur pour les trois, afin qu'ils restent directement
    // comparables). Une case blanche non déterminée ("." dans example_grid)
    // ne compte jamais comme "remplie", que showPreviewLetters affiche sa
    // lettre ou non — ce taux reflète le vrai progrès de la recherche, pas
    // ce que le joueur voit à l'écran à cet instant. Le taux de cases
    // injouables vient directement de `impossibleSet` (déjà calculé
    // ci-dessus pour la classe .impossible) — pas un second calcul.
    let blackCount = 0;
    let filledCount = 0;
    for (let r = 0; r < height; r++) {
      for (let c = 0; c < width; c++) {
        const ch = exampleGrid[r][c];
        const cell = document.createElement("div");
        if (ch === BLACK) {
          cell.className = "cell black";
          blackCount++;
          // Enregistrée elle aussi (voir crossword_gen.py's
          // `_optimize_before_cleanup`, à la demande explicite de
          // l'utilisateur) — un `locked_cells` peut désormais désigner une
          // case noire (une case bordant un emplacement encore
          // entièrement vide, protégée du retrait pendant cette
          // optimisation), pas seulement des cases blanches comme pour
          // les autres appelants de ce mécanisme. Sans cet enregistrement,
          // la case était introuvable par la passe de recouvrement plus
          // bas (`cellElementsByCoord.get(...)` renvoyait `undefined`),
          // donc jamais mise en évidence — signalé directement par
          // l'utilisateur : "On devrait aussi voir les cases blanches et
          // noires verrouillées, or elles ne sont pas entourées."
          cellElementsByCoord.set(`${r},${c}`, cell);
        } else {
          cell.className = "cell white";
          if (ch !== ".") filledCount++;
          if (showPreviewLetters && ch !== ".") cell.textContent = ch;
          if (impossibleSet.has(`${r},${c}`)) cell.classList.add("impossible");
          cellElementsByCoord.set(`${r},${c}`, cell);
        }
        miniGrid.appendChild(cell);
      }
    }
    // Final overlay pass: every previously-built cell already sits in the
    // DOM at this point, so adding .forced/.locked/.low-candidates here
    // can never be affected by where this runs relative to the loop
    // above.
    for (const [r, c] of forcedCells || []) {
      const cell = cellElementsByCoord.get(`${r},${c}`);
      if (cell) cell.classList.add("forced");
    }
    for (const [r, c] of lockedCells || []) {
      const cell = cellElementsByCoord.get(`${r},${c}`);
      if (cell) cell.classList.add("locked");
    }
    // Only ever non-empty on the "pattern" (cycle-start) preview — see
    // backend/crossword_gen.py's _low_candidate_slot_cells, which scopes
    // this to that one event — but `|| []` here means every other event
    // (whose examples simply lack this key) renders exactly as before,
    // with no special-casing needed on this side.
    for (const [r, c] of lowCandidateCells || []) {
      const cell = cellElementsByCoord.get(`${r},${c}`);
      if (cell) cell.classList.add("low-candidates");
    }
    // Only ever non-empty on the "pattern" (cycle-start) preview too —
    // see backend/crossword_gen.py's _noise_slot_cells — same `|| []`
    // no-op convention as lowCandidateCells just above.
    for (const [r, c] of noiseCells || []) {
      const cell = cellElementsByCoord.get(`${r},${c}`);
      if (cell) cell.classList.add("noise");
    }
    const totalCells = height * width;
    const blackPercent = Math.round((100 * blackCount) / totalCells);
    const fillPercent = Math.round((100 * filledCount) / totalCells);
    const impossiblePercent = Math.round((100 * impossibleSet.size) / totalCells);
    const stats = document.createElement("p");
    stats.className = "attempt-preview-stats";
    // Préfixe en gras par le numéro du process qui a réellement produit
    // cette grille (backend/crossword_gen.py's own `process_number`, voir
    // sa propre docstring), à la demande explicite de l'utilisateur :
    // "permet de suivre une grille qui change de place d'un cycle à
    // l'autre." Absent (`null`/`undefined`) pour la seule prévisualisation
    // qui n'a jamais de vrai process derrière elle (le tout premier palier
    // d'une génération, avant toute soumission réelle) — dans ce cas, pas
    // de préfixe du tout plutôt qu'un numéro inventé.
    if (processNumber != null) {
      const processLabel = document.createElement("strong");
      processLabel.className = "attempt-preview-process";
      processLabel.textContent = I18N[uiLanguage].attemptPreviewProcessLabel(processNumber);
      stats.appendChild(processLabel);
      stats.appendChild(document.createTextNode(" "));
    }
    stats.appendChild(
      document.createTextNode(I18N[uiLanguage].attemptPreviewStats(blackPercent, fillPercent, impossiblePercent))
    );
    item.appendChild(stats);
    item.appendChild(miniGrid);
    attemptPreviewGrids.appendChild(item);
  }
  attemptPreview.hidden = false;
  syncRssPanelVisibility();
}

// Bi-stable toggle (same pattern as solutionBtn/checkBtn below), at the
// user's explicit request — default off, and re-renders whichever batch
// of examples is currently on screen so the effect is immediate, without
// waiting for the next poll to bring in new data. Unlike solutionBtn/
// checkBtn, this one is never reset between generations (see
// hideAttemptPreview() below): it's meant as a standing preference the
// player sets once, decided *before* a generation even starts, not a
// per-generation state that should snap back to hidden every time.
//
// Also gates the word-verification table (renderWordTable(), further
// below) at the user's explicit request — the button was renamed from
// "Lettres" to "Voir" to reflect this broader "reveal secondary detail"
// role, rather than "letters" specifically.
let showPreviewLetters = false;

function togglePreviewLetters() {
  showPreviewLetters = !showPreviewLetters;
  attemptPreviewRevealBtn.classList.toggle("active", showPreviewLetters);
  if (lastPreviewExamples) renderAttemptPreview(lastPreviewExamples);
  renderWordTable(lastWordTable);
}

attemptPreviewRevealBtn.addEventListener("click", togglePreviewLetters);

// Full history of every attempt-preview state examples_history has ever
// produced during the current generation — one element per entry
// pollJob() has recorded, appended to as they arrive (in full batches,
// never paced or skipped — see pollJob's own comment), never shortened.
// Lets the player step back to an earlier state and forward again with
// the prev/next buttons next to #attempt-preview-label, at the user's
// explicit request — independent of the *live* display, which only ever
// reflects whatever is most current. Each element is `{step, examples}`
// (backend/app.py), not a bare examples array — at the user's own
// explicit follow-up request, "L'historique des visualisation doit
// inclure le status (indiquant notamment le nombre de cycles)", so a
// shown grid can always be paired with which cycle/attempt it actually
// came from, not just the grid on its own.
let previewHistory = [];
// Index into previewHistory currently shown on screen. Stays pinned to the
// newest entry (auto-follow) as long as the player hasn't navigated back
// manually — pollJob() advances it one step at a time via showNextPreview()
// as it records new entries (see autoFollowPreview just below); showPrevious
// Preview()/showNextPreview() also move it explicitly on a manual click.
let previewHistoryIndex = -1;
// Whether the live view should keep stepping forward through
// `previewHistory` on its own, one entry per poll, as pollJob() records
// new ones — true by default (and reset on every new generation, see
// hideAttemptPreview()). Set to `false` the moment the player clicks "◀"
// to look back at an earlier entry (showPreviousPreview()), so a poll
// landing in between doesn't yank their view forward again while they're
// reviewing something — the same "pause autoscroll while scrolled up"
// courtesy a chat/log viewer gives. Resumed by manually clicking "▶"
// (see its own click listener below) — clicking "forward" is read as "I
// want to keep following again from here."
let autoFollowPreview = true;
// The `step` half of whichever previewHistory entry is currently on
// screen (or null) — kept separately from `lastPreviewExamples` (the
// `examples` half, used by togglePreviewLetters()'s own re-render) purely
// so a UI language change can re-translate the status line the same way
// applyTranslations()'s own caller already re-renders the grids (see the
// languageSelect "change" handler further below).
let lastPreviewStep = null;

function updatePreviewNavButtons() {
  const atStart = previewHistoryIndex <= 0;
  const atEnd = previewHistoryIndex >= previewHistory.length - 1;
  attemptPreviewFirstBtn.disabled = atStart;
  attemptPreviewPrevBtn.disabled = atStart;
  attemptPreviewNextBtn.disabled = atEnd;
  attemptPreviewLastBtn.disabled = atEnd;
  // Same enabled/disabled state, mirrored onto the pair of nav buttons at
  // the end of #generation-times (see hideAttemptPreviewPanel()) — a
  // single source of truth for "where are we in previewHistory" driving
  // both sets of buttons, so they can never drift out of sync with each
  // other regardless of which one the player actually clicks.
  generationTimesPrevBtn.disabled = atStart;
  generationTimesNextBtn.disabled = atEnd;
  renderPreviewPosition();
}

// "Étape 5/13" next to the ◀/▶ buttons, at the user's explicit request —
// `previewHistory.length` (the denominator) grows the moment pollJob()
// records a new entry, even while the player is paused/behind reviewing
// an earlier one (recordPreviewHistory() already calls updatePreviewNav
// Buttons() unconditionally on every new batch) — so the readout itself
// is what makes "the Front keeps silently accumulating new states while
// paused" visible to the player, not just an internal implementation
// detail. Called from updatePreviewNavButtons() itself so every existing
// call site (recording, both jump buttons, both step buttons, the reveal
// timer's own auto-advance, hideAttemptPreview()) keeps it in sync for
// free, and again directly from the language-change handler further
// below (a language switch touches no index, so updatePreviewNavButtons()
// itself isn't otherwise re-triggered).
function renderPreviewPosition() {
  const text = previewHistoryIndex >= 0 && previewHistory.length
    ? I18N[uiLanguage].attemptPreviewPosition(previewHistoryIndex + 1, previewHistory.length)
    : "";
  attemptPreviewPosition.textContent = text;
  // Same text, on the copy of this readout next to #generation-times' own
  // nav buttons (see updatePreviewNavButtons()).
  generationTimesPosition.textContent = text;
}

// Redraws #attempt-preview-status from whatever lastPreviewStep currently
// holds, in the current UI language — describeStep() (defined further
// below, but a plain function declaration, so it's hoisted and callable
// here) already turns any known step code, including "pattern" (a cycle's
// own starting state, see crossword_gen.py's _cycle_start_preview) and
// "pattern_attempt_failed"/"pattern_found" (its end state), into the
// right localized "Tentative X/Y..." text — the exact same status a
// player would otherwise only ever see drain through the live #status
// line one poll at a time, now paired with whichever preview grid is on
// screen.
function renderPreviewStatus() {
  attemptPreviewStatus.textContent = lastPreviewStep ? describeStep(I18N[uiLanguage], lastPreviewStep) : "";
}

// Diagnostic table shown right below the final, already-minimized grid's
// own preview (the "clues" previewHistory entry — see backend/app.py,
// _build_word_verification_table), at the user's explicit request: one
// row per grid word, sorted in reading order (top-to-bottom, then
// left-to-right — the same order the backend already sorts by, this is
// purely a display pass-through, no re-sorting done here). Column 1 is
// the word's own H/V direction ("across"/"down" — literal "H"/"V", not
// translated per UI language, at the user's explicit request) followed
// by its (y, x) starting coordinate (1-based, row then column — the
// opposite order from a mathematical (x, y) pair, matching #grid's own
// row/column headers). Column 2 checks the word really exists as a
// MOT entry in data/wordlist_<lang>_full.tsv — the exact dictionary the
// solver drew from — showing the entry's *entire, verbatim TSV line*
// (`row.wordlist_line`, MOT/ACCENTUE/FREQUENCE/CANONIQUE together, exactly
// as written in the file — not just the word's own accented spelling)
// when it does, or the bare grid answer in red (`.word-missing`) when it
// doesn't: the one directly visible symptom of the rare "invented word"
// edge case documented in CLAUDE.md (a slot completed purely via crossing
// assignments, never validated against the real dictionary). Column 3
// shows the *entire, verbatim JSON Lines entry* (`row.gloss_lines`, one
// per matching canonical form, at the user's explicit request for "la
// ligne complète des fichiers de référence" rather than just the matched
// lemma) for each of the word's candidate canonical form(s) that has a
// real entry in data/gloss_dictionary/<lang>_glosses.jsonl (an em dash
// when none do). Both raw-line columns use `.raw-line` (CSS `white-space:
// pre-wrap`) so a long TSV/JSON line wraps onto several visual lines
// instead of forcing the whole table to scroll arbitrarily wide.
// `table` is only ever present on the one previewHistory entry backend/
// app.py builds it for — `undefined`/empty on every other entry, so the
// table naturally disappears while navigating to an earlier/later step.
//
// Only actually shown while `showPreviewLetters` ("Voir", see
// togglePreviewLetters()) is on, at the user's explicit request — kept in
// `lastWordTable` regardless of that flag (mirroring `lastPreviewExamples`)
// so toggling the button re-renders instantly, from whatever the current
// previewHistory entry already holds, with no need to re-navigate.
let lastWordTable = null;

function renderWordTable(table) {
  lastWordTable = table;
  wordVerificationTbody.innerHTML = "";
  if (!table || !table.length || !showPreviewLetters) {
    wordVerificationWrap.hidden = true;
    return;
  }
  for (const row of table) {
    const tr = document.createElement("tr");

    const posTd = document.createElement("td");
    const directionPrefix = row.direction === "across" ? "H" : "V";
    posTd.textContent = `${directionPrefix} (${row.row + 1}, ${row.col + 1})`;
    tr.appendChild(posTd);

    const wordTd = document.createElement("td");
    wordTd.classList.add("raw-line");
    wordTd.textContent = row.in_wordlist ? row.wordlist_line : row.answer;
    if (!row.in_wordlist) wordTd.classList.add("word-missing");
    tr.appendChild(wordTd);

    const rootTd = document.createElement("td");
    rootTd.classList.add("raw-line");
    rootTd.textContent = (row.gloss_lines && row.gloss_lines.length)
      ? row.gloss_lines.join("\n")
      : "—";
    tr.appendChild(rootTd);

    wordVerificationTbody.appendChild(tr);
  }
  wordVerificationWrap.hidden = false;
}

// Displays one previewHistory entry: its grids (via renderAttemptPreview,
// unchanged), its paired status text, and its word-verification table (if
// any) together — the one place every part of an entry actually reaches
// the screen, used by every path that shows a previewHistory entry
// (auto-follow, and both nav buttons) so they can never drift out of sync
// with each other.
function showPreviewEntry(entry) {
  renderAttemptPreview(entry.examples);
  lastPreviewStep = entry.step || null;
  renderPreviewStatus();
  renderWordTable(entry.word_table);
}

// Called from pollJob() with every new attempt-preview state examples_
// history produced since the last poll, however many that is. Purely a
// recording step — every one of `newEntries` is unconditionally pushed
// into `previewHistory` and the nav buttons' enabled state is refreshed,
// but nothing is rendered here. What (if anything) gets shown live this
// poll is decided by pollJob()'s own caller, right after this returns —
// see its own comment for why "just render whatever's most recent",
// tried first, doesn't work for this specific backend.
//
// This used to also render live (the last of `newEntries`, only when the
// player was already following along) — reverted at the user's explicit
// report: "je ne vois plus qu'une seule grille, jamais plus" / "le
// stream des états en Live ne montre que les fins de cycles [en fait,
// les débuts] ; il ne stream pas les phases intermédiaires." Root cause,
// confirmed live by simulating this exact poll loop against a real job's
// raw `examples_history`: `generate_grid()`'s own palier loop
// (backend/crossword_gen.py) runs almost entirely inside one blocking
// worker thread (`asyncio.to_thread`) with no `await` point of its own —
// the *only* moment that thread ever actually blocks (and so the only
// moment the event loop thread can get scheduled to serve an HTTP poll)
// is while waiting on the `ProcessPoolExecutor` results for a palier's
// own CSP search. `progress("pattern_generated", ...)` (up to 6 grids)
// and `progress("pattern_attempt_failed", ...)` (up to 6 grids) both fire
// immediately once those results come back, followed *immediately* — no
// blocking point in between — by `progress("pattern", ...)` (1 grid) for
// the *next* palier, right before that thread blocks again waiting on the
// next batch of results. So whenever an HTTP poll actually gets to run,
// `examples_history`'s own newest entry is — deterministically, not just
// by chance — almost always that next palier's own "pattern" event: a
// single grid, the state carried forward from the previous cycle, not
// the richer up-to-6-grid state a completed search just produced.
// Rendering only ever "whatever's most recent" therefore meant the live
// view got stuck cycling through single-grid "pattern" snapshots, never
// the 6-grid ones — confirmed directly: 21 new entries recorded between
// two consecutive 2-second polls of a real job, every one of them ending
// in a "pattern" event.
function recordPreviewHistory(newEntries) {
  if (!newEntries.length) return;
  for (const entry of newEntries) previewHistory.push(entry);
  updatePreviewNavButtons();
}

// Jumps the live view straight to the newest recorded entry, with no
// pacing — used once a job reaches a terminal status (done/error/
// cancelled), so the final, true end state is shown immediately rather
// than however far behind the paced one-per-poll reveal below happened
// to still be (no more polls are coming to keep draining it forward).
function catchUpPreviewToEnd() {
  autoFollowPreview = true;
  if (previewHistory.length === 0) return;
  if (previewHistoryIndex === previewHistory.length - 1) return;
  previewHistoryIndex = previewHistory.length - 1;
  showPreviewEntry(previewHistory[previewHistoryIndex]);
  updatePreviewNavButtons();
}

function showPreviousPreview() {
  if (previewHistoryIndex <= 0) return;
  previewHistoryIndex--;
  showPreviewEntry(previewHistory[previewHistoryIndex]);
  autoFollowPreview = false;
  updatePreviewNavButtons();
}

function showNextPreview() {
  if (previewHistoryIndex >= previewHistory.length - 1) return;
  previewHistoryIndex++;
  showPreviewEntry(previewHistory[previewHistoryIndex]);
  updatePreviewNavButtons();
}

// Jumps straight to the very first recorded entry in one click, at the
// user's explicit request — same "pause auto-follow" treatment as a
// single "◀" click (showPreviousPreview()), since this also moves away
// from the live edge.
function showFirstPreview() {
  if (previewHistory.length === 0 || previewHistoryIndex <= 0) return;
  previewHistoryIndex = 0;
  showPreviewEntry(previewHistory[previewHistoryIndex]);
  autoFollowPreview = false;
  updatePreviewNavButtons();
}

attemptPreviewFirstBtn.addEventListener("click", showFirstPreview);
attemptPreviewPrevBtn.addEventListener("click", showPreviousPreview);
attemptPreviewNextBtn.addEventListener("click", () => {
  showNextPreview();
  // Resume auto-follow only once this click actually lands on the true
  // last recorded entry — at the user's explicit request: "L'avancement
  // automatique ne doit reprendre que quand l'utilisateur arrive à la
  // dernière grille disponible." A single "▶" while several steps still
  // remain ahead (a real, common case once the Back has gotten well
  // ahead of the Front — see recordPreviewHistory's own comment) must
  // stay paused, not silently resume and start auto-advancing again from
  // wherever this one click happened to land.
  if (previewHistoryIndex === previewHistory.length - 1) autoFollowPreview = true;
});
// Jumps straight to the newest recorded entry in one click, at the
// user's explicit request — reuses catchUpPreviewToEnd() outright: by
// definition this always lands exactly on the true last entry, so
// resuming auto-follow unconditionally (which that function already
// does) is always correct here, unlike the plain "▶" button above.
attemptPreviewLastBtn.addEventListener("click", catchUpPreviewToEnd);

function hideAttemptPreview() {
  attemptPreview.hidden = true;
  attemptPreviewGrids.innerHTML = "";
  attemptPreviewStatus.textContent = "";
  lastPreviewExamples = null;
  lastPreviewStep = null;
  previewHistory = [];
  previewHistoryIndex = -1;
  autoFollowPreview = true;
  renderWordTable(null);
  updatePreviewNavButtons();
  syncRssPanelVisibility();
}

// Hides the attempt-preview panel WITHOUT wiping previewHistory, at the
// user's explicit request: once the final, playable grid is ready, the
// panel goes back to hidden by default (it's not meant to permanently sit
// above a finished puzzle), but the search history it recorded must
// survive so the new #generation-times-prev-btn/-next-btn (see below) can
// still bring it back up on demand — unlike hideAttemptPreview() above,
// which is reserved for the start of a brand new generation, where
// wiping previous history is exactly the point. Used in place of
// hideAttemptPreview() at the "grid is ready" transition in
// runGeneration().
function hideAttemptPreviewPanel() {
  attemptPreview.hidden = true;
  syncRssPanelVisibility();
}

// The two buttons at the end of #generation-times reuse the exact same
// previewHistory/showPreviousPreview()/showNextPreview() machinery the
// in-progress panel already has — the only thing they add is revealing
// the panel again first, since hideAttemptPreviewPanel() (see above)
// leaves it hidden but its content/history intact once a grid is ready.
generationTimesPrevBtn.addEventListener("click", () => {
  attemptPreview.hidden = false;
  syncRssPanelVisibility();
  showPreviousPreview();
});
generationTimesNextBtn.addEventListener("click", () => {
  attemptPreview.hidden = false;
  syncRssPanelVisibility();
  showNextPreview();
});

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
  // Never intercept a keystroke meant for a focused text field — reported
  // live by the user: with a grid cell selected, clicking into #chatbot-
  // input to type a question still had every letter/Backspace keystroke
  // swallowed here (preventDefault()'d and written into the grid) instead
  // of reaching the chat box. Guards on whatever element is actually
  // focused, generically (any <input>/<textarea>), not just the chat
  // input by id — so any other text field added later is protected the
  // same way, with no need to special-case it here too.
  const active = document.activeElement;
  if (active && (active.tagName === "INPUT" || active.tagName === "TEXTAREA")) return;
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
  renderHoverDefinitionPlaceholder();

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

  // #hover-definition's CSS width: 100% (stretching to #grid-column's own
  // auto-computed width) turned out not to be enough to make long
  // definitions wrap, even with min-width: 0 on the panel itself — reported
  // live by the user, the box still grew to fit unwrapped text. Root cause:
  // #grid-column is *itself* a flex item (of #board, flex-shrink: 0) with
  // the browser's default min-width: auto, so its own auto-computed width
  // can still be pulled wide by an unwrapped child's natural size before
  // any child-level min-width: 0 gets a chance to matter — a compounding
  // version of the same flexbox gotcha across two nested containers, not
  // fixed by patching only the inner one. Sidesteps the whole
  // auto-sizing/stretch ambiguity by setting #hover-definition's width
  // explicitly, in pixels, to #grid's own actual rendered width — read
  // *after* every cell above has been appended, so offsetWidth reflects
  // the grid's final layout, not a partial one. Guaranteed correct
  // regardless of any flex/grid intrinsic-sizing subtlety, since it's an
  // explicit measured value rather than something left for the browser to
  // infer from content.
  hoverDefinition.style.width = `${gridEl.offsetWidth}px`;
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

// Applies showDefinitions to the DOM — called both from the toggle below
// and right after a new grid is rendered (see the form submit handler),
// so a fresh grid always starts with the clue lists hidden regardless of
// whatever the *previous* grid's own toggle state happened to be.
function applyDefinitionsVisibility() {
  cluesEl.hidden = !showDefinitions;
  downCluesSection.hidden = !showDefinitions;
  definitionsBtn.classList.toggle("active", showDefinitions);
}

function toggleDefinitions() {
  showDefinitions = !showDefinitions;
  applyDefinitionsVisibility();
}

solutionBtn.addEventListener("click", toggleSolution);
checkBtn.addEventListener("click", toggleChecking);
definitionsBtn.addEventListener("click", toggleDefinitions);
document.addEventListener("keydown", handleKeydown);

// Clavier virtuel, à la demande explicite de l'utilisateur : "un clavier
// virtuel ne contenant que les 26 lettres de l'alphabet en majuscules
// dans l'ordre naturel sur 2 lignes, plus une flèche vers le bas pour
// configurer le sens vertical... et une flèche vers la droite pour
// configurer le sens horizontalement." `virtualKeyboardDirection` est un
// mode persistant (façon CAPS LOCK, pas SHIFT maintenu — un bouton
// cliqué ne peut pas vraiment être "maintenu") plutôt qu'un état par
// touche : reste actif jusqu'à ce que l'autre bouton de direction soit
// cliqué, exactement comme handleKeydown()'s propre distinction
// Shift/CapsLock (isUpper) décide déjà du sens pour une frappe physique,
// mais fixé une fois pour toutes ici plutôt que réévalué à chaque lettre.
let virtualKeyboardDirection = "across";

function setVirtualKeyboardDirection(direction) {
  virtualKeyboardDirection = direction;
  virtualKeyboardAcrossBtn.classList.toggle("active", direction === "across");
  virtualKeyboardDownBtn.classList.toggle("active", direction === "down");
}

function typeVirtualLetter(letter) {
  // Mêmes gardes que handleKeydown() : une lettre cliquée quand aucune
  // case n'est sélectionnée, ou que la solution est affichée, ne fait
  // rien plutôt que d'écrire dans le vide ou d'écraser la solution.
  if (!puzzle || !selected || showSolution) return;
  userLetters[selected.row][selected.col] = letter;
  // moveSelection() attend "right" pour horizontal, n'importe quelle
  // autre valeur pour vertical (voir sa propre définition) — pas les
  // mêmes libellés que virtualKeyboardDirection ("across"/"down").
  moveSelection(virtualKeyboardDirection === "across" ? "right" : "down");
  renderGrid();
}

function buildVirtualKeyboard() {
  virtualKeyboardRows.replaceChildren();
  const letters = "ABCDEFGHIJKLMNOPQRSTUVWXYZ";
  // 2 rangs de 13 lettres chacun (A-M puis N-Z), l'ordre alphabétique
  // naturel demandé — pas une disposition QWERTY/AZERTY.
  const rows = [letters.slice(0, 13), letters.slice(13)];
  for (const row of rows) {
    const rowEl = document.createElement("div");
    rowEl.className = "virtual-keyboard-row";
    for (const letter of row) {
      const key = document.createElement("button");
      key.type = "button";
      key.className = "virtual-keyboard-key";
      key.textContent = letter;
      key.addEventListener("click", () => typeVirtualLetter(letter));
      rowEl.appendChild(key);
    }
    virtualKeyboardRows.appendChild(rowEl);
  }
}

buildVirtualKeyboard();
virtualKeyboardAcrossBtn.addEventListener("click", () => setVirtualKeyboardDirection("across"));
virtualKeyboardDownBtn.addEventListener("click", () => setVirtualKeyboardDirection("down"));
// La grille (ou tout autre contenu en bas de page) était masquée par le
// clavier virtuel déplié, sans aucun moyen de défiler plus bas pour la
// faire remonter au-dessus — rapporté directement par l'utilisateur.
// #virtual-keyboard est en `position: fixed`, donc totalement indépendant
// de la hauteur réelle de <main> : la page ne peut jamais défiler plus
// loin que le bas naturel de <main> lui-même, qui n'a aucune raison de
// réserver de la place pour un widget flottant qu'il ignore. Une classe
// dédiée sur <main>, ajoutée/retirée en même temps que le clavier se
// déplie/replie, réserve un espace supplémentaire en bas de page
// uniquement pendant que le clavier est réellement ouvert — jamais tout
// le temps, pour ne pas gâcher d'espace quand il est replié (repli par
// défaut).
const mainEl = document.querySelector("main");
virtualKeyboardToggleBtn.addEventListener("click", () => {
  virtualKeyboardEl.classList.toggle("virtual-keyboard-collapsed");
  mainEl.classList.toggle(
    "keyboard-open-padding",
    !virtualKeyboardEl.classList.contains("virtual-keyboard-collapsed"),
  );
});

languageSelect.addEventListener("change", () => {
  uiLanguage = languageSelect.value;
  applyTranslations();
  // attemptPreviewStats is a parameterized string (see i18n.js), rendered
  // directly inside renderAttemptPreview() rather than through the generic
  // [data-i18n] walker applyTranslations() just ran — so a language switch
  // while a preview is showing needs its own explicit re-render, same as
  // togglePreviewLetters() already does below. renderPreviewStatus() is the
  // same kind of parameterized text (describeStep()'s own localized
  // "Tentative X/Y..." message), needing the same explicit re-render —
  // same for attemptPreviewPosition ("Étape X/Y").
  if (lastPreviewExamples) renderAttemptPreview(lastPreviewExamples);
  renderPreviewStatus();
  renderPreviewPosition();
  // "David FALCON"'s own welcome bubble, at the user's explicit request
  // — see renderChatWelcome()'s own docstring for why this only ever
  // does anything before the player's first real message.
  renderChatWelcome();
  // Le filtre de langue du panneau "Actu Croisée" suit désormais la
  // langue de l'interface à chaque changement, à la demande explicite de
  // l'utilisateur : "Quand l'utilisateur change la langue de
  // l'interface, il faut adapter la langue du fil d'actu." Revirement
  // assumé par rapport à la décision initiale de ce même panneau (fixé
  // une seule fois au chargement, jamais resynchronisé ensuite pour ne
  // pas écraser un choix manuel du joueur sur ce sélecteur précisément)
  // — la préférence la plus récente de l'utilisateur prévaut, même
  // schéma de revirement déjà appliqué cette session à la visibilité du
  // panneau lui-même.
  rssLanguageFilter.value = uiLanguage;
  renderRssList();
});

applyTranslations();

// Raised from 700ms to 2000ms at the user's explicit request, after a
// reported sporadic 502 on /api/generate/status with no corresponding trace
// at all in the backend's own log (see frontend/server.py's PROXY_TIMEOUT_S
// for the full diagnosis) — the user doesn't need sub-second status
// updates, and polling less often means fewer chances to catch the backend
// mid-stall during a heavy generation (up to PARALLEL_ATTEMPTS parallel CSP
// search processes, see crossword_gen.py).
const POLL_INTERVAL_MS = 2000;

// How many consecutive failed polls pollJob() tolerates (a network error,
// or a "backend_unavailable" 502 from the proxy) before actually declaring
// the connection lost, at the user's explicit request: "Est-ce possible
// que le Front effectue quelques tentatives de reconnexion avant de
// déclarer la liaison brisée ?" A single missed poll is often just a brief
// blip (a Wi-Fi hiccup, or the exact kind of momentarily-CPU-starved-
// event-loop 502 already documented in frontend/server.py's own
// PROXY_TIMEOUT_S entry) — the job itself keeps running server-side the
// whole time regardless (see errorConnectionLostDuringGeneration/
// errorBackendUnavailableDuringGeneration's own reasoning), so there's no
// reason to give up on the very first one. Deliberately scoped to the poll
// loop only, never to the initial POST /api/generate(/continue) calls (see
// their own comments) — those aren't safely retriable the same way (a lost
// response there could just as easily mean the request never reached the
// server, or that it did and a job already got created; retrying blindly
// risks launching a second, redundant generation).
const POLL_RECONNECT_ATTEMPTS = 3;

// How often the live attempt-preview advances by one more previewHistory
// entry (see pollJob() below) — deliberately its own, faster-ticking timer,
// independent of POLL_INTERVAL_MS, at the user's explicit report: "quand le
// Back est en avance sur le Front, le Front continue à télécharger les
// grilles d'aperçu, mais n'avance plus dans la séquence... tant que
// l'utilisateur ne revient pas en arrière, il faut que le Front continue à
// avancer dans l'affichage au fur et à mesure que les nouvelles grilles de
// l'aperçu arrivent." A real, structural gap, not a perception issue:
// tying the reveal to the poll loop itself (one `showNextPreview()` call
// per `GET /api/generate/status` round trip) caps the reveal rate at 1
// entry per POLL_INTERVAL_MS no matter how large a backlog piles up — and
// a real burst regularly produces far more than that in a single poll
// window (21 new entries measured between two consecutive 2s polls of a
// real job), so the displayed sequence falls further and further behind
// the true live edge over time, never catching back up on its own — a
// player would have needed to click "▶" by hand, over and over, to make
// any further visible progress. Ticking this reveal on its own faster
// timer lets it drain a backlog considerably quicker than it typically
// accumulates, while a caught-up, no-backlog run still only ever has one
// new entry to reveal every couple of seconds anyway, so it isn't made to
// look rushed either.
const PREVIEW_REVEAL_INTERVAL_MS = 500;

// Hard ceiling on any single fetch to this origin (the /api/generate and
// /api/generate/status polls) — without this, a fetch left hanging (server
// process killed mid-connection rather than cleanly refusing it, or any
// other stall between browser and server) never resolves nor rejects on its
// own, leaving pollJob's loop stuck on an unresolved await indefinitely with
// no way for the user to recover short of reloading the page. Set above
// frontend/server.py's own outbound proxy timeout to the backend
// (PROXY_TIMEOUT_S, 30s as of this value) so a legitimately slow-but-healthy
// round trip through the proxy doesn't race against this client-side abort.
const FETCH_TIMEOUT_MS = 35000;

// A chat reply (POST /api/chat) is a single, synchronous LLM call, not a
// quick status check — set above frontend/server.py's own CHAT_PROXY_
// TIMEOUT_S (150s), same "above the callee's own timeout" reasoning as
// FETCH_TIMEOUT_MS itself vs. PROXY_TIMEOUT_S.
const CHAT_FETCH_TIMEOUT_MS = 160000;

function sleep(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

// "XhXmnXs" (heures/minutes/secondes), à la demande explicite de
// l'utilisateur, pour afficher les durées de génération de la grille et
// des définitions (voir gridData.generation_duration_seconds/
// clues_duration_seconds, calculées côté back — backend/app.py). Les
// unités à zéro en tête sont omises (ex. "45s" plutôt que "0h0mn45s")
// plutôt que toujours afficher les trois, pour rester lisible sur le cas
// courant (quelques dizaines de secondes à quelques minutes).
function formatDuration(seconds) {
  const total = Math.max(0, Math.round(seconds || 0));
  const h = Math.floor(total / 3600);
  const m = Math.floor((total % 3600) / 60);
  const s = total % 60;
  let out = "";
  if (h > 0) out += `${h}h`;
  if (h > 0 || m > 0) out += `${m}mn`;
  out += `${s}s`;
  return out;
}

// Décompte de mots essayés (backend/crossword_gen.py, `total_attempts` —
// somme de `Filler.checks`, incrémenté une fois par mot candidat essayé,
// voir son propre historique) formaté pour rester lisible même une fois
// dans les millions, à la demande explicite de l'utilisateur : nombre
// exact en dessous de 1000, puis divisé par 1000 avec un suffixe K/M/G au
// fur et à mesure — sans décimale ("12K", jamais "12,3K"), à sa demande
// explicite elle aussi.
function formatAttemptCount(n) {
  const value = Math.max(0, Math.round(n || 0));
  if (value < 1000) return String(value);
  if (value < 1000000) return `${Math.round(value / 1000)}K`;
  if (value < 1000000000) return `${Math.round(value / 1000000)}M`;
  return `${Math.round(value / 1000000000)}G`;
}

async function fetchWithTimeout(url, options, timeoutMs) {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeoutMs);
  try {
    return await fetch(url, { ...options, signal: controller.signal });
  } finally {
    clearTimeout(timer);
  }
}

// Turns one backend progress step ({code, ...data} — see
// backend/crossword_gen.py's on_progress calls) into a localized status
// message. Falls back to the generic "generating" message for any step
// this version of the frontend doesn't know how to describe yet, so an
// older UI never breaks against a newer backend.
function describeStep(t, step) {
  if (!step) return t.statusGenerating;
  let message;
  switch (step.code) {
    case "starting":
      message = t.statusStarting;
      break;
    case "queued_grid":
      message = t.statusQueuedGrid(step.position, step.queue_length);
      break;
    case "queued_clues":
      message = t.statusQueuedClues(step.position, step.queue_length);
      break;
    case "pattern":
      message = t.statusPattern(step.attempt, step.attempts, formatAttemptCount(step.total_attempts));
      break;
    case "pattern_generated":
      message = t.statusPatternGenerated(step.attempt, step.attempts, formatAttemptCount(step.total_attempts));
      break;
    case "pattern_attempt_failed":
      message = t.statusPatternAttemptFailed(step.attempt, step.attempts, formatAttemptCount(step.total_attempts));
      break;
    case "pre_cleanup_optimizing":
      message = t.statusPreCleanupOptimizing(step.attempt, step.attempts, formatAttemptCount(step.total_attempts));
      break;
    case "pre_cleanup_optimized":
      message = t.statusPreCleanupOptimized(step.attempt, step.attempts, formatAttemptCount(step.total_attempts));
      break;
    case "pattern_found":
      message = t.statusPatternFound(step.attempt, formatAttemptCount(step.total_attempts));
      break;
    case "minimizing":
      message = t.statusMinimizing;
      break;
    case "grid_ready":
      message = t.statusGridReady(step.word_count);
      break;
    case "clues":
      message = t.statusClues(step.current, step.total);
      break;
    case "saving":
      message = t.statusSaving;
      break;
    default:
      message = t.statusGenerating;
  }
  // `budget_percent` (backend/crossword_gen.py's "budget_progress" event,
  // merged into job["step"] by backend/app.py rather than replacing it —
  // see its own comment) is only ever present while a pattern-search
  // attempt is genuinely still in flight (never on "minimizing"/"clues"/
  // etc., which only start once the drain thread that produces it has
  // already stopped) — appended as a plain suffix rather than woven into
  // each statusPattern*/etc. string itself, at the user's explicit
  // request ("sur la ligne de statut de l'interface, ajouter le
  // pourcentage du budget déjà consommé"), so every existing message stays
  // untouched and only gains this one extra fragment when the data is
  // actually available.
  if (typeof step.budget_percent === "number") {
    message = t.statusBudgetPercent(message, step.budget_percent);
  }
  return message;
}

// Turns a backend error code (backend/app.py's job["error_code"], or
// frontend/server.py's {"code": ...} proxy error) into a localized error
// message. Falls back to whatever raw text the backend sent (itself
// falling back to the generic "unknown error" message) for a code this
// version of the frontend doesn't recognize, so an older UI degrades to
// plain text instead of breaking against a newer backend.
// `duringGeneration` (false by default) only changes the "backend_
// unavailable" case (the proxy reaching the back but getting no response —
// see frontend/server.py's own PROXY_TIMEOUT_S comment on why this can be
// a transient hiccup, not necessarily the back having actually crashed):
// when true, the job this call was polling for may well still be running
// server-side despite this one poll failing, so the message adds a note
// that it may still show up in the library once finished. Every other
// caller (the initial POST /api/generate(/continue), and loadLibraryGrid)
// never has a real job in flight yet at that exact call, so they keep the
// plain message.
function describeErrorCode(t, code, fallbackText, duringGeneration = false) {
  switch (code) {
    case "no_fillable_grid":
      return t.errorNoFillableGrid;
    case "clue_generation_failed":
      return t.errorClueGenerationFailed;
    case "internal_error":
      return t.errorInternal;
    case "backend_unavailable":
      return duringGeneration
        ? t.errorBackendUnavailableDuringGeneration
        : t.errorBackendUnavailable;
    default:
      return fallbackText || t.statusErrorFallback;
  }
}

// Distinguishes a user-requested stop (see stopBtn below) from a genuine
// failure — the submit handler's own catch block uses this to skip the
// error styling (#status.error, red text) for a cancellation, which isn't
// an error at all, just a choice the player made.
class CancelledError extends Error {}

// Carries a failed job's own id/error code alongside the localized message
// a plain Error would already have — at the user's explicit request, for
// the "Continuer" button (see continueBtn below): runGeneration()'s catch
// block needs to know *which* job just failed and *why* to decide whether
// to offer resuming it, which a bare Error (message only) can't convey.
class GenerationFailedError extends Error {
  constructor(message, jobId, errorCode) {
    super(message);
    this.jobId = jobId;
    this.errorCode = errorCode;
  }
}

// job_id of whichever generation is currently in flight, or null — set
// right after POST /api/generate resolves, cleared once the whole submit
// handler finishes (see its own finally block). stopBtn's click handler
// (below) needs this to know *which* job to cancel, since it can be
// clicked at any point while pollJob() is still awaiting its own poll
// loop, well before that loop returns control to the submit handler.
let currentJobId = null;

// Generation runs as a background job on the backend (grid + clue
// generation together can take anywhere from a few seconds to a few
// minutes) — this polls its status until it's done, updating the on-screen
// status message with each step along the way, and returns the finished
// grid.
async function pollJob(jobId, t) {
  // How many of `examples_history`'s entries this loop has already
  // recorded into `previewHistory` — every new entry is always recorded
  // in full, right away, however many arrived since the last poll (see
  // recordPreviewHistory's own comment). What actually gets *revealed*
  // live is handled by a wholly separate timer (see PREVIEW_REVEAL_
  // INTERVAL_MS's own comment for why this loop's own POLL_INTERVAL_MS
  // cadence turned out to be the wrong thing to tie it to) started right
  // below and stopped again in the `finally` once this loop ends, one way
  // or another. catchUpPreviewToEnd() jumps straight to the true final
  // entry the moment the job reaches a terminal status, so a large
  // remaining backlog never delays the actual result.
  let nextExampleIndex = 0;
  // Consecutive failed polls so far (network error or backend_unavailable
  // 502) — reset to 0 the moment a poll actually succeeds. See
  // POLL_RECONNECT_ATTEMPTS's own comment for why this exists.
  let consecutivePollFailures = 0;
  const revealTimer = setInterval(() => {
    if (autoFollowPreview) showNextPreview();
  }, PREVIEW_REVEAL_INTERVAL_MS);
  try {
    while (true) {
      let response;
      try {
        response = await fetchWithTimeout(`/api/generate/status/${jobId}`, {}, FETCH_TIMEOUT_MS);
      } catch (err) {
        // Covers both a hard timeout (AbortError, see FETCH_TIMEOUT_MS) and an
        // outright connection failure (e.g. the server process died) — either
        // way there's no response to read a structured error code from, so a
        // dedicated, translated message stands in for describeErrorCode's
        // usual backend-error-code lookup. Deliberately a distinct string
        // from the plain errorConnectionLost used by the initial POST
        // /api/generate(/continue) calls below: unlike those (where no job
        // was ever created, so there's nothing to look for later), this poll
        // loss happens once a real job_id already exists and is running on
        // the backend independently of this browser tab's own connection —
        // generation keeps going server-side and, on success, still gets
        // saved to GRID_STORE/ (see backend/app.py's _run_generate_job), so
        // it's worth telling the player it may still show up in the library.
        consecutivePollFailures += 1;
        if (consecutivePollFailures <= POLL_RECONNECT_ATTEMPTS) {
          setStatus(t.statusReconnecting(consecutivePollFailures, POLL_RECONNECT_ATTEMPTS), false);
          await sleep(POLL_INTERVAL_MS);
          continue;
        }
        throw new Error(t.errorConnectionLostDuringGeneration);
      }
      const data = await response.json();
      if (!response.ok) {
        consecutivePollFailures += 1;
        if (consecutivePollFailures <= POLL_RECONNECT_ATTEMPTS) {
          setStatus(t.statusReconnecting(consecutivePollFailures, POLL_RECONNECT_ATTEMPTS), false);
          await sleep(POLL_INTERVAL_MS);
          continue;
        }
        throw new Error(describeErrorCode(t, data.detail && data.detail.code, data.detail, true));
      }
      consecutivePollFailures = 0;
      const history = data.examples_history || [];
      if (history.length > nextExampleIndex) {
        recordPreviewHistory(history.slice(nextExampleIndex));
        nextExampleIndex = history.length;
      }
      if (data.status === "error") {
        catchUpPreviewToEnd();
        throw new GenerationFailedError(
          describeErrorCode(t, data.error_code, data.error), jobId, data.error_code,
        );
      }
      if (data.status === "cancelled") {
        catchUpPreviewToEnd();
        throw new CancelledError(t.statusCancelled);
      }
      if (data.status === "done") {
        catchUpPreviewToEnd();
        return data.result;
      }
      setStatus(describeStep(t, data.step), false);
      await sleep(POLL_INTERVAL_MS);
    }
  } finally {
    clearInterval(revealTimer);
  }
}

// Renders a finished grid's own `result` (backend/crossword_gen.py's
// generate_grid() return dict, extended by backend/app.py with the three
// duration fields and, at the user's explicit request, a short LLM-
// generated `title`) into #result — shared by runGeneration()'s own
// success path (a grid that just finished generating) and loadLibraryGrid()
// below ("Bibliothèque" button, at the user's explicit request), so a
// grid reloaded from GRID_STORE/ displays through exactly the same code
// path as one fresh out of the generator, with the "Vérification"/
// "Solution" buttons immediately usable either way — no separate,
// easily-drifting rendering logic for the two cases.
function displayFinalGrid(gridData) {
  puzzle = gridData;
  userLetters = Array.from({ length: gridData.height }, () => Array(gridData.width).fill(""));
  selected = null;
  showSolution = false;
  checking = false;
  solutionBtn.classList.remove("active");
  checkBtn.classList.remove("active");

  hideAttemptPreviewPanel();
  // #result (and so #grid, its descendant) must already be visible before
  // renderGrid() runs — see runGeneration()'s own historical note on this
  // exact ordering requirement (renderGrid() measures gridEl.offsetWidth).
  result.hidden = false;
  syncRssPanelVisibility();
  // Grid title (see backend/clues.py's LLMClueGenerator.generate_title),
  // at the user's explicit request: "Affiche ce nom en haut de la grille
  // à jouer." A grid saved before this feature existed (or one whose
  // title generation itself failed, see generate_title's own "" return)
  // simply has no title line shown, rather than an empty heading.
  gridTitleEl.textContent = gridData.title || "";
  gridTitleEl.hidden = !gridData.title;
  renderGrid();
  renderClues(gridData.words);
  const t = I18N[uiLanguage];
  stats.textContent = t.stats(gridData.word_count, gridData.black_count, (gridData.black_ratio * 100).toFixed(1));
  generationTimes.textContent = t.generationTimes(
    formatDuration(gridData.generation_duration_seconds),
    formatDuration(gridData.optimization_duration_seconds),
    formatDuration(gridData.clues_duration_seconds),
  );
  solutionBtn.hidden = false;
  checkBtn.hidden = false;
  definitionsBtn.hidden = false;
  // Hidden by default on every fresh grid, at the user's explicit
  // request, regardless of whatever the *previous* grid's own toggle
  // state was left at.
  showDefinitions = false;
  applyDefinitionsVisibility();
  // The solution is established now — solutionBtn takes over revealing
  // letters from here on, at the user's explicit request, so the
  // preview-only toggle has nothing left to control.
  attemptPreviewRevealBtn.hidden = true;
}

function hideLibraryPanel() {
  libraryPanel.hidden = true;
  syncRssPanelVisibility();
}

// 1-based, reset to 1 every time the panel is (re)opened (see the
// libraryBtn click handler below) — module-level rather than a
// renderLibraryList() parameter so libraryPrevBtn/libraryNextBtn's own
// click handlers can mutate it and re-render without threading it
// through every call site.
let libraryCurrentPage = 1;
// Recomputed on every renderLibraryList() call from the response's own
// `total`/`page_size` — kept around (rather than only a local variable
// there) so libraryNextBtn's own click handler can bound-check against it
// too, the same defensive belt-and-suspenders style already used by
// showNextPreview()'s own out-of-range guard (never relying solely on the
// button's native `disabled` attribute to prevent an out-of-range click).
let libraryTotalPages = 1;

// "Bibliothèque" button (permanent, unlike every other button in
// #generate-form — see index.html), at the user's explicit request:
// lists every grid saved under GRID_STORE/ (backend/grid_store.py), the
// UI's own current language first, then English (unless that's already
// the UI language), then everything else, most recent first within each
// group — the sorting itself is entirely server-side (GET /api/library),
// this just renders whatever order the backend already returned. Fetched
// fresh every time the panel opens rather than cached, since a grid saved
// by a generation finishing in another tab (or another browser entirely)
// should show up without needing a page reload.
//
// Paginated (20 rows per page — see backend/app.py's own LIBRARY_PAGE_
// SIZE, the actual source of truth this reads back from the response
// rather than duplicating the number client-side), at the user's
// explicit request: "Ajoute une pagination à la liste des grilles de la
// bibliothèque : 20 lignes affichées max à chaque page." The pagination
// itself is server-side (GET /api/library?page=N) — this just renders
// whichever single page's worth of rows came back, plus a "Page X/Y"
// readout and disables libraryPrevBtn/libraryNextBtn at either end, from
// the `total`/`page_size` the same response carries.
async function renderLibraryList() {
  const t = I18N[uiLanguage];
  libraryTbody.replaceChildren();
  libraryEmpty.hidden = true;
  libraryPagination.hidden = true;
  let entries = [];
  let total = 0;
  let pageSize = 1;
  try {
    const response = await fetchWithTimeout(
      `/api/library?preferred_language=${encodeURIComponent(uiLanguage)}&page=${libraryCurrentPage}`,
      {}, FETCH_TIMEOUT_MS,
    );
    if (response.ok) {
      const data = await response.json();
      entries = data.grids || [];
      total = data.total || 0;
      pageSize = data.page_size || 1;
    }
  } catch (err) {
    // Best-effort: a browsing feature failing silently (empty list) is
    // preferable to surfacing a connection error over it, especially
    // since #status may currently be showing an unrelated generation's
    // own progress.
  }
  if (total > 0) {
    libraryTotalPages = Math.max(1, Math.ceil(total / pageSize));
    libraryPagination.hidden = false;
    libraryPosition.textContent = t.libraryPosition(libraryCurrentPage, libraryTotalPages);
    libraryPrevBtn.disabled = libraryCurrentPage <= 1;
    libraryNextBtn.disabled = libraryCurrentPage >= libraryTotalPages;
  }
  if (entries.length === 0) {
    libraryEmpty.hidden = false;
    return;
  }
  const difficultyLabels = {
    easy: t.difficultyEasy, medium: t.difficultyMedium, hard: t.difficultyHard,
  };
  for (const entry of entries) {
    const tr = document.createElement("tr");
    tr.tabIndex = 0;
    // First column, at the user's explicit request: "la première colonne
    // doit indiquer la langue (la langue de l'interface en premier)" — the
    // sort itself already puts the UI's own language first (GET /api/
    // library's own preferred_language, see backend/grid_store.py's
    // list_grids), this just makes that language visible per row. Reuses
    // #language's own <option> text (a language always names itself in
    // its own language there — "English", never "Anglais" — rather than
    // duplicating that same small list here, which could otherwise drift
    // out of sync with it).
    const languageTd = document.createElement("td");
    const languageOption = languageSelect.querySelector(`option[value="${entry.language}"]`);
    languageTd.textContent = languageOption ? languageOption.textContent : (entry.language || "");
    const dateTd = document.createElement("td");
    dateTd.textContent = entry.created_at ? new Date(entry.created_at).toLocaleString(uiLanguage) : "";
    const titleTd = document.createElement("td");
    titleTd.textContent = entry.title || "";
    const difficultyTd = document.createElement("td");
    difficultyTd.textContent = difficultyLabels[entry.difficulty] || entry.difficulty || "";
    const sizeTd = document.createElement("td");
    sizeTd.textContent = entry.width && entry.height ? `${entry.width}×${entry.height}` : "";
    tr.append(languageTd, dateTd, titleTd, difficultyTd, sizeTd);
    tr.addEventListener("click", () => loadLibraryGrid(entry.id));
    tr.addEventListener("keydown", (event) => {
      if (event.key === "Enter" || event.key === " ") {
        event.preventDefault();
        loadLibraryGrid(entry.id);
      }
    });
    libraryTbody.appendChild(tr);
  }
}

// Clicking a row loads that grid straight into the player, at the user's
// explicit request: "ça charge la grille pour la jouer (exactement comme
// en fin de process, avec les boutons 'Vérification' et 'Solution'
// visibles)" — reuses displayFinalGrid() above, so this behaves exactly
// like a generation that just finished. Deliberately doesn't touch
// currentJobId/stopBtn/continueBtn beyond hiding them: a library grid was
// never a job in the first place, so there's nothing to cancel/continue.
async function loadLibraryGrid(gridId) {
  const t = I18N[uiLanguage];
  try {
    const response = await fetchWithTimeout(`/api/library/${gridId}`, {}, FETCH_TIMEOUT_MS);
    const data = await response.json();
    if (!response.ok) {
      throw new Error(describeErrorCode(t, data.detail && data.detail.code, data.detail));
    }
    hideAttemptPreview();
    stopBtn.hidden = true;
    continueBtn.hidden = true;
    displayFinalGrid(data);
    hideLibraryPanel();
    setStatus(t.statusLibraryLoaded, false);
  } catch (err) {
    setStatus(err.message, true);
  }
}

libraryBtn.addEventListener("click", () => {
  if (libraryPanel.hidden) {
    libraryPanel.hidden = false;
    syncRssPanelVisibility();
    libraryCurrentPage = 1;
    renderLibraryList();
  } else {
    hideLibraryPanel();
  }
});

libraryCloseBtn.addEventListener("click", hideLibraryPanel);

libraryPrevBtn.addEventListener("click", () => {
  if (libraryCurrentPage <= 1) return;
  libraryCurrentPage -= 1;
  renderLibraryList();
});

libraryNextBtn.addEventListener("click", () => {
  if (libraryCurrentPage >= libraryTotalPages) return;
  libraryCurrentPage += 1;
  renderLibraryList();
});

// "David FALCON" chat widget, at the user's explicit request: "En bas à
// droite de l'interface, ajoute un ChatBot (ouvert par défaut) avec
// l'icône de l'application... Il affiche un message de bienvenue...
// Le message de bienvenu doit être réécrit si l'utilisateur change la
// langue." `chatHistory` only ever holds genuine user/assistant turns
// actually exchanged with the LLM (backend/chatbot.py rebuilds its own
// system prompt fresh every call, so this never includes it) — the
// purely cosmetic welcome bubble is rendered straight to the DOM and
// deliberately never added here, since it was never something the model
// actually said. `chatUserHasSpoken` gates the "rewrite the welcome
// message on language change" behavior: once the player has sent a real
// message, the welcome bubble is already part of the conversation's own
// past and is left alone on a later language change, rather than
// retroactively rewriting an earlier turn mid-conversation — only
// before any real exchange has happened does switching languages
// replace the single greeting bubble shown so far.
let chatHistory = [];
let chatUserHasSpoken = false;
// Identifiant opaque, généré une seule fois par chargement de page, à la
// demande explicite de l'utilisateur : "Pour chaque discussion dans le
// ChatBot, crée un LOG des questions/réponses... Un fichier par session
// utilisateur." Envoyé sur chaque message (voir plus bas) pour que
// backend/app.py route tous les tours de cette même conversation vers le
// même fichier de log. `crypto.randomUUID()` est disponible dans tout
// navigateur moderne servant cette page en HTTPS/localhost (les deux
// seuls contextes où l'API Web Crypto est exposée) ; un repli simple
// (horodatage + nombre aléatoire) couvre le cas contraire plutôt que de
// faire planter le chat entier pour un identifiant qui n'a besoin que
// d'être raisonnablement unique, jamais cryptographiquement sûr.
const chatSessionId = (window.crypto && window.crypto.randomUUID)
  ? window.crypto.randomUUID()
  : `${Date.now()}-${Math.random().toString(36).slice(2)}`;

function escapeHtml(text) {
  return text.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
}

// A small, self-contained Markdown-to-HTML renderer for David FALCON's own
// replies, at the user's explicit request: "L'affichage du Bot doit être
// capable de formatter du Markdown produit par le LLM." Deliberately not a
// third-party library pulled in from a CDN — this project has never had an
// external frontend dependency of any kind (see index.html's own two plain
// <script> tags), and the small subset of Markdown this project's own small
// local model actually produces (bold, italics, inline code, bullet/
// numbered lists, the occasional heading) doesn't need one. `text` is
// HTML-escaped FIRST, unconditionally, before any Markdown syntax is
// turned into real tags — this is the one thing that makes it safe to
// render as `innerHTML` at all: whatever the LLM writes can only ever
// become the small, fixed set of tags this function itself emits below,
// never arbitrary markup of its own.
function renderInlineMarkdown(escaped) {
  return escaped
    .replace(/`([^`]+)`/g, "<code>$1</code>")
    .replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>")
    .replace(/(^|[^*])\*([^*\s][^*]*?)\*(?!\*)/g, "$1<em>$2</em>")
    .replace(/(^|[^_\w])_([^_\s][^_]*?)_(?!_)/g, "$1<em>$2</em>")
    .replace(/\[([^\]]+)\]\((https?:\/\/[^)\s]+)\)/g, '<a href="$2" target="_blank" rel="noopener noreferrer">$1</a>');
}

function renderMarkdown(text) {
  const lines = escapeHtml(text).split("\n");
  const htmlParts = [];
  let listItems = null;
  let listTag = null;
  const flushList = () => {
    if (listItems) {
      htmlParts.push(`<${listTag}>${listItems.join("")}</${listTag}>`);
      listItems = null;
      listTag = null;
    }
  };
  for (const rawLine of lines) {
    const line = rawLine.trim();
    const bulletMatch = line.match(/^[-*+]\s+(.*)$/);
    const numberedMatch = line.match(/^\d+[.)]\s+(.*)$/);
    const headingMatch = line.match(/^#{1,6}\s+(.*)$/);
    if (bulletMatch || numberedMatch) {
      const tag = bulletMatch ? "ul" : "ol";
      const content = bulletMatch ? bulletMatch[1] : numberedMatch[1];
      if (listTag && listTag !== tag) flushList();
      listTag = tag;
      listItems = listItems || [];
      listItems.push(`<li>${renderInlineMarkdown(content)}</li>`);
    } else {
      flushList();
      if (headingMatch) {
        htmlParts.push(`<p><strong>${renderInlineMarkdown(headingMatch[1])}</strong></p>`);
      } else if (line) {
        htmlParts.push(`<p>${renderInlineMarkdown(line)}</p>`);
      }
    }
  }
  flushList();
  return htmlParts.join("");
}

function appendChatBubble(role, text) {
  const bubble = document.createElement("div");
  bubble.className = `chatbot-message chatbot-message-${role}`;
  // Only the assistant's own replies are ever interpreted as Markdown —
  // the player's own typed message stays literal plain text, exactly as
  // typed, with no reason to interpret any Markdown-like syntax in it.
  if (role === "assistant") {
    bubble.innerHTML = renderMarkdown(text);
  } else {
    bubble.textContent = text;
  }
  chatbotMessages.appendChild(bubble);
  chatbotMessages.scrollTop = chatbotMessages.scrollHeight;
  return bubble;
}

function renderChatWelcome() {
  if (chatUserHasSpoken) return;
  chatbotMessages.replaceChildren();
  appendChatBubble("assistant", I18N[uiLanguage].chatbotWelcome);
}

// Everything David FALCON is told about the live interface state, at the
// user's explicit request: "A chaque question de l'utilisateur, le LLM
// est informé de... l'état de l'interface, y compris la position et le
// sens d'un éventuel mot sélectionner dans la grille à jouer [et] la
// liste des définitions... numéro de ligne et de colonne de chaque mot,
// vertical ou horizontal, définition, valeur du mot (réponse)." Reports
// two genuinely distinct concepts, at the user's own later, explicit
// clarification: "un mot est sélectionné en passant la souris au dessus
// sans forcément cliquer sur une case. Faire la différence entre 'mot
// sélectionné' (survol) et 'case/mot en cours de remplissage' (cliqué)."
//   - `hovered_word` — the word currently framed by the mouse-hover
//     highlight (`hoveredWord`, see highlightWordAt()/clearHighlights()),
//     already resolved to one specific word's own (row, col, direction) —
//     this is what "quel est le mot sélectionné" actually refers to.
//   - `filling_cell` — the clicked cell the player is actively typing
//     into (`selected`, see selectCell()), which has no direction of its
//     own and can belong to up to two words (across and down) at once;
//     sent as a raw position rather than pre-resolved to one word, same
//     as before this clarification — backend/chatbot.py's own
//     _find_selected_words() does that resolution.
// Both are sent alongside the *entire* word list either way (each word's
// own row/col/direction/answer lets a lookup by either concept work, and
// each word's own length is simply len(answer) — no separate field
// needed for it).
function buildChatUiContext() {
  return {
    puzzle_loaded: !!puzzle,
    hovered_word: hoveredWord ? { row: hoveredWord.row, col: hoveredWord.col, direction: hoveredWord.direction } : null,
    filling_cell: selected ? { row: selected.row, col: selected.col } : null,
    words: puzzle
      ? puzzle.words.map((w) => ({
          row: w.row, col: w.col, direction: w.direction, clue: w.clue, answer: w.answer,
        }))
      : [],
  };
}

function toggleChatbotCollapsed() {
  chatbotEl.classList.toggle("chatbot-collapsed");
}
chatbotToggleBtn.addEventListener("click", toggleChatbotCollapsed);
// Icône cliquable en mode réduit (le bouton "–" lui-même est alors caché,
// voir style.css), à la demande explicite de l'utilisateur : "En mode
// 'réduit', le ChatBot doit afficher l'icône du site" — même fonction de
// bascule que le bouton, réutilisée telle quelle plutôt que dupliquée.
document.getElementById("chatbot-icon").addEventListener("click", toggleChatbotCollapsed);

// Reads POST /api/chat's own text/event-stream body (frontend/server.py
// relays it chunk by chunk, backend/app.py/backend/chatbot.py's own
// ChatBot.reply_stream() produce it) and calls `onDelta(text)` for every
// `{"delta": ...}` event as it arrives, in order — at the user's
// explicit request: "Le Bot doit afficher la réponse en streaming."
// Returns the full reply once the stream ends (`data: [DONE]`), or
// throws if the very first event is a `{"error": ...}` one (a connection
// failure that happened before any real content was ever produced —
// see frontend/server.py's own proxy_chat docstring for why this arrives
// as a normal 200 stream event rather than a non-2xx HTTP status). A
// `{"error": ...}` event arriving *after* some real content already
// streamed in is treated as "stop here, keep what we have" rather than
// discarding it — there's no clean way to retroactively un-show text the
// player has already seen appear.
async function readChatStream(response, onDelta) {
  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  let full = "";
  let sawAnyDelta = false;
  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    let sepIndex;
    while ((sepIndex = buffer.indexOf("\n\n")) !== -1) {
      const rawEvent = buffer.slice(0, sepIndex);
      buffer = buffer.slice(sepIndex + 2);
      if (!rawEvent.startsWith("data: ")) continue;
      const payload = rawEvent.slice(6).trim();
      if (payload === "[DONE]") return full;
      let event;
      try {
        event = JSON.parse(payload);
      } catch (err) {
        continue;
      }
      if (event.error) {
        if (!sawAnyDelta) throw new Error(event.error);
        return full;
      }
      if (event.delta) {
        sawAnyDelta = true;
        full += event.delta;
        onDelta(full);
      }
    }
  }
  return full;
}

chatbotForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  const message = chatbotInput.value.trim();
  if (!message) return;
  const t = I18N[uiLanguage];
  chatUserHasSpoken = true;
  chatbotInput.value = "";
  chatbotInput.disabled = true;
  appendChatBubble("user", message);
  const replyBubble = appendChatBubble("assistant", "");
  let fullReply = "";
  try {
    const response = await fetchWithTimeout("/api/chat", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        message,
        history: chatHistory,
        language: uiLanguage,
        ui_context: buildChatUiContext(),
        session_id: chatSessionId,
      }),
    }, CHAT_FETCH_TIMEOUT_MS);
    if (!response.ok || !response.body) throw new Error(t.chatbotErrorFailed);
    fullReply = await readChatStream(response, (textSoFar) => {
      // Re-rendered from scratch on every incremental chunk — the raw
      // Markdown source itself (never partially-rendered HTML) is what's
      // accumulated, so a `**bold**` marker split across two separate
      // stream chunks still renders correctly once its closing `**`
      // finally arrives, rather than ever being parsed a token at a time.
      replyBubble.innerHTML = renderMarkdown(textSoFar);
      chatbotMessages.scrollTop = chatbotMessages.scrollHeight;
    });
    if (!fullReply) throw new Error(t.chatbotErrorFailed);
    chatHistory.push({ role: "user", content: message });
    chatHistory.push({ role: "assistant", content: fullReply });
  } catch (err) {
    replyBubble.innerHTML = renderMarkdown(t.chatbotErrorFailed);
  } finally {
    chatbotInput.disabled = false;
    chatbotInput.focus();
    chatbotMessages.scrollTop = chatbotMessages.scrollHeight;
  }
});

renderChatWelcome();

// Shared by the form's own submit handler and continueBtn's click handler
// (both further below) — at the user's explicit request: "Continuer"
// relaunches a fresh job from a failed one's own resume state, but from
// here on it needs exactly the same UI setup, polling, rendering, and
// error handling as a brand new generation, not a separate code path that
// could quietly drift out of sync with it. `startJob(t)` is the one part
// that actually differs between the two callers — it does whatever HTTP
// call starts the job and resolves to its job_id (or throws), everything
// else here is identical either way.
async function runGeneration(startJob) {
  const t = I18N[uiLanguage];

  generationInProgress = true;
  button.disabled = true;
  result.hidden = true;
  syncRssPanelVisibility();
  solutionBtn.hidden = true;
  checkBtn.hidden = true;
  definitionsBtn.hidden = true;
  // Shown again in case a *previous* generation's own result already
  // hid it (see below) — the player can still change their mind about
  // seeing preview letters for this new run, right from the start.
  attemptPreviewRevealBtn.hidden = false;
  stopBtn.hidden = false;
  stopBtn.disabled = false;
  // Hidden on every fresh attempt (a new form submission or a "Continuer"
  // click alike) — only shown again if *this* run itself ends in the
  // specific "no_fillable_grid" failure the button exists for (see the
  // catch block below).
  continueBtn.hidden = true;
  currentJobId = null;
  setStatus(t.statusGenerating, false);
  hideAttemptPreview();

  try {
    const jobId = await startJob(t);
    currentJobId = jobId;
    const gridData = await pollJob(jobId, t);
    displayFinalGrid(gridData);
    setStatus(t.statusGenerated, false);
  } catch (err) {
    // A user-requested stop isn't an error — no red #status.error styling
    // for it (see CancelledError above).
    setStatus(err.message, !(err instanceof CancelledError));
    // "Continuer" button, at the user's explicit request: only offered for
    // the one specific failure it can actually do something about — a
    // total search failure (every one of `attempts`, 200 by default,
    // paliers exhausted) genuinely has a resume state to pick up from (see
    // backend/crossword_gen.py's `_serialize_resume_state`); any other
    // failure (a lost connection, an internal error, clue generation
    // failing) has nothing meaningful to resume, so the button stays
    // hidden for those, leaving "submit the form again" as the only option
    // exactly as before this feature.
    if (err instanceof GenerationFailedError && err.errorCode === "no_fillable_grid") {
      continueBtn.dataset.jobId = err.jobId;
      continueBtn.hidden = false;
    }
  } finally {
    button.disabled = false;
    stopBtn.hidden = true;
    currentJobId = null;
    generationInProgress = false;
    syncRssPanelVisibility();
  }
}

form.addEventListener("submit", async (event) => {
  event.preventDefault();

  const language = languageSelect.value;
  const width = Number(widthInput.value);
  const height = Number(heightInput.value);
  const difficulty = document.getElementById("difficulty").value;
  const mode = document.getElementById("mode").value;
  const blackEnrichmentPercent = Number(blackEnrichmentInput.value);
  const forceLettersPercent = Number(document.getElementById("force-letters").value);

  await runGeneration(async (t) => {
    let response;
    try {
      response = await fetchWithTimeout("/api/generate", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          language, width, height, difficulty, mode,
          black_enrichment_percent: blackEnrichmentPercent,
          force_letters_percent: forceLettersPercent,
        }),
      }, FETCH_TIMEOUT_MS);
    } catch (err) {
      throw new Error(t.errorConnectionLost);
    }
    const data = await response.json();
    if (!response.ok) {
      throw new Error(describeErrorCode(t, data.detail && data.detail.code, data.detail));
    }
    return data.job_id;
  });
});

// "Continuer" button, at the user's explicit request: relaunches another
// `attempts` (200 by default) paliers from the exact state the just-failed
// job left off at (backend/app.py's POST /api/generate/continue/{job_id}),
// rather than starting a brand new generation from a blank grid. Reuses
// `runGeneration()` — same setup/polling/rendering/error handling as the
// form's own submit handler, only the HTTP call that starts the job
// differs. `continueBtn.dataset.jobId` (set in runGeneration()'s own catch
// block above) always refers to whichever job most recently failed with
// "no_fillable_grid" — if this new attempt fails the same way again, that
// same catch block updates it to the new job's own id, so clicking
// "Continuer" repeatedly keeps chaining from the latest failure rather
// than always retrying the original one.
continueBtn.addEventListener("click", async () => {
  const jobId = continueBtn.dataset.jobId;
  if (!jobId) return;

  await runGeneration(async (t) => {
    let response;
    try {
      response = await fetchWithTimeout(
        `/api/generate/continue/${jobId}`, { method: "POST" }, FETCH_TIMEOUT_MS,
      );
    } catch (err) {
      throw new Error(t.errorConnectionLost);
    }
    const data = await response.json();
    if (!response.ok) {
      throw new Error(describeErrorCode(t, data.detail && data.detail.code, data.detail));
    }
    return data.job_id;
  });
});

// Fire-and-forget: the actual UI transition (hiding stopBtn, showing the
// final status) happens once pollJob()'s own loop sees status "cancelled"
// on its next poll, not here — this only asks the backend to stop.
stopBtn.addEventListener("click", async () => {
  if (!currentJobId) return;
  const t = I18N[uiLanguage];
  stopBtn.disabled = true;
  setStatus(t.statusCancelling, false);
  try {
    await fetchWithTimeout(`/api/generate/cancel/${currentJobId}`, { method: "POST" }, FETCH_TIMEOUT_MS);
  } catch (err) {
    // Best-effort — if the connection itself is down, pollJob()'s own
    // error handling will surface that on its next poll anyway.
  }
});
