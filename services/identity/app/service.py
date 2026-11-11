"""Identity & Auth: who is calling, from which device, and step-up confirmation.

Storage split:
  Postgres (identity DB)   credentials, devices, staff users, OTP hashes
  Document store           sessions (with TTL) and login events: high-volume, semi-structured

Sessions are checked on every request (through the gateway), so ending one takes
effect at once; an idle customer session locks after 5 minutes and is unlocked with
the app PIN. Refresh tokens are single-use: a reused one ends the session.

Fraud signals produced here:
  new device login     -> customer is notified; Risk Detection weighs payments made soon after
  impossible travel    -> two logins further apart than a plane could cover in the time between
  PIN locked, password or PIN reset, contact details changed, refresh token reused
All are written to DynaStore with a short TTL (best effort) and published as
`auth-events` through the outbox.
"""

from __future__ import annotations

import hashlib
import hmac
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
SESSIONS = Counter("identity_session_events_total", "Session events", ["event"])

AUTH_TOPIC = "auth-events"
MAX_FAILED_LOGINS = 5
LOCK_MINUTES = 15
OTP_TTL_SECONDS = 300
OTP_MAX_ATTEMPTS = 3
IMPOSSIBLE_SPEED_KMH = 900        # faster than a passenger jet
IMPOSSIBLE_MIN_KM = 100
TOUCH_EVERY_SECONDS = 15          # how stale a session's last_seen may get before it is written again
MAX_UNLOCK_FAILURES = 5
STAFF_TOKEN_SECONDS = 8 * 3600


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


class WrongSecret(IdentityError):
    """A wrong PIN, code or password from someone who is already signed in (not 401: the
    session itself is fine)."""
    code = "WRONG_SECRET"
    http_status = 403


class PinNotSet(IdentityError):
    code = "PIN_NOT_SET"
    http_status = 428


class PinLocked(IdentityError):
    code = "PIN_LOCKED"
    http_status = 423


