"""
Shared read/write helpers for users/registry.json -- the slug -> friend-name
lookup table used by create_user.py, list_users.py, and reset_user.py so a
random /u/<slug>/ token can be tied back to who it was actually given to.

Gitignored along with the rest of users/: this is personal data about who
you've shared accounts with, not app code.
"""
import json
import os

USERS_DIR = "users"
REGISTRY_PATH = os.path.join(USERS_DIR, "registry.json")


def load_registry():
    if not os.path.exists(REGISTRY_PATH):
        return {}
    with open(REGISTRY_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


def save_registry(registry):
    os.makedirs(USERS_DIR, exist_ok=True)
    with open(REGISTRY_PATH, "w", encoding="utf-8") as f:
        json.dump(registry, f, ensure_ascii=False, indent=4)


def find_by_name(registry, name):
    """
    Slugs whose registered name matches (case-insensitively). A list, not a
    single result, since nothing stops two accounts sharing a name -- e.g. if
    an old one was never cleaned up before a friend was re-invited.
    """
    needle = name.strip().lower()
    return [slug for slug, entry in registry.items()
            if entry.get("name", "").strip().lower() == needle]
