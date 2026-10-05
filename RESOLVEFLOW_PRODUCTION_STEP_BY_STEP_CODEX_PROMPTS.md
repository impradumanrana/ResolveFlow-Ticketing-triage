# ResolveFlow AI - Production SaaS Step-by-Step Codex Prompts (Next.js Edition)

> **Execution track:** `FOUNDER_SAAS`. Do not use this billing/multi-tenant sequence for the dedicated client v1. Start with `RESOLVEFLOW_EXECUTION_TRACKS.md` and select the intended track explicitly.

## How to use this file

Run one prompt at a time from the existing MVP project root. Do not paste the entire file into Codex.

Start with **Prompt 00**. Copy only the text inside its fenced `text` block. After it finishes, confirm its gate is green before copying Prompt 01. Continue in the Recommended execution order near the end of this file.

After every step:

1. Review the result, git diff, and actual test output.
2. Do not continue if the prompt reports BLOCKED, NO-GO, unreviewed destructive changes, or a failed gate.
3. Commit the green step.
4. Start a fresh Codex context if desired.
5. Paste only the next prompt's fenced `text` block.

The prompts use `AI_HANDOFF.md` as compact working memory so later steps do not need to reread the entire repository history.

## Source of truth and drift control

- The user's current request and explicit approvals always control the work.
- `RESOLVEFLOW_PRODUCTION_SAAS_UPGRADE_PLAN.md` is the accepted product, architecture, security, multi-mailbox, BYOK, and pricing source of truth.
- This file is the execution sequence. It must implement the accepted plan without silently expanding scope or weakening its gates.
- Existing `README.md` and `docs/*.md` describe the current hackathon MVP until Prompt 00 audits and classifies them; their Streamlit implementation details are not production-frontend instructions.
- `DECISIONS.md` records later approved deviations. If documents conflict and no approved decision resolves the conflict, stop and report it instead of guessing.

## Mandatory root documents

Prompt 00 creates or normalizes these files in the project root:

- `PRODUCTION_PLAN.md`
- `ARCHITECTURE.md`
- `DATA_MODEL.md`
- `SECURITY.md`
- `API_CONTRACTS.md`
- `DECISIONS.md`
- `AI_HANDOFF.md`
- `PROGRESS.md`
- `TEST_REPORT.md`
- `DEPLOYMENT.md`
- `RUNBOOK.md`
- `FRONTEND.md`
- `AI_PROVIDERS.md`
- `PRICING.md`

Every later prompt reads `AI_HANDOFF.md`, `DECISIONS.md`, `PROGRESS.md`, and the documents explicitly named in that prompt. This provides continuity without wasting context on every Markdown file.

## Global rules for every prompt

- Work in the current repository. Use one coherent repository or documented monorepo layout; never create a throwaway duplicate application.
- Inspect before editing and preserve working MVP behavior.
- The customer-facing production application is Next.js App Router + React + TypeScript. This decision is locked unless the user explicitly changes it.
- Treat Streamlit as a temporary prototype, regression reference, and internal diagnostic tool only. Do not add new production features to it and do not deploy it as the customer-facing SaaS.
- Preserve the proven Python triage, RAG, MCP, guardrail, and evaluation code behind authenticated internal service APIs. Do not rewrite it in TypeScript merely to share the frontend language.
- Prefer Tailwind CSS, shadcn/ui or Radix primitives, Lucide icons, Zod, React Hook Form, and TanStack Query where they fit the audited repository. Do not install overlapping UI libraries.
- Use React Server Components for initial authenticated reads where practical. Use Client Components only for interaction, browser APIs, or live state.
- Keep secrets and privileged Supabase/service credentials in server-only modules. Never import them into code reachable by the browser.
- Never store raw organization LLM keys in application database columns, logs, analytics, traces, exports, error messages, browser storage, or client bundles. Store only a tenant-scoped secret-manager reference and masked metadata.
- Separate ordinary inference credentials from optional provider billing/admin credentials. Never request billing/admin privilege just to run inference.
- Do not call estimated spend a provider balance. Label provider-reported usage, ResolveFlow-estimated cost, and internal budget remaining separately with freshness timestamps.
- Never silently move a BYOK workload onto a ResolveFlow-owned key, a more expensive model, an incompatible embedding model, or a disallowed processing region.
- Every customer-facing page must have accessible loading, empty, error, disabled, and retry states and work at mobile, tablet, laptop, and wide desktop sizes.
- Queue filters, sorting, pagination, and selected-ticket state should be URL-backed where practical.
- Never hardcode secrets, tokens, tenant IDs, user IDs, mailbox addresses, or example production URLs.
- All tenant-owned data must be scoped by a server-derived `organization_id`.
- Do not trust `organization_id`, role, permissions, mailbox, or department claims supplied by the browser.
- Treat members, independently connected mailboxes, send identities/aliases, and processed-ticket usage as separate concepts and separate entitlements.
- A low-seat customer may connect many authorized mailboxes; never force seat count to match mailbox count.
- Use ResolveFlow per-user seen state for new/unseen UI. Do not treat Gmail's shared `UNREAD` label as proof that every agent has or has not seen a ticket.
- Never make the browser poll every connected mailbox. Ingestion belongs in durable workers; the UI consumes tenant-authorized normalized ticket events.
- Read-only and Observe Mode are the default.
- Never auto-send customer replies during these steps.
- Deterministic authorization and safety rules override all model suggestions.
- Treat emails, attachments, imported knowledge, and ticket text as untrusted input.
- Model/tool failures must fail safely to human review.
- Never store hidden chain-of-thought. Store concise decision evidence, rule codes, citations, timings, and outcomes.
- External writes require a permission check immediately before the provider call and an idempotency key.
- Add migrations; do not destructively reset shared/staging/production data.
- Run the narrowest relevant tests during development and the full required gate before finishing.
- Update `AI_HANDOFF.md`, `PROGRESS.md`, `TEST_REPORT.md`, `DECISIONS.md`, and affected technical documents at the end of every step.
- Do not start the next numbered step.
- Do not claim a test passed unless it was actually run.

---

# Stage A - Establish the production foundation

## Prompt 00 - Audit the current MVP and freeze the migration plan

```text
You are upgrading the existing ResolveFlow AI Support Ticket Triage MVP into a production multi-tenant SaaS.

This is an AUDIT AND DOCUMENTATION step only. Do not implement production features yet.

FIRST

1. Inspect the complete repository, including source, tests, configuration, dependency files, database files, migrations, Docker/deployment files, and git status.
2. Read every Markdown file currently in the project root.
3. Locate the existing triage graph, MCP server/client, model adapter, knowledge ingestion/retrieval, fixtures, UI, state storage, and tests.
4. Inventory every Streamlit screen and capture its workflow, data dependency, useful behavior, and acceptance criteria. Mark each screen REBUILD IN NEXT.JS, KEEP AS INTERNAL DIAGNOSTIC, or RETIRE.
5. Determine whether Next.js already exists. If it does, preserve its conventions. If it does not, document the minimum production app layout without scaffolding it in this audit step.
6. Run the currently documented test and startup commands. Record actual results.
7. Preserve uncommitted user changes and identify any overlap before editing documentation.

TARGET PRODUCT

ResolveFlow AI must eventually provide:

- organizations/workspaces and tenant isolation
- users, invitations, roles, departments, queues, working hours, and SLAs
- multiple independently authorized Gmail shared/team mailbox connections per organization
- durable per-mailbox Gmail Pub/Sub/history ingestion, failure isolation, and unified queues
- deterministic routing rules
- organization-owned versioned knowledge
- structured AI triage and grounded reply drafts
- human approval and audited provider actions
- optional remote MCP gateway with OAuth 2.1
- finalized workspace/ticket-volume pricing, independent seat/mailbox entitlements, transparent usage/overages, billing, security, observability, and deletion/export workflows
- a polished Next.js SaaS UI; Streamlit is not a production dependency
- tenant-owned LLM API connections, provider-neutral workloads, audited fallback, model qualification, usage estimates, and organization budget controls

The first beta must remain Observe/Draft mode. Do not auto-send.

CREATE OR UPDATE THESE ROOT DOCUMENTS

- PRODUCTION_PLAN.md: phased scope, non-goals, gates, and dependency order.
- ARCHITECTURE.md: current architecture, target architecture, service boundaries, queues, data flow, and migration strategy.
- DATA_MODEL.md: proposed tables, ownership, tenant keys, unique constraints, and lifecycle states.
- SECURITY.md: threat model, trust boundaries, auth, tenant isolation, secrets, Gmail scopes, prompt injection, and data retention.
- API_CONTRACTS.md: current and planned API/event/tool contracts.
- DECISIONS.md: dated decisions, alternatives, and unresolved decisions.
- AI_HANDOFF.md: at most 120 lines containing current state, exact commands, invariants, blockers, and the next step.
- PROGRESS.md: done/current/next/blockers.
- TEST_REPORT.md: commands and actual baseline results.
- DEPLOYMENT.md: current environments and target deployment model without invented values.
- RUNBOOK.md: placeholder operational procedures and incident categories.
- FRONTEND.md: Next.js route map, authenticated layout, component system, state/data-fetching rules, responsive behavior, accessibility requirements, and Streamlit screen migration matrix.
- AI_PROVIDERS.md: provider adapter contract, capability registry, secret lifecycle, workload routing, fallback rules, usage/cost semantics, supported providers, evaluation gates, and staged rollout.
- PRICING.md: finalized launch plans, included entitlements, low-seat/high-mailbox examples, billable-ticket definition, overages, trials, BYOK cost treatment, and billing-state behavior.

REQUIRED OUTPUT

- Current-state map with file paths.
- Reuse/refactor/replace decision for each major MVP component.
- Explicit Next.js-to-Python boundary and deployment topology.
- Reuse/refactor decision for the current OpenAI adapter and a staged BYOK provider rollout that starts with one complete vertical slice rather than many incomplete integrations.
- Reconcile PRODUCTION_PLAN.md and PRICING.md with `RESOLVEFLOW_PRODUCTION_SAAS_UPGRADE_PLAN.md`; record any deliberate deviation instead of silently changing a locked decision.
- Exact first migration slice.
- Blocking decisions that genuinely require user input.
- Risks ranked P0/P1/P2.
- No feature implementation.

Stop after documentation and baseline verification.
```

