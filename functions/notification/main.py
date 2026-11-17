"""Notification Service: a Cloud Function triggered by the `notifications` Pub/Sub topic.

Other services publish a small JSON message when something happens that a customer
should know about (payment outcome, a code, "Was this you?", account frozen). Every
message goes to the customer's in-app inbox. It can also go out by:

    email   Gmail SMTP (an app password) or Brevo's API, whichever is configured
    rcs     Google RCS Business Messaging to the customer's phone, if an RBM agent is
            configured; otherwise into a simulated outbox (Firestore `rcs_outbox`)

A message chooses its channels with `"channels": ["email", "rcs"]`. Without that, the
type decides (DEFAULT_CHANNELS): codes, security and fraud messages go everywhere,
routine ones (a payment went through) only to the inbox. The inbox is always written:
it is the record the app shows, with how each channel went.

The email address and phone come with the message, or else from contacts/<customer_id>,
which Identity keeps up to date, so services that never see them (Fraud) still reach
the customer.

Administrators control it from the staff console (System > Notifications), through
the Firestore document settings/notifications (written by Identity):
    email_enabled, rcs_enabled   switch a channel off for everyone (the inbox stays)
    email_provider               "auto" (Gmail, then Brevo), "gmail" or "brevo"
In "auto", Gmail is used until it reports its daily sending limit; then Gmail is paused
for 24 hours (settings/notifications_state.gmail_paused_until) and Brevo takes over.
Any other Gmail failure also falls through to Brevo for that message. Counts per day
and provider are kept in notification_stats/<YYYY-MM-DD>.

It is asynchronous and event-driven: nothing waits for it, and Pub/Sub retries it if
it fails. The inbox document id is the message id, so a retry cannot create a
duplicate. A channel that fails is recorded as "failed"; it doesn't make Pub/Sub
redeliver (that would re-send the channels that worked).

Environment:
    FIRESTORE_DATABASE   Firestore database id (default "(default)")
    EMAIL_FROM           sender address (the Gmail account, or a sender verified in Brevo)
    SMTP_PASSWORD        Gmail app password -> email by SMTP (preferred when set)
    SMTP_HOST, SMTP_PORT default smtp.gmail.com, 587 (STARTTLS)
    SMTP_USER            default EMAIL_FROM
    BREVO_API_KEY        email by Brevo's API instead
    RBM_AGENT_ID         RCS Business Messaging agent id -> real RCS
    RBM_CREDENTIALS      that agent's service-account key (JSON)
"""

from __future__ import annotations

import base64
import datetime
import json
import logging
import os
import smtplib
import ssl
import time
import urllib.error
import urllib.request
from email.message import EmailMessage

log = logging.getLogger("notification")
logging.basicConfig(level=logging.INFO)

CHANNELS = ("email", "rcs")
DEFAULT_CHANNELS = {
    "OTP": ("email", "rcs"),
    "SECURITY": ("email", "rcs"),
    "FRAUD": ("email", "rcs"),
    "KYC": ("email",),
}                                   # anything else (TXN, INFO): the inbox only
NEVER_EMAIL_DOMAINS = ("loadtest.local", "example.com", "example.org")   # simulator and test users
FOOTER = "\n\nUnbeatable Bank (student project, not a real bank)"
PROVIDERS = ("auto", "gmail", "brevo")
GMAIL_PAUSE_SECONDS = 24 * 3600     # Gmail's sending limit is over a rolling day
SETTINGS_TTL = 30                   # seconds a function instance reuses the settings it read
_db = None
_settings_cache: tuple[float, dict] = (0.0, {})


class FirestoreStore:
    def __init__(self) -> None:
        from google.cloud import firestore

        self._db = firestore.Client(database=os.getenv("FIRESTORE_DATABASE", "(default)"))

    def set(self, collection: str, doc_id: str, data: dict) -> None:
        self._db.collection(collection).document(doc_id).set(data)

    def get(self, collection: str, doc_id: str) -> dict | None:
        snapshot = self._db.collection(collection).document(doc_id).get()
        return snapshot.to_dict() if snapshot.exists else None

    def merge(self, collection: str, doc_id: str, data: dict) -> None:
        self._db.collection(collection).document(doc_id).set(data, merge=True)

    def increment(self, collection: str, doc_id: str, field: str) -> None:
        from google.cloud import firestore

        self._db.collection(collection).document(doc_id).set({field: firestore.Increment(1)}, merge=True)


