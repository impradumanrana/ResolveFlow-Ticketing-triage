# Test Report

## Final verification

Date: 31 August 2026  
Environment: Python 3.14.5 (project supports Python 3.11+)  
Live smoke mode: OpenAI `gpt-5.6-luna` + OpenAI `text-embedding-3-small` + real LangGraph/MCP/Qdrant

Automated evaluation mode: explicit deterministic test provider/embeddings + real LangGraph/MCP/Qdrant

## Results

| Check | Command | Result |
|---|---|---|
| Full automated suite | `.venv/bin/python -m pytest -q` | PASS — 41 passed in 54.17s |
| Golden evaluation | `python -m app.eval` | PASS — 15 cases |
| Category accuracy | automated golden evaluation | 100% |
| Route accuracy | automated golden evaluation | 100% |
| High-risk recall | automated golden evaluation | 100% |
| Unsafe auto-resolves | automated golden evaluation | 0 |
| Knowledge top-1 retrieval | automated Qdrant hybrid evaluation | 100% — 9/9 paraphrases |
| Grounded draft rate | automated golden evaluation | 100% |
| MCP trace completeness | automated golden evaluation | 100% |
| Route distribution | golden evaluation | 7 auto-resolve, 2 clarify, 6 escalate |
| Paraphrase retrieval comparison | 9 realistic queries | old scorer 5/9 top-1; hybrid scorer 9/9 top-1 |
| E-commerce retrieval | isolated 25-article store | PASS — 10/10 top-1 and 10/10 above safety threshold |
| Live RAG storage migration | existing 57-article corpus | PASS — 1536-dimensional OpenAI embeddings indexed in persistent Qdrant |
| Live end-to-end grounded ticket | OpenAI → guardrails → MCP → Qdrant/BM25 → rerank → OpenAI → validator | PASS — `AUTO_RESOLVE`, real citation, 2/2 claims verified |
| Protected live guided batch | configured OpenAI + isolated 15-article demo corpus | PASS — 6/6 expected routes; 2 drafts, 1 clarification, 3 escalations |
| Streamlit rendered interaction | `.venv/bin/python -m pytest -q tests/test_dashboard.py` | PASS — 5 tests in 11.26s |
| Streamlit startup | `streamlit run app/dashboard.py --server.headless true --server.address 127.0.0.1 --server.port 8504` | PASS — server listening |
| Streamlit health | `curl .../\_stcore/health` | PASS — `ok` |
| Make targets | `make -n setup run mcp test eval` | PASS — commands resolve |
| Secret-pattern scan | source scan excluding local `.env` and virtualenv | PASS — no key-shaped secret found |
| Release packaging | staged-file and ignore audit | PASS — source, docs, tests, and samples included; `.env`, databases, vectors, caches, and virtualenv excluded |

## Automated coverage

- typed models and import smoke
- one bounded hosted-output repair attempt
- every blocking safety rule
- false-positive regression for “issue” vs “sue”
- real MCP search against the full FAQ fixture
- SQLite initialization and migration of built-in and user-added knowledge
- persistent Qdrant collection creation, synchronization, clearing, replacement, and semantic query
- persistent add, clear-user, clear-all, replace-all, and restore operations
- nine realistic paraphrase retrieval cases with strong expected matches
- Markdown/text chunk ingestion and preview validation; PDF extraction uses the same parser path
- OpenAI dense + Qdrant/BM25 hybrid score components and reranked positions returned through MCP
- knowledge-base manual/CSV UI and CSV validation
- KB threshold at and immediately below the boundary
- safe auto-resolve route
- missing-information clarification route
- high-risk escalation route
- provider/schema failure → `MODEL_ERROR` → escalation
- MCP outage → `MCP_UNAVAILABLE` → escalation
- prompt-injection resistance
- KB citation in auto-resolution
- immutable short evidence-key mapping back to exact stored article IDs
- exact supporting-quote validation and adversarial unsupported-answer withholding
- paragraph-level citation enforcement (a partly cited draft is withheld)
- forgotten-password guardrail regression (not mistaken for missing billing information)
- exact successful graph trace including Observe
- 15-case golden accuracy and safety metrics
- Streamlit empty state
- explicit light theme and component-level contrast rules
- plain-language navigation and action labels
- six-case batch and KPI totals
- selectable full 36-ticket sample batch
- CSV batch parsing, required-column validation, and 50-ticket limit
- 45-ticket and 50-ticket example CSVs, duplicate-ID rejection, and SLA target mapping
- sidebar Technical details contrast in expanded and collapsed states
- compact ticket and MCP evidence cards (only the KPI row uses large metrics)
- operational source and status fields
- queue table and downloads
- ticket detail and trace
- human route override and audit event
- reset
- Evaluation and Architecture navigation

## UI acceptance

Streamlit's rendered-app testing harness loaded the actual dashboard and exercised the six-case batch, filters/widgets, details, downloads, human review control, audit log, reset, and navigation without exceptions. The live server also passed its health endpoint.

The in-app browser automation connection was unavailable during this session, so screenshot-based visual QA is not claimed. Visual design was implemented and render-tested, but should receive one quick human browser pass before presentation.

## Provider note

The live RAG checks above were freshly measured using configured OpenAI `gpt-5.6-luna` plus `text-embedding-3-small`. Accuracy percentages are from the current repeatable 41-test suite, which uses explicit deterministic provider/embedding fixtures while retaining the real graph, MCP subprocess, SQLite, and Qdrant paths. Normal dashboard and CLI operation do not expose or silently select those fixtures. Run **Quality check** for a fresh full live OpenAI evaluation against the protected evaluation corpus.

## Known limitations

- Ticket results and reviewer audit events remain session-scoped; knowledge articles persist in SQLite and dense vectors persist in Qdrant local mode.
- No production auth or live helpdesk integration, by hackathon scope.
- Each MCP lookup launches a short-lived stdio server process, prioritizing visible isolation and demo simplicity over throughput.
