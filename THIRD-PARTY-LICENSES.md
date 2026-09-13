# Third-party licenses and attribution

JuziGenius bundles data and code from the projects below. The MIT license in
`LICENSE` covers **only the source code written for this project** — it does not
cover anything listed here. Each item keeps its own license.

If you fork, redistribute, or run this commercially, these terms travel with the
data. The two that carry real conditions are the stroke data (Arphic Public
License) and the Tatoeba sentences (attribution required).

---

## Stroke-order data — `stroke_data.json`

**Arphic Public License.** Full text in [`ARPHICPL.TXT`](ARPHICPL.TXT).

Vendored by `fetch_stroke_data.py` from the
[hanzi-writer-data](https://github.com/chanind/hanzi-writer-data) package, which
states:

> This data comes from the Make Me A Hanzi project, which extracted the data
> from fonts by Arphic Technology, a Taiwanese font forge that released their
> work under a permissive license in 1999.

Chain of provenance:

- Arphic Technology Co., Ltd. — AR PL UMing / AR PL KaitiM fonts, © 1999
- [Make Me a Hanzi](https://github.com/skishore/makemeahanzi) — extracted stroke
  graphics and medians from those fonts
- [hanzi-writer-data](https://github.com/chanind/hanzi-writer-data) — repackaged
  for Hanzi Writer

The Arphic Public License permits commercial use, copying and modification. It
requires that the license and copyright notices travel with the data, that
modifications be documented, and that derivative works of the data remain under
the same license. It applies to the **stroke data**, not to this application's
own source code.

> **Note:** `fetch_stroke_data.py` previously described this data as MIT. That
> was incorrect — the *Hanzi Writer library* is MIT, the *character data* is not.

## Hanzi Writer — `vendor/hanzi-writer.min.js`

**MIT License.** Copyright (c) 2014 David Chanin.
<https://github.com/chanind/hanzi-writer> — v3.5.0

> Permission is hereby granted, free of charge, to any person obtaining a copy of
> this software and associated documentation files (the "Software"), to deal in
> the Software without restriction, including without limitation the rights to
> use, copy, modify, merge, publish, distribute, sublicense, and/or sell copies
> of the Software, and to permit persons to whom the Software is furnished to do
> so, subject to the following conditions:
>
> The above copyright notice and this permission notice shall be included in all
> copies or substantial portions of the Software.
>
> THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
> IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
> FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
> AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
> LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
> OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
> SOFTWARE.

## Example sentences — `tatoeba_sentences.csv`

**CC BY 2.0 FR** — <https://creativecommons.org/licenses/by/2.0/fr/>

Mandarin–English sentence pairs from the [Tatoeba Project](https://tatoeba.org),
obtained via the [manythings.org/anki](https://www.manythings.org/anki/) export
and filtered by `build_extra_sentences.py`.

Tatoeba sentences are contributed by its members. Commercial use is permitted;
**attribution is required**, which is why Tatoeba is credited on the in-app
credits page and here.

## Character dictionary — `hanzi_db.csv` → `master_dictionary.json`

Character definitions, radicals, stroke counts and frequency ranks. The
definition text follows the conventions of the **Unicode Han Database (Unihan)**
`kDefinition` field, distributed under the
[Unicode License](https://www.unicode.org/license.txt), which permits commercial
use with attribution.

© 1991–present Unicode, Inc. All rights reserved.

## Word list and glosses — `words_freq.json`

Two distinct sources:

- **HSK 1–3 entries (ranks 1–611)** — compiled for this project alongside the
  hand-written example sentences in `hsk_level1and2_words_with_sentences.csv` and
  `hsk_level3_words_with_sentences.csv`.
- **HSK 4–6 entries (ranks 612+)** — from
  [clem109/hsk-vocabulary](https://github.com/clem109/hsk-vocabulary) (MIT),
  which sources from
  [gigacool/hanyu-shuiping-kaoshi](https://github.com/gigacool/hanyu-shuiping-kaoshi).

> **Unresolved provenance.** The HSK 4–6 glosses use formatting conventions
> characteristic of [CC-CEDICT](https://www.mdbg.net/chinese/dictionary?page=cc-cedict)
> — classifier annotations (`CL:個|个[gè]`), pronunciation notes (`Taiwan pr.`),
> and cross-references (`erhua variant of`). CC-CEDICT is distributed under
> **CC BY-SA 4.0**, which permits commercial use but requires attribution and
> share-alike on the data. Neither upstream repository acknowledges a dictionary
> source, so the chain cannot be confirmed from the repositories alone.
>
> CC-CEDICT is credited here on that basis. If you rely on this data, resolve the
> provenance before treating `words_freq.json` as MIT-licensed. Note that CC
> share-alike would attach to the data file, not to this application's code —
> Creative Commons licenses do not propagate through linking the way the GPL does.

## HSK vocabulary lists

The HSK levels themselves are published by Hanban / Chinese Testing
International. The word lists are used here as reference data.

## Text-to-speech audio — removed

**Resolved: no third-party audio is distributed.** Speech is synthesized on the
user's own device by the browser's Web Speech API, using whichever Mandarin
voices that device has installed. Nothing about that passes through this
project, so no license attaches to it.

There was previously a pre-generated path: ~700 MB of MP3s produced by
`build_speech_audio.py` with [Piper](https://github.com/rhasspy/piper) (MIT) and
served from `/api/speech`. It was removed — from the app, the server and the
host — because Piper's *engine* is MIT but its *voice models* are licensed
separately, and both `zh_CN` voices used (`huayan`, `chaowen`) trace to the
[HuaYan_TTS](https://github.com/PlayVoice/HuaYan_TTS) dataset, whose license the
upstream Piper model card records as **"Unknown"**. The bundled
`model.onnx.json` files carry no license field either.

"Unknown" is not a grant of permission, and this app is heading for a paid tier.
`build_speech_audio.py` is kept, marked not-in-use, in case a properly-licensed
voice is adopted later; the serving code is in git history.

## Build-time tools

These generate vendored data but are not shipped or linked at runtime.

| Tool | License | Produces |
|---|---|---|
| [pypinyin](https://github.com/mozillazg/python-pinyin) | MIT | `pinyin_readings.json` |
| [opencc-python-reimplemented](https://github.com/yichen0831/opencc-python) | Apache 2.0 | `char_script_map.json` |
| [Piper](https://github.com/rhasspy/piper) | MIT | `speech_audio/` (see above) |

OpenCC conversion tables (`STCharacters.txt`, `TWVariants.txt`,
`HKVariants.txt`) are © [BYVoid](https://github.com/BYVoid/OpenCC) under the
Apache License 2.0.

---

## Draft language for a Terms of Service

*Not legal advice — draft wording to hand a lawyer, not to publish unreviewed.*

A normal Terms of Service says something like "you may not copy, reproduce or
redistribute any material from the Service." That clause, written without a
carve-out, collides with two of the licenses above:

- **Arphic Public License, section 5:** *"You may not impose any further
  restrictions on the recipients' exercise of the rights granted herein."*
  Everyone who receives the stroke data automatically receives an Arphic license
  to copy, modify and redistribute it. A blanket no-redistribution clause purports
  to take that back, which you have no standing to do.
- **CC BY 2.0 FR / CC BY-SA 4.0** (Tatoeba, and CC-CEDICT if confirmed) grant
  recipients similar rights that your terms cannot withdraw.

This matters more once the Service is paid, because that is exactly when a
restrictive redistribution clause tends to get written. Suggested wording:

> **Third-party content.** Parts of the Service are provided to you under open
> licenses granted by their original authors, not by us. These include the
> stroke-order data (Arphic Public License), example sentences from the Tatoeba
> Project (CC BY 2.0 FR), character definitions derived from the Unicode Han
> Database, and word glosses that may derive from CC-CEDICT (CC BY-SA 4.0). A
> current list, with licenses, is published at /credits.html.
>
> Nothing in these Terms limits — and we do not purport to limit — any right you
> hold in that third-party content under its own license, including any right to
> copy, modify or redistribute it. Where those licenses require that you receive
> the same rights we did, you do.
>
> Any restriction in these Terms on copying or redistributing material from the
> Service applies only to the parts we own: our software and interface, the
> example sentences written for this project, and account data. It does not apply
> to the third-party content described above.

Two practical consequences worth deciding deliberately rather than discovering:

1. **Paywalling the stroke data is fine. Forbidding its redistribution is not.**
   You may charge for access to the Service. You may not tell a subscriber they
   have no right to redistribute the Arphic-licensed data they received.
2. **Taking this repository private would breach Arphic section 2(b).** That
   section requires modifications — and `stroke_data.json` is a modification, being
   a subset repacked into one file — to stay "Freely Available as a whole to all
   third parties under the terms of this License." A public repository satisfies
   that today. Closing the source when you start charging, which is a natural
   instinct, would break it unless the stroke data is published somewhere else
   under the same terms.

## Corrections

Attribution errors here are bugs. If something is miscredited, mis-licensed, or
missing, please open an issue at
<https://github.com/jwsanders76/JuziGenius/issues>.
