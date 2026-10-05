output "state_bucket" {
  description = "Value to place in each environment's backend.hcl."
  value       = google_storage_bucket.terraform_state.name
}
