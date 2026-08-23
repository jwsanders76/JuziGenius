import http.server
import json
import os
from juzi_engine import JuziEngine

PORT = 8000
engine = JuziEngine()

class JuziAPIHandler(http.server.SimpleHTTPRequestHandler):
    def do_GET(self):
        # Intercept API requests for dynamic sentence generation
        if self.path == "/api/session":
            try:
                # Generate sentences using the JuziEngine backend
                raw_sentences = engine.generate_session(count=3)
                
                # Enrich sentences with character metadata (Pinyin lookup from brain.json)
                enriched_session = []
                with open(engine.brain_path, "r", encoding="utf-8") as f:
                    brain_data = json.load(f)
                    unlocked_chars = brain_data.get("unlocked_chars", {})

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
                            # Fallback for punctuation or unindexed characters
                            char_metadata[char] = { "pinyin": "", "meaning": "" }

                    enriched_session.append({
                        "english": item["english"],
                        "chinese": chi_str,
                        "char_metadata": char_metadata
                    })

                # Send JSON response back to frontend
                self.send_response(200)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.end_headers()
                self.wfile.write(json.dumps(enriched_session, ensure_ascii=False).encode("utf-8"))
                return
            except Exception as e:
                self.send_response(500)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps({"error": str(e)}).encode("utf-8"))
                return

        # Fallback to standard static file serving (index.html, style.css, app.js)
        return super().do_GET()

if __name__ == "__main__":
    server_address = ("", PORT)
    httpd = http.server.HTTPServer(server_address, JuziAPIHandler)
    print(f"JuziGenius Server running locally at http://localhost:{PORT}")
    httpd.serve_forever()
