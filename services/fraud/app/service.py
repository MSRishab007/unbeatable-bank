"""Fraud Alert: turns events into alerts, cases and protective action.

Inputs (Kafka topics):
    txn-events         every payment decision, from Transaction Intake
    risk-signals       patterns found by the Flink job (bursts, mule fan-in, probing, structuring)
    auth-events        logins, from Identity & Auth
    analytics-results  per-minute system statistics, from the Flink job

What it does with them:
    - keeps an alert feed and an analytics snapshot (document store)
    - opens a case for every held payment, customer report and serious pattern
    - freezes accounts when several signals line up, and tells the customer
    - feeds signals back to the risk engine (DynaStore), so stream analytics
      influences the next real-time decision
    - runs the analyst workflow, with maker-checker approval for unfreezing
"""

from __future__ import annotations

import hashlib
import logging
import time
from dataclasses import dataclass
from typing import Any
from uuid import UUID

import psycopg
from prometheus_client import Counter
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from ubcommon.docstore import DocStore
from ubcommon.notify import Notifier

from .clients import SYSTEM_ACTOR, AccountClient, IntakeClient

log = logging.getLogger("fraud")

EVENTS = Counter("fraud_events_total", "Events handled", ["topic", "event_type"])
ALERTS = Counter("fraud_alerts_total", "Alerts raised", ["type", "severity"])
CASES = Counter("fraud_cases_total", "Cases opened and resolved", ["type", "outcome"])
FREEZES = Counter("fraud_auto_freezes_total", "Accounts frozen automatically")

SIGNAL_WINDOW_MINUTES = 10
SIGNAL_TTL = 1800
SEVERE_SIGNALS = {"MULE_FAN_IN"}       # serious enough to act on alone
DENY_TTL = 30 * 86400


class FraudError(Exception):
    code = "FRAUD_ERROR"
    http_status = 400

    def __init__(self, message: str, **details: Any) -> None:
        super().__init__(message)
        self.details = details


class NotFound(FraudError):
    code = "NOT_FOUND"
    http_status = 404


class Conflict(FraudError):
    code = "CONFLICT"
    http_status = 409


class Forbidden(FraudError):
    code = "FORBIDDEN"
    http_status = 403


@dataclass
class Deps:
    docstore: DocStore
    notifier: Notifier
    intake: IntakeClient
    accounts: AccountClient
    signals: Any = None            # DynaClient, optional
    notify_outcomes: bool = True   # tell the payer about every completed / declined payment


def _cur(conn: psycopg.Connection) -> psycopg.Cursor:
    return conn.cursor(row_factory=dict_row)


def _rupees(paise: int | None) -> str:
    return f"Rs {(paise or 0) / 100:,.2f}"


def _mid(*parts) -> str:
    """Deterministic message id: handling the same event twice doesn't notify twice."""
    return hashlib.sha1(":".join(str(p) for p in parts).encode()).hexdigest()


# ------------------------------------------------------------------ building blocks


def raise_alert(deps: Deps, alert_id: str, *, type: str, severity: str, title: str, account_id=None,
                customer_id=None, case_id=None, detail: dict | None = None) -> None:
    deps.docstore.set("alerts", alert_id, {
        "type": type, "severity": severity, "title": title,
        "account_id": str(account_id) if account_id else None,
        "customer_id": str(customer_id) if customer_id else None,
        "case_id": str(case_id) if case_id else None,
        "detail": detail or {}, "created_at": time.time(), "expires_at": time.time() + 30 * 86400})
    ALERTS.labels(type, severity).inc()


