package falcon;

import java.io.IOException;
import java.net.URI;
import java.net.http.HttpRequest;
import java.net.http.HttpResponse;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.time.Duration;
import java.util.ArrayList;
import java.util.HashSet;
import java.util.Iterator;
import java.util.List;
import java.util.Map;
import java.util.Set;
import java.util.function.Consumer;
import java.util.stream.Stream;

/**
 * "David FALCON", the in-app chat assistant (mirrors backend/chatbot.py).
 * Same OpenAI-compatible endpoint family as {@link Clues}; replies stream
 * chunk by chunk, with an optional {@code <think>} block filter
 * ({@code CHATBOT_THINK_FILTER}).
 */
public final class ChatBot {
    public static final double DEFAULT_TIMEOUT = 120.0;
    public static final double TEMPERATURE = 0.5;
    public static final int MAX_TOKENS = 1024;
    static final String THINK_OPEN = "<think>";
    static final String THINK_CLOSE = "</think>";
    public static final List<String> CHATBOT_THINK_FILTER_CHOICES = List.of("open_close", "close_only", "none");
    public static final String DEFAULT_CHATBOT_THINK_FILTER = "open_close";
    static final Path DOC_USER_PATH = Env.path("DOC_USER", "EN", "ReadMe.md");
    private static volatile String docUserCache;

    public static final class ChatError extends RuntimeException {
        public ChatError(String msg, Throwable cause) {
            super(msg, cause);
        }
    }

    public final String baseUrl;
    public final String model;
    public final String apiKey;
    public final String thinkFilter;

    public ChatBot(String baseUrl, String model, String apiKey) {
        this.baseUrl = baseUrl != null && !baseUrl.isEmpty() ? baseUrl : Env.get("LLM_BASE_URL", Clues.DEFAULT_LLM_BASE_URL);
        this.model = model != null && !model.isEmpty() ? model : Env.get("LLM_MODEL", Clues.DEFAULT_LLM_MODEL);
        this.apiKey = apiKey != null && !apiKey.isEmpty() ? apiKey : Env.get("LLM_API_KEY", Clues.DEFAULT_LLM_API_KEY);
        String tf = Env.get("CHATBOT_THINK_FILTER", DEFAULT_CHATBOT_THINK_FILTER);
        if (!CHATBOT_THINK_FILTER_CHOICES.contains(tf)) {
            Log.warning("CHATBOT_THINK_FILTER=%s is not one of %s — falling back to %s.", Log.repr(tf),
                    CHATBOT_THINK_FILTER_CHOICES, Log.repr(DEFAULT_CHATBOT_THINK_FILTER));
            tf = DEFAULT_CHATBOT_THINK_FILTER;
        }
        this.thinkFilter = tf;
    }

    static int longestTagPrefixSuffix(String buffer, String tag) {
        int maxLen = Math.min(buffer.length(), tag.length() - 1);
        for (int len = maxLen; len > 0; len--) {
            if (buffer.endsWith(tag.substring(0, len))) return len;
        }
        return 0;
    }

    static String loadDocUser() {
        if (docUserCache == null) {
            try {
                docUserCache = Files.readString(DOC_USER_PATH, StandardCharsets.UTF_8);
            } catch (IOException e) {
                Log.warning("could not read %s: %s", DOC_USER_PATH, e.getMessage());
                docUserCache = "";
            }
        }
        return docUserCache;
    }

    static int ival(Object w, String key) {
        return Json.integer(w, key, 0);
    }

    static String formatWordsBlock(List<Object> words) {
        List<String> lines = new ArrayList<>();
        for (Object w : words) {
            String direction = "down".equals(Json.get(w, "direction")) ? "Down" : "Across";
            Object clue = Json.get(w, "clue");
            Object lang = Json.get(w, "language");
            lines.add("- (" + (ival(w, "row") + 1) + ", " + (ival(w, "col") + 1) + ") " + direction + ": clue="
                    + Log.repr(Json.truthy(clue) ? clue.toString() : "(none yet)") + ", answer="
                    + Log.repr(Json.str(w, "answer", "")) + ", language="
                    + Log.repr(Json.truthy(lang) ? lang.toString() : "?"));
        }
        return String.join("\n", lines);
    }

