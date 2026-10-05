variable "project_id" {
  description = "Client-owned project that holds Terraform state. May be a dedicated admin project."
  type        = string
}

variable "region" {
  description = "Client-approved region for the state bucket."
  type        = string
}

variable "state_bucket_name" {
  description = "Globally unique name for the Terraform state bucket."
  type        = string
}

variable "state_writer_members" {
  description = "IAM members allowed to read and write Terraform state. Terraform identities only."
  type        = list(string)
  default     = []
}
