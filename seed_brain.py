"""
Seeds a starter brain.json for a new install. JuziGenius normally builds
brain.json's unlocked_chars entirely from what you unlock via text import or
AI generation, so a brand new install starts with zero unlocked characters
and nothing to practice. This script gives new users a starting pool instead.

master_dictionary.json is already ordered by character frequency (it's built
from hanzi_db.csv, sorted by frequency_rank), so "first N keys" is simply the
N most common characters.

Usage:
    python3 seed_brain.py --size 5|50|300
    python3 seed_brain.py --size 50 --force   # overwrite an existing brain.json
"""
import argparse
import json
import os

MASTER_DICT_PATH = "master_dictionary.json"
BRAIN_PATH = "brain.json"
SIZE_CHOICES = (5, 50, 300)


def build_brain(size):
    with open(MASTER_DICT_PATH, "r", encoding="utf-8") as f:
        master = json.load(f)

    chars = list(master.keys())[:size]
    unlocked_chars = {
        char: {
            "pinyin": master[char]["pinyin"],
            "meaning": master[char]["meaning"],
            "interval": 0,
            "factor": 2.5,
            "reps": 0,
            "last": None,
        }
        for char in chars
    }

    return {
        "unlocked_chars": unlocked_chars,
        "settings": {"daily_goal": 10, "strict_mode": True},
        "sentences": [],
    }


def main():
    parser = argparse.ArgumentParser(description="Seed a starter brain.json for a new install.")
    parser.add_argument(
        "--size", type=int, choices=SIZE_CHOICES, required=True,
        help="How many of the most common characters to start unlocked with.",
    )
    parser.add_argument(
        "--force", action="store_true",
        help="Overwrite an existing brain.json instead of refusing.",
    )
    args = parser.parse_args()

    if os.path.exists(BRAIN_PATH) and not args.force:
        raise SystemExit(
            f"{BRAIN_PATH} already exists -- refusing to overwrite your progress. "
            "Pass --force if you really mean to replace it."
        )

    brain = build_brain(args.size)
    with open(BRAIN_PATH, "w", encoding="utf-8") as f:
        json.dump(brain, f, ensure_ascii=False, indent=4)

    print(f"Seeded {BRAIN_PATH} with the {args.size} most common characters.")


if __name__ == "__main__":
    main()
