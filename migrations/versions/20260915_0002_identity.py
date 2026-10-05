"""Identity, one organization, membership, departments, and security audit.

C03 adds only the identity slice of the durable model. C04 extends this schema
with queues, mailboxes, threads, tickets, knowledge, and the rest; it does not
rewrite it. Every tenant-owned table here carries `organization_id` from the
start, so the later SaaS profile needs no destructive migration (C-D004).

Revision ID: 20260915_0002
Revises: 20260911_0001
Create Date: 2026-09-15
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260915_0002"
down_revision: str | Sequence[str] | None = "20260911_0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Roles are a database-level enum so an unknown role cannot be written by any
# client, migration, or ad-hoc statement.
MEMBERSHIP_ROLES = (
    "OWNER",
    "ADMIN",
    "SUPERVISOR",
    "AGENT",
    "KNOWLEDGE_MANAGER",
    "AUDITOR",
)

MEMBERSHIP_STATUSES = ("INVITED", "ACTIVE", "SUSPENDED", "REVOKED")

INVITATION_STATUSES = ("PENDING", "ACCEPTED", "REVOKED", "EXPIRED")


def _timestamps() -> list[sa.Column]:
    return [
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    ]


def upgrade() -> None:
    bind = op.get_bind()

    # Types are created once, explicitly. The instances bound to columns below
    # set `create_type=False`: without it SQLAlchemy emits CREATE TYPE again for
    # the first table that references each enum, which fails on a real database
    # even though the offline SQL looks correct.
    for enum_name, enum_values in (
        ("membership_role", MEMBERSHIP_ROLES),
        ("membership_status", MEMBERSHIP_STATUSES),
        ("invitation_status", INVITATION_STATUSES),
    ):
        postgresql.ENUM(*enum_values, name=enum_name).create(bind, checkfirst=True)

    membership_role = postgresql.ENUM(
        *MEMBERSHIP_ROLES, name="membership_role", create_type=False
    )
    membership_status = postgresql.ENUM(
        *MEMBERSHIP_STATUSES, name="membership_status", create_type=False
    )
    invitation_status = postgresql.ENUM(
        *INVITATION_STATUSES, name="invitation_status", create_type=False
    )

    op.create_table(
        "organizations",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("slug", sa.Text(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        # Observe Mode is a property of the deployment, recorded so that an
        # operator cannot claim the system was sending when it was not.
        sa.Column(
            "sending_enabled",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
        *_timestamps(),
        sa.CheckConstraint("slug ~ '^[a-z0-9][a-z0-9-]{1,61}[a-z0-9]$'", name="slug_format"),
        sa.CheckConstraint("length(btrim(name)) > 0", name="name_not_blank"),
        # C12 owns any change here. C03 must not ship a deployment that sends.
        sa.CheckConstraint("sending_enabled = false", name="sending_disabled_in_v1"),
    )
    op.create_index("uq_organizations_slug", "organizations", ["slug"], unique=True)

    # An approved domain is necessary but never sufficient: an invited
    # membership is still required to sign in (C-D005).
    op.create_table(
        "organization_domains",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("organization_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("domain", sa.Text(), nullable=False),
        *_timestamps(),
        sa.ForeignKeyConstraint(
            ["organization_id"], ["organizations.id"], ondelete="CASCADE"
        ),
        sa.CheckConstraint("domain = lower(domain)", name="domain_is_lowercase"),
        sa.CheckConstraint(
            "domain ~ '^[a-z0-9]([a-z0-9-]*[a-z0-9])?(\\.[a-z0-9]([a-z0-9-]*[a-z0-9])?)+$'",
            name="domain_format",
        ),
    )
    op.create_index(
        "uq_organization_domains_domain", "organization_domains", ["domain"], unique=True
    )

    # Auth.js core tables. `users` is deliberately global: membership, not the
    # user row, carries tenancy.
    op.create_table(
        "users",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("email", sa.Text(), nullable=False),
        sa.Column("name", sa.Text(), nullable=True),
        sa.Column("image", sa.Text(), nullable=True),
        sa.Column("email_verified", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("last_seen_at", sa.TIMESTAMP(timezone=True), nullable=True),
        *_timestamps(),
        sa.CheckConstraint("email = lower(email)", name="email_is_lowercase"),
        sa.CheckConstraint("position('@' in email) > 1", name="email_has_domain"),
    )
    op.create_index("uq_users_email", "users", ["email"], unique=True)

    op.create_table(
        "accounts",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("type", sa.Text(), nullable=False),
        sa.Column("provider", sa.Text(), nullable=False),
        sa.Column("provider_account_id", sa.Text(), nullable=False),
        # Provider tokens for *login* are short-lived and are not mailbox
        # tokens. Mailbox OAuth is a separate, encrypted store in C06.
        sa.Column("refresh_token", sa.Text(), nullable=True),
        sa.Column("access_token", sa.Text(), nullable=True),
        sa.Column("expires_at", sa.BigInteger(), nullable=True),
        sa.Column("token_type", sa.Text(), nullable=True),
        sa.Column("scope", sa.Text(), nullable=True),
        sa.Column("id_token", sa.Text(), nullable=True),
        sa.Column("session_state", sa.Text(), nullable=True),
        *_timestamps(),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
    )
    op.create_index(
        "uq_accounts_provider_account",
        "accounts",
        ["provider", "provider_account_id"],
        unique=True,
    )
    op.create_index("ix_accounts_user_id", "accounts", ["user_id"])

    # Database sessions, not JWTs: a session must be revocable server-side the
    # moment access is withdrawn (C-D005).
    op.create_table(
        "sessions",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("session_token", sa.Text(), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("expires", sa.TIMESTAMP(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("revoked_reason", sa.Text(), nullable=True),
        *_timestamps(),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
    )
    op.create_index("uq_sessions_session_token", "sessions", ["session_token"], unique=True)
    op.create_index("ix_sessions_user_id", "sessions", ["user_id"])
    op.create_index(
        "ix_sessions_active_expiry",
        "sessions",
        ["expires"],
        postgresql_where=sa.text("revoked_at IS NULL"),
    )

    op.create_table(
        "verification_tokens",
        sa.Column("identifier", sa.Text(), nullable=False),
        sa.Column("token", sa.Text(), nullable=False),
        sa.Column("expires", sa.TIMESTAMP(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("identifier", "token"),
    )

    op.create_table(
        "departments",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("organization_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("slug", sa.Text(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("archived_at", sa.TIMESTAMP(timezone=True), nullable=True),
        *_timestamps(),
        sa.ForeignKeyConstraint(
            ["organization_id"], ["organizations.id"], ondelete="CASCADE"
        ),
        sa.CheckConstraint("slug ~ '^[a-z0-9][a-z0-9-]{1,61}[a-z0-9]$'", name="slug_format"),
    )
    op.create_index(
        "uq_departments_organization_slug",
        "departments",
        ["organization_id", "slug"],
        unique=True,
    )

    op.create_table(
        "memberships",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("organization_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("role", membership_role, nullable=False),
        sa.Column(
            "status", membership_status, nullable=False, server_default=sa.text("'INVITED'")
        ),
        sa.Column("invited_by_membership_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("invited_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("activated_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("revoked_at", sa.TIMESTAMP(timezone=True), nullable=True),
        *_timestamps(),
        sa.ForeignKeyConstraint(
            ["organization_id"], ["organizations.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["invited_by_membership_id"], ["memberships.id"], ondelete="SET NULL"
        ),
        sa.CheckConstraint(
            "(status = 'REVOKED') = (revoked_at IS NOT NULL)",
            name="revoked_status_matches_timestamp",
        ),
    )
    op.create_index(
        "uq_memberships_organization_user",
        "memberships",
        ["organization_id", "user_id"],
        unique=True,
    )
    op.create_index("ix_memberships_user_id", "memberships", ["user_id"])
    op.create_index(
        "ix_memberships_organization_status",
        "memberships",
        ["organization_id", "status"],
    )

    # Exactly one Owner per organization, enforced by the database rather than
    # by application convention.
    op.create_index(
        "uq_memberships_single_active_owner",
        "memberships",
        ["organization_id"],
        unique=True,
        postgresql_where=sa.text("role = 'OWNER' AND status <> 'REVOKED'"),
    )

    op.create_table(
        "department_memberships",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("organization_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("membership_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("department_id", postgresql.UUID(as_uuid=True), nullable=False),
        *_timestamps(),
        sa.ForeignKeyConstraint(
            ["organization_id"], ["organizations.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(["membership_id"], ["memberships.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["department_id"], ["departments.id"], ondelete="CASCADE"),
    )
    op.create_index(
        "uq_department_memberships_pair",
        "department_memberships",
        ["membership_id", "department_id"],
        unique=True,
    )
    op.create_index(
        "ix_department_memberships_department",
        "department_memberships",
        ["department_id"],
    )

    op.create_table(
        "invitations",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("organization_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("email", sa.Text(), nullable=False),
        sa.Column("role", membership_role, nullable=False),
        # Only a hash is stored. A leaked database row must not be redeemable.
        sa.Column("token_hash", sa.Text(), nullable=False),
        sa.Column(
            "status", invitation_status, nullable=False, server_default=sa.text("'PENDING'")
        ),
        sa.Column("expires_at", sa.TIMESTAMP(timezone=True), nullable=False),
        sa.Column("created_by_membership_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("accepted_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("accepted_user_id", postgresql.UUID(as_uuid=True), nullable=True),
        *_timestamps(),
        sa.ForeignKeyConstraint(
            ["organization_id"], ["organizations.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["created_by_membership_id"], ["memberships.id"], ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(["accepted_user_id"], ["users.id"], ondelete="SET NULL"),
        sa.CheckConstraint("email = lower(email)", name="email_is_lowercase"),
        sa.CheckConstraint("length(token_hash) = 64", name="token_hash_is_sha256_hex"),
        sa.CheckConstraint(
            "(status = 'ACCEPTED') = (accepted_at IS NOT NULL)",
            name="accepted_status_matches_timestamp",
        ),
        # An Owner is seeded by the bootstrap procedure, never invited by email.
        sa.CheckConstraint("role <> 'OWNER'", name="owner_is_not_invitable"),
    )
    op.create_index("uq_invitations_token_hash", "invitations", ["token_hash"], unique=True)
    op.create_index(
        "uq_invitations_pending_email",
        "invitations",
        ["organization_id", "email"],
        unique=True,
        postgresql_where=sa.text("status = 'PENDING'"),
    )

    # Append-only security and authorization audit. C04 extends this with the
    # operational action history; the append-only guarantee is established here
    # because C03 is the first phase that produces attributable decisions.
    op.create_table(
        "audit_events",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("organization_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("actor_user_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("actor_membership_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("actor_email", sa.Text(), nullable=True),
        sa.Column("action", sa.Text(), nullable=False),
        sa.Column("outcome", sa.Text(), nullable=False),
        sa.Column("reason_code", sa.Text(), nullable=True),
        sa.Column("target_type", sa.Text(), nullable=True),
        sa.Column("target_id", sa.Text(), nullable=True),
        sa.Column("request_id", sa.Text(), nullable=True),
        # Hashed, not raw: the address is needed to correlate an incident, not
        # to profile a person.
        sa.Column("source_ip_hash", sa.Text(), nullable=True),
        sa.Column("user_agent", sa.Text(), nullable=True),
        sa.Column(
            "metadata",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column(
            "occurred_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.ForeignKeyConstraint(
            ["organization_id"], ["organizations.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(["actor_user_id"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(
            ["actor_membership_id"], ["memberships.id"], ondelete="SET NULL"
        ),
        sa.CheckConstraint(
            "outcome in ('ALLOWED', 'DENIED', 'FAILED')", name="outcome_is_known"
        ),
    )
    op.create_index(
        "ix_audit_events_organization_occurred",
        "audit_events",
        ["organization_id", "occurred_at"],
    )
    op.create_index("ix_audit_events_actor_user", "audit_events", ["actor_user_id"])
    op.create_index("ix_audit_events_action", "audit_events", ["action", "occurred_at"])

    # Append-only is enforced in the database. An application bug, a compromised
    # application identity, or an operator with table access cannot quietly
    # rewrite history; deletion requires dropping the trigger, which is itself a
    # logged DDL change (pgaudit records ddl by C02 configuration).
    op.execute(
        """
        CREATE OR REPLACE FUNCTION audit_events_append_only()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        BEGIN
            -- Built by concatenation rather than a format placeholder,
            -- which the DBAPI paramstyle would escape in offline SQL output.
            RAISE EXCEPTION USING
                ERRCODE = 'restrict_violation',
                MESSAGE = 'audit_events is append-only (attempted ' || TG_OP || ')';
        END;
        $$;
        """
    )
    op.execute(
        """
        CREATE TRIGGER audit_events_no_update_or_delete
        BEFORE UPDATE OR DELETE ON audit_events
        FOR EACH ROW EXECUTE FUNCTION audit_events_append_only();
        """
    )


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS audit_events_no_update_or_delete ON audit_events;")
    op.execute("DROP FUNCTION IF EXISTS audit_events_append_only();")

    op.drop_table("audit_events")
    op.drop_table("invitations")
    op.drop_table("department_memberships")
    op.drop_table("memberships")
    op.drop_table("departments")
    op.drop_table("verification_tokens")
    op.drop_table("sessions")
    op.drop_table("accounts")
    op.drop_table("users")
    op.drop_table("organization_domains")
    op.drop_table("organizations")

    for enum_name in ("invitation_status", "membership_status", "membership_role"):
        op.execute(f"DROP TYPE IF EXISTS {enum_name};")
