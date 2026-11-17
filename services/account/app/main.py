"""Account service REST API.

Until Identity & Auth exists, the caller identity comes from the X-Actor header
(e.g. 'customer:<uuid>', 'staff:<id>', 'system:intake'). The API gateway will set
it from the verified JWT, and strip it from incoming public requests.
"""

import os
from contextlib import asynccontextmanager
from datetime import date
from typing import Literal
from uuid import UUID

from fastapi import Depends, FastAPI, Header, Request
from fastapi.responses import JSONResponse
from psycopg_pool import ConnectionPool
from pydantic import BaseModel, EmailStr, Field

from ubcommon.dbpool import connection_dependency, readiness
from ubcommon.logging_setup import setup_logging
from ubcommon.metrics import instrument

from . import service
from .ledger_client import HttpLedgerClient, LedgerClient, LedgerUnavailable

_state: dict = {}


@asynccontextmanager
async def lifespan(app: FastAPI):
    _state["pool"] = ConnectionPool(
        os.environ["DATABASE_URL"], min_size=1, max_size=int(os.getenv("DB_POOL_MAX", "5")),
        kwargs={"autocommit": True}, open=True,
    )
    _state.setdefault("ledger", HttpLedgerClient())
    yield
    _state["pool"].close()


setup_logging("account")
app = FastAPI(title="Account Service", version="0.1.0", lifespan=lifespan)
instrument(app, "account")


# Waits for a free connection without holding a worker thread (see ubcommon.dbpool).
get_conn = connection_dependency(lambda: _state["pool"])


def get_ledger() -> LedgerClient:
    return _state["ledger"]


def get_actor(x_actor: str = Header("system:unknown", alias="X-Actor")) -> str:
    return x_actor


@app.exception_handler(service.AccountError)
def account_error(_: Request, exc: service.AccountError):
    return JSONResponse(status_code=exc.http_status,
                        content={"error": exc.code, "message": str(exc), "details": exc.details})


@app.exception_handler(LedgerUnavailable)
def ledger_unavailable(_: Request, exc: LedgerUnavailable):
    return JSONResponse(status_code=503, content={"error": "LEDGER_UNAVAILABLE", "message": str(exc)})


# ----------------------------------------------------------------- models


class CustomerIn(BaseModel):
    customer_id: UUID | None = None   # supplied by Identity so a retried sign-up is the same customer
    full_name: str
    date_of_birth: date
    phone: str = Field(..., examples=["+919876543210"])
    email: EmailStr
    pan: str | None = Field(None, examples=["ABCDE1234F"])


class AccountIn(BaseModel):
    product_type: Literal["SAVINGS", "CURRENT"]
    holder_ids: list[UUID] = Field(..., min_length=1, max_length=4)
    operating_mode: Literal["SINGLE", "EITHER_OR_SURVIVOR"] | None = None
    overdraft_limit: int = Field(0, ge=0)
    daily_limit: int | None = Field(None, gt=0)
    per_txn_limit: int | None = Field(None, gt=0)
    nickname: str | None = None


class LimitsIn(BaseModel):
    daily_limit: int | None = Field(None, gt=0)
    per_txn_limit: int | None = Field(None, gt=0)


class PrecheckIn(BaseModel):
    customer_id: UUID
    from_account_id: UUID
    payee: str


class DepositAccountIn(BaseModel):
    account_id: UUID | None = None   # supplied by Deposits so retries don't open two accounts
    product_type: Literal["FD", "RD"]
    holder_ids: list[UUID] = Field(..., min_length=1, max_length=4)
    nickname: str | None = None


class HolderIn(BaseModel):
    customer_id: UUID


class FreezeIn(BaseModel):
    reason: str = Field(..., min_length=3)


class BeneficiaryIn(BaseModel):
    payee_account_number: str
    nickname: str | None = None


class NomineeIn(BaseModel):
    full_name: str = Field(..., min_length=2, max_length=120)
    relation: str
    date_of_birth: date | None = None
    share_pct: float = Field(..., gt=0, le=100)
    guardian_name: str | None = Field(None, max_length=120)


class ContactIn(BaseModel):
    email: EmailStr | None = None
    phone: str | None = None


class AliasIn(BaseModel):
    alias: str


# ----------------------------------------------------------------- health


@app.get("/healthz")
async def healthz():
    # async: answered on the event loop, so a busy thread pool (every worker waiting
    # for a database connection under load) can't make the liveness probe time out.
    return {"status": "ok"}


# Ready while the pool is open; no query, so a busy pod isn't taken out (ubcommon.dbpool).
app.get("/readyz")(readiness(lambda: _state["pool"]))


# ----------------------------------------------------------------- customers


@app.post("/v1/customers", status_code=201)
def create_customer(body: CustomerIn, conn=Depends(get_conn)):
    return service.create_customer(conn, **body.model_dump())


@app.get("/internal/v1/customers")
def search_customers(query: str = "", kyc_status: str | None = None, limit: int = 20, conn=Depends(get_conn)):
    return service.search_customers(conn, query, kyc_status, limit)


@app.get("/v1/customers/{customer_id}")
def get_customer(customer_id: UUID, conn=Depends(get_conn)):
    return service.get_customer(conn, customer_id)


@app.get("/v1/customers/{customer_id}/accounts")
def customer_accounts(customer_id: UUID, conn=Depends(get_conn)):
    return service.list_customer_accounts(conn, customer_id)


@app.post("/v1/customers/{customer_id}/beneficiaries", status_code=201)
def add_beneficiary(customer_id: UUID, body: BeneficiaryIn, conn=Depends(get_conn)):
    return service.add_beneficiary(conn, customer_id, **body.model_dump())


