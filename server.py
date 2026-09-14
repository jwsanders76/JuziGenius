import datetime
import hashlib
import http.cookies
import http.server
import json
import os
import re
import shutil
import threading
import urllib.parse
import accounts
import auth
import invites
import mailer
import user_registry
from juzi_engine import JuziEngine
from seed_brain import SIZE_CHOICES, TIER_INFO, empty_brain
from seed_brain import build_brain as seed_build_brain
from brain_history import save_brain

PORT = 8000

# Matches the /u/<slug>/... prefix used to route a request to one friend's
# own isolated brain.json instead of the shared default one (see
# get_engine_for_id below). Anchored and length-floored so a malformed or
# short guess never reaches the filesystem as a path component.
USER_PREFIX_RE = re.compile(r"^/u/([A-Za-z0-9_-]{16,})(/.*)?$")

# The same prefix, unanchored, for redacting slugs out of log lines -- those
# carry a whole request line ("GET /u/<slug>/api/session HTTP/1.1"), not a bare
# path, so the anchored pattern above cannot match inside one. See
# JuziAPIHandler.log_message.
USER_SLUG_IN_LOG_RE = re.compile(r"/u/[A-Za-z0-9_-]{16,}")

# No POST body may legitimately need more than this -- even a whole novel
# chapter pasted into Paste Text is a few hundred KB. Without a cap, do_POST
# reads exactly whatever Content-Length the client claims with no upper
# bound: a client can declare and send an arbitrarily large body (tested:
# an 84MB payload was accepted and fully processed) and the server buffers
# the whole thing in memory -- multiple times over, across the raw bytes,
# the decoded string, and the parsed JSON -- for no legitimate reason. This
# rejects oversized requests before reading the body at all.
MAX_BODY_SIZE = 2 * 1024 * 1024  # 2 MB

# POST /api/account/reset erases an account irreversibly, so it requires this
# word in its body: arriving at the right URL is not enough, the request has
# to state what it intends. app.js asks the user to type the same word, so
# the string is deliberately short, unambiguous and language-neutral.
RESET_CONFIRMATION = "RESET"

# Only these paths may ever be served as static files. This is a network-facing
# server (bound to all interfaces so it's reachable from a tablet on the same
# LAN), and SimpleHTTPRequestHandler's default behavior serves ANY file under
# the working directory by path -- which would expose brain.json (personal
# SRS data) and other source files to anyone on the network. Everything not
# explicitly listed here gets a 404.
ALLOWED_STATIC_PATHS = {
    "/", "/index.html", "/style.css", "/app.js",
    "/avatar-nobg-128.png", "/avatar-nobg.png",
    "/vendor/hanzi-writer.min.js",
    # Installable-app assets. sw.js must be served from the root for its scope
    # to cover /u/<slug>/ pages as well as the bare site.
    "/sw.js", "/icon-192.png", "/icon-512.png", "/icon-maskable-512.png",
    # The real-login page's own script. login.html itself isn't listed here --
    # it's reached only via the /login route (see do_GET), the same pattern
    # index.html and landing.html already use.
    "/auth.js",
    # Attribution for the bundled third-party data (see
    # THIRD-PARTY-LICENSES.md). Served to everyone, logged in or not: the
    # Tatoeba sentences are CC BY and the stroke data is under the Arphic
    # Public License, and both require their notices to reach the people
    # actually using the work -- which a page behind a login does not do.
    "/credits.html", "/ARPHICPL.TXT",
}

# --- Health check for an external uptime monitor (GET/HEAD /healthz) ---
# A monitor on the far side of the internet only sees HTTP status codes, so
# anything worth an alert has to surface here as a non-200. The process not
# answering at all needs nothing extra -- Caddy returns 502 on its behalf --
# and the checks below cover the failures an outside probe can't see.
HEALTH_DISK_LIMIT_PERCENT = 80
# The nightly backup's log, written by ~/bin/juzi-backup.sh on the droplet --
# infrastructure that lives outside this repository (see project_state.md's
# Backups section). Unset, as in local development, skips the check.
BACKUP_LOG_PATH = os.environ.get("JUZI_BACKUP_LOG", "")
# The backup runs nightly, so a healthy log is never much more than 24 hours
# old. 30 leaves room for a slow run without raising an alert over it.
BACKUP_MAX_AGE_HOURS = 30

STROKE_DATA_PATH = "stroke_data.json"
STROKE_INDEX_PATH = "stroke_data.index.json"

# Sentence audio is spoken by the browser's own Web Speech API (see
# playBrowserTTS in app.js). There was a pre-generated path here -- 699 MB of
# MP3s synthesized by build_speech_audio.py and served from /api/speech -- and
# it was removed, along with this server's ability to serve it, because the
# Piper voice models it came from trace to a dataset whose licence the
# upstream model card records as "Unknown". That is not permission to
# distribute the output, and this app is heading for a paid tier. See
# THIRD-PARTY-LICENSES.md. If a properly-licensed voice is adopted later, the
# removed code is in the history of this file.


def build_manifest(start_url="/"):
    """
    The web app manifest, generated per request rather than served as a static
    file, because `start_url` and `scope` have to differ per account: a friend
    who installs from /u/<slug>/ must get an app that opens on THEIR practice
    session, not on the default account's. A static manifest can only name one
    start URL, so installing from a slug would have silently produced an icon
    that opens somebody else's data.

    `id` is pinned to the start URL for the same reason -- browsers key an
    installed app by id, so two accounts installed on one device must not
    collide into a single entry.
    """
    return {
        "id": start_url,
        "name": "JuziGenius \u53e5\u5b50Genius",
        "short_name": "JuziGenius",
        "description": "Hardcore Mandarin handwriting practice with spaced "
                       "repetition. Fully offline: no AI, no accounts, no keys.",
        "start_url": start_url,
        "scope": start_url,
        "display": "standalone",
        "orientation": "portrait",
        "background_color": "#121214",
        "theme_color": "#121214",
        "categories": ["education"],
        "lang": "en",
        "icons": [
            {"src": "/icon-192.png", "sizes": "192x192", "type": "image/png",
             "purpose": "any"},
            {"src": "/icon-512.png", "sizes": "512x512", "type": "image/png",
             "purpose": "any"},
            {"src": "/icon-maskable-512.png", "sizes": "512x512",
             "type": "image/png", "purpose": "maskable"},
        ],
    }

# JuziGenius is a hosted service. The bare domain is a landing page; practice
# happens behind a /u/<slug>/ link or a login session, against that account's
# own brain.json. There is deliberately no single-user mode reachable over
# HTTP: an apex URL serving a default account straight from the top-level
# brain.json means anyone who finds the domain can read and write whoever's
# data happens to be there.
#
# On by default, and the escape hatch is deliberately awkward to reach by
# accident: JUZI_ALLOW_DEFAULT_ACCOUNT=1 restores the old behaviour for local
# development against the root brain.json. Do not set it on a public host.
REQUIRE_SLUG = os.environ.get("JUZI_ALLOW_DEFAULT_ACCOUNT", "") != "1"

# Whether the real-login session cookie gets the Secure attribute (browser
# refuses to ever send it over plain http). On by default -- this process
# normally sits behind Caddy, which terminates real HTTPS in front of it
# (see the Caddyfile), so the cookie should never travel in the clear.
# Deliberately its own flag rather than reusing REQUIRE_SLUG: testing the
# real hosted-mode routing (REQUIRE_SLUG true) against a local plain-http
# server needs Secure off, so tying the two together would make that
# combination impossible to test at all. Set JUZI_COOKIE_SECURE=0 for that
# case; never set it on a public host.
COOKIE_SECURE = os.environ.get("JUZI_COOKIE_SECURE", "1") != "0"

# The origin every emailed link points at. Configured, and deliberately never
# built from the request's Host header: a forgot-password request sent with a
# forged Host would otherwise mail the real owner a genuine reset token inside
# a link to the attacker's domain -- the classic password-reset poisoning bug.
# Set JUZI_PUBLIC_URL=http://127.0.0.1:8000 for local development.
PUBLIC_URL = os.environ.get("JUZI_PUBLIC_URL", "https://juzigenius.com").rstrip("/")