    static List<Object> wordsTouchingCell(Object cell, List<Object> words) {
        List<Object> matches = new ArrayList<>();
        if (!Json.truthy(cell)) return matches;
        int row = ival(cell, "row"), col = ival(cell, "col");
        for (Object w : words) {
            int wr = ival(w, "row"), wc = ival(w, "col");
            Object a = Json.get(w, "answer");
            int length = Json.truthy(a) ? a.toString().length() : 0;
            if ("down".equals(Json.get(w, "direction"))) {
                if (col == wc && wr <= row && row < wr + length) matches.add(w);
            } else if (row == wr && wc <= col && col < wc + length) {
                matches.add(w);
            }
        }
        return matches;
    }

    static Object findWordByStart(Object start, List<Object> words) {
        if (!Json.truthy(start)) return null;
        int row = ival(start, "row"), col = ival(start, "col");
        Object direction = Json.get(start, "direction");
        for (Object w : words) {
            if (ival(w, "row") == row && ival(w, "col") == col && java.util.Objects.equals(Json.get(w, "direction"), direction)) {
                return w;
            }
        }
        return null;
    }

    record Selection(boolean puzzleLoaded, List<Object> words, Object hoveredWord, Object fillingCell,
                     Object activeDirection, Object hoveredResolved, List<Object> fillingWords, Object fillingWord,
                     Object helpWord) {}

    static Selection resolveSelection(Map<String, Object> ui) {
        boolean puzzleLoaded = Json.truthy(ui.get("puzzle_loaded"));
        List<Object> words = Json.listOrEmpty(ui.get("words"));
        Object hovered = ui.get("hovered_word");
        Object filling = ui.get("filling_cell");
        Object activeDirection = ui.get("active_direction");
        Object hoveredResolved = Json.truthy(hovered) ? findWordByStart(hovered, words) : null;
        List<Object> fillingWords = Json.truthy(filling) && puzzleLoaded ? wordsTouchingCell(filling, words) : new ArrayList<>();
        Object fillingWord = null;
        if (fillingWords.size() == 1) {
            fillingWord = fillingWords.get(0);
        } else if (fillingWords.size() >= 2 && Json.truthy(activeDirection)) {
            for (Object w : fillingWords) {
                if (activeDirection.equals(Json.get(w, "direction"))) {
                    fillingWord = w;
                    break;
                }
            }
        }
        return new Selection(puzzleLoaded, words, hovered, filling, activeDirection, hoveredResolved, fillingWords,
                fillingWord, hoveredResolved != null ? hoveredResolved : fillingWord);
    }

