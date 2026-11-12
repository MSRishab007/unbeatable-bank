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

**Deployed on GCP (11 Nov):** project `unbeatable-bank-17`, everything except Flink is
running and `make smoke` passes there, including Kafka.

**Next on GCP: test Flink (requirement e).** It has only run locally so far.

1. `make flink-up` (about 10 minutes; Dataproc adds about $0.20/hour while it runs).
   On the free-trial billing account it may hit a CPU quota: then switch
   `active_billing` to a USD account first.
2. About 5 minutes later: `python loadtest/simulator.py http://<bank IP> --with-flink`
3. Results on the output topic:
   `kubectl -n kafka exec bank-kafka-combined-0 -- /opt/kafka/bin/kafka-console-consumer.sh --bootstrap-server localhost:9092 --topic analytics-results --from-beginning --max-messages 5 --timeout-ms 180000`
   and the System page's "Stream analytics" fills in.
4. If nothing arrives: the job log is `/var/log/audit-analytics.log` on the Dataproc
   master (`gcloud compute ssh <cluster>-m --zone us-central1-a`).
5. `make flink-down` afterwards to stop only Flink (`make down` also stops it).

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

## Feature backlog (agreed 11 Nov): build all of this, then polish the UI

The bank works end to end but is basic. Everything below is built and tested first;
the UI redesign (modern, animated) comes last, once the features are settled.
Each item gets unit tests, smoke-test checks and browser-test steps like the rest.

### A. Security a real bank would not launch without — DONE (11 Nov)

Built, with unit tests, smoke-test section 1c/4/8c and browser-test steps. As built:

A1. **Two PINs.** Payment PIN (6 digits): payments, adding a payee, raising a limit,
adding a joint holder. App PIN (4 digits): open/close FD and RD, nominees, unlocking
the app. The app PIN may not be the first or last four digits of the payment PIN;
trivial PINs (111111, 123456) are refused. The gateway checks the `X-Pin` header with
Identity before forwarding (the PIN never reaches the service doing the work). Stored
as HMAC-SHA256 with a per-row salt and a server-side pepper (`PIN_PEPPER`): a PIN has
too few values for a slow hash to help; the 3-tries lock is what protects it.
3 wrong: locked 24 h, customer told, `pin_locked` risk signal. Forgot/locked: password
+ code (email and inbox), then a `credential_reset` risk signal for a day. Set on first
use (the app asks) or on the Security page; change with the current PIN.

A2. **Sessions.** Every signed-in request is checked with Identity (`ACTIVE`, `LOCKED`,
`ENDED`); the gateway reuses an ACTIVE answer for 5 s, and forgets it at once on the
pod that ended it. Customer sessions **lock after 5 minutes idle** (423) and unlock
with the app PIN or password, carrying on where they were; the app also shows the
lock screen after 5 minutes without input, and has a Lock button. Background polling
(`X-Background: 1`) neither keeps a session awake nor triggers the lock screen.
Refresh tokens rotate; a reused one ends the session (and raises `token_reuse`).
Staff have revocable sessions that end after 15 minutes idle, and a logout.

A3. **Passwords:** change (current password; every other session ends); forgot (code
by email + date of birth; every session ends; a wrong date of birth uses up the code).

A4. **New-device sign-in needs a code** (email + inbox of the customer's other
devices). The sign-up device is trusted; removing a device makes it earn trust again.

A5. **Email/phone change:** password + a code to the *current* email/app; the old
address is told. Payments are **not** blocked afterwards (agreed): a 3-day
`contact_changed` signal adds weight only when paying an untrusted payee.

A6. **Block everything:** freezes every account, ends every session, opens a HIGH
ACCOUNT_TAKEOVER case. Works while the app is locked, needs no PIN.

A7. **Limits:** lowering applies at once; raising needs the payment PIN and takes
effect 24 hours later (a new request replaces a waiting raise).

Also done from later sections: **B1** (saved payees: add with PIN, nickname, 24-hour
cooling shown, remove), **C1** (app PIN for deposits), **C3** (nominees for accounts
and deposits: up to four, shares to 100%, guardian for a minor, app PIN), and the
D4 rules for PIN locks, credential resets and contact changes.

### B. Payments

B1. **Beneficiaries:** save payees with a nickname (PIN to add), first 24 hours at a
lower limit (exists as a rule today; make it visible), remove, list.
B2. **Request money (collect request):** ask someone to pay you; they approve with
their PIN or decline. Risk rule for the "you'll receive money, enter your PIN" scam:
first-time collect requests show a clear warning.
B3. **Scheduled and recurring payments** (standing instructions) run by the end-of-day
batch through the normal pipeline; paused after 3 failures.
B4. **Payment receipt** as a PDF for any completed payment.
B5. **Passbook search and filters** (dates, amount, type) and CSV export.
B6. **Bulk payments from a CSV** for current accounts, rows through a Kafka queue,
each row still risk-checked; result file to download.
B7. **Disputes:** customer disputes a completed payment; case; if fraud is confirmed,
a lien on the receiving (mule) account and a reversal approved by two analysts.

