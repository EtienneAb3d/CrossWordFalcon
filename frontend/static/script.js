const BLACK = "#";
const WHITE = ".";

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
  document.querySelectorAll("[data-i18n-title]").forEach((el) => {
    const key = el.getAttribute("data-i18n-title");
    if (t[key]) el.setAttribute("title", t[key]);
  });
  renderSystemInfoTooltip();
  // Only redraws the idle-state placeholder (or the selected word's own
  // clue, if a cell is clicked — see renderHoverDefinitionForSelection),
  // never a live hover definition (that's puzzle content, in the grid's
  // own language, not interface chrome — see highlightWordAt() below).
  if (!hoveredGridCell) renderHoverDefinitionForSelection();
  renderRssList();
  // "x online" — parameterized text (see i18n.js), re-applied on a
  // language change from the last known value.
  renderOnlineCount();
  // CPU/GPU meters — same reason: the labels ("CPU"/"GPU N") are
  // translated, so re-render from the last known value on a language switch.
  renderResourceMeters();
  // Queue-length rows — same reason: their labels are translated too.
  renderQueueLengths();
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

// What #hover-definition should show when NOTHING is hovered: at the
// user's request, the clue of the currently *selected* (clicked) word —
// the word running through the selected cell in the current fill
// direction (activeDirection), the same word the light-green band marks
// — rather than the idle placeholder. Falls back to the placeholder when
// there is no grid, no selection, the solution is shown, or the selected
// cell has no real word in that direction. Reuses the matching
// .clue-segment's own text (those spans stay in the DOM even while the
// clue lists are hidden), the same source highlightWordAt() uses on hover.
function renderHoverDefinitionForSelection() {
  // #hover-definition-row (this panel's own wrapper) is hidden entirely
  // in "Interactif" mode (see applyDefinitionsVisibility) — the play-mode
  // definition/direction block is not shown there at all, only the
  // interactive-specific #interactive-definition-input/Proposer/Vérifier
  // block. Nothing to compute; just keep this element in a sane, idle
  // state in case it's ever revealed again.
  if (interactiveMode) {
    renderHoverDefinitionPlaceholder();
    return;
  }
  if (puzzle && selected && !showSolution) {
    const cells = wordCellsAt(selected.row, selected.col, activeDirection);
    if (cells.length >= 2) {
      const start = cells[0];
      const segment = document.querySelector(
        `.clue-segment[data-row="${start.row}"][data-col="${start.col}"][data-direction="${activeDirection}"]`,
      );
      if (segment) {
        hoverDefinition.textContent = segment.textContent;
        hoverDefinition.classList.remove("placeholder");
        return;
      }
    }
  }
  renderHoverDefinitionPlaceholder();
}

const form = document.getElementById("generate-form");
const languageSelect = document.getElementById("language");
const bilingualLanguageSelect = document.getElementById("bilingual-language");
const welcomeOverlay = document.getElementById("welcome-overlay");
const welcomeExperimentalNotice = document.getElementById("welcome-experimental-notice");
const welcomeForm = document.getElementById("welcome-form");
const welcomeLanguageSelect = document.getElementById("welcome-language");
const welcomePseudoInput = document.getElementById("welcome-pseudo");
const welcomeSecretInput = document.getElementById("welcome-secret");
const welcomeAcceptBtn = document.getElementById("welcome-accept-btn");
const userPseudoBtn = document.getElementById("user-pseudo");
const button = document.getElementById("generate-btn");
const status = document.getElementById("status");
const result = document.getElementById("result");
const stats = document.getElementById("stats");
const generationTimes = document.getElementById("generation-times-text");
const generationTimesPrevBtn = document.getElementById("generation-times-prev-btn");
const generationTimesNextBtn = document.getElementById("generation-times-next-btn");
const generationTimesPosition = document.getElementById("generation-times-position");
const gridEl = document.getElementById("grid");
const gridColumn = document.getElementById("grid-column");
const hoverDefinition = document.getElementById("hover-definition");
// The flex row wrapping #hover-definition and its own duplicated
// direction-selector buttons (see #hover-definition-row's own comment
// in style.css) — the JS-measured-width workaround below now targets
// this wrapper, not #hover-definition alone, so the whole row (text
// plus buttons) matches #grid's own rendered width.
const hoverDefinitionRow = document.getElementById("hover-definition-row");
const cluesAcross = document.getElementById("clues-across");
const cluesDown = document.getElementById("clues-down");
const solutionBtn = document.getElementById("solution-btn");
const checkBtn = document.getElementById("check-btn");
const definitionsBtn = document.getElementById("definitions-btn");
const recomputeBtn = document.getElementById("recompute-btn");
const stopBtn = document.getElementById("stop-btn");
// Gold-medal counter of genuinely successful grids, shown in the left
// margin while an automatic generation runs (job["success_count"]).
const successMedal = document.getElementById("success-medal");
const successMedalCount = document.getElementById("success-medal-count");
const continueBtn = document.getElementById("continue-btn");
const cluesEl = document.getElementById("clues");
const downCluesSection = document.getElementById("down-clues-section");
const versionBadge = document.getElementById("version-badge");
const onlineCountEl = document.getElementById("online-count");
const infoBadge = document.getElementById("info-badge");
const infoTooltip = document.getElementById("info-tooltip");
const infoTooltipLines = document.getElementById("info-tooltip-lines");
const resourceMetersEl = document.getElementById("resource-meters");
const queueLengthsEl = document.getElementById("queue-lengths");
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
const liveCluesWrap = document.getElementById("live-clues-wrap");
const liveCluesList = document.getElementById("live-clues-list");
const gridTitleEl = document.getElementById("grid-title");
const gridTitleTextEl = document.getElementById("grid-title-text");
const gridDifficultyEl = document.getElementById("grid-difficulty");
const gridTimerEl = document.getElementById("grid-timer");
const libraryBtn = document.getElementById("library-btn");
const libraryPanel = document.getElementById("library");
const libraryRefreshBtn = document.getElementById("library-refresh-btn");
const libraryCloseBtn = document.getElementById("library-close-btn");
const libraryLanguageFilter = document.getElementById("library-language-filter");
const libraryDifficultyFilter = document.getElementById("library-difficulty-filter");
const librarySeenFilter = document.getElementById("library-seen-filter");
const libraryTbody = document.getElementById("library-tbody");
const libraryEmpty = document.getElementById("library-empty");
const libraryPagination = document.getElementById("library-pagination");
const libraryPrevBtn = document.getElementById("library-prev-btn");
const libraryNextBtn = document.getElementById("library-next-btn");
const libraryPosition = document.getElementById("library-position");
const interactiveWorkBtn = document.getElementById("interactive-work-btn");
const interactiveWorkPanel = document.getElementById("interactive-work");
const interactiveWorkRefreshBtn = document.getElementById("interactive-work-refresh-btn");
const interactiveWorkCloseBtn = document.getElementById("interactive-work-close-btn");
const interactiveWorkTbody = document.getElementById("interactive-work-tbody");
const interactiveWorkEmpty = document.getElementById("interactive-work-empty");
const dictionaryBtn = document.getElementById("dictionary-btn");
const dictionaryPanel = document.getElementById("dictionary");
const dictionaryForm = document.getElementById("dictionary-form");
const dictionaryInput = document.getElementById("dictionary-input");
const dictionarySearchBtn = document.getElementById("dictionary-search-btn");
const dictionarySimilarBtn = document.getElementById("dictionary-similar-btn");
const dictionarySynonymsBtn = document.getElementById("dictionary-synonyms-btn");
const dictionaryDefineBtn = document.getElementById("dictionary-define-btn");
const dictionaryResults = document.getElementById("dictionary-results");
const dictionaryClearBtn = document.getElementById("dictionary-clear-btn");
const dictionaryPerplexityBtn = document.getElementById("dictionary-perplexity-btn");
const dictionaryCloseBtn = document.getElementById("dictionary-close-btn");
const dictionaryLanguage = document.getElementById("dictionary-language");
const paraphraseBtn = document.getElementById("paraphrase-btn");
const paraphrasePanel = document.getElementById("paraphrase");
const paraphraseForm = document.getElementById("paraphrase-form");
const paraphraseInput = document.getElementById("paraphrase-input");
const paraphraseGenerateBtn = document.getElementById("paraphrase-generate-btn");
const paraphraseResults = document.getElementById("paraphrase-results");
const paraphraseClearBtn = document.getElementById("paraphrase-clear-btn");
const paraphrasePerplexityBtn = document.getElementById("paraphrase-perplexity-btn");
const paraphraseCloseBtn = document.getElementById("paraphrase-close-btn");
const paraphraseLanguage = document.getElementById("paraphrase-language");
const qdrantAdminBtn = document.getElementById("qdrant-admin-btn");
const qdrantAdminPanel = document.getElementById("qdrant-admin");
const qdrantAdminBody = document.getElementById("qdrant-admin-body");
const qdrantAdminRefreshBtn = document.getElementById("qdrant-admin-refresh-btn");
const qdrantAdminCloseBtn = document.getElementById("qdrant-admin-close-btn");
const rssPanel = document.getElementById("rss-panel");
const virtualKeyboardEl = document.getElementById("virtual-keyboard");
const virtualKeyboardToggleBtn = document.getElementById("virtual-keyboard-toggle-btn");
const virtualKeyboardRows = document.getElementById("virtual-keyboard-rows");
const virtualKeyboardAcrossBtn = document.getElementById("virtual-keyboard-across-btn");
const virtualKeyboardDownBtn = document.getElementById("virtual-keyboard-down-btn");
// Duplicate of the same pair, next to "Verticalement" under the grid, at
// the user's explicit request: "duplicate the virtual keyboard's own
// direction selector to put it to the right of the definitions below the
// grid." Both pairs drive and reflect the exact same activeDirection
// state (see below) — clicking either pair's own buttons keeps all 4 in
// sync.
const cluesDirectionAcrossBtn = document.getElementById("clues-direction-across-btn");
const cluesDirectionDownBtn = document.getElementById("clues-direction-down-btn");
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
const chatbotResetBtn = document.getElementById("chatbot-reset-btn");
const chatbotInput = document.getElementById("chatbot-input");
const widthInput = document.getElementById("width");
const heightInput = document.getElementById("height");
const blackEnrichmentInput = document.getElementById("black-enrichment");
// "Thématique" field — shared const so both the generation form's submit
// handler and Interactive mode's own enterInteractiveMode() (re-filling
// it from a re-edited grid's own origin theme, at the user's explicit
// request) can reach it without each re-querying the DOM. Same "+/-"
// chip-list mechanic as "Mots Défi (personnalisation)" just below — see
// #theme-field in index.html and renderThemeList()/addThemeWord() further
// down: `themeInput` itself only ever holds the pending, not-yet-added
// word; the real list lives in `themeKeywords` (not `themeWords` — that
// name is already taken elsewhere for a slot's own candidate-word list,
// e.g. renderInteractiveWords()'s own parameter), joined back into one
// space-separated string wherever a `theme` request field is built (the
// backend's own tokenizer, `_theme_tokens`, splits on whitespace/
// punctuation anyway, so this round-trips losslessly).
const themeInput = document.getElementById("theme");
const themeAddBtn = document.getElementById("theme-add-btn");
const themeList = document.getElementById("theme-list");
let themeKeywords = [];

// "Mots Défi (personnalisation)" mini-form on the main generation form —
// see #generate-challenge-panel in index.html, and renderGenerateChallengeList()/
// addGenerateChallengeWord() further down for the logic (a much simpler
// cousin of the Interactive-mode panel below: no grid yet to color/
// click-insert against before generation).
const generateChallengeInput = document.getElementById("generate-challenge-input");
const generateChallengeAddBtn = document.getElementById("generate-challenge-add-btn");
const generateChallengeList = document.getElementById("generate-challenge-list");
let generateChallengeWords = [];

// "Interactif" authoring mode controls (see the Interactive-mode section
// further down).
const interactiveControls = document.getElementById("interactive-controls");
// Every "results" zone (status message, answers, "Proposer une
// définition", Sauvegarder...) — a full-width sibling of #board, toggled
// in lockstep with interactiveControls (see enterInteractiveMode()/
// hideInteractivePanel()) but no longer nested inside it, at the user's
// own explicit follow-up correction — see style.css's own comment on
// #interactive-results.
const interactiveResults = document.getElementById("interactive-results");
const interactivePrevBtn = document.getElementById("interactive-prev-btn");
const interactiveNextBtn = document.getElementById("interactive-next-btn");
const interactiveChallengePanel = document.getElementById("interactive-challenge-panel");
const interactiveChallengeInput = document.getElementById("interactive-challenge-input");
const interactiveChallengeAddBtn = document.getElementById("interactive-challenge-add-btn");
const interactiveChallengeList = document.getElementById("interactive-challenge-list");
const interactiveCellStats = document.getElementById("interactive-cell-stats");
const interactiveBlackStat = document.getElementById("interactive-black-stat");
const interactiveFillStat = document.getElementById("interactive-fill-stat");
const interactiveMessage = document.getElementById("interactive-message");
const interactiveDirAcrossBtn = document.getElementById("interactive-dir-across-btn");
const interactiveDirDownBtn = document.getElementById("interactive-dir-down-btn");
const interactiveCleanBtn = document.getElementById("interactive-clean-btn");
const interactiveCleanDeepBtn = document.getElementById("interactive-clean-deep-btn");
const interactiveDefinitionInput = document.getElementById("interactive-definition-input");
const interactiveProposeBtn = document.getElementById("interactive-propose-btn");
const interactiveCorrectBtn = document.getElementById("interactive-correct-btn");
const interactiveProposeClearBtn = document.getElementById("interactive-propose-clear-btn");
const interactiveStatsBtn = document.getElementById("interactive-stats-btn");
const interactiveImpossibleBtn = document.getElementById("interactive-impossible-btn");
const interactiveVerifyBtn = document.getElementById("interactive-verify-btn");
const interactiveDefinitionsBtn = document.getElementById("interactive-definitions-btn");
const interactiveFinishZoneBtn = document.getElementById("interactive-finish-zone-btn");
const interactiveFinishBtn = document.getElementById("interactive-finish-btn");
const interactiveResultsClearBtn = document.getElementById("interactive-results-clear-btn");
const interactiveHelpBtn = document.getElementById("interactive-help-btn");
const interactiveHelpOverlay = document.getElementById("interactive-help-overlay");
const interactiveHelpCloseBtn = document.getElementById("interactive-help-close-btn");
const interactiveHelpList = document.getElementById("interactive-help-list");
const interactiveWordsBtn = document.getElementById("interactive-words-btn");
const interactiveCrossingBtn = document.getElementById("interactive-crossing-btn");
const interactiveStartBtn = document.getElementById("interactive-start-btn");
const interactiveEndBtn = document.getElementById("interactive-end-btn");
// Shared "Mots"/"Croisés" answer zone — see renderInteractiveWords()/
// renderInteractiveCrossing() below, both prepend their own block here.
const interactiveAnswers = document.getElementById("interactive-answers");
const interactiveProposeResults = document.getElementById("interactive-propose-results");
const interactiveVerifyReportEl = document.getElementById("interactive-verify-report");
const interactiveTitleRow = document.getElementById("interactive-title-row");
const interactiveTitleInput = document.getElementById("interactive-title-input");
const interactiveTitleProposeBtn = document.getElementById("interactive-title-propose-btn");
const interactiveTitleCorrectBtn = document.getElementById("interactive-title-correct-btn");
const interactiveTitleProposeClearBtn = document.getElementById("interactive-title-propose-clear-btn");
const interactiveTitleProposeResults = document.getElementById("interactive-title-propose-results");
const interactiveDraftSaveBtn = document.getElementById("interactive-draft-save-btn");
const interactiveSaveBtn = document.getElementById("interactive-save-btn");
const interactiveSaveResult = document.getElementById("interactive-save-result");

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

// Localizes one {kind, model} role entry (see backend/system_info.py —
// "llm_auto"/"llm_interactive"/"embedding") into its display label. The
// backend never returns pre-translated text, only these plain kind codes,
// so every UI language renders its own wording from the same raw data.
function systemInfoRoleLabel(t, role) {
  if (role.kind === "llm_auto") return t.systemInfoRoleLlmAuto(role.model);
  if (role.kind === "llm_interactive") return t.systemInfoRoleLlmInteractive(role.model);
  if (role.kind === "embedding") return t.systemInfoRoleEmbedding(role.model);
  return role.model;
}

// One line per detected GPU (0, 1, ... — this project's own dev host has
// two, see backend/system_info.py), each followed by one line per model
// role assigned to it (automatic-generation LLM, interactive/on-demand
// LLM, the embedding model) — plus a RAM and a CPU-count line, and a
// "CPU:" section for any role that isn't on a GPU at all — at the user's
// explicit request. Replaces the single "Modèle LLM: X" + "GPU: Y" pair
// this tooltip originally showed for one card only.
function renderSystemInfoTooltip() {
  if (!systemInfo) return;
  const t = I18N[uiLanguage];
  const lines = [];
  const gpus = systemInfo.gpus || [];
  if (gpus.length) {
    gpus.forEach((gpu) => {
      const gb = gpu.vram_mb ? Math.round(gpu.vram_mb / 1024) : null;
      lines.push(
        gb === null
          ? t.systemInfoGpuLineNoVram(gpu.index, gpu.name)
          : systemInfo.unified_memory
            ? t.systemInfoGpuLineUnified(gpu.index, gpu.name, gb)
            : t.systemInfoGpuLine(gpu.index, gpu.name, gb),
      );
      (gpu.roles || []).forEach((role) => lines.push(`- ${systemInfoRoleLabel(t, role)}`));
    });
  } else {
    lines.push(t.systemInfoComputeCpu);
  }
  if ((systemInfo.cpu_roles || []).length) {
    lines.push(t.systemInfoCpuHeading);
    systemInfo.cpu_roles.forEach((role) => lines.push(`- ${systemInfoRoleLabel(t, role)}`));
  }
  if (systemInfo.ram_total_mb) {
    lines.push(t.systemInfoRam(Math.round(systemInfo.ram_total_mb / 1024)));
  }
  if (systemInfo.cpu_count) {
    lines.push(t.systemInfoCpuCount(systemInfo.cpu_count));
  }
  infoTooltipLines.replaceChildren(
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
    // Red "experimental site" notice on the welcome panel — see
    // backend/app.py's EXPERIMENTAL_NOTICE. Left visible by the markup's
    // own default (no `hidden` attribute) and only ever hidden here, once
    // a stable deployment's own `experimental_notice: false` is
    // confirmed — a failed/slow fetch (the `.catch` below) never touches
    // it, so the warning fails safe (shown) rather than silently
    // disappearing.
    if (data && data.experimental_notice === false) {
      welcomeExperimentalNotice.hidden = true;
    }
  })
  .catch(() => {});

// "Actu Croisée" panel (crossword-specialized RSS feeds — see
// fetch_rss_feeds.py/backend/app.py), at the user's explicit request.
// Loaded once, at page startup — no live refresh during the session, the
// back end itself only refreshes once a day (see its own
// _rss_daily_scheduler).
let rssItems = [];

// Allows a small subset of HTML tags/attributes from an RSS feed's raw
// content (third-party, untrusted) before inserting it into the page —
// never a direct innerHTML of the raw content, at the user's explicit
// request: "make sure any links shown are clickable and open in a new
// tab." Parsed via the browser's real DOMParser (never a regex-based
// approach on an HTML string, too easy to bypass), then rebuilds a new
// tree containing only explicitly allowed elements/attributes — anything
// else (an unlisted tag, any "on*" attribute, any attribute absent from
// its own tag's allowlist) is silently dropped rather than copied as-is.
// An <a> keeps its "href" only if it starts with http(s):/mailto: (never
// "javascript:" or an unknown scheme) and systematically gets
// target="_blank" rel="noopener noreferrer" forced onto it, whether or
// not it already had them in the original feed.
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
      // Disallowed tag: still keep its own text/child content (a
      // <script>/<style> normally has no relevant "visible" child at all,
      // but a plain, unrecognized <font>/<center> does have content worth
      // preserving) — only the tag itself is dropped, not what it
      // contained.
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
  // Language filter (see #rss-language-filter), at the user's explicit
  // request — "all" ("All languages", at the top of the list) excludes
  // nothing; any other value keeps only articles of that exact language
  // (see fetch_rss_feeds.py's own "language" field).
  const filterLang = rssLanguageFilter.value;
  // An entry may be monolingual (item.language is a string) or
  // multilingual (item.language is an array of language codes, e.g.
  // WordsCroisés which is EN+FR and must show under both filters).
  const itemMatchesLang = (item, lang) => Array.isArray(item.language)
    ? item.language.includes(lang)
    : item.language === lang;
  let filteredItems = filterLang === "all"
    ? rssItems
    : rssItems.filter((item) => itemMatchesLang(item, filterLang));
  // Falls back to English if the chosen language has no article at all,
  // at the user's explicit request ("show the English list" plus a
  // message explaining it at the top of the list) — never when the
  // chosen language is already "en" or "all" (the fallback itself, or
  // already showing everything), so it can never loop back on itself nor
  // hide a genuinely global "no articles" state (the English fallback
  // itself being empty too).
  let showFallbackNotice = false;
  if (!filteredItems.length && filterLang !== "all" && filterLang !== "en") {
    const englishItems = rssItems.filter((item) => itemMatchesLang(item, "en"));
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
    // Small icon on the left indicating the entry's own origin, at the
    // user's explicit request: "add a small icon on the left showing
    // whether this is a link to a web page or an RSS item." A plain
    // Unicode character (like #virtual-keyboard's own "⌨"/"→"/"↓"
    // elsewhere in this file), never an external image/icon — 🔗 for
    // "grid" (a click opens the grid's own external page directly), 📰
    // for "rss" (a click opens the internal article preview) — the same
    // "rss" fallback used elsewhere for an item with no `kind` at all (a
    // cache written before this field was added).
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
    // Line under the title: for an RSS entry, this is the feed's own
    // name (extra information — which blog the article comes from). For
    // a "grid" entry (direct link), the source's own name is already in
    // the title itself (e.g. "Fox News – Saturday…") — this line would
    // be redundant, so at the user's explicit request it shows the URL
    // that actually gets opened on click instead.
    const source = document.createElement("div");
    source.className = isGrid ? "rss-item-source rss-item-url" : "rss-item-source";
    source.textContent = isGrid ? item.link : item.source;
    textWrap.appendChild(title);
    textWrap.appendChild(source);
    li.appendChild(textWrap);
    // Two origins merged into the same journal, at the user's explicit
    // request ("add the SCRAPP entries to the front page's journal,
    // knowing that this time, a click goes straight to the grid's own
    // page — no article to show on our own site"): an RSS entry
    // (item.kind === "rss", or absent — a fallback for a cache already
    // written before this field was added) always opens the existing
    // internal preview; a "grid" entry (fetch_grid_links.py/SCRAPP)
    // opens the grid's own external URL directly in a new tab, with no
    // preview at all. (`isGrid` already computed above, for the icon.)
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

// Closes on Escape, at the user's explicit request — checks
// `!rssDetail.hidden` first, no cost or effect when the overlay isn't
// open anyway.
document.addEventListener("keydown", (event) => {
  if (!rssDetail.hidden && event.key === "Escape") {
    closeRssDetail();
  }
});

// Default value = the interface's current language when the page loads,
// at the user's explicit request ("by default, the interface's
// language"). Now also resynchronized on every later interface-language
// change too (see languageSelect's own "change" listener further below)
// — a deliberate reversal of the initial decision ("never
// resynchronized... so as not to overwrite a player's own choice"),
// following a later, explicit request from the user: "when the user
// changes the interface language, the news feed's language should follow."
rssLanguageFilter.value = uiLanguage;
rssLanguageFilter.addEventListener("change", renderRssList);

// The "Actu Croisée" panel (#rss-panel) disappears as soon as one of the
// three central panels (#library, #attempt-preview, #result) is shown,
// at the user's explicit request: "the news journal should disappear
// whenever something else needs to be shown, e.g. the Library (currently
// the Library displays below the journal)." A deliberate reversal of an
// earlier request from this same project (the panel had become a
// sibling of these three sections, never hidden by them, specifically so
// it would no longer disappear — see further up in this file): the
// user's most recent preference wins. Called from every place that
// toggles one of the three panels' own visibility rather than
// duplicating this logic — a single source of truth for the "at least
// one of the three is visible => hide the journal" rule.
// True for the whole duration of a grid generation (see runGeneration()
// further below), not only once one of the three central panels is
// actually visible — fixes a real gap reported by the user: "the news
// panel should disappear whenever something else needs to be shown in
// the central area (Library, grid generation, etc)." Between the form
// submission and the very first preview event received from the back
// end, neither #library, #attempt-preview, nor #result is visible yet —
// without this flag, syncRssPanelVisibility() would have wrongly let the
// journal reappear during that window.
let generationInProgress = false;

// Declared here (not with the rest of the "Interactif" state block below)
// because syncRssPanelVisibility() reads it and is called at top level,
// long before that block — a `let` referenced before its declaration is a
// temporal-dead-zone ReferenceError that would halt the rest of the script.
let interactiveMode = false;

function syncRssPanelVisibility() {
  rssPanel.hidden = generationInProgress || interactiveMode
    || !(libraryPanel.hidden && dictionaryPanel.hidden && paraphrasePanel.hidden
         && qdrantAdminPanel.hidden
         && interactiveWorkPanel.hidden && attemptPreview.hidden && result.hidden);
}
// Explicit initial state rather than relying on a mere coincidence
// between index.html's own default `hidden` state and this rule.
syncRssPanelVisibility();

// Merges the two sources into a single chronological journal, at the
// user's explicit request — two independent fetches (Promise.allSettled:
// one failing must never prevent showing what the other genuinely
// returned), gathered then sorted once by descending publication date
// (the same sort criterion each origin script already applies
// server-side — redone here since merging two already-sorted lists
// doesn't, on its own, guarantee the combined result stays sorted).
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
// "what is the selected word" refers to whichever word the mouse is
// currently over (hover), never the click-to-type target — David FALCON
// (see buildChatUiContext()) is told about both, under clearly separate
// labels, precisely so it doesn't conflate the two.
let hoveredWord = null;

// The single, shared "which direction is currently active" state, at the
// user's explicit request: the virtual keyboard's own direction buttons
// ("a virtual keyboard... plus an arrow pointing down to configure the
// vertical direction... and an arrow pointing right to configure the
// horizontal direction") were duplicated next to "Verticalement" under the
// grid, and both pairs, plus Shift/CapsLock, now drive and reflect this
// one variable — a persistent mode (CAPS-LOCK-like, not SHIFT-held: a
// clicked button can't really be "held down") rather than a per-key
// state, exactly like the virtual keyboard's own original design. Grid
// hover (see the mouseenter listener in renderGrid()) reads this
// directly too, rather than the live modifier state of the mouseenter
// event alone, so hovering the grid always respects whichever direction
// the player last chose — by clicking a button or via Shift/CapsLock —
// rather than the mouse-move event's own momentary key state overriding
// it: "the grid's own selection (mouse over) should follow the direction
// selectors."
let activeDirection = "across";

// ---- "Interactif" authoring mode state ----
// interactiveMode itself is declared earlier (near generationInProgress),
// because syncRssPanelVisibility() reads it before this point. It gates
// every fast-path added to selectCell/renderGrid/handleKeydown/
// typeVirtualLetter/setActiveDirection/updateHoverForModifierKey; when
// false the normal play-mode code runs unchanged.
let interactiveJobId = null;
// [row][col] -> "#" (black) | "" (empty white) | "A".."Z" (filled white).
let interactiveGrid = [];
// Deep-copied snapshots of interactiveGrid; the first is pushed on entry
// so length <= 1 means "nothing left to undo".
let interactiveUndoStack = [];
// "Mots Défi" — free-form list of words the author wants to force into
// the grid (see #interactive-challenge-panel in index.html). Kept exactly
// as typed — accents/case and all, "comme dans les dictionnaires," at the
// user's explicit request — never reduced to interactiveGrid's own bare-
// uppercase cell convention here; challengeWordGridForm() derives that
// grid form on demand wherever a comparison against actual cells is
// needed (an earlier version stripped straight to bare A-Z at input time,
// which silently discarded an accented letter outright instead of
// folding it to its base letter — "randonnées" ended up stored as
// "RANDONNES"). "Mots"/"Croisés"/"Début"/"Fin" test candidates against
// this list's own grid forms purely client-side (a challenge word need
// not even be a real dictionary entry, so filtering the backend's own
// theme_words/other_words arrays would silently drop it) — but the list
// itself IS sent to the backend, verbatim, on every "Suivant" click
// (interactive-next-btn's own handler, below) so the placed word is drawn
// from it first, ahead of the theme glossary — the backend derives its
// own grid form there too (`challenge_word_grid_form`), never trusting a
// pre-stripped value — and it IS persisted to GRID_WORK on every
// autosave/"Sauvegarder" (autosaveInteractiveWork()) and restored on
// resume (see enterInteractiveMode()).
let interactiveChallengeWords = [];
let interactiveHasTheme = false;
let interactiveLanguage = "fr";
// The session's own second (vertical-words) language on a genuinely
// bilingual "Interactif" grid — "" (falsy) for an ordinary monolingual
// one, mirroring interactiveLanguage's own always-a-string convention.
// Read from backend/app.py's job["result"]["bilingual_language"] on every
// entry path (fresh start, "Ouvrir en mode Interactif", resuming a
// draft) — see enterInteractiveMode() — and sent back on "Suivant"'s own
// wire format via syncPuzzleFromInteractive()'s puzzle.bilingual_language.
let interactiveBilingualLanguage = "";
let interactiveDifficulty = "easy";
let interactiveTheme = "";
// "startRow,startCol,direction" -> clue text.
let interactiveDefs = new Map();
// "row,col" for every cell of a word placed automatically ("Suivant" /
// the first word on entry) that came from the theme glossary — shown in
// magenta letters in renderGrid(), at the user's explicit request.
// Pruned on every renderInteractive() to cells that still carry a letter,
// so undo/erase/toggle-black drop the mark naturally.
let interactiveThemeCells = new Set();
// Same idea, for a word placed automatically that came from "Mots Défi"
// instead (backend's `placed.from_challenge`, crossword_gen.py's
// interactive_place_word) — shown in green letters in renderGrid(), at
// the user's explicit request ("les Mots Défi doivent être affichés en
// vert, comme sur la grille du mode Interactif"). Mutually exclusive
// with interactiveThemeCells by construction (a placed word is never
// both at once — see backend's own `from_theme`/`from_challenge`
// comments), maintained the same way (pruned on every renderInteractive()
// to cells still carrying a letter).
let interactiveChallengeCells = new Set();
// A click-dragged "zone" of the grid (Édition mode only), at the user's
// explicit request: "add the ability to click-and-drag to select a zone
// of the grid: select every emplacement that shares at least one letter
// with the selected zone (show the cells that aren't part of the
// selection with a gray background)." A `Set` of "row,col" strings — the
// raw dragged rectangle already EXPANDED to every full emplacement
// (across/down) touching it (see interactiveExpandZoneSelection()) — or
// `null` when nothing is selected. Drives both the gray "not part of the
// selection" overlay in renderGrid() and, when non-null, what "Finir la
// zone" sends as its own `zone_cells` (see interactiveFinishZoneBtn below).
// A plain click INSIDE the zone leaves it completely untouched (so the
// player can keep working — typing, "Croisés", "Mots"... — without
// losing it); only a genuine new drag replaces it, the Escape key/
// leaving Interactive mode clears it outright, or a plain click on a
// grayed-out ("not part of the zone") cell deselects it, at the user's
// explicit request: "when the user clicks a grayed-out cell, deselect it."
let interactiveZoneSelection = null;
// Live drag-tracking state (module-level rather than per-listener, since
// the drag itself spans several cells'/the document's own mouse events).
let interactiveDragStart = null; // { row, col } while a drag is in progress, else null
let interactiveDragCurrent = null; // { row, col }, updated as the pointer moves
let interactiveDragActive = false;
// "row,col" for every cell already carrying a letter in `interactiveGrid`
// at the moment "Finir la grille" is clicked — highlighted in light green
// in every attempt-preview grid of the automatic generation that button
// launches (renderAttemptPreview()'s own .finish-locked overlay, see
// style.css), at the user's explicit request: "permanently locking the
// already-placed letters (shown framed in light green in the previews)."
// `null` (not merely empty) whenever no such generation is
// in progress — reset only by a genuinely fresh, unrelated generation (the
// plain form submit handler), never by "Continuer" (which resumes THIS
// SAME finish job) nor by runGeneration()'s own generic reset.
let finishLockedCells = null;
// "row,col" keys for the backend-computed fill diagnostics of the LAST
// auto-placement (start / "Suivant") — impossible slots (no candidate word
// left at all) and slots below the fill-option threshold — shown in
// renderGrid() the same way the attempt previews do. Snapshots: cleared on
// any manual edit (they only describe the state the backend last saw).
let interactiveImpossibleCells = new Set();
let interactiveLowCells = new Set();
// Always a subset of interactiveImpossibleCells: the exact crossing
// cell(s) where two still-open slots' own remaining candidates share no
// letter at all (backend's Filler._crossing_deadlock_slots), as opposed
// to the rest of those same two slots' cells, impossible only by
// association — shown in a more vivid red, at the user's explicit
// request: "la case ... devrait être en rouge vif (plus vif que les mots
// impossibles qui passent par cette case)." Same staleness rule as
// interactiveImpossibleCells/LowCells above.
let interactiveDeadlockCells = new Set();
// "Emplacements écartés" (DOC_ALGO/FR/Lexicon.md): slots the last
// "Suivant" click set aside because no word could go on them without
// creating an impossible one. Shown on a weaker yellow background than
// the red/orange above, and never subtracted from them — a cell can be
// both écarté and (genuinely) impossible at once, in which case the
// stronger signal wins the background through the CSS cascade, exactly
// as in the attempt previews. Unlike an impossible slot, an écarté one
// never forbids placing a word that crosses it. Same staleness rule as
// interactiveImpossibleCells/LowCells above.
let interactiveExcludedCells = new Set();
// The candidate slots of the last "Suivant" click — the level-6 window of
// the slot-selection cascade (backend `Filler._select_target_slot`), the
// slots closest to the grid's center the cascade chose among — each shown
// by its own cell closest to the center, outlined blue. Same staleness
// rule as the sets above.
let interactiveWindowCells = new Set();
// The word the last "Suivant" click placed, exactly as the backend
// reported it (`POST /api/interactive/step`'s own `placed`: the word, its
// cells, its direction, and which glossary it came from) — kept only so
// the autosave can record it alongside the diagnostics below (see
// interactiveDiagnosticsPayload), never used for rendering: the grid
// already shows the letters, and their theme/"Mots Défi" colouring comes
// from interactiveThemeCells/interactiveChallengeCells. Same staleness
// rule as interactiveImpossibleCells/LowCells above.
let interactiveLastPlaced = null;
// "Stats" button: "row,col" -> the statistically most probable letter for
// that still-empty cell, from POST /api/interactive/stats (backend's
// `_interactive_letter_stats`, the same crossed across/down tally the
// automatic search and its previews read). Shown in light gray in
// renderGrid(). Same staleness rule as interactiveImpossibleCells/
// LowCells above: cleared on any manual edit, since it describes a grid
// state the backend may no longer recognize.
let interactiveStatLetters = new Map();
// "Stats" is a two-state toggle, on by default: while on, every
// renderInteractive() whose grid differs from the one the letters above
// were fetched for (`interactiveStatsGridKey`, reset to null whenever the
// letters are cleared) refetches them, debounced, so they always describe
// the grid on screen (scheduleInteractiveStatsRefresh).
let interactiveStatsOn = true;
let interactiveStatsGridKey = null;
let interactiveStatsTimer = null;
const INTERACTIVE_STATS_DEBOUNCE_MS = 300;
// "Vérifier" button: cells of every complete word flagged as a problem —
// either not a real dictionary word (checked server-side, POST /api/
// interactive/verify) or missing a definition (checked client-side against
// interactiveDefs) — at the user's explicit request: "check the whole
// grid and mark in red every complete word that has a problem."
// Same staleness rule as interactiveImpossibleCells/LowCells above: cleared
// on any manual edit, since it describes a check run against a past grid
// state that may no longer be accurate.
let interactiveInvalidCells = new Set();
// Same "Vérifier" check as above, but the detailed, one-line-per-word
// report shown below the definition input, at the user's explicit
// request: "a report showing the problems found on each word." Each
// entry is `{direction, row, col, answer, reasons}` — only
// words that actually have a problem are ever included (an empty array
// means either nothing has been verified yet, or the last check found
// nothing wrong). Same staleness rule as interactiveInvalidCells: cleared
// on any manual edit.
let interactiveVerifyReport = [];
// Set true once the LLM has been asked for a title, so it isn't re-asked
// automatically on every completeness re-check.
let interactiveTitleProposed = false;

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
  renderHoverDefinitionForSelection();
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
// user's explicit request for a fixed 5-line panel under the grid, so a
// player can read the currently-hovered word's definition without the
// full across/down clue lists in view at the same time.
// The "two selected languages" for both the Dictionary AND the
// Paraphraser, at the user's explicit request: "when two languages are
// selected, add '<lang1>/<lang2>' to the Dictionary's own language
// selector [...] Same for the Paraphraser." Source of truth, in order:
// the currently loaded grid if it's bilingual (`puzzle.language`/
// `puzzle.bilingual_language` — see backend/crossword_gen.py's
// generate_grid; the player may have changed the form's own selectors
// since generating/loading this specific grid, so only the grid itself
// knows which language(s) it was actually written in), otherwise the
// generation form's own two selectors (#language/#bilingual-language) if
// they currently differ. Returns `[lang1, lang2]` (in this order — lang1
// = the across/primary words' language, lang2 = the down/secondary
// words' language) or `null` if only a single language is currently in
// play. Shared by both panels, never duplicated: the two can therefore
// never disagree about "how many languages are configured."
function currentBilingualLangs() {
  if (puzzle && puzzle.bilingual_language && puzzle.bilingual_language !== puzzle.language) {
    return [puzzle.language, puzzle.bilingual_language];
  }
  if (bilingualLanguageSelect.value && bilingualLanguageSelect.value !== languageSelect.value) {
    return [languageSelect.value, bilingualLanguageSelect.value];
  }
  return null;
}

// A plain language code's own native label (never a combination), read
// directly from one of `selectEl`'s 6 base `<option>`s (e.g. "fr" ->
// "Français") rather than duplicated here — these six options are never
// translated by uiLanguage (see index.html, identical on #dictionary-
// language AND #paraphrase-language), so this label stays stable
// regardless of the interface's own language, and regardless of which
// selector is used.
function nativeLanguageLabel(selectEl, lang) {
  const opt = selectEl.querySelector(`option[value="${lang}"]`);
  return opt ? opt.textContent : lang;
}

// Adds/removes/refreshes, on `selectEl` (#dictionary-language or
// #paraphrase-language), the combined "<lang1>/<lang2>" option matching
// currentBilingualLangs() above — at the user's explicit request. Finds
// whichever option a previous call already added via
// `data-bilingual-combo` (never more than one at a time) and removes it
// before recomputing, so it can never accumulate duplicates or leave a
// now-stale language combination behind. Preserves the current selection
// when it's still valid; if it pointed at the old combination (or there's
// no combination left at all), falls back respectively to the new
// combination or to the interface's own language — never to a value that
// no longer matches any `<option>`. Returns the combination's own value
// ("lang1/lang2") if one is available, `null` otherwise — used by
// defaultToBilingualOption() below to decide whether to select it
// automatically.
function refreshBilingualOption(selectEl) {
  const previousValue = selectEl.value;
  const existing = selectEl.querySelector("option[data-bilingual-combo]");
  if (existing) existing.remove();
  const langs = currentBilingualLangs();
  if (!langs) {
    if (previousValue.includes("/")) selectEl.value = uiLanguage;
    return null;
  }
  const [lang1, lang2] = langs;
  const value = `${lang1}/${lang2}`;
  const opt = document.createElement("option");
  opt.value = value;
  opt.textContent = `${nativeLanguageLabel(selectEl, lang1)}/${nativeLanguageLabel(selectEl, lang2)}`;
  opt.dataset.bilingualCombo = "1";
  selectEl.appendChild(opt);
  if (previousValue === value || previousValue.includes("/")) {
    selectEl.value = value;
  } else {
    selectEl.value = previousValue;
  }
  return value;
}

// Like refreshBilingualOption() above, but also selects the bilingual
// combination by default as soon as it exists — at the user's explicit
// request: "On Dictionnaire and Paraphraseur, if bilingual, select the
// language pair by default." Used at a panel's "default" entry points
// (opening it, a grid being loaded, a language change);
// updateDictionaryLanguageForDirection() below (triggered by a plain word
// hover in the grid) deliberately calls refreshBilingualOption() alone,
// never this one, so a plain hover never overwrites a single-language
// choice the player made in the meantime — see its own note.
function defaultToBilingualOption(selectEl) {
  const value = refreshBilingualOption(selectEl);
  if (value) selectEl.value = value;
}

// Follows the "Dictionnaire" panel's own language selector to the
// language of the hovered/selected word in the playable grid, at the
// user's explicit request: "the dictionary's language selector should
// automatically adapt to the language depending on the grid's selection
// direction." Uses the languages actually recorded on the grid itself
// (`puzzle.language`/`puzzle.bilingual_language` — see backend/
// crossword_gen.py's generate_grid), not the generation form's own
// selectors: the player may have changed those after generating/loading
// this specific grid, so only the grid itself knows which language(s) it
// was actually written in. No effect on an ordinary monolingual grid
// (`puzzle.bilingual_language` then `null`/absent): `direction === "down"`
// simply falls back to the same primary language as "across". Never
// forces the selector out of an already-selected bilingual combination
// (whether picked by default or explicitly by the player, see
// refreshBilingualOption()/defaultToBilingualOption() above) — a plain
// hover must never cancel that choice.
function updateDictionaryLanguageForDirection(direction) {
  if (!puzzle) return;
  refreshBilingualOption(dictionaryLanguage);
  if (dictionaryLanguage.value.includes("/")) return;
  const lang = (direction === "down" && puzzle.bilingual_language)
    ? puzzle.bilingual_language
    : (puzzle.language || languageSelect.value);
  if (lang) dictionaryLanguage.value = lang;
}

// Re-syncs (and selects the combination by default, see
// defaultToBilingualOption() above) both "bilingual" selectors
// (Dictionnaire and Paraphraseur) whenever the generation form's own
// "Bilingue" selector changes — even while the matching panels are
// currently closed (DOM work with no visible effect, so no real cost).
// #language has its own equivalent call at the end of setUiLanguage()
// (see further below) — no need to duplicate a second listener here,
// since every change to #language already goes through it.
bilingualLanguageSelect.addEventListener("change", () => {
  defaultToBilingualOption(dictionaryLanguage);
  defaultToBilingualOption(paraphraseLanguage);
});

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
  updateDictionaryLanguageForDirection(direction);
  const selector = `.clue-segment[data-row="${start.row}"][data-col="${start.col}"][data-direction="${direction}"]`;
  const segment = document.querySelector(selector);
  if (segment) {
    segment.classList.add("hover-highlight");
    hoverDefinition.textContent = segment.textContent;
    hoverDefinition.classList.remove("placeholder");
  }
}

// Light-green background on every cell of the word running through the
// clicked cell, in the current fill direction (`activeDirection`), at the
// user's explicit request — the clicked cell itself is deliberately
// excluded so it keeps its own solid-blue `.selected` style. Operates on
// the live `cellElements` map (rebuilt by renderGrid()), so it's called
// both at the end of renderGrid() and from setActiveDirection() (so
// flipping Across/Down updates the highlight without a full rebuild).
function applySelectedWordHighlight() {
  cellElements.forEach((el) => el.classList.remove("selected-word"));
  if (showSolution || !selected || !puzzle || !isWhite(selected.row, selected.col)) return;
  const cells = wordCellsAt(selected.row, selected.col, activeDirection);
  for (const { row, col } of cells) {
    if (row === selected.row && col === selected.col) continue;
    const el = cellElements.get(`${row},${col}`);
    if (el) el.classList.add("selected-word");
  }
  updateDictionaryInputForSelection(cells);
}

// Auto-fills the Dictionary panel's own search field with the word
// currently selected (the clicked cell, in the current activeDirection),
// whenever that word is already entirely filled in — at the user's
// explicit request: "quand un emplacement sélectionné change, si cet
// emplacement possède toutes ses lettres remplies, renseigner
// automatiquement le champ de saisie du Dictionnaire avec le mot
// complet." Works the same way in ordinary play mode (userLetters) and
// in "Interactif" mode (interactiveGrid) — `cells` is whatever
// applySelectedWordHighlight() already resolved for the selected word,
// reused here rather than recomputed. Left completely untouched (never
// cleared) whenever the word isn't fully typed yet, or nothing is
// selected — #dictionary-input is a plain, transient search field, safe
// to leave showing whatever the player last typed/looked up.
function updateDictionaryInputForSelection(cells) {
  if (!cells || cells.length < 2) return;
  let answer = "";
  for (const { row, col } of cells) {
    const letter = interactiveMode ? interactiveGrid[row][col] : userLetters[row][col];
    if (!letter || letter === "#") return;
    answer += letter;
  }
  dictionaryInput.value = answer;
}

// True when the keyboard focus is inside a text field (the chat box, the
// generation-form number inputs, any future one) — used to keep grid
// keyboard shortcuts from firing while the player is typing elsewhere.
// Reported live: pressing Shift to type a capital in #chatbot-input was
// flipping the grid's active direction (across <-> down) via
// updateHoverForModifierKey below, which in turn re-resolved the hovered
// word to the crossing word in the other direction — on a bilingual grid,
// the other language — so the chatbot then answered about the wrong word
// in the wrong language.
function isTextInputFocused() {
  const el = document.activeElement;
  return !!el && (el.tagName === "INPUT" || el.tagName === "TEXTAREA" || el.isContentEditable);
}

// A real, non-collapsed text selection anywhere on the page (e.g. the
// player selected some dictionary/paraphrase/chat text to copy it) — even
// though nothing is focused in that case (a plain text selection doesn't
// move document.activeElement), the grid must still not swallow the
// keystroke: reported live, Ctrl+C to copy a selected definition was
// instead typing "C" into the currently-selected grid cell and blocking
// the browser's own copy, because isTextInputFocused() alone saw no
// focused input and let the grid handle it.
function hasActiveTextSelection() {
  const sel = window.getSelection && window.getSelection();
  return !!sel && !sel.isCollapsed && sel.toString().length > 0;
}

function shouldGridIgnoreKeydown() {
  return isTextInputFocused() || hasActiveTextSelection();
}

// Vertical word on Shift or CapsLock (either one), horizontal otherwise —
// getModifierState() is part of the DOM's shared modifier-key mixin, so it
// works on a MouseEvent (mouseenter) exactly like on a KeyboardEvent, no
// separate key-tracking state needed.
function hoverDirectionFromEvent(event) {
  return event.getModifierState("Shift") || event.getModifierState("CapsLock") ? "down" : "across";
}

// Updates activeDirection and every one of its 4 buttons (both the
// virtual keyboard's own pair and its duplicate next to "Verticalement",
// see their shared declaration above) at once, at the user's explicit
// request that both button pairs stay in sync with each other and with
// Shift/CapsLock. Also refreshes, immediately (not only on the next
// mouse move / re-render), everything that depends on the direction for
// the cell currently in focus: the hovered word's highlight if the
// mouse is over the grid, the light-green selected-word band, and — when
// nothing is hovered but a cell is selected — the definition panel under
// the grid, so it follows the new direction's word through that cell.
function setActiveDirection(direction) {
  activeDirection = direction;
  virtualKeyboardAcrossBtn.classList.toggle("active", direction === "across");
  virtualKeyboardDownBtn.classList.toggle("active", direction === "down");
  cluesDirectionAcrossBtn.classList.toggle("active", direction === "across");
  cluesDirectionDownBtn.classList.toggle("active", direction === "down");
  if (interactiveDirAcrossBtn) interactiveDirAcrossBtn.classList.toggle("active", direction === "across");
  if (interactiveDirDownBtn) interactiveDirDownBtn.classList.toggle("active", direction === "down");
  if (interactiveMode) {
    renderInteractive();
    return;
  }
  if (hoveredGridCell) highlightWordAt(hoveredGridCell.row, hoveredGridCell.col, direction);
  else renderHoverDefinitionForSelection();
  applySelectedWordHighlight();
}

// Shift/CapsLock can be toggled at any time, mouse over the grid or not —
// re-evaluate activeDirection on every key change (not gated on a cell
// being hovered, unlike the earlier version of this listener:
// setActiveDirection() itself already handles refreshing the hover
// highlight when there is one) so both button pairs stay in sync with
// the physical modifier key too, per the user's explicit request.
document.addEventListener("keydown", updateHoverForModifierKey);
document.addEventListener("keyup", updateHoverForModifierKey);

function updateHoverForModifierKey(event) {
  if (interactiveMode) return;
  if (event.key !== "Shift" && event.key !== "CapsLock") return;
  // Ignore Shift/CapsLock while typing in the chat (or any text field), or
  // while some page text is selected — otherwise typing a capital there
  // silently changes the grid's active direction and the currently-hovered
  // word (see shouldGridIgnoreKeydown()).
  if (shouldGridIgnoreKeydown()) return;
  setActiveDirection(hoverDirectionFromEvent(event));
}

// Ctrl flips the fill direction (across <-> down) on every press, in both
// play mode and Interactive mode, at the user's explicit request. Unlike
// Shift/CapsLock (a held modifier — down while held, across while
// released, mirroring the lowercase/uppercase typing convention), Ctrl is
// a plain press-to-toggle: each tap swaps the direction. Guarded like the
// other grid shortcuts — ignored while a text field has focus, so Ctrl+C /
// Ctrl+A in the chat box or a form input aren't hijacked — and event.repeat
// is skipped so holding Ctrl down doesn't flip it over and over. Only fires
// once the page actually has a grid (a loaded puzzle or Interactive mode),
// so it's a no-op on the plain generation form. preventDefault() is never
// called, so browser Ctrl shortcuts (Ctrl+T, Ctrl+R, ...) still work.
document.addEventListener("keydown", toggleDirectionOnCtrl);

function toggleDirectionOnCtrl(event) {
  if (event.key !== "Control" || event.repeat) return;
  if (shouldGridIgnoreKeydown()) return;
  if (!puzzle && !interactiveMode) return;
  setActiveDirection(activeDirection === "across" ? "down" : "across");
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
// cell can be found by this overlay too — the other overlays
// (.forced/.low-candidates/.noise/.theme) never receive a black-cell
// coordinate from any current backend caller, but finding one harmlessly
// no-ops since their own CSS rules stay scoped to `.white`.
// Each `theme_cells` (crossword_gen.py's `_theme_word_cells`, possibly
// empty — always empty for a non-themed generation) is likewise an array
// of [row, col] pairs, the cells of every slot whose assigned word comes
// from the themed-generation glossary (generate_grid's `priority_words`) —
// their letter is coloured bright magenta + bold (--theme-fg, see
// style.css's .cell.white.theme), at the user's explicit request, so the
// theme words stand out sharply against the black letters in the preview.
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
    deadlock_cells: deadlockCells,
    excluded_cells: excludedCells,
    forced_cells: forcedCells,
    locked_cells: lockedCells,
    low_candidate_cells: lowCandidateCells,
    noise_cells: noiseCells,
    theme_cells: themeCells,
    challenge_cells: challengeCells,
    process_number: processNumber,
    is_best: isBest,
    live_status: liveStatus,
    budget_percent: budgetPercent,
    stat_letters: statLetters,
  } of examples) {
    if (!exampleGrid || !exampleGrid.length) continue;
    const height = exampleGrid.length;
    const width = exampleGrid[0].length;
    const impossibleSet = new Set((impossibleCells || []).map(([r, c]) => `${r},${c}`));
    // The most probable letter of every still-empty cell (backend/
    // crossword_gen.py's `Filler.stat_letters`, the same crossed across/
    // down tally as Interactive mode's "Stats"), carried by the live tiles
    // and the attempt's own key step only — `|| []` everywhere else.
    const statLetterMap = new Map((statLetters || []).map(([r, c, l]) => [`${r},${c}`, l]));
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
    // Live-preview-only tile border (`live_status`, see backend/
    // crossword_gen.py's `on_live_preview` and style.css's own
    // `.live-computing`/`.live-succeeded`/`.live-failed` rules), at the
    // user's explicit request — `undefined` on every previewHistory
    // entry (that field only ever rides on the ephemeral live channel),
    // so this is a no-op there, same `|| []`-style convention as every
    // other optional field this function already reads.
    if (liveStatus === "computing") miniGrid.classList.add("live-computing");
    else if (liveStatus === "succeeded") miniGrid.classList.add("live-succeeded");
    else if (liveStatus === "failed") miniGrid.classList.add("live-failed");
    else if (liveStatus === "interrupted") miniGrid.classList.add("live-interrupted");
    miniGrid.style.gridTemplateColumns = `repeat(${width}, 1.1rem)`;
    const cellElementsByCoord = new Map();
    // Rates shown above each grid, at the user's explicit request — black
    // cells / total, white cells already carrying a real letter / total,
    // and cells deemed unplayable / total (the same denominator for all
    // three, so they stay directly comparable). An undetermined white
    // cell ("." in example_grid) never counts as "filled," whether
    // showPreviewLetters shows its letter or not — this rate reflects the
    // search's real progress, not what the player currently sees on
    // screen. The unplayable-cell rate comes straight from `impossibleSet`
    // (already computed above for the .impossible class) — not a second
    // computation.
    let blackCount = 0;
    let filledCount = 0;
    for (let r = 0; r < height; r++) {
      for (let c = 0; c < width; c++) {
        const ch = exampleGrid[r][c];
        const cell = document.createElement("div");
        if (ch === BLACK) {
          cell.className = "cell black";
          blackCount++;
          // Registered too (see crossword_gen.py's
          // `_optimize_before_cleanup`, at the user's explicit request) —
          // `locked_cells` can now name a black cell (one bordering a
          // still-entirely-empty slot, protected from removal during this
          // optimization), not only white cells like this mechanism's
          // other callers. Without this registration, the cell was
          // unreachable by the overlay pass further below
          // (`cellElementsByCoord.get(...)` returned `undefined`), so it
          // was never highlighted — reported directly by the user: "We
          // should also see the locked white and black cells, but they
          // aren't outlined."
          cellElementsByCoord.set(`${r},${c}`, cell);
        } else {
          cell.className = "cell white";
          if (ch !== ".") filledCount++;
          if (showPreviewLetters && ch !== ".") cell.textContent = ch;
          // Light-gray statistical letter in a still-empty cell — a hint
          // about the solution like the real letters, so it follows the
          // same "Voir" toggle.
          if (showPreviewLetters && ch === ".") {
            const suggested = statLetterMap.get(`${r},${c}`);
            if (suggested) {
              const hint = document.createElement("span");
              hint.className = "preview-stat-letter";
              hint.textContent = suggested;
              cell.appendChild(hint);
            }
          }
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
    // An "emplacement écarté": a slot this attempt found unable to take
    // any word at least once, so its own search only comes back to it
    // once no other slot can take one (backend/crossword_gen.py's
    // `Filler.excluded_zone_cells`) — "impossible détecté, mais non
    // définitif". Never subtracted
    // from `impossibleCells`/`deadlockCells` on the backend side — a cell
    // in both simply ends up with both classes, and the CSS cascade order
    // (`.low-candidates` then `.excluded` then `.noise`/`.impossible`/
    // `.deadlock` in style.css) lets whichever stronger signal applies
    // win the background, regardless of the order these classes are
    // added here: yellow outranks orange, red still outranks yellow.
    for (const [r, c] of excludedCells || []) {
      const cell = cellElementsByCoord.get(`${r},${c}`);
      if (cell) cell.classList.add("excluded");
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
    // Always a subset of impossibleCells above — the exact crossing
    // cell(s) of a crossing-letter deadlock (two still-open slots whose
    // own remaining candidates share no letter at all), shown in a more
    // vivid red than the rest of the same impossible slot(s), at the
    // user's explicit request: "les prévisualisations [doivent montrer]
    // les cases impossibles en rouge vif... comme sur le mode
    // Interactif" (see .interactive-deadlock, already built for that
    // mode). `|| []` is a no-op whenever this specific case never
    // occurred (backend/crossword_gen.py's `Filler.deadlock_zone_cells`).
    for (const [r, c] of deadlockCells || []) {
      const cell = cellElementsByCoord.get(`${r},${c}`);
      if (cell) cell.classList.add("deadlock");
    }
    // Green letters for words coming from the theme glossary, at the
    // user's explicit request: "In the preview grids, show words coming
    // from the theme glossary in green letters." Always empty for a
    // non-themed generation (see backend/crossword_gen.py's
    // `_theme_word_cells`), so `|| []` is a no-op everywhere else, same as
    // the overlays above. A text color, which composes cleanly with the
    // .impossible/.noise/.low-candidates backgrounds and the
    // .forced/.locked borders already in place.
    for (const [r, c] of themeCells || []) {
      const cell = cellElementsByCoord.get(`${r},${c}`);
      if (cell) cell.classList.add("theme");
    }
    // Green letters for words coming from "Mots Défi" instead, at the
    // user's explicit request: "les Mots Défi doivent être affichés en
    // vert, comme sur la grille du mode Interactif." Always empty for a
    // generation with no "Mots Défi" list (see backend/crossword_gen.py's
    // `_challenge_word_cells_from_assignment`), so `|| []` is a no-op
    // everywhere else, same as themeCells above.
    for (const [r, c] of challengeCells || []) {
      const cell = cellElementsByCoord.get(`${r},${c}`);
      if (cell) cell.classList.add("challenge");
    }
    // "Finir la grille": a light-green frame around every letter already
    // placed manually before this generation was launched (see style.css's
    // own .finish-locked rule) — `finishLockedCells` (module-level) is only
    // ever set while a generation started by that button is running,
    // `null` for every other generation, so this whole block is a no-op
    // there.
    if (finishLockedCells) {
      for (const key of finishLockedCells) {
        const cell = cellElementsByCoord.get(key);
        if (cell) cell.classList.add("finish-locked");
      }
    }
    const totalCells = height * width;
    const blackPercent = Math.round((100 * blackCount) / totalCells);
    const fillPercent = Math.round((100 * filledCount) / totalCells);
    const impossiblePercent = Math.round((100 * impossibleSet.size) / totalCells);
    const stats = document.createElement("p");
    stats.className = "attempt-preview-stats";
    // Dimmed text lives in its own span (rather than directly in `stats`)
    // so the pencil button appended below — a sibling, not a descendant of
    // this span — stays at full opacity: CSS `opacity` dims an entire
    // rendered subtree, so a child button couldn't undo it with its own
    // `opacity: 1` if it lived inside this same dimmed element.
    const statsText = document.createElement("span");
    statsText.className = "attempt-preview-stats-text";
    // Bold prefix with the number of the process that actually produced
    // this grid (backend/crossword_gen.py's own `process_number`, see its
    // own docstring), at the user's explicit request: "lets you track a
    // grid that changes position from one cycle to the next." Absent
    // (`null`/`undefined`) for the one preview that never has a real
    // process behind it (the very first palier of a generation, before
    // any real submission) — in that case, no prefix at all rather than a
    // made-up number.
    if (processNumber != null) {
      const processLabel = document.createElement("strong");
      processLabel.className = "attempt-preview-process";
      processLabel.textContent = I18N[uiLanguage].attemptPreviewProcessLabel(processNumber);
      statsText.appendChild(processLabel);
      statsText.appendChild(document.createTextNode(" "));
    }
    statsText.appendChild(
      document.createTextNode(I18N[uiLanguage].attemptPreviewStats(blackPercent, fillPercent, impossiblePercent))
    );
    // Per-process budget-consumption percentage, at the user's explicit
    // request: "Afficher le taux de budget consommé par le process sur la
    // ligne d'info de chaque grille Live (à gauche du bouton icône
    // crayon)." Only ever present on the live channel (`live_status` is
    // `undefined` on every navigable `previewHistory` entry — see
    // CLAUDE.md's own "Scoped to one attempt's own lifecycle" section),
    // so `typeof budgetPercent === "number"` is a no-op there, the same
    // optional-field convention as every other example field this
    // function reads. Appended to the same dimmed `statsText` span (not a
    // separate sibling) so it reads as part of the same secondary-stat
    // line, still to the left of the pencil button appended below.
    if (typeof budgetPercent === "number") {
      statsText.appendChild(
        document.createTextNode(I18N[uiLanguage].attemptPreviewBudgetPercent(budgetPercent))
      );
    }
    stats.appendChild(statsText);
    // Pencil icon button, at the user's explicit request: "à droite des
    // mentions de remplissage des prévisualisations... ajouter un bouton
    // icône crayon (comme sur la liste de la Bibliothèque) permettant de
    // reprendre n'importe quelle grille de l'historique en mode
    // Interactif." Same icon/markup as the Library panel's own
    // `.library-interactive-btn` (script.js's renderLibraryList()), reusing
    // its i18n key (`libraryInteractiveText`) since it's the exact same
    // action — open this grid in "Interactif" mode as a new "Créations"
    // draft — just triggered from a different place. See
    // openAttemptPreviewInteractive() below and POST /api/interactive/
    // from-attempt.
    const openInteractiveBtn = document.createElement("button");
    openInteractiveBtn.type = "button";
    openInteractiveBtn.className = "attempt-preview-interactive-btn";
    openInteractiveBtn.setAttribute("aria-label", I18N[uiLanguage].libraryInteractiveText);
    openInteractiveBtn.title = I18N[uiLanguage].libraryInteractiveText;
    openInteractiveBtn.innerHTML =
      '<svg class="interactive-icon" viewBox="0 0 24 24" width="14" height="14" ' +
      'aria-hidden="true" focusable="false">' +
      '<path d="M4 20h4L18.5 9.5l-4-4L4 16v4z" fill="none" stroke="currentColor" ' +
      'stroke-width="1.6" stroke-linejoin="round"/>' +
      '<path d="M13.5 6.5l4 4" fill="none" stroke="currentColor" stroke-width="1.6"/>' +
      "</svg>";
    openInteractiveBtn.addEventListener("click", (event) => {
      event.stopPropagation();
      openAttemptPreviewInteractive(exampleGrid);
    });
    stats.appendChild(openInteractiveBtn);
    item.appendChild(stats);
    item.appendChild(miniGrid);
    attemptPreviewGrids.appendChild(item);
  }
  attemptPreview.hidden = false;
  syncRssPanelVisibility();
}

// Backs the attempt-preview pencil icon button above — at the user's
// explicit request, opens whichever attempt grid is currently on screen
// (live or from the history scrubber alike, since `exampleGrid` is
// whatever this specific row was rendered with) in "Interactif" mode as a
// brand-new "Créations" draft. `language`/`bilingual_language`/
// `difficulty`/`theme`/`challenge_words` are read straight off the
// generation form's own live fields/lists rather than kept in a separate
// snapshot variable, since nothing resets them until the next "Générer"
// submit — the same fields the still-running (or already finished)
// generation job itself was started with. If a generation is still
// running, its job is cancelled first (same call as the "Stop" button)
// so it doesn't keep occupying a queue slot once the player has moved on
// to editing this attempt by hand.
async function openAttemptPreviewInteractive(exampleGrid) {
  const jobToCancel = currentJobId;
  if (jobToCancel) {
    try {
      await fetchWithTimeout(`/api/generate/cancel/${jobToCancel}`, { method: "POST" }, FETCH_TIMEOUT_MS);
    } catch (err) {
      // Best-effort, same tolerance as stopBtn's own cancel — a connection
      // hiccup here shouldn't block opening this attempt in Interactif.
    }
  }
  const language = languageSelect.value;
  const bilingualLanguage = bilingualLanguageSelect.value;
  const difficulty = document.getElementById("difficulty").value;
  const theme = themeKeywords.join(" ");
  await runInteractive(
    {
      grid: exampleGrid.map((row) => Array.from(row)),
      language,
      bilingual_language: bilingualLanguage !== language ? bilingualLanguage : undefined,
      difficulty,
      theme: theme || undefined,
      challenge_words: generateChallengeWords.length ? generateChallengeWords : undefined,
    },
    "/api/interactive/from-attempt"
  );
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
  renderLiveClues();
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
// Index into previewHistory currently shown on screen — or, while caught
// up (`previewHistoryIndex === previewHistory.length - 1`) and following
// live, only the *last entry actually rendered*: the live view itself,
// past that point, is driven by the separate live_preview channel (see
// renderLivePreview()), not by this index moving any further. As long as
// the player hasn't navigated back manually, advanceLiveDisplay() (called
// from pollJob() on every poll) steps this forward by exactly one entry
// per poll while any unseen key step remains (`showNextPreview()`), at
// the user's explicit request: "si il existe une étape clef qui n'a pas
// encore été affichée, afficher cette étape clef" — so a player who falls
// behind still sees every recorded step in turn, not just the latest one
// each time. showPreviousPreview()/showNextPreview() also move it
// explicitly on a manual click.
let previewHistoryIndex = -1;
// Whether the live view should keep advancing — one key step at a time
// while behind, the live_preview channel once caught up (see
// advanceLiveDisplay()) — true by default (and reset on every new
// generation, see hideAttemptPreview()). Set to `false` the moment the
// player clicks "◀" to look back at an earlier entry (showPreviousPreview()),
// so a poll landing in between doesn't yank their view forward again while
// they're reviewing something — the same "pause autoscroll while scrolled
// up" courtesy a chat/log viewer gives, and exactly the user's own explicit
// request: "si l'utilisateur revient en arrière dans l'historique, l'affichage
// temps réel ne doit pas se faire, jusqu'à ce qu'il revienne sur la dernière
// étape." Resumed by manually clicking "▶" (see its own click listener
// below) — clicking "forward" is read as "I want to keep following again
// from here."
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
  // "Play mode shows arrows for navigating the generation history, which
  // shouldn't be shown when that history is empty" — at the user's
  // explicit request. previewHistory stays empty for a grid loaded from
  // the Library/a shared link/a resumed GRID_GAME (no generation ever
  // happened in this tab — see loadLibraryGrid's own
  // hideAttemptPreview()), so there's nothing to navigate. The single
  // source of truth for this trio's visibility (see enterInteractiveMode()
  // / hideInteractivePanel(), which call this function rather than
  // setting `.hidden` themselves) — always hidden in "Interactif" mode
  // too, which has nothing to do with previewHistory.
  const hasNavigableHistory = !interactiveMode && previewHistory.length > 0;
  generationTimesPrevBtn.hidden = !hasNavigableHistory;
  generationTimesNextBtn.hidden = !hasNavigableHistory;
  generationTimesPosition.hidden = !hasNavigableHistory;
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

// Live "definitions created so far" feed, at the user's explicit request:
// "afficher les définitions créées sous la grille aperçu, si Voir est
// sélectionné, afficher aussi les mots." Every entry backend/app.py's own
// "clues_progress" job field has ever produced during the current
// generation (one {answer, accented, clue} dict per word that actually got
// a clue — see pollJob() below, which appends to this array as new entries
// arrive), never shortened or reset except at the start of a brand new
// generation (hideAttemptPreview()). Unlike lastWordTable/lastPreviewExamples
// this isn't tied to any one previewHistory entry — it's a single, ever-
// growing feed shown regardless of which historical grid the player happens
// to be browsing, since it reflects the clue-generation phase as a whole,
// not one specific search attempt.
let liveClues = [];

function renderLiveClues() {
  liveCluesList.innerHTML = "";
  if (!liveClues.length) {
    liveCluesWrap.hidden = true;
    return;
  }
  for (const entry of liveClues) {
    const li = document.createElement("li");
    // The word/answer itself is the one piece of this list that would
    // spoil the puzzle, so — unlike the definition text right next to it,
    // always shown — it only ever appears once showPreviewLetters ("Voir")
    // is on, mirroring the same reveal convention already used for every
    // other answer-carrying element in this panel (renderWordTable, the
    // preview grids' own letters).
    if (showPreviewLetters) {
      const wordSpan = document.createElement("span");
      wordSpan.className = "live-clue-word";
      wordSpan.textContent = entry.accented || entry.answer;
      li.appendChild(wordSpan);
      li.appendChild(document.createTextNode(" — "));
    }
    const clueSpan = document.createElement("span");
    clueSpan.textContent = entry.clue;
    li.appendChild(clueSpan);
    liveCluesList.appendChild(li);
  }
  liveCluesWrap.hidden = false;
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

// Renders `data.live_preview` (backend/app.py's `_on_live_preview`, see
// generate_grid(on_live_preview=...) in crossword_gen.py) — a
// continuously OVERWRITTEN "what does each grid currently being built
// look like right now" snapshot, published on every new backtrack state
// the search reaches, entirely distinct from `previewHistory`: it never
// touches `previewHistoryIndex`/`previewHistory` itself, at the user's
// explicit request: "Cet état s'affiche dans l'interface au moment de la
// mise à jour du statut (mais pas stocké dans l'historique navigable)."
// Called from advanceLiveDisplay() (itself called from pollJob() on every
// poll), only once the player has caught up to the newest recorded key
// step — see that function's own comment for the full priority order — and
// only while `autoFollowPreview` is true, the user's own further explicit
// requirement: "Si l'utilisateur est remonté dans l'historique navigable,
// l'affichage temps réelle ne doit pas se faire, jusqu'à ce que
// l'utilisateur revienne au dernier état de l'historique" — reusing the
// exact same flag that already pauses previewHistory's own auto-follow
// (showPreviousPreview()), so stepping back to review an earlier state
// also freezes this live channel until the player returns to the live
// edge. `lastPreviewStep` is still updated (from the job's own current
// `step`, not a previewHistory entry's) so #attempt-preview-status
// reflects the true current progress
// rather than whichever historical entry was shown last; no word_table —
// only the "clues" step ever carries one, and that step publishes no
// live_preview of its own (see crossword_gen.py, which only wires this
// callback into the grid-search phase).
function renderLivePreview(examples, step) {
  if (!examples || !examples.length || !autoFollowPreview) return;
  renderAttemptPreview(examples);
  lastPreviewStep = step || lastPreviewStep;
  renderPreviewStatus();
}

// Called from pollJob() with every new attempt-preview state examples_
// history produced since the last poll, however many that is. Every one
// of `newEntries` is unconditionally pushed into `previewHistory` — the
// full, real record, never shortened or skipped, so prev/next navigation
// always has access to everything the backend actually produced. Purely a
// recording step — nothing is rendered here; see advanceLiveDisplay()'s
// own comment, called right after this on every poll, for what (if
// anything) actually reaches the screen this tick.
function recordPreviewHistory(newEntries) {
  if (!newEntries.length) return;
  for (const entry of newEntries) previewHistory.push(entry);
  updatePreviewNavButtons();
}

// Decides what the live view shows on THIS poll, at the user's explicit
// request, in priority order:
// 1. "quand le statut est réfraîchi dans l'interface, si il existe une
//    étape clef (enregistrée dans l'historique) qui n'a pas encore été
//    affichée, afficher cette étape clef" — if the player hasn't caught
//    up to the newest recorded `previewHistory` entry yet, step forward
//    by exactly one (`showNextPreview()`, the same function "▶" already
//    uses) rather than jumping straight to the end — so a player who
//    just fell behind (several key steps recorded since the last poll)
//    still gets to see each one in turn, one per poll, rather than only
//    ever the very latest.
// 2. "si aucune nouvelle étape clef n'est disponible, afficher l'étape
//    Live" — once caught up, render whatever `data.live_preview` (the
//    continuously-overwritten backtrack state, see renderLivePreview()'s
//    own comment) currently holds.
// 3. "quand on arrive à la dernière étape clef ... n'afficher que cette
//    dernière étape clef" — needs no special case here at all: once the
//    search genuinely ends, backend/crossword_gen.py stops publishing
//    live_preview and clears it (see generate_grid's own end-of-search
//    cleanup), so step 2 above naturally renders nothing further —
//    renderLivePreview() already no-ops on an empty/absent `examples`.
// 4. "si l'utilisateur revient en arrière ... n'afficher que l'étape
//    demandée ... jusqu'à ce qu'il revienne sur la dernière étape" —
//    already the existing `autoFollowPreview` contract (set `false` by
//    showPreviousPreview()/showFirstPreview(), resumed once "▶"/"⏭"
//    genuinely lands back on the tail): both showNextPreview() and
//    renderLivePreview() already gate on it themselves, so this function
//    only needs its own outer check to skip the work entirely while
//    paused.
function advanceLiveDisplay(data) {
  if (!autoFollowPreview) return;
  if (previewHistoryIndex < previewHistory.length - 1) {
    showNextPreview();
  } else {
    renderLivePreview(data.live_preview, data.step);
  }
}

// Jumps the live view straight to the newest recorded entry and resumes
// auto-follow unconditionally — used once a job reaches a terminal status
// (done/error/cancelled), so the final, true end state is always shown,
// even if the player had paused on an earlier entry to look back at it
// (recordPreviewHistory() alone only snaps forward while already
// following along).
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
  liveClues = [];
  renderLiveClues();
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
  // In interactive authoring mode ANY cell is selectable (black cells
  // included — the user edits them too); no showSolution/isWhite guard.
  if (interactiveMode) {
    // Reported live by the user: clicking a grid cell right after typing
    // in an interface field (Dictionnaire, Paraphraseur, ChatBot, the
    // definition/title inputs, ...) left that field focused — a plain
    // click on a non-focusable grid cell never moves `document.
    // activeElement` on its own — so every following letter keystroke
    // still went to that field instead of the grid (see
    // isTextInputFocused()/shouldGridIgnoreKeydown(), which handleKeydown
    // already consults). Explicitly reclaiming focus here, the moment a
    // cell is actually clicked, makes the grid the keystroke target again
    // without touching that guard itself — it stays correct for every
    // other case (a real text selection elsewhere, an input the player
    // is still actively using without having clicked the grid).
    if (document.activeElement && document.activeElement !== document.body) {
      document.activeElement.blur();
    }
    selected = { row: r, col: c };
    renderInteractive();
    return;
  }
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

// Arrow keys move the selection to the next white cell in that direction,
// skipping black cells, clamped to the grid (nothing happens at the edge,
// or when only black cells lie beyond), at the user's explicit request —
// matching the arrow-key navigation Interactive mode already has. Unlike
// moveSelection() (used after typing a letter, one direction only), this
// handles all four directions from the currently-selected cell.
function moveSelectionArrow(dr, dc) {
  if (!selected || !puzzle) return;
  const { width, height } = puzzle;
  let { row, col } = selected;
  while (true) {
    row += dr;
    col += dc;
    if (row < 0 || col < 0 || row >= height || col >= width) return;
    if (isWhite(row, col)) {
      selected = { row, col };
      renderGrid();
      return;
    }
  }
}

const ARROW_DELTAS = {
  ArrowUp: [-1, 0],
  ArrowDown: [1, 0],
  ArrowLeft: [0, -1],
  ArrowRight: [0, 1],
};

function handleKeydown(event) {
  // Never intercept a keystroke meant for a focused text field — reported
  // live by the user: with a grid cell selected, clicking into #chatbot-
  // input to type a question still had every letter/Backspace keystroke
  // swallowed here (preventDefault()'d and written into the grid) instead
  // of reaching the chat box. Guards on whatever element is actually
  // focused, generically (any <input>/<textarea>/contenteditable), not
  // just the chat input by id — so any other text field added later is
  // protected the same way, with no need to special-case it here too.
  // Also bails out on a plain page text selection with no focused field at
  // all (see hasActiveTextSelection()) — otherwise Ctrl+C to copy some
  // selected dictionary/paraphrase/chat text typed "C" into the grid and
  // blocked the real copy instead, breaking copy/paste between tools.
  if (shouldGridIgnoreKeydown()) return;
  if (interactiveMode) {
    handleInteractiveKeydown(event);
    return;
  }
  if (!puzzle || !selected || showSolution) return;
  const key = event.key;

  if (ARROW_DELTAS[key]) {
    event.preventDefault();
    moveSelectionArrow(...ARROW_DELTAS[key]);
  } else if (key.length === 1 && /[a-zA-Z]/.test(key)) {
    event.preventDefault();
    const isUpper = key !== key.toLowerCase();
    userLetters[selected.row][selected.col] = key.toUpperCase();
    ensureGridTimerRunning();
    // Auto-advance follows the current selection direction (activeDirection
    // — set by Ctrl, the Across/Down buttons, or Shift/Caps Lock), not just
    // the letter's own case. Typing an uppercase letter still advances down
    // (an uppercase key implies a vertical word, and Shift/Caps Lock have
    // already set activeDirection to "down" anyway) — kept as a fallback
    // for the rare case where Caps Lock was on before the grid opened, so
    // no keydown ever fired to update activeDirection.
    moveSelection(isUpper || activeDirection === "down" ? "down" : "right");
    renderGrid();
    scheduleGridGameSave();
  } else if (key === "Backspace" || key === "Delete") {
    event.preventDefault();
    userLetters[selected.row][selected.col] = "";
    renderGrid();
    scheduleGridGameSave();
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
  renderHoverDefinitionForSelection();

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
        // In interactive authoring mode a black cell is editable too:
        // clickable/selectable, and framed when it is the selected cell.
        if (interactiveMode) {
          if (selected && selected.row === r && selected.col === c) {
            cell.classList.add("selected");
          }
          // Gray-out for "not part of the drag-selected zone" — see
          // interactiveZoneSelection's own docstring. A black cell can
          // never belong to a real emplacement, but it can still be
          // dragged over (part of the raw rectangle) and, being outside
          // any resulting selection either way, is always eligible for
          // this overlay whenever a zone is active.
          if (interactiveZoneSelection && !interactiveZoneSelection.has(`${r},${c}`)) {
            cell.classList.add("zone-unselected");
          }
          cell.addEventListener("click", () => selectCell(r, c));
          attachInteractiveDragHandlers(cell, r, c);
          cellElements.set(`${r},${c}`, cell);
        }
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

      // "Interactif" mode "Stats" button: light-gray suggested letter,
      // shown only while the cell is still genuinely empty (see
      // interactiveStatLetters's own docstring) — an overlay span, never
      // the real letter text node above, so it never gets mistaken for a
      // placed letter and disappears the instant a real one is typed.
      if (interactiveMode && !letter) {
        const suggested = interactiveStatLetters.get(`${r},${c}`);
        if (suggested) {
          const hint = document.createElement("span");
          hint.className = "interactive-stat-letter";
          hint.textContent = suggested;
          cell.appendChild(hint);
        }
      }

      // "Interactif" mode: magenta letter for a word placed automatically
      // from the theme glossary (see interactiveThemeCells).
      if (interactiveMode && letter && interactiveThemeCells.has(`${r},${c}`)) {
        cell.classList.add("interactive-theme");
      }
      // "Interactif" mode: green letter for a word placed automatically
      // from "Mots Défi" (see interactiveChallengeCells) — mutually
      // exclusive with interactive-theme above by construction.
      if (interactiveMode && letter && interactiveChallengeCells.has(`${r},${c}`)) {
        cell.classList.add("interactive-challenge");
      }
      // "Interactif" mode: red/orange background for a slot that's
      // impossible / below the fill-options threshold — same meaning as
      // in the previews (see interactiveImpossibleCells/LowCells).
      if (interactiveMode) {
        const dk = `${r},${c}`;
        // Added on its own, never in the else-chain below: an écarté cell
        // can also be impossible/low, and the CSS cascade arbitrates —
        // yellow outranks .interactive-low's orange, and is in turn
        // outranked by .interactive-impossible/.interactive-deadlock's
        // red (see their declaration order in style.css).
        if (interactiveExcludedCells.has(dk)) cell.classList.add("interactive-excluded");
        if (interactiveWindowCells.has(dk)) cell.classList.add("interactive-window");
        if (interactiveImpossibleCells.has(dk)) cell.classList.add("interactive-impossible");
        else if (interactiveLowCells.has(dk)) cell.classList.add("interactive-low");
        // Always a subset of interactiveImpossibleCells above — the exact
        // crossing cell of a deadlock, shown in a more vivid red on top of
        // the ordinary impossible-slot background (see interactiveDeadlockCells).
        if (interactiveDeadlockCells.has(dk)) cell.classList.add("interactive-deadlock");
        if (interactiveInvalidCells.has(dk)) cell.classList.add("interactive-invalid");
        // Gray-out for "not part of the drag-selected zone" — see
        // interactiveZoneSelection's own docstring/comment above (the
        // matching black-cell branch) for the full reasoning.
        if (interactiveZoneSelection && !interactiveZoneSelection.has(dk)) {
          cell.classList.add("zone-unselected");
        }
      }

      if (!showSolution && checking && userLetters[r][c]) {
        cell.classList.add(userLetters[r][c] === solution[r][c] ? "correct" : "incorrect");
      }
      if (!showSolution && selected && selected.row === r && selected.col === c) {
        cell.classList.add("selected");
      }
      if (!showSolution) {
        cell.addEventListener("click", () => selectCell(r, c));
      }
      if (interactiveMode) attachInteractiveDragHandlers(cell, r, c);
      // Hover word highlighting works the same in every mode (typing,
      // solution shown, checking) — it's a passive reading aid, not tied
      // to the click-to-select input flow above.
      cellElements.set(`${r},${c}`, cell);
      cell.addEventListener("mouseenter", () => {
        hoveredGridCell = { row: r, col: c };
        // Reads the shared activeDirection state rather than this event's
        // own live modifier keys, at the user's explicit request — hover
        // must respect whichever direction was last chosen (button click
        // or Shift/CapsLock), not be silently overridden by whatever the
        // mouse's own modifier state happens to be at this exact moment.
        highlightWordAt(r, c, activeDirection);
      });
      cell.addEventListener("mouseleave", () => {
        hoveredGridCell = null;
        clearHighlights();
      });
      gridEl.appendChild(cell);
    }
  }

  // Light-green highlight of the word running through the clicked cell,
  // now that every cell element exists in `cellElements`.
  applySelectedWordHighlight();

  // #hover-definition-row's CSS width: 100% (stretching to #grid-column's
  // own auto-computed width) turned out not to be enough to make long
  // definitions wrap, even with min-width: 0 on the panel itself — reported
  // live by the user, the box still grew to fit unwrapped text. Root cause:
  // #grid-column is *itself* a flex item (of #board, flex-shrink: 0) with
  // the browser's default min-width: auto, so its own auto-computed width
  // can still be pulled wide by an unwrapped child's natural size before
  // any child-level min-width: 0 gets a chance to matter — a compounding
  // version of the same flexbox gotcha across two nested containers, not
  // fixed by patching only the inner one. Sidesteps the whole
  // auto-sizing/stretch ambiguity by setting #hover-definition-row's width
  // explicitly, in pixels, to #grid's own actual rendered width — read
  // *after* every cell above has been appended, so offsetWidth reflects
  // the grid's final layout, not a partial one. Guaranteed correct
  // regardless of any flex/grid intrinsic-sizing subtlety, since it's an
  // explicit measured value rather than something left for the browser to
  // infer from content. Set on the row (not #hover-definition directly)
  // now that the direction-selector buttons sit next to it in that same
  // row — #hover-definition itself still shrinks correctly within it via
  // its own flex: 1 1 auto/min-width: 0 (see style.css).
  hoverDefinitionRow.style.width = `${gridEl.offsetWidth}px`;
}

// Standard crossword layout: across clues are grouped grid-row by
// grid-row, down clues column by column (not by cell number); several
// clues sharing the same row/column are chained onto the same line of
// text, with no line break.
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
  // In "Interactif" mode we never show the full Horizontalement/
  // Verticalement lists, nor the play mode's own definition/arrows block
  // right under the grid (#hover-definition-row) — at the user's explicit
  // request: "there are two blocks of arrows and definitions... don't show
  // the blocks that belong to play mode... only keep the blocks specific
  // to interactive mode (with the Proposer and Vérifier buttons)," i.e.
  // #interactive-arrows/#interactive-definition-row in
  // #interactive-controls, already the only ones shown in this mode.
  const showLists = showDefinitions && !interactiveMode;
  cluesEl.hidden = !showLists;
  downCluesSection.hidden = !showLists;
  hoverDefinitionRow.hidden = interactiveMode;
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

// Virtual keyboard, at the user's explicit request: "a virtual keyboard
// containing only the 26 uppercase letters of the alphabet in natural
// order over 2 rows, plus a down arrow to set the vertical direction...
// and a right arrow to set the horizontal direction." Its own direction
// buttons drive the shared `activeDirection`/`setActiveDirection()`
// (declared earlier, alongside the hover state it's now unified with)
// rather than a dedicated variable of their own — see that declaration's
// own comment for the full "a persistent mode... rather than a per-key
// state" reasoning, unchanged from this feature's own original design.

function insertAtCursor(input, text) {
  // Inserts `text` at the cursor position (replacing the selection if
  // there is one) and leaves the cursor right after it. On a focused
  // <input type="text">, selectionStart/End are always numbers; the
  // fallback to value.length is only there as a precaution.
  const start = input.selectionStart == null ? input.value.length : input.selectionStart;
  const end = input.selectionEnd == null ? input.value.length : input.selectionEnd;
  input.value = input.value.slice(0, start) + text + input.value.slice(end);
  const pos = start + text.length;
  input.setSelectionRange(pos, pos);
  // In case something ever listens for "input" (no listener today — the
  // dictionary form only acts on submit — but this costs nothing and
  // avoids a surprise later).
  input.dispatchEvent(new Event("input", { bubbles: true }));
}

function typeVirtualLetter(letter) {
  // The virtual keyboard also serves to type a dictionary search, at the
  // user's explicit request. When #dictionary-input has focus, it writes
  // into it (lowercase, matching a real keystroke in that same field —
  // the search is case/accent-insensitive on the backend either way)
  // instead of into the grid. Focus is preserved by the
  // `mousedown`/preventDefault set on every key (see buildVirtualKeyboard):
  // without it, clicking the button would take focus away from the field
  // before ever reaching here.
  if (document.activeElement === dictionaryInput
      || document.activeElement === interactiveDefinitionInput
      || document.activeElement === interactiveTitleInput) {
    insertAtCursor(document.activeElement, letter.toLowerCase());
    return;
  }
  if (interactiveMode) {
    interactiveTypeLetter(letter);
    return;
  }
  // Same guards as handleKeydown(): a clicked letter with no cell
  // selected, or while the solution is shown, does nothing rather than
  // writing into the void or overwriting the solution.
  if (!puzzle || !selected || showSolution) return;
  userLetters[selected.row][selected.col] = letter;
  ensureGridTimerRunning();
  // moveSelection() expects "right" for horizontal, anything else for
  // vertical (see its own definition) — not the same labels as
  // activeDirection ("across"/"down").
  moveSelection(activeDirection === "across" ? "right" : "down");
  renderGrid();
  scheduleGridGameSave();
}

function buildVirtualKeyboard() {
  virtualKeyboardRows.replaceChildren();
  const letters = "ABCDEFGHIJKLMNOPQRSTUVWXYZ";
  // 2 rows of 13 letters each (A-M then N-Z), the requested natural
  // alphabetical order — not a QWERTY/AZERTY layout.
  const rows = [letters.slice(0, 13), letters.slice(13)];
  for (const row of rows) {
    const rowEl = document.createElement("div");
    rowEl.className = "virtual-keyboard-row";
    for (const letter of row) {
      const key = document.createElement("button");
      key.type = "button";
      key.className = "virtual-keyboard-key";
      key.textContent = letter;
      // Prevents the click from stealing focus away from a text field
      // (the dictionary search field): without this, typeVirtualLetter
      // would no longer see #dictionary-input as document.activeElement.
      // Harmless for the grid, which doesn't depend on focus.
      key.addEventListener("mousedown", (e) => e.preventDefault());
      key.addEventListener("click", () => typeVirtualLetter(letter));
      rowEl.appendChild(key);
    }
    virtualKeyboardRows.appendChild(rowEl);
  }
  // "Case noire" key, at the user's explicit request: same effect as the
  // Space bar — toggle the selected cell black, only meaningful in
  // interactive authoring mode.
  const blackKey = document.createElement("button");
  blackKey.type = "button";
  blackKey.className = "virtual-keyboard-key virtual-keyboard-black";
  blackKey.textContent = "■";
  blackKey.setAttribute("data-i18n-aria", "interactiveBlackKey");
  blackKey.setAttribute("aria-label", (I18N[uiLanguage] || {}).interactiveBlackKey || "Case noire");
  blackKey.addEventListener("mousedown", (e) => e.preventDefault());
  blackKey.addEventListener("click", () => {
    if (interactiveMode && selected) interactiveToggleBlack();
  });
  virtualKeyboardRows.lastElementChild.appendChild(blackKey);
}

buildVirtualKeyboard();
// Same as the letter keys: clicking a direction arrow must not steal
// focus away from the dictionary search field (the arrows have no effect
// on the dictionary input, but an accidental click must not break
// whatever the player is currently typing there). Both button pairs
// (the virtual keyboard's own, plus the duplicate under "Verticalement")
// drive the same shared setActiveDirection().
virtualKeyboardAcrossBtn.addEventListener("mousedown", (e) => e.preventDefault());
virtualKeyboardDownBtn.addEventListener("mousedown", (e) => e.preventDefault());
virtualKeyboardAcrossBtn.addEventListener("click", () => setActiveDirection("across"));
virtualKeyboardDownBtn.addEventListener("click", () => setActiveDirection("down"));
cluesDirectionAcrossBtn.addEventListener("mousedown", (e) => e.preventDefault());
cluesDirectionDownBtn.addEventListener("mousedown", (e) => e.preventDefault());
cluesDirectionAcrossBtn.addEventListener("click", () => setActiveDirection("across"));
cluesDirectionDownBtn.addEventListener("click", () => setActiveDirection("down"));
// The grid (or any other content at the bottom of the page) used to get
// hidden behind the expanded virtual keyboard, with no way to scroll
// further down to bring it back above it — reported directly by the
// user. #virtual-keyboard is `position: fixed`, so it's completely
// independent of <main>'s own real height: the page can never scroll any
// further than <main>'s own natural bottom, which has no reason to
// reserve room for a floating widget it knows nothing about. A dedicated
// class on <main>, added/removed in lockstep with the keyboard expanding/
// collapsing, reserves extra space at the bottom of the page only while
// the keyboard is genuinely open — never all the time, so it doesn't
// waste space while it's collapsed (collapsed by default).
const mainEl = document.querySelector("main");
virtualKeyboardToggleBtn.addEventListener("click", () => {
  virtualKeyboardEl.classList.toggle("virtual-keyboard-collapsed");
  mainEl.classList.toggle(
    "keyboard-open-padding",
    !virtualKeyboardEl.classList.contains("virtual-keyboard-collapsed"),
  );
});

const SUPPORTED_UI_LANGS = ["fr", "en", "de", "es", "it", "pt"];
// Pseudo (nickname) chosen in the welcome overlay (see below); "" when
// none is set. Persisted in the cwf-prefs cookie, mirrored into every
// grid this browser generates (POST /api/generate `pseudo`) and into the
// "Mes grilles" library filter.
let userPseudo = "";
// Secret word proving this browser's userPseudo actually belongs to this
// user (see POST /api/pseudo/claim, backend/secret_store.py) — at the
// user's explicit request. "" when none is set yet. Persisted in the
// same cwf-prefs cookie as userPseudo, so a returning visitor who only
// wants to change the UI language never has to retype it.
let userSecret = "";
// `false` until the initUserPrefs() IIFE below has run once — used to
// skip, on that very first setUiLanguage() call, the few re-renders that
// touch state declared later in this file (renderChatWelcome reads
// `chatUserHasSpoken`, a `let` further down — accessing it now would
// throw); the standalone renderChatWelcome() call near its own
// definition handles the initial render regardless.
let uiBootstrapped = false;

// --- "x en ligne" presence counter -----------------------------------
// At the user's explicit request: show, left of the version badge, how
// many distinct active users there are (de-duplicated by pseudo — two
// tabs of the same person count once), refreshed every 2s; a user is
// dropped after 60s without a heartbeat (enforced server-side, see
// backend/app.py's POST /api/presence).
const PRESENCE_INTERVAL_MS = 2000;
const PRESENCE_TIMEOUT_MS = 4000; // short: a heartbeat must not outlive its own interval
const presenceSessionId = (window.crypto && window.crypto.randomUUID)
  ? window.crypto.randomUUID()
  : `${Date.now()}-${Math.random().toString(36).slice(2)}`;
// Last count received; kept so renderOnlineCount() can re-localise the
// "x en ligne" text on a language change without waiting for the next
// heartbeat. `null` = no successful heartbeat yet (badge stays hidden).
let lastOnlineCount = null;

function renderOnlineCount() {
  if (lastOnlineCount === null) return;
  onlineCountEl.textContent = I18N[uiLanguage].onlineCount(lastOnlineCount);
  onlineCountEl.hidden = false;
}

// Small CPU/GPU occupancy meters in the info tooltip, at the user's
// explicit request: "un petit vu-mètre indiquant le taux d'occupation de
// chaque ressource (GPUs / CPU). Un seul vu-mètre pour l'ensemble des
// CPUs." Fed by the same POST /api/presence heartbeat as the online-
// count above (backend/app.py's presence(), resource_usage field —
// sampled server-side on its own timer, never recomputed per request),
// rather than a separate fetch, per the user's own explicit request.
// `lastResourceUsage` is kept raw (not pre-rendered) so a UI-language
// change can redraw the labels without waiting for the next heartbeat —
// the same pattern already used for `systemInfo`/renderSystemInfoTooltip().
let lastResourceUsage = null;

function renderResourceMeters() {
  const usage = lastResourceUsage;
  if (!usage) {
    resourceMetersEl.replaceChildren();
    return;
  }
  const t = I18N[uiLanguage];
  const rows = [];
  if (typeof usage.cpu_percent === "number") {
    rows.push({ label: t.resourceMeterCpuLabel, percent: usage.cpu_percent });
  }
  // Sorted by index defensively — the backend already emits them in
  // order (backend/system_info.py's _nvidia_gpu_utilization()), but
  // nothing here should silently depend on that staying true.
  (usage.gpu_percent || [])
    .slice()
    .sort((a, b) => a.index - b.index)
    .forEach((gpu) => {
      if (typeof gpu.percent === "number") {
        rows.push({ label: t.resourceMeterGpuLabel(gpu.index), percent: gpu.percent });
      }
    });
  resourceMetersEl.replaceChildren(
    ...rows.map((row) => {
      const percent = Math.max(0, Math.min(100, row.percent));
      const wrap = document.createElement("span");
      wrap.className = "resource-meter";
      const label = document.createElement("span");
      label.className = "resource-meter-label";
      label.textContent = row.label;
      const track = document.createElement("span");
      track.className = "resource-meter-track";
      const fill = document.createElement("span");
      fill.className = "resource-meter-fill";
      fill.style.width = `${Math.round(percent)}%`;
      track.appendChild(fill);
      const value = document.createElement("span");
      value.className = "resource-meter-value";
      value.textContent = `${Math.round(percent)}%`;
      wrap.append(label, track, value);
      return wrap;
    })
  );
}

// The two background queues' current length (backend/app.py's GRID_QUEUE/
// CLUES_QUEUE — index 0 is whichever task is running or about to start,
// so a length of 1 means "one active, nothing waiting"), at the user's
// explicit request: "ajouter une indication sur la longueur des 2 files
// d'attente : Grille (CPU) et Définition (GPU)." Fed by the same
// POST /api/presence heartbeat as the resource meters above — see
// `lastQueueLengths`' own "kept raw, redrawn on language switch" pattern,
// identical to `lastResourceUsage`.
let lastQueueLengths = null;

function renderQueueLengths() {
  const lengths = lastQueueLengths;
  if (!lengths) {
    queueLengthsEl.replaceChildren();
    return;
  }
  const t = I18N[uiLanguage];
  const rows = [
    { label: t.queueLengthGridLabel, value: lengths.grid },
    { label: t.queueLengthCluesLabel, value: lengths.clues },
  ].filter((row) => typeof row.value === "number");
  queueLengthsEl.replaceChildren(
    ...rows.map((row) => {
      const wrap = document.createElement("div");
      wrap.className = "queue-length-row";
      const label = document.createElement("span");
      label.textContent = row.label;
      const value = document.createElement("span");
      value.className = "queue-length-value";
      value.textContent = String(row.value);
      wrap.append(label, value);
      return wrap;
    })
  );
}

// The backend (backend/app.py's presence()/_presence_snapshot) is the
// sole decider of whether it writes a LOG_USERS line, comparing the
// LIST of users (not just the total count) against the last one
// recorded — at the user's explicit request: "LOG_USERS doit se mettre
// à jour à chaque fois que la liste des utilisateurs change." (LOG_USERS
// must update every time the list of users changes.) A user going from
// anonymous to named (or changing their pseudo) therefore already
// triggers a new line on this very heartbeat, even when the total count
// doesn't move. This function is called both on page load and right
// after the welcome form is submitted (see welcomeForm's "submit"
// listener) so this recomputation happens right away, without waiting
// for the setInterval's next regular tick (up to PRESENCE_INTERVAL_MS
// later) — but in every case, it's the backend that decides, never a
// flag sent by this client.
async function pingPresence() {
  try {
    const response = await fetchWithTimeout("/api/presence", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        session_id: presenceSessionId,
        pseudo: userPseudo || "",
      }),
    }, PRESENCE_TIMEOUT_MS);
    if (!response.ok) return;
    const data = await response.json();
    if (typeof data.count === "number") {
      lastOnlineCount = data.count;
      renderOnlineCount();
    }
    if (data.resource_usage) {
      lastResourceUsage = data.resource_usage;
      renderResourceMeters();
    }
    if (data.queue_lengths) {
      lastQueueLengths = data.queue_lengths;
      renderQueueLengths();
    }
  } catch (err) {
    // Transient failure — keep showing the last known count; the next
    // heartbeat (2s later) self-corrects.
  }
}

// Applies `lang` everywhere: the interface's own text, both language
// selectors (#language and the welcome overlay's own #welcome-language,
// kept in sync — "deux sélecteurs de langue" per the user's request),
// the bilingual/RSS/library/dictionary language selectors that follow
// the interface language, and every parameterized string that isn't a
// plain [data-i18n] node. Used both by the two selectors' own "change"
// handlers and once at startup.
function setUiLanguage(lang) {
  if (SUPPORTED_UI_LANGS.indexOf(lang) === -1) lang = "fr";
  uiLanguage = lang;
  languageSelect.value = lang;
  welcomeLanguageSelect.value = lang;
  // Bilingual grid, at the user's explicit request: "the first language
  // selector sets the interface language, and forces the second language
  // selector to take the same value." A plain interface-language change
  // therefore always cancels a bilingual choice already made on that
  // selector — the player has to reconfigure "Bilingue" to a different
  // language every time they genuinely want a bilingual grid, rather than
  // that selector staying stuck on an old value that's now inconsistent
  // with the new primary language.
  bilingualLanguageSelect.value = lang;
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
  // The difficulty label to the right of the playable grid's title is
  // translated (see renderGridDifficulty), so it must be re-applied on a
  // language change.
  renderGridDifficulty();
  // The "Interactif" mode panel carries translated labels/messages.
  if (interactiveMode) renderInteractive();
  // The "Interactif" mode help panel builds its list dynamically (see
  // renderInteractiveHelpList()) — re-render it if it's open at the
  // moment of the language change.
  if (!interactiveHelpOverlay.hidden) renderInteractiveHelpList();
  // Header pseudo: the "set a nickname" label (shown when no pseudo is
  // set) is translated, so it must be re-applied.
  if (!userPseudoBtn.hidden) renderUserPseudo();
  // "David FALCON"'s own welcome bubble, at the user's explicit request
  // — see renderChatWelcome()'s own docstring for why this only ever
  // does anything before the player's first real message. Skipped on the
  // bootstrap call (see uiBootstrapped) — the standalone call near
  // renderChatWelcome()'s definition covers the initial render.
  if (uiBootstrapped) renderChatWelcome();
  // The "Actu Croisée" panel's own language filter now follows the
  // interface language on every change, at the user's explicit request:
  // "When the user changes the interface language, the news feed's
  // language should adapt too." A deliberate reversal of this same
  // panel's own initial decision (set once at load, never resynced
  // afterward so as not to overwrite a manual choice the player made on
  // that exact selector) — the user's most recent preference now wins,
  // the same reversal pattern already applied this session to the
  // panel's own visibility.
  rssLanguageFilter.value = lang;
  renderRssList();
  // The Library's own language filter follows the interface language too,
  // at the user's explicit request ("changed whenever the interface
  // language changes"). If the panel is open, re-render it from page 1.
  libraryLanguageFilter.value = lang;
  if (!libraryPanel.hidden) {
    libraryCurrentPage = 1;
    renderLibraryList();
  }
  // Both the Dictionnaire's and the Paraphraseur's own language selectors
  // also follow the interface language (the requested default), as long
  // as the player hasn't changed anything on those specific panels.
  dictionaryLanguage.value = lang;
  paraphraseLanguage.value = lang;
  // Selects the "<lang1>/<lang2>" combination by default on both if it
  // still exists (see currentBilingualLangs()/defaultToBilingualOption()
  // above) — #bilingual-language was just forced to `lang` right above,
  // so only an already-loaded bilingual grid can still produce one at
  // this point.
  defaultToBilingualOption(dictionaryLanguage);
  defaultToBilingualOption(paraphraseLanguage);
  // The Qdrant admin panel (localhost): its labels are set at build time,
  // so it's re-rendered if it's open.
  if (!qdrantAdminPanel.hidden) loadQdrantAdmin();
}

languageSelect.addEventListener("change", () => setUiLanguage(languageSelect.value));
welcomeLanguageSelect.addEventListener("change", () => setUiLanguage(welcomeLanguageSelect.value));

// --- Welcome overlay + user pseudo -------------------------------------
// At the user's explicit request: on the first visit (no cwf-prefs
// cookie) the browser language is detected and a mandatory overlay form
// is shown (language selector + optional pseudo + a cookie notice). It
// can only be dismissed by clicking "Accepter". The user's pseudo is
// then shown centered in the header and, clicked, reopens this same
// form. Preferences live in a single functional cookie — no tracking,
// no advertising — leaving the (potentially large) seen-grids list in
// localStorage as before.
const MAX_PSEUDO_LENGTH = 15;
// Secret word max length (see backend/app.py's own MAX_SECRET_LENGTH,
// kept in sync) — at the user's explicit request, to let a user prove a
// pseudo belongs to them.
const MAX_SECRET_LENGTH = 60;
const PREFS_COOKIE = "cwf-prefs";

function loadPrefs() {
  try {
    const m = document.cookie.match(/(?:^|;\s*)cwf-prefs=([^;]*)/);
    if (!m) return null;
    const parsed = JSON.parse(decodeURIComponent(m[1]));
    return parsed && typeof parsed === "object" ? parsed : null;
  } catch (e) {
    return null;
  }
}

function savePrefs(prefs) {
  try {
    const value = encodeURIComponent(JSON.stringify(prefs));
    // One year, whole site, Lax — a functional preferences cookie.
    document.cookie = PREFS_COOKIE + "=" + value + "; path=/; max-age=31536000; samesite=lax";
  } catch (e) {
    // Cookies disabled entirely — the overlay just reappears next load.
  }
}

function detectBrowserLanguage() {
  const cands = (navigator.languages && navigator.languages.length)
    ? navigator.languages
    : [navigator.language || ""];
  for (const c of cands) {
    const base = String(c).toLowerCase().split("-")[0];
    if (SUPPORTED_UI_LANGS.indexOf(base) !== -1) return base;
  }
  return "en";
}

function renderUserPseudo() {
  const t = I18N[uiLanguage];
  userPseudoBtn.hidden = false;
  userPseudoBtn.textContent = userPseudo || t.userPseudoUnset;
  userPseudoBtn.classList.toggle("user-pseudo-unset", !userPseudo);
}

function openWelcomeOverlay() {
  welcomeLanguageSelect.value = uiLanguage;
  welcomePseudoInput.value = userPseudo;
  welcomeSecretInput.value = userSecret;
  welcomeOverlay.hidden = false;
  welcomePseudoInput.focus();
}

// POST /api/pseudo/claim (backend/app.py + backend/secret_store.py) — at
// the user's explicit request: "Mot secret" proves a chosen pseudo
// belongs to this user. Returns true (claim accepted — either the secret
// matched, or the pseudo was genuinely unclaimed and is now registered
// with it) or false (the pseudo already exists under a different secret
// — the caller must keep the panel open and tell the user so).
async function claimPseudoSecret(pseudo, secret) {
  const response = await fetchWithTimeout("/api/pseudo/claim", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ pseudo, secret }),
  }, FETCH_TIMEOUT_MS);
  if (!response.ok) throw new Error("pseudo claim failed: " + response.status);
  const data = await response.json();
  return !!data.ok;
}

welcomeForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  const pseudo = welcomePseudoInput.value.trim().slice(0, MAX_PSEUDO_LENGTH);
  // The panel doesn't close if the pseudo is empty, at the user's
  // explicit request. `required` already blocks a strictly empty field on
  // the browser's own side (the "submit" event doesn't even fire); this
  // additionally covers the "whitespace only" case.
  if (!pseudo) {
    welcomePseudoInput.setCustomValidity(I18N[uiLanguage].welcomePseudoRequired);
    welcomePseudoInput.reportValidity();
    return;
  }
  welcomePseudoInput.setCustomValidity("");
  // Same rule for the secret word, at the user's explicit request: "The
  // 'Secret word' must not be empty to be able to close the box (just
  // like the pseudo)."
  const secret = welcomeSecretInput.value.trim().slice(0, MAX_SECRET_LENGTH);
  if (!secret) {
    welcomeSecretInput.setCustomValidity(I18N[uiLanguage].welcomeSecretRequired);
    welcomeSecretInput.reportValidity();
    return;
  }
  welcomeSecretInput.setCustomValidity("");

  welcomeAcceptBtn.disabled = true;
  let claimed;
  try {
    claimed = await claimPseudoSecret(pseudo, secret);
  } catch (err) {
    welcomeSecretInput.setCustomValidity(I18N[uiLanguage].welcomeSecretCheckError);
    welcomeSecretInput.reportValidity();
    welcomeAcceptBtn.disabled = false;
    return;
  }
  welcomeAcceptBtn.disabled = false;
  if (!claimed) {
    // Pseudo already taken under a different secret word — at the user's
    // explicit request: "tell the user this pseudo is already taken,
    // don't close the box."
    welcomeSecretInput.setCustomValidity(I18N[uiLanguage].welcomeSecretTaken);
    welcomeSecretInput.reportValidity();
    return;
  }

  userPseudo = pseudo;
  userSecret = secret;
  savePrefs({ accepted: true, lang: uiLanguage, pseudo: userPseudo, secret: userSecret });
  renderUserPseudo();
  welcomeOverlay.hidden = true;
  // Fires a presence heartbeat right away (see pingPresence's own
  // docstring) instead of waiting for the setInterval's next tick, so
  // LOG_USERS reflects with no perceptible delay a user who just named
  // themselves (or changed their pseudo) — at the user's explicit
  // request. Fire-and-forget, like pingPresence() already is everywhere
  // else — must never delay the panel closing.
  pingPresence();
  // The pseudo may have changed — if the Library is open (especially on
  // the "Mes grilles" filter), refresh it.
  if (!libraryPanel.hidden) {
    libraryCurrentPage = 1;
    renderLibraryList();
  }
  // The pseudo has just become known (first visit) or may have changed
  // (pseudo edited later) — either way, check whether this pseudo has
  // any work in progress in GRID_WORK.
  checkForSavedInteractiveWork();
});

// Clears the custom validity message as soon as the user types something
// again, otherwise the field would stay marked invalid.
welcomePseudoInput.addEventListener("input", () => {
  welcomePseudoInput.setCustomValidity("");
});
welcomeSecretInput.addEventListener("input", () => {
  welcomeSecretInput.setCustomValidity("");
});

userPseudoBtn.addEventListener("click", openWelcomeOverlay);

(function initUserPrefs() {
  const prefs = loadPrefs();
  if (prefs && prefs.accepted) {
    userPseudo = typeof prefs.pseudo === "string"
      ? prefs.pseudo.slice(0, MAX_PSEUDO_LENGTH)
      : "";
    userSecret = typeof prefs.secret === "string"
      ? prefs.secret.slice(0, MAX_SECRET_LENGTH)
      : "";
    setUiLanguage(typeof prefs.lang === "string" ? prefs.lang : "fr");
    renderUserPseudo();
    checkForSavedInteractiveWork();
    // A pseudo chosen before "Mot secret" was added (or a cookie that
    // got reset on the secret's side for some other reason) has no
    // associated secret word yet — at the user's explicit request, the
    // panel is reopened to ask them to complete their profile, rather
    // than leaving this pseudo permanently unprotected. The panel then
    // behaves exactly as usual (the pseudo is already pre-filled, the
    // secret word is still required to close it) — if it was never
    // claimed by anyone else, submitting it simply claims it for the
    // first time.
    if (userPseudo && !userSecret) {
      openWelcomeOverlay();
    }
  } else {
    setUiLanguage(detectBrowserLanguage());
    openWelcomeOverlay();
  }
  uiBootstrapped = true;
})();

// Whether the page is being served from the local machine itself. Some
// generation options are only offered on localhost, because on a LAN
// address (run_Falcon.sh binds the frontend to 0.0.0.0) a remote visitor
// could otherwise tie up the single backend process for a very long
// time.
function isLocalhostOrigin() {
  const host = window.location.hostname;
  return (
    host === "localhost" ||
    host.endsWith(".localhost") ||
    host === "127.0.0.1" ||
    host === "::1" ||
    host === "[::1]"
  );
}

const MIN_DIMENSION = 5;
const REMOTE_MAX_DIMENSION = 20;

// Width/height are forced back into range whenever the field loses focus
// (or the entry is committed): never below MIN_DIMENSION (5), on every
// origin; and — off localhost only — never above REMOTE_MAX_DIMENSION
// (20), where the <input max> attribute is also lowered from 30 to 20 so
// the browser's own native validation blocks a larger value on submit
// too. An empty field is left alone (still being edited; `required` +
// `min` already block a submit).
(function clampDimensionInputs() {
  const max = isLocalhostOrigin() ? Infinity : REMOTE_MAX_DIMENSION;
  if (max !== Infinity) {
    widthInput.max = String(max);
    heightInput.max = String(max);
  }
  for (const input of [widthInput, heightInput]) {
    const clamp = () => {
      const n = Number(input.value);
      if (input.value === "" || Number.isNaN(n)) return;
      if (n < MIN_DIMENSION) input.value = String(MIN_DIMENSION);
      else if (n > max) input.value = String(max);
    };
    input.addEventListener("blur", clamp);
    input.addEventListener("change", clamp);
    clamp();
  }
})();

// "Ultra" mode (5,000,000 checks per attempt — see backend/app.py's
// BUDGET_MODES) is only offered on localhost: off it, its <option> is
// disabled — greyed out and non-selectable by the browser's own native
// rendering, no extra CSS needed — and a leftover "ultra" value falls
// back to "medium".
(function restrictUltraModeToLocalhost() {
  if (isLocalhostOrigin()) return;
  const modeSelect = document.getElementById("mode");
  const ultraOption = modeSelect.querySelector('option[value="ultra"]');
  if (ultraOption) ultraOption.disabled = true;
  if (modeSelect.value === "ultra") modeSelect.value = "medium";
})();

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

// "Définir" (GET /api/dictionary/define) makes one real LLM round-trip
// asking for up to 10 definitions at once — measured live at ~40s on this
// project's own small local model, well past FETCH_TIMEOUT_MS. Set above
// frontend/server.py's own DEFINE_PROXY_TIMEOUT_S (100s), same reasoning.
const DEFINE_FETCH_TIMEOUT_MS = 110000;

// "Thématique" (GET /api/similar_words) now also makes one LLM round-trip
// (describe_theme, expanding the typed term into keywords) before the
// per-keyword Qdrant searches, so it is no longer "quick or 503". Set
// above frontend/server.py's own SIMILAR_PROXY_TIMEOUT_S (60s), same
// "above the callee's timeout" reasoning as the others.
const SIMILAR_FETCH_TIMEOUT_MS = 70000;

function sleep(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

// "XhXmnXs" (hours/minutes/seconds), at the user's explicit request, to
// show the grid/definitions generation durations (see
// gridData.generation_duration_seconds/clues_duration_seconds, computed
// on the backend side — backend/app.py). Leading zero units are omitted
// (e.g. "45s" rather than "0h0mn45s") rather than always showing all
// three, to stay readable in the common case (a few tens of seconds to a
// few minutes).
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

// Elapsed-time counter for the current game, shown to the left of the
// grid's title (#grid-timer), at the user's explicit request — who then
// specified that the count should only start progressing the moment the
// player types their very first letter, including after a resume
// (reloading an in-progress grid must not run the counter while the page
// simply sits open without being used). startGridTimer() therefore only
// displays the starting value (0, or the saved value) without starting
// the interval; ensureGridTimerRunning() — called from the two places
// where a letter is actually written into the grid, handleKeydown() and
// typeVirtualLetter() — starts the interval on its very first call and
// does nothing afterward (it's also called on every following letter,
// but `gridTimerIntervalId` already being set makes that a no-op).
// Module-level (not a plain local state) since scheduleGridGameSave()
// (see further below) needs to read the current value on every autosave.
let gridTimerSeconds = 0;
let gridTimerIntervalId = null;

function renderGridTimer() {
  gridTimerEl.textContent = formatDuration(gridTimerSeconds);
}

// Stops the counter WITHOUT hiding it (see startGridTimer, which handles
// the display) — called right before (re)displaying a new one, so two
// `setInterval`s never run in parallel.
function stopGridTimer() {
  if (gridTimerIntervalId !== null) {
    clearInterval(gridTimerIntervalId);
    gridTimerIntervalId = null;
  }
}

// Displays the counter at `initialSeconds` (0 for a freshly generated
// grid, or a library grid with no saved game — see displayFinalGrid and
// GET /api/library/{grid_id}'s own `saved_game`; otherwise the value from
// the last GRID_GAME save, "resume the time counter where it was at save
// time" at the user's explicit request) WITHOUT starting it — see
// ensureGridTimerRunning() below, which triggers the actual countdown as
// soon as the player types their first letter.
function startGridTimer(initialSeconds) {
  stopGridTimer();
  gridTimerSeconds = Math.max(0, Math.round(initialSeconds || 0));
  gridTimerEl.hidden = false;
  renderGridTimer();
}

// Actually starts the countdown, only one `setInterval` at a time —
// called on every letter typed by the player (see handleKeydown/
// typeVirtualLetter); a no-op once it's already running.
function ensureGridTimerRunning() {
  if (gridTimerIntervalId !== null) return;
  gridTimerIntervalId = setInterval(() => {
    gridTimerSeconds += 1;
    renderGridTimer();
  }, 1000);
}

function hideGridTimer() {
  stopGridTimer();
  gridTimerEl.hidden = true;
}

// GRID_GAME autosave — "Every time the grid is edited, save the grid's
// state into GRID_GAME under the user's name so it can be reloaded
// later. Include the time counter's state." — at the user's explicit
// request. Fire-and-forget, like autosaveInteractiveWork(): a failure or
// slowness must never block typing. Only fires for a grid that's
// genuinely stored (`puzzle.id` — see backend/app.py's _run_generate_job,
// which adds it to the result as soon as it's saved to the library,
// whether it was just generated or reloaded) played in normal mode
// (never in "Interactif" mode, which has its own save mechanism), by a
// player who has already set a pseudo — a game is never saved
// "anonymously," per the user's explicit request.
async function scheduleGridGameSave() {
  if (interactiveMode || !puzzle || !puzzle.id || !userPseudo) return;
  try {
    await fetchWithTimeout("/api/game/save", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        grid_id: puzzle.id,
        pseudo: userPseudo,
        user_letters: userLetters,
        elapsed_seconds: gridTimerSeconds,
      }),
    }, FETCH_TIMEOUT_MS);
  } catch (err) {
    // Best-effort, silently ignored — same convention as
    // autosaveInteractiveWork().
  }
}

// Count of words tried (backend/crossword_gen.py, `total_attempts` — the
// sum of `Filler.checks`, incremented once per candidate word tried, see
// its own history) formatted to stay readable even once in the millions,
// at the user's explicit request: the exact number under 1000, then
// divided by 1000 with a K/M/G suffix as it grows — no decimal ("12K",
// never "12.3K"), at their own explicit further request.
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
    case "theme":
      message = t.statusTheme;
      break;
    case "interactive_building":
      message = t.statusInteractiveBuilding;
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
      // `count` is set when every attempt has stopped and several
      // successful grids are being optimized to pick the best one.
      message = step.count ? t.statusMinimizingMany(step.count) : t.statusMinimizing;
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
  // Not while the successful grids are being evaluated: the search budget
  // no longer means anything once every attempt has stopped.
  if (typeof step.budget_percent === "number" && step.code !== "minimizing") {
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
  // in full, right away, however many arrived since the last poll. Each
  // recordPreviewHistory() call also snaps the live view straight to the
  // newest of them (when following along — see its own comment), so the
  // on-screen preview is always whatever this poll just learned, with no
  // separate reveal pacing of its own. catchUpPreviewToEnd() still forces
  // that same jump unconditionally once the job reaches a terminal status,
  // covering the case where the player had paused on an earlier entry.
  let nextExampleIndex = 0;
  // Same incremental-read cursor as nextExampleIndex just above, but for
  // job["clues_progress"] (backend/app.py) — every definition the LLM has
  // produced since the last poll is appended to the module-level
  // liveClues array (never overwritten), at the user's explicit request:
  // "afficher les définitions créées sous la grille aperçu."
  let nextClueIndex = 0;
  // Consecutive failed polls so far (network error or backend_unavailable
  // 502) — reset to 0 the moment a poll actually succeeds. See
  // POLL_RECONNECT_ATTEMPTS's own comment for why this exists.
  let consecutivePollFailures = 0;
  // True once some other job has taken over the shared preview/status UI
  // (currentJobId now points elsewhere) — e.g. the attempt-preview pencil
  // button cancels this loop's own job and immediately starts a brand-new
  // interactive session while this loop's very last poll(s) may still be
  // in flight. Without this guard, that straggler poll's own catchUpPreview
  // ToEnd()/setStatus() calls would overwrite the new session's UI with this
  // now-irrelevant job's own final state a moment after it was shown.
  const isCurrentJob = () => jobId === currentJobId;
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
    if (isCurrentJob()) successMedalCount.textContent = String(data.success_count || 0);
    const cluesFeed = data.clues_progress || [];
    if (cluesFeed.length > nextClueIndex) {
      if (isCurrentJob()) {
        liveClues = liveClues.concat(cluesFeed.slice(nextClueIndex));
        renderLiveClues();
      }
      nextClueIndex = cluesFeed.length;
    }
    const history = data.examples_history || [];
    if (history.length > nextExampleIndex) {
      if (isCurrentJob()) recordPreviewHistory(history.slice(nextExampleIndex));
      nextExampleIndex = history.length;
    }
    // Decides what this poll actually shows — a not-yet-seen key step if
    // one is waiting, otherwise the live search state — see its own
    // comment for the full priority order.
    if (isCurrentJob()) advanceLiveDisplay(data);
    // "Definitions count stuck at 0/27, even though the definitions
    // themselves appear right below the grid" — a bug already reported
    // several times, root-caused here: the clue-generation phase only
    // ever gets ONE entry in examples_history/previewHistory (the very
    // first progress("clues", current=0, ...) call, the only call of
    // this phase to carry `examples` — see backend/app.py's
    // progress()). Every later update (one more word defined) never
    // carries `examples`, so it's never recorded; #attempt-preview-
    // status (lastPreviewStep, fed only by showPreviewEntry() on a NEW
    // entry) therefore stayed frozen on "0/N" the whole time, even
    // though the "Définitions générées" list right below it (liveClues,
    // fed separately above) was genuinely progressing word by word.
    // "saving" (right after) doesn't carry `examples` either, so this
    // "clues" entry stays the last one in previewHistory for the whole
    // rest of the job — so it can be found and refreshed in place
    // here, live, instead of being left frozen.
    if (isCurrentJob() && data.step && data.step.code === "clues" && previewHistory.length) {
      const cluesEntry = previewHistory[previewHistory.length - 1];
      if (cluesEntry.step && cluesEntry.step.code === "clues") {
        cluesEntry.step = {
          ...cluesEntry.step, current: data.step.current, total: data.step.total,
        };
        // Only re-render if this entry is genuinely the one currently
        // shown — if the player has navigated back in the history to
        // review an earlier step, this live update must not change
        // what's on screen out from under them; it will be picked up
        // once they come back to this entry (see showNextPreview()/
        // catchUpPreviewToEnd(), which always re-read `entry.step` at
        // the moment of display).
        if (previewHistoryIndex === previewHistory.length - 1) {
          lastPreviewStep = cluesEntry.step;
          renderPreviewStatus();
        }
      }
    }
    if (data.status === "error") {
      if (isCurrentJob()) catchUpPreviewToEnd();
      throw new GenerationFailedError(
        describeErrorCode(t, data.error_code, data.error), jobId, data.error_code,
      );
    }
    if (data.status === "cancelled") {
      if (isCurrentJob()) catchUpPreviewToEnd();
      throw new CancelledError(t.statusCancelled);
    }
    if (data.status === "done") {
      if (isCurrentJob()) catchUpPreviewToEnd();
      return data.result;
    }
    if (isCurrentJob()) setStatus(describeStep(t, data.step), false);
    await sleep(POLL_INTERVAL_MS);
  }
}

// "Already seen" — the set of identifiers (a GRID_STORE file's own `id`
// field, see backend/grid_store.py) of the grids this browser has already
// displayed, at the user's explicit request ("store the GRID_STORE file
// name ... to know it's already been seen"). Kept in localStorage rather
// than a real cookie: after automatic population (Automation/Populate.py,
// 1000 grids) the list can hold thousands of entries — well past the
// ~4 KB a cookie can hold, and a cookie would be sent back on every
// request for nothing. The full list is passed to the backend in
// POST /api/library's own body (see renderLibraryList) so it can
// filter/annotate the list itself. A generous FIFO ceiling: ~35 bytes per
// id, 20000 ids ≈ 700 KB, well under localStorage's own limit.
const SEEN_GRIDS_KEY = "cwf-seen-grids";
const SEEN_GRIDS_MAX = 20000;

function loadSeenGridIds() {
  try {
    const raw = JSON.parse(localStorage.getItem(SEEN_GRIDS_KEY) || "[]");
    return Array.isArray(raw) ? raw : [];
  } catch (err) {
    return [];
  }
}

function markGridSeen(gridId) {
  if (!gridId) return;
  let ids = loadSeenGridIds();
  if (ids.includes(gridId)) return;
  ids.push(gridId);
  if (ids.length > SEEN_GRIDS_MAX) ids = ids.slice(ids.length - SEEN_GRIDS_MAX);
  try {
    localStorage.setItem(SEEN_GRIDS_KEY, JSON.stringify(ids));
  } catch (err) {
    // Quota full / storage disabled: too bad, "already seen" tracking is
    // a convenience, not a critical feature.
  }
}

// Difficulty level shown to the right of the playable grid's title, at
// the user's explicit request. Read from `puzzle`: backend/app.py adds
// `difficulty` to the result of a freshly generated grid, and a library
// grid's own GRID_STORE record already carries it when reloaded. The
// label is translated (difficultyEasy/Medium/Hard), so it's re-called on
// an interface language change. A grid with no such field (saved before
// this feature existed) simply shows nothing.
function renderGridDifficulty() {
  const key = { easy: "difficultyEasy", medium: "difficultyMedium", hard: "difficultyHard" }[
    puzzle && puzzle.difficulty
  ];
  if (key) {
    // Prefixed with "Difficulté : " (punctuation per language) rather
    // than a bare adjective ("Moyenne"), which reads oddly with no
    // context.
    gridDifficultyEl.textContent = I18N[uiLanguage].gridDifficulty(I18N[uiLanguage][key]);
    gridDifficultyEl.hidden = false;
  } else {
    gridDifficultyEl.textContent = "";
    gridDifficultyEl.hidden = true;
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
  // A newly loaded bilingual grid must immediately offer (and select by
  // default, at the user's explicit request) its own "<lang1>/<lang2>"
  // combination in both the Dictionnaire and the Paraphraseur, without
  // waiting for a first word hover (see
  // currentBilingualLangs()/defaultToBilingualOption()).
  defaultToBilingualOption(dictionaryLanguage);
  defaultToBilingualOption(paraphraseLanguage);
  userLetters = Array.from({ length: gridData.height }, () => Array(gridData.width).fill(""));
  // A game already saved in GRID_GAME for this grid + this pseudo (see
  // GET /api/library/{grid_id}'s own `saved_game`, passed by
  // loadLibraryGrid — never present for a grid that just finished
  // generating, whose grid_id is brand new), at the user's explicit
  // request: "check whether this grid exists in GRID_GAME to reload it
  // and resume the time counter where it was at save time." Dimensions
  // re-checked before use — should never differ (a grid_id always
  // designates the same content), purely defensive.
  const savedGame = gridData.saved_game || null;
  const savedLetters = savedGame && savedGame.user_letters;
  if (savedLetters && savedLetters.length === gridData.height
      && savedLetters.every((row) => row.length === gridData.width)) {
    userLetters = savedLetters.map((row) => row.slice());
  }
  selected = null;
  showSolution = false;
  checking = false;
  solutionBtn.classList.remove("active");
  checkBtn.classList.remove("active");

  hideAttemptPreviewPanel();
  // A normal, playable grid replacing whatever the "Interactif" mode
  // panel was showing inside #result.
  hideInteractivePanel();
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
  gridTitleTextEl.textContent = gridData.title || "";
  // Difficulty level ("Facile" / "Moyen" / "Difficile") shown to the
  // right of the title, at the user's explicit request. Translated per
  // the interface language, so also re-rendered on a language change (see
  // languageSelect's own handler). The difficulty alone (with no title)
  // is enough to show the #grid-title line.
  renderGridDifficulty();
  gridTitleEl.hidden = !gridData.title && gridDifficultyEl.hidden;
  // Time counter: shows 0 for a grid with no saved game, or "where it was
  // at save time" (see savedGame above), but only starts the countdown on
  // the first letter typed (see ensureGridTimerRunning) — at the user's
  // explicit request, including on a resume like this one.
  startGridTimer(savedGame ? savedGame.elapsed_seconds : 0);
  // Marks this grid "already seen" — the same path for a grid that just
  // finished generating (backend/app.py adds `id` to the `result`, see
  // its own comment) and a grid reloaded from the library
  // (GET /api/library/{grid_id} already returns `id`). At the user's
  // explicit request: "including the grid they just generated".
  markGridSeen(gridData.id);
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
  // "Recalculer" button — recomputes this grid's definitions into a brand
  // new library copy (title gets a bumped "(Vn)" marker) and swaps it in,
  // at the user's explicit request. Available for any grid shown in play mode
  // (freshly generated or loaded from the library alike), since either
  // way it carries an `id` displayFinalGrid() just marked seen.
  recomputeBtn.hidden = false;
  recomputeBtn.disabled = false;
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
  // Generic close (the "Bibliothèque"/"X" button, or before loading a
  // normal grid) — cancels any pending automatic-reopen intent.
  // openLibraryGridInteractive() below sets `libraryReopenArmPending` back
  // to true right AFTER its own call to this function, so this generic
  // reset never overwrites it in that specific case.
  libraryReopenOnInteractive = false;
  libraryReopenArmPending = false;
}

function openLibraryPanel() {
  libraryPanel.hidden = false;
  syncRssPanelVisibility();
  libraryCurrentPage = 1;
  renderLibraryList();
}

// The Library must reopen automatically every time Edition (Interactif)
// mode is shown again AFTER the session has actually started — at the
// user's explicit request — including after a round trip through "Finir
// la grille"/"Finir la zone" (which hides then re-shows this mode
// without ever going back through openLibraryGridInteractive) — but NOT
// on the very first show that follows clicking the Library's own pencil
// icon (a real, previously-reported regression: closing the Library
// right before opening the grid for editing, then immediately reopening
// it on that same entry, defeated the point of closing it at all).
// `libraryReopenArmPending` carries the intent from openLibraryGridInteractive()
// through that first, reopen-suppressed enterInteractiveMode() call;
// `libraryReopenOnInteractive` itself only flips true once that first
// call actually runs, so it only ever fires starting from the session's
// SECOND show onward. Both reset to false by any manual/generic Library
// close (hideLibraryPanel), so neither is ever forced back open after
// the player closed it themselves — and by runInteractive() itself
// whenever a genuinely fresh "/api/interactive/start" session begins
// (see its own comment), so an unrelated new session started from the
// generation form's own "Générer la grille" button while already
// editing a from-library grid never inherits that earlier session's
// still-armed reopen intent.
let libraryReopenOnInteractive = false;
let libraryReopenArmPending = false;

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

// Public base for the Library's own shareable links, at the user's
// explicit request ("base URL https://falcon.cubaix.com/"). Each row's
// link is SHARE_BASE_URL + "?grid=<id>"; opened in a new tab, it reloads
// the grid to play it (see maybeLoadGridFromUrl() at the end of this
// file, which reads ?grid= regardless of the host — the table's own link
// always points at this public domain).
const SHARE_BASE_URL = "https://falcon.cubaix.com/";

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
// itself is server-side (POST /api/library, page in the body) — this just
// renders whichever single page's worth of rows came back, plus a "Page
// X/Y" readout and disables libraryPrevBtn/libraryNextBtn at either end,
// from the `total`/`page_size` the same response carries.
//
// POST (not GET) so the request body can carry `seen_ids` — the list of
// grids this browser has already viewed (see markGridSeen / SEEN_GRIDS_
// KEY) — plus `seen_filter` (#library-seen-filter: all / unseen / seen).
// The back does the filtering + pagination and annotates each grid with
// `seen`, at the user's explicit request ("Passer les grilles déjà vues
// au Back pour qu'il sache comment gérer la liste à transmettre au
// Front"). A `seen` row is greyed (.library-grid-seen) but still fully
// clickable.
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
      "/api/library",
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          preferred_language: uiLanguage,
          page: libraryCurrentPage,
          // "all" or a language code — defaults to the interface language
          // (see #library-language-filter), at the user's explicit
          // request: "By default, only show grids in the interface
          // language".
          language_filter: libraryLanguageFilter.value,
          // "all" or easy/medium/hard — "Tous les niveaux" by default,
          // does not follow the interface language (see
          // #library-difficulty-filter).
          difficulty_filter: libraryDifficultyFilter.value,
          // "all"/"unseen"/"seen"/"mine" — "Mes grilles" ("mine") filters
          // on the backend side on each grid's own `pseudo` field,
          // compared against the current pseudo sent below.
          seen_filter: librarySeenFilter.value,
          seen_ids: loadSeenGridIds(),
          pseudo: userPseudo,
        }),
      },
      FETCH_TIMEOUT_MS,
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
  const seenSet = new Set(loadSeenGridIds());
  for (const entry of entries) {
    const tr = document.createElement("tr");
    tr.tabIndex = 0;
    // Greyed if the backend annotated it `seen` (or, as a safety net, if
    // our own localStorage already knows it) — still clickable.
    if (entry.seen || seenSet.has(entry.id)) {
      tr.classList.add("library-grid-seen");
    }
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
    // Bilingual grid (see backend/grid_store.py's own `bilingual`
    // field), at the user's explicit request: shows both language codes
    // ("fr/en") right after the name already shown above, rather than a
    // second, possibly long, spelled-out label — stays readable even
    // when both languages share a narrow table row.
    if (entry.bilingual) {
      languageTd.textContent += ` (${entry.language}/${entry.bilingual})`;
    }
    const dateTd = document.createElement("td");
    dateTd.textContent = entry.created_at ? new Date(entry.created_at).toLocaleString(uiLanguage) : "";
    const titleTd = document.createElement("td");
    const titleMain = document.createElement("div");
    titleMain.textContent = entry.title || "";
    titleTd.appendChild(titleMain);
    // A grid created by editing an existing Library grid ("Ouvrir en
    // mode Interactif" button — see backend/grid_store.py's
    // save_grid_json/save_grid_work's own `origin`), at the user's
    // explicit request: mentions the provenance (origin grid/author/date)
    // under the title, on its own line. `origin` is a snapshot taken at
    // the moment editing started (never re-read from the origin grid,
    // which may have since changed or disappeared) — absent/`None` for
    // any grid not born from an edit.
    if (entry.origin && entry.origin.id) {
      const originDate = entry.origin.created_at
        ? new Date(entry.origin.created_at).toLocaleDateString(uiLanguage)
        : "";
      const originAuthor = entry.origin.pseudo || t.libraryAuthorBot;
      const originTag = document.createElement("div");
      originTag.className = "library-origin-tag";
      originTag.textContent = t.libraryOriginTag(entry.origin.title || "", originAuthor, originDate);
      titleTd.appendChild(originTag);
    }
    // "Thématique" column: the word list typed at generation time (the
    // grid's own JSON `theme` field — see backend/grid_store.py), empty
    // when the grid has no theme. At the user's explicit request.
    const themeTd = document.createElement("td");
    themeTd.textContent = entry.theme || "";
    const difficultyTd = document.createElement("td");
    difficultyTd.textContent = difficultyLabels[entry.difficulty] || entry.difficulty || "";
    const sizeTd = document.createElement("td");
    sizeTd.textContent = entry.width && entry.height ? `${entry.width}×${entry.height}` : "";
    // Last column: the author's pseudo (the grid's own JSON `pseudo`
    // field — see backend/grid_store.py). A grid with no author
    // (generated with no pseudo set) is attributed to "Falcon Auto Bot".
    const authorTd = document.createElement("td");
    authorTd.textContent = entry.pseudo || t.libraryAuthorBot;
    // A grid built via "Interactif" mode: "(Création)" tag next to the
    // author (see backend/grid_store.py's save_grid_json's `interactive`).
    if (entry.interactive) authorTd.textContent += ` ${t.libraryCreationTag}`;
    // Last column: shareable link to the grid, at the user's explicit
    // request. Opens SHARE_BASE_URL + "?grid=<id>" in a new tab (the
    // public domain, regardless of the current host). stopPropagation so
    // it doesn't also trigger the row click's own loadLibraryGrid()
    // (which loads into the current tab).
    const linkTd = document.createElement("td");
    const link = document.createElement("a");
    link.href = `${SHARE_BASE_URL}?grid=${encodeURIComponent(entry.id)}`;
    link.target = "_blank";
    link.rel = "noopener noreferrer";
    link.textContent = t.libraryLinkText;
    link.className = "library-link";
    link.addEventListener("click", (event) => event.stopPropagation());
    link.addEventListener("keydown", (event) => event.stopPropagation());
    linkTd.appendChild(link);
    // "Interactif" column: an icon button (pencil) opening the grid in
    // "Interactif" mode — the backend then creates a new GRID_WORK task
    // from this grid (see POST /api/interactive/from-library). Inline
    // SVG icon (no external icon font/library, same convention as the
    // PDF badge / the header's own "i" badge). stopPropagation like the
    // neighboring links so it doesn't also trigger the row click's own
    // loadLibraryGrid().
    const interactiveTd = document.createElement("td");
    const interactiveBtn = document.createElement("button");
    interactiveBtn.type = "button";
    interactiveBtn.className = "library-interactive-btn";
    interactiveBtn.setAttribute("aria-label", t.libraryInteractiveText);
    interactiveBtn.title = t.libraryInteractiveText;
    interactiveBtn.innerHTML =
      '<svg class="interactive-icon" viewBox="0 0 24 24" width="18" height="18" ' +
      'aria-hidden="true" focusable="false">' +
      '<path d="M4 20h4L18.5 9.5l-4-4L4 16v4z" fill="none" stroke="currentColor" ' +
      'stroke-width="1.6" stroke-linejoin="round"/>' +
      '<path d="M13.5 6.5l4 4" fill="none" stroke="currentColor" stroke-width="1.6"/>' +
      "</svg>";
    interactiveBtn.addEventListener("click", (event) => {
      event.stopPropagation();
      openLibraryGridInteractive(entry.id);
    });
    interactiveBtn.addEventListener("keydown", (event) => event.stopPropagation());
    interactiveTd.appendChild(interactiveBtn);
    // Last column: printable PDF download (empty grid + definitions +
    // title, no answers — see GET /api/library/<id>/pdf), at the user's
    // explicit request. Relative URL: goes through the frontend's own
    // proxy on the current host (local or public). `download` +
    // stopPropagation, like the "Jouer" link above.
    const pdfTd = document.createElement("td");
    const pdfLink = document.createElement("a");
    pdfLink.href = `/api/library/${encodeURIComponent(entry.id)}/pdf`;
    pdfLink.setAttribute("download", "");
    pdfLink.rel = "noopener";
    // A PDF icon rather than the word "Télécharger", at the user's
    // explicit request. A red "PDF" badge on a page — drawn as inline SVG
    // (this project uses no external icon font/library, cf. the header's
    // own "i" badge). The accessible label stays `libraryPdfText`
    // (aria-label + title).
    pdfLink.className = "library-link library-pdf-link";
    pdfLink.setAttribute("aria-label", t.libraryPdfText);
    pdfLink.title = t.libraryPdfText;
    pdfLink.innerHTML =
      '<svg class="pdf-icon" viewBox="0 0 24 24" width="20" height="20" ' +
      'aria-hidden="true" focusable="false">' +
      '<path d="M7 2h7l5 5v13a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2z" ' +
      'fill="#ffffff" stroke="#9ca3af" stroke-width="1.3"/>' +
      '<path d="M14 2v5h5" fill="#ffffff" stroke="#9ca3af" stroke-width="1.3"/>' +
      '<rect x="3.5" y="12" width="17" height="8" rx="1.5" fill="#dc2626"/>' +
      '<text x="12" y="18.2" font-size="5.4" font-weight="700" ' +
      'text-anchor="middle" fill="#ffffff" font-family="sans-serif">PDF</text>' +
      "</svg>";
    pdfLink.addEventListener("click", (event) => event.stopPropagation());
    pdfLink.addEventListener("keydown", (event) => event.stopPropagation());
    pdfTd.appendChild(pdfLink);
    tr.append(
      languageTd, dateTd, titleTd, themeTd, difficultyTd, sizeTd, authorTd,
      linkTd, interactiveTd, pdfTd,
    );
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
    // Pseudo sent as a query string — at the user's explicit request, the
    // backend then looks for a game already saved in GRID_GAME for
    // (gridId, userPseudo) and joins it into the result under
    // `saved_game` (see backend/app.py's library_get and
    // displayFinalGrid, which reads it). Omitted if no pseudo is set
    // yet — nothing to find in that case.
    const query = userPseudo ? `?pseudo=${encodeURIComponent(userPseudo)}` : "";
    const response = await fetchWithTimeout(`/api/library/${gridId}${query}`, {}, FETCH_TIMEOUT_MS);
    const data = await response.json();
    if (!response.ok) {
      throw new Error(describeErrorCode(t, data.detail && data.detail.code, data.detail));
    }
    hideAttemptPreview();
    stopBtn.hidden = true;
    continueBtn.hidden = true;
    // gridId is always known here; displayFinalGrid already marks
    // data.id, this duplicate covers the unlikely case where the disk
    // record didn't return the field.
    markGridSeen(gridId);
    displayFinalGrid(data);
    hideLibraryPanel();
    setStatus(t.statusLibraryLoaded, false);
  } catch (err) {
    setStatus(err.message, true);
  }
}

// The library list's own "Ouvrir en mode Interactif" icon button: opens a
// finished library grid in the "Interactif" authoring mode. The backend
// (POST /api/interactive/from-library) reshapes the stored record into an
// editable session and, from the first autosave on, it lives as its own
// brand-new GRID_WORK "Créations" entry — the original library grid is
// never touched. Reuses runInteractive()'s full flow (hide play-mode
// chrome, "Stop", poll, enterInteractiveMode) exactly like
// resumeInteractiveWork(), only the endpoint/body differ.
async function openLibraryGridInteractive(gridId) {
  hideLibraryPanel();
  // Arm the reopen for every entry into this mode AFTER this one — see
  // `libraryReopenArmPending`'s own declaration above for why this isn't
  // `libraryReopenOnInteractive` directly.
  libraryReopenArmPending = true;
  await runInteractive({ grid_id: gridId }, "/api/interactive/from-library");
}

libraryBtn.addEventListener("click", () => {
  if (libraryPanel.hidden) {
    openLibraryPanel();
  } else {
    hideLibraryPanel();
  }
});

libraryCloseBtn.addEventListener("click", hideLibraryPanel);

// "Actualiser" button, at the user's explicit request: re-renders the
// current page with the current filters, without going back to page 1
// (unlike the filter changes below) — only meant to review the most
// recent state (a newly added grid, a "seen" status updated by another
// tab, etc.) without losing one's place.
libraryRefreshBtn.addEventListener("click", () => {
  renderLibraryList();
});

// The three selectors at the top of the Library: language filter (all /
// one language / bilingual), difficulty filter (all levels / easy /
// medium / hard) and "already seen" filter (all / unseen / seen). Every
// change goes back to page 1 and re-renders the list. The language
// filter defaults to the interface language and follows its changes (see
// #language's own handler further below); the difficulty filter stays on
// "Tous les niveaux" and never follows the language.
libraryLanguageFilter.value = uiLanguage;
libraryLanguageFilter.addEventListener("change", () => {
  libraryCurrentPage = 1;
  renderLibraryList();
});
libraryDifficultyFilter.addEventListener("change", () => {
  libraryCurrentPage = 1;
  renderLibraryList();
});
librarySeenFilter.addEventListener("change", () => {
  libraryCurrentPage = 1;
  renderLibraryList();
});

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

// "Dictionnaire" panel, at the user's explicit request: given a word
// (accents/case ignored), the backend lists every word of the same root
// drawn from the wordlist, with their definitions from the
// <lang>_glosses.jsonl file (see backend/dictionary_lookup.py + GET
// /api/dictionary). Each search produces its own table, stacked at the
// top (most recent first); "Effacer" clears the stack.
function hideDictionaryPanel() {
  dictionaryPanel.hidden = true;
  syncRssPanelVisibility();
}

// One table per searched word: column 1 = full form (canonical form in
// parentheses), column 2 = "part of speech : definition" list. Built
// entirely via the DOM API (textContent) — no dictionary data is ever
// injected as innerHTML. Returns the node WITHOUT attaching it to the
// page (see buildBilingualResultBlock() further below, at the user's
// explicit request: when two languages are selected, this node becomes
// one of the two columns of a bilingual result instead of standing alone
// in the stack) — the form handler further below decides itself whether
// to stack it alone or inside a bilingual block.
function buildDictionaryResultNode(query, data) {
  const t = I18N[uiLanguage];
  const block = document.createElement("div");
  block.className = "dictionary-result";

  const heading = document.createElement("h3");
  heading.textContent = query;
  block.appendChild(heading);

  const rows = (data && data.rows) || [];
  if (!rows.length) {
    const empty = document.createElement("p");
    empty.className = "dictionary-empty";
    empty.textContent = `${t.dictionaryNoResults} « ${query} »`;
    block.appendChild(empty);
    return block;
  }

  if (data.truncated) {
    const note = document.createElement("p");
    note.className = "dictionary-truncated";
    note.textContent = t.dictionaryTruncated;
    block.appendChild(note);
  }

  const wrap = document.createElement("div");
  wrap.className = "table-scroll";
  const table = document.createElement("table");
  table.className = "dictionary-table";
  const thead = document.createElement("thead");
  const htr = document.createElement("tr");
  for (const label of [t.dictionaryColForm, t.dictionaryColDefinitions]) {
    const th = document.createElement("th");
    th.textContent = label;
    htr.appendChild(th);
  }
  thead.appendChild(htr);
  table.appendChild(thead);

  const tbody = document.createElement("tbody");
  for (const row of rows) {
    const tr = document.createElement("tr");
    const c1 = document.createElement("td");
    c1.className = "dictionary-form-cell";
    c1.textContent = `${row.form} (${row.canonical})`;
    tr.appendChild(c1);

    const c2 = document.createElement("td");
    c2.className = "dictionary-def-cell";
    if (!row.definitions || !row.definitions.length) {
      const em = document.createElement("em");
      em.textContent = t.dictionaryNoDefinition;
      c2.appendChild(em);
    } else {
      for (const d of row.definitions) {
        const line = document.createElement("div");
        line.className = "dictionary-def-line";
        const prefix = d.lemma ? `[${d.lemma}] ` : "";
        const pos = d.pos ? `${d.pos} : ` : "";
        line.textContent = `${prefix}${pos}${d.gloss}`;
        c2.appendChild(line);
      }
    }
    tr.appendChild(c2);
    tbody.appendChild(tr);
  }
  table.appendChild(tbody);
  wrap.appendChild(table);
  block.appendChild(wrap);
  return block;
}

dictionaryBtn.addEventListener("click", () => {
  if (dictionaryPanel.hidden) {
    dictionaryPanel.hidden = false;
    dictionaryLanguage.value = uiLanguage;
    defaultToBilingualOption(dictionaryLanguage);
    syncRssPanelVisibility();
    dictionaryInput.focus();
  } else {
    hideDictionaryPanel();
  }
});

dictionaryCloseBtn.addEventListener("click", hideDictionaryPanel);

dictionaryClearBtn.addEventListener("click", () => {
  dictionaryResults.replaceChildren();
  dictionaryInput.value = "";
  dictionaryInput.focus();
});

// Combines two already-built result nodes (one per language) into a
// block shown 50/50, each headed by its own language — at the user's
// explicit request, for both the Dictionnaire and the Paraphraseur:
// "when two languages are selected... each tool is then run in both
// languages, and the results shown in a 50/50 table with the language
// shown as a heading." `selectEl` is the language selector
// (#dictionary-language or #paraphrase-language) to read the native
// labels from; `lang1`/`lang2` are always plain ISO codes (never a
// combination "xx/yy" themselves); `node1`/`node2` are nodes already
// built by one of this file's buildXNode() functions.
function buildBilingualResultBlock(selectEl, lang1, node1, lang2, node2) {
  const wrap = document.createElement("div");
  wrap.className = "bilingual-result";
  for (const [lang, node] of [[lang1, node1], [lang2, node2]]) {
    const col = document.createElement("div");
    col.className = "bilingual-col";
    const title = document.createElement("h4");
    title.className = "bilingual-lang-title";
    title.textContent = nativeLanguageLabel(selectEl, lang);
    col.appendChild(title);
    col.appendChild(node);
    wrap.appendChild(col);
  }
  return wrap;
}

// Language adjectives per SENTENCE language (the Perplexity template
// used by both the Dictionnaire and the Paraphraseur below), one set per
// sentence language covering all 6 possible target languages — lets us
// compose "français/anglais" etc. when two languages are selected, at
// the user's explicit request: "Also send Perplexity a query with
// <lang1>/<lang2>." Agreement already chosen for each template
// (masculine "mot" in French, neuter "Wort" in German, feminine
// "palabra"/"parola"/"palavra" in Spanish/Italian/Portuguese, invariable
// in English).
const PERPLEXITY_LANGUAGE_ADJECTIVES = {
  fr: { fr: "français", en: "anglais", de: "allemand", es: "espagnol", it: "italien", pt: "portugais" },
  en: { fr: "French", en: "English", de: "German", es: "Spanish", it: "Italian", pt: "Portuguese" },
  de: { fr: "französische", en: "englische", de: "deutsche", es: "spanische", it: "italienische", pt: "portugiesische" },
  es: { fr: "francesa", en: "inglesa", de: "alemana", es: "española", it: "italiana", pt: "portuguesa" },
  it: { fr: "francese", en: "inglese", de: "tedesca", es: "spagnola", it: "italiana", pt: "portoghese" },
  pt: { fr: "francesa", en: "inglesa", de: "alemã", es: "espanhola", it: "italiana", pt: "portuguesa" },
};

function perplexityLanguageAdjective(sentenceLang, targetLang) {
  const table = PERPLEXITY_LANGUAGE_ADJECTIVES[sentenceLang] || PERPLEXITY_LANGUAGE_ADJECTIVES.fr;
  return table[targetLang] || targetLang;
}

// Splits a language-selector value (plain "fr", or combined "fr/en")
// into an array of 1 or 2 ISO codes — shared by every bilingual use
// (Dictionnaire, Paraphraseur).
function splitLanguageValue(langValue) {
  return langValue.includes("/") ? langValue.split("/") : [langValue];
}

// Definition query sent to Perplexity, adapted to #dictionary-language's
// own selected language — never to uiLanguage (the interface), since we
// want to ask for the definition OF A WORD IN THAT language, regardless
// of the interface's own language. The word exactly as typed (never
// translated or re-accented) is inserted into `${word}`. When two
// languages are selected, the sentence stays in the first one's own
// language (`langCodes[0]`, the one for horizontal words — see
// currentBilingualLangs()) but the language adjective becomes
// "<adj1>/<adj2>" (perplexityLanguageAdjective above), at the user's
// explicit request. The template itself is taken verbatim from the
// user's explicit request: "Give 5 crossword-puzzle definition
// suggestions, then define every possible meaning of the <lang> word:
// <text>."
const PERPLEXITY_DEFINE_QUERY_TEMPLATES = {
  fr: (adj, word) => `Faire 5 propositions de définitions pour les mots croisés, puis définir tous les sens possibles du mot ${adj} : ${word}`,
  en: (adj, word) => `Give 5 crossword-puzzle definition suggestions, then define every possible meaning of the ${adj} word: ${word}`,
  de: (adj, word) => `Mach 5 Vorschläge für Kreuzworträtsel-Definitionen, definiere dann alle möglichen Bedeutungen für das ${adj} Wort: ${word}`,
  es: (adj, word) => `Haz 5 propuestas de definiciones para crucigramas, y luego define todos los significados posibles de la palabra ${adj}: ${word}`,
  it: (adj, word) => `Fai 5 proposte di definizioni per il cruciverba, poi definisci tutti i possibili significati della parola ${adj}: ${word}`,
  pt: (adj, word) => `Faça 5 propostas de definições para palavras cruzadas, depois defina todos os significados possíveis da palavra ${adj}: ${word}`,
};

dictionaryPerplexityBtn.addEventListener("click", () => {
  const word = dictionaryInput.value.trim();
  if (!word) {
    dictionaryInput.focus();
    return;
  }
  const langCodes = splitLanguageValue(dictionaryLanguage.value);
  const sentenceLang = langCodes[0];
  const adjective = langCodes
    .map((code) => perplexityLanguageAdjective(sentenceLang, code))
    .join("/");
  const template =
    PERPLEXITY_DEFINE_QUERY_TEMPLATES[sentenceLang] ||
    PERPLEXITY_DEFINE_QUERY_TEMPLATES.fr;
  const url = `https://www.perplexity.ai/search?q=${encodeURIComponent(template(adjective, word))}`;
  window.open(url, "_blank", "noopener,noreferrer");
});

// Queries /api/dictionary for a given language and returns the already-
// built result node (never attached to the page) — factored out so it
// can be called once or twice (bilingual) by the handler below.
async function fetchDictionaryResultNode(query, lang) {
  const t = I18N[uiLanguage];
  const response = await fetchWithTimeout(
    `/api/dictionary?q=${encodeURIComponent(query)}&lang=${encodeURIComponent(lang)}`,
    {}, FETCH_TIMEOUT_MS,
  );
  if (!response.ok) throw new Error(t.dictionaryError);
  const data = await response.json();
  return buildDictionaryResultNode(query, data);
}

dictionaryForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  const query = dictionaryInput.value.trim();
  if (!query) return;
  const t = I18N[uiLanguage];
  dictionarySearchBtn.disabled = true;
  try {
    const langCodes = splitLanguageValue(dictionaryLanguage.value);
    if (langCodes.length === 2) {
      const [lang1, lang2] = langCodes;
      const [node1, node2] = await Promise.all([
        fetchDictionaryResultNode(query, lang1),
        fetchDictionaryResultNode(query, lang2),
      ]);
      dictionaryResults.prepend(buildBilingualResultBlock(dictionaryLanguage, lang1, node1, lang2, node2));
    } else {
      dictionaryResults.prepend(await fetchDictionaryResultNode(query, langCodes[0]));
    }
    dictionaryInput.select();
  } catch (err) {
    const line = document.createElement("p");
    line.className = "dictionary-empty";
    line.textContent = t.dictionaryError;
    dictionaryResults.prepend(line);
  } finally {
    dictionarySearchBtn.disabled = false;
  }
});

// "Mots similaires" / "Synonymes": the words closest to the typed
// expression in the "words" Qdrant collection (tenant = the chosen
// language), sorted from most to least similar, shown on a single line
// separated by commas. Stacked at the top like the dictionary results.
// See GET /api/similar_words / GET /api/synonyms (backend/app.py) +
// backend/qdrant_store.py. Returns the node without attaching it to the
// page — same reason as buildDictionaryResultNode() above.
function buildSimilarWordsResultNode(query, words) {
  const t = I18N[uiLanguage];
  const block = document.createElement("div");
  block.className = "dictionary-result";

  const heading = document.createElement("h3");
  heading.textContent = `${t.dictionarySimilarHeading} « ${query} »`;
  block.appendChild(heading);

  if (!words || !words.length) {
    const empty = document.createElement("p");
    empty.className = "dictionary-empty";
    empty.textContent = `${t.dictionaryNoResults} « ${query} »`;
    block.appendChild(empty);
  } else {
    const line = document.createElement("p");
    line.className = "dictionary-similar-line";
    // Each entry is {word, score} — the Qdrant score is shown in
    // parentheses with 2 decimals next to the word, at the user's
    // explicit request.
    line.textContent = words
      .map((x) => {
        const s = typeof x.score === "number" ? ` (${x.score.toFixed(2)})` : "";
        return `${x.word}${s}`;
      })
      .join(", ");
    block.appendChild(line);
  }
  return block;
}

// "Thématique": unlike buildSimilarWordsResultNode() above (reused as-is
// by "Synonymes", a single-stage direct Qdrant search), this button's
// search runs in two stages — see backend/app.py's _similar_words_impl —
// and both are shown, stacked in the same result block, at the user's
// explicit request: the LLM's own raw keyword expansion first, under
// "Champ lexical", then the Qdrant-compiled word list (scores included,
// same rendering as buildSimilarWordsResultNode) under "Glossaire
// thématique". Returns the node without attaching it to the page — same
// reason as buildDictionaryResultNode() above.
function buildThematicResultNode(query, keywords, words) {
  const t = I18N[uiLanguage];
  const block = document.createElement("div");
  block.className = "dictionary-result";

  const lexicalHeading = document.createElement("h3");
  lexicalHeading.textContent = `${t.dictionaryLexicalFieldHeading} « ${query} »`;
  block.appendChild(lexicalHeading);

  if (!keywords || !keywords.length) {
    const empty = document.createElement("p");
    empty.className = "dictionary-empty";
    empty.textContent = `${t.dictionaryNoResults} « ${query} »`;
    block.appendChild(empty);
  } else {
    const lexicalLine = document.createElement("p");
    lexicalLine.className = "dictionary-similar-line";
    lexicalLine.textContent = keywords.join(", ");
    block.appendChild(lexicalLine);
  }

  const glossaryHeading = document.createElement("h3");
  glossaryHeading.textContent = `${t.dictionaryThematicGlossaryHeading} « ${query} »`;
  block.appendChild(glossaryHeading);

  if (!words || !words.length) {
    const empty = document.createElement("p");
    empty.className = "dictionary-empty";
    empty.textContent = `${t.dictionaryNoResults} « ${query} »`;
    block.appendChild(empty);
  } else {
    const glossaryLine = document.createElement("p");
    glossaryLine.className = "dictionary-similar-line";
    glossaryLine.textContent = words
      .map((x) => {
        const s = typeof x.score === "number" ? ` (${x.score.toFixed(2)})` : "";
        return `${x.word}${s}`;
      })
      .join(", ");
    block.appendChild(glossaryLine);
  }
  return block;
}

// #theme-precision is an <input type="text"> (see index.html): its value
// is read while FORCING the dot as the decimal separator (a typed comma
// is normalized to a dot, at the user's explicit request — a comma
// separator is a source of confusion), clamped to [0, 1]. Returns
// undefined if the field is empty (the backend then applies
// THEME_MIN_SCORE). Shared by grid generation AND the Dictionnaire
// panel's own "Thématique" button.
function readThemePrecision() {
  const el = document.getElementById("theme-precision");
  if (!el) return undefined;
  const raw = el.value.trim().replace(",", ".");
  if (raw === "") return undefined;
  const n = Number(raw);
  if (!Number.isFinite(n)) return undefined;
  return Math.min(1, Math.max(0, n));
}

// On blur, rewrites the field with the normalized value (dot, clamped to
// [0,1]) — a typed "0,7" visibly becomes "0.7", "1.5" becomes "1".
(() => {
  const el = document.getElementById("theme-precision");
  if (!el) return;
  el.addEventListener("blur", () => {
    const v = readThemePrecision();
    if (v !== undefined) el.value = String(v);
  });
})();

// Queries /api/similar_words or /api/synonyms for a given language and
// returns the already-built result node — factored out so it can be
// called once or twice (bilingual) by "Thématique"/"Synonymes" below.
// "Thématique" (similar_words) additionally returns the LLM's own raw
// keyword expansion (`data.keywords`), rendered as its own "Champ
// lexical" section ahead of the Qdrant-compiled one — see
// buildThematicResultNode(); "Synonymes" (synonyms) has no such stage,
// so it keeps the plain single-list rendering.
async function fetchSimilarWordsResultNode(query, lang, endpoint, timeoutMs, errorMessage) {
  const prec = readThemePrecision();
  let url = `/api/${endpoint}?q=${encodeURIComponent(query)}&lang=${encodeURIComponent(lang)}`;
  if (prec !== undefined) url += `&min_score=${prec}`;
  const response = await fetchWithTimeout(url, {}, timeoutMs);
  if (!response.ok) throw new Error(errorMessage);
  const data = await response.json();
  if (endpoint === "similar_words") {
    return buildThematicResultNode(query, (data && data.keywords) || [], (data && data.words) || []);
  }
  return buildSimilarWordsResultNode(query, (data && data.words) || []);
}

dictionarySimilarBtn.addEventListener("click", async () => {
  const query = dictionaryInput.value.trim();
  if (!query) return;
  const t = I18N[uiLanguage];
  dictionarySimilarBtn.disabled = true;
  try {
    // The Dictionnaire's "Thématique" button reuses the generation
    // form's own "Précision thématique" field as its threshold
    // (min_score), at the user's explicit request — omitted if the field
    // is empty. Widened timeout: the backend does an LLM expansion of the
    // term before the Qdrant searches (see SIMILAR_FETCH_TIMEOUT_MS).
    const langCodes = splitLanguageValue(dictionaryLanguage.value);
    if (langCodes.length === 2) {
      const [lang1, lang2] = langCodes;
      const [node1, node2] = await Promise.all([
        fetchSimilarWordsResultNode(query, lang1, "similar_words", SIMILAR_FETCH_TIMEOUT_MS, t.dictionarySimilarError),
        fetchSimilarWordsResultNode(query, lang2, "similar_words", SIMILAR_FETCH_TIMEOUT_MS, t.dictionarySimilarError),
      ]);
      dictionaryResults.prepend(buildBilingualResultBlock(dictionaryLanguage, lang1, node1, lang2, node2));
    } else {
      dictionaryResults.prepend(
        await fetchSimilarWordsResultNode(query, langCodes[0], "similar_words", SIMILAR_FETCH_TIMEOUT_MS, t.dictionarySimilarError),
      );
    }
    dictionaryInput.select();
  } catch (err) {
    const line = document.createElement("p");
    line.className = "dictionary-empty";
    line.textContent = t.dictionarySimilarError;
    dictionaryResults.prepend(line);
  } finally {
    dictionarySimilarBtn.disabled = false;
  }
});

// "Synonymes": the same rendering as "Thématique"
// (buildSimilarWordsResultNode), but a direct Qdrant search on the typed
// word/expression, with NO LLM call to expand the search — at the user's
// explicit request. Generic timeout (FETCH_TIMEOUT_MS), not "Thématique"'s
// own widened timeout: there's no LLM round-trip to wait for here.
dictionarySynonymsBtn.addEventListener("click", async () => {
  const query = dictionaryInput.value.trim();
  if (!query) return;
  const t = I18N[uiLanguage];
  dictionarySynonymsBtn.disabled = true;
  try {
    const langCodes = splitLanguageValue(dictionaryLanguage.value);
    if (langCodes.length === 2) {
      const [lang1, lang2] = langCodes;
      const [node1, node2] = await Promise.all([
        fetchSimilarWordsResultNode(query, lang1, "synonyms", FETCH_TIMEOUT_MS, t.dictionarySynonymsError),
        fetchSimilarWordsResultNode(query, lang2, "synonyms", FETCH_TIMEOUT_MS, t.dictionarySynonymsError),
      ]);
      dictionaryResults.prepend(buildBilingualResultBlock(dictionaryLanguage, lang1, node1, lang2, node2));
    } else {
      dictionaryResults.prepend(
        await fetchSimilarWordsResultNode(query, langCodes[0], "synonyms", FETCH_TIMEOUT_MS, t.dictionarySynonymsError),
      );
    }
    dictionaryInput.select();
  } catch (err) {
    const line = document.createElement("p");
    line.className = "dictionary-empty";
    line.textContent = t.dictionarySynonymsError;
    dictionaryResults.prepend(line);
  } finally {
    dictionarySynonymsBtn.disabled = false;
  }
});

// "Définir": up to 10 independent definitions of the typed expression,
// generated by the LLM the same way as for a grid word (see
// backend/clues.py, LLMClueGenerator.generate_definitions), one per
// line. A single best-effort call on the backend side (no retry loop
// like grid generation has) — re-clicking is enough to retry. See
// DEFINE_FETCH_TIMEOUT_MS above for why this button uses a much longer
// timeout than the other two buttons on this same form. Returns the
// node without attaching it to the page — same reason as
// buildDictionaryResultNode() above.
function buildDefineResultNode(query, definitions) {
  const t = I18N[uiLanguage];
  const block = document.createElement("div");
  block.className = "dictionary-result";

  const heading = document.createElement("h3");
  heading.textContent = `${t.dictionaryDefineHeading} « ${query} »`;
  block.appendChild(heading);

  if (!definitions || !definitions.length) {
    const empty = document.createElement("p");
    empty.className = "dictionary-empty";
    empty.textContent = `${t.dictionaryNoResults} « ${query} »`;
    block.appendChild(empty);
  } else {
    const list = document.createElement("div");
    list.className = "dictionary-define-list";
    for (const definition of definitions) {
      const line = document.createElement("p");
      line.className = "dictionary-define-line";
      line.textContent = definition;
      list.appendChild(line);
    }
    block.appendChild(list);
  }
  return block;
}

// Queries /api/dictionary/define for a given language and returns the
// already-built node — factored out so it can be called once or twice
// (bilingual) by the handler below.
async function fetchDefineResultNode(query, lang) {
  const t = I18N[uiLanguage];
  const response = await fetchWithTimeout(
    `/api/dictionary/define?q=${encodeURIComponent(query)}&lang=${encodeURIComponent(lang)}`,
    {}, DEFINE_FETCH_TIMEOUT_MS,
  );
  if (!response.ok) throw new Error(t.dictionaryDefineError);
  const data = await response.json();
  return buildDefineResultNode(query, (data && data.definitions) || []);
}

dictionaryDefineBtn.addEventListener("click", async () => {
  const query = dictionaryInput.value.trim();
  if (!query) return;
  const t = I18N[uiLanguage];
  dictionaryDefineBtn.disabled = true;
  try {
    const langCodes = splitLanguageValue(dictionaryLanguage.value);
    if (langCodes.length === 2) {
      const [lang1, lang2] = langCodes;
      const [node1, node2] = await Promise.all([
        fetchDefineResultNode(query, lang1),
        fetchDefineResultNode(query, lang2),
      ]);
      dictionaryResults.prepend(buildBilingualResultBlock(dictionaryLanguage, lang1, node1, lang2, node2));
    } else {
      dictionaryResults.prepend(await fetchDefineResultNode(query, langCodes[0]));
    }
    dictionaryInput.select();
  } catch (err) {
    const line = document.createElement("p");
    line.className = "dictionary-empty";
    line.textContent = t.dictionaryDefineError;
    dictionaryResults.prepend(line);
  } finally {
    dictionaryDefineBtn.disabled = false;
  }
});

// ---------------------------------------------------------------------------
// "Paraphraseur" panel, at the user's explicit request: "On the main
// page, add a Paraphraseur tool. This tool is similar to the
// dictionary: a language selector, a text field... a Paraphraser button,
// a clear button, a Perplexity button." The "Paraphraser" button asks the
// LLM for 5 rewordings of the typed text (backend/clues.py's
// LLMClueGenerator.generate_paraphrases, GET /api/paraphrase); the same
// bilingual 50/50 behavior as the Dictionnaire above, directly reusing
// currentBilingualLangs()/refreshBilingualOption()/
// defaultToBilingualOption()/buildBilingualResultBlock()/
// splitLanguageValue() — a single mechanism, never duplicated.
// ---------------------------------------------------------------------------
function hideParaphrasePanel() {
  paraphrasePanel.hidden = true;
  syncRssPanelVisibility();
}

paraphraseBtn.addEventListener("click", () => {
  if (paraphrasePanel.hidden) {
    paraphrasePanel.hidden = false;
    paraphraseLanguage.value = uiLanguage;
    defaultToBilingualOption(paraphraseLanguage);
    syncRssPanelVisibility();
    paraphraseInput.focus();
  } else {
    hideParaphrasePanel();
  }
});

paraphraseCloseBtn.addEventListener("click", hideParaphrasePanel);

paraphraseClearBtn.addEventListener("click", () => {
  paraphraseResults.replaceChildren();
  paraphraseInput.value = "";
  paraphraseInput.focus();
});

// Language names "en <lang>"/"in <lang>" per SENTENCE language, DISTINCT
// from PERPLEXITY_LANGUAGE_ADJECTIVES above: "en français"/"in French"
// uses the language's own name (often masculine/default, or invariable),
// not the adjective agreed with a specific noun's gender ("la palabra
// española"/"la parola tedesca") that the table above provides — the two
// diverge in Spanish ("español" vs. "española"), Italian ("tedesco" vs.
// "tedesca"), Portuguese ("alemão" vs. "alemã") and German (the
// undeclined adverb "Deutsch" vs. the declined adjective "deutsche").
// French and English happen to coincide (both tables give them the same
// value) — no third set of values needed for them.
const PERPLEXITY_LANGUAGE_NAMES = {
  fr: { fr: "français", en: "anglais", de: "allemand", es: "espagnol", it: "italien", pt: "portugais" },
  en: { fr: "French", en: "English", de: "German", es: "Spanish", it: "Italian", pt: "Portuguese" },
  de: { fr: "Französisch", en: "Englisch", de: "Deutsch", es: "Spanisch", it: "Italienisch", pt: "Portugiesisch" },
  es: { fr: "francés", en: "inglés", de: "alemán", es: "español", it: "italiano", pt: "portugués" },
  it: { fr: "francese", en: "inglese", de: "tedesco", es: "spagnolo", it: "italiano", pt: "portoghese" },
  pt: { fr: "francês", en: "inglês", de: "alemão", es: "espanhol", it: "italiano", pt: "português" },
};

function perplexityLanguageName(sentenceLang, targetLang) {
  const table = PERPLEXITY_LANGUAGE_NAMES[sentenceLang] || PERPLEXITY_LANGUAGE_NAMES.fr;
  return table[targetLang] || targetLang;
}

// "Donner 5 paraphrases en <lang> : <texte>", at the user's explicit
// request — the same principle as PERPLEXITY_DEFINE_QUERY_TEMPLATES
// above (a combined language name "<name1>/<name2>" when two languages
// are selected, sentence in the first one's language), but with
// perplexityLanguageName() rather than perplexityLanguageAdjective() —
// see the note above on why the two must stay distinct.
const PERPLEXITY_PARAPHRASE_QUERY_TEMPLATES = {
  fr: (name, text) => `Donner 5 paraphrases en ${name} : ${text}`,
  en: (name, text) => `Give 5 paraphrases in ${name}: ${text}`,
  de: (name, text) => `Formuliere 5 Paraphrasen auf ${name}: ${text}`,
  es: (name, text) => `Da 5 paráfrasis en ${name}: ${text}`,
  it: (name, text) => `Dai 5 parafrasi in ${name}: ${text}`,
  pt: (name, text) => `Dê 5 paráfrases em ${name}: ${text}`,
};

paraphrasePerplexityBtn.addEventListener("click", () => {
  const text = paraphraseInput.value.trim();
  if (!text) {
    paraphraseInput.focus();
    return;
  }
  const langCodes = splitLanguageValue(paraphraseLanguage.value);
  const sentenceLang = langCodes[0];
  const name = langCodes
    .map((code) => perplexityLanguageName(sentenceLang, code))
    .join("/");
  const template =
    PERPLEXITY_PARAPHRASE_QUERY_TEMPLATES[sentenceLang] ||
    PERPLEXITY_PARAPHRASE_QUERY_TEMPLATES.fr;
  const url = `https://www.perplexity.ai/search?q=${encodeURIComponent(template(name, text))}`;
  window.open(url, "_blank", "noopener,noreferrer");
});

// 5 rewordings, one per line — the same presentation as "Définir"
// (.dictionary-define-list/.dictionary-define-line, reused as-is: a
// "one answer per line" list is a "one answer per line" list regardless
// of which tool produced it). Returns the node without attaching it to
// the page — same reason as buildDefineResultNode().
function buildParaphraseResultNode(query, paraphrases) {
  const t = I18N[uiLanguage];
  const block = document.createElement("div");
  block.className = "dictionary-result";

  const heading = document.createElement("h3");
  heading.textContent = `${t.paraphraseHeading} « ${query} »`;
  block.appendChild(heading);

  if (!paraphrases || !paraphrases.length) {
    const empty = document.createElement("p");
    empty.className = "dictionary-empty";
    empty.textContent = `${t.dictionaryNoResults} « ${query} »`;
    block.appendChild(empty);
  } else {
    const list = document.createElement("div");
    list.className = "dictionary-define-list";
    for (const p of paraphrases) {
      const line = document.createElement("p");
      line.className = "dictionary-define-line";
      line.textContent = p;
      list.appendChild(line);
    }
    block.appendChild(list);
  }
  return block;
}

async function fetchParaphraseResultNode(query, lang) {
  const t = I18N[uiLanguage];
  const response = await fetchWithTimeout(
    `/api/paraphrase?q=${encodeURIComponent(query)}&lang=${encodeURIComponent(lang)}`,
    {}, DEFINE_FETCH_TIMEOUT_MS,
  );
  if (!response.ok) throw new Error(t.paraphraseError);
  const data = await response.json();
  return buildParaphraseResultNode(query, (data && data.paraphrases) || []);
}

paraphraseForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  const query = paraphraseInput.value.trim();
  if (!query) return;
  const t = I18N[uiLanguage];
  paraphraseGenerateBtn.disabled = true;
  try {
    const langCodes = splitLanguageValue(paraphraseLanguage.value);
    if (langCodes.length === 2) {
      const [lang1, lang2] = langCodes;
      const [node1, node2] = await Promise.all([
        fetchParaphraseResultNode(query, lang1),
        fetchParaphraseResultNode(query, lang2),
      ]);
      paraphraseResults.prepend(buildBilingualResultBlock(paraphraseLanguage, lang1, node1, lang2, node2));
    } else {
      paraphraseResults.prepend(await fetchParaphraseResultNode(query, langCodes[0]));
    }
    paraphraseInput.select();
  } catch (err) {
    const line = document.createElement("p");
    line.className = "dictionary-empty";
    line.textContent = t.paraphraseError;
    paraphraseResults.prepend(line);
  } finally {
    paraphraseGenerateBtn.disabled = false;
  }
});

// ---------------------------------------------------------------------------
// "Qdrant (admin)" panel — a localhost-only maintenance view of the vector
// database (state + two quick actions + a link to Qdrant's own dashboard).
// The button stays hidden unless the page is served from this machine
// (showQdrantAdminOnLocalhost below); frontend/server.py also rejects
// /api/qdrant/admin/* for any non-loopback client/Host. Same toggling
// group as #library / #dictionary / #attempt-preview / #result.
// ---------------------------------------------------------------------------
function hideQdrantAdminPanel() {
  qdrantAdminPanel.hidden = true;
  syncRssPanelVisibility();
}

(function showQdrantAdminOnLocalhost() {
  if (isLocalhostOrigin()) qdrantAdminBtn.hidden = false;
})();

function qdrantAdminPara(text, cls) {
  const p = document.createElement("p");
  if (cls) p.className = cls;
  p.textContent = text;
  return p;
}

function qdrantAdminCount(n) {
  return typeof n === "number" ? n.toLocaleString(uiLanguage) : "—";
}

function qdrantAdminRecreateButton() {
  const t = I18N[uiLanguage];
  const b = document.createElement("button");
  b.type = "button";
  b.className = "nav-btn";
  b.textContent = t.qdrantAdminRecreateBtn;
  b.addEventListener("click", qdrantAdminRecreate);
  return b;
}

function qdrantAdminAppendExtras(data) {
  const t = I18N[uiLanguage];
  const a = document.createElement("a");
  a.href = data.dashboard_url;
  a.target = "_blank";
  a.rel = "noopener noreferrer";
  a.className = "qdrant-admin-dashboard-link";
  a.textContent = t.qdrantAdminDashboardLink;
  qdrantAdminBody.appendChild(a);
  qdrantAdminBody.appendChild(
    qdrantAdminPara(t.qdrantAdminPopulateHint, "qdrant-admin-hint"),
  );
}

// Builds the panel body from GET /api/qdrant/admin. Pure DOM (textContent
// only); the one bit of dynamic markup is the dashboard <a>, whose href is
// the server-provided base_url + "/dashboard".
function renderQdrantAdmin(data) {
  const t = I18N[uiLanguage];
  qdrantAdminBody.replaceChildren();
  qdrantAdminBody.appendChild(
    qdrantAdminPara(`${data.base_url} · ${data.collection}`, "qdrant-admin-endpoint"),
  );

  if (!data.reachable) {
    qdrantAdminBody.appendChild(
      qdrantAdminPara(t.qdrantAdminUnreachable, "qdrant-admin-empty"),
    );
    return;
  }
  if (!data.exists) {
    qdrantAdminBody.appendChild(
      qdrantAdminPara(t.qdrantAdminNoCollection, "qdrant-admin-empty"),
    );
    qdrantAdminBody.appendChild(qdrantAdminRecreateButton());
    qdrantAdminAppendExtras(data);
    return;
  }

  const v = data.vector || {};
  const stats = document.createElement("div");
  stats.className = "qdrant-admin-stats";
  const rows = [
    [t.qdrantAdminStatVector,
     `${v.size || "—"} · ${v.distance || "—"} · `
     + `${v.on_disk ? t.qdrantAdminOnDisk : t.qdrantAdminInMemory}`],
    [t.qdrantAdminStatStatus,
     `${data.status || "—"}`
     + `${data.optimizer_status ? " / " + data.optimizer_status : ""}`],
    [t.qdrantAdminStatPoints, qdrantAdminCount(data.points_count)],
    [t.qdrantAdminStatIndexed, qdrantAdminCount(data.indexed_vectors_count)],
    [t.qdrantAdminStatSegments, qdrantAdminCount(data.segments_count)],
    [t.qdrantAdminStatTenantIndex,
     data.tenant_index ? t.qdrantAdminYes : t.qdrantAdminNo],
  ];
  for (const [k, val] of rows) {
    const row = document.createElement("div");
    const kEl = document.createElement("span");
    kEl.textContent = k;
    const vEl = document.createElement("span");
    vEl.textContent = val;
    row.append(kEl, vEl);
    stats.appendChild(row);
  }
  qdrantAdminBody.appendChild(stats);

  const wrap = document.createElement("div");
  wrap.className = "table-scroll";
  const table = document.createElement("table");
  table.className = "qdrant-admin-table";
  const thead = document.createElement("thead");
  const htr = document.createElement("tr");
  for (const h of [t.qdrantAdminColLang, t.qdrantAdminColCount, ""]) {
    const th = document.createElement("th");
    th.textContent = h;
    htr.appendChild(th);
  }
  thead.appendChild(htr);
  table.appendChild(thead);
  const tbody = document.createElement("tbody");
  const langs = data.languages || {};
  for (const code of Object.keys(langs)) {
    const tr = document.createElement("tr");
    const c1 = document.createElement("td");
    c1.textContent = code;
    const c2 = document.createElement("td");
    c2.textContent = qdrantAdminCount(langs[code]);
    const c3 = document.createElement("td");
    const del = document.createElement("button");
    del.type = "button";
    del.className = "nav-btn";
    del.textContent = t.qdrantAdminDeleteTenantBtn;
    del.disabled = !langs[code];
    del.addEventListener("click", () => qdrantAdminDeleteTenant(code));
    c3.appendChild(del);
    tr.append(c1, c2, c3);
    tbody.appendChild(tr);
  }
  table.appendChild(tbody);
  wrap.appendChild(table);
  qdrantAdminBody.appendChild(wrap);

  const actions = document.createElement("div");
  actions.className = "qdrant-admin-actions";
  actions.appendChild(qdrantAdminRecreateButton());
  qdrantAdminBody.appendChild(actions);

  qdrantAdminAppendExtras(data);
}

async function loadQdrantAdmin() {
  const t = I18N[uiLanguage];
  qdrantAdminBody.replaceChildren(
    qdrantAdminPara(t.qdrantAdminLoading, "qdrant-admin-empty"),
  );
  try {
    const response = await fetchWithTimeout("/api/qdrant/admin", {}, FETCH_TIMEOUT_MS);
    if (!response.ok) throw new Error();
    renderQdrantAdmin(await response.json());
  } catch (err) {
    qdrantAdminBody.replaceChildren(
      qdrantAdminPara(t.qdrantAdminActionError, "qdrant-admin-empty"),
    );
  }
}

async function qdrantAdminRecreate() {
  const t = I18N[uiLanguage];
  if (!window.confirm(t.qdrantAdminRecreateConfirm)) return;
  try {
    const response = await fetchWithTimeout(
      "/api/qdrant/admin/recreate", { method: "POST" }, FETCH_TIMEOUT_MS,
    );
    if (!response.ok) throw new Error();
  } catch (err) {
    window.alert(t.qdrantAdminActionError);
  }
  loadQdrantAdmin();
}

async function qdrantAdminDeleteTenant(lang) {
  const t = I18N[uiLanguage];
  if (!window.confirm(t.qdrantAdminDeleteTenantConfirm(lang))) return;
  try {
    const response = await fetchWithTimeout(
      "/api/qdrant/admin/delete-tenant",
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ lang }),
      },
      FETCH_TIMEOUT_MS,
    );
    if (!response.ok) throw new Error();
  } catch (err) {
    window.alert(t.qdrantAdminActionError);
  }
  loadQdrantAdmin();
}

qdrantAdminBtn.addEventListener("click", () => {
  if (qdrantAdminPanel.hidden) {
    qdrantAdminPanel.hidden = false;
    syncRssPanelVisibility();
    loadQdrantAdmin();
  } else {
    hideQdrantAdminPanel();
  }
});
qdrantAdminRefreshBtn.addEventListener("click", loadQdrantAdmin);
qdrantAdminCloseBtn.addEventListener("click", hideQdrantAdminPanel);

// "David FALCON" chat widget, at the user's explicit request: "Bottom
// right of the interface, add a ChatBot (open by default) with the
// app's own icon... It shows a welcome message... The welcome message
// must be rewritten if the user changes the language." `chatHistory`
// only ever holds genuine user/assistant turns
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
// Opaque identifier, generated once per page load, at the user's
// explicit request: "For each conversation in the ChatBot, create a LOG
// of the questions/answers... One file per user session." Sent on every
// message (see further below) so backend/app.py routes every turn of
// this same conversation to the same log file. `crypto.randomUUID()` is
// available in every modern browser serving this page over HTTPS/
// localhost (the only two contexts where the Web Crypto API is exposed);
// a plain fallback (timestamp + random number) covers the other case
// rather than crashing the whole chat over an identifier that only needs
// to be reasonably unique, never cryptographically secure.
function newChatSessionId() {
  return (window.crypto && window.crypto.randomUUID)
    ? window.crypto.randomUUID()
    : `${Date.now()}-${Math.random().toString(36).slice(2)}`;
}
// `let`, not `const`: the conversation-reset button (#chatbot-reset-btn,
// see further below) regenerates a new one, so the restarted
// conversation is tracked in a new LOG_CHAT file on backend/app.py's
// side rather than being appended after the previous one.
let chatSessionId = newChatSessionId();

function escapeHtml(text) {
  return text.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
}

// A small, self-contained Markdown-to-HTML renderer for David FALCON's own
// replies, at the user's explicit request: "The Bot's display must be
// able to format Markdown produced by the LLM." Deliberately not a
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
// user's explicit request: "For every question from the user, the LLM
// is told about... the interface's state, including the position and
// direction of any word selected in the playable grid [and] the
// definitions list... row and column number of each word, vertical or
// horizontal, definition, the word's value (answer)." Reports
// two genuinely distinct concepts, at the user's own later, explicit
// clarification: "a word is selected by hovering the mouse over it,
// without necessarily clicking a cell. Make the distinction between the
// 'selected word' (hover) and the 'cell/word being filled in' (clicked)."
//   - `hovered_word` — the word currently framed by the mouse-hover
//     highlight (`hoveredWord`, see highlightWordAt()/clearHighlights()),
//     already resolved to one specific word's own (row, col, direction) —
//     this is what "which word is selected" actually refers to.
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
    // The grid's current fill/selection direction ("across"/"down", the
    // shared activeDirection state driven by the Across/Down buttons and
    // Shift/CapsLock) — lets the backend pick WHICH of two crossing words
    // a clicked cell is about (and, on a bilingual grid, in which
    // language to answer) instead of having to ask the player.
    active_direction: activeDirection,
    words: puzzle
      ? puzzle.words.map((w) => ({
          row: w.row, col: w.col, direction: w.direction, clue: w.clue, answer: w.answer,
          // The language actually used for THIS word (see backend/
          // crossword_gen.py's generate_grid — every word carries its own
          // `language` according to its direction on a bilingual grid) —
          // at the user's explicit request, so David FALCON replies in
          // the word's own language when help filling it in is asked
          // for. The same for every word on an ordinary grid, so no
          // effect in that case.
          language: w.language,
        }))
      : [],
  };
}

// Open/collapsed state, at the user's explicit request: remembered across
// a reload for the rest of the current day only — a cookie whose max-age is
// computed down to local midnight, rather than the `cwf-prefs` cookie's own
// one-year lifetime, so a new day always finds the ChatBot open again, its
// normal default.
const CHATBOT_STATE_COOKIE = "cwf-chatbot-state";

function saveChatbotStateCookie(collapsed) {
  try {
    const midnight = new Date();
    midnight.setHours(24, 0, 0, 0);
    const maxAge = Math.max(1, Math.round((midnight.getTime() - Date.now()) / 1000));
    document.cookie = `${CHATBOT_STATE_COOKIE}=${collapsed ? "collapsed" : "open"}; path=/; max-age=${maxAge}; samesite=lax`;
  } catch (e) {
    // Cookies disabled — the ChatBot just reopens on the next load.
  }
}

function loadChatbotStateCookie() {
  const m = document.cookie.match(/(?:^|;\s*)cwf-chatbot-state=([^;]*)/);
  return m ? m[1] : null;
}

function toggleChatbotCollapsed() {
  chatbotEl.classList.toggle("chatbot-collapsed");
  saveChatbotStateCookie(chatbotEl.classList.contains("chatbot-collapsed"));
}
if (loadChatbotStateCookie() === "collapsed") chatbotEl.classList.add("chatbot-collapsed");
chatbotToggleBtn.addEventListener("click", toggleChatbotCollapsed);
// Clickable icon in collapsed mode (the "–" button itself is then
// hidden, see style.css), at the user's explicit request: "In 'collapsed'
// mode, the ChatBot should show the site's icon" — the same toggle
// function as the button, reused as-is rather than duplicated.
document.getElementById("chatbot-icon").addEventListener("click", toggleChatbotCollapsed);

// Reads POST /api/chat's own text/event-stream body (frontend/server.py
// relays it chunk by chunk, backend/app.py/backend/chatbot.py's own
// ChatBot.reply_stream() produce it) and calls `onDelta(text)` for every
// `{"delta": ...}` event as it arrives, in order — at the user's
// explicit request: "The Bot must display the reply as it streams in."
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

// "Reset conversation" icon button, at the user's explicit request:
// clears the client-side history (chatHistory), resets chatUserHasSpoken
// to false so the welcome message becomes re-translatable on a language
// change again, regenerates a new session identifier (a new LOG_CHAT
// file on the backend side), and re-shows the welcome message.
if (chatbotResetBtn) {
  chatbotResetBtn.addEventListener("click", () => {
    chatHistory = [];
    chatUserHasSpoken = false;
    chatSessionId = newChatSessionId();
    chatbotInput.value = "";
    renderChatWelcome();
    chatbotInput.focus();
  });
}

renderChatWelcome();

// ===========================================================================
// "Interactif" authoring mode — build ONE grid word by word, hand-write
// every clue, propose a title, save as a "(Création)". Reuses #result /
// #grid / renderGrid() via a synthesized `puzzle`; every hook into a
// shared play-mode function (selectCell/renderGrid/handleKeydown/
// typeVirtualLetter/setActiveDirection/updateHoverForModifierKey) is a
// leading `if (interactiveMode)` fast-path, so play mode is untouched.
// ===========================================================================

function interactiveKey(w) {
  return `${w.startRow},${w.startCol},${w.direction}`;
}

// True when every white cell of interactiveGrid carries a letter.
function interactiveGridFilled() {
  return interactiveGrid.every((row) => row.every((ch) => ch === "#" || ch !== ""));
}

// Populate the impossible / low-fill-option highlight sets from a backend
// interactive response (start job result, or a /step response).
function setInteractiveDiagnostics(data) {
  interactiveImpossibleCells = new Set((data.impossible_cells || []).map(([r, c]) => `${r},${c}`));
  interactiveLowCells = new Set((data.low_candidate_cells || []).map(([r, c]) => `${r},${c}`));
  interactiveDeadlockCells = new Set((data.deadlock_cells || []).map(([r, c]) => `${r},${c}`));
  interactiveExcludedCells = new Set((data.excluded_cells || []).map(([r, c]) => `${r},${c}`));
  interactiveWindowCells = new Set((data.window_cells || []).map(([r, c]) => `${r},${c}`));
}

// The diagnostics describe the grid the backend last saw; drop them the
// moment the player edits by hand so a stale highlight is never shown.
function clearInteractiveDiagnostics() {
  interactiveImpossibleCells = new Set();
  interactiveLowCells = new Set();
  interactiveDeadlockCells = new Set();
  interactiveExcludedCells = new Set();
  interactiveWindowCells = new Set();
  interactiveInvalidCells = new Set();
  interactiveVerifyReport = [];
  interactiveStatLetters = new Map();
  interactiveStatsGridKey = null;
  interactiveLastPlaced = null;
}

// Everything the grid is SHOWING beyond its own letters: every diagnostic
// overlay above, the theme/"Mots Défi" letter colouring, the "Stats"
// letters, the selected zone, and the word the last "Suivant" placed.
// Sent with every autosave (see autosaveInteractiveWork) so a GRID_WORK
// record — and, through its own `previous` field, the state displayed
// right before the last click — can be analysed afterwards exactly as the
// player saw it. Several of these lists exist nowhere else: an
// "emplacement écarté" in particular is rebuilt from scratch by each
// `POST /api/interactive/step` call and kept nowhere afterwards, so it is
// unrecoverable from the saved grid alone. Diagnostic-only — nothing
// reads it back to rebuild a session.
function interactiveDiagnosticsPayload() {
  const cells = (set) => [...set].map((key) => key.split(",").map(Number));
  return {
    impossible_cells: cells(interactiveImpossibleCells),
    deadlock_cells: cells(interactiveDeadlockCells),
    low_candidate_cells: cells(interactiveLowCells),
    excluded_cells: cells(interactiveExcludedCells),
    window_cells: cells(interactiveWindowCells),
    invalid_cells: cells(interactiveInvalidCells),
    theme_cells: cells(interactiveThemeCells),
    challenge_cells: cells(interactiveChallengeCells),
    // [row, col, letter] triples, the same shape POST /api/interactive/
    // stats itself returns.
    stat_letters: [...interactiveStatLetters].map(
      ([key, letter]) => [...key.split(",").map(Number), letter],
    ),
    zone_cells: interactiveZoneSelection ? cells(interactiveZoneSelection) : null,
    last_placed: interactiveLastPlaced,
  };
}

function setInteractiveMessage(text, isError) {
  interactiveMessage.textContent = text || "";
  interactiveMessage.classList.toggle("error", !!isError);
}

// Scans interactiveGrid for every white run >= 2 (rows then columns),
// numbering start cells left-to-right / top-to-bottom (shared between the
// across and down word starting at the same cell) — mirrors
// extract_slots + build_word_entries.
function interactiveSlots() {
  const rows = interactiveGrid.length;
  const cols = rows ? interactiveGrid[0].length : 0;
  const isW = (r, c) => interactiveGrid[r][c] !== "#";
  const slots = [];
  const addSlot = (cells, direction) => {
    const first = cells[0];
    slots.push({
      cells,
      direction,
      startRow: first.row,
      startCol: first.col,
      answer: cells.map((p) => interactiveGrid[p.row][p.col] || "").join(""),
      filled: cells.every((p) => (interactiveGrid[p.row][p.col] || "") !== ""),
    });
  };
  for (let r = 0; r < rows; r++) {
    let c = 0;
    while (c < cols) {
      if (isW(r, c)) {
        const start = c;
        while (c < cols && isW(r, c)) c++;
        if (c - start >= 2) {
          const cells = [];
          for (let cc = start; cc < c; cc++) cells.push({ row: r, col: cc });
          addSlot(cells, "across");
        }
      } else {
        c++;
      }
    }
  }
  for (let c = 0; c < cols; c++) {
    let r = 0;
    while (r < rows) {
      if (isW(r, c)) {
        const start = r;
        while (r < rows && isW(r, c)) r++;
        if (r - start >= 2) {
          const cells = [];
          for (let rr = start; rr < r; rr++) cells.push({ row: rr, col: c });
          addSlot(cells, "down");
        }
      } else {
        r++;
      }
    }
  }
  // Number the start cells (row-major), sharing a number between the
  // across/down word starting at the same cell.
  const startKeys = [...new Set(slots.map((s) => `${s.startRow},${s.startCol}`))]
    .sort((a, b) => {
      const [ar, ac] = a.split(",").map(Number);
      const [br, bc] = b.split(",").map(Number);
      return ar - br || ac - bc;
    });
  const numberByStart = new Map(startKeys.map((k, i) => [k, i + 1]));
  for (const s of slots) s.number = numberByStart.get(`${s.startRow},${s.startCol}`);
  return slots;
}

// The rectangle of "row,col" strings between the drag's start and current
// cell (inclusive both ends) — used both for the live drag preview and to
// build the final selection once the drag ends.
function interactiveDragRectCells() {
  const cells = new Set();
  if (!interactiveDragStart || !interactiveDragCurrent) return cells;
  const r0 = Math.min(interactiveDragStart.row, interactiveDragCurrent.row);
  const r1 = Math.max(interactiveDragStart.row, interactiveDragCurrent.row);
  const c0 = Math.min(interactiveDragStart.col, interactiveDragCurrent.col);
  const c1 = Math.max(interactiveDragStart.col, interactiveDragCurrent.col);
  for (let r = r0; r <= r1; r++) {
    for (let c = c0; c <= c1; c++) cells.add(`${r},${c}`);
  }
  return cells;
}

// Expands a raw set of "row,col" cells (the dragged rectangle) to every
// FULL emplacement (across and/or down) sharing at least one cell with
// it, at the user's explicit request: "select every slot that shares at
// least one letter with the selected zone." A
// single pass only — a cell added purely because it belongs to one of
// these newly-included emplacements does NOT, in turn, pull in whichever
// OTHER emplacement crosses it; only the originally-dragged cells decide
// which emplacements join the selection, keeping this deterministic and
// bounded rather than a potentially grid-wide snowball.
function interactiveExpandZoneSelection(rawCells) {
  const selection = new Set(rawCells);
  for (const slot of interactiveSlots()) {
    if (slot.cells.some(({ row, col }) => rawCells.has(`${row},${col}`))) {
      for (const { row, col } of slot.cells) selection.add(`${row},${col}`);
    }
  }
  return selection;
}

// Live, lightweight visual feedback while a drag is in progress — toggles
// a CSS class directly on the already-rendered cell elements rather than
// a full renderGrid() call on every mouse move, which would be needlessly
// heavy and could disturb the typing-selection state mid-drag.
// Live feedback while a drag is in progress, at the user's explicit
// request: "While selecting a zone, show the grayed-out backgrounds in
// real time." Computes the SAME whole-emplacement expansion the drag
// will actually produce on drop (interactiveExpandZoneSelection) from the
// current rectangle, and grays out every cell NOT in it — exactly the
// final .zone-unselected overlay renderGrid() itself applies once
// interactiveZoneSelection is set, just recomputed on the fly here
// without waiting for the drag to end (and without touching
// interactiveZoneSelection itself, which is only ever updated on
// mouseup). A cell actually part of the raw dragged rectangle can never
// end up grayed out (interactiveExpandZoneSelection always includes it),
// so the two overlays below never conflict on the same cell.
function renderInteractiveDragPreview() {
  const rect = interactiveDragRectCells();
  const previewSelection = interactiveExpandZoneSelection(rect);
  cellElements.forEach((el, key) => {
    el.classList.toggle("zone-drag-preview", rect.has(key));
    el.classList.toggle("zone-unselected", !previewSelection.has(key));
  });
}

// Starts/continues a click-drag zone selection on one grid cell — see
// interactiveZoneSelection's own docstring. Mousedown begins tracking;
// mouseenter (while a drag is already active) extends the live preview
// rectangle to the newly-entered cell. The drag is only ever FINALIZED
// by the single document-level "mouseup" listener registered once below
// (not per cell), since the pointer can legitimately be released
// anywhere on the page, not necessarily over a grid cell.
function attachInteractiveDragHandlers(cell, r, c) {
  cell.addEventListener("mousedown", (e) => {
    if (!interactiveMode) return;
    // Prevents the browser's own native text-selection drag, which would
    // otherwise fight with this custom one (grid cells hold real text
    // nodes for their letters/numbers).
    e.preventDefault();
    interactiveDragStart = { row: r, col: c };
    interactiveDragCurrent = { row: r, col: c };
    interactiveDragActive = true;
    // Deliberately does NOT call renderInteractiveDragPreview() here — at
    // the user's explicit request: "When no zone is selected, a click
    // should not change the display... only select the word and the cell
    // to fill in." Showing the live preview
    // already on mousedown (before any real movement) would flash a
    // one-cell-wide gray overlay over the WHOLE grid for every ordinary
    // click, and could even briefly override an already-selected zone's
    // own correct gray pattern with a bogus one-cell version. The
    // preview only ever starts once the pointer genuinely reaches a
    // DIFFERENT cell (see the "mouseenter" listener below) — a plain
    // click, with no movement at all, never triggers it, so the mouseup
    // handler below can then safely do nothing when nothing actually
    // changed, leaving the plain "click" event's own selectCell() as the
    // only thing that reacts to it.
  });
  cell.addEventListener("mouseenter", () => {
    if (!interactiveMode || !interactiveDragActive) return;
    interactiveDragCurrent = { row: r, col: c };
    renderInteractiveDragPreview();
  });
}

// Finalizes a click-drag zone selection the moment the mouse button is
// released anywhere on the page (not only over a grid cell) — registered
// once here rather than per cell, since interactiveDragActive/Start/
// Current already carry every bit of state this needs. A drag that never
// actually moved (mousedown+mouseup on the very same cell, i.e. an
// ordinary single-cell click) is deliberately left as a no-op:
// selectCell()'s own "click" listener already handles that case, and
// interactiveZoneSelection is left completely untouched, so the player
// can freely click around (type, run "Croisés"/"Mots", ...) without
// losing an already drag-selected zone — only dragging a genuinely new
// rectangle ever replaces it.
document.addEventListener("mouseup", () => {
  if (!interactiveDragActive) return;
  interactiveDragActive = false;
  const startCell = interactiveDragStart;
  const moved = interactiveDragStart && interactiveDragCurrent && (
    interactiveDragStart.row !== interactiveDragCurrent.row
    || interactiveDragStart.col !== interactiveDragCurrent.col
  );
  const rect = interactiveDragRectCells();
  interactiveDragStart = null;
  interactiveDragCurrent = null;
  let changed = false;
  if (moved) {
    interactiveZoneSelection = interactiveExpandZoneSelection(rect);
    changed = true;
  } else if (
    startCell && interactiveZoneSelection
    && !interactiveZoneSelection.has(`${startCell.row},${startCell.col}`)
  ) {
    // Clicking a grayed-out ("not part of the zone") cell deselects the
    // whole zone, at the user's explicit request: "When the user clicks a
    // grayed-out cell, deselect." — a quick, discoverable
    // way to clear a selection without reaching for Escape or dragging a
    // brand-new one.
    interactiveZoneSelection = null;
    changed = true;
  }
  // Only re-render when interactiveZoneSelection genuinely changed — at
  // the user's explicit request: "When no zone is selected, a click
  // should not change the display..., only select the word and the cell
  // to fill in." A plain click that changes
  // nothing here (no zone existed, or the click landed inside an already-
  // selected zone) needs no cleanup either: renderInteractiveDragPreview()
  // is never called for a click with no real movement (see
  // attachInteractiveDragHandlers()'s own mousedown handler), so no
  // .zone-drag-preview/.zone-unselected class was ever applied in the
  // first place — the ordinary "click" event's own selectCell() remains
  // the only thing that reacts to it, exactly like before this whole
  // feature existed.
  if (changed) {
    renderInteractive();
  }
});

// Escape clears an active drag-selected zone (Édition mode only) — the
// only other way to clear one short of dragging a fresh replacement or
// leaving Interactive mode entirely (see hideInteractivePanel()).
// shouldGridIgnoreKeydown() keeps this from firing while the player is
// typing in an unrelated text field (chat, dictionary search, ...).
document.addEventListener("keydown", (event) => {
  if (
    interactiveMode && interactiveZoneSelection
    && event.key === "Escape" && !shouldGridIgnoreKeydown()
  ) {
    interactiveZoneSelection = null;
    renderInteractive();
  }
});

// The word running through `selected` in the current activeDirection, as
// an interactiveSlots()-shaped object, or null (black/isolated cell).
function selectedInteractiveWord() {
  if (!selected) return null;
  const rows = interactiveGrid.length;
  const cols = rows ? interactiveGrid[0].length : 0;
  const { row, col } = selected;
  if (interactiveGrid[row][col] === "#") return null;
  const isW = (r, c) => r >= 0 && r < rows && c >= 0 && c < cols && interactiveGrid[r][c] !== "#";
  const cells = [];
  if (activeDirection === "across") {
    let c = col;
    while (isW(row, c - 1)) c--;
    for (; isW(row, c); c++) cells.push({ row, col: c });
  } else {
    let r = row;
    while (isW(r - 1, col)) r--;
    for (; isW(r, col); r++) cells.push({ row: r, col });
  }
  if (cells.length < 2) return null;
  const first = cells[0];
  const w = {
    cells,
    direction: activeDirection,
    startRow: first.row,
    startCol: first.col,
    answer: cells.map((p) => interactiveGrid[p.row][p.col] || "").join(""),
  };
  // `w.answer` is built by `.map(...).join("")`, which silently drops any
  // empty ("" — not-yet-filled) cell rather than keeping a placeholder for
  // it — so a word missing even one letter always comes back SHORTER
  // than `cells.length`, and the length check alone is already the right,
  // sufficient test. A second check used to also require
  // `!w.answer.includes("")` — removed: `String.prototype.includes("")`
  // is ALWAYS `true` for any string in JavaScript (the empty string
  // trivially matches everywhere), so that condition was always `false`
  // and `w.filled` could therefore never be `true` at all — the real
  // cause of "Proposer" always answering "Select a fully filled word",
  // reported directly by the user, regardless of which word was
  // actually selected.
  w.filled = w.answer.length === cells.length;
  return w;
}

// Feeds renderGrid() a synthesized puzzle/userLetters from interactiveGrid.
function syncPuzzleFromInteractive() {
  const rows = interactiveGrid.length;
  const cols = rows ? interactiveGrid[0].length : 0;
  puzzle = {
    width: cols,
    height: rows,
    pattern: interactiveGrid.map((row) => row.map((ch) => (ch === "#" ? BLACK : WHITE))),
    solution: interactiveGrid.map((row) => row.map((ch) => (/[A-Z]/.test(ch) ? ch : "#"))),
    words: interactiveSlots().map((s) => ({
      number: s.number,
      direction: s.direction,
      row: s.startRow,
      col: s.startCol,
      length: s.cells.length,
      answer: s.answer,
      clue: interactiveDefs.get(interactiveKey(s)) || "",
    })),
    difficulty: interactiveDifficulty,
    language: interactiveLanguage,
    // "" (falsy) for an ordinary monolingual session — currentBilingualLangs()
    // already treats a bilingual_language identical to/absent from
    // language as "no bilingual pair", exactly like a real generated
    // puzzle does, so this needs no extra normalization here.
    bilingual_language: interactiveBilingualLanguage || null,
  };
  userLetters = interactiveGrid.map((row) => row.map((ch) => (/[A-Z]/.test(ch) ? ch : "")));
  showSolution = false;
  checking = false;
}

function updateInteractiveFinishState() {
  const slots = interactiveSlots();
  const everyCellFilled = interactiveGridFilled();
  const everyDefFilled =
    slots.length > 0 && slots.every((s) => (interactiveDefs.get(interactiveKey(s)) || "").trim());
  const complete = everyCellFilled && everyDefFilled;
  interactiveTitleRow.hidden = !complete;
  // "Publier" (#interactive-save-btn) only ever shows once the grid is
  // complete; the always-visible "Sauvegarder" (#interactive-draft-save-btn,
  // draft to GRID_WORK, no publish) sits to its left.
  interactiveSaveBtn.hidden = !complete;
  if (complete && !interactiveTitleProposed && !interactiveTitleInput.value.trim()) {
    proposeInteractiveTitle(true);
  }
}

// Renders interactiveVerifyReport as one <p> per flagged word, below the
// definition input, at the user's explicit request: "a report showing
// the problems found with each word. One word per line." Reads the
// state rather than being handed it directly, and is never itself
// responsible for clearing it (clearInteractiveDiagnostics() does that,
// on any edit) — so calling it from renderInteractive(), including the
// one renderInteractive() call the "Vérifier" handler itself makes right
// after populating the report, always shows the current, correct content
// instead of wiping it out. Same H/V + 1-based (row, col) convention
// already established for the word-verification table (renderWordTable).
function renderInteractiveVerifyReport() {
  interactiveVerifyReportEl.innerHTML = "";
  if (!interactiveVerifyReport.length) {
    interactiveVerifyReportEl.hidden = true;
    return;
  }
  for (const entry of interactiveVerifyReport) {
    const line = document.createElement("p");
    line.className = "interactive-verify-report-line";
    const dirPrefix = entry.direction === "across" ? "H" : "V";
    line.textContent =
      `${dirPrefix} (${entry.row + 1}, ${entry.col + 1}) ${entry.answer} : ${entry.reasons.join(", ")}`;
    interactiveVerifyReportEl.appendChild(line);
  }
  interactiveVerifyReportEl.hidden = false;
}

// Black-cell / white-cell-fill percentages, shown above the grid in
// Interactive mode — at the user's explicit request. Two independent
// denominators, not the same one: the black percentage is measured
// against the WHOLE grid (how much of it is black at all), while the
// fill percentage is measured against WHITE cells only (how much of the
// actually fillable area already carries a real letter) — a plain
// "filled / total cells" figure would understate progress on a
// heavily-blackened grid, whereas this reads as a genuine completion
// rate for the part of the puzzle still being worked on.
function renderInteractiveCellStats() {
  const rows = interactiveGrid.length;
  const cols = rows ? interactiveGrid[0].length : 0;
  let blackCount = 0;
  let whiteCount = 0;
  let filledCount = 0;
  for (let r = 0; r < rows; r++) {
    for (let c = 0; c < cols; c++) {
      const ch = interactiveGrid[r][c];
      if (ch === "#") {
        blackCount++;
      } else {
        whiteCount++;
        if (/[A-Z]/.test(ch)) filledCount++;
      }
    }
  }
  const totalCells = rows * cols;
  const blackPercent = totalCells ? Math.round((100 * blackCount) / totalCells) : 0;
  const fillPercent = whiteCount ? Math.round((100 * filledCount) / whiteCount) : 0;
  interactiveBlackStat.textContent = I18N[uiLanguage].interactiveBlackPercent(blackPercent);
  interactiveFillStat.textContent = I18N[uiLanguage].interactiveFillPercent(fillPercent);
  interactiveCellStats.hidden = false;
}

function renderInteractive() {
  // Drop any theme-word cell that no longer carries a letter (undo, erase,
  // toggle-to-black) so the magenta mark tracks the real grid content.
  for (const key of interactiveThemeCells) {
    const [r, c] = key.split(",").map(Number);
    if (!/[A-Z]/.test(interactiveGrid[r] && interactiveGrid[r][c])) {
      interactiveThemeCells.delete(key);
    }
  }
  // Same pruning for a "Mots Défi" cell — see interactiveChallengeCells.
  for (const key of interactiveChallengeCells) {
    const [r, c] = key.split(",").map(Number);
    if (!/[A-Z]/.test(interactiveGrid[r] && interactiveGrid[r][c])) {
      interactiveChallengeCells.delete(key);
    }
  }
  syncPuzzleFromInteractive();
  renderGrid();
  renderInteractiveCellStats();
  scheduleInteractiveStatsRefresh();
  // "Mots Défi" list's own green/black coloring tracks the live grid — at
  // the user's explicit request (see gridContainsWord()'s own docstring).
  renderInteractiveChallengeList();
  const w = selectedInteractiveWord();
  // Falls back to the word itself when no clue has been written yet AND
  // the word is already entirely filled in — at the user's explicit
  // request: "Also automatically fill in the 'Proposer une définition'
  // input field the same way." Never overwrites an existing,
  // already-stored clue (interactiveDefs still wins whenever it has one)
  // — this is only a convenient starting point to type a real definition
  // from, not a value that gets silently saved on its own: setting
  // .value here doesn't fire the field's own "input" listener, so it's
  // never mistaken for a real clue unless the player actually edits it.
  interactiveDefinitionInput.value = w
    ? interactiveDefs.get(interactiveKey(w)) || (w.filled ? w.answer : "")
    : "";
  interactiveDefinitionInput.disabled = !w;
  interactivePrevBtn.disabled = interactiveUndoStack.length <= 1;
  // The shared "Mots"/"Croisés" answer zone (#interactive-answers) is
  // deliberately the one exception to this function's own "clear every
  // result panel on every render" rule below, at the user's explicit
  // request: "'Mots' must show the word list without erasing the
  // previously displayed list, which stays visible underneath (lets you
  // compare several word lists)."
  // Each of its own stacked blocks stays bound to the exact slot/cell it
  // was fetched for regardless of the live selection (see
  // renderInteractiveWords()'s own docstring), so a later selection/grid
  // change can never make an already-shown block misleading or unsafe to
  // click — it's only ever reset wholesale by a genuinely new interactive
  // session (enterInteractiveMode()), never by an ordinary render here.
  //
  // The "Proposer" (définition) pick-list is a different case, still
  // cleared on every render — reported live: "if you change the
  // Horizontal/Vertical direction without re-clicking in the grid, the
  // definition-suggestion list keeps using the previously configured
  // direction." Root cause: setActiveDirection()
  // already calls renderInteractive() on every H/V toggle, which
  // correctly recomputes selectedInteractiveWord() for the NEW
  // direction — but, before this fix, never cleared the pick-list still
  // showing suggestions fetched for the OLD direction's word. Clicking
  // one of those stale entries then called interactiveDefs.set(
  // interactiveKey(w), def) with a freshly-recomputed `w` (the new
  // word), silently attaching the previous direction's suggestion text
  // to the new word's key — a genuine, reproducible bug, not merely a
  // stale-looking display. "Proposer un titre" was checked too, at the
  // same explicit request: proposeInteractiveTitle() never reads
  // selected/activeDirection at all (it lists every slot's own answer,
  // whole-grid, direction-independent), so its own pick-list can never
  // go stale from a direction change specifically — but it's just as
  // stale after any OTHER grid edit renderInteractive() already reacts
  // to (a typed letter, undo, clean, ...), so it's cleared here too for
  // the same reason, not because the reported symptom itself applies to
  // it.
  interactiveProposeResults.hidden = true;
  interactiveProposeResults.innerHTML = "";
  interactiveTitleProposeResults.hidden = true;
  interactiveTitleProposeResults.innerHTML = "";
  renderInteractiveVerifyReport();
  updateInteractiveFinishState();
}

// ---- Editing primitives (push undo, then re-render) ----
function interactivePushUndo() {
  interactiveUndoStack.push(interactiveGrid.map((row) => row.slice()));
  if (interactiveUndoStack.length > 500) interactiveUndoStack.shift();
  // Any grid mutation invalidates the last backend fill diagnostics; the
  // "Suivant" handler re-populates them from its own response afterwards.
  clearInteractiveDiagnostics();
}
// Advances the selection by exactly one cell in the current direction —
// unlike play mode's own moveSelection() (which skips over black cells,
// since those are truly fixed there), this never skips a black cell, at
// the user's explicit request: "When a user types letters in Edition
// mode, black cells that can be replaced must not be skipped (unlike in
// Play mode)." A black cell in
// Interactive mode is always directly editable (interactiveTypeLetter()
// itself never refuses to type over one — only the Space key's own
// toggle treats it specially), so skipping past it here would leave the
// player unable to simply keep typing straight through it.
function advanceInteractiveSelection() {
  if (!selected) return;
  const rows = interactiveGrid.length;
  const cols = rows ? interactiveGrid[0].length : 0;
  let { row, col } = selected;
  if (activeDirection === "across") col += 1;
  else row += 1;
  if (row >= rows || col >= cols) return;
  selected = { row, col };
}
function interactiveTypeLetter(letter) {
  if (!interactiveMode || !selected) return;
  interactivePushUndo();
  interactiveGrid[selected.row][selected.col] = letter.toUpperCase();
  advanceInteractiveSelection();
  setInteractiveMessage("");
  renderInteractive();
}
// A letter at (row, col) is about to be removed (erased, or blackened) —
// drop the saved definition of every word (across and/or down) crossing
// that cell, since it risks no longer matching the word's content.
function interactiveClearDefsAt(row, col) {
  for (const s of interactiveSlots()) {
    if (s.cells.some((p) => p.row === row && p.col === col)) {
      interactiveDefs.delete(interactiveKey(s));
    }
  }
}

function interactiveToggleBlack() {
  if (!interactiveMode || !selected) return;
  interactivePushUndo();
  const cur = interactiveGrid[selected.row][selected.col];
  if (cur !== "" && cur !== "#") interactiveClearDefsAt(selected.row, selected.col);
  interactiveGrid[selected.row][selected.col] = cur === "#" ? "" : "#";
  setInteractiveMessage("");
  renderInteractive();
}
function interactiveErase() {
  if (!interactiveMode || !selected) return;
  interactivePushUndo();
  const cur = interactiveGrid[selected.row][selected.col];
  if (cur !== "" && cur !== "#") interactiveClearDefsAt(selected.row, selected.col);
  interactiveGrid[selected.row][selected.col] = "";
  setInteractiveMessage("");
  renderInteractive();
}
function interactiveUndo() {
  if (interactiveUndoStack.length <= 1) return;
  interactiveUndoStack.pop();
  interactiveGrid = interactiveUndoStack[interactiveUndoStack.length - 1].map((r) => r.slice());
  clearInteractiveDiagnostics();
  setInteractiveMessage("");
  renderInteractive();
}
// Arrow keys move the selection one cell (black cells included — every
// cell is selectable in this mode) from the currently-clicked cell,
// clamped to the grid, at the user's explicit request.
function interactiveArrowMove(dr, dc) {
  if (!selected) return;
  const rows = interactiveGrid.length;
  const cols = rows ? interactiveGrid[0].length : 0;
  const row = Math.min(rows - 1, Math.max(0, selected.row + dr));
  const col = Math.min(cols - 1, Math.max(0, selected.col + dc));
  if (row === selected.row && col === selected.col) return;
  selected = { row, col };
  renderInteractive();
}

// "Suppr"/Delete with an active zone selection clears the WHOLE zone
// instead of just the single selected cell, at the user's explicit
// request: "When a zone is selected, the DELETE key removes the zone's
// letters while preserving black cells. If there is no letter in the
// zone, remove the zone's black cells." A
// black cell is never removed while at least one letter still exists
// anywhere in the zone (checked once, up front — a single pass, not
// per-cell) — only once the whole zone is entirely letter-free does the
// second pass instead clear every black cell of the zone.
function interactiveEraseZone() {
  if (!interactiveZoneSelection || !interactiveZoneSelection.size) return;
  const cells = [...interactiveZoneSelection].map((key) => key.split(",").map(Number));
  const hasLetter = cells.some(([r, c]) => /[A-Z]/.test(interactiveGrid[r][c]));
  interactivePushUndo();
  if (hasLetter) {
    for (const [r, c] of cells) {
      const ch = interactiveGrid[r][c];
      if (ch !== "#" && ch !== "") {
        interactiveClearDefsAt(r, c);
        interactiveGrid[r][c] = "";
      }
    }
  } else {
    for (const [r, c] of cells) {
      if (interactiveGrid[r][c] === "#") {
        interactiveGrid[r][c] = "";
      }
    }
  }
  setInteractiveMessage("");
  renderInteractive();
}

function handleInteractiveKeydown(event) {
  // Checked before the `!selected` guard below — the zone-wide erase
  // above works purely off interactiveZoneSelection and doesn't need any
  // cell to be individually selected for typing. "Backspace" deliberately
  // keeps its own, unchanged single-cell meaning (see the Backspace/
  // Delete branch further below) even while a zone is active — only
  // "Delete" itself switches to the zone-wide behavior.
  if (event.key === "Delete" && interactiveZoneSelection && interactiveZoneSelection.size) {
    event.preventDefault();
    interactiveEraseZone();
    return;
  }
  if (!selected) return;
  const key = event.key;
  if (key === "ArrowUp") {
    event.preventDefault();
    interactiveArrowMove(-1, 0);
  } else if (key === "ArrowDown") {
    event.preventDefault();
    interactiveArrowMove(1, 0);
  } else if (key === "ArrowLeft") {
    event.preventDefault();
    interactiveArrowMove(0, -1);
  } else if (key === "ArrowRight") {
    event.preventDefault();
    interactiveArrowMove(0, 1);
  } else if (key.length === 1 && /[a-zA-Z]/.test(key)) {
    event.preventDefault();
    interactiveTypeLetter(key);
  } else if (key === " ") {
    event.preventDefault();
    interactiveToggleBlack();
  } else if (key === "Backspace" || key === "Delete") {
    event.preventDefault();
    interactiveErase();
  }
}

// ---- Definition / title proposals (reuse GET /api/dictionary/define,
// POST /api/interactive/title) ----

// Renders `list` as a stack of clickable lines inside `container` —
// shared by renderInteractiveProposals() (definitions) and
// renderInteractiveTitleProposals() (titles) below, since the two are
// otherwise identical: one <p class="interactive-propose-line"> per
// item, clicking (or Enter/Space) calls `onPick(item)` and nothing else
// — each caller's own `onPick` decides what "picking" one actually
// means (fill a field, remember it against a slot, hide the list, ...).
// Empty/missing `list` just hides `container`, matching both callers'
// pre-existing "no proposals" behavior.
function renderInteractivePickList(container, list, onPick) {
  container.innerHTML = "";
  if (!list || !list.length) {
    container.hidden = true;
    return;
  }
  for (const item of list) {
    const line = document.createElement("p");
    line.className = "interactive-propose-line";
    line.tabIndex = 0;
    line.textContent = item;
    const pick = () => onPick(item);
    line.addEventListener("click", pick);
    line.addEventListener("keydown", (e) => {
      if (e.key === "Enter" || e.key === " ") {
        e.preventDefault();
        pick();
      }
    });
    container.appendChild(line);
  }
  container.hidden = false;
}

function renderInteractiveProposals(list) {
  renderInteractivePickList(interactiveProposeResults, list, (def) => {
    interactiveDefinitionInput.value = def;
    const w = selectedInteractiveWord();
    if (w) interactiveDefs.set(interactiveKey(w), def);
    interactiveProposeResults.hidden = true;
    interactiveProposeResults.innerHTML = "";
    updateInteractiveFinishState();
  });
}

// "Proposer un titre" button's own 10-proposal list, at the user's
// explicit request ("10 proposals shown below, like 'Proposer une
// définition'") — see proposeInteractiveTitle() below for
// how the list itself is fetched.
function renderInteractiveTitleProposals(list) {
  renderInteractivePickList(interactiveTitleProposeResults, list, (title) => {
    interactiveTitleInput.value = title;
    interactiveTitleProposeResults.hidden = true;
    interactiveTitleProposeResults.innerHTML = "";
  });
}

// Converts one "Mots Défi" entry — kept everywhere else exactly as typed,
// accents/case and all (interactiveChallengeWords itself, the list shown
// in #interactive-challenge-list, GRID_WORK persistence) — to the grid's
// own bare-uppercase, accent-stripped form: the only form ever compared
// against interactiveGrid cells or written into them, since a grid cell
// never holds anything else (same MOT-column convention as the wordlist
// itself, see data_builder/build_wordlist_freq.py's own strip_accents).
// At the user's explicit request, after a live report: the previous
// version normalized the TYPED value itself this way before ever storing
// it, which silently dropped accented letters entirely (`[^A-Z]` strips
// "É", it doesn't fold it to "E") instead of just stripping their accent
// — "randonnées" became "RANDONNES", not "RANDONNEES". Normalizing to NFD
// (accents become separate combining marks) before stripping non-A-Z
// keeps the base letter and only discards the mark itself. A ligature
// letter (French "œ"/"Œ"/"æ"/"Æ") is folded into its two separate letters
// first, since NFD never decomposes it (it's an atomic code point, not an
// accent-style combining-mark sequence) — without this, the final
// [^A-Z] strip would just delete it outright: "sœur" became "SUR", not
// "SOEUR".
const LIGATURE_GRID_FOLD = { "œ": "oe", "Œ": "OE", "æ": "ae", "Æ": "AE" };
function challengeWordGridForm(word) {
  return word
    .replace(/[œŒæÆ]/g, (ch) => LIGATURE_GRID_FOLD[ch])
    .normalize("NFD")
    .replace(/[̀-ͯ]/g, "")
    .toUpperCase()
    .replace(/[^A-Z]/g, "");
}

// Whether `word` (already in grid form — see challengeWordGridForm()
// above) is actually present in the grid right now — an exact match
// against some full across or down run (bounded by black cells or the
// grid's own edge), not merely a substring — at the user's explicit
// request: "when a word from the 'Mots Défi' list is actually present in
// the grid, it shows in green in that list, otherwise in black." Reuses
// interactiveRunAt() (defined further below — hoisted, so callable here)
// for the run itself.
function gridContainsWord(word) {
  const rows = interactiveGrid.length;
  const cols = rows ? interactiveGrid[0].length : 0;
  const matches = (cells) => cells.length === word.length
    && cells.every(({ row, col }, i) => interactiveGrid[row][col] === word[i]);
  for (let r = 0; r < rows; r++) {
    for (let c = 0; c < cols; c++) {
      if (interactiveGrid[r][c] === "#") continue;
      if ((c === 0 || interactiveGrid[r][c - 1] === "#") && matches(interactiveRunAt(r, c, "across"))) {
        return true;
      }
      if ((r === 0 || interactiveGrid[r - 1][c] === "#") && matches(interactiveRunAt(r, c, "down"))) {
        return true;
      }
    }
  }
  return false;
}

// Clicking a "Mots Défi" word inserts it into the grid starting at the
// currently selected cell, along the current fill direction, overwriting
// any letter or black cell already there — at the user's explicit
// request: "an authoritative choice." Unlike every other word-list click
// in this panel (Mots/Croisés/Début/Fin), this never checks the target
// cells against a real slot/dictionary fit first — same raw-write
// behavior as typing letter-by-letter (interactiveTypeLetter()), just for
// a whole word at once starting from the selection instead of one cell.
// `word` must already be in grid form (challengeWordGridForm()) — every
// caller below converts its own interactiveChallengeWords entry first.
function insertInteractiveChallengeWord(word) {
  if (!interactiveMode || !selected) {
    setInteractiveMessage(I18N[uiLanguage].interactiveCrossingNeedsCell, true);
    return;
  }
  const rows = interactiveGrid.length;
  const cols = rows ? interactiveGrid[0].length : 0;
  interactivePushUndo();
  let row = selected.row;
  let col = selected.col;
  for (let i = 0; i < word.length && row < rows && col < cols; i++) {
    const cur = interactiveGrid[row][col];
    if (cur !== "" && cur !== "#") interactiveClearDefsAt(row, col);
    interactiveGrid[row][col] = word[i];
    if (activeDirection === "across") col++; else row++;
  }
  setInteractiveMessage("");
  renderInteractive();
}

// ---- "Mots Défi" panel — see #interactive-challenge-panel in index.html.
// Renders the current interactiveChallengeWords list as a plain add/
// remove list (mirrors the "Créations" drafts table's own per-row 🗑
// delete button, see renderInteractiveWorkList() further below). Each
// word is also clickable, like every other word list in this panel (see
// insertInteractiveChallengeWord() above), and colored green/black
// depending on whether it's actually present in the grid right now (see
// gridContainsWord() above) — refreshed on every renderInteractive() call
// so it always reflects the live grid, not just its own state at the
// moment the word was added. The label shown is the word exactly as
// typed (accents/case kept, "comme dans les dictionnaires" — at the
// user's explicit request); only the grid-matching/insertion below ever
// uses its derived bare-uppercase grid form (challengeWordGridForm()).
function renderInteractiveChallengeList() {
  const t = I18N[uiLanguage];
  interactiveChallengeList.innerHTML = "";
  for (const word of interactiveChallengeWords) {
    const gridForm = challengeWordGridForm(word);
    const li = document.createElement("li");
    const span = document.createElement("span");
    span.textContent = word;
    span.tabIndex = 0;
    span.className = gridForm && gridContainsWord(gridForm)
      ? "interactive-word-item interactive-word-challenge"
      : "interactive-word-item";
    const pick = () => insertInteractiveChallengeWord(gridForm);
    span.addEventListener("click", pick);
    span.addEventListener("keydown", (e) => {
      if (e.key === "Enter" || e.key === " ") {
        e.preventDefault();
        pick();
      }
    });
    li.appendChild(span);
    const removeBtn = document.createElement("button");
    removeBtn.type = "button";
    removeBtn.className = "nav-btn clear-icon-btn";
    removeBtn.textContent = "−";
    removeBtn.title = t.interactiveChallengeRemoveBtn;
    removeBtn.setAttribute("aria-label", `${t.interactiveChallengeRemoveBtn} ${word}`);
    removeBtn.addEventListener("click", () => {
      interactiveChallengeWords = interactiveChallengeWords.filter((w) => w !== word);
      renderInteractiveChallengeList();
    });
    li.appendChild(removeBtn);
    interactiveChallengeList.appendChild(li);
  }
}

function addInteractiveChallengeWord() {
  // Stored exactly as typed — accents, case, everything — "comme dans les
  // dictionnaires," at the user's explicit request, after a live report:
  // the previous version upper-cased and stripped straight to bare A-Z
  // right here, so "randonnées" was saved as "RANDONNES" (the "é" simply
  // vanished — [^A-Z] deletes an accented letter outright rather than
  // folding it to its base letter). Only trimmed of surrounding
  // whitespace; every place that actually needs to compare against or
  // write into interactiveGrid derives the bare-uppercase grid form on
  // its own, on demand (challengeWordGridForm()) — this stored value
  // never is that form itself. Duplicates are still rejected, but by
  // their GRID form (case/accent-insensitive) rather than by exact typed
  // text, so "Randonnées" typed twice (or once accented, once not) still
  // only ever adds one entry.
  const word = interactiveChallengeInput.value.trim();
  interactiveChallengeInput.value = "";
  if (!word) return;
  const gridForm = challengeWordGridForm(word);
  if (!gridForm) return;
  if (interactiveChallengeWords.some((w) => challengeWordGridForm(w) === gridForm)) return;
  interactiveChallengeWords.push(word);
  renderInteractiveChallengeList();
}

interactiveChallengeAddBtn.addEventListener("click", addInteractiveChallengeWord);
interactiveChallengeInput.addEventListener("keydown", (e) => {
  if (e.key === "Enter") {
    e.preventDefault();
    addInteractiveChallengeWord();
  }
});
// Automatic word validation on punctuation — see attachPunctuationAutoAdd()
// further down (hoisted, safe to call from here) for the shared mechanic.
attachPunctuationAutoAdd(interactiveChallengeInput, addInteractiveChallengeWord);

// ---- "Mots Défi (personnalisation)" mini-form on the main generation
// form — see #generate-challenge-panel in index.html. A much simpler
// cousin of renderInteractiveChallengeList()/addInteractiveChallengeWord()
// above: no grid exists yet before generation, so no green/black
// coloring and no click-to-insert — just a plain add/remove list, sent
// as-is (`generate_challenge_words`, see the /api/generate submit
// handler further down) to backend/app.py's GenerateRequest.challenge_
// words. #generate-challenge-list itself is only shown once non-empty;
// unlike #interactive-challenge-list (one word per row), its own CSS lays
// every <li> out in a wrapping horizontal line (see style.css) — each
// word's own remove button already separates it from the next, so no
// comma is added — to stay compact rather than growing tall.
function renderGenerateChallengeList() {
  const t = I18N[uiLanguage];
  generateChallengeList.innerHTML = "";
  generateChallengeList.hidden = generateChallengeWords.length === 0;
  for (const word of generateChallengeWords) {
    const li = document.createElement("li");
    const span = document.createElement("span");
    span.textContent = word;
    // Not `.interactive-word-item` (that class's cursor/hover styling
    // signals a clickable, insert-into-grid word — meaningless here,
    // there is no grid yet before generation).
    span.className = "generate-challenge-word";
    li.appendChild(span);
    const removeBtn = document.createElement("button");
    removeBtn.type = "button";
    removeBtn.className = "nav-btn clear-icon-btn";
    removeBtn.textContent = "−";
    removeBtn.title = t.interactiveChallengeRemoveBtn;
    removeBtn.setAttribute("aria-label", `${t.interactiveChallengeRemoveBtn} ${word}`);
    removeBtn.addEventListener("click", () => {
      generateChallengeWords = generateChallengeWords.filter((w) => w !== word);
      renderGenerateChallengeList();
    });
    li.appendChild(removeBtn);
    generateChallengeList.appendChild(li);
  }
}

function addGenerateChallengeWord() {
  // Same "kept exactly as typed" convention as addInteractiveChallengeWord()
  // above — see its own comment.
  const word = generateChallengeInput.value.trim();
  generateChallengeInput.value = "";
  if (!word) return;
  const gridForm = challengeWordGridForm(word);
  if (!gridForm) return;
  if (generateChallengeWords.some((w) => challengeWordGridForm(w) === gridForm)) return;
  generateChallengeWords.push(word);
  renderGenerateChallengeList();
}

generateChallengeAddBtn.addEventListener("click", addGenerateChallengeWord);
generateChallengeInput.addEventListener("keydown", (e) => {
  if (e.key === "Enter") {
    e.preventDefault();
    addGenerateChallengeWord();
  }
});

// Automatic word validation on punctuation, at the user's explicit
// request: typing a space/comma/period/etc at the end of a "Mots Défi" or
// "Thématique" input field validates whatever word precedes it — exactly
// as if "+" had been clicked — and resets the field, so a user can type a
// whole list fluently ("chat, chien, oiseau ") without ever touching the
// "+" button. Attached to every one of this trio's input fields right
// below their own "+"/Enter handlers.
const CHALLENGE_WORD_AUTOADD_PUNCTUATION_RE = /[\s,;:.!?]$/;
function attachPunctuationAutoAdd(input, addFn) {
  input.addEventListener("input", () => {
    if (CHALLENGE_WORD_AUTOADD_PUNCTUATION_RE.test(input.value)) {
      input.value = input.value.replace(/[\s,;:.!?]+$/, "");
      addFn();
    }
  });
}
attachPunctuationAutoAdd(generateChallengeInput, addGenerateChallengeWord);

// ---- "Thématique" chip list — see #theme-field in index.html. Same
// add/remove mechanic as "Mots Défi (personnalisation)" just above
// (renderGenerateChallengeList()/addGenerateChallengeWord()), but with no
// grid-form dedup (a theme word isn't a grid answer — dictionary
// accents/case matter for the Qdrant pre-search) and a plain case-
// insensitive duplicate check instead.
function renderThemeList() {
  const t = I18N[uiLanguage];
  themeList.innerHTML = "";
  themeList.hidden = themeKeywords.length === 0;
  for (const word of themeKeywords) {
    const li = document.createElement("li");
    const span = document.createElement("span");
    span.textContent = word;
    li.appendChild(span);
    const removeBtn = document.createElement("button");
    removeBtn.type = "button";
    removeBtn.className = "nav-btn clear-icon-btn";
    removeBtn.textContent = "−";
    removeBtn.title = t.themeRemoveBtn;
    removeBtn.setAttribute("aria-label", `${t.themeRemoveBtn} ${word}`);
    removeBtn.addEventListener("click", () => {
      themeKeywords = themeKeywords.filter((w) => w !== word);
      renderThemeList();
    });
    li.appendChild(removeBtn);
    themeList.appendChild(li);
  }
}

function addThemeWord() {
  const word = themeInput.value.trim();
  themeInput.value = "";
  if (!word) return;
  if (themeKeywords.some((w) => w.toLowerCase() === word.toLowerCase())) return;
  themeKeywords.push(word);
  renderThemeList();
}

// Re-fills the whole chip list at once from a plain theme string (a
// re-edited grid's own origin theme — see enterInteractiveMode() below):
// split on whitespace, same granularity backend/app.py's own tokenizer
// (`_theme_tokens`) already uses, so re-splitting a previously-joined
// list round-trips losslessly.
function setThemeWords(str) {
  themeKeywords = (str || "").split(/\s+/).filter(Boolean);
  themeInput.value = "";
  renderThemeList();
}

themeAddBtn.addEventListener("click", addThemeWord);
themeInput.addEventListener("keydown", (e) => {
  if (e.key === "Enter") {
    e.preventDefault();
    addThemeWord();
  }
});
attachPunctuationAutoAdd(themeInput, addThemeWord);

// Every distinct grid form (challengeWordGridForm()) of the current "Mots
// Défi" list, deduplicated — the ONLY thing challengeWordsForCells()/
// challengeWordsForBoundary() below ever test against the grid or return:
// their results get mixed directly into the same candidate lists as real
// dictionary words (renderInteractiveWords()/renderInteractiveBoundary())
// and can be clicked to write straight into interactiveGrid, so they must
// already be in the grid's own bare-uppercase form, never the accented
// text actually typed (that form is only ever shown in this panel's own
// #interactive-challenge-list — see renderInteractiveChallengeList()).
function interactiveChallengeGridForms() {
  return [...new Set(interactiveChallengeWords.map(challengeWordGridForm))].filter(Boolean);
}

// Tests every "Mots Défi" word against one exact-length run of cells
// (used by "Mots", and by "Croisés" for each of its own across/down
// runs) — a match requires the same length plus agreement with every
// letter already on the grid there. `overrideIndex`/`overrideLetter`
// force one position to a specific letter regardless of the grid's own
// (possibly still empty) content there — "Croisés" uses this to test
// against a hypothetical intersection letter it hasn't been placed yet.
function challengeWordsForCells(cells, overrideIndex, overrideLetter) {
  if (!interactiveChallengeWords.length) return [];
  const pattern = cells.map(({ row, col }, i) => {
    if (i === overrideIndex) return overrideLetter;
    const ch = interactiveGrid[row][col];
    return ch && ch !== "#" ? ch : null;
  });
  return interactiveChallengeGridForms().filter((word) => {
    if (word.length !== cells.length) return false;
    for (let i = 0; i < cells.length; i++) {
      if (pattern[i] && word[i] !== pattern[i]) return false;
    }
    return true;
  });
}

// Same idea for "Début"/"Fin", whose candidates may be shorter than the
// slot itself — mirrors renderInteractiveBoundary()'s own placeWord()
// rule: a shorter word only fits if the single cell right beyond it is
// still empty (free to turn black).
function challengeWordsForBoundary(cells, side) {
  if (!interactiveChallengeWords.length) return [];
  const maxLen = cells.length;
  return interactiveChallengeGridForms().filter((word) => {
    if (!word.length || word.length > maxLen) return false;
    const wordCells = side === "start" ? cells.slice(0, word.length) : cells.slice(maxLen - word.length);
    for (let i = 0; i < wordCells.length; i++) {
      const { row, col } = wordCells[i];
      const ch = interactiveGrid[row][col];
      if (ch && ch !== "#" && word[i] !== ch) return false;
    }
    if (word.length < maxLen) {
      const boundary = side === "start" ? cells[word.length] : cells[maxLen - word.length - 1];
      if (interactiveGrid[boundary.row][boundary.col]) return false;
    }
    return true;
  });
}

// Builds one candidate word's letters inside `item`, one <span> per
// letter — shared by the "Mots"/"Croisés"/"Début"/"Fin" panels so both
// the selected-cell highlight (blue, `highlightPos`, -1 for none) and the
// "would create an impossible crossing slot" warning (red underline,
// `unsafeSet`, 0-indexed positions from the backend's own `unsafe` field)
// can be marked independently — and together, when they land on the same
// letter. A letter needing neither stays a plain text node, like before
// this warning existed.
function appendWordLetters(item, word, highlightPos, unsafeSet) {
  for (let pos = 0; pos < word.length; pos++) {
    const classes = [];
    if (pos === highlightPos) classes.push("interactive-word-highlight-letter");
    if (unsafeSet && unsafeSet.has(pos)) classes.push("interactive-word-unsafe-letter");
    if (classes.length) {
      const mark = document.createElement("span");
      mark.className = classes.join(" ");
      mark.textContent = word[pos];
      item.appendChild(mark);
    } else {
      item.appendChild(document.createTextNode(word[pos]));
    }
  }
}

// "Eye" toggle button added to the header of every "Mots"/"Croisés"/
// "Début"/"Fin" results block, at the user's explicit request: "ajouter
// un bouton icône 'voir' (oeil) en haut à droite. Quand on clique sur ce
// bouton, masquer les mots contenant des lettres en rouge... et changer
// le bouton icône avec un barré. Un deuxième clique remontre les mots...
// et restaure le bouton voir non barré." Operates purely by class on
// `block`'s own already-rendered `.interactive-word-item-unsafe` words
// (set by the three render functions below whenever a candidate's own
// `unsafe` list is non-empty) — no re-fetch, no re-render, so it never
// disturbs the block's own click-to-place handlers or any other stacked
// block. The two SVGs (open eye / crossed-out eye) are both always
// present, toggled via their own `hidden` attribute — see
// .interactive-eye-icon in style.css.
function createInteractiveWordsEyeToggle(block) {
  const t = I18N[uiLanguage];
  const btn = document.createElement("button");
  btn.type = "button";
  btn.className = "nav-btn clear-icon-btn interactive-words-eye-btn";
  btn.setAttribute("aria-pressed", "false");
  btn.setAttribute("aria-label", t.interactiveHideUnsafeBtn);
  btn.title = t.interactiveHideUnsafeBtn;
  // Both icons always exist; which one is visible is driven purely by
  // the button's own `aria-pressed` state via CSS (see
  // .interactive-words-eye-btn[aria-pressed="true"] in style.css) —
  // not by an SVG `hidden` attribute/property, which some browsers
  // don't reflect live on SVG elements the way they do on HTML ones.
  btn.innerHTML =
    '<svg class="interactive-eye-icon interactive-eye-icon-open" viewBox="0 0 24 24" ' +
    'width="16" height="16" aria-hidden="true" focusable="false">' +
    '<path d="M1.5 12S5.5 5 12 5s10.5 7 10.5 7-4 7-10.5 7-10.5-7-10.5-7z" ' +
    'fill="none" stroke="currentColor" stroke-width="1.6" stroke-linejoin="round"/>' +
    '<circle cx="12" cy="12" r="2.6" fill="none" stroke="currentColor" stroke-width="1.6"/>' +
    "</svg>" +
    '<svg class="interactive-eye-icon interactive-eye-icon-closed" viewBox="0 0 24 24" ' +
    'width="16" height="16" aria-hidden="true" focusable="false">' +
    '<path d="M1.5 12S5.5 5 12 5s10.5 7 10.5 7-4 7-10.5 7-10.5-7-10.5-7z" ' +
    'fill="none" stroke="currentColor" stroke-width="1.6" stroke-linejoin="round"/>' +
    '<circle cx="12" cy="12" r="2.6" fill="none" stroke="currentColor" stroke-width="1.6"/>' +
    '<line x1="2.5" y1="21.5" x2="21.5" y2="2.5" stroke="currentColor" stroke-width="1.6" stroke-linecap="round"/>' +
    "</svg>";
  btn.addEventListener("click", () => {
    const hiding = btn.getAttribute("aria-pressed") !== "true";
    btn.setAttribute("aria-pressed", hiding ? "true" : "false");
    block.querySelectorAll(".interactive-word-item-unsafe").forEach((item) => {
      item.classList.toggle("interactive-word-item-hidden", hiding);
    });
  });
  return btn;
}

// ---- Candidate words for the selected slot (POST /api/interactive/
// candidates) — "Mots" button, at the user's explicit request: "add a
// Mots button that lists the possible words for the selected slot...
// First, the theme glossary's own words... in magenta, then the other
// words in black. When the user clicks a word, it places it into the
// selected slot."
//
// Each call PREPENDS its own block instead of replacing the previous
// one, at the user's explicit follow-up request: "'Mots' must show the
// word list without erasing the previously displayed list, which stays
// visible underneath (lets you compare several word lists)" — so
// selecting a different emplacement and clicking "Mots" again stacks a
// new block on top of, not instead of, the earlier one(s); see
// renderInteractive()'s own comment for why this panel is the one
// exception to its usual "clear every result panel on every render"
// staleness rule.
//
// `slot`/`atCell` are the emplacement/cell this SPECIFIC call was fetched
// for — the click handler's own frozen snapshot, taken before the async
// round trip — captured into this block's own click handlers and
// highlight computation rather than re-read from the live selection: a
// later block can sit on screen long after the player has moved on to a
// different emplacement, so re-deriving "the selected word" at click time
// would silently place a stale block's word onto the WRONG, now-current
// slot — the exact staleness bug class already found and fixed once for
// the "Proposer" pick-list, avoided here up front instead of retrofitted.
function renderInteractiveWords(themeWords, otherWords, slot, atCell) {
  // "Mots Défi" matches shown first, in green, ahead of the theme
  // glossary's own magenta words — at the user's explicit request. Any
  // dictionary word already covered by a challenge-word match is dropped
  // from its own bucket below to avoid listing it twice.
  const challengeWords = challengeWordsForCells(slot.cells);
  const challengeSet = new Set(challengeWords);
  const all = [
    ...challengeWords.map((word) => ({ word, cls: "interactive-word-challenge", unsafe: [] })),
    ...(themeWords || []).filter((entry) => !challengeSet.has(entry.word))
      .map((entry) => ({ word: entry.word, cls: "interactive-word-theme", unsafe: entry.unsafe })),
    ...(otherWords || []).filter((entry) => !challengeSet.has(entry.word))
      .map((entry) => ({ word: entry.word, cls: "", unsafe: entry.unsafe })),
  ];
  if (!all.length) return false;
  // Which position in `slot.cells` is the cell that was selected (shown
  // in blue on the grid) at the moment "Mots" was clicked — highlighted
  // in blue within every candidate below, at the user's explicit request
  // ("lets you locate the letter within the crossing combinations"). -1
  // (no highlight) only if that cell can't be found — shouldn't normally
  // happen, since selectedInteractiveWord() always builds `slot.cells` by
  // walking outward from the selected cell itself.
  const highlightIndex = atCell
    ? slot.cells.findIndex(({ row, col }) => row === atCell.row && col === atCell.col)
    : -1;
  const placeWord = (word) => {
    if (word.length !== slot.cells.length) return;
    interactivePushUndo();
    for (let i = 0; i < slot.cells.length; i++) {
      const { row, col } = slot.cells[i];
      interactiveGrid[row][col] = word[i];
    }
    setInteractiveMessage("");
    renderInteractive();
  };
  const block = document.createElement("div");
  block.className = "interactive-words-block";
  const header = document.createElement("div");
  header.className = "interactive-words-block-header";
  const label = document.createElement("p");
  label.className = "interactive-words-block-label";
  const dirPrefix = slot.direction === "across" ? "H" : "V";
  label.textContent = `${dirPrefix} (${slot.startRow + 1}, ${slot.startCol + 1})`;
  header.appendChild(label);
  header.appendChild(createInteractiveWordsEyeToggle(block));
  block.appendChild(header);
  all.forEach(({ word, cls, unsafe }) => {
    const item = document.createElement("span");
    item.className = cls ? `interactive-word-item ${cls}` : "interactive-word-item";
    item.tabIndex = 0;
    const unsafeSet = new Set(unsafe);
    if (unsafeSet.size) item.classList.add("interactive-word-item-unsafe");
    appendWordLetters(item, word, highlightIndex, unsafeSet);
    const pick = () => placeWord(word);
    item.addEventListener("click", pick);
    item.addEventListener("keydown", (e) => {
      if (e.key === "Enter" || e.key === " ") {
        e.preventDefault();
        pick();
      }
    });
    block.appendChild(item);
  });
  interactiveAnswers.insertBefore(block, interactiveAnswers.firstChild);
  interactiveAnswers.hidden = false;
  return true;
}

interactiveWordsBtn.addEventListener("click", async () => {
  const t = I18N[uiLanguage];
  const w = selectedInteractiveWord();
  if (!w) {
    setInteractiveMessage(t.interactiveWordsNeedsSlot, true);
    return;
  }
  // Frozen now, not re-read after the await — see renderInteractiveWords()'s
  // own docstring for why.
  const atCell = selected ? { row: selected.row, col: selected.col } : null;
  interactiveWordsBtn.disabled = true;
  setInteractiveMessage("");
  try {
    const wireGrid = interactiveGrid.map((row) => row.map((ch) => (ch === "" ? "." : ch)));
    const resp = await fetchWithTimeout("/api/interactive/candidates", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        job_id: interactiveJobId,
        grid: wireGrid,
        cells: w.cells.map((c) => [c.row, c.col]),
        challenge_words: interactiveChallengeWords,
      }),
    }, FETCH_TIMEOUT_MS);
    if (resp.status === 404) {
      setInteractiveMessage(t.interactiveSessionLost, true);
      return;
    }
    if (!resp.ok) throw new Error(t.interactiveWordsError);
    const data = await resp.json();
    const themeWords = (data && data.theme_words) || [];
    const otherWords = (data && data.other_words) || [];
    const rendered = renderInteractiveWords(themeWords, otherWords, w, atCell);
    if (!rendered) {
      setInteractiveMessage(t.interactiveWordsEmpty, true);
    }
  } catch (err) {
    setInteractiveMessage(t.interactiveWordsError, true);
  } finally {
    interactiveWordsBtn.disabled = false;
  }
});

// The maximal white run through (row, col) along `direction` in the
// CURRENT interactiveGrid — same scan as selectedInteractiveWord()'s own
// inner logic, but reusable for an arbitrary starting cell (not just the
// live `selected` one) — needed by "Croisés", whose own two word lists
// each start from a cell the backend already resolved (across_start/
// down_start), not necessarily the one currently selected for typing.
function interactiveRunAt(row, col, direction) {
  const rows = interactiveGrid.length;
  const cols = rows ? interactiveGrid[0].length : 0;
  const isW = (r, c) => r >= 0 && r < rows && c >= 0 && c < cols && interactiveGrid[r][c] !== "#";
  const cells = [];
  if (direction === "across") {
    let c = col;
    while (isW(row, c - 1)) c--;
    for (; isW(row, c); c++) cells.push({ row, col: c });
  } else {
    let r = row;
    while (isW(r - 1, col)) r--;
    for (; isW(r, col); r++) cells.push({ row: r, col });
  }
  return cells;
}

// "Croisés" button's own results block — one per click, stacked on top of
// the previous one (never cleared by an ordinary render), same
// convention as "Mots" (renderInteractiveWords) and for the same reason:
// each block stays bound to the exact cell it was fetched for, regardless
// of the live selection, so it's safe to keep several around for
// comparison. For each letter compatible with a real word in BOTH
// directions, lists its own horizontal and vertical candidates
// (comma-separated) with that one letter highlighted in blue within every
// word — at the user's explicit request: "highlight the shared letter in
// blue (like the selected letter in the Mots function)". Every
// listed word is clickable to place it on its own emplacement, same
// interaction as "Mots".
function renderInteractiveCrossing(acrossStart, downStart, letters, atCell) {
  if (!letters || !letters.length) return;
  const t = I18N[uiLanguage];
  const acrossCells = interactiveRunAt(acrossStart[0], acrossStart[1], "across");
  const downCells = interactiveRunAt(downStart[0], downStart[1], "down");
  const posAcross = acrossCells.findIndex((p) => p.row === atCell.row && p.col === atCell.col);
  const posDown = downCells.findIndex((p) => p.row === atCell.row && p.col === atCell.col);
  const placeWord = (cells, word) => {
    if (word.length !== cells.length) return;
    interactivePushUndo();
    for (let i = 0; i < cells.length; i++) {
      const { row, col } = cells[i];
      interactiveGrid[row][col] = word[i];
    }
    setInteractiveMessage("");
    renderInteractive();
  };
  const appendWordList = (parent, words, cells, highlightPos, challengeWords) => {
    // "Mots Défi" matches shown first, in green — at the user's explicit
    // request. Any dictionary word already covered by a challenge-word
    // match is dropped from `words` to avoid listing it twice. `words`
    // itself is the backend's own list of `{word, unsafe}` dicts.
    const challengeSet = new Set(challengeWords);
    const ordered = [
      ...challengeWords.map((word) => ({ word, unsafe: [], challenge: true })),
      ...words.filter((entry) => !challengeSet.has(entry.word)),
    ];
    ordered.forEach(({ word, unsafe, challenge }) => {
      const item = document.createElement("span");
      item.className = challenge
        ? "interactive-word-item interactive-word-challenge"
        : "interactive-word-item";
      item.tabIndex = 0;
      const unsafeSet = new Set(unsafe);
      if (unsafeSet.size) item.classList.add("interactive-word-item-unsafe");
      appendWordLetters(item, word, highlightPos, unsafeSet);
      const pick = () => placeWord(cells, word);
      item.addEventListener("click", pick);
      item.addEventListener("keydown", (e) => {
        if (e.key === "Enter" || e.key === " ") {
          e.preventDefault();
          pick();
        }
      });
      parent.appendChild(item);
    });
  };
  const block = document.createElement("div");
  block.className = "interactive-words-block";
  const header = document.createElement("div");
  header.className = "interactive-words-block-header";
  const label = document.createElement("p");
  label.className = "interactive-words-block-label";
  label.textContent = t.interactiveCrossingLabel(atCell.row + 1, atCell.col + 1);
  header.appendChild(label);
  header.appendChild(createInteractiveWordsEyeToggle(block));
  block.appendChild(header);
  for (const { letter, across_words: acrossWords, down_words: downWords } of letters) {
    const group = document.createElement("div");
    group.className = "interactive-crossing-group";
    // The reference letter on its own line, at the user's explicit
    // request: "put the reference letter on one line, then H and V on 2
    // lines underneath" — previously prefixed onto the H line
    // itself ("A : H mot1, mot2..."), now its own row above both.
    const letterLine = document.createElement("p");
    letterLine.className = "interactive-crossing-line interactive-crossing-letter-line";
    const letterMark = document.createElement("span");
    letterMark.className = "interactive-word-highlight-letter";
    letterMark.textContent = letter;
    letterLine.appendChild(letterMark);
    group.appendChild(letterLine);
    const acrossLine = document.createElement("p");
    acrossLine.className = "interactive-crossing-line";
    const acrossLabel = document.createElement("span");
    acrossLabel.className = "interactive-crossing-dir-label";
    acrossLabel.textContent = t.interactiveCrossingAcrossLabel;
    acrossLine.appendChild(acrossLabel);
    acrossLine.appendChild(document.createTextNode(" "));
    appendWordList(acrossLine, acrossWords, acrossCells, posAcross,
      challengeWordsForCells(acrossCells, posAcross, letter));
    group.appendChild(acrossLine);
    const downLine = document.createElement("p");
    downLine.className = "interactive-crossing-line interactive-crossing-line-down";
    const downLabel = document.createElement("span");
    downLabel.className = "interactive-crossing-dir-label";
    downLabel.textContent = t.interactiveCrossingDownLabel;
    downLine.appendChild(downLabel);
    downLine.appendChild(document.createTextNode(" "));
    appendWordList(downLine, downWords, downCells, posDown,
      challengeWordsForCells(downCells, posDown, letter));
    group.appendChild(downLine);
    block.appendChild(group);
  }
  interactiveAnswers.insertBefore(block, interactiveAnswers.firstChild);
  interactiveAnswers.hidden = false;
}

interactiveCrossingBtn.addEventListener("click", async () => {
  const t = I18N[uiLanguage];
  if (!interactiveMode || !selected) {
    setInteractiveMessage(t.interactiveCrossingNeedsCell, true);
    return;
  }
  const atCell = { row: selected.row, col: selected.col };
  interactiveCrossingBtn.disabled = true;
  setInteractiveMessage("");
  try {
    const wireGrid = interactiveGrid.map((row) => row.map((ch) => (ch === "" ? "." : ch)));
    const resp = await fetchWithTimeout("/api/interactive/crossing", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        job_id: interactiveJobId,
        grid: wireGrid,
        cell: [atCell.row, atCell.col],
        challenge_words: interactiveChallengeWords,
      }),
    }, FETCH_TIMEOUT_MS);
    if (resp.status === 404) {
      setInteractiveMessage(t.interactiveSessionLost, true);
      return;
    }
    if (!resp.ok) throw new Error(t.interactiveCrossingError);
    const data = await resp.json();
    if (!data || !data.across_start || !data.down_start) {
      setInteractiveMessage(t.interactiveCrossingNoCrossing, true);
      return;
    }
    const letters = data.letters || [];
    renderInteractiveCrossing(data.across_start, data.down_start, letters, atCell);
    if (!letters.length) {
      setInteractiveMessage(t.interactiveCrossingEmpty, true);
    }
  } catch (err) {
    setInteractiveMessage(t.interactiveCrossingError, true);
  } finally {
    interactiveCrossingBtn.disabled = false;
  }
});

// "Début"/"Fin" buttons' own results block — to the right of "Croisés", at
// the user's explicit request: "add a Début button listing the words that
// can start the selected slot, taking the letters already placed into
// account, even if it doesn't reach the slot's full length... add a Fin
// button doing the same for words that can end the slot. Sort the
// proposed words by length, then alphabetically." Same stacking
// convention as renderInteractiveWords()/renderInteractiveCrossing()
// (each block stays bound to the exact slot/side it was fetched for).
// `side` is "start" (Début) or "end" (Fin) — a candidate shorter than the
// slot occupies the first/last `word.length` cells of `slot.cells`
// respectively; the backend (`interactive_boundary_candidates`) already
// guarantees the single boundary cell beyond it is free to turn black
// (never overwriting a letter, always structurally valid), so placing it
// here is a plain, unconditional grid mutation — no extra check needed.
function renderInteractiveBoundary(themeWords, otherWords, slot, side, label) {
  // "Mots Défi" matches shown first, in green — at the user's explicit
  // request. Any dictionary word already covered by a challenge-word
  // match is dropped from its own bucket below to avoid listing it twice.
  const challengeWords = challengeWordsForBoundary(slot.cells, side);
  const challengeSet = new Set(challengeWords);
  const all = [
    ...challengeWords.map((word) => ({ word, cls: "interactive-word-challenge", unsafe: [] })),
    ...(themeWords || []).filter((entry) => !challengeSet.has(entry.word))
      .map((entry) => ({ word: entry.word, cls: "interactive-word-theme", unsafe: entry.unsafe })),
    ...(otherWords || []).filter((entry) => !challengeSet.has(entry.word))
      .map((entry) => ({ word: entry.word, cls: "", unsafe: entry.unsafe })),
  ];
  if (!all.length) return false;
  const placeWord = (word) => {
    if (word.length > slot.cells.length) return;
    const wordCells = side === "start"
      ? slot.cells.slice(0, word.length)
      : slot.cells.slice(slot.cells.length - word.length);
    interactivePushUndo();
    for (let i = 0; i < wordCells.length; i++) {
      const { row, col } = wordCells[i];
      interactiveGrid[row][col] = word[i];
    }
    if (word.length < slot.cells.length) {
      const boundary = side === "start"
        ? slot.cells[word.length]
        : slot.cells[slot.cells.length - word.length - 1];
      interactiveGrid[boundary.row][boundary.col] = "#";
    }
    setInteractiveMessage("");
    renderInteractive();
  };
  const block = document.createElement("div");
  block.className = "interactive-words-block";
  const header = document.createElement("div");
  header.className = "interactive-words-block-header";
  const labelEl = document.createElement("p");
  labelEl.className = "interactive-words-block-label";
  labelEl.textContent = label;
  header.appendChild(labelEl);
  header.appendChild(createInteractiveWordsEyeToggle(block));
  block.appendChild(header);
  all.forEach(({ word, cls, unsafe }) => {
    const item = document.createElement("span");
    item.className = cls ? `interactive-word-item ${cls}` : "interactive-word-item";
    item.tabIndex = 0;
    const unsafeSet = new Set(unsafe);
    if (unsafeSet.size) item.classList.add("interactive-word-item-unsafe");
    appendWordLetters(item, word, -1, unsafeSet);
    const pick = () => placeWord(word);
    item.addEventListener("click", pick);
    item.addEventListener("keydown", (e) => {
      if (e.key === "Enter" || e.key === " ") {
        e.preventDefault();
        pick();
      }
    });
    block.appendChild(item);
  });
  interactiveAnswers.insertBefore(block, interactiveAnswers.firstChild);
  interactiveAnswers.hidden = false;
  return true;
}

async function fetchInteractiveBoundary(side, btn, keys) {
  const t = I18N[uiLanguage];
  const w = selectedInteractiveWord();
  if (!w) {
    setInteractiveMessage(t[keys.needsSlot], true);
    return;
  }
  btn.disabled = true;
  setInteractiveMessage("");
  try {
    const wireGrid = interactiveGrid.map((row) => row.map((ch) => (ch === "" ? "." : ch)));
    const resp = await fetchWithTimeout("/api/interactive/boundary", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        job_id: interactiveJobId,
        grid: wireGrid,
        cells: w.cells.map((c) => [c.row, c.col]),
        side,
        challenge_words: interactiveChallengeWords,
      }),
    }, FETCH_TIMEOUT_MS);
    if (resp.status === 404) {
      setInteractiveMessage(t.interactiveSessionLost, true);
      return;
    }
    if (!resp.ok) throw new Error(t[keys.error]);
    const data = await resp.json();
    const themeWords = (data && data.theme_words) || [];
    const otherWords = (data && data.other_words) || [];
    const dirPrefix = w.direction === "across" ? "H" : "V";
    const label = `${t[keys.btn]} ${dirPrefix} (${w.startRow + 1}, ${w.startCol + 1})`;
    const rendered = renderInteractiveBoundary(themeWords, otherWords, w, side, label);
    if (!rendered) {
      setInteractiveMessage(t[keys.empty], true);
    }
  } catch (err) {
    setInteractiveMessage(t[keys.error], true);
  } finally {
    btn.disabled = false;
  }
}

interactiveStartBtn.addEventListener("click", () => {
  fetchInteractiveBoundary("start", interactiveStartBtn, {
    btn: "interactiveStartBtn",
    needsSlot: "interactiveStartNeedsSlot",
    empty: "interactiveStartEmpty",
    error: "interactiveStartError",
  });
});

interactiveEndBtn.addEventListener("click", () => {
  fetchInteractiveBoundary("end", interactiveEndBtn, {
    btn: "interactiveEndBtn",
    needsSlot: "interactiveEndNeedsSlot",
    empty: "interactiveEndEmpty",
    error: "interactiveEndError",
  });
});

// Fetches up to 10 candidate titles and shows them as a pick list (see
// renderInteractiveTitleProposals()), at the user's explicit request:
// "The 'Proposer un titre' button must generate 10 proposals shown
// below (like 'Proposer une définition'), and also use the Thématique
// field if it's filled in." Two callers, distinguished by
// `autoFill`: the automatic, one-time proposal fired by
// updateInteractiveFinishState() once the grid becomes complete
// (`autoFill=true` — also fills #interactive-title-input with the very
// first proposal, so a player who never bothers clicking still gets a
// real title, exactly like before this feature existed) vs. the
// "Proposer un titre" button's own explicit click (`autoFill=false` —
// only shows the list, never touches whatever the player already typed
// or picked).
async function proposeInteractiveTitle(autoFill) {
  interactiveTitleProposed = true;
  const t = I18N[uiLanguage];
  const words = interactiveSlots().map((s) => ({ answer: s.answer }));
  try {
    const resp = await fetchWithTimeout("/api/interactive/title", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        job_id: interactiveJobId, words, language: interactiveLanguage,
        theme: interactiveTheme || undefined,
      }),
    }, DEFINE_FETCH_TIMEOUT_MS);
    const data = await resp.json();
    const list = (resp.ok && data.titles) || [];
    renderInteractiveTitleProposals(list);
    if (autoFill && list.length && !interactiveTitleInput.value.trim()) {
      interactiveTitleInput.value = list[0];
    }
    // The automatic trigger stays silent on an empty result (matching its
    // own pre-existing behavior — it's a background convenience, not a
    // player-initiated action) — only the explicit button click surfaces
    // this, the same way "Proposer une définition" only ever does for its
    // own explicit click.
    if (!autoFill && !list.length) setInteractiveMessage(t.interactiveTitleEmpty, true);
  } catch (err) {
    setInteractiveMessage(t.interactiveTitleError, true);
  }
}

// ---- Mode lifecycle ----
function enterInteractiveMode(state) {
  interactiveMode = true;
  interactiveJobId = currentJobId || interactiveJobId;
  interactiveGrid = state.grid.map((row) => row.map((ch) => (ch === "." ? "" : ch)));
  interactiveHasTheme = !!state.has_theme;
  // Re-fill the "Thématique" field from this session's own theme — ""
  // for an untouched/themeless grid — at the user's explicit request:
  // "When a user re-edits a themed grid, fill in the Thématique field
  // with the origin grid's own words." Covers every
  // entry path uniformly (a fresh start, "Ouvrir en mode Interactif"
  // from the Library, resuming a "Créations" draft): backend/app.py's
  // _run_interactive_job/_run_interactive_resume_job both put the raw
  // theme string on job["result"]["theme"] (pollJob() only ever returns
  // that, never job["interactive"]). `interactiveTheme` also feeds
  // "Proposer une définition"/"Proposer un titre" below (see
  // dictionaryDefineUrl()/proposeInteractiveTitle()).
  interactiveTheme = state.theme || "";
  setThemeWords(interactiveTheme);
  // `interactiveLanguage`/`interactiveDifficulty` used to only ever be set
  // by the generation form's own submit handler (correct for a fresh
  // start, but left stale — whatever a PREVIOUS session set, or the "fr"/
  // "easy" defaults — for "Ouvrir en mode Interactif" from the Library and
  // for resuming a "Créations" draft, neither of which ever touched them).
  // A real bug, not just cosmetic: "Publier"/"Sauvegarder" both send
  // `language: interactiveLanguage`/`difficulty: interactiveDifficulty`,
  // so re-editing a non-French (or non-"easy") grid this way could
  // silently publish it under the WRONG language/difficulty. Fixed the
  // same way as `interactiveTheme` above — backend/app.py's
  // _run_interactive_job/_run_interactive_resume_job both now include
  // them on job["result"] too, read here uniformly on every entry path.
  interactiveLanguage = state.language || interactiveLanguage;
  // Same reasoning as interactiveLanguage/interactiveDifficulty just
  // above, at the user's explicit request: "when a bilingual grid is
  // loaded, set the languages to those of the grid (same for
  // monolingual)." `state.bilingual_language` is only ever non-null on
  // a genuinely bilingual session (backend/app.py's own is_bilingual
  // check already normalizes it) — a monolingual entry always resets
  // this back to "", never leaving a PREVIOUS session's own bilingual
  // pair stuck on screen.
  interactiveBilingualLanguage = state.bilingual_language || "";
  // Reflect the same two values onto the top-of-page generation selectors
  // themselves (#language/#bilingual-language), at the user's explicit
  // request: "when loading a bilingual grid in edit mode, set both
  // languages in the main selectors at the top of the page (same for
  // monolingual)." Until now, only the internal `interactiveLanguage`/
  // `interactiveBilingualLanguage` variables (and, through them, `puzzle`
  // — see syncPuzzleFromInteractive()) reflected the loaded grid's own
  // language(s); the visible dropdowns kept showing whatever they were
  // last left at, inconsistent with the rest of this same fix. A plain
  // `.value` assignment (no synthetic "change" event) — this never
  // touches `uiLanguage`/setUiLanguage() itself, since loading a grid's
  // own language(s) is a different concern from switching the whole
  // interface's language. For an ordinary monolingual grid, both
  // selectors simply end up showing the same language, exactly as
  // #bilingual-language's own "change" handler already forces for a
  // fresh generation.
  languageSelect.value = interactiveLanguage;
  bilingualLanguageSelect.value = interactiveBilingualLanguage || interactiveLanguage;
  interactiveDifficulty = state.difficulty || interactiveDifficulty;
  // Reconfigure the generation form's own Largeur/Hauteur fields too, at
  // the user's explicit request: re-editing a grid whose own dimensions
  // differ from whatever the form last showed (a previous session, or
  // the "5"/"5" defaults) otherwise left them silently wrong — a real
  // bug for "Publier"/"Sauvegarder", which never read these two fields
  // in the first place (interactiveGrid's own real shape is what's
  // actually saved), but a genuinely misleading display regardless.
  // `state.width`/`state.height` are always present on every entry path
  // (backend/app.py's _run_interactive_job/_run_interactive_resume_job
  // both set them on job["result"], see its own docstring for `width`),
  // so no `||` fallback is needed here unlike interactiveLanguage/
  // interactiveDifficulty just above.
  if (state.width) widthInput.value = String(state.width);
  if (state.height) heightInput.value = String(state.height);
  // Reconfigure the generation-form's own Mode/Taux noir/Graines/
  // Précision thématique fields to match whatever was used to create
  // this grid automatically, at the user's explicit request: "When a
  // grid is saved after automatic creation, save all the parameters
  // (Taux noir, Graines, Mode, Précision Thématique, etc) so they can be
  // reconfigured identically when the grid is reloaded in edit mode."
  // `state.generation_params` is only ever
  // set for a grid whose origin went through the automatic generator
  // (backend/app.py's _run_generate_job) — a fresh interactive session,
  // or one resumed from a Créations draft that itself started that way,
  // has none, and every field below is simply left untouched.
  if (state.generation_params) {
    const gp = state.generation_params;
    if (gp.mode) document.getElementById("mode").value = gp.mode;
    if (gp.black_enrichment_percent !== undefined && gp.black_enrichment_percent !== null) {
      blackEnrichmentInput.value = gp.black_enrichment_percent;
    }
    if (gp.force_letters_percent !== undefined && gp.force_letters_percent !== null) {
      document.getElementById("force-letters").value = gp.force_letters_percent;
    }
    if (gp.theme_precision !== undefined && gp.theme_precision !== null) {
      document.getElementById("theme-precision").value = gp.theme_precision;
    }
  }
  // A resumed session's own result carries `definitions`/`title` (see
  // backend/app.py's _run_interactive_resume_job) — a fresh start's never
  // does (neither field exists yet), so `interactiveDefs` still starts
  // empty and the title input still starts blank in that case, exactly
  // as before this feature existed.
  interactiveDefs = new Map();
  for (const d of state.definitions || []) {
    if (d && d.clue) interactiveDefs.set(`${d.row},${d.col},${d.direction}`, d.clue);
  }
  interactiveThemeCells = new Set();
  if (state.placed && state.placed.from_theme && state.placed.cells) {
    for (const [r, c] of state.placed.cells) interactiveThemeCells.add(`${r},${c}`);
  }
  interactiveChallengeCells = new Set();
  if (state.placed && state.placed.from_challenge && state.placed.cells) {
    for (const [r, c] of state.placed.cells) interactiveChallengeCells.add(`${r},${c}`);
  }
  // "Mots Défi" list restore, at the user's explicit request — only ever
  // set on a resumed session's own result (backend/app.py's _run_
  // interactive_resume_job), same convention as `definitions`/`title`
  // just above; a fresh start's result carries none, so this correctly
  // resets to an empty list on that path.
  interactiveChallengeWords = Array.isArray(state.challenge_words)
    ? state.challenge_words.slice() : [];
  renderInteractiveChallengeList();
  // Same restore onto the main generation form's own "Mots Défi
  // (personnalisation)" mini-form (#generate-challenge-panel), at the
  // user's explicit request — mirrors how `setThemeWords()` above
  // already re-fills "Thématique" from this same loaded grid: reopening
  // a library grid in edit mode should show its "Mots Défi" list at the
  // top of the page too, not just in the Interactive-mode panel below.
  generateChallengeWords = interactiveChallengeWords.slice();
  renderGenerateChallengeList();
  interactiveUndoStack = [];
  interactivePushUndo();
  setInteractiveDiagnostics(state); // after pushUndo (which clears them)
  interactiveTitleProposed = false;
  interactiveTitleInput.value = state.title || "";
  interactiveSaveResult.textContent = "";
  interactiveProposeResults.hidden = true;
  interactiveProposeResults.innerHTML = "";
  interactiveTitleProposeResults.hidden = true;
  interactiveTitleProposeResults.innerHTML = "";
  // Unlike the two panels just above, "Mots"/"Croisés" (a single shared
  // #interactive-answers zone) are no longer cleared on every ordinary
  // render (see renderInteractive()'s own comment) — its stacked blocks
  // must still be wiped here, at the start of a genuinely new session,
  // so a previous grid's own accumulated answers never survive into a
  // freshly loaded/started one.
  interactiveAnswers.hidden = true;
  interactiveAnswers.innerHTML = "";
  // A drag-selected "zone" (see interactiveZoneSelection) is scoped to
  // one editing session — never carried over into a freshly loaded/
  // started one.
  interactiveZoneSelection = null;
  interactiveTitleRow.hidden = true;
  interactiveSaveBtn.hidden = true;
  interactiveDraftSaveBtn.disabled = false;

  selected = null;
  if (state.placed && state.placed.cells && state.placed.cells.length) {
    const [r0, c0] = state.placed.cells[0];
    selected = { row: r0, col: c0 };
    activeDirection = state.placed.direction || "across";
  } else {
    const rows = interactiveGrid.length;
    const cols = rows ? interactiveGrid[0].length : 0;
    for (let r = 0; r < rows && !selected; r++) {
      for (let c = 0; c < cols && !selected; c++) {
        if (interactiveGrid[r][c] !== "#") selected = { row: r, col: c };
      }
    }
  }

  // Reveal the panel inside #result; hide play-mode-only chrome.
  result.hidden = false;
  interactiveControls.hidden = false;
  interactiveResults.hidden = false;
  solutionBtn.hidden = true;
  checkBtn.hidden = true;
  definitionsBtn.hidden = true;
  recomputeBtn.hidden = true;
  attemptPreviewRevealBtn.hidden = true;
  gridTitleEl.hidden = true;
  stats.textContent = "";
  generationTimes.textContent = "";
  // These duplicate the "reprise/recherche" history navigation of the
  // automatic-generation preview (#attempt-preview) — meaningless here,
  // where "Précédent"/"Suivant" (below) already do the interactive mode's
  // own, entirely different job (undo one edit / place one more word).
  // Reported directly by the user: leftover arrows floating above the
  // grid with no "Grille générée" text next to them, since this function
  // already empties that text but never hid the buttons themselves.
  // `interactiveMode` is already `true` at this point (see the top of this
  // function) — updatePreviewNavButtons() is the single source of truth
  // for this trio's own visibility (see its own comment).
  updatePreviewNavButtons();
  cluesAcross.innerHTML = "";
  cluesDown.innerHTML = "";
  applyDefinitionsVisibility(); // hides #clues/#down-clues-section/#hover-definition-row
  // "Précédent"/"Suivant" flank the grid, vertically centered against it,
  // at the user's explicit request — moved here (out of the play-mode-
  // shaped #interactive-nav row) into #interactive-flank-row, a plain,
  // unconditional flex row inside #grid-column wrapping the pair around
  // #interactive-grid-wrap (see style.css's own comment on that row) — no
  // JS class toggle needed for THAT layout, only the two buttons' own
  // `hidden` attribute below. The `interactive-flank` class added right
  // here still matters for a different reason: it makes #grid-column
  // itself span the full "Grille + Boutons" width next to "Mots Défi"
  // (rather than shrinking to its own content), so #interactive-controls
  // can in turn stretch to fill that same full width — see style.css's
  // own comment on #grid-column.interactive-flank.
  gridColumn.classList.add("interactive-flank");
  // #board is now unconditionally centered (see its own style.css
  // comment) — this mode's own Précédent+Grille+Suivant row lands
  // centered "for free" within the now-wider #grid-column.
  interactivePrevBtn.hidden = false;
  interactiveNextBtn.hidden = false;
  interactiveChallengePanel.hidden = false;
  // A full grid reported "impossible" (e.g. a resumed session the player
  // had already finished) is complete, not a dead end — show the success
  // message instead. Validity isn't re-checked here (no round trip on
  // entry); "Vérifier" confirms it on demand.
  const startImpossible = state.impossible && !interactiveGridFilled();
  setInteractiveMessage(
    state.impossible
      ? (startImpossible ? I18N[uiLanguage].interactiveImpossible : I18N[uiLanguage].interactiveCompleteValid)
      : "",
    startImpossible,
  );
  // "In interactive mode, automatically open the Dictionnaire." — at
  // the user's explicit request. Mirrors dictionaryBtn's own manual-open
  // logic (unhide the panel, set its language) but deliberately never
  // calls dictionaryInput.focus(): an automatic/background action must
  // never steal keyboard focus away from the grid the way a real click
  // on the button is allowed to. `interactiveLanguage` is reliably
  // correct here on every entry path (fresh start, "Ouvrir en mode
  // Interactif" from the Library, resuming a draft) — see its own
  // assignment above, fixed for exactly this kind of use. This now
  // applies unconditionally, including on the "Finir la grille"/"Finir
  // la zone" auto-reopen (runGeneration()'s own resumeInteractiveWork()
  // call, see runInteractiveFinish()) — an earlier version instead kept
  // the Dictionnaire forcibly closed on that specific return path (via a
  // since-removed `hideToolPanels` option), but the user explicitly
  // corrected this: the return to Édition mode should reopen it like any
  // other entry into this mode. The Paraphraseur needs no matching
  // special-case here: runInteractiveFinish() already force-closes it
  // the moment "Finir la grille"/"Finir la zone" is clicked (its own
  // stale-content concern, unrelated to this Dictionnaire auto-open
  // policy), and nothing in between ever reopens it — it simply stays
  // closed until the player opens it again by hand.
  dictionaryPanel.hidden = false;
  dictionaryLanguage.value = interactiveLanguage;
  // "The Library must reopen automatically when Edition mode is shown
  // again" — at the user's explicit request. As long as
  // `libraryReopenOnInteractive` stays true, it reopens every time this
  // mode is (re)shown, including after a round trip through "Finir la
  // grille"/"Finir la zone" — see this flag's own declaration (right
  // before openLibraryGridInteractive) for when it's set to true/reset to
  // false. Deliberately checked BEFORE consuming `libraryReopenArmPending`
  // below, so the very first entry (freshly armed by
  // openLibraryGridInteractive(), library already closed on purpose)
  // never reopens it — only every entry after that one does.
  if (libraryReopenOnInteractive) openLibraryPanel();
  if (libraryReopenArmPending) {
    libraryReopenArmPending = false;
    libraryReopenOnInteractive = true;
  }
  // Rebuild `puzzle` right now (normally only ever refreshed by
  // renderInteractive(), itself only reached via setActiveDirection()
  // further below) so defaultToBilingualOption()'s own currentBilingualLangs()
  // check already sees THIS session's real language/bilingual_language
  // pair instead of whatever puzzle a previous session (or none) left
  // behind — a real, previously-reported bug: on a genuinely bilingual
  // interactive session, the Dictionary's own combined-language option
  // never appeared because this call used to run before puzzle was ever
  // synced. setActiveDirection() below calls renderInteractive() again
  // regardless, harmlessly recomputing the identical puzzle a second
  // time (nothing else changes state in between).
  syncPuzzleFromInteractive();
  defaultToBilingualOption(dictionaryLanguage);
  syncRssPanelVisibility();
  setActiveDirection(activeDirection); // syncs the 4 direction buttons + renders
}

function hideInteractivePanel() {
  interactiveMode = false;
  interactiveControls.hidden = true;
  interactiveResults.hidden = true;
  gridColumn.classList.remove("interactive-flank");
  interactivePrevBtn.hidden = true;
  interactiveNextBtn.hidden = true;
  interactiveChallengePanel.hidden = true;
  interactiveCellStats.hidden = true;
  interactiveZoneSelection = null;
  interactiveDragStart = null;
  interactiveDragCurrent = null;
  interactiveDragActive = false;
  // The time counter is only relevant to play mode — stopped every time
  // we leave it or are about to (re)start a new one (displayFinalGrid
  // calls startGridTimer again right after). "Interactif" mode has its
  // own save mechanism (GRID_WORK), with no counter.
  hideGridTimer();
  // Restore the automatic-generation history-navigation controls hidden by
  // enterInteractiveMode() — only actually shown again if previewHistory
  // itself is non-empty (updatePreviewNavButtons() is the single source
  // of truth for this trio's own visibility, see its own comment).
  // `interactiveMode` is already `false` at this point (see the top of
  // this function).
  updatePreviewNavButtons();
  applyDefinitionsVisibility(); // restore normal #clues/#hover-definition-row rules
  syncRssPanelVisibility();
  // Never leave the help overlay stuck open once Interactive mode itself
  // is exited (its own "?" button disappears along with #interactive-
  // controls, but the overlay is a sibling `position: fixed` element that
  // would otherwise still be visible).
  closeInteractiveHelp();
}

function exitInteractiveMode() {
  hideInteractivePanel();
}

// ---- "Créations" panel (GRID_WORK autosaves) ----

// Every complete slot's own definition, as {row, col, direction, clue} —
// shared by the final "Sauvegarder" button and every autosave below, at
// the user's explicit request that "Suivant"/"Précédent" persist the same
// kind of state a final save already would.
function interactiveDefinitionsPayload() {
  return interactiveSlots().map((s) => ({
    row: s.startRow,
    col: s.startCol,
    direction: s.direction,
    clue: interactiveDefs.get(interactiveKey(s)) || "",
  }));
}

// "Finir la grille"/"Finir la zone" : every "row,col" the backend will
// lock/freeze forever (or, for "Finir la zone", revert back to its exact
// pre-click value — see backend/app.py's `interactive_finish`/
// `_run_generate_job`'s own `zone_revert`), used as the light-green
// preview highlight (see finishLockedCells above/renderAttemptPreview()).
// Every already-lettered cell of `interactiveGrid` (implicitly, and
// exactly, the set of cells POST /api/interactive/finish itself derives
// its own locked-letter set from — the same whole grid is sent either
// way).
//
// `zoneCells` ("Finir la zone" only, `null`/omitted for "Finir la
// grille") additionally adds every cell OUTSIDE the selected zone —
// whether black, lettered (already covered above), or blank — at the
// user's own explicit correction, after a version that only marked the
// zone's own pre-existing black cells here: "at the first step, the
// grayed-out zone... isn't shown in green... Every grayed-out cell must
// be locked." The backend now genuinely reverts every one
// of these cells, in every live preview AND the final saved grid, back
// to exactly its pre-click value — so marking them green here is no
// longer a promise the backend can't keep (unlike the very first version
// of this feature, which forced them all black): they really do stay
// untouched, start to finish.
function computeFinishLockedCells(zoneCells) {
  const cells = new Set();
  for (let r = 0; r < interactiveGrid.length; r++) {
    const row = interactiveGrid[r];
    for (let c = 0; c < row.length; c++) {
      const ch = row[c];
      if (ch && ch !== "#") cells.add(`${r},${c}`);
    }
  }
  if (zoneCells && zoneCells.length) {
    const zone = new Set(zoneCells.map(([r, c]) => `${r},${c}`));
    for (let r = 0; r < interactiveGrid.length; r++) {
      for (let c = 0; c < interactiveGrid[r].length; c++) {
        const key = `${r},${c}`;
        if (!zone.has(key)) cells.add(key);
      }
    }
  }
  return cells;
}

// Fired after every "Suivant"/"Précédent" click, at the user's explicit
// request: "every press of Suivant/Précédent saves the creation process's
// current state into the GRID_WORK folder." Best-effort and
// silent — a failure here (network blip, or the in-memory session having
// since expired) must never interrupt the player's own action, which has
// already happened client-side regardless of whether this succeeds.
async function autosaveInteractiveWork() {
  if (!interactiveMode || !interactiveJobId) return;
  try {
    const wireGrid = interactiveGrid.map((row) => row.map((ch) => (ch === "" ? "." : ch)));
    await fetchWithTimeout("/api/interactive/save_work", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        job_id: interactiveJobId,
        grid: wireGrid,
        definitions: interactiveDefinitionsPayload(),
        title: interactiveTitleInput.value.trim(),
        pseudo: userPseudo || undefined,
        // "Mots Défi" persisted across a pause/resume of the editing
        // session, at the user's explicit request — see backend/app.py's
        // InteractiveSaveWorkRequest/grid_store.save_grid_work.
        challenge_words: interactiveChallengeWords,
        diagnostics: interactiveDiagnosticsPayload(),
      }),
    }, FETCH_TIMEOUT_MS);
  } catch (err) {
    // Silently ignored — see this function's own comment above.
  }
}

function hideInteractiveWorkPanel() {
  interactiveWorkPanel.hidden = true;
  syncRssPanelVisibility();
}

// Relaunches a saved work-in-progress session exactly where it stopped —
// reuses runInteractive()'s own full flow (hides play-mode chrome, shows
// "Stop", polls, calls enterInteractiveMode with the result) by pointing
// it at POST /api/interactive/resume instead of .../start — including
// runGeneration()'s own "Finir la grille"/"Finir la zone" auto-reopen,
// which relies on this same path to bring the player straight back into
// Édition mode (see its own comment) once the automatic fill finishes.
async function resumeInteractiveWork(workId) {
  await runInteractive({ work_id: workId }, "/api/interactive/resume");
}

// Deletes one saved work-in-progress file, then refreshes the list — at
// the user's explicit request: "click an icon button to delete the
// task." Best-effort: a failure here just leaves the list
// showing what it already had (no error message — this is a minor
// housekeeping action, not worth interrupting the player over).
async function deleteInteractiveWork(workId) {
  try {
    await fetchWithTimeout("/api/interactive/work/delete", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ work_id: workId }),
    }, FETCH_TIMEOUT_MS);
  } catch (err) {
    // Best-effort — see this function's own comment above.
  }
  renderInteractiveWorkList();
}

// Renders the current list of saved work-in-progress sessions belonging
// to the current pseudo (GET /api/interactive/work?pseudo=...  — the web
// UI always has a pseudo set by the time this can be called, see
// checkForSavedInteractiveWork(), so every player only ever sees their
// own in-progress creations). Each row resumes that session on click
// (or Enter/Space, via tabIndex — same accessible-row convention already
// established for #library-table's own rows); its own trailing delete
// button stops propagation so it never also triggers a resume.
async function renderInteractiveWorkList() {
  const t = I18N[uiLanguage];
  interactiveWorkTbody.innerHTML = "";
  let items = [];
  try {
    const resp = await fetchWithTimeout(
      `/api/interactive/work?pseudo=${encodeURIComponent(userPseudo || "")}`,
      {}, FETCH_TIMEOUT_MS,
    );
    const data = await resp.json();
    items = (resp.ok && data.items) || [];
  } catch (err) {
    items = [];
  }
  interactiveWorkEmpty.hidden = items.length > 0;
  const difficultyKeys = { easy: "difficultyEasy", medium: "difficultyMedium", hard: "difficultyHard" };
  for (const item of items) {
    const tr = document.createElement("tr");
    tr.tabIndex = 0;
    const resume = () => resumeInteractiveWork(item.id);
    tr.addEventListener("click", resume);
    tr.addEventListener("keydown", (e) => {
      if (e.key === "Enter" || e.key === " ") {
        e.preventDefault();
        resume();
      }
    });

    // Same columns, in the same order and format, as #library-table
    // (renderLibraryList) — at the user's explicit request: "complete the
    // columns and place them in the same order as 'Bibliothèque'."
    const langTd = document.createElement("td");
    const languageOption = languageSelect.querySelector(`option[value="${item.language}"]`);
    langTd.textContent = languageOption ? languageOption.textContent : (item.language || "");
    tr.appendChild(langTd);

    const dateTd = document.createElement("td");
    dateTd.textContent = item.created_at ? new Date(item.created_at).toLocaleString(uiLanguage) : "";
    tr.appendChild(dateTd);

    const titleTd = document.createElement("td");
    titleTd.textContent = item.title || t.interactiveWorkUntitled;
    tr.appendChild(titleTd);

    const themeTd = document.createElement("td");
    themeTd.textContent = item.theme || "";
    tr.appendChild(themeTd);

    const diffTd = document.createElement("td");
    const diffKey = difficultyKeys[item.difficulty];
    diffTd.textContent = diffKey ? t[diffKey] : (item.difficulty || "");
    tr.appendChild(diffTd);

    const sizeTd = document.createElement("td");
    sizeTd.textContent = item.width && item.height ? `${item.width}×${item.height}` : "";
    tr.appendChild(sizeTd);

    // Specific to this panel (no equivalent column in the Library) —
    // kept after the shared columns, at the user's explicit request
    // ("Keep the heading 'Dernière modification'").
    const updatedTd = document.createElement("td");
    updatedTd.textContent = item.updated_at
      ? new Date(item.updated_at).toLocaleString(uiLanguage)
      : "";
    tr.appendChild(updatedTd);

    const deleteTd = document.createElement("td");
    const deleteBtn = document.createElement("button");
    deleteBtn.type = "button";
    deleteBtn.className = "nav-btn interactive-work-delete-btn";
    deleteBtn.textContent = "🗑";
    deleteBtn.title = t.interactiveWorkDeleteBtn;
    deleteBtn.setAttribute("aria-label", t.interactiveWorkDeleteBtn);
    deleteBtn.addEventListener("click", (e) => {
      e.stopPropagation();
      deleteInteractiveWork(item.id);
    });
    deleteBtn.addEventListener("keydown", (e) => e.stopPropagation());
    deleteTd.appendChild(deleteBtn);
    tr.appendChild(deleteTd);

    interactiveWorkTbody.appendChild(tr);
  }
}

interactiveWorkBtn.addEventListener("click", () => {
  if (interactiveWorkPanel.hidden) {
    interactiveWorkPanel.hidden = false;
    syncRssPanelVisibility();
    renderInteractiveWorkList();
  } else {
    hideInteractiveWorkPanel();
  }
});

interactiveWorkCloseBtn.addEventListener("click", hideInteractiveWorkPanel);
interactiveWorkRefreshBtn.addEventListener("click", () => {
  renderInteractiveWorkList();
});

// "When a user opens the interface (or reloads it), if they have grids
// saved in GRID_WORK, show a panel with the list," at the user's
// explicit request — called once, right after the current pseudo is
// actually known (see initUserPrefs()'s returning-user branch and the
// welcome form's own submit handler below), never before: without a
// pseudo there is no reliable way to know which saved sessions belong to
// this particular player. "closing the panel and leaving it unchanged
// for next time" is already the default behaviour of
// hideInteractiveWorkPanel() — it never touches GRID_WORK itself, so the
// exact same check simply runs again, and can show the panel again, on
// the next page load.
async function checkForSavedInteractiveWork() {
  if (!userPseudo) return;
  try {
    const resp = await fetchWithTimeout(
      `/api/interactive/work?pseudo=${encodeURIComponent(userPseudo)}`,
      {}, FETCH_TIMEOUT_MS,
    );
    const data = await resp.json();
    if (resp.ok && data.items && data.items.length) {
      interactiveWorkPanel.hidden = false;
      syncRssPanelVisibility();
      renderInteractiveWorkList();
    }
  } catch (err) {
    // No saved-work check on a connection failure — the "Créations"
    // button still lets the player open the panel by hand later.
  }
}

// `endpoint` defaults to the fresh-start route; the "Créations" panel's
// own resume flow calls this same function with `/api/interactive/resume`
// instead (see resumeInteractiveWork below) — every other bit of UI state
// handling (hiding play-mode chrome, showing "Stop", polling, entering
// interactive mode with the result) is identical either way, only the
// endpoint and the request body itself differ (resume's own body is just
// `{work_id}` — no `mode` field to merge in, unlike a fresh start's).
async function runInteractive(body, endpoint = "/api/interactive/start") {
  const t = I18N[uiLanguage];
  generationInProgress = true;
  button.disabled = true;
  result.hidden = true;
  solutionBtn.hidden = true;
  checkBtn.hidden = true;
  definitionsBtn.hidden = true;
  recomputeBtn.hidden = true;
  attemptPreviewRevealBtn.hidden = true;
  continueBtn.hidden = true;
  stopBtn.hidden = false;
  stopBtn.disabled = false;
  currentJobId = null;
  setStatus(t.statusGenerating, false);
  hideAttemptPreview();
  hideInteractivePanel();
  hideInteractiveWorkPanel();
  syncRssPanelVisibility();
  // A genuinely fresh authoring session (the top form's own "Générer la
  // grille" with Mode set to "Interactif", still reachable — and its
  // Mode dropdown often still reading "interactive" — while already
  // editing a grid opened from the Library) must never inherit that
  // earlier session's own pending Library-reopen intent: a real,
  // previously-reported regression, since `libraryReopenOnInteractive`/
  // `libraryReopenArmPending` (see their own declaration) are plain
  // module-level flags with no per-session scoping — left armed from an
  // earlier `openLibraryGridInteractive()` call, they would otherwise
  // reopen the Library the moment THIS unrelated new session's own
  // enterInteractiveMode() call runs. `/api/interactive/resume` is
  // deliberately exempt: that's the endpoint both a legitimate "Finir la
  // grille"/"Finir la zone" round trip and a "Créations" draft resume
  // use, and either one may legitimately need these flags to survive.
  if (endpoint === "/api/interactive/start") {
    libraryReopenOnInteractive = false;
    libraryReopenArmPending = false;
  }

  try {
    let response;
    try {
      const payload = endpoint === "/api/interactive/start"
        ? { ...body, mode: "interactive" }
        : body;
      response = await fetchWithTimeout(endpoint, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      }, FETCH_TIMEOUT_MS);
    } catch (err) {
      throw new Error(t.errorConnectionLost);
    }
    const data = await response.json();
    if (!response.ok) {
      throw new Error(describeErrorCode(t, data.detail && data.detail.code, data.detail));
    }
    currentJobId = data.job_id;
    interactiveJobId = data.job_id;
    const startResult = await pollJob(data.job_id, t);
    enterInteractiveMode(startResult);
    setStatus(t.statusGenerated, false);
  } catch (err) {
    setStatus(err.message, !(err instanceof CancelledError));
  } finally {
    button.disabled = false;
    stopBtn.hidden = true;
    currentJobId = null;
    generationInProgress = false;
    syncRssPanelVisibility();
  }
}

// ---- Button / input wiring ----
interactivePrevBtn.addEventListener("click", async () => {
  setInteractiveMessage("");
  interactiveUndo();
  // Autosave after "Précédent" too, at the user's explicit request — this
  // is the one interactive-mode mutation that never talks to the backend
  // on its own (a plain client-side undo), so it needs its own explicit
  // save call rather than piggybacking on an existing request/response.
  await autosaveInteractiveWork();
});

interactiveNextBtn.addEventListener("click", async () => {
  const t = I18N[uiLanguage];
  interactiveNextBtn.disabled = true;
  setInteractiveMessage("");
  interactivePushUndo();
  try {
    const wireGrid = interactiveGrid.map((row) => row.map((ch) => (ch === "" ? "." : ch)));
    // "Mots Défi" sent along on every "Suivant" click, at the user's
    // explicit request, so the backend can draw the placed word from this
    // list first (ahead of the theme glossary) whenever one still fits —
    // see backend/app.py's InteractiveStepRequest/interactive_step.
    const resp = await fetchWithTimeout("/api/interactive/step", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        job_id: interactiveJobId, grid: wireGrid,
        challenge_words: interactiveChallengeWords,
      }),
    }, FETCH_TIMEOUT_MS);
    if (resp.status === 404) {
      interactiveUndoStack.pop();
      setInteractiveMessage(t.interactiveSessionLost, true);
      return;
    }
    const data = await resp.json();
    if (!resp.ok) {
      interactiveUndoStack.pop();
      setInteractiveMessage(describeErrorCode(t, data.detail && data.detail.code, data.detail), true);
      return;
    }
    if (data.impossible) {
      interactiveUndoStack.pop();
      // Grid unchanged, but the highlights still describe it — show them.
      setInteractiveDiagnostics(data);
      // "Suivant" placing nothing more is only a real dead end while the
      // grid still has empty cells. If every white cell is filled and
      // every word is a real dictionary entry, this is success, not
      // "impossible" — say so instead (at the user's explicit request).
      let complete = false;
      if (interactiveGridFilled()) {
        try {
          const vresp = await fetchWithTimeout("/api/interactive/verify", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
              job_id: interactiveJobId,
              words: interactiveSlots().map((s) => ({ answer: s.answer, direction: s.direction })),
              challenge_words: interactiveChallengeWords,
            }),
          }, FETCH_TIMEOUT_MS);
          if (vresp.ok) {
            const vdata = await vresp.json();
            complete = !(vdata.invalid_words || []).length;
          }
        } catch (err) {
          /* verify unreachable — fall back to the "impossible" message */
        }
      }
      setInteractiveMessage(
        complete ? t.interactiveCompleteValid : t.interactiveImpossible,
        !complete,
      );
      renderInteractive();
      return;
    }
    // The whole grid is replaced from the response, not just `placed.cells`
    // patched in one by one: "Suivant" can now also reshape the black-cell
    // layout itself before placing the word (see backend/crossword_gen.py's
    // `_widen_floating_black_cells_for_priority_words`, reused by
    // `interactive_place_word` for an over-length "Mots Défi"/theme word
    // with no matching-length slot yet), moving a black cell to a cell
    // that's never part of `placed.cells` — patching only the placed
    // word's own cells silently dropped that black-cell move client-side,
    // leaving the newly opened/closed cell showing its stale state (a
    // slot missing the black cell that should have closed it) even though
    // the backend's own grid was correct. Same reasoning as "Nettoyer"
    // just below, which already replaces the whole grid for the same
    // "can change cells outside the obvious ones" reason.
    interactiveGrid = data.grid.map((row) => row.map((ch) => (ch === "." ? "" : ch)));
    if (data.placed.from_theme) {
      for (const [r, c] of data.placed.cells) interactiveThemeCells.add(`${r},${c}`);
    }
    if (data.placed.from_challenge) {
      for (const [r, c] of data.placed.cells) interactiveChallengeCells.add(`${r},${c}`);
    }
    setInteractiveDiagnostics(data);
    interactiveLastPlaced = data.placed;
    selected = { row: data.placed.cells[0][0], col: data.placed.cells[0][1] };
    activeDirection = data.placed.direction || activeDirection;
    setActiveDirection(activeDirection);
    // Autosave after a genuine placement only, at the user's explicit
    // request — never on "impossible"/an error above, where the grid
    // itself never actually changed (interactiveUndoStack.pop() already
    // reverted it), so there is nothing new worth persisting.
    await autosaveInteractiveWork();
  } catch (err) {
    interactiveUndoStack.pop();
    setInteractiveMessage(t.errorConnectionLost, true);
  } finally {
    interactiveNextBtn.disabled = false;
  }
});

// "Nettoyer" — full cleanup of every impossible zone (remove crossing
// words, or blacken a cell), the same "nettoyage complet" the automatic
// generator applies at each palier — at the user's explicit request.
// Cleanup can change cells anywhere in the grid, so the whole
// interactiveGrid is replaced from the response rather than patched cell
// by cell — same reasoning "Suivant" itself now also follows, above.
// Shared by "Nettoyer" and "Nettoyer (+noires)" — same request/undo/error
// handling either way, only `deep` (sent to the backend) and the
// resulting status message differ. Both buttons are disabled while
// either request is in flight, so the two can never overlap and race on
// the same interactiveGrid/undo stack.
async function runInteractiveClean(deep) {
  const t = I18N[uiLanguage];
  interactiveCleanBtn.disabled = true;
  interactiveCleanDeepBtn.disabled = true;
  setInteractiveMessage("");
  interactivePushUndo();
  try {
    const wireGrid = interactiveGrid.map((row) => row.map((ch) => (ch === "" ? "." : ch)));
    const resp = await fetchWithTimeout("/api/interactive/clean", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        job_id: interactiveJobId, grid: wireGrid, deep,
        challenge_words: interactiveChallengeWords,
      }),
    }, FETCH_TIMEOUT_MS);
    if (resp.status === 404) {
      interactiveUndoStack.pop();
      setInteractiveMessage(t.interactiveSessionLost, true);
      return;
    }
    const data = await resp.json();
    if (!resp.ok) {
      interactiveUndoStack.pop();
      setInteractiveMessage(describeErrorCode(t, data.detail && data.detail.code, data.detail), true);
      return;
    }
    if (!data.changed) {
      // Nothing to undo — this click made no change at all.
      interactiveUndoStack.pop();
      setInteractiveMessage(t.interactiveNothingToClean, false);
      return;
    }
    interactiveGrid = data.grid.map((row) => row.map((ch) => (ch === "." ? "" : ch)));
    setInteractiveDiagnostics(data);
    setInteractiveMessage(
      deep ? t.interactiveDeepCleaned(data.cleared_count, data.removed_black_count || 0)
           : t.interactiveCleaned(data.cleared_count),
      false,
    );
    renderInteractive();
  } catch (err) {
    interactiveUndoStack.pop();
    setInteractiveMessage(t.errorConnectionLost, true);
  } finally {
    interactiveCleanBtn.disabled = false;
    interactiveCleanDeepBtn.disabled = false;
  }
}

interactiveCleanBtn.addEventListener("click", () => runInteractiveClean(false));
interactiveCleanDeepBtn.addEventListener("click", () => runInteractiveClean(true));

// Interactive mode's help panel ("?" button to the left of "Mots") —
// fixed text translated per language (see interactiveHelpLines in
// i18n.js), at the user's explicit request. Rebuilt on every opening (and
// re-rendered if the language changes while it's open, see
// setUiLanguage()) rather than once at load, so it always stays in the
// interface's current language.
function renderInteractiveHelpList() {
  const t = I18N[uiLanguage];
  // `innerHTML`, not `textContent` — each line carries a `<strong>` around
  // the real button names it mentions (see interactiveHelpLines in
  // i18n.js), at the user's explicit request. Safe here specifically
  // because these lines are 100% static, developer-authored strings, never
  // user/LLM content — unlike renderMarkdown()/sanitizeRssHtml() elsewhere
  // in this file, no sanitization step is needed for this one.
  interactiveHelpList.replaceChildren(
    ...t.interactiveHelpLines.map((line) => {
      const li = document.createElement("li");
      li.innerHTML = line;
      return li;
    }),
  );
}

function openInteractiveHelp() {
  renderInteractiveHelpList();
  interactiveHelpOverlay.hidden = false;
}

function closeInteractiveHelp() {
  interactiveHelpOverlay.hidden = true;
}

interactiveHelpBtn.addEventListener("click", openInteractiveHelp);
interactiveHelpCloseBtn.addEventListener("click", closeInteractiveHelp);

// Closing via the keyboard (Escape key), same convention as #rss-detail —
// checks `!interactiveHelpOverlay.hidden` first, no cost or effect when
// the panel isn't open anyway.
document.addEventListener("keydown", (event) => {
  if (!interactiveHelpOverlay.hidden && event.key === "Escape") {
    closeInteractiveHelp();
  }
});

interactiveDirAcrossBtn.addEventListener("mousedown", (e) => e.preventDefault());
interactiveDirDownBtn.addEventListener("mousedown", (e) => e.preventDefault());
interactiveDirAcrossBtn.addEventListener("click", () => setActiveDirection("across"));
interactiveDirDownBtn.addEventListener("click", () => setActiveDirection("down"));

interactiveDefinitionInput.addEventListener("input", () => {
  const w = selectedInteractiveWord();
  if (!w) return;
  const v = interactiveDefinitionInput.value.trim();
  if (v) interactiveDefs.set(interactiveKey(w), v);
  else interactiveDefs.delete(interactiveKey(w));
  updateInteractiveFinishState();
});

// GET /api/dictionary/define URL for one word, in the CURRENT session
// language, carrying the CURRENT "Thématique" field's value along (when
// not blank) — shared by every interactive-mode caller of this endpoint
// ("Proposer" below and the bulk "Définitions" button further down), at
// the user's explicit request: "Check that the 'Propose une définition'
// button genuinely uses the theme field for the proposals when it's
// filled in." `interactiveTheme` is kept current by
// enterInteractiveMode() (re-editing/resuming a themed grid) and by the
// generation form's own submit handler (a fresh themed session) — never
// re-read from the DOM here, so a mid-session edit of the visible field
// alone (without going through those two paths) isn't picked up; that
// matches every other interactive-mode use of `interactiveLanguage`/
// `interactiveDifficulty`, which are session-scoped the same way.
function dictionaryDefineUrl(word, direction) {
  // A down word on a genuinely bilingual interactive session is in the
  // second language — see interactiveBilingualLanguage — everything else
  // (a monolingual session, or an across word on a bilingual one) stays
  // on interactiveLanguage, unchanged from before this parameter existed.
  const lang = (direction === "down" && interactiveBilingualLanguage)
    ? interactiveBilingualLanguage
    : interactiveLanguage;
  let url = `/api/dictionary/define?q=${encodeURIComponent(word)}`
    + `&lang=${encodeURIComponent(lang)}`;
  if (interactiveTheme) url += `&theme=${encodeURIComponent(interactiveTheme)}`;
  return url;
}

// "Corriger" (next to "Proposer une définition" and "Proposer un titre"):
// sends `input`'s text to GET /api/correct (the LLM fixes agreement errors,
// typos and missing spaces, keeping the wording), shows the before/after
// comparison in `results` and writes the corrected text back into `input`,
// firing its "input" event so it is stored like a typed edit. The text is
// only written back if the field still shows what was sent (the player may
// have moved to another word meanwhile).
async function correctInteractiveField(input, btn, results, lang, needsTextMsg) {
  const t = I18N[uiLanguage];
  const text = input.value.trim();
  if (input.disabled || !text) {
    setInteractiveMessage(needsTextMsg, true);
    return;
  }
  btn.disabled = true;
  setInteractiveMessage("");
  try {
    const resp = await fetchWithTimeout(
      `/api/correct?q=${encodeURIComponent(text)}&lang=${encodeURIComponent(lang)}`,
      {}, DEFINE_FETCH_TIMEOUT_MS,
    );
    if (!resp.ok) throw new Error(t.interactiveCorrectError);
    const data = await resp.json();
    const corrected = ((data && data.corrected) || "").trim();
    if (!corrected || input.value.trim() !== text) return;
    renderInteractiveCorrection(results, text, corrected);
    if (corrected === text) {
      setInteractiveMessage(t.interactiveCorrectUnchanged, false);
      return;
    }
    input.value = corrected;
    input.dispatchEvent(new Event("input"));
    setInteractiveMessage(t.interactiveCorrectDone, false);
  } catch (err) {
    setInteractiveMessage(t.interactiveCorrectError, true);
  } finally {
    btn.disabled = false;
  }
}

// Definition: language follows the selected word's direction, like
// dictionaryDefineUrl(). Title: the session language.
interactiveCorrectBtn.addEventListener("click", () => {
  const w = selectedInteractiveWord();
  const lang = (w && w.direction === "down" && interactiveBilingualLanguage)
    ? interactiveBilingualLanguage
    : interactiveLanguage;
  correctInteractiveField(
    interactiveDefinitionInput, interactiveCorrectBtn, interactiveProposeResults,
    lang, I18N[uiLanguage].interactiveCorrectNeedsText,
  );
});
interactiveTitleCorrectBtn.addEventListener("click", () => {
  correctInteractiveField(
    interactiveTitleInput, interactiveTitleCorrectBtn, interactiveTitleProposeResults,
    interactiveLanguage, I18N[uiLanguage].interactiveCorrectNeedsTitle,
  );
});

interactiveProposeBtn.addEventListener("click", async () => {
  const t = I18N[uiLanguage];
  const w = selectedInteractiveWord();
  if (!w || !w.filled) {
    setInteractiveMessage(t.interactiveProposeNeedsWord, true);
    return;
  }
  interactiveProposeBtn.disabled = true;
  setInteractiveMessage("");
  try {
    const resp = await fetchWithTimeout(
      dictionaryDefineUrl(w.answer, w.direction), {}, DEFINE_FETCH_TIMEOUT_MS,
    );
    if (!resp.ok) throw new Error(t.interactiveProposeError);
    const data = await resp.json();
    const list = (data && data.definitions) || [];
    renderInteractiveProposals(list);
    // A successful call can still come back with zero usable definitions
    // — the LLM sometimes phrases every candidate as "<word> is a ..."
    // ("Chat est un..."), which the shared containment filter correctly
    // rejects for repeating the target word as its own subject (see
    // backend/clues.py's rule 1) — every candidate can be rejected this
    // way at once, purely by chance of phrasing, confirmed live in
    // backend.log. Without this, the button silently did nothing at all
    // in that case (renderInteractiveProposals([]) just leaves the
    // results block hidden), which is exactly the "spins, but shows no
    // result" reported directly by the user — it was
    // never actually stuck, just silently empty.
    if (!list.length) setInteractiveMessage(t.interactiveProposeEmpty, true);
  } catch (err) {
    setInteractiveMessage(t.interactiveProposeError, true);
  } finally {
    interactiveProposeBtn.disabled = false;
  }
});

// Character-level Levenshtein alignment of `before` and `after` (code
// points). Returns two flag arrays: `removed[i]` marks a character of
// `before` deleted or substituted, `added[j]` a character of `after`
// inserted or substituted — the letters "Corriger" changed.
function editDistanceDiff(before, after) {
  const a = Array.from(before);
  const b = Array.from(after);
  const n = a.length;
  const m = b.length;
  const d = [];
  for (let i = 0; i <= n; i++) {
    d.push(new Array(m + 1).fill(0));
    d[i][0] = i;
  }
  for (let j = 0; j <= m; j++) d[0][j] = j;
  for (let i = 1; i <= n; i++) {
    for (let j = 1; j <= m; j++) {
      const cost = a[i - 1] === b[j - 1] ? 0 : 1;
      d[i][j] = Math.min(d[i - 1][j] + 1, d[i][j - 1] + 1, d[i - 1][j - 1] + cost);
    }
  }
  const removed = new Array(n).fill(false);
  const added = new Array(m).fill(false);
  let i = n;
  let j = m;
  while (i > 0 || j > 0) {
    if (i > 0 && j > 0 && d[i][j] === d[i - 1][j - 1] + (a[i - 1] === b[j - 1] ? 0 : 1)) {
      if (a[i - 1] !== b[j - 1]) {
        removed[i - 1] = true;
        added[j - 1] = true;
      }
      i--;
      j--;
    } else if (i > 0 && d[i][j] === d[i - 1][j] + 1) {
      removed[i - 1] = true;
      i--;
    } else {
      added[j - 1] = true;
      j--;
    }
  }
  return { a, b, removed, added };
}

// "Corriger" result: the text before and after correction, one above the
// other, in `results` (#interactive-propose-results or #interactive-title-
// propose-results, so that row's sponge clears it too) — changed letters
// in red on the "before" line, in green on the "after" one.
function renderInteractiveCorrection(results, before, after) {
  const t = I18N[uiLanguage];
  const { a, b, removed, added } = editDistanceDiff(before, after);
  const line = (label, chars, flags, cls) => {
    const p = document.createElement("p");
    p.className = "interactive-correct-line";
    const tag = document.createElement("span");
    tag.className = "interactive-correct-label";
    tag.textContent = label;
    p.appendChild(tag);
    const body = document.createElement("span");
    body.className = "interactive-correct-text";
    chars.forEach((ch, k) => {
      if (flags[k]) {
        const s = document.createElement("span");
        s.className = cls;
        s.textContent = ch;
        body.appendChild(s);
      } else {
        body.appendChild(document.createTextNode(ch));
      }
    });
    p.appendChild(body);
    return p;
  };
  results.innerHTML = "";
  const box = document.createElement("div");
  box.className = "interactive-correct-diff";
  box.appendChild(line(t.interactiveCorrectBefore, a, removed, "interactive-correct-removed"));
  box.appendChild(line(t.interactiveCorrectAfter, b, added, "interactive-correct-added"));
  results.appendChild(box);
  results.hidden = false;
}

// "Effacer": resets #interactive-propose-results without re-running any
// request, at the user's explicit request — reuses renderInteractive
// Proposals([]) (an empty list already hides+empties the container, see
// renderInteractivePickList) rather than touching it directly, so this stays
// in sync with however that rendering is built.
interactiveProposeClearBtn.addEventListener("click", () => {
  renderInteractiveProposals([]);
});

// Shared by "Impossibles" and "Vérifier" — POST /api/interactive/impossible
// for the CURRENT grid (read-only, no mutation, no cleanup) and update
// interactiveImpossibleCells/interactiveLowCells from the response (see
// setInteractiveDiagnostics). Returns the impossible-cells Set on
// success — "Vérifier" also uses it to tell whether a given complete
// word's own
// cells are entirely covered by it (see _interactive_fill_diagnostics's own
// `_invalid_fully_known_indices` check: that's exactly what flags a
// complete-but-unknown-to-the-dictionary word as impossible — except a
// "Mots Défi" word, sent here as `challenge_words` and considered part of
// the dictionary for this check, at the user's explicit request) — or
// `null` on failure, with the error already reported via
// setInteractiveMessage.
async function fetchInteractiveImpossible(t) {
  try {
    const wireGrid = interactiveGrid.map((row) => row.map((ch) => (ch === "" ? "." : ch)));
    const resp = await fetchWithTimeout("/api/interactive/impossible", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        job_id: interactiveJobId, grid: wireGrid,
        challenge_words: interactiveChallengeWords,
      }),
    }, FETCH_TIMEOUT_MS);
    if (resp.status === 404) {
      setInteractiveMessage(t.interactiveSessionLost, true);
      return null;
    }
    const data = await resp.json();
    if (!resp.ok) {
      setInteractiveMessage(describeErrorCode(t, data.detail && data.detail.code, data.detail), true);
      return null;
    }
    setInteractiveDiagnostics(data);
    return interactiveImpossibleCells;
  } catch (err) {
    setInteractiveMessage(t.errorConnectionLost, true);
    return null;
  }
}

// "Stats" button — POST /api/interactive/stats for the CURRENT grid
// (read-only, no mutation) and store the suggested letters into
// interactiveStatLetters (see its own docstring). Returns the backend's
// `letters` array (`[r, c, letter]` triples) on success, or `null` on
// failure, with the error already reported via setInteractiveMessage.
async function fetchInteractiveStats(t) {
  try {
    const wireGrid = interactiveGrid.map((row) => row.map((ch) => (ch === "" ? "." : ch)));
    const resp = await fetchWithTimeout("/api/interactive/stats", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ job_id: interactiveJobId, grid: wireGrid }),
    }, FETCH_TIMEOUT_MS);
    if (resp.status === 404) {
      setInteractiveMessage(t.interactiveSessionLost, true);
      return null;
    }
    const data = await resp.json();
    if (!resp.ok) {
      setInteractiveMessage(describeErrorCode(t, data.detail && data.detail.code, data.detail), true);
      return null;
    }
    const letters = data.letters || [];
    // The grid may have changed while this request was in flight: its
    // letters would then describe a state no longer on screen.
    if (JSON.stringify(wireGrid) !== JSON.stringify(
      interactiveGrid.map((row) => row.map((ch) => (ch === "" ? "." : ch))),
    )) return null;
    interactiveStatLetters = new Map(letters.map(([r, c, letter]) => [`${r},${c}`, letter]));
    interactiveStatsGridKey = JSON.stringify(wireGrid);
    return letters;
  } catch (err) {
    setInteractiveMessage(t.errorConnectionLost, true);
    return null;
  }
}

// Called by renderInteractive(): while "Stats" is on, refetch the letters
// once the grid on screen is no longer the one they were computed for.
// Debounced so a burst of edits (typing a word) costs one request; silent
// (no status message) since it runs on every change, not on a click.
function scheduleInteractiveStatsRefresh() {
  if (!interactiveStatsOn || !interactiveMode || !interactiveJobId) return;
  const key = JSON.stringify(
    interactiveGrid.map((row) => row.map((ch) => (ch === "" ? "." : ch))),
  );
  if (key === interactiveStatsGridKey) return;
  clearTimeout(interactiveStatsTimer);
  interactiveStatsTimer = setTimeout(async () => {
    const letters = await fetchInteractiveStats(I18N[uiLanguage]);
    if (letters) renderInteractive();
  }, INTERACTIVE_STATS_DEBOUNCE_MS);
}

// "Stats": a two-state toggle, on by default (see interactiveStatsOn),
// showing in light gray the single most probable letter of every
// still-empty cell — the crossed across/down tally the automatic search
// itself reads to pick its most constrained cell. Turning it on fetches
// the letters right away and reports how many were found; turning it off
// hides them. Placed just before "Impossibles" (see index.html).
interactiveStatsBtn.addEventListener("click", async () => {
  const t = I18N[uiLanguage];
  interactiveStatsOn = !interactiveStatsOn;
  interactiveStatsBtn.classList.toggle("active", interactiveStatsOn);
  setInteractiveMessage("");
  clearTimeout(interactiveStatsTimer);
  if (!interactiveStatsOn) {
    interactiveStatLetters = new Map();
    interactiveStatsGridKey = null;
    renderInteractive();
    return;
  }
  interactiveStatsBtn.disabled = true;
  try {
    const letters = await fetchInteractiveStats(t);
    if (!letters) return;
    renderInteractive();
    setInteractiveMessage(
      letters.length ? t.interactiveStatsSummary(letters.length) : t.interactiveStatsNone,
      false,
    );
  } finally {
    interactiveStatsBtn.disabled = false;
  }
});

// How many interactiveSlots() are THEMSELVES genuinely flagged in
// `cellSet` — used to summarize the impossible/low-candidate cell sets
// (backend's own per-cell unit) as a word/emplacement count for the
// "Impossibles" button's own status message. Requires EVERY cell of the
// slot to be in `cellSet`, not just one — matching exactly how the
// backend builds these sets (`_interactive_fill_diagnostics`:
// `impossible.update(cells)`/`low.update(cells)` always adds the WHOLE
// cell list of the one slot that is itself flagged, never a single
// marker cell). A `.some(...)` version (this function's own original
// implementation) massively overcounted: a slot merely CROSSING one cell
// of a genuinely impossible/low slot in the other direction would get
// counted too, even though that crossing slot can otherwise be perfectly
// fillable — reported directly by the user ("the 'Impossibles' button
// gives a message '22 impossible slots', but only shows 4 impossible
// ones (red)"): a single long impossible down-slot already
// contributes one cell to up to `length` separate, otherwise-healthy
// across-slots, each wrongly counted as "impossible" on top of the one
// slot that actually is. `.every()` is exactly the same check "Vérifier"
// already uses to flag a completed word as invalid (see its own
// `isInvalidWord` computation) — this brings the summary count in line
// with that established, correct convention instead of inventing a
// second, looser one.
function countInteractiveFlaggedSlots(cellSet) {
  if (!cellSet.size) return 0;
  let count = 0;
  for (const s of interactiveSlots()) {
    if (s.cells.every(({ row, col }) => cellSet.has(`${row},${col}`))) count++;
  }
  return count;
}

// "Impossibles": a read-only check of impossible slots (including a
// fully placed word absent from the dictionary — see POST /api/
// interactive/impossible) and of ones with too few options, without
// cleaning up or checking definitions — at the user's explicit request.
interactiveImpossibleBtn.addEventListener("click", async () => {
  const t = I18N[uiLanguage];
  interactiveImpossibleBtn.disabled = true;
  interactiveVerifyBtn.disabled = true;
  setInteractiveMessage("");
  try {
    const impossibleCells = await fetchInteractiveImpossible(t);
    if (!impossibleCells) return;
    // "Impossibles" is a read-only check of impossible/low-option cells
    // only — it must not leave a stale "Vérifier" overlay (invalid/
    // missing-definition highlight + its report) drawn on top, reported
    // directly by the user: calling "Vérifier" then "Impossibles" kept
    // showing the earlier missing-definition highlight instead of
    // resetting to just impossible/low cells.
    interactiveInvalidCells = new Set();
    interactiveVerifyReport = [];
    renderInteractive();
    const impossibleCount = countInteractiveFlaggedSlots(interactiveImpossibleCells);
    const lowCount = countInteractiveFlaggedSlots(interactiveLowCells);
    if (!impossibleCount && !lowCount) {
      setInteractiveMessage(t.interactiveImpossibleNone, false);
    } else {
      setInteractiveMessage(t.interactiveImpossibleSummary(impossibleCount, lowCount), !!impossibleCount);
    }
  } finally {
    interactiveImpossibleBtn.disabled = false;
    interactiveVerifyBtn.disabled = false;
  }
});

// Checks the WHOLE grid at once, at the user's explicit request: "check
// the whole grid and highlight in red the complete words that have a
// problem, either because they aren't dictionary words, or because they
// have no definition." Supersedes an earlier version that
// only ever looked at the currently selected word (see the two prior bug
// reports this project's own history already documents for that narrower
// design). Now built directly on top of "Impossibles"'s own check (see
// fetchInteractiveImpossible) instead of its own separate dictionary-only
// round trip — at the user's explicit request: "Vérifier adds to that
// [Impossibles] the check that complete words aren't missing a
// definition" — the missing-definition half still needs no round
// trip at all, since interactiveDefs already lives client-side.
interactiveVerifyBtn.addEventListener("click", async () => {
  const t = I18N[uiLanguage];
  const filled = interactiveSlots().filter((s) => s.filled);
  if (!filled.length) {
    interactiveInvalidCells = new Set();
    interactiveVerifyReport = [];
    renderInteractive();
    setInteractiveMessage(t.interactiveVerifyNoWords, true);
    return;
  }
  interactiveVerifyBtn.disabled = true;
  interactiveImpossibleBtn.disabled = true;
  setInteractiveMessage("");
  try {
    const impossibleCells = await fetchInteractiveImpossible(t);
    if (!impossibleCells) return;
    const invalidCells = new Set();
    const report = [];
    let problemCount = 0;
    for (const s of filled) {
      const hasDef = !!(interactiveDefs.get(interactiveKey(s)) || "").trim();
      const isInvalidWord = s.cells.every(({ row, col }) => impossibleCells.has(`${row},${col}`));
      if (!hasDef || isInvalidWord) {
        problemCount++;
        for (const { row, col } of s.cells) invalidCells.add(`${row},${col}`);
        const reasons = [];
        if (isInvalidWord) reasons.push(t.interactiveVerifyReasonInvalid);
        if (!hasDef) reasons.push(t.interactiveVerifyReasonMissingDef);
        report.push({
          direction: s.direction,
          row: s.startRow,
          col: s.startCol,
          answer: s.answer,
          reasons,
        });
      }
    }
    interactiveInvalidCells = invalidCells;
    interactiveVerifyReport = report;
    renderInteractive();
    setInteractiveMessage(
      problemCount ? t.interactiveVerifyProblems(problemCount) : t.interactiveVerifyOk,
      !!problemCount,
    );
  } finally {
    interactiveVerifyBtn.disabled = false;
    interactiveImpossibleBtn.disabled = false;
  }
});

// "Effacer" (to the right of the button row): empties the display area
// right below (status message, "Vérifier" report, "Mots"/"Croisés"
// answers) without re-running any request, at the user's explicit
// request. clearInteractiveDiagnostics() also removes the matching grid
// coloring (impossible/low-option/invalid slots) and the "Vérifier"
// report — the same function already used by any grid edit for this
// same reason — then renderInteractive() applies all of it (grid +
// #interactive-verify-report). #interactive-answers is cleared
// directly: renderInteractive() no longer does it (see its own
// comment — "Mots"/"Croisés" are deliberately left displayed across
// renders so several answers can be compared).
interactiveResultsClearBtn.addEventListener("click", () => {
  setInteractiveMessage("");
  interactiveAnswers.hidden = true;
  interactiveAnswers.innerHTML = "";
  clearInteractiveDiagnostics();
  renderInteractive();
});

// "Définitions": automatically generates a definition for every fully
// filled and valid word that doesn't have one yet, at the user's
// explicit request. Reuses POST /api/interactive/verify (to discard
// words absent from the dictionary — "and valid") then, word by word,
// GET /api/dictionary/define (like "Proposer"), keeping the first
// proposed definition. Sequential: each call is a real LLM round trip,
// potentially slow — progress is shown and the whole thing is
// best-effort (a word whose generation fails is simply left with no
// definition).
interactiveDefinitionsBtn.addEventListener("click", async () => {
  const t = I18N[uiLanguage];
  const filled = interactiveSlots().filter((s) => s.filled);
  const pending = filled.filter(
    (s) => !(interactiveDefs.get(interactiveKey(s)) || "").trim(),
  );
  if (!pending.length) {
    setInteractiveMessage(t.interactiveDefinitionsNothing, false);
    return;
  }
  const busyBtns = [
    interactiveDefinitionsBtn,
    interactiveProposeBtn,
    interactiveCorrectBtn,
    interactiveImpossibleBtn,
    interactiveVerifyBtn,
    interactiveCleanBtn,
    interactiveCleanDeepBtn,
  ];
  for (const b of busyBtns) b.disabled = true;
  setInteractiveMessage(t.interactiveDefinitionsWorking(0, pending.length));
  try {
    // Keep only words that really exist in the dictionary — the same
    // batched check as "Vérifier". If it can't be reached, fall through
    // and try to define every pending word anyway (best-effort).
    let targets = pending;
    try {
      const resp = await fetchWithTimeout("/api/interactive/verify", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          job_id: interactiveJobId,
          words: pending.map((s) => ({ answer: s.answer, direction: s.direction })),
          challenge_words: interactiveChallengeWords,
        }),
      }, FETCH_TIMEOUT_MS);
      if (resp.status === 404) {
        setInteractiveMessage(t.interactiveSessionLost, true);
        return;
      }
      if (resp.ok) {
        const data = await resp.json();
        const invalid = new Set(data.invalid_words || []);
        targets = pending.filter((s) => !invalid.has(s.answer));
      }
    } catch (err) {
      /* verify unreachable — proceed with every pending word */
    }
    if (!targets.length) {
      setInteractiveMessage(t.interactiveDefinitionsNothing, false);
      return;
    }
    let done = 0;
    let failed = 0;
    for (const s of targets) {
      setInteractiveMessage(t.interactiveDefinitionsWorking(done, targets.length));
      try {
        const resp = await fetchWithTimeout(
          dictionaryDefineUrl(s.answer, s.direction), {}, DEFINE_FETCH_TIMEOUT_MS,
        );
        const data = resp.ok ? await resp.json() : null;
        const first = ((data && data.definitions) || [])
          .map((d) => (d || "").trim())
          .find(Boolean);
        if (first) {
          interactiveDefs.set(interactiveKey(s), first);
          done++;
        } else {
          failed++;
        }
      } catch (err) {
        failed++;
      }
    }
    renderInteractive();
    setInteractiveMessage(
      failed
        ? t.interactiveDefinitionsPartial(done, failed)
        : t.interactiveDefinitionsDone(done),
      failed > 0 && done === 0,
    );
  } finally {
    for (const b of busyBtns) b.disabled = false;
  }
});

// Shared by "Finir la grille" and "Finir la zone" (see both click
// handlers below) — the two only differ in whether `zoneCells` is `null`
// (whole grid, "Finir la grille") or the current drag-selected zone's own
// cell list ("Finir la zone", at the user's explicit request: "locking
// every slot that isn't part of the selection"). Reuses
// runGeneration() (the same mechanism as "Continuer"): leaves Interactive
// mode and shows the exact same attempt-preview grids / final grid as an
// ordinary automatic generation. See POST /api/interactive/finish
// (backend/app.py) for the letter-locking (and, for "Finir la zone", the
// out-of-zone freezing) and the reuse of already-typed definitions.
async function runInteractiveFinish(zoneCells) {
  if (!interactiveMode || !interactiveJobId) return;
  // Captured now, while Interactive mode is still active — interactiveGrid
  // itself is never wiped by hideInteractivePanel()/runGeneration() anyway,
  // but pin down the "before" state unambiguously before anything else
  // happens. `zoneCells` (null for "Finir la grille") makes every cell
  // outside the selection green-framed too, not just already-lettered
  // ones — see computeFinishLockedCells()'s own docstring.
  finishLockedCells = computeFinishLockedCells(zoneCells);
  // Close the Dictionnaire/Paraphraseur RIGHT NOW, at the user's explicit
  // correction: "It must no longer be visible during the following fill
  // phase" — closing them only once the whole job is done
  // (see runGeneration()'s own resumeInteractiveWork() reopen below) would
  // leave either panel sitting open, showing stale content, for the entire
  // duration of the search/optimization/clue-writing phase that's about to
  // start — runGeneration()'s own hideInteractivePanel() call hides the
  // interactive EDITING controls but never touches these two panels at
  // all, so nothing else was ever going to close them this early. Once the
  // fill finishes and Édition mode reopens, enterInteractiveMode()'s own
  // auto-open policy brings the Dictionnaire back (fresh, no longer
  // stale); the Paraphraseur, which that policy never manages, simply
  // stays closed until the player reopens it by hand.
  dictionaryPanel.hidden = true;
  paraphrasePanel.hidden = true;
  await runGeneration(async (t) => {
    const wireGrid = interactiveGrid.map((row) => row.map((ch) => (ch === "" ? "." : ch)));
    // #mode's own current value is "interactive" as long as this button is
    // even visible (that's how the player got into this mode in the first
    // place) — never a real budget mode POST /api/interactive/finish
    // accepts (BUDGET_MODES has no "interactive" entry). Forward it only
    // when the player has since changed it to a genuine budget mode;
    // otherwise fall back to "medium", matching the backend's own default
    // for this exact field.
    const rawMode = document.getElementById("mode").value;
    const finishMode = rawMode === "interactive" ? "medium" : rawMode;
    let response;
    try {
      response = await fetchWithTimeout("/api/interactive/finish", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          job_id: interactiveJobId,
          grid: wireGrid,
          definitions: interactiveDefinitionsPayload(),
          mode: finishMode,
          black_enrichment_percent: Number(blackEnrichmentInput.value),
          force_letters_percent: Number(document.getElementById("force-letters").value),
          pseudo: userPseudo || undefined,
          zone_cells: zoneCells || undefined,
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
}

// "Finir la zone": to the left of "Finir la grille", at the user's
// explicit request — the same mechanism, but restricted to the zone
// currently selected by click-drag (interactiveZoneSelection).
// Refuses to start as long as no zone is selected.
interactiveFinishZoneBtn.addEventListener("click", async () => {
  const t = I18N[uiLanguage];
  if (!interactiveZoneSelection || !interactiveZoneSelection.size) {
    setInteractiveMessage(t.interactiveFinishZoneNeedsSelection, true);
    return;
  }
  const zoneCells = [...interactiveZoneSelection].map((key) => key.split(",").map(Number));
  await runInteractiveFinish(zoneCells);
});

// "Finir la grille": permanently locks every letter already placed and
// starts a brand-new automatic generation from that state — see
// runInteractiveFinish() above for the shared mechanism.
interactiveFinishBtn.addEventListener("click", async () => {
  await runInteractiveFinish(null);
});

interactiveTitleProposeBtn.addEventListener("click", async () => {
  interactiveTitleProposed = false;
  interactiveTitleProposeBtn.disabled = true;
  setInteractiveMessage("");
  try {
    // Never touches the currently-typed/picked title (autoFill=false) —
    // just shows the 10-proposal list below, same "look, then pick"
    // interaction as "Proposer une définition".
    await proposeInteractiveTitle(false);
  } finally {
    interactiveTitleProposeBtn.disabled = false;
  }
});

// "Effacer": resets #interactive-title-propose-results without
// re-running any request, at the user's explicit request — the same
// principle as interactiveProposeClearBtn above, for the list of
// proposed titles this time.
interactiveTitleProposeClearBtn.addEventListener("click", () => {
  renderInteractiveTitleProposals([]);
});

// "Sauvegarder" — writes the whole current grid + definitions + title to
// GRID_WORK (the "Créations" draft) WITHOUT publishing to the Library, at
// the user's explicit request. Same POST /api/interactive/save_work call
// as the autosave, but visible: it reports success/failure (the autosave
// is deliberately silent) and, unlike "Publier", never leaves interactive
// mode.
interactiveDraftSaveBtn.addEventListener("click", async () => {
  const t = I18N[uiLanguage];
  if (!interactiveMode || !interactiveJobId) return;
  interactiveDraftSaveBtn.disabled = true;
  setInteractiveMessage("");
  try {
    const wireGrid = interactiveGrid.map((row) => row.map((ch) => (ch === "" ? "." : ch)));
    const resp = await fetchWithTimeout("/api/interactive/save_work", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        job_id: interactiveJobId,
        grid: wireGrid,
        definitions: interactiveDefinitionsPayload(),
        title: interactiveTitleInput.value.trim(),
        pseudo: userPseudo || undefined,
        challenge_words: interactiveChallengeWords,
        diagnostics: interactiveDiagnosticsPayload(),
      }),
    }, FETCH_TIMEOUT_MS);
    if (resp.status === 404) {
      setInteractiveMessage(t.interactiveSessionLost, true);
      return;
    }
    setInteractiveMessage(resp.ok ? t.interactiveDraftSaved : t.interactiveSaveError, !resp.ok);
  } catch (err) {
    setInteractiveMessage(t.errorConnectionLost, true);
  } finally {
    interactiveDraftSaveBtn.disabled = false;
  }
});

interactiveSaveBtn.addEventListener("click", async () => {
  const t = I18N[uiLanguage];
  interactiveSaveBtn.disabled = true;
  interactiveSaveResult.textContent = "";
  try {
    const slots = interactiveSlots();
    const definitions = slots.map((s) => ({
      row: s.startRow,
      col: s.startCol,
      direction: s.direction,
      clue: interactiveDefs.get(interactiveKey(s)) || "",
    }));
    const wireGrid = interactiveGrid.map((row) => row.map((ch) => (ch === "" ? "." : ch)));
    const resp = await fetchWithTimeout("/api/interactive/save", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        job_id: interactiveJobId,
        grid: wireGrid,
        definitions,
        title: interactiveTitleInput.value.trim(),
        language: interactiveLanguage,
        bilingual_language: interactiveBilingualLanguage || undefined,
        difficulty: interactiveDifficulty,
        theme: interactiveTheme || undefined,
        pseudo: userPseudo || undefined,
        // Without this, "Publier" never told the backend about the
        // session's current "Mots Défi" list at all — the endpoint used
        // to fall back to a stale job["interactive"] snapshot that's only
        // ever populated at session start/resume, never kept in sync with
        // edits made mid-session (see InteractiveSaveRequest.challenge_
        // words' own docstring, and the identical existing convention on
        // the "Sauvegarder"/save_work call just above).
        challenge_words: interactiveChallengeWords,
        // Publishing also refreshes this session's own GRID_WORK draft,
        // so the currently-displayed diagnostics travel with it exactly
        // like the two calls above — otherwise that refresh would blank
        // them out of the draft.
        diagnostics: interactiveDiagnosticsPayload(),
      }),
    }, FETCH_TIMEOUT_MS);
    const data = await resp.json();
    if (resp.ok && data.grid_id) {
      markGridSeen(data.grid_id);
      interactiveSaveResult.textContent = t.interactiveSaved;
      setStatus(t.interactiveSaved, false);
      exitInteractiveMode();
    } else {
      setInteractiveMessage(t.interactiveSaveError, true);
    }
  } catch (err) {
    setInteractiveMessage(t.interactiveSaveError, true);
  } finally {
    interactiveSaveBtn.disabled = false;
  }
});

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
  recomputeBtn.hidden = true;
  // Shown again in case a *previous* generation's own result already
  // hid it (see below) — the player can still change their mind about
  // seeing preview letters for this new run, right from the start.
  attemptPreviewRevealBtn.hidden = false;
  stopBtn.hidden = false;
  stopBtn.disabled = false;
  successMedalCount.textContent = "0";
  successMedal.hidden = false;
  // Hidden on every fresh attempt (a new form submission or a "Continuer"
  // click alike) — only shown again if *this* run itself ends in the
  // specific "no_fillable_grid" failure the button exists for (see the
  // catch block below).
  continueBtn.hidden = true;
  currentJobId = null;
  setStatus(t.statusGenerating, false);
  hideAttemptPreview();
  hideInteractivePanel();

  try {
    const jobId = await startJob(t);
    currentJobId = jobId;
    const gridData = await pollJob(jobId, t);
    if (gridData.grid_work_id) {
      // "Finir la grille" (see POST /api/interactive/finish) never
      // publishes to the Bibliothèque — the finished grid is a brand-new
      // "Créations" draft instead (backend/app.py's _run_generate_job,
      // `publish=False`), reopened directly in Édition mode here, at the
      // user's explicit request: "don't publish the grid. Add the new
      // version to the author's Créations. Reopen the grid automatically
      // in edit mode." Reuses the exact same
      // POST /api/interactive/resume mechanism the "Créations" panel
      // itself already uses (resumeInteractiveWork()) — its own call to
      // runInteractive() handles every bit of UI setup/teardown (hiding
      // #result, the "Stop" button, enterInteractiveMode(), the final
      // status text) on its own, so nothing further is needed here. The
      // Dictionnaire panel this reopen brings back is opened automatically
      // by enterInteractiveMode() itself (its own long-standing auto-open
      // policy for Édition mode) — an earlier version forced it to stay
      // closed here instead, but the user explicitly corrected that: the
      // return to Édition mode should reopen it like any other entry into
      // this mode. The Paraphraseur is unaffected by any of this — it was
      // already force-closed the moment "Finir la grille"/"Finir la zone"
      // was clicked (see runInteractiveFinish()), and stays that way until
      // the player reopens it by hand.
      await resumeInteractiveWork(gridData.grid_work_id);
    } else {
      displayFinalGrid(gridData);
      setStatus(t.statusGenerated, false);
    }
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
    successMedal.hidden = true;
    currentJobId = null;
    generationInProgress = false;
    syncRssPanelVisibility();
  }
}

form.addEventListener("submit", async (event) => {
  event.preventDefault();

  // Close the Dictionnaire panel if it's open, at the user's explicit
  // request — typically still open from Édition mode (which auto-opens
  // it, see enterInteractiveMode()) when the player relaunches an
  // automatic generation with the same settings straight from there; its
  // content would otherwise sit stale once the interactive session it was
  // opened for is gone. Applies to every submission, not just this one
  // path — nothing calls hideDictionaryPanel() elsewhere in either
  // generation branch below.
  if (!dictionaryPanel.hidden) hideDictionaryPanel();

  const language = languageSelect.value;
  // Bilingual grid, at the user's explicit request: omitted
  // (`undefined`, so absent from the sent JSON) when the "Bilingue"
  // selector stayed identical to the primary language — the backend
  // (GenerateRequest.bilingual_language) already treats `None`/an
  // identical value as "ordinary monolingual grid" regardless, but there's
  // no reason to send a field with no real effect.
  const bilingualLanguage = bilingualLanguageSelect.value;
  // Off localhost, width/height are capped at REMOTE_MAX_DIMENSION (see
  // restrictOptionsToLocalhost above) — clamp here too, as a last line
  // of defense in case a value slipped past the input's own max/change
  // handler.
  const dimCap = isLocalhostOrigin() ? Infinity : REMOTE_MAX_DIMENSION;
  const width = Math.min(Number(widthInput.value), dimCap);
  const height = Math.min(Number(heightInput.value), dimCap);
  const difficulty = document.getElementById("difficulty").value;
  const mode = document.getElementById("mode").value;
  const blackEnrichmentPercent = Number(blackEnrichmentInput.value);
  const forceLettersPercent = Number(document.getElementById("force-letters").value);
  // Optional Thématique (word list, "+/-" chip list — see themeKeywords/
  // renderThemeList() above) — joined back into one space-separated
  // string, omitted if empty. Whatever is still pending, untyped-in
  // `themeInput` (never clicked "+"/hit Enter/typed a punctuation) is not
  // included, same convention as "Mots Défi (personnalisation)" above.
  const theme = themeKeywords.join(" ");
  // "Précision thématique": the theme glossary's own minimal Qdrant
  // similarity threshold (see backend/app.py's THEME_MIN_SCORE). Dot
  // forced as the decimal separator, clamped to [0,1]; undefined if
  // empty -> the backend applies its own default value.
  const themePrecision = readThemePrecision();

  if (mode === "interactive") {
    interactiveLanguage = language;
    // Same "omit rather than send a no-op identical value" convention as
    // the automatic-generation request just above — GenerateRequest
    // (reused as-is by POST /api/interactive/start) already treats
    // None/an identical value as "monolingual session".
    interactiveBilingualLanguage = bilingualLanguage !== language ? bilingualLanguage : "";
    interactiveDifficulty = difficulty;
    interactiveTheme = theme;
    await runInteractive({
      language, width, height, difficulty,
      bilingual_language: interactiveBilingualLanguage || undefined,
      black_enrichment_percent: blackEnrichmentPercent,
      force_letters_percent: forceLettersPercent,
      theme: theme || undefined,
      theme_precision: themePrecision,
      pseudo: userPseudo || undefined,
      // "Mots Défi (personnalisation)" typed on this same main form
      // (#generate-challenge-panel) before switching to "Interactif" —
      // same convention as the automatic-generation POST /api/generate
      // body just below (omitted rather than an empty array when
      // untouched), reused as-is by POST /api/interactive/start's own
      // GenerateRequest.challenge_words.
      challenge_words: generateChallengeWords.length ? generateChallengeWords : undefined,
    });
    return;
  }

  // A genuinely fresh, unrelated generation — never keep a previous
  // "Finir la grille" run's own light-green preview highlight around for
  // this one (see finishLockedCells above). "Continuer" deliberately
  // never resets this, since it resumes THIS SAME job, whatever started
  // it.
  finishLockedCells = null;
  await runGeneration(async (t) => {
    let response;
    try {
      response = await fetchWithTimeout("/api/generate", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          language, width, height, difficulty, mode,
          bilingual_language: bilingualLanguage !== language ? bilingualLanguage : undefined,
          black_enrichment_percent: blackEnrichmentPercent,
          force_letters_percent: forceLettersPercent,
          // Thématique: a word list semantically steering the grid
          // (a backend-side Qdrant pre-search — see backend/app.py's
          // THEME_PRESEARCH_LIMIT). Omitted if empty.
          theme: theme || undefined,
          // The theme glossary's own similarity threshold (see
          // backend/app.py's THEME_MIN_SCORE) — omitted if the field is
          // empty.
          theme_precision: themePrecision,
          // The author's pseudo, stored in the grid's own JSON (see
          // backend/grid_store.py's save_grid_json) — omitted if empty.
          pseudo: userPseudo || undefined,
          // "Mots Défi (personnalisation)" — see #generate-challenge-panel
          // above and backend/app.py's GenerateRequest.challenge_words.
          // Omitted (rather than an empty array) on the same "no-op ->
          // no field" convention as `theme`/`pseudo` above.
          challenge_words: generateChallengeWords.length ? generateChallengeWords : undefined,
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

// "Recalculer" button, at the user's explicit request: re-runs only clue
// generation for the currently displayed grid, saves it as a brand new
// library copy whose title gets a bumped "(Vn)" marker ("Graines" ->
// "Graines (V2)" -> "Graines (V3)"; the original library record is left
// untouched), and swaps the displayed grid for the new copy. The backend
// job (POST /api/recompute → _run_recompute_job)
// reuses the same JOBS registry as a generation, so pollJob() and
// displayFinalGrid() work unchanged — only the HTTP call that starts the
// job differs from runGeneration()'s own flow, and none of the
// generation-specific UI (stop/continue buttons, preview reveal toggle)
// applies here, so this deliberately does not reuse runGeneration().
recomputeBtn.addEventListener("click", async () => {
  if (!puzzle || !puzzle.id || recomputeBtn.disabled || generationInProgress) return;
  const t = I18N[uiLanguage];
  const gridId = puzzle.id;

  recomputeBtn.disabled = true;
  generationInProgress = true;
  // Wipe any stale attempt-preview history left over from an earlier
  // generation (displayFinalGrid()/library loads don't clear it), the
  // same way runGeneration() does at the start of a fresh run — pollJob()
  // below records the recompute's own "final grid" preview into it.
  hideAttemptPreview();
  setStatus(t.statusRecomputing, false);

  try {
    let response;
    try {
      response = await fetchWithTimeout("/api/recompute", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ grid_id: gridId }),
      }, FETCH_TIMEOUT_MS);
    } catch (err) {
      throw new Error(t.errorConnectionLost);
    }
    const data = await response.json();
    if (!response.ok) {
      throw new Error(describeErrorCode(t, data.detail && data.detail.code, data.detail));
    }
    currentJobId = data.job_id;
    const newGrid = await pollJob(data.job_id, t);
    displayFinalGrid(newGrid);
    setStatus(t.statusRecomputed, false);
  } catch (err) {
    setStatus(err.message, !(err instanceof CancelledError));
  } finally {
    currentJobId = null;
    generationInProgress = false;
    // displayFinalGrid() re-enables recomputeBtn on the success path; on
    // an error path the grid stays as it was, so re-enable it here too.
    recomputeBtn.disabled = false;
    syncRssPanelVisibility();
  }
});

// The Library's own shareable link: if the URL carries "?grid=<id>" (see
// SHARE_BASE_URL / the table's own "Lien" column), load that grid to
// play it as soon as the page opens. Reads the parameter regardless of
// the host (the table's own link targets the public domain, but a
// "?grid=" tacked onto http://127.0.0.1:3000/ works just as well).
// loadLibraryGrid() handles the error on its own (unknown id -> #status).
function maybeLoadGridFromUrl() {
  let gridId = null;
  try {
    gridId = new URLSearchParams(window.location.search).get("grid");
  } catch (err) {
    gridId = null;
  }
  if (gridId) {
    loadLibraryGrid(gridId.trim());
  }
}
maybeLoadGridFromUrl();

// Démarre le battement de cœur "x en ligne" — un appel immédiat puis
// toutes les PRESENCE_INTERVAL_MS (2s). Placé en toute fin de fichier
// pour que fetchWithTimeout et toutes les constantes soient définies.
pingPresence();
setInterval(pingPresence, PRESENCE_INTERVAL_MS);
