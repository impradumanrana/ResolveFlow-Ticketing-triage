output "workload_identity_provider" {
  description = "Value GitHub Actions passes as workload_identity_provider."
  value       = google_iam_workload_identity_pool_provider.github.name
}

output "ci_service_account_emails" {
  description = "Map of CI account key to service account email."
  value       = { for key, account in google_service_account.ci : key => account.email }
}
