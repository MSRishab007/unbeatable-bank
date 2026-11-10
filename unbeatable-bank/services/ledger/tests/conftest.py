"""Test fixtures: a throwaway Postgres database with migrations applied.

Needs a running Postgres. Locally: `docker compose up -d postgres` from the repo
root. Override with TEST_ADMIN_URL if yours differs.
"""

import os
import subprocess
import sys
import uuid
from pathlib import Path

import psycopg
import pytest
from psycopg_pool import ConnectionPool

from app import internal_accounts, posting

ADMIN_URL = os.getenv("TEST_ADMIN_URL", "postgresql://postgres:postgres@localhost:5432/postgres")
DB_NAME = f"ledger_test_{uuid.uuid4().hex[:8]}"
SERVICE_DIR = Path(__file__).resolve().parents[1]


def _db_url(name: str) -> str:
    base, _, _ = ADMIN_URL.rpartition("/")
    return f"{base}/{name}"


@pytest.fixture(scope="session")
def db_url():
    with psycopg.connect(ADMIN_URL, autocommit=True) as admin:
        admin.execute(f'CREATE DATABASE "{DB_NAME}"')
    url = _db_url(DB_NAME)
    subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        cwd=SERVICE_DIR, env={**os.environ, "DATABASE_URL": url}, check=True,
    )
    yield url
    with psycopg.connect(ADMIN_URL, autocommit=True) as admin:
        admin.execute(f'DROP DATABASE IF EXISTS "{DB_NAME}" WITH (FORCE)')


@pytest.fixture(scope="session")
def pool(db_url):
    p = ConnectionPool(db_url, min_size=2, max_size=60, kwargs={"autocommit": True}, open=True)
    yield p
    p.close()


@pytest.fixture
def conn(pool):
    with pool.connection() as c:
        yield c


@pytest.fixture
def make_account(conn):
    """Open a customer account and fund it from CASH."""

    def _make(balance: int = 0, product_type: str = "SAVINGS", overdraft_limit: int = 0):
        account_id = uuid.uuid4()
        posting.open_account(conn, account_id=account_id, product_type=product_type,
                             overdraft_limit=overdraft_limit)
        if balance:
            posting.post_journal(
                conn, idempotency_key=f"fund-{account_id}", txn_type="DEPOSIT",
                legs=[posting.Leg(internal_accounts.CASH, -balance), posting.Leg(account_id, balance)],
            )
        return account_id

    return _make
