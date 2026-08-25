/**
 * JuziGenius (句子Genius) - Main Application Controller
 */

// Global App State
const state = {
    sentences: [],
    currentIndex: 0,
    charIndex: 0,
    hintTier: 0,
    writer: null,
    currentScorePenalty: 0,
    totalUnlockedCount: 0,
    totalDueCount: 0,
    ttsVoiceURI: localStorage.getItem("juzi_tts_voice_uri") || null,
    isCompleted: false,
    importMode: "paste",
    providers: []
};

const SUBMIT_LABELS = { paste: "Process & Unlock", hsk: "Get Sentences", ai: "Generate", suggest: "Add Selected" };

// Global audio reference to prevent garbage collection
let currentUtterance = null;

// DOM Elements Cache
const elements = {};

document.addEventListener("DOMContentLoaded", () => {
    cacheDomElements();
    initEventListeners();
    fetchNewSession();
    // Chrome loads its voice list asynchronously; kick it off early so it's
    // ready by the time a sentence completes and the victory card needs it.
    if ("speechSynthesis" in window) window.speechSynthesis.getVoices();
});

function cacheDomElements() {
    elements.englishPrompt = document.getElementById("english-prompt");
    elements.assemblyLine = document.getElementById("assembly-line");
    elements.canvasContainer = document.getElementById("tian-zi-ge");
    elements.btnClear = document.getElementById("btn-clear");
    elements.btnHint = document.getElementById("btn-hint");
    elements.btnDiscuss = document.getElementById("btn-discuss");
    elements.btnImport = document.getElementById("btn-import");
    elements.counterValue = document.getElementById("counter-value");
    elements.dueCounter = document.getElementById("due-counter");
    elements.dueCounterValue = document.getElementById("due-counter-value");
    
    // Import Modal Elements
    elements.importModal = document.getElementById("import-modal");
    elements.modalBtnCancel = document.getElementById("modal-btn-cancel");
    elements.modalBtnSubmit = document.getElementById("modal-btn-submit");
    elements.importTextarea = document.getElementById("import-textarea");
    elements.modeTabs = document.querySelectorAll(".mode-tab");
    elements.modePanels = document.querySelectorAll(".mode-panel");
    elements.aiProviderSelect = document.getElementById("ai-provider-select");
    elements.aiApiKeyInput = document.getElementById("ai-api-key-input");
    elements.aiKeyLabel = document.getElementById("ai-key-label");
    elements.aiKeyRow = document.getElementById("ai-key-row");
    elements.suggestionsList = document.getElementById("suggestions-list");
}

function initEventListeners() {
    if (elements.btnClear) elements.btnClear.addEventListener("click", handleClearCanvas);
    if (elements.btnHint) elements.btnHint.addEventListener("click", handleHintEscalation);
    
    if (elements.btnDiscuss) {
        elements.btnDiscuss.addEventListener("click", () => {
            alert("Sentence discussion and grammar breakdown feature coming soon!");
        });
    }

    // Import Modal Event Listeners
    if (elements.btnImport) {
        elements.btnImport.addEventListener("click", () => {
            if (elements.importModal) {
                elements.importModal.style.display = "flex";
                elements.importTextarea.value = "";
                switchImportMode("paste");
                elements.importTextarea.focus();
                if (state.providers.length === 0) loadProviders();
            }
        });
    }

    elements.modeTabs.forEach(tab => {
        tab.addEventListener("click", () => switchImportMode(tab.dataset.mode));
    });

    if (elements.aiProviderSelect) {
        elements.aiProviderSelect.addEventListener("change", updateAiKeyFieldState);
    }

    if (elements.modalBtnCancel) {
        elements.modalBtnCancel.addEventListener("click", () => {
            if (elements.importModal) elements.importModal.style.display = "none";
        });
    }

    if (elements.modalBtnSubmit) {
        elements.modalBtnSubmit.addEventListener("click", handleModalSubmit);
    }

    // Keyboard navigation: Press Space or Enter to load Next sentence when completed
    window.addEventListener("keydown", (e) => {
        if (state.isCompleted && (e.code === "Space" || e.code === "Enter")) {
            e.preventDefault();
            nextSentence();
        }
    });
}

