"""The pilot acceptance run (C14).

The run itself is exercised end to end in `CLIENT_C14_TEST_REPORT.md`. These
tests hold the parts that decide whether its verdict means anything: that each
invariant can actually fail, that the send check looks at what the product did
rather than at what a customer wrote, and that the report refuses to claim an
accuracy it cannot measure.
"""

from __future__ import annotations

import csv
from pathlib import Path
from typing import Any

import pytest

from scripts.acceptance_run import (
    CORPUS,
    INVARIANTS,
    RISK_CODES,
    evaluate,
    load_corpus,
    send_shaped_steps,
    step_identifiers,
)

ROOT = Path(__file__).resolve().parents[1]
KNOWN = {"KB-001", "KB-002"}


class FakeOutcome:
    """Enough of a `TriageOutcome` for the invariants to be checked."""

    def __init__(
        self,
        *,
        route: str = "ESCALATE",
        grounding_validated: bool = True,
        citations: tuple[str, ...] = ("KB-001",),
        rule_codes: tuple[str, ...] = (),
        trace: tuple[dict[str, Any], ...] | None = None,
        correlation_id: str = "triage-1",
        processing_ms: int = 10,
        cost_micro: int = 1000,
    ):
        self.route = route
        self.grounding_validated = grounding_validated
        self.citations = citations
        self.rule_codes = rule_codes
        self.trace = trace if trace is not None else ({"node": "decide", "status": "success"},)
        self.correlation_id = correlation_id
        self.processing_ms = processing_ms
        self.cost_micro = cost_micro


def report_for(outcomes: list[Any], second: list[Any] | None = None) -> dict[str, Any]:
    return evaluate(outcomes, second if second is not None else outcomes, KNOWN)


def failed(report: dict[str, Any]) -> set[str]:
    return {name for name, result in report["invariants"].items() if not result["passed"]}


# ===========================================================================
# THE SEND CHECK LOOKS AT THE PRODUCT, NOT AT THE CUSTOMER
# ===========================================================================


def test_a_customer_asking_us_to_send_something_is_not_a_send() -> None:
    """The defect this prevents actually happened.

    The first version searched the whole serialized step for "send" and failed
    on two of the forty-five conversations, because the knowledge-search step
    carries the query and a customer had written "please send me another link".
    Scanning prose for a capability finds the customer's words.
    """
    outcome = FakeOutcome(
        trace=(
            {
                "node": "kb_search_mcp",
                "status": "success",
                "message": "Knowledge base searched across the MCP stdio boundary.",
                "data": {
                    "tool_name": "search_knowledge_base",
                    "query": "please send me another password reset link, resend the invitation",
                },
            },
        )
    )

    assert send_shaped_steps(outcome) == set()
    assert failed(report_for([outcome])) == set()


def test_a_real_send_step_is_caught() -> None:
    """The positive control. Without it the check passes by looking nowhere."""
    for step in (
        {"node": "send_reply", "status": "success"},
        {"node": "deliver", "status": "success"},
        {"node": "x", "data": {"tool_name": "sendMessage"}},
        {"node": "x", "data": {"endpoint": "messages/send"}},
        {"node": "x", "data": {"operation": "dispatch_email"}},
        {"node": "x", "data": {"action": "transmit"}},
        {"node": "resend_link", "status": "success"},
        {"node": "x", "data": {"operation": "forward_to_customer"}},
    ):
        outcome = FakeOutcome(trace=(step,))
        assert send_shaped_steps(outcome), step
        assert "no_send_path_exercised" in failed(report_for([outcome])), step


def test_a_draft_reply_is_not_mistaken_for_a_send() -> None:
    """Drafting is the product working. Only outbound verbs are flagged."""
    for step in (
        {"node": "draft_reply", "status": "success"},
        {"node": "x", "data": {"tool_name": "search_knowledge_base"}},
        {"node": "x", "data": {"operation": "sender_address_check"}},
    ):
        assert send_shaped_steps(FakeOutcome(trace=(step,))) == set(), step


def test_only_identifiers_are_scanned() -> None:
    """A step's free text is deliberately out of scope."""
    outcome = FakeOutcome(
        trace=({"node": "decide", "message": "the customer asked us to send a refund"},)
    )

    assert step_identifiers(outcome) == {"decide"}
    assert send_shaped_steps(outcome) == set()


def test_a_malformed_trace_step_does_not_crash_the_run() -> None:
    outcome = FakeOutcome(trace=("not a dict", {"node": "decide"}, {}))  # type: ignore[arg-type]

    assert step_identifiers(outcome) == {"decide"}


# ===========================================================================
# EVERY INVARIANT CAN FAIL
# ===========================================================================


def test_an_ungrounded_auto_resolve_fails() -> None:
    report = report_for(
        [FakeOutcome(route="AUTO_RESOLVE", grounding_validated=False, citations=("KB-001",))]
    )

    assert "auto_resolve_always_grounded" in failed(report)


def test_an_uncited_auto_resolve_fails() -> None:
    report = report_for([FakeOutcome(route="AUTO_RESOLVE", citations=())])

    assert "auto_resolve_always_grounded" in failed(report)


@pytest.mark.parametrize("code", sorted(RISK_CODES))
def test_a_risky_auto_resolve_fails(code: str) -> None:
    report = report_for([FakeOutcome(route="AUTO_RESOLVE", rule_codes=(code,))])

    assert "auto_resolve_never_risky" in failed(report)


def test_a_risky_conversation_that_escalates_is_fine() -> None:
    report = report_for([FakeOutcome(route="ESCALATE", rule_codes=("THREAT_DETECTED",))])

    assert failed(report) == set()


