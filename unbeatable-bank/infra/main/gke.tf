# Managed Kubernetes for the stateless microservices (and Kafka, DynaStore, Prometheus, ArgoCD).

resource "google_service_account" "gke_nodes" {
  account_id   = "ub-gke-nodes"
  display_name = "GKE nodes (minimal: logs, metrics, pull images)"
}

resource "google_project_iam_member" "gke_nodes" {
  for_each = toset([
    "roles/logging.logWriter",
    "roles/monitoring.metricWriter",
    "roles/monitoring.viewer",
    "roles/stackdriver.resourceMetadata.writer",
    "roles/artifactregistry.reader",
  ])
  project = var.project_id
  role    = each.value
  member  = "serviceAccount:${google_service_account.gke_nodes.email}"
}

resource "google_container_cluster" "main" {
  name     = "ub-cluster"
  location = var.zone # zonal: covered by the GKE free tier

  network    = google_compute_network.vpc.id
  subnetwork = google_compute_subnetwork.main.id

  # Node pool is managed separately so it can be scaled to zero.
  remove_default_node_pool = true
  initial_node_count       = 1
  deletion_protection      = false

  ip_allocation_policy {
    cluster_secondary_range_name  = "pods"
    services_secondary_range_name = "services"
  }

  # Dataplane V2 enforces Kubernetes NetworkPolicies (deploy/bank/common.yaml).
  datapath_provider = "ADVANCED_DATAPATH"

  # Pods authenticate to Google APIs as service accounts, without key files.
  workload_identity_config {
    workload_pool = "${var.project_id}.svc.id.goog"
  }

  release_channel {
    channel = "REGULAR"
  }

  # Centralised logging: every container's stdout goes to Cloud Logging,
  # next to the Dataproc/Flink logs.
  logging_config {
    enable_components = ["SYSTEM_COMPONENTS", "WORKLOADS"]
  }

  # Metrics are collected by our own Prometheus (deployed by ArgoCD).
  monitoring_config {
    enable_components = ["SYSTEM_COMPONENTS"]
    managed_prometheus {
      enabled = false
    }
  }

  resource_labels = var.labels
}

resource "google_container_node_pool" "main" {
  name       = "ub-pool"
  cluster    = google_container_cluster.main.id
  node_count = var.node_count

  node_config {
    machine_type    = var.node_machine_type
    spot            = var.spot_nodes
    disk_size_gb    = 50
    disk_type       = "pd-standard"
    service_account = google_service_account.gke_nodes.email
    oauth_scopes    = ["https://www.googleapis.com/auth/cloud-platform"]
    tags            = ["ub-gke-node"]
    labels          = var.labels

    workload_metadata_config {
      mode = "GKE_METADATA"
    }
  }

  management {
    auto_repair  = true
    auto_upgrade = true
  }

  depends_on = [google_project_iam_member.gke_nodes]
}