/**
 * Fetches session sentences and metadata from the Python local server.
 */
async function fetchNewSession() {
    elements.englishPrompt.textContent = "Loading session from offline bank...";
    try {
        const response = await fetch('/api/session');
        if (!response.ok) throw new Error("Failed to fetch session from backend engine.");
        
        const data = await response.json();
        
        // Handle server envelope { sentences, total_unlocked_count } or legacy array format
        if (data && typeof data === 'object' && !Array.isArray(data)) {
            state.sentences = data.sentences || [];
            state.totalUnlockedCount = data.total_unlocked_count || 0;
            state.totalDueCount = data.total_due_count || 0;
        } else if (Array.isArray(data)) {
            state.sentences = data;
        }

        updateCharacterCounter();
        updateDueCounter();

        if (state.sentences.length > 0) {
            state.currentIndex = 0;
            loadSession();
        } else {
            elements.englishPrompt.textContent = "Your unlocked character pool is empty. Click 'Import' to paste Chinese text and begin!";
            if (elements.canvasContainer) elements.canvasContainer.innerHTML = "";
            if (elements.assemblyLine) elements.assemblyLine.innerHTML = "";
        }
    } catch (err) {
        console.error(err);
        elements.englishPrompt.textContent = "Connection error with Python backend engine.";
    }
}

/**
 * Loads the active session sentence and prepares the character canvas.
 */
function loadSession() {
    state.isCompleted = false;
    toggleSidebarButtons(true);

    const currentSentence = state.sentences[state.currentIndex];
    if (!currentSentence) return;

    elements.englishPrompt.textContent = currentSentence.english;
    state.charIndex = 0;
    state.hintTier = 0;
    state.currentScorePenalty = 0;

    advancePastPunctuation();
    renderAssemblyLine();
    setupCurrentCharacterWriter();
}

function updateCharacterCounter() {
    if (elements.counterValue) {
        elements.counterValue.textContent = state.totalUnlockedCount;
    }
}

/**
 * Reflects state.totalDueCount in the top bar: the count of unlocked
 * characters currently due for SM-2 review (never reviewed, or past their
 * interval). Highlighted when non-zero so there's a visible cue that
 * review is waiting, not just silent scheduling in the background.
 */
function updateDueCounter() {
    if (elements.dueCounterValue) {
        elements.dueCounterValue.textContent = state.totalDueCount;
    }
    if (elements.dueCounter) {
        elements.dueCounter.classList.toggle("has-due", state.totalDueCount > 0);
    }
}

function toggleSidebarButtons(enabled) {
    if (elements.btnClear) elements.btnClear.disabled = !enabled;
    if (elements.btnHint) elements.btnHint.disabled = !enabled;
}

/**
 * Auto-completes and skips past any punctuation characters starting from state.charIndex.
 */
function advancePastPunctuation() {
    const currentSentence = state.sentences[state.currentIndex];
    if (!currentSentence) return;
    const chineseChars = Array.from(currentSentence.chinese);

    while (state.charIndex < chineseChars.length && isPunctuation(chineseChars[state.charIndex])) {
        state.charIndex++;
    }
}

/**
 * Renders the slot blocks for the current sentence.
 */
function renderAssemblyLine() {
    if (!elements.assemblyLine) return;
    elements.assemblyLine.innerHTML = "";
    
    const currentSentence = state.sentences[state.currentIndex];
    if (!currentSentence) return;

    const chineseChars = Array.from(currentSentence.chinese);

    chineseChars.forEach((char, idx) => {
        const slot = document.createElement("div");
        slot.className = "character-slot";
        slot.id = `slot-${idx}`;

        if (isPunctuation(char)) {
            slot.classList.add("punctuation-slot");
            slot.textContent = char;
        } else {
            slot.textContent = idx < state.charIndex ? char : "_";
            if (idx === state.charIndex && !state.isCompleted) {
                slot.classList.add("active");
            }
        }
        elements.assemblyLine.appendChild(slot);
    });
}

/**
 * Instantiates Hanzi Writer inside the Tian Zi Ge canvas for the active character.
 */
