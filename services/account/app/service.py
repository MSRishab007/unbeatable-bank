"""Account service business logic.

Cross-service ordering is chosen so that a failure always leaves the system
in the SAFE state (money blocked rather than wrongly allowed):
  - open:     ledger account created first (blocked if KYC pending), then the
              account row. An orphan ledger row with zero balance is harmless.
  - freeze:   ledger blocked first, then status updated.
  - unfreeze: status updated first, then ledger unblocked.
"""

from __future__ import annotations

import hashlib
import json
from datetime import date, datetime, timedelta, timezone
from typing import Any
from uuid import UUID, uuid4

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from .ledger_client import LedgerClient
from .numbers import PAN_RE, hash_pan, is_valid_account_number, make_account_number

MAX_HOLDERS = 4
BENEFICIARY_COOLING = timedelta(hours=24)


class AccountError(Exception):
    code = "ACCOUNT_ERROR"
    http_status = 400

    def __init__(self, message: str, **details: Any) -> None:
        super().__init__(message)
        self.details = details


class NotFound(AccountError):
    code = "NOT_FOUND"
    http_status = 404


class Conflict(AccountError):
    code = "CONFLICT"
    http_status = 409


class Forbidden(AccountError):
    code = "FORBIDDEN"
    http_status = 403


def _cur(conn: psycopg.Connection) -> psycopg.Cursor:
    return conn.cursor(row_factory=dict_row)


# ------------------------------------------------------------------- event chain


def _event_hash(prev_hash: str | None, account_id: UUID, event_type: str, payload: dict, actor: str) -> str:
    body = json.dumps(
        {"prev": prev_hash, "account": str(account_id), "type": event_type, "payload": payload, "actor": actor},
        sort_keys=True, default=str,
    )
    return hashlib.sha256(body.encode()).hexdigest()


def _record_event(cur: psycopg.Cursor, account_id: UUID, event_type: str, payload: dict, actor: str) -> None:
    """Caller must hold the account row lock, so the chain can't fork."""
    cur.execute(
        "SELECT hash FROM account.account_events WHERE account_id = %s ORDER BY event_id DESC LIMIT 1",
        (account_id,),
    )
    prev = cur.fetchone()
    prev_hash = prev["hash"] if prev else None
    h = _event_hash(prev_hash, account_id, event_type, payload, actor)
    cur.execute(
        """
        INSERT INTO account.account_events (account_id, event_type, payload, actor, prev_hash, hash)
        VALUES (%s, %s, %s, %s, %s, %s)
        """,
        (account_id, event_type, Jsonb(payload), actor, prev_hash, h),
    )
    cur.execute(
        "INSERT INTO account.outbox (topic, event_key, event_type, payload) VALUES (%s, %s, %s, %s)",
        ("account.events", str(account_id), event_type,
         Jsonb({"account_id": str(account_id), "actor": actor, **payload})),
    )


def verify_event_chain(conn: psycopg.Connection, account_id: UUID) -> bool:
    with _cur(conn) as cur:
        cur.execute(
            "SELECT * FROM account.account_events WHERE account_id = %s ORDER BY event_id", (account_id,)
        )
        prev_hash = None
        for e in cur.fetchall():
            expected = _event_hash(prev_hash, account_id, e["event_type"], e["payload"], e["actor"])
            if e["prev_hash"] != prev_hash or e["hash"] != expected:
                return False
            prev_hash = e["hash"]
    return True


# ------------------------------------------------------------------- customers


def create_customer(
    conn: psycopg.Connection, *, full_name: str, date_of_birth: date, phone: str, email: str,
    pan: str | None = None,
) -> dict:
    pan_hash = pan_last4 = None
    if pan:
        pan = pan.strip().upper()
        if not PAN_RE.match(pan):
            raise AccountError("invalid PAN format")
        pan_hash, pan_last4 = hash_pan(pan), pan[-5:]
    try:
        with conn.transaction(), _cur(conn) as cur:
            cur.execute(
                """
                INSERT INTO account.customers (full_name, date_of_birth, phone, email, pan_hash, pan_last4)
                VALUES (%s, %s, %s, %s, %s, %s) RETURNING *
                """,
                (full_name.strip(), date_of_birth, phone, email.strip().lower(), pan_hash, pan_last4),
            )
            return _public_customer(cur.fetchone())
    except psycopg.errors.UniqueViolation as e:
        field = {"customers_phone_key": "phone", "customers_email_key": "email",
                 "customers_pan_hash_key": "pan"}.get(e.diag.constraint_name, "unknown")
        raise Conflict(f"a customer with this {field} already exists", field=field) from e
    except psycopg.errors.CheckViolation as e:
        raise AccountError("invalid customer details", constraint=e.diag.constraint_name) from e


