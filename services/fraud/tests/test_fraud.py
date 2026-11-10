import uuid

import pytest

from app import service

ANALYST, SUPERVISOR = "staff:asha:ANALYST", "staff:vikram:ANALYST"


def ids(n=1):
    out = [str(uuid.uuid4()) for _ in range(n)]
    return out[0] if n == 1 else out


def txn_event(status, *, code=None, amount=2_000_000, score=70, reasons=("NEW_DEVICE", "NEW_PAYEE", "ATO_PATTERN"),
              **over):
    e = {"event_id": ids(), "event_type": f"Transaction{status.title()}", "txn_id": ids(), "customer_id": ids(),
         "from_account_id": ids(), "to_account_id": ids(), "amount": amount, "status": status,
         "decline_code": code, "risk_score": score, "risk_reasons": list(reasons), "device_id": "device-9"}
    e.update(over)
    return e


def handle(conn, deps, topic, event):
    return service.handle_event(conn, deps, topic, event)


def inbox(deps, customer):
    return service.list_notifications(deps, uuid.UUID(customer))


def held_payment(conn, deps, **over):
    e = txn_event("HELD", **over)
    deps.intake.add(txn_id=e["txn_id"], customer_id=e["customer_id"], from_account_id=e["from_account_id"],
                    to_account_id=e["to_account_id"], amount=e["amount"], status="HELD",
                    risk_reasons=e["risk_reasons"], device_id=e["device_id"])
    handle(conn, deps, "txn-events", e)
    case = next(c for c in service.list_cases(conn) if str(c["txn_id"]) == e["txn_id"])
    return e, case


# ------------------------------------------------------------------ held payments


def test_held_payment_opens_case_alerts_and_asks_the_customer(conn, deps):
    e, case = held_payment(conn, deps)
    assert (case["type"], case["severity"], case["status"]) == ("HELD_PAYMENT", "MEDIUM", "OPEN")
    assert case["case_number"].startswith("CASE-") and "Rs 20,000.00" in case["summary"]
    alert = service.list_alerts(deps)[0]
    assert alert["type"] == "HELD_PAYMENT" and alert["case_id"] == str(case["case_id"])
    note = inbox(deps, e["customer_id"])[0]
    assert note["title"] == "Was this you?" and note["data"] == {"txn_id": e["txn_id"], "action": "CONFIRM_OR_DENY"}


def test_events_are_handled_once(conn, deps):
    e, _ = held_payment(conn, deps)
    assert handle(conn, deps, "txn-events", e) is False            # redelivery
    assert len(inbox(deps, e["customer_id"])) == 1
    assert len([c for c in service.list_cases(conn) if str(c["txn_id"]) == e["txn_id"]]) == 1


def test_crash_before_marking_processed_does_not_duplicate(conn, deps):
    """Same event content delivered under a new event id, as after a consumer crash."""
    e, case = held_payment(conn, deps)
    conn.execute("DELETE FROM fraud.processed_events WHERE event_id = %s", (e["event_id"],))
    assert handle(conn, deps, "txn-events", e) is True
    assert len(inbox(deps, e["customer_id"])) == 1                  # deterministic notification id
    assert len([c for c in service.list_cases(conn) if str(c["txn_id"]) == e["txn_id"]]) == 1


def test_high_score_is_high_severity(conn, deps):
    _, case = held_payment(conn, deps, score=80)
    assert case["severity"] == "HIGH"


# ------------------------------------------------------------------ analyst decisions


def test_analyst_confirms_fraud(conn, deps):
    e, case = held_payment(conn, deps)
    done = service.resolve_case(conn, deps, case["case_id"], resolution="CONFIRMED_FRAUD",
                                note="customer confirmed by phone", actor=ANALYST)
    assert (done["status"], done["resolution"], done["resolved_by"]) == ("RESOLVED", "CONFIRMED_FRAUD", ANALYST)
    assert deps.intake.calls == [("cancel", e["txn_id"], ANALYST)]
    assert deps.accounts.frozen[0][0] == e["from_account_id"]
    assert f"deny:acct:{e['to_account_id']}" in deps.signals.keys and "deny:dev:device-9" in deps.signals.keys
    assert [t["event_type"] for t in done["timeline"]] == ["OPENED", "RESOLVED"]
    assert inbox(deps, e["customer_id"])[0]["title"] == "Fraud confirmed on your account"
    with pytest.raises(service.Conflict):
        service.resolve_case(conn, deps, case["case_id"], resolution="FALSE_POSITIVE", note="oops", actor=ANALYST)


