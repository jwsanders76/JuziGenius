"""
Provisions a new friend account for multi-user hosting (server.py's
/u/<slug>/ routing -- see get_engine_for_slug there).

Each account is a directory under users/<slug>/ holding its own isolated
brain.json. It starts with no characters unlocked and "onboarded": false --
the friend picks their own starting tier (see seed_brain.py's TIER_INFO for
the catalog: First Peel, Sun-Ripened, Full Zest, Mandarin Orange) from a
picker shown the first time they open their own link, rather than the
operator choosing a --size for them up front. That picker calls server.py's
POST /api/onboarding/seed, which is what actually calls
seed_brain.build_brain -- this script no longer does.

The slug is a random, unguessable token -- there's no username/password,
so the link itself is the credential. Treat it like one: send it privately,
and only over HTTPS in production (see the deployment notes in
project_state.md).

server.py deliberately never creates users/<slug>/ on its own; this script
is the only way a new account comes into existence.

Since a slug is a random token, "which account is my friend Alice's" isn't
answerable from the slug alone. Every account made through this script is
therefore also recorded in users/registry.json (slug -> name/created date),
gitignored along with the rest of users/ since it's personal data, not app
code. Look accounts up with list_users.py, and reset one by name with
reset_user.py -- both read this same file.

Usage:
    python3 create_user.py --name Alice
    python3 create_user.py --name Bob --base-url https://juzigenius.example.com
"""
import argparse
import datetime
import json
import os
import secrets

from user_registry import USERS_DIR, load_registry, save_registry


def create_user():
    slug = secrets.token_urlsafe(16)
    user_dir = os.path.join(USERS_DIR, slug)
    # Astronomically unlikely to collide, but don't silently reuse an
    # existing directory if it somehow does.
    os.makedirs(user_dir, exist_ok=False)

    brain = {
        "unlocked_chars": {},
        "unlocked_words": {},
        "settings": {"daily_goal": 10, "strict_mode": True},
        "sentences": [],
        "onboarded": False,
    }
    with open(os.path.join(user_dir, "brain.json"), "w", encoding="utf-8") as f:
        json.dump(brain, f, ensure_ascii=False, indent=4)

    return slug


def main():
    parser = argparse.ArgumentParser(
        description="Create a new friend account for multi-user hosting and "
                     "print their private practice link. They'll choose their "
                     "own starting tier the first time they open it."
    )
    parser.add_argument(
        "--name", required=True,
        help="Who this account is for (e.g. a friend's name). Recorded in "
             "users/registry.json so you can tell accounts apart later -- "
             "see list_users.py and reset_user.py.",
    )
    parser.add_argument(
        "--base-url", default="http://localhost:8000",
        help="Scheme+host to print the link against, e.g. https://your-domain.com "
             "(default: http://localhost:8000).",
    )
    args = parser.parse_args()

    os.makedirs(USERS_DIR, exist_ok=True)
    slug = create_user()

    registry = load_registry()
    registry[slug] = {
        "name": args.name,
        "created": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds"),
    }
    save_registry(registry)

    print(f"Created account for {args.name} -- no starting tier chosen yet.")
    print("Private link -- this IS the login, so send it privately and treat it like a password:")
    print(f"  {args.base_url.rstrip('/')}/u/{slug}/")
    print("They'll see a tier picker (First Peel / Sun-Ripened / Full Zest / Mandarin Orange) the first time they open it.")


if __name__ == "__main__":
    main()
