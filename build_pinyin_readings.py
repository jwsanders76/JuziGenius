#!/usr/bin/env python3
"""
Builds pinyin_readings.json -- the context-aware pinyin data behind the
tier-1 hint -- from the pypinyin package (MIT).

WHY THIS EXISTS
---------------
master_dictionary.json stores exactly ONE pinyin per character, and
hanzi_db.csv (its source) pairs some characters' pinyin with a gloss from a
different reading entirely: 地 -> `de` with "earth; soil, ground", 长 ->
`zhǎng` with "long; length". The tier-1 hint showed that single reading, so a
learner writing 长 in 很长 was taught `zhǎng`. Fixing it needs per-POSITION
readings, which needs phrase-level context -- a character's reading depends on
the word it sits in (行 is xíng in 行走 but háng in 银行).

The app's own words_freq.json was measured as an inadequate source: only 31%
of corpus character positions fall inside one of its words, and for the four
worst offenders it is far worse -- 的 0.4%, 了 2.4%, 不 6.9%, 一 20.4%. Those
are standalone grammatical particles, so no word-lookup approach reaches them.
pypinyin ships a 47k-phrase dictionary plus per-character readings ordered
most-common-first, which covers both cases.

WHAT IT WRITES
--------------
pinyin_readings.json, tracked, in the same spirit as stroke_data.json:
  phrases  word -> [reading per character]        (context-correct readings)
  chars    char -> [reading, ...] most-common first (fallback + ambiguity)

SCOPING (default) matches fetch_stroke_data.py's reasoning: keep only phrases
that occur in SENTENCE_SOURCE_FILES or are words the app already knows from
words_freq.json. That is ~3.7k phrases / ~250 KB instead of ~47k / ~1.8 MB.
Everything the app serves by default is covered; pasted text containing an
unlisted phrase degrades to the character's most-common reading, which is
pypinyin's own fallback behaviour. Pass --all to vendor every phrase whose
characters are all in master_dictionary (~1.8 MB) if that trade stops holding.

Like stroke_data.json, this file is single-line minified JSON that git cannot
delta meaningfully, so every regeneration stores a fresh full blob in history.
Prefer one batched regeneration over several incremental ones.

Usage:
    python3 build_pinyin_readings.py           # scoped (default)
    python3 build_pinyin_readings.py --all     # every serveable phrase
"""
import argparse
import csv
import io
import json
import os
import re
import sys
import urllib.request
import zipfile

PYPI_JSON = "https://pypi.org/pypi/pypinyin/json"
OUTPUT = "pinyin_readings.json"
LICENSE_OUTPUT = "pypinyin_source.LICENSE.txt"
MASTER_DICT = "master_dictionary.json"
WORDS_FREQ = "words_freq.json"
SENTENCE_FILES = [
    "hsk_level1and2_words_with_sentences.csv",
    "hsk_level3_words_with_sentences.csv",
    "tatoeba_sentences.csv",
]

# Cases where pypinyin's own output is wrong for this app's purposes, verified
# by hand. Kept deliberately tiny and listed with reasons -- this is the one
# place hand-authored linguistic data enters the pipeline, and every entry
# here is a claim someone has to be able to check.
#
# pypinyin resolves a character outside any known phrase to its most-common
# reading, which is right often enough but not always: 长's most-common
# reading is zhǎng, so 很长 ("very long") comes out hěn zhǎng. Adding the
# two-character phrase is exactly how pypinyin itself would fix it.
PHRASE_OVERRIDES = {
    "很长": ["hěn", "cháng"],      # adverb + adjective: cháng, not zhǎng
    "多长": ["duō", "cháng"],      # "how long"
    "太长": ["tài", "cháng"],
    "长短": ["cháng", "duǎn"],
    # Adverbial 地 is de, but its most-common reading is dì, and pypinyin has
    # no entry for these. Only the forms that actually occur in the corpus are
    # listed -- this is a long tail, not a closed set (see the module note).
    "慢慢地": ["màn", "màn", "de"],
    "好好地": ["hǎo", "hǎo", "de"],
    "轻轻地": ["qīng", "qīng", "de"],
    "认真地": ["rèn", "zhēn", "de"],
    "高兴地": ["gāo", "xìng", "de"],
    "难过地": ["nán", "guò", "de"],
    # Three characters, not 好地: 好地方 is a real word (hǎo dìfāng) and a
    # two-character 好地 entry would fight it. Longest-match makes the longer,
    # unambiguous form safe.
    "更好地": ["gèng", "hǎo", "de"],
    # pypinyin reads 都会 as dū huì, the noun "metropolis". In learner material
    # it is essentially always 都 ("all") + 会 ("will"), as in 每个人都会说谎.
    "都会": ["dōu", "huì"],
    "都是": ["dōu", "shì"],
    "都有": ["dōu", "yǒu"],
}

# Characters whose upstream most-common reading is wrong for THIS corpus.
# Applied to the character table's ordering, not to any phrase.
CHAR_OVERRIDES = {
    # 著 is the traditional form of 着 and leaks into the Tatoeba-derived
    # sentences (跟著他走, 待著). pypinyin ranks zhù first -- correct for
    # simplified 著作 "to author", wrong for every occurrence in this corpus.
    "著": "zhe",
}


def fetch_wheel():
    """Downloads the current pypinyin wheel and returns (zipfile, version)."""
    with urllib.request.urlopen(PYPI_JSON, timeout=60) as r:
        meta = json.load(r)
    version = meta["info"]["version"]
    url = next(u["url"] for u in meta["urls"] if u["packagetype"] == "bdist_wheel")
    print(f"Fetching pypinyin {version} ...")
    with urllib.request.urlopen(url, timeout=180) as r:
        data = r.read()
    print(f"  {len(data) / 1024:.0f} KB")
    return zipfile.ZipFile(io.BytesIO(data)), version


