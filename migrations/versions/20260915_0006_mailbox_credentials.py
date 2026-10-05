"""Encrypted mailbox credentials and single-use OAuth connection attempts.

C04 modelled mailboxes with a `credential_secret_name` reference. C06 decides
what that reference points at: an AES-256-GCM envelope in `mailbox_credentials`,
sealed under a Secret Manager-held key and bound to its one mailbox (C-D056).
`credential_secret_name` records which keyring and key sealed it, so a
connected mailbox always states how its credential can be opened.

Access tokens are never stored. They live for an hour, are minted from the
refresh token when needed, and persisting them would only widen what a
database compromise yields.

Revision ID: 20260915_0006
Revises: 20260915_0005
Create Date: 2026-09-15
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260915_0006"
down_revision: str | Sequence[str] | None = "20260915_0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "mailbox_credentials",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("organization_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("mailbox_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("key_id", sa.Text(), nullable=False),
        # Ciphertext only. The check below rejects anything shaped like a raw
        # Google refresh token so an accidental plaintext write fails loudly.
        sa.Column("refresh_token_envelope", sa.Text(), nullable=False),
        sa.Column("granted_scopes", postgresql.ARRAY(sa.Text()), nullable=False),
        sa.Column("provider_account_email", sa.Text(), nullable=False),
        sa.Column("connected_by_membership_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.Column("last_refreshed_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("updated_at", sa.TIMESTAMP(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["mailbox_id"], ["mailboxes.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["connected_by_membership_id"], ["memberships.id"], ondelete="SET NULL"
        ),
        sa.CheckConstraint("left(refresh_token_envelope, 3) = 'v1.'", name="envelope_is_sealed"),
        sa.CheckConstraint("left(refresh_token_envelope, 5) <> '1//0'", name="not_a_raw_token"),
        sa.CheckConstraint(
            "provider_account_email = lower(provider_account_email)",
            name="account_email_is_lowercase",
        ),
        sa.CheckConstraint(
            "NOT ('https://mail.google.com/' = ANY(granted_scopes)) "
            "AND NOT ('https://www.googleapis.com/auth/gmail.send' = ANY(granted_scopes)) "
            "AND NOT ('https://www.googleapis.com/auth/gmail.compose' = ANY(granted_scopes)) "
            "AND NOT ('https://www.googleapis.com/auth/gmail.modify' = ANY(granted_scopes))",
            name="no_write_scope_in_v1",
        ),
    )
    # One live credential per mailbox. Reconnecting replaces, never accumulates.
    op.create_index(
        "uq_mailbox_credentials_mailbox", "mailbox_credentials", ["mailbox_id"], unique=True
    )
    op.create_index("ix_mailbox_credentials_key", "mailbox_credentials", ["key_id"])

    op.create_table(
        "mailbox_connection_attempts",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("organization_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("mailbox_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("initiated_by_membership_id", postgresql.UUID(as_uuid=True), nullable=False),
        # SHA-256 of the state value. The state itself only ever exists in the
        # browser redirect, so a database read cannot complete someone's flow.
        sa.Column("state_hash", sa.Text(), nullable=False),
        # The PKCE verifier cannot be hashed - it is sent to Google - so it is
        # sealed with the vault like any other credential.
        sa.Column("verifier_key_id", sa.Text(), nullable=False),
        sa.Column("verifier_envelope", sa.Text(), nullable=False),
        sa.Column("expected_address", sa.Text(), nullable=False),
        sa.Column("expires_at", sa.TIMESTAMP(timezone=True), nullable=False),
        sa.Column("consumed_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("outcome", sa.Text(), nullable=True),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["mailbox_id"], ["mailboxes.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["initiated_by_membership_id"], ["memberships.id"], ondelete="CASCADE"
        ),
        sa.CheckConstraint("length(state_hash) = 64", name="state_hash_is_sha256"),
        sa.CheckConstraint("left(verifier_envelope, 3) = 'v1.'", name="verifier_is_sealed"),
        sa.CheckConstraint("expires_at > created_at", name="expiry_follows_creation"),
        sa.CheckConstraint(
            "(consumed_at IS NULL) = (outcome IS NULL)", name="consumed_attempt_has_outcome"
        ),
    )
    op.create_index(
        "uq_mailbox_connection_attempts_state",
        "mailbox_connection_attempts",
        ["state_hash"],
        unique=True,
    )
    op.create_index(
        "ix_mailbox_connection_attempts_open",
        "mailbox_connection_attempts",
        ["mailbox_id", "expires_at"],
        postgresql_where=sa.text("consumed_at IS NULL"),
    )


def downgrade() -> None:
    op.drop_table("mailbox_connection_attempts")
    op.drop_table("mailbox_credentials")
