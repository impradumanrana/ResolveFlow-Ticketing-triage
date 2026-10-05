provider "google" {
  project = var.project_id
  region  = var.region

  # Terraform runs as the federated CI identity or as an operator impersonating
  # it. No service account key file is ever referenced.
  impersonate_service_account = var.impersonate_service_account
}

module "environment" {
  source = "../../modules/environment"

  environment = "production"

  project_id         = var.project_id
  region             = var.region
  github_repository  = var.github_repository
  billing_account_id = var.billing_account_id

  alert_email_addresses    = var.alert_email_addresses
  uptime_check_host        = var.uptime_check_host
  google_oauth_client_id   = var.google_oauth_client_id
  gmail_oauth_client_id    = var.gmail_oauth_client_id
  gmail_oauth_redirect_uri = var.gmail_oauth_redirect_uri

  # Production serves the client's staff over the public internet. Workspace
  # login, invited membership, and RBAC are enforced by the application (C03),
  # not by Cloud Run IAM, so this is set to allUsers only at launch approval.
  web_invoker_members = var.web_invoker_members

  subnet_cidr                 = "10.70.0.0/20"
  private_service_access_cidr = "10.71.0.0"

  ci_deploy_principal = {
    attribute = "environment"
    value     = "production-deploy"
  }

  ci_infra_principal = {
    attribute = "environment"
    value     = "production-infra"
  }

  # Regional availability, a longer recovery window, and protection against
  # accidental deletion of the system of record.
  database_tier                = "db-custom-2-7680"
  database_availability_type   = "REGIONAL"
  database_disk_size_gb        = 50
  database_retained_backups    = 30
  database_pitr_days           = 7
  database_deletion_protection = true
  database_max_connections     = 200

  # A warm instance on the interactive path keeps the p95 read target
  # achievable without a cold start.
  web_min_instances    = 1
  web_max_instances    = 10
  api_min_instances    = 1
  api_max_instances    = 8
  worker_min_instances = 1
  worker_max_instances = 12

  service_deletion_protection = true
  kms_protection_level        = var.kms_protection_level

  attachment_retention_days = var.attachment_retention_days
  export_retention_days     = var.export_retention_days
  log_retention_days        = var.log_retention_days

  alert_thresholds = {
    http_5xx_per_minute         = 1
    unacked_message_age_seconds = 900
    database_disk_utilization   = 0.85
    triage_failures_per_minute  = 1
  }

  monthly_budget_amount     = var.monthly_budget_amount
  budget_currency           = var.budget_currency
  budget_threshold_percents = [0.5, 0.8, 0.95]

  scheduler_time_zone = var.scheduler_time_zone
}
