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
  // "x en ligne" — texte paramétré (voir i18n.js), à ré-appliquer au
  // changement de langue à partir de la dernière valeur connue.
  renderOnlineCount();
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
const welcomeForm = document.getElementById("welcome-form");
const welcomeLanguageSelect = document.getElementById("welcome-language");
const welcomePseudoInput = document.getElementById("welcome-pseudo");
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
const board = document.getElementById("board");
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
const continueBtn = document.getElementById("continue-btn");
const cluesEl = document.getElementById("clues");
const downCluesSection = document.getElementById("down-clues-section");
const versionBadge = document.getElementById("version-badge");
const onlineCountEl = document.getElementById("online-count");
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
const gridTitleTextEl = document.getElementById("grid-title-text");
const gridDifficultyEl = document.getElementById("grid-difficulty");
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
// the user's explicit request: "Duplique le sélecteur de sens du clavier
// virtuel pour le mettre à droite des définitions sous la grille." Both
// pairs drive and reflect the exact same activeDirection state (see
// below) — clicking either pair's own buttons keeps all 4 in sync.
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
// request) can reach it without each re-querying the DOM.
const themeInput = document.getElementById("theme");

// "Interactif" authoring mode controls (see the Interactive-mode section
// further down).
const interactiveControls = document.getElementById("interactive-controls");
const interactivePrevBtn = document.getElementById("interactive-prev-btn");
const interactiveNextBtn = document.getElementById("interactive-next-btn");
const interactiveMessage = document.getElementById("interactive-message");
const interactiveDirAcrossBtn = document.getElementById("interactive-dir-across-btn");
const interactiveDirDownBtn = document.getElementById("interactive-dir-down-btn");
const interactiveCleanBtn = document.getElementById("interactive-clean-btn");
const interactiveCleanDeepBtn = document.getElementById("interactive-clean-deep-btn");
const interactiveDefinitionInput = document.getElementById("interactive-definition-input");
const interactiveProposeBtn = document.getElementById("interactive-propose-btn");
const interactiveVerifyBtn = document.getElementById("interactive-verify-btn");
const interactiveDefinitionsBtn = document.getElementById("interactive-definitions-btn");
const interactiveWordsBtn = document.getElementById("interactive-words-btn");
const interactiveWordsResults = document.getElementById("interactive-words-results");
const interactiveProposeResults = document.getElementById("interactive-propose-results");
const interactiveVerifyReportEl = document.getElementById("interactive-verify-report");
const interactiveTitleRow = document.getElementById("interactive-title-row");
const interactiveTitleInput = document.getElementById("interactive-title-input");
const interactiveTitleProposeBtn = document.getElementById("interactive-title-propose-btn");
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
  // An entry may be monolingual (item.language is a string) or
  // multilingual (item.language is an array of language codes, e.g.
  // WordsCroisés which is EN+FR and must show under both filters).
  const itemMatchesLang = (item, lang) => Array.isArray(item.language)
    ? item.language.includes(lang)
    : item.language === lang;
  let filteredItems = filterLang === "all"
    ? rssItems
    : rssItems.filter((item) => itemMatchesLang(item, filterLang));
  // Repli sur l'anglais si la langue choisie n'a aucun article, à la
  // demande explicite de l'utilisateur ("afficher la liste anglaise" +
  // un message l'expliquant en haut de la liste) — jamais quand la langue
  // choisie est déjà "en" ou "all" (le repli lui-même, ou déjà tout
  // montré), pour ne jamais boucler sur lui-même ni masquer un "aucun
  // article" réellement global (tout repli anglais lui-même vide).
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
    // Ligne sous le titre : pour une entrée RSS, c'est le nom du flux
    // (information complémentaire — de quel blog vient l'article). Pour
    // une entrée "grid" (lien direct), le nom de la source est déjà dans
    // le titre lui-même (ex. "Fox News – Saturday…") : cette ligne serait
    // redondante — à la demande explicite de l'utilisateur, on y affiche
    // à la place l'URL réellement ouverte au clic.
    const source = document.createElement("div");
    source.className = isGrid ? "rss-item-source rss-item-url" : "rss-item-source";
    source.textContent = isGrid ? item.link : item.source;
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

// Declared here (not with the rest of the "Interactif" state block below)
// because syncRssPanelVisibility() reads it and is called at top level,
// long before that block — a `let` referenced before its declaration is a
// temporal-dead-zone ReferenceError that would halt the rest of the script.
let interactiveMode = false;

