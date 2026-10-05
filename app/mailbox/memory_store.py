"""In-memory mailbox store.

A test fixture, following the rule set by the deterministic model provider
(D-012): it exists so every connection case runs without a database, and it is
never selectable at runtime. It also serves C07's 50-mailbox simulation.

It enforces the same invariants the database does where they affect service
behaviour - single-use attempts, one credential per mailbox, the REVOKED and
CONNECTED status rules - so a test cannot pass here and fail against PostgreSQL
for a reason the service is responsible for.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime
from typing import Any

from app.mailbox.records import ConnectionAttempt, MailboxRecord, StoredCredential


class InMemoryMailboxStore:
    def __init__(
        self,
        mailboxes: list[MailboxRecord] | None = None,
        allowed_domains: dict[str, frozenset[str]] | None = None,
    ):
        self.mailboxes: dict[str, MailboxRecord] = {m.id: m for m in mailboxes or []}
        self.domains = dict(allowed_domains or {})
        self.attempts: dict[str, ConnectionAttempt] = {}
        self.credentials: dict[str, StoredCredential] = {}
        self.audit: list[dict[str, Any]] = []
        self.mailbox_fields: dict[str, dict[str, Any]] = {m.id: {} for m in mailboxes or []}

    def get_mailbox(self, organization_id: str, mailbox_id: str) -> MailboxRecord | None:
        mailbox = self.mailboxes.get(mailbox_id)
        return mailbox if mailbox and mailbox.organization_id == organization_id else None

    def find_mailbox(self, mailbox_id: str) -> MailboxRecord | None:
        return self.mailboxes.get(mailbox_id)

    def allowed_domains(self, organization_id: str) -> frozenset[str]:
        return self.domains.get(organization_id, frozenset())

    def create_attempt(self, attempt: ConnectionAttempt) -> None:
        if any(a.state_hash == attempt.state_hash for a in self.attempts.values()):
            raise ValueError("duplicate state hash")
        self.attempts[attempt.id] = attempt

    def find_attempt(self, state_hash: str) -> ConnectionAttempt | None:
        return next((a for a in self.attempts.values() if a.state_hash == state_hash), None)

    def consume_attempt(self, attempt_id: str, outcome: str, at: datetime) -> bool:
        attempt = self.attempts.get(attempt_id)
        if attempt is None or attempt.consumed_at is not None:
            return False
        self.attempts[attempt_id] = replace(attempt, consumed_at=at, outcome=outcome)
        return True

    def set_attempt_outcome(self, attempt_id: str, outcome: str) -> None:
        attempt = self.attempts[attempt_id]
        self.attempts[attempt_id] = replace(attempt, outcome=outcome)

    def save_credential(self, credential: StoredCredential) -> None:
        self.credentials[credential.mailbox_id] = credential

    def get_credential(self, mailbox_id: str) -> StoredCredential | None:
        return self.credentials.get(mailbox_id)

    def delete_credential(self, mailbox_id: str) -> bool:
        return self.credentials.pop(mailbox_id, None) is not None

    def _set(
        self, mailbox_id: str, status: str, revoked_at: datetime | None, **fields: Any
    ) -> None:
        mailbox = self.mailboxes[mailbox_id]
        if status == "CONNECTED" and not fields.get(
            "credential_reference", self.mailbox_fields[mailbox_id].get("credential_reference")
        ):
            raise ValueError("a CONNECTED mailbox requires a credential reference")
        if (status == "REVOKED") != (revoked_at is not None):
            raise ValueError("REVOKED status must match revoked_at")
        self.mailboxes[mailbox_id] = replace(mailbox, status=status, revoked_at=revoked_at)
        self.mailbox_fields[mailbox_id].update(fields)

    def mark_connected(
        self,
        mailbox_id: str,
        *,
        credential_reference: str,
        scopes: frozenset[str],
        membership_id: str,
        at: datetime,
    ) -> None:
        self._set(
            mailbox_id,
            "CONNECTED",
            None,
            credential_reference=credential_reference,
            granted_scopes=scopes,
            connected_by=membership_id,
            connected_at=at,
            last_error_code=None,
        )

    def mark_refreshed(self, mailbox_id: str, *, at: datetime) -> None:
        mailbox = self.mailboxes[mailbox_id]
        self._set(
            mailbox_id,
            "CONNECTED" if mailbox.status != "REVOKED" else mailbox.status,
            mailbox.revoked_at,
            last_synced_at=at,
            last_error_code=None,
        )

    def mark_degraded(self, mailbox_id: str, *, error_code: str, at: datetime) -> None:
        self._set(mailbox_id, "DEGRADED", None, last_error_code=error_code, last_error_at=at)

    def mark_revoked(self, mailbox_id: str, *, error_code: str | None, at: datetime) -> None:
        self._set(
            mailbox_id,
            "REVOKED",
            at,
            credential_reference=None,
            last_error_code=error_code,
        )

    def record_audit(self, **event: Any) -> None:
        self.audit.append(event)
