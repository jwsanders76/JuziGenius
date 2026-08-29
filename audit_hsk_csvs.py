#!/usr/bin/env python3
"""
Audits the hand-curated HSK sentence CSVs for data defects.

These two files are hand-curated rather than generated -- there is no build
step behind them, unlike master_dictionary.json or tatoeba_sentences.csv -- so
a bad row stays bad until someone notices. Two rows were found in August 2026
whose `sentence` column was corrupted (a truncated sentence and a misplaced
field boundary); because pick_hsk_sentences reads that column, both were being
served to the user as malformed practice items.

A naive check -- compare each row's hanzi count to its pinyin syllable count --
misses the worst cases, because a row where sentence_pinyin faithfully mirrors
a *broken* sentence has matching counts. This script instead does three checks:

  A. A word token repeated back-to-back in the `sentence` column (with no
     punctuation between, so enumerations like "我、我妈妈" are not flagged).
     These reach the user, so they are the serious class.
  B. Rows whose sentence_pinyin cannot be cut into exactly as many syllables
     as the sentence has characters. Catches dropped words, stray characters,
     and misplaced spaces.
  C. Rows giving a character a reading attested nowhere else in the repo.
     Catches genuine polyphone errors, e.g. 参加 romanized with 参's shēn
     reading instead of cān.

The per-character reading table is built from the repo's own data only
(master_dictionary.json, words_freq.json, hsk_level4to6_vocab_source.json, and
the CSVs' own word/word_pinyin columns), so this needs no network access and no
third-party package, matching the rest of the project.

WHAT THIS CANNOT CATCH: a character given a wrong reading that is nonetheless a
real reading of that character in some other context -- 的 romanized dì rather
than de, say. Both are genuine readings of 的, so only a context-aware model
could tell them apart, which is exactly outstanding finding 10. Passing this
script means the files are free of the mechanical defects above, not that
sentence_pinyin is correct. Treat that column as unverified input.

Usage:  python3 audit_hsk_csvs.py        (exit 0 = clean, 1 = findings)
"""
import csv
import json
import os
import re
import sys
import unicodedata
from collections import defaultdict
from functools import lru_cache

CSV_FILES = [
    "hsk_level1and2_words_with_sentences.csv",
    "hsk_level3_words_with_sentences.csv",
]

# Readings the single-reading sources structurally cannot supply: master_dictionary.json
# stores exactly one pinyin per character (see finding 10), so genuine polyphones need
# their alternates listed here or check C reports false positives.
EXTRA_READINGS = {
    "的": {"de", "di"}, "了": {"le", "liao"}, "不": {"bu"}, "一": {"yi"},
    "地": {"de", "di"}, "得": {"de", "dei"}, "着": {"zhe", "zhao", "zhuo"},
    "长": {"chang", "zhang"}, "儿": {"er", "r"}, "们": {"men"}, "个": {"ge"},
    "什": {"shen", "she"}, "行": {"xing", "hang"}, "少": {"shao"},
    "教": {"jiao"}, "还": {"hai", "huan"}, "会": {"hui", "kuai"},
    "为": {"wei"}, "过": {"guo"}, "只": {"zhi"}, "好": {"hao"},
    "觉": {"jue", "jiao"},
}

PUNCT = set("，。！？、；：“”‘’—…,.!?:;")


def base(pinyin):
    """Tone-stripped lowercase syllable: 'dì' -> 'di', 'lǚ' -> 'lv'."""
    s = unicodedata.normalize("NFD", pinyin.lower())
    s = "".join(c for c in s if unicodedata.category(c) != "Mn")
    return s.replace("ü", "v").strip()


def is_hanzi(c):
    return "一" <= c <= "鿿"


def build_reading_table():
    """char -> {tone-stripped readings}, from the repo's own data only."""
    readings = defaultdict(set)

    def add_word(word, pinyin):
        chars = [c for c in word if is_hanzi(c)]
        syls = [base(s) for s in re.split(r"[\s']+", pinyin) if base(s)]
        if len(chars) == len(syls):  # map positionally only when unambiguous
            for c, s in zip(chars, syls):
                readings[c].add(s)

    with open("master_dictionary.json", encoding="utf-8") as f:
        for c, v in json.load(f).items():
            if v.get("pinyin"):
                readings[c].add(base(v["pinyin"]))

    with open("words_freq.json", encoding="utf-8") as f:
        for word, v in json.load(f).items():
            if isinstance(v, dict) and v.get("pinyin"):
                add_word(word, v["pinyin"])

    if os.path.exists("hsk_level4to6_vocab_source.json"):
        with open("hsk_level4to6_vocab_source.json", encoding="utf-8") as f:
            for level in json.load(f).get("levels", {}).values():
                for entry in level:
                    add_word(entry.get("hanzi", ""), entry.get("pinyin", ""))

    for path in CSV_FILES:
        with open(path, encoding="utf-8") as f:
            for row in csv.DictReader(f, delimiter="\t"):
                if row.get("word") and row.get("word_pinyin"):
                    add_word(row["word"], row["word_pinyin"])

    for char, extra in EXTRA_READINGS.items():
        readings[char] |= extra
    return readings