### Gate 00

- Current tests/startup are reproducible or failures are documented.
- Target architecture matches the actual repository.
- No production code was prematurely added.

## Prompt 01 - Add production configuration and service boundaries

```text
Continue ResolveFlow AI from the audited state.

READ FIRST

- AI_HANDOFF.md
- DECISIONS.md
- PROGRESS.md
- ARCHITECTURE.md
- SECURITY.md
- DEPLOYMENT.md
- Current git diff/status

Implement only the production configuration, Next.js shell, and service-boundary foundation selected in Prompt 00.

REQUIREMENTS

1. Create typed environment/configuration loading for development, test, staging, and production.
2. Validate required variables at process startup with useful errors.
3. Keep secrets out of client bundles and logs.
4. Separate concerns into the minimum viable boundaries documented by the audit:
   - Next.js web/control plane
   - background worker
   - connector/provider adapters
   - authenticated Python triage/knowledge service
5. Add health/readiness checks that do not leak configuration.
6. Add correlation/request IDs and structured logging with sensitive-field redaction.
7. Add local development commands and a deterministic test configuration.
8. Scaffold or normalize the Next.js App Router application with strict TypeScript, linting, server-only environment validation, Tailwind, the selected accessible component primitives, and a minimal responsive public/authenticated layout.
9. Add route-level loading, error, and not-found states plus a neutral design-token foundation for color, type, spacing, radius, focus, and motion.
10. Define a typed internal service client from Next.js to Python with timeout, correlation ID, authentication placeholder, error normalization, and no browser exposure of the internal service URL.
11. Do not add Gmail, organizations, billing, or real product features yet.

VERIFY

- Existing MVP flow still works.
- Missing production configuration fails closed.
- Tests never require real credentials.
- Secret-like test fixtures do not appear in logs.
- Run formatting, type checks, focused tests, and the existing full test command.

Update the handoff and technical documents with actual commands/results. Stop here.
```

### Gate 01

- Web/API and worker boundaries start independently.
- Next.js and Python services start independently through documented commands and communicate only through the defined internal contract.
- Configuration is typed and secrets are server-only.
- MVP behavior remains green.

## Prompt 02 - Build the multi-tenant database and Row Level Security

```text
Continue from the current green state.

READ FIRST

- AI_HANDOFF.md
- DECISIONS.md
- PROGRESS.md
- DATA_MODEL.md
- SECURITY.md
- ARCHITECTURE.md
- Existing schema and migration history

Implement the tenant foundation only.

CORE TABLES

- organizations
- user_profiles only if the auth provider requires it
- organization_memberships
- invitations
- audit_events

Add `organization_id` to every existing tenant-owned MVP table that will survive the migration. Use staged, reversible migrations appropriate to the current database. Do not reset or discard data.

REQUIREMENTS

1. Define ownership, foreign keys, cascade/restrict behavior, timestamps, status enums, and unique constraints.
2. Enable and implement Row Level Security for browser-accessible tenant tables.
3. Derive organization access from the authenticated organization membership.
4. Never accept organization scope from an unverified request body/query parameter.
5. Add service-layer tenant context for trusted background jobs.
6. Create an immutable audit-event writer with actor type: user, service, system, or MCP client.
7. Backfill existing local MVP rows into one clearly identified development organization without affecting production-like environments.
8. Add tests that create two organizations and attempt cross-tenant reads, writes, updates, deletes, retrieval, and audit access.

Do not build onboarding UI, departments, Gmail, or billing yet.

VERIFY

- Migrations apply to a clean test database.
- Upgrade migration works on a copy/fixture of the existing schema.
- Cross-tenant tests fail closed.
- Existing triage tests remain green.

Update DATA_MODEL.md with the implemented schema and SECURITY.md with proven isolation behavior. Stop here.
```

### Gate 02

- Automated cross-tenant tests pass.
- All retained tenant data has a non-null organization owner.
- Migrations are reversible or have an explicit rollback procedure.

## Prompt 03 - Add authentication, workspace onboarding, and invitations

```text
Continue from the green multi-tenant database state.

READ FIRST

- AI_HANDOFF.md
- DECISIONS.md
- PROGRESS.md
- DATA_MODEL.md
- SECURITY.md
- API_CONTRACTS.md
- FRONTEND.md
- Existing authentication/UI code

Implement organization onboarding and member invitations.

USER FLOWS

1. Sign up/sign in using the selected existing auth provider.
2. Create a workspace with name, timezone, locale, default language, and business-hours placeholder.
3. Land in an onboarding checklist.
4. Invite a teammate by email with one-time, expiring, hashed invitation tokens.
5. Accept, expire, revoke, and resend invitations safely.
6. Switch organizations only when the user belongs to both.
7. Sign out and revoke sessions according to the auth provider.

REQUIREMENTS

- First creator becomes Owner in one transaction.
- Invitations cannot be used by a different intended email unless the documented policy permits it.
- Rate-limit invitation creation and acceptance.
- Record security-relevant audit events.
- Provide accessible loading, expired-link, already-used, and unauthorized UI states.
- Add server-side authorization to every new route/action.
- Build the flow in Next.js App Router using server-derived session and organization context. Never use middleware or client-side redirects as the only authorization layer.
- Create explicit routes for sign-in, onboarding, invitation acceptance, and the authenticated application shell.
- The authenticated shell includes responsive sidebar/drawer navigation, organization switcher, user menu, breadcrumbs where useful, and role-aware links.
- Validate forms on both client and server from shared Zod schemas where practical. Return field-level, accessible errors without leaking account-existence details.
- No Gmail connection yet.

VERIFY

- End-to-end tests for create workspace, invite, accept, revoke, expiry, replay, and unauthorized access.
- Two-organization switching does not leak cached state.
- Existing tests remain green.

Update docs and stop here.
```

### Gate 03

- A company can create a workspace and invite a teammate.
- Invitation replay and cross-organization access are denied.

## Prompt 04 - Add roles, departments, queues, and team management

```text
Continue from the green onboarding state.

READ FIRST

- AI_HANDOFF.md
- DECISIONS.md
- PROGRESS.md
- DATA_MODEL.md
- SECURITY.md
- API_CONTRACTS.md
- FRONTEND.md
- Relevant organization/membership code and tests

Implement production RBAC and support-team organization.

ROLES

- OWNER
- ADMIN
- MANAGER
- AGENT
- AUDITOR

DOMAIN

- departments
- department_members
- queues
- business_hours
- holidays
- membership access tier: FULL, LIGHT, or BILLING_ONLY, separate from role
- member availability/capacity fields only if required by the documented assignment design

REQUIREMENTS

1. Define a permission matrix in code and SECURITY.md.
2. Enforce permissions server-side; UI hiding is not authorization.
3. Owners manage billing/security/integrations; Admins manage operational configuration; Managers manage permitted departments; Agents work permitted queues; Auditors are read-only.
4. Keep role separate from paid access tier. FULL may perform permitted triage/draft/approve/send work; LIGHT may view permitted data, add internal notes, and participate in allowed approvals but cannot independently send; BILLING_ONLY may manage subscription surfaces without message-body access and does not consume a full seat.
5. Never silently upgrade a member's paid access tier when changing role or department; require an authorized explicit change and future entitlement check.
6. Prevent removal/demotion of the last active Owner.
7. Allow users to belong to multiple departments.
8. Add department and queue CRUD with archive rather than destructive deletion where history exists.
9. Add member invitation role, access tier, and department selection.
10. Audit role, access-tier, department, queue, and membership changes.
11. Build polished Next.js Team and Departments pages using reusable table, dialog/drawer, form, badge, pagination, confirmation, and toast patterns. Include empty/error/loading/permission-denied states and responsive alternatives to wide tables.

Do not implement SLA calculation or assignment algorithms yet.

VERIFY

- Permission tests cover every role and sensitive action.
- Department-restricted users cannot access another department's tickets/knowledge placeholders.
- Last-owner protections pass.
- Role/access-tier combinations and FULL/LIGHT/BILLING_ONLY restrictions pass without relying on UI hiding.
- Full existing suite remains green.

Update docs and stop here.
```

### Gate 04

- RBAC is enforced in APIs/database access, not just the interface.
- Team and department management work across two test organizations.

---

# Stage B - Connect and ingest multiple shared/team Gmail mailboxes

## Prompt 05 - Create the channel connector domain and credential vault

```text
Continue from the green tenant/team foundation.

READ FIRST

- AI_HANDOFF.md
- DECISIONS.md
- PROGRESS.md
- ARCHITECTURE.md
- DATA_MODEL.md
- SECURITY.md
- API_CONTRACTS.md

Implement the provider-neutral channel connection foundation. Do not call Gmail yet.

ADD

- channel_connections
- mailboxes
- mailbox_departments
- mailbox_memberships / mailbox_permissions
- mailbox_send_identities
- mailbox_sync_states
- credential references/encrypted credential envelopes
- connection_health_events
- sync_cursors
- webhook_events
- ingestion_jobs or the equivalent selected queue model

CONNECTION STATES

`PENDING`, `ACTIVE`, `DEGRADED`, `REAUTH_REQUIRED`, `DISCONNECTED`, `REVOKED`.

PERMISSION LEVELS

`READ_ONLY`, `DRAFT_ONLY`, `APPROVED_SEND`.

REQUIREMENTS

1. Provider adapter interface for connect, disconnect, health, initial sync, incremental sync, and guarded action.
2. Credential encryption using the repository's documented key-management approach; never store plaintext refresh tokens.
3. Credentials, mailboxes, permissions, send identities, cursors, jobs, and events are organization-scoped.
4. Model actual provider mailboxes separately from aliases/send identities. A real mailbox has independent authorization/sync state; an alias does not create a fake watch or cursor.
5. Model viewer, triager, drafter, approver, and sender permissions separately from department ownership and aggregate-metrics access.
6. Idempotency and unique provider identifiers.
7. Explicit per-mailbox pause, reconnect, disconnect, revoke, and deletion lifecycle plus an organization-wide connector kill switch.
8. Sanitized connection diagnostics and bulk health summaries for admins.
9. Permission check is callable immediately before every future provider read or write.
10. Fake connector supporting at least 50 simulated mailboxes for deterministic concurrency, pagination, failure-isolation, and entitlement tests.

VERIFY

- Credential round-trip/rotation tests without logging plaintext.
- Cross-tenant connector tests.
- Duplicate webhook/job tests across multiple mailboxes.
- One degraded mailbox does not block another.
- Revoked connections cannot be used.
- A one-person organization can be authorized for many mailboxes without creating fake users.

Update docs and stop before Gmail OAuth.
```

