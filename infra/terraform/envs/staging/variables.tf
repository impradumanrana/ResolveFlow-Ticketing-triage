# Values with no default are recorded client gates in CLIENT_SCOPE.md.
# `terraform plan` fails without them rather than inventing a deployment target.

variable "project_id" {
  description = "Client-owned GCP project for staging."
  type        = string
}

variable "region" {
  description = "Client-approved GCP region."
  type        = string
}

variable "github_repository" {
  description = "Repository allowed to federate into this project, as owner/name."
  type        = string
}

variable "impersonate_service_account" {
  description = "Service account Terraform impersonates. Null uses the caller's own credentials."
  type        = string
  default     = null
}

variable "billing_account_id" {
  description = "Client billing account for budget alerts. Null disables the budget."
  type        = string
  default     = null
}

variable "alert_email_addresses" {
  description = "Client operations contacts for alerts."
  type        = list(string)
  default     = []
}

variable "web_invoker_members" {
  description = "Members allowed to reach staging. Empty keeps staging closed."
  type        = list(string)
  default     = []
}

variable "uptime_check_host" {
  description = "Hostname for the uptime check, once a domain is mapped."
  type        = string
  default     = null
}

variable "monthly_budget_amount" {
  description = "Monthly staging budget in whole currency units."
  type        = number
  default     = 150
}

variable "budget_currency" {
  description = "Budget currency code."
  type        = string
  default     = "USD"
}

variable "scheduler_time_zone" {
  description = "IANA time zone for scheduled jobs."
  type        = string
  default     = "Etc/UTC"
}

variable "google_oauth_client_id" {
  description = "Workspace OAuth client id for sign-in. Supplied by the client's Workspace administrator."
  type        = string
  default     = ""
}

variable "gmail_oauth_client_id" {
  description = "Gmail connection OAuth client id. Supplied by the client's Workspace administrator."
  type        = string
  default     = ""
}

variable "gmail_oauth_redirect_uri" {
  description = "HTTPS Gmail connection callback, ending /api/mailboxes/oauth/callback."
  type        = string
  default     = ""
}
