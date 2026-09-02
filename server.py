import hashlib
import http.cookies
import http.server
import json
import os
import re
import threading
import urllib.parse
import accounts
import auth
import invites
from juzi_engine import JuziEngine
from seed_brain import SIZE_CHOICES, TIER_INFO, empty_brain
from seed_brain import build_brain as seed_build_brain

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
}

STROKE_DATA_PATH = "stroke_data.json"
STROKE_INDEX_PATH = "stroke_data.index.json"

# Pre-generated sentence audio (see build_speech_audio.py). Content-addressed
# by sha256(sentence text), sharded by the hash's first two hex characters,
# so no in-memory index is needed the way stroke_data.index.json is -- the
# path is derived, not looked up. Only covers SENTENCE_SOURCE_FILES; a
# pasted sentence (or anything else not in that corpus at build time) simply
# has no file here, and a 404 is the intended signal for app.js to fall back
# to the browser's Web Speech API -- not an error to fix on this end.
SPEECH_AUDIO_DIR = "speech_audio"
SPEECH_VOICES = {"chaowen", "huayan"}


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

# JuziGenius is a hosted service. The bare domain is a landing page (and will
# become the login screen when real accounts land); practice happens behind a
# /u/<slug>/ link, against that account's own brain.json. There is no
# single-user mode reachable over HTTP any more: previously the apex URL
# served a default account straight from the top-level brain.json, so anyone
# who found the domain landed on -- and could read and write -- whoever's data
# happened to be there.
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

# Guards users/accounts.json and users/invite_codes.json across concurrent
# requests -- signup reads and writes both files together (redeem a code,
# create an account), and this makes that pair atomic with respect to two
# simultaneous signups, the same reason engines_lock exists for brand-new
# slugs. Login and session-cookie checks only read, but take the same lock
# so they can never observe a signup's writes half-applied.
ACCOUNTS_LOCK = threading.Lock()


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
# Finding 20: this file is 29.4 MB, and parsing it into one dict cost 137 MB
# resident (195 MB peak) held for the process lifetime -- to serve what are
# only ever single-key lookups. fetch_stroke_data.py now writes a byte-offset
# index beside it, so a character's stroke data is read as one ~3 KB span and
# the rest is never materialised. The span is already JSON, so it goes to the
# socket verbatim: no parse on the way in, no re-encode on the way out.
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


