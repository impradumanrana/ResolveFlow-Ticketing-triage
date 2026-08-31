# Product Specification

## Product

**ResolveFlow AI — safe, explainable first-line support triage.**

## Problem

Support teams burn time reading repetitive requests, estimating urgency, and routing risky issues. Meanwhile payment failures and account-security incidents sit in the same queue as password resets. The product must cut repetitive work *without* trading away safety: routine FAQs get handled fast, while security, billing, refund, and angry or urgent tickets are escalated rather than auto-answered.

## User stories

1. As a support lead, I want each ticket classified as billing, technical, or account so I can assess issue type at a glance.
2. As an ops analyst, I want urgency and risk signals shown clearly so I can prioritize the queue.
3. As a triage agent, I want a real knowledge-base lookup through MCP to ground the decision and show its evidence.
4. As a team member, I want safe route selection across `AUTO_RESOLVE`, `CLARIFY`, and `ESCALATE` with no hidden chain-of-thought.
5. As a reviewer, I want an inspectable graph trace for every ticket.
6. As a judge, I want KPI and evaluation output proving better throughput with zero unsafe auto-resolution.

## MVP output

For each ticket the system produces a category, an urgency, a recommended queue or owner, a knowledge result obtained through a real MCP server, a route, a draft response or escalation note, and concise decision evidence with the full trace.

## Safety policy

Never auto-resolve when any of the following holds:

- high or critical urgency
- anger or threat language
- security or fraud risk
- payment failure
- refund or legal risk
- a missing required fact
- low classification confidence
- an article score below threshold
- model parse or provider failure
- MCP failure
- any blocking deterministic guardrail

In every unsafe case the system routes to `CLARIFY` or `ESCALATE`. It never guesses when policy says no, and it never contacts a customer autonomously.

## Configuration

```env
OPENAI_API_KEY=your_api_key_here
OPENAI_MODEL=gpt-4.1-mini
OPENAI_EMBEDDING_MODEL=text-embedding-3-small
OPENAI_EMBEDDING_DIMENSIONS=1536
QDRANT_COLLECTION=resolveflow_knowledge
KB_THRESHOLD=0.55
```

Secrets are never hardcoded in source and `.env` is git-ignored.

## Non-goals

Live Zendesk/Gmail/Freshdesk integration · production auth, SSO, or multi-tenancy · hosted multi-instance vector infrastructure · multilingual scope beyond the demo · autonomous customer sending · extended CRM workflows.

## Acceptance criteria

1. A runnable Python project triages fixture tickets from the command line.
2. The workflow is a LangGraph state machine: `START → perceive → classify → risk_guard → kb_search_mcp → decide → draft_resolution → validate_grounding` / `draft_clarification` / `create_escalation` `→ observe → END`.
3. The knowledge lookup crosses a real MCP server/client boundary over stdio, not a direct local function call.
4. Semantic retrieval uses OpenAI dense embeddings in persistent Qdrant, fused with BM25 and transparently reranked.
5. An automatic draft uses only retrieved evidence and must pass citation/grounding validation, or it is withheld.
6. The UI shows the queue, a detail pane, and a node-by-node trace — evidence, not chain-of-thought.
7. At least one safe `AUTO_RESOLVE`, one `CLARIFY`, and one `ESCALATE` are demonstrated from fixtures.
8. The deterministic guardrail layer overrides the model on every blocked scenario.
9. Evaluation covers at least 15 labelled golden cases with a route/category comparison.
10. pytest and a CLI smoke run pass for the three core demo tickets.
11. `.env.example` and a safe `.gitignore` exist, with no secrets committed.
12. A clean local run path exists on Python 3.11+ with a dependency manifest and documented commands.
13. Sample batches use a protected corpus that operational knowledge uploads cannot change.

## Golden demo cases

| Case | Scenario | Expected route | Why |
|---|---|---|---|
| G1 | Routine password reset with a strong article match | `AUTO_RESOLVE` | Clear issue, low risk, high confidence |
| G2 | Billing question missing an invoice number | `CLARIFY` | A specific required fact is absent |
| G3 | Angry customer, payment failure, chargeback threat | `ESCALATE` | Anger + payment failure + legal risk |
| G4 | Account-takeover suspicion with login anomalies | `ESCALATE` | Security and fraud risk |
| G5 | Technical FAQ with a concrete documented fix | `AUTO_RESOLVE` | Safe, low-risk match |
| G6 | Out-of-scope or low-confidence ticket | `ESCALATE` | Score below threshold |

## Demo narrative

The strongest story for a judge is the contrast between a routine ticket resolved on strong MCP evidence and an urgent, risky ticket safely escalated *despite* a superficially relevant article match. That single pairing demonstrates safety, real tool use, and explainability at once.
