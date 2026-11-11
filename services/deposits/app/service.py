"""Deposits: fixed deposits (FD), recurring deposits (RD) and end-of-day processing.

A deposit is an account of its own (opened through the Account service), and
every rupee moves through the Ledger, so the bank's books cover deposits too:

    book FD            savings account   -> FD account
    RD installment      savings account   -> RD account          (monthly, automatic)
    interest           bank: interest expense -> deposit account (quarterly)
    maturity / closure deposit account   -> savings account
    early closure      interest is recalculated at a lower rate; any excess
                       already credited goes back to bank: penalty income

End of day (EOD) is a batch run once per business date. Each step is keyed by
the date (or by deposit + date), so a run that crashes half-way can simply be
started again without paying anyone twice.
"""

from __future__ import annotations

import json
import logging
import time
import uuid
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal
from typing import Any, Protocol
from uuid import UUID

import psycopg
from prometheus_client import Counter
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from ubcommon.objstore import ObjectStore

from . import interest
from .interest import add_months

log = logging.getLogger("deposits")

BOOKED = Counter("deposits_booked_total", "Deposits booked", ["product"])
EOD_RUNS = Counter("deposits_eod_runs_total", "End-of-day runs", ["result"])

INTEREST_EXPENSE = UUID("00000000-0000-0000-0000-000000000002")
PENALTY_INCOME = UUID("00000000-0000-0000-0000-000000000003")
MIN_FD, MIN_RD = 100_000, 10_000          # Rs 1,000 and Rs 100
RD_MIN_MONTHS = 6


class DepositError(Exception):
    code = "DEPOSIT_ERROR"
    http_status = 400

    def __init__(self, message: str, **details: Any) -> None:
        super().__init__(message)
        self.details = details


class NotFound(DepositError):
    code = "NOT_FOUND"
    http_status = 404


class Forbidden(DepositError):
    code = "FORBIDDEN"
    http_status = 403


class Conflict(DepositError):
    code = "CONFLICT"
    http_status = 409


class Unavailable(DepositError):
    code = "UNAVAILABLE"
    http_status = 503


class LedgerRejected(Exception):
    def __init__(self, code: str, message: str = "") -> None:
        super().__init__(message or code)
        self.code = code


class Ledger(Protocol):
    def post_journal(self, *, idempotency_key: str, txn_type: str, legs: list[tuple[UUID, int]],
                     description: str, business_date: date) -> str:
        """Returns the journal id. Raises LedgerRejected or Unavailable."""

    def accrue_savings(self, business_date: date, annual_rate: Decimal) -> dict: ...

    def post_savings_interest(self, business_date: date) -> dict: ...

    def reconciliation(self) -> dict: ...

    def statement(self, account_id: UUID, date_from: date, date_to: date) -> dict:
        """Entries for the period with opening/closing balances. Raises NotFound for an unknown account."""


class Accounts(Protocol):
    def authorize(self, account_id: UUID, customer_id: UUID) -> dict: ...

    def open_deposit_account(self, account_id: UUID, product: str, customer_id: UUID) -> dict: ...

    def date_of_birth(self, customer_id: UUID) -> date | None: ...

    def account(self, account_id: UUID) -> dict:
        """account_number, product_type, opened_at, holders[{full_name}]. Raises NotFound."""


@dataclass
class Deps:
    accounts: Accounts
    ledger: Ledger
    objstore: ObjectStore
    signals: Any = None      # DynaClient, optional


def _cur(conn: psycopg.Connection) -> psycopg.Cursor:
    return conn.cursor(row_factory=dict_row)


# ------------------------------------------------------------------ clock and rates


def business_date(conn: psycopg.Connection) -> date:
    return conn.execute("SELECT business_date FROM deposits.bank_clock").fetchone()[0]


def rate_cards(conn: psycopg.Connection) -> list[dict]:
    with _cur(conn) as cur:
        cur.execute(
            """
            SELECT DISTINCT ON (product, min_days) product, min_days, max_days, annual_rate, effective_from
            FROM deposits.rate_cards
            WHERE effective_from <= (SELECT business_date FROM deposits.bank_clock)
            ORDER BY product, min_days, effective_from DESC
            """
        )
        return cur.fetchall()


