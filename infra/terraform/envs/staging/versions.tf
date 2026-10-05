terraform {
  required_version = ">= 1.9.0"

  required_providers {
    google = {
      source  = "hashicorp/google"
      version = "~> 6.14"
    }
  }

  # Partial configuration. The state bucket is client-owned and created by
  # infra/terraform/bootstrap, so it cannot be named here before it exists:
  #
  #   terraform init -backend-config=backend.hcl
  #
  # State holds resource metadata and must be treated as sensitive: the bucket
  # is private, versioned, and access-audited.
  backend "gcs" {
    prefix = "resolveflow/staging"
  }
}
