"""Transaction Intake: the single entry point for customer payments.

One payment moves through these steps. Each is a layer of defence:

  1. Claim the client's Idempotency-Key            (a retried request is the same payment)
  2. Rate limit       customer, account, device, IP (Rate Limit Coordinator, gRPC)
  3. Authorise        holder of an active account?  (Account, REST)
  4. Resolve payee    account number or alias
  5. Policy           per-payment and new-payee limits
  6. Risk score       ALLOW / CHALLENGE / HOLD / BLOCK (Risk Detection, gRPC)
       CHALLENGE  -> customer confirms with an OTP, then the payment continues
       HOLD       -> funds are reserved and a fraud analyst approves or rejects
  7. Daily limits     checked and reserved under a lock
  8. Post             the money moves in the Ledger (gRPC), key "intake:<txn_id>"
  9. Record           the decision and an outbox event, in one transaction

If a dependency fails mid-way the row stays PROCESSING and the client retries
with the same key. The retry continues the same row, and the Ledger's own
idempotency key guarantees the money moves at most once.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import time
from dataclasses import dataclass
from typing import Any
from uuid import UUID

import psycopg
from prometheus_client import Counter
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from .clients import (
    AccountClient, DependencyUnavailable, IdentityClient, LedgerClient, LedgerRejected, RateLimiter,
    RiskClient,
)

log = logging.getLogger("intake")

TXN_TOPIC = "txn-events"
CASH_ACCOUNT = UUID("00000000-0000-0000-0000-000000000001")
NEW_PAYEE_LIMIT = int(os.getenv("NEW_PAYEE_LIMIT_PAISE", "1000000"))  # Rs 10,000
CHALLENGE_TTL_SECONDS = 600
IST_DAY_START = "(date_trunc('day', now() AT TIME ZONE 'Asia/Kolkata') AT TIME ZONE 'Asia/Kolkata')"

OUTCOMES = Counter("intake_payments_total", "Payment outcomes", ["status", "code"])

# Plain-language reasons shown to customers.
DECLINE_MESSAGES = {
    "RATE_LIMITED": "Too many attempts. Please wait a minute and try again.",
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
    "RISK_BLOCKED": "This payment was blocked to protect your account.",
    "CHALLENGE_FAILED": "The confirmation code was wrong too many times.",
    "CHALLENGE_EXPIRED": "The confirmation code expired. Please start the payment again.",
    "CUSTOMER_DENIED": "You told us you didn't make this payment. It was cancelled and the account frozen.",
    "FRAUD_CONFIRMED": "This payment was cancelled by our fraud team.",
}
WAITING_MESSAGES = {
    "CHALLENGE": ("OTP_REQUIRED", "We need to confirm it's you. Enter the code we sent you."),
    "HELD": ("UNDER_REVIEW", "This payment is being reviewed. The money is reserved and safe."),
}


class IntakeError(Exception):
    code = "INTAKE_ERROR"
    http_status = 400

    def __init__(self, message: str, **details: Any) -> None:
        super().__init__(message)
        self.details = details


class InvalidRequest(IntakeError):
    code = "INVALID_REQUEST"


class InvalidOtp(IntakeError):
    code = "INVALID_OTP"


class IdempotencyConflict(IntakeError):
    code = "IDEMPOTENCY_KEY_REUSED"
    http_status = 409


class WrongState(IntakeError):
    code = "WRONG_STATE"
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


@dataclass
class Deps:
    accounts: AccountClient
    ledger: LedgerClient
    limiter: RateLimiter
    risk: RiskClient
    identity: IdentityClient


def _cur(conn: psycopg.Connection) -> psycopg.Cursor:
    return conn.cursor(row_factory=dict_row)


def _hash(**fields) -> str:
    return hashlib.sha256(json.dumps(fields, sort_keys=True, default=str).encode()).hexdigest()


def present(row: dict) -> dict:
    out = {k: v for k, v in row.items() if k not in ("request_hash", "reserved", "limits", "ip")}
    out["decline_message"] = DECLINE_MESSAGES.get(row["decline_code"]) if row["decline_code"] else None
    out["next_action"], out["message"] = WAITING_MESSAGES.get(row["status"], (None, out["decline_message"]))
    return out


# ------------------------------------------------------------------ customer API


def submit_payment(
    conn: psycopg.Connection,
    deps: Deps,
    *,
    customer_id: UUID,
    idempotency_key: str,
    from_account_id: UUID,
    payee: str,
    amount: int,
    note: str | None = None,
    channel: str = "TRANSFER",
    device_id: str = "",
    ip: str = "",
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

    with conn.transaction(), _cur(conn) as cur:
        try:
            cur.execute(
                """
                INSERT INTO intake.transactions
                    (customer_id, idempotency_key, request_hash, channel, from_account_id,
                     payee_ref, amount, note, device_id, ip)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (customer_id, idempotency_key) DO NOTHING
                RETURNING *
                """,
                (customer_id, idempotency_key, request_hash, channel, from_account_id, payee, amount,
                 note, device_id or None, ip or None),
            )
        except psycopg.errors.CheckViolation as e:
            raise InvalidRequest("invalid payment", constraint=e.diag.constraint_name) from e
        txn = cur.fetchone()
        if txn is None:
            cur.execute(
                "SELECT * FROM intake.transactions WHERE customer_id = %s AND idempotency_key = %s",
                (customer_id, idempotency_key),
            )
            txn = cur.fetchone()
            if txn["request_hash"] != request_hash:
                raise IdempotencyConflict("Idempotency-Key was already used for a different payment",
                                          txn_id=str(txn["txn_id"]))
            if txn["status"] != "PROCESSING":
                return present(txn), True       # decided, or waiting for OTP / review
            # else: an earlier attempt didn't finish; continue it below
    return _advance(conn, deps, txn), False


def confirm_challenge(conn: psycopg.Connection, deps: Deps, txn_id: UUID, customer_id: UUID, otp: str) -> dict:
    """The customer answers a step-up challenge with the OTP they were sent."""
    txn = _load(conn, txn_id, customer_id)
    if txn["status"] in ("COMPLETED", "DECLINED", "HELD"):
        return present(txn)
    if txn["status"] == "CHALLENGE":
        if time.time() - txn["created_at"].timestamp() > CHALLENGE_TTL_SECONDS:
            return _set_status(conn, txn_id, "DECLINED", ("CHALLENGE",), decline_code="CHALLENGE_EXPIRED")
        try:
            result = deps.identity.verify_otp(customer_id, f"txn:{txn_id}", otp)
        except DependencyUnavailable as e:
            raise Pending("could not verify the code right now; please try again", reason=str(e)) from e
        if result == "LOCKED":
            return _set_status(conn, txn_id, "DECLINED", ("CHALLENGE",), decline_code="CHALLENGE_FAILED")
        if result != "OK":
            raise InvalidOtp("That code isn't right. Please try again.")
        with _cur(conn) as cur:
            cur.execute(
                "UPDATE intake.transactions SET status = 'PROCESSING', challenge_passed = true, "
                "updated_at = now() WHERE txn_id = %s AND status = 'CHALLENGE' RETURNING *",
                (txn_id,),
            )
            txn = cur.fetchone() or _load(conn, txn_id, customer_id)
    if txn["status"] != "PROCESSING":
        return present(txn)
    return _advance(conn, deps, txn)


def deny_payment(conn: psycopg.Connection, deps: Deps, txn_id: UUID, customer_id: UUID) -> dict:
    """'This wasn't me': cancel the payment and freeze the account."""
    txn = _load(conn, txn_id, customer_id)
    if txn["status"] not in ("CHALLENGE", "HELD"):
        raise WrongState("only a payment waiting for confirmation or review can be denied",
                         status=txn["status"])
    try:
        # Freeze first: if anything below fails, the account is already safe.
        deps.accounts.freeze(txn["from_account_id"], "customer reported an unauthorised payment",
                             f"customer:{customer_id}")
        if txn["hold_id"]:
            deps.ledger.release_hold(txn["hold_id"])
    except DependencyUnavailable as e:
        raise Pending("could not complete right now; please try again", reason=str(e)) from e
    return _set_status(conn, txn_id, "DECLINED", ("CHALLENGE", "HELD"), decline_code="CUSTOMER_DENIED")


