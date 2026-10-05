"""Labelled retrieval evaluation.

The C05 gate is a measurement, not an assertion: labelled Recall@K and MRR
thresholds must pass, and the migration from local SQLite/Qdrant to pgvector
must not regress quality. The largest risk in this phase is a silent quality
drop that no unit test would catch, so the comparison is explicit.

Metrics are deliberately simple and standard:

* **Recall@K** - fraction of cases whose expected article appears in the top K.
  This is what an agent experiences: did the right answer show up at all.
* **MRR** - mean reciprocal rank of the expected article. This is what the
  route decision experiences: was the right answer *first*, because only the
  top match is considered for grounding.

Both are computed at article level. A passage-level metric would flatter the
system by counting any chunk of the right article as a hit regardless of rank.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

DEFAULT_K = 3

# Thresholds are versioned so a change is a decision with a date, not a quiet
# edit. They match the MVP's demonstrated behaviour on the labelled set.
THRESHOLD_VERSION = "c05.2026-09-15"
THRESHOLDS: dict[str, float] = {
    "recall_at_1": 0.80,
    "recall_at_3": 0.90,
    "mrr": 0.85,
}


@dataclass(frozen=True)
class LabelledCase:
    query: str
    expected_ref: str
    category: str = ""
    note: str = ""


@dataclass(frozen=True)
class CaseOutcome:
    case: LabelledCase
    retrieved_refs: tuple[str, ...]
    rank: int | None
    top_score: float

    @property
    def hit_at_1(self) -> bool:
        return self.rank == 1

    def hit_at(self, k: int) -> bool:
        return self.rank is not None and self.rank <= k

    @property
    def reciprocal_rank(self) -> float:
        return 1.0 / self.rank if self.rank else 0.0


@dataclass(frozen=True)
class EvaluationReport:
    label: str
    outcomes: tuple[CaseOutcome, ...]
    k: int = DEFAULT_K

    @property
    def case_count(self) -> int:
        return len(self.outcomes)

    @property
    def recall_at_1(self) -> float:
        return self._mean(outcome.hit_at_1 for outcome in self.outcomes)

    @property
    def recall_at_k(self) -> float:
        return self._mean(outcome.hit_at(self.k) for outcome in self.outcomes)

    @property
    def mrr(self) -> float:
        return self._mean(outcome.reciprocal_rank for outcome in self.outcomes)

    @property
    def misses(self) -> tuple[CaseOutcome, ...]:
        return tuple(outcome for outcome in self.outcomes if not outcome.hit_at(self.k))

    def _mean(self, values: Any) -> float:
        collected = [float(value) for value in values]
        return sum(collected) / len(collected) if collected else 0.0

    def metrics(self) -> dict[str, float]:
        return {
            "recall_at_1": self.recall_at_1,
            f"recall_at_{self.k}": self.recall_at_k,
            "mrr": self.mrr,
        }

    def gate_results(self) -> dict[str, tuple[float, float, bool]]:
        """metric -> (value, threshold, passed)."""
        measured = {
            "recall_at_1": self.recall_at_1,
            "recall_at_3": self.recall_at_k,
            "mrr": self.mrr,
        }
        return {
            name: (measured[name], threshold, measured[name] >= threshold)
            for name, threshold in THRESHOLDS.items()
        }

    @property
    def passed(self) -> bool:
        return all(passed for _, _, passed in self.gate_results().values())

    def describe(self) -> str:
        lines = [
            f"{self.label}: {self.case_count} labelled cases (K={self.k})",
            f"  recall@1 {self.recall_at_1:.3f}   "
            f"recall@{self.k} {self.recall_at_k:.3f}   MRR {self.mrr:.3f}",
        ]
        for name, (value, threshold, passed) in self.gate_results().items():
            lines.append(
                f"  {'PASS' if passed else 'FAIL'} {name:<12} "
                f"{value:.3f} (threshold {threshold:.2f})"
            )
        for outcome in self.misses:
            lines.append(
                f"  miss: {outcome.case.query!r} expected {outcome.case.expected_ref}, "
                f"got {list(outcome.retrieved_refs[: self.k])}"
            )
        return "\n".join(lines)


def load_cases(path: Path) -> list[LabelledCase]:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    return [
        LabelledCase(
            query=item["query"],
            expected_ref=item["expected_ref"],
            category=item.get("category", ""),
            note=item.get("note", ""),
        )
        for item in payload
    ]


def evaluate(
    cases: Sequence[LabelledCase],
    retrieve: Callable[[LabelledCase], Sequence[str]],
    *,
    label: str,
    k: int = DEFAULT_K,
) -> EvaluationReport:
    """Run `retrieve` over each case and score the ranked article references.

    `retrieve` returns article references in rank order, so the same harness
    scores the MVP path and the pgvector path without either knowing about the
    other.
    """
    outcomes: list[CaseOutcome] = []

    for case in cases:
        refs = tuple(retrieve(case))
        rank = None
        for position, ref in enumerate(refs, start=1):
            if ref == case.expected_ref:
                rank = position
                break
        outcomes.append(
            CaseOutcome(
                case=case,
                retrieved_refs=refs,
                rank=rank,
                top_score=0.0,
            )
        )

    return EvaluationReport(label=label, outcomes=tuple(outcomes), k=k)


def compare(baseline: EvaluationReport, candidate: EvaluationReport) -> str:
    """Side-by-side report. A regression must be visible, not inferred."""
    lines = [
        f"{'metric':<12} {baseline.label:>12} {candidate.label:>12}   delta",
    ]
    for name in ("recall_at_1", f"recall_at_{candidate.k}", "mrr"):
        before = baseline.metrics().get(name, 0.0)
        after = candidate.metrics().get(name, 0.0)
        delta = after - before
        marker = "" if delta >= 0 else "   REGRESSION"
        lines.append(f"{name:<12} {before:>12.3f} {after:>12.3f}   {delta:+.3f}{marker}")
    return "\n".join(lines)
