"""
Single-use invite codes gating self-service signup (see server.py's
POST /api/signup and create_invite.py). Kept separate from accounts.py: an
invite code authorizes creating an account, it isn't itself an account.

Signup is invite-gated rather than fully open by explicit decision: the
droplet this runs on is a 1 vCPU / 458MB box with no CAPTCHA or email
verification, so an open signup form is a real bot/memory-exhaustion risk --
every account gets a permanently process-lifetime-cached JuziEngine (see
get_engine_for_id in server.py). A short code the operator hands out
privately closes that off at effectively no cost to a real friend signing up.

users/invite_codes.json, gitignored alongside the rest of users/:
    {code: {created, used_by, used_at}}
"""
import datetime
import json
import os
import secrets

from user_registry import USERS_DIR

INVITE_CODES_PATH = os.path.join(USERS_DIR, "invite_codes.json")


def load_invite_codes():
    if not os.path.exists(INVITE_CODES_PATH):
        return {}
    with open(INVITE_CODES_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


def save_invite_codes(codes):
    os.makedirs(USERS_DIR, exist_ok=True)
    with open(INVITE_CODES_PATH, "w", encoding="utf-8") as f:
        json.dump(codes, f, ensure_ascii=False, indent=4)


def create_invite_code():
    """Mints, records, and returns one new unused code. Used by create_invite.py."""
    codes = load_invite_codes()
    code = secrets.token_urlsafe(6)
    codes[code] = {
        "created": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds"),
        "used_by": None,
        "used_at": None,
    }
    save_invite_codes(codes)
    return code


def redeem_invite_code(codes, code):
    """
    True if `code` exists and is unused. Does not mark it used or save --
    the caller does that only once the account it authorizes actually gets
    created, so a signup that fails for some other reason doesn't burn it.
    """
    entry = codes.get(code)
    return entry is not None and entry.get("used_by") is None


def mark_invite_code_used(codes, code, username):
    codes[code]["used_by"] = username
    codes[code]["used_at"] = datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")
