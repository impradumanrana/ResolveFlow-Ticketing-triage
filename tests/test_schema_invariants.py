"""Structural invariants of the durable schema, verified offline.

These render the whole migration chain as PostgreSQL SQL without a database and
assert the properties C04 is accountable for. They are cheap enough to run on
every commit, which is what stops a later migration from quietly dropping a
tenancy scope or an append-only guarantee.

Behaviour that only a live database can prove - that the constraints actually
reject the rows they should, that a dump restores faithfully - is recorded in
`CLIENT_C04_TEST_REPORT.md`.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config

ROOT = Path(__file__).resolve().parents[1]

# Global identity, owned by Auth.js, plus the tenant root itself and Alembic's
# own bookkeeping. Everything else must be tenant-scoped.
# Tenant-scoped, but the column is nullable for a specific recorded reason.
NULLABLE_SCOPE_TABLES = {"audit_events"}

UNSCOPED_TABLES = {
    "organizations",
    "users",
    "accounts",
    "sessions",
    "verification_tokens",
    "alembic_version",
}


@pytest.fixture(scope="module")
def rendered_sql() -> str:
    import contextlib
    import io

    config = Config(str(ROOT / "alembic.ini"))
    buffer = io.StringIO()
    with contextlib.redirect_stdout(buffer):
        command.upgrade(config, "head", sql=True)
    return buffer.getvalue()


def created_tables(sql: str) -> dict[str, str]:
    """Map table name to its CREATE TABLE body."""
    tables: dict[str, str] = {}
    for match in re.finditer(r"CREATE TABLE (\w+) \((.*?)\n\);", sql, re.DOTALL):
        tables[match.group(1)] = match.group(2)
    return tables


def test_the_chain_has_one_head(rendered_sql: str) -> None:
    for revision in (
        "20260911_0001",
        "20260915_0002",
        "20260915_0003",
        "20260915_0004",
        "20260915_0005",
        "20260915_0006",
        "20260916_0007",
        "20260916_0008",
        "20260917_0009",
        "20260918_0010",
        "20260919_0011",
        "20260920_0012",
    ):
        assert revision in rendered_sql, f"{revision} is not in the chain"


def test_every_expected_table_is_created(rendered_sql: str) -> None:
    tables = created_tables(rendered_sql)

    expected = {
        # C03 identity
        "organizations",
        "organization_domains",
        "users",
        "accounts",
        "sessions",
        "verification_tokens",
        "departments",
        "memberships",
        "department_memberships",
        "invitations",
        "audit_events",
        # C04 operations
        "queues",
        "mailboxes",
        "mailbox_permissions",
        "threads",
        "messages",
        "attachments",
        "tickets",
        "ticket_assignments",
        "ticket_seen_state",
        "sla_policies",
        "ticket_sla_states",
        # C04 knowledge and governance
        "knowledge_sources",
        "knowledge_articles",
        "knowledge_chunks",
        "knowledge_embeddings",
        "ai_configs",
        "triage_runs",
        "actions",
        "jobs",
        "provider_usage",
        "evaluation_runs",
        "evaluation_results",
        "retention_policies",
        "legal_holds",
        "retention_sweeps",
        # C06 credential custody
        "mailbox_credentials",
        "mailbox_connection_attempts",
        # C09 client rules
        "routing_rules",
        "vip_contacts",
        "business_holidays",
        # C10 AI gateway
        "ai_model_approvals",
        "provider_budget_ledgers",
        "provider_calls",
        # C12 review and provider drafts
        "draft_revisions",
        "provider_drafts",
        # C13 security and operations
        "rate_limit_counters",
        "erasure_records",
    }

    missing = expected - set(tables)
    assert not missing, f"missing tables: {sorted(missing)}"

    # And the other direction, which is what catches drift: a new table has to
    # be declared here, so the tenancy and append-only tests below cannot be
    # passed by a table nobody listed.
    undeclared = set(tables) - expected - {"alembic_version"}
    assert not undeclared, f"undeclared tables: {sorted(undeclared)}"


def test_every_tenant_table_is_scoped_by_organization(rendered_sql: str) -> None:
    """C-D004: tenancy is designed in, so no later destructive migration is needed."""
    for table, body in created_tables(rendered_sql).items():
        if table in UNSCOPED_TABLES:
            continue
        if table in NULLABLE_SCOPE_TABLES:
            assert "organization_id UUID" in body, f"{table} has no organization column"
            continue
        assert "organization_id UUID NOT NULL" in body, f"{table} is not tenant-scoped"


def test_only_the_audit_table_may_have_a_nullable_organization(
    rendered_sql: str,
) -> None:
    """A sign-in refused before any organization resolves must still be recorded.

    That is the single reason this exception exists. Any other table gaining a
    nullable scope is a tenancy hole, so the exception list is pinned here.
    """
    assert NULLABLE_SCOPE_TABLES == {"audit_events"}

    body = created_tables(rendered_sql)["audit_events"]
    assert "organization_id UUID," in body
    assert "organization_id UUID NOT NULL" not in body


def test_every_tenant_table_cascades_from_its_organization(rendered_sql: str) -> None:
    """Deleting an organization must not strand its rows."""
    for table, body in created_tables(rendered_sql).items():
        if table in UNSCOPED_TABLES or "organization_id" not in body:
            continue
        pattern = (
            r"FOREIGN KEY\(organization_id\) REFERENCES organizations \(id\) "
            r"ON DELETE (CASCADE|RESTRICT)"
        )
        assert re.search(pattern, body), f"{table} has no organization foreign key"


def test_pgvector_is_created_by_the_migration_not_assumed(rendered_sql: str) -> None:
    assert "CREATE EXTENSION IF NOT EXISTS vector" in rendered_sql


def test_embeddings_pin_their_model_and_dimension(rendered_sql: str) -> None:
    """Vectors from different models are comparable arithmetic but meaningless."""
    body = created_tables(rendered_sql)["knowledge_embeddings"]
    assert "embedding_model TEXT NOT NULL" in body
    assert "dimensions INTEGER NOT NULL" in body
    assert "CHECK (dimensions = 1536)" in body
    assert "ADD COLUMN embedding vector(1536) NOT NULL" in rendered_sql


def test_mailbox_credentials_hold_only_sealed_envelopes(rendered_sql: str) -> None:
    """C-D056: a raw refresh token must never be storable, even by mistake."""
    body = created_tables(rendered_sql)["mailbox_credentials"]
    assert "ck_mailbox_credentials_envelope_is_sealed" in body
    assert "ck_mailbox_credentials_not_a_raw_token" in body
    assert "access_token" not in body, "access tokens are never persisted"


def test_a_send_capable_scope_can_never_be_stored_on_a_credential(rendered_sql: str) -> None:
    """C-D126: compose became storable with the scope change; sending did not.

    Read against the whole rendered chain, not one migration, because that is
    what the database ends up enforcing: 0006 forbade every write-capable
    scope and 0010 narrowed it to the send-capable ones.
    """
    from app.mailbox.scopes import GMAIL_COMPOSE, NEVER_PERMITTED_SCOPES

    match = re.search(
        r"ALTER TABLE mailbox_credentials ADD CONSTRAINT "
        r"ck_mailbox_credentials_no_send_capable_scope CHECK \((.*?)\);",
        rendered_sql,
        re.DOTALL,
    )
    assert match, "the credential table has no send-scope constraint"
    constraint = match.group(1)

    for scope in NEVER_PERMITTED_SCOPES:
        assert f"NOT ('{scope}' = ANY(granted_scopes))" in constraint, f"{scope} is storable"
    # Permitted by this constraint, and the only write scope that is.
    assert GMAIL_COMPOSE not in constraint

    # C06's blanket constraint is gone, and only after the narrower one exists:
    # no instant in the chain permits a send-capable scope.
    added = rendered_sql.index("ck_mailbox_credentials_no_send_capable_scope")
    dropped = rendered_sql.index("DROP CONSTRAINT ck_mailbox_credentials_no_write_scope_in_v1")
    assert added < dropped


def test_each_mailbox_holds_at_most_one_credential(rendered_sql: str) -> None:
    assert (
        "CREATE UNIQUE INDEX uq_mailbox_credentials_mailbox ON mailbox_credentials (mailbox_id)"
    ) in rendered_sql


def test_connection_state_is_stored_only_as_a_hash(rendered_sql: str) -> None:
    body = created_tables(rendered_sql)["mailbox_connection_attempts"]
    assert "state_hash TEXT NOT NULL" in body
    assert "ck_mailbox_connection_attempts_state_hash_is_sha256" in body
    assert "ck_mailbox_connection_attempts_verifier_is_sealed" in body
    assert "CREATE UNIQUE INDEX uq_mailbox_connection_attempts_state" in rendered_sql


def test_curated_search_terms_are_stored_apart_from_the_body(rendered_sql: str) -> None:
    """Appending curated terms to the body would put them inside quoted passages."""
    assert "ADD COLUMN search_terms TEXT[]" in rendered_sql
    assert "ck_knowledge_articles_search_terms_are_bounded" in rendered_sql


def test_retrieval_has_both_a_vector_and_a_fulltext_index(rendered_sql: str) -> None:
    assert "USING hnsw (embedding vector_cosine_ops)" in rendered_sql
    assert "ix_knowledge_chunks_search" in rendered_sql
    assert "USING gin" in rendered_sql
    assert "GENERATED ALWAYS AS (to_tsvector('english', content)) STORED" in rendered_sql


@pytest.mark.parametrize(
    "table,index",
    [
        ("messages", "uq_messages_mailbox_provider_message"),
        ("threads", "uq_threads_mailbox_provider_thread"),
        ("jobs", "uq_jobs_idempotency_key"),
        ("actions", "uq_actions_idempotency_key"),
        ("triage_runs", "uq_triage_runs_correlation"),
    ],
)
def test_at_least_once_delivery_is_made_idempotent(
    table: str, index: str, rendered_sql: str
) -> None:
    """Redelivery is expected; a duplicate row is not."""
    assert re.search(rf"CREATE UNIQUE INDEX {index} ON {table}", rendered_sql), index


def test_provider_identifiers_are_unique_per_mailbox_not_globally(
    rendered_sql: str,
) -> None:
    """Two mailboxes legitimately receive the same Gmail message or thread.

    A globally unique provider id would make the second mailbox silently drop
    the conversation, so the uniqueness must lead with mailbox_id.
    """
    for table, index, columns in [
        ("messages", "uq_messages_mailbox_provider_message", "(mailbox_id, provider_message_id)"),
        ("threads", "uq_threads_mailbox_provider_thread", "(mailbox_id, provider_thread_id)"),
    ]:
        statement = f"CREATE UNIQUE INDEX {index} ON {table} {columns}"
        assert statement in rendered_sql, f"expected: {statement}"


def test_the_grounding_rule_is_enforced_by_the_database(rendered_sql: str) -> None:
    """C-D010 fail-closed: an ungrounded auto-resolve draft cannot be stored."""
    body = created_tables(rendered_sql)["triage_runs"]
    assert "ck_triage_runs_auto_resolve_draft_must_be_grounded" in body
    assert "ck_triage_runs_auto_resolve_draft_must_cite" in body


def test_sending_remains_impossible(rendered_sql: str) -> None:
    body = created_tables(rendered_sql)["organizations"]
    assert "CHECK (sending_enabled = false)" in body


def test_byok_credentials_cannot_be_stored_in_a_column(rendered_sql: str) -> None:
    """C-D009: only a secret-manager reference, never the key."""
    body = created_tables(rendered_sql)["ai_configs"]
    assert "ck_ai_configs_credential_reference_is_not_a_key" in body
    assert "credential_last_four" in body
    assert "ck_ai_configs_only_the_last_four_are_shown" in body


def test_human_actions_are_attributable(rendered_sql: str) -> None:
    body = created_tables(rendered_sql)["actions"]
    assert "ck_actions_human_actions_have_an_actor" in body


def test_tickets_carry_a_version_for_optimistic_locking(rendered_sql: str) -> None:
    """C12 concurrency: a reviewer acting on a stale view must lose."""
    body = created_tables(rendered_sql)["tickets"]
    assert "version INTEGER DEFAULT 1 NOT NULL" in body


def test_per_user_seen_state_exists_separately_from_provider_labels(
    rendered_sql: str,
) -> None:
    """Gmail's UNREAD is shared; it cannot answer 'has this agent seen it'."""
    body = created_tables(rendered_sql)["ticket_seen_state"]
    assert "membership_id UUID NOT NULL" in body
    assert "uq_ticket_seen_state_pair" in rendered_sql


