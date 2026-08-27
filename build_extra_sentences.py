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
*full* dictionary at build time. There's no per-sentence pinyin in the source,
so sentence_pinyin is left blank like the rest of this schema's unused columns
(word/word_meaning/word_pinyin) -- pick_hsk_sentences doesn't read them.

Usage:
    python3 build_extra_sentences.py
"""
import csv
import json

SOURCE_PATH = "tatoeba_cmn_eng_source.tsv"
MASTER_DICT_PATH = "master_dictionary.json"
OUTPUT_PATH = "tatoeba_sentences.csv"
ALLOWED_PUNCT = "，。！？、；：“”‘’—…"

MIN_HANZI = 2
MAX_HANZI = 25


def build():
    with open(MASTER_DICT_PATH, "r", encoding="utf-8") as f:
        master = json.load(f)

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

            if not chinese or not english or chinese in seen:
                continue
            if not all((c in master) or (c in ALLOWED_PUNCT) for c in chinese):
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
