import json
import uuid
from datetime import date
from decimal import Decimal

import pytest

from app import interest, service

RS = 100
LAKH = 100_000 * RS


def key():
    return str(uuid.uuid4())


@pytest.fixture
def riya(deps):
    cid = deps.accounts.customer()
    return {"cid": cid, "savings": deps.accounts.savings(cid, balance=5 * LAKH)}


def book_fd(conn, deps, riya, amount=LAKH, months=12, k=None):
    return service.book(conn, deps, customer_id=riya["cid"], product="FD", funding_account_id=riya["savings"],
                        amount=amount, tenure_months=months, idempotency_key=k or key())


def book_rd(conn, deps, riya, installment=2_000 * RS, months=6):
    return service.book(conn, deps, customer_id=riya["cid"], product="RD", funding_account_id=riya["savings"],
                        amount=installment, tenure_months=months, idempotency_key=key())


def days_until(conn, target: date) -> int:
    return (target - service.business_date(conn)).days


# ------------------------------------------------------------------ quotes


def test_fd_quote_matches_the_compound_interest_formula(conn):
    q = service.quote(conn, product="FD", amount=LAKH, tenure_months=12)
    assert q["annual_rate"] == Decimal("0.0700") and q["maturity_date"] == date(2027, 1, 1)
    assert q["maturity_amount"] == round(100_000 * (1 + 0.07 / 4) ** 4 * 100)      # Rs 1,07,185.90
    assert q["interest"] == q["maturity_amount"] - LAKH


def test_rate_depends_on_tenure_and_age(conn):
    rates = {m: service.quote(conn, product="FD", amount=LAKH, tenure_months=m)["annual_rate"] for m in (1, 3, 6, 24)}
    assert rates == {1: Decimal("0.0400"), 3: Decimal("0.0550"), 6: Decimal("0.0625"), 24: Decimal("0.0725")}
    senior = service.quote(conn, product="FD", amount=LAKH, tenure_months=12, senior=True)
    assert senior["annual_rate"] == Decimal("0.0750")


def test_rd_quote(conn):
    q = service.quote(conn, product="RD", amount=2_000 * RS, tenure_months=12)
    assert q["total_deposited"] == 24_000 * RS and q["annual_rate"] == Decimal("0.0650")
    assert 800 * RS < q["interest"] < 900 * RS          # about Rs 861 on Rs 24,000 paid in monthly


def test_quote_validation(conn):
    for bad in ({"product": "FD", "amount": 50 * RS, "tenure_months": 12},
                {"product": "RD", "amount": 2_000 * RS, "tenure_months": 3},
                {"product": "FD", "amount": LAKH, "tenure_months": 121}):
        with pytest.raises(service.DepositError):
            service.quote(conn, **bad)


# ------------------------------------------------------------------ booking


def test_booking_an_fd_moves_the_money(conn, deps, balance, riya):
    fd = book_fd(conn, deps, riya)
    assert fd["status"] == "ACTIVE" and fd["balance"] == LAKH and fd["account_number"]
    assert balance(riya["savings"]) == 4 * LAKH and balance(fd["deposit_id"]) == LAKH


def test_customer_can_hold_several_deposits(conn, deps, riya):
    book_fd(conn, deps, riya, months=6)
    book_fd(conn, deps, riya, months=24)
    book_rd(conn, deps, riya)
    assert sorted(d["product"] for d in service.list_deposits(conn, riya["cid"])) == ["FD", "FD", "RD"]


def test_booking_retry_is_the_same_deposit(conn, deps, balance, riya):
    k = key()
    first, again = book_fd(conn, deps, riya, k=k), book_fd(conn, deps, riya, k=k)
    assert first["deposit_id"] == again["deposit_id"] and balance(riya["savings"]) == 4 * LAKH
    with pytest.raises(service.Conflict):
        book_fd(conn, deps, riya, amount=2 * LAKH, k=k)


def test_insufficient_funds_and_other_peoples_accounts(conn, deps, balance, riya):
    with pytest.raises(service.DepositError) as exc:
        book_fd(conn, deps, riya, amount=9 * LAKH)
    assert exc.value.details["code"] == "INSUFFICIENT_FUNDS" and balance(riya["savings"]) == 5 * LAKH
    assert service.list_deposits(conn, riya["cid"]) == []
    stranger = {"cid": deps.accounts.customer(), "savings": riya["savings"]}
    with pytest.raises(service.Forbidden):
        book_fd(conn, deps, stranger)


