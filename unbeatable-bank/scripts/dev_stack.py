"""Run the whole bank on one machine WITHOUT Docker (plain Python processes).

Needs: Python 3.11+, a local PostgreSQL, and the service dependencies:

    pip install -e "libs/common[kafka]"
    pip install -r services/ledger/requirements.txt -r services/ratelimit/requirements.txt

One-time database setup (as the postgres superuser):

    psql -U postgres -f infra/local/postgres-init.sql

Then:

    python scripts/dev_stack.py            # migrate, start everything, Ctrl+C to stop
    python scripts/smoke_test.py           # in another terminal

There is no Kafka in this mode: the outbox relays POST events straight to the
Fraud Alert service instead (OUTBOX_HTTP_SINKS). Docker Compose and GKE use Kafka.

The web/API entry point is the gateway: http://localhost:8080/api/docs

A DynaStore node may be killed (kill <pid>) or frozen (kill -STOP <pid>) to show the
bank keeps working; the rest keeps running. Bring a killed node back with
`touch .dev/restart-dyna-1` (or kill -CONT for a frozen one).
"""

from __future__ import annotations

import os
import signal
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RUN_DIR = ROOT / ".dev"
PG = os.getenv("DEV_PG_HOST", "localhost:5432")

DYNA_PEERS = "dyna-0=127.0.0.1:7000,dyna-1=127.0.0.1:7001,dyna-2=127.0.0.1:7002"
DYNA_NODES = "127.0.0.1:7000,127.0.0.1:7001,127.0.0.1:7002"
STAFF = "admin:ADMIN:admin-dev-pass,analyst1:ANALYST:analyst-dev-pass,analyst2:ANALYST:analyst-dev-pass," \
        "teller1:TELLER:teller-dev-pass,kyc1:KYC:kyc-dev-pass"


def db(user: str, database: str) -> str:
    return f"postgresql://{user}_svc:{user}_dev@{PG}/{database}"


COMMON = {
    "JWT_SECRET": "dev-only-jwt-secret-change-me-0123456789",
    "DOCSTORE": "postgres",
    "DOCSTORE_URL": db("docstore", "docstore"),
    "PUBSUB": "local",
    "OBJSTORE": "local",
    "OBJSTORE_DIR": str(RUN_DIR / "objects"),
    "DYNASTORE_NODES": DYNA_NODES,
    "LEDGER_URL": "http://127.0.0.1:8081",
    "ACCOUNT_URL": "http://127.0.0.1:8082",
    "INTAKE_URL": "http://127.0.0.1:8083",
    "RATELIMIT_URL": "http://127.0.0.1:8084",
    "RISK_URL": "http://127.0.0.1:8085",
    "IDENTITY_URL": "http://127.0.0.1:8086",
    "FRAUD_URL": "http://127.0.0.1:8087",
    "DEPOSITS_URL": "http://127.0.0.1:8088",
    "DYNASTORE_URL": "http://127.0.0.1:7100",
    "LEDGER_GRPC": "127.0.0.1:50051",
    "RATELIMIT_GRPC": "127.0.0.1:50052",
    "RISK_GRPC": "127.0.0.1:50053",
    "DB_POOL_MAX": "4",
}

# name -> (folder under services/, http port, extra environment)
SERVICES: dict[str, tuple[str, int, dict[str, str]]] = {
    **{f"dyna-{i}": ("dynastore", 7100 + i, {
        "NODE_ID": f"dyna-{i}", "PEERS": DYNA_PEERS, "GRPC_PORT": str(7000 + i),
        "DATA_DIR": str(RUN_DIR / f"dyna-{i}")}) for i in range(3)},
    "ledger": ("ledger", 8081, {"DATABASE_URL": db("ledger", "core_banking"), "GRPC_PORT": "50051"}),
    "account": ("account", 8082, {"DATABASE_URL": db("account", "core_banking")}),
    "ratelimit": ("ratelimit", 8084, {"GRPC_PORT": "50052"}),
    "risk": ("risk", 8085, {"GRPC_PORT": "50053"}),
    "identity": ("identity", 8086, {"DATABASE_URL": db("identity", "identity"), "STAFF_BOOTSTRAP": STAFF,
                                    "AUTO_VERIFY_KYC": os.getenv("AUTO_VERIFY_KYC", "false")}),
    "intake": ("intake", 8083, {"DATABASE_URL": db("intake", "core_banking")}),
    "fraud": ("fraud", 8087, {"DATABASE_URL": db("fraud", "cases")}),
    "deposits": ("deposits", 8088, {"DATABASE_URL": db("deposits", "core_banking")}),
    "gateway": ("gateway", 8080, {"ALLOW_SIM_HEADERS": "true"}),
}
MIGRATED = ["ledger", "account", "intake", "identity", "fraud", "deposits"]
# Outbox relays: (schema, service whose database it reads, where events go without Kafka)
EVENTS_URL = "http://127.0.0.1:8087/internal/v1/events"
RELAYS = [("intake", "intake", f"txn-events={EVENTS_URL}"), ("identity", "identity", f"auth-events={EVENTS_URL}")]