### Gate 05

- Credentials are encrypted and tenant-isolated.
- Connector behavior can be tested without an external provider.

## Prompt 06 - Add Google OAuth for multiple read-only shared/team mailboxes

```text
Continue from the green connector foundation.

READ FIRST

- AI_HANDOFF.md
- DECISIONS.md
- PROGRESS.md
- SECURITY.md
- API_CONTRACTS.md
- DEPLOYMENT.md
- Existing connector interfaces/tests

Implement Google OAuth for multiple explicitly authorized Gmail support mailboxes per organization in READ_ONLY mode. Complete the vertical slice with two dedicated test mailboxes in one organization; design and test the connection list for at least 50.

Use current official Google OAuth/Gmail documentation. Request the narrowest scope required to read message content. Do not add compose/modify/send scopes in this step.

REQUIREMENTS

1. Owner/Admin can initiate “Connect Gmail”; the actual mailbox account completes OAuth unless approved enterprise DWD is used.
2. Bind OAuth state to the authenticated organization, initiating actor, pending connection, nonce, expiry, and exact redirect intent; prevent cross-organization callback substitution and replay.
3. Use Authorization Code flow with PKCE/state protection where applicable and offline access for background sync.
4. Validate exact environment-specific redirect URIs.
5. Encrypt each refresh token under a distinct tenant/connection secret reference; do not persist access tokens longer than necessary.
6. Fetch and verify the authenticated mailbox identity after consent and prevent the same provider mailbox from being ambiguously connected twice in one organization.
7. Require confirmation of display name, color, department owner, permitted members, retention, business hours, and mailbox permission before activation.
8. Store granted scopes and display them per connection.
9. Distinguish a real Gmail mailbox from a send-as alias and a Google Group/Collaborative Inbox. Do not create independent watch state for an alias. For beta Groups, require delivery/forwarding into a connected mailbox unless a separately proven connector exists.
10. Build an accessible Connections page with add, status, filter, setup progress, reauthorize, pause, disconnect, and per-mailbox error states. It must remain usable with 50 rows through pagination/virtualization and bulk health summaries.
11. Handle denied consent, missing refresh token, revoked grant, token refresh failure, duplicate connection, and reconnect independently per mailbox.
12. Add per-mailbox disconnect/revoke controls and audit events plus an organization-wide kill switch.
13. Do not ingest messages yet.

TESTING

- Unit tests with mocked Google endpoints.
- OAuth state/PKCE/replay/redirect tests.
- Manual integration script/checklist using two dedicated test mailboxes when credentials are available.
- Tests must not send mail.

Update docs with exact Google console prerequisites but never store real client secrets. Stop here.
```

### Gate 06

- One organization can independently connect, pause, reauthorize, and disconnect two verified mailboxes.
- Another organization cannot use or inspect either connection.
- A simulated 50-mailbox list remains usable and tenant-isolated.
- Only the required read scope is requested.

## Prompt 07 - Implement Gmail Pub/Sub, history sync, and reconciliation

```text
Continue from the green read-only Gmail OAuth state.

READ FIRST

- AI_HANDOFF.md
- DECISIONS.md
- PROGRESS.md
- ARCHITECTURE.md
- DATA_MODEL.md
- SECURITY.md
- API_CONTRACTS.md
- DEPLOYMENT.md
- Gmail connection/sync code and tests

Implement reliable read-only Gmail ingestion.

REQUIREMENTS

1. Start and renew an independent Gmail watch for every active confirmed mailbox using the configured Pub/Sub infrastructure.
2. Verify/authenticate incoming Pub/Sub requests using the selected deployment architecture.
3. Persist separate mailbox `historyId`, watch expiration, last successful sync, reconciliation state, and sync health for every connection.
4. On notification, enqueue an incremental-sync job; do not perform AI or full Gmail work in the webhook request.
5. Use Gmail history synchronization to fetch changed messages/threads.
6. Normalize notifications as at-least-once and potentially out of order.
7. Deduplicate by organization, connection, mailbox, provider message ID/thread ID, and provider event/history identity.
8. Renew each watch on schedule before expiry; one renewal failure must not block other mailboxes.
9. Add periodic reconciliation and a safe recovery flow for invalid/expired history cursors.
10. Respect Gmail quotas/backoff and isolate/bound concurrency per organization and mailbox.
11. Add per-mailbox circuit/health state, dead-letter status, bulk admin diagnostics, and retry controls.
12. Never mutate Gmail.

TEST CASES

- duplicate notification
- out-of-order history event
- missing history range
- expired watch
- revoked token
- transient 429/5xx
- malformed/unauthenticated webhook
- reconnect after downtime
- two organizations receiving events concurrently
- two mailboxes in one organization receiving overlapping events
- one mailbox revoked/degraded while another continues normally
- simulated 50-mailbox watch renewal, quota, fairness, and reconciliation load

Run focused and full tests. Update deployment prerequisites, runbook, handoff, and test report. Stop here.
```

### Gate 07

- Duplicate and out-of-order events do not duplicate tickets/messages.
- Independent watch renewal, gap recovery, and mailbox failure isolation are tested.
- The 50-mailbox simulated load meets the documented fairness and latency target without unbounded concurrency.
- Webhook handlers return quickly and processing is durable.

## Prompt 08 - Build the production conversation/ticket model and live inbox

```text
Continue from green Gmail ingestion.

READ FIRST

- AI_HANDOFF.md
- DECISIONS.md
- PROGRESS.md
- DATA_MODEL.md
- ARCHITECTURE.md
- SECURITY.md
- API_CONTRACTS.md
- FRONTEND.md
- Existing MVP ticket/UI models and Gmail normalization code

Convert ingested provider data into the production support domain while preserving immutable provider evidence.

ADD OR FINALIZE

- contacts
- conversations
- messages
- attachment metadata
- tickets
- assignments
- internal_notes
- ticket_status_events
- user_ticket_seen_state
- ticket_presence_leases
- reply_composer_leases

REQUIREMENTS

1. Immutable normalized provider messages separate from mutable ticket workflow state.
2. Gmail thread-to-conversation rules that are deterministic and documented.
3. Ticket statuses: NEW, OPEN, PENDING_CUSTOMER, PENDING_INTERNAL, RESOLVED, CLOSED.
4. Direction, sender/recipient metadata, receiving mailbox, selected recipient/send identity, safe plain-text extraction, timestamps, and provider deep link.
5. Attachment metadata only initially; do not automatically download arbitrary attachments.
6. Organization and department authorization on all list/detail/search endpoints.
7. Build the Next.js workspace with **All inboxes**, department groups, individual mailbox navigation, per-user new/unseen badges, SLA-risk badges, saved views, and visible per-mailbox connection/sync health.
8. Build the live queue with server-rendered initial data, cursor pagination, URL-backed mailbox/department/status/assignee/SLA/filter/sort/search state, debounced global search, and realtime invalidation from normalized ticket events—not browser polling of provider mailboxes.
9. Build a responsive ticket workspace: source mailbox and reply identity, message thread, customer/context panel, internal notes, audit timeline, presence/collision indicators, and triage placeholder. On small screens these become navigable panels instead of compressed columns.
10. Add an Owner/Admin Inbox operations dashboard with drill-down counts per mailbox and department: received, replied, resolved, reopened, pending, unassigned, overdue/SLA-risk, first-response time, sync lag, last successful sync, watch expiry, and connector errors. Aggregate visibility must not grant message-body access.
11. Use ResolveFlow-specific per-user `seen_at`; preserve Gmail `UNREAD` separately and do not mutate provider read state in Observe Mode.
12. Use TanStack Query only for interactive refresh/mutations that benefit from it; invalidate tenant-scoped query keys after mutations and clear them on organization switch/sign-out.
13. Add route-level loading/error states, keyboard navigation, focus management, accessible status announcements, and pagination/virtualization so an unbounded queue is never rendered.
14. No LLM calls yet.

VERIFY

- HTML/plain/multipart/encoded email fixtures.
- Thread updates and reopened conversations.
- Duplicate message protection.
- Department, mailbox-content, aggregate-metric, and tenant access tests.
- Per-user seen/unseen correctness across refresh, reconnect, and two different users; Gmail unread remains independent.
- One-mailbox failure isolation and a simulated 50-mailbox navigation/search/load test.
- Next.js component and end-to-end tests for loading/empty/error/pagination, URL persistence, organization switching, mailbox switching, realtime new-ticket indicators, keyboard access, and mobile/desktop layouts.

Update docs and stop here.
```

### Gate 08

- Real read-only Gmail messages from two mailboxes appear in one tenant-isolated unified queue and in correct mailbox-specific views.
- New/unseen state is per ResolveFlow user and manager aggregates do not leak restricted bodies.
- Provider evidence and source-mailbox identity remain immutable.

---

