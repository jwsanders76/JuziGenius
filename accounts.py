"""
Real username/password accounts, layered on top of the existing multi-user
storage convention: users/<id>/brain.json (see get_engine_for_id in
server.py). A real account's user_id is generated exactly like a
create_user.py slug (secrets.token_urlsafe) and used as that same directory
name, so JuziEngine and seed_brain.py need no changes at all to serve a
cookie-authenticated account -- see project_state.md's account-system entry
for why this reuse was possible.

This is a second, independent index alongside users/registry.json (the old
slug -> name lookup for create_user.py accounts). The two systems share the
users/<id>/ storage layout but keep entirely separate account records, per
the deliberate decision to let /u/<slug>/ links keep working unchanged
alongside real login.

users/accounts.json, gitignored (personal data, not app code, same reasoning
as registry.json): keyed by lowercase username ->
    {user_id, password_hash, display_name, session_version, created}
"""
import datetime
import json
import os
import re
import secrets

from auth import hash_password, verify_password
from seed_brain import empty_brain
from user_registry import USERS_DIR

ACCOUNTS_PATH = os.path.join(USERS_DIR, "accounts.json")

# Kept short and log-friendly on purpose -- unlike a slug, a username isn't
# secret and ends up in ordinary server logs, so no punctuation that could
# be confused with a path separator or read as HTML.
USERNAME_RE = re.compile(r"^[A-Za-z0-9_-]{3,20}$")
MIN_PASSWORD_LENGTH = 8

# Every brand new account starts here; set_password bumps it on a reset.
INITIAL_SESSION_VERSION = 1


def load_accounts():
    if not os.path.exists(ACCOUNTS_PATH):
        return {}
    with open(ACCOUNTS_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


def save_accounts(accounts):
    os.makedirs(USERS_DIR, exist_ok=True)
    with open(ACCOUNTS_PATH, "w", encoding="utf-8") as f:
        json.dump(accounts, f, ensure_ascii=False, indent=4)


def find_account_by_user_id(accounts, user_id):
    """
    Session cookies carry a user_id, not a username, and accounts.json is
    keyed by username -- this is the reverse lookup a cookie check needs. A
    linear scan is fine at the account count this app runs at; an index
    would be premature for a handful of friends.
    """
    for username, entry in accounts.items():
        if entry.get("user_id") == user_id:
            return username, entry
    return None, None


def validate_new_account(accounts, username, password):
    """Raises ValueError with a user-facing message if invalid."""
    if not USERNAME_RE.match(username):
        raise ValueError("Username must be 3-20 characters: letters, numbers, _ or - only.")
    if username.lower() in accounts:
        raise ValueError("That username is already taken.")
    if len(password) < MIN_PASSWORD_LENGTH:
        raise ValueError(f"Password must be at least {MIN_PASSWORD_LENGTH} characters.")


def create_account(accounts, username, password):
    """
    Provisions a brand new account: a fresh users/<user_id>/brain.json (the
    same empty_brain() every other account-creation path uses) plus an entry
    in `accounts`. Does not save `accounts` to disk -- the caller does that
    after also marking the invite code used, so both writes land together
    rather than leaving a used-but-unrecorded code if the second somehow
    failed. Returns the new user_id; the account's session_version is always
    INITIAL_SESSION_VERSION.
    """
    user_id = secrets.token_urlsafe(16)
    user_dir = os.path.join(USERS_DIR, user_id)
    os.makedirs(user_dir, exist_ok=False)
    with open(os.path.join(user_dir, "brain.json"), "w", encoding="utf-8") as f:
        json.dump(empty_brain(), f, ensure_ascii=False, indent=4)

    accounts[username.lower()] = {
        "user_id": user_id,
        "password_hash": hash_password(password),
        "display_name": username,
        "session_version": INITIAL_SESSION_VERSION,
        "created": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds"),
    }
    return user_id


# A fixed-format hash with no real password behind it, used only so
# verify_login spends the same PBKDF2 time whether or not the username
# exists -- otherwise a timing difference could be used to enumerate valid
# usernames without ever guessing a password.
_DUMMY_HASH = hash_password(secrets.token_urlsafe(32))


def verify_login(accounts, username, password):
    """Returns (user_id, session_version) on success, else None."""
    entry = accounts.get(username.lower())
    if entry is None:
        verify_password(password, _DUMMY_HASH)
        return None
    if not verify_password(password, entry["password_hash"]):
        return None
    return entry["user_id"], entry["session_version"]


def set_password(accounts, username, new_password):
    """
    Used by set_password.py, the manual password-recovery path. Bumps
    session_version so every cookie issued before the reset stops working --
    otherwise a stolen or leaked old cookie would outlive the password
    change it was supposed to be invalidated by.
    """
    entry = accounts.get(username.lower())
    if entry is None:
        raise KeyError(username)
    entry["password_hash"] = hash_password(new_password)
    entry["session_version"] = entry.get("session_version", INITIAL_SESSION_VERSION) + 1
