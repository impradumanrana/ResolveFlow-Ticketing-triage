variable "project_id" {
  description = "Client-owned GCP project that hosts this environment."
  type        = string
}

variable "name_prefix" {
  description = "Prefix applied to every resource name in this environment."
  type        = string
}

variable "region" {
  description = "Client-approved GCP region."
  type        = string
}

variable "network_id" {
  description = "VPC the instance attaches to over private service access."
  type        = string
}

variable "private_service_access_connection" {
  description = "Peering connection id, used only for dependency ordering."
  type        = string
}

variable "database_version" {
  description = "Cloud SQL PostgreSQL version. pgvector requires 15 or later."
  type        = string
  default     = "POSTGRES_16"

  validation {
    condition     = can(regex("^POSTGRES_(1[5-9]|[2-9][0-9])$", var.database_version))
    error_message = "Use POSTGRES_15 or later so pgvector is available."
  }
}

variable "database_name" {
  description = "Application database name."
  type        = string
  default     = "resolveflow"
}

variable "tier" {
  description = "Machine tier. Sized per environment."
  type        = string
}

variable "edition" {
  description = "ENTERPRISE or ENTERPRISE_PLUS."
  type        = string
  default     = "ENTERPRISE"
}

variable "availability_type" {
  description = "ZONAL or REGIONAL. Production must be REGIONAL."
  type        = string

  validation {
    condition     = contains(["ZONAL", "REGIONAL"], var.availability_type)
    error_message = "Availability type must be ZONAL or REGIONAL."
  }
}

variable "disk_size_gb" {
  description = "Initial data disk size."
  type        = number
  default     = 20
}

variable "disk_autoresize_limit_gb" {
  description = "Upper bound on automatic disk growth. 0 means unlimited, which is not used here."
  type        = number
  default     = 200
}

variable "deletion_protection" {
  description = "Block deletion of the instance. Production must keep this true."
  type        = bool
  default     = true
}

variable "kms_key_id" {
  description = "CMEK for instance data."
  type        = string
  default     = null
}

variable "backup_start_time" {
  description = "Daily backup start time, HH:MM UTC."
  type        = string
  default     = "02:00"
}

variable "backup_location" {
  description = "Backup location. Kept in the client-approved region by default."
  type        = string
  default     = null
}

variable "retained_backups" {
  description = "Number of automated backups retained."
  type        = number
  default     = 14
}

variable "transaction_log_retention_days" {
  description = "PITR window in days. Bounds the recovery point objective."
  type        = number
  default     = 7

  validation {
    condition     = var.transaction_log_retention_days >= 1 && var.transaction_log_retention_days <= 35
    error_message = "Transaction log retention must be between 1 and 35 days."
  }
}

variable "maintenance_window_day" {
  description = "Day of week for maintenance, 1 is Monday."
  type        = number
  default     = 7
}

variable "maintenance_window_hour" {
  description = "Hour of day for maintenance, UTC."
  type        = number
  default     = 3
}

variable "maintenance_update_track" {
  description = "stable or week5. Production trails staging."
  type        = string
  default     = "stable"
}

variable "database_flags" {
  description = "Cloud SQL database flags as a name to value map."
  type        = map(string)
  default     = {}
}

variable "iam_service_account_emails" {
  description = "Service accounts that may authenticate to the database as IAM users."
  type        = list(string)
  default     = []
}

variable "labels" {
  description = "Labels applied to the instance."
  type        = map(string)
  default     = {}
}
