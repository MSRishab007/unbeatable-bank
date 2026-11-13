"""Fraud Alert REST API and event consumer.

/v1/notifications, /v1/reports     customers (through the gateway)
/v1/alerts, /v1/cases, /v1/approvals, /v1/analytics   staff
/internal/v1/events                event intake over HTTP (used when Kafka isn't running)

Environment:
    DATABASE_URL        cases database
    INTAKE_URL, ACCOUNT_URL
    KAFKA_BOOTSTRAP     consume txn-events, risk-signals, auth-events, analytics-results
    DOCSTORE, PUBSUB    see ubcommon
    DYNASTORE_NODES     optional: feed signals back to the risk engine
"""

import logging
import os
from contextlib import asynccontextmanager
from typing import Literal
from uuid import UUID

from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.responses import JSONResponse
from psycopg_pool import ConnectionPool
from pydantic import BaseModel, Field

from ubcommon.dbpool import connection_dependency, readiness
from ubcommon.auth import parse_actor
from ubcommon.docstore import get_docstore
from ubcommon.logging_setup import setup_logging
from ubcommon.metrics import instrument
from ubcommon.notify import get_notifier

from . import service
from .clients import HttpAccounts, HttpIntake, Unavailable

log = logging.getLogger("fraud")
TOPICS = ["txn-events", "risk-signals", "auth-events", "analytics-results"]
_state: dict = {}


def build_deps() -> service.Deps:
    docstore = get_docstore()
    signals = None
    if os.getenv("DYNASTORE_NODES"):
        from ubcommon.dynaclient import DynaClient
        signals = DynaClient(timeout=0.5)
    return service.Deps(docstore=docstore, notifier=get_notifier(docstore), intake=HttpIntake(),
                        accounts=HttpAccounts(), signals=signals,
                        notify_outcomes=os.getenv("NOTIFY_TXN_OUTCOMES", "true").lower() == "true")


def _consume(topic: str, event: dict) -> None:
    with _state["pool"].connection() as conn:
        service.handle_event(conn, _state["deps"], topic, event)


@asynccontextmanager
async def lifespan(_: FastAPI):
    _state["pool"] = ConnectionPool(os.environ["DATABASE_URL"], min_size=1,
                                    max_size=int(os.getenv("DB_POOL_MAX", "5")),
                                    kwargs={"autocommit": True}, open=True)
    _state.setdefault("deps", build_deps())
    stop = None
    if os.getenv("KAFKA_BOOTSTRAP"):
        from ubcommon.kafka import start_consumer_thread
        stop = start_consumer_thread(os.environ["KAFKA_BOOTSTRAP"], "fraud-alert", TOPICS, _consume)
    yield
    if stop:
        stop.set()
    _state["pool"].close()


setup_logging("fraud")
app = FastAPI(title="Fraud Alert", version="0.1.0", lifespan=lifespan)
instrument(app, "fraud")


# Waits for a free connection without holding a worker thread (see ubcommon.dbpool).
get_conn = connection_dependency(lambda: _state["pool"])


def get_deps() -> service.Deps:
    return _state["deps"]


def get_customer(x_actor: str = Header(..., alias="X-Actor")) -> UUID:
    actor = parse_actor(x_actor)
    if actor.customer_id is None:
        raise HTTPException(status_code=403, detail="customers only")
    return actor.customer_id


def get_staff(x_actor: str = Header(..., alias="X-Actor")) -> str:
    actor = parse_actor(x_actor)
    if actor.kind != "staff":
        raise HTTPException(status_code=403, detail="staff only")
    return actor.header()


@app.exception_handler(service.FraudError)
def fraud_error(_: Request, exc: service.FraudError):
    return JSONResponse(status_code=exc.http_status,
                        content={"error": exc.code, "message": str(exc), "details": exc.details})


@app.exception_handler(Unavailable)
def unavailable(_: Request, exc: Unavailable):
    return JSONResponse(status_code=503, content={"error": "UPSTREAM_UNAVAILABLE", "message": str(exc)})


class ReportIn(BaseModel):
    txn_id: UUID
    description: str = Field(..., min_length=5, max_length=1000)


class LockdownIn(BaseModel):
    account_ids: list[UUID] = Field(default_factory=list, max_length=50)


class ResolveIn(BaseModel):
    resolution: Literal["CONFIRMED_FRAUD", "FALSE_POSITIVE"]
    note: str = Field(..., min_length=3, max_length=2000)


