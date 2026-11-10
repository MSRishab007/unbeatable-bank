"""Double-money test suite for the posting engine."""

import uuid
from concurrent.futures import ThreadPoolExecutor

import psycopg
import pytest

from app import internal_accounts
from app.posting import (
    AccountNotOpen, HoldError, IdempotencyConflict, InsufficientFunds, InvalidJournal, Leg,
    create_hold, get_balance, list_entries, post_journal, reconcile, release_hold,
    reverse_journal, update_account,
)


def transfer(conn, src, dst, amount, key=None, **kw):
    return post_journal(
        conn, idempotency_key=key or str(uuid.uuid4()), txn_type="TRANSFER",
        legs=[Leg(src, -amount), Leg(dst, amount)], **kw,
    )


def bal(conn, account_id):
    b = get_balance(conn, account_id)
    return b["ledger_balance"], b["available_balance"]


# ------------------------------------------------------------------ basics


def test_transfer_moves_money(conn, make_account):
    a, b = make_account(100_000), make_account()
    result = transfer(conn, a, b, 30_000)
    assert not result.replayed
    assert sum(e["amount"] for e in result.entries) == 0
    assert bal(conn, a) == (70_000, 70_000)
    assert bal(conn, b) == (30_000, 30_000)
    assert [e["amount"] for e in list_entries(conn, a)] == [-30_000, 100_000]


@pytest.mark.parametrize("legs", [
    [(1, 100)],                       # one leg
    [(1, 100), (2, -99)],             # doesn't sum to zero
    [(1, 0), (2, 0)],                 # zero amounts
    [(1, 100), (1, -100)],            # same account twice
])
def test_invalid_journals_rejected(conn, legs):
    ids = {1: uuid.uuid4(), 2: uuid.uuid4()}
    with pytest.raises(InvalidJournal):
        post_journal(conn, idempotency_key=str(uuid.uuid4()), txn_type="TRANSFER",
                     legs=[Leg(ids[i], amt) for i, amt in legs])


def test_insufficient_funds(conn, make_account):
    a, b = make_account(1_000), make_account()
    with pytest.raises(InsufficientFunds):
        transfer(conn, a, b, 1_001)
    assert bal(conn, a) == (1_000, 1_000)


# ------------------------------------------------------------------ idempotency


def test_retry_storm_posts_once(pool, conn, make_account):
    """50 identical requests in parallel (double clicks, client retries) -> 1 debit."""
    a, b = make_account(100_000), make_account()
    key = str(uuid.uuid4())

    def attempt(_):
        with pool.connection() as c:
            return transfer(c, a, b, 10_000, key=key)

    with ThreadPoolExecutor(max_workers=50) as ex:
        results = list(ex.map(attempt, range(50)))

    assert len({r.journal_id for r in results}) == 1
    assert sum(not r.replayed for r in results) == 1
    assert bal(conn, a) == (90_000, 90_000)
    assert bal(conn, b) == (10_000, 10_000)


def test_same_key_different_request_is_rejected(conn, make_account):
    a, b = make_account(100_000), make_account()
    key = str(uuid.uuid4())
    transfer(conn, a, b, 10_000, key=key)
    with pytest.raises(IdempotencyConflict):
        transfer(conn, a, b, 99_000, key=key)
    assert bal(conn, a)[0] == 90_000


# ------------------------------------------------------------------ races


def test_concurrent_overspend(pool, conn, make_account):
    """Balance 1000, twenty concurrent 800 debits -> exactly one succeeds."""
    a = make_account(1_000)
    targets = [make_account() for _ in range(20)]

    def attempt(dst):
        with pool.connection() as c:
            try:
                transfer(c, a, dst, 800)
                return True
            except InsufficientFunds:
                return False

    with ThreadPoolExecutor(max_workers=20) as ex:
        outcomes = list(ex.map(attempt, targets))

    assert outcomes.count(True) == 1
    assert bal(conn, a) == (200, 200)


def test_opposite_transfers_do_not_deadlock(pool, conn, make_account):
    """A->B and B->A at the same time, many times. Lock ordering prevents deadlocks."""
    a, b = make_account(1_000_000), make_account(1_000_000)

    def attempt(i):
        with pool.connection() as c:
            src, dst = (a, b) if i % 2 else (b, a)
            transfer(c, src, dst, 100)

    with ThreadPoolExecutor(max_workers=20) as ex:
        list(ex.map(attempt, range(200)))  # raises if any deadlock occurred

    assert bal(conn, a)[0] + bal(conn, b)[0] == 2_000_000


# ------------------------------------------------------------------ overdraft & status


def test_current_account_overdraft(conn, make_account):
    cur = make_account(0, product_type="CURRENT", overdraft_limit=50_000)
    dst = make_account()
    transfer(conn, cur, dst, 50_000)
    assert bal(conn, cur) == (-50_000, -50_000)
    with pytest.raises(InsufficientFunds):
        transfer(conn, cur, dst, 1)


def test_overdraft_not_allowed_on_savings(conn):
    from app.posting import open_account
    with pytest.raises(InvalidJournal):
        open_account(conn, account_id=uuid.uuid4(), product_type="SAVINGS", overdraft_limit=100)


def test_cannot_lower_overdraft_below_usage(conn, make_account):
    cur = make_account(0, product_type="CURRENT", overdraft_limit=50_000)
    transfer(conn, cur, make_account(), 40_000)
    with pytest.raises(InsufficientFunds):
        update_account(conn, cur, overdraft_limit=10_000)