# The engine for a request with no /u/<slug>/ prefix. Unreachable over HTTP
# unless JUZI_ALLOW_DEFAULT_ACCOUNT=1 (see REQUIRE_SLUG above); it exists so
# local development, seed_brain.py and create_user.py still have something to
# operate on.
default_engine = JuziEngine()

# Per-friend engines, one per provisioned /u/<slug>/ account, cached across
# requests so each friend's brain.json is only opened/parsed once per
# process rather than on every call. Guarded by engines_lock: server.py runs
# on ThreadingHTTPServer, so two requests for the same brand-new slug could
# otherwise race past the "not yet cached" check together and each construct
# its own JuziEngine -- two separate brain_lock RLocks guarding the same
# on-disk file, which reopens exactly the lost-update race brain_lock exists
# to close.
USERS_DIR = "users"
engines = {}
engines_lock = threading.Lock()

# Guards users/accounts.json and users/invite_codes.json (plus
# users/registry.json when a link account is claimed) across concurrent
# requests -- signup reads and writes both files together (redeem a code,
# create an account), and this makes that pair atomic with respect to two
# simultaneous signups, the same reason engines_lock exists for brand-new
# slugs. Login and session-cookie checks only read, but take the same lock
# so they can never observe a signup's writes half-applied.
ACCOUNTS_LOCK = threading.Lock()


def _last_backup_age_hours(log_path):
    """
    Hours since the backup log last recorded success. None if the log can't
    be read, has never recorded a success, or its most recent outcome was a
    failure -- a failed run should raise an alert that night, not a day later
    once the previous success finally ages out.
    """
    try:
        with open(log_path, "rb") as f:
            f.seek(0, os.SEEK_END)
            # Only the tail matters, and the log grows every night with
            # restic's own output. 64 KB holds several nights of it.
            f.seek(max(0, f.tell() - 65536))
            tail = f.read().decode("utf-8", "replace")
    except OSError:
        return None
    # Status lines are "<date -Is>  <message>"; restic's interleaved output
    # never starts with either message, so it falls through harmlessly.
    for line in reversed(tail.splitlines()):
        stamp, _, message = line.partition("  ")
        if message.startswith("FAILED:"):
            return None
        if message.startswith("backup OK"):
            try:
                finished = datetime.datetime.fromisoformat(stamp)
            except ValueError:
                return None
            now = datetime.datetime.now(datetime.timezone.utc)
            return (now - finished).total_seconds() / 3600
    return None


def health_problems():
    """
    What is wrong right now, as a list of short words; empty means healthy.
    /healthz is unauthenticated, so these name a category and never a number
    or a path.
    """
    problems = []
    # used / (used + free) is what `df` reports as Use%, so the threshold
    # means the same thing here as on the command line.
    usage = shutil.disk_usage(USERS_DIR)
    if usage.used * 100 >= (usage.used + usage.free) * HEALTH_DISK_LIMIT_PERCENT:
        problems.append("disk")
    try:
        with ACCOUNTS_LOCK:
            accounts.load_accounts()
    except Exception:
        problems.append("accounts")
    if BACKUP_LOG_PATH:
        age = _last_backup_age_hours(BACKUP_LOG_PATH)
        if age is None or age > BACKUP_MAX_AGE_HOURS:
            problems.append("backup")
    return problems


def get_engine_for_id(account_id):
    """
    Resolves either a /u/<slug>/ token or a real-login user_id to that
    account's own isolated JuziEngine (users/<account_id>/brain.json),
    caching instances across requests. Both kinds of id are generated the
    same way (secrets.token_urlsafe) and used as the same users/<id>/
    directory name -- see create_user.py and accounts.create_account -- so
    one cache and one lookup serve both account systems.

    Deliberately does NOT create users/<account_id>/ on demand -- only
    create_user.py (old slug accounts) and accounts.create_account (new
    login accounts) provision directories. An id that merely matches the
    format but was never provisioned returns None (the caller 404s), same
    as any other guess.
    """
    with engines_lock:
        if account_id in engines:
            return engines[account_id]
        user_dir = os.path.join(USERS_DIR, account_id)
        if not os.path.isdir(user_dir):
            return None
        engine = JuziEngine(brain_path=os.path.join(user_dir, "brain.json"))
        engines[account_id] = engine
        return engine

# Vendored Hanzi Writer stroke data. This is what makes handwriting work
# offline: without it the library fetches every character from
# cdn.jsdelivr.net as the user is asked to write it. Tracked in the repo
# (built by fetch_stroke_data.py), so a fresh clone has it. If it's ever
# missing, /api/strokes 404s and app.js falls back to the CDN rather than
# failing outright.
#
# The file is 29.4 MB, and parsing it into one dict cost 137 MB resident held
# for the process lifetime -- to serve what are only ever single-key lookups.
# fetch_stroke_data.py writes a byte-offset index beside it, so a character's
# stroke data is read as one ~3 KB span and the rest is never materialised.
# The span is already JSON, so it goes to the socket verbatim: no parse on
# the way in, no re-encode on the way out.
#
# The index carries the size and sha256 of the file it describes. A stale
# index would serve one character's strokes under another character's name --
# silent, and miserable to diagnose from the symptom -- so a mismatch falls
# back to the old full parse rather than being trusted.
_stroke_index = None            # {char: [offset, length]}, or {} if unusable
_stroke_data = None             # full parse; populated only on the fallback path
_stroke_lock = threading.Lock()


def _file_digest(path, chunk_size=1 << 20):
    """sha256 of a file, read in chunks so a 29 MB file costs 1 MB of memory."""
    digest = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(chunk_size), b""):
            digest.update(block)
    return digest.hexdigest()


def _load_stroke_index():
    """
    The byte-offset index, or {} if it is absent or does not match the data
    file (in which case the caller falls back to parsing the whole thing).
    """
    if not os.path.exists(STROKE_INDEX_PATH):
        print(f"Notice: {STROKE_INDEX_PATH} not found -- falling back to parsing "
              f"all of {STROKE_DATA_PATH} into memory. Re-run "
              f"'python3 fetch_stroke_data.py' to rebuild the index.", flush=True)
        return {}
    try:
        with open(STROKE_INDEX_PATH, "r", encoding="utf-8") as f:
            index = json.load(f)
        if index.get("source_bytes") != os.path.getsize(STROKE_DATA_PATH):
            raise ValueError("size does not match")
        if index.get("source_sha256") != _file_digest(STROKE_DATA_PATH):
            raise ValueError("checksum does not match")
        entries = index.get("entries") or {}
        # flush=True on every message in this group: stdout is block-buffered
        # whenever it is not a terminal, which is every real deployment. These
        # are the lines a deploy is checked against -- whether the index
        # arrived with the pull, or the service is silently paying the
        # pre-finding-20 memory cost -- and unflushed they sit in the buffer
        # while the access log (stderr) races ahead in the same journal. Their
        # absence then reads as a fault that is really just buffering. Same
        # reasoning as the account-reset log line.
        print(f"Loaded stroke-data index for {len(entries)} characters "
              f"({STROKE_DATA_PATH} stays on disk).", flush=True)
        return entries
    except Exception as e:
        print(f"Warning: {STROKE_INDEX_PATH} is stale or unreadable ({e}) -- "
              f"falling back to parsing all of {STROKE_DATA_PATH}. Re-run "
              f"'python3 fetch_stroke_data.py' to rebuild it.", flush=True)
        return {}


