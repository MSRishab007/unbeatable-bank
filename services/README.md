# Services

All services are Python 3.12 (FastAPI). Each folder is self-contained:

```
services/<name>/
├── app/                 # service code
├── migrations/          # Alembic migrations for this service's own DB schema
├── tests/               # pytest, runs against a real Postgres
├── Dockerfile           # same image serves the API and runs migrations
├── requirements.txt
└── requirements-dev.txt
```

| Service | Port (local) | Owns schema | Status |
|---|---|---|---|
| Ledger | 8081 (REST), 50051 (gRPC) | `core_banking.ledger` | Balances, journals, entries, holds, outbox |
| Account | 8082 | `core_banking.account` | Customers, accounts, joint holders, payees, aliases |
| Transaction Intake | 8083 | `core_banking.intake` | Payment requests, decisions, limits, outbox |

How they talk: Intake → Account over **REST**, Intake → Ledger over **gRPC**
(contract in `proto/ledger/v1/ledger.proto`). Every service writes events to an
outbox table that will be relayed to **Kafka**.

## Run everything locally (Windows, macOS, Linux)

Needs Docker Desktop.

```powershell
docker compose up --build
```

Then open http://localhost:8081/docs (Ledger), http://localhost:8082/docs (Account)
and http://localhost:8083/docs (Intake). To run the end-to-end check:

```powershell
pip install httpx
python scripts/smoke_test.py
```

If you ran `docker compose` before a new service was added, reset the local
database once so the new database users get created: `docker compose down -v`.

## Run a service's tests

```powershell
docker compose up -d postgres
cd services\ledger
python -m venv .venv
.venv\Scripts\activate           # macOS/Linux: source .venv/bin/activate
pip install -r requirements-dev.txt
pytest -q
```

Tests create and drop their own throwaway database. They also run on every
pull request in GitHub Actions (`.github/workflows/ci.yml`).

## Database rules

- One database user per service; a service can only see its own schema.
- Services never join across each other's tables; they call each other's APIs.
- Schema changes are new Alembic migrations, never edits to old ones.
  On Kubernetes, ArgoCD runs `alembic upgrade head` as a PreSync Job before
  rolling out new pods, so schema changes go through Git like everything else.
- Money is always `bigint` paise. Only the Ledger service changes balances.

## Adding a migration

```powershell
cd services\account
$env:DATABASE_URL = "postgresql://account_svc:account_dev@localhost:5432/core_banking"
alembic revision -m "add nominee address"     # then write the SQL in the new file
alembic upgrade head
```

## Changing a gRPC contract

Edit the file under `proto/`, then regenerate the committed stubs:

```powershell
pip install grpcio-tools==1.68.1
python scripts/gen_protos.py
```

CI fails if the stubs don't match the `.proto` files.
