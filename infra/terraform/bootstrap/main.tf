# One-time bootstrap: the Terraform state bucket.
#
# This is the only configuration that cannot use remote state, because it
# creates the bucket that holds remote state. It is applied once per client,
# by a named client administrator, and then its own local state file is stored
# with the client's records or migrated into the bucket it just created.
#
# State contains resource metadata, connection names, and IAM structure. It is
# not a secret store - this stack writes no secret values into state - but it
# is still sensitive and is versioned, private, and access-logged.

terraform {
  required_version = ">= 1.9.0"

  required_providers {
    google = {
      source  = "hashicorp/google"
      version = "~> 6.14"
    }
  }
}

provider "google" {
  project = var.project_id
  region  = var.region
}

resource "google_storage_bucket" "terraform_state" {
  project  = var.project_id
  name     = var.state_bucket_name
  location = var.region

  storage_class               = "STANDARD"
  uniform_bucket_level_access = true
  public_access_prevention    = "enforced"
  force_destroy               = false

  # Versioning is how a corrupted or truncated state file is recovered.
  versioning {
    enabled = true
  }

  soft_delete_policy {
    retention_duration_seconds = 2592000 # 30 days
  }

  lifecycle_rule {
    condition {
      num_newer_versions = 30
      with_state         = "ARCHIVED"
    }
    action {
      type = "Delete"
    }
  }

  labels = {
    application = "resolveflow"
    purpose     = "terraform-state"
    managed_by  = "terraform"
  }

  lifecycle {
    prevent_destroy = true
  }
}

# Only the Terraform identities read or write state. Deploy identities and
# runtime identities must not: state reveals the shape of the whole environment.
resource "google_storage_bucket_iam_member" "state_writers" {
  for_each = toset(var.state_writer_members)

  bucket = google_storage_bucket.terraform_state.name
  role   = "roles/storage.objectAdmin"
  member = each.value
}