function setupCurrentCharacterWriter() {
    const currentSentence = state.sentences[state.currentIndex];
    if (!currentSentence) return;

    advancePastPunctuation();
    const chineseChars = Array.from(currentSentence.chinese);

    const container = document.getElementById('tian-zi-ge');
    if (!container) return;

    const hintContainer = document.getElementById("hint-display-container");
    if (hintContainer) hintContainer.textContent = "";

    // Sentence auto-completion condition
    if (state.charIndex >= chineseChars.length) {
        triggerSentenceCompletion();
        return;
    }

    const targetChar = chineseChars[state.charIndex];
    container.innerHTML = "";

    state.writer = HanziWriter.create('tian-zi-ge', targetChar, {
        width: 240,
        height: 240,
        padding: 10,
        showCharacter: false,
        showOutline: false,   
        strokeAnimationSpeed: 2,
        delayBetweenStrokes: 150,
        leniency: 1.0,
        highlightColor: '#e74c3c',
        drawingColor: '#000000',
        strokeColor: '#333333',
        outlineColor: '#b0b0b0'
    });

    state.writer.quiz({
        onCorrectStroke: () => {},
        onMistake: () => {},
        onComplete: () => {
            handleCharacterSuccess(targetChar);
        }
    });

    renderAssemblyLine();
}

/**
 * Handles actions taken when a character canvas quiz is completed successfully.
 */
function handleCharacterSuccess(char) {
    const slot = document.getElementById(`slot-${state.charIndex}`);
    if (slot) {
        slot.textContent = char;
        slot.classList.remove("active");
    }

    submitCharacterReview(char, state.hintTier);

    state.charIndex++;
    state.hintTier = 0;
    setTimeout(setupCurrentCharacterWriter, 200);
}

/**
 * Reports a completed character quiz to the backend so its SM-2 scheduling
 * fields (interval/factor/reps/last) advance. Recall quality (0-5) is
 * derived from how many hint tiers were needed before completion: no hints
 * is a perfect 5, each tier escalation lowers it, floored at 2 (SM-2 treats
 * anything under 3 as a failed recall and resets the repetition streak).
 * Fire-and-forget -- a failed request shouldn't block practice.
 */
function submitCharacterReview(char, hintTier) {
    const quality = Math.max(2, 5 - hintTier);
    fetch('/api/character/review', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ char, quality })
    })
        .then(response => response.ok ? response.json() : null)
        .then(result => {
            if (result && result.due_count !== undefined) {
                state.totalDueCount = result.due_count;
                updateDueCounter();
            }
        })
        .catch(err => console.error("Could not record character review.", err));
}

/**
 * Celebratory auto-completion routine: green flash, native TTS, and victory card display.
 */
function triggerSentenceCompletion() {
    state.isCompleted = true;
    toggleSidebarButtons(false);

    const currentSentence = state.sentences[state.currentIndex];
    if (!currentSentence) return;

    // 1. Flash all slots green
    const slots = document.querySelectorAll(".character-slot");
    slots.forEach(s => s.classList.add("success-flash"));

    // 2. Trigger native audio speech playback
    playNativeTTS(currentSentence.chinese);

    // 3. Render Victory Card with Mascot, audio controls, and Next button inside Tian Zi Ge
    const container = document.getElementById('tian-zi-ge');
    if (container) {
        container.innerHTML = `
            <div class="victory-card">
                <img src="mascot.png" alt="Juzi Mascot" class="victory-mascot" />
                <div class="victory-title">太棒了! Well Done!</div>
                <div class="victory-audio-controls">
                    <button id="btn-replay-audio" class="btn-audio-action" title="Replay pronunciation">🔊 Replay</button>
                    <button id="btn-switch-voice" class="btn-audio-action" title="Switch voice">🔄 Switch Voice</button>
                </div>
                <button id="btn-next" class="btn-next">Next Sentence →</button>
            </div>
        `;

        const btnNext = document.getElementById("btn-next");
        if (btnNext) {
            btnNext.addEventListener("click", nextSentence);
        }

        const btnReplay = document.getElementById("btn-replay-audio");
        if (btnReplay) {
            btnReplay.addEventListener("click", () => playNativeTTS(currentSentence.chinese));
        }

        const btnSwitchVoice = document.getElementById("btn-switch-voice");
        if (btnSwitchVoice) {
            updateSwitchVoiceButton(btnSwitchVoice);
            btnSwitchVoice.addEventListener("click", () => {
                switchToNextVoice();
                updateSwitchVoiceButton(btnSwitchVoice);
                playNativeTTS(currentSentence.chinese);
            });
        }
    }
}

