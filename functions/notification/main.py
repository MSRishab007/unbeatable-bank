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
_db = None


class FirestoreStore:
    def __init__(self) -> None:
        from google.cloud import firestore

        self._db = firestore.Client(database=os.getenv("FIRESTORE_DATABASE", "(default)"))

    def set(self, collection: str, doc_id: str, data: dict) -> None:
        self._db.collection(collection).document(doc_id).set(data)

    def get(self, collection: str, doc_id: str) -> dict | None:
        snapshot = self._db.collection(collection).document(doc_id).get()
        return snapshot.to_dict() if snapshot.exists else None


# ------------------------------------------------------------------ email


def send_email_smtp(to: str, subject: str, body: str) -> None:
    """Gmail (or any SMTP server) with STARTTLS. Gmail needs an app password:
    Google Account > Security > 2-Step Verification > App passwords."""
    sender = os.environ["EMAIL_FROM"]
    msg = EmailMessage()
    msg["From"] = f"Unbeatable Bank <{sender}>"
    msg["To"] = to
    msg["Subject"] = subject
    msg.set_content(body + FOOTER)
    host, port = os.getenv("SMTP_HOST", "smtp.gmail.com"), int(os.getenv("SMTP_PORT", "587"))
    with smtplib.SMTP(host, port, timeout=15) as smtp:
        smtp.starttls(context=ssl.create_default_context())
        smtp.login(os.getenv("SMTP_USER") or sender, os.environ["SMTP_PASSWORD"])
        smtp.send_message(msg)


def send_email_brevo(to: str, subject: str, body: str) -> None:
    request = urllib.request.Request(
        "https://api.brevo.com/v3/smtp/email",
        data=json.dumps({
            "sender": {"email": os.environ["EMAIL_FROM"], "name": "Unbeatable Bank"},
            "to": [{"email": to}],
            "subject": subject,
            "textContent": body + FOOTER,
        }).encode(),
        headers={"api-key": os.environ["BREVO_API_KEY"], "content-type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=10) as response:
        if response.status >= 300:
            raise RuntimeError(f"email provider returned {response.status}")


def email_sender():
    """The configured way to send email, or None."""
    if not os.getenv("EMAIL_FROM"):
        return None
    if os.getenv("SMTP_PASSWORD"):
        return send_email_smtp
    if os.getenv("BREVO_API_KEY"):
        return send_email_brevo
    return None


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
    return send_rcs_rbm if os.getenv("RBM_AGENT_ID") and os.getenv("RBM_CREDENTIALS") else None


# ------------------------------------------------------------------ one message


def channels_for(message: dict) -> list[str]:
    asked = message.get("channels")
    chosen = asked if isinstance(asked, list) else DEFAULT_CHANNELS.get(message.get("type", ""), ())
    return [c for c in chosen if c in CHANNELS]


def handle(message: dict, store, mailer=None, rcs=None) -> dict:
    """Process one notification. Returns what was done per channel (also useful in tests).

    mailer / rcs default to whatever is configured; tests pass their own."""
    if not message.get("customer_id") or not message.get("message_id"):
        log.error("dropping malformed notification: %s", message)
        return {"inbox": False, "email": "skipped", "rcs": "skipped"}
    mailer = mailer if mailer is not None else email_sender()
    rcs = rcs if rcs is not None else rcs_sender()
    created = message.get("created_at") or time.time()
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

    if "email" in wanted:
        email = (message.get("email") or contact.get("email") or "").lower()
        if mailer and "@" in email and not email.endswith(NEVER_EMAIL_DOMAINS):
            try:
                mailer(email, title, body)
                result["email"] = "sent"
            except (urllib.error.URLError, smtplib.SMTPException, RuntimeError, OSError) as e:
                log.warning("email to customer %s failed: %s", customer, e)
                result["email"] = "failed"

    if "rcs" in wanted:
        phone = message.get("phone") or contact.get("phone")
        text = f"{title}\n{body}"
        if phone and rcs:
            try:
                rcs(phone, text, message["message_id"])
                result["rcs"] = "sent"
            except (urllib.error.URLError, RuntimeError, OSError, ValueError, KeyError) as e:
                log.warning("RCS to customer %s failed: %s", customer, e)
                result["rcs"] = "failed"
        elif phone:
            # No RBM agent configured: keep what would have been sent, so it can be shown.
            store.set("rcs_outbox", message["message_id"], {
                "customer_id": customer, "phone": phone, "text": text, "created_at": created})
            result["rcs"] = "simulated"

    store.set("notifications", message["message_id"], {
        "customer_id": customer,
        "type": message.get("type", "INFO"),
        "title": title,
        "body": body,
        "data": message.get("data") or {},
        "created_at": created,
        "read": False,
        "email": result["email"],
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
        payload = base64.b64decode(cloud_event.data["message"]["data"])
        try:
            message = json.loads(payload)
        except ValueError:
            log.error("dropping non-JSON notification")
            return
        handle(message, _db)
except ImportError:   # unit tests run without the functions framework installed
    pass
