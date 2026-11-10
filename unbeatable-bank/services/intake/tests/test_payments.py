import dataclasses
import uuid
from concurrent.futures import ThreadPoolExecutor

import pytest

from app import service
from app.clients import DependencyUnavailable, RiskDecision

RS = 100  # paise per rupee


def pay(conn, deps, customer, from_acct, payee, amount, key=None, **kw):
    return service.submit_payment(
        conn, deps, customer_id=customer, idempotency_key=key or str(uuid.uuid4()),
        from_account_id=from_acct, payee=payee, amount=amount, **kw,
    )


@pytest.fixture
def riya(accounts):
    """Riya with a funded savings account, and Arjun whose account is a trusted payee of hers."""
    cid = accounts.customer()
    acct, _ = accounts.account([cid], balance=100_000 * RS)
    arjun = accounts.customer()
    arjun_acct, arjun_no = accounts.account([arjun], name="Arjun Mehta")
    accounts.trust(cid, arjun_no)
    return {"cid": cid, "acct": acct, "payee_acct": arjun_acct, "payee_no": arjun_no}


def send(conn, deps, riya, rupees, **kw):
    return pay(conn, deps, riya["cid"], riya["acct"], riya["payee_no"], int(rupees * RS), **kw)


# ------------------------------------------------------------------ happy path


def test_payment_completes(conn, deps, balance, riya):
    txn, replayed = send(conn, deps, riya, 2_500, note="dinner", device_id="phone-1", ip="10.0.0.9")
    assert txn["status"] == "COMPLETED" and txn["journal_id"] and not replayed
    assert txn["to_display_name"] == "Arjun M****" and txn["risk_decision"] == "ALLOW"
    assert balance(riya["acct"]) == 97_500 * RS and balance(riya["payee_acct"]) == 2_500 * RS
    # every layer was consulted, with the right identifiers
    assert deps.limiter.calls[0]["device_id"] == "phone-1" and deps.limiter.calls[0]["ip"] == "10.0.0.9"
    assert deps.risk.calls[0]["payee_trusted"] is True and deps.risk.calls[0]["amount"] == 2_500 * RS


def test_pay_by_alias(conn, deps, accounts, riya):
    accounts.aliases["arjun@unbeatable"] = riya["payee_no"]
    txn, _ = pay(conn, deps, riya["cid"], riya["acct"], "Arjun@Unbeatable", 100 * RS, channel="QR_PAYMENT")
    assert txn["status"] == "COMPLETED" and txn["channel"] == "QR_PAYMENT"


# ------------------------------------------------------------------ idempotency


def test_retry_storm_one_payment(pool, deps, balance, riya):
    key = str(uuid.uuid4())

    def attempt(_):
        with pool.connection() as c:
            try:
                return send(c, deps, riya, 1_000, key=key)
            except service.Pending:
                return None

    with ThreadPoolExecutor(max_workers=30) as ex:
        results = [r for r in ex.map(attempt, range(30)) if r]

    assert {r[0]["txn_id"] for r in results} == {results[0][0]["txn_id"]}
    assert all(r[0]["status"] == "COMPLETED" for r in results)
    assert balance(riya["acct"]) == 99_000 * RS  # charged exactly once


def test_same_key_different_payment_rejected(conn, deps, riya):
    key = str(uuid.uuid4())
    send(conn, deps, riya, 100, key=key)
    with pytest.raises(service.IdempotencyConflict):
        send(conn, deps, riya, 999, key=key)


def test_replay_returns_original_decision(conn, deps, riya):
    key = str(uuid.uuid4())
    first, _ = send(conn, deps, riya, 500_000, key=key)
    again, replayed = send(conn, deps, riya, 500_000, key=key)
    assert replayed and again["txn_id"] == first["txn_id"] and again["status"] == "DECLINED"


def test_dependency_down_then_retry_succeeds_once(conn, deps, accounts, balance, riya):
    key = str(uuid.uuid4())
    accounts.down = True
    with pytest.raises(service.Pending):
        send(conn, deps, riya, 700, key=key)
    assert balance(riya["acct"]) == 100_000 * RS
    accounts.down = False
    txn, _ = send(conn, deps, riya, 700, key=key)
    assert txn["status"] == "COMPLETED" and balance(riya["acct"]) == 99_300 * RS


