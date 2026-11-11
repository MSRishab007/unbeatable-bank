"""Identity & Auth: who is calling, from which device, and step-up confirmation.

Storage split:
  Postgres (identity DB)   credentials, devices, staff users, OTP hashes
  Document store           sessions (with TTL) and login events: high-volume, semi-structured

Fraud signals produced here:
  new device login     -> customer is notified; Risk Detection weighs payments made soon after
  impossible travel    -> two logins further apart than a plane could cover in the time between
Both are written to DynaStore with a short TTL (best effort) and published as
`auth-events` through the outbox.
"""

from __future__ import annotations

import logging
import time
import uuid
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from typing import Any, Callable, Protocol
from uuid import UUID

import psycopg
from prometheus_client import Counter
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from ubcommon.auth import issue_token
from ubcommon.docstore import DocStore
from ubcommon.notify import Notifier

from . import security

log = logging.getLogger("identity")

LOGINS = Counter("identity_logins_total", "Login attempts", ["result"])
OTPS = Counter("identity_otps_total", "OTP events", ["event"])

AUTH_TOPIC = "auth-events"
MAX_FAILED_LOGINS = 5
LOCK_MINUTES = 15
OTP_TTL_SECONDS = 300
OTP_MAX_ATTEMPTS = 3
IMPOSSIBLE_SPEED_KMH = 900        # faster than a passenger jet
IMPOSSIBLE_MIN_KM = 100


class IdentityError(Exception):
    code = "IDENTITY_ERROR"
    http_status = 400

    def __init__(self, message: str, **details: Any) -> None:
        super().__init__(message)
        self.details = details


class InvalidCredentials(IdentityError):
    code = "INVALID_CREDENTIALS"
    http_status = 401


class Locked(IdentityError):
    code = "LOCKED"
    http_status = 423


class TooManyRequests(IdentityError):
    code = "RATE_LIMITED"
    http_status = 429


class Conflict(IdentityError):
    code = "CONFLICT"
    http_status = 409


class NotFound(IdentityError):
    code = "NOT_FOUND"
    http_status = 404


class Unavailable(IdentityError):
    code = "UNAVAILABLE"
    http_status = 503


class AccountsClient(Protocol):
    def create_customer(self, profile: dict) -> dict:
        """Returns the customer, or raises Conflict / IdentityError."""

    def verify_kyc(self, customer_id: UUID, actor: str = "system:identity") -> None: ...


class Signals(Protocol):
    """DynaStore, as far as this service needs it."""

    def put(self, key: str, value, *, ttl: int = 0, **kw) -> Any: ...

    def put_if_absent(self, key: str, value, *, ttl: int = 0, **kw) -> Any: ...


@dataclass
class Deps:
    accounts: AccountsClient
    docstore: DocStore
    notifier: Notifier
    jwt_secret: str
    signals: Signals | None = None
    login_allowed: Callable[[str, str], bool] = lambda identifier, ip: True   # rate limiter hook
    access_ttl: int = 900
    session_ttl: int = 7 * 86400
    auto_verify_kyc: bool = False
    clock: Callable[[], float] = field(default=time.time)
    objstore: Any = None            # ubcommon.objstore.ObjectStore: where KYC files are kept


def _cur(conn: psycopg.Connection) -> psycopg.Cursor:
    return conn.cursor(row_factory=dict_row)


def _event(cur: psycopg.Cursor, key: str, event_type: str, payload: dict) -> None:
    cur.execute("INSERT INTO identity.outbox (topic, event_key, event_type, payload) VALUES (%s, %s, %s, %s)",
                (AUTH_TOPIC, key, event_type, Jsonb({**payload, "event_time_ms": int(time.time() * 1000)})))


def _signal(deps: Deps, fn: str, key: str, ttl: int, at: float | None = None) -> None:
    if deps.signals is None:
        return
    try:
        getattr(deps.signals, fn)(key, repr(at if at is not None else deps.clock()), ttl=ttl, w=1)
    except Exception:   # signals are best effort; login must not fail because of them
        log.warning("could not write signal %s", key)


