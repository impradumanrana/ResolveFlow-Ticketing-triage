"""Where deterministic decisions override model output.

This module is the point the C09 gate is really about. Three kinds of judgement
meet here, and the order they meet in is the whole safety property:

1. **Safety, decided by code.** ``evaluate_guardrails`` is the MVP's proven
   guardrail set. It is imported, never reimplemented, and it runs first.
2. **Routing, decided by the client's rules.** Versioned rules may change where
   a conversation goes, who owns it, and how urgent it is.
3. **Service levels, computed** from the resulting scope in the policy's own
   time zone.

The invariant, stated plainly because it is the one that matters: *a client
rule can send a conversation to a person, but no client rule can send a
conversation to the model.* Rules add escalation; they never remove it. The
final route is computed with the MVP's exact precedence from ``app.graph`` and
then narrowed - never widened - by the rule layer.

Fail-safe: anything the engine cannot resolve confidently - conflicting rules,
an unparseable rule, a conflicting service-level policy - routes to a human.
An unresolvable configuration must never quietly become an automatic reply.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime
from typing import Any

from app.config import KB_THRESHOLD
from app.graph import BLOCKING_RISK_CODES
from app.guardrails import evaluate_guardrails
from app.rules.routing import RoutingOutcome, RoutingRule, evaluate_rules, vip_tier
from app.rules.sla import SlaPolicy, SlaTargets, TicketScope, compute_targets

# The MVP's classification floor, kept in one named place rather than repeated
# as a literal in a second decision site.
CLASSIFICATION_CONFIDENCE_FLOOR = 0.65

# Codes this layer adds. Each names a reason a human is needed, so the
# workspace can explain the route rather than showing a bare "ESCALATE".
RULE_CONFLICT = "RULE_CONFLICT"
RULE_INVALID = "RULE_INVALID"
RULE_FORCED_ESCALATION = "RULE_FORCED_ESCALATION"
SLA_POLICY_CONFLICT = "SLA_POLICY_CONFLICT"

# Every code this layer can introduce that means "a person must look".
FAIL_SAFE_CODES = frozenset(
    {RULE_CONFLICT, RULE_INVALID, RULE_FORCED_ESCALATION, SLA_POLICY_CONFLICT}
)


@dataclass(frozen=True)
class Conversation:
    """The facts a rule may be evaluated against."""

    subject: str = ""
    body: str = ""
    from_address: str = ""
    mailbox_address: str = ""
    category: str = ""
    urgency: str = ""
    has_attachments: bool = False
    queue_id: str | None = None
    department_id: str | None = None
    received_at: datetime | None = None

    @property
    def normalized_text(self) -> str:
        """The same subject-and-body join app.graph feeds the guardrails."""
        return f"{self.subject}\n{self.body}".strip()

    @property
    def from_domain(self) -> str:
        address = (self.from_address or "").strip().lower()
        return address.rsplit("@", 1)[1] if "@" in address else ""


@dataclass(frozen=True)
class Evidence:
    """What the model and the knowledge base contributed.

    Defaults are the cautious ones: no confidence, no match, so a caller that
    forgets to pass evidence gets a human, not an automatic reply.
    """

    classification_confidence: float = 0.0
    kb_top_score: float = 0.0
    mcp_error: str | None = None
    provider_error: str | None = None


@dataclass(frozen=True)
class RuleOutcome:
    decision: str
    rule_codes: tuple[str, ...]
    assignments: dict[str, Any]
    sla: SlaTargets
    matched_rules: tuple[str, ...] = ()
    conflicts: tuple[str, ...] = ()
    errors: tuple[str, ...] = ()
    vip_tier: str | None = None

    @property
    def needs_human(self) -> bool:
        return self.decision != "AUTO_RESOLVE"


def base_decision(rule_codes: list[str], evidence: Evidence) -> tuple[str, list[str]]:
    """The MVP's route precedence, unchanged.

    Reproduced here rather than called because the graph's version reads and
    writes LangGraph state. The ordering is asserted against ``app.graph`` in
    the C09 tests so the two cannot drift apart unnoticed.
    """
    codes = list(rule_codes)

    if evidence.mcp_error:
        codes.append("MCP_UNAVAILABLE")
        return "ESCALATE", codes
    if "MODEL_ERROR" in codes:
        return "ESCALATE", codes
    if any(code in BLOCKING_RISK_CODES for code in codes):
        return "ESCALATE", codes
    if "MISSING_INFORMATION" in codes:
        return "CLARIFY", codes
    if evidence.classification_confidence < CLASSIFICATION_CONFIDENCE_FLOOR:
        codes.append("LOW_CLASSIFICATION_CONFIDENCE")
        return "ESCALATE", codes
    if evidence.kb_top_score < KB_THRESHOLD:
        codes.append("LOW_KB_CONFIDENCE")
        return "ESCALATE", codes
    return "AUTO_RESOLVE", codes


def apply(
    conversation: Conversation,
    *,
    rules: list[RoutingRule],
    policies: list[SlaPolicy],
    evidence: Evidence | None = None,
    vip_addresses: dict[str, str] | None = None,
    vip_domains: dict[str, str] | None = None,
    holidays: frozenset[date] = frozenset(),
    department_holidays: dict[str, frozenset[date]] | None = None,
    invalid_rules: tuple[str, ...] = (),
    now: datetime | None = None,
) -> RuleOutcome:
    """Run the three stages in order and return one explainable decision."""
    evidence = evidence or Evidence()
    moment = now or conversation.received_at or datetime.now(UTC)
    received = conversation.received_at or moment

    # Stage 1: safety, by code, before anything configurable runs.
    # The fact keys are exactly the ones app.graph.risk_guard passes. Sending a
    # differently shaped dict would leave the guardrails reading empty text and
    # silently finding no risk, so the shape is asserted in the C09 tests.
    guardrails = evaluate_guardrails(
        {
            "urgency": conversation.urgency,
            "content": conversation.normalized_text,
        }
    )
    safety_codes = list(guardrails.get("rule_codes", []))
    if evidence.provider_error:
        # Mirrors risk_guard: a model failure is a guardrail code, not a
        # silently degraded answer.
        safety_codes.append("MODEL_ERROR")

    tier = vip_tier(
        conversation.from_address,
        addresses=vip_addresses or {},
        domains=vip_domains or {},
    )

    # Stage 2: client rules route. They see the safety codes so a client can
    # send, say, THREAT_DETECTED to a named team - but the codes are read-only
    # here and are carried through untouched.
    facts: dict[str, Any] = {
        "subject": conversation.subject,
        "body": conversation.body,
        "from_address": conversation.from_address,
        "from_domain": conversation.from_domain,
        "mailbox_address": conversation.mailbox_address,
        "category": conversation.category,
        "urgency": conversation.urgency,
        "vip_tier": tier or "",
        "has_attachments": conversation.has_attachments,
        "rule_codes": safety_codes,
    }
    routing: RoutingOutcome = evaluate_rules(rules, facts, now=moment)

    codes = list(safety_codes)
    if routing.conflicts:
        codes.append(RULE_CONFLICT)
    errors = tuple(invalid_rules) + routing.errors
    if errors:
        # A stored rule that no longer parses might have been the one meant to
        # catch this conversation. Its absence is not evidence of safety.
        codes.append(RULE_INVALID)
    if routing.assignments.get("escalate"):
        codes.append(RULE_FORCED_ESCALATION)

    # Stage 3: service levels, against the scope the rules produced.
    scope = TicketScope(
        queue_id=routing.assignments.get("queue_id", conversation.queue_id),
        department_id=routing.assignments.get("department_id", conversation.department_id),
        urgency=routing.assignments.get("urgency", conversation.urgency) or None,
    )
    # Holidays are chosen after routing, because the department that observes
    # them is only known now.
    observed = holidays | (department_holidays or {}).get(scope.department_id or "", frozenset())
    sla = compute_targets(policies, scope, received_at=received, holidays=observed)
    if sla.reason == SLA_POLICY_CONFLICT:
        codes.append(SLA_POLICY_CONFLICT)

    decision, codes = base_decision(codes, evidence)

    # The narrowing step. Nothing below may turn a human route into an
    # automatic one - only the other way.
    if decision == "AUTO_RESOLVE" and any(code in FAIL_SAFE_CODES for code in codes):
        decision = "ESCALATE"
    elif decision == "CLARIFY" and any(
        code in (RULE_CONFLICT, RULE_INVALID, RULE_FORCED_ESCALATION) for code in codes
    ):
        # Asking the customer a question is still an automatic outward action.
        decision = "ESCALATE"

    return RuleOutcome(
        decision=decision,
        rule_codes=tuple(dict.fromkeys(codes)),
        assignments=dict(routing.assignments),
        sla=sla,
        matched_rules=routing.matched_rules,
        conflicts=routing.conflicts,
        errors=errors,
        vip_tier=tier,
    )
