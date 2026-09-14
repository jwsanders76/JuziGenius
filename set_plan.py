"""
Sets which plan an account is on (see plans.py), or lists every account's plan.

Usage:
    python3 set_plan.py --username alice --plan paid
    python3 set_plan.py --name Barry --plan founding       # a link account, by registry name
    python3 set_plan.py --slug <id> --plan free --note "refunded"
    python3 set_plan.py --all-existing --plan founding --note "beta tester"
    python3 set_plan.py --list

--all-existing gives the plan to every account that exists right now and has
no plan recorded yet -- how the beta testers became founding members before
the free-plan limits went live. An account that already has a plan is left
alone, so running it twice changes nothing and never downgrades anyone.

Safe while the service is running: the server reads plans.json on every
request, and writes are atomic. Output names login accounts by username and
link accounts by their registry name, never by id -- a link account's id is
its password.
"""
import argparse
import os

from accounts import load_accounts
from plans import PLAN_NAMES, load_plans, plan_for, save_plans, set_plan
from user_registry import USERS_DIR, find_by_name, load_registry


def account_labels():
    """{account_id: human-readable label} for every account under users/."""
    labels = {}
    for username, entry in load_accounts().items():
        labels[entry["user_id"]] = f"login {entry.get('display_name', username)}"
    for slug, entry in load_registry().items():
        # A claimed link account is already labelled by its login above.
        labels.setdefault(slug, f"link account {entry.get('name', '(unnamed)')!r}")
    return {account_id: label for account_id, label in labels.items()
            if os.path.isdir(os.path.join(USERS_DIR, account_id))}


def resolve_account(parser, args):
    """The account id named by --username, --name or --slug."""
    if args.username:
        entry = load_accounts().get(args.username.lower())
        if entry is None:
            parser.error(f"No login account named {args.username!r}.")
        account_id = entry["user_id"]
    elif args.name:
        matches = find_by_name(load_registry(), args.name)
        if not matches:
            parser.error(f"No link account named {args.name!r} in users/registry.json.")
        if len(matches) > 1:
            parser.error(f"{len(matches)} link accounts are named {args.name!r}; use --slug.")
        account_id = matches[0]
    else:
        account_id = args.slug

    if not os.path.isdir(os.path.join(USERS_DIR, account_id)):
        parser.error("No such account under users/.")
    return account_id


def main():
    parser = argparse.ArgumentParser(description="Set or list account plans.")
    who = parser.add_mutually_exclusive_group(required=True)
    who.add_argument("--username", help="A login account's username.")
    who.add_argument("--name", help="A link account's name in users/registry.json.")
    who.add_argument("--slug", help="The account's directory name under users/.")
    who.add_argument("--all-existing", action="store_true",
                     help="Every existing account that has no plan recorded yet.")
    who.add_argument("--list", action="store_true", help="Show every account's plan.")
    parser.add_argument("--plan", choices=PLAN_NAMES)
    parser.add_argument("--note", help="Why, for the record (for example 'beta tester').")
    args = parser.parse_args()

    labels = account_labels()
    plans = load_plans()

    if args.list:
        for account_id, label in sorted(labels.items(), key=lambda item: item[1].lower()):
            print(f"  {plan_for(account_id, plans):<9} {label}")
        return

    if not args.plan:
        parser.error("--plan is required.")

    if args.all_existing:
        targets = [account_id for account_id in labels if account_id not in plans]
        for account_id in targets:
            set_plan(plans, account_id, args.plan, "operator", args.note)
        save_plans(plans)
        print(f"Set {len(targets)} account(s) to {args.plan}; "
              f"{len(labels) - len(targets)} already had a plan and were left alone.")
        return

    account_id = resolve_account(parser, args)
    before = plan_for(account_id, plans)
    set_plan(plans, account_id, args.plan, "operator", args.note)
    save_plans(plans)
    print(f"{labels.get(account_id, 'That account')}: {before} -> {args.plan}")


if __name__ == "__main__":
    main()
