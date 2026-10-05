variable "project_id" {
  description = "Client-owned GCP project that hosts this environment."
  type        = string
}

variable "name_prefix" {
  description = "Prefix applied to every resource name in this environment."
  type        = string
}

variable "region" {
  description = "Replication location. Secrets stay in the client-approved region."
  type        = string
}

variable "kms_key_id" {
  description = "CMEK used for secret payloads. Null falls back to Google-managed keys."
  type        = string
  default     = null
}

variable "secrets" {
  description = <<-DESC
    Secret containers to create. Values are never supplied here.
    `accessors` are IAM member strings granted read access to that one secret.
    `version_adders` may add a new version and nothing else: they cannot read,
    disable, or destroy versions. Only the client's own BYOK key is written by
    a runtime service; every other value is added out of band.
  DESC
  type = map(object({
    secret_class    = string
    accessors       = optional(list(string), [])
    version_adders  = optional(list(string), [])
    rotation_period = optional(string)
  }))
}

variable "rotation_topic_id" {
  description = "Pub/Sub topic notified on scheduled rotation. Required when any secret sets a rotation period."
  type        = string
  default     = null
}

variable "first_rotation_time" {
  description = "RFC3339 timestamp of the first scheduled rotation."
  type        = string
  default     = null
}

variable "labels" {
  description = "Labels applied to every secret."
  type        = map(string)
  default     = {}
}