# Stage C - Build the operational support system

## Prompt 09 - Add versioned routing rules, SLAs, and assignments

```text
Continue from the green live inbox.

READ FIRST

- AI_HANDOFF.md
- DECISIONS.md
- PROGRESS.md
- DATA_MODEL.md
- ARCHITECTURE.md
- SECURITY.md
- FRONTEND.md
- Existing MVP guardrails and current ticket/department code

Implement deterministic operational routing before adding production AI.

ADD

- rule_sets and immutable published versions
- rules and ordered conditions/actions
- rule_execution snapshots
- SLA policies/events
- assignment policies/events

MINIMUM CONDITIONS

- mailbox/channel
- sender domain
- subject/body contains phrase or safe regex
- category/urgency placeholders
- ticket age/business hours
- attachment present
- customer tags when available

MINIMUM ACTIONS

- set category/priority/tags
- route department/queue
- require manager approval
- block drafting/provider action
- set SLA policy

REQUIREMENTS

1. Draft, validate, publish, archive, clone, and rollback rulesets.
2. Explicit priority, continue/stop behavior, conflict detection, and deterministic output.
3. Dry-run a draft ruleset against historical tickets and show a diff without changing live tickets.
4. Store the exact ruleset version and matches on each execution.
5. Calculate SLA due times using organization timezone, business hours, and holidays.
6. Implement manual assignment plus one simple automatic strategy selected in DECISIONS.md.
7. Build practical Next.js Rules, SLA, and Queue settings pages with a readable condition/action builder, conflict warnings, dry-run diffs, rule version history, timezone-aware SLA previews, and responsive accessible controls.

No LLM-dependent rule conditions yet. No external writes.

VERIFY

- rule ordering/conflict/rollback tests
- DST, weekend, holiday, and after-hours SLA tests
- replay gives the same result for the same input/version
- two-tenant isolation and authorization tests
- full suite green

Update docs and stop here.
```

### Gate 09

- Routing is deterministic and replayable.
- SLA and assignment behavior is covered by edge-case tests.

## Prompt 10 - Add organization knowledge lifecycle and retrieval

```text
Continue from the green operational routing system.

READ FIRST

- AI_HANDOFF.md
- DECISIONS.md
- PROGRESS.md
- DATA_MODEL.md
- SECURITY.md
- ARCHITECTURE.md
- FRONTEND.md
- Existing MVP MCP KB implementation and Northstar test data

Upgrade the mock knowledge base into a tenant-owned governed knowledge system.

LIFECYCLE

`DRAFT -> IN_REVIEW -> PUBLISHED -> RETIRED`

REQUIREMENTS

1. Knowledge sources, articles, versions, chunks, ingestion jobs, and retrieval events.
2. Import Markdown and CSV first; PDF only if reliable extraction already exists or can be added without destabilizing the step.
3. Store source filename, checksum, article/version, section/page when available, owner, department visibility, effective dates, and status.
4. Only PUBLISHED, effective, tenant-authorized content can ground replies.
5. Implement hybrid lexical/vector retrieval if the existing stack supports it; otherwise deliver reliable lexical retrieval first and document the vector follow-up.
6. Every result returns article ID, title, version, excerpt, score, and source location.
7. Re-index asynchronously and atomically switch published versions.
8. Add retrieval evaluation using the Northstar knowledge/ticket dataset.
9. Preserve a real MCP `search_knowledge_base` tool, now tenant-aware and backed by the same retrieval service.
10. Build the Next.js Knowledge workspace for upload, review, publish, retire, version history, search preview, and ingestion errors. Large lists are paginated; uploads expose progress and safe retry; destructive lifecycle changes require explicit confirmation.

SECURITY

- File type/size limits.
- Private storage.
- No cross-tenant retrieval.
- Imported content is untrusted data, never system instructions.
- Do not log document content unnecessarily.

VERIFY

- tenant and department retrieval isolation
- draft/retired content exclusion
- version switch and rollback
- malformed file handling
- retrieval quality report on labelled fixtures
- full suite green

Update docs and stop here.
```

### Gate 10

- Retrieval citations resolve to the exact published tenant version.
- Cross-tenant and unpublished content never appears.

## Prompt 10A - Build the secure BYOK foundation and OpenAI vertical slice

```text
Continue from the green knowledge system.

READ FIRST

- AI_HANDOFF.md
- DECISIONS.md
- PROGRESS.md
- DATA_MODEL.md
- SECURITY.md
- API_CONTRACTS.md
- AI_PROVIDERS.md
- FRONTEND.md
- Existing model adapter, configuration, usage tracking, and secret-handling code

Implement the provider-neutral AI gateway foundation and one complete OpenAI BYOK vertical slice. Do not add fallback or other providers yet.

DATA MODEL

- ai_provider_connections
- ai_credential_references
- ai_model_catalog
- ai_provider_health
- model_invocations
- model_cost_ledger
- budget_policies

SECURITY REQUIREMENTS

1. Only Owner/Admin can create, rotate, test, disable, or delete a provider connection.
2. The browser submits a new key once to a server-only endpoint over TLS. Never echo the key in a response.
3. Production stores the secret in the selected managed secret store and persists only organization ID, provider, secret reference, masked fingerprint/label, status, creator, and timestamps.
4. Raw keys must never enter Postgres columns, browser storage, client bundles, URLs, logs, analytics, traces, exception messages, audit payloads, exports, or support diagnostics.
5. Create/test/rotate/read-use/disable/delete operations are tenant-authorized and audited without secret values.
6. Rotation validates the replacement before an atomic reference switch; a failed rotation keeps the last healthy credential.
7. Disable/delete prevents new use immediately. Define safe handling for in-flight jobs and secret deletion retries.
8. Keep inference credentials separate from optional billing/admin credentials. Do not implement billing credential collection in this step.

GATEWAY CONTRACT

- Canonical request/response and error types for classification, drafting, embeddings, reranking, and evaluation.
- Capability registry fields: structured JSON, tool use, streaming, context limit, region, usage metadata, prompt caching, embedding model/dimension, and declared retention/privacy metadata.
- Normalized errors: AUTHENTICATION, RATE_LIMIT, QUOTA_EXHAUSTED, TIMEOUT, TRANSIENT_PROVIDER, UNSUPPORTED_CAPABILITY, POLICY_REFUSAL, INVALID_OUTPUT, BUDGET_BLOCKED, DISABLED.
- Server-selected provider/model only; never accept an arbitrary provider, model, endpoint, or secret reference directly from ticket content or an untrusted browser request.

OPENAI VERTICAL SLICE

1. Adapt the existing OpenAI implementation to the canonical gateway without changing proven guardrails.
2. Add connect, bounded test-connection, model discovery/refresh where supported, disable, rotate, and delete.
3. Do not hardcode one model in business logic. Store an approved model selection and capability snapshot.
4. A connection test uses minimal tokens, strict timeout, no customer ticket data, and records no prompt content.
5. Record request/model/token/latency/error metadata and versioned estimated cost for every actual invocation.
6. Add a deterministic fake provider for tests so the normal test suite spends no API credit.

NEXT.JS UI

- Add Settings > AI Providers.
- Show provider, masked credential label, status, last validation, model, capabilities, and allowed actions.
- The key field is password-type, never prefilled, never revealed later, excluded from form persistence and telemetry, and cleared after submission.
- Add accessible connection, validation, rotation, disable, and deletion states with explicit confirmation.

VERIFY

- secret leakage scan across database fixtures, logs, traces, rendered HTML, browser storage, error output, and exports
- two-tenant credential-reference and invocation isolation
- Owner/Admin/Manager/Agent/Auditor permission matrix
- create/test/rotate/failure/disable/delete lifecycle
- deterministic gateway conformance tests
- OpenAI adapter tests with mocked HTTP plus one optional explicitly approved and cost-bounded real connection test
- no fallback and no provider-owned billing lookup yet
- full suite green

Update AI_PROVIDERS.md, DATA_MODEL.md, SECURITY.md, API_CONTRACTS.md, FRONTEND.md, TEST_REPORT.md, PROGRESS.md, and AI_HANDOFF.md. Stop here.
```

### Gate 10A

- An organization can securely connect and use its own OpenAI key without the key being stored or exposed outside the managed secret system.
- The gateway contract and deterministic fake provider pass conformance tests.
- All model invocations are tenant-scoped, permission-checked, metered, and auditable.

## Prompt 10B - Add provider adapters and model qualification

```text
Continue from the green OpenAI BYOK vertical slice.

READ FIRST

- AI_HANDOFF.md
- DECISIONS.md
- PROGRESS.md
- AI_PROVIDERS.md
- SECURITY.md
- API_CONTRACTS.md
- Existing gateway and OpenAI conformance tests

Add providers through the existing canonical gateway. Do not duplicate business logic inside adapters.

IMPLEMENT IN THIS ORDER

1. Anthropic/Claude.
2. Google Gemini.
3. Azure OpenAI.
4. AWS Bedrock using its documented credential/role mechanism rather than forcing an API-key shape.
5. NVIDIA NIM.
6. Approved OpenAI-compatible endpoints for Mistral, Groq, Together AI, OpenRouter, and compatible self-hosted/cloud endpoints.

For each provider, finish and test one adapter before starting the next. If an SDK or credential prerequisite is unavailable, document that adapter as BLOCKED and continue only when doing so does not weaken the completed adapters.

REQUIREMENTS

1. Provider-specific credential schemas with secret fields separated from display metadata.
2. Server-side validation, bounded test request, safe error normalization, and disable/rotate/delete support.
3. Model discovery where reliable; otherwise use a versioned audited catalog. Never scatter hardcoded model names or prices through product code.
4. Capability records drive eligibility for each workload. Unsupported models cannot be selected.
5. Custom endpoints require an HTTPS allowlist and SSRF controls: reject loopback, link-local, private/reserved networks, redirects to disallowed hosts, embedded credentials, and unapproved ports. Do not offer arbitrary endpoints until these controls pass.
6. Normalize structured output and usage metadata without pretending unsupported provider features exist.
7. Record provider/model/region/privacy metadata shown to admins without making unverified compliance claims.
8. Keep customer content out of connection tests.

MODEL QUALIFICATION

- Extend the provider conformance suite for schema validity, refusal classification, timeouts, rate limits, quota exhaustion, malformed output, token usage, cancellation, and redaction.
- Run the Northstar golden set for any model proposed as a production default.
- Compare route accuracy, unsafe automation count, citation grounding, schema success, latency, and estimated cost.
- Require an approval record before a materially different model becomes active; retain rollback to the previous approved choice.

VERIFY

- every enabled adapter passes the common contract suite
- unavailable/unfinished adapters cannot be selected
- endpoint SSRF and redirect tests
- credential fields never cross tenant or reach the browser after creation
- deterministic tests use mocks/fakes; real-provider tests are opt-in and cost-bounded
- full suite green

Update AI_PROVIDERS.md and all affected documents. Stop here.
```

