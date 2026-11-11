# Roadmap: what to build before going to GCP

Everything here can be built and tested without a cloud account (local stand-ins,
emulators, docker compose). Each item lists what it adds, which assignment
requirement it strengthens, and how it will be tested. Rough size is in working
sessions.

Status today (11 Nov): all 12 build steps done and tested locally; nothing applied
on GCP yet.

**Done since this list was written:** item 1 (CI: compose job with real Kafka and a
node failure, adapter contract tests against emulators), item 3 (KYC document upload)
and item 4 (PDF statements), plus from Tier 3 the web security headers, a request
size limit and a rate limit on the public statement check. Two things were built
differently from the notes below, on purpose: a rejected KYC file is deleted at once
instead of after 30 days, and a monthly statement is built the first time it is
asked for instead of for every account at month end (see docs/design.md).

**Still open:** item 2 (local Prometheus and Grafana, request IDs), item 5 (ML
anomaly score), item 6 (mule-account recovery), Tier 2, and the rest of Tier 3.

## Tier 1: build these first

### 1. Prove the Kafka path and the cloud adapters in CI (testability) — 1 session

What exists only as unit tests today gets exercised for real on every push.

- **`compose-e2e` CI job.** `docker compose up --build` on the GitHub runner (real
  Kafka, real images, the same Dockerfile GKE uses), then the smoke test, fraud
  simulator and browser test. Ends the "check Kafka by hand in Codespaces" step.
- **Chaos step in the same job:** `docker compose stop dyna-1`, run the smoke test
  again (section 7 tests against the dead node), start it, check hinted handoff
  drains.
- **Emulator tests for the Google adapters** (`ubcommon/docstore.py`, `pubsub.py`,
  `objstore.py`): Firestore and Pub/Sub emulators from the gcloud SDK, and
  fake-gcs-server for Cloud Storage, in a CI job. Today only the Postgres/in-process/
  folder stand-ins are tested.
- **Image smoke check:** every built image starts and answers `/healthz` (catches a
  missing file in the image, like the QR libraries were missing from the repo).

Requirements: (b), (g), and confidence for the GCP day.

### 2. Local Prometheus + Grafana (observability before GKE) — 0.5 session

- A `monitoring` profile in docker compose: Prometheus scraping every service and
  Grafana loading `deploy/monitoring/bank-overview.json`, the same dashboard GKE uses.
  `docker compose --profile monitoring up`.
- **Alert rules** (PrometheusRule on GKE, rules file locally): ledger out of balance,
  DynaStore hints piling up, decline rate spike, a service down, Kafka consumer lag.
- **Request ID through every hop:** the gateway sets `X-Request-ID`, every service
  logs it and passes it on (REST headers, gRPC metadata, Kafka headers), so one
  payment can be followed across all services in Cloud Logging. Shown on the payment
  slip as the reference.
- Test: a CI check that every panel's query returns data after the smoke test.

Requirements: (h).

### 3. KYC document upload (Cloud Storage) — 1 session

Today staff press "Verify KYC" with nothing to look at.

- **Customer:** uploads PAN card, address proof and a photo from the web app
  (JPEG/PNG/PDF, at most 5 MB, file type checked from the file's bytes, not the
  name). Aadhaar must be the masked version (only the last 4 digits visible); the
  page says so.
- **Storage:** files in a private bucket at `kyc/<customer>/<document>`; only
  metadata (type, size, SHA-256, status) in Postgres. Locally the folder stand-in.
- **Staff (KYC role):** a review queue on the branch desk; opening a document gives
  a short-lived link (signed URL on GCS, streamed through the gateway locally).
  Approve verifies KYC; reject needs a reason, the customer is notified and can
  upload again.
- **Audit:** every upload, view and decision goes into the account's hash chain.
- **Retention:** rejected files deleted after 30 days by a bucket lifecycle rule
  (Terraform).
- Tests: unit tests (type checks, size limit, who may view), browser-test steps
  (upload, approve, reject and re-upload), the emulator test from item 1.

Requirements: (g) Cloud Storage doing real work.

### 4. Monthly PDF statements (Cloud Storage) — 1 session

- At month end the end-of-day batch writes a PDF statement for every account:
  opening and closing balance, every entry, interest, holds, deposits summary.
