"""What the pipeline is given, and what it produces."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Protocol

# Most restrictive last: a route may be narrowed toward a person, never widened.
ROUTE_ORDER = ("AUTO_RESOLVE", "CLARIFY", "ESCALATE")


def narrower(first: str, second: str) -> str:
    return max(first, second, key=ROUTE_ORDER.index)


@dataclass(frozen=True)
class Conversation:
    """One inbound message, as the pipeline needs it."""

    organization_id: str
    ticket_id: str
    subject: str
    body: str
    message_id: str | None = None
    customer_id: str | None = None
    from_address: str = ""
    mailbox_address: str = ""
    queue_id: str | None = None
    department_id: str | None = None
    urgency: str = ""
    has_attachments: bool = False
    received_at: datetime | None = None


@dataclass(frozen=True)
class TriageOutcome:
    correlation_id: str
    route: str
    category: str
    urgency: str
    confidence: float
    rule_codes: tuple[str, ...]
    decision_summary: str
    draft: str | None
    citations: tuple[str, ...]
    grounding_validated: bool
    grounding_details: dict[str, Any]
    mcp_connected: bool
    processing_ms: int
    model_used: str | None
    prompt_tokens: int
    completion_tokens: int
    cost_micro: int
    trace: tuple[dict[str, Any], ...]
    assignments: dict[str, Any] = field(default_factory=dict)
    sla_policy_id: str | None = None
    first_response_due_at: datetime | None = None
    resolution_due_at: datetime | None = None
    sla_state: str = "NOT_APPLICABLE"
    run_id: str | None = None

    @property
    def needs_human(self) -> bool:
        return self.route != "AUTO_RESOLVE"


class TriageStore(Protocol):
    def record_run(self, conversation: Conversation, outcome: TriageOutcome) -> str:
        """Persist the run, its action, the routing it produced, and its targets."""
        ...