def get_transaction(conn: psycopg.Connection, txn_id: UUID, customer_id: UUID | None) -> dict:
    return present(_load(conn, txn_id, customer_id))


def list_transactions(conn: psycopg.Connection, customer_id: UUID, limit: int = 50) -> list[dict]:
    with _cur(conn) as cur:
        cur.execute(
            "SELECT * FROM intake.transactions WHERE customer_id = %s ORDER BY created_at DESC LIMIT %s",
            (customer_id, min(limit, 200)),
        )
        return [present(r) for r in cur.fetchall()]


# ------------------------------------------------------------------ staff API (fraud review, teller)


def release_held(conn: psycopg.Connection, deps: Deps, txn_id: UUID, actor: str) -> dict:
    """A fraud analyst approves a held payment: it is posted against its hold."""
    with _cur(conn) as cur:
        cur.execute(
            "UPDATE intake.transactions SET status = 'PROCESSING', review = 'APPROVED', reviewed_by = %s, "
            "updated_at = now() WHERE txn_id = %s AND status = 'HELD' RETURNING *",
            (actor, txn_id),
        )
        txn = cur.fetchone() or _load(conn, txn_id, None)
    if txn["status"] != "PROCESSING":
        if txn["status"] == "COMPLETED":
            return present(txn)
        raise WrongState("payment is not held", status=txn["status"])
    return _advance(conn, deps, txn)