def test_analyst_clears_false_positive(conn, deps):
    e, case = held_payment(conn, deps)
    service.resolve_case(conn, deps, case["case_id"], resolution="FALSE_POSITIVE", note="salary transfer",
                         actor=ANALYST)
    assert deps.intake.calls == [("release", e["txn_id"], ANALYST)]
    assert deps.accounts.frozen == [] and deps.signals.keys == {}
    assert inbox(deps, e["customer_id"])[0]["title"] == "Your payment has been released"


def test_customer_denial_escalates_the_case(conn, deps):
    e, case = held_payment(conn, deps)
    handle(conn, deps, "txn-events", {**e, "event_id": ids(), "status": "DECLINED", "decline_code": "CUSTOMER_DENIED"})
    updated = service.get_case(conn, deps, case["case_id"])
    assert (updated["type"], updated["severity"]) == ("ACCOUNT_TAKEOVER", "HIGH")
    assert [t["event_type"] for t in updated["timeline"]] == ["OPENED", "CUSTOMER_DENIED"]
    assert inbox(deps, e["customer_id"])[0]["title"] == "Your account is frozen"


# ------------------------------------------------------------------ other payment outcomes


def test_blocked_payment_alerts_and_notifies(conn, deps):
    e = txn_event("DECLINED", code="RISK_BLOCKED", score=100, reasons=["DENY_LIST"])
    handle(conn, deps, "txn-events", e)
    assert service.list_alerts(deps)[0]["type"] == "BLOCKED_PAYMENT"
    assert inbox(deps, e["customer_id"])[0]["title"] == "We blocked a payment"


def test_ordinary_outcomes_go_to_the_inbox_only(conn, deps):
    done, declined, throttled = (txn_event("COMPLETED", score=0, reasons=[]),
                                 txn_event("DECLINED", code="INSUFFICIENT_FUNDS", score=0, reasons=[]),
                                 txn_event("DECLINED", code="RATE_LIMITED", score=0, reasons=[]))
    for e in (done, declined, throttled):
        handle(conn, deps, "txn-events", e)
    assert inbox(deps, done["customer_id"])[0]["title"] == "Rs 20,000.00 sent"
    assert "insufficient funds" in inbox(deps, declined["customer_id"])[0]["body"]
    assert inbox(deps, throttled["customer_id"]) == []              # don't spam a throttled bot
    assert service.list_alerts(deps) == [] 


# ------------------------------------------------------------------ signals from stream analytics


def signal(signal_type, account, value=6):
    return {"event_id": ids(), "signal_type": signal_type, "account_id": account, "value": value,
            "window_end": "2026-11-10 12:01:00"}


def test_single_signal_alerts_and_feeds_the_risk_engine(conn, deps):
    account = ids()
    handle(conn, deps, "risk-signals", signal("VELOCITY_BURST", account))
    alert = service.list_alerts(deps)[0]
    assert (alert["type"], alert["severity"]) == ("VELOCITY_BURST", "MEDIUM")
    assert f"sig:acct:{account}" in deps.signals.keys
    assert deps.accounts.frozen == []


def test_repeated_signal_from_sliding_windows_is_one_alert(conn, deps):
    account = ids()
    for _ in range(5):
        handle(conn, deps, "risk-signals", signal("MICRO_PROBING", account))
    assert len(service.list_alerts(deps)) == 1


def test_two_different_signals_freeze_the_account(conn, deps):
    account, holder = ids(2)
    deps.accounts.holders[account] = holder
    handle(conn, deps, "risk-signals", signal("VELOCITY_BURST", account))
    handle(conn, deps, "risk-signals", signal("STRUCTURING", account))
    assert deps.accounts.frozen == [(account, "automatic: STRUCTURING, VELOCITY_BURST", "system:fraud-alert")]
    case = next(c for c in service.list_cases(conn) if str(c["account_id"]) == account)
    assert (case["type"], case["severity"], case["reasons"]) == ("PATTERN", "HIGH", ["STRUCTURING", "VELOCITY_BURST"])
    assert inbox(deps, holder)[0]["title"] == "Your account is frozen"
    assert service.list_alerts(deps)[0]["title"].endswith("account frozen")