def _public_customer(row: dict) -> dict:
    row = dict(row)
    row.pop("pan_hash", None)
    return row


def get_customer(conn: psycopg.Connection, customer_id: UUID) -> dict:
    with _cur(conn) as cur:
        cur.execute("SELECT * FROM account.customers WHERE customer_id = %s", (customer_id,))
        row = cur.fetchone()
    if row is None:
        raise NotFound("customer not found")
    return _public_customer(row)


def verify_kyc(conn: psycopg.Connection, ledger: LedgerClient, customer_id: UUID, actor: str) -> dict:
    """Mark KYC verified and activate accounts where every holder is now verified."""
    with conn.transaction(), _cur(conn) as cur:
        cur.execute(
            "UPDATE account.customers SET kyc_status = 'VERIFIED' WHERE customer_id = %s RETURNING *",
            (customer_id,),
        )
        customer = cur.fetchone()
        if customer is None:
            raise NotFound("customer not found")
        cur.execute(
            """
            SELECT a.account_id FROM account.accounts a
            JOIN account.account_holders h ON h.account_id = a.account_id AND h.removed_at IS NULL
            WHERE h.customer_id = %s AND a.status = 'PENDING_KYC'
              AND NOT EXISTS (
                  SELECT 1 FROM account.account_holders h2
                  JOIN account.customers c ON c.customer_id = h2.customer_id
                  WHERE h2.account_id = a.account_id AND h2.removed_at IS NULL
                    AND c.kyc_status <> 'VERIFIED')
            ORDER BY a.account_id
            FOR UPDATE OF a
            """,
            (customer_id,),
        )
        activated = [r["account_id"] for r in cur.fetchall()]
        for account_id in activated:
            cur.execute("UPDATE account.accounts SET status = 'ACTIVE' WHERE account_id = %s", (account_id,))
            _record_event(cur, account_id, "ACCOUNT_ACTIVATED", {"reason": "kyc_verified"}, actor)
    for account_id in activated:  # unblock after commit (safe order)
        ledger.set_status(account_id, "OPEN")
    return {**_public_customer(customer), "activated_accounts": [str(a) for a in activated]}


# ------------------------------------------------------------------- accounts


