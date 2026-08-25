import csv
import json
import os
import random
import re

import ai_providers

try:
    import config
except ImportError:
    config = None

HSK_SOURCE_FILES = ["hsk_level1and2_words_with_sentences.csv", "hsk_level3_words_with_sentences.csv"]


class JuziEngine:
    def __init__(self, brain_path="brain.json", words_path="words_freq.json", master_dict_path="master_dictionary.json"):
        self.brain_path = brain_path
        self.words_path = words_path
        self.master_dict_path = master_dict_path

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

    def load_unlocked_words(self) -> list:
        """
        Loads the compound words the user has explicitly added (brain.json's
        unlocked_words), most-frequent first. Used to bias AI sentence
        generation toward real, already-studied vocabulary instead of letting
        it freely invent obscure compounds from the raw character pool.
        """
        try:
            if not os.path.exists(self.brain_path):
                return []
            with open(self.brain_path, "r", encoding="utf-8") as f:
                words = json.load(f).get("unlocked_words", {})
            return sorted(words.keys(), key=lambda w: words[w].get("rank", 99999))
        except Exception as e:
            print(f"Warning loading unlocked words: {e}")
            return []

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
        brain_data = {"unlocked_chars": {}, "unlocked_words": {}, "sentences": []}
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
        unlocked_words = brain_data.setdefault("unlocked_words", {})
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

        # Every compound word the frequency database recognizes in the pasted
        # text is recorded in unlocked_words too, not just its individual
        # characters -- so it counts as "already studied" and (a) won't be
        # suggested again by the Suggest Words tab, and (b) is available to
        # bias AI sentence generation toward real vocabulary.
        compounds = self.analyze_text_compounds(raw_text)
        added_word_count = 0
        for item in compounds:
            word = item["word"]
            if word not in unlocked_words:
                unlocked_words[word] = {
                    "pinyin": item.get("pinyin", ""),
                    "meaning": item.get("meaning", ""),
                    "rank": item.get("rank", 99999),
                }
                added_word_count += 1

        # Save updates back to brain.json
        with open(self.brain_path, "w", encoding="utf-8") as f:
            json.dump(brain_data, f, ensure_ascii=False, indent=4)

        total_unlocked = len(unlocked)

        msg = f"Instantly unlocked {added_count} characters locally! Total active pool: {total_unlocked}."
        if compounds:
            msg += f" Found {len(compounds)} high-frequency compound words ({added_word_count} new)."
        if missing_chars:
            msg += f" ({len(missing_chars)} chars missing from master dictionary)."

        return {
            "added_count": added_count,
            "total_unlocked_count": total_unlocked,
            "compounds_detected": compounds,
            "added_word_count": added_word_count,
            "message": msg
        }

    def suggest_new_words(self, count: int = 5) -> list:
        """
        Suggests the highest-frequency compound words from words_freq.json
        that the user hasn't already added to their vocabulary (tracked via
        brain.json's unlocked_words), so vocabulary growth follows real-world
        usage frequency rather than random order.
        """
        word_db = self.load_word_frequencies()

        known_words = {}
        if os.path.exists(self.brain_path):
            try:
                with open(self.brain_path, "r", encoding="utf-8") as f:
                    known_words = json.load(f).get("unlocked_words", {})
            except Exception as e:
                print(f"Error reading brain database: {e}")

        candidates = [
            {
                "word": word,
                "pinyin": meta.get("pinyin", ""),
                "meaning": meta.get("meaning", ""),
                "rank": meta.get("rank", 99999),
            }
            for word, meta in word_db.items()
            if not word.startswith("_") and word not in known_words
        ]
        candidates.sort(key=lambda x: x["rank"])
        return candidates[:count]

    def add_words(self, words: list) -> dict:
        """
        Adds the given compound words to brain.json's unlocked_words (so
        future suggestions skip them) and unlocks any of their individual
        characters that aren't already unlocked, so the new words are
        immediately available for handwriting practice sentences.
        """
        word_db = self.load_word_frequencies()
        master = self.load_master_dictionary()

        brain_data = {"unlocked_chars": {}, "unlocked_words": {}, "sentences": []}
        if os.path.exists(self.brain_path):
            try:
                with open(self.brain_path, "r", encoding="utf-8") as f:
                    brain_data = json.load(f)
            except Exception as e:
                print(f"Error reading brain database: {e}")

        unlocked_words = brain_data.setdefault("unlocked_words", {})
        unlocked_chars = brain_data.setdefault("unlocked_chars", {})

        added_words = []
        added_chars = 0

        for word in words:
            meta = word_db.get(word)
            if not meta or word in unlocked_words:
                continue

            unlocked_words[word] = {
                "pinyin": meta.get("pinyin", ""),
                "meaning": meta.get("meaning", ""),
                "rank": meta.get("rank", 99999),
            }
            added_words.append(word)

            for char in word:
                if char not in unlocked_chars and char in master:
                    unlocked_chars[char] = {
                        "pinyin": master[char].get("pinyin", ""),
                        "meaning": master[char].get("meaning", ""),
                        "interval": 0,
                        "factor": 2.5,
                        "reps": 0,
                        "last": None,
                    }
                    added_chars += 1

        with open(self.brain_path, "w", encoding="utf-8") as f:
            json.dump(brain_data, f, ensure_ascii=False, indent=4)

        return {
            "added_words": added_words,
            "added_chars_count": added_chars,
            "total_unlocked_count": len(unlocked_chars),
            "total_words_count": len(unlocked_words),
        }

    def list_providers(self) -> list:
        """
        Reports which AI providers exist and whether a server-side key is
        configured for them (currently only possible for Gemini, via
        config.py). Every provider can still be used by supplying a
        client-held API key per request regardless of this flag.
        """
        server_gemini_key = getattr(config, "GEMINI_API_KEY", None) if config else None
        has_server_gemini_key = bool(server_gemini_key and server_gemini_key != "PASTE_YOUR_API_KEY_HERE")

        providers = []
        for provider_id, meta in ai_providers.PROVIDER_CONFIG.items():
            providers.append({
                "id": provider_id,
                "label": meta["label"],
                "server_configured": has_server_gemini_key if provider_id == "gemini" else False
            })
        return providers

    def pick_hsk_sentences(self, count: int = 5) -> list:
        """
        Picks real example sentences from the local HSK corpora whose characters
        are entirely within the user's currently unlocked pool. No AI, no
        network call, no API key required.
        """
        unlocked = self.load_unlocked_chars()
        if not unlocked:
            return []

        unlocked_set = set(unlocked)
        allowed_punct = "，。！？、；：“”‘’—…"
        candidates = []
        seen_chinese = set()

        for filename in HSK_SOURCE_FILES:
            if not os.path.exists(filename):
                continue
            try:
                with open(filename, "r", encoding="utf-8") as f:
                    reader = csv.DictReader(f, delimiter="\t")
                    for row in reader:
                        chinese = (row.get("sentence") or "").replace(" ", "").strip()
                        english = (row.get("sentence_meaning") or "").strip()
                        if not chinese or not english or chinese in seen_chinese:
                            continue
                        if all(c in unlocked_set or c in allowed_punct for c in chinese):
                            seen_chinese.add(chinese)
                            candidates.append({"english": english, "chinese": chinese})
            except Exception as e:
                print(f"Warning: could not read {filename}: {e}")

        random.shuffle(candidates)
        return candidates[:count]

    def generate_session(self, count: int = 5, provider: str = "gemini", api_key: str = None) -> list:
        """Dynamically generates a fresh list of session sentences using ONLY unlocked characters."""
        unlocked = self.load_unlocked_chars()

        if not unlocked:
            return []

        if not api_key and provider == "gemini":
            api_key = getattr(config, "GEMINI_API_KEY", None) if config else None
        if not api_key:
            raise ValueError(f"No API key available for provider '{provider}'.")

        # With a large unlocked character pool, an AI left to freely combine
        # characters can produce grammatically valid but obscure/rare
        # compound words. Steer it toward the words the user has actually
        # studied (via text import or the Suggest Words picker) when
        # possible, without forbidding other valid combinations of the
        # unlocked characters.
        known_words = self.load_unlocked_words()
        word_guidance = ""
        if known_words:
            word_guidance = (
                f" Where natural, prefer building sentences around these compound words the user has already studied: "
                f"{'、'.join(known_words)}. Avoid inventing obscure or rarely-used compound words from the character pool "
                f"when a common, everyday word would fit instead."
            )

        prompt = (
            f"Generate {count} unique, natural Mandarin Chinese practice sentences using ONLY these characters: {unlocked}. "
            f"Do not use any Chinese characters outside this set, except standard Chinese punctuation (，。！？、)."
            f"{word_guidance} "
            f"Output strictly one sentence per line formatted as: English | Chinese. No markdown code blocks, no list numbers."
        )

        model_override = getattr(config, "BATCH_MODEL", None) if (config and provider == "gemini") else None
        raw_output = ai_providers.call_provider(provider, api_key, prompt, model=model_override).strip()
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

    def generate_fresh_session(self, count: int = 5, source: str = "ai", provider: str = "gemini", api_key: str = None) -> dict:
        """
        Generates a brand new batch of session sentences and replaces the
        saved sentence bank in brain.json, so a completed session doesn't
        just replay the same sentences forever.

        source="ai" calls the given AI provider (raises if no key is
        available). source="hsk" picks real example sentences from the local
        HSK corpora instead -- no AI, no network, no key needed. If no
        sentences come back, the existing saved bank is left untouched.
        """
        brain_data = {"unlocked_chars": {}, "sentences": []}
        if os.path.exists(self.brain_path):
            try:
                with open(self.brain_path, "r", encoding="utf-8") as f:
                    brain_data = json.load(f)
            except Exception as e:
                print(f"Error reading brain database: {e}")

        unlocked_chars = brain_data.get("unlocked_chars", {})

        if source == "hsk":
            raw_sentences = self.pick_hsk_sentences(count=count)
        else:
            raw_sentences = self.generate_session(count=count, provider=provider, api_key=api_key)

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
