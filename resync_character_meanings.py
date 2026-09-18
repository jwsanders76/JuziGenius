"""
Brings stored character meanings in line with the everyday-meanings rewrite.

unlocked_chars[char]["meaning"] (brain.json) is copied from the master
dictionary once, when a character is unlocked, and never read from it again.
After the everyday-meanings rewrite (meaning_overrides.json, init_vocab_db.py)
anyone who unlocked a character earlier still holds its old text. Meanings are
never edited by a user, so updating them is a pure repair.

Deliberately narrower than "copy the master dictionary": a stored meaning can
also have come from a better-curated source (the HSK word lists), and copying
the raw dictionary over it would make it worse. So a character with a
hand-written entry in meaning_overrides.json gets that entry, and any other
stored meaning only has its unwanted senses (surname, radical, classical...)
stripped, never replaced. backfill_character_meanings.py fills blank ones.

Does nothing unless run with --apply: the default is a dry run that lists what
would change. A brain.json is only rewritten if something in it differs, and
each rewrite goes through save_brain with a forced history snapshot first, so
brain-history holds the version from before (restore_brain.py undoes it).

Usage:
    python3 resync_character_meanings.py            # show what would change
    python3 resync_character_meanings.py --apply    # do it
"""
import argparse
import json

from backfill_character_meanings import find_brain_paths
from brain_history import save_brain
from init_vocab_db import clean_meaning, load_overrides


def stale_characters(brain_data, overrides):
    unlocked = brain_data.get("unlocked_chars") or {}
    stale = {}
    for char, meta in unlocked.items():
        old_meaning = (meta.get("meaning") or "").strip()
        new_meaning = overrides.get(char) or (clean_meaning(old_meaning) if old_meaning else "")
        if new_meaning and new_meaning != old_meaning:
            stale[char] = (old_meaning, new_meaning)
    return stale


def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--apply", action="store_true",
                        help="write the changes (default: dry run)")
    args = parser.parse_args()

    overrides = load_overrides()

    total = 0
    for brain_path in find_brain_paths():
        with open(brain_path, "r", encoding="utf-8") as f:
            brain_data = json.load(f)
        stale = stale_characters(brain_data, overrides)
        if not stale:
            continue
        for char, (old, new) in stale.items():
            print(f"{brain_path}: {char}  {old!r} -> {new!r}")
            brain_data["unlocked_chars"][char]["meaning"] = new
        if args.apply:
            save_brain(brain_path, brain_data, force_snapshot=True)
        total += len(stale)

    verb = "updated" if args.apply else "would update"
    print(f"Done. {total} character meaning(s) {verb}." if total
          else "Done. Nothing to update.")
    if total and not args.apply:
        print("Dry run only. Re-run with --apply to write.")


if __name__ == "__main__":
    main()