def open_account(
    conn: psycopg.Connection,
    ledger: LedgerClient,
    *,
    product_type: str,
    holder_ids: list[UUID],
    operating_mode: str | None = None,
    overdraft_limit: int = 0,
    daily_limit: int | None = None,
    per_txn_limit: int | None = None,
    nickname: str | None = None,
    actor: str,
) -> dict:
    """holder_ids[0] becomes the PRIMARY holder."""
    if not holder_ids or len(holder_ids) > MAX_HOLDERS or len(set(holder_ids)) != len(holder_ids):
        raise AccountError(f"an account needs 1 to {MAX_HOLDERS} distinct holders")
    mode = operating_mode or ("SINGLE" if len(holder_ids) == 1 else "EITHER_OR_SURVIVOR")
    if (mode == "SINGLE") != (len(holder_ids) == 1):
        raise AccountError("SINGLE mode needs exactly one holder; joint modes need two or more")
    if mode == "JOINTLY":
        raise AccountError("JOINTLY operating mode is not supported yet")
    if overdraft_limit and product_type != "CURRENT":
        raise AccountError("overdraft is only available on CURRENT accounts")

    with _cur(conn) as cur:
        cur.execute(
            "SELECT customer_id, status, kyc_status FROM account.customers WHERE customer_id = ANY(%s)",
            (holder_ids,),
        )
        customers = {r["customer_id"]: r for r in cur.fetchall()}
    missing = [str(h) for h in holder_ids if h not in customers]
    if missing:
        raise NotFound("customer not found", customer_ids=missing)
    if any(c["status"] != "ACTIVE" for c in customers.values()):
        raise Forbidden("all holders must be active customers")
    all_verified = all(c["kyc_status"] == "VERIFIED" for c in customers.values())
    status = "ACTIVE" if all_verified else "PENDING_KYC"

    account_id = uuid4()
    ledger.open_account(account_id, product_type, overdraft_limit)
    if status != "ACTIVE":
        ledger.set_status(account_id, "DEBIT_BLOCKED")

    with conn.transaction(), _cur(conn) as cur:
        cur.execute("SELECT nextval('account.account_no_seq') AS n")
        number = make_account_number(cur.fetchone()["n"])
        cur.execute(
            """
            INSERT INTO account.accounts
                (account_id, account_number, product_type, operating_mode, status, overdraft_limit,
                 daily_limit, per_txn_limit, nickname)
            VALUES (%s, %s, %s, %s, %s, %s, COALESCE(%s, 10000000), COALESCE(%s, 5000000), %s)
            """,
            (account_id, number, product_type, mode, status, overdraft_limit,
             daily_limit, per_txn_limit, nickname),
        )
        for i, customer_id in enumerate(holder_ids):
            cur.execute(
                "INSERT INTO account.account_holders (account_id, customer_id, role) VALUES (%s, %s, %s)",
                (account_id, customer_id, "PRIMARY" if i == 0 else "JOINT"),
            )
        cur.execute("SELECT 1 FROM account.accounts WHERE account_id = %s FOR UPDATE", (account_id,))
        _record_event(cur, account_id, "ACCOUNT_OPENED", {
            "account_number": number, "product_type": product_type, "operating_mode": mode,
            "holders": [str(h) for h in holder_ids], "status": status,
        }, actor)
    return get_account(conn, account_id)


def get_account(conn: psycopg.Connection, account_id: UUID) -> dict:
    with _cur(conn) as cur:
        cur.execute("SELECT * FROM account.accounts WHERE account_id = %s", (account_id,))
        account = cur.fetchone()
        if account is None:
            raise NotFound("account not found")
        cur.execute(
            """
            SELECT h.customer_id, h.role, h.added_at, c.full_name, c.cif_number
            FROM account.account_holders h JOIN account.customers c USING (customer_id)
            WHERE h.account_id = %s AND h.removed_at IS NULL
            ORDER BY h.role DESC, h.added_at
            """,
            (account_id,),
        )
        account["holders"] = cur.fetchall()
    return account


def list_customer_accounts(conn: psycopg.Connection, customer_id: UUID) -> list[dict]:
    with _cur(conn) as cur:
        cur.execute(
            """
            SELECT a.*, h.role AS my_role
            FROM account.accounts a JOIN account.account_holders h USING (account_id)
            WHERE h.customer_id = %s AND h.removed_at IS NULL AND a.status <> 'CLOSED'
            ORDER BY a.opened_at
            """,
            (customer_id,),
        )
        return cur.fetchall()


def lookup_by_number(conn: psycopg.Connection, account_number: str) -> dict:
    """Payee name confirmation before paying. Returns a masked name only."""
    if not is_valid_account_number(account_number):
        raise AccountError("invalid account number (check digit failed)")
    with _cur(conn) as cur:
        cur.execute(
            """
            SELECT a.account_id, a.account_number, a.status, c.full_name
            FROM account.accounts a
            JOIN account.account_holders h ON h.account_id = a.account_id
                 AND h.role = 'PRIMARY' AND h.removed_at IS NULL
            JOIN account.customers c ON c.customer_id = h.customer_id
            WHERE a.account_number = %s
            """,
            (account_number,),
        )
        row = cur.fetchone()
    if row is None or row["status"] == "CLOSED":
        raise NotFound("account not found")
    return {"account_id": row["account_id"], "account_number": row["account_number"],
            "display_name": mask_name(row["full_name"]),
            "can_receive": row["status"] in ("ACTIVE", "DORMANT", "FROZEN")}