class ApprovalIn(BaseModel):
    action: Literal["UNFREEZE_ACCOUNT"] = "UNFREEZE_ACCOUNT"
    target_id: UUID
    reason: str = Field(..., min_length=5)


class EventIn(BaseModel):
    topic: str
    key: str | None = None
    value: dict


@app.get("/healthz")
async def healthz():
    # async: answered on the event loop, so a busy thread pool (every worker waiting
    # for a database connection under load) can't make the liveness probe time out.
    return {"status": "ok"}


# Ready while the pool is open; no query, so a busy pod isn't taken out (ubcommon.dbpool).
app.get("/readyz")(readiness(lambda: _state["pool"]))


# ----------------------------------------------------------------- customers


@app.get("/v1/notifications")
def notifications(limit: int = 50, customer_id: UUID = Depends(get_customer), deps=Depends(get_deps)):
    return service.list_notifications(deps, customer_id, limit)


@app.post("/v1/notifications/{notification_id}/read", status_code=204)
def read_notification(notification_id: str, customer_id: UUID = Depends(get_customer), deps=Depends(get_deps)):
    service.mark_read(deps, customer_id, notification_id)


@app.post("/v1/reports", status_code=201)
def report(body: ReportIn, customer_id: UUID = Depends(get_customer), conn=Depends(get_conn),
           deps=Depends(get_deps)):
    return service.report_fraud(conn, deps, customer_id, body.txn_id, body.description)


@app.post("/v1/lockdown", status_code=201)
def lockdown(body: LockdownIn, customer_id: UUID = Depends(get_customer), conn=Depends(get_conn),
             deps=Depends(get_deps)):
    return service.customer_lockdown(conn, deps, customer_id, body.account_ids)


@app.get("/v1/reports")
def my_reports(customer_id: UUID = Depends(get_customer), conn=Depends(get_conn)):
    return service.my_cases(conn, customer_id)


# ----------------------------------------------------------------- staff


@app.get("/v1/alerts")
def alerts(limit: int = 100, _: str = Depends(get_staff), deps=Depends(get_deps)):
    return service.list_alerts(deps, limit)


@app.get("/v1/cases")
def cases(status: Literal["OPEN", "RESOLVED", "ALL"] = "OPEN", _: str = Depends(get_staff),
          conn=Depends(get_conn)):
    return service.list_cases(conn, None if status == "ALL" else status)


@app.get("/v1/cases/{case_id}")
def case(case_id: UUID, _: str = Depends(get_staff), conn=Depends(get_conn), deps=Depends(get_deps)):
    return service.get_case(conn, deps, case_id)


@app.post("/v1/cases/{case_id}/resolve")
def resolve(case_id: UUID, body: ResolveIn, actor: str = Depends(get_staff), conn=Depends(get_conn),
            deps=Depends(get_deps)):
    return service.resolve_case(conn, deps, case_id, resolution=body.resolution, note=body.note, actor=actor)


@app.get("/v1/approvals")
def approvals(status: Literal["PENDING", "APPROVED", "REJECTED", "ALL"] = "PENDING",
              _: str = Depends(get_staff), conn=Depends(get_conn)):
    return service.list_approvals(conn, None if status == "ALL" else status)


@app.post("/v1/approvals", status_code=201)
def request_approval(body: ApprovalIn, actor: str = Depends(get_staff), conn=Depends(get_conn)):
    return service.request_approval(conn, action=body.action, target_id=body.target_id, reason=body.reason,
                                    actor=actor)


@app.post("/v1/approvals/{approval_id}/approve")
def approve(approval_id: UUID, actor: str = Depends(get_staff), conn=Depends(get_conn), deps=Depends(get_deps)):
    return service.decide_approval(conn, deps, approval_id, approve=True, actor=actor)


@app.post("/v1/approvals/{approval_id}/reject")
def reject(approval_id: UUID, actor: str = Depends(get_staff), conn=Depends(get_conn), deps=Depends(get_deps)):
    return service.decide_approval(conn, deps, approval_id, approve=False, actor=actor)


@app.get("/v1/analytics")
def analytics(_: str = Depends(get_staff), conn=Depends(get_conn), deps=Depends(get_deps)):
    return service.analytics(conn, deps)


# ----------------------------------------------------------------- events over HTTP


@app.post("/internal/v1/events")
def ingest(body: EventIn, conn=Depends(get_conn), deps=Depends(get_deps)):
    """The same handler the Kafka consumer uses. Lets the system run without Kafka locally."""
    return {"handled": service.handle_event(conn, deps, body.topic, body.value)}
