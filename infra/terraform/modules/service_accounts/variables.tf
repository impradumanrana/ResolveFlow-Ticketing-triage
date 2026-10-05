variable "project_id" {
  description = "Client-owned GCP project that hosts this environment."
  type        = string
}

variable "name_prefix" {
  description = "Prefix applied to every resource name in this environment."
  type        = string
}

variable "accounts" {
  description = <<-DESC
    Runtime service accounts to create. `project_roles` must stay minimal:
    resource-scoped grants belong on the bucket, secret, topic, or key itself.
  DESC
  type = map(object({
    display_name  = string
    description   = string
    project_roles = optional(list(string), [])
  }))
}
