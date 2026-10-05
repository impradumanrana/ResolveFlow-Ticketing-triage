# Cloud SQL for PostgreSQL.
#
# Private IP only: there is no public endpoint and no authorized-network
# allowlist. Application and migration identities authenticate as IAM service
# accounts, so no database password exists to store, rotate, or leak into state.
#
# pgvector is NOT created here. The extension and every table are owned by the
# Alembic migration chain (C04/C05) so that schema history has one source of
# truth. This module provisions the instance, the database, and access only.

resource "google_sql_database_instance" "main" {
  project          = var.project_id
  name             = "${var.name_prefix}-postgres"
  region           = var.region
  database_version = var.database_version

  # Protects against `terraform destroy` and console deletion separately.
  deletion_protection = var.deletion_protection

  encryption_key_name = var.kms_key_id

  settings {
    tier              = var.tier
    edition           = var.edition
    availability_type = var.availability_type
    disk_type         = "PD_SSD"
    disk_size         = var.disk_size_gb
    disk_autoresize   = true

    disk_autoresize_limit = var.disk_autoresize_limit_gb

    deletion_protection_enabled = var.deletion_protection

    user_labels = var.labels

    ip_configuration {
      # No public IP. Reachable only from the peered VPC.
      ipv4_enabled                                  = false
      private_network                               = var.network_id
      enable_private_path_for_google_cloud_services = true
      ssl_mode                                      = "ENCRYPTED_ONLY"
    }

    backup_configuration {
      enabled                        = true
      start_time                     = var.backup_start_time
      location                       = var.backup_location
      point_in_time_recovery_enabled = true
      transaction_log_retention_days = var.transaction_log_retention_days

      backup_retention_settings {
        retained_backups = var.retained_backups
        retention_unit   = "COUNT"
      }
    }

    maintenance_window {
      day          = var.maintenance_window_day
      hour         = var.maintenance_window_hour
      update_track = var.maintenance_update_track
    }

    insights_config {
      query_insights_enabled  = true
      query_string_length     = 1024
      record_application_tags = true
      record_client_address   = false # Client addresses are not needed and are PII-adjacent.
    }

    dynamic "database_flags" {
      for_each = var.database_flags
      content {
        name  = database_flags.key
        value = database_flags.value
      }
    }
  }

  # The instance cannot be created before the VPC peering range exists.
  depends_on = [var.private_service_access_connection]

  lifecycle {
    ignore_changes = [
      # Google rewrites this after a restore from backup.
      settings[0].disk_size,
    ]
  }
}

resource "google_sql_database" "application" {
  project  = var.project_id
  instance = google_sql_database_instance.main.name
  name     = var.database_name

  # Dropping the database would take every ticket, message, and embedding.
  deletion_policy = "ABANDON"
}

# IAM database users. The name is the service account email with the
# `.gserviceaccount.com` suffix removed, which is what Cloud SQL expects.
resource "google_sql_user" "iam_service_accounts" {
  for_each = toset(var.iam_service_account_emails)

  project  = var.project_id
  instance = google_sql_database_instance.main.name
  name     = trimsuffix(each.value, ".gserviceaccount.com")
  type     = "CLOUD_IAM_SERVICE_ACCOUNT"

  deletion_policy = "ABANDON"
}
