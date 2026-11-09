import uuid
from concurrent.futures import ThreadPoolExecutor

import pytest

from app import service
from app.clients import DependencyUnavailable

RS = 100  # paise per rupee


def pay(conn, accounts, ledger, customer, from_acct, payee, amount, key=None, **kw):
    txn, replayed = service.submit_payment(
        conn, accounts, ledger, customer_id=customer, idempotency_key=key or str(uuid.uuid4()),
        from_account_id=from_acct, payee=payee, amount=amount, **kw,
    )
    return txn, replayed


@pytest.fixture
def riya(accounts):
    """Riya with a funded savings account, and Arjun with an account she has saved as a payee."""
    cid = accounts.customer()
    acct, _ = accounts.account([cid], balance=100_000 * RS)
    arjun = accounts.customer()
    arjun_acct, arjun_no = accounts.account([arjun], name="Arjun Mehta")
    accounts.save_payee(cid, arjun_no)
    return {"cid": cid, "acct": acct, "payee_acct": arjun_acct, "payee_no": arjun_no}


# ------------------------------------------------------------------ happy path


def test_payment_completes(conn, accounts, ledger, balance, riya):
    txn, replayed = pay(conn, accounts, ledger, riya["cid"], riya["acct"], riya["payee_no"], 2_500 * RS,
                        note="dinner")
    assert txn["status"] == "COMPLETED" and txn["journal_id"] and not replayed
    assert txn["to_display_name"] == "Arjun M****"
    assert balance(riya["acct"]) == 97_500 * RS and balance(riya["payee_acct"]) == 2_500 * RS


def test_pay_by_alias(conn, accounts, ledger, balance, riya):
    accounts.aliases["arjun@unbeatable"] = riya["payee_no"]
    txn, _ = pay(conn, accounts, ledger, riya["cid"], riya["acct"], "Arjun@Unbeatable", 100 * RS,
                 channel="QR_PAYMENT")
    assert txn["status"] == "COMPLETED" and txn["channel"] == "QR_PAYMENT"


# ------------------------------------------------------------------ idempotency


def test_retry_storm_one_payment(pool, accounts, ledger, balance, riya):
    key = str(uuid.uuid4())

    def attempt(_):
        with pool.connection() as c:
            try:
                return pay(c, accounts, ledger, riya["cid"], riya["acct"], riya["payee_no"], 1_000 * RS, key=key)
            except service.Pending:
                return None

    with ThreadPoolExecutor(max_workers=30) as ex:
        results = [r for r in ex.map(attempt, range(30)) if r]

    assert {r[0]["txn_id"] for r in results} == {results[0][0]["txn_id"]}
    assert all(r[0]["status"] == "COMPLETED" for r in results)
    assert balance(riya["acct"]) == 99_000 * RS  # charged exactly once


def test_same_key_different_payment_rejected(conn, accounts, ledger, riya):
    key = str(uuid.uuid4())
    pay(conn, accounts, ledger, riya["cid"], riya["acct"], riya["payee_no"], 100 * RS, key=key)
    with pytest.raises(service.IdempotencyConflict):
        pay(conn, accounts, ledger, riya["cid"], riya["acct"], riya["payee_no"], 999 * RS, key=key)


def test_replay_returns_original_decision(conn, accounts, ledger, riya):
    key = str(uuid.uuid4())
    first, _ = pay(conn, accounts, ledger, riya["cid"], riya["acct"], riya["payee_no"], 500_000 * RS, key=key)
    again, replayed = pay(conn, accounts, ledger, riya["cid"], riya["acct"], riya["payee_no"], 500_000 * RS, key=key)
    assert replayed and again["txn_id"] == first["txn_id"] and again["status"] == "DECLINED"


def test_dependency_down_then_retry_succeeds_once(conn, accounts, ledger, balance, riya):
    key = str(uuid.uuid4())
    accounts.down = True
    with pytest.raises(service.Pending):
        pay(conn, accounts, ledger, riya["cid"], riya["acct"], riya["payee_no"], 700 * RS, key=key)
    assert balance(riya["acct"]) == 100_000 * RS
    accounts.down = False
    txn, _ = pay(conn, accounts, ledger, riya["cid"], riya["acct"], riya["payee_no"], 700 * RS, key=key)
    assert txn["status"] == "COMPLETED" and balance(riya["acct"]) == 99_300 * RS