def test_attachments_are_untrusted_until_scanned(rendered_sql: str) -> None:
    body = created_tables(rendered_sql)["attachments"]
    assert "ck_attachments_scan_state_is_known" in body
    assert "ck_attachments_clean_attachment_has_an_object" in body


def test_sla_is_evaluated_in_a_recorded_time_zone(rendered_sql: str) -> None:
    """Never the server's zone: the client's business hours are the contract."""
    body = created_tables(rendered_sql)["sla_policies"]
    assert "time_zone TEXT DEFAULT 'Etc/UTC' NOT NULL" in body
    assert "ck_sla_policies_business_hours_are_a_valid_range" in body


def test_retention_is_bounded_and_auditable(rendered_sql: str) -> None:
    policies = created_tables(rendered_sql)["retention_policies"]
    assert "ck_retention_policies_retain_days_is_bounded" in policies

    assert "legal_holds" in created_tables(rendered_sql)
    assert "retention_sweeps" in created_tables(rendered_sql)


@pytest.mark.parametrize(
    "table,trigger",
    [
        ("audit_events", "audit_events_no_update_or_delete"),
        ("retention_sweeps", "retention_sweeps_no_update_or_delete"),
    ],
)
def test_history_tables_are_append_only(table: str, trigger: str, rendered_sql: str) -> None:
    assert f"CREATE TRIGGER {trigger}" in rendered_sql
    assert f"BEFORE UPDATE OR DELETE ON {table}" in rendered_sql


