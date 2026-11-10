# Managed SQL: Cloud SQL for PostgreSQL, reachable only on a private IP.
# One instance, one database per bounded context, one user per service.

locals {
  # service -> database it lives in
  service_databases = {
    ledger   = "core_banking"
    account  = "core_banking"
    intake   = "core_banking"
    deposits = "core_banking"
    identity = "identity"
    fraud    = "cases"
  }
}

resource "google_sql_database_instance" "main" {
  name                = "ub-postgres"
  database_version    = "POSTGRES_16"
  region              = var.region
  deletion_protection = false

  settings {
    tier              = var.sql_tier
    edition           = "ENTERPRISE"
    availability_type = "ZONAL"
    activation_policy = var.sql_running ? "ALWAYS" : "NEVER"
    disk_type         = "PD_SSD"
    disk_size         = 10
    disk_autoresize   = false
    user_labels       = var.labels

    ip_configuration {
      ipv4_enabled    = false
      private_network = google_compute_network.vpc.id
      ssl_mode        = "ENCRYPTED_ONLY"
    }

    backup_configuration {
      enabled = false # student project: saves cost; a real bank would enable PITR
    }

    database_flags {
      name  = "max_connections"
      value = "100"
    }
  }

  depends_on = [google_service_networking_connection.private_services]
}

resource "google_sql_database" "db" {
  for_each = toset(distinct(values(local.service_databases)))
  name     = each.value
  instance = google_sql_database_instance.main.name
}

resource "random_password" "db" {
  for_each = local.service_databases
  length   = 28
  special  = false
}

resource "google_sql_user" "service" {
  for_each = local.service_databases
  name     = "${each.key}_svc"
  instance = google_sql_database_instance.main.name
  password = random_password.db[each.key].result
}

locals {
  database_urls = {
    for service, database in local.service_databases :
    service => "postgresql://${service}_svc:${random_password.db[service].result}@${google_sql_database_instance.main.private_ip_address}:5432/${database}?sslmode=require"
  }
}