def cancel_held(conn: psycopg.Connection, deps: Deps, txn_id: UUID, actor: str,
                code: str = "FRAUD_CONFIRMED") -> dict:
    """A fraud analyst rejects a held payment: the reserved funds are given back."""
    txn = _load(conn, txn_id, None)
    if txn["status"] == "DECLINED":
        return present(txn)
    if txn["status"] != "HELD":
        raise WrongState("payment is not held", status=txn["status"])
    try:
        deps.ledger.release_hold(txn["hold_id"])
    except DependencyUnavailable as e:
        raise Pending("ledger unavailable; try again", reason=str(e)) from e
    return _set_status(conn, txn_id, "DECLINED", ("HELD",), decline_code=code, review="REJECTED",
                       reviewed_by=actor)


def list_waiting(conn: psycopg.Connection, status: str = "HELD", limit: int = 100) -> list[dict]:
    with _cur(conn) as cur:
        cur.execute("SELECT * FROM intake.transactions WHERE status = %s ORDER BY created_at LIMIT %s",
                    (status, min(limit, 500)))
        return [present(r) for r in cur.fetchall()]


def cash_deposit(deps: Deps, *, account_number: str, amount: int, idempotency_key: str, actor: str) -> dict:
    """Simulated cash paid in at a branch: money enters from the bank's CASH account."""
    if not isinstance(amount, int) or amount <= 0:
        raise InvalidRequest("amount must be a positive integer in paise")
    try:
        account = deps.accounts.lookup_number(account_number)
        if account is None:
            raise NotFound("account not found")
        journal_id = deps.ledger.post_journal(
            idempotency_key=f"cash:{idempotency_key}", txn_type="DEPOSIT",
            legs=[(CASH_ACCOUNT, -amount), (UUID(str(account["account_id"])), amount)],
            description="Cash deposit", metadata={"actor": actor})
    except LedgerRejected as r:
        raise InvalidRequest(r.message, code=r.code) from r
    except DependencyUnavailable as e:
        raise Pending("a downstream service is unavailable; retry with the same Idempotency-Key") from e
    return {"journal_id": journal_id, "account_id": account["account_id"], "amount": amount}


# ------------------------------------------------------------------ the pipeline


def _load(conn: psycopg.Connection, txn_id: UUID, customer_id: UUID | None) -> dict:
    with _cur(conn) as cur:
        cur.execute("SELECT * FROM intake.transactions WHERE txn_id = %s", (txn_id,))
        row = cur.fetchone()
    if row is None or (customer_id is not None and row["customer_id"] != customer_id):
        raise NotFound("transaction not found")
    return row


