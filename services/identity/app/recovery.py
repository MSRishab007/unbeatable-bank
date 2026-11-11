"""Changing and recovering credentials, and changing contact details.

    change password    signed in: current password -> new one; every other session ends
    forgot password    signed out: a code (email) + date of birth -> new password;
                       every session ends
    contact change     signed in: password + a code sent to the CURRENT email/app ->
                       new email or phone. Payments are not blocked afterwards; the risk
                       engine weighs them for 3 days instead (a new contact is how an
                       account takeover usually starts, but most changes are genuine).

Every one of these tells the customer, and raises a short-lived risk signal.
"""

from __future__ import annotations

from datetime import date
from uuid import UUID

import psycopg

from . import security
from .service import (OTP_TTL_SECONDS, Conflict, Deps, IdentityError, Unavailable, WrongSecret, _cur, _event,
                      _signal, end_all_sessions, issue_otp, verify_otp)

MIN_PASSWORD = 8


def _check_new_password(password: str, *, old_hash: str | None = None) -> None:
    if len(password) < MIN_PASSWORD:
        raise IdentityError(f"password must be at least {MIN_PASSWORD} characters")
    if old_hash and security.verify_password(password, old_hash):
        raise IdentityError("choose a password you haven't used just now")


def _set_password(conn: psycopg.Connection, customer_id: UUID, password: str, event: str) -> None:
    with conn.transaction(), _cur(conn) as cur:
        cur.execute("UPDATE identity.credentials SET password_hash = %s, failed_attempts = 0, locked_until = NULL "
                    "WHERE customer_id = %s", (security.hash_password(password), customer_id))
        _event(cur, str(customer_id), event, {"customer_id": str(customer_id)})


def change_password(conn: psycopg.Connection, deps: Deps, customer_id: UUID, session_id: str, *,
                    current_password: str, new_password: str) -> dict:
    with _cur(conn) as cur:
        cur.execute("SELECT email, password_hash FROM identity.credentials WHERE customer_id = %s", (customer_id,))
        row = cur.fetchone()
    if row is None or not security.verify_password(current_password, row["password_hash"]):
        raise WrongSecret("your current password is not right")
    _check_new_password(new_password, old_hash=row["password_hash"])
    _set_password(conn, customer_id, new_password, "PasswordChanged")
    ended = end_all_sessions(deps, customer_id, "PASSWORD_CHANGED", keep=session_id)
    _signal(deps, "put", f"sig:cust:{customer_id}:credential_reset", ttl=86400)
    deps.notifier.send(customer_id, "SECURITY", "Your password was changed",
                       f"You changed your password and we signed out {ended} other session(s). "
                       "If this wasn't you, call the bank now.", email=row["email"])
    return {"other_sessions_ended": ended}


def _find(conn: psycopg.Connection, identifier: str) -> dict | None:
    identifier = identifier.strip().lower()
    with _cur(conn) as cur:
        cur.execute("SELECT customer_id, email, password_hash FROM identity.credentials "
                    "WHERE email = %s OR phone = %s", (identifier, identifier))
        return cur.fetchone()


def forgot_password(conn: psycopg.Connection, deps: Deps, identifier: str) -> dict:
    """Always the same answer, so it can't be used to find out who banks here."""
    cred = _find(conn, identifier)
    if cred is not None:
        issue_otp(conn, deps, cred["customer_id"], "password_reset", "You asked to reset your password")
    return {"sent": True, "expires_in": OTP_TTL_SECONDS,
            "message": "If that email or phone belongs to a customer, we sent a code to their email."}


def reset_password(conn: psycopg.Connection, deps: Deps, *, identifier: str, code: str, date_of_birth: date,
                   new_password: str) -> dict:
    """The code is checked first and used up either way, so each guess at the date of birth
    costs a fresh code (and those are rate limited)."""
    wrong = IdentityError("the code or the date of birth is not right")
    cred = _find(conn, identifier)
    if cred is None:
        raise wrong
    result = verify_otp(conn, cred["customer_id"], "password_reset", code)
    if result == "WRONG":
        raise wrong
    if result != "OK":
        raise IdentityError("that code has expired or was tried too many times; ask for a new one")
    try:
        profile = deps.accounts.get_customer(cred["customer_id"])
    except Unavailable:
        raise
    except Exception as e:
        raise Unavailable("can't check your details right now; try again") from e
    if str(profile.get("date_of_birth")) != date_of_birth.isoformat():
        raise wrong
    _check_new_password(new_password)
    _set_password(conn, cred["customer_id"], new_password, "PasswordReset")
    ended = end_all_sessions(deps, cred["customer_id"], "PASSWORD_RESET")
    _signal(deps, "put", f"sig:cust:{cred['customer_id']}:credential_reset", ttl=86400)
    deps.notifier.send(cred["customer_id"], "SECURITY", "Your password was reset",
                       f"Your password was reset and we signed out {ended} session(s). "
                       "If this wasn't you, call the bank now.", email=cred["email"])
    return {"reset": True, "sessions_ended": ended}


