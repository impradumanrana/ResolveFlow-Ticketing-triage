# Support Ticket Triage Agent — Hackathon Build Playbook

## The fastest credible build

Build a polished **Python + LangGraph + MCP + Streamlit** application called **ResolveFlow AI**.

The demo must prove four things visibly:

1. A ticket is classified into `billing`, `technical`, or `account` with urgency and confidence.
2. The workflow is a real state graph: **Perceive → Assess Risk → Search KB via MCP → Decide → Act → Observe**.
3. The knowledge-base lookup crosses a real MCP boundary and its request/response appears in the trace.
4. The agent safely auto-resolves strong matches, asks a clarifying question when information is missing, and escalates angry, risky, urgent, or low-confidence cases.

Do not spend hackathon time on Gmail, Zendesk, authentication, vector databases, Docker orchestration, or deployment until the local demo works.

## Recommended stack

| Layer | Choice | Reason |
|---|---|---|
| Language | Python 3.11+ | Fastest path for LangGraph, MCP, tests, and Streamlit |
| Agent graph | LangGraph | Direct match to judging brief; graph is inspectable |
| MCP | Official Python MCP SDK / FastMCP | Minimal real stdio MCP server |
| UI | Streamlit + custom CSS | Polished dashboard in hours, not days |
| State/data | JSON fixtures + in-memory session state | Reliable and demo-friendly |
| LLM | Gemini Flash free tier first; OpenAI small model fallback | Low-cost, fast structured output |
| KB search | Deterministic TF-IDF/fuzzy scoring inside MCP | Transparent confidence; no embedding setup risk |
| Tests | pytest | Fast guardrail and routing verification |

## Model decision

### Recommended for this hackathon: OpenAI

Use `gpt-4.1-mini` as the primary model for ticket classification, KB-grounded route selection, and structured JSON output. It is the best balance of reliability, schema adherence, and price for a support-triage workflow on a prepaid account. If budget permits and you want top-end quality, `gpt-4.1` is the next step; for a low-cost fallback, `gpt-4o-mini` is also viable but less ideal for structured decisions than `gpt-4.1-mini`.

### Strong fallback: Gemini

Use a current Gemini Flash model when OpenAI is rate-limited or unavailable. Keep the exact model ID in `.env` because availability varies by account and region.

### Additional fallback options

- **Groq:** fast and useful if the selected model consistently obeys JSON schemas.
- **NVIDIA NIM:** backup if a hosted endpoint is available and stable.
- **Ollama 3.2:3b:** local offline fallback only. It can classify simple tickets but is less reliable for multi-field JSON and tool decisions.

The app must have a provider adapter controlled by environment variables. The default priority should be OpenAI-first, with Gemini/Groq/Ollama as fallbacks:

```env
LLM_PROVIDER=openai
LLM_MODEL=gpt-4.1-mini
OPENAI_API_KEY=your_api_key_here
GEMINI_API_KEY=
GROQ_API_KEY=
OLLAMA_BASE_URL=http://localhost:11434
OLLAMA_MODEL=llama3.2:3b
```

No secret may be committed. Keep a root `.env` file locally, add a `.env.example` template, and ensure `.env` is ignored in git.

## Brownie-point features worth building

Build these because they directly reinforce the judging metrics:

- Three-way route: **Auto-resolve / Clarify / Human review**.
- Safety rules override the LLM: urgent, payment/security/refund-risk, angry, threats, or weak KB match can never auto-resolve.
- Human override controls with an audit entry.
- SLA countdown/risk badge and recommended queue/owner.
- Visible confidence breakdown: classification confidence, KB match score, final decision confidence.
- Expandable agent trace with node timestamps and MCP request/response.
- “Why this decision?” summary based only on recorded state—not hidden chain-of-thought.
- Before/after metrics: auto-resolution rate, escalation rate, estimated minutes saved, high-risk tickets caught.
- Demo mode with six curated golden tickets plus batch mode with 30–50 tickets.
- Failure mode: if the LLM or MCP server is unavailable, route safely to human review and display the failure.
- Download results as CSV and trace as JSON.
- Deterministic evaluation page showing expected vs actual route/category accuracy.

Avoid exposing private chain-of-thought. Show concise decision evidence, rules triggered, tool results, and state transitions.

## UI direction

Use a clean SaaS operations dashboard:

- Dark navy sidebar; warm off-white content background; indigo primary accent; red only for true risk.
- Top KPI cards: total, auto-resolved, needs human, SLA risk, time saved.
- Main queue table with search, category/urgency/route filters, confidence bars, status badges.
- Ticket detail drawer/page: original message, classification, proposed response, KB citation, decision evidence.
- Right-side vertical trace: Perceive, Risk Check, MCP Search, Decision, Action, Observe.
- Settings sidebar: provider/model, KB threshold, auto-resolve toggle, demo reset.
- Empty, loading, success, error, and MCP-offline states must look intentional.

