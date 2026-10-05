"""Knowledge, AI configuration, jobs, usage, evaluations, actions, retention.

The second half of C04. Creates the pgvector extension and the embedding
storage the C05 retrieval migration will use, plus the governance tables that
make work attributable, repeatable, and deletable.

Two decisions worth reading before changing anything here:

* Embeddings record the model and dimension that produced them. A provider
  changing its embedding model silently would otherwise corrupt retrieval with
  no visible failure - vectors of the same length from different models are
  still comparable arithmetic, just meaningless.
* Retention is expressed as policy rows plus a computed `purge_after`, not as
  scattered `DELETE ... WHERE age > n` statements. A legal hold must be able to
  stop deletion, and a policy must be auditable.

Revision ID: 20260915_0004
Revises: 20260915_0003
Create Date: 2026-09-15
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260915_0004"
down_revision: str | Sequence[str] | None = "20260915_0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# text-embedding-3-small, matching the proven MVP configuration. Recorded per
# row so a change is a migration, not a silent corruption.
DEFAULT_EMBEDDING_DIMENSIONS = 1536

ARTICLE_STATUSES = ("DRAFT", "INDEXING", "PUBLISHED", "FAILED", "ARCHIVED")

JOB_STATUSES = ("QUEUED", "RUNNING", "SUCCEEDED", "FAILED", "DEAD_LETTERED", "CANCELLED")

ACTION_TYPES = (
    "TRIAGE_RUN",
    "DRAFT_APPROVED",
    "DRAFT_REJECTED",
    "DRAFT_EDITED",
    "TICKET_REROUTED",
    "TICKET_ASSIGNED",
    "TICKET_RESOLVED",
    "PROVIDER_DRAFT_CREATED",
    "KNOWLEDGE_INGESTED",
    "KNOWLEDGE_DELETED",
)

DATA_CLASSES = (
    "MESSAGE",
    "ATTACHMENT",
    "TICKET",
    "TRIAGE_RUN",
    "AUDIT_EVENT",
    "JOB",
    "USAGE",
    "EVALUATION",
)


def _timestamps() -> list[sa.Column]:
    return [
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.Column("updated_at", sa.TIMESTAMP(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
    ]


def _organization_column() -> sa.Column:
    return sa.Column("organization_id", postgresql.UUID(as_uuid=True), nullable=False)


def _primary_key() -> sa.Column:
    return sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True,
                     server_default=sa.text("gen_random_uuid()"))


def upgrade() -> None:
    bind = op.get_bind()

    # pgvector. C02 provisions the instance; the extension and every table are
    # owned by this chain so schema history has one source of truth.
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")

    for enum_name, enum_values in (
        ("article_status", ARTICLE_STATUSES),
        ("job_status", JOB_STATUSES),
        ("action_type", ACTION_TYPES),
        ("retention_data_class", DATA_CLASSES),
    ):
        postgresql.ENUM(*enum_values, name=enum_name).create(bind, checkfirst=True)

    article_status = postgresql.ENUM(*ARTICLE_STATUSES, name="article_status", create_type=False)
    job_status = postgresql.ENUM(*JOB_STATUSES, name="job_status", create_type=False)
    action_type = postgresql.ENUM(*ACTION_TYPES, name="action_type", create_type=False)
    retention_data_class = postgresql.ENUM(
        *DATA_CLASSES, name="retention_data_class", create_type=False
    )

    # ------------------------------------------------------------------
    # Knowledge
    # ------------------------------------------------------------------

    op.create_table(
        "knowledge_sources",
        _primary_key(),
        _organization_column(),
        sa.Column("filename", sa.Text(), nullable=False),
        sa.Column("content_type", sa.Text(), nullable=False),
        sa.Column("size_bytes", sa.BigInteger(), nullable=False),
        sa.Column("storage_object", sa.Text(), nullable=True),
        sa.Column("sha256", sa.Text(), nullable=False),
        sa.Column("uploaded_by_membership_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("parse_state", sa.Text(), nullable=False, server_default=sa.text("'PENDING'")),
        sa.Column("parse_error", sa.Text(), nullable=True),
        sa.Column("article_count", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("deleted_at", sa.TIMESTAMP(timezone=True), nullable=True),
        *_timestamps(),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["uploaded_by_membership_id"], ["memberships.id"], ondelete="SET NULL"
        ),
        sa.CheckConstraint("size_bytes > 0", name="size_is_positive"),
        sa.CheckConstraint("length(sha256) = 64", name="sha256_is_hex"),
        sa.CheckConstraint(
            "content_type IN ('application/pdf', 'text/markdown', 'text/plain', "
            "'text/html', 'text/csv')",
            name="content_type_is_approved",
        ),
        sa.CheckConstraint(
            "parse_state IN ('PENDING', 'PARSING', 'PARSED', 'FAILED')",
            name="parse_state_is_known",
        ),
    )
    # Re-uploading identical content is idempotent rather than duplicated.
    op.create_index(
        "uq_knowledge_sources_organization_sha",
        "knowledge_sources",
        ["organization_id", "sha256"],
        unique=True,
        postgresql_where=sa.text("deleted_at IS NULL"),
    )

    op.create_table(
        "knowledge_articles",
        _primary_key(),
        _organization_column(),
        sa.Column("source_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("external_ref", sa.Text(), nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("category", sa.Text(), nullable=True),
        sa.Column("status", article_status, nullable=False, server_default=sa.text("'DRAFT'")),
        sa.Column("version", sa.Integer(), nullable=False, server_default=sa.text("1")),
        sa.Column("indexed_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("index_error", sa.Text(), nullable=True),
        sa.Column("archived_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("deleted_at", sa.TIMESTAMP(timezone=True), nullable=True),
        *_timestamps(),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["source_id"], ["knowledge_sources.id"], ondelete="SET NULL"),
        sa.CheckConstraint("length(btrim(title)) > 0", name="title_not_blank"),
        sa.CheckConstraint("length(btrim(body)) > 0", name="body_not_blank"),
        sa.CheckConstraint("version >= 1", name="version_starts_at_one"),
        sa.CheckConstraint(
            "(status = 'ARCHIVED') = (archived_at IS NOT NULL)",
            name="archived_status_matches_timestamp",
        ),
    )
    op.create_index(
        "uq_knowledge_articles_organization_ref",
        "knowledge_articles",
        ["organization_id", "external_ref"],
        unique=True,
    )
    op.create_index(
        "ix_knowledge_articles_retrievable",
        "knowledge_articles",
        ["organization_id", "category"],
        postgresql_where=sa.text("status = 'PUBLISHED' AND deleted_at IS NULL"),
    )

    op.create_table(
        "knowledge_chunks",
        _primary_key(),
        _organization_column(),
        sa.Column("article_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("chunk_index", sa.Integer(), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        # Character offsets into the article body, so a citation resolves to an
        # exact passage rather than "somewhere in this document".
        sa.Column("start_offset", sa.Integer(), nullable=False),
        sa.Column("end_offset", sa.Integer(), nullable=False),
        sa.Column("token_count", sa.Integer(), nullable=True),
        *_timestamps(),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["article_id"], ["knowledge_articles.id"], ondelete="CASCADE"),
        sa.CheckConstraint("chunk_index >= 0", name="chunk_index_not_negative"),
        sa.CheckConstraint("end_offset > start_offset", name="offsets_are_ordered"),
        sa.CheckConstraint("start_offset >= 0", name="start_offset_not_negative"),
        sa.CheckConstraint("length(content) > 0", name="content_not_empty"),
    )
    op.create_index(
        "uq_knowledge_chunks_article_index",
        "knowledge_chunks",
        ["article_id", "chunk_index"],
        unique=True,
    )

    # PostgreSQL full-text half of the hybrid retrieval the MVP proved.
    op.execute(
        "ALTER TABLE knowledge_chunks "
        "ADD COLUMN search_vector tsvector "
        "GENERATED ALWAYS AS (to_tsvector('english', content)) STORED"
    )
    op.create_index(
        "ix_knowledge_chunks_search",
        "knowledge_chunks",
        ["search_vector"],
        postgresql_using="gin",
    )

    op.create_table(
        "knowledge_embeddings",
        _primary_key(),
        _organization_column(),
        sa.Column("chunk_id", postgresql.UUID(as_uuid=True), nullable=False),
        # Recorded, not assumed. Mixing models in one index is silent corruption.
        sa.Column("embedding_model", sa.Text(), nullable=False),
        sa.Column("dimensions", sa.Integer(), nullable=False),
        *_timestamps(),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["chunk_id"], ["knowledge_chunks.id"], ondelete="CASCADE"),
        sa.CheckConstraint(
            f"dimensions = {DEFAULT_EMBEDDING_DIMENSIONS}",
            name="dimensions_match_the_configured_model",
        ),
    )
    op.execute(
        f"ALTER TABLE knowledge_embeddings "
        f"ADD COLUMN embedding vector({DEFAULT_EMBEDDING_DIMENSIONS}) NOT NULL"
    )
    op.create_index(
        "uq_knowledge_embeddings_chunk_model",
        "knowledge_embeddings",
        ["chunk_id", "embedding_model"],
        unique=True,
    )
    # HNSW rather than IVFFlat: it needs no training pass over existing data,
    # which matters when a client's corpus starts empty and grows.
    op.execute(
        "CREATE INDEX ix_knowledge_embeddings_vector "
        "ON knowledge_embeddings USING hnsw (embedding vector_cosine_ops)"
    )

    # ------------------------------------------------------------------
    # AI configuration
    # ------------------------------------------------------------------

    op.create_table(
        "ai_configs",
        _primary_key(),
        _organization_column(),
        sa.Column("provider", sa.Text(), nullable=False),
        # A Secret Manager resource name. Never the key itself (C-D009).
        sa.Column("credential_secret_name", sa.Text(), nullable=False),
        sa.Column("credential_last_four", sa.Text(), nullable=True),
        sa.Column("classification_model", sa.Text(), nullable=False),
        sa.Column("generation_model", sa.Text(), nullable=False),
        sa.Column("embedding_model", sa.Text(), nullable=False),
        sa.Column("embedding_dimensions", sa.Integer(), nullable=False),
        sa.Column("processing_region", sa.Text(), nullable=True),
        sa.Column("monthly_budget_minor_units", sa.BigInteger(), nullable=True),
        sa.Column("budget_currency", sa.Text(), nullable=False, server_default=sa.text("'USD'")),
        sa.Column("fallback_provider", sa.Text(), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("last_verified_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("last_verification_error", sa.Text(), nullable=True),
        *_timestamps(),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"], ondelete="CASCADE"),
        # The whole point of C-D009: a key must never land in a column. These
        # make an accidental write fail loudly instead of leaking quietly.
        # left(...) rather than LIKE: a '%' in raw SQL is escaped to '%%' in
        # Alembic's offline output, so the rendered migration would be wrong if
        # an operator applied it directly.
        sa.CheckConstraint(
            "left(credential_secret_name, 3) <> 'sk-' "
            "AND left(credential_secret_name, 4) <> 'AIza' "
            "AND length(credential_secret_name) < 512",
            name="credential_reference_is_not_a_key",
        ),
        sa.CheckConstraint(
            "credential_last_four IS NULL OR length(credential_last_four) <= 4",
            name="only_the_last_four_are_shown",
        ),
        sa.CheckConstraint(
            "embedding_dimensions > 0", name="embedding_dimensions_are_positive"
        ),
        sa.CheckConstraint(
            "monthly_budget_minor_units IS NULL OR monthly_budget_minor_units >= 0",
            name="budget_not_negative",
        ),
    )
    op.create_index(
        "uq_ai_configs_active_provider",
        "ai_configs",
        ["organization_id", "provider"],
        unique=True,
        postgresql_where=sa.text("is_active"),
    )

    # ------------------------------------------------------------------
    # Triage runs and actions
    # ------------------------------------------------------------------

    op.create_table(
        "triage_runs",
        _primary_key(),
        _organization_column(),
        sa.Column("ticket_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("message_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("correlation_id", sa.Text(), nullable=False),
        sa.Column("route", sa.Text(), nullable=False),
        sa.Column("category", sa.Text(), nullable=True),
        sa.Column("urgency", sa.Text(), nullable=True),
        sa.Column("confidence", sa.Numeric(4, 3), nullable=True),
        sa.Column("rule_codes", postgresql.ARRAY(sa.Text()), nullable=False,
                  server_default=sa.text("'{}'::text[]")),
        sa.Column("decision_summary", sa.Text(), nullable=True),
        sa.Column("draft", sa.Text(), nullable=True),
        sa.Column("citations", postgresql.ARRAY(sa.Text()), nullable=False,
                  server_default=sa.text("'{}'::text[]")),
        sa.Column("grounding_validated", sa.Boolean(), nullable=False,
                  server_default=sa.text("false")),
        sa.Column("mcp_connected", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("processing_ms", sa.Integer(), nullable=True),
        sa.Column("model_used", sa.Text(), nullable=True),
        sa.Column("prompt_tokens", sa.Integer(), nullable=True),
        sa.Column("completion_tokens", sa.Integer(), nullable=True),
        # Recorded decision evidence only. Never hidden chain-of-thought.
        sa.Column("trace", postgresql.JSONB(), nullable=False,
                  server_default=sa.text("'[]'::jsonb")),
        sa.Column("grounding_details", postgresql.JSONB(), nullable=False,
                  server_default=sa.text("'{}'::jsonb")),
        sa.Column("deleted_at", sa.TIMESTAMP(timezone=True), nullable=True),
        *_timestamps(),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["ticket_id"], ["tickets.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["message_id"], ["messages.id"], ondelete="SET NULL"),
        sa.CheckConstraint(
            "route IN ('AUTO_RESOLVE', 'CLARIFY', 'ESCALATE')", name="route_is_known"
        ),
        # The MVP's fail-closed grounding rule, now enforced by the database:
        # an auto-resolve draft without validated grounding cannot be stored.
        sa.CheckConstraint(
            "route <> 'AUTO_RESOLVE' OR draft IS NULL OR grounding_validated",
            name="auto_resolve_draft_must_be_grounded",
        ),
        sa.CheckConstraint(
            "route <> 'AUTO_RESOLVE' OR draft IS NULL OR cardinality(citations) > 0",
            name="auto_resolve_draft_must_cite",
        ),
        sa.CheckConstraint("jsonb_typeof(trace) = 'array'", name="trace_is_an_array"),
    )
    op.create_index("ix_triage_runs_ticket", "triage_runs", ["ticket_id", "created_at"])
    op.create_index(
        "uq_triage_runs_correlation",
        "triage_runs",
        ["organization_id", "correlation_id"],
        unique=True,
    )
    op.create_index(
        "ix_triage_runs_organization_created", "triage_runs", ["organization_id", "created_at"]
    )

    op.create_table(
        "actions",
        _primary_key(),
        _organization_column(),
        sa.Column("ticket_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("triage_run_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("actor_membership_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("action_type", action_type, nullable=False),
        # Every external write carries an idempotency key checked immediately
        # before the provider call, so a retry cannot double-send.
        sa.Column("idempotency_key", sa.Text(), nullable=True),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("outcome", sa.Text(), nullable=False),
        sa.Column("error_code", sa.Text(), nullable=True),
        sa.Column("ticket_version_before", sa.Integer(), nullable=True),
        sa.Column("ticket_version_after", sa.Integer(), nullable=True),
        sa.Column("payload", postgresql.JSONB(), nullable=False,
                  server_default=sa.text("'{}'::jsonb")),
        sa.Column("occurred_at", sa.TIMESTAMP(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["ticket_id"], ["tickets.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["triage_run_id"], ["triage_runs.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(
            ["actor_membership_id"], ["memberships.id"], ondelete="SET NULL"
        ),
        sa.CheckConstraint(
            "outcome IN ('SUCCEEDED', 'FAILED', 'REJECTED')", name="outcome_is_known"
        ),
        # A human decision must be attributable to a person. A system action
        # (triage, ingestion) legitimately has no actor.
        sa.CheckConstraint(
            "action_type IN ('TRIAGE_RUN', 'KNOWLEDGE_INGESTED') "
            "OR actor_membership_id IS NOT NULL",
            name="human_actions_have_an_actor",
        ),
    )
    op.create_index("ix_actions_ticket", "actions", ["ticket_id", "occurred_at"])
    op.create_index(
        "ix_actions_organization_occurred", "actions", ["organization_id", "occurred_at"]
    )
    op.create_index(
        "uq_actions_idempotency_key",
        "actions",
        ["organization_id", "idempotency_key"],
        unique=True,
        postgresql_where=sa.text("idempotency_key IS NOT NULL"),
    )

    # ------------------------------------------------------------------
    # Jobs and idempotency
    # ------------------------------------------------------------------

    op.create_table(
        "jobs",
        _primary_key(),
        _organization_column(),
        sa.Column("job_type", sa.Text(), nullable=False),
        # Redelivery is expected (at-least-once). This is what makes handling
        # it exactly once possible.
        sa.Column("idempotency_key", sa.Text(), nullable=False),
        sa.Column("status", job_status, nullable=False, server_default=sa.text("'QUEUED'")),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("max_attempts", sa.Integer(), nullable=False, server_default=sa.text("10")),
        sa.Column("available_at", sa.TIMESTAMP(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.Column("started_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("completed_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("dead_lettered_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("payload", postgresql.JSONB(), nullable=False,
                  server_default=sa.text("'{}'::jsonb")),
        *_timestamps(),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"], ondelete="CASCADE"),
        sa.CheckConstraint("attempts >= 0", name="attempts_not_negative"),
        sa.CheckConstraint("max_attempts > 0", name="max_attempts_is_positive"),
        sa.CheckConstraint("attempts <= max_attempts", name="attempts_within_limit"),
        sa.CheckConstraint(
            "(status = 'DEAD_LETTERED') = (dead_lettered_at IS NOT NULL)",
            name="dead_letter_state_matches_timestamp",
        ),
    )
    op.create_index(
        "uq_jobs_idempotency_key",
        "jobs",
        ["organization_id", "job_type", "idempotency_key"],
        unique=True,
    )
    op.create_index(
        "ix_jobs_runnable",
        "jobs",
        ["available_at"],
        postgresql_where=sa.text("status = 'QUEUED'"),
    )
    op.create_index("ix_jobs_status", "jobs", ["organization_id", "status"])

    # ------------------------------------------------------------------
    # Usage and evaluation
    # ------------------------------------------------------------------

    op.create_table(
        "provider_usage",
        _primary_key(),
        _organization_column(),
        sa.Column("usage_date", sa.Date(), nullable=False),
        sa.Column("provider", sa.Text(), nullable=False),
        sa.Column("model", sa.Text(), nullable=False),
        sa.Column("operation", sa.Text(), nullable=False),
        sa.Column("request_count", sa.BigInteger(), nullable=False, server_default=sa.text("0")),
        sa.Column("prompt_tokens", sa.BigInteger(), nullable=False, server_default=sa.text("0")),
        sa.Column("completion_tokens", sa.BigInteger(), nullable=False,
                  server_default=sa.text("0")),
        # Our estimate, explicitly labelled. Never presented as a provider balance.
        sa.Column("estimated_cost_minor_units", sa.BigInteger(), nullable=False,
                  server_default=sa.text("0")),
        sa.Column("currency", sa.Text(), nullable=False, server_default=sa.text("'USD'")),
        *_timestamps(),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"], ondelete="CASCADE"),
        sa.CheckConstraint("request_count >= 0", name="request_count_not_negative"),
        sa.CheckConstraint("prompt_tokens >= 0", name="prompt_tokens_not_negative"),
        sa.CheckConstraint("completion_tokens >= 0", name="completion_tokens_not_negative"),
        sa.CheckConstraint(
            "estimated_cost_minor_units >= 0", name="estimated_cost_not_negative"
        ),
        sa.CheckConstraint(
            "operation IN ('classification', 'generation', 'embedding')",
            name="operation_is_known",
        ),
    )
    op.create_index(
        "uq_provider_usage_daily",
        "provider_usage",
        ["organization_id", "usage_date", "provider", "model", "operation"],
        unique=True,
    )

    op.create_table(
        "evaluation_runs",
        _primary_key(),
        _organization_column(),
        sa.Column("dataset_version", sa.Text(), nullable=False),
        sa.Column("threshold_version", sa.Text(), nullable=False),
        # The corpus the run measured. A later corpus change invalidates the
        # numbers, and the UI must be able to say so.
        sa.Column("knowledge_fingerprint", sa.Text(), nullable=False),
        sa.Column("article_count", sa.Integer(), nullable=False),
        sa.Column("triggered_by_membership_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("status", sa.Text(), nullable=False, server_default=sa.text("'RUNNING'")),
        sa.Column("passed", sa.Boolean(), nullable=True),
        sa.Column("started_at", sa.TIMESTAMP(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.Column("completed_at", sa.TIMESTAMP(timezone=True), nullable=True),
        *_timestamps(),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["triggered_by_membership_id"], ["memberships.id"], ondelete="SET NULL"
        ),
        sa.CheckConstraint(
            "status IN ('RUNNING', 'COMPLETED', 'FAILED')", name="status_is_known"
        ),
        sa.CheckConstraint("article_count >= 0", name="article_count_not_negative"),
        sa.CheckConstraint(
            "(status = 'COMPLETED') <= (passed IS NOT NULL)",
            name="completed_run_has_a_verdict",
        ),
    )
    op.create_index(
        "ix_evaluation_runs_organization_started",
        "evaluation_runs",
        ["organization_id", "started_at"],
    )

    op.create_table(
        "evaluation_results",
        _primary_key(),
        _organization_column(),
        sa.Column("evaluation_run_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("gate", sa.Text(), nullable=False),
        sa.Column("metric", sa.Text(), nullable=False),
        sa.Column("value", sa.Numeric(10, 4), nullable=False),
        sa.Column("threshold", sa.Numeric(10, 4), nullable=False),
        sa.Column("passed", sa.Boolean(), nullable=False),
        sa.Column("detail", postgresql.JSONB(), nullable=False,
                  server_default=sa.text("'{}'::jsonb")),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["evaluation_run_id"], ["evaluation_runs.id"], ondelete="CASCADE"
        ),
    )
    op.create_index(
        "uq_evaluation_results_run_gate",
        "evaluation_results",
        ["evaluation_run_id", "gate", "metric"],
        unique=True,
    )

    # ------------------------------------------------------------------
    # Retention and legal hold
    # ------------------------------------------------------------------

    op.create_table(
        "retention_policies",
        _primary_key(),
        _organization_column(),
        sa.Column("data_class", retention_data_class, nullable=False),
        sa.Column("retain_days", sa.Integer(), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("approved_by_membership_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("approved_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        *_timestamps(),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["approved_by_membership_id"], ["memberships.id"], ondelete="SET NULL"
        ),
        # No indefinite retention, and no same-day deletion by accident.
        sa.CheckConstraint(
            "retain_days >= 1 AND retain_days <= 3650", name="retain_days_is_bounded"
        ),
    )
    op.create_index(
        "uq_retention_policies_class",
        "retention_policies",
        ["organization_id", "data_class"],
        unique=True,
    )

    # A hold stops deletion for a named reason. Retention must never silently
    # win over a legal obligation.
    op.create_table(
        "legal_holds",
        _primary_key(),
        _organization_column(),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("data_class", retention_data_class, nullable=True),
        sa.Column("ticket_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("placed_by_membership_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("placed_at", sa.TIMESTAMP(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.Column("released_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("released_by_membership_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["ticket_id"], ["tickets.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["placed_by_membership_id"], ["memberships.id"], ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(
            ["released_by_membership_id"], ["memberships.id"], ondelete="SET NULL"
        ),
        sa.CheckConstraint("length(btrim(reason)) > 0", name="reason_not_blank"),
        sa.CheckConstraint(
            "released_at IS NULL OR released_at >= placed_at", name="release_follows_placement"
        ),
    )
    op.create_index(
        "ix_legal_holds_active",
        "legal_holds",
        ["organization_id", "data_class"],
        postgresql_where=sa.text("released_at IS NULL"),
    )
    op.create_index(
        "ix_legal_holds_ticket",
        "legal_holds",
        ["ticket_id"],
        postgresql_where=sa.text("released_at IS NULL AND ticket_id IS NOT NULL"),
    )

    # Append-only record of what retention actually removed. Deleting data
    # without a record of the deletion is indistinguishable from data loss.
    op.create_table(
        "retention_sweeps",
        _primary_key(),
        _organization_column(),
        sa.Column("data_class", retention_data_class, nullable=False),
        sa.Column("cutoff_at", sa.TIMESTAMP(timezone=True), nullable=False),
        sa.Column("rows_deleted", sa.BigInteger(), nullable=False, server_default=sa.text("0")),
        sa.Column("rows_held", sa.BigInteger(), nullable=False, server_default=sa.text("0")),
        sa.Column("dry_run", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("swept_at", sa.TIMESTAMP(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"], ondelete="CASCADE"),
        sa.CheckConstraint("rows_deleted >= 0", name="rows_deleted_not_negative"),
        sa.CheckConstraint("rows_held >= 0", name="rows_held_not_negative"),
    )
    op.create_index(
        "ix_retention_sweeps_organization_swept",
        "retention_sweeps",
        ["organization_id", "swept_at"],
    )

    op.execute(
        """
        CREATE OR REPLACE FUNCTION retention_sweeps_append_only()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        BEGIN
            RAISE EXCEPTION USING
                ERRCODE = 'restrict_violation',
                MESSAGE = 'retention_sweeps is append-only (attempted ' || TG_OP || ')';
        END;
        $$;
        """
    )
    op.execute(
        """
        CREATE TRIGGER retention_sweeps_no_update_or_delete
        BEFORE UPDATE OR DELETE ON retention_sweeps
        FOR EACH ROW EXECUTE FUNCTION retention_sweeps_append_only();
        """
    )


def downgrade() -> None:
    op.execute(
        "DROP TRIGGER IF EXISTS retention_sweeps_no_update_or_delete ON retention_sweeps;"
    )
    op.execute("DROP FUNCTION IF EXISTS retention_sweeps_append_only();")

    for table in (
        "retention_sweeps",
        "legal_holds",
        "retention_policies",
        "evaluation_results",
        "evaluation_runs",
        "provider_usage",
        "jobs",
        "actions",
        "triage_runs",
        "ai_configs",
        "knowledge_embeddings",
        "knowledge_chunks",
        "knowledge_articles",
        "knowledge_sources",
    ):
        op.drop_table(table)

    for enum_name in (
        "retention_data_class",
        "action_type",
        "job_status",
        "article_status",
    ):
        op.execute(f"DROP TYPE IF EXISTS {enum_name};")

    # The vector extension is deliberately left in place: another schema in the
    # same database may use it, and dropping it would take their data with it.
