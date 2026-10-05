output "instance_name" {
  description = "Cloud SQL instance name."
  value       = google_sql_database_instance.main.name
}

output "connection_name" {
  description = "Instance connection name used by the Cloud SQL connector."
  value       = google_sql_database_instance.main.connection_name
}

output "private_ip_address" {
  description = "Private IP of the instance. No public address exists."
  value       = google_sql_database_instance.main.private_ip_address
}

output "database_name" {
  description = "Application database name."
  value       = google_sql_database.application.name
}

output "public_ip_address" {
  description = "Always empty. Asserted by the offline infrastructure checks."
  value       = google_sql_database_instance.main.public_ip_address
}
