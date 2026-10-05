output "network_id" {
  description = "Self link of the environment VPC."
  value       = google_compute_network.main.id
}

output "network_name" {
  description = "Name of the environment VPC."
  value       = google_compute_network.main.name
}

output "subnet_id" {
  description = "Self link of the primary subnet used for direct VPC egress."
  value       = google_compute_subnetwork.main.id
}

output "private_service_access_connection" {
  description = "Private service access peering, used to order Cloud SQL creation."
  value       = google_service_networking_connection.private_service_access.id
}
