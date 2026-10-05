"""PostgreSQL mailbox store.

Every statement is parameterised, and every mailbox lookup made on behalf of a
person is constrained by organization. `find_mailbox` is the one unscoped read,
used only by system-initiated refresh, which has no actor to scope by.
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from sqlalchemy import text

from app.mailbox.records import ConnectionAttempt, MailboxRecord, StoredCredential
from app.mailbox.vault import SealedCredential

_MAILBOX_COLUMNS = (
    "id::text, organization_id::text, email_address, kind::text, status::text, revoked_at"
)


def _mailbox(row: Any) -> MailboxRecord:
    return MailboxRecord(
        id=row[0],
        organization_id=row[1],
        email_address=row[2],
        kind=row[3],
        status=row[4],
        revoked_at=row[5],
    )


class PostgresMailboxStore:
    def __init__(self, connection: Any):
        self._c = connection

    def get_mailbox(self, organization_id: str, mailbox_id: str) -> MailboxRecord | None:
        row = self._c.execute(
            text(
                f"SELECT {_MAILBOX_COLUMNS} FROM mailboxes "
                "WHERE id = CAST(:id AS uuid) AND organization_id = CAST(:org AS uuid)"
            ),
            {"id": mailbox_id, "org": organization_id},
        ).first()
        return _mailbox(row) if row else None

    def find_mailbox(self, mailbox_id: str) -> MailboxRecord | None:
        row = self._c.execute(
            text(f"SELECT {_MAILBOX_COLUMNS} FROM mailboxes WHERE id = CAST(:id AS uuid)"),
            {"id": mailbox_id},
        ).first()
        return _mailbox(row) if row else None

    def allowed_domains(self, organization_id: str) -> frozenset[str]:
        rows = self._c.execute(
            text(
                "SELECT domain FROM organization_domains WHERE organization_id = CAST(:org AS uuid)"
            ),
            {"org": organization_id},
        ).all()
        return frozenset(row[0] for row in rows)

    def create_attempt(self, attempt: ConnectionAttempt) -> None:
        self._c.execute(
            text(
                "INSERT INTO mailbox_connection_attempts "
                "(id, organization_id, mailbox_id, initiated_by_membership_id, state_hash, "
                " verifier_key_id, verifier_envelope, expected_address, expires_at, created_at) "
                "VALUES (CAST(:id AS uuid), CAST(:org AS uuid), CAST(:mailbox AS uuid), "
                " CAST(:membership AS uuid), :state_hash, :key_id, :envelope, :address, "
                " :expires_at, :created_at)"
            ),
            {
                "id": attempt.id,
                "org": attempt.organization_id,
                "mailbox": attempt.mailbox_id,
                "membership": attempt.initiated_by_membership_id,
                "state_hash": attempt.state_hash,
                "key_id": attempt.verifier.key_id,
                "envelope": attempt.verifier.envelope,
                "address": attempt.expected_address,
                "expires_at": attempt.expires_at,
                "created_at": attempt.created_at,
            },
        )

    def find_attempt(self, state_hash: str) -> ConnectionAttempt | None:
        row = self._c.execute(
            text(
                "SELECT id::text, organization_id::text, mailbox_id::text, "
                " initiated_by_membership_id::text, state_hash, verifier_key_id, "
                " verifier_envelope, expected_address, expires_at, created_at, consumed_at, "
                "outcome "
                "FROM mailbox_connection_attempts WHERE state_hash = :state_hash"
            ),
            {"state_hash": state_hash},
        ).first()
        if row is None:
            return None
        return ConnectionAttempt(
            id=row[0],
            organization_id=row[1],
            mailbox_id=row[2],
            initiated_by_membership_id=row[3],
            state_hash=row[4],
            verifier=SealedCredential(key_id=row[5], envelope=row[6]),
            expected_address=row[7],
            expires_at=row[8],
            created_at=row[9],
            consumed_at=row[10],
            outcome=row[11],
        )

    def consume_attempt(self, attempt_id: str, outcome: str, at: datetime) -> bool:
        # The WHERE clause is the replay guard: two concurrent callbacks for the
        # same state cannot both update a row whose consumed_at is still null.
        result = self._c.execute(
            text(
                "UPDATE mailbox_connection_attempts SET consumed_at = :at, outcome = :outcome "
                "WHERE id = CAST(:id AS uuid) AND consumed_at IS NULL"
            ),
            {"id": attempt_id, "outcome": outcome, "at": at},
        )
        return bool(result.rowcount)

    def set_attempt_outcome(self, attempt_id: str, outcome: str) -> None:
        self._c.execute(
            text(
                "UPDATE mailbox_connection_attempts SET outcome = :outcome WHERE id = CAST(:id AS "
                "uuid)"
            ),
            {"id": attempt_id, "outcome": outcome},
        )

    def save_credential(self, credential: StoredCredential) -> None:
        self._c.execute(
            text(
                "INSERT INTO mailbox_credentials "
                "(organization_id, mailbox_id, key_id, refresh_token_envelope, granted_scopes, "
                " provider_account_email, connected_by_membership_id, last_refreshed_at) "
                "VALUES (CAST(:org AS uuid), CAST(:mailbox AS uuid), :key_id, :envelope, :scopes, "
                " :email, CAST(:membership AS uuid), :refreshed) "
                "ON CONFLICT (mailbox_id) DO UPDATE SET "
                " key_id = EXCLUDED.key_id, refresh_token_envelope = "
                "EXCLUDED.refresh_token_envelope, "
                " granted_scopes = EXCLUDED.granted_scopes, "
                " provider_account_email = EXCLUDED.provider_account_email, "
                " connected_by_membership_id = EXCLUDED.connected_by_membership_id, "
                " last_refreshed_at = EXCLUDED.last_refreshed_at, updated_at = now()"
            ),
            {
                "org": credential.organization_id,
                "mailbox": credential.mailbox_id,
                "key_id": credential.sealed.key_id,
                "envelope": credential.sealed.envelope,
                "scopes": sorted(credential.granted_scopes),
                "email": credential.provider_account_email,
                "membership": credential.connected_by_membership_id,
                "refreshed": credential.last_refreshed_at,
            },
        )

    def get_credential(self, mailbox_id: str) -> StoredCredential | None:
        row = self._c.execute(
            text(
                "SELECT organization_id::text, mailbox_id::text, key_id, refresh_token_envelope, "
                " granted_scopes, provider_account_email, connected_by_membership_id::text, "
                " last_refreshed_at FROM mailbox_credentials WHERE mailbox_id = CAST(:id AS uuid)"
            ),
            {"id": mailbox_id},
        ).first()
        if row is None:
            return None
        return StoredCredential(
            organization_id=row[0],
            mailbox_id=row[1],
            sealed=SealedCredential(key_id=row[2], envelope=row[3]),
            granted_scopes=frozenset(row[4]),
            provider_account_email=row[5],
            connected_by_membership_id=row[6],
            last_refreshed_at=row[7],
        )

    def delete_credential(self, mailbox_id: str) -> bool:
        result = self._c.execute(
            text("DELETE FROM mailbox_credentials WHERE mailbox_id = CAST(:id AS uuid)"),
            {"id": mailbox_id},
        )
        return bool(result.rowcount)

    def mark_connected(
        self,
        mailbox_id: str,
        *,
        credential_reference: str,
        scopes: frozenset[str],
        membership_id: str,
        at: datetime,
    ) -> None:
        self._c.execute(
            text(
                "UPDATE mailboxes SET status = 'CONNECTED', credential_secret_name = :ref, "
                " granted_scopes = :scopes, connected_by_membership_id = CAST(:membership AS "
                "uuid), "
                " connected_at = :at, revoked_at = NULL, last_error_code = NULL, "
                " last_error_at = NULL, updated_at = now() WHERE id = CAST(:id AS uuid)"
            ),
            {
                "id": mailbox_id,
                "ref": credential_reference,
                "scopes": sorted(scopes),
                "membership": membership_id,
                "at": at,
            },
        )

    def mark_refreshed(self, mailbox_id: str, *, at: datetime) -> None:
        self._c.execute(
            text(
                "UPDATE mailboxes SET status = 'CONNECTED', last_error_code = NULL, "
                " last_error_at = NULL, updated_at = now() "
                "WHERE id = CAST(:id AS uuid) AND status <> 'REVOKED'"
            ),
            {"id": mailbox_id},
        )
        self._c.execute(
            text(
                "UPDATE mailbox_credentials SET last_refreshed_at = :at, updated_at = now() "
                "WHERE mailbox_id = CAST(:id AS uuid)"
            ),
            {"id": mailbox_id, "at": at},
        )

    def mark_degraded(self, mailbox_id: str, *, error_code: str, at: datetime) -> None:
        self._c.execute(
            text(
                "UPDATE mailboxes SET status = 'DEGRADED', last_error_code = :code, "
                " last_error_at = :at, updated_at = now() WHERE id = CAST(:id AS uuid)"
            ),
            {"id": mailbox_id, "code": error_code, "at": at},
        )

    def mark_revoked(self, mailbox_id: str, *, error_code: str | None, at: datetime) -> None:
        self._c.execute(
            text(
                "UPDATE mailboxes SET status = 'REVOKED', revoked_at = :at, "
                " credential_secret_name = NULL, last_error_code = :code, "
                " last_error_at = CASE WHEN CAST(:code AS text) IS NULL THEN last_error_at ELSE "
                ":at END, "
                " updated_at = now() WHERE id = CAST(:id AS uuid)"
            ),
            {"id": mailbox_id, "code": error_code, "at": at},
        )

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
    ) -> None:
        self._c.execute(
            text(
                "INSERT INTO audit_events (organization_id, actor_membership_id, action, outcome, "
                " reason_code, target_type, target_id, metadata) VALUES (CAST(:org AS uuid), "
                " CAST(:membership AS uuid), :action, :outcome, :reason, 'mailbox', :target, "
                " CAST(:metadata AS jsonb))"
            ),
            {
                "org": organization_id,
                "membership": actor_membership_id,
                "action": action,
                "outcome": outcome,
                "reason": reason_code,
                "target": target_id,
                "metadata": json.dumps(metadata, sort_keys=True),
            },
        )
