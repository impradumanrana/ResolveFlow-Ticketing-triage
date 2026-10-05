# Logging, alerting, and cost control.
#
# Alerts are deliberately few and specific. A page that fires for everything is
# ignored, so the policies here cover the failures that silently break the
# product: mailbox events stopping, dead letters accumulating, the database
# filling, the request path erroring, and spend running away.

resource "google_monitoring_notification_channel" "email" {
  for_each = toset(var.alert_email_addresses)

  project      = var.project_id
  display_name = "ResolveFlow ${var.environment} - ${each.value}"
  type         = "email"

  labels = {
    email_address = each.value
  }
}

locals {
  channels = [for channel in google_monitoring_notification_channel.email : channel.id]
}

# Long-term log retention lives in a bucket the client owns and can lock.
resource "google_logging_project_sink" "archive" {
  project     = var.project_id
  name        = "${var.name_prefix}-log-archive"
  destination = "storage.googleapis.com/${var.log_archive_bucket}"

  # Data access logs and request logs only. Debug noise is not archived.
  filter = join(" OR ", [
    "logName:\"cloudaudit.googleapis.com\"",
    "resource.type=\"cloud_run_revision\" AND severity>=WARNING",
    "resource.type=\"cloudsql_database\" AND severity>=WARNING",
  ])

  unique_writer_identity = true
}

resource "google_storage_bucket_iam_member" "sink_writer" {
  bucket = var.log_archive_bucket
  role   = "roles/storage.objectCreator"
  member = google_logging_project_sink.archive.writer_identity
}

# Counts application-level triage failures so an alert can fire on a rate,
# not on a single noisy line.
resource "google_logging_metric" "triage_failures" {
  project = var.project_id
  name    = "${var.name_prefix}-triage-failures"
  filter  = "resource.type=\"cloud_run_revision\" AND jsonPayload.event=\"triage_failed\""

  metric_descriptor {
    metric_kind = "DELTA"
    value_type  = "INT64"
    unit        = "1"
  }
}

resource "google_monitoring_alert_policy" "cloud_run_errors" {
  project      = var.project_id
  display_name = "ResolveFlow ${var.environment} - Cloud Run 5xx rate"
  combiner     = "OR"
  severity     = "ERROR"

  documentation {
    content   = "Server errors on a ResolveFlow Cloud Run service. Check the revision logs and consider rolling back to the previous revision. Runbook: infra/docs/RUNBOOK.md"
    mime_type = "text/markdown"
  }

  conditions {
    display_name = "5xx responses above threshold"
    condition_threshold {
      filter = join(" AND ", [
        "resource.type=\"cloud_run_revision\"",
        "metric.type=\"run.googleapis.com/request_count\"",
        "metric.label.\"response_code_class\"=\"5xx\"",
      ])
      comparison      = "COMPARISON_GT"
      threshold_value = var.thresholds.http_5xx_per_minute
      duration        = "300s"

      aggregations {
        alignment_period     = "60s"
        per_series_aligner   = "ALIGN_RATE"
        cross_series_reducer = "REDUCE_SUM"
        group_by_fields      = ["resource.label.service_name"]
      }
    }
  }

  notification_channels = local.channels
  alert_strategy {
    auto_close = "3600s"
  }
}

# A Gmail event that stops being acknowledged is invisible in the UI: the inbox
# just looks quiet. This is the alert that catches a stalled ingestion path.
resource "google_monitoring_alert_policy" "subscription_backlog" {
  project      = var.project_id
  display_name = "ResolveFlow ${var.environment} - Pub/Sub backlog age"
  combiner     = "OR"
  severity     = "WARNING"

  documentation {
    content   = "Mailbox or ticket events are not being acknowledged. Check worker health, the dead-letter topic, and Gmail watch expiry. Runbook: infra/docs/RUNBOOK.md"
    mime_type = "text/markdown"
  }

  conditions {
    display_name = "Oldest unacknowledged message is too old"
    condition_threshold {
      filter = join(" AND ", [
        "resource.type=\"pubsub_subscription\"",
        "metric.type=\"pubsub.googleapis.com/subscription/oldest_unacked_message_age\"",
      ])
      comparison      = "COMPARISON_GT"
      threshold_value = var.thresholds.unacked_message_age_seconds
      duration        = "300s"

      aggregations {
        alignment_period     = "60s"
        per_series_aligner   = "ALIGN_MAX"
        cross_series_reducer = "REDUCE_MAX"
        group_by_fields      = ["resource.label.subscription_id"]
      }
    }
  }

  notification_channels = local.channels
  alert_strategy {
    auto_close = "86400s"
  }
}

