# Private Cloud Storage buckets.
#
# Every bucket in this module is uniform-access, public-access-prevented,
# versioned, CMEK-encrypted, and regional. There is no bucket that a browser
# can read directly: the application signs short-lived URLs for authorized
# users instead.

resource "google_storage_bucket" "managed" {
  for_each = var.buckets

  project  = var.project_id
  name     = "${var.name_prefix}-${each.key}-${var.bucket_suffix}"
  location = var.region

  storage_class = "STANDARD"

  uniform_bucket_level_access = true
  public_access_prevention    = "enforced"
  force_destroy               = false

  labels = merge(var.labels, {
    data_class = each.value.data_class
  })

  versioning {
    enabled = true
  }

  encryption {
    default_kms_key_name = var.kms_key_id
  }

  soft_delete_policy {
    retention_duration_seconds = each.value.soft_delete_retention_seconds
  }

  dynamic "retention_policy" {
    for_each = each.value.retention_seconds == null ? [] : [each.value.retention_seconds]
    content {
      retention_period = retention_policy.value
      is_locked        = each.value.retention_locked
    }
  }

  # Age out noncurrent versions so deletion actually reclaims data.
  lifecycle_rule {
    condition {
      num_newer_versions = each.value.keep_noncurrent_versions
      with_state         = "ARCHIVED"
    }
    action {
      type = "Delete"
    }
  }

  dynamic "lifecycle_rule" {
    for_each = each.value.delete_after_days == null ? [] : [each.value.delete_after_days]
    content {
      condition {
        age        = lifecycle_rule.value
        with_state = "LIVE"
      }
      action {
        type = "Delete"
      }
    }
  }

  lifecycle {
    prevent_destroy = true
  }
}

locals {
  bucket_bindings = merge([
    for bucket_key, bucket in var.buckets : {
      for grant in bucket.grants :
      "${bucket_key}:${grant.role}:${grant.member}" => {
        bucket_key = bucket_key
        role       = grant.role
        member     = grant.member
      }
    }
  ]...)
}

# Object access is granted per bucket and per identity. No principal holds a
# project-level storage role.
resource "google_storage_bucket_iam_member" "grants" {
  for_each = local.bucket_bindings

  bucket = google_storage_bucket.managed[each.value.bucket_key].name
  role   = each.value.role
  member = each.value.member
}
