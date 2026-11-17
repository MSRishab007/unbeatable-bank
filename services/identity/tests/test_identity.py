import uuid
from datetime import date

import pytest

from ubcommon.auth import verify_token

from app import security, service

DELHI, MUMBAI, LONDON = (28.61, 77.21), (19.08, 72.88), (51.51, -0.13)


def login(conn, deps, user, fingerprint="laptop-fingerprint-1", password=None, **kw):
    return service.login(conn, deps, identifier=user["email"], password=password or user["password"],
                         fingerprint=fingerprint, device_label="Chrome on Windows", ip="203.0.113.7", **kw)


def login_new_device(conn, deps, user, fingerprint="phone-fingerprint-2", **kw):
    """A login from a device the customer hasn't confirmed: password, then the code we send."""
    challenge = login(conn, deps, user, fingerprint=fingerprint, **kw)
    assert challenge["otp_required"] and "access_token" not in challenge
    return service.verify_login(conn, deps, challenge_id=challenge["challenge_id"],
                                code=code_from_inbox(deps, user), ip="203.0.113.7")


def inbox(deps, user):
    return deps.docstore.query("notifications", {"customer_id": str(user["customer_id"])})


# ------------------------------------------------------------------ registration


def test_register_creates_customer_and_credentials(conn, deps, user):
    assert str(user["customer_id"]) in deps.accounts.customers
    stored = conn.execute("SELECT password_hash FROM identity.credentials WHERE customer_id = %s",
                          (user["customer_id"],)).fetchone()[0]
    assert user["password"] not in stored and security.verify_password(user["password"], stored)


def test_duplicate_email_is_rejected(conn, deps, user):
    with pytest.raises(service.Conflict) as exc:
        service.register(conn, deps, full_name="Someone Else", date_of_birth=date(1990, 1, 1),
                         phone="+919000000001", email=user["email"].upper(), password="another password")
    assert exc.value.details["field"] == "email"


def test_account_service_refusal_leaves_nothing_behind(conn, deps):
    deps.accounts.fail = service.Conflict("a customer with this pan already exists", field="pan")
    email = f"x{uuid.uuid4().hex[:8]}@example.com"
    with pytest.raises(service.Conflict):
        service.register(conn, deps, full_name="X Y", date_of_birth=date(1990, 1, 1), phone="+919111111111",
                         email=email, password="long enough pw")
    assert conn.execute("SELECT count(*) FROM identity.credentials WHERE email = %s", (email,)).fetchone()[0] == 0


def test_short_password_rejected(conn, deps):
    with pytest.raises(service.IdentityError):
        service.register(conn, deps, full_name="X Y", date_of_birth=date(1990, 1, 1), phone="+919111111112",
                         email="short@example.com", password="short")


# ------------------------------------------------------------------ login and tokens


def test_login_issues_tokens_and_records_session(conn, deps, user):
    out = login(conn, deps, user)
    claims = verify_token(out["access_token"], "test-secret")
    assert claims["sub"] == str(user["customer_id"]) and claims["role"] == "customer"
    assert claims["did"] == out["device_id"] and not out["new_device"]      # first device is not "new"
    sessions = service.list_sessions(deps, user["customer_id"])
    assert len(sessions) == 1 and sessions[0]["session_id"] == claims["sid"]
    assert service.login_history(deps, user["customer_id"])[0]["result"] == "OK"
    # by phone works too
    assert service.login(conn, deps, identifier=user["phone"], password=user["password"],
                         fingerprint="laptop-fingerprint-1")["device_id"] == out["device_id"]


def test_wrong_password_then_lockout(conn, deps, user):
    for attempt in range(4):
        with pytest.raises(service.InvalidCredentials) as exc:
            login(conn, deps, user, password="wrong password")
        assert exc.value.details["attempts_left"] == 4 - attempt
    with pytest.raises(service.InvalidCredentials):
        login(conn, deps, user, password="wrong password")       # fifth failure locks
    with pytest.raises(service.Locked):
        login(conn, deps, user)                                   # even the right password now
    deps.clock.now += 16 * 60
    assert login(conn, deps, user)["access_token"]


