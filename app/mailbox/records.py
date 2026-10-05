"""Records and the storage port the connection service works through.

The port keeps the service testable without a database - every connect,
refresh, revoke, reconnect, scope-denial, and wrong-mailbox case runs offline -
while the PostgreSQL adapter in `store.py` is verified against a live database.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Protocol

from app.mailbox.vault import SealedCredential

CONNECTABLE_KINDS = frozenset({"SHARED_MAILBOX", "USER_MAILBOX"})


@dataclass(frozen=True)
class MailboxRecord:
    id: str
    organization_id: str
    email_address: str
    kind: str
    status: str
    revoked_at: datetime | None = None


@dataclass(frozen=True)
class ConnectionAttempt:
    id: str
    organization_id: str
    mailbox_id: str
    initiated_by_membership_id: str
    state_hash: str
    verifier: SealedCredential = field(repr=False)
    expected_address: str
    expires_at: datetime
    created_at: datetime
    consumed_at: datetime | None = None
    outcome: str | None = None


@dataclass(frozen=True)
class StoredCredential:
    organization_id: str
    mailbox_id: str
    sealed: SealedCredential = field(repr=False)
    granted_scopes: frozenset[str]
    provider_account_email: str
    connected_by_membership_id: str | None
    last_refreshed_at: datetime | None = None


class MailboxStore(Protocol):
    def get_mailbox(self, organization_id: str, mailbox_id: str) -> MailboxRecord | None: ...

    def find_mailbox(self, mailbox_id: str) -> MailboxRecord | None: ...

    def allowed_domains(self, organization_id: str) -> frozenset[str]: ...

    def create_attempt(self, attempt: ConnectionAttempt) -> None: ...

    def find_attempt(self, state_hash: str) -> ConnectionAttempt | None: ...

    def consume_attempt(self, attempt_id: str, outcome: str, at: datetime) -> bool:
        """Atomically mark consumed. False if it was already consumed."""
        ...

    def set_attempt_outcome(self, attempt_id: str, outcome: str) -> None: ...

    def save_credential(self, credential: StoredCredential) -> None:
        """Replace any existing credential for the mailbox."""
        ...

    def get_credential(self, mailbox_id: str) -> StoredCredential | None: ...

    def delete_credential(self, mailbox_id: str) -> bool: ...

    def mark_connected(
        self,
        mailbox_id: str,
        *,
        credential_reference: str,
        scopes: frozenset[str],
        membership_id: str,
        at: datetime,
    ) -> None: ...

    def mark_refreshed(self, mailbox_id: str, *, at: datetime) -> None: ...

    def mark_degraded(self, mailbox_id: str, *, error_code: str, at: datetime) -> None: ...

    def mark_revoked(self, mailbox_id: str, *, error_code: str | None, at: datetime) -> None: ...

    def record_audit(
        self,
        *,
        organization_id: str,
        actor_membership_id: str | None,
        action: str,
        outcome: str,
        reason_code: str | None,
        target_id: str,
        metadata: dict[str, Any],
    ) -> None: ...
