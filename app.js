/**
 * JuziGenius (句子Genius) - Main Application Controller
 * Handles Hanzi Writer strict character validation, hint escalation, 
 * punctuation insertion, and dynamic backend session fetching.
 */

/**
 * JuziGenius (句子Genius) - Main Application Controller
 * Using a hardcoded test session for rapid local development.
 */

// Global App State with Hardcoded Test Data
const state = {
    sentences: [
        {
            english: "I love China.",
            chinese: "我爱中国。",
            char_metadata: {
                "我": { "pinyin": "wǒ", "meaning": "I/me" },
                "爱": { "pinyin": "ài", "meaning": "to love" },
                "中": { "pinyin": "zhōng", "meaning": "middle/China" },
                "国": { "pinyin": "guó", "meaning": "country" }
            }
        }
    ],
    currentIndex: 0,
    charIndex: 0,
    hintTier: 0,
    writer: null,
    currentScorePenalty: 0
};

// DOM Elements Cache
const elements = {};

document.addEventListener("DOMContentLoaded", () => {
    cacheDomElements();
    initEventListeners();

    // BYPASS BACKEND FETCH FOR FAST LOADING:
    loadSession();
});

// Global App State
/**const state = {
    sentences: [],          // Dynamically loaded from Python backend
    currentIndex: 0,       // Current sentence index
    charIndex: 0,          // Current character index within the active sentence
    hintTier: 0,           // 0: None, 1: Pinyin, 2: Ghost Outline, 3: Animated Stroke
    writer: null,          // Active HanziWriter instance
    currentScorePenalty: 0 // Tracks SRS penalty based on highest hint tier reached
};

// DOM Elements Cache
const elements = {};

document.addEventListener("DOMContentLoaded", () => {
    cacheDomElements();
    initEventListeners();
    fetchNewSession(); // Fetch real-time AI sentences from backend on load
});
*/

function cacheDomElements() {
    elements.englishPrompt = document.getElementById("english-prompt");
    elements.assemblyLine = document.getElementById("assembly-line");
    elements.canvasContainer = document.getElementById("tian-zi-ge");
    elements.btnUndo = document.getElementById("btn-undo");
    elements.btnHint = document.getElementById("btn-hint");
    elements.btnSubmit = document.getElementById("btn-submit");
    elements.btnDiscuss = document.getElementById("btn-discuss");
    elements.puncButtons = document.querySelectorAll(".punc-btn");
}

function initEventListeners() {
    elements.btnUndo.addEventListener("click", handleUndo);
    elements.btnHint.addEventListener("click", handleHintEscalation);
    elements.btnSubmit.addEventListener("click", handleSentenceSubmit);
    
    // Punctuation Toolbelt Listeners
    elements.puncButtons.forEach(btn => {
        btn.addEventListener("click", (e) => {
            const punc = e.target.getAttribute("data-punc");
            insertPunctuation(punc);
        });
    });
}

/**
 * Fetches AI-generated sentences and brain.json metadata from the Python local server.
 */
async function fetchNewSession() {
    elements.englishPrompt.textContent = "Generating dynamic sentences via Gemini AI...";
    try {
        const response = await fetch('/api/session');
        if (!response.ok) throw new Error("Failed to fetch session from backend engine.");
        
        const data = await response.json();
        if (data && data.length > 0) {
            state.sentences = data;
            state.currentIndex = 0;
            loadSession();
        } else {
            elements.englishPrompt.textContent = "Error: Unlocked character pool empty or generation failed.";
        }
    } catch (err) {
        console.error(err);
        elements.englishPrompt.textContent = "Connection error with Python backend engine.";
    }
}

/**
 * Loads the active session sentence and prepares the first character canvas.
 */
function loadSession() {
    const currentSentence = state.sentences[state.currentIndex];
    if (!currentSentence) return;

    elements.englishPrompt.textContent = currentSentence.english;
    state.charIndex = 0;
    state.hintTier = 0;
    state.currentScorePenalty = 0;

    renderAssemblyLine();
    setupCurrentCharacterWriter();
}

/**
 * Renders the slot blocks for the current sentence.
 */