def speech_audio_path(voice, chinese):
    """
    The on-disk path build_speech_audio.py would have written this sentence
    to for this voice, or None if `voice` isn't one we generate. Derived,
    not looked up -- see SPEECH_AUDIO_DIR's comment. Existence is checked by
    the caller; this just computes where to look.
    """
    if voice not in SPEECH_VOICES:
        return None
    h = hashlib.sha256(chinese.encode("utf-8")).hexdigest()
    return os.path.join(SPEECH_AUDIO_DIR, voice, h[:2], f"{h}.mp3")


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
                self.send_response(404)
                self.end_headers()
                return
            sub_path = user_match.group(2) or "/"
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
            self.send_response(404)
            self.end_headers()
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
            engine = get_engine_for_id(user_id) if user_id else None
            if engine is not None and self._handle_api_get(path, engine):
                return
            if path in ("/", "/index.html"):
                # "/" can serve either file depending on login state, but a
                # browser's conditional-GET cache doesn't know that -- it
                # caches by URL, and SimpleHTTPRequestHandler's 304 check
                # only compares the file it's about to serve against
                # whatever If-Modified-Since the browser learned from
                # *whichever file it fetched last time*. A visitor who saw
                # landing.html anonymously, then logged in and requested "/"
                # again, got a 304 back (landing.html's mtime happens to be
                # newer than index.html's) and their browser silently kept
                # showing the stale cached landing page instead of ever
                # re-requesting the real app -- indistinguishable from
                # "signup succeeded but nothing loads". Dropping the
                # conditional headers here forces a real 200 every time, so
                # the file that actually matches this response's login state
                # is what gets shown.
                self._strip_conditional_headers()
                if engine is not None:
                    self.path = "/index.html"
                    return super().do_GET()
                self.path = "/landing.html"
                return super().do_GET()
            if path.startswith("/api/"):
                self.send_response(404)
                self.end_headers()
                return
        elif self._handle_api_get(path, default_engine):
            return

        if path == "/manifest.json":
            self._send_manifest("/")
            return

        # Refuse to serve anything not explicitly whitelisted (blocks config.py,
        # brain.json, .git, hanzi_db.csv, etc. from being fetched over the network)
        if path not in ALLOWED_STATIC_PATHS:
            self.send_response(404)
            self.end_headers()
            return

        return super().do_GET()

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
        /api/strokes sets a long immutable one, since a character's stroke
        data never changes. Finding 23: that check compared the capitalised
        "Cache-Control" against _headers_buffer_names(), which returns
        lowercased names, so it never matched and a second, contradictory
        `no-cache` was appended to every response that set one. A stray
        `path.startswith("/api/")` test hid this at the apex, but a hosted
        request path begins `/u/<slug>/`, so for every real account
        /api/strokes went out with both headers -- and `no-cache` wins when a
        browser combines them, quietly defeating a year of immutable caching
        on the one endpoint that most needs it. That path test is gone: it was
        a cruder second guess at the same rule, and it left the other API
        routes with no cache header at all, exposing per-account state to
        heuristic freshness caching.
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

    def _handle_api_get(self, path, engine):
        """
        Handles the API GET routes against a resolved `engine` -- either the
        default single-user one or a specific friend's, via
        get_engine_for_id. Returns True if `path` was one of these routes
        (a response has already been sent), False otherwise so the caller
        can fall through to static-file serving or 404.
        """
        # Intercept API requests for offline session sentence loading
        if path == "/api/session":
            try:
                # Load saved sentences and metadata directly from local brain.json
                brain_data = {"unlocked_chars": {}, "sentences": []}
                with engine.brain_lock:
                    if os.path.exists(engine.brain_path):
                        with open(engine.brain_path, "r", encoding="utf-8") as f:
                            brain_data = json.load(f)

                unlocked_chars = brain_data.get("unlocked_chars", {})
                saved_sentences = brain_data.get("sentences", [])
                total_unlocked = len(unlocked_chars)

                # Bootstrap: populate an initial batch from the local HSK corpus if the
                # bank is empty but characters exist, so first-run works with zero
                # configuration.
                #
                # Also rebuild a character-only bank that is narrower than the
                # unlocked pool. The saved bank is otherwise replaced only on
                # an explicit "Get Sentences", which is right for sentences
                # and strands people in character practice: a Tier 1 account
                # seeded before the batch covered the pool holds three of its
                # five characters and loops them forever, while the other two
                # sit in the "Due" badge unreachable. Same for a character
                # unlocked mid-phase through Suggest Characters or Paste
                # Text. beginner_bank_is_stale asks for exactly the size a
                # fresh batch would be, so this settles after one rebuild
                # rather than rewriting brain.json on every page load.
                if total_unlocked > 0 and (not saved_sentences
                                           or engine.beginner_bank_is_stale(brain_data)):
                    try:
                        fresh = engine.generate_fresh_session(count=3)
                        saved_sentences = fresh["sentences"]
                    except Exception as gen_err:
                        print(f"Session bootstrap notice: {gen_err}")

                # Backfill per-character hint data on sentences saved before it
                # existed. char_pinyin (per-POSITION, context-aware -- see
                # finding 10) is newer than char_metadata, so a bank written by
                # an older build has the latter but not the former; rebuild
                # whenever either is missing rather than only on char_metadata.
                # Computed for the response only, not written back: the saved
                # bank is rewritten wholesale on the next generated batch, and
                # this GET deliberately holds no write lock.
                for s in saved_sentences:
                    if "char_metadata" not in s or "char_pinyin" not in s:
                        engine.attach_char_data(s, unlocked_chars)

                response_payload = {
                    "sentences": saved_sentences,
                    "total_unlocked_count": total_unlocked,
                    "total_due_count": engine.total_due_count(brain_data),
                    # Characters unlocked but held behind the daily intake cap
                    # (finding 13), so the badge can say "12 due, 60 waiting"
                    # rather than presenting the whole backlog as today's work.
                    "new_backlog": engine.new_character_backlog(unlocked_chars),
                    # False only for a brand-new create_user.py account that
                    # hasn't picked a starting tier yet -- app.js shows the
                    # tier picker instead of the normal session in that case.
                    # Missing key (every brain predating the picker) defaults
                    # True so existing installs are never re-prompted.
                    "onboarded": bool(brain_data.get("onboarded", True)),
                }

                self.send_response(200)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.end_headers()
                self.wfile.write(json.dumps(response_payload, ensure_ascii=False).encode("utf-8"))
                return True
            except Exception as e:
                self.send_response(500)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps({"error": str(e)}).encode("utf-8"))
                return True

        # Suggests the highest-frequency compound words not yet added to the
        # user's vocabulary, for the "Suggest Words" modal tab.
        if path == "/api/suggestions":
            try:
                suggestions = engine.suggest_new_words(count=5)
                self.send_response(200)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.end_headers()
                self.wfile.write(json.dumps({"suggestions": suggestions}, ensure_ascii=False).encode("utf-8"))
                return True
            except Exception as e:
                self.send_response(500)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps({"error": str(e)}).encode("utf-8"))
                return True

        # The most useful characters not yet unlocked, for the "Suggest
        # Characters" modal tab. The words equivalent has existed for a while;
        # this answers the same question for the actual practice unit, which
        # is what the app is built to teach.
        if path == "/api/characters/suggestions":
            try:
                suggestions = engine.suggest_new_characters(count=8)
                self.send_response(200)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.end_headers()
                self.wfile.write(json.dumps({"suggestions": suggestions},
                                            ensure_ascii=False).encode("utf-8"))
                return True
            except Exception as e:
                self._send_json_error(500, str(e))
                return True

        # Everything the progress view needs, in one request.
        if path == "/api/progress":
            try:
                payload = engine.progress_summary()
                self.send_response(200)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.end_headers()
                self.wfile.write(json.dumps(payload, ensure_ascii=False).encode("utf-8"))
                return True
            except Exception as e:
                self._send_json_error(500, str(e))
                return True

        # The Settings panel's payload: the stored values plus the bounds and
        # today's counts that make the number mean something.
        if path == "/api/settings":
            try:
                payload = engine.read_settings()
                self.send_response(200)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.end_headers()
                self.wfile.write(json.dumps(payload, ensure_ascii=False).encode("utf-8"))
                return True
            except Exception as e:
                self._send_json_error(500, str(e))
                return True

        # The starting-tier catalog shown to a friend who hasn't onboarded yet
        # (see /api/onboarding/seed below and TIER_INFO in seed_brain.py).
        # Static, shared reference data -- `engine` is unused here, same as
        # /api/strokes below.
        if path == "/api/onboarding/tiers":
            try:
                tiers = [
                    {"size": size, **TIER_INFO[size]}
                    for size in SIZE_CHOICES
                ]
                self.send_response(200)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.end_headers()
                self.wfile.write(json.dumps({"tiers": tiers}, ensure_ascii=False).encode("utf-8"))
                return True
            except Exception as e:
                self._send_json_error(500, str(e))
                return True

        # Serves one character's stroke-order data out of the vendored
        # stroke_data.json, replacing Hanzi Writer's default per-character
        # fetch to cdn.jsdelivr.net. A 404 here is not an error: app.js reads
        # it as "not vendored" and falls back to the CDN. Shared reference
        # data, not per-user, so `engine` is unused here -- every account
        # reads the same vendored stroke set.
        if path == "/api/strokes":
            try:
                params = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
                char = (params.get("char") or [""])[0]
                # Already-encoded JSON straight off disk (see
                # stroke_entry_bytes) -- nothing to parse or re-serialise.
                payload = stroke_entry_bytes(char) if char else None

                if payload is None:
                    self.send_response(404)
                    self.send_header("Content-Type", "application/json")
                    self.end_headers()
                    self.wfile.write(json.dumps({"error": "No vendored stroke data."}).encode("utf-8"))
                    return True

                self.send_response(200)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Content-Length", str(len(payload)))
                # Stroke data for a character never changes; let the browser
                # keep it so repeat characters don't re-request every time.
                self.send_header("Cache-Control", "public, max-age=31536000, immutable")
                self.end_headers()
                self.wfile.write(payload)
                return True
            except Exception as e:
                self.send_response(500)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps({"error": str(e)}).encode("utf-8"))
                return True

        # Serves one sentence's pre-generated audio (build_speech_audio.py),
        # replacing the browser's Web Speech API for anything in the local
        # corpus. A 404 here is not an error -- it's how app.js knows to fall
        # back to speechSynthesis, which is the intended (and only) path for
        # a user's own pasted sentences, since those don't exist at build
        # time. Shared reference data, not per-user, so `engine` is unused,
        # same as /api/strokes above.
        if path == "/api/speech":
            try:
                params = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
                text = (params.get("text") or [""])[0]
                voice = (params.get("voice") or [""])[0]
                file_path = speech_audio_path(voice, text) if text else None

                if file_path is None or not os.path.exists(file_path):
                    self.send_response(404)
                    self.send_header("Content-Type", "application/json")
                    self.end_headers()
                    self.wfile.write(json.dumps({"error": "No pre-generated audio for this sentence/voice."}).encode("utf-8"))
                    return True

                # Opened per request, like /api/strokes, rather than through a
                # shared handle -- a seek/read on a shared file object isn't
                # thread-safe and ThreadingHTTPServer makes concurrent
                # requests real.
                with open(file_path, "rb") as f:
                    payload = f.read()

                self.send_response(200)
                self.send_header("Content-Type", "audio/mpeg")
                self.send_header("Content-Length", str(len(payload)))
                # This exact (text, voice) pair's audio never changes -- the
                # hash is of the text itself, so any edit produces a
                # different path rather than a stale file at this one.
                self.send_header("Cache-Control", "public, max-age=31536000, immutable")
                self.end_headers()
                self.wfile.write(payload)
                return True
            except Exception as e:
                self.send_response(500)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps({"error": str(e)}).encode("utf-8"))
                return True

        return False

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
        if not content_type.split(";")[0].strip().lower() == "application/json":
            self.send_response(403)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({"error": "Content-Type must be application/json."}).encode("utf-8"))
            return True

        origin = self.headers.get("Origin")
        if origin is not None:
            host = self.headers.get("Host", "")
            origin_host = urllib.parse.urlparse(origin).netloc
            if origin_host != host:
                self.send_response(403)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps({"error": "Cross-origin request rejected."}).encode("utf-8"))
                return True

        return False

    def _send_manifest(self, start_url):
        payload = json.dumps(build_manifest(start_url),
                             ensure_ascii=False).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/manifest+json; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def _send_json_error(self, status, message):
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps({"error": message}).encode("utf-8"))

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

    def _set_session_cookie(self, user_id, session_version):
        cookie_value = auth.make_session_cookie(user_id, session_version)
        # HttpOnly: JavaScript can't read it, so it isn't a target for an XSS
        # payload to exfiltrate. SameSite=Lax: the browser won't attach it to
        # a cross-site POST at all, which is a second, independent layer on
        # top of the existing CSRF check below. Secure per COOKIE_SECURE --
        # see its definition for why this is a separate flag from REQUIRE_SLUG.
        flags = "HttpOnly; SameSite=Lax; Path=/"
        if COOKIE_SECURE:
            flags += "; Secure"
        self.send_header(
            "Set-Cookie",
            f"{auth.SESSION_COOKIE_NAME}={cookie_value}; Max-Age={auth.SESSION_TTL_SECONDS}; {flags}"
        )

    def _clear_session_cookie(self):
        self.send_header("Set-Cookie", f"{auth.SESSION_COOKIE_NAME}=; Max-Age=0; Path=/")

    def _handle_signup(self):
        """
        POST /api/signup. Body: {"username", "password", "invite_code"}.
        Creates a brand new real-login account (same empty_brain() shape
        every account-creation path uses) and logs it straight in, so the
        redirect to / lands on the existing onboarding tier picker with no
        extra step -- a fresh account's brain.json already has
        "onboarded": false, and app.js's fetchNewSession already knows what
        to do with that.
        """
        try:
            body = self._read_json_body_or_reject()
            if body is None:
                return
            data = json.loads(body)
            username = (data.get("username") or "").strip()
            password = data.get("password") or ""
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
                except ValueError as bad:
                    self._send_json_error(400, str(bad))
                    return

                user_id = accounts.create_account(accounts_data, username, password)
                invites.mark_invite_code_used(invite_codes, invite_code, username.lower())
                accounts.save_accounts(accounts_data)
                invites.save_invite_codes(invite_codes)

            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            # A brand new account always starts at INITIAL_SESSION_VERSION --
            # no need to re-read the record just written above.
            self._set_session_cookie(user_id, accounts.INITIAL_SESSION_VERSION)
            self.end_headers()
            self.wfile.write(json.dumps({"ok": True}).encode("utf-8"))
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

            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self._set_session_cookie(user_id, session_version)
            self.end_headers()
            self.wfile.write(json.dumps({"ok": True}).encode("utf-8"))
        except Exception as e:
            self._send_json_error(500, str(e))

    def _handle_logout(self):
        # No body or existing session required -- clearing a cookie that may
        # not even be valid is harmless, so a stray call here just no-ops.
        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self._clear_session_cookie()
        self.end_headers()
        self.wfile.write(json.dumps({"ok": True}).encode("utf-8"))

    def do_POST(self):
        path = urllib.parse.urlparse(self.path).path

        if self._csrf_check_failed():
            return

        # Auth endpoints are reachable at the root regardless of
        # REQUIRE_SLUG -- they're how an anonymous visitor becomes a
        # session-cookie-authenticated one in the first place, so they can't
        # themselves require a session or a slug.
        if path == "/api/signup":
            self._handle_signup()
            return
        if path == "/api/login":
            self._handle_login()
            return
        if path == "/api/logout":
            self._handle_logout()
            return

        user_match = USER_PREFIX_RE.match(path)
        if user_match:
            engine = get_engine_for_id(user_match.group(1))
            if engine is None:
                self.send_response(404)
                self.end_headers()
                return
            sub_path = user_match.group(2) or "/"
            if self._handle_api_post(sub_path, engine):
                return
            self.send_response(404)
            self.end_headers()
            return

        if REQUIRE_SLUG:
            # Same idea as do_GET: no /u/<slug>/ prefix, but a valid
            # real-login session cookie still resolves to that account's
            # own engine.
            user_id, _username = self._resolve_session_account()
            engine = get_engine_for_id(user_id) if user_id else None
            if engine is not None and self._handle_api_post(path, engine):
                return
            self.send_response(404)
            self.end_headers()
            return

        if self._handle_api_post(path, default_engine):
            return

        self.send_response(404)
        self.end_headers()

    def _handle_api_post(self, path, engine):
        """
        Handles the API POST routes against a resolved `engine` -- either the
        default single-user one or a specific friend's, via
        get_engine_for_id. Returns True if `path` was one of these routes
        (a response has already been sent), False otherwise so the caller
        can 404.
        """
        # First-run tier choice: a friend picks their own starting pool from
        # the tier picker app.js shows instead of the operator choosing a
        # --size for them at create_user.py time. Body: { "size": 50 }.
        # Only works once -- an account with characters already unlocked, or
        # already marked onboarded, is left untouched (409), so this can't be
        # replayed to wipe out real progress later. Deliberately reads/writes
        # brain.json directly rather than through a JuziEngine method, same
        # as /api/session's GET handler above.
        if path == "/api/onboarding/seed":
            try:
                body = self._read_json_body_or_reject()
                if body is None:
                    return True
                size = json.loads(body).get("size")
                if size not in SIZE_CHOICES:
                    self._send_json_error(400, f"'size' must be one of {list(SIZE_CHOICES)}.")
                    return True

                with engine.brain_lock:
                    brain_data = {}
                    if os.path.exists(engine.brain_path):
                        with open(engine.brain_path, "r", encoding="utf-8") as f:
                            brain_data = json.load(f)

                    already_onboarded = brain_data.get("onboarded", True)
                    has_chars = bool(brain_data.get("unlocked_chars"))
                    if already_onboarded or has_chars:
                        self._send_json_error(409, "This account has already been set up.")
                        return True

                    master = engine.load_master_dictionary()
                    new_brain = seed_build_brain(size, master)
                    with open(engine.brain_path, "w", encoding="utf-8") as f:
                        json.dump(new_brain, f, ensure_ascii=False, indent=4)

                self.send_response(200)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.end_headers()
                self.wfile.write(json.dumps({
                    "size": size,
                    "name": TIER_INFO[size]["name"],
                    "total_unlocked_count": len(new_brain["unlocked_chars"]),
                }, ensure_ascii=False).encode("utf-8"))
                return True
            except Exception as e:
                self._send_json_error(500, str(e))
                return True

        # Wipes the account back to a brand new one -- everything unlocked,
        # every SM-2 schedule, every completed sentence and every personally
        # pasted sentence, followed by the tier picker on the next session
        # fetch. Body: { "confirm": "RESET" }.
        #
        # This is the account owner's own escape hatch (the Start Over tab in
        # the progress view), for someone who wants to begin again from a
        # clean slate rather than live with a pool they picked wrong or a
        # review backlog they have given up on. Without it the only way back
        # was to ask the operator to run reset_user.py.
        #
        # Note what this deliberately gives up. /api/onboarding/seed is
        # one-shot precisely so that holding the link cannot wipe an
        # account's progress; this endpoint hands that capability back, and
        # the link is the only credential there is. That is inherent in the
        # feature -- a self-service reset is a self-service reset -- and it
        # widens nothing that link-holding did not already permit, since
        # anyone with the link can already grade characters wrongly or import
        # junk. The protections that remain are the CSRF pair on every POST
        # (so another site cannot trigger this in a logged-in browser), the
        # explicit confirmation token below, and the two-step confirmation in
        # the UI.
        #
        # RESET_CONFIRMATION is required in the body so that no bare,
        # bodyless POST to this path can destroy an account: a request has to
        # say what it is doing, not merely arrive at the right URL.
        if path == "/api/account/reset":
            try:
                body = self._read_json_body_or_reject()
                if body is None:
                    return True
                if json.loads(body).get("confirm") != RESET_CONFIRMATION:
                    self._send_json_error(
                        400, f"Reset requires \"confirm\": \"{RESET_CONFIRMATION}\".")
                    return True

                with engine.brain_lock:
                    brain_data = {}
                    if os.path.exists(engine.brain_path):
                        with open(engine.brain_path, "r", encoding="utf-8") as f:
                            brain_data = json.load(f)

                    # Counted before the overwrite so the response can report
                    # what was actually destroyed, rather than what the client
                    # last happened to render.
                    erased = {
                        "unlocked_chars": len(brain_data.get("unlocked_chars", {}) or {}),
                        "unlocked_words": len(brain_data.get("unlocked_words", {}) or {}),
                        "completed_sentences": len(brain_data.get("completed_sentences", {}) or {}),
                        "pasted_sentences": len(brain_data.get("pasted_sentences", []) or []),
                    }

                    with open(engine.brain_path, "w", encoding="utf-8") as f:
                        json.dump(empty_brain(), f, ensure_ascii=False, indent=4)

                # Logged because it is irreversible and someone will ask what
                # happened to their progress. The account is not named: the
                # slug is the credential and does not belong in a log (see
                # log_message and finding 21), so this records that a reset
                # happened and how much it took, not whose it was.
                #
                # flush=True because stdout is block-buffered whenever it is
                # not a terminal -- which is every real deployment, systemd
                # included. Without it this line sits in the buffer while the
                # access log (stderr, unbuffered) races ahead, so the one
                # event worth finding in the journal arrives late, out of
                # order, or not at all if the process is killed.
                print(f"Account reset on request: erased {erased['unlocked_chars']} characters, "
                      f"{erased['completed_sentences']} completed sentences, "
                      f"{erased['pasted_sentences']} saved sentences.", flush=True)

                self.send_response(200)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.end_headers()
                self.wfile.write(json.dumps({"reset": True, "erased": erased},
                                            ensure_ascii=False).encode("utf-8"))
                return True
            except Exception as e:
                self._send_json_error(500, str(e))
                return True

        # Saves the Settings panel. Body: { "daily_new_limit": 15 }.
        # update_settings ignores any key it doesn't understand, so this
        # can't be used to write arbitrary data into brain.json, and raises
        # ValueError on an out-of-range value rather than clamping -- someone
        # who types 500 should be told the cap, not left believing they set it.
        if path == "/api/settings":
            try:
                body = self._read_json_body_or_reject()
                if body is None:
                    return True
                values = json.loads(body)
                if not isinstance(values, dict):
                    self._send_json_error(400, "Body must be a JSON object.")
                    return True
                try:
                    payload = engine.update_settings(values)
                except ValueError as bad:
                    self._send_json_error(400, str(bad))
                    return True

                self.send_response(200)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.end_headers()
                self.wfile.write(json.dumps(payload, ensure_ascii=False).encode("utf-8"))
                return True
            except Exception as e:
                self._send_json_error(500, str(e))
                return True

        # Unlocks characters chosen in the Suggest Characters tab.
        # Body: { "chars": ["\u662f", "\u4eba", ...] }
        if path == "/api/characters/add":
            try:
                body = self._read_json_body_or_reject()
                if body is None:
                    return True
                chars = json.loads(body).get("chars", [])
                if not isinstance(chars, list):
                    self._send_json_error(400, "'chars' must be a list.")
                    return True
                result = engine.add_characters(chars)
                self.send_response(200)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.end_headers()
                self.wfile.write(json.dumps(result, ensure_ascii=False).encode("utf-8"))
                return True
            except Exception as e:
                self._send_json_error(500, str(e))
                return True

        # Records that a sentence was written all the way through, so batches
        # stop re-serving what was just practiced (finding 12).
        # Body: { "chinese": "..." }
        if path == "/api/sentence/complete":
            try:
                body = self._read_json_body_or_reject()
                if body is None:
                    return True
                chinese = json.loads(body).get("chinese", "")
                result = engine.record_sentence_completion(chinese)
                self.send_response(200)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.end_headers()
                self.wfile.write(json.dumps(result, ensure_ascii=False).encode("utf-8"))
                return True
            except Exception as e:
                self._send_json_error(500, str(e))
                return True

        # Intercept POST requests for importing raw text/sentences locally.
        # Body: { "text": "<chinese and english, combined>" }
        # import_text_locally auto-detects Chinese/English pairs within the
        # single blob and saves what it finds to the user's persistent
        # pasted_sentences for future practice.
        if path == "/api/import":
            try:
                body = self._read_json_body_or_reject()
                if body is None:
                    return True
                data = json.loads(body)
                raw_text = data.get("text", "")

                # Call the local master dictionary import method on the engine
                result = engine.import_text_locally(raw_text)

                self.send_response(200)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.end_headers()
                self.wfile.write(json.dumps(result, ensure_ascii=False).encode("utf-8"))
                return True
            except Exception as e:
                self.send_response(500)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps({"error": str(e)}).encode("utf-8"))
                return True

        # Generates a brand new batch of real HSK/Tatoeba example sentences,
        # replacing the saved bank. No body fields required.
        if path == "/api/session/generate":
            try:
                body = self._read_json_body_or_reject()
                if body is None:
                    return True

                # 3 keeps each batch focused rather than exhausting
                # due/relevant sentences in one go. styles={"sentences"}
                # overrides the account's general study-styles setting: this
                # button is an explicit "give me sentences" request, not the
                # general session bootstrap the toggle is meant to govern
                # (see generate_fresh_session).
                result = engine.generate_fresh_session(count=3, styles={"sentences"})

                self.send_response(200)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.end_headers()
                self.wfile.write(json.dumps(result, ensure_ascii=False).encode("utf-8"))
                return True
            except Exception as e:
                self.send_response(502)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps({"error": str(e)}).encode("utf-8"))
                return True

        # Adds user-selected words from the "Suggest Words" tab to brain.json.
        # Body: { "words": ["谢谢", "再见", ...] }
        if path == "/api/suggestions/add":
            try:
                body = self._read_json_body_or_reject()
                if body is None:
                    return True
                data = json.loads(body) if body else {}
                words = data.get("words", [])

                result = engine.add_words(words)

                self.send_response(200)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.end_headers()
                self.wfile.write(json.dumps(result, ensure_ascii=False).encode("utf-8"))
                return True
            except Exception as e:
                self.send_response(500)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps({"error": str(e)}).encode("utf-8"))
                return True

        # Grades one completed character quiz and advances its SM-2
        # scheduling fields (interval/factor/reps/last) in brain.json.
        # Body: { "char": "我", "quality": 0-5 }
        if path == "/api/character/review":
            try:
                body = self._read_json_body_or_reject()
                if body is None:
                    return True
                data = json.loads(body) if body else {}

                char = data.get("char", "")
                quality = data.get("quality")

                if not char or quality is None:
                    raise ValueError("Both 'char' and 'quality' are required.")

                result = engine.review_character(char, quality)

                self.send_response(200)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.end_headers()
                self.wfile.write(json.dumps(result, ensure_ascii=False).encode("utf-8"))
                return True
            except ValueError as e:
                # Missing field or unknown character -- a client error, not a server error
                self.send_response(400)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps({"error": str(e)}).encode("utf-8"))
                return True
            except Exception as e:
                self.send_response(500)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps({"error": str(e)}).encode("utf-8"))
                return True

        # Grades one completed word-practice item and advances its own
        # SM-2 scheduling fields, independent of any per-character grading
        # the same item's characters separately received via
        # /api/character/review. Body: { "word": "你好", "quality": 0-5 }
        if path == "/api/word/review":
            try:
                body = self._read_json_body_or_reject()
                if body is None:
                    return True
                data = json.loads(body) if body else {}

                word = data.get("word", "")
                quality = data.get("quality")

                if not word or quality is None:
                    raise ValueError("Both 'word' and 'quality' are required.")

                result = engine.review_word(word, quality)

                self.send_response(200)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.end_headers()
                self.wfile.write(json.dumps(result, ensure_ascii=False).encode("utf-8"))
                return True
            except ValueError as e:
                self.send_response(400)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps({"error": str(e)}).encode("utf-8"))
                return True
            except Exception as e:
                self.send_response(500)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps({"error": str(e)}).encode("utf-8"))
                return True

        return False

