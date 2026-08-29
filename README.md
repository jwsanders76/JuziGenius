# JuziGenius 🧠✍️
**JuziGenius** (句子Genius) is a hardcore Mandarin Chinese writing and active-recall translation app, hosted as a web service. You practise at your own `/u/<link>/` address; the bare domain is a landing page.

Unlike most apps, JuziGenius uses a "Zero-Help" philosophy:
- **Handwriting Only:** No Pinyin input, no predictive text, no ghost outlines by default.
- **Strict Stroke Order:** Every character is validated by *Hanzi Writer* — stroke count, order, and direction all have to be right.
- **No AI, No API Keys, No Third-Party Calls:** handwriting, character lookups, compound-word detection and a 17,000-sentence practice corpus are all served from data in this repo. The server never calls out to anything — no model, no dictionary API, no CDN during practice (the Hanzi Writer library and its stroke data are vendored). Your text and your progress stay on the server you run.

## Deploying
1. Clone the repo on the host. Everything runs on the Python 3 standard library — no `pip install` needed.
2. Put a domain in front of it over HTTPS. The tracked `Caddyfile` reverse-proxies to `server.py` and gets a Let's Encrypt certificate automatically; its comments cover the one-time host setup. HTTPS is not optional — a `/u/<slug>/` link *is* the credential for that account, so it must not travel in clear text.
3. Run the server: `python3 server.py`. It binds `127.0.0.1` so port 8000 is reachable only from the reverse proxy on the same machine, never directly from the internet.
4. Give each person an account: `python3 create_user.py`. This provisions `users/<slug>/` with its own seeded `brain.json` and prints the link to send them. The link is the credential — send it privately, and note there is no password reset yet, so a lost link means a lost account.
5. The bare domain serves a landing page. There is no default account behind it: `/` and `/api/*` reach no data, so anyone who finds the domain gets a front door rather than someone's practice history.

Characters in a new account are picked for sentence coverage rather than raw frequency, so the pool always closes over complete sentences that can be practised straight away. `seed_brain.py --size N` grows an existing pool through four tiers (5 / 50 / 300 / 500 characters — 7 / 90 / 1,632 / 3,357 playable sentences); it only ever *adds* characters, so no SRS progress is lost. Past that, vocabulary grows in-app via Paste Text, Suggest Characters and Suggest Words.

The stroke-order database (`stroke_data.json`, ~3,300 characters) and the context-aware pinyin data ship with the repo, so there is no build step. Re-run `fetch_stroke_data.py` or `build_pinyin_readings.py` only to widen those sets.

## What's implemented
- Hardcore handwriting validation with a tiered hint staircase (pinyin → structural outline → stroke walkthrough)
- Self-contained server — no third-party calls during practice, including the handwriting canvas (stroke data comes from the local `stroke_data.json`, with a CDN fallback only for rare characters outside the vendored set, and a readable error plus Skip if neither is available)
- Text import with compound-word detection, backed by a 9,900-character dictionary and a 5,000+ word frequency corpus (HSK 1–6); optionally pair a pasted translation to save real sentences from your own reading into a persistent personal practice bank
- "HSK Sentences" practice mode drawing on 17,400+ real sentences (hand-curated HSK 1-3 examples plus a large Tatoeba-derived corpus, filtered to your known vocabulary) — plus your own saved sentences. No AI, no network, no API keys anywhere in the app.
- Audible reinforcement via native text-to-speech on sentence completion, with Replay and Switch Voice controls (cycles through installed Mandarin voices, favoring a male/female split when the device's voices support it)
- **Suggest Characters** — the most frequently used characters you haven't unlocked yet, each showing how many practice sentences it immediately makes writable (a common character that completes no sentence buys you no practice today)
- **Progress view** — frequency-list coverage ("you can write 40 of the 100 most common characters in Chinese"), HSK coverage, study stages, and a 14-day review forecast. Charts are hand-built in HTML and inline SVG; no chart library, so nothing here needs the network
- New characters are introduced at a steady pace (default 15/day, `settings.daily_new_limit` in `brain.json`), most frequent first, so pasting a long text queues characters rather than dumping hundreds into review at once
- **Installable** — add your `/u/<link>/` page to a phone or tablet home screen and it opens in its own window with a home-screen icon instead of a browser tab. It is not an offline app: nothing is cached, and it needs the server exactly as the website does. Requires HTTPS (see the `Caddyfile`)
- Spaced repetition scheduling (SM-2): each completed character quiz grades recall quality from the hint tier used and updates that character's schedule; practice sentences are biased toward characters currently due for review, and a "Due: N" badge in the top bar tracks it live. A character advances its schedule at most once per day — repeats within a session (the same character twice in one sentence, or a looped sentence bank) don't compound the interval — but a failed repeat still applies its lapse

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
