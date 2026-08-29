/**
 * JuziGenius (句子Genius) - Main Application Controller
 */

// Global App State
const state = {
    sentences: [],
    currentIndex: 0,
    charIndex: 0,
    hintTier: 0,
    charMistakes: 0,
    writer: null,
    writerToken: 0,
    skippedIndices: new Set(),
    totalUnlockedCount: 0,
    totalDueCount: 0,
    newBacklog: 0,
    ttsVoiceURI: localStorage.getItem("juzi_tts_voice_uri") || null,
    isCompleted: false,
    importMode: "paste"
};

const SUBMIT_LABELS = { paste: "Process & Unlock", hsk: "Get Sentences", suggest: "Add Selected", chars: "Unlock Selected" };

// When the page is served at /u/<slug>/, every API call must carry that
// same prefix so the server routes it to that friend's own brain.json
// instead of the shared default one. Empty string (no prefix) for a plain
// single-user install at the site root, so this stays a no-op there.
const API_BASE = (() => {
    const match = window.location.pathname.match(/^\/u\/([A-Za-z0-9_-]{16,})(?:\/|$)/);
    return match ? `/u/${match[1]}` : "";
})();

// Speed of the tier-3 stroke walkthrough. Set on the writer at creation time
// rather than passed to animateCharacter(), which ignores per-call speed.
const HINT_WALKTHROUGH_SPEED = 4.5;

// Upstream stroke data, used only for characters this install didn't vendor
// (see loadCharacterStrokes). Pinned to the version fetch_stroke_data.py
// vendored from, so an online fallback can't quietly serve different data.
const STROKE_DATA_CDN = "https://cdn.jsdelivr.net/npm/hanzi-writer-data@2.0.1";

/**
 * Supplies Hanzi Writer with a character's stroke-order data.
 *
 * Replaces the library's default loader, which fetches every character from
 * cdn.jsdelivr.net at the moment you're asked to write it -- that quietly made
 * handwriting, the core of the app, require an internet connection. Stroke
 * data now comes from the local server (stroke_data.json, built by
 * fetch_stroke_data.py), so normal play is genuinely offline.
 *
 * The vendored set covers the vocabulary corpus, the HSK sentences, and your
 * unlocked pool, but text import can unlock rarer characters than that. Those
 * fall back to the CDN when online, and raise onLoadCharDataError when not --
 * which is the difference between a readable message and a blank canvas.
 */
function loadCharacterStrokes(char, onComplete, onError) {
    fetch(`${API_BASE}/api/strokes?char=${encodeURIComponent(char)}`)
        .then(response => {
            if (response.ok) return response.json();
            if (response.status === 404) return null;   // not vendored
            throw new Error(`Local stroke data request failed (${response.status}).`);
        })
        .then(data => {
            if (data) return onComplete(data);
            return fetch(`${STROKE_DATA_CDN}/${encodeURIComponent(char)}.json`)
                .then(response => {
                    if (!response.ok) throw new Error(`CDN returned ${response.status}.`);
                    return response.json();
                })
                .then(onComplete);
        })
        .catch(err => onError(err));
}

/**
 * Shown when a character's stroke data can't be loaded at all -- it isn't
 * vendored and the CDN is unreachable or doesn't have it. Previously this
 * left an empty canvas with no explanation and no way forward: the quiz could
 * never complete, and Clear just rebuilt the same broken writer. Offer the
 * only useful action instead.
 */
function showStrokeDataError(char, token) {
    if (token !== state.writerToken) return;

    const container = document.getElementById('tian-zi-ge');
    if (!container) return;

    container.innerHTML = `
        <div class="stroke-error-card">
            <div class="stroke-error-char">${char}</div>
            <div class="stroke-error-title">No stroke data for this character</div>
            <div class="stroke-error-note">It isn't in the offline set and the character database couldn't be reached. Run <code>python3 fetch_stroke_data.py</code> to widen the offline set.</div>
            <button id="btn-skip-char" class="btn-next">Skip This Character →</button>
        </div>
    `;

    const btnSkip = document.getElementById("btn-skip-char");
    if (btnSkip) btnSkip.addEventListener("click", () => skipCurrentCharacter());
}

/**
 * Steps past a character that can't be practiced. Deliberately records no
 * SM-2 review: the user never actually wrote it, and grading it either way
 * would be a lie to the scheduler.
 */
function skipCurrentCharacter() {
    // Recorded in state, not just on the slot element: setupCurrentCharacterWriter
    // re-renders the whole assembly line, which would otherwise wipe the marker
    // and leave a skipped character looking exactly like a written one.
    state.skippedIndices.add(state.charIndex);
    state.charIndex++;
    state.hintTier = 0;
    state.charMistakes = 0;
    setupCurrentCharacterWriter();
}

// Global audio reference to prevent garbage collection
let currentUtterance = null;

// DOM Elements Cache
const elements = {};

document.addEventListener("DOMContentLoaded", () => {
    cacheDomElements();
    initEventListeners();
    registerServiceWorker();
    fetchNewSession();
    // Chrome loads its voice list asynchronously; kick it off early so it's
    // ready by the time a sentence completes and the victory card needs it.
    if ("speechSynthesis" in window) window.speechSynthesis.getVoices();
});

/**
 * Registers the service worker that makes JuziGenius installable and lets it
 * open with no server reachable at all.
 *
 * Always registered at the site root, never at /u/<slug>/: one worker with
 * root scope serves every account, and a per-account worker would fight the
 * root one over the same shell assets. The account a page belongs to is
 * decided by API_BASE, not by the worker.
 *
 * The worker is network-only and caches nothing: JuziGenius is a hosted
 * service, so an app that loaded without reaching the server could only show
 * an empty shell anyway. Its sole job is to satisfy the browser's install
 * criteria. See sw.js.
 *
 * Requires a secure context -- HTTPS or localhost. Registration is skipped
 * over plain http:// on a bare IP, which is why the failure is logged rather
 * than shown: the app is fully functional without it.
 */
function registerServiceWorker() {
    if (!("serviceWorker" in navigator)) return;
    window.addEventListener("load", () => {
        navigator.serviceWorker.register("/sw.js", { scope: "/" })
            .catch(err => console.info(
                "Service worker not registered — the app still works, it just " +
                "won't be installable from here. This is expected over plain " +
                "http:// on a LAN address; a secure context is required.", err));
    });
}

