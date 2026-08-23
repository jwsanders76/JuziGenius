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

// Global audio reference to prevent browser garbage collection
let currentUtterance = null;

// DOM Elements Cache
const elements = {};

document.addEventListener("DOMContentLoaded", () => {
    cacheDomElements();
    initEventListeners();
    loadSession();
});

function cacheDomElements() {
    elements.englishPrompt = document.getElementById("english-prompt");
    elements.assemblyLine = document.getElementById("assembly-line");
    elements.canvasContainer = document.getElementById("tian-zi-ge");
    elements.btnClear = document.getElementById("btn-clear");
    elements.btnHint = document.getElementById("btn-hint");
    elements.btnSubmit = document.getElementById("btn-submit");
    elements.btnDiscuss = document.getElementById("btn-discuss");
    elements.puncButtons = document.querySelectorAll(".punc-btn");
}

function initEventListeners() {
    elements.btnClear.addEventListener("click", handleClearCanvas);
    elements.btnHint.addEventListener("click", handleHintEscalation);
    elements.btnSubmit.addEventListener("click", handleSentenceSubmit);
    
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

    while (state.charIndex < chineseChars.length && isPunctuation(chineseChars[state.charIndex])) {
        state.charIndex++;
    }

    const container = document.getElementById('tian-zi-ge');
    if (!container) {
        console.error("Fatal: #tian-zi-ge container element missing from DOM.");
        return;
    }

    if (state.charIndex >= chineseChars.length) {
        container.innerHTML = `<div class="completion-notice">Sentence Complete! Press Submit.</div>`;
        return;
    }


    const targetChar = chineseChars[state.charIndex];

    container.innerHTML = "";

    // Clear the hint box text when switching characters (without removing the box itself)
    const hintContainer = document.getElementById("hint-display-container");
    if (hintContainer) hintContainer.textContent = "";
    const existingPinyin = document.getElementById("pinyin-push-display");
    if (existingPinyin) existingPinyin.remove();

    state.writer = HanziWriter.create('tian-zi-ge', targetChar, {
        width: 240,
        height: 240,
        padding: 10,
        showCharacter: false,
        showOutline: false,   // Blank grid by default (Hardcore mode)
        strokeAnimationSpeed: 2,
	delayBetweenStrokes: 150, // Shortened pause between individual strokes in milliseconds
        leniency: 1.0,
        highlightColor: '#e74c3c',
        drawingColor: '#000000',
        strokeColor: '#333333',
	outlineColor: '#b0b0b0'
    });

    state.writer.quiz({
        onCorrectStroke: (strokeData) => {},
        onMistake: (strokeData) => {},
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

    state.charIndex++;
    state.hintTier = 0; 
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
            state.writer.animateCharacter();
        }
    }
}

function showPinyinPush(char, sentenceObj) {
    const hintContainer = document.getElementById("hint-display-container");
    if (!hintContainer) return;

    const charMeta = sentenceObj.char_metadata && sentenceObj.char_metadata[char];
    const pinyinResult = charMeta ? charMeta.pinyin : "pīn yīn";
    
    hintContainer.textContent = `Hint (Tier 1 Pinyin): ${pinyinResult}`;
}
/**
 * Clears the active character canvas so the user can retry writing from scratch.
 */
function handleClearCanvas() {
    if (state.writer) {
        // Cancel current HanziWriter quiz session and reload clean slate
        state.writer.cancelQuiz();
        setupCurrentCharacterWriter();
    }
}

/**
 * Final sentence submission and trigger for post-success TTS audio.
 */
function handleSentenceSubmit() {
    const currentSentence = state.sentences[state.currentIndex];
    
    if (state.charIndex < Array.from(currentSentence.chinese).length) {
        alert("Please finish writing all characters before submitting.");
        return;
    }

    playNativeTTS(currentSentence.chinese);
    alert("Sentence Verified Successfully!");
    
/** Uncomment this when dev is done. 
    state.currentIndex++;
    if (state.currentIndex >= state.sentences.length) {
        fetchNewSession();
    } else {
        loadSession();
    }
*/
    // Loop back to the start of the mock array instead of fetching from Gemini backend
    state.currentIndex = (state.currentIndex + 1) % state.sentences.length;
    loadSession();
}

/**
 * Neural Text-to-Speech playback at native speed (Rate 1.0).
 */
function playNativeTTS(text) {
    if (!('speechSynthesis' in window)) return;
    window.speechSynthesis.cancel();

    currentUtterance = new SpeechSynthesisUtterance(text);
    currentUtterance.lang = 'zh-CN';
    currentUtterance.rate = 1.0;

    const voices = window.speechSynthesis.getVoices();
    const chineseVoice = voices.find(v => v.lang === 'zh-CN' || v.lang === 'zh');
    if (chineseVoice) {
        currentUtterance.voice = chineseVoice;
    }

    window.speechSynthesis.speak(currentUtterance);
}

function isPunctuation(char) {
    const allowedPunct = "，。！？、 ；：“”‘’—…";
    return allowedPunct.includes(char);
}
