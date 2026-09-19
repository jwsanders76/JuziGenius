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
import re

SOURCE_CSV = "hanzi_db.csv"
OUTPUT_PATH = "master_dictionary.json"
OVERRIDES_PATH = "meaning_overrides.json"
SCRIPT_MAP_PATH = "char_script_map.json"

# Senses a learner never needs: surname tags, radical labels, classical /
# archaic / dialect notes, and pointers to variant forms (including ones the
# source truncated mid-sentence, like "simplified form of").
_UNWANTED_SENSE = re.compile(
    r"^\(?surname\b|radical|^rad\.|classical|archaic|literary|obsolete|dialect"
    r"|^\(?(simp\.? for|simplified form|same as|variant of|abbr\.? of)"
    r"|non-simplified form",
    re.IGNORECASE,
)


# A whole meaning that is only the start of a sentence the source cut off.
_TRUNCATED_FRAGMENT = re.compile(
    r"\(?(simp\.? for|simplified form of|same as|variant of|abbr\.? of)", re.IGNORECASE
)


def clean_meaning(meaning):
    """Drop the senses above. If that would leave nothing, keep the original
    (a rare character with only a surname sense is better labelled than blank),
    unless the original is just a truncated fragment, which teaches nothing."""
    senses = [s.strip() for s in meaning.split(";")]
    kept = [s for s in senses if s and not _UNWANTED_SENSE.search(s)]
    if kept:
        return "; ".join(kept)
    return "" if _TRUNCATED_FRAGMENT.fullmatch(meaning.strip()) else meaning


def load_overrides(path=OVERRIDES_PATH):
    """Hand-written everyday meanings for the most common characters. They live
    in their own file because this script regenerates master_dictionary.json
    and would wipe a hand edit made there."""
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def fill_from_simplified_twin(dictionary, script_map_path=SCRIPT_MAP_PATH):
    """A character with no meaning that is only the traditional form of a
    simplified one (陰 for 阴) takes that character's meaning. Skipped when
    several simplified characters merge into it and they disagree."""
    with open(script_map_path, "r", encoding="utf-8") as f:
        simplified_to_traditional = json.load(f)
    twins = {}
    for simplified, traditional in simplified_to_traditional.items():
        if simplified != traditional:
            twins.setdefault(traditional, []).append(simplified)
    for char, sources in twins.items():
        entry = dictionary.get(char)
        if not entry or entry["meaning"].strip():
            continue
        meanings = {dictionary[s]["meaning"] for s in sources
                    if s in dictionary and dictionary[s]["meaning"].strip()
                    and not _UNWANTED_SENSE.search(dictionary[s]["meaning"])}
        if len(meanings) == 1:
            entry["meaning"] = meanings.pop()


def build_master_dictionary(source_csv=SOURCE_CSV, overrides_path=OVERRIDES_PATH):
    overrides = load_overrides(overrides_path)
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
                "meaning": overrides.get(char) or clean_meaning(row["definition"].strip()),
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
    fill_from_simplified_twin(dictionary)
    return dictionary


if __name__ == "__main__":
    dictionary = build_master_dictionary()
    with open(OUTPUT_PATH, "w", encoding="utf-8") as f:
        json.dump(dictionary, f, ensure_ascii=False, indent=2)
    print(f"Wrote {len(dictionary)} characters to {OUTPUT_PATH}")