/**
 * Advances to the next sentence in the session, looping back to the start
 * once the bank is exhausted. Getting a new batch is a deliberate user
 * action via the Import modal (Paste / HSK / Generate with AI), not automatic.
 */
function nextSentence() {
    if (!state.sentences || state.sentences.length === 0) return;
    state.currentIndex = (state.currentIndex + 1) % state.sentences.length;
    loadSession();
}

/**
 * The Hint Staircase Engine: Progressive tiered assistance.
 */
function handleHintEscalation() {
    if (state.isCompleted) return;

    const currentSentence = state.sentences[state.currentIndex];
    if (!currentSentence) return;

    const chineseChars = Array.from(currentSentence.chinese);
    if (state.charIndex >= chineseChars.length) return;

    state.hintTier++;
    const targetChar = chineseChars[state.charIndex];

    if (state.hintTier === 1) {
        state.currentScorePenalty = Math.max(state.currentScorePenalty, 1);
        showPinyinPush(targetChar, currentSentence);
    } else if (state.hintTier === 2) {
        state.currentScorePenalty = Math.max(state.currentScorePenalty, 2);
        if (state.writer) {
            state.writer.showOutline();
        }
    } else if (state.hintTier >= 3) {
        state.currentScorePenalty = 3;
        if (state.writer) {
            // Tier 3: Master Class fast 4.5x speed stroke walkthrough
            state.writer.animateCharacter({
                strokeSpeed: 4.5
            });
        }
    }
}

function showPinyinPush(char, sentenceObj) {
    const hintContainer = document.getElementById("hint-display-container");
    if (!hintContainer) return;

    const charMeta = sentenceObj.char_metadata && sentenceObj.char_metadata[char];
    const pinyinResult = charMeta && charMeta.pinyin ? charMeta.pinyin : "pīn yīn";
    
    hintContainer.textContent = `Hint (Tier 1 Pinyin): ${pinyinResult}`;
}

/**
 * Clears the active character canvas so the user can retry writing from scratch.
 */
function handleClearCanvas() {
    if (state.isCompleted) return;
    if (state.writer) {
        state.writer.cancelQuiz();
        setupCurrentCharacterWriter();
    }
}

/**
 * Switches the Import modal between its three modes: paste-your-own-text,
 * pick from local HSK sentences, or generate new ones with an AI provider.
 */
function switchImportMode(mode) {
    state.importMode = mode;

    elements.modeTabs.forEach(tab => {
        tab.classList.toggle("active", tab.dataset.mode === mode);
    });
    elements.modePanels.forEach(panel => {
        panel.hidden = panel.dataset.modePanel !== mode;
    });

    elements.modalBtnSubmit.textContent = SUBMIT_LABELS[mode] || "Submit";

    if (mode === "suggest") loadSuggestions();
}

/**
 * Loads which AI providers exist and whether a server-side key is already
 * configured for them, then populates the provider dropdown.
 */
async function loadProviders() {
    try {
        const response = await fetch('/api/providers');
        if (!response.ok) return;
        const data = await response.json();
        state.providers = data.providers || [];
        renderProviderOptions();
    } catch (err) {
        console.error("Could not load AI provider list.", err);
    }
}

function renderProviderOptions() {
    if (!elements.aiProviderSelect) return;
    elements.aiProviderSelect.innerHTML = "";

    state.providers.forEach(provider => {
        const option = document.createElement("option");
        option.value = provider.id;
        option.textContent = provider.server_configured
            ? `${provider.label} (server key configured)`
            : provider.label;
        elements.aiProviderSelect.appendChild(option);
    });

    updateAiKeyFieldState();
}

