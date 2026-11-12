"""PINs, session locking, new-device codes, password and contact changes, lockdown."""

import uuid
from datetime import date

import pytest
from fastapi.testclient import TestClient

from ubcommon.auth import verify_token

from app import pins, recovery, service
from test_identity import code_from_inbox, inbox, login

DOB = date(2001, 5, 17)       # the `user` fixture's date of birth


def sid_of(tokens):
    return verify_token(tokens["access_token"], "test-secret")["sid"]


def other(code):
    return "000000" if code != "000000" else "111111"


# ------------------------------------------------------------------ new-device codes


def test_contact_record_is_kept_for_the_notification_function(conn, deps, user):
    login(conn, deps, user)
    contact = deps.docstore.get("contacts", str(user["customer_id"]))
    assert (contact["email"], contact["phone"]) == (user["email"], user["phone"])


def test_wrong_device_codes_end_the_attempt(conn, deps, user):
    login(conn, deps, user)
    challenge = login(conn, deps, user, fingerprint="stolen-laptop")
    code = code_from_inbox(deps, user)
    for _ in range(2):
        with pytest.raises(service.WrongSecret):
            service.verify_login(conn, deps, challenge_id=challenge["challenge_id"], code=other(code))
    with pytest.raises(service.InvalidCredentials):
        service.verify_login(conn, deps, challenge_id=challenge["challenge_id"], code=other(code))
    with pytest.raises(service.InvalidCredentials):            # the right code is no use now
        service.verify_login(conn, deps, challenge_id=challenge["challenge_id"], code=code)
    assert [d["trusted"] for d in service.list_devices(conn, user["customer_id"])].count(False) == 1
    assert f"sig:cust:{user['customer_id']}:new_device_login" not in deps.signals.keys


def test_device_code_expires(conn, deps, user):
    login(conn, deps, user)
    challenge = login(conn, deps, user, fingerprint="new-phone-123")
    deps.clock.now += service.OTP_TTL_SECONDS + 1
    with pytest.raises(service.InvalidCredentials):
        service.verify_login(conn, deps, challenge_id=challenge["challenge_id"], code=code_from_inbox(deps, user))


def test_device_code_message_names_the_device(conn, deps, user):
    login(conn, deps, user)
    login(conn, deps, user, fingerprint="new-phone-123")
    body = [n for n in inbox(deps, user) if n["type"] == "OTP"][-1]["body"]
    assert "new device (Chrome on Windows)" in body and "change your password" in body


# ------------------------------------------------------------------ idle lock and unlock


def test_session_locks_when_idle_and_unlocks_with_app_pin(conn, deps, user):
    sid = sid_of(login(conn, deps, user))
    pins.set_pin(conn, deps, user["customer_id"], "APP", "4826", password=user["password"])
    deps.clock.now += 200
    assert service.check_session(deps, sid)["state"] == "ACTIVE"          # activity resets the clock
    deps.clock.now += 200
    assert service.check_session(deps, sid)["state"] == "ACTIVE"
    deps.clock.now += 301
    assert service.check_session(deps, sid, activity=False)["state"] == "ACTIVE"   # background poll: no change
    assert service.check_session(deps, sid)["state"] == "LOCKED"
    assert service.check_session(deps, sid, activity=False)["state"] == "LOCKED"
    with pytest.raises(service.WrongSecret) as wrong:
        service.unlock_session(conn, deps, user["customer_id"], sid, pin="1357")
    assert wrong.value.details["attempts_left"] == 2
    assert service.unlock_session(conn, deps, user["customer_id"], sid, pin="4826")["state"] == "ACTIVE"
    assert service.check_session(deps, sid)["state"] == "ACTIVE"
    assert pins.status(conn, deps, user["customer_id"])["APP"]["attempts_left"] == 3   # success clears failures


def test_manual_lock_and_unlock_with_password(conn, deps, user):
    sid = sid_of(login(conn, deps, user))
    service.lock_session(deps, user["customer_id"], sid)
    assert service.check_session(deps, sid) == {"state": "LOCKED", "reason": "BY_CUSTOMER"}
    with pytest.raises(service.PinNotSet):                                  # no app PIN yet
        service.unlock_session(conn, deps, user["customer_id"], sid, pin="4826")
    assert service.unlock_session(conn, deps, user["customer_id"], sid, password=user["password"])["state"] == "ACTIVE"