# ------------------------------------------------------------------ registration


def register(conn: psycopg.Connection, deps: Deps, *, full_name: str, date_of_birth: date, phone: str,
             email: str, password: str, pan: str | None = None) -> dict:
    if len(password) < 8:
        raise IdentityError("password must be at least 8 characters")
    email = email.strip().lower()
    customer_id = uuid.uuid4()
    try:
        with conn.transaction(), _cur(conn) as cur:
            cur.execute(
                "INSERT INTO identity.credentials (customer_id, email, phone, password_hash) "
                "VALUES (%s, %s, %s, %s)",
                (customer_id, email, phone, security.hash_password(password)),
            )
            # Created inside the transaction: if the Account service refuses, nothing is kept here.
            customer = deps.accounts.create_customer({
                "customer_id": str(customer_id), "full_name": full_name,
                "date_of_birth": date_of_birth.isoformat(), "phone": phone, "email": email, "pan": pan})
            _event(cur, str(customer_id), "CustomerRegistered", {"customer_id": str(customer_id)})
    except psycopg.errors.UniqueViolation as e:
        field_name = "phone" if "phone" in (e.diag.constraint_name or "") else "email"
        raise Conflict(f"an account with this {field_name} already exists", field=field_name) from e
    if deps.auto_verify_kyc:
        deps.accounts.verify_kyc(customer_id)
    return {"customer_id": customer_id, "cif_number": customer.get("cif_number"), "email": email}


# ------------------------------------------------------------------ login


