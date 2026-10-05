output "service_urls" {
  description = "Map of service key to its Cloud Run URL."
  value       = { for key, service in google_cloud_run_v2_service.services : key => service.uri }
}

output "service_names" {
  description = "Map of service key to Cloud Run service name."
  value       = { for key, service in google_cloud_run_v2_service.services : key => service.name }
}

output "service_ingress" {
  description = "Map of service key to its effective ingress setting."
  value       = { for key, service in google_cloud_run_v2_service.services : key => service.ingress }
}

output "migration_job_name" {
  description = "Name of the migration job, or null when disabled."
  value       = var.migration_job == null ? null : google_cloud_run_v2_job.migrate[0].name
}
