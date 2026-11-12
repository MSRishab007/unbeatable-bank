import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import main  # noqa: E402


class Store:
    def __init__(self):
        self.docs = {}

    def set(self, collection, doc_id, data):
        self.docs[(collection, doc_id)] = data

    def get(self, collection, doc_id):
        return self.docs.get((collection, doc_id))


def message(**over):
    m = {"message_id": "m1", "customer_id": "c1", "type": "FRAUD", "title": "Was this you?",
         "body": "A payment of Rs 20,000.00 is on hold.", "email": "riya@gmail.com",
         "data": {"txn_id": "t1"}, "created_at": 1_700_000_000.0}
    m.update(over)
    return m


def nothing(*_):
    raise AssertionError("must not be called")


def test_inbox_document_email_and_rcs():
    store, sent, texts = Store(), [], []
    result = main.handle(message(phone="+919876543210"), store, lambda to, subject, body: sent.append((to, subject)),
                         lambda phone, text, mid: texts.append((phone, mid)))
    assert result == {"inbox": True, "email": "sent", "rcs": "sent"}
    assert sent == [("riya@gmail.com", "Was this you?")] and texts == [("+919876543210", "m1")]
    doc = store.docs[("notifications", "m1")]
    assert doc["customer_id"] == "c1" and doc["read"] is False and doc["data"] == {"txn_id": "t1"}
    assert (doc["email"], doc["rcs"]) == ("sent", "sent")


def test_redelivery_overwrites_the_same_document():
    store = Store()
    main.handle(message(), store)
    main.handle(message(), store)
    assert len([k for k in store.docs if k[0] == "notifications"]) == 1


def test_routine_messages_go_to_the_inbox_only():
    result = main.handle(message(type="TXN", phone="+919876543210"), Store(), nothing, nothing)
    assert result == {"inbox": True, "email": "skipped", "rcs": "skipped"}


def test_a_message_can_choose_its_channels():
    store, sent = Store(), []
    only_email = main.handle(message(type="TXN", channels=["email"], phone="+919876543210"), store,
                             lambda *a: sent.append(a), nothing)
    assert only_email == {"inbox": True, "email": "sent", "rcs": "skipped"}
    none = main.handle(message(type="OTP", channels=[], phone="+919876543210"), store, nothing, nothing)
    assert none == {"inbox": True, "email": "skipped", "rcs": "skipped"}       # inbox is always written
    assert main.handle(message(channels=["pigeon"]), store, nothing, nothing)["email"] == "skipped"


def test_rcs_without_an_agent_goes_to_the_simulated_outbox(monkeypatch):
    monkeypatch.delenv("RBM_AGENT_ID", raising=False)
    store = Store()
    result = main.handle(message(type="OTP", body="Your code is 123456.", phone="+919876543210"), store, lambda *a: None)
    assert result["rcs"] == "simulated"
    assert store.docs[("rcs_outbox", "m1")]["text"] == "Was this you?\nYour code is 123456."


def test_no_email_without_a_configured_sender_or_for_test_users(monkeypatch):
    for var in ("BREVO_API_KEY", "SMTP_PASSWORD"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("EMAIL_FROM", "bank@gmail.com")
    assert main.handle(message(), Store())["email"] == "skipped"                     # nothing configured
    assert main.handle(message(email="bot7@loadtest.local"), Store(), nothing)["email"] == "skipped"
    assert main.handle(message(email=None), Store(), nothing)["email"] == "skipped"  # no address anywhere


def test_smtp_is_preferred_when_both_are_configured(monkeypatch):
    monkeypatch.setenv("EMAIL_FROM", "bank@gmail.com")
    monkeypatch.setenv("BREVO_API_KEY", "k")
    assert main.email_sender() is main.send_email_brevo
    monkeypatch.setenv("SMTP_PASSWORD", "app-password")
    assert main.email_sender() is main.send_email_smtp
    monkeypatch.delenv("EMAIL_FROM")
    assert main.email_sender() is None


def test_smtp_sends_with_starttls_and_login(monkeypatch):
    calls = []

    class FakeSMTP:
        def __init__(self, host, port, timeout):
            calls.append(("connect", host, port))

        def __enter__(self):
            return self

        def __exit__(self, *_):
            return False

        def starttls(self, context):
            calls.append(("starttls",))

        def login(self, user, password):
            calls.append(("login", user, password))

        def send_message(self, msg):
            calls.append(("send", msg["To"], msg["Subject"], "student project" in msg.get_content()))

    monkeypatch.setattr(main.smtplib, "SMTP", FakeSMTP)
    monkeypatch.setenv("EMAIL_FROM", "unbeatable.bank@gmail.com")
    monkeypatch.setenv("SMTP_PASSWORD", "app-password")
    main.send_email_smtp("riya@gmail.com", "Your code", "123456")
    assert calls == [("connect", "smtp.gmail.com", 587), ("starttls",),
                     ("login", "unbeatable.bank@gmail.com", "app-password"),
                     ("send", "riya@gmail.com", "Your code", True)]


def test_a_failed_channel_is_recorded_and_the_others_still_go():
    def broken(*_):
        raise RuntimeError("provider down")

    store, texts = Store(), []
    result = main.handle(message(phone="+919876543210"), store, broken, lambda *a: texts.append(a))
    assert result == {"inbox": True, "email": "failed", "rcs": "sent"} and texts
    assert store.docs[("notifications", "m1")]["email"] == "failed"


def test_malformed_message_is_dropped():
    store = Store()
    assert main.handle({"title": "no ids"}, store)["inbox"] is False and store.docs == {}


def test_address_and_phone_come_from_contacts_when_the_message_has_none():
    """Fraud, and identity's codes, don't carry them: Identity's contacts record does."""
    sent, texts = [], []
    store = Store()
    assert main.handle(message(email=None), store, lambda *a: sent.append(a))["email"] == "skipped"
    store.set("contacts", "c1", {"email": "riya@gmail.com", "phone": "+919876543210"})
    result = main.handle(message(email=None, type="OTP"), store, lambda *a: sent.append(a),
                         lambda *a: texts.append(a))
    assert result == {"inbox": True, "email": "sent", "rcs": "sent"}
    assert sent[0][0] == "riya@gmail.com" and texts[0][0] == "+919876543210"
