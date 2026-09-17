"""
Paddle's webhooks: verified, then turned into the subscription records
plans.py already understands.

Paddle is the merchant of record (see the Terms, section 5). It owns the
checkout, the money and the subscription lifecycle; this app only needs to
know whether an account currently has full access, which plans.py decides
from a subscription's billing period, status and period end. So everything
here is a translation layer: check the message really came from Paddle, work
out which account it concerns, and hand plans.record_subscription the three
fields it wants.

Deliberately stdlib-only, like mailer.py and the rest: hmac and hashlib are
all a signature check needs.

Configuration, read from /home/deploy/.juzi-env via the systemd unit's
EnvironmentFile (the same arrangement as the Resend key):

    JUZI_PADDLE_ENV             "sandbox" or "live"; only labels logs today
    JUZI_PADDLE_WEBHOOK_SECRET  the notification destination's secret
    JUZI_PADDLE_PRICE_MONTHLY   the pri_... id of the monthly price
    JUZI_PADDLE_PRICE_ANNUAL    the pri_... id of the annual price
    JUZI_PADDLE_PRICE_LIFETIME  the pri_... id of the one-time lifetime price
    JUZI_ALERT_EMAIL            where setup-error alerts go; default support@

The price ids are configuration rather than constants because sandbox and
live are separate Paddle accounts with entirely separate catalogues: the
same three prices have different ids in each, so hardcoding either set would
mean a code change to go live, and a wrong-looking id is then a config typo
rather than a deploy.

WITH NO SECRET SET, NOTHING IS ACCEPTED. server.py answers 503 rather than
trusting an unsigned body, so a half-configured server can never record a
subscription somebody merely claimed to have bought.
"""
import hashlib
import hmac
import json
import os
import threading
import time
import urllib.error
import urllib.request

import plans

ENVIRONMENT = os.environ.get("JUZI_PADDLE_ENV", "sandbox").strip() or "sandbox"
WEBHOOK_SECRET = os.environ.get("JUZI_PADDLE_WEBHOOK_SECRET", "").strip()
API_KEY = os.environ.get("JUZI_PADDLE_API_KEY", "").strip()
# Safe in the page by design: it identifies the seller to Paddle.js and can
# do nothing on its own. The API key above never leaves this process.
CLIENT_TOKEN = os.environ.get("JUZI_PADDLE_CLIENT_TOKEN", "").strip()

# Sandbox and live are separate hosts; a key for one is refused by the other,
# which is the failure we want if JUZI_PADDLE_ENV and the keys ever disagree.
API_BASE = ("https://sandbox-api.paddle.com" if ENVIRONMENT == "sandbox"
            else "https://api.paddle.com")
PORTAL_TIMEOUT_SECONDS = 10

# How far out of step a webhook's timestamp may be before it's refused as a
# replay. Paddle's own SDKs default to five seconds, which is tighter than
# this box can promise: the app runs on one shared vCPU behind Caddy, and a
# request that queues behind a corpus scan can easily be seconds late through
# no fault of Paddle's, at which point a real payment silently stops being
# recorded. Five minutes still makes a captured body useless long before an
# attacker could do anything with it, and matches what other payment
# providers ask for.
SIGNATURE_TOLERANCE_SECONDS = 300

# Paddle's subscription statuses -> the ones plans.py stores. "trialing" is
# mapped rather than rejected: JuziGenius sells no trials (the free plan is
# not a trial, and the pricing page says so), but a trial switched on by
# accident in the Paddle dashboard should still leave the subscriber with
# access rather than being dropped as an unknown status.
STATUS_MAP = {
    "active": plans.ACTIVE,
    "trialing": plans.ACTIVE,
    "past_due": plans.PAST_DUE,
    "paused": plans.PAUSED,
    "canceled": plans.CANCELED,
}

SUBSCRIPTION_EVENTS = ("subscription.created", "subscription.updated",
                       "subscription.paused", "subscription.past_due",
                       "subscription.canceled")
