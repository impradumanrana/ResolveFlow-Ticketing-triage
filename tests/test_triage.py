from app.guardrails import evaluate_guardrails
from app.models import Ticket, TriageResult


def test_ticket_and_result_models_are_importable():
    ticket = Ticket(
        ticket_id="T-001",
        customer_id="C-001",
        subject="Password reset",
        body="I need to reset my password.",
    )
    assert ticket.ticket_id == "T-001"

    result = TriageResult(
        ticket_id=ticket.ticket_id,
        route="AUTO_RESOLVE",
        category="technical",
        urgency="low",
        confidence=0.9,
        decision_summary="Routine reset FAQ matched.",
        rule_codes=[],
        trace=[],
    )
    assert result.route == "AUTO_RESOLVE"


def test_guardrails_block_auto_resolve_on_risky_ticket():
    facts = {"urgency": "high", "content": "I was charged twice and now I am furious."}
    blocked = evaluate_guardrails(facts)
    assert "HIGH_URGENCY" in blocked["rule_codes"] or "ANGRY_CUSTOMER" in blocked["rule_codes"]
