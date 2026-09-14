"""
Password hashing and session-cookie signing for the real username/password
account system (see accounts.py for the account store itself, invites.py for
signup gating).

Deliberately stdlib-only -- no bcrypt/passlib dependency, matching the rest
of the project's "no build step, no extra packages" convention. PBKDF2-HMAC
is the standard library's own slow-hash primitive, so nothing here is a
home-grown crypto scheme.

Sessions are stateless, signed cookies rather than a server-side session
store: the droplet restarts on every deploy (see project_state.md's
Deploying entry), and an in-memory session store would log every friend out
on every restart, while a disk-backed one would just be one more file to
keep consistent for no real benefit at this account count. A cookie carries
<user_id>.<expiry>.<session_version>, HMAC-signed under a server secret that
IS persisted across restarts (see _load_or_create_secret). session_version
is checked against the account's current value by the caller (server.py,
via accounts.find_account_by_user_id) at verify time -- bumping it (a
password reset, or a future "log out everywhere") instantly invalidates
every cookie issued before the bump, with no revocation list needed.
"""
import base64
import hashlib
import hmac
import json
import os
import secrets
import threading
import time

SESSION_SECRET_PATH = ".session_secret"
SESSION_COOKIE_NAME = "juzi_session"
SESSION_TTL_SECONDS = 90 * 24 * 60 * 60  # 90 days

PBKDF2_ALGORITHM = "sha256"
PBKDF2_ITERATIONS = 200_000


def _load_or_create_secret():
    """
    The HMAC key session cookies are signed with. Created once with
    os.urandom(32) and persisted to a gitignored file at the repo root so it
    survives a deploy restart -- a secret regenerated on every process start
    would silently log every logged-in friend out on every deploy.
    """
    if os.path.exists(SESSION_SECRET_PATH):
        with open(SESSION_SECRET_PATH, "rb") as f:
            secret = f.read()
        if secret:
            return secret
    secret = os.urandom(32)
    # Restrictive permissions from the moment the file exists: this key lets
    # its holder forge a session for any account.
    fd = os.open(SESSION_SECRET_PATH, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "wb") as f:
        f.write(secret)
    return secret


_SESSION_SECRET = _load_or_create_secret()


def hash_password(password):
    """
    Self-describing hash string: pbkdf2_<algo>$<iterations>$<salt_hex>$<hash_hex>,
    so the scheme (iteration count, algorithm) can change later without
    invalidating passwords hashed under an older version.
    """
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac(PBKDF2_ALGORITHM, password.encode("utf-8"), salt, PBKDF2_ITERATIONS)
    return f"pbkdf2_{PBKDF2_ALGORITHM}${PBKDF2_ITERATIONS}${salt.hex()}${digest.hex()}"


def verify_password(password, stored_hash):
    """Constant-time comparison against a hash_password() string."""
    try:
        algo_part, iterations_str, salt_hex, hash_hex = stored_hash.split("$")
        algorithm = algo_part.split("_", 1)[1]
        iterations = int(iterations_str)
        salt = bytes.fromhex(salt_hex)
        expected = bytes.fromhex(hash_hex)
    except (ValueError, IndexError):
        return False
    computed = hashlib.pbkdf2_hmac(algorithm, password.encode("utf-8"), salt, iterations)
    return hmac.compare_digest(computed, expected)


def make_session_cookie(user_id, session_version, ttl_seconds=SESSION_TTL_SECONDS):
    expiry = int(time.time()) + ttl_seconds
    payload = f"{user_id}.{expiry}.{session_version}"
    signature = hmac.new(_SESSION_SECRET, payload.encode("utf-8"), hashlib.sha256).hexdigest()
    return f"{payload}.{signature}"


