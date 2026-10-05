"""C11: Quality Check.

The metrics are pure functions, so they are tested directly, and then the whole
check is run over the labelled dataset through the real pipeline. Each gate is
also deliberately broken, because a gate that cannot fail measures nothing.
"""

from __future__ import annotations

import json
import uuid
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

import pytest
from gateway_fakes import ORG, harness

from app.triage.mcp_client import KnowledgeMcpClient
from app.triage.memory_store import InMemoryTriageStore
from app.triage.offline_provider import DeterministicAdapter
from app.triage.pipeline import TriagePipeline
from app.triage.quality import (
    DEFAULT_DATASET,
    DEFAULT_THRESHOLDS,
    CaseRun,
    Dataset,
    QualityCase,
    Threshold,
    ThresholdSet,
    classification_metrics,
    conversation_for,
    cost_metrics,
    expected_guardrail_codes,
    groundedness_metrics,
    latency_metrics,
    percentile,
    retrieval_metrics,
    run_quality_check,
    safety_metrics,
    score,
)
from app.triage.quality_store import InMemoryQualityStore
from app.triage.records import TriageOutcome

FIXTURE = Path("app/fixtures/quality/knowledge_fixture.json").resolve()
ENV = {"RESOLVEFLOW_MCP_FIXTURE": str(FIXTURE), "RESOLVEFLOW_TEST_MODE": "1"}
NOW = datetime(2026, 9, 17, 12, 0, tzinfo=UTC)


def case(**overrides) -> QualityCase:
    base = {
        "case_id": "c-1",
        "subject": "Reset my password",
        "body": "I forgot it.",
        "category": "technical",
        "urgency": "low",
        "expected_article": "KB-001",
    }
    return QualityCase(**{**base, **overrides})


def outcome(**overrides) -> TriageOutcome:
    base = {
        "correlation_id": "triage-1",
        "route": "AUTO_RESOLVE",
        "category": "technical",
        "urgency": "low",
        "confidence": 0.9,
        "rule_codes": (),
        "decision_summary": "",
        "draft": "answer [KB-001]",
        "citations": ("KB-001",),
        "grounding_validated": True,
        "grounding_details": {},
        "mcp_connected": True,
        "processing_ms": 100,
        "model_used": "m",
        "prompt_tokens": 10,
        "completion_tokens": 5,
        "cost_micro": 1000,
        "trace": (),
    }
    return TriageOutcome(**{**base, **overrides})


def run_of(case_, outcome_, retrieved=("KB-001", "KB-002")) -> CaseRun:
    return CaseRun(case=case_, outcome=outcome_, retrieved=retrieved)


# --------------------------------------------------------------------------
# Metrics
# --------------------------------------------------------------------------


def test_classification_accuracy_counts_category_and_urgency_separately():
    runs = [
        run_of(case(), outcome()),
        run_of(case(case_id="c-2", category="billing"), outcome(category="technical")),
        run_of(case(case_id="c-3", urgency="high"), outcome(urgency="low")),
    ]
    metrics = classification_metrics(runs)
    assert metrics["category_accuracy"] == pytest.approx(2 / 3)
    assert metrics["urgency_accuracy"] == pytest.approx(2 / 3)


def test_retrieval_is_scored_with_the_harness_the_corpus_was_accepted_with():
    runs = [
        run_of(case(), outcome(), retrieved=("KB-001",)),
        run_of(case(case_id="c-2"), outcome(), retrieved=("KB-002", "KB-001")),
        run_of(case(case_id="c-3"), outcome(), retrieved=("KB-002", "KB-007", "KB-012")),
    ]
    metrics = retrieval_metrics(runs)
    assert metrics["recall_at_1"] == pytest.approx(1 / 3)
    assert metrics["recall_at_3"] == pytest.approx(2 / 3)
    assert metrics["mrr"] == pytest.approx((1 + 0.5 + 0) / 3)


def test_cases_without_an_expected_article_are_not_counted_as_retrieval_misses():
    runs = [run_of(case(expected_article=None), outcome(), retrieved=())]
    assert retrieval_metrics(runs)["recall_at_1"] is None


def test_groundedness_measures_only_what_was_answered_automatically():
    runs = [
        run_of(case(), outcome()),
        run_of(
            case(case_id="c-2"),
            outcome(
                route="ESCALATE",
                draft="Human review required",
                grounding_validated=False,
                citations=(),
            ),
        ),
    ]
    metrics = groundedness_metrics(runs)
    assert metrics["validated_share"] == 1.0 and metrics["citation_validity"] == 1.0

    ungrounded = [run_of(case(), outcome(grounding_validated=False))]
    assert groundedness_metrics(ungrounded)["validated_share"] == 0.0


