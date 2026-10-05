"""Run the Quality Check and print the gate table.

Offline by default: the labelled dataset against the fixture corpus and the
offline provider, which is what CI can run on every change. `--live` measures
the client's own corpus with the client's own provider, and refuses unless the
spend that implies is explicitly authorized.

Exit status is the verdict, so this can be a build step.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import uuid
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

FIXTURE = ROOT / "app" / "fixtures" / "quality" / "knowledge_fixture.json"
OFFLINE_ORGANIZATION = "00000000-0000-0000-0000-0000000000a1"


def offline_parts() -> tuple[Any, Any, str, str, int]:
    os.environ.setdefault("RESOLVEFLOW_TEST_MODE", "1")

    from app.gateway.contract import Operation
    from app.gateway.credentials import CredentialCache, SecretValue
    from app.gateway.memory_store import InMemoryGatewayStore
    from app.gateway.policy import ModelApproval, ProviderConfig
    from app.gateway.service import AIGateway
    from app.triage.mcp_client import KnowledgeMcpClient
    from app.triage.memory_store import InMemoryTriageStore
    from app.triage.offline_provider import DeterministicAdapter
    from app.triage.pipeline import TriagePipeline

    config = ProviderConfig(
        organization_id=OFFLINE_ORGANIZATION,
        provider="sim",
        credential_secret_name="projects/offline/secrets/offline-key",
        credential_last_four=None,
        classification_model="offline-classifier",
        generation_model="offline-writer",
        embedding_model="offline-embedder",
        embedding_dimensions=1536,
        region="eu",
        monthly_budget_minor_units=100_000,
    )
    approvals = [
        ModelApproval("sim", "offline-classifier", Operation.CLASSIFICATION, "eu", 40, 160, 400),
        ModelApproval("sim", "offline-writer", Operation.GENERATION, "eu", 200, 800, 1500),
    ]

    class Secrets:
        def access(self, name: str) -> SecretValue:
            return SecretValue("sk-offline-quality-check-key-0000")

        def add_version(self, name: str, value: SecretValue) -> str:  # pragma: no cover
            raise RuntimeError("The Quality Check never writes a key.")

    gateway = AIGateway(
        InMemoryGatewayStore(configs=[config], approvals=approvals),
        CredentialCache(Secrets()),
        {"sim": DeterministicAdapter()},
        index_dimensions=1536,
    )
    knowledge = KnowledgeMcpClient(
        env={"RESOLVEFLOW_MCP_FIXTURE": str(FIXTURE), "RESOLVEFLOW_TEST_MODE": "1"}
    )
    pipeline = TriagePipeline(gateway, knowledge, InMemoryTriageStore())
    fingerprint = f"fixture-{hashlib.sha256(FIXTURE.read_bytes()).hexdigest()[:12]}"
    return (
        pipeline,
        knowledge,
        OFFLINE_ORGANIZATION,
        fingerprint,
        len(json.loads(FIXTURE.read_text())),
    )


def live_parts(database_url: str, organization_id: str) -> tuple[Any, Any, str, str, int]:
    from sqlalchemy import create_engine

    from app.gateway.credentials import CredentialCache, SecretManagerSource
    from app.gateway.openai_adapter import OpenAIAdapter
    from app.gateway.service import AIGateway
    from app.gateway.store import PostgresGatewayStore
    from app.knowledge import repository
    from app.triage.mcp_client import KnowledgeMcpClient
    from app.triage.pipeline import TriagePipeline
    from app.triage.store import PostgresTriageStore

    engine = create_engine(database_url, pool_pre_ping=True)
    gateway = AIGateway(
        PostgresGatewayStore(engine),
        CredentialCache(SecretManagerSource()),
        {"openai": OpenAIAdapter()},
    )
    knowledge = KnowledgeMcpClient(
        env={"DATABASE_URL": database_url, "RESOLVEFLOW_ORGANIZATION_ID": organization_id}
    )
    with engine.connect() as connection:
        fingerprint = repository.knowledge_fingerprint(connection, organization_id)
        articles = repository.retrievable_article_count(connection, organization_id)
    pipeline = TriagePipeline(gateway, knowledge, PostgresTriageStore(engine))
    return pipeline, knowledge, organization_id, fingerprint, articles


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true")
    parser.add_argument("--database-url", default=os.getenv("DATABASE_URL", ""))
    parser.add_argument("--organization", default=os.getenv("RESOLVEFLOW_ORGANIZATION_ID", ""))
    parser.add_argument("--membership", default=None, help="Who asked for this run.")
    parser.add_argument("--record", action="store_true", help="Store the run in evaluation_runs.")
    parser.add_argument("--i-have-client-authorization", action="store_true")
    parser.add_argument("--json", action="store_true", help="Print the gates as JSON.")
    arguments = parser.parse_args()

    from app.triage.quality import conversation_for, run_quality_check

    store = None
    if arguments.live:
        if not (arguments.database_url and arguments.organization):
            print("A live check needs --database-url and --organization.", file=sys.stderr)
            return 2
        if not arguments.i_have_client_authorization:
            print(
                "Refusing: a live Quality Check calls the client's paid model for every case. "
                "Re-run with --i-have-client-authorization once that is approved.",
                file=sys.stderr,
            )
            return 2
        pipeline, knowledge, organization, fingerprint, articles = live_parts(
            arguments.database_url, arguments.organization
        )
    else:
        pipeline, knowledge, organization, fingerprint, articles = offline_parts()

    if arguments.record:
        if not arguments.database_url:
            print("Recording a run needs --database-url.", file=sys.stderr)
            return 2
        from sqlalchemy import create_engine

        from app.triage.quality_store import PostgresQualityStore

        store = PostgresQualityStore(
            create_engine(arguments.database_url), organization, membership_id=arguments.membership
        )

    try:
        report = run_quality_check(
            triage=lambda case: pipeline.triage(
                conversation_for(case, organization_id=organization, ticket_id=str(uuid.uuid4()))
            ),
            retrieve=lambda case: [
                result["article_id"]
                for result in knowledge.search(case.query, case.category, top_k=3)
            ],
            knowledge_fingerprint=fingerprint,
            article_count=articles,
            store=store,
        )
    finally:
        close = getattr(knowledge, "close", None)
        if callable(close):
            close()

    if arguments.json:
        print(
            json.dumps(
                {
                    "dataset_version": report.dataset_version,
                    "threshold_version": report.threshold_version,
                    "knowledge_fingerprint": report.knowledge_fingerprint,
                    "article_count": report.article_count,
                    "passed": report.passed,
                    "gates": [
                        {
                            "gate": result.gate,
                            "metric": result.metric,
                            "value": result.value,
                            "threshold": result.threshold,
                            "passed": result.passed,
                        }
                        for result in report.results
                    ],
                },
                indent=2,
            )
        )
    else:
        print(
            f"dataset {report.dataset_version}  thresholds {report.threshold_version}  "
            f"corpus {report.knowledge_fingerprint} ({report.article_count} articles)\n"
        )
        print(report.table())
        for failure in report.failures:
            detail = json.dumps(failure.detail, default=str)[:400]
            print(f"\n{failure.gate}.{failure.metric} detail: {detail}")

    return 0 if report.passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