def stroke_entry_bytes(char):
    """
    One character's stroke data as raw JSON bytes, or None if not vendored.

    A 404 from the caller is not an error: app.js reads it as "not vendored"
    and falls back to the pinned CDN.
    """
    global _stroke_index, _stroke_data

    if not os.path.exists(STROKE_DATA_PATH):
        if _stroke_index is None:
            with _stroke_lock:
                if _stroke_index is None:
                    print(f"Notice: {STROKE_DATA_PATH} not found -- handwriting will "
                          f"fall back to the CDN. Run 'python3 fetch_stroke_data.py' "
                          f"to enable offline stroke data.", flush=True)
                    _stroke_index, _stroke_data = {}, {}
        return None

    if _stroke_index is None:
        with _stroke_lock:
            if _stroke_index is None:
                _stroke_index = _load_stroke_index()

    span = _stroke_index.get(char)
    if span is not None:
        offset, length = span
        # Opened per request rather than holding one shared handle: a seek on
        # a shared file object is not thread-safe, and ThreadingHTTPServer
        # means concurrent /api/strokes calls are real. The open is cheap and
        # the browser caches each character immutably, so this is rare.
        with open(STROKE_DATA_PATH, "rb") as f:
            f.seek(offset)
            return f.read(length)

    if _stroke_index:
        return None             # index is good and simply has no such character

    # Fallback: no usable index, so parse the file the old way.
    if _stroke_data is None:
        with _stroke_lock:
            if _stroke_data is None:
                with open(STROKE_DATA_PATH, "r", encoding="utf-8") as f:
                    _stroke_data = json.load(f)
                print(f"Loaded offline stroke data for {len(_stroke_data)} characters.",
                      flush=True)
    entry = _stroke_data.get(char)
    if entry is None:
        return None
    return json.dumps(entry, ensure_ascii=False).encode("utf-8")