def test_a_citation_that_was_never_retrieved_fails_citation_validity():
    runs = [run_of(case(), outcome(citations=("KB-999",)), retrieved=("KB-001",))]
    assert groundedness_metrics(runs)["citation_validity"] == 0.0


def test_safety_counts_risky_conversations_and_expected_guardrails():
    runs = [
        run_of(
            case(case_id="risky", risky=True, expected_rule_codes=("THREAT_DETECTED",)),
            outcome(route="ESCALATE", rule_codes=("THREAT_DETECTED",)),
        ),
        run_of(
            case(case_id="leaked", risky=True, expected_rule_codes=("SECURITY_RISK",)),
            outcome(route="AUTO_RESOLVE", rule_codes=()),
        ),
    ]
    metrics = safety_metrics(runs)
    assert metrics["risky_never_auto_resolved"] == 0.5
    assert metrics["guardrail_recall"] == 0.5


def test_percentile_takes_a_real_observation_rather_than_inventing_one():
    assert percentile([10, 20, 30, 40], 0.95) == 40
    assert percentile([5], 0.95) == 5
    assert percentile([], 0.95) is None
    assert percentile(list(range(1, 101)), 0.95) == 95


def test_latency_and_cost_are_measured_per_run():
    runs = [
        run_of(case(), outcome(processing_ms=100, cost_micro=1000)),
        run_of(case(case_id="c-2"), outcome(processing_ms=900, cost_micro=3000)),
    ]
    assert latency_metrics(runs)["p95_ms"] == 900
    assert cost_metrics(runs)["mean_micro_units_per_run"] == 2000


# --------------------------------------------------------------------------
# Thresholds
# --------------------------------------------------------------------------


def test_direction_decides_whether_higher_or_lower_is_better():
    assert Threshold("g", "m", 0.8, "min").passes(0.8)
    assert not Threshold("g", "m", 0.8, "min").passes(0.79)
    assert Threshold("g", "m", 100, "max").passes(100)
    assert not Threshold("g", "m", 100, "max").passes(101)


def test_a_metric_that_could_not_be_computed_fails_rather_than_passing():
    assert not Threshold("g", "m", 0.8, "min").passes(None)
    assert not Threshold("g", "m", 0.8, "min").passes(float("nan"))
    assert not Threshold("g", "m", 100, "max").passes(None)


def test_an_unknown_gate_or_metric_fails_loudly():
    thresholds = ThresholdSet("test", (Threshold("invented", "metric", 1.0, "min"),))
    results = score([run_of(case(), outcome())], thresholds)
    assert results[0].passed is False and results[0].value is None


def test_the_shipped_thresholds_and_dataset_are_versioned_and_consistent():
    thresholds = ThresholdSet.load()
    dataset = Dataset.load()
    assert thresholds.version and dataset.version
    assert {t.gate for t in thresholds.thresholds} == {
        "classification",
        "retrieval",
        "groundedness",
        "safety",
        "latency",
        "cost",
    }
    assert all(t.direction in {"min", "max"} for t in thresholds.thresholds)
    # Every labelled guardrail expectation is one the guardrails actually produce.
    for labelled in dataset.cases:
        assert set(labelled.expected_rule_codes) <= set(expected_guardrail_codes(labelled)), (
            labelled.case_id
        )
    assert len({c.case_id for c in dataset.cases}) == len(dataset.cases)
    assert sum(1 for c in dataset.cases if c.risky) >= 3
    assert sum(1 for c in dataset.cases if c.expected_article) >= 5
    assert json.loads(DEFAULT_THRESHOLDS.read_text())["version"] == thresholds.version
    assert json.loads(DEFAULT_DATASET.read_text())["version"] == dataset.version


# --------------------------------------------------------------------------
# The whole check, over the real pipeline
# --------------------------------------------------------------------------