# ------------------------------------------------------------------ email


def setting(name: str) -> str:
    """A configured value, or "" if it is missing or still a placeholder from the docs
    (like "xkeysib-…"): keys and addresses are plain ASCII."""
    value = os.getenv(name, "").strip()
    if not value:
        return ""
    if not value.isascii() or "..." in value or value.lower() in ("changeme", "todo", "xxx"):
        log.error("%s looks like a placeholder, not a real value; ignoring it", name)
        return ""
    return value


class StageError(RuntimeError):
    """Which step of sending failed, so the log says more than 'connection closed'."""


def send_email_smtp(to: str, subject: str, body: str) -> None:
    """Gmail (or any SMTP server) with STARTTLS. Gmail needs an app password:
    Google Account > Security > 2-Step Verification > App passwords."""
    sender = setting("EMAIL_FROM")
    msg = EmailMessage()
    msg["From"] = f"Unbeatable Bank <{sender}>"
    msg["To"] = to
    msg["Subject"] = subject
    msg.set_content(body + FOOTER)
    host, port = os.getenv("SMTP_HOST", "smtp.gmail.com"), int(os.getenv("SMTP_PORT", "587"))
    password = setting("SMTP_PASSWORD").replace(" ", "")      # Gmail shows app passwords in groups of four
    stage = f"connecting to {host}:{port}"
    try:
        with smtplib.SMTP(host, port, timeout=15) as smtp:
            stage = "starting TLS"
            smtp.starttls(context=ssl.create_default_context())
            stage = f"signing in as {setting('SMTP_USER') or sender}"
            smtp.login(setting("SMTP_USER") or sender, password)
            stage = "sending"
            smtp.send_message(msg)
    except smtplib.SMTPAuthenticationError as e:
        raise StageError(f"{stage}: Gmail refused the app password ({e.smtp_code} {e.smtp_error!r})") from e
    except smtplib.SMTPResponseException as e:
        raise StageError(f"{stage}: {e.smtp_code} {e.smtp_error!r}") from e
    except (smtplib.SMTPException, OSError) as e:
        raise StageError(f"{stage}: {type(e).__name__}: {e}") from e


