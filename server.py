import http.server
import json
import os
import re
import threading
import urllib.parse
from juzi_engine import JuziEngine

PORT = 8000

# Matches the /u/<slug>/... prefix used to route a request to one friend's
# own isolated brain.json instead of the shared default one (see
# get_engine_for_slug below). Anchored and length-floored so a malformed or
# short guess never reaches the filesystem as a path component.
USER_PREFIX_RE = re.compile(r"^/u/([A-Za-z0-9_-]{16,})(/.*)?$")

# No POST body may legitimately need more than this -- even a whole novel
# chapter pasted into Paste Text is a few hundred KB. Without a cap, do_POST
# reads exactly whatever Content-Length the client claims with no upper
# bound: a client can declare and send an arbitrarily large body (tested:
# an 84MB payload was accepted and fully processed) and the server buffers
# the whole thing in memory -- multiple times over, across the raw bytes,
# the decoded string, and the parsed JSON -- for no legitimate reason. This
# rejects oversized requests before reading the body at all.
MAX_BODY_SIZE = 2 * 1024 * 1024  # 2 MB

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
}

STROKE_DATA_PATH = "stroke_data.json"

# When true, the bare domain (no /u/<slug>/ prefix) serves nothing but the
# shared static assets -- no default single-user account, no API. Set via
# JUZI_REQUIRE_SLUG=1 on a public deployment so that anyone who visits the
# apex URL directly doesn't land on -- and get to read/write -- whoever's
# data happens to live in the top-level brain.json. Off by default so a
# local/LAN single-user install (the original use case, no /u/<slug>/ link
# involved at all) keeps working with no config.
REQUIRE_SLUG = os.environ.get("JUZI_REQUIRE_SLUG", "") == "1"

# The engine you get when you hit the server with no /u/<slug>/ prefix --
# your own single-user local instance, unchanged from before multi-user
# hosting existed. Unreachable over the web when REQUIRE_SLUG is set (see
# do_GET/do_POST); still used for local/LAN single-user runs.
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


def get_engine_for_slug(slug):
    """
    Resolves a /u/<slug>/ URL to that friend's own isolated JuziEngine (their
    own brain.json under users/<slug>/), caching instances across requests.

    Deliberately does NOT create users/<slug>/ on demand -- only
    create_user.py provisions accounts. If it auto-vivified a fresh (empty,
    unseeded) account for any request whose slug merely matches the format
    regex, a scanner hammering random /u/<guess>/ paths would litter the
    disk with junk directories, and "provisioned" would stop meaning
    anything. A slug that hasn't been created returns None (the caller 404s),
    same as any other guess.
    """
    with engines_lock:
        if slug in engines:
            return engines[slug]
        user_dir = os.path.join(USERS_DIR, slug)
        if not os.path.isdir(user_dir):
            return None
        engine = JuziEngine(brain_path=os.path.join(user_dir, "brain.json"))
        engines[slug] = engine
        return engine

# Vendored Hanzi Writer stroke data, loaded once on first use and held in
# memory. This is what makes handwriting work offline: without it the library
# fetches every character from cdn.jsdelivr.net as the user is asked to write
# it. Tracked in the repo (built by fetch_stroke_data.py), so a fresh clone
# has it. If it's ever missing, /api/strokes 404s and app.js falls back to
# the CDN rather than failing outright.
_stroke_data = None


def load_stroke_data():
    global _stroke_data
    if _stroke_data is None:
        if not os.path.exists(STROKE_DATA_PATH):
            print(
                f"Notice: {STROKE_DATA_PATH} not found -- handwriting will fall back to the CDN. "
                f"Run 'python3 fetch_stroke_data.py' to enable offline stroke data."
            )
            _stroke_data = {}
        else:
            with open(STROKE_DATA_PATH, "r", encoding="utf-8") as f:
                _stroke_data = json.load(f)
            print(f"Loaded offline stroke data for {len(_stroke_data)} characters.")
    return _stroke_data

