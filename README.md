# Unbeatable Bank

A cloud-native bank with real-time fraud detection and its own Dynamo-style
leaderless store, built on Google Cloud for CS/SS G527 Cloud Computing (BITS Pilani).

> Every payment has to get past five layers of defence.

- **Design document:** [docs/design.md](docs/design.md)
- **Deploying to GCP and the demo script:** [docs/DEPLOY.md](docs/DEPLOY.md)
- **Who did what:** [docs/task_division.txt](docs/task_division.txt)
- **What comes next:** [docs/ROADMAP.md](docs/ROADMAP.md)

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
| `scripts/` | Smoke test, browser test, local stack runner, code and manifest generators |
| `docs/` | Design document, deployment guide, billing notes, task division |

## Run it on your machine

With Docker (or a GitHub Codespace):

```bash
docker compose up --build            # first build takes a few minutes
pip install httpx
python scripts/smoke_test.py         # about 70 end-to-end checks through the gateway
python loadtest/simulator.py         # fraud personas
pip install playwright && playwright install chromium
python scripts/ui_test.py            # 42 checks of both web apps in a real browser
```

Open http://localhost:8080 for the web app and http://localhost:8080/api/docs for the API.
Staff logins for local development: `admin` / `admin-dev-pass`, `analyst1` / `analyst-dev-pass`,
`teller1` / `teller-dev-pass`, `kyc1` / `kyc-dev-pass`.

## The web app

Two front ends, both served by the gateway from `services/gateway/static`:

- **`/`: the customer app** (`web/`): React, TypeScript, Tailwind and shadcn/ui-style
  components, with Motion for animation (the rolling "We are Un…" headline, the
  swipe-to-pay slider, PIN and one-time-code boxes). `make web` (or `npm run build` in
  `web/`) builds it into `services/gateway/static`, and the build is committed, so the
  gateway image needs no Node. `npm run dev` in `web/` serves it with hot reload against a
  gateway on localhost:8080.
- **`/classic/`: the original app**, plain HTML, CSS and JavaScript with no build step. It
  has the staff console and the few customer screens not rebuilt yet (statements,
  nominees, scan-a-QR with the camera). Both apps share the sign-in (same browser storage),
  so a link from one to the other keeps you signed in.

- **Customers:** sign up, upload KYC documents, open savings and current accounts
  (single or joint), passbook, monthly PDF statements and statements for any dates,
  pay by account number, pay ID or QR code (camera or a picture of the code), OTP
  confirmation when a payment looks unusual, fixed and recurring deposits with live
  quotes and early closure, inbox, devices and sign-in history.
- **Staff** (the Staff tab on the sign-in page): fraud desk with cases and held
  payments, two-person approvals, branch desk (customer search, KYC document
  review, cash deposits, audit-trail check) and a System page with the live
  DynaStore ring, fault injection, ledger reconciliation and the bank calendar.
- **Anyone:** "Check it is genuine" on the sign-in page tells someone holding a
  statement PDF whether the bank issued exactly that file.

Every payment is shown as a slip with the six checks it went through and which
one stopped it. In a Codespace, open the forwarded port 8080; it is served over
https there, so the camera scanner works.

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