class FlakyLedger:
    """Posts the money, then 'loses' the response, like a network timeout."""

    def __init__(self, real):
        self.real, self.fail_next = real, True

    def post_journal(self, **kw):
        journal_id = self.real.post_journal(**kw)
        if self.fail_next:
            self.fail_next = False
            raise DependencyUnavailable("timeout after posting")
        return journal_id

    def __getattr__(self, name):
        return getattr(self.real, name)


def test_lost_ledger_response_does_not_double_charge(conn, deps, balance, riya):
    flaky = dataclasses.replace(deps, ledger=FlakyLedger(deps.ledger))
    key = str(uuid.uuid4())
    with pytest.raises(service.Pending):
        send(conn, flaky, riya, 1_234, key=key)
    assert balance(riya["acct"]) == 98_766 * RS  # money did move...
    txn, _ = send(conn, flaky, riya, 1_234, key=key)
    assert txn["status"] == "COMPLETED"
    assert balance(riya["acct"]) == 98_766 * RS  # ...but only once
    assert len(deps.risk.calls) == 1             # the retry did not re-score the payment


# ------------------------------------------------------------------ declines


@pytest.mark.parametrize("setup, code", [
    (lambda a, r: a.accounts[str(r["acct"])].update(status="FROZEN"), "ACCOUNT_FROZEN"),
    (lambda a, r: a.customers[str(r["cid"])].update(status="FROZEN"), "CUSTOMER_FROZEN"),
    (lambda a, r: a.holders[str(r["acct"])].clear(), "NOT_A_HOLDER"),
])
def test_authorisation_declines(conn, deps, accounts, balance, riya, setup, code):
    setup(accounts, riya)
    txn, _ = send(conn, deps, riya, 100)
    assert txn["status"] == "DECLINED" and txn["decline_code"] == code and txn["decline_message"]
    assert balance(riya["acct"]) == 100_000 * RS


def test_payee_declines(conn, deps, accounts, riya):
    typo = riya["payee_no"][:-1] + str((int(riya["payee_no"][-1]) + 1) % 10)
    cases = {
        typo: "INVALID_PAYEE",
        "ghost@unbeatable": "PAYEE_NOT_FOUND",
        accounts.accounts[str(riya["acct"])]["account_number"]: "SAME_ACCOUNT",
    }
    for payee, code in cases.items():
        txn, _ = pay(conn, deps, riya["cid"], riya["acct"], payee, 100 * RS)
        assert txn["decline_code"] == code, payee


def test_insufficient_funds_from_ledger(conn, deps, accounts, riya):
    poor_acct, _ = accounts.account([riya["cid"]], balance=50 * RS)
    txn, _ = pay(conn, deps, riya["cid"], poor_acct, riya["payee_no"], 51 * RS)
    assert txn["decline_code"] == "INSUFFICIENT_FUNDS"


def test_per_transaction_limit(conn, deps, riya):
    assert send(conn, deps, riya, 50_001)[0]["decline_code"] == "PER_TXN_LIMIT_EXCEEDED"


def test_new_payee_limit(conn, deps, accounts, riya):
    stranger = accounts.customer()
    _, stranger_no = accounts.account([stranger], name="Unknown Person")
    _, own_no = accounts.account([riya["cid"]])
    big = 15_000 * RS

    def to(number, amount):
        return pay(conn, deps, riya["cid"], riya["acct"], number, amount)[0]

    assert to(stranger_no, big)["decline_code"] == "NEW_PAYEE_LIMIT_EXCEEDED"
    assert to(stranger_no, 10_000 * RS)["status"] == "COMPLETED"   # small amounts to new payees are fine
    assert to(own_no, big)["status"] == "COMPLETED"                # own accounts are always trusted
    accounts.trust(riya["cid"], stranger_no)                       # saved and past the cooling period
    assert to(stranger_no, big)["status"] == "COMPLETED"


# ------------------------------------------------------------------ daily limits