def rate_for(conn: psycopg.Connection, product: str, days: int) -> Decimal | None:
    row = conn.execute(
        """
        SELECT annual_rate FROM deposits.rate_cards
        WHERE product = %s AND %s BETWEEN min_days AND max_days
          AND effective_from <= (SELECT business_date FROM deposits.bank_clock)
        ORDER BY effective_from DESC LIMIT 1
        """,
        (product, days),
    ).fetchone()
    return row[0] if row else None


def savings_rate(conn: psycopg.Connection) -> Decimal:
    return rate_for(conn, "SAVINGS", 0) or Decimal("0")


def quote(conn: psycopg.Connection, *, product: str, amount: int, tenure_months: int,
          senior: bool = False, start: date | None = None) -> dict:
    """What a deposit would earn. `amount` is the FD amount or the RD monthly installment (paise)."""
    if product not in ("FD", "RD"):
        raise DepositError("product must be FD or RD")
    minimum = MIN_FD if product == "FD" else MIN_RD
    if amount < minimum:
        raise DepositError(f"minimum is Rs {minimum // 100:,}")
    if not 1 <= tenure_months <= 120 or (product == "RD" and tenure_months < RD_MIN_MONTHS):
        raise DepositError("tenure must be 1-120 months for an FD and 6-120 months for an RD")
    start = start or business_date(conn)
    maturity = add_months(start, tenure_months)
    rate = rate_for(conn, product, (maturity - start).days)
    if rate is None:
        raise DepositError("no rate available for this tenure")
    if senior:
        rate += interest.SENIOR_BONUS
    flows = ([(start, amount)] if product == "FD"
             else [(d, amount) for d in interest.rd_schedule(start, tenure_months)])
    p = interest.project(flows, rate, start, maturity)
    return {"product": product, "amount": amount, "tenure_months": tenure_months, "annual_rate": rate,
            "senior_citizen_rate": senior, "start_date": start, "maturity_date": maturity,
            "total_deposited": p.paid_in, "interest": p.interest, "maturity_amount": p.balance}


# ------------------------------------------------------------------ booking


