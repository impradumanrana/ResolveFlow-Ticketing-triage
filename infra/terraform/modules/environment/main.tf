# One ResolveFlow environment, composed from the single-purpose modules.
#
# Staging and production call this module with different sizing, thresholds, and
# protection settings. They never share a project, network, database, bucket,
# key, secret, registry, or service account. Promotion between them is a
# deliberate CI action against a specific image digest, not shared state.

data "google_project" "current" {
  project_id = var.project_id
}

data "google_storage_project_service_account" "gcs" {
  project    = var.project_id
  depends_on = [module.project_services]
}

locals {
  name_prefix = "rf-${var.environment}"

  labels = merge(var.labels, {
    application = "resolveflow"
    environment = var.environment
    managed_by  = "terraform"
  })

  project_number = data.google_project.current.number

  # Google-managed service agents that encrypt with our CMEK or publish to our
  # topics. Written out rather than hardcoded anywhere else in the stack.
  service_agents = {
    pubsub    = "service-${local.project_number}@gcp-sa-pubsub.iam.gserviceaccount.com"
    sql       = "service-${local.project_number}@gcp-sa-cloud-sql.iam.gserviceaccount.com"
    secrets   = "service-${local.project_number}@gcp-sa-secretmanager.iam.gserviceaccount.com"
    artifacts = "service-${local.project_number}@gcp-sa-artifactregistry.iam.gserviceaccount.com"
    storage   = data.google_storage_project_service_account.gcs.email_address
  }

  # Gmail's push notifications are published by a Google-owned account. It is
  # granted publisher on exactly one topic.
  gmail_push_member = "serviceAccount:gmail-api-push@system.gserviceaccount.com"
}

module "project_services" {
  source = "../project_services"

  project_id            = var.project_id
  enable_billing_budget = var.billing_account_id != null
}

module "network" {
  source = "../network"

  project_id                           = var.project_id
  environment                          = var.environment
  name_prefix                          = local.name_prefix
  region                               = var.region
  subnet_cidr                          = var.subnet_cidr
  private_service_access_cidr          = var.private_service_access_cidr
  private_service_access_prefix_length = var.private_service_access_prefix_length
  flow_log_sampling                    = var.flow_log_sampling

  depends_on = [module.project_services]
}

module "kms" {
  source = "../kms"

  project_id       = var.project_id
  name_prefix      = local.name_prefix
  region           = var.region
  protection_level = var.kms_protection_level
  labels           = local.labels

  service_agent_members = {
    sql       = { key = "sql", member = "serviceAccount:${local.service_agents.sql}" }
    storage   = { key = "storage", member = "serviceAccount:${local.service_agents.storage}" }
    pubsub    = { key = "pubsub", member = "serviceAccount:${local.service_agents.pubsub}" }
    secrets   = { key = "secrets", member = "serviceAccount:${local.service_agents.secrets}" }
    artifacts = { key = "artifact", member = "serviceAccount:${local.service_agents.artifacts}" }
  }

  depends_on = [module.project_services]
}

module "service_accounts" {
  source = "../service_accounts"

  project_id  = var.project_id
  name_prefix = local.name_prefix

