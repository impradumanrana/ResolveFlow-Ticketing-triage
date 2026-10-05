output "repository_id" {
  description = "Fully qualified Artifact Registry repository id."
  value       = google_artifact_registry_repository.containers.id
}

output "repository_url" {
  description = "Host path used when tagging images for this environment."
  value       = "${var.region}-docker.pkg.dev/${var.project_id}/${google_artifact_registry_repository.containers.repository_id}"
}