def test_wrong_unlocks_end_the_session(conn, deps, user):
    sid = sid_of(login(conn, deps, user))
    service.lock_session(deps, user["customer_id"], sid)
    for _ in range(service.MAX_UNLOCK_FAILURES - 1):
        with pytest.raises(service.WrongSecret):
            service.unlock_session(conn, deps, user["customer_id"], sid, password="guess guess")
    with pytest.raises(service.InvalidCredentials):
        service.unlock_session(conn, deps, user["customer_id"], sid, password="guess guess")
    assert service.check_session(deps, sid)["state"] == "ENDED"


def test_a_locked_app_pin_ends_the_session_it_was_tried_on(conn, deps, user):
    sid = sid_of(login(conn, deps, user))
    pins.set_pin(conn, deps, user["customer_id"], "APP", "4826", password=user["password"])
    service.lock_session(deps, user["customer_id"], sid)
    for _ in range(2):
        with pytest.raises(service.WrongSecret):
            service.unlock_session(conn, deps, user["customer_id"], sid, pin="1357")
    with pytest.raises(service.InvalidCredentials):
        service.unlock_session(conn, deps, user["customer_id"], sid, pin="1357")
    assert service.check_session(deps, sid)["state"] == "ENDED"


def test_cannot_lock_someone_elses_session(conn, deps, user):
    sid = sid_of(login(conn, deps, user))
    with pytest.raises(service.InvalidCredentials):
        service.lock_session(deps, uuid.uuid4(), sid)


def test_sessions_list_marks_the_current_one_and_others_can_be_ended(conn, deps, user):
    a = sid_of(login(conn, deps, user))
    b = sid_of(login(conn, deps, user))
    listed = {s["session_id"]: s["current"] for s in service.list_sessions(deps, user["customer_id"], current=a)}
    assert listed == {a: True, b: False}
    service.end_other_session(deps, user["customer_id"], b)
    assert service.check_session(deps, b)["state"] == "ENDED"
    with pytest.raises(service.NotFound):
        service.end_other_session(deps, uuid.uuid4(), a)


# ------------------------------------------------------------------ staff sessions


def test_staff_sessions_can_be_ended_and_time_out(conn, deps):
    staff_id = f"ravi{uuid.uuid4().hex[:6]}"
    service.upsert_staff(conn, staff_id, "TELLER", "teller password")
    out = service.staff_login(conn, deps, staff_id, "teller password")
    sid = sid_of(out)
    assert service.check_session(deps, sid)["state"] == "ACTIVE"
    deps.clock.now += deps.staff_idle_seconds + 1
    assert service.check_session(deps, sid)["state"] == "ENDED"           # staff: ends, no PIN unlock
    sid2 = sid_of(service.staff_login(conn, deps, staff_id, "teller password"))
    service.staff_logout(deps, "someone-else", sid2)
    assert service.check_session(deps, sid2)["state"] == "ACTIVE"
    service.staff_logout(deps, staff_id, sid2)
    assert service.check_session(deps, sid2)["state"] == "ENDED"
    with pytest.raises(service.InvalidCredentials):                       # staff sessions can't be refreshed
        service.refresh(deps, f"{sid2}.whatever")


# ------------------------------------------------------------------ PINs


@pytest.mark.parametrize("kind,pin", [("PAYMENT", "12345"), ("PAYMENT", "12345a"), ("APP", "12345"),
                                      ("PAYMENT", "111111"), ("PAYMENT", "123456"), ("APP", "9876"),
                                      ("APP", "0000")])
def test_weak_or_malformed_pins_are_refused(conn, deps, user, kind, pin):
    with pytest.raises(service.IdentityError):
        pins.set_pin(conn, deps, user["customer_id"], kind, pin, password=user["password"])


