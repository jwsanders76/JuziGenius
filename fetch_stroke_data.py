"""
Builds stroke_data.json -- the local, offline stroke-order database that
Hanzi Writer draws from.

By default Hanzi Writer fetches every character's stroke data from
cdn.jsdelivr.net at the moment you're asked to write it, which made
handwriting -- the whole point of this app -- silently require an internet
connection, on the tablet it was built to run on. This script pulls that data
down once so normal play really is offline.

It vendors only the characters JuziGenius can actually put in front of you:
every character in the words_freq.json vocabulary corpus, in the HSK example
sentences, and in your current brain.json. That's ~2,600 characters (a few MB)
rather than the full ~9,600-character, 31 MB upstream package. Characters
outside that set -- rare or variant forms you might paste in via text import --
still fall back to the CDN when you're online, and surface a readable error
instead of a dead canvas when you aren't (see charDataLoader in app.js).

Source: the hanzi-writer-data npm package (MIT). Fetched as a single tarball
rather than thousands of individual requests.

Usage:
    python3 fetch_stroke_data.py
    python3 fetch_stroke_data.py --all      # vendor every available character
"""
import argparse
import csv
import io
import json
import os
import tarfile
import urllib.request

WORDS_PATH = "words_freq.json"
BRAIN_PATH = "brain.json"
MASTER_DICT_PATH = "master_dictionary.json"
OUTPUT_PATH = "stroke_data.json"
HSK_SOURCE_FILES = [
    "hsk_level1and2_words_with_sentences.csv",
    "hsk_level3_words_with_sentences.csv",
]

PACKAGE = "hanzi-writer-data"
REGISTRY_URL = f"https://registry.npmjs.org/{PACKAGE}"

CJK = lambda ch: "一" <= ch <= "龥"


def needed_characters():
    """Every character the app can currently ask the user to write."""
    chars = set()

    if os.path.exists(WORDS_PATH):
        with open(WORDS_PATH, "r", encoding="utf-8") as f:
            for word in json.load(f):
                if not word.startswith("_"):
                    chars.update(c for c in word if CJK(c))

    for filename in HSK_SOURCE_FILES:
        if not os.path.exists(filename):
            continue
        with open(filename, "r", encoding="utf-8") as f:
            for row in csv.DictReader(f, delimiter="\t"):
                chars.update(c for c in (row.get("sentence") or "") if CJK(c))

    # Whatever this install has already unlocked, in case it was imported from
    # text that reaches outside the shipped corpora.
    if os.path.exists(BRAIN_PATH):
        with open(BRAIN_PATH, "r", encoding="utf-8") as f:
            chars.update(c for c in json.load(f).get("unlocked_chars", {}) if CJK(c))

    return chars


def resolve_tarball():
    with urllib.request.urlopen(REGISTRY_URL, timeout=60) as resp:
        meta = json.load(resp)
    version = meta["dist-tags"]["latest"]
    return version, meta["versions"][version]["dist"]["tarball"]


def build(wanted=None):
    version, tarball_url = resolve_tarball()
    print(f"Fetching {PACKAGE}@{version} ...")
    with urllib.request.urlopen(tarball_url, timeout=300) as resp:
        raw = resp.read()
    print(f"  downloaded {len(raw) / 1024 / 1024:.1f} MB")

    data = {}
    with tarfile.open(fileobj=io.BytesIO(raw), mode="r:gz") as tar:
        for member in tar:
            if not member.isfile() or not member.name.endswith(".json"):
                continue
            char = os.path.basename(member.name)[:-5]
            # Package ships one <char>.json per character at its root, plus
            # metadata files (package.json etc.) that aren't characters.
            if len(char) != 1 or not CJK(char):
                continue
            if wanted is not None and char not in wanted:
                continue
            extracted = tar.extractfile(member)
            if extracted is None:
                continue
            data[char] = json.loads(extracted.read().decode("utf-8"))

    return data, version


def main():
    parser = argparse.ArgumentParser(description="Vendor Hanzi Writer stroke data for offline use.")
    parser.add_argument(
        "--all", action="store_true",
        help="Vendor every character the upstream package has, not just the ones this app can serve.",
    )
    args = parser.parse_args()

    wanted = None if args.all else needed_characters()
    if wanted is not None:
        print(f"Scoped to {len(wanted)} characters the app can present.")

    data, version = build(wanted)

    with open(OUTPUT_PATH, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, separators=(",", ":"))

    size_mb = os.path.getsize(OUTPUT_PATH) / 1024 / 1024
    print(f"Wrote {len(data)} characters to {OUTPUT_PATH} ({size_mb:.1f} MB) from {PACKAGE}@{version}.")

    if wanted is not None:
        missing = wanted - set(data)
        if missing:
            print(
                f"{len(missing)} character(s) have no upstream stroke data and will fall back to "
                f"the CDN (and show a readable error offline): {''.join(sorted(missing))}"
            )


if __name__ == "__main__":
    main()
