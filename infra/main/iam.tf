# Workload Identity: pods get Google credentials through their Kubernetes
# service account. No key files anywhere.
#
# Only the services that call Google APIs get an identity (least privilege):
#   identity  - Firestore (sessions, login events), Pub/Sub (notifications),
#               and the KYC bucket: it is the ONLY identity that can read customers' ID documents
#   fraud     - Firestore (alerts, inbox, analytics), Pub/Sub
#   deposits  - Cloud Storage (EOD reports, statements), Firestore, Pub/Sub
# Every other pod runs with no Google permissions at all.

locals {
  app_namespace = "bank"
  # Kubernetes service account -> the Google service account it acts as.
  workload_identity = {
    identity = google_service_account.identity.email
    fraud    = google_service_account.apps.email
    deposits = google_service_account.apps.email
  }
}

resource "google_service_account" "apps" {
  account_id   = "ub-apps"
  display_name = "Bank services that use Firestore, Pub/Sub and Cloud Storage"
}

resource "google_project_iam_member" "apps" {
  for_each = toset(["roles/datastore.user", "roles/pubsub.publisher"])
  project  = var.project_id
  role     = each.value
  member   = "serviceAccount:${google_service_account.apps.email}"
}

resource "google_storage_bucket_iam_member" "apps_reports" {
  bucket = google_storage_bucket.reports.name
  role   = "roles/storage.objectAdmin"
  member = "serviceAccount:${google_service_account.apps.email}"
}

resource "google_service_account" "identity" {
  account_id   = "ub-identity"
  display_name = "Identity & Auth: sessions, notifications and KYC documents"
}

resource "google_project_iam_member" "identity" {
  for_each = toset(["roles/datastore.user", "roles/pubsub.publisher"])
  project  = var.project_id
  role     = each.value
  member   = "serviceAccount:${google_service_account.identity.email}"
}

resource "google_storage_bucket_iam_member" "identity_kyc" {
  bucket = google_storage_bucket.kyc.name
  role   = "roles/storage.objectAdmin"
  member = "serviceAccount:${google_service_account.identity.email}"
}

resource "google_service_account_iam_member" "workload_identity" {
  for_each           = local.workload_identity
  service_account_id = "projects/${var.project_id}/serviceAccounts/${each.value}"
  role               = "roles/iam.workloadIdentityUser"
  member             = "serviceAccount:${var.project_id}.svc.id.goog[${local.app_namespace}/${each.key}]"

  depends_on = [google_container_cluster.main] # the identity pool exists once the cluster does
}
