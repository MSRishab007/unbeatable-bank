"""Transaction Intake REST API.

/v1/...           customer endpoints, reached through the API Gateway
/internal/v1/...  staff and service endpoints (fraud review, teller cash deposits)

The caller comes from the X-Actor header set by the gateway after it has
verified the JWT: 'customer:<uuid>' or 'staff:<id>:<ROLE>'.

Environment:
    DATABASE_URL, ACCOUNT_URL, LEDGER_GRPC          required
    RATELIMIT_GRPC, RISK_GRPC, IDENTITY_URL         optional; without them the step is skipped
"""

import os
from contextlib import asynccontextmanager
from typing import Literal
from uuid import UUID

from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse
from psycopg_pool import ConnectionPool
from pydantic import BaseModel, Field

from ubcommon.dbpool import connection_dependency
from ubcommon.auth import parse_actor
from ubcommon.logging_setup import setup_logging
from ubcommon.metrics import instrument

from . import service
from .clients import (
    DevIdentity, GrpcLedgerClient, GrpcRateLimiter, GrpcRiskClient, HttpAccountClient,
    HttpIdentityClient, NoRateLimiter, NoRisk,
)

_state: dict = {}


def build_deps() -> service.Deps:
    return service.Deps(
        accounts=HttpAccountClient(),
        ledger=GrpcLedgerClient(),
        limiter=GrpcRateLimiter() if os.getenv("RATELIMIT_GRPC") else NoRateLimiter(),
        risk=GrpcRiskClient() if os.getenv("RISK_GRPC") else NoRisk(),
        identity=HttpIdentityClient() if os.getenv("IDENTITY_URL") else DevIdentity(),
    )


@asynccontextmanager
async def lifespan(_: FastAPI):
    _state["pool"] = ConnectionPool(
        os.environ["DATABASE_URL"], min_size=1, max_size=int(os.getenv("DB_POOL_MAX", "5")),
        kwargs={"autocommit": True}, open=True,
    )
    _state.setdefault("deps", build_deps())
    yield
    _state["pool"].close()


setup_logging("intake")
app = FastAPI(title="Transaction Intake", version="0.2.0", lifespan=lifespan)
instrument(app, "intake")


# Waits for a free connection without holding a worker thread (see ubcommon.dbpool).
get_conn = connection_dependency(lambda: _state["pool"])


def get_deps() -> service.Deps:
    return _state["deps"]


def get_customer(x_actor: str = Header(..., alias="X-Actor")) -> UUID:
    actor = parse_actor(x_actor)
    if actor.customer_id is None:
        raise HTTPException(status_code=403, detail="only customers can make payments")
    return actor.customer_id


def get_staff(x_actor: str = Header(..., alias="X-Actor")) -> str:
    actor = parse_actor(x_actor)
    if actor.kind not in ("staff", "system"):
        raise HTTPException(status_code=403, detail="staff only")
    return actor.header()


@app.exception_handler(service.IntakeError)
def intake_error(_: Request, exc: service.IntakeError):
    return JSONResponse(status_code=exc.http_status,
                        content={"error": exc.code, "message": str(exc), "details": exc.details})


class PaymentIn(BaseModel):
    from_account_id: UUID
    payee: str = Field(..., description="12-digit account number or alias like riya@unbeatable",
                       examples=["500100000016", "riya@unbeatable"])
    amount: int = Field(..., gt=0, description="paise")
    note: str | None = Field(None, max_length=140)
    channel: Literal["TRANSFER", "QR_PAYMENT"] = "TRANSFER"


class ConfirmIn(BaseModel):
    otp: str = Field(..., min_length=4, max_length=8)


class CancelIn(BaseModel):
    code: Literal["FRAUD_CONFIRMED"] = "FRAUD_CONFIRMED"


class CashDepositIn(BaseModel):
    account_number: str
    amount: int = Field(..., gt=0, description="paise")


# 201 completed, 202 waiting (OTP or review), 422 declined. A replay of a completed payment is 200.
_STATUS_CODE = {"COMPLETED": 201, "DECLINED": 422, "PROCESSING": 202, "CHALLENGE": 202, "HELD": 202}


