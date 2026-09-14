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
    {user_id, password_hash, display_name, session_version, created,
     email?, email_verified_at?, pending_email?}

`email` only ever holds an address its owner has proven they receive mail at
(see confirm_email). Anything typed in but not yet confirmed lives in
`pending_email`, and nothing treats it as belonging to the account.
"""
import datetime
import os
import re
import secrets

from auth import hash_password, verify_password
from seed_brain import empty_brain
from user_registry import USERS_DIR, load_index, save_index
from atomic_io import write_json

ACCOUNTS_PATH = os.path.join(USERS_DIR, "accounts.json")

# Kept short and log-friendly on purpose -- unlike a slug, a username isn't
# secret and ends up in ordinary server logs, so no punctuation that could
# be confused with a path separator or read as HTML.
USERNAME_RE = re.compile(r"^[A-Za-z0-9_-]{3,20}$")
MIN_PASSWORD_LENGTH = 8

# Every brand new account starts here; set_password bumps it on a reset.
INITIAL_SESSION_VERSION = 1


def load_accounts():
    return load_index(ACCOUNTS_PATH)


def save_accounts(accounts):
    save_index(ACCOUNTS_PATH, accounts)


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
    validate_password(password)


def validate_password(password):
    """Raises ValueError with a user-facing message if `password` is too weak."""
    if len(password) < MIN_PASSWORD_LENGTH:
        raise ValueError(f"Password must be at least {MIN_PASSWORD_LENGTH} characters.")


# Deliberately loose. The only real test of an address is whether mail sent to
# it arrives, which confirmation already checks; a strict pattern here rejects
# valid addresses (plus-tags, new TLDs) while catching nothing that
# confirmation wouldn't.
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
MAX_EMAIL_LENGTH = 254


def normalize_email(email):
    """
    Trimmed and lowercased. Strictly, the part before the @ may be
    case-sensitive, but no mainstream provider treats it that way, and
    comparing case-insensitively is what stops Alice@x.com and alice@x.com
    from counting as two different people.
    """
    return (email or "").strip().lower() if isinstance(email, str) else ""


def validate_email(email):
    """Raises ValueError with a user-facing message if a normalized `email` is unusable."""
    if not email:
        raise ValueError("Enter an email address.")
    if len(email) > MAX_EMAIL_LENGTH or not EMAIL_RE.match(email):
        raise ValueError("That doesn't look like an email address.")


def find_account_by_email(accounts, email):
    """
    (username, entry) for the account whose CONFIRMED email is `email`, else
    (None, None). Pending addresses deliberately don't count: anyone can type
    anyone's address into their own settings, and an unproven claim must never
    receive a password reset or lock the real owner out of their own address.
    """
    if not email:
        return None, None
    for username, entry in accounts.items():
        if entry.get("email") == email:
            return username, entry
    return None, None


def request_email_change(entry, email):
    """
    Records `email` as awaiting confirmation on one account entry. A confirmed
    address already on the account stays in force until the new one is
    confirmed -- a typo in the new address must not cost someone the ability
    to reset their password. Returns True if a confirmation link needs
    sending, False if `email` already is the confirmed address (in which case
    any pending change is simply abandoned).
    """
    if entry.get("email") == email:
        entry.pop("pending_email", None)
        return False
    entry["pending_email"] = email
    return True


def confirm_email(accounts, user_id, email):
    """
    Applies an opened confirmation link. Returns one of:
        "verified"          `email` is now this account's address
        "already_verified"  it already was -- the same link opened twice
        "stale"             the account has since asked to confirm another address
        "taken"             a different account confirmed this address first
        "no_account"
    Only "verified" modifies `accounts`; the caller saves.
    """
    _username, entry = find_account_by_user_id(accounts, user_id)
    if entry is None or not email:
        return "no_account"
    if entry.get("email") == email:
        return "already_verified"
    if entry.get("pending_email") != email:
        return "stale"
    if find_account_by_email(accounts, email)[1] is not None:
        return "taken"
    entry["email"] = email
    entry["email_verified_at"] = datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")
    entry.pop("pending_email", None)
    return "verified"


def public_view(entry):
    """What an account's own owner is shown about it (GET /api/account). Never the hash."""
    return {
        "username": entry.get("display_name"),
        "email": entry.get("email"),
        "pending_email": entry.get("pending_email"),
    }


def create_account(accounts, username, password, email=None):
    """
    Provisions a brand new account: a fresh users/<user_id>/brain.json (the
    same empty_brain() every other account-creation path uses) plus an entry
    in `accounts`. Does not save `accounts` to disk -- the caller does that
    after also marking the invite code used, so both writes land together
    rather than leaving a used-but-unrecorded code if the second somehow
    failed. Returns the new user_id; the account's session_version is always
    INITIAL_SESSION_VERSION.

    `email`, if given, is recorded as pending, never as confirmed -- see
    confirm_email. Sending the confirmation link is the caller's job too.
    """
    user_id = secrets.token_urlsafe(16)
    user_dir = os.path.join(USERS_DIR, user_id)
    os.makedirs(user_dir, exist_ok=False)
    write_json(os.path.join(user_dir, "brain.json"), empty_brain())
    _add_entry(accounts, user_id, username, password, email)
    return user_id


def claim_link_account(accounts, slug, username, password, email):
    """
    Turns an existing /u/<slug>/ link account (create_user.py) into a real
    login account, keeping all of its history. A slug is generated exactly
    like a user_id and is already the name of the account's users/<slug>/
    directory, so it simply becomes the new account's user_id: nothing moves,
    nothing is copied, and JuziEngine never notices the difference.

    Validation is the caller's job, as with create_account, and so is saving
    `accounts`. Raises ValueError if the slug has already been claimed -- the
    caller checks first, so this only guards two claims racing each other.
    """
    if find_account_by_user_id(accounts, slug)[1] is not None:
        raise ValueError("This link has already been turned into a login.")
    _add_entry(accounts, slug, username, password, email)


def _add_entry(accounts, user_id, username, password, email):
    """The accounts.json record both account-creation paths write."""
    accounts[username.lower()] = {
        "user_id": user_id,
        "password_hash": hash_password(password),
        "display_name": username,
        "session_version": INITIAL_SESSION_VERSION,
        "created": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds"),
    }
    if email:
        accounts[username.lower()]["pending_email"] = email


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