def login(conn: psycopg.Connection, deps: Deps, *, identifier: str, password: str, fingerprint: str,
          device_label: str | None = None, ip: str = "", user_agent: str = "",
          geo: tuple[float, float] | None = None) -> dict:
    identifier = identifier.strip().lower()
    if not deps.login_allowed(identifier, ip):
        LOGINS.labels("rate_limited").inc()
        raise TooManyRequests("too many login attempts; please wait a few minutes")
    now = datetime.fromtimestamp(deps.clock(), tz=timezone.utc)

    with conn.transaction(), _cur(conn) as cur:
        cur.execute("SELECT * FROM identity.credentials WHERE email = %s OR phone = %s FOR UPDATE",
                    (identifier, identifier))
        cred = cur.fetchone()
        if cred is None:
            security.verify_password(password, security.DUMMY_HASH)
            _log_login(deps, None, identifier, "FAILED", "unknown_user", ip, user_agent, geo)
            LOGINS.labels("failed").inc()
            raise InvalidCredentials("email/phone or password is incorrect")
        if cred["locked_until"] and cred["locked_until"] > now:
            LOGINS.labels("locked").inc()
            raise Locked("too many failed attempts; try again later",
                         retry_after_seconds=int((cred["locked_until"] - now).total_seconds()))
        if not security.verify_password(password, cred["password_hash"]):
            failed = cred["failed_attempts"] + 1
            lock = failed >= MAX_FAILED_LOGINS
            cur.execute(
                "UPDATE identity.credentials SET failed_attempts = %s, locked_until = %s WHERE customer_id = %s",
                (0 if lock else failed, now + timedelta(minutes=LOCK_MINUTES) if lock else None,
                 cred["customer_id"]),
            )
            if lock:
                _event(cur, str(cred["customer_id"]), "LoginLocked", {"customer_id": str(cred["customer_id"]),
                                                                      "ip": ip})
            failure = InvalidCredentials("email/phone or password is incorrect",
                                         attempts_left=0 if lock else MAX_FAILED_LOGINS - failed)
        else:
            failure = None
            customer_id = cred["customer_id"]

            # Device: known, new, or previously revoked (which counts as new again).
            cur.execute(
                """
                INSERT INTO identity.devices (customer_id, fingerprint_hash, label, last_ip, first_seen, last_seen)
                VALUES (%s, %s, %s, %s, %s, %s)
                ON CONFLICT (customer_id, fingerprint_hash) DO UPDATE
                    SET last_seen = excluded.last_seen, last_ip = excluded.last_ip,
                        first_seen = CASE WHEN identity.devices.revoked_at IS NULL
                                          THEN identity.devices.first_seen ELSE excluded.first_seen END,
                        revoked_at = NULL
                RETURNING device_id, (first_seen = last_seen) AS is_new
                """,
                (customer_id, security.sha256(f"{customer_id}:{fingerprint}"), device_label, ip or None, now, now),
            )
            device = cur.fetchone()
            first_login = cred["last_login_at"] is None
            new_device = device["is_new"] and not first_login

            travel = False
            if geo and cred["last_login_lat"] is not None and cred["last_login_at"] is not None:
                km = security.distance_km(cred["last_login_lat"], cred["last_login_lon"], *geo)
                hours = max((now - cred["last_login_at"]).total_seconds() / 3600, 1 / 3600)
                travel = km >= IMPOSSIBLE_MIN_KM and km / hours > IMPOSSIBLE_SPEED_KMH

            cur.execute(
                """
                UPDATE identity.credentials
                SET failed_attempts = 0, locked_until = NULL, last_login_at = %s,
                    last_login_lat = COALESCE(%s, last_login_lat), last_login_lon = COALESCE(%s, last_login_lon)
                WHERE customer_id = %s
                """,
                (now, geo[0] if geo else None, geo[1] if geo else None, customer_id),
            )
            _event(cur, str(customer_id), "LoginSucceeded", {
                "customer_id": str(customer_id), "device_id": str(device["device_id"]),
                "new_device": new_device, "impossible_travel": travel, "ip": ip,
                "device_label": device_label})

    if failure is not None:   # raised after the transaction so the failed-attempt count is committed
        _log_login(deps, cred["customer_id"], identifier, "FAILED", "wrong_password", ip, user_agent, geo)
        LOGINS.labels("failed").inc()
        raise failure

    device_id = str(device["device_id"])
    # Tell the risk engine when this device was first seen, and about anything unusual.
    # The device a customer signs up on is trusted from the start.
    _signal(deps, "put_if_absent", f"seen:dev:{customer_id}:{device_id}", ttl=365 * 86400,
            at=deps.clock() - 2 * 86400 if first_login else None)
    if new_device:
        _signal(deps, "put", f"sig:cust:{customer_id}:new_device_login", ttl=1800)
        deps.notifier.send(customer_id, "SECURITY", "New device signed in",
                           f"Your account was just accessed from a new device ({device_label or 'unknown'}). "
                           "If this wasn't you, freeze your account now.", email=cred["email"])
    if travel:
        _signal(deps, "put", f"sig:cust:{customer_id}:impossible_travel", ttl=3600)

    session_id = uuid.uuid4().hex
    refresh_token, refresh_hash = security.new_refresh_token(session_id)
    deps.docstore.set("sessions", session_id, {
        "customer_id": str(customer_id), "device_id": device_id, "refresh_hash": refresh_hash,
        "ip": ip, "user_agent": user_agent[:200], "created_at": deps.clock(),
        "expires_at": deps.clock() + deps.session_ttl, "revoked": False})
    _log_login(deps, customer_id, identifier, "OK", "", ip, user_agent, geo, device_id=device_id,
               new_device=new_device, impossible_travel=travel)
    LOGINS.labels("ok").inc()
    return {
        "access_token": _access_token(deps, str(customer_id), session_id, device_id),
        "refresh_token": refresh_token,
        "token_type": "Bearer",
        "expires_in": deps.access_ttl,
        "customer_id": customer_id,
        "device_id": device_id,
        "new_device": new_device,
    }


def _access_token(deps: Deps, customer_id: str, session_id: str, device_id: str) -> str:
    return issue_token(subject=customer_id, role="customer", secret=deps.jwt_secret,
                       ttl_seconds=deps.access_ttl, sid=session_id, did=device_id)


