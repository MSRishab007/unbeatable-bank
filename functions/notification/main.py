"""Notification Service: a Cloud Function triggered by the `notifications` Pub/Sub topic.

Other services publish a small JSON message when something happens that a
customer should know about (payment outcome, OTP, "Was this you?", account
frozen). For each message this function:

  1. writes it to the customer's inbox in Firestore (shown in the web app)
  2. copies OTPs to a simulated SMS outbox (real SMS in India needs DLT registration)
  3. emails security-relevant messages through Brevo, if an API key is configured.
     The address comes with the message, or else from contacts/<customer_id>, which
     Identity keeps up to date (so services that don't know the email, like Fraud,
     still reach the customer by email)

It is asynchronous and event-driven: nothing waits for it, and Pub/Sub retries
it if it fails. The inbox document id is the message id, so a retry cannot
create a duplicate.

Environment:
    FIRESTORE_DATABASE   Firestore database id (default "(default)")
    BREVO_API_KEY        optional; without it emails are skipped
    EMAIL_FROM           sender address verified in Brevo
    EMAIL_TYPES          comma list of message types to email (default OTP,SECURITY,FRAUD)
"""

from __future__ import annotations

import base64
import json
import logging
import os
import time
import urllib.error
import urllib.request

log = logging.getLogger("notification")
logging.basicConfig(level=logging.INFO)

NEVER_EMAIL_DOMAINS = ("loadtest.local", "example.com", "example.org")   # simulator and test users
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


def send_email_brevo(to: str, subject: str, body: str) -> None:
    request = urllib.request.Request(
        "https://api.brevo.com/v3/smtp/email",
        data=json.dumps({
            "sender": {"email": os.environ["EMAIL_FROM"], "name": "Unbeatable Bank"},
            "to": [{"email": to}],
            "subject": subject,
            "textContent": body + "\n\nUnbeatable Bank (student project, not a real bank)",
        }).encode(),
        headers={"api-key": os.environ["BREVO_API_KEY"], "content-type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=10) as response:
        if response.status >= 300:
            raise RuntimeError(f"email provider returned {response.status}")


def should_email(message: dict) -> bool:
    email = (message.get("email") or "").lower()
    types = os.getenv("EMAIL_TYPES", "OTP,SECURITY,FRAUD").split(",")
    return (bool(email) and "@" in email and bool(os.getenv("BREVO_API_KEY"))
            and message.get("type") in types and not email.endswith(NEVER_EMAIL_DOMAINS))


def handle(message: dict, store, mailer=send_email_brevo) -> dict:
    """Process one notification. Returns what was done (also useful in tests)."""
    if not message.get("customer_id") or not message.get("message_id"):
        log.error("dropping malformed notification: %s", message)
        return {"inbox": False, "sms": False, "email": "skipped"}

    created = message.get("created_at") or time.time()
    result = {"inbox": True, "sms": False, "email": "skipped"}
    if not message.get("email") and os.getenv("BREVO_API_KEY"):
        try:
            contact = store.get("contacts", str(message["customer_id"])) or {}
        except Exception:  # noqa: BLE001 - no address is no worse than before
            log.warning("could not look up the email for customer %s", message["customer_id"])
            contact = {}
        message = {**message, "email": contact.get("email")}
    if should_email(message):
        try:
            mailer(message["email"], message.get("title", "Unbeatable Bank"), message.get("body", ""))
            result["email"] = "sent"
        except (urllib.error.URLError, RuntimeError, OSError) as e:
            # The inbox copy still gets written; an email failure shouldn't make Pub/Sub redeliver forever.
            log.warning("email to customer %s failed: %s", message["customer_id"], e)
            result["email"] = "failed"

    store.set("notifications", message["message_id"], {
        "customer_id": message["customer_id"],
        "type": message.get("type", "INFO"),
        "title": message.get("title", ""),
        "body": message.get("body", ""),
        "data": message.get("data") or {},
        "created_at": created,
        "read": False,
        "email": result["email"],
    })
    if message.get("type") == "OTP":
        store.set("sms_outbox", message["message_id"], {
            "customer_id": message["customer_id"], "text": message.get("body", ""), "created_at": created})
        result["sms"] = True
    log.info("notification %s type=%s customer=%s email=%s", message["message_id"], message.get("type"),
             message["customer_id"], result["email"])
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
