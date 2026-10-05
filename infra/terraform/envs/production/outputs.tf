output "web_url" {
  description = "Cloud Run URL of the production web service."
  value       = module.environment.web_url
}

output "service_ingress" {
  description = "Effective ingress per service. api and worker must stay internal."
  value       = module.environment.service_ingress
}

output "artifact_repository_url" {
  description = "Image path CI tags against."
  value       = module.environment.artifact_repository_url
}

output "sql_connection_name" {
  description = "Cloud SQL connection name."
  value       = module.environment.sql_connection_name
}

output "sql_public_ip_address" {
  description = "Expected empty: the instance has no public endpoint."
  value       = module.environment.sql_public_ip_address
}

output "secret_ids" {
  description = "Secret containers awaiting values from an authorized operator."
  value       = module.environment.secret_ids
}

output "bucket_names" {
  description = "Private buckets for this environment."
  value       = module.environment.bucket_names
}

output "topic_names" {
  description = "Pub/Sub topics, including the Gmail notification target."
  value       = module.environment.topic_names
}

output "workload_identity_provider" {
  description = "Value the GitHub Actions auth step passes as workload_identity_provider."
  value       = module.environment.workload_identity_provider
}

output "ci_service_account_emails" {
  description = "CI identities for this environment."
  value       = module.environment.ci_service_account_emails
}

output "runtime_service_account_emails" {
  description = "Runtime identities for this environment."
  value       = module.environment.runtime_service_account_emails
}

output "migration_job_name" {
  description = "Cloud Run job that applies the Alembic chain."
  value       = module.environment.migration_job_name
}
