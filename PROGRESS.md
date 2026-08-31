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
- Added an editable 45-ticket realistic CSV, empty template, duplicate-ID protection, privacy guidance, SLA response targets, and priority-first queue sorting.
- Fixed the sidebar Technical details expander with explicit dark backgrounds and readable text for collapsed, expanded, and hover states.
- Added an explicit processing-mode selector so deterministic demo runs and configured hosted-model runs cannot be confused.
- Replaced user-facing `KB` abbreviations with “Support knowledge base,” “Help article,” and “Article match”; raw MCP terms remain only in the technical evidence areas.
- Replaced weak tests with coverage for every blocking rule, threshold boundary, provider failure, MCP outage, injection resistance, citation, exact trace, golden metrics, and rendered UI interactions.
- Added verified Makefile targets.
- Updated README, architecture, demo, progress, and test evidence to match actual behavior.

## Measured result

- Tests: 22 passed.
- Golden labelled cases: 15.
- Category accuracy: 100%.
- Route accuracy: 100%.
- High-risk recall: 100%.
- Unsafe auto-resolutions: 0.
- Curated dashboard batch: 6 tickets → 2 auto-resolve, 1 clarify, 3 escalate.

## Honest limitations

- The in-app browser automation connection was unavailable in this session, so screenshot-based visual QA could not be completed.
- Streamlit's rendered-app harness verified navigation and interactions, and the live local server started successfully without runtime errors.
- A real hosted OpenAI run was not used for the reported metrics; the verified evaluation is explicitly the deterministic provider plus real MCP stdio.
- Gemini/Groq/Ollama adapters are configuration placeholders, not implemented providers.
- This directory is not a git repository, so no commit or tracked-secret status is claimed. The local `.gitignore` excludes `.env`.

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