/**
 * Prefills the API key field from this browser's localStorage (client-held
 * only -- never persisted server-side) and labels it optional/required
 * depending on whether the selected provider has a server-configured key.
 */
function updateAiKeyFieldState() {
    if (!elements.aiProviderSelect || !elements.aiApiKeyInput) return;

    const providerId = elements.aiProviderSelect.value;
    const provider = state.providers.find(p => p.id === providerId);

    elements.aiApiKeyInput.value = localStorage.getItem(`juzi_api_key_${providerId}`) || "";
    elements.aiKeyLabel.textContent = provider && provider.server_configured
        ? "API Key (optional — server key configured)"
        : "API Key (required)";
}

/**
 * Dispatches the modal's Submit button to the handler for the active mode.
 */
function handleModalSubmit() {
    if (state.importMode === "hsk") {
        handleGenerateSession("hsk");
    } else if (state.importMode === "ai") {
        handleGenerateSession("ai");
    } else if (state.importMode === "suggest") {
        handleAddSuggestedWords();
    } else {
        handleTextImport();
    }
}

/**
 * Fetches the highest-frequency compound words the user hasn't added yet
 * and renders them as a checkbox list in the "Suggest Words" tab.
 */
async function loadSuggestions() {
    if (!elements.suggestionsList) return;
    elements.suggestionsList.innerHTML = `<p class="suggestions-empty">Loading suggestions...</p>`;

    try {
        const response = await fetch('/api/suggestions');
        if (!response.ok) throw new Error("Failed to fetch word suggestions.");
        const data = await response.json();
        renderSuggestions(data.suggestions || []);
    } catch (err) {
        console.error(err);
        elements.suggestionsList.innerHTML = `<p class="suggestions-empty">Could not load suggestions.</p>`;
    }
}

function renderSuggestions(suggestions) {
    if (!elements.suggestionsList) return;
    elements.suggestionsList.innerHTML = "";

    if (suggestions.length === 0) {
        elements.suggestionsList.innerHTML = `<p class="suggestions-empty">No new words to suggest &mdash; you've added them all!</p>`;
        return;
    }

    suggestions.forEach((item, idx) => {
        const row = document.createElement("label");
        row.className = "suggestion-row";

        const checkbox = document.createElement("input");
        checkbox.type = "checkbox";
        checkbox.className = "suggestion-checkbox";
        checkbox.dataset.word = item.word;
        checkbox.id = `suggest-${idx}`;

        const wordSpan = document.createElement("span");
        wordSpan.className = "suggestion-word";
        wordSpan.textContent = item.word;

        const pinyinSpan = document.createElement("span");
        pinyinSpan.className = "suggestion-pinyin";
        pinyinSpan.textContent = item.pinyin;

        const meaningSpan = document.createElement("span");
        meaningSpan.className = "suggestion-meaning";
        meaningSpan.textContent = item.meaning;

        row.append(checkbox, wordSpan, pinyinSpan, meaningSpan);
        elements.suggestionsList.appendChild(row);
    });
}

/**
 * Adds the checked suggested words to brain.json (unlocking any of their
 * characters that aren't already unlocked) via POST to the local server.
 */
async function handleAddSuggestedWords() {
    const checked = elements.suggestionsList
        ? Array.from(elements.suggestionsList.querySelectorAll(".suggestion-checkbox:checked"))
        : [];

    if (checked.length === 0) {
        alert("Please select at least one word to add.");
        return;
    }

    const words = checked.map(box => box.dataset.word);

    elements.modalBtnSubmit.textContent = "Adding...";
    elements.modalBtnSubmit.disabled = true;

    try {
        const response = await fetch('/api/suggestions/add', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ words })
        });

        if (!response.ok) throw new Error("Failed to add selected words.");

        const result = await response.json();

        if (result.total_unlocked_count !== undefined) {
            state.totalUnlockedCount = result.total_unlocked_count;
            updateCharacterCounter();
        }

        alert(`Added ${result.added_words.length} word(s), unlocking ${result.added_chars_count} new character(s).`);

        elements.importModal.style.display = "none";
        fetchNewSession();

    } catch (err) {
        console.error(err);
        alert("Error connecting to Python server while adding words.");
    } finally {
        elements.modalBtnSubmit.textContent = SUBMIT_LABELS[state.importMode] || "Submit";
        elements.modalBtnSubmit.disabled = false;
    }
}