def test_setting_a_pin_needs_the_password_and_they_must_differ(conn, deps, user):
    cid = user["customer_id"]
    with pytest.raises(service.IdentityError):
        pins.set_pin(conn, deps, cid, "PAYMENT", "583921")                       # nothing to prove who you are
    with pytest.raises(service.WrongSecret):
        pins.set_pin(conn, deps, cid, "PAYMENT", "583921", password="not it at all")
    pins.set_pin(conn, deps, cid, "PAYMENT", "583921", password=user["password"])
    for clash in ("5839", "3921"):                                               # start or end of the payment PIN
        with pytest.raises(service.IdentityError, match="different"):
            pins.set_pin(conn, deps, cid, "APP", clash, password=user["password"])
    pins.set_pin(conn, deps, cid, "APP", "4826", password=user["password"])
    with pytest.raises(service.IdentityError, match="different"):               # and the other way round
        pins.set_pin(conn, deps, cid, "PAYMENT", "482671", current_pin="583921")
    status = pins.status(conn, deps, cid)
    assert status["PAYMENT"]["set"] and status["APP"]["set"] and not status["APP"]["locked"]
    stored = conn.execute("SELECT pin_hash FROM identity.pins WHERE customer_id = %s AND kind = 'PAYMENT'",
                          (cid,)).fetchone()[0]
    assert "583921" not in stored and stored.startswith("hmac$")
    titles = [n["title"] for n in inbox(deps, user)]
    assert "Your payment PIN was set" in titles and "Your app PIN was set" in titles


def test_changing_a_pin_with_the_current_one(conn, deps, user):
    cid = user["customer_id"]
    pins.set_pin(conn, deps, cid, "PAYMENT", "583921", password=user["password"])
    with pytest.raises(service.WrongSecret):
        pins.set_pin(conn, deps, cid, "PAYMENT", "640282", current_pin="000001")
    pins.set_pin(conn, deps, cid, "PAYMENT", "640282", current_pin="583921")
    assert pins.verify(conn, deps, cid, "PAYMENT", "640282")["result"] == "OK"
    assert pins.verify(conn, deps, cid, "PAYMENT", "583921")["result"] == "WRONG"


def test_three_wrong_pins_lock_it_for_a_day(conn, deps, user):
    cid = user["customer_id"]
    assert pins.verify(conn, deps, cid, "PAYMENT", "583921") == {"result": "NOT_SET"}
    assert pins.verify(conn, deps, cid, "PAYMENT", "") == {"result": "NOT_SET"}
    pins.set_pin(conn, deps, cid, "PAYMENT", "583921", password=user["password"])
    assert pins.verify(conn, deps, cid, "PAYMENT", "") == {"result": "REQUIRED"}         # asking isn't a try
    assert pins.verify(conn, deps, cid, "PAYMENT", "000001") == {"result": "WRONG", "attempts_left": 2}
    assert pins.verify(conn, deps, cid, "PAYMENT", "583921")["result"] == "OK"        # a success resets the count
    assert pins.verify(conn, deps, cid, "PAYMENT", "000001")["attempts_left"] == 2
    assert pins.verify(conn, deps, cid, "PAYMENT", "000001")["attempts_left"] == 1
    assert pins.verify(conn, deps, cid, "PAYMENT", "000001")["result"] == "LOCKED"
    assert pins.verify(conn, deps, cid, "PAYMENT", "583921")["result"] == "LOCKED"    # even the right one
    assert pins.verify(conn, deps, cid, "APP", "4826")["result"] == "NOT_SET"         # the other PIN is separate
    assert f"sig:cust:{cid}:pin_locked" in deps.signals.keys
    assert "Your payment PIN is locked" in [n["title"] for n in inbox(deps, user)]
    assert conn.execute("SELECT count(*) FROM identity.outbox WHERE event_type = 'PinLocked' "
                        "AND event_key = %s", (str(cid),)).fetchone()[0] == 1
    with pytest.raises(service.PinLocked):
        pins.set_pin(conn, deps, cid, "PAYMENT", "640282", password=user["password"])
    deps.clock.now += 24 * 3600 + 1
    assert pins.verify(conn, deps, cid, "PAYMENT", "583921")["result"] == "OK"        # the lock lapses


