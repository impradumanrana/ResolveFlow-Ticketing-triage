"""PostgreSQL ingestion store.

Every write is idempotent by construction, because at-least-once delivery means
the same window will be processed more than once:

* Messages and threads rely on the C04 per-mailbox unique indexes with
  `ON CONFLICT DO NOTHING`, so a redelivered message is a no-op rather than a
  duplicate ticket.
* Job claiming relies on the unique idempotency key, so the same Pub/Sub
  notification queues exactly one job however many times it is delivered.
* Job start is a conditional UPDATE, so two workers cannot run one job.

Thread counters are recomputed from the messages themselves rather than
incremented, so a replay cannot drift them.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import text

from app.ingestion.normalize import AttachmentCandidate, NormalizedMessage
from app.ingestion.records import JobClaim, JobRecord, MailboxSync, MessageUpsert

_MAILBOX_COLUMNS = (
    "id::text, organization_id::text, email_address, status::text, history_cursor, "
    "watch_expires_at, last_synced_at, default_queue_id::text, department_id::text"
)


def _mailbox(row: Any) -> MailboxSync:
    return MailboxSync(
        id=row[0],
        organization_id=row[1],
        email_address=row[2],
        status=row[3],
        history_cursor=row[4],
        watch_expires_at=row[5],
        last_synced_at=row[6],
        default_queue_id=row[7],
        department_id=row[8],
    )


class PostgresIngestionStore:
    def __init__(self, connection: Any):
        self._c = connection

    # --- mailboxes -----------------------------------------------------

    def find_mailbox(self, mailbox_id: str) -> MailboxSync | None:
        row = self._c.execute(
            text(f"SELECT {_MAILBOX_COLUMNS} FROM mailboxes WHERE id = CAST(:id AS uuid)"),
            {"id": mailbox_id},
        ).first()
        return _mailbox(row) if row else None

    def find_mailbox_by_address(self, email_address: str) -> MailboxSync | None:
        row = self._c.execute(
            text(f"SELECT {_MAILBOX_COLUMNS} FROM mailboxes WHERE email_address = lower(:a)"),
            {"a": email_address},
        ).first()
        return _mailbox(row) if row else None

    def connected_mailboxes(self) -> list[MailboxSync]:
        rows = self._c.execute(
            text(f"SELECT {_MAILBOX_COLUMNS} FROM mailboxes WHERE status = 'CONNECTED'")
        ).all()
        return [_mailbox(row) for row in rows]

    def mailboxes_needing_watch(self, before: datetime) -> list[MailboxSync]:
        rows = self._c.execute(
            text(
                f"SELECT {_MAILBOX_COLUMNS} FROM mailboxes "
                "WHERE status = 'CONNECTED' "
                "AND (watch_expires_at IS NULL OR watch_expires_at <= :before) "
                "ORDER BY watch_expires_at NULLS FIRST"
            ),
            {"before": before},
        ).all()
        return [_mailbox(row) for row in rows]

    def set_history_cursor(self, mailbox_id: str, cursor: str, *, at: datetime) -> None:
        # The comparison is in SQL so two concurrent syncs cannot interleave a
        # read and a write and move the cursor backwards.
        self._c.execute(
            text(
                "UPDATE mailboxes SET history_cursor = :cursor, last_synced_at = :at, "
                "updated_at = now() WHERE id = CAST(:id AS uuid) "
                "AND (history_cursor IS NULL OR history_cursor !~ '^[0-9]+$' "
                "     OR CAST(history_cursor AS numeric) < CAST(:cursor AS numeric))"
            ),
            {"id": mailbox_id, "cursor": cursor, "at": at},
        )

    def set_watch(self, mailbox_id: str, *, expires_at: datetime, at: datetime) -> None:
        self._c.execute(
            text(
                "UPDATE mailboxes SET watch_expires_at = :expires, last_error_code = NULL, "
                "last_error_at = NULL, updated_at = now() WHERE id = CAST(:id AS uuid)"
            ),
            {"id": mailbox_id, "expires": expires_at},
        )

    def mark_sync_error(self, mailbox_id: str, *, code: str, at: datetime) -> None:
        self._c.execute(
            text(
                "UPDATE mailboxes SET last_error_code = :code, last_error_at = :at, "
                "updated_at = now() WHERE id = CAST(:id AS uuid)"
            ),
            {"id": mailbox_id, "code": code, "at": at},
        )

    # --- jobs ----------------------------------------------------------

    def claim_job(
        self,
        *,
        organization_id: str,
        job_type: str,
        idempotency_key: str,
        payload: dict[str, Any],
        available_at: datetime,
        max_attempts: int,
    ) -> JobClaim:
        row = self._c.execute(
            text(
                "INSERT INTO jobs (organization_id, job_type, idempotency_key, payload, "
                " available_at, max_attempts) VALUES (CAST(:org AS uuid), :job_type, :key, "
                " CAST(:payload AS jsonb), :available_at, :max_attempts) "
                "ON CONFLICT (organization_id, job_type, idempotency_key) DO NOTHING "
                "RETURNING id::text"
            ),
            {
                "org": organization_id,
                "job_type": job_type,
                "key": idempotency_key,
                "payload": json.dumps(payload, sort_keys=True),
                "available_at": available_at,
                "max_attempts": max_attempts,
            },
        ).first()
        if row is not None:
            return JobClaim(row[0], created=True)

        existing = self._c.execute(
            text(
                "SELECT id::text FROM jobs WHERE organization_id = CAST(:org AS uuid) "
                "AND job_type = :job_type AND idempotency_key = :key"
            ),
            {"org": organization_id, "job_type": job_type, "key": idempotency_key},
        ).scalar_one()
        return JobClaim(existing, created=False)

    def due_jobs(self, *, now: datetime, job_type: str, limit: int) -> list[JobRecord]:
        rows = self._c.execute(
            text(
                "SELECT id::text, organization_id::text, job_type, idempotency_key, status::text, "
                " attempts, max_attempts, payload FROM jobs "
                "WHERE job_type = :job_type AND status = 'QUEUED' AND available_at <= :now "
                "ORDER BY available_at LIMIT :limit"
            ),
            {"job_type": job_type, "now": now, "limit": limit},
        ).all()
        return [
            JobRecord(
                id=row[0],
                organization_id=row[1],
                job_type=row[2],
                idempotency_key=row[3],
                status=row[4],
                attempts=row[5],
                max_attempts=row[6],
                payload=row[7] if isinstance(row[7], dict) else json.loads(row[7] or "{}"),
            )
            for row in rows
        ]

    def start_job(self, job_id: str, *, at: datetime) -> bool:
        # Conditional on still being QUEUED: the claim, not the listing, is what
        # prevents two workers running the same job.
        result = self._c.execute(
            text(
                "UPDATE jobs SET status = 'RUNNING', started_at = :at, updated_at = now() "
                "WHERE id = CAST(:id AS uuid) AND status = 'QUEUED'"
            ),
            {"id": job_id, "at": at},
        )
        return bool(result.rowcount)

    def complete_job(self, job_id: str, *, at: datetime) -> None:
        self._c.execute(
            text(
                "UPDATE jobs SET status = 'SUCCEEDED', completed_at = :at, started_at = NULL, "
                "updated_at = now() WHERE id = CAST(:id AS uuid)"
            ),
            {"id": job_id, "at": at},
        )

    def fail_job(self, job_id: str, *, error: str, retry_at: datetime, at: datetime) -> str:
        status = self._c.execute(
            text(
                "UPDATE jobs SET attempts = attempts + 1, last_error = :error, started_at = NULL, "
                " status = CASE WHEN attempts + 1 >= max_attempts THEN 'DEAD_LETTERED'::job_status "
                "               ELSE 'QUEUED'::job_status END, "
                " dead_lettered_at = CASE WHEN attempts + 1 >= max_attempts THEN :at END, "
                " available_at = CASE WHEN attempts + 1 >= max_attempts THEN available_at "
                "                     ELSE :retry_at END, "
                " updated_at = now() "
                "WHERE id = CAST(:id AS uuid) RETURNING status::text"
            ),
            {"id": job_id, "error": error[:2000], "retry_at": retry_at, "at": at},
        ).scalar_one()
        return str(status)

    def reclaim_stalled_jobs(self, *, now: datetime, lease_seconds: int) -> list[str]:
        """Return jobs a crashed worker left RUNNING to the queue."""
        rows = self._c.execute(
            text(
                "UPDATE jobs SET status = 'QUEUED', started_at = NULL, available_at = :now, "
                " updated_at = now() "
                "WHERE status = 'RUNNING' AND started_at IS NOT NULL AND started_at <= :cutoff "
                "RETURNING id::text"
            ),
            {"now": now, "cutoff": now - timedelta(seconds=lease_seconds)},
        ).all()
        return [row[0] for row in rows]

    # --- conversation --------------------------------------------------

    def upsert_message(
        self, mailbox: MailboxSync, message: NormalizedMessage, *, at: datetime
    ) -> MessageUpsert:
        thread_id = self._c.execute(
            text(
                "INSERT INTO threads (organization_id, mailbox_id, provider_thread_id, subject, "
                " first_message_at, last_message_at) VALUES (CAST(:org AS uuid), "
                " CAST(:mailbox AS uuid), :provider_thread_id, :subject, :sent_at, :sent_at) "
                "ON CONFLICT (mailbox_id, provider_thread_id) DO UPDATE "
                " SET subject = coalesce(threads.subject, EXCLUDED.subject), updated_at = now() "
                "RETURNING id::text"
            ),
            {
                "org": mailbox.organization_id,
                "mailbox": mailbox.id,
                "provider_thread_id": message.provider_thread_id,
                "subject": message.subject,
                "sent_at": message.sent_at,
            },
        ).scalar_one()

        inserted = self._c.execute(
            text(
                "INSERT INTO messages (organization_id, mailbox_id, thread_id, "
                "provider_message_id, "
                " rfc822_message_id, direction, from_address, to_addresses, cc_addresses, subject, "
                " body_text, snippet, has_attachments, sent_at, received_at) "
                "VALUES (CAST(:org AS uuid), CAST(:mailbox AS uuid), CAST(:thread AS uuid), "
                " :provider_message_id, :rfc822, CAST(:direction AS message_direction), "
                ":from_address, "
                " :to_addresses, :cc_addresses, :subject, :body_text, :snippet, :has_attachments, "
                " :sent_at, :at) "
                "ON CONFLICT (mailbox_id, provider_message_id) DO NOTHING RETURNING id::text"
            ),
            {
                "org": mailbox.organization_id,
                "mailbox": mailbox.id,
                "thread": thread_id,
                "provider_message_id": message.provider_message_id,
                "rfc822": message.rfc822_message_id,
                "direction": message.direction,
                "from_address": message.from_address,
                "to_addresses": list(message.to_addresses),
                "cc_addresses": list(message.cc_addresses),
                "subject": message.subject,
                "body_text": message.body_text,
                "snippet": message.snippet,
                "has_attachments": message.has_attachments,
                "sent_at": message.sent_at,
                "at": at,
            },
        ).first()

        created = inserted is not None
        message_id = (
            inserted[0]
            if created
            else self._c.execute(
                text(
                    "SELECT id::text FROM messages WHERE mailbox_id = CAST(:mailbox AS uuid) "
                    "AND provider_message_id = :provider_message_id"
                ),
                {"mailbox": mailbox.id, "provider_message_id": message.provider_message_id},
            ).scalar_one()
        )

        if created:
            # Recomputed, not incremented: a replay must not inflate counters.
            self._c.execute(
                text(
                    "UPDATE threads SET message_count = counts.total, "
                    " first_message_at = counts.first_at, last_message_at = counts.last_at, "
                    " updated_at = now() "
                    "FROM (SELECT count(*) AS total, min(sent_at) AS first_at, max(sent_at) AS "
                    "last_at "
                    "      FROM messages WHERE thread_id = CAST(:thread AS uuid) "
                    "      AND deleted_at IS NULL) AS counts "
                    "WHERE threads.id = CAST(:thread AS uuid)"
                ),
                {"thread": thread_id},
            )

        ticket_id = self._c.execute(
            text(
                "INSERT INTO tickets (organization_id, mailbox_id, thread_id, queue_id, "
                " department_id, subject, customer_address) VALUES (CAST(:org AS uuid), "
                " CAST(:mailbox AS uuid), CAST(:thread AS uuid), CAST(:queue AS uuid), "
                " CAST(:department AS uuid), :subject, :customer) "
                "ON CONFLICT (thread_id) DO UPDATE SET updated_at = now() RETURNING id::text"
            ),
            {
                "org": mailbox.organization_id,
                "mailbox": mailbox.id,
                "thread": thread_id,
                "queue": mailbox.default_queue_id,
                "department": mailbox.department_id,
                "subject": message.subject,
                "customer": message.from_address if message.direction == "INBOUND" else None,
            },
        ).scalar_one()

        if created and message.direction == "INBOUND":
            # A customer reply reopens a finished ticket. Richer routing rules
            # are C09's; this is the minimum that stops a reply disappearing.
            self._c.execute(
                text(
                    "UPDATE tickets SET status = 'NEW', version = version + 1, updated_at = now() "
                    "WHERE id = CAST(:id AS uuid) AND status IN ('RESOLVED', 'CLOSED')"
                ),
                {"id": ticket_id},
            )

        return MessageUpsert(message_id, thread_id, ticket_id, created=created)

    def save_attachments(
        self, mailbox: MailboxSync, message_id: str, attachments: tuple[AttachmentCandidate, ...]
    ) -> int:
        saved = 0
        for attachment in attachments:
            self._c.execute(
                text(
                    "INSERT INTO attachments (organization_id, message_id, provider_attachment_id, "
                    " filename, content_type, size_bytes, scan_state) VALUES (CAST(:org AS uuid), "
                    " CAST(:message AS uuid), :provider_id, :filename, :content_type, :size, :scan)"
                ),
                {
                    "org": mailbox.organization_id,
                    "message": message_id,
                    "provider_id": attachment.provider_attachment_id,
                    "filename": attachment.filename,
                    "content_type": attachment.content_type,
                    "size": attachment.size_bytes,
                    "scan": attachment.scan_state,
                },
            )
            saved += 1
        return saved

    def mark_message_deleted(
        self, mailbox: MailboxSync, provider_message_id: str, *, at: datetime
    ) -> bool:
        result = self._c.execute(
            text(
                "UPDATE messages SET deleted_at = :at, updated_at = now() "
                "WHERE mailbox_id = CAST(:mailbox AS uuid) "
                "AND provider_message_id = :provider_message_id AND deleted_at IS NULL"
            ),
            {"mailbox": mailbox.id, "provider_message_id": provider_message_id, "at": at},
        )
        return bool(result.rowcount)

    # --- audit ---------------------------------------------------------

    def record_audit(
        self,
        *,
        organization_id: str | None,
        action: str,
        outcome: str,
        reason_code: str | None,
        target_id: str | None,
        metadata: dict[str, Any],
    ) -> None:
        self._c.execute(
            text(
                "INSERT INTO audit_events (organization_id, action, outcome, reason_code, "
                " target_type, target_id, metadata) VALUES (CAST(:org AS uuid), :action, :outcome, "
                " :reason, 'mailbox', :target, CAST(:metadata AS jsonb))"
            ),
            {
                "org": organization_id,
                "action": action,
                "outcome": outcome,
                "reason": reason_code,
                "target": target_id,
                "metadata": json.dumps(metadata, sort_keys=True, default=str),
            },
        )