function cacheDomElements() {
    elements.englishPrompt = document.getElementById("english-prompt");
    elements.assemblyLine = document.getElementById("assembly-line");
    elements.canvasContainer = document.getElementById("tian-zi-ge");
    elements.btnClear = document.getElementById("btn-clear");
    elements.btnHint = document.getElementById("btn-hint");
    elements.btnImport = document.getElementById("btn-import");
    elements.counterValue = document.getElementById("counter-value");
    elements.dueCounter = document.getElementById("due-counter");
    elements.dueCounterValue = document.getElementById("due-counter-value");

    // Sentence pronunciation controls (beside the assembly line)
    elements.sentenceAudioControls = document.getElementById("sentence-audio-controls");
    elements.btnReplayAudio = document.getElementById("btn-replay-audio");
    elements.btnSwitchVoice = document.getElementById("btn-switch-voice");
    
    // Import Modal Elements
    elements.importModal = document.getElementById("import-modal");
    elements.modalBtnCancel = document.getElementById("modal-btn-cancel");
    elements.modalBtnSubmit = document.getElementById("modal-btn-submit");
    elements.importTextarea = document.getElementById("import-textarea");
    elements.importTranslationTextarea = document.getElementById("import-translation-textarea");
    elements.modeTabs = elements.importModal.querySelectorAll(".mode-tab");
    elements.modePanels = elements.importModal.querySelectorAll(".mode-panel");
    elements.suggestionsList = document.getElementById("suggestions-list");
    elements.charSuggestionsList = document.getElementById("char-suggestions-list");
    elements.backlogNote = document.getElementById("backlog-note");
    elements.btnProgress = document.getElementById("btn-progress");
    elements.progressModal = document.getElementById("progress-modal");
    elements.progressBody = document.getElementById("progress-body");
    elements.progressCharacters = document.getElementById("progress-characters");
    elements.progressTabs = elements.progressModal.querySelectorAll(".mode-tab");
    elements.progressPanels = elements.progressModal.querySelectorAll(".mode-panel");
    elements.progressBtnClose = document.getElementById("progress-btn-close");

    elements.onboardingModal = document.getElementById("onboarding-modal");
    elements.onboardingTiersList = document.getElementById("onboarding-tiers");
    elements.onboardingStatus = document.getElementById("onboarding-status");
}

function initEventListeners() {
    if (elements.btnClear) elements.btnClear.addEventListener("click", handleClearCanvas);
    if (elements.btnHint) elements.btnHint.addEventListener("click", handleHintEscalation);
    
    if (elements.btnReplayAudio) {
        elements.btnReplayAudio.addEventListener("click", () => playNativeTTS(currentSentenceText()));
    }

    if (elements.btnSwitchVoice) {
        elements.btnSwitchVoice.addEventListener("click", () => {
            switchToNextVoice();
            updateSwitchVoiceButton(elements.btnSwitchVoice);
            playNativeTTS(currentSentenceText());
        });
    }

    if (elements.btnProgress) {
        elements.btnProgress.addEventListener("click", openProgressView);
    }
    if (elements.progressBtnClose) {
        elements.progressBtnClose.addEventListener("click", () => {
            if (elements.progressModal) elements.progressModal.style.display = "none";
        });
    }
    elements.progressTabs.forEach(tab => {
        tab.addEventListener("click", () => switchProgressTab(tab.dataset.progressTab));
    });

    // Import Modal Event Listeners
    if (elements.btnImport) {
        elements.btnImport.addEventListener("click", () => {
            if (elements.importModal) {
                elements.importModal.style.display = "flex";
                elements.importTextarea.value = "";
                if (elements.importTranslationTextarea) elements.importTranslationTextarea.value = "";
                switchImportMode("paste");
                elements.importTextarea.focus();
            }
        });
    }

    elements.modeTabs.forEach(tab => {
        tab.addEventListener("click", () => switchImportMode(tab.dataset.mode));
    });

    if (elements.modalBtnCancel) {
        elements.modalBtnCancel.addEventListener("click", () => {
            if (elements.importModal) elements.importModal.style.display = "none";
        });
    }

    if (elements.modalBtnSubmit) {
        elements.modalBtnSubmit.addEventListener("click", handleModalSubmit);
    }

    // Keyboard navigation: Press Space or Enter to load Next sentence when completed.
    //
    // Scoped deliberately. This listener is on window and calls
    // preventDefault(), so without the guards below it swallowed the first
    // Space or Enter typed into the import textarea -- and, because the
    // shortcut also fires while the modal covers the screen, silently
    // advanced the sentence hidden behind it. Typing into a field is never a
    // request to advance the sentence, and neither is anything typed while a
    // modal is open.
    window.addEventListener("keydown", (e) => {
        if (!state.isCompleted) return;
        if (e.code !== "Space" && e.code !== "Enter") return;
        if (isTypingTarget(e.target)) return;
        if (isModalOpen()) return;

        e.preventDefault();
        nextSentence();
    });
}

/**
 * Fetches session sentences and metadata from the Python local server.
 */