def test_reset_a_locked_pin_with_password_and_code(conn, deps, user):
    cid = user["customer_id"]
    pins.set_pin(conn, deps, cid, "PAYMENT", "583921", password=user["password"])
    for _ in range(3):
        pins.verify(conn, deps, cid, "PAYMENT", "000001")
    pins.request_reset(conn, deps, cid, "PAYMENT")
    code = code_from_inbox(deps, user)
    with pytest.raises(service.WrongSecret):
        pins.reset(conn, deps, cid, "PAYMENT", code=code, password="wrong password!", new_pin="640282")
    with pytest.raises(service.WrongSecret):
        pins.reset(conn, deps, cid, "PAYMENT", code=other(code), password=user["password"], new_pin="640282")
    out = pins.reset(conn, deps, cid, "PAYMENT", code=code, password=user["password"], new_pin="640282")
    assert out["set"] and not out["locked"]
    assert pins.verify(conn, deps, cid, "PAYMENT", "640282")["result"] == "OK"
    assert f"sig:cust:{cid}:credential_reset" in deps.signals.keys
    with pytest.raises(service.IdentityError):                                   # the code was used up
        pins.reset(conn, deps, cid, "PAYMENT", code=code, password=user["password"], new_pin="702938")


# ------------------------------------------------------------------ passwords


def test_change_password_ends_every_other_session(conn, deps, user):
    here = sid_of(login(conn, deps, user))
    there = sid_of(login(conn, deps, user))
    with pytest.raises(service.WrongSecret):
        recovery.change_password(conn, deps, user["customer_id"], here, current_password="nope nope",
                                 new_password="a brand new password")
    with pytest.raises(service.IdentityError):
        recovery.change_password(conn, deps, user["customer_id"], here, current_password=user["password"],
                                 new_password=user["password"])                  # same as before
    out = recovery.change_password(conn, deps, user["customer_id"], here, current_password=user["password"],
                                   new_password="a brand new password")
    assert out == {"other_sessions_ended": 1}
    assert service.check_session(deps, here)["state"] == "ACTIVE"
    assert service.check_session(deps, there)["state"] == "ENDED"
    with pytest.raises(service.InvalidCredentials):
        login(conn, deps, user)
    assert login(conn, deps, user, password="a brand new password")["access_token"]


def test_forgot_password_needs_the_code_and_date_of_birth(conn, deps, user):
    sid = sid_of(login(conn, deps, user))
    assert recovery.forgot_password(conn, deps, "nobody@example.com")["sent"]     # same answer for strangers
    recovery.forgot_password(conn, deps, user["email"])
    code = code_from_inbox(deps, user)
    with pytest.raises(service.IdentityError):                                    # wrong DOB uses the code up
        recovery.reset_password(conn, deps, identifier=user["email"], code=code, date_of_birth=date(2000, 1, 1),
                                new_password="reset password 1")
    with pytest.raises(service.IdentityError):
        recovery.reset_password(conn, deps, identifier=user["email"], code=code, date_of_birth=DOB,
                                new_password="reset password 1")
    recovery.forgot_password(conn, deps, user["phone"])                           # phone works as identifier
    code = code_from_inbox(deps, user)
    out = recovery.reset_password(conn, deps, identifier=user["phone"], code=code, date_of_birth=DOB,
                                  new_password="reset password 1")
    assert out["reset"] and out["sessions_ended"] == 1
    assert service.check_session(deps, sid)["state"] == "ENDED"
    assert login(conn, deps, user, password="reset password 1")["access_token"]
    assert f"sig:cust:{user['customer_id']}:credential_reset" in deps.signals.keys


def test_reset_password_clears_a_login_lockout(conn, deps, user):
    for _ in range(service.MAX_FAILED_LOGINS):
        with pytest.raises(service.InvalidCredentials):
            login(conn, deps, user, password="wrong wrong")
    with pytest.raises(service.Locked):
        login(conn, deps, user)
    recovery.forgot_password(conn, deps, user["email"])
    recovery.reset_password(conn, deps, identifier=user["email"], code=code_from_inbox(deps, user),
                            date_of_birth=DOB, new_password="reset password 2")
    assert login(conn, deps, user, password="reset password 2")["access_token"]


