"""Quality Check: what the pipeline actually does, measured against agreed thresholds.

Six gates, because a support pipeline can fail in six independent ways and an
average hides all of them:

* **classification** - is the conversation understood?
* **retrieval** - is the right article found, in the client's own corpus?
* **groundedness** - is every automatic answer supported by what was retrieved?
* **safety** - is a risky conversation never answered automatically?
* **latency** - does an answer arrive while it still matters?
* **cost** - is a run affordable at the client's own prices?

Two rules keep the numbers honest. A metric that could not be computed fails
its gate rather than being skipped, because a silent skip reads as a pass. And
every run records the corpus fingerprint it measured: change the knowledge and
yesterday's numbers describe a system that no longer exists (C04).
"""

from __future__ import annotations

import json
import math
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol

from app.guardrails import evaluate_guardrails
from app.knowledge import evaluation as knowledge_evaluation
from app.triage.records import Conversation, TriageOutcome

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "quality"
DEFAULT_THRESHOLDS = FIXTURES / "thresholds_v1.json"
DEFAULT_DATASET = FIXTURES / "dataset_v1.json"


@dataclass(frozen=True)
class Threshold:
    gate: str
    metric: str
    threshold: float
    direction: str  # "min" - higher is better; "max" - lower is better

    def passes(self, value: float | None) -> bool:
        if value is None or math.isnan(value):
            return False
        return value >= self.threshold if self.direction == "min" else value <= self.threshold


@dataclass(frozen=True)
class ThresholdSet:
    version: str
    thresholds: tuple[Threshold, ...]

    @classmethod
    def load(cls, path: Path = DEFAULT_THRESHOLDS) -> ThresholdSet:
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
        return cls(
            version=str(payload["version"]),
            thresholds=tuple(
                Threshold(
                    gate=str(entry["gate"]),
                    metric=str(entry["metric"]),
                    threshold=float(entry["threshold"]),
                    direction=str(entry.get("direction", "min")),
                )
                for entry in payload["gates"]
            ),
        )


@dataclass(frozen=True)
class QualityCase:
    """One labelled conversation."""

    case_id: str
    subject: str
    body: str
    category: str
    urgency: str
    expected_article: str | None = None
    risky: bool = False
    expected_rule_codes: tuple[str, ...] = ()

    @property
    def query(self) -> str:
        return f"{self.subject}\n{self.body}".strip()


@dataclass(frozen=True)
class Dataset:
    version: str
    cases: tuple[QualityCase, ...]

    @classmethod
    def load(cls, path: Path = DEFAULT_DATASET) -> Dataset:
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
        return cls(
            version=str(payload["version"]),
            cases=tuple(
                QualityCase(
                    case_id=str(case["case_id"]),
                    subject=case["subject"],
                    body=case["body"],
                    category=case["category"],
                    urgency=case["urgency"],
                    expected_article=case.get("expected_article"),
                    risky=bool(case.get("risky", False)),
                    expected_rule_codes=tuple(case.get("expected_rule_codes", ())),
                )
                for case in payload["cases"]
            ),
        )


@dataclass(frozen=True)
class CaseRun:
    case: QualityCase
    outcome: TriageOutcome
    retrieved: tuple[str, ...]


@dataclass(frozen=True)
class GateResult:
    gate: str
    metric: str
    value: float | None
    threshold: float
    direction: str
    passed: bool
    detail: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class QualityReport:
    dataset_version: str
    threshold_version: str
    knowledge_fingerprint: str
    article_count: int
    results: tuple[GateResult, ...]
    case_runs: tuple[CaseRun, ...]
    started_at: datetime
    completed_at: datetime

    @property
    def passed(self) -> bool:
        return bool(self.results) and all(result.passed for result in self.results)

    @property
    def failures(self) -> tuple[GateResult, ...]:
        return tuple(result for result in self.results if not result.passed)

    def table(self) -> str:
        lines = [f"{'gate':<16}{'metric':<28}{'value':>12}{'threshold':>12}  verdict"]
        for result in self.results:
            value = "n/a" if result.value is None else f"{result.value:.4f}".rstrip("0").rstrip(".")
            verdict = "pass" if result.passed else "FAIL"
            lines.append(
                f"{result.gate:<16}{result.metric:<28}{value:>12}"
                f"{result.threshold:>12.4g}  {verdict}"
            )
        lines.append(f"\nVerdict: {'PASS' if self.passed else 'FAIL'}")
        return "\n".join(lines)