def book(conn: psycopg.Connection, deps: Deps, *, customer_id: UUID, product: str, funding_account_id: UUID,
         amount: int, tenure_months: int, idempotency_key: str) -> dict:
    """Open an FD (lump sum) or an RD (first installment now, the rest monthly)."""
    if not idempotency_key:
        raise DepositError("Idempotency-Key header is required")
    auth = deps.accounts.authorize(funding_account_id, customer_id)
    if not auth.get("allowed"):
        raise Forbidden("you can't fund a deposit from this account", reason=auth.get("reason"))
    if auth.get("product_type") not in ("SAVINGS", "CURRENT"):
        raise DepositError("deposits are funded from a savings or current account")

    dob = deps.accounts.date_of_birth(customer_id)
    today = business_date(conn)
    q = quote(conn, product=product, amount=amount, tenure_months=tenure_months, start=today,
              senior=dob is not None and interest.age_on(dob, today) >= 60)

    with conn.transaction(), _cur(conn) as cur:
        cur.execute(
            """
            INSERT INTO deposits.deposits
                (deposit_id, customer_id, idempotency_key, product, funding_account_id, principal,
                 tenure_months, annual_rate, start_date, maturity_date, projected_maturity, next_capitalisation)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (customer_id, idempotency_key) DO NOTHING
            RETURNING *
            """,
            (uuid.uuid4(), customer_id, idempotency_key, product, funding_account_id, amount, tenure_months,
             q["annual_rate"], today, q["maturity_date"], q["maturity_amount"], add_months(today, 3)),
        )
        dep = cur.fetchone()
        if dep is None:
            cur.execute("SELECT * FROM deposits.deposits WHERE customer_id = %s AND idempotency_key = %s",
                        (customer_id, idempotency_key))
            dep = cur.fetchone()
            if (dep["product"], dep["principal"], dep["tenure_months"]) != (product, amount, tenure_months):
                raise Conflict("Idempotency-Key was already used for a different deposit")
            if dep["status"] != "PENDING":
                return present(dep)        # already booked (or failed): same answer as the first time
        elif product == "RD":
            for n, due in enumerate(interest.rd_schedule(today, tenure_months), start=1):
                cur.execute("INSERT INTO deposits.rd_installments (deposit_id, n, due_date) VALUES (%s, %s, %s)",
                            (dep["deposit_id"], n, due))

    # Each step below is idempotent, so a retry after a crash continues where it stopped.
    deposit_id = dep["deposit_id"]
    account = deps.accounts.open_deposit_account(deposit_id, product, customer_id)
    try:
        deps.ledger.post_journal(
            idempotency_key=f"dep-book:{deposit_id}",
            txn_type="FD_BOOKING" if product == "FD" else "RD_INSTALLMENT",
            legs=[(funding_account_id, -amount), (deposit_id, amount)],
            description=f"{product} {account['account_number']} " + ("booked" if product == "FD" else "installment 1"),
            business_date=dep["start_date"])
    except LedgerRejected as r:
        conn.execute("UPDATE deposits.deposits SET status = 'FAILED', failure_code = %s WHERE deposit_id = %s "
                     "AND status = 'PENDING'", (r.code, deposit_id))
        message = "insufficient balance in the funding account" if r.code == "INSUFFICIENT_FUNDS" else str(r)
        raise DepositError(message, code=r.code) from r

    with conn.transaction(), _cur(conn) as cur:
        cur.execute(
            "UPDATE deposits.deposits SET status = 'ACTIVE', balance = %s, account_number = %s, "
            "installments_paid = %s WHERE deposit_id = %s AND status = 'PENDING' RETURNING *",
            (amount, account["account_number"], 1 if product == "RD" else 0, deposit_id))
        row = cur.fetchone()
        if row is None:
            cur.execute("SELECT * FROM deposits.deposits WHERE deposit_id = %s", (deposit_id,))
            row = cur.fetchone()
        elif product == "RD":
            cur.execute("UPDATE deposits.rd_installments SET status = 'PAID', paid_on = %s "
                        "WHERE deposit_id = %s AND n = 1", (row["start_date"], deposit_id))
    BOOKED.labels(product).inc()
    return present(row)


def present(dep: dict) -> dict:
    out = {k: v for k, v in dep.items() if k not in ("idempotency_key",)}
    days_total = (dep["maturity_date"] - dep["start_date"]).days or 1
    out["interest_earned"] = dep["interest_posted"] + int(dep["accrued"])
    out["days_total"] = days_total
    return out


def list_deposits(conn: psycopg.Connection, customer_id: UUID) -> list[dict]:
    today = business_date(conn)
    with _cur(conn) as cur:
        cur.execute("SELECT * FROM deposits.deposits WHERE customer_id = %s AND status <> 'FAILED' "
                    "ORDER BY created_at DESC", (customer_id,))
        return [{**present(d), "days_elapsed": min((today - d["start_date"]).days,
                                                    (d["maturity_date"] - d["start_date"]).days)}
                for d in cur.fetchall()]


def get_deposit(conn: psycopg.Connection, deposit_id: UUID, customer_id: UUID | None) -> dict:
    with _cur(conn) as cur:
        cur.execute("SELECT * FROM deposits.deposits WHERE deposit_id = %s", (deposit_id,))
        dep = cur.fetchone()
        if dep is None or (customer_id is not None and dep["customer_id"] != customer_id):
            raise NotFound("deposit not found")
        out = present(dep)
        if dep["product"] == "RD":
            cur.execute("SELECT n, due_date, status, paid_on FROM deposits.rd_installments "
                        "WHERE deposit_id = %s ORDER BY n", (deposit_id,))
            out["installments"] = cur.fetchall()
    return out


# ------------------------------------------------------------------ early closure


