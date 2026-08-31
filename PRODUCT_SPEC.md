# Product Specification

## Product

**ResolveFlow AI — Safe, explainable first-line support triage**

## Problem

Support operations teams burn time reading repetitive requests, estimating urgency, and routing risky issues. The product must reduce repetitive work without compromising safety: routine FAQs should be handled quickly, while security, billing, refund, or angry/urgent tickets must be escalated instead of auto-answered.

## User stories

1. As a support lead, I want each ticket to be classified into billing, technical, or account so I can quickly assess the type of issue.
2. As an ops analyst, I want urgency and risk signals to be displayed clearly so I can prioritize the queue correctly.
3. As a triage agent, I want a real knowledge-base lookup through MCP to ground the decision and show evidence.
4. As a team member, I want safe route selection of AUTO_RESOLVE, CLARIFY, or ESCALATE without hidden chain-of-thought.
5. As a reviewer, I want an inspectable graph trace showing Perceive → Reason → Act → Observe for every ticket.
6. As a judge, I want strong KPI and evaluation outputs proving better throughput without unsafe auto-resolution.

## MVP

For each ticket, the system must produce:

- category: billing, technical, or account
- urgency: low, medium, high, or critical
- recommended queue or owner
- KB result obtained through a real MCP server
- route: AUTO_RESOLVE, CLARIFY, or ESCALATE
- draft response or escalation note
- concise decision evidence and trace

## Safety policy

Never auto-resolve when any of the following is true:

- high or critical urgency
- anger or threat language
- security or fraud risk
- payment failure
- refund or legal risk
- missing required facts
- low KB confidence or score below threshold
- LLM parse failure
- MCP failure
- any blocking rule triggered by the deterministic guardrail layer

In all unsafe cases, the system must default to CLARIFY or ESCALATE, never AUTO_RESOLVE.

## Provider behavior

The judged dashboard path always uses the OpenAI provider and model configured in `.env`; its output is schema-validated. The deterministic provider is limited to automated tests. A hosted failure is recorded and escalated, never silently presented as a successful fallback result.

```env
OPENAI_API_KEY=your_api_key_here
OPENAI_MODEL=gpt-4.1-mini
KB_THRESHOLD=0.55
```

The app must never hardcode secrets in source files.

## Non-goals

- live Zendesk/Gmail/Freshdesk integration
- production authentication, SSO, or multi-tenant deployment
- hosted multi-instance vector-database infrastructure
- multilingual support beyond the demo scope
- autonomous sending to customers without human review
- extended CRM workflows beyond demo ticket triage

## Exact acceptance criteria

1. The repository contains a runnable Python project that can triage one or more fixture tickets from the command line.
2. The workflow is a LangGraph state machine: START → perceive → classify → risk_guard → kb_search_mcp → decide → draft_resolution → validate_grounding / draft_clarification / create_escalation → observe → END.
3. The KB lookup happens through a real MCP server/client boundary over stdio, not by directly importing a local function.
4. Semantic retrieval uses OpenAI dense embeddings in persistent Qdrant, fused with BM25 and transparently reranked.
5. An automatic draft must use only retrieved evidence and pass citation/grounding validation; otherwise it is withheld for human review.
6. The UI displays the ticket queue, detail pane, and node-by-node trace that shows Perceive → Reason → Act → Observe evidence without exposing hidden chain-of-thought.
7. The system demonstrates at least one safe AUTO_RESOLVE, one CLARIFY, and one ESCALATE case using fixture tickets.
8. The deterministic guardrail layer overrides the model on all blocked scenarios.
9. The evaluation page or CLI output includes at least 15 labelled golden cases and a route/category comparison.
10. The implementation passes pytest smoke tests and a CLI smoke run for the three core demo tickets.
11. The repo contains `.env.example` and a safe `.gitignore` with no secrets committed.
12. The project has a clean local run path using Python 3.11+, a dependency manifest, and a documented command to launch the app and tests.
13. Sample batches and evaluation use a protected knowledge corpus that cannot be changed by operational knowledge uploads.

## Golden demo cases

| Case | Scenario | Expected route | Why |
|---|---|---|---|
| G1 | Routine password reset FAQ with strong KB match | AUTO_RESOLVE | Clear issue, low risk, high KB confidence |
| G2 | Billing question missing invoice number | CLARIFY | Insufficient required fact |
| G3 | Angry customer reports payment failure and threatens chargeback | ESCALATE | Angry + payment failure + possible refund/legal risk |
| G4 | Account takeover suspicion with password reset and login anomalies | ESCALATE | Security/fraud risk |
| G5 | Technical FAQ with a concrete fix in KB | AUTO_RESOLVE | Safe, low-risk match |
| G6 | Out-of-scope or low-confidence ticket | ESCALATE | KB score below threshold or missing confidence |

## Demo narrative

The strongest judge-facing story is the contrast between a routine ticket resolved by strong MCP evidence and an urgent risky ticket safely escalated despite a superficially relevant FAQ match. This demonstrates safety, tool use, and explainability without exposing hidden reasoning.
