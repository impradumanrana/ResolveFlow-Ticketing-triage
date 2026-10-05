# ResolveFlow AI - Dedicated Client Build and Deployment Plan

**Track ID:** `CLIENT`

**Target:** A production-quality first version for one client, hosted in the client’s Google Cloud account

**Commercial UI:** None. No pricing plans, checkout, Stripe, trials, or subscription enforcement.

## 1. Product outcome

The client receives a private support-operations workspace that can connect authorized support mailboxes, ingest the client’s knowledge, classify and route conversations, retrieve grounded evidence, prepare cited reply drafts, and let authorized people approve actions from one clean interface.

Version 1 is not an autonomous email bot. It starts in Observe Mode and then enables human-approved provider drafts. Actual sending is a separately approved production capability. Model failures, missing evidence, conflicting policies, safety risk, and low confidence always route to a person.

## 2. Users and scope

- One client organization, but multiple departments, queues, roles, locations, and mailboxes.
- Owner, Admin, Supervisor, Agent, Knowledge Manager, and Auditor roles.
- One person may oversee many authorized shared mailboxes; mailbox count is not tied to seat count.
- Google Workspace login is restricted to approved client domains and invited users.
- Begin with 1-2 shared support mailboxes. Prove reliability before expanding toward 50.
- Distinguish independent mailboxes, aliases/send-as identities, and Google Groups.

## 3. Locked architecture

| Layer | Client v1 decision |
|---|---|
| Frontend | Next.js App Router, React, TypeScript, Tailwind, accessible Radix/shadcn primitives |
| Web/API | Containerized Next.js BFF on Cloud Run; server-derived authorization context |
| AI services | Existing Python triage/RAG/MCP/evaluation logic behind a private authenticated Cloud Run API |
| Database | Cloud SQL PostgreSQL with pgvector, full-text search, migrations, PITR/backups, and connection pooling |
| Authentication | Auth.js with Google Workspace OAuth, database sessions, invited membership, and domain allowlist |
| Async work | Pub/Sub for Gmail events; Cloud Tasks for controlled delivery/retries; Scheduler for reconciliation/watch renewal |
| Files | Private Cloud Storage with type/size validation and safe attachment handling |
| Secrets | Secret Manager; KMS-backed envelope encryption where needed |
| Observability | Structured logs, traces, error reporting, SLOs, alerts, immutable audit events, and cost budgets |
| Infrastructure | Reviewed infrastructure as code; staging and production are separate |

## 4. Required workflow

```text
Authorized mailbox event
  -> idempotent ingestion and thread normalization
  -> selected-provider structured classification
  -> deterministic safety, permission, SLA, and routing guardrails
  -> tenant-scoped MCP knowledge search
  -> pgvector semantic + PostgreSQL keyword retrieval
  -> rerank best passages
  -> evidence-bound answer draft
  -> citation and grounding validator
  -> quality score and reason codes
  -> human review / provider draft / no-action decision
  -> complete audit event
```

MCP is an authorized tool boundary, not the vector database. PostgreSQL/pgvector stores embeddings and performs indexed semantic search; MCP exposes the approved knowledge-search operation and trace.

## 5. Client-facing screens

1. Sign in and access-denied/help states.
2. Operations overview by mailbox, department, queue, SLA risk, and assignment.
3. Unified inbox with obvious unseen state, filters, saved views, keyboard navigation, bulk assignment, and pagination.
4. Ticket workspace with conversation, customer context, route, urgency, evidence, citations, draft, quality result, trace, and action history.
5. Knowledge workspace for upload, parse status, article/chunk preview, indexing, retrieval test, versioning, archive, and clear-all confirmation.
6. Quality workspace using stored knowledge and real triage outputs: route/urgency accuracy, retrieval recall, groundedness, citation validity, safety, and latency.
7. Mailbox/department administration with connection health, last sync, history cursor, permissions, watch expiry, and reconnect controls.
8. AI settings with masked BYOK credentials, connection test, workload model, fallback, estimated usage, and internal budget—not misleading provider “balance” claims.
9. Members, roles, audit log, retention, export/delete, and system health.
10. “How it works” describing the real deployed pipeline and safety boundaries.

Every control needs a visible border/focus state, label, accessible contrast, keyboard behavior, and loading/empty/error/success states.

## 6. Phase-by-phase execution

### C00 - Contract, environment, and repository audit

Inventory the MVP, tests, secrets, data, Gmail work, RAG behavior, and plan documents. Write `CLIENT_SCOPE.md`, `CLIENT_HANDOFF.md`, `DECISIONS.md`, and an evidence-backed gap report. Confirm cloud ownership, domain, residency, retention, roles, mailboxes, volume, and permitted providers. Do not deploy or mutate live data.

**Gate:** V1 scope/exclusions, ownership boundaries, and secret handling are recorded.

### C01 - Production repository foundation

Create the coherent Next.js plus Python layout, shared API contracts, local environment, lint/type/test commands, containers, CI, and migrations. Preserve proven Python behavior.

**Gate:** Clean checkout passes install, lint, type-check, unit/contract tests, and container builds.

### C02 - Client-owned GCP foundation

Define staging/production using reviewed infrastructure as code: IAM, Artifact Registry, Cloud Run, Cloud SQL, Storage, Pub/Sub, Tasks, Scheduler, Secret Manager/KMS, logging, alerts, backups, and budgets. Use Workload Identity Federation for CI.

**Gate:** Staging deploys with private backend/worker/database access and documented rollback. Production still requires explicit approval.

### C03 - Identity, one organization, and RBAC