def open_case(conn: psycopg.Connection, *, type: str, severity: str, summary: str, customer_id=None,
              account_id=None, txn_id=None, reasons: list | None = None, actor: str = SYSTEM_ACTOR) -> dict:
    """Idempotent per transaction, and per account for open PATTERN cases."""
    with conn.transaction(), _cur(conn) as cur:
        cur.execute(
            """
            INSERT INTO fraud.cases (type, severity, summary, customer_id, account_id, txn_id, reasons)
            VALUES (%s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT DO NOTHING
            RETURNING *
            """,
            (type, severity, summary, customer_id, account_id, txn_id, Jsonb(reasons or [])),
        )
        case = cur.fetchone()
        if case is not None:
            cur.execute("INSERT INTO fraud.case_events (case_id, event_type, detail, actor) VALUES (%s, %s, %s, %s)",
                        (case["case_id"], "OPENED", Jsonb({"summary": summary, "reasons": reasons or []}), actor))
            CASES.labels(type, "opened").inc()
            return case
        if txn_id is not None:
            cur.execute("SELECT * FROM fraud.cases WHERE txn_id = %s", (txn_id,))
        else:
            cur.execute("SELECT * FROM fraud.cases WHERE account_id = %s AND type = %s AND status = 'OPEN'",
                        (account_id, type))
        return cur.fetchone()


def add_case_event(conn: psycopg.Connection, case_id: UUID, event_type: str, detail: dict, actor: str) -> None:
    conn.execute("INSERT INTO fraud.case_events (case_id, event_type, detail, actor) VALUES (%s, %s, %s, %s)",
                 (case_id, event_type, Jsonb(detail), actor))


def _customer_email(deps: Deps, customer_id) -> str | None:
    try:
        customer = deps.accounts.get_customer(UUID(str(customer_id)))
        return customer.get("email") if customer else None
    except Exception:
        return None


def _signal(deps: Deps, key: str, ttl: int = SIGNAL_TTL) -> None:
    if deps.signals is None:
        return
    try:
        deps.signals.put(key, repr(time.time()), ttl=ttl, w=1)
    except Exception:
        log.warning("could not write signal %s", key)


# ------------------------------------------------------------------ event handling


def handle_event(conn: psycopg.Connection, deps: Deps, topic: str, event: dict) -> bool:
    """Handle one event. Safe to call again with the same event. Returns False if it was a duplicate."""
    event_id = event.get("event_id") or _mid(topic, event)
    with _cur(conn) as cur:
        cur.execute("SELECT 1 FROM fraud.processed_events WHERE event_id = %s", (event_id,))
        if cur.fetchone():
            return False
    handler = {"txn-events": _on_txn, "risk-signals": _on_signal, "auth-events": _on_auth,
               "analytics-results": _on_analytics}.get(topic)
    if handler:
        handler(conn, deps, event_id, event)
    EVENTS.labels(topic, event.get("event_type") or event.get("signal_type") or "").inc()
    # Marked last: if anything above failed, the event is delivered again and every step is idempotent.
    conn.execute("INSERT INTO fraud.processed_events (event_id) VALUES (%s) ON CONFLICT DO NOTHING", (event_id,))
    return True


