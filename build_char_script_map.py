#!/usr/bin/env python3
"""
Builds char_script_map.json -- a single-character simplified -> traditional
mapping used to render a practice item's writable text in Traditional Chinese
when settings.character_script is "traditional" -- from the character-level
conversion table bundled in opencc-python-reimplemented (Apache 2.0).

WHY A CHARACTER-LEVEL MAP, NOT A REAL OPENCC RUNTIME DEPENDENCY
-----------------------------------------------------------------
OpenCC's *phrase*-level dictionaries pick the contextually-correct traditional
form for characters with more than one traditional counterpart (simplified 干
-> 幹/乾/干 depending on meaning). This app deliberately does not take that on
as a runtime dependency: juzi_engine.py/server.py have zero third-party
imports today (stdlib only), and the phrase dictionaries are large. Instead,
this is a one-time build step (same shape as build_pinyin_readings.py's use of
pypinyin) that vendors just the character-level table -- first-listed
candidate only, no phrase disambiguation -- as a small tracked JSON file
loaded with plain `json.load` at runtime, same as pinyin_readings.json.

This is a real, accepted limitation: a character with several traditional
forms sometimes gets the wrong one out of context (e.g. a name using an
uncommon 乾 reading might render as 干's more common 幹). Good enough for
"write this character correctly," which is what stroke practice tests; not a
claim of publishing-grade Traditional Chinese conversion.

WHAT IT WRITES
--------------
char_script_map.json, tracked: {simplified_char: traditional_char}, one entry
per character that actually differs between scripts (~4k entries -- a
character identical in both scripts, e.g. 你/好, is simply absent, and
lookups fall back to the original character for anything not in the map).

Usage:
    python3 build_char_script_map.py
"""
import io
import json
import urllib.request
import zipfile

PYPI_JSON = "https://pypi.org/pypi/opencc-python-reimplemented/json"
OUTPUT = "char_script_map.json"
LICENSE_OUTPUT = "opencc_source.LICENSE.txt"
SOURCE_FILE = "opencc/dictionary/STCharacters.txt"


def fetch_wheel():
    """Downloads the current opencc-python-reimplemented wheel."""
    with urllib.request.urlopen(PYPI_JSON, timeout=60) as r:
        meta = json.load(r)
    version = meta["info"]["version"]
    license_name = meta["info"]["license"] or "Apache License"
    home_page = meta["info"]["home_page"] or meta["info"]["project_url"] or ""
    url = next(u["url"] for u in meta["urls"] if u["packagetype"] == "bdist_wheel")
    print(f"Fetching opencc-python-reimplemented {version} ...")
    with urllib.request.urlopen(url, timeout=180) as r:
        data = r.read()
    print(f"  {len(data) / 1024:.0f} KB")
    return data, version, license_name, home_page


def parse_st_characters(raw: bytes) -> dict:
    """
    STCharacters.txt is tab-separated: `simplified<TAB>trad1 trad2 ...`, one
    line per character that has at least one distinct traditional form. The
    first candidate is OpenCC's most-common choice (see module docstring for
    why later candidates -- the context-disambiguated ones -- are dropped).
    """
    mapping = {}
    for line in raw.decode("utf-8").splitlines():
        if not line.strip():
            continue
        simp, _, trads = line.partition("\t")
        if not simp or not trads:
            continue
        mapping[simp] = trads.split(" ", 1)[0]
    return mapping


def main():
    wheel_bytes, version, license_name, home_page = fetch_wheel()
    with zipfile.ZipFile(io.BytesIO(wheel_bytes)) as zf:
        raw = zf.read(SOURCE_FILE)

    mapping = parse_st_characters(raw)
    print(f"  {len(mapping)} characters differ between scripts")

    with open(OUTPUT, "w", encoding="utf-8") as f:
        json.dump(mapping, f, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
    print(f"Wrote {OUTPUT}")

    with open(LICENSE_OUTPUT, "w", encoding="utf-8") as f:
        f.write(
            f"{OUTPUT} is derived from {SOURCE_FILE} in "
            f"opencc-python-reimplemented {version}, {license_name}.\n"
            f"Source: {home_page or 'https://github.com/yichen0831/opencc-python'}\n"
            "Only the character-level (non-phrase-context) mapping is used; see "
            "build_char_script_map.py's module docstring for what that trades away.\n"
        )
    print(f"Wrote {LICENSE_OUTPUT}")


if __name__ == "__main__":
    main()
