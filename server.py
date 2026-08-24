import http.server
import json
import os
from juzi_engine import JuziEngine

PORT = 8000
engine = JuziEngine()

class JuziAPIHandler(http.server.SimpleHTTPRequestHandler):
    def do_GET(self):
        # Intercept API requests for offline session sentence loading
        if self.path == "/api/session":
            try:
                # Load saved sentences and metadata directly from local brain.json
                brain_data = {"unlocked_chars": {}, "sentences": [], "master_dictionary": {}}
                if os.path.exists(engine.brain_path):
                    with open(engine.brain_path, "r", encoding="utf-8") as f:
                        brain_data = json.load(f)

                unlocked_chars = brain_data.get("unlocked_chars", {})
                saved_sentences = brain_data.get("sentences", [])
                total_unlocked = len(unlocked_chars)

                # Fallback to dynamic generation if local sentence bank is empty but characters exist
                if not saved_sentences and total_unlocked > 0:
                    try:
                        raw_sentences = engine.generate_session(count=3)
                        for item in raw_sentences:
                            chi_str = item["chinese"]
                            char_metadata = {}
                            for char in chi_str:
                                if char in unlocked_chars:
                                    char_metadata[char] = {
                                        "pinyin": unlocked_chars[char].get("pinyin", "pīn yīn"),
                                        "meaning": unlocked_chars[char].get("meaning", "")
                                    }
                                else:
                                    char_metadata[char] = {"pinyin": "", "meaning": ""}

                            saved_sentences.append({
                                "english": item["english"],
                                "chinese": chi_str,
                                "char_metadata": char_metadata
                            })

                        brain_data["sentences"] = saved_sentences
                        with open(engine.brain_path, "w", encoding="utf-8") as f:
                            json.dump(brain_data, f, ensure_ascii=False, indent=4)
                    except Exception as gen_err:
                        print(f"AI session generation fallback notice: {gen_err}")

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

        # Fallback to standard static file serving (index.html, style.css, app.js)
        return super().do_GET()

    def do_POST(self):
        # Intercept POST requests for importing raw text/sentences locally
        if self.path == "/api/import":
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

        self.send_response(404)
        self.end_headers()

if __name__ == "__main__":
    server_address = ("", PORT)
    httpd = http.server.HTTPServer(server_address, JuziAPIHandler)
    print(f"JuziGenius Server running locally at http://localhost:{PORT}")
    httpd.serve_forever()
