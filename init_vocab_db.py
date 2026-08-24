"""
Regenerates master_dictionary.json -- the character -> pinyin/meaning lookup
used by JuziEngine.import_text_locally() -- from hanzi_db.csv.

master_dictionary.json is derived, reference data (like words_freq.json), so
it's kept as its own tracked file rather than inside brain.json, which is
gitignored because it also holds personal SRS progress. Re-run this script
any time hanzi_db.csv changes, or to rebuild master_dictionary.json from
scratch if it's ever lost. It never touches brain.json.
"""
import csv
import json

SOURCE_CSV = "hanzi_db.csv"
OUTPUT_PATH = "master_dictionary.json"


def build_master_dictionary(source_csv=SOURCE_CSV):
    dictionary = {}
    with open(source_csv, "r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            char = row["character"].strip()
            if not char or char in dictionary:
                # CSV is ordered by frequency_rank ascending; keep the
                # first (most frequent) reading if a character repeats.
                continue
            dictionary[char] = {
                "pinyin": row["pinyin"].strip(),
                "meaning": row["definition"].strip()
            }
    return dictionary


if __name__ == "__main__":
    dictionary = build_master_dictionary()
    with open(OUTPUT_PATH, "w", encoding="utf-8") as f:
        json.dump(dictionary, f, ensure_ascii=False, indent=2)
    print(f"Wrote {len(dictionary)} characters to {OUTPUT_PATH}")
