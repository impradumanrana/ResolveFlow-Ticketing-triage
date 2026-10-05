output "notification_channel_ids" {
  description = "Monitoring notification channels created for this environment."
  value       = [for channel in google_monitoring_notification_channel.email : channel.id]
}

output "log_sink_writer_identity" {
  description = "Service account the log sink writes as."
  value       = google_logging_project_sink.archive.writer_identity
}

output "alert_policy_names" {
  description = "Alert policies defined for this environment."
  value = [
    google_monitoring_alert_policy.cloud_run_errors.display_name,
    google_monitoring_alert_policy.subscription_backlog.display_name,
    google_monitoring_alert_policy.dead_letters.display_name,
    google_monitoring_alert_policy.database_disk.display_name,
    google_monitoring_alert_policy.triage_failures.display_name,
  ]
}
