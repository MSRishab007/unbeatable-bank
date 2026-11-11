"""The statement query: opening balance, running balance and closing balance for a date range."""

import uuid
from datetime import date

import pytest

from app import internal_accounts, posting
from app.posting import Leg


def move(conn, account, amount, on, txn_type="TRANSFER", description=None):
    """Positive amount: money into the account; negative: out of it."""
    posting.post_journal(conn, idempotency_key=str(uuid.uuid4()), txn_type=txn_type, description=description,
                         legs=[Leg(internal_accounts.CASH, -amount), Leg(account, amount)], business_date=on)


def test_statement_carries_the_balance_forward(conn, make_account):
    account = make_account()
    move(conn, account, 100_000, date(2026, 9, 28), "DEPOSIT", "Cash deposit")
    move(conn, account, -30_000, date(2026, 10, 3), description="Rent")
    move(conn, account, 5_000, date(2026, 10, 15), description="Refund")
    move(conn, account, -1_000, date(2026, 10, 31), description="Chai")
    move(conn, account, -2_000, date(2026, 11, 1), description="Next month")

    october = posting.statement(conn, account, date(2026, 10, 1), date(2026, 10, 31))
    assert (october["opening_balance"], october["closing_balance"]) == (100_000, 74_000)
    assert (october["total_credits"], october["total_debits"]) == (5_000, 31_000)
    assert [(e["description"], e["amount"], e["balance"]) for e in october["entries"]] == [
        ("Rent", -30_000, 70_000), ("Refund", 5_000, 75_000), ("Chai", -1_000, 74_000)]

    november = posting.statement(conn, account, date(2026, 11, 1), date(2026, 11, 30))
    assert november["opening_balance"] == october["closing_balance"]          # months join up exactly
    assert november["closing_balance"] == posting.get_balance(conn, account)["ledger_balance"] == 72_000


def test_an_entry_posted_late_with_an_earlier_date_lands_in_the_right_month(conn, make_account):
    account = make_account()
    move(conn, account, 50_000, date(2026, 10, 5))
    move(conn, account, -10_000, date(2026, 11, 2))
    move(conn, account, 7_000, date(2026, 10, 20), description="Backdated interest")      # posted last, dated earlier

    october = posting.statement(conn, account, date(2026, 10, 1), date(2026, 10, 31))
    assert [e["balance"] for e in october["entries"]] == [50_000, 57_000] and october["closing_balance"] == 57_000
    november = posting.statement(conn, account, date(2026, 11, 1), date(2026, 11, 30))
    assert (november["opening_balance"], november["closing_balance"]) == (57_000, 47_000)


def test_empty_period_and_bad_requests(conn, make_account):
    account = make_account(balance=9_000)
    future = posting.statement(conn, account, date(2031, 1, 1), date(2031, 1, 31))
    assert future["entries"] == [] and future["opening_balance"] == future["closing_balance"] == 9_000
    with pytest.raises(posting.InvalidJournal):
        posting.statement(conn, account, date(2026, 11, 2), date(2026, 11, 1))
    with pytest.raises(posting.AccountNotFound):
        posting.statement(conn, uuid.uuid4(), date(2026, 11, 1), date(2026, 11, 30))


def test_only_this_accounts_entries_appear(conn, make_account):
    mine, other = make_account(), make_account()
    move(conn, mine, 1_000, date(2026, 10, 2))
    move(conn, other, 99_000, date(2026, 10, 2))
    statement = posting.statement(conn, mine, date(2026, 10, 1), date(2026, 10, 31))
    assert [e["amount"] for e in statement["entries"]] == [1_000]
