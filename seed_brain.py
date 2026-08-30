"""
Seeds a starter brain.json for a new install, and grows it as the user is
ready for more -- a tiered onboarding path from "a few basic characters" up
to "ready to jump in" without ever discarding practice progress. JuziGenius
normally builds brain.json's unlocked_chars entirely from what you unlock via
text import, so a brand new install starts with zero
unlocked characters and nothing to practice. This script gives new users a
starting pool instead, and re-running it with a bigger --size tops that pool
up rather than replacing it.

The pool is chosen so that it can actually be *practiced*. Seeding by raw
character frequency looks reasonable but doesn't work: the 50 most common
characters are almost all function words (的一是不了在人有我他...), and the
"HSK Sentences" practice mode only serves a sentence when every one of its
characters is already unlocked. Measured against the local sentence corpora, a
frequency-ranked seed of 5 or 50 characters yields *zero* playable
sentences, so a new install following the README landed on an empty session.

Instead this picks characters by sentence coverage: it greedily takes the
sentences that cost the fewest not-yet-unlocked characters, so every
character spent buys as much practice material as possible and the pool
always closes over a set of complete sentences. Any budget left over after
no further sentence fits is filled with the most common characters overall.

Four tiers span the full local sentence corpus -- the hand-curated HSK 1-3
sentences plus the larger Tatoeba-derived corpus (see SENTENCE_SOURCE_FILES in
juzi_engine.py): 17,426 sentences, 2,879 distinct characters. Each tier is a
superset of the last:
    Tier 1 -- First Characters  (  5 chars,    6 sentences) -- a first taste
    Tier 2 -- Elementary        ( 50 chars,   93 sentences) -- basic sessions
    Tier 3 -- Intermediate      (300 chars, 1633 sentences) -- real variety
    Tier 4 -- Ready to Jump In  (500 chars, 3358 sentences) -- a large, varied
                                  bank; from here, Paste Text / Suggest Words
                                  carry vocabulary growth forward.

Usage:
    python3 seed_brain.py --size 5              # first run: seeds brain.json
    python3 seed_brain.py --size 50              # tier up: grows it to 50,
                                                  # keeping all SRS progress
    python3 seed_brain.py --size 300             # tier up again
    python3 seed_brain.py --size 500             # tier up to "ready to jump in"
    python3 seed_brain.py --size 50 --force      # discard brain.json and
                                                  # reseed from scratch at 50
"""
import argparse
import csv
import json
import os

from juzi_engine import DEFAULT_DAILY_NEW_LIMIT, SENTENCE_SOURCE_FILES

MASTER_DICT_PATH = "master_dictionary.json"
BRAIN_PATH = "brain.json"
STROKE_DATA_PATH = "stroke_data.json"
SIZE_CHOICES = (5, 50, 300, 500)

# Stand-in stroke count for a character missing from stroke_data.json (a
# handful of rare ones are -- see fetch_stroke_data.py). Set above the
# median of real stroke counts so an unmeasured character reads as
# moderately complex rather than winning ties by default as if it were
# trivially simple.
FALLBACK_STROKE_COUNT = 15

# How common a character has to be (by frequency rank) to be eligible for
# Tier 1's simplicity-first selection. Loose enough to cover everyday
# vocabulary, tight enough that an obscure-but-simple character (plenty of
# rare characters are only 2-3 strokes) can't outrank a common one just for
# being easy to draw.
BEGINNER_RANK_CUTOFF = 1500

# Single source of truth for tier presentation -- read by seed_brain.py's own
# CLI, create_user.py, reset_user.py, and server.py's onboarding endpoint (the
# tier picker a friend sees on their own /u/<slug>/ link the first time they
# open it). Sentence counts are the figures measured against the full
# SENTENCE_SOURCE_FILES corpus (see this module's docstring); they're
# descriptive copy for the picker, not recomputed at runtime.
TIER_INFO = {
    5: {
        "name": "First Peel",
        "sentences": 0,
        # Shown in place of the usual "N sentences" line -- see
        # app.js's renderOnboardingTierButton, which prefers this field
        # when present. Tier 1 has no sentences by design (see
        # select_beginner_characters and JuziEngine's character-only
        # practice threshold), so "0 sentences" would read as broken.
        "meta": "5 characters — character practice",
        "blurb": (
            "Five simple, common characters to get your hand moving -- no "
            "sentences yet. You'll practice characters one at a time until "
            "you've unlocked about 20, then sentence practice opens up "
            "automatically."
        ),
    },
    50: {
        "name": "Sun-Ripened",
        "sentences": 93,
        "blurb": "Basic sessions with enough variety to feel like real practice.",
    },
    300: {
        "name": "Full Zest",
        "sentences": 1633,
        "blurb": "A serious starting vocabulary and real sentence variety.",
    },
    500: {
        "name": "Mandarin Orange",
        "sentences": 3358,
        "blurb": "A large, varied bank -- jump straight into real practice.",
    },
}
TIER_NAMES = {size: info["name"] for size, info in TIER_INFO.items()}

