# Architecture

## Current implementation

ResolveFlow AI is a Python 3.11+ application using LangGraph, FastMCP/MCP over stdio, Streamlit, Pydantic, and pytest. The repository contains a working CLI, dashboard, evaluation command, fixtures, and tests.

## Runtime flow

```text
START
  → perceive
  → classify
  → risk_guard
  → kb_search_mcp
  → decide
      ├─ AUTO_RESOLVE → draft_resolution
      ├─ CLARIFY     → draft_clarification
      └─ ESCALATE    → create_escalation
  → observe
  → END
```

Every node appends a typed `TraceEvent` with timestamp, measured duration, status, a concise message, and recorded evidence.

## MCP boundary

`MCPClient` launches `app/mcp_server.py` as a distinct Python process and opens an MCP client session over stdio. The graph calls:

```text
search_knowledge_base(query, category, top_k)
```

The server loads all 15 articles from `app/fixtures/faqs.json`, performs transparent token/phrase/fuzzy scoring, and returns bounded ranked matches. The graph never imports or calls the server's search function directly.

The trace records the tool name, transport, sanitized ticket query, category, top-k value, returned matches, score, and tool duration.

## State and result

Inputs:

- typed `Ticket`
- provider adapter
- MCP client
- trace list

Recorded state includes the correlation ID, normalized ticket text, validated classification, guardrail result, KB matches, selected route, rule codes, and draft.

`TriageResult` exposes:

- ticket ID, correlation ID
- category, urgency, confidence, queue
- route and decision summary
- top KB match and MCP health
- response/clarification/escalation draft
- processing duration
- complete trace

## Provider behavior

- The deterministic provider is the reliable offline demo/test path.
- The OpenAI adapter is selected only when `LLM_PROVIDER=openai` and a non-placeholder API key is present.
- Hosted output is validated against the typed `Classification` schema.
- Hosted-provider errors are not disguised as successful model output: the graph records `MODEL_ERROR` and escalates.

Gemini, Groq, and Ollama environment fields remain future adapter points; they are not claimed as implemented runtime providers.

## Guardrails and routing

Deterministic safety controls override the model:

- `HIGH_URGENCY`
- `CRITICAL_URGENCY`
- `ANGRY_CUSTOMER`
- `THREAT_DETECTED`
- `SECURITY_RISK`
- `PAYMENT_FAILURE`
- `REFUND_OR_LEGAL`
- `MISSING_INFORMATION`
- `LOW_CLASSIFICATION_CONFIDENCE`
- `LOW_KB_CONFIDENCE`
- `MODEL_ERROR`
- `MCP_UNAVAILABLE`

Route precedence:

1. Model or MCP failure → `ESCALATE`.
2. High-risk control → `ESCALATE`.
3. Missing required fact without a higher risk → `CLARIFY`.
4. Low model confidence or KB score below the configured threshold → `ESCALATE`.
5. Otherwise → `AUTO_RESOLVE` with article ID/title citation.

## Failure paths

- Provider exception or invalid schema → visible classification error → `MODEL_ERROR` → `ESCALATE`
- MCP connection/tool error → visible MCP error → `MCP_UNAVAILABLE` → `ESCALATE`
- Weak/no KB evidence → `LOW_KB_CONFIDENCE` → `ESCALATE`
- Missing billing lookup identifiers → `MISSING_INFORMATION` → `CLARIFY`

## UI structure

- **Ticket triage:** KPIs, 6- or 36-ticket sample batches, validated CSV import up to 50 tickets, single-ticket form, source/status/search filters, queue, compact ticket facts, response/handoff, compact MCP evidence, seven-step trace, reviewer override, audit log, and downloads.
- **Quality check:** measured golden metrics, route distribution, expected-vs-actual table.
- **How it works:** rendered graph flow, MCP process boundary, route contract, and safety policy.

The UI displays evidence and state transitions, never hidden chain-of-thought.