TRANSACTION_EVENTS = ("transaction.completed",)
# A refund is an adjustment, not a subscription event. It matters only for
# lifetime purchases: refunding a subscription makes Paddle cancel it, and
# subscription.canceled already lapses the account, while a lifetime buyer
# has no subscription for Paddle to cancel.
ADJUSTMENT_EVENTS = ("adjustment.created", "adjustment.updated")
# Only money that has actually gone back ends a lifetime plan. A refund is
# created as pending_approval on a live account and Paddle may reject it, and
# a chargeback_warning is only a warning. Chargebacks are created approved.
#
# Any approved refund counts, however much of the purchase it returns. The
# adjustment's own full/partial "type" can't be trusted for this: a refund
# issued from Paddle's dashboard, where the amount is typed in, arrives as
# "partial" even when it returns every cent (seen in the sandbox, September
# 2026, on a $155.70 refund the dashboard itself called "Full refund
# issued"). Lifetime is only ever refunded in full under the Refund Policy,
# so the rare goodwill or tax-only refund that shouldn't end the plan is
# restored by hand with set_plan.py.
REFUND_ACTIONS = ("refund", "chargeback")
REFUND_STATUS = "approved"

# A lifetime price must be one-time in Paddle's catalogue. If it's set to
# recur, a lifetime purchase also creates a subscription that charges the
# customer again every period -- which happened in the sandbox in September
# 2026, when the lifetime price was saved as $149 a month. Events showing
# that are refused with this in the log, so the mistake is named there
# rather than recorded as ordinary purchases.
LIFETIME_RECURS = "setup error: the lifetime price recurs in Paddle; make it one-time"

# The log line alone would go unread, and the cost of this mistake is
# customers being charged $149 again every period, so it is also emailed.
# At most one email per ALERT_INTERVAL_SECONDS per server process: a single
# purchase sends two or three affected events within a second, and every
# one of them is still in the log.
ALERT_ADDRESS = os.environ.get("JUZI_ALERT_EMAIL", "").strip() or "support@juzigenius.com"
ALERT_INTERVAL_SECONDS = 6 * 60 * 60
_alert_lock = threading.Lock()
_last_alert_at = None


def price_billing_map():
    """
    {price id: billing period} from the environment, skipping any that isn't
    set. Read per call rather than cached at import so a corrected id takes
    effect on restart without reasoning about import order.
    """
    configured = (
        (os.environ.get("JUZI_PADDLE_PRICE_MONTHLY", "").strip(), plans.MONTHLY),
        (os.environ.get("JUZI_PADDLE_PRICE_ANNUAL", "").strip(), plans.ANNUAL),
        (os.environ.get("JUZI_PADDLE_PRICE_LIFETIME", "").strip(), plans.LIFETIME),
    )
    return {price_id: billing for price_id, billing in configured if price_id}


def parse_signature(header):
    """
    (timestamp, signature) from a Paddle-Signature header, or (None, None).

    The header looks like `ts=1671552777;h1=eb4d0dc8...`. Parsed by key
    rather than by position, since the order of the parts is Paddle's to
    change.
    """
    timestamp = signature = None
    for part in (header or "").split(";"):
        key, _, value = part.partition("=")
        key = key.strip()
        if key == "ts":
            try:
                timestamp = int(value.strip())
            except ValueError:
                return None, None
        elif key == "h1":
            signature = value.strip()
    if timestamp is None or not signature:
        return None, None
    return timestamp, signature


def verify_signature(header, raw_body, secret=None, now=None):
    """
    Whether `raw_body` (bytes, exactly as received) really came from Paddle.

    Paddle signs the string "<ts>:<raw body>" with HMAC-SHA256 under the
    notification destination's secret. The body must be the untouched bytes
    off the wire: re-serialising the JSON, or even decoding and re-encoding
    it, can reorder keys or change escaping and the signature then fails for
    a message that was perfectly genuine. That is why server.py reads this
    one route's body as bytes.

    Compared with hmac.compare_digest, so a wrong signature can't be found
    character by character from how long the comparison takes.
    """
    secret = WEBHOOK_SECRET if secret is None else secret
    if not secret:
        return False
    timestamp, signature = parse_signature(header)
    if timestamp is None:
        return False
    if abs((time.time() if now is None else now) - timestamp) > SIGNATURE_TOLERANCE_SECONDS:
        return False
    expected = hmac.new(secret.encode("utf-8"),
                        b"%d:%s" % (timestamp, raw_body),
                        hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature)


