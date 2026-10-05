"""Counters for the rate limits (C13).

One row per (organization, policy, key, window). The primary key is the
conflict target for the limiter's single atomic upsert, which is what makes the
count correct under concurrency - a read-then-write limiter does not limit
anything when it matters.

The key is a SHA-256, never the identifier. Counters are keyed by membership,
mailbox or address, and a control that exists to protect the system must not
become another store of personal data (C-D009). It also means these rows are
outside the scope of an erasure request: there is nothing in them to erase.

Rows are evidence of a burst for a day or two and noise afterwards, so the C04
retention sweep removes them like anything else.

Revision ID: 20260919_0011
Revises: 20260918_0010
Create Date: 2026-10-05
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260919_0011"
down_revision: str | Sequence[str] | None = "20260918_0010"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "rate_limit_counters",
        sa.Column("organization_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("policy", sa.Text(), nullable=False),
        # SHA-256 of the limited key. The shape is constrained so a bug that
        # writes a raw identifier here fails loudly instead of leaking one.
        sa.Column("key_hash", sa.Text(), nullable=False),
        sa.Column("window_start", sa.TIMESTAMP(timezone=True), nullable=False),
        sa.Column("request_count", sa.Integer(), nullable=False, server_default=sa.text("1")),
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
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint(
            "organization_id",
            "policy",
            "key_hash",
            "window_start",
            name="pk_rate_limit_counters",
        ),
        sa.CheckConstraint("key_hash ~ '^[0-9a-f]{64}$'", name="key_is_a_sha256_digest"),
        sa.CheckConstraint("policy ~ '^[a-z][a-z0-9_]{2,63}$'", name="policy_is_an_identifier"),
        sa.CheckConstraint("request_count > 0", name="count_is_positive"),
        # Deliberately no constraint relating `window_start` to a timestamp the
        # database generates. The window comes from the application's clock and
        # `updated_at` from the database's; a few seconds of skew between them
        # would make the insert fail at a window boundary, and a limiter that
        # fails closed on clock skew refuses legitimate work. Every constraint
        # here is about the row's own integrity, which needs no second clock.
    )
    # The sweep deletes by age; nothing else scans this table.
    op.create_index("ix_rate_limit_counters_window", "rate_limit_counters", ["window_start"])


def downgrade() -> None:
    op.drop_table("rate_limit_counters")
