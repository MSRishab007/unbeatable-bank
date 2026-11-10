output "project_id" {
  value = var.project_id
}

output "region" {
  value = var.region
}

output "zone" {
  value = var.zone
}

output "cluster_name" {
  value = google_container_cluster.main.name
}

output "cluster_endpoint" {
  value     = google_container_cluster.main.endpoint
  sensitive = true
}

output "cluster_ca_certificate" {
  value     = google_container_cluster.main.master_auth[0].cluster_ca_certificate
  sensitive = true
}

output "image_registry" {
  description = "Prefix for service images: <registry>/<service>:<tag>"
  value       = "${var.region}-docker.pkg.dev/${var.project_id}/${google_artifact_registry_repository.images.repository_id}"
}

output "apps_service_account" {
  value = google_service_account.apps.email
}

output "workload_identity_users" {
  value = local.workload_identity_users
}

output "app_namespace" {
  value = local.app_namespace
}

output "reports_bucket" {
  value = google_storage_bucket.reports.name
}

output "firestore_database" {
  value = google_firestore_database.main.name
}

output "app_secrets" {
  description = "Copied into the cluster by infra/platform."
  value       = local.secrets
  sensitive   = true
}