def _on_txn(conn, deps: Deps, event_id: str, e: dict) -> None:
    status, code, customer = e.get("status"), e.get("decline_code"), e.get("customer_id")
    amount, txn_id, account = _rupees(e.get("amount")), e.get("txn_id"), e.get("from_account_id")
    reasons = e.get("risk_reasons") or []

    if status == "HELD":
        severity = "HIGH" if (e.get("risk_score") or 0) >= 75 else "MEDIUM"
        case = open_case(conn, type="HELD_PAYMENT", severity=severity, customer_id=customer, account_id=account,
                         txn_id=txn_id, reasons=reasons,
                         summary=f"Payment of {amount} held: {', '.join(reasons) or 'risk rules'}")
        raise_alert(deps, _mid(event_id, "alert"), type="HELD_PAYMENT", severity=severity,
                    title=f"{amount} payment held for review", account_id=account, customer_id=customer,
                    case_id=case["case_id"], detail={"txn_id": txn_id, "reasons": reasons, "score": e.get("risk_score")})
        deps.notifier.send(customer, "FRAUD", "Was this you?",
                           f"A payment of {amount} from your account is on hold because it looks unusual. "
                           "If you made it, no action is needed and we'll review it shortly. "
                           "If you didn't, tap 'This wasn't me' and we'll cancel it and secure your account.",
                           email=_customer_email(deps, customer),
                           data={"txn_id": txn_id, "action": "CONFIRM_OR_DENY"}, message_id=_mid(event_id, "n"))
    elif status == "CHALLENGE":
        pass   # Identity has already sent the OTP
    elif status == "DECLINED" and code == "RISK_BLOCKED":
        raise_alert(deps, _mid(event_id, "alert"), type="BLOCKED_PAYMENT", severity="HIGH",
                    title=f"{amount} payment blocked", account_id=account, customer_id=customer,
                    detail={"txn_id": txn_id, "reasons": reasons, "score": e.get("risk_score")})
        deps.notifier.send(customer, "FRAUD", "We blocked a payment",
                           f"A payment of {amount} from your account was blocked to protect you. "
                           "If this was you, please contact us.", email=_customer_email(deps, customer),
                           data={"txn_id": txn_id}, message_id=_mid(event_id, "n"))
    elif status == "DECLINED" and code == "CUSTOMER_DENIED":
        case = open_case(conn, type="ACCOUNT_TAKEOVER", severity="HIGH", customer_id=customer, account_id=account,
                         txn_id=txn_id, reasons=reasons,
                         summary=f"Customer says they did not make a {amount} payment; account frozen")
        with conn.transaction():
            conn.execute("UPDATE fraud.cases SET severity = 'HIGH', type = 'ACCOUNT_TAKEOVER' WHERE case_id = %s",
                         (case["case_id"],))
            add_case_event(conn, case["case_id"], "CUSTOMER_DENIED",
                           {"note": "customer reported the payment as unauthorised; account frozen"},
                           f"customer:{customer}")
        raise_alert(deps, _mid(event_id, "alert"), type="ACCOUNT_TAKEOVER", severity="HIGH",
                    title="Customer denied a held payment; account frozen", account_id=account,
                    customer_id=customer, case_id=case["case_id"], detail={"txn_id": txn_id})
        deps.notifier.send(customer, "SECURITY", "Your account is frozen",
                           f"We cancelled the {amount} payment and froze your account. No money left your "
                           "account. Our fraud team will contact you.", email=_customer_email(deps, customer),
                           data={"txn_id": txn_id}, message_id=_mid(event_id, "n"))
    elif status == "DECLINED" and code == "FRAUD_CONFIRMED":
        pass   # the analyst's resolution already notified the customer
    elif deps.notify_outcomes and status == "COMPLETED":
        deps.notifier.send(customer, "TXN", f"{amount} sent",
                           f"Your payment of {amount} was completed.", data={"txn_id": txn_id},
                           message_id=_mid(event_id, "n"))
    elif deps.notify_outcomes and status == "DECLINED" and code != "RATE_LIMITED":
        deps.notifier.send(customer, "TXN", f"{amount} payment declined",
                           f"Your payment of {amount} was declined ({(code or '').replace('_', ' ').lower()}).",
                           data={"txn_id": txn_id, "decline_code": code}, message_id=_mid(event_id, "n"))