def _respond(txn: dict, replayed: bool = False, created: bool = True) -> JSONResponse:
    code = _STATUS_CODE[txn["status"]]
    if code == 201 and (replayed or not created):
        code = 200
    return JSONResponse(status_code=code, content=jsonable_encoder({**txn, "replayed": replayed}))


@app.get("/healthz")
async def healthz():
    # async: answered on the event loop, so a busy thread pool (every worker waiting
    # for a database connection under load) can't make the liveness probe time out.
    return {"status": "ok"}


@app.get("/readyz")
def readyz(conn=Depends(get_conn)):
    conn.execute("SELECT 1")
    return {"status": "ready"}


# ----------------------------------------------------------------- customers


@app.post("/v1/payments")
def create_payment(
    body: PaymentIn,
    idempotency_key: str = Header(..., alias="Idempotency-Key"),
    device_id: str = Header("", alias="X-Device-Id"),
    client_ip: str = Header("", alias="X-Client-Ip"),
    customer_id: UUID = Depends(get_customer),
    conn=Depends(get_conn), deps=Depends(get_deps),
):
    txn, replayed = service.submit_payment(
        conn, deps, customer_id=customer_id, idempotency_key=idempotency_key,
        from_account_id=body.from_account_id, payee=body.payee, amount=body.amount,
        note=body.note, channel=body.channel, device_id=device_id, ip=client_ip,
    )
    return _respond(txn, replayed)


@app.get("/v1/payments")
def list_payments(limit: int = 50, customer_id: UUID = Depends(get_customer), conn=Depends(get_conn)):
    return service.list_transactions(conn, customer_id, limit)


@app.get("/v1/payments/{txn_id}")
def get_payment(txn_id: UUID, customer_id: UUID = Depends(get_customer), conn=Depends(get_conn)):
    return service.get_transaction(conn, txn_id, customer_id)


@app.post("/v1/payments/{txn_id}/confirm")
def confirm_payment(txn_id: UUID, body: ConfirmIn, customer_id: UUID = Depends(get_customer),
                    conn=Depends(get_conn), deps=Depends(get_deps)):
    """Answer an OTP challenge."""
    return _respond(service.confirm_challenge(conn, deps, txn_id, customer_id, body.otp), created=False)


@app.post("/v1/payments/{txn_id}/deny")
def deny_payment(txn_id: UUID, customer_id: UUID = Depends(get_customer),
                 conn=Depends(get_conn), deps=Depends(get_deps)):
    """'This wasn't me': cancels the payment and freezes the account."""
    return JSONResponse(content=jsonable_encoder(service.deny_payment(conn, deps, txn_id, customer_id)))


# ----------------------------------------------------------------- staff and services


@app.get("/internal/v1/payments")
def waiting_payments(status: Literal["HELD", "CHALLENGE"] = "HELD", _: str = Depends(get_staff),
                     conn=Depends(get_conn)):
    return service.list_waiting(conn, status)


@app.get("/internal/v1/payments/{txn_id}")
def staff_get_payment(txn_id: UUID, _: str = Depends(get_staff), conn=Depends(get_conn)):
    return service.get_transaction(conn, txn_id, None)


@app.post("/internal/v1/payments/{txn_id}/release")
def release_payment(txn_id: UUID, actor: str = Depends(get_staff), conn=Depends(get_conn),
                    deps=Depends(get_deps)):
    return JSONResponse(content=jsonable_encoder(service.release_held(conn, deps, txn_id, actor)))


@app.post("/internal/v1/payments/{txn_id}/cancel")
def cancel_payment(txn_id: UUID, body: CancelIn | None = None, actor: str = Depends(get_staff),
                   conn=Depends(get_conn), deps=Depends(get_deps)):
    return JSONResponse(content=jsonable_encoder(
        service.cancel_held(conn, deps, txn_id, actor, (body or CancelIn()).code)))


@app.post("/internal/v1/cash-deposits", status_code=201)
def cash_deposit(body: CashDepositIn, idempotency_key: str = Header(..., alias="Idempotency-Key"),
                 actor: str = Depends(get_staff), deps=Depends(get_deps)):
    """Teller counter: simulated cash paid into an account."""
    return service.cash_deposit(deps, account_number=body.account_number, amount=body.amount,
                                idempotency_key=idempotency_key, actor=actor)