def test_dropped_constraints_use_the_bare_name_not_the_rendered_one() -> None:
    """env.py's naming convention is applied on drop as well as on create.

    Passing the already-rendered `ck_<table>_<name>` to `drop_constraint`
    produces a doubled, truncated, non-existent name, so the downgrade fails on
    a real database while every offline check still passes. This caught exactly
    that defect in migration 0005.
    """
    versions = ROOT / "migrations" / "versions"
    pattern = re.compile(r"""drop_constraint\(\s*["']([^"']+)["']""")

    for migration in sorted(versions.glob("*.py")):
        source = migration.read_text(encoding="utf-8")
        for name in pattern.findall(source):
            assert not name.startswith(("ck_", "uq_", "fk_", "pk_")), (
                f"{migration.name}: drop_constraint({name!r}) passes a rendered "
                "name; pass the bare constraint name instead"
            )


def test_no_bind_parameter_escape_leaks_into_the_rendered_sql(rendered_sql: str) -> None:
    """A '%' in raw SQL is escaped by the DBAPI and would break a direct apply."""
    assert "%%" not in rendered_sql


def test_every_timestamp_is_timezone_aware(rendered_sql: str) -> None:
    """A naive timestamp makes SLA and retention wrong by the UTC offset."""
    for table, body in created_tables(rendered_sql).items():
        for line in body.splitlines():
            if "TIMESTAMP" in line and "WITH TIME ZONE" not in line:
                pytest.fail(f"{table} has a naive timestamp: {line.strip()}")