def test_daily_limit_and_declines_dont_count(conn, deps, accounts, riya):
    acct, _ = accounts.account([riya["cid"]], balance=200_000 * RS, daily_limit=100_000 * RS,
                               per_txn_limit=50_000 * RS)

    def p(amount):
        return pay(conn, deps, riya["cid"], acct, riya["payee_no"], amount)[0]

    assert p(50_000 * RS)["status"] == "COMPLETED"
    assert p(60_000 * RS)["decline_code"] == "PER_TXN_LIMIT_EXCEEDED"  # declined: doesn't use up limit
    assert p(50_000 * RS)["status"] == "COMPLETED"
    assert p(1 * RS)["decline_code"] == "DAILY_LIMIT_EXCEEDED"


def test_customer_daily_limit_across_accounts(conn, deps, accounts, riya):
    cid = accounts.customer(daily_transfer_limit=30_000 * RS)
    a1, _ = accounts.account([cid], balance=100_000 * RS)
    a2, _ = accounts.account([cid], balance=100_000 * RS)
    accounts.trust(cid, riya["payee_no"])
    assert pay(conn, deps, cid, a1, riya["payee_no"], 20_000 * RS)[0]["status"] == "COMPLETED"
    assert pay(conn, deps, cid, a2, riya["payee_no"], 15_000 * RS)[0]["decline_code"] \
        == "CUSTOMER_DAILY_LIMIT_EXCEEDED"


def test_concurrent_payments_cannot_beat_daily_limit(pool, deps, accounts, balance, riya):
    """Ten concurrent Rs 30,000 payments against a Rs 1,00,000 daily limit -> exactly three succeed."""
    acct, _ = accounts.account([riya["cid"]], balance=1_000_000 * RS, daily_limit=100_000 * RS,
                               per_txn_limit=50_000 * RS)

    def attempt(_):
        with pool.connection() as c:
            return pay(c, deps, riya["cid"], acct, riya["payee_no"], 30_000 * RS)[0]["status"]

    with ThreadPoolExecutor(max_workers=10) as ex:
        statuses = list(ex.map(attempt, range(10)))
    assert statuses.count("COMPLETED") == 3
    assert balance(acct) == 910_000 * RS


def test_joint_holders_share_account_limit(conn, deps, accounts, riya):
    a, b = accounts.customer(), accounts.customer()
    joint, _ = accounts.account([a, b], balance=500_000 * RS, daily_limit=60_000 * RS)
    for c in (a, b):
        accounts.trust(c, riya["payee_no"])
    assert pay(conn, deps, a, joint, riya["payee_no"], 40_000 * RS)[0]["status"] == "COMPLETED"
    assert pay(conn, deps, b, joint, riya["payee_no"], 30_000 * RS)[0]["decline_code"] \
        == "DAILY_LIMIT_EXCEEDED"


# ------------------------------------------------------------------ rate limiting and risk


def test_rate_limited_payment_is_declined_before_anything_else(conn, deps, balance, riya):
    deps.limiter.deny = True
    txn, _ = send(conn, deps, riya, 100)
    assert (txn["status"], txn["decline_code"]) == ("DECLINED", "RATE_LIMITED")
    assert "wait a minute" in txn["decline_message"]
    assert deps.risk.calls == [] and balance(riya["acct"]) == 100_000 * RS


def test_risk_block(conn, deps, balance, riya):
    deps.risk.next = RiskDecision("BLOCK", 100, ["DENY_LIST"])
    txn, _ = send(conn, deps, riya, 100)
    assert (txn["status"], txn["decline_code"], txn["risk_score"]) == ("DECLINED", "RISK_BLOCKED", 100)
    assert txn["risk_reasons"] == ["DENY_LIST"] and balance(riya["acct"]) == 100_000 * RS


