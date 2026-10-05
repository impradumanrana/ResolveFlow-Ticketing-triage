"""A record of each data-subject erasure (C13).

An erasure destroys content, which means afterwards there is nothing left to
show that it was done. This table is that evidence, and it is deliberately
built so the evidence is not itself a copy of what was erased:

* the subject is a **SHA-256 of the address**, so a repeat request can be
  recognised as already satisfied without the table holding the address;
* `counts` records how many rows in each table were scrubbed, which is what
  makes the operation auditable;
* `storage_objects` names the attachment objects an operator still has to
  delete from the bucket, because that is a different system and the row here
  cannot reach it.

Append-only by the same trigger C04 uses for `audit_events`: a destruction
record that can be edited is not a record.

Revision ID: 20260920_0012
Revises: 20260919_0011
Create Date: 2026-10-05
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260920_0012"
down_revision: str | Sequence[str] | None = "20260919_0011"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "erasure_records",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("organization_id", postgresql.UUID(as_uuid=True), nullable=False),
        # SHA-256 of the lowercased address. The shape is constrained so a bug
        # that writes the address itself fails loudly rather than storing it.
        sa.Column("subject_digest", sa.Text(), nullable=False),
        sa.Column("requested_by_membership_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("counts", postgresql.JSONB(), nullable=False),
        sa.Column(
            "storage_objects",
            postgresql.ARRAY(sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'"),
        ),
        sa.Column("completed_at", sa.TIMESTAMP(timezone=True), nullable=False),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"], ondelete="CASCADE"),
        # RESTRICT, not SET NULL: an erasure must stay attributable to the
        # person who ordered it, so their membership cannot be deleted out
        # from under the record.
        sa.ForeignKeyConstraint(
            ["requested_by_membership_id"], ["memberships.id"], ondelete="RESTRICT"
        ),
        sa.CheckConstraint("subject_digest ~ '^[0-9a-f]{64}$'", name="subject_is_a_sha256_digest"),
        sa.CheckConstraint("reason IS NULL OR length(reason) <= 2000", name="reason_is_bounded"),
        sa.CheckConstraint("jsonb_typeof(counts) = 'object'", name="counts_is_an_object"),
    )
    op.create_index(
        "ix_erasure_records_subject",
        "erasure_records",
        ["organization_id", "subject_digest"],
    )

    # The same append-only guarantee as audit_events, reusing C04's trigger
    # function rather than defining a second one that could drift from it.
    op.execute(
        "CREATE TRIGGER erasure_records_no_update_or_delete "
        "BEFORE UPDATE OR DELETE ON erasure_records "
        "FOR EACH ROW EXECUTE FUNCTION audit_events_append_only()"
    )


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS erasure_records_no_update_or_delete ON erasure_records")
    op.drop_table("erasure_records")