def test_senior_citizen_gets_the_bonus_rate(conn, deps):
    cid = deps.accounts.customer(born=date(1960, 3, 1))
    fd = book_fd(conn, deps, {"cid": cid, "savings": deps.accounts.savings(cid, balance=2 * LAKH)})
    assert fd["annual_rate"] == Decimal("0.0750")


# ------------------------------------------------------------------ FD life cycle


def test_fd_pays_exactly_what_was_quoted(conn, deps, balance, riya):
    fd = book_fd(conn, deps, riya)                                   # Rs 1,00,000, 12 months, 7%
    result = service.fast_forward(conn, deps, days_until(conn, fd["maturity_date"]) + 1)
    done = service.get_deposit(conn, fd["deposit_id"], riya["cid"])
    assert done["status"] == "MATURED" and done["payout_amount"] == fd["projected_maturity"] == 10_718_590
    assert balance(riya["savings"]) >= 4 * LAKH + 10_718_590        # plus some savings interest
    assert balance(fd["deposit_id"]) == 0
    assert result["matured"] == 1 and result["interest_credits"] == 3    # three quarterly credits, then the final one
    assert deps.ledger.reconciliation()["balanced"]


def test_interest_is_credited_every_quarter(conn, deps, balance, riya):
    fd = book_fd(conn, deps, riya)
    service.fast_forward(conn, deps, days_until(conn, date(2026, 4, 1)) + 1)   # through the first quarter date
    after_q1 = service.get_deposit(conn, fd["deposit_id"], riya["cid"])
    first_quarter = round(LAKH * 0.07 * 90 / 365)                    # 1 Jan - 31 Mar = 90 days
    assert abs(after_q1["interest_posted"] - first_quarter) <= 1
    assert balance(fd["deposit_id"]) == LAKH + after_q1["interest_posted"]
    assert after_q1["next_capitalisation"] == date(2026, 7, 1)


def test_rerunning_end_of_day_never_pays_twice(conn, deps, balance, riya):
    fd = book_fd(conn, deps, riya)
    service.fast_forward(conn, deps, 95)                             # past the first quarterly credit
    before = (balance(fd["deposit_id"]), balance(riya["savings"]),
              service.get_deposit(conn, fd["deposit_id"], riya["cid"])["accrued"])
    # Simulate a crash: the run for the last date is marked unfinished and the clock is put back.
    last = service.business_date(conn).fromordinal(service.business_date(conn).toordinal() - 1)
    conn.execute("UPDATE deposits.eod_runs SET status = 'RUNNING' WHERE business_date = %s", (last,))
    conn.execute("UPDATE deposits.bank_clock SET business_date = %s", (last,))
    service.run_eod(conn, deps)
    after = (balance(fd["deposit_id"]), balance(riya["savings"]),
             service.get_deposit(conn, fd["deposit_id"], riya["cid"])["accrued"])
    assert after == before
    assert service.run_eod(conn, deps)["replayed"] is False          # the next day proceeds normally


# ------------------------------------------------------------------ RD life cycle


def test_rd_collects_installments_and_pays_what_was_quoted(conn, deps, balance, riya):
    rd = book_rd(conn, deps, riya)                                   # Rs 2,000 x 6 months
    assert balance(riya["savings"]) == 5 * LAKH - 2_000 * RS         # first installment taken at booking
    result = service.fast_forward(conn, deps, days_until(conn, rd["maturity_date"]) + 1)
    done = service.get_deposit(conn, rd["deposit_id"], riya["cid"])
    assert [i["status"] for i in done["installments"]] == ["PAID"] * 6
    assert result["installments_paid"] == 5 and result["installments_missed"] == 0
    assert done["status"] == "MATURED" and done["payout_amount"] == rd["projected_maturity"]
    assert done["payout_amount"] > 12_000 * RS
    assert deps.ledger.reconciliation()["balanced"]


