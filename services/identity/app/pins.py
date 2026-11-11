"""Two PINs per customer.

    PAYMENT  6 digits   money leaving: payments, adding a payee, raising a limit
    APP      4 digits   deposits (open, close), nominees, unlocking an idle session

They must be different from each other: the app PIN may not be the start or the end
of the payment PIN, so knowing one does not give away the other.

Three wrong tries lock that PIN for 24 hours. The customer is told, the risk engine
gets a signal, and the lock can be lifted early only by resetting the PIN with the
password and a code sent to the customer.

The gateway asks `verify` before forwarding any request that needs a PIN; the PIN
itself never reaches the service that does the work.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from uuid import UUID

import psycopg
from prometheus_client import Counter

from . import security
from .service import (Deps, IdentityError, InvalidCredentials, PinLocked, WrongSecret, _cur, _event,
                      _signal, issue_otp, verify_otp)

PIN_CHECKS = Counter("identity_pin_checks_total", "PIN verifications", ["kind", "result"])

LENGTH = {"PAYMENT": 6, "APP": 4}
NAME = {"PAYMENT": "payment PIN", "APP": "app PIN"}
MAX_ATTEMPTS = 3
LOCK_FOR = timedelta(hours=24)


def _kind(kind: str) -> str:
    kind = kind.upper()
    if kind not in LENGTH:
        raise IdentityError("PIN kind must be PAYMENT or APP")
    return kind


def check_shape(kind: str, pin: str) -> None:
    """Right length, digits only, and not one anyone would guess first."""
    n = LENGTH[kind]
    if len(pin) != n or not pin.isdigit():
        raise IdentityError(f"the {NAME[kind]} must be exactly {n} digits")
    digits = [int(c) for c in pin]
    steps = {b - a for a, b in zip(digits, digits[1:])}
    if len(set(digits)) == 1 or steps in ({1}, {-1}):
        raise IdentityError(f"that {NAME[kind]} is too easy to guess (repeated or consecutive digits)")


def _clashes(conn: psycopg.Connection, deps: Deps, customer_id: UUID, kind: str, pin: str) -> bool:
    """Is the app PIN the first or last four digits of the payment PIN?

    Only hashes are stored, so this tries the 200 payment PINs an app PIN could be part
    of (or the 2 app PINs a payment PIN could contain). Cheap with HMAC."""
    other = "APP" if kind == "PAYMENT" else "PAYMENT"
    with _cur(conn) as cur:
        cur.execute("SELECT pin_hash FROM identity.pins WHERE customer_id = %s AND kind = %s", (customer_id, other))
        row = cur.fetchone()
    if row is None:
        return False
    if kind == "PAYMENT":
        candidates = {pin[:4], pin[-4:]}
    else:
        candidates = {f"{pin}{x:02d}" for x in range(100)} | {f"{x:02d}{pin}" for x in range(100)}
    return any(security.verify_pin(c, row["pin_hash"], deps.pin_pepper) for c in candidates)


def status(conn: psycopg.Connection, deps: Deps, customer_id: UUID) -> dict:
    now = datetime.fromtimestamp(deps.clock(), tz=timezone.utc)
    with _cur(conn) as cur:
        cur.execute("SELECT kind, failed_attempts, locked_until, set_at FROM identity.pins WHERE customer_id = %s",
                    (customer_id,))
        rows = {r["kind"]: r for r in cur.fetchall()}
    out = {}
    for kind in LENGTH:
        r = rows.get(kind)
        locked = bool(r and r["locked_until"] and r["locked_until"] > now)
        out[kind] = {"set": r is not None, "length": LENGTH[kind], "locked": locked,
                     "locked_until": r["locked_until"] if locked else None,
                     "attempts_left": None if r is None else MAX_ATTEMPTS - r["failed_attempts"],
                     "set_at": r["set_at"] if r else None}
    return out


def _check_password(conn: psycopg.Connection, customer_id: UUID, password: str) -> dict:
    with _cur(conn) as cur:
        cur.execute("SELECT email, password_hash FROM identity.credentials WHERE customer_id = %s", (customer_id,))
        row = cur.fetchone()
    if row is None:
        raise InvalidCredentials("customer not found")
    if not security.verify_password(password, row["password_hash"]):
        raise WrongSecret("your password is not right")
    return row


def set_pin(conn: psycopg.Connection, deps: Deps, customer_id: UUID, kind: str, pin: str, *,
            password: str | None = None, current_pin: str | None = None) -> dict:
    """Set a PIN for the first time (needs the password) or change it (the current PIN or
    the password). A locked PIN cannot be changed this way: use the reset."""
    kind = _kind(kind)
    check_shape(kind, pin)
    with _cur(conn) as cur:
        cur.execute("SELECT * FROM identity.pins WHERE customer_id = %s AND kind = %s", (customer_id, kind))
        existing = cur.fetchone()
    now = datetime.fromtimestamp(deps.clock(), tz=timezone.utc)
    if existing and existing["locked_until"] and existing["locked_until"] > now:
        raise PinLocked(f"your {NAME[kind]} is locked; reset it with your password and a code",
                        locked_until=existing["locked_until"].isoformat())
    if password:
        _check_password(conn, customer_id, password)
    elif current_pin and existing:
        outcome = verify(conn, deps, customer_id, kind, current_pin)
        if outcome["result"] == "LOCKED":
            raise PinLocked(f"your {NAME[kind]} is now locked for 24 hours",
                            locked_until=outcome.get("locked_until"))
        if outcome["result"] != "OK":
            raise WrongSecret(f"your current {NAME[kind]} is not right", attempts_left=outcome.get("attempts_left"))
    else:
        raise IdentityError("enter your password" if not existing else f"enter your current {NAME[kind]}")
    if _clashes(conn, deps, customer_id, kind, pin):
        raise IdentityError("your app PIN and payment PIN must be different: the app PIN can't be the "
                            "first or last four digits of the payment PIN")
    _store(conn, deps, customer_id, kind, pin, "PinChanged" if existing else "PinSet")
    deps.notifier.send(customer_id, "SECURITY", f"Your {NAME[kind]} was {'changed' if existing else 'set'}",
                       "If this wasn't you, use 'Block everything' in the app and call the bank.")
    return status(conn, deps, customer_id)[kind]


def _store(conn: psycopg.Connection, deps: Deps, customer_id: UUID, kind: str, pin: str, event: str) -> None:
    with conn.transaction(), _cur(conn) as cur:
        cur.execute(
            """
            INSERT INTO identity.pins (customer_id, kind, pin_hash, set_at) VALUES (%s, %s, %s, now())
            ON CONFLICT (customer_id, kind) DO UPDATE
                SET pin_hash = excluded.pin_hash, set_at = now(), failed_attempts = 0, locked_until = NULL
            """,
            (customer_id, kind, security.hash_pin(pin, deps.pin_pepper)))
        _event(cur, str(customer_id), event, {"customer_id": str(customer_id), "kind": kind})


def verify(conn: psycopg.Connection, deps: Deps, customer_id: UUID, kind: str, pin: str) -> dict:
    """OK, WRONG (with attempts_left), LOCKED (with locked_until), NOT_SET, or REQUIRED when
    no PIN was given (so the app knows whether to ask for it or to have it set first)."""
    kind = _kind(kind)
    now = datetime.fromtimestamp(deps.clock(), tz=timezone.utc)
    with conn.transaction(), _cur(conn) as cur:
        cur.execute("SELECT * FROM identity.pins WHERE customer_id = %s AND kind = %s FOR UPDATE",
                    (customer_id, kind))
        row = cur.fetchone()
        if row is None:
            result = {"result": "NOT_SET"}
        elif row["locked_until"] and row["locked_until"] > now:
            result = {"result": "LOCKED", "locked_until": row["locked_until"].isoformat()}
        elif not pin:              # "is a PIN set?": asked before the customer has typed one; not a try
            result = {"result": "REQUIRED"}
        elif security.verify_pin(pin or "", row["pin_hash"], deps.pin_pepper):
            if row["failed_attempts"]:
                cur.execute("UPDATE identity.pins SET failed_attempts = 0 WHERE customer_id = %s AND kind = %s",
                            (customer_id, kind))
            result = {"result": "OK"}
        else:
            failed = row["failed_attempts"] + 1
            locked_until = now + LOCK_FOR if failed >= MAX_ATTEMPTS else None
            cur.execute("UPDATE identity.pins SET failed_attempts = %s, locked_until = %s "
                        "WHERE customer_id = %s AND kind = %s",
                        (0 if locked_until else failed, locked_until, customer_id, kind))
            if locked_until:
                _event(cur, str(customer_id), "PinLocked", {"customer_id": str(customer_id), "kind": kind})
                result = {"result": "LOCKED", "locked_until": locked_until.isoformat(), "just_locked": True}
            else:
                result = {"result": "WRONG", "attempts_left": MAX_ATTEMPTS - failed}
    PIN_CHECKS.labels(kind, result["result"]).inc()
    if result.get("just_locked"):
        _signal(deps, "put", f"sig:cust:{customer_id}:pin_locked", ttl=86400)
        deps.notifier.send(customer_id, "SECURITY", f"Your {NAME[kind]} is locked",
                           f"Someone entered a wrong {NAME[kind]} {MAX_ATTEMPTS} times, so it is locked for "
                           "24 hours. If this wasn't you, change your password and use 'Block everything'.")
    return result


def request_reset(conn: psycopg.Connection, deps: Deps, customer_id: UUID, kind: str) -> dict:
    kind = _kind(kind)
    return issue_otp(conn, deps, customer_id, f"pin_reset:{kind}", f"You asked to reset your {NAME[kind]}")


def reset(conn: psycopg.Connection, deps: Deps, customer_id: UUID, kind: str, *, code: str, password: str,
          new_pin: str) -> dict:
    """Forgot or locked PIN: the password and a code sent to the customer set a new one."""
    kind = _kind(kind)
    check_shape(kind, new_pin)
    _check_password(conn, customer_id, password)
    result = verify_otp(conn, customer_id, f"pin_reset:{kind}", code)
    if result == "WRONG":
        raise WrongSecret("that code is not right")
    if result != "OK":
        raise IdentityError("that code has expired or was tried too many times; ask for a new one")
    if _clashes(conn, deps, customer_id, kind, new_pin):
        raise IdentityError("your app PIN and payment PIN must be different: the app PIN can't be the "
                            "first or last four digits of the payment PIN")
    _store(conn, deps, customer_id, kind, new_pin, "PinReset")
    # A reset is what an attacker who has the password would do next: weigh payments for a day.
    _signal(deps, "put", f"sig:cust:{customer_id}:credential_reset", ttl=86400)
    deps.notifier.send(customer_id, "SECURITY", f"Your {NAME[kind]} was reset",
                       "If this wasn't you, use 'Block everything' in the app and call the bank.")
    return status(conn, deps, customer_id)[kind]