### Gate 10B

- Every visible provider is genuinely usable or clearly marked unavailable; there are no decorative integrations.
- Provider differences are contained inside adapters and capability records.
- No model becomes a default without conformance and golden-set evidence.

## Prompt 10C - Add workload routing, safe fallback, budgets, and provider usage UI

```text
Continue from the green multi-provider gateway.

READ FIRST

- AI_HANDOFF.md
- DECISIONS.md
- PROGRESS.md
- AI_PROVIDERS.md
- DATA_MODEL.md
- SECURITY.md
- API_CONTRACTS.md
- FRONTEND.md
- Current adapters, cost ledger, and organization settings

Implement per-workload routing and financial controls. Do not change the ticket safety policy.

WORKLOADS

- classification
- drafting
- embeddings
- reranking
- evaluation

ROUTING POLICY

Each policy has an ordered provider/model list, required capabilities, allowed regions, timeout, maximum attempts, retry budget, cooldown, monthly/internal budget, per-request cost ceiling, and approval/version metadata.

REQUIREMENTS

1. Validate and publish routing policies as immutable versions with preview, conflict/error messages, approval, activation, rollback, and audit history.
2. Fallback is allowed only for timeout, transient 5xx, rate limit, unavailable model, exhausted quota, or invalid/revoked credentials.
3. Fallback must never bypass a safety/policy refusal, tenant budget block, required capability, allowed-region rule, organization disable switch, or maximum cost.
4. Never silently use a ResolveFlow-owned credential for a BYOK organization.
5. Add circuit breakers and provider/model cooldowns so repeated failures do not hit every ticket.
6. When all eligible models fail, persist a concise error code and route the ticket to human review without losing ingestion.
7. Pin each knowledge index to an embedding provider/model/version/dimension profile. Never query it with an incompatible embedding. Re-index separately before switching profiles.
8. Record every attempt, selection reason, fallback reason, latency, token usage, versioned estimated cost, result/error class, and policy version without storing hidden reasoning or secrets.
9. Enforce budget reservations atomically for concurrent jobs and reconcile estimates with final usage metadata.
10. Alert organization admins for threshold crossing, projected exhaustion, quota/auth failure, sustained rate limiting, fallback activation, and all-provider failure.

USAGE AND BILLING DATA

- Add optional, separately authorized provider-specific usage/billing connections only when the provider supports them.
- A normal inference key must not be assumed to expose provider billing.
- Store source, authority, collection time, reporting delay/freshness, currency, period, and provider account scope for every snapshot.
- Label UI values exactly as Provider-reported spend/balance, ResolveFlow-estimated usage/cost, or ResolveFlow internal budget remaining.
- If authoritative balance is unavailable, say so and link to the provider billing console; never infer or fabricate credit remaining.

NEXT.JS UI

- Workload-specific primary/fallback model selection with capability and region warnings.
- Reorder fallback priority accessibly; do not rely on drag-and-drop alone.
- Budget controls, threshold alerts, provider health, circuit state, usage charts, cost/latency/error/fallback breakdown, and freshness labels.
- Preview the effective policy and estimated maximum cost before activation.

VERIFY

- timeout/rate-limit/quota/auth/transient fallback cases
- refusal, budget, region, disabled, and unsupported-capability non-fallback cases
- all-provider failure routes safely to human review
- atomic concurrent budget tests
- embedding profile mismatch and re-index tests
- stale/missing provider billing data displays honestly
- organization isolation and authorization
- deterministic full suite green

Update AI_PROVIDERS.md and all affected documents. Stop here.
```

### Gate 10C

- Each AI workload has an approved, versioned, tenant-scoped routing policy.
- Fallback is bounded, auditable, budget-aware, region/capability-safe, and never changes who pays.
- Usage screens clearly distinguish provider facts, ResolveFlow estimates, and internal budget controls.

## Prompt 11 - Upgrade the MVP triage graph for production

```text
Continue from the green knowledge, rules, and multi-provider gateway foundation.

READ FIRST

- AI_HANDOFF.md
- DECISIONS.md
- PROGRESS.md
- ARCHITECTURE.md
- SECURITY.md
- API_CONTRACTS.md
- AI_PROVIDERS.md
- Existing LangGraph/state-machine code, model adapter, guardrails, evaluation fixtures, and retrieval service

Upgrade the existing triage graph without turning it into one opaque model prompt.

REQUIRED GRAPH

ingest_context
-> security_precheck
-> classify_extract
-> deterministic_rules
-> retrieve_published_knowledge
-> decide_route
-> persist_result
-> observe_audit

STRUCTURED OUTPUT

- category and subcategory
- urgency
- sentiment/anger
- language
- concise internal summary
- requested action
- extracted order/reference facts
- missing information
- risk flags
- classification confidence
- recommended department/queue

REQUIREMENTS

1. Use the canonical multi-provider gateway and strict canonical structured output. The triage graph must not import or branch on provider-specific SDKs.
2. Resolve the organization-approved classification routing policy server-side. Never hardcode or accept the model/provider from ticket text or untrusted browser input.
3. At most one bounded schema repair/retry.
4. One classification model call per new material ticket state, not one call per graph node.
5. Cache/reuse classification for unchanged message content and prompt/model/rules version.
6. Deterministic security and organizational rules override the LLM.
7. Prompt injection inside tickets cannot change tools, tenant, rules, or permissions.
8. Failure routes to human review with a safe error code.
9. Persist prompt template version, provider/model, routing-policy version, attempt/fallback metadata, token usage, latency, versioned estimated cost, structured result, citations, rule version, and concise decision evidence.
10. Never store hidden chain-of-thought.
11. Trigger triage asynchronously through the durable queue.

EVALUATION

- Run deterministic tests without paid API calls.
- Run the Northstar labelled/adversarial set.
- If an organization BYOK connection is configured and explicit test permission exists, run a small bounded real-model evaluation and report provider/model plus actual tracked estimated cost. Never use another tenant's or a ResolveFlow-owned credential implicitly.
- Target zero unsafe automation; tune toward escalation when uncertain.

Update docs and stop here.
```

### Gate 11

- Production graph is asynchronous, versioned, observable, and tenant-aware.
- Prompt injection and high-risk fixtures fail safely.
- Automated tests do not spend API credits.

## Prompt 12 - Add grounded draft replies and human approval

```text
Continue from the green production triage graph.

READ FIRST

- AI_HANDOFF.md
- DECISIONS.md
- PROGRESS.md
- SECURITY.md
- API_CONTRACTS.md
- FRONTEND.md
- AI_PROVIDERS.md
- Triage, retrieval, rules, roles, and UI code

Implement grounded reply drafts and approval workflow. Do not write to Gmail yet.

DRAFT INPUT

- normalized customer thread
- permitted customer/order facts
- published KB excerpts with article/version citations
- organization tone guide
- channel formatting constraints
- deterministic prohibited-action/promise rules

STRICT OUTPUT

- subject
- body
- citations
- missing_information
- risk_flags
- requires_approval
- concise draft rationale based on evidence

REQUIREMENTS

1. Generate only after triage, rules, and retrieval finish.
2. Use the organization-approved drafting routing policy through the canonical gateway. Provider fallback must obey Prompt 10C and cannot weaken approval or safety requirements.
3. Resolve the source mailbox and permitted reply/send identity server-side before generation. Bind every draft version to organization, ticket, source mailbox, selected identity, and current recipient set; never draft through another mailbox as fallback.
4. Validate citations against retrieved published content.
5. Detect unsupported promises, invented refunds/credits, sensitive-data requests, and policy contradictions.
6. Security, fraud, legal, chargeback, high-value, angry, and low-confidence tickets always require manager/human review.
7. Store draft versions and edits; preserve original AI draft.
8. Actions: edit, approve, reject with reason, request regeneration, return to agent.
9. Record actor, timestamps, source mailbox/identity, recipient set, source draft, final content hash, provider/model/routing version, usage/cost metadata, and audit events.
10. Measure acceptance, rejection reason, and edit distance without storing unnecessary model reasoning.
11. Build a polished Next.js approval queue and ticket-side draft editor with visible From/To/Cc/mailbox identity, citation chips linked to an in-product source preview, unsaved-change protection, permission-aware actions, keyboard shortcuts that never bypass confirmation, and clear AI-generated-content labeling.
12. No provider-side draft and no send action in this step.

VERIFY

- citation and hallucination guard tests
- permission tests for Agent/Manager/Admin
- concurrent approval/update protection
- adversarial ticket tests
- no external Gmail mutation
- full suite green

Update docs and stop here.
```

### Gate 12

- Every draft is traceable to published knowledge and a triage version.
- Human approval is enforced for all drafts.

---

# Stage D - Controlled provider actions and integrations

## Prompt 13 - Add Gmail draft creation and labels with least privilege