if __name__ == "__main__":
    # Loopback by default, because the deployment this serves sits behind
    # Caddy (see the Caddyfile) on a public host: binding only 127.0.0.1 means
    # plain-HTTP port 8000 is reachable from the reverse proxy on the same
    # machine and from nowhere else, rather than being exposed to the internet
    # in parallel with the HTTPS Caddy serves. This used to default to all
    # interfaces for LAN tablet access over plain HTTP, which is exactly the
    # arrangement a hosted deployment should not have. Set JUZI_BIND_HOST=""
    # explicitly to go back to all interfaces.
    bind_host = os.environ.get("JUZI_BIND_HOST", "127.0.0.1")
    server_address = (bind_host, PORT)
    # ThreadingHTTPServer, not HTTPServer: the plain version handles one
    # connection at a time on its single main thread, so one slow or
    # malicious connection (e.g. a request that declares a large body and
    # trickles it in slowly) blocks every other request -- including the
    # tablet/phone client this server is built to serve -- for as long as
    # that connection is held open. Each connection now gets its own thread.
    # brain.json access is guarded by JuziEngine.brain_lock (see there) so
    # concurrent requests can't race on its read-modify-write.
    httpd = http.server.ThreadingHTTPServer(server_address, JuziAPIHandler)
    display_host = bind_host or "localhost"
    print(f"JuziGenius Server running at http://{display_host}:{PORT}")
    httpd.serve_forever()
