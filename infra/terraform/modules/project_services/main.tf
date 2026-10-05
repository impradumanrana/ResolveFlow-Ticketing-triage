# Enables exactly the Google APIs this deployment uses.
# Services are never disabled on destroy: disabling an API in a shared client
# project can break resources this stack does not own.

locals {
  required_services = [
    "artifactregistry.googleapis.com",
    "cloudbuild.googleapis.com",
    "cloudkms.googleapis.com",
    "cloudresourcemanager.googleapis.com",
    "cloudscheduler.googleapis.com",
    "cloudtasks.googleapis.com",
    "cloudtrace.googleapis.com",
    "clouderrorreporting.googleapis.com",
    "compute.googleapis.com",
    "iam.googleapis.com",
    "iamcredentials.googleapis.com",
    "logging.googleapis.com",
    "monitoring.googleapis.com",
    "pubsub.googleapis.com",
    "run.googleapis.com",
    "secretmanager.googleapis.com",
    "servicenetworking.googleapis.com",
    "sqladmin.googleapis.com",
    "storage.googleapis.com",
    "sts.googleapis.com",
    "vpcaccess.googleapis.com",
  ]

  billing_services = var.enable_billing_budget ? ["billingbudgets.googleapis.com"] : []
}

resource "google_project_service" "required" {
  for_each = toset(concat(local.required_services, local.billing_services))

  project = var.project_id
  service = each.value

  disable_on_destroy         = false
  disable_dependent_services = false
}
