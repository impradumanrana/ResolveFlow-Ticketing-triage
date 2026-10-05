output "enabled_services" {
  description = "Service names enabled by this module."
  value       = sort([for service in google_project_service.required : service.service])
}