  accounts = {
    web = {
      display_name = "ResolveFlow web BFF (${var.environment})"
      description  = "Serves the authenticated Next.js application. Holds session storage access only."
      # Sessions and membership live in Cloud SQL; the web tier authenticates
      # as an IAM database user rather than with a stored password.
      project_roles = ["roles/cloudsql.client", "roles/cloudsql.instanceUser"]
    }
    api = {
      display_name  = "ResolveFlow AI API (${var.environment})"
      description   = "Private triage, retrieval, and MCP boundary. Holds model credentials."
      project_roles = ["roles/cloudsql.client", "roles/cloudsql.instanceUser"]
    }
    worker = {
      display_name  = "ResolveFlow ingestion worker (${var.environment})"
      description   = "Processes mailbox events, reconciliation, and queued work."
      project_roles = ["roles/cloudsql.client", "roles/cloudsql.instanceUser", "roles/cloudtasks.enqueuer"]
    }
    migrate = {
      display_name  = "ResolveFlow migration job (${var.environment})"
      description   = "Applies the Alembic migration chain. Runs from CI, never on boot."
      project_roles = ["roles/cloudsql.client", "roles/cloudsql.instanceUser"]
    }
    pubsub_invoker = {
      display_name = "ResolveFlow Pub/Sub invoker (${var.environment})"
      description  = "Mints OIDC tokens for push delivery to the worker. No data access."
    }
    scheduler_invoker = {
      display_name = "ResolveFlow Scheduler invoker (${var.environment})"
      description  = "Mints OIDC tokens for reconciliation and watch renewal. No data access."
    }
  }

  depends_on = [module.project_services]
}

module "artifact_registry" {
  source = "../artifact_registry"

  project_id     = var.project_id
  environment    = var.environment
  name_prefix    = local.name_prefix
  region         = var.region
  kms_key_id     = module.kms.key_ids["artifact"]
  immutable_tags = var.immutable_image_tags
  labels         = local.labels

  writer_members = ["serviceAccount:${module.cicd.ci_service_account_emails["deploy"]}"]
}

module "storage" {
  source = "../storage"

  project_id    = var.project_id
  name_prefix   = local.name_prefix
  bucket_suffix = var.project_id
  region        = var.region
  kms_key_id    = module.kms.key_ids["storage"]
  labels        = local.labels

  buckets = {
    knowledge = {
      data_class = "client-knowledge"
      grants = [
        { role = "roles/storage.objectUser", member = module.service_accounts.members["api"] },
        { role = "roles/storage.objectUser", member = module.service_accounts.members["web"] },
      ]
    }
    attachments = {
      data_class        = "client-attachments"
      delete_after_days = var.attachment_retention_days
      grants = [
        { role = "roles/storage.objectUser", member = module.service_accounts.members["worker"] },
        { role = "roles/storage.objectViewer", member = module.service_accounts.members["api"] },
      ]
    }
    exports = {
      data_class        = "privacy-exports"
      delete_after_days = var.export_retention_days
      grants = [
        { role = "roles/storage.objectUser", member = module.service_accounts.members["web"] },
      ]
    }
    logs = {
      data_class        = "operational-logs"
      delete_after_days = var.log_retention_days
      retention_seconds = var.log_lock_retention_seconds
    }
  }
}

module "secrets" {
  source = "../secrets"

  project_id  = var.project_id
  name_prefix = local.name_prefix
  region      = var.region
  kms_key_id  = module.kms.key_ids["secrets"]
  labels      = local.labels

  # Containers only. Values are added by an authorized client operator after
  # apply; see infra/docs/RUNBOOK.md.
  secrets = {
    auth-session-secret = {
      secret_class = "session-signing"
      accessors    = [module.service_accounts.members["web"]]
    }
    audit-ip-salt = {
      secret_class = "audit-pseudonymisation"
      accessors    = [module.service_accounts.members["web"]]
    }
    google-oauth-client-secret = {
      secret_class = "workspace-login"
      accessors    = [module.service_accounts.members["web"]]
    }
    gmail-oauth-client-secret = {
      secret_class = "mailbox-connection"
      # The api exchanges the authorization code during connection (C06); the
      # worker refreshes tokens during ingestion (C07). The browser-facing web
      # tier never holds it.
      accessors = [
        module.service_accounts.members["api"],
        module.service_accounts.members["worker"],
      ]
    }
    mailbox-token-encryption-key = {
      secret_class = "envelope-encryption"
      accessors = [
        module.service_accounts.members["worker"],
        module.service_accounts.members["api"],
      ]
    }
    llm-provider-api-key = {
      secret_class = "client-byok"
      accessors    = [module.service_accounts.members["api"]]
      # The api stores a replacement key only after the provider accepts it.
      version_adders = [module.service_accounts.members["api"]]
    }
    internal-service-token = {
      secret_class = "service-to-service"
      accessors = [
        module.service_accounts.members["web"],
        module.service_accounts.members["api"],
        module.service_accounts.members["worker"],
      ]
    }
  }
}

