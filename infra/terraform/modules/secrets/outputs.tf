output "secret_ids" {
  description = "Map of secret key to created secret id."
  value       = { for key, secret in google_secret_manager_secret.managed : key => secret.secret_id }
}

output "secret_names" {
  description = "Map of secret key to fully qualified secret name."
  value       = { for key, secret in google_secret_manager_secret.managed : key => secret.name }
}
