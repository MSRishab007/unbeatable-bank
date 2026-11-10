import os
import subprocess
import sys
import uuid
from datetime import date
from pathlib import Path

import psycopg
import pytest
from psycopg_pool import ConnectionPool

from app import service

ADMIN_URL = os.getenv("TEST_ADMIN_URL", "postgresql://postgres:postgres@localhost:5432/postgres")
DB_NAME = f"account_test_{uuid.uuid4().hex[:8]}"
SERVICE_DIR = Path(__file__).resolve().parents[1]


class FakeLedger:
    """Records calls instead of talking to the Ledger service."""

    def __init__(self):
        self.status: dict = {}
        self.opened: dict = {}

    def open_account(self, account_id, product_type, overdraft_limit):
        self.opened[account_id] = (product_type, overdraft_limit)
        self.status[account_id] = "OPEN"

    def set_status(self, account_id, status):
        self.status[account_id] = status


@pytest.fixture(scope="session")
def db_url():
    with psycopg.connect(ADMIN_URL, autocommit=True) as admin:
        admin.execute(f'CREATE DATABASE "{DB_NAME}"')
    url = ADMIN_URL.rpartition("/")[0] + "/" + DB_NAME
    subprocess.run([sys.executable, "-m", "alembic", "upgrade", "head"], cwd=SERVICE_DIR,
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
        yield c


@pytest.fixture
def ledger():
    return FakeLedger()


_counter = iter(range(10_000_000))


@pytest.fixture
def make_customer(conn):
    def _make(name="Riya Sharma", verified=True, pan=None):
        n = next(_counter)
        c = service.create_customer(
            conn, full_name=name, date_of_birth=date(2001, 5, 17),
            phone=f"+919{(uuid.uuid4().int % 10**9):09d}", email=f"user{n}-{uuid.uuid4().hex[:6]}@example.com",
            pan=pan,
        )
        if verified:
            service.verify_kyc(conn, FakeLedger(), c["customer_id"], "staff:test")
        return c["customer_id"]

    return _make
