import json
import re
import unittest

import init_vocab_db as ivd

UNWANTED = re.compile(r"surname|radical|classical|archaic|literary|dialect|simplified form", re.I)


class CleanMeaning(unittest.TestCase):
    def test_drops_unwanted_senses(self):
        self.assertEqual(ivd.clean_meaning("also; classical final particle of strong affirmation"), "also")
        self.assertEqual(ivd.clean_meaning("horse; surname; KangXi radical 187"), "horse")
        self.assertEqual(ivd.clean_meaning("run; simplified form of"), "run")

    def test_keeps_original_rather_than_blank(self):
        self.assertEqual(ivd.clean_meaning("surname"), "surname")

    def test_leaves_ordinary_meanings_alone(self):
        self.assertEqual(ivd.clean_meaning("big, great, vast, large, high"), "big, great, vast, large, high")


class GeneratedFile(unittest.TestCase):
    def setUp(self):
        with open(ivd.OUTPUT_PATH, encoding="utf-8") as f:
            self.committed = json.load(f)

    def test_committed_file_matches_generator(self):
        self.assertEqual(ivd.build_master_dictionary(), self.committed)

    def test_overrides_are_used_and_target_real_characters(self):
        overrides = ivd.load_overrides()
        for char, meaning in overrides.items():
            self.assertIn(char, self.committed, char)
            self.assertEqual(self.committed[char]["meaning"], meaning, char)
            self.assertTrue(meaning.strip(), char)

    def test_top_500_carry_no_unwanted_labels(self):
        top = sorted(self.committed.items(), key=lambda kv: kv[1].get("freq") or 10**9)[:500]
        for char, entry in top:
            self.assertFalse(UNWANTED.search(entry["meaning"].replace("Wang (very common surname)", "").replace("Zhang (common surname)", "").replace("Lin (common surname)", "").replace("Ma (common surname)", "").replace("Li (very common surname)", "").replace("Luo (surname)", "")), f"{char}: {entry['meaning']}")


if __name__ == "__main__":
    unittest.main()