def load_json_member(zf, name):
    return json.loads(zf.read(name))


def corpus_text():
    """Every sentence the app can serve, for scoping."""
    out = []
    for path in SENTENCE_FILES:
        if not os.path.exists(path):
            print(f"  note: {path} missing, skipping")
            continue
        with open(path, encoding="utf-8") as f:
            for row in csv.DictReader(f, delimiter="\t"):
                s = (row.get("sentence") or "").replace(" ", "").strip()
                if s:
                    out.append(s)
    return "\n".join(out)


def phrases_occurring(text, vocab):
    """Longest-match scan for which vocab phrases actually appear in text."""
    if not vocab:
        return set()
    max_len = max(len(w) for w in vocab)
    found = set()
    for run in re.findall(r"[一-龥]+", text):
        for i in range(len(run)):
            for length in range(min(max_len, len(run) - i), 1, -1):
                candidate = run[i:i + length]
                if candidate in vocab:
                    found.add(candidate)
    return found


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--all", action="store_true",
                    help="vendor every phrase whose characters are all in "
                         "master_dictionary.json (~1.8 MB) instead of the "
                         "corpus-scoped subset (~250 KB)")
    args = ap.parse_args()

    if not os.path.exists(MASTER_DICT):
        sys.exit(f"{MASTER_DICT} not found -- run init_vocab_db.py first.")

    zf, version = fetch_wheel()
    upstream_chars = load_json_member(zf, "pypinyin/pinyin_dict.json")
    upstream_phrases = load_json_member(zf, "pypinyin/phrases_dict.json")
    license_text = zf.read(
        f"pypinyin-{version}.dist-info/licenses/LICENSE.txt").decode("utf-8")

    with open(MASTER_DICT, encoding="utf-8") as f:
        serveable = set(json.load(f))

    # --- characters: every serveable char, readings most-common first --------
    chars = {}
    for c in serveable:
        raw = upstream_chars.get(str(ord(c)))
        if raw:
            chars[c] = [r for r in raw.split(",") if r]
    for char, preferred in CHAR_OVERRIDES.items():
        if char in chars:
            rest = [r for r in chars[char] if r != preferred]
            chars[char] = [preferred] + rest
    print(f"chars:   {len(chars)} of {len(serveable)} serveable characters "
          f"({len(CHAR_OVERRIDES)} reordered by hand)")

    # --- phrases -------------------------------------------------------------
    serveable_phrases = {w for w in upstream_phrases if all(c in serveable for c in w)}
    if args.all:
        keep = serveable_phrases
        scope = "all"
    else:
        with open(WORDS_FREQ, encoding="utf-8") as f:
            app_vocab = {w for w in json.load(f)
                         if len(w) >= 2 and not w.startswith("_")}
        occurring = phrases_occurring(corpus_text(), serveable_phrases)
        keep = occurring | (app_vocab & serveable_phrases)
        scope = "corpus+vocab"
        print(f"phrases: {len(occurring)} occur in the corpus, "
              f"{len(app_vocab & serveable_phrases)} are app vocabulary")

    # upstream stores a list of alternatives per character; the first is the
    # one pypinyin itself would use, and alternatives inside a known phrase are
    # not useful here -- the phrase is the disambiguation.
    phrases = {w: [alts[0] for alts in upstream_phrases[w]] for w in keep}

    # Word boundaries the runtime matcher needs even where the readings are
    # unremarkable. pypinyin only lists a phrase when its reading is irregular,
    # so a perfectly regular word like 客人 is absent -- and then longest-match
    # segmentation of 很多客人参加了 consumes 客 alone and matches 人参
    # (ginseng, rén shēn) straight across the word boundary, so 参加 never gets
    # a chance and the learner is taught shēn for cān. Synthesising an entry
    # from each character's most-common reading gives the matcher the boundary
    # it needs; where pypinyin does know the word, its entry always wins.
    synthesized = 0
    with open(WORDS_FREQ, encoding="utf-8") as f:
        vocabulary = [w for w in json.load(f)
                      if len(w) >= 2 and not w.startswith("_")]
    for word in vocabulary:
        if word in phrases or not all(c in chars for c in word):
            continue
        phrases[word] = [chars[c][0] for c in word]
        synthesized += 1

    phrases.update(PHRASE_OVERRIDES)
    print(f"phrases: {len(phrases)} kept ({scope}, {synthesized} synthesized "
          f"word boundaries, {len(PHRASE_OVERRIDES)} hand-authored overrides)")

    payload = {
        "_source": f"pypinyin {version} (https://pypi.org/project/pypinyin/), "
                   f"MIT licensed -- see {LICENSE_OUTPUT}",
        "_scope": scope,
        "_note": "Generated by build_pinyin_readings.py. phrases: word -> one "
                 "reading per character. chars: character -> readings, "
                 "most-common first.",
        "phrases": phrases,
        "chars": chars,
    }
    with open(OUTPUT, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, separators=(",", ":"))
    size = os.path.getsize(OUTPUT) / 1024
    print(f"Wrote {OUTPUT} ({size:.0f} KB)")

    with open(LICENSE_OUTPUT, "w", encoding="utf-8") as f:
        f.write(f"pinyin_readings.json is generated from pypinyin {version}.\n"
                f"Upstream: https://github.com/mozillazg/python-pinyin\n"
                f"Retrieved from PyPI by build_pinyin_readings.py.\n\n"
                f"{license_text}")
    print(f"Wrote {LICENSE_OUTPUT}")


if __name__ == "__main__":
    main()
