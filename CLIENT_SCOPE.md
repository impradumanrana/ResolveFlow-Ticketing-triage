# ResolveFlow AI - Dedicated Client V1 Scope

**Track:** `CLIENT`

**Status:** Baseline scope recorded at C00; client-specific values listed as “confirmation required” must be resolved before the phase that consumes them.

**Deployment profile:** `client-dedicated`

## Product objective

Deliver a private support-operations workspace in infrastructure owned by one client. Authorized staff can monitor multiple approved support mailboxes, ingest company knowledge, triage conversations, retrieve evidence, review cited AI drafts, and record human decisions. The system reduces repetitive first-line work without allowing model output to bypass permissions or safety policy.

## V1 operating model

- One client organization with multiple departments, queues, members, roles, and authorized mailboxes.
- Client-owned Google Cloud projects and billing for staging and production.
- Client-controlled Google Workspace/OAuth configuration and model-provider credentials.
- No public signup or self-service workspace creation.
- No pricing, subscription, trial, Stripe, invoicing, quota-purchase, or upgrade UI.
- Observe Mode first, followed by human-approved creation of provider drafts.
- No automatic customer sending.
- One person may supervise many mailboxes; mailbox count does not equal seat count.

## In scope

### Identity and authorization

- Google Workspace sign-in restricted by invited membership and approved client domains.
- Owner, Admin, Supervisor, Agent, Knowledge Manager, and Auditor roles.
- Server-derived organization, department, role, mailbox, and action authorization.
- Session management, revocation, CSRF protection, security events, and an append-only audit trail.

### Mail and operations

- OAuth connection of explicitly approved Gmail/shared mailboxes.
- Begin with 1-2 test/shared mailboxes; expand only after the ingestion reliability gate.
- Gmail watch, Pub/Sub notifications, history reconciliation, backfill, deduplication, retry, dead-letter handling, and connection-health UI.
- Unified inbox, departments, queues, assignments, saved views, SLA state, per-user unseen state, filters, pagination, and bulk assignment.
- Original conversation, route, risk, evidence, citations, draft, quality result, trace, and action history in the ticket workspace.

### Knowledge and RAG

- Ingest approved PDF, Markdown, text, HTML, and CSV sources.
- Validation, extraction, chunk preview, versioning, indexing status, reindex, archive/delete, export, and unmistakable clear-all confirmation.
- PostgreSQL full-text plus pgvector semantic retrieval, tenant filters, hybrid fusion, passage reranking, and exact citations.
- Tenant-aware MCP knowledge-search tool and recorded request/response evidence.
- Evidence-only draft generation followed by citation and grounding validation.

### AI and safety

- Client-owned model credentials stored in Google Secret Manager.
- Launch with the client-approved provider/model; adapters may support additional approved providers without exposing all of them by default.
- Structured classification and generation contracts, bounded retries, explicit fallback policy, provider health, estimated usage, and internal budgets.
- Deterministic safety, permission, routing, and SLA rules override model suggestions.
- Low confidence, weak evidence, provider/tool failure, unsafe content, or invalid grounding routes to a person.
- Quality Check measures the current operational knowledge and real triage pipeline.

### Platform and operations

- Next.js customer UI plus private Python AI/RAG/MCP services.
- Cloud Run, Cloud SQL PostgreSQL/pgvector, Pub/Sub, Cloud Tasks, Cloud Scheduler, Cloud Storage, Secret Manager/KMS, Artifact Registry, and observability.
- Separate staging and production resources.
- Infrastructure as code, automated tests, reviewed deployment pipeline, backup/restore, rollback, monitoring, alerts, budgets, runbook, and handover.

## Explicitly out of scope

- Public registration, multi-customer SaaS onboarding, organization marketplace, or reseller features.
- Pricing pages, payment collection, subscriptions, entitlements, trials, taxes, invoices, coupons, or dunning.
- Automatic email sending or model-controlled provider writes.
- Bulk access to every employee mailbox through domain-wide delegation by default.
- Non-Gmail channels in V1 unless the client explicitly changes scope.
- Training or fine-tuning a model on client content.
- Copying client production emails, knowledge, credentials, logs, or brand assets into the founder SaaS.
- Claims of SOC 2, ISO 27001, HIPAA, GDPR certification, or other assurance not independently completed.
- Replacing the client’s helpdesk/CRM beyond the agreed triage and reply-draft workflow.

## Data classes and boundaries

