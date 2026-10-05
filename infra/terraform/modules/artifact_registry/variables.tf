variable "project_id" {
  description = "Client-owned GCP project that hosts this environment."
  type        = string
}

variable "environment" {
  description = "Environment name, staging or production."
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

variable "kms_key_id" {
  description = "CMEK for stored image layers."
  type        = string
  default     = null
}

variable "immutable_tags" {
  description = "Reject re-tagging an existing image tag. Production must keep this true."
  type        = bool
  default     = true
}

variable "keep_image_count" {
  description = "Number of recent image versions retained by the cleanup policy."
  type        = number
  default     = 20
}

variable "untagged_retention" {
  description = "Age after which untagged images are deleted."
  type        = string
  default     = "604800s" # 7 days
}

variable "cleanup_dry_run" {
  description = "Report cleanup matches without deleting. Start true, flip after review."
  type        = bool
  default     = true
}

variable "writer_members" {
  description = "IAM members allowed to push images. CI only."
  type        = list(string)
  default     = []
}

variable "reader_members" {
  description = "IAM members allowed to pull images explicitly."
  type        = list(string)
  default     = []
}

variable "labels" {
  description = "Labels applied to the repository."
  type        = map(string)
  default     = {}
}
