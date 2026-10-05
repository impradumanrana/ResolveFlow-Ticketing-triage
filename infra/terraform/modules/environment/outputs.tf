output "project_id" {
  description = "Project hosting this environment."
  value       = var.project_id
}

output "region" {
  description = "Region hosting this environment."
  value       = var.region
}

output "web_url" {
  description = "Cloud Run URL of the web service."
  value       = module.runtime.service_urls["web"]
}

output "service_ingress" {
  description = "Effective ingress per service. Only web may be non-internal."
  value       = module.runtime.service_ingress
}

output "artifact_repository_url" {
  description = "Image path CI tags against."
  value       = module.artifact_registry.repository_url
}

output "sql_connection_name" {
  description = "Cloud SQL connection name for the migration job and services."
  value       = module.database.connection_name
}

output "sql_public_ip_address" {
  description = "Expected to be empty: the instance has no public endpoint."
  value       = module.database.public_ip_address
}

output "secret_ids" {
  description = "Secret containers created for this environment. Values are added out of band."
  value       = module.secrets.secret_ids
}

output "bucket_names" {
  description = "Private buckets created for this environment."
  value       = module.storage.bucket_names
}

output "topic_names" {
  description = "Pub/Sub topics, including the Gmail notification target."
  value       = module.messaging.topic_names
}

output "dead_letter_topic_ids" {
  description = "Dead-letter topics that hold unprocessable mailbox events."
  value       = module.messaging.dead_letter_topic_ids
}

output "scheduler_jobs" {
  description = "Scheduled reconciliation and maintenance jobs."
  value       = module.messaging.scheduler_job_names
}

output "workload_identity_provider" {
  description = "Value the GitHub Actions auth step passes as workload_identity_provider."
  value       = module.cicd.workload_identity_provider
}

output "ci_service_account_emails" {
  description = "CI identities for this environment."
  value       = module.cicd.ci_service_account_emails
}

output "runtime_service_account_emails" {
  description = "Runtime identities for this environment."
  value       = module.service_accounts.emails
}

output "migration_job_name" {
  description = "Cloud Run job that applies migrations."
  value       = module.runtime.migration_job_name
}

output "kms_key_ring" {
  description = "Key ring holding the environment CMEKs."
  value       = module.kms.key_ring_id
}