class QualityStore(Protocol):
    def start_run(self, report_header: dict[str, Any]) -> str: ...

    def record_results(self, run_id: str, report: QualityReport) -> None: ...


# ---------------------------------------------------------------------------
# Metrics. Pure, so each one is testable without a pipeline.
# ---------------------------------------------------------------------------


def _share(values: Sequence[bool]) -> float | None:
    return sum(values) / len(values) if values else None


def classification_metrics(runs: Sequence[CaseRun]) -> dict[str, float | None]:
    return {
        "category_accuracy": _share([r.outcome.category == r.case.category for r in runs]),
        "urgency_accuracy": _share([r.outcome.urgency == r.case.urgency for r in runs]),
    }


def retrieval_metrics(runs: Sequence[CaseRun], *, k: int = 3) -> dict[str, float | None]:
    """Recall and MRR over the cases that name an expected article.

    Scored with C05's harness so retrieval is measured the same way here as it
    was when the corpus was accepted.
    """
    labelled = [run for run in runs if run.case.expected_article]
    if not labelled:
        return {"recall_at_1": None, f"recall_at_{k}": None, "mrr": None}

    cases = [
        knowledge_evaluation.LabelledCase(
            query=run.case.query,
            expected_ref=str(run.case.expected_article),
            category=run.case.category,
        )
        for run in labelled
    ]
    # Paired by position, not by query text: two cases can legitimately ask the
    # same question, and keying by it would score both against one result set.
    pending = iter([run.retrieved for run in labelled])
    report = knowledge_evaluation.evaluate(
        cases, lambda _case: next(pending, ()), label="pipeline", k=k
    )
    return {
        "recall_at_1": report.recall_at_1,
        f"recall_at_{k}": report.recall_at_k,
        "mrr": report.mrr,
    }


def groundedness_metrics(runs: Sequence[CaseRun]) -> dict[str, float | None]:
    """Only automatic answers are measured: a withheld draft is not a failure."""
    answered = [run for run in runs if run.outcome.route == "AUTO_RESOLVE" and run.outcome.draft]
    if not answered:
        # Nothing was answered automatically, so nothing ungrounded was sent.
        return {"validated_share": 1.0, "citation_validity": 1.0}
    return {
        "validated_share": _share([run.outcome.grounding_validated for run in answered]),
        "citation_validity": _share(
            [
                bool(run.outcome.citations)
                and set(run.outcome.citations).issubset(set(run.retrieved))
                for run in answered
            ]
        ),
    }


def safety_metrics(runs: Sequence[CaseRun]) -> dict[str, float | None]:
    risky = [run for run in runs if run.case.risky]
    expected = [run for run in runs if run.case.expected_rule_codes]
    return {
        "risky_never_auto_resolved": _share([run.outcome.route != "AUTO_RESOLVE" for run in risky])
        if risky
        else 1.0,
        "guardrail_recall": _share(
            [
                set(run.case.expected_rule_codes).issubset(set(run.outcome.rule_codes))
                for run in expected
            ]
        )
        if expected
        else 1.0,
    }


def percentile(values: Sequence[float], fraction: float) -> float | None:
    """Nearest-rank percentile: with few runs, no interpolation invents a value."""
    if not values:
        return None
    ordered = sorted(values)
    rank = max(1, math.ceil(fraction * len(ordered)))
    return float(ordered[rank - 1])


def latency_metrics(runs: Sequence[CaseRun]) -> dict[str, float | None]:
    return {"p95_ms": percentile([float(run.outcome.processing_ms) for run in runs], 0.95)}


def cost_metrics(runs: Sequence[CaseRun]) -> dict[str, float | None]:
    costs = [float(run.outcome.cost_micro) for run in runs]
    return {"mean_micro_units_per_run": sum(costs) / len(costs) if costs else None}


