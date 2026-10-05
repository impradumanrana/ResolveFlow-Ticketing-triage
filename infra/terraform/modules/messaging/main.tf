# Asynchronous work: Pub/Sub for mailbox events, Cloud Tasks for controlled
# outbound work, Cloud Scheduler for reconciliation and watch renewal.
#
# Nothing here calls a provider directly. Every push subscription and every
# scheduled job authenticates to a private Cloud Run service with an OIDC token
# minted for a dedicated invoker identity, so an unauthenticated request to the
# worker is rejected at the platform edge before the application sees it.

locals {
  dead_letter_topics = { for key, topic in var.topics : key => topic if topic.dead_letter }
}

resource "google_pubsub_topic" "main" {
  for_each = var.topics

  project      = var.project_id
  name         = "${var.name_prefix}-${each.key}"
  labels       = var.labels
  kms_key_name = var.kms_key_id

  message_retention_duration = each.value.message_retention

  message_storage_policy {
    allowed_persistence_regions = [var.region]
  }
}

resource "google_pubsub_topic" "dead_letter" {
  for_each = local.dead_letter_topics

  project      = var.project_id
  name         = "${var.name_prefix}-${each.key}-dlq"
  labels       = merge(var.labels, { role = "dead-letter" })
  kms_key_name = var.kms_key_id

  # Dead letters are held longer than live traffic so an operator has time to
  # inspect and replay them.
  message_retention_duration = var.dead_letter_retention

  message_storage_policy {
    allowed_persistence_regions = [var.region]
  }
}

# Publishers named by the caller. For Gmail this is the Google-owned push
# identity, which is why it is granted on the topic and not project-wide.
locals {
  publisher_bindings = merge([
    for topic_key, topic in var.topics : {
      for member in topic.publishers :
      "${topic_key}:${member}" => {
        topic_key = topic_key
        member    = member
      }
    }
  ]...)
}

resource "google_pubsub_topic_iam_member" "publishers" {
  for_each = local.publisher_bindings

  project = var.project_id
  topic   = google_pubsub_topic.main[each.value.topic_key].name
  role    = "roles/pubsub.publisher"
  member  = each.value.member
}

resource "google_pubsub_subscription" "push" {
  for_each = var.subscriptions

  project = var.project_id
  name    = "${var.name_prefix}-${each.key}"
  topic   = google_pubsub_topic.main[each.value.topic].id
  labels  = var.labels

  ack_deadline_seconds       = each.value.ack_deadline_seconds
  message_retention_duration = each.value.message_retention
  retain_acked_messages      = false
  enable_message_ordering    = each.value.ordered

  # Redelivery is expected. Handlers are idempotent by ticket and message id;
  # see C07 for the ingestion contract this relies on.
  expiration_policy {
    ttl = "" # Never expire. An expired subscription silently drops mailbox events.
  }

  retry_policy {
    minimum_backoff = each.value.minimum_backoff
    maximum_backoff = each.value.maximum_backoff
  }

  dynamic "dead_letter_policy" {
    for_each = var.topics[each.value.topic].dead_letter ? [1] : []
    content {
      dead_letter_topic     = google_pubsub_topic.dead_letter[each.value.topic].id
      max_delivery_attempts = each.value.max_delivery_attempts
    }
  }

  push_config {
    push_endpoint = "${var.push_endpoints[each.value.push_endpoint_key]}${each.value.push_path}"

    oidc_token {
      service_account_email = var.push_invoker_service_account
      audience              = var.push_endpoints[each.value.push_endpoint_key]
    }

    no_wrapper {
      write_metadata = true
    }
  }
}

# The Pub/Sub service agent needs explicit rights to move a message into the
# dead-letter topic and to acknowledge the failed delivery.
resource "google_pubsub_topic_iam_member" "dead_letter_publisher" {
  for_each = local.dead_letter_topics

  project = var.project_id
  topic   = google_pubsub_topic.dead_letter[each.key].name
  role    = "roles/pubsub.publisher"
  member  = "serviceAccount:${var.pubsub_service_agent}"
}

resource "google_pubsub_subscription_iam_member" "dead_letter_subscriber" {
  for_each = { for key, sub in var.subscriptions : key => sub if var.topics[sub.topic].dead_letter }

  project      = var.project_id
  subscription = google_pubsub_subscription.push[each.key].name
  role         = "roles/pubsub.subscriber"
  member       = "serviceAccount:${var.pubsub_service_agent}"
}

# A dead-letter topic with no subscription discards its messages. This one
# holds them for operator replay.
resource "google_pubsub_subscription" "dead_letter_hold" {
  for_each = local.dead_letter_topics

  project = var.project_id
  name    = "${var.name_prefix}-${each.key}-dlq-hold"
  topic   = google_pubsub_topic.dead_letter[each.key].id
  labels  = merge(var.labels, { role = "dead-letter" })

  ack_deadline_seconds       = 600
  message_retention_duration = var.dead_letter_retention
  retain_acked_messages      = false

  expiration_policy {
    ttl = ""
  }
}

resource "google_cloud_tasks_queue" "main" {
  for_each = var.task_queues

  project  = var.project_id
  name     = "${var.name_prefix}-${each.key}"
  location = var.region

  rate_limits {
    max_dispatches_per_second = each.value.max_dispatches_per_second
    max_concurrent_dispatches = each.value.max_concurrent_dispatches
  }

  retry_config {
    max_attempts       = each.value.max_attempts
    min_backoff        = each.value.min_backoff
    max_backoff        = each.value.max_backoff
    max_retry_duration = each.value.max_retry_duration
    max_doublings      = 4
  }

  stackdriver_logging_config {
    sampling_ratio = 1.0
  }
}

resource "google_cloud_scheduler_job" "main" {
  for_each = var.scheduler_jobs

  project     = var.project_id
  region      = var.region
  name        = "${var.name_prefix}-${each.key}"
  description = each.value.description
  schedule    = each.value.schedule
  time_zone   = var.scheduler_time_zone

  attempt_deadline = each.value.attempt_deadline

  retry_config {
    retry_count          = each.value.retry_count
    min_backoff_duration = "10s"
    max_backoff_duration = "600s"
    max_doublings        = 3
  }

  http_target {
    http_method = "POST"
    uri         = "${var.push_endpoints[each.value.endpoint_key]}${each.value.path}"

    headers = {
      "Content-Type" = "application/json"
    }

    oidc_token {
      service_account_email = var.scheduler_invoker_service_account
      audience              = var.push_endpoints[each.value.endpoint_key]
    }
  }
}