def _advance(conn: psycopg.Connection, deps: Deps, txn: dict) -> dict:
    """Drive a PROCESSING payment as far as it can go."""
    txn_id = txn["txn_id"]
    try:
        if txn["risk_decision"] is None:
            txn = _screen(conn, deps, txn)
            if txn["status"] != "PROCESSING":
                return present(txn)                       # now waiting for an OTP
        if not txn["reserved"]:
            txn = _reserve(conn, txn)
            if txn["status"] != "PROCESSING":
                return present(txn)                       # a concurrent retry got further
        if txn["risk_decision"] == "HOLD" and txn["review"] is None:
            hold_id = deps.ledger.create_hold(
                idempotency_key=f"intake-hold:{txn_id}", account_id=txn["from_account_id"],
                amount=txn["amount"], reason="fraud review")
            return _set_status(conn, txn_id, "HELD", ("PROCESSING",), hold_id=hold_id)
        journal_id = deps.ledger.post_journal(
            idempotency_key=f"intake:{txn_id}",
            txn_type=txn["channel"],
            legs=[(txn["from_account_id"], -txn["amount"]), (txn["to_account_id"], txn["amount"])],
            description=txn["note"],
            metadata={"txn_id": str(txn_id), "customer_id": str(txn["customer_id"]), "channel": txn["channel"]},
            capture_hold_id=txn["hold_id"] if txn["review"] == "APPROVED" else None,
        )
    except _Decline as d:
        return _set_status(conn, txn_id, "DECLINED", ("PROCESSING",), decline_code=d.code)
    except LedgerRejected as r:
        if txn.get("hold_id"):
            try:
                deps.ledger.release_hold(txn["hold_id"])
            except (DependencyUnavailable, LedgerRejected):
                log.warning("could not release hold %s for declined txn %s", txn["hold_id"], txn_id)
        return _set_status(conn, txn_id, "DECLINED", ("PROCESSING",), decline_code=r.code)
    except DependencyUnavailable as e:
        raise Pending("a downstream service is unavailable; retry with the same Idempotency-Key",
                      txn_id=str(txn_id), reason=str(e)) from e
    return _set_status(conn, txn_id, "COMPLETED", ("PROCESSING",), journal_id=journal_id)


def _screen(conn: psycopg.Connection, deps: Deps, txn: dict) -> dict:
    """Steps 2-6: rate limit, authorise, payee, policy, risk. Raises _Decline."""
    customer, account = str(txn["customer_id"]), str(txn["from_account_id"])

    limit = deps.limiter.check(customer_id=customer, account_id=account, device_id=txn["device_id"] or "",
                               ip=txn["ip"] or "", amount=txn["amount"])
    if not limit.allowed:
        raise _Decline("RATE_LIMITED")

    pre = deps.accounts.precheck(txn["customer_id"], txn["from_account_id"], txn["payee_ref"])
    auth = pre["auth"]
    if not auth["allowed"]:
        raise _Decline(auth["reason"])
    if pre.get("payee_error"):
        raise _Decline(pre["payee_error"])
    payee = pre["payee"]
    if not payee.get("can_receive", False):
        raise _Decline("PAYEE_CANNOT_RECEIVE")
    if str(payee["account_id"]) == account:
        raise _Decline("SAME_ACCOUNT")
    if txn["amount"] > auth["per_txn_limit"]:
        raise _Decline("PER_TXN_LIMIT_EXCEEDED")
    if txn["amount"] > NEW_PAYEE_LIMIT and not pre["payee_trusted"]:
        raise _Decline("NEW_PAYEE_LIMIT_EXCEEDED")

    risk = deps.risk.evaluate(
        txn_id=str(txn["txn_id"]), customer_id=customer, from_account_id=account,
        to_account_id=str(payee["account_id"]), amount=txn["amount"], channel=txn["channel"],
        device_id=txn["device_id"] or "", ip=txn["ip"] or "", payee_trusted=bool(pre["payee_trusted"]),
        account_idle_days=int(auth.get("idle_days") or 0))

    with _cur(conn) as cur:
        cur.execute(
            """
            UPDATE intake.transactions
            SET to_account_id = %s, to_account_number = %s, to_display_name = %s, payee_trusted = %s,
                limits = %s, risk_decision = %s, risk_score = %s, risk_reasons = %s, updated_at = now()
            WHERE txn_id = %s AND status = 'PROCESSING'
            RETURNING *
            """,
            (payee["account_id"], payee["account_number"], payee["display_name"], pre["payee_trusted"],
             Jsonb({"daily_limit": auth["daily_limit"], "customer_daily_limit": auth["daily_transfer_limit"]}),
             risk.decision, risk.score, Jsonb(risk.reasons), txn["txn_id"]),
        )
        saved = cur.fetchone()
    if saved is None:
        return _load(conn, txn["txn_id"], None)           # a concurrent retry already moved it on

    if risk.decision == "BLOCK":
        raise _Decline("RISK_BLOCKED")
    if risk.decision == "CHALLENGE":
        deps.identity.issue_otp(
            txn["customer_id"], f"txn:{txn['txn_id']}",
            f"Confirm payment of Rs {txn['amount'] / 100:,.2f} to {payee['display_name']}")
        return _set_status(conn, txn["txn_id"], "CHALLENGE", ("PROCESSING",), raw=True)
    return saved