module "database" {
  source = "../database"

  project_id                        = var.project_id
  name_prefix                       = local.name_prefix
  region                            = var.region
  network_id                        = module.network.network_id
  private_service_access_connection = module.network.private_service_access_connection
  kms_key_id                        = module.kms.key_ids["sql"]
  labels                            = local.labels

  tier                           = var.database_tier
  availability_type              = var.database_availability_type
  disk_size_gb                   = var.database_disk_size_gb
  deletion_protection            = var.database_deletion_protection
  retained_backups               = var.database_retained_backups
  transaction_log_retention_days = var.database_pitr_days
  backup_location                = var.region

  database_flags = {
    # IAM authentication removes stored database passwords entirely.
    "cloudsql.iam_authentication" = "on"
    # Audit DDL and role changes; statement-level auditing of tenant data is a
    # C13 decision because it would log message content.
    "cloudsql.enable_pgaudit"    = "on"
    "pgaudit.log"                = "ddl,role"
    "log_min_duration_statement" = tostring(var.slow_query_log_threshold_ms)
    "max_connections"            = tostring(var.database_max_connections)
  }

  iam_service_account_emails = [
    module.service_accounts.emails["web"],
    module.service_accounts.emails["api"],
    module.service_accounts.emails["worker"],
    module.service_accounts.emails["migrate"],
  ]
}

module "runtime" {
  source = "../runtime"

  project_id             = var.project_id
  name_prefix            = local.name_prefix
  region                 = var.region
  network_id             = module.network.network_id
  subnet_id              = module.network.subnet_id
  sql_connection_name    = module.database.connection_name
  service_account_emails = module.service_accounts.emails
  labels                 = local.labels

  common_env = {
    RESOLVEFLOW_ENVIRONMENT     = var.environment
    RESOLVEFLOW_REGION          = var.region
    RESOLVEFLOW_DEPLOYMENT_MODE = "client-dedicated"
    # Observe Mode and the absence of a send path are enforced in code; this
    # makes the deployed posture visible in the platform as well.
    RESOLVEFLOW_SENDING_ENABLED = "false"
    # Read-only unless the client authorized drafting. Visible in the platform
    # for the same reason as the line above: the posture should be readable
    # without reading the code.
    RESOLVEFLOW_MAILBOX_SCOPES  = var.mailbox_scope_profile
    DB_INSTANCE_CONNECTION_NAME = module.database.connection_name
    DB_NAME                     = module.database.database_name
    KNOWLEDGE_BUCKET            = module.storage.bucket_names["knowledge"]
    ATTACHMENT_BUCKET           = module.storage.bucket_names["attachments"]
  }

