# Storage products besides Cloud SQL:
#   Firestore       - semi-structured, high-write data (sessions, login events, alerts, inbox, analytics)
#   Cloud Storage   - objects (EOD reports, Flink job files and checkpoints, function source)
#   Artifact Registry - container images

resource "google_firestore_database" "main" {
  name        = "(default)"
  location_id = var.firestore_location
  type        = "FIRESTORE_NATIVE"
}

resource "google_storage_bucket" "reports" {
  name                        = "${var.project_id}-reports"
  location                    = var.region
  uniform_bucket_level_access = true
  public_access_prevention    = "enforced"
  force_destroy               = true
  labels                      = var.labels

  lifecycle_rule {
    condition {
      age = 90
    }
    action {
      type = "Delete"
    }
  }
}

resource "google_storage_bucket" "flink" {
  name                        = "${var.project_id}-flink"
  location                    = var.region
  uniform_bucket_level_access = true
  public_access_prevention    = "enforced"
  force_destroy               = true
  labels                      = var.labels
}

resource "google_storage_bucket" "functions" {
  name                        = "${var.project_id}-functions"
  location                    = var.region
  uniform_bucket_level_access = true
  public_access_prevention    = "enforced"
  force_destroy               = true
  labels                      = var.labels
}

resource "google_artifact_registry_repository" "images" {
  repository_id = "unbeatable"
  location      = var.region
  format        = "DOCKER"
  description   = "Service images built by GitHub Actions"
  labels        = var.labels

  cleanup_policies {
    id     = "keep-recent"
    action = "KEEP"
    most_recent_versions {
      keep_count = 5
    }
  }
  cleanup_policies {
    id     = "delete-old"
    action = "DELETE"
    condition {
      older_than = "604800s" # 7 days
    }
  }
}
