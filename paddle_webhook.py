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
# created as pending_approval on a live account and Paddle may reject it; a
# chargeback_warning is only a warning; a partial refund is a goodwill
# gesture, not a return of the purchase. Chargebacks are created approved.
REFUND_ACTIONS = ("refund", "chargeback")
REFUND_STATUS = "approved"
REFUND_TYPE = "full"


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
    kind = (data.get("type") or "").lower()
    not_final = None
    if status != REFUND_STATUS:
        not_final = f"{action} {status or 'with no status'}, not approved"
    elif kind != REFUND_TYPE:
        not_final = f"{kind or 'unspecified'} {action}, not full"
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
    return f"recorded {record['billing']}/{record['status']}"


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
    general = (urls.get("general") or {}).get("overview")
    for subscription in urls.get("subscriptions") or []:
        # The deep link straight to cancelling is the more useful one when a
        # subscription is named, and the overview is the honest fallback.
        if subscription.get("cancel_subscription"):
            return subscription["cancel_subscription"]
    return general
