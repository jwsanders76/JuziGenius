"""
Prints one friend's full account link again, looked up by the name recorded
in users/registry.json (see create_user.py) -- for when you need to
re-share a link that's since been lost, without resetting anything.

Usage:
    python3 get_user_link.py --name Alice
    python3 get_user_link.py --slug <slug>   # if two accounts share a name
"""
import argparse
import os

from user_registry import USERS_DIR, find_by_name, load_registry

# Every account link points at the one live domain -- see create_user.py's
# BASE_URL for the same constant.
BASE_URL = "https://juzigenius.com"


def main():
    parser = argparse.ArgumentParser(
        description="Print a friend's account link again, by name."
    )
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--name", help="Registered name to look up (from list_users.py).")
    group.add_argument("--slug", help="Look up by exact slug instead, if a name is ambiguous.")
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

    print(f"{BASE_URL}/u/{slug}/")


if __name__ == "__main__":
    main()
