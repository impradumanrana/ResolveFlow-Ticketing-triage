"""Where a triage run is written.

One transaction per run, because the row, its action, the routing it applied,
and the service-level targets are one fact about the conversation. A partial
write would leave a ticket routed by rules nobody can see the reason for.

Idempotency is the correlation id: C04 makes it unique per organization, so a
retried job updates its own row instead of creating a second history for the
same conversation.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

from sqlalchemy import text

from app.triage.records import Conversation, TriageOutcome

_IDENTIFIER = r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$"


class PostgresTriageStore:
    def __init__(self, engine: Any):
        self._engine = engine

    @contextmanager
    def _tx(self) -> Iterator[Any]:
        with self._engine.begin() as connection:
            yield connection

    def record_run(self, conversation: Conversation, outcome: TriageOutcome) -> str:
        with self._tx() as c:
            run_id = c.execute(
                text(
                    "INSERT INTO triage_runs (organization_id, ticket_id, message_id, "
                    "correlation_id, route, category, urgency, confidence, rule_codes, "
                    "decision_summary, draft, citations, grounding_validated, mcp_connected, "
                    "processing_ms, model_used, prompt_tokens, completion_tokens, trace, "
                    "grounding_details) VALUES (CAST(:org AS uuid), CAST(:ticket AS uuid), "
                    "CAST(:message AS uuid), :correlation, :route, :category, :urgency, "
                    ":confidence, :rule_codes, :summary, :draft, :citations, :grounded, "
                    ":mcp, :ms, :model, :prompt_tokens, :completion_tokens, "
                    "CAST(:trace AS jsonb), CAST(:grounding AS jsonb)) "
                    "ON CONFLICT (organization_id, correlation_id) DO UPDATE SET "
                    "route = EXCLUDED.route, category = EXCLUDED.category, "
                    "urgency = EXCLUDED.urgency, confidence = EXCLUDED.confidence, "
                    "rule_codes = EXCLUDED.rule_codes, "
                    "decision_summary = EXCLUDED.decision_summary, draft = EXCLUDED.draft, "
                    "citations = EXCLUDED.citations, "
                    "grounding_validated = EXCLUDED.grounding_validated, "
                    "mcp_connected = EXCLUDED.mcp_connected, "
                    "processing_ms = EXCLUDED.processing_ms, model_used = EXCLUDED.model_used, "
                    "prompt_tokens = EXCLUDED.prompt_tokens, "
                    "completion_tokens = EXCLUDED.completion_tokens, trace = EXCLUDED.trace, "
                    "grounding_details = EXCLUDED.grounding_details, updated_at = now() "
                    "RETURNING id::text"
                ),
                {
                    "org": conversation.organization_id,
                    "ticket": conversation.ticket_id,
                    "message": conversation.message_id,
                    "correlation": outcome.correlation_id,
                    "route": outcome.route,
                    "category": outcome.category,
                    "urgency": outcome.urgency,
                    "confidence": outcome.confidence,
                    "rule_codes": list(outcome.rule_codes),
                    "summary": outcome.decision_summary,
                    "draft": outcome.draft,
                    "citations": list(outcome.citations),
                    "grounded": outcome.grounding_validated,
                    "mcp": outcome.mcp_connected,
                    "ms": outcome.processing_ms,
                    "model": outcome.model_used,
                    "prompt_tokens": outcome.prompt_tokens,
                    "completion_tokens": outcome.completion_tokens,
                    "trace": json.dumps(list(outcome.trace), default=str),
                    "grounding": json.dumps(outcome.grounding_details, default=str),
                },
            ).scalar_one()

            # The pipeline is the system acting, so the action has no actor.
            c.execute(
                text(
                    "INSERT INTO actions (organization_id, ticket_id, triage_run_id, "
                    "action_type, idempotency_key, reason, outcome, payload) VALUES "
                    "(CAST(:org AS uuid), CAST(:ticket AS uuid), CAST(:run AS uuid), "
                    "'TRIAGE_RUN', :key, :reason, 'SUCCEEDED', CAST(:payload AS jsonb)) "
                    "ON CONFLICT DO NOTHING"
                ),
                {
                    "org": conversation.organization_id,
                    "ticket": conversation.ticket_id,
                    "run": run_id,
                    "key": outcome.correlation_id,
                    "reason": outcome.route,
                    "payload": json.dumps(
                        {
                            "rule_codes": list(outcome.rule_codes),
                            "assignments": outcome.assignments,
                            "cost_micro_units": outcome.cost_micro,
                            "model_used": outcome.model_used,
                        },
                        sort_keys=True,
                        default=str,
                    ),
                },
            )

            self._apply_routing(c, conversation, outcome)
            self._record_targets(c, conversation, outcome)
        return str(run_id)

    def _apply_routing(self, c: Any, conversation: Conversation, outcome: TriageOutcome) -> None:
        """Apply what the rules decided, and nothing the model decided alone."""
        assignments = outcome.assignments
        updates = [
            "category = :category",
            "urgency = CAST(:urgency AS ticket_urgency)",
            "route = CAST(:route AS ticket_route)",
            "confidence = :confidence",
        ]
        params: dict[str, Any] = {
            "org": conversation.organization_id,
            "ticket": conversation.ticket_id,
            "category": outcome.category or None,
            "urgency": (outcome.urgency or "medium").lower(),
            "route": outcome.route,
            "confidence": outcome.confidence,
        }
        for column, key in (
            ("queue_id", "queue_id"),
            ("department_id", "department_id"),
            ("assigned_membership_id", "assign_to_membership_id"),
        ):
            value = assignments.get(key)
            # A rule may name only an identifier; anything else is ignored
            # rather than written into a foreign key column.
            if isinstance(value, str) and _is_identifier(value):
                updates.append(f"{column} = CAST(:{column} AS uuid)")
                params[column] = value

        c.execute(
            text(
                f"UPDATE tickets SET {', '.join(updates)}, version = version + 1, "
                "updated_at = now() WHERE id = CAST(:ticket AS uuid) "
                "AND organization_id = CAST(:org AS uuid)"
            ),
            params,
        )

    def _record_targets(self, c: Any, conversation: Conversation, outcome: TriageOutcome) -> None:
        if outcome.first_response_due_at is None:
            return
        c.execute(
            text(
                "INSERT INTO ticket_sla_states (organization_id, ticket_id, sla_policy_id, "
                "first_response_due_at, resolution_due_at, state) VALUES (CAST(:org AS uuid), "
                "CAST(:ticket AS uuid), CAST(:policy AS uuid), :first_due, :resolution_due, "
                "CAST(:state AS sla_state)) ON CONFLICT (ticket_id) DO UPDATE SET "
                "sla_policy_id = EXCLUDED.sla_policy_id, "
                "first_response_due_at = EXCLUDED.first_response_due_at, "
                "resolution_due_at = EXCLUDED.resolution_due_at, "
                "state = EXCLUDED.state, updated_at = now() "
                # A breach already recorded is never erased by a later run.
                "WHERE ticket_sla_states.state <> 'BREACHED'",
            ),
            {
                "org": conversation.organization_id,
                "ticket": conversation.ticket_id,
                # The target still applies even if the policy reference is not
                # one this database knows; losing the whole run over it would
                # be worse than recording the target without its policy.
                "policy": outcome.sla_policy_id
                if outcome.sla_policy_id and _is_identifier(outcome.sla_policy_id)
                else None,
                "first_due": outcome.first_response_due_at,
                "resolution_due": outcome.resolution_due_at,
                "state": outcome.sla_state,
            },
        )


def _is_identifier(value: str) -> bool:
    import re

    return bool(re.fullmatch(_IDENTIFIER, value, re.IGNORECASE))
