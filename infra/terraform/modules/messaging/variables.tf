variable "project_id" {
  description = "Client-owned GCP project that hosts this environment."
  type        = string
}

variable "name_prefix" {
  description = "Prefix applied to every resource name in this environment."
  type        = string
}

variable "region" {
  description = "Client-approved GCP region. Message storage is pinned to it."
  type        = string
}

variable "kms_key_id" {
  description = "CMEK for message payloads at rest."
  type        = string
  default     = null
}

variable "pubsub_service_agent" {
  description = "Pub/Sub service agent email, needed for dead-letter forwarding."
  type        = string
}

variable "push_endpoints" {
  description = "Map of endpoint key to Cloud Run base URL that push and scheduled jobs target."
  type        = map(string)
}

variable "push_invoker_service_account" {
  description = "Service account whose OIDC token authenticates Pub/Sub push requests."
  type        = string
}

variable "scheduler_invoker_service_account" {
  description = "Service account whose OIDC token authenticates scheduled requests."
  type        = string
}

variable "scheduler_time_zone" {
  description = "IANA time zone for cron expressions."
  type        = string
  default     = "Etc/UTC"
}

variable "dead_letter_retention" {
  description = "How long dead letters are held for operator replay."
  type        = string
  default     = "604800s" # 7 days
}

variable "topics" {
  description = "Pub/Sub topics. `publishers` names external identities such as the Gmail push account."
  type = map(object({
    dead_letter       = optional(bool, true)
    message_retention = optional(string, "604800s")
    publishers        = optional(list(string), [])
  }))
}

variable "subscriptions" {
  description = "Push subscriptions delivering to a private Cloud Run service."
  type = map(object({
    topic                 = string
    push_endpoint_key     = string
    push_path             = string
    ack_deadline_seconds  = optional(number, 60)
    message_retention     = optional(string, "604800s")
    max_delivery_attempts = optional(number, 10)
    minimum_backoff       = optional(string, "10s")
    maximum_backoff       = optional(string, "600s")
    ordered               = optional(bool, false)
  }))
}

variable "task_queues" {
  description = "Cloud Tasks queues for controlled outbound work."
  type = map(object({
    max_dispatches_per_second = optional(number, 5)
    max_concurrent_dispatches = optional(number, 10)
    max_attempts              = optional(number, 5)
    min_backoff               = optional(string, "10s")
    max_backoff               = optional(string, "600s")
    max_retry_duration        = optional(string, "3600s")
  }))
  default = {}
}

variable "scheduler_jobs" {
  description = "Scheduled reconciliation and maintenance calls."
  type = map(object({
    description      = string
    schedule         = string
    endpoint_key     = string
    path             = string
    attempt_deadline = optional(string, "320s")
    retry_count      = optional(number, 3)
  }))
  default = {}
}

variable "labels" {
  description = "Labels applied to topics and subscriptions."
  type        = map(string)
  default     = {}
}