```text
Continue from the green internal approval workflow.

READ FIRST

- AI_HANDOFF.md
- DECISIONS.md
- PROGRESS.md
- SECURITY.md
- API_CONTRACTS.md
- Gmail OAuth/connector code
- Draft approval and idempotency design

Implement an explicit permission upgrade from READ_ONLY to DRAFT_ONLY for selected organization mailboxes, then add approved Gmail draft creation and safe label application without weakening cross-mailbox isolation.

Use current official Gmail scope documentation. Request only the additional scopes required for the enabled actions and record granted scopes.

REQUIREMENTS

1. Owner/Admin initiates a selected mailbox's permission upgrade and sees exact new capabilities.
2. Re-authentication is explicit; no silent permission expansion.
3. Only an approved current draft can create a provider-side Gmail draft.
4. Re-check organization membership, role, source mailbox, selected send identity, mailbox-content permission, mailbox status, permission level, ticket state, approval, and content hash immediately before the Gmail call.
5. Lock the provider draft to the ticket's source mailbox or an explicitly verified and permitted send-as identity. Never create it through another healthy mailbox as fallback.
6. Use a stable idempotency key and provider-action record.
7. Retry safely without creating duplicate drafts or labels.
8. Add presence plus a short-lived reply-composer lease and optimistic version check so simultaneous agents cannot overwrite or duplicate an approved draft silently.
9. Record provider IDs and immutable audit events.
10. Read-only mailboxes reject actions before any Gmail API call.
11. Revoked/degraded connections reject writes without falling back to another mailbox.
12. Keep sending disabled and absent from the UI/API.

VERIFY

- mocked success, retry, timeout, duplicate, revoked, stale approval, changed draft, wrong mailbox/send identity, simultaneous agents, wrong tenant, and read-only denial
- manual integration test on a dedicated test mailbox only when credentials exist
- verify no email is sent
- full suite green

Update docs/runbook and stop here.
```

### Gate 13

- Approved draft creation is permission-checked, source-mailbox-safe, concurrency-safe, idempotent, and audited.
- Sending is still impossible.

## Prompt 14 - Productionize the remote MCP gateway with OAuth 2.1

```text
Continue from the green SaaS application services.

READ FIRST

- AI_HANDOFF.md
- DECISIONS.md
- PROGRESS.md
- ARCHITECTURE.md
- SECURITY.md
- API_CONTRACTS.md
- Existing MVP MCP server/client and application authorization services

Upgrade the demo MCP server into an optional tenant-aware remote MCP gateway over Streamable HTTP.

Use the current stable MCP authorization specification supported by the selected SDK/client. Do not retain one global API key as the production identity model.

TOOLS FOR V1

- list_queues
- search_tickets
- get_ticket
- search_knowledge_base
- create_internal_note
- create_reply_draft
- assign_ticket
- apply_tag
- request_human_approval

Do not expose send_reply.

REQUIREMENTS

1. OAuth 2.1-based authorization for remote MCP.
2. Token resolves to organization, actor/service identity, scopes, expiry, and revocation state.
3. Reuse application services; do not duplicate business logic in MCP handlers.
4. Re-check tenant, role, department, ticket, mailbox, and action permissions per call.
5. Strict tool schemas and bounded pagination/result sizes.
6. Per-user/service and per-organization rate limits.
7. Audit actor, tool, target, purpose/request ID, result, latency, and denied calls without logging sensitive bodies.
8. Protect against confused-deputy and token-forwarding mistakes.
9. MCP access can be revoked without disconnecting Gmail.

VERIFY

- valid/expired/revoked/wrong-audience/wrong-tenant tokens
- department access denial
- tool-scope denial
- rate limits
- prompt-injection input cannot expand permissions
- MCP and web UI produce consistent results from the same service
- full suite green

Update docs and stop here.
```

### Gate 14

- MCP access is tenant-aware, scoped, revocable, rate-limited, and audited.
- MCP cannot bypass application authorization.

---

# Stage E - Commercial and operational readiness

## Prompt 15 - Implement finalized pricing, usage metering, billing, and entitlements

```text
Continue from the green application/MCP state.

READ FIRST

- AI_HANDOFF.md
- DECISIONS.md
- PROGRESS.md
- PRODUCTION_PLAN.md
- PRICING.md
- DATA_MODEL.md
- SECURITY.md
- API_CONTRACTS.md
- AI_PROVIDERS.md
- Section 16 of RESOLVEFLOW_PRODUCTION_SAAS_UPGRADE_PLAN.md
- Current billing-provider decision and existing billing code

Implement the finalized launch pricing and server-enforced entitlements. Use Stripe or the selected equivalent only when test credentials/configuration are available; otherwise finish the provider-neutral billing adapter, deterministic fake provider, UI, and automated tests, then mark live checkout MANUAL REQUIRED rather than inventing credentials or product IDs.

LOCKED LAUNCH PLANS

- Sandbox: $0; 1 user; no live inbox; 250 CSV/manual tickets/month; 1 BYOK connection; 7-day retention.
- Starter: $49 month-to-month or $39/month billed annually; 5 full + 5 light users; 2 live inboxes; 2,000 processed tickets/month; 2 BYOK connections; 90-day retention.
- Team: $199 month-to-month or $159/month billed annually; 20 full + 20 light users; 10 live inboxes; 10,000 processed tickets/month; 5 BYOK connections; 12-month retention.
- Business: $599 month-to-month or $479/month billed annually; 75 full + unlimited light users; 50 live inboxes; 50,000 processed tickets/month; unlimited BYOK connections; 36-month retention.
- Enterprise: custom annual contract and negotiated entitlements.

LOCKED CAPACITY AND USAGE ADD-ONS

- Additional 1,000 processed tickets: Starter $15, Team $12, Business $8.
- Additional full user/month: Starter $9, Team $8, Business $7.
- Starter capacity up to 10 total live inboxes: $20 monthly or $16/month billed annually.
- Starter/Team capacity up to 50 total live inboxes: $79 monthly or $63/month billed annually; Business already includes 50.
- Team/Business capacity up to 100 total live inboxes after operational review: $129 monthly or $103/month billed annually.

LOCKED TRIAL AND DESIGN-PARTNER OFFER

- 14-day Team trial without a credit card, limited to two live inboxes and 1,000 processed tickets; Sandbox remains available afterward.
- First 10 approved design-partner organizations: 40% off the applicable paid base plan for six months, subscribed base-price lock for 12 months, excluding disclosed overages/taxes, with scheduled feedback and separately optional permission for anonymized product metrics. Never require a public testimonial.

Seats, mailbox capacity, ticket volume, send identities, storage, and feature tier are separate entitlements. A solo user may buy capacity for 50 authorized mailboxes without buying Business seats. Verified aliases on one underlying provider mailbox do not count as separate live-mailbox connections; independently authorized provider accounts do.

BILLABLE TICKET DEFINITION

A processed ticket is one new external customer conversation accepted into the live workspace during the billing period.

Do not bill again for additional messages/replies in the same conversation, internal notes, agent replies, sync retries, duplicate events, merged duplicates, spam, newsletters, delivery failures, or auto-replies. A reopen within 30 days remains the same billable ticket. Historical dry-run imports use a separately visible evaluation allowance.

DATA MODEL

- billing_customers
- plans and immutable/versioned plan prices
- subscriptions and subscription_items
- plan_entitlements and entitlement_overrides
- usage_ledger and reconciled monthly_usage_counters
- usage_events
- invoices or provider references
- billing_webhook_events
- trials/coupons where supported

REQUIREMENTS

1. Store monetary amounts in minor currency units and never use floating-point arithmetic for billing.
2. Record immutable, idempotent usage-ledger entries keyed by organization, billable unit, source event, period, and stable idempotency identity.
3. Reconcile monthly counters from the ledger; never invoice from mutable dashboard cards.
4. Verify billing-webhook signatures and handle duplicate, delayed, missing, and out-of-order events idempotently.
5. Enforce entitlements server-side for full/light users, live mailbox capacity, monthly processed tickets, BYOK connections, retention, and premium features. UI hiding is not enforcement.
6. Implement the 14-day Team trial without a credit card, limited to two live inboxes and 1,000 processed tickets. Sandbox remains available after trial.
7. Implement trial, active, past-due, at-least-seven-day grace, cancelled, and suspended behavior.
8. Warn at 70%, 85%, and 100%, show projected overage and whether upgrading is cheaper, and require Owner confirmation before paid add-ons, upgrades, or managed-AI activation.
9. Keep inbound mail ingestion running during overage, budget exhaustion, and payment retry. Optional AI and nonessential processing may pause according to the disclosed policy; route tickets to manual review.
10. Apply upgrades with a clear proration preview. Schedule downgrades for renewal unless the Owner explicitly accepts immediate capability reduction.
11. Build Settings > Plan & Billing with plan comparison, monthly/annual toggle, independent seats/inboxes/ticket usage, low-seat/high-mailbox configuration, invoices, hosted customer-portal link, payment/grace status, usage export, and cancellation.
12. Every metric drills into an inspectable usage ledger or filtered list. Explain the billable-ticket definition beside usage.
13. Keep ResolveFlow subscription usage visually and semantically separate from external provider-reported LLM spend, ResolveFlow-estimated model cost, and internal AI budget remaining.
14. BYOK inference charges are paid directly by the organization to its provider. Do not add a ResolveFlow per-answer/per-token fee and never silently use a managed credential.
15. No client-only plan enforcement. Do not trust price IDs, amount, plan, entitlement, discount, quantity, organization, or billing status from the browser.
16. Add invoice/usage export, billing audit events, and organization-isolated billing access.
17. Update PRICING.md with actual configured billing product/price IDs by environment only when they exist; never put secrets in documentation.

VERIFY

- plan and add-on entitlement matrix
- solo user + 50-mailbox capacity without extra fake seats
- aliases do not consume mailbox connections; separate provider accounts do
- exact billable-ticket inclusion/exclusion and 30-day reopen boundary
- atomic concurrent usage and idempotent ledger tests
- duplicate/out-of-order/missing billing webhook tests
- monthly/annual price, coupon, tax, currency, and proration calculations
- 70/85/100 warning and explicit purchase confirmation
- trial expiry, upgrade, downgrade, payment failure/grace, cancellation, and portal flows
- no cross-tenant billing access
- AI-disabled-at-limit/manual-review behavior without lost inbound mail
- BYOK is not double-billed
- accessible/responsive billing UI and full suite green

Update PRICING.md, DATA_MODEL.md, SECURITY.md, API_CONTRACTS.md, FRONTEND.md, TEST_REPORT.md, PROGRESS.md, and AI_HANDOFF.md with actual evidence. Stop here.
```

