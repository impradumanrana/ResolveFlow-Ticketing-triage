# Customer-managed encryption keys.
#
# One key ring per environment with a separate key per data class, so a single
# key can be rotated or revoked without affecting unrelated data. Key material
# never leaves KMS and is never rendered into Terraform state.

resource "google_kms_key_ring" "main" {
  project  = var.project_id
  name     = "${var.name_prefix}-keyring"
  location = var.region
}

locals {
  # Purpose -> rotation period. Database and storage keys hold the longest-lived
  # client data, so they rotate on the same schedule as the others but are kept
  # separate to allow independent revocation.
  keys = {
    sql      = "Cloud SQL data encryption."
    storage  = "Knowledge files and permitted attachments."
    pubsub   = "Gmail notification and ticket event payloads."
    secrets  = "Secret Manager envelope encryption."
    artifact = "Container image encryption in Artifact Registry."
  }
}

resource "google_kms_crypto_key" "keys" {
  for_each = local.keys

  name            = "${var.name_prefix}-${each.key}"
  key_ring        = google_kms_key_ring.main.id
  purpose         = "ENCRYPT_DECRYPT"
  rotation_period = var.rotation_period

  labels = merge(var.labels, {
    data_class = each.key
  })

  version_template {
    algorithm        = "GOOGLE_SYMMETRIC_ENCRYPTION"
    protection_level = var.protection_level
  }

  lifecycle {
    # A destroyed key is unrecoverable and takes its ciphertext with it.
    prevent_destroy = true
  }
}

# Each Google service encrypts with its own agent identity. Grants are scoped to
# the single key that service uses, never to the key ring.
resource "google_kms_crypto_key_iam_member" "service_agents" {
  for_each = var.service_agent_members

  crypto_key_id = google_kms_crypto_key.keys[each.value.key].id
  role          = "roles/cloudkms.cryptoKeyEncrypterDecrypter"
  member        = each.value.member
}
