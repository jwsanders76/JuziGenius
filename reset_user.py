"""
Resets one friend's account back to a fresh starting pool, looked up by the
name recorded in users/registry.json (see create_user.py) rather than by
slug, since the slug is a random token nobody would remember.

This discards that account's entire brain.json -- SRS progress, unlocked
words, pasted sentences, everything -- and reseeds it from scratch at
--size, exactly like seed_brain.py --force does for the root install. There
is no undo; the old brain.json is simply overwritten.

Usage:
    python3 reset_user.py --name Alice --size 50
    python3 reset_user.py --slug <slug> --size 50   # if two accounts share a name
"""
import argparse
import json
import os

from seed_brain import MASTER_DICT_PATH, SIZE_CHOICES, TIER_NAMES, build_brain
from user_registry import USERS_DIR, find_by_name, load_registry


def main():
    parser = argparse.ArgumentParser(
        description="Reset a friend's account to a fresh starting pool, by name."
    )
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--name", help="Registered name to reset (from list_users.py).")
    group.add_argument("--slug", help="Reset by exact slug instead, if a name is ambiguous.")
    parser.add_argument(
        "--size", type=int, choices=SIZE_CHOICES, required=True,
        help="Starting character tier to reset to: 5, 50, 300, or 500.",
    )
    args = parser.parse_args()

    registry = load_registry()

    if args.slug:
        if args.slug not in registry:
            parser.error(f"No account with slug {args.slug!r} in users/registry.json.")
        slug = args.slug
    else:
        matches = find_by_name(registry, args.name)
        if not matches:
            parser.error(f"No account named {args.name!r} in users/registry.json. Run list_users.py to see what's registered.")
        if len(matches) > 1:
            parser.error(
                f"{len(matches)} accounts are named {args.name!r} -- re-run with --slug to pick one:\n  "
                + "\n  ".join(matches)
            )
        slug = matches[0]

    user_dir = os.path.join(USERS_DIR, slug)
    if not os.path.isdir(user_dir):
        parser.error(f"users/registry.json points at {user_dir}, but that directory doesn't exist.")

    with open(MASTER_DICT_PATH, "r", encoding="utf-8") as f:
        master = json.load(f)

    brain = build_brain(args.size, master)
    with open(os.path.join(user_dir, "brain.json"), "w", encoding="utf-8") as f:
        json.dump(brain, f, ensure_ascii=False, indent=4)

    tier_name = TIER_NAMES.get(args.size, "")
    name = registry.get(slug, {}).get("name", "(unnamed)")
    print(
        f"Reset {name}'s account ({slug}) to Tier {SIZE_CHOICES.index(args.size) + 1} "
        f"({tier_name}, {args.size} characters). All prior progress on this account was discarded."
    )


if __name__ == "__main__":
    main()