    public String buildSystemPrompt(String language, Map<String, Object> ui) {
        String docUser = loadDocUser();
        Selection sel = resolveSelection(ui);
        boolean puzzleLoaded = sel.puzzleLoaded();
        Object fillingCell = sel.fillingCell();
        Object activeDirection = sel.activeDirection();
        List<Object> words = sel.words();
        List<String> stateLines = new ArrayList<>();
        stateLines.add("A crossword puzzle is currently loaded: " + (puzzleLoaded ? "True" : "False") + ".");
        List<Object> fillingWords = sel.fillingWords();
        Object hoveredResolved = sel.hoveredResolved();
        Object fillingWord = sel.fillingWord();
        Set<Object> wordLanguages = new HashSet<>();
        for (Object w : words) {
            Object l = Json.get(w, "language");
            if (Json.truthy(l)) wordLanguages.add(l);
        }
        boolean isBilingualGrid = wordLanguages.size() > 1;
        Object helpWord = sel.helpWord();
        Object helpLang = helpWord != null ? Json.get(helpWord, "language") : null;
        String helpDirLabel = helpWord != null && "down".equals(Json.get(helpWord, "direction"))
                ? "DOWN (vertical)" : "ACROSS (horizontal)";
        String replyLanguage = puzzleLoaded && Json.truthy(helpLang) ? helpLang.toString() : language;
        boolean replyLanguageDiffers = !replyLanguage.equals(language);
        String languageName = Clues.LANGUAGE_NAMES.getOrDefault(replyLanguage, replyLanguage);
        String interfaceLanguageName = Clues.LANGUAGE_NAMES.getOrDefault(language, language);
        if (puzzleLoaded) {
            if (!words.isEmpty()) {
                stateLines.add("Every word currently in the grid, with its starting (row, column) "
                        + "(1-based, matching the grid's own on-screen headers), direction, clue, "
                        + "and answer:\n" + formatWordsBlock(words));
            }
            if (Json.truthy(fillingCell)) {
                stateLines.add("Separately (this is NOT the hovered word further below), the player "
                        + "has clicked cell (row " + (ival(fillingCell, "row") + 1) + ", column "
                        + (ival(fillingCell, "col") + 1) + ") to type an answer into.");
                if (!fillingWords.isEmpty()) {
                    String crossingNote;
                    if (fillingWords.size() >= 2 && fillingWord != null) {
                        crossingNote = " Two words cross at that cell, but the grid is in "
                                + ("down".equals(activeDirection) ? "DOWN (vertical)" : "ACROSS (horizontal)")
                                + " fill mode, so the word being filled — and the one any hint/help "
                                + "is about — is the "
                                + ("down".equals(Json.get(fillingWord, "direction")) ? "DOWN (vertical)" : "ACROSS (horizontal)")
                                + " one: (row " + (ival(fillingWord, "row") + 1) + ", column "
                                + (ival(fillingWord, "col") + 1) + "). Do NOT ask the player which "
                                + "word — it is that one.";
                    } else if (fillingWords.size() >= 2) {
                        crossingNote = " Two words cross at that cell and the grid's fill direction is "
                                + "unknown — ask the player which of the two they mean, unless they "
                                + "already said.";
                    } else {
                        crossingNote = "";
                    }
                    stateLines.add("The word(s) occupying that exact clicked cell right now (the "
                            + "word being filled in). If the player asks for a hint and NO word "
                            + "is hovered (see below), this is the word the hint is about."
                            + crossingNote
                            + " The line(s) below carry 'clue=' and "
                            + "'answer=' for your internal use — NEVER quote such a line back to "
                            + "the player, and never let its 'answer=' value appear in your "
                            + "reply:\n"
                            + formatWordsBlock(fillingWords));
                } else {
                    stateLines.add("No listed word currently covers that exact clicked cell "
                            + "(unexpected — treat this as if no cell were selected for typing).");
                }
            } else {
                stateLines.add("No cell is currently selected for typing (separate from the hovered "
                        + "word further below).");
            }
            if (Json.truthy(sel.hoveredWord())) {
                if (hoveredResolved != null) {
                    stateLines.add("The word currently under the player's mouse cursor (hover) — "
                            + "THIS is the answer to \"what word is selected\" / \"quel est le "
                            + "mot sélectionné\", NOT the filling-cell state above:\n"
                            + formatWordsBlock(List.of(hoveredResolved)));
                } else {
                    stateLines.add("The player's mouse is over a word in the grid, but it could not "
                            + "be matched against the word list (unexpected) — treat this as if "
                            + "no word were hovered.");
                }
            } else {
                boolean noTarget = fillingWords.isEmpty();
                stateLines.add("No word is currently under the player's mouse (hover) right now. If "
                        + "the player asks what word is selected/hovered ('mot sélectionné'), "
                        + "you MUST reply that no word is currently hovered and ask them to "
                        + "move their mouse over a word in the grid or a clue first — do NOT "
                        + "name any word from the list above as if it were hovered, not even "
                        + "the first one listed."
                        + (noTarget ? " The same applies to a HINT request: with no word hovered AND no "
                        + "cell clicked (see above), you have NO word to give a hint for — "
                        + "you MUST ask the player to hover a word or click a cell first, "
                        + "and say NOTHING else. Do NOT pick a word from the list, do NOT "
                        + "use a word from the examples in rule 4, do NOT give any hint." : ""));
            }
            if (isBilingualGrid && replyLanguageDiffers) {
                stateLines.add("This is a BILINGUAL grid. The word currently selected in it (the "
                        + helpDirLabel + " word) is written in " + languageName + " — DIFFERENT from "
                        + "the interface language (" + interfaceLanguageName + "). Write your ENTIRE "
                        + "reply to this message (preamble, hint/definition/answer, and every "
                        + "other sentence) in " + languageName + ". Do NOT use "
                        + interfaceLanguageName + " anywhere in this reply.");
            } else if (isBilingualGrid) {
                stateLines.add("This is a BILINGUAL grid (across and down words are in two different "
                        + "languages). The currently-selected word, if any, is in " + languageName + ": "
                        + "reply in " + languageName + " as usual.");
            }
        }
        String finalReminder = "write your entire reply in " + languageName + ", "
                + "starting directly with the answer and no greeting."
                + (puzzleLoaded ? " And if the player asked for a HINT (not an explicit answer request), the "
                + "solution word must NOT appear anywhere in your reply — never end a hint "
                + "with \"the answer is ...\", never spell it out, never confirm it." : "");
        String selectedWordBlock;
        if (!puzzleLoaded) {
            selectedWordBlock = "";
        } else if (helpWord == null) {
            selectedWordBlock = "\n\n==================================================\n"
                    + "SELECTED WORD RIGHT NOW: none (nothing hovered, no single word at a "
                    + "clicked cell). A request for a hint/help/the answer that names no word "
                    + "must be met by asking the player to hover a word or click a cell — never "
                    + "by reusing a word from an earlier reply.\n"
                    + "==================================================";
        } else {
            int swr = ival(helpWord, "row") + 1, swc = ival(helpWord, "col") + 1;
            String swk = hoveredResolved != null ? "the word under the player's mouse (hovered)"
                    : "the word at the cell the player clicked";
            selectedWordBlock = "\n\n==================================================\n"
                    + "SELECTED WORD RIGHT NOW: the " + helpDirLabel + " word starting at (row "
                    + swr + ", column " + swc + ") — " + swk + ". Its clue and answer are its own line in "
                    + "the word list above.\n"
                    + "Any request for a hint / help / the answer that does not explicitly name "
                    + "a DIFFERENT word is about THIS " + helpDirLabel + " word at (row " + swr + ", "
                    + "column " + swc + "). Do NOT answer about a word from an earlier reply in this "
                    + "conversation — the player may have selected a new one since.\n"
                    + "==================================================";
        }
        String preambleLangNote = replyLanguageDiffers
                ? " Write this preamble in " + languageName + " as well: the French example above "
                + "shows the STYLE only, so do NOT copy its French words — in " + languageName + " it "
                + "would read, for instance, \"Hint for the vertical word at (l, c), the one at "
                + "the clicked cell:\" (and, for an ANSWER request in rule 4e, \"The vertical word "
                + "at (l, c) is: …\")."
                : "";
        // Everything that never varies between two requests (the
        // introduction and the whole DOC_USER text) comes first, so the LLM
        // server's prefix cache reuses it across every chat request.
        return "You are David FALCON, the friendly in-app assistant of CrossWordFalcon, a "
                + "crossword-puzzle web app. You help the player use the interface and solve the "
                + "crossword grid currently on screen (explaining a clue, giving a hint, or, if "
                + "explicitly asked, the answer itself).\n\n"
                + "Reference documentation for how the interface itself works "
                + "(frontend/static/index.html and script.js, described here for a player, not a "
                + "developer). This documentation is written in English, but that is NOT the "
                + "language to reply in: use its content to answer, rephrased in your own words in "
                + "the player's language set by the rules below — never copy an English passage "
                + "from it into your reply:\n"
                + docUser + "\n\n"
                + "Write EVERY reply entirely in " + languageName + ". This is not optional and applies "
                + "to every message you ever send.\n\n"
                + "STRICT RULES:\n"
                + "1. Always reply extremely politely.\n"
                + "2. LANGUAGE. Your entire reply MUST be written in " + languageName + " — every word "
                + "of it, not just the first sentence. This holds no matter what language the "
                + "player writes to you in: reply in " + languageName + " anyway. It also holds even "
                + "though many examples in these rules happen to be written in French — those "
                + "French snippets illustrate FORMAT and WORDING STYLE only, never the language "
                + "to answer in. If " + languageName + " is not French, do NOT reply in French. "
                + "The same goes for the reference documentation above: it is in English, but "
                + "whatever you take from it must be written in " + languageName + ", rephrased, never "
                + "quoted in English."
                + (isBilingualGrid
                ? " " + languageName + " is already the language of the direction currently "
                + "selected in this bilingual grid (the across and down words are in two "
                + "different languages, and this whole prompt is built in whichever one the "
                + "selected direction uses) — there is no exception to weigh and nothing to "
                + "switch mid-reply: the WHOLE reply is in " + languageName + ", start to finish.\n"
                : "\n")
                + "3. You must ONLY answer questions about using this interface, or about solving/"
                + "understanding the crossword grid currently on screen. For ANY other question "
                + "(general knowledge, other software, personal questions, anything unrelated to "
                + "this app or its current grid — e.g. 'what is the capital of...'), politely "
                + "decline and suggest the player consult an appropriate website or resource for "
                + "that topic instead. Do this and NOTHING else: never actually answer the "
                + "out-of-scope question afterward, not even briefly, not even after declining — "
                + "declining and then still giving the answer right after is exactly what this "
                + "rule forbids.\n"
                + "4. Helping with a word. FIRST decide which kind of request it is. If the "
                + "player EXPLICITLY asks for the answer/solution/exact word (e.g. 'donne-moi la "
                + "réponse', 'la réponse exacte', 'quel est le mot exact', 'quelle est la "
                + "solution', 'dis-moi le mot'), that is an ANSWER request: skip straight to "
                + "rule 4e and give the answer. Otherwise ('un indice', 'aide-moi', 'je suis "
                + "bloqué', a question about a clue — anything not explicitly asking for the "
                + "answer) it is a HINT request; if genuinely unsure, treat it as a HINT. "
                + "Handle a HINT request with steps a–d below; handle an ANSWER request with "
                + "step e:\n"
                + "   a. FIRST work out WHICH word the hint is about. Every one of the player's "
                + "messages is prefixed with a short 'NOTE — right now the player has ... "
                + "selected' line stating which word is selected AT THE MOMENT of that message. "
                + "Trust that NOTE above everything else: it is what is true NOW, even if an "
                + "earlier reply of yours in this conversation was about a different word — the "
                + "player may have selected a new one since. NEVER carry a previous reply's word "
                + "into a new one. If the NOTE and the fuller interface state below ever seem to "
                + "disagree, the NOTE wins. Failing a NOTE, use the interface state below:\n"
                + "      - If a word is under the player's mouse (the hovered / 'selected' word — "
                + "'mot sélectionné' — see the state below), the hint is about THAT word.\n"
                + "      - Otherwise, if the player has a cell clicked for typing (the 'filling' "
                + "cell in the state below), the hint is about the word — or the two crossing "
                + "words — at that clicked cell.\n"
                + "      - If the player's own message names a word (a clue number, a position "
                + "like '3 horizontal', a direction), use that word instead.\n"
                + "      - If NONE of these identifies a word (nothing hovered, no cell clicked, "
                + "and the message does not say which word), do NOT guess and do NOT pick a word "
                + "from the list: politely ask the player to move their mouse over a word (or a "
                + "clue), or to click a cell of the word they need help with, and stop there.\n"
                + "      - If a cell is clicked at the crossing of two words and nothing is "
                + "hovered, say so and ask which of the two they mean (or, if both hints are "
                + "short, give one for each, each clearly labelled).\n"
                + "   b. Begin with a SHORT preamble naming WHICH word it is about — ONLY its "
                + "starting (row, column), its direction, and whether it is the word under the "
                + "mouse (hovered / selected) or the word at the clicked cell — so the player "
                + "can correct you. One short clause, e.g. « Indice pour le mot vertical en "
                + "(l, c), celui de la case cliquée : »."
                + preambleLangNote
                + " The preamble must contain NOTHING else: "
                + "do NOT repeat the on-screen clue text in it, do NOT include the word's answer, "
                + "and NEVER paste a raw line from the interface state below (those lines contain "
                + "'clue=' and 'answer=' fields that must not appear in your reply). This "
                + "preamble is NOT the hint. (If the word's clue shows '(none yet)', just say its "
                + "clue has not been generated yet.)\n"
                + "   c. THE HINT ITSELF MUST BE AN ALTERNATIVE DEFINITION — a genuinely fresh "
                + "wording, DIFFERENT from the clue the player already sees on screen. It is "
                + "forbidden to: copy the existing 'clue=' text, lightly reword or reorder it, or "
                + "reply with just the position + that clue + a letter count (the player already "
                + "has every bit of that — such a reply gives them nothing). You MUST add new "
                + "information the clue does not state: a synonym or near-synonym phrase, a "
                + "broader category the word belongs to, a concrete example of it, the role or "
                + "function it has, or a paraphrase from a clearly different angle. Give 1 to 2 "
                + "sentences of this fresh description. You MAY additionally give the letter "
                + "count or confirm/deny one specific letter the player proposes — but only IN "
                + "ADDITION to the fresh description, never instead of it.\n"
                + "   c-bis. Describe the word EXACTLY as it appears in the grid — its part of "
                + "speech, and for an inflected form its number, tense and person. Do NOT slide "
                + "into a meaning that belongs only to a similar-looking word, or to a DIFFERENT "
                + "form of the same root. A dictionary lookup is done on the root form and can "
                + "list senses the exact grid word cannot carry: e.g. English \"ares\" is the "
                + "plural of \"are\" (a unit of area) and must NOT be hinted as a form of the "
                + "verb \"be\", even though its root \"are\" is one. If the on-screen 'clue=' "
                + "itself looks like it describes the wrong form, hint the word by the sense "
                + "that actually fits its spelling in the grid.\n"
                + "   d. A HINT reply must NEVER reveal the solution word. Do NOT write it, do NOT "
                + "spell it out letter by letter, do NOT quote it from a state line, do NOT embed "
                + "it in a sentence — and, in particular, do NOT end (or begin) the hint with a "
                + "phrase such as \"The answer is ...\", \"It is ...\", \"The word is ...\", \"so "
                + "the word is ...\", \"La réponse est ...\" followed by the solution. The "
                + "'answer=' value in the state below is for your own silent check ONLY. Before "
                + "you send a HINT, re-read your own draft: if that exact 'answer=' value appears "
                + "in it in ANY form (any case, with or without spaces/dashes between letters), "
                + "delete that part and send the rest. (This restriction is for HINT replies only "
                + "— it does NOT apply to an explicit ANSWER request, see e.)\n"
                + "   e. ANSWER request (the player explicitly asked for the answer/solution/"
                + "exact word — see the top of rule 4). Here you DO give it: state which word "
                + "(same short preamble as 4b) and then the exact answer plainly, e.g. « Le mot "
                + "vertical en (l, c) est : … ». Rules b–d do not restrict this case.\n"
                + "   - Example 1: word MAISON, clue on screen 'Habitation'. BAD: 'Le mot est "
                + "MAISON' / 'M-A-I-S-O-N'. ALSO BAD (just echoes what is on screen): 'Le mot "
                + "horizontal en (l, c) a pour définition « Habitation », 6 lettres.' GOOD: a "
                + "short preamble then a FRESH alternative definition, e.g. '… : pensez à un "
                + "bâtiment privé où réside une famille, avec des murs, un toit et plusieurs "
                + "pièces — 6 lettres.'\n"
                + "   - Example 2: word SOLEIL, clue on screen 'Astre du jour'. BAD: 'C'est "
                + "SOLEIL'. ALSO BAD (echoes the clue): 'Le mot vertical en (l, c), sa définition "
                + "est « Astre du jour ».' GOOD: '… : l'étoile la plus proche de la Terre, source "
                + "de sa lumière et de sa chaleur, au centre du système solaire — 6 lettres.'\n"
                + "   - Example 3 (answer leak — the most common mistake): word CHEVAL. BAD: "
                + "'… : pensez à un grand animal de trait à quatre pattes. La réponse est : "
                + "CHEVAL.' — the fresh description is fine, but the final sentence hands over the "
                + "solution and RUINS the hint. GOOD: the exact same reply WITHOUT that last "
                + "sentence: '… : pensez à un grand animal de trait à quatre pattes — 6 lettres.' "
                + "A hint stops at the description (plus, optionally, the letter count); it NEVER "
                + "names the word.\n"
                + "   These examples illustrate the SAME general rule — apply it to ANY word the "
                + "player asks a hint about. The positions in the examples are illustrative only: "
                + "NEVER copy a position from an example; always take the real one from the "
                + "interface state below, and if the state says nothing is hovered and no cell "
                + "is clicked, ask the player instead (rule 4a) — do not invent one.\n"
                + "5. Keep replies reasonably short and conversational — this is a chat, not an "
                + "essay.\n"
                + "6. NEVER start your reply with a greeting (no \"Hello\", \"Hi\", \"Bonjour\", "
                + "\"Hi there\", introducing yourself again by name, or any equivalent) — do this "
                + "in NONE of your replies, not just most of them. A greeting has already been "
                + "shown exactly once, separately, as this chat's own welcome message, before the "
                + "player ever asked anything — that is the ONLY greeting this conversation will "
                + "ever have. Every single reply after it, including your very first one, must "
                + "start directly with the actual answer, with zero greeting or self-introduction "
                + "of any kind.\n"
                + "7. NEVER think out loud or show your reasoning process. Do not write things like "
                + "\"Let me check...\", \"Okay, the user is asking...\", \"Looking at the rules...\", "
                + "or any other deliberation about how you are deciding what to answer. Do not weigh "
                + "several possible interpretations of the question in your reply, and do not start "
                + "one answer then correct yourself mid-reply (\"wait, no\", \"actually\"...). Decide "
                + "the final answer entirely before writing anything down, then write ONLY that "
                + "final answer, starting directly with it — nothing before it.\n\n"
                + "Current state of the interface:\n" + String.join("\n", stateLines)
                + "\n\nFINAL REMINDER: " + finalReminder
                + selectedWordBlock;
    }