def test_client_rules_are_versioned_bounded_and_never_empty(rendered_sql: str) -> None:
    body = created_tables(rendered_sql)["routing_rules"]
    for constraint in (
        "ck_routing_rules_priority_is_bounded",
        "ck_routing_rules_version_starts_at_one",
        "ck_routing_rules_effective_window_is_ordered",
        "ck_routing_rules_conditions_are_an_object",
        "ck_routing_rules_actions_are_an_object",
        "ck_routing_rules_a_rule_must_do_something",
    ):
        assert constraint in body, constraint
    # Versions are unique per rule name, per organization.
    assert re.search(
        r"CREATE UNIQUE INDEX uq_routing_rules_name_version ON routing_rules "
        r"\(organization_id, name, version\)",
        rendered_sql,
    )


def test_vip_entries_are_unambiguous_and_normalised(rendered_sql: str) -> None:
    body = created_tables(rendered_sql)["vip_contacts"]
    assert "(email_address IS NULL) <> (email_domain IS NULL)" in body
    assert "email_address = lower(email_address)" in body
    assert "email_domain = lower(email_domain)" in body
    # Partial unique indexes, because NULLs never collide in a plain one.
    assert (
        "uq_vip_contacts_address ON vip_contacts (organization_id, email_address) "
        "WHERE email_address IS NOT NULL"
    ) in rendered_sql
    assert (
        "uq_vip_contacts_domain ON vip_contacts (organization_id, email_domain) "
        "WHERE email_domain IS NOT NULL"
    ) in rendered_sql