Use a product name and subtitle consistently: **ResolveFlow AI — Safe, explainable first-line support triage**.

## Root-level continuity files

Every prompt below must read all existing root-level Markdown files before acting. The agent must maintain:

- `README.md` — setup, run commands, architecture summary, screenshots placeholder.
- `PRODUCT_SPEC.md` — frozen scope and acceptance criteria.
- `ARCHITECTURE.md` — graph, state schema, MCP boundary, safety rules.
- `DECISIONS.md` — dated decisions and tradeoffs.
- `PROGRESS.md` — done/current/next/blockers and exact commands.
- `TEST_REPORT.md` — test commands and latest results.
- `DEMO_SCRIPT.md` — 3-minute presentation and golden tickets.

At the end of every step, update `PROGRESS.md` and any affected document. Never replace accurate prior notes with vague summaries.

---

# Copy-paste prompts

Run these prompts in order in Codex from the project root. Use a fresh context only if necessary; the root Markdown files are the handoff memory.

## Prompt 0 — Inspect and freeze the plan (20 minutes)

```text
You are the lead engineer for a time-boxed hackathon project: “Support Ticket Triage Agent with MCP.” Work inside the current project; do not create a nested project folder.

FIRST:
1. Inspect the complete repository and read every Markdown file in the project root, including the hackathon brief.
2. Do not implement feature code in this step.
3. Identify existing code, runtime, package manager, conflicts, missing prerequisites, and the shortest safe path to a working demo.

Target product: ResolveFlow AI — a polished support operations dashboard that classifies tickets, assigns urgency, searches a mock KB through a REAL MCP server, then chooses AUTO_RESOLVE, CLARIFY, or ESCALATE using explicit safety guardrails. The workflow must be an inspectable LangGraph state machine and must show Perceive → Reason → Act → Observe evidence without exposing hidden chain-of-thought.

Preferred stack unless the repository already has a viable equivalent: Python 3.11+, LangGraph, official MCP Python SDK/FastMCP over stdio, Streamlit, pytest, JSON fixtures. Use a provider adapter for Gemini/OpenAI/Groq/Ollama selected via environment variables. No secrets in source.

Create or update these ROOT files:
- PRODUCT_SPEC.md: MVP, non-goals, user stories, exact acceptance criteria, golden demo cases.
- ARCHITECTURE.md: file layout, typed state, graph nodes/edges, MCP process boundary, provider interface, guardrails, failure paths.
- DECISIONS.md: dated architectural decisions and why.
- PROGRESS.md: repository findings, prerequisites, ordered implementation checklist, risks, exact next command.

Required graph:
START → perceive → classify → risk_guard → kb_search_mcp → decide → one of draft_resolution / draft_clarification / create_escalation → observe → END.

Hard policy: high/critical urgency, anger/threat, security/fraud, payment failure, refund/legal risk, missing required facts, LLM parse failure, MCP failure, or KB score below threshold must never auto-resolve.

Return: concise findings, files created, final architecture choice, blockers, and the exact next prompt/command. Stop after documentation and validation of the plan.
```

## Prompt 1 — Scaffold a runnable vertical slice (40 minutes)

```text
Continue ResolveFlow AI in the current project root.

Before changing anything, read README.md, PRODUCT_SPEC.md, ARCHITECTURE.md, DECISIONS.md, PROGRESS.md, TEST_REPORT.md, and DEMO_SCRIPT.md if they exist. Inspect the repository and preserve valid work. State briefly which documented constraints govern this step.

Implement the smallest runnable end-to-end vertical slice:
- clean Python package structure;
- dependency and run configuration;
- `.env.example` and safe `.gitignore`;
- typed Ticket, Classification, KBMatch, TraceEvent, and TriageResult models;
- provider interface plus one hosted provider selected from env;
- deterministic safe fallback on provider errors;
- minimal FastMCP stdio server exposing `search_knowledge_base(query, category, top_k)`;
- MCP client that launches/connects to the server and calls the tool across the MCP boundary;
- a LangGraph workflow with all documented nodes and conditional edges;
- one CLI command that triages one fixture ticket and prints the final structured result and trace.

Use structured output validation with one bounded repair attempt. Do not fake MCP by importing and calling the KB function directly. Add correlation IDs and duration to trace events. Never log secrets or hidden chain-of-thought.

Create a tiny KB and three tickets only for this slice: one safe auto-resolution, one angry urgent escalation, one clarification case.

Run the CLI against all three and fix failures. Update README.md, ARCHITECTURE.md, DECISIONS.md, PROGRESS.md, and TEST_REPORT.md in the root with exact commands and actual results.

Do not build the dashboard yet. Finish only when the vertical slice runs from a clean command.
```

