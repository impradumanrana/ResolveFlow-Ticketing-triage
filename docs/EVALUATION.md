# Evaluation and Test Evidence

Date: 31 August 2026
Environment: Python 3.14.5 (project supports 3.11+)
Live mode: OpenAI `gpt-5.6-luna` + `text-embedding-3-small` through the real LangGraph / MCP / Qdrant runtime
Automated mode: explicit deterministic provider and embedding fixtures, with the real graph, MCP subprocess, SQLite, and Qdrant paths retained

## How to reproduce

```bash
source .venv/bin/activate
pytest -q                                  # full automated suite
python -m app.eval                         # 15-case golden evaluation
python -m app.cli                          # CLI smoke on the core demo tickets
python scripts/evaluate_company_sample.py  # live 30-article company corpus run
```

## Results

| Check | Command | Result |
|---|---|---|
| Full automated suite | `pytest -q` | PASS — 43 passed in 43.75s |
| Golden evaluation | `python -m app.eval` | PASS — 15 labelled cases |
| Category accuracy | golden evaluation | 100% |
| Route accuracy | golden evaluation | 100% |
| High-risk recall | golden evaluation | 100% |
| Unsafe auto-resolves | golden evaluation | 0 |
| Knowledge top-1 retrieval | Qdrant hybrid evaluation | 100% — 9/9 paraphrases |
| Grounded draft rate | golden evaluation | 100% |
| MCP trace completeness | golden evaluation | 100% |
| Route distribution | golden evaluation | 7 auto-resolve, 2 clarify, 6 escalate |
| Paraphrase retrieval comparison | 9 realistic queries | prior scorer 5/9 top-1; hybrid scorer 9/9 top-1 |
| Company sample retrieval | isolated 30-article store | PASS — 10/10 top-1 regression cases |
| Live company RAG evaluation | `scripts/evaluate_company_sample.py` | PASS — 30 indexed; 11/11 top-1 above threshold; 10/10 complete MCP traces; all drafts grounded; 0 unsafe auto-resolves |
| Live RAG storage migration | existing 57-article corpus | PASS — 1536-dim OpenAI embeddings indexed in persistent Qdrant |
| Live end-to-end grounded ticket | full pipeline through the validator | PASS — `AUTO_RESOLVE`, real citation, 2/2 claims verified |
| Protected live guided batch | OpenAI + isolated 15-article demo corpus | PASS — 6/6 expected routes; 2 drafts, 1 clarification, 3 escalations |
| Streamlit rendered interaction | `pytest -q tests/test_dashboard.py` | PASS — 6 tests |
| Streamlit startup | `streamlit run app/dashboard.py --server.port 8504` | PASS — server listening |
| Streamlit health | `curl .../_stcore/health` | PASS — `ok` |
| Browser render check | Playwright against the live server | PASS — dashboard, Support knowledge, and Quality check render with zero console errors |
| Control contrast | computed-style check in a live browser | PASS — fields render `1.5px #6f7d99`, previously `1px #ffffff` |
| Make targets | `make -n setup run mcp test eval` | PASS — commands resolve |
| Secret-pattern scan | source scan excluding local `.env` and virtualenv | PASS — no key-shaped secret found |
| Fresh clone from GitHub | clone → venv → `pip install -e .` → `pytest -q` | PASS — 43 passed with a placeholder key, and again with no `.env` at all |
| Docker image build | `docker build -t resolveflow-ai .` | PASS — builds clean from the pinned lockfile |
| Suite inside container | `docker run --rm resolveflow-ai python -m pytest -q` | PASS — 43 passed; confirms MCP stdio subprocesses spawn under Docker |
| Container startup | `docker run -p 8501:8501 --env-file .env` | PASS — healthy in 2s; Docker `HEALTHCHECK` reports healthy |
| Container first boot, empty volume | keyed run against a fresh named volume | PASS — seeds and embeds the 15 starters; sidebar reports `OpenAI active · Ready · 15 articles`, no exception |
| Volume persistence | `docker restart` | PASS — `knowledge.db` survives the restart |
| Compose safety guard | `docker compose config` with no `OPENAI_API_KEY` | PASS — refuses to start with an explicit message |
| Keyless startup | `streamlit run app/dashboard.py` with no `.env` | PASS — server healthy; sidebar reports the provider unavailable and tickets route to human review |
| Editable install | `pip install -e .` | PASS — package discovery pinned to `app` |
| Wheel build | `pip wheel --no-deps .` | PASS — builds; wheel contains only `app` |
| Release packaging | staged-file and ignore audit | PASS — `.env`, databases, vectors, caches, screenshots, and virtualenv excluded |

