"""Transaction Intake: the single entry point for customer payments.

Flow for one payment:
  1. Claim the client's Idempotency-Key (a row in PROCESSING).
  2. Authorise: is the customer an active holder of an active, debitable account?
  3. Resolve the payee (account number with check digit, or alias).
  4. Policy checks: per-transaction limit, new-payee limit.
     (Rate limiting and risk scoring plug in here later.)
  5. Daily limits, under a lock, then mark the row as reserved.
  6. Post to the Ledger over gRPC with key "intake:<txn_id>".
  7. Record the decision and an outbox event in one transaction.

If a dependency fails mid-way, the row stays PROCESSING and the client retries
with the same key. A retry re-drives the same row; the Ledger's own idempotency
key guarantees the money moves at most once.
"""

from __future__ import annotations

import hashlib
import json
import os
from typing import Any
from uuid import UUID

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from .clients import (
    AccountClient, BadRequest, DependencyUnavailable, LedgerClient, LedgerRejected, NotFoundError,
)

TXN_TOPIC = "txn-events"
NEW_PAYEE_LIMIT = int(os.getenv("NEW_PAYEE_LIMIT_PAISE", "1000000"))  # Rs 10,000
IST_DAY_START = "(date_trunc('day', now() AT TIME ZONE 'Asia/Kolkata') AT TIME ZONE 'Asia/Kolkata')"

# Plain-language reasons shown to customers.
DECLINE_MESSAGES = {
    "NOT_A_HOLDER": "You are not a holder of this account.",
    "CUSTOMER_FROZEN": "Your profile is frozen. Please contact the bank.",
    "CUSTOMER_CLOSED": "Your profile is closed.",
    "ACCOUNT_FROZEN": "This account is frozen.",
    "ACCOUNT_PENDING_KYC": "This account is waiting for KYC verification.",
    "ACCOUNT_DORMANT": "This account is dormant. Please reactivate it first.",
    "ACCOUNT_CLOSED": "This account is closed.",
    "ACCOUNT_NOT_OPEN": "This account cannot send money right now.",
    "DEPOSIT_ACCOUNT_NOT_DEBITABLE": "Payments can't be made directly from a fixed or recurring deposit.",
    "NEEDS_ALL_HOLDERS": "All holders must approve payments from this account.",
    "INVALID_PAYEE": "The payee account number is invalid. Please check for typos.",
    "PAYEE_NOT_FOUND": "We couldn't find that payee.",
    "PAYEE_CANNOT_RECEIVE": "The payee account cannot receive money.",
    "SAME_ACCOUNT": "You can't send money to the same account.",
    "PER_TXN_LIMIT_EXCEEDED": "This amount is above the per-transaction limit for this account.",
    "NEW_PAYEE_LIMIT_EXCEEDED": "New payees can receive at most Rs 10,000 per payment for the first 24 hours.",
    "DAILY_LIMIT_EXCEEDED": "This payment would exceed today's limit for this account.",
    "CUSTOMER_DAILY_LIMIT_EXCEEDED": "This payment would exceed your daily transfer limit.",
    "INSUFFICIENT_FUNDS": "Insufficient balance.",
}


class IntakeError(Exception):
    code = "INTAKE_ERROR"
    http_status = 400

    def __init__(self, message: str, **details: Any) -> None:
        super().__init__(message)
        self.details = details


class InvalidRequest(IntakeError):
    code = "INVALID_REQUEST"


class IdempotencyConflict(IntakeError):
    code = "IDEMPOTENCY_KEY_REUSED"
    http_status = 409


class NotFound(IntakeError):
    code = "NOT_FOUND"
    http_status = 404


class Pending(IntakeError):
    """Outcome unknown because a dependency is down; retry with the same key."""

    code = "PENDING_RETRY"
    http_status = 503


class _Decline(Exception):
    def __init__(self, code: str) -> None:
        self.code = code


def _cur(conn: psycopg.Connection) -> psycopg.Cursor:
    return conn.cursor(row_factory=dict_row)


def _hash(**fields) -> str:
    return hashlib.sha256(json.dumps(fields, sort_keys=True, default=str).encode()).hexdigest()


# ------------------------------------------------------------------ public API


def submit_payment(
    conn: psycopg.Connection,
    accounts: AccountClient,
    ledger: LedgerClient,
    *,
    customer_id: UUID,
    idempotency_key: str,
    from_account_id: UUID,
    payee: str,
    amount: int,
    note: str | None = None,
    channel: str = "TRANSFER",
) -> tuple[dict, bool]:
    """Returns (transaction, replayed)."""
    if not idempotency_key or len(idempotency_key) > 200:
        raise InvalidRequest("Idempotency-Key header is required (max 200 chars)")
    if not isinstance(amount, int) or isinstance(amount, bool) or amount <= 0:
        raise InvalidRequest("amount must be a positive integer in paise")
    if channel not in ("TRANSFER", "QR_PAYMENT"):
        raise InvalidRequest("channel must be TRANSFER or QR_PAYMENT")
    payee = payee.strip().lower()
    request_hash = _hash(channel=channel, from_account=str(from_account_id), payee=payee,
                         amount=amount, note=note)

    txn, replayed = _claim(conn, customer_id, idempotency_key, request_hash, channel,
                           from_account_id, payee, amount, note)
    if replayed:
        return txn, True
    return _process(conn, accounts, ledger, txn), False


