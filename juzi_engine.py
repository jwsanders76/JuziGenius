import json
import os
import re
import warnings
from google import genai
import config

# Suppress minor SDK warning logs regarding automatic function calling
warnings.filterwarnings("ignore", category=UserWarning, module="google.genai")

class JuziEngine:
    def __init__(self, brain_path="brain.json", words_path="words_freq.json", master_dict_path="master_dictionary.json"):
        self.brain_path = brain_path
        self.words_path = words_path
        self.master_dict_path = master_dict_path
        # Initialize official GenAI client
        self.client = genai.Client(api_key=config.GEMINI_API_KEY)
        self.model_name = getattr(config, "BATCH_MODEL", "gemini-2.5-flash")

    def load_unlocked_chars(self) -> str:
        """Loads unlocked characters from brain.json and returns them as a single string pool."""
        try:
            if not os.path.exists(self.brain_path):
                return ""
            with open(self.brain_path, "r", encoding="utf-8") as f:
                data = json.load(f)
                chars = list(data.get("unlocked_chars", {}).keys())
                return "".join(chars)
        except Exception as e:
            print(f"Warning loading unlocked characters: {e}")
            return ""

    def load_master_dictionary(self) -> dict:
        """Loads the static character -> pinyin/meaning reference dictionary."""
        if not os.path.exists(self.master_dict_path):
            return {}
        try:
            with open(self.master_dict_path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            print(f"Warning: Could not load {self.master_dict_path}: {e}")
            return {}

    def load_word_frequencies(self) -> dict:
        """Loads the separate static word frequency file safely."""
        if not os.path.exists(self.words_path):
            return {}
        try:
            with open(self.words_path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            print(f"Warning: Could not load {self.words_path}: {e}")
            return {}

    def analyze_text_compounds(self, raw_text: str) -> list:
        """
        Scans text and cross-references the separate static word frequency database 
        to identify high-frequency compound words present in the input.
        """
        word_db = self.load_word_frequencies()
        found_words = []

        for word, meta in word_db.items():
            if word.startswith("_"):
                continue
            if word in raw_text:
                found_words.append({
                    "word": word,
                    "pinyin": meta.get("pinyin", ""),
                    "meaning": meta.get("meaning", ""),
                    "rank": meta.get("rank", 99999)
                })

        # Sort discovered words by natural corpus usage frequency rank
        found_words.sort(key=lambda x: x["rank"])
        return found_words

    def import_text_locally(self, raw_text: str) -> dict:
        """
        Parses raw text/sentences, extracts unique Chinese characters, pulls their 
        pinyin/meanings instantly from the local master_dictionary, and updates brain.json.
        Also scans for compound words via the static word frequency database.
        """
        chinese_chars = set(re.findall(r'[\u4e00-\u9fa5]', raw_text))
        
        # Load existing brain database
        brain_data = {"unlocked_chars": {}, "sentences": []}
        if os.path.exists(self.brain_path):
            try:
                with open(self.brain_path, "r", encoding="utf-8") as f:
                    brain_data = json.load(f)
            except Exception as e:
                print(f"Error reading brain database: {e}")

        # master_dictionary now lives in its own tracked file (master_dictionary.json),
        # not inside gitignored brain.json -- drop any stale embedded copy on save.
        brain_data.pop("master_dictionary", None)

        unlocked = brain_data.setdefault("unlocked_chars", {})
        master = self.load_master_dictionary()

        if not chinese_chars:
            return {
                "added_count": 0,
                "total_unlocked_count": len(unlocked),
                "message": "No Chinese characters found in input text."
            }

        added_count = 0
        missing_chars = []

        for char in chinese_chars:
            if char not in unlocked:
                if char in master:
                    unlocked[char] = {
                        "pinyin": master[char].get("pinyin", ""),
                        "meaning": master[char].get("meaning", ""),
                        "interval": 0,
                        "factor": 2.5,
                        "reps": 0,
                        "last": None
                    }
                    added_count += 1
                else:
                    missing_chars.append(char)

        # Save updates back to brain.json
        with open(self.brain_path, "w", encoding="utf-8") as f:
            json.dump(brain_data, f, ensure_ascii=False, indent=4)

        compounds = self.analyze_text_compounds(raw_text)
        total_unlocked = len(unlocked)

        msg = f"Instantly unlocked {added_count} characters locally! Total active pool: {total_unlocked}."
        if compounds:
            msg += f" Found {len(compounds)} high-frequency compound words."
        if missing_chars:
            msg += f" ({len(missing_chars)} chars missing from master dictionary)."

        return {
            "added_count": added_count,
            "total_unlocked_count": total_unlocked,
            "compounds_detected": compounds,
            "message": msg
        }

    def generate_session(self, count: int = 5) -> list:
        """Dynamically generates a fresh list of session sentences using ONLY unlocked characters."""
        unlocked = self.load_unlocked_chars()
        
        if not unlocked:
            return []

        prompt = (
            f"Generate {count} unique, natural Mandarin Chinese practice sentences using ONLY these characters: {unlocked}. "
            f"Do not use any Chinese characters outside this set, except standard Chinese punctuation (，。！？、). "
            f"Output strictly one sentence per line formatted as: English | Chinese. No markdown code blocks, no list numbers."
        )

        response = self.client.models.generate_content(
            model=self.model_name,
            contents=prompt
        )

        raw_output = response.text.strip()
        # Strip markdown fences if present
        raw_output = re.sub(r'```[a-zA-Z]*\n?', '', raw_output).replace('```', '')

        session_sentences = []
        lines = raw_output.strip().split("\n")
        allowed_punct = "，。！？、 ；：“”‘’—…\t\r"

        for raw_line in lines:
            line = raw_line.strip()
            # Remove leading bullet points or numbered lists
            line = re.sub(r'^\s*(\d+[\.\)]|\*|-)\s*', '', line)

            if "|" in line:
                parts = line.split("|", 1)
                eng = parts[0].strip()
                chi = parts[1].strip()

                if eng and chi and all(c in unlocked or c in allowed_punct for c in chi):
                    session_sentences.append({
                        "english": eng,
                        "chinese": chi,
                        "status": "pending"
                    })

        return session_sentences

    def generate_fresh_session(self, count: int = 5) -> dict:
        """
        Generates a brand new batch of AI session sentences and replaces the
        saved sentence bank in brain.json, so a completed session doesn't
        just replay the same sentences forever. If generation fails (no API
        key, offline, etc.) the existing saved bank is left untouched.
        """
        brain_data = {"unlocked_chars": {}, "sentences": []}
        if os.path.exists(self.brain_path):
            try:
                with open(self.brain_path, "r", encoding="utf-8") as f:
                    brain_data = json.load(f)
            except Exception as e:
                print(f"Error reading brain database: {e}")

        unlocked_chars = brain_data.get("unlocked_chars", {})
        raw_sentences = self.generate_session(count=count)

        new_sentences = []
        for item in raw_sentences:
            chi_str = item["chinese"]
            char_metadata = {}
            for char in chi_str:
                if char in unlocked_chars:
                    char_metadata[char] = {
                        "pinyin": unlocked_chars[char].get("pinyin", ""),
                        "meaning": unlocked_chars[char].get("meaning", "")
                    }
                else:
                    char_metadata[char] = {"pinyin": "", "meaning": ""}
            new_sentences.append({
                "english": item["english"],
                "chinese": chi_str,
                "char_metadata": char_metadata
            })

        if new_sentences:
            brain_data["sentences"] = new_sentences
            with open(self.brain_path, "w", encoding="utf-8") as f:
                json.dump(brain_data, f, ensure_ascii=False, indent=4)

        return {
            "sentences": brain_data.get("sentences", []),
            "total_unlocked_count": len(unlocked_chars)
        }

if __name__ == "__main__":
    engine = JuziEngine()
    try:
        result = engine.import_text_locally("我喜欢学习中文。")
        print("Import Result:", result)
    except Exception as e:
        print(f"Error: {e}")
