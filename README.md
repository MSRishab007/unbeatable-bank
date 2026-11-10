# Unbeatable Bank

A cloud-native bank with real-time fraud detection and its own Dynamo-style
leaderless store, built on Google Cloud for CS/SS G527 Cloud Computing (BITS Pilani).

> Every payment has to get past five layers of defence.

- **Design document:** [docs/design.md](docs/design.md)
- **Deploying to GCP and the demo script:** [docs/DEPLOY.md](docs/DEPLOY.md)
- **Who did what:** [docs/task_division.txt](docs/task_division.txt)

## What is in here

| Folder | Contents |
|---|---|
| `services/` | The microservices: gateway, identity, account, ledger, intake, ratelimit, risk, fraud, deposits, dynastore, and the Flink job `audit-analytics` |
| `functions/notification/` | The serverless Notification Service (Cloud Function) |
| `libs/common/` | Shared library: auth, metrics, logging, outbox, Kafka, Pub/Sub, document and object stores, gRPC stubs |
| `proto/` | gRPC contracts |
| `infra/` | Terraform: `bootstrap` (project, IAM, budgets), `main` (cloud resources), `platform` (ArgoCD, secrets), `local` (dev database) |
| `deploy/` | What ArgoCD deploys: `bank` (all services), `kafka`, `monitoring`, `argocd/apps` |
| `loadtest/` | k6 load test and the fraud simulator |
| `scripts/` | Smoke test, local stack runner, code and manifest generators |
| `docs/` | Design document, deployment guide, billing notes, task division |

## Run it on your machine

With Docker (or a GitHub Codespace):

```bash
docker compose up --build            # first build takes a few minutes
pip install httpx
python scripts/smoke_test.py         # 48 end-to-end checks through the gateway
python loadtest/simulator.py         # fraud personas
```

Open http://localhost:8080 for the web app and http://localhost:8080/api/docs for the API.
Staff logins for local development: `admin` / `admin-dev-pass`, `analyst1` / `analyst-dev-pass`,
`teller1` / `teller-dev-pass`.

Without Docker, with a local PostgreSQL: see the top of `scripts/dev_stack.py`.

After pulling changes that add a service or database, reset local data once:
`docker compose down -v`.

## Tests

Each service has its own suite (`cd services/<name> && pytest`), run in CI on
every pull request together with the Flink SQL tests, Terraform validation and
Kubernetes manifest checks. See [services/README.md](services/README.md).

## Ground rules

- All infrastructure is created by Terraform; all deployments go through ArgoCD.
  No console clicks, no `kubectl apply`.
- Never commit state files, `terraform.tfvars` or secrets.
- Run `make down` at the end of every cloud session.
