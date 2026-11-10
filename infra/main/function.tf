# Serverless: the Notification Service is a Cloud Function (2nd gen) triggered
# asynchronously by the `notifications` Pub/Sub topic.

resource "google_pubsub_topic" "notifications" {
  name   = "notifications"
  labels = var.labels
}

resource "google_service_account" "fn_notification" {
  account_id   = "ub-fn-notification"
  display_name = "Notification function"
}

resource "google_project_iam_member" "fn_notification" {
  for_each = toset([
    "roles/datastore.user", # write the inbox to Firestore
    "roles/run.invoker",    # let the Pub/Sub trigger call the function
    "roles/logging.logWriter",
  ])
  project = var.project_id
  role    = each.value
  member  = "serviceAccount:${google_service_account.fn_notification.email}"
}

# Dedicated build identity (newer projects no longer give the default one enough rights).
resource "google_service_account" "fn_builder" {
  account_id   = "ub-fn-builder"
  display_name = "Builds Cloud Functions"
}

resource "google_project_iam_member" "fn_builder" {
  for_each = toset([
    "roles/cloudbuild.builds.builder",
    "roles/logging.logWriter",
    "roles/artifactregistry.writer",
    "roles/storage.objectViewer",
  ])
  project = var.project_id
  role    = each.value
  member  = "serviceAccount:${google_service_account.fn_builder.email}"
}

locals {
  # Whether a key was supplied is not itself a secret.
  email_enabled = nonsensitive(var.brevo_api_key != "")
}

data "archive_file" "notification" {
  type        = "zip"
  source_dir  = "${path.module}/../../functions/notification"
  output_path = "${path.module}/.build/notification.zip"
  excludes    = ["tests", "__pycache__", ".pytest_cache", "pytest.ini"]
}

resource "google_storage_bucket_object" "notification" {
  # The hash in the name makes Terraform redeploy the function when its code changes.
  name   = "notification-${data.archive_file.notification.output_md5}.zip"
  bucket = google_storage_bucket.functions.name
  source = data.archive_file.notification.output_path
}

resource "google_secret_manager_secret" "brevo" {
  count     = local.email_enabled ? 1 : 0
  secret_id = "ub-brevo-api-key"
  replication {
    auto {}
  }
}

resource "google_secret_manager_secret_version" "brevo" {
  count       = local.email_enabled ? 1 : 0
  secret      = google_secret_manager_secret.brevo[0].id
  secret_data = var.brevo_api_key
}

resource "google_secret_manager_secret_iam_member" "brevo" {
  count     = local.email_enabled ? 1 : 0
  secret_id = google_secret_manager_secret.brevo[0].id
  role      = "roles/secretmanager.secretAccessor"
  member    = "serviceAccount:${google_service_account.fn_notification.email}"
}

resource "google_cloudfunctions2_function" "notification" {
  name     = "ub-notification"
  location = var.region
  labels   = var.labels

  build_config {
    runtime         = "python312"
    entry_point     = "notify"
    service_account = google_service_account.fn_builder.id

    source {
      storage_source {
        bucket = google_storage_bucket.functions.name
        object = google_storage_bucket_object.notification.name
      }
    }
  }

  service_config {
    service_account_email = google_service_account.fn_notification.email
    available_memory      = "256M"
    timeout_seconds       = 60
    min_instance_count    = 0 # scales to zero: costs nothing when idle
    max_instance_count    = 3

    environment_variables = {
      FIRESTORE_DATABASE = google_firestore_database.main.name
      EMAIL_FROM         = var.email_from
    }

    dynamic "secret_environment_variables" {
      for_each = local.email_enabled ? [1] : []
      content {
        key        = "BREVO_API_KEY"
        project_id = var.project_id
        secret     = google_secret_manager_secret.brevo[0].secret_id
        version    = "latest"
      }
    }
  }

  event_trigger {
    trigger_region        = var.region
    event_type            = "google.cloud.pubsub.topic.v1.messagePublished"
    pubsub_topic          = google_pubsub_topic.notifications.id
    retry_policy          = "RETRY_POLICY_RETRY"
    service_account_email = google_service_account.fn_notification.email
  }

  depends_on = [
    google_project_iam_member.fn_builder,
    google_project_iam_member.fn_notification,
    google_secret_manager_secret_version.brevo,
  ]
}
