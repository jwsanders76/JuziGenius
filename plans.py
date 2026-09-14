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
character it already has.

users/plans.json, gitignored and backed up with the rest of users/:
    {account_id: {"plan": "paid" | "founding" | "free",
                  "since": ISO time, "source": "operator" | "invite",
                  "note": optional}}

Keyed by the users/<id>/ directory name, which is the same for both account
systems, so a /u/<slug>/ link account keeps its plan when it's claimed as a
login (the slug becomes the user_id). An account with no entry is free.
Founding members are the beta testers from before paid plans existed; set
with set_plan.py.
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


def plan_for(account_id, plans=None):
    """The account's plan name: free when it has no entry, or an unknown one."""
    if plans is None:
        plans = load_plans()
    plan = (plans.get(account_id) or {}).get("plan")
    return plan if plan in PLAN_NAMES else FREE


def has_full_access(plan):
    return plan in (PAID, FOUNDING)


def set_plan(plans, account_id, plan, source, note=None):
    """Records `plan` for `account_id` in `plans`. The caller saves."""
    if plan not in PLAN_NAMES:
        raise ValueError(f"Unknown plan {plan!r}; expected one of {', '.join(PLAN_NAMES)}.")
    entry = {
        "plan": plan,
        "since": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds"),
        "source": source,
    }
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
    form juzi_engine's suggestion and unlock methods take.
    """

    def __init__(self, plan, master):
        self.plan = plan
        self.full_access = has_full_access(plan)
        self.allowed_chars = None if self.full_access else free_characters(master)

    @property
    def can_paste(self):
        return self.full_access

    def allows_tier(self, size):
        return self.full_access or size in FREE_TIER_SIZES

    def view(self, unlocked_chars):
        """The plan as app.js shows it (GET /api/plan)."""
        view = {
            "plan": self.plan,
            "full_access": self.full_access,
            "can_paste": self.can_paste,
            "tier_sizes": None if self.full_access else list(FREE_TIER_SIZES),
        }
        if not self.full_access:
            unlocked = sum(1 for char in unlocked_chars if char in self.allowed_chars)
            view["free_limit"] = {
                "hsk_level": FREE_HSK_LEVEL,
                "total": len(self.allowed_chars),
                "unlocked": unlocked,
                "reached": unlocked >= len(self.allowed_chars),
            }
        return view