class JuziAPIHandler(http.server.SimpleHTTPRequestHandler):
    # Without this, a connection that stops sending data mid-request (or
    # never finishes a declared body) blocks its handler thread forever --
    # harmless with ThreadingHTTPServer's own thread per connection, but a
    # cheap way for one bad connection to tie up resources indefinitely.
    # 30s is generous for a real client on the same LAN and short enough
    # that an abandoned/slow connection doesn't linger.
    timeout = 30

    def do_GET(self):
        path = urllib.parse.urlparse(self.path).path

        user_match = USER_PREFIX_RE.match(path)
        if user_match:
            engine = get_engine_for_slug(user_match.group(1))
            if engine is None:
                self.send_response(404)
                self.end_headers()
                return
            sub_path = user_match.group(2) or "/"
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
            # No default-account fallback on a public deployment: the app
            # itself and its API only work behind a real /u/<slug>/ link.
            # The bare domain gets a static landing page instead of the app,
            # so a stranger who wanders in learns nothing more than "ask
            # around" -- no login form, no hint at how accounts are made.
            # Shared static assets below (CSS/JS/images) still serve --
            # every /u/<slug>/ page references them by the same absolute
            # path, and they carry no personal data.
            if path in ("/", "/index.html"):
                self.path = "/landing.html"
                return super().do_GET()
            if path.startswith("/api/"):
                self.send_response(404)
                self.end_headers()
                return
        elif self._handle_api_get(path, default_engine):
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
        304 rather than a re-download. /api/strokes sets its own long immutable
        caching (stroke data for a character never changes) and is left alone.
        """
        if "Cache-Control" not in self._headers_buffer_names():
            path = urllib.parse.urlparse(self.path).path
            if not path.startswith("/api/"):
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
        get_engine_for_slug. Returns True if `path` was one of these routes
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
                if not saved_sentences and total_unlocked > 0:
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
                    "total_due_count": len(engine.get_due_characters(unlocked_chars)),
                    # Characters unlocked but held behind the daily intake cap
                    # (finding 13), so the badge can say "12 due, 60 waiting"
                    # rather than presenting the whole backlog as today's work.
                    "new_backlog": engine.new_character_backlog(unlocked_chars),
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
                entry = load_stroke_data().get(char) if char else None

                if entry is None:
                    self.send_response(404)
                    self.send_header("Content-Type", "application/json")
                    self.end_headers()
                    self.wfile.write(json.dumps({"error": "No vendored stroke data."}).encode("utf-8"))
                    return True

                payload = json.dumps(entry, ensure_ascii=False).encode("utf-8")
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

    def do_POST(self):
        path = urllib.parse.urlparse(self.path).path

        if self._csrf_check_failed():
            return

        user_match = USER_PREFIX_RE.match(path)
        if user_match:
            engine = get_engine_for_slug(user_match.group(1))
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
            # Same reasoning as do_GET: no default-account fallback publicly.
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
        get_engine_for_slug. Returns True if `path` was one of these routes
        (a response has already been sent), False otherwise so the caller
        can 404.
        """
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
        # Body: { "text": "<chinese>", "translation": "<optional english>" }
        # When translation is given, matching sentences are also saved to
        # the user's persistent pasted_sentences for future practice.
        if path == "/api/import":
            try:
                body = self._read_json_body_or_reject()
                if body is None:
                    return True
                data = json.loads(body)
                raw_text = data.get("text", "")
                translation_text = data.get("translation", "")

                # Call the local master dictionary import method on the engine
                result = engine.import_text_locally(raw_text, translation_text=translation_text)

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
                # due/relevant sentences in one go.
                result = engine.generate_fresh_session(count=3)

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

        return False

if __name__ == "__main__":
    # Empty string (default) binds all interfaces, same as always -- what
    # LAN-tablet/phone use needs. When this server sits behind Caddy (see
    # the Caddyfile) on a publicly reachable host, set JUZI_BIND_HOST=127.0.0.1
    # so plain-HTTP port 8000 is reachable only from Caddy's reverse proxy on
    # the same machine, not directly from the internet in parallel with it.
    bind_host = os.environ.get("JUZI_BIND_HOST", "")
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