# ------------------------------------------------------------------ contact details


def test_contact_change_needs_a_code_and_is_only_a_risk_signal(conn, deps, user):
    cid = user["customer_id"]
    deps.accounts.customers[str(cid)].update(email=user["email"])
    new_email = f"riya.new{uuid.uuid4().hex[:6]}@example.com"
    with pytest.raises(service.WrongSecret):
        recovery.start_contact_change(conn, deps, cid, field="email", value=new_email, password="nope nope")
    with pytest.raises(service.IdentityError):
        recovery.start_contact_change(conn, deps, cid, field="email", value="not-an-email", password=user["password"])
    recovery.start_contact_change(conn, deps, cid, field="email", value=new_email.upper(), password=user["password"])
    otp = [n for n in inbox(deps, user) if n["type"] == "OTP"][-1]
    assert "change your email to ri" in otp["body"] and new_email not in otp["body"]     # masked
    code = code_from_inbox(deps, user)
    with pytest.raises(service.WrongSecret):
        recovery.confirm_contact_change(conn, deps, cid, code=other(code))
    out = recovery.confirm_contact_change(conn, deps, cid, code=code)
    assert out == {"changed": "email", "value": new_email}
    assert deps.accounts.customers[str(cid)]["email"] == new_email
    assert login(conn, deps, {**user, "email": new_email})["access_token"]
    assert deps.docstore.get("contacts", str(cid))["email"] == new_email      # where emails will now go
    assert f"sig:cust:{cid}:contact_changed" in deps.signals.keys
    notice = [n for n in inbox(deps, user) if n["title"] == "Your email was changed"]
    assert notice                                                                 # told about it


def test_contact_change_is_all_or_nothing(conn, deps, user):
    cid = user["customer_id"]
    recovery.start_contact_change(conn, deps, cid, field="phone", value="+91 99999 00001", password=user["password"])
    deps.accounts.fail = service.Unavailable("account service down")
    with pytest.raises(service.Unavailable):
        recovery.confirm_contact_change(conn, deps, cid, code=code_from_inbox(deps, user))
    phone = conn.execute("SELECT phone FROM identity.credentials WHERE customer_id = %s", (cid,)).fetchone()[0]
    assert phone == user["phone"]


def test_contact_change_refuses_a_taken_email(conn, deps, user):
    taken = service.register(conn, deps, full_name="Arjun Mehta", date_of_birth=DOB, phone=f"+9188{uuid.uuid4().int % 10**8:08d}",
                             email=f"arjun{uuid.uuid4().hex[:6]}@example.com", password="arjun password")
    with pytest.raises(service.Conflict):
        recovery.start_contact_change(conn, deps, user["customer_id"], field="email", value=taken["email"],
                                      password=user["password"])


# ------------------------------------------------------------------ block everything


def test_lockdown_ends_every_session(conn, deps, user):
    a, b = sid_of(login(conn, deps, user)), sid_of(login(conn, deps, user))
    out = service.lockdown(conn, deps, user["customer_id"], f"customer:{user['customer_id']}")
    assert out == {"sessions_ended": 2}
    assert {service.check_session(deps, s)["state"] for s in (a, b)} == {"ENDED"}
    assert f"sig:cust:{user['customer_id']}:lockdown" in deps.signals.keys


# ------------------------------------------------------------------ over HTTP


@pytest.fixture
def api(db_url, deps, monkeypatch):
    from app import main

    monkeypatch.setenv("DATABASE_URL", db_url)
    monkeypatch.setitem(main._state, "deps", deps)
    with TestClient(main.app) as client:
        yield client


def test_http_new_device_login_is_two_steps(api, conn, deps, user):
    body = {"identifier": user["email"], "password": user["password"], "device_label": "Pixel 8"}
    first = api.post("/v1/auth/login", json={**body, "device_fingerprint": "first-device-1"})
    assert first.status_code == 200 and "access_token" in first.json()
    second = api.post("/v1/auth/login", json={**body, "device_fingerprint": "second-device-2"})
    assert second.status_code == 202 and second.json()["otp_required"]
    assert "access_token" not in second.json()
    done = api.post("/v1/auth/login/verify", json={"challenge_id": second.json()["challenge_id"],
                                                    "code": code_from_inbox(deps, user)})
    assert done.status_code == 200 and done.json()["new_device"] is True


