"""
Sets which plan an account is on (see plans.py), or lists every account's plan.

Usage:
    python3 set_plan.py --username alice --plan paid
    python3 set_plan.py --name Barry --plan founding       # a link account, by registry name
    python3 set_plan.py --slug <id> --plan free --note "refunded"
    python3 set_plan.py --all-existing --plan founding --note "beta tester"
    python3 set_plan.py --list

    # A bought subscription, recorded by hand until checkout records its own:
    python3 set_plan.py --username alice --billing annual --status active --period-end 2027-09-15
    python3 set_plan.py --username alice --billing annual --status canceled --period-end 2026-09-01
    python3 set_plan.py --username alice --billing lifetime

--billing records a subscription (plans.record_subscription), which puts the
account on the paid plan; whether that still gives full access then follows
--status (default active) and --period-end. That makes every state, a lapsed
account included, possible to try before real payments exist. --plan without
--billing records a plan with no subscription, so paid set that way is a grant
that never runs out.

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
from plans import (ACTIVE, BILLING_PERIODS, PAID, PLAN_NAMES, SUBSCRIPTION_STATUSES,
                   access_for, entry_for, load_plans, record_subscription, save_plans,
                   set_plan)
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


def describe(entry):
    """An entry's plan in effect, with its subscription if it has one."""
    plan, lapsed = access_for(entry)
    subscription = entry.get("subscription")
    if not isinstance(subscription, dict):
        return plan
    details = [subscription.get("billing", "?"), subscription.get("status", "?")]
    if subscription.get("current_period_end"):
        details.append(f"period ends {subscription['current_period_end'][:10]}")
    if lapsed:
        details.append("lapsed")
    return f"{plan} ({', '.join(details)})"


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
    parser.add_argument("--billing", choices=BILLING_PERIODS,
                        help="Record a bought subscription with this billing period.")
    parser.add_argument("--status", choices=SUBSCRIPTION_STATUSES,
                        help="The subscription's status (default active). Needs --billing.")
    parser.add_argument("--period-end",
                        help="When the paid period ends, as an ISO date or time. Needs --billing.")
    parser.add_argument("--note", help="Why, for the record (for example 'beta tester').")
    args = parser.parse_args()

    labels = account_labels()
    plans = load_plans()

    if args.list:
        for account_id, label in sorted(labels.items(), key=lambda item: item[1].lower()):
            plan, _lapsed = access_for(entry_for(account_id, plans))
            detail = describe(entry_for(account_id, plans))
            print(f"  {plan:<9} {label}{detail[len(plan):]}")
        return

    if args.billing:
        if args.all_existing:
            parser.error("--billing records one account's subscription; it can't be used with --all-existing.")
        if args.plan and args.plan != PAID:
            parser.error("A subscription is always on the paid plan; leave out --plan or use --plan paid.")
    elif args.status or args.period_end:
        parser.error("--status and --period-end describe a subscription, so they need --billing.")
    elif not args.plan:
        parser.error("--plan or --billing is required.")

    if args.all_existing:
        targets = [account_id for account_id in labels if account_id not in plans]
        for account_id in targets:
            set_plan(plans, account_id, args.plan, "operator", args.note)
        save_plans(plans)
        print(f"Set {len(targets)} account(s) to {args.plan}; "
              f"{len(labels) - len(targets)} already had a plan and were left alone.")
        return

    account_id = resolve_account(parser, args)
    before = describe(entry_for(account_id, plans))
    if args.billing:
        try:
            record_subscription(plans, account_id, args.billing, args.status or ACTIVE,
                                args.period_end, source="operator", note=args.note)
        except ValueError as bad:
            parser.error(str(bad))
    else:
        set_plan(plans, account_id, args.plan, "operator", args.note)
    save_plans(plans)
    print(f"{labels.get(account_id, 'That account')}: {before} -> {describe(entry_for(account_id, plans))}")


if __name__ == "__main__":
    main()
