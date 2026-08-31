# ResolveFlow AI

**Safe, explainable first-line customer-support triage.**
Built for the Customer Support Ticket Triage Agent hackathon.

ResolveFlow classifies each ticket, applies deterministic safety controls, searches a persistent support knowledge base through a **real MCP stdio client/server boundary**, and chooses one of three routes:

| Route | Meaning |
|---|---|
| `AUTO_RESOLVE` | Strong, low-risk article match with a cited, grounding-validated response draft |
| `CLARIFY` | One specific required customer fact is missing |
| `ESCALATE` | Risk, uncertainty, weak evidence, or a provider/MCP failure |

**The app never sends a message to a customer.** `AUTO_RESOLVE` produces a review-ready draft, nothing more.

---

## Quick start

Python 3.11+ required (developed on 3.14.5).

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e .
cp .env.example .env        # then add your OpenAI API key
streamlit run app/dashboard.py
```

Open the printed local URL. Keep **Guided demo · 6 tickets** selected, click **Load sample batch**, then open T-001 and T-002 to see the contrast between grounded auto-resolution and safety-first escalation.

A valid OpenAI API key is required: both classification and dense embeddings use OpenAI. The automated tests use explicit network-free fixtures. Secrets are git-ignored.

### Other commands

```bash
python -m app.cli                          # CLI smoke run
python -m app.eval                         # 15-case golden evaluation
python scripts/evaluate_company_sample.py  # live 30-article company corpus run
pytest -q                                  # full suite (43 tests)
```

`make run`, `make test`, and `make eval` are equivalent shortcuts.

---

## Requirement coverage

| Hackathon requirement | Implementation |
|---|---|
| Category + urgency | Typed billing/technical/account and low/medium/high/critical classification |
| Agentic loop | Inspectable LangGraph: perceive → classify → risk guard → MCP/Qdrant search → rerank → decide → act → validate → observe |
| Real MCP tool use | A separate FastMCP process over stdio — no in-process shortcut |
| Safe routing | Deterministic guardrails override model output |
| Batch output | Six-case guided demo, full 36-ticket batch, and validated CSV import up to 50 tickets |
| Explainability | Node trace, rule codes, timestamps, latency, MCP request/response, rerank scores, citations, grounding result |
| Dashboard | Queue, filters, details, KPIs, human controls, downloads, evaluation, architecture |
| Refusal case | Angry payment failure and account takeover are visibly escalated |

---

## How it works

```text
Ticket
  → perceive
  → classify              (OpenAI, schema-validated)
  → risk_guard            (deterministic controls)
  → kb_search_mcp ──stdio──> FastMCP server ──> SQLite + Qdrant dense/BM25 hybrid search
  → decide
      ├─ AUTO_RESOLVE → grounded OpenAI draft → citation/grounding validator
      ├─ CLARIFY      → draft_clarification
      └─ ESCALATE     → create_escalation
  → observe
