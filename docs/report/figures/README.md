# Figures for the design document

Mermaid sources go here as `<name>.mmd`; export each to PDF with
`mmdc -i <name>.mmd -o <name>.pdf` (or paste the source into mermaid.live and export).
Screenshots go here as `<name>.png`. In `main.tex`, replace the matching
`\mermaidplaceholder{<name>}...` or `\screenshotplaceholder{<name>}...` with
`\begin{figure}[H]\centering\includegraphics[width=\linewidth]{figures/<name>}\caption{...}\label{fig:<name>}\end{figure}`.

## Mermaid diagrams (10)

| # | File | Section | What it shows | Status |
|---|------|---------|---------------|--------|
| 1 | deployment.pdf | 3.1 Architecture Overview | Users and k6 to the Google load balancer, Traefik and the gateway in GKE; bank namespace (services, DynaStore), Kafka, ArgoCD, monitoring; Cloud SQL, Firestore, Cloud Storage, Pub/Sub, Cloud Function, Dataproc, Secret Manager, Artifact Registry, Cloud Logging; all in one VPC | to do |
| 2 | terraform-layers.pdf | 3.5 Infrastructure as Code | Three layers (bootstrap, main, platform), the resources each creates, and the dependencies between them | to do |
| 3 | gitops.pdf | 3.6 Continuous Delivery and GitOps | Commit to main, CI, image build to Artifact Registry, deploy pull request, merge, ArgoCD pre-sync migrations, rolling update | to do |
| 4 | services.pdf | 4.1 Service Decomposition | The twelve services and their calls (REST from the gateway, gRPC from Intake, DynaStore users, Kafka to Flink and Fraud Alert, Pub/Sub to the function) | to do |
| 5 | payment-sequence.pdf | 4.3 Lifecycle of a Payment | Sequence: client, gateway, Identity (session and PIN), Intake, Rate Limit, Account, Risk, Ledger, outbox to Kafka, Fraud Alert consumes the event and publishes the receipt to Pub/Sub, Notification function; the four risk outcomes | to do |
| 6 | communication.pdf | 5.1 Interconnection Overview | Same services, edges styled by mechanism (REST, gRPC, Kafka, Pub/Sub) with a legend | to do |
| 7 | outbox.pdf | 5.5 Transactional Outbox | Business row and outbox row in one transaction, relay sidecar to Kafka, idempotent consumers | to do |
| 8 | fraud-layers.pdf | 6.2 Defence in Depth | Payment through the five layers; risk score bands (allow < 30, OTP 30-59, hold 60-94, block >= 95); feedback from Flink and Fraud Alert into DynaStore | to do |
| 9 | dynastore-ring.pdf | 7.2 DynaStore | Hash ring with virtual nodes for three nodes, one key and its preference list | to do |
| 10 | flink-pipeline.pdf | 7.3 Stream Processing | txn-events into the Flink job on Dataproc, windows, analytics-results and risk-signals, Fraud Alert, checkpoints in Cloud Storage | to do |

## Screenshots (5, plus 1 optional)

| # | File | Section | What it shows | Status |
|---|------|---------|---------------|--------|
| 1 | app-overview.png | 2.1 Functional Scope | Phone screens: Home, Pay with the swipe control, the payment result with its six checks | to do |
| 2 | dynastore-ui.png | 7.2 DynaStore | The DynaStore status page: ring ownership, keys, hints waiting | have (13 Nov) |
| 3 | flink-results.png | 7.3 Stream Processing | analytics-results and risk-signals output, Stream analytics table, fraud cases | have (13 Nov) |
| 4 | grafana-loadtest.png | 8.1 Scalability | Grafana during load-test run 3: requests by service, errors, latency, HPA replicas | have (13 Nov) |
| 5 | cloud-logging.png | 8.3 Observability | Logs Explorer across the bank namespace | to do |
| 6 | simulator.png (optional) | 6.8 Evaluation with a Fraud Simulator | Fraud simulator summary, 8 of 8 OK, next to Table (simulator outcomes) | have (13 Nov) |