def _paid_cashflows(conn: psycopg.Connection, dep: dict) -> list[tuple[date, int]]:
    if dep["product"] == "FD":
        return [(dep["start_date"], dep["principal"])]
    rows = conn.execute("SELECT paid_on FROM deposits.rd_installments WHERE deposit_id = %s AND status = 'PAID'",
                        (dep["deposit_id"],)).fetchall()
    return [(r[0], dep["principal"]) for r in rows]


def closure_quote(conn: psycopg.Connection, deposit_id: UUID, customer_id: UUID | None) -> dict:
    """What the customer gets if they break the deposit today."""
    dep = get_deposit(conn, deposit_id, customer_id)
    if dep["status"] != "ACTIVE":
        raise Conflict("only an active deposit can be closed", status=dep["status"])
    today = business_date(conn)
    held_days = (today - dep["start_date"]).days
    card = rate_for(conn, dep["product"], held_days) or Decimal(0)   # the rate for the time actually held
    rate = max(Decimal(0), min(card, dep["annual_rate"]) - interest.PREMATURE_PENALTY)
    p = interest.project(_paid_cashflows(conn, dep), rate, dep["start_date"], today)
    paid_in = dep["balance"] - dep["interest_posted"]
    return {
        "deposit_id": dep["deposit_id"], "held_days": held_days, "booked_rate": dep["annual_rate"],
        "closure_rate": rate, "deposited": paid_in, "interest_at_closure_rate": p.interest,
        "interest_already_credited": dep["interest_posted"],
        "payout_amount": paid_in + p.interest,
        "maturity_amount_if_kept": dep["projected_maturity"],
    }


def close_early(conn: psycopg.Connection, deps: Deps, deposit_id: UUID, customer_id: UUID) -> dict:
    q = closure_quote(conn, deposit_id, customer_id)
    with conn.transaction(), _cur(conn) as cur:
        cur.execute("SELECT * FROM deposits.deposits WHERE deposit_id = %s FOR UPDATE", (deposit_id,))
        dep = cur.fetchone()
        if dep["status"] != "ACTIVE":
            raise Conflict("only an active deposit can be closed", status=dep["status"])
        today = business_date(conn)
        adjustment = q["interest_at_closure_rate"] - dep["interest_posted"]
        try:
            if adjustment > 0:
                deps.ledger.post_journal(
                    idempotency_key=f"dep-close-int:{deposit_id}", txn_type="INTEREST",
                    legs=[(INTEREST_EXPENSE, -adjustment), (deposit_id, adjustment)],
                    description="Interest on early closure", business_date=today)
            elif adjustment < 0:
                deps.ledger.post_journal(
                    idempotency_key=f"dep-close-pen:{deposit_id}", txn_type="PENALTY",
                    legs=[(deposit_id, adjustment), (PENALTY_INCOME, -adjustment)],
                    description="Interest reversed on early closure", business_date=today)
            deps.ledger.post_journal(
                idempotency_key=f"dep-close:{deposit_id}", txn_type="FD_PREMATURE_CLOSURE",
                legs=[(deposit_id, -q["payout_amount"]), (dep["funding_account_id"], q["payout_amount"])],
                description=f"{dep['product']} {dep['account_number']} closed early", business_date=today)
        except LedgerRejected as r:
            raise DepositError(f"could not close the deposit: {r.code}", code=r.code) from r
        cur.execute(
            "UPDATE deposits.deposits SET status = 'CLOSED', balance = 0, accrued = 0, payout_amount = %s, "
            "penalty_amount = %s, interest_posted = %s, closed_at = now() WHERE deposit_id = %s RETURNING *",
            (q["payout_amount"], max(0, -adjustment), q["interest_at_closure_rate"], deposit_id))
        row = cur.fetchone()
    # Breaking a deposit and then sending the money away is a known fraud pattern: tell the risk engine.
    if deps.signals is not None:
        try:
            deps.signals.put(f"sig:cust:{customer_id}:fd_break", repr(time.time()), ttl=1800, w=1)
        except Exception:
            log.warning("could not write fd_break signal")
    return {**present(row), "closure": q}


# ------------------------------------------------------------------ end of day


