"""
Mints one single-use invite code for the real username/password signup flow
(POST /api/signup, see server.py and login.html) and prints it for the
operator to send privately -- the same "run a script, hand someone the
output" ergonomics as create_user.py's slug links, just authorizing a
self-service signup instead of provisioning the account directly.

Usage:
    python3 create_invite.py
    python3 create_invite.py --plan founding    # the account starts with everything unlocked

Without --plan the account starts on the free plan, HSK 1 (see plans.py).
"""
import argparse

from invites import create_invite_code
from plans import FOUNDING, PAID


def main():
    parser = argparse.ArgumentParser(description="Mint one single-use signup invite code.")
    parser.add_argument("--plan", choices=(FOUNDING, PAID),
                        help="Start the account this code creates on this plan instead of free.")
    args = parser.parse_args()

    code = create_invite_code(plan=args.plan)
    print(f"Invite code: {code}")
    if args.plan:
        print(f"The account it creates starts on the {args.plan} plan.")
    print("Send it privately. It's good for exactly one signup at https://juzigenius.com/login")


if __name__ == "__main__":
    main()