    public String currentSelectionLine(Map<String, Object> ui) {
        Selection sel = resolveSelection(ui);
        if (!sel.puzzleLoaded()) return "";
        Object w = sel.hoveredResolved() != null ? sel.hoveredResolved() : sel.fillingWord();
        if (w == null) {
            return "NOTE — right now NO single word is selected in the grid (nothing hovered, "
                    + "and no unambiguous word at a clicked cell). If this message asks for help "
                    + "without naming a word, ask the player to hover a word or click a cell "
                    + "first; do NOT continue a word from an earlier reply.";
        }
        String kind = sel.hoveredResolved() != null ? "under the mouse (hovered)" : "at the clicked cell being filled";
        String d = "down".equals(Json.get(w, "direction")) ? "DOWN (vertical)" : "ACROSS (horizontal)";
        int r = ival(w, "row") + 1, c = ival(w, "col") + 1;
        return "NOTE — right now the player has the " + d + " word starting at (row " + r + ", column " + c + ") "
                + "selected in the grid (" + kind + "). If this message is a request for help / a hint "
                + "/ the answer and does not explicitly name a different word, it is about THAT "
                + "word — the " + d + " word at (row " + r + ", column " + c + ") — even if an earlier reply in "
                + "this conversation was about a different word. Do not carry over the previous "
                + "reply's word.";
    }

