variable "project_id" {
  description = "Client-owned GCP project that hosts this environment."
  type        = string
}

variable "enable_billing_budget" {
  description = "Enable the Billing Budgets API. Requires billing-account access."
  type        = bool
  default     = true
}