@app.delete("/v1/customers/{customer_id}/beneficiaries/{beneficiary_id}", status_code=204)
def remove_beneficiary(customer_id: UUID, beneficiary_id: UUID, conn=Depends(get_conn)):
    service.remove_beneficiary(conn, customer_id, beneficiary_id)


@app.patch("/internal/v1/customers/{customer_id}/contact")
def update_contact(customer_id: UUID, body: ContactIn, conn=Depends(get_conn)):
    return service.update_contact(conn, customer_id, email=body.email, phone=body.phone)


@app.get("/v1/accounts/{account_id}/nominees")
def nominees(account_id: UUID, conn=Depends(get_conn)):
    return service.list_nominees(conn, account_id)


@app.post("/v1/accounts/{account_id}/nominees", status_code=201)
def add_nominee(account_id: UUID, body: NomineeIn, conn=Depends(get_conn), actor=Depends(get_actor)):
    return service.add_nominee(conn, account_id, actor=actor, **body.model_dump())


@app.delete("/v1/accounts/{account_id}/nominees/{nominee_id}", status_code=204)
def remove_nominee(account_id: UUID, nominee_id: UUID, conn=Depends(get_conn), actor=Depends(get_actor)):
    service.remove_nominee(conn, account_id, nominee_id, actor)


@app.get("/v1/customers/{customer_id}/beneficiaries")
def beneficiaries(customer_id: UUID, conn=Depends(get_conn)):
    return service.list_beneficiaries(conn, customer_id)


# ----------------------------------------------------------------- accounts


@app.post("/v1/accounts", status_code=201)
def open_account(body: AccountIn, conn=Depends(get_conn), ledger=Depends(get_ledger),
                 actor=Depends(get_actor)):
    return service.open_account(conn, ledger, actor=actor, **body.model_dump())


@app.get("/v1/accounts/{account_id}")
def get_account(account_id: UUID, conn=Depends(get_conn)):
    return service.get_account(conn, account_id)


@app.get("/v1/accounts/by-number/{account_number}")
def lookup(account_number: str, conn=Depends(get_conn)):
    return service.lookup_by_number(conn, account_number)


@app.post("/v1/accounts/{account_id}/holders")
def add_holder(account_id: UUID, body: HolderIn, conn=Depends(get_conn), actor=Depends(get_actor)):
    return service.add_holder(conn, account_id, body.customer_id, actor)


@app.post("/v1/accounts/{account_id}/freeze")
def freeze(account_id: UUID, body: FreezeIn, conn=Depends(get_conn), ledger=Depends(get_ledger),
           actor=Depends(get_actor)):
    return service.freeze_account(conn, ledger, account_id, reason=body.reason, actor=actor)


@app.post("/v1/accounts/{account_id}/unfreeze")
def unfreeze(account_id: UUID, conn=Depends(get_conn), ledger=Depends(get_ledger),
             actor=Depends(get_actor)):
    # TODO: require OTP (customer) or maker-checker approval (staff) once those services exist.
    return service.unfreeze_account(conn, ledger, account_id, actor=actor)


@app.patch("/v1/accounts/{account_id}/limits")
def update_limits(account_id: UUID, body: LimitsIn, conn=Depends(get_conn), actor=Depends(get_actor)):
    return service.update_limits(conn, account_id, actor=actor, **body.model_dump())


@app.get("/v1/accounts/{account_id}/events")
def account_events(account_id: UUID, limit: int = 100, conn=Depends(get_conn)):
    return service.list_events(conn, account_id, limit)


@app.post("/v1/accounts/{account_id}/alias", status_code=201)
def create_alias(account_id: UUID, body: AliasIn, conn=Depends(get_conn)):
    return service.create_alias(conn, account_id, body.alias)


@app.get("/v1/aliases/{alias}")
def resolve_alias(alias: str, conn=Depends(get_conn)):
    return service.resolve_alias(conn, alias)


# ----------------------------------------------------------------- internal (other services only)


@app.get("/internal/v1/accounts/{account_id}/authorize/{customer_id}")
def authorize(account_id: UUID, customer_id: UUID, conn=Depends(get_conn)):
    return service.authorize(conn, account_id, customer_id)


@app.post("/internal/v1/accounts/{account_id}/authorize/{customer_id}")
def authorize_debit(account_id: UUID, customer_id: UUID, conn=Depends(get_conn)):
    """Same check, and records the activity (used by Transaction Intake before a payment)."""
    return service.authorize(conn, account_id, customer_id, touch=True)


@app.post("/internal/v1/payments/precheck")
def precheck(body: PrecheckIn, conn=Depends(get_conn)):
    return service.precheck_payment(conn, customer_id=body.customer_id,
                                    from_account_id=body.from_account_id, payee_ref=body.payee.strip().lower())


@app.post("/internal/v1/deposit-accounts", status_code=201)
def open_deposit_account(body: DepositAccountIn, conn=Depends(get_conn), ledger=Depends(get_ledger),
                         actor=Depends(get_actor)):
    """Called by the Deposits service when an FD or RD is booked."""
    return service.open_account(conn, ledger, actor=actor, **body.model_dump())


@app.post("/internal/v1/customers/{customer_id}/kyc/verify")
def verify_kyc(customer_id: UUID, conn=Depends(get_conn), ledger=Depends(get_ledger),
               actor=Depends(get_actor)):
    return service.verify_kyc(conn, ledger, customer_id, actor)


@app.get("/internal/v1/accounts/{account_id}/events/verify")
def verify_chain(account_id: UUID, conn=Depends(get_conn)):
    return {"account_id": str(account_id), "chain_valid": service.verify_event_chain(conn, account_id)}
