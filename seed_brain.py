"""
Seeds a starter brain.json for a new install. JuziGenius normally builds
brain.json's unlocked_chars entirely from what you unlock via text import or
AI generation, so a brand new install starts with zero unlocked characters
and nothing to practice. This script gives new users a starting pool instead.

The pool is chosen so that it can actually be *practiced*. Seeding by raw
character frequency looks reasonable but doesn't work: the 50 most common
characters are almost all function words (的一是不了在人有我他...), and the
"HSK Sentences" practice mode only serves a sentence when every one of its
characters is already unlocked. Measured against the HSK 1-3 corpora, a
frequency-ranked seed of 5 or 50 characters yields *zero* playable
sentences, so a new install following the README landed on an empty session.

Instead this picks characters by sentence coverage: it greedily takes the
HSK sentences that cost the fewest not-yet-unlocked characters, so every
character spent buys as much practice material as possible and the pool
always closes over a set of complete sentences. Any budget left over after
no further sentence fits is filled with the most common characters overall.

Usage:
    python3 seed_brain.py --size 5|50|300
    python3 seed_brain.py --size 50 --force   # overwrite an existing brain.json
"""
import argparse
import csv
import json
import os

from juzi_engine import HSK_SOURCE_FILES

MASTER_DICT_PATH = "master_dictionary.json"
BRAIN_PATH = "brain.json"
SIZE_CHOICES = (5, 50, 300)

# Punctuation the practice modes render as auto-filled slots rather than
# characters to write, so it costs nothing to "unlock". Kept in sync with
# JuziEngine.pick_hsk_sentences().
ALLOWED_PUNCT = "，。！？、；：“”‘’—…"


def load_hsk_sentences(master):
    """
    Every distinct HSK example sentence, as the set of characters it would
    require the user to have unlocked. Sentences containing a character
    missing from master_dictionary.json are dropped -- they could never be
    seeded with pinyin/meaning, so they'd be unplayable anyway.
    """
    sentences = []
    seen = set()
    for filename in HSK_SOURCE_FILES:
        if not os.path.exists(filename):
            continue
        with open(filename, "r", encoding="utf-8") as f:
            for row in csv.DictReader(f, delimiter="\t"):
                chinese = (row.get("sentence") or "").replace(" ", "").strip()
                if not chinese or chinese in seen:
                    continue
                seen.add(chinese)
                needed = {c for c in chinese if c not in ALLOWED_PUNCT}
                if needed and all(c in master for c in needed):
                    sentences.append((chinese, needed))
    return sentences


def select_characters(size, master):
    """
    Greedily grows a character pool that covers as many complete HSK
    sentences as it can within `size` characters, then tops up with the most
    common remaining characters. Returns the pool in frequency order.
    """
    rank = {char: i for i, char in enumerate(master)}
    sentences = load_hsk_sentences(master)
    pool = set()

    while len(pool) < size:
        best = None
        for chinese, needed in sentences:
            new = needed - pool
            if not new or len(pool) + len(new) > size:
                continue
            # Cheapest sentence first; ties go to the shorter sentence, then
            # to the one whose rarest character is most common overall.
            key = (len(new), len(chinese), max(rank[c] for c in needed))
            if best is None or key < best[0]:
                best = (key, new)
        if best is None:
            break
        pool |= best[1]

    # No sentence fits in what's left (or the corpus ran out) -- spend the
    # remainder on the most frequent characters not already unlocked.
    if len(pool) < size:
        for char in master:
            if len(pool) >= size:
                break
            pool.add(char)

    return sorted(pool, key=lambda c: rank[c])


def count_playable(pool, master):
    """How many HSK sentences the seeded pool can actually serve."""
    pool_set = set(pool)
    return sum(1 for _, needed in load_hsk_sentences(master) if needed <= pool_set)


def build_brain(size):
    with open(MASTER_DICT_PATH, "r", encoding="utf-8") as f:
        master = json.load(f)

    chars = select_characters(size, master)
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
        "unlocked_words": {},
        "settings": {"daily_goal": 10, "strict_mode": True},
        "sentences": [],
    }, master


def main():
    parser = argparse.ArgumentParser(description="Seed a starter brain.json for a new install.")
    parser.add_argument(
        "--size", type=int, choices=SIZE_CHOICES, required=True,
        help="How many characters to start unlocked with.",
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

    brain, master = build_brain(args.size)
    with open(BRAIN_PATH, "w", encoding="utf-8") as f:
        json.dump(brain, f, ensure_ascii=False, indent=4)

    playable = count_playable(brain["unlocked_chars"], master)
    print(
        f"Seeded {BRAIN_PATH} with {len(brain['unlocked_chars'])} characters, "
        f"covering {playable} ready-to-practice HSK sentences."
    )


if __name__ == "__main__":
    main()