- Stored at `statements/<account>/<yyyy-mm>.pdf`; the customer downloads it from the
  account page; staff can open any statement.
- **Tamper evidence:** the footer carries a SHA-256 of the statement's entries;
  `GET /api/statements/verify` re-checks it against the ledger.
- Customer can also ask for a statement for any date range (generated on demand).
- Notification "Your October statement is ready" through the Cloud Function.
- Library: fpdf2 (pure Python, small image).
- Tests: totals match the ledger, the hash verifies and fails after a change,
  fast-forward 35 days in the smoke test produces a statement.

Requirements: (g), and a realistic banking feature.

### 5. ML anomaly score in the risk engine — 1 to 1.5 sessions

A second opinion next to the rules, not a replacement.

- **Features per payment:** amount against the customer's own history (z-score),
  hour of day against their usual hours, payee novelty, device age, velocity,
  account age, days since last payment. All already in DynaStore, so no new
  storage on the hot path.
- **Model:** Isolation Forest (scikit-learn), trained by `ml/train.py` on traffic
  from the simulator's normal persona; evaluated against the fraud personas
  (precision and recall written to `ml/report.md`).
- **Model file in Cloud Storage** (`models/risk/<version>.joblib`); the risk
  service loads it at start and on change; the version is in every decision log.
- **Shadow mode first:** a new rule `ML_ANOMALY` with `shadow: true` in
  `rules.yaml`: scored and logged, not counted. A Grafana panel compares it with
  the rules. Switching it on is a one-line GitOps change, which is the story for
  the demo.
- The payment slip shows "Unusual for this customer" when it fires.
- Honest limits in the design document: synthetic training data shows the
  mechanism, not real-world accuracy.
- Tests: the model loads, scores fall in range, the normal persona is mostly below
  the threshold and the fraud personas mostly above, and a missing model file
  disables the rule instead of failing payments.

Requirements: (f)/(h) story, and fraud detection, the project's emphasis.

### 6. Recover money from mule accounts on confirmed fraud — 0.5 session

Today confirming fraud cancels a held payment and freezes the victim's account,
but money from a payment that already completed stays with the receiver.

- When an analyst confirms fraud on a completed payment, put a **lien** (a hold) on
  the receiving account for the amount still there, freeze it for outgoing
  payments, and open a linked case for it.
- A second analyst approves the **reversal** (maker-checker); the ledger moves the
  money back as a reversal journal, never by editing entries.
- Tests: the lien never exceeds the balance; the reversal balances; the receiver
  can still receive while frozen.

Requirements: fraud and risk emphasis, and shows the ledger's holds.

## Tier 2: worth doing if time allows

### 7. Bulk payments from a CSV file (message queue showcase) — 1 session

For current accounts (salary, vendor payouts). The file goes to Cloud Storage;
every row becomes a message on a Kafka topic; workers push each row through the
normal payment pipeline with its own idempotency key (`batch:row`), so a retried
batch never pays twice. The customer sees progress and downloads a result file.
Each row is still risk-scored, so a bulk file can't bypass fraud checks.

### 8. Standing instructions (scheduled payments) — 0.5 session

"Pay rent of Rs 15,000 on the 1st of every month." The end-of-day batch runs them
through the normal pipeline; failures notify the customer; three failures in a row
pause the instruction.

### 9. Money-flow view for analysts — 1 session

On a case, a graph of where the money went (two hops out from the account), built
from ledger entries and the Flink mule-ring results. Makes the stream-processing
output visible in the UI.

### 10. Spending insights for customers — 0.5 session

A monthly chart of money in and out by category (from payment notes and payees)
on the accounts page.

## Tier 3: hardening

- **Secret scanning** (gitleaks) and **dependency audit** (pip-audit) in CI; the repo
  is public.
- **Container scan** (Trivy) on the built images.
- **Security headers** on the web app: Content-Security-Policy, no framing.
- **Account closure** flow (balance to zero, deposits closed, final statement).

## After that: GCP (needs everyone's billing account)

Bootstrap Terraform, `make up`, GitHub repository variables, release workflow,
ArgoCD sync, Flink on Dataproc, `make smoke`, `make ui-test`, `make simulate`,
`make loadtest` (watch HPAs), Grafana screenshots, `make down`.

## Not planned

Loans, cards and real UPI integration: each is a project on its own and would not
add to any assignment requirement.
