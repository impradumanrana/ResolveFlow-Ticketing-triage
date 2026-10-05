"""Loading the rules in force, and publishing new versions of them.

Reads are scoped to one organization and one moment. Evaluating "the rules"
without a moment would let a rule scheduled for next week route mail today.

Writes never edit a rule in place. Publishing closes the current version's
effective window and inserts the next version in the same transaction, so the
history of what routed a conversation, and when, survives every change.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any

from sqlalchemy import text

from app.rules.engine import RuleOutcome
from app.rules.routing import RoutingRule, RuleError, is_valid_domain, parse_rule
from app.rules.sla import SlaPolicy, SlaStatus


@dataclass(frozen=True)
class RuleSet:
    rules: list[RoutingRule] = field(default_factory=list)
    invalid_rules: tuple[str, ...] = ()
    policies: list[SlaPolicy] = field(default_factory=list)
    vip_addresses: dict[str, str] = field(default_factory=dict)
    vip_domains: dict[str, str] = field(default_factory=dict)
    holidays: frozenset[date] = frozenset()
    department_holidays: dict[str, frozenset[date]] = field(default_factory=dict)

    def engine_arguments(self) -> dict[str, Any]:
        """Keyword arguments for ``engine.apply``, so callers cannot miss one."""
        return {
            "rules": self.rules,
            "invalid_rules": self.invalid_rules,
            "policies": self.policies,
            "vip_addresses": self.vip_addresses,
            "vip_domains": self.vip_domains,
            "holidays": self.holidays,
            "department_holidays": self.department_holidays,
        }


def build_rule_set(
    *,
    rule_rows: list[dict[str, Any]],
    policy_rows: list[dict[str, Any]],
    vip_rows: list[dict[str, Any]],
    holiday_rows: list[dict[str, Any]],
) -> RuleSet:
    """Turn rows into a rule set. Pure, so the parsing is tested without a database."""
    rules: list[RoutingRule] = []
    invalid: list[str] = []
    for row in rule_rows:
        try:
            rules.append(parse_rule(row))
        except (RuleError, TypeError, ValueError) as error:
            # Recorded, not dropped: the engine turns this into RULE_INVALID.
            invalid.append(f"{row.get('name', 'unnamed')} v{row.get('version', '?')}: {error}")

    policies = [
        SlaPolicy(
            id=str(row["id"]),
            name=str(row["name"]),
            first_response_minutes=int(row["first_response_minutes"]),
            resolution_minutes=(
                int(row["resolution_minutes"])
                if row.get("resolution_minutes") is not None
                else None
            ),
            queue_id=_optional(row.get("queue_id")),
            department_id=_optional(row.get("department_id")),
            urgency=_optional(row.get("urgency")),
            time_zone=str(row.get("time_zone") or "Etc/UTC"),
            business_hours_only=bool(row.get("business_hours_only", True)),
            day_start_minute=int(row.get("business_day_start_minute", 540)),
            day_end_minute=int(row.get("business_day_end_minute", 1020)),
            business_days=tuple(int(day) for day in (row.get("business_days") or (1, 2, 3, 4, 5))),
            version=int(row.get("version", 1)),
        )
        for row in policy_rows
    ]

    addresses: dict[str, str] = {}
    domains: dict[str, str] = {}
    for row in vip_rows:
        if row.get("email_address"):
            addresses[str(row["email_address"]).lower()] = str(row["tier"])
        elif row.get("email_domain"):
            domains[str(row["email_domain"]).lower()] = str(row["tier"])

    organization_wide: set[date] = set()
    by_department: dict[str, set[date]] = {}
    for row in holiday_rows:
        if row.get("department_id"):
            by_department.setdefault(str(row["department_id"]), set()).add(row["holiday_date"])
        else:
            organization_wide.add(row["holiday_date"])

    return RuleSet(
        rules=rules,
        invalid_rules=tuple(invalid),
        policies=policies,
        vip_addresses=addresses,
        vip_domains=domains,
        holidays=frozenset(organization_wide),
        department_holidays={key: frozenset(value) for key, value in by_department.items()},
    )


def _optional(value: Any) -> str | None:
    return str(value) if value is not None else None


class PostgresRuleStore:
    def __init__(self, connection: Any):
        self.connection = connection

    def _rows(self, sql: str, **params: Any) -> list[dict[str, Any]]:
        result = self.connection.execute(text(sql), params)
        return [dict(row._mapping) for row in result]

    def load(self, organization_id: str, *, at: datetime) -> RuleSet:
        # Every query filters by organization. Nothing here is shared between
        # clients, and the in-force window is applied in SQL as well as in the
        # evaluator so a caller cannot forget it.
        in_force = (
            "organization_id = CAST(:org AS uuid) AND effective_from <= :at "
            "AND (effective_to IS NULL OR effective_to > :at)"
        )
        rule_rows = self._rows(
            "SELECT id::text AS id, name, priority, version, enabled, effective_from, "
            "effective_to, conditions, actions FROM routing_rules "
            f"WHERE enabled AND {in_force} ORDER BY priority, name, version",
            org=organization_id,
            at=at,
        )
        policy_rows = self._rows(
            "SELECT id::text AS id, name, first_response_minutes, resolution_minutes, "
            "queue_id::text AS queue_id, department_id::text AS department_id, "
            "urgency::text AS urgency, time_zone, business_hours_only, "
            "business_day_start_minute, business_day_end_minute, business_days, version "
            f"FROM sla_policies WHERE {in_force} ORDER BY name, version",
            org=organization_id,
            at=at,
        )
        vip_rows = self._rows(
            "SELECT email_address, email_domain, tier FROM vip_contacts "
            "WHERE organization_id = CAST(:org AS uuid)",
            org=organization_id,
        )
        holiday_rows = self._rows(
            "SELECT holiday_date, department_id::text AS department_id FROM business_holidays "
            "WHERE organization_id = CAST(:org AS uuid)",
            org=organization_id,
        )
        return build_rule_set(
            rule_rows=rule_rows,
            policy_rows=policy_rows,
            vip_rows=vip_rows,
            holiday_rows=holiday_rows,
        )

    def publish_rule(
        self,
        organization_id: str,
        payload: dict[str, Any],
        *,
        at: datetime,
        membership_id: str | None = None,
    ) -> int:
        """Validate and publish the next version of a named rule. Returns the version."""
        # Validation happens before any write: an invalid rule is refused at the
        # door rather than stored and flagged at evaluation time.
        rule = parse_rule(payload)

        scheduled = self.connection.execute(
            text(
                "SELECT count(*) FROM routing_rules WHERE organization_id = CAST(:org AS uuid) "
                "AND name = :name AND effective_from >= :at"
            ),
            {"org": organization_id, "name": rule.name, "at": at},
        ).scalar_one()
        if scheduled:
            # That version cannot be closed before it opens, so publishing now
            # would leave two versions of one rule in force at once.
            raise RuleError(
                f"A version of {rule.name!r} is already scheduled at or after this time."
            )

        current = self.connection.execute(
            text(
                "SELECT COALESCE(max(version), 0) FROM routing_rules "
                "WHERE organization_id = CAST(:org AS uuid) AND name = :name"
            ),
            {"org": organization_id, "name": rule.name},
        ).scalar_one()
        next_version = int(current) + 1

        self.connection.execute(
            text(
                "UPDATE routing_rules SET effective_to = :at, updated_at = now() "
                "WHERE organization_id = CAST(:org AS uuid) AND name = :name "
                "AND effective_to IS NULL AND effective_from < :at"
            ),
            {"org": organization_id, "name": rule.name, "at": at},
        )
        self.connection.execute(
            text(
                "INSERT INTO routing_rules (organization_id, name, description, priority, "
                "version, enabled, effective_from, conditions, actions, created_by_membership_id) "
                "VALUES (CAST(:org AS uuid), :name, :description, :priority, :version, :enabled, "
                ":at, CAST(:conditions AS jsonb), CAST(:actions AS jsonb), "
                "CAST(:membership AS uuid))"
            ),
            {
                "org": organization_id,
                "name": rule.name,
                "description": payload.get("description"),
                "priority": rule.priority,
                "version": next_version,
                "enabled": rule.enabled,
                "at": at,
                "conditions": json.dumps(payload.get("conditions") or {}),
                "actions": json.dumps(rule.actions),
                "membership": membership_id,
            },
        )
        return next_version

    def add_vip(self, organization_id: str, value: str, *, tier: str = "VIP") -> None:
        value = value.strip().lower()
        if "@" in value:
            column = "email_address"
        elif is_valid_domain(value):
            column = "email_domain"
        else:
            raise RuleError(f"{value!r} is neither an address nor a domain.")
        self.connection.execute(
            text(
                f"INSERT INTO vip_contacts (organization_id, {column}, tier) "
                "VALUES (CAST(:org AS uuid), :value, :tier)"
            ),
            {"org": organization_id, "value": value, "tier": tier},
        )

    def record_targets(
        self, organization_id: str, ticket_id: str, outcome: RuleOutcome, status: SlaStatus
    ) -> None:
        """Store the computed targets against the ticket, idempotently."""
        breached = status.state == "BREACHED"
        self.connection.execute(
            text(
                "INSERT INTO ticket_sla_states (organization_id, ticket_id, sla_policy_id, "
                "first_response_due_at, resolution_due_at, state, breached_at) "
                "VALUES (CAST(:org AS uuid), CAST(:ticket AS uuid), CAST(:policy AS uuid), "
                ":first_due, :resolution_due, CAST(:state AS sla_state), "
                "CASE WHEN :breached THEN now() END) "
                "ON CONFLICT (ticket_id) DO UPDATE SET "
                "sla_policy_id = EXCLUDED.sla_policy_id, "
                "first_response_due_at = EXCLUDED.first_response_due_at, "
                "resolution_due_at = EXCLUDED.resolution_due_at, "
                "state = EXCLUDED.state, "
                # Keep the first breach time; a recomputation must not move it.
                "breached_at = CASE WHEN EXCLUDED.state = 'BREACHED' "
                "THEN COALESCE(ticket_sla_states.breached_at, EXCLUDED.breached_at) END, "
                "updated_at = now()"
            ),
            {
                "org": organization_id,
                "ticket": ticket_id,
                "policy": outcome.sla.policy_id,
                "first_due": outcome.sla.first_response_due_at,
                "resolution_due": outcome.sla.resolution_due_at,
                "state": status.state,
                "breached": breached,
            },
        )
