import http.server
import json
import os
import urllib.parse
from juzi_engine import JuziEngine

PORT = 8000

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

engine = JuziEngine()

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

                # Ensure existing sentences have char_metadata attached
                for s in saved_sentences:
                    if "char_metadata" not in s:
                        s["char_metadata"] = {}
                        for char in s.get("chinese", ""):
                            if char in unlocked_chars:
                                s["char_metadata"][char] = {
                                    "pinyin": unlocked_chars[char].get("pinyin", ""),
                                    "meaning": unlocked_chars[char].get("meaning", "")
                                }

                response_payload = {
                    "sentences": saved_sentences,
                    "total_unlocked_count": total_unlocked,
                    "total_due_count": len(engine.get_due_characters(unlocked_chars))
                }

                self.send_response(200)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.end_headers()
                self.wfile.write(json.dumps(response_payload, ensure_ascii=False).encode("utf-8"))
                return
            except Exception as e:
                self.send_response(500)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps({"error": str(e)}).encode("utf-8"))
                return

        # Suggests the highest-frequency compound words not yet added to the
        # user's vocabulary, for the "Suggest Words" modal tab.
        if path == "/api/suggestions":
            try:
                suggestions = engine.suggest_new_words(count=5)
                self.send_response(200)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.end_headers()
                self.wfile.write(json.dumps({"suggestions": suggestions}, ensure_ascii=False).encode("utf-8"))
                return
            except Exception as e:
                self.send_response(500)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps({"error": str(e)}).encode("utf-8"))
                return

        # Serves one character's stroke-order data out of the vendored
        # stroke_data.json, replacing Hanzi Writer's default per-character
        # fetch to cdn.jsdelivr.net. A 404 here is not an error: app.js reads
        # it as "not vendored" and falls back to the CDN.
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
                    return

                payload = json.dumps(entry, ensure_ascii=False).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Content-Length", str(len(payload)))
                # Stroke data for a character never changes; let the browser
                # keep it so repeat characters don't re-request every time.
                self.send_header("Cache-Control", "public, max-age=31536000, immutable")
                self.end_headers()
                self.wfile.write(payload)
                return
            except Exception as e:
                self.send_response(500)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps({"error": str(e)}).encode("utf-8"))
                return

        # Refuse to serve anything not explicitly whitelisted (blocks config.py,
        # brain.json, .git, hanzi_db.csv, etc. from being fetched over the network)
        if path not in ALLOWED_STATIC_PATHS:
            self.send_response(404)
            self.end_headers()
            return

        return super().do_GET()

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

        # Intercept POST requests for importing raw text/sentences locally.
        # Body: { "text": "<chinese>", "translation": "<optional english>" }
        # When translation is given, matching sentences are also saved to
        # the user's persistent pasted_sentences for future practice.
        if path == "/api/import":
            try:
                body = self._read_json_body_or_reject()
                if body is None:
                    return
                data = json.loads(body)
                raw_text = data.get("text", "")
                translation_text = data.get("translation", "")

                # Call the local master dictionary import method on the engine
                result = engine.import_text_locally(raw_text, translation_text=translation_text)

                self.send_response(200)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.end_headers()
                self.wfile.write(json.dumps(result, ensure_ascii=False).encode("utf-8"))
                return
            except Exception as e:
                self.send_response(500)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps({"error": str(e)}).encode("utf-8"))
                return

        # Generates a brand new batch of real HSK/Tatoeba example sentences,
        # replacing the saved bank. No body fields required.
        if path == "/api/session/generate":
            try:
                body = self._read_json_body_or_reject()
                if body is None:
                    return

                # 3 keeps each batch focused rather than exhausting
                # due/relevant sentences in one go.
                result = engine.generate_fresh_session(count=3)

                self.send_response(200)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.end_headers()
                self.wfile.write(json.dumps(result, ensure_ascii=False).encode("utf-8"))
                return
            except Exception as e:
                self.send_response(502)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps({"error": str(e)}).encode("utf-8"))
                return

        # Adds user-selected words from the "Suggest Words" tab to brain.json.
        # Body: { "words": ["谢谢", "再见", ...] }
        if path == "/api/suggestions/add":
            try:
                body = self._read_json_body_or_reject()
                if body is None:
                    return
                data = json.loads(body) if body else {}
                words = data.get("words", [])

                result = engine.add_words(words)

                self.send_response(200)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.end_headers()
                self.wfile.write(json.dumps(result, ensure_ascii=False).encode("utf-8"))
                return
            except Exception as e:
                self.send_response(500)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps({"error": str(e)}).encode("utf-8"))
                return

        # Grades one completed character quiz and advances its SM-2
        # scheduling fields (interval/factor/reps/last) in brain.json.
        # Body: { "char": "我", "quality": 0-5 }
        if path == "/api/character/review":
            try:
                body = self._read_json_body_or_reject()
                if body is None:
                    return
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
                return
            except ValueError as e:
                # Missing field or unknown character -- a client error, not a server error
                self.send_response(400)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps({"error": str(e)}).encode("utf-8"))
                return
            except Exception as e:
                self.send_response(500)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps({"error": str(e)}).encode("utf-8"))
                return

        self.send_response(404)
        self.end_headers()

if __name__ == "__main__":
    server_address = ("", PORT)
    # ThreadingHTTPServer, not HTTPServer: the plain version handles one
    # connection at a time on its single main thread, so one slow or
    # malicious connection (e.g. a request that declares a large body and
    # trickles it in slowly) blocks every other request -- including the
    # tablet/phone client this server is built to serve -- for as long as
    # that connection is held open. Each connection now gets its own thread.
    # brain.json access is guarded by JuziEngine.brain_lock (see there) so
    # concurrent requests can't race on its read-modify-write.
    httpd = http.server.ThreadingHTTPServer(server_address, JuziAPIHandler)
    print(f"JuziGenius Server running locally at http://localhost:{PORT}")
    httpd.serve_forever()
