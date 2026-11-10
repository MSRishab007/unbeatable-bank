import os
import subprocess
import sys
import uuid
from pathlib import Path

import psycopg
import pytest
from psycopg_pool import ConnectionPool

from ubcommon.docstore import MemoryDocStore
from ubcommon.notify import get_notifier

from app import service

ADMIN_URL = os.getenv("TEST_ADMIN_URL", "postgresql://postgres:postgres@localhost:5432/postgres")
DB_NAME = f"identity_test_{uuid.uuid4().hex[:8]}"
SERVICE_DIR = Path(__file__).resolve().parents[1]


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


class FakeAccounts:
    def __init__(self):
        self.customers, self.verified, self.fail = {}, [], None

    def create_customer(self, profile):
        if self.fail:
            raise self.fail
        self.customers[profile["customer_id"]] = profile
        return {**profile, "cif_number": f"CIF{len(self.customers):09d}"}

    def verify_kyc(self, customer_id):
        self.verified.append(str(customer_id))


class FakeSignals:
    def __init__(self):
        self.keys = {}

    def put(self, key, value, *, ttl=0, **kw):
        self.keys[key] = float(value)

    def put_if_absent(self, key, value, *, ttl=0, **kw):
        self.keys.setdefault(key, float(value))


class Clock:
    def __init__(self):
        import time
        self.now = time.time()

    def __call__(self):
        return self.now


@pytest.fixture
def deps():
    docstore = MemoryDocStore()
    return service.Deps(accounts=FakeAccounts(), docstore=docstore, notifier=get_notifier(docstore),
                        jwt_secret="test-secret", signals=FakeSignals(), clock=Clock())


@pytest.fixture
def user(conn, deps):
    """A registered customer."""
    n = uuid.uuid4().int
    profile = {"email": f"riya{n % 10**8}@example.com", "phone": f"+919{n % 10**9:09d}",
               "password": "correct horse battery"}
    out = service.register(conn, deps, full_name="Riya Sharma", date_of_birth=__import__("datetime").date(2001, 5, 17),
                           **profile)
    return {**profile, "customer_id": out["customer_id"]}
