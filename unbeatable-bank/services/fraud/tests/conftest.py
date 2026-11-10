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
DB_NAME = f"fraud_test_{uuid.uuid4().hex[:8]}"
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


class FakeIntake:
    def __init__(self):
        self.payments, self.calls = {}, []

    def add(self, **payment):
        self.payments[str(payment["txn_id"])] = payment
        return payment

    def get(self, txn_id):
        return self.payments.get(str(txn_id))

    def release(self, txn_id, actor):
        self.calls.append(("release", str(txn_id), actor))
        self.payments[str(txn_id)]["status"] = "COMPLETED"
        return self.payments[str(txn_id)]

    def cancel(self, txn_id, actor):
        self.calls.append(("cancel", str(txn_id), actor))
        self.payments[str(txn_id)]["status"] = "DECLINED"
        return self.payments[str(txn_id)]


class FakeAccounts:
    def __init__(self):
        self.frozen, self.unfrozen, self.holders = [], [], {}

    def freeze(self, account_id, reason, actor):
        self.frozen.append((str(account_id), reason, actor))

    def unfreeze(self, account_id, actor):
        self.unfrozen.append((str(account_id), actor))

    def get_account(self, account_id):
        holder = self.holders.get(str(account_id))
        return {"holders": [{"customer_id": holder, "role": "PRIMARY"}]} if holder else None

    def get_customer(self, customer_id):
        return {"email": f"{str(customer_id)[:8]}@example.com"}


class FakeSignals:
    def __init__(self):
        self.keys = {}

    def put(self, key, value, *, ttl=0, **kw):
        self.keys[key] = ttl


@pytest.fixture
def deps():
    docstore = MemoryDocStore()
    return service.Deps(docstore=docstore, notifier=get_notifier(docstore), intake=FakeIntake(),
                        accounts=FakeAccounts(), signals=FakeSignals())