def test_http_pins_and_unlock(api, deps, user):
    tokens = api.post("/v1/auth/login", json={"identifier": user["email"], "password": user["password"],
                                              "device_fingerprint": "first-device-1"}).json()
    me = {"X-Actor": f"customer:{user['customer_id']}", "X-Session-Id": sid_of(tokens)}
    assert api.get("/v1/auth/pins", headers=me).json()["PAYMENT"]["set"] is False
    weak = api.put("/v1/auth/pins/payment", json={"pin": "123456", "password": user["password"]}, headers=me)
    assert weak.status_code == 400
    assert api.put("/v1/auth/pins/payment", json={"pin": "583921", "password": user["password"]},
                   headers=me).status_code == 200
    check = api.post("/internal/v1/pins/verify", json={"customer_id": str(user["customer_id"]), "kind": "PAYMENT",
                                                        "pin": "583921"})
    assert check.json() == {"result": "OK"}
    assert api.post("/v1/auth/lock", headers=me).json() == {"state": "LOCKED"}
    assert api.post(f"/internal/v1/sessions/{sid_of(tokens)}/check").json()["state"] == "LOCKED"
    wrong = api.post("/v1/auth/unlock", json={"password": "nope nope"}, headers=me)
    assert wrong.status_code == 403 and wrong.json()["error"] == "WRONG_SECRET"
    assert api.post("/v1/auth/unlock", json={"pin": "4826"}, headers=me).status_code == 428   # no app PIN yet
    assert api.post("/v1/auth/unlock", json={"password": user["password"]}, headers=me).status_code == 200
    assert api.post(f"/internal/v1/sessions/{sid_of(tokens)}/check").json()["state"] == "ACTIVE"
    sessions = api.get("/v1/auth/sessions", headers=me).json()
    assert [s["current"] for s in sessions] == [True]


def test_http_forgot_password_is_the_same_for_everyone(api, user):
    known = api.post("/v1/auth/password/forgot", json={"identifier": user["email"]})
    unknown = api.post("/v1/auth/password/forgot", json={"identifier": "ghost@example.com"})
    assert known.status_code == unknown.status_code == 202
    assert known.json() == unknown.json()


# ------------------------------------------------------------------ notification switches (admin)


def test_notification_settings_are_admin_only_and_logged(api, deps):
    admin = {"X-Actor": "staff:admin:ADMIN"}
    assert api.get("/v1/staff/settings/notifications", headers={"X-Actor": "staff:asha:ANALYST"}).status_code == 403
    assert api.get("/v1/staff/settings/notifications", headers={"X-Actor": f"customer:{uuid.uuid4()}"}).status_code == 403
    now = api.get("/v1/staff/settings/notifications", headers=admin).json()
    assert (now["email_enabled"], now["rcs_enabled"], now["email_provider"]) == (True, True, "auto")
    assert api.patch("/v1/staff/settings/notifications", headers=admin, json={"email_provider": "pigeon"}).status_code == 422
    changed = api.patch("/v1/staff/settings/notifications", headers=admin,
                        json={"rcs_enabled": False, "email_provider": "brevo"}).json()
    assert (changed["email_enabled"], changed["rcs_enabled"], changed["email_provider"]) == (True, False, "brevo")
    assert deps.docstore.get("settings", "notifications")["updated_by"] == "staff:admin:ADMIN"
    assert deps.docstore.query("settings_log", {"setting": "notifications"})


def test_gmail_pause_is_shown_and_can_be_lifted(api, deps):
    admin = {"X-Actor": "staff:admin:ADMIN"}
    deps.docstore.set("settings", "notifications_state", {"gmail_paused_until": deps.clock() + 3600,
                                                          "gmail_paused_reason": "550 5.4.5 limit"})
    assert api.get("/v1/staff/settings/notifications", headers=admin).json()["gmail_paused_until"]
    assert api.post("/v1/staff/settings/notifications/resume-gmail", headers=admin).json()["gmail_paused_until"] is None