```

Every node records a timestamped trace event with its duration, status, and evidence. The dashboard renders those recorded state facts — never hidden chain-of-thought.

**Retrieval.** SQLite (`app/data/knowledge.db`) is the article source of truth; Qdrant local persistent mode (`app/data/qdrant`) is the vector index. OpenAI `text-embedding-3-small` produces 1536-dimensional dense vectors. Qdrant cosine similarity and BM25 build a candidate set, then a transparent rerank combines semantic, lexical, fuzzy, keyword, category, and reciprocal-rank signals. The MCP trace returns every score component.

**Grounding.** Only an `AUTO_RESOLVE` candidate reaches generation, and the model sees only the ticket and the retrieved passages. It must return article citations plus exact supporting quotes. A fail-closed validator confirms every citation ID came from the MCP response and every quote exists verbatim in its cited article. Any failure withholds the draft and routes to a human.

Full detail: **[docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)**.

---

## Safety model

Auto-resolution is blocked by any of: high or critical urgency, anger or threats, security risk, payment failure, refund or legal risk, missing information, low classification confidence, an article score below `KB_THRESHOLD`, model failure, MCP failure, or failed grounding validation.

Rule codes: `HIGH_URGENCY` · `CRITICAL_URGENCY` · `ANGRY_CUSTOMER` · `THREAT_DETECTED` · `SECURITY_RISK` · `PAYMENT_FAILURE` · `REFUND_OR_LEGAL` · `MISSING_INFORMATION` · `LOW_CLASSIFICATION_CONFIDENCE` · `LOW_KB_CONFIDENCE` · `MODEL_ERROR` · `MCP_UNAVAILABLE` · `GROUNDING_VALIDATION_FAILED`

Provider and MCP outages are visible in the trace and route to human review. The system fails closed; it never silently swaps in a different classifier.

---

## Using the app

### Ticket triage

Three start tabs: load a sample batch, add one ticket manually, or import a CSV.

**CSV import** — download the empty template or the editable 50-ticket Northstar example. Required columns are `subject` and `body`; `ticket_id` and `customer_id` are optional. The app validates the file, rejects duplicate IDs, caps a run at 50 tickets, and routes every row through the real graph and MCP workflow.

Below the tabs: KPIs, source/status/search filters, priority sorting, SLA response targets, the queue, ticket facts, response and handoff drafts, MCP evidence, grounding status, the full trace, a reviewer override with audit log, and downloads.

### Support knowledge

Browse and export articles, add one approved article manually, import CSV, or ingest PDF, Markdown, and text files. The ingestion panel includes a downloadable 30-article Northstar example. Optional per-section `**Category:**` labels preserve technical, billing, and account categories in mixed Markdown. Extracted sections are previewed before indexing, and imports can append or replace completely. A single typed confirmation clears the whole knowledge base; the 15 starter articles restore separately. A built-in console runs the same real MCP search that ticket triage uses.

### Quality check

Runs live against the **operational** knowledge base — the one manual and CSV tickets use. It classifies 15 labelled tickets, has OpenAI write customer-style questions for up to eight sampled stored articles, searches each through the real MCP boundary, and validates every draft. Seven gates: classification, routing, high-risk recall, unsafe automation, stored-article retrieval, grounded drafts, and complete MCP evidence.

Each run stamps a knowledge fingerprint and timestamp, and warns you if the corpus changed afterwards.

### Two corpora, on purpose

Guided **sample batches** use a protected 15-article demo corpus, so a knowledge upload can never break the reproducible six-ticket judge narrative. **Quality check** deliberately does the opposite and measures whatever is currently ingested. See [D-013 and D-014](docs/DECISIONS.md).

---

## Verified results

- **43 tests pass**, covering protected-demo isolation, mixed-category Markdown ingestion, persistent SQLite/Qdrant CRUD, hybrid paraphrase retrieval, real MCP search, paragraph-level citation and grounding validation, CSV validation, hosted-output repair, SLA targets, rendered Streamlit interactions, and contrast safeguards.
- **Golden run** (deterministic fixtures + real MCP/Qdrant): 15/15 category and route matches, 100% high-risk recall, **0 unsafe auto-resolves**, 9/9 paraphrase retrieval top-1.
- **Live Northstar company evaluation**: 30/30 articles indexed, 11/11 paraphrase searches correct and above threshold, 10/10 complete MCP traces, all drafts grounded, 0 unsafe auto-resolves.
- **Live OpenAI RAG smoke**: a safe password-reset ticket reached `AUTO_RESOLVE` through real MCP/Qdrant retrieval with an exact stored citation and 2/2 grounding claims verified.
- **Protected six-ticket demo**: 6/6 expected routes — 2 grounded drafts, 1 clarification, 3 safe escalations.
- **Browser render check**: dashboard, Support knowledge, and Quality check render with zero console errors.

Commands, the full coverage list, and honest limitations: **[docs/EVALUATION.md](docs/EVALUATION.md)**.

---

## Demo in 60 seconds

1. Select **Guided demo · 6 tickets** → **Load sample batch**.
2. Open **T-001**: `AUTO_RESOLVE`, KB-001, its score, and the MCP request.
3. Open **T-002**: `ESCALATE` despite relevant evidence, because urgency, anger, and payment-risk controls override it.
4. Switch to **Quality check** and run the live evaluation.
5. Show all seven gates.

Curated order: routine password reset → `AUTO_RESOLVE` · billing question missing identifiers → `CLARIFY` · angry payment failure → `ESCALATE` · account takeover → `ESCALATE` · routine 2FA FAQ → `AUTO_RESOLVE` · prompt injection → `ESCALATE`

Full narration, judge Q&A, and a pre-demo checklist: **[docs/DEMO.md](docs/DEMO.md)**.

---

## Documentation

| Document | Contents |
|---|---|
| [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) | Runtime flow, MCP boundary, storage, retrieval, grounding, guardrails, UI structure |
| [docs/PRODUCT_SPEC.md](docs/PRODUCT_SPEC.md) | Problem, user stories, safety policy, acceptance criteria, golden cases |
| [docs/EVALUATION.md](docs/EVALUATION.md) | Test evidence, metrics, coverage, limitations |
| [docs/DEMO.md](docs/DEMO.md) | Demo script, talking points, judge Q&A, checklist |
| [docs/DECISIONS.md](docs/DECISIONS.md) | Decision log D-001 → D-015 with supersessions |

## Repository layout

```text
app/
  dashboard.py       Streamlit UI
  graph.py           LangGraph workflow and routing
  guardrails.py      Deterministic safety controls
  mcp_client.py      Launches the MCP server over stdio
  mcp_server.py      FastMCP server exposing search_knowledge_base
  knowledge_store.py SQLite + Qdrant persistence and hybrid retrieval
  providers.py       OpenAI adapter and the test-only deterministic provider
  eval.py            Golden and operational evaluation
  cli.py             Command-line triage
  models.py          Typed Ticket, Classification, TraceEvent, TriageResult
  fixtures/          Labelled tickets and starter articles
docs/                Architecture, spec, evaluation, demo, decisions
sample_data/         Northstar company knowledge and ticket examples
scripts/             Live company-corpus evaluation
tests/               43 automated tests
```

## Configuration

```env
OPENAI_API_KEY=your_api_key_here
OPENAI_MODEL=gpt-4.1-mini
OPENAI_EMBEDDING_MODEL=text-embedding-3-small
OPENAI_EMBEDDING_DIMENSIONS=1536
QDRANT_COLLECTION=resolveflow_knowledge
KB_THRESHOLD=0.55
```

## Limitations

Session-scoped ticket results and audit events · embedded Qdrant rather than a hosted service · a short-lived MCP process per lookup · **no PII redaction in this build**, so do not submit secrets · no production auth or live helpdesk integration, by hackathon scope.
