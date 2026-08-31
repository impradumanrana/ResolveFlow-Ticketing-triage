# ResolveFlow AI

Safe, explainable first-line customer-support triage built for the Customer Support Ticket Triage Agent hackathon.

ResolveFlow classifies each ticket, evaluates deterministic safety controls, searches a 15-article knowledge base through a **real MCP stdio client/server boundary**, and chooses one route:

- `AUTO_RESOLVE` — strong, low-risk KB match with a cited response draft
- `CLARIFY` — a required customer fact is missing
- `ESCALATE` — risk, uncertainty, weak evidence, or a provider/MCP failure

The app never sends customer messages autonomously.

## Requirement coverage

| Hackathon requirement | Implementation |
|---|---|
| Category + urgency | Typed billing/technical/account and low/medium/high/critical classification |
| Agentic loop | Inspectable LangGraph with Perceive → Classify → Risk → MCP → Decide → Act → Observe |
| Real MCP tool use | Separate FastMCP process over stdio; no in-process KB shortcut |
| Safe routing | Deterministic guardrails override model output |
| Batch output | Six-case guided demo, full 36-ticket batch, and validated CSV import for up to 50 tickets |
| Explainability | Node trace, rule codes, timestamps, latency, MCP request/response, KB citation |
| Dashboard | Queue, filters, details, KPIs, human controls, downloads, evaluation, architecture |
| Refusal case | Angry payment failure and account takeover are visibly escalated |

## Architecture

```text
Ticket
  → perceive
  → classify
  → risk_guard
  → kb_search_mcp ──stdio──> FastMCP server ──> 15 FAQ articles
  → decide
      ├─ AUTO_RESOLVE → draft_resolution
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

No hosted-model key is required for the reliable deterministic demo. For a hosted run, set a valid key in `.env`; secrets are ignored by git.

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

Open **Import a ticket batch from CSV** in the Ticket triage view. Download either the empty template or the editable 45-ticket realistic example, replace the examples with your own requests, and upload it. Required columns are `subject` and `body`; `ticket_id` and `customer_id` are optional. The app validates the file, rejects duplicate IDs, limits each run to 50 tickets, and routes every row through the real graph and MCP workflow.

Choose **Reliable demo classifier** for repeatable offline results or **Configured hosted model** to use valid provider settings from `.env`. The selected mode is explicit; hosted failures are shown and safely escalated.

## Demo in 60 seconds

1. Select **Guided demo · 6 tickets** and click **Load sample batch**.
2. Open T-001 and show `AUTO_RESOLVE`, KB-001, its score, and the MCP request.
3. Open T-002 and show `ESCALATE` despite relevant KB evidence because high urgency, anger, and payment-risk controls override it.
4. Switch to **Quality check** and run the 15-case golden evaluation.
5. Highlight 100% high-risk recall and zero unsafe auto-resolutions.

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

- 22 tests pass, including hosted-output repair, 45-ticket CSV validation, duplicate-ID checks, SLA targets, rendered Streamlit interactions, compact detail cards, and explicit contrast safeguards.
- Golden deterministic + real MCP run: 15/15 category and route matches.
- High-risk recall: 100%.
- Unsafe auto-resolves: 0.
- Live Streamlit startup: verified on `127.0.0.1:8504`.

See [TEST_REPORT.md](TEST_REPORT.md) for commands and honest limitations.