def test_mule_fan_in_acts_alone_and_flags_the_payee(conn, deps):
    account = ids()
    handle(conn, deps, "risk-signals", signal("MULE_FAN_IN", account, value=7))
    assert deps.accounts.frozen[0][0] == account
    assert f"sig:mule:{account}" in deps.signals.keys             # paying into it now scores as MULE_PAYEE


# ------------------------------------------------------------------ logins and analytics


def test_auth_events(conn, deps):
    customer = ids()
    handle(conn, deps, "auth-events", {"event_id": ids(), "event_type": "LoginSucceeded", "customer_id": customer,
                                       "impossible_travel": True, "ip": "198.51.100.9"})
    handle(conn, deps, "auth-events", {"event_id": ids(), "event_type": "LoginSucceeded", "customer_id": customer,
                                       "impossible_travel": False})
    handle(conn, deps, "auth-events", {"event_id": ids(), "event_type": "LoginLocked", "customer_id": customer})
    assert sorted(a["type"] for a in service.list_alerts(deps)) == ["IMPOSSIBLE_TRAVEL", "LOGIN_LOCKED"]
    assert inbox(deps, customer)[0]["title"] == "Unusual sign-in location"


def test_analytics_snapshot(conn, deps):
    for minute in (1, 2, 3):
        handle(conn, deps, "analytics-results", {
            "window_start": f"2026-11-10 12:0{minute}:00", "window_end": f"2026-11-10 12:0{minute + 1}:00",
            "total": 100 * minute, "completed": 90 * minute, "declined": 8 * minute, "rate_limited": 2 * minute,
            "unique_customers": 40})
    view = service.analytics(conn, deps)
    assert [w["total"] for w in view["windows"]] == [300, 200, 100]   # newest first
    assert "cases" in view and view["pending_approvals"] >= 0


# ------------------------------------------------------------------ customer reports


def test_customer_reports_a_payment(conn, deps):
    customer, txn = uuid.uuid4(), uuid.uuid4()
    deps.intake.add(txn_id=str(txn), customer_id=str(customer), from_account_id=ids(), amount=450_000,
                    status="COMPLETED", risk_reasons=[])
    case = service.report_fraud(conn, deps, customer, txn, "I did not make this payment")
    assert case["type"] == "CUSTOMER_REPORT" and "Rs 4,500.00" in case["summary"]
    assert [c["case_number"] for c in service.my_cases(conn, customer)] == [case["case_number"]]
    with pytest.raises(service.NotFound):
        service.report_fraud(conn, deps, uuid.uuid4(), txn, "not my payment at all")   # someone else's


def test_notifications_belong_to_their_customer(conn, deps):
    e, _ = held_payment(conn, deps)
    note = inbox(deps, e["customer_id"])[0]
    with pytest.raises(service.NotFound):
        service.mark_read(deps, uuid.uuid4(), note["id"])
    service.mark_read(deps, uuid.UUID(e["customer_id"]), note["id"])
    assert inbox(deps, e["customer_id"])[0]["read"] is True


# ------------------------------------------------------------------ maker-checker


def test_unfreeze_needs_a_second_person(conn, deps):
    account = uuid.uuid4()
    request = service.request_approval(conn, action="UNFREEZE_ACCOUNT", target_id=account,
                                       reason="identity re-verified at branch", actor=ANALYST)
    with pytest.raises(service.Conflict):
        service.request_approval(conn, action="UNFREEZE_ACCOUNT", target_id=account,
                                 reason="duplicate request", actor=SUPERVISOR)
    with pytest.raises(service.Forbidden):
        service.decide_approval(conn, deps, request["approval_id"], approve=True, actor=ANALYST)   # own request
    assert deps.accounts.unfrozen == []
    done = service.decide_approval(conn, deps, request["approval_id"], approve=True, actor=SUPERVISOR)
    assert (done["status"], done["decided_by"]) == ("APPROVED", SUPERVISOR)
    assert deps.accounts.unfrozen == [(str(account), SUPERVISOR)]
    with pytest.raises(service.Conflict):
        service.decide_approval(conn, deps, request["approval_id"], approve=False, actor=SUPERVISOR)


def test_rejected_request_does_nothing(conn, deps):
    request = service.request_approval(conn, action="UNFREEZE_ACCOUNT", target_id=uuid.uuid4(),
                                       reason="customer asked nicely", actor=ANALYST)
    assert service.decide_approval(conn, deps, request["approval_id"], approve=False,
                                   actor=SUPERVISOR)["status"] == "REJECTED"
    assert deps.accounts.unfrozen == []
