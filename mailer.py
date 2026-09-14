"""
Outgoing email: address confirmation and password-reset links.

Deliberately stdlib-only, like auth.py -- one HTTPS POST to Resend's API via
urllib, rather than an SDK or an SMTP client. The provider is a detail of
_deliver() alone; moving to Postmark or SES means rewriting that one function
and nothing that calls it.

Configuration comes from the environment, which on the droplet is
/home/deploy/.juzi-env loaded by the systemd unit (see juzigenius.service) --
never from a file in this repository, because the key can send mail as
juzigenius.com to anyone:

    JUZI_RESEND_API_KEY   the key. Absent means console mode, below.
    JUZI_MAIL_FROM        e.g. "JuziGenius <accounts@juzigenius.com>"

With no key nothing is sent: each message is printed to stdout instead, link
and all. That is what lets local development work with no account anywhere,
and on the droplet it degrades to the operator reading a link out of
`journalctl -u juzigenius` and passing it on by hand -- the same position as
before this module existed, not a worse one.

Sending happens on a background thread. That is not only so a request never
waits on a third-party API: the forgot-password endpoint must take the same
time whether or not an address belongs to an account, and a synchronous send
would make "yes, it does" measurably slower.
"""
import json
import os
import threading
import urllib.error
import urllib.request

RESEND_ENDPOINT = "https://api.resend.com/emails"
API_KEY = os.environ.get("JUZI_RESEND_API_KEY", "").strip()
MAIL_FROM = os.environ.get("JUZI_MAIL_FROM", "JuziGenius <accounts@juzigenius.com>").strip()
SEND_TIMEOUT_SECONDS = 15


def is_configured():
    return bool(API_KEY)


def send_verification(to_address, link):
    send(to_address, "Confirm your email for JuziGenius", (
        "Confirm this address for your JuziGenius account by opening the link below:\n"
        "\n"
        f"{link}\n"
        "\n"
        "The link works for 48 hours. Once confirmed, this is where password reset "
        "links for your account will go.\n"
        "\n"
        "If you didn't ask for this, ignore this email. Nothing changes unless the "
        "link is opened.\n"
    ))


def send_password_reset(to_address, link, username):
    send(to_address, "Reset your JuziGenius password", (
        f"Someone asked to reset the password for the JuziGenius account \"{username}\".\n"
        "\n"
        "Choose a new password here:\n"
        "\n"
        f"{link}\n"
        "\n"
        "The link works once, for one hour. Setting a new password signs out every "
        "device the account is logged in on.\n"
        "\n"
        "If this wasn't you, ignore this email. Your password stays as it is.\n"
    ))


def send(to_address, subject, text):
    """Queues one plain-text email. Returns immediately and never raises."""
    threading.Thread(target=_deliver_logged, args=(to_address, subject, text),
                     daemon=True).start()


def _deliver_logged(to_address, subject, text):
    try:
        _deliver(to_address, subject, text)
    except Exception as e:
        # The domain, not the address: enough to spot "every gmail.com message
        # is bouncing" without writing people's addresses into the journal on
        # every failure. flush=True for the reason in project_state.md's
        # Security & Hardening notes -- unflushed prints go missing under systemd.
        domain = to_address.rpartition("@")[2]
        print(f"mail: FAILED sending {subject!r} to <...@{domain}>: {e}", flush=True)


def _deliver(to_address, subject, text):
    if not API_KEY:
        quoted = "\n".join(f"  | {line}" for line in text.splitlines())
        print(f"mail: console mode, NOT sent (JUZI_RESEND_API_KEY is unset)\n"
              f"  To: {to_address}\n  Subject: {subject}\n{quoted}", flush=True)
        return

    payload = json.dumps({
        "from": MAIL_FROM,
        "to": [to_address],
        "subject": subject,
        "text": text,
    }).encode("utf-8")
    request = urllib.request.Request(RESEND_ENDPOINT, data=payload, method="POST", headers={
        "Authorization": f"Bearer {API_KEY}",
        "Content-Type": "application/json",
        # urllib's default "Python-urllib/3.x" gets refused as bot traffic by
        # some API edge networks.
        "User-Agent": "JuziGenius/1.0",
    })
    try:
        with urllib.request.urlopen(request, timeout=SEND_TIMEOUT_SECONDS) as response:
            response.read()
    except urllib.error.HTTPError as e:
        detail = e.read(300).decode("utf-8", "replace")
        raise RuntimeError(f"HTTP {e.code}: {detail}") from None
