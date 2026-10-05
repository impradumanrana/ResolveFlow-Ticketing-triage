"""The C11 demonstration: one conversation, every boundary visible.

Prints the request, the model call, the guardrails, the MCP tool call and its
response, the decision, the grounded draft, and the validator's verdict - with
the actual values at each step, not a narration of them.

Offline by default: a fixture corpus over the real MCP stdio boundary and the
offline provider, so it runs with no database, no network, and no paid model.
`--live` runs the same pipeline against the client's PostgreSQL corpus and the
client's own provider, and refuses unless that spend is explicitly authorized.
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
DEMO_ORGANIZATION = "00000000-0000-0000-0000-0000000000a1"
RULE = "-" * 78


def heading(step: int, title: str) -> None:
    print(f"\n{RULE}\n{step}. {title}\n{RULE}")


def show(payload: Any) -> None:
    print(json.dumps(payload, indent=2, default=str)[:1600])


def offline_pipeline(subject: str, body: str) -> tuple[Any, Any, Any]:
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

    secret = "projects/demo/secrets/demo-llm-provider-api-key"
    config = ProviderConfig(
        organization_id=DEMO_ORGANIZATION,
        provider="sim",
        credential_secret_name=secret,
        credential_last_four="demo",
        classification_model="offline-classifier",
        generation_model="offline-writer",
        embedding_model="offline-embedder",
        embedding_dimensions=1536,
        region="eu",
        monthly_budget_minor_units=10_000,
    )
    approvals = [
        ModelApproval("sim", "offline-classifier", Operation.CLASSIFICATION, "eu", 40, 160, 400),
        ModelApproval("sim", "offline-writer", Operation.GENERATION, "eu", 200, 800, 1500),
    ]

    class Secrets:
        def access(self, name: str) -> SecretValue:
            return SecretValue("sk-offline-demonstration-key-0000")

        def add_version(self, name: str, value: SecretValue) -> str:  # pragma: no cover
            raise RuntimeError("The demonstration never writes a key.")

    gateway = AIGateway(
        InMemoryGatewayStore(configs=[config], approvals=approvals),
        CredentialCache(Secrets()),
        {"sim": DeterministicAdapter()},
        index_dimensions=1536,
    )
    knowledge = KnowledgeMcpClient(
        env={"RESOLVEFLOW_MCP_FIXTURE": str(FIXTURE), "RESOLVEFLOW_TEST_MODE": "1"}
    )
    return TriagePipeline(gateway, knowledge, InMemoryTriageStore()), knowledge, DEMO_ORGANIZATION


def live_pipeline(database_url: str, organization_id: str) -> tuple[Any, Any, Any]:
    from sqlalchemy import create_engine

    from app.gateway.credentials import CredentialCache, SecretManagerSource
    from app.gateway.openai_adapter import OpenAIAdapter
    from app.gateway.service import AIGateway
    from app.gateway.store import PostgresGatewayStore
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
    return (
        TriagePipeline(gateway, knowledge, PostgresTriageStore(engine)),
        knowledge,
        organization_id,
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--subject", default="Reset my password")
    parser.add_argument(
        "--body", default="I forgot my password and need a reset link so I can sign in again."
    )
    parser.add_argument("--ticket-id", default=str(uuid.uuid4()))
    parser.add_argument(
        "--live", action="store_true", help="Run against the client's own corpus and provider."
    )
    parser.add_argument("--database-url", default=os.getenv("DATABASE_URL", ""))
    parser.add_argument("--organization", default=os.getenv("RESOLVEFLOW_ORGANIZATION_ID", ""))
    parser.add_argument(
        "--i-have-client-authorization",
        action="store_true",
        help="Required with --live: a live run calls the client's paid model.",
    )
    arguments = parser.parse_args()

    if arguments.live:
        if not (arguments.database_url and arguments.organization):
            print("A live demonstration needs --database-url and --organization.", file=sys.stderr)
            return 2
        if not arguments.i_have_client_authorization:
            print(
                "Refusing: a live demonstration calls the client's paid model. "
                "Re-run with --i-have-client-authorization once that is approved.",
                file=sys.stderr,
            )
            return 2
        pipeline, knowledge, organization = live_pipeline(
            arguments.database_url, arguments.organization
        )
        corpus = f"PostgreSQL corpus for organization {arguments.organization}"
    else:
        pipeline, knowledge, organization = offline_pipeline(arguments.subject, arguments.body)
        corpus = (
            f"fixture corpus {FIXTURE.name} "
            f"({hashlib.sha256(FIXTURE.read_bytes()).hexdigest()[:12]})"
        )

    from app.triage.records import Conversation

    heading(1, "REQUEST")
    print(
        f"subject: {arguments.subject}\nbody:    {arguments.body}\nticket:  {arguments.ticket_id}"
    )
    print(f"corpus:  {corpus}")

    try:
        outcome = pipeline.triage(
            Conversation(
                organization_id=organization,
                ticket_id=arguments.ticket_id,
                subject=arguments.subject,
                body=arguments.body,
                from_address="customer@example.net",
            )
        )
    finally:
        close = getattr(knowledge, "close", None)
        if callable(close):
            close()

    events = {event["node"]: event for event in outcome.trace}

    heading(2, "MODEL CALL (classification, through the client's gateway)")
    classify = events.get("classify", {})
    print(f"status:  {classify.get('status')}  in {classify.get('duration_ms')} ms")
    show(classify.get("data"))

    heading(3, "GUARDRAILS (deterministic, before any routing)")
    show(events.get("risk_guard", {}).get("data"))

    heading(4, "MCP CALL (stdio boundary)")
    print("tool:      search_knowledge_base\ntransport: stdio\n")
    show(getattr(knowledge, "last_request", None))

    heading(5, "KNOWLEDGE RESPONSE (retrieved passages)")
    for match in events.get("kb_search_mcp", {}).get("data", {}).get("matches", []) or []:
        print(f"  {match['article_id']}  score {match['score']:.3f}  {match['title']}")
        print(f"      {match['excerpt'][:110]}...")

    heading(6, "DECISION")
    show(events.get("decide", {}).get("data"))
    print(f"final route after client rules: {outcome.route}")
    print(f"rule codes: {list(outcome.rule_codes) or 'none'}")

    heading(7, "GROUNDED ANSWER (evidence only)")
    print(outcome.draft or "(no draft: this conversation goes to a person)")

    heading(8, "VALIDATOR")
    show(outcome.grounding_details)

    heading(9, "RECORDED")
    print(
        f"correlation: {outcome.correlation_id}\n"
        f"route:       {outcome.route}\n"
        f"citations:   {list(outcome.citations)}\n"
        f"tokens:      {outcome.prompt_tokens} prompt, {outcome.completion_tokens} completion\n"
        f"cost:        {outcome.cost_micro} micro-units (estimated, client prices)\n"
        f"latency:     {outcome.processing_ms} ms"
    )
    print("\nNothing was sent to the customer. Observe Mode (C-D010).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
