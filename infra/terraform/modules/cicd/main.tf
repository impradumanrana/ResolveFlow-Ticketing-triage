# Keyless CI access through Workload Identity Federation.
#
# No service account key is created, downloaded, or stored in GitHub. CI
# exchanges its short-lived GitHub OIDC token for a short-lived Google access
# token, and the exchange is constrained to one repository by an attribute
# condition evaluated before any token is issued.
#
# Two identities, deliberately separated:
#   deploy - pushes images and rolls Cloud Run revisions.
#   infra  - runs Terraform. Only a protected, reviewed workflow may assume it.

resource "google_iam_workload_identity_pool" "github" {
  project                   = var.project_id
  workload_identity_pool_id = "${var.name_prefix}-github"
  display_name              = "GitHub Actions"
  description               = "Federated CI identity for ResolveFlow ${var.environment}."
}

resource "google_iam_workload_identity_pool_provider" "github" {
  project                            = var.project_id
  workload_identity_pool_id          = google_iam_workload_identity_pool.github.workload_identity_pool_id
  workload_identity_pool_provider_id = "github-oidc"
  display_name                       = "GitHub OIDC"

  attribute_mapping = {
    "google.subject"        = "assertion.sub"
    "attribute.repository"  = "assertion.repository"
    "attribute.ref"         = "assertion.ref"
    "attribute.environment" = "assertion.environment"
  }

  # Without this condition any GitHub repository in the world could request a
  # token from this provider.
  attribute_condition = "assertion.repository == \"${var.github_repository}\""

  oidc {
    issuer_uri = "https://token.actions.githubusercontent.com"
  }
}

resource "google_service_account" "ci" {
  for_each = var.ci_accounts

  project      = var.project_id
  account_id   = "${var.name_prefix}-ci-${each.key}"
  display_name = each.value.display_name
  description  = each.value.description
}

locals {
  ci_role_bindings = merge([
    for account_key, account in var.ci_accounts : {
      for role in account.project_roles :
      "${account_key}:${role}" => {
        account_key = account_key
        role        = role
      }
    }
  ]...)
}

resource "google_project_iam_member" "ci" {
  for_each = local.ci_role_bindings

  project = var.project_id
  role    = each.value.role
  member  = "serviceAccount:${google_service_account.ci[each.value.account_key].email}"
}

# Which federated principals may impersonate which CI identity. The subject is
# narrowed further than the provider condition: production deploys are bound to
# a named GitHub environment that requires manual approval.
resource "google_service_account_iam_member" "workload_identity_user" {
  for_each = var.ci_accounts

  service_account_id = google_service_account.ci[each.key].name
  role               = "roles/iam.workloadIdentityUser"
  member             = "principalSet://iam.googleapis.com/${google_iam_workload_identity_pool.github.name}/attribute.${each.value.principal_attribute}/${each.value.principal_value}"
}

# Deploying a Cloud Run revision means acting as that revision's runtime
# identity. Granted per runtime service account, never project-wide.
resource "google_service_account_iam_member" "act_as_runtime" {
  for_each = var.runtime_service_account_ids

  service_account_id = each.value
  role               = "roles/iam.serviceAccountUser"
  member             = "serviceAccount:${google_service_account.ci[var.deploy_account_key].email}"
}
