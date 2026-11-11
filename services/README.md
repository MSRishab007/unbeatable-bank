# Services

All services are Python 3.12. Each folder is self-contained and they share one
library, `libs/common` (auth, metrics, logging, outbox, Kafka, Pub/Sub, stores,
gRPC stubs). One Dockerfile builds every image:

```bash
docker build -f services/Dockerfile --build-arg SERVICE=ledger -t ledger .   # from the repo root
```

```
services/<name>/
├── app/                 # service code (app/main.py is the API)
├── migrations/          # Alembic migrations for this service's own schema (if it has a database)
├── config/              # rate-limit policies / risk rules (mounted from a ConfigMap on Kubernetes)
├── tests/               # pytest; database tests run against a real Postgres
└── requirements*.txt
```

| Service | Interfaces | Data | What it does |
|---|---|---|---|
| `gateway` | REST (public) | none | Verifies JWTs, checks roles and ownership, routes `/api/...`, serves the web app |
| `identity` | REST | Postgres `identity`, document store, object store | Sign-up, login, devices, sessions, OTP, staff, KYC documents |
| `account` | REST | Postgres `core_banking.account` | Customers, accounts, joint holders, limits, freeze, payees, aliases, audit chain |
| `ledger` | REST + gRPC :50051 | Postgres `core_banking.ledger` | Double-entry journals, balances, holds, interest, reconciliation |
| `intake` | REST | Postgres `core_banking.intake` | Payment admission pipeline: idempotency, limits, risk decision, OTP, holds |
| `ratelimit` | REST + gRPC :50052 | DynaStore | Sliding-window rate limits shared by all pods |
| `risk` | REST + gRPC :50053 | DynaStore | Real-time risk scoring from `config/rules.yaml` |
| `fraud` | REST, Kafka consumer | Postgres `cases`, document store | Alerts, auto-freeze, cases, maker-checker, customer inbox |
| `deposits` | REST | Postgres `core_banking.deposits`, object store | FD, RD, rate cards, end-of-day batch, bank calendar, PDF statements |
| `dynastore` | REST + gRPC :7000 | local disk (SQLite) | The leaderless key-value store |
| `audit-analytics` | Flink SQL job | Kafka | Windowed aggregations; see its README |

The Notification Service is in `functions/notification` (Cloud Function).

How they talk: clients → gateway → services over **REST**; Intake → Rate Limit,
Risk and Ledger over **gRPC** (`proto/`); events through a transactional
**outbox** to **Kafka**; notifications through **Pub/Sub**.

## Run everything locally

```bash
docker compose up --build        # from the repo root
python scripts/smoke_test.py
```

The gateway is on http://localhost:8080 (API docs at `/api/docs`). Individual
services are not published; to poke at one, add a `ports:` entry in
`docker-compose.yml` or use `scripts/dev_stack.py`, which runs every service on
its own local port (8081-8088) without Docker.

Locally, Firestore, Pub/Sub and Cloud Storage are replaced by Postgres-backed,
in-process and folder-backed stand-ins (`DOCSTORE`, `PUBSUB`, `OBJSTORE`).

## Run one service's tests

```bash
docker compose up -d postgres
cd services/ledger
pip install -r requirements-dev.txt
pytest -q
```

Tests create and drop their own throwaway database. The concurrency tests open
many connections; if Postgres refuses them, raise `max_connections` (the
compose file already does).

## Database rules

- One database user per service; a service can only see its own schema.
- Services never join across each other's tables; they call each other's APIs.
- Schema changes are new Alembic migrations, never edits to old ones. On
  Kubernetes, ArgoCD runs `alembic upgrade head` as a PreSync Job before rolling
  out new pods.
- Money is always `bigint` paise. Only the Ledger service changes balances.

```bash
cd services/account
export DATABASE_URL=postgresql://account_svc:account_dev@localhost:5432/core_banking
alembic revision -m "add nominee address"     # then write the SQL in the new file
alembic upgrade head
```

## Changing a gRPC contract

Edit the file under `proto/`, then regenerate the committed stubs:

```bash
pip install grpcio-tools==1.68.1
python scripts/gen_protos.py
```

## Changing how a service is deployed

Edit `scripts/gen_manifests.py` (replicas, autoscaling, environment) and run it;
commit the regenerated files under `deploy/bank`. CI fails if stubs or
manifests are out of date.