def _period_end_for(data, status):
    """
    When a subscription's access should run out.

    While a subscription is billing, Paddle reports the period it has been
    paid for in current_billing_period, and that is the answer. A canceled or
    paused one is different: Paddle stops sending a current billing period
    once it is no longer billing, so reading only that field yielded None and
    plans.record_subscription rejected the event as malformed -- meaning a
    cancellation never got recorded and the account stayed active for good.
    That is the ordinary path for every customer who cancels, so it has to
    work whatever Paddle chooses to send.

    In order: the period already paid for; the date a scheduled cancellation
    or pause takes effect; and failing both, now -- because a subscription
    that Paddle says is over, with no date attached, is over. Erring towards
    now rather than discarding the event is the safe direction: the access
    rules in plans.py still let a canceled subscription run to its period
    end, so the worst case is access ending on time rather than never.
    """
    period_end = (data.get("current_billing_period") or {}).get("ends_at")
    if period_end:
        return period_end
    if status in (plans.CANCELED, plans.PAUSED):
        scheduled = (data.get("scheduled_change") or {}).get("effective_at")
        return scheduled or plans._iso(plans._now())
    return None


def _subscription_record(data, prices):
    """The fields plans.record_subscription wants, from a subscription entity."""
    billing = None
    for item in data.get("items") or []:
        price_id = ((item.get("price") or {}).get("id") or "").strip()
        if price_id in prices:
            billing = prices[price_id]
            break
    if billing is None:
        return None
    if billing == plans.LIFETIME:
        # Only possible when the lifetime price is set to recur. The purchase
        # itself is honoured from its transaction (see _lifetime_record);
        # the subscription behind it describes nothing the customer bought,
        # and letting it through would, for one, lapse a lifetime plan when
        # the stray subscription is cancelled.
        return {"ignore": LIFETIME_RECURS}

    status = STATUS_MAP.get(data.get("status"))
    if status is None:
        return None

    period_end = _period_end_for(data, status)
    # A subscriber who cancels mid-period leaves Paddle reporting an active
    # subscription with the cancellation held in scheduled_change. Status
    # alone therefore cannot tell "renewing" from "already cancelled", and
    # reading it as active told the subscriber their plan renewed on the very
    # day it ends. Only a cancellation counts here: Paddle also uses
    # scheduled_change for pauses and resumes, which say something different.
    scheduled = data.get("scheduled_change") or {}
    cancel_scheduled_for = (scheduled.get("effective_at")
                            if scheduled.get("action") == "cancel" else None)
    return {
        "billing": billing,
        "status": status,
        "current_period_end": period_end,
        "cancel_scheduled_for": cancel_scheduled_for,
        "subscription_id": data.get("id"),
        "customer_id": data.get("customer_id"),
        "custom_data": data.get("custom_data") or {},
    }


def _lifetime_record(data, prices):
    """
    The same, from a completed transaction, but only for the lifetime price.

    A lifetime purchase is a one-time charge, so it produces no subscription
    and no subscription.* events at all -- only transaction.completed. Every
    other completed transaction (the first charge of a subscription, each
    renewal) is ignored here, because the subscription events already
    describe those and describe them better.

    A lifetime transaction that belongs to a subscription means the price is
    set to recur (see LIFETIME_RECURS). The first charge is still a real
    lifetime purchase and is honoured, flagged so the log names the setup
    error; apply_event refuses the repeat charges that follow.
    """
    lifetime_ids = {price_id for price_id, billing in prices.items()
                    if billing == plans.LIFETIME}
    for item in data.get("items") or []:
        price_id = ((item.get("price") or {}).get("id") or "").strip()
        if price_id in lifetime_ids:
            return {
                "billing": plans.LIFETIME,
                "status": plans.ACTIVE,
                "current_period_end": None,
                "subscription_id": data.get("subscription_id"),
                "customer_id": data.get("customer_id"),
                "custom_data": data.get("custom_data") or {},
                "recurs": bool(data.get("subscription_id")),
            }
    return None


