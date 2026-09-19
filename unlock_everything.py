"""
Unlocks every character and every compound word in one account, so a tester
can dig into the whole dictionary without unlocking it a few at a time.

Usage:
    python3 unlock_everything.py --username alice            # dry run: counts only
    python3 unlock_everything.py --username alice --apply
    python3 unlock_everything.py --name Barry --apply         # a link account, by registry name
    python3 unlock_everything.py --slug <id> --apply

Unlocking is not the same as being allowed to. A free or lapsed account still
has its plan's limits (plans.py) and would hold everything outside HSK 1, so
give the account a plan with full access first (create_invite.py --plan founding,
or set_plan.py).

What it does, and does not:
* Adds every character in master_dictionary.json and every compound word in
  words_freq.json that the account doesn't already have, through the engine's
  own add_characters / add_words, so entries look exactly like ones unlocked
  by hand: never graded, `interval` 0, `factor` 2.5.
* Only ever adds. An existing entry keeps its SM-2 progress untouched, and
  nothing else in brain.json (settings, sentences, completed sentences) changes.
* Sentences are not stored as unlocked: once every character is unlocked all
  the corpus sentences are playable, and Import -> HSK Sentences -> Get
  Sentences pulls them into the Sentence Bank.
* The daily new-character limit (settings.daily_new_limit) still caps how many
  never-graded characters are served per sitting, so nothing floods.

Before writing, the current brain.json is kept in brain-history (always, not
only when the last snapshot is old), so restore_brain.py can undo it. Refuses
while the service runs, like restore_brain.py: a practice session saving in
the middle would overwrite the result.

Output names a login account by username and a link account by its registry
name, never by id -- a link account's id is its password.
"""
import argparse
import json
import os
import shutil
import subprocess

from accounts import load_accounts
from brain_history import snapshot
from juzi_engine import JuziEngine
from user_registry import USERS_DIR, find_by_name, load_registry

SERVICE_NAME = "juzigenius"


def service_running():
    """True if systemd reports the app running. False where there is no systemd (a dev machine)."""
    if not shutil.which("systemctl"):
        return False
    return subprocess.run(["systemctl", "is-active", "--quiet", SERVICE_NAME]).returncode == 0


def resolve_account(parser, args):
    """(account_id, label) for the account named by --username, --name or --slug."""
    if args.username:
        entry = load_accounts().get(args.username.lower())
        if entry is None:
            parser.error(f"No login account named {args.username!r}.")
        account_id, label = entry["user_id"], f"login {args.username}"
    elif args.name:
        matches = find_by_name(load_registry(), args.name)
        if not matches:
            parser.error(f"No link account named {args.name!r} in users/registry.json.")
        if len(matches) > 1:
            parser.error(f"{len(matches)} link accounts are named {args.name!r}; use --slug.")
        account_id, label = matches[0], f"link account {args.name!r}"
    else:
        account_id, label = args.slug, "the account given by --slug"

    if not os.path.isfile(os.path.join(USERS_DIR, account_id, "brain.json")):
        parser.error("No such account under users/.")
    return account_id, label


def real_words(word_db):
    """The compound words in words_freq.json: skips `_` notice keys and single characters."""
    return [word for word, meta in word_db.items()
            if not word.startswith("_") and isinstance(meta, dict) and len(word) >= 2]


def main():
    parser = argparse.ArgumentParser(description="Unlock every character and word in one account.")
    who = parser.add_mutually_exclusive_group(required=True)
    who.add_argument("--username", help="A login account's username.")
    who.add_argument("--name", help="A link account's name in users/registry.json.")
    who.add_argument("--slug", help="The account's directory name under users/.")
    parser.add_argument("--apply", action="store_true",
                        help="Actually write the changes. Without it, only report what would change.")
    args = parser.parse_args()

    account_id, label = resolve_account(parser, args)
    brain_path = os.path.join(USERS_DIR, account_id, "brain.json")
    engine = JuziEngine(brain_path=brain_path)
    master = engine.load_master_dictionary()
    words = real_words(engine.load_word_frequencies())

    with open(brain_path, "r", encoding="utf-8") as f:
        brain = json.load(f)
    have_chars = brain.get("unlocked_chars") or {}
    have_words = brain.get("unlocked_words") or {}
    new_chars = [c for c in master if c not in have_chars]
    new_words = [w for w in words if w not in have_words]

    print(f"{label}: {len(have_chars)} characters and {len(have_words)} words unlocked now.")
    print(f"  would add {len(new_chars)} characters and {len(new_words)} words "
          f"(leaving {len(master)} characters and {len(words)} words in total).")

    if not args.apply:
        print("Dry run. Nothing was written; add --apply to do it.")
        return

    if service_running():
        parser.error(f"The {SERVICE_NAME} service is running, and a practice session saving now could "
                     f"overwrite this. Stop it first: sudo systemctl stop {SERVICE_NAME}")

    if not snapshot(brain_path, force=True):
        parser.error("Could not keep a snapshot of the current brain.json, so nothing was changed.")

    engine.add_characters(list(master))
    engine.add_words(words)

    with open(brain_path, "r", encoding="utf-8") as f:
        after = json.load(f)
    print(f"Done: {len(after['unlocked_chars'])} characters and {len(after['unlocked_words'])} words unlocked.")
    print("The state before is kept in brain-history; restore_brain.py undoes it.")
    if shutil.which("systemctl"):
        print(f"Start the service again: sudo systemctl start {SERVICE_NAME}")


if __name__ == "__main__":
    main()