async function fetchNewSession() {
    elements.englishPrompt.textContent = "Loading session from offline bank...";
    try {
        const response = await fetch(`${API_BASE}/api/session`);
        if (!response.ok) throw new Error("Failed to fetch session from backend engine.");
        
        const data = await response.json();
        
        // Handle server envelope { sentences, total_unlocked_count } or legacy array format
        if (data && typeof data === 'object' && !Array.isArray(data)) {
            state.sentences = data.sentences || [];
            state.totalUnlockedCount = data.total_unlocked_count || 0;
            state.totalDueCount = data.total_due_count || 0;
            state.newBacklog = data.new_backlog || 0;

            // A brand new create_user.py account that hasn't picked a
            // starting tier yet -- show the picker instead of the normal
            // "pool is empty" messaging, which would send them to Import for
            // a problem they don't actually have.
            if (data.onboarded === false) {
                showOnboarding();
                return;
            }
        } else if (Array.isArray(data)) {
            state.sentences = data;
        }

        if (elements.onboardingModal) elements.onboardingModal.style.display = "none";
        updateCharacterCounter();
        updateDueCounter();

        if (state.sentences.length > 0) {
            state.currentIndex = 0;
            loadSession();
        } else {
            // An empty sentence bank has two very different causes, and
            // reporting the wrong one sends the user off to fix a problem
            // they don't have: either nothing is unlocked yet, or plenty is
            // unlocked but no HSK sentence is built entirely from it.
            elements.englishPrompt.textContent = state.totalUnlockedCount > 0
                ? `No practice sentences yet. You have ${state.totalUnlockedCount} characters unlocked, but no HSK sentence uses only those characters. Click 'Import' to unlock more characters.`
                : "Your unlocked character pool is empty. Click 'Import' to paste Chinese text and begin!";
            if (elements.canvasContainer) elements.canvasContainer.innerHTML = "";
            if (elements.assemblyLine) elements.assemblyLine.innerHTML = "";
            toggleSentenceAudioControls(false);
        }
    } catch (err) {
        console.error(err);
        // Now that the app is installable, it will be opened offline -- from a
        // home screen, on a tablet whose host machine is asleep. The shell
        // comes out of the service worker cache and renders fine, so the only
        // thing missing is the session, and "Connection error with Python
        // backend engine" is the wrong thing to tell someone in that moment:
        // it reads as a fault when the app is working exactly as designed.
        elements.englishPrompt.textContent = navigator.onLine
            ? "Can't reach JuziGenius right now. Please try again in a moment."
            : "You appear to be offline. JuziGenius needs a connection — your practice data lives on the server.";
    }
}

/**
 * Shows the first-run tier picker for a brand new account (create_user.py)
 * that hasn't chosen a starting pool yet, and loads the tier catalog to
 * populate it. Not dismissible -- there is no session to fall back to until
 * a tier is picked, so the modal has no Cancel button.
 */
async function showOnboarding() {
    if (elements.canvasContainer) elements.canvasContainer.innerHTML = "";
    if (elements.assemblyLine) elements.assemblyLine.innerHTML = "";
    elements.englishPrompt.textContent = "";
    toggleSentenceAudioControls(false);
    toggleSidebarButtons(false);

    if (!elements.onboardingModal) return;
    elements.onboardingModal.style.display = "flex";
    if (elements.onboardingStatus) elements.onboardingStatus.hidden = true;
    if (elements.onboardingTiersList) {
        elements.onboardingTiersList.innerHTML = '<p class="suggestions-empty">Loading tiers…</p>';
    }

    try {
        const response = await fetch(`${API_BASE}/api/onboarding/tiers`);
        if (!response.ok) throw new Error(`Failed to load tiers (${response.status}).`);
        const data = await response.json();
        renderOnboardingTiers(data.tiers || []);
    } catch (err) {
        console.error(err);
        if (elements.onboardingTiersList) {
            elements.onboardingTiersList.innerHTML = '<p class="suggestions-empty">Couldn\'t load starting tiers. Please try again in a moment.</p>';
        }
    }
}

function renderOnboardingTiers(tiers) {
    if (!elements.onboardingTiersList) return;
    elements.onboardingTiersList.innerHTML = "";
    tiers.forEach(tier => elements.onboardingTiersList.appendChild(renderOnboardingTierButton(tier)));
}

function renderOnboardingTierButton(tier) {
    const btn = document.createElement("button");
    btn.type = "button";
    btn.className = "onboarding-tier";
    btn.dataset.size = tier.size;
    // `meta` overrides the usual "N characters · N sentences" line -- used
    // by Tier 1, which has no sentences by design (character-only practice
    // until ~20 characters are unlocked; see seed_brain.TIER_INFO).
    const meta = tier.meta || `${tier.size} characters &middot; ${tier.sentences.toLocaleString()} sentences`;
    btn.innerHTML = `
        <div class="onboarding-tier-info">
            <div class="onboarding-tier-name">${tier.name}</div>
            <div class="onboarding-tier-meta">${meta}</div>
            <div class="onboarding-tier-blurb">${tier.blurb}</div>
        </div>
        <span class="onboarding-tier-arrow">→</span>
    `;
    btn.addEventListener("click", () => chooseOnboardingTier(tier.size));
    return btn;
}

/**
 * Posts the chosen starting tier and, on success, reloads the session --
 * which now finds a real, seeded brain.json and proceeds normally.
 */
async function chooseOnboardingTier(size) {
    const buttons = elements.onboardingTiersList
        ? elements.onboardingTiersList.querySelectorAll("button")
        : [];
    buttons.forEach(b => (b.disabled = true));
    if (elements.onboardingStatus) {
        elements.onboardingStatus.hidden = false;
        elements.onboardingStatus.textContent = "Setting up your pool…";
    }

    try {
        const response = await fetch(`${API_BASE}/api/onboarding/seed`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ size })
        });
        if (!response.ok) throw new Error(`Onboarding seed failed (${response.status}).`);
        fetchNewSession();
    } catch (err) {
        console.error(err);
        if (elements.onboardingStatus) {
            elements.onboardingStatus.textContent = "Something went wrong setting up your pool. Please try again.";
        }
        buttons.forEach(b => (b.disabled = false));
    }
}

/**
 * Loads the active session sentence and prepares the character canvas.
 */
