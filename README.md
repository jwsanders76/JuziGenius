# JuziGenius 🧠✍️
**JuziGenius** (句子Genius) is a hardcore Mandarin Chinese writing and active-recall translation app, built for daily practice on Linux and deployed to a Pixel tablet/phone over LAN.

Unlike most apps, JuziGenius uses a "Zero-Help" philosophy:
- **Handwriting Only:** No Pinyin input, no predictive text, no ghost outlines by default.
- **Strict Stroke Order:** Every character is validated by *Hanzi Writer* — stroke count, order, and direction all have to be right.
- **Offline-First:** Handwriting, character lookups, compound-word detection, and a full "HSK Sentences" practice mode all work with zero network access and zero API key. The Hanzi Writer library and its stroke-order data are vendored locally rather than pulled from a CDN mid-practice.
- **Optional, Multi-Provider AI:** When you want novel sentences instead of the offline HSK bank, generate them on demand from Gemini, Claude, ChatGPT, or Grok — you pick the provider per request.

## Setup
1. Clone the repo. Everything runs on the Python 3 standard library — no `pip install` needed.
2. Seed a starting vocabulary: `python3 seed_brain.py --size 50` (choose `5`, `50`, or `300`). This creates your personal `brain.json`. Characters are picked for sentence coverage, not raw frequency, so the pool always closes over a set of complete HSK sentences you can practice immediately — a 50-character seed opens 38 of them.
3. Download the offline stroke-order database: `python3 fetch_stroke_data.py`. This is a one-time ~13 MB download that writes a 6.6 MB `stroke_data.json` covering every character the app can present (~2,600). Skip it and handwriting still works, but each character is fetched from a CDN as you're asked to write it — so practice needs a live connection.
4. *(Optional)* For AI-generated sentences with a server-held Gemini key instead of pasting one into the browser each time: rename `config.py.example` to `config.py` and add your key. Claude/ChatGPT/Grok keys are always entered client-side (stored in browser `localStorage`, never on disk) — no server config needed for those.
5. Run the server: `python3 server.py`. It prints `http://localhost:8000` and also binds to your LAN, so you can open that same port from a tablet/phone on the same network.

## What's implemented
- Hardcore handwriting validation with a tiered hint staircase (pinyin → structural outline → stroke walkthrough)
- Offline-first local server — normal play needs no network access, including the handwriting canvas (stroke data is served from the local `stroke_data.json`, with a CDN fallback only for rare characters outside the vendored set, and a readable error plus Skip if neither is available)
- Text import with compound-word detection, backed by a 9,900-character dictionary and a 5,000+ word frequency corpus (HSK 1–6)
- Free, no-AI "HSK Sentences" practice mode using real HSK example sentences
- Multi-provider AI sentence generation (Gemini / Claude / ChatGPT / Grok), client-held API keys
- Audible reinforcement via native text-to-speech on sentence completion, with Replay and Switch Voice controls (cycles through installed Mandarin voices, favoring a male/female split when the device's voices support it)
- Spaced repetition scheduling (SM-2): each completed character quiz grades recall quality from the hint tier used and updates that character's schedule; practice sentences are biased toward characters currently due for review, and a "Due: N" badge in the top bar tracks it live. A character advances its schedule at most once per day — repeats within a session (the same character twice in one sentence, or a looped sentence bank) don't compound the interval — but a failed repeat still applies its lapse

## What's not implemented yet
- **Discuss / grammar breakdown:** placeholder button only.
- **PWA installability:** runs as a plain page, not an installable/offline app.

See `project_state.md` for the full architecture blueprint, file-by-file breakdown, and roadmap.

## License
MIT — see `LICENSE`.
