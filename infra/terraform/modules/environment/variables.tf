# ---------------------------------------------------------------------------
# Client-supplied values. Every one of these is a recorded gate in
# CLIENT_SCOPE.md. There are no defaults for project, region, billing, domain,
# or contact values: guessing a client deployment parameter is worse than
# failing to plan.
# ---------------------------------------------------------------------------

variable "project_id" {
  description = "Client-owned GCP project for this environment."
  type        = string
}

variable "environment" {
  description = "Environment name."
  type        = string

  validation {
    condition     = contains(["staging", "production"], var.environment)
    error_message = "Environment must be staging or production."
  }
}

variable "region" {
  description = "Client-approved GCP region. V1 is single-region with no cross-region fallback."
  type        = string
}

variable "github_repository" {
  description = "Repository allowed to federate into this project, as owner/name."
  type        = string
}

variable "billing_account_id" {
  description = "Client billing account for the budget. Null disables budget creation."
  type        = string
  default     = null
}

variable "alert_email_addresses" {
  description = "Client operations contacts for alerts and budget notifications."
  type        = list(string)
  default     = []
}

variable "web_invoker_members" {
  description = <<-DESC
    IAM members allowed to invoke the web service. Staging should name the
    client's test group; production uses allUsers once launch is approved,
    because Workspace login is enforced by the application, not by Cloud Run.
    An empty list leaves the service reachable by nobody, which is the correct
    state before the client supplies its launch cohort.
  DESC
  type        = list(string)
  default     = []
}

variable "google_oauth_client_id" {
  description = <<-DESC
    Google Workspace OAuth client id used for sign-in only (C03). Public by
    design, so it is configuration rather than a secret; the matching client
    secret lives in Secret Manager. Empty until the client creates the client.
  DESC
  type        = string
  default     = ""
}

variable "gmail_oauth_client_id" {
  description = <<-DESC
    OAuth client used to connect Gmail mailboxes (C06). Kept separate from the
    sign-in client so the restricted gmail.readonly scope never sits on the
    client every staff member signs in through. Empty until the client's
    Workspace administrator creates it.
  DESC
  type        = string
  default     = ""
}

variable "gmail_oauth_redirect_uri" {
  description = "HTTPS callback registered on the Gmail OAuth client, ending /api/mailboxes/oauth/callback."
  type        = string
  default     = ""

  validation {
    condition     = var.gmail_oauth_redirect_uri == "" || can(regex("^https://[^/]+/api/mailboxes/oauth/callback$", var.gmail_oauth_redirect_uri))
    error_message = "The Gmail redirect URI must be HTTPS and end with /api/mailboxes/oauth/callback."
  }
}

variable "mailbox_scope_profile" {
  description = <<-DESC
    Which OAuth scopes mailbox consent asks for. "read_only" is the default and
    the only posture a deployment gets without an explicit client decision.
    "read_and_draft" adds gmail.compose so an approved answer can become a
    draft in the client's mailbox; it also requires the scope on the client's
    own consent screen, migration 20260918_0010, and the mailbox reconnected.
    See "Turning mailbox drafting on or off" in infra/docs/RUNBOOK.md.

    Neither value permits sending. Nothing in the product calls a send endpoint
    and organizations.sending_enabled stays false.
  DESC
  type        = string
  default     = "read_only"

  validation {
    condition     = contains(["read_only", "read_and_draft"], var.mailbox_scope_profile)
    error_message = "The mailbox scope profile must be read_only or read_and_draft."
  }
}

variable "uptime_check_host" {
  description = "Public hostname for the web uptime check. Null until a domain is mapped."
  type        = string
  default     = null
}

# ---------------------------------------------------------------------------
# Network
# ---------------------------------------------------------------------------

variable "subnet_cidr" {
  description = "Primary subnet range."
  type        = string
}

variable "private_service_access_cidr" {
  description = "Base address of the Cloud SQL private service access range."
  type        = string
}

variable "private_service_access_prefix_length" {
  description = "Prefix length of the private service access range."
  type        = number
  default     = 20
}

variable "flow_log_sampling" {
  description = "VPC flow log sampling rate."
  type        = number
  default     = 0.5
}

# ---------------------------------------------------------------------------
# Security posture
# ---------------------------------------------------------------------------

variable "kms_protection_level" {
  description = "SOFTWARE or HSM key protection."
  type        = string
  default     = "SOFTWARE"
}

variable "immutable_image_tags" {
  description = "Reject re-tagging an existing image tag."
  type        = bool
  default     = true
}