def test_unknown_user_gets_the_same_error(conn, deps):
    with pytest.raises(service.InvalidCredentials) as exc:
        service.login(conn, deps, identifier="nobody@example.com", password="whatever", fingerprint="f" * 10)
    assert str(exc.value) == "email/phone or password is incorrect"


def test_login_rate_limit_hook(conn, deps, user):
    deps.login_allowed = lambda identifier, ip: False
    with pytest.raises(service.TooManyRequests):
        login(conn, deps, user)


def test_refresh_and_logout(conn, deps, user):
    out = login(conn, deps, user)
    refreshed = service.refresh(deps, out["refresh_token"])
    assert refreshed["refresh_token"] != out["refresh_token"]          # rotated on every use
    sid = verify_token(refreshed["access_token"], "test-secret")["sid"]
    assert service.session_is_active(deps, sid)
    with pytest.raises(service.InvalidCredentials):
        service.refresh(deps, refreshed["refresh_token"] + "x")
    again = service.refresh(deps, refreshed["refresh_token"])
    service.logout(deps, user["customer_id"], sid)
    assert not service.session_is_active(deps, sid)
    assert service.check_session(deps, sid)["state"] == "ENDED"
    with pytest.raises(service.InvalidCredentials):
        service.refresh(deps, again["refresh_token"])


def test_reusing_an_old_refresh_token_ends_the_session(conn, deps, user):
    out = login(conn, deps, user)
    thief = service.refresh(deps, out["refresh_token"])               # someone copied the token and used it
    with pytest.raises(service.InvalidCredentials):
        service.refresh(deps, out["refresh_token"])                   # the real app then tries the old one
    with pytest.raises(service.InvalidCredentials):
        service.refresh(deps, thief["refresh_token"])                 # and the thief's copy is dead too
    sid = verify_token(thief["access_token"], "test-secret")["sid"]
    assert service.check_session(deps, sid)["state"] == "ENDED"
    assert f"sig:cust:{user['customer_id']}:token_reuse" in deps.signals.keys
    assert "We signed you out" in [n["title"] for n in inbox(deps, user)]


def test_session_expires(conn, deps, user):
    out = login(conn, deps, user)
    deps.clock.now += 8 * 86400
    with pytest.raises(service.InvalidCredentials):
        service.refresh(deps, out["refresh_token"])


# ------------------------------------------------------------------ devices and fraud signals


def test_new_device_is_flagged_and_customer_is_told(conn, deps, user):
    first = login(conn, deps, user)
    signup_device_seen = deps.signals.keys[f"seen:dev:{user['customer_id']}:{first['device_id']}"]
    assert deps.clock.now - signup_device_seen > 86400          # sign-up device is trusted from the start
    deps.clock.now += 3600
    second = login_new_device(conn, deps, user)
    assert second["new_device"] and second["device_id"] != first["device_id"]
    assert f"sig:cust:{user['customer_id']}:new_device_login" in deps.signals.keys
    assert deps.signals.keys[f"seen:dev:{user['customer_id']}:{second['device_id']}"] == deps.clock.now
    assert [n["title"] for n in inbox(deps, user)] == ["Confirmation code", "New device signed in"]
    assert login(conn, deps, user, fingerprint="phone-fingerprint-2")["access_token"]   # trusted from now on
    event = conn.execute("SELECT payload FROM identity.outbox WHERE event_type = 'LoginSucceeded' "
                         "AND payload->>'device_id' = %s", (second["device_id"],)).fetchone()[0]
    assert event["new_device"] is True
    assert len(service.list_devices(conn, user["customer_id"])) == 2


def test_revoke_device_ends_its_sessions(conn, deps, user):
    laptop = login(conn, deps, user)
    phone = login_new_device(conn, deps, user)
    result = service.revoke_device(conn, deps, user["customer_id"], uuid.UUID(phone["device_id"]))
    assert result["sessions_ended"] == 1
    with pytest.raises(service.InvalidCredentials):
        service.refresh(deps, phone["refresh_token"])
    assert service.refresh(deps, laptop["refresh_token"])["access_token"]   # other device unaffected
    assert len(service.list_devices(conn, user["customer_id"])) == 1
    assert login(conn, deps, user, fingerprint="phone-fingerprint-2")["otp_required"]   # must earn trust again
    with pytest.raises(service.NotFound):
        service.revoke_device(conn, deps, uuid.uuid4(), uuid.UUID(laptop["device_id"]))   # someone else