class JuziAPIHandler(http.server.SimpleHTTPRequestHandler):
    # Without this, a connection that stops sending data mid-request (or
    # never finishes a declared body) blocks its handler thread forever --
    # harmless with ThreadingHTTPServer's own thread per connection, but a
    # cheap way for one bad connection to tie up resources indefinitely.
    # 30s is generous for a real client on the same LAN and short enough
    # that an abandoned/slow connection doesn't linger.
    timeout = 30

    def log_message(self, format, *args):
        """
        Logs requests with the account slug redacted.

        The /u/<slug>/ link IS the credential -- there is no username or
        password behind it, so anyone holding it has full read/write access to
        that account (see create_user.py). http.server logs the full request
        line by default, which put that credential into the systemd journal on
        every single request, where it is retained, rotated to disk, and
        readable by anyone with journal access. Caddy's own access log records
        it a second time; redact there too if those logs are kept.

        The slug is replaced rather than dropped so the logs stay useful for
        debugging: which account is unclear, but the route and status are not.
        """
        super().log_message(format, *(
            USER_SLUG_IN_LOG_RE.sub("/u/<redacted>", arg) if isinstance(arg, str) else arg
            for arg in args))

    def _strip_conditional_headers(self):
        """
        Removes If-Modified-Since/If-None-Match from this request before
        handing off to SimpleHTTPRequestHandler, so it can't 304 against a
        cache entry left by a *different* file previously served under the
        same URL. See the "/" route in do_GET for why that happens.
        """
        del self.headers["If-Modified-Since"]
        del self.headers["If-None-Match"]

    def do_GET(self):
        path = urllib.parse.urlparse(self.path).path

        if path == "/healthz":
            self._send_health()
            return

        if path == "/login":
            # Same bypass-the-allowlist pattern as index.html/landing.html
            # below: rewrite and hand off to SimpleHTTPRequestHandler's own
            # file lookup rather than adding login.html to ALLOWED_STATIC_PATHS.
            self.path = "/login.html"
            return super().do_GET()

        user_match = USER_PREFIX_RE.match(path)
        if user_match:
            engine = get_engine_for_id(user_match.group(1))
            if engine is None:
                self._send_404()
                return
            sub_path = user_match.group(2) or "/"
            if self._link_retired(user_match.group(1), sub_path):
                return
            if sub_path == "/manifest.json":
                self._send_manifest(f"/u/{user_match.group(1)}/")
                return
            if sub_path in ("/", "/index.html"):
                # Same page, served under the friend's own URL -- app.js
                # figures out which account it's talking to from
                # location.pathname (see API_BASE), so there's nothing
                # per-user to inject into the HTML itself.
                self.path = "/index.html"
                return super().do_GET()
            if self._handle_api_get(sub_path, engine):
                return
            self._send_404()
            return

        if REQUIRE_SLUG:
            # No /u/<slug>/ prefix, but there might still be a valid
            # real-login session cookie -- resolve it the same way a slug
            # would be, so a cookie-authenticated visitor gets the practice
            # app at the bare domain exactly like the old single-account
            # default_engine did, just scoped to their own account instead
            # of a shared one. Anonymous or invalid-cookie requests fall
            # through unchanged to the landing page / 404 below.
            user_id, _username = self._resolve_session_account()
            if path == "/api/account":
                # About the login account itself rather than its practice
                # data, so it resolves by user_id, not through an engine.
                if user_id is None:
                    self._send_404()
                else:
                    self._send_account(user_id)
                return
            engine = get_engine_for_id(user_id) if user_id else None
            if engine is not None and self._handle_api_get(path, engine):
                return
            if path in ("/", "/index.html"):
                # "/" serves either landing.html or index.html depending on
                # login state, but a browser's conditional-GET cache keys by
                # URL alone: it can 304 against the mtime of whichever file
                # it fetched here last time, and go on showing a stale
                # landing page to someone who has since logged in. Dropping
                # the conditional headers forces a real 200, so the file
                # that matches this response's login state is what's shown.
                self._strip_conditional_headers()
                if engine is not None:
                    self.path = "/index.html"
                    return super().do_GET()
                self.path = "/landing.html"
                return super().do_GET()
            if path.startswith("/api/"):
                self._send_404()
                return
        elif self._handle_api_get(path, default_engine):
            return

        if path == "/manifest.json":
            self._send_manifest("/")
            return

        # Refuse to serve anything not explicitly whitelisted (blocks config.py,
        # brain.json, .git, hanzi_db.csv, etc. from being fetched over the network)
        if path not in ALLOWED_STATIC_PATHS:
            self._send_404()
            return

        return super().do_GET()

    def do_HEAD(self):
        """
        HEAD answers for exactly what GET would serve, and nothing else.

        Without this override, SimpleHTTPRequestHandler's own do_HEAD answered
        for ANY file under the working directory, skipping do_GET's allowlist
        entirely: HEAD /.session_secret and HEAD /users/accounts.json returned
        200 with each file's real size and modification time. No contents left
        -- HEAD carries no body, verified both through Caddy and directly --
        but it confirmed which secrets exist and when accounts last changed.
        Found September 14, 2026 while adding /healthz, which uptime monitors
        commonly probe with HEAD.
        """
        path = urllib.parse.urlparse(self.path).path
        if path == "/healthz":
            self._send_health(head_only=True)
            return
        if path == "/login":
            self.path = "/login.html"
            return super().do_HEAD()
        if path in ("/", "/index.html"):
            # GET picks landing.html or index.html by session; a HEAD needs
            # no body to decide between them, so it answers for the public one.
            self.path = "/landing.html"
            return super().do_HEAD()
        if path in ALLOWED_STATIC_PATHS:
            return super().do_HEAD()
        self._send_404()

    def _send_health(self, head_only=False):
        """
        GET/HEAD /healthz. 200 "ok" when healthy, otherwise 503 with the
        problem words from health_problems(), so a single uptime monitor
        alerts on the disk filling, accounts.json becoming unreadable, or the
        nightly backup going stale -- as well as on the site being down, which
        Caddy reports as 502. No session needed: the monitor has none.
        """
        try:
            problems = health_problems()
        except Exception:
            problems = ["check"]
        body = (" ".join(problems) if problems else "ok").encode("ascii") + b"\n"
        self.send_response(503 if problems else 200)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        # A cached "ok" is worse than no answer at all.
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        if not head_only:
            self.wfile.write(body)

    def end_headers(self):
        """
        Makes the browser revalidate the app's own files instead of trusting a
        heuristic freshness guess.

        Without a Cache-Control header, Chrome caches app.js and style.css
        based on Last-Modified alone and will happily keep serving a stale copy
        after an update -- which presents as new code simply not running, with
        no error anywhere to explain it. `no-cache` still allows the cache, it
        just requires a revalidation first, so the usual response is a cheap
        304 rather than a re-download.

        A handler that has already set its own Cache-Control keeps it --
        /api/strokes sets a long immutable one, since its content is
        addressed by something that changes when it does. The
        check must be case-insensitive: _headers_buffer_names lowercases, so
        comparing against a capitalised "Cache-Control" silently never
        matches and appends a contradictory second `no-cache`, which wins
        when a browser combines the two.
        """
        if "cache-control" not in self._headers_buffer_names():
            self.send_header("Cache-Control", "no-cache")
        super().end_headers()

    def _headers_buffer_names(self):
        """Header names already queued for this response, lowercased."""
        return {
            line.split(b":", 1)[0].strip().lower().decode("latin-1")
            for line in getattr(self, "_headers_buffer", []) or []
            if b":" in line
        }

    # GET API routes: path -> method name. Each takes the resolved `engine`
    # and returns a payload to send as 200 JSON, or None having already
    # written its own response (the two that serve raw bytes). Anything
    # raised becomes a 500; see _handle_api_get.
    API_GET_ROUTES = {
        "/api/session": "_get_session",
        "/api/suggestions": "_get_word_suggestions",
        "/api/characters/suggestions": "_get_character_suggestions",
        "/api/progress": "_get_progress",
        "/api/sentences/importable": "_get_importable_sentences",
        "/api/settings": "_get_settings",
        "/api/onboarding/tiers": "_get_onboarding_tiers",
        "/api/strokes": "_get_strokes",
    }

    def _handle_api_get(self, path, engine):
        """
        Handles the API GET routes against a resolved `engine` -- either the
        default single-user one or a specific friend's, via
        get_engine_for_id. Returns True if `path` was one of these routes
        (a response has already been sent), False otherwise so the caller
        can fall through to static-file serving or 404.
        """
        method = self.API_GET_ROUTES.get(path)
        if method is None:
            return False
        try:
            payload = getattr(self, method)(engine)
            if payload is not None:
                self._send_json(200, payload)
        except Exception as e:
            self._send_json_error(500, str(e))
        return True

    def _get_session(self, engine):
        """The saved practice bank plus the counters the top bar shows."""
        brain_data = {"unlocked_chars": {}, "sentences": []}
        with engine.brain_lock:
            if os.path.exists(engine.brain_path):
                with open(engine.brain_path, "r", encoding="utf-8") as f:
                    brain_data = json.load(f)

        unlocked_chars = brain_data.get("unlocked_chars", {})
        saved_sentences = brain_data.get("sentences", [])

        # Bootstrap an initial batch from the local corpus if the bank is
        # empty but characters exist, so first-run works with zero
        # configuration -- and rebuild a character-only bank narrower than
        # the unlocked pool. The saved bank is otherwise replaced only on an
        # explicit "Get Sentences", which is right for sentences and strands
        # people in character practice: a Tier 1 account seeded before the
        # batch covered the pool holds three of its five characters and loops
        # them forever while the other two sit in the "Due" badge,
        # unreachable. beginner_bank_is_stale asks for exactly the size a
        # fresh batch would be, so this settles after one rebuild rather than
        # rewriting brain.json on every page load.
        if unlocked_chars and (not saved_sentences
                               or engine.beginner_bank_is_stale(brain_data)):
            try:
                engine.generate_fresh_session(count=3)
                # Re-read rather than trust that call's own return value:
                # generate_fresh_session persists the (always-simplified)
                # bank to disk but returns an already script-converted copy
                # (see apply_character_script), and unlocked_chars/onboarded/
                # etc. below need the raw simplified brain state, not that.
                with engine.brain_lock:
                    with open(engine.brain_path, "r", encoding="utf-8") as f:
                        brain_data = json.load(f)
                unlocked_chars = brain_data.get("unlocked_chars", {})
                saved_sentences = brain_data.get("sentences", [])
            except Exception as gen_err:
                print(f"Session bootstrap notice: {gen_err}")

        # Backfill per-character hint data on sentences saved before it
        # existed. char_pinyin (per-POSITION and context-aware) is newer than
        # char_metadata, so a bank written by an older build has the latter
        # but not the former; rebuild whenever either is missing. Computed
        # for the response only, not written back -- the saved bank is
        # rewritten wholesale on the next generated batch, and this GET
        # deliberately holds no write lock.
        for sentence in saved_sentences:
            if "char_metadata" not in sentence or "char_pinyin" not in sentence:
                engine.attach_char_data(sentence, unlocked_chars)

        # Converted here, once, for the response only -- saved_sentences (and
        # brain_data["sentences"] on disk) stay simplified either way.
        display_sentences = engine.apply_character_script(
            saved_sentences, engine.character_script(brain_data))

        return {
            "sentences": display_sentences,
            "total_unlocked_count": len(unlocked_chars),
            "total_due_count": engine.total_due_count(brain_data),
            # Characters unlocked but held behind the daily intake cap, so
            # the badge can say "12 due, 60 waiting" rather than presenting
            # the whole backlog as today's work.
            "new_backlog": engine.new_character_backlog(unlocked_chars),
            # False only for a brand-new create_user.py account that hasn't
            # picked a starting tier yet -- app.js shows the tier picker
            # instead of the normal session in that case. A missing key
            # (every brain predating the picker) defaults True so existing
            # installs are never re-prompted.
            "onboarded": bool(brain_data.get("onboarded", True)),
        }

    def _get_word_suggestions(self, engine):
        """Highest-frequency compound words not yet added, for "Suggest Words"."""
        return {"suggestions": engine.suggest_new_words(count=5)}

    def _get_character_suggestions(self, engine):
        """
        The most useful characters not yet unlocked, for "Suggest
        Characters" -- the same question the words tab answers, asked about
        the actual practice unit this app is built to teach.
        """
        return {"suggestions": engine.suggest_new_characters(count=8)}

    def _get_progress(self, engine):
        """Everything the progress view needs, in one request."""
        return engine.progress_summary()

    def _get_importable_sentences(self, engine):
        """
        The checklist behind the Overview tab's "N sentences writable"
        quick-import button: every corpus sentence the current pool can
        already fully write that isn't in the Sentence Bank yet.
        """
        return engine.list_importable_sentences()

    def _get_settings(self, engine):
        """
        The Settings panel's payload: the stored values plus the bounds and
        today's counts that make the number mean something.
        """
        return engine.read_settings()

    def _get_onboarding_tiers(self, _engine):
        """
        The starting-tier catalog shown to a friend who hasn't onboarded yet
        (see /api/onboarding/seed and TIER_INFO in seed_brain.py). Static,
        shared reference data, so the engine is unused.
        """
        return {"tiers": [{"size": size, **TIER_INFO[size]} for size in SIZE_CHOICES]}

    def _get_strokes(self, _engine):
        """
        One character's stroke-order data out of the vendored
        stroke_data.json, replacing Hanzi Writer's default per-character
        fetch to cdn.jsdelivr.net. A 404 here is not an error: app.js reads
        it as "not vendored" and falls back to the CDN. Shared reference
        data, not per-user, so the engine is unused.
        """
        char = self._query_param("char")
        # Already-encoded JSON straight off disk (see stroke_entry_bytes) --
        # nothing to parse or re-serialise.
        payload = stroke_entry_bytes(char) if char else None
        if payload is None:
            self._send_json_error(404, "No vendored stroke data.")
            return None
        # A character's stroke data never changes; let the browser keep it so
        # repeat characters don't re-request every time.
        self._send_bytes(payload, "application/json; charset=utf-8", immutable=True)
        return None

    def _csrf_check_failed(self):
        """Rejects cross-origin POSTs. Without this, any page the user has open in
        another tab can silently fetch() one of our write endpoints (CORS-simple
        request, since we don't require a preflight) and it would just work --
        importing junk text or corrupting brain.json. Two independent checks:
        1. Content-Type must be application/json. A plain HTML <form> or a
           fetch() with a "simple" content-type (text/plain, form-urlencoded)
           can be fired cross-origin with no preflight; application/json can't.
        2. If the browser sent an Origin header (it always does for fetch/XHR),
           it must match the Host we're being addressed as. This is defense in
           depth against a same-site page on a different port/scheme, and
           costs nothing for legitimate same-origin requests.
        Returns True (and has already written a 403 response) if the request
        should be rejected.
        """
        content_type = self.headers.get("Content-Type", "")
        if content_type.split(";")[0].strip().lower() != "application/json":
            self._send_json_error(403, "Content-Type must be application/json.")
            return True

        origin = self.headers.get("Origin")
        if origin is not None:
            if urllib.parse.urlparse(origin).netloc != self.headers.get("Host", ""):
                self._send_json_error(403, "Cross-origin request rejected.")
                return True

        return False

    def _send_404(self):
        """A bare, body-less 404 -- the response for any path not routed."""
        self.send_response(404)
        self.end_headers()

    def _send_bytes(self, payload, content_type, status=200, immutable=False,
                    cookie=None):
        """
        The one place a response body is written. `immutable` marks content
        addressed by something that changes when the content does (a
        character's stroke data, a sentence hash), so the browser may keep it
        for good rather than revalidating -- end_headers leaves an explicit
        Cache-Control alone.
        """
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(payload)))
        if immutable:
            self.send_header("Cache-Control", "public, max-age=31536000, immutable")
        if cookie:
            self.send_header("Set-Cookie", cookie)
        self.end_headers()
        self.wfile.write(payload)

    def _send_json(self, status, payload, cookie=None):
        self._send_bytes(json.dumps(payload, ensure_ascii=False).encode("utf-8"),
                         "application/json; charset=utf-8", status, cookie=cookie)

    def _send_json_error(self, status, message):
        self._send_json(status, {"error": message})

    def _send_manifest(self, start_url):
        self._send_bytes(
            json.dumps(build_manifest(start_url), ensure_ascii=False).encode("utf-8"),
            "application/manifest+json; charset=utf-8")

    def _query_param(self, name):
        """One query-string value off the current request URL, "" if absent."""
        params = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
        return (params.get(name) or [""])[0]

    def _read_json_body_or_reject(self, empty_default="{}"):
        """
        Validates Content-Length and reads the POST body, capping it at
        MAX_BODY_SIZE before ever reading from the socket -- without this, a
        client can declare (and send) an arbitrarily large body and the
        server buffers all of it in memory with no limit. Also rejects a
        missing/negative/non-numeric Content-Length outright rather than
        letting a bad header value reach self.rfile.read().

        Returns the decoded body string (or `empty_default` when the body is
        empty), or None if a rejection response has already been sent -- the
        caller must return immediately when it gets None.
        """
        raw_length = self.headers.get('Content-Length')
        try:
            content_length = int(raw_length) if raw_length is not None else 0
        except ValueError:
            content_length = -1

        if content_length < 0:
            self._send_json_error(400, "Missing or invalid Content-Length.")
            return None
        if content_length > MAX_BODY_SIZE:
            self._send_json_error(413, f"Request body too large (max {MAX_BODY_SIZE} bytes).")
            return None
        if content_length == 0:
            return empty_default
        return self.rfile.read(content_length).decode('utf-8')

    def _client_ip(self):
        """
        The requester's real IP for rate limiting, preferring the
        X-Forwarded-For Caddy sets when this process sits behind it (see the
        Caddyfile) -- without it every request would appear to come from
        127.0.0.1 and share one rate-limit bucket. Falls back to the raw
        socket address for local development with no proxy in front.
        """
        forwarded = self.headers.get("X-Forwarded-For")
        if forwarded:
            return forwarded.split(",")[0].strip()
        return self.client_address[0]

    def _resolve_session_account(self):
        """
        Returns (user_id, username) for a valid, current real-login session
        cookie, or (None, None) if there isn't one -- missing, malformed,
        expired, or signed under a session_version older than the account's
        current one (e.g. after a password reset). Never raises; every
        failure mode is simply "not logged in".
        """
        cookie_header = self.headers.get("Cookie")
        if not cookie_header:
            return None, None
        jar = http.cookies.SimpleCookie()
        try:
            jar.load(cookie_header)
        except Exception:
            return None, None
        morsel = jar.get(auth.SESSION_COOKIE_NAME)
        if morsel is None:
            return None, None
        verified = auth.verify_session_cookie(morsel.value)
        if verified is None:
            return None, None
        user_id, session_version = verified
        with ACCOUNTS_LOCK:
            accounts_data = accounts.load_accounts()
        username, entry = accounts.find_account_by_user_id(accounts_data, user_id)
        if entry is None or entry.get("session_version") != session_version:
            return None, None
        return user_id, username

    @staticmethod
    def _session_cookie(user_id, session_version):
        """
        The Set-Cookie value for a fresh session.

        HttpOnly: JavaScript can't read it, so it isn't a target for an XSS
        payload to exfiltrate. SameSite=Lax: the browser won't attach it to a
        cross-site POST at all, which is a second, independent layer on top
        of the CSRF check. Secure per COOKIE_SECURE -- see its definition for
        why that is a separate flag from REQUIRE_SLUG.
        """
        flags = "HttpOnly; SameSite=Lax; Path=/"
        if COOKIE_SECURE:
            flags += "; Secure"
        return (f"{auth.SESSION_COOKIE_NAME}={auth.make_session_cookie(user_id, session_version)}; "
                f"Max-Age={auth.SESSION_TTL_SECONDS}; {flags}")

    def _handle_signup(self):
        """
        POST /api/signup. Body: {"username", "password", "email", "invite_code"}.
        Creates a brand new real-login account (same empty_brain() shape
        every account-creation path uses) and logs it straight in, so the
        redirect to / lands on the existing onboarding tier picker with no
        extra step -- a fresh account's brain.json already has
        "onboarded": false, and app.js's fetchNewSession already knows what
        to do with that.

        The email is required but not yet trusted: it is stored as pending
        and a confirmation link goes to it. Logging in doesn't wait on that
        -- a friend should be practising in seconds, not after a trip to
        their inbox -- and nothing but that one link is mailed to the
        address until it has been confirmed.
        """
        try:
            body = self._read_json_body_or_reject()
            if body is None:
                return
            data = json.loads(body)
            username = (data.get("username") or "").strip()
            password = data.get("password") or ""
            email = accounts.normalize_email(data.get("email"))
            invite_code = (data.get("invite_code") or "").strip()

            ip_key = f"signup:{self._client_ip()}"
            if auth.rate_limited(ip_key):
                self._send_json_error(429, "Too many signup attempts. Try again in a few minutes.")
                return
            auth.record_attempt(ip_key)

            with ACCOUNTS_LOCK:
                invite_codes = invites.load_invite_codes()
                if not invites.redeem_invite_code(invite_codes, invite_code):
                    self._send_json_error(400, "Invalid or already-used invite code.")
                    return

                accounts_data = accounts.load_accounts()
                try:
                    accounts.validate_new_account(accounts_data, username, password)
                    accounts.validate_email(email)
                except ValueError as bad:
                    self._send_json_error(400, str(bad))
                    return

                user_id = accounts.create_account(accounts_data, username, password, email=email)
                invites.mark_invite_code_used(invite_codes, invite_code, username.lower())
                accounts.save_accounts(accounts_data)
                invites.save_invite_codes(invite_codes)

            self._send_confirmation_link(user_id, email)
            # A brand new account always starts at INITIAL_SESSION_VERSION --
            # no need to re-read the record just written above.
            self._send_json(200, {"ok": True}, cookie=self._session_cookie(
                user_id, accounts.INITIAL_SESSION_VERSION))
        except Exception as e:
            self._send_json_error(500, str(e))

    def _handle_login(self):
        """
        POST /api/login. Body: {"username", "password"}. Wrong password and
        unknown username get the exact same 401 message so this can't be
        used to enumerate valid usernames (see accounts.verify_login's
        constant-time dummy-hash comparison for the same reasoning applied
        to timing).
        """
        try:
            body = self._read_json_body_or_reject()
            if body is None:
                return
            data = json.loads(body)
            username = (data.get("username") or "").strip()
            password = data.get("password") or ""

            ip_key = f"login:{self._client_ip()}"
            if auth.rate_limited(ip_key):
                self._send_json_error(429, "Too many login attempts. Try again in a few minutes.")
                return
            auth.record_attempt(ip_key)

            with ACCOUNTS_LOCK:
                accounts_data = accounts.load_accounts()
            result = accounts.verify_login(accounts_data, username, password)
            if result is None:
                self._send_json_error(401, "Invalid username or password.")
                return
            user_id, session_version = result
            self._send_json(200, {"ok": True},
                            cookie=self._session_cookie(user_id, session_version))
        except Exception as e:
            self._send_json_error(500, str(e))

    def _handle_logout(self):
        # No body or existing session required -- clearing a cookie that may
        # not even be valid is harmless, so a stray call here just no-ops.
        self._send_json(200, {"ok": True},
                        cookie=f"{auth.SESSION_COOKIE_NAME}=; Max-Age=0; Path=/")

    def _send_redirect(self, location):
        self.send_response(302)
        self.send_header("Location", location)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def _link_retired(self, slug, sub_path):
        """
        Once a link account has been claimed as a real login (see
        _handle_link_claim), its /u/<slug>/ link stops granting access.
        Otherwise it would remain a password-free way into an account that now
        has a password, an email address and, eventually, billing -- and the
        person agreed to exactly this when they claimed it.

        The page itself redirects, so a bookmark or an installed home-screen
        icon lands somewhere useful rather than on an error: straight into the
        app if this browser holds the session, otherwise the login page. API
        calls get a 410 with a message instead, since a script can't usefully
        follow a redirect to a login page. Returns True if a response was sent.
        """
        with ACCOUNTS_LOCK:
            accounts_data = accounts.load_accounts()
        if accounts.find_account_by_user_id(accounts_data, slug)[1] is None:
            return False
        if sub_path.startswith("/api/"):
            self._send_json_error(410, "This link was replaced by a username and password. "
                                       "Log in at juzigenius.com/login.")
        else:
            user_id, _username = self._resolve_session_account()
            self._send_redirect("/" if user_id else "/login")
        return True

    def _handle_link_claim(self, slug):
        """
        POST /u/<slug>/api/account/claim. Body: {"username", "email",
        "password"}. Turns this link account into a real login account in
        place -- the same users/<slug>/ directory, every bit of its history --
        and logs this browser in. No invite code: holding the link already
        proves the account is theirs. From the next request on, the link
        itself is retired (see _link_retired).
        """
        try:
            data = self._json_body()
            if data is None:
                return
            username = (data.get("username") or "").strip()
            password = data.get("password") or ""
            email = accounts.normalize_email(data.get("email"))

            ip_key = f"claim:{self._client_ip()}"
            if auth.rate_limited(ip_key):
                self._send_json_error(429, "Too many attempts. Try again in a few minutes.")
                return
            auth.record_attempt(ip_key)

            with ACCOUNTS_LOCK:
                accounts_data = accounts.load_accounts()
                try:
                    accounts.validate_new_account(accounts_data, username, password)
                    accounts.validate_email(email)
                    accounts.claim_link_account(accounts_data, slug, username, password, email)
                except ValueError as bad:
                    self._send_json_error(400, str(bad))
                    return
                registry = user_registry.load_registry()
                user_registry.mark_converted(registry, slug, username)
                accounts.save_accounts(accounts_data)
                user_registry.save_registry(registry)

            self._send_confirmation_link(slug, email)
            self._send_json(200, {"ok": True}, cookie=self._session_cookie(
                slug, accounts.INITIAL_SESSION_VERSION))
        except Exception as e:
            self._send_json_error(500, str(e))

    def _send_account(self, user_id):
        """GET /api/account: the login account's own username and email state."""
        with ACCOUNTS_LOCK:
            accounts_data = accounts.load_accounts()
        _username, entry = accounts.find_account_by_user_id(accounts_data, user_id)
        if entry is None:
            self._send_404()
            return
        self._send_json(200, accounts.public_view(entry))

    def _send_confirmation_link(self, user_id, email):
        token = auth.make_action_token("verify-email", {"u": user_id, "e": email},
                                       auth.EMAIL_VERIFY_TTL_SECONDS)
        mailer.send_verification(email, f"{PUBLIC_URL}/login#verify={token}")

    def _handle_account_email(self, user_id):
        """
        POST /api/account/email. Body: {"email"}. Session required. Records
        the address as pending and mails it a confirmation link; a confirmed
        address already on the account keeps working until that link is
        opened. Posting the same pending address again is how "resend" works.
        """
        try:
            data = self._json_body()
            if data is None:
                return
            email = accounts.normalize_email(data.get("email"))
            try:
                accounts.validate_email(email)
            except ValueError as bad:
                self._send_json_error(400, str(bad))
                return

            # Per account rather than per IP: what this protects is somebody
            # else's inbox, which a logged-in account could otherwise fill
            # with confirmation mail by posting their address in a loop.
            limit_key = f"email-change:{user_id}"
            if auth.rate_limited(limit_key):
                self._send_json_error(429, "Too many emails sent. Try again in a few minutes.")
                return

            with ACCOUNTS_LOCK:
                accounts_data = accounts.load_accounts()
                _username, entry = accounts.find_account_by_user_id(accounts_data, user_id)
                if entry is None:
                    self._send_404()
                    return
                needs_link = accounts.request_email_change(entry, email)
                accounts.save_accounts(accounts_data)
                view = accounts.public_view(entry)

            if needs_link:
                auth.record_attempt(limit_key)
                self._send_confirmation_link(user_id, email)
            self._send_json(200, view)
        except Exception as e:
            self._send_json_error(500, str(e))

    def _handle_email_verify(self):
        """
        POST /api/email/verify. Body: {"token"}. No session needed: the link
        is usually opened wherever the mail is read, often a phone that has
        never logged in, and the signed token already proves both which
        account it is and that the opener receives mail at the address.

        A POST from login.html's script, not a GET on the emailed URL itself,
        so opening a URL never changes anything by itself. The token rides
        in the URL fragment, which no browser sends to any server, keeping it
        out of every access log.
        """
        try:
            data = self._json_body()
            if data is None:
                return
            claims = auth.verify_action_token("verify-email", data.get("token"))
            if claims is None:
                self._send_json_error(400, "This link is invalid or has expired. "
                                           "Send a new one from Settings.")
                return

            with ACCOUNTS_LOCK:
                accounts_data = accounts.load_accounts()
                outcome = accounts.confirm_email(accounts_data, claims.get("u"), claims.get("e"))
                if outcome == "verified":
                    accounts.save_accounts(accounts_data)

            if outcome in ("verified", "already_verified"):
                self._send_json(200, {"ok": True, "email": claims["e"]})
            elif outcome == "stale":
                self._send_json_error(400, "This account has asked to confirm a different "
                                           "address since this link was sent. Use the newest link.")
            elif outcome == "taken":
                self._send_json_error(409, "That address is already confirmed on another "
                                           "JuziGenius account.")
            else:
                self._send_json_error(400, "This link is invalid or has expired.")
        except Exception as e:
            self._send_json_error(500, str(e))

    def _handle_password_forgot(self):
        """
        POST /api/password/forgot. Body: {"email"}. Answers the same 200
        whether or not the address belongs to an account -- any difference
        would turn this form into a way to test which addresses are
        registered. The mail goes out on a background thread (mailer.send),
        so response timing gives the answer away no more than the body does.

        Only a CONFIRMED address ever receives a reset link. A pending one is
        just text typed into a settings field, possibly someone else's.
        """
        try:
            data = self._json_body()
            if data is None:
                return
            email = accounts.normalize_email(data.get("email"))
            try:
                accounts.validate_email(email)
            except ValueError as bad:
                self._send_json_error(400, str(bad))
                return

            # Two limits. Per IP stops one client hammering the endpoint; per
            # address stops many clients mail-bombing one inbox. The address
            # key is counted whether or not an account matched, so the limit
            # can't be used to tell those apart either.
            ip_key = f"forgot:{self._client_ip()}"
            email_key = f"forgot-email:{email}"
            if auth.rate_limited(ip_key) or auth.rate_limited(email_key, max_attempts=3):
                self._send_json_error(429, "Too many reset requests. Try again in a few minutes.")
                return
            auth.record_attempt(ip_key)
            auth.record_attempt(email_key)

            with ACCOUNTS_LOCK:
                accounts_data = accounts.load_accounts()
            username, entry = accounts.find_account_by_email(accounts_data, email)
            if entry is not None:
                token = auth.make_action_token(
                    "reset-password",
                    {"u": entry["user_id"], "e": email, "v": entry["session_version"]},
                    auth.PASSWORD_RESET_TTL_SECONDS)
                mailer.send_password_reset(email, f"{PUBLIC_URL}/login#reset={token}",
                                           entry.get("display_name") or username)

            self._send_json(200, {"ok": True})
        except Exception as e:
            self._send_json_error(500, str(e))

    def _handle_password_reset(self):
        """
        POST /api/password/reset. Body: {"token", "password"}. Sets the new
        password and logs this browser straight in.

        A reset link is single-use with no record of used links. Its token
        carries the account's session_version, and setting a password bumps
        that version (accounts.set_password), so the same link fails the
        second time -- as does every session cookie issued before the reset,
        which is the point when the reset is happening because someone else
        had the old password. The token also carries the address it was
        mailed to, so changing the account's email voids any link still
        outstanding at the old one.
        """
        try:
            data = self._json_body()
            if data is None:
                return
            password = data.get("password") or ""
            invalid = "This reset link is invalid, already used, or expired. Request a new one."

            claims = auth.verify_action_token("reset-password", data.get("token"))
            if claims is None:
                self._send_json_error(400, invalid)
                return
            try:
                accounts.validate_password(password)
            except ValueError as bad:
                self._send_json_error(400, str(bad))
                return

            with ACCOUNTS_LOCK:
                accounts_data = accounts.load_accounts()
                username, entry = accounts.find_account_by_user_id(accounts_data, claims.get("u"))
                if (entry is None
                        or entry.get("session_version") != claims.get("v")
                        or entry.get("email") != claims.get("e")):
                    self._send_json_error(400, invalid)
                    return
                accounts.set_password(accounts_data, username, password)
                accounts.save_accounts(accounts_data)
                user_id, session_version = entry["user_id"], entry["session_version"]

            self._send_json(200, {"ok": True},
                            cookie=self._session_cookie(user_id, session_version))
        except Exception as e:
            self._send_json_error(500, str(e))

    def do_POST(self):
        path = urllib.parse.urlparse(self.path).path

        if self._csrf_check_failed():
            return

        # Auth endpoints are reachable at the root regardless of
        # REQUIRE_SLUG -- they're how an anonymous visitor becomes a
        # session-cookie-authenticated one in the first place, so they can't
        # themselves require a session or a slug. The emailed-link endpoints
        # belong here for the same reason: the person opening the link is
        # very often not logged in.
        if path == "/api/signup":
            self._handle_signup()
            return
        if path == "/api/login":
            self._handle_login()
            return
        if path == "/api/logout":
            self._handle_logout()
            return
        if path == "/api/password/forgot":
            self._handle_password_forgot()
            return
        if path == "/api/password/reset":
            self._handle_password_reset()
            return
        if path == "/api/email/verify":
            self._handle_email_verify()
            return

        user_match = USER_PREFIX_RE.match(path)
        if user_match:
            engine = get_engine_for_id(user_match.group(1))
            if engine is None:
                self._send_404()
                return
            sub_path = user_match.group(2) or "/"
            if self._link_retired(user_match.group(1), sub_path):
                return
            if sub_path == "/api/account/claim":
                self._handle_link_claim(user_match.group(1))
                return
            if self._handle_api_post(sub_path, engine):
                return
            self._send_404()
            return

        if REQUIRE_SLUG:
            # Same idea as do_GET: no /u/<slug>/ prefix, but a valid
            # real-login session cookie still resolves to that account's
            # own engine.
            user_id, _username = self._resolve_session_account()
            if path == "/api/account/email":
                if user_id is None:
                    self._send_404()
                else:
                    self._handle_account_email(user_id)
                return
            engine = get_engine_for_id(user_id) if user_id else None
            if engine is not None and self._handle_api_post(path, engine):
                return
            self._send_404()
            return

        if self._handle_api_post(path, default_engine):
            return

        self._send_404()

    # POST API routes: path -> (method name, status for an unexpected error,
    # whether a ValueError is the client's fault rather than ours). Each
    # method takes the resolved `engine` and returns a payload to send as 200
    # JSON, or None having already written its own response (a rejected body,
    # a validation failure). See _handle_api_post.
    API_POST_ROUTES = {
        "/api/onboarding/seed": ("_post_onboarding_seed", 500, False),
        "/api/account/reset": ("_post_account_reset", 500, False),
        "/api/settings": ("_post_settings", 500, False),
        "/api/characters/add": ("_post_characters_add", 500, False),
        "/api/sentence/complete": ("_post_sentence_complete", 500, False),
        "/api/sentence/edit": ("_post_sentence_edit", 500, True),
        "/api/sentence/delete": ("_post_sentence_delete", 500, False),
        "/api/sentences/import": ("_post_sentences_import", 500, False),
        "/api/import": ("_post_import", 500, False),
        # 502 rather than 500: generating a batch is the one thing that can
        # fail because the corpus underneath it came up empty-handed.
        "/api/session/generate": ("_post_session_generate", 502, False),
        "/api/session/refresh": ("_post_session_refresh", 502, False),
        "/api/suggestions/add": ("_post_suggestions_add", 500, False),
        # A missing field or an unknown character/word is a client error.
        "/api/character/review": ("_post_character_review", 500, True),
        "/api/word/review": ("_post_word_review", 500, True),
    }

    def _handle_api_post(self, path, engine):
        """
        Handles the API POST routes against a resolved `engine` -- either the
        default single-user one or a specific friend's, via
        get_engine_for_id. Returns True if `path` was one of these routes
        (a response has already been sent), False otherwise so the caller
        can 404.
        """
        route = self.API_POST_ROUTES.get(path)
        if route is None:
            return False
        method, error_status, value_error_is_client = route
        try:
            payload = getattr(self, method)(engine)
            if payload is not None:
                self._send_json(200, payload)
        except ValueError as e:
            self._send_json_error(400 if value_error_is_client else error_status, str(e))
        except Exception as e:
            self._send_json_error(error_status, str(e))
        return True

    def _json_body(self):
        """
        The decoded JSON object for this POST, or None if the body was
        already rejected (too large, bad Content-Length) and the caller must
        return immediately.
        """
        body = self._read_json_body_or_reject()
        return None if body is None else json.loads(body)

    def _post_onboarding_seed(self, engine):
        """
        First-run tier choice: a friend picks their own starting pool from
        the tier picker app.js shows, instead of the operator choosing a
        --size for them at create_user.py time. Body: { "size": 50 }.

        Only works once -- an account with characters already unlocked, or
        already marked onboarded, is left untouched (409), so this can't be
        replayed to wipe out real progress later. Reads and writes brain.json
        directly rather than through a JuziEngine method, same as
        _get_session.
        """
        data = self._json_body()
        if data is None:
            return None
        size = data.get("size")
        if size not in SIZE_CHOICES:
            self._send_json_error(400, f"'size' must be one of {list(SIZE_CHOICES)}.")
            return None

        with engine.brain_lock:
            brain_data = {}
            if os.path.exists(engine.brain_path):
                with open(engine.brain_path, "r", encoding="utf-8") as f:
                    brain_data = json.load(f)

            if brain_data.get("onboarded", True) or brain_data.get("unlocked_chars"):
                self._send_json_error(409, "This account has already been set up.")
                return None

            new_brain = seed_build_brain(size, engine.load_master_dictionary())
            save_brain(engine.brain_path, new_brain)

        return {
            "size": size,
            "name": TIER_INFO[size]["name"],
            "total_unlocked_count": len(new_brain["unlocked_chars"]),
        }

    def _post_account_reset(self, engine):
        """
        Wipes the account back to a brand new one -- everything unlocked,
        every SM-2 schedule, every completed sentence and every personally
        pasted sentence, followed by the tier picker on the next session
        fetch. Body: { "confirm": "RESET" }.

        This is the account owner's own escape hatch (the Start Over tab),
        for someone who wants to begin again rather than live with a pool
        they picked wrong or a review backlog they have given up on. Without
        it the only way back was to ask the operator to run reset_user.py.

        Note what it deliberately gives up. _post_onboarding_seed is one-shot
        precisely so that holding the link cannot wipe an account's progress;
        this hands that capability back, and the link is the only credential
        there is. That is inherent in the feature, and it widens nothing that
        link-holding did not already permit -- anyone with the link can
        already grade characters wrongly or import junk. What remains is the
        CSRF pair on every POST, the confirmation token below, and the
        two-step confirmation in the UI.
        """
        data = self._json_body()
        if data is None:
            return None
        if data.get("confirm") != RESET_CONFIRMATION:
            self._send_json_error(
                400, f"Reset requires \"confirm\": \"{RESET_CONFIRMATION}\".")
            return None

        with engine.brain_lock:
            brain_data = {}
            if os.path.exists(engine.brain_path):
                with open(engine.brain_path, "r", encoding="utf-8") as f:
                    brain_data = json.load(f)

            # Counted before the overwrite so the response can report what
            # was actually destroyed, rather than what the client last
            # happened to render.
            erased = {key: len(brain_data.get(key) or {}) for key in
                      ("unlocked_chars", "unlocked_words",
                       "completed_sentences", "pasted_sentences")}

            save_brain(engine.brain_path, empty_brain(), force_snapshot=True)

        # Logged because it is irreversible and someone will ask what
        # happened to their progress. The account is not named: the slug is
        # the credential and does not belong in a log (see log_message), so
        # this records that a reset happened and how much it took, not whose
        # it was. flush=True because stdout is block-buffered whenever it is
        # not a terminal -- which is every real deployment, systemd included
        # -- so without it this line sits in the buffer while the access log
        # (stderr, unbuffered) races ahead.
        print(f"Account reset on request: erased {erased['unlocked_chars']} characters, "
              f"{erased['completed_sentences']} completed sentences, "
              f"{erased['pasted_sentences']} saved sentences.", flush=True)

        return {"reset": True, "erased": erased}

    def _post_settings(self, engine):
        """
        Saves the Settings panel. Body: { "daily_new_limit": 15 }.
        update_settings ignores any key it doesn't understand, so this can't
        be used to write arbitrary data into brain.json, and raises
        ValueError on an out-of-range value rather than clamping -- someone
        who types 500 should be told the cap, not left believing they set it.
        """
        values = self._json_body()
        if values is None:
            return None
        if not isinstance(values, dict):
            self._send_json_error(400, "Body must be a JSON object.")
            return None
        try:
            return engine.update_settings(values)
        except ValueError as bad:
            self._send_json_error(400, str(bad))
            return None

    def _post_characters_add(self, engine):
        """
        Unlocks characters chosen in the Suggest Characters tab.
        Body: { "chars": ["是", "人", ...] }
        """
        data = self._json_body()
        if data is None:
            return None
        chars = data.get("chars", [])
        if not isinstance(chars, list):
            self._send_json_error(400, "'chars' must be a list.")
            return None
        return engine.add_characters(chars)

    def _post_sentence_complete(self, engine):
        """
        Records that a sentence was written all the way through, so batches
        stop re-serving what was just practiced. Body: { "chinese": "..." }
        """
        data = self._json_body()
        if data is None:
            return None
        return engine.record_sentence_completion(data.get("chinese", ""))

    def _post_sentences_import(self, engine):
        """
        Bulk-adds sentences selected from the "N sentences writable"
        checklist straight to the Sentence Bank -- no writing practice
        required. Body: { "sentences": ["...", ...] }
        """
        data = self._json_body()
        if data is None:
            return None
        sentences = data.get("sentences", [])
        if not isinstance(sentences, list):
            self._send_json_error(400, "'sentences' must be a list.")
            return None
        return engine.bulk_add_to_sentence_bank(sentences)

    def _post_import(self, engine):
        """
        Imports pasted text. Body: { "text": "<chinese and english,
        combined>" } -- import_text_locally auto-detects Chinese/English
        pairs within the single blob and saves what it finds to the user's
        persistent pasted_sentences for future practice.
        """
        data = self._json_body()
        if data is None:
            return None
        return engine.import_text_locally(data.get("text", ""))

    def _post_session_generate(self, engine):
        """
        Generates a brand new batch of real corpus sentences, replacing the
        saved bank. No body fields required.

        3 keeps each batch focused rather than exhausting due/relevant
        sentences in one go. styles={"sentences"} overrides the account's
        general study-styles setting: this button is an explicit "give me
        sentences" request, not the general session bootstrap the toggle
        governs. allow_character_fallback=True is likewise explicit -- even
        with "Individual characters" unchecked in Settings, this still falls
        back to characters for a pool too small for any sentence yet, and
        app.js says why when that happens, so it never reads as ignoring the
        toggle. restrict_sentences_to_bank=False is the one deliberate
        exception to bank-only practice: pulling in new corpus sentences is
        this button's whole purpose.
        """
        if self._json_body() is None:
            return None
        return engine.generate_fresh_session(
            count=3, styles={"sentences"}, allow_character_fallback=True,
            restrict_sentences_to_bank=False)

    def _post_session_refresh(self, engine):
        """
        Regenerates the practice batch using the account's own settings --
        study_styles and bank-restricted sentences -- the same call
        _get_session's bootstrap uses, with no overrides. Called by the
        client once it has cycled through every item in the current batch,
        so ordinary practice picks up newly completed/pasted sentences and
        newly unlocked characters instead of looping one fixed batch forever
        between page loads. Unlike _post_session_generate this always stays
        bank-restricted: it's the automatic path, not an explicit request to
        discover new material.
        """
        if self._json_body() is None:
            return None
        return engine.generate_fresh_session(count=5)

    def _post_suggestions_add(self, engine):
        """
        Adds user-selected words from the "Suggest Words" tab to brain.json.
        Body: { "words": ["谢谢", "再见", ...] }
        """
        data = self._json_body()
        if data is None:
            return None
        return engine.add_words(data.get("words", []))

    def _post_sentence_edit(self, engine):
        """
        Corrects a personal sentence's Chinese and/or English -- fixing a
        typo or a wrong translation without deleting and re-pasting it.
        Body: { "old_chinese": "...", "chinese": "...", "english": "..." }
        """
        data = self._json_body()
        if data is None:
            return None
        return engine.edit_pasted_sentence(
            data.get("old_chinese", ""), data.get("chinese", ""), data.get("english", ""))

    def _post_sentence_delete(self, engine):
        """
        Removes a personal sentence from the Sentence Bank entirely.
        Body: { "chinese": "..." }
        """
        data = self._json_body()
        if data is None:
            return None
        return engine.delete_pasted_sentence(data.get("chinese", ""))

    def _post_character_review(self, engine):
        """
        Grades one completed character quiz and advances its SM-2 scheduling
        fields in brain.json. Body: { "char": "我", "quality": 0-5 }
        """
        data = self._json_body()
        if data is None:
            return None
        char, quality = data.get("char", ""), data.get("quality")
        if not char or quality is None:
            raise ValueError("Both 'char' and 'quality' are required.")
        return engine.review_character(char, quality)

    def _post_word_review(self, engine):
        """
        Grades one completed word-practice item against the word's own SM-2
        schedule, independent of the per-character grading its characters
        separately received. Body: { "word": "你好", "quality": 0-5 }
        """
        data = self._json_body()
        if data is None:
            return None
        word, quality = data.get("word", ""), data.get("quality")
        if not word or quality is None:
            raise ValueError("Both 'word' and 'quality' are required.")
        return engine.review_word(word, quality)


