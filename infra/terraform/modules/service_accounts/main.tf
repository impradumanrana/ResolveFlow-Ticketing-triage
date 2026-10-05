# Runtime identities.
#
# Every Cloud Run service, job, and event delivery path gets its own service
# account. No identity is shared between the browser-facing tier and the tier
# that holds database, secret, and mailbox access. No service account key is
# ever created: CI authenticates through Workload Identity Federation and
# runtime identities are attached by the platform.

locals {
  # Emitting telemetry is not a privilege worth withholding, so every runtime
  # identity gets the same observability baseline and nothing more.
  telemetry_roles = [
    "roles/logging.logWriter",
    "roles/monitoring.metricWriter",
    "roles/cloudtrace.agent",
    "roles/errorreporting.writeUser",
  ]

  # Flatten per-account extra project roles into unique binding keys.
  extra_role_bindings = merge([
    for account_key, account in var.accounts : {
      for role in account.project_roles :
      "${account_key}:${role}" => {
        account_key = account_key
        role        = role
      }
    }
  ]...)

  telemetry_bindings = merge([
    for account_key, account in var.accounts : {
      for role in local.telemetry_roles :
      "${account_key}:${role}" => {
        account_key = account_key
        role        = role
      }
    }
  ]...)
}

resource "google_service_account" "runtime" {
  for_each = var.accounts

  project      = var.project_id
  account_id   = "${var.name_prefix}-${each.key}"
  display_name = each.value.display_name
  description  = each.value.description
}

resource "google_project_iam_member" "telemetry" {
  for_each = local.telemetry_bindings

  project = var.project_id
  role    = each.value.role
  member  = "serviceAccount:${google_service_account.runtime[each.value.account_key].email}"
}

resource "google_project_iam_member" "extra" {
  for_each = local.extra_role_bindings

  project = var.project_id
  role    = each.value.role
  member  = "serviceAccount:${google_service_account.runtime[each.value.account_key].email}"
}
