# Demo Script

## 30-second opening

Support teams lose time sorting repetitive requests, while payment failures and account-security incidents disappear into the same queue. ResolveFlow AI automates first-line triage with an inspectable LangGraph workflow, a real MCP knowledge-base lookup, and deterministic safety controls that stop risky tickets from being auto-resolved.

## Three-minute walkthrough

1. Open **Ticket triage**, select **Guided demo · 6 tickets**, click **Load sample batch**.
2. Point at the KPI row: 6 triaged, 2 grounded answer drafts, 1 clarification, 3 human reviews, ~8 estimated minutes saved. The urgent-ticket count is classified live and may vary without changing the safe routes.
3. Open **T-001**.
   - Show `AUTO_RESOLVE`, technical/low, and the cited KB-001 response.
   - Expand the `kb_search_mcp` trace step.
   - Show `search_knowledge_base`, the stdio transport, the request, the ranked response, the score, and the duration.
4. Open **T-003** — show `CLARIFY` and the specific order/invoice/purchase-email fact being requested.
5. Open **T-002** — the angry duplicate-charge message.
   - Highlight `HIGH_URGENCY`, `ANGRY_CUSTOMER`, `PAYMENT_FAILURE`.
   - Make the point: the relevant article match does **not** override safety. The ticket stays in human review.
6. Briefly show **T-004** (account takeover) and **T-020** (prompt-injection refusal).
7. Demonstrate a human override and the resulting session audit record.
8. Switch to **Quality check** and click **Run check on these N articles** — the button names the current article count. Show the seven readiness gates, route distribution, ticket outcomes, and stored-article retrieval evidence. Say plainly that this measures the knowledge base currently in service, not the protected demo corpus.
9. Finish on **How it works** — the graph and the real MCP process boundary.

## What to say at the MCP trace

> "This is not a local helper disguised as a tool. The graph launches a separate FastMCP server over stdio and calls `search_knowledge_base`. OpenAI embeds the query, Qdrant runs dense search, BM25 adds exact-term evidence, and the MCP response shows the transparent rerank scores."

## What to say about safe escalation

> "The model cannot overrule the safety policy. High urgency, anger, security risk, payment failure, legal or refund risk, missing information, weak evidence, or any system failure blocks auto-resolution. We show recorded facts and rule codes — not private chain-of-thought."

## Architecture in 30 seconds

> "A ticket moves through perceive, OpenAI classification, deterministic risk checks, a real MCP hybrid search, the route decision, grounded generation, citation validation, and observe. Each node writes a timestamped evidence event. Invalid grounding is withheld and sent to human review."

## Metric statement

The verified deterministic-provider plus real-MCP golden run: 15 labelled cases, 100% category accuracy, 100% route accuracy, 100% high-risk recall, zero unsafe auto-resolutions. Time saved is an explicitly labelled estimate of four minutes per safely auto-resolved ticket.

## Likely judge questions

**Why not auto-resolve every article match?**
Because relevance is necessary but not sufficient. Deterministic risk controls always take precedence.

**Is the MCP boundary real?**
Yes. The client launches a distinct server process over stdio. The trace shows the request, the returned matches, the score, and the latency.

**What happens if MCP or the model fails?**
The graph records `MCP_UNAVAILABLE` or `MODEL_ERROR` and routes to human review.

**Are responses sent automatically?**
No. `AUTO_RESOLVE` means a grounded, review-ready draft. Autonomous customer contact is deliberately disabled.

**Are the evaluation numbers from a hosted model?**
Quality check runs live on the configured OpenAI model through the real MCP and LangGraph runtime, against whichever knowledge base is ingested. The automated suite uses deterministic fixtures for repeatability. The two are labelled separately — see [EVALUATION.md](EVALUATION.md).

## Backup plan

If the network or credentials fail, use screenshots or a recorded run; the product intentionally fails closed rather than silently switching models. If MCP itself fails, show the tested `MCP_UNAVAILABLE` escalation path and note that customer sending stays disabled.

## Pre-demo checklist

- [ ] `.env` holds a valid `OPENAI_API_KEY`
- [ ] `pytest -q` passes (43 tests)
- [ ] `streamlit run app/dashboard.py` starts and the sidebar shows **OpenAI active**
- [ ] The sidebar reports a non-zero **Support knowledge base** article count
- [ ] **Guided demo · 6 tickets** is the selected sample batch