# Punctuation the practice modes render as auto-filled slots rather than
# characters to write, so it costs nothing to "unlock". Kept in sync with
# JuziEngine.pick_hsk_sentences().
ALLOWED_PUNCT = "，。！？、；：“”‘’—…"


def load_hsk_sentences(master):
    """
    Every distinct sentence from SENTENCE_SOURCE_FILES (the hand-curated HSK
    sentences plus the larger Tatoeba-derived corpus), as the set of
    characters it would require the user to have unlocked. Sentences
    containing a character missing from master_dictionary.json are dropped --
    they could never be seeded with pinyin/meaning, so they'd be unplayable
    anyway.
    """
    sentences = []
    seen = set()
    for filename in SENTENCE_SOURCE_FILES:
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


def load_stroke_counts():
    """
    Maps character -> stroke count, from the same vendored stroke_data.json
    that drives offline handwriting (see fetch_stroke_data.py). Used by
    select_beginner_characters as the primary measure of how physically
    complex a character is to write. Missing entries fall back to
    FALLBACK_STROKE_COUNT.
    """
    if not os.path.exists(STROKE_DATA_PATH):
        return {}
    with open(STROKE_DATA_PATH, "r", encoding="utf-8") as f:
        data = json.load(f)
    return {char: len(entry.get("strokes", [])) for char, entry in data.items()}


def select_beginner_characters(size, master, start_pool=None):
    """
    Picks individual characters for the very first tier by simplicity, not
    sentence coverage: lowest stroke count first, among characters common
    enough to be worth learning at all (BEGINNER_RANK_CUTOFF by frequency,
    so a rare-but-simple character like 乜 doesn't outrank a common one).

    This is deliberately unconnected to whether any sentence becomes
    playable -- Tier 1 is a character-only practice phase (see
    JuziEngine.pick_hsk_sentences' character-only threshold), so unlike
    select_characters there is nothing to "cover" yet. That's what let the
    previous sentence-coverage approach hand a first-time user something
    like 真 (10 strokes) or 想 (13): both are common enough and useful for
    completing a cheap sentence, but neither is simple to write, and
    simplicity is the only thing that matters until sentence practice
    starts.

    `start_pool`, if given, is kept and topped up rather than replaced --
    same contract as select_characters, so a returning user's characters
    are never removed.
    """
    rank = {char: meta.get("freq") or (i + 1)
            for i, (char, meta) in enumerate(master.items())}
    strokes = load_stroke_counts()
    pool = set(start_pool) if start_pool else set()

    candidates = [
        char for char, meta in master.items()
        if char not in pool and meta.get("pinyin") and meta.get("meaning")
        and rank[char] <= BEGINNER_RANK_CUTOFF
    ]
    candidates.sort(key=lambda c: (strokes.get(c, FALLBACK_STROKE_COUNT), rank[c]))

    for char in candidates:
        if len(pool) >= size:
            break
        pool.add(char)

    return pool


def select_characters(size, master, start_pool=None):
    """
    Greedily grows a character pool that covers as many complete HSK
    sentences as it can within `size` characters, then tops up with the most
    common remaining characters. Returns the pool in frequency order.

    `start_pool`, if given, seeds the pool with characters already unlocked
    (from an existing brain.json) before the greedy growth begins -- this is
    what makes tiering up additive: the algorithm only ever adds characters
    on top of what's already there, it never removes any, so growing from
    tier to tier can't cost the user practice progress on characters they
    already have. Used for every tier except the first -- see
    select_beginner_characters for Tier 1's simplicity-first approach.
    """
    # Read the frequency rank explicitly rather than inferring it from the
    # order master_dictionary.json happens to be serialised in. The old
    # `enumerate(master)` was correct only while that file preserved
    # hanzi_db.csv's frequency ordering; re-serialising it sorted would have
    # silently turned every "most frequent" decision here into a codepoint
    # ordering with no error to notice. `freq` is written by init_vocab_db.py;
    # the fallback keeps this working against an older dictionary file.
    rank = {char: meta.get("freq") or (i + 1)
            for i, (char, meta) in enumerate(master.items())}
    sentences = load_hsk_sentences(master)
    pool = set(start_pool) if start_pool else set()

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
        for char in sorted(master, key=lambda c: rank[c]):
            if len(pool) >= size:
                break
            pool.add(char)

    return sorted(pool, key=lambda c: rank[c])


def count_playable(pool, master):
    """How many HSK sentences the seeded pool can actually serve."""
    pool_set = set(pool)
    return sum(1 for _, needed in load_hsk_sentences(master) if needed <= pool_set)


def _fresh_char_entry(char, master):
    return {
        "pinyin": master[char]["pinyin"],
        "meaning": master[char]["meaning"],
        "interval": 0,
        "factor": 2.5,
        "reps": 0,
        "last": None,
    }