def verify_session_cookie(cookie_value):
    """
    Returns (user_id, session_version) if the cookie's signature is valid and
    it hasn't expired, else None. Does NOT check session_version against the
    account's current value -- this module has no access to accounts.json,
    so the caller does that after looking the account up.
    """
    if not cookie_value:
        return None
    # user_id (secrets.token_urlsafe) never contains ".", so a plain split
    # into exactly 4 fields is unambiguous.
    parts = cookie_value.split(".")
    if len(parts) != 4:
        return None
    user_id, expiry_str, version_str, signature = parts
    payload = f"{user_id}.{expiry_str}.{version_str}"
    expected = hmac.new(_SESSION_SECRET, payload.encode("utf-8"), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(signature, expected):
        return None
    try:
        expiry = int(expiry_str)
        session_version = int(version_str)
    except ValueError:
        return None
    if expiry < int(time.time()):
        return None
    return user_id, session_version


# --- Single-purpose signed links (email confirmation, password reset) ---
# The same stateless idea as the session cookie, for the same reasons: nothing
# to store, nothing lost on a restart. Each token names its purpose inside the
# signed payload, so a confirmation link can never be replayed as a reset link
# even though both are signed with the one secret. Revocation comes from what
# the claims are checked against when the link is used, not from a list -- see
# server.py's _handle_password_reset for what a reset link binds to.
EMAIL_VERIFY_TTL_SECONDS = 48 * 60 * 60
PASSWORD_RESET_TTL_SECONDS = 60 * 60


def _action_signature(encoded):
    # "action." keeps these signatures in a different space from session
    # cookies, whose signed payload is "<user_id>.<expiry>.<version>".
    return hmac.new(_SESSION_SECRET, f"action.{encoded}".encode("ascii"),
                    hashlib.sha256).hexdigest()


def make_action_token(purpose, claims, ttl_seconds):
    """
    A URL-safe token carrying `claims` (a small JSON-serializable dict) for one
    `purpose`, valid for `ttl_seconds`. Signed, not encrypted: whoever holds
    the link can read the claims, so they must never contain anything the
    recipient shouldn't see. A user_id and the address the link was mailed to
    are fine.
    """
    body = dict(claims, p=purpose, x=int(time.time()) + ttl_seconds)
    raw = json.dumps(body, separators=(",", ":"), sort_keys=True).encode("utf-8")
    encoded = base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")
    return f"{encoded}.{_action_signature(encoded)}"


def verify_action_token(purpose, token):
    """
    The claims dict if `token` is authentic, unexpired, and was issued for
    `purpose`; otherwise None. Authentic only means this server issued it --
    whether it still applies (the account has since changed its email or
    password) is the caller's check.
    """
    # isascii first: hmac.compare_digest raises on non-ASCII str, and a token
    # arrives straight from a request body.
    if not isinstance(token, str) or not token.isascii() or token.count(".") != 1:
        return None
    encoded, signature = token.split(".")
    if not hmac.compare_digest(signature, _action_signature(encoded)):
        return None
    try:
        body = json.loads(base64.urlsafe_b64decode(encoded + "=" * (-len(encoded) % 4)))
    except ValueError:
        return None
    if not isinstance(body, dict) or body.get("p") != purpose:
        return None
    expiry = body.get("x")
    if not isinstance(expiry, int) or expiry < int(time.time()):
        return None
    return body


# --- Rate limiting for /api/login and /api/signup ---
# In-memory only: it resets on restart, which is fine here -- this is a
# blunt defense against rapid automated attempts, not a durable audit trail.
RATE_LIMIT_WINDOW_SECONDS = 10 * 60
RATE_LIMIT_MAX_ATTEMPTS = 5

_attempts = {}
_attempts_lock = threading.Lock()


def rate_limited(key, max_attempts=RATE_LIMIT_MAX_ATTEMPTS):
    """
    True if `key` (e.g. "login:<ip>" or "signup:<ip>") has recorded
    `max_attempts` or more attempts within the last
    RATE_LIMIT_WINDOW_SECONDS. Checking is separate from record_attempt so a
    caller can decide whether to count an attempt only once it has actually
    happened.
    """
    now = time.time()
    with _attempts_lock:
        timestamps = [t for t in _attempts.get(key, []) if now - t < RATE_LIMIT_WINDOW_SECONDS]
        # Drop a key once its window empties rather than keeping it forever.
        # Keys used to be IPs only; "forgot-email:<address>" lets a client mint
        # a new key per request, and a dict that never shrinks is a slow
        # memory leak on a 458 MB box.
        if timestamps:
            _attempts[key] = timestamps
        else:
            _attempts.pop(key, None)
        return len(timestamps) >= max_attempts


def record_attempt(key):
    with _attempts_lock:
        _attempts.setdefault(key, []).append(time.time())