def test_a_citation_outside_the_corpus_fails() -> None:
    """An answer citing an article that was never searched is a fabrication."""
    report = report_for([FakeOutcome(citations=("KB-999",))])

    assert "citations_exist" in failed(report)


def test_a_run_with_no_route_or_no_trace_fails() -> None:
    assert "every_run_recorded" in failed(report_for([FakeOutcome(route="")]))
    assert "every_run_recorded" in failed(report_for([FakeOutcome(trace=())]))


def test_a_route_that_changes_between_passes_fails() -> None:
    """A pilot nobody can reproduce is not evidence."""
    report = report_for(
        [FakeOutcome(route="AUTO_RESOLVE", rule_codes=())], [FakeOutcome(route="ESCALATE")]
    )

    assert "routes_are_reproducible" in failed(report)


def test_a_clean_run_fails_nothing() -> None:
    report = report_for([FakeOutcome() for _ in range(5)])

    assert failed(report) == set()
    assert set(report["invariants"]) == set(INVARIANTS)


def test_mismatched_passes_are_refused_rather_than_silently_truncated() -> None:
    with pytest.raises(ValueError):
        evaluate([FakeOutcome(), FakeOutcome()], [FakeOutcome()], KNOWN)


# ===========================================================================
# THE NUMBERS AN OPERATIONS MANAGER READS
# ===========================================================================


def test_the_route_mix_and_human_share_are_reported() -> None:
    report = report_for(
        [FakeOutcome(route="AUTO_RESOLVE", rule_codes=())] * 6
        + [FakeOutcome(route="ESCALATE")] * 3
        + [FakeOutcome(route="CLARIFY")]
    )

    assert report["routes"] == {"AUTO_RESOLVE": 6, "ESCALATE": 3, "CLARIFY": 1}
    assert report["human_share"] == 0.4
    assert report["cases"] == 10


def test_the_guardrail_share_counts_conversations_not_codes() -> None:
    report = report_for(
        [FakeOutcome(rule_codes=("THREAT_DETECTED", "ANGRY_CUSTOMER")), FakeOutcome()]
    )

    assert report["guardrail_share"] == 0.5


def test_latency_percentiles_are_computed_over_sorted_values() -> None:
    """Deliberately out of order.

    The first version of this test used an already-sorted fixture, so removing
    the sort changed nothing and a mutation run walked straight past it. A
    percentile taken over unsorted values is not a percentile.
    """
    report = report_for([FakeOutcome(processing_ms=ms) for ms in (1000, 5, 400, 10, 20, 2000, 40)])

    latency = report["latency_ms"]
    assert latency["p50"] <= latency["p95"] <= latency["max"]
    assert latency["max"] == 2000
    assert latency["p50"] == 40, latency
    assert latency["mean"] == round(sum((1000, 5, 400, 10, 20, 2000, 40)) / 7, 1)


def test_an_empty_run_does_not_divide_by_zero() -> None:
    report = report_for([])

    assert report["cases"] == 0
    assert report["human_share"] == 0.0
    assert report["latency_ms"]["p95"] == 0


# ===========================================================================
# THE REPORT DOES NOT CLAIM WHAT IT CANNOT MEASURE
# ===========================================================================


def test_the_pilot_corpus_carries_no_labels() -> None:
    """If labels are ever added, the accuracy claim has to be revisited
    deliberately rather than inherited from a fixture edit."""
    with CORPUS.open(newline="", encoding="utf-8") as handle:
        columns = set(next(csv.reader(handle)))

    assert columns == {"ticket_id", "customer_id", "subject", "body"}
    for label in ("category", "urgency", "expected_article", "expected_route"):
        assert label not in columns


def test_the_corpus_is_large_enough_to_say_anything() -> None:
    rows = load_corpus()

    assert len(rows) >= 40, "a pilot over a handful of conversations proves little"
    assert len({row["ticket_id"] for row in rows}) == len(rows), "duplicate ticket ids"
    assert all(row["subject"].strip() and row["body"].strip() for row in rows)


def test_the_report_states_that_it_measures_no_accuracy(tmp_path: Path) -> None:
    """The honesty of the whole report rests on this field and the note beside
    it, and nothing was asserting either."""
    import json
    import subprocess
    import sys

    destination = tmp_path / "report.json"
    completed = subprocess.run(
        [sys.executable, "-m", "scripts.acceptance_run", "--json", str(destination)],
        cwd=ROOT,
        capture_output=True,
        text=True,
        env={"RESOLVEFLOW_TEST_MODE": "1", "PATH": "/usr/bin:/bin", "HOME": str(tmp_path)},
    )
    assert completed.returncode == 0, completed.stderr[-2000:]

    report = json.loads(destination.read_text(encoding="utf-8"))

    assert report["measures_accuracy"] is False
    note = report["accuracy_note"].casefold()
    assert "no accuracy is claimed" in note
    assert "labelled" in note or "labels" in note
    assert report["provider"].casefold().startswith("offline")
    assert "no paid model" in report["provider"].casefold()


def test_every_invariant_states_what_it_means() -> None:
    for name, description in INVARIANTS.items():
        assert len(description) > 30, f"{name} is not explained"


def test_the_runner_never_calls_a_paid_provider() -> None:
    source = (ROOT / "scripts" / "acceptance_run.py").read_text(encoding="utf-8")

    # It builds its provider through the offline parts the Quality Check uses,
    # which refuse to construct without RESOLVEFLOW_TEST_MODE.
    assert "offline_parts" in source
    assert "OpenAI" not in source
    assert "api_key" not in source