def mask_name(full_name: str) -> str:
    """'Riya Sharma' -> 'Riya S*****'"""
    parts = full_name.split()
    return " ".join([parts[0]] + [p[0] + "*" * (len(p) - 1) for p in parts[1:]])


def authorize(conn: psycopg.Connection, account_id: UUID, customer_id: UUID) -> dict:
    """The single check every service uses: may this customer debit this account?"""
    with _cur(conn) as cur:
        cur.execute(
            """
            SELECT a.account_id, a.product_type, a.status AS account_status, a.operating_mode,
                   a.daily_limit, a.per_txn_limit, a.overdraft_limit,
                   h.role, c.status AS customer_status, c.daily_transfer_limit
            FROM account.accounts a
            JOIN account.account_holders h ON h.account_id = a.account_id AND h.removed_at IS NULL
            JOIN account.customers c ON c.customer_id = h.customer_id
            WHERE a.account_id = %s AND h.customer_id = %s
            """,
            (account_id, customer_id),
        )
        row = cur.fetchone()
    if row is None:
        return {"allowed": False, "reason": "NOT_A_HOLDER"}
    reason = None
    if row["customer_status"] != "ACTIVE":
        reason = "CUSTOMER_" + row["customer_status"]
    elif row["account_status"] != "ACTIVE":
        reason = "ACCOUNT_" + row["account_status"]
    elif row["product_type"] in ("FD", "RD"):
        reason = "DEPOSIT_ACCOUNT_NOT_DEBITABLE"  # only the Deposits service moves FD/RD money
    elif row["operating_mode"] == "JOINTLY":
        reason = "NEEDS_ALL_HOLDERS"
    return {"allowed": reason is None, "reason": reason, **row}


def add_holder(conn: psycopg.Connection, account_id: UUID, customer_id: UUID, actor: str) -> dict:
    with conn.transaction(), _cur(conn) as cur:
        cur.execute("SELECT * FROM account.accounts WHERE account_id = %s FOR UPDATE", (account_id,))
        account = cur.fetchone()
        if account is None:
            raise NotFound("account not found")
        if account["status"] in ("CLOSED", "FROZEN"):
            raise Conflict(f"cannot add holders to a {account['status'].lower()} account")
        cur.execute("SELECT status FROM account.customers WHERE customer_id = %s", (customer_id,))
        customer = cur.fetchone()
        if customer is None:
            raise NotFound("customer not found")
        if customer["status"] != "ACTIVE":
            raise Forbidden("customer is not active")
        cur.execute(
            "SELECT customer_id FROM account.account_holders WHERE account_id = %s AND removed_at IS NULL",
            (account_id,),
        )
        current = [r["customer_id"] for r in cur.fetchall()]
        if customer_id in current:
            raise Conflict("customer is already a holder")
        if len(current) >= MAX_HOLDERS:
            raise Conflict(f"an account can have at most {MAX_HOLDERS} holders")
        cur.execute(
            """
            INSERT INTO account.account_holders (account_id, customer_id, role) VALUES (%s, %s, 'JOINT')
            ON CONFLICT (account_id, customer_id) DO UPDATE
                SET removed_at = NULL, added_at = now(), role = 'JOINT'
            """,
            (account_id, customer_id),
        )
        if account["operating_mode"] == "SINGLE":
            cur.execute(
                "UPDATE account.accounts SET operating_mode = 'EITHER_OR_SURVIVOR' WHERE account_id = %s",
                (account_id,),
            )
        _record_event(cur, account_id, "HOLDER_ADDED", {"customer_id": str(customer_id)}, actor)
    return get_account(conn, account_id)