# ------------------------------------------------------------------ contact details

FIELDS = ("email", "phone")


def _normalise(field: str, value: str) -> str:
    value = value.strip()
    if field == "email":
        value = value.lower()
        local, _, domain = value.partition("@")
        if not local or "." not in domain or " " in value:
            raise IdentityError("that email address doesn't look right")
    else:
        digits = value.replace(" ", "").replace("-", "")
        if not (digits.startswith("+") and digits[1:].isdigit() and 10 <= len(digits) - 1 <= 15):
            raise IdentityError("phone must be in international form, e.g. +919876543210")
        value = digits
    return value


def start_contact_change(conn: psycopg.Connection, deps: Deps, customer_id: UUID, *, field: str, value: str,
                         password: str) -> dict:
    if field not in FIELDS:
        raise IdentityError("you can change your email or phone")
    value = _normalise(field, value)
    with _cur(conn) as cur:
        cur.execute("SELECT email, phone, password_hash FROM identity.credentials WHERE customer_id = %s",
                    (customer_id,))
        cred = cur.fetchone()
        if cred is None or not security.verify_password(password, cred["password_hash"]):
            raise WrongSecret("your password is not right")
        if cred[field] == value:
            raise IdentityError(f"that is already your {field}")
        cur.execute(f"SELECT 1 FROM identity.credentials WHERE {field} = %s", (value,))   # field is whitelisted
        if cur.fetchone():
            raise Conflict(f"another customer already uses that {field}", field=field)
    deps.docstore.set("contact_changes", str(customer_id), {
        "field": field, "value": value, "expires_at": deps.clock() + OTP_TTL_SECONDS})
    # The code goes to the CURRENT email and the app, so a thief with only the password can't do it.
    issue_otp(conn, deps, customer_id, "contact_change",
              f"You asked to change your {field} to {_masked(field, value)}")
    return {"pending": True, "field": field, "expires_in": OTP_TTL_SECONDS}


def confirm_contact_change(conn: psycopg.Connection, deps: Deps, customer_id: UUID, *, code: str) -> dict:
    pending = deps.docstore.get("contact_changes", str(customer_id))
    if pending is None or pending["expires_at"] <= deps.clock():
        raise IdentityError("there is no change waiting; start again")
    result = verify_otp(conn, customer_id, "contact_change", code)
    if result == "WRONG":
        raise WrongSecret("that code is not right")
    deps.docstore.delete("contact_changes", str(customer_id))
    if result != "OK":
        raise IdentityError("that code has expired or was tried too many times; start again")
    field, value = pending["field"], pending["value"]
    try:
        with conn.transaction(), _cur(conn) as cur:
            cur.execute("SELECT email, phone FROM identity.credentials WHERE customer_id = %s FOR UPDATE",
                        (customer_id,))
            old = cur.fetchone()
            cur.execute(f"UPDATE identity.credentials SET {field} = %s WHERE customer_id = %s", (value, customer_id))
            # Inside the transaction: if the profile can't be updated, neither is the login.
            deps.accounts.update_contact(customer_id, field, value, f"customer:{customer_id}")
            _event(cur, str(customer_id), "ContactChanged", {"customer_id": str(customer_id), "field": field})
    except psycopg.errors.UniqueViolation as e:
        raise Conflict(f"another customer already uses that {field}", field=field) from e
    _signal(deps, "put", f"sig:cust:{customer_id}:contact_changed", ttl=3 * 86400)
    # Tell the OLD address too: it is the one that would notice a takeover.
    deps.notifier.send(customer_id, "SECURITY", f"Your {field} was changed",
                       f"Your {field} was changed from {_masked(field, old[field])} to {_masked(field, value)}. "
                       "If this wasn't you, use 'Block everything' in the app and call the bank.",
                       email=old["email"])
    return {"changed": field, "value": value}


def _masked(field: str, value: str) -> str:
    if field == "email":
        local, _, domain = value.partition("@")
        return f"{local[:2]}{'*' * max(1, len(local) - 2)}@{domain}"
    return f"{value[:3]}{'*' * max(1, len(value) - 6)}{value[-3:]}"
