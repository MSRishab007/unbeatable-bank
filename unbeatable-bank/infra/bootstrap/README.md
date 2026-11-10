# infra/bootstrap

One-time foundation for the GCP project. Creates:

- the project, linked to the active billing account
- all APIs the system needs
- a versioned GCS bucket for shared Terraform state
- teammates' IAM access
- a $50 budget with $15 / $30 / $40 alerts on every collected billing account
- Workload Identity Federation so GitHub Actions can push images without key files

## First run (Member A)

```bash
gcloud auth login
gcloud auth application-default login

cd infra/bootstrap
cp terraform.tfvars.example terraform.tfvars   # fill it in
terraform init
terraform apply
```

If budget creation fails on the first run with a permissions or quota-project
error, run `terraform apply` once more: the Budgets API takes a minute to
become usable after it is enabled.

## Move state to the shared bucket

1. Uncomment the `backend "gcs"` block in `versions.tf` and set the bucket to
   the `tfstate_bucket` output.
2. Run `terraform init -migrate-state` and answer `yes`.
3. Delete the local `terraform.tfstate*` files.

## Adding teammates later

Add their email under `team_members`, their billing ID under `billing_accounts`
(only after they've granted the team billing roles), then `terraform apply`.

## Switching billing accounts

Change `active_billing` (e.g. `"b"`), open a PR, merge, `terraform apply`.
Log the switch in `docs/BILLING.md`. Switch at the $40 alert, never at $50.

## Teammate check

Clone, `gcloud auth application-default login`, then in this folder:
`terraform init && terraform plan` should report **No changes**.
