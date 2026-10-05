# ResolveFlow AI - Founder-Owned SaaS Plan

**Track ID:** `FOUNDER_SAAS`

**Target:** A generic, multi-tenant product sold on monthly or yearly subscriptions

**Prerequisite:** The dedicated client pilot is stable, and contractual reuse rights have been verified.

## 1. Product outcome

ResolveFlow becomes a founder-operated SaaS where independent organizations create isolated workspaces, invite teams, connect authorized mailboxes, ingest their knowledge, connect model providers, run grounded triage, review drafts, and pay for capacity. Founder cloud resources, domains, Stripe, monitoring, and support are completely separate from the client environment.

Detailed architecture, multi-mailbox behavior, provider strategy, and pricing remain in:

- `RESOLVEFLOW_PRODUCTION_SAAS_UPGRADE_PLAN.md`
- `RESOLVEFLOW_PRODUCTION_STEP_BY_STEP_CODEX_PROMPTS.md`

Those documents belong to this founder track, not the dedicated client deployment.

## 2. Hosting decision

Use founder-owned GCP initially with the same portable container boundaries proven for the client:

- Cloud Run for Next.js, Python APIs, workers, and jobs.
- Cloud SQL PostgreSQL/pgvector for tenant data and hybrid retrieval.
- Pub/Sub, Cloud Tasks, Scheduler, Storage, Secret Manager/KMS, Artifact Registry, and observability.
- Separate development, staging, and production projects and databases.

Vercel is optional later for the Next.js web tier. Adopt it only after measuring a real benefit and documenting cross-cloud latency, authentication, previews, secrets, logs, incidents, residency, and cost. Never move ingestion or mailbox workers into the web request path.

## 3. Founder SaaS phases

### S00 - Legal and clean-room boundary

Review ownership/reuse rights. Establish founder-owned repository, domains, cloud organization, billing, security contacts, and vendors. Import no client data, secrets, confidential prompts, branding, or environment configuration.

**Gate:** Written reuse decision and automated secret/history scan are clean; founder resources are independent.

### S01 - Reusable-core extraction

Promote generic Next.js, Python AI, RAG, MCP, connectors, and schemas into a configurable core. Isolate client-specific behavior. Add `DEPLOYMENT_PROFILE=saas` without weakening authorization.

**Gate:** Client and SaaS builds pass independently and share no secrets, IDs, assets, or data.

### S02 - Multi-tenant isolation

Enable workspace creation/switching, memberships, departments, roles, invitations, and tenant-aware jobs. Enforce server-derived organization context, RLS where applicable, object authorization, and scoped caches/files/secrets/vector queries.

**Gate:** Adversarial isolation tests cannot read, infer, mutate, enqueue, retrieve, or export another tenant’s data.

### S03 - Self-service onboarding

Build signup/invite, workspace, mailbox, knowledge, model, sample test, readiness, recovery, and offboarding flows.

**Gate:** A new customer reaches a grounded draft without developer/database intervention and can disconnect/delete fully.

### S04 - SaaS mailbox control plane

Harden OAuth verification, encrypted connections, mailbox capacity, quotas, watch lifecycle, reconnection, aliases, Groups, audit, and support tools. Keep domain-wide delegation enterprise-only.

**Gate:** Revocation, quota, expiry, and 50-mailbox-per-org load tests pass across tenants.

### S05 - Multi-provider BYOK

Offer OpenAI, Anthropic, Gemini, Azure OpenAI, Bedrock, NVIDIA NIM, and approved compatible endpoints through one gateway. Add capability discovery, health, workload routing, explicit fallback, region/policy constraints, masked credentials, estimates, budgets, and embedding migrations.

**Gate:** Credential, budget, schema, fallback, region, and embedding-version tests pass; UI never falsely claims cash balance.

### S06 - Subscription billing and entitlements

Implement accepted monthly/yearly pricing from the SaaS upgrade plan with Stripe Checkout/Portal, verified idempotent webhooks, subscription states, seats, full/light roles, mailbox packs, usage, plan changes, invoices, tax, dunning, grace, and safe downgrade.

**Gate:** Test-clock and replay suites prove lifecycle transitions; billing never deletes or exposes data.

### S07 - SaaS operations

Add audited break-glass support, organization health, metering, abuse controls, communications, privacy requests, flags, rollouts, backups, restore, incidents, and on-call ownership.

**Gate:** Restore, rollback, deletion, incident, and controlled-support-access drills pass.

### S08 - Security and compliance readiness

Complete independent review, Gmail restricted-scope verification/assessment planning, vulnerability management, vendor inventory, privacy terms/DPA, retention, subprocessors, audit evidence, regional controls, and SSO/SCIM roadmap.

**Gate:** No open critical/high issue; public claims match completed evidence.

### S09 - Closed beta

Onboard a few separate organizations in Observe Mode, run per-customer evaluations, and measure reliability, latency, cost, and operator load.

**Gate:** Accuracy, grounding, connector reliability, isolation, latency, unit-cost, and support thresholds pass.

### S10 - Public product and sales surface

Publish positioning, pricing, security/privacy/terms, documentation, onboarding, status/support, and synthetic demos. Instrument acquisition without collecting sensitive ticket content.

**Gate:** Legal, billing, support, accessibility, SEO, analytics privacy, and smoke tests pass.

### S11 - Controlled sending and enterprise access

After proven drafts, add human-approved sending with scopes, preview, idempotency, audit, limits, and kill switches. Evaluate domain-wide delegation only for a contracted enterprise with allowlists and review.

**Gate:** No autonomous send path; authorization and duplicate-send adversarial tests pass.

### S12 - General availability

Use progressive rollout, capacity plans, SLOs, error budgets, rollback, communications, support rotations, reconciliation, and post-launch review.

**Gate:** Formal go/no-go is signed and blockers are closed.

## 4. Exact Codex instruction

```text
Execute FOUNDER_SAAS plan, phase S00 only, from RESOLVEFLOW_FOUNDER_SAAS_PLAN.md.
Read RESOLVEFLOW_EXECUTION_TRACKS.md, RESOLVEFLOW_PRODUCTION_SAAS_UPGRADE_PLAN.md, and the existing SaaS prompt sequence first. Stop at the S00 gate, run proportional tests, update AI_HANDOFF.md and PROGRESS.md, and report evidence, risks, and the exact next phase. Do not deploy or make external changes without explicit approval.
```

If the client product is live, point Codex only at the clean founder repository containing legally reusable core—never at client production data or credentials.

## 5. Existing detailed prompts

`RESOLVEFLOW_PRODUCTION_STEP_BY_STEP_CODEX_PROMPTS.md` remains the detailed implementation sequence. During S00, map its numbered prompts to S00-S12 in `PROGRESS.md`; skip nothing silently. If client work already appears to satisfy a gate, rerun the tests in the founder environment and record evidence.