def test_organization_wide_holidays_cannot_be_duplicated(rendered_sql: str) -> None:
    # A plain unique index over (org, department, date) would let the same
    # organization-wide holiday be inserted twice, since NULL <> NULL.
    assert (
        "uq_business_holidays_organization ON business_holidays "
        "(organization_id, holiday_date) WHERE department_id IS NULL"
    ) in rendered_sql
    assert (
        "uq_business_holidays_department ON business_holidays "
        "(organization_id, department_id, holiday_date) WHERE department_id IS NOT NULL"
    ) in rendered_sql


def column_names(body: str) -> set[str]:
    names = set()
    for line in body.splitlines():
        match = re.match(r"\s+(\w+) [A-Z]", line)
        if match and match.group(1) not in {"CONSTRAINT", "PRIMARY", "FOREIGN", "UNIQUE", "CHECK"}:
            names.add(match.group(1))
    return names


def test_the_call_log_has_no_column_that_could_hold_content(rendered_sql: str) -> None:
    # An allowlist, not a blocklist: a later "error_detail" or "request_body"
    # column must be a deliberate change to this test, not an accident.
    assert column_names(created_tables(rendered_sql)["provider_calls"]) == {
        "id",
        "organization_id",
        "correlation_id",
        "operation",
        "provider",
        "model",
        "region",
        "purpose",
        "outcome",
        "failure_code",
        "http_status",
        "fallback_from",
        "period_month",
        "reserved_micro_units",
        "reservation_state",
        "cost_micro_units",
        "prompt_tokens",
        "completion_tokens",
        "latency_ms",
        "started_at",
        "finished_at",
    }


def test_the_call_log_enforces_its_invariants(rendered_sql: str) -> None:
    body = created_tables(rendered_sql)["provider_calls"]
    for constraint in (
        "ck_provider_calls_failures_have_a_code",
        "ck_provider_calls_failure_code_is_a_code",
        "ck_provider_calls_only_pending_calls_hold_money",
        "ck_provider_calls_reservations_name_a_period",
        "ck_provider_calls_finished_calls_have_an_end",
        "ck_provider_calls_measurements_not_negative",
        "ck_provider_calls_purpose_is_known",
    ):
        assert constraint in body, constraint


def test_budget_ledgers_cannot_go_negative_or_split_a_month(rendered_sql: str) -> None:
    body = created_tables(rendered_sql)["provider_budget_ledgers"]
    assert "ck_provider_budget_ledgers_reserved_not_negative" in body
    assert "ck_provider_budget_ledgers_spent_not_negative" in body
    assert "ck_provider_budget_ledgers_period_is_a_month" in body
    assert (
        "uq_provider_budget_ledgers_period ON provider_budget_ledgers "
        "(organization_id, provider, period_month)"
    ) in rendered_sql


