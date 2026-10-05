"""In-memory ingestion store.

A test fixture, like the mailbox and identity equivalents, never selectable at
runtime. It enforces the invariants that matter to service behaviour - the
per-mailbox uniqueness of provider ids, single-claim jobs, monotonic cursors -
so a scenario cannot pass here and fail against PostgreSQL for a reason the
service is responsible for.
"""

from __future__ import annotations

import itertools
from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from typing import Any

from app.ingestion.normalize import AttachmentCandidate, NormalizedMessage
from app.ingestion.records import JobClaim, JobRecord, MailboxSync, MessageUpsert


@dataclass
class StoredMessage:
    id: str
    mailbox_id: str
    thread_id: str
    provider_message_id: str
    direction: str
    from_address: str | None
    subject: str | None
    body_text: str | None
    sent_at: datetime
    has_attachments: bool
    deleted_at: datetime | None = None


@dataclass
class StoredThread:
    id: str
    mailbox_id: str
    provider_thread_id: str
    subject: str | None
    message_count: int = 0
    last_message_at: datetime | None = None


@dataclass
class StoredTicket:
    id: str
    mailbox_id: str
    thread_id: str
    subject: str | None
    status: str
    customer_address: str | None
    reference: int
    version: int = 1


@dataclass
class StoredJob:
    id: str
    organization_id: str
    job_type: str
    idempotency_key: str
    status: str
    attempts: int
    max_attempts: int
    payload: dict[str, Any]
    available_at: datetime
    started_at: datetime | None = None
    completed_at: datetime | None = None
    dead_lettered_at: datetime | None = None
    last_error: str | None = None