if __name__ == "__main__":
    # Loopback by default, because the deployment this serves sits behind
    # Caddy (see the Caddyfile) on a public host: binding only 127.0.0.1 means
    # plain-HTTP port 8000 is reachable from the reverse proxy on the same
    # machine and from nowhere else, rather than being exposed to the internet
    # in parallel with the HTTPS Caddy serves. Set JUZI_BIND_HOST="" to bind
    # all interfaces instead, for LAN tablet access on a trusted network.
    bind_host = os.environ.get("JUZI_BIND_HOST", "127.0.0.1")
    server_address = (bind_host, PORT)
    # ThreadingHTTPServer, not HTTPServer: the plain version handles one
    # connection at a time, so a single slow connection (a request that
    # declares a large body and trickles it in) blocks every other request
    # for as long as it's held open. brain.json access is guarded by
    # JuziEngine.brain_lock so the resulting concurrency can't race on its
    # read-modify-write.
    httpd = http.server.ThreadingHTTPServer(server_address, JuziAPIHandler)
    display_host = bind_host or "localhost"
    print(f"JuziGenius Server running at http://{display_host}:{PORT}")
    if not mailer.is_configured():
        print("mail: JUZI_RESEND_API_KEY is not set -- emails will be printed here, "
              "not sent. See mailer.py.", flush=True)
    httpd.serve_forever()
