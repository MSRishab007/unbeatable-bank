# Unbeatable Bank

A cloud-native retail bank on Google Cloud that admits payments in real time and monitors
them for fraud. Every payment passes five layers of defence before money moves, and the
fast-changing data those layers rely on lives in **DynaStore**, a Dynamo-style leaderless
key-value store built for this project.

**Scenario:** S17, Banking Transaction Monitoring. **Concept:** leaderless replication and
Dynamo-style storage.

| | |
|---|---|
| Design document | [docs/report/main.pdf](docs/report/main.pdf) (LaTeX source: [docs/report/main.tex](docs/report/main.tex)) |
| Task division | [task_division.txt](task_division.txt) |
| Live site | https://unbeatable-bank.duckdns.org (while the cluster is running) |
| Deployment guide and demo script | [docs/DEPLOY.md](docs/DEPLOY.md) |

## Team

| ID number | Name | Requirements | Services |
|---|---|---|---|
| 2025H1030064 | Ashish Shetty | (a) Infrastructure as code, (d) GitOps | Identity and Authentication, Account, Ledger |
| 2025H1030066 | S Rishab | (b) Serverless and communication, (c) GKE, autoscaling and load testing | API Gateway, Transaction Intake, Notification |
| 2025H1030060 | Jenil Gandhi | (f) Leaderless replication concept, (g) Storage products | DynaStore, Rate Limit Coordinator, Risk Detection |
| 2025H1030049 | Manmay Maheshwari | (e) Stream processing with Flink, (h) Observability | Audit Analytics, Fraud Alert, Deposits |

## Where each requirement is met

| Requirement | Implementation | Where to look |
|---|---|---|
| (a) Infrastructure as code | Terraform in three layers: project and IAM, cloud resources, in-cluster platform | `infra/bootstrap`, `infra/main`, `infra/platform`, `Makefile` |
| (b) Serverless; REST, gRPC and queues | Notification Service as a Cloud Function triggered by Pub/Sub; REST through the gateway; gRPC on the payment path; Kafka for events | `functions/notification`, `proto/`, `libs/common/ubcommon/outbox.py`, `kafka.py`, `pubsub.py` |
| (c) GKE, HPA and load test | Twelve services on GKE; six CPU-based autoscalers; k6 load test in three runs | `deploy/bank`, `scripts/gen_manifests.py`, `loadtest/payments.js` |
| (d) GitOps | ArgoCD app-of-apps tracking this repository; a release is a merged deploy pull request | `deploy/argocd`, `.github/workflows/release.yml` |
| (e) Flink on a managed cluster | Flink SQL on Dataproc: reads `txn-events`, one-minute windows with unique customers, fraud patterns; writes `analytics-results` and `risk-signals` | `services/audit-analytics`, `infra/main/dataproc.tf` |
| (f) Concept | DynaStore: consistent hashing, N=3 with tunable quorums, vector clocks, PN-counter CRDTs, sloppy quorum, hinted handoff, read repair, anti-entropy | `services/dynastore` |
| (g) Three or more storage products | Cloud SQL (money, customers), Firestore (sessions, inbox, analytics), Cloud Storage (KYC, statements, checkpoints), plus DynaStore and Kafka | `infra/main/sql.tf`, `storage.tf`, `libs/common/ubcommon/docstore.py`, `objstore.py` |
| (h) Observability | Prometheus and Grafana with an RPS, error, latency and cluster-health dashboard; every service and the Flink job log to Cloud Logging | `deploy/monitoring`, `libs/common/ubcommon/metrics.py`, `logging_setup.py` |

## Repository layout

| Folder | Contents |
|---|---|
| `services/` | The microservices: gateway, identity, account, ledger, intake, ratelimit, risk, fraud, deposits, dynastore, and the Flink job `audit-analytics` |
| `functions/notification/` | The serverless Notification Service |
| `libs/common/` | Shared library: authentication, metrics, logging, outbox, Kafka, Pub/Sub, document and object stores, gRPC |
| `proto/` | gRPC contracts |
| `infra/` | Terraform layers, and a local database for development |
| `deploy/` | What ArgoCD deploys: the bank services, Kafka, monitoring, the edge, and the ArgoCD applications |
| `web/` | Source of the customer web app (React, TypeScript) |
| `loadtest/` | k6 load test and the fraud simulator |
| `scripts/` | Smoke test, browser test, local stack and manifest generator |
| `docs/` | Design document, deployment guide, billing notes |

## The web application

The API Gateway serves both front ends from one public address.

- **Customer app at `/`** (`web/`): sign-up and KYC upload, savings and current accounts,
  payments by account number, pay ID, mobile number or QR code, a full-screen view of each
  payment passing the bank's six checks with the time it took, fixed and recurring
  deposits, inbox and security settings. Statements and nominees open in the classic
  pages under `/classic/`.
- **Staff console at `/classic/#/staff`**: fraud desk with cases and held payments,
  two-person approvals, branch desk (KYC review, cash deposits, audit-trail check), and a
  system page with the live DynaStore ring and fault injection.

## Running it

On Google Cloud (full steps in [docs/DEPLOY.md](docs/DEPLOY.md)):

```bash
make up              # cluster, database and every service, deployed by ArgoCD
make status          # ArgoCD applications, pods and autoscalers
make flink-up        # Dataproc cluster and the Flink job
make simulate ARGS=--with-flink
make down            # stop everything that costs money (data is kept)
```

On one machine, with Docker:

```bash
docker compose up --build
python scripts/smoke_test.py
```

Then open http://localhost:8080. Without any backend, `make demo` runs the customer app
with built-in sample data.

## Tests

Every pull request runs these in GitHub Actions:

| Suite | Size | Run with |
|---|---|---|
| Unit and service tests (pytest), per service, the function and the shared library | 358 tests | `cd services/<name> && pytest` |
| Smoke test through the public API | about 90 checks | `python scripts/smoke_test.py` |
| Fraud simulator, one scenario per defence layer | 8 scenarios | `python loadtest/simulator.py` |
| Browser test of both web apps | 42 steps | `python scripts/ui_test.py` |
| Flink SQL queries on a local mini-cluster | 7 tests | `cd services/audit-analytics && pytest` |

CI also validates the Terraform, checks the Kubernetes manifests, builds every image and
type-checks the web app.

## Ground rules

- All infrastructure is created by Terraform, and all deployments go through ArgoCD.
- No state files, `terraform.tfvars` or secrets are committed.
- `make down` at the end of every cloud session.
