"""Versioned routing rules, VIP contacts, and holiday calendars.

C04 modelled service-level policies with business hours and a time zone. C09
adds the rules that decide *where* a conversation goes and *who* counts as
important, plus the holidays those business hours must skip.

Rules are versioned rather than edited in place. "Why was this ticket routed
here in March" is a question an operator will ask, and an overwritten rule
cannot answer it. Each version carries an effective window; the engine
evaluates only versions in force at the moment being decided.

Revision ID: 20260916_0007
Revises: 20260915_0006
Create Date: 2026-09-16
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260916_0007"
down_revision: str | Sequence[str] | None = "20260915_0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "routing_rules",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("organization_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        # Lower runs first. Two enabled rules at the same priority that set the
        # same field to different values are a conflict the engine refuses to
        # guess about.
        sa.Column("priority", sa.Integer(), nullable=False, server_default=sa.text("100")),
        sa.Column("version", sa.Integer(), nullable=False, server_default=sa.text("1")),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column(
            "effective_from",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("effective_to", sa.TIMESTAMP(timezone=True), nullable=True),
        # A closed condition language, stored as data. Not code, not a template:
        # a rule must never be able to do more than match on known fields.
        sa.Column(
            "conditions", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")
        ),
        sa.Column(
            "actions", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")
        ),
        sa.Column("created_by_membership_id", postgresql.UUID(as_uuid=True), nullable=True),
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
        sa.ForeignKeyConstraint(
            ["created_by_membership_id"], ["memberships.id"], ondelete="SET NULL"
        ),
        sa.CheckConstraint("length(btrim(name)) > 0", name="name_not_blank"),
        sa.CheckConstraint("priority >= 0 AND priority <= 10000", name="priority_is_bounded"),
        sa.CheckConstraint("version >= 1", name="version_starts_at_one"),
        sa.CheckConstraint(
            "effective_to IS NULL OR effective_to > effective_from",
            name="effective_window_is_ordered",
        ),
        sa.CheckConstraint("jsonb_typeof(conditions) = 'object'", name="conditions_are_an_object"),
        sa.CheckConstraint("jsonb_typeof(actions) = 'object'", name="actions_are_an_object"),
        # A rule that changes nothing is a configuration mistake that would
        # silently do nothing at evaluation time.
        sa.CheckConstraint("actions <> '{}'::jsonb", name="a_rule_must_do_something"),
    )
    op.create_index(
        "uq_routing_rules_name_version",
        "routing_rules",
        ["organization_id", "name", "version"],
        unique=True,
    )
    op.create_index(
        "ix_routing_rules_in_force",
        "routing_rules",
        ["organization_id", "priority"],
        postgresql_where=sa.text("enabled"),
    )

    op.create_table(
        "vip_contacts",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("organization_id", postgresql.UUID(as_uuid=True), nullable=False),
        # Exactly one of these. A row that matched both would be ambiguous.
        sa.Column("email_address", sa.Text(), nullable=True),
        sa.Column("email_domain", sa.Text(), nullable=True),
        sa.Column("tier", sa.Text(), nullable=False, server_default=sa.text("'VIP'")),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"], ondelete="CASCADE"),
        sa.CheckConstraint(
            "(email_address IS NULL) <> (email_domain IS NULL)",
            name="exactly_one_of_address_or_domain",
        ),
        sa.CheckConstraint(
            "email_address IS NULL OR email_address = lower(email_address)",
            name="address_is_lowercase",
        ),
        sa.CheckConstraint(
            "email_domain IS NULL OR email_domain = lower(email_domain)",
            name="domain_is_lowercase",
        ),
        sa.CheckConstraint("length(btrim(tier)) > 0", name="tier_not_blank"),
    )
    op.create_index(
        "uq_vip_contacts_address",
        "vip_contacts",
        ["organization_id", "email_address"],
        unique=True,
        postgresql_where=sa.text("email_address IS NOT NULL"),
    )
    op.create_index(
        "uq_vip_contacts_domain",
        "vip_contacts",
        ["organization_id", "email_domain"],
        unique=True,
        postgresql_where=sa.text("email_domain IS NOT NULL"),
    )

    # Business hours without holidays promise a response on a public holiday.
    op.create_table(
        "business_holidays",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("organization_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("department_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("holiday_date", sa.Date(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["department_id"], ["departments.id"], ondelete="CASCADE"),
        sa.CheckConstraint("length(btrim(name)) > 0", name="name_not_blank"),
    )
    op.create_index(
        "uq_business_holidays_organization",
        "business_holidays",
        ["organization_id", "holiday_date"],
        unique=True,
        postgresql_where=sa.text("department_id IS NULL"),
    )
    op.create_index(
        "uq_business_holidays_department",
        "business_holidays",
        ["organization_id", "department_id", "holiday_date"],
        unique=True,
        postgresql_where=sa.text("department_id IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_table("business_holidays")
    op.drop_table("vip_contacts")
    op.drop_table("routing_rules")
