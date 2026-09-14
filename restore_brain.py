"""
Lists and restores the save history kept for one account's brain.json (see
brain_history.py): a version from every ten minutes or so of practice over
the last two days, plus the last few whatever their age.

Listing is read-only and safe any time. Restoring replaces the account's
current brain.json, so it refuses to run while the service is up -- a
practice session saving in the middle would overwrite the restore. The state
being replaced is kept as a snapshot first, so a restore can itself be undone
the same way.

Usage:
    python3 restore_brain.py --username alice                  # list
    python3 restore_brain.py --name Barry                      # a link account, by registry name
    python3 restore_brain.py --slug <id>                       # by directory name
    python3 restore_brain.py --username alice --restore 20260914T031500Z

On the droplet:
    sudo systemctl stop juzigenius
    python3 restore_brain.py --username alice --restore <timestamp>
    sudo systemctl start juzigenius
"""
import argparse
import json
import os
import shutil
import subprocess
from datetime import datetime, timezone

import brain_history
from accounts import load_accounts
from atomic_io import write_json
from user_registry import USERS_DIR, find_by_name, load_registry

SERVICE_NAME = "juzigenius"


def service_running():
    """True if systemd reports the app running. False where there is no systemd (a dev machine)."""
    if not shutil.which("systemctl"):
        return False
    return subprocess.run(["systemctl", "is-active", "--quiet", SERVICE_NAME]).returncode == 0


def resolve_account(parser, args):
    """(label, brain.json path) for whichever identifier was given."""
    if args.username:
        entry = load_accounts().get(args.username.lower())
        if entry is None:
            parser.error(f"No login account named {args.username!r}.")
        account_id, label = entry["user_id"], entry.get("display_name", args.username)
    elif args.name:
        matches = find_by_name(load_registry(), args.name)
        if not matches:
            parser.error(f"No link account named {args.name!r} in users/registry.json.")
        if len(matches) > 1:
            parser.error(f"{len(matches)} link accounts are named {args.name!r}; use --slug.")
        account_id, label = matches[0], args.name
    else:
        account_id, label = args.slug, "that account"

    path = os.path.join(USERS_DIR, account_id, "brain.json")
    if not os.path.isfile(path):
        parser.error(f"{path} doesn't exist.")
    return label, path


def describe(path):
    """A short summary of one brain file, to help pick a restore point."""
    try:
        with open(path, "r", encoding="utf-8") as f:
            brain = json.load(f)
    except (OSError, ValueError):
        return "unreadable"
    chars = brain.get("unlocked_chars", {})
    reviewed = sum(1 for v in chars.values() if isinstance(v, dict) and v.get("last"))
    return (f"{len(chars)} characters ({reviewed} reviewed), "
            f"{len(brain.get('unlocked_words', {}))} words, "
            f"{len(brain.get('pasted_sentences', []))} saved sentences")


def age(saved_at, now):
    minutes = int((now - saved_at).total_seconds() // 60)
    if minutes < 60:
        return f"{minutes} min ago"
    if minutes < 48 * 60:
        return f"{minutes // 60} h {minutes % 60:02d} min ago"
    return f"{minutes // (24 * 60)} days ago"


def main():
    parser = argparse.ArgumentParser(description="List or restore an account's brain.json save history.")
    who = parser.add_mutually_exclusive_group(required=True)
    who.add_argument("--username", help="A login account's username.")
    who.add_argument("--name", help="A link account's name in users/registry.json.")
    who.add_argument("--slug", help="The account's directory name under users/.")
    parser.add_argument("--restore", metavar="TIMESTAMP",
                        help="Restore the snapshot with this timestamp (as listed).")
    args = parser.parse_args()

    label, path = resolve_account(parser, args)
    now = datetime.now(timezone.utc)
    snapshots = brain_history.list_snapshots(path)

    if not args.restore:
        current_saved = datetime.fromtimestamp(os.path.getmtime(path), timezone.utc)
        print(f"Save history for {label}:")
        print(f"  current           saved {current_saved:%Y-%m-%d %H:%M} UTC ({age(current_saved, now)})  "
              f"{describe(path)}")
        if not snapshots:
            print("  (no snapshots yet -- one is kept on the first save more than ten minutes after the last)")
        for saved_at, snapshot_path in reversed(snapshots):
            print(f"  {brain_history.stamp(saved_at)}  saved {saved_at:%Y-%m-%d %H:%M} UTC "
                  f"({age(saved_at, now)})  {describe(snapshot_path)}")
        return

    chosen = next((p for saved_at, p in snapshots if brain_history.stamp(saved_at) == args.restore), None)
    if chosen is None:
        parser.error(f"No snapshot {args.restore!r} for {label}. Run without --restore to list them.")
    if service_running():
        parser.error(f"The {SERVICE_NAME} service is running, and a practice session saving now would "
                     f"overwrite the restore. Stop it first: sudo systemctl stop {SERVICE_NAME}")
    try:
        with open(chosen, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError) as e:
        parser.error(f"That snapshot can't be read ({type(e).__name__}); pick another.")
    if not isinstance(data, dict):
        parser.error("That snapshot isn't a brain.json; pick another.")

    kept = brain_history.snapshot(path, force=True)
    if kept is None:
        parser.error("Couldn't keep a snapshot of the current state first, so nothing was changed.")
    write_json(path, data)

    kept_stamp = os.path.basename(kept)[len("brain-"):-len(".json")]
    print(f"Restored {label} to the version saved {args.restore}: {describe(path)}.")
    print(f"The state it replaced is kept as {kept_stamp}; restore that the same way to undo this.")
    if shutil.which("systemctl"):
        print(f"Start the service again: sudo systemctl start {SERVICE_NAME}")


if __name__ == "__main__":
    main()
