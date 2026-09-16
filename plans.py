"""
Which plan each account is on, and what that plan lets it do.

The free plan is HSK 1, decided September 13, 2026: a free account can unlock
only the characters the HSK 1 syllabus lists (master_dictionary.json's `hsk`
field, 174 characters), can start only from the two smallest onboarding tiers,
and can't paste its own text. That is about twelve days of practice at the
default fifteen new characters a day, with 759 corpus sentences writable from
it -- enough to feel the method work before being asked to pay, and a line
learners already understand. Paid and founding accounts have no limits.

Enforced on the server, never only in app.js: every route that unlocks a
character builds this account's Limits first (see server.py's _limits).
Reviews are never limited, and an account that loses full access keeps every
character it has practised.

users/plans.json, gitignored and backed up with the rest of users/:
    {account_id: {"plan": "paid" | "founding" | "free",
                  "since": ISO time,
                  "source": "operator" | "invite" | "subscription",
                  "note": optional,
                  "subscription": optional, below}}

Keyed by the users/<id>/ directory name, which is the same for both account
systems, so a /u/<slug>/ link account keeps its plan when it's claimed as a
login (the slug becomes the user_id). An account with no entry is free.
Founding members are the beta testers from before paid plans existed; set
with set_plan.py.

A paid plan with a "subscription" is one somebody bought, and whether it
still gives full access follows that record rather than the plan name:

    {"billing": "monthly" | "annual" | "lifetime",
     "status": "active" | "past_due" | "paused" | "canceled",
     "current_period_end": ISO time, or null for lifetime,
     "updated": ISO time,
     "provider", "subscription_id", "customer_id": optional, the payment
         provider's own references, never sent to app.js}

Nothing records these from a payment provider yet. record_subscription is the
one function a checkout webhook will call, and set_plan.py uses it today so
every state can be tried by hand. A paid plan without a subscription is one
the operator granted, and it doesn't run out.

An account whose subscription has ended is "lapsed" (see access_for). It is
limited exactly like a free account and keeps everything it practised.
Characters beyond HSK 1 it unlocked but never practised wait for a paid plan
rather than being deleted -- juzi_engine's held_items -- which is what the
draft pricing page and Terms promise.
"""
import datetime
import os
import threading

from user_registry import USERS_DIR, load_index, save_index

PLANS_PATH = os.path.join(USERS_DIR, "plans.json")

FREE = "free"
PAID = "paid"
FOUNDING = "founding"
PLAN_NAMES = (FREE, PAID, FOUNDING)

MONTHLY = "monthly"
ANNUAL = "annual"
LIFETIME = "lifetime"
BILLING_PERIODS = (MONTHLY, ANNUAL, LIFETIME)

ACTIVE = "active"
PAST_DUE = "past_due"
PAUSED = "paused"
CANCELED = "canceled"
SUBSCRIPTION_STATUSES = (ACTIVE, PAST_DUE, PAUSED, CANCELED)

FREE_HSK_LEVEL = 1

# The onboarding tiers a free account may start from (seed_brain.TIER_INFO).
# The two larger tiers start well past HSK 1, so they come with paid plans.
FREE_TIER_SIZES = (5, 50)

# Serializes read-modify-write of plans.json inside the server process.
PLANS_LOCK = threading.Lock()


def load_plans():
    return load_index(PLANS_PATH)


def save_plans(plans):
    save_index(PLANS_PATH, plans)


def _now():
    return datetime.datetime.now(datetime.timezone.utc)


def _iso(moment):
    return moment.astimezone(datetime.timezone.utc).isoformat(timespec="seconds")


def parse_time(value):
    """
    An ISO date or time as an aware UTC datetime, or None if it isn't one. A
    bare date means midnight UTC. A trailing Z is accepted, as payment
    providers send it, though Python before 3.11 can't parse one itself.
    """
    if not isinstance(value, str) or not value.strip():
        return None
    text = value.strip()
    if text[-1] in "Zz":
        text = text[:-1] + "+00:00"
    try:
        moment = datetime.datetime.fromisoformat(text)
    except ValueError:
        return None
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=datetime.timezone.utc)
    return moment.astimezone(datetime.timezone.utc)


