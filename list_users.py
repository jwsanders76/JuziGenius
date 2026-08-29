"""
Lists every account created by create_user.py, so a random /u/<slug>/ link
can be tied back to who it belongs to -- reads users/registry.json plus each
account's own brain.json for a quick sense of where they've gotten to.

Usage:
    python3 list_users.py
"""
import json
import os

from user_registry import USERS_DIR, load_registry

# Every account link points at the one live domain -- see create_user.py's
# BASE_URL for the same constant.
BASE_URL = "https://juzigenius.com"


def main():
    registry = load_registry()
    if not registry:
        print("No accounts recorded in users/registry.json yet -- create one with create_user.py.")
        return

    rows = sorted(registry.items(), key=lambda item: item[1].get("created", ""))
    for slug, entry in rows:
        brain_path = os.path.join(USERS_DIR, slug, "brain.json")
        status = "? (brain.json missing)"
        if os.path.exists(brain_path):
            with open(brain_path, "r", encoding="utf-8") as f:
                brain = json.load(f)
            chars = len(brain.get("unlocked_chars", {}))
            # onboarded defaults True for brains predating the tier picker
            # (create_user.py, seed_brain.py's build_brain) -- only an
            # explicit False means the friend hasn't chosen a starting tier
            # yet, i.e. they haven't opened their link for the first time.
            if not brain.get("onboarded", True):
                status = "awaiting their first visit (no tier chosen yet)"
            else:
                status = f"{chars} characters unlocked"

        print(f"{entry.get('name', '(unnamed)')}")
        print(f"  link:    {BASE_URL}/u/{slug}/")
        print(f"  slug:    {slug}")
        print(f"  created: {entry.get('created', '?')}")
        print(f"  status:  {status}")
        print()


if __name__ == "__main__":
    main()
