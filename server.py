import http.server
import json
import os
import urllib.parse
from juzi_engine import JuziEngine

PORT = 8000

# Only these paths may ever be served as static files. This is a network-facing
# server (bound to all interfaces so it's reachable from a tablet on the same
# LAN), and SimpleHTTPRequestHandler's default behavior serves ANY file under
# the working directory by path -- which would expose config.py (API key),
# brain.json (personal SRS data), and other source files to anyone on the
# network. Everything not explicitly listed here gets a 404.
ALLOWED_STATIC_PATHS = {"/", "/index.html", "/style.css", "/app.js", "/mascot.png"}

engine = JuziEngine()

class JuziAPIHandler(http.server.SimpleHTTPRequestHandler):
    def do_GET(self):
        path = urllib.parse.urlparse(self.path).path

        # Intercept API requests for offline session sentence loading
        if path == "/api/session":
            try:
                # Load saved sentences and metadata directly from local brain.json
                brain_data = {"unlocked_chars": {}, "sentences": []}
                if os.path.exists(engine.brain_path):
                    with open(engine.brain_path, "r", encoding="utf-8") as f:
                        brain_data = json.load(f)

                unlocked_chars = brain_data.get("unlocked_chars", {})
                saved_sentences = brain_data.get("sentences", [])
                total_unlocked = len(unlocked_chars)

                # Bootstrap: populate an initial batch from the local HSK corpus if the
                # bank is empty but characters exist. Uses the no-key local fallback
                # rather than AI, so first-run works with zero configuration.
                if not saved_sentences and total_unlocked > 0:
                    try:
                        fresh = engine.generate_fresh_session(count=3, source="hsk")
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
                    "total_unlocked_count": total_unlocked
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

        # Reports which AI providers exist and whether a server-side key is
        # already configured for them, so the frontend knows which providers
        # still need a client-pasted API key.
        if path == "/api/providers":
            try:
                self.send_response(200)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.end_headers()
                self.wfile.write(json.dumps({"providers": engine.list_providers()}, ensure_ascii=False).encode("utf-8"))
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

        # Refuse to serve anything not explicitly whitelisted (blocks config.py,
        # brain.json, .git, hanzi_db.csv, etc. from being fetched over the network)
        if path not in ALLOWED_STATIC_PATHS:
            self.send_response(404)
            self.end_headers()
            return

        return super().do_GET()

    def do_POST(self):
        path = urllib.parse.urlparse(self.path).path

        # Intercept POST requests for importing raw text/sentences locally
        if path == "/api/import":
            try:
                content_length = int(self.headers.get('Content-Length', 0))
                body = self.rfile.read(content_length).decode('utf-8')
                data = json.loads(body)
                raw_text = data.get("text", "")

                # Call the local master dictionary import method on the engine
                result = engine.import_text_locally(raw_text)

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

        # Generates a brand new sentence batch, replacing the saved bank.
        # Body: { "source": "ai" | "hsk", "provider": "gemini"|"claude"|"openai"|"grok",
        #         "api_key": "<optional, client-held -- never written to disk>" }
        if path == "/api/session/generate":
            try:
                content_length = int(self.headers.get('Content-Length', 0))
                body = self.rfile.read(content_length).decode('utf-8') if content_length else "{}"
                data = json.loads(body) if body else {}

                source = data.get("source", "ai")
                provider = data.get("provider", "gemini")
                api_key = data.get("api_key") or None

                result = engine.generate_fresh_session(count=5, source=source, provider=provider, api_key=api_key)

                self.send_response(200)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.end_headers()
                self.wfile.write(json.dumps(result, ensure_ascii=False).encode("utf-8"))
                return
            except ValueError as e:
                # Missing/invalid API key -- a client error, not a server error
                self.send_response(400)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps({"error": str(e)}).encode("utf-8"))
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
                content_length = int(self.headers.get('Content-Length', 0))
                body = self.rfile.read(content_length).decode('utf-8') if content_length else "{}"
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

        self.send_response(404)
        self.end_headers()

if __name__ == "__main__":
    server_address = ("", PORT)
    httpd = http.server.HTTPServer(server_address, JuziAPIHandler)
    print(f"JuziGenius Server running locally at http://localhost:{PORT}")
    httpd.serve_forever()
