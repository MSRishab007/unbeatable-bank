"""Deposits tests use the real Ledger posting engine in the same test database."""

import os
import subprocess
import sys
import uuid
from datetime import date
from pathlib import Path

import psycopg
import pytest
from psycopg_pool import ConnectionPool

from ubcommon.objstore import LocalObjectStore
from ubcommon.testing import load_app

from app import service

ADMIN_URL = os.getenv("TEST_ADMIN_URL", "postgresql://postgres:postgres@localhost:5432/postgres")
DB_NAME = f"deposits_test_{uuid.uuid4().hex[:8]}"
SERVICE_DIR = Path(__file__).resolve().parents[1]
CASH = uuid.UUID("00000000-0000-0000-0000-000000000001")
START = date(2026, 1, 1)


@pytest.fixture(scope="session")
def db_url():
    with psycopg.connect(ADMIN_URL, autocommit=True) as admin:
        admin.execute(f'CREATE DATABASE "{DB_NAME}"')
    url = ADMIN_URL.rpartition("/")[0] + "/" + DB_NAME
    for service_dir in (SERVICE_DIR.parent / "ledger", SERVICE_DIR):
        subprocess.run([sys.executable, "-m", "alembic", "upgrade", "head"], cwd=service_dir,
                       env={**os.environ, "DATABASE_URL": url}, check=True)
    yield url
    with psycopg.connect(ADMIN_URL, autocommit=True) as admin:
        admin.execute(f'DROP DATABASE IF EXISTS "{DB_NAME}" WITH (FORCE)')


@pytest.fixture(scope="session")
def pool(db_url):
    p = ConnectionPool(db_url, min_size=1, max_size=10, kwargs={"autocommit": True}, open=True)
    yield p
    p.close()


@pytest.fixture
def conn(pool):
    with pool.connection() as c:
        # Every test starts from an empty bank on the same business date.
        c.execute("TRUNCATE deposits.statements, deposits.rd_installments, deposits.deposits, deposits.eod_runs, ledger.accrual_runs, "
                  "ledger.outbox, ledger.holds, ledger.entries, ledger.journals, ledger.balances CASCADE")
        c.execute("INSERT INTO ledger.balances (account_id, kind, product_type) "
                  "SELECT ('00000000-0000-0000-0000-00000000000' || n)::uuid, 'INTERNAL', 'INTERNAL' "
                  "FROM generate_series(1, 6) AS n")
        c.execute("UPDATE deposits.bank_clock SET business_date = %s", (START,))
        yield c


@pytest.fixture(scope="session")
def posting():
    load_app("ledger", "ledger_app")
    from ledger_app import posting
    return posting


class RealLedger:
    """The Ledger client protocol, served by the posting engine directly."""

    def __init__(self, posting, pool):
        self.posting, self.pool = posting, pool

    def post_journal(self, *, idempotency_key, txn_type, legs, description, business_date):
        try:
            with self.pool.connection() as c:
                return str(self.posting.post_journal(
                    c, idempotency_key=idempotency_key, txn_type=txn_type,
                    legs=[self.posting.Leg(a, amount) for a, amount in legs], description=description,
                    business_date=business_date).journal_id)
        except self.posting.LedgerError as e:
            raise service.LedgerRejected(e.code, str(e)) from e

    def accrue_savings(self, business_date, annual_rate):
        with self.pool.connection() as c:
            return self.posting.accrue_savings_interest(c, business_date=business_date,
                                                        annual_rate=f"{annual_rate:.6f}")

    def post_savings_interest(self, business_date):
        with self.pool.connection() as c:
            return self.posting.post_savings_interest(c, business_date=business_date)

    def reconciliation(self):
        with self.pool.connection() as c:
            return self.posting.reconcile(c)

    def statement(self, account_id, date_from, date_to):
        try:
            with self.pool.connection() as c:
                return self.posting.statement(c, account_id, date_from, date_to)
        except self.posting.AccountNotFound as e:
            raise service.NotFound("account not found") from e


class FakeAccounts:
    def __init__(self, posting, pool):
        self.posting, self.pool = posting, pool
        self.owners, self.dob, self.serial, self.opened = {}, {}, 0, {}

    def customer(self, born=date(1995, 6, 15)):
        cid = uuid.uuid4()
        self.dob[cid] = born
        return cid

    def savings(self, customer_id, balance=0, product_type="SAVINGS"):
        account_id = uuid.uuid4()
        with self.pool.connection() as c:
            self.posting.open_account(c, account_id=account_id, product_type=product_type)
            if balance:
                self.posting.post_journal(c, idempotency_key=f"fund-{account_id}", txn_type="DEPOSIT",
                                          legs=[self.posting.Leg(CASH, -balance), self.posting.Leg(account_id, balance)])
        self.owners[account_id] = (customer_id, product_type)
        return account_id

    def authorize(self, account_id, customer_id):
        owner = self.owners.get(account_id)
        if owner is None or owner[0] != customer_id:
            return {"allowed": False, "reason": "NOT_A_HOLDER", "is_holder": False}
        return {"allowed": True, "product_type": owner[1], "is_holder": True}

    def account(self, account_id):
        owner = self.owners.get(account_id)
        if owner is None:
            raise service.NotFound("account not found")
        return {"account_id": str(account_id), "account_number": "5001" + str(account_id.int)[-8:],
                "product_type": owner[1], "opened_at": self.opened.get(account_id, "2025-11-20T10:00:00+00:00"),
                "holders": [{"full_name": "Riya Sharma", "role": "PRIMARY"}]}

    def open_deposit_account(self, account_id, product, customer_id):
        with self.pool.connection() as c:
            self.posting.open_account(c, account_id=account_id, product_type=product)
        self.serial += 1
        return {"account_id": str(account_id), "account_number": f"5001{self.serial:08d}"}

    def date_of_birth(self, customer_id):
        return self.dob.get(customer_id)


class FakeSignals:
    def __init__(self):
        self.keys = {}

    def put(self, key, value, *, ttl=0, **kw):
        self.keys[key] = ttl


@pytest.fixture
def deps(posting, pool, tmp_path):
    return service.Deps(accounts=FakeAccounts(posting, pool), ledger=RealLedger(posting, pool),
                        objstore=LocalObjectStore(str(tmp_path / "objects")), signals=FakeSignals())


@pytest.fixture
def balance(posting, pool):
    def _balance(account_id):
        with pool.connection() as c:
            return posting.get_balance(c, account_id)["ledger_balance"]
    return _balance
