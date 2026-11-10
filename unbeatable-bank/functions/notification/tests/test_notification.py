import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import main  # noqa: E402


class Store:
    def __init__(self):
        self.docs = {}

    def set(self, collection, doc_id, data):
        self.docs[(collection, doc_id)] = data


def message(**over):
    m = {"message_id": "m1", "customer_id": "c1", "type": "FRAUD", "title": "Was this you?",
         "body": "A payment of Rs 20,000.00 is on hold.", "email": "riya@gmail.com",
         "data": {"txn_id": "t1"}, "created_at": 1_700_000_000.0}
    m.update(over)
    return m


def test_inbox_document_and_email(monkeypatch):
    monkeypatch.setenv("BREVO_API_KEY", "key")
    store, sent = Store(), []
    result = main.handle(message(), store, lambda to, subject, body: sent.append((to, subject)))
    assert result == {"inbox": True, "sms": False, "email": "sent"}
    assert sent == [("riya@gmail.com", "Was this you?")]
    doc = store.docs[("notifications", "m1")]
    assert doc["customer_id"] == "c1" and doc["read"] is False and doc["data"] == {"txn_id": "t1"}


def test_redelivery_overwrites_the_same_document(monkeypatch):
    store = Store()
    main.handle(message(), store)
    main.handle(message(), store)
    assert len(store.docs) == 1


def test_otp_also_goes_to_the_simulated_sms_outbox():
    store = Store()
    result = main.handle(message(type="OTP", body="Your code is 123456."), store)
    assert result["sms"] and store.docs[("sms_outbox", "m1")]["text"] == "Your code is 123456."


def test_no_email_without_key_for_routine_messages_or_test_users(monkeypatch):
    sent = []
    mailer = lambda *a: sent.append(a)
    assert main.handle(message(), Store(), mailer)["email"] == "skipped"            # no API key
    monkeypatch.setenv("BREVO_API_KEY", "key")
    assert main.handle(message(type="TXN"), Store(), mailer)["email"] == "skipped"  # routine outcome
    assert main.handle(message(email="bot7@loadtest.local"), Store(), mailer)["email"] == "skipped"
    assert main.handle(message(email=None), Store(), mailer)["email"] == "skipped"
    assert sent == []


def test_email_failure_still_writes_the_inbox(monkeypatch):
    monkeypatch.setenv("BREVO_API_KEY", "key")

    def broken(*_):
        raise RuntimeError("provider down")

    store = Store()
    assert main.handle(message(), store, broken)["email"] == "failed"
    assert store.docs[("notifications", "m1")]["email"] == "failed"


def test_malformed_message_is_dropped():
    store = Store()
    assert main.handle({"title": "no ids"}, store)["inbox"] is False and store.docs == {}
