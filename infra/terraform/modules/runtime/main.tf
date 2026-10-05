# Cloud Run services and the migration job.
#
# Ingress is per service. Only the web tier may accept traffic from outside the
# VPC; the Python API and the ingestion worker are INTERNAL_ONLY and are reached
# through direct VPC egress from the web tier, from Pub/Sub push, or from Cloud
# Scheduler - always with an OIDC token.
#
# Container images are deployed by CI, not by Terraform. Each service is created
# with a placeholder image and then ignores image drift, so `terraform apply`
# can never silently roll a running service back to an older revision.

resource "google_cloud_run_v2_service" "services" {
  for_each = var.services

  project     = var.project_id
  name        = "${var.name_prefix}-${each.key}"
  location    = var.region
  description = each.value.description
  labels      = var.labels

  ingress             = each.value.ingress
  deletion_protection = each.value.deletion_protection

  template {
    service_account                  = var.service_account_emails[each.value.service_account_key]
    timeout                          = each.value.request_timeout
    max_instance_request_concurrency = each.value.concurrency
    execution_environment            = "EXECUTION_ENVIRONMENT_GEN2"

    scaling {
      min_instance_count = each.value.min_instances
      max_instance_count = each.value.max_instances
    }

    # All outbound traffic leaves through the environment VPC and Cloud NAT,
    # so provider and Gmail egress has a stable, auditable path.
    vpc_access {
      egress = "ALL_TRAFFIC"
      network_interfaces {
        network    = var.network_id
        subnetwork = var.subnet_id
      }
    }

    dynamic "volumes" {
      for_each = each.value.needs_database ? [1] : []
      content {
        name = "cloudsql"
        cloud_sql_instance {
          instances = [var.sql_connection_name]
        }
      }
    }

    containers {
      image = each.value.image

      dynamic "ports" {
        for_each = each.value.container_port == null ? [] : [each.value.container_port]
        content {
          name           = "http1"
          container_port = ports.value
        }
      }

      resources {
        limits = {
          cpu    = each.value.cpu
          memory = each.value.memory
        }
        cpu_idle          = each.value.min_instances == 0
        startup_cpu_boost = true
      }

      dynamic "env" {
        for_each = merge(var.common_env, each.value.env)
        content {
          name  = env.key
          value = env.value
        }
      }

      # Secret payloads are resolved by the platform at start-up. No secret
      # value appears in this configuration, in plan output, or in state.
      dynamic "env" {
        for_each = each.value.secret_env
        content {
          name = env.key
          value_source {
            secret_key_ref {
              secret  = env.value
              version = "latest"
            }
          }
        }
      }

      dynamic "volume_mounts" {
        for_each = each.value.needs_database ? [1] : []
        content {
          name       = "cloudsql"
          mount_path = "/cloudsql"
        }
      }

      dynamic "startup_probe" {
        for_each = each.value.health_path == null ? [] : [each.value.health_path]
        content {
          initial_delay_seconds = 5
          timeout_seconds       = 5
          period_seconds        = 10
          failure_threshold     = 6
          http_get {
            path = startup_probe.value
          }
        }
      }

      dynamic "liveness_probe" {
        for_each = each.value.health_path == null ? [] : [each.value.health_path]
        content {
          timeout_seconds   = 5
          period_seconds    = 60
          failure_threshold = 3
          http_get {
            path = liveness_probe.value
          }
        }
      }
    }
  }

  traffic {
    type    = "TRAFFIC_TARGET_ALLOCATION_TYPE_LATEST"
    percent = 100
  }

  lifecycle {
    ignore_changes = [
      # Owned by the deployment pipeline.
      template[0].containers[0].image,
      client,
      client_version,
    ]
  }
}

locals {
  invoker_bindings = merge([
    for service_key, service in var.services : {
      for member in service.invoker_members :
      "${service_key}:${member}" => {
        service_key = service_key
        member      = member
      }
    }
  ]...)
}

# Invoke rights are explicit. A service with no binding is reachable by nobody,
# which is the intended state for a private service before its caller exists.
resource "google_cloud_run_v2_service_iam_member" "invokers" {
  for_each = local.invoker_bindings

  project  = var.project_id
  location = var.region
  name     = google_cloud_run_v2_service.services[each.value.service_key].name
  role     = "roles/run.invoker"
  member   = each.value.member
}

# Database migrations run as a job from CI, never on container start-up.
# A service that migrates on boot races itself across revisions.
resource "google_cloud_run_v2_job" "migrate" {
  count = var.migration_job == null ? 0 : 1

  project             = var.project_id
  name                = "${var.name_prefix}-migrate"
  location            = var.region
  labels              = var.labels
  deletion_protection = false

  template {
    task_count  = 1
    parallelism = 1

    template {
      service_account = var.service_account_emails[var.migration_job.service_account_key]
      max_retries     = 0
      timeout         = var.migration_job.timeout

      vpc_access {
        egress = "ALL_TRAFFIC"
        network_interfaces {
          network    = var.network_id
          subnetwork = var.subnet_id
        }
      }

      volumes {
        name = "cloudsql"
        cloud_sql_instance {
          instances = [var.sql_connection_name]
        }
      }

      containers {
        image   = var.migration_job.image
        command = var.migration_job.command
        args    = var.migration_job.args

        resources {
          limits = {
            cpu    = "1"
            memory = "1Gi"
          }
        }

        dynamic "env" {
          for_each = merge(var.common_env, var.migration_job.env)
          content {
            name  = env.key
            value = env.value
          }
        }

        volume_mounts {
          name       = "cloudsql"
          mount_path = "/cloudsql"
        }
      }
    }
  }

  lifecycle {
    ignore_changes = [
      template[0].template[0].containers[0].image,
      client,
      client_version,
    ]
  }
}