/**
 * Handles processing and unlocking words locally via POST request to python backend.
 */
async function handleTextImport() {
    const text = elements.importTextarea.value.trim();
    if (!text) {
        alert("Please paste some Chinese text first.");
        return;
    }

    elements.modalBtnSubmit.textContent = "Processing...";
    elements.modalBtnSubmit.disabled = true;

    try {
        const response = await fetch('/api/import', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ text })
        });

        if (!response.ok) throw new Error("Import failed on backend server.");

        const result = await response.json();

        if (result.total_unlocked_count !== undefined) {
            state.totalUnlockedCount = result.total_unlocked_count;
            updateCharacterCounter();
        }

        alert(result.message || "Import completed successfully!");

        elements.importModal.style.display = "none";
        fetchNewSession();

    } catch (err) {
        console.error(err);
        alert("Error connecting to Python server during text import.");
    } finally {
        elements.modalBtnSubmit.textContent = SUBMIT_LABELS[state.importMode] || "Submit";
        elements.modalBtnSubmit.disabled = false;
    }
}

/**
 * Handles both the HSK (no key) and AI (provider + key) sentence-generation
 * modes, which share the same backend endpoint and response shape.
 */
async function handleGenerateSession(source) {
    const payload = { source };

    if (source === "ai") {
        const providerId = elements.aiProviderSelect.value;
        const apiKey = elements.aiApiKeyInput.value.trim();
        payload.provider = providerId;
        if (apiKey) {
            payload.api_key = apiKey;
            localStorage.setItem(`juzi_api_key_${providerId}`, apiKey);
        }
    }

    elements.modalBtnSubmit.textContent = "Working...";
    elements.modalBtnSubmit.disabled = true;

    try {
        const response = await fetch('/api/session/generate', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload)
        });

        const result = await response.json();
        if (!response.ok) throw new Error(result.error || "Sentence generation failed.");

        if (Array.isArray(result.sentences) && result.sentences.length > 0) {
            state.sentences = result.sentences;
            state.currentIndex = 0;
            state.totalUnlockedCount = result.total_unlocked_count ?? state.totalUnlockedCount;
            updateCharacterCounter();
            elements.importModal.style.display = "none";
            loadSession();
        } else {
            alert("No matching sentences found. Try unlocking more characters first.");
        }
    } catch (err) {
        console.error(err);
        alert(err.message || "Error generating sentences.");
    } finally {
        elements.modalBtnSubmit.textContent = SUBMIT_LABELS[state.importMode] || "Submit";
        elements.modalBtnSubmit.disabled = false;
    }
}

/**
 * Neural Text-to-Speech playback at native speed (Rate 1.0), using whichever
 * Mandarin voice is currently selected (see getPreferredChineseVoice).
 */
function playNativeTTS(text) {
    if (!('speechSynthesis' in window)) return;
    window.speechSynthesis.cancel();

    currentUtterance = new SpeechSynthesisUtterance(text);
    currentUtterance.lang = 'zh-CN';
    currentUtterance.rate = 1.0;

    const voice = getPreferredChineseVoice();
    if (voice) {
        currentUtterance.voice = voice;
    }

    window.speechSynthesis.speak(currentUtterance);
}

// Name substrings from known TTS voice packs (Microsoft/Apple/Amazon Mandarin
// voices) used to guess a voice's gender, since the Web Speech API exposes no
// real gender field. Voices that don't match either list are "unknown" and
// still selectable -- they just can't be labeled Male/Female in the UI.
const FEMALE_VOICE_NAME_HINTS = [
    "female", "ting-ting", "tingting", "mei-jia", "meijia", "sin-ji", "sinji",
    "yaoyao", "huihui", "xiaoxiao", "xiaoyi", "xiaomo", "xiaoxuan", "xiaohan", "xiaorui"
];
const MALE_VOICE_NAME_HINTS = [
    "male", "kangkang", "zhiwei", "yunyang", "yunjian", "yunxi", "yunfeng", "li-mu", "limu"
];