def test_ai_configs_accept_only_a_secret_name_and_codes(rendered_sql: str) -> None:
    assert "ck_ai_configs_credential_reference_is_a_secret_name CHECK" in rendered_sql
    assert "ck_ai_configs_failure_fields_hold_codes CHECK" in rendered_sql
    assert "ck_ai_configs_fallback_is_another_provider CHECK" in rendered_sql


def test_approvals_are_unique_while_current(rendered_sql: str) -> None:
    body = created_tables(rendered_sql)["ai_model_approvals"]
    assert "ck_ai_model_approvals_embeddings_have_no_output" in body
    assert "ck_ai_model_approvals_prices_not_negative" in body
    assert (
        "uq_ai_model_approvals_current ON ai_model_approvals "
        "(organization_id, provider, model, operation, region) WHERE revoked_at IS NULL"
    ) in rendered_sql


def test_nothing_in_the_schema_can_represent_a_sent_message(rendered_sql: str) -> None:
    """The product creates drafts for a person to send, and never sends (C-D010).

    An allowlist of the places "sent" may legitimately appear: a received
    message has a sent time, and the organization flag is constrained to false.
    A new `sent_at` on a draft would be a different product.
    """
    allowed = {
        ("messages", "sent_at"),
        ("organizations", "sending_enabled"),
    }
    for table, body in created_tables(rendered_sql).items():
        for line in body.splitlines():
            match = re.match(r"\s+(\w+) [A-Z]", line)
            if match and re.search(r"sent|sending|send_", match.group(1)):
                assert (table, match.group(1)) in allowed, f"{table}.{match.group(1)}"


def test_a_draft_revision_is_attributable_and_never_overwritten(rendered_sql: str) -> None:
    body = created_tables(rendered_sql)["draft_revisions"]
    for constraint in (
        "ck_draft_revisions_human_revisions_have_an_author",
        "ck_draft_revisions_the_model_writes_revision_one",
        "ck_draft_revisions_source_is_known",
        "ck_draft_revisions_body_is_present_and_bounded",
        "ck_draft_revisions_body_digest_is_a_sha256",
    ):
        assert constraint in body, constraint
    # Revisions are append-only by key: the same revision cannot be rewritten.
    assert (
        "uq_draft_revisions_ticket_revision ON draft_revisions (ticket_id, revision)"
    ) in rendered_sql


def test_a_provider_draft_is_claimed_then_settled(rendered_sql: str) -> None:
    body = created_tables(rendered_sql)["provider_drafts"]
    for constraint in (
        "ck_provider_drafts_status_is_known",
        "ck_provider_drafts_created_drafts_have_a_provider_id",
        "ck_provider_drafts_unsuccessful_drafts_say_why",
        "ck_provider_drafts_settled_drafts_have_a_time",
        "ck_provider_drafts_failure_code_is_a_code",
        "ck_provider_drafts_body_digest_is_a_sha256",
    ):
        assert constraint in body, constraint
    assert "approved_by_membership_id UUID NOT NULL" in body, "a draft names its approver"
    assert (
        "uq_provider_drafts_idempotency ON provider_drafts (organization_id, idempotency_key)"
    ) in rendered_sql
    # One live draft per conversation: a second approval cannot quietly add one.
    assert (
        "uq_provider_drafts_live_per_ticket ON provider_drafts (ticket_id) "
        "WHERE status IN ('PENDING', 'CREATED')"
    ) in rendered_sql


def test_a_provider_draft_cannot_outlive_the_revision_it_was_built_from(rendered_sql: str) -> None:
    body = created_tables(rendered_sql)["provider_drafts"]
    assert "FOREIGN KEY(revision_id) REFERENCES draft_revisions (id) ON DELETE RESTRICT" in body
    assert (
        "FOREIGN KEY(approved_by_membership_id) REFERENCES memberships (id) ON DELETE RESTRICT"
    ) in body