def test_rd_missed_installment_is_recorded_and_charged(conn, deps, posting, pool, balance):
    cid = deps.accounts.customer()
    poor = {"cid": cid, "savings": deps.accounts.savings(cid, balance=5_000 * RS)}   # enough for 2 of 6
    rd = book_rd(conn, deps, poor)
    service.fast_forward(conn, deps, days_until(conn, date(2026, 3, 2)))             # installments 2 and 3 fall due
    mid = service.get_deposit(conn, rd["deposit_id"], cid)
    assert [i["status"] for i in mid["installments"]][:3] == ["PAID", "PAID", "MISSED"]
    with pool.connection() as c:                                                      # the customer tops up
        posting.post_journal(c, idempotency_key=key(), txn_type="DEPOSIT",
                             legs=[posting.Leg(uuid.UUID(int=1), -20_000 * RS), posting.Leg(poor["savings"], 20_000 * RS)])
    service.fast_forward(conn, deps, days_until(conn, rd["maturity_date"]) + 1)
    done = service.get_deposit(conn, rd["deposit_id"], cid)
    assert [i["status"] for i in done["installments"]] == ["PAID", "PAID", "MISSED", "PAID", "PAID", "PAID"]
    assert done["penalty_amount"] == 30 * RS                         # 1.5% of Rs 2,000 for the one missed
    assert 10_000 * RS < done["payout_amount"] < rd["projected_maturity"]
    assert deps.ledger.reconciliation()["balanced"]


# ------------------------------------------------------------------ early closure


def test_breaking_an_fd_early_costs_interest(conn, deps, balance, riya):
    fd = book_fd(conn, deps, riya)                                   # booked at 7% for 12 months
    service.fast_forward(conn, deps, 200)                            # two quarterly credits at 7% already made
    q = service.closure_quote(conn, fd["deposit_id"], riya["cid"])
    assert q["held_days"] == 200 and q["closure_rate"] == Decimal("0.0525")      # 180-364 day rate 6.25% - 1%
    assert q["interest_at_closure_rate"] < q["interest_already_credited"]         # some must be given back
    assert LAKH < q["payout_amount"] < q["maturity_amount_if_kept"]

    savings_before = balance(riya["savings"])
    closed = service.close_early(conn, deps, fd["deposit_id"], riya["cid"])
    assert closed["status"] == "CLOSED" and closed["payout_amount"] == q["payout_amount"]
    assert closed["penalty_amount"] == q["interest_already_credited"] - q["interest_at_closure_rate"]
    assert balance(riya["savings"]) == savings_before + q["payout_amount"] and balance(fd["deposit_id"]) == 0
    assert f"sig:cust:{riya['cid']}:fd_break" in deps.signals.keys   # the risk engine is told
    assert deps.ledger.reconciliation()["balanced"]
    with pytest.raises(service.Conflict):
        service.close_early(conn, deps, fd["deposit_id"], riya["cid"])


def test_closing_within_a_week_returns_just_the_principal(conn, deps, riya):
    fd = book_fd(conn, deps, riya)
    service.fast_forward(conn, deps, 3)
    assert service.closure_quote(conn, fd["deposit_id"], riya["cid"])["payout_amount"] == LAKH


def test_deposits_are_private(conn, deps, riya):
    fd = book_fd(conn, deps, riya)
    with pytest.raises(service.NotFound):
        service.get_deposit(conn, fd["deposit_id"], uuid.uuid4())
    with pytest.raises(service.NotFound):
        service.close_early(conn, deps, fd["deposit_id"], uuid.uuid4())


# ------------------------------------------------------------------ savings interest and reports


def test_savings_interest_accrues_daily_and_is_paid_at_quarter_end(conn, deps, balance):
    cid = deps.accounts.customer()
    savings = deps.accounts.savings(cid, balance=3_650_000)          # Rs 36,500 at 3.5% = Rs 3.50 a day
    service.fast_forward(conn, deps, days_until(conn, date(2026, 3, 31)))
    assert balance(savings) == 3_650_000                             # accruing, not yet paid
    service.run_eod(conn, deps)                                      # 31 March
    assert balance(savings) == 3_650_000 + 90 * 350                  # 1 Jan - 31 Mar: 90 days


def test_eod_writes_a_report(conn, deps, riya):
    book_fd(conn, deps, riya)
    result = service.run_eod(conn, deps)
    report = json.loads(deps.objstore.get("eod/2026-01-01.json"))
    assert report["business_date"] == "2026-01-01" and report["deposits_accrued"] == 1
    assert report["books_balanced"] is True and result["report"].endswith("eod/2026-01-01.json")
    assert service.business_date(conn) == date(2026, 1, 2)


def test_projection_helpers():
    assert interest.add_months(date(2026, 1, 31), 1) == date(2026, 2, 28)
    assert interest.is_quarter_end(date(2026, 6, 30)) and not interest.is_quarter_end(date(2026, 6, 29))
    assert interest.age_on(date(1966, 11, 10), date(2026, 11, 9)) == 59
