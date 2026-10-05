variable "project_id" {
  description = "Client-owned GCP project that hosts this environment."
  type        = string
}

variable "name_prefix" {
  description = "Prefix applied to every resource name in this environment."
  type        = string
}

variable "region" {
  description = "KMS location. Matches the client-approved data region."
  type        = string
}

variable "rotation_period" {
  description = "Automatic key rotation period in seconds."
  type        = string
  default     = "7776000s" # 90 days
}

variable "protection_level" {
  description = "SOFTWARE or HSM. HSM costs more and is a client decision."
  type        = string
  default     = "SOFTWARE"

  validation {
    condition     = contains(["SOFTWARE", "HSM"], var.protection_level)
    error_message = "Protection level must be SOFTWARE or HSM."
  }
}

variable "service_agent_members" {
  description = "Map of grant name to the key alias and service agent member that may use it."
  type = map(object({
    key    = string
    member = string
  }))
  default = {}
}

variable "labels" {
  description = "Labels applied to every key."
  type        = map(string)
  default     = {}
}
