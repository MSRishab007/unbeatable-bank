"""Ledger service REST API.

Internal-only: called by Account, Transaction Intake, Deposits and the EOD engine.
A gRPC interface for the Intake hot path will wrap the same posting functions.
"""

from contextlib import asynccontextmanager
from datetime import date, datetime
from typing import Literal
from uuid import UUID

from fastapi import Depends, FastAPI, Header, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from . import posting
from .db import close_pool, init_pool


@asynccontextmanager
async def lifespan(_: FastAPI):
    init_pool()
    yield
    close_pool()


app = FastAPI(title="Ledger Service", version="0.1.0", lifespan=lifespan)


def get_conn():
    with init_pool().connection() as conn:
        yield conn


@app.exception_handler(posting.LedgerError)
def ledger_error_handler(_: Request, exc: posting.LedgerError):
    return JSONResponse(
        status_code=exc.http_status,
        content={"error": exc.code, "message": str(exc), "details": exc.details},
    )


# ----------------------------------------------------------------- models

ProductType = Literal["SAVINGS", "CURRENT", "FD", "RD"]


class OpenAccountIn(BaseModel):
    account_id: UUID
    product_type: ProductType
    overdraft_limit: int = Field(0, ge=0)


class UpdateAccountIn(BaseModel):
    status: Literal["OPEN", "DEBIT_BLOCKED", "CLOSED"] | None = None
    overdraft_limit: int | None = Field(None, ge=0)


class LegIn(BaseModel):
    account_id: UUID
    amount: int = Field(..., description="paise; positive = credit, negative = debit")


class JournalIn(BaseModel):
    txn_type: str
    legs: list[LegIn] = Field(..., min_length=2)
    description: str | None = None
    metadata: dict = Field(default_factory=dict)
    business_date: date | None = None
    capture_hold_id: UUID | None = None


class ReverseIn(BaseModel):
    reason: str


class HoldIn(BaseModel):
    account_id: UUID
    amount: int = Field(..., gt=0)
    reason: str
    expires_at: datetime | None = None


# ----------------------------------------------------------------- routes


@app.get("/healthz")
def healthz():
    return {"status": "ok"}


@app.get("/readyz")
def readyz(conn=Depends(get_conn)):
    conn.execute("SELECT 1")
    return {"status": "ready"}


@app.post("/v1/accounts", status_code=201)
def open_account(body: OpenAccountIn, conn=Depends(get_conn)):
    return posting.open_account(conn, **body.model_dump())


@app.patch("/v1/accounts/{account_id}")
def update_account(account_id: UUID, body: UpdateAccountIn, conn=Depends(get_conn)):
    return posting.update_account(conn, account_id, **body.model_dump())


@app.get("/v1/accounts/{account_id}/balance")
def balance(account_id: UUID, conn=Depends(get_conn)):
    return posting.get_balance(conn, account_id)


@app.get("/v1/accounts/{account_id}/entries")
def entries(account_id: UUID, before: int | None = None, limit: int = 50, conn=Depends(get_conn)):
    posting.get_balance(conn, account_id)  # 404 if unknown
    return posting.list_entries(conn, account_id, before_entry_id=before, limit=limit)


@app.post("/v1/journals")
def post_journal(
    body: JournalIn,
    idempotency_key: str = Header(..., alias="Idempotency-Key"),
    conn=Depends(get_conn),
):
    result = posting.post_journal(
        conn,
        idempotency_key=idempotency_key,
        txn_type=body.txn_type,
        legs=[posting.Leg(l.account_id, l.amount) for l in body.legs],
        description=body.description,
        metadata=body.metadata,
        business_date=body.business_date,
        capture_hold_id=body.capture_hold_id,
    )
    # 201 for a new posting, 200 when replaying an already-posted key.
    return JSONResponse(
        status_code=200 if result.replayed else 201,
        content={
            "journal_id": str(result.journal_id),
            "replayed": result.replayed,
            "txn_type": result.txn_type,
            "entries": [
                {"account_id": str(e["account_id"]), "amount": e["amount"],
                 "balance_after": e["balance_after"]}
                for e in result.entries
            ],
        },
    )


@app.post("/v1/journals/{journal_id}/reverse")
def reverse(
    journal_id: UUID,
    body: ReverseIn,
    idempotency_key: str = Header(..., alias="Idempotency-Key"),
    conn=Depends(get_conn),
):
    result = posting.reverse_journal(conn, journal_id, idempotency_key=idempotency_key, reason=body.reason)
    return {"journal_id": str(result.journal_id), "replayed": result.replayed}


@app.post("/v1/holds", status_code=201)
def create_hold(
    body: HoldIn,
    idempotency_key: str = Header(..., alias="Idempotency-Key"),
    conn=Depends(get_conn),
):
    return posting.create_hold(conn, idempotency_key=idempotency_key, **body.model_dump())


@app.post("/v1/holds/{hold_id}/release")
def release_hold(hold_id: UUID, conn=Depends(get_conn)):
    return posting.release_hold(conn, hold_id)


@app.get("/v1/reconciliation")
def reconciliation(conn=Depends(get_conn)):
    return posting.reconcile(conn)
