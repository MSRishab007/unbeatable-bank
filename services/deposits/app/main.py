"""Deposits REST API.

/v1/...           customers (through the gateway): rates, quotes, FDs and RDs
/internal/v1/...  operations: the bank clock and end-of-day processing

End of day is triggered by a Kubernetes CronJob calling POST /internal/v1/eod/run.

Environment:
    DATABASE_URL, LEDGER_URL, ACCOUNT_URL
    OBJSTORE=gcs + GCS_BUCKET   where EOD reports and statements are written (local folder otherwise)
    DYNASTORE_NODES             optional: fraud signal when a deposit is closed early
"""

import os
from contextlib import asynccontextmanager
from datetime import date
from typing import Literal
from uuid import UUID

from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.encoders import jsonable_encoder
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import JSONResponse, Response
from psycopg_pool import ConnectionPool
from pydantic import BaseModel, Field

from ubcommon.dbpool import connection_dependency, readiness
from ubcommon.auth import parse_actor
from ubcommon.logging_setup import setup_logging
from ubcommon.metrics import instrument
from ubcommon.objstore import get_objstore

from . import service, statements
from .clients import HttpAccounts, HttpLedger

_state: dict = {}


def build_deps() -> service.Deps:
    signals = None
    if os.getenv("DYNASTORE_NODES"):
        from ubcommon.dynaclient import DynaClient
        signals = DynaClient(timeout=0.5)
    return service.Deps(accounts=HttpAccounts(), ledger=HttpLedger(), objstore=get_objstore(), signals=signals)


@asynccontextmanager
async def lifespan(_: FastAPI):
    _state["pool"] = ConnectionPool(os.environ["DATABASE_URL"], min_size=1,
                                    max_size=int(os.getenv("DB_POOL_MAX", "5")),
                                    kwargs={"autocommit": True}, open=True)
    if "deps" not in _state:            # tests put their own in first
        _state["deps"] = build_deps()
    yield
    _state["pool"].close()


setup_logging("deposits")
app = FastAPI(title="Deposits", version="0.1.0", lifespan=lifespan)
instrument(app, "deposits")


# Waits for a free connection without holding a worker thread (see ubcommon.dbpool).
get_conn = connection_dependency(lambda: _state["pool"])


def get_deps() -> service.Deps:
    return _state["deps"]


def get_customer(x_actor: str = Header(..., alias="X-Actor")) -> UUID:
    actor = parse_actor(x_actor)
    if actor.customer_id is None:
        raise HTTPException(status_code=403, detail="customers only")
    return actor.customer_id


@app.exception_handler(service.DepositError)
def deposit_error(_: Request, exc: service.DepositError):
    return JSONResponse(status_code=exc.http_status, content=jsonable_encoder(
        {"error": exc.code, "message": str(exc), "details": exc.details}))


class DepositIn(BaseModel):
    funding_account_id: UUID
    amount: int = Field(..., gt=0, description="paise: the FD amount, or the RD monthly installment")
    tenure_months: int = Field(..., ge=1, le=120)


class FastForwardIn(BaseModel):
    days: int = Field(..., ge=1, le=400)


def _booked(dep: dict) -> JSONResponse:
    code = 422 if dep["status"] == "FAILED" else 201
    return JSONResponse(status_code=code, content=jsonable_encoder(dep))


@app.get("/healthz")
async def healthz():
    # async: answered on the event loop, so a busy thread pool (every worker waiting
    # for a database connection under load) can't make the liveness probe time out.
    return {"status": "ok"}


# Ready while the pool is open; no query, so a busy pod isn't taken out (ubcommon.dbpool).
app.get("/readyz")(readiness(lambda: _state["pool"]))


@app.get("/v1/rates")
def rates(conn=Depends(get_conn)):
    return {"business_date": service.business_date(conn), "rates": service.rate_cards(conn)}


@app.get("/v1/quote")
def quote(product: Literal["FD", "RD"], amount: int, tenure_months: int, senior: bool = False,
          conn=Depends(get_conn)):
    """The maturity calculator. No login needed."""
    return service.quote(conn, product=product, amount=amount, tenure_months=tenure_months, senior=senior)


@app.post("/v1/deposits/fd")
def book_fd(body: DepositIn, idempotency_key: str = Header(..., alias="Idempotency-Key"),
            customer_id: UUID = Depends(get_customer), conn=Depends(get_conn), deps=Depends(get_deps)):
    return _booked(service.book(conn, deps, customer_id=customer_id, product="FD",
                                idempotency_key=idempotency_key, **body.model_dump()))