def _log_login(deps: Deps, customer_id, identifier: str, result: str, reason: str, ip: str, user_agent: str,
               geo, **extra) -> None:
    try:
        deps.docstore.add("login_events", {
            "customer_id": str(customer_id) if customer_id else None,
            "identifier_hash": security.sha256(identifier)[:16], "result": result, "reason": reason,
            "ip": ip, "user_agent": user_agent[:200], "geo": list(geo) if geo else None,
            "at": deps.clock(), "expires_at": deps.clock() + 90 * 86400, **extra})
    except Exception:
        log.exception("could not record login event")


def refresh(deps: Deps, refresh_token: str) -> dict:
    session_id, _, secret = refresh_token.partition(".")
    session = deps.docstore.get("sessions", session_id) if session_id else None
    if (session is None or session.get("revoked") or session["expires_at"] <= deps.clock()
            or session["refresh_hash"] != security.sha256(secret)):
        raise InvalidCredentials("session has expired; please log in again")
    return {"access_token": _access_token(deps, session["customer_id"], session_id, session["device_id"]),
            "token_type": "Bearer", "expires_in": deps.access_ttl}


def logout(deps: Deps, customer_id: UUID, session_id: str) -> None:
    session = deps.docstore.get("sessions", session_id)
    if session and session["customer_id"] == str(customer_id):
        deps.docstore.set("sessions", session_id, {"revoked": True}, merge=True)


# ------------------------------------------------------------------ devices and sessions


def list_devices(conn: psycopg.Connection, customer_id: UUID) -> list[dict]:
    with _cur(conn) as cur:
        cur.execute(
            "SELECT device_id, label, first_seen, last_seen, last_ip FROM identity.devices "
            "WHERE customer_id = %s AND revoked_at IS NULL ORDER BY last_seen DESC", (customer_id,))
        return cur.fetchall()


def revoke_device(conn: psycopg.Connection, deps: Deps, customer_id: UUID, device_id: UUID) -> dict:
    """'Log out this device': the device is forgotten and its sessions are ended."""
    with conn.transaction(), _cur(conn) as cur:
        cur.execute(
            "UPDATE identity.devices SET revoked_at = now() WHERE device_id = %s AND customer_id = %s "
            "AND revoked_at IS NULL RETURNING device_id", (device_id, customer_id))
        if cur.fetchone() is None:
            raise NotFound("device not found")
        _event(cur, str(customer_id), "DeviceRevoked", {"customer_id": str(customer_id),
                                                         "device_id": str(device_id)})
    ended = 0
    for session in deps.docstore.query("sessions", {"customer_id": str(customer_id),
                                                    "device_id": str(device_id)}, limit=200):
        if not session.get("revoked"):
            deps.docstore.set("sessions", session["id"], {"revoked": True}, merge=True)
            ended += 1
    return {"device_id": device_id, "sessions_ended": ended}


def list_sessions(deps: Deps, customer_id: UUID) -> list[dict]:
    sessions = deps.docstore.query("sessions", {"customer_id": str(customer_id)}, order_by="created_at",
                                   descending=True, limit=50)
    return [{"session_id": s["id"], "device_id": s["device_id"], "ip": s.get("ip"),
             "user_agent": s.get("user_agent"), "created_at": s["created_at"]}
            for s in sessions if not s.get("revoked")]


def session_is_active(deps: Deps, session_id: str) -> bool:
    session = deps.docstore.get("sessions", session_id)
    return bool(session) and not session.get("revoked") and session["expires_at"] > deps.clock()


def login_history(deps: Deps, customer_id: UUID, limit: int = 20) -> list[dict]:
    events = deps.docstore.query("login_events", {"customer_id": str(customer_id)}, order_by="at",
                                 descending=True, limit=limit)
    return [{k: e.get(k) for k in ("result", "ip", "at", "new_device", "impossible_travel", "device_id")}
            for e in events]


# ------------------------------------------------------------------ one-time passwords