procs: list[tuple[str, subprocess.Popen]] = []


def start(name: str, cmd: list[str], cwd: Path, env: dict[str, str]) -> None:
    log = open(RUN_DIR / "logs" / f"{name}.log", "w")
    procs.append((name, subprocess.Popen(cmd, cwd=cwd, env={**os.environ, **COMMON, **env},
                                         stdout=log, stderr=subprocess.STDOUT)))


def wait_healthy(name: str, port: int, timeout: float = 40) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            urllib.request.urlopen(f"http://127.0.0.1:{port}/healthz", timeout=1)
            return
        except Exception:
            time.sleep(0.3)
    stop_all()
    sys.exit(f"{name} did not start; see {RUN_DIR / 'logs' / (name + '.log')}")


def stop_all(*_) -> None:
    for _, p in reversed(procs):
        p.send_signal(signal.SIGTERM)
    for _, p in procs:
        try:
            p.wait(timeout=8)
        except subprocess.TimeoutExpired:
            p.kill()


def main() -> None:
    (RUN_DIR / "logs").mkdir(parents=True, exist_ok=True)
    for name in MIGRATED:
        folder, _, env = SERVICES[name]
        print(f"migrating {name} ...")
        subprocess.run([sys.executable, "-m", "alembic", "upgrade", "head"], cwd=ROOT / "services" / folder,
                       env={**os.environ, **env}, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    signal.signal(signal.SIGTERM, lambda *_: (stop_all(), sys.exit(0)))
    for name, (folder, port, env) in SERVICES.items():
        if "DATA_DIR" in env:
            Path(env["DATA_DIR"]).mkdir(parents=True, exist_ok=True)
        start(name, [sys.executable, "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", str(port),
                     "--log-level", "warning"], ROOT / "services" / folder, env)
        wait_healthy(name, port)
        print(f"  {name:10s} http://127.0.0.1:{port}")
    for schema, service, sinks in RELAYS:
        start(f"outbox-{schema}", [sys.executable, "-m", "ubcommon.outbox", "--schema", schema], ROOT,
              {"DATABASE_URL": SERVICES[service][2]["DATABASE_URL"], "OUTBOX_HTTP_SINKS": sinks,
               "OUTBOX_METRICS_PORT": "0"})
    print("\nAll services are up. API: http://127.0.0.1:8080/api/docs   logs: .dev/logs/   Ctrl+C to stop")
    down: set[str] = set()
    try:
        while True:
            time.sleep(1)
            for name, p in procs:
                if p.poll() is not None and name not in down:
                    print(f"{name} exited with code {p.returncode}; see .dev/logs/{name}.log")
                    if not name.startswith("dyna-"):
                        stop_all()
                        sys.exit(1)
                    # A DynaStore node going away is a failure the bank must survive (and a demo).
                    down.add(name)
                    print(f"  the rest keeps running; bring it back with: touch {RUN_DIR / ('restart-' + name)}")
            for name in list(down):
                flag = RUN_DIR / f"restart-{name}"
                if flag.exists():
                    flag.unlink()
                    folder, port, env = SERVICES[name]
                    procs[:] = [(n, p) for n, p in procs if n != name]
                    start(name, [sys.executable, "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1",
                                 "--port", str(port), "--log-level", "warning"], ROOT / "services" / folder, env)
                    down.discard(name)
                    print(f"{name} restarted")
    except KeyboardInterrupt:
        print("\nstopping ...")
        stop_all()


if __name__ == "__main__":
    main()