def _on_signal(conn, deps: Deps, event_id: str, e: dict) -> None:
    signal, account = e.get("signal_type"), e.get("account_id")
    if not signal or not account:
        return
    with conn.transaction(), _cur(conn) as cur:
        # Sliding windows report the same pattern repeatedly; keep one per type per window.
        cur.execute(
            "SELECT 1 FROM fraud.signals WHERE account_id = %s AND signal_type = %s "
            "AND created_at > now() - make_interval(mins => %s)", (account, signal, SIGNAL_WINDOW_MINUTES))
        repeat = cur.fetchone() is not None
        if not repeat:
            cur.execute("INSERT INTO fraud.signals (signal_type, account_id, value) VALUES (%s, %s, %s)",
                        (signal, account, e.get("value")))
        cur.execute(
            "SELECT DISTINCT signal_type FROM fraud.signals WHERE account_id = %s "
            "AND created_at > now() - make_interval(mins => %s)", (account, SIGNAL_WINDOW_MINUTES))
        recent = sorted(r["signal_type"] for r in cur.fetchall())

    # Feed the pattern back to the real-time risk engine.
    _signal(deps, f"sig:mule:{account}" if signal == "MULE_FAN_IN" else f"sig:acct:{account}")
    if repeat:
        return

    serious = signal in SEVERE_SIGNALS or len(recent) >= 2
    severity = "HIGH" if serious else "MEDIUM"
    case = None
    if serious:
        case = open_case(conn, type="PATTERN", severity="HIGH", account_id=account, reasons=recent,
                         summary=f"Stream analytics: {', '.join(recent)} on one account")
        deps.accounts.freeze(UUID(account), f"automatic: {', '.join(recent)}", SYSTEM_ACTOR)
        FREEZES.inc()
        add_case_event(conn, case["case_id"], "ACCOUNT_FROZEN", {"signals": recent}, SYSTEM_ACTOR)
        holder = _primary_holder(deps, account)
        if holder:
            deps.notifier.send(holder, "SECURITY", "Your account is frozen",
                               "We noticed unusual activity on your account and froze it to protect you. "
                               "Please contact us to restore access.", email=_customer_email(deps, holder),
                               data={"account_id": account}, message_id=_mid(case["case_id"], "frozen"))
    raise_alert(deps, _mid(event_id, "alert"), type=signal, severity=severity, account_id=account,
                title=f"{signal.replace('_', ' ').title()} detected" + (" - account frozen" if serious else ""),
                case_id=case["case_id"] if case else None,
                detail={"value": e.get("value"), "window_end": e.get("window_end"), "recent_signals": recent})


def _primary_holder(deps: Deps, account_id: str) -> str | None:
    try:
        account = deps.accounts.get_account(UUID(account_id)) or {}
        return next((str(h["customer_id"]) for h in account.get("holders", []) if h["role"] == "PRIMARY"), None)
    except Exception:
        return None


def _on_auth(conn, deps: Deps, event_id: str, e: dict) -> None:
    customer = e.get("customer_id")
    if e.get("event_type") == "LoginSucceeded" and e.get("impossible_travel"):
        raise_alert(deps, _mid(event_id, "alert"), type="IMPOSSIBLE_TRAVEL", severity="HIGH", customer_id=customer,
                    title="Login from a location too far from the previous one", detail={"ip": e.get("ip")})
        deps.notifier.send(customer, "SECURITY", "Unusual sign-in location",
                           "Your account was accessed from a location far from your last sign-in. "
                           "If this wasn't you, freeze your account now.", email=_customer_email(deps, customer),
                           message_id=_mid(event_id, "n"))
    elif e.get("event_type") == "LoginLocked":
        raise_alert(deps, _mid(event_id, "alert"), type="LOGIN_LOCKED", severity="MEDIUM", customer_id=customer,
                    title="Login locked after repeated wrong passwords", detail={"ip": e.get("ip")})


def _on_analytics(conn, deps: Deps, event_id: str, e: dict) -> None:
    window = str(e.get("window_start") or e.get("window_end") or event_id)
    deps.docstore.set("analytics", hashlib.sha1(window.encode()).hexdigest(),
                      {**e, "stored_at": time.time(), "expires_at": time.time() + 7 * 86400})


# ------------------------------------------------------------------ customer API


def list_notifications(deps: Deps, customer_id: UUID, limit: int = 50) -> list[dict]:
    return deps.docstore.query("notifications", {"customer_id": str(customer_id)}, order_by="created_at",
                               descending=True, limit=limit)


def mark_read(deps: Deps, customer_id: UUID, notification_id: str) -> None:
    doc = deps.docstore.get("notifications", notification_id)
    if doc is None or doc.get("customer_id") != str(customer_id):
        raise NotFound("notification not found")
    deps.docstore.set("notifications", notification_id, {"read": True}, merge=True)


