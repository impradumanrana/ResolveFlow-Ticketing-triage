"""Versioned client rules that decide where a conversation goes.

Rules are data, not code. The condition language is deliberately small and
closed: a rule can match on a fixed set of facts with a fixed set of operators,
and can set a fixed set of outcomes. A client's support manager should be able
to route mail without anyone being able to express "run this".

How conflicts resolve, and why:

* Rules run in priority order, lowest number first.
* The first rule to set a field wins it. A later rule may still set fields the
  earlier one left alone, so rules compose without overwriting each other.
* Two *enabled, equally prioritised* rules setting the same field to different
  values is a genuine conflict. The engine does not pick one - it reports the
  conflict and the conversation goes to a person. Guessing would make routing
  depend on row order, which is invisible and unexplainable later.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

# The complete set of facts a rule may examine.
MATCHABLE_FIELDS = frozenset(
    {
        "subject",
        "body",
        "from_address",
        "from_domain",
        "mailbox_address",
        "category",
        "urgency",
        "vip_tier",
        "has_attachments",
        "rule_codes",
    }
)

# The complete set of outcomes a rule may set.
ASSIGNABLE_ACTIONS = frozenset(
    {
        "department_id",
        "queue_id",
        "urgency",
        "assign_to_membership_id",
        "escalate",
        "tag",
    }
)

# Matches the ticket_urgency enum from the C04 schema.
URGENCIES = ("low", "medium", "high", "critical")

OPERATORS = frozenset({"equals", "not_equals", "contains", "in", "not_in", "ends_with", "any_of"})

MAX_CONDITIONS = 20
MAX_VALUE_LENGTH = 200


class RuleError(ValueError):
    """A rule that cannot be evaluated. Always surfaced, never skipped silently."""


@dataclass(frozen=True)
class Condition:
    field_name: str
    operator: str
    value: Any

    def matches(self, facts: dict[str, Any]) -> bool:
        actual = facts.get(self.field_name)

        if self.operator == "equals":
            return _text(actual) == _text(self.value)
        if self.operator == "not_equals":
            return _text(actual) != _text(self.value)
        if self.operator == "contains":
            return _text(self.value) in _text(actual)
        if self.operator == "ends_with":
            return _text(actual).endswith(_text(self.value))
        if self.operator == "in":
            return _text(actual) in {_text(item) for item in _as_list(self.value)}
        if self.operator == "not_in":
            return _text(actual) not in {_text(item) for item in _as_list(self.value)}
        if self.operator == "any_of":
            # For list-valued facts such as rule codes.
            present = {_text(item) for item in _as_list(actual)}
            return bool(present & {_text(item) for item in _as_list(self.value)})
        raise RuleError(f"Unknown operator {self.operator!r}")


def _text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value).strip().lower()


def _as_list(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, (list, tuple, set, frozenset)):
        return list(value)
    return [value]


@dataclass(frozen=True)
class RoutingRule:
    id: str
    name: str
    priority: int
    version: int
    conditions: tuple[Condition, ...]
    actions: dict[str, Any]
    enabled: bool = True
    effective_from: datetime | None = None
    effective_to: datetime | None = None

    def in_force(self, moment: datetime) -> bool:
        if not self.enabled:
            return False
        if self.effective_from and moment < self.effective_from:
            return False
        if self.effective_to and moment >= self.effective_to:
            return False
        return True

    def matches(self, facts: dict[str, Any]) -> bool:
        # No conditions means "always", which is how a default rule is written.
        return all(condition.matches(facts) for condition in self.conditions)


def parse_rule(payload: dict[str, Any]) -> RoutingRule:
    """Build a rule from stored JSON, refusing anything outside the language."""
    raw_conditions = payload.get("conditions") or {}
    clauses = raw_conditions.get("all") if isinstance(raw_conditions, dict) else None
    clauses = clauses or []
    if not isinstance(clauses, list):
        raise RuleError("Conditions must be a list under 'all'.")
    if len(clauses) > MAX_CONDITIONS:
        raise RuleError(f"A rule may have at most {MAX_CONDITIONS} conditions.")

    conditions: list[Condition] = []
    for clause in clauses:
        if not isinstance(clause, dict):
            raise RuleError("Each condition must be an object.")
        field_name = str(clause.get("field") or "")
        operator = str(clause.get("operator") or "")
        if field_name not in MATCHABLE_FIELDS:
            raise RuleError(f"Rules cannot match on {field_name!r}.")
        if operator not in OPERATORS:
            raise RuleError(f"Unknown operator {operator!r}.")
        value = clause.get("value")
        if isinstance(value, str) and len(value) > MAX_VALUE_LENGTH:
            raise RuleError("Condition value is too long.")
        conditions.append(Condition(field_name, operator, value))

    actions = payload.get("actions") or {}
    if not isinstance(actions, dict) or not actions:
        raise RuleError("A rule must set at least one action.")
    unknown = set(actions) - ASSIGNABLE_ACTIONS
    if unknown:
        raise RuleError(f"Rules cannot set {', '.join(sorted(unknown))}.")
    if "urgency" in actions and actions["urgency"] not in URGENCIES:
        raise RuleError(f"Urgency must be one of {', '.join(URGENCIES)}.")
    if "escalate" in actions and not isinstance(actions["escalate"], bool):
        raise RuleError("escalate must be true or false.")
    for key in ("department_id", "queue_id", "assign_to_membership_id", "tag"):
        if key in actions and (not isinstance(actions[key], str) or not actions[key].strip()):
            raise RuleError(f"{key} must be a non-empty string.")

    return RoutingRule(
        id=str(payload.get("id") or ""),
        name=str(payload.get("name") or "unnamed"),
        priority=int(payload.get("priority", 100)),
        version=int(payload.get("version", 1)),
        conditions=tuple(conditions),
        actions=dict(actions),
        enabled=bool(payload.get("enabled", True)),
        effective_from=payload.get("effective_from"),
        effective_to=payload.get("effective_to"),
    )


@dataclass(frozen=True)
class RoutingOutcome:
    assignments: dict[str, Any] = field(default_factory=dict)
    matched_rules: tuple[str, ...] = ()
    conflicts: tuple[str, ...] = ()
    errors: tuple[str, ...] = ()

    @property
    def has_problem(self) -> bool:
        return bool(self.conflicts or self.errors)


def evaluate_rules(
    rules: list[RoutingRule], facts: dict[str, Any], *, now: datetime
) -> RoutingOutcome:
    """Apply rules in force, first-wins per field, reporting genuine conflicts."""
    in_force = [rule for rule in rules if rule.in_force(now)]
    # Priority first, then name, so evaluation order never depends on the order
    # rows came back from the database.
    in_force.sort(key=lambda rule: (rule.priority, rule.name, rule.version))

    assignments: dict[str, Any] = {}
    decided_by: dict[str, RoutingRule] = {}
    matched: list[str] = []
    conflicts: list[str] = []
    errors: list[str] = []

    for rule in in_force:
        try:
            if not rule.matches(facts):
                continue
        except RuleError as error:
            errors.append(f"{rule.name}: {error}")
            continue

        matched.append(rule.name)
        for key, value in rule.actions.items():
            if key not in assignments:
                assignments[key] = value
                decided_by[key] = rule
                continue

            winner = decided_by[key]
            if assignments[key] == value:
                continue
            if winner.priority == rule.priority:
                # Same priority, same field, different answers: unresolvable
                # without asking the client which they meant.
                conflicts.append(
                    f"{key}: {winner.name} says {assignments[key]!r}, "
                    f"{rule.name} says {value!r} at the same priority"
                )
            # A lower-priority rule simply loses; that is not a conflict.

    return RoutingOutcome(
        assignments=assignments,
        matched_rules=tuple(matched),
        conflicts=tuple(dict.fromkeys(conflicts)),
        errors=tuple(errors),
    )


def vip_tier(
    from_address: str | None,
    *,
    addresses: dict[str, str],
    domains: dict[str, str],
) -> str | None:
    """VIP tier for a sender: exact address first, then domain."""
    if not from_address or "@" not in from_address:
        return None
    address = from_address.strip().lower()
    if address in addresses:
        return addresses[address]
    return domains.get(address.rsplit("@", 1)[1])


_DOMAIN = re.compile(r"^[a-z0-9]([a-z0-9-]*[a-z0-9])?(\.[a-z0-9]([a-z0-9-]*[a-z0-9])?)+$")


def is_valid_domain(value: str) -> bool:
    return bool(_DOMAIN.fullmatch(value.strip().lower()))