def entry_for(account_id, plans=None):
    """The account's plans.json entry, or {} when it has none."""
    if plans is None:
        plans = load_plans()
    entry = plans.get(account_id)
    return entry if isinstance(entry, dict) else {}


def plan_for(account_id, plans=None):
    """The account's recorded plan name: free when it has no entry, or an unknown one."""
    plan = entry_for(account_id, plans).get("plan")
    return plan if plan in PLAN_NAMES else FREE


def has_full_access(plan):
    return plan in (PAID, FOUNDING)


def subscription_active(subscription, now=None):
    """
    Whether a bought subscription still gives full access at `now`:

    * active: yes.
    * past_due: yes. The payment provider is still retrying a failed renewal,
      and the Terms keep access through that window. The provider cancels the
      subscription when it gives up, which ends access below.
    * paused or canceled: until current_period_end. Cancelling stops the next
      renewal, not the time already paid for.
    * lifetime: while active. A refunded lifetime purchase is recorded as
      canceled.

    Any other status is treated like canceled, so a malformed record runs
    out at its period end rather than granting access indefinitely.
    """
    if not isinstance(subscription, dict):
        return False
    status = subscription.get("status")
    if subscription.get("billing") == LIFETIME:
        return status == ACTIVE
    if status in (ACTIVE, PAST_DUE):
        return True
    end = parse_time(subscription.get("current_period_end"))
    return end is not None and (now or _now()) < end


def access_for(entry, now=None):
    """
    (plan, lapsed) in effect for a plans.json entry at `now`: the plan its
    limits follow, and whether that is because a bought subscription has
    ended. A paid plan whose subscription no longer gives access counts as
    free.
    """
    plan = entry.get("plan") if isinstance(entry, dict) else None
    if plan not in PLAN_NAMES:
        return FREE, False
    subscription = entry.get("subscription")
    if plan == PAID and isinstance(subscription, dict) and not subscription_active(subscription, now):
        return FREE, True
    return plan, False


def set_plan(plans, account_id, plan, source, note=None):
    """
    Records `plan` for `account_id` in `plans`, replacing any subscription
    record. The caller saves.
    """
    if plan not in PLAN_NAMES:
        raise ValueError(f"Unknown plan {plan!r}; expected one of {', '.join(PLAN_NAMES)}.")
    entry = {
        "plan": plan,
        "since": _iso(_now()),
        "source": source,
    }
    if note:
        entry["note"] = note
    plans[account_id] = entry
    return entry


def record_subscription(plans, account_id, billing, status, current_period_end=None,
                        source="subscription", note=None, provider=None,
                        subscription_id=None, customer_id=None,
                        cancel_scheduled_for=None):
    """
    Records a bought subscription for `account_id` in `plans`, on the paid
    plan. The caller saves. Every change a payment provider reports -- a
    renewal, a failed payment, a cancellation, a refund -- is this call with
    the new status and period end.

    Raises ValueError for an unknown billing period or status, or a monthly
    or annual subscription without a period end.
    """
    if billing not in BILLING_PERIODS:
        raise ValueError(f"Unknown billing period {billing!r}; expected one of {', '.join(BILLING_PERIODS)}.")
    if status not in SUBSCRIPTION_STATUSES:
        raise ValueError(f"Unknown status {status!r}; expected one of {', '.join(SUBSCRIPTION_STATUSES)}.")
    end = None
    if billing != LIFETIME:
        end = parse_time(current_period_end)
        if end is None:
            raise ValueError("A monthly or annual subscription needs a period end, as an ISO date or time.")

    previous = entry_for(account_id, plans)
    already_paid = previous.get("plan") == PAID
    subscription = {
        "billing": billing,
        "status": status,
        "current_period_end": _iso(end) if end else None,
        "updated": _iso(_now()),
    }
    # cancel_scheduled_for is why a subscriber who has already cancelled is
    # not told their plan is renewing. Paddle keeps a scheduled cancellation
    # as an active subscription with the intent recorded separately, so the
    # status alone can't tell the two apart, and without this the account
    # settings page said "renewing on" the very date the plan ends.
    for key, value in (("provider", provider), ("subscription_id", subscription_id),
                       ("customer_id", customer_id),
                       ("cancel_scheduled_for", _iso(parse_time(cancel_scheduled_for))
                        if parse_time(cancel_scheduled_for) else None)):
        if value:
            subscription[key] = value

    entry = {
        "plan": PAID,
        "since": previous.get("since") if already_paid and previous.get("since") else _iso(_now()),
        "source": source,
        "subscription": subscription,
    }
    note = note or (previous.get("note") if already_paid else None)
    if note:
        entry["note"] = note
    plans[account_id] = entry
    return entry


