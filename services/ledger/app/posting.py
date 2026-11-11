"""The posting engine: the only code that changes money.

Double-money protections implemented here:
  1. Idempotency  - journals.idempotency_key is UNIQUE; a retried request returns
                    the original journal instead of posting twice.
  2. No races     - all touched balance rows are locked (SELECT ... FOR UPDATE) in
                    account_id order before checking funds, so two concurrent debits
                    can't both see the same balance, and A->B / B->A can't deadlock.
  3. Double entry - legs must sum to zero (checked here and again by a DB trigger).
  4. Holds        - reserved funds reduce available_balance immediately.
  5. Outbox       - the event for Kafka is written in the same transaction.
Lock order everywhere: balances (by account_id) first, then holds.

Connections must be in autocommit mode; every function opens its own
transaction with conn.transaction().
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any, Iterable
from uuid import UUID

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

JOURNAL_TOPIC = "ledger.journal-posted"
HOLD_TOPIC = "ledger.hold-changed"


# --------------------------------------------------------------------------- errors


class LedgerError(Exception):
    code = "LEDGER_ERROR"
    http_status = 400

    def __init__(self, message: str, **details: Any) -> None:
        super().__init__(message)
        self.details = details


class InvalidJournal(LedgerError):
    code = "INVALID_JOURNAL"


class AccountNotFound(LedgerError):
    code = "ACCOUNT_NOT_FOUND"
    http_status = 404


class AccountNotOpen(LedgerError):
    code = "ACCOUNT_NOT_OPEN"
    http_status = 409


class InsufficientFunds(LedgerError):
    code = "INSUFFICIENT_FUNDS"
    http_status = 422


class IdempotencyConflict(LedgerError):
    code = "IDEMPOTENCY_KEY_REUSED"
    http_status = 409


class HoldError(LedgerError):
    code = "HOLD_ERROR"
    http_status = 409


class NotFound(LedgerError):
    code = "NOT_FOUND"
    http_status = 404


# --------------------------------------------------------------------------- types


@dataclass(frozen=True)
class Leg:
    account_id: UUID
    amount: int  # paise; > 0 credit, < 0 debit


@dataclass
class PostResult:
    journal_id: UUID
    replayed: bool  # True when the idempotency key had already been posted
    txn_type: str
    entries: list[dict] = field(default_factory=list)


# --------------------------------------------------------------------------- helpers


def _request_hash(txn_type: str, legs: Iterable[Leg], capture_hold_id: UUID | None) -> str:
    body = {
        "txn_type": txn_type,
        "legs": sorted([str(l.account_id), l.amount] for l in legs),
        "capture_hold_id": str(capture_hold_id) if capture_hold_id else None,
    }
    return hashlib.sha256(json.dumps(body, sort_keys=True).encode()).hexdigest()


def _validate_legs(legs: list[Leg]) -> None:
    if len(legs) < 2:
        raise InvalidJournal("a journal needs at least two legs")
    ids = [l.account_id for l in legs]
    if len(set(ids)) != len(ids):
        raise InvalidJournal("each account may appear only once per journal")
    for l in legs:
        if not isinstance(l.amount, int) or isinstance(l.amount, bool) or l.amount == 0:
            raise InvalidJournal("leg amounts must be non-zero integers (paise)")
    if sum(l.amount for l in legs) != 0:
        raise InvalidJournal("legs must sum to zero", total=sum(l.amount for l in legs))


def _lock_balances(cur: psycopg.Cursor, account_ids: Iterable[UUID]) -> dict[UUID, dict]:
    ids = sorted(set(account_ids))
    cur.execute(
        """
        SELECT account_id, kind, product_type, status, ledger_balance,
               available_balance, overdraft_limit
        FROM ledger.balances
        WHERE account_id = ANY(%s)
        ORDER BY account_id
        FOR UPDATE
        """,
        (ids,),
    )
    rows = {r["account_id"]: r for r in cur.fetchall()}
    missing = [str(i) for i in ids if i not in rows]
    if missing:
        raise AccountNotFound("ledger account not found", account_ids=missing)
    return rows


def _entries(cur: psycopg.Cursor, journal_id: UUID) -> list[dict]:
    cur.execute(
        """
        SELECT entry_id, account_id, amount, balance_after, created_at
        FROM ledger.entries WHERE journal_id = %s ORDER BY entry_id
        """,
        (journal_id,),
    )
    return cur.fetchall()


def _cursor(conn: psycopg.Connection) -> psycopg.Cursor:
    return conn.cursor(row_factory=dict_row)


# --------------------------------------------------------------------------- accounts


def open_account(
    conn: psycopg.Connection,
    *,
    account_id: UUID,
    product_type: str,
    overdraft_limit: int = 0,
    kind: str = "CUSTOMER",
) -> dict:
    """Create the ledger side of an account. Idempotent: safe to call again."""
    with conn.transaction(), _cursor(conn) as cur:
        try:
            cur.execute(
                """
                INSERT INTO ledger.balances (account_id, kind, product_type, overdraft_limit)
                VALUES (%s, %s, %s, %s)
                ON CONFLICT (account_id) DO NOTHING
                """,
                (account_id, kind, product_type, overdraft_limit),
            )
        except psycopg.errors.CheckViolation as e:
            raise InvalidJournal("invalid ledger account", reason=str(e.diag.constraint_name)) from e
        cur.execute("SELECT * FROM ledger.balances WHERE account_id = %s", (account_id,))
        row = cur.fetchone()
        if row["product_type"] != product_type or row["kind"] != kind:
            raise IdempotencyConflict("account already exists with different settings")
        return row


def update_account(
    conn: psycopg.Connection,
    account_id: UUID,
    *,
    status: str | None = None,
    overdraft_limit: int | None = None,
) -> dict:
    """Change status (OPEN / DEBIT_BLOCKED / CLOSED) or overdraft limit."""
    with conn.transaction(), _cursor(conn) as cur:
        acct = _lock_balances(cur, [account_id])[account_id]
        if status == "CLOSED" and acct["ledger_balance"] != 0:
            raise AccountNotOpen("account must have zero balance before closing")
        try:
            cur.execute(
                """
                UPDATE ledger.balances
                SET status = COALESCE(%s, status),
                    overdraft_limit = COALESCE(%s, overdraft_limit),
                    updated_at = now()
                WHERE account_id = %s
                RETURNING *
                """,
                (status, overdraft_limit, account_id),
            )
        except psycopg.errors.CheckViolation as e:
            # e.g. lowering the overdraft below what is already used
            raise InsufficientFunds(
                "change would leave the account overdrawn", constraint=e.diag.constraint_name
            ) from e
        return cur.fetchone()


def get_balance(conn: psycopg.Connection, account_id: UUID) -> dict:
    with _cursor(conn) as cur:
        cur.execute(
            """
            SELECT account_id, kind, product_type, status, currency, ledger_balance,
                   available_balance, overdraft_limit, accrued_interest, updated_at
            FROM ledger.balances WHERE account_id = %s
            """,
            (account_id,),
        )
        row = cur.fetchone()
    if row is None:
        raise AccountNotFound("ledger account not found", account_id=str(account_id))
    return row


def list_entries(
    conn: psycopg.Connection, account_id: UUID, *, before_entry_id: int | None = None, limit: int = 50
) -> list[dict]:
    """Account history, newest first, keyset-paginated by entry_id."""
    with _cursor(conn) as cur:
        cur.execute(
            """
            SELECT e.entry_id, e.journal_id, e.amount, e.balance_after, e.created_at,
                   j.txn_type, j.description, j.business_date
            FROM ledger.entries e JOIN ledger.journals j USING (journal_id)
            WHERE e.account_id = %s AND (%s::bigint IS NULL OR e.entry_id < %s::bigint)
            ORDER BY e.entry_id DESC
            LIMIT %s
            """,
            (account_id, before_entry_id, before_entry_id, min(limit, 200)),
        )
        return cur.fetchall()


MAX_STATEMENT_ENTRIES = 5000


def statement(conn: psycopg.Connection, account_id: UUID, date_from: date, date_to: date) -> dict:
    """An account's entries for a range of business dates, oldest first, with the balance
    carried forward: opening balance, each entry with the balance after it, closing balance.

    Ordered by business date (then posting order), and the opening balance is the sum of
    everything dated earlier, so the figures are right even if an entry was posted late
    with an earlier business date.
    """
    if date_from > date_to:
        raise InvalidJournal("the statement period ends before it starts")
    get_balance(conn, account_id)  # AccountNotFound if unknown
    with _cursor(conn) as cur:
        cur.execute(
            "SELECT COALESCE(SUM(e.amount), 0)::bigint AS opening FROM ledger.entries e "
            "JOIN ledger.journals j USING (journal_id) WHERE e.account_id = %s AND j.business_date < %s",
            (account_id, date_from))
        opening = cur.fetchone()["opening"]
        cur.execute(
            """
            SELECT e.entry_id, e.journal_id, e.amount, j.txn_type, j.description, j.business_date
            FROM ledger.entries e JOIN ledger.journals j USING (journal_id)
            WHERE e.account_id = %s AND j.business_date BETWEEN %s AND %s
            ORDER BY j.business_date, e.entry_id
            LIMIT %s
            """,
            (account_id, date_from, date_to, MAX_STATEMENT_ENTRIES + 1))
        rows = cur.fetchall()
    if len(rows) > MAX_STATEMENT_ENTRIES:
        raise InvalidJournal(f"more than {MAX_STATEMENT_ENTRIES} entries in this period; choose a shorter one")
    balance = opening
    for row in rows:
        balance += row["amount"]
        row["balance"] = balance
    return {
        "account_id": account_id, "date_from": date_from, "date_to": date_to,
        "opening_balance": opening, "closing_balance": balance,
        "total_credits": sum(r["amount"] for r in rows if r["amount"] > 0),
        "total_debits": -sum(r["amount"] for r in rows if r["amount"] < 0),
        "entries": rows,
    }


# --------------------------------------------------------------------------- journals


def post_journal(
    conn: psycopg.Connection,
    *,
    idempotency_key: str,
    txn_type: str,
    legs: list[Leg],
    description: str | None = None,
    metadata: dict | None = None,
    business_date: date | None = None,
    capture_hold_id: UUID | None = None,
    reverses_journal_id: UUID | None = None,
) -> PostResult:
    """Post a balanced journal atomically. Retrying with the same key is safe."""
    if not idempotency_key or len(idempotency_key) > 200:
        raise InvalidJournal("idempotency_key is required (max 200 chars)")
    _validate_legs(legs)
    request_hash = _request_hash(txn_type, legs, capture_hold_id)

    with conn.transaction(), _cursor(conn) as cur:
        # 1. Claim the idempotency key. A concurrent request with the same key
        #    waits here until we commit, then sees our journal.
        try:
            cur.execute(
                """
                INSERT INTO ledger.journals
                    (idempotency_key, request_hash, txn_type, description, metadata,
                     business_date, reverses_journal_id)
                VALUES (%s, %s, %s, %s, %s, COALESCE(%s, CURRENT_DATE), %s)
                ON CONFLICT (idempotency_key) DO NOTHING
                RETURNING journal_id, created_at
                """,
                (
                    idempotency_key, request_hash, txn_type, description,
                    Jsonb(metadata or {}), business_date, reverses_journal_id,
                ),
            )
        except psycopg.errors.CheckViolation as e:
            raise InvalidJournal("invalid journal", constraint=e.diag.constraint_name) from e
        except psycopg.errors.UniqueViolation as e:
            raise IdempotencyConflict("journal has already been reversed") from e
        claimed = cur.fetchone()

        if claimed is None:
            cur.execute(
                "SELECT journal_id, request_hash, txn_type FROM ledger.journals WHERE idempotency_key = %s",
                (idempotency_key,),
            )
            existing = cur.fetchone()
            if existing["request_hash"] != request_hash:
                raise IdempotencyConflict(
                    "idempotency key was already used for a different request",
                    journal_id=str(existing["journal_id"]),
                )
            return PostResult(
                journal_id=existing["journal_id"],
                replayed=True,
                txn_type=existing["txn_type"],
                entries=_entries(cur, existing["journal_id"]),
            )

        journal_id = claimed["journal_id"]

        # 2. Lock every touched balance row, always in account_id order.
        accounts = _lock_balances(cur, [l.account_id for l in legs])

        # 3. Capturing a hold: put its amount back into available before the debit.
        if capture_hold_id is not None:
            cur.execute(
                "SELECT hold_id, account_id, amount, status FROM ledger.holds WHERE hold_id = %s FOR UPDATE",
                (capture_hold_id,),
            )
            hold = cur.fetchone()
            if hold is None or hold["status"] != "ACTIVE":
                raise HoldError("hold is not active", hold_id=str(capture_hold_id))
            debit = next((l for l in legs if l.account_id == hold["account_id"] and l.amount < 0), None)
            if debit is None or -debit.amount != hold["amount"]:
                raise HoldError("journal must debit exactly the held amount from the held account")
            accounts[hold["account_id"]]["available_balance"] += hold["amount"]
            cur.execute(
                """
                UPDATE ledger.holds SET status = 'CAPTURED', journal_id = %s, resolved_at = now()
                WHERE hold_id = %s
                """,
                (journal_id, capture_hold_id),
            )

        # 4. Check rules for every leg, then apply.
        for leg in legs:
            acct = accounts[leg.account_id]
            if acct["status"] == "CLOSED":
                raise AccountNotOpen("account is closed", account_id=str(leg.account_id))
            if leg.amount < 0:
                if acct["status"] != "OPEN":
                    raise AccountNotOpen("account cannot be debited", account_id=str(leg.account_id),
                                         status=acct["status"])
                if acct["kind"] == "CUSTOMER" and (
                    acct["available_balance"] + leg.amount + acct["overdraft_limit"] < 0
                ):
                    raise InsufficientFunds(
                        "insufficient funds",
                        account_id=str(leg.account_id),
                        available=acct["available_balance"] + acct["overdraft_limit"],
                        requested=-leg.amount,
                    )
            acct["ledger_balance"] += leg.amount
            acct["available_balance"] += leg.amount

        for leg in legs:
            acct = accounts[leg.account_id]
            cur.execute(
                """
                UPDATE ledger.balances
                SET ledger_balance = %s, available_balance = %s, updated_at = now()
                WHERE account_id = %s
                """,
                (acct["ledger_balance"], acct["available_balance"], leg.account_id),
            )
            cur.execute(
                """
                INSERT INTO ledger.entries (journal_id, account_id, amount, balance_after)
                VALUES (%s, %s, %s, %s)
                """,
                (journal_id, leg.account_id, leg.amount, acct["ledger_balance"]),
            )

        # 5. Outbox event, same transaction.
        cur.execute(
            """
            INSERT INTO ledger.outbox (topic, event_key, event_type, payload)
            VALUES (%s, %s, 'JournalPosted', %s)
            """,
            (
                JOURNAL_TOPIC,
                str(journal_id),
                Jsonb({
                    "journal_id": str(journal_id),
                    "idempotency_key": idempotency_key,
                    "txn_type": txn_type,
                    "legs": [{"account_id": str(l.account_id), "amount": l.amount} for l in legs],
                    "metadata": metadata or {},
                    "posted_at": claimed["created_at"].isoformat(),
                }),
            ),
        )
        entries = _entries(cur, journal_id)

    return PostResult(journal_id=journal_id, replayed=False, txn_type=txn_type, entries=entries)


def reverse_journal(conn: psycopg.Connection, journal_id: UUID, *, idempotency_key: str,
                    reason: str) -> PostResult:
    """Post the exact opposite of a journal (append-only correction)."""
    with _cursor(conn) as cur:
        cur.execute("SELECT txn_type FROM ledger.journals WHERE journal_id = %s", (journal_id,))
        original = cur.fetchone()
        if original is None:
            raise NotFound("journal not found", journal_id=str(journal_id))
        if original["txn_type"] == "REVERSAL":
            raise InvalidJournal("a reversal cannot be reversed")
        legs = [Leg(e["account_id"], -e["amount"]) for e in _entries(cur, journal_id)]
    return post_journal(
        conn,
        idempotency_key=idempotency_key,
        txn_type="REVERSAL",
        legs=legs,
        description=reason,
        metadata={"reverses": str(journal_id)},
        reverses_journal_id=journal_id,
    )


# --------------------------------------------------------------------------- holds


def create_hold(
    conn: psycopg.Connection,
    *,
    account_id: UUID,
    amount: int,
    idempotency_key: str,
    reason: str,
    expires_at: datetime | None = None,
) -> dict:
    """Reserve funds (e.g. a transaction held for fraud review)."""
    if not isinstance(amount, int) or amount <= 0:
        raise InvalidJournal("hold amount must be a positive integer (paise)")
    with conn.transaction(), _cursor(conn) as cur:
        # Lock first: holds on one account are serialised, so the check below
        # always sees a concurrent request's committed hold.
        acct = _lock_balances(cur, [account_id])[account_id]
        cur.execute("SELECT * FROM ledger.holds WHERE idempotency_key = %s", (idempotency_key,))
        existing = cur.fetchone()
        if existing:
            if existing["account_id"] != account_id or existing["amount"] != amount:
                raise IdempotencyConflict("idempotency key was already used for a different hold")
            return existing

        if acct["status"] != "OPEN":
            raise AccountNotOpen("account cannot be debited", status=acct["status"])
        if acct["kind"] == "CUSTOMER" and acct["available_balance"] - amount + acct["overdraft_limit"] < 0:
            raise InsufficientFunds("insufficient funds for hold",
                                    available=acct["available_balance"] + acct["overdraft_limit"],
                                    requested=amount)
        cur.execute(
            "UPDATE ledger.balances SET available_balance = available_balance - %s, updated_at = now() "
            "WHERE account_id = %s",
            (amount, account_id),
        )
        cur.execute(
            """
            INSERT INTO ledger.holds (account_id, amount, reason, idempotency_key, expires_at)
            VALUES (%s, %s, %s, %s, %s)
            ON CONFLICT (idempotency_key) DO NOTHING
            RETURNING *
            """,
            (account_id, amount, reason, idempotency_key, expires_at),
        )
        hold = cur.fetchone()
        if hold is None:
            # A concurrent request with the same key won the race; undo ours.
            raise IdempotencyConflict("concurrent hold with the same key, retry the request")
        cur.execute(
            "INSERT INTO ledger.outbox (topic, event_key, event_type, payload) "
            "VALUES (%s, %s, 'HoldCreated', %s)",
            (HOLD_TOPIC, str(account_id),
             Jsonb({"hold_id": str(hold["hold_id"]), "account_id": str(account_id),
                    "amount": amount, "reason": reason})),
        )
        return hold


def release_hold(conn: psycopg.Connection, hold_id: UUID, *, status: str = "RELEASED") -> dict:
    """Give reserved funds back. Idempotent: releasing twice is a no-op."""
    if status not in ("RELEASED", "EXPIRED"):
        raise InvalidJournal("status must be RELEASED or EXPIRED")
    with conn.transaction(), _cursor(conn) as cur:
        cur.execute("SELECT account_id FROM ledger.holds WHERE hold_id = %s", (hold_id,))
        found = cur.fetchone()
        if found is None:
            raise NotFound("hold not found", hold_id=str(hold_id))
        _lock_balances(cur, [found["account_id"]])  # lock order: balance, then hold
        cur.execute("SELECT * FROM ledger.holds WHERE hold_id = %s FOR UPDATE", (hold_id,))
        hold = cur.fetchone()
        if hold["status"] in ("RELEASED", "EXPIRED"):
            return hold
        if hold["status"] == "CAPTURED":
            raise HoldError("hold was already captured")
        cur.execute(
            "UPDATE ledger.balances SET available_balance = available_balance + %s, updated_at = now() "
            "WHERE account_id = %s",
            (hold["amount"], hold["account_id"]),
        )
        cur.execute(
            "UPDATE ledger.holds SET status = %s, resolved_at = now() WHERE hold_id = %s RETURNING *",
            (status, hold_id),
        )
        hold = cur.fetchone()
        cur.execute(
            "INSERT INTO ledger.outbox (topic, event_key, event_type, payload) "
            "VALUES (%s, %s, 'HoldReleased', %s)",
            (HOLD_TOPIC, str(hold["account_id"]),
             Jsonb({"hold_id": str(hold_id), "account_id": str(hold["account_id"]),
                    "amount": hold["amount"], "status": status})),
        )
        return hold


# --------------------------------------------------------------------------- savings interest


def accrue_savings_interest(conn: psycopg.Connection, *, business_date: date, annual_rate: str) -> dict:
    """Daily accrual on the end-of-day balance of every savings account.

    Interest builds up in `accrued_interest` (fractional paise) and is only
    rounded when it is posted. Idempotent per business date."""
    with conn.transaction(), _cursor(conn) as cur:
        cur.execute(
            "INSERT INTO ledger.accrual_runs (business_date, annual_rate, accounts) VALUES (%s, %s, 0) "
            "ON CONFLICT (business_date) DO NOTHING RETURNING business_date",
            (business_date, annual_rate),
        )
        if cur.fetchone() is None:
            cur.execute("SELECT accounts FROM ledger.accrual_runs WHERE business_date = %s", (business_date,))
            return {"business_date": business_date, "accounts": cur.fetchone()["accounts"], "replayed": True}
        cur.execute(
            """
            UPDATE ledger.balances
            SET accrued_interest = accrued_interest + ledger_balance * %s::numeric / 365
            WHERE kind = 'CUSTOMER' AND product_type = 'SAVINGS' AND status <> 'CLOSED'
              AND ledger_balance > 0
            """,
            (annual_rate,),
        )
        accounts = cur.rowcount
        cur.execute("UPDATE ledger.accrual_runs SET accounts = %s WHERE business_date = %s",
                    (accounts, business_date))
    return {"business_date": business_date, "accounts": accounts, "replayed": False}


def post_savings_interest(conn: psycopg.Connection, *, business_date: date) -> dict:
    """Credit accrued interest (whole paise) to each savings account. Idempotent per date."""
    from .internal_accounts import INTEREST_EXPENSE

    with _cursor(conn) as cur:
        cur.execute(
            "SELECT account_id, floor(accrued_interest)::bigint AS amount FROM ledger.balances "
            "WHERE kind = 'CUSTOMER' AND product_type = 'SAVINGS' AND accrued_interest >= 1 "
            "AND status <> 'CLOSED' ORDER BY account_id"
        )
        due = cur.fetchall()
    posted = total = 0
    for row in due:
        with conn.transaction():
            result = post_journal(
                conn,
                idempotency_key=f"sav-int:{row['account_id']}:{business_date.isoformat()}",
                txn_type="INTEREST",
                legs=[Leg(INTEREST_EXPENSE, -row["amount"]), Leg(row["account_id"], row["amount"])],
                description=f"Savings interest up to {business_date.isoformat()}",
                business_date=business_date,
            )
            if not result.replayed:
                conn.execute(
                    "UPDATE ledger.balances SET accrued_interest = accrued_interest - %s WHERE account_id = %s",
                    (row["amount"], row["account_id"]),
                )
                posted += 1
                total += row["amount"]
    return {"business_date": business_date, "accounts": posted, "total_paise": total}


def get_balances(conn: psycopg.Connection, account_ids: list[UUID]) -> list[dict]:
    with _cursor(conn) as cur:
        cur.execute(
            "SELECT account_id, status, product_type, ledger_balance, available_balance, overdraft_limit, "
            "accrued_interest FROM ledger.balances WHERE account_id = ANY(%s)",
            (account_ids,),
        )
        return cur.fetchall()


# --------------------------------------------------------------------------- reconciliation


def reconcile(conn: psycopg.Connection) -> dict:
    """Check the books. Shown as the 'Books balanced' tile on the ops dashboard."""
    with _cursor(conn) as cur:
        cur.execute("SELECT COALESCE(SUM(amount), 0) AS total FROM ledger.entries")
        total = cur.fetchone()["total"]
        cur.execute(
            """
            SELECT b.account_id, b.ledger_balance, COALESCE(SUM(e.amount), 0) AS entries_sum
            FROM ledger.balances b LEFT JOIN ledger.entries e USING (account_id)
            GROUP BY b.account_id, b.ledger_balance
            HAVING b.ledger_balance <> COALESCE(SUM(e.amount), 0)
            """
        )
        ledger_mismatches = cur.fetchall()
        cur.execute(
            """
            SELECT b.account_id, b.available_balance,
                   b.ledger_balance - COALESCE(SUM(h.amount), 0) AS expected_available
            FROM ledger.balances b
            LEFT JOIN ledger.holds h ON h.account_id = b.account_id AND h.status = 'ACTIVE'
            GROUP BY b.account_id, b.available_balance, b.ledger_balance
            HAVING b.available_balance <> b.ledger_balance - COALESCE(SUM(h.amount), 0)
            """
        )
        hold_mismatches = cur.fetchall()
    return {
        "balanced": total == 0 and not ledger_mismatches and not hold_mismatches,
        "entries_total": total,
        "ledger_mismatches": ledger_mismatches,
        "hold_mismatches": hold_mismatches,
    }
