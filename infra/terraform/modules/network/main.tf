# Private network for the environment.
#
# Cloud SQL is reachable only over private service access. Cloud Run services
# send all outbound traffic through this VPC using direct VPC egress, so egress
# leaves through Cloud NAT with a known address and can be audited.

resource "google_compute_network" "main" {
  project                 = var.project_id
  name                    = "${var.name_prefix}-vpc"
  auto_create_subnetworks = false
  routing_mode            = "REGIONAL"
  description             = "ResolveFlow ${var.environment} private network."
}

resource "google_compute_subnetwork" "main" {
  project                  = var.project_id
  name                     = "${var.name_prefix}-subnet"
  region                   = var.region
  network                  = google_compute_network.main.id
  ip_cidr_range            = var.subnet_cidr
  private_ip_google_access = true

  log_config {
    aggregation_interval = "INTERVAL_5_SEC"
    flow_sampling        = var.flow_log_sampling
    metadata             = "INCLUDE_ALL_METADATA"
  }
}

# Dedicated range reserved for Google-managed services (Cloud SQL).
resource "google_compute_global_address" "private_service_access" {
  project       = var.project_id
  name          = "${var.name_prefix}-psa-range"
  purpose       = "VPC_PEERING"
  address_type  = "INTERNAL"
  address       = var.private_service_access_cidr
  prefix_length = var.private_service_access_prefix_length
  network       = google_compute_network.main.id
}

resource "google_service_networking_connection" "private_service_access" {
  network                 = google_compute_network.main.id
  service                 = "servicenetworking.googleapis.com"
  reserved_peering_ranges = [google_compute_global_address.private_service_access.name]

  # Keeps the peered range from being dropped while Cloud SQL still uses it.
  deletion_policy = "ABANDON"
}

resource "google_compute_router" "main" {
  project = var.project_id
  name    = "${var.name_prefix}-router"
  region  = var.region
  network = google_compute_network.main.id
}

# Egress to Google APIs, the client's model provider, and Gmail leaves here.
resource "google_compute_router_nat" "main" {
  project                            = var.project_id
  name                               = "${var.name_prefix}-nat"
  router                             = google_compute_router.main.name
  region                             = var.region
  nat_ip_allocate_option             = "AUTO_ONLY"
  source_subnetwork_ip_ranges_to_nat = "LIST_OF_SUBNETWORKS"

  subnetwork {
    name                    = google_compute_subnetwork.main.id
    source_ip_ranges_to_nat = ["ALL_IP_RANGES"]
  }

  log_config {
    enable = true
    filter = "ERRORS_ONLY"
  }
}

# The VPC carries no VM workloads in V1. Deny-by-default is explicit rather
# than implied, and internal service-to-service traffic is allowed narrowly.
resource "google_compute_firewall" "deny_all_ingress" {
  project     = var.project_id
  name        = "${var.name_prefix}-deny-all-ingress"
  network     = google_compute_network.main.name
  direction   = "INGRESS"
  priority    = 65000
  description = "Default deny. Cloud Run ingress is controlled by the service, not the VPC."

  deny {
    protocol = "all"
  }

  source_ranges = ["0.0.0.0/0"]

  log_config {
    metadata = "INCLUDE_ALL_METADATA"
  }
}

resource "google_compute_firewall" "allow_internal_subnet" {
  project     = var.project_id
  name        = "${var.name_prefix}-allow-internal"
  network     = google_compute_network.main.name
  direction   = "INGRESS"
  priority    = 1000
  description = "Allow traffic originating inside the environment subnet only."

  allow {
    protocol = "tcp"
    ports    = ["5432", "8080"]
  }

  source_ranges = [var.subnet_cidr]

  log_config {
    metadata = "INCLUDE_ALL_METADATA"
  }
}