def _refund_record(data):
    """
    A refund or chargeback, reduced to the customer it concerns.

    Deliberately not matched against the refunded transaction's line items:
    an adjustment references transaction items rather than prices directly,
    and guessing at that nesting would be the kind of assumption that breaks
    silently. The customer is enough, because apply_event only acts on a
    refund when that customer's recorded plan is a lifetime one -- the single
    case nothing else revokes.

    A refund that isn't final yet still comes back as a record, marked with
    why it doesn't count, so the log says "pending" rather than looking like
    an event nobody handled. Paddle sends the same adjustment again, through
    adjustment.updated, once it is approved.
    """
    action = (data.get("action") or "").lower()
    if action not in REFUND_ACTIONS:
        return None
    status = (data.get("status") or "").lower()
    not_final = None
    if status != REFUND_STATUS:
        not_final = f"{action} {status or 'with no status'}, not approved"
    return {"refund": True, "not_final": not_final,
            "customer_id": data.get("customer_id"),
            "subscription_id": data.get("subscription_id"), "custom_data": {}}


def event_record(event, prices=None):
    """
    What one webhook says about a subscription, or None if it says nothing
    this app acts on -- an event type we don't handle, a price that isn't
    ours, or a status Paddle has invented since this was written.
    """
    prices = price_billing_map() if prices is None else prices
    data = event.get("data") or {}
    event_type = event.get("event_type")
    if event_type in SUBSCRIPTION_EVENTS:
        return _subscription_record(data, prices)
    if event_type in TRANSACTION_EVENTS:
        return _lifetime_record(data, prices)
    if event_type in ADJUSTMENT_EVENTS:
        return _refund_record(data)
    return None


def account_for(record, plans_data):
    """
    Which JuziGenius account a webhook concerns.

    Three ways, in order of trustworthiness. The checkout attaches the
    account id as custom data, and Paddle carries that onto the transaction
    and the subscription, so it is normally right there in the payload. If it
    isn't -- a subscription created in Paddle's dashboard by hand, say -- fall
    back to whichever account already has this subscription id recorded, and
    failing that this customer id. Returning None means the event is about
    somebody this app has never heard of, which is not an error: the sandbox
    account will accumulate test purchases that belong to nobody.
    """
    account_id = (record.get("custom_data") or {}).get("account_id")
    if account_id:
        return account_id

    for known_id, entry in plans_data.items():
        subscription = (entry or {}).get("subscription") or {}
        if record.get("subscription_id") and subscription.get("subscription_id") == record["subscription_id"]:
            return known_id
    for known_id, entry in plans_data.items():
        subscription = (entry or {}).get("subscription") or {}
        if record.get("customer_id") and subscription.get("customer_id") == record["customer_id"]:
            return known_id
    return None


