# One VPC for everything: GKE, Dataproc and (through private services access) Cloud SQL.

resource "google_compute_network" "vpc" {
  name                    = "ub-vpc"
  auto_create_subnetworks = false
}

resource "google_compute_subnetwork" "main" {
  name                     = "ub-subnet"
  region                   = var.region
  network                  = google_compute_network.vpc.id
  ip_cidr_range            = "10.10.0.0/20"
  private_ip_google_access = true

  secondary_ip_range {
    range_name    = "pods"
    ip_cidr_range = "10.20.0.0/16"
  }
  secondary_ip_range {
    range_name    = "services"
    ip_cidr_range = "10.30.0.0/20"
  }
}

# Nodes, pods and the Dataproc VM may talk to each other (Flink reaches Kafka's NodePort this way).
resource "google_compute_firewall" "internal" {
  name          = "ub-allow-internal"
  network       = google_compute_network.vpc.name
  source_ranges = ["10.10.0.0/20", "10.20.0.0/16"]

  allow {
    protocol = "tcp"
  }
  allow {
    protocol = "udp"
  }
  allow {
    protocol = "icmp"
  }
}

# SSH only through Identity-Aware Proxy (gcloud compute ssh --tunnel-through-iap), never from the internet.
resource "google_compute_firewall" "iap_ssh" {
  name          = "ub-allow-iap-ssh"
  network       = google_compute_network.vpc.name
  source_ranges = ["35.235.240.0/20"]

  allow {
    protocol = "tcp"
    ports    = ["22"]
  }
}

# Private services access: gives Cloud SQL a private IP inside this VPC.
resource "google_compute_global_address" "private_services" {
  name          = "ub-private-services"
  purpose       = "VPC_PEERING"
  address_type  = "INTERNAL"
  prefix_length = 20
  network       = google_compute_network.vpc.id
}

resource "google_service_networking_connection" "private_services" {
  network                 = google_compute_network.vpc.id
  service                 = "servicenetworking.googleapis.com"
  reserved_peering_ranges = [google_compute_global_address.private_services.name]
}
