// DevBot page (DevBot.html): a stand-alone chat whose replies come from
// POST /api/devchat, grounded on the DOC_ALGO/FR files only. The
// Markdown rendering and the stream reading mirror script.js's own
// "David FALCON" widget, which this page cannot load (script.js drives
// the whole index.html DOM).

const SUPPORTED_UI_LANGS = ["fr", "en", "de", "es", "it", "pt"];
const CHAT_FETCH_TIMEOUT_MS = 160000;

// The user's language: `?lang=` in the URL, else the interface language
// saved by the main page (cwf-prefs cookie), else the browser's.
function detectLanguage() {
  const fromUrl = new URLSearchParams(window.location.search).get("lang");
  if (fromUrl && SUPPORTED_UI_LANGS.indexOf(fromUrl.toLowerCase()) !== -1) return fromUrl.toLowerCase();
  try {
    const m = document.cookie.match(/(?:^|;\s*)cwf-prefs=([^;]*)/);
    if (m) {
      const prefs = JSON.parse(decodeURIComponent(m[1]));
      if (prefs && SUPPORTED_UI_LANGS.indexOf(prefs.lang) !== -1) return prefs.lang;
    }
  } catch (e) {
    // Unreadable cookie: fall through to the browser's language.
  }
  const cands = (navigator.languages && navigator.languages.length)
    ? navigator.languages
    : [navigator.language || ""];
  for (const c of cands) {
    const base = String(c).toLowerCase().split("-")[0];
    if (SUPPORTED_UI_LANGS.indexOf(base) !== -1) return base;
  }
  return "en";
}

const uiLanguage = detectLanguage();
const t = I18N[uiLanguage];

const chatbotMessages = document.getElementById("chatbot-messages");
const chatbotForm = document.getElementById("chatbot-form");
const chatbotInput = document.getElementById("chatbot-input");
const chatbotResetBtn = document.getElementById("chatbot-reset-btn");

document.documentElement.lang = uiLanguage;
document.title = t.devbotPageTitle;
document.querySelectorAll("[data-i18n-placeholder]").forEach((el) => {
  el.placeholder = t[el.dataset.i18nPlaceholder];
});
document.querySelectorAll("[data-i18n-aria]").forEach((el) => {
  el.setAttribute("aria-label", t[el.dataset.i18nAria]);
});
document.querySelectorAll("[data-i18n-title]").forEach((el) => {
  el.title = t[el.dataset.i18nTitle];
});

let chatHistory = [];

function newChatSessionId() {
  const id = (window.crypto && window.crypto.randomUUID)
    ? window.crypto.randomUUID()
    : `${Date.now()}-${Math.random().toString(36).slice(2)}`;
  return `devbot-${id}`;
}
let chatSessionId = newChatSessionId();

function escapeHtml(text) {
  return text.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
}

function renderInlineMarkdown(escaped) {
  return escaped
    .replace(/`([^`]+)`/g, "<code>$1</code>")
    .replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>")
    .replace(/(^|[^*])\*([^*\s][^*]*?)\*(?!\*)/g, "$1<em>$2</em>")
    .replace(/\[([^\]]+)\]\((https?:\/\/[^)\s]+)\)/g, '<a href="$2" target="_blank" rel="noopener noreferrer">$1</a>');
}

// HTML-escaped first, then a small Markdown subset: paragraphs, headings
// (as bold lines), bullet/numbered lists, inline code/bold/italic/links.
// Unlike script.js's renderer, `_x_` is not italic here: replies are full
// of identifiers such as `_clean_blocked_slots`.
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
  if (role === "assistant") {
    bubble.innerHTML = renderMarkdown(text);
  } else {
    bubble.textContent = text;
  }
  chatbotMessages.appendChild(bubble);
  chatbotMessages.scrollTop = chatbotMessages.scrollHeight;
  return bubble;
}

// Reply bubble holding a waiting phrase until the first reply chunk
// replaces it (same as the main page's chat).
function appendChatPendingBubble(text) {
  const bubble = appendChatBubble("assistant", "");
  bubble.classList.add("chatbot-message-pending");
  bubble.textContent = text;
  return bubble;
}

function renderChatWelcome() {
  chatbotMessages.replaceChildren();
  appendChatBubble("assistant", t.devbotWelcome);
}

// Reads the `data: {"delta"}` / `data: {"error"}` / `data: [DONE]` event
// stream of POST /api/devchat, calling onDelta with the text so far.
async function readChatStream(response, onDelta) {
  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  let full = "";
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
        if (!full) throw new Error(event.error);
        return full;
      }
      if (event.delta) {
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
  chatbotInput.value = "";
  chatbotInput.disabled = true;
  appendChatBubble("user", message);
  const replyBubble = appendChatPendingBubble(t.chatbotPending);
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), CHAT_FETCH_TIMEOUT_MS);
  try {
    const response = await fetch("/api/devchat", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        message,
        history: chatHistory,
        language: uiLanguage,
        session_id: chatSessionId,
      }),
      signal: controller.signal,
    });
    if (!response.ok || !response.body) throw new Error(t.chatbotErrorFailed);
    const fullReply = await readChatStream(response, (textSoFar) => {
      replyBubble.classList.remove("chatbot-message-pending");
      replyBubble.innerHTML = renderMarkdown(textSoFar);
      chatbotMessages.scrollTop = chatbotMessages.scrollHeight;
    });
    if (!fullReply) throw new Error(t.chatbotErrorFailed);
    chatHistory.push({ role: "user", content: message });
    chatHistory.push({ role: "assistant", content: fullReply });
  } catch (err) {
    replyBubble.classList.remove("chatbot-message-pending");
    replyBubble.innerHTML = renderMarkdown(t.chatbotErrorFailed);
  } finally {
    clearTimeout(timer);
    chatbotInput.disabled = false;
    chatbotInput.focus();
    chatbotMessages.scrollTop = chatbotMessages.scrollHeight;
  }
});

chatbotResetBtn.addEventListener("click", () => {
  chatHistory = [];
  chatSessionId = newChatSessionId();
  chatbotInput.value = "";
  renderChatWelcome();
  chatbotInput.focus();
});

renderChatWelcome();
chatbotInput.focus();
