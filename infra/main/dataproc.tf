# Stream processing: the Audit Analytics Flink job on a managed Dataproc cluster.
#
# The cluster is the most expensive piece, so it only exists while
# flink_enabled = true (`make flink-up` / `make flink-down`), and deletes itself
# when idle. Creating the cluster also submits the job: the initialization
# action runs services/audit-analytics/submit.sh. Flink checkpoints are in
# Cloud Storage, so the job resumes where it stopped.

resource "google_service_account" "dataproc" {
  account_id   = "ub-dataproc"
  display_name = "Dataproc (Flink) cluster"
}

resource "google_project_iam_member" "dataproc" {
  for_each = toset([
    "roles/dataproc.worker",
    "roles/compute.viewer", # submit.sh looks up the GKE node addresses to reach Kafka
  ])
  project = var.project_id
  role    = each.value
  member  = "serviceAccount:${google_service_account.dataproc.email}"
}

resource "google_storage_bucket_iam_member" "dataproc_flink" {
  bucket = google_storage_bucket.flink.name
  role   = "roles/storage.objectAdmin"
  member = "serviceAccount:${google_service_account.dataproc.email}"
}

locals {
  flink_job_dir   = "${path.module}/../../services/audit-analytics"
  flink_job_files = concat(tolist(fileset(local.flink_job_dir, "sql/*.sql")), ["submit.sh"])
}

resource "google_storage_bucket_object" "flink_job" {
  for_each = toset(local.flink_job_files)
  name     = "audit-analytics/${each.value}"
  bucket   = google_storage_bucket.flink.name
  source   = "${local.flink_job_dir}/${each.value}"
}

resource "google_dataproc_cluster" "flink" {
  count  = var.flink_enabled ? 1 : 0
  name   = "ub-flink"
  region = var.region
  labels = var.labels

  cluster_config {
    staging_bucket = google_storage_bucket.flink.name

    gce_cluster_config {
      zone             = var.zone
      subnetwork       = google_compute_subnetwork.main.id
      service_account  = google_service_account.dataproc.email
      tags             = ["ub-dataproc"]
      internal_ip_only = false # needs the internet once, to download the Kafka connector

      metadata = {
        job-bucket     = google_storage_bucket.flink.name
        kafka-nodeport = tostring(var.kafka_bootstrap_nodeport)
        gke-node-tag   = "ub-gke-node"
      }
    }

    # Single node: enough for the demo and half the price of master + workers.
    master_config {
      num_instances = 1
      machine_type  = "e2-standard-4"
      disk_config {
        boot_disk_type    = "pd-standard"
        boot_disk_size_gb = 50
      }
    }

    software_config {
      image_version       = "2.2-debian12"
      optional_components = ["FLINK"]
      override_properties = {
        "dataproc:dataproc.allow.zero.workers" = "true"
      }
    }

    initialization_action {
      script      = "gs://${google_storage_bucket.flink.name}/audit-analytics/submit.sh"
      timeout_sec = 600
    }

    lifecycle_config {
      idle_delete_ttl = "${var.flink_idle_delete_minutes * 60}s"
    }
  }

  depends_on = [
    google_storage_bucket_object.flink_job,
    google_project_iam_member.dataproc,
    google_storage_bucket_iam_member.dataproc_flink,
  ]
}