class FlakyLedger:
    """Posts the money, then 'loses' the response, like a network timeout."""

    def __init__(self, real):
        self.real, self.fail_next = real, True

    def post_transfer(self, **kw):
        journal_id = self.real.post_transfer(**kw)
        if self.fail_next:
            self.fail_next = False
            raise DependencyUnavailable("timeout after posting")
        return journal_id


def test_lost_ledger_response_does_not_double_charge(conn, accounts, ledger, balance, riya):
    flaky, key = FlakyLedger(ledger), str(uuid.uuid4())
    with pytest.raises(service.Pending):
        pay(conn, accounts, flaky, riya["cid"], riya["acct"], riya["payee_no"], 1_234 * RS, key=key)
    assert balance(riya["acct"]) == 98_766 * RS  # money did move...
    txn, _ = pay(conn, accounts, flaky, riya["cid"], riya["acct"], riya["payee_no"], 1_234 * RS, key=key)
    assert txn["status"] == "COMPLETED"
    assert balance(riya["acct"]) == 98_766 * RS  # ...but only once


# ------------------------------------------------------------------ declines


@pytest.mark.parametrize("setup, code", [
    (lambda a, r: a.accounts[str(r["acct"])].update(status="FROZEN"), "ACCOUNT_FROZEN"),
    (lambda a, r: a.customers[str(r["cid"])].update(status="FROZEN"), "CUSTOMER_FROZEN"),
    (lambda a, r: a.holders[str(r["acct"])].clear(), "NOT_A_HOLDER"),
])
def test_authorisation_declines(conn, accounts, ledger, balance, riya, setup, code):
    setup(accounts, riya)
    txn, _ = pay(conn, accounts, ledger, riya["cid"], riya["acct"], riya["payee_no"], 100 * RS)
    assert txn["status"] == "DECLINED" and txn["decline_code"] == code and txn["decline_message"]
    assert balance(riya["acct"]) == 100_000 * RS


def test_payee_declines(conn, accounts, ledger, riya):
    _, own_no = accounts.account([riya["cid"]])
    typo = riya["payee_no"][:-1] + str((int(riya["payee_no"][-1]) + 1) % 10)
    cases = {
        typo: "INVALID_PAYEE",
        "999999999999"[:11] + "9": "INVALID_PAYEE",
        "ghost@unbeatable": "PAYEE_NOT_FOUND",
        accounts.accounts[str(riya["acct"])]["account_number"]: "SAME_ACCOUNT",
    }
    for payee, code in cases.items():
        txn, _ = pay(conn, accounts, ledger, riya["cid"], riya["acct"], payee, 100 * RS)
        assert txn["decline_code"] == code, payee


def test_insufficient_funds_from_ledger(conn, accounts, ledger, riya):
    poor_acct, _ = accounts.account([riya["cid"]], balance=50 * RS)
    txn, _ = pay(conn, accounts, ledger, riya["cid"], poor_acct, riya["payee_no"], 51 * RS)
    assert txn["decline_code"] == "INSUFFICIENT_FUNDS"


def test_per_transaction_limit(conn, accounts, ledger, riya):
    txn, _ = pay(conn, accounts, ledger, riya["cid"], riya["acct"], riya["payee_no"], 50_001 * RS)
    assert txn["decline_code"] == "PER_TXN_LIMIT_EXCEEDED"


def test_new_payee_limit(conn, accounts, ledger, riya):
    stranger = accounts.customer()
    _, stranger_no = accounts.account([stranger], name="Unknown Person")
    _, own_no = accounts.account([riya["cid"]])
    big = 15_000 * RS
    assert pay(conn, accounts, ledger, riya["cid"], riya["acct"], stranger_no, big)[0]["decline_code"] \
        == "NEW_PAYEE_LIMIT_EXCEEDED"
    assert pay(conn, accounts, ledger, riya["cid"], riya["acct"], stranger_no, 10_000 * RS)[0]["status"] \
        == "COMPLETED"  # small amounts to new payees are fine
    accounts.save_payee(riya["cid"], stranger_no, cooling=True)
    assert pay(conn, accounts, ledger, riya["cid"], riya["acct"], stranger_no, big)[0]["decline_code"] \
        == "NEW_PAYEE_LIMIT_EXCEEDED"  # still cooling
    assert pay(conn, accounts, ledger, riya["cid"], riya["acct"], own_no, big)[0]["status"] \
        == "COMPLETED"  # own accounts are always trusted


