"""Records and the storage port ingestion works through.

The port exists so every recovery scenario the C07 gate names - duplicate,
delayed, out-of-order, expired watch, revoked token, quota, restart - runs
offline against an in-memory store, while the PostgreSQL adapter is verified
against a real database.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Protocol

from app.ingestion.normalize import AttachmentCandidate, NormalizedMessage


@dataclass(frozen=True)
class MailboxSync:
    """Everything sync needs to know about a mailbox."""

    id: str
    organization_id: str
    email_address: str
    status: str
    history_cursor: str | None = None
    watch_expires_at: datetime | None = None
    last_synced_at: datetime | None = None
    default_queue_id: str | None = None
    department_id: str | None = None


@dataclass(frozen=True)
class JobClaim:
    job_id: str
    created: bool


@dataclass(frozen=True)
class JobRecord:
    id: str
    organization_id: str
    job_type: str
    idempotency_key: str
    status: str
    attempts: int
    max_attempts: int
    payload: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class MessageUpsert:
    message_id: str
    thread_id: str
    ticket_id: str
    created: bool


@dataclass(frozen=True)
class TokenLease:
    """What the C06 connection service hands back for a mailbox."""

    ok: bool
    code: str
    access_token: str | None = field(default=None, repr=False)


class TokenProvider(Protocol):
    def __call__(self, mailbox_id: str) -> TokenLease: ...


class IngestionStore(Protocol):
    # --- mailboxes -----------------------------------------------------
    def find_mailbox(self, mailbox_id: str) -> MailboxSync | None: ...

    def find_mailbox_by_address(self, email_address: str) -> MailboxSync | None: ...

    def connected_mailboxes(self) -> list[MailboxSync]: ...

    def mailboxes_needing_watch(self, before: datetime) -> list[MailboxSync]: ...

    def set_history_cursor(self, mailbox_id: str, cursor: str, *, at: datetime) -> None: ...

    def set_watch(self, mailbox_id: str, *, expires_at: datetime, at: datetime) -> None: ...

    def mark_sync_error(self, mailbox_id: str, *, code: str, at: datetime) -> None: ...

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
    ) -> JobClaim: ...

    def due_jobs(self, *, now: datetime, job_type: str, limit: int) -> list[JobRecord]: ...

    def start_job(self, job_id: str, *, at: datetime) -> bool: ...

    def complete_job(self, job_id: str, *, at: datetime) -> None: ...

    def fail_job(self, job_id: str, *, error: str, retry_at: datetime, at: datetime) -> str:
        """Returns the resulting status: QUEUED or DEAD_LETTERED."""
        ...

    def reclaim_stalled_jobs(self, *, now: datetime, lease_seconds: int) -> list[str]: ...

    # --- conversation --------------------------------------------------
    def upsert_message(
        self,
        mailbox: MailboxSync,
        message: NormalizedMessage,
        *,
        at: datetime,
    ) -> MessageUpsert: ...

    def save_attachments(
        self, mailbox: MailboxSync, message_id: str, attachments: tuple[AttachmentCandidate, ...]
    ) -> int: ...

    def mark_message_deleted(
        self, mailbox: MailboxSync, provider_message_id: str, *, at: datetime
    ) -> bool: ...

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
    ) -> None: ...
