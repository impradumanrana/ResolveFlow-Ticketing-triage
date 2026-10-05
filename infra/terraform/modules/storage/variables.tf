variable "project_id" {
  description = "Client-owned GCP project that hosts this environment."
  type        = string
}

variable "name_prefix" {
  description = "Prefix applied to every resource name in this environment."
  type        = string
}

variable "bucket_suffix" {
  description = "Suffix that makes bucket names globally unique, normally the project id."
  type        = string
}

variable "region" {
  description = "Client-approved GCP region."
  type        = string
}

variable "kms_key_id" {
  description = "CMEK applied as the bucket default encryption key."
  type        = string
}

variable "buckets" {
  description = <<-DESC
    Buckets to create. `retention_seconds` sets a bucket lock window and
    `retention_locked` makes it irreversible, so leave it false until the client
    confirms a legal-hold requirement.
  DESC
  type = map(object({
    data_class                    = string
    soft_delete_retention_seconds = optional(number, 604800) # 7 days
    retention_seconds             = optional(number)
    retention_locked              = optional(bool, false)
    keep_noncurrent_versions      = optional(number, 3)
    delete_after_days             = optional(number)
    grants = optional(list(object({
      role   = string
      member = string
    })), [])
  }))
}

variable "labels" {
  description = "Labels applied to every bucket."
  type        = map(string)
  default     = {}
}