def apply_event(event, plans_data, prices=None):
    """
    Records one verified webhook in `plans_data`, which the caller saves
    under plans.PLANS_LOCK. Returns a short outcome string for the log --
    never containing an account id, since a link account's id is its
    password and the log is not the place for it (see server.py's
    log_message).

    Two rules that matter, both straight from Paddle's own guidance:

    * **Deduplicate on event id.** Delivery is at-least-once, so the same
      event arrives again after any hiccup, and applying a cancellation twice
      would be harmless while applying an out-of-date one would not.
    * **Ignore events older than the one already recorded.** Events can
      arrive out of order, and without this a delayed "past_due" could land
      after the "active" that resolved it and lock a paying subscriber out.
    """
    record = event_record(event, prices)
    if record is None:
        return "ignored (not a subscription event for a known price)"
    if record.get("ignore"):
        return f"ignored ({record['ignore']})"

    account_id = account_for(record, plans_data)
    if account_id is None:
        return "ignored (no matching account)"

    event_id = event.get("event_id")
    occurred_at = event.get("occurred_at") or ""
    previous = ((plans_data.get(account_id) or {}).get("subscription") or {})

    if record.get("refund"):
        if previous.get("billing") != plans.LIFETIME:
            return "ignored (refund of something with its own cancellation)"
        if record.get("not_final"):
            return f"ignored ({record['not_final']})"
        if previous.get("status") == plans.CANCELED:
            return "ignored (already refunded)"
        entry = plans.record_subscription(
            plans_data, account_id, plans.LIFETIME, plans.CANCELED, None,
            source="paddle", provider="paddle",
            subscription_id=previous.get("subscription_id"),
            customer_id=previous.get("customer_id"))
        # Stamped like any other event, so a late redelivery of the purchase
        # itself is recognised as older and can't grant lifetime back.
        entry["subscription"]["last_event_id"] = event_id
        entry["subscription"]["event_occurred_at"] = max(
            occurred_at, previous.get("event_occurred_at", ""))
        return "recorded lifetime/canceled (refunded)"

    if event_id and previous.get("last_event_id") == event_id:
        return "ignored (already applied)"
    if (previous.get("billing") == plans.LIFETIME and previous.get("status") == plans.ACTIVE
            and record["billing"] != plans.LIFETIME):
        # Lifetime always wins. A subscriber who switches to lifetime has the
        # old subscription cancelled (see subscription_to_cancel), and Paddle
        # then reports that cancellation -- as it would any late renewal or
        # resumed pause on it. None of those may take lifetime away.
        return "ignored (the account has lifetime, which a subscription can't replace)"
    if record.get("recurs") and previous.get("subscription_id") == record["subscription_id"]:
        # A repeat charge of a recurring "lifetime" price, or the first charge
        # delivered again. Either way the account already has what this
        # subscription can give it, and after a refund it must not get it back.
        return f"ignored ({LIFETIME_RECURS}; a repeat charge)"
    if occurred_at and previous.get("event_occurred_at", "") > occurred_at:
        return "ignored (older than the record it would replace)"

    try:
        entry = plans.record_subscription(
            plans_data, account_id,
            record["billing"], record["status"], record["current_period_end"],
            source="paddle", provider="paddle",
            subscription_id=record.get("subscription_id"),
            customer_id=record.get("customer_id"),
            cancel_scheduled_for=record.get("cancel_scheduled_for"))
    except ValueError as bad:
        return f"ignored ({bad})"

    # Stamped after the fact: record_subscription builds the subscription
    # dict from scratch each time, which is what keeps a stale field from
    # surviving a change, so these two belong here rather than in its
    # signature.
    entry["subscription"]["last_event_id"] = event_id
    entry["subscription"]["event_occurred_at"] = occurred_at
    replaced = _replaced_subscription(previous, record)
    if replaced:
        entry["subscription"]["replaced_subscription_id"] = replaced
        return "recorded lifetime/active (replaces a subscription, to be cancelled)"
    if record.get("recurs"):
        return f"recorded {record['billing']}/{record['status']} ({LIFETIME_RECURS})"
    return f"recorded {record['billing']}/{record['status']}"


# Statuses a subscription can still bill or come back from, so switching to
# lifetime has to cancel it. A canceled one has already stopped.
CANCELLABLE_STATUSES = (plans.ACTIVE, plans.PAST_DUE, plans.PAUSED)


def _replaced_subscription(previous, record):
    """
    The subscription id a new lifetime purchase replaces, or None.

    Only a monthly or annual subscription that can still bill counts; its
    own id is never "replaced" by itself (a lifetime price set to recur, see
    LIFETIME_RECURS, carries a subscription id of its own).
    """
    if record.get("billing") != plans.LIFETIME or record.get("status") != plans.ACTIVE:
        return None
    if previous.get("billing") not in (plans.MONTHLY, plans.ANNUAL):
        return None
    if previous.get("status") not in CANCELLABLE_STATUSES:
        return None
    old = previous.get("subscription_id")
    if not old or old == record.get("subscription_id"):
        return None
    return old