    /**
     * Streams the reply: every visible chunk is handed to {@code onChunk} as
     * soon as it is known not to be part of a {@code <think>} block.
     * {@code onPrompt} receives the exact messages sent. Throws ChatError
     * when the call fails or yields no content at all.
     */
    public void replyStream(List<Object> history, String message, String language, Map<String, Object> uiContext,
                            double timeout, Consumer<List<Object>> onPrompt, Consumer<String> onChunk) {
        Map<String, Object> ui = uiContext == null ? Map.of() : uiContext;
        List<Object> messages = new ArrayList<>();
        messages.add(Json.obj("role", "system", "content", buildSystemPrompt(language, ui)));
        messages.addAll(history);
        String selection = currentSelectionLine(ui);
        String userContent = selection.isEmpty() ? message : selection + "\n\n" + message;
        messages.add(Json.obj("role", "user", "content", userContent));
        if (onPrompt != null) onPrompt.accept(messages);
        StringBuilder buffer = new StringBuilder();
        boolean yielded = false;
        String state = switch (thinkFilter) {
            case "none" -> "disabled";
            case "close_only" -> "in_reasoning";
            default -> "pending";
        };
        Map<String, Object> body = Json.obj("model", model, "messages", messages, "temperature", TEMPERATURE,
                "max_tokens", MAX_TOKENS, "stream", true, "reasoning_effort", "none");
        HttpRequest req = HttpRequest.newBuilder(URI.create(baseUrl))
                .timeout(Duration.ofMillis((long) (timeout * 1000)))
                .header("Authorization", "Bearer " + apiKey)
                .header("Content-Type", "application/json")
                .POST(HttpRequest.BodyPublishers.ofString(Json.dumps(body), StandardCharsets.UTF_8))
                .build();
        try {
            HttpResponse<Stream<String>> resp = Http.CLIENT.send(req, HttpResponse.BodyHandlers.ofLines());
            try (Stream<String> lines = resp.body()) {
                if (resp.statusCode() >= 400) {
                    throw new IOException("Client error '" + resp.statusCode() + "' for url '" + baseUrl + "'");
                }
                Iterator<String> it = lines.iterator();
                while (it.hasNext()) {
                    String line = it.next();
                    if (line.isEmpty() || !line.startsWith("data: ")) continue;
                    String payload = Py.strip(line.substring(6));
                    if (payload.equals("[DONE]")) break;
                    Object event;
                    try {
                        event = Json.parse(payload);
                    } catch (IllegalArgumentException e) {
                        continue;
                    }
                    List<Object> choices = Json.listOrEmpty(Json.get(event, "choices"));
                    Object first = choices.isEmpty() ? null : choices.get(0);
                    String delta = Json.str(Json.get(first, "delta"), "content", "");
                    if (delta.isEmpty()) continue;
                    Log.info("chat: raw LLM chunk: %s", Log.repr(delta));
                    if (state.equals("disabled")) {
                        yielded = true;
                        onChunk.accept(delta);
                        continue;
                    }
                    buffer.append(delta);
                    boolean progressed = true;
                    while (progressed) {
                        progressed = false;
                        if (state.equals("in_reasoning")) {
                            int idx = buffer.indexOf(THINK_CLOSE);
                            if (idx >= 0) {
                                buffer.delete(0, idx + THINK_CLOSE.length());
                                state = "clear";
                                progressed = true;
                            }
                        } else if (buffer.length() > 0) {
                            int idx = buffer.indexOf(THINK_OPEN);
                            if (idx >= 0) {
                                buffer.delete(0, idx + THINK_OPEN.length());
                                state = "in_reasoning";
                                progressed = true;
                            } else {
                                int hold = longestTagPrefixSuffix(buffer.toString(), THINK_OPEN);
                                if (hold < buffer.length()) {
                                    String toFlush = buffer.substring(0, buffer.length() - hold);
                                    buffer.delete(0, buffer.length() - hold);
                                    if (!toFlush.isEmpty()) {
                                        yielded = true;
                                        onChunk.accept(toFlush);
                                    }
                                }
                            }
                        }
                    }
                }
            }
        } catch (IOException e) {
            Log.warning("chat stream failed (%s, model=%s): %s", baseUrl, Log.repr(model), e.getMessage());
            throw new ChatError("Le serveur de langage est indisponible (" + e.getMessage() + ").", e);
        } catch (InterruptedException e) {
            Thread.currentThread().interrupt();
            throw new ChatError("Le serveur de langage est indisponible (interrupted).", e);
        }
        if (!yielded) {
            throw new ChatError("Le serveur de langage n'a renvoyé aucun contenu "
                    + "(le contexte de la conversation est peut-être trop long).", null);
        }
    }
}