def _reserve(conn: psycopg.Connection, txn: dict) -> dict:
    """Step 7: daily-limit check and reservation, serialised per customer and per account."""
    limits = txn["limits"] or {}
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
              AND reserved AND status <> 'DECLINED'
              AND txn_id <> %(txn)s
              AND created_at >= {IST_DAY_START}
            """,
            {"acct": txn["from_account_id"], "cust": txn["customer_id"], "txn": txn["txn_id"]},
        )
        used = cur.fetchone()
        if used["account_used"] + txn["amount"] > limits.get("daily_limit", 0):
            raise _Decline("DAILY_LIMIT_EXCEEDED")
        if used["customer_used"] + txn["amount"] > limits.get("customer_daily_limit", 0):
            raise _Decline("CUSTOMER_DAILY_LIMIT_EXCEEDED")
        cur.execute(
            "UPDATE intake.transactions SET reserved = true, updated_at = now() "
            "WHERE txn_id = %s AND status = 'PROCESSING' RETURNING *",
            (txn["txn_id"],),
        )
        row = cur.fetchone()
    return row or _load(conn, txn["txn_id"], None)


_EVENT_TYPES = {"COMPLETED": "TransactionCompleted", "DECLINED": "TransactionDeclined",
                "HELD": "TransactionHeld", "CHALLENGE": "TransactionChallenged"}


def _set_status(conn: psycopg.Connection, txn_id: UUID, status: str, from_statuses: tuple[str, ...], *,
                decline_code: str | None = None, journal_id: str | None = None,
                hold_id: str | None = None, review: str | None = None, reviewed_by: str | None = None,
                raw: bool = False) -> dict:
    """Move a payment to a new status exactly once, and emit its event in the same
    transaction. A concurrent retry that loses the race just reads the result."""
    final = status in ("COMPLETED", "DECLINED")
    with conn.transaction(), _cur(conn) as cur:
        cur.execute(
            """
            UPDATE intake.transactions
            SET status = %s,
                decline_code = COALESCE(%s, decline_code),
                journal_id = COALESCE(%s, journal_id),
                hold_id = COALESCE(%s, hold_id),
                review = COALESCE(%s, review),
                reviewed_by = COALESCE(%s, reviewed_by),
                decided_at = CASE WHEN %s THEN now() ELSE NULL END,
                updated_at = now()
            WHERE txn_id = %s AND status = ANY(%s)
            RETURNING *
            """,
            (status, decline_code, journal_id, hold_id, review, reviewed_by, final, txn_id,
             list(from_statuses)),
        )
        row = cur.fetchone()
        if row is None:
            cur.execute("SELECT * FROM intake.transactions WHERE txn_id = %s", (txn_id,))
            row = cur.fetchone()
            return row if raw else present(row)
        cur.execute(
            "INSERT INTO intake.outbox (topic, event_key, event_type, payload) VALUES (%s, %s, %s, %s)",
            (
                TXN_TOPIC,
                str(row["from_account_id"]),  # partition by account: per-account ordering for the stream job
                _EVENT_TYPES[status],
                Jsonb({
                    "txn_id": str(row["txn_id"]),
                    "customer_id": str(row["customer_id"]),
                    "from_account_id": str(row["from_account_id"]),
                    "to_account_id": str(row["to_account_id"]) if row["to_account_id"] else None,
                    "amount": row["amount"],
                    "channel": row["channel"],
                    "status": status,
                    "decline_code": row["decline_code"],
                    "risk_decision": row["risk_decision"],
                    "risk_score": row["risk_score"],
                    "risk_reasons": row["risk_reasons"],
                    "device_id": row["device_id"],
                    "ip": row["ip"],
                    "journal_id": str(row["journal_id"]) if row["journal_id"] else None,
                    "event_time_ms": int(time.time() * 1000),
                }),
            ),
        )
    OUTCOMES.labels(status, row["decline_code"] or "").inc()
    return row if raw else present(row)
