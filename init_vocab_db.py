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
            entry = {
                "pinyin": row["pinyin"].strip(),
                "meaning": row["definition"].strip(),
            }
            # Frequency rank, HSK level and stroke count are carried through
            # rather than dropped. This app exists to teach the most frequently
            # used characters, and without the rank at runtime it could not
            # answer its own central question -- "what is the most useful
            # character I don't know yet?" -- nor order the queue of newly
            # unlocked characters by usefulness.
            #
            # seed_brain.py previously recovered frequency by enumerating this
            # file's keys, relying on JSON preserving the insertion order that
            # happens to match frequency_rank. That held, but nothing asserted
            # it: re-serialising this file sorted would have silently turned
            # "frequency order" into codepoint order with no error anywhere.
            # Storing the rank makes the dependency explicit and checkable.
            for key, column in (("freq", "frequency_rank"),
                                ("strokes", "stroke_count"),
                                ("hsk", "hsk_level")):
                raw = (row.get(column) or "").strip()
                if raw:
                    try:
                        entry[key] = int(raw)
                    except ValueError:
                        pass
            dictionary[char] = entry
    return dictionary


if __name__ == "__main__":
    dictionary = build_master_dictionary()
    with open(OUTPUT_PATH, "w", encoding="utf-8") as f:
        json.dump(dictionary, f, ensure_ascii=False, indent=2)
    print(f"Wrote {len(dictionary)} characters to {OUTPUT_PATH}")