class AccountsClient(Protocol):
    def create_customer(self, profile: dict) -> dict:
        """Returns the customer, or raises Conflict / IdentityError."""

    def verify_kyc(self, customer_id: UUID, actor: str = "system:identity") -> None: ...

    def get_customer(self, customer_id: UUID) -> dict: ...

    def update_contact(self, customer_id: UUID, field: str, value: str, actor: str) -> None: ...


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
    idle_lock_seconds: int = 300    # a customer session locks after this long without activity
    staff_idle_seconds: int = 900   # a staff session ends after this long without activity
    pin_pepper: bytes = b""         # secret mixed into PIN hashes; derived from jwt_secret if empty

    def __post_init__(self) -> None:
        if not self.pin_pepper:
            self.pin_pepper = hashlib.sha256(b"pin-pepper:" + self.jwt_secret.encode()).digest()


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
#
# A login is two steps when the device is new: the password, then a code sent to the
# customer (email and the in-app inbox of their other devices). The device they sign up
# on, and any device they have confirmed before, skip the second step.


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
                        label = COALESCE(excluded.label, identity.devices.label),
                        first_seen = CASE WHEN identity.devices.revoked_at IS NULL
                                          THEN identity.devices.first_seen ELSE excluded.first_seen END,
                        trusted_at = CASE WHEN identity.devices.revoked_at IS NULL
                                          THEN identity.devices.trusted_at END,
                        revoked_at = NULL
                RETURNING device_id, trusted_at
                """,
                (customer_id, security.sha256(f"{customer_id}:{fingerprint}"), device_label, ip or None, now, now),
            )
            device = cur.fetchone()
            first_login = cred["last_login_at"] is None
            if first_login:            # the device a customer signs up on is trusted from the start
                cur.execute("UPDATE identity.devices SET trusted_at = %s WHERE device_id = %s",
                            (now, device["device_id"]))
            needs_code = device["trusted_at"] is None and not first_login

            travel = False
            if geo and cred["last_login_lat"] is not None and cred["last_login_at"] is not None:
                km = security.distance_km(cred["last_login_lat"], cred["last_login_lon"], *geo)
                hours = max((now - cred["last_login_at"]).total_seconds() / 3600, 1 / 3600)
                travel = km >= IMPOSSIBLE_MIN_KM and km / hours > IMPOSSIBLE_SPEED_KMH

            cur.execute("UPDATE identity.credentials SET failed_attempts = 0, locked_until = NULL "
                        "WHERE customer_id = %s", (customer_id,))
            if needs_code:
                _event(cur, str(customer_id), "LoginChallenged", {
                    "customer_id": str(customer_id), "device_id": str(device["device_id"]), "ip": ip,
                    "impossible_travel": travel, "device_label": device_label})
            else:
                _record_login(cur, customer_id, device["device_id"], now, geo, new_device=False,
                              travel=travel, ip=ip, device_label=device_label)

    if failure is not None:   # raised after the transaction so the failed-attempt count is committed
        _log_login(deps, cred["customer_id"], identifier, "FAILED", "wrong_password", ip, user_agent, geo)
        LOGINS.labels("failed").inc()
        raise failure

    device_id = str(device["device_id"])
    if needs_code:
        challenge_id = uuid.uuid4().hex
        deps.docstore.set("login_challenges", challenge_id, {
            "customer_id": str(customer_id), "device_id": device_id, "device_label": device_label,
            "identifier": identifier, "geo": list(geo) if geo else None, "impossible_travel": travel,
            "created_at": deps.clock(), "expires_at": deps.clock() + OTP_TTL_SECONDS})
        issue_otp(conn, deps, customer_id, f"device:{challenge_id}",
                  f"Someone is signing in to your account on a new device ({device_label or 'unknown device'}). "
                  "If this is you, enter this code there. If it isn't, change your password now")
        _log_login(deps, customer_id, identifier, "CHALLENGED", "new_device", ip, user_agent, geo,
                   device_id=device_id, new_device=True, impossible_travel=travel)
        LOGINS.labels("challenged").inc()
        return {"otp_required": True, "challenge_id": challenge_id, "expires_in": OTP_TTL_SECONDS,
                "device_id": device_id,
                "message": "This is a new device. We sent a code to your email and to the app on your "
                           "other devices."}

    _signal(deps, "put_if_absent", f"seen:dev:{customer_id}:{device_id}", ttl=365 * 86400,
            at=deps.clock() - 2 * 86400 if first_login else None)
    return _start_session(deps, customer_id, device_id, identifier=identifier, new_device=False, travel=travel,
                          ip=ip, user_agent=user_agent, geo=geo, device_label=device_label, email=cred["email"])


def verify_login(conn: psycopg.Connection, deps: Deps, *, challenge_id: str, code: str, ip: str = "",
                 user_agent: str = "") -> dict:
    """Second step of a login from a new device: the code we sent. Three wrong codes end the attempt."""
    challenge = deps.docstore.get("login_challenges", challenge_id) if challenge_id else None
    if challenge is None or challenge["expires_at"] <= deps.clock():
        raise InvalidCredentials("this sign-in has expired; sign in again")
    customer_id = UUID(challenge["customer_id"])
    result = verify_otp(conn, customer_id, f"device:{challenge_id}", code)
    if result == "WRONG":
        raise WrongSecret("that code is not right")
    deps.docstore.delete("login_challenges", challenge_id)
    if result != "OK":
        _log_login(deps, customer_id, challenge["identifier"], "FAILED", "device_code", ip, user_agent, None)
        raise InvalidCredentials("too many wrong codes; sign in again")

    geo = tuple(challenge["geo"]) if challenge.get("geo") else None
    now = datetime.fromtimestamp(deps.clock(), tz=timezone.utc)
    with conn.transaction(), _cur(conn) as cur:
        cur.execute("UPDATE identity.devices SET trusted_at = %s WHERE device_id = %s AND revoked_at IS NULL "
                    "RETURNING device_id", (now, challenge["device_id"]))
        if cur.fetchone() is None:
            raise InvalidCredentials("this device was signed out; sign in again")
        cur.execute("SELECT email FROM identity.credentials WHERE customer_id = %s", (customer_id,))
        email = cur.fetchone()["email"]
        _record_login(cur, customer_id, challenge["device_id"], now, geo, new_device=True,
                      travel=challenge["impossible_travel"], ip=ip, device_label=challenge.get("device_label"))
    _signal(deps, "put_if_absent", f"seen:dev:{customer_id}:{challenge['device_id']}", ttl=365 * 86400)
    return _start_session(deps, customer_id, challenge["device_id"], identifier=challenge["identifier"],
                          new_device=True, travel=challenge["impossible_travel"], ip=ip, user_agent=user_agent,
                          geo=geo, device_label=challenge.get("device_label"), email=email)


def _record_login(cur: psycopg.Cursor, customer_id, device_id, now: datetime, geo, *, new_device: bool,
                  travel: bool, ip: str, device_label: str | None) -> None:
    cur.execute(
        """
        UPDATE identity.credentials
        SET last_login_at = %s,
            last_login_lat = COALESCE(%s, last_login_lat), last_login_lon = COALESCE(%s, last_login_lon)
        WHERE customer_id = %s
        """,
        (now, geo[0] if geo else None, geo[1] if geo else None, customer_id),
    )
    _event(cur, str(customer_id), "LoginSucceeded", {
        "customer_id": str(customer_id), "device_id": str(device_id), "new_device": new_device,
        "impossible_travel": travel, "ip": ip, "device_label": device_label})


def _start_session(deps: Deps, customer_id, device_id: str, *, identifier: str, new_device: bool, travel: bool,
                   ip: str, user_agent: str, geo, device_label: str | None, email: str) -> dict:
    if new_device:
        _signal(deps, "put", f"sig:cust:{customer_id}:new_device_login", ttl=1800)
        deps.notifier.send(customer_id, "SECURITY", "New device signed in",
                           f"Your account was just accessed from a new device ({device_label or 'unknown'}). "
                           "If this wasn't you, use 'Block everything' in the app now.", email=email)
    if travel:
        _signal(deps, "put", f"sig:cust:{customer_id}:impossible_travel", ttl=3600)

    session_id = uuid.uuid4().hex
    refresh_token, refresh_hash = security.new_refresh_token(session_id)
    now = deps.clock()
    deps.docstore.set("sessions", session_id, {
        "kind": "customer", "customer_id": str(customer_id), "device_id": device_id,
        "refresh_hash": refresh_hash, "prev_refresh_hash": None, "ip": ip, "user_agent": user_agent[:200],
        "created_at": now, "last_seen": now, "locked": False, "unlock_failures": 0,
        "expires_at": now + deps.session_ttl, "revoked": False})
    _log_login(deps, customer_id, identifier, "OK", "", ip, user_agent, geo, device_id=device_id,
               new_device=new_device, impossible_travel=travel)
    LOGINS.labels("ok").inc()
    return {
        "access_token": _access_token(deps, str(customer_id), session_id, device_id),
        "refresh_token": refresh_token,
        "token_type": "Bearer",
        "expires_in": deps.access_ttl,
        "idle_lock_seconds": deps.idle_lock_seconds,
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


# ------------------------------------------------------------------ sessions
#
# Every request through the gateway checks its session here (cached there for a few
# seconds), so ending a session takes effect at once, not when the access token expires.
#
# A customer session that sees no activity for `idle_lock_seconds` locks: requests get
# 423 until the customer unlocks it with their app PIN (or password). It is not ended,
# so they carry on where they were. Staff sessions end instead of locking.


def refresh(deps: Deps, refresh_token: str) -> dict:
    """A new access token and a new refresh token; the old refresh token stops working.

    If an old refresh token comes back, two parties hold copies of it (a stolen token
    being replayed, or the real customer after the thief used it first). We cannot tell
    which is which, so the session is ended for both."""
    session_id, _, secret = refresh_token.partition(".")
    session = deps.docstore.get("sessions", session_id) if session_id else None
    presented = security.sha256(secret)
    if session is None or session.get("revoked") or session["expires_at"] <= deps.clock() \
            or session.get("kind", "customer") != "customer":
        raise InvalidCredentials("session has expired; please log in again")
    if session.get("prev_refresh_hash") and hmac.compare_digest(presented, session["prev_refresh_hash"]):
        deps.docstore.set("sessions", session_id, {"revoked": True, "ended_reason": "REFRESH_REUSED"}, merge=True)
        _signal(deps, "put", f"sig:cust:{session['customer_id']}:token_reuse", ttl=86400)
        deps.notifier.send(session["customer_id"], "SECURITY", "We signed you out",
                           "A sign-in token from one of your sessions was used twice, which can mean "
                           "someone copied it. We ended that session. Sign in again; if you see anything "
                           "you don't recognise, change your password.")
        SESSIONS.labels("refresh_reused").inc()
        raise InvalidCredentials("session ended for your safety; please log in again")
    if not hmac.compare_digest(presented, session["refresh_hash"]):
        raise InvalidCredentials("session has expired; please log in again")
    new_token, new_hash = security.new_refresh_token(session_id)
    deps.docstore.set("sessions", session_id, {"refresh_hash": new_hash, "prev_refresh_hash": presented},
                      merge=True)
    return {"access_token": _access_token(deps, session["customer_id"], session_id, session["device_id"]),
            "refresh_token": new_token, "token_type": "Bearer", "expires_in": deps.access_ttl}


def check_session(deps: Deps, session_id: str, *, activity: bool = True) -> dict:
    """ACTIVE, LOCKED (idle too long, or locked by the customer) or ENDED.

    activity=False is for background requests (the app checking its inbox): they neither
    keep a session awake nor are refused because it locked."""
    session = deps.docstore.get("sessions", session_id) if session_id else None
    now = deps.clock()
    if session is None or session.get("revoked") or session["expires_at"] <= now:
        return {"state": "ENDED"}
    staff = session.get("kind") == "staff"
    idle_limit = deps.staff_idle_seconds if staff else deps.idle_lock_seconds
    idle = now - session.get("last_seen", now)
    if staff and idle > idle_limit:
        deps.docstore.set("sessions", session_id, {"revoked": True, "ended_reason": "IDLE"}, merge=True)
        return {"state": "ENDED"}
    if session.get("locked"):
        return {"state": "LOCKED", "reason": session.get("locked_reason", "IDLE")}
    if idle > idle_limit:
        if not activity:
            return {"state": "ACTIVE", "idle_seconds": int(idle)}     # locks on the next real request
        deps.docstore.set("sessions", session_id, {"locked": True, "locked_reason": "IDLE"}, merge=True)
        SESSIONS.labels("idle_locked").inc()
        return {"state": "LOCKED", "reason": "IDLE"}
    if activity and idle > TOUCH_EVERY_SECONDS:     # don't write on every request
        deps.docstore.set("sessions", session_id, {"last_seen": now}, merge=True)
    return {"state": "ACTIVE", "idle_seconds": int(idle) if not activity else 0,
            "lock_after": idle_limit}


def session_is_active(deps: Deps, session_id: str) -> bool:
    return check_session(deps, session_id, activity=False)["state"] == "ACTIVE"


def _own_session(deps: Deps, customer_id: UUID, session_id: str) -> dict:
    session = deps.docstore.get("sessions", session_id) if session_id else None
    if (session is None or session.get("customer_id") != str(customer_id) or session.get("revoked")
            or session["expires_at"] <= deps.clock()):
        raise InvalidCredentials("session has ended; please log in again")
    return session


def lock_session(deps: Deps, customer_id: UUID, session_id: str) -> dict:
    _own_session(deps, customer_id, session_id)
    deps.docstore.set("sessions", session_id, {"locked": True, "locked_reason": "BY_CUSTOMER"}, merge=True)
    return {"state": "LOCKED"}


def unlock_session(conn: psycopg.Connection, deps: Deps, customer_id: UUID, session_id: str, *,
                   pin: str | None = None, password: str | None = None) -> dict:
    """Unlock with the app PIN (or the password, e.g. before a PIN is set). Five wrong
    passwords, or the PIN locking, end the session."""
    from . import pins

    session = _own_session(deps, customer_id, session_id)
    if pin:
        outcome = pins.verify(conn, deps, customer_id, "APP", pin)
        if outcome["result"] == "LOCKED":
            end_session(deps, session_id, "PIN_LOCKED")
            raise InvalidCredentials("your app PIN is locked; sign in again with your password",
                                     locked_until=outcome.get("locked_until"))
        if outcome["result"] == "NOT_SET":
            raise PinNotSet("you have no app PIN yet; unlock with your password", kind="APP")
        ok = outcome["result"] == "OK"
        attempts_left = outcome.get("attempts_left")
    elif password:
        with _cur(conn) as cur:
            cur.execute("SELECT password_hash FROM identity.credentials WHERE customer_id = %s", (customer_id,))
            row = cur.fetchone()
        ok = bool(row) and security.verify_password(password, row["password_hash"])
        failures = 0 if ok else session.get("unlock_failures", 0) + 1
        if failures >= MAX_UNLOCK_FAILURES:
            end_session(deps, session_id, "UNLOCK_FAILED")
            raise InvalidCredentials("too many wrong passwords; sign in again")
        deps.docstore.set("sessions", session_id, {"unlock_failures": failures}, merge=True)
        attempts_left = MAX_UNLOCK_FAILURES - failures
    else:
        raise IdentityError("enter your app PIN or your password")
    if not ok:
        raise WrongSecret("that is not right", attempts_left=attempts_left)
    deps.docstore.set("sessions", session_id, {"locked": False, "locked_reason": None,
                                                "last_seen": deps.clock(), "unlock_failures": 0}, merge=True)
    SESSIONS.labels("unlocked").inc()
    return {"state": "ACTIVE"}


def end_session(deps: Deps, session_id: str, reason: str) -> None:
    deps.docstore.set("sessions", session_id, {"revoked": True, "ended_reason": reason}, merge=True)


def end_all_sessions(deps: Deps, customer_id, reason: str, keep: str | None = None) -> int:
    ended = 0
    for session in deps.docstore.query("sessions", {"customer_id": str(customer_id)}, limit=500):
        if not session.get("revoked") and session["id"] != keep:
            end_session(deps, session["id"], reason)
            ended += 1
    return ended


def logout(deps: Deps, customer_id: UUID, session_id: str) -> None:
    session = deps.docstore.get("sessions", session_id)
    if session and session.get("customer_id") == str(customer_id):
        end_session(deps, session_id, "LOGOUT")


# ------------------------------------------------------------------ devices and sessions


def list_devices(conn: psycopg.Connection, customer_id: UUID) -> list[dict]:
    with _cur(conn) as cur:
        cur.execute(
            "SELECT device_id, label, first_seen, last_seen, last_ip, trusted_at IS NOT NULL AS trusted "
            "FROM identity.devices WHERE customer_id = %s AND revoked_at IS NULL ORDER BY last_seen DESC",
            (customer_id,))
        return cur.fetchall()


def revoke_device(conn: psycopg.Connection, deps: Deps, customer_id: UUID, device_id: UUID) -> dict:
    """'Log out this device': the device is forgotten and its sessions are ended."""
    with conn.transaction(), _cur(conn) as cur:
        cur.execute(
            "UPDATE identity.devices SET revoked_at = now(), trusted_at = NULL "
            "WHERE device_id = %s AND customer_id = %s AND revoked_at IS NULL RETURNING device_id",
            (device_id, customer_id))
        if cur.fetchone() is None:
            raise NotFound("device not found")
        _event(cur, str(customer_id), "DeviceRevoked", {"customer_id": str(customer_id),
                                                         "device_id": str(device_id)})
    ended = 0
    for session in deps.docstore.query("sessions", {"customer_id": str(customer_id),
                                                    "device_id": str(device_id)}, limit=200):
        if not session.get("revoked"):
            end_session(deps, session["id"], "DEVICE_REVOKED")
            ended += 1
    return {"device_id": device_id, "sessions_ended": ended}


def list_sessions(deps: Deps, customer_id: UUID, current: str | None = None) -> list[dict]:
    sessions = deps.docstore.query("sessions", {"customer_id": str(customer_id)}, order_by="created_at",
                                   descending=True, limit=50)
    now = deps.clock()
    return [{"session_id": s["id"], "device_id": s["device_id"], "ip": s.get("ip"),
             "user_agent": s.get("user_agent"), "created_at": s["created_at"],
             "last_seen": s.get("last_seen"), "locked": bool(s.get("locked")), "current": s["id"] == current}
            for s in sessions if not s.get("revoked") and s["expires_at"] > now]


def end_other_session(deps: Deps, customer_id: UUID, session_id: str) -> None:
    session = deps.docstore.get("sessions", session_id)
    if session is None or session.get("customer_id") != str(customer_id):
        raise NotFound("session not found")
    end_session(deps, session_id, "ENDED_BY_CUSTOMER")


def lockdown(conn: psycopg.Connection, deps: Deps, customer_id: UUID, actor: str) -> dict:
    """'Block everything' (identity's part): every session ends, everywhere.
    The gateway also freezes the accounts and opens a fraud case."""
    with conn.transaction(), _cur(conn) as cur:
        cur.execute("SELECT email FROM identity.credentials WHERE customer_id = %s", (customer_id,))
        row = cur.fetchone()
        if row is None:
            raise NotFound("customer not found")
        _event(cur, str(customer_id), "CustomerLockdown", {"customer_id": str(customer_id), "actor": actor})
    ended = end_all_sessions(deps, customer_id, "LOCKDOWN")
    _signal(deps, "put", f"sig:cust:{customer_id}:lockdown", ttl=7 * 86400)
    deps.notifier.send(customer_id, "SECURITY", "Everything is blocked",
                       "You blocked your accounts and signed out everywhere. Money cannot leave your "
                       "accounts until the bank has spoken to you.",
                       email=row["email"])
    return {"sessions_ended": ended}


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
    # Staff sessions are checked like customers' (so they can be ended), and end after
    # `staff_idle_seconds` without activity.
    session_id = uuid.uuid4().hex
    now = deps.clock()
    deps.docstore.set("sessions", session_id, {
        "kind": "staff", "staff_id": staff["staff_id"], "customer_id": None, "device_id": "",
        "created_at": now, "last_seen": now, "expires_at": now + STAFF_TOKEN_SECONDS, "revoked": False})
    token = issue_token(subject=staff["staff_id"], role="staff", secret=deps.jwt_secret,
                        ttl_seconds=STAFF_TOKEN_SECONDS, staff_role=staff["role"], sid=session_id)
    return {"access_token": token, "token_type": "Bearer", "expires_in": STAFF_TOKEN_SECONDS,
            "idle_timeout_seconds": deps.staff_idle_seconds, "staff_id": staff["staff_id"], "role": staff["role"]}


def staff_logout(deps: Deps, staff_id: str, session_id: str) -> None:
    session = deps.docstore.get("sessions", session_id)
    if session and session.get("kind") == "staff" and session.get("staff_id") == staff_id:
        end_session(deps, session_id, "LOGOUT")
