# Secrets live in Secret Manager. infra/platform copies them into the cluster
# as a Kubernetes Secret; nothing secret is ever written to Git.

resource "random_password" "jwt_secret" {
  length  = 48
  special = false
}

locals {
  secrets = merge(
    { for service, url in local.database_urls : "DATABASE_URL_${upper(service)}" => url },
    {
      JWT_SECRET      = random_password.jwt_secret.result
      STAFF_BOOTSTRAP = var.staff_bootstrap
    },
    # Only when set: Secret Manager refuses an empty secret.
    { for k, v in { DUCKDNS_TOKEN = var.duckdns_token } : k => v if v != "" },
  )
}

resource "google_secret_manager_secret" "app" {
  for_each  = toset(nonsensitive(keys(local.secrets)))
  secret_id = "ub-${lower(replace(each.value, "_", "-"))}"
  labels    = var.labels

  replication {
    auto {}
  }
}

resource "google_secret_manager_secret_version" "app" {
  for_each    = google_secret_manager_secret.app
  secret      = each.value.id
  secret_data = local.secrets[each.key]
}
