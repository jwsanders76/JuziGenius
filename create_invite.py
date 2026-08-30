"""
Mints one single-use invite code for the real username/password signup flow
(POST /api/signup, see server.py and login.html) and prints it for the
operator to send privately -- the same "run a script, hand someone the
output" ergonomics as create_user.py's slug links, just authorizing a
self-service signup instead of provisioning the account directly.

Usage:
    python3 create_invite.py
"""
from invites import create_invite_code


def main():
    code = create_invite_code()
    print(f"Invite code: {code}")
    print("Send it privately. It's good for exactly one signup at https://juzigenius.com/login")


if __name__ == "__main__":
    main()