Implement Workspace login, invited membership, domain allowlist, sessions, CSRF protection, one bootstrap Owner, departments, roles, and server-side authorization. Keep `organization_id` on every tenant-owned record.

**Gate:** Route, API, object, and role tests prove unauthorized access fails.

### C04 - Durable data model and audit

Add organizations, memberships, departments, queues, mailboxes, messages, threads, tickets, assignments, SLA, knowledge/chunks/embeddings, AI configs, jobs, usage, evaluations, actions, and append-only audits. Add idempotency and retention.

**Gate:** Constraints, indexes, migrations, restore, retention, and audit tests pass.

### C05 - Knowledge ingestion and production RAG

Support approved PDF, Markdown, text, HTML, and CSV sources. Validate, extract, normalize, chunk, embed, index, version, and show failures. Implement scoped pgvector semantic plus PostgreSQL full-text retrieval, reranking, citations, retrieval testing, deletion/reindex, and clear-all confirmation.

**Gate:** Labeled Recall@K/MRR thresholds pass; archived or unauthorized content cannot be retrieved; citations map to exact passages.

### C06 - Gmail connection and permissions

Connect one shared mailbox with least-privilege OAuth. Encrypt tokens and record scopes, connection owner, health, revocation, and explicit mailbox permissions. Start read-only. Document aliases, Groups, OAuth mailboxes, and domain-wide delegation separately.

**Gate:** Connect, refresh, revoke, reconnect, scope-denial, and wrong-mailbox tests pass.

### C07 - Reliable multi-mailbox ingestion

Implement Gmail watches, Pub/Sub verification, history cursors, reconciliation, backfill, normalized threads, deduplication, retries, dead letters, attachment policy, and per-user unseen state. Expand live mailboxes only after reliability; simulate 50 for load tests.

**Gate:** Duplicate, delayed, out-of-order, expired-watch, revoked-token, quota, and restart scenarios recover correctly.

### C08 - Unified operations workspace

Build overview, inbox, ticket details, filters, saved views, assignment, SLA, bulk actions, realtime updates, and accessible responsive states. Distinguish “new to me,” provider unread, waiting, approval, failed, and resolved.

**Gate:** Critical journeys pass browser, keyboard, contrast, and responsive tests.

### C09 - Rules, routing, SLA, and guardrails

Add versioned department/category/urgency/assignment/VIP/risk/business-hours/SLA/escalation rules. Deterministic permission and safety decisions override model output.

**Gate:** Golden rules, conflicts, time zones, SLA, and fail-safe review tests pass.

### C10 - Client BYOK AI gateway

Support approved providers behind one structured contract, beginning with the launch provider. Store keys in Secret Manager, expose masked metadata only, test server-side, configure workload models, and permit fallback only within approved providers, regions, and budgets.

**Gate:** Invalid, expired, rate-limited, budget, invalid-schema, and fallback cases fail safely and visibly.

### C11 - Triage, grounded drafting, and quality

Run classification, guardrails, MCP retrieval, reranking, evidence-only generation, citations, validation, and disposition. Make Quality Check measure actual stored knowledge and triage results, including accuracy, retrieval, groundedness, safety, latency, and cost.

**Gate:** Versioned evaluation thresholds pass; a demo visibly shows request -> MCP call -> knowledge response -> grounded answer -> validator.

### C12 - Human review and provider drafts

Implement edit, approve, reject, reroute, assign, and Gmail/provider draft creation with optimistic locking, reasons, preview, audit, and permissions. Keep automatic sending disabled.

**Gate:** No path auto-sends; concurrency and duplicate-action tests pass; every decision is attributable.

### C13 - Security, privacy, and operations

Complete threat modeling, OWASP checks, prompt-injection defenses, attachment safety, rate limits, egress, scanning, rotation, retention/deletion/export, redaction, disaster recovery, incident response, and audit access.

**Gate:** No open critical/high issue; restore/rollback and privacy deletion pass.

### C14 - Staging pilot and acceptance

Deploy staging, ingest approved knowledge, connect test mailboxes, run synthetic plus client-approved cases, measure accuracy/latency/cost, conduct role UAT, train users, and run Observe Mode before drafts.

**Gate:** Signed UAT criteria, AI report, runbook, support contacts, and production go/no-go exist.

### C15 - Production launch and handover

Deploy through reviewed CI, seed the Owner securely, connect a limited cohort, monitor, expand gradually, document backup/restore/rotation, and hand over client-owned administration. Record rollback and support boundaries.

**Gate:** Production smoke tests, alerts, audit, backups, budgets, access review, and handover are green.

## 7. Exact Codex instruction

```text
Execute CLIENT plan, phase C02 only, from RESOLVEFLOW_CLIENT_DEDICATED_PLAN.md.
Read CLIENT_HANDOFF.md, CLIENT_SCOPE.md, DECISIONS.md, CLIENT_C00_GAP_REPORT.md, CLIENT_C01_TEST_REPORT.md, and PROGRESS.md first. Preserve unrelated worktree changes. Stop at the C02 gate, run proportional offline checks, update the handoff/progress/decisions, and report evidence and remaining risks. Define infrastructure as code, but do not apply it, deploy, create cloud resources, connect Gmail, call paid models, or mutate production data without explicit approval.
```

Replace `C02` with the next phase only after its gate is green.

## 8. Explicitly out of scope

- Public registration or arbitrary workspace creation.
- Stripe, pricing, trials, coupons, invoices, dunning, and plan entitlements.
- Founder-owned production credentials or cloud resources.
- Domain-wide access to all employee mailboxes by default.
- Automatic reply sending.
- Unverified compliance claims.
- Copying production data into laptops, tests, demos, or the future SaaS.
