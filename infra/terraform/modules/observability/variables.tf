variable "project_id" {
  description = "Client-owned GCP project that hosts this environment."
  type        = string
}

variable "project_number" {
  description = "Numeric project id, required by the budget filter."
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

variable "alert_email_addresses" {
  description = "Client operations contacts notified by alert policies and budgets."
  type        = list(string)
  default     = []
}

variable "log_archive_bucket" {
  description = "Bucket that receives the audit and warning log sink."
  type        = string
}

variable "thresholds" {
  description = "Alert thresholds, tuned per environment."
  type = object({
    http_5xx_per_minute         = optional(number, 1)
    unacked_message_age_seconds = optional(number, 900)
    database_disk_utilization   = optional(number, 0.85)
    triage_failures_per_minute  = optional(number, 1)
  })
  default = {}
}

variable "uptime_check_host" {
  description = "Hostname for the web uptime check. Null disables the check."
  type        = string
  default     = null
}

variable "uptime_check_path" {
  description = "Path probed by the uptime check."
  type        = string
  default     = "/api/health"
}

variable "billing_account_id" {
  description = "Client billing account. Null disables the budget."
  type        = string
  default     = null
}

variable "monthly_budget_amount" {
  description = "Monthly budget in whole currency units."
  type        = number
  default     = 500
}

variable "budget_currency" {
  description = "Budget currency code."
  type        = string
  default     = "USD"
}

variable "budget_threshold_percents" {
  description = "Fractions of the budget at which a notification fires."
  type        = list(number)
  default     = [0.5, 0.8, 0.95]
}