@pytest.fixture(scope="module")
def measured():
    """One offline Quality Check over the labelled dataset."""
    with KnowledgeMcpClient(env=ENV) as knowledge:
        state = harness()
        state.gateway.adapters["sim"] = DeterministicAdapter()
        pipeline = TriagePipeline(state.gateway, knowledge, InMemoryTriageStore(), now=lambda: NOW)
        store = InMemoryQualityStore()
        report = run_quality_check(
            triage=lambda c: pipeline.triage(
                conversation_for(c, organization_id=ORG, ticket_id=str(uuid.uuid4()))
            ),
            retrieve=lambda c: [
                r["article_id"] for r in knowledge.search(c.query, c.category, top_k=3)
            ],
            knowledge_fingerprint="fixture-corpus",
            article_count=len(json.loads(FIXTURE.read_text())),
            store=store,
            now=lambda: NOW,
        )
        yield report, store


def test_the_pipeline_passes_every_gate_on_the_labelled_dataset(measured):
    report, _ = measured
    assert report.passed, report.table()
    assert len(report.results) == len(ThresholdSet.load().thresholds)
    assert all(result.value is not None for result in report.results)


def test_no_gate_is_vacuous(measured):
    """Each gate must have had something to measure."""
    report, _ = measured
    runs = report.case_runs
    assert len(runs) == len(Dataset.load().cases)
    assert sum(1 for r in runs if r.outcome.route == "AUTO_RESOLVE") >= 3, (
        "groundedness measured nothing"
    )
    assert sum(1 for r in runs if r.case.risky) >= 3, "safety measured nothing"
    assert sum(1 for r in runs if r.case.expected_article) >= 5, "retrieval measured nothing"
    assert all(r.outcome.cost_micro > 0 for r in runs), "cost measured nothing"
    assert all(r.outcome.processing_ms >= 0 for r in runs)


def test_the_run_is_recorded_with_its_corpus_and_verdict(measured):
    report, store = measured
    assert store.runs[0]["knowledge_fingerprint"] == "fixture-corpus"
    assert store.runs[0]["dataset_version"] == report.dataset_version
    assert store.runs[0]["status"] == "COMPLETED"
    assert store.runs[0]["passed"] is True
    assert store.results["run-1"] is report


def test_the_table_names_every_gate_and_the_verdict(measured):
    report, _ = measured
    table = report.table()
    for gate in ("classification", "retrieval", "groundedness", "safety", "latency", "cost"):
        assert gate in table
    assert "Verdict: PASS" in table


@pytest.mark.parametrize(
    ("break_it", "expected_failures"),
    [
        (
            lambda runs: [replace(r, retrieved=()) for r in runs],
            {("retrieval", "recall_at_1"), ("retrieval", "recall_at_3"), ("retrieval", "mrr")},
        ),
        (
            lambda runs: [
                replace(
                    r,
                    outcome=replace(
                        r.outcome,
                        route="AUTO_RESOLVE",
                        draft="x",
                        citations=("KB-001",),
                        grounding_validated=True,
                    ),
                )
                if r.case.risky
                else r
                for r in runs
            ],
            {("safety", "risky_never_auto_resolved")},
        ),
        (
            lambda runs: [
                replace(r, outcome=replace(r.outcome, grounding_validated=False))
                if r.outcome.route == "AUTO_RESOLVE"
                else r
                for r in runs
            ],
            {("groundedness", "validated_share")},
        ),
        (
            lambda runs: [
                replace(r, outcome=replace(r.outcome, processing_ms=99_000)) for r in runs
            ],
            {("latency", "p95_ms")},
        ),
        (
            lambda runs: [
                replace(r, outcome=replace(r.outcome, cost_micro=9_000_000)) for r in runs
            ],
            {("cost", "mean_micro_units_per_run")},
        ),
        (
            lambda runs: [replace(r, outcome=replace(r.outcome, category="wrong")) for r in runs],
            {("classification", "category_accuracy")},
        ),
    ],
    ids=["retrieval", "safety", "groundedness", "latency", "cost", "classification"],
)
def test_every_gate_can_fail(measured, break_it, expected_failures):
    report, _ = measured
    results = score(break_it(list(report.case_runs)), ThresholdSet.load())
    failed = {(r.gate, r.metric) for r in results if not r.passed}
    assert expected_failures <= failed, failed


def test_a_failure_says_which_cases_it_is_about(measured):
    report, _ = measured
    broken = [replace(r, retrieved=("KB-999",)) for r in report.case_runs]
    results = score(broken, ThresholdSet.load())
    retrieval = next(r for r in results if r.metric == "recall_at_1")
    assert not retrieval.passed
    assert retrieval.detail["missed"], "a failed gate must name the cases"
    assert retrieval.detail["missed"][0]["expected"] is not None
