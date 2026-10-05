"""Where a Quality Check is recorded.

A run is opened before the cases are measured and closed with a verdict, so a
check that crashes leaves a `RUNNING` row rather than no evidence at all. Each
gate is its own row: "quality passed" is a conclusion, and an operator needs
the metric that produced it.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import text

from app.triage.quality import QualityReport


class PostgresQualityStore:
    def __init__(self, engine: Any, organization_id: str, *, membership_id: str | None = None):
        self._engine = engine
        self.organization_id = organization_id
        self.membership_id = membership_id

    @contextmanager
    def _tx(self) -> Iterator[Any]:
        with self._engine.begin() as connection:
            yield connection

    def start_run(self, header: dict[str, Any]) -> str:
        with self._tx() as c:
            return str(
                c.execute(
                    text(
                        "INSERT INTO evaluation_runs (organization_id, dataset_version, "
                        "threshold_version, knowledge_fingerprint, article_count, "
                        "triggered_by_membership_id, status, started_at) VALUES "
                        "(CAST(:org AS uuid), :dataset, :thresholds, :fingerprint, :articles, "
                        "CAST(:membership AS uuid), 'RUNNING', :started) RETURNING id::text"
                    ),
                    {
                        "org": self.organization_id,
                        "dataset": header["dataset_version"],
                        "thresholds": header["threshold_version"],
                        "fingerprint": header["knowledge_fingerprint"],
                        "articles": header["article_count"],
                        "membership": self.membership_id,
                        "started": header["started_at"],
                    },
                ).scalar_one()
            )

    def record_results(self, run_id: str, report: QualityReport) -> None:
        with self._tx() as c:
            for result in report.results:
                c.execute(
                    text(
                        "INSERT INTO evaluation_results (organization_id, evaluation_run_id, "
                        "gate, metric, value, threshold, passed, detail) VALUES "
                        "(CAST(:org AS uuid), CAST(:run AS uuid), :gate, :metric, :value, "
                        ":threshold, :passed, CAST(:detail AS jsonb)) "
                        "ON CONFLICT (evaluation_run_id, gate, metric) DO UPDATE SET "
                        "value = EXCLUDED.value, threshold = EXCLUDED.threshold, "
                        "passed = EXCLUDED.passed, detail = EXCLUDED.detail"
                    ),
                    {
                        "org": self.organization_id,
                        "run": run_id,
                        "gate": result.gate,
                        "metric": result.metric,
                        # A metric that could not be computed is stored as -1
                        # and failed, never as a missing row that reads as
                        # "not measured" or, worse, as a pass.
                        "value": result.value if result.value is not None else -1,
                        "threshold": result.threshold,
                        "passed": result.passed,
                        "detail": json.dumps(
                            {**result.detail, "direction": result.direction},
                            sort_keys=True,
                            default=str,
                        ),
                    },
                )
            c.execute(
                text(
                    "UPDATE evaluation_runs SET status = 'COMPLETED', passed = :passed, "
                    "completed_at = :completed, updated_at = now() WHERE id = CAST(:run AS uuid)"
                ),
                {"run": run_id, "passed": report.passed, "completed": report.completed_at},
            )

    def fail_run(self, run_id: str, reason: str) -> None:
        with self._tx() as c:
            c.execute(
                text(
                    "UPDATE evaluation_runs SET status = 'FAILED', passed = false, "
                    "completed_at = now(), updated_at = now() WHERE id = CAST(:run AS uuid)"
                ),
                {"run": run_id},
            )

    def latest(self) -> dict[str, Any] | None:
        with self._tx() as c:
            run = c.execute(
                text(
                    "SELECT id::text AS id, dataset_version, threshold_version, "
                    "knowledge_fingerprint, article_count, status, passed, started_at, "
                    "completed_at FROM evaluation_runs WHERE organization_id = CAST(:org AS uuid) "
                    "ORDER BY started_at DESC LIMIT 1"
                ),
                {"org": self.organization_id},
            ).first()
            if run is None:
                return None
            results = c.execute(
                text(
                    "SELECT gate, metric, value, threshold, passed, detail FROM "
                    "evaluation_results WHERE evaluation_run_id = CAST(:run AS uuid) "
                    "ORDER BY gate, metric"
                ),
                {"run": run.id},
            ).all()
        return {
            **{key: getattr(run, key) for key in run._mapping},
            "results": [dict(row._mapping) for row in results],
        }


@dataclass
class InMemoryQualityStore:
    runs: list[dict[str, Any]] = field(default_factory=list)
    results: dict[str, QualityReport] = field(default_factory=dict)

    def start_run(self, header: dict[str, Any]) -> str:
        run_id = f"run-{len(self.runs) + 1}"
        self.runs.append({"id": run_id, "status": "RUNNING", **header})
        return run_id

    def record_results(self, run_id: str, report: QualityReport) -> None:
        self.results[run_id] = report
        for run in self.runs:
            if run["id"] == run_id:
                run["status"] = "COMPLETED"
                run["passed"] = report.passed
