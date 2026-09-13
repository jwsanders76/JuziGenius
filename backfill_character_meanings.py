"""
Re-syncs a blank stored character meaning against the current
master_dictionary.json.

unlocked_chars[char]["meaning"] (brain.json) is copied from
master_dictionary.json once, at the moment a character is unlocked (see
add_characters/import_text_locally in juzi_engine.py) -- it is never read
again afterward. Fixing a gap in master_dictionary.json (hanzi_db.csv, then
init_vocab_db.py) therefore only helps characters unlocked *after* the fix;
anyone who already unlocked the character keeps whatever was there at the
time, blank included, forever. Found this way: a user reported 您 showing no
English prompt after it had already been fixed in the dictionary (commit
"Fill in missing English definitions for 32 HSK characters") -- their own
account had unlocked it earlier and never got the update.

This is the other half of that kind of fix: for every users/<slug>/brain.json
(and the root brain.json, for a single-account/local install), any unlocked
character whose stored meaning is blank gets overwritten with
master_dictionary.json's current value, if that's now non-blank. Never
touches a character that already has a real meaning -- there's no scenario
where a meaning was deliberately left blank on purpose, so this is a pure
repair, not a sync that could clobber something intentional. Safe to re-run
any time a dictionary gap gets patched; a brain.json with nothing to fix is
left untouched (not even rewritten) to avoid needless diffs/timestamps.

Usage:
    python3 backfill_character_meanings.py
"""
import json
import os

MASTER_DICT_PATH = "master_dictionary.json"
USERS_DIR = "users"
ROOT_BRAIN_PATH = "brain.json"


def find_brain_paths():
    paths = []
    if os.path.exists(ROOT_BRAIN_PATH):
        paths.append(ROOT_BRAIN_PATH)
    if os.path.isdir(USERS_DIR):
        for entry in sorted(os.listdir(USERS_DIR)):
            brain_path = os.path.join(USERS_DIR, entry, "brain.json")
            if os.path.isfile(brain_path):
                paths.append(brain_path)
    return paths


def backfill(brain_path, master):
    with open(brain_path, "r", encoding="utf-8") as f:
        brain_data = json.load(f)

    unlocked = brain_data.get("unlocked_chars") or {}
    fixed = []
    for char, meta in unlocked.items():
        if (meta.get("meaning") or "").strip():
            continue
        new_meaning = (master.get(char) or {}).get("meaning", "")
        if new_meaning.strip():
            meta["meaning"] = new_meaning
            fixed.append(char)

    if fixed:
        with open(brain_path, "w", encoding="utf-8") as f:
            json.dump(brain_data, f, ensure_ascii=False, indent=4)
    return fixed


def main():
    with open(MASTER_DICT_PATH, "r", encoding="utf-8") as f:
        master = json.load(f)

    total_fixed = 0
    for brain_path in find_brain_paths():
        fixed = backfill(brain_path, master)
        if fixed:
            print(f"{brain_path}: filled in {', '.join(fixed)}")
            total_fixed += len(fixed)

    print(f"Done. {total_fixed} character meaning(s) backfilled." if total_fixed
          else "Done. Nothing to backfill.")


if __name__ == "__main__":
    main()