# ------------------------------------------------------------------ daily limits


def test_daily_limit_and_declines_dont_count(conn, accounts, ledger, riya):
    acct, _ = accounts.account([riya["cid"]], balance=200_000 * RS, daily_limit=100_000 * RS,
                               per_txn_limit=50_000 * RS)
    p = lambda amt: pay(conn, accounts, ledger, riya["cid"], acct, riya["payee_no"], amt)[0]
    assert p(50_000 * RS)["status"] == "COMPLETED"
    assert p(60_000 * RS)["decline_code"] == "PER_TXN_LIMIT_EXCEEDED"  # declined: doesn't use up limit
    assert p(50_000 * RS)["status"] == "COMPLETED"
    assert p(1 * RS)["decline_code"] == "DAILY_LIMIT_EXCEEDED"


def test_customer_daily_limit_across_accounts(conn, accounts, ledger, riya):
    cid = accounts.customer(daily_transfer_limit=30_000 * RS)
    a1, _ = accounts.account([cid], balance=100_000 * RS)
    a2, _ = accounts.account([cid], balance=100_000 * RS)
    accounts.save_payee(cid, riya["payee_no"])
    assert pay(conn, accounts, ledger, cid, a1, riya["payee_no"], 20_000 * RS)[0]["status"] == "COMPLETED"
    assert pay(conn, accounts, ledger, cid, a2, riya["payee_no"], 15_000 * RS)[0]["decline_code"] \
        == "CUSTOMER_DAILY_LIMIT_EXCEEDED"


def test_concurrent_payments_cannot_beat_daily_limit(pool, accounts, ledger, balance, riya):
    """Ten concurrent Rs 30,000 payments against a Rs 1,00,000 daily limit -> exactly three succeed."""
    acct, _ = accounts.account([riya["cid"]], balance=1_000_000 * RS, daily_limit=100_000 * RS,
                               per_txn_limit=50_000 * RS)

    def attempt(_):
        with pool.connection() as c:
            return pay(c, accounts, ledger, riya["cid"], acct, riya["payee_no"], 30_000 * RS)[0]["status"]

    with ThreadPoolExecutor(max_workers=10) as ex:
        statuses = list(ex.map(attempt, range(10)))
    assert statuses.count("COMPLETED") == 3
    assert balance(acct) == 910_000 * RS


def test_joint_holders_share_account_limit(conn, accounts, ledger, riya):
    a, b = accounts.customer(), accounts.customer()
    joint, _ = accounts.account([a, b], balance=500_000 * RS, daily_limit=60_000 * RS)
    for c in (a, b):
        accounts.save_payee(c, riya["payee_no"])
    assert pay(conn, accounts, ledger, a, joint, riya["payee_no"], 40_000 * RS)[0]["status"] == "COMPLETED"
    assert pay(conn, accounts, ledger, b, joint, riya["payee_no"], 30_000 * RS)[0]["decline_code"] \
        == "DAILY_LIMIT_EXCEEDED"


# ------------------------------------------------------------------ events & history


def test_every_decision_emits_one_event(conn, accounts, ledger, riya):
    key = str(uuid.uuid4())
    for _ in range(3):  # retries must not duplicate the event
        txn, _ = pay(conn, accounts, ledger, riya["cid"], riya["acct"], riya["payee_no"], 10 * RS, key=key)
    declined, _ = pay(conn, accounts, ledger, riya["cid"], riya["acct"], riya["payee_no"], 60_000 * RS)
    rows = conn.execute(
        "SELECT event_type, payload->>'status', payload->>'decline_code' FROM intake.outbox "
        "WHERE payload->>'txn_id' = ANY(%s) ORDER BY created_at",
        ([str(txn["txn_id"]), str(declined["txn_id"])],),
    ).fetchall()
    assert rows == [("TransactionCompleted", "COMPLETED", None),
                    ("TransactionDeclined", "DECLINED", "PER_TXN_LIMIT_EXCEEDED")]


def test_history_is_per_customer(conn, accounts, ledger, riya):
    txn, _ = pay(conn, accounts, ledger, riya["cid"], riya["acct"], riya["payee_no"], 10 * RS)
    assert txn["txn_id"] in {t["txn_id"] for t in service.list_transactions(conn, riya["cid"])}
    with pytest.raises(service.NotFound):
        service.get_transaction(conn, txn["txn_id"], uuid.uuid4())
