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
      ├─ AUTO_RESOLVE → draft_resolution → validate_grounding
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

SQLite at `app/data/knowledge.db` is the article source of truth. Qdrant local persistent mode at `app/data/qdrant` is the dedicated vector index. On first RAG use, existing articles are batch-embedded with OpenAI `text-embedding-3-small` and indexed without discarding the ingested corpus.

Sample batches and Quality Check use `app/data/demo/knowledge.db` with a separate Qdrant index. This protected 15-article corpus makes the judge demo repeatable even after a user replaces the operational knowledge base.

Retrieval is hybrid and transparent: Qdrant dense cosine similarity and BM25 create a candidate set, then semantic similarity, term relevance, fuzzy coverage, keyword evidence, category preference, and reciprocal rank produce the final rerank score. Category remains a preference rather than a hard filter. The graph never imports or calls the server's search function directly.

Only `AUTO_RESOLVE` candidates reach OpenAI grounded generation. The model sees the ticket and retrieved passages only, and must return cited claims with exact evidence quotes. `validate_grounding` verifies retrieved citation IDs, answer markers, and exact quote provenance. Any generation or validation failure sets `GROUNDING_VALIDATION_FAILED`, withholds the answer, and changes the route to `ESCALATE`.

The trace records the tool name, transport, ticket query, category, top-k value, returned matches, score, and tool duration. Because this local hackathon build does not redact PII, users are warned not to submit secrets; production deployment requires redaction and retention controls.

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
- exact article citations and grounding-validation details
- processing duration
- complete trace

## Provider behavior

- Normal dashboard and CLI processing always use the configured OpenAI provider and model.
- The deterministic provider exists only as an explicit automated-test fixture; it is not a user-selectable runtime mode.
- Hosted output is validated against the typed `Classification` schema.
- Hosted-provider errors are not disguised as successful model output: the graph records `MODEL_ERROR` and escalates.

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
- `GROUNDING_VALIDATION_FAILED`

Route precedence:

1. Model or MCP failure → `ESCALATE`.
2. High-risk control → `ESCALATE`.
3. Missing required fact without a higher risk → `CLARIFY`.
4. Low model confidence or KB score below the configured threshold → `ESCALATE`.
5. Otherwise → generate only from retrieved passages and validate citations/support quotes.
6. Grounding failure → withhold the draft and `ESCALATE`; successful validation → `AUTO_RESOLVE`.

## Failure paths

- Provider exception or invalid schema → visible classification error → `MODEL_ERROR` → `ESCALATE`
- MCP connection/tool error → visible MCP error → `MCP_UNAVAILABLE` → `ESCALATE`
- Weak/no KB evidence → `LOW_KB_CONFIDENCE` → `ESCALATE`
- Missing billing lookup identifiers → `MISSING_INFORMATION` → `CLARIFY`
- Invalid citation, unsupported quote, insufficient evidence, or generation failure → `GROUNDING_VALIDATION_FAILED` → `ESCALATE`

## UI structure

- **Ticket triage:** three prominent start tabs for sample batches, one manual ticket, or CSV import; then KPIs, source/status/search filters, priority sorting, SLA response targets, queue, compact ticket facts, response/handoff, MCP evidence, grounding status, full trace, reviewer override, audit log, and downloads.
- **Support knowledge:** persistent action selector; browse/export articles; manually add an approved article; ingest PDF/Markdown/text; append or replace from CSV; create OpenAI dense embeddings; persist them in Qdrant; test the real MCP hybrid search; clear the entire knowledge base with typed confirmation; restore starters separately; and inspect both storage paths.
- **Quality check:** live configured-OpenAI evaluation across classification, routing, safety recall, unsafe automation, paraphrase retrieval, grounded citations, and complete MCP traces.
- **How it works:** rendered graph flow, MCP process boundary, route contract, and safety policy.

The UI displays evidence and state transitions, never hidden chain-of-thought.