def get_transaction(conn: psycopg.Connection, txn_id: UUID, customer_id: UUID) -> dict:
    with _cur(conn) as cur:
        cur.execute("SELECT * FROM intake.transactions WHERE txn_id = %s AND customer_id = %s",
                    (txn_id, customer_id))
        row = cur.fetchone()
    if row is None:
        raise NotFound("transaction not found")
    return present(row)


def list_transactions(conn: psycopg.Connection, customer_id: UUID, limit: int = 50) -> list[dict]:
    with _cur(conn) as cur:
        cur.execute(
            "SELECT * FROM intake.transactions WHERE customer_id = %s ORDER BY created_at DESC LIMIT %s",
            (customer_id, min(limit, 200)),
        )
        return [present(r) for r in cur.fetchall()]


def present(row: dict) -> dict:
    out = {k: v for k, v in row.items() if k not in ("request_hash", "reserved")}
    out["decline_message"] = DECLINE_MESSAGES.get(row["decline_code"]) if row["decline_code"] else None
    return out


# ------------------------------------------------------------------ steps


def _claim(conn, customer_id, idempotency_key, request_hash, channel, from_account_id, payee,
           amount, note) -> tuple[dict, bool]:
    with conn.transaction(), _cur(conn) as cur:
        try:
            cur.execute(
                """
                INSERT INTO intake.transactions
                    (customer_id, idempotency_key, request_hash, channel, from_account_id,
                     payee_ref, amount, note)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (customer_id, idempotency_key) DO NOTHING
                RETURNING *
                """,
                (customer_id, idempotency_key, request_hash, channel, from_account_id, payee,
                 amount, note),
            )
        except psycopg.errors.CheckViolation as e:
            raise InvalidRequest("invalid payment", constraint=e.diag.constraint_name) from e
        row = cur.fetchone()
        if row is not None:
            return row, False
        cur.execute(
            "SELECT * FROM intake.transactions WHERE customer_id = %s AND idempotency_key = %s",
            (customer_id, idempotency_key),
        )
        existing = cur.fetchone()
    if existing["request_hash"] != request_hash:
        raise IdempotencyConflict("Idempotency-Key was already used for a different payment",
                                  txn_id=str(existing["txn_id"]))
    if existing["status"] == "PROCESSING":
        return existing, False  # earlier attempt didn't finish: re-drive it
    return present(existing), True


def _process(conn, accounts: AccountClient, ledger: LedgerClient, txn: dict) -> dict:
    try:
        if not txn["reserved"]:
            payee = _checks(accounts, txn)
            txn = _reserve(conn, txn, payee)
        journal_id = ledger.post_transfer(
            idempotency_key=f"intake:{txn['txn_id']}",
            txn_type=txn["channel"],
            from_account=txn["from_account_id"],
            to_account=txn["to_account_id"],
            amount=txn["amount"],
            description=txn["note"],
            metadata={"txn_id": str(txn["txn_id"]), "customer_id": str(txn["customer_id"]),
                      "channel": txn["channel"]},
        )
    except _AlreadyDecided:
        return _read(conn, txn["txn_id"])
    except _Decline as d:
        return _finish(conn, txn["txn_id"], "DECLINED", decline_code=d.code)
    except LedgerRejected as r:
        return _finish(conn, txn["txn_id"], "DECLINED", decline_code=r.code)
    except DependencyUnavailable as e:
        raise Pending("a downstream service is unavailable; retry with the same Idempotency-Key",
                      txn_id=str(txn["txn_id"]), reason=str(e)) from e
    return _finish(conn, txn["txn_id"], "COMPLETED", journal_id=journal_id)


def _checks(accounts: AccountClient, txn: dict) -> dict:
    """Authorisation, payee resolution and per-payment policy. Raises _Decline."""
    auth = accounts.authorize(txn["from_account_id"], txn["customer_id"])
    if not auth["allowed"]:
        raise _Decline(auth["reason"])

    ref = txn["payee_ref"]
    try:
        payee = accounts.resolve_alias(ref) if "@" in ref else accounts.lookup_number(ref)
    except BadRequest:
        raise _Decline("INVALID_PAYEE")
    except NotFoundError:
        raise _Decline("PAYEE_NOT_FOUND")
    if not payee.get("can_receive", False):
        raise _Decline("PAYEE_CANNOT_RECEIVE")
    if str(payee["account_id"]) == str(txn["from_account_id"]):
        raise _Decline("SAME_ACCOUNT")

    if txn["amount"] > auth["per_txn_limit"]:
        raise _Decline("PER_TXN_LIMIT_EXCEEDED")

    if txn["amount"] > NEW_PAYEE_LIMIT and not _trusted_payee(accounts, txn["customer_id"], payee):
        raise _Decline("NEW_PAYEE_LIMIT_EXCEEDED")

    return {**payee, "daily_limit": auth["daily_limit"],
            "customer_daily_limit": auth["daily_transfer_limit"]}


