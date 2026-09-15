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

WHY A STANDARD-GLYPH CORRECTION PASS
-------------------------------------
STCharacters.txt's first candidate is sometimes a rare historical/Kangxi
variant rather than the glyph actually printed in Taiwan or Hong Kong
Traditional text -- e.g. simplified 为 maps to 爲, not the far more common 為.
Both are legitimate "traditional Chinese," but a learner studying Traditional
script expects the one that's actually in use, and stroke practice made it
worse: the vendored stroke set (fetch_stroke_data.py, from hanzi-writer-data)
doesn't cover these rare variants at all, so writing practice hit a dead "no
stroke data for this character" error for them (found via a user report).
OpenCC ships exactly the correction table this needs -- TWVariants.txt and
HKVariants.txt map a variant glyph to its Taiwan/Hong-Kong standard form. This
is still a one-time build step, not a runtime dependency; see the module
docstring above.

WHY EVERY PICK MUST HAVE STROKE DATA
------------------------------------
Applying those tables blindly swapped some glyphs the stroke set does cover
for ones it doesn't. HKVariants maps 說 to 説, so simplified 说 (HSK 1) was
shown to Traditional learners as 説 and hit the same dead "no stroke data"
error (a second user report, September 14, 2026); 卫, 阅, 叙, 锐, 哗, 蕴 and 酝
had the same problem. So each simplified character's candidates are tried in
the same preference order as before -- the Taiwan standard form of OpenCC's
first traditional form, its Hong Kong standard form, that first form itself,
then OpenCC's other traditional forms -- and the first one stroke_data.json
can draw wins. Keeping the order means only the undrawable picks change. A character with no drawable traditional form
at all (all rare: 绫 -> 綾, 疡 -> 瘍, ...) gets no entry, so a Traditional
learner writes its simplified form instead of meeting a character they can't
write. The build refuses to write a map containing any glyph without stroke
data, so rerun it after fetch_stroke_data.py changes the stroke set.

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
TW_VARIANTS_FILE = "opencc/dictionary/TWVariants.txt"
HK_VARIANTS_FILE = "opencc/dictionary/HKVariants.txt"


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


STROKE_INDEX = "stroke_data.index.json"


def parse_candidates(raw: bytes) -> dict:
    """
    STCharacters.txt is tab-separated: `simplified<TAB>trad1 trad2 ...`, one
    line per character that has at least one traditional form, most common
    first. The variant files (TWVariants.txt, HKVariants.txt) have the same
    shape: `variant<TAB>standard candidate(s)`. Returns every candidate, in
    order, so a pick can skip ones the stroke set can't draw.
    """
    candidates = {}
    for line in raw.decode("utf-8").splitlines():
        if not line.strip():
            continue
        key, _, values = line.partition("\t")
        if not key or not values.strip():
            continue
        candidates[key] = values.split()
    return candidates


def load_drawable() -> set:
    """Every character stroke_data.json has strokes for, from its index."""
    with open(STROKE_INDEX, encoding="utf-8") as f:
        return set(json.load(f)["entries"])


def pick_traditional(trads: list, tw_variants: dict, hk_variants: dict, drawable: set):
    """
    The traditional form to show, or None when no candidate can be drawn:
    the Taiwan standard form of OpenCC's first choice, its Hong Kong standard
    form, that first choice itself, then OpenCC's other candidates.
    """
    first = trads[0]
    order = [*tw_variants.get(first, [])[:1], *hk_variants.get(first, [])[:1], first, *trads[1:]]
    return next((c for c in order if c in drawable), None)


def main():
    wheel_bytes, version, license_name, home_page = fetch_wheel()
    with zipfile.ZipFile(io.BytesIO(wheel_bytes)) as zf:
        st_characters = parse_candidates(zf.read(SOURCE_FILE))
        tw_variants = parse_candidates(zf.read(TW_VARIANTS_FILE))
        hk_variants = parse_candidates(zf.read(HK_VARIANTS_FILE))
    drawable = load_drawable()
    print(f"  {len(st_characters)} characters have a traditional form; "
          f"{len(drawable)} characters have stroke data")

    mapping = {}
    for simp, trads in st_characters.items():
        trad = pick_traditional(trads, tw_variants, hk_variants, drawable)
        if trad is not None:
            mapping[simp] = trad
    print(f"  {len(mapping)} mapped; {len(st_characters) - len(mapping)} left simplified "
          "because no traditional form has stroke data")

    undrawable = sorted(t for t in mapping.values() if t not in drawable)
    if undrawable:
        raise SystemExit(f"Refusing to write {OUTPUT}: no stroke data for {''.join(undrawable)}")

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
