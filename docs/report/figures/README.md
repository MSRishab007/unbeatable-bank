# Figures of the design document

The design document (`../main.tex`) includes every file here.

## Diagrams

| File | Section | Shows |
|---|---|---|
| `deployment.pdf` | 3.1 | Cloud deployment architecture |
| `terraform-layers.pdf` | 3.5 | The three Terraform layers and their dependencies |
| `gitops.pdf` | 3.6 | Delivery pipeline from commit to rollout |
| `services.pdf` | 4.1 | The twelve services and how each pair communicates |
| `payment-sequence.pdf` | 4.3 | Sequence of one payment through the services |
| `outbox.pdf` | 5.5 | Transactional outbox and idempotent consumers |
| `fraud-layers.pdf` | 6.2 | The five layers of defence |
| `dynastore-ring.tex` | 7.2 | DynaStore consistent hashing ring (TikZ) |
| `flink-pipeline.pdf` | 7.3 | Stream processing pipeline on Dataproc |

Each `.pdf` diagram is built from the `.mmd` Mermaid source next to it:

```bash
mmdc -c mermaid.json -i <name>.mmd -o <name>.pdf -f -b white
```

## Screenshots

| File | Section | Shows |
|---|---|---|
| `app-overview.png` | 2.1 | Customer home page and staff fraud desk |
| `dynastore-ui.png` | 7.2 | DynaStore status page during the load test |
| `flink-results.png` | 7.3 | Per-minute statistics and the cases opened by fraud signals |
| `grafana-loadtest.png` | 8.1 | Grafana during the final load test |
| `cloud-logging.png` | 8.3 | Service logs in Cloud Logging |
| `bits-logo.png` | Cover | Institute logo |