def test_challenge_then_otp_completes(conn, deps, balance, riya):
    deps.risk.next = RiskDecision("CHALLENGE", 35, ["AMOUNT_ANOMALY"])
    key = str(uuid.uuid4())
    txn, _ = send(conn, deps, riya, 30_000, key=key)
    assert txn["status"] == "CHALLENGE" and txn["next_action"] == "OTP_REQUIRED"
    assert balance(riya["acct"]) == 100_000 * RS                      # nothing moved yet
    assert deps.identity.issued == [(str(riya["cid"]), f"txn:{txn['txn_id']}",
                                     "Confirm payment of Rs 30,000.00 to Arjun M****")]
    assert send(conn, deps, riya, 30_000, key=key) == (txn, True)      # a retry doesn't skip the OTP

    with pytest.raises(service.InvalidOtp):
        service.confirm_challenge(conn, deps, txn["txn_id"], riya["cid"], "000000")
    done = service.confirm_challenge(conn, deps, txn["txn_id"], riya["cid"], deps.identity.CODE)
    assert done["status"] == "COMPLETED" and balance(riya["acct"]) == 70_000 * RS
    again = service.confirm_challenge(conn, deps, txn["txn_id"], riya["cid"], deps.identity.CODE)
    assert again["status"] == "COMPLETED" and balance(riya["acct"]) == 70_000 * RS   # no double post


def test_challenge_fails_after_too_many_wrong_codes(conn, deps, balance, riya):
    deps.risk.next = RiskDecision("CHALLENGE", 35, ["VELOCITY_1M"])
    txn, _ = send(conn, deps, riya, 500)
    for _ in range(2):
        with pytest.raises(service.InvalidOtp):
            service.confirm_challenge(conn, deps, txn["txn_id"], riya["cid"], "111111")
    final = service.confirm_challenge(conn, deps, txn["txn_id"], riya["cid"], "111111")
    assert (final["status"], final["decline_code"]) == ("DECLINED", "CHALLENGE_FAILED")
    assert balance(riya["acct"]) == 100_000 * RS


def test_someone_elses_challenge_cannot_be_confirmed(conn, deps, accounts, riya):
    deps.risk.next = RiskDecision("CHALLENGE", 35, ["VELOCITY_1M"])
    txn, _ = send(conn, deps, riya, 500)
    with pytest.raises(service.NotFound):
        service.confirm_challenge(conn, deps, txn["txn_id"], accounts.customer(), deps.identity.CODE)


def held(conn, deps, riya, rupees=20_000):
    deps.risk.next = RiskDecision("HOLD", 70, ["NEW_DEVICE", "NEW_PAYEE", "ATO_PATTERN"])
    return send(conn, deps, riya, rupees)[0]


def test_hold_reserves_funds_until_an_analyst_approves(conn, deps, balance, riya):
    txn = held(conn, deps, riya)
    assert txn["status"] == "HELD" and txn["next_action"] == "UNDER_REVIEW" and txn["hold_id"]
    assert balance(riya["acct"]) == 100_000 * RS                       # not debited...
    assert balance(riya["acct"], available=True) == 80_000 * RS        # ...but can't be spent elsewhere
    assert balance(riya["payee_acct"]) == 0

    done = service.release_held(conn, deps, txn["txn_id"], "staff:asha:ANALYST")
    assert done["status"] == "COMPLETED" and done["review"] == "APPROVED"
    assert balance(riya["acct"]) == 80_000 * RS and balance(riya["acct"], available=True) == 80_000 * RS
    assert balance(riya["payee_acct"]) == 20_000 * RS
    assert service.release_held(conn, deps, txn["txn_id"], "staff:asha:ANALYST")["status"] == "COMPLETED"


def test_analyst_rejects_held_payment_and_funds_come_back(conn, deps, balance, riya):
    txn = held(conn, deps, riya)
    done = service.cancel_held(conn, deps, txn["txn_id"], "staff:asha:ANALYST")
    assert (done["status"], done["decline_code"], done["review"]) == ("DECLINED", "FRAUD_CONFIRMED", "REJECTED")
    assert balance(riya["acct"], available=True) == 100_000 * RS
    with pytest.raises(service.WrongState):
        service.release_held(conn, deps, txn["txn_id"], "staff:asha:ANALYST")


def test_customer_says_not_me_cancels_and_freezes(conn, deps, accounts, balance, riya):
    txn = held(conn, deps, riya)
    done = service.deny_payment(conn, deps, txn["txn_id"], riya["cid"])
    assert (done["status"], done["decline_code"]) == ("DECLINED", "CUSTOMER_DENIED")
    assert balance(riya["acct"], available=True) == 100_000 * RS
    assert accounts.frozen == [(str(riya["acct"]), "customer reported an unauthorised payment",
                                f"customer:{riya['cid']}")]
    assert send(conn, deps, riya, 10)[0]["decline_code"] == "ACCOUNT_FROZEN"


