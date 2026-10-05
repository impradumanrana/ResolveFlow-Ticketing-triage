output "key_ids" {
  description = "Map of key alias to fully qualified crypto key id."
  value       = { for alias, key in google_kms_crypto_key.keys : alias => key.id }
}

output "key_ring_id" {
  description = "Fully qualified key ring id."
  value       = google_kms_key_ring.main.id
}