### Gate 15

- Published prices, add-ons, billable-unit semantics, and server entitlements match PRICING.md exactly.
- Usage and invoices reconcile to an immutable idempotent ledger.
- A one-person workspace can purchase 50-mailbox capacity without purchasing unused seats.
- Hitting a plan, payment, or AI limit never loses inbound customer tickets or causes hidden charges.

## Prompt 16 - Security, privacy, deletion, export, and retention hardening

```text
Continue from the green commercial foundation.

READ FIRST

- AI_HANDOFF.md
- DECISIONS.md
- PROGRESS.md
- SECURITY.md
- DATA_MODEL.md
- RUNBOOK.md
- DEPLOYMENT.md
- AI_PROVIDERS.md
- All credential, storage, attachment, audit, and tenant-access code

Perform the production security/privacy hardening slice.

IMPLEMENT

1. Configurable organization retention policy and bounded purge jobs.
2. Organization export and deletion-request workflows with authorization, status, audit, and retry behavior.
3. Deletion propagation to database rows, private storage, search/vector indexes, caches, Gmail credentials, LLM credential references, and managed provider secrets according to documented policy.
4. OAuth and LLM-provider revocation/disconnect, credential rotation, disabled-provider, and secret-manager outage runbooks.
5. Private attachment storage, allowlisted file types, size limits, safe names, and malware-scanning integration boundary. Do not execute attachment content.
6. Sensitive-data redaction in logs/model requests where feasible.
7. CSRF, CORS, secure headers, cookie/session, rate-limit, and webhook-signature review.
8. Audit coverage for sensitive reads and every external write.
9. Secrets rotation and key-version support, including proof that raw LLM keys never appear in database exports, logs, browser responses, analytics, traces, or customer support bundles.
10. Privacy policy/data-flow/subprocessor documentation placeholders clearly marked for legal review.

TEST

- tenant isolation across API, UI, worker, storage, retrieval, export, audit, billing, and MCP
- malicious filenames/content types
- deletion retry/idempotency
- revoked sessions/tokens
- audit immutability
- prompt injection and data-exfiltration attempts
- secrets/log scanning

Run the full security and functional suites. Update SECURITY.md and RUNBOOK.md with evidence and remaining risks. Stop here.
```

### Gate 16

- Export, retention, disconnect, revocation, and deletion work end-to-end.
- Security tests cover every data-access surface.

## Prompt 17 - Add observability, resilience, backups, and operational runbooks

```text
Continue from the hardened security state.

READ FIRST

- AI_HANDOFF.md
- DECISIONS.md
- PROGRESS.md
- ARCHITECTURE.md
- DEPLOYMENT.md
- RUNBOOK.md
- TEST_REPORT.md
- Current queues, workers, providers, logging, and health checks

Implement production operations without adding product scope.

REQUIREMENTS

1. Structured logs with correlation IDs and tenant-safe metadata.
2. Metrics for ingestion lag, queue depth/age, Gmail watch expiry, connector health, API latency/error rate, AI latency/error/cost by provider/model/workload, fallback/circuit state, budget exhaustion, provider-usage freshness, retrieval quality, action failures, and billing webhook failures.
3. Traces across webhook -> queue -> ingestion -> triage -> retrieval -> draft/action.
4. Alerts with actionable thresholds and runbook links.
5. Exponential backoff, retry caps, dead-letter handling, replay tooling, and circuit breakers.
6. Per-organization concurrency/rate limits to prevent noisy-neighbor failures.
7. Database backup/PITR configuration documentation and a tested restore drill in a safe non-production environment.
8. Status/incident communication process.
9. Health, readiness, and dependency checks that do not leak secrets.
10. SLO dashboard and initial targets documented.

TEST

- provider 429/5xx
- primary/fallback provider timeout, rate limit, quota exhaustion, invalid credential, and outage
- queue backlog
- database transient failure
- duplicate job replay
- dead-letter recovery
- partial external-action uncertainty
- watch expiration
- backup restore validation

Update DEPLOYMENT.md, RUNBOOK.md, TEST_REPORT.md, and AI_HANDOFF.md with actual evidence. Stop here.
```

### Gate 17

- Failures are visible, bounded, recoverable, and documented.
- Backup restoration has been tested, not merely configured.

## Prompt 18 - Build staging and run the private-beta release gate

```text
This is the final private-beta release-candidate step. Do not add features.

READ FIRST

- AI_HANDOFF.md
- DECISIONS.md
- PROGRESS.md
- PRODUCTION_PLAN.md
- ARCHITECTURE.md
- SECURITY.md
- DEPLOYMENT.md
- RUNBOOK.md
- TEST_REPORT.md
- FRONTEND.md
- AI_PROVIDERS.md
- PRICING.md
- Current git status and deployment configuration

Prepare and validate a staging release for two dedicated test organizations. The primary test organization uses two dedicated shared Gmail test mailboxes; also run a deterministic simulated 50-mailbox workload for the low-seat/high-mailbox case.

RELEASE WORK

1. Produce a traceable commit/image/version from a clean source state.
2. Apply migrations using the documented staging procedure.
3. Deploy the Next.js web/control plane, authenticated Python AI service, worker, queue/webhook endpoints, and scheduled watch/reconciliation jobs.
4. Configure secrets through the managed secret system only.
5. Configure Google OAuth redirect URIs and Pub/Sub identities for staging.
6. Seed the primary organization with Owner, Manager, Agent, departments, queues, rules, SLA, and Northstar KB using supported product flows where practical; seed a second organization for isolation tests.
7. Connect two test mailboxes in READ_ONLY mode, map them to different departments/permissions, and verify unified plus mailbox-specific queues.
8. Run historical dry mode and live ingestion.
9. Connect one dedicated low-limit test BYOK provider credential through the real server-only product flow; never commit or print it.
10. Qualify and activate classification/drafting models through the real routing-policy flow, then enable internal draft generation and approval. Keep provider sending disabled.
11. Verify timeout, rate-limit, quota, invalid credential, budget block, allowed fallback, prohibited fallback, all-provider failure, credential rotation/revocation, and tenant isolation.
12. Verify independent mailbox pause/reconnect/disconnect, credential revocation, export, retention, and deletion in staging.
13. Verify per-user new/unseen state, aggregate-versus-content authorization, mailbox health dashboards, and one-mailbox failure isolation.
14. Run the billing provider in test mode: trial, finalized plans, solo + 50-mailbox capacity, usage ledger, warning thresholds, overage preview, proration, grace, invoice, and cancellation.

FULL RELEASE GATE

- formatting/lint/type/build
- migrations on clean and upgrade paths
- unit/integration/end-to-end/security tests
- two-tenant isolation suite
- multi-mailbox Gmail duplicate/out-of-order/gap/reconnect/failure-isolation suite
- two-real-mailbox unified queue plus deterministic 50-mailbox load/fairness test
- per-user seen/unseen, aggregate/content authorization, and cross-mailbox reply-identity tests
- Northstar routing/retrieval/draft/adversarial evaluation
- BYOK secret lifecycle and leakage scan
- adapter conformance and model-qualification evidence for every enabled provider
- workload routing, circuit-breaker, fallback, embedding-profile, and concurrent budget suites
- honest separation of provider-reported values, ResolveFlow cost estimates, and internal budget remaining
- zero unsafe external actions
- load test at the documented beta target
- model and provider failure drills
- dead-letter replay
- backup restore drill
- secrets scan and dependency vulnerability review
- Next.js production build and server/client boundary checks
- keyboard-only and automated accessibility checks for critical UI flows
- responsive visual regression at mobile, tablet, laptop, and wide desktop viewports
- Playwright end-to-end tests for onboarding, organization switching, unified/mailbox queues, new indicators, ticket detail, knowledge publication, rules dry-run, draft approval, provider settings, and plan/billing
- verification that no customer-facing production route depends on Streamlit

Create a requirement-to-evidence release checklist in TEST_REPORT.md. Record exact commands, results, deployed version identifiers, known limitations, rollback steps, and a GO/NO-GO verdict.

Do not claim production or public readiness if Google OAuth verification, security assessment, legal documents, or operational ownership remain incomplete. Stop after the private-beta verdict.
```

### Gate 18

- Staging proves the complete multi-mailbox Observe/Draft workflow with two real test mailboxes and simulated 50-mailbox capacity.
- Sending remains disabled.
- Every acceptance statement links to actual evidence.

## Prompt 18A - Add explicitly human-approved Gmail sending pilot

