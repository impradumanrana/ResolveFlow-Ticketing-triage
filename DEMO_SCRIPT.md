# ResolveFlow AI Demo Script

## 30-second opening

Support teams lose time sorting repetitive requests while payment failures and account-security incidents can disappear in the same queue. ResolveFlow AI automates first-line triage with an inspectable LangGraph workflow, a real MCP knowledge-base lookup, and deterministic safety controls that stop risky tickets from being auto-resolved.

## Three-minute demo

1. Open **Ticket triage**, select **Guided demo · 6 tickets**, and click **Load sample batch**.
2. Point to the KPI row: 6 triaged, 2 grounded answer drafts, 1 clarification, 3 human reviews, and 8 estimated minutes saved. Urgent-ticket count is classified live and may vary without changing the safe routes.
3. Open T-001.
   - Show `AUTO_RESOLVE`, technical/low, and the cited KB-001 response.
   - Open the `kb_search_mcp` trace step.
   - Show `search_knowledge_base`, stdio transport, request, ranked response, score, and duration.
4. Open T-003.
   - Show `CLARIFY` and the requested order/invoice/purchase-email fact.
5. Open T-002.
   - Show the angry duplicate-charge message.
   - Highlight `HIGH_URGENCY`, `ANGRY_CUSTOMER`, and `PAYMENT_FAILURE`.
   - Explain that the relevant KB result does not override safety; the ticket remains in human review.
6. Briefly show T-004 account takeover and T-020 prompt-injection refusal.
7. Demonstrate a human override and the resulting session audit record.
8. Switch to **Quality check**, click **Run live OpenAI quality check**, and show all seven readiness gates, route distribution, ticket outcomes, and paraphrase retrieval evidence.
9. Switch to **How it works** and finish on the graph and real MCP process boundary.

## What to say at the MCP trace

“This is not a local helper disguised as a tool. The graph launches a separate FastMCP server over stdio and calls `search_knowledge_base`. OpenAI embeds the query, Qdrant performs dense search, BM25 adds exact-term evidence, and the MCP response shows the transparent rerank scores.”

## What to say about safe escalation

“The model cannot overrule the safety policy. High urgency, anger, security risk, payment failure, legal/refund risk, missing information, weak evidence, or any system failure blocks auto-resolution. We show recorded facts and rule codes, not private chain-of-thought.”

## Architecture in 30 seconds

“A ticket moves through Perceive, OpenAI classification, deterministic risk checks, a real MCP hybrid search, route decision, grounded generation, citation validation, and Observe. Each node writes a timestamped evidence event. Invalid grounding is withheld and sent to human review.”

## Metric statement

The verified deterministic-provider + real-MCP golden run has:

- 15 labelled cases
- 100% category accuracy
- 100% route accuracy
- 100% high-risk recall
- zero unsafe auto-resolutions

Time saved is an explicitly labelled estimate of four minutes per safely auto-resolved ticket.

## Likely judge questions

### Why not auto-resolve every KB match?

Because KB relevance is necessary but not sufficient. Deterministic risk controls always have higher precedence.

### Is MCP real?

Yes. The client launches a distinct server process over stdio; the trace shows the request, returned matches, score, and latency.

### What happens if MCP or the model fails?

The graph records `MCP_UNAVAILABLE` or `MODEL_ERROR` and routes to human review.

### Are responses sent automatically?

No. `AUTO_RESOLVE` means a grounded, review-ready draft. Autonomous customer contact is intentionally disabled.

### Are the evaluation numbers from a hosted model?

Yes. The Quality Check metrics are generated live using the configured OpenAI model plus the real MCP and LangGraph runtime. The automated unit suite still uses explicit deterministic fixtures for repeatability, and the two result types are labelled separately.

## Backup plan

If internet or provider credentials fail, use the screenshots or recorded backup demo; the product intentionally fails closed instead of silently switching models. If MCP itself fails, show the tested `MCP_UNAVAILABLE` escalation path and explain that customer sending remains disabled.
