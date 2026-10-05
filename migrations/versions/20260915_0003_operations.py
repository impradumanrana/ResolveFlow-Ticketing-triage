"""Durable operational model: mailboxes, conversations, tickets, and SLA.

Extends the C03 identity schema; it does not replace it. Split from the
knowledge and governance migration (0004) so that a failure in one is not a
failure in both, and so each is reviewable.

Conventions used throughout C04:

* Every tenant-owned table carries `organization_id` (C-D004), and every
  composite lookup index leads with it.
* Provider identifiers are unique *per mailbox*, never globally. Two mailboxes
  legitimately see the same Gmail message id.
* Timestamps are `timestamptz`. Business hours and SLA are computed against a
  recorded time zone, never the server's.
* `deleted_at` marks a row as withdrawn from the product without erasing it;
  retention (migration 0004) is what actually removes data.

Revision ID: 20260915_0003
Revises: 20260915_0002
Create Date: 2026-09-15
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260915_0003"
down_revision: str | Sequence[str] | None = "20260915_0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# A mailbox, an alias, and a Google Group are different things with different
# permission models (C-D007). Recording which one this is prevents a later
# "why did replies come from the wrong address" incident.
MAILBOX_KINDS = ("SHARED_MAILBOX", "USER_MAILBOX", "GROUP", "ALIAS")

MAILBOX_STATUSES = (
    "PENDING",
    "CONNECTED",
    "DEGRADED",
    "REVOKED",
    "DISCONNECTED",
)

TICKET_STATUSES = (
    "NEW",
    "TRIAGED",
    "WAITING_ON_CUSTOMER",
    "WAITING_ON_REVIEW",
    "IN_PROGRESS",
    "RESOLVED",
    "CLOSED",
)

TICKET_ROUTES = ("AUTO_RESOLVE", "CLARIFY", "ESCALATE")

TICKET_URGENCIES = ("low", "medium", "high", "critical")

MESSAGE_DIRECTIONS = ("INBOUND", "OUTBOUND", "INTERNAL")

SLA_STATES = ("ON_TRACK", "AT_RISK", "BREACHED", "MET", "NOT_APPLICABLE")


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


def _organization_column() -> sa.Column:
    return sa.Column("organization_id", postgresql.UUID(as_uuid=True), nullable=False)


def _primary_key() -> sa.Column:
    return sa.Column(
        "id",
        postgresql.UUID(as_uuid=True),
        primary_key=True,
        server_default=sa.text("gen_random_uuid()"),
    )


def upgrade() -> None:
    bind = op.get_bind()

    for enum_name, enum_values in (
        ("mailbox_kind", MAILBOX_KINDS),
        ("mailbox_status", MAILBOX_STATUSES),
        ("ticket_status", TICKET_STATUSES),
        ("ticket_route", TICKET_ROUTES),
        ("ticket_urgency", TICKET_URGENCIES),
        ("message_direction", MESSAGE_DIRECTIONS),
        ("sla_state", SLA_STATES),
    ):
        postgresql.ENUM(*enum_values, name=enum_name).create(bind, checkfirst=True)

    mailbox_kind = postgresql.ENUM(*MAILBOX_KINDS, name="mailbox_kind", create_type=False)
    mailbox_status = postgresql.ENUM(
        *MAILBOX_STATUSES, name="mailbox_status", create_type=False
    )
    ticket_status = postgresql.ENUM(*TICKET_STATUSES, name="ticket_status", create_type=False)
    ticket_route = postgresql.ENUM(*TICKET_ROUTES, name="ticket_route", create_type=False)
    ticket_urgency = postgresql.ENUM(
        *TICKET_URGENCIES, name="ticket_urgency", create_type=False
    )
    message_direction = postgresql.ENUM(
        *MESSAGE_DIRECTIONS, name="message_direction", create_type=False
    )
    sla_state = postgresql.ENUM(*SLA_STATES, name="sla_state", create_type=False)

    # ------------------------------------------------------------------
    # Queues
    # ------------------------------------------------------------------

    op.create_table(
        "queues",
        _primary_key(),
        _organization_column(),
        sa.Column("department_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("slug", sa.Text(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("archived_at", sa.TIMESTAMP(timezone=True), nullable=True),
        *_timestamps(),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["department_id"], ["departments.id"], ondelete="SET NULL"),
        sa.CheckConstraint("slug ~ '^[a-z0-9][a-z0-9-]{1,61}[a-z0-9]$'", name="slug_format"),
    )
    op.create_index(
        "uq_queues_organization_slug", "queues", ["organization_id", "slug"], unique=True
    )
    op.create_index("ix_queues_department", "queues", ["department_id"])

    # ------------------------------------------------------------------
    # Mailboxes
    # ------------------------------------------------------------------

    op.create_table(
        "mailboxes",
        _primary_key(),
        _organization_column(),
        sa.Column("department_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("default_queue_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("email_address", sa.Text(), nullable=False),
        sa.Column("display_name", sa.Text(), nullable=True),
        sa.Column("kind", mailbox_kind, nullable=False),
        sa.Column("status", mailbox_status, nullable=False, server_default=sa.text("'PENDING'")),
        sa.Column("provider", sa.Text(), nullable=False, server_default=sa.text("'gmail'")),
        # Credentials live in Secret Manager (C-D009). Only the reference is here.
        sa.Column("credential_secret_name", sa.Text(), nullable=True),
        sa.Column("granted_scopes", postgresql.ARRAY(sa.Text()), nullable=False,
                  server_default=sa.text("'{}'::text[]")),
        sa.Column("connected_by_membership_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("connected_at", sa.TIMESTAMP(timezone=True), nullable=True),
        # Gmail watches expire after seven days; renewal is a scheduled job.
        sa.Column("watch_expires_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("history_cursor", sa.Text(), nullable=True),
        sa.Column("last_synced_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("last_error_code", sa.Text(), nullable=True),
        sa.Column("last_error_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("revoked_at", sa.TIMESTAMP(timezone=True), nullable=True),
        *_timestamps(),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["department_id"], ["departments.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["default_queue_id"], ["queues.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(
            ["connected_by_membership_id"], ["memberships.id"], ondelete="SET NULL"
        ),
        sa.CheckConstraint("email_address = lower(email_address)", name="address_is_lowercase"),
        sa.CheckConstraint("position('@' in email_address) > 1", name="address_has_domain"),
        # A connected mailbox must say where its credentials are; a pending one
        # has none yet. This stops a half-connected mailbox from looking live.
        sa.CheckConstraint(
            "status <> 'CONNECTED' OR credential_secret_name IS NOT NULL",
            name="connected_requires_credential_reference",
        ),
        sa.CheckConstraint(
            "(status = 'REVOKED') = (revoked_at IS NOT NULL)",
            name="revoked_status_matches_timestamp",
        ),
    )
    op.create_index(
        "uq_mailboxes_organization_address",
        "mailboxes",
        ["organization_id", "email_address"],
        unique=True,
    )
    op.create_index("ix_mailboxes_status", "mailboxes", ["organization_id", "status"])
    op.create_index(
        "ix_mailboxes_watch_expiry",
        "mailboxes",
        ["watch_expires_at"],
        postgresql_where=sa.text("status = 'CONNECTED'"),
    )

    # Explicit per-membership mailbox permission. Membership in the
    # organization does not imply access to every mailbox.
    op.create_table(
        "mailbox_permissions",
        _primary_key(),
        _organization_column(),
        sa.Column("mailbox_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("membership_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("can_view", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("can_action", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("granted_by_membership_id", postgresql.UUID(as_uuid=True), nullable=True),
        *_timestamps(),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["mailbox_id"], ["mailboxes.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["membership_id"], ["memberships.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["granted_by_membership_id"], ["memberships.id"], ondelete="SET NULL"
        ),
        sa.CheckConstraint("can_view OR NOT can_action", name="action_requires_view"),
    )
    op.create_index(
        "uq_mailbox_permissions_pair",
        "mailbox_permissions",
        ["mailbox_id", "membership_id"],
        unique=True,
    )
    op.create_index(
        "ix_mailbox_permissions_membership", "mailbox_permissions", ["membership_id"]
    )

    # ------------------------------------------------------------------
    # Conversations
    # ------------------------------------------------------------------

    op.create_table(
        "threads",
        _primary_key(),
        _organization_column(),
        sa.Column("mailbox_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("provider_thread_id", sa.Text(), nullable=False),
        sa.Column("subject", sa.Text(), nullable=True),
        sa.Column("participant_addresses", postgresql.ARRAY(sa.Text()), nullable=False,
                  server_default=sa.text("'{}'::text[]")),
        sa.Column("message_count", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("first_message_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("last_message_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("deleted_at", sa.TIMESTAMP(timezone=True), nullable=True),
        *_timestamps(),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["mailbox_id"], ["mailboxes.id"], ondelete="CASCADE"),
        sa.CheckConstraint("message_count >= 0", name="message_count_not_negative"),
    )
    # Per mailbox, not global: two mailboxes may both receive the same thread.
    op.create_index(
        "uq_threads_mailbox_provider_thread",
        "threads",
        ["mailbox_id", "provider_thread_id"],
        unique=True,
    )
    op.create_index(
        "ix_threads_organization_recent",
        "threads",
        ["organization_id", "last_message_at"],
    )

    op.create_table(
        "messages",
        _primary_key(),
        _organization_column(),
        sa.Column("mailbox_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("thread_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("provider_message_id", sa.Text(), nullable=False),
        # RFC 5322 Message-ID. Kept alongside the provider id because
        # deduplication across mailboxes needs the transport identity.
        sa.Column("rfc822_message_id", sa.Text(), nullable=True),
        sa.Column("direction", message_direction, nullable=False),
        sa.Column("from_address", sa.Text(), nullable=True),
        sa.Column("to_addresses", postgresql.ARRAY(sa.Text()), nullable=False,
                  server_default=sa.text("'{}'::text[]")),
        sa.Column("cc_addresses", postgresql.ARRAY(sa.Text()), nullable=False,
                  server_default=sa.text("'{}'::text[]")),
        sa.Column("subject", sa.Text(), nullable=True),
        sa.Column("body_text", sa.Text(), nullable=True),
        sa.Column("snippet", sa.Text(), nullable=True),
        sa.Column("has_attachments", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("sent_at", sa.TIMESTAMP(timezone=True), nullable=False),
        sa.Column("received_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("deleted_at", sa.TIMESTAMP(timezone=True), nullable=True),
        *_timestamps(),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["mailbox_id"], ["mailboxes.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["thread_id"], ["threads.id"], ondelete="CASCADE"),
    )
    # The idempotency guarantee for ingestion: redelivery of the same provider
    # message into the same mailbox is a no-op, not a duplicate ticket.
    op.create_index(
        "uq_messages_mailbox_provider_message",
        "messages",
        ["mailbox_id", "provider_message_id"],
        unique=True,
    )
    op.create_index("ix_messages_thread_sent", "messages", ["thread_id", "sent_at"])
    op.create_index(
        "ix_messages_organization_sent", "messages", ["organization_id", "sent_at"]
    )
    op.create_index(
        "ix_messages_rfc822",
        "messages",
        ["organization_id", "rfc822_message_id"],
        postgresql_where=sa.text("rfc822_message_id IS NOT NULL"),
    )

    op.create_table(
        "attachments",
        _primary_key(),
        _organization_column(),
        sa.Column("message_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("provider_attachment_id", sa.Text(), nullable=True),
        sa.Column("filename", sa.Text(), nullable=False),
        sa.Column("content_type", sa.Text(), nullable=True),
        sa.Column("size_bytes", sa.BigInteger(), nullable=False),
        sa.Column("storage_object", sa.Text(), nullable=True),
        sa.Column("sha256", sa.Text(), nullable=True),
        # Attachments are untrusted input. Nothing may read one until the scan
        # state says it is clean (C13 owns the scanner itself).
        sa.Column("scan_state", sa.Text(), nullable=False, server_default=sa.text("'PENDING'")),
        sa.Column("scanned_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("deleted_at", sa.TIMESTAMP(timezone=True), nullable=True),
        *_timestamps(),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["message_id"], ["messages.id"], ondelete="CASCADE"),
        sa.CheckConstraint("size_bytes >= 0", name="size_not_negative"),
        sa.CheckConstraint(
            "scan_state in ('PENDING', 'CLEAN', 'INFECTED', 'FAILED', 'SKIPPED')",
            name="scan_state_is_known",
        ),
        sa.CheckConstraint(
            "scan_state <> 'CLEAN' OR storage_object IS NOT NULL",
            name="clean_attachment_has_an_object",
        ),
    )
    op.create_index("ix_attachments_message", "attachments", ["message_id"])
    op.create_index(
        "ix_attachments_scan_state", "attachments", ["organization_id", "scan_state"]
    )

    op.create_table(
        "tickets",
        _primary_key(),
        _organization_column(),
        sa.Column("mailbox_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("thread_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("queue_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("department_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("assigned_membership_id", postgresql.UUID(as_uuid=True), nullable=True),
        # Human-facing sequential reference, unique per organization.
        sa.Column("reference", sa.BigInteger(), nullable=False),
        sa.Column("subject", sa.Text(), nullable=True),
        sa.Column("status", ticket_status, nullable=False, server_default=sa.text("'NEW'")),
        sa.Column("route", ticket_route, nullable=True),
        sa.Column("category", sa.Text(), nullable=True),
        sa.Column("urgency", ticket_urgency, nullable=True),
        sa.Column("confidence", sa.Numeric(4, 3), nullable=True),
        sa.Column("customer_address", sa.Text(), nullable=True),
        # Optimistic locking for C12: a reviewer acting on a stale view loses.
        sa.Column("version", sa.Integer(), nullable=False, server_default=sa.text("1")),
        sa.Column("first_response_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("resolved_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("closed_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("deleted_at", sa.TIMESTAMP(timezone=True), nullable=True),
        *_timestamps(),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["mailbox_id"], ["mailboxes.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["thread_id"], ["threads.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["queue_id"], ["queues.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["department_id"], ["departments.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(
            ["assigned_membership_id"], ["memberships.id"], ondelete="SET NULL"
        ),
        sa.CheckConstraint("version >= 1", name="version_starts_at_one"),
        sa.CheckConstraint(
            "confidence IS NULL OR (confidence >= 0 AND confidence <= 1)",
            name="confidence_is_a_probability",
        ),
        sa.CheckConstraint(
            "(status = 'RESOLVED') <= (resolved_at IS NOT NULL)",
            name="resolved_status_has_timestamp",
        ),
        sa.CheckConstraint(
            "(status = 'CLOSED') <= (closed_at IS NOT NULL)",
            name="closed_status_has_timestamp",
        ),
    )
    # One ticket per conversation in V1.
    op.create_index("uq_tickets_thread", "tickets", ["thread_id"], unique=True)
    op.create_index(
        "uq_tickets_organization_reference",
        "tickets",
        ["organization_id", "reference"],
        unique=True,
    )
    op.create_index(
        "ix_tickets_queue_status", "tickets", ["organization_id", "queue_id", "status"]
    )
    op.create_index(
        "ix_tickets_assignee_status",
        "tickets",
        ["organization_id", "assigned_membership_id", "status"],
    )
    op.create_index(
        "ix_tickets_open_updated",
        "tickets",
        ["organization_id", "updated_at"],
        postgresql_where=sa.text("status NOT IN ('RESOLVED', 'CLOSED') AND deleted_at IS NULL"),
    )

    # Per-organization ticket numbering. A shared sequence would leak one
    # organization's volume to another once this core runs multi-tenant.
    op.execute(
        """
        CREATE OR REPLACE FUNCTION assign_ticket_reference()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        BEGIN
            IF NEW.reference IS NULL OR NEW.reference = 0 THEN
                SELECT coalesce(max(reference), 0) + 1
                  INTO NEW.reference
                  FROM tickets
                 WHERE organization_id = NEW.organization_id;
            END IF;
            RETURN NEW;
        END;
        $$;
        """
    )
    op.execute(
        """
        CREATE TRIGGER tickets_assign_reference
        BEFORE INSERT ON tickets
        FOR EACH ROW EXECUTE FUNCTION assign_ticket_reference();
        """
    )
    op.alter_column("tickets", "reference", server_default=sa.text("0"))

    op.create_table(
        "ticket_assignments",
        _primary_key(),
        _organization_column(),
        sa.Column("ticket_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("membership_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("assigned_by_membership_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column(
            "assigned_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("unassigned_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["ticket_id"], ["tickets.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["membership_id"], ["memberships.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(
            ["assigned_by_membership_id"], ["memberships.id"], ondelete="SET NULL"
        ),
    )
    op.create_index(
        "ix_ticket_assignments_ticket", "ticket_assignments", ["ticket_id", "assigned_at"]
    )
    # At most one open assignment per ticket.
    op.create_index(
        "uq_ticket_assignments_current",
        "ticket_assignments",
        ["ticket_id"],
        unique=True,
        postgresql_where=sa.text("unassigned_at IS NULL"),
    )

    # ------------------------------------------------------------------
    # Per-user seen state
    # ------------------------------------------------------------------

    # Gmail's UNREAD label is shared across everyone with mailbox access, so it
    # cannot answer "has *this* agent seen this ticket". This table can.
    op.create_table(
        "ticket_seen_state",
        _primary_key(),
        _organization_column(),
        sa.Column("ticket_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("membership_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("first_seen_at", sa.TIMESTAMP(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.Column("last_seen_at", sa.TIMESTAMP(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        # Compared against the ticket's latest message to decide "new to me".
        sa.Column("seen_through_message_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["ticket_id"], ["tickets.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["membership_id"], ["memberships.id"], ondelete="CASCADE"),
    )
    op.create_index(
        "uq_ticket_seen_state_pair",
        "ticket_seen_state",
        ["ticket_id", "membership_id"],
        unique=True,
    )
    op.create_index(
        "ix_ticket_seen_state_membership", "ticket_seen_state", ["membership_id", "last_seen_at"]
    )

    # ------------------------------------------------------------------
    # SLA
    # ------------------------------------------------------------------

    op.create_table(
        "sla_policies",
        _primary_key(),
        _organization_column(),
        sa.Column("department_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("queue_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("urgency", ticket_urgency, nullable=True),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("first_response_minutes", sa.Integer(), nullable=False),
        sa.Column("resolution_minutes", sa.Integer(), nullable=True),
        # Business hours are evaluated in a recorded zone, never the server's.
        sa.Column("time_zone", sa.Text(), nullable=False, server_default=sa.text("'Etc/UTC'")),
        sa.Column("business_hours_only", sa.Boolean(), nullable=False,
                  server_default=sa.text("true")),
        sa.Column("business_day_start_minute", sa.Integer(), nullable=False,
                  server_default=sa.text("540")),
        sa.Column("business_day_end_minute", sa.Integer(), nullable=False,
                  server_default=sa.text("1020")),
        sa.Column("business_days", postgresql.ARRAY(sa.SmallInteger()), nullable=False,
                  server_default=sa.text("'{1,2,3,4,5}'::smallint[]")),
        sa.Column("version", sa.Integer(), nullable=False, server_default=sa.text("1")),
        sa.Column("effective_from", sa.TIMESTAMP(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.Column("effective_to", sa.TIMESTAMP(timezone=True), nullable=True),
        *_timestamps(),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["department_id"], ["departments.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["queue_id"], ["queues.id"], ondelete="CASCADE"),
        sa.CheckConstraint("first_response_minutes > 0", name="first_response_is_positive"),
        sa.CheckConstraint(
            "resolution_minutes IS NULL OR resolution_minutes >= first_response_minutes",
            name="resolution_is_not_before_first_response",
        ),
        sa.CheckConstraint(
            "business_day_start_minute >= 0 AND business_day_end_minute <= 1440 "
            "AND business_day_start_minute < business_day_end_minute",
            name="business_hours_are_a_valid_range",
        ),
        sa.CheckConstraint(
            "effective_to IS NULL OR effective_to > effective_from",
            name="effective_window_is_ordered",
        ),
    )
    op.create_index(
        "ix_sla_policies_scope",
        "sla_policies",
        ["organization_id", "queue_id", "department_id", "urgency"],
    )

    op.create_table(
        "ticket_sla_states",
        _primary_key(),
        _organization_column(),
        sa.Column("ticket_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("sla_policy_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("first_response_due_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("resolution_due_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("state", sla_state, nullable=False, server_default=sa.text("'ON_TRACK'")),
        sa.Column("breached_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("paused_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("paused_minutes", sa.Integer(), nullable=False, server_default=sa.text("0")),
        *_timestamps(),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["ticket_id"], ["tickets.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["sla_policy_id"], ["sla_policies.id"], ondelete="SET NULL"),
        sa.CheckConstraint("paused_minutes >= 0", name="paused_minutes_not_negative"),
        sa.CheckConstraint(
            "(state = 'BREACHED') = (breached_at IS NOT NULL)",
            name="breached_state_matches_timestamp",
        ),
    )
    op.create_index("uq_ticket_sla_states_ticket", "ticket_sla_states", ["ticket_id"], unique=True)
    op.create_index(
        "ix_ticket_sla_states_due",
        "ticket_sla_states",
        ["organization_id", "first_response_due_at"],
        postgresql_where=sa.text("state IN ('ON_TRACK', 'AT_RISK')"),
    )


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS tickets_assign_reference ON tickets;")
    op.execute("DROP FUNCTION IF EXISTS assign_ticket_reference();")

    for table in (
        "ticket_sla_states",
        "sla_policies",
        "ticket_seen_state",
        "ticket_assignments",
        "tickets",
        "attachments",
        "messages",
        "threads",
        "mailbox_permissions",
        "mailboxes",
        "queues",
    ):
        op.drop_table(table)

    for enum_name in (
        "sla_state",
        "message_direction",
        "ticket_urgency",
        "ticket_route",
        "ticket_status",
        "mailbox_status",
        "mailbox_kind",
    ):
        op.execute(f"DROP TYPE IF EXISTS {enum_name};")
