variable "project_id" {
  description = "Client-owned GCP project that hosts this environment."
  type        = string
}

variable "name_prefix" {
  description = "Prefix applied to every resource name in this environment."
  type        = string
}

variable "region" {
  description = "Client-approved GCP region."
  type        = string
}

variable "network_id" {
  description = "VPC used for direct VPC egress."
  type        = string
}

variable "subnet_id" {
  description = "Subnet used for direct VPC egress."
  type        = string
}

variable "sql_connection_name" {
  description = "Cloud SQL instance connection name mounted for services that need the database."
  type        = string
}

variable "service_account_emails" {
  description = "Map of runtime identity key to service account email."
  type        = map(string)
}

variable "common_env" {
  description = "Non-secret environment variables applied to every service."
  type        = map(string)
  default     = {}
}

variable "services" {
  description = <<-DESC
    Cloud Run services. Leave `private_tier` true for anything holding model
    credentials, mailbox tokens, or the MCP boundary; those services must use
    INGRESS_TRAFFIC_INTERNAL_ONLY. `secret_env` maps an environment variable
    name to a Secret Manager secret id; values are resolved by the platform at
    start-up and never appear in this configuration, in plan output, or in state.
  DESC
  type = map(object({
    description         = string
    service_account_key = string
    image               = optional(string, "us-docker.pkg.dev/cloudrun/container/hello")
    ingress             = optional(string, "INGRESS_TRAFFIC_INTERNAL_ONLY")
    container_port      = optional(number, 8080)
    cpu                 = optional(string, "1")
    memory              = optional(string, "1Gi")
    min_instances       = optional(number, 0)
    max_instances       = optional(number, 4)
    concurrency         = optional(number, 80)
    request_timeout     = optional(string, "300s")
    health_path         = optional(string)
    needs_database      = optional(bool, false)
    private_tier        = optional(bool, true)
    deletion_protection = optional(bool, false)
    env                 = optional(map(string), {})
    secret_env          = optional(map(string), {})
    invoker_members     = optional(list(string), [])
  }))

  validation {
    condition = alltrue([
      for service in var.services :
      contains([
        "INGRESS_TRAFFIC_ALL",
        "INGRESS_TRAFFIC_INTERNAL_ONLY",
        "INGRESS_TRAFFIC_INTERNAL_LOAD_BALANCER",
      ], service.ingress)
    ])
    error_message = "Each service ingress must be a recognised Cloud Run ingress setting."
  }

  # The web tier is the only service allowed to answer a browser. Everything
  # holding provider credentials, mailbox tokens, or the MCP boundary is private.
  validation {
    condition = alltrue([
      for service in var.services :
      service.ingress == "INGRESS_TRAFFIC_INTERNAL_ONLY" if service.private_tier
    ])
    error_message = "A service marked private_tier must not accept traffic from outside the VPC."
  }

  validation {
    condition     = length([for service in var.services : service if !service.private_tier]) <= 1
    error_message = "At most one service may be publicly reachable. Keep the web tier as the only public surface."
  }
}

variable "migration_job" {
  description = "Cloud Run job that applies Alembic migrations. Null disables it."
  type = object({
    service_account_key = string
    image               = optional(string, "us-docker.pkg.dev/cloudrun/container/hello")
    command             = optional(list(string), ["python"])
    args                = optional(list(string), ["-m", "alembic", "upgrade", "head"])
    timeout             = optional(string, "900s")
    env                 = optional(map(string), {})
  })
  default = null
}

variable "labels" {
  description = "Labels applied to every service."
  type        = map(string)
  default     = {}
}
