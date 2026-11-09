# Unbeatable Bank

Cloud-native banking system with real-time fraud detection and a Dynamo-style leaderless store, built on Google Cloud for CS/SS G527 Cloud Computing (BITS Pilani).

> Every transaction has to get past five layers of defense.

## Repository layout

| Folder | Contents |
|---|---|
| `infra/bootstrap/` | One-time Terraform: project, APIs, IAM, state bucket, budgets, GitHub Actions auth |
| `infra/main/` | Terraform for the platform: VPC, GKE, Cloud SQL, Firestore, Pub/Sub, GCS, Dataproc |
| `services/` | One folder per microservice |
| `deploy/` | Kubernetes manifests / Helm charts tracked by ArgoCD |
| `docs/` | Design document, `BILLING.md`, `task_division.txt` |
| `infra/local/` | Local-only setup used by `docker-compose.yml` |

## Local development

`docker compose up --build` starts Postgres and the services built so far.
See [services/README.md](services/README.md).

## Ground rules

- All infrastructure is created by Terraform; all deployments go through ArgoCD. No console clicks, no `kubectl apply`.
- Never commit state files, `terraform.tfvars` or secrets.
- Run `make down` at the end of every cloud session.