def report_fraud(conn: psycopg.Connection, deps: Deps, customer_id: UUID, txn_id: UUID, description: str) -> dict:
    """A customer flags a payment they don't recognise."""
    txn = deps.intake.get(txn_id)
    if txn is None or str(txn.get("customer_id")) != str(customer_id):
        raise NotFound("payment not found")
    case = open_case(conn, type="CUSTOMER_REPORT", severity="MEDIUM", customer_id=customer_id,
                     account_id=txn["from_account_id"], txn_id=txn_id, reasons=txn.get("risk_reasons") or [],
                     summary=f"Customer reported a {_rupees(txn.get('amount'))} payment: {description[:200]}",
                     actor=f"customer:{customer_id}")
    add_case_event(conn, case["case_id"], "CUSTOMER_REPORT", {"description": description[:1000]},
                   f"customer:{customer_id}")
    raise_alert(deps, _mid(case["case_id"], "report"), type="CUSTOMER_REPORT", severity="MEDIUM",
                title="Customer reported a payment as fraud", customer_id=customer_id,
                account_id=txn["from_account_id"], case_id=case["case_id"], detail={"txn_id": str(txn_id)})
    return case


def my_cases(conn: psycopg.Connection, customer_id: UUID) -> list[dict]:
    with _cur(conn) as cur:
        cur.execute(
            "SELECT case_id, case_number, type, status, resolution, txn_id, summary, created_at, resolved_at "
            "FROM fraud.cases WHERE customer_id = %s ORDER BY created_at DESC LIMIT 50", (customer_id,))
        return cur.fetchall()


# ------------------------------------------------------------------ analyst API


def list_alerts(deps: Deps, limit: int = 100) -> list[dict]:
    return deps.docstore.query("alerts", order_by="created_at", descending=True, limit=limit)


def list_cases(conn: psycopg.Connection, status: str | None = "OPEN", limit: int = 100) -> list[dict]:
    with _cur(conn) as cur:
        cur.execute(
            "SELECT * FROM fraud.cases WHERE (%s::text IS NULL OR status = %s) "
            "ORDER BY (severity = 'HIGH') DESC, created_at DESC LIMIT %s", (status, status, min(limit, 500)))
        return cur.fetchall()


def get_case(conn: psycopg.Connection, deps: Deps, case_id: UUID) -> dict:
    with _cur(conn) as cur:
        cur.execute("SELECT * FROM fraud.cases WHERE case_id = %s", (case_id,))
        case = cur.fetchone()
        if case is None:
            raise NotFound("case not found")
        cur.execute("SELECT event_type, detail, actor, created_at FROM fraud.case_events WHERE case_id = %s "
                    "ORDER BY id", (case_id,))
        case["timeline"] = cur.fetchall()
    case["payment"] = None
    if case["txn_id"]:
        try:
            case["payment"] = deps.intake.get(case["txn_id"])
        except Exception:
            pass
    return case


def resolve_case(conn: psycopg.Connection, deps: Deps, case_id: UUID, *, resolution: str, note: str,
                 actor: str) -> dict:
    """CONFIRMED_FRAUD: cancel the held payment, freeze the account, deny-list the payee.
    FALSE_POSITIVE:  release the held payment."""
    if resolution not in ("CONFIRMED_FRAUD", "FALSE_POSITIVE"):
        raise FraudError("resolution must be CONFIRMED_FRAUD or FALSE_POSITIVE")
    case = get_case(conn, deps, case_id)
    if case["status"] == "RESOLVED":
        raise Conflict("case is already resolved", resolution=case["resolution"])
    payment = case["payment"]
    held = payment is not None and payment.get("status") == "HELD"

    if resolution == "CONFIRMED_FRAUD":
        if held:
            deps.intake.cancel(case["txn_id"], actor)
        if case["account_id"]:
            deps.accounts.freeze(case["account_id"], f"fraud confirmed in {case['case_number']}", actor)
        if payment and payment.get("to_account_id"):
            _signal(deps, f"deny:acct:{payment['to_account_id']}", DENY_TTL)
        if payment and payment.get("device_id"):
            _signal(deps, f"deny:dev:{payment['device_id']}", DENY_TTL)
        title, body = "Fraud confirmed on your account", (
            "We confirmed a fraudulent payment attempt. It was cancelled, no money was lost, and your "
            "account stays frozen until we have verified it is safe.")
    else:
        if held:
            deps.intake.release(case["txn_id"], actor)
        title, body = "Your payment has been released", (
            "We reviewed your payment and it has now been completed. Thank you for your patience.")

    with conn.transaction(), _cur(conn) as cur:
        cur.execute(
            "UPDATE fraud.cases SET status = 'RESOLVED', resolution = %s, resolution_note = %s, "
            "resolved_by = %s, resolved_at = now() WHERE case_id = %s AND status = 'OPEN' RETURNING case_id",
            (resolution, note, actor, case_id))
        if cur.fetchone() is None:
            raise Conflict("case is already resolved")
        add_case_event(conn, case_id, "RESOLVED", {"resolution": resolution, "note": note}, actor)
    CASES.labels(case["type"], resolution.lower()).inc()
    if case["customer_id"]:
        deps.notifier.send(case["customer_id"], "FRAUD", title, body,
                           email=_customer_email(deps, case["customer_id"]),
                           data={"case_number": case["case_number"]}, message_id=_mid(case_id, "resolved"))
    return get_case(conn, deps, case_id)