function loadSession() {
    state.isCompleted = false;
    toggleSidebarButtons(true);
    toggleSentenceAudioControls(false);

    const currentSentence = state.sentences[state.currentIndex];
    if (!currentSentence) return;

    elements.englishPrompt.textContent = currentSentence.english;
    state.charIndex = 0;
    state.hintTier = 0;
    state.charMistakes = 0;
    state.skippedIndices = new Set();

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
    // Characters unlocked but held behind the daily intake cap. Naming them
    // separately is the point of the cap: "12 due, 60 waiting" is a plan,
    // where the old undifferentiated "Due: 72" was just a wall.
    if (elements.backlogNote) {
        const waiting = state.newBacklog || 0;
        elements.backlogNote.hidden = waiting === 0;
        elements.backlogNote.textContent = waiting > 0 ? `+${waiting} waiting` : "";
        elements.backlogNote.title = `${waiting} unlocked character(s) queued for future days — new characters are introduced a few at a time, most frequent first.`;
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
            if (state.skippedIndices.has(idx)) {
                slot.classList.add("skipped-slot");
                slot.title = "Skipped — no stroke data available for this character";
            }
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

    // Every writer gets a token. Hanzi Writer callbacks are asynchronous and
    // can't be unsubscribed, so a walkthrough animation still in flight when
    // the user hits Clear (or finishes the character) would otherwise fire
    // against a writer whose canvas has already been torn down and replaced.
    // Callbacks compare their captured token against the live one and no-op
    // if they've been superseded.
    const token = ++state.writerToken;

    state.writer = HanziWriter.create('tian-zi-ge', targetChar, {
        width: 240,
        height: 240,
        padding: 10,
        showCharacter: false,
        showOutline: false,
        // This is what the tier-3 walkthrough actually animates at:
        // animateCharacter() reads strokeAnimationSpeed from the writer's own
        // options and ignores anything passed into the call itself, so the
        // speed has to be set here. It doesn't affect quiz stroke input.
        strokeAnimationSpeed: HINT_WALKTHROUGH_SPEED,
        delayBetweenStrokes: 150,
        leniency: 1.0,
        // Hanzi Writer defaults this to 3: after three wrong strokes it
        // flashes the correct one, unasked and unrecorded. That is a ghost
        // handwriting guide arriving on its own, which is precisely what the
        // Zero-Help philosophy rules out -- and it made the Hint button
        // optional, since waiting out three mistakes bought the same help
        // without the score cost. Help here is only ever requested.
        showHintAfterMisses: false,
        highlightColor: '#e74c3c',
        drawingColor: '#000000',
        strokeColor: '#333333',
        outlineColor: '#b0b0b0',
        charDataLoader: loadCharacterStrokes,
        onLoadCharDataError: (err) => {
            console.error(`Could not load stroke data for '${targetChar}'.`, err);
            showStrokeDataError(targetChar, token);
        }
    });

    startQuiz(state.writer, targetChar, token);

    renderAssemblyLine();
}

/**
 * Arms (or re-arms) the stroke quiz on a writer. Extracted so the tier-3
 * hint can put the quiz back after animateCharacter() tears it down.
 */
function startQuiz(writer, targetChar, token) {
    if (!writer) return;
    writer.quiz({
        onCorrectStroke: () => {},
        // Wrong strokes are evidence about recall and now feed the SM-2
        // grade (see submitCharacterReview). Counted on state rather than
        // read from the library's own totalMistakes, because the quiz is
        // rebuilt on Clear and after the tier-3 walkthrough -- which would
        // reset the library's counter and hand back a clean slate.
        onMistake: () => {
            if (token !== state.writerToken) return;
            state.charMistakes++;
        },
        onComplete: () => {
            if (token !== state.writerToken) return;
            handleCharacterSuccess(targetChar);
        }
    });
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

    submitCharacterReview(char, state.hintTier, state.charMistakes);

    state.charIndex++;
    state.hintTier = 0;
    state.charMistakes = 0;
    setTimeout(setupCurrentCharacterWriter, 200);
}

/**
 * How much a character's wrong strokes cost it on the 0-5 recall scale.
 * Bucketed rather than subtracted one for one: the difference between a clean
 * write and a stumble is real, and between one stumble and several, but past a
 * handful the extra strokes say nothing new about recall.
 */
function mistakePenalty(mistakes) {
    if (mistakes === 0) return 0;
    if (mistakes <= 2) return 1;
    return 2;
}

/**
 * Reports a completed character quiz to the backend so its SM-2 scheduling
 * fields (interval/factor/reps/last) advance.
 *
 * Recall quality (0-5) comes from both signals the quiz produces: how many
 * hint tiers were needed, and how many wrong strokes were made getting there.
 * Hints alone were not enough -- someone who fumbled twenty strokes but never
 * pressed Hint scored the same perfect 5 as someone who wrote it cleanly, so
 * the scheduler could not tell a shaky character from a solid one.
 *
 * Floored at 2, matching the previous behaviour for heavy-hint completions:
 * quality below 3 already registers as a lapse in SM-2 and resets the
 * repetition streak, and the character *was* eventually written, so a total
 * blackout score of 0 would overstate it.
 *
 * Fire-and-forget -- a failed request shouldn't block practice.
 */
function submitCharacterReview(char, hintTier, mistakes = 0) {
    const quality = Math.max(2, 5 - hintTier - mistakePenalty(mistakes));
    fetch(`${API_BASE}/api/character/review`, {
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

    // Tell the server this sentence is done, so future batches prefer material
    // the user hasn't written yet (finding 12). Fire-and-forget: a failed
    // request must never interrupt the celebration.
    fetch(`${API_BASE}/api/sentence/complete`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ chinese: currentSentence.chinese })
    }).catch(err => console.error("Could not record sentence completion.", err));

    // 1. Flash all slots green
    const slots = document.querySelectorAll(".character-slot");
    slots.forEach(s => s.classList.add("success-flash"));

    // 2. Trigger native audio speech playback
    playNativeTTS(currentSentence.chinese);

    // 3. Reveal the pronunciation controls, which live beside the completed
    //    Chinese sentence rather than inside the victory card.
    toggleSentenceAudioControls(true);

    // 4. Render Victory Card with Mascot and Next button inside Tian Zi Ge
    const container = document.getElementById('tian-zi-ge');
    if (container) {
        container.innerHTML = `
            <div class="victory-card">
                <img src="/avatar-nobg.png" alt="Juzi Mascot" class="victory-mascot" />
                <div class="victory-title">太棒了! Well Done!</div>
                <button id="btn-next" class="btn-next">Next Sentence →</button>
            </div>
        `;

        const btnNext = document.getElementById("btn-next");
        if (btnNext) {
            btnNext.addEventListener("click", nextSentence);
        }
    }
}

/**
 * Shows or hides the sentence pronunciation controls. They're only meaningful
 * once the sentence is done -- offering playback mid-practice would just hand
 * the user the answer they're supposed to be recalling.
 */
function toggleSentenceAudioControls(visible) {
    if (!elements.sentenceAudioControls) return;
    elements.sentenceAudioControls.hidden = !visible;
    if (visible && elements.btnSwitchVoice) {
        updateSwitchVoiceButton(elements.btnSwitchVoice);
    }
}