def subscription_to_cancel(event, plans_data, prices=None):
    """
    After apply_event has recorded `event`: the id of the subscription that
    lifetime purchase replaced, if it just did, else None. The caller cancels
    it through Paddle (cancel_subscription). Asked only when apply_event's
    outcome starts "recorded", so a redelivered purchase doesn't cancel twice.
    """
    record = event_record(event, prices)
    if not record or record.get("refund") or record.get("ignore"):
        return None
    account_id = account_for(record, plans_data)
    subscription = ((plans_data.get(account_id) or {}).get("subscription") or {}) if account_id else {}
    if (subscription.get("billing") != plans.LIFETIME
            or subscription.get("last_event_id") != event.get("event_id")):
        return None
    return subscription.get("replaced_subscription_id")


def cancel_subscription(subscription_id):
    """
    Cancels a subscription through Paddle's API, immediately rather than at
    the end of its period: the customer has just bought lifetime, and the
    switch screen told them the rest of the period isn't refunded.

    Returns (True, "cancelled") or (False, a short reason). Never raises. The
    API key needs the "Subscriptions: Write" permission; without it Paddle
    answers 403 and the caller emails the operator to cancel by hand.
    """
    if not API_KEY:
        return False, "no Paddle API key configured"
    if not subscription_id:
        return False, "no subscription id"
    request = urllib.request.Request(
        f"{API_BASE}/subscriptions/{subscription_id}/cancel",
        data=json.dumps({"effective_from": "immediately"}).encode("utf-8"), method="POST",
        headers={"Authorization": f"Bearer {API_KEY}",
                 "Content-Type": "application/json",
                 "User-Agent": "JuziGenius/1.0"})
    try:
        with urllib.request.urlopen(request, timeout=PORTAL_TIMEOUT_SECONDS) as response:
            response.read()
        return True, "cancelled"
    except urllib.error.HTTPError as e:
        code = ""
        try:
            code = (json.loads(e.read().decode("utf-8")).get("error") or {}).get("code") or ""
        except (ValueError, AttributeError):
            pass
        return False, f"HTTP {e.code}" + (f" {code}" if code else "")
    except (urllib.error.URLError, TimeoutError) as e:
        return False, type(e).__name__


def cancel_failed_alert(subscription_id, reason):
    """
    (subject, text) for the operator when a replaced subscription couldn't be
    cancelled automatically. The subscription id is the one thing needed to
    find it in Paddle; nothing else about the customer is included.
    """
    environment = ENVIRONMENT.upper()
    dashboard = ("sandbox-vendors.paddle.com" if ENVIRONMENT == "sandbox"
                 else "vendors.paddle.com")
    permission = ("\nPaddle refused the request (403): the server's API key is missing the\n"
                  "\"Subscriptions: Write\" permission. Add it under Developer tools > Authentication.\n"
                  if reason.startswith("HTTP 403") else "")
    subject = f"[JuziGenius {environment}] Cancel a subscription by hand: its owner switched to lifetime"
    text = (
        f"A subscriber bought lifetime ({environment}), and their old subscription could not be\n"
        "cancelled automatically, so Paddle will go on charging them for it.\n"
        "\n"
        f"  Subscription: {subscription_id}\n"
        f"  Reason: {reason}\n"
        f"{permission}"
        "\n"
        f"What to do, at {dashboard}:\n"
        "  1. Subscriptions > find the id above > Cancel > IMMEDIATELY (not at the end\n"
        "     of the billing period).\n"
        "  2. Their lifetime access is already in place; the cancellation doesn't touch it.\n"
    )
    return subject, text