def _trusted_payee(accounts: AccountClient, customer_id: UUID, payee: dict) -> bool:
    """Own accounts and saved payees past their cooling period are trusted."""
    own = {str(a["account_id"]) for a in accounts.customer_accounts(customer_id)}
    if str(payee["account_id"]) in own:
        return True
    return any(
        b["payee_account_number"] == payee["account_number"] and not b["in_cooling_period"]
        for b in accounts.beneficiaries(customer_id)
    )


def _reserve(conn, txn: dict, payee: dict) -> dict:
    """Daily-limit check and reservation, serialised per customer and per account."""
    with conn.transaction(), _cur(conn) as cur:
        # Fixed lock order (customer, then account) so two locks never deadlock.
        cur.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))", (f"cust:{txn['customer_id']}",))
        cur.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))", (f"acct:{txn['from_account_id']}",))
        cur.execute(
            f"""
            SELECT
              COALESCE(SUM(amount) FILTER (WHERE from_account_id = %(acct)s), 0) AS account_used,
              COALESCE(SUM(amount) FILTER (WHERE customer_id = %(cust)s), 0)     AS customer_used
            FROM intake.transactions
            WHERE (from_account_id = %(acct)s OR customer_id = %(cust)s)
              AND txn_id <> %(txn)s
              AND created_at >= {IST_DAY_START}
              AND (status = 'COMPLETED' OR (status = 'PROCESSING' AND reserved))
            """,
            {"acct": txn["from_account_id"], "cust": txn["customer_id"], "txn": txn["txn_id"]},
        )
        used = cur.fetchone()
        if used["account_used"] + txn["amount"] > payee["daily_limit"]:
            raise _Decline("DAILY_LIMIT_EXCEEDED")
        if used["customer_used"] + txn["amount"] > payee["customer_daily_limit"]:
            raise _Decline("CUSTOMER_DAILY_LIMIT_EXCEEDED")
        cur.execute(
            """
            UPDATE intake.transactions
            SET reserved = true, to_account_id = %s, to_account_number = %s, to_display_name = %s,
                updated_at = now()
            WHERE txn_id = %s AND status = 'PROCESSING'
            RETURNING *
            """,
            (payee["account_id"], payee["account_number"], payee["display_name"], txn["txn_id"]),
        )
        row = cur.fetchone()
    if row is None:  # a concurrent retry already finished this transaction
        raise _AlreadyDecided()
    return row


class _AlreadyDecided(Exception):
    pass


def _read(conn, txn_id: UUID) -> dict:
    with _cur(conn) as cur:
        cur.execute("SELECT * FROM intake.transactions WHERE txn_id = %s", (txn_id,))
        return present(cur.fetchone())


def _finish(conn, txn_id: UUID, status: str, *, decline_code: str | None = None,
            journal_id: str | None = None) -> dict:
    """Record the final decision once. A concurrent retry that loses the race just reads it."""
    with conn.transaction(), _cur(conn) as cur:
        cur.execute(
            """
            UPDATE intake.transactions
            SET status = %s, decline_code = %s, journal_id = %s, decided_at = now(), updated_at = now()
            WHERE txn_id = %s AND status = 'PROCESSING'
            RETURNING *
            """,
            (status, decline_code, journal_id, txn_id),
        )
        row = cur.fetchone()
        if row is None:
            cur.execute("SELECT * FROM intake.transactions WHERE txn_id = %s", (txn_id,))
            return present(cur.fetchone())
        cur.execute(
            "INSERT INTO intake.outbox (topic, event_key, event_type, payload) VALUES (%s, %s, %s, %s)",
            (
                TXN_TOPIC,
                str(row["from_account_id"]),  # partition by account: per-account ordering for Flink
                "TransactionCompleted" if status == "COMPLETED" else "TransactionDeclined",
                Jsonb({
                    "txn_id": str(row["txn_id"]),
                    "customer_id": str(row["customer_id"]),
                    "from_account_id": str(row["from_account_id"]),
                    "to_account_id": str(row["to_account_id"]) if row["to_account_id"] else None,
                    "amount": row["amount"],
                    "channel": row["channel"],
                    "status": status,
                    "decline_code": decline_code,
                    "journal_id": journal_id,
                    "created_at": row["created_at"].isoformat(),
                    "decided_at": row["decided_at"].isoformat(),
                }),
            ),
        )
        return present(row)
