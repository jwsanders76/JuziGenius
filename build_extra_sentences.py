"""
Filters tatoeba_cmn_eng_source.tsv (the raw Tatoeba Mandarin<->English export,
via manythings.org/anki -- native-speaker, proofread sentence pairs, CC BY 2.0
France) down to the sentences JuziGenius can actually vendor and serve, and
writes them out as tatoeba_sentences.csv in the same tab-delimited schema as
the two hand-curated HSK CSVs (hsk_level1and2_words_with_sentences.csv,
hsk_level3_words_with_sentences.csv), so juzi_engine.py's SENTENCE_SOURCE_FILES
can read it with no reader-side changes.

Raw Tatoeba pairs get rejected for the same two reasons a pasted-text
character can't be unlocked: no pinyin/meaning to teach it with, or it isn't
actually the Chinese script this app teaches. Concretely, a sentence is kept
only if every character in it is either standard Chinese punctuation or
present in master_dictionary.json (which is itself sourced from a simplified-
Chinese character list) -- so sentences containing digits, Latin script,
traditional-only characters, or anything else outside that 9,900-character
set are dropped. This is the same character-membership test pick_hsk_sentences
already applies against the user's *unlocked* pool, just run here against the
*full* dictionary at build time.

**And it must have stroke data (added September 19, 2026).** The dictionary
test alone is not enough, because master_dictionary.json's 9,900 characters
are a superset of the 9,531 JuziGenius vendors stroke order for. This is a
handwriting app: a sentence containing a character whose strokes cannot be
taught is unservable by definition -- the learner reaches it, gets the
"can't be practised yet" card, and has to skip. It let 24 sentences through,
all of them traditional-script text that slipped past the simplified filter
because every character in them happened to be in the dictionary, using 箇
for 個/个 (22 sentences), plus one 覚 and one 迺. Requiring stroke data is a
better rule than a list of the three offending characters: it states the
actual requirement, and it cannot go stale the way a blocklist would.

**Traditional sentences are converted, not dropped (added September 19,
2026).** Roughly 15% of the kept corpus was traditional-script text -- 開打。,
走開！, 我在家裡。 -- which passed every filter because traditional forms like
開, 點 and 裡 are all in the dictionary and all have stroke data. That matters
because this app's script identity is always simplified (see
apply_character_script in juzi_engine.py): a sentence stored traditional is
unlocked and scheduled as traditional characters, so a learner in the default
simplified mode was simply served traditional text. Deleting them would have
thrown away a seventh of the corpus of perfectly good sentences, so they are
converted instead, character by character, before every other test runs --
which also means the dedup below collapses a converted sentence against the
simplified one that was already there.

The table is trad_to_simp.json (see build_trad_to_simp.py), which comes from
OpenCC's own TSCharacters.txt. **It is deliberately not the inverse of
char_script_map.json**, which is the forward map and is lossy in two ways that
make its inverse wrong -- it is TW/HK-variant corrected, so 脫 is missing from
it entirely and 摆脫 went unconverted; and 126 of its traditional values are
themselves ordinary simplified characters (了, 出, 只, 台, 家), so inverting
wholesale would rewrite 了 in every sentence in the corpus. That reasoning is
written out in full in build_trad_to_simp.py, and is worth reading before
anyone is tempted to drop this file's dependency and just flip the other map.

There's no per-sentence pinyin in the source,
so sentence_pinyin is left blank like the rest of this schema's unused columns
(word/word_meaning/word_pinyin) -- pick_hsk_sentences doesn't read them.

Usage:
    python3 build_extra_sentences.py
"""
import csv
import json

SOURCE_PATH = "tatoeba_cmn_eng_source.tsv"
MASTER_DICT_PATH = "master_dictionary.json"
# The byte-offset index over stroke_data.json, whose "entries" keys are
# exactly the characters this app can teach the strokes for. The index is
# read rather than the 30MB data file itself, since only the key set matters
# here and the index is built from that file (see fetch_stroke_data.py).
STROKE_INDEX_PATH = "stroke_data.index.json"
# Traditional -> simplified, from OpenCC via build_trad_to_simp.py.
TRAD_TO_SIMP_PATH = "trad_to_simp.json"
OUTPUT_PATH = "tatoeba_sentences.csv"
ALLOWED_PUNCT = "，。！？、；：“”‘’—…"

MIN_HANZI = 2
MAX_HANZI = 25


def load_trad_to_simp():
    """The vendored traditional -> simplified character table."""
    with open(TRAD_TO_SIMP_PATH, "r", encoding="utf-8") as f:
        return json.load(f)["map"]


def build():
    with open(MASTER_DICT_PATH, "r", encoding="utf-8") as f:
        master = json.load(f)

    with open(STROKE_INDEX_PATH, "r", encoding="utf-8") as f:
        strokes = set(json.load(f)["entries"])

    trad_to_simp = load_trad_to_simp()

    seen = set()
    kept = 0
    total = 0

    with open(SOURCE_PATH, "r", encoding="utf-8") as src, \
         open(OUTPUT_PATH, "w", encoding="utf-8", newline="") as out:
        writer = csv.writer(out, delimiter="\t")
        writer.writerow(["word", "word_meaning", "word_pinyin", "sentence", "sentence_pinyin", "sentence_meaning"])

        for line in src:
            parts = line.rstrip("\n").split("\t")
            if len(parts) < 2:
                continue
            total += 1
            english, chinese = parts[0].strip(), parts[1].replace(" ", "").strip()

            # Before every other test, so the dedup below compares converted
            # text against converted text (a traditional sentence and its
            # simplified twin collapse to one row), and so the membership and
            # stroke checks run against the characters that will actually be
            # served rather than the ones the source happened to use.
            chinese = "".join(trad_to_simp.get(c, c) for c in chinese)

            if not chinese or not english or chinese in seen:
                continue
            # Teachable: in the dictionary (so it has a pinyin and a meaning)
            # AND vendored in the stroke data (so it can actually be written).
            if not all((c in master and c in strokes) or (c in ALLOWED_PUNCT)
                       for c in chinese):
                continue
            hanzi_count = sum(1 for c in chinese if c in master)
            if hanzi_count < MIN_HANZI or hanzi_count > MAX_HANZI:
                continue

            seen.add(chinese)
            kept += 1
            writer.writerow(["", "", "", chinese, "", english])

    print(f"Kept {kept} of {total} Tatoeba sentence pairs -> {OUTPUT_PATH}")


if __name__ == "__main__":
    build()