```text
Run this prompt only after Prompt 18 is GO and the user explicitly authorizes implementing the controlled-send pilot. Do not enable sending for every organization or mailbox.

READ FIRST

- AI_HANDOFF.md
- DECISIONS.md
- PROGRESS.md
- SECURITY.md
- API_CONTRACTS.md
- RUNBOOK.md
- TEST_REPORT.md
- Gmail connector, mailbox permissions, drafts, approvals, presence/leases, outbox, and provider-action code
- Current official Gmail send, OAuth scope, threading, and error-handling documentation

Implement human-approved sending behind an organization and per-mailbox feature flag. This is not auto-send.

REQUIREMENTS

1. Add an explicit per-mailbox upgrade from DRAFT_ONLY to APPROVED_SEND. Owner/Admin sees the exact new scope and capability and must complete reauthorization; no silent scope expansion.
2. Send only the current approved immutable draft version. Bind approval to organization, ticket, source mailbox, send identity, recipient set, subject/body/attachment hashes, approver, and expiry.
3. Immediately before enqueue and again before the provider call, re-check tenant membership, role, mailbox-content/sender permission, connection health, granted scope, feature flag, ticket state, approval, content hashes, recipient policy, and budget/plan entitlement.
4. Force the ticket's authorized source mailbox or explicitly verified permitted send-as identity. Never route a send through another mailbox as provider fallback.
5. Use a transactional outbox, immutable provider_action, stable idempotency key, and a send state machine such as PENDING, SENDING, SUCCEEDED, UNKNOWN, FAILED, and CANCELLED.
6. Handle ambiguous timeout/network results safely: reconcile using stored provider/RFC message identity and thread evidence before any retry. Never blindly resend when success is uncertain.
7. Preserve Gmail threading headers and recipient semantics. Sanitize headers and block header injection, unauthorized BCC, reply-all expansion, and changed recipients without reapproval.
8. Use a short-lived send lease/optimistic ticket version so two agents cannot send the same reply concurrently. A successful send invalidates competing draft approvals and updates the conversation exactly once.
9. UI shows From, To/Cc, mailbox, approval, final content, and irreversible-action confirmation. The Send button is never the default focused action and keyboard shortcuts cannot bypass confirmation.
10. Display Sent only after provider-confirmed success. UNKNOWN remains visibly unresolved with a reconciliation action; do not claim delivery or customer receipt merely because Gmail accepted the message.
11. Record actor, approver, mailbox/send identity, ticket/draft/action IDs, scopes, timestamps, idempotency identity, provider result, and concise error classification without message content or secrets in general logs.
12. Add organization/mailbox kill switches and an incident procedure that immediately disables new sends while preserving ingestion.
13. Keep autonomous/LLM-triggered auto-send impossible. Every send in this step requires a current human approval and explicit human send confirmation.
14. Real sending may be tested only with explicit user permission, one dedicated test mailbox, allowlisted test recipients, and a strict message-count cap. Otherwise use the fake/mock provider and mark the real-send check MANUAL REQUIRED.

VERIFY

- wrong tenant, role, department, mailbox, send identity, scope, feature flag, recipient, content hash, expired/stale approval, and revoked/degraded connection are denied before provider call
- simultaneous agents and duplicate clicks produce at most one sent message
- timeout-before-send, timeout-after-possible-send, 429, 5xx, worker crash, outbox replay, and reconciliation cases
- correct thread/reply headers and prohibited header/BCC injection
- provider success updates one ticket/thread/action and invalidates competing approval
- UNKNOWN state never auto-retries blindly and is operationally recoverable
- audit completeness and sensitive-data redaction
- read-only and draft-only mailboxes remain unable to send
- real-send feature defaults off for every existing and new organization
- full deterministic suite green

Update SECURITY.md, API_CONTRACTS.md, RUNBOOK.md, TEST_REPORT.md, PROGRESS.md, and AI_HANDOFF.md. Stop here.
```

### Gate 18A

- Human-approved sending is tenant-, mailbox-, identity-, scope-, approval-, and content-version-bound.
- Duplicate/uncertain outcomes cannot cause blind resend.
- Auto-send remains impossible and the capability defaults off.

---

# Stage F - Optional enterprise and public-launch work

## Prompt 19 - Add enterprise Google Workspace DWD only after beta approval

```text
Run this prompt only after Prompt 18 is GO and a real enterprise customer explicitly requires multi-mailbox domain-wide delegation.

READ FIRST

- AI_HANDOFF.md
- DECISIONS.md
- PROGRESS.md
- SECURITY.md
- ARCHITECTURE.md
- Gmail OAuth connector code
- Original gmail-multi-account-mcp-spec.md if present
- Private-beta findings

Design and implement an enterprise-only Google Workspace DWD connection mode without weakening the existing per-organization OAuth mode.

REQUIREMENTS

1. Workspace Super Admin-only setup with exact client ID/scopes and confirmation.
2. Explicit approved mailbox allowlist stored per organization; never “any mailbox in domain.”
3. Read-only default and per-mailbox application-level permission.
4. Exclude HR, legal, executive, and personal-use mailboxes by default.
5. Isolate each organization's DWD configuration/credential/encryption context.
6. Every tool/API/job requires an explicit mailbox and validates allowlist plus permission before impersonation.
7. Organization kill switch, per-mailbox disable, credential rotation, revoke, and complete audit.
8. Rate limits and concurrency controls prevent mailbox enumeration.
9. Admin UI shows reach, risk, last access, sync health, and write capability.
10. Confirm employee-notice/legal-basis requirement during onboarding without pretending the application provides legal advice.

WRITE ACTIONS REMAIN DISABLED unless separately approved and implemented through the same human approval/idempotency gates.

TEST

- unlisted mailbox denial before Google call
- read-only write denial
- wrong organization/domain
- revoked DWD
- 50-mailbox concurrency/quota behavior
- audit completeness
- compromised/malformed mailbox input
- cross-tenant isolation

Update docs and stop here.
```

### Gate 19

- DWD access is organization-isolated, deny-by-default, allowlist-only, observable, revocable, and read-only unless a separately gated write path is explicitly approved.
- The 50-mailbox test demonstrates bounded concurrency and no mailbox enumeration or cross-tenant access.

## Prompt 20 - Public launch and Google verification readiness

```text
This is a launch-readiness documentation and evidence step. Do not invent approvals.

READ FIRST

- AI_HANDOFF.md
- DECISIONS.md
- PROGRESS.md
- SECURITY.md
- DEPLOYMENT.md
- RUNBOOK.md
- TEST_REPORT.md
- Actual Google OAuth/Workspace configuration and granted scopes

Prepare the public-launch readiness package.

REQUIREMENTS

1. Enumerate every Google scope actually requested and map it to a user-visible feature and code path.
2. Remove unused/broader scopes.
3. Verify public homepage, privacy policy, terms, support contact, authorized domains, redirect URIs, data deletion instructions, and account deletion flow.
4. Produce the OAuth verification demonstration script showing consent and the data flow for every scope.
5. Document whether restricted-scope verification and an independent security assessment are required; do not guess approval status.
6. Prepare Workspace Marketplace listing assets/requirements only if that distribution path is selected.
7. Finalize DPA/subprocessor/data-retention/security documentation for qualified legal/security review.
8. Complete penetration test and dependency/security review; record unresolved issues.
9. Assign operational owners for incidents, billing, Google verification responses, backups, security, and customer support.
10. Produce a launch checklist with BLOCKED, READY, and MANUAL REQUIRED statuses.

Return a strict GO/NO-GO verdict. Do not launch or enable auto-send unless separately authorized by the user and all required gates are green.
```

### Gate 20

- Every launch claim is backed by current evidence; unresolved legal, Google-verification, security-assessment, operational-owner, or penetration-test items force NO-GO or MANUAL REQUIRED.
- Human-approved sending, if enabled, remains separate from autonomous auto-send and retains all Prompt 18A controls.

---

# Recommended execution order

| Order | Prompt | Outcome |
|---:|---|---|
| 1 | 00 | Repository-specific production plan |
| 2 | 01 | Production configuration and boundaries |
| 3 | 02 | Tenant-isolated database |
| 4 | 03 | Workspace onboarding and invitations |
| 5 | 04 | Roles, departments, and queues |
| 6 | 05 | Safe connector foundation |
| 7 | 06 | Multiple tenant-isolated read-only Gmail connections |
| 8 | 07 | Durable Gmail ingestion |
| 9 | 08 | Live support inbox |
| 10 | 09 | Rules, assignments, and SLA |
| 11 | 10 | Governed company knowledge |
| 12 | 10A | Secure BYOK foundation and OpenAI vertical slice |
| 13 | 10B | Additional provider adapters and qualification |
| 14 | 10C | Workload routing, fallback, budgets, and provider usage |
| 15 | 11 | Production AI triage through the provider-neutral gateway |
| 16 | 12 | Grounded drafts and approval |
| 17 | 13 | Approved provider-side drafts |
| 18 | 14 | Secure remote MCP gateway |
| 19 | 15 | Plans, billing, and usage |
| 20 | 16 | Security and privacy hardening |
| 21 | 17 | Observability and resilience |
| 22 | 18 | Staging private-beta gate |
| Optional after GO + explicit authorization | 18A | Human-approved Gmail sending pilot |
| Optional | 19 | Enterprise multi-mailbox DWD |
| Final | 20 | Public launch readiness |

## Stop points that require manual input

Codex must stop rather than guess when it needs:

- the actual production/staging domains
- Google OAuth client configuration
- Pub/Sub project/topic identities
- managed encryption/secret keys
- billing provider credentials/products/prices
- customer-owned LLM credentials, cloud regions, allowed custom endpoints, and production model choices
- optional provider billing/admin credentials and explicit approval for their extra privileges
- permission and a strict cost cap before any real-provider evaluation
- mailbox addresses and permission levels
- organization retention requirements
- the legal basis/employee notice for multi-mailbox access
- Google verification/security-assessment decisions
- permission to deploy or enable external writes
- separate explicit permission, dedicated test recipients, and a strict message cap before any real Gmail send test

## Scope discipline

The first useful commercial milestone is Prompt 12 after completing Prompts 10A-10C: a solo operator or company can create a workspace, connect multiple authorized inboxes, invite a team, configure departments/rules/SLAs, securely connect preferred LLM providers, publish knowledge, and receive grounded AI reply drafts with human approval.

The first controlled end-to-end reply milestone is optional Prompt 18A after the private-beta gate. It adds explicit human-approved Gmail sending while keeping autonomous auto-send impossible.

Do not wait for enterprise DWD, omnichannel integrations, SAML/SCIM, or auto-send before validating that milestone with real support teams.