def freeze_account(conn: psycopg.Connection, ledger: LedgerClient, account_id: UUID, *,
                   reason: str, actor: str) -> dict:
    ledger.set_status(account_id, "DEBIT_BLOCKED")  # block money first
    with conn.transaction(), _cur(conn) as cur:
        cur.execute("SELECT status FROM account.accounts WHERE account_id = %s FOR UPDATE", (account_id,))
        row = cur.fetchone()
        if row is None:
            raise NotFound("account not found")
        if row["status"] == "CLOSED":
            raise Conflict("account is closed")
        if row["status"] != "FROZEN":
            cur.execute(
                "UPDATE account.accounts SET status = 'FROZEN', freeze_reason = %s WHERE account_id = %s",
                (reason, account_id),
            )
            _record_event(cur, account_id, "ACCOUNT_FROZEN", {"reason": reason}, actor)
    return get_account(conn, account_id)


def unfreeze_account(conn: psycopg.Connection, ledger: LedgerClient, account_id: UUID, *, actor: str) -> dict:
    with conn.transaction(), _cur(conn) as cur:
        cur.execute("SELECT status FROM account.accounts WHERE account_id = %s FOR UPDATE", (account_id,))
        row = cur.fetchone()
        if row is None:
            raise NotFound("account not found")
        if row["status"] != "FROZEN":
            raise Conflict("account is not frozen")
        cur.execute(
            "UPDATE account.accounts SET status = 'ACTIVE', freeze_reason = NULL WHERE account_id = %s",
            (account_id,),
        )
        _record_event(cur, account_id, "ACCOUNT_UNFROZEN", {}, actor)
    ledger.set_status(account_id, "OPEN")  # unblock money last
    return get_account(conn, account_id)


# ------------------------------------------------------------------- beneficiaries & aliases


def add_beneficiary(conn: psycopg.Connection, customer_id: UUID, *, payee_account_number: str,
                    nickname: str | None = None) -> dict:
    payee = lookup_by_number(conn, payee_account_number)
    cooling_until = datetime.now(timezone.utc) + BENEFICIARY_COOLING
    try:
        with conn.transaction(), _cur(conn) as cur:
            cur.execute(
                """
                INSERT INTO account.beneficiaries
                    (customer_id, payee_account_number, payee_name, nickname, cooling_until)
                VALUES (%s, %s, %s, %s, %s) RETURNING *
                """,
                (customer_id, payee_account_number, payee["display_name"], nickname, cooling_until),
            )
            return cur.fetchone()
    except psycopg.errors.UniqueViolation as e:
        raise Conflict("payee already saved") from e
    except psycopg.errors.ForeignKeyViolation as e:
        raise NotFound("customer not found") from e


def list_beneficiaries(conn: psycopg.Connection, customer_id: UUID) -> list[dict]:
    with _cur(conn) as cur:
        cur.execute(
            """
            SELECT *, cooling_until > now() AS in_cooling_period
            FROM account.beneficiaries WHERE customer_id = %s AND removed_at IS NULL
            ORDER BY created_at
            """,
            (customer_id,),
        )
        return cur.fetchall()


def create_alias(conn: psycopg.Connection, account_id: UUID, alias: str) -> dict:
    alias = alias.strip().lower()
    if not alias.endswith("@unbeatable"):
        alias = f"{alias}@unbeatable"
    try:
        with conn.transaction(), _cur(conn) as cur:
            cur.execute(
                "INSERT INTO account.aliases (alias, account_id) VALUES (%s, %s) RETURNING *",
                (alias, account_id),
            )
            return cur.fetchone()
    except psycopg.errors.UniqueViolation as e:
        raise Conflict("alias is taken, or this account already has an alias") from e
    except psycopg.errors.CheckViolation as e:
        raise AccountError("alias must be 3-31 characters: a-z, 0-9, dot, dash, underscore") from e
    except psycopg.errors.ForeignKeyViolation as e:
        raise NotFound("account not found") from e


def resolve_alias(conn: psycopg.Connection, alias: str) -> dict:
    with _cur(conn) as cur:
        cur.execute(
            "SELECT a.account_number FROM account.aliases al JOIN account.accounts a USING (account_id) "
            "WHERE al.alias = %s AND al.disabled_at IS NULL",
            (alias.strip().lower(),),
        )
        row = cur.fetchone()
    if row is None:
        raise NotFound("alias not found")
    return {"alias": alias, **lookup_by_number(conn, row["account_number"])}