def test_debit_blocked_account_can_receive_not_send(conn, make_account):
    a, b = make_account(10_000), make_account(10_000)
    update_account(conn, a, status="DEBIT_BLOCKED")
    transfer(conn, b, a, 1_000)
    with pytest.raises(AccountNotOpen):
        transfer(conn, a, b, 1_000)


# ------------------------------------------------------------------ holds


def test_hold_reserves_then_release(conn, make_account):
    a, b = make_account(1_000), make_account()
    hold = create_hold(conn, account_id=a, amount=800, idempotency_key=str(uuid.uuid4()),
                       reason="fraud review")
    assert bal(conn, a) == (1_000, 200)
    with pytest.raises(InsufficientFunds):
        transfer(conn, a, b, 300)  # can't spend held money
    release_hold(conn, hold["hold_id"])
    release_hold(conn, hold["hold_id"])  # idempotent
    assert bal(conn, a) == (1_000, 1_000)


def test_hold_capture(conn, make_account):
    a, b = make_account(1_000), make_account()
    hold = create_hold(conn, account_id=a, amount=800, idempotency_key=str(uuid.uuid4()),
                       reason="fraud review")
    transfer(conn, a, b, 800, capture_hold_id=hold["hold_id"])
    assert bal(conn, a) == (200, 200)
    with pytest.raises(HoldError):
        release_hold(conn, hold["hold_id"])
    with pytest.raises(HoldError):
        transfer(conn, a, b, 800, capture_hold_id=hold["hold_id"])


def test_hold_idempotent(conn, make_account):
    a = make_account(1_000)
    key = str(uuid.uuid4())
    h1 = create_hold(conn, account_id=a, amount=500, idempotency_key=key, reason="x")
    h2 = create_hold(conn, account_id=a, amount=500, idempotency_key=key, reason="x")
    assert h1["hold_id"] == h2["hold_id"]
    assert bal(conn, a) == (1_000, 500)


# ------------------------------------------------------------------ reversal & integrity


def test_reversal(conn, make_account):
    a, b = make_account(1_000), make_account()
    j = transfer(conn, a, b, 400)
    key = str(uuid.uuid4())
    reverse_journal(conn, j.journal_id, idempotency_key=key, reason="customer dispute")
    assert bal(conn, a)[0] == 1_000
    with pytest.raises(IdempotencyConflict):  # can't reverse twice with a new key
        reverse_journal(conn, j.journal_id, idempotency_key=str(uuid.uuid4()), reason="again")


def test_database_rejects_unbalanced_entries(conn, make_account):
    """Even code that bypasses post_journal can't write an unbalanced journal."""
    a = make_account()
    with pytest.raises(psycopg.errors.CheckViolation):
        with conn.transaction():
            jid = conn.execute(
                "INSERT INTO ledger.journals (idempotency_key, request_hash, txn_type) "
                "VALUES (%s, 'x', 'TRANSFER') RETURNING journal_id", (str(uuid.uuid4()),)
            ).fetchone()[0]
            conn.execute(
                "INSERT INTO ledger.entries (journal_id, account_id, amount, balance_after) "
                "VALUES (%s, %s, 500, 500)", (jid, a)
            )


def test_entries_are_append_only(conn, make_account):
    make_account(1_000)
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        conn.execute("UPDATE ledger.entries SET amount = amount * 2")


def test_database_blocks_overspend_directly(conn, make_account):
    a = make_account(1_000)
    with pytest.raises(psycopg.errors.CheckViolation):
        conn.execute("UPDATE ledger.balances SET available_balance = -1, ledger_balance = -1 "
                     "WHERE account_id = %s", (a,))


def test_outbox_written_with_journal(conn, make_account):
    a, b = make_account(1_000), make_account()
    j = transfer(conn, a, b, 100)
    row = conn.execute(
        "SELECT event_type, payload->>'txn_type' FROM ledger.outbox WHERE event_key = %s",
        (str(j.journal_id),),
    ).fetchone()
    assert row == ("JournalPosted", "TRANSFER")


def test_books_balance(conn, make_account):
    a, b = make_account(5_000), make_account()
    transfer(conn, a, b, 1_234)
    create_hold(conn, account_id=a, amount=100, idempotency_key=str(uuid.uuid4()), reason="x")
    report = reconcile(conn)
    assert report["balanced"], report
    assert internal_accounts.CASH  # internal accounts seeded


# ------------------------------------------------------------------ savings interest


def test_savings_interest_accrues_daily_and_posts_once(conn, make_account):
    from datetime import date, timedelta
    from app.posting import accrue_savings_interest, post_savings_interest

    acct = make_account(3_650_000)  # Rs 36,500 at 3.65% -> exactly Rs 3.65 (365 paise) a day
    start = date(2030, 1, 1) + timedelta(days=int(uuid.uuid4().int % 20000))  # unique dates per run
    for day in range(10):
        first = accrue_savings_interest(conn, business_date=start + timedelta(days=day), annual_rate="0.0365")
        again = accrue_savings_interest(conn, business_date=start + timedelta(days=day), annual_rate="0.0365")
        assert not first["replayed"] and again["replayed"]  # rerunning EOD doesn't accrue twice
    assert get_balance(conn, acct)["accrued_interest"] == 3650

    end = start + timedelta(days=9)
    post_savings_interest(conn, business_date=end)
    post_savings_interest(conn, business_date=end)  # rerun: no double credit
    b = get_balance(conn, acct)
    assert b["ledger_balance"] == 3_650_000 + 3650 and b["accrued_interest"] == 0
    assert reconcile(conn)["balanced"]
