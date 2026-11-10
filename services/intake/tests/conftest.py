"""Intake tests run against a REAL Ledger (posting engine + gRPC server) in the
same test database, so the gRPC path is exercised end to end. The Account
service, rate limiter, risk engine and identity service are small fakes here;
test_full_pipeline.py swaps in the real rate limiter and risk engine on DynaStore.
"""

import os
import subprocess
import sys
import uuid
from pathlib import Path

import psycopg
import pytest
from psycopg_pool import ConnectionPool

from ubcommon.testing import load_app

from app import service
from app.clients import DependencyUnavailable, GrpcLedgerClient, LimitDecision, RiskDecision

ADMIN_URL = os.getenv("TEST_ADMIN_URL", "postgresql://postgres:postgres@localhost:5432/postgres")
DB_NAME = f"intake_test_{uuid.uuid4().hex[:8]}"
INTAKE_DIR = Path(__file__).resolve().parents[1]
LEDGER_DIR = INTAKE_DIR.parent / "ledger"
CASH = uuid.UUID("00000000-0000-0000-0000-000000000001")


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
    load_app("ledger", "ledger_app")
    from ledger_app import grpc_server, posting

    server = grpc_server.start(pool, 0)
    client = GrpcLedgerClient(f"localhost:{server.bound_port}")
    yield posting, client, server
    client.close()
    server.stop(0)


@pytest.fixture
def ledger(ledger_engine):
    return ledger_engine[1]


def _luhn(digits: str) -> str:
    total = 0
    for i, ch in enumerate(reversed(digits)):
        d = int(ch)
        if i % 2 == 0:
            d = d * 2 - 9 if d * 2 > 9 else d * 2
        total += d
    return str((10 - total % 10) % 10)


class FakeAccounts:
    """In-memory stand-in for the Account service, backed by real ledger accounts."""

    _serial = 0

    def __init__(self, posting, conn):
        self.posting, self.conn = posting, conn
        self.accounts: dict[str, dict] = {}
        self.holders: dict[str, set] = {}
        self.aliases: dict[str, str] = {}
        self.trusted: set[tuple[str, str]] = set()      # (customer, payee account number)
        self.customers: dict[str, dict] = {}
        self.frozen: list[tuple[str, str, str]] = []
        self.down = False

    def _check(self):
        if self.down:
            raise DependencyUnavailable("account service down")

    def customer(self, daily_transfer_limit=20_000_000, status="ACTIVE"):
        cid = uuid.uuid4()
        self.customers[str(cid)] = {"status": status, "daily_transfer_limit": daily_transfer_limit}
        return cid

    def account(self, holders, balance=0, *, name="Riya Sharma", status="ACTIVE", product_type="SAVINGS",
                daily_limit=10_000_000, per_txn_limit=5_000_000, idle_days=0):
        account_id = uuid.uuid4()
        FakeAccounts._serial += 1
        body = f"5001{FakeAccounts._serial:07d}"
        number = body + _luhn(body)
        self.posting.open_account(self.conn, account_id=account_id, product_type=product_type)
        if balance:
            self.posting.post_journal(self.conn, idempotency_key=f"fund-{account_id}", txn_type="DEPOSIT",
                                      legs=[self.posting.Leg(CASH, -balance), self.posting.Leg(account_id, balance)])
        first, *rest = name.split()
        self.accounts[str(account_id)] = {
            "account_id": str(account_id), "account_number": number, "status": status,
            "product_type": product_type, "daily_limit": daily_limit, "per_txn_limit": per_txn_limit,
            "idle_days": idle_days, "display_name": " ".join([first] + [p[0] + "*" * (len(p) - 1) for p in rest]),
        }
        self.holders[str(account_id)] = {str(h) for h in holders}
        return account_id, number

    def trust(self, customer_id, number):
        """Saved payee, past its cooling period."""
        self.trusted.add((str(customer_id), number))

    # ---- AccountClient protocol
    def _authorize(self, account_id, customer_id):
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
                "per_txn_limit": a["per_txn_limit"], "daily_transfer_limit": c["daily_transfer_limit"],
                "idle_days": a["idle_days"]}

    def lookup_number(self, number):
        self._check()
        for a in self.accounts.values():
            if a["account_number"] == number:
                return {"account_id": a["account_id"], "account_number": number,
                        "display_name": a["display_name"], "can_receive": a["status"] != "CLOSED"}
        return None

    def precheck(self, customer_id, from_account_id, payee):
        self._check()
        out = {"auth": self._authorize(from_account_id, customer_id), "payee": None, "payee_error": None,
               "payee_trusted": False}
        if not out["auth"]["allowed"]:
            return out
        number = self.aliases.get(payee) if "@" in payee else payee
        if number is None:
            out["payee_error"] = "PAYEE_NOT_FOUND"
        elif len(number) != 12 or not number.isdigit() or _luhn(number[:-1]) != number[-1]:
            out["payee_error"] = "INVALID_PAYEE"
        else:
            out["payee"] = self.lookup_number(number)
            if out["payee"] is None:
                out["payee_error"] = "PAYEE_NOT_FOUND"
            else:
                own = str(customer_id) in self.holders[out["payee"]["account_id"]]
                out["payee_trusted"] = own or (str(customer_id), number) in self.trusted
        return out

    def freeze(self, account_id, reason, actor):
        self._check()
        self.accounts[str(account_id)]["status"] = "FROZEN"
        self.frozen.append((str(account_id), reason, actor))


class FakeLimiter:
    def __init__(self):
        self.deny = False
        self.calls = []

    def check(self, **kw):
        self.calls.append(kw)
        return LimitDecision(not self.deny, "account" if self.deny else "", 30 if self.deny else 0)


class FakeRisk:
    """Returns whatever decision the test sets next (default ALLOW)."""

    def __init__(self):
        self.next = RiskDecision()
        self.calls = []

    def evaluate(self, **kw):
        self.calls.append(kw)
        decision, self.next = self.next, RiskDecision()
        return decision


class FakeIdentity:
    CODE = "482913"

    def __init__(self):
        self.issued = []
        self.wrong = 0

    def issue_otp(self, customer_id, purpose, context):
        self.issued.append((str(customer_id), purpose, context))

    def verify_otp(self, customer_id, purpose, code):
        if code == self.CODE:
            return "OK"
        self.wrong += 1
        return "LOCKED" if self.wrong >= 3 else "WRONG"


@pytest.fixture
def accounts(ledger_engine, conn):
    return FakeAccounts(ledger_engine[0], conn)


@pytest.fixture
def deps(accounts, ledger):
    return service.Deps(accounts=accounts, ledger=ledger, limiter=FakeLimiter(), risk=FakeRisk(),
                        identity=FakeIdentity())


@pytest.fixture
def balance(ledger_engine, conn):
    def _balance(account_id, available=False):
        b = ledger_engine[0].get_balance(conn, account_id)
        return b["available_balance"] if available else b["ledger_balance"]
    return _balance
