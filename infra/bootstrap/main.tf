############################################
# Project, linked to the active billing account
############################################

resource "google_project" "bank" {
  project_id      = var.project_id
  name            = var.project_name
  org_id          = var.org_id
  billing_account = var.billing_accounts[var.active_billing]
  labels          = var.labels

  # Never let a stray destroy delete the whole project.
  deletion_policy = "PREVENT"
}

############################################
# APIs
############################################

locals {
  apis = [
    "cloudresourcemanager.googleapis.com",
    "serviceusage.googleapis.com",
    "iam.googleapis.com",
    "iamcredentials.googleapis.com",
    "sts.googleapis.com",
    "cloudbilling.googleapis.com",
    "billingbudgets.googleapis.com",
    "compute.googleapis.com",
    "container.googleapis.com",
    "servicenetworking.googleapis.com",
    "sqladmin.googleapis.com",
    "firestore.googleapis.com",
    "storage.googleapis.com",
    "pubsub.googleapis.com",
    "dataproc.googleapis.com",
    "cloudfunctions.googleapis.com",
    "run.googleapis.com",
    "cloudbuild.googleapis.com",
    "eventarc.googleapis.com",
    "cloudscheduler.googleapis.com",
    "artifactregistry.googleapis.com",
    "secretmanager.googleapis.com",
    "logging.googleapis.com",
    "monitoring.googleapis.com",
  ]
}

resource "google_project_service" "apis" {
  for_each = toset(local.apis)

  project            = google_project.bank.project_id
  service            = each.value
  disable_on_destroy = false
}

############################################
# Shared Terraform state bucket
############################################

resource "google_storage_bucket" "tfstate" {
  project                     = google_project.bank.project_id
  name                        = "${var.project_id}-tfstate"
  location                    = var.region
  uniform_bucket_level_access = true
  public_access_prevention    = "enforced"
  force_destroy               = false
  labels                      = var.labels

  versioning {
    enabled = true
  }

  # Keep only the 10 most recent non-current state versions.
  lifecycle_rule {
    condition {
      num_newer_versions = 10
      with_state         = "ARCHIVED"
    }
    action {
      type = "Delete"
    }
  }

  depends_on = [google_project_service.apis]
}

############################################
# Team access
#
# The Owner role cannot be granted to accounts outside an organization
# through the API (it needs a console invitation), so teammates get the
# set of admin roles Terraform actually needs instead.
############################################

locals {
  member_roles = [
    "roles/editor",
    "roles/resourcemanager.projectIamAdmin",
    "roles/iam.serviceAccountAdmin",
    "roles/iam.workloadIdentityPoolAdmin",
    "roles/secretmanager.admin",
    "roles/storage.admin",
    "roles/container.admin",
    "roles/serviceusage.serviceUsageAdmin",
  ]

  member_role_pairs = {
    for pair in setproduct(keys(var.team_members), local.member_roles) :
    "${pair[0]}|${pair[1]}" => { email = var.team_members[pair[0]], role = pair[1] }
  }
}

resource "google_project_iam_member" "team" {
  for_each = local.member_role_pairs

  project = google_project.bank.project_id
  role    = each.value.role
  member  = "user:${each.value.email}"
}

############################################
# Budget alerts, one per collected billing account
############################################

resource "google_monitoring_notification_channel" "budget_email" {
  for_each = toset(var.alert_emails)

  project      = google_project.bank.project_id
  display_name = "Budget alert ${each.value}"
  type         = "email"
  labels = {
    email_address = each.value
  }

  depends_on = [google_project_service.apis]
}

resource "google_billing_budget" "per_account" {
  provider = google.billing
  for_each = var.billing_accounts

  billing_account = each.value
  display_name    = "unbeatable-bank (${each.key})"

  budget_filter {
    projects = ["projects/${google_project.bank.number}"]
  }

  amount {
    specified_amount {
      currency_code = lookup(var.budget_currencies, each.key, var.budget_currency)
      units         = tostring(lookup(var.budget_amounts, each.key, var.budget_amount))
    }
  }

  dynamic "threshold_rules" {
    for_each = var.budget_alert_thresholds
    content {
      threshold_percent = threshold_rules.value
      spend_basis       = "CURRENT_SPEND"
    }
  }

  all_updates_rule {
    monitoring_notification_channels = [
      for ch in google_monitoring_notification_channel.budget_email : ch.id
    ]
    disable_default_iam_recipients = false
  }

  depends_on = [google_project_service.apis]
}

############################################
# GitHub Actions -> GCP without key files (Workload Identity Federation)
############################################

resource "google_iam_workload_identity_pool" "github" {
  project                   = google_project.bank.project_id
  workload_identity_pool_id = "github"
  display_name              = "GitHub Actions"

  depends_on = [google_project_service.apis]
}

resource "google_iam_workload_identity_pool_provider" "github" {
  project                            = google_project.bank.project_id
  workload_identity_pool_id          = google_iam_workload_identity_pool.github.workload_identity_pool_id
  workload_identity_pool_provider_id = "github-oidc"
  display_name                       = "GitHub OIDC"

  attribute_mapping = {
    "google.subject"       = "assertion.sub"
    "attribute.repository" = "assertion.repository"
    "attribute.ref"        = "assertion.ref"
  }

  # Only our repository can use this provider.
  attribute_condition = "assertion.repository == \"${var.github_repository}\""

  oidc {
    issuer_uri = "https://token.actions.githubusercontent.com"
  }
}

resource "google_service_account" "ci" {
  project      = google_project.bank.project_id
  account_id   = "github-ci"
  display_name = "GitHub Actions CI"

  depends_on = [google_project_service.apis]
}

resource "google_service_account_iam_member" "ci_wif" {
  service_account_id = google_service_account.ci.name
  role               = "roles/iam.workloadIdentityUser"
  member             = "principalSet://iam.googleapis.com/${google_iam_workload_identity_pool.github.name}/attribute.repository/${var.github_repository}"
}

# CI only pushes images. Artifact Registry itself is created in infra/main.
resource "google_project_iam_member" "ci_artifact_writer" {
  project = google_project.bank.project_id
  role    = "roles/artifactregistry.writer"
  member  = "serviceAccount:${google_service_account.ci.email}"
}
