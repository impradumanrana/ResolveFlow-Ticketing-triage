"""C12: the SQL a review decision runs.

Proven against PostgreSQL in CLIENT_C12_TEST_REPORT.md. These pin what can be
checked without a database: one transaction, the visibility predicate, the
optimistic lock, and the claim taken before any provider call.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime
from typing import Any

import pytest

from app.review.records import Actor, Decision, DraftOutcome, ReviewRequest, TicketFacts
from app.review.store import STATUS_AFTER, VISIBILITY, PostgresReviewStore

ORG = "00000000-0000-0000-0000-0000000000a1"
TICKET = "11111111-1111-1111-1111-111111111111"
MEMBERSHIP = "22222222-2222-2222-2222-222222222222"
NOW = datetime(2026, 10, 4, 9, 0, tzinfo=UTC)


MISSING = object()


class Result:
    def __init__(self, value: Any = None, row: Any = MISSING):
        self._value = value
        self._row = row

    def scalar_one(self) -> Any:
        return self._value or "33333333-3333-3333-3333-333333333333"

    def scalar(self) -> Any:
        return self._value

    def first(self) -> Any:
        # `None` means "no row", which is how a lost optimistic lock looks.
        if self._row is MISSING:
            return type("Row", (), {"version": 4})()
        return self._row

    def all(self) -> list[Any]:
        return []


TICKET_ROW = {
    "ticket_id": TICKET,
    "organization_id": ORG,
    "mailbox_id": "44444444-4444-4444-4444-444444444444",
    "thread_id": "55555555-5555-5555-5555-555555555555",
    "version": 3,
    "status": "WAITING_ON_REVIEW",
    "mailbox_address": "support@acme.example",
    "reference": 42,
    "subject": "Reset my password",
    "customer_address": "customer@example.net",
    "queue_id": None,
    "department_id": None,
    "assigned_membership_id": None,
    "route": "AUTO_RESOLVE",
    "triage_run_id": None,
    "model_draft": "Reply. [KB-001]",
    "citations": ["KB-001"],
    "grounding_validated": True,
    "latest_revision_id": None,
    "latest_revision": 0,
    "latest_body": None,
    "granted_scopes": ["https://www.googleapis.com/auth/gmail.readonly"],
}


def row_from(values: dict[str, Any]) -> Any:
    return type("Row", (), {**values, "_mapping": values})()


class RecordingConnection:
    def __init__(self) -> None:
        self.statements: list[tuple[str, dict[str, Any]]] = []
        self.transactions = 0

    def execute(self, statement: Any, params: dict[str, Any] | None = None) -> Result:
        text = str(statement)
        self.statements.append((text, dict(params or {})))
        if "FROM tickets t" in text:
            return Result(row=row_from(TICKET_ROW))
        if "FROM messages" in text:
            return Result(row=None)
        return Result()

    def matching(self, fragment: str) -> tuple[str, dict[str, Any]]:
        return next((sql, params) for sql, params in self.statements if fragment in sql)

    def kinds(self) -> list[str]:
        return [sql.split()[0] for sql, _ in self.statements]


class RecordingEngine:
    def __init__(self) -> None:
        self.connection = RecordingConnection()

    def begin(self) -> Any:
        engine = self

        class Transaction:
            def __enter__(self) -> RecordingConnection:
                engine.connection.transactions += 1
                return engine.connection

            def __exit__(self, *_: object) -> None:
                return None

        return Transaction()


def request_for(
    decision: Decision = Decision.APPROVE, role: str = "AGENT", **overrides
) -> ReviewRequest:
    base: dict[str, Any] = {
        "organization_id": ORG,
        "ticket_id": TICKET,
        "actor": Actor(membership_id=MEMBERSHIP, organization_id=ORG, role=role),
        "decision": decision,
        "idempotency_key": "key-12345678",
        "expected_version": 3,
    }
    return ReviewRequest(**{**base, **overrides})


def facts(**overrides) -> TicketFacts:
    base = {
        "ticket_id": TICKET,
        "organization_id": ORG,
        "mailbox_id": "44444444-4444-4444-4444-444444444444",
        "thread_id": "55555555-5555-5555-5555-555555555555",
        "version": 3,
        "status": "WAITING_ON_REVIEW",
        "citations": ("KB-001",),
    }
    return TicketFacts(**{**base, **overrides})


@pytest.fixture
def engine() -> RecordingEngine:
    return RecordingEngine()


# --------------------------------------------------------------------------
# Visibility
# --------------------------------------------------------------------------


def test_acting_needs_the_mailbox_action_permission_not_just_view():
    assert "mp.can_action" in VISIBILITY
    assert "can_view" not in VISIBILITY


def test_visibility_covers_organization_mailbox_and_department():
    assert "t.organization_id = CAST(:org AS uuid)" in VISIBILITY
    assert "t.deleted_at IS NULL" in VISIBILITY
    assert "mailbox_permissions" in VISIBILITY
    assert "department_memberships" in VISIBILITY
    # An organization-wide role skips both membership checks, nothing else.
    assert VISIBILITY.count(":org_wide") == 2


def test_loading_a_ticket_is_scoped_to_the_actor(engine):
    PostgresReviewStore(engine).load_ticket(request_for())
    sql, params = engine.connection.matching("FROM tickets t")
    assert VISIBILITY.strip() in sql
    assert params == {
        "org": ORG,
        "membership": MEMBERSHIP,
        "org_wide": False,
        "ticket": TICKET,
    }


def test_an_organization_wide_role_is_passed_as_such(engine):
    PostgresReviewStore(engine).load_ticket(request_for(role="ADMIN"))
    _, params = engine.connection.matching("FROM tickets t")
    assert params["org_wide"] is True


def test_the_load_reads_the_latest_run_revision_and_granted_scopes(engine):
    facts = PostgresReviewStore(engine).load_ticket(request_for())
    assert facts is not None and facts.mailbox_address == "support@acme.example", (
        "the address the draft would come from is read with the ticket, not looked up later"
    )
    sql, _ = engine.connection.matching("FROM tickets t")
    assert "JOIN mailboxes mb ON mb.id = t.mailbox_id" in sql
    assert "FROM triage_runs" in sql and "ORDER BY created_at DESC LIMIT 1" in sql
    assert "FROM draft_revisions" in sql and "ORDER BY revision DESC LIMIT 1" in sql
    assert "mailbox_credentials cred" in sql and "granted_scopes" in sql


# --------------------------------------------------------------------------
# Applying a decision
# --------------------------------------------------------------------------


def test_a_decision_is_one_transaction(engine):
    PostgresReviewStore(engine).apply(request_for(Decision.RESOLVE), facts(), at=NOW)
    assert engine.connection.transactions == 1
    assert engine.connection.kinds() == ["UPDATE", "INSERT", "INSERT"]


def test_the_ticket_update_carries_the_version_the_person_saw(engine):
    PostgresReviewStore(engine).apply(request_for(Decision.RESOLVE), facts(), at=NOW)
    sql, params = engine.connection.matching("UPDATE tickets")
    assert "AND version = :expected" in sql
    assert "version = version + 1" in sql
    assert params["expected"] == 3
    assert "RETURNING version" in sql


def test_zero_rows_updated_is_a_conflict_not_a_retry(engine):
    class Conflicting(RecordingConnection):
        def execute(self, statement, params=None):
            self.statements.append((str(statement), dict(params or {})))
            if "UPDATE tickets" in str(statement):
                return Result(row=None)
            if "SELECT version FROM tickets" in str(statement):
                return Result(value=9)
            return Result()

    engine.connection = Conflicting()
    from app.review.records import VersionConflict

    with pytest.raises(VersionConflict) as conflict:
        PostgresReviewStore(engine).apply(request_for(Decision.RESOLVE), facts(), at=NOW)
    assert conflict.value.current_version == 9


def test_an_edit_inserts_a_revision_attributed_to_its_author(engine):
    PostgresReviewStore(engine).apply(
        request_for(Decision.EDIT, body="A clearer reply."), facts(), at=NOW
    )
    sql, params = engine.connection.matching("INSERT INTO draft_revisions")
    assert params["source"] == "HUMAN"
    assert params["membership"] == MEMBERSHIP
    assert params["revision"] == 1
    assert params["body"] == "A clearer reply."
    assert re.fullmatch(r"[0-9a-f]{64}", params["digest"])


def test_the_action_row_carries_the_actor_reason_and_both_versions(engine):
    PostgresReviewStore(engine).apply(
        request_for(Decision.REJECT, reason="A person should reply."), facts(), at=NOW
    )
    sql, params = engine.connection.matching("INSERT INTO actions")
    assert params["membership"] == MEMBERSHIP
    assert params["action_type"] == "DRAFT_REJECTED"
    assert params["reason"] == "A person should reply."
    assert (params["before"], params["after"]) == (3, 4)
    assert params["key"] == "key-12345678"
    assert "CAST(:payload AS jsonb)" in sql


def test_every_decision_writes_an_audit_event(engine):
    PostgresReviewStore(engine).apply(request_for(Decision.RESOLVE), facts(), at=NOW)
    sql, params = engine.connection.matching("INSERT INTO audit_events")
    assert params["outcome"] == "ALLOWED"
    assert params["action"] == "review.resolve"
    assert params["target"] == TICKET
    assert "'ticket'" in sql


def test_a_refusal_is_recorded_as_a_rejected_action_and_a_denied_audit(engine):
    PostgresReviewStore(engine).record_refusal(
        request_for(Decision.APPROVE), "ROLE_LACKS_PERMISSION", ticket_version=3, at=NOW
    )
    _, action = engine.connection.matching("INSERT INTO actions")
    assert action["outcome"] == "REJECTED"
    assert action["code"] == "ROLE_LACKS_PERMISSION"
    assert action["key"] is None, "a refusal must not consume the idempotency key"
    _, audit = engine.connection.matching("INSERT INTO audit_events")
    assert audit["outcome"] == "DENIED"


@pytest.mark.parametrize(
    ("decision", "fragment"),
    [
        (Decision.APPROVE, "status = CAST(:status AS ticket_status)"),
        (Decision.RESOLVE, "resolved_at = COALESCE(resolved_at, :at)"),
        (Decision.ASSIGN, "assigned_membership_id = CAST(:assignee AS uuid)"),
    ],
)
def test_each_decision_changes_what_it_should(engine, decision, fragment):
    extra = {"assignee_membership_id": MEMBERSHIP} if decision is Decision.ASSIGN else {}
    PostgresReviewStore(engine).apply(request_for(decision, **extra), facts(), at=NOW)
    sql, _ = engine.connection.matching("UPDATE tickets")
    assert fragment in sql


def test_an_edit_leaves_the_status_alone(engine):
    assert STATUS_AFTER[Decision.EDIT] is None
    PostgresReviewStore(engine).apply(request_for(Decision.EDIT, body="x"), facts(), at=NOW)
    sql, _ = engine.connection.matching("UPDATE tickets")
    assert "status =" not in sql


# --------------------------------------------------------------------------
# The provider draft
# --------------------------------------------------------------------------


def test_a_draft_is_claimed_as_pending_before_any_provider_call(engine):
    PostgresReviewStore(engine).claim_provider_draft(
        request_for(),
        facts(latest_revision_id="66666666-6666-6666-6666-666666666666", latest_revision=1),
        body="Reply.",
        at=NOW,
    )
    sql, params = engine.connection.matching("INSERT INTO provider_drafts")
    assert "'PENDING'" in sql
    assert params["membership"] == MEMBERSHIP, "a draft names the person who approved it"
    assert params["key"] == "key-12345678:draft"
    assert re.fullmatch(r"[0-9a-f]{64}", params["digest"])
    assert "provider_draft_id" not in sql, "nothing is claimed with a provider id"


def test_settling_only_touches_a_pending_claim(engine):
    PostgresReviewStore(engine).settle_provider_draft(
        ORG,
        "77777777-7777-7777-7777-777777777777",
        DraftOutcome(status="CREATED", provider_draft_id="draft-1"),
        at=NOW,
    )
    sql, params = engine.connection.matching("UPDATE provider_drafts")
    assert "AND status = 'PENDING'" in sql
    assert params["provider_id"] == "draft-1"
    assert params["org"] == ORG


def test_a_stale_claim_is_closed_as_unknown(engine):
    PostgresReviewStore(engine).release_stale_claims(NOW, at=NOW)
    sql, _ = engine.connection.matching("UPDATE provider_drafts")
    assert "'DRAFT_OUTCOME_UNKNOWN'" in sql
    assert "status = 'PENDING'" in sql


def test_the_provider_draft_is_its_own_attributable_action(engine):
    PostgresReviewStore(engine).record_draft_action(
        request_for(), facts(), DraftOutcome(status="CREATED", provider_draft_id="draft-1"), at=NOW
    )
    sql, params = engine.connection.matching("INSERT INTO actions")
    assert "'PROVIDER_DRAFT_CREATED'" in sql
    assert params["outcome"] == "SUCCEEDED"
    assert params["key"] == "key-12345678:draft-action"


def test_no_statement_in_the_store_mentions_sending(engine):
    store = PostgresReviewStore(engine)
    store.apply(request_for(Decision.APPROVE), facts(), at=NOW)
    store.claim_provider_draft(request_for(), facts(), body="Reply.", at=NOW)
    store.settle_provider_draft(
        ORG, "x", DraftOutcome(status="CREATED", provider_draft_id="d"), at=NOW
    )
    for sql, _ in engine.connection.statements:
        assert "send" not in sql.lower()


def test_every_statement_is_scoped_to_one_organization(engine):
    store = PostgresReviewStore(engine)
    store.load_ticket(request_for())
    store.apply(request_for(Decision.RESOLVE), facts(), at=NOW)
    store.claim_provider_draft(request_for(), facts(), body="Reply.", at=NOW)
    for sql, params in engine.connection.statements:
        assert "organization_id" in sql
        assert params.get("org") == ORG


def test_a_second_edit_is_the_next_revision_not_the_first_again(engine):
    """The revision number comes from what the ticket already has."""
    PostgresReviewStore(engine).apply(
        request_for(Decision.EDIT, body="Second thoughts."),
        facts(latest_revision=1, latest_revision_id="66666666-6666-6666-6666-666666666666",
              latest_body="First draft."),
        at=NOW,
    )
    _, params = engine.connection.matching("INSERT INTO draft_revisions")
    assert params["revision"] == 2


def test_the_visibility_predicate_has_no_comment_or_tautology():
    """A clause can be neutered without being deleted."""
    for banned in ("--", "/*", "OR true", "or true", "1=1", "1 = 1"):
        assert banned not in VISIBILITY, banned
    assert "dm.membership_id = CAST(:membership AS uuid)" in VISIBILITY
    assert "mp.membership_id = CAST(:membership AS uuid)" in VISIBILITY
    # Exactly two escapes, both the organization-wide role.
    assert VISIBILITY.count(":org_wide") == 2
