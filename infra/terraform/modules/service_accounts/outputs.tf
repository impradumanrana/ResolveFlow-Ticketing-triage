output "emails" {
  description = "Map of account key to service account email."
  value       = { for key, account in google_service_account.runtime : key => account.email }
}

output "members" {
  description = "Map of account key to IAM member string."
  value       = { for key, account in google_service_account.runtime : key => "serviceAccount:${account.email}" }
}

output "ids" {
  description = "Map of account key to fully qualified service account id."
  value       = { for key, account in google_service_account.runtime : key => account.id }
}