/**
 * The Chinese text of the sentence currently on screen, or "" if there isn't
 * one. Read at click time rather than captured in a closure, so the audio
 * buttons can be wired once at startup instead of on every completion.
 */
function currentSentenceText() {
    const sentence = state.sentences[state.currentIndex];
    return sentence ? sentence.chinese : "";
}

/**
 * Advances to the next sentence in the session, looping back to the start
 * once the bank is exhausted. Getting a new batch is a deliberate user
 * action via the Import modal (Paste / Suggest Words / HSK), not automatic.
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
        showPinyinPush(targetChar, currentSentence, state.charIndex);
    } else if (state.hintTier === 2) {
        if (state.writer) {
            state.writer.showOutline();
        }
    } else if (state.hintTier >= 3) {
        if (state.writer) {
            // Tier 3: Master Class stroke walkthrough.
            //
            // animateCharacter() starts by calling cancelQuiz() internally,
            // so once the walkthrough finishes the canvas no longer accepts
            // strokes -- the user watches the demo and then can't write the
            // character they just watched. Re-arm the quiz on completion so
            // the staircase ends where it should: shown how, now do it.
            const writer = state.writer;
            const token = state.writerToken;

            writer.animateCharacter({
                onComplete: () => {
                    // Superseded by Clear, or by advancing to another
                    // character while the animation was still playing.
                    if (token !== state.writerToken) return;

                    startQuiz(writer, targetChar, token);

                    // quiz() rebuilds the render state, so re-assert the
                    // outline tier 2 already earned (tier 3 is only reachable
                    // through it): the staircase is cumulative, and a higher
                    // tier should never take away a lower one.
                    writer.showOutline();
                }
            });
        }
    }
}

function showPinyinPush(char, sentenceObj, charIndex) {
    const hintContainer = document.getElementById("hint-display-container");
    if (!hintContainer) return;

    // Prefer char_pinyin, which is indexed by POSITION and so can hold a
    // context-correct reading: 长 is cháng in 很长 but zhǎng in 长大, and a
    // sentence containing both has only one slot for it in char_metadata,
    // which is keyed by character. Fall back to char_metadata for a sentence
    // saved before char_pinyin existed.
    const positional = sentenceObj.char_pinyin && sentenceObj.char_pinyin[charIndex];
    const charMeta = sentenceObj.char_metadata && sentenceObj.char_metadata[char];
    const pinyinResult = positional || (charMeta && charMeta.pinyin) || "";

    // No reading available. The old fallback here printed the literal string
    // "pīn yīn", which reads as a real answer and teaches a nonsense one;
    // saying so plainly is the honest degradation.
    hintContainer.textContent = pinyinResult
        ? `Hint (Tier 1 Pinyin): ${pinyinResult}`
        : "Hint (Tier 1 Pinyin): unavailable for this character";
}

/**
 * Clears the active character canvas so the user can retry writing from scratch.
 *
 * Clear resets the canvas, not the help already earned. setupCurrentCharacterWriter
 * rebuilds the writer from scratch, which wipes the pinyin text and the outline,
 * so hints bought at tiers 1 and 2 silently vanished -- while state.hintTier kept
 * charging for them in the SM-2 grade. The user paid and got nothing back.
 *
 * Restoring them rather than resetting the tier is the deliberate direction: the
 * opposite fix would make Clear a way to launder hints into a clean score, the
 * same reason charMistakes is not reset here either.
 */
function handleClearCanvas() {
    if (state.isCompleted) return;
    if (!state.writer) return;

    state.writer.cancelQuiz();
    setupCurrentCharacterWriter();
    reapplyEarnedHints();
}

/**
 * Puts back the hint tiers already earned for the current character after the
 * canvas has been rebuilt. Tier 3's walkthrough is deliberately not replayed --
 * it is an animation, not a persistent state, and re-running it on every Clear
 * would trap the user watching it again before they could write.
 */
function reapplyEarnedHints() {
    const currentSentence = state.sentences[state.currentIndex];
    if (!currentSentence || state.hintTier < 1) return;

    const chineseChars = Array.from(currentSentence.chinese);
    const targetChar = chineseChars[state.charIndex];
    if (targetChar === undefined) return;

    showPinyinPush(targetChar, currentSentence, state.charIndex);
    if (state.hintTier >= 2 && state.writer) state.writer.showOutline();
}

/**
 * Switches the Import modal between its modes: paste-your-own-text,
 * suggest new words, or pick from local HSK sentences.
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
    if (mode === "chars") loadCharacterSuggestions();
}

/**
 * Dispatches the modal's Submit button to the handler for the active mode.
 */
