"""The production triage pipeline.

The workflow is the MVP's, compiled from `app.graph`, with production parts
plugged into its two seams: the provider is C10's gateway, and the knowledge
client is C05's corpus across the MCP boundary. Nothing in the proven path -
the prompts, the ordering, the grounding validator - is reimplemented here.

Around that, this module does what the MVP had no need for:

* decides the route again with C09's rules, from the same evidence, and keeps
  whichever answer is more cautious;
* applies the client's routing (department, queue, urgency, assignment) and
  computes service-level targets;
* records the run, its decision evidence, and its cost against the ticket.

`triage` never raises for an expected failure. A provider outage, a knowledge
outage, an unreadable key, or an exhausted budget all end in a stored run whose
route is a person, because a pipeline that throws leaves the conversation with
no record of why nothing happened.
"""

from __future__ import annotations

import time
import uuid
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

from app.gateway.errors import GatewayFailure
from app.gateway.mvp_provider import GatewayProvider
from app.gateway.service import AIGateway
from app.graph import build_workflow
from app.models import Ticket
from app.rules import engine as rules_engine
from app.rules.sla import evaluate_status
from app.rules.store import RuleSet
from app.triage.mcp_client import McpUnavailable
from app.triage.records import Conversation, TriageOutcome, TriageStore, narrower

# The gateway records its calls under the run's correlation id, so cost and
# provider attempts join back to the run without another table.
CORRELATION_PREFIX = "triage"


class TriagePipeline:
    def __init__(
        self,
        gateway: AIGateway,
        knowledge: Any,
        store: TriageStore,
        *,
        rule_set: RuleSet | None = None,
        now: Callable[[], datetime] = lambda: datetime.now(UTC),
    ):
        self.gateway = gateway
        self.knowledge = knowledge
        self.store = store
        self.rule_set = rule_set or RuleSet()
        self.now = now

    def triage(self, conversation: Conversation) -> TriageOutcome:
        started = time.perf_counter()
        correlation_id = f"{CORRELATION_PREFIX}-{uuid.uuid4()}"
        provider = GatewayProvider(self.gateway, conversation.organization_id, correlation_id)

        ticket = Ticket(
            ticket_id=conversation.ticket_id,
            customer_id=conversation.customer_id,
            subject=conversation.subject,
            body=conversation.body,
        )
        state: dict[str, Any] = {
            "ticket": ticket,
            "provider": provider,
            "mcp_client": self.knowledge,
            "trace": [],
        }
        state = build_workflow(provider, self.knowledge).invoke(state)
        result = state["result"]

        outcome = self._decide(conversation, state, result, provider, correlation_id, started)
        run_id = self.store.record_run(conversation, outcome)
        return replace_run_id(outcome, run_id)

    # ------------------------------------------------------------------

    def _decide(
        self,
        conversation: Conversation,
        state: dict[str, Any],
        result: Any,
        provider: GatewayProvider,
        correlation_id: str,
        started: float,
    ) -> TriageOutcome:
        top_score = float(result.kb_match.score) if result.kb_match else 0.0
        failure = provider.last_failure
        rule_outcome = rules_engine.apply(
            rules_engine.Conversation(
                subject=conversation.subject,
                body=conversation.body,
                from_address=conversation.from_address,
                mailbox_address=conversation.mailbox_address,
                category=result.category,
                urgency=result.urgency,
                has_attachments=conversation.has_attachments,
                queue_id=conversation.queue_id,
                department_id=conversation.department_id,
                received_at=conversation.received_at or self.now(),
            ),
            evidence=rules_engine.Evidence(
                classification_confidence=float(result.confidence),
                kb_top_score=top_score,
                mcp_error=state.get("mcp_error"),
                provider_error=state.get("provider_error")
                or (failure.code.value if failure else None),
            ),
            now=self.now(),
            **self.rule_set.engine_arguments(),
        )

        # Two independent decisions from the same evidence. They agree unless
        # the client's rules add something, and where they differ the more
        # cautious one stands.
        route = narrower(result.route, rule_outcome.decision)
        codes = tuple(
            dict.fromkeys(
                [
                    *result.rule_codes,
                    *rule_outcome.rule_codes,
                    *provider.rule_codes,
                    *([failure.code.value] if failure else []),
                ]
            )
        )
        draft, citations, grounded = (
            result.draft,
            tuple(result.citations),
            result.grounding_validated,
        )
        if route != result.route:
            # The rules narrowed the route after the draft was written, so the
            # draft is withheld rather than stored against a human route.
            draft, citations, grounded = _withheld(result, rule_outcome), (), False

        status = evaluate_status(rule_outcome.sla, now=self.now())
        return TriageOutcome(
            correlation_id=correlation_id,
            route=route,
            category=result.category,
            urgency=str(rule_outcome.assignments.get("urgency") or result.urgency),
            confidence=float(result.confidence),
            rule_codes=codes,
            decision_summary=result.decision_summary,
            draft=draft,
            citations=citations,
            grounding_validated=bool(grounded),
            grounding_details=dict(result.grounding_details or {}),
            mcp_connected=bool(result.mcp_connected),
            processing_ms=max(0, round((time.perf_counter() - started) * 1000)),
            model_used=provider.model,
            prompt_tokens=sum(run.prompt_tokens for run in provider.results),
            completion_tokens=sum(run.completion_tokens for run in provider.results),
            cost_micro=sum(run.cost_micro for run in provider.results),
            trace=_trace(state, correlation_id),
            assignments=dict(rule_outcome.assignments),
            sla_policy_id=rule_outcome.sla.policy_id,
            first_response_due_at=rule_outcome.sla.first_response_due_at,
            resolution_due_at=rule_outcome.sla.resolution_due_at,
            sla_state=status.state,
        )


def _withheld(result: Any, rule_outcome: Any) -> str:
    reasons = ", ".join(rule_outcome.rule_codes) or "a routing rule"
    return (
        f"Human review required. The answer candidate was withheld because {reasons} "
        "sent this conversation to a person."
    )


def _trace(state: dict[str, Any], correlation_id: str) -> tuple[dict[str, Any], ...]:
    """The workflow's trace, with the run's correlation id throughout.

    The workflow mints its own identifier; the run is stored under this one, and
    a trace that disagreed with the row above it would be read as two runs.
    """
    events: list[dict[str, Any]] = []
    for event in state.get("trace", []):
        item = event.model_dump() if hasattr(event, "model_dump") else dict(event)
        data = dict(item.get("data") or {})
        if "correlation_id" in data:
            data["correlation_id"] = correlation_id
        item["data"] = data
        events.append(item)
    return tuple(events)


def replace_run_id(outcome: TriageOutcome, run_id: str | None) -> TriageOutcome:
    from dataclasses import replace

    return replace(outcome, run_id=run_id)


__all__ = ["Conversation", "GatewayFailure", "McpUnavailable", "TriageOutcome", "TriagePipeline"]
