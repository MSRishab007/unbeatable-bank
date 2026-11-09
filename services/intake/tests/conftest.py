"""Intake tests run against a REAL Ledger (posting engine + gRPC server) in the
same test database, so the gRPC path is exercised end to end. The Account
service is faked in memory.

The Ledger code is imported from services/ledger, so run tests from the repo
with both folders present (as in CI).
"""

import os
import subprocess
import sys
import uuid
from pathlib import Path

import psycopg
import pytest
from psycopg_pool import ConnectionPool

ADMIN_URL = os.getenv("TEST_ADMIN_URL", "postgresql://postgres:postgres@localhost:5432/postgres")
DB_NAME = f"intake_test_{uuid.uuid4().hex[:8]}"
INTAKE_DIR = Path(__file__).resolve().parents[1]
LEDGER_DIR = INTAKE_DIR.parent / "ledger"
CASH = "00000000-0000-0000-0000-000000000001"


def _ledger_modules():
    """Import the Ledger's posting engine and gRPC server under a separate name."""
    import importlib.util

    def load(name, path):
        spec = importlib.util.spec_from_file_location(name, path, submodule_search_locations=[str(path.parent)]
                                                      if path.name == "__init__.py" else None)
        mod = importlib.util.module_from_spec(spec)
        sys.modules[name] = mod
        spec.loader.exec_module(mod)
        return mod

    load("ledger_app", LEDGER_DIR / "app" / "__init__.py")
    posting = load("ledger_app.posting", LEDGER_DIR / "app" / "posting.py")
    grpc_server = load("ledger_app.grpc_server", LEDGER_DIR / "app" / "grpc_server.py")
    return posting, grpc_server


@pytest.fixture(scope="session")
def db_url():
    with psycopg.connect(ADMIN_URL, autocommit=True) as admin:
        admin.execute(f'CREATE DATABASE "{DB_NAME}"')
    url = ADMIN_URL.rpartition("/")[0] + "/" + DB_NAME
    for service_dir in (LEDGER_DIR, INTAKE_DIR):
        subprocess.run([sys.executable, "-m", "alembic", "upgrade", "head"], cwd=service_dir,
                       env={**os.environ, "DATABASE_URL": url}, check=True)
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


@pytest.fixture(scope="session")
def ledger_engine(pool):
    posting, grpc_server = _ledger_modules()
    server = grpc_server.start(pool, 0)
    from app.clients import GrpcLedgerClient

    client = GrpcLedgerClient(f"localhost:{server.bound_port}")
    yield posting, client, server
    client.close()
    server.stop(0)


@pytest.fixture
def ledger(ledger_engine):
    return ledger_engine[1]


class FakeAccounts:
    """In-memory stand-in for the Account service, backed by real ledger accounts."""

    def __init__(self, posting, conn):
        self.posting, self.conn = posting, conn
        self.accounts: dict[str, dict] = {}
        self.holders: dict[str, set] = {}
        self.aliases: dict[str, str] = {}
        self.beneficiary_list: dict[str, list] = {}
        self.customers: dict[str, dict] = {}
        self.down = False
        self._serial = 0

    def _check(self):
        if self.down:
            from app.clients import DependencyUnavailable
            raise DependencyUnavailable("account service down")

    def customer(self, daily_transfer_limit=20_000_000, status="ACTIVE"):
        cid = uuid.uuid4()
        self.customers[str(cid)] = {"status": status, "daily_transfer_limit": daily_transfer_limit}
        return cid

    def account(self, holders, balance=0, *, name="Riya Sharma", status="ACTIVE", product_type="SAVINGS",
                daily_limit=10_000_000, per_txn_limit=5_000_000):
        from app.clients import NotFoundError  # noqa: F401
        account_id = uuid.uuid4()
        self._serial += 1
        body = f"5001{self._serial:07d}"
        number = body + _luhn(body)
        self.posting.open_account(self.conn, account_id=account_id, product_type=product_type)
        if balance:
            self.posting.post_journal(self.conn, idempotency_key=f"fund-{account_id}", txn_type="DEPOSIT",
                                      legs=[self.posting.Leg(uuid.UUID(CASH), -balance),
                                            self.posting.Leg(account_id, balance)])
        self.accounts[str(account_id)] = {
            "account_id": str(account_id), "account_number": number, "status": status,
            "product_type": product_type, "daily_limit": daily_limit, "per_txn_limit": per_txn_limit,
            "display_name": name.split()[0] + " " + "".join(p[0] + "*" * (len(p) - 1) for p in name.split()[1:]),
        }
        self.holders[str(account_id)] = {str(h) for h in holders}
        return account_id, number

    def save_payee(self, customer_id, number, cooling=False):
        self.beneficiary_list.setdefault(str(customer_id), []).append(
            {"payee_account_number": number, "in_cooling_period": cooling})

    # ---- AccountClient protocol
    def authorize(self, account_id, customer_id):
        self._check()
        a, c = self.accounts.get(str(account_id)), self.customers.get(str(customer_id))
        if a is None or str(customer_id) not in self.holders.get(str(account_id), set()):
            return {"allowed": False, "reason": "NOT_A_HOLDER"}
        reason = None
        if c["status"] != "ACTIVE":
            reason = "CUSTOMER_" + c["status"]
        elif a["status"] != "ACTIVE":
            reason = "ACCOUNT_" + a["status"]
        elif a["product_type"] in ("FD", "RD"):
            reason = "DEPOSIT_ACCOUNT_NOT_DEBITABLE"
        return {"allowed": reason is None, "reason": reason, "daily_limit": a["daily_limit"],
                "per_txn_limit": a["per_txn_limit"], "daily_transfer_limit": c["daily_transfer_limit"]}

    def lookup_number(self, number):
        from app.clients import BadRequest, NotFoundError
        self._check()
        if len(number) != 12 or not number.isdigit() or _luhn(number[:-1]) != number[-1]:
            raise BadRequest("invalid account number")
        for a in self.accounts.values():
            if a["account_number"] == number:
                return {"account_id": a["account_id"], "account_number": number,
                        "display_name": a["display_name"], "can_receive": a["status"] != "CLOSED"}
        raise NotFoundError("account not found")

    def resolve_alias(self, alias):
        from app.clients import NotFoundError
        self._check()
        if alias not in self.aliases:
            raise NotFoundError("alias not found")
        return self.lookup_number(self.aliases[alias])

    def beneficiaries(self, customer_id):
        self._check()
        return self.beneficiary_list.get(str(customer_id), [])

    def customer_accounts(self, customer_id):
        self._check()
        return [a for aid, a in self.accounts.items() if str(customer_id) in self.holders[aid]]


def _luhn(digits: str) -> str:
    total = 0
    for i, ch in enumerate(reversed(digits)):
        d = int(ch)
        if i % 2 == 0:
            d = d * 2 - 9 if d * 2 > 9 else d * 2
        total += d
    return str((10 - total % 10) % 10)


@pytest.fixture
def accounts(ledger_engine, conn):
    return FakeAccounts(ledger_engine[0], conn)


@pytest.fixture
def balance(ledger_engine, conn):
    def _balance(account_id):
        return ledger_engine[0].get_balance(conn, account_id)["ledger_balance"]
    return _balance
