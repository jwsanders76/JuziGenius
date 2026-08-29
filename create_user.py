"""
Provisions a new friend account for multi-user hosting (server.py's
/u/<slug>/ routing -- see get_engine_for_slug there).

Each account is a directory under users/<slug>/ holding its own isolated
brain.json, seeded with a starting character pool via the same
sentence-coverage logic seed_brain.py uses for a fresh local install (see
that file's module docstring for why raw-frequency seeding doesn't work).
The slug is a random, unguessable token -- there's no username/password,
so the link itself is the credential. Treat it like one: send it privately,
and only over HTTPS in production (see the deployment notes in
project_state.md).

server.py deliberately never creates users/<slug>/ on its own; this script
is the only way a new account comes into existence.

Usage:
    python3 create_user.py                       # new account, Tier 2 (50 chars)
    python3 create_user.py --size 300             # new account, Tier 3
    python3 create_user.py --base-url https://juzigenius.example.com
"""
import argparse
import json
import os
import secrets

from seed_brain import MASTER_DICT_PATH, SIZE_CHOICES, TIER_NAMES, build_brain

USERS_DIR = "users"


def create_user(size, master):
    slug = secrets.token_urlsafe(16)
    user_dir = os.path.join(USERS_DIR, slug)
    # Astronomically unlikely to collide, but don't silently reuse an
    # existing directory if it somehow does.
    os.makedirs(user_dir, exist_ok=False)

    brain = build_brain(size, master)
    with open(os.path.join(user_dir, "brain.json"), "w", encoding="utf-8") as f:
        json.dump(brain, f, ensure_ascii=False, indent=4)

    return slug


def main():
    parser = argparse.ArgumentParser(
        description="Create a new friend account for multi-user hosting and "
                     "print their private practice link."
    )
    parser.add_argument(
        "--size", type=int, choices=SIZE_CHOICES, default=50,
        help="Starting character tier for the new account (default: 50, Elementary).",
    )
    parser.add_argument(
        "--base-url", default="http://localhost:8000",
        help="Scheme+host to print the link against, e.g. https://your-domain.com "
             "(default: http://localhost:8000).",
    )
    args = parser.parse_args()

    with open(MASTER_DICT_PATH, "r", encoding="utf-8") as f:
        master = json.load(f)

    os.makedirs(USERS_DIR, exist_ok=True)
    slug = create_user(args.size, master)
    tier_name = TIER_NAMES.get(args.size, "")
    tier_number = SIZE_CHOICES.index(args.size) + 1

    print(f"Created new account: Tier {tier_number} ({tier_name}, {args.size} characters).")
    print("Private link -- this IS the login, so send it privately and treat it like a password:")
    print(f"  {args.base_url.rstrip('/')}/u/{slug}/")


if __name__ == "__main__":
    main()