## Automated coverage

Typed models and import smoke · one bounded hosted-output repair attempt · every blocking safety rule · the "issue" vs "sue" false-positive regression · real MCP search across the full FAQ fixture · SQLite initialization and migration · persistent Qdrant creation, sync, clearing, replacement, and semantic query · add / clear-user / clear-all / replace-all / restore operations · nine realistic paraphrase retrieval cases · Markdown and text chunk ingestion with 30 section boundaries and mixed per-section categories (PDF uses the same parser path) · hybrid score components and reranked positions returned through MCP · knowledge CSV validation · the `KB_THRESHOLD` boundary and one step below it · the safe auto-resolve route · the missing-information clarification route · the high-risk escalation route · provider and schema failure → `MODEL_ERROR` → escalation · MCP outage → `MCP_UNAVAILABLE` → escalation · prompt-injection resistance · citation presence in auto-resolution · immutable short evidence keys mapping back to exact stored article IDs · exact supporting-quote validation and adversarial unsupported-answer withholding · paragraph-level citation enforcement · the forgotten-password guardrail regression · the exact successful graph trace including `observe` · 15-case golden accuracy and safety metrics · Streamlit empty state · explicit light-theme and component-level contrast rules including the `data-baseweb` regression guard · plain-language navigation labels · the six-case batch and KPI totals · the full 36-ticket sample batch · CSV parsing, required-column validation, and the 50-ticket limit · duplicate-ID rejection and SLA target mapping · sidebar contrast in both expander states · compact ticket and MCP evidence cards · operational source and status fields · queue table and downloads · ticket detail and trace · human route override and audit event · reset · Quality check and How it works navigation.

## Two kinds of number, labelled separately

**Fixed metrics** (the accuracy percentages above) come from the repeatable 43-test suite using deterministic provider and embedding fixtures, with the real graph, MCP subprocess, SQLite, and Qdrant retained. They do not move between runs.

**Quality check metrics**, produced in-app, are measured live against whichever knowledge base is currently ingested. OpenAI writes customer-style questions for up to eight sampled stored articles and each is searched through the real MCP boundary. These figures legitimately move as the corpus changes — that is the point of the view. Each run stamps a knowledge fingerprint and a UTC timestamp, and the view warns when the corpus has changed since the run. See [D-014](DECISIONS.md).

Normal dashboard and CLI operation never expose or silently select a test fixture.

## Quality check gates

| Gate | Target |
|---|---|
| Ticket classification | ≥ 85% |
| End-to-end routing | ≥ 80% |
| High-risk recall | 100% |
| Unsafe automation | 0 |
| Stored-article retrieval | ≥ 75% |
| Grounded answer drafts | 100% |
| Complete MCP evidence | 100% |

A `REVIEW` result is actionable evidence, not a hidden failure — the tables beneath the gates name the exact ticket or article that missed its target.

## Known limitations

- Ticket results and reviewer audit events are session-scoped. Knowledge articles persist in SQLite and dense vectors persist in Qdrant local mode.
- Qdrant runs in embedded local persistent mode; a hosted Qdrant service is the natural multi-instance upgrade.
- Each MCP lookup starts a short-lived stdio server process, favoring visible isolation over throughput.
- This build does not redact PII. Users are warned not to submit secrets; production deployment requires redaction and retention controls.
- No production auth or live helpdesk integration, by hackathon scope.
