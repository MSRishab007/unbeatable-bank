import smtplib
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import main  # noqa: E402


class Store:
    def __init__(self):
        self.docs = {}

    def set(self, collection, doc_id, data):
        self.docs[(collection, doc_id)] = data

    def get(self, collection, doc_id):
        return self.docs.get((collection, doc_id))

    def merge(self, collection, doc_id, data):
        self.docs[(collection, doc_id)] = {**self.docs.get((collection, doc_id), {}), **data}

    def increment(self, collection, doc_id, field):
        doc = self.docs.setdefault((collection, doc_id), {})
        doc[field] = doc.get(field, 0) + 1


@pytest.fixture(autouse=True)
def fresh_settings(monkeypatch):
    monkeypatch.setattr(main, "_settings_cache", (0.0, {}))


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
    assert result == {"inbox": True, "email": "sent", "email_via": "gmail", "rcs": "sent"}
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
    assert only_email == {"inbox": True, "email": "sent", "email_via": "gmail", "rcs": "skipped"}
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


def test_both_providers_are_found_gmail_first(monkeypatch):
    monkeypatch.setenv("EMAIL_FROM", "bank@gmail.com")
    monkeypatch.setenv("BREVO_API_KEY", "k")
    monkeypatch.delenv("SMTP_PASSWORD", raising=False)
    assert list(main.email_providers()) == ["brevo"]
    monkeypatch.setenv("SMTP_PASSWORD", "app-password")
    assert list(main.email_providers()) == ["gmail", "brevo"]
    monkeypatch.delenv("EMAIL_FROM")
    assert main.email_providers() == {}


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
    assert result == {"inbox": True, "email": "failed", "email_via": "", "rcs": "sent"} and texts
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
    assert result == {"inbox": True, "email": "sent", "email_via": "gmail", "rcs": "sent"}
    assert sent[0][0] == "riya@gmail.com" and texts[0][0] == "+919876543210"


# ------------------------------------------------------------------ admin switches and Gmail -> Brevo


def recorder(name, log):
    return lambda to, subject, body: log.append((name, to))


def quota_hit(*_):
    raise smtplib.SMTPDataError(550, b"5.4.5 Daily user sending limit exceeded.")


def test_admin_can_switch_email_and_rcs_off():
    store, sent, texts = Store(), [], []
    store.set("settings", "notifications", {"email_enabled": False, "rcs_enabled": False})
    result = main.handle(message(phone="+919876543210"), store, recorder("gmail", sent), lambda *a: texts.append(a))
    assert result == {"inbox": True, "email": "off", "rcs": "off"} and sent == texts == []
    assert store.docs[("notifications", "m1")]["email"] == "off"                  # the inbox still gets it


def test_auto_uses_gmail_and_brevo_takes_over_at_the_gmail_limit():
    store, sent = Store(), []
    both = {"gmail": recorder("gmail", sent), "brevo": recorder("brevo", sent)}
    assert main.handle(message(message_id="a"), store, both, now=1000.0)["email_via"] == "gmail"
    hit = {"gmail": quota_hit, "brevo": recorder("brevo", sent)}
    out = main.handle(message(message_id="b"), store, hit, now=2000.0)
    assert out["email"] == "sent" and out["email_via"] == "brevo"
    assert store.docs[("settings", "notifications_state")]["gmail_paused_until"] == 2000.0 + 24 * 3600
    # Gmail is left alone for a day...
    assert main.handle(message(message_id="c"), store, both, now=3000.0)["email_via"] == "brevo"
    # ...then tried again.
    main._settings_cache = (0.0, {})
    assert main.handle(message(message_id="d"), store, both, now=2000.0 + 24 * 3600 + 1)["email_via"] == "gmail"
    stats = store.docs[("notification_stats", "1970-01-01")]
    assert stats["gmail"] == 1 and stats["brevo"] == 2 and stats["gmail_failed"] == 1


def test_any_gmail_failure_falls_back_to_brevo_without_pausing_gmail():
    store, sent = Store(), []

    def broken(*_):
        raise OSError("connection reset")

    out = main.handle(message(), store, {"gmail": broken, "brevo": recorder("brevo", sent)}, now=5000.0)
    assert out["email_via"] == "brevo" and ("settings", "notifications_state") not in store.docs


def test_admin_can_pin_one_provider():
    store, sent = Store(), []
    both = {"gmail": recorder("gmail", sent), "brevo": recorder("brevo", sent)}
    store.set("settings", "notifications", {"email_provider": "brevo"})
    assert main.handle(message(message_id="a"), store, both)["email_via"] == "brevo"
    main._settings_cache = (0.0, {})
    store.set("settings", "notifications", {"email_provider": "gmail"})
    out = main.handle(message(message_id="b"), store, {"gmail": quota_hit, "brevo": recorder("brevo", sent)})
    assert out["email"] == "failed"                                                  # pinned: no fallback


def test_settings_are_reused_for_a_short_while():
    store, sent = Store(), []
    main.handle(message(message_id="a"), store, recorder("gmail", sent), now=100.0)
    store.set("settings", "notifications", {"email_enabled": False})
    assert main.handle(message(message_id="b"), store, recorder("gmail", sent), now=110.0)["email"] == "sent"
    assert main.handle(message(message_id="c"), store, recorder("gmail", sent), now=200.0)["email"] == "off"


def test_quota_errors_are_told_apart():
    assert main.is_quota_error(smtplib.SMTPDataError(550, b"5.4.5 Daily user sending limit exceeded."))
    assert main.is_quota_error(smtplib.SMTPSenderRefused(421, b"4.7.0 Try again later, rate limited", "x"))
    assert not main.is_quota_error(smtplib.SMTPAuthenticationError(535, b"5.7.8 Username and Password not accepted"))
    assert not main.is_quota_error(OSError("connection reset"))