def run_eod(conn: psycopg.Connection, deps: Deps) -> dict:
    """Process the current business date, then move the clock to the next day."""
    today = business_date(conn)
    with _cur(conn) as cur:
        cur.execute("INSERT INTO deposits.eod_runs (business_date) VALUES (%s) ON CONFLICT DO NOTHING", (today,))
        cur.execute("SELECT status, stats FROM deposits.eod_runs WHERE business_date = %s", (today,))
        run = cur.fetchone()
    if run["status"] == "DONE":      # the clock didn't advance last time: finish that step
        _advance_clock(conn, today)
        return {"business_date": today, "replayed": True, **run["stats"]}

    stats = {"deposits_accrued": 0, "interest_credits": 0, "interest_credited": 0, "installments_paid": 0,
             "installments_missed": 0, "matured": 0, "paid_out": 0}
    try:
        savings = deps.ledger.accrue_savings(today, savings_rate(conn))
        stats["savings_accounts_accrued"] = savings.get("accounts", 0)
        if interest.is_quarter_end(today):
            posted = deps.ledger.post_savings_interest(today)
            stats["savings_interest_accounts"] = posted.get("accounts", 0)
            stats["savings_interest_credited"] = posted.get("total_paise", 0)

        ids = [r[0] for r in conn.execute(
            "SELECT deposit_id FROM deposits.deposits WHERE status = 'ACTIVE' ORDER BY deposit_id").fetchall()]
        for deposit_id in ids:
            try:
                _process_deposit(conn, deps, deposit_id, today, stats)
            except LedgerRejected as r:
                # One deposit the ledger refuses must not stop everyone else's interest.
                stats["errors"] = stats.get("errors", 0) + 1
                log.error("EOD %s: deposit %s skipped: %s", today, deposit_id, r.code)
        stats["books_balanced"] = bool(deps.ledger.reconciliation().get("balanced"))
    except Unavailable:
        EOD_RUNS.labels("failed").inc()
        raise

    report = {"business_date": today.isoformat(), **stats}
    stats["report"] = deps.objstore.put(f"eod/{today.isoformat()}.json",
                                        json.dumps(report, indent=2).encode(), "application/json")
    conn.execute("UPDATE deposits.eod_runs SET status = 'DONE', stats = %s, finished_at = now() "
                 "WHERE business_date = %s", (Jsonb(stats), today))
    _advance_clock(conn, today)
    EOD_RUNS.labels("ok").inc()
    log.info("EOD %s done: %s", today, stats)
    return {"business_date": today, "replayed": False, **stats}


def _advance_clock(conn: psycopg.Connection, processed: date) -> None:
    conn.execute("UPDATE deposits.bank_clock SET business_date = %s WHERE business_date = %s",
                 (processed + timedelta(days=1), processed))


def fast_forward(conn: psycopg.Connection, deps: Deps, days: int) -> dict:
    """Demo control: run end-of-day for several days in a row."""
    if not 1 <= days <= 400:
        raise DepositError("days must be between 1 and 400")
    totals: dict[str, int] = {}
    first = business_date(conn)
    for _ in range(days):
        for key, value in run_eod(conn, deps).items():
            if isinstance(value, int) and not isinstance(value, bool):
                totals[key] = totals.get(key, 0) + value
    return {"from": first, "to": business_date(conn), "days": days, **totals}


