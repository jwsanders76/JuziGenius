"""
Shared read/write helpers for users/registry.json -- the slug -> friend-name
lookup table used by create_user.py, list_users.py, and reset_user.py so a
random /u/<slug>/ token can be tied back to who it was actually given to.

Gitignored along with the rest of users/: this is personal data about who
you've shared accounts with, not app code.
"""
import datetime
import json
import os
from atomic_io import write_json

USERS_DIR = "users"
REGISTRY_PATH = os.path.join(USERS_DIR, "registry.json")


def load_index(path):
    """
    One of the users/ JSON index files, or {} if it doesn't exist yet.
    Shared with accounts.py and invites.py, which keep their own indexes in
    the same directory under the same conventions.
    """
    if not os.path.exists(path):
        return {}
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def save_index(path, data):
    """Writes one of those index files, creating users/ if it's not there."""
    os.makedirs(USERS_DIR, exist_ok=True)
    write_json(path, data)


def load_registry():
    return load_index(REGISTRY_PATH)


def save_registry(registry):
    save_index(REGISTRY_PATH, registry)


def find_by_name(registry, name):
    """
    Slugs whose registered name matches (case-insensitively). A list, not a
    single result, since nothing stops two accounts sharing a name -- e.g. if
    an old one was never cleaned up before a friend was re-invited.
    """
    needle = name.strip().lower()
    return [slug for slug, entry in registry.items()
            if entry.get("name", "").strip().lower() == needle]


def mark_converted(registry, slug, username):
    """
    Records that a link account became a real login (accounts.claim_link_account)
    and that its link no longer works. The entry is kept rather than deleted:
    it is still the only record of whom the operator originally gave the link
    to, which is how list_users.py can say who "alice_t" actually is.
    """
    entry = registry.setdefault(slug, {})
    entry["converted_to"] = username.lower()
    entry["converted_at"] = datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")