  services = {
    web = {
      description         = "Next.js application and BFF. The only browser-reachable service."
      service_account_key = "web"
      ingress             = "INGRESS_TRAFFIC_ALL"
      private_tier        = false
      needs_database      = true
      health_path         = "/api/health"
      cpu                 = var.web_cpu
      memory              = var.web_memory
      min_instances       = var.web_min_instances
      max_instances       = var.web_max_instances
      deletion_protection = var.service_deletion_protection
      env = {
        DB_IAM_USER = module.service_accounts.emails["web"]
        # Not a secret: an OAuth client id is public by design. It is still
        # client-specific, so it is a variable rather than a literal.
        GOOGLE_OAUTH_CLIENT_ID = var.google_oauth_client_id
        # AUTH_URL is deliberately unset. Auth.js derives the origin from the
        # request (trustHost), and an unset value makes the session cookie
        # __Secure- prefixed, which is correct on Cloud Run.
      }
      secret_env = {
        AUTH_SECRET                = module.secrets.secret_ids["auth-session-secret"]
        AUDIT_IP_SALT              = module.secrets.secret_ids["audit-ip-salt"]
        GOOGLE_OAUTH_CLIENT_SECRET = module.secrets.secret_ids["google-oauth-client-secret"]
        INTERNAL_SERVICE_TOKEN     = module.secrets.secret_ids["internal-service-token"]
      }
      # Gate: staging stays unreachable until the client names its testers.
      invoker_members = var.web_invoker_members
    }

    api = {
      description         = "Private FastAPI triage, retrieval, and MCP boundary."
      service_account_key = "api"
      needs_database      = true
      health_path         = "/healthz"
      cpu                 = var.api_cpu
      memory              = var.api_memory
      min_instances       = var.api_min_instances
      max_instances       = var.api_max_instances
      request_timeout     = "600s"
      concurrency         = 20
      deletion_protection = var.service_deletion_protection
      env = {
        DB_IAM_USER = module.service_accounts.emails["api"]
        # Gmail connection (C06). The client id and redirect are public by
        # design; the matching secret is injected from Secret Manager below.
        GMAIL_OAUTH_CLIENT_ID    = var.gmail_oauth_client_id
        GMAIL_OAUTH_REDIRECT_URI = var.gmail_oauth_redirect_uri
      }
      secret_env = {
        GMAIL_OAUTH_CLIENT_SECRET    = module.secrets.secret_ids["gmail-oauth-client-secret"]
        LLM_PROVIDER_API_KEY         = module.secrets.secret_ids["llm-provider-api-key"]
        INTERNAL_SERVICE_TOKEN       = module.secrets.secret_ids["internal-service-token"]
        MAILBOX_TOKEN_ENCRYPTION_KEY = module.secrets.secret_ids["mailbox-token-encryption-key"]
      }
      invoker_members = [module.service_accounts.members["web"]]
    }

    worker = {
      description         = "Mailbox ingestion, reconciliation, and queued work."
      service_account_key = "worker"
      needs_database      = true
      health_path         = "/healthz"
      cpu                 = var.worker_cpu
      memory              = var.worker_memory
      min_instances       = var.worker_min_instances
      max_instances       = var.worker_max_instances
      request_timeout     = "540s"
      concurrency         = 10
      deletion_protection = var.service_deletion_protection
      env = {
        DB_IAM_USER = module.service_accounts.emails["worker"]
      }
      secret_env = {
        GMAIL_OAUTH_CLIENT_SECRET    = module.secrets.secret_ids["gmail-oauth-client-secret"]
        MAILBOX_TOKEN_ENCRYPTION_KEY = module.secrets.secret_ids["mailbox-token-encryption-key"]
        INTERNAL_SERVICE_TOKEN       = module.secrets.secret_ids["internal-service-token"]
      }
      invoker_members = [
        module.service_accounts.members["pubsub_invoker"],
        module.service_accounts.members["scheduler_invoker"],
      ]
    }
  }

  migration_job = {
    service_account_key = "migrate"
    env = {
      DB_IAM_USER = module.service_accounts.emails["migrate"]
    }
  }
}

module "messaging" {
  source = "../messaging"

  project_id                        = var.project_id
  name_prefix                       = local.name_prefix
  region                            = var.region
  kms_key_id                        = module.kms.key_ids["pubsub"]
  pubsub_service_agent              = local.service_agents.pubsub
  push_invoker_service_account      = module.service_accounts.emails["pubsub_invoker"]
  scheduler_invoker_service_account = module.service_accounts.emails["scheduler_invoker"]
  scheduler_time_zone               = var.scheduler_time_zone
  labels                            = local.labels

  push_endpoints = {
    worker = module.runtime.service_urls["worker"]
  }