## Prompt 2 — Make routing safe and accurate (45 minutes)

```text
Continue from the current project. First read every root-level Markdown file and inspect the existing implementation/tests. Do not rewrite working architecture.

Harden triage accuracy and safety:
- classification categories: billing, technical, account;
- urgency: low, medium, high, critical;
- routes: AUTO_RESOLVE, CLARIFY, ESCALATE;
- deterministic rule engine that can override LLM suggestions;
- calibrated KB similarity score and configurable threshold;
- explicit rule codes such as HIGH_URGENCY, ANGRY_CUSTOMER, SECURITY_RISK, PAYMENT_FAILURE, REFUND_OR_LEGAL, MISSING_INFORMATION, LOW_KB_CONFIDENCE, MCP_UNAVAILABLE, MODEL_ERROR;
- human-readable `decision_summary` composed from state facts and triggered rules;
- cited KB article ID/title in every proposed auto-resolution;
- no auto-resolution if any blocking rule fires.

Create 35–40 realistic, balanced sample tickets and 12–18 FAQ articles. Include adversarial/ambiguous cases, typos, conflicting cues, prompt-injection text inside tickets, angry customers, account security, billing failures, and routine FAQs. Add expected category/route labels to at least 15 golden cases.

Add pytest tests for graph transitions, MCP lookup, threshold boundaries, structured-output failure, MCP outage, injection resistance, and every blocking rule. Ensure tests can use a deterministic fake model while production/demo still uses the configured real provider.

Run all tests and the golden evaluation. Fix regressions. Update all affected root Markdown files, especially TEST_REPORT.md with actual counts/accuracy and PROGRESS.md with the next step.
```

## Prompt 3 — Build the polished dashboard (60–75 minutes)

```text
Continue ResolveFlow AI. First read every Markdown file in the root, inspect current code, run the existing smoke test, and preserve the proven graph/MCP boundary.

Build a polished Streamlit dashboard suitable for judges:
- branded header: “ResolveFlow AI” and “Safe, explainable first-line support triage”;
- responsive KPI row: total tickets, auto-resolved, human review, SLA risk, estimated minutes saved;
- searchable/filterable queue for route, category, urgency, and status;
- confidence indicators and clear status badges;
- single-ticket entry form and batch-demo button;
- ticket detail view showing original message, classification, urgency, queue, KB evidence, draft response/clarifying question/escalation note;
- a visible vertical agent trace with node, timestamp, duration, decision evidence, and MCP request/response summary;
- human controls: approve resolution, override route, assign queue, add note; record each as an audit event in session state;
- CSV result download and JSON trace download;
- provider/model and MCP connection health in the sidebar;
- intentional loading, empty, provider-error, and MCP-offline states.

Visual design: premium support-operations SaaS, navy/indigo/off-white palette, restrained shadows, excellent spacing, readable typography, accessible contrast. Red only for genuine danger. Add custom CSS sparingly and keep behavior stable.

Do not expose chain-of-thought; show concise state evidence and rule codes. Do not call the model again merely to render UI.

Run the app, test the main flows, inspect for console/runtime errors, and fix them. Update README.md and PROGRESS.md with the exact launch command. Add UI acceptance results to TEST_REPORT.md.
```

## Prompt 4 — Evaluation and judge-facing proof (45 minutes)

```text
Read all root Markdown files and inspect the working ResolveFlow AI app before editing.

Add a judge-facing Evaluation view that proves the brief:
- run the labelled golden dataset;
- display category accuracy, route accuracy, high-risk recall, unsafe-auto-resolve count, average latency, and route distribution;
- show an expected-vs-actual table with mismatches highlighted;
- target zero unsafe auto-resolutions; favor escalation over unsafe guessing;
- include a compact graph visualization or rendered node/edge view matching the actual LangGraph implementation;
- add an MCP evidence panel showing server connected, tool name, last request, top match, score, and duration;
- add estimated time saved with an explicitly labelled assumption (for example, 4 minutes per safely auto-resolved ticket).

Create a repeatable evaluation command. Run it with a deterministic test provider and, if credentials are configured, once with the real provider. Do not fabricate numbers; clearly label which run produced each result.

Fix material routing mistakes without weakening safety. Update TEST_REPORT.md with actual results, ARCHITECTURE.md if behavior changed, and PROGRESS.md.
```

## Prompt 5 — Demo mode and presentation polish (35 minutes)