GATE_METRICS: dict[str, Callable[[Sequence[CaseRun]], dict[str, float | None]]] = {
    "classification": classification_metrics,
    "retrieval": retrieval_metrics,
    "groundedness": groundedness_metrics,
    "safety": safety_metrics,
    "latency": latency_metrics,
    "cost": cost_metrics,
}


def score(runs: Sequence[CaseRun], thresholds: ThresholdSet) -> tuple[GateResult, ...]:
    measured: dict[str, dict[str, float | None]] = {
        gate: metrics(runs) for gate, metrics in GATE_METRICS.items()
    }
    results: list[GateResult] = []
    for threshold in thresholds.thresholds:
        value = measured.get(threshold.gate, {}).get(threshold.metric)
        results.append(
            GateResult(
                gate=threshold.gate,
                metric=threshold.metric,
                value=value,
                threshold=threshold.threshold,
                direction=threshold.direction,
                passed=threshold.passes(value),
                detail=_detail(threshold, runs),
            )
        )
    return tuple(results)


def _detail(threshold: Threshold, runs: Sequence[CaseRun]) -> dict[str, Any]:
    """Which cases a failure would be about. A number alone is not actionable."""
    if threshold.gate == "classification" and threshold.metric == "category_accuracy":
        return {
            "misclassified": [
                {"case": r.case.case_id, "expected": r.case.category, "got": r.outcome.category}
                for r in runs
                if r.outcome.category != r.case.category
            ][:10]
        }
    if threshold.gate == "retrieval":
        return {
            "missed": [
                {
                    "case": r.case.case_id,
                    "expected": r.case.expected_article,
                    "got": list(r.retrieved[:3]),
                }
                for r in runs
                if r.case.expected_article and r.case.expected_article not in r.retrieved[:3]
            ][:10]
        }
    if threshold.gate == "safety":
        return {
            "auto_resolved_risky": [
                r.case.case_id for r in runs if r.case.risky and r.outcome.route == "AUTO_RESOLVE"
            ]
        }
    if threshold.gate == "groundedness":
        return {
            "ungrounded": [
                r.case.case_id
                for r in runs
                if r.outcome.route == "AUTO_RESOLVE"
                and r.outcome.draft
                and not r.outcome.grounding_validated
            ]
        }
    return {"cases": len(runs)}


# ---------------------------------------------------------------------------
# Running the check
# ---------------------------------------------------------------------------


def run_quality_check(
    *,
    triage: Callable[[QualityCase], TriageOutcome],
    retrieve: Callable[[QualityCase], Sequence[str]],
    knowledge_fingerprint: str,
    article_count: int,
    dataset: Dataset | None = None,
    thresholds: ThresholdSet | None = None,
    store: QualityStore | None = None,
    now: Callable[[], datetime] = lambda: datetime.now(UTC),
) -> QualityReport:
    dataset = dataset or Dataset.load()
    thresholds = thresholds or ThresholdSet.load()
    started = now()

    runs = [
        CaseRun(case=case, outcome=triage(case), retrieved=tuple(retrieve(case)))
        for case in dataset.cases
    ]
    report = QualityReport(
        dataset_version=dataset.version,
        threshold_version=thresholds.version,
        knowledge_fingerprint=knowledge_fingerprint,
        article_count=article_count,
        results=score(runs, thresholds),
        case_runs=tuple(runs),
        started_at=started,
        completed_at=now(),
    )
    if store is not None:
        run_id = store.start_run(
            {
                "dataset_version": report.dataset_version,
                "threshold_version": report.threshold_version,
                "knowledge_fingerprint": report.knowledge_fingerprint,
                "article_count": report.article_count,
                "started_at": report.started_at,
            }
        )
        store.record_results(run_id, report)
    return report


def conversation_for(case: QualityCase, *, organization_id: str, ticket_id: str) -> Conversation:
    return Conversation(
        organization_id=organization_id,
        ticket_id=ticket_id,
        subject=case.subject,
        body=case.body,
        from_address="customer@example.net",
    )


def expected_guardrail_codes(case: QualityCase) -> tuple[str, ...]:
    """The codes the MVP guardrails produce for a case, for dataset review."""
    guard = evaluate_guardrails(
        {"urgency": case.urgency, "content": f"{case.subject}\n{case.body}".strip()}
    )
    return tuple(guard["rule_codes"])
