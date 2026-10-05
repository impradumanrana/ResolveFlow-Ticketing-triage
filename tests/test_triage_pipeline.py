"""C11: the production pipeline.

The chain under test is the real one: the MVP workflow, the C10 gateway, the
C05 corpus across the MCP boundary, and the C09 rules. What is asserted is what
the client is promised - an answer only when it is grounded and safe, a person
whenever anything at all goes wrong, and a record of why either way.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from pathlib import Path

import pytest
from gateway_fakes import ORG, config, fail, harness

from app.gateway.errors import FailureCode
from app.rules.engine import Conversation as RuleConversation
from app.rules.engine import Evidence
from app.rules.engine import apply as apply_rules
from app.rules.routing import parse_rule
from app.rules.sla import SlaPolicy
from app.rules.store import RuleSet
from app.triage.mcp_client import KnowledgeMcpClient, McpUnavailable
from app.triage.memory_store import InMemoryTriageStore
from app.triage.offline_provider import DeterministicAdapter
from app.triage.pipeline import TriagePipeline
from app.triage.quality import Dataset
from app.triage.records import Conversation, narrower

FIXTURE = Path("app/fixtures/quality/knowledge_fixture.json").resolve()
ENV = {"RESOLVEFLOW_MCP_FIXTURE": str(FIXTURE), "RESOLVEFLOW_TEST_MODE": "1"}
NOW = datetime(2026, 9, 17, 10, 0, tzinfo=UTC)
PASSWORD_HELP = (
    "Reset my password",
    "I forgot my password and need a reset link so I can sign in again.",
)


@pytest.fixture(scope="module")
def knowledge():
    with KnowledgeMcpClient(env=ENV) as client:
        yield client


def build(scripts=None, rule_set=None, adapter=None, configs=None):
    """A pipeline whose only simulated part is the provider."""
    state = harness(configs=configs, scripts=scripts)
    if adapter is not None:
        state.gateway.adapters["sim"] = adapter
        state.adapter = adapter
    store = InMemoryTriageStore()
    return state, store, rule_set


def run(knowledge, state, store, conversation, rule_set=None):
    pipeline = TriagePipeline(state.gateway, knowledge, store, rule_set=rule_set, now=lambda: NOW)
    return pipeline.triage(conversation)


def conversation(subject=PASSWORD_HELP[0], body=PASSWORD_HELP[1], **overrides):
    return Conversation(
        organization_id=ORG,
        ticket_id=str(uuid.uuid4()),
        subject=subject,
        body=body,
        from_address="customer@example.net",
        received_at=NOW,
        **overrides,
    )


class FailingAdapter(DeterministicAdapter):
    """The offline provider, but one model refuses."""

    def __init__(self, failure, model=None):
        super().__init__()
        self.failure = failure
        self.model = model

    def complete(self, credential, *, model, region, request, timeout):
        if self.model in (None, model):
            raise self.failure
        return super().complete(
            credential, model=model, region=region, request=request, timeout=timeout
        )


def offline(rule_set=None, configs=None, adapter=None):
    return build(rule_set=rule_set, adapter=adapter or DeterministicAdapter(), configs=configs)


# --------------------------------------------------------------------------
# The whole chain
# --------------------------------------------------------------------------


def test_a_safe_answerable_conversation_is_answered_from_evidence(knowledge):
    state, store, _ = offline()
    outcome = run(knowledge, state, store, conversation())

    assert outcome.route == "AUTO_RESOLVE"
    assert outcome.category == "technical" and outcome.urgency == "low"
    assert outcome.citations == ("KB-001",)
    assert outcome.grounding_validated is True
    assert "Forgot password" in (outcome.draft or "")
    assert outcome.rule_codes == ()
    assert outcome.mcp_connected is True
    assert [event["node"] for event in outcome.trace] == [
        "perceive",
        "classify",
        "risk_guard",
        "kb_search_mcp",
        "decide",
        "draft_resolution",
        "validate_grounding",
        "observe",
    ]
    assert outcome.prompt_tokens > 0 and outcome.completion_tokens > 0
    assert outcome.cost_micro > 0
    assert store.runs and store.last.correlation_id == outcome.correlation_id


def test_every_boundary_the_demonstration_shows_is_in_the_trace(knowledge):
    state, store, _ = offline()
    outcome = run(knowledge, state, store, conversation())
    events = {event["node"]: event for event in outcome.trace}

    assert events["kb_search_mcp"]["data"]["transport"] == "stdio"
    assert events["kb_search_mcp"]["data"]["tool_name"] == "search_knowledge_base"
    assert events["kb_search_mcp"]["data"]["matches"][0]["article_id"] == "KB-001"
    assert events["decide"]["data"]["kb_score"] > events["decide"]["data"]["threshold"]
    assert events["validate_grounding"]["data"]["valid"] is True
    assert events["validate_grounding"]["data"]["verified_claims"] == 1


def test_the_run_and_its_provider_calls_share_one_correlation_id(knowledge):
    state, store, _ = offline()
    outcome = run(knowledge, state, store, conversation())

    assert outcome.correlation_id.startswith("triage-")
    assert {call.correlation_id for call in state.store.calls} == {outcome.correlation_id}
    # The workflow mints its own id; the stored trace carries the run's.
    assert outcome.trace[0]["data"]["correlation_id"] == outcome.correlation_id


def test_the_cost_of_a_run_is_the_cost_of_its_provider_calls(knowledge):
    state, store, _ = offline()
    outcome = run(knowledge, state, store, conversation())
    billed = sum(call.cost_micro for call in state.store.calls if call.outcome == "SUCCEEDED")
    assert outcome.cost_micro == billed


# --------------------------------------------------------------------------
# Every failure ends with a person and a record
# --------------------------------------------------------------------------


def test_a_provider_failure_routes_to_a_person_and_is_still_recorded(knowledge):
    state, store, _ = offline(
        adapter=FailingAdapter(fail(FailureCode.QUOTA_EXHAUSTED, status=429), model="sim-small")
    )
    outcome = run(knowledge, state, store, conversation())

    assert outcome.route == "ESCALATE"
    assert "MODEL_ERROR" in outcome.rule_codes
    assert "QUOTA_EXHAUSTED" in outcome.rule_codes
    assert outcome.grounding_validated is False and outcome.citations == ()
    assert store.last.route == "ESCALATE"


def test_a_knowledge_outage_routes_to_a_person(knowledge):
    class Broken:
        last_request = None

        def search(self, *args, **kwargs):
            raise McpUnavailable("stdio closed")

    state, store, _ = offline()
    outcome = run(knowledge=Broken(), state=state, store=store, conversation=conversation())

    assert outcome.route == "ESCALATE"
    assert "MCP_UNAVAILABLE" in outcome.rule_codes
    assert outcome.mcp_connected is False
    assert outcome.draft is None or "Human review required" in outcome.draft


def test_a_question_the_corpus_cannot_answer_routes_to_a_person(knowledge):
    state, store, _ = offline()
    outcome = run(
        knowledge,
        state,
        store,
        conversation(
            "Bulk export", "Can I export every record from last quarter as a spreadsheet?"
        ),
    )
    assert outcome.route == "ESCALATE"
    assert "LOW_KB_CONFIDENCE" in outcome.rule_codes
    assert outcome.citations == ()


def test_a_risky_conversation_is_never_answered_automatically(knowledge):
    state, store, _ = offline()
    outcome = run(
        knowledge,
        state,
        store,
        conversation(
            "Refund or I take this further",
            "I want a refund and I will take legal action if I do not get one.",
        ),
    )
    assert outcome.route == "ESCALATE"
    assert {"THREAT_DETECTED", "REFUND_OR_LEGAL"} <= set(outcome.rule_codes)
    assert outcome.grounding_validated is False


def test_a_conversation_missing_a_detail_asks_the_customer(knowledge):
    state, store, _ = offline()
    outcome = run(
        knowledge, state, store, conversation("Billing question", "I have a billing question.")
    )
    assert outcome.route == "CLARIFY"
    assert "MISSING_INFORMATION" in outcome.rule_codes


def test_an_exhausted_budget_routes_to_a_person_rather_than_raising(knowledge):
    state, store, _ = offline(configs=[config(monthly_budget_minor_units=0)])
    outcome = run(knowledge, state, store, conversation())
    assert outcome.route == "ESCALATE"
    assert "BUDGET_EXCEEDED" in outcome.rule_codes


# --------------------------------------------------------------------------
# Deterministic rules still win
# --------------------------------------------------------------------------


def test_a_client_rule_can_take_an_answerable_conversation_to_a_person(knowledge):
    rules = RuleSet(
        rules=[parse_rule({"name": "review everything", "actions": {"escalate": True}})]
    )
    state, store, _ = offline()
    outcome = run(knowledge, state, store, conversation(), rule_set=rules)

    assert outcome.route == "ESCALATE"
    assert "RULE_FORCED_ESCALATION" in outcome.rule_codes
    # The draft was written before the rules ran; it is withheld, not stored.
    assert outcome.citations == () and outcome.grounding_validated is False
    assert "withheld" in (outcome.draft or "")


def test_a_client_rule_cannot_make_an_unsafe_conversation_answerable(knowledge):
    rules = RuleSet(
        rules=[parse_rule({"name": "vip lane", "actions": {"queue_id": "q-vip", "urgency": "low"}})]
    )
    state, store, _ = offline()
    outcome = run(
        knowledge,
        state,
        store,
        conversation(
            "Someone accessed my account", "Someone logged into my account without my permission."
        ),
        rule_set=rules,
    )
    assert outcome.route == "ESCALATE"
    assert "SECURITY_RISK" in outcome.rule_codes


def test_routing_and_service_levels_come_from_the_rules(knowledge):
    department = "11111111-1111-1111-1111-111111111111"
    rules = RuleSet(
        rules=[
            parse_rule(
                {
                    "name": "password work",
                    "priority": 10,
                    "conditions": {
                        "all": [{"field": "subject", "operator": "contains", "value": "password"}]
                    },
                    "actions": {"department_id": department, "urgency": "high"},
                }
            )
        ],
        policies=[SlaPolicy("p-1", "Default", 240, time_zone="Europe/London")],
    )
    state, store, _ = offline()
    outcome = run(knowledge, state, store, conversation(), rule_set=rules)

    assert outcome.assignments == {"department_id": department, "urgency": "high"}
    assert outcome.urgency == "high", "the ticket takes the urgency the rules set"
    assert outcome.sla_policy_id == "p-1"
    assert outcome.first_response_due_at is not None
    assert outcome.sla_state in {"ON_TRACK", "AT_RISK"}
    # Raising urgency after the guardrails ran must not rewrite what they saw.
    assert "HIGH_URGENCY" not in outcome.rule_codes


@pytest.mark.parametrize("case", Dataset.load().cases, ids=lambda case: case.case_id)
def test_the_rules_engine_and_the_workflow_agree_when_there_are_no_client_rules(knowledge, case):
    """Two independent decisions from the same evidence, on every labelled case.

    The pipeline keeps the more cautious of the two. If they ever disagreed
    without a client rule to explain it, one of them would be wrong.
    """
    state, store, _ = offline()
    outcome = run(knowledge, state, store, conversation(case.subject, case.body))
    workflow_route = next(
        event["data"]["decision"] for event in outcome.trace if event["node"] == "decide"
    )
    # Grounding can narrow the workflow's own route after `decide`.
    final_workflow_route = narrower(
        workflow_route,
        "ESCALATE" if "GROUNDING_VALIDATION_FAILED" in outcome.rule_codes else workflow_route,
    )

    rule_outcome = apply_rules(
        RuleConversation(
            subject=case.subject,
            body=case.body,
            from_address="customer@example.net",
            category=outcome.category,
            urgency=outcome.urgency,
            received_at=NOW,
        ),
        rules=[],
        policies=[],
        evidence=Evidence(
            classification_confidence=outcome.confidence,
            kb_top_score=next(
                event["data"]["kb_score"] for event in outcome.trace if event["node"] == "decide"
            ),
        ),
        now=NOW,
    )
    assert rule_outcome.decision == final_workflow_route
    assert outcome.route == final_workflow_route


def test_the_route_is_only_ever_narrowed():
    assert narrower("AUTO_RESOLVE", "ESCALATE") == "ESCALATE"
    assert narrower("ESCALATE", "AUTO_RESOLVE") == "ESCALATE"
    assert narrower("CLARIFY", "AUTO_RESOLVE") == "CLARIFY"
    assert narrower("CLARIFY", "ESCALATE") == "ESCALATE"
    assert narrower("AUTO_RESOLVE", "AUTO_RESOLVE") == "AUTO_RESOLVE"


def test_rerunning_a_conversation_replaces_its_own_record_only(knowledge):
    state, store, _ = offline()
    first = run(knowledge, state, store, conversation())
    second = run(knowledge, state, store, conversation())
    assert first.correlation_id != second.correlation_id
    assert len(store.runs) == 2

    store.record_run(store.runs[0][0], first)
    assert len(store.runs) == 2, "the same correlation id updates its run"