```text
Read every root Markdown file and verify the app still runs.

Create a reliable Demo Mode with six curated tickets in this sequence:
1. routine password/reset or FAQ → strong MCP KB match → AUTO_RESOLVE;
2. billing question with missing order/invoice detail → CLARIFY;
3. angry payment failure → ESCALATE;
4. possible account takeover/security issue → ESCALATE;
5. routine technical FAQ with strong KB evidence → AUTO_RESOLVE;
6. out-of-scope or low-confidence issue → ESCALATE.

Add a Reset Demo button, predictable ordering, progress indicator, and a “Spotlight” selector that opens the full trace for cases 1 and 3. Make the real MCP tool call unmistakable in the trace.

Write DEMO_SCRIPT.md in the project root containing:
- a crisp 30-second problem statement;
- a 3-minute click-by-click demo;
- what to say while showing the MCP request/response;
- what to say about safe escalation and lack of chain-of-thought;
- architecture explanation in 30 seconds;
- metric summary;
- likely judge questions with short answers;
- backup plan if internet/model API fails.

Update README.md with a “Demo in 60 seconds” section and PROGRESS.md. Rehearse the documented sequence against the app and fix anything unreliable.
```

## Prompt 6 — Final QA and submission package (45 minutes)

```text
This is the final hackathon hardening pass. Read every Markdown file in the project root and inspect git/repository status before changing files. Preserve working behavior.

Perform release QA:
- install from the documented clean setup path;
- verify secrets are absent from tracked/source files and `.env` is ignored;
- run formatting/linting if configured, all tests, golden evaluation, CLI smoke test, MCP server/client test, and Streamlit startup smoke test;
- verify provider failure and MCP failure both fail safely to ESCALATE;
- verify no high-risk golden ticket auto-resolves;
- verify downloads, filters, reset, human override, and trace display;
- verify README commands are copy-paste accurate;
- remove dead code, misleading claims, debug noise, and broken UI elements;
- keep scope hackathon-sized; do not add new frameworks.

Create/update in the ROOT:
- TEST_REPORT.md with timestamped commands and actual pass/fail results;
- PROGRESS.md with final status and any honest known limitations;
- README.md with overview, architecture, setup, environment variables, run/test commands, screenshots placeholders, safety design, MCP proof, and demo steps;
- DEMO_SCRIPT.md finalized for the actual UI.

If time allows, add a simple `Makefile` with `setup`, `run`, `mcp`, `test`, and `eval` targets, but only if verified on this machine.

Return a final readiness table mapped directly to every hackathon requirement and judging metric. Do not claim readiness for anything you did not run.
```

## Prompt 7 — Emergency 15-minute rescue (only if something breaks)

```text
We have 15 minutes before the demo. Read PROGRESS.md, TEST_REPORT.md, ARCHITECTURE.md, and README.md first. Inspect the current error and reproduce it once.

Prioritize a reliable local demo over new features. Preserve these non-negotiables: real MCP server/client boundary, visible MCP trace, explicit graph, one auto-resolve, one clarify, one safe escalation, and dashboard summary.

Make the smallest targeted fix. If a hosted model is unavailable, activate the deterministic demo provider while clearly labelling Demo Provider in the UI; MCP calls and graph execution must remain real. Do not fake test results. Run the 3-ticket smoke test and Streamlit startup check, then update PROGRESS.md and TEST_REPORT.md with the exact fallback in use.
```

---

# Time-box plan

| Clock | Deliverable | Cut if late |
|---|---|---|
| 0:00–0:20 | Frozen product/architecture docs | Nothing |
| 0:20–1:00 | One ticket end-to-end via graph + real MCP | Extra providers |
| 1:00–1:45 | Guardrails, fixtures, core tests | Fancy similarity |
| 1:45–3:00 | Polished dashboard | Persistence/database |
| 3:00–3:45 | Evaluation evidence | Charts beyond core metrics |
| 3:45–4:20 | Demo mode and script | Extra animations |
| 4:20–5:05 | QA and submission | All nonessential features |
| Remaining | Rehearse, screenshots, recording | New scope |

## Hard stop rule

Once the app demonstrates the six curated tickets reliably, stop adding features. Spend remaining time on test evidence, screenshots, README clarity, and rehearsing the MCP/guardrail story.

## What wins the demo

The strongest moment is not an elaborate chatbot. It is this contrast:

- A routine ticket receives a strong KB match through an observable MCP call and is safely auto-resolved.
- An angry payment-failure ticket may have a superficially relevant FAQ, but deterministic safety rules override it and route it to a human with an SLA warning.

That single comparison demonstrates tool use, agency, reasoning state, operational value, and responsible automation.

## Current official references checked on 31 August 2026

- Gemini API pricing/free tiers: https://ai.google.dev/gemini-api/docs/pricing
- OpenAI prepaid billing minimum: https://help.openai.com/en/articles/8264644-how-can-i-set-up-prepaid-billing
- NVIDIA NIM development endpoints: https://build.nvidia.com/
- Groq rate limits: https://console.groq.com/docs/rate-limits

