# Progress

## Final status — 31 August 2026

The original hackathon brief and every root Markdown file were reviewed. Playbook Prompts 0–6 were re-audited against the actual repository, and the implementation was rebuilt where the prior prototype did not match its documentation.

## Completed

- Preserved and hardened the real LangGraph workflow.
- Preserved the real FastMCP client/server stdio process boundary.
- Connected the MCP server to all 15 FAQ fixtures instead of three hardcoded articles.
- Replaced fragile substring retrieval and guardrail behavior with deterministic phrase/token/fuzzy matching.
- Added explicit safe failure handling for provider errors, schema errors, weak evidence, and MCP outages.
- Added correlation IDs, measured node durations, queue, MCP status, processing latency, citations, and the Observe event to results.
- Rebuilt the Streamlit UI as an operations dashboard with queue/search/filters, KPIs, details, trace, MCP evidence, human override/audit, downloads, Evaluation, and Architecture views.
- Fixed dark-theme text leaking into light cards and forms; added explicit high-contrast metric, label, input, select, alert, and button styles plus clearer plain-language navigation and onboarding.
- Added the ordered six-case demo and 36-ticket / 15-golden fixture set.
- Added selectable 6- and 36-ticket sample batches, CSV import with validation/template, operational source and status columns, and compact ticket/MCP evidence cards.
- Moved sample loading, manual ticket entry, and CSV import into three prominent start tabs above KPIs and the queue.
- Added an editable 45-ticket realistic CSV, empty template, duplicate-ID protection, privacy guidance, SLA response targets, and priority-first queue sorting.
- Fixed the sidebar Technical details expander with explicit dark backgrounds and readable text for collapsed, expanded, and hover states.
- Rebuilt the How it works view as an end-to-end product walkthrough covering batch inputs, processing modes, graph stages, MCP/stdio, guardrails, route outcomes, SLA targets, human review, outputs, and trace evidence.
- Added a Support knowledge workspace with article browsing/export, manual creation, CSV import, separate custom persistence, and verified MCP ingestion of built-in plus user-added articles.
- Upgraded RAG storage to persistent SQLite plus dedicated Qdrant, using OpenAI dense embeddings, BM25 hybrid retrieval, transparent reranking, evidence-only generation, and fail-closed citation/grounding validation.
- Added append-or-replace ingestion, one confirmed clear-all action, and a separate restore-starter control.
- Replaced unstable knowledge expanders with a persistent action selector so document imports remain in context after reruns; added explicit high-contrast text-input borders.
- Added PDF, Markdown, and text ingestion with chunk preview plus an in-product real MCP search tester.
- Removed the user-facing processing-mode selector; dashboard and CLI now always use configured OpenAI and fail safely without a silent classifier fallback.
- Rebuilt Quality Check around seven live readiness gates: classification, routing, safety recall, unsafe automation, knowledge retrieval, grounded drafts, and MCP trace completeness.
- Added separate local e-commerce test resources with 25 knowledge articles, Markdown guidance, a reusable PDF/MD/CSV generation prompt, and 50 realistic support tickets; these are intentionally not exposed in the product UI.
- Added explainable retrieval component scores to each MCP match and realistic paraphrase-quality tests.
- Replaced user-facing `KB` abbreviations with “Support knowledge base,” “Help article,” and “Article match”; raw MCP terms remain only in the technical evidence areas.
- Replaced weak tests with coverage for every blocking rule, threshold boundary, provider failure, MCP outage, injection resistance, citation, exact trace, golden metrics, and rendered UI interactions.
- Added verified Makefile targets.
- Updated README, architecture, demo, progress, and test evidence to match actual behavior.

## Measured result

- Tests: 41 passed.
- Golden labelled cases: 15.
- Category accuracy: 100%.
- Route accuracy: 100%.
- High-risk recall: 100%.
- Unsafe auto-resolutions: 0.
- Paraphrase retrieval benchmark: previous scorer 5/9 top-1; new hybrid scorer 9/9 top-1.
- Curated dashboard batch: 6 tickets → 2 auto-resolve, 1 clarify, 3 escalate.
- Live existing knowledge migration: 57/57 articles embedded with OpenAI and indexed in Qdrant.
- Live full RAG verification: safe password-reset ticket auto-resolved with a real retrieved article citation and 2/2 support claims validated.
- Protected live six-ticket demo: 6/6 expected routes; 2 grounded drafts, 1 clarification, 3 escalations, and zero unsafe auto-resolutions regardless of user knowledge changes.

## Honest limitations

- The in-app browser automation connection was unavailable in this session, so screenshot-based visual QA could not be completed.
- Streamlit's rendered-app harness verified navigation and interactions, and the live local server started successfully without runtime errors.
- A live end-to-end OpenAI/MCP/Qdrant/validator smoke passed; use the Quality check page to run the longer live evaluation against whichever corpus is currently ingested.
- Qdrant is running in embedded local persistent mode for this hackathon build; a hosted Qdrant service is the natural multi-instance production upgrade.

## Commands

```bash
source .venv/bin/activate
pytest -q
python -m app.eval
python -m app.cli
streamlit run app/dashboard.py
```

Or:

```bash
make test
make eval
make run
```

## Demo priority

Show T-001 first: a routine password reset grounded by KB-001 through MCP. Then show T-002: a relevant billing match is present, but high urgency, anger, and payment risk override it and force human review.