@app.post("/v1/deposits/rd")
def book_rd(body: DepositIn, idempotency_key: str = Header(..., alias="Idempotency-Key"),
            customer_id: UUID = Depends(get_customer), conn=Depends(get_conn), deps=Depends(get_deps)):
    return _booked(service.book(conn, deps, customer_id=customer_id, product="RD",
                                idempotency_key=idempotency_key, **body.model_dump()))


@app.get("/v1/deposits")
def my_deposits(customer_id: UUID = Depends(get_customer), conn=Depends(get_conn)):
    return service.list_deposits(conn, customer_id)


@app.get("/v1/deposits/{deposit_id}")
def get_deposit(deposit_id: UUID, customer_id: UUID = Depends(get_customer), conn=Depends(get_conn)):
    return service.get_deposit(conn, deposit_id, customer_id)


@app.get("/v1/deposits/{deposit_id}/closure-quote")
def closure_quote(deposit_id: UUID, customer_id: UUID = Depends(get_customer), conn=Depends(get_conn)):
    return service.closure_quote(conn, deposit_id, customer_id)


@app.post("/v1/deposits/{deposit_id}/close")
def close(deposit_id: UUID, customer_id: UUID = Depends(get_customer), conn=Depends(get_conn),
          deps=Depends(get_deps)):
    return service.close_early(conn, deps, deposit_id, customer_id)


# ----------------------------------------------------------------- statements


def get_actor(x_actor: str = Header(..., alias="X-Actor")):
    return parse_actor(x_actor)


def _pdf(data: bytes, filename: str) -> Response:
    return Response(content=data, media_type="application/pdf", headers={
        "Content-Disposition": f'inline; filename="{filename}"', "X-Content-Type-Options": "nosniff",
        "Content-Security-Policy": "sandbox; default-src 'none'", "Cache-Control": "no-store"})


@app.get("/v1/accounts/{account_id}/statements")
def statement_periods(account_id: UUID, actor=Depends(get_actor), conn=Depends(get_conn), deps=Depends(get_deps)):
    statements.check_access(deps, account_id, actor)
    return statements.periods(conn, deps, account_id)


@app.get("/v1/accounts/{account_id}/statements/interim")
def statement_interim(account_id: UUID, date_from: date, date_to: date, actor=Depends(get_actor),
                      conn=Depends(get_conn), deps=Depends(get_deps)):
    """Any date range up to today. Built on request; not registered, so it cannot be verified later."""
    statements.check_access(deps, account_id, actor)
    return _pdf(*statements.interim(conn, deps, account_id, date_from, date_to))


@app.get("/v1/accounts/{account_id}/statements/{period}")
def statement_monthly(account_id: UUID, period: str, actor=Depends(get_actor), conn=Depends(get_conn),
                      deps=Depends(get_deps)):
    """The statement for a completed month (period = YYYY-MM), as a PDF."""
    statements.check_access(deps, account_id, actor)
    return _pdf(*statements.monthly(conn, deps, account_id, period))


@app.get("/v1/accounts/{account_id}/statements/{period}/verify")
def statement_verify(account_id: UUID, period: str, actor=Depends(get_actor), conn=Depends(get_conn),
                     deps=Depends(get_deps)):
    statements.check_access(deps, account_id, actor)
    return statements.verify(conn, deps, account_id, period)


@app.post("/v1/statements/verify-file")
async def statement_verify_file(request: Request, deps=Depends(get_deps)):
    """The request body is a PDF. Says whether the bank issued exactly that file. No login needed."""
    data = await request.body()

    def work():
        with _state["pool"].connection() as conn:
            return statements.verify_file(conn, deps, data)

    return jsonable_encoder(await run_in_threadpool(work))


@app.get("/internal/v1/clock")
def clock(conn=Depends(get_conn)):
    return {"business_date": service.business_date(conn)}


@app.post("/internal/v1/eod/run")
def eod_run(conn=Depends(get_conn), deps=Depends(get_deps)):
    return service.run_eod(conn, deps)


@app.post("/internal/v1/eod/fast-forward")
def eod_fast_forward(body: FastForwardIn, conn=Depends(get_conn), deps=Depends(get_deps)):
    return service.fast_forward(conn, deps, body.days)
