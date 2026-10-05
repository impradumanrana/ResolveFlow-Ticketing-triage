"""The client's AI gateway: approvals, budgets, and a metadata-only call log.

C04 created `ai_configs` (a Secret Manager reference, never a key) and daily
`provider_usage`. C10 adds what the gateway needs to refuse a call *before* it
happens and to explain every call afterwards:

* `ai_model_approvals` - which provider, model, operation, and region the
  client has approved, at what agreed price. Unapproved means never called.
* `provider_budget_ledgers` - spend and in-flight reservations per provider
  per month. A call reserves its worst-case cost with a conditional update, so
  two concurrent calls cannot both squeeze under the budget.
* `provider_calls` - one row per attempt: who, which model, outcome, code,
  tokens, cost, latency. It has no column that could hold a prompt, an answer,
  or provider error text.

Revision ID: 20260916_0008
Revises: 20260916_0007
Create Date: 2026-09-16
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260916_0008"
down_revision: str | Sequence[str] | None = "20260916_0007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

OPERATIONS = "operation IN ('classification', 'generation', 'embedding')"


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
    # ------------------------------------------------------------------
    # ai_configs: failure visibility, same-provider fallback, strict reference
    # ------------------------------------------------------------------
    op.add_column(
        "ai_configs", sa.Column("fallback_classification_model", sa.Text(), nullable=True)
    )
    op.add_column("ai_configs", sa.Column("fallback_generation_model", sa.Text(), nullable=True))
    op.add_column("ai_configs", sa.Column("last_failure_code", sa.Text(), nullable=True))
    op.add_column(
        "ai_configs", sa.Column("last_failure_at", sa.TIMESTAMP(timezone=True), nullable=True)
    )
    op.add_column(
        "ai_configs", sa.Column("last_success_at", sa.TIMESTAMP(timezone=True), nullable=True)
    )
    # C04 refused known key prefixes. This goes further: the column accepts
    # only the shape of a Secret Manager resource name, so no key of any
    # provider's format can be stored in it.
    op.create_check_constraint(
        "credential_reference_is_a_secret_name",
        "ai_configs",
        "credential_secret_name ~ '^projects/[a-z0-9-]{1,63}/secrets/[A-Za-z0-9_-]{1,255}$'",
    )
    # Verification and failure fields hold a code, never provider text.
    op.create_check_constraint(
        "failure_fields_hold_codes",
        "ai_configs",
        "(last_failure_code IS NULL OR last_failure_code ~ '^[A-Z_]{3,64}$') "
        "AND (last_verification_error IS NULL OR last_verification_error ~ '^[A-Z_]{3,64}$')",
    )
    op.create_check_constraint(
        "fallback_is_another_provider",
        "ai_configs",
        "fallback_provider IS NULL OR fallback_provider <> provider",
    )

    op.add_column(
        "provider_usage",
        sa.Column(
            "failed_request_count", sa.BigInteger(), nullable=False, server_default=sa.text("0")
        ),
    )
    op.add_column(
        "provider_usage",
        sa.Column(
            "estimated_cost_micro_units",
            sa.BigInteger(),
            nullable=False,
            server_default=sa.text("0"),
        ),
    )
    op.create_check_constraint(
        "failed_request_count_not_negative", "provider_usage", "failed_request_count >= 0"
    )
    op.create_check_constraint(
        "estimated_cost_micro_not_negative", "provider_usage", "estimated_cost_micro_units >= 0"
    )

    # ------------------------------------------------------------------
    # Approvals
    # ------------------------------------------------------------------
    op.create_table(
        "ai_model_approvals",
        _id(),
        _organization(),
        sa.Column("provider", sa.Text(), nullable=False),
        sa.Column("model", sa.Text(), nullable=False),
        sa.Column("operation", sa.Text(), nullable=False),
        sa.Column("region", sa.Text(), nullable=False),
        # Agreed prices, in minor currency units per million tokens.
        sa.Column("input_price_per_million_minor", sa.BigInteger(), nullable=False),
        sa.Column(
            "output_price_per_million_minor",
            sa.BigInteger(),
            nullable=False,
            server_default=sa.text("0"),
        ),
        sa.Column("max_output_tokens", sa.Integer(), nullable=True),
        sa.Column("approved_by_membership_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column(
            "approved_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("revoked_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["approved_by_membership_id"], ["memberships.id"], ondelete="SET NULL"
        ),
        sa.CheckConstraint(OPERATIONS, name="operation_is_known"),
        sa.CheckConstraint(
            "length(btrim(provider)) > 0 AND length(btrim(model)) > 0",
            name="provider_and_model_not_blank",
        ),
        sa.CheckConstraint("length(btrim(region)) > 0", name="region_not_blank"),
        sa.CheckConstraint(
            "input_price_per_million_minor >= 0 AND output_price_per_million_minor >= 0",
            name="prices_not_negative",
        ),
        sa.CheckConstraint(
            "max_output_tokens IS NULL OR max_output_tokens BETWEEN 1 AND 200000",
            name="max_output_tokens_is_bounded",
        ),
        # An embedding produces no output tokens, so it has no output price.
        sa.CheckConstraint(
            "operation <> 'embedding' OR (output_price_per_million_minor = 0 "
            "AND max_output_tokens IS NULL)",
            name="embeddings_have_no_output",
        ),
        sa.CheckConstraint(
            "revoked_at IS NULL OR revoked_at >= approved_at", name="revoked_after_approval"
        ),
    )
    op.create_index(
        "uq_ai_model_approvals_current",
        "ai_model_approvals",
        ["organization_id", "provider", "model", "operation", "region"],
        unique=True,
        postgresql_where=sa.text("revoked_at IS NULL"),
    )

    # ------------------------------------------------------------------
    # Budget ledger
    # ------------------------------------------------------------------
    op.create_table(
        "provider_budget_ledgers",
        _id(),
        _organization(),
        sa.Column("provider", sa.Text(), nullable=False),
        sa.Column("period_month", sa.Date(), nullable=False),
        sa.Column(
            "spent_micro_units", sa.BigInteger(), nullable=False, server_default=sa.text("0")
        ),
        sa.Column(
            "reserved_micro_units", sa.BigInteger(), nullable=False, server_default=sa.text("0")
        ),
        sa.Column(
            "updated_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"], ondelete="CASCADE"),
        sa.CheckConstraint("extract(day from period_month) = 1", name="period_is_a_month"),
        sa.CheckConstraint("spent_micro_units >= 0", name="spent_not_negative"),
        # A reservation released twice would drive this negative and silently
        # raise the effective budget.
        sa.CheckConstraint("reserved_micro_units >= 0", name="reserved_not_negative"),
    )
    op.create_index(
        "uq_provider_budget_ledgers_period",
        "provider_budget_ledgers",
        ["organization_id", "provider", "period_month"],
        unique=True,
    )

    # ------------------------------------------------------------------
    # Call log - metadata only
    # ------------------------------------------------------------------
    op.create_table(
        "provider_calls",
        _id(),
        _organization(),
        sa.Column("correlation_id", sa.Text(), nullable=False),
        sa.Column("operation", sa.Text(), nullable=False),
        sa.Column("provider", sa.Text(), nullable=False),
        sa.Column("model", sa.Text(), nullable=False),
        sa.Column("region", sa.Text(), nullable=True),
        sa.Column("purpose", sa.Text(), nullable=False, server_default=sa.text("'CALL'")),
        sa.Column("outcome", sa.Text(), nullable=False),
        sa.Column("failure_code", sa.Text(), nullable=True),
        sa.Column("http_status", sa.SmallInteger(), nullable=True),
        sa.Column("fallback_from", sa.Text(), nullable=True),
        sa.Column("period_month", sa.Date(), nullable=True),
        sa.Column(
            "reserved_micro_units", sa.BigInteger(), nullable=False, server_default=sa.text("0")
        ),
        sa.Column("reservation_state", sa.Text(), nullable=False, server_default=sa.text("'NONE'")),
        sa.Column("cost_micro_units", sa.BigInteger(), nullable=False, server_default=sa.text("0")),
        sa.Column("prompt_tokens", sa.Integer(), nullable=True),
        sa.Column("completion_tokens", sa.Integer(), nullable=True),
        sa.Column("latency_ms", sa.Integer(), nullable=True),
        sa.Column(
            "started_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("finished_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"], ondelete="CASCADE"),
        sa.CheckConstraint(OPERATIONS, name="operation_is_known"),
        sa.CheckConstraint("purpose IN ('CALL', 'REPAIR', 'VERIFY')", name="purpose_is_known"),
        sa.CheckConstraint(
            "outcome IN ('PENDING', 'SUCCEEDED', 'FAILED', 'REFUSED')", name="outcome_is_known"
        ),
        sa.CheckConstraint(
            "reservation_state IN ('NONE', 'HELD', 'SETTLED', 'RELEASED')",
            name="reservation_state_is_known",
        ),
        # A failure always says why; a success never carries a failure code.
        sa.CheckConstraint(
            "(outcome IN ('FAILED', 'REFUSED')) = (failure_code IS NOT NULL)",
            name="failures_have_a_code",
        ),
        sa.CheckConstraint(
            "failure_code IS NULL OR failure_code ~ '^[A-Z_]{3,64}$'",
            name="failure_code_is_a_code",
        ),
        # Only an in-flight call may hold a reservation, and every held
        # reservation names the ledger period it came from.
        sa.CheckConstraint(
            "(reservation_state = 'HELD') = (outcome = 'PENDING')",
            name="only_pending_calls_hold_money",
        ),
        sa.CheckConstraint(
            "reservation_state = 'NONE' OR period_month IS NOT NULL",
            name="reservations_name_a_period",
        ),
        sa.CheckConstraint(
            "reserved_micro_units >= 0 AND cost_micro_units >= 0 "
            "AND coalesce(prompt_tokens, 0) >= 0 AND coalesce(completion_tokens, 0) >= 0 "
            "AND coalesce(latency_ms, 0) >= 0",
            name="measurements_not_negative",
        ),
        sa.CheckConstraint(
            "(outcome = 'PENDING') = (finished_at IS NULL)", name="finished_calls_have_an_end"
        ),
        sa.CheckConstraint(
            "length(correlation_id) BETWEEN 1 AND 128", name="correlation_id_is_bounded"
        ),
    )
    op.create_index("ix_provider_calls_recent", "provider_calls", ["organization_id", "started_at"])
    op.create_index(
        "ix_provider_calls_correlation", "provider_calls", ["organization_id", "correlation_id"]
    )
    op.create_index(
        "ix_provider_calls_held",
        "provider_calls",
        ["started_at"],
        postgresql_where=sa.text("reservation_state = 'HELD'"),
    )


def downgrade() -> None:
    op.drop_table("provider_calls")
    op.drop_table("provider_budget_ledgers")
    op.drop_table("ai_model_approvals")
    op.drop_constraint("estimated_cost_micro_not_negative", "provider_usage", type_="check")
    op.drop_constraint("failed_request_count_not_negative", "provider_usage", type_="check")
    op.drop_column("provider_usage", "estimated_cost_micro_units")
    op.drop_column("provider_usage", "failed_request_count")
    op.drop_constraint("fallback_is_another_provider", "ai_configs", type_="check")
    op.drop_constraint("failure_fields_hold_codes", "ai_configs", type_="check")
    op.drop_constraint("credential_reference_is_a_secret_name", "ai_configs", type_="check")
    for column in (
        "last_success_at",
        "last_failure_at",
        "last_failure_code",
        "fallback_generation_model",
        "fallback_classification_model",
    ):
        op.drop_column("ai_configs", column)