def send_email_brevo(to: str, subject: str, body: str) -> None:
    request = urllib.request.Request(
        "https://api.brevo.com/v3/smtp/email",
        data=json.dumps({
            "sender": {"email": setting("EMAIL_FROM"), "name": "Unbeatable Bank"},
            "to": [{"email": to}],
            "subject": subject,
            "textContent": body + FOOTER,
        }).encode(),
        headers={"api-key": setting("BREVO_API_KEY"), "content-type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            if response.status >= 300:
                raise StageError(f"Brevo answered {response.status}")
    except urllib.error.HTTPError as e:
        raise StageError(f"Brevo answered {e.code}: {e.read()[:300]!r}") from e


def email_providers() -> dict:
    """The email providers that are configured, Gmail first."""
    if not setting("EMAIL_FROM"):
        return {}
    found = {}
    if setting("SMTP_PASSWORD"):
        found["gmail"] = send_email_smtp
    if setting("BREVO_API_KEY"):
        found["brevo"] = send_email_brevo
    return found


def is_quota_error(error: Exception) -> bool:
    """Gmail's answer when the account has sent its daily allowance
    (550 5.4.5 'Daily user sending limit exceeded', or 421 4.7.x 'try again later')."""
    error = error.__cause__ if isinstance(error, StageError) and error.__cause__ else error
    code = getattr(error, "smtp_code", None)
    text = str(getattr(error, "smtp_error", b"") or error).lower()
    return (code in (550, 421, 454) and any(w in text for w in ("5.4.5", "4.7.0", "limit", "quota", "rate"))) \
        or "daily user sending" in text


def load_settings(store, now: float) -> dict:
    """settings/notifications plus its state, cached for SETTINGS_TTL."""
    global _settings_cache
    at, cached = _settings_cache
    if cached and now - at < SETTINGS_TTL:
        return cached
    try:
        chosen = store.get("settings", "notifications") or {}
        state = store.get("settings", "notifications_state") or {}
    except Exception:  # noqa: BLE001 - fall back to the defaults rather than lose the message
        log.warning("could not read the notification settings; using the defaults")
        chosen, state = {}, {}
    settings = {"email_enabled": chosen.get("email_enabled", True), "rcs_enabled": chosen.get("rcs_enabled", True),
                "email_provider": chosen.get("email_provider", "auto"),
                "gmail_paused_until": state.get("gmail_paused_until") or 0}
    _settings_cache = (now, settings)
    return settings


def _count(store, field: str, now: float) -> None:
    try:
        day = datetime.datetime.fromtimestamp(now, datetime.timezone.utc).strftime("%Y-%m-%d")
        store.increment("notification_stats", day, field)
    except Exception:  # noqa: BLE001 - counting must never stop a message
        log.warning("could not count %s", field)


def send_email(store, settings: dict, providers: dict, to: str, subject: str, body: str, now: float) -> tuple[str, str]:
    """(status, provider used). In "auto": Gmail unless paused, then Brevo."""
    global _settings_cache
    choice = settings["email_provider"] if settings["email_provider"] in PROVIDERS else "auto"
    order = [choice] if choice != "auto" else ["gmail", "brevo"]
    if choice == "auto" and settings["gmail_paused_until"] > now and "brevo" in providers:
        order = ["brevo"]           # without Brevo there is nothing to switch to: keep trying Gmail
    tried = False
    for name in order:
        send = providers.get(name)
        if send is None:
            continue
        tried = True
        try:
            send(to, subject, body)
            _count(store, name, now)
            return "sent", name
        except Exception as e:  # noqa: BLE001 - one provider failing must never lose the message
            _count(store, f"{name}_failed", now)
            if name == "gmail" and is_quota_error(e) and "brevo" in providers:
                log.warning("Gmail reached its sending limit; using Brevo for 24 hours")
                store.merge("settings", "notifications_state", {
                    "gmail_paused_until": now + GMAIL_PAUSE_SECONDS, "gmail_paused_reason": str(e)[:300],
                    "gmail_paused_at": now})
                _settings_cache = (0.0, {})
            else:
                log.warning("email by %s failed: %s", name, e)
    return ("failed" if tried else "skipped"), ""


# ------------------------------------------------------------------ RCS


def send_rcs_rbm(phone: str, text: str, message_id: str) -> None:
    """Google RCS Business Messaging: a text message from the bank's agent. A test agent
    can message up to 200 phones that accepted its invitation, at no cost."""
    from google.auth.transport.requests import Request
    from google.oauth2 import service_account

    credentials = service_account.Credentials.from_service_account_info(
        json.loads(os.environ["RBM_CREDENTIALS"]),
        scopes=["https://www.googleapis.com/auth/rcsbusinessmessaging"])
    credentials.refresh(Request())
    url = (f"https://rcsbusinessmessaging.googleapis.com/v1/phones/{phone}/agentMessages"
           f"?messageId={message_id}&agentId={os.environ['RBM_AGENT_ID']}")
    request = urllib.request.Request(
        url, data=json.dumps({"contentMessage": {"text": text[:3000]}}).encode(),
        headers={"Authorization": f"Bearer {credentials.token}", "content-type": "application/json"},
        method="POST")
    with urllib.request.urlopen(request, timeout=10) as response:
        if response.status >= 300:
            raise RuntimeError(f"RBM returned {response.status}")


def rcs_sender():
    return send_rcs_rbm if setting("RBM_AGENT_ID") and setting("RBM_CREDENTIALS") else None


# ------------------------------------------------------------------ one message


def channels_for(message: dict) -> list[str]:
    asked = message.get("channels")
    chosen = asked if isinstance(asked, list) else DEFAULT_CHANNELS.get(message.get("type", ""), ())
    return [c for c in chosen if c in CHANNELS]


def handle(message: dict, store, mailer=None, rcs=None, now: float | None = None) -> dict:
    """Process one notification. Returns what was done per channel (also useful in tests).

    mailer: a send function (used as "gmail") or {"gmail": f, "brevo": g}; rcs: a send
    function. Both default to whatever is configured; tests pass their own."""
    if not message.get("customer_id") or not message.get("message_id"):
        log.error("dropping malformed notification: %s", message)
        return {"inbox": False, "email": "skipped", "rcs": "skipped"}
    now = now if now is not None else time.time()
    providers = mailer if isinstance(mailer, dict) else ({"gmail": mailer} if mailer else email_providers())
    rcs = rcs if rcs is not None else rcs_sender()
    settings = load_settings(store, now)
    created = message.get("created_at") or now
    customer = str(message["customer_id"])
    title, body = message.get("title", "Unbeatable Bank"), message.get("body", "")
    wanted = channels_for(message)
    result = {"inbox": True, "email": "skipped", "rcs": "skipped"}

    contact = {}
    if wanted and not (message.get("email") and message.get("phone")):
        try:
            contact = store.get("contacts", customer) or {}
        except Exception:  # noqa: BLE001 - no address is no worse than not sending
            log.warning("could not look up the contact details of customer %s", customer)

    if "email" in wanted and not settings["email_enabled"]:
        result["email"] = "off"
    elif "email" in wanted:
        email = (message.get("email") or contact.get("email") or "").lower()
        if providers and "@" in email and not email.endswith(NEVER_EMAIL_DOMAINS):
            result["email"], result["email_via"] = send_email(store, settings, providers, email, title, body, now)

    if "rcs" in wanted and not settings["rcs_enabled"]:
        result["rcs"] = "off"
    elif "rcs" in wanted:
        phone = message.get("phone") or contact.get("phone")
        text = f"{title}\n{body}"
        if phone and rcs:
            try:
                rcs(phone, text, message["message_id"])
                result["rcs"] = "sent"
                _count(store, "rcs", now)
            except Exception as e:  # noqa: BLE001 - never lose the inbox copy over RCS
                log.warning("RCS to customer %s failed: %s", customer, e)
                result["rcs"] = "failed"
        elif phone:
            # No RBM agent configured: keep what would have been sent, so it can be shown.
            store.set("rcs_outbox", message["message_id"], {
                "customer_id": customer, "phone": phone, "text": text, "created_at": created})
            result["rcs"] = "simulated"
            _count(store, "rcs_simulated", now)

    store.set("notifications", message["message_id"], {
        "customer_id": customer,
        "type": message.get("type", "INFO"),
        "title": title,
        "body": body,
        "data": message.get("data") or {},
        "created_at": created,
        "read": False,
        "email": result["email"],
        "email_via": result.get("email_via", ""),
        "rcs": result["rcs"],
    })
    log.info("notification %s type=%s customer=%s email=%s rcs=%s", message["message_id"], message.get("type"),
             customer, result["email"], result["rcs"])
    return result


try:
    import functions_framework

    @functions_framework.cloud_event
    def notify(cloud_event):
        """Entry point. Pub/Sub delivers the message base64-encoded inside a CloudEvent."""
        global _db
        if _db is None:
            _db = FirestoreStore()
            try:   # tell the admin panel which ways of sending this deployment has
                _db.merge("settings", "notifications_state", {"configured": {
                    "gmail": "gmail" in email_providers(), "brevo": "brevo" in email_providers(),
                    "rcs": rcs_sender() is not None}})
            except Exception:  # noqa: BLE001
                log.warning("could not record the configured providers")
        payload = base64.b64decode(cloud_event.data["message"]["data"])
        try:
            message = json.loads(payload)
        except ValueError:
            log.error("dropping non-JSON notification")
            return
        handle(message, _db)
except ImportError:   # unit tests run without the functions framework installed
    pass