def test_held_payment_counts_towards_daily_limit(conn, deps, accounts, riya):
    acct, _ = accounts.account([riya["cid"]], balance=500_000 * RS, daily_limit=50_000 * RS)
    deps.risk.next = RiskDecision("HOLD", 70, ["ATO_PATTERN"])
    assert pay(conn, deps, riya["cid"], acct, riya["payee_no"], 40_000 * RS)[0]["status"] == "HELD"
    assert pay(conn, deps, riya["cid"], acct, riya["payee_no"], 20_000 * RS)[0]["decline_code"] \
        == "DAILY_LIMIT_EXCEEDED"


def test_account_frozen_during_review_declines_on_approval(conn, deps, ledger_engine, balance, riya):
    txn = held(conn, deps, riya)
    ledger_engine[0].update_account(conn, riya["acct"], status="DEBIT_BLOCKED")
    done = service.release_held(conn, deps, txn["txn_id"], "staff:asha:ANALYST")
    assert (done["status"], done["decline_code"]) == ("DECLINED", "ACCOUNT_NOT_OPEN")
    assert balance(riya["acct"], available=True) == 100_000 * RS       # hold released, nothing lost


def test_staff_queue_of_held_payments(conn, deps, riya):
    txn = held(conn, deps, riya)
    assert txn["txn_id"] in {t["txn_id"] for t in service.list_waiting(conn, "HELD")}


# ------------------------------------------------------------------ cash, events, history


def test_cash_deposit_is_idempotent(deps, accounts, balance, riya):
    number = accounts.accounts[str(riya["acct"])]["account_number"]
    key = str(uuid.uuid4())
    first = service.cash_deposit(deps, account_number=number, amount=5_000 * RS, idempotency_key=key,
                                 actor="staff:teller-1:TELLER")
    again = service.cash_deposit(deps, account_number=number, amount=5_000 * RS, idempotency_key=key,
                                 actor="staff:teller-1:TELLER")
    assert first["journal_id"] == again["journal_id"] and balance(riya["acct"]) == 105_000 * RS
    with pytest.raises(service.NotFound):
        service.cash_deposit(deps, account_number="500199999999", amount=1, idempotency_key="k", actor="x")


def test_every_decision_emits_one_event(conn, deps, riya):
    key = str(uuid.uuid4())
    for _ in range(3):  # retries must not duplicate the event
        txn, _ = send(conn, deps, riya, 10, key=key)
    declined, _ = send(conn, deps, riya, 60_000)
    held_txn = held(conn, deps, riya)
    service.release_held(conn, deps, held_txn["txn_id"], "staff:asha:ANALYST")
    rows = conn.execute(
        "SELECT event_type, payload->>'status', payload->>'decline_code' FROM intake.outbox "
        "WHERE payload->>'txn_id' = ANY(%s) ORDER BY created_at",
        ([str(txn["txn_id"]), str(declined["txn_id"]), str(held_txn["txn_id"])],),
    ).fetchall()
    assert rows == [("TransactionCompleted", "COMPLETED", None),
                    ("TransactionDeclined", "DECLINED", "PER_TXN_LIMIT_EXCEEDED"),
                    ("TransactionHeld", "HELD", None),
                    ("TransactionCompleted", "COMPLETED", None)]
    payload = conn.execute("SELECT payload FROM intake.outbox WHERE payload->>'txn_id' = %s "
                           "AND event_type = 'TransactionHeld'", (str(held_txn["txn_id"]),)).fetchone()[0]
    assert payload["risk_reasons"] == ["NEW_DEVICE", "NEW_PAYEE", "ATO_PATTERN"] and payload["event_time_ms"] > 0


def test_history_is_per_customer(conn, deps, riya):
    txn, _ = send(conn, deps, riya, 10)
    assert txn["txn_id"] in {t["txn_id"] for t in service.list_transactions(conn, riya["cid"])}
    with pytest.raises(service.NotFound):
        service.get_transaction(conn, txn["txn_id"], uuid.uuid4())