function classifyVoiceGender(voice) {
    const name = voice.name.toLowerCase();
    if (FEMALE_VOICE_NAME_HINTS.some(hint => name.includes(hint))) return "female";
    if (MALE_VOICE_NAME_HINTS.some(hint => name.includes(hint))) return "male";
    return "unknown";
}

/**
 * Mandarin-language codes only. "zh" alone is ambiguous across browsers/OSes
 * (some report bare "zh" for Mandarin), but zh-HK and yue* are Cantonese --
 * a different spoken language, not another voice option for Mandarin -- so
 * they're deliberately excluded here rather than lumped in as "just another
 * Chinese voice."
 */
function isMandarinVoice(voice) {
    const lang = (voice.lang || "").toLowerCase();
    return lang === "zh" || lang.startsWith("zh-cn") || lang.startsWith("zh-sg") ||
        lang.startsWith("zh-tw") || lang.startsWith("cmn");
}

/**
 * All installed voices usable for Mandarin playback. Grouped with any
 * detected female voices first, then male, then ungendered/unknown ones, so
 * switchToNextVoice() and the initial pick both favor a clean female/male
 * split when the device actually has both.
 */
function getOrderedChineseVoices() {
    if (!('speechSynthesis' in window)) return [];
    const voices = window.speechSynthesis.getVoices().filter(isMandarinVoice);
    const female = voices.filter(v => classifyVoiceGender(v) === "female");
    const male = voices.filter(v => classifyVoiceGender(v) === "male");
    const unknown = voices.filter(v => classifyVoiceGender(v) === "unknown");
    return [...female, ...male, ...unknown];
}

/**
 * Returns the voice to speak with: the user's saved choice (state.ttsVoiceURI)
 * if it's still installed, otherwise the first available Mandarin voice.
 */
function getPreferredChineseVoice() {
    const ordered = getOrderedChineseVoices();
    if (ordered.length === 0) return null;
    if (state.ttsVoiceURI) {
        const saved = ordered.find(v => v.voiceURI === state.ttsVoiceURI);
        if (saved) return saved;
    }
    return ordered.find(v => v.lang === 'zh-CN') || ordered[0];
}

/**
 * Advances to the next voice in the ordered list (wrapping around) and
 * persists the choice in localStorage so it survives a reload. With two
 * detected genders this reads as "switch to the other gender"; with only
 * ungendered voices available it still cycles through whatever exists.
 */
function switchToNextVoice() {
    const ordered = getOrderedChineseVoices();
    if (ordered.length < 2) return;
    const current = getPreferredChineseVoice();
    const currentIdx = ordered.findIndex(v => v.voiceURI === current.voiceURI);
    const next = ordered[(currentIdx + 1) % ordered.length];
    state.ttsVoiceURI = next.voiceURI;
    localStorage.setItem("juzi_tts_voice_uri", next.voiceURI);
}

/**
 * Labels the Switch Voice button with the gender it would switch TO (so the
 * button reads as an action), or disables it when there's nothing to switch
 * to -- either only one Mandarin voice is installed, or the device exposes
 * several but none can be told apart.
 */
function updateSwitchVoiceButton(btn) {
    const ordered = getOrderedChineseVoices();
    if (ordered.length < 2) {
        btn.disabled = true;
        btn.textContent = "🔊 Only Voice Available";
        btn.title = "Only one Mandarin voice is installed on this device.";
        return;
    }

    const current = getPreferredChineseVoice();
    const currentIdx = ordered.findIndex(v => v.voiceURI === current.voiceURI);
    const next = ordered[(currentIdx + 1) % ordered.length];
    const nextGender = classifyVoiceGender(next);

    btn.disabled = false;
    if (nextGender === "female") {
        btn.textContent = "🔄 Switch to Female Voice";
    } else if (nextGender === "male") {
        btn.textContent = "🔄 Switch to Male Voice";
    } else {
        btn.textContent = "🔄 Switch Voice";
    }
    btn.title = `Current voice: ${current.name}`;
}

function isPunctuation(char) {
    const allowedPunct = "，。！？、 ；：“”‘’—…\t\r\n";
    return allowedPunct.includes(char);
}