function syncRssPanelVisibility() {
  rssPanel.hidden = generationInProgress || interactiveMode
    || !(libraryPanel.hidden && dictionaryPanel.hidden && qdrantAdminPanel.hidden
         && interactiveWorkPanel.hidden && attemptPreview.hidden && result.hidden);
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

// The single, shared "which direction is currently active" state, at the
// user's explicit request: the virtual keyboard's own direction buttons
// ("un clavier virtuel... plus une flèche vers le bas pour configurer le
// sens vertical... et une flèche vers la droite pour configurer le sens
// horizontalement") were duplicated next to "Verticalement" under the
// grid, and both pairs, plus Shift/CapsLock, now drive and reflect this
// one variable — a persistent mode (CAPS-LOCK-like, not SHIFT-held: a
// clicked button can't really be "held down") rather than a per-key
// state, exactly like the virtual keyboard's own original design. Grid
// hover (see the mouseenter listener in renderGrid()) reads this
// directly too, rather than the live modifier state of the mouseenter
// event alone, so hovering the grid always respects whichever direction
// the player last chose — by clicking a button or via Shift/CapsLock —
// rather than the mouse-move event's own momentary key state overriding
// it: "La sélection dans la grille (mouse over) doit s'adapter aux
// sélecteurs de sens."
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
let interactiveHasTheme = false;
let interactiveLanguage = "fr";
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
// "row,col" keys for the backend-computed fill diagnostics of the LAST
// auto-placement (start / "Suivant") — impossible slots (no candidate word
// left at all) and slots below the fill-option threshold — shown in
// renderGrid() the same way the attempt previews do. Snapshots: cleared on
// any manual edit (they only describe the state the backend last saw).
let interactiveImpossibleCells = new Set();
let interactiveLowCells = new Set();
// "Vérifier" button: cells of every complete word flagged as a problem —
// either not a real dictionary word (checked server-side, POST /api/
// interactive/verify) or missing a definition (checked client-side against
// interactiveDefs) — at the user's explicit request: "vérifier toute la
// grille et mettre en rouge les mots complets qui posent un problème."
// Same staleness rule as interactiveImpossibleCells/LowCells above: cleared
// on any manual edit, since it describes a check run against a past grid
// state that may no longer be accurate.
let interactiveInvalidCells = new Set();
// Same "Vérifier" check as above, but the detailed, one-line-per-word
// report shown below the definition input, at the user's explicit
// request: "un rapport indiquant les problèmes rencontrés sur chaque
// mot." Each entry is `{direction, row, col, answer, reasons}` — only
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
// Fait suivre le sélecteur de langue du panneau "Dictionnaire" à la
// langue du mot survolé/sélectionné dans la grille à jouer, à la demande
// explicite de l'utilisateur : "le sélecteur de langue du dictionnaire
// doit s'adapter automatiquement à la langue suivant le sens de
// sélection de la grille." Utilise les langues réellement enregistrées
// sur la grille elle-même (`puzzle.language`/`puzzle.bilingual_language`
// — voir backend/crossword_gen.py's generate_grid), pas les sélecteurs
// du formulaire de génération : le joueur a pu les changer après avoir
// généré/chargé cette grille précise, donc seule la grille elle-même
// sait dans quelle(s) langue(s) elle a réellement été écrite. Sans effet
// sur une grille monolingue ordinaire (`puzzle.bilingual_language` alors
// `null`/absent) : `direction === "down"` retombe simplement sur la même
// langue primaire que "across".
function updateDictionaryLanguageForDirection(direction) {
  if (!puzzle) return;
  const lang = (direction === "down" && puzzle.bilingual_language)
    ? puzzle.bilingual_language
    : (puzzle.language || languageSelect.value);
  if (lang) dictionaryLanguage.value = lang;
}

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
  for (const { row, col } of wordCellsAt(selected.row, selected.col, activeDirection)) {
    if (row === selected.row && col === selected.col) continue;
    const el = cellElements.get(`${row},${col}`);
    if (el) el.classList.add("selected-word");
  }
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
  // Ignore Shift/CapsLock while typing in the chat (or any text field) —
  // otherwise typing a capital there silently changes the grid's active
  // direction and the currently-hovered word (see isTextInputFocused()).
  if (isTextInputFocused()) return;
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
  if (isTextInputFocused()) return;
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
    forced_cells: forcedCells,
    locked_cells: lockedCells,
    low_candidate_cells: lowCandidateCells,
    noise_cells: noiseCells,
    theme_cells: themeCells,
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
    // Lettres vertes pour les mots issus du glossaire thématique, à la
    // demande explicite de l'utilisateur : "Dans les grilles aperçus,
    // indiquer en lettres vertes les mots issus du glossaire thématique."
    // Toujours vide pour une génération non thématique (voir backend/
    // crossword_gen.py's `_theme_word_cells`), donc `|| []` = no-op partout
    // ailleurs, comme les recouvrements ci-dessus. Une couleur de texte,
    // qui se compose proprement avec les fonds .impossible/.noise/
    // .low-candidates et les bordures .forced/.locked déjà en place.
    for (const [r, c] of themeCells || []) {
      const cell = cellElementsByCoord.get(`${r},${c}`);
      if (cell) cell.classList.add("theme");
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
  // In interactive authoring mode ANY cell is selectable (black cells
  // included — the user edits them too); no showSolution/isWhite guard.
  if (interactiveMode) {
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
  if (isTextInputFocused()) return;
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
    // Auto-advance follows the current selection direction (activeDirection
    // — set by Ctrl, the Across/Down buttons, or Shift/Caps Lock), not just
    // the letter's own case. Typing an uppercase letter still advances down
    // (an uppercase key implies a vertical word, and Shift/Caps Lock have
    // already set activeDirection to "down" anyway) — kept as a fallback
    // for the rare case where Caps Lock was on before the grid opened, so
    // no keydown ever fired to update activeDirection.
    moveSelection(isUpper || activeDirection === "down" ? "down" : "right");
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
          cell.addEventListener("click", () => selectCell(r, c));
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

      // "Interactif" : lettre magenta pour un mot posé automatiquement
      // issu du glossaire thématique (voir interactiveThemeCells).
      if (interactiveMode && letter && interactiveThemeCells.has(`${r},${c}`)) {
        cell.classList.add("interactive-theme");
      }
      // "Interactif" : fond rouge/orange pour un emplacement impossible /
      // en dessous du seuil d'options de remplissage — même sens que sur
      // les prévisualisations (voir interactiveImpossibleCells/LowCells).
      if (interactiveMode) {
        const dk = `${r},${c}`;
        if (interactiveImpossibleCells.has(dk)) cell.classList.add("interactive-impossible");
        else if (interactiveLowCells.has(dk)) cell.classList.add("interactive-low");
        if (interactiveInvalidCells.has(dk)) cell.classList.add("interactive-invalid");
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
  // En mode "Interactif" on n'affiche jamais les listes complètes
  // Horizontalement/Verticalement, ni le bloc de définition/flèches du
  // mode jeu juste sous la grille (#hover-definition-row) — à la demande
  // explicite de l'utilisateur : "il y a deux blocs de flèches et de
  // définitions... ne pas afficher les blocs qui correspondent au mode
  // jeu... ne garder que les blocs spécifiques au mode interactif (avec
  // les boutons Proposer et Vérifier)", c'est-à-dire #interactive-arrows/
  // #interactive-definition-row dans #interactive-controls, déjà seuls
  // affichés dans ce mode.
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

// Clavier virtuel, à la demande explicite de l'utilisateur : "un clavier
// virtuel ne contenant que les 26 lettres de l'alphabet en majuscules
// dans l'ordre naturel sur 2 lignes, plus une flèche vers le bas pour
// configurer le sens vertical... et une flèche vers la droite pour
// configurer le sens horizontalement." Its own direction buttons drive
// the shared `activeDirection`/`setActiveDirection()` (declared earlier,
// alongside the hover state it's now unified with) rather than a
// dedicated variable of their own — see that declaration's own comment
// for the full "un mode persistant... plutôt qu'un état par touche"
// reasoning, unchanged from this feature's own original design.

function insertAtCursor(input, text) {
  // Insère `text` à la position du curseur (en remplaçant la sélection
  // s'il y en a une) et laisse le curseur juste après. Sur un <input
  // type="text"> ayant le focus, selectionStart/End sont toujours des
  // nombres ; le repli sur value.length ne sert que par prudence.
  const start = input.selectionStart == null ? input.value.length : input.selectionStart;
  const end = input.selectionEnd == null ? input.value.length : input.selectionEnd;
  input.value = input.value.slice(0, start) + text + input.value.slice(end);
  const pos = start + text.length;
  input.setSelectionRange(pos, pos);
  // Au cas où quelque chose écouterait "input" (aucun écouteur
  // aujourd'hui — le formulaire du dictionnaire n'agit qu'à la
  // soumission — mais sans coût et évite une surprise plus tard).
  input.dispatchEvent(new Event("input", { bubbles: true }));
}

function typeVirtualLetter(letter) {
  // Le clavier virtuel sert aussi à saisir une recherche dans le
  // dictionnaire, à la demande explicite de l'utilisateur. Quand
  // #dictionary-input a le focus, on écrit dedans (en minuscules,
  // comme une frappe physique dans ce même champ — la recherche est de
  // toute façon insensible à la casse et aux accents côté backend)
  // plutôt que dans la grille. Le focus est préservé par le
  // `mousedown`/preventDefault posé sur chaque touche (voir
  // buildVirtualKeyboard) : sans lui, le clic sur le bouton retirerait
  // le focus du champ avant même d'arriver ici.
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
  // Mêmes gardes que handleKeydown() : une lettre cliquée quand aucune
  // case n'est sélectionnée, ou que la solution est affichée, ne fait
  // rien plutôt que d'écrire dans le vide ou d'écraser la solution.
  if (!puzzle || !selected || showSolution) return;
  userLetters[selected.row][selected.col] = letter;
  // moveSelection() attend "right" pour horizontal, n'importe quelle
  // autre valeur pour vertical (voir sa propre définition) — pas les
  // mêmes libellés qu'activeDirection ("across"/"down").
  moveSelection(activeDirection === "across" ? "right" : "down");
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
      // Empêche le clic de retirer le focus d'un champ texte (le champ
      // de recherche du dictionnaire) : sans ça, typeVirtualLetter ne
      // verrait plus #dictionary-input comme document.activeElement.
      // Inoffensif pour la grille, qui ne dépend pas du focus.
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
// Comme les touches lettres : un clic sur une flèche de sens ne doit pas
// retirer le focus du champ de recherche du dictionnaire (les flèches
// n'ont aucun effet sur la saisie dictionnaire, mais un clic accidentel
// ne doit pas casser la frappe en cours). Les deux paires de boutons
// (clavier virtuel + doublon sous "Verticalement") pilotent le même
// setActiveDirection() partagé.
virtualKeyboardAcrossBtn.addEventListener("mousedown", (e) => e.preventDefault());
virtualKeyboardDownBtn.addEventListener("mousedown", (e) => e.preventDefault());
virtualKeyboardAcrossBtn.addEventListener("click", () => setActiveDirection("across"));
virtualKeyboardDownBtn.addEventListener("click", () => setActiveDirection("down"));
cluesDirectionAcrossBtn.addEventListener("mousedown", (e) => e.preventDefault());
cluesDirectionDownBtn.addEventListener("mousedown", (e) => e.preventDefault());
cluesDirectionAcrossBtn.addEventListener("click", () => setActiveDirection("across"));
cluesDirectionDownBtn.addEventListener("click", () => setActiveDirection("down"));
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

const SUPPORTED_UI_LANGS = ["fr", "en", "de", "es", "it", "pt"];
// Pseudo (nickname) chosen in the welcome overlay (see below); "" when
// none is set. Persisted in the cwf-prefs cookie, mirrored into every
// grid this browser generates (POST /api/generate `pseudo`) and into the
// "Mes grilles" library filter.
let userPseudo = "";
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

async function pingPresence() {
  try {
    const response = await fetchWithTimeout("/api/presence", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ session_id: presenceSessionId, pseudo: userPseudo || "" }),
    }, PRESENCE_TIMEOUT_MS);
    if (!response.ok) return;
    const data = await response.json();
    if (typeof data.count === "number") {
      lastOnlineCount = data.count;
      renderOnlineCount();
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
  // Grille bilingue, à la demande explicite de l'utilisateur : "le
  // premier sélecteur de langue configure la langue de l'interface, et
  // force le second sélecteur de langue à prendre la même valeur." Un
  // simple changement de la langue de l'interface annule donc toujours
  // un choix bilingue déjà fait sur ce sélecteur — le joueur doit
  // reconfigurer "Bilingue" sur une langue différente à chaque fois
  // qu'il souhaite réellement une grille bilingue, jamais que ce
  // sélecteur reste figé sur une ancienne valeur devenue incohérente
  // avec la nouvelle langue principale.
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
  // Le libellé du niveau à droite du titre de la grille jouée est traduit
  // (voir renderGridDifficulty), donc à ré-appliquer au changement de langue.
  renderGridDifficulty();
  // Le panneau du mode "Interactif" porte des libellés/messages traduits.
  if (interactiveMode) renderInteractive();
  // Pseudo de l'en-tête : le libellé "définir un pseudo" (quand aucun
  // pseudo n'est saisi) est traduit, donc à ré-appliquer.
  if (!userPseudoBtn.hidden) renderUserPseudo();
  // "David FALCON"'s own welcome bubble, at the user's explicit request
  // — see renderChatWelcome()'s own docstring for why this only ever
  // does anything before the player's first real message. Skipped on the
  // bootstrap call (see uiBootstrapped) — the standalone call near
  // renderChatWelcome()'s definition covers the initial render.
  if (uiBootstrapped) renderChatWelcome();
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
  rssLanguageFilter.value = lang;
  renderRssList();
  // Le filtre de langue de la Bibliothèque suit lui aussi la langue de
  // l'interface, à la demande explicite de l'utilisateur ("modifiée si la
  // langue de l'interface change"). Si le panneau est ouvert, on le
  // re-rend depuis la page 1.
  libraryLanguageFilter.value = lang;
  if (!libraryPanel.hidden) {
    libraryCurrentPage = 1;
    renderLibraryList();
  }
  // Le sélecteur de langue du dictionnaire suit lui aussi la langue de
  // l'interface (défaut demandé), tant que le joueur n'a rien changé
  // dessus sur ce panneau précisément.
  dictionaryLanguage.value = lang;
  // Le panneau d'admin Qdrant (localhost) : ses libellés sont posés à la
  // construction, donc on le re-rend s'il est ouvert.
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
  welcomeOverlay.hidden = false;
  welcomePseudoInput.focus();
}

welcomeForm.addEventListener("submit", (event) => {
  event.preventDefault();
  const pseudo = welcomePseudoInput.value.trim().slice(0, MAX_PSEUDO_LENGTH);
  // Le panneau ne se ferme pas si le pseudo est vide, à la demande
  // explicite de l'utilisateur. `required` bloque déjà un champ
  // strictement vide côté navigateur (l'événement "submit" ne se
  // déclenche même pas) ; ceci couvre en plus le cas "que des espaces".
  if (!pseudo) {
    welcomePseudoInput.setCustomValidity(I18N[uiLanguage].welcomePseudoRequired);
    welcomePseudoInput.reportValidity();
    return;
  }
  welcomePseudoInput.setCustomValidity("");
  userPseudo = pseudo;
  savePrefs({ accepted: true, lang: uiLanguage, pseudo: userPseudo });
  renderUserPseudo();
  welcomeOverlay.hidden = true;
  // Le pseudo peut avoir changé — si la Bibliothèque est ouverte (et
  // surtout sur le filtre "Mes grilles"), on la rafraîchit.
  if (!libraryPanel.hidden) {
    libraryCurrentPage = 1;
    renderLibraryList();
  }
  // Le pseudo vient d'être connu (première visite) ou peut avoir changé
  // (pseudo modifié plus tard) — dans les deux cas, on vérifie si ce
  // pseudo a des créations en cours dans GRID_WORK.
  checkForSavedInteractiveWork();
});

// Efface le message de validité personnalisé dès que l'utilisateur
// retape quelque chose, sinon le champ resterait marqué invalide.
welcomePseudoInput.addEventListener("input", () => {
  welcomePseudoInput.setCustomValidity("");
});

userPseudoBtn.addEventListener("click", openWelcomeOverlay);

(function initUserPrefs() {
  const prefs = loadPrefs();
  if (prefs && prefs.accepted) {
    userPseudo = typeof prefs.pseudo === "string"
      ? prefs.pseudo.slice(0, MAX_PSEUDO_LENGTH)
      : "";
    setUiLanguage(typeof prefs.lang === "string" ? prefs.lang : "fr");
    renderUserPseudo();
    checkForSavedInteractiveWork();
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

// "Déjà vues" — l'ensemble des identifiants (champ `id` d'un fichier
// GRID_STORE, voir backend/grid_store.py) des grilles que ce navigateur a
// déjà affichées, à la demande explicite de l'utilisateur ("stocker le
// nom du fichier de GRID_STORE ... pour savoir qu'il l'a déjà vue").
// Conservé en localStorage plutôt qu'un vrai cookie : après le peuplement
// automatique (Automation/Populate.py, 1000 grilles) la liste peut
// compter des milliers d'entrées — bien au-delà des ~4 Ko qu'un cookie
// encaisse, et un cookie serait renvoyé à chaque requête pour rien. La
// liste complète est passée au back dans le corps de POST /api/library
// (voir renderLibraryList) pour qu'il filtre/annote la liste lui-même.
// Plafond FIFO généreux : ~35 octets par id, 20000 ids ≈ 700 Ko, très
// en dessous de la limite localStorage.
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
    // Quota plein / stockage désactivé : tant pis, le suivi "déjà vue"
    // est un confort, pas une fonctionnalité critique.
  }
}

// Niveau de difficulté affiché à droite du titre de la grille à jouer,
// à la demande explicite de l'utilisateur. Lu depuis `puzzle` :
// backend/app.py ajoute `difficulty` au result d'une grille fraîchement
// générée, et l'enregistrement GRID_STORE d'une grille rechargée depuis
// la bibliothèque le porte déjà. Le libellé est traduit (difficultyEasy/
// Medium/Hard), donc ré-appelé au changement de langue de l'interface.
// Une grille sans ce champ (sauvegardée avant cette fonctionnalité)
// n'affiche simplement rien.
function renderGridDifficulty() {
  const key = { easy: "difficultyEasy", medium: "difficultyMedium", hard: "difficultyHard" }[
    puzzle && puzzle.difficulty
  ];
  if (key) {
    // Préfixé "Difficulté : " (ponctuation par langue) plutôt qu'un
    // adjectif féminin nu ("Moyenne"), qui sans contexte se lit mal.
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
  userLetters = Array.from({ length: gridData.height }, () => Array(gridData.width).fill(""));
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
  // Niveau de difficulté ("Facile" / "Moyen" / "Difficile") affiché à
  // droite du titre, à la demande explicite de l'utilisateur. Traduit
  // selon la langue de l'interface, donc re-rendu aussi au changement de
  // langue (voir languageSelect's own handler). La difficulté seule
  // (sans titre) suffit à afficher la ligne #grid-title.
  renderGridDifficulty();
  gridTitleEl.hidden = !gridData.title && gridDifficultyEl.hidden;
  // Marque cette grille "déjà vue" — même chemin pour une grille qui vient
  // d'être générée (backend/app.py ajoute `id` au `result`, voir son
  // commentaire) et une grille rechargée depuis la bibliothèque
  // (GET /api/library/{grid_id} renvoie déjà `id`). À la demande explicite
  // de l'utilisateur : "y compris la grille qu'il vient de générer".
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

// Base publique pour les liens partageables de la Bibliothèque, à la
// demande explicite de l'utilisateur ("URL de base https://falcon.cubaix.com/").
// Le lien de chaque ligne est SHARE_BASE_URL + "?grid=<id>" ; ouvert dans
// un nouvel onglet, il recharge la grille pour la jouer (voir
// maybeLoadGridFromUrl() en fin de fichier, qui lit ?grid= quel que soit
// l'hôte — le lien du tableau, lui, pointe toujours vers ce domaine public).
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
          // "all" ou un code langue — par défaut la langue de l'interface
          // (voir #library-language-filter), à la demande explicite de
          // l'utilisateur : "Par défaut, n'afficher que les grilles dans
          // la langue de l'interface".
          language_filter: libraryLanguageFilter.value,
          // "all" ou easy/medium/hard — "Tous les niveaux" par défaut, ne
          // suit pas la langue de l'interface (voir #library-difficulty-filter).
          difficulty_filter: libraryDifficultyFilter.value,
          // "all"/"unseen"/"seen"/"mine" — "Mes grilles" ("mine") filtre
          // côté back sur le champ `pseudo` de chaque grille, comparé au
          // pseudo courant envoyé ci-dessous.
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
    // Grisé si le back l'a annotée `seen` (ou, par sécurité, si notre
    // propre localStorage la connaît) — reste cliquable.
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
    // Grille bilingue (voir backend/grid_store.py's own `bilingual`
    // field), à la demande explicite de l'utilisateur : montre les deux
    // codes langue ("fr/en") à la suite du nom déjà affiché ci-dessus,
    // plutôt qu'un second, éventuellement long, libellé en toutes
    // lettres — reste lisible même quand les deux langues partagent une
    // ligne étroite du tableau.
    if (entry.bilingual) {
      languageTd.textContent += ` (${entry.language}/${entry.bilingual})`;
    }
    const dateTd = document.createElement("td");
    dateTd.textContent = entry.created_at ? new Date(entry.created_at).toLocaleString(uiLanguage) : "";
    const titleTd = document.createElement("td");
    const titleMain = document.createElement("div");
    titleMain.textContent = entry.title || "";
    titleTd.appendChild(titleMain);
    // Grille créée en modifiant une grille existante de la Bibliothèque
    // (bouton "Ouvrir en mode Interactif" — voir backend/grid_store.py's
    // save_grid_json/save_grid_work's own `origin`), à la demande
    // explicite de l'utilisateur : mentionne la provenance (grille/
    // auteur/date d'origine) sous le titre, sur sa propre ligne. `origin`
    // est un instantané pris au moment où l'édition a commencé (jamais
    // relu depuis la grille d'origine, qui peut depuis avoir changé ou
    // disparu) — absent/`None` pour toute grille non issue d'une édition.
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
    // Colonne "Thématique" : la liste de mots saisie à la génération
    // (champ `theme` du JSON de la grille — voir backend/grid_store.py),
    // vide quand la grille n'a pas de thématique. À la demande explicite
    // de l'utilisateur.
    const themeTd = document.createElement("td");
    themeTd.textContent = entry.theme || "";
    const difficultyTd = document.createElement("td");
    difficultyTd.textContent = difficultyLabels[entry.difficulty] || entry.difficulty || "";
    const sizeTd = document.createElement("td");
    sizeTd.textContent = entry.width && entry.height ? `${entry.width}×${entry.height}` : "";
    // Dernière colonne : pseudo de l'auteur (champ `pseudo` du JSON de la
    // grille — voir backend/grid_store.py). Une grille sans auteur
    // (générée sans pseudo défini) est attribuée à "Falcon Auto Bot".
    const authorTd = document.createElement("td");
    authorTd.textContent = entry.pseudo || t.libraryAuthorBot;
    // Grille bâtie via le mode "Interactif" : tag "(Création)" à côté de
    // l'auteur (voir backend/grid_store.py's save_grid_json's `interactive`).
    if (entry.interactive) authorTd.textContent += ` ${t.libraryCreationTag}`;
    // Dernière colonne : lien partageable vers la grille, à la demande
    // explicite de l'utilisateur. Ouvre SHARE_BASE_URL + "?grid=<id>" dans
    // un nouvel onglet (le domaine public, indépendamment de l'hôte
    // courant). stopPropagation pour ne pas déclencher aussi le
    // loadLibraryGrid() du clic sur la ligne (qui, lui, charge dans
    // l'onglet courant).
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
    // Colonne "Interactif" : bouton icône (crayon) ouvrant la grille dans
    // le mode "Interactif" — le back crée alors une nouvelle tâche
    // GRID_WORK à partir de cette grille (voir POST /api/interactive/
    // from-library). Icône SVG inline (aucune police/lib d'icône externe,
    // même convention que le badge PDF / le badge "i" de l'en-tête).
    // stopPropagation comme les liens voisins pour ne pas aussi déclencher
    // le loadLibraryGrid() du clic sur la ligne.
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
    // Dernière colonne : téléchargement PDF imprimable (grille vide +
    // définitions + titre, sans réponses — voir GET /api/library/<id>/pdf),
    // à la demande explicite de l'utilisateur. URL relative : passe par le
    // proxy du front sur l'hôte courant (local ou public). `download` +
    // stopPropagation, comme le lien "Jouer" ci-dessus.
    const pdfTd = document.createElement("td");
    const pdfLink = document.createElement("a");
    pdfLink.href = `/api/library/${encodeURIComponent(entry.id)}/pdf`;
    pdfLink.setAttribute("download", "");
    pdfLink.rel = "noopener";
    // Icône PDF plutôt que le mot "Télécharger", à la demande explicite de
    // l'utilisateur. Badge rouge "PDF" sur une page — dessiné en SVG inline
    // (ce projet n'utilise aucune police/lib d'icônes externe, cf. le badge
    // "i" de l'en-tête). Le libellé accessible reste `libraryPdfText`
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
    const response = await fetchWithTimeout(`/api/library/${gridId}`, {}, FETCH_TIMEOUT_MS);
    const data = await response.json();
    if (!response.ok) {
      throw new Error(describeErrorCode(t, data.detail && data.detail.code, data.detail));
    }
    hideAttemptPreview();
    stopBtn.hidden = true;
    continueBtn.hidden = true;
    // gridId est toujours connu ici ; displayFinalGrid marque déjà
    // data.id, ce doublon couvre le cas improbable où le disque n'aurait
    // pas renvoyé le champ.
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
  await runInteractive({ grid_id: gridId }, "/api/interactive/from-library");
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

// Bouton "Actualiser", à la demande explicite de l'utilisateur : re-rend
// la page courante avec les filtres actuels, sans revenir à la page 1
// (contrairement aux changements de filtre ci-dessous) — sert juste à
// revoir l'état le plus récent (une grille nouvellement ajoutée, un statut
// "vue" mis à jour par un autre onglet, etc.) sans perdre sa place.
libraryRefreshBtn.addEventListener("click", () => {
  renderLibraryList();
});

// Les trois sélecteurs en haut de la Bibliothèque : filtre de langue
// (toutes / une langue / bilingue), filtre de niveau (tous les niveaux /
// easy / medium / hard) et filtre "déjà vues" (toutes / non vues / déjà
// vues). Chaque changement repart de la page 1 et re-rend la liste. Le
// filtre de langue vaut par défaut la langue de l'interface et suit ses
// changements (voir le handler de #language plus bas) ; le filtre de
// niveau reste sur "Tous les niveaux" et ne suit pas la langue.
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

// Panneau "Dictionnaire", à la demande explicite de l'utilisateur : à
// partir d'un mot (accents/casse ignorés), le back liste tous les mots de
// la même racine tirés du wordlist, avec leurs définitions du fichier
// <lang>_glosses.jsonl (voir backend/dictionary_lookup.py + GET
// /api/dictionary). Chaque recherche produit son propre tableau, empilé
// en haut (le plus récent d'abord) ; "Effacer" vide la pile.
function hideDictionaryPanel() {
  dictionaryPanel.hidden = true;
  syncRssPanelVisibility();
}

// Un tableau par mot cherché : colonne 1 = forme complète (forme canonique
// entre parenthèses), colonne 2 = liste "type grammatical : définition".
// Construit entièrement via l'API DOM (textContent) — aucune donnée
// dictionnaire n'est jamais injectée en innerHTML.
function renderDictionaryResult(query, data) {
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
    dictionaryResults.prepend(block);
    return;
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
  dictionaryResults.prepend(block);
}

dictionaryBtn.addEventListener("click", () => {
  if (dictionaryPanel.hidden) {
    dictionaryPanel.hidden = false;
    dictionaryLanguage.value = uiLanguage;
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

// Requête de définition envoyée à Perplexity, adaptée à la langue du
// sélecteur #dictionary-language — jamais à uiLanguage (l'interface),
// puisqu'on veut demander la définition D'UN MOT DANS CETTE langue-là,
// quelle que soit la langue de l'interface elle-même. `%s` reçoit le mot
// tel que saisi (jamais traduit, ni ré-accentué). Fallback sur le
// gabarit français si `dictionaryLanguage.value` ne correspond à aucune
// entrée connue (ne devrait jamais arriver, le sélecteur n'offre que ces
// 6 langues).
const PERPLEXITY_DEFINE_QUERY_TEMPLATES = {
  fr: (word) => `Définir le mot français : ${word}`,
  en: (word) => `Define the English word: ${word}`,
  de: (word) => `Definiere das deutsche Wort: ${word}`,
  es: (word) => `Definir la palabra española: ${word}`,
  it: (word) => `Definisci la parola italiana: ${word}`,
  pt: (word) => `Definir a palavra portuguesa: ${word}`,
};

dictionaryPerplexityBtn.addEventListener("click", () => {
  const word = dictionaryInput.value.trim();
  if (!word) {
    dictionaryInput.focus();
    return;
  }
  const template =
    PERPLEXITY_DEFINE_QUERY_TEMPLATES[dictionaryLanguage.value] ||
    PERPLEXITY_DEFINE_QUERY_TEMPLATES.fr;
  const url = `https://www.perplexity.ai/search?q=${encodeURIComponent(template(word))}`;
  window.open(url, "_blank", "noopener,noreferrer");
});

dictionaryForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  const query = dictionaryInput.value.trim();
  if (!query) return;
  const t = I18N[uiLanguage];
  dictionarySearchBtn.disabled = true;
  try {
    const response = await fetchWithTimeout(
      `/api/dictionary?q=${encodeURIComponent(query)}&lang=${encodeURIComponent(dictionaryLanguage.value)}`,
      {}, FETCH_TIMEOUT_MS,
    );
    if (!response.ok) throw new Error(t.dictionaryError);
    const data = await response.json();
    renderDictionaryResult(query, data);
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

// "Mots similaires" : les 50 mots les plus proches de l'expression saisie
// dans la collection Qdrant "words" (tenant = langue du panneau), triés du
// plus similaire au moins similaire, affichés sur une seule ligne séparés
// par des virgules. Empilé en haut comme les résultats du dictionnaire.
// Voir GET /api/similar_words (backend/app.py) + backend/qdrant_store.py.
function renderSimilarWordsResult(query, words) {
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
    // Chaque entrée est {word, score} — on affiche le score Qdrant entre
    // parenthèses avec 2 décimales à côté du mot, à la demande explicite
    // de l'utilisateur.
    line.textContent = words
      .map((x) => {
        const s = typeof x.score === "number" ? ` (${x.score.toFixed(2)})` : "";
        return `${x.word}${s}`;
      })
      .join(", ");
    block.appendChild(line);
  }
  dictionaryResults.prepend(block);
}

// #theme-precision est un <input type="text"> (voir index.html) : on lit
// sa valeur en FORÇANT le point comme séparateur décimal (une virgule
// saisie est normalisée en point, à la demande explicite de
// l'utilisateur — un séparateur virgule est source de confusion), bornée
// à [0, 1]. Renvoie undefined si le champ est vide (le back applique
// alors THEME_MIN_SCORE). Partagé par la génération de grille ET le
// bouton "Thématique" du panneau Dictionnaire.
function readThemePrecision() {
  const el = document.getElementById("theme-precision");
  if (!el) return undefined;
  const raw = el.value.trim().replace(",", ".");
  if (raw === "") return undefined;
  const n = Number(raw);
  if (!Number.isFinite(n)) return undefined;
  return Math.min(1, Math.max(0, n));
}

// Au blur, réécrit le champ avec la valeur normalisée (point, bornée [0,1])
// — un "0,7" saisi devient visiblement "0.7", un "1.5" devient "1".
(() => {
  const el = document.getElementById("theme-precision");
  if (!el) return;
  el.addEventListener("blur", () => {
    const v = readThemePrecision();
    if (v !== undefined) el.value = String(v);
  });
})();

dictionarySimilarBtn.addEventListener("click", async () => {
  const query = dictionaryInput.value.trim();
  if (!query) return;
  const t = I18N[uiLanguage];
  dictionarySimilarBtn.disabled = true;
  try {
    // Le bouton "Thématique" du Dictionnaire réutilise le seuil du champ
    // "Précision thématique" du formulaire de génération (min_score), à la
    // demande explicite de l'utilisateur — omis si le champ est vide.
    const prec = readThemePrecision();
    let url = `/api/similar_words?q=${encodeURIComponent(query)}`
      + `&lang=${encodeURIComponent(dictionaryLanguage.value)}`;
    if (prec !== undefined) url += `&min_score=${prec}`;
    // Timeout élargi : le back fait une expansion LLM du terme avant les
    // recherches Qdrant (voir SIMILAR_FETCH_TIMEOUT_MS).
    const response = await fetchWithTimeout(url, {}, SIMILAR_FETCH_TIMEOUT_MS);
    if (!response.ok) throw new Error(t.dictionarySimilarError);
    const data = await response.json();
    renderSimilarWordsResult(query, (data && data.words) || []);
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

// "Synonymes" : même rendu que "Thématique" (renderSimilarWordsResult),
// mais une recherche Qdrant directe sur le mot/l'expression saisi(e),
// SANS appel au LLM pour étendre la recherche — à la demande explicite
// de l'utilisateur. Timeout générique (FETCH_TIMEOUT_MS), pas le timeout
// élargi de "Thématique" : il n'y a ici aucun aller-retour LLM à attendre.
dictionarySynonymsBtn.addEventListener("click", async () => {
  const query = dictionaryInput.value.trim();
  if (!query) return;
  const t = I18N[uiLanguage];
  dictionarySynonymsBtn.disabled = true;
  try {
    const prec = readThemePrecision();
    let url = `/api/synonyms?q=${encodeURIComponent(query)}`
      + `&lang=${encodeURIComponent(dictionaryLanguage.value)}`;
    if (prec !== undefined) url += `&min_score=${prec}`;
    const response = await fetchWithTimeout(url, {}, FETCH_TIMEOUT_MS);
    if (!response.ok) throw new Error(t.dictionarySynonymsError);
    const data = await response.json();
    renderSimilarWordsResult(query, (data && data.words) || []);
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

// "Définir" : jusqu'à 10 définitions indépendantes de l'expression saisie,
// générées par le LLM comme pour un mot de grille (voir backend/clues.py,
// LLMClueGenerator.generate_definitions), une par ligne. Un seul appel
// best-effort côté back (pas de relance comme en génération de grille) —
// re-cliquer suffit à retenter. Voir DEFINE_FETCH_TIMEOUT_MS ci-dessus
// pour pourquoi ce bouton utilise un délai bien plus long que les deux
// autres boutons de ce même formulaire.
function renderDefineResult(query, definitions) {
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
  dictionaryResults.prepend(block);
}

dictionaryDefineBtn.addEventListener("click", async () => {
  const query = dictionaryInput.value.trim();
  if (!query) return;
  const t = I18N[uiLanguage];
  dictionaryDefineBtn.disabled = true;
  try {
    const response = await fetchWithTimeout(
      `/api/dictionary/define?q=${encodeURIComponent(query)}`
      + `&lang=${encodeURIComponent(dictionaryLanguage.value)}`,
      {}, DEFINE_FETCH_TIMEOUT_MS,
    );
    if (!response.ok) throw new Error(t.dictionaryDefineError);
    const data = await response.json();
    renderDefineResult(query, (data && data.definitions) || []);
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
function newChatSessionId() {
  return (window.crypto && window.crypto.randomUUID)
    ? window.crypto.randomUUID()
    : `${Date.now()}-${Math.random().toString(36).slice(2)}`;
}
// `let`, not `const` : le bouton de réinitialisation de la conversation
// (#chatbot-reset-btn, voir plus bas) en régénère un nouveau, pour que la
// discussion repartie soit tracée dans un nouveau fichier LOG_CHAT côté
// backend/app.py plutôt que d'être ajoutée à la suite de la précédente.
let chatSessionId = newChatSessionId();

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
    // The grid's current fill/selection direction ("across"/"down", the
    // shared activeDirection state driven by the Across/Down buttons and
    // Shift/CapsLock) — lets the backend pick WHICH of two crossing words
    // a clicked cell is about (and, on a bilingual grid, in which
    // language to answer) instead of having to ask the player.
    active_direction: activeDirection,
    words: puzzle
      ? puzzle.words.map((w) => ({
          row: w.row, col: w.col, direction: w.direction, clue: w.clue, answer: w.answer,
          // Langue réellement utilisée pour CE mot (voir backend/
          // crossword_gen.py's generate_grid, chaque mot porte son propre
          // `language` selon sa direction sur une grille bilingue) — à la
          // demande explicite de l'utilisateur, pour que David FALCON
          // réponde dans la langue du mot quand une aide de remplissage
          // est demandée. Identique pour tous les mots sur une grille
          // ordinaire, donc sans effet dans ce cas.
          language: w.language,
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

// Bouton icône "réinitialiser la conversation", à la demande explicite de
// l'utilisateur : vide l'historique client (chatHistory), remet
// chatUserHasSpoken à false pour que le message de bienvenue redevienne
// re-traduisible sur changement de langue, en régénère un nouvel
// identifiant de session (nouveau fichier LOG_CHAT côté backend) et
// ré-affiche le message d'accueil.
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
}

// The diagnostics describe the grid the backend last saw; drop them the
// moment the player edits by hand so a stale highlight is never shown.
function clearInteractiveDiagnostics() {
  interactiveImpossibleCells = new Set();
  interactiveLowCells = new Set();
  interactiveInvalidCells = new Set();
  interactiveVerifyReport = [];
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
  // cause of "Proposer" always answering "Sélectionnez un mot entièrement
  // rempli", reported directly by the user, regardless of which word was
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
// definition input, at the user's explicit request: "un rapport indiquant
// les problèmes rencontrés sur chaque mot. Un mot par ligne." Reads the
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

function renderInteractive() {
  // Drop any theme-word cell that no longer carries a letter (undo, erase,
  // toggle-to-black) so the magenta mark tracks the real grid content.
  for (const key of interactiveThemeCells) {
    const [r, c] = key.split(",").map(Number);
    if (!/[A-Z]/.test(interactiveGrid[r] && interactiveGrid[r][c])) {
      interactiveThemeCells.delete(key);
    }
  }
  syncPuzzleFromInteractive();
  renderGrid();
  const w = selectedInteractiveWord();
  interactiveDefinitionInput.value = w ? interactiveDefs.get(interactiveKey(w)) || "" : "";
  interactiveDefinitionInput.disabled = !w;
  interactivePrevBtn.disabled = interactiveUndoStack.length <= 1;
  // The "Mots" candidate list is only ever valid for the exact grid state
  // it was computed from — any render (selection change, typed letter,
  // undo, clean, ...) can make it stale, so it's cleared here rather than
  // left showing outdated candidates; the player just clicks "Mots" again.
  interactiveWordsResults.hidden = true;
  interactiveWordsResults.innerHTML = "";
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
function advanceInteractiveSelection() {
  if (!selected) return;
  const rows = interactiveGrid.length;
  const cols = rows ? interactiveGrid[0].length : 0;
  let { row, col } = selected;
  while (true) {
    if (activeDirection === "across") col += 1;
    else row += 1;
    if (row >= rows || col >= cols) return;
    if (interactiveGrid[row][col] !== "#") {
      selected = { row, col };
      return;
    }
  }
}
function interactiveTypeLetter(letter) {
  if (!interactiveMode || !selected) return;
  interactivePushUndo();
  interactiveGrid[selected.row][selected.col] = letter.toUpperCase();
  advanceInteractiveSelection();
  setInteractiveMessage("");
  renderInteractive();
}
function interactiveToggleBlack() {
  if (!interactiveMode || !selected) return;
  interactivePushUndo();
  const cur = interactiveGrid[selected.row][selected.col];
  interactiveGrid[selected.row][selected.col] = cur === "#" ? "" : "#";
  setInteractiveMessage("");
  renderInteractive();
}
function interactiveErase() {
  if (!interactiveMode || !selected) return;
  interactivePushUndo();
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

function handleInteractiveKeydown(event) {
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
// explicit request ("10 propositions affichées en dessous, comme
// 'Proposer une définition'") — see proposeInteractiveTitle() below for
// how the list itself is fetched.
function renderInteractiveTitleProposals(list) {
  renderInteractivePickList(interactiveTitleProposeResults, list, (title) => {
    interactiveTitleInput.value = title;
    interactiveTitleProposeResults.hidden = true;
    interactiveTitleProposeResults.innerHTML = "";
  });
}

// ---- Candidate words for the selected slot (POST /api/interactive/
// candidates) — "Mots" button, at the user's explicit request: "ajouter
// un bouton Mots qui liste les mots possible pour l'emplacement
// sélectionné... En premier, les mots du glossaire thématique... en
// magenta, puis les autres mots en noir. Quand l'utilisateur clique sur
// un mot, ça le met en place sur l'emplacement sélectionné." ----
function renderInteractiveWords(themeWords, otherWords) {
  interactiveWordsResults.innerHTML = "";
  const all = [
    ...(themeWords || []).map((word) => ({ word, theme: true })),
    ...(otherWords || []).map((word) => ({ word, theme: false })),
  ];
  if (!all.length) {
    interactiveWordsResults.hidden = true;
    return;
  }
  const placeWord = (word) => {
    const w = selectedInteractiveWord();
    if (!w || word.length !== w.cells.length) return;
    interactivePushUndo();
    for (let i = 0; i < w.cells.length; i++) {
      const { row, col } = w.cells[i];
      interactiveGrid[row][col] = word[i];
    }
    setInteractiveMessage("");
    renderInteractive();
  };
  all.forEach(({ word, theme }, i) => {
    const item = document.createElement("span");
    item.className = theme
      ? "interactive-word-item interactive-word-theme"
      : "interactive-word-item";
    item.tabIndex = 0;
    item.textContent = word;
    const pick = () => placeWord(word);
    item.addEventListener("click", pick);
    item.addEventListener("keydown", (e) => {
      if (e.key === "Enter" || e.key === " ") {
        e.preventDefault();
        pick();
      }
    });
    interactiveWordsResults.appendChild(item);
    if (i < all.length - 1) {
      interactiveWordsResults.appendChild(document.createTextNode(", "));
    }
  });
  interactiveWordsResults.hidden = false;
}

interactiveWordsBtn.addEventListener("click", async () => {
  const t = I18N[uiLanguage];
  const w = selectedInteractiveWord();
  if (!w) {
    setInteractiveMessage(t.interactiveWordsNeedsSlot, true);
    return;
  }
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
    renderInteractiveWords(themeWords, otherWords);
    if (!themeWords.length && !otherWords.length) {
      setInteractiveMessage(t.interactiveWordsEmpty, true);
    }
  } catch (err) {
    setInteractiveMessage(t.interactiveWordsError, true);
  } finally {
    interactiveWordsBtn.disabled = false;
  }
});

// Fetches up to 10 candidate titles and shows them as a pick list (see
// renderInteractiveTitleProposals()), at the user's explicit request:
// "Le bouton 'Proposer un titre' doit générer 10 propositions affichées
// en dessous (comme 'Proposer une définition'), et également utiliser
// le champ Thématique si renseigné." Two callers, distinguished by
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
  // "Quand un utilisateur réédite une grille thématique, renseigner le
  // champ Thématique avec les mots de la grille d'origine." Covers every
  // entry path uniformly (a fresh start, "Ouvrir en mode Interactif"
  // from the Library, resuming a "Créations" draft): backend/app.py's
  // _run_interactive_job/_run_interactive_resume_job both put the raw
  // theme string on job["result"]["theme"] (pollJob() only ever returns
  // that, never job["interactive"]). `interactiveTheme` also feeds
  // "Proposer une définition"/"Proposer un titre" below (see
  // dictionaryDefineUrl()/proposeInteractiveTitle()).
  interactiveTheme = state.theme || "";
  themeInput.value = interactiveTheme;
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
  interactiveDifficulty = state.difficulty || interactiveDifficulty;
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
  generationTimesPrevBtn.hidden = true;
  generationTimesNextBtn.hidden = true;
  generationTimesPosition.hidden = true;
  cluesAcross.innerHTML = "";
  cluesDown.innerHTML = "";
  applyDefinitionsVisibility(); // hides #clues/#down-clues-section/#hover-definition-row
  // "Précédent"/"Suivant" flank the grid, vertically centered against it,
  // at the user's explicit request — moved here (out of the play-mode-
  // shaped #interactive-nav row) into #grid-column itself, right next to
  // #grid, which switches to a horizontal flex row for this mode (see
  // style.css's #grid-column.interactive-flank).
  gridColumn.classList.add("interactive-flank");
  // Centers the whole Précédent+Grille+Suivant row within #board's full
  // width, at the user's explicit request — scoped to interactive mode
  // only (via this class) so the ordinary play-mode #clues+#grid-column
  // layout is never affected: #clues is always hidden in this mode (see
  // applyDefinitionsVisibility above), so #grid-column is #board's only
  // visible child here, with nothing else for centering it to disturb.
  board.classList.add("interactive-centered");
  interactivePrevBtn.hidden = false;
  interactiveNextBtn.hidden = false;
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
  // "En mode interactif, ouvrir automatiquement le Dictionnaire." — at
  // the user's explicit request. Mirrors dictionaryBtn's own manual-open
  // logic (unhide the panel, set its language) but deliberately never
  // calls dictionaryInput.focus(): an automatic/background action must
  // never steal keyboard focus away from the grid the way a real click
  // on the button is allowed to. `interactiveLanguage` is reliably
  // correct here on every entry path (fresh start, "Ouvrir en mode
  // Interactif" from the Library, resuming a draft) — see its own
  // assignment above, fixed for exactly this kind of use.
  dictionaryPanel.hidden = false;
  dictionaryLanguage.value = interactiveLanguage;
  syncRssPanelVisibility();
  setActiveDirection(activeDirection); // syncs the 4 direction buttons + renders
}

function hideInteractivePanel() {
  interactiveMode = false;
  interactiveControls.hidden = true;
  gridColumn.classList.remove("interactive-flank");
  board.classList.remove("interactive-centered");
  interactivePrevBtn.hidden = true;
  interactiveNextBtn.hidden = true;
  // Restore the automatic-generation history-navigation controls hidden by
  // enterInteractiveMode() — harmless even before a real grid exists, since
  // they were never conditionally hidden outside of interactive mode to
  // begin with (only ever disabled via updatePreviewNavButtons()).
  generationTimesPrevBtn.hidden = false;
  generationTimesNextBtn.hidden = false;
  generationTimesPosition.hidden = false;
  applyDefinitionsVisibility(); // restore normal #clues/#hover-definition-row rules
  syncRssPanelVisibility();
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

// Fired after every "Suivant"/"Précédent" click, at the user's explicit
// request: "chaque appui sur Suivant/Précédent sauvegarde l'état en cours
// du process de création dans le dossier GRID_WORK." Best-effort and
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
// it at POST /api/interactive/resume instead of .../start.
async function resumeInteractiveWork(workId) {
  await runInteractive({ work_id: workId }, "/api/interactive/resume");
}

// Deletes one saved work-in-progress file, then refreshes the list — at
// the user's explicit request: "cliquer sur un bouton icône pour
// supprimer la tâche." Best-effort: a failure here just leaves the list
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

    const titleTd = document.createElement("td");
    titleTd.textContent = item.title || t.interactiveWorkUntitled;
    tr.appendChild(titleTd);

    const langTd = document.createElement("td");
    langTd.textContent = item.language || "";
    tr.appendChild(langTd);

    const diffTd = document.createElement("td");
    const diffKey = difficultyKeys[item.difficulty];
    diffTd.textContent = diffKey ? t[diffKey] : (item.difficulty || "");
    tr.appendChild(diffTd);

    const sizeTd = document.createElement("td");
    sizeTd.textContent = item.width && item.height ? `${item.width} × ${item.height}` : "";
    tr.appendChild(sizeTd);

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

// "Quand un utilisateur ouvre l'interface (ou la recharge), si il a des
// grilles sauvegardées dans GRID_WORK, afficher un panneau avec la
// liste," at the user's explicit request — called once, right after the
// current pseudo is actually known (see initUserPrefs()'s returning-user
// branch and the welcome form's own submit handler below), never before:
// without a pseudo there is no reliable way to know which saved sessions
// belong to this particular player. "fermer le panneau en le laissant
// inchangé pour la fois suivante" is already the default behaviour of
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
    const resp = await fetchWithTimeout("/api/interactive/step", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ job_id: interactiveJobId, grid: wireGrid }),
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
              words: interactiveSlots().map((s) => s.answer),
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
    for (const [r, c] of data.placed.cells) interactiveGrid[r][c] = data.grid[r][c];
    if (data.placed.from_theme) {
      for (const [r, c] of data.placed.cells) interactiveThemeCells.add(`${r},${c}`);
    }
    setInteractiveDiagnostics(data);
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
// Unlike "Suivant" (which only ever touches the cells of the one word
// just placed), cleanup can change cells anywhere in the grid, so the
// whole interactiveGrid is replaced from the response rather than
// patched cell by cell.
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
      body: JSON.stringify({ job_id: interactiveJobId, grid: wireGrid, deep }),
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
// the user's explicit request: "Vérifier que le bouton 'Propose une
// définition' utilise bien le champ thématique pour les propositions
// quand il est renseigné." `interactiveTheme` is kept current by
// enterInteractiveMode() (re-editing/resuming a themed grid) and by the
// generation form's own submit handler (a fresh themed session) — never
// re-read from the DOM here, so a mid-session edit of the visible field
// alone (without going through those two paths) isn't picked up; that
// matches every other interactive-mode use of `interactiveLanguage`/
// `interactiveDifficulty`, which are session-scoped the same way.
function dictionaryDefineUrl(word) {
  let url = `/api/dictionary/define?q=${encodeURIComponent(word)}`
    + `&lang=${encodeURIComponent(interactiveLanguage)}`;
  if (interactiveTheme) url += `&theme=${encodeURIComponent(interactiveTheme)}`;
  return url;
}

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
      dictionaryDefineUrl(w.answer), {}, DEFINE_FETCH_TIMEOUT_MS,
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
    // results block hidden), which is exactly the "tourne, mais
    // n'affiche pas de résultat" reported directly by the user — it was
    // never actually stuck, just silently empty.
    if (!list.length) setInteractiveMessage(t.interactiveProposeEmpty, true);
  } catch (err) {
    setInteractiveMessage(t.interactiveProposeError, true);
  } finally {
    interactiveProposeBtn.disabled = false;
  }
});

// Checks the WHOLE grid at once, at the user's explicit request: "vérifier
// toute la grille et mettre en rouge les mots complets qui posent un
// problème, soit parce qu'ils ne sont pas des mots du dictionnaire, soit
// parce qu'ils n'ont pas de définition." Supersedes an earlier version that
// only ever looked at the currently selected word (see the two prior bug
// reports this project's own history already documents for that narrower
// design). Dictionary membership is checked server-side, in one batched
// call (POST /api/interactive/verify) — the missing-definition half needs
// no round trip at all, since interactiveDefs already lives client-side.
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
  setInteractiveMessage("");
  try {
    const resp = await fetchWithTimeout("/api/interactive/verify", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        job_id: interactiveJobId,
        words: filled.map((s) => s.answer),
      }),
    }, FETCH_TIMEOUT_MS);
    if (resp.status === 404) {
      setInteractiveMessage(t.interactiveSessionLost, true);
      return;
    }
    const data = await resp.json();
    if (!resp.ok) {
      setInteractiveMessage(describeErrorCode(t, data.detail && data.detail.code, data.detail), true);
      return;
    }
    const invalidWords = new Set(data.invalid_words || []);
    const invalidCells = new Set();
    const report = [];
    let problemCount = 0;
    for (const s of filled) {
      const hasDef = !!(interactiveDefs.get(interactiveKey(s)) || "").trim();
      const isInvalidWord = invalidWords.has(s.answer);
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
  } catch (err) {
    setInteractiveMessage(t.errorConnectionLost, true);
  } finally {
    interactiveVerifyBtn.disabled = false;
  }
});

// "Définitions" : génère automatiquement une définition pour chaque mot
// entièrement rempli et valide qui n'en a pas encore, à la demande
// explicite de l'utilisateur. Réutilise POST /api/interactive/verify (pour
// écarter les mots absents du dictionnaire — "et valides") puis, mot par
// mot, GET /api/dictionary/define (comme "Proposer"), en gardant la
// première définition proposée. Séquentiel : chaque appel est un vrai
// aller-retour LLM, potentiellement lent — la progression est affichée et
// le traitement est au mieux (un mot dont la génération échoue est
// simplement laissé sans définition).
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
          words: pending.map((s) => s.answer),
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
          dictionaryDefineUrl(s.answer), {}, DEFINE_FETCH_TIMEOUT_MS,
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
        difficulty: interactiveDifficulty,
        theme: interactiveTheme || undefined,
        pseudo: userPseudo || undefined,
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
  // Grille bilingue, à la demande explicite de l'utilisateur : omis
  // (`undefined`, donc absent du JSON envoyé) quand le sélecteur
  // "Bilingue" est resté identique à la langue principale — c'est le
  // Back (GenerateRequest.bilingual_language) qui traite déjà `None`/une
  // valeur identique comme "grille monolingue ordinaire" de toute façon,
  // mais autant ne pas envoyer un champ sans effet réel.
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
  // Thématique optionnelle (liste de mots) — omise si vide.
  const theme = themeInput.value.trim();
  // "Précision thématique" : seuil de similarité Qdrant minimal du
  // glossaire thématique (voir backend/app.py's THEME_MIN_SCORE). Point
  // forcé comme séparateur décimal, borné [0,1] ; undefined si vide -> le
  // back applique sa valeur par défaut.
  const themePrecision = readThemePrecision();

  if (mode === "interactive") {
    interactiveLanguage = language;
    interactiveDifficulty = difficulty;
    interactiveTheme = theme;
    await runInteractive({
      language, width, height, difficulty,
      black_enrichment_percent: blackEnrichmentPercent,
      force_letters_percent: forceLettersPercent,
      theme: theme || undefined,
      theme_precision: themePrecision,
      pseudo: userPseudo || undefined,
    });
    return;
  }

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
          // Thématique : liste de mots orientant sémantiquement la grille
          // (pré-recherche Qdrant côté back — voir backend/app.py's
          // THEME_PRESEARCH_LIMIT). Omise si vide.
          theme: theme || undefined,
          // Seuil de similarité du glossaire thématique (voir
          // backend/app.py's THEME_MIN_SCORE) — omis si le champ est vide.
          theme_precision: themePrecision,
          // Pseudo de l'auteur, enregistré dans le JSON de la grille (voir
          // backend/grid_store.py's save_grid_json) — omis s'il est vide.
          pseudo: userPseudo || undefined,
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

// Lien partageable de la Bibliothèque : si l'URL porte "?grid=<id>" (voir
// SHARE_BASE_URL / la colonne "Lien" du tableau), charger cette grille
// pour la jouer dès l'ouverture de la page. Lit le paramètre quel que
// soit l'hôte (le lien du tableau vise le domaine public, mais un
// "?grid=" collé sur http://127.0.0.1:3000/ marche tout autant).
// loadLibraryGrid() gère seul l'erreur (id inconnu -> #status).
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
