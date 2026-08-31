# ResolveFlow AI Demo Script

## 30-second opening

Support teams lose time sorting repetitive requests while payment failures and account-security incidents can disappear in the same queue. ResolveFlow AI automates first-line triage with an inspectable LangGraph workflow, a real MCP knowledge-base lookup, and deterministic safety controls that stop risky tickets from being auto-resolved.

## Three-minute demo

1. Open **Ticket triage**, select **Guided demo · 6 tickets**, and click **Load sample batch**.
2. Point to the KPI row: 6 triaged, 2 auto-resolved, 3 human review, 2 SLA risk, and 8 estimated minutes saved.
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
8. Switch to **Quality check**, click **Run accuracy check**, and show route/category accuracy, high-risk recall, zero unsafe auto-resolves, route distribution, and expected-vs-actual rows.
9. Switch to **How it works** and finish on the graph and real MCP process boundary.

## What to say at the MCP trace

“This is not a local helper disguised as a tool. The graph launches a separate FastMCP server process, opens an MCP client session over stdio, and calls `search_knowledge_base`. The route is grounded in the returned article and score shown here.”

## What to say about safe escalation

“The model cannot overrule the safety policy. High urgency, anger, security risk, payment failure, legal/refund risk, missing information, weak evidence, or any system failure blocks auto-resolution. We show recorded facts and rule codes, not private chain-of-thought.”

## Architecture in 30 seconds

“A ticket moves through Perceive, structured classification, deterministic risk checks, a real MCP KB search, route decision, one of three actions, and Observe. Each node writes a timestamped evidence event. The result includes the queue, grounded draft or handoff, citation, controls, MCP health, and complete trace.”

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

No. They are clearly labelled as the deterministic provider plus the real MCP and LangGraph runtime. No hosted-run metrics are fabricated.

## Backup plan

If internet or provider credentials fail, keep the deterministic demo provider active. The LangGraph execution and real MCP calls remain unchanged. If MCP itself fails, show the safe `MCP_UNAVAILABLE` escalation test and trace.