def _process_deposit(conn: psycopg.Connection, deps: Deps, deposit_id: UUID, today: date, stats: dict) -> None:
    """One deposit, one business date. Same order of steps as interest.project()."""
    with conn.transaction(), _cur(conn) as cur:
        cur.execute("SELECT * FROM deposits.deposits WHERE deposit_id = %s FOR UPDATE", (deposit_id,))
        dep = cur.fetchone()
        if dep["status"] != "ACTIVE" or (dep["last_accrual_date"] is not None and dep["last_accrual_date"] >= today):
            return   # already processed for this date
        label = f"{dep['product']} {dep['account_number']}"
        balance, posted, accrued = dep["balance"], dep["interest_posted"], dep["accrued"]

        if today >= dep["maturity_date"]:
            credit = interest.final_paise(accrued)
            if credit > 0:
                deps.ledger.post_journal(
                    idempotency_key=f"dep-int:{deposit_id}:final", txn_type="INTEREST",
                    legs=[(INTEREST_EXPENSE, -credit), (deposit_id, credit)],
                    description=f"{label} final interest", business_date=today)
            balance, posted = balance + credit, posted + credit
            penalty = 0
            if dep["installments_missed"]:
                penalty = min(balance, int(dep["principal"] * interest.RD_MISSED_PENALTY * dep["installments_missed"]))
                if penalty:
                    deps.ledger.post_journal(
                        idempotency_key=f"dep-pen:{deposit_id}", txn_type="PENALTY",
                        legs=[(deposit_id, -penalty), (PENALTY_INCOME, penalty)],
                        description=f"{label} missed installment charges", business_date=today)
            payout = balance - penalty
            deps.ledger.post_journal(
                idempotency_key=f"dep-mat:{deposit_id}",
                txn_type="FD_MATURITY" if dep["product"] == "FD" else "RD_MATURITY",
                legs=[(deposit_id, -payout), (dep["funding_account_id"], payout)],
                description=f"{label} matured", business_date=today)
            cur.execute(
                "UPDATE deposits.deposits SET status = 'MATURED', balance = 0, accrued = 0, interest_posted = %s, "
                "payout_amount = %s, penalty_amount = %s, last_accrual_date = %s, closed_at = now() "
                "WHERE deposit_id = %s", (posted, payout, penalty, today, deposit_id))
            stats["matured"] += 1
            stats["paid_out"] += payout
            stats["interest_credited"] += credit
            return

        next_cap = dep["next_capitalisation"]
        if today >= next_cap:
            credit = interest.whole_paise(accrued)
            if credit > 0:
                deps.ledger.post_journal(
                    idempotency_key=f"dep-int:{deposit_id}:{next_cap.isoformat()}", txn_type="INTEREST",
                    legs=[(INTEREST_EXPENSE, -credit), (deposit_id, credit)],
                    description=f"{label} quarterly interest", business_date=today)
                stats["interest_credits"] += 1
                stats["interest_credited"] += credit
            balance, posted, accrued = balance + credit, posted + credit, accrued - credit
            quarters = round((next_cap.year - dep["start_date"].year) * 12
                             + next_cap.month - dep["start_date"].month) // 3
            next_cap = add_months(dep["start_date"], 3 * (quarters + 1))

        paid = missed = 0
        if dep["product"] == "RD":
            cur.execute("SELECT n FROM deposits.rd_installments WHERE deposit_id = %s AND status = 'DUE' "
                        "AND due_date <= %s ORDER BY n", (deposit_id, today))
            for (n,) in [(r["n"],) for r in cur.fetchall()]:
                try:
                    deps.ledger.post_journal(
                        idempotency_key=f"rd:{deposit_id}:{n}", txn_type="RD_INSTALLMENT",
                        legs=[(dep["funding_account_id"], -dep["principal"]), (deposit_id, dep["principal"])],
                        description=f"{label} installment {n}", business_date=today)
                except LedgerRejected:
                    cur.execute("UPDATE deposits.rd_installments SET status = 'MISSED' "
                                "WHERE deposit_id = %s AND n = %s", (deposit_id, n))
                    missed += 1
                    continue
                cur.execute("UPDATE deposits.rd_installments SET status = 'PAID', paid_on = %s "
                            "WHERE deposit_id = %s AND n = %s", (today, deposit_id, n))
                balance += dep["principal"]
                paid += 1

        accrued += interest.daily_interest(balance, dep["annual_rate"])
        cur.execute(
            "UPDATE deposits.deposits SET balance = %s, interest_posted = %s, accrued = %s, "
            "next_capitalisation = %s, last_accrual_date = %s, installments_paid = installments_paid + %s, "
            "installments_missed = installments_missed + %s WHERE deposit_id = %s",
            (balance, posted, accrued, next_cap, today, paid, missed, deposit_id))
        stats["deposits_accrued"] += 1
        stats["installments_paid"] += paid
        stats["installments_missed"] += missed