def analytics(conn: psycopg.Connection, deps: Deps) -> dict:
    """Operations view: latest per-minute windows from Flink, plus the case backlog."""
    windows = deps.docstore.query("analytics", order_by="window_start", descending=True, limit=30)
    with _cur(conn) as cur:
        cur.execute("SELECT status, severity, count(*) AS n FROM fraud.cases GROUP BY status, severity")
        backlog = cur.fetchall()
        cur.execute("SELECT count(*) AS n FROM fraud.approvals WHERE status = 'PENDING'")
        pending = cur.fetchone()["n"]
    return {"windows": windows, "cases": backlog, "pending_approvals": pending}


# ------------------------------------------------------------------ maker-checker approvals


def request_approval(conn: psycopg.Connection, *, action: str, target_id: UUID, reason: str, actor: str) -> dict:
    try:
        with _cur(conn) as cur:
            cur.execute(
                "INSERT INTO fraud.approvals (action, target_id, reason, requested_by) VALUES (%s, %s, %s, %s) "
                "RETURNING *", (action, target_id, reason, actor))
            return cur.fetchone()
    except psycopg.errors.UniqueViolation as e:
        raise Conflict("there is already a pending request for this") from e
    except psycopg.errors.CheckViolation as e:
        raise FraudError("invalid request: check the action and give a reason of at least 5 characters") from e


def list_approvals(conn: psycopg.Connection, status: str | None = "PENDING") -> list[dict]:
    with _cur(conn) as cur:
        cur.execute("SELECT * FROM fraud.approvals WHERE (%s::text IS NULL OR status = %s) "
                    "ORDER BY created_at DESC LIMIT 200", (status, status))
        return cur.fetchall()


def decide_approval(conn: psycopg.Connection, deps: Deps, approval_id: UUID, *, approve: bool, actor: str) -> dict:
    """The second pair of eyes. The requester cannot approve their own request."""
    with _cur(conn) as cur:
        cur.execute("SELECT * FROM fraud.approvals WHERE approval_id = %s", (approval_id,))
        approval = cur.fetchone()
    if approval is None:
        raise NotFound("approval request not found")
    if approval["status"] != "PENDING":
        raise Conflict("request was already decided", status=approval["status"])
    if approval["requested_by"] == actor:
        raise Forbidden("a different member of staff must decide this request")
    if approve and approval["action"] == "UNFREEZE_ACCOUNT":
        deps.accounts.unfreeze(approval["target_id"], actor)
    with _cur(conn) as cur:
        cur.execute(
            "UPDATE fraud.approvals SET status = %s, decided_by = %s, decided_at = now() "
            "WHERE approval_id = %s AND status = 'PENDING' RETURNING *",
            ("APPROVED" if approve else "REJECTED", actor, approval_id))
        row = cur.fetchone()
    if row is None:
        raise Conflict("request was already decided")
    return row
