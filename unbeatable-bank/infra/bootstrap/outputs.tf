output "project_id" {
  value = google_project.bank.project_id
}

output "project_number" {
  value = google_project.bank.number
}

output "active_billing_account" {
  value = var.billing_accounts[var.active_billing]
}

output "tfstate_bucket" {
  description = "Put this name in the backend \"gcs\" block of every Terraform layer."
  value       = google_storage_bucket.tfstate.name
}

output "github_wif_provider" {
  description = "Use as workload_identity_provider in google-github-actions/auth."
  value       = google_iam_workload_identity_pool_provider.github.name
}

output "github_ci_service_account" {
  description = "Use as service_account in google-github-actions/auth."
  value       = google_service_account.ci.email
}
