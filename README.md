# ResolveFlow AI

Safe, explainable first-line customer-support triage built for the Customer Support Ticket Triage Agent hackathon.

ResolveFlow classifies each ticket, evaluates deterministic safety controls, searches a persistent support knowledge base through a **real MCP stdio client/server boundary**, and chooses one route:

- `AUTO_RESOLVE` — strong, low-risk KB match with a cited response draft
- `CLARIFY` — a required customer fact is missing
- `ESCALATE` — risk, uncertainty, weak evidence, or a provider/MCP failure

The app never sends customer messages autonomously.

## Requirement coverage

| Hackathon requirement | Implementation |
|---|---|
| Category + urgency | Typed billing/technical/account and low/medium/high/critical classification |
| Agentic loop | Inspectable LangGraph with Perceive → Classify → Risk → MCP/Qdrant → Rerank → Act → Validate → Observe |
| Real MCP tool use | Separate FastMCP process over stdio; no in-process KB shortcut |
| Safe routing | Deterministic guardrails override model output |
| Batch output | Six-case guided demo, full 36-ticket batch, and validated CSV import for up to 50 tickets |
| Explainability | Node trace, rule codes, timestamps, latency, MCP request/response, rerank scores, citations, grounding result |
| Dashboard | Queue, filters, details, KPIs, human controls, downloads, evaluation, architecture |
| Refusal case | Angry payment failure and account takeover are visibly escalated |

Sample batches and Quality Check use a protected 15-article demo corpus. User-added or replaced knowledge is used for manual and CSV tickets but cannot change the judge demo outcomes.

## Architecture

```text
Ticket
  → perceive
  → classify
  → risk_guard
  → kb_search_mcp ──stdio──> FastMCP server ──> SQLite + Qdrant dense/BM25 hybrid search
  → decide
      ├─ AUTO_RESOLVE → grounded OpenAI draft → citation/grounding validator
      ├─ CLARIFY     → draft_clarification
      └─ ESCALATE    → create_escalation
  → observe
```

The dashboard exposes recorded state facts and evidence, not hidden chain-of-thought.

## Setup

Python 3.11+ is required.

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e .
cp .env.example .env
```

A valid OpenAI API key is required for normal dashboard and CLI processing because both classification and dense embeddings use OpenAI. Automated tests use explicit network-free fixtures. Secrets are ignored by git.

## Run

```bash
source .venv/bin/activate
streamlit run app/dashboard.py
```

Open the printed local URL, keep **Guided demo · 6 tickets** selected, choose **Load sample batch**, then inspect T-001 and T-002 to show the contrast between grounded auto-resolution and safety-first escalation.

CLI smoke:

```bash
source .venv/bin/activate
python -m app.cli
```

Evaluation:

```bash
source .venv/bin/activate
python -m app.eval
```

Tests:

```bash
source .venv/bin/activate
pytest -q
```

Convenience targets are also available:

```bash
make run
make test
make eval
```

## Process real tickets from CSV

Open the top-level **Import ticket CSV** tab in the Ticket triage view. Download either the empty template or the editable 45-ticket realistic example, replace the examples with your own requests, and upload it. Required columns are `subject` and `body`; `ticket_id` and `customer_id` are optional. The app validates the file, rejects duplicate IDs, limits each run to 50 tickets, and routes every row through the real graph and MCP workflow.

The dashboard always uses the OpenAI provider and model configured in `.env`; there is no runtime mode selector. If OpenAI is unavailable, the trace records `MODEL_ERROR` and safely routes the ticket to a person rather than silently changing classifiers.

## Manage the support knowledge base

Open **Support knowledge** in the sidebar to view and export searchable help articles, add one approved article manually, import CSV, or ingest PDF, Markdown, and text files. A persistent action selector keeps the active workflow open after an import. Extracted sections are previewed before indexing. Imports can append to the current collection or replace it completely. A single confirmed action clears the complete knowledge base, and the 15 optional starter articles can be restored separately. A built-in test console runs the same real MCP search used by ticket triage.

These controls manage the operational knowledge used by manual and imported tickets. The guided sample and Quality Check intentionally use a separate protected starter corpus so a knowledge upload cannot break the reproducible evaluation story.

Knowledge article text, metadata, provenance, and timestamps are persisted in `app/data/knowledge.db` using SQLite. OpenAI `text-embedding-3-small` creates 1536-dimensional dense embeddings, and Qdrant local persistent mode indexes them in `app/data/qdrant`. Retrieval builds a candidate set from Qdrant cosine similarity and BM25 term relevance, then transparently reranks it with semantic, lexical, fuzzy, keyword, category, and reciprocal-rank signals. The MCP trace returns every score component and reranked position.

For an eligible low-risk ticket, OpenAI receives only the top retrieved passages and must return article citations plus exact supporting quotes. A fail-closed validator checks that citation IDs came from the MCP response and every support quote exists in its cited article. Invalid or insufficient grounding changes the route to human review and withholds the candidate draft.

## Demo in 60 seconds

1. Select **Guided demo · 6 tickets** and click **Load sample batch**.
2. Open T-001 and show `AUTO_RESOLVE`, KB-001, its score, and the MCP request.
3. Open T-002 and show `ESCALATE` despite relevant KB evidence because high urgency, anger, and payment-risk controls override it.
4. Switch to **Quality check** and run the live OpenAI readiness evaluation.
5. Show all seven gates: classification, routing, safety, unsafe automation, knowledge retrieval, grounding, and MCP evidence.

Curated order:

1. routine password reset → `AUTO_RESOLVE`
2. billing question missing identifiers → `CLARIFY`
3. angry payment failure → `ESCALATE`
4. account takeover concern → `ESCALATE`
5. routine 2FA FAQ → `AUTO_RESOLVE`
6. prompt injection / out-of-policy request → `ESCALATE`

## Safety and failure behavior

Auto-resolution is blocked by high/critical urgency, anger/threats, security risk, payment failure, refund/legal risk, missing information, low classification confidence, low KB score, model failure, or MCP failure. Provider and MCP outages are visible in the trace and route to human review.

## Current verified result

- 41 tests pass, including protected-demo isolation, persistent SQLite/Qdrant knowledge CRUD, document ingestion, hybrid paraphrase retrieval, real MCP search, paragraph-level citation and grounding validation, knowledge-CSV validation, hosted-output repair, 50-ticket CSV validation, SLA targets, rendered Streamlit interactions, and explicit contrast safeguards.
- Live OpenAI `gpt-5.6-luna` + `text-embedding-3-small` RAG smoke: a safe password-reset ticket reached `AUTO_RESOLVE` through real MCP/Qdrant retrieval with an exact stored citation and 2/2 grounding claims verified.
- Protected live six-ticket demo: 6/6 expected routes, 2 grounded answer drafts, 1 clarification, 3 safe escalations, and zero unsafe automatic drafts.
- Golden deterministic + real MCP/Qdrant run: 15/15 category and route matches; 9/9 paraphrase retrieval cases top-1.
- High-risk recall: 100%.
- Unsafe auto-resolves: 0.
- Live Streamlit startup: verified on `127.0.0.1:8504`.

See [TEST_REPORT.md](TEST_REPORT.md) for commands and honest limitations.