resource "google_monitoring_alert_policy" "dead_letters" {
  project      = var.project_id
  display_name = "ResolveFlow ${var.environment} - dead-lettered messages"
  combiner     = "OR"
  severity     = "ERROR"

  documentation {
    content   = "Messages exhausted their retries and were dead-lettered. Each one is a mailbox event the product did not process. Inspect the hold subscription and replay after fixing the cause. Runbook: infra/docs/RUNBOOK.md"
    mime_type = "text/markdown"
  }

  conditions {
    display_name = "Dead-letter topic received messages"
    condition_threshold {
      filter = join(" AND ", [
        "resource.type=\"pubsub_topic\"",
        "metric.type=\"pubsub.googleapis.com/topic/send_message_operation_count\"",
        "resource.label.\"topic_id\"=monitoring.regex.full_match(\".*-dlq\")",
      ])
      comparison      = "COMPARISON_GT"
      threshold_value = 0
      duration        = "60s"

      aggregations {
        alignment_period     = "60s"
        per_series_aligner   = "ALIGN_SUM"
        cross_series_reducer = "REDUCE_SUM"
      }
    }
  }

  notification_channels = local.channels
  alert_strategy {
    auto_close = "86400s"
  }
}

resource "google_monitoring_alert_policy" "database_disk" {
  project      = var.project_id
  display_name = "ResolveFlow ${var.environment} - Cloud SQL disk utilisation"
  combiner     = "OR"
  severity     = "WARNING"

  documentation {
    content   = "Cloud SQL storage is filling. Autoresize has a ceiling; a full disk stops writes and ingestion. Runbook: infra/docs/RUNBOOK.md"
    mime_type = "text/markdown"
  }

  conditions {
    display_name = "Disk utilisation above threshold"
    condition_threshold {
      filter = join(" AND ", [
        "resource.type=\"cloudsql_database\"",
        "metric.type=\"cloudsql.googleapis.com/database/disk/utilization\"",
      ])
      comparison      = "COMPARISON_GT"
      threshold_value = var.thresholds.database_disk_utilization
      duration        = "600s"

      aggregations {
        alignment_period   = "300s"
        per_series_aligner = "ALIGN_MEAN"
      }
    }
  }

  notification_channels = local.channels
  alert_strategy {
    auto_close = "86400s"
  }
}

resource "google_monitoring_alert_policy" "triage_failures" {
  project      = var.project_id
  display_name = "ResolveFlow ${var.environment} - triage failures"
  combiner     = "OR"
  severity     = "WARNING"

  documentation {
    content   = "Triage is failing closed to human review more often than expected. Check provider health, MCP availability, and retrieval. Runbook: infra/docs/RUNBOOK.md"
    mime_type = "text/markdown"
  }

  conditions {
    display_name = "Triage failure rate above threshold"
    condition_threshold {
      filter          = "resource.type=\"cloud_run_revision\" AND metric.type=\"logging.googleapis.com/user/${google_logging_metric.triage_failures.name}\""
      comparison      = "COMPARISON_GT"
      threshold_value = var.thresholds.triage_failures_per_minute
      duration        = "600s"

      aggregations {
        alignment_period   = "60s"
        per_series_aligner = "ALIGN_RATE"
      }
    }
  }

  notification_channels = local.channels
  alert_strategy {
    auto_close = "86400s"
  }
}

resource "google_monitoring_uptime_check_config" "web" {
  count = var.uptime_check_host == null ? 0 : 1

  project      = var.project_id
  display_name = "ResolveFlow ${var.environment} - web health"
  timeout      = "10s"
  period       = "300s"

  http_check {
    path         = var.uptime_check_path
    port         = 443
    use_ssl      = true
    validate_ssl = true
  }

  monitored_resource {
    type = "uptime_url"
    labels = {
      project_id = var.project_id
      host       = var.uptime_check_host
    }
  }
}

# Cost control. The budget notifies; it does not cap spend. Capping is a client
# billing decision and would take the product down rather than degrade it.
resource "google_billing_budget" "environment" {
  count = var.billing_account_id == null ? 0 : 1

  billing_account = var.billing_account_id
  display_name    = "ResolveFlow ${var.environment}"

  budget_filter {
    projects               = ["projects/${var.project_number}"]
    calendar_period        = "MONTH"
    credit_types_treatment = "INCLUDE_ALL_CREDITS"
  }

  amount {
    specified_amount {
      currency_code = var.budget_currency
      units         = tostring(var.monthly_budget_amount)
    }
  }

  dynamic "threshold_rules" {
    for_each = var.budget_threshold_percents
    content {
      threshold_percent = threshold_rules.value
      spend_basis       = "CURRENT_SPEND"
    }
  }

  threshold_rules {
    threshold_percent = 1.0
    spend_basis       = "FORECASTED_SPEND"
  }

  all_updates_rule {
    monitoring_notification_channels = local.channels
    disable_default_iam_recipients   = true
  }
}
