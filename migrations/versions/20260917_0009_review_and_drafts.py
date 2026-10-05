"""Human review decisions and provider drafts.

C04 already records *that* something happened: `actions` carries the actor, the
reason, the ticket version before and after, and an idempotency key unique per
organization. C12 adds the two artefacts a review produces, which an action
row cannot hold:

* `draft_revisions` - the reply text at each revision. The model's output is
  revision 1 and is never overwritten; a human edit is a new revision with its
  author. "What did the model actually write" has to stay answerable after
  someone improves it.
* `provider_drafts` - what was created in the mail provider. A row is claimed
  before the provider is called and settled after, so a retry cannot create a
  second draft in the client's mailbox.

Neither table has a column that could represent a send, and a schema test
asserts that: the product creates drafts for a person to send, and never sends
(C-D010).

Revision ID: 20260917_0009
Revises: 20260916_0008
Create Date: 2026-10-04
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260917_0009"
down_revision: str | Sequence[str] | None = "20260916_0008"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

MAX_BODY_CHARACTERS = 20000


def _id() -> sa.Column:
    return sa.Column(
        "id",
        postgresql.UUID(as_uuid=True),
        primary_key=True,
        server_default=sa.text("gen_random_uuid()"),
    )


def _organization() -> sa.Column:
    return sa.Column("organization_id", postgresql.UUID(as_uuid=True), nullable=False)


def upgrade() -> None:
    op.create_table(
        "draft_revisions",
        _id(),
        _organization(),
        sa.Column("ticket_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("triage_run_id", postgresql.UUID(as_uuid=True), nullable=True),
        # 1 is always the model's own candidate; a human edit is 2, 3, ...
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("source", sa.Text(), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("body_sha256", sa.Text(), nullable=False),
        sa.Column(
            "citations",
            postgresql.ARRAY(sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::text[]"),
        ),
        sa.Column("created_by_membership_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["ticket_id"], ["tickets.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["triage_run_id"], ["triage_runs.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(
            ["created_by_membership_id"], ["memberships.id"], ondelete="SET NULL"
        ),
        sa.CheckConstraint("source IN ('MODEL', 'HUMAN')", name="source_is_known"),
        sa.CheckConstraint("revision >= 1", name="revision_starts_at_one"),
        # Attributability: a human revision names the person who wrote it, and
        # the model's revision never claims one.
        sa.CheckConstraint(
            "(source = 'HUMAN') = (created_by_membership_id IS NOT NULL)",
            name="human_revisions_have_an_author",
        ),
        sa.CheckConstraint(
            "source <> 'MODEL' OR revision = 1", name="the_model_writes_revision_one"
        ),
        sa.CheckConstraint(
            f"length(btrim(body)) > 0 AND length(body) <= {MAX_BODY_CHARACTERS}",
            name="body_is_present_and_bounded",
        ),
        sa.CheckConstraint("body_sha256 ~ '^[0-9a-f]{64}$'", name="body_digest_is_a_sha256"),
    )
    op.create_index(
        "uq_draft_revisions_ticket_revision",
        "draft_revisions",
        ["ticket_id", "revision"],
        unique=True,
    )
    op.create_index("ix_draft_revisions_ticket", "draft_revisions", ["ticket_id", "created_at"])

    op.create_table(
        "provider_drafts",
        _id(),
        _organization(),
        sa.Column("ticket_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("mailbox_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("revision_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("triage_run_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("provider", sa.Text(), nullable=False, server_default=sa.text("'gmail'")),
        sa.Column("provider_draft_id", sa.Text(), nullable=True),
        sa.Column("provider_thread_id", sa.Text(), nullable=True),
        sa.Column("status", sa.Text(), nullable=False, server_default=sa.text("'PENDING'")),
        sa.Column("failure_code", sa.Text(), nullable=True),
        # A draft exists only because a person approved it.
        sa.Column("approved_by_membership_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("idempotency_key", sa.Text(), nullable=False),
        sa.Column("body_sha256", sa.Text(), nullable=False),
        sa.Column(
            "claimed_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("settled_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["ticket_id"], ["tickets.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["mailbox_id"], ["mailboxes.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["revision_id"], ["draft_revisions.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["triage_run_id"], ["triage_runs.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(
            ["approved_by_membership_id"], ["memberships.id"], ondelete="RESTRICT"
        ),
        sa.CheckConstraint("provider IN ('gmail')", name="provider_is_supported"),
        sa.CheckConstraint(
            "status IN ('PENDING', 'CREATED', 'FAILED', 'REFUSED')", name="status_is_known"
        ),
        # Claimed before the call, settled after: a created draft names its
        # provider id, and anything else says why it does not.
        sa.CheckConstraint(
            "(status = 'CREATED') = (provider_draft_id IS NOT NULL)",
            name="created_drafts_have_a_provider_id",
        ),
        sa.CheckConstraint(
            "(status IN ('FAILED', 'REFUSED')) = (failure_code IS NOT NULL)",
            name="unsuccessful_drafts_say_why",
        ),
        sa.CheckConstraint(
            "failure_code IS NULL OR failure_code ~ '^[A-Z_]{3,64}$'",
            name="failure_code_is_a_code",
        ),
        sa.CheckConstraint(
            "(status = 'PENDING') = (settled_at IS NULL)", name="settled_drafts_have_a_time"
        ),
        sa.CheckConstraint("body_sha256 ~ '^[0-9a-f]{64}$'", name="body_digest_is_a_sha256"),
        sa.CheckConstraint(
            "length(idempotency_key) BETWEEN 8 AND 200", name="idempotency_key_is_bounded"
        ),
    )
    op.create_index(
        "uq_provider_drafts_idempotency",
        "provider_drafts",
        ["organization_id", "idempotency_key"],
        unique=True,
    )
    # One live draft per ticket: approving twice replaces nothing silently.
    op.create_index(
        "uq_provider_drafts_live_per_ticket",
        "provider_drafts",
        ["ticket_id"],
        unique=True,
        postgresql_where=sa.text("status IN ('PENDING', 'CREATED')"),
    )
    op.create_index(
        "ix_provider_drafts_pending",
        "provider_drafts",
        ["claimed_at"],
        postgresql_where=sa.text("status = 'PENDING'"),
    )


def downgrade() -> None:
    op.drop_table("provider_drafts")
    op.drop_table("draft_revisions")