function handleModalSubmit() {
    if (state.importMode === "hsk") {
        handleGenerateSession();
    } else if (state.importMode === "chars") {
        handleAddSuggestedCharacters();
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
        const response = await fetch(`${API_BASE}/api/suggestions`);
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
        const response = await fetch(`${API_BASE}/api/suggestions/add`, {
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

        let summary = `Added ${result.added_words.length} word(s), unlocking ${result.added_chars_count} new character(s).`;
        // Single characters aren't compound words and aren't stored as such.
        // Say so, and point at the tab that does handle them, rather than
        // reporting a bare "Added 0 words" that looks like a failure.
        if (result.rejected_single_chars && result.rejected_single_chars.length > 0) {
            summary += `\n\nSkipped ${result.rejected_single_chars.join("、")} — single characters are unlocked for practice via the Paste Text tab, not stored as compound words.`;
        }
        alert(summary);

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
    const translation = elements.importTranslationTextarea ? elements.importTranslationTextarea.value.trim() : "";
    if (!text) {
        alert("Please paste some Chinese text first.");
        return;
    }

    elements.modalBtnSubmit.textContent = "Processing...";
    elements.modalBtnSubmit.disabled = true;

    try {
        const response = await fetch(`${API_BASE}/api/import`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ text, translation })
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
 * Fetches a fresh batch of real HSK/Tatoeba example sentences from the local
 * corpus -- no AI, no network, no key needed.
 */
async function handleGenerateSession() {
    elements.modalBtnSubmit.textContent = "Working...";
    elements.modalBtnSubmit.disabled = true;

    try {
        const response = await fetch(`${API_BASE}/api/session/generate`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({})
        });

        const result = await response.json();
        if (!response.ok) throw new Error(result.error || "Sentence generation failed.");

        if (Array.isArray(result.sentences) && result.sentences.length > 0) {
            state.sentences = result.sentences;
            state.currentIndex = 0;
            state.totalUnlockedCount = result.total_unlocked_count ?? state.totalUnlockedCount;
            // The due badge has to move with the new batch too. It was left
            // untouched here, so after generating sentences it kept showing
            // whatever figure the page loaded with -- drifting further from
            // the truth with every review until the next full refresh.
            state.totalDueCount = result.total_due_count ?? state.totalDueCount;
            state.newBacklog = result.new_backlog ?? state.newBacklog;
            updateCharacterCounter();
            updateDueCounter();
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
        btn.textContent = "🔄 Only One Voice";
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

/**
 * True when the event target is somewhere the user is typing, so global
 * keyboard shortcuts should keep their hands off the keystroke. Covers
 * contentEditable as well as form fields -- the modal only has a textarea and
 * an input today, but a shortcut that eats text is a bug that reappears the
 * moment another field is added.
 */
function isTypingTarget(target) {
    if (!target || !target.tagName) return false;
    if (target.isContentEditable) return true;
    return ["INPUT", "TEXTAREA", "SELECT"].includes(target.tagName);
}

/**
 * True while the Add Practice Sentences modal or the onboarding tier picker
 * is on screen. Read from the computed style rather than the inline one, so
 * it's correct before any code has assigned to style.display (the initial
 * "none" comes from the .modal-overlay rule in style.css, not from an inline
 * attribute).
 */
function isModalOpen() {
    return [elements.importModal, elements.onboardingModal].some(
        modal => modal && window.getComputedStyle(modal).display !== "none"
    );
}

function isPunctuation(char) {
    const allowedPunct = "，。！？、 ；：“”‘’—…\t\r\n";
    return allowedPunct.includes(char);
}

/* ==========================================================================
   Suggest Characters
   ==========================================================================
   Characters are the practice unit, so "which character should I learn next?"
   is the most direct question this app can answer -- and until now the only
   way to unlock one was to paste text containing it or add a word built from
   it. Frequency alone is not the whole answer: HSK mode only serves a
   sentence when every character in it is unlocked, so a top-100 character
   that completes no sentence buys no practice today. Both numbers are shown.
   ========================================================================== */

async function loadCharacterSuggestions() {
    if (!elements.charSuggestionsList) return;
    elements.charSuggestionsList.innerHTML = `<p class="suggestions-empty">Loading suggestions…</p>`;

    try {
        const response = await fetch(`${API_BASE}/api/characters/suggestions`);
        if (!response.ok) throw new Error("Failed to fetch character suggestions.");
        const data = await response.json();
        renderCharacterSuggestions(data.suggestions || []);
    } catch (err) {
        console.error(err);
        elements.charSuggestionsList.innerHTML = `<p class="suggestions-empty">Could not load suggestions.</p>`;
    }
}

function renderCharacterSuggestions(suggestions) {
    if (!elements.charSuggestionsList) return;
    elements.charSuggestionsList.innerHTML = "";

    if (suggestions.length === 0) {
        elements.charSuggestionsList.innerHTML =
            `<p class="suggestions-empty">Nothing left to suggest &mdash; you've unlocked every character in the dictionary!</p>`;
        return;
    }

    suggestions.forEach((item, idx) => {
        const row = document.createElement("label");
        row.className = "suggestion-row char-suggestion-row";

        const checkbox = document.createElement("input");
        checkbox.type = "checkbox";
        checkbox.className = "suggestion-checkbox char-suggestion-checkbox";
        checkbox.dataset.char = item.char;
        checkbox.id = `suggest-char-${idx}`;

        const charSpan = document.createElement("span");
        charSpan.className = "suggestion-hanzi";
        charSpan.textContent = item.char;

        const pinyinSpan = document.createElement("span");
        pinyinSpan.className = "suggestion-pinyin";
        pinyinSpan.textContent = item.pinyin;

        const meaningSpan = document.createElement("span");
        meaningSpan.className = "suggestion-meaning";
        meaningSpan.textContent = item.meaning;

        const statsSpan = document.createElement("span");
        statsSpan.className = "suggestion-stats";
        const bits = [`#${item.freq}`];
        if (item.strokes) bits.push(`${item.strokes} strokes`);
        if (item.hsk) bits.push(`HSK ${item.hsk}`);
        statsSpan.textContent = bits.join(" · ");
        statsSpan.title = `Frequency rank ${item.freq} of 9,900`;

        const unlocksSpan = document.createElement("span");
        unlocksSpan.className = "suggestion-unlocks";
        unlocksSpan.classList.toggle("is-zero", !item.unlocks);
        unlocksSpan.textContent = item.unlocks
            ? `unlocks ${item.unlocks}`
            : "unlocks 0";
        unlocksSpan.title = item.unlocks
            ? `${item.unlocks} practice sentence(s) become writable as soon as you add this character.`
            : "No sentence becomes writable from this character alone — it still needs other characters you haven't unlocked.";

        row.append(checkbox, charSpan, pinyinSpan, meaningSpan, statsSpan, unlocksSpan);
        elements.charSuggestionsList.appendChild(row);
    });
}

async function handleAddSuggestedCharacters() {
    const checked = elements.charSuggestionsList
        ? Array.from(elements.charSuggestionsList.querySelectorAll(".char-suggestion-checkbox:checked"))
        : [];

    if (checked.length === 0) {
        alert("Please select at least one character to unlock.");
        return;
    }

    const chars = checked.map(box => box.dataset.char);
    elements.modalBtnSubmit.textContent = "Unlocking...";
    elements.modalBtnSubmit.disabled = true;

    try {
        const response = await fetch(`${API_BASE}/api/characters/add`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ chars })
        });
        if (!response.ok) throw new Error("Failed to unlock the selected characters.");
        const result = await response.json();

        state.totalUnlockedCount = result.total_unlocked_count ?? state.totalUnlockedCount;
        state.totalDueCount = result.total_due_count ?? state.totalDueCount;
        state.newBacklog = result.new_backlog ?? state.newBacklog;
        updateCharacterCounter();
        updateDueCounter();

        alert(`Unlocked ${result.added_chars.length} character(s): ${result.added_chars.join("、")}`);
        elements.importModal.style.display = "none";
        fetchNewSession();
    } catch (err) {
        console.error(err);
        alert("Error connecting to Python server while unlocking characters.");
    } finally {
        elements.modalBtnSubmit.textContent = SUBMIT_LABELS[state.importMode] || "Submit";
        elements.modalBtnSubmit.disabled = false;
    }
}

/* ==========================================================================
   Progress view
   ==========================================================================
   The app accumulated a great deal of state the learner could never see --
   SM-2 intervals, ease factors, which characters were mature, how far into
   the frequency list they had got. All of it existed; none of it was
   visible, so there was no way to answer "am I getting anywhere?", which is
   the question that keeps someone going on a months-long project.

   Chart forms follow the data's job: coverage and stages are magnitudes and
   part-of-wholes, which are bars, and bars are plain HTML here (responsive,
   readable by a screen reader, no coordinate maths). Only the forecast --
   which needs a shared baseline and a time axis -- is drawn as SVG. No chart
   library: this app is offline-first and a CDN dependency would break that.

   Every value is directly labelled, so nothing depends on reading a colour
   or on hovering; `title` carries the secondary detail.
   ========================================================================== */

// Ordinal ramp for the study stages, new -> mature. One hue stepped by
// lightness because the stages are an ordered progression, not unrelated
// categories. Validated against the #262630 card surface: lightness is
// monotonic, contrast runs 2.78:1 to 11.31:1 (ordinal floor is 2:1), and the
// worst adjacent colour-vision-deficiency separation is dE 9.5 (target 8).
const STAGE_STYLE = [
    { key: "new",      label: "New",      color: "#256abf", note: "Unlocked but never reviewed" },
    { key: "learning", label: "Learning", color: "#3987e5", note: "Interval under a week" },
    { key: "young",    label: "Young",    color: "#86b6ef", note: "Interval of one to three weeks" },
    { key: "mature",   label: "Mature",   color: "#cde2fb", note: "Interval over three weeks" }
];

const COVERAGE_COLOR = "#f39c12";   // 6.83:1 on the card surface
const FORECAST_COLOR = "#d95926";   // 3.86:1

async function openProgressView() {
    if (!elements.progressModal) return;
    elements.progressModal.style.display = "flex";
    switchProgressTab("overview");
    elements.progressBody.innerHTML = `<p class="suggestions-empty">Loading…</p>`;

    try {
        const response = await fetch(`${API_BASE}/api/progress`);
        if (!response.ok) throw new Error("Failed to load progress.");
        const data = await response.json();
        renderProgress(data);
        renderProgressCharacters(data.characters || []);
    } catch (err) {
        console.error(err);
        elements.progressBody.innerHTML = `<p class="suggestions-empty">Could not load your progress.</p>`;
    }
}

/**
 * Switches the Progress modal between its Overview and Characters tabs.
 */
function switchProgressTab(tab) {
    elements.progressTabs.forEach(t => {
        t.classList.toggle("active", t.dataset.progressTab === tab);
    });
    elements.progressPanels.forEach(panel => {
        panel.hidden = panel.dataset.progressPanel !== tab;
    });
}

function statTile(value, label, title) {
    return `<div class="stat-tile" title="${escapeAttr(title)}">
        <div class="stat-value">${value}</div>
        <div class="stat-label">${escapeHtml(label)}</div>
    </div>`;
}

/**
 * One horizontal coverage bar: a filled track with the count and percentage
 * labelled directly on the row, so the bar is a quick comparison and the text
 * is the precise answer. 4px rounded end on the fill, anchored to the track.
 */
function coverageRow(label, known, total, title) {
    const pct = total ? Math.round((known / total) * 100) : 0;
    return `<div class="cov-row" title="${escapeAttr(title)}">
        <div class="cov-label">${escapeHtml(label)}</div>
        <div class="cov-track">
            <div class="cov-fill" style="width:${pct}%;background:${COVERAGE_COLOR}"></div>
        </div>
        <div class="cov-value"><strong>${known}</strong><span class="cov-total">/${total}</span> <span class="cov-pct">${pct}%</span></div>
    </div>`;
}

/**
 * The 14-day review forecast, as SVG columns on a shared baseline. A time
 * axis with a zero baseline is the one form here that genuinely needs
 * coordinates; everything else is a bar and stays in HTML.
 */
function forecastChart(forecast) {
    const max = Math.max(1, ...forecast.map(d => d.count));
    const W = 460, H = 132, PAD_L = 26, PAD_B = 20, PAD_T = 8;
    const plotW = W - PAD_L - 8, plotH = H - PAD_B - PAD_T;
    const slot = plotW / forecast.length;
    const barW = Math.max(6, slot - 6);

    const bars = forecast.map((d, i) => {
        const h = d.count ? Math.max(2, (d.count / max) * plotH) : 0;
        const x = PAD_L + i * slot + (slot - barW) / 2;
        const y = PAD_T + plotH - h;
        const day = `In ${d.in_days} day${d.in_days === 1 ? "" : "s"}: ${d.count} character${d.count === 1 ? "" : "s"} due`;
        if (!h) {
            return `<rect x="${x}" y="${PAD_T + plotH - 2}" width="${barW}" height="2" rx="1"
                     fill="var(--border-color)"><title>${escapeAttr(day)}</title></rect>`;
        }
        return `<rect x="${x}" y="${y}" width="${barW}" height="${h}" rx="4"
                 fill="${FORECAST_COLOR}"><title>${escapeAttr(day)}</title></rect>`;
    }).join("");

    // Recessive axis: a baseline and a single max gridline, nothing more.
    const ticks = forecast.map((d, i) =>
        (d.in_days === 1 || d.in_days === 7 || d.in_days === 14)
            ? `<text class="axis-text" x="${PAD_L + i * slot + slot / 2}" y="${H - 6}" text-anchor="middle">${d.in_days}d</text>`
            : "").join("");

    return `<svg class="forecast-svg" viewBox="0 0 ${W} ${H}" role="img"
                 aria-label="Characters due each day for the next fourteen days">
        <line class="axis-line" x1="${PAD_L}" y1="${PAD_T}" x2="${W - 8}" y2="${PAD_T}" stroke-dasharray="2 3"/>
        <text class="axis-text" x="${PAD_L - 6}" y="${PAD_T + 4}" text-anchor="end">${max}</text>
        <line class="axis-line" x1="${PAD_L}" y1="${PAD_T + plotH}" x2="${W - 8}" y2="${PAD_T + plotH}"/>
        <text class="axis-text" x="${PAD_L - 6}" y="${PAD_T + plotH + 4}" text-anchor="end">0</text>
        ${bars}${ticks}
    </svg>`;
}

function renderProgress(p) {
    const totalStages = Object.values(p.stages).reduce((a, b) => a + b, 0);

    // Stacked part-of-whole: one bar, segments separated by a 2px surface gap
    // so adjacent steps of the same hue stay distinguishable, plus a legend
    // that carries the numbers -- identity is never colour alone.
    const segments = STAGE_STYLE
        .filter(s => p.stages[s.key] > 0)
        .map(s => {
            const pct = (p.stages[s.key] / totalStages) * 100;
            return `<div class="stage-seg" style="width:${pct}%;background:${s.color}"
                         title="${escapeAttr(`${s.label}: ${p.stages[s.key]} characters — ${s.note}`)}"></div>`;
        }).join("");

    const legend = STAGE_STYLE.map(s => `
        <div class="legend-item" title="${escapeAttr(s.note)}">
            <span class="legend-swatch" style="background:${s.color}"></span>
            <span class="legend-label">${s.label}</span>
            <span class="legend-value">${p.stages[s.key]}</span>
        </div>`).join("");

    const freqRows = p.frequency_bands.map(b =>
        coverageRow(`Top ${b.band.toLocaleString()}`, b.known, b.total,
            `You can write ${b.known} of the ${b.total} most frequently used characters in Chinese.`)
    ).join("");

    const hskRows = p.hsk_levels.map(l =>
        coverageRow(`HSK ${l.level}`, l.known, l.total,
            `${l.known} of ${l.total} HSK level ${l.level} characters unlocked.`)
    ).join("");

    const topBand = p.frequency_bands[0];
    const headline = topBand
        ? `You can write <strong>${topBand.known}</strong> of the <strong>${topBand.total}</strong> most common characters in Chinese.`
        : "";

    elements.progressBody.innerHTML = `
        <p class="progress-headline">${headline}</p>

        <div class="stat-row">
            ${statTile(p.unlocked_chars.toLocaleString(), "characters unlocked", "Total characters in your practice pool.")}
            ${statTile(p.playable_sentences.toLocaleString(), "sentences writable", "Corpus sentences you can currently write every character of.")}
            ${statTile(p.due_count.toLocaleString(), "due today", "Characters scheduled for review right now.")}
            ${statTile((p.new_backlog || 0).toLocaleString(), "waiting", `Unlocked characters queued for later days. New characters are introduced at most ${p.daily_new_limit} per day, most frequent first.`)}
        </div>

        <section class="progress-section">
            <h3 class="progress-title">Frequency coverage</h3>
            <p class="progress-note">How far into the most-used characters you've got. This is the measure that matters for writing real Chinese &mdash; a large pool of rare characters is worth less than a small pool of common ones.</p>
            ${freqRows}
        </section>

        <section class="progress-section">
            <h3 class="progress-title">Study stages</h3>
            <p class="progress-note">Where your unlocked characters sit in the review schedule.</p>
            <div class="stage-bar">${segments}</div>
            <div class="legend">${legend}</div>
            ${p.avg_interval !== null ? `<p class="progress-note">Average review interval <strong>${p.avg_interval} days</strong>, average ease <strong>${p.avg_factor}</strong>.</p>` : ""}
        </section>

        <section class="progress-section">
            <h3 class="progress-title">Review forecast</h3>
            <p class="progress-note">Characters coming due over the next two weeks.</p>
            ${forecastChart(p.forecast)}
        </section>

        <section class="progress-section">
            <h3 class="progress-title">HSK coverage</h3>
            ${hskRows}
        </section>

        <section class="progress-section">
            <h3 class="progress-title">Sentences</h3>
            <p class="progress-note">
                <strong>${p.sentences_completed_unique.toLocaleString()}</strong> different sentences written
                (<strong>${p.sentences_completed_total.toLocaleString()}</strong> completions in total),
                and <strong>${p.unlocked_words.toLocaleString()}</strong> compound words recorded.
            </p>
        </section>
    `;
}

/**
 * The Characters tab: every unlocked character at a glance, sorted most-
 * common-first (same order as the frequency-coverage bars), for a quick
 * "what have I learned so far" refresher. Reuses STAGE_STYLE's colors so
 * the left-edge color on each tile means the same thing it does in the
 * Overview tab's Study stages legend.
 */
function renderProgressCharacters(characters) {
    if (!elements.progressCharacters) return;

    if (characters.length === 0) {
        elements.progressCharacters.innerHTML = `<p class="suggestions-empty">Nothing unlocked yet -- import some text to get started.</p>`;
        return;
    }

    const stageColor = Object.fromEntries(STAGE_STYLE.map(s => [s.key, s.color]));

    const tiles = characters.map(c => {
        const title = `${c.char}${c.pinyin ? ` — ${c.pinyin}` : ""}${c.meaning ? ` — ${c.meaning}` : ""}`;
        return `<div class="char-tile" style="--stage-color:${stageColor[c.stage] || "transparent"}" title="${escapeAttr(title)}">
            <div class="char-tile-hanzi">${escapeHtml(c.char)}</div>
            <div class="char-tile-pinyin">${escapeHtml(c.pinyin || "")}</div>
            <div class="char-tile-meaning">${escapeHtml(c.meaning || "")}</div>
        </div>`;
    }).join("");

    elements.progressCharacters.innerHTML = `
        <p class="progress-note">${characters.length.toLocaleString()} character${characters.length === 1 ? "" : "s"} unlocked, most common first.</p>
        <div class="char-grid">${tiles}</div>
    `;
}

function escapeHtml(value) {
    return String(value).replace(/[&<>"']/g, ch => (
        { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[ch]
    ));
}

function escapeAttr(value) {
    return escapeHtml(value);
}
