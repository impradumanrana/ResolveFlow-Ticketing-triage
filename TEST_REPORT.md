# Test Report

## Final verification

Date: 31 August 2026  
Environment: Python 3.14.5 (project supports Python 3.11+)  
Reported evaluation mode: Deterministic Provider + real LangGraph + real MCP stdio

## Results

| Check | Command | Result |
|---|---|---|
| Full automated suite | `.venv/bin/pytest -q` | PASS — 22 passed in 16.06s |
| Golden evaluation | `python -m app.eval` | PASS — 15 cases |
| Category accuracy | golden evaluation | 100% |
| Route accuracy | golden evaluation | 100% |
| High-risk recall | golden evaluation | 100% |
| Unsafe auto-resolves | golden evaluation | 0 |
| Route distribution | golden evaluation | 7 auto-resolve, 2 clarify, 6 escalate |
| Average measured latency | golden evaluation | 649 ms/ticket on this machine |
| CLI three-route smoke | `LLM_PROVIDER=demo python -m app.cli` | PASS — auto-resolve, escalate, clarify; MCP connected on all |
| Streamlit rendered interaction | `pytest -q tests/test_dashboard.py` | PASS — 1 test in 6.34s |
| Streamlit startup | `streamlit run app/dashboard.py --server.headless true --server.address 127.0.0.1 --server.port 8504` | PASS — server listening |
| Streamlit health | `curl .../\_stcore/health` | PASS — `ok` |
| Make targets | `make -n setup run mcp test eval` | PASS — commands resolve |
| Secret-pattern scan | source scan excluding local `.env` and virtualenv | PASS — no key-shaped secret found |
| Git status | `git status --short --branch` | NOT AVAILABLE — directory is not a git repository |

## Automated coverage

- typed models and import smoke
- one bounded hosted-output repair attempt
- every blocking safety rule
- false-positive regression for “issue” vs “sue”
- real MCP search against the full FAQ fixture
- KB threshold at and immediately below the boundary
- safe auto-resolve route
- missing-information clarification route
- high-risk escalation route
- provider/schema failure → `MODEL_ERROR` → escalation
- MCP outage → `MCP_UNAVAILABLE` → escalation
- prompt-injection resistance
- KB citation in auto-resolution
- exact successful graph trace including Observe
- 15-case golden accuracy and safety metrics
- Streamlit empty state
- explicit light theme and component-level contrast rules
- plain-language navigation and action labels
- six-case batch and KPI totals
- selectable full 36-ticket sample batch
- CSV batch parsing, required-column validation, and 50-ticket limit
- 45-ticket example CSV, duplicate-ID rejection, and SLA target mapping
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

The published metrics are not from a hosted model. They are deliberately labelled as the deterministic provider with the real MCP and LangGraph runtime. A hosted OpenAI adapter exists, but no real-provider quality or availability claim is made in this report.

## Known limitations

- No persistence beyond Streamlit session state.
- No production auth or live helpdesk integration, by hackathon scope.
- Gemini/Groq/Ollama environment fields are placeholders; only deterministic and OpenAI providers are implemented.
- Each MCP lookup launches a short-lived stdio server process, prioritizing visible isolation and demo simplicity over throughput.
