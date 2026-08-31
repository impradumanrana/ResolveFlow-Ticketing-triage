from __future__ import annotations

import json

from app.config import HAS_OPENAI_KEY, LLM_PROVIDER
from app.graph import build_workflow
from app.mcp_client import MCPClient
from app.models import Ticket
from app.providers import DeterministicProvider, OpenAIProvider


def triage_ticket(ticket: Ticket, provider=None, mcp_client=None):
    if provider is None:
        if LLM_PROVIDER == "openai" and HAS_OPENAI_KEY:
            provider = OpenAIProvider()
        else:
            provider = DeterministicProvider()
    mcp_client = mcp_client or MCPClient()
    app = build_workflow(provider, mcp_client)
    state = {
        "ticket": ticket,
        "provider": provider,
        "mcp_client": mcp_client,
        "trace": [],
    }
    final_state = app.invoke(state)
    return final_state["result"]


if __name__ == "__main__":
    tickets = [
        Ticket(
            ticket_id="T-001",
            customer_id="C-001",
            subject="Password reset",
            body="I need help resetting my password. I just need the reset link and I am not blocked.",
        ),
        Ticket(
            ticket_id="T-002",
            customer_id="C-002",
            subject="Payment issue",
            body="I was charged twice and I am furious. I will dispute this charge if you do not fix it quickly.",
        ),
        Ticket(
            ticket_id="T-003",
            customer_id="C-003",
            subject="Billing question",
            body="I have a billing question. Please help.",
        ),
    ]
    for ticket in tickets:
        result = triage_ticket(ticket)
        print(json.dumps(result.model_dump(), indent=2))
