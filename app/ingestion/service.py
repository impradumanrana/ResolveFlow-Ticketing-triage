"""The ingestion lifecycle: notifications, sync, watches, and recovery.

Every scenario the C07 gate names resolves to one of a few rules:

* **A notification is a hint.** Sync always reads from the mailbox's stored
  cursor, never from the notification's history id, so duplicated, delayed, and
  out-of-order notifications converge on the same result.
* **Cursors only move forward**, and only after the work for that window
  succeeded. A crash mid-sync leaves the cursor where it was, and the next run
  redoes the window - which is safe because writes are idempotent.
* **Failures are classified.** A stale cursor triggers reconciliation, a quota
  error defers with backoff, a revoked grant stops the mailbox and tells
  someone, a deleted message is skipped.
* **Access tokens are cached conservatively** and dropped the moment Gmail
  rejects one, so fifty mailboxes do not mint fifty tokens a minute.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

from app.ingestion import history as history_module
from app.ingestion.gmail import (
    GmailClient,
    GmailError,
    HistoryTooOld,
    MessageGone,
    RateLimited,
    Unauthorized,
)
from app.ingestion.normalize import NormalizationError, normalize_message
from app.ingestion.pubsub import (
    NotificationRefused,
    notification_job_key,
    parse_push_envelope,
)
from app.ingestion.records import IngestionStore, MailboxSync, TokenLease, TokenProvider

NOTIFICATION_JOB = "gmail.notification"
DEFAULT_MAX_ATTEMPTS = 10

# Gmail expires a watch after seven days. Renewing daily leaves six days of
# slack, so a scheduler outage does not silence a mailbox.
WATCH_RENEWAL_THRESHOLD = timedelta(days=2)
WATCH_LIFETIME_FALLBACK = timedelta(days=7)

# Access tokens live an hour. Fifteen minutes of margin covers clock skew and a
# slow sync that started just before expiry.
ACCESS_TOKEN_TTL = timedelta(minutes=45)

# Reconciliation window when the cursor is unusable. Gmail retains about a
# week of history, so a fortnight is comfortably wider than any gap it can hide.
RECONCILE_DAYS = 14

BASE_BACKOFF_SECONDS = 30
MAX_BACKOFF_SECONDS = 3600
JOB_LEASE_SECONDS = 900


def compute_backoff(attempts: int, *, base: int = BASE_BACKOFF_SECONDS) -> int:
    """Exponential backoff, bounded. Deterministic so tests can assert it."""
    if attempts <= 0:
        return base
    return min(MAX_BACKOFF_SECONDS, base * (2 ** min(attempts, 12)))


def _utc_now() -> datetime:
    return datetime.now(UTC)


@dataclass(frozen=True)
class NotificationOutcome:
    accepted: bool
    code: str
    mailbox_id: str | None = None
    job_id: str | None = None


@dataclass(frozen=True)
class SyncOutcome:
    ok: bool
    code: str
    mailbox_id: str
    ingested: int = 0
    duplicates: int = 0
    deleted: int = 0
    cursor: str | None = None
    retry_after_seconds: int | None = None

    @property
    def should_retry(self) -> bool:
        return self.code in {"RATE_LIMITED", "GMAIL_UNAVAILABLE", "TOKEN_UNAVAILABLE"}


@dataclass(frozen=True)
class WatchOutcome:
    mailbox_id: str
    ok: bool
    code: str
    expires_at: datetime | None = None


@dataclass
class _CachedToken:
    access_token: str = field(repr=False)
    expires_at: datetime


class IngestionService:
    def __init__(
        self,
        store: IngestionStore,
        gmail: GmailClient,
        token_provider: TokenProvider,
        *,
        topic_name: str = "",
        now: Callable[[], datetime] = _utc_now,
    ):
        self._store = store
        self._gmail = gmail
        self._tokens = token_provider
        self._topic = topic_name
        self._now = now
        self._token_cache: dict[str, _CachedToken] = {}

    # ------------------------------------------------------------------
    # Tokens
    # ------------------------------------------------------------------

    def _access_token(self, mailbox_id: str) -> TokenLease:
        cached = self._token_cache.get(mailbox_id)
        if cached and cached.expires_at > self._now():
            return TokenLease(True, "CACHED", cached.access_token)

        lease = self._tokens(mailbox_id)
        if lease.ok and lease.access_token:
            self._token_cache[mailbox_id] = _CachedToken(
                access_token=lease.access_token, expires_at=self._now() + ACCESS_TOKEN_TTL
            )
        return lease

    def _invalidate_token(self, mailbox_id: str) -> None:
        self._token_cache.pop(mailbox_id, None)

    # ------------------------------------------------------------------
    # Notifications
    # ------------------------------------------------------------------

    def accept_notification(self, envelope: Any) -> NotificationOutcome:
        """Validate a push notification and queue exactly one sync for it."""
        try:
            notification = parse_push_envelope(envelope)
        except NotificationRefused as refused:
            return NotificationOutcome(False, refused.code)

        mailbox = self._store.find_mailbox_by_address(notification.email_address)
        if mailbox is None:
            # Acknowledged, not retried: redelivering will not make a mailbox
            # that was never connected here appear.
            return NotificationOutcome(False, "UNKNOWN_MAILBOX")

        if mailbox.status == "REVOKED":
            self._store.record_audit(
                organization_id=mailbox.organization_id,
                action="ingestion.notification_ignored",
                outcome="DENIED",
                reason_code="MAILBOX_REVOKED",
                target_id=mailbox.id,
                metadata={},
            )
            return NotificationOutcome(False, "MAILBOX_REVOKED", mailbox.id)

        claim = self._store.claim_job(
            organization_id=mailbox.organization_id,
            job_type=NOTIFICATION_JOB,
            idempotency_key=notification_job_key(notification),
            payload={
                "mailbox_id": mailbox.id,
                "history_id": notification.history_id,
                "email_address": notification.email_address,
            },
            available_at=self._now(),
            max_attempts=DEFAULT_MAX_ATTEMPTS,
        )
        if not claim.created:
            # Pub/Sub delivers at least once; the second delivery is expected.
            return NotificationOutcome(False, "DUPLICATE", mailbox.id, claim.job_id)

        return NotificationOutcome(True, "QUEUED", mailbox.id, claim.job_id)

    # ------------------------------------------------------------------
    # Job draining
    # ------------------------------------------------------------------

    def run_due_jobs(self, *, limit: int = 20) -> list[SyncOutcome]:
        """Claim and run queued notification jobs that are due."""
        outcomes: list[SyncOutcome] = []
        for job in self._store.due_jobs(now=self._now(), job_type=NOTIFICATION_JOB, limit=limit):
            if not self._store.start_job(job.id, at=self._now()):
                # Another worker took it between listing and claiming.
                continue

            mailbox_id = str(job.payload.get("mailbox_id") or "")
            outcome = self.sync_mailbox(mailbox_id, reason="notification")
            outcomes.append(outcome)

            if outcome.ok or not outcome.should_retry:
                self._store.complete_job(job.id, at=self._now())
            else:
                delay = outcome.retry_after_seconds or compute_backoff(job.attempts)
                self._store.fail_job(
                    job.id,
                    error=outcome.code,
                    retry_at=self._now() + timedelta(seconds=delay),
                    at=self._now(),
                )
        return outcomes

    def reclaim_stalled_jobs(self) -> list[str]:
        """Return jobs abandoned by a crashed worker to the queue."""
        reclaimed = self._store.reclaim_stalled_jobs(
            now=self._now(), lease_seconds=JOB_LEASE_SECONDS
        )
        if reclaimed:
            self._store.record_audit(
                organization_id=None,
                action="ingestion.jobs_reclaimed",
                outcome="ALLOWED",
                reason_code=None,
                target_id=None,
                metadata={"count": len(reclaimed)},
            )
        return reclaimed

    # ------------------------------------------------------------------
    # Sync
    # ------------------------------------------------------------------

    def sync_mailbox(self, mailbox_id: str, *, reason: str = "manual") -> SyncOutcome:
        mailbox = self._store.find_mailbox(mailbox_id)
        if mailbox is None:
            return SyncOutcome(False, "MAILBOX_NOT_FOUND", mailbox_id)
        if mailbox.status == "REVOKED":
            return SyncOutcome(False, "MAILBOX_REVOKED", mailbox.id)
        if mailbox.status == "PENDING":
            return SyncOutcome(False, "NOT_CONNECTED", mailbox.id)

        lease = self._access_token(mailbox.id)
        if not lease.ok or not lease.access_token:
            return self._token_failure(mailbox, lease)

        if not mailbox.history_cursor:
            # Never synced, or the cursor was cleared. Reconcile rather than
            # guess a starting point.
            return self.reconcile_mailbox(mailbox.id, reason="initial")

        try:
            return self._sync_from_cursor(mailbox, lease.access_token)
        except HistoryTooOld:
            self._store.record_audit(
                organization_id=mailbox.organization_id,
                action="ingestion.history_expired",
                outcome="FAILED",
                reason_code="HISTORY_TOO_OLD",
                target_id=mailbox.id,
                metadata={"reason": reason},
            )
            return self.reconcile_mailbox(mailbox.id, reason="history_too_old")
        except Unauthorized:
            self._invalidate_token(mailbox.id)
            return self._handle_unauthorized(mailbox)
        except RateLimited as limited:
            self._store.mark_sync_error(mailbox.id, code="RATE_LIMITED", at=self._now())
            return SyncOutcome(
                False,
                "RATE_LIMITED",
                mailbox.id,
                retry_after_seconds=limited.retry_after_seconds or compute_backoff(1),
            )
        except GmailError as error:
            self._store.mark_sync_error(mailbox.id, code=error.code, at=self._now())
            return SyncOutcome(
                False,
                error.code,
                mailbox.id,
                retry_after_seconds=compute_backoff(1) if error.transient else None,
            )

    def _sync_from_cursor(self, mailbox: MailboxSync, access_token: str) -> SyncOutcome:
        cursor = mailbox.history_cursor
        assert cursor is not None

        added: list[str] = []
        deleted: list[str] = []
        latest: str | None = cursor
        page_token: str | None = None

        while True:
            page = self._gmail.list_history(
                access_token, start_history_id=cursor, page_token=page_token
            )
            effects = history_module.compute_effects(page.records)
            added.extend(effects.added_message_ids)
            deleted.extend(effects.deleted_message_ids)
            latest = history_module.advance_cursor(latest, effects.highest_history_id)
            latest = history_module.advance_cursor(latest, page.latest_history_id)

            page_token = page.next_page_token
            if not page_token:
                break

        ingested, duplicates = self._ingest_messages(mailbox, access_token, added)
        removed = sum(
            1
            for message_id in deleted
            if self._store.mark_message_deleted(mailbox, message_id, at=self._now())
        )

        # Only after the window's work succeeded.
        if latest and history_module.is_behind(mailbox.history_cursor, latest):
            self._store.set_history_cursor(mailbox.id, latest, at=self._now())

        return SyncOutcome(
            True,
            "SYNCED",
            mailbox.id,
            ingested=ingested,
            duplicates=duplicates,
            deleted=removed,
            cursor=latest,
        )

    def _ingest_messages(
        self, mailbox: MailboxSync, access_token: str, message_ids: list[str]
    ) -> tuple[int, int]:
        ingested = 0
        duplicates = 0

        for message_id in dict.fromkeys(message_ids):
            try:
                resource = self._gmail.get_message(access_token, message_id)
            except MessageGone:
                # Deleted between the notification and the fetch. Nothing to do.
                continue

            try:
                normalized = normalize_message(resource, mailbox_address=mailbox.email_address)
            except NormalizationError as error:
                self._store.record_audit(
                    organization_id=mailbox.organization_id,
                    action="ingestion.message_rejected",
                    outcome="FAILED",
                    reason_code="NORMALIZATION_FAILED",
                    target_id=mailbox.id,
                    metadata={"provider_message_id": message_id, "detail": str(error)[:200]},
                )
                continue

            result = self._store.upsert_message(mailbox, normalized, at=self._now())
            if result.created:
                ingested += 1
                if normalized.attachments:
                    self._store.save_attachments(mailbox, result.message_id, normalized.attachments)
            else:
                duplicates += 1

        return ingested, duplicates

    # ------------------------------------------------------------------
    # Reconciliation and backfill
    # ------------------------------------------------------------------

    def reconcile_mailbox(
        self, mailbox_id: str, *, reason: str = "scheduled", days: int = RECONCILE_DAYS
    ) -> SyncOutcome:
        """List recent messages directly, ingesting anything history missed."""
        mailbox = self._store.find_mailbox(mailbox_id)
        if mailbox is None:
            return SyncOutcome(False, "MAILBOX_NOT_FOUND", mailbox_id)
        if mailbox.status == "REVOKED":
            return SyncOutcome(False, "MAILBOX_REVOKED", mailbox.id)

        lease = self._access_token(mailbox.id)
        if not lease.ok or not lease.access_token:
            return self._token_failure(mailbox, lease)

        ingested = 0
        duplicates = 0
        page_token: str | None = None

        try:
            while True:
                page = self._gmail.list_messages(
                    lease.access_token, query=f"newer_than:{days}d", page_token=page_token
                )
                batch_ingested, batch_duplicates = self._ingest_messages(
                    mailbox, lease.access_token, list(page.message_ids)
                )
                ingested += batch_ingested
                duplicates += batch_duplicates

                page_token = page.next_page_token
                if not page_token:
                    break

            # A fresh watch also yields a current history id, which becomes the
            # new cursor. Without it the next sync would start from a stale one.
            cursor = self._register_watch(mailbox, lease.access_token)
        except Unauthorized:
            self._invalidate_token(mailbox.id)
            return self._handle_unauthorized(mailbox)
        except RateLimited as limited:
            return SyncOutcome(
                False,
                "RATE_LIMITED",
                mailbox.id,
                ingested=ingested,
                duplicates=duplicates,
                retry_after_seconds=limited.retry_after_seconds or compute_backoff(1),
            )
        except GmailError as error:
            self._store.mark_sync_error(mailbox.id, code=error.code, at=self._now())
            return SyncOutcome(
                False, error.code, mailbox.id, ingested=ingested, duplicates=duplicates
            )

        self._store.record_audit(
            organization_id=mailbox.organization_id,
            action="ingestion.reconciled",
            outcome="ALLOWED",
            reason_code=reason,
            target_id=mailbox.id,
            metadata={"ingested": ingested, "duplicates": duplicates, "days": days},
        )
        return SyncOutcome(
            True, "RECONCILED", mailbox.id, ingested=ingested, duplicates=duplicates, cursor=cursor
        )

    # ------------------------------------------------------------------
    # Watches
    # ------------------------------------------------------------------

    def renew_watches(self, *, limit: int = 100) -> list[WatchOutcome]:
        """Renew watches before Gmail expires them."""
        due = self._store.mailboxes_needing_watch(self._now() + WATCH_RENEWAL_THRESHOLD)
        outcomes: list[WatchOutcome] = []

        for mailbox in due[:limit]:
            lease = self._access_token(mailbox.id)
            if not lease.ok or not lease.access_token:
                outcomes.append(WatchOutcome(mailbox.id, False, lease.code))
                continue
            try:
                self._register_watch(mailbox, lease.access_token)
            except Unauthorized:
                self._invalidate_token(mailbox.id)
                self._handle_unauthorized(mailbox)
                outcomes.append(WatchOutcome(mailbox.id, False, "UNAUTHORIZED"))
                continue
            except GmailError as error:
                self._store.mark_sync_error(mailbox.id, code=error.code, at=self._now())
                outcomes.append(WatchOutcome(mailbox.id, False, error.code))
                continue

            refreshed = self._store.find_mailbox(mailbox.id)
            outcomes.append(
                WatchOutcome(
                    mailbox.id,
                    True,
                    "RENEWED",
                    refreshed.watch_expires_at if refreshed else None,
                )
            )
        return outcomes

    def _register_watch(self, mailbox: MailboxSync, access_token: str) -> str | None:
        registration = self._gmail.watch(access_token, topic_name=self._topic)
        expires_at = datetime.fromtimestamp(registration.expiration_ms / 1000, tz=UTC)
        self._store.set_watch(mailbox.id, expires_at=expires_at, at=self._now())

        cursor = history_module.advance_cursor(mailbox.history_cursor, registration.history_id)
        if cursor and history_module.is_behind(mailbox.history_cursor, cursor):
            self._store.set_history_cursor(mailbox.id, cursor, at=self._now())
        return cursor

    # ------------------------------------------------------------------
    # Failure handling
    # ------------------------------------------------------------------

    def _token_failure(self, mailbox: MailboxSync, lease: TokenLease) -> SyncOutcome:
        if lease.code in {"INVALID_GRANT", "SCOPE_REMOVED", "MAILBOX_REVOKED", "NOT_CONNECTED"}:
            self._invalidate_token(mailbox.id)
            self._store.record_audit(
                organization_id=mailbox.organization_id,
                action="ingestion.stopped",
                outcome="FAILED",
                reason_code=lease.code,
                target_id=mailbox.id,
                metadata={},
            )
            # The connection service already withdrew the mailbox; retrying
            # would only produce the same refusal.
            return SyncOutcome(False, lease.code, mailbox.id)

        self._store.mark_sync_error(mailbox.id, code=lease.code, at=self._now())
        return SyncOutcome(
            False, "TOKEN_UNAVAILABLE", mailbox.id, retry_after_seconds=compute_backoff(1)
        )

    def _handle_unauthorized(self, mailbox: MailboxSync) -> SyncOutcome:
        """Gmail rejected a token that was accepted moments ago."""
        self._store.mark_sync_error(mailbox.id, code="UNAUTHORIZED", at=self._now())
        self._store.record_audit(
            organization_id=mailbox.organization_id,
            action="ingestion.unauthorized",
            outcome="FAILED",
            reason_code="UNAUTHORIZED",
            target_id=mailbox.id,
            metadata={},
        )
        return SyncOutcome(False, "UNAUTHORIZED", mailbox.id)
