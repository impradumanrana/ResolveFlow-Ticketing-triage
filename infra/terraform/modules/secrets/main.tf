# Secret Manager containers.
#
# This module creates empty secrets and the access grants for them. It never
# creates a secret *version*. Secret values are added out of band by an
# authorized client operator (see infra/docs/RUNBOOK.md), so no credential is
# written into Terraform configuration, plan output, or state.

resource "google_secret_manager_secret" "managed" {
  for_each = var.secrets

  project   = var.project_id
  secret_id = "${var.name_prefix}-${each.key}"

  labels = merge(var.labels, {
    secret_class = each.value.secret_class
  })

  replication {
    user_managed {
      replicas {
        location = var.region

        dynamic "customer_managed_encryption" {
          for_each = var.kms_key_id == null ? [] : [var.kms_key_id]
          content {
            kms_key_name = customer_managed_encryption.value
          }
        }
      }
    }
  }

  dynamic "rotation" {
    for_each = each.value.rotation_period == null ? [] : [each.value.rotation_period]
    content {
      next_rotation_time = var.first_rotation_time
      rotation_period    = rotation.value
    }
  }

  dynamic "topics" {
    for_each = each.value.rotation_period == null ? [] : [1]
    content {
      name = var.rotation_topic_id
    }
  }

  lifecycle {
    prevent_destroy = true
  }
}

locals {
  accessor_bindings = merge([
    for secret_key, secret in var.secrets : {
      for member in secret.accessors :
      "${secret_key}:${member}" => {
        secret_key = secret_key
        member     = member
      }
    }
  ]...)
}

# Accessor grants are per secret. No identity holds a project-wide
# secretmanager.secretAccessor role.
resource "google_secret_manager_secret_iam_member" "accessors" {
  for_each = local.accessor_bindings

  project   = var.project_id
  secret_id = google_secret_manager_secret.managed[each.value.secret_key].secret_id
  role      = "roles/secretmanager.secretAccessor"
  member    = each.value.member
}

locals {
  version_adder_bindings = merge([
    for secret_key, secret in var.secrets : {
      for member in secret.version_adders :
      "${secret_key}:${member}" => {
        secret_key = secret_key
        member     = member
      }
    }
  ]...)
}

# Add-only grants, per secret. Used for the client's BYOK key, which an Owner
# replaces from the application after the provider has accepted it (C10).
resource "google_secret_manager_secret_iam_member" "version_adders" {
  for_each = local.version_adder_bindings

  project   = var.project_id
  secret_id = google_secret_manager_secret.managed[each.value.secret_key].secret_id
  role      = "roles/secretmanager.secretVersionAdder"
  member    = each.value.member
}