def issue_otp(conn: psycopg.Connection, deps: Deps, customer_id: UUID, purpose: str, context: str = "") -> dict:
    code = security.new_otp()
    with conn.transaction(), _cur(conn) as cur:
        cur.execute("UPDATE identity.otps SET consumed_at = now() WHERE customer_id = %s AND purpose = %s "
                    "AND consumed_at IS NULL", (customer_id, purpose))
        cur.execute(
            "INSERT INTO identity.otps (customer_id, purpose, code_hash, expires_at) "
            "VALUES (%s, %s, %s, now() + make_interval(secs => %s)) RETURNING otp_id",
            (customer_id, purpose, "pending", OTP_TTL_SECONDS))
        otp_id = cur.fetchone()["otp_id"]
        cur.execute("UPDATE identity.otps SET code_hash = %s WHERE otp_id = %s",
                    (security.sha256(f"{otp_id}:{code}"), otp_id))
        cur.execute("SELECT email FROM identity.credentials WHERE customer_id = %s", (customer_id,))
        row = cur.fetchone()
    deps.notifier.send(customer_id, "OTP", "Confirmation code",
                       f"{context + '. ' if context else ''}Your code is {code}. It expires in 5 minutes. "
                       "Never share it with anyone.", email=row["email"] if row else None,
                       data={"purpose": purpose})
    OTPS.labels("issued").inc()
    return {"otp_id": otp_id, "expires_in": OTP_TTL_SECONDS}


def verify_otp(conn: psycopg.Connection, customer_id: UUID, purpose: str, code: str) -> str:
    """OK, WRONG, or LOCKED (too many wrong attempts, or nothing left to try)."""
    with conn.transaction(), _cur(conn) as cur:
        cur.execute(
            "SELECT * FROM identity.otps WHERE customer_id = %s AND purpose = %s "
            "ORDER BY created_at DESC LIMIT 1 FOR UPDATE", (customer_id, purpose))
        otp = cur.fetchone()
        if otp is None or otp["consumed_at"] is not None or otp["expires_at"] <= datetime.now(timezone.utc):
            OTPS.labels("locked").inc()
            return "LOCKED"
        if security.sha256(f"{otp['otp_id']}:{code}") == otp["code_hash"]:
            cur.execute("UPDATE identity.otps SET consumed_at = now() WHERE otp_id = %s", (otp["otp_id"],))
            OTPS.labels("verified").inc()
            return "OK"
        attempts = otp["attempts"] + 1
        exhausted = attempts >= OTP_MAX_ATTEMPTS
        cur.execute("UPDATE identity.otps SET attempts = %s, consumed_at = CASE WHEN %s THEN now() END "
                    "WHERE otp_id = %s", (attempts, exhausted, otp["otp_id"]))
        OTPS.labels("locked" if exhausted else "wrong").inc()
        return "LOCKED" if exhausted else "WRONG"


# ------------------------------------------------------------------ staff


def upsert_staff(conn: psycopg.Connection, staff_id: str, role: str, password: str) -> None:
    conn.execute(
        "INSERT INTO identity.staff_users (staff_id, password_hash, role) VALUES (%s, %s, %s) "
        "ON CONFLICT (staff_id) DO UPDATE SET role = excluded.role, password_hash = excluded.password_hash, "
        "active = true", (staff_id, security.hash_password(password), role))


def staff_login(conn: psycopg.Connection, deps: Deps, staff_id: str, password: str) -> dict:
    with _cur(conn) as cur:
        cur.execute("SELECT * FROM identity.staff_users WHERE staff_id = %s AND active", (staff_id.lower(),))
        staff = cur.fetchone()
    if not security.verify_password(password, staff["password_hash"] if staff else security.DUMMY_HASH) \
            or staff is None:
        raise InvalidCredentials("staff id or password is incorrect")
    token = issue_token(subject=staff["staff_id"], role="staff", secret=deps.jwt_secret,
                        ttl_seconds=8 * 3600, staff_role=staff["role"])
    return {"access_token": token, "token_type": "Bearer", "expires_in": 8 * 3600,
            "staff_id": staff["staff_id"], "role": staff["role"]}
