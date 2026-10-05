# ResolveFlow AI - C00 Repository and Production Gap Report

**Audit date:** 11 September 2026

**Audited commit:** `788a4ed` on `main`

**Scope:** Read-only inspection and local non-network test execution. No deployment, cloud mutation, mailbox connection, paid model call, or deliberate knowledge-content mutation was performed. Offline tests exercised local retrieval paths and may update ignored cache or vector-store housekeeping files.

## Executive finding

The repository is a credible, well-tested hackathon MVP and a useful production-core reference. It is not currently a production client application.

The strongest reusable elements are the typed ticket/result contracts, LangGraph workflow, deterministic safety overrides, real MCP stdio process boundary, dense-plus-keyword hybrid retrieval, evidence-only drafting, citation validation, operational Quality Check, sample data, and automated tests.

The production build is primarily a platform migration around that core: Next.js, authenticated service APIs, PostgreSQL/pgvector, durable Gmail ingestion, tenant/role authorization, persistent audits, managed secrets, observability, infrastructure as code, and client-owned deployment.

## Evidence collected

| Check | Result |
|---|---|
| Python automated suite | PASS - 43 tests in 52.76 seconds |
| Import smoke for application modules | PASS |
| Make target resolution | PASS |
| Docker Compose service resolution | PASS - one service named `resolveflow` |
| Package shape | Python-only; no `package.json` or Next.js application |
| CI workflows | None found |
| Infrastructure-as-code files | None found |
| Local runtime data | `app/data` exists locally, approximately 5.8 MB, and is ignored |
| Local `.env` | Present, ignored, and untracked |
| Credential-shaped files | No PEM/key/service-account credential files found in the repository tree |
| Current tracked-source key scan | No real key-shaped match; README contains an intentional placeholder |
| Commit-history key-pattern scan | No non-placeholder key-shaped match found by the bounded regex scan |
| External calls during C00 | None |

Pytest reported one cache-write warning because the current tool sandbox could not update `.pytest_cache`; all 43 tests passed. This is an execution-environment warning, not an application failure.

## Current architecture inventory

| Area | Current implementation | Production disposition |
|---|---|---|
| UI | 1,213-line Streamlit dashboard | Preserve as internal reference; replace customer UI with Next.js |
| Workflow | LangGraph nodes for perceive, classify, guard, MCP search, decide, draft/clarify/escalate, validate, observe | Preserve behavior behind an authenticated Python API |
| Models | Pydantic ticket, classification, KB match, trace, triage result | Version as external/internal API contracts |
| Safety | Deterministic rules override model; failures escalate | Preserve, broaden, version, test and audit |
| LLM runtime | OpenAI runtime; deterministic provider restricted to tests | Replace direct global configuration with client BYOK gateway |
| Knowledge source | Local SQLite article store | Migrate to organization-scoped PostgreSQL |
| Vector index | Embedded persistent Qdrant | Migrate client production to pgvector per locked plan |
| Embeddings | OpenAI `text-embedding-3-small`, 1536 dimensions | Make model/version explicit and migration-safe |
| Retrieval | Dense Qdrant plus local BM25/fuzzy/keyword/category/RRF rerank | Preserve measurable behavior using pgvector + PostgreSQL full text |
| MCP | New short-lived stdio process per query | Preserve tool contract; productionize process/service lifecycle and auth |
| Grounding | Retrieved IDs, exact support quotes, paragraph citation validation | Preserve fail-closed behavior and add passage-level provenance |
| Quality | 15 labeled tickets plus generated probes against operational knowledge | Preserve and expand with versioned datasets/thresholds |
| Ticket persistence | Streamlit session state | Replace with durable database records and concurrency control |
| Reviewer audit | Streamlit session list | Replace with actor-attributed append-only persistent audit events |
| Knowledge persistence | Local volume | Replace with Cloud SQL/Storage, migrations, backups and deletion workflows |
| Authentication | None | Build Workspace login, invitation, membership and RBAC |
| Gmail/helpdesk connector | None | Build OAuth, Pub/Sub/history ingestion, reconciliation and health |
| Background work | None | Build queues, tasks, workers, retries and dead letters |
| Cloud deployment | Local Docker/Compose | Build client-owned GCP IaC and CI/CD |

## Requirement-to-gap matrix

