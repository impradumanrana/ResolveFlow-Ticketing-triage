output "bucket_names" {
  description = "Map of bucket key to created bucket name."
  value       = { for key, bucket in google_storage_bucket.managed : key => bucket.name }
}

output "bucket_urls" {
  description = "Map of bucket key to gs:// URL."
  value       = { for key, bucket in google_storage_bucket.managed : key => bucket.url }
}