| Data | System of record | Boundary |
|---|---|---|
| Users, membership, roles, queues, tickets, messages, decisions, rules, metadata | Client Cloud SQL | Client project and organization scope |
| Embeddings and retrieval indexes | Client Cloud SQL with pgvector | Same organization and environment as source chunks |
| Uploaded knowledge and permitted attachments | Private client Cloud Storage | No public objects; malware/type/size policy applies |
| OAuth refresh tokens, LLM keys, signing keys | Client Secret Manager/KMS | Never exposed to browser, logs, analytics, exports, or ordinary database columns |
| Logs, metrics, traces, audit events | Client GCP operations stack and database | Redacted; access limited and audited |
| Local MVP SQLite/Qdrant data | Developer-only migration source | Must not become production storage or be copied into client production without approval |

Production data must not flow to development, demos, analytics, or the founder SaaS. Synthetic or explicitly sanitized fixtures are required outside the client environments.

## Ownership and responsibility

| Asset or decision | Owner |
|---|---|
| GCP organization/projects, billing, production database, backups, logs | Client |
| Google Workspace admin approval, OAuth app, mailbox authorization | Client |
| Client LLM accounts, API keys, provider spend, allowed regions/models | Client |
| Client email, knowledge, users, configuration, retention decisions | Client |
| Application implementation and agreed warranty/support work | Delivery team under the client contract |
| Generic founder SaaS code and branding | Separate founder environment, only to the extent the contract permits reuse |

The signed client agreement controls intellectual-property ownership and reuse. This plan is an engineering boundary, not a substitute for contract review.

## Proposed non-functional baselines

These are safe planning defaults, not contractual promises:

- Availability target: 99.5% monthly for V1 after planned maintenance, pending client agreement.
- Recovery targets: RPO 24 hours and RTO 4 hours initially, pending business-impact review.
- Interactive application reads: p95 under 2 seconds where no model/provider call is required.
- Triage processing: visible progress and bounded timeout; proposed p95 under 30 seconds under agreed load.
- Ingestion: at-least-once delivery with idempotent processing and reconciliation.
- Accessibility: keyboard-operable critical journeys, visible focus, WCAG AA color contrast target.
- Browser support: current stable Chrome, Edge, Safari, and Firefox.
- Security: no committed secrets; least privilege; encryption in transit/at rest; critical/high findings block production.

## Inputs requiring client confirmation

| Input | Safe current assumption | Required before |
|---|---|---|
| Legal client name and contract/reuse terms | Unknown; no reuse assumed beyond this repository until confirmed | C01 extraction and any later founder reuse |
| GCP organization, project IDs, billing owner, deployment administrators | Client-owned; identifiers not yet supplied | C02 apply/deploy |
| Primary Workspace domains and invited launch users | Domain allowlist plus explicit invitations | C03 production configuration |
| Data residency/processing region | One client-approved GCP region; no cross-region model fallback | C02 final design |
| Retention, deletion, attachment, and legal-hold rules | Minimize storage; no indefinite retention | C04/C13 |
| Initial departments, queues, roles, SLAs, hours, time zones | Support, Billing, Technical, Security; exact mapping TBD | C04/C09 |
| Initial Gmail mailboxes and whether each is mailbox, alias, or Group | Start with 1-2 shared mailboxes | C06 |
| Expected daily/peak messages and attachment sizes | Load-test 50 mailbox connections with synthetic events; volume TBD | C02/C07 |
| Approved LLM and embedding providers/models/regions/budgets | OpenAI first; no silent cross-provider fallback | C10 |
| Whether human-approved Gmail draft creation is desired | Yes; actual sending remains disabled | C12 |
| Availability, RPO/RTO, support window, escalation contacts | Proposed baselines above | C13/C14 |

Unknown values do not authorize external setup. They are explicit gates for their consuming phases.

## V1 acceptance summary

The client V1 is acceptable only when:

1. Authentication, roles, mailbox permissions, and organization scoping pass adversarial tests.
2. A mailbox event is ingested idempotently and becomes a correctly threaded ticket.
3. The visible trace shows classification, guardrails, MCP request, retrieved passages, reranking, grounded draft, citations, validator result, and final human-review disposition.
4. Quality Check measures stored client knowledge and meets agreed safety/retrieval thresholds.
5. No automatic-send path exists.
6. Backup/restore, rollback, revocation, deletion, monitoring, audit, and incident runbooks are proven in staging.
7. Client UAT and production go/no-go are signed.