### C. Deposits and accounts

C1. **PIN to open, close or break an FD/RD** (with A1).
C2. **FD at maturity:** choose payout or auto-renew (principal, or principal +
interest) when booking; the end-of-day batch renews at the then-current rate.
C3. **Nominee** for accounts and deposits (name, relation, share), PIN to change.
C4. **Sweep-in FD:** savings above a chosen amount move into an FD automatically and
come back when the balance runs short (shows the ledger's holds and journals).
C5. **TDS on FD interest** (10% above the yearly threshold, Form 15G/15H to waive) and
a yearly interest certificate PDF.
C6. **Joint accounts "jointly" mode:** both holders approve each payment (today
either holder can pay alone).
C7. **Overdraft for current accounts:** customer applies, staff approve
(maker-checker), interest on the used amount at end of day.
C8. **Close an account:** balance to another account, deposits closed or kept, a final
statement, then the account number is retired.
C9. **Minimum balance and charges** (optional; shows charges as ledger journals).

### D. Fraud and risk (the project's emphasis)

D1. **ML anomaly score** in shadow mode, then on (Tier 1 item 5 above).
D2. **Mule recovery** with liens (with B7).
D3. **Money-flow graph** for analysts (Tier 2 item 9).
D4. **New fraud rules:** PIN failures, SIM swap (A5), collect-request scam (B2),
dormant account suddenly active, many new beneficiaries in a day, payment right after
a password or PIN reset.
D5. **Case work:** assign cases to analysts, a due time by severity, notes, escalation;
"my cases" view.
D6. **Customer-facing fraud explanations:** why a payment was held, in plain words.

### E. Notifications and the customer

E1. **OTPs and alerts over free channels**, all sent by the Notification Cloud Function.
BUILT (12 Nov): every message can choose `channels` (email, rcs) besides the inbox;
by default codes, security and fraud messages go everywhere, routine ones inbox only.
The address and phone come from `contacts/<customer_id>` (kept by Identity).
- Email by Gmail SMTP with an app password (`email_from` + `smtp_password`) and Brevo
  (`brevo_api_key`) together: Gmail first; at Gmail's daily limit it pauses Gmail for 24
  hours and Brevo takes over; any other Gmail failure falls back to Brevo for that message.
- Admin switches (staff console, System > Notifications): email on/off, RCS on/off,
  email by automatic / Gmail only / Brevo only, "try Gmail again", today's counts.
- RCS: real sending is written (RBM API) but not yet tried; until `rbm_agent_id` and
  `rbm_credentials` are set, RCS messages land in the Firestore collection `rcs_outbox`.
  To make it real: through a Google RCS Business Messaging *test* agent: messages to registered
  test devices are not charged, and an unlaunched agent can only message those
  devices (each must accept an invite; 200 invites in total). Enough for the team's
  and the examiners' phones; a public launch would need brand verification and is paid.
- In-app inbox stays the fallback that always works. Plain SMS stays simulated (needs
  paid DLT registration in India).
E2. Notification preferences (what by email, what in-app only).
E3. Profile: change address, download all my data.

### F. Staff and operations

F1. Audit log viewer for every staff action.
F2. Maker-checker for limit raises, overdrafts and reversals (as for unfreeze today).
F3. End-of-day report viewer (the reports already go to Cloud Storage).

### G. Cloud requirements still to demonstrate on GCP

G1. Flink on Dataproc (see "Next on GCP" above).
G2. Load test on GKE showing the autoscalers scale out (requirement c).
G3. Local Prometheus + Grafana, alert rules, request IDs (Tier 1 item 2).
G4. Fixed address for the demo (static IP, optional domain and https).
G5. Security scans in CI (gitleaks, pip-audit, Trivy).

### H. Last: UI redesign

A complete visual redesign (modern, sleek, with purposeful animation) of the
customer app and staff console, once A to F are done. Same pages and API; new look,
motion, and mobile-first layouts.

### Suggested order

A, B1, C1 and C3 are done. Next: B2-B4, C2, the rest of D4, D5-D6, then G1-G2 on GCP, then B5-B7, C4-C9, D1-D3, E, F, G3-G5,
and H last.

## Not planned

Loans, cards and real UPI integration: each is a project on its own and would not
add to any assignment requirement.