def empty_brain():
    """
    A brand new account with nothing unlocked and no tier chosen yet -- the
    state create_user.py provisions, and the state server.py's
    POST /api/account/reset returns an account to when its owner asks to
    start over.

    `onboarded: false` is what makes app.js show the tier picker instead of
    the normal session (see fetchNewSession), so this is not merely an empty
    brain but specifically a *pre-onboarding* one. Note what is absent as
    much as what is here: no completed_sentences and no pasted_sentences, so
    a reset drops the user's own saved sentences along with their SRS
    schedule. That is the point of starting over, but it is the one part
    that cannot be re-derived from anything, so anything calling this must
    say so plainly first.

    Defined here, beside build_brain, so the two agree on the schema --
    create_user.py used to carry its own copy of this dict.
    """
    return {
        "unlocked_chars": {},
        "unlocked_words": {},
        # daily_new_limit is written explicitly rather than left to
        # DEFAULT_DAILY_NEW_LIMIT so the value the Settings panel edits is
        # visible in brain.json instead of being an invisible default. The
        # long-dormant daily_goal and strict_mode keys are gone: both were
        # written here and read by nothing, and a setting that does nothing
        # is worse than no setting -- update_settings drops them from
        # existing accounts on the next save.
        "settings": {"daily_new_limit": DEFAULT_DAILY_NEW_LIMIT},
        "sentences": [],
        "onboarded": False,
    }


def build_brain(size, master, onboarded=True):
    """
    `onboarded=False` marks the brain as still awaiting its owner's own tier
    choice -- used by create_user.py, which no longer picks a size on a
    friend's behalf (see server.py's /api/onboarding/seed, which calls this
    same function once they pick one from the tier picker on their first
    visit). Every other caller (this module's own CLI, reset_user.py) wants
    the default: a brain that's immediately playable, no picker shown.
    """
    chars = (select_beginner_characters(size, master) if size == SIZE_CHOICES[0]
              else select_characters(size, master))

    brain = empty_brain()
    brain["unlocked_chars"] = {char: _fresh_char_entry(char, master) for char in chars}
    brain["onboarded"] = onboarded
    return brain


def grow_brain(size, brain_data, master):
    """
    Tiers an existing brain.json up to `size` characters in place. Only ever
    adds -- every existing entry in unlocked_chars (and its SRS progress:
    interval/factor/reps/last) is left untouched, along with unlocked_words,
    sentences, and settings. Returns the list of newly added characters, or
    None if the brain is already at or past this tier (nothing to do).
    """
    unlocked_chars = brain_data.setdefault("unlocked_chars", {})
    start_pool = set(unlocked_chars.keys())
    if len(start_pool) >= size:
        return None

    full_pool = select_characters(size, master, start_pool=start_pool)
    added = [char for char in full_pool if char not in unlocked_chars]
    for char in added:
        unlocked_chars[char] = _fresh_char_entry(char, master)
    return added


def main():
    parser = argparse.ArgumentParser(
        description=(
            "Seed a starter brain.json, or tier an existing one up. Re-run with a "
            "bigger --size any time to grow your pool -- it only ever adds "
            "characters, so existing practice progress is never touched."
        )
    )
    parser.add_argument(
        "--size", type=int, choices=SIZE_CHOICES, required=True,
        help="Target tier size: 5 (First Characters), 50 (Elementary), "
             "300 (Intermediate), or 500 (Ready to Jump In).",
    )
    parser.add_argument(
        "--force", action="store_true",
        help="Discard the existing brain.json entirely and reseed from scratch "
             "at --size, instead of tiering it up in place.",
    )
    args = parser.parse_args()

    with open(MASTER_DICT_PATH, "r", encoding="utf-8") as f:
        master = json.load(f)

    tier_name = TIER_NAMES.get(args.size, "")
    exists = os.path.exists(BRAIN_PATH)

    if not exists or args.force:
        brain = build_brain(args.size, master)
        with open(BRAIN_PATH, "w", encoding="utf-8") as f:
            json.dump(brain, f, ensure_ascii=False, indent=4)
        playable = count_playable(brain["unlocked_chars"], master)
        verb = "Reseeded" if exists else "Seeded"
        print(
            f"{verb} {BRAIN_PATH} at Tier {SIZE_CHOICES.index(args.size) + 1} "
            f"({tier_name}) with {len(brain['unlocked_chars'])} characters, "
            f"covering {playable} ready-to-practice HSK sentences."
        )
        return

    with open(BRAIN_PATH, "r", encoding="utf-8") as f:
        brain_data = json.load(f)

    before = len(brain_data.get("unlocked_chars", {}))
    added = grow_brain(args.size, brain_data, master)

    if added is None:
        print(
            f"{BRAIN_PATH} already has {before} characters unlocked, at or past "
            f"Tier {SIZE_CHOICES.index(args.size) + 1} ({tier_name}, {args.size} "
            "characters). Pick a higher --size to tier up further."
        )
        return

    with open(BRAIN_PATH, "w", encoding="utf-8") as f:
        json.dump(brain_data, f, ensure_ascii=False, indent=4)

    after = len(brain_data["unlocked_chars"])
    playable = count_playable(brain_data["unlocked_chars"], master)
    print(
        f"Tiered up {BRAIN_PATH} to Tier {SIZE_CHOICES.index(args.size) + 1} "
        f"({tier_name}): {before} -> {after} characters (+{len(added)} new), "
        f"covering {playable} ready-to-practice HSK sentences. "
        "Progress on previously-unlocked characters was preserved."
    )


if __name__ == "__main__":
    main()
