"""
One-time repair for unlocked_words meanings frozen at the wrong text.

unlocked_words[word]["meaning"] (brain.json) is copied from words_freq.json
once, at the moment a word is unlocked (see analyze_text_compounds callers
and add_words_from_suggestions in juzi_engine.py) -- it is never read again
afterward. Fixing a wrong entry in words_freq.json therefore only helps
words unlocked *after* the fix; anyone who already unlocked the word keeps
whatever wrong text was there at the time, forever. This is the word
equivalent of backfill_character_meanings.py, needed because the September
2026 HSK-corpus translation review corrected 10 words in the HSK
"_with_sentences" CSVs (words_freq.json is a separate, hand-built snapshot
that doesn't regenerate from those CSVs automatically) and cleaned up 22
more HSK 4-6 words that had raw CC-CEDICT leftovers ("variant of X[pinyin]",
"CL:...[pinyin]", "see also X[pinyin]") leaking into the English shown to
users.

Unlike backfill_character_meanings.py, this can't just look for "blank" --
the corrections here replaced wrong-but-present text, not missing text. So
this script carries the exact old->new mapping for the 32 words touched by
that review and only overwrites a stored meaning that matches the old text
byte-for-byte, leaving everything else untouched.

Usage:
    python3 backfill_word_meanings.py
"""
import json
import os

USERS_DIR = "users"
ROOT_BRAIN_PATH = "brain.json"

# word -> (old meaning, corrected meaning), from the words_freq.json fixes
# applied alongside the HSK corpus translation review.
WORD_MEANING_FIXES = {
    "杯": ("(counter for bottles)", "(counter for cups)"),
    "人": ("man", "person"),
    "钱": ("coin", "money"),
    "点": ("o'ckock", "o'clock; a bit; point"),
    "长": ("grow", "long"),
    "非常": ("unusual", "very; extremely"),
    "得": ("to have to", "particle used after a verb"),
    "跑步": ("runing", "running"),
    "爷爷": ("grandgather", "grandfather"),
    "只": ("only", "(counter for birds and some animals)"),
    "暗": (
        "to close (a door); to eclipse; muddled; stupid; ignorant; variant of 暗[àn]",
        "to close (a door); to eclipse; muddled; stupid; ignorant",
    ),
    "份": (
        "classifier for gifts; newspaper; magazine; papers; reports; contracts etc; variant of 分[fèn]",
        "classifier for gifts; newspaper; magazine; papers; reports; contracts etc; variant of 分",
    ),
    "陪": (
        "to accompany; to keep sb company; to assist; old variant of 賠|赔[péi]",
        "to accompany; to keep sb company; to assist; old variant of 赔",
    ),
    "胡同": ("variant of 胡同[hú tòng]", "hutong; lane; alley"),
    "克": (
        "variant of 克[kè]; to subdue; to overthrow; to restrain",
        "to subdue; to overthrow; to restrain",
    ),
    "平": (
        "flat; level; equal; to tie (make the same score); to draw (score); calm; peaceful; see also 平聲|平声[píng shēng]",
        "flat; level; equal; to tie (make the same score); to draw (score); calm; peaceful; see also 平声",
    ),
    "升": ("variant of 升[shēng]", "liter (l); to rise; to ascend; to promote"),
    "铜": (
        "copper (chemistry); see also 紅銅|红铜[hóng tóng]",
        "copper (chemistry); see also 红铜",
    ),
    "祖国": (
        "ancestral land CL:個|个[gè]; homeland; used for PRC",
        "ancestral land; homeland; used for PRC",
    ),
    "别致": (
        "variant of 別緻|别致[bié zhì]",
        "unique; novel; delicate and unusual in style",
    ),
    "大伙儿": ("erhua variant of 大伙[dà huǒ]", "erhua variant of 大伙"),
    "淡季": (
        "off season; slow business season; see also 旺季[wàng jì]",
        "off season; slow business season; see also 旺季",
    ),
    "得罪": (
        "to commit an offense; to violate the law; excuse me! (formal); see also 得罪[dé zui]",
        "to commit an offense; to violate the law; excuse me! (formal)",
    ),
    "法人": (
        "legal person; corporation; see also 自然人[zì rán rén]",
        "legal person; corporation; see also 自然人",
    ),
    "弥漫": (
        "variant of 彌漫|弥漫[mí màn]",
        "to fill the air; to spread all over; to pervade",
    ),
    "摊儿": ("erhua variant of 攤|摊[tān]", "erhua variant of 摊"),
    "掏": (
        "variant of 掏[tāo]",
        "to dig out; to scoop out; to fish out (e.g. from a pocket)",
    ),
    "玩意儿": ("erhua variant of 玩意[wán yì]", "erhua variant of 玩意"),
    "溪": ("variant of 溪; creek; rivulet", "creek; rivulet"),
    "馅儿": (
        "erhua variant of 餡|馅; stuffing; filling; e.g. in 包子 or 饺子[jiǎo zi]",
        "erhua variant of 馅; stuffing; filling; e.g. in 包子 or 饺子",
    ),
    "凶恶": (
        "variant of 兇惡|凶恶; fierce; ferocious; fiendish; frightening",
        "fierce; ferocious; fiendish; frightening",
    ),
    "折": ("variant of 折[zhé]; to fold", "to fold"),
}


def find_brain_paths():
    paths = []
    if os.path.exists(ROOT_BRAIN_PATH):
        paths.append(ROOT_BRAIN_PATH)
    if os.path.isdir(USERS_DIR):
        for entry in sorted(os.listdir(USERS_DIR)):
            brain_path = os.path.join(USERS_DIR, entry, "brain.json")
            if os.path.isfile(brain_path):
                paths.append(brain_path)
    return paths


def backfill(brain_path):
    with open(brain_path, "r", encoding="utf-8") as f:
        brain_data = json.load(f)

    unlocked_words = brain_data.get("unlocked_words") or {}
    fixed = []
    for word, meta in unlocked_words.items():
        fix = WORD_MEANING_FIXES.get(word)
        if not fix:
            continue
        old_meaning, new_meaning = fix
        if meta.get("meaning") == old_meaning:
            meta["meaning"] = new_meaning
            fixed.append(word)

    if fixed:
        with open(brain_path, "w", encoding="utf-8") as f:
            json.dump(brain_data, f, ensure_ascii=False, indent=4)
    return fixed


def main():
    total_fixed = 0
    for brain_path in find_brain_paths():
        fixed = backfill(brain_path)
        if fixed:
            print(f"{brain_path}: fixed {', '.join(fixed)}")
            total_fixed += len(fixed)

    print(f"Done. {total_fixed} word meaning(s) backfilled." if total_fixed
          else "Done. Nothing to backfill.")


if __name__ == "__main__":
    main()