class InMemoryIngestionStore:
    def __init__(self, mailboxes: list[MailboxSync] | None = None):
        self.mailboxes: dict[str, MailboxSync] = {m.id: m for m in mailboxes or []}
        self.threads: dict[tuple[str, str], StoredThread] = {}
        self.messages: dict[tuple[str, str], StoredMessage] = {}
        self.tickets: dict[str, StoredTicket] = {}
        # Indexed by thread: scanning every ticket per message made the
        # fifty-mailbox simulation quadratic.
        self.tickets_by_thread: dict[str, StoredTicket] = {}
        self.attachments: dict[str, list[AttachmentCandidate]] = {}
        self.jobs: dict[str, StoredJob] = {}
        self.audit: list[dict[str, Any]] = []
        self.cursor_writes: list[tuple[str, str]] = []
        self._ids = itertools.count(1)
        self._references: dict[str, int] = {}

    def _next(self, prefix: str) -> str:
        return f"{prefix}-{next(self._ids)}"

    # --- mailboxes -----------------------------------------------------

    def find_mailbox(self, mailbox_id: str) -> MailboxSync | None:
        return self.mailboxes.get(mailbox_id)

    def find_mailbox_by_address(self, email_address: str) -> MailboxSync | None:
        address = email_address.strip().lower()
        return next((m for m in self.mailboxes.values() if m.email_address == address), None)

    def connected_mailboxes(self) -> list[MailboxSync]:
        return [m for m in self.mailboxes.values() if m.status == "CONNECTED"]

    def mailboxes_needing_watch(self, before: datetime) -> list[MailboxSync]:
        return [
            m
            for m in self.mailboxes.values()
            if m.status == "CONNECTED"
            and (m.watch_expires_at is None or m.watch_expires_at <= before)
        ]

    def set_history_cursor(self, mailbox_id: str, cursor: str, *, at: datetime) -> None:
        mailbox = self.mailboxes[mailbox_id]
        current = mailbox.history_cursor
        if current and current.isdigit() and cursor.isdigit() and int(cursor) < int(current):
            raise ValueError("history cursor must never move backwards")
        self.mailboxes[mailbox_id] = replace(mailbox, history_cursor=cursor, last_synced_at=at)
        self.cursor_writes.append((mailbox_id, cursor))

    def set_watch(self, mailbox_id: str, *, expires_at: datetime, at: datetime) -> None:
        self.mailboxes[mailbox_id] = replace(
            self.mailboxes[mailbox_id], watch_expires_at=expires_at
        )

    def mark_sync_error(self, mailbox_id: str, *, code: str, at: datetime) -> None:
        self.audit.append(
            {"action": "ingestion.sync_error", "reason_code": code, "target_id": mailbox_id}
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
        existing = next(
            (
                job
                for job in self.jobs.values()
                if job.job_type == job_type
                and job.idempotency_key == idempotency_key
                and job.organization_id == organization_id
            ),
            None,
        )
        if existing:
            return JobClaim(existing.id, created=False)

        job = StoredJob(
            id=self._next("job"),
            organization_id=organization_id,
            job_type=job_type,
            idempotency_key=idempotency_key,
            status="QUEUED",
            attempts=0,
            max_attempts=max_attempts,
            payload=dict(payload),
            available_at=available_at,
        )
        self.jobs[job.id] = job
        return JobClaim(job.id, created=True)

    def due_jobs(self, *, now: datetime, job_type: str, limit: int) -> list[JobRecord]:
        due = [
            job
            for job in self.jobs.values()
            if job.job_type == job_type and job.status == "QUEUED" and job.available_at <= now
        ]
        due.sort(key=lambda job: job.available_at)
        return [
            JobRecord(
                id=job.id,
                organization_id=job.organization_id,
                job_type=job.job_type,
                idempotency_key=job.idempotency_key,
                status=job.status,
                attempts=job.attempts,
                max_attempts=job.max_attempts,
                payload=dict(job.payload),
            )
            for job in due[:limit]
        ]

    def start_job(self, job_id: str, *, at: datetime) -> bool:
        job = self.jobs.get(job_id)
        if job is None or job.status != "QUEUED":
            return False
        job.status = "RUNNING"
        job.started_at = at
        return True

    def complete_job(self, job_id: str, *, at: datetime) -> None:
        job = self.jobs[job_id]
        job.status = "SUCCEEDED"
        job.completed_at = at
        job.started_at = None

    def fail_job(self, job_id: str, *, error: str, retry_at: datetime, at: datetime) -> str:
        job = self.jobs[job_id]
        job.attempts += 1
        job.last_error = error
        job.started_at = None
        if job.attempts >= job.max_attempts:
            job.status = "DEAD_LETTERED"
            job.dead_lettered_at = at
        else:
            job.status = "QUEUED"
            job.available_at = retry_at
        return job.status

    def reclaim_stalled_jobs(self, *, now: datetime, lease_seconds: int) -> list[str]:
        cutoff = now - timedelta(seconds=lease_seconds)
        reclaimed: list[str] = []
        for job in self.jobs.values():
            if job.status == "RUNNING" and job.started_at is not None and job.started_at <= cutoff:
                job.status = "QUEUED"
                job.available_at = now
                job.started_at = None
                reclaimed.append(job.id)
        return reclaimed

    # --- conversation --------------------------------------------------

    def upsert_message(
        self, mailbox: MailboxSync, message: NormalizedMessage, *, at: datetime
    ) -> MessageUpsert:
        message_key = (mailbox.id, message.provider_message_id)
        thread_key = (mailbox.id, message.provider_thread_id)

        thread = self.threads.get(thread_key)
        if thread is None:
            thread = StoredThread(
                id=self._next("thread"),
                mailbox_id=mailbox.id,
                provider_thread_id=message.provider_thread_id,
                subject=message.subject,
            )
            self.threads[thread_key] = thread

        existing = self.messages.get(message_key)
        if existing is not None:
            known = self.tickets_by_thread[thread.id]
            return MessageUpsert(existing.id, thread.id, known.id, created=False)

        stored = StoredMessage(
            id=self._next("message"),
            mailbox_id=mailbox.id,
            thread_id=thread.id,
            provider_message_id=message.provider_message_id,
            direction=message.direction,
            from_address=message.from_address,
            subject=message.subject,
            body_text=message.body_text,
            sent_at=message.sent_at,
            has_attachments=message.has_attachments,
        )
        self.messages[message_key] = stored

        thread.message_count += 1
        if thread.last_message_at is None or message.sent_at > thread.last_message_at:
            thread.last_message_at = message.sent_at
        if thread.subject is None:
            thread.subject = message.subject

        ticket = self.tickets_by_thread.get(thread.id)
        if ticket is None:
            reference = self._references.get(mailbox.organization_id, 0) + 1
            self._references[mailbox.organization_id] = reference
            ticket = StoredTicket(
                id=self._next("ticket"),
                mailbox_id=mailbox.id,
                thread_id=thread.id,
                subject=message.subject,
                status="NEW",
                customer_address=message.from_address if message.direction == "INBOUND" else None,
                reference=reference,
            )
            self.tickets[ticket.id] = ticket
            self.tickets_by_thread[thread.id] = ticket
        elif ticket.status in {"RESOLVED", "CLOSED"} and message.direction == "INBOUND":
            ticket.status = "NEW"
            ticket.version += 1

        return MessageUpsert(stored.id, thread.id, ticket.id, created=True)

    def save_attachments(
        self, mailbox: MailboxSync, message_id: str, attachments: tuple[AttachmentCandidate, ...]
    ) -> int:
        self.attachments[message_id] = list(attachments)
        return len(attachments)

    def mark_message_deleted(
        self, mailbox: MailboxSync, provider_message_id: str, *, at: datetime
    ) -> bool:
        stored = self.messages.get((mailbox.id, provider_message_id))
        if stored is None or stored.deleted_at is not None:
            return False
        stored.deleted_at = at
        return True

    # --- audit ---------------------------------------------------------

    def record_audit(self, **event: Any) -> None:
        self.audit.append(event)

    # --- helpers for assertions ----------------------------------------

    def audit_actions(self) -> list[str]:
        return [event.get("action", "") for event in self.audit]

    def live_messages(self) -> list[StoredMessage]:
        return [m for m in self.messages.values() if m.deleted_at is None]
