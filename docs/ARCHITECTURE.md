# Architecture

## Stack

Python 3.11+ (developed and verified on 3.14.5) with LangGraph, FastMCP/MCP over stdio, Streamlit, Pydantic, SQLite, Qdrant, and pytest. OpenAI supplies both the classifier and the dense embeddings.

## Runtime flow

```text
START
  → perceive
  → classify
  → risk_guard
  → kb_search_mcp
  → decide
      ├─ AUTO_RESOLVE → draft_resolution → validate_grounding
      ├─ CLARIFY      → draft_clarification
      └─ ESCALATE     → create_escalation
  → observe
  → END
```

Every node appends a typed `TraceEvent` carrying a timestamp, a measured duration, a status, a concise message, and its recorded evidence. The trace is what the dashboard renders — the app shows state facts and tool evidence, never hidden chain-of-thought.

| Node | Responsibility |
|---|---|
| `perceive` | Normalize the ticket, assign a correlation ID |
| `classify` | OpenAI structured classification into category, urgency, confidence |
| `risk_guard` | Deterministic safety controls over the raw ticket text |
| `kb_search_mcp` | Hybrid knowledge search across the real MCP stdio boundary |
| `decide` | Apply route precedence over model output and guardrail results |
| `draft_resolution` | Generate an answer from retrieved passages only |
| `validate_grounding` | Fail-closed citation and supporting-quote verification |
| `draft_clarification` | Ask for the one missing customer fact |
| `create_escalation` | Produce a handoff note with the triggered rule codes |
| `observe` | Close the trace and record the final outcome |

## The MCP boundary

`MCPClient` launches [`app/mcp_server.py`](../app/mcp_server.py) as a **separate Python process** and opens an MCP client session over stdio. The graph never imports or calls the server's search function directly — the process boundary is real, and the trace records the transport, tool name, request, response, score, and duration.

The server exposes one tool:

```python
search_knowledge_base(query: str, category: str, top_k: int = 3)
```

Each lookup starts a short-lived stdio server process. That favors visible isolation and demo simplicity over throughput; a long-lived pooled session is the natural production change.

## Storage and retrieval

- **SQLite** (`app/data/knowledge.db`) is the source of truth for article text, metadata, provenance, and timestamps.
- **Qdrant** in local persistent mode (`app/data/qdrant`) is the dedicated vector index.
- **OpenAI `text-embedding-3-small`** produces 1536-dimensional dense vectors. On first RAG use, existing articles are batch-embedded and indexed without discarding the ingested corpus.

Retrieval is hybrid and transparent. Qdrant cosine similarity and BM25 term relevance build a candidate set; a rerank stage then combines semantic similarity, lexical relevance, fuzzy coverage, keyword evidence, category preference, and reciprocal rank. Category is a *preference*, not a hard filter. The MCP response returns every score component and the reranked position, so the dashboard can show why a given article won.

### Two corpora, on purpose

| Corpus | Path | Used by |
|---|---|---|
| Protected demo | `app/data/demo/knowledge.db` + its own Qdrant index | Guided sample batches |
| Operational | `app/data/knowledge.db` + `app/data/qdrant` | Manual tickets, CSV import, Quality check |

The 15-article protected corpus keeps the guided six-ticket demo reproducible even after a user replaces the operational knowledge base. Quality check deliberately uses the operational corpus instead, so it reports on the knowledge actually in service. See [D-013 and D-014](DECISIONS.md).

## Grounded generation

Only an `AUTO_RESOLVE` candidate reaches generation. The model sees the ticket and the retrieved passages — nothing else — and must return article citations plus exact supporting quotes.

`validate_grounding` then verifies, fail-closed, that:

1. every citation ID came back from the MCP response,
2. the answer carries the required markers, and
3. every support quote exists verbatim in its cited article.

Any generation or validation failure sets `GROUNDING_VALIDATION_FAILED`, withholds the draft, and changes the route to `ESCALATE`.

## State and result

`TriageResult` exposes the ticket and correlation IDs, category, urgency, confidence, queue, route, decision summary, top article match, MCP health, the response/clarification/escalation draft, exact citations with grounding detail, processing duration, and the complete trace.

## Provider behavior

- The dashboard and CLI always use the OpenAI provider and model configured in `.env`.
- Hosted output is validated against the typed `Classification` schema, with one bounded repair attempt.
- A provider error is never disguised as successful model output: the graph records `MODEL_ERROR` and escalates.
- The deterministic provider exists **only** as an explicit automated-test fixture and is not selectable at runtime.

## Guardrails and routing

Deterministic controls override the model. The full rule-code set:

`HIGH_URGENCY` · `CRITICAL_URGENCY` · `ANGRY_CUSTOMER` · `THREAT_DETECTED` · `SECURITY_RISK` · `PAYMENT_FAILURE` · `REFUND_OR_LEGAL` · `MISSING_INFORMATION` · `LOW_CLASSIFICATION_CONFIDENCE` · `LOW_KB_CONFIDENCE` · `MODEL_ERROR` · `MCP_UNAVAILABLE` · `GROUNDING_VALIDATION_FAILED`

Route precedence, in order:

1. Model or MCP failure → `ESCALATE`
2. Any high-risk control → `ESCALATE`
3. A missing required fact with no higher risk → `CLARIFY`
4. Low model confidence, or an article score below `KB_THRESHOLD` → `ESCALATE`
5. Otherwise generate from retrieved passages only, then validate
6. Grounding failure → withhold the draft and `ESCALATE`; success → `AUTO_RESOLVE`

## Failure paths

| Trigger | Rule code | Route |
|---|---|---|
| Provider exception or invalid schema | `MODEL_ERROR` | `ESCALATE` |
| MCP connection or tool error | `MCP_UNAVAILABLE` | `ESCALATE` |
| Weak or out-of-scope evidence | `LOW_KB_CONFIDENCE` | `ESCALATE` |
| Missing billing lookup identifiers | `MISSING_INFORMATION` | `CLARIFY` |
| Invalid citation or unsupported quote | `GROUNDING_VALIDATION_FAILED` | `ESCALATE` |

## UI structure

- **Ticket triage** — three start tabs (sample batch, one manual ticket, CSV import), then KPIs, filters, priority sorting, SLA targets, the queue, ticket facts, response/handoff, MCP evidence, grounding status, the full trace, reviewer override, audit log, and downloads.
- **Support knowledge** — browse and export articles, add one manually, import CSV, ingest PDF/Markdown/text with chunk preview, append or replace, clear all behind a typed confirmation, restore starters, and a console that runs the real MCP search.
- **Quality check** — live evaluation of the operational corpus across seven gates.
- **How it works** — the graph, the MCP process boundary, the route contract, and the safety policy.

## Styling note

The dashboard ships a custom light theme. Streamlit's rendered DOM in this build exposes **no `data-baseweb` attributes**, so all control styling targets stable `data-testid` values and ARIA roles (`stTextInputRootElement`, `stTextAreaRootElement`, `stSelectbox div[role="group"]`, `stTab`, and the detached `portal` that hosts open dropdowns). A regression test asserts that no `[data-baseweb=` selector reappears, because such a rule fails silently and leaves controls with an invisible white-on-white border.
