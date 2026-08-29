"""
Extends words_freq.json -- the compound-word detection corpus used by
JuziEngine.analyze_text_compounds() -- with HSK 4-6 vocabulary.

words_freq.json originally held only the ~611 words from the HSK 1-3
"_with_sentences" CSVs (ranks 1-611). This script appends the HSK 4-6 word
list from hsk_level4to6_vocab_source.json (sourced from clem109/hsk-vocabulary,
MIT licensed -- see that file's "_source" field), continuing the rank
sequence and skipping any word already present. It never touches brain.json.

These HSK 4-6 words have no real example sentences (no free/open dataset
provides them past HSK 3), so they widen text-import compound detection and
the vocabulary pool only -- they won't appear in the no-AI "HSK Sentences"
practice mode, which stays limited to hsk_level1and2/3_words_with_sentences.csv.
"""
import json

WORDS_PATH = "words_freq.json"
SOURCE_PATH = "hsk_level4to6_vocab_source.json"


def clean_meaning(translations):
    kept = [t for t in translations if not t.startswith("CL:")]
    if not kept:
        kept = translations
    return "; ".join(kept)


def build():
    with open(WORDS_PATH, "r", encoding="utf-8") as f:
        words = json.load(f)

    with open(SOURCE_PATH, "r", encoding="utf-8") as f:
        source = json.load(f)

    next_rank = max(v["rank"] for k, v in words.items() if not k.startswith("_")) + 1
    added = 0
    for level in ("4", "5", "6"):
        for entry in source["levels"][level]:
            word = entry["hanzi"].strip()
            if not word or word in words:
                continue
            words[word] = {
                "pinyin": entry["pinyin"].strip(),
                "meaning": clean_meaning(entry["translations"]),
                "rank": next_rank,
            }
            next_rank += 1
            added += 1

    # Count the entries actually present rather than deriving it from the
    # rank counter: the two agree only while nothing is ever removed, and
    # a stale total is worse than no total.
    words["_metadata"] = {"total_words": sum(1 for w in words if not w.startswith("_"))}
    return words, added


if __name__ == "__main__":
    words, added = build()
    with open(WORDS_PATH, "w", encoding="utf-8") as f:
        json.dump(words, f, ensure_ascii=False, indent=4)
    print(f"Added {added} words. Total: {words['_metadata']['total_words']}")