  topics = {
    gmail-notifications = {
      dead_letter = true
      publishers  = [local.gmail_push_member]
    }
    ticket-events = {
      dead_letter = true
    }
  }

  subscriptions = {
    gmail-notifications-worker = {
      topic                 = "gmail-notifications"
      push_endpoint_key     = "worker"
      push_path             = "/internal/gmail/notifications"
      ack_deadline_seconds  = 60
      max_delivery_attempts = 10
    }
    ticket-events-worker = {
      topic                 = "ticket-events"
      push_endpoint_key     = "worker"
      push_path             = "/internal/tickets/events"
      ack_deadline_seconds  = 120
      max_delivery_attempts = 10
    }
  }

  task_queues = {
    provider-drafts = {
      # Human-approved provider drafts only. Deliberately slow: a burst here
      # would be a bug, not load.
      max_dispatches_per_second = 2
      max_concurrent_dispatches = 5
      max_attempts              = 3
    }
    knowledge-indexing = {
      max_dispatches_per_second = 10
      max_concurrent_dispatches = 20
      max_attempts              = 5
    }
  }

  scheduler_jobs = {
    gmail-watch-renewal = {
      description  = "Renew Gmail watches before their seven-day expiry."
      schedule     = "0 */6 * * *"
      endpoint_key = "worker"
      path         = "/internal/gmail/renew-watches"
    }
    ingestion-reconciliation = {
      description  = "Reconcile Gmail history cursors and recover missed events."
      schedule     = "*/15 * * * *"
      endpoint_key = "worker"
      path         = "/internal/gmail/reconcile"
    }
    retention-sweep = {
      description  = "Apply retention and deletion policy to expired records."
      schedule     = "30 3 * * *"
      endpoint_key = "worker"
      path         = "/internal/retention/sweep"
    }
    usage-rollup = {
      description  = "Aggregate provider usage and internal budget counters."
      schedule     = "0 1 * * *"
      endpoint_key = "worker"
      path         = "/internal/usage/rollup"
    }
  }
}

module "observability" {
  source = "../observability"

  project_id            = var.project_id
  project_number        = local.project_number
  environment           = var.environment
  name_prefix           = local.name_prefix
  alert_email_addresses = var.alert_email_addresses
  log_archive_bucket    = module.storage.bucket_names["logs"]
  thresholds            = var.alert_thresholds

  uptime_check_host = var.uptime_check_host

  billing_account_id        = var.billing_account_id
  monthly_budget_amount     = var.monthly_budget_amount
  budget_currency           = var.budget_currency
  budget_threshold_percents = var.budget_threshold_percents
}

module "cicd" {
  source = "../cicd"

  project_id        = var.project_id
  environment       = var.environment
  name_prefix       = local.name_prefix
  github_repository = var.github_repository

  ci_accounts = {
    deploy = {
      display_name = "ResolveFlow CI deploy (${var.environment})"
      description  = "Pushes images and rolls Cloud Run revisions. No database or secret read access."
      project_roles = [
        "roles/run.developer",
        "roles/cloudsql.client",
      ]
      principal_attribute = var.ci_deploy_principal.attribute
      principal_value     = var.ci_deploy_principal.value
    }
    infra = {
      display_name = "ResolveFlow CI Terraform (${var.environment})"
      description  = "Runs Terraform plan and apply from a reviewed, protected workflow."
      # Deliberately broad. Constrained by the protected GitHub environment in
      # `ci_infra_principal`, by required review, and by an audited state bucket.
      project_roles = [
        "roles/editor",
        "roles/iam.securityAdmin",
      ]
      principal_attribute = var.ci_infra_principal.attribute
      principal_value     = var.ci_infra_principal.value
    }
  }

  deploy_account_key          = "deploy"
  runtime_service_account_ids = module.service_accounts.ids

  depends_on = [module.project_services]
}