# (dictionary object, its HSK 1 characters). One slot is enough: every
# request asks, and recomputing for a different engine's copy is cheap.
_free_characters = (None, frozenset())


def free_characters(master):
    """Every HSK 1 character in `master`: what a free account may unlock."""
    global _free_characters
    cached_master, chars = _free_characters
    if cached_master is not master:
        chars = frozenset(char for char, meta in master.items()
                          if meta.get("hsk") == FREE_HSK_LEVEL)
        _free_characters = (master, chars)
    return chars


def restricted_master(master, allowed):
    """`master` narrowed to `allowed`, for seeding a free account's starting pool."""
    return {char: meta for char, meta in master.items() if char in allowed}


class Limits:
    """
    What one account may do right now. `allowed_chars` is None with full
    access, and otherwise the frozenset of characters it may unlock -- the
    form juzi_engine's suggestion, unlock and practice methods take.
    """

    def __init__(self, plan, master, lapsed=False, subscription=None):
        self.plan = plan
        self.lapsed = lapsed
        self.subscription = subscription if isinstance(subscription, dict) else None
        self.full_access = has_full_access(plan)
        self.allowed_chars = None if self.full_access else free_characters(master)

    @classmethod
    def for_entry(cls, entry, master, now=None):
        """The limits a plans.json entry gives at `now` (see access_for)."""
        plan, lapsed = access_for(entry, now)
        return cls(plan, master, lapsed=lapsed, subscription=(entry or {}).get("subscription"))

    @property
    def can_paste(self):
        return self.full_access

    def allows_tier(self, size):
        return self.full_access or size in FREE_TIER_SIZES

    def view(self, unlocked_chars, waiting_chars=0):
        """
        The plan as app.js shows it (GET /api/plan). `waiting_chars` is how
        many unlocked characters the plan holds back (see juzi_engine's
        held_items).
        """
        view = {
            "plan": self.plan,
            "full_access": self.full_access,
            "lapsed": self.lapsed,
            "can_paste": self.can_paste,
            "tier_sizes": None if self.full_access else list(FREE_TIER_SIZES),
        }
        if self.subscription:
            view["subscription"] = {
                "billing": self.subscription.get("billing"),
                "status": self.subscription.get("status"),
                "period_end": self.subscription.get("current_period_end"),
                # Set when the subscriber has already cancelled but the plan
                # is still running out its paid period, which Paddle reports
                # as an active subscription. Without it the page would say
                # the plan renews on the day it actually ends.
                "cancel_scheduled_for": self.subscription.get("cancel_scheduled_for"),
                # The payment provider's customer portal, where a subscriber
                # updates a card or cancels. None until checkout exists.
                "manage_url": None,
            }
        if not self.full_access:
            unlocked = sum(1 for char in unlocked_chars if char in self.allowed_chars)
            view["free_limit"] = {
                "hsk_level": FREE_HSK_LEVEL,
                "total": len(self.allowed_chars),
                "unlocked": unlocked,
                "reached": unlocked >= len(self.allowed_chars),
            }
            view["waiting_chars"] = waiting_chars
        return view