def setup_alert(event, outcome, now=None):
    """
    (subject, text) for an email to ALERT_ADDRESS when `outcome` names the
    recurring-lifetime setup error, or None -- including when an alert went
    out within the last ALERT_INTERVAL_SECONDS. The caller sends it.

    Names only the environment, the event type, Paddle's event id and the
    price id: nothing about the customer. The event id is enough to find
    the purchase in Paddle's own notification log.
    """
    global _last_alert_at
    if LIFETIME_RECURS not in outcome:
        return None
    now = time.time() if now is None else now
    with _alert_lock:
        if _last_alert_at is not None and now - _last_alert_at < ALERT_INTERVAL_SECONDS:
            return None
        _last_alert_at = now
    lifetime_ids = [price_id for price_id, billing in price_billing_map().items()
                    if billing == plans.LIFETIME]
    environment = ENVIRONMENT.upper()
    dashboard = ("sandbox-vendors.paddle.com" if ENVIRONMENT == "sandbox"
                 else "vendors.paddle.com")
    subject = f"[JuziGenius {environment}] Paddle setup error: the lifetime price recurs"
    text = (
        f"Paddle sent an event showing that the lifetime price is set to recur ({environment}).\n"
        "\n"
        f"  Event: {event.get('event_type')} {event.get('event_id')}\n"
        f"  Lifetime price: {', '.join(lifetime_ids) or 'not configured'}\n"
        f"  Server's decision: {outcome}\n"
        "\n"
        "A customer who bought lifetime gets it from their first charge, but Paddle will\n"
        "charge them again every billing period until this is fixed. The app ignores\n"
        "the repeat charges; it can't stop or refund them.\n"
        "\n"
        f"What to do, at {dashboard}:\n"
        "  1. Catalog > JuziGenius Full access > edit the lifetime price and set its\n"
        "     billing to one-time.\n"
        "  2. Subscriptions: cancel IMMEDIATELY every subscription on the lifetime\n"
        "     price (not at the end of the period). The customers keep lifetime.\n"
        "  3. Transactions: refund any repeat charges already taken.\n"
        "\n"
        f"Further events like this are only logged for the next {ALERT_INTERVAL_SECONDS // 3600} hours.\n"
    )
    return subject, text


def checkout_config():
    """
    What app.js needs to open a checkout, or None when Paddle isn't
    configured -- in which case the upgrade screen goes on explaining the
    paid plan without offering to sell it, which is the right behaviour for
    a local development copy as much as for a half-configured server.

    Only the client-side token goes out, never the API key: the token
    identifies the seller to Paddle.js and is meant to sit in the page.
    """
    prices = price_billing_map()
    if not CLIENT_TOKEN or not prices:
        return None
    return {
        "client_token": CLIENT_TOKEN,
        "environment": ENVIRONMENT,
        "prices": {billing: price_id for price_id, billing in prices.items()},
    }


def portal_session_url(customer_id, subscription_id=None):
    """
    A signed link into Paddle's customer portal, where a subscriber updates a
    card or cancels -- which is where the Terms send them.

    Always the portal's overview page, never its direct cancel link, even
    though Paddle offers both. The button says "Manage subscription", and
    people press it to check a renewal date or change a card; landing them on
    a cancel dialog read as though we wanted them gone. Cancelling stays one
    click away on the overview, which keeps it as easy online as the Terms
    and California's click-to-cancel rule require.

    Minted per click and never stored: Paddle's links carry a short-lived
    token, so a cached one would be a link that works for a while and then
    quietly doesn't. Returns None rather than raising when Paddle can't be
    reached, so Settings can fall back to telling people to email support
    instead of showing an error it can't act on.
    """
    if not API_KEY or not customer_id:
        return None
    payload = json.dumps(
        {"subscription_ids": [subscription_id]} if subscription_id else {}).encode("utf-8")
    request = urllib.request.Request(
        f"{API_BASE}/customers/{customer_id}/portal-sessions",
        data=payload, method="POST",
        headers={"Authorization": f"Bearer {API_KEY}",
                 "Content-Type": "application/json",
                 "User-Agent": "JuziGenius/1.0"})
    try:
        with urllib.request.urlopen(request, timeout=PORTAL_TIMEOUT_SECONDS) as response:
            body = json.loads(response.read().decode("utf-8"))
    except (urllib.error.URLError, ValueError, TimeoutError) as e:
        # The reason, never the response body: it describes a real customer.
        print(f"paddle portal session failed: {type(e).__name__}", flush=True)
        return None

    urls = ((body.get("data") or {}).get("urls") or {})
    return (urls.get("general") or {}).get("overview")
