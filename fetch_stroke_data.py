"""
Builds stroke_data.json -- the local, offline stroke-order database that
Hanzi Writer draws from.

By default Hanzi Writer fetches every character's stroke data from
cdn.jsdelivr.net at the moment you're asked to write it, which made
handwriting -- the whole point of this app -- silently require an internet
connection, on the tablet it was built to run on. This script pulls that data
down once so normal play really is offline.

The generated stroke_data.json is committed to the repo rather than ignored,
so a fresh clone is offline-capable with no build step. You only need to run
this again to widen the character set (see --all below) or to rebuild the
file from scratch.

It vendors only the characters JuziGenius can actually put in front of you:
every character in the words_freq.json vocabulary corpus, in the local
sentence corpora (SENTENCE_SOURCE_FILES -- the hand-curated HSK sentences plus
the larger Tatoeba-derived set), and in your current brain.json. That's a few
thousand characters (a few MB) rather than the full ~9,600-character, 31 MB
upstream package. Characters outside that set -- rare or variant forms you
might paste in via text import -- still fall back to the CDN when you're
online, and surface a readable error instead of a dead canvas when you aren't
(see charDataLoader in app.js).

Source: the hanzi-writer-data npm package (MIT). Fetched as a single tarball
rather than thousands of individual requests.

Usage:
    python3 fetch_stroke_data.py
    python3 fetch_stroke_data.py --all      # vendor every available character
"""
import argparse
import csv
import hashlib
import io
import json
import os
import tarfile
import urllib.request

from juzi_engine import SENTENCE_SOURCE_FILES

WORDS_PATH = "words_freq.json"
BRAIN_PATH = "brain.json"
MASTER_DICT_PATH = "master_dictionary.json"
OUTPUT_PATH = "stroke_data.json"
INDEX_PATH = "stroke_data.index.json"

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

    for filename in SENTENCE_SOURCE_FILES:
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


def file_digest(path, chunk_size=1 << 20):
    """sha256 of a file, read in chunks so a 29 MB file costs 1 MB of memory."""
    digest = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(chunk_size), b""):
            digest.update(block)
    return digest.hexdigest()


def write_with_index(data, output_path, index_path):
    """
    Writes stroke_data.json and, beside it, a byte-offset index into it.

    Finding 20: server.py used to parse the whole 29 MB file into one dict on
    the first /api/strokes request and hold it for the process lifetime --
    137 MB resident, 195 MB peak, to serve what are only ever single-key
    lookups. Irrelevant on a dev box, a permanent fixed cost per process on
    the small VPS this deploys to. With this index the server reads one ~3 KB
    byte span per character and never materialises the rest.

    The offsets are recorded *while writing*, not recovered by scanning
    afterwards, so they are exact by construction -- there is no parser to get
    subtly wrong, and no startup scan to pay for (a brace-depth scanner over
    this file measured 2.8 s). The output is byte-identical to the
    json.dump(..., separators=(",", ":")) this replaces.

    The index carries the size and sha256 of the file it describes, so
    server.py can refuse a stale index rather than serving one character's
    strokes under another character's name -- the kind of corruption that is
    very hard to diagnose from the symptom.
    """
    offsets = {}
    with open(output_path, "wb") as f:
        f.write(b"{")
        for i, (char, entry) in enumerate(data.items()):
            if i:
                f.write(b",")
            f.write(json.dumps(char, ensure_ascii=False).encode("utf-8"))
            f.write(b":")
            blob = json.dumps(entry, ensure_ascii=False,
                              separators=(",", ":")).encode("utf-8")
            offsets[char] = [f.tell(), len(blob)]
            f.write(blob)
        f.write(b"}")

    index = {
        "source": os.path.basename(output_path),
        "source_bytes": os.path.getsize(output_path),
        "source_sha256": file_digest(output_path),
        "entries": offsets,
    }
    with open(index_path, "w", encoding="utf-8") as f:
        json.dump(index, f, ensure_ascii=False, separators=(",", ":"))
    return index


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

    write_with_index(data, OUTPUT_PATH, INDEX_PATH)

    size_mb = os.path.getsize(OUTPUT_PATH) / 1024 / 1024
    index_kb = os.path.getsize(INDEX_PATH) / 1024
    print(f"Wrote {len(data)} characters to {OUTPUT_PATH} ({size_mb:.1f} MB) from {PACKAGE}@{version}.")
    print(f"Wrote byte-offset index to {INDEX_PATH} ({index_kb:.0f} KB) -- "
          f"commit both together; server.py rejects an index whose checksum "
          f"doesn't match the data file.")

    if wanted is not None:
        missing = wanted - set(data)
        if missing:
            print(
                f"{len(missing)} character(s) have no upstream stroke data and will fall back to "
                f"the CDN (and show a readable error offline): {''.join(sorted(missing))}"
            )


if __name__ == "__main__":
    main()