def make_aligner(readings):
    """Count-aware segmentation: a greedy split mis-cuts dàngāo as dang+ao."""
    all_syls = frozenset(s for v in readings.values() for s in v if s)

    @lru_cache(maxsize=None)
    def cuts(run, n):
        """Every way to cut `run` into exactly n known syllables."""
        if n == 0:
            return [()] if not run else []
        out = []
        for k in range(1, min(6, len(run)) + 1):
            if run[:k] in all_syls:
                for rest in cuts(run[k:], n - 1):
                    out.append((run[:k],) + rest)
        return out

    def align(chars, runs):
        """(syllables, every_reading_known) or (None, False) if no split fits."""
        def rec(i, remaining):
            if i == len(runs):
                return [()] if remaining == 0 else []
            out = []
            for n in range(1, remaining - (len(runs) - i - 1) + 1):
                for head in cuts(runs[i], n):
                    for tail in rec(i + 1, remaining - n):
                        out.append(head + tail)
                        if len(out) > 400:  # plenty to find a valid one
                            return out
            return out

        candidates = rec(0, len(chars))
        if not candidates:
            return None, False
        for cand in candidates:
            if all(not readings.get(c) or s in readings[c]
                   for c, s in zip(chars, cand)):
                return list(cand), True
        return list(candidates[0]), False

    return align


def audit():
    readings = build_reading_table()
    align = make_aligner(readings)
    duplicated, unsplittable, bad_reading = [], [], []

    for path in CSV_FILES:
        with open(path, encoding="utf-8") as f:
            for lineno, row in enumerate(csv.DictReader(f, delimiter="\t"), start=2):
                sentence = row.get("sentence") or ""
                pinyin = row.get("sentence_pinyin") or ""
                where = (path, lineno, sentence, pinyin)

                # A -- repeated word token, no punctuation between
                tokens = [t for t in sentence.split() if t.strip()]
                for a, b in zip(tokens, tokens[1:]):
                    if a == b and any(is_hanzi(c) for c in a) and not (set(a) & PUNCT):
                        duplicated.append(where + (f"repeated token {a!r}",))
                        break

                chars = [c for c in sentence if is_hanzi(c)]
                runs = [r for r in re.findall(r"[a-zü]+", base(pinyin)) if r]
                if not chars or not runs:
                    continue

                syls, all_known = align(chars, runs)
                if syls is None:
                    unsplittable.append(
                        where + (f"cannot cut pinyin into {len(chars)} syllables",))
                elif not all_known:
                    bad = [(c, s) for c, s in zip(chars, syls)
                           if readings.get(c) and s not in readings[c]]
                    detail = ", ".join(
                        f"{c}->{s} (attested: {'/'.join(sorted(readings[c]))})"
                        for c, s in bad[:4])
                    bad_reading.append(where + (detail,))

    return duplicated, unsplittable, bad_reading


def report(title, rows):
    print(f"=== {title}: {len(rows)} ===")
    for path, lineno, sentence, pinyin, why in rows:
        tag = "L1-2" if "1and2" in path else "L3"
        print(f"  {tag}:{lineno}  {sentence}")
        print(f"        {pinyin}")
        print(f"        -> {why}")
    print()


def main():
    duplicated, unsplittable, bad_reading = audit()
    report("A. duplicated word token in the SENTENCE column (served to the user)",
           duplicated)
    report("B. pinyin cannot be cut to the sentence's character count", unsplittable)
    report("C. character given a reading unattested elsewhere in the repo", bad_reading)

    total = len(duplicated) + len(unsplittable) + len(bad_reading)
    if total:
        print(f"{total} finding(s). See this file's docstring for what these mean.")
        return 1
    print("Clean -- no mechanical defects found. Note this cannot detect a "
          "wrong-but-real reading (see the docstring).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
