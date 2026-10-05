"""C09: loading and publishing client rules.

The SQL itself is proven against PostgreSQL in CLIENT_C09_TEST_REPORT.md. These
tests pin what can be checked without a database: parsing of stored rows,
tenant scoping of every read, and refusal before any write.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from typing import Any

import pytest

from app.rules.engine import Conversation, Evidence, apply
from app.rules.routing import RuleError
from app.rules.store import PostgresRuleStore, build_rule_set

NOW = datetime(2026, 9, 16, 9, 0, tzinfo=UTC)
ORG = "00000000-0000-0000-0000-00000000000a"

GOOD_RULE = {
    "id": "r1",
    "name": "billing",
    "priority": 10,
    "version": 2,
    "enabled": True,
    "effective_from": None,
    "effective_to": None,
    "conditions": {"all": [{"field": "subject", "operator": "contains", "value": "invoice"}]},
    "actions": {"queue_id": "q-billing"},
}
BROKEN_RULE = {
    **GOOD_RULE,
    "id": "r2",
    "name": "legacy",
    "version": 3,
    "conditions": {"all": [{"field": "subject", "operator": "regex", "value": ".*"}]},
}


def policy_row(**overrides: Any) -> dict[str, Any]:
    row = {
        "id": "p1",
        "name": "Default",
        "first_response_minutes": 60,
        "resolution_minutes": None,
        "queue_id": None,
        "department_id": None,
        "urgency": None,
        "time_zone": "Europe/London",
        "business_hours_only": True,
        "business_day_start_minute": 540,
        "business_day_end_minute": 1020,
        "business_days": [1, 2, 3, 4, 5],
        "version": 1,
    }
    row.update(overrides)
    return row


def test_rows_become_a_rule_set():
    rule_set = build_rule_set(
        rule_rows=[GOOD_RULE],
        policy_rows=[policy_row(urgency="critical", business_days=[1, 2, 3])],
        vip_rows=[
            {"email_address": "ceo@big.example", "email_domain": None, "tier": "PLATINUM"},
            {"email_address": None, "email_domain": "big.example", "tier": "GOLD"},
        ],
        holiday_rows=[
            {"holiday_date": date(2026, 12, 25), "department_id": None},
            {"holiday_date": date(2026, 12, 24), "department_id": "d-support"},
        ],
    )
    assert [rule.name for rule in rule_set.rules] == ["billing"]
    assert rule_set.invalid_rules == ()
    assert rule_set.policies[0].urgency == "critical"
    assert rule_set.policies[0].business_days == (1, 2, 3)
    assert rule_set.vip_addresses == {"ceo@big.example": "PLATINUM"}
    assert rule_set.vip_domains == {"big.example": "GOLD"}
    assert rule_set.holidays == frozenset({date(2026, 12, 25)})
    assert rule_set.department_holidays == {"d-support": frozenset({date(2026, 12, 24)})}


def test_a_broken_stored_rule_is_recorded_not_dropped():
    rule_set = build_rule_set(
        rule_rows=[GOOD_RULE, BROKEN_RULE], policy_rows=[], vip_rows=[], holiday_rows=[]
    )
    assert [rule.name for rule in rule_set.rules] == ["billing"]
    assert rule_set.invalid_rules == ("legacy v3: Unknown operator 'regex'.",)

    outcome = apply(
        Conversation(subject="Hello", body="How do I change my display name?", urgency="low"),
        evidence=Evidence(0.95, 0.9),
        now=NOW,
        **rule_set.engine_arguments(),
    )
    assert outcome.decision == "ESCALATE"
    assert "RULE_INVALID" in outcome.rule_codes


def test_engine_arguments_cover_every_rule_set_field():
    # A field added to RuleSet but not passed on would silently do nothing.
    from dataclasses import fields

    from app.rules.store import RuleSet

    assert set(RuleSet().engine_arguments()) == {field.name for field in fields(RuleSet)}


class Result:
    def __init__(self, rows: list[dict[str, Any]] | None = None, scalar: Any = 0):
        self.rows = rows or []
        self.scalar = scalar

    def __iter__(self):
        for row in self.rows:
            yield type("Row", (), {"_mapping": row})()

    def scalar_one(self) -> Any:
        return self.scalar


class RecordingConnection:
    def __init__(self, scalars: list[Any] | None = None):
        self.statements: list[tuple[str, dict[str, Any]]] = []
        self.scalars = list(scalars or [])

    def execute(self, statement: Any, params: dict[str, Any] | None = None) -> Result:
        self.statements.append((str(statement), dict(params or {})))
        return Result(scalar=self.scalars.pop(0) if self.scalars else 0)


def test_every_read_is_scoped_to_one_organization():
    connection = RecordingConnection()
    PostgresRuleStore(connection).load(ORG, at=NOW)

    assert len(connection.statements) == 4
    for sql, params in connection.statements:
        assert "organization_id = CAST(:org AS uuid)" in sql
        assert params["org"] == ORG


def test_rules_and_policies_are_read_as_of_a_moment():
    connection = RecordingConnection()
    PostgresRuleStore(connection).load(ORG, at=NOW)
    windowed = [
        sql
        for sql, _ in connection.statements
        if "FROM routing_rules" in sql or "FROM sla_policies" in sql
    ]
    assert len(windowed) == 2
    for sql in windowed:
        assert "effective_from <= :at" in sql
        assert "effective_to IS NULL OR effective_to > :at" in sql
    assert "WHERE enabled AND" in windowed[0]


def test_invalid_rules_are_refused_before_any_write():
    connection = RecordingConnection()
    with pytest.raises(RuleError):
        PostgresRuleStore(connection).publish_rule(ORG, BROKEN_RULE, at=NOW)
    assert connection.statements == []


def test_publishing_closes_the_previous_version_and_inserts_the_next():
    connection = RecordingConnection(scalars=[0, 2])  # nothing scheduled; current max version 2
    version = PostgresRuleStore(connection).publish_rule(ORG, GOOD_RULE, at=NOW, membership_id=None)

    assert version == 3
    kinds = [sql.split()[0] for sql, _ in connection.statements]
    assert kinds == ["SELECT", "SELECT", "UPDATE", "INSERT"]
    update_sql, update_params = connection.statements[2]
    assert "SET effective_to = :at" in update_sql and "effective_to IS NULL" in update_sql
    insert_sql, insert_params = connection.statements[3]
    assert insert_params["version"] == 3
    assert insert_params["at"] == NOW
    assert "CAST(:actions AS jsonb)" in insert_sql
    assert all("::" not in sql for sql, _ in connection.statements)


def test_publishing_over_a_scheduled_version_is_refused():
    connection = RecordingConnection(scalars=[1])
    with pytest.raises(RuleError, match="already scheduled"):
        PostgresRuleStore(connection).publish_rule(ORG, GOOD_RULE, at=NOW)
    assert [sql.split()[0] for sql, _ in connection.statements] == ["SELECT"]


@pytest.mark.parametrize(
    ("value", "column", "stored"),
    [
        ("CEO@Big.Example", "email_address", "ceo@big.example"),
        ("Big.Example", "email_domain", "big.example"),
    ],
)
def test_vip_entries_are_normalised_into_the_right_column(value, column, stored):
    connection = RecordingConnection()
    PostgresRuleStore(connection).add_vip(ORG, value, tier="GOLD")
    sql, params = connection.statements[0]
    assert f"({'organization_id'}, {column}, tier)" in sql
    assert params["value"] == stored


@pytest.mark.parametrize("value", ["not a domain", "", "localhost", "-bad.example", "a..b"])
def test_unusable_vip_entries_are_refused(value):
    connection = RecordingConnection()
    with pytest.raises(RuleError):
        PostgresRuleStore(connection).add_vip(ORG, value)
    assert connection.statements == []
