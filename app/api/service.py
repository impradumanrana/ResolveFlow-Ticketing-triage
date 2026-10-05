from __future__ import annotations

from typing import Protocol

from app.graph import build_workflow
from app.mcp_client import MCPClient
from app.models import Ticket, TriageResult
from app.providers import OpenAIProvider


class TriageServiceProtocol(Protocol):
    def triage(self, ticket: Ticket) -> TriageResult: ...


class TriageService:
    """Thin adapter that preserves the verified graph until later persistence phases."""

    def triage(self, ticket: Ticket) -> TriageResult:
        provider = OpenAIProvider()
        mcp_client = MCPClient()
        workflow = build_workflow(provider, mcp_client)
        state = workflow.invoke(
            {
                "ticket": ticket,
                "provider": provider,
                "mcp_client": mcp_client,
                "trace": [],
            }
        )
        return state["result"]


def get_triage_service() -> TriageServiceProtocol:
    return TriageService()