variable "ci_deploy_principal" {
  description = "Federated attribute and value permitted to impersonate the deploy identity."
  type = object({
    attribute = string
    value     = string
  })
}

variable "ci_infra_principal" {
  description = "Federated attribute and value permitted to impersonate the Terraform identity. Use a protected GitHub environment."
  type = object({
    attribute = string
    value     = string
  })
}

# ---------------------------------------------------------------------------
# Database sizing and durability
# ---------------------------------------------------------------------------

variable "database_tier" {
  description = "Cloud SQL machine tier."
  type        = string
}

variable "database_availability_type" {
  description = "ZONAL or REGIONAL."
  type        = string
}

variable "database_disk_size_gb" {
  description = "Initial data disk size."
  type        = number
  default     = 20
}

variable "database_deletion_protection" {
  description = "Block instance deletion."
  type        = bool
  default     = true
}

variable "database_retained_backups" {
  description = "Number of automated backups retained."
  type        = number
  default     = 14
}

variable "database_pitr_days" {
  description = "Point-in-time recovery window in days."
  type        = number
  default     = 7
}

variable "database_max_connections" {
  description = "PostgreSQL max_connections. Cloud Run scales horizontally, so this bounds pool exhaustion."
  type        = number
  default     = 100
}

variable "slow_query_log_threshold_ms" {
  description = "Log statements slower than this many milliseconds."
  type        = number
  default     = 1000
}

# ---------------------------------------------------------------------------
# Service sizing
# ---------------------------------------------------------------------------

variable "web_cpu" {
  description = "Web service CPU limit."
  type        = string
  default     = "1"
}

variable "web_memory" {
  description = "Web service memory limit."
  type        = string
  default     = "1Gi"
}

variable "web_min_instances" {
  description = "Minimum web instances. Non-zero avoids cold starts on the interactive path."
  type        = number
  default     = 0
}

variable "web_max_instances" {
  description = "Maximum web instances."
  type        = number
  default     = 4
}

variable "api_cpu" {
  description = "AI API CPU limit."
  type        = string
  default     = "2"
}

variable "api_memory" {
  description = "AI API memory limit. Retrieval and the MCP process need headroom."
  type        = string
  default     = "2Gi"
}

variable "api_min_instances" {
  description = "Minimum AI API instances."
  type        = number
  default     = 0
}

variable "api_max_instances" {
  description = "Maximum AI API instances. Also bounds concurrent provider spend."
  type        = number
  default     = 4
}

variable "worker_cpu" {
  description = "Worker CPU limit."
  type        = string
  default     = "1"
}

variable "worker_memory" {
  description = "Worker memory limit."
  type        = string
  default     = "2Gi"
}

variable "worker_min_instances" {
  description = "Minimum worker instances."
  type        = number
  default     = 0
}

variable "worker_max_instances" {
  description = "Maximum worker instances."
  type        = number
  default     = 6
}

variable "service_deletion_protection" {
  description = "Block Cloud Run service deletion."
  type        = bool
  default     = false
}

# ---------------------------------------------------------------------------
# Retention
# ---------------------------------------------------------------------------

variable "attachment_retention_days" {
  description = "Delete stored attachments after this many days. Confirmed with the client at C13."
  type        = number
  default     = 90
}

variable "export_retention_days" {
  description = "Delete generated privacy or audit exports after this many days."
  type        = number
  default     = 14
}

variable "log_retention_days" {
  description = "Delete archived logs after this many days."
  type        = number
  default     = 400
}

variable "log_lock_retention_seconds" {
  description = "Bucket-lock window on the log archive. Null leaves it unlocked; locking is irreversible."
  type        = number
  default     = null
}

# ---------------------------------------------------------------------------
# Operations
# ---------------------------------------------------------------------------

variable "scheduler_time_zone" {
  description = "IANA time zone for scheduled jobs."
  type        = string
  default     = "Etc/UTC"
}

variable "alert_thresholds" {
  description = "Alert thresholds for this environment."
  type = object({
    http_5xx_per_minute         = optional(number, 1)
    unacked_message_age_seconds = optional(number, 900)
    database_disk_utilization   = optional(number, 0.85)
    triage_failures_per_minute  = optional(number, 1)
  })
  default = {}
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
  description = "Budget fractions at which notifications fire."
  type        = list(number)
  default     = [0.5, 0.8, 0.95]
}

variable "labels" {
  description = "Extra labels applied to supported resources."
  type        = map(string)
  default     = {}
}
