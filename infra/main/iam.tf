# Workload Identity: pods get Google credentials through their Kubernetes
# service account. No key files anywhere.
#
# Only the services that call Google APIs get an identity (least privilege):
#   identity  - Firestore (sessions, login events), Pub/Sub (notifications)
#   fraud     - Firestore (alerts, inbox, analytics), Pub/Sub
#   deposits  - Cloud Storage (EOD reports), Pub/Sub
# Every other pod runs with no Google permissions at all.

locals {
  app_namespace           = "bank"
  workload_identity_users = ["identity", "fraud", "deposits"]
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

resource "google_service_account_iam_member" "apps_workload_identity" {
  for_each           = toset(local.workload_identity_users)
  service_account_id = google_service_account.apps.name
  role               = "roles/iam.workloadIdentityUser"
  member             = "serviceAccount:${var.project_id}.svc.id.goog[${local.app_namespace}/${each.value}]"

  depends_on = [google_container_cluster.main] # the identity pool exists once the cluster does
}
