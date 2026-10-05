# One Docker repository per environment.
#
# Staging and production never share a repository: an image promoted to
# production must be published deliberately, by digest, from CI.

resource "google_artifact_registry_repository" "containers" {
  project       = var.project_id
  location      = var.region
  repository_id = "${var.name_prefix}-containers"
  description   = "ResolveFlow ${var.environment} service images."
  format        = "DOCKER"
  labels        = var.labels

  kms_key_name = var.kms_key_id

  docker_config {
    immutable_tags = var.immutable_tags
  }

  cleanup_policy_dry_run = var.cleanup_dry_run

  cleanup_policies {
    id     = "keep-recent-releases"
    action = "KEEP"
    most_recent_versions {
      keep_count = var.keep_image_count
    }
  }

  cleanup_policies {
    id     = "delete-stale-untagged"
    action = "DELETE"
    condition {
      tag_state  = "UNTAGGED"
      older_than = var.untagged_retention
    }
  }
}

# CI pushes. Runtime identities never need to read the registry directly -
# Cloud Run pulls with the Google-managed service agent.
resource "google_artifact_registry_repository_iam_member" "writers" {
  for_each = toset(var.writer_members)

  project    = var.project_id
  location   = google_artifact_registry_repository.containers.location
  repository = google_artifact_registry_repository.containers.name
  role       = "roles/artifactregistry.writer"
  member     = each.value
}

resource "google_artifact_registry_repository_iam_member" "readers" {
  for_each = toset(var.reader_members)

  project    = var.project_id
  location   = google_artifact_registry_repository.containers.location
  repository = google_artifact_registry_repository.containers.name
  role       = "roles/artifactregistry.reader"
  member     = each.value
}