function renderAssemblyLine() {
    elements.assemblyLine.innerHTML = "";
    const currentSentence = state.sentences[state.currentIndex];
    const chineseChars = Array.from(currentSentence.chinese);

    chineseChars.forEach((char, idx) => {
        const slot = document.createElement("div");
        slot.className = "character-slot";
        slot.id = `slot-${idx}`;

        // Check if it's punctuation or standard character
        if (isPunctuation(char)) {
            slot.classList.add("punctuation-slot");
            slot.textContent = char;
        } else {
            slot.textContent = idx < state.charIndex ? char : "_";
            if (idx === state.charIndex) {
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
    const chineseChars = Array.from(currentSentence.chinese);
    
    // Skip punctuation automatically if encountered at the pointer
    while (state.charIndex < chineseChars.length && isPunctuation(chineseChars[state.charIndex])) {
        state.charIndex++;
    }

    if (state.charIndex >= chineseChars.length) {
        // Sentence fully written! Trigger validation readiness.
        elements.canvasContainer.innerHTML = `<div class="completion-notice">Sentence Complete! Press Submit.</div>`;
        return;
    }

    const targetChar = chineseChars[state.charIndex];
    elements.canvasContainer.innerHTML = ""; // Clear canvas container

    // Reset Hint display area if present
    const existingPinyin = document.getElementById("pinyin-push-display");
    if (existingPinyin) existingPinyin.remove();

    // Initialize HanziWriter (Hardcore Configuration: showCharacter: false)
    state.writer = HanziWriter.create('tian-zi-ge', targetChar, {
        width: 260,
        height: 260,
        padding: 10,
        showCharacter: false, // ZERO PREDICTIVE ASSISTANCE BY DEFAULT
        showOutline: false,
        strokeAnimationSpeed: 1,
        leniency: 1.0,        // Strict stroke verification
        highlightColor: '#e74c3c',
        drawingColor: '#2c3e50',
        strokeColor: '#bdc3c7'
    });

    state.writer.quiz({
        onCorrectStroke: (strokeData) => {
            // Optional micro-feedback on correct stroke
        },
        onMistake: (strokeData) => {
            // Hardcore mode tracking
        },
        onComplete: (summaryData) => {
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

    // Advance to next character
    state.charIndex++;
    state.hintTier = 0; // Reset hint tier for the next character

    // Load next character in sequence
    setTimeout(setupCurrentCharacterWriter, 300);
}

/**
 * Handles free soft-input punctuation insertion.
 */
function insertPunctuation(punc) {
    const currentSentence = state.sentences[state.currentIndex];
    const chineseChars = Array.from(currentSentence.chinese);

    if (state.charIndex < chineseChars.length && chineseChars[state.charIndex] === punc) {
        const slot = document.getElementById(`slot-${state.charIndex}`);
        if (slot) {
            slot.textContent = punc;
            slot.classList.remove("active");
        }
        state.charIndex++;
        renderAssemblyLine();
        setupCurrentCharacterWriter();
    }
}

/**
 * The Hint Staircase Engine: Progressive tiered assistance.
 */
function handleHintEscalation() {
    state.hintTier++;
    const currentSentence = state.sentences[state.currentIndex];
    const targetChar = Array.from(currentSentence.chinese)[state.charIndex];

    if (state.hintTier === 1) {
        // Tier 1: Phonetic Push (Pinyin pulled dynamically from database metadata)
        state.currentScorePenalty = Math.max(state.currentScorePenalty, 1);
        showPinyinPush(targetChar, currentSentence);
    } else if (state.hintTier === 2) {
        // Tier 2: Structural Skeleton (Ghost Outline enabled temporarily)
        state.currentScorePenalty = Math.max(state.currentScorePenalty, 2);
        if (state.writer) {
            state.writer.showOutline();
        }
    } else if (state.hintTier >= 3) {
        // Tier 3: The Master Class (Animated Stroke Walkthrough)
        state.currentScorePenalty = 3; // Max penalty / Fail state for SRS
        if (state.writer) {
            state.writer.animateCharacter();
        }
    }
}

function showPinyinPush(char, sentenceObj) {
    let pinyinDisplay = document.getElementById("pinyin-push-display");
    if (!pinyinDisplay) {
        pinyinDisplay = document.createElement("div");
        pinyinDisplay.id = "pinyin-push-display";
        pinyinDisplay.style.cssText = "font-size: 1.4rem; font-weight: bold; color: var(--accent-gold); margin-bottom: 8px; text-align: center;";
        elements.canvasContainer.parentNode.insertBefore(pinyinDisplay, elements.canvasContainer);
    }

    // Pull real database metadata matching this character from the active sentence session
    const charMeta = sentenceObj.char_metadata && sentenceObj.char_metadata[char];
    const pinyinResult = charMeta ? charMeta.pinyin : "pīn yīn";
    
    pinyinDisplay.textContent = `Hint (Tier 1 Pinyin): ${pinyinResult}`;
}

/**
 * Handles undo action for the current active stroke canvas.
 */
function handleUndo() {
    if (state.writer) {
        state.writer.undoLastStroke();
    }
}

/**
 * Final sentence submission and trigger for post-success TTS audio.
 */
function handleSentenceSubmit() {
    const currentSentence = state.sentences[state.currentIndex];
    
    // Check if user completed all characters
    if (state.charIndex < Array.from(currentSentence.chinese).length) {
        alert("Please finish writing all characters before submitting.");
        return;
    }

    // Trigger Audible Reinforcement (Native Web Speech TTS)
    playNativeTTS(currentSentence.chinese);

    alert("Sentence Verified Successfully!");
    
    // Move to next sentence or fetch new batch if pool is finished
    state.currentIndex++;
    if (state.currentIndex >= state.sentences.length) {
        fetchNewSession();
    } else {
        loadSession();
    }
}

/**
 * Neural Text-to-Speech playback at native speed (Rate 1.0).
 */
function playNativeTTS(text) {
    if ('speechSynthesis' in window) {
        const utterance = new SpeechSynthesisUtterance(text);
        utterance.lang = 'zh-CN';
        utterance.rate = 1.0; // Full speed native Mandarin
        window.speechSynthesis.speak(utterance);
    }
}

function isPunctuation(char) {
    const allowedPunct = "，。！？、 ；：“”‘’—…";
    return allowedPunct.includes(char);
}
