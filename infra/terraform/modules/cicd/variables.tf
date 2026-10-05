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

variable "github_repository" {
  description = "The only repository allowed to federate, as owner/name."
  type        = string

  validation {
    condition     = can(regex("^[A-Za-z0-9._-]+/[A-Za-z0-9._-]+$", var.github_repository))
    error_message = "Provide the repository as owner/name."
  }
}

variable "ci_accounts" {
  description = <<-DESC
    CI identities. `principal_attribute` and `principal_value` narrow which
    federated subject may impersonate the account, for example
    attribute `environment` with value `production`.
  DESC
  type = map(object({
    display_name        = string
    description         = string
    project_roles       = optional(list(string), [])
    principal_attribute = string
    principal_value     = string
  }))
}

variable "deploy_account_key" {
  description = "Key of the CI account that deploys Cloud Run revisions."
  type        = string
  default     = "deploy"
}

variable "runtime_service_account_ids" {
  description = "Runtime service account ids the deploy identity may act as."
  type        = map(string)
  default     = {}
}
