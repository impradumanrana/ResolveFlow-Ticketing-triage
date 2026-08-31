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

The reliable judged path uses the deterministic provider so the demo and evaluation are repeatable without network credentials. An OpenAI adapter is available for optional hosted classification and its output is schema-validated. A hosted failure must be recorded and escalated, never silently presented as a successful fallback model result.

```env
LLM_PROVIDER=openai
OPENAI_API_KEY=your_api_key_here
OPENAI_MODEL=gpt-4.1-mini
GEMINI_API_KEY=
GROQ_API_KEY=
OLLAMA_BASE_URL=http://localhost:11434
OLLAMA_MODEL=llama3.2:3b
```

Gemini, Groq, and Ollama fields are reserved configuration points, not implemented adapters. The app must never hardcode secrets in source files.

## Non-goals

- live Zendesk/Gmail/Freshdesk integration
- production authentication, SSO, or multi-tenant deployment
- vector database or embeddings service setup
- multilingual support beyond the demo scope
- autonomous sending to customers without human review
- extended CRM workflows beyond demo ticket triage

## Exact acceptance criteria

1. The repository contains a runnable Python project that can triage one or more fixture tickets from the command line.
2. The workflow is a LangGraph state machine with nodes and edges matching the required path: START → perceive → classify → risk_guard → kb_search_mcp → decide → draft_resolution / draft_clarification / create_escalation → observe → END.
3. The KB lookup happens through a real MCP server/client boundary over stdio, not by directly importing a local function.
4. The UI displays the ticket queue, detail pane, and node-by-node trace that shows Perceive → Reason → Act → Observe evidence without exposing hidden chain-of-thought.
5. The system demonstrates at least one safe AUTO_RESOLVE, one CLARIFY, and one ESCALATE case using fixture tickets.
6. The deterministic guardrail layer overrides the model on all blocked scenarios.
7. The evaluation page or CLI output includes at least 15 labelled golden cases and a route/category comparison.
8. The implementation passes pytest smoke tests and a CLI smoke run for the three core demo tickets.
9. The repo contains `.env.example` and a safe `.gitignore` with no secrets committed.
10. The project has a clean local run path using Python 3.11+, a dependency manifest, and a documented command to launch the app and tests.

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
