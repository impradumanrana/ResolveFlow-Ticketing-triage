output "topic_ids" {
  description = "Map of topic key to fully qualified topic id."
  value       = { for key, topic in google_pubsub_topic.main : key => topic.id }
}

output "topic_names" {
  description = "Map of topic key to topic name."
  value       = { for key, topic in google_pubsub_topic.main : key => topic.name }
}

output "dead_letter_topic_ids" {
  description = "Map of topic key to its dead-letter topic id."
  value       = { for key, topic in google_pubsub_topic.dead_letter : key => topic.id }
}

output "subscription_names" {
  description = "Map of subscription key to subscription name."
  value       = { for key, sub in google_pubsub_subscription.push : key => sub.name }
}

output "task_queue_ids" {
  description = "Map of queue key to fully qualified queue id."
  value       = { for key, queue in google_cloud_tasks_queue.main : key => queue.id }
}

output "scheduler_job_names" {
  description = "Map of job key to scheduler job name."
  value       = { for key, job in google_cloud_scheduler_job.main : key => job.name }
}
