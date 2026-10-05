provider "google" {
  project = var.project_id
  region  = var.region

  # Terraform runs as the federated CI identity or as an operator impersonating
  # it. No service account key file is ever referenced.
  impersonate_service_account = var.impersonate_service_account
}

module "environment" {
  source = "../../modules/environment"

  environment = "staging"

  project_id         = var.project_id
  region             = var.region
  github_repository  = var.github_repository
  billing_account_id = var.billing_account_id

  alert_email_addresses    = var.alert_email_addresses
  uptime_check_host        = var.uptime_check_host
  google_oauth_client_id   = var.google_oauth_client_id
  gmail_oauth_client_id    = var.gmail_oauth_client_id
  gmail_oauth_redirect_uri = var.gmail_oauth_redirect_uri

  # Staging is not public. Until the client names its test group this list is
  # empty and the service is reachable by nobody, which is the intended
  # fail-closed state for an environment that has no application login yet.
  web_invoker_members = var.web_invoker_members

  subnet_cidr                 = "10.60.0.0/20"
  private_service_access_cidr = "10.61.0.0"

  ci_deploy_principal = {
    attribute = "repository"
    value     = var.github_repository
  }

  # Terraform in staging still requires the protected GitHub environment, so
  # the apply path is identical in shape to production.
  ci_infra_principal = {
    attribute = "environment"
    value     = "staging-infra"
  }

  # Smallest supportable footprint. Staging proves behaviour, not capacity.
  database_tier                = "db-custom-1-3840"
  database_availability_type   = "ZONAL"
  database_disk_size_gb        = 20
  database_retained_backups    = 7
  database_pitr_days           = 3
  database_deletion_protection = true

  web_min_instances    = 0
  web_max_instances    = 2
  api_min_instances    = 0
  api_max_instances    = 2
  worker_min_instances = 0
  worker_max_instances = 2

  service_deletion_protection = false

  attachment_retention_days = 30
  export_retention_days     = 7
  log_retention_days        = 90

  # Staging carries synthetic and client-approved test data only, so alerting
  # is looser than production and exists to prove the path works.
  alert_thresholds = {
    http_5xx_per_minute         = 5
    unacked_message_age_seconds = 1800
    database_disk_utilization   = 0.9
    triage_failures_per_minute  = 5
  }

  monthly_budget_amount     = var.monthly_budget_amount
  budget_currency           = var.budget_currency
  budget_threshold_percents = [0.5, 0.9]

  scheduler_time_zone = var.scheduler_time_zone
}