| Client capability | Current evidence | Gap severity | Target phase |
|---|---|---:|---|
| Next.js production UI | No JS manifest; Streamlit only | Critical | C01/C08 |
| Authenticated private application | Product spec explicitly lists no production auth | Critical | C03 |
| Organization/department/RBAC data model | No organization or membership models | Critical | C03/C04 |
| Gmail connection and multi-mailbox control | No Gmail/OAuth/mailbox implementation in application code | Critical | C06/C07 |
| Durable ingestion and jobs | No queue/worker/watch/history implementation | Critical | C02/C07 |
| Hosted production data | SQLite and embedded Qdrant on local volume | Critical | C04/C05 |
| Managed secrets | Local environment variables only | High | C02/C10 |
| Production API boundary | UI imports and invokes Python modules directly | High | C01 |
| Persistent ticket/action/audit state | Session state only | Critical | C04/C08/C12 |
| PII/privacy controls | Evaluation document explicitly states no PII redaction | Critical | C13 |
| Backups, restore, retention, deletion | Local persistence only | Critical | C02/C04/C13 |
| Observability/SLO/alerts | No production telemetry stack | High | C02/C13 |
| CI/CD and IaC | No workflows and zero IaC files | High | C01/C02 |
| Production MCP throughput/auth | Short-lived local stdio subprocess per lookup | High | C01/C05 |
| Multi-provider BYOK | Local env contains several provider variable names, but application config/runtime consumes OpenAI settings only | Medium | C10 |
| Human-approved Gmail drafts | Review is session-only and sends nothing | High | C12 |
| Automated customer sending | Correctly absent | No gap; retain prohibition | C12 |
| RAG trace and grounding | Implemented and tested | Low; migrate without regression | C05/C11 |
| Quality evaluation | Implemented against operational knowledge | Medium; persist/version results | C11 |
| UI accessibility/contrast | MVP has regression tests and explicit Streamlit styling | Medium; rebuild/test in Next.js | C08 |

## Security and privacy findings

### Positive controls

- `.env`, local databases, Qdrant data, caches, and virtual environments are ignored.
- No credential file was found, and bounded current/history scans found no non-placeholder key pattern.
- Provider and MCP failures fail closed to human review.
- Prompt-injection-like phrases trigger deterministic controls.
- Automatic customer sending does not exist.
- Grounded answers require retrieved citation IDs and exact support quotes.

### Production blockers

- No authentication, membership, RBAC, or object-level authorization.
- No PII detection/redaction, retention enforcement, export/delete workflow, legal hold, or data classification.
- Local secrets have no managed rotation, KMS boundary, or actor audit.
- Session audit records are neither durable nor strongly attributable.
- Uploaded files do not yet have a production malware/quarantine pipeline.
- No rate limits, WAF/ingress policy, egress policy, security headers, production dependency scanning, or incident response.
- No tenant-scoped database/vector policy because the MVP has no tenant model.
- No Gmail OAuth token protection or consent/scope lifecycle because Gmail is not implemented.

## Data and configuration observations

- `app/data` is ignored local runtime state and must be treated as potentially user-generated. C00 did not inspect article contents.
- Sample Northstar data is tracked and suitable for synthetic testing, but it must remain clearly synthetic.
- The local `.env` contains variable names for OpenAI, Gemini, Groq, and Ollama. Current `app/config.py` and normal runtime are OpenAI-only; the extra names do not constitute implemented providers or fallback.
- `requirements.txt` is pinned, while `pyproject.toml` uses broad minimum versions. C01 must establish one authoritative reproducible dependency workflow.
- The Docker image currently runs Streamlit and writes SQLite/Qdrant under one mounted path. It is not the target service topology.
- The repository has no `AGENTS.md`; current root plans and user instructions therefore govern the production work.

## Risks ranked

| Risk | Likelihood | Impact | Treatment |
|---|---|---|---|
| Building on client data before privacy controls | Medium | Critical | Synthetic-only through staging readiness; C13 gate |
| OAuth/restricted Gmail scope approval delays | High | High | Start read-only scope planning early; explicit client ownership |
| Missing client region/retention/volume inputs | High | High | Resolve before consuming phase; never guess deployment values |
| Rewriting proven AI logic during frontend migration | Medium | High | Contract tests around Python service; preserve graph behavior |
| Cross-mailbox or cross-role authorization defect | Medium | Critical | Server-derived permissions and adversarial tests |
| Duplicate/missed Gmail events | High | High | Idempotency, history reconciliation, dead letters, recovery drills |
| Retrieval quality regression during pgvector migration | Medium | High | Freeze labeled cases and compare old/new metrics |
| Provider cost or outage | Medium | Medium | Budgets, bounded calls, explicit safe fallback, human route |
| Client/founder IP or data mixing | Medium | Critical | Contract review and clean founder boundary before reuse |
| Unrelated dirty worktree changes overwritten | Low | High | Preserve deleted presentation and pre-existing untracked plans |

## C00 gate result

**GREEN for completion of C00 and entry into local C01 foundation work.**

The V1 scope, exclusions, ownership boundaries, secret-handling rules, current implementation, gaps, risks, and unknown client inputs are recorded. C01 can proceed without cloud identifiers or production data.

This does **not** authorize C02 infrastructure application, OAuth consent changes, mailbox connection, staging/production deployment, paid-provider testing, or production-data migration. The unresolved input table in `CLIENT_SCOPE.md` blocks the phase that consumes each value.
