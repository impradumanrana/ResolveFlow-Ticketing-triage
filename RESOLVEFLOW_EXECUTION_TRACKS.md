# ResolveFlow AI - Execution Track Selector

**Decision status:** Final planning split

**Rule:** Execute only one track at a time. Keep one reusable product core; do not create two unrelated applications.

## Choose the track

| Track | Use it when | Cloud ownership | Billing inside ResolveFlow | Exact instruction to give Codex |
|---|---|---|---|---|
| **CLIENT** | Building and deploying the first private version for one named client | Client owns the GCP organization/project, billing, domain, OAuth app, secrets, and production data | None | `Execute CLIENT plan, phase C00 only.` |
| **FOUNDER_SAAS** | Turning the proven product into a public multi-tenant subscription business | Founder/company owns the cloud, SaaS domains, Stripe account, and operating environment | Monthly/yearly plans, seats, mailbox capacity, and usage entitlements | `Execute FOUNDER_SAAS plan, phase S00 only.` |

Do not say only “start building.” Always include the track and phase ID. Complete and commit one green phase before starting the next.

## Recommended order

1. Execute **CLIENT C00-C15** and launch a dedicated client production environment.
2. Run the client pilot, collect evidence, and stabilize the reusable core.
3. Confirm the contract permits reuse of generic code and product concepts.
4. Create a clean founder-owned repository and cloud environment with no client data, secrets, branding, or confidential configuration.
5. Execute **FOUNDER_SAAS S00-S12**.

This order produces a real deployment and reference customer before adding public signup, tenant self-service, subscription billing, and SaaS support.

## Shared product core

Both tracks should share:

- Next.js customer-facing app; Streamlit remains an internal prototype/evaluation reference only.
- Python triage, RAG, MCP, guardrail, and evaluation services behind authenticated APIs.
- PostgreSQL with pgvector hybrid retrieval and organization-scoped records.
- Multiple Gmail/shared mailboxes, departments, queues, SLAs, assignments, and per-user unseen state.
- Organization-owned knowledge ingestion, citations, versioning, quality checks, and safe deletion.
- BYOK model credentials stored in a managed secret store.
- Deterministic guardrails, grounded drafts, human review, and complete audit history.
- Read-only/Observe Mode first. No automatic customer sending.

The client deployment still stores an `organization_id` on tenant-owned records and enforces authorization. It is initialized with one organization, avoiding an expensive data-model rewrite when the generic core later becomes SaaS.

## Features that must remain track-specific

### CLIENT only

- One client organization and an allowlisted email domain.
- Admin-provisioned users; no public signup.
- Client-owned GCP, OAuth consent configuration, API keys, database, backups, and logs.
- No pricing page, trials, Stripe, subscription status, plan comparison, or self-service upgrades.
- Client branding and client-specific retention/configuration live only in the client deployment layer.

### FOUNDER_SAAS only

- Public or invite-based workspace creation for many independent customers.
- Strict cross-tenant isolation testing and tenant-aware operations.
- Stripe subscriptions, monthly/yearly billing, entitlements, quotas, invoices, dunning, and plan changes.
- Founder-operated onboarding, support, incident response, privacy workflows, and product analytics.
- Generic branding and configuration; never copy client data or confidential material.

## Hosting decision

### Client v1: client-owned Google Cloud

Use separate client-owned GCP staging and production resources:

- Cloud Run: Next.js web/BFF, private Python AI API, ingestion worker, and scheduled jobs.
- Cloud SQL for PostgreSQL: transactional data, full-text indexes, and pgvector embeddings.
- Pub/Sub and Cloud Tasks: Gmail notifications, durable jobs, retries, and dead-letter handling.
- Secret Manager plus Cloud KMS: OAuth tokens, model keys, signing secrets, and envelope encryption.
- Cloud Storage: encrypted knowledge files and permitted attachments.
- Artifact Registry and Cloud Build or GitHub Actions with Workload Identity Federation: images and deployment.
- Cloud Logging, Monitoring, Error Reporting, audit logs, backups, and budget alerts.

This is preferred over splitting v1 between Vercel and GCP because the Gmail event pipeline, Python workers, secrets, database, and operations already belong on GCP. One cloud simplifies IAM, logging, network boundaries, billing ownership, incident response, and handover.

### Founder SaaS: founder-owned GCP first

Start on the same portable container architecture in founder-owned GCP. Vercel may later host only the Next.js web tier if preview deployments or frontend delivery materially justify a second control plane. Keep APIs, workers, Gmail events, database, and secrets on GCP. Do not depend on Vercel-specific behavior unless recorded in `DECISIONS.md`.

## Non-negotiable ownership boundary

Before building the founder SaaS, review the client agreement for intellectual-property ownership, work-for-hire terms, confidentiality, and reuse rights. Reuse only generic code and ideas you are legally entitled to reuse. Never export the client’s email, knowledge base, prompts containing confidential content, credentials, logs, users, branding, or production configuration.
