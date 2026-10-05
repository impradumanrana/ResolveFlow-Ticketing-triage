"""C11: what a triage run writes.

The SQL is proven against PostgreSQL in CLIENT_C11_TEST_REPORT.md. These pin
what can be checked without a database: one transaction, tenant scoping,
idempotency by correlation id, and that only an identifier ever reaches a
foreign-key column.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime
from typing import Any

import pytest

from app.triage.records import Conversation, TriageOutcome
from app.triage.store import PostgresTriageStore

NOW = datetime(2026, 9, 17, 9, 0, tzinfo=UTC)
ORG = "00000000-0000-0000-0000-0000000000a1"
TICKET = "11111111-2222-3333-4444-555555555555"
DEPARTMENT = "22222222-3333-4444-5555-666666666666"


class Result:
    def __init__(self, value: Any = "99999999-8888-7777-6666-555555555555"):
        self.value = value

    def scalar_one(self) -> Any:
        return self.value


class RecordingConnection:
    def __init__(self) -> None:
        self.statements: list[tuple[str, dict[str, Any]]] = []
        self.transactions = 0

    def execute(self, statement: Any, params: dict[str, Any] | None = None) -> Result:
        self.statements.append((str(statement), dict(params or {})))
        return Result()

    def kinds(self) -> list[str]:
        return [sql.split()[0] for sql, _ in self.statements]

    def matching(self, fragment: str) -> tuple[str, dict[str, Any]]:
        return next((sql, params) for sql, params in self.statements if fragment in sql)


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


def conversation(**overrides: Any) -> Conversation:
    return Conversation(
        organization_id=ORG,
        ticket_id=TICKET,
        subject="s",
        body="b",
        message_id="33333333-4444-5555-6666-777777777777",
        **overrides,
    )


def outcome(**overrides: Any) -> TriageOutcome:
    base: dict[str, Any] = {
        "correlation_id": "triage-1",
        "route": "AUTO_RESOLVE",
        "category": "technical",
        "urgency": "low",
        "confidence": 0.91,
        "rule_codes": ("A",),
        "decision_summary": "ok",
        "draft": "answer [KB-001]",
        "citations": ("KB-001",),
        "grounding_validated": True,
        "grounding_details": {"valid": True},
        "mcp_connected": True,
        "processing_ms": 120,
        "model_used": "openai/gpt",
        "prompt_tokens": 10,
        "completion_tokens": 5,
        "cost_micro": 4321,
        "trace": ({"node": "perceive"},),
    }
    return TriageOutcome(**{**base, **overrides})


@pytest.fixture
def engine() -> RecordingEngine:
    return RecordingEngine()


def test_a_run_is_one_transaction(engine):
    PostgresTriageStore(engine).record_run(conversation(), outcome())
    assert engine.connection.transactions == 1
    assert engine.connection.kinds() == ["INSERT", "INSERT", "UPDATE"]


def test_the_run_is_written_with_its_evidence(engine):
    PostgresTriageStore(engine).record_run(conversation(), outcome())
    sql, params = engine.connection.matching("INSERT INTO triage_runs")

    assert params["org"] == ORG and params["ticket"] == TICKET
    assert params["correlation"] == "triage-1"
    assert params["rule_codes"] == ["A"] and params["citations"] == ["KB-001"]
    assert params["grounded"] is True and params["mcp"] is True
    assert '"node": "perceive"' in params["trace"]
    assert "CAST(:trace AS jsonb)" in sql
    # A bind parameter is cast with CAST(...), never `:param::type`, which the
    # driver escapes and Alembic renders wrongly (C-D040's lesson).
    assert not re.search(r":\w+::", sql)


def test_a_rerun_updates_its_own_row_rather_than_forking_the_history(engine):
    PostgresTriageStore(engine).record_run(conversation(), outcome())
    sql, _ = engine.connection.matching("INSERT INTO triage_runs")
    assert "ON CONFLICT (organization_id, correlation_id) DO UPDATE" in sql


def test_the_action_records_the_system_as_the_actor_and_the_cost(engine):
    PostgresTriageStore(engine).record_run(conversation(), outcome())
    sql, params = engine.connection.matching("INSERT INTO actions")
    assert "'TRIAGE_RUN'" in sql and "actor_membership_id" not in sql
    assert params["key"] == "triage-1"
    assert '"cost_micro_units": 4321' in params["payload"]


def test_routing_applies_what_the_rules_decided(engine):
    PostgresTriageStore(engine).record_run(
        conversation(),
        outcome(
            assignments={"department_id": DEPARTMENT, "queue_id": DEPARTMENT, "urgency": "high"}
        ),
    )
    sql, params = engine.connection.matching("UPDATE tickets")
    assert "department_id = CAST(:department_id AS uuid)" in sql
    assert "queue_id = CAST(:queue_id AS uuid)" in sql
    assert "version = version + 1" in sql
    assert params["department_id"] == DEPARTMENT
    assert params["org"] == ORG and "organization_id = CAST(:org AS uuid)" in sql


@pytest.mark.parametrize(
    "value", ["not-an-id", "", "'; DROP TABLE tickets; --", 42, None, "q-billing"]
)
def test_only_an_identifier_reaches_a_foreign_key_column(engine, value):
    PostgresTriageStore(engine).record_run(conversation(), outcome(assignments={"queue_id": value}))
    sql, params = engine.connection.matching("UPDATE tickets")
    assert "queue_id" not in sql
    assert value not in params.values() or value in (None,)


def test_the_ticket_takes_the_route_and_confidence_the_run_produced(engine):
    PostgresTriageStore(engine).record_run(conversation(), outcome(route="ESCALATE"))
    sql, params = engine.connection.matching("UPDATE tickets")
    assert "route = CAST(:route AS ticket_route)" in sql
    assert params["route"] == "ESCALATE" and params["confidence"] == 0.91


def test_service_level_targets_are_written_when_a_policy_applied(engine):
    due = datetime(2026, 9, 17, 17, 0, tzinfo=UTC)
    PostgresTriageStore(engine).record_run(
        conversation(),
        outcome(
            sla_policy_id="44444444-5555-6666-7777-888888888888",
            first_response_due_at=due,
            sla_state="ON_TRACK",
        ),
    )
    sql, params = engine.connection.matching("INSERT INTO ticket_sla_states")
    assert params["first_due"] == due
    assert "ON CONFLICT (ticket_id) DO UPDATE" in sql
    # A breach already recorded is never erased by a later run.
    assert "WHERE ticket_sla_states.state <> 'BREACHED'" in sql


def test_no_targets_are_written_when_no_policy_applied(engine):
    PostgresTriageStore(engine).record_run(conversation(), outcome())
    assert not any("ticket_sla_states" in sql for sql, _ in engine.connection.statements)


def test_every_statement_is_scoped_to_one_organization(engine):
    PostgresTriageStore(engine).record_run(conversation(), outcome())
    for sql, params in engine.connection.statements:
        assert params.get("org") == ORG
        assert "organization_id" in sql


def test_a_policy_reference_the_database_cannot_hold_does_not_lose_the_run(engine):
    due = datetime(2026, 9, 17, 17, 0, tzinfo=UTC)
    PostgresTriageStore(engine).record_run(
        conversation(),
        outcome(sla_policy_id="p-1", first_response_due_at=due, sla_state="ON_TRACK"),
    )
    _, params = engine.connection.matching("INSERT INTO ticket_sla_states")
    assert params["policy"] is None
    assert params["first_due"] == due, "the target is still recorded"
