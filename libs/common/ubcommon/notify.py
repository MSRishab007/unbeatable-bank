"""Sending a notification to a customer.

Services publish a small message to the `notifications` Pub/Sub topic. In GCP
the Notification Cloud Function picks it up, writes it to the customer's inbox
in Firestore and sends it by email and RCS too. Which of those is chosen per
message (`channels`); by default the type decides: codes, security and fraud
messages go everywhere, routine ones only to the inbox. Locally there is no
function, so only the inbox write happens, in-process.

Inbox documents (collection `notifications`):
    customer_id, type, title, body, data, created_at (epoch seconds), read
"""

from __future__ import annotations

import os
import time
import uuid

from .docstore import DocStore
from .pubsub import Publisher, get_publisher

TOPIC = "notifications"


def inbox_document(message: dict) -> dict:
    """The document the Notification function stores for a message."""
    return {
        "customer_id": message["customer_id"],
        "type": message.get("type", "INFO"),
        "title": message.get("title", ""),
        "body": message.get("body", ""),
        "data": message.get("data") or {},
        "created_at": message.get("created_at") or time.time(),
        "read": False,
    }


class Notifier:
    def __init__(self, publisher: Publisher) -> None:
        self.publisher = publisher

    def send(self, customer_id, type: str, title: str, body: str, *, email: str | None = None,
             data: dict | None = None, message_id: str | None = None,
             channels: list[str] | None = None) -> str:
        """Pass a deterministic message_id when re-sending must not create a duplicate.

        channels: besides the inbox (always), any of "email", "rcs". None = by type."""
        message_id = message_id or uuid.uuid4().hex
        message = {
            "message_id": message_id, "customer_id": str(customer_id), "type": type, "title": title,
            "body": body, "email": email, "data": data or {}, "created_at": time.time(),
        }
        if channels is not None:
            message["channels"] = list(channels)
        self.publisher.publish(TOPIC, message)
        return message_id


def get_notifier(docstore: DocStore) -> Notifier:
    """In GCP: publish to Pub/Sub. Locally: write straight to the inbox."""
    def local_sink(_topic: str, message: dict, _attributes: dict) -> None:
        docstore.set("notifications", message["message_id"], inbox_document(message))

    return Notifier(get_publisher(None if os.getenv("PUBSUB") == "gcp" else local_sink))