def test_impossible_travel(conn, deps, user):
    login(conn, deps, user, geo=DELHI)
    deps.clock.now += 2 * 3600
    login(conn, deps, user, geo=MUMBAI)                          # ~1150 km in 2 h: a flight, plausible
    assert f"sig:cust:{user['customer_id']}:impossible_travel" not in deps.signals.keys
    deps.clock.now += 30 * 60
    login(conn, deps, user, geo=LONDON)                          # ~7200 km in 30 min: not possible
    assert f"sig:cust:{user['customer_id']}:impossible_travel" in deps.signals.keys
    assert service.login_history(deps, user["customer_id"])[0]["impossible_travel"] is True


def test_signals_are_best_effort(conn, deps, user):
    class Broken:
        def put(self, *a, **k):
            raise RuntimeError("store down")
        put_if_absent = put

    deps.signals = Broken()
    assert login(conn, deps, user)["access_token"]               # login still works


# ------------------------------------------------------------------ OTP


def code_from_inbox(deps, user):
    body = [n for n in inbox(deps, user) if n["type"] == "OTP"][-1]["body"]
    return body.split("Your code is ")[1][:6]


def test_otp_round_trip(conn, deps, user):
    service.issue_otp(conn, deps, user["customer_id"], "txn:abc", "Confirm payment of Rs 30,000.00 to Arjun M****")
    code = code_from_inbox(deps, user)
    assert code not in conn.execute("SELECT code_hash FROM identity.otps WHERE customer_id = %s",
                                    (user["customer_id"],)).fetchone()[0]          # only a hash is stored
    wrong = "000000" if code != "000000" else "111111"
    assert service.verify_otp(conn, user["customer_id"], "txn:abc", wrong) == "WRONG"
    assert service.verify_otp(conn, user["customer_id"], "txn:other", code) == "LOCKED"   # wrong purpose
    assert service.verify_otp(conn, user["customer_id"], "txn:abc", code) == "OK"
    assert service.verify_otp(conn, user["customer_id"], "txn:abc", code) == "LOCKED"     # single use


def test_otp_locks_after_three_wrong_attempts(conn, deps, user):
    service.issue_otp(conn, deps, user["customer_id"], "txn:x")
    code = code_from_inbox(deps, user)
    wrong = "000000" if code != "000000" else "111111"
    assert [service.verify_otp(conn, user["customer_id"], "txn:x", wrong) for _ in range(3)] \
        == ["WRONG", "WRONG", "LOCKED"]
    assert service.verify_otp(conn, user["customer_id"], "txn:x", code) == "LOCKED"


def test_new_otp_replaces_the_old_one(conn, deps, user):
    service.issue_otp(conn, deps, user["customer_id"], "txn:y")
    old = code_from_inbox(deps, user)
    service.issue_otp(conn, deps, user["customer_id"], "txn:y")
    new = code_from_inbox(deps, user)
    if old != new:
        assert service.verify_otp(conn, user["customer_id"], "txn:y", old) == "WRONG"
    assert service.verify_otp(conn, user["customer_id"], "txn:y", new) == "OK"


# ------------------------------------------------------------------ staff


def test_staff_login_carries_role(conn, deps):
    staff_id = f"asha{uuid.uuid4().hex[:6]}"
    service.upsert_staff(conn, staff_id, "ANALYST", "analyst password")
    out = service.staff_login(conn, deps, staff_id.upper(), "analyst password")
    claims = verify_token(out["access_token"], "test-secret")
    assert (claims["sub"], claims["role"], claims["staff_role"]) == (staff_id, "staff", "ANALYST")
    with pytest.raises(service.InvalidCredentials):
        service.staff_login(conn, deps, staff_id, "wrong")
    with pytest.raises(service.InvalidCredentials):
        service.staff_login(conn, deps, "ghost", "whatever")
