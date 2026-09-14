"""
One-off repair for review intervals inflated by early reviews
(found September 14, 2026).

Until that date, JuziEngine._advance_sm2 advanced an item's SM-2 schedule on
every successful review, due or not. A common character appears in nearly
every sentence, so it was graded once a day, every day, and each day
multiplied its interval by its ease factor again -- which also rose with each
perfect answer. One account's 一 reached 2,023,815 days after thirteen days
of study; another account had eleven characters scheduled more than ten
years out. The engine now advances only a due item and caps intervals at
MAX_INTERVAL_DAYS. This script repairs the data that had already inflated.

The review history that produced each value was never recorded, so it can't
be replayed. What IS recorded is when an item was first graded
(`introduced`) and last graded (`last`). From those, the script replays the
fixed scheduler as the best case: a perfect answer on every day the item was
actually due, from `introduced` up to `last`. That gives the longest interval
and highest ease honest scheduling could have produced in that time. An
item's interval and ease are lowered to those bounds if they exceed them,
and never raised. Everything else about an item -- `reps`, `last`,
`introduced`, its pinyin and meaning -- is left alone.

If `introduced` were ever later than an item's true first grading, the span
would be underestimated and the repaired interval would be shorter than
deserved. That errs toward reviewing a character sooner, never later, which
is the harmless direction.

Run it with the service stopped, so a practice session can't write a
brain.json between this script reading and rewriting it:

    python3 repair_sm2_intervals.py           # dry run: report what would change
    python3 repair_sm2_intervals.py --apply   # write the repairs

Safe to re-run: an already-repaired account reports nothing to change.
"""
import argparse
import json
import os
import statistics
from datetime import date, timedelta

from atomic_io import write_json
from juzi_engine import MAX_INTERVAL_DAYS, JuziEngine
from user_registry import USERS_DIR

BANKS = ("unlocked_chars", "unlocked_words")


def best_case_schedule(introduced, last):
    """
    (interval, factor) after reviewing only when due, perfectly every time,
    from `introduced` through `last`. Drives the engine's own _advance_sm2 so
    the bound can never drift from the real scheduling rules.
    """
    entry = {"interval": 0, "factor": 2.5, "reps": 0, "last": None}
    day = introduced
    while day <= last:
        JuziEngine._advance_sm2(entry, 5, day.isoformat())
        day += timedelta(days=max(1, entry["interval"]))
    return entry["interval"], entry["factor"]


def repair_entry(entry):
    """
    Lowers one entry's interval and factor to their bounds, in place.
    Returns (old_interval, new_interval) if anything changed, else None.
    """
    if not isinstance(entry, dict) or not (entry.get("reps") or 0) > 0:
        return None
    interval = entry.get("interval", 0) or 0
    factor = entry.get("factor", 2.5) or 2.5
    max_interval, max_factor = MAX_INTERVAL_DAYS, None
    try:
        introduced = date.fromisoformat(entry["introduced"])
        last = date.fromisoformat(entry["last"])
    except (KeyError, TypeError, ValueError):
        pass  # no usable dates: the cap is the only bound available
    else:
        if introduced <= last:
            max_interval, max_factor = best_case_schedule(introduced, last)

    new_interval = min(interval, max_interval)
    new_factor = factor if max_factor is None else min(factor, max_factor)
    if new_interval == interval and new_factor == factor:
        return None
    entry["interval"] = new_interval
    entry["factor"] = round(new_factor, 2)
    return interval, new_interval


def median_interval(bank):
    graded = [(v.get("interval") or 0) for v in bank.values()
              if isinstance(v, dict) and (v.get("reps") or 0) > 0]
    return statistics.median(graded) if graded else None, \
        statistics.mean(graded) if graded else None


def main():
    parser = argparse.ArgumentParser(description=__doc__.strip().splitlines()[0])
    parser.add_argument("--apply", action="store_true",
                        help="Write the repairs. Without this, only report them.")
    args = parser.parse_args()

    brains = sorted(d for d in os.listdir(USERS_DIR)
                    if os.path.isfile(os.path.join(USERS_DIR, d, "brain.json")))
    changed_accounts = 0
    for number, account in enumerate(brains, 1):
        path = os.path.join(USERS_DIR, account, "brain.json")
        with open(path, "r", encoding="utf-8") as f:
            brain = json.load(f)

        lines = []
        for bank_name in BANKS:
            bank = brain.get(bank_name, {})
            median_before, mean_before = median_interval(bank)
            repairs = [(key, change) for key, entry in bank.items()
                       for change in [repair_entry(entry)] if change]
            if not repairs:
                continue
            median_after, mean_after = median_interval(bank)
            worst = sorted(repairs, key=lambda r: -r[1][0])[:5]
            lines.append(
                f"  {bank_name}: {len(repairs)} repaired | mean interval "
                f"{mean_before:,.1f} -> {mean_after:,.1f} days | median "
                f"{median_before:g} -> {median_after:g}")
            lines.append("    largest: " + ", ".join(
                f"{key} {old:,}->{new:,}" for key, (old, new) in worst))

        if not lines:
            continue
        changed_accounts += 1
        # Numbered rather than named: an account directory name can be a
        # /u/<slug>/ link, which is a credential.
        print(f"account #{number}:")
        print("\n".join(lines))
        if args.apply:
            write_json(path, brain)

    if not changed_accounts:
        print("Nothing to repair.")
    elif args.apply:
        print(f"\nRepaired {changed_accounts} account(s).")
    else:
        print(f"\nDry run: {changed_accounts} account(s) would change. Re-run with --apply to write.")


if __name__ == "__main__":
    main()
