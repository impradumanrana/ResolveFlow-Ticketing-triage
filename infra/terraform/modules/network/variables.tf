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
  description = "Client-approved GCP region. V1 uses exactly one region."
  type        = string
}

variable "subnet_cidr" {
  description = "Primary subnet range used by direct VPC egress."
  type        = string
}

variable "private_service_access_cidr" {
  description = "Base address of the range reserved for Google-managed services."
  type        = string
}

variable "private_service_access_prefix_length" {
  description = "Prefix length of the reserved private service access range."
  type        = number
  default     = 20
}

variable "flow_log_sampling" {
  description = "VPC flow log sampling rate between 0 and 1."
  type        = number
  default     = 0.5

  validation {
    condition     = var.flow_log_sampling > 0 && var.flow_log_sampling <= 1
    error_message = "Flow log sampling must be greater than 0 and at most 1."
  }
}
