import csv
import json
import os
import random
import re
import threading
from datetime import date, timedelta

HSK_SOURCE_FILES = ["hsk_level1and2_words_with_sentences.csv", "hsk_level3_words_with_sentences.csv"]
# Real sentences beyond the hand-curated HSK 1-3 set: 16,832 pairs filtered
# from the Tatoeba project (via manythings.org/anki, CC BY 2.0 France --
# native-speaker/proofread) down to sentences using only characters in
# master_dictionary.json. See build_extra_sentences.py for the filter and
# tatoeba_cmn_eng_source.tsv for the raw, attributed upstream data. Read
# through the same tab-delimited schema as the HSK files (only `sentence` and
# `sentence_meaning` are populated), so no reader-side changes were needed.
EXTRA_SENTENCE_FILES = ["tatoeba_sentences.csv"]
SENTENCE_SOURCE_FILES = HSK_SOURCE_FILES + EXTRA_SENTENCE_FILES

# How far suggest_new_words demotes a word whose characters are all already
# unlocked: it teaches vocabulary but unlocks no new handwriting practice.
# Applied as a multiplier on the frequency rank, so a top-50 word like 你好
# still surfaces early while a rank-3000 one drops out of reach.
KNOWN_CHARS_RANK_PENALTY = 3


class JuziEngine:
    def __init__(self, brain_path="brain.json", words_path="words_freq.json", master_dict_path="master_dictionary.json"):
        self.brain_path = brain_path
        self.words_path = words_path
        self.master_dict_path = master_dict_path
        # server.py now runs requests concurrently (ThreadingHTTPServer), so
        # brain.json's read-modify-write methods (review_character,
        # import_text_locally, add_words, generate_fresh_session) can race:
        # two threads each read the same on-disk state, mutate their own copy,
        # and whichever writes last silently wins -- the other thread's
        # update (a character's SRS progress, a saved sentence) is lost. An
        # RLock (not a plain Lock) because these methods call each other and
        # each other's read-only helpers (e.g. generate_fresh_session ->
        # pick_hsk_sentences -> load_unlocked_chars), all on the same thread;
        # a plain Lock would deadlock a thread against itself on the second
        # acquire. Every method that touches self.brain_path holds this for
        # its full read-through-write span, not just around each open().
        self.brain_lock = threading.RLock()

    def load_unlocked_chars(self) -> str:
        """Loads unlocked characters from brain.json and returns them as a single string pool."""
        try:
            with self.brain_lock:
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

    def get_due_characters(self, unlocked_chars: dict = None) -> set:
        """
        Returns the subset of unlocked characters that are due for SM-2
        review: never reviewed yet (last is None/unparseable) or whose
        interval has elapsed since their last review. Used to bias which
        practice sentences get picked/generated toward characters that
        actually need reinforcement, rather than treating the whole
        unlocked pool as equally worth practicing.
        """
        if unlocked_chars is None:
            brain_data = {}
            with self.brain_lock:
                if os.path.exists(self.brain_path):
                    try:
                        with open(self.brain_path, "r", encoding="utf-8") as f:
                            brain_data = json.load(f)
                    except Exception as e:
                        print(f"Error reading brain database: {e}")
            unlocked_chars = brain_data.get("unlocked_chars", {})

        today = date.today()
        due = set()
        for char, meta in unlocked_chars.items():
            last = meta.get("last")
            if not last:
                due.add(char)
                continue
            try:
                last_date = date.fromisoformat(last)
            except ValueError:
                due.add(char)
                continue
            interval = meta.get("interval", 0) or 0
            if last_date + timedelta(days=interval) <= today:
                due.add(char)
        return due

    def review_character(self, char: str, quality: int) -> dict:
        """
        Grades a single completed character quiz and advances its SM-2
        scheduling fields (interval, factor, reps, last) in brain.json.
        quality is 0-5 recall quality (5 = perfect, no hints needed);
        the frontend derives it from how many hint tiers were used.
        Per standard SM-2, quality < 3 counts as a failed recall and resets
        the repetition streak (interval back to 1, reps back to 0) rather
        than advancing the schedule.

        SM-2 assumes at most one grading per item per day. This app can
        easily produce many more: a character often appears twice in one
        sentence (我爱我的妈妈), and the sentence bank loops forever, so a
        single sitting can grade the same character six or more times.
        Advancing on every one of those compounds the interval multiplier
        against itself (1 -> 6 -> 16 -> 45 -> 130 -> 390 days), scheduling a
        character the user has barely learned a year out. So a repeat
        grading on a day the character was already reviewed does not
        advance the schedule. A *failed* repeat still applies its lapse:
        forgetting a character later in the same session is real evidence
        that it isn't known, and ignoring it would let the loop paper over
        genuine failures.
        """
        quality = max(0, min(5, int(quality)))

        with self.brain_lock:
            brain_data = {"unlocked_chars": {}, "sentences": []}
            if os.path.exists(self.brain_path):
                try:
                    with open(self.brain_path, "r", encoding="utf-8") as f:
                        brain_data = json.load(f)
                except Exception as e:
                    print(f"Error reading brain database: {e}")

            unlocked_chars = brain_data.setdefault("unlocked_chars", {})
            entry = unlocked_chars.get(char)
            if entry is None:
                raise ValueError(f"Character '{char}' is not in the unlocked pool.")

            today = date.today().isoformat()
            already_reviewed_today = entry.get("last") == today

            reps = entry.get("reps", 0) or 0
            interval = entry.get("interval", 0) or 0
            factor = entry.get("factor", 2.5) or 2.5

            if quality < 3:
                # A lapse always counts, including a same-day repeat.
                reps = 0
                interval = 1
                factor = factor + (0.1 - (5 - quality) * (0.08 + (5 - quality) * 0.02))
                factor = max(1.3, factor)
            elif already_reviewed_today:
                # Successful repeat on a day already credited: no schedule
                # change, and no ease bump either (that would inflate the
                # multiplier for every later review just as badly).
                return {
                    "char": char,
                    "reps": reps,
                    "interval": interval,
                    "factor": round(factor, 2),
                    "last": entry.get("last"),
                    "counted": False,
                    "due_count": len(self.get_due_characters(unlocked_chars))
                }
            else:
                if reps == 0:
                    interval = 1
                elif reps == 1:
                    interval = 6
                else:
                    interval = round(interval * factor)
                reps += 1
                factor = factor + (0.1 - (5 - quality) * (0.08 + (5 - quality) * 0.02))
                factor = max(1.3, factor)

            entry["reps"] = reps
            entry["interval"] = interval
            entry["factor"] = round(factor, 2)
            entry["last"] = today

            with open(self.brain_path, "w", encoding="utf-8") as f:
                json.dump(brain_data, f, ensure_ascii=False, indent=4)

            return {
                "char": char,
                "reps": reps,
                "interval": interval,
                "factor": entry["factor"],
                "last": entry["last"],
                "counted": True,
                "due_count": len(self.get_due_characters(unlocked_chars))
            }

    @staticmethod
    def prune_single_char_words(unlocked_words: dict) -> int:
        """
        Drops single-character entries from an unlocked_words map in place,
        returning how many were removed.

        Before compound detection did real segmentation, a bare substring
        scan recorded any of the corpus's 704 single-character entries that
        appeared anywhere in pasted text, so existing installs carry junk
        like 的/是/我 as "studied compound words". Called on the write paths
        so the file heals itself the next time it's saved, rather than
        needing a separate migration script.
        """
        stale = [w for w in unlocked_words if len(w) < 2]
        for word in stale:
            del unlocked_words[word]
        return len(stale)

    def segment_compounds(self, raw_text: str, word_db: dict = None) -> list:
        """
        Finds the compound words from the frequency corpus that actually occur
        in raw_text, as a list of unique words.

        This replaces a bare `word in raw_text` substring scan over all 5,007
        corpus entries, which was wrong in three separate ways:

        * 704 of those entries are *single characters*, so 的/是/我 were
          reported as "compound words the user has studied" -- and then
          recorded in brain.json as exactly that. Words here are length >= 2,
          always.
        * Overlapping matches both fired: 早上 and 上 were each reported for
          the same two characters. Longest-match-wins segmentation consumes
          the text, so each position is claimed once.
        * A substring search ignores punctuation, so a "word" could straddle
          a clause boundary (...喝热茶。他是... spanning the 。). Matching runs
          of Chinese characters means a match can never cross one.

        Greedy longest-match is not a real statistical segmenter and will
        mis-split genuinely ambiguous strings. For "which HSK words appear in
        this pasted text" that tradeoff is fine; it is not jieba.
        """
        if word_db is None:
            word_db = self.load_word_frequencies()

        # Only real corpus words are matchable: "_"-prefixed keys are metadata,
        # and single characters are what this whole method exists to exclude.
        vocab = {w for w in word_db if len(w) >= 2 and not w.startswith("_")}
        if not vocab:
            return []
        max_len = max(len(w) for w in vocab)

        found = []
        seen = set()

        # Runs of Chinese characters, so punctuation, latin text, digits, and
        # newlines all act as boundaries without having to enumerate them.
        for run in re.findall(r"[一-龥]+", raw_text):
            i = 0
            while i < len(run):
                # Longest candidate first, stopping at 2 -- range's exclusive
                # bound of 1 is what keeps single characters unmatchable.
                for length in range(min(max_len, len(run) - i), 1, -1):
                    candidate = run[i:i + length]
                    if candidate in vocab:
                        if candidate not in seen:
                            seen.add(candidate)
                            found.append(candidate)
                        i += length
                        break
                else:
                    i += 1

        return found

    def analyze_text_compounds(self, raw_text: str) -> list:
        """
        Scans text and cross-references the separate static word frequency database
        to identify high-frequency compound words present in the input.
        """
        word_db = self.load_word_frequencies()

        found_words = [
            {
                "word": word,
                "pinyin": word_db[word].get("pinyin", ""),
                "meaning": word_db[word].get("meaning", ""),
                "rank": word_db[word].get("rank", 99999),
            }
            for word in self.segment_compounds(raw_text, word_db)
        ]

        # Sort discovered words by natural corpus usage frequency rank
        found_words.sort(key=lambda x: x["rank"])
        return found_words

    @staticmethod
    def _split_chinese_sentences(text: str) -> list:
        """Splits after each sentence-ending punctuation mark, keeping it attached to the sentence before it."""
        return [s.strip() for s in re.split(r'(?<=[\u3002\uff01\uff1f])', text.strip()) if s.strip()]

    @staticmethod
    def _split_english_sentences(text: str) -> list:
        """
        Splits after '.', '!', or '?' followed by whitespace. A simple heuristic
        (it will over-split on abbreviations like "Mr.") that only needs to be
        right often enough to line up 1:1 with _split_chinese_sentences' count --
        when it doesn't, import_text_locally falls back to saving the whole
        paste as one sentence rather than mis-pairing.
        """
        return [s.strip() for s in re.split(r'(?<=[.!?])\s+', text.strip()) if s.strip()]

    def import_text_locally(self, raw_text: str, translation_text: str = "") -> dict:
        """
        Parses raw text/sentences, extracts unique Chinese characters, pulls their
        pinyin/meanings instantly from the local master_dictionary, and updates brain.json.
        Also scans for compound words via the static word frequency database.

        If translation_text is given, also saves the pasted Chinese as practice
        sentences paired with it, into brain.json's pasted_sentences -- a
        persistent personal corpus (unlike the ephemeral `sentences` bank,
        which generate_fresh_session overwrites wholesale on every new batch)
        that pick_hsk_sentences draws from alongside the built-in corpora, so
        a user's own imported material keeps resurfacing in practice. Both
        texts are split into individual sentences and paired by position; if
        the counts don't match (imperfect sentence-boundary detection on
        either side), pairing is ambiguous, so the whole paste is saved as one
        sentence instead of guessing an alignment.
        """
        chinese_chars = set(re.findall(r'[\u4e00-\u9fa5]', raw_text))

        with self.brain_lock:
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
            pruned_word_count = self.prune_single_char_words(unlocked_words)
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
            # characters -- so it counts as "already studied" and won't be
            # suggested again by the Suggest Words tab.
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

            # If a translation was pasted alongside the Chinese, save real
            # sentence pairs from it into the user's persistent pasted_sentences.
            saved_sentence_count = 0
            skipped_sentence_count = 0
            sentence_pairing_matched = None
            if translation_text and translation_text.strip():
                chinese_sentences = self._split_chinese_sentences(raw_text)
                english_sentences = self._split_english_sentences(translation_text)
                sentence_pairing_matched = bool(chinese_sentences) and len(chinese_sentences) == len(english_sentences)

                if sentence_pairing_matched:
                    pairs = list(zip(chinese_sentences, english_sentences))
                else:
                    pairs = [(raw_text.strip(), translation_text.strip())]

                pasted_sentences = brain_data.setdefault("pasted_sentences", [])
                existing_chinese = {s.get("chinese") for s in pasted_sentences}

                for chi, eng in pairs:
                    chi = re.sub(r'\s+', '', chi)
                    eng = eng.strip()
                    if not chi or not eng or chi in existing_chinese:
                        continue
                    # Every Chinese character in the sentence must already be
                    # unlockable (present in unlocked, which by now holds every
                    # character from raw_text that's in master_dictionary) --
                    # otherwise it can never be shown on the canvas with real
                    # pinyin/meaning, so the pair isn't worth saving.
                    if not all(c in unlocked for c in re.findall(r'[一-龥]', chi)):
                        skipped_sentence_count += 1
                        continue
                    pasted_sentences.append({"chinese": chi, "english": eng})
                    existing_chinese.add(chi)
                    saved_sentence_count += 1

            # Save updates back to brain.json
            with open(self.brain_path, "w", encoding="utf-8") as f:
                json.dump(brain_data, f, ensure_ascii=False, indent=4)

            total_unlocked = len(unlocked)

        msg = f"Instantly unlocked {added_count} characters locally! Total active pool: {total_unlocked}."
        if compounds:
            msg += f" Found {len(compounds)} high-frequency compound words ({added_word_count} new)."
        if missing_chars:
            msg += f" ({len(missing_chars)} chars missing from master dictionary)."
        if pruned_word_count:
            msg += f" (Cleaned up {pruned_word_count} single-character entries wrongly stored as words.)"
        if saved_sentence_count:
            msg += f" Saved {saved_sentence_count} sentence(s) to your personal practice bank."
        if skipped_sentence_count:
            msg += f" ({skipped_sentence_count} sentence(s) skipped -- contain characters outside the master dictionary.)"
        if sentence_pairing_matched is False:
            msg += " (Couldn't line up sentence boundaries between the two texts, so they were saved as one combined sentence.)"

        return {
            "added_count": added_count,
            "total_unlocked_count": total_unlocked,
            "compounds_detected": compounds,
            "added_word_count": added_word_count,
            "pruned_word_count": pruned_word_count,
            "saved_sentence_count": saved_sentence_count,
            "skipped_sentence_count": skipped_sentence_count,
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

        with self.brain_lock:
            known_words = {}
            unlocked_chars = {}
            if os.path.exists(self.brain_path):
                try:
                    with open(self.brain_path, "r", encoding="utf-8") as f:
                        brain_data = json.load(f)
                    known_words = brain_data.get("unlocked_words", {})
                    unlocked_chars = brain_data.get("unlocked_chars", {})
                except Exception as e:
                    print(f"Error reading brain database: {e}")

        candidates = [
            {
                "word": word,
                "pinyin": meta.get("pinyin", ""),
                "meaning": meta.get("meaning", ""),
                "rank": meta.get("rank", 99999),
                # A word every one of whose characters is already unlocked is
                # still worth learning -- it teaches the compound's meaning --
                # but it buys no new practice material, so it's demoted rather than
                # dropped. The demotion is a multiplier on the frequency rank,
                # not a separate bucket: bucketing sorts *every* word that
                # unlocks a character above *every* word that doesn't, and
                # since almost all 5,007 corpus words contain some character
                # you haven't unlocked, that buries 你好 thousands of entries
                # deep -- exclusion wearing a different hat. Scaling the rank
                # keeps very common known-character words near the top while
                # still letting rarer ones fall behind.
                "_sort_rank": meta.get("rank", 99999) * (
                    1 if any(c not in unlocked_chars for c in word) else KNOWN_CHARS_RANK_PENALTY
                ),
            }
            # len >= 2 is the same filter segment_compounds applies: 704 of the
            # corpus's 5,007 entries are single characters, which is why this
            # tab used to suggest 是/我/的 as "compound words".
            for word, meta in word_db.items()
            if len(word) >= 2 and not word.startswith("_") and word not in known_words
        ]
        candidates.sort(key=lambda x: x["_sort_rank"])
        top = candidates[:count]
        for c in top:
            del c["_sort_rank"]
        return top

    def add_words(self, words: list) -> dict:
        """
        Adds the given compound words to brain.json's unlocked_words (so
        future suggestions skip them) and unlocks any of their individual
        characters that aren't already unlocked, so the new words are
        immediately available for handwriting practice sentences.
        """
        word_db = self.load_word_frequencies()
        master = self.load_master_dictionary()

        with self.brain_lock:
            brain_data = {"unlocked_chars": {}, "unlocked_words": {}, "sentences": []}
            if os.path.exists(self.brain_path):
                try:
                    with open(self.brain_path, "r", encoding="utf-8") as f:
                        brain_data = json.load(f)
                except Exception as e:
                    print(f"Error reading brain database: {e}")

            unlocked_words = brain_data.setdefault("unlocked_words", {})
            unlocked_chars = brain_data.setdefault("unlocked_chars", {})
            pruned_word_count = self.prune_single_char_words(unlocked_words)

            added_words = []
            rejected_single_chars = []
            added_chars = 0

            for word in words:
                meta = word_db.get(word)
                # len >= 2 guards the endpoint itself, not just the suggestions
                # feeding it: /api/suggestions/add takes an arbitrary word list
                # from the client, and a single character is a character, not a
                # compound word -- unlocked_chars is where those belong. Reported
                # back rather than dropped silently: a stale browser tab holding
                # pre-fix suggestions would otherwise submit them and be told
                # "Added 0 words" with no reason given. Single-character words are
                # a real thing (猫, 水, 茶) -- they're learned by pasting them into
                # the Paste Text tab, which unlocks them for practice directly.
                if meta and len(word) < 2:
                    rejected_single_chars.append(word)
                    continue
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
            "pruned_word_count": pruned_word_count,
            "rejected_single_chars": rejected_single_chars,
        }

    def load_pasted_sentences(self) -> list:
        """
        Loads the user's own sentence pairs saved from the Paste Text tab
        (brain.json's pasted_sentences) -- real sentences the user pasted
        alongside a translation, persisted separately from the ephemeral
        `sentences` practice bank (which generate_fresh_session replaces
        wholesale on every new batch) so they keep resurfacing in HSK-mode
        rotation indefinitely, not just for one session.
        """
        try:
            with self.brain_lock:
                if not os.path.exists(self.brain_path):
                    return []
                with open(self.brain_path, "r", encoding="utf-8") as f:
                    return json.load(f).get("pasted_sentences", [])
        except Exception as e:
            print(f"Warning loading pasted sentences: {e}")
            return []

    def pick_hsk_sentences(self, count: int = 5) -> list:
        """
        Picks real example sentences -- from the hand-curated HSK corpora, the
        larger Tatoeba-derived corpus, and the user's own saved pasted
        sentences -- whose characters are entirely within the user's currently
        unlocked pool. No AI, no network call, no API key required. Among
        equally-due candidates, the user's own pasted sentences are preferred
        (real content they chose to study), and sentences that reuse
        characters currently due for SM-2 review are preferred over ones that
        don't, so practice naturally reinforces what's due instead of
        drifting toward whatever the corpus happens to contain.
        """
        unlocked = self.load_unlocked_chars()
        if not unlocked:
            return []

        unlocked_set = set(unlocked)
        due_set = self.get_due_characters()
        allowed_punct = "，。！？、；：“”‘’—…"
        candidates = []
        seen_chinese = set()

        for item in self.load_pasted_sentences():
            chinese = (item.get("chinese") or "").strip()
            english = (item.get("english") or "").strip()
            if not chinese or not english or chinese in seen_chinese:
                continue
            if all(c in unlocked_set or c in allowed_punct for c in chinese):
                seen_chinese.add(chinese)
                due_hits = sum(1 for c in chinese if c in due_set)
                candidates.append({"english": english, "chinese": chinese, "_due_hits": due_hits, "_personal": True})

        for filename in SENTENCE_SOURCE_FILES:
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
                            due_hits = sum(1 for c in chinese if c in due_set)
                            candidates.append({"english": english, "chinese": chinese, "_due_hits": due_hits, "_personal": False})
            except Exception as e:
                print(f"Warning: could not read {filename}: {e}")

        random.shuffle(candidates)
        candidates.sort(key=lambda c: (c["_due_hits"], c["_personal"]), reverse=True)
        for c in candidates:
            del c["_due_hits"]
            del c["_personal"]
        return candidates[:count]

    def generate_fresh_session(self, count: int = 5) -> dict:
        """
        Picks a brand new batch of real example sentences -- from the local
        HSK/Tatoeba corpora and the user's own saved pasted sentences -- and
        replaces the saved sentence bank in brain.json, so a completed
        session doesn't just replay the same sentences forever. No AI, no
        network, no key needed. If no sentences come back, the existing saved
        bank is left untouched.
        """
        with self.brain_lock:
            brain_data = {"unlocked_chars": {}, "sentences": []}
            if os.path.exists(self.brain_path):
                try:
                    with open(self.brain_path, "r", encoding="utf-8") as f:
                        brain_data = json.load(f)
                except Exception as e:
                    print(f"Error reading brain database: {e}")
            unlocked_chars = brain_data.get("unlocked_chars", {})

            # pick_hsk_sentences reacquires brain_lock through its own helpers,
            # which is safe since it's an RLock, and is a fast local read with
            # no network call, so holding the lock across it doesn't stall
            # other requests.
            raw_sentences = self.pick_hsk_sentences(count=count)

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
