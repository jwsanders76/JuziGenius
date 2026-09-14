"""
Manually sets a new password for an existing real-login account, by
username. Self-service reset by email (POST /api/password/forgot) covers any
account with a confirmed address; this is the operator's fallback for one
without. Mirrors reset_user.py's --name ergonomics, but only
touches users/accounts.json: it never resets or reseeds the account's own
brain.json, so a friend's SRS progress is completely untouched.

Bumps the account's session_version (see accounts.set_password), so any
session cookie issued before this reset -- including one an attacker who
guessed the old password might be holding -- stops working immediately.

Usage:
    python3 set_password.py --username alice
"""
import argparse
import getpass

from accounts import load_accounts, save_accounts, set_password, validate_password


def main():
    parser = argparse.ArgumentParser(
        description="Set a new password for an existing real-login account."
    )
    parser.add_argument("--username", required=True, help="Account to reset.")
    args = parser.parse_args()

    accounts = load_accounts()
    if args.username.lower() not in accounts:
        parser.error(f"No account named {args.username!r} in users/accounts.json.")

    new_password = getpass.getpass("New password: ")
    confirm = getpass.getpass("Confirm new password: ")
    if new_password != confirm:
        parser.error("Passwords did not match.")

    try:
        # The same check signup and the emailed reset link enforce.
        validate_password(new_password)
    except ValueError as bad:
        parser.error(str(bad))

    set_password(accounts, args.username, new_password)
    save_accounts(accounts)

    print(f"Password updated for {args.username}. Every session they were previously "
          "logged in on has been signed out; they'll need to log in again.")


if __name__ == "__main__":
    main()
