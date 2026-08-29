# JuziGenius 🧠✍️
**JuziGenius** (句子Genius) is a hardcore Mandarin Chinese writing and active-recall translation app, built for daily practice on Linux and deployed to a Pixel tablet/phone over LAN.

Unlike most apps, JuziGenius uses a "Zero-Help" philosophy:
- **Handwriting Only:** No Pinyin input, no predictive text, no ghost outlines by default.
- **Strict Stroke Order:** Every character is validated by *Hanzi Writer* — stroke count, order, and direction all have to be right.
- **Fully Offline, No AI, No API Keys:** Handwriting, character lookups, compound-word detection, and a full "HSK Sentences" practice mode — over 17,000 real sentences — all work with zero network access. The Hanzi Writer library and its stroke-order data are vendored locally rather than pulled from a CDN mid-practice.

## Setup
1. Clone the repo. Everything runs on the Python 3 standard library — no `pip install` needed.
2. Seed a starting vocabulary: `python3 seed_brain.py --size 5`. This creates your personal `brain.json`. Characters are picked for sentence coverage, not raw frequency, so the pool always closes over a set of complete sentences you can practice immediately.
   - There are four tiers to grow into as you're ready — Tier 1 `--size 5` (First Characters, 7 playable sentences), Tier 2 `--size 50` (Elementary, 93 playable sentences), Tier 3 `--size 300` (Intermediate, 1,633 playable sentences), Tier 4 `--size 500` (Ready to Jump In, 3,358 playable sentences). Just re-run `seed_brain.py` with a bigger `--size` whenever you want to tier up; it only ever *adds* characters on top of what you've already unlocked, so none of your SRS practice progress is lost. Past Tier 4, keep growing your vocabulary in-app via Paste Text or Suggest Words.
3. *(Optional)* The offline stroke-order database (`stroke_data.json`, ~3,300 characters) ships with the repo, so handwriting works offline straight from a clone — no build step. Re-run `python3 fetch_stroke_data.py` only if you want to widen the character set (`--all` vendors every character upstream has).
4. Run the server: `python3 server.py`. It prints `http://localhost:8000` and also binds to your LAN, so you can open that same port from a tablet/phone on the same network.

## What's implemented
- Hardcore handwriting validation with a tiered hint staircase (pinyin → structural outline → stroke walkthrough)
- Offline-first local server — normal play needs no network access, including the handwriting canvas (stroke data is served from the local `stroke_data.json`, with a CDN fallback only for rare characters outside the vendored set, and a readable error plus Skip if neither is available)
- Text import with compound-word detection, backed by a 9,900-character dictionary and a 5,000+ word frequency corpus (HSK 1–6); optionally pair a pasted translation to save real sentences from your own reading into a persistent personal practice bank
- "HSK Sentences" practice mode drawing on 17,400+ real sentences (hand-curated HSK 1-3 examples plus a large Tatoeba-derived corpus, filtered to your known vocabulary) — plus your own saved sentences. No AI, no network, no API keys anywhere in the app.
- Audible reinforcement via native text-to-speech on sentence completion, with Replay and Switch Voice controls (cycles through installed Mandarin voices, favoring a male/female split when the device's voices support it)
- Spaced repetition scheduling (SM-2): each completed character quiz grades recall quality from the hint tier used and updates that character's schedule; practice sentences are biased toward characters currently due for review, and a "Due: N" badge in the top bar tracks it live. A character advances its schedule at most once per day — repeats within a session (the same character twice in one sentence, or a looped sentence bank) don't compound the interval — but a failed repeat still applies its lapse

## What's not implemented yet
- **Discuss / grammar breakdown:** placeholder button only.
- **PWA installability:** runs as a plain page, not an installable/offline app.

## Data sources & attribution
- HSK 1-3 example sentences: hand-curated (`hsk_level1and2_words_with_sentences.csv`, `hsk_level3_words_with_sentences.csv`).
- HSK 4-6 vocabulary: [clem109/hsk-vocabulary](https://github.com/clem109/hsk-vocabulary) (MIT).
- General sentence corpus (`tatoeba_sentences.csv`, filtered from `tatoeba_cmn_eng_source.tsv`): [Tatoeba Project](https://tatoeba.org), via [manythings.org/anki](https://www.manythings.org/anki/) — native-speaker, proofread sentence pairs — licensed [CC BY 2.0 (France)](https://creativecommons.org/licenses/by/2.0/). Per-sentence contributor attribution is preserved in `tatoeba_cmn_eng_source.tsv`.
- Character dictionary (`hanzi_db.csv`, backing `master_dictionary.json`): [ruddfawcett/hanziDB.csv](https://github.com/ruddfawcett/hanziDB.csv) (MIT), itself based on Jun Da's Modern Chinese Character Frequency List.
- Character stroke data: [hanzi-writer-data](https://www.npmjs.com/package/hanzi-writer-data) (MIT).
- Handwriting library (`vendor/hanzi-writer.min.js`): [Hanzi Writer](https://github.com/chanind/hanzi-writer) (MIT).
- Context-aware pinyin readings (`pinyin_readings.json`, built by `build_pinyin_readings.py`): [pypinyin](https://github.com/mozillazg/python-pinyin) (MIT) — see `pypinyin_source.LICENSE.txt`. Used at build time only; the app reads the generated file and makes no network call.

## License
MIT — see `LICENSE`.
