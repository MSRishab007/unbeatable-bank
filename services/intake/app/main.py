"""Transaction Intake REST API (public, behind the API gateway).

Caller identity comes from X-Actor ('customer:<uuid>') until Identity & Auth
exists; the gateway will set it from the verified JWT.
"""

import os
from contextlib import asynccontextmanager
from typing import Literal
from uuid import UUID

from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.responses import JSONResponse
from fastapi.encoders import jsonable_encoder
from psycopg_pool import ConnectionPool
from pydantic import BaseModel, Field

from . import service
from .clients import GrpcLedgerClient, HttpAccountClient

_state: dict = {}


@asynccontextmanager
async def lifespan(app: FastAPI):
    _state["pool"] = ConnectionPool(
        os.environ["DATABASE_URL"], min_size=1, max_size=int(os.getenv("DB_POOL_MAX", "5")),
        kwargs={"autocommit": True}, open=True,
    )
    _state.setdefault("accounts", HttpAccountClient())
    _state.setdefault("ledger", GrpcLedgerClient())
    yield
    _state["pool"].close()


app = FastAPI(title="Transaction Intake", version="0.1.0", lifespan=lifespan)


def get_conn():
    with _state["pool"].connection() as conn:
        yield conn


def get_customer(x_actor: str = Header(..., alias="X-Actor")) -> UUID:
    kind, _, value = x_actor.partition(":")
    try:
        if kind == "customer":
            return UUID(value)
    except ValueError:
        pass
    raise HTTPException(status_code=403, detail="only customers can make payments")


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


# Response code by outcome, same for first attempt and replays (except 201 -> 200 on replay).
_STATUS_CODE = {"COMPLETED": 201, "DECLINED": 422, "PROCESSING": 202}


@app.get("/healthz")
def healthz():
    return {"status": "ok"}


@app.get("/readyz")
def readyz(conn=Depends(get_conn)):
    conn.execute("SELECT 1")
    return {"status": "ready"}


@app.post("/v1/payments")
def create_payment(
    body: PaymentIn,
    idempotency_key: str = Header(..., alias="Idempotency-Key"),
    customer_id: UUID = Depends(get_customer),
    conn=Depends(get_conn),
):
    txn, replayed = service.submit_payment(
        conn, _state["accounts"], _state["ledger"],
        customer_id=customer_id, idempotency_key=idempotency_key,
        from_account_id=body.from_account_id, payee=body.payee, amount=body.amount,
        note=body.note, channel=body.channel,
    )
    code = _STATUS_CODE[txn["status"]]
    if replayed and code == 201:
        code = 200
    return JSONResponse(status_code=code, content=jsonable_encoder({**txn, "replayed": replayed}),
                        headers={"Idempotent-Replayed": str(replayed).lower()})


@app.get("/v1/payments")
def list_payments(limit: int = 50, customer_id: UUID = Depends(get_customer), conn=Depends(get_conn)):
    return service.list_transactions(conn, customer_id, limit)


@app.get("/v1/payments/{txn_id}")
def get_payment(txn_id: UUID, customer_id: UUID = Depends(get_customer), conn=Depends(get_conn)):
    return service.get_transaction(conn, txn_id, customer_id)
