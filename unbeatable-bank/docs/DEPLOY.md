# Deploying to Google Cloud

Everything is created by Terraform and deployed by ArgoCD. The only manual
steps are the ones that cannot be code: redeeming coupons, granting billing
roles and setting four GitHub variables.

Work from a Linux shell with `gcloud`, `terraform`, `kubectl` and `make`.
A GitHub Codespace works well:

```bash
# one-time tools in a Codespace
curl -fsSL https://apt.releases.hashicorp.com/gpg | sudo gpg --dearmor -o /usr/share/keyrings/hashicorp.gpg
echo "deb [signed-by=/usr/share/keyrings/hashicorp.gpg] https://apt.releases.hashicorp.com $(lsb_release -cs) main" | sudo tee /etc/apt/sources.list.d/hashicorp.list
curl -fsSL https://packages.cloud.google.com/apt/doc/apt-key.gpg | sudo gpg --dearmor -o /usr/share/keyrings/cloud.google.gpg
echo "deb [signed-by=/usr/share/keyrings/cloud.google.gpg] https://packages.cloud.google.com/apt cloud-sdk main" | sudo tee /etc/apt/sources.list.d/google-cloud-sdk.list
sudo apt-get update && sudo apt-get install -y terraform google-cloud-cli google-cloud-cli-gke-gcloud-auth-plugin kubectl

gcloud auth login --no-launch-browser
gcloud auth application-default login --no-launch-browser
```

## 1. Project, state bucket, budgets (once)

```bash
cd infra/bootstrap
cp terraform.tfvars.example terraform.tfvars     # project id, billing account(s), emails
terraform init && terraform apply
```

Then move the state into the bucket it created, as described in
`infra/bootstrap/README.md`. Note the outputs `github_wif_provider` and
`github_ci_service_account`.

## 2. Platform and services

```bash
cp infra/main/terraform.tfvars.example infra/main/terraform.tfvars   # project id, staff logins
make up
```

`make up` takes about 10 minutes the first time: network, GKE, Cloud SQL,
Firestore, buckets, Pub/Sub, the Notification function, then ArgoCD.

## 3. Let GitHub build and release the images (once)

In the repository: **Settings → Secrets and variables → Actions → Variables**:

| Variable | Value |
|---|---|
| `GCP_PROJECT_ID` | your project id |
| `GCP_REGION` | `us-central1` |
| `WIF_PROVIDER` | output `github_wif_provider` from step 1 |
| `CI_SERVICE_ACCOUNT` | output `github_ci_service_account` from step 1 |

Then **Actions → Release → Run workflow**. It builds the ten images, pushes
them to Artifact Registry and commits the image tag to
`deploy/bank/kustomization.yaml`. ArgoCD deploys that commit. From now on every
merge to `main` that touches service code does the same.

If `main` is protected, allow the `github-actions` bot to push to it (or the
release commit will be rejected).

## 4. Check it

```bash
make status          # ArgoCD applications, pods, autoscalers
make url             # public URL of the bank
ADMIN_PASSWORD=... ANALYST_PASSWORD=... make smoke
```

`make argocd` and `make grafana` open the two UIs through a port-forward.

## 5. Flink job

```bash
make flink-up        # creates Dataproc and submits the job (about 5 minutes)
make flink-down      # delete it again; it also deletes itself after an idle hour
```

To look at it: `gcloud compute ssh ub-flink-m --tunnel-through-iap`, then
`yarn application -list` and `tail -f /var/log/audit-analytics.log`.

## 6. Stop paying

```bash
make down
```

Scales the cluster to zero nodes, stops Cloud SQL, deletes Dataproc and the
load balancer. Data is kept. What remains costs a few cents a day (disks).
`make up` brings everything back; the public IP will be a new one.

## Demo script

| Step | Command | Shows |
|---|---|---|
| 1 | `make status`, ArgoCD UI | Everything deployed from Git (d) |
| 2 | `make smoke` | All services working together; REST, gRPC, Kafka, Pub/Sub (b) |
| 3 | `make simulate` | Each fraud persona caught by a different layer |
| 4 | Web app, staff view: DynaStore page, switch a node off, run `make simulate` again, switch it on | Leaderless store keeps serving; hinted handoff and repair in Grafana (f) |
| 5 | `make loadtest` with `kubectl -n bank get hpa -w` | Autoscaling under sustained load (c) |
| 6 | `make flink-up`, then `python loadtest/simulator.py http://<ip> --with-flink` | Windowed aggregation on Dataproc, results back on Kafka, mule ring alert (e) |
| 7 | `make grafana`; Cloud Logging in the console | Metrics and centralised logs, including the Flink job (h) |
| 8 | Edit a limit in `services/ratelimit/config/policies.yaml`, run `python scripts/gen_manifests.py`, merge | A configuration change rolled out by GitOps |
| 9 | Console: Cloud SQL, Firestore, Cloud Storage | Three storage products (g) |
| 10 | `make down` | Cost control |

To check a deployment from your laptop: `make smoke` (API), `make ui-test` (web app
in a browser) and `make simulate` (fraud personas).

## Not yet verified on GCP

The application code is tested locally end to end. The Terraform validates and
the manifests pass schema checks, but none of it had been applied to a real
project when this was written. Expect to fix small things on the first
`make up`; the most likely places are the Dataproc submit script
(`services/audit-analytics/submit.sh`), chart versions in `deploy/argocd/apps`
and IAM roles for the Cloud Function build. The `e2e` job in CI has only been run by
hand on a development machine, not yet on GitHub's runners.
